---
title: Installation
parent: Software
nav_order: 1
---

# Installation

Everything below assumes the Pi user `muon_telescope`, the repository checked out at `/home/muon_telescope/muon`, and a venv at `/home/muon_telescope/muon_venv`. Those three paths are baked into the systemd unit; change them there if yours differ.

## 1. Raspberry Pi preparation

```bash
sudo raspi-config        # Interface Options -> I2C: enable (IMU and display)
sudo apt install -y python3-venv git i2c-tools
i2cdetect -y 1           # expect 69 (IMU) and 3c (display)
```

Do **not** enable the 1-wire overlay (`dtoverlay=w1-gpio`): its default pin is GPIO4, which is the ACTIVE LED. The temperature sensor is a DHT11 on GPIO19 and does not use 1-wire.

## 2. Fetch the code

See [Fetching the code](../operations/git.html) for a sparse checkout of just `software/`. The short form:

```bash
git clone --no-checkout https://github.com/AG-Schumann/Muon_Telescope_Remote_Control muon
cd muon && git sparse-checkout init --cone && git sparse-checkout set software && git checkout main
```

## 3. Virtual environment and dependencies

```bash
python3 -m venv /home/muon_telescope/muon_venv
source /home/muon_telescope/muon_venv/bin/activate
cd /home/muon_telescope/muon/software
pip install -e .                     # the package: gpiozero, pyserial, smbus2
pip install -r requirements.txt      # textual, BMI160_i2c, adafruit DHT, python-dotenv
pip install influxdb-client          # optional: Grafana publishing
pip install matplotlib               # optional: calibration plots
```

`pip install -e .` creates two commands in the venv: `muon-daemon` and `muon_telescope_manager` (the TUI).

Anything missing is reported by the daemon at startup as a warning, not a crash — a missing IMU library, for instance, logs `IMU unavailable` and the daemon still runs. Check `journalctl` after a fresh install.

## 4. Configuration

`software/config/default.toml` is the single source of truth, documented key by key in [Configuration](config.html). The data directory defaults to `/home/muon_telescope/muon_data`; create it:

```bash
mkdir -p /home/muon_telescope/muon_data
```

For InfluxDB publishing, put the token in `software/.env` (never committed):

```
INFLUX_URL=http://localhost:8086
INFLUX_ORG=telescope_org
INFLUX_TOKEN=<token>
INFLUX_BUCKET_ENV=telescope_env
INFLUX_BUCKET_RATE=telescope_rate
```

## 5. The daemon as a service

```bash
sudo cp software/deploy/muon-daemon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now muon-daemon
sudo systemctl status muon-daemon
```

The unit runs `muon-daemon --config config/default.toml --data-dir /home/muon_telescope/muon_data` from `WorkingDirectory=/home/muon_telescope/muon/software`, restarts on failure after 5 s, and gives a stop 15 s before SIGKILL. The socket is `/tmp/muon_telescope.sock`, readable only by the daemon's user.

After pulling new code: `sudo systemctl restart muon-daemon`. The service does not pick up changes by itself.

## 6. Front-panel shutdown button

Holding the Shutdown button powers the Pi off. The daemon runs as a normal user, so give it that one command without a password:

```bash
echo 'muon_telescope ALL=(root) NOPASSWD: /sbin/poweroff' | sudo tee /etc/sudoers.d/muon-daemon
```

Without this rule the daemon still stops cleanly on the button; only the power-off step is skipped.

## 7. Run the TUI

From any SSH session as `muon_telescope`:

```bash
source /home/muon_telescope/muon_venv/bin/activate
muon_telescope_manager
```

Keys on the dashboard: `n` new run, `c` calibrate, `s` stop run, `q` quit. Quitting the TUI never stops a run — the daemon owns it.

## Bench scripts

`software/scripts/` holds standalone checks that talk to one piece of hardware at a time. Stop the daemon first so two processes do not share a device:

| script | checks |
|---|---|
| `spin_motor.py` | stepper, relay, direction |
| `tilt_sensor.py` | IMU readings |
| `hall_sensor.py` | the two hall switches |
| `push_button.py` | the limit-switch inputs |
| `measure_temp.py` | DHT11 |
| `display_test.py` | the front-panel display (`--live` for a counter) |
| `hv_control.py` | the N1470: status, and the emergency `off` |
| `plot_imu_calibration.py` | re-draw any saved calibration record |
| `run_scan.py` | a full scan without the daemon (legacy CLI path) |
