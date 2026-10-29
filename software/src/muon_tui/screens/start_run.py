"""
muon_tui.screens.start_run — Wizard for starting a new run

The user selects the run kind (hemisphere_scanning / measurement), the name
(auto-generated, editable) and the duration (fixed number of minutes /
unlimited). When "Start" is pressed, the result is passed back to the
Dashboard as a dict via dismiss().

If hemisphere_scanning is selected, pressing "Start" first opens
ScanSetupScreen: the duration of a scan is not something the user types in,
it follows from the number of scanned angles and the counting time per
angle. That is why the duration field on this screen is disabled when a
scan is selected, and the duration is taken from the scan setup instead.

"""

from __future__ import annotations

from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    RadioButton,
    RadioSet,
    Static,
)


from muon_tui.screens.scan_setup import ScanSetupScreen


def _auto_name(kind: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{kind}_{stamp}"


class StartRunScreen(Screen):
    """Wizard for starting a new run. Cancel/Start returns to the Dashboard."""

    CSS = """
    StartRunScreen {
        align: center middle;
    }
    #wizard_box {
        max-height: 100%;
        overflow-y: auto;
        width: 70;
        height: auto;
        border: round $primary;
        padding: 1 2;
    }
    #wizard_box Label {
        margin-top: 1;
    }
    #duration_input {
        margin-left: 4;
    }
    #button_row {
        margin-top: 2;
        align: center middle;
    }
    #button_row Button {
        margin: 0 1;
    }
    """

    def __init__(self, limit_min_deg: float = -50.0, limit_max_deg: float = 110.0) -> None:
        super().__init__()
        # Only passed on to ScanSetupScreen; the daemon re-checks every
        # move and is the actual authority on the limits.
        self._limit_min = limit_min_deg
        self._limit_max = limit_max_deg

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="wizard_box"):
            yield Static("[b]NEW RUN START[/b]")

            yield Label("Run kind:")
            with RadioSet(id="kind_select"):
                yield RadioButton("hemisphere_scanning (angular scanning)", value=True, id="kind_scan")
                yield RadioButton("measurement (fixed angle measurement)", id="kind_measurement")

            yield Label("Run name:")
            yield Input(value=_auto_name("hemisphere_scanning"), id="name_input")

            yield Label("Duration:")
            with RadioSet(id="duration_select"):
                yield RadioButton("Fixed duration (minutes)", value=True, id="duration_fixed")
                yield RadioButton("Unlimited (manual stop)", id="duration_unlimited")
            yield Input(value="30", id="duration_input", type="integer")
            yield Static("", id="duration_note")

            yield Static("", id="error_label")
            yield Static(
                "[dim]Press Enter in any field, or click Start.[/dim]",
                id="submit_hint",
            )

            with Horizontal(id="button_row"):
                yield Button("Start", variant="success", id="start_btn")
                yield Button("Cancel", variant="error", id="cancel_btn")
        yield Footer()

    def on_mount(self) -> None:
        self._apply_kind("hemisphere_scanning")

    def _apply_kind(self, kind: str) -> None:
        """A scan's duration comes out of the scan setup, so asking for
        one here would let the operator type a number that is quietly
        ignored. Disable the field and say why instead."""
        is_scan = kind == "hemisphere_scanning"
        self.query_one("#duration_select", RadioSet).disabled = is_scan
        self.query_one("#duration_input", Input).disabled = is_scan
        self.query_one("#duration_note", Static).update(
            "[dim]A scan runs until every angle is done -- the length comes "
            "from the scan setup on the next screen.[/dim]" if is_scan else ""
        )

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id == "kind_select":
            # When the kind changes, update the name if it still looks
            # auto-generated. If the user edited the name by hand, leave it
            # alone (simple heuristic: check whether the name starts with
            # one of the auto-name prefixes).
            
            name_input = self.query_one("#name_input", Input)
            new_kind = "hemisphere_scanning" if event.pressed.id == "kind_scan" else "measurement"
            if name_input.value.startswith("hemisphere_scanning_") or name_input.value.startswith("measurement_"):
                name_input.value = _auto_name(new_kind)
            self._apply_kind(new_kind)

        elif event.radio_set.id == "duration_select":
            duration_input = self.query_one("#duration_input", Input)
            duration_input.disabled = event.pressed.id == "duration_unlimited"

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in a text field starts the run. Without this Enter does
        nothing at all here, which reads as a broken Start button rather
        than as a form that only accepts the mouse."""
        self._start()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel_btn":
            self.dismiss(None)
            return

        if event.button.id == "start_btn":
            self._start()

    def _start(self) -> None:
        error_label = self.query_one("#error_label", Static)

        name = self.query_one("#name_input", Input).value.strip()
        if not name:
            error_label.update("[red]Error: run name cannot be empty.[/red]")
            return

        kind_set = self.query_one("#kind_select", RadioSet)
        kind = "hemisphere_scanning" if kind_set.pressed_button.id == "kind_scan" else "measurement"

        if kind == "hemisphere_scanning":
            # Hand over to the scan setup; it supplies the angles and
            # with them the run's length, so nothing else is read here.
            self._pending_name = name
            self.app.push_screen(
                ScanSetupScreen(
                    limit_min_deg=self._limit_min,
                    limit_max_deg=self._limit_max,
                ),
                callback=self._handle_scan_setup,
            )
            return

        duration_set = self.query_one("#duration_select", RadioSet)
        is_unlimited = duration_set.pressed_button.id == "duration_unlimited"

        duration_s: float | None = None
        if not is_unlimited:
            raw = self.query_one("#duration_input", Input).value.strip()
            try:
                duration_min = float(raw)
                if duration_min <= 0:
                    raise ValueError
            except ValueError:
                error_label.update("[red]Error: duration must be a positive number.[/red]")
                return
            duration_s = duration_min * 60.0

        result = {
            "name": name,
            "kind": kind,
            "duration_s": duration_s,  # None = suresiz
            "scan": None,
        }
        self.dismiss(result)

    def _handle_scan_setup(self, scan: dict | None) -> None:
        """Cancelling the scan setup returns to this wizard rather than
        aborting the whole thing, so a mistyped angle does not cost the
        run name and kind as well."""
        if scan is None:
            self.query_one("#error_label", Static).update(
                "[dim]Scan setup cancelled -- adjust and press Start again.[/dim]"
            )
            return

        self.dismiss(
            {
                "name": self._pending_name,
                "kind": "hemisphere_scanning",
                "duration_s": None,  # the scan itself decides when it is done
                "scan": scan,
            }
        )