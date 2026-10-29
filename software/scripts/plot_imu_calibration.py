"""
scripts/plot_imu_calibration.py — plots an IMU calibration record

Reads the txt written by calibration_record.write_record, draws a
two-panel figure and saves it as .png in the same folder.

    python scripts/plot_imu_calibration.py <record.txt>
    python scripts/plot_imu_calibration.py --latest <folder>   # plot the most recent one

The daemon runs this itself when a calibration finishes (see state.py).
It is a separate script because the first matplotlib import can take
seconds if it has to build its font cache, which would block the
daemon's event loop right when the calibration finishes. Here it runs
in a separate process.

It is also used to re-plot an old record later -- updating the thesis
figure does not require repeating the measurement.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from muon_control.calibration_record import read_record   # noqa: E402
# Thesis figure: there is a single data series, so no legend box -- the
# axis labels already say what is plotted. The y = x and zero lines are
# grey, the data has the only accent colour, so the eye goes to the
# points first.
_DATA = "#1f5fa9"
_GUIDE = "#555555"
_BAND = "#bbbbbb"


def plot(record_path: Path, out_path: Path | None = None) -> Path:
    import matplotlib
    matplotlib.use("Agg")          # ekransiz: Pi'de X yok
    import matplotlib.pyplot as plt

    meta, points = read_record(record_path)
    if not points:
        raise SystemExit(f"{record_path}: icinde nokta yok")

    ref = [p["ref_deg"] for p in points]
    imu = [p["imu_deg"] for p in points]
    imu_err = [p["imu_err_deg"] for p in points]
    res = [p["residual_deg"] for p in points]
    ref_err = meta.get("reference_accuracy_deg", 0.0)

    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(7.0, 7.0), sharex=True,
        gridspec_kw={"height_ratios": [2, 1], "hspace": 0.08},
    )

    # --- top panel: calibrated IMU vs reference ------------------------
    span = [min(ref) - 2, max(ref) + 2]
    ax_top.plot(span, span, "-", color=_GUIDE, lw=1, zorder=1)
    ax_top.annotate("y = x", xy=(span[1], span[1]), xytext=(-6, 6),
                    textcoords="offset points", ha="right",
                    color=_GUIDE, fontsize=9)
    ax_top.errorbar(ref, imu, yerr=imu_err, xerr=ref_err,
                    fmt="o", ms=5, lw=0, elinewidth=1.1, capsize=3,
                    color=_DATA, ecolor=_DATA, zorder=3)
    ax_top.set_ylabel("Calibrated IMU angle  [deg]")
    ax_top.set_xlim(*span)

    # --- bottom panel: residuals -- what the figure is really about -----
    # The reference instrument's own accuracy is drawn as a band: a
    # residual inside this band is a difference the reference itself
    # cannot resolve.
    if ref_err:
        ax_bot.axhspan(-ref_err, ref_err, color=_BAND, alpha=0.35, zorder=0)
    ax_bot.axhline(0.0, color=_GUIDE, lw=1, zorder=1)
    ax_bot.errorbar(ref, res, yerr=imu_err, xerr=ref_err,
                    fmt="o", ms=5, lw=0, elinewidth=1.1, capsize=3,
                    color=_DATA, ecolor=_DATA, zorder=3)
    # Headroom first, THEN the label -- so the band's caption can never
    # land on a data point, whatever the residuals turn out to be.
    ax_bot.margins(y=0.32)
    if ref_err:
        ax_bot.text(0.02, 0.97, f"shaded: reference accuracy  ±{ref_err:g}°",
                    transform=ax_bot.transAxes, va="top", ha="left",
                    fontsize=8, color="#666666")
    ax_bot.set_xlabel("Reference inclinometer angle  [deg]")
    ax_bot.set_ylabel("Residual  [deg]")

    for ax in (ax_top, ax_bot):
        ax.grid(True, color="#dddddd", lw=0.6)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)

    # --- fit parameters, on the figure ---------------------------------
    fit = meta.get("fit", "?")
    text = [
        f"fit: {fit}",
        f"offset = {meta.get('offset_deg', float('nan')):+.3f}°",
    ]
    if "bias_y" in meta:
        text += [
            f"bias  = ({meta['bias_y']:+.4f}, {meta['bias_z']:+.4f}) g",
            f"scale = ({meta['scale_y']:.4f}, {meta['scale_z']:.4f})",
        ]
    text += [
        f"RMS residual = {meta.get('rms_residual_deg', float('nan')):.3f}°",
        f"max residual = {meta.get('max_residual_deg', float('nan')):.3f}°",
        f"n = {len(points)} points",
    ]
    ax_top.text(0.02, 0.98, "\n".join(text), transform=ax_top.transAxes,
                va="top", ha="left", fontsize=8.5, color="#222222",
                family="monospace",
                bbox=dict(boxstyle="round,pad=0.5", fc="white",
                          ec="#cccccc", lw=0.8))

    ax_top.set_title(f"IMU tilt calibration — {meta.get('utc', '')[:19]}",
                     fontsize=11, pad=10)

    out_path = out_path or record_path.with_suffix(".png")
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return out_path


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="IMU kalibrasyon kaydini ciz")
    ap.add_argument("record", nargs="?", help="imu_cal_*.txt yolu")
    ap.add_argument("--latest", metavar="KLASOR",
                    help="bu klasordeki en yeni imu_cal_*.txt'yi ciz")
    args = ap.parse_args(argv)

    if args.latest:
        found = sorted(Path(args.latest).glob("imu_cal_*.txt"))
        if not found:
            raise SystemExit(f"{args.latest}: imu_cal_*.txt bulunamadi")
        record = found[-1]
    elif args.record:
        record = Path(args.record)
    else:
        ap.error("bir kayit dosyasi ver ya da --latest kullan")

    print(f"[plot] cizildi -> {plot(record)}", flush=True)


if __name__ == "__main__":
    main()
