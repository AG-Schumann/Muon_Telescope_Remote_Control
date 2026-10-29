"""
muon_tui.screens.confirm — Generic yes/no confirmation modal

Usage:
    self.app.push_screen(ConfirmScreen("Are you sure?"), callback=on_result)

By default, focus starts on the cancel button ("No") so that an
accidental Enter press (or a hurried Tab/click) does not trigger the
potentially destructive action. Pass default_focus="confirm" to flip
this for confirmations where the safe default is actually to proceed.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmScreen(ModalScreen[bool]):
    """Dims the background and shows a confirmation box in the center."""

    CSS = """
    ConfirmScreen {
        align: center middle;
    }
    #confirm_box {
        width: 60;
        height: auto;
        border: round $warning;
        padding: 1 2;
        background: $surface;
    }
    #confirm_message {
        margin-bottom: 1;
    }
    #confirm_buttons {
        align: center middle;
    }
    #confirm_buttons Button {
        margin: 0 1;
    }
    """

    def __init__(
        self,
        message: str,
        confirm_label: str = "Yes",
        confirm_variant: str = "warning",
        cancel_label: str = "No",
        cancel_variant: str = "default",
        default_focus: str = "cancel",  # "cancel" or "confirm"
    ) -> None:
        super().__init__()
        self._message = message
        self._confirm_label = confirm_label
        self._confirm_variant = confirm_variant
        self._cancel_label = cancel_label
        self._cancel_variant = cancel_variant
        self._default_focus = default_focus

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm_box"):
            yield Static(self._message, id="confirm_message")
            with Horizontal(id="confirm_buttons"):
                yield Button(self._confirm_label, variant=self._confirm_variant, id="confirm_yes")
                yield Button(self._cancel_label, variant=self._cancel_variant, id="confirm_no")

    def on_mount(self) -> None:
        # Default focus goes to the safe option, so a stray Enter press
        # does not accidentally trigger the destructive action.
        target_id = "#confirm_yes" if self._default_focus == "confirm" else "#confirm_no"
        self.query_one(target_id, Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm_yes")