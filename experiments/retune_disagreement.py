#!/usr/bin/env python3
"""How wide is the band where the two retune objectives disagree?

Reproduces the first figure the README quotes. Prints a table and exits 1 if
the headline claim --- that the band is non-empty at the reference rhythm ---
stops holding.
"""

from __future__ import annotations

import random
import statistics
import sys

sys.path.insert(0, "src")

from ocintent.legality import (  # noqa: E402
    JobRhythm,
    cheapest_legal,
    disagreement_intervals,
    soonest_legal,
)

SEED = 20260901
POPULATION = 400


def reference(**over) -> JobRhythm:
    base = dict(
        accelerators=16_384, step_s=2.4, cross_stitch_collective_s=0.31,
        steps_per_checkpoint=250, checkpoint_window_s=120.0,
        checkpoint_crosses_stitch=False, steps_per_epoch=4_000, epoch_gap_s=45.0,
    )
    base.update(over)
    return JobRhythm(**base)


def main() -> int:
    print("THE REFERENCE RHYTHM")
    print("16,384 accelerators, 2.4 s step, 0.31 s cross-stitch collective,")
    print("checkpoint every 250 steps taking 120 s, epoch every 4,000 steps.")
    print()

    for crosses in (False, True):
        rhythm = reference(checkpoint_crosses_stitch=crosses)
        intervals = disagreement_intervals(rhythm)
        width = sum(hi - lo for lo, hi in intervals)
        label = "checkpoints cross the stitch" if crosses else "checkpoints stay local"
        print(f"{label}:")
        print(f"  disagreement set  " + ", ".join(
            f"[{lo:,.3f}, {hi:,.3f}) s" for lo, hi in intervals))
        print(f"  total width       {width:,.1f} s")
        cheap = cheapest_legal(rhythm, 60.0)
        soon = soonest_legal(rhythm, 60.0)
        print(f"  at a 60 s retune  cheapest waits for {cheap.boundary.value} "
              f"({cheap.total_delay_s:,.0f} s of delay, "
              f"{cheap.lost_accelerator_hours:,.1f} accel-h)")
        print(f"                    soonest takes {soon.boundary.value} "
              f"({soon.total_delay_s:,.0f} s of delay, "
              f"{soon.lost_accelerator_hours:,.1f} accel-h)")
        print(f"                    a storage decision, not a network one, moves the "
              f"wait by {abs(cheap.total_delay_s - soon.total_delay_s):,.0f} s")
        print()

    rng = random.Random(SEED)
    widths, counts = [], {}
    for _ in range(POPULATION):
        step = rng.uniform(0.4, 12.0)
        rhythm = JobRhythm(
            accelerators=rng.choice([512, 1024, 4096, 16_384, 32_768]),
            step_s=step,
            cross_stitch_collective_s=rng.uniform(0.02, 0.6) * step,
            steps_per_checkpoint=rng.choice([50, 100, 250, 500, 1000]),
            checkpoint_window_s=rng.uniform(20.0, 400.0),
            checkpoint_crosses_stitch=rng.random() < 0.5,
            steps_per_epoch=rng.choice([1000, 2000, 4000, 10_000]),
            epoch_gap_s=rng.uniform(5.0, 180.0),
        )
        intervals = disagreement_intervals(rhythm)
        counts[len(intervals)] = counts.get(len(intervals), 0) + 1
        widths.append(sum(hi - lo for lo, hi in intervals))

    nonzero = sorted(w for w in widths if w > 0)
    print(f"OVER {POPULATION} RANDOM RHYTHMS (seed {SEED})")
    print(f"  non-empty disagreement set   {len(nonzero)}/{POPULATION}")
    print(f"  width  min {nonzero[0]:,.1f} s  median "
          f"{statistics.median(nonzero):,.1f} s  max {nonzero[-1]:,.1f} s")
    print("  shape  " + ", ".join(f"{k} interval(s): {v}"
                                  for k, v in sorted(counts.items())))
    print()
    print("  A MEMS optical switch reconfigures in tens of milliseconds, which is")
    print(f"  below every band edge here. The disagreement is a slow-reconfiguration")
    print("  problem: ROADM provisioning, a metro turn-up, a hand patch.")

    ok = bool(disagreement_intervals(reference()))
    print()
    print("headline holds" if ok else "HEADLINE NO LONGER HOLDS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
