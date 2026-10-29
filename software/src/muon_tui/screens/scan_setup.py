"""
muon_tui.screens.scan_setup -- Hemisphere scan parameters

A scan is a list of angles, not a duration: the operator says where to
start, where to stop, how big a step to take, and how long to count at
each angle. Everything else (how many points, how long the whole thing
takes) follows from those four numbers, so this screen derives them and
shows them back rather than asking for them again.

Why a separate screen
---------------------
The run wizard asks for a duration, which is the wrong question for a
scan: the total is (number of angles) x (minutes each) plus the moves in
between, and the moves are not predictable enough to type in. So for a
scan the wizard hands over to this screen and the duration field there
is disabled.

Approach direction
------------------
Angles are entered low to high; the daemon decides which end to start
from, so that every step of the scan moves in the direction the
telescope's own weight already pulls it (see hardware._move_blocking).
Either way the angle written into the data is the one the IMU measured,
not the one that was asked for, so a point is never mislabelled.

The soft limits are passed in from the Dashboard, which got them from
the daemon's status broadcast. They catch an out-of-range scan here,
while it is still one keystroke to fix; the daemon checks every move
again and remains the authority.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static

# Refuse absurd scans. A 0.1 deg step over the full range is thousands of
# points and weeks of counting -- far more likely a typo than an intent.
MAX_POINTS = 200


def scan_angles(start_deg: float, stop_deg: float, step_deg: float) -> list[float]:
    """The angles a scan will visit, both ends included.

    Built as start + i*step rather than by repeated addition, so float
    error cannot accumulate along the list and leave the last angle at
    89.999999 instead of 90.
    """
    n = int(round((stop_deg - start_deg) / step_deg))
    return [round(start_deg + i * step_deg, 6) for i in range(n + 1)]


def format_duration(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes = rest // 60
    if hours and minutes:
        return f"{hours} h {minutes} min"
    if hours:
        return f"{hours} h"
    return f"{minutes} min"


class ScanSetupScreen(Screen):
    """Ask for the scan range, step and per-angle counting time."""

    CSS = """
    ScanSetupScreen {
        align: center middle;
    }
    #scan_box {
        max-height: 100%;
        overflow-y: auto;
        width: 74;
        height: auto;
        border: round $primary;
        padding: 1 2;
    }
    #scan_box Label {
        margin-top: 1;
    }
    #scan_preview {
        margin-top: 1;
        margin-bottom: 1;
    }
    #scan_error {
        margin-top: 1;
    }
    #button_row {
        margin-top: 2;
        align: center middle;
    }
    #button_row Button {
        margin: 0 1;
    }
    """

    def __init__(
        self,
        limit_min_deg: float = -50.0,
        limit_max_deg: float = 110.0,
        start_deg: float = 0.0,
        stop_deg: float = 90.0,
        step_deg: float = 10.0,
        minutes_per_angle: float = 30.0,
    ) -> None:
        super().__init__()
        self._limit_min = limit_min_deg
        self._limit_max = limit_max_deg
        self._defaults = (start_deg, stop_deg, step_deg, minutes_per_angle)

    # ---------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        start_deg, stop_deg, step_deg, minutes = self._defaults
        yield Header()
        with Vertical(id="scan_box"):
            yield Static("[b]HEMISPHERE SCAN SETUP[/b]")
            yield Static(
                f"[dim]Allowed range: {self._limit_min:.0f} .. "
                f"{self._limit_max:.0f} deg[/dim]"
            )

            yield Label("Start angle (deg):")
            yield Input(value=f"{start_deg:g}", id="start_input")

            yield Label("Stop angle (deg):")
            yield Input(value=f"{stop_deg:g}", id="stop_input")

            yield Label("Step (deg):")
            yield Input(value=f"{step_deg:g}", id="step_input")

            yield Label("Counting time per angle (minutes):")
            yield Input(value=f"{minutes:g}", id="minutes_input")

            yield Static("", id="scan_preview")
            yield Static("", id="scan_error")
            yield Static(
                "[dim]Press Enter in any field, or click Start scan.[/dim]",
                id="submit_hint",
            )

            with Horizontal(id="button_row"):
                yield Button("Start scan", variant="success", id="scan_start_btn")
                yield Button("Cancel", variant="error", id="scan_cancel_btn")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_preview()

    # ------------------------------------------------------------ validation

    def _read_values(self) -> tuple[dict | None, str]:
        """Parse and check the four inputs.

        Returns (values, error). Exactly one of the two is meaningful, so
        the preview and the Start button can share this one check instead
        of validating the same numbers in two places and disagreeing.
        """
        raw = {
            "start_deg": self.query_one("#start_input", Input).value.strip(),
            "stop_deg": self.query_one("#stop_input", Input).value.strip(),
            "step_deg": self.query_one("#step_input", Input).value.strip(),
            "minutes": self.query_one("#minutes_input", Input).value.strip(),
        }

        values = {}
        for key, text in raw.items():
            try:
                values[key] = float(text)
            except ValueError:
                return None, f"{key.replace('_deg', '').replace('_', ' ')} must be a number."

        if values["step_deg"] <= 0:
            return None, "Step must be greater than zero."
        if values["minutes"] <= 0:
            return None, "Counting time must be greater than zero."
        if values["stop_deg"] < values["start_deg"]:
            return None, (
                "Stop angle must not be below the start angle -- a scan always "
                "runs upwards so every angle is approached from below."
            )
        for name, angle in (("Start", values["start_deg"]), ("Stop", values["stop_deg"])):
            if not (self._limit_min <= angle <= self._limit_max):
                return None, (
                    f"{name} angle {angle:.1f} deg is outside the soft limits "
                    f"({self._limit_min:.1f} .. {self._limit_max:.1f})."
                )

        angles = scan_angles(values["start_deg"], values["stop_deg"], values["step_deg"])
        if len(angles) > MAX_POINTS:
            return None, (
                f"{len(angles)} angles is more than the {MAX_POINTS} allowed -- "
                f"use a bigger step."
            )

        # The last angle lands on the step grid, which need not include
        # the stop angle (0..90 in steps of 40 stops at 80). Say so
        # rather than silently scanning a different range.
        values["angles"] = angles
        values["overshoot"] = angles[-1] != values["stop_deg"]
        return values, ""

    def _refresh_preview(self) -> None:
        try:
            preview = self.query_one("#scan_preview", Static)
            error = self.query_one("#scan_error", Static)
        except Exception:
            # An Input can post Changed while the screen is still being
            # composed, before these two exist further down the layout.
            return

        values, message = self._read_values()
        if values is None:
            preview.update("[dim]--[/dim]")
            error.update(f"[red]{message}[/red]")
            return

        angles = values["angles"]
        counting_s = len(angles) * values["minutes"] * 60.0
        shown = ", ".join(f"{a:g}" for a in angles[:8])
        if len(angles) > 8:
            shown += f", ... , {angles[-1]:g}"

        note = ""
        if values["overshoot"]:
            note = (
                f"\n[yellow]The step does not divide the range evenly, so the "
                f"last angle is {angles[-1]:g} deg, not "
                f"{values['stop_deg']:g}.[/yellow]"
            )

        preview.update(
            f"[green]{len(angles)} angles[/green]: {shown}\n"
            f"{values['minutes']:g} min each -> {format_duration(counting_s)} of "
            f"counting, plus the moves in between.{note}"
        )
        error.update("")

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh_preview()

    # -------------------------------------------------------------- buttons

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in any of the four fields starts the scan, so the whole
        screen can be filled in and submitted without reaching for the
        mouse."""
        self._start_scan()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "scan_cancel_btn":
            self.dismiss(None)
            return

        if event.button.id == "scan_start_btn":
            self._start_scan()

    def _start_scan(self) -> None:
        values, message = self._read_values()
        if values is None:
            self.query_one("#scan_error", Static).update(f"[red]{message}[/red]")
            return

        self.dismiss(
            {
                "start_deg": values["start_deg"],
                "stop_deg": values["stop_deg"],
                "step_deg": values["step_deg"],
                "acquisition_s": values["minutes"] * 60.0,
            }
        )
