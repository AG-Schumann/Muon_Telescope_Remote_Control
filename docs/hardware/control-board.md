---
title: Control board
parent: Hardware
nav_order: 2
---

# Control board

The "mu-TC v1" board carries the Raspberry Pi 4 and everything between it and the telescope: the two DAQ inputs, the stepper driver wiring, the hall sensors, the IMU, the temperature sensor and the front-panel connectors. All pin assignments are in `software/config/default.toml` under `[gpio]`; the table below is that section with its wiring.

## GPIO map (BCM numbering)

| GPIO | Schematic name | Function | Direction |
|---|---|---|---|
| 2, 3 | SDA, SCL | I2C bus 1: IMU (0x69) and display (0x3C) | bus |
| 4 | LED_ACTIVE | front panel LED, "daemon running" | out |
| 5 | — | motor toggle / end-switch chain sense (reserved, not read) | in |
| 7 | DISPLAY_RST | display reset, active low | out |
| 8 | RELAY | 12 V relay for the TB6600 — the actual motor power cut | out |
| 12 | STEP | TB6600 step pulse | out |
| 16 | EN | TB6600 enable (active low on the driver) | out |
| 17 | BTN_START | front panel button, J10 pin 1 | in, pull-up |
| 18 | SIG_1 | DAQ channel 1 (CH_1, connector J16) | in |
| 19 | TEMP | DHT11 temperature/humidity (single-wire DHT protocol, not Dallas 1-wire) | in/out |
| 20 | DIR | TB6600 direction | out |
| 21 | BTN_SHUTDOWN | front panel button, J10 pin 4 | in, pull-up |
| 22 | BTN_RESET | front panel button, J10 pin 3 | in, pull-up |
| 23 | SIG_0 | DAQ channel 0 (CH_0, connector J1) | in |
| 24 | LED_MEAS | front panel LED, "measurement running" | out |
| 25 | SW_MAX | hall sensor at the far end — read-only cross-check | in |
| 26 | SW_0 | hall sensor at zero — read-only cross-check | in |
| 27 | BTN_STOP | front panel button, J10 pin 2 | in, pull-up |

GPIO 6 and 13 are routed to the microstep pins of an unpopulated DRV8825 footprint and are left alone. GPIO 9/10/11 (SPI) and 14/15 (UART) are free.

The config loader checks this table for collisions at startup: a pin listed twice, or I2C's GPIO 2/3 reused as digital I/O, stops the daemon with a clear message rather than letting two drivers fight over one pin.

## DAQ inputs

Two coaxial inputs, each through an inverting BC547 stage: an event pulls the GPIO **low**, so the counter watches falling edges with the internal pull-up on. The input pulse is the Model 222 gate, ~1.003 ms wide (measured on an oscilloscope; `[daq] gate_width_s`).

- CH_0 → J1 → GPIO23 — this was the original single channel and stays `ch0`, so older single-channel runs keep their meaning.
- CH_1 → J16 → GPIO18

## Motor

- NEMA 17 stepper, 1.8°/step, through a **100:1 planetary gearbox**
- TB6600 driver at 1/8 microstepping (set on the driver's DIP switches): 200 × 8 × 100 / 360 = **444.4 steps per degree**
- 12 V reaches the driver through a relay (GPIO8) which the daemon closes only while moving. After every move the coils are released **and** the 12 V is cut — nothing holds the load but the gearbox. This is why the approach direction matters; see [Motion](../software/motion.html).
- The front-panel motor toggle and the end switches are in series with the relay coil, so the hardware can always deny power regardless of software.
- Two hall sensors (GPIO26 at zero, GPIO25 at the far end) are read-only cross-checks. There is no homing: the absolute angle reference is the IMU.
- Software soft limits: −50° to +110° (`[safety]`), 5° inside the hardware switches.

## Sensors

- **IMU**: BMI160 on I2C, address 0x69 (SDO → VDD). Mounted on the moving assembly, so it reads the telescope's true tilt. See [IMU calibration](../software/calibration.html).
- **Temperature / humidity**: DHT11 on GPIO19.
- **High voltage**: CAEN N1470 over USB (FTDI serial), addressed by its stable `/dev/serial/by-id/…` path. Read-only from software; see [High voltage](../software/hv.html).

## Front panel connectors

**J12 — LEDs.** Two LEDs through 470 Ω, both driven from a GPIO to ground (active-high). Verified on the bench; the schematic can be read either way, which is why the polarity is a config key (`[leds]`).

**J10 — buttons.** Each button: GPIO → 1 kΩ → switch → GND, with 100 nF to ground after the resistor as an RC filter. There is no pull-up on the board; the Pi's internal pull-up is used, so idle is high and pressed is low. Pin 5 is ground.

| J10 pin | button | GPIO |
|---|---|---|
| 1 | Start | 17 |
| 2 | Stop | 27 |
| 3 | Reset | 22 |
| 4 | Shutdown | 21 |
| 5 | GND | — |

**Display.** EA DOGS164W-A, 4 × 16 characters, SSD1803A controller in I2C mode, 3.3 V only.

| Display pin | goes to |
|---|---|
| 13 VDD | 3.3 V |
| 14 VSS | GND |
| 12 IM1, 18 CS | GND (selects I2C) |
| 19 SA0 | GND (address 0x3C) |
| 17 SCLK | GPIO3 (SCL) |
| 16 SID + 15 SOD | GPIO2 (SDA), joined |
| 20 RST | GPIO7, active low |
| 11 VOUT | 1 µF to GND |
| 1, 2, 9, 10 | backlight (amber LED40X33-A, 33 Ω from 3.3 V, always on) |

Decoupling: 1 µF tantalum VDD–VSS and VOUT–VSS. Do not add external I2C pull-ups; the Pi's 1.8 kΩ are enough and the display's pins have ~600–1000 Ω internal series resistance.
