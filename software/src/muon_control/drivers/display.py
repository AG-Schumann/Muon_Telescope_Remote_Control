"""
muon_control.display — Layer 1: front-panel display (EA DOGS164W-A, SSD1803A, I2C)

4 lines x 16 characters. Only writes; the daemon decides WHAT is shown
(see muon_daemon/panel.py). The init sequence is the one already tested
on the hardware, unchanged -- only error tolerance and small helpers
were added.

Wiring (on the board):
    SDA/SCL -> GPIO2/3 (I2C bus 1), SA0 -> GND (address 0x3C)
    RST     -> [gpio] display_rst in the config (active LOW); 0 means no
               pin, and a reset is just re-sending the init sequence
    backlight is hard-wired, always on

An I2C error (OSError) never takes the DAQ down: if a write fails, one
re-init is attempted; if that fails too, the display is treated as
disconnected and is retried quietly.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger("muon_control.display")

_CMD, _DATA = 0x00, 0x40                  # control bytes: command / data
_LINE_ADDR = (0x00, 0x20, 0x40, 0x60)      # DDRAM start address of each line (bottom view)
COLS, ROWS = 16, 4


class Display:
    def __init__(self, bus: int = 1, addr: int = 0x3C, rst_pin: int = 0,
                 contrast: int = 0x0A) -> None:
        from smbus2 import SMBus
        self._bus = SMBus(bus)
        self._addr = addr
        self._contrast = contrast & 0x0F
        self._rst = None
        if rst_pin:
            from gpiozero import OutputDevice
            self._rst = OutputDevice(rst_pin, initial_value=True)   # aktif LOW, bosta HIGH
        self.ok = False            # son yazma basarili miydi
        self._shown = [""] * ROWS  # ekranda su an ne var -- degismeyeni yazmayalim

    @classmethod
    def from_config(cls, cfg: dict) -> "Display":
        d = cfg.get("display", {})
        return cls(bus=d.get("i2c_bus", 1), addr=d.get("i2c_addr", 0x3C),
                   rst_pin=cfg["gpio"].get("display_rst", 0),
                   contrast=d.get("contrast", 0x0A))

    # --- alcak seviye -----------------------------------------------------

    def _cmd(self, c: int, delay: float = 0.0) -> None:
        self._bus.write_i2c_block_data(self._addr, _CMD, [c])
        time.sleep(delay + 0.0005)

    def _reset(self) -> None:
        if self._rst is None:
            return                      # pin yok: init dizisi tek basina yeter
        self._rst.off(); time.sleep(0.005)
        self._rst.on();  time.sleep(0.05)

    def init(self) -> None:
        """Datasheet init dizisi (senin betiginle ayni)."""
        self._reset()
        for c in (0x3A, 0x09, 0x06, 0x1E, 0x39):   # RE=1, 4 satir, bottom view, bias, RE=0/IS=1
            self._cmd(c)
        self._cmd(0x1B)                  # dahili OSC, bias 1/6
        self._cmd(0x6C, 0.2)             # follower, ~200 ms bekle
        self._cmd(0x56)                  # booster on, C5/C4
        self._cmd(0x70 | self._contrast) # kontrast C3..C0
        self._cmd(0x38)                  # RE=0, IS=0
        self._cmd(0x0C)                  # ekran acik, imlec kapali
        self.clear()
        self.ok = True

    def clear(self) -> None:
        self._cmd(0x01, 0.002)
        self._shown = [""] * ROWS

    def set_contrast(self, contrast: int) -> None:
        self._contrast = contrast & 0x0F
        for c in (0x39, 0x56, 0x70 | self._contrast, 0x38):
            self._cmd(c)

    def _write_line_raw(self, line: int, text: str) -> None:
        self._cmd(0x80 | _LINE_ADDR[line])
        data = [ord(ch) if ord(ch) < 256 else ord("?") for ch in text]
        self._bus.write_i2c_block_data(self._addr, _DATA, data)

    # --- high level -------------------------------------------------------

    def write_line(self, line: int, text: str) -> None:
        """Write one line, padded or truncated to 16 characters. Text that
        is already on the display is not rewritten, so there is I2C
        traffic only when something changes."""

            # Display lost (cable, bus lock-up, ...). The next write starts
            # with a re-init; the counting is not affected at all.
        text = text.ljust(COLS)[:COLS]
        if self._shown[line] == text and self.ok:
            return
        try:
            if not self.ok:
                self.init()
            self._write_line_raw(line, text)
            self._shown[line] = text
        except OSError as exc:
            # Ekran kopmus (kablo, bus kilidi...). Bir dahaki yazmada
            # init'ten baslariz; sayim bundan hic etkilenmez.
            if self.ok:
                log.warning("Display write failed, will re-init: %s", exc)
            self.ok = False

    def show(self, lines: list[str]) -> None:
        for i, text in enumerate(lines[:ROWS]):
            self.write_line(i, text)

    def close(self) -> None:
        try:
            self.clear()
        except OSError:
            pass
        try:
            self._bus.close()
        except OSError:
            pass
        if self._rst is not None:
            self._rst.close()
