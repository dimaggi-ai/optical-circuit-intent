"""Behaviour tests for the six models.

Organised by module. Where a test encodes a judgement call rather than a fact
--- which boundary wins, what counts as stranded --- the docstring says so, so a
reader who disagrees knows which test to change.
"""

from __future__ import annotations

import json
import math

import pytest

from ocintent.checkpoint import (
    CheckpointPlan,
    StallCause,
    StallEvidence,
    Strategy,
    cheapest_durable,
    classify_stall,
)
from ocintent.checkpoint import compare as compare_strategies
from ocintent.checkpoint import tax
from ocintent.drift import (
    THERMAL_DELAY_PS_PER_KM_K,
    DeclaredCircuit,
    DriftVerdict,
    MeasuredCircuit,
    ber_from_margin_db,
    ber_from_q,
    forecast,
    q_from_margin_db,
    thermal_rtt_swing_us,
)
from ocintent.drift import compare as compare_circuit
from ocintent.intent import Boundary, Endpoint, Intent, Verb, compile_intent
from ocintent.ledger import (
    Cause,
    DebtEntry,
    Ledger,
    debt_from_drift,
    debt_from_stranded_ports,
)
from ocintent.legality import (
    JobRhythm,
    Legality,
    assess,
    cheapest_legal,
    disagreement_intervals,
    disagreement_width_s,
    ladder,
    objectives_disagree,
    soonest_legal,
)
from ocintent.radix import (
    Allocation,
    OpticalSwitch,
    RadixExhausted,
    Request,
    Trunk,
    preemption_plan,
)

DAY = 86_400.0


def reference_rhythm(**over) -> JobRhythm:
    base = dict(
        accelerators=16_384,
        step_s=2.4,
        cross_stitch_collective_s=0.31,
        steps_per_checkpoint=250,
        checkpoint_window_s=120.0,
        checkpoint_crosses_stitch=False,
        steps_per_epoch=4_000,
        epoch_gap_s=45.0,
    )
    base.update(over)
    return JobRhythm(**base)


def a_request_intent(**over) -> Intent:
    base = dict(
        verb=Verb.REQUEST,
        circuit_id="stitch-ab-1",
        endpoints=(Endpoint("hall-a", "ocs-1/4"), Endpoint("hall-b", "ocs-3/9")),
        min_bw_gbps=800.0,
        hold_s=6 * 3600.0,
        job_id="pretrain-7",
    )
    base.update(over)
    return Intent(**base)


# ==========================================================================
# intent
# ==========================================================================


class TestIntent:
    def test_a_request_without_a_hold_is_refused(self):
        """A circuit with no stated duration is one the scheduler cannot plan around."""
        with pytest.raises(ValueError, match="how long"):
            a_request_intent(hold_s=None)

    def test_a_request_needs_two_endpoints(self):
        with pytest.raises(ValueError):
            a_request_intent(endpoints=(Endpoint("hall-a", "ocs-1/4"),))

    def test_a_circuit_cannot_start_and_end_in_one_hall(self):
        with pytest.raises(ValueError):
            a_request_intent(
                endpoints=(Endpoint("hall-a", "ocs-1/4"), Endpoint("hall-a", "ocs-1/5"))
            )

    def test_a_failover_names_what_it_replaces(self):
        with pytest.raises(ValueError, match="replaces"):
            Intent(
                verb=Verb.FAILOVER_TO,
                circuit_id="stitch-ac-1",
                endpoints=(Endpoint("hall-a", "p1"), Endpoint("hall-c", "p2")),
                min_bw_gbps=800.0,
                hold_s=3600.0,
            )

    def test_a_release_needs_no_endpoints(self):
        intent = Intent(verb=Verb.RELEASE, circuit_id="stitch-ab-1")
        assert compile_intent(intent).operations

    def test_the_digest_is_stable_across_field_order(self):
        one = a_request_intent(labels={"a": "1", "b": "2"})
        two = a_request_intent(labels={"b": "2", "a": "1"})
        assert one.digest() == two.digest()

    def test_the_digest_changes_when_bandwidth_changes(self):
        assert a_request_intent().digest() != a_request_intent(min_bw_gbps=400.0).digest()

    def test_an_intent_round_trips_through_json(self):
        intent = a_request_intent()
        assert Intent.from_dict(json.loads(json.dumps(intent.to_dict()))) == intent

    def test_a_request_compiles_to_reserve_connect_verify(self):
        ops = [o.call for o in compile_intent(a_request_intent()).operations]
        assert ops == ["reserve_ports", "cross_connect", "verify_path"]

    def test_a_failover_verifies_the_replacement_before_teardown(self):
        """The reverse order turns a degraded circuit into no circuit."""
        intent = Intent(
            verb=Verb.FAILOVER_TO,
            circuit_id="stitch-ac-1",
            endpoints=(Endpoint("hall-a", "p1"), Endpoint("hall-c", "p2")),
            min_bw_gbps=800.0,
            hold_s=3600.0,
            replaces="stitch-ab-1",
        )
        ops = [o.call for o in compile_intent(intent).operations]
        assert ops.index("verify_path") < ops.index("teardown")

    def test_taking_a_path_away_is_disruptive_and_adding_one_is_not(self):
        assert Intent(verb=Verb.RELEASE, circuit_id="c").disruptive
        assert not a_request_intent().disruptive

    def test_the_plan_carries_the_boundary_it_was_compiled_for(self):
        plan = compile_intent(a_request_intent(), boundary=Boundary.BETWEEN_EPOCHS)
        assert plan.boundary is Boundary.BETWEEN_EPOCHS

    def test_no_operation_names_a_vendor(self):
        """DECISIONS.md D2: plans are returned, not executed, and stay generic."""
        vendors = ("calient", "polatis", "ciena", "infinera", "juniper", "arista", "nokia")
        for verb, kwargs in (
            (Verb.REQUEST, {}),
            (Verb.RELEASE, {"endpoints": (), "min_bw_gbps": 0.0, "hold_s": None}),
        ):
            intent = a_request_intent(verb=verb, **kwargs)
            blob = json.dumps([{"call": o.call, "args": o.args}
                               for o in compile_intent(intent).operations]).lower()
            assert not any(v in blob for v in vendors)


