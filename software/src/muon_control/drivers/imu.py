"""
muon_control.drivers.imu — Layer 1: BMI160 IMU (gyro + accel)

Usage:
    imu = IMU.from_config(cfg)
    imu.read()                  # {'gx': °/s ..., 'ax': g ...}
    imu.tilt_deg()              # signed telescope tilt, offset applied
    imu.raw_tilt_deg()          # same, without the offset
    imu.accel_magnitude_g()     # stationary -> approximately 1.0 g

Mounting offset
---------------
The sensor sits in a printed box bolted to the telescope and attached
with two bolts to the frame, so its zero does not line up with the 
mechanical zero: measured on this build, level reads about -2 deg. 
The offset is a property of how the box was mounted, not of the sensor,
so it is measured with the calibration screen and stored in a file
rather than written into the config.

The file lives with the measurement data, not in git: it describes this
one machine, and it becomes wrong the moment the sensor is remounted.
The stored timestamp is there so a stale calibration can be spotted.

Two separate corrections
------------------------
The sensor and the mounting are two different errors and they need two
different fixes:

  ellipse   The accelerometer itself. A perfect one, held still, would
            put (ay, az) somewhere on a circle of radius 1 g whatever
            the angle. A real one has a zero offset (bias) and a gain
            error (scale) on each axis, so the readings land on an
            ELLIPSE whose centre is not at the origin. Undo that and the
            axes become trustworthy.

  offset    How the printed box sits on the frame. Even with a perfect
            sensor, a box bolted on two degrees out reads two degrees
            out. That is a plain rotation and one number fixes it.

You can measure the mounting offset alone and get useful angles -- that
is what a fresh install does. But if the residuals grow with angle, the
sensor's own ellipse is what is left over, and no single offset can
take it out. See fit_ellipse().

Axis convention
---------------
Rotation is about the sensor's X axis, so tilt comes from Y and Z:
positive ay means the telescope is tilted towards the +110 deg end,
negative towards -55 deg. This was established by tilting the telescope
by hand and watching all three axes; ax stayed at 0.00 g throughout.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path


# BMI160 default range: ±2g, ±250°/s.
# If the hardware range changes, update these values.
_ACCEL_LSB_PER_G = 16384.0
_GYRO_LSB_PER_DPS = 131.2

_DEFAULT_CALIBRATION_PATH = "/home/muon_telescope/muon_data/imu_calibration.json"


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Solve a small linear system by Gaussian elimination.

    Written out rather than pulled from numpy: it is four equations, the
    project has no numerical dependencies, and a reader can check this
    against a textbook in a minute.
    """
    n = len(rhs)
    a = [row[:] + [r] for row, r in zip(matrix, rhs)]

    for i in range(n):
        pivot = max(range(i, n), key=lambda r: abs(a[r][i]))
        if abs(a[pivot][i]) < 1e-12:
            raise ValueError("ellipse fit is singular -- the points are degenerate")
        a[i], a[pivot] = a[pivot], a[i]
        for r in range(i + 1, n):
            factor = a[r][i] / a[i][i]
            for c in range(i, n + 1):
                a[r][c] -= factor * a[i][c]

    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (a[i][n] - sum(a[i][j] * x[j] for j in range(i + 1, n))) / a[i][i]
    return x


def fit_ellipse(axes: list[tuple[float, float]]) -> dict:
    """Fit bias and scale for the ay/az axes from stationary readings.

    Held still at any angle, a perfect accelerometer reads a point on the
    circle ay^2 + az^2 = 1 g. A real one adds a bias and a gain error to
    each axis, so the same readings trace an ellipse instead:

        ((ay - bias_y) / scale_y)^2 + ((az - bias_z) / scale_z)^2 = 1

    Four unknowns, so at least four points are needed and more is better.
    They must be spread over a wide range of angles: readings bunched
    into one corner fit an ellipse through that corner and say nothing
    about the rest of it.

    The trick that makes this a linear least-squares problem is writing
    the ellipse as a general conic and fixing the ay^2 coefficient at 1:

        ay^2 + B*az^2 + C*ay + D*az + E = 0

    B, C, D, E come straight out of a normal least-squares solve, and
    completing the square turns them back into bias and scale.
    """
    if len(axes) < 4:
        raise ValueError("an ellipse fit needs at least 4 points")

    design = [[az * az, ay, az, 1.0] for ay, az in axes]
    target = [-(ay * ay) for ay, _ in axes]

    # Normal equations: (M^T M) x = M^T y
    n = 4
    ata = [[sum(row[i] * row[j] for row in design) for j in range(n)] for i in range(n)]
    atb = [sum(row[i] * t for row, t in zip(design, target)) for i in range(n)]
    b_coef, c_coef, d_coef, e_coef = _solve(ata, atb)

    if b_coef <= 0:
        raise ValueError("ellipse fit did not converge to an ellipse")

    bias_y = -c_coef / 2.0
    bias_z = -d_coef / (2.0 * b_coef)
    # Completing the square leaves (ay-by)^2 + B*(az-bz)^2 = k
    k = bias_y ** 2 + b_coef * bias_z ** 2 - e_coef
    if k <= 0:
        raise ValueError("ellipse fit did not converge to an ellipse")

    return {
        "bias_y": bias_y,
        "bias_z": bias_z,
        "scale_y": math.sqrt(k),
        "scale_z": math.sqrt(k / b_coef),
    }


