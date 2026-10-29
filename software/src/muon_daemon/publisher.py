"""
muon_daemon.publisher — Writes daemon state to InfluxDB 2.

Philosophy (matches the whole project): InfluxDB is the live-monitoring
layer, NOT the scientific source of truth. Raw data lives in the local
txt files; here we only publish summaries for dashboards/Grafana. So:

  - InfluxDB writes must NEVER block or crash the daemon. If InfluxDB is
    unreachable, we log once and carry on -- DAQ/txt logging is primary.
  - Environmental data (temp/humidity/IMU/angle) is published always,
    every env_interval_s, to the env bucket. Client-side batching means
    many points leave as one HTTP write, so the SD card sees far fewer
    writes than points (InfluxDB runs on the Pi itself).

    Two knobs, both in the config's [influx] section, and they trade off
    against each other: env_interval_s is how fine-grained the angle
    trace is, flush_interval_s is how long a point stays invisible to
    Grafana. Setting the flush interval too high makes the data look
    like it never arrived -- it has simply not been sent yet.
  - Coincidence rate is published only while a run is active, once per
    ~30-minute bin, to the rate bucket. The rate is the simple
    (count_end - count_start) / bin_seconds; dead-time-corrected rates
    are computed later from the txt files during offline analysis.

Config comes from the same .env used elsewhere (python-dotenv). If the
.env is missing or InfluxDB can't be reached at startup, the publisher
disables itself gracefully rather than taking the daemon down.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("muon_daemon.publisher")


# Fallbacks, used when the config has no [influx] section.
ENV_INTERVAL_S = 10.0
FLUSH_INTERVAL_S = 30.0
# One minute of counting is a few hundred events at this telescope's
# rate: noisy for physics, fine for watching that the DAQ is alive,
# which is what a dashboard is for. The scientific rate is rebuilt from
# the txt files offline, where the binning can be chosen after the fact.
RATE_BIN_S = 60.0

# Do not publish a closing bin shorter than this: a two-second tail
# holds no counts and would drag a visible spike into the graph.
_MIN_PARTIAL_BIN_S = 10.0


class InfluxPublisher:
    def __init__(self, hardware, state, cfg: dict | None = None,
                 env_path: Path | None = None) -> None:
        """
        hardware: the HardwareController (for live HardwareState).
        state:    the DaemonState (for run phase + daq_count).
        cfg:      the loaded TOML config; its [influx] section sets the
                  publish and flush intervals. Missing -> the fallbacks
                  above, so tests can leave it out.
        env_path: optional explicit path to the .env file. If None, we
                  look next to this file (mirrors the old publisher's
                  cwd-independent loading).
        """
        self._hw = hardware
        self._state = state

        influx_cfg = (cfg or {}).get("influx", {})
        self.env_interval_s = float(influx_cfg.get("env_interval_s", ENV_INTERVAL_S))
        self.flush_interval_s = float(influx_cfg.get("flush_interval_s", FLUSH_INTERVAL_S))
        self.rate_bin_s = float(influx_cfg.get("rate_bin_s", RATE_BIN_S))
        self._enabled = False
        self._client = None
        self._write_api = None
        self._bucket_env = ""
        self._bucket_rate = ""
        self._org = ""

        self._connect(env_path)

    def _connect(self, env_path: Path | None) -> None:
        """Best-effort setup. Any failure disables the publisher without
        raising -- the daemon must run fine even with no InfluxDB."""
        try:
            from dotenv import load_dotenv
            from influxdb_client import InfluxDBClient
            from influxdb_client.client.write_api import WriteOptions
        except ImportError as exc:
            log.warning("InfluxDB libraries not installed, publishing disabled: %s", exc)
            return

        if env_path is None:
            env_path = Path(__file__).resolve().parent / ".env"
        load_dotenv(env_path)

        try:
            url = os.environ["INFLUX_URL"]
            token = os.environ["INFLUX_TOKEN"]
            self._org = os.environ["INFLUX_ORG"]
            self._bucket_env = os.environ["INFLUX_BUCKET_ENV"]
            self._bucket_rate = os.environ["INFLUX_BUCKET_RATE"]
        except KeyError as exc:
            log.warning("Missing InfluxDB env var %s, publishing disabled", exc)
            return

        try:
            self._client = InfluxDBClient(url=url, token=token, org=self._org, timeout=10_000)
            # Batching: points accumulate in RAM and flush in one HTTP
            # write per interval, so the SD card sees few, coalesced writes.
            self._write_api = self._client.write_api(write_options=WriteOptions(
                batch_size=500,
                flush_interval=int(self.flush_interval_s * 1000),
                jitter_interval=2_000,
                retry_interval=5_000,
                max_retries=5,
            ))
            self._enabled = True
            log.info(
                "InfluxDB publisher connected (env=%s every %.0fs, flush every %.0fs, rate=%s)",
                self._bucket_env, self.env_interval_s, self.flush_interval_s, self._bucket_rate,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not connect to InfluxDB, publishing disabled: %s", exc)

    # ---- periodic tasks (started by the server, like the poll_* loops) ---

    async def publish_env_forever(self, interval_s: float | None = None) -> None:
        """Always-on: write environmental + orientation data to the env bucket."""
        if interval_s is None:
            interval_s = self.env_interval_s
        while True:
            if self._enabled:
                try:
                    self._write_env_point()
                    self._write_hv_points()
                except Exception as exc:  # noqa: BLE001
                    log.warning("env publish failed (ignored): %s", exc)
            await asyncio.sleep(interval_s)

    async def publish_rate_forever(self, bin_s: float | None = None,
                                   poll_s: float = 1.0) -> None:
        """Run-only: write the mean coincidence rate over each bin.

        Polls once a second rather than sleeping a whole bin, for two
        reasons. It has to notice the run ending -- a run shorter than
        one bin used to produce no point at all, because the only write
        happened after a full bin had elapsed and by then the run was
        over. And the last, partial bin of any run is worth keeping: it
        is real counting time, and dropping it means the end of every
        measurement is missing from the dashboard.

        The rate here is the plain count difference over wall time.
        Dead-time-corrected rates come from the txt files offline; this
        is the monitoring view, not the measurement.
        """
        from muon_daemon.state import RunPhase

        if bin_s is None:
            bin_s = self.rate_bin_s

        bin_start_count = self._state.hardware.daq_count
        bin_start_time = time.monotonic()
        was_running = False
        # The run is already cleared by the time we notice it ended, so
        # the name has to be remembered while it is still going or the
        # closing point would be tagged with an empty one.
        run_name, run_kind = "", ""

        while True:
            await asyncio.sleep(poll_s)

            running = self._state.phase == RunPhase.RUNNING
            now = time.monotonic()
            count_now = self._state.hardware.daq_count

            def reanchor() -> None:
                nonlocal bin_start_count, bin_start_time
                bin_start_count, bin_start_time = count_now, now

            if not self._enabled or (not running and not was_running):
                reanchor()
                was_running = running
                continue

            if running and not was_running:
                # A new run resets the DAQ counter, so the previous
                # anchor would give a negative difference.
                reanchor()
                run_name = self._state.run.name
                run_kind = self._state.run.kind
                was_running = True
                continue

            if running:
                run_name = self._state.run.name
                run_kind = self._state.run.kind

            elapsed = now - bin_start_time
            bin_full = running and elapsed >= bin_s
            run_ended = was_running and not running

            if bin_full or (run_ended and elapsed >= _MIN_PARTIAL_BIN_S):
                delta = count_now - bin_start_count
                if elapsed > 0 and delta >= 0:
                    rate = delta / elapsed
                    # Poisson error on N counts is sqrt(N); on the rate
                    # it is sqrt(N)/elapsed. Gives Grafana an error band
                    # for free.
                    rate_err = (delta ** 0.5) / elapsed
                    try:
                        self._write_rate_point(rate, rate_err, delta, elapsed,
                                               run_name, run_kind)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("rate publish failed (ignored): %s", exc)
                reanchor()

            if run_ended:
                reanchor()
            was_running = running

    # ---- point builders --------------------------------------------------

    def _write_env_point(self) -> None:
        from influxdb_client import Point

        hw = self._hw.hw
        p = (
            Point("environment")
            .field("temp_c", float(hw.temp_c))
            .field("humidity_rh", float(hw.humidity_rh))
            .field("angle_deg", float(hw.angle_deg))
            .field("target_angle_deg", float(hw.target_angle_deg))
            .field("imu_tilt_deg", float(hw.imu_tilt_deg))
            .field("imu_rms_dps", float(hw.imu_rms_dps))
            .field("imu_stable", bool(hw.imu_stable))
        )
        self._write_api.write(bucket=self._bucket_env, record=p)

    def _write_hv_points(self) -> None:
        """One point per HV channel, tagged by channel.

        A tag rather than four sets of fields, so a Grafana panel can
        plot all channels from one query and keeps working if a channel
        is added or removed. Nothing is written while the supply is
        unreachable: a gap in the graph is honest about not knowing,
        where a repeated last value would not be.
        """
        from influxdb_client import Point

        hw = self._hw.hw
        if not hw.hv_connected:
            return

        for ch in hw.hv_channels:
            p = (
                Point("hv")
                .tag("channel", str(ch["channel"]))
                .field("vmon", float(ch["vmon"]))
                .field("vset", float(ch["vset"]))
                .field("imon_ua", float(ch["imon"]))
                .field("iset_ua", float(ch["iset"]))
                .field("on", bool(ch["on"]))
                .field("status_raw", int(ch["status_raw"]))
                .field("faulted", bool(ch["faults"]))
            )
            self._write_api.write(bucket=self._bucket_env, record=p)

    def _write_rate_point(self, rate: float, rate_err: float, counts: int,
                          livetime_s: float, run_name: str, run_kind: str) -> None:
        from influxdb_client import Point

        p = (
            Point("coincidence_rate")
            .tag("run_name", run_name)
            .tag("run_kind", run_kind)
            .field("rate_hz", float(rate))
            .field("rate_err_hz", float(rate_err))
            .field("counts", int(counts))
            .field("bin_seconds", float(livetime_s))
        )
        self._write_api.write(bucket=self._bucket_rate, record=p)

    # ---- shutdown --------------------------------------------------------

    def close(self) -> None:
        """Flush any buffered points and close the client. Called during
        daemon shutdown so the last batch isn't lost."""
        if not self._enabled:
            return
        try:
            self._write_api.close()
            self._client.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("Error closing InfluxDB client: %s", exc)