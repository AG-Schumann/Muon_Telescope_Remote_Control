"""
muon_daemon.protocol — Wire format between the daemon and its clients (TUI, etc.)

Transport: a Unix domain socket, line-delimited JSON (one JSON object per
line, terminated by \\n). This is purely an in-memory IPC channel -- no
disk I/O happens here regardless of how often messages are sent.

Message shapes
--------------
Request  (client -> daemon):
    {"type": "request", "id": "<client-chosen-id>", "cmd": "<command>", "payload": {...}}

Response (daemon -> client, matches a request by id):
    {"type": "response", "id": "<same-id>", "ok": true,  "result": {...}}
    {"type": "response", "id": "<same-id>", "ok": false, "error": "<message>"}

Event (daemon -> client, unsolicited, no id):
    {"type": "event", "event": "<event-name>", "payload": {...}}
"""
from __future__ import annotations

import json
from typing import Any


# ---- command names (client -> daemon) --------------------------------------
CMD_START_RUN = "start_run"
CMD_STOP_RUN = "stop_run"
CMD_CALIBRATE_MOVE = "calibrate_move"
CMD_STOP_MOTOR = "stop_motor"
CMD_GET_STATUS = "get_status"
CMD_CALIBRATE_STEPS = "calibrate_steps"
CMD_IMU_SAMPLE     = "imu_sample"
CMD_IMU_SET_CALIBRATION = "imu_set_calibration"

# ---- event names (daemon -> client, unsolicited) ---------------------------
EVENT_STATUS_UPDATE = "status_update"


class ProtocolError(Exception):
    """Raised when an incoming line cannot be parsed as a valid message."""


def encode(message: dict[str, Any]) -> bytes:
    """Serialize a message dict to a single newline-terminated JSON line."""
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


def decode(line: bytes) -> dict[str, Any]:
    """Parse one line of input into a message dict. Raises ProtocolError on failure."""
    try:
        msg = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"invalid JSON line: {exc}") from exc

    if not isinstance(msg, dict) or "type" not in msg:
        raise ProtocolError("message must be a JSON object with a 'type' field")

    return msg


def make_request(request_id: str, cmd: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"type": "request", "id": request_id, "cmd": cmd, "payload": payload or {}}


def make_response_ok(request_id: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"type": "response", "id": request_id, "ok": True, "result": result or {}}


def make_response_error(request_id: str, error: str) -> dict[str, Any]:
    return {"type": "response", "id": request_id, "ok": False, "error": error}


def make_event(event_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "event", "event": event_name, "payload": payload}