"""
muon_tui.screens.dashboard — Main status screen

Two columns (run status / hardware status) + log panel + footer
shortcuts. All data now comes from the real daemon over the Unix
socket: apply_status() is called by the App whenever a status_update
event arrives, and user actions (start/stop/calibrate) send real
commands via self.app.client.request(...).
"""
from __future__ import annotations

from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Header, RichLog, Static
from textual.binding import Binding

from muon_daemon import protocol
from muon_tui.client import DaemonRequestError
from muon_tui.screens.start_run import StartRunScreen
from muon_tui.screens.confirm import ConfirmScreen
from muon_tui.screens.calibration import CalibrationScreen


def _local_time(started_utc: str) -> str:
    """Render the daemon's UTC start time in the operator's local time.

    The daemon stores UTC because that is what the data files use and it
    survives timezone changes; a person reading the screen wants the
    clock on their wall.
    """
    if not started_utc:
        return ""
    try:
        started = datetime.fromisoformat(started_utc)
    except ValueError:
        return started_utc  # unrecognised format: better raw than blank

    local = started.astimezone()
    # A scan can run past midnight, and then the time alone is a riddle.
    if local.date() != datetime.now().astimezone().date():
        return local.strftime("%d %b %H:%M:%S")
    return local.strftime("%H:%M:%S")


def _hm(seconds: float) -> str:
    """Durations as h/min. Seconds are noise on a scan that runs for
    hours, and a bare minute count past 90 stops being readable."""
    minutes = int(round(seconds / 60.0))
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours} h {minutes} min"
    if hours:
        return f"{hours} h"
    return f"{minutes} min"


class RunStatusPanel(Static):
    """Left column: active run information."""

    run_active: reactive[bool] = reactive(False)
    run_name: reactive[str] = reactive("")
    run_kind: reactive[str] = reactive("")
    started_iso: reactive[str] = reactive("")
    duration_target_s: reactive[float | None] = reactive(None)
    elapsed_s: reactive[float] = reactive(0.0)
    daq_count: reactive[int] = reactive(0)
    daq_counts: reactive[list] = reactive(list)
    rate_hz: reactive[float] = reactive(0.0)
    scan_active: reactive[bool] = reactive(False)
    scan_index: reactive[int] = reactive(0)
    scan_total: reactive[int] = reactive(0)
    scan_angle_deg: reactive[float] = reactive(0.0)
    scan_step_state: reactive[str] = reactive("")
    scan_acquisition_s: reactive[float] = reactive(0.0)
    scan_point_elapsed_s: reactive[float] = reactive(0.0)

    def render(self) -> str:
        if not self.run_active:
            return (
                "[b]RUN STATUS[/b]\n"
                "─────────────\n"
                "Status:   [dim]○ IDLE — no active run[/dim]\n"
            )

        scan_line = ""
        if self.scan_active:
            scan_line = (
                f"Scan:      point [b]{self.scan_index}/{self.scan_total}[/b] "
                f"at {self.scan_angle_deg:+.1f}° "
                f"([yellow]{self.scan_step_state or 'starting'}[/yellow])\n"
            )

        return (
            "[b]RUN STATUS[/b]\n"
            "─────────────\n"
            f"Status:    [green]● RUNNING[/green]\n"
            f"Name:      {self.run_name}\n"
            f"Kind:      {self.run_kind}\n"
            f"Started:   {self.started_iso}\n"
            f"{scan_line}"
            f"{self._duration_lines()}"
            f"DAQ count: {self.daq_count:,}{self._per_channel()}\n"
            f"Instantaneous rate: {self.rate_hz:.2f} Hz\n"
        )

    def _per_channel(self) -> str:
        """Break the total down by channel when there is more than one.

        The total is the headline because it is what the rate is built
        from, but with two channels the split is the thing you actually
        look at -- one channel gone quiet is invisible in a sum.
        """
        if len(self.daq_counts) < 2:
            return ""
        parts = "  ".join(f"ch{i} {n:,}" for i, n in enumerate(self.daq_counts))
        return f"   [dim]({parts})[/dim]"

    def _duration_lines(self) -> str:
        """Two clocks, because a scan has two.

        'Target duration' is the angle being counted right now -- the one
        that decides when this point's file closes. 'Total duration' is
        the whole scan. Showing only the second, as this panel used to,
        reads as if the current point had hours left on it.

        The total counts counting time only: the moves between angles are
        real minutes but not predictable ones, so they are called out as
        extra rather than folded into a number that would then always be
        wrong.
        """
        if not self.scan_active:
            if self.duration_target_s is None:
                return "Target duration: [dim]unlimited (manual stop)[/dim]\n"
            left = max(0.0, self.duration_target_s - self.elapsed_s)
            return (
                f"Target duration: {_hm(self.duration_target_s)} "
                f"([b]{_hm(left)} left[/b])\n"
            )

        point_total = self.scan_acquisition_s
        if self.scan_step_state == "acquiring":
            point_left = max(0.0, point_total - self.scan_point_elapsed_s)
            point_note = f"[b]{_hm(point_left)} left[/b]"
        else:
            # Not counting yet at this angle, so the point clock has not
            # started; saying "30 min left" here would be a guess.
            point_note = f"[dim]not counting yet ({self.scan_step_state})[/dim]"

        points_left = max(0, self.scan_total - self.scan_index)
        total_left = points_left * point_total
        if self.scan_step_state == "acquiring":
            total_left += max(0.0, point_total - self.scan_point_elapsed_s)

        return (
            f"Target duration: {_hm(point_total)} at this angle ({point_note})\n"
            f"Total duration:  {_hm(self.scan_total * point_total)} of counting "
            f"([b]{_hm(total_left)} left[/b] + moves)\n"
        )


