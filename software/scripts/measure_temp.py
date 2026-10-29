#!/usr/bin/env python3
"""
scripts/measure_temp.py — temperature/humidity readout (optional logging)

    python scripts/measure_temp.py                       # print to screen only
    python scripts/measure_temp.py --interval 30         # every 30 s
    python scripts/measure_temp.py --save                # also save to a file
    Ctrl-C -> exit (when saving, a footer is written and the file is closed)

Note: run from the 'software/' root (the config path is relative).
The file format follows the same idea as daq.py: append-only, monotonic
timestamps + a single UTC anchor -> the readings can be matched to the
muon events on a common time axis.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

from muon_control.config import load_config
from muon_control.drivers.temperature import Temperature


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Read temperature/humidity")
    ap.add_argument("--config", default="config/default.toml")
    ap.add_argument("--interval", type=float, default=30.0, help="read interval (s)")
    ap.add_argument("--save", action="store_true", help="save to a file")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    temp = Temperature.from_config(cfg)

    # --- optional logging setup ---
    f = None
    mono_anchor_ns = None
    if args.save:
        data_dir = Path(cfg["paths"]["data_dir"])
        data_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = data_dir / f"temp_{stamp}.txt"

        utc_anchor = time.time()
        mono_anchor_ns = time.monotonic_ns()
        f = open(out_path, "w", buffering=1)     # line-buffered: survives a process crash
        iso = datetime.fromtimestamp(utc_anchor, timezone.utc).isoformat()
        f.write(
            "# temp_run\n"
            f"# utc_anchor_iso = {iso}\n"
            f"# utc_anchor_unix = {utc_anchor!r}\n"
            f"# mono_anchor_ns = {mono_anchor_ns}\n"
            f"# sensor_type = {cfg['temperature']['sensor_type']}\n"
            f"# interval_s = {args.interval!r}\n"
            "# columns = t_rel_s temperature_C humidity_RH\n"
        )
        print(f"[measure_temp] logging to: {out_path}", flush=True)

    print(f"[measure_temp] interval={args.interval:.0f}s  (Ctrl-C to stop)", flush=True)

    try:
        while True:
            t, rh = temp.read()                  # one reading

            if t is None or rh is None:
                print("[measure_temp] READ FAILED (None)", flush=True)
                t_field, rh_field = "nan", "nan"
            else:
                print(f"[measure_temp] temperature={t:.1f}°C  humidity={rh:.1f}%RH", flush=True)
                t_field, rh_field = f"{t:.1f}", f"{rh:.1f}"

            if f is not None:
                t_rel_s = (time.monotonic_ns() - mono_anchor_ns) / 1e9
                f.write(f"{t_rel_s:.3f} {t_field} {rh_field}\n")

            time.sleep(args.interval)            # wait until the next reading
    except KeyboardInterrupt:
        print("\n[measure_temp] stopped.")
    finally:
        if f is not None:
            f.write(f"# stopped_iso = {datetime.now(timezone.utc).isoformat()}\n")
            f.close()


if __name__ == "__main__":
    main()