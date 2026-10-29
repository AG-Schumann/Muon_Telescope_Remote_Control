"""
muon_control.drivers.hv — Layer 1: CAEN N1470 high-voltage supply

The N1470 is connected over USB and appears as an FTDI serial port.

What this driver will and will not do
------------------------------------
It reads everything, and the only thing it can write is a channel
on/off. It does not set voltages or currents and does not clear trips:
those decide how much energy goes into a PMT, and a remote interface
makes them far too easy to get wrong by accident. Setting values stays
on the front panel.

Nothing in the daemon or the TUI calls the on/off methods -- the
running system is read-only. They exist for scripts/hv_control.py, an
operator-run tool for switching the HV off over SSH when something is
wrong. Keeping the write out of the always-on path and behind a command
someone has to type deliberately is the whole point of the split.

REMOTE mode is required to write
--------------------------------
The N1470 refuses SET commands while its front panel is in LOCAL, and
reports its mode as BDCTR. This build reads LOCAL, so on/off is refused
until someone walks over and switches the panel -- the panel is the real
lockout. set_channel() checks the mode first so the refusal says that
plainly instead of surfacing a bare protocol error.

Protocol
--------
ASCII, one command per line, terminated CR LF:

    $BD:00,CMD:MON,PAR:BDNAME          -> #BD:00,CMD:OK,VAL:N1470
    $BD:00,CMD:MON,CH:0,PAR:VMON       -> #BD:00,CMD:OK,VAL:1339.2

Every query is a separate round-trip: reading all four channels is 24
of them, which at 9600 baud takes on the order of a second. That is why
the daemon polls this on its own thread and at a slow cadence rather
than on the hardware executor shared with the motor.

Units: VSET/VMON in volts, ISET/IMON in microamps.
"""

from __future__ import annotations

import time

import serial


# Channel status is a bit field returned as a decimal number. Bit 0 is
# confirmed against this build: a channel sitting at its set voltage
# reads STAT:00001. The rest follow the N1470 manual -- if a flag ever
# looks wrong, this table is the first thing to check against it.
_STATUS_BITS = [
    (0, "ON"),
    (1, "RAMP_UP"),
    (2, "RAMP_DOWN"),
    (3, "OVER_CURRENT"),
    (4, "OVER_VOLTAGE"),
    (5, "UNDER_VOLTAGE"),
    (6, "MAX_V"),
    (7, "TRIP"),
    (8, "OVER_POWER"),
    (9, "OVER_TEMP"),
    (10, "DISABLED"),
    (11, "KILL"),
    (12, "INTERLOCK"),
    (13, "NOT_CALIBRATED"),
]

# Flags that mean something is wrong, as opposed to merely describing
# what the channel is doing. The TUI colours on this.
FAULT_FLAGS = {
    "OVER_CURRENT", "OVER_VOLTAGE", "UNDER_VOLTAGE", "TRIP",
    "OVER_POWER", "OVER_TEMP", "KILL", "INTERLOCK", "NOT_CALIBRATED",
}


def decode_status(status_raw: int) -> list[str]:
    """Turn the STAT bit field into readable flags."""
    return [name for bit, name in _STATUS_BITS if status_raw & (1 << bit)]


class HVError(RuntimeError):
    """Raised when communication with the HV supply fails."""


