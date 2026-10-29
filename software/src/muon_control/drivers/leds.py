"""
muon_control.leds — Layer 1: front-panel status LEDs (J12)

Two LEDs:
    LED_ACTIVE_GPIO4    daemon is running  (i.e. the Pi is on and the software is up)
    LED_MEAS_GPIO24     a measurement is in progress

This module does not decide WHEN the LEDs are lit; it only switches them
on and off when told to. The decisions are made in the daemon (see
server.py and state.py).

The polarity depends on the wiring. J12 provides both +3.3 V and GND:
an LED between 3.3 V and the GPIO lights when the GPIO is pulled LOW
(active_high = false); an LED between the GPIO and GND lights when the
GPIO is driven HIGH (active_high = true). The two are configured
separately: on the current board they are wired differently (see the
defaults), and if the wiring changes later, only the config has to change.
"""

from __future__ import annotations

from gpiozero import LED


class StatusLeds:
    def __init__(self, active_pin: int, measuring_pin: int,
                 active_led_active_high: bool = False,
                 measuring_led_active_high: bool = True) -> None:
        # initial_value=False: ikisi de sonuk baslar. Daemon acilinca
        # "active"i kendisi yakar, boylece led gercekten "yazilim
        # ayakta" demis olur -- sadece "karta guc gelmis" degil.
        self._active = LED(active_pin, active_high=active_led_active_high,
                           initial_value=False)
        self._measuring = LED(measuring_pin, active_high=measuring_led_active_high,
                              initial_value=False)

    @classmethod
    def from_config(cls, cfg: dict) -> "StatusLeds":
        g = cfg["gpio"]
        leds = cfg.get("leds", {})
        return cls(
            active_pin=g["led_active"],
            measuring_pin=g["led_meas"],
            active_led_active_high=leds.get("active_led_active_high", False),
            measuring_led_active_high=leds.get("measuring_led_active_high", True),
        )

    def set_active(self, on: bool) -> None:
        self._active.on() if on else self._active.off()

    def set_measuring(self, on: bool) -> None:
        self._measuring.on() if on else self._measuring.off()

    def close(self) -> None:
        """Switch both off and release the pins. Called when the daemon
        shuts down; otherwise the panel would keep showing "measurement
        in progress"."""
        for led in (self._active, self._measuring):
            try:
                led.off()
                led.close()
            except Exception:  # noqa: BLE001 -- kapanisi hicbir sey engellemesin
                pass