# ==========================================================================
# legality
# ==========================================================================


class TestLegality:
    def test_mid_collective_is_fatal_past_the_timeout(self):
        cost = assess(reference_rhythm(), 700.0, Boundary.MID_COLLECTIVE)
        assert cost.legality is Legality.FATAL

    def test_a_retune_inside_the_step_gap_is_invisible(self):
        rhythm = reference_rhythm()
        quiet = rhythm.quiet_window_s(Boundary.BETWEEN_STEPS)
        assert assess(rhythm, quiet * 0.5, Boundary.BETWEEN_STEPS).legality is Legality.INVISIBLE

    def test_a_retune_longer_than_the_quiet_window_stalls(self):
        rhythm = reference_rhythm()
        quiet = rhythm.quiet_window_s(Boundary.BETWEEN_STEPS)
        cost = assess(rhythm, quiet + 10.0, Boundary.BETWEEN_STEPS)
        assert cost.legality is Legality.STALL
        assert cost.stall_s == pytest.approx(10.0)

    def test_between_jobs_has_an_unbounded_quiet_window(self):
        assert math.isinf(reference_rhythm().quiet_window_s(Boundary.BETWEEN_JOBS))

    def test_the_checkpoint_window_shrinks_when_checkpoints_cross_the_stitch(self):
        """A retune cannot hide inside a checkpoint that is itself using the circuit."""
        local = reference_rhythm(checkpoint_crosses_stitch=False)
        crossing = reference_rhythm(checkpoint_crosses_stitch=True)
        assert (crossing.quiet_window_s(Boundary.BETWEEN_CHECKPOINTS)
                < local.quiet_window_s(Boundary.BETWEEN_CHECKPOINTS))

    def test_the_ladder_covers_every_boundary(self):
        assert len(ladder(reference_rhythm(), 60.0)) == len(Boundary)

    def test_mid_collective_is_never_selected(self):
        """It is legal only for an instantaneous change, which no plant offers."""
        for retune in (0.0, 0.01, 1.0, 60.0):
            for pick in (cheapest_legal, soonest_legal):
                chosen = pick(reference_rhythm(), retune)
                assert chosen is None or chosen.boundary is not Boundary.MID_COLLECTIVE

    def test_cheapest_minimises_accelerator_hours(self):
        rhythm = reference_rhythm()
        chosen = cheapest_legal(rhythm, 60.0)
        others = [c for c in ladder(rhythm, 60.0)
                  if c.boundary is not Boundary.MID_COLLECTIVE]
        assert chosen.lost_accelerator_hours == min(c.lost_accelerator_hours for c in others)

    def test_soonest_minimises_total_delay(self):
        rhythm = reference_rhythm()
        chosen = soonest_legal(rhythm, 60.0)
        others = [c for c in ladder(rhythm, 60.0)
                  if c.boundary is not Boundary.MID_COLLECTIVE]
        assert chosen.total_delay_s == min(c.total_delay_s for c in others)

    def test_the_two_objectives_disagree_at_the_reference_rhythm(self):
        """The headline W4 result. If this goes quiet the finding is gone."""
        assert objectives_disagree(reference_rhythm(), 60.0) is not None

    def test_a_crossing_checkpoint_widens_the_gap_by_orders_of_magnitude(self):
        local = objectives_disagree(reference_rhythm(checkpoint_crosses_stitch=False), 60.0)
        crossing = objectives_disagree(reference_rhythm(checkpoint_crosses_stitch=True), 60.0)
        assert crossing["extra_wait_s"] > 100 * local["extra_wait_s"]

    def test_the_disagreement_set_is_reported_as_exact_intervals(self):
        intervals = disagreement_intervals(reference_rhythm())
        assert intervals and all(lo < hi for lo, hi in intervals)

    def test_the_intervals_are_disjoint_and_ordered(self):
        intervals = disagreement_intervals(reference_rhythm(step_s=1.1, epoch_gap_s=8.0))
        for (a_lo, a_hi), (b_lo, b_hi) in zip(intervals, intervals[1:]):
            assert a_hi < b_lo

    def test_a_point_inside_an_interval_really_disagrees(self):
        """The decomposition is only worth having if its answer holds pointwise."""
        rhythm = reference_rhythm()
        for lo, hi in disagreement_intervals(rhythm):
            mid = (lo + hi) / 2.0
            assert objectives_disagree(rhythm, mid) is not None

    def test_a_point_outside_every_interval_agrees(self):
        rhythm = reference_rhythm()
        intervals = disagreement_intervals(rhythm)
        outside = [intervals[0][0] * 0.5, intervals[-1][1] + 1.0]
        for retune in outside:
            assert objectives_disagree(rhythm, retune) is None

    def test_the_width_is_the_sum_of_the_intervals(self):
        rhythm = reference_rhythm()
        total = sum(hi - lo for lo, hi in disagreement_intervals(rhythm))
        assert disagreement_width_s(rhythm) == pytest.approx(total)

    def test_a_rhythm_needs_a_positive_step(self):
        with pytest.raises(ValueError):
            reference_rhythm(step_s=0.0)

    def test_a_collective_cannot_outlast_its_step(self):
        with pytest.raises(ValueError):
            reference_rhythm(cross_stitch_collective_s=3.0)


