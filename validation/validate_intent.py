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
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, "src")

from ocintent.checkpoint import (  # noqa: E402
    CheckpointPlan,
    Strategy,
    cheapest_durable,
)
from ocintent.checkpoint import compare as compare_strategies  # noqa: E402
from ocintent.checkpoint import tax  # noqa: E402
from ocintent.drift import (  # noqa: E402
    THERMAL_DELAY_PS_PER_KM_K,
    DeclaredCircuit,
    MeasuredCircuit,
    ber_from_q,
    forecast,
    thermal_rtt_swing_us,
)
from ocintent.drift import compare as compare_circuit  # noqa: E402
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
    "No measured plant. Every number here is a model output. Nothing has been "
    "compared against a real optical switch, a real ROADM, or a real metro span.",
    "The two calibrated points pin ONE published relation at two places. They are "
    "not two independent anchors, and a systematic error in that relation would "
    "leave both of them green.",
    "The margin-to-Q relation is first order. It ignores chromatic dispersion, "
    "fibre nonlinearity and every amplifier's noise contribution, and it is not "
    "calibrated against any particular receiver. It gets the shape of the cliff "
    "right and should not be read as predicting a number for a given link.",
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
)


def run_registry() -> List[Point]:
    """Run every point. A point that raises becomes a failing point.

    An exception used to abort the run and hide every later result, which meant
    one broken check could make a red registry look like a crash and a crash
    look like nothing at all.
    """
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
