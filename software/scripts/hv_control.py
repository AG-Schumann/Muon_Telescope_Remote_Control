#!/usr/bin/env python3
"""
scripts/hv_control.py — switch the CAEN N1470 HV off (or on) from a shell

The emergency path. The daemon and the TUI never write to the HV: they
only read it. Turning the supply off is a decision someone makes, so it
lives here, in a command that has to be typed, rather than behind a
button that can be pressed by accident.

    python3 scripts/hv_control.py                # show what the HV is doing
    python3 scripts/hv_control.py off            # switch every channel off
    python3 scripts/hv_control.py off --wait     # ... and watch it ramp down
    python3 scripts/hv_control.py on --yes       # switch back on (needs --yes)

Two things worth knowing before you need this at 3am:

REMOTE mode.  The N1470 refuses every SET command while its front panel
is in LOCAL, and this build sits in LOCAL by default. That is deliberate
-- the panel is the real lockout -- but it means `off` will be refused
until someone switches the panel to REMOTE. If you are standing at the
crate anyway, the panel's own off switch is faster than this script.

The daemon holds the same port.  It polls the HV every 15 s over the
same serial line, so the two can interleave. A command still reaches the
board, but a reply can land in the wrong process. That is why `off`
never trusts the reply alone: it reads the voltages back and tells you
what they actually are. If you have the time, stopping the daemon first
(`sudo systemctl stop muon-daemon`) makes the exchange clean.

Run it from the 'software/' directory.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from muon_control.config import load_config
from muon_control.drivers.hv import HVSupply, HVError

# Below this the tubes are effectively cold. Not zero: the monitor reads
# a few volts of offset even with the channel off.
OFF_THRESHOLD_V = 20.0

DAEMON_SOCKET = Path("/tmp/muon_telescope.sock")


def print_channels(channels: list[dict]) -> None:
    for ch in channels:
        flags = ",".join(ch["flags"]) or "off"
        print(
            f"  CH{ch['channel']}  {ch['vmon']:7.1f} V "
            f"(set {ch['vset']:7.1f})  {ch['imon']:7.1f} uA   {flags}"
        )


def show_status(hv: HVSupply) -> None:
    board = hv.board_info()
    print(f"\n{board['model']}  fw {board['firmware']}  s/n {board['serial_number']}")
    print(f"  control mode : {board['control']}"
          + ("" if board["control"] == "REMOTE" else "   <- SET commands are refused in this mode"))
    print(f"  interlock    : {board['interlock']}   (NO means nothing is tripped)")
    print(f"  alarm        : {board['alarm']}")
    print()
    print_channels(hv.read_all_channels())
    print()


def warn_if_daemon_running() -> None:
    if DAEMON_SOCKET.exists():
        print(
            f"note: the daemon looks like it is running ({DAEMON_SOCKET}) and polls\n"
            f"      this same serial port. Commands still get through, but replies\n"
            f"      can cross; the voltages read back below are the real answer.\n",
            file=sys.stderr,
        )


def do_switch(hv: HVSupply, on: bool, wait: bool) -> int:
    warn_if_daemon_running()

    action = "ON" if on else "OFF"
    print(f"Switching all channels {action} ...")
    try:
        hv.set_all_channels(on)
    except HVError as exc:
        print(f"\nRefused: {exc}", file=sys.stderr)
        return 2

    print(f"Command accepted. The supply ramps at its own rate.\n")

    if not wait:
        print_channels(hv.read_all_channels())
        if not on:
            print("\nRe-run with --wait, or just re-run this script, to confirm "
                  "the voltage has actually come down.")
        return 0

    if on:
        # Nothing useful to wait for: "ramped up" is not a safety
        # condition and the operator is watching anyway.
        print_channels(hv.read_all_channels())
        return 0

    print(f"Waiting for every channel to fall below {OFF_THRESHOLD_V:.0f} V ...\n")

    def show(channels: list[dict]) -> None:
        highest = max(ch["vmon"] for ch in channels)
        print(f"  highest channel: {highest:7.1f} V")

    reached = hv.wait_until_below(OFF_THRESHOLD_V, on_poll=show)
    print()
    print_channels(hv.read_all_channels())

    if not reached:
        print(
            "\nTIMED OUT: the channels have not come down. Treat the HV as LIVE "
            "and use the front panel.",
            file=sys.stderr,
        )
        return 1

    print(f"\nAll channels are below {OFF_THRESHOLD_V:.0f} V. The HV is off.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Read the CAEN N1470, or switch its channels off/on.",
        epilog="The daemon and TUI never write to the HV; this script is the only writer.",
    )
    ap.add_argument("action", nargs="?", default="status",
                    choices=["status", "off", "on"],
                    help="default: status")
    ap.add_argument("--config", default="config/default.toml")
    ap.add_argument("--wait", action="store_true",
                    help="after 'off', watch the voltages until they are actually down")
    ap.add_argument("--yes", action="store_true",
                    help="required for 'on' -- switching HV on is not something to do by accident")
    args = ap.parse_args(argv)

    if args.action == "on" and not args.yes:
        print(
            "Refusing to switch the HV ON without --yes.\n"
            "Check that the detector is closed and nobody is working on it, then:\n"
            "    python3 scripts/hv_control.py on --yes",
            file=sys.stderr,
        )
        return 2

    try:
        cfg = load_config(args.config)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not read {args.config}: {exc}", file=sys.stderr)
        return 2

    try:
        hv = HVSupply.from_config(cfg)
    except KeyError:
        print("The config has no [hv] section -- nothing to talk to.", file=sys.stderr)
        return 2

    try:
        with hv:
            if args.action == "status":
                show_status(hv)
                return 0
            return do_switch(hv, on=(args.action == "on"), wait=args.wait)
    except HVError as exc:
        print(f"HV error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted. The last command sent still stands -- "
              "re-run with no arguments to see where the HV actually is.",
              file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