class HVSupply:
    def __init__(self, port: str, baudrate: int = 9600, board: int = 0,
                 timeout_s: float = 2.0) -> None:
        self.port = port
        self.baudrate = baudrate
        self.board = board
        self.timeout_s = timeout_s
        self._serial = None
        # Filled on the first board_info()/read_all_channels(). The
        # channel count cannot change while the crate is powered, so
        # asking for it on every poll would just be one more round-trip
        # on an already slow link.
        self._n_channels: int | None = None

    @classmethod
    def from_config(cls, cfg: dict) -> "HVSupply":
        hv = cfg["hv"]
        return cls(
            port=hv["port"],
            baudrate=hv.get("baudrate", 9600),
            board=hv.get("board", 0),
            timeout_s=hv.get("timeout_s", 2.0),
        )

    # --- connection --------------------------------------------------------

    def connect(self) -> None:
        if self.connected:
            return

        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                xonxoff=True,
                timeout=self.timeout_s,
            )
        except serial.SerialException as exc:
            self._serial = None
            raise HVError(f"Could not open HV serial port: {exc}") from exc

        # The N1470 can ignore the first command right after the port
        # opens, so give it a moment before anything is asked of it.
        time.sleep(0.5)

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass  # already gone (unplugged); nothing useful left to do
            self._serial = None
        self._n_channels = None

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    # --- low-level communication -------------------------------------------

    def _ask(self, command: str) -> str:
        if not self.connected:
            raise HVError("HV supply is not connected")

        message = (command + "\r\n").encode("ascii")

        try:
            self._serial.reset_input_buffer()
            self._serial.write(message)
            self._serial.flush()
            response = self._serial.readline().decode("ascii", errors="replace").strip()

            # Retry once: the board occasionally drops a command rather
            # than answering it, and one retry is cheaper than failing a
            # whole poll cycle over a single lost line.
            if not response:
                time.sleep(0.2)
                self._serial.write(message)
                self._serial.flush()
                response = self._serial.readline().decode("ascii", errors="replace").strip()
        except serial.SerialException as exc:
            # The USB cable went away mid-conversation. Drop the handle so
            # the next poll reconnects instead of reusing a dead port.
            self.close()
            raise HVError(f"HV serial link failed: {exc}") from exc

        if not response:
            raise HVError(f"No response to command: {command}")
        if "CMD:OK" not in response:
            raise HVError(f"HV returned an error: {response}")

        return response

    @staticmethod
    def _value_from_response(response: str) -> str:
        marker = "VAL:"
        if marker not in response:
            raise HVError(f"Response has no value: {response}")
        return response.split(marker, 1)[1]

    def _read_board_parameter(self, parameter: str) -> str:
        return self._value_from_response(
            self._ask(f"$BD:{self.board:02d},CMD:MON,PAR:{parameter}")
        )

    def _read_channel_parameter(self, channel: int, parameter: str) -> str:
        return self._value_from_response(
            self._ask(f"$BD:{self.board:02d},CMD:MON,CH:{channel},PAR:{parameter}")
        )

    # --- board information -------------------------------------------------

    def channel_count(self) -> int:
        if self._n_channels is None:
            self._n_channels = int(self._read_board_parameter("BDNCH"))
        return self._n_channels

    def board_info(self) -> dict:
        info = {
            "model": self._read_board_parameter("BDNAME"),
            "channels": self.channel_count(),
            "firmware": self._read_board_parameter("BDFREL"),
            "serial_number": self._read_board_parameter("BDSNUM"),
            "control": self._read_board_parameter("BDCTR"),
            "interlock": self._read_board_parameter("BDILK"),
            "interlock_mode": self._read_board_parameter("BDILKM"),
            "alarm": int(self._read_board_parameter("BDALARM")),
        }
        return info

    def read_board_status(self) -> dict:
        """The two board-level things that change while running.

        The rest of board_info() is fixed for the life of the connection,
        so a polling loop only needs these -- two round-trips instead of
        eight on a link where every one of them costs real time.
        """
        return {
            "alarm": int(self._read_board_parameter("BDALARM")),
            "interlock": self._read_board_parameter("BDILK"),
        }

    def control_mode(self) -> str:
        """LOCAL or REMOTE. SET commands only work in REMOTE."""
        return self._read_board_parameter("BDCTR").strip().upper()

    # --- the one write: channel on/off (see the module docstring) ----------

    def set_channel(self, channel: int, on: bool) -> None:
        """Switch one channel on or off.

        Not instant: the supply ramps at its configured RUP/RDW rate, so
        the channel is still at voltage for tens of seconds after an off.
        Anything that needs the tubes actually cold has to watch VMON --
        see wait_until_below().
        """
        mode = self.control_mode()
        if mode != "REMOTE":
            raise HVError(
                f"HV is in {mode} mode and will refuse to switch channels. "
                f"Set the front panel to REMOTE first."
            )

        parameter = "ON" if on else "OFF"
        self._ask(f"$BD:{self.board:02d},CMD:SET,CH:{channel},PAR:{parameter}")

    def set_all_channels(self, on: bool) -> None:
        """Switch every channel. Looped rather than using a broadcast
        channel number, so a board that refuses one channel still reports
        which one."""
        for channel in range(self.channel_count()):
            self.set_channel(channel, on)

    def wait_until_below(self, volts: float, timeout_s: float = 180.0,
                         poll_s: float = 2.0, on_poll=None) -> bool:
        """Wait for every channel to ramp below `volts`. True if they did.

        Switching off only *starts* the ramp down, so the only honest
        confirmation that the HV is actually off is watching the measured
        voltage fall. on_poll, if given, is called with the channel list
        each cycle so a caller can show progress.
        """
        deadline = time.monotonic() + timeout_s
        while True:
            channels = self.read_all_channels()
            if on_poll is not None:
                on_poll(channels)
            if all(ch["vmon"] < volts for ch in channels):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll_s)

    # --- channel monitoring ------------------------------------------------

    def read_channel(self, channel: int) -> dict:
        status_raw = int(self._read_channel_parameter(channel, "STAT"))
        flags = decode_status(status_raw)
        return {
            "channel": channel,
            "vset": float(self._read_channel_parameter(channel, "VSET")),
            "vmon": float(self._read_channel_parameter(channel, "VMON")),
            "iset": float(self._read_channel_parameter(channel, "ISET")),
            "imon": float(self._read_channel_parameter(channel, "IMON")),
            "polarity": self._read_channel_parameter(channel, "POL"),
            "status_raw": status_raw,
            "flags": flags,
            "on": "ON" in flags,
            "faults": [f for f in flags if f in FAULT_FLAGS],
        }

    def read_all_channels(self) -> list[dict]:
        return [self.read_channel(ch) for ch in range(self.channel_count())]

    # --- context manager ---------------------------------------------------

    def __enter__(self) -> "HVSupply":
        self.connect()
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False
