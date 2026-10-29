"""
muon_daemon.hardware — Wraps the real muon_control drivers behind an
async-friendly interface.

Blocking hardware calls are offloaded to worker threads via
loop.run_in_executor, and there is one worker per piece of hardware that
can only do one thing at a time:

    motion   the stepper (GPIO)
    sensors  the IMU (I2C) and the temperature sensor
    hv       the CAEN N1470 (USB serial)

The DAQ is on none of them, and that is the point. Counting is a
gpiozero edge callback running on gpiozero's own thread, and the first
thing it does is take a nanosecond timestamp -- put that behind any
queue and the timestamps it exists to produce become worthless.
start_daq/stop_daq only open and close the file, so they are called
straight from the event loop.

Motion and counting never overlap anyway: a scan moves, settles, then
counts, and calibrate_move is refused while a run is active. But that
is the state machine's doing, not this file's, and counting would keep
working if it ever did overlap -- which is the safer way round.

Each of those three is a separate physical connection, so nothing is
gained by making them wait for each other -- and quite a lot is lost.
A move holds its worker for the whole motion, which with the 100:1
gearbox is minutes. When the sensors shared that worker, every IMU read
queued behind the move and the angle on screen froze for exactly as
long as the telescope was turning: the one moment you most want to
watch it. One worker *within* each group is still deliberate, so two
reads never land on the same bus at once.

Dependency injection: pass fake stepper/imu/limits/temperature objects
for testing (mirroring the storage_backend pattern in state.py). On the
Pi, leaving them as None constructs the real drivers from config.
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("muon_daemon.hardware")


class HardwareError(RuntimeError):
    """Raised when a real hardware operation fails (limit switch hit,
    sensor read failure, etc). state.py catches this and turns it into
    a DaemonError for the protocol layer."""


@dataclass
class HardwareState:
    angle_deg: float = 0.0
    target_angle_deg: float = 0.0
    temp_c: float = 0.0
    humidity_rh: float = 0.0
    imu_stable: bool = True
    imu_tilt_deg: float = 0.0
    imu_rms_dps: float = 0.0
    imu_offset_deg: float = 0.0
    imu_calibrated: bool = False
    imu_calibration_utc: str = ""
    hv_connected: bool = False
    hv_board: dict = field(default_factory=dict)
    hv_channels: list = field(default_factory=list)
    hv_error: str = ""



class _RollingRms:
    """Simple fixed-window rolling RMS over gyro magnitude samples,
    used as the stability metric (see the vibration/creep discussion --
    this is the "settle & verify" style RMS window, not a full FFT)."""

    def __init__(self, window_size: int = 30) -> None:
        self._samples: deque[float] = deque(maxlen=window_size)

    def add(self, value: float) -> float:
        self._samples.append(value)
        if len(self._samples) < 2:
            return 0.0
        mean = sum(self._samples) / len(self._samples)
        variance = sum((x - mean) ** 2 for x in self._samples) / len(self._samples)
        return math.sqrt(variance)


# Fallbacks for the stable/vibration flag when the config does not set
# them. Two thresholds rather than one so the flag cannot flap while the
# RMS sits right at the boundary: it takes the higher one to raise the
# flag and the lower one to clear it.
_DEFAULT_VIBRATION_ON_DPS = 0.5
_DEFAULT_VIBRATION_OFF_DPS = 0.3

# How long to wait after a motion before trusting the IMU for a position
# seed. The gearbox rings for a moment after the last step, and disable()
# has just released the coils -- averaging straight away would fold that
# transient into the seed. Same wait move_steps already uses.
_SEED_SETTLE_S = 2.0

# A direction check only means something if we asked for a move big
# enough to see above the IMU noise.
_DIRECTION_CHECK_MIN_DEG = 2.0


class HardwareController:
    def __init__(
        self,
        cfg: dict,
        hw_state: HardwareState,
        executor: ThreadPoolExecutor,
        stepper=None,
        imu=None,
        limits=None,
        temperature=None,
        hv=None,
    ) -> None:
        self.cfg = cfg
        self.hw = hw_state
        # The caller's executor drives the motor. See the module docstring
        # for why the sensors and the HV get their own, and why the DAQ
        # is on none of them.
        self._motion_executor = executor
        self._rms = _RollingRms(window_size=30)  # ~30s window at 1Hz polling

        # Vibration thresholds, in deg/s on the gyro RMS. They live in
        # the config because the right value depends on the bench, not
        # on the code.
        imu_cfg = (cfg or {}).get("imu", {})
        self._vibration_on_dps = float(
            imu_cfg.get("vibration_on_dps", _DEFAULT_VIBRATION_ON_DPS)
        )
        self._vibration_off_dps = float(
            imu_cfg.get("vibration_off_dps", _DEFAULT_VIBRATION_OFF_DPS)
        )
        self._daq: list[dict] = []  # one entry per channel, filled in start_daq()
        # Cooperative stop signal for in-progress motor motion. A
        # threading.Event is the right primitive here (not an asyncio
        # one): it's set from the asyncio event loop's thread while the
        # blocking motion runs on the executor's worker thread, and
        # threading.Event is safe to signal across threads.
        self._stop_event = threading.Event()

        if limits is None:
            try:
                from muon_control.drivers.limit_switches import LimitSwitches
                limits = LimitSwitches.from_config(cfg)
            except Exception as exc:
                log.error("Limit switches unavailable, motion will be unprotected: %s", exc)
                limits = None
        self._limits = limits

        if stepper is None:
            try:
                from muon_control.drivers.stepper import Stepper
                stepper = Stepper.from_config(cfg, limits=limits)
            except Exception as exc:
                log.error("Stepper unavailable -- calibration commands will be rejected: %s", exc)
                stepper = None
        self._stepper = stepper

        if imu is None:
            try:
                from muon_control.drivers.imu import IMU
                imu = IMU.from_config(cfg)
            except Exception as exc:
                log.error("IMU unavailable -- hardware panel will show stale/default values: %s", exc)
                imu = None
        self._imu = imu

        # On panel ledleri. Yoksa da olcum alinir, o yuzden diger
        # opsiyonel surucular gibi: kurulamazsa uyar ve None ile devam.
        try:
            from muon_control.drivers.leds import StatusLeds
            self._leds = StatusLeds.from_config(cfg)
        except Exception as exc:  # noqa: BLE001
            log.error("Front panel LEDs unavailable: %s", exc)
            self._leds = None

        # On panel ekrani ve butonlari -- ayni kural: yoksa olcum yine
        # alinir. Ekran IMU ile ayni I2C bus'ta (bus 1), o yuzden
        # yazmalari IMU okumalariyla ayni worker'da siraya girer
        # (show_panel), iki cihazin islemleri birbirine karismasin.
        try:
            from muon_control.drivers.display import Display
            self._display = Display.from_config(cfg)
        except Exception as exc:  # noqa: BLE001
            log.error("Front panel display unavailable: %s", exc)
            self._display = None
        try:
            from muon_control.drivers.buttons import FrontPanelButtons
            self._buttons = FrontPanelButtons.from_config(cfg)
        except Exception as exc:  # noqa: BLE001
            log.error("Front panel buttons unavailable: %s", exc)
            self._buttons = None

        if temperature is None:
            try:
                from muon_control.drivers.temperature import Temperature
                temperature = Temperature.from_config(cfg)
            except Exception as exc:
                log.error("Temperature sensor unavailable: %s", exc)
                temperature = None
        self._temperature = temperature

        if hv is None:
            try:
                from muon_control.drivers.hv import HVSupply
                hv = HVSupply.from_config(cfg)
            except Exception as exc:
                log.error("HV supply unavailable -- the HV panel will stay empty: %s", exc)
                hv = None
        self._hv = hv

        # The IMU and the temperature sensor read on their own worker, so
        # a move cannot block them. They share one worker with each other
        # because a single I2C read at a time is the safe way to use the
        # bus, and both reads are over in milliseconds anyway.
        self._sensor_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sensor")

        # How far below a target a downward move swings before coming
        # back up. See _move_blocking for what this buys.
        self._approach_margin_deg = float(
            (cfg or {}).get("stepper", {}).get("approach_margin_deg", 0.0)
        )

        # Which side every angle is approached from. Gravity decides
        # this, not taste -- see the config comment and _move_blocking.
        approach_from = str(
            (cfg or {}).get("stepper", {}).get("approach_from", "above")
        ).lower()
        if approach_from not in ("above", "below"):
            log.warning(
                "approach_from=%r is not 'above' or 'below' -- using 'above'",
                approach_from,
            )
            approach_from = "above"
        self._approach_from = approach_from

        # The N1470 is on its own USB serial port, so it shares nothing
        # with either of the above and gets its own worker for the same
        # reason: reading all four channels takes about a second, and
        # queueing that behind anything else only delays both.
        self._hv_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hv")

        # Reflect the stepper's own boot-time state. The step count
        # starts at 0 on every power cycle and is not trustworthy until
        # seed_from_imu() runs (see server.py's start(), which does this
        # once right after the daemon comes up). If the stepper itself
        # failed to construct, keep the dataclass default (angle=0.0).
        if self._stepper is not None:
            self.hw.angle_deg = stepper.angle_deg

        # Surface the calibration that was loaded from disk (if any), so
        # the TUI can tell the operator it is running a stored one rather
        # than silently starting from a zero offset.
        self._refresh_imu_calibration_state()

    def _refresh_imu_calibration_state(self) -> None:
        if self._imu is None:
            return
        # getattr throughout: an injected test double is only required to
        # provide read()/tilt_from_axes(), not the calibration bookkeeping.
        self.hw.imu_offset_deg = getattr(self._imu, "offset_deg", 0.0)
        self.hw.imu_calibrated = getattr(self._imu, "calibration_loaded", False)
        self.hw.imu_calibration_utc = getattr(self._imu, "calibration_saved_utc", "")

    async def _run_blocking(self, func, *args):
        """Run a motor or DAQ call on the motion worker."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._motion_executor, func, *args)

    async def _run_sensor_blocking(self, func, *args):
        """Run a sensor read on the sensor worker.

        Separate from _run_blocking so a reading is never stuck behind a
        motion: that is the whole reason the two workers exist.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._sensor_executor, func, *args)

    # ---- calibration (called from state.py, offloaded to executor) -------

    async def _average_imu(self, duration_s: float) -> tuple[int, float, float, float]:
        """Sample the IMU repeatedly for duration_s and return
        (sample count, mean ay, mean az, standard error of the tilt).

        A single reading carries ~0.2 deg of noise; averaging several
        seconds of samples brings that down a lot. How far down is not a
        guess -- it comes out of the samples themselves, which is why the
        spread is measured here rather than assumed. The returned figure
        is the standard error of the MEAN (sigma / sqrt(n)), because the
        mean is what gets used, not one sample. That is the error bar on
        a calibration point.
        """
        samples = []
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            samples.append(await self._run_sensor_blocking(self._imu.read))
            await asyncio.sleep(0.05)

        n = len(samples)
        ay = sum(s["ay"] for s in samples) / n
        az = sum(s["az"] for s in samples) / n

        # Spread of the per-sample tilt, not of the raw axes: the tilt is
        # what the calibration compares against the reference, so its own
        # scatter is the honest uncertainty.
        tilts = [self._imu.raw_tilt_from_axes(s["ay"], s["az"]) for s in samples]
        if n > 1:
            mean_tilt = sum(tilts) / n
            variance = sum((t - mean_tilt) ** 2 for t in tilts) / (n - 1)
            sem_deg = math.sqrt(variance / n)
        else:
            sem_deg = 0.0

        return n, ay, az, sem_deg

    async def sample_imu(self, duration_s: float = 10.0) -> dict:
        """Average the raw axes over a window, for one IMU-offset
        calibration point (see imu_calibration.py).

        The tilt returned here is the UNCORRECTED one. The calibration
        screen fits an offset by comparing this against an inclinometer,
        so handing it an already-corrected reading would have it measure
        the error of a number that has had the error taken out, fit ~0,
        and save that over the good offset -- one press of Recalibrate
        would throw the calibration away.
        """
        if self._imu is None:
            raise HardwareError("IMU not available")

        n, ay, az, sem_deg = await self._average_imu(duration_s)
        raw_tilt = self._imu.raw_tilt_from_axes(ay, az)

        return {
            "n": n,
            "ay": ay,
            "az": az,
            "raw_tilt_deg": raw_tilt,
            "sigma_deg": sem_deg,
        }

    async def set_imu_calibration(self, offset_deg: float,
                                  ellipse: dict | None = None) -> dict:
        """Apply and persist the calibration, so it survives a daemon
        restart without the operator doing it again.

        ellipse is optional: with too few points to fit one the screen
        sends only the offset, and the sensor correction stays at its
        do-nothing defaults.
        """
        if self._imu is None:
            raise HardwareError("IMU not available")

        self._imu.offset_deg = offset_deg
        if ellipse:
            self._imu.bias_y = float(ellipse["bias_y"])
            self._imu.bias_z = float(ellipse["bias_z"])
            self._imu.scale_y = float(ellipse["scale_y"])
            self._imu.scale_z = float(ellipse["scale_z"])

        self._imu.save_calibration()
        self._refresh_imu_calibration_state()
        log.info("IMU calibration saved: offset %+.3f deg, ellipse %s",
                 offset_deg, "fitted" if ellipse else "unchanged")
        return {"offset_deg": offset_deg, "ellipse": bool(ellipse)}

    async def seed_from_imu(self, duration_s: float = 5.0,
                           settle_s: float = _SEED_SETTLE_S) -> dict:
        """Set the stepper's absolute position from a few seconds of
        averaged IMU reading. This replaces homing: there is no
        mechanical origin to drive to any more, the IMU already knows
        the true angle, so the step counter is just set to match it.
        Called once when the daemon starts, and again after every
        completed move (see move_to_angle) so a missed step never
        lingers uncorrected."""
        if self._stepper is None or self._imu is None:
            raise HardwareError("Stepper or IMU not available -- cannot seed position")

        # Let the mechanism come to rest first, otherwise the average is
        # taken across the settling transient and the seed is wrong --
        # which then poisons the NEXT move, since that one computes its
        # delta from this position.
        if settle_s > 0:
            await asyncio.sleep(settle_s)

        n, ay, az, _ = await self._average_imu(duration_s)
        angle_deg = self._imu.tilt_from_axes(ay, az)
        await self._run_blocking(self._stepper.seed_position, angle_deg)

        self.hw.angle_deg = self._stepper.angle_deg
        return {"angle_deg": self.hw.angle_deg, "n": n}

    async def move_to_angle(self, angle_deg: float) -> dict:
        """Move to an angle, then measure where the telescope actually
        ended up and report that.

        One move, no retries. A move never lands exactly on the number
        you asked for: gearbox backlash, the odd missed step and an
        imperfect steps_per_deg each leave it a fraction of a degree
        off. None of that needs correcting, because nothing downstream
        uses the angle we *asked* for. The IMU measures where the
        telescope really is, and that measured angle is what goes on the
        event filename and into the summary row. Asking for 10 deg and
        recording 9.6 deg is exactly as good a measurement as landing on
        10.0 -- it is the same physics, honestly labelled.

        Each move is also computed from the freshly seeded position, not
        from a running total, so the error cannot accumulate from one
        angle to the next.
        """
        if self._stepper is None:
            raise HardwareError("Stepper driver not available (failed to initialize at startup)")

        self.hw.target_angle_deg = angle_deg
        before = self.hw.angle_deg

        self._stop_event.clear()
        try:
            await self._run_blocking(self._move_blocking, angle_deg)
        except Exception as exc:  # LimitError/StoppedError etc.
            raise HardwareError(str(exc)) from exc
        finally:
            # Not a correction -- this is what keeps the step counter
            # honest. The soft limits are checked against it, so if it
            # drifted away from reality they would stop meaning
            # anything. Runs whether the move finished or was cut short.
            try:
                await self.seed_from_imu()
            except Exception as seed_exc:  # noqa: BLE001 -- must not mask the motion error
                log.warning("Could not re-seed position from IMU after a move: %s", seed_exc)
                self.hw.angle_deg = self._stepper.angle_deg

        after = self.hw.angle_deg
        commanded = angle_deg - before
        measured = after - before

        # Did the telescope move the way we asked? If it went the other
        # way, the DIR wiring and the invert_direction flag disagree.
        # This is a wiring fault, not a small error, so it is worth
        # saying out loud rather than quietly recording the result.
        if (abs(commanded) > _DIRECTION_CHECK_MIN_DEG
                and abs(measured) > _DIRECTION_CHECK_MIN_DEG
                and (measured > 0) != (commanded > 0)):
            raise HardwareError(
                f"Motor turned the wrong way: asked for {commanded:+.1f}deg, "
                f"the IMU measured {measured:+.1f}deg. The DIR wiring and "
                f"'invert_direction' in the config's [stepper] section "
                f"disagree -- flip that flag."
            )

        log.info("Asked for %.2f deg, the IMU measures %.2f deg", angle_deg, after)

        return {
            "angle_deg": after,
            "target_angle_deg": angle_deg,
            "error_deg": angle_deg - after,
        }

    def _move_blocking(self, angle_deg: float) -> None:
        """Drive to angle_deg, always finishing the last motion on the
        same side -- and, by default, on the side gravity pulls toward.

        A gearbox has a dead zone -- backlash. Reverse the motor and it
        turns a little before the telescope moves at all, so which side
        of the teeth are touching depends on which way you last moved.
        Arrive at the same angle from above once and from below once and
        the telescope ends up in two slightly different places.

        The fix costs nothing but time: a move that would arrive from
        the wrong side goes PAST the target by approach_margin_deg
        first, then comes back. Every angle is then reached with the
        teeth loaded the same way, which is what makes two visits to the
        same angle comparable.

        WHICH side matters, because this move ends with disable(): the
        coils are released and 12V is cut, so afterwards nothing holds
        the load but the gearbox. The telescope is bottom-heavy, so
        gravity always pulls it toward smaller angles. Finish upwards
        and the teeth are loaded against gravity -- the instant the
        power goes, the load sags back across the whole dead zone, and
        the closed loop above cannot fix it because its correction sags
        exactly the same way. Finish downwards and the teeth already
        rest on the flank gravity holds them against, so releasing the
        motor changes nothing. The unbalance stops being an error source
        and becomes a free preload.

        Note this is not a correction applied to the number. We are not
        guessing the backlash and adding it on; we are arranging never to
        care what it is.
        """
        # +1 = finish the last motion upwards, -1 = downwards.
        final_dir = -1.0 if self._approach_from == "above" else 1.0

        self._stepper.enable()
        try:
            # Would a direct move arrive from the wrong side? It would if
            # its direction is opposite to the one we want to finish in.
            wrong_side = (angle_deg - self._stepper.angle_deg) * final_dir < 0
            swing_to = angle_deg - final_dir * self._approach_margin_deg

            if wrong_side and self._approach_margin_deg > 0:
                if self._approach_from == "above":
                    have_room = swing_to <= self._stepper.soft_max_deg
                    limit = self._stepper.soft_max_deg
                else:
                    have_room = swing_to >= self._stepper.soft_min_deg
                    limit = self._stepper.soft_min_deg

                if have_room:
                    self._stepper.move_to_angle(swing_to, stop_event=self._stop_event)
                else:
                    # No room past the target to swing into. A direct
                    # move is the best available, so say so rather than
                    # let the caller believe the approach was clean.
                    log.warning(
                        "No room to approach %.1f deg from %s (soft limit %.1f) -- "
                        "moving straight there, so this angle is on the other side "
                        "of the backlash from the rest",
                        angle_deg, self._approach_from, limit,
                    )

            self._stepper.move_to_angle(angle_deg, stop_event=self._stop_event)
        finally:
            self._stepper.disable()

    @property
    def approach_from(self) -> str:
        """'above' or 'below' -- which side every angle is reached from.
        A scan reads this so it can visit its angles in the order that
        needs no extra swing (see DaemonState.start_run)."""
        return self._approach_from

    async def move_steps(self, n_steps: int) -> dict:
        """Relative move by a known number of steps. Used to measure
        steps/deg: the IMU tilt is read before and after, and the caller
        divides the step count by the measured angle change."""
        if self._stepper is None:
            raise HardwareError("Stepper driver not available")

        angle_before = self.hw.imu_tilt_deg
        self._stop_event.clear()
        try:
            await self._run_blocking(self._move_steps_blocking, n_steps)
        except Exception as exc:
            raise HardwareError(str(exc)) from exc

        # Let the mechanism settle before reading the IMU again -- the
        # gearbox rings for a moment after the last step.
        await asyncio.sleep(2.0)
        angle_after = self.hw.imu_tilt_deg

        self.hw.angle_deg = self._stepper.angle_deg
        return {
            "angle_before_deg": angle_before,
            "angle_after_deg": angle_after,
            "steps_angle_deg": self._stepper.angle_deg,
        }

    def _move_steps_blocking(self, n_steps: int) -> None:
        self._stepper.enable()
        try:
            self._stepper.move_steps(n_steps, stop_event=self._stop_event)
        finally:
            self._stepper.disable()

    async def stop_motor(self) -> None:
        """Emergency stop. Deliberately NOT offloaded to the executor:
        setting a threading.Event is instant and must work even while
        the executor's single worker thread is busy mid-motion -- that's
        the entire point of this call."""
        self._stop_event.set()

    # ---- DAQ lifecycle (tied to run start/stop, not continuous) -----------

    @property
    def daq_channels(self) -> list[int]:
        """The DAQ pins from config, in channel order.

        A list, because the board has two conditioned inputs and there is
        no reason the software should only look at one of them. Older
        configs with a single `daq_signal` still work.
        """
        gpio = (self.cfg or {}).get("gpio", {})
        pins = gpio.get("daq_signals")
        if pins is None:
            single = gpio.get("daq_signal")
            pins = [single] if single is not None else []
        return list(pins)

    def start_daq(self, run_dir: Path, stem: str = "daq_events") -> None:
        """Start one counter per channel, each writing its own file.

        stem names the set: a scan passes the angle, so a point becomes
        <stem>_ch0.txt and <stem>_ch1.txt. Separate files rather than one
        interleaved file, because each channel is an independent
        measurement and nothing downstream should have to split them
        apart again.
        """
        from muon_control.drivers.daq import DAQCounter

        daq_cfg = (self.cfg or {}).get("daq", {})
        self._daq = []
        for channel, pin in enumerate(self.daq_channels):
            filename = f"{stem}_ch{channel}.txt"
            counter = DAQCounter(
                pin=pin,
                out_path=Path(run_dir) / filename,
                gate_width_s=daq_cfg["gate_width_s"],
                pull_up=daq_cfg.get("pull_up", True),
            )
            counter.start()
            self._daq.append(
                {"channel": channel, "pin": pin, "file": filename, "counter": counter}
            )

    def stop_daq(self) -> list[dict]:
        """Stop every counter. One result per channel:

            {"channel", "pin", "file", "counts", "livetime_s"}

        Livetime is per channel rather than shared. They start and stop
        within milliseconds of each other, but a rate is counts over the
        time that channel was actually counting, and pretending otherwise
        would put a small lie in every rate.
        """
        results = []
        for entry in self._daq:
            counts, livetime_s = entry["counter"].stop()
            entry["counter"]._button.close()  # release the pin for the next point
            results.append({
                "channel": entry["channel"],
                "pin": entry["pin"],
                "file": entry["file"],
                "counts": counts,
                "livetime_s": livetime_s,
            })
        self._daq = []
        return results

    @property
    def daq_counts(self) -> list[int]:
        """Live count per channel."""
        return [entry["counter"].count for entry in self._daq]

    @property
    def daq_count(self) -> int:
        """Total across channels -- the one number the rate display and
        the Influx publisher want."""
        return sum(self.daq_counts)

    @property
    def imu(self):
        """The IMU driver, so the daemon can recompute a tilt through the
        calibration that is actually loaded. None if it failed to start."""
        return self._imu

    @property
    def soft_limits(self) -> tuple[float, float]:
        """Soft limits from config, so the TUI can show the allowed range.
        Falls back to a wide range if the stepper failed to construct."""
        if self._stepper is None:
            return (0.0, 0.0)
        return (self._stepper.soft_min_deg, self._stepper.soft_max_deg)

    # ---- background polling (continuous, independent of run state) -------

    async def poll_imu_forever(self, interval_s: float = 1.0) -> None:
        """Report tilt and a rolling RMS of the gyro magnitude, once a
        second, for the hardware panel.

        This is a live activity indicator, not a measurement gate. It
        deliberately does no settle-time or accel-magnitude check before
        trusting the tilt: move_to_angle does its own waiting (see
        seed_from_imu) and records the angle it measures, so there is no
        second verification step for this loop to feed. An earlier design
        put one here; the simpler arrangement replaced it."""
        while True:
            if self._imu is None:
                await asyncio.sleep(interval_s)
                continue

            try:
                reading = await self._run_sensor_blocking(self._imu.read)

                tilt = self._imu.tilt_from_axes(
                    reading["ay"],
                    reading["az"],
                )

                gyro_mag = math.sqrt(
                    reading["gx"] ** 2
                    + reading["gy"] ** 2
                    + reading["gz"] ** 2
                )

                rms = self._rms.add(gyro_mag)

                self.hw.imu_tilt_deg = tilt
                self.hw.imu_rms_dps = rms
                # Hysteresis: only flip state at the ON/OFF thresholds,
                # not a single midpoint value (avoids flapping).
                if self.hw.imu_stable and rms > self._vibration_on_dps:
                    self.hw.imu_stable = False
                elif not self.hw.imu_stable and rms < self._vibration_off_dps:
                    self.hw.imu_stable = True
            except Exception as exc:
                # A transient I2C read failure shouldn't kill the polling
                # loop -- log-and-continue.
                log.warning("IMU read failed, will retry: %s", exc)

            await asyncio.sleep(interval_s)

    async def _run_hv_blocking(self, func, *args):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._hv_executor, func, *args)

    async def poll_hv_forever(self, interval_s: float | None = None) -> None:
        """Read the HV board and every channel, slowly and forgivingly.

        Reconnects on its own: the supply can be switched off or
        unplugged and the daemon must not need a restart when it comes
        back, so a failure only records the reason and the next cycle
        tries again from connect().
        """
        if interval_s is None:
            interval_s = float((self.cfg or {}).get("hv", {}).get("poll_interval_s", 15.0))

        while True:
            if self._hv is None:
                await asyncio.sleep(interval_s)
                continue

            try:
                if not self._hv.connected:
                    await self._run_hv_blocking(self._hv.connect)
                    board = await self._run_hv_blocking(self._hv.board_info)
                    self.hw.hv_board = board
                    log.info("HV connected: %s (fw %s, s/n %s, %d channels)",
                             board.get("model"), board.get("firmware"),
                             board.get("serial_number"), board.get("channels", 0))
                else:
                    # Only the parts that change while running; the rest
                    # of the board info was read once on connect.
                    self.hw.hv_board.update(
                        await self._run_hv_blocking(self._hv.read_board_status)
                    )

                self.hw.hv_channels = await self._run_hv_blocking(self._hv.read_all_channels)
                self.hw.hv_connected = True
                self.hw.hv_error = ""
            except Exception as exc:  # noqa: BLE001 -- HV must never kill the daemon
                if self.hw.hv_connected:
                    log.warning("Lost the HV supply, will keep retrying: %s", exc)
                self.hw.hv_connected = False
                self.hw.hv_error = str(exc)
                self.hw.hv_channels = []

            await asyncio.sleep(interval_s)

    # ---- front panel LEDs -------------------------------------------------

    def set_active_led(self, on: bool) -> None:
        """'Daemon calisiyor' ledi. Daemon acilirken yanar, kapanirken soner."""
        if self._leds is None:
            return
        try:
            self._leds.set_active(on)
        except Exception:  # noqa: BLE001 -- bir led olcumu durdurmaz
            log.warning("Could not drive the ACTIVE LED", exc_info=True)

    # ---- front panel display & buttons ------------------------------------

    @property
    def buttons(self):
        """FrontPanelButtons or None. The server wires the callbacks."""
        return self._buttons

    async def show_panel(self, lines: list[str]) -> None:
        """Write 4 lines to the display, on the sensor worker (shared I2C
        bus with the IMU). Never raises: a dead display is the display's
        problem, not the run's."""
        if self._display is None:
            return
        try:
            await self._run_sensor_blocking(self._display.show, lines)
        except Exception:  # noqa: BLE001
            log.warning("Could not update the front panel display", exc_info=True)

    def set_measuring_led(self, on: bool) -> None:
        """'Olcum suruyor' ledi. Run baslayinca yanar, bitince soner."""
        if self._leds is None:
            return
        try:
            self._leds.set_measuring(on)
        except Exception:  # noqa: BLE001
            log.warning("Could not drive the MEASURING LED", exc_info=True)

    def shutdown(self) -> None:
        """Release the HV port and the workers this controller owns.

        The motion worker is not touched: the server created it and
        shuts it down itself.
        """
        if self._hv is not None:
            try:
                self._hv.close()
            except Exception:  # noqa: BLE001
                log.warning("Error closing the HV port during shutdown", exc_info=True)
        if self._leds is not None:
            # Ikisini de sondur: daemon yokken panelde "calisiyor" ya da
            # "olcum suruyor" yanik kalmasi yalan olurdu.
            self._leds.close()
        if self._buttons is not None:
            self._buttons.close()
        if self._display is not None:
            try:
                self._display.close()    # ekrani temizle: daemon yokken eski sayim kalmasin
            except Exception:  # noqa: BLE001
                pass
        self._hv_executor.shutdown(wait=False)
        self._sensor_executor.shutdown(wait=False)

    async def poll_temperature_forever(self, interval_s: float = 30.0) -> None:
        while True:
            if self._temperature is None:
                await asyncio.sleep(interval_s)
                continue

            try:
                temp_c, humidity_rh = await self._run_sensor_blocking(self._temperature.read)
                if temp_c is not None:
                    self.hw.temp_c = temp_c
                if humidity_rh is not None:
                    self.hw.humidity_rh = humidity_rh
            except Exception as exc:
                log.warning("Temperature read failed, will retry: %s", exc)

            await asyncio.sleep(interval_s)