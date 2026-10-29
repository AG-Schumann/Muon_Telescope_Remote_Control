"""
muon_control.scan — Layer 2: tarama orkestrasyonu

Layer 1 surucileri bir olcum dongusunde koordine eder. YENI donanim bilgisi
EKLEMEZ — sadece surucileri dogru sirayla ve dogru kosullarla cagirir.

Her aci noktasi icin:  SLEW -> SETTLE -> ACQUIRE -> WRITE
    stepper.move_to_angle -> settle -> DAQCounter(sure) -> ozet satiri

Cikti:
    - aci basina EVENT dosyasi (DAQCounter'in ham timestamp'leri)
    - tek SCAN OZET dosyasi (nokta basina bir satir: theta, count, rate, env, flag)

Suruculer disaridan verilir (enjeksiyon). imu/temp OPSIYONEL (None-guvenli):
varsa oku ve kaydet, yoksa/None ise quality flag'e isaretle.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

_NAN = float("nan")

# IMU averaging window used to seed the absolute position at scan start
# (replaces homing -- see seed_from_imu below).
SEED_SECONDS = 5.0


def seed_from_imu(stepper, imu, duration_s: float = SEED_SECONDS) -> None:
    """IMU'yu birkac saniye ortala ve stepper'in mutlak pozisyonunu bu
    olculen aciya gore ayarla. Eskiden burada limit switch'e gidip sifiri
    bulan bir homing adimi vardi; artik mutlak aci referansi IMU oldugu
    icin buna gerek yok."""
    samples = []
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        samples.append(imu.tilt_deg())
        time.sleep(0.05)
    avg_tilt = sum(samples) / len(samples)
    stepper.seed_position(avg_tilt)


def theta_points(scan_cfg: dict) -> list[float]:
    """Taranacak zenit acilari. Float birikimini onlemek icin round ile uretilir."""
    start = scan_cfg["theta_start_deg"]
    stop = scan_cfg["theta_stop_deg"]
    step = scan_cfg["theta_step_deg"]
    n = round((stop - start) / step)
    return [round(start + i * step, 6) for i in range(n + 1)]


def settle(settle_s: float, imu=None) -> None:
    """Sistem yerlessin diye bekle.

    SIMDILIK: sabit bekleme (time.sleep). imu parametresi ileri-uyumluluk icin
    burada; IMU calisinca bu fonksiyon tilt-std penceresi tabanli GERCEK settle'a
    yukseltilecek (esik altina inip orada kalana kadar bekle)."""
    time.sleep(settle_s)


def read_environment(temp=None, imu=None) -> dict:
    """Opsiyonel cevresel okuma. None-guvenli: sensor yoksa ya da okuma
    basarisizsa ilgili alan nan olur ve flag'e eklenir."""
    env = {"temp_C": _NAN, "humidity_RH": _NAN, "tilt_deg": _NAN, "flags": []}

    if temp is not None:
        t, rh = temp.read()
        if t is None:
            env["flags"].append("temp_fail")
        else:
            env["temp_C"], env["humidity_RH"] = t, rh

    if imu is not None:
        try:
            env["tilt_deg"] = imu.tilt_deg()
        except Exception:
            env["flags"].append("imu_fail")

    return env


def acquire(daqs, duration_s: float) -> list[tuple[int, float]]:
    """Her kanalin sayacini duration_s boyunca calistir.

    Kanal basina (count, livetime_s) doner. Hepsi ayni anda baslar ve
    biter; livetime yine de kanal basina tutulur, cunku hiz o kanalin
    gercekten saydigi sureye bolunmeli."""
    for daq in daqs:
        daq.start()
    try:
        time.sleep(duration_s)
    finally:
        results = []
        for daq in daqs:
            results.append(daq.stop())
            daq._button.close()
    return results


