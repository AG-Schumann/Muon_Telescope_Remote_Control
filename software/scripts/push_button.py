#!/usr/bin/env python3

"""
scripts/push_button.py — SSingle data receiving GPIO button

Usage:
    python scripts/push_button.py
    python scripts/push_button.py --config config/default.toml
    Ctrl-C  -> exits (nothing to save, just test GPIO callback)

Note: run from 'software/' root (config path is relative).
"""


from __future__ import annotations

import argparse
from pathlib import Path

from muon_control.config import load_config
from muon_control.drivers.limit_switches import LimitSwitches


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Muon teleskop push button test")
    ap.add_argument("--config", default="config/default.toml")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    switches = LimitSwitches.from_config(cfg)

    print(f"[push_button] zero_pin=GPIO{switches.zero.pin.number}  max_pin=GPIO{switches.max.pin.number}")
    print(f"[push_button] baslatiliyor... (Ctrl-C ile durdur)", flush=True)

    try:
        while True:
            if switches.at_zero:
                print("[push_button] limit switch: ZERO (homing) triggered")
            if switches.at_max:
                print("[push_button] limit switch: MAX (safety) triggered")
            if not switches.any_triggered():
                print("[push_button] no limit switch triggered")
            switches.zero.wait_for_release()
            switches.max.wait_for_release()
    except KeyboardInterrupt:
        print("[push_button] durduruldu, cikiliyor...")

if __name__ == "__main__":
    main()