class HardwareStatusPanel(Static):
    """Right column: hardware status (independent of run, always live)."""

    angle_deg: reactive[float] = reactive(0.0)
    target_angle_deg: reactive[float] = reactive(0.0)
    temp_c: reactive[float] = reactive(0.0)
    humidity_rh: reactive[float] = reactive(0.0)
    imu_stable: reactive[bool] = reactive(True)
    imu_tilt_deg: reactive[float] = reactive(0.0)
    imu_rms_dps: reactive[float] = reactive(0.0)
    imu_offset_deg: reactive[float] = reactive(0.0)
    imu_calibrated: reactive[bool] = reactive(False)
    imu_calibration_utc: reactive[str] = reactive("")
    hv_connected: reactive[bool] = reactive(False)
    hv_board: reactive[dict] = reactive(dict)
    hv_channels: reactive[list] = reactive(list)
    hv_error: reactive[str] = reactive("")

    def render(self) -> str:
        imu_status = "[green]● stable[/green]" if self.imu_stable else "[yellow]● vibration[/yellow]"
        hv_block = self._hv_block()
        if self.imu_calibrated:
            cal_line = f"[green]✓ loaded[/green] (offset {self.imu_offset_deg:+.2f}°)"
        else:
            cal_line = "[yellow]✗ none — calibrate the IMU[/yellow]"

        return (
            "[b]HARDWARE STATUS[/b]\n"
            "───────────────\n"
            f"Angle:        {self.angle_deg:.1f}° (target {self.target_angle_deg:.1f}°)\n"
            f"Temperature:  {self.temp_c:.1f}°C\n"
            f"Humidity:     {self.humidity_rh:.0f}%\n"
            f"IMU:          {imu_status}  (tilt={self.imu_tilt_deg:.2f}°, rms={self.imu_rms_dps:.2f}°/s)\n"
            f"Calibration:  {cal_line}\n"
            f"{hv_block}"
        )

    def _hv_block(self) -> str:
        """The HV summary plus one line per channel.

        A channel is shown as faulted only on a flag that means something
        is wrong (trip, over-current, interlock...), never on the ones
        that merely say what it is doing -- a channel ramping up is not a
        problem, and colouring it red would teach the operator to ignore
        red.
        """
        if not self.hv_connected:
            reason = f" ({self.hv_error})" if self.hv_error else ""
            return f"HV unit:      [dim]— not connected{reason}[/dim]\n"

        board = self.hv_board or {}

        # "interlock NO, no alarm" was on screen at all times and read
        # like a warning when it actually meant everything is fine. The
        # steady state says only what the software can do here -- read --
        # and the two safety signals appear only when they are not nominal,
        # which is the only time they carry information.
        warnings = []
        if board.get("alarm", 0):
            warnings.append(f"[red]ALARM {board['alarm']}[/red]")
        if str(board.get("interlock", "NO")).strip().upper() == "YES":
            warnings.append("[red]INTERLOCK OPEN[/red]")

        note = "  ".join(warnings) if warnings else "[dim]read only[/dim]"
        header = (
            f"HV unit:      [green]● {board.get('model', 'N1470')}[/green]  ({note})\n"
        )

        lines = []
        for ch in self.hv_channels:
            faults = ch.get("faults") or []
            if faults:
                state = "[red]" + ",".join(faults) + "[/red]"
            elif ch.get("on"):
                state = "[green]ON[/green]"
            else:
                state = "[dim]off[/dim]"
            lines.append(
                f"  CH{ch['channel']}  {ch['vmon']:7.1f} V "
                f"(set {ch['vset']:.0f})  {ch['imon']:6.1f} uA  {state}\n"
            )
        return header + "".join(lines)


