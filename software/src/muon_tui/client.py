"""
muon_tui.client — asyncio Unix socket client connecting to muon_daemon.

Runs as a background asyncio task inside the TUI's own event loop --
Textual apps are already asyncio-based, so no threads are needed here.
Incoming status_update events are forwarded to a callback the App
registers; outgoing commands are sent via request() and await a
matching response by id.

Handles disconnection: if the daemon isn't running yet (or goes away),
connect_with_retry() keeps trying with a capped exponential backoff
instead of giving up.
"""
from __future__ import annotations

import asyncio
import itertools
from typing import Callable

from muon_daemon import protocol


class DaemonRequestError(Exception):
    """Raised when the daemon responds with ok=false to a request(),
    or when the request cannot be sent/answered at all (not connected,
    timeout)."""


class DaemonClient:
    def __init__(self, socket_path: str, on_event: Callable[[dict], None] | None = None) -> None:
        self.socket_path = socket_path
        self._on_event = on_event
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._id_counter = itertools.count(1)
        self._listen_task: asyncio.Task | None = None
        self.connected = False

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_unix_connection(self.socket_path)
        self.connected = True
        self._listen_task = asyncio.create_task(self._listen())

    async def connect_with_retry(
        self,
        on_connected: Callable[[], None] | None = None,
        on_disconnected: Callable[[], None] | None = None,
        max_backoff_s: float = 10.0,
    ) -> None:
        """Keeps (re)connecting forever. Meant to be run as a background task."""
        backoff = 1.0
        while True:
            try:
                await self.connect()
                backoff = 1.0
                if on_connected:
                    on_connected()
                # Block here until the connection drops (listen task exits).
                assert self._listen_task is not None
                await self._listen_task
            except (ConnectionError, FileNotFoundError, OSError):
                pass
            finally:
                self.connected = False
                if on_disconnected:
                    on_disconnected()

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff_s)

    async def close(self) -> None:
        if self._listen_task:
            self._listen_task.cancel()
        if self._writer:
            self._writer.close()
        self.connected = False

    async def request(self, cmd: str, payload: dict | None = None, timeout: float = 5.0) -> dict:
        if not self.connected or self._writer is None:
            raise DaemonRequestError("Not connected to daemon")

        req_id = str(next(self._id_counter))
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut

        try:
            self._writer.write(protocol.encode(protocol.make_request(req_id, cmd, payload)))
            await self._writer.drain()
            response = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise DaemonRequestError(f"Daemon did not respond within {timeout}s") from exc
        except (ConnectionError, OSError) as exc:
            raise DaemonRequestError(f"Connection error: {exc}") from exc
        finally:
            self._pending.pop(req_id, None)

        if not response.get("ok", False):
            raise DaemonRequestError(response.get("error", "unknown error"))
        return response.get("result", {})

    async def _listen(self) -> None:
        assert self._reader is not None
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break  # daemon closed the connection
                try:
                    msg = protocol.decode(line)
                except protocol.ProtocolError:
                    continue

                if msg["type"] == "response":
                    fut = self._pending.get(msg["id"])
                    if fut and not fut.done():
                        fut.set_result(msg)
                elif msg["type"] == "event":
                    if self._on_event:
                        self._on_event(msg["payload"])
        except asyncio.CancelledError:
            pass
        finally:
            self.connected = False