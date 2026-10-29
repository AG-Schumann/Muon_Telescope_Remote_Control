#!/usr/bin/env python3
"""
web/monitor.py — SALT-OKUNUR izleme sunucusu (Flask)

Olcum sistemine DOKUNMAZ: sadece config'deki data_dir'deki dosyalari okur
(scan_*.txt, temp_*.txt, run_*.txt) ve durumu gosterir. Hicbir kontrol yok
(baslat/durdur SSH'ta). Web tarafinda ne olursa olsun olcume sifir etki.

    python web/monitor.py                        # config/default.toml -> data_dir
    python web/monitor.py --port 8000
    python web/monitor.py --data-dir /path/to/data

Baska bilgisayardan:  http://<pi-ip>:8000
Not: 'software/' kokunden calistir (config yolu goreli).
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template

from muon_control.config import load_config

app = Flask(__name__)
DATA_DIR: Path = Path(".")     # main() icinde ayarlanir


# ============================================================================
#  Dosya okuma yardimcilari — GUVENLIK: yalnizca DATA_DIR icinde bilinen
#  desenler okunur; istemciden dosya adi ALINMAZ (yol gecisi imkansiz).
# ============================================================================

def _latest(pattern: str) -> Path | None:
    """Checks DATA_DIR for files matching pattern, returns the most recent one (or None)."""
    files = sorted(DATA_DIR.glob(pattern), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _tail_lines(path: Path, n: int) -> list[str]:
    """Reads the last n lines of a file efficiently."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        block = 8192
        data = b""
        while size > 0 and data.count(b"\n") <= n:
            step = min(block, size)
            size -= step
            f.seek(size)
            data = f.read(step) + data
        lines = data.split(b"\n")
        return [l.decode("utf-8", "replace") for l in lines if l.strip()][-n:]


def _read_header(path: Path, max_lines: int = 20) -> dict:
    """Reads '# key = value' header lines from a file and returns them as a dictionary."""
    meta = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for _ in range(max_lines):
            line = f.readline()
            if not line:
                break
            if line.startswith("#") and "=" in line:
                k, v = line[1:].split("=", 1)
                meta[k.strip()] = v.strip()
            elif not line.startswith("#"):
                break
    return meta


def _parse_scan(path: Path) -> tuple[dict, list[dict]]:
    """Scan summary file -> (header_meta, point list)."""
    meta, points, cols = {}, [], None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("# columns"):
            cols = line.split("=", 1)[1].split()
        elif line.startswith("#") and "=" in line:
            k, v = line[1:].split("=", 1)
            meta[k.strip()] = v.strip()
        elif line.strip() and cols:
            parts = line.split()
            if len(parts) == len(cols):
                points.append(dict(zip(cols, parts)))
    return meta, points


def _parse_temp_tail(path: Path, n: int = 300) -> list[dict]:
    """Reads the last n lines of a temperature log file and returns them as a list of dictionaries."""
    series = []
    for line in _tail_lines(path, n + 5):
        if line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 3:
            try:
                series.append({
                    "t_rel_s": float(parts[0]),
                    "temp_C": None if parts[1] == "nan" else float(parts[1]),
                    "humidity_RH": None if parts[2] == "nan" else float(parts[2]),
                })
            except ValueError:
                pass
    return series[-n:]


def _daq_count(path: Path) -> int | None:
    """Event dosyasinin son veri satirindaki olay indeksi = o ana kadarki
    sayim. Satir formati 'index t_rel_ns' (bkz. DAQCounter), o yuzden ilk
    alani okumak yeterli. Bulunamazsa None."""
    for line in reversed(_tail_lines(path, 5)):
        if not line.startswith("#"):
            try:
                return int(line.split()[0])
            except (ValueError, IndexError):
                pass
    return None


# ============================================================================
#  Rotalar
# ============================================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    now = time.time()
    out = {
        "now_iso": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(DATA_DIR),
    }

    scan_f = _latest("scan_*.txt")
    if scan_f:
        meta, points = _parse_scan(scan_f)
        out["scan"] = {
            "file": scan_f.name, "meta": meta, "points": points,
            "age_s": round(now - scan_f.stat().st_mtime, 1),
        }

    temp_f = _latest("temp_*.txt")
    if temp_f:
        hdr = _read_header(temp_f)
        series = _parse_temp_tail(temp_f, 300)
        out["temp"] = {
            "file": temp_f.name,
            "latest": series[-1] if series else None,
            "series": series,
            "anchor_unix": hdr.get("utc_anchor_unix"),
            "age_s": round(now - temp_f.stat().st_mtime, 1),
        }

    run_f = _latest("run_*.txt")
    if run_f:
        out["daq"] = {
            "file": run_f.name,
            "count": _daq_count(run_f),
            "age_s": round(now - run_f.stat().st_mtime, 1),
        }

    ages = [out[k]["age_s"] for k in ("scan", "temp", "daq") if k in out]
    out["last_activity_s"] = min(ages) if ages else None
    return jsonify(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Muon teleskop izleme sunucusu (salt-okunur)")
    ap.add_argument("--config", default="config/default.toml")
    ap.add_argument("--host", default="0.0.0.0", help="0.0.0.0 = lab agindan erisilebilir")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--data-dir", default=None, help="config yerine dogrudan data dizini")
    args = ap.parse_args()

    global DATA_DIR
    if args.data_dir:
        DATA_DIR = Path(args.data_dir)
    else:
        DATA_DIR = Path(load_config(args.config)["paths"]["data_dir"])

    print(f"[monitor] data_dir = {DATA_DIR}")
    print(f"[monitor] http://<pi-ip>:{args.port}  (salt-okunur)")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
