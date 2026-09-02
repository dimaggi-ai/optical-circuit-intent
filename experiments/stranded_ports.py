#!/usr/bin/env python3
"""Free optical ports that cannot serve the demand waiting for them.

Reproduces the third figure the README quotes. A switch can be half empty and
still unable to admit anything, because ports belong to trunks and trunks lead
somewhere specific.
"""

from __future__ import annotations

import random
import sys

sys.path.insert(0, "src")

from ocintent.radix import OpticalSwitch, Request, Trunk  # noqa: E402

SEED = 20260901
POPULATION = 400


def main() -> int:
    print("A THREE-TRUNK SWITCH, 48 PORTS, NOTHING ALLOCATED")
    switch = OpticalSwitch("hall-a", (
        Trunk("hall-b", 16, 400.0), Trunk("hall-c", 16, 400.0), Trunk("hall-d", 16, 400.0),
    ))
    scenarios = {
        "4.8T spread evenly over three peers": [
            Request("a", "hall-b", 1600.0), Request("b", "hall-c", 1600.0),
            Request("c", "hall-d", 1600.0),
        ],
        "the same 4.8T all wanting one peer": [Request("a", "hall-b", 4800.0)],
        "6.4T wanting one peer (over that trunk)": [Request("a", "hall-b", 6400.0)],
    }
    for label, demand in scenarios.items():
        stranded = switch.stranded_ports(demand)
        frag = switch.fragmentation(demand)
        print(f"  {label:<42} {stranded:>3} stranded  ({frag * 100:5.1f}% of free)")
    print()

    print("A SWITCH FOUR-FIFTHS EMPTY THAT CANNOT ADMIT ANYTHING")
    busy = OpticalSwitch("hall-a", (Trunk("hall-b", 8, 400.0), Trunk("hall-c", 24, 400.0)))
    busy.allocate(Request("live-1", "hall-b", 2400.0, priority=5, job_id="pretrain-7"))
    demand = [Request("want", "hall-b", 1200.0, priority=1, job_id="eval-2")]
    print(f"  {busy.used_ports()}/{busy.total_ports()} ports used, "
          f"{busy.free_ports()} free")
    print(f"  free on the trunk the demand wants: {busy.free_ports('hall-b')}")
    print(f"  free on the trunk it does not:      {busy.free_ports('hall-c')}")
    print(f"  stranded: {busy.stranded_ports(demand)} "
          f"({busy.fragmentation(demand) * 100:.0f}% of free capacity)")
    print()

    rng = random.Random(SEED)
    fragmentations = []
    fully_stranded = 0
    for _ in range(POPULATION):
        trunks = tuple(
            Trunk(f"hall-{chr(98 + i)}", rng.choice([4, 8, 16, 32]), 400.0)
            for i in range(rng.randint(2, 5))
        )
        sw = OpticalSwitch("hall-a", trunks)
        for i in range(rng.randint(0, 6)):
            peer = rng.choice(trunks).peer_hall
            try:
                sw.allocate(Request(f"c{i}", peer, rng.randint(1, 8) * 400.0))
            except Exception:
                pass
        want = [Request("w", rng.choice(trunks).peer_hall, rng.randint(1, 12) * 400.0)]
        if sw.free_ports() == 0:
            continue
        frag = sw.fragmentation(want)
        fragmentations.append(frag)
        if frag == 1.0:
            fully_stranded += 1
    fragmentations.sort()
    mid = fragmentations[len(fragmentations) // 2]
    print(f"OVER {len(fragmentations)} RANDOM SWITCHES AND DEMANDS (seed {SEED})")
    print(f"  median fragmentation                         {mid * 100:.0f}% of free ports stranded")
    print(f"  switches where every free port is stranded   {fully_stranded} "
          f"({fully_stranded / len(fragmentations) * 100:.0f}%)")
    print()
    print("A capacity report that counts free ports is counting the wrong thing.")

    ok = fully_stranded > 0
    print()
    print("headline holds" if ok else "HEADLINE NO LONGER HOLDS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
