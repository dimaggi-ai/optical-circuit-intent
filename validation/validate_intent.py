#!/usr/bin/env python3
"""What this repository checks, and --- printed first --- what it does not.

Every point is one of three kinds, and the kind is the honest part:

``calibrated``
    Pinned to a figure published outside this work. Two points, and they pin
    *one* relation at two places rather than being two independent anchors.
    That limitation is in the declined list, not buried here.
``emergent``
    An ordering or a regime boundary that nothing in the code was tuned to
    produce. These are the points that can actually go red on a real change.
``sanity``
    A property of this repository's own structure. Useful, and worth nothing as
    evidence about optical plants. Sanity points carry no citation, and the
    reference column prints ``-`` for them by construction.

Run it: ``python validation/validate_intent.py``. Exit status is 1 if any point
fails.
"""

from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, "src")

from ocintent.checkpoint import (  # noqa: E402
    CheckpointPlan,
    Strategy,
    cheapest_durable,
)
from ocintent.checkpoint import compare as compare_strategies  # noqa: E402
from ocintent.checkpoint import tax  # noqa: E402
from ocintent.drift import (  # noqa: E402
    DEFAULT_TARGET_BER,
    THERMAL_DELAY_PS_PER_KM_K,
    DeclaredCircuit,
    MeasuredCircuit,
    ber_from_q,
    forecast,
    thermal_rtt_swing_us,
)
from ocintent.drift import compare as compare_circuit  # noqa: E402
from ocintent import hedge  # noqa: E402
from ocintent.intent import Boundary, Endpoint, Intent, Verb  # noqa: E402
from ocintent.ledger import Cause, DebtEntry, Ledger  # noqa: E402
from ocintent.legality import (  # noqa: E402
    JobRhythm,
    cheapest_legal,
    disagreement_intervals,
    ladder,
    objectives_disagree,
    soonest_legal,
)
from ocintent.radix import (  # noqa: E402
    Allocation,
    OpticalSwitch,
    Request,
    Trunk,
    preemption_plan,
)

SEED = 20260901
POPULATION = 400

CALIBRATED, EMERGENT, SANITY = "calibrated", "emergent", "sanity"


@dataclass(frozen=True)
class Point:
    name: str
    kind: str
    passed: bool
    detail: str
    reference: str = "-"

    def __post_init__(self) -> None:
        if self.kind == SANITY and self.reference != "-":
            raise ValueError(
                f"{self.name}: a sanity point checks this repository's own structure "
                "and must not cite anything; a citation on it would make an internal "
                "consistency check look like evidence about the world"
            )
        if self.kind == CALIBRATED and self.reference == "-":
            raise ValueError(f"{self.name}: a calibrated point must name its anchor")


#: What this registry cannot check. Printed before the results, every run.
DECLINED: Tuple[str, ...] = (
    "One measured plant, and it is a laboratory link: the HEDGE testbed "
    "(SOURCES.md S6), more than 100 km of fibre through four amplifiers and a "
    "ROADM, and two induced faults, a fibre bend and a transmit-side "
    "attenuator. That its transponders are coherent and its error rate the "
    "pre-FEC one is this repository's reading of a paper that says neither "
    "(ASSUMPTIONS.md A12). Nothing has been compared against a production "
    "switch, ROADM or metro span, and the retune, radix, checkpoint and ledger "
    "models remain unmeasured.",
    "The measured Layer-3 outcome is UDP availability from four iperf sessions, "
    "each carrying the median rate the reader prints (1.2 Gbit/s in every run), "
    "not a collective and not capacity; the paper puts those probes on a "
    "600 Gbit/s aggregate in its bend runs. The lead times run from a "
    "wavelength's uncorrectable-FEC counter moving to the probes going quiet. "
    "The iperf side is re-timed from iperf's own interval field, because each "
    "log line's stamp is a stdout flush up to five seconds late (DECISIONS.md "
    "D14); the transponder side is polled about every 1.6 s on a clock whose "
    "agreement with the iperf hosts' is undocumented. Read a lead to the "
    "nearest poll, and never as a job's or a collective's lead time.",
    "No number measured on that link transfers to another plant as a constant. "
    "The registry claims the orderings the paper published and the shape of the "
    "alarm-to-outage lead inside the authors' analysis windows; outside them the "
    "files hold further disturbances the paper does not describe, and in one of "
    "them the link went dark more than a poll before the last wavelength's alarm "
    "sample. It claims no alarm threshold, no scheduler coupling, no "
    "capacity-degradation profile, and it does not close the thermal blind spot, "
    "which needs a delay measurement the testbed did not take.",
    "The two error-rate anchors pin ONE published relation at two places. They "
    "are not two independent anchors, and a systematic error in that relation "
    "would leave both of them green. The six measured anchors pin the paper's "
    "reading of its own raw files, which makes this parsing trustworthy and says "
    "nothing about the six models.",
    "The margin-to-Q relation is first order. It ignores chromatic dispersion, "
    "fibre nonlinearity and every amplifier's noise contribution, and it is not "
    "calibrated against any particular receiver. It gets the shape of the cliff "
    "right and should not be read as predicting a number for a given link. On "
    "every wavelength of the measured link that failed, the drop from BER onset "
    "to the FEC cliff was closer to the 20 dB per decade branch than to the "
    "default 10, and steeper than both; the default stays direct because the "
    "anchors are written against it (DECISIONS.md D15).",
    "Connector contamination is an event, not a trend. It is the most common cause "
    "of real insertion-loss faults and the forecast is structurally blind to it, so "
    "a green forecast is not a statement that a path is healthy.",
    "The contention model shares a circuit linearly between a replication and a "
    "collective. There is no queueing model, no congestion control, and no "
    "incast behaviour, all of which matter at the widths quoted.",
    "Converting accelerator-hours to currency needs a rate only the plant owner "
    "has. None is supplied and none is defaulted.",
    "The slowdown fraction that turns a drifted circuit into a daily debt is a "
    "caller input. Nothing here derives it from a job's communication profile.",
    "A retune is treated as one scalar duration. A real reconfiguration has a "
    "distribution, a failure probability, and a rollback cost, none of which are "
    "modelled.",
    "No plant has accepted a compiled plan. There is no vendor session by design "
    "(DECISIONS.md D2), so 'compiles' means 'produces operations', not 'works'.",
    "Stranded-port counts take pending demand as given. In a real plant the "
    "pending demand is itself a forecast, and a wrong one changes the answer.",
    "The reference rhythm is illustrative. It is not measured from a named job, "
    "and every headline figure is quoted against it.",
)


def reference_rhythm(**over) -> JobRhythm:
    base = dict(
        accelerators=16_384, step_s=2.4, cross_stitch_collective_s=0.31,
        steps_per_checkpoint=250, checkpoint_window_s=120.0,
        checkpoint_crosses_stitch=False, steps_per_epoch=4_000, epoch_gap_s=45.0,
    )
    base.update(over)
    return JobRhythm(**base)


def random_rhythm(rng: random.Random) -> JobRhythm:
    step = rng.uniform(0.4, 12.0)
    return JobRhythm(
        accelerators=rng.choice([512, 1024, 4096, 16_384, 32_768]),
        step_s=step,
        cross_stitch_collective_s=rng.uniform(0.02, 0.6) * step,
        steps_per_checkpoint=rng.choice([50, 100, 250, 500, 1000]),
        checkpoint_window_s=rng.uniform(20.0, 400.0),
        checkpoint_crosses_stitch=rng.random() < 0.5,
        steps_per_epoch=rng.choice([1000, 2000, 4000, 10_000]),
        epoch_gap_s=rng.uniform(5.0, 180.0),
    )


# ==========================================================================
# calibrated
# ==========================================================================


def point_q_six_is_the_published_1e9() -> Point:
    """The convention every 1e-9 receiver specification is written against.

    Checked as "rounds to the published one-significant-figure value" rather
    than against a tolerance chosen here, because one significant figure is how
    the anchor is published. A tolerance picked after seeing the residual would
    not be a check.
    """
    got = ber_from_q(6.0)
    rounded = float(f"{got:.0e}")
    return Point(
        "q-of-six-rounds-to-the-published-1e-9",
        CALIBRATED,
        rounded == 1e-9,
        f"Q=6 gives {got:.4e}, which to one significant figure is {rounded:.0e}",
        reference="SOURCES.md S1",
    )


