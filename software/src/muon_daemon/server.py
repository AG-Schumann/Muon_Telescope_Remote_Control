"""
muon_daemon.server — Unix domain socket server

Runs an asyncio server on a Unix socket file. Each connected client
(TUI, future tools) can send requests (start_run, stop_run, etc.) and
receives a matching response, plus unsolicited status_update events
broadcast to all connected clients roughly once per second.

Owns the hardware executor (single worker -- see hardware.py) and the
background IMU/temperature polling tasks, which run independently of
run state (matching the "hardware status is always live" design).

Run with:
    python -m muon_daemon.server
    python -m muon_daemon.server --socket /tmp/muon_telescope.sock --data-dir /home/muon_telescope/muon_data --config config/default.toml
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shlex
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from muon_daemon import panel, protocol
from muon_daemon.state import DaemonState, DaemonError, RunPhase
from muon_daemon.hardware import HardwareController, HardwareState, HardwareError
from muon_daemon.publisher import InfluxPublisher

log = logging.getLogger("muon_daemon")


class DaemonServer:
    def __init__(self, socket_path: Path, data_dir: Path, cfg: dict | None = None,
                 storage_backend=None, hardware: HardwareController | None = None,
                 publisher: "InfluxPublisher | None" = None) -> None:
        self.socket_path = Path(socket_path)

        if hardware is None:
            # Real deployment: one dedicated worker thread serializes all
            # blocking hardware access (motor motion, I2C reads).
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hw")
            hw_state = HardwareState()
            hardware = HardwareController(cfg, hw_state, self._executor)
        else:
            self._executor = None  # test injected its own hardware, nothing to own/shut down

        self.state = DaemonState(data_dir, hardware=hardware, storage_backend=storage_backend)

        # Publisher is best-effort: if construction fails or InfluxDB is
        # unreachable, it disables itself internally -- the daemon runs
        # regardless. Tests can inject a fake (or None to skip it).
        if publisher is None and cfg is not None:
            publisher = InfluxPublisher(hardware, self.state, cfg=cfg)
        self._publisher = publisher

        self._writers: set[asyncio.StreamWriter] = set()
        self._write_locks: dict[asyncio.StreamWriter, asyncio.Lock] = {}
        self._server: asyncio.base_events.Server | None = None
        self._broadcast_task: asyncio.Task | None = None
        self._imu_task: asyncio.Task | None = None
        self._temp_task: asyncio.Task | None = None
        self._hv_task: asyncio.Task | None = None
        self._env_pub_task: asyncio.Task | None = None
        self._rate_pub_task: asyncio.Task | None = None

        self._last_daq_count = 0
        self._last_daq_time = time.monotonic()
        self._stopped = False

        # On panel. _panel_tick satir kaydirma icin sayac; _panel_notice
        # 2. satiri gecici olarak ezen mesaj ("Shutting down" gibi).
        # shutdown_requested, kapatma butonunun SIGTERM ile ayni yoldan
        # gitmesini saglar: _amain bunu bekler ve server.stop() cagirir.
        self._panel_tick = 0
        self._panel_notice = ""
        self.shutdown_requested = asyncio.Event()
        self._poweroff_after_stop = False

    # ---- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        if self.socket_path.exists():
            self.socket_path.unlink()

        self._server = await asyncio.start_unix_server(self._handle_client, path=str(self.socket_path))
        os.chmod(self.socket_path, 0o600)

        # The IMU is the absolute angle reference now -- there is no
        # homing step, so the step counter starts every boot at 0 and
        # is unknown until this runs once. Best-effort: if the IMU or
        # stepper isn't available yet, the daemon still comes up and a
        # move (or a retry) will seed it later.
        try:
            await self.state.hardware.seed_from_imu()
        except HardwareError as exc:
            log.warning("Could not seed position from IMU at startup: %s", exc)

        # Panelde "calisiyor" ledi. Burada yakiyoruz, karta guc gelince
        # degil -- ledin anlami "yazilim ayakta", "besleme var" degil.
        self.state.hardware.set_active_led(True)

        self._wire_buttons()
        await self._refresh_panel()

        self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        self._imu_task = asyncio.create_task(self.state.hardware.poll_imu_forever())
        self._temp_task = asyncio.create_task(self.state.hardware.poll_temperature_forever())
        self._hv_task = asyncio.create_task(self.state.hardware.poll_hv_forever())

        if self._publisher is not None:
            self._env_pub_task = asyncio.create_task(self._publisher.publish_env_forever())
            self._rate_pub_task = asyncio.create_task(self._publisher.publish_rate_forever())

        log.info("Daemon listening on %s", self.socket_path)

    async def serve_forever(self) -> None:
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        # Idempotent: SIGTERM + SIGINT (or two Ctrl-C's) could call this
        # twice; the second call must be harmless.
        if self._stopped:
            return
        self._stopped = True

        # If a run is still active when the daemon is told to shut down,
        # close it out cleanly (flush DAQ file, mark storage done) so the
        # measurement folder isn't left in a dangling "running" state.
        # A calibration in progress is interrupted via the stop_event so
        # the executor thread can unwind instead of being killed mid-motion.
        if self.state.phase == RunPhase.RUNNING:
            try:
                log.info("Shutdown with active run -- closing it out cleanly")
                self.state.stop_run()
            except Exception:
                log.exception("Error while closing active run during shutdown")
        elif self.state.phase == RunPhase.CALIBRATING:
            try:
                log.info("Shutdown during calibration -- signalling motor stop")
                await self.state.stop_motor()
            except Exception:
                log.exception("Error while stopping motor during shutdown")

        for task in (self._broadcast_task, self._imu_task, self._temp_task,
                     self._hv_task, self._env_pub_task, self._rate_pub_task):
            if task:
                task.cancel()

        # Flush any buffered InfluxDB points before we exit, so the last
        # unflushed batch isn't silently lost on shutdown.
        if self._publisher is not None:
            self._publisher.close()

        for writer in list(self._writers):
            writer.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        # Release the HV serial port and its worker thread.
        self.state.hardware.shutdown()

        if self._poweroff_after_stop:
            self._run_shutdown_command()
        if self._executor:
            self._executor.shutdown(wait=False)
        if self.socket_path.exists():
            self.socket_path.unlink()
        log.info("Daemon stopped cleanly")

    # ---- per-connection handling ------------------------------------------

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        self._write_locks[writer] = asyncio.Lock()
        peer = writer.get_extra_info("peername") or "unix-client"
        log.info("Client connected: %s (total=%d)", peer, len(self._writers))

        try:
            await self._send(writer, protocol.make_event(protocol.EVENT_STATUS_UPDATE, self.state.snapshot()))

            while True:
                line = await reader.readline()
                if not line:
                    break
                # Spawn as a task instead of awaiting directly: a slow
                # command (e.g. calibrate_move, which blocks for seconds
                # on the executor) must not prevent this loop from
                # reading the NEXT line -- otherwise an emergency
                # stop_motor request sent on the same connection would
                # never even be read until the slow command finished,
                # defeating the entire point of an emergency stop.
                asyncio.create_task(self._handle_line(writer, line))
        except (asyncio.IncompleteReadError, ConnectionError, BrokenPipeError):
            # Client went away mid-read/mid-write (closed abruptly). Not an
            # error worth logging -- just fall through to cleanup.
            pass
        finally:
            self._writers.discard(writer)
            self._write_locks.pop(writer, None)
            writer.close()
            log.info("Client disconnected (total=%d)", len(self._writers))

    async def _handle_line(self, writer: asyncio.StreamWriter, line: bytes) -> None:
        try:
            msg = protocol.decode(line)
        except protocol.ProtocolError as exc:
            log.warning("Malformed message ignored: %s", exc)
            return

        if msg.get("type") != "request":
            return

        request_id = msg.get("id", "")
        cmd = msg.get("cmd", "")
        payload = msg.get("payload", {}) or {}

        try:
            result = await self._dispatch(cmd, payload)
            await self._send(writer, protocol.make_response_ok(request_id, result))
        except DaemonError as exc:
            await self._send(writer, protocol.make_response_error(request_id, str(exc)))
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error handling cmd=%s", cmd)
            await self._send(writer, protocol.make_response_error(request_id, f"internal error: {exc}"))

        if cmd in (protocol.CMD_START_RUN, protocol.CMD_STOP_RUN,
                protocol.CMD_CALIBRATE_MOVE, protocol.CMD_CALIBRATE_STEPS):
            await self._broadcast_status()

    async def _dispatch(self, cmd: str, payload: dict) -> dict:
        if cmd == protocol.CMD_GET_STATUS:
            return self.state.snapshot()

        if cmd == protocol.CMD_START_RUN:
            result = self.state.start_run(
                name=payload.get("name", ""),
                kind=payload.get("kind", ""),
                duration_s=payload.get("duration_s"),
                scan=payload.get("scan"),
            )
            self._last_daq_count = 0
            self._last_daq_time = time.monotonic()
            return result

        if cmd == protocol.CMD_STOP_RUN:
            return self.state.stop_run()

        if cmd == protocol.CMD_CALIBRATE_MOVE:
            return await self.state.calibrate_move(angle_deg=float(payload.get("angle_deg", 0.0)))
        if cmd == protocol.CMD_CALIBRATE_STEPS:
            return await self.state.calibrate_steps(n_steps=int(payload.get("steps", 0)))
        if cmd == protocol.CMD_STOP_MOTOR:
            return await self.state.stop_motor()
        if cmd == protocol.CMD_IMU_SAMPLE:
            return await self.state.imu_sample(
                duration_s=float(payload.get("duration_s", 10.0))
            )
        if cmd == protocol.CMD_IMU_SET_CALIBRATION:
            return await self.state.imu_set_calibration(
                offset_deg=float(payload.get("offset_deg", 0.0)),
                ellipse=payload.get("ellipse"),
                points=payload.get("points"),
            )

        raise DaemonError(f"Unknown command: {cmd!r}")

    # ---- broadcast ---------------------------------------------------------

    async def _broadcast_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                self._autostop_if_due()
                self._update_rate()
                await self._broadcast_status()
                await self._refresh_panel()
        except asyncio.CancelledError:
            pass

    def _autostop_if_due(self) -> None:
        """End a fixed-duration run when its time is up. Checked on the
        broadcast tick rather than with a per-run timer, so there is one
        place that ends a run and it cannot be left behind by a restart."""
        try:
            result = self.state.autostop_if_due()
        except Exception:  # noqa: BLE001 -- must never kill the broadcast loop
            log.exception("Error while auto-stopping a finished run")
            return
        if result is not None:
            log.info(
                "Run %s reached its duration -- stopped automatically (%d counts)",
                result["name"], result["final_daq_count"],
            )

    def _update_rate(self) -> None:
        """Recomputed once per broadcast tick (~1Hz), not on every
        snapshot() call, so the reported rate is stable rather than
        noisy from irregular get_status polling."""
        now = time.monotonic()
        count = self.state.hardware.daq_count

        if self.state.phase == RunPhase.RUNNING:
            dt = now - self._last_daq_time
            if dt > 0:
                self.state.rate_hz = (count - self._last_daq_count) / dt
        else:
            self.state.rate_hz = 0.0

        self._last_daq_count = count
        self._last_daq_time = now

    # ---- front panel: display + buttons -----------------------------------

    async def _refresh_panel(self) -> None:
        """Once per tick: what the display should say now."""
        self._panel_tick += 1
        lines = panel.panel_lines(self.state.snapshot(), self._panel_tick, self._panel_notice)
        await self.state.hardware.show_panel(lines)

    def _wire_buttons(self) -> None:
        """Button presses arrive on gpiozero's thread; hand each one to
        the event loop as a task so it runs where everything else does."""
        buttons = self.state.hardware.buttons
        if buttons is None:
            return
        loop = asyncio.get_running_loop()
        actions = {
            "start": self._btn_start,
            "stop": self._btn_stop,
            "reset": self._btn_reset,
            "shutdown": self._btn_shutdown,
        }
        try:
            for name, action in actions.items():
                buttons.on_held(name, lambda a=action: loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(a())))
            log.info("Front panel buttons armed: start/stop %.0fs, reset/shutdown %.0fs hold",
                     buttons.hold_time("start"), buttons.hold_time("reset"))
        except Exception:  # noqa: BLE001 -- the panel must never stop the daemon
            log.warning("Front panel buttons could not be wired; panel buttons inactive",
                        exc_info=True)

    async def _btn_start(self) -> None:
        """Idle -> start a plain, open-ended run with an automatic name.
        During a run the button does nothing: Stop ends a run, Start
        never resumes one -- a resumed run would mean two event files
        for one measurement and a livetime nobody can add up."""
        if self.state.phase != RunPhase.IDLE:
            log.info("Start button ignored: daemon is %s", self.state.phase.value)
            return
        # 15 chars, fits the 16-column display. Two presses in the same
        # second would collide on the folder name, so try a letter suffix
        # before giving up (still 16 chars).
        base = "btn_" + time.strftime("%m%d_%H%M%S")
        for suffix in ("", "b", "c", "d"):
            name = base + suffix
            try:
                self.state.start_run(name=name, kind="measurement", duration_s=None)
            except FileExistsError:
                continue
            except Exception as exc:  # noqa: BLE001
                log.error("Start button: could not start a run: %s", exc)
                break
            self._last_daq_count = 0
            self._last_daq_time = time.monotonic()
            log.info("Start button: run %s started", name)
            break
        else:
            log.error("Start button: could not find a free run name for %s", base)
        await self._broadcast_status()
        await self._refresh_panel()

    async def _btn_stop(self) -> None:
        """End the run normally (status done)."""
        if self.state.phase != RunPhase.RUNNING:
            log.info("Stop button ignored: no run active")
            return
        result = self.state.stop_run(status="done")
        log.info("Stop button: run %s stopped (%d counts)",
                 result["name"], result["final_daq_count"])
        await self._broadcast_status()
        await self._refresh_panel()

    async def _btn_reset(self) -> None:
        """Abort whatever is happening and return to idle: a run is closed
        with status 'aborted' (so the data folder says it was thrown away
        on purpose, not finished), a motor move is stopped."""
        if self.state.phase == RunPhase.RUNNING:
            result = self.state.stop_run(status="aborted")
            log.info("Reset button: run %s aborted", result["name"])
        elif self.state.phase == RunPhase.CALIBRATING:
            await self.state.stop_motor()
            log.info("Reset button: motor stopped")
        else:
            log.info("Reset button: already idle")
        await self._broadcast_status()
        await self._refresh_panel()

    async def _btn_shutdown(self) -> None:
        """Close the run, stop the daemon cleanly, then power the Pi off.
        Goes through the same path as SIGTERM, so nothing is left half
        done; the poweroff command runs last, after the hardware is
        released."""
        self._panel_notice = "Shutting down..."
        await self._refresh_panel()
        log.info("Shutdown button: stopping daemon, then powering off")
        self._poweroff_after_stop = True
        self.shutdown_requested.set()

    def _run_shutdown_command(self) -> None:
        cmd = (self.state.hardware.cfg or {}).get("buttons", {}).get("shutdown_command", "")
        if not cmd:
            return
        try:
            subprocess.run(shlex.split(cmd), check=True, timeout=10)
        except Exception as exc:  # noqa: BLE001 -- sudoers rule missing, most likely
            log.error("Shutdown command failed (%s): %s -- see [buttons] in the config", cmd, exc)

    async def _broadcast_status(self) -> None:
        event = protocol.make_event(protocol.EVENT_STATUS_UPDATE, self.state.snapshot())
        for writer in list(self._writers):
            await self._send(writer, event)

    async def _send(self, writer: asyncio.StreamWriter, message: dict) -> None:
        lock = self._write_locks.get(writer)
        try:
            if lock is not None:
                async with lock:
                    writer.write(protocol.encode(message))
                    await writer.drain()
            else:
                writer.write(protocol.encode(message))
                await writer.drain()
        except (ConnectionError, BrokenPipeError, OSError):
            # Peer closed; drop this writer so we stop trying to reach it.
            self._writers.discard(writer)


def _parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Muon telescope control daemon")
    ap.add_argument("--socket", default="/tmp/muon_telescope.sock")
    ap.add_argument("--data-dir", default="/home/muon_telescope/muon_data")
    ap.add_argument("--config", default="config/default.toml")
    ap.add_argument("--log-level", default="INFO")
    return ap.parse_args(argv)


async def _amain(argv=None) -> None:
    args = _parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from muon_control.config import load_config
    cfg = load_config(args.config)

    server = DaemonServer(socket_path=Path(args.socket), data_dir=Path(args.data_dir), cfg=cfg)
    await server.start()

    # Graceful shutdown: systemd sends SIGTERM on `systemctl stop`, and
    # Ctrl-C sends SIGINT. Both set the same event, which unblocks the
    # wait below and lets us run server.stop() cleanly instead of dying
    # mid-operation (leaving a stale socket file and a run stuck in the
    # "running" state).
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _request_shutdown(signame: str) -> None:
        log.info("Received %s -- shutting down", signame)
        shutdown_event.set()

    for signame in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(
                getattr(signal, signame),
                lambda s=signame: _request_shutdown(s),
            )
        except NotImplementedError:
            # add_signal_handler isn't available on some platforms
            # (e.g. Windows); the KeyboardInterrupt fallback in main()
            # covers SIGINT there. On Linux/Pi this branch never runs.
            pass

    serve_task = asyncio.create_task(server.serve_forever())
    try:
        # SIGTERM/SIGINT ya da on paneldeki kapatma butonu -- ikisi de
        # ayni temiz kapanisi tetikler.
        await asyncio.wait(
            [asyncio.create_task(shutdown_event.wait()),
             asyncio.create_task(server.shutdown_requested.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        serve_task.cancel()
        await server.stop()


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        # Fallback if the SIGINT signal handler didn't get installed
        # (see the NotImplementedError branch in _amain). On Linux the
        # handler normally catches Ctrl-C first and this never fires.
        pass


if __name__ == "__main__":
    main()