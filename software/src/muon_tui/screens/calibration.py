"""
muon_tui.screens.calibration -- Calibration mode

Only reachable when no run is active (Dashboard enforces this before
pushing this screen -- and the daemon enforces it again, authoritatively,
since it rejects calibrate_* commands while RUNNING).

Sends real commands to the daemon over the Unix socket; the daemon's
own status_update broadcast is what actually updates Dashboard's
HardwareStatusPanel -- this screen just shows the direct response for
immediate feedback.

No homing
--------
There is no "home" step any more. Homing existed to find a mechanical
reference with the hall sensors, but the magnets are large enough that
the sensor triggers a degree or two before the true position, which
made the reference softer than the thing it was supposed to anchor.
The IMU reads the absolute angle directly and to better than a tenth of
a degree once averaged, so it is the reference now. The hall sensors at
0 and 90 deg stay as an independent cross-check, not as the origin.

The "Stop motor" button is an emergency stop: it stays enabled even
while a motion is in progress (that's the whole point -- you need it
precisely when a motion is already running), and it works even though
the daemon-side motion command is still blocked on the executor thread,
because the daemon reads and dispatches requests concurrently rather
than one-at-a-time (see server.py's _handle_client).

The soft limits are passed in from the Dashboard, which got them from
the daemon's status broadcast. They are only used to label the input
and to catch an obviously bad number early -- the daemon checks them
again and is the authority.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static

from muon_daemon import protocol
from muon_tui.client import DaemonRequestError
from muon_tui.screens.imu_calibration import ImuCalibrationScreen

# One generous timeout for every motion. With the 100:1 gearbox a
# full-range move takes minutes, and a timeout firing mid-motion is a
# bad failure mode: the screen would report an error while the motor is
# still turning.
MOTION_TIMEOUT_S = 900.0


class CalibrationScreen(Screen):
    """Calibration wizard. Only usable while no run is active."""

    CSS = """
    CalibrationScreen {
        align: center middle;
    }
    #cal_box {
        max-height: 100%;
        overflow-y: auto;
        width: 76;
        height: auto;
        border: round $primary;
        padding: 1 2;
    }
    #cal_box Label {
        margin-top: 1;
    }
    #cal_status {
        margin-top: 1;
        margin-bottom: 1;
    }
    #cal_loaded {
        margin-top: 1;
    }
    #move_row Input {
        width: 20;
    }
    #move_row {
        height: auto;
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
        current_angle: float,
        imu_stable: bool,
        limit_min_deg: float,
        limit_max_deg: float,
        imu_offset_deg: float = 0.0,
        imu_calibrated: bool = False,
        imu_calibration_utc: str = "",
    ) -> None:
        super().__init__()
        self._current_angle = current_angle
        self._imu_stable = imu_stable
        self._limit_min = limit_min_deg
        self._limit_max = limit_max_deg
        self._imu_offset = imu_offset_deg
        self._imu_calibrated = imu_calibrated
        self._imu_calibration_utc = imu_calibration_utc
        # Live IMU tilt, refreshed from every status broadcast while this
        # screen is open (see apply_status). Seeded from the step count
        # so the first render is not misleadingly 0.
        self._imu_tilt = current_angle

    # ---------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="cal_box"):
            yield Static("[b]CALIBRATION MODE[/b]")
            yield Static(self._info_text(), id="cal_info")
            yield Static("", id="cal_status")

            yield Static(self._calibration_text(), id="cal_loaded")
            with Horizontal():
                yield Button(
                    "Recalibrate IMU" if self._imu_calibrated else "IMU calibration",
                    variant="primary",
                    id="imu_cal_btn",
                )

            yield Label(
                f"Move to angle ({self._limit_min:.0f}..{self._limit_max:.0f} deg):"
            )
            with Horizontal(id="move_row"):
                yield Input(value=f"{self._current_angle:.1f}", id="angle_input")
                yield Button("Move", variant="primary", id="move_btn")

            with Horizontal():
                yield Button("Stop motor", variant="error", id="stop_motor_btn")
            with Horizontal(id="button_row"):
                yield Button("Close", variant="default", id="close_btn")
        yield Footer()

    # ----------------------------------------------------------- info panel

    def _info_text(self) -> str:
        """Both numbers, side by side. The step count is where the motor
        thinks it is; the IMU is where the telescope actually is, with
        the mounting offset already applied. After a move they should
        agree to a few tenths -- a gap that does not close is the useful
        signal here, so it is worth showing rather than hiding."""
        if self._imu_stable:
            imu_line = "[green]stable[/green]"
        else:
            imu_line = "[yellow]vibrating -- wait before trusting it[/yellow]"
        return (
            f"Step count: {self._current_angle:6.1f} deg    "
            f"IMU now: {self._imu_tilt:+7.2f} deg ({imu_line})"
        )

    def apply_status(self, payload: dict) -> None:
        """Called by the App on every status broadcast while this screen
        is on the stack, so the IMU reading on screen tracks the
        telescope live instead of freezing at whatever it was when the
        screen opened."""
        hw = payload.get("hardware", {})
        self._current_angle = hw.get("angle_deg", self._current_angle)
        self._imu_tilt = hw.get("imu_tilt_deg", self._imu_tilt)
        self._imu_stable = hw.get("imu_stable", self._imu_stable)
        self._imu_offset = hw.get("imu_offset_deg", self._imu_offset)
        self._imu_calibrated = hw.get("imu_calibrated", self._imu_calibrated)
        self._imu_calibration_utc = hw.get("imu_calibration_utc", self._imu_calibration_utc)

        try:
            self.query_one("#cal_info", Static).update(self._info_text())
            self.query_one("#cal_loaded", Static).update(self._calibration_text())
        except Exception:
            pass  # screen not composed yet, or already being torn down

    def _calibration_text(self) -> str:
        """Tell the operator whether the angles on screen rest on a
        stored calibration. A loaded one is not automatically a good
        one -- it describes how the IMU box was bolted on the day it was
        measured, and it silently becomes wrong the moment the sensor is
        remounted -- so it is worth checking, not just trusting."""
        if not self._imu_calibrated:
            return (
                "[yellow]No stored IMU calibration -- angles include the "
                "mounting error. Run the IMU calibration.[/yellow]"
            )

        when = self._imu_calibration_utc[:16].replace("T", " ") or "unknown date"
        return (
            f"[green]Stored IMU calibration loaded[/green] "
            f"(offset {self._imu_offset:+.2f} deg, saved {when} UTC).\n"
            f"[dim]Please check it still holds -- it is only valid while "
            f"the sensor stays bolted where it was measured. "
            f"Recalibrate if in doubt.[/dim]"
        )

    def _refresh_info(self) -> None:
        self.query_one("#cal_info", Static).update(self._info_text())

    def _set_buttons_disabled(self, disabled: bool) -> None:
        # Deliberately NOT including stop_motor_btn here: the emergency
        # stop must remain clickable while a motion is in progress --
        # disabling it during the one moment you'd actually need it
        # would defeat its purpose.
        self.query_one("#move_btn", Button).disabled = disabled
        self.query_one("#imu_cal_btn", Button).disabled = disabled
        self.query_one("#angle_input", Input).disabled = disabled

    # -------------------------------------------------------------- buttons

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close_btn":
            self.dismiss({"angle": self._current_angle})
        elif event.button.id == "move_btn":
            self.run_worker(self._do_move(), exclusive=True)
        elif event.button.id == "imu_cal_btn":
            self.app.push_screen(
                ImuCalibrationScreen(
                    current_offset_deg=self._imu_offset,
                    already_calibrated=self._imu_calibrated,
                ),
                callback=self._handle_imu_calibration_result,
            )
        elif event.button.id == "stop_motor_btn":
            # Not exclusive/not grouped with the motion worker -- this
            # must be sendable concurrently while one is still running.
            self.run_worker(self._do_stop_motor())

    def _handle_imu_calibration_result(self, result: dict | None) -> None:
        """Reflect a freshly fitted offset without waiting for the next
        status broadcast, so the note above does not keep showing the
        old number right after a recalibration."""
        if result is None:
            return
        self._imu_offset = result.get("offset_deg", self._imu_offset)
        self._imu_calibrated = True
        self._imu_calibration_utc = ""  # refreshed by the daemon's broadcast
        self.query_one("#cal_loaded", Static).update(self._calibration_text())
        self.query_one("#imu_cal_btn", Button).label = "Recalibrate IMU"

    # -------------------------------------------------------------- actions

    async def _do_move(self) -> None:
        status = self.query_one("#cal_status", Static)
        raw = self.query_one("#angle_input", Input).value.strip()

        try:
            target = float(raw)
        except ValueError:
            status.update("[red]Angle must be a number.[/red]")
            return

        # Caught here as well as in the daemon, so the user gets an
        # instant answer instead of a socket round-trip for an obviously
        # out-of-range number.
        if not (self._limit_min <= target <= self._limit_max):
            status.update(
                f"[red]{target:.1f} deg is outside the soft limits "
                f"({self._limit_min:.1f} .. {self._limit_max:.1f}).[/red]"
            )
            return

        status.update(f"[yellow]Moving to {target:.1f} deg...[/yellow]")

        self._set_buttons_disabled(True)

        try:
            result = await self.app.client.request(
                protocol.CMD_CALIBRATE_MOVE, {"angle_deg": target},
                timeout=MOTION_TIMEOUT_S,
            )
        except DaemonRequestError as exc:
            status.update(f"[red]Move failed: {exc}[/red]")
            self._set_buttons_disabled(False)
            return

        self._current_angle = result["angle_deg"]
        self._refresh_info()
        # The measured angle, not the one that was typed. They differ by
        # a fraction of a degree and the measured one is the real answer.
        status.update(
            f"[green]Now at {self._current_angle:.2f} deg[/green] "
            f"[dim](asked for {target:.1f})[/dim]"
        )
        self._set_buttons_disabled(False)

    async def _do_stop_motor(self) -> None:
        status = self.query_one("#cal_status", Static)
        try:
            await self.app.client.request(protocol.CMD_STOP_MOTOR)
        except DaemonRequestError as exc:
            status.update(f"[red]Stop request failed: {exc}[/red]")
            return

        # An interrupted motion leaves the step counter out of sync with
        # the real position, but the IMU still knows where the telescope
        # is, so the daemon re-seeds the counter from it -- there is
        # nothing the operator has to redo.
        status.update("[red]Motor stopped.[/red]")
        # The in-progress worker (if any) will independently receive its
        # own error response once the interrupted motion unwinds, and
        # will re-enable the controls itself via its except-branch --
        # nothing further to do here.