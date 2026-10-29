"""
muon_control.buttons — Layer 1: front-panel buttons (J10)

Four buttons, all wired the same way: GPIO -> 1k -> button -> GND,
filtered with 100 nF (see schematic Front_Panel_Buttons). There is no
pull-up on the board; the Pi's internal pull-up is used: HIGH when idle,
LOW when pressed.

Each button has a hold time, so that an accidental touch does nothing:
~1 s for start/stop, 5 s for the irreversible reset/shutdown. gpiozero's
when_held provides this directly; we only supply the times.

This module does not decide WHAT to do -- it only passes the button
event on to the daemon (see server.py). The callbacks run in gpiozero's
own thread; the daemon moves them onto its event loop.
"""

from __future__ import annotations

from typing import Callable

NAMES = ("start", "stop", "reset", "shutdown")


class FrontPanelButtons:
    def __init__(self, pins: dict[str, int], hold_s: dict[str, float],
                 pull_up: bool = True) -> None:
        from gpiozero import Button
        self._buttons: dict[str, "Button"] = {}
        for name in NAMES:
            self._buttons[name] = Button(
                pins[name], pull_up=pull_up, hold_time=hold_s[name],
                bounce_time=0.02,        # RC filter on the board, but debounce in software as well
            )

    @classmethod
    def from_config(cls, cfg: dict) -> "FrontPanelButtons":
        g, b = cfg["gpio"], cfg.get("buttons", {})
        pins = {n: g[f"btn_{n}"] for n in NAMES}
        hold = {n: float(b.get(f"{n}_hold_s", 5.0 if n in ("reset", "shutdown") else 1.0))
                for n in NAMES}
        return cls(pins, hold, pull_up=b.get("pull_up", True))

    def on_held(self, name: str, callback: Callable[[], None]) -> None:
        """Call callback once the button has been held for its hold time
        (once per press)."""
        self._buttons[name].when_held = lambda: callback()

    def hold_time(self, name: str) -> float:
        return self._buttons[name].hold_time

    def close(self) -> None:
        for b in self._buttons.values():
            b.close()
