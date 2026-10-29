---
title: Data formats
parent: Software
nav_order: 4
---

# Data formats

Everything is plain text, append-only, with `# key = value` headers. A run that crashes halfway still leaves every event written up to that moment on disk.

## The run folder

```
muon_data/
├── latest.txt                         # which run is current/last, and its status
├── p0_350mv/                          # one folder per run, name chosen at start
│   ├── daq_events_ch0.txt             # plain run: one event file per channel
│   └── daq_events_ch1.txt
├── scan_20261004/                     # a scan
│   ├── summary.txt                    # one row per angle
│   ├── scan_th+090.0_real+89.97_ch0.txt
│   ├── scan_th+090.0_real+89.97_ch1.txt
│   ├── scan_th+080.0_real+80.21_ch0.txt
│   └── ...
└── imu_calibration/
    ├── imu_cal_2026-10-04T14-03-22Z.txt
    └── imu_cal_2026-10-04T14-03-22Z.png
```

A run name is never reused: starting a run with an existing name is refused so nothing is overwritten. Runs started from the front-panel button are named `btn_MMDD_HHMMSS`.

`latest.txt`:

```
# latest
# name = p0_350mv
# folder = p0_350mv
# kind = measurement
# status = running          running | done | aborted | stopped | error
# updated_iso = 2026-10-04T12:25:59+00:00
# started_iso = 2026-10-04T12:25:59+00:00
# closed_iso = ...          once the run has ended
```

`done` is a run that ended normally (Stop, or its duration). `aborted` is the front-panel Reset. The `status = ...` line is the fastest way to tell, months later, whether a folder is a finished measurement.

## Event files

One per channel per counting period. Scan files carry both the requested angle (`th`) and the one the IMU measured (`real`) in the name, so a directory listing already says where each spectrum was taken.

```
# daq_run
# utc_anchor_iso = 2026-10-04T12:25:59.694513+00:00
# utc_anchor_unix = 1788117959.6945128
# mono_anchor_ns = 218587569970277
# gpio_pin = 23
# gate_width_s = 0.001003
# columns = index t_rel_ns
1 1949920399
2 12637321038
...
# stop_t_rel_ns = 1039921037882
# livetime_s = 1039.921037882
# total_counts = 135
```

- `t_rel_ns` is an integer nanosecond offset from `mono_anchor_ns`, taken with the monotonic clock in the edge callback before anything else happens. It is immune to NTP adjustments. Absolute time of an event = `utc_anchor_unix + t_rel_ns / 1e9`.
- Resolution is 1 ns; accuracy is that of a Linux interrupt plus Python, roughly 100 µs to 1 ms. For counting that is irrelevant; for timing analysis it is the number to quote.
- `livetime_s` is **measured** from the anchors, not assumed from the requested duration. Always compute rates as `total_counts / livetime_s`. A run that overran its target (it happens; see [Troubleshooting](../operations/troubleshooting.html)) is still correct because of this.
- The last data line's `index` equals `total_counts`.

## Scan summary

`summary.txt`, one row per angle, written as each angle finishes:

```
# scan_run
# utc_start = 2026-10-04T10:00:00+00:00
# acquisition_s = 3600.0
# settle_s = 5.0
# columns = theta_deg angle_actual_deg livetime_s ch0_counts ch0_rate_Hz ch1_counts ch1_rate_Hz temp_C humidity_RH tilt_deg event_file_stem
90.000 89.968 3600.301 2210 0.6139 2187 0.6075 21.5 41.0 89.97 scan_th+090.0_real+89.97
```

The column list is generated from the number of channels, so header and rows cannot disagree. `theta_deg` is what was asked for; `angle_actual_deg` is what the IMU measured and is the value to bin by. Angles are visited from high to low (see [Motion](motion.html)); sort by `theta_deg` if you prefer ascending.

## IMU calibration

`imu_calibration.json` holds the current calibration (offset, and the ellipse parameters when fitted) and is what the daemon loads at start. Alongside it, every calibration writes a timestamped record with the points it was fitted from:

```
# imu_calibration
# utc = 2026-10-04T14:03:22+00:00
# n_points = 9
# fit = ellipse+offset
# offset_deg = -1.712
# bias_y = ...  bias_z = ...  scale_y = ...  scale_z = ...
# rms_residual_deg = 0.155
# max_residual_deg = 0.321
# reference_accuracy_deg = 0.2
# columns = ref_deg ref_err_deg imu_deg imu_err_deg residual_deg ay az n_samples
```

`imu_deg` is the IMU's angle *after* the calibration was applied, recomputed through the driver that will actually report it; `residual_deg = imu_deg − ref_deg`. `imu_err_deg` is the standard error of the IMU mean over the averaging window. The `.png` next to it is drawn by `scripts/plot_imu_calibration.py`; see [IMU calibration](calibration.html).