def point_q_seven_is_the_published_value() -> Point:
    """The other place the same relation is quoted to three figures."""
    got = ber_from_q(7.0)
    published = 1.28e-12
    rel = abs(got - published) / published
    return Point(
        "q-of-seven-matches-the-published-1.28e-12",
        CALIBRATED,
        rel < 0.01,
        f"Q=7 gives {got:.4e} against a published {published:.2e} ({rel * 100:.2f}% apart)",
        reference="SOURCES.md S1",
    )


# ==========================================================================
# emergent
# ==========================================================================


def point_published_ocs_switching_is_below_every_disagreement_band() -> Point:
    """The result that decides who should care about the legality ladder.

    A MEMS optical circuit switch reconfigures in the tens of milliseconds. If
    that is the retune, the two objectives agree at every rhythm tested and the
    whole ladder is academic. The disagreement lives above a couple of seconds
    --- which is ROADM provisioning, a metro circuit turn-up, a manual patch.
    Nothing was tuned to produce this; it falls out of the step times.
    """
    rng = random.Random(SEED)
    ocs_retune_s = 0.025  # the slow end of published MEMS reconfiguration times
    disagreeing_at_ocs = 0
    lowest_edge = math.inf
    for _ in range(POPULATION):
        rhythm = random_rhythm(rng)
        if objectives_disagree(rhythm, ocs_retune_s) is not None:
            disagreeing_at_ocs += 1
        intervals = disagreement_intervals(rhythm)
        if intervals:
            lowest_edge = min(lowest_edge, intervals[0][0])
    return Point(
        "a-mems-ocs-retune-is-below-every-rhythms-disagreement-band",
        EMERGENT,
        disagreeing_at_ocs == 0 and lowest_edge > ocs_retune_s,
        f"at a {ocs_retune_s * 1000:.0f} ms retune, {disagreeing_at_ocs}/{POPULATION} "
        f"rhythms disagree; the lowest band edge over the population is "
        f"{lowest_edge:.3f} s, {lowest_edge / ocs_retune_s:.0f}x the OCS time",
        reference="SOURCES.md S3",
    )


def point_the_objectives_disagree_for_most_rhythms() -> Point:
    """If this were rare the ladder would be a curiosity rather than a decision."""
    rng = random.Random(SEED + 1)
    widths = []
    for _ in range(POPULATION):
        total = sum(hi - lo for lo, hi in disagreement_intervals(random_rhythm(rng)))
        widths.append(total)
    nonzero = [w for w in widths if w > 0]
    widths.sort()
    return Point(
        "the-two-objectives-disagree-for-most-rhythms",
        EMERGENT,
        len(nonzero) > 0.9 * POPULATION,
        f"{len(nonzero)}/{POPULATION} rhythms have a non-empty disagreement set; "
        f"median width {widths[len(widths) // 2]:,.0f} s, max {widths[-1]:,.0f} s",
    )


def point_the_disagreement_set_is_often_not_one_interval() -> Point:
    """Why the exact decomposition replaced a bisection that returned one band.

    A bisected 'edge' is only an edge if the set is contiguous. It frequently
    is not, and the fraction that is not is the size of the error the earlier
    approach was making.
    """
    rng = random.Random(SEED + 2)
    counts = {}
    for _ in range(POPULATION):
        n = len(disagreement_intervals(random_rhythm(rng)))
        counts[n] = counts.get(n, 0) + 1
    multi = sum(v for k, v in counts.items() if k > 1)
    shape = ", ".join(f"{k} interval(s): {v}" for k, v in sorted(counts.items()))
    return Point(
        "the-disagreement-set-is-often-more-than-one-interval",
        EMERGENT,
        multi > 0,
        f"{multi}/{POPULATION} rhythms have a non-contiguous disagreement set ({shape})",
    )


def point_the_cheapest_durable_strategy_flips_with_circuit_width() -> Point:
    """The headline checkpoint result. Nothing selects for it."""
    rhythm = reference_rhythm()
    picks = {}
    for gbps in (100.0, 200.0, 400.0, 800.0, 1600.0, 3200.0):
        plan = CheckpointPlan(Strategy.WRITE_LOCAL, int(4.2e12), 60.0, gbps, 0.5)
        picks[gbps] = cheapest_durable(plan, rhythm).strategy
    distinct = len(set(picks.values()))
    rendered = ", ".join(f"{int(g)}G->{s.value}" for g, s in picks.items())
    return Point(
        "the-cheapest-durable-checkpoint-strategy-changes-with-stitch-width",
        EMERGENT,
        distinct >= 3,
        f"{distinct} distinct answers across the range: {rendered}",
    )


def point_the_fastest_strategy_is_not_the_cheapest() -> Point:
    """The stop tax is visible in a training curve. The contention tax is not.

    Checked over a range of widths rather than at one, and scored by how often
    the two disagree. An earlier version tested a single width with a fallback
    clause, so it could report PASS on the fallback while the claim in its own
    name was false at that width --- the exact shape of a check that measures
    nothing. There is no fallback here: the point is the disagreement or it is
    nothing.
    """
    rhythm = reference_rhythm()
    disagreeing = []
    for gbps in (100.0, 200.0, 400.0, 800.0, 1600.0, 3200.0):
        plan = CheckpointPlan(Strategy.WRITE_LOCAL, int(4.2e12), 60.0, gbps, 0.5)
        durable = [t for t in compare_strategies(plan, rhythm).values()
                   if t.survives_hall_loss]
        shortest = min(durable, key=lambda t: t.stop_s)
        cheapest = min(durable, key=lambda t: t.total_fraction)
        if shortest.strategy is not cheapest.strategy:
            disagreeing.append(
                f"{int(gbps)}G: shortest window {shortest.strategy.value} "
                f"({shortest.stop_s:.0f} s, {shortest.total_fraction * 100:.2f}% total) "
                f"but cheapest {cheapest.strategy.value} "
                f"({cheapest.total_fraction * 100:.2f}%)"
            )
    return Point(
        "the-shortest-checkpoint-window-is-not-the-cheapest-strategy",
        EMERGENT,
        len(disagreeing) > 0,
        f"the two criteria pick different strategies at {len(disagreeing)} of 6 widths"
        + ("; " + "; ".join(disagreeing) if disagreeing else ""),
    )


def point_a_wider_stitch_never_hurts() -> Point:
    """A monotonicity nothing enforces, over a population rather than two points."""
    rng = random.Random(SEED + 3)
    violations = 0
    moved = 0
    for _ in range(POPULATION):
        rhythm = random_rhythm(rng)
        state = rng.uniform(0.2e12, 20e12)
        local = rng.uniform(5.0, 200.0)
        narrow_g = rng.uniform(50.0, 400.0)
        wide_g = narrow_g * rng.uniform(1.5, 8.0)
        for strategy in (Strategy.ASYNC_REPLICATE, Strategy.SYNC_REPLICATE,
                         Strategy.STAGE_THROUGH_OBJECT):
            narrow = tax(CheckpointPlan(strategy, state, local, narrow_g, 0.5), rhythm)
            wide = tax(CheckpointPlan(strategy, state, local, wide_g, 0.5), rhythm)
            if wide.total_fraction > narrow.total_fraction + 1e-12:
                violations += 1
            if wide.total_fraction < narrow.total_fraction - 1e-12:
                moved += 1
    return Point(
        "a-wider-stitch-never-costs-a-crossing-strategy-more",
        EMERGENT,
        violations == 0 and moved > 0,
        f"{violations} violations over {POPULATION * 3} comparisons, "
        f"{moved} of which strictly improved (a run where nothing moved would "
        f"pass this check while testing nothing)",
    )


