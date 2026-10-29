---
title: Architecture
parent: Software
nav_order: 2
---

# Architecture

```
          SSH                      Unix socket
 operator ───► muon_telescope_manager ──────────► muon-daemon ──► drivers ──► hardware
               (Textual TUI)        line JSON       (asyncio)
                                                       │
                                                       ├──► data_dir/<run>/*.txt   (the record)
                                                       └──► InfluxDB ──► Grafana   (a view)
```

## Two processes

**The daemon** (`muon_daemon.server`) is the only process that touches hardware and the only one that writes data. It runs as a systemd service, holds the run state, and keeps going whether or not anyone is watching.

**The TUI** (`muon_tui.app`, installed as `muon_telescope_manager`) is a client. It connects to the socket, sends commands, and renders the status the daemon broadcasts. Closing it changes nothing in the daemon. Several clients may connect at once.

## Wire protocol

Line-delimited JSON over `/tmp/muon_telescope.sock` (`muon_daemon/protocol.py`):

```json
{"type": "request",  "id": "7", "cmd": "start_run", "payload": {"name": "p0_350mv", "kind": "measurement", "duration_s": 900}}
{"type": "response", "id": "7", "ok": true, "result": {...}}
{"type": "event", "event": "status_update", "payload": {...snapshot...}}
```

| command | does |
|---|---|
| `get_status` | one snapshot |
| `start_run` | a plain run (`duration_s` or open-ended) or, with a `scan` object, a hemisphere scan |
| `stop_run` | end the run, close the files |
| `calibrate_move` | move to an angle (idle only) |
| `calibrate_steps` | move a raw number of steps (idle only) |
| `stop_motor` | emergency stop — no state check, always accepted |
| `imu_sample` | average the IMU for one calibration point |
| `imu_set_calibration` | store offset and ellipse, write the calibration record |

Every second the daemon broadcasts a `status_update` event to all clients: run phase, counts, rate, angle, IMU, temperature, HV, scan progress. On connect a client gets one immediately.

The daemon has three phases — `idle`, `running`, `calibrating` — and refuses commands that do not fit (a move during a run, a run during a move). `stop_motor` is the one exception: it is always accepted.

## Inside the daemon

`DaemonState` (`state.py`) decides *whether* something may happen: the phase rules, run bookkeeping, scan sequencing, auto-stop of timed runs. `HardwareController` (`hardware.py`) decides *how*: it owns the drivers and the worker threads.

Blocking hardware work runs off the event loop, on one worker per physical resource, so a slow device never stalls another:

| worker | serves | why its own |
|---|---|---|
| motion | stepper | a move takes seconds and must not block sensor reads |
| sensor | IMU, DHT11, display | one I2C bus; serialising its users keeps transactions whole |
| HV | N1470 serial | reading four channels is ~24 round-trips at 9600 baud |

**The DAQ is on none of them.** Each channel's counter is a gpiozero edge callback on gpiozero's own thread: timestamp first, increment, write one line. There is nothing to queue. During a scan the counters are closed before a move and reopened after it, so motion and counting never overlap by construction.

## Runs and scans

A **plain run** opens one event file per channel and counts until stopped or until `duration_s` elapses. The auto-stop check lives in the once-a-second broadcast tick.

A **scan** walks a list of angles. At each: move, settle, open fresh counters, count for `acquisition_s`, close them, append one row to `summary.txt`. The angle written into the row and the filename is the one the IMU measured after the move, not the one that was requested (see [Motion](motion.html)). A scan ends itself after its last angle; a clock never cuts one short mid-point.

## Where the data goes

`data_dir/<run name>/` — one folder per run, never overwritten (a reused name is refused). `data_dir/latest.txt` points at the current or most recent run with its status. Formats are in [Data formats](data-format.html).

InfluxDB publishing (`publisher.py`) is a monitoring layer on top. Points are batched in memory and flushed every 30 s, which is also the Grafana latency. If the InfluxDB client library is missing or the server is unreachable, publishing disables itself and the run is unaffected.

## Legacy CLI path

`scripts/run_scan.py` with `muon_control/scan.py` runs a scan without the daemon, writing the same file formats. It predates the daemon and is kept for bench use.
