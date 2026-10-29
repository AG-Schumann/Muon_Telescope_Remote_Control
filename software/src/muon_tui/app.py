"""
muon_tui.app — TUI entry point

Owns the DaemonClient connection (connects in the background with
retry, forwards events to the Dashboard screen) and the quit
confirmation flow.

Run with:
    pip install textual
    python -m muon_tui.app
    python -m muon_tui.app --socket /tmp/muon_telescope.sock
"""
from __future__ import annotations

import argparse
import asyncio

from textual.app import App
from textual.binding import Binding

from muon_tui.client import DaemonClient
from muon_tui.screens.dashboard import DashboardScreen
from muon_tui.screens.confirm import ConfirmScreen


class MuonTelescopeApp(App):
    """Main TUI application. Screen stack navigation makes Dashboard the home screen."""

    CSS_PATH = "app.tcss"

    # Global shortcuts -- shown automatically in the Footer, inherited by Dashboard too
    BINDINGS = [
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, socket_path: str = "/tmp/muon_telescope.sock") -> None:
        super().__init__()
        self.socket_path = socket_path
        self.client = DaemonClient(socket_path, on_event=self._on_daemon_event)
        self.dashboard = DashboardScreen()
        self._daemon_task: asyncio.Task | None = None

    def on_mount(self) -> None:
        self.push_screen(self.dashboard)
        self.sub_title = "connecting..."
        self._daemon_task = asyncio.create_task(
            self.client.connect_with_retry(
                on_connected=self._on_daemon_connected,
                on_disconnected=self._on_daemon_disconnected,
            )
        )

    async def on_unmount(self) -> None:
        # Stop the retry loop and close the socket cleanly on shutdown,
        # so we don't get a disconnected callback firing after the
        # screen/DOM has already been torn down.
        if self._daemon_task:
            self._daemon_task.cancel()
        await self.client.close()

    def _on_daemon_connected(self) -> None:
        self.sub_title = "connected"
        self.dashboard.on_daemon_connected()

    def _on_daemon_disconnected(self) -> None:
        self.sub_title = "disconnected -- retrying..."
        self.dashboard.on_daemon_disconnected()

    def _on_daemon_event(self, payload: dict) -> None:
        # Called from the client's read loop, which runs as an asyncio
        # task on this same event loop -- safe to touch reactive state
        # directly, no thread-safety wrapper needed.
        #
        # Every screen on the stack that wants live state gets it, not
        # just the Dashboard: the calibration screen sits on top of it
        # and shows a live IMU angle while the operator works.
        for screen in list(self.screen_stack):
            apply_status = getattr(screen, "apply_status", None)
            if apply_status is None:
                continue
            try:
                apply_status(payload)
            except Exception:
                pass  # a screen being torn down must not kill the read loop

    async def action_quit(self) -> None:
        message = (
            "Are you sure you want to quit?\n\n"
            "The background service will keep running --\n"
            "only this interface will close."
        )
        self.push_screen(
            ConfirmScreen(message, confirm_variant="error", cancel_variant="success"),
            callback=self._handle_quit_confirm,
        )

    def _handle_quit_confirm(self, confirmed: bool | None) -> None:
        if confirmed:
            asyncio.create_task(self.client.close())
            self.exit()


def _parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Muon telescope TUI")
    ap.add_argument("--socket", default="/tmp/muon_telescope.sock")
    return ap.parse_args(argv)


def main() -> None:
    args = _parse_args()
    app = MuonTelescopeApp(socket_path=args.socket)
    app.run()


if __name__ == "__main__":
    main()