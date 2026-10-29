#!/usr/bin/env python3
"""
scripts/run_scan.py — tek nokta veri alimi (motor henuz yok)

Akis:  config yukle -> DAQCounter ile belirli sure say -> zaman damgali txt.
Ileride bu govde, zenit acisi uzerinde donen tarama dongusune donusecek
(her aci icin: slew -> settle -> ACQUIRE(bu blok) -> write).

    python scripts/run_scan.py                    # config'deki acquisition_s
    python scripts/run_scan.py --duration 60      # sureyi override et (s)
    python scripts/run_scan.py --config config/default.toml
    Ctrl-C  -> temiz durur (footer yazilir, dosya kapanir)

Not: 'software/' kokunden calistir (config yolu goreli).
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

from muon_control.config import load_config
from muon_control.drivers.daq import DAQCounter


def _status(count: int, elapsed: float) -> None:
    """SSH uzerinden canli izleme icin heartbeat satiri."""
    rate = count / elapsed if elapsed > 0 else 0.0
    print(f"[stat] t={elapsed:7.1f}s  N={count:7d}  R≈{rate:7.3f} Hz", flush=True)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Muon teleskop tek-nokta veri alimi")
    ap.add_argument("--config", default="config/default.toml")
    ap.add_argument("--duration", type=float, default=None,
                    help="olcum suresi (s); verilmezse config'deki acquisition_s")
    ap.add_argument("--status-interval", type=float, default=30.0,
                    help="heartbeat araligi (s)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    duration = args.duration if args.duration is not None else cfg["scan"]["acquisition_s"]

    # Run basina benzersiz, siralanabilir dosya adi (UTC damgasi)
    data_dir = Path(cfg["paths"]["data_dir"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = data_dir / f"run_{stamp}.txt"

    # Tek-nokta bench testi: sadece CH_0'i sayar. Iki kanalli olcum
    # daemon/tarama yolunda (muon_control.scan) yapiliyor.
    g = cfg["gpio"]
    pin = (g.get("daq_signals") or [g["daq_signal"]])[0]

    daq = DAQCounter(
        pin=pin,
        out_path=out_path,
        gate_width_s=cfg["daq"]["gate_width_s"],
        pull_up=cfg["daq"]["pull_up"],
    )

    print(f"[run] pin=GPIO{daq.pin}  gate={daq.gate_width_s*1e3:.3f} ms")
    print(f"[run] sure={duration:.0f}s  dosya={out_path}")
    print(f"[run] baslatiliyor... (Ctrl-C ile durdur)", flush=True)

    try:
        with daq:                          # start(): anchor + header + callback
            start = time.monotonic()
            deadline = start + duration
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                time.sleep(min(args.status_interval, remaining))
                _status(daq.count, time.monotonic() - start)
        # __exit__ -> stop(): footer + dosya kapat, livetime_s dolar
    except KeyboardInterrupt:
        print("\n[run] Ctrl-C — olcum erken durduruldu (dosya guvenle kapatildi)")

    # Ozet: Poisson sayim belirsizligi ile rate (manuel kare kurali: sigma_R = sqrt(N)/T)
    n = daq.count
    lt = daq.livetime_s
    if lt and lt > 0:
        rate = n / lt
        rate_err = (n ** 0.5) / lt        # Poisson: sigma_N = sqrt(N)
        print(f"[done] N={n}  livetime={lt:.2f}s")
        print(f"[done] R = ({rate:.4f} ± {rate_err:.4f}) Hz  (Poisson sayim hatasi)")
    else:
        print(f"[done] N={n}  (livetime yok)")
    print(f"[done] {out_path}")


if __name__ == "__main__":
    main()