# ==========================================================================
# radix
# ==========================================================================


def a_switch(**over) -> OpticalSwitch:
    return OpticalSwitch(
        hall_id=over.get("hall_id", "hall-a"),
        trunks=over.get("trunks", (
            Trunk("hall-b", 32, 400.0),
            Trunk("hall-c", 8, 400.0),
        )),
    )


class TestRadix:
    def test_ports_round_up(self):
        assert Trunk("hall-b", 32, 400.0).ports_for(401.0) == 2

    def test_a_zero_bandwidth_request_needs_no_ports(self):
        assert Trunk("hall-b", 32, 400.0).ports_for(0.0) == 0

    def test_two_trunks_to_one_hall_are_refused(self):
        with pytest.raises(ValueError, match="merge"):
            a_switch(trunks=(Trunk("hall-b", 8, 400.0), Trunk("hall-b", 8, 400.0)))

    def test_a_trunk_cannot_lead_back_to_its_own_hall(self):
        with pytest.raises(ValueError):
            a_switch(trunks=(Trunk("hall-a", 8, 400.0),))

    def test_the_three_port_counts_have_the_same_shape(self):
        """One of them was a property once and a caller formatted a bound method."""
        sw = a_switch()
        assert sw.total_ports() == 40
        assert sw.used_ports() == 0
        assert sw.free_ports() == 40
        assert sw.total_ports("hall-c") == 8

    def test_allocation_consumes_ports_on_one_trunk_only(self):
        sw = a_switch()
        sw.allocate(Request("c1", "hall-b", 4000.0))
        assert sw.used_ports("hall-b") == 10
        assert sw.used_ports("hall-c") == 0

    def test_a_request_larger_than_its_trunk_is_refused_even_with_free_ports_elsewhere(self):
        """The whole point of the module. Ports are not a pool."""
        sw = a_switch()
        with pytest.raises(RadixExhausted, match="other trunks"):
            sw.allocate(Request("c1", "hall-c", 6000.0))
        assert sw.free_ports() == 40

    def test_releasing_returns_the_ports(self):
        sw = a_switch()
        sw.allocate(Request("c1", "hall-b", 4000.0))
        sw.release("c1")
        assert sw.free_ports() == 40

    def test_allocating_the_same_circuit_twice_is_refused(self):
        sw = a_switch()
        sw.allocate(Request("c1", "hall-b", 400.0))
        with pytest.raises(ValueError, match="already allocated"):
            sw.allocate(Request("c1", "hall-b", 400.0))

    def test_free_ports_on_the_wrong_trunk_are_stranded(self):
        sw = a_switch()
        stranded = sw.stranded_ports([Request("c1", "hall-b", 12800.0)])
        assert stranded == 8

    def test_nothing_is_stranded_when_demand_can_use_every_trunk(self):
        sw = a_switch()
        demand = [Request("c1", "hall-b", 12800.0), Request("c2", "hall-c", 3200.0)]
        assert sw.stranded_ports(demand) == 0

    def test_fragmentation_is_a_fraction_of_free_ports(self):
        sw = a_switch()
        assert sw.fragmentation([Request("c1", "hall-b", 12800.0)]) == pytest.approx(8 / 40)

    def test_fragmentation_of_a_full_switch_is_zero_not_undefined(self):
        sw = a_switch(trunks=(Trunk("hall-b", 1, 400.0),))
        sw.allocate(Request("c1", "hall-b", 400.0))
        assert sw.fragmentation([Request("c2", "hall-b", 400.0)]) == 0.0

    def test_preemption_is_unnecessary_when_the_request_already_fits(self):
        sw = a_switch()
        plan = preemption_plan(sw, Request("c1", "hall-b", 400.0, priority=5), lambda a: 1.0)
        assert plan is not None and plan.victims == ()

    def test_preemption_never_evicts_equal_or_higher_priority(self):
        sw = a_switch()
        sw.allocate(Request("victim", "hall-b", 12800.0, priority=5))
        assert preemption_plan(sw, Request("c1", "hall-b", 400.0, priority=5),
                               lambda a: 1.0) is None

    def test_preemption_returns_none_when_no_subset_frees_enough(self):
        sw = a_switch()
        sw.allocate(Request("small", "hall-b", 400.0, priority=1))
        assert preemption_plan(sw, Request("c1", "hall-b", 400_000.0, priority=9),
                               lambda a: 1.0) is None

    def test_preemption_picks_the_cheapest_subset_not_the_greedy_one(self):
        """Greedy by cost-per-port gets this wrong; that is why it enumerates."""
        sw = OpticalSwitch("hall-a", (Trunk("hall-b", 10, 400.0),))
        sw.allocate(Request("wide", "hall-b", 2400.0, priority=1))   # 6 ports
        sw.allocate(Request("narrow-a", "hall-b", 800.0, priority=1))  # 2
        sw.allocate(Request("narrow-b", "hall-b", 800.0, priority=1))  # 2
        cost = {"wide": 10.0, "narrow-a": 1.0, "narrow-b": 1.0}
        plan = preemption_plan(sw, Request("c1", "hall-b", 1600.0, priority=9),
                               lambda a: cost[a.circuit_id])
        assert {v.circuit_id for v in plan.victims} == {"narrow-a", "narrow-b"}
        assert plan.cost == 2.0

    def test_too_many_candidates_is_refused_rather_than_hanging(self):
        sw = OpticalSwitch("hall-a", (Trunk("hall-b", 26, 400.0),))
        for i in range(25):
            sw.allocate(Request(f"c{i}", "hall-b", 400.0, priority=1))
        with pytest.raises(ValueError, match="2\\*\\*n"):
            preemption_plan(sw, Request("big", "hall-b", 8000.0, priority=9),
                            lambda a: 1.0)

    def test_a_preemption_names_the_jobs_it_would_kill(self):
        sw = OpticalSwitch("hall-a", (Trunk("hall-b", 4, 400.0),))
        sw.allocate(Request("v", "hall-b", 1600.0, priority=1, job_id="eval-2"))
        plan = preemption_plan(sw, Request("c1", "hall-b", 1600.0, priority=9), lambda a: 1.0)
        assert plan.job_ids == ("eval-2",)