class IMU:
    def __init__(self, driver, calibration_path: str | Path = _DEFAULT_CALIBRATION_PATH):
        self._drv = driver
        self._calibration_path = Path(calibration_path)
        self.offset_deg: float = 0.0
        # Sensor ellipse. The defaults mean "no correction": subtract
        # nothing, divide by one. An uncalibrated sensor then behaves
        # exactly as it did before any of this existed.
        self.bias_y: float = 0.0
        self.bias_z: float = 0.0
        self.scale_y: float = 1.0
        self.scale_z: float = 1.0
        # Whether a stored calibration was actually found, and when it
        # was written. The TUI shows both, so the operator can see at a
        # glance that this machine is running a calibration someone made
        # earlier -- and how old it is.
        self.calibration_loaded: bool = False
        self.calibration_saved_utc: str = ""
        self.load_calibration()

    @classmethod
    def from_config(cls, cfg: dict, driver=None) -> "IMU":
        if driver is None:
            from BMI160_i2c import Driver
            driver = Driver(cfg["imu"]["i2c_addr"])

        path = cfg["imu"].get("calibration_path", _DEFAULT_CALIBRATION_PATH)
        return cls(driver, calibration_path=path)

    def read(self) -> dict:
        """Return gyro values in deg/s and accelerometer values in g."""
        gx, gy, gz, ax, ay, az = self._drv.getMotion6()

        return {
            "gx": gx / _GYRO_LSB_PER_DPS,
            "gy": gy / _GYRO_LSB_PER_DPS,
            "gz": gz / _GYRO_LSB_PER_DPS,
            "ax": ax / _ACCEL_LSB_PER_G,
            "ay": ay / _ACCEL_LSB_PER_G,
            "az": az / _ACCEL_LSB_PER_G,
        }

    # --- tilt --------------------------------------------------------------

    @staticmethod
    def raw_tilt_from_axes(ay: float, az: float) -> float:
        """Signed tilt straight from the axes: no ellipse, no offset.

        The calibration screen needs this. It works out the corrections
        by comparing against an inclinometer, so handing it a reading
        that already has them applied would have it measure the error of
        a number whose error is gone, fit nothing, and save that over a
        good calibration.
        """
        return math.degrees(math.atan2(ay, az))

    def corrected_axes(self, ay: float, az: float) -> tuple[float, float]:
        """Undo the sensor's own bias and gain, putting the reading back
        on a unit circle where atan2 means what it should."""
        return (ay - self.bias_y) / self.scale_y, (az - self.bias_z) / self.scale_z

    def tilt_from_axes(self, ay: float, az: float) -> float:
        """Signed telescope tilt: sensor corrected, then mounting removed.

        The order matters. The ellipse describes the sensor, so it comes
        off first, in the axes. The offset describes how the box is
        bolted on, which is a rotation of the already-correct angle, so
        it comes off afterwards, in degrees.
        """
        cy, cz = self.corrected_axes(ay, az)
        return self.raw_tilt_from_axes(cy, cz) - self.offset_deg

    def raw_tilt_deg(self) -> float:
        reading = self.read()
        return self.raw_tilt_from_axes(reading["ay"], reading["az"])

    def tilt_deg(self) -> float:
        """Return signed telescope tilt in degrees. Level -> about 0 deg."""
        reading = self.read()
        return self.tilt_from_axes(reading["ay"], reading["az"])

    def accel_magnitude_g(self) -> float:
        """Return total acceleration magnitude.

        When the telescope is stationary this should be close to 1 g.
        This build reads about 1.038 g, and the value drifts with angle,
        which means the two axes have slightly different scale factors.
        That leaves a residual the single offset cannot remove -- the
        calibration screen reports it so the operator can decide whether
        it matters.
        """
        reading = self.read()

        return math.sqrt(
            reading["ax"] ** 2
            + reading["ay"] ** 2
            + reading["az"] ** 2
        )

    @property
    def has_ellipse(self) -> bool:
        """True once a real ellipse has been fitted, as opposed to the
        do-nothing defaults."""
        return (self.bias_y, self.bias_z, self.scale_y, self.scale_z) != (0.0, 0.0, 1.0, 1.0)

    # --- calibration persistence -------------------------------------------

    def load_calibration(self) -> None:
        """Read the stored offset. A missing or unreadable file is not an
        error: an uncalibrated telescope still reports angles, just with
        the mounting error included, and the operator sees that on the
        calibration screen."""
        try:
            data = json.loads(self._calibration_path.read_text())
            self.offset_deg = float(data["offset_deg"])
            # .get with the neutral defaults, so a file written before
            # the ellipse existed still loads and still gives the angles
            # it always gave.
            self.bias_y = float(data.get("bias_y", 0.0))
            self.bias_z = float(data.get("bias_z", 0.0))
            self.scale_y = float(data.get("scale_y", 1.0))
            self.scale_z = float(data.get("scale_z", 1.0))
            self.calibration_saved_utc = str(data.get("saved_utc", ""))
            self.calibration_loaded = True
        except (FileNotFoundError, KeyError, ValueError, OSError):
            self.offset_deg = 0.0
            self.bias_y = self.bias_z = 0.0
            self.scale_y = self.scale_z = 1.0
            self.calibration_saved_utc = ""
            self.calibration_loaded = False

    def save_calibration(self) -> None:
        """Write the offset out so it survives a daemon restart."""
        saved_utc = datetime.now(timezone.utc).isoformat()
        self._calibration_path.parent.mkdir(parents=True, exist_ok=True)
        self._calibration_path.write_text(
            json.dumps(
                {
                    "offset_deg": self.offset_deg,
                    "bias_y": self.bias_y,
                    "bias_z": self.bias_z,
                    "scale_y": self.scale_y,
                    "scale_z": self.scale_z,
                    "saved_utc": saved_utc,
                },
                indent=2,
            )
        )
        self.calibration_saved_utc = saved_utc
        self.calibration_loaded = True