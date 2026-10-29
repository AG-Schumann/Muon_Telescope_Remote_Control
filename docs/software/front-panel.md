---
title: Front panel
parent: Software
nav_order: 7
---

# Front panel

Two LEDs, a 4 × 16 character display and four buttons, all driven by the daemon. If any of them is missing or faulty the daemon logs it and carries on: a dead panel light must never stop a measurement.

## LEDs (J12)

| LED | GPIO | meaning |
|---|---|---|
| ACTIVE | 4 | the daemon is running. Lit in `server.start()`, not when the board powers up — so it means "the software is up", which is the question worth answering from across the room. Off on shutdown. |
| MEAS | 24 | a measurement is running. Lit at the end of a successful `start_run`, cleared in `stop_run`. |

Polarity is a config key per LED (`[leds]`), because the schematic can be read either way; the bench says both are active-high.

## Display (EA DOGS164W-A)

Updated **once per second** from the daemon's broadcast tick — never per event, since writing I2C from the DAQ callback is exactly what the counting must stay clear of. Only lines that changed are rewritten. Writes share the sensor worker with the IMU, which is on the same I2C bus.

```
idle                        running
|Muon Telescope  |          |btn_1028_143522 |
|No active run   |          |RUN  00:12:34   |
| +30.0deg 21.5C |          |CH0        1,234|
|                |          |CH1           56|
```

A run name longer than 16 characters scrolls two columns per second. An I2C error (loose cable, bus glitch) is caught; the display re-initialises on the next write and the run is unaffected.

`scripts/display_test.py` drives the display alone, for wiring checks. Stop the daemon first.

## Buttons (J10)

All four are "hold to act", so a brush against the panel does nothing. Times are in `[buttons]`.

| button | hold | action |
|---|---|---|
| **Start** | 1 s | idle only: starts a plain, open-ended run named `btn_MMDD_HHMMSS`. During a run it does nothing. |
| **Stop** | 1 s | ends the run normally, `status = done`. |
| **Reset** | 5 s | aborts whatever is happening: a run is closed with `status = aborted` (the folder says it was thrown away on purpose), a motor move is stopped. Returns to idle. |
| **Shutdown** | 5 s | shows `Shutting down...`, closes any open run, turns the LEDs off, clears the display, stops the daemon, then runs `shutdown_command` (`sudo -n /sbin/poweroff`). Needs the sudoers rule from [Installation](install.html); without it the Pi stays on and the daemon has still stopped cleanly. |

Stop ends a run and Start never resumes one. Resuming would mean two event files for one measurement and a livetime nobody can add up; a new run is always the honest choice.

Button presses arrive on gpiozero's thread and are handed to the daemon's event loop, so they go through exactly the same code as the TUI's commands.
