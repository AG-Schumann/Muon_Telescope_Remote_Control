---
title: Configuration
parent: Software
nav_order: 3
---

# Configuration

One file, `software/config/default.toml`, passed to the daemon with `--config`. There is no layering or override file: edit this one (it is version-controlled; the Pi's copy is the deployed one). The loader also derives `stepper.steps_per_deg` and checks the GPIO table for pin collisions before anything touches hardware.

## `[gpio]`

The pin map; see [Control board](../hardware/control-board.html) for the wiring behind each entry. `daq_signals` is a list in channel order (`[23, 18]` = CH0 on GPIO23, CH1 on GPIO18); a single `daq_signal = N` is still accepted for old configs. `display_rst = 0` means "no reset pin".

## `[stepper]`

| key | default | meaning |
|---|---|---|
| `steps_per_rev` | 200 | motor, 1.8°/step |
| `microstep` | 8 | TB6600 DIP setting; must match the driver |
| `gear_ratio` | 100.0 | planetary gearbox |
| `max_rate_hz` | 100.0 | step rate; conservative |
| `invert_direction` | true | which DIR level means "increasing angle". A wiring fact. If a move goes the wrong way the daemon stops and says so; flip this. |
| `approach_margin_deg` | 1.0 | how far past a target to swing when arriving from the wrong side; 0 disables |
| `approach_from` | "above" | the side every angle is reached from. Decided by gravity: see [Motion](motion.html) |

## `[safety]`

`soft_limit_min_deg` / `soft_limit_max_deg` (−50 / 110): every move is checked against these, 5° inside the hardware end switches. `limit_zero_deg` / `limit_max_deg` document where the hall sensors sit; they are not used for control.

## `[leds]`

`active_led_active_high`, `measuring_led_active_high` (both true). If an LED is lit when it should be off, flip its key.

## `[daq]`

`gate_width_s` (1.003 ms, the Model 222 pulse as measured) is recorded in every event file header. `pull_up = true` and the falling edge match the inverting input stage.

## `[imu]`

| key | meaning |
|---|---|
| `i2c_addr` | 0x69 with SDO → VDD; 0x68 if SDO → GND |
| `reference_accuracy_deg` | 0.2 — the Bosch inclinometer's catalogue accuracy; drawn as the horizontal error bar and the shaded band in calibration plots |
| `vibration_on_dps` / `vibration_off_dps` | 0.5 / 0.3 — gyro RMS thresholds (deg/s) for the "stable" flag, with hysteresis so it cannot flap |
| `calibration_path` | where the offset and ellipse are stored |
| `settle_tilt_std_deg`, `settle_window_s` | reserved; the settle before counting is currently a fixed wait |

## `[display]`, `[buttons]`

See [Front panel](front-panel.html). Hold times are in seconds; `shutdown_command` is what the Shutdown button runs after the daemon has stopped.

## `[scan]`

Defaults offered by the scan setup screen: `theta_start_deg`, `theta_stop_deg`, `theta_step_deg`, `acquisition_s` (3600 s per point by default). The TUI lets you change them per scan.

## `[temperature]`

`sensor_type = "DHT11"` (DHT22/AM2302 also accepted by the driver). The thresholds are informational.

## `[hv]`

`port` is the N1470's stable `/dev/serial/by-id/…` path, `baudrate` 9600, `poll_interval_s` 15. Read-only; see [High voltage](hv.html).

## `[influx]`

| key | default | meaning |
|---|---|---|
| `env_interval_s` | 10 | how often an environment point is built (angle is the fast quantity; the DHT updates every 30 s anyway) |
| `flush_interval_s` | 30 | how long points sit in RAM before one batched write — the Grafana latency, and what protects the SD card |
| `rate_bin_s` | 60 | the live rate bin; the scientific rate is rebuilt from the text files offline |

## `[paths]`

`data_dir` — where runs are written. The daemon's `--data-dir` argument and the systemd unit must agree with it.
