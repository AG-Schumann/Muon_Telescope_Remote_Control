---
title: Dead time
parent: Measurements
nav_order: 1
---

# Dead time of the counting chain

The Model 222 stretches each ~50 ns discriminator pulse to ~1 ms before it reaches the Pi. How many counts does that 1 ms cost?

## Measurement

An external hex scaler counted the same signal twice: once raw and once after the 1 ms stretch, for 9.67 hours.

| | counts |
|---|---|
| raw (~50 ns) | 21 717 |
| stretched to 1 ms | 21 704 |
| elapsed | 34 800 859 ms |

**13 counts lost out of 21 717 — 0.06 %** at a singles rate of 0.624 Hz.

## Model

For a non-paralyzable dead time τ the expected loss is `N · R · τ`:

```
21717 × 0.624 Hz × 1 ms = 13.6 counts
```

Measured 13, predicted 13.6. The model and the bench agree to within a single count, which is the useful result: the dead time is understood, not merely small.

## Against the statistics

Poisson σ on 21 717 counts is √21717 = 147, so the loss is **0.09 σ**. Correcting for it shifts the rate by 374 µHz against an error bar of 4.2 mHz — under a tenth of the error bar. The correction is one line of arithmetic offline, so it can simply be applied; it will never be visible.

## What follows

- At this rate, 1 ms of dead time is not a reason for a faster DAQ. Even a pulse 20 000 × longer than the real one loses almost nothing.
- The Pi's own dead time is far below 1 ms, so its counting loss is smaller still.
- The 1 ms gate also removes PMT afterpulses (100 ns – 10 µs) before the Pi sees them. The same raw-vs-stretched comparison bounds afterpulsing at the current discriminator threshold to **< 0.03 % (2σ)**: any afterpulse population would have shown up as excess raw counts, and the excess is 13 − 13.6 = −0.6 ± 3.7. Repeating the pair at each threshold setting is the cheapest afterpulse study available with the hardware on the bench.