# ==========================================================================
# checkpoint
# ==========================================================================


def a_plan(**over) -> CheckpointPlan:
    base = dict(
        strategy=Strategy.WRITE_LOCAL,
        state_bytes=int(4.2e12),
        local_write_GBps=60.0,
        stitch_bw_gbps=400.0,
        stitch_share=0.5,
    )
    base.update(over)
    return CheckpointPlan(**base)


class TestCheckpoint:
    def test_write_local_does_not_survive_losing_its_hall(self):
        assert not Strategy.WRITE_LOCAL.survives_hall_loss

    def test_async_replication_is_durable_but_off_the_critical_path(self):
        assert Strategy.ASYNC_REPLICATE.survives_hall_loss
        assert not Strategy.ASYNC_REPLICATE.on_critical_path

    def test_write_local_has_no_stitch_transfer(self):
        assert a_plan(strategy=Strategy.WRITE_LOCAL).stitch_transfer_s == 0.0

    def test_a_critical_path_transfer_is_not_throttled_by_the_share(self):
        """The job is stopped, so no collective is competing for the circuit."""
        half = a_plan(strategy=Strategy.SYNC_REPLICATE, stitch_share=0.5)
        full = a_plan(strategy=Strategy.SYNC_REPLICATE, stitch_share=1.0)
        assert half.stitch_transfer_s == pytest.approx(full.stitch_transfer_s)

    def test_a_background_transfer_is_throttled_by_the_share(self):
        half = a_plan(strategy=Strategy.ASYNC_REPLICATE, stitch_share=0.5)
        full = a_plan(strategy=Strategy.ASYNC_REPLICATE, stitch_share=1.0)
        assert half.stitch_transfer_s == pytest.approx(2 * full.stitch_transfer_s)

    def test_write_local_pays_a_stop_tax_and_no_contention_tax(self):
        t = tax(a_plan(strategy=Strategy.WRITE_LOCAL), reference_rhythm())
        assert t.stop_fraction > 0 and t.contention_fraction == 0.0

    def test_async_replicate_pays_a_contention_tax_that_a_curve_will_not_show(self):
        """The W9 point: the invisible tax is the one that costs money."""
        t = tax(a_plan(strategy=Strategy.ASYNC_REPLICATE), reference_rhythm())
        assert t.contention_fraction > 0

    def test_async_and_write_local_stop_for_exactly_as_long(self):
        rhythm = reference_rhythm()
        local = tax(a_plan(strategy=Strategy.WRITE_LOCAL), rhythm)
        asyn = tax(a_plan(strategy=Strategy.ASYNC_REPLICATE), rhythm)
        assert local.stop_s == pytest.approx(asyn.stop_s)
        assert asyn.total_fraction > local.total_fraction

    def test_the_destination_ingest_rate_can_bind_instead_of_the_circuit(self):
        """For an object store it usually does, which is the point of the field."""
        slow = a_plan(strategy=Strategy.STAGE_THROUGH_OBJECT, ingest_budget_GBps=5.0)
        fast = a_plan(strategy=Strategy.STAGE_THROUGH_OBJECT, ingest_budget_GBps=None)
        assert slow.stop_s > fast.stop_s

    def test_the_destination_ingest_rate_does_not_touch_a_local_write(self):
        budgeted = a_plan(strategy=Strategy.WRITE_LOCAL, ingest_budget_GBps=1.0)
        free = a_plan(strategy=Strategy.WRITE_LOCAL, ingest_budget_GBps=None)
        assert budgeted.stop_s == pytest.approx(free.stop_s)

    def test_cheapest_durable_never_returns_write_local(self):
        best = cheapest_durable(a_plan(), reference_rhythm())
        assert best.strategy is not Strategy.WRITE_LOCAL
        assert best.survives_hall_loss

    def test_the_cheapest_durable_strategy_changes_with_circuit_width(self):
        """The headline W9 result: the answer flips inside the range of real plants."""
        rhythm = reference_rhythm()
        picks = {
            gbps: cheapest_durable(a_plan(stitch_bw_gbps=gbps), rhythm).strategy
            for gbps in (200.0, 400.0, 800.0, 3200.0)
        }
        assert len(set(picks.values())) >= 2

    def test_a_wider_stitch_never_makes_a_crossing_strategy_worse(self):
        rhythm = reference_rhythm()
        for strategy in (Strategy.ASYNC_REPLICATE, Strategy.SYNC_REPLICATE):
            narrow = tax(a_plan(strategy=strategy, stitch_bw_gbps=200.0), rhythm)
            wide = tax(a_plan(strategy=strategy, stitch_bw_gbps=1600.0), rhythm)
            assert wide.total_fraction <= narrow.total_fraction

    def test_compare_covers_every_strategy(self):
        assert set(compare_strategies(a_plan(), reference_rhythm())) == set(Strategy)

    def test_state_must_be_positive(self):
        with pytest.raises(ValueError):
            a_plan(state_bytes=0)


