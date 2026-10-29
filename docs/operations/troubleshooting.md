---
title: Troubleshooting
parent: Operations
nav_order: 4
---

# Troubleshooting

First, always:

```bash
sudo systemctl status muon-daemon
sudo journalctl -u muon-daemon -n 100 --no-pager
```

The daemon logs every driver it could not start as a warning, every client connect/disconnect, every run start, stop and auto-stop, and every front-panel button press.

## The service restarts in a loop, exit status 1, under a second

The daemon died before it reached any hardware — almost always the config. The journal shows a Python traceback; the two common ones:

- `TypeError: unhashable type: 'list'` or `pin cakismasi` from `config.py` — a pin is listed twice in `[gpio]`, or a list-valued key was edited into something the loader does not expect. Fix the config.
- `FileNotFoundError` on the config — the unit's `WorkingDirectory` and `--config` path disagree with where the checkout is.

The config path in the unit is relative (`config/default.toml`); it only resolves because of `WorkingDirectory`.

## "Motor turned the wrong way"

The move aborted because the IMU saw the telescope go the opposite way to what was commanded. The DIR wiring and `[stepper] invert_direction` disagree; flip the flag. Nothing in calibration can fix this — it fits an offset, never a sign.

## A move stops short, or a scan "didn't reach 90°"

It is not corrected on purpose. The angle written into the data is the IMU's measurement, so a point taken at 88.8° is labelled 88.8°. See [Motion](../software/motion.html). If the shortfall is large (several degrees) check `steps_per_deg` inputs: the TB6600 DIP switches must match `[stepper] microstep`.

## The TUI shows stale numbers after an SSH reconnect

Scenario: an SSH session drops without the TUI being closed; sshd does not notice; the old TUI stops reading the socket; the daemon's once-a-second broadcast blocks on that dead client — and with it the auto-stop of timed runs. A fresh TUI gets one snapshot on connect, then freezes. The data files are unaffected (livetime is measured), but a timed run can overrun until the stale session finally dies.

What to do: find and kill the orphaned TUI (`ps aux | grep muon_telescope_manager`), or just wait for the SSH timeout; restart your TUI afterwards. Run the TUI inside [tmux](tmux.html) so a dropped SSH session does not leave an orphan in the first place. This coupling is a known limitation of the current daemon.

## Front-panel LEDs do not light

In order:

1. `sudo systemctl restart muon-daemon` — a pull does not restart the service.
2. `sudo journalctl -u muon-daemon | grep -i led` — `Front panel LEDs unavailable: …` names the cause.
3. `grep dtoverlay /boot/firmware/config.txt` — `w1-gpio` claims GPIO4 (the ACTIVE LED) for the kernel; remove it.
4. One LED lit when it should be off, or the reverse: flip its key in `[leds]`.

## The display is blank or shows garbage

`i2cdetect -y 1` must show `3c`. Then `python scripts/display_test.py` with the daemon stopped. Garbage after a working start usually means an interrupted I2C transaction; the daemon re-initialises the display on the next write by itself.

## `hv_control.py off` is refused

The N1470 front panel is in LOCAL mode, which is the default on this build and is deliberate. Switch the panel to REMOTE, or use the panel's own off switch — if you are at the crate it is faster anyway.

## The Grafana rate panel is empty

Normal when no run is active: the rate publisher only writes during a run. Pick a past run from the `run` dropdown and set the time range to its dates. If a *running* run shows nothing after a minute or two, check the "Data age" panel and the journal for `InfluxDB`.

## Starting a run says the name already exists

Run folders are never overwritten. Choose another name; the folder is the record.
