---
title: High voltage
parent: Software
nav_order: 8
---

# High voltage (CAEN N1470)

## The rule

**The software never sets a voltage.** Voltages, current limits and trip clearing are done on the N1470's front panel, by a person. The daemon polls the supply every 15 s over USB serial and shows what it reads — on the TUI's hardware panel (`read only` next to the header), on the front-panel display, and in InfluxDB as the `hv` measurement (per-channel `vmon`, `vset`, `imon_ua`, `iset_ua`, `on`, `faulted`).

The N1470 enforces the same rule in hardware: while its panel is in **LOCAL** mode it refuses every SET command. This build sits in LOCAL by default; the panel is the real lockout.

## Channel map

The mapping below is read off the N1470's set values as the TUI showed them (CH0 1340 V, CH1 1800 V, CH2 1560 V, CH3 1550 V), matched to the panels' suggested maxima in [Telescope](../hardware/telescope.html):

| N1470 channel | panel |
|---|---|
| CH0 | Panel 2 |
| CH1 | Panel 1 |
| CH2 | Panel 0 |
| CH3 | Panel 3 |

Confirm it against the cable labels before trusting it on a rebuilt setup, and take the operating voltages from the supply itself, not from this page.

## Emergency off

The one write that exists lives in a script that has to be typed, not behind a button:

```bash
cd /home/muon_telescope/muon/software
python3 scripts/hv_control.py              # what is the HV doing
python3 scripts/hv_control.py off          # all channels off
python3 scripts/hv_control.py off --wait   # ... and watch them ramp down
python3 scripts/hv_control.py on --yes     # back on (needs --yes)
```

Two things to know before you need this at 3 am:

- `off` is refused while the panel is in LOCAL. If you are standing at the crate anyway, the panel's own switch is faster.
- The daemon holds the same serial port and polls it every 15 s, so a reply can land in the wrong process. The script therefore never trusts the reply alone: it reads the voltages back and tells you what they actually are. Stopping the daemon first (`sudo systemctl stop muon-daemon`) makes the exchange clean.

## Interaction with motion

Switching the HV off during motor moves was considered and set aside: the move is slow and the supply ramps slowly, so the benefit was small and the added control path was not worth it.