class TestStallFidelity:
    """A storage stall is not a fabric break. Guessing wrong reroutes the wrong layer."""

    def test_storage_evidence_reads_as_storage(self):
        cause, _ = classify_stall(StallEvidence(
            pfs_latency_elevated=True, pfs_queue_depth_elevated=True,
            during_checkpoint_window=True))
        assert cause is StallCause.STORAGE

    def test_fabric_evidence_reads_as_fabric(self):
        cause, _ = classify_stall(StallEvidence(
            fabric_link_errors=True, circuit_ber_elevated=True,
            non_stitched_ranks_stalled=False))
        assert cause is StallCause.FABRIC

    def test_no_evidence_is_ambiguous_rather_than_a_guess(self):
        cause, why = classify_stall(StallEvidence())
        assert cause is StallCause.AMBIGUOUS
        assert why

    def test_evidence_for_both_is_ambiguous(self):
        cause, _ = classify_stall(StallEvidence(
            pfs_latency_elevated=True, pfs_queue_depth_elevated=True,
            fabric_link_errors=True, circuit_ber_elevated=True))
        assert cause is StallCause.AMBIGUOUS

    def test_off_stitch_ranks_stalling_does_not_move_the_verdict(self):
        """Under a barrier both failures propagate to every rank, so it separates neither."""
        without = classify_stall(StallEvidence(
            circuit_ber_elevated=True, fabric_link_errors=True))
        with_ = classify_stall(StallEvidence(
            circuit_ber_elevated=True, fabric_link_errors=True,
            non_stitched_ranks_stalled=True))
        assert without == with_

    def test_off_stitch_ranks_stalling_alone_points_outside_both_layers(self):
        cause, why = classify_stall(StallEvidence(non_stitched_ranks_stalled=True))
        assert cause is StallCause.AMBIGUOUS
        assert "not the circuit" in why


