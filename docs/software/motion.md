---
title: Motion
parent: Software
nav_order: 6
---

# Motion

## No homing

Earlier versions drove to a hall switch to find zero. That mechanism is gone. The IMU knows the absolute angle, so at daemon start — and again after every completed move — the step counter is simply set to match it (`seed_from_imu`: wait 2 s for the mechanism to stop ringing, average the IMU for 5 s). The two hall sensors remain as read-only cross-checks and never control motion.

The soft limits (−50° … +110°, `[safety]`) are checked against that counter, which is why keeping it seeded matters: it is what makes the limits mean anything.

## A move is one move

`move_to_angle` computes the step delta from the freshly seeded position, moves once, releases the motor, re-seeds, and reports the angle the IMU measures. There are no correction retries. A move never lands exactly — backlash, the odd missed step, an imperfect `steps_per_deg` — but nothing downstream uses the *requested* angle: the measured one goes on the filename and into the summary row. Asking for 10° and recording 9.6° is the same physics, honestly labelled, and costs one move instead of three.

Because each move starts from a re-seeded position, errors do not accumulate across a scan.

One check remains and is deliberately loud: if the IMU says the telescope moved the opposite way to what was commanded (by more than 2°), the move aborts with a message naming `invert_direction`. That is a wiring fault, not a small error.

## Gravity and the approach direction

The telescope is bottom-heavy — the half below the axis outweighs the half above — so gravity always pulls it toward smaller angles, most strongly near 90°. And every move ends by releasing the coils **and cutting the 12 V**: afterwards nothing holds the load but the gearbox.

That decides which side to arrive from. Finish a move *upwards* and the gear teeth are loaded against gravity; the moment power goes, the load sags back across the gearbox's dead zone. Finish *downwards* and the teeth already rest on the flank gravity holds them against, so releasing changes nothing. The imbalance becomes a free anti-backlash preload — provided every angle is approached from above.

`approach_from = "above"` does exactly that: a move that would arrive from below first swings `approach_margin_deg` past the target, then comes down. In a simulated 0.8° dead zone this took the landing error from 0.80° on every target to 0.00°, at no cost in moves.

It also decides the **scan order**. Walking the angles from high to low means every step is already a downward move and needs no extra swing, so a scan visits 90° first and 0° last. The files carry their angles, so ordering is cosmetic; sort by `theta_deg` if you want it ascending.

Setting `approach_from = "below"` restores the old behaviour (ascending scans, upward finishes) and the sag that comes with it.

## Timing

A move costs the motion itself plus about 7 s of seeding (2 s settle + 5 s averaging). A scan adds `settle_s` (5 s) at each point before counting. For a 3600 s point that is noise; for a short bench scan it is most of the time.
