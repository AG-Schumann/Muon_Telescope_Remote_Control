"""
muon_control.calibration_record — permanent record of the IMU calibration

calibration.json holds one thing: the calibration currently in use.
This module records HOW that calibration was obtained -- which points
were taken, what the fit gave, and how large the residual was at each
point. In other words, the json is the "result" and the file written
here is the "lab notebook".

Each calibration is written to its own time-stamped file and is never
overwritten. Whether the mount shifts over time can only be seen if the
old records are kept.

Plain text, no dependencies: the daemon writes it, the plotting script
reads it.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

# Dosya adindaki damga: siralanabilir olsun diye UTC, dosya adinda
# ':' kullanilamadigi icin '-' ile.
_STAMP = "%Y-%m-%dT%H-%M-%SZ"


def _rms(values: list[float]) -> float:
    if not values:
        return 0.0
    return math.sqrt(sum(v * v for v in values) / len(values))


def write_record(folder, points: list[dict], offset_deg: float,
                 ellipse: dict | None, reference_accuracy_deg: float) -> Path:
    """Write one calibration as a human-readable txt file and return its path.

    points: each one {"ref", "imu", "sigma", "ay", "az", "n"}
            ref   -- angle of the reference instrument (Bosch)
            imu   -- IMU angle AFTER the calibration has been applied
            sigma -- standard error of the mean IMU angle at that point
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    path = folder / f"imu_cal_{now.strftime(_STAMP)}.txt"

    residuals = [p["imu"] - p["ref"] for p in points]

    lines = [
        "# imu_calibration",
        f"# utc = {now.isoformat()}",
        f"# n_points = {len(points)}",
        f"# fit = {'ellipse+offset' if ellipse else 'offset_only'}",
        f"# offset_deg = {offset_deg!r}",
    ]
    if ellipse:
        for key in ("bias_y", "bias_z", "scale_y", "scale_z"):
            lines.append(f"# {key} = {ellipse[key]!r}")
    lines += [
        f"# rms_residual_deg = {_rms(residuals)!r}",
        f"# max_residual_deg = {max((abs(r) for r in residuals), default=0.0)!r}",
        f"# reference_accuracy_deg = {reference_accuracy_deg!r}",
        "# columns = ref_deg ref_err_deg imu_deg imu_err_deg residual_deg ay az n_samples",
    ]

    for p, residual in zip(points, residuals):
        lines.append(
            f"{p['ref']:.3f} {reference_accuracy_deg:.3f} "
            f"{p['imu']:.4f} {p['sigma']:.4f} {residual:+.4f} "
            f"{p['ay']:+.5f} {p['az']:+.5f} {p['n']}"
        )

    path.write_text("\n".join(lines) + "\n")
    return path


def read_record(path) -> tuple[dict, list[dict]]:
    """Read back a file written by write_record: (header parameters, points).

    Every header value is tried as a float; if that fails it is kept as
    a string (e.g. fit = 'ellipse+offset').
    """
    meta: dict = {}
    points: list[dict] = []
    columns: list[str] = []

    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            body = line[1:].strip()
            if "=" not in body:
                continue
            key, _, value = body.partition("=")
            key, value = key.strip(), value.strip()
            if key == "columns":
                columns = value.split()
                continue
            try:
                meta[key] = float(value)
            except ValueError:
                meta[key] = value
            continue

        fields = line.split()
        if not columns or len(fields) != len(columns):
            continue
        points.append({
            name: (int(f) if name == "n_samples" else float(f))
            for name, f in zip(columns, fields)
        })

    return meta, points