# ==========================================================================
# drift
# ==========================================================================


def a_declared(**over) -> DeclaredCircuit:
    base = dict(circuit_id="stitch-ab-1", a_hall="hall-a", z_hall="hall-b",
                bw_gbps=800.0, rtt_us=8.0, path_km=1.2, il_db=3.0)
    base.update(over)
    return DeclaredCircuit(**base)


def a_measured(**over) -> MeasuredCircuit:
    base = dict(circuit_id="stitch-ab-1", rtt_us=8.0, il_db=3.0, ber=1e-15, bw_gbps=800.0)
    base.update(over)
    return MeasuredCircuit(**base)


class TestDrift:
    def test_q_six_rounds_to_the_published_error_rate(self):
        """The specification a 1e-9 receiver is written against."""
        assert float(f"{ber_from_q(6.0):.0e}") == pytest.approx(1e-9)

    def test_q_seven_matches_the_published_value(self):
        assert ber_from_q(7.0) == pytest.approx(1.28e-12, rel=0.01)

    def test_the_error_rate_falls_as_q_rises(self):
        qs = [3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        bers = [ber_from_q(q) for q in qs]
        assert bers == sorted(bers, reverse=True)

    def test_zero_margin_sits_at_the_rated_threshold(self):
        assert q_from_margin_db(0.0) == pytest.approx(6.0)

    def test_losing_margin_costs_orders_of_magnitude(self):
        """A path does not degrade gracefully, which is why a trend is the only warning."""
        assert ber_from_margin_db(2.0) < ber_from_margin_db(0.0) / 1e6

    def test_the_thermal_coefficient_is_the_published_one(self):
        assert THERMAL_DELAY_PS_PER_KM_K == pytest.approx(40.0)

    def test_a_metro_span_moves_a_measurable_fraction_of_a_microsecond_with_the_weather(self):
        assert thermal_rtt_swing_us(120.0, 25.0) == pytest.approx(0.24, rel=1e-9)

    def test_the_thermal_swing_is_linear_in_both_arguments(self):
        assert thermal_rtt_swing_us(240.0, 25.0) == pytest.approx(
            2 * thermal_rtt_swing_us(120.0, 25.0))

    def test_a_circuit_cannot_start_and_end_in_one_hall(self):
        with pytest.raises(ValueError):
            a_declared(z_hall="hall-a")

    def test_the_topology_hash_is_stable(self):
        assert a_declared().topology_hash() == a_declared().topology_hash()

    def test_the_topology_hash_moves_when_any_declared_field_moves(self):
        base = a_declared().topology_hash()
        for field, value in (("bw_gbps", 400.0), ("rtt_us", 9.0),
                             ("path_km", 2.0), ("il_db", 4.0), ("connectors", 6)):
            assert a_declared(**{field: value}).topology_hash() != base

    def test_a_matching_measurement_matches(self):
        assert compare_circuit(a_declared(), a_measured()).verdict is DriftVerdict.MATCHES

    def test_a_five_times_round_trip_is_drift(self):
        report = compare_circuit(a_declared(), a_measured(rtt_us=40.0))
        assert report.verdict is DriftVerdict.DRIFTED
        assert [f.field for f in report.exceeded] == ["rtt_us"]

    def test_a_different_circuit_id_stops_the_comparison(self):
        """Comparing fields across two different circuits produces confident nonsense."""
        report = compare_circuit(a_declared(), a_measured(circuit_id="stitch-zz"))
        assert report.verdict is DriftVerdict.WRONG_CIRCUIT
        assert report.fields == ()

    def test_tolerances_can_be_overridden(self):
        loose = compare_circuit(a_declared(), a_measured(rtt_us=40.0), {"rtt_us": 10.0})
        assert loose.verdict is DriftVerdict.MATCHES

    def test_a_flat_path_has_nothing_to_forecast(self):
        fc = forecast("c", il_now_db=6.0, il_rate_db_per_year=0.0, receiver_budget_db=18.0)
        assert math.isinf(fc.years_to_threshold)
        assert "nothing to forecast" in fc.explain()

    def test_a_slow_trend_that_never_crosses_says_so_rather_than_claiming_no_trend(self):
        fc = forecast("c", il_now_db=6.0, il_rate_db_per_year=0.01, receiver_budget_db=18.0)
        assert math.isinf(fc.years_to_threshold)
        assert "does not cross" in fc.explain()

    def test_a_fast_trend_crosses_within_the_horizon(self):
        fc = forecast("c", il_now_db=14.0, il_rate_db_per_year=1.2, receiver_budget_db=18.0)
        assert 0.0 < fc.years_to_threshold < 25.0

    def test_a_faster_trend_crosses_sooner(self):
        slow = forecast("c", 14.0, 0.5, 18.0).years_to_threshold
        fast = forecast("c", 14.0, 2.0, 18.0).years_to_threshold
        assert fast < slow

    def test_a_path_already_over_the_target_crosses_now(self):
        fc = forecast("c", il_now_db=19.0, il_rate_db_per_year=0.5, receiver_budget_db=18.0)
        assert fc.years_to_threshold == 0.0

    def test_a_crossing_inside_a_year_is_flagged_as_actionable(self):
        assert forecast("c", 17.0, 2.0, 18.0).actionable


# ==========================================================================
# ledger
# ==========================================================================


def a_ledger() -> Ledger:
    led = Ledger()
    led.open_entry(DebtEntry("d1", "hall-a", Cause.DRIFT, 0.0,
                             daily_accelerator_hours=100.0,
                             remediation_accelerator_hours=200.0))
    led.open_entry(DebtEntry("d2", "hall-b", Cause.STRANDED_PORTS, 50 * DAY,
                             daily_accelerator_hours=10.0))
    return led


class TestLedger:
    def test_debt_cannot_be_negative(self):
        with pytest.raises(ValueError):
            DebtEntry("x", "h", Cause.DRIFT, 0.0, daily_accelerator_hours=-1.0)

    def test_an_entry_cannot_close_before_it_opens(self):
        with pytest.raises(ValueError):
            DebtEntry("x", "h", Cause.DRIFT, 10.0, closed_at_s=5.0)

    def test_duplicate_ids_are_refused(self):
        led = a_ledger()
        with pytest.raises(ValueError, match="duplicate"):
            led.open_entry(DebtEntry("d1", "hall-a", Cause.DRIFT, 0.0))

    def test_debt_accrues_while_the_entry_stays_open(self):
        """The entire argument for remediation."""
        led = a_ledger()
        assert led.total(100 * DAY) > led.total(10 * DAY)

    def test_closing_an_entry_stops_the_clock(self):
        led = a_ledger()
        led.close_entry("d1", 10 * DAY)
        frozen = led.entries[0].accrued(10 * DAY)
        assert led.entries[0].accrued(500 * DAY) == pytest.approx(frozen)

    def test_a_closed_entry_leaves_the_outstanding_balance(self):
        led = a_ledger()
        before = led.total(100 * DAY)
        led.close_entry("d1", 100 * DAY)
        assert led.total(100 * DAY) < before

    def test_a_closed_entry_stays_in_the_lifetime_total(self):
        led = a_ledger()
        before = led.total(100 * DAY, open_only=False)
        led.close_entry("d1", 100 * DAY)
        assert led.total(100 * DAY, open_only=False) == pytest.approx(before)

    def test_closing_twice_is_refused(self):
        led = a_ledger()
        led.close_entry("d1", DAY)
        with pytest.raises(ValueError, match="already closed"):
            led.close_entry("d1", 2 * DAY)

    def test_closing_an_unknown_entry_raises(self):
        with pytest.raises(KeyError):
            a_ledger().close_entry("nope", DAY)

    def test_aging_buckets_partition_the_outstanding_balance(self):
        led = a_ledger()
        now = 100 * DAY
        assert sum(led.aging(now).values()) == pytest.approx(led.total(now))

    def test_an_old_entry_lands_in_the_oldest_bucket(self):
        led = a_ledger()
        assert led.aging(100 * DAY)["90d+"] > 0

    def test_by_hall_and_by_cause_both_sum_to_the_total(self):
        led = a_ledger()
        now = 100 * DAY
        assert sum(led.by_hall(now).values()) == pytest.approx(led.total(now))
        assert sum(led.by_cause(now).values()) == pytest.approx(led.total(now))

    def test_worst_first_ranks_by_daily_rate_not_by_accrued_total(self):
        """A big old entry that has stopped bleeding is not the one to fix."""
        led = Ledger()
        led.open_entry(DebtEntry("old-big", "h", Cause.DRIFT, 0.0,
                                 upfront_accelerator_hours=1e6))
        led.open_entry(DebtEntry("new-bleeding", "h", Cause.DRIFT, 99 * DAY,
                                 daily_accelerator_hours=500.0))
        assert led.worst_first(100 * DAY)[0].entry_id == "new-bleeding"

    def test_payback_is_infinite_when_nothing_accrues(self):
        entry = DebtEntry("x", "h", Cause.DRIFT, 0.0, upfront_accelerator_hours=100.0,
                          remediation_accelerator_hours=50.0)
        assert math.isinf(entry.payback_days())

    def test_payback_is_remediation_over_the_daily_rate(self):
        assert a_ledger().entries[0].payback_days() == pytest.approx(2.0)

    def test_pricing_requires_a_rate_and_has_no_default(self):
        with pytest.raises(TypeError):
            a_ledger().priced(100 * DAY)

    def test_a_supplied_rate_scales_the_total(self):
        led = a_ledger()
        assert led.priced(100 * DAY, 2.0) == pytest.approx(2 * led.total(100 * DAY))

    def test_the_report_names_every_open_entry_cause(self):
        text = a_ledger().report(100 * DAY)
        assert "drift" in text and "stranded-ports" in text

    def test_a_ledger_round_trips_through_json(self):
        led = a_ledger()
        led.close_entry("d1", 20 * DAY)
        assert Ledger.from_json(led.to_json()).total(100 * DAY) == pytest.approx(
            led.total(100 * DAY))

    def test_a_wrong_circuit_becomes_an_inventory_error_not_a_drift(self):
        report = compare_circuit(a_declared(), a_measured(circuit_id="other"))
        entry = debt_from_drift(report, hall="hall-a", opened_at_s=0.0,
                                accelerators=1024, slowdown=0.1)
        assert entry.cause is Cause.INVENTORY_ERROR

    def test_a_drift_entry_carries_the_reason_forward(self):
        report = compare_circuit(a_declared(), a_measured(rtt_us=40.0))
        entry = debt_from_drift(report, hall="hall-a", opened_at_s=0.0,
                                accelerators=1024, slowdown=0.1)
        assert "rtt_us" in entry.note
        assert entry.circuit_id == "stitch-ab-1"

    def test_the_slowdown_is_the_callers_number(self):
        """A slow path a job barely touches is nearly free. The report cannot know."""
        report = compare_circuit(a_declared(), a_measured(rtt_us=40.0))
        light = debt_from_drift(report, hall="h", opened_at_s=0.0,
                                accelerators=1024, slowdown=0.001)
        heavy = debt_from_drift(report, hall="h", opened_at_s=0.0,
                                accelerators=1024, slowdown=0.5)
        assert heavy.daily_accelerator_hours == pytest.approx(
            500 * light.daily_accelerator_hours)

    def test_debt_from_drift_rejects_a_non_report(self):
        with pytest.raises(TypeError):
            debt_from_drift({"circuit_id": "x"}, hall="h", opened_at_s=0.0,
                            accelerators=1, slowdown=0.1)

    def test_stranded_ports_convert_at_the_callers_port_density(self):
        entry = debt_from_stranded_ports("hall-a", 8, 8, opened_at_s=0.0)
        assert entry.daily_accelerator_hours == pytest.approx(8 * 8 * 24)

    def test_stranded_port_counts_cannot_be_negative(self):
        with pytest.raises(ValueError):
            debt_from_stranded_ports("hall-a", -1, 8, opened_at_s=0.0)


# ==========================================================================
# the models compose
# ==========================================================================


class TestComposition:
    def test_a_drifted_circuit_becomes_a_ledger_line_with_a_payback(self):
        report = compare_circuit(a_declared(), a_measured(rtt_us=40.0, il_db=6.4))
        entry = debt_from_drift(report, hall="hall-a", opened_at_s=0.0,
                                accelerators=4096, slowdown=0.06,
                                remediation_accelerator_hours=4096 * 3.0)
        assert entry.payback_days() < 5.0

    def test_a_stranded_trunk_becomes_a_ledger_line(self):
        sw = a_switch()
        stranded = sw.stranded_ports([Request("c1", "hall-b", 12800.0)])
        entry = debt_from_stranded_ports("hall-a", stranded, 8, opened_at_s=0.0)
        assert entry.cause is Cause.STRANDED_PORTS
        assert entry.daily_accelerator_hours > 0

    def test_a_retune_cost_and_a_checkpoint_tax_are_in_the_same_unit(self):
        """Everything in the series prices in accelerator-hours so it can be added up."""
        rhythm = reference_rhythm()
        retune_h = cheapest_legal(rhythm, 400.0).lost_accelerator_hours
        ckpt = tax(a_plan(strategy=Strategy.ASYNC_REPLICATE), rhythm)
        ckpt_h = ckpt.total_fraction * rhythm.accelerators * 24.0
        assert retune_h >= 0 and ckpt_h > 0
