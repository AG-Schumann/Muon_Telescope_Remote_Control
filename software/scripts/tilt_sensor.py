#!/usr/bin/env python3
"""
scripts/tilt_sensor.py — BMI160 IMU okuma testi

Fiziksel eksenleri, tilt acisini ve ivme buyuklugunu surekli basar.

    python scripts/tilt_sensor.py
    python scripts/tilt_sensor.py --interval 0.5
    Ctrl-C -> cikar

Not: 'software/' kokunden calistir (config yolu goreli).
"""
from __future__ import annotations

import argparse
import time

from muon_control.config import load_config
from muon_control.drivers.imu import IMU


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="BMI160 IMU okuma testi")
    ap.add_argument("--config", default="config/default.toml")
    ap.add_argument("--interval", type=float, default=0.2, help="okuma araligi (s)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    addr = cfg["imu"]["i2c_addr"]

    # GERI BILDIRIM: bloke edebilecek Driver init'inden ONCE yaz (flush!).
    # Boylece takilirsa nerede takildigini gorursun.
    print(f"[tilt_sensor] IMU'ya baglaniliyor (0x{addr:02X})...", flush=True)
    try:
        imu = IMU.from_config(cfg)
    except Exception as e:
        print(f"[tilt_sensor] BAGLANTI HATASI: {e}", flush=True)
        print("[tilt_sensor] kontrol: i2cdetect -y 1  (adres gorunuyor mu?),"
              " config'deki i2c_addr, kablolama (SDA=BCM2/SCL=BCM3)", flush=True)
        return

    print("[tilt_sensor] baglandi. Ctrl-C ile durdur.", flush=True)
    print("[tilt_sensor] SABITKEN |a| ~1.000 g bekle (olcek/sira dogrulamasi)\n", flush=True)

    try:
        while True:
            r = imu.read()                       # tek okuma -> tutarli snapshot
            tilt = imu.tilt_deg()
            mag = imu.accel_magnitude_g()
            print(
                f"a=({r['ax']:+.2f},{r['ay']:+.2f},{r['az']:+.2f})g  "
                f"w=({r['gx']:+6.1f},{r['gy']:+6.1f},{r['gz']:+6.1f})°/s  "
                f"tilt={tilt:6.2f}°  |a|={mag:.3f}g",
                flush=True,
            )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[tilt_sensor] durduruldu.")


if __name__ == "__main__":
    main()