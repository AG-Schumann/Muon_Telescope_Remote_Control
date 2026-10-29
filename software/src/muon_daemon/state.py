"""
muon_daemon.state — Central, in-memory authority for run lifecycle.

This is the single source of truth every client (TUI, web monitor,
future tools) queries over the socket. Nobody else touches storage.py's
latest.txt directly -- only this module does, and only on state
transitions (start/stop), not on every status poll.

Actual hardware access (motor motion, DAQ counting) is delegated to a
HardwareController (see hardware.py) -- this module only decides
*whether* an operation is allowed right now (the state machine rules),
not *how* it physically happens.

Both storage_backend and hardware are dependency-injected so this
module can be unit-tested without a real muon_control installation,
filesystem layout, or GPIO hardware.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from muon_daemon.hardware import HardwareController, HardwareError

log = logging.getLogger("muon_daemon.state")


def scan_angles(start_deg: float, stop_deg: float, step_deg: float) -> list[float]:
    """The angles one scan visits, both ends included.

    Built as start + i*step rather than by repeated addition, so float
    error cannot accumulate along the list. Returned ascending; the
    caller reverses it when the approach side calls for it, so that
    every step of the scan already moves from the side the anti-backlash
    rule wants (see start_run and hardware._move_blocking).
    """
    if step_deg <= 0:
        raise ValueError("scan step must be greater than zero")
    if stop_deg < start_deg:
        raise ValueError("scan stop angle must not be below the start angle")
    n = int(round((stop_deg - start_deg) / step_deg))
    return [round(start_deg + i * step_deg, 6) for i in range(n + 1)]


class RunPhase(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    CALIBRATING = "calibrating"


class DaemonError(Exception):
    """Raised when a command is rejected due to an invalid state transition,
    invalid parameters, or an underlying hardware failure. The server layer
    turns this into an error response; it is never a crash."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunInfo:
    name: str = ""
    kind: str = ""
    duration_s: Optional[float] = None
    # Two clocks, because they answer different questions. started_mono
    # is monotonic and immune to the system clock being adjusted, so it
    # measures how long the run has been going. started_utc is the wall
    # clock, which is the only one that can say WHEN it started -- and
    # the daemon is the only place that knows, since a client that
    # connects halfway through never saw the beginning.
    started_mono: float = 0.0
    started_utc: str = ""
    # Scan parameters, or None for a plain fixed-angle run. Its presence
    # is what makes this a scan, so it also decides who ends the run:
    # a scan ends when its last angle is done, not on a clock.
    scan: Optional[dict] = None


