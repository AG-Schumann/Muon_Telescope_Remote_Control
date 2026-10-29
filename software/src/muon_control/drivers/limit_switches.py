"""
muon_control.drivers.limit_switches — Layer 1: limit switch'ler

Iki mekanik switch: sifir (homing referansi) ve max (uzak uc / guvenlik).

Kablolama varsayimi:
    switch, sinyal pini ile GND arasinda -> pull_up=True -> basili = LOW (is_pressed)
Farkli kablolanması durumunda (sinyal<->3V3, pull-down) pull_up=False yapılarak sistem uygun hale getirilebilir.
"""

from __future__ import annotations

from gpiozero import Button


class LimitSwitches:
    def __init__(self, zero_pin: int, max_pin: int,
                 pull_up: bool = True, bounce_s: float = 0.01):
        self.zero = Button(zero_pin, pull_up=pull_up, bounce_time=bounce_s)
        self.max = Button(max_pin, pull_up=pull_up, bounce_time=bounce_s)

    @property
    def at_zero(self) -> bool:
        return self.zero.is_pressed

    @property
    def at_max(self) -> bool:
        return self.max.is_pressed

    def any_triggered(self) -> bool:
        return self.at_zero or self.at_max

    @classmethod
    def from_config(cls, cfg: dict) -> "LimitSwitches":
        g = cfg["gpio"]
        return cls(zero_pin=g["hall_limit_zero"], max_pin=g["hall_limit_max"])