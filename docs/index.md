---
title: Home
nav_order: 1
---

# Muon Telescope Remote Control

A four-panel plastic-scintillator cosmic-ray muon telescope that rotates in zenith angle under software control, built around a Raspberry Pi 4. This site documents the instrument, the control software and the data it writes.

![Muon Telescope V2, CAD render](assets/img/telescope_v2_cad.png)

## What the system does

- **Counts** muon events on two conditioned inputs (CH0, CH1) with microsecond-resolution timestamps, one plain-text file per channel per run.
- **Points** the telescope to a zenith angle with a stepper motor and a 100:1 gearbox, and **measures** where it actually is with an IMU — the measured angle is what goes into the data, not the commanded one.
- **Scans** the hemisphere automatically: a list of angles, a fixed counting time at each, one summary row per angle.
- **Monitors** itself: temperature and humidity, IMU tilt and vibration, the CAEN N1470 high-voltage supply (read-only), and publishes all of it to InfluxDB for a Grafana dashboard.
- **Runs unattended** as a systemd service, with a terminal UI over SSH, a front panel (two LEDs, a 4x16 display, four buttons) and a safe shutdown path.

## Where to start

| If you want to… | read |
|---|---|
| understand the detector and its panels | [Telescope](hardware/telescope.html) |
| know which wire goes where | [Control board](hardware/control-board.html) |
| install the software on a fresh Pi | [Installation](software/install.html) |
| understand how the daemon, TUI and drivers fit together | [Architecture](software/architecture.html) |
| look up a config key | [Configuration](software/config.html) |
| analyse the data files | [Data formats](software/data-format.html) |
| calibrate the angle sensor | [IMU calibration](software/calibration.html) |
| fix something that stopped working | [Troubleshooting](operations/troubleshooting.html) |

## Repository layout

```
Muon_Telescope_Remote_Control/
├── software/        the Python package (daemon, TUI, drivers), config, scripts, systemd unit
├── hardware/        KiCad board designs, CAD, measurement-setup drawings and photos
└── docs/            this site
```

The scientific record is always the plain-text files under `data_dir` (default `/home/muon_telescope/muon_data`). Everything else — the TUI, Grafana, the front panel — is a view of that record, never a substitute for it.
