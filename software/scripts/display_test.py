"""
scripts/display_test.py — try out the front-panel display on its own

    python scripts/display_test.py                 # write 4 lines, exit
    python scripts/display_test.py --contrast 12
    python scripts/display_test.py --live          # live counter demo without the daemon

Note: run from the 'software/' root. If the daemon is running, stop it
first, so that two processes do not write to the same I2C address.
"""

from __future__ import annotations

import argparse
import time

from muon_control.config import load_config
from muon_control.drivers.display import Display


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="EA DOGS164 display test")
    ap.add_argument("--config", default="config/default.toml")
    ap.add_argument("--contrast", type=int, default=None, help="0..15")
    ap.add_argument("--live", action="store_true", help="live counter demo")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.contrast is not None:
        cfg.setdefault("display", {})["contrast"] = args.contrast
    lcd = Display.from_config(cfg)
    lcd.init()
    print(f"[display] 0x{cfg['display']['i2c_addr']:02X} init OK, "
          f"rst=GPIO{cfg['gpio'].get('display_rst', 0) or '-'}")

    lcd.show(["Muon Telescope", "EA DOGS164-A", f"I2C 0x{cfg['display']['i2c_addr']:02X}  OK",
              "0123456789ABCDEF"])
    if not args.live:
        return
    try:
        n = 0
        while True:
            lcd.show(["Muon Telescope", "demo counter", f"n = {n:>11,d}", ""])
            n += 1
            time.sleep(1.0)
    except KeyboardInterrupt:
        lcd.close()


if __name__ == "__main__":
    main()
