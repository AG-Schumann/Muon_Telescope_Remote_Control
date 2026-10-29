"""
muon_tui.screens.imu_calibration -- IMU mounting calibration

The IMU sits in a 3D-printed box bolted to the telescope, so its zero
never lines up exactly with the mechanical zero. Measured on the bench:
level reads about -2 deg. That offset is specific to this build and
changes whenever the sensor is remounted, so it is measured here rather
than written into the config by hand.

How the operator uses this screen
---------------------------------
1. Move the telescope to a known angle, verified with a separate
   digital inclinometer (this screen trusts that instrument, not the
   step counter -- the steps/deg ratio is not yet established at first
   setup, so using it here would leave two unknowns in one equation).
2. Type the inclinometer reading into "Reference angle" and press
   Capture. The daemon averages the IMU for a few seconds and stores
   the raw axes alongside the reference.
3. Repeat for a spread of angles. Include both signs -- an asymmetric
   error only shows up if negative angles are in the set.
4. Press "Fit & save".

What the fit does
-----------------
Two corrections come out of the same points, because there are two
different errors to remove.

The offset is the mounting: the mean of (imu - reference). One number,
a plain rotation, and it is all a well-behaved sensor needs.

The ellipse is the sensor itself. Held still, a perfect accelerometer
puts (ay, az) on a circle of radius 1 g at every angle; a real one has
a zero offset and a gain error per axis, so the points trace an ellipse
instead. That shows up as residuals that VARY WITH ANGLE, which no
single offset can remove -- and it is why every point keeps its raw
ay/az here rather than just the tilt.

With four points or more the screen fits both and shows what each is
worth, then saves the better one. The residual column is the honest
scoreboard: whatever the chosen fit cannot explain is still in there.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from muon_daemon import protocol
from muon_control.drivers.imu import IMU, fit_ellipse
from muon_tui.client import DaemonRequestError

# How long the daemon averages the IMU for one calibration point.
SAMPLE_SECONDS = 10.0

# Suggested angles. Spread over the working range and including both
# signs, so an asymmetry between the positive and negative side shows up.
SUGGESTED_ANGLES = "-45, -30, 0, 30, 45, 60, 90"


def _worst(values: list[float]) -> float:
    return max(abs(v) for v in values) if values else 0.0


class ImuCalibrationScreen(Screen):
    """Collect (reference angle, IMU reading) pairs and fit an offset."""

    CSS = """
    ImuCalibrationScreen {
        align: center middle;
    }
    #imu_box {
        max-height: 100%;
        overflow-y: auto;
        width: 80;
        height: auto;
        border: round $primary;
        padding: 1 2;
    }
    #imu_box Label {
        margin-top: 1;
    }
    #imu_status {
        margin-top: 1;
        margin-bottom: 1;
    }
    #capture_row Input {
        width: 20;
    }
    #capture_row {
        height: auto;
    }
    #points_table {
        height: 12;
        margin-top: 1;
    }
    #button_row {
        margin-top: 1;
        align: center middle;
    }
    #button_row Button {
        margin: 0 1;
    }
    """

    def __init__(self, current_offset_deg: float = 0.0,
                 already_calibrated: bool = False) -> None:
        super().__init__()
        self._offset = current_offset_deg
        self._already_calibrated = already_calibrated
        # Each point: {"ref": float, "raw_tilt": float, "ay": float, "az": float}
        self._points: list[dict] = []

    # ---------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="imu_box"):
            yield Static("[b]IMU CALIBRATION[/b]")
            yield Static(self._info_text(), id="imu_info")
            yield Static("", id="imu_status")

            yield Label(
                f"Move the telescope to a known angle, confirm it with your "
                f"inclinometer, then capture.\nSuggested: {SUGGESTED_ANGLES}"
            )
            with Horizontal(id="capture_row"):
                yield Input(placeholder="angle in deg", id="ref_input")
                yield Button("Capture", variant="primary", id="capture_btn")

            yield DataTable(id="points_table", zebra_stripes=True)

            with Horizontal(id="button_row"):
                yield Button(
                    "Refit & save" if self._already_calibrated else "Fit & save",
                    variant="warning",
                    id="fit_btn",
                )
                yield Button("Delete last", variant="default", id="undo_btn")
                yield Button("Clear all", variant="error", id="clear_btn")
                yield Button("Close", variant="default", id="close_btn")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#points_table", DataTable)
        table.add_columns("Reference", "IMU raw", "Residual", "ay", "az")

    # ----------------------------------------------------------- info panel

    def _info_text(self) -> str:
        source = "loaded from file" if self._already_calibrated else "none saved yet"
        return (
            f"Stored offset: {self._offset:+.2f} deg ({source})   |   "
            f"points: {len(self._points)}   |   "
            f"averaging window: {SAMPLE_SECONDS:.0f} s"
        )

    def _refresh_info(self) -> None:
        self.query_one("#imu_info", Static).update(self._info_text())

    def _refresh_table(self) -> None:
        """Redraw the whole table. Residuals depend on the offset fitted
        from every point, so one new point changes all the other rows --
        updating a single row would leave the rest stale."""
        table = self.query_one("#points_table", DataTable)
        table.clear()

        if not self._points:
            self._refresh_info()
            return

        _, _, residuals = self._best_fit()
        for p, residual in zip(self._points, residuals):
            table.add_row(
                f"{p['ref']:+.2f}",
                f"{p['raw_tilt']:+.2f}",
                f"{residual:+.3f}",
                f"{p['ay']:+.4f}",
                f"{p['az']:+.4f}",
            )
        self._refresh_info()

    def _fitted_offset(self) -> float:
        """Mean of (imu - reference). With one point this is just that
        point's error, which is a perfectly good starting calibration."""
        if not self._points:
            return 0.0
        return sum(p["raw_tilt"] - p["ref"] for p in self._points) / len(self._points)

    def _fitted_ellipse(self) -> dict | None:
        """Fit the sensor ellipse, or None if it cannot be done.

        Four unknowns need four points, and a fit that fails (all the
        points bunched at one angle, say) is not an error worth stopping
        for -- the offset-only calibration is still perfectly usable, so
        we fall back to it quietly.
        """
        if len(self._points) < 4:
            return None
        try:
            return fit_ellipse([(p["ay"], p["az"]) for p in self._points])
        except ValueError:
            return None

    def _residuals(self, ellipse: dict | None) -> tuple[list[float], float]:
        """Residuals for one candidate fit, and the offset that goes with
        it. Written once and used for both candidates, so the comparison
        on screen is genuinely like-for-like."""
        if ellipse is None:
            tilts = [p["raw_tilt"] for p in self._points]
        else:
            # Same maths the driver will use once this is saved.
            probe = IMU.__new__(IMU)
            probe.offset_deg = 0.0
            probe.bias_y, probe.bias_z = ellipse["bias_y"], ellipse["bias_z"]
            probe.scale_y, probe.scale_z = ellipse["scale_y"], ellipse["scale_z"]
            tilts = [probe.tilt_from_axes(p["ay"], p["az"]) for p in self._points]

        offset = sum(t - p["ref"] for t, p in zip(tilts, self._points)) / len(self._points)
        residuals = [(t - offset) - p["ref"] for t, p in zip(tilts, self._points)]
        return residuals, offset

    def _best_fit(self) -> tuple[dict | None, float, list[float]]:
        """Pick between offset-only and offset+ellipse.

        The ellipse has more freedom, so it will always fit the points at
        least as well -- but only take it if it is clearly better, not
        just marginally. Otherwise a noisy set of points would buy four
        stored parameters that describe nothing but that noise.
        """
        offset_res, offset_only = self._residuals(None)
        ellipse = self._fitted_ellipse()
        if ellipse is None:
            return None, offset_only, offset_res

        ell_res, ell_offset = self._residuals(ellipse)
        if _worst(ell_res) < 0.5 * _worst(offset_res):
            return ellipse, ell_offset, ell_res
        return None, offset_only, offset_res

    def _max_residual(self) -> float:
        _, _, residuals = self._best_fit()
        return _worst(residuals)

    def _set_busy(self, busy: bool) -> None:
        self.query_one("#capture_btn", Button).disabled = busy
        self.query_one("#fit_btn", Button).disabled = busy
        self.query_one("#ref_input", Input).disabled = busy

    # -------------------------------------------------------------- buttons

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close_btn":
            self.dismiss({"offset_deg": self._offset})
        elif event.button.id == "capture_btn":
            self.run_worker(self._do_capture(), exclusive=True)
        elif event.button.id == "fit_btn":
            self.run_worker(self._do_fit(), exclusive=True)
        elif event.button.id == "undo_btn":
            self._do_undo()
        elif event.button.id == "clear_btn":
            self._do_clear()

    # -------------------------------------------------------------- actions

    async def _do_capture(self) -> None:
        status = self.query_one("#imu_status", Static)
        raw = self.query_one("#ref_input", Input).value.strip()

        try:
            ref = float(raw)
        except ValueError:
            status.update("[red]Reference angle must be a number.[/red]")
            return

        self._set_busy(True)
        status.update(
            f"[yellow]Averaging the IMU for {SAMPLE_SECONDS:.0f} s -- "
            f"keep the telescope still.[/yellow]"
        )

        try:
            result = await self.app.client.request(
                protocol.CMD_IMU_SAMPLE,
                {"duration_s": SAMPLE_SECONDS},
                timeout=SAMPLE_SECONDS + 15.0,
            )
        except DaemonRequestError as exc:
            status.update(f"[red]Capture failed: {exc}[/red]")
            self._set_busy(False)
            return

        self._points.append(
            {
                "ref": ref,
                "raw_tilt": result["raw_tilt_deg"],
                "ay": result["ay"],
                "az": result["az"],
                # Error bar for this point: how much the IMU's own mean
                # wandered over the averaging window. Measured, not assumed.
                "sigma": result.get("sigma_deg", 0.0),
                "n": result.get("n", 0),
            }
        )
        self._points.sort(key=lambda p: p["ref"])
        self._refresh_table()

        status.update(
            f"[green]Captured {ref:+.1f} deg -> IMU reads "
            f"{result['raw_tilt_deg']:+.2f} deg "
            f"({result['n']} samples).[/green]"
        )
        self.query_one("#ref_input", Input).value = ""
        self._set_busy(False)

    async def _do_fit(self) -> None:
        status = self.query_one("#imu_status", Static)

        if not self._points:
            status.update("[red]Capture at least one point first.[/red]")
            return

        ellipse, offset, residuals = self._best_fit()
        worst = _worst(residuals)
        offset_only_res, _ = self._residuals(None)

        try:
            await self.app.client.request(
                protocol.CMD_IMU_SET_CALIBRATION,
                # The points go too, so the daemon can keep a record of
                # how this calibration was arrived at and plot it.
                {"offset_deg": offset, "ellipse": ellipse,
                 "points": self._points},
            )
        except DaemonRequestError as exc:
            status.update(f"[red]Could not save the calibration: {exc}[/red]")
            return

        self._offset = offset
        self._already_calibrated = True
        self._refresh_info()

        # Say which fit was saved and what it bought, because "worst
        # residual 0.24" on its own does not tell the operator whether
        # they are done or whether the rig needs more work.
        if ellipse is not None:
            gained = _worst(offset_only_res)
            detail = (
                f"Saved [b]offset + sensor ellipse[/b]: offset {offset:+.3f} deg, "
                f"bias ({ellipse['bias_y']:+.4f}, {ellipse['bias_z']:+.4f}) g, "
                f"scale ({ellipse['scale_y']:.4f}, {ellipse['scale_z']:.4f}).\n"
                f"Worst residual {worst:.3f} deg, down from {gained:.3f} deg with "
                f"an offset alone."
            )
            verdict = self._verdict(worst, have_ellipse=True)
        else:
            detail = (
                f"Saved [b]offset only[/b]: {offset:+.3f} deg from "
                f"{len(self._points)} points.\nWorst residual: {worst:.3f} deg."
            )
            verdict = self._verdict(worst, have_ellipse=False)

        status.update(f"[green]{detail}[/green]\n{verdict}")

    def _verdict(self, worst: float, have_ellipse: bool) -> str:
        """Turn the worst residual into what to do next."""
        if len(self._points) < 4:
            return (
                "[yellow]Fewer than 4 points, so only an offset could be fitted. "
                "Capture more angles, spread over the whole range and both signs, "
                "to see whether the sensor itself needs correcting.[/yellow]"
            )
        if worst < 0.3:
            return "[green]That is as good as this sensor gets. Nothing more to do.[/green]"
        if worst < 1.0:
            return (
                "[yellow]Noticeable but usable. More points, spread wider, may "
                "tighten it -- or this is simply the sensor's noise floor.[/yellow]"
            )
        if have_ellipse:
            return (
                "[red]Still over a degree even with the ellipse. Something is "
                "moving that should not be: check the sensor box is bolted tight "
                "and that the telescope was fully still at every capture.[/red]"
            )
        return (
            "[red]An offset alone cannot describe this sensor, and the ellipse fit "
            "did not improve on it enough to be worth saving. Capture more angles "
            "across the full range -- points bunched together cannot pin down an "
            "ellipse.[/red]"
        )

    def _do_undo(self) -> None:
        status = self.query_one("#imu_status", Static)
        if not self._points:
            status.update("[dim]Nothing to delete.[/dim]")
            return
        removed = self._points.pop()
        self._refresh_table()
        status.update(f"[dim]Removed the point at {removed['ref']:+.1f} deg.[/dim]")

    def _do_clear(self) -> None:
        self._points.clear()
        self._refresh_table()
        self.query_one("#imu_status", Static).update(
            "[dim]All points cleared. The saved offset is unchanged until "
            "you fit again.[/dim]"
        )