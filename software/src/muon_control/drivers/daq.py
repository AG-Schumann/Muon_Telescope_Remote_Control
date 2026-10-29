"""
muon_control.daq — Layer 1: DAQ event counter

Model 222 gate -> BC547 (inverting) -> GPIO falling-edge counter.
Event times are taken from the monotonic clock (unaffected by NTP clock
steps); absolute UTC is tied in with a SINGLE anchor at the start of the
run. Output: append-only txt (one file per run).

Usage:
    daq = DAQCounter(pin=23, out_path="run_0001.txt", gate_width_s=1.003e-3)
    with daq:                 # start(): take the anchor, write the header, attach the callback
        time.sleep(1800)      # measurement time (just waits; other work can run meanwhile)
    # __exit__ -> stop(): detach the callback, write livetime + total, close the file
    print(daq.count)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from gpiozero import Button


class DAQCounter:
    def __init__(self, pin: int, out_path, gate_width_s: float, pull_up: bool = True):
        self.pin = pin
        self.out_path = Path(out_path)
        self.gate_width_s = gate_width_s
        self.count = 0
        self.livetime_s = None        # stop() sonrasi dolar

        # Inverting topoloji: event -> LOW -> when_pressed (dusen kenar).
        # bounce_time=None: DAQ hattinda debounce YOK (yakin event'leri birlestirme).
        self._button = Button(pin, pull_up=pull_up, bounce_time=None)

        self._file = None
        self._utc_anchor = None       # time.time() — run basinda TEK kez
        self._mono_anchor_ns = None   # time.monotonic_ns() — ayni an

    # --- event callback: MUMKUN OLDUGUNCA HAFIF ---------------------------
    def _on_event(self):
        t_ns = time.monotonic_ns()            # ILK IS: zaman damgasi (I/O gecikmesi bulasmasin)
        self.count += 1
        # t_rel_ns: anchor'a gore tam-sayi nanosaniye (kesin, float artigi yok)
        self._file.write(f"{self.count} {t_ns - self._mono_anchor_ns}\n")

    # --- lifecycle ----------------------------------------------------------

        self._button.when_pressed = self._on_event   # start counting    def start(self):
        self.out_path.parent.mkdir(parents=True, exist_ok=True)

        # Read the two clocks as close together as possible (consistency of the anchor pair).
        self._utc_anchor = time.time()
        self._mono_anchor_ns = time.monotonic_ns()

        # buffering=1: satir-tamponlu -> her event newline'da OS'e yazilir.
        # Process bir exception ile olurse o ana kadarki tum event'ler diskte kalir.
        self._file = open(self.out_path, "w", buffering=1)
        # already with the OS (it survives a process crash, not a power cut).

        iso = datetime.fromtimestamp(self._utc_anchor, timezone.utc).isoformat()
        self._file.write(
            "# daq_run\n"
            f"# utc_anchor_iso = {iso}\n"
            f"# utc_anchor_unix = {self._utc_anchor!r}\n"
            f"# mono_anchor_ns = {self._mono_anchor_ns}\n"
            f"# gpio_pin = {self.pin}\n"
            f"# gate_width_s = {self.gate_width_s!r}\n"
            "# columns = index t_rel_ns\n"
        )
        self._button.when_pressed = self._on_event   # sayimi baslat

    def stop(self):
        self._button.when_pressed = None             # sayimi durdur
        t_stop_ns = time.monotonic_ns()
        livetime_s = (t_stop_ns - self._mono_anchor_ns) / 1e9
        self.livetime_s = livetime_s

        # Kapanis metadata'si (analiz bunlari header'dan okur)
        self._file.write(f"# stop_t_rel_ns = {t_stop_ns - self._mono_anchor_ns}\n")
        self._file.write(f"# livetime_s = {livetime_s!r}\n")
        self._file.write(f"# total_counts = {self.count}\n")
        self._file.close()
        return self.count, livetime_s

    # with-blogu: exception olsa bile stop() cagrilir -> dosya guvenle kapanir
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        self._button.close()   # pini serbest birak (tarama: aci basina yeni DAQCounter)
        return False           