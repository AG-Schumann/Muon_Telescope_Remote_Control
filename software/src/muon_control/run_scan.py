#!/usr/bin/env python3
"""
scripts/run_scan.py — hemisphere tarama entry point (Layer 2)

config'den suruculeri kurar, muon_control.scan.scan()'i cagirir.

Opsiyonel sensorler (IMU/temp/limit switch'ler): kurmayi DENER; basarisizsa
UYARIR ve None ile devam eder (tarama durmaz). Homing YOK -- mutlak aci
referansi IMU: scan() basta IMU'yu birkac saniye ortalayip pozisyonu ordan
ayarlar (bkz. scan.py seed_from_imu). Limit switch'ler artik sadece pasif,
salt-okunur bir capraz kontrol icin kuruluyor; hicbir hareketi kontrol
etmiyorlar.

    python scripts/run_scan.py                 # tarama; sensorler otomatik
    python scripts/run_scan.py --no-imu        # IMU'yu hic deneme
    python scripts/run_scan.py --no-temp

Not: 'software/' kokunden calistir.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from muon_control.config import load_config
from muon_control.drivers.daq import DAQCounter
from muon_control.drivers.stepper import Stepper
from muon_control.scan import scan
from muon_control import storage


def _try_imu(cfg):
    """IMU'yu kur ve bir okumayla DOGRULA (I2C okumasi basarisizsa sensor yok
    demektir — DHT'nin aksine transient degil). Basarisizsa uyar + None."""
    try:
        from muon_control.drivers.imu import IMU
        imu = IMU.from_config(cfg)
        imu.accel_magnitude_g()                       # dogrulama okumasi
        print("[run_scan] IMU hazir", flush=True)
        return imu
    except Exception as e:
        print(f"[run_scan] UYARI: IMU kurulamadi ({e}) -> IMU'suz devam", flush=True)
        return None


def _try_limits(cfg):
    """Hall sensor'leri kur. Sadece pasif bir capraz kontrol icin -- hicbir
    harekete karismiyorlar, o yuzden takili degillerse tarama yine de
    devam eder."""
    try:
        from muon_control.drivers.limit_switches import LimitSwitches
        limits = LimitSwitches.from_config(cfg)
        print("[run_scan] hall sensorleri hazir (salt-okunur capraz kontrol)", flush=True)
        return limits
    except Exception as e:
        print(f"[run_scan] UYARI: hall sensorleri kurulamadi ({e}) -> onlarsiz devam", flush=True)
        return None


def _try_temp(cfg):
    """Sicaklik sensorunu kur. DOGRULAMA OKUMASI YOK — DHT'de tek basarisiz okuma
    normaldir, calisan sensoru yanlislikla devre disi birakmayalim. Sadece
    kurulumun kendisi patlarsa None."""
    try:
        from muon_control.drivers.temperature import Temperature
        temp = Temperature.from_config(cfg)
        print("[run_scan] sicaklik sensoru hazir", flush=True)
        return temp
    except Exception as e:
        print(f"[run_scan] UYARI: sicaklik sensoru kurulamadi ({e}) -> temp'siz devam",
              flush=True)
        return None


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Muon teleskop hemisphere tarama")
    ap.add_argument("--config", default="config/default.toml")
    ap.add_argument("--name", required=True,
                    help="olcum adi (data_dir/<name>/ klasoru; ayni isim varsa hata)")
    ap.add_argument("--no-imu", action="store_true", help="IMU'yu hic kurma")
    ap.add_argument("--no-temp", action="store_true", help="sicaklik sensorunu hic kurma")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    data_dir = cfg["paths"]["data_dir"]

    # Olcum klasorunu olustur (ayni isim varsa veri ezilmesin diye hata verir)
    try:
        folder = storage.create_measurement(data_dir, args.name,
                                             config_path=args.config, kind="scan")
    except FileExistsError as e:
        print(f"[run_scan] HATA: {e}", flush=True)
        return
    print(f"[run_scan] olcum klasoru: {folder}", flush=True)

    # --- Zorunlu cekirdek: motor + DAQ ---
    # Limit switch'ler artik sadece pasif bir capraz kontrol (bkz. yukarida).
    limits = _try_limits(cfg)
    stepper = Stepper.from_config(cfg, limits=limits)

    # DAQ fabrikasi: aci basina YENI DAQCounter (ayri event dosyasi).
    g, d = cfg["gpio"], cfg["daq"]
    def make_daq(out_dir, stem):
        """Kanal basina bir DAQCounter, kanal basina bir dosya."""
        pins = g.get("daq_signals") or [g["daq_signal"]]
        return [
            DAQCounter(pin=pin, out_path=Path(out_dir) / f"{stem}_ch{ch}.txt",
                       gate_width_s=d["gate_width_s"], pull_up=d["pull_up"])
            for ch, pin in enumerate(pins)
        ]

    # --- Opsiyonel sensorler: dene, basarisizsa uyar + None ---
    imu = None if args.no_imu else _try_imu(cfg)
    temp = None if args.no_temp else _try_temp(cfg)

    # --- Tarama ---
    try:
        scan(cfg, stepper, make_daq, out_dir=folder, imu=imu, temp=temp)
        storage.mark_done(data_dir, status="done")
    except KeyboardInterrupt:
        print("\n[run_scan] Ctrl-C — tarama kesildi", flush=True)
        storage.mark_done(data_dir, status="stopped")
    except Exception as e:
        print(f"\n[run_scan] HATA: {e}", flush=True)
        storage.mark_done(data_dir, status="error")
        raise


if __name__ == "__main__":
    main()