class DaemonState:
    def __init__(self, data_dir: Path, hardware: HardwareController, storage_backend=None) -> None:
        self.data_dir = Path(data_dir)
        self.phase = RunPhase.IDLE
        self.run = RunInfo()
        self.hardware = hardware

        # Rate calculation bookkeeping -- updated by the server's
        # broadcast loop (see server.py), not on every snapshot() call,
        # so it reflects a stable ~1s cadence rather than being noisy
        # from irregular get_status polling.
        self.rate_hz = 0.0

        # Scan progress, for the status broadcast. index is 1-based and
        # 0 means "not started"; step_state is what the scan is doing at
        # this instant (moving / settling / acquiring).
        self.scan_angles: list[float] = []
        self.scan_index: int = 0
        self.scan_step_state: str = ""
        # When counting started at the current angle. Only the acquiring
        # phase counts: moving and settling are overhead, not measurement,
        # so a "time left at this point" built from them would be wrong.
        self.scan_point_started_mono: float = 0.0
        self._scan_task: Optional[asyncio.Task] = None

        if storage_backend is None:
            from muon_control import storage as storage_backend  # real backend on the Pi
        self._storage = storage_backend

    @property
    def hw(self):
        return self.hardware.hw

    # ---- run lifecycle ------------------------------------------------

    def start_run(self, name: str, kind: str, duration_s: float | None,
                  scan: dict | None = None) -> dict:
        if self.phase != RunPhase.IDLE:
            raise DaemonError(f"Cannot start run: daemon is in '{self.phase.value}' state")
        if not name:
            raise DaemonError("Run name cannot be empty")

        angles: list[float] = []
        acquisition_s = 0.0
        if scan is not None:
            try:
                angles = scan_angles(
                    float(scan["start_deg"]), float(scan["stop_deg"]), float(scan["step_deg"])
                )
                # Visit them in the order that needs no extra swing. Every
                # angle is approached from one side (see _move_blocking);
                # walking the scan in that same direction means each step
                # is already a move from the right side, so the scan pays
                # nothing for the anti-backlash rule. Approaching from
                # above therefore scans downwards, from 90 toward 0.
                if getattr(self.hardware, "approach_from", "below") == "above":
                    angles = list(reversed(angles))
                acquisition_s = float(scan["acquisition_s"])
            except (KeyError, TypeError, ValueError) as exc:
                raise DaemonError(f"Invalid scan parameters: {exc}") from exc
            if acquisition_s <= 0:
                raise DaemonError("Scan counting time must be greater than zero")

        started_utc = _now_iso()
        self._storage.create_measurement(self.data_dir, name, kind=kind)
        self._storage.write_latest(
            self.data_dir, name, status="running", kind=kind, started_iso=started_utc
        )

        run_dir = self.data_dir / name

        self.run = RunInfo(name=name, kind=kind, duration_s=duration_s,
                           started_mono=time.monotonic(), started_utc=started_utc,
                           scan=scan)
        self.phase = RunPhase.RUNNING
        self.scan_angles = angles
        self.scan_index = 0
        self.scan_step_state = ""
        self.scan_point_started_mono = 0.0

        if scan is None:
            self.hardware.start_daq(run_dir)
        else:
            # A scan counts per angle, so it opens and closes its own DAQ
            # file at each point rather than one file for the whole run.
            # Only an estimate: the moves between angles take time too.
            self.run.duration_s = len(angles) * acquisition_s
            self._scan_task = asyncio.create_task(
                self._run_scan(run_dir, angles, acquisition_s)
            )

        # Panel ledi. En sonda: yukaridaki adimlardan biri patlarsa
        # (ayni isimde olcum var gibi) run baslamamis olur, led de
        # yanmamali.
        self.hardware.set_measuring_led(True)

        return {"name": name, "kind": kind, "angles": angles}

    def stop_run(self, status: str = "done") -> dict:
        if self.phase != RunPhase.RUNNING:
            raise DaemonError("No active run to stop")

        self._cancel_scan_task()
        channels = self.hardware.stop_daq()
        self.hardware.set_measuring_led(False)
        self._storage.mark_done(self.data_dir, status=status)

        finished_name = self.run.name
        self.run = RunInfo()
        self.phase = RunPhase.IDLE
        self.rate_hz = 0.0
        self.scan_angles = []
        self.scan_index = 0
        self.scan_step_state = ""
        self.scan_point_started_mono = 0.0
        return {
            "name": finished_name,
            "final_daq_count": sum(c["counts"] for c in channels),
            "channels": channels,
            "livetime_s": max((c["livetime_s"] for c in channels), default=0.0),
        }

    def autostop_if_due(self) -> dict | None:
        """Stop a fixed-duration run once its time is up. Called once per
        broadcast tick by the server.

        Only plain runs: a scan ends when its last angle is finished, and
        the moves between angles take time no fixed duration can predict,
        so a clock must not cut it short mid-point.
        """
        if self.phase != RunPhase.RUNNING:
            return None
        if self.run.scan is not None or self.run.duration_s is None:
            return None
        if time.monotonic() - self.run.started_mono < self.run.duration_s:
            return None
        return self.stop_run()

    # ---- scan execution --------------------------------------------------

    def _cancel_scan_task(self) -> None:
        task, self._scan_task = self._scan_task, None
        if task is None or task.done():
            return
        try:
            # The scan calls stop_run() itself after its last angle;
            # cancelling from inside would kill it mid-cleanup.
            if asyncio.current_task() is task:
                return
        except RuntimeError:
            pass  # no running loop (unit test) -- nothing to cancel into
        task.cancel()

    async def _run_scan(self, run_dir: Path, angles: list[float], acquisition_s: float) -> None:
        """Walk the scan angles: move, settle, count, write a summary row.

        One DAQ file per angle plus one summary row per angle, in the
        same layout muon_control.scan writes for the CLI path, so the
        offline analysis does not have to know which one produced the
        data. Ends the run itself once the last angle is done -- that is
        what makes a scan finish on its own.
        """
        cfg = getattr(self.hardware, "cfg", None) or {}
        settle_s = float(cfg.get("scan", {}).get("settle_s", 5.0))
        summary_path = Path(run_dir) / "summary.txt"
        status = "done"

        try:
            # buffering=1: each row reaches the disk as it is written, so
            # an interrupted scan still leaves every completed angle.
            with open(summary_path, "w", buffering=1) as sf:
                sf.write("# scan_run\n")
                sf.write(f"# utc_start = {_now_iso()}\n")
                sf.write(f"# acquisition_s = {acquisition_s!r}\n")
                sf.write(f"# settle_s = {settle_s!r}\n")
                # Built from the channel count rather than written out
                # by hand, so the header always matches the rows even if
                # a channel is added or unplugged.
                columns = ["theta_deg", "angle_actual_deg", "livetime_s"]
                for channel in range(len(self.hardware.daq_channels)):
                    columns += [f"ch{channel}_counts", f"ch{channel}_rate_Hz"]
                columns += ["temp_C", "humidity_RH", "tilt_deg", "event_file_stem"]
                sf.write("# columns = " + " ".join(columns) + "\n")

                for index, theta in enumerate(angles, start=1):
                    self.scan_index = index

                    self.scan_step_state = "moving"
                    await self.hardware.move_to_angle(theta)

                    self.scan_step_state = "settling"
                    await asyncio.sleep(settle_s)

                    self.scan_step_state = "acquiring"
                    self.scan_point_started_mono = time.monotonic()
                    # Both angles go in the name: the one we asked for, and
                    # the one the IMU says we actually got. The requested
                    # angle keeps the files sorted and predictable; the
                    # measured one is what the physics is binned by, and
                    # having it on the file means a directory listing
                    # already tells you where each spectrum was taken.
                    # start_daq appends _ch0/_ch1, one file per channel.
                    measured = self.hw.angle_deg
                    stem = f"scan_th{theta:+06.1f}_real{measured:+06.2f}"
                    self.hardware.start_daq(run_dir, stem=stem)
                    await asyncio.sleep(acquisition_s)
                    channels = self.hardware.stop_daq()

                    hw = self.hw
                    livetime_s = max((c["livetime_s"] for c in channels), default=0.0)
                    fields = [f"{theta:.3f}", f"{hw.angle_deg:.3f}", f"{livetime_s:.3f}"]
                    for c in channels:
                        rate = c["counts"] / c["livetime_s"] if c["livetime_s"] else float("nan")
                        fields += [str(c["counts"]), f"{rate:.4f}"]
                    fields += [f"{hw.temp_c:.1f}", f"{hw.humidity_rh:.1f}",
                               f"{hw.imu_tilt_deg:.2f}", stem]
                    sf.write(" ".join(fields) + "\n")

                    log.info(
                        "Scan point %d/%d done: theta=%.1f counts=%s",
                        index, len(angles), theta,
                        ", ".join(f"ch{c['channel']}={c['counts']}" for c in channels),
                    )

        except asyncio.CancelledError:
            # Stopped by the operator. stop_run() is already doing the
            # DAQ and storage cleanup, so just let the task unwind.
            self.scan_step_state = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 -- a scan failure must not kill the daemon
            log.exception("Scan failed at point %d: %s", self.scan_index, exc)
            self.scan_step_state = "failed"
            status = "error"
            self.hardware.stop_daq()

        self.scan_step_state = "finished" if status == "done" else self.scan_step_state
        try:
            self.stop_run(status=status)
        except DaemonError:
            pass  # already stopped from elsewhere -- nothing left to do

    # ---- calibration ---------------------------------------------------

    async def calibrate_move(self, angle_deg: float) -> dict:
        if self.phase != RunPhase.IDLE:
            raise DaemonError(f"Cannot calibrate while daemon is in '{self.phase.value}' state")

        self.phase = RunPhase.CALIBRATING
        try:
            return await self.hardware.move_to_angle(angle_deg)
        except HardwareError as exc:
            raise DaemonError(str(exc)) from exc
        finally:
            self.phase = RunPhase.IDLE

    async def calibrate_steps(self, n_steps: int) -> dict:
        if self.phase != RunPhase.IDLE:
            raise DaemonError(f"Cannot calibrate while daemon is in '{self.phase.value}' state")

        self.phase = RunPhase.CALIBRATING
        try:
            return await self.hardware.move_steps(n_steps)
        except HardwareError as exc:
            raise DaemonError(str(exc)) from exc
        finally:
            self.phase = RunPhase.IDLE

    async def stop_motor(self) -> dict:
        """Emergency stop -- deliberately has NO phase check. A stuck or
        runaway motor must be stoppable no matter what state we think
        we're in; this is a safety escape hatch, not a normal command."""
        await self.hardware.stop_motor()
        return {"stopped": True}

    async def imu_sample(self, duration_s: float = 10.0) -> dict:
        """Average the IMU over a window for one calibration point.

        A calibration point needs a much quieter number than the live
        panel does, so this averages fresh readings rather than handing
        back the cached one-per-second value.
        """
        if self.phase != RunPhase.IDLE:
            raise DaemonError(f"Cannot sample the IMU while daemon is in '{self.phase.value}' state")

        self.phase = RunPhase.CALIBRATING
        try:
            return await self.hardware.sample_imu(duration_s)
        except HardwareError as exc:
            raise DaemonError(str(exc)) from exc
        finally:
            self.phase = RunPhase.IDLE

    async def imu_set_calibration(self, offset_deg: float, ellipse: dict | None = None,
                                  points: list[dict] | None = None) -> dict:
        """Store the calibration the screen worked out: the mounting
        offset, and optionally the sensor ellipse that goes with it.

        If the screen also sent the points it fitted, keep a record of
        them. calibration.json holds only the answer; the record holds
        the working, which is what you need months later to say how good
        the calibration was.
        """
        try:
            result = await self.hardware.set_imu_calibration(offset_deg, ellipse)
        except HardwareError as exc:
            raise DaemonError(str(exc)) from exc

        if points:
            try:
                result["record"] = str(self._write_calibration_record(points, offset_deg, ellipse))
            except Exception:  # noqa: BLE001 -- a lost record must not lose the calibration
                log.exception("Could not write the IMU calibration record")

        return result

    def _write_calibration_record(self, points: list[dict], offset_deg: float,
                                  ellipse: dict | None) -> Path:
        """Write the calibration record, then draw it in a separate
        process.

        The tilts are recomputed here through the driver that just had
        the new calibration applied, so the record shows what the
        telescope will actually report -- not what the screen predicted.
        """
        from muon_control.calibration_record import write_record

        imu = self.hardware.imu
        rows = []
        for p in points:
            rows.append({
                "ref": float(p["ref"]),
                "imu": imu.tilt_from_axes(float(p["ay"]), float(p["az"])),
                "sigma": float(p.get("sigma", 0.0)),
                "ay": float(p["ay"]),
                "az": float(p["az"]),
                "n": int(p.get("n", 0)),
            })

        cfg = getattr(self.hardware, "cfg", None) or {}
        path = write_record(
            folder=self.data_dir / "imu_calibration",
            points=rows,
            offset_deg=offset_deg,
            ellipse=ellipse,
            reference_accuracy_deg=float(
                cfg.get("imu", {}).get("reference_accuracy_deg", 0.0)
            ),
        )
        log.info("IMU calibration record written to %s", path)

        # Drawn in its own process on purpose: importing matplotlib can
        # take seconds the first time (it builds a font cache), and in
        # here that would stall the daemon mid-calibration.
        script = Path(__file__).resolve().parents[2] / "scripts" / "plot_imu_calibration.py"
        try:
            subprocess.Popen([sys.executable, str(script), str(path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001 -- no matplotlib is not an error
            log.warning("Could not start the calibration plot; run %s by hand", script)

        return path

    # ---- status snapshot ------------------------------------------------

    def snapshot(self) -> dict:
        """A JSON-serializable snapshot of everything a client needs to render."""
        elapsed_s = 0.0
        if self.phase == RunPhase.RUNNING:
            elapsed_s = time.monotonic() - self.run.started_mono

        return {
            "phase": self.phase.value,
            "run": {
                "active": self.phase == RunPhase.RUNNING,
                "name": self.run.name,
                "kind": self.run.kind,
                "duration_s": self.run.duration_s,
                "started_utc": self.run.started_utc,
                "elapsed_s": elapsed_s,
                "daq_count": self.hardware.daq_count,
                "daq_counts": self.hardware.daq_counts,
                "rate_hz": self.rate_hz,
                "scan": {
                    "active": self.run.scan is not None,
                    "index": self.scan_index,
                    "total": len(self.scan_angles),
                    "angle_deg": (
                        self.scan_angles[self.scan_index - 1]
                        if 0 < self.scan_index <= len(self.scan_angles)
                        else 0.0
                    ),
                    "step_state": self.scan_step_state,
                    "acquisition_s": (
                        float(self.run.scan.get("acquisition_s", 0.0))
                        if self.run.scan else 0.0
                    ),
                    "point_elapsed_s": (
                        time.monotonic() - self.scan_point_started_mono
                        if self.scan_step_state == "acquiring"
                        and self.scan_point_started_mono
                        else 0.0
                    ),
                },
            },
            "hardware": {
                "angle_deg": self.hw.angle_deg,
                "target_angle_deg": self.hw.target_angle_deg,
                "temp_c": self.hw.temp_c,
                "humidity_rh": self.hw.humidity_rh,
                "imu_stable": self.hw.imu_stable,
                "imu_tilt_deg": self.hw.imu_tilt_deg,
                "imu_rms_dps": self.hw.imu_rms_dps,
                "imu_offset_deg": self.hw.imu_offset_deg,
                "imu_calibrated": self.hw.imu_calibrated,
                "imu_calibration_utc": self.hw.imu_calibration_utc,
                "hv_connected": self.hw.hv_connected,
                "hv_board": self.hw.hv_board,
                "hv_channels": self.hw.hv_channels,
                "hv_error": self.hw.hv_error,
                "limits": {
                    "min_deg": self.hardware.soft_limits[0],
                    "max_deg": self.hardware.soft_limits[1],
                },
            },
        }