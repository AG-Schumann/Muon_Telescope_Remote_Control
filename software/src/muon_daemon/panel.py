"""
muon_daemon.panel — what the front-panel display shows

Pure function: takes the daemon's snapshot and returns 4 lines of text.
No hardware, no I/O, so it can be tested end to end with fake data.

Idle:                          During a run:
    Muon Telescope                 <run name, scrolls if too long>
    No active run                  RUN  00:12:34
    +30.0deg 21.5C                 CH0         1234
                                   CH1         5678
"""
from __future__ import annotations

COLS = 16
TITLE = "Muon Telescope"


def _hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _scroll(text: str, tick: int) -> str:
    """16'ya sigmayan metni her tikte 2 karakter kaydir. Sigiyorsa oldugu
    gibi. Basta ve sonda bir an duraksasin diye bosluk eklenir."""
    if len(text) <= COLS:
        return text
    padded = text + "   "
    start = (tick * 2) % len(padded)
    window = (padded + padded)[start:start + COLS]
    return window


def panel_lines(snapshot: dict, tick: int = 0, notice: str = "") -> list[str]:
    """4 satir. 'notice' verilirse (orn. 'Shutting down') 2. satiri ezer."""
    run = snapshot.get("run", {})
    hw = snapshot.get("hardware", {})
    angle = hw.get("angle_deg", 0.0)
    temp = hw.get("temp_c", 0.0)

    if run.get("active"):
        counts = run.get("daq_counts") or [run.get("daq_count", 0)]
        ch0 = counts[0] if len(counts) > 0 else 0
        ch1 = counts[1] if len(counts) > 1 else 0
        lines = [
            _scroll(run.get("name", ""), tick),
            f"RUN  {_hms(run.get('elapsed_s', 0.0))}",
            f"CH0 {ch0:>12,d}",
            f"CH1 {ch1:>12,d}",
        ]
    else:
        phase = snapshot.get("phase", "idle")
        lines = [
            TITLE,
            "No active run" if phase == "idle" else phase.upper(),
            f"{angle:+6.1f}deg {temp:4.1f}C",     # 16 karaktere sigar
            "",
        ]

    if notice:
        lines[1] = notice
    return [l.ljust(COLS)[:COLS] for l in lines]
