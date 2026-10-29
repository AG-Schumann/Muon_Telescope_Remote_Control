# Muon Telescope Remote Control — software

The Python package: the control daemon (`muon_daemon`), the terminal UI (`muon_tui`), the hardware drivers and scan logic (`muon_control`), bench scripts, the default configuration and the systemd unit.

Installation, architecture, the configuration reference and the data formats are documented on the project site: https://AG-Schumann.github.io/Muon_Telescope_Remote_Control/

```
software/
├── pyproject.toml           package metadata; installs muon-daemon and muon_telescope_manager
├── requirements.txt         extra runtime dependencies (TUI, sensors)
├── config/default.toml      the configuration, single source of truth
├── deploy/                  systemd unit
├── scripts/                 bench scripts, HV emergency off, calibration plotting
├── src/muon_control/        drivers (stepper, IMU, DAQ, HV, display, buttons, LEDs), config, storage
├── src/muon_daemon/         the daemon: server, state machine, hardware controller, publisher
├── src/muon_tui/            the Textual terminal UI
└── tests/                   hardware bench tests
```