class DashboardScreen(Screen):
    """Main status screen. n/c/s shortcuts navigate to other screens."""

    BINDINGS = [
        Binding("n", "new_run", "New Run"),
        Binding("c", "calibrate", "Calibrate"),
        Binding("s", "stop_run", "Stop"),
    ]

    def __init__(self) -> None:
        super().__init__()
        # Soft limits arrive with every status broadcast, so the TUI
        # never has to find and parse the config file itself.
        self._limits: dict = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield RunStatusPanel(id="run_status")
            yield HardwareStatusPanel(id="hw_status")
        yield RichLog(id="event_log", markup=True, max_lines=200)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#event_log", RichLog).write("[dim]Dashboard initialized -- connecting to daemon...[/dim]")

    # ---- daemon connection lifecycle --------------------------------------

    def on_daemon_connected(self) -> None:
        try:
            self.query_one("#event_log", RichLog).write("[green]✓ Connected to daemon.[/green]")
        except Exception:
            pass  # screen may already be torn down during app shutdown

    def on_daemon_disconnected(self) -> None:
        try:
            self.query_one("#event_log", RichLog).write(
                "[red]✗ Lost connection to daemon. Retrying...[/red]"
            )
        except Exception:
            pass  # screen may already be torn down during app shutdown

    # ---- receiving live state from the daemon -----------------------------

    def apply_status(self, payload: dict) -> None:
        """Called by the App whenever a status_update event arrives.
        Translates the daemon's snapshot dict into the reactive panels."""
        try:
            run_panel = self.query_one("#run_status", RunStatusPanel)
            hw_panel = self.query_one("#hw_status", HardwareStatusPanel)
        except Exception:
            return  # screen may already be torn down during app shutdown

        run = payload["run"]
        run_panel.run_active = run["active"]
        run_panel.run_name = run["name"]
        run_panel.run_kind = run["kind"]
        run_panel.duration_target_s = run["duration_s"]
        run_panel.elapsed_s = run["elapsed_s"]
        run_panel.daq_count = run["daq_count"]
        run_panel.daq_counts = run.get("daq_counts", [])
        run_panel.rate_hz = run["rate_hz"]
        scan = run.get("scan", {})
        run_panel.scan_active = scan.get("active", False)
        run_panel.scan_index = scan.get("index", 0)
        run_panel.scan_total = scan.get("total", 0)
        run_panel.scan_angle_deg = scan.get("angle_deg", 0.0)
        run_panel.scan_step_state = scan.get("step_state", "")
        run_panel.scan_acquisition_s = scan.get("acquisition_s", 0.0)
        run_panel.scan_point_elapsed_s = scan.get("point_elapsed_s", 0.0)
        # The daemon owns this. The panel used to stamp the current time
        # the first moment it saw an active run, which is right only if
        # the TUI happened to be open when the run began -- reconnect an
        # hour later and it claimed the run had just started.
        run_panel.started_iso = _local_time(run.get("started_utc", ""))
        run_panel.refresh(layout=True)

        hw = payload["hardware"]
        self._limits = hw.get("limits", {})
        hw_panel.angle_deg = hw["angle_deg"]
        hw_panel.target_angle_deg = hw["target_angle_deg"]
        hw_panel.temp_c = hw["temp_c"]
        hw_panel.humidity_rh = hw["humidity_rh"]
        hw_panel.imu_stable = hw["imu_stable"]
        hw_panel.imu_tilt_deg = hw["imu_tilt_deg"]
        hw_panel.imu_rms_dps = hw["imu_rms_dps"]
        # .get() so an older daemon that does not send these yet just
        # shows "not calibrated" instead of crashing the whole panel.
        hw_panel.imu_offset_deg = hw.get("imu_offset_deg", 0.0)
        hw_panel.imu_calibrated = hw.get("imu_calibrated", False)
        hw_panel.imu_calibration_utc = hw.get("imu_calibration_utc", "")
        hw_panel.hv_connected = hw["hv_connected"]
        hw_panel.hv_board = hw.get("hv_board", {})
        hw_panel.hv_channels = hw.get("hv_channels", [])
        hw_panel.hv_error = hw.get("hv_error", "")
        hw_panel.refresh(layout=True)

    # ---- new run -----------------------------------------------------------

    def action_new_run(self) -> None:
        run_panel = self.query_one("#run_status", RunStatusPanel)

        if run_panel.run_active:
            message = (
                f"There is an active run: [b]{run_panel.run_name}[/b]\n"
                "You are about to start a new run, which will stop the current one.\n\n"
                "Are you sure?"
            )
            self.app.push_screen(
                ConfirmScreen(message, confirm_variant="error", cancel_variant="success"),
                callback=self._handle_new_run_confirm,
            )
        else:
            self.app.push_screen(self._make_start_run_screen(), callback=self._handle_new_run_result)

    def _handle_new_run_confirm(self, confirmed: bool | None) -> None:
        log = self.query_one("#event_log", RichLog)
        if not confirmed:
            log.write("[dim]New run request cancelled (active run kept).[/dim]")
            return
        self.app.push_screen(self._make_start_run_screen(), callback=self._handle_new_run_result)

    def _make_start_run_screen(self) -> StartRunScreen:
        """The soft limits arrive with every status broadcast, so the
        wizard (and the scan setup behind it) can range-check angles
        without reading the config itself."""
        return StartRunScreen(
            limit_min_deg=self._limits.get("min_deg", -50.0),
            limit_max_deg=self._limits.get("max_deg", 110.0),
        )

    def _handle_new_run_result(self, result: dict | None) -> None:
        log = self.query_one("#event_log", RichLog)
        if result is None:
            log.write("[dim]New run cancelled.[/dim]")
            return
        self.run_worker(self._send_start_run(result), exclusive=True)

    async def _send_start_run(self, result: dict) -> None:
        log = self.query_one("#event_log", RichLog)
        try:
            response = await self.app.client.request(
                protocol.CMD_START_RUN,
                {
                    "name": result["name"],
                    "kind": result["kind"],
                    "duration_s": result["duration_s"],
                    "scan": result.get("scan"),
                },
            )
            scan = result.get("scan")
            if scan is not None:
                n_angles = len(response.get("angles", []))
                log.write(
                    f"[green]✓ Scan started:[/green] {result['name']} — "
                    f"{n_angles} angles from {scan['start_deg']:g}° to "
                    f"{scan['stop_deg']:g}° in {scan['step_deg']:g}° steps, "
                    f"{scan['acquisition_s'] / 60:g} min each"
                )
            else:
                duration_desc = (
                    "unlimited" if result["duration_s"] is None
                    else f"{result['duration_s'] / 60:.0f} min"
                )
                log.write(
                    f"[green]✓ Run started:[/green] {result['name']} "
                    f"(kind={result['kind']}, duration={duration_desc})"
                )
        except DaemonRequestError as exc:
            log.write(f"[red]✗ Could not start run: {exc}[/red]")

    # ---- stop run -----------------------------------------------------------

    def action_stop_run(self) -> None:
        run_panel = self.query_one("#run_status", RunStatusPanel)

        if not run_panel.run_active:
            log = self.query_one("#event_log", RichLog)
            log.write("[dim]No active run to stop.[/dim]")
            return
        else:
            message = (
                f"Are you sure you want to stop the active run: [b]{run_panel.run_name}[/b]?\n"
                "This will end the run and save the data collected so far."
            )
            self.app.push_screen(
                ConfirmScreen(message, confirm_variant="error", cancel_variant="success"),
                callback=self._handle_stop_run_confirm,
            )

    def _handle_stop_run_confirm(self, confirmed: bool | None) -> None:
        log = self.query_one("#event_log", RichLog)
        if not confirmed:
            log.write("[dim]Stop cancelled, run continues.[/dim]")
            return
        self.run_worker(self._send_stop_run(), exclusive=True)

    async def _send_stop_run(self) -> None:
        log = self.query_one("#event_log", RichLog)
        try:
            result = await self.app.client.request(protocol.CMD_STOP_RUN)
            log.write(
                f"[green]✓ Run stopped:[/green] {result['name']} "
                f"(final DAQ count={result['final_daq_count']:,}). Data saved."
            )
        except DaemonRequestError as exc:
            log.write(f"[red]✗ Could not stop run: {exc}[/red]")

    # ---- calibration --------------------------------------------------------

    def action_calibrate(self) -> None:
        run_panel = self.query_one("#run_status", RunStatusPanel)
        log = self.query_one("#event_log", RichLog)

        if run_panel.run_active:
            log.write(
                "[red]✗ Cannot calibrate while a run is active. Stop the run first.[/red]"
            )
            return

        hw_panel = self.query_one("#hw_status", HardwareStatusPanel)
        self.app.push_screen(
            CalibrationScreen(
                current_angle=hw_panel.angle_deg,
                imu_stable=hw_panel.imu_stable,
                imu_offset_deg=hw_panel.imu_offset_deg,
                imu_calibrated=hw_panel.imu_calibrated,
                imu_calibration_utc=hw_panel.imu_calibration_utc,
                limit_min_deg=self._limits.get("min_deg", 0.0),
                limit_max_deg=self._limits.get("max_deg", 0.0),
            ),
            callback=self._handle_calibration_result,
        )
        if not self._limits:
            log.write("[yellow]Waiting for the first status update from the daemon.[/yellow]")
            return

    def _handle_calibration_result(self, result: dict | None) -> None:
        # Hardware state (angle etc.) is updated via the daemon's own
        # status_update broadcast, not directly here -- nothing to do
        # besides an optional log line.
        if result is not None:
            log = self.query_one("#event_log", RichLog)
            log.write("[dim]Calibration screen closed.[/dim]")