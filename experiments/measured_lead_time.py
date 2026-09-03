#!/usr/bin/env python3
"""The one measured link: how far the physical layer's alarm ran ahead of the outage.

Reproduces the fourth finding the README quotes. Every number printed here is
read from the HEDGE testbed files (``make data``; SOURCES.md S6) by
``ocintent.hedge``: the first uncorrectable-FEC sample of each wavelength,
the interval in which the four UDP probes all went quiet, and the
received-power drop each wavelength took before its FEC cliff. The iperf
side is re-timed from iperf's own interval field, because each log line's
stamp is a stdout flush that trails its interval by up to five seconds
(DECISIONS.md D14); what the re-timing measured is printed first.

Two headlines, and the script fails loudly if either stops holding:

* in every run that lost the link, the first FEC alarm led the outage by
  more than one transponder poll, and the outage began within one poll of
  the last wavelength's failure sample: the first alarm was a warning and
  the last failure was the outage, to the data's own resolution;
* on every wavelength that failed, the power drop between BER onset and
  the cliff was closer to the 20 dB per decade branch than to the 10 dB
  per decade branch the drift model shipped with, and steeper than both.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, "src")

from ocintent import hedge  # noqa: E402

DATA = Path("data/hedge")


def _s(x):
    return "   never" if x is None else f"{x:7.1f} s"


def _signed(x):
    if x is None:
        return "      never"
    return f"{abs(x):5.1f} s {'after' if x >= 0 else 'before'}"


def main() -> int:
    try:
        summaries = hedge.load_summaries(DATA)
    except hedge.HedgeDataError as exc:
        print(f"no measurement: {exc}")
        return 1

    print("WHAT RE-TIMING THE IPERF LOGS MEASURED (per run, four server logs)")
    print(f"  {'run':<12} {'intervals':>9} {'anchor spread':>14} {'lines >1 s late':>16} "
          f"{'latest stamp':>13} {'probe median':>13}")
    for name, s in summaries.items():
        anchors = [rt.anchor_s for rt in s.retiming]
        late = sum(rt.late_lines for rt in s.retiming)
        print(f"  {name:<12} {s.reports:>9} {(max(anchors) - min(anchors)) * 1000:11.0f} ms "
              f"{late:>7}/{s.reports:<8} {max(rt.max_late_s for rt in s.retiming):10.1f} s "
              f"{s.probe_gbps_median:7.3f} Gbit/s")
    print()

    print("ALARM-TO-OUTAGE LEAD, PER RUN (seconds after the run's first transponder sample)")
    print(f"  {'run':<12} {'first FEC':>10} {'last FEC':>10} {'poll gap':>9} {'link dark':>10} "
          f"{'lead, first':>12}   outage vs last alarm sample")
    lost = []
    for name, s in summaries.items():
        gap = "        -" if s.last_failure_poll_gap_s is None else f"{s.last_failure_poll_gap_s:7.1f} s"
        print(f"  {name:<12} {_s(s.first_failure_s):>10} {_s(s.last_failure_s):>10} {gap:>9} "
              f"{_s(s.link_lost_s):>10} {_s(s.lead_first_failure_to_link_loss_s):>12}   "
              f"{_signed(s.lead_last_failure_to_link_loss_s)}")
        if s.link_lost_s is not None:
            lost.append(s)
    print()

    print("WHAT EACH FORMAT TOOK BEFORE ITS FEC CLIFF (attenuation run, received-power drop)")
    proto = summaries["prototype"]
    for tl in sorted(proto.labelled(), key=lambda t: t.failed_s or 0.0):
        print(f"  {tl.name:<16} BER {tl.ber_at_start:.1e} -> {tl.ber_at_cliff:.1e}   "
              f"{tl.attenuation_at_cliff_db:5.2f} dB down   failed {_s(tl.failed_s)}")
    print()

    print("HOW THE MEASURED DROP COMPARES WITH THE TWO BRANCHES (dB per decade of Q)")
    print(f"  {'wavelength':<30} {'measured':>9} {'decades':>8} {'implied':>8} {'10/dec':>7} "
          f"{'20/dec':>7}   closer      steeper than both")
    tally = {"coherent": 0, "direct": 0, "neither": 0}
    steeper = 0
    for name, s in summaries.items():
        for tl in s.failures:
            c = hedge.scaling_check(tl)
            if c is None:
                continue
            tally[c.closer] += 1
            steeper += c.steeper_than_both
            print(f"  {name + ' ' + tl.name:<30} {c.measured_db:6.1f} dB {c.decades_of_q:8.2f} "
                  f"{c.implied_db_per_decade:8.1f} {c.direct_db:7.1f} {c.coherent_db:7.1f}   "
                  f"{c.closer:<11} {'yes' if c.steeper_than_both else 'no'}")
    total = sum(tally.values())
    slopes = [hedge.scaling_check(tl).implied_db_per_decade
              for s in summaries.values() for tl in s.failures if hedge.scaling_check(tl)]
    print(f"  closer to the 20 dB branch on {tally['coherent']}/{total}, to the 10 dB branch on "
          f"{tally['direct']}, neither on {tally['neither']}; steeper than both on {steeper}/{total}")
    print(f"  implied slopes run from {min(slopes):.1f} to {max(slopes):.1f} dB per decade of Q")
    print()

    print("OUTSIDE THE AUTHORS' ANALYSIS WINDOWS (further disturbances the paper does not")
    print("describe, read from the same files; printed so the shape above is taken as what")
    print("the published runs show, not as a law)")
    for name, s in summaries.items():
        lo, hi = s.window
        whole = hedge.summarize(hedge.load_run(DATA, name, window=(0.0, math.inf)))
        later_dark = [(a, b) for a, b in whole.link_down if a > hi]
        later_fail = [t for t in whole.failures if t.failed_s > hi]
        dark = ", ".join(f"{a:.1f}-{f'{b:.1f}' if b is not None else 'end of file'} s"
                         for a, b in later_dark) or "none"
        fails = ", ".join(f"{t.name} {t.failed_s:.1f} s" for t in later_fail) or "none marked"
        line = f"  {name:<12} window {lo:g}-{hi:g} s: later dark intervals {dark}; later failures {fails}"
        if later_dark and later_fail:
            a = later_dark[0][0]
            first, last = later_fail[0].failed_s, later_fail[-1].failed_s
            line += (f"; the link went dark {abs(a - first):.1f} s "
                     f"{'after' if a >= first else 'before'} the first of them and "
                     f"{abs(a - last):.1f} s {'after' if a >= last else 'before'} the last")
        print(line)
    print("  (a wavelength's failure marker is its first counter change, so one that failed")
    print("  inside the window is not marked again later)")
    print()

    lead_ok = bool(lost) and all(
        s.lead_first_failure_to_link_loss_s is not None
        and s.last_failure_poll_gap_s is not None
        and s.lead_first_failure_to_link_loss_s > s.last_failure_poll_gap_s
        and s.outage_within_a_poll_of_last_failure
        for s in lost
    )
    scaling_ok = total > 0 and tally["coherent"] == total and steeper == total
    print("The first alarm led the outage by more than a poll in every run that had one, and "
          "the outage began within one poll of the last wavelength's failure, not the first."
          if lead_ok else "THE LEAD HEADLINE NO LONGER HOLDS")
    print("Every failing wavelength's drop was closer to the 20 dB per decade branch than to "
          "the 10 dB branch the model shipped with, and steeper than both."
          if scaling_ok else "THE SCALING HEADLINE NO LONGER HOLDS")
    print()
    print("headline holds" if lead_ok and scaling_ok else "HEADLINE NO LONGER HOLDS")
    return 0 if lead_ok and scaling_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
