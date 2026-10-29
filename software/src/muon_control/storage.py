"""
muon_control.storage — measurement folder + latest.txt handling

Each measurement is collected in its own folder:  data_dir/<name>/
    config_snapshot.toml   settings of that measurement (provenance)
    summary.txt            (scans only) per-point summary
    temp.txt               temperature log
    run_*.txt              event files

data_dir/latest.txt        points to the active/most recent measurement. Used for two things:
    (1) the web server finds the active measurement
    (2) a measure_temp running in parallel can write temp.txt into this folder (--attach)

Design: the entry point decides the name/folder; scan() only writes to the
folder it is given (it knows nothing about storage; loose coupling).
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_measurement(data_dir, name: str, config_path=None, kind: str = "scan") -> Path:
    """data_dir/<name>/ olustur, config'i kopyala, latest.txt yaz.

    Klasor ZATEN VARSA hata verir — mevcut olcum verisini ezmemek icin.
    (Yeniden calistirmak istiyorsan farkli bir --name ver, orn. coinc1_v2.)"""
    data_dir = Path(data_dir)
    folder = data_dir / name
    if folder.exists():
        raise FileExistsError(
            f"'{name}' isimli olcum zaten var: {folder} — "
            f"veri ezilmesin diye farkli bir --name ver."
        )
    folder.mkdir(parents=True)

    if config_path and Path(config_path).is_file():
        shutil.copy(config_path, folder / "config_snapshot.toml")

    write_latest(data_dir, name, status="running", kind=kind, started_iso=_now_iso())
    return folder


def write_latest(data_dir, name: str, *, status: str, kind: str = "scan",
                 started_iso: str | None = None, closed_iso: str | None = None, **extra) -> None:
    """data_dir/latest.txt yaz (# key = value). Web ve --attach bunu okur."""
    data_dir = Path(data_dir)
    lines = [
        "# latest",
        f"# name = {name}",
        f"# folder = {name}",
        f"# kind = {kind}",
        f"# status = {status}",
        f"# updated_iso = {_now_iso()}",
    ]
    if started_iso:
        lines.append(f"# started_iso = {started_iso}")
    if closed_iso:
        lines.append(f"# closed_iso = {closed_iso}")
    for k, v in extra.items():
        lines.append(f"# {k} = {v}")
    (data_dir / "latest.txt").write_text("\n".join(lines) + "\n")


def read_latest(data_dir) -> dict | None:
    """latest.txt'yi oku (web / --attach icin). Yoksa None."""
    p = Path(data_dir) / "latest.txt"
    if not p.is_file():
        return None
    meta = {}
    for line in p.read_text().splitlines():
        if line.startswith("#") and "=" in line:
            k, v = line[1:].split("=", 1)
            meta[k.strip()] = v.strip()
    return meta


def mark_done(data_dir, status: str = "done") -> None:
    """Aktif olcumu status=done (ya da 'error'/'stopped') olarak isaretle.
    started_iso ve digerlerini korur."""
    meta = read_latest(data_dir) or {}
    write_latest(
        data_dir, meta.get("name", "?"),
        status=status, kind=meta.get("kind", "scan"),
        started_iso=meta.get("started_iso"),
        closed_iso=_now_iso(),
    )


def active_folder(data_dir) -> Path | None:
    """Aktif olcum klasoru (latest.txt'den). --attach icin."""
    meta = read_latest(data_dir)
    if not meta or "folder" not in meta:
        return None
    return Path(data_dir) / meta["folder"]