def scan(cfg: dict, stepper, make_daq, out_dir, imu=None, temp=None):
    """Tam hemisphere tarama. Ciktilari OUT_DIR (olcum klasoru) icine yazar.

    stepper : Stepper ornegi (tum tarama boyunca kullanilir)
    make_daq: (out_dir, stem) -> DAQCounter listesi (kanal basina bir dosya)
    out_dir : olcum klasoru (entry point storage.create_measurement ile olusturur)
    imu,temp: OPSIYONEL sensorler (None-guvenli). imu verilirse basta
              seed_from_imu ile pozisyon ayarlanir (bkz. yukarida).
    """
    scfg = cfg["scan"]
    gcfg = cfg["gpio"]
    n_channels = len(gcfg.get("daq_signals") or [gcfg["daq_signal"]])
    angles = theta_points(scfg)
    duration = scfg["acquisition_s"]
    settle_s = scfg.get("settle_s", 5.0)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "summary.txt"

    print(f"[scan] {len(angles)} nokta: {angles}", flush=True)
    print(f"[scan] nokta basina {duration:.0f}s, settle {settle_s:.0f}s", flush=True)
    print(f"[scan] klasor: {out_dir}", flush=True)

    # buffering=1: her nokta bitince diske yazilir -> cokme olsa bile
    # o ana kadarki noktalar kayitli (append-only felsefe)
    with open(summary_path, "w", buffering=1) as sf:
        sf.write("# scan_run\n")
        sf.write(f"# utc_start = {datetime.now(timezone.utc).isoformat()}\n")
        sf.write(f"# acquisition_s = {duration!r}\n")
        sf.write(f"# settle_s = {settle_s!r}\n")
        # Basligi kanal sayisindan uretiyoruz ki bir kanal eklenince ya da
        # cikarilinca satirlarla uyumsuz kalmasin.
        columns = ["theta_deg", "angle_actual_deg", "livetime_s"]
        for ch in range(n_channels):
            columns += [f"ch{ch}_counts", f"ch{ch}_rate_Hz"]
        columns += ["temp_C", "humidity_RH", "tilt_deg", "event_file_stem", "flags"]
        sf.write("# columns = " + " ".join(columns) + "\n")

        if imu is not None:
            print(f"[scan] IMU'dan pozisyon ayarlaniyor ({SEED_SECONDS:.0f}s ortalama)...", flush=True)
            seed_from_imu(stepper, imu)
        else:
            print("[scan] UYARI: IMU yok, mevcut pozisyon (0 varsayilan) kullanilacak.", flush=True)

        for theta in angles:
            stepper.move_to_angle(theta)                 # SLEW (surucu cagrisi)
            settle(settle_s, imu)                        # SETTLE
            env = read_environment(temp, imu)            # cevresel snapshot

            stem = f"run_th{theta:+06.1f}"
            results = acquire(make_daq(out_dir, stem), duration)   # ACQUIRE
            lt = max((t for _, t in results), default=0.0)

            flags = ";".join(env["flags"]) or "ok"
            fields = [f"{theta:.3f}", f"{stepper.angle_deg:.3f}", f"{lt:.3f}"]
            for counts, livetime_s in results:           # kanal basina sayim + hiz
                rate = counts / livetime_s if livetime_s else _NAN
                fields += [str(counts), f"{rate:.4f}"]
            fields += [f"{env['temp_C']:.1f}", f"{env['humidity_RH']:.1f}",
                       f"{env['tilt_deg']:.2f}", stem, flags]
            sf.write(" ".join(fields) + "\n")           # WRITE (ozet satiri)

            per_ch = "  ".join(f"ch{ch}={c}" for ch, (c, _) in enumerate(results))
            print(f"[scan] θ={theta:+6.1f}° (gercek {stepper.angle_deg:+.2f}°)  "
                  f"{per_ch}  T={env['temp_C']:.1f}°C  [{flags}]",
                  flush=True)

    print(f"[scan] tamamlandi -> {summary_path}", flush=True)
    return summary_path