"""
muon_control.drivers.stepper — Layer 1: TB6600 step motor driver

Design focus: POSITION IS KEPT AS AN ABSOLUTE INTEGER STEP COUNT.
The angle is a derived quantity. move_to_angle
rounds the target to the nearest step, so rounding errors never
accumulate over repeated moves.

    st = Stepper.from_config(cfg, limits=LimitSwitches.from_config(cfg))
    with st:                       # relay on + driver engaged
        st.seed_position(measured_angle)  # set position from an IMU reading
        st.move_to_angle(10.0)     # absolute 10 deg
        print(st.angle_deg)        # ~10.0 (reachable angle on the step grid)

No homing
---------
There used to be a home() step that drove the motor to the zero hall
switch and used that contact point as the origin. The IMU now reads the
absolute angle directly and more precisely than the hall switch ever
did, so there is no mechanical homing any more -- seed_position() sets
the position straight from a measured angle instead. The hall sensors
stay wired in (via `limits`) as a passive, read-only cross-check; they
are no longer used to control anything.

Direction
---------
`moving_positive` decides the DIR pin level, but whether DIR HIGH turns
the telescope towards +110 or towards -55 depends on how the motor is
wired -- the code cannot tell. On this build DIR HIGH drives towards the
NEGATIVE end, so `invert_direction` (config [stepper]) is set and the
level is flipped. If a move goes the wrong way, that flag is what to
change; the IMU calibration cannot fix it, because it only fits an
additive offset, never a sign.

Power path
----------
12V reaches the TB6600 through a relay, and the relay coil is fed from
the front panel motor toggle through the end-switch chain. So the
driver only has power when:

    software relay ON  AND  front panel toggle ON  AND  end switches closed

The relay is driven from enable()/disable() rather than being a
separate object, because it is part of "turn the motor on" from every
caller's point of view. Ordering matters: power comes up before the
driver is engaged, and goes down after it is released, so the driver
outputs are never live while the 12V rail is switching.

ENA polarity
------------
TB6600's ENA input is an optocoupler LED. Driving ENA- low lights the
LED, which *disables* the driver outputs (motor free). Our board
inverts the GPIO through a 2N7000, and measurement on the bench gave:

    GPIO HIGH -> motor free
    GPIO LOW  -> motor holding

So active_high=False maps .on() to "driver engaged", which is what
enable() means. initial_value=False leaves the motor free at startup.

Note that ENA is NOT a safety feature: an unpowered optocoupler cannot
disable anything, so a dead 5V rail leaves the driver *engaged*. The
relay is the actual power cut; ENA only avoids needless coil heating
while the telescope sits still.
"""

from __future__ import annotations

import time

from gpiozero import OutputDevice


# TB6600 setup time between DIR and STEP (datasheet asks for a few us).
_SETUP_S = 5e-6

# Let the driver's rails settle after the relay closes, before engaging.
_POWER_SETTLE_S = 0.2

# Let the driver release the coils before the 12V rail is cut.
_RELEASE_SETTLE_S = 0.05


class StepperError(RuntimeError):
    pass


class LimitError(StepperError):
    """Limit switch is active while moving in the direction of that limit."""


class StoppedError(StepperError):
    """Raised when an external stop_event was set mid-motion
    (emergency stop). Position remains accurate up to the last
    completed step -- position_steps is updated every iteration,
    so an interrupted move never loses track of where it is."""


class Stepper:
    def __init__(self, *, step_pin, dir_pin, enable_pin, relay_pin,
                 steps_per_deg, rate_hz, soft_min_deg, soft_max_deg,
                 limits=None, invert_direction=False):
        self.steps_per_deg = steps_per_deg
        self.rate = rate_hz
        self.invert_direction = invert_direction
        self.soft_min_deg = soft_min_deg
        self.soft_max_deg = soft_max_deg
        self.limits = limits

        # Relay: active-high, and it must start OFF so a daemon restart
        # never leaves 12V on the driver unattended.
        self._relay = OutputDevice(relay_pin, initial_value=False)

        # ENA: see the polarity note in the module docstring.
        self._enable = OutputDevice(enable_pin, active_high=False, initial_value=False)
        self._dir = OutputDevice(dir_pin, initial_value=False)
        self._step = OutputDevice(step_pin, initial_value=False)

        # Microstepping is set with the DIP switches on the TB6600 board
        # itself, so there is nothing to drive here. The config value is
        # only used to compute steps_per_deg -- if the DIP switches and
        # the config disagree, every move is wrong by that factor.

        # Position: ABSOLUTE INTEGER STEP COUNT (the only true state).
        self.position_steps: int = 0
        self.enabled: bool = False

    @classmethod
    def from_config(cls, cfg: dict, limits=None) -> "Stepper":
        g, s = cfg["gpio"], cfg["stepper"]
        return cls(
            step_pin=g["step"], dir_pin=g["dir"], enable_pin=g["enable"],
            relay_pin=g["relay"],
            steps_per_deg=s["steps_per_deg"],   # derived in config.py
            rate_hz=s["max_rate_hz"], limits=limits,
            invert_direction=s.get("invert_direction", False),
            soft_min_deg=cfg["safety"]["soft_limit_min_deg"],
            soft_max_deg=cfg["safety"]["soft_limit_max_deg"],
        )

    # --- power + enable/disable -------------------------------------------
    def enable(self) -> None:
        """Close the relay, wait for the rails, then engage the driver."""
        self._relay.on()
        time.sleep(_POWER_SETTLE_S)
        self._enable.on()
        self.enabled = True

    def disable(self) -> None:
        """Release the motor, then cut 12V. Safe to call when already off."""
        self._enable.off()
        self._step.off()
        time.sleep(_RELEASE_SETTLE_S)
        self._relay.off()
        self.enabled = False

    @property
    def powered(self) -> bool:
        """True when software has closed the relay. Says nothing about
        whether 12V actually arrived -- the front panel toggle and the
        end-switch chain are in series and are not readable from here."""
        return self._relay.value == 1

    # --- position ----------------------------------------------------------
    @property
    def angle_deg(self) -> float:
        """Derived angle: the actual position reached on the step grid."""
        return self.position_steps / self.steps_per_deg

    # --- low-level stepping ------------------------------------------------
    def _step_once(self, half_period: float) -> None:
        self._step.on()
        time.sleep(half_period)
        self._step.off()
        time.sleep(half_period)

    # --- absolute motion (drift-free core) ---------------------------------
    def move_to_steps(self, target_steps: int, stop_event=None) -> None:

        """Move to an absolute step position. Since the position is an
        integer, no rounding error accumulates.

        stop_event: an optional threading.Event. Checked every step;
        if set, motion halts immediately and StoppedError is raised.
        Position is left exactly where it was interrupted (still
        drift-free, since position_steps is updated per-step)."""

        target_deg = int(target_steps) / self.steps_per_deg
        if not (self.soft_min_deg <= target_deg <= self.soft_max_deg):
            raise LimitError(
                f"target {target_deg:.1f}° outside soft limits "
                f"({self.soft_min_deg} .. {self.soft_max_deg})" )
        delta = int(target_steps) - self.position_steps
        if delta == 0:
            return
        if not self.enabled:
            self.enable()

        moving_positive = delta > 0
        # Which DIR level means "increasing angle" is a wiring fact, not
        # something the code can work out on its own -- see the direction
        # note in the module docstring. invert_direction flips the level
        # without touching the position bookkeeping below, which always
        # follows the direction we asked for.
        self._dir.value = moving_positive != self.invert_direction
        time.sleep(_SETUP_S)

        half = 1.0 / (2.0 * self.rate)
        for _ in range(abs(delta)):
            if stop_event is not None and stop_event.is_set():
                raise StoppedError(
                    f"Emergency stop requested — motion halted at step {self.position_steps}"
                )
            self._step_once(half)
            self.position_steps += 1 if moving_positive else -1

    def move_to_angle(self, deg: float, stop_event=None) -> None:
        self.move_to_steps(round(deg * self.steps_per_deg), stop_event=stop_event)

    def move_steps(self, n_steps: int, stop_event=None) -> None:
        """Relative move, used by the steps/deg calibration: send a known
        number of steps and compare against what the IMU measured."""
        self.move_to_steps(self.position_steps + int(n_steps), stop_event=stop_event)

    # --- absolute-position seeding ------------------------------------------
    def seed_position(self, angle_deg: float) -> None:
        """Set the step count from a measured angle, instead of homing.

        The IMU is the absolute angle reference now, so there is no
        mechanical origin to drive to any more: whoever measured the
        angle (the daemon, averaging the IMU for a few seconds) just
        tells the stepper what angle it is currently at, and the step
        count is set to match. Rounds to the nearest step, same as
        move_to_angle."""
        self.position_steps = round(angle_deg * self.steps_per_deg)

    # --- context manager ---------------------------------------------------
    def __enter__(self) -> "Stepper":
        self.enable()
        return self

    def __exit__(self, *exc) -> bool:
        self.disable()   # release coils and cut 12V on the way out
        return False