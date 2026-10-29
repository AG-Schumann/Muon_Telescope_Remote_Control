#!/usr/bin/env python3
"""
scripts/hall_test.py — Hall sensor / limit switch monitor
Usage:
    python scripts/hall_test.py
    python scripts/hall_test.py --config config/default.toml
    Ctrl-C -> exit
Run from 'software/' root (config path is relative).
"""
from __future__ import annotations
import argparse
from signal import pause

from muon_control.config import load_config
from muon_control.drivers.limit_switches import LimitSwitches


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Hall sensor / limit switch test")
    ap.add_argument("--config", default="config/default.toml")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    sw = LimitSwitches.from_config(cfg)

    print(f"[hall] zero=GPIO{sw.zero.pin.number}  max=GPIO{sw.max.pin.number}")
    print(f"[hall] initial state: zero={sw.at_zero}  max={sw.at_max}")
    print("[hall] monitoring... (Ctrl-C to stop)", flush=True)

    sw.zero.when_pressed  = lambda: print("[hall] ZERO  -> TRIGGERED", flush=True)
    sw.zero.when_released = lambda: print("[hall] ZERO  -> released",  flush=True)
    sw.max.when_pressed   = lambda: print("[hall] MAX   -> TRIGGERED", flush=True)
    sw.max.when_released  = lambda: print("[hall] MAX   -> released",  flush=True)

    try:
        pause()
    except KeyboardInterrupt:
        print("\n[hall] stopped.")


if __name__ == "__main__":
    main()
