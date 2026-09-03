#!/usr/bin/env python3
"""Which checkpoint strategy is cheapest, and where does the answer change?

Reproduces the second figure the README quotes: the cheapest durable strategy
is not fixed, and it flips twice inside the range of stitch widths that plants
actually have.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

from ocintent.checkpoint import (  # noqa: E402
    CheckpointPlan,
    Strategy,
    cheapest_durable,
)
from ocintent.checkpoint import compare as compare_strategies  # noqa: E402
from ocintent.legality import JobRhythm  # noqa: E402

STATE_BYTES = int(4.2e12)
LOCAL_GBPS = 60.0
SHARE = 0.5
WIDTHS = (100.0, 200.0, 400.0, 800.0, 1600.0, 3200.0)


def main() -> int:
    rhythm = JobRhythm(16_384, 2.4, 0.31, 250, 120.0, False, 4_000, 45.0)
    print(f"{STATE_BYTES / 1e12:g} TB of state, {LOCAL_GBPS:g} GB/s hall-local write,")
    print(f"a background replication allowed {SHARE:g} of the stitch.")
    print("Figures are percent of wall clock lost to checkpointing.")
    print()
    header = f"{'stitch':>8} " + " ".join(f"{s.value:>21}" for s in Strategy)
    print(header)
    print("-" * len(header))
    picks = {}
    for gbps in WIDTHS:
        plan = CheckpointPlan(Strategy.WRITE_LOCAL, STATE_BYTES, LOCAL_GBPS, gbps, SHARE)
        table = compare_strategies(plan, rhythm)
        best = cheapest_durable(plan, rhythm)
        picks[gbps] = best.strategy
        cells = []
        for strategy in Strategy:
            t = table[strategy]
            mark = "*" if strategy is best.strategy else " "
            durable = "" if t.survives_hall_loss else " (loses hall)"
            cells.append(f"{t.total_fraction * 100:>19.2f}{mark}{durable[:1]}")
        print((f"{int(gbps):>7}G " + " ".join(cells)).rstrip())
    print()
    print("* cheapest strategy that survives losing the hall it wrote in.")
    print("write-local is never marked: it does not survive, at any width.")
    print()
    transitions = [
        (a, b) for (a, sa), (b, sb) in zip(list(picks.items()), list(picks.items())[1:])
        if sa is not sb
    ]
    print(f"the answer changes {len(transitions)} time(s) across this range:")
    for lo, hi in transitions:
        print(f"  between {int(lo)}G and {int(hi)}G: "
              f"{picks[lo].value} -> {picks[hi].value}")
    print()
    print("At 200G the durable answer is async replication, whose cost is entirely")
    print("invisible in a training curve: the job stops for exactly as long as a")
    print("local write, and pays the difference in stolen collective bandwidth.")

    ok = len(set(picks.values())) >= 3
    print()
    print("headline holds" if ok else "HEADLINE NO LONGER HOLDS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