def point_exact_preemption_beats_greedy() -> Point:
    """Why the subset search is exact rather than greedy by cost per port.

    The greedy rule here is the natural one: evict the lowest cost-per-port
    circuits until enough ports are free. It is not a straw man, and it is what
    an implementation reaches for first.
    """
    rng = random.Random(SEED + 4)
    worse, equal, cases = 0, 0, 0
    total_excess = 0.0
    for _ in range(POPULATION):
        ports = rng.choice([8, 16, 32, 64])
        switch = OpticalSwitch("hall-a", (Trunk("hall-b", ports, 400.0),))
        costs = {}
        for i in range(rng.randint(2, 6)):
            width = rng.randint(1, max(1, ports // 3)) * 400.0
            cid = f"c{i}"
            try:
                switch.allocate(Request(cid, "hall-b", width, priority=1, job_id=f"j{i}"))
            except Exception:
                break
            costs[cid] = rng.uniform(0.5, 40.0)
        want = rng.randint(1, ports) * 400.0
        request = Request("new", "hall-b", want, priority=9)
        exact = preemption_plan(switch, request, lambda a: costs[a.circuit_id])
        if exact is None or not exact.victims:
            continue
        needed = switch.trunk("hall-b").ports_for(want) - switch.free_ports("hall-b")
        greedy_cost, freed = 0.0, 0
        for alloc in sorted(switch.allocations.values(),
                            key=lambda a: costs[a.circuit_id] / a.ports):
            if freed >= needed:
                break
            freed += alloc.ports
            greedy_cost += costs[alloc.circuit_id]
        if freed < needed:
            continue
        cases += 1
        if greedy_cost > exact.cost + 1e-9:
            worse += 1
            total_excess += greedy_cost - exact.cost
        else:
            equal += 1
    return Point(
        "greedy-preemption-evicts-more-than-it-needs-to",
        EMERGENT,
        cases > 0 and worse > 0,
        f"over {cases} contended cases greedy was more expensive {worse} times and "
        f"tied {equal}; mean excess where it lost {total_excess / max(worse, 1):.2f} "
        f"cost units",
    )


def point_stranded_ports_rise_as_demand_concentrates() -> Point:
    """Fragmentation is about which trunk, not how many ports."""
    switch = OpticalSwitch("hall-a", (
        Trunk("hall-b", 16, 400.0), Trunk("hall-c", 16, 400.0), Trunk("hall-d", 16, 400.0),
    ))
    spread = switch.stranded_ports([
        Request("a", "hall-b", 1600.0), Request("b", "hall-c", 1600.0),
        Request("c", "hall-d", 1600.0),
    ])
    concentrated = switch.stranded_ports([Request("a", "hall-b", 4800.0)])
    return Point(
        "concentrated-demand-strands-more-ports-than-spread-demand",
        EMERGENT,
        concentrated > spread,
        f"{switch.free_ports()} free ports; spread demand strands {spread}, "
        f"the same total width on one trunk strands {concentrated}",
    )


def point_a_faster_loss_trend_never_crosses_later() -> Point:
    rng = random.Random(SEED + 5)
    violations, moved = 0, 0
    for _ in range(POPULATION):
        il_now = rng.uniform(1.0, 16.0)
        budget = il_now + rng.uniform(0.5, 8.0)
        slow = rng.uniform(0.05, 2.0)
        fast = slow * rng.uniform(1.2, 6.0)
        a = forecast("c", il_now, slow, budget).years_to_threshold
        b = forecast("c", il_now, fast, budget).years_to_threshold
        if b > a + 1e-9:
            violations += 1
        if b < a - 1e-9:
            moved += 1
    return Point(
        "a-faster-insertion-loss-trend-never-crosses-later",
        EMERGENT,
        violations == 0 and moved > 0,
        f"{violations} violations over {POPULATION} pairs, {moved} strictly earlier",
    )


def point_debt_is_monotone_while_entries_stay_open() -> Point:
    rng = random.Random(SEED + 6)
    led = Ledger()
    for i in range(40):
        led.open_entry(DebtEntry(
            f"e{i}", f"hall-{i % 4}", rng.choice(list(Cause)),
            opened_at_s=rng.uniform(0, 30 * 86_400.0),
            upfront_accelerator_hours=rng.uniform(0, 500),
            daily_accelerator_hours=rng.uniform(0, 300),
        ))
    totals = [led.total(t * 86_400.0) for t in range(30, 120)]
    violations = sum(1 for a, b in zip(totals, totals[1:]) if b < a - 1e-9)
    return Point(
        "outstanding-debt-never-falls-while-nothing-is-closed",
        EMERGENT,
        violations == 0 and totals[-1] > totals[0],
        f"{violations} violations across 90 days; balance grew from "
        f"{totals[0]:,.0f} to {totals[-1]:,.0f} accelerator-hours",
    )


def point_crossing_checkpoints_never_widens_a_quiet_window() -> Point:
    """A retune cannot hide inside a checkpoint that is using the same circuit."""
    rng = random.Random(SEED + 7)
    violations, moved = 0, 0
    for _ in range(POPULATION):
        rhythm = random_rhythm(rng)
        local = JobRhythm(**{**rhythm.__dict__, "checkpoint_crosses_stitch": False})
        cross = JobRhythm(**{**rhythm.__dict__, "checkpoint_crosses_stitch": True})
        for boundary in Boundary:
            a = local.quiet_window_s(boundary)
            b = cross.quiet_window_s(boundary)
            if b > a + 1e-9:
                violations += 1
            if b < a - 1e-9:
                moved += 1
    return Point(
        "checkpoints-that-cross-the-stitch-never-widen-a-quiet-window",
        EMERGENT,
        violations == 0 and moved > 0,
        f"{violations} violations over {POPULATION * len(Boundary)} comparisons, "
        f"{moved} strictly narrower",
    )


# ==========================================================================
# sanity
# ==========================================================================


def point_thermal_swing_is_linear() -> Point:
    a = thermal_rtt_swing_us(120.0, 25.0)
    b = thermal_rtt_swing_us(240.0, 25.0)
    c = thermal_rtt_swing_us(120.0, 50.0)
    ok = math.isclose(b, 2 * a) and math.isclose(c, 2 * a)
    return Point(
        "the-thermal-delay-model-is-linear-in-distance-and-temperature",
        SANITY, ok,
        f"120 km/25 K gives {a:.4f} us; doubling either doubles it "
        f"({b:.4f}, {c:.4f}), coefficient {THERMAL_DELAY_PS_PER_KM_K:g} ps/km/K",
    )


def point_the_topology_hash_covers_every_declared_field() -> Point:
    base = DeclaredCircuit("c", "hall-a", "hall-b", 800.0, 8.0, 1.2, 3.0, 4)
    changes = {"bw_gbps": 400.0, "rtt_us": 9.0, "path_km": 2.0,
               "il_db": 4.0, "connectors": 6}
    blind = [f for f, v in changes.items()
             if DeclaredCircuit(**{**base.__dict__, f: v}).topology_hash()
             == base.topology_hash()]
    return Point(
        "the-topology-hash-moves-when-any-declared-field-moves",
        SANITY, not blind,
        "every declared field changes the hash" if not blind
        else f"hash is blind to {', '.join(blind)}",
    )


def point_a_wrong_circuit_id_stops_the_comparison() -> Point:
    declared = DeclaredCircuit("c", "hall-a", "hall-b", 800.0, 8.0, 1.2, 3.0)
    report = compare_circuit(declared, MeasuredCircuit("other", 8.0, 3.0, 1e-15, 800.0))
    return Point(
        "a-measurement-of-a-different-circuit-produces-no-field-comparisons",
        SANITY,
        report.verdict.value == "wrong-circuit" and report.fields == (),
        f"verdict {report.verdict.value!r} with {len(report.fields)} field comparisons",
    )


def point_the_ladder_covers_every_boundary() -> Point:
    rows = ladder(reference_rhythm(), 60.0)
    covered = {r.boundary for r in rows}
    return Point(
        "the-ladder-reports-every-boundary-including-the-unselectable-one",
        SANITY,
        covered == set(Boundary),
        f"{len(rows)} rows covering {len(covered)} of {len(Boundary)} boundaries",
    )


def point_mid_collective_is_never_selected() -> Point:
    rng = random.Random(SEED + 8)
    picked = 0
    for _ in range(POPULATION):
        rhythm = random_rhythm(rng)
        retune = rng.uniform(0.0, 1000.0)
        for chooser in (cheapest_legal, soonest_legal):
            chosen = chooser(rhythm, retune)
            if chosen is not None and chosen.boundary is Boundary.MID_COLLECTIVE:
                picked += 1
    return Point(
        "mid-collective-is-shown-but-never-chosen",
        SANITY, picked == 0,
        f"selected {picked} times over {POPULATION * 2} selections",
    )


def point_the_intervals_are_pointwise_correct() -> Point:
    """The decomposition is worth having only if its answer holds at points.

    Probes a spread of interior points across every returned interval, not just
    its midpoint. A midpoint-only version could not tell a correct answer from
    one interval hull thrown over a set with holes in it --- the midpoint of the
    hull still disagrees, and there are no reported gaps left to probe. Interior
    points land in the holes, so they catch it.
    """
    rng = random.Random(SEED + 9)
    inside_wrong, outside_wrong, checked = 0, 0, 0
    interior = [i / 12.0 for i in range(1, 12)]
    for _ in range(POPULATION):
        rhythm = random_rhythm(rng)
        intervals = disagreement_intervals(rhythm)
        if not intervals:
            continue
        for lo, hi in intervals:
            for frac in interior:
                checked += 1
                if objectives_disagree(rhythm, lo + (hi - lo) * frac) is None:
                    inside_wrong += 1
        gaps = [intervals[0][0] * 0.5] + [
            (a_hi + b_lo) / 2.0 for (_, a_hi), (b_lo, _) in zip(intervals, intervals[1:])
        ] + [intervals[-1][1] * 1.5 + 1.0]
        for point in gaps:
            checked += 1
            if objectives_disagree(rhythm, point) is not None:
                outside_wrong += 1
    return Point(
        "every-point-inside-an-interval-disagrees-and-every-gap-agrees",
        SANITY,
        inside_wrong == 0 and outside_wrong == 0 and checked > POPULATION,
        f"{checked} probes across {len(interior)} interior points per interval: "
        f"{inside_wrong} inside an interval that agreed, "
        f"{outside_wrong} outside that disagreed",
    )


def point_aging_partitions_the_balance() -> Point:
    rng = random.Random(SEED + 10)
    led = Ledger()
    for i in range(60):
        led.open_entry(DebtEntry(
            f"e{i}", f"hall-{i % 5}", rng.choice(list(Cause)),
            opened_at_s=rng.uniform(0, 200 * 86_400.0),
            upfront_accelerator_hours=rng.uniform(0, 900),
            daily_accelerator_hours=rng.uniform(0, 120),
        ))
    now = 200 * 86_400.0
    total = led.total(now)
    residual = abs(sum(led.aging(now).values()) - total)
    by_hall = abs(sum(led.by_hall(now).values()) - total)
    by_cause = abs(sum(led.by_cause(now).values()) - total)
    return Point(
        "aging-buckets-hall-and-cause-each-partition-the-balance",
        SANITY,
        max(residual, by_hall, by_cause) < 1e-6,
        f"balance {total:,.0f} accelerator-hours; residuals "
        f"{residual:.2e} / {by_hall:.2e} / {by_cause:.2e}",
    )


def point_worst_first_ranks_by_rate() -> Point:
    led = Ledger()
    led.open_entry(DebtEntry("old-big", "h", Cause.DRIFT, 0.0,
                             upfront_accelerator_hours=1e6))
    led.open_entry(DebtEntry("new-bleeding", "h", Cause.DRIFT, 99 * 86_400.0,
                             daily_accelerator_hours=500.0))
    first = led.worst_first(100 * 86_400.0)[0]
    return Point(
        "the-fix-list-ranks-by-daily-rate-not-by-accrued-total",
        SANITY,
        first.entry_id == "new-bleeding",
        f"first to fix is {first.entry_id!r} "
        f"({first.daily_accelerator_hours:,.0f}/day) rather than the larger old entry",
    )


def point_a_ledger_round_trips() -> Point:
    rng = random.Random(SEED + 11)
    led = Ledger()
    for i in range(25):
        led.open_entry(DebtEntry(
            f"e{i}", f"hall-{i % 3}", rng.choice(list(Cause)),
            opened_at_s=rng.uniform(0, 50 * 86_400.0),
            upfront_accelerator_hours=rng.uniform(0, 400),
            daily_accelerator_hours=rng.uniform(0, 80),
            remediation_accelerator_hours=rng.uniform(0, 900),
        ))
    led.close_entry("e3", 60 * 86_400.0)
    now = 100 * 86_400.0
    back = Ledger.from_json(led.to_json())
    return Point(
        "a-ledger-survives-a-json-round-trip-including-closed-entries",
        SANITY,
        abs(back.total(now) - led.total(now)) < 1e-9
        and len(back.outstanding()) == len(led.outstanding()),
        f"{len(led.entries)} entries, {len(led.outstanding())} open, "
        f"balance {led.total(now):,.2f} reproduced to "
        f"{abs(back.total(now) - led.total(now)):.2e}",
    )


def point_the_intent_digest_ignores_field_order() -> Point:
    one = Intent(Verb.REQUEST, "c", (Endpoint("a", "p1"), Endpoint("b", "p2")),
                 hold_s=1.0, min_bw_gbps=800.0, labels={"x": "1", "y": "2"})
    two = Intent(Verb.REQUEST, "c", (Endpoint("a", "p1"), Endpoint("b", "p2")),
                 hold_s=1.0, min_bw_gbps=800.0, labels={"y": "2", "x": "1"})
    three = Intent(Verb.REQUEST, "c", (Endpoint("a", "p1"), Endpoint("b", "p2")),
                   hold_s=1.0, min_bw_gbps=400.0, labels={"x": "1", "y": "2"})
    return Point(
        "the-intent-digest-is-stable-under-reordering-and-moves-on-content",
        SANITY,
        one.digest() == two.digest() and one.digest() != three.digest(),
        f"reordered labels agree ({one.digest()[:12]}), a bandwidth change does not "
        f"({three.digest()[:12]})",
    )


def point_preemption_respects_priority() -> Point:
    rng = random.Random(SEED + 12)
    violations, plans = 0, 0
    for _ in range(POPULATION):
        switch = OpticalSwitch("hall-a", (Trunk("hall-b", 32, 400.0),))
        for i in range(rng.randint(1, 5)):
            try:
                switch.allocate(Request(f"c{i}", "hall-b",
                                        rng.randint(1, 8) * 400.0,
                                        priority=rng.randint(0, 9)))
            except Exception:
                break
        request = Request("new", "hall-b", rng.randint(1, 32) * 400.0,
                          priority=rng.randint(0, 9))
        plan = preemption_plan(switch, request, lambda a: 1.0)
        if plan is None:
            continue
        plans += 1
        if any(v.priority >= request.priority for v in plan.victims):
            violations += 1
    return Point(
        "preemption-never-evicts-an-equal-or-higher-priority-circuit",
        SANITY,
        violations == 0 and plans > 0,
        f"{violations} violations over {plans} returned plans",
    )


def point_ports_round_up() -> Point:
    trunk = Trunk("hall-b", 32, 400.0)
    cases = {0.0: 0, 1.0: 1, 400.0: 1, 401.0: 2, 800.0: 2, 1201.0: 4}
    wrong = {bw: trunk.ports_for(bw) for bw, want in cases.items()
             if trunk.ports_for(bw) != want}
    return Point(
        "a-partial-port-is-a-whole-port",
        SANITY, not wrong,
        "every case rounds up" if not wrong else f"wrong: {wrong}",
    )


# ==========================================================================
# harness
# ==========================================================================

# ---------------------------------------------------------------------------
# the one measured link
# ---------------------------------------------------------------------------

#: Where ``make data`` puts the fetched files. A module global so a test can
#: point the registry at an empty directory and watch every measured point
#: fail rather than vanish.
HEDGE_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "hedge"
HEDGE_REF = "SOURCES.md S6"

_HEDGE_RUNS: Dict[str, hedge.Run] = {}
_HEDGE_SUMMARIES: Dict[str, hedge.RunSummary] = {}


def hedge_runs() -> Dict[str, hedge.Run]:
    """The three runs, loaded once per registry run.

    ``run_registry`` clears both caches before it starts, so a mutation
    patched into ``ocintent.hedge`` reaches every measured point rather than
    none of them.
    """
    if not _HEDGE_RUNS:
        _HEDGE_RUNS.update({name: hedge.load_run(HEDGE_DATA_DIR, name) for name in hedge.RUNS})
    return _HEDGE_RUNS


def hedge_summaries() -> Dict[str, hedge.RunSummary]:
    if not _HEDGE_SUMMARIES:
        _HEDGE_SUMMARIES.update({n: hedge.summarize(r) for n, r in hedge_runs().items()})
    return _HEDGE_SUMMARIES


HedgeCheck = Callable[[Dict[str, hedge.RunSummary]], Tuple[bool, str]]


def measured(name: str, kind: str, reference: str, check: HedgeCheck) -> Point:
    """A point that reads the measured link.

    Missing or altered files make the point fail, in its own kind, with the
    reason in its detail. It never skips: a registry that dropped its measured
    points when the data was absent would be green for the wrong reason
    (DECISIONS.md D13). A check that raises fails the same way, so a broken
    measured point cannot be re-filed as a sanity failure by the registry's
    catch-all.
    """
    try:
        passed, detail = check(hedge_summaries())
    except hedge.HedgeDataError as exc:
        return Point(name, kind, False, f"no measurement: {exc}", reference)
    except Exception as exc:  # noqa: BLE001 - a raising measured point fails in its own kind
        return Point(name, kind, False, f"raised {type(exc).__name__}: {exc}", reference)
    return Point(name, kind, passed, detail, reference)


def _s(x: Optional[float]) -> str:
    return "never" if x is None else f"{x:.1f} s"


def _all_failures() -> List[Tuple[str, hedge.WavelengthTimeline]]:
    return [(name, tl) for name, s in hedge_summaries().items() for tl in s.failures]


# -- calibrated against what the paper says about its own runs --------------

def point_hedge_the_formats_fail_at_the_published_450_500_and_550_s() -> Point:
    """Attenuation run: 16-QAM near 450 s, 8-QAM near 500 s, PM-QPSK near 550 s.

    Appendix A.5 gives those to the nearest fifty seconds, so each
    wavelength's first uncorrectable-FEC sample is rounded the same way and
    equality is required. The failure marker is the authors' own and the
    precision is the paper's; nothing was chosen after seeing the data
    (DECISIONS.md D14). The paper's 500 s is read off one averaged 8-QAM
    curve; the two 8-QAM wavelengths are rounded separately here, which is
    the stricter reading.
    """
    published = {"16-QAM": 450, "8-QAM": 500, "PM-QPSK": 550}

    def check(summaries: Dict[str, hedge.RunSummary]) -> Tuple[bool, str]:
        run = summaries["prototype"]
        ok, parts = True, []
        for label, expected in published.items():
            for tl in run.by_label(label):
                if not tl.failed:
                    ok = False
                    parts.append(f"{tl.name} never failed")
                    continue
                nearest = int(round(tl.failed_s / 50.0)) * 50
                ok = ok and nearest == expected
                parts.append(f"{tl.name} {tl.failed_s:.1f} s -> {nearest} (published ~{expected})")
        return ok and len(parts) == len(run.labelled()), "; ".join(parts)

    return measured("hedge-the-formats-fail-at-the-published-450-500-and-550-s",
                    CALIBRATED, HEDGE_REF + " (A.5; figure 14c)", check)


def point_hedge_attenuation_traffic_continues_until_pm_qpsk_fails() -> Point:
    """Attenuation run: UDP traffic continues until the PM-QPSK wavelength fails.

    Appendix A.5 and figure 14b. Checked as four facts, to the transponder's
    own resolution: the PM-QPSK failure is the last failure in the run;
    Layer 3 carried traffic until the last poll before its counter moved;
    the outage began within one poll gap of its failure sample, on either
    side; and the link stays dark to the window's end. Within one poll gap,
    because the failure happened somewhere inside the gap between two polls
    and the re-timed iperf intervals resolve the outage to about a second.
    The bound is the data's own resolution, not a tolerance chosen after
    seeing the number (DECISIONS.md D14); where the outage fell relative to
    the alarm sample is printed.
    """
    def check(summaries: Dict[str, hedge.RunSummary]) -> Tuple[bool, str]:
        run = summaries["prototype"]
        pm = run.by_label("PM-QPSK")
        if len(pm) != 1 or not pm[0].failed:
            return False, "expected exactly one failed PM-QPSK wavelength"
        last = pm[0]
        gap = last.failure_poll_gap_s
        lead = run.lead_last_failure_to_link_loss_s
        ok = (run.last_failure_s == last.failed_s
              and run.link_up_throughout(last.cliff_s)
              and lead is not None and abs(lead) <= gap
              and not run.link_up_at_window_end)
        dark = ", ".join(f"{a:.1f}-{_s(b) if b is not None else 'window end'}"
                         for a, b in run.link_down) or "none"
        where = ("never" if lead is None else
                 f"{abs(lead):.1f} s {'after' if lead >= 0 else 'before'} that sample")
        return ok, (f"PM-QPSK failed at {last.failed_s:.1f} s (poll gap {gap:.1f} s), last of "
                    f"{len(run.failures)} failures; layer 3 dark intervals: {dark}; the outage "
                    f"began {where}; up at window end: {run.link_up_at_window_end}")

    return measured("hedge-attenuation-traffic-continues-until-pm-qpsk-fails",
                    CALIBRATED, HEDGE_REF + " (A.5; figure 14b)", check)


def _format_groups(run: hedge.RunSummary):
    q16, q8, qpsk = run.by_label("16-QAM"), run.by_label("8-QAM"), run.by_label("PM-QPSK")
    groups = (q16, q8, qpsk)
    if not all(g and all(t.failed for t in g) for g in groups):
        return None

    def ordered(key: Callable[[hedge.WavelengthTimeline], float]) -> bool:
        return (max(key(t) for t in q16) < min(key(t) for t in q8)
                and max(key(t) for t in q8) < min(key(t) for t in qpsk))

    return groups, ordered


def point_hedge_the_higher_the_format_the_earlier_the_failure_and_the_less_loss() -> Point:
    """Attenuation run: the higher the format, the earlier it fails and the less loss it takes.

    Appendix A.5 says three things: the higher the format, the earlier the
    BER spikes, the earlier it fails, and the less loss it takes. The last
    two are checked here as orderings with nothing to tune: in failure time
    and in received-power drop at the FEC cliff, PM-QPSK sits beyond both
    8-QAM wavelengths and both of those sit beyond 16-QAM. The first needs a
    spike marker the paper does not define, so it is checked under this
    repository's own marker as an emergent point, not a calibrated one.
    """
    def check(summaries: Dict[str, hedge.RunSummary]) -> Tuple[bool, str]:
        found = _format_groups(summaries["prototype"])
        if found is None:
            return False, "not every labelled wavelength failed"
        groups, ordered = found
        failed = ordered(lambda t: t.failed_s)
        loss = ordered(lambda t: t.attenuation_at_cliff_db)
        rows = "; ".join(
            f"{t.name} failed {t.failed_s:.1f} s, {t.attenuation_at_cliff_db:.2f} dB down at the cliff"
            for g in groups for t in g
        )
        return failed and loss, (
            f"ordered 16-QAM < 8-QAM < PM-QPSK in failure: {failed}, "
            f"power drop at cliff: {loss}; {rows}")

    return measured("hedge-the-higher-the-format-the-earlier-the-failure-and-the-less-loss",
                    CALIBRATED, HEDGE_REF + " (A.5; figure 14c)", check)


def point_hedge_bend_red_and_blue_fail_before_green_and_the_link_holds_until_green() -> Point:
    """Bend run: 1567 nm and 1539 nm fail before 1547 nm, and the LAG stays up until 1547 nm fails.

    Section 3.1, Finding 1, and figure 4a/4b. Channel 3 is the notebook's red
    wavelength, 52 its green and 74 its blue. "Stays up until" is read to
    the transponder's resolution, as in the attenuation point: traffic
    continued until the last poll before the green counter moved, and the
    outage began within one poll gap of the green failure sample, on either
    side (DECISIONS.md D14).
    """
    def check(summaries: Dict[str, hedge.RunSummary]) -> Tuple[bool, str]:
        run = summaries["wdl"]
        by_ch = {t.channel: t for t in run.wavelengths}
        red, green, blue = by_ch[3], by_ch[52], by_ch[74]
        if not (red.failed and green.failed and blue.failed):
            return False, "one of the three labelled wavelengths never failed"
        gap = green.failure_poll_gap_s
        lead = None if run.link_lost_s is None else run.link_lost_s - green.failed_s
        ok = (red.failed_s < green.failed_s and blue.failed_s < green.failed_s
              and run.link_up_throughout(green.cliff_s)
              and lead is not None and abs(lead) <= gap)
        where = ("never" if lead is None else
                 f"{abs(lead):.1f} s {'after' if lead >= 0 else 'before'} the green failure sample")
        return ok, (f"failed: {red.name} {red.failed_s:.1f} s, {blue.name} {blue.failed_s:.1f} s, "
                    f"{green.name} {green.failed_s:.1f} s (poll gap {gap:.1f} s); layer 3 dark "
                    f"from {_s(run.link_lost_s)}, {where}")

    return measured("hedge-bend-red-and-blue-fail-before-green-and-the-link-holds-until-green",
                    CALIBRATED, HEDGE_REF + " (3.1 Finding 1; figure 4a/4b)", check)


def point_hedge_bend_released_every_failed_wavelength_restabilises_and_the_link_returns() -> Point:
    """Bend runs: every wavelength that failed re-stabilised and the link came back.

    Section 3.1: "we release the fiber bend and all three wavelengths
    recover"; the figure 4 caption defines re-stabilisation as the dotted
    marker. Checked for both bend runs over the labelled wavelengths: every
    failed one has a re-stabilisation sample inside the window and Layer 3
    is up at the window's end. The first bend run's fourth channel, 84, has
    no label in the notebook and is not one of the paper's three, so what it
    did is printed and not asserted.
    """
    def check(summaries: Dict[str, hedge.RunSummary]) -> Tuple[bool, str]:
        ok, parts = True, []
        for name in ("wdl", "mod_formats"):
            run = summaries[name]
            failures = [t for t in run.failures if t.label is not None]
            unlabelled = [t for t in run.failures if t.label is None]
            recovered = [t for t in failures if t.restabilised_s is not None]
            ok = ok and bool(failures) and len(recovered) == len(failures) and run.link_up_at_window_end
            parts.append(f"{name}: {len(recovered)}/{len(failures)} failed labelled wavelengths "
                         f"re-stabilised (" + ", ".join(f"{t.name} at {_s(t.restabilised_s)}" for t in failures)
                         + f"), link up at window end: {run.link_up_at_window_end}; unlabelled: "
                         + (", ".join(f"{t.name} failed {t.failed_s:.1f} s, re-stabilised "
                                      f"{_s(t.restabilised_s)}" for t in unlabelled) or "none"))
        return ok, "; ".join(parts)

    return measured("hedge-bend-released-every-failed-wavelength-restabilises-and-the-link-returns",
                    CALIBRATED, HEDGE_REF + " (3.1 Finding 1; figure 4 caption)", check)


def point_hedge_format_bend_only_16qam_fails_and_the_link_stays_up() -> Point:
    """Format bend run: 16-QAM is the only wavelength to fail and the link stays up.

    Section 3.1, Finding 2, and figure 4c: the highest format spikes first,
    and past the FEC limit it fails while the lower formats hold. Checked as:
    16-QAM fails, no other wavelength does, and Layer 3 never goes dark. The
    "spikes first" half is not checked. The paper defines no spike marker;
    the window-start marker used elsewhere here exists only for wavelengths
    that failed, and the other candidate, the first sample strictly above
    the start, reads noise on this run (the emergent onset point prints what
    both markers give on every wavelength).
    """
    def check(summaries: Dict[str, hedge.RunSummary]) -> Tuple[bool, str]:
        run = summaries["mod_formats"]
        q16 = run.by_label("16-QAM")
        others = [t for t in run.labelled() if t.label != "16-QAM"]
        if len(q16) != 1 or not others:
            return False, "expected one 16-QAM wavelength and at least one other"
        t16 = q16[0]
        ok = (t16.failed and not any(t.failed for t in others) and not run.link_down)
        return ok, (f"16-QAM failed at {_s(t16.failed_s)} (onset {_s(t16.onset_s)}); "
                    + ", ".join(f"{t.name} {'failed' if t.failed else 'held'}" for t in others)
                    + f"; layer 3 dark intervals in the window: {len(run.link_down)}")

    return measured("hedge-format-bend-only-16qam-fails-and-the-link-stays-up",
                    CALIBRATED, HEDGE_REF + " (3.1 Finding 2; figure 4c)", check)


# -- emergent: what the raw files say that the paper did not quote ---------

def point_hedge_the_first_fec_alarm_precedes_link_loss_in_every_run_that_lost_it() -> Point:
    """In every run that lost the link, the first uncorrectable-FEC sample came before Layer 3 went dark.

    The lead is printed, not asserted against a number. A run that never lost
    the link contributes nothing, and the point fails if no run did, so it
    cannot pass vacuously.
    """
    def check(summaries: Dict[str, hedge.RunSummary]) -> Tuple[bool, str]:
        lost = [(n, s) for n, s in summaries.items() if s.link_lost_s is not None]
        leads = [(n, s.lead_first_failure_to_link_loss_s) for n, s in lost]
        ok = bool(lost) and all(lead is not None and lead > 0 for _, lead in leads)
        return ok, (f"{len(lost)} of {len(summaries)} runs lost the link; first FEC alarm led "
                    "the outage by " + ", ".join(f"{lead:.1f} s ({n})" for n, lead in leads))

    return measured("hedge-the-first-fec-alarm-precedes-link-loss-in-every-run-that-lost-it",
                    EMERGENT, "-", check)


def point_hedge_the_outage_began_within_one_poll_gap_of_the_last_failure_not_the_first() -> Point:
    """Layer 3 went dark within one poll gap of the last wavelength's failure, and long after the first.

    In every run that lost the link, the outage begins within one
    transponder poll of the last failure sample, on either side, and the
    first failure led it by more than that. Both runs put the outage inside
    the poll interval in which the last wavelength failed, so "the link
    followed the last wavelength" is true to the data's resolution and no
    finer; the earlier reading of these logs, which put the outage whole
    seconds after the last failure, was an artefact of stamps that trail
    their intervals (DECISIONS.md D14). This is the shape an aggregated link
    should have, and it is what makes the first alarm's lead a warning
    rather than a countdown. It is the shape of the published runs, not a
    law: outside the authors' windows the format-bend file holds a later
    disturbance the paper does not describe, in which the link went dark
    more than a poll before the last wavelength's alarm sample (the
    experiment prints it, and the mutation that removes the windows
    records that this point goes red).
    """
    def check(summaries: Dict[str, hedge.RunSummary]) -> Tuple[bool, str]:
        lost = [(n, s) for n, s in summaries.items() if s.link_lost_s is not None]
        ok = bool(lost)
        parts = []
        for n, s in lost:
            first = s.lead_first_failure_to_link_loss_s
            last = s.lead_last_failure_to_link_loss_s
            gap = s.last_failure_poll_gap_s
            ok = ok and None not in (first, last, gap) and abs(last) <= gap < first
            parts.append(f"{n}: the outage began {abs(last):.1f} s "
                         f"{'after' if last >= 0 else 'before'} the last failure sample "
                         f"(poll gap {gap:.1f} s) and {first:.1f} s after the first")
        return ok, "; ".join(parts) or "no run lost the link"

    return measured("hedge-the-outage-began-within-one-poll-gap-of-the-last-failure-not-the-first",
                    EMERGENT, "-", check)


def point_hedge_ber_rises_across_more_than_one_poll_before_every_fec_change() -> Point:
    """Every FEC failure was preceded by a visible BER rise: the cliff BER exceeds the start BER and the rise spans more than one poll.

    The onset marker is the last pre-failure sample at or below the
    window-start BER. Requiring at least one sample strictly between onset
    and failure means the rise was visible in a poll before the counter
    moved; the lead in seconds is printed for each wavelength.
    """
    def check(summaries: Dict[str, hedge.RunSummary]) -> Tuple[bool, str]:
        runs = hedge_runs()
        ok, parts, leads = True, [], []
        for name, tl in _all_failures():
            raw = runs[name].wavelength(tl.channel).samples
            between = sum(1 for s in raw if tl.onset_s < s.t_s < tl.failed_s)
            rose = tl.ber_at_cliff > tl.ber_at_start
            ok = ok and rose and between >= 1
            leads.append(tl.onset_lead_s)
            parts.append(f"{name} {tl.name} lead {tl.onset_lead_s:.1f} s over {between} polls")
        ok = ok and bool(parts)
        return ok, (f"{len(parts)} failures; onset leads {min(leads):.1f}-{max(leads):.1f} s; "
                    + "; ".join(parts))

    return measured("hedge-ber-rises-across-more-than-one-poll-before-every-fec-change",
                    EMERGENT, "-", check)


def _first_strict_rise_s(samples: Sequence[hedge.Sample]) -> Optional[float]:
    """The other candidate onset marker: the first sample strictly above the window-start BER."""
    start = samples[0].ber
    for sample in samples:
        if sample.ber > start:
            return sample.t_s
    return None


def point_hedge_the_window_start_onset_marker_orders_the_formats_16qam_first() -> Point:
    """Attenuation run: under the window-start onset marker, the BER onsets are ordered 16-QAM, then 8-QAM, then PM-QPSK.

    Appendix A.5 says the higher the format, the earlier the BER spikes,
    but defines no spike marker, so the ordering cannot be calibrated
    against the paper. The marker used here is the last pre-failure sample
    at or below the window-start BER (DECISIONS.md D14); it has no
    parameter, and it is a choice. The obvious alternative, the first sample
    strictly above the start, is printed beside it for both the attenuation
    run and the format bend run and is not asserted: on a noisy error rate
    it reads the poll after the first, on every wavelength. Emergent because
    the marker is this repository's, not the paper's.
    """
    def check(summaries: Dict[str, hedge.RunSummary]) -> Tuple[bool, str]:
        found = _format_groups(summaries["prototype"])
        if found is None:
            return False, "not every labelled wavelength failed"
        groups, ordered = found
        runs = hedge_runs()
        strict = {t.channel: _first_strict_rise_s(runs["prototype"].wavelength(t.channel).samples)
                  for g in groups for t in g}
        marker = ordered(lambda t: t.onset_s)
        alternative = ordered(lambda t: math.inf if strict[t.channel] is None else strict[t.channel])
        rows = "; ".join(f"{t.name} onset {t.onset_s:.1f} s, first strict rise {_s(strict[t.channel])}"
                         for g in groups for t in g)
        bend = summaries["mod_formats"]
        bend_rows = ", ".join(
            f"{t.name} {_s(_first_strict_rise_s(runs['mod_formats'].wavelength(t.channel).samples))}"
            f"{' (onset ' + _s(t.onset_s) + ')' if t.onset_s is not None else ''}"
            for t in bend.labelled())
        return marker, (f"ordered 16-QAM < 8-QAM < PM-QPSK under the window-start marker: {marker}; "
                        f"under the first-strict-rise marker: {alternative}; {rows}; format bend "
                        f"run first strict rises: {bend_rows}")

    return measured("hedge-the-window-start-onset-marker-orders-the-formats-16qam-first",
                    EMERGENT, "-", check)


def point_hedge_the_fec_cliff_sits_decades_above_the_forecast_default_target() -> Point:
    """The measured pre-FEC BER at every cliff is orders above the forecast's default target and the Q=6 anchor.

    The drift forecast defaults to a target of 1e-12, the figure a
    direct-detection receiver is specified to; a coherent transponder runs
    to its FEC limit instead. The smallest cliff BER across the nine
    failures is compared with both, and the gap is printed in decades.
    """
    def check(summaries: Dict[str, hedge.RunSummary]) -> Tuple[bool, str]:
        cliffs = [tl.ber_at_cliff for _, tl in _all_failures()]
        if not cliffs:
            return False, "no failure to read a cliff from"
        default_target = DEFAULT_TARGET_BER
        anchor = ber_from_q(6.0)
        lo, hi = min(cliffs), max(cliffs)
        if lo <= 0.0 or anchor <= 0.0 or default_target <= 0.0:
            return False, f"a cliff BER of {lo:.2e} is not a cliff"
        ok = lo > anchor > default_target
        return ok, (f"cliff BER {lo:.2e}-{hi:.2e} over {len(cliffs)} failures; "
                    f"{math.log10(lo / anchor):.1f} decades above the Q=6 anchor "
                    f"({anchor:.1e}) and {math.log10(lo / default_target):.1f} decades above "
                    f"the forecast default target ({default_target:.0e})")

    return measured("hedge-the-fec-cliff-sits-decades-above-the-forecast-default-target",
                    EMERGENT, "-", check)


def point_hedge_every_failing_wavelength_scales_closer_to_coherent_than_direct() -> Point:
    """On every wavelength that failed, the measured power drop between BER onset and the cliff is closer to the 20 dB per decade branch than to the 10.

    The drift model shipped with the thermal-noise-limited direct-detection
    relation, 10 dB per decade of Q; an amplified link runs OSNR-limited,
    where Q squared follows the signal-to-noise ratio and a decade of Q costs
    20 dB. Converting each wavelength's onset and cliff BERs to Q and asking
    how many decibels each branch predicts between them, the measured drop
    is nearer the 20 dB branch every time, and steeper than both: the slope
    the data itself implies is printed per wavelength. "Closer" is the whole
    claim; the measurement does not match either branch. That is why the
    model now takes a ``detection`` argument (DECISIONS.md D15); the default
    stays direct because the anchors are written against it.
    """
    def check(summaries: Dict[str, hedge.RunSummary]) -> Tuple[bool, str]:
        checks = [(name, hedge.scaling_check(tl)) for name, tl in _all_failures()]
        checks = [(n, c) for n, c in checks if c is not None]
        tally = {"coherent": 0, "direct": 0, "neither": 0}
        for _, c in checks:
            tally[c.closer] += 1
        steeper = sum(1 for _, c in checks if c.steeper_than_both)
        ok = bool(checks) and tally["coherent"] == len(checks)
        rows = "; ".join(f"{n} ch{c.channel} {c.label or '(unlabelled)'}: measured {c.measured_db:.1f} dB "
                         f"over {c.decades_of_q:.2f} decades of Q, {c.implied_db_per_decade:.1f} dB "
                         f"per decade; direct {c.direct_db:.1f}, coherent {c.coherent_db:.1f}"
                         for n, c in checks)
        return ok, (f"closer to coherent on {tally['coherent']}/{len(checks)}, direct on "
                    f"{tally['direct']}, neither on {tally['neither']}; steeper than both on "
                    f"{steeper}/{len(checks)}; {rows}")

    return measured("hedge-every-failing-wavelength-scales-closer-to-coherent-than-direct",
                    EMERGENT, "-", check)


# -- sanity: this repository's own reading of the files -------------------

def point_hedge_every_pinned_file_is_present_and_matches_its_sha256() -> Point:
    """Fifteen files, three runs by five, each matching its pinned SHA-256."""
    expected = len(hedge.RUNS) * len(hedge.FILES)
    problems = hedge.verify(HEDGE_DATA_DIR)
    ok = len(hedge.MANIFEST) == expected and not problems
    detail = (f"{len(hedge.MANIFEST)} pins for {expected} files under {HEDGE_DATA_DIR.name}/; "
              + ("all present and matching" if not problems else
                 "problems: " + ", ".join(f"{p.path} ({p.what})" for p in problems)))
    return Point("hedge-every-pinned-file-is-present-and-matches-its-sha256", SANITY, ok, detail)


def point_hedge_the_four_servers_re_timing_anchors_agree_within_one_report_interval() -> Point:
    """In every run, the four server logs' re-timing anchors agree within one report interval.

    Each log is re-timed on its own: its anchor is the least-late line's
    stamp minus that line's interval end (DECISIONS.md D14). The four
    servers took one client's streams over one link, so their interval
    clocks should sit at one offset from the run's clock, and if re-timing
    were reading noise the four anchors would scatter. The bound is one
    report interval, fixed before the comparison ran; the measured spread
    is printed in milliseconds, with how late the stamps were.
    """
    bound = hedge.REPORT_INTERVAL_S

    def check(summaries: Dict[str, hedge.RunSummary]) -> Tuple[bool, str]:
        ok, parts, worst = True, [], 0.0
        for run_name, s in summaries.items():
            anchors = [rt.anchor_s for rt in s.retiming]
            spread = max(anchors) - min(anchors)
            worst = max(worst, spread)
            ok = ok and len(anchors) == len(hedge.FILES) - 1 and spread <= bound
            late = sum(rt.late_lines for rt in s.retiming)
            lines = sum(rt.lines for rt in s.retiming)
            parts.append(f"{run_name}: anchors {min(anchors):.3f} to {max(anchors):.3f} s, spread "
                         f"{spread * 1000:.0f} ms; {late}/{lines} interval lines were stamped more "
                         f"than {bound:g} s after their interval, the latest by "
                         f"{max(rt.max_late_s for rt in s.retiming):.1f} s")
        return ok, (f"largest anchor spread {worst * 1000:.0f} ms against a bound of {bound:g} s; "
                    + "; ".join(parts))

    return measured("hedge-the-four-servers-re-timing-anchors-agree-within-one-report-interval",
                    SANITY, "-", check)


def authors_link_down(run: hedge.Run) -> Tuple[Tuple[float, Optional[float]], ...]:
    """The notebooks' Layer-3 reduction, for comparison with the event join.

    The authors align the four server logs by report index, sum the i-th
    intervals, and call the link down where the sum is zero, stamping the
    state with server 1's timestamps. Here both joins use the re-timed
    interval starts, so the comparison is between the two reductions and not
    between two clocks. Clipped to the run's window the same way
    ``link_down_intervals`` clips.
    """
    n = min(len(s) for s in run.servers)
    lo, hi = run.window
    out: List[Tuple[float, Optional[float]]] = []
    since: Optional[float] = None
    for i in range(n):
        total = sum(s[i].gbps for s in run.servers)
        t = run.servers[0][i].start_s
        if total <= 0.0 and since is None:
            since = t
        elif total > 0.0 and since is not None:
            if since <= hi and t >= lo:
                out.append((max(since, lo), min(t, hi)))
            since = None
    if since is not None and since <= hi:
        out.append((max(since, lo), None))
    return tuple(out)


def point_hedge_the_event_join_agrees_with_the_authors_index_aligned_reduction() -> Point:
    """The event-driven Layer-3 join and the authors' index-aligned one agree at every transition.

    Each method stamps a transition with one server's re-timed interval
    start. The authors' notebooks align the four logs by report index and
    sum the i-th intervals; every log is a contiguous run of one-second
    intervals, so once the stamps are re-timed the servers' i-th reports
    coincide to within the anchors' spread, milliseconds, and the two joins
    should agree to that. The bound is two report intervals, one for a
    possible index misalignment and one of interval jitter; it was fixed
    before the comparison ran and is not adjusted to the data. Before
    re-timing, the raw stamps' i-th spread reached several seconds in the
    bend runs (DECISIONS.md D14). The largest disagreement and the spread
    statistics are printed.
    """
    bound = 2 * hedge.REPORT_INTERVAL_S
    name = "hedge-the-event-join-agrees-with-the-authors-index-aligned-reduction"
    try:
        runs = hedge_runs()
        summaries = hedge_summaries()
    except hedge.HedgeDataError as exc:
        return Point(name, SANITY, False, f"no measurement: {exc}")
    ok, parts, worst = True, [], 0.0
    for run_name, run in runs.items():
        mine, theirs = summaries[run_name].link_down, authors_link_down(run)
        n = min(len(s) for s in run.servers)
        lo, hi = run.window
        spreads = sorted(
            max(s[i].t_s for s in run.servers) - min(s[i].t_s for s in run.servers)
            for i in range(n) if lo <= run.servers[0][i].t_s <= hi
        )
        spread_note = (f"i-th report spread median {spreads[len(spreads) // 2] * 1000:.0f} ms, "
                       f"max {spreads[-1] * 1000:.0f} ms" if spreads else "no reports in the window")
        if len(mine) != len(theirs):
            ok = False
            parts.append(f"{run_name}: {len(mine)} dark intervals by event join, "
                         f"{len(theirs)} by index; {spread_note}")
            continue
        gaps = [0.0]
        for (a, b), (c, d) in zip(mine, theirs):
            if (b is None) != (d is None):
                ok = False
            gaps.append(abs(a - c))
            if b is not None and d is not None:
                gaps.append(abs(b - d))
        worst = max(worst, max(gaps))
        ok = ok and max(gaps) <= bound
        parts.append(f"{run_name}: {len(mine)} dark interval(s), stamps within {max(gaps):.1f} s; "
                     f"{spread_note}")
    return Point(name, SANITY, ok,
                 f"largest transition disagreement {worst:.1f} s against a bound of {bound:.0f} s; "
                 + "; ".join(parts))


REGISTRY: Tuple[Callable[[], Point], ...] = (
    point_q_six_is_the_published_1e9,
    point_q_seven_is_the_published_value,
    point_published_ocs_switching_is_below_every_disagreement_band,
    point_the_objectives_disagree_for_most_rhythms,
    point_the_disagreement_set_is_often_not_one_interval,
    point_the_cheapest_durable_strategy_flips_with_circuit_width,
    point_the_fastest_strategy_is_not_the_cheapest,
    point_a_wider_stitch_never_hurts,
    point_exact_preemption_beats_greedy,
    point_stranded_ports_rise_as_demand_concentrates,
    point_a_faster_loss_trend_never_crosses_later,
    point_debt_is_monotone_while_entries_stay_open,
    point_crossing_checkpoints_never_widens_a_quiet_window,
    point_thermal_swing_is_linear,
    point_the_topology_hash_covers_every_declared_field,
    point_a_wrong_circuit_id_stops_the_comparison,
    point_the_ladder_covers_every_boundary,
    point_mid_collective_is_never_selected,
    point_the_intervals_are_pointwise_correct,
    point_aging_partitions_the_balance,
    point_worst_first_ranks_by_rate,
    point_a_ledger_round_trips,
    point_the_intent_digest_ignores_field_order,
    point_preemption_respects_priority,
    point_ports_round_up,
    # the one measured link
    point_hedge_the_formats_fail_at_the_published_450_500_and_550_s,
    point_hedge_attenuation_traffic_continues_until_pm_qpsk_fails,
    point_hedge_the_higher_the_format_the_earlier_the_failure_and_the_less_loss,
    point_hedge_bend_red_and_blue_fail_before_green_and_the_link_holds_until_green,
    point_hedge_bend_released_every_failed_wavelength_restabilises_and_the_link_returns,
    point_hedge_format_bend_only_16qam_fails_and_the_link_stays_up,
    point_hedge_the_first_fec_alarm_precedes_link_loss_in_every_run_that_lost_it,
    point_hedge_the_outage_began_within_one_poll_gap_of_the_last_failure_not_the_first,
    point_hedge_ber_rises_across_more_than_one_poll_before_every_fec_change,
    point_hedge_the_window_start_onset_marker_orders_the_formats_16qam_first,
    point_hedge_the_fec_cliff_sits_decades_above_the_forecast_default_target,
    point_hedge_every_failing_wavelength_scales_closer_to_coherent_than_direct,
    point_hedge_every_pinned_file_is_present_and_matches_its_sha256,
    point_hedge_the_four_servers_re_timing_anchors_agree_within_one_report_interval,
    point_hedge_the_event_join_agrees_with_the_authors_index_aligned_reduction,
)


def run_registry() -> List[Point]:
    """Run every point. A point that raises becomes a failing point.

    An exception used to abort the run and hide every later result, which meant
    one broken check could make a red registry look like a crash and a crash
    look like nothing at all.
    """
    _HEDGE_RUNS.clear()
    _HEDGE_SUMMARIES.clear()
    results: List[Point] = []
    for check in REGISTRY:
        try:
            results.append(check())
        except Exception as exc:  # noqa: BLE001 - a raising point is a failing point
            results.append(Point(
                check.__name__.removeprefix("point_").replace("_", "-"),
                SANITY, False, f"raised {type(exc).__name__}: {exc}",
            ))
    return results


def main() -> int:
    print("=" * 78)
    print("WHAT THIS REGISTRY DOES NOT CHECK")
    print("=" * 78)
    for i, item in enumerate(DECLINED, 1):
        print(f"{i:2d}. {item}")
    print()

    results = run_registry()
    by_kind = {CALIBRATED: [], EMERGENT: [], SANITY: []}
    for point in results:
        by_kind[point.kind].append(point)

    for kind in (CALIBRATED, EMERGENT, SANITY):
        points = by_kind[kind]
        print("=" * 78)
        print(f"{kind.upper()}  ({len(points)} points)")
        print("=" * 78)
        for point in points:
            mark = "PASS" if point.passed else "FAIL"
            print(f"[{mark}] {point.name}")
            print(f"       {point.detail}")
            print(f"       ref: {point.reference}")
        print()

    failed = [p for p in results if not p.passed]
    print("=" * 78)
    print(f"{len(results) - len(failed)}/{len(results)} points pass  "
          f"({len(by_kind[CALIBRATED])} calibrated, {len(by_kind[EMERGENT])} emergent, "
          f"{len(by_kind[SANITY])} sanity, {len(DECLINED)} declined)")
    if failed:
        print("failing: " + ", ".join(p.name for p in failed))
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
