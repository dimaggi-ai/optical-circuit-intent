"""Break the machinery on purpose and check the registry notices.

A registry that stays green when you delete the thing it is supposed to be
checking is decoration. Each test here removes real machinery --- not a
constant nudged by a percent, but a rule deleted or inverted --- and asserts
the *exact* set of points that turns red. Naming the exact set matters: a
mutation that reddens everything proves nothing about which point was doing the
work.

:func:`test_unmutated_control` runs first and asserts the registry is green
without any mutation, so a red set below cannot be an artefact of a registry
that was already failing.

The last test is the odd one. It applies a mutation and asserts that the
registry does **not** notice, because the registry genuinely cannot: the value
of the fibre thermal coefficient is an input, and nothing here measures it.
Printing that blind spot is worth more than pretending it is covered.
"""

from __future__ import annotations

import sys
from typing import Set

import pytest

import ocintent.checkpoint as checkpoint_mod
import ocintent.drift as drift_mod
import ocintent.hedge as hedge_mod
import ocintent.legality as legality_mod
import ocintent.ledger as ledger_mod
import ocintent.radix as radix_mod
import validate_intent as registry


def red_set() -> Set[str]:
    """Names of the points that fail right now."""
    return {p.name for p in registry.run_registry() if not p.passed}


def patch_everywhere(monkeypatch, name: str, replacement) -> None:
    """Replace ``name`` in its own module and in every module that imported it.

    ``from ocintent.drift import ber_from_q`` binds a second reference. Patching
    only the defining module leaves the registry calling the original, and the
    mutation test then passes for the wrong reason.
    """
    patched = 0
    for module in list(sys.modules.values()):
        if module is None or not getattr(module, "__name__", "").startswith(
            ("ocintent", "validate_intent")
        ):
            continue
        if hasattr(module, name):
            monkeypatch.setattr(module, name, replacement, raising=True)
            patched += 1
    assert patched, f"nothing to patch for {name!r}; the mutation would be a no-op"


def test_unmutated_control():
    """Green before anything is broken, or every red set below means nothing."""
    assert red_set() == set()


def test_flatten_the_error_rate_relation(monkeypatch):
    """Both error-rate anchors pin this one function. Deleting it must show.

    The loss forecast goes red too, and should: it reaches its answer by asking
    when the error rate crosses a target, so a constant error rate makes every
    path forecast the same date. Two measured points follow it down: the
    scaling comparison converts each wavelength's BERs to Q through the same
    relation (its inverse is a bisection on it), and the cliff point compares
    the measured cliff with this function's value at Q of six. That the
    measured link is read through the model's own relation is worth seeing.
    """
    patch_everywhere(monkeypatch, "ber_from_q", lambda q: 1e-12)
    assert red_set() == {
        "q-of-six-rounds-to-the-published-1e-9",
        "q-of-seven-matches-the-published-1.28e-12",
        "a-faster-insertion-loss-trend-never-crosses-later",
        "hedge-every-failing-wavelength-scales-closer-to-coherent-than-direct",
        "hedge-the-fec-cliff-sits-decades-above-the-forecast-default-target",
    }


def test_make_preemption_greedy(monkeypatch):
    """The exact search is the claim. Replace it with greedy and the claim dies."""

    def greedy(switch, request, cost_of_evicting):
        trunk = switch.trunk(request.peer_hall)
        needed = trunk.ports_for(request.bw_gbps) - switch.free_ports(request.peer_hall)
        if needed <= 0:
            return radix_mod.Preemption((), 0, 0.0)
        candidates = [a for a in switch.allocations.values()
                      if a.peer_hall == request.peer_hall
                      and a.priority < request.priority]
        victims, freed, cost = [], 0, 0.0
        for alloc in sorted(candidates, key=lambda a: cost_of_evicting(a) / a.ports):
            if freed >= needed:
                break
            victims.append(alloc)
            freed += alloc.ports
            cost += cost_of_evicting(alloc)
        if freed < needed:
            return None
        return radix_mod.Preemption(tuple(victims), freed, cost)

    patch_everywhere(monkeypatch, "preemption_plan", greedy)
    assert red_set() == {"greedy-preemption-evicts-more-than-it-needs-to"}


def test_throttle_critical_path_transfers_by_the_share(monkeypatch):
    """The bug an earlier draft shipped: a stopped job has no collective to share with."""
    def stitch_transfer_s(self):
        if not self.strategy.crosses_stitch:
            return 0.0
        rate = self.stitch_bw_gbps * self.stitch_share / 8.0
        if self.ingest_budget_GBps is not None:
            rate = min(rate, self.ingest_budget_GBps)
        return self.state_bytes / (rate * checkpoint_mod.GIGABYTE)

    monkeypatch.setattr(checkpoint_mod.CheckpointPlan, "stitch_transfer_s",
                        property(stitch_transfer_s))
    assert red_set() == {
        "the-cheapest-durable-checkpoint-strategy-changes-with-stitch-width",
        "the-shortest-checkpoint-window-is-not-the-cheapest-strategy",
    }


def test_return_the_convex_hull_instead_of_the_intervals(monkeypatch):
    """A single band was the earlier answer, and it was wrong for the gaps."""
    original = legality_mod.disagreement_intervals

    def hull(rhythm):
        intervals = original(rhythm)
        if not intervals:
            return ()
        return ((intervals[0][0], intervals[-1][1]),)

    patch_everywhere(monkeypatch, "disagreement_intervals", hull)
    assert red_set() == {
        "the-disagreement-set-is-often-more-than-one-interval",
        "every-point-inside-an-interval-disagrees-and-every-gap-agrees",
    }


def test_allow_mid_collective_to_be_selected(monkeypatch):
    """It is legal only for an instantaneous change, which no plant offers."""
    monkeypatch.setattr(
        legality_mod, "_selectable",
        lambda rhythm, retune_s: [
            c for c in legality_mod.ladder(rhythm, retune_s)
            if c.legality is not legality_mod.Legality.FATAL
        ],
    )
    assert "mid-collective-is-shown-but-never-chosen" in red_set()


def test_drop_a_field_from_the_topology_hash(monkeypatch):
    """A hash blind to insertion loss cannot detect the fault it exists to detect."""
    def topology_hash(self):
        import hashlib
        import json
        payload = {k: v for k, v in self.__dict__.items() if k != "il_db"}
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    monkeypatch.setattr(drift_mod.DeclaredCircuit, "topology_hash", topology_hash)
    assert red_set() == {"the-topology-hash-moves-when-any-declared-field-moves"}


def test_compare_fields_across_two_different_circuits(monkeypatch):
    """Confident nonsense: every field of circuit A measured against circuit B."""
    original = drift_mod.compare

    def compare_anyway(declared, measured, tolerances=None):
        measured = drift_mod.MeasuredCircuit(
            **{**measured.__dict__, "circuit_id": declared.circuit_id}
        )
        return original(declared, measured, tolerances)

    patch_everywhere(monkeypatch, "compare_circuit", compare_anyway)
    monkeypatch.setattr(drift_mod, "compare", compare_anyway)
    assert red_set() == {
        "a-measurement-of-a-different-circuit-produces-no-field-comparisons"
    }


def test_pretend_no_port_is_ever_stranded(monkeypatch):
    """Treating ports as a pool is the mistake the whole module exists to name."""
    monkeypatch.setattr(radix_mod.OpticalSwitch, "stranded_ports",
                        lambda self, demand: 0)
    assert red_set() == {"concentrated-demand-strands-more-ports-than-spread-demand"}


def test_drop_the_oldest_aging_bucket(monkeypatch):
    """The bucket holding most of the balance, silently unreported."""
    monkeypatch.setattr(ledger_mod, "AGING_BUCKETS", ledger_mod.AGING_BUCKETS[:-1])
    assert red_set() == {"aging-buckets-hall-and-cause-each-partition-the-balance"}


def test_rank_the_fix_list_by_accrued_total(monkeypatch):
    """A big old entry that stopped bleeding is not the one to spend a window on."""
    monkeypatch.setattr(
        ledger_mod.Ledger, "worst_first",
        lambda self, now_s, limit=10: tuple(
            sorted(self.outstanding(), key=lambda e: -e.accrued(now_s))[:limit]
        ),
    )
    assert red_set() == {"the-fix-list-ranks-by-daily-rate-not-by-accrued-total"}


def test_round_ports_down(monkeypatch):
    """Half a port carries no light."""
    monkeypatch.setattr(radix_mod.Trunk, "ports_for",
                        lambda self, bw_gbps: int(bw_gbps // self.port_bw_gbps))
    assert red_set() == {"a-partial-port-is-a-whole-port"}


def test_let_preemption_evict_anything(monkeypatch):
    """Priority is the only thing standing between a plan and killing the big job."""
    original = radix_mod.preemption_plan

    def no_priority(switch, request, cost_of_evicting):
        raised = radix_mod.Request(
            request.circuit_id, request.peer_hall, request.bw_gbps,
            priority=10_000, job_id=request.job_id,
        )
        return original(switch, raised, cost_of_evicting)

    patch_everywhere(monkeypatch, "preemption_plan", no_priority)
    assert red_set() == {
        "preemption-never-evicts-an-equal-or-higher-priority-circuit"
    }


def test_ignore_whether_checkpoints_cross_the_stitch(monkeypatch):
    """A retune cannot hide inside a checkpoint that is using the same circuit."""
    original = legality_mod.JobRhythm.quiet_window_s

    def blind(self, boundary):
        local = legality_mod.JobRhythm(
            **{**self.__dict__, "checkpoint_crosses_stitch": False}
        )
        return original(local, boundary)

    monkeypatch.setattr(legality_mod.JobRhythm, "quiet_window_s", blind)
    assert red_set() == {
        "checkpoints-that-cross-the-stitch-never-widen-a-quiet-window"
    }


def test_forecast_ignores_the_loss_trend(monkeypatch):
    """A forecast that does not use the trend is a constant with a date on it."""
    original = drift_mod.forecast

    def flat(circuit_id, il_now_db, il_rate_db_per_year, receiver_budget_db, **kw):
        return original(circuit_id, il_now_db, 0.5, receiver_budget_db, **kw)

    patch_everywhere(monkeypatch, "forecast", flat)
    assert red_set() == {"a-faster-insertion-loss-trend-never-crosses-later"}


def test_debt_stops_accruing_while_it_stays_open(monkeypatch):
    """The accrual is the entire argument for fixing anything."""
    monkeypatch.setattr(
        ledger_mod.DebtEntry, "accrued",
        lambda self, now_s: self.upfront_accelerator_hours,
    )
    assert red_set() == {"outstanding-debt-never-falls-while-nothing-is-closed"}


def test_the_registry_cannot_see_a_wrong_thermal_coefficient(monkeypatch):
    """A blind spot, asserted rather than hidden.

    The 40 ps/km/K coefficient is an input taken from the literature. The only
    point that touches it checks that the model is *linear* in distance and
    temperature, which it remains at any coefficient. So the registry cannot
    tell a correct coefficient from a wrong one, and this test exists to say so
    in the test output rather than in a footnote nobody reads.

    Closing it needs a measurement from a real span. That is item 1 of the
    declined list, and it stays there until someone has a plant.
    """
    patch_everywhere(monkeypatch, "THERMAL_DELAY_PS_PER_KM_K", 4000.0)
    monkeypatch.setattr(
        drift_mod, "thermal_rtt_swing_us",
        lambda path_km, delta_t_k: 2.0 * 4000.0 * path_km * delta_t_k * 1e-6,
    )
    patch_everywhere(
        monkeypatch, "thermal_rtt_swing_us",
        lambda path_km, delta_t_k: 2.0 * 4000.0 * path_km * delta_t_k * 1e-6,
    )
    assert red_set() == set(), (
        "the registry noticed a hundred-fold error in the thermal coefficient, "
        "which would be good news and means this test is out of date"
    )


# --- the one measured link ---------------------------------------------------
#
# The registry reads the HEDGE files through ``ocintent.hedge``. The mutations
# below break that reading one marker at a time; each red set was measured by
# running the mutated registry, not predicted. Two of them are blind spots and
# say so, in the same spirit as the thermal-coefficient test above.

MEASURED_POINTS = {
    "hedge-the-formats-fail-at-the-published-450-500-and-550-s",
    "hedge-attenuation-traffic-continues-until-pm-qpsk-fails",
    "hedge-the-higher-the-format-the-earlier-the-failure-and-the-less-loss",
    "hedge-bend-red-and-blue-fail-before-green-and-the-link-holds-until-green",
    "hedge-bend-released-every-failed-wavelength-restabilises-and-the-link-returns",
    "hedge-format-bend-only-16qam-fails-and-the-link-stays-up",
    "hedge-the-first-fec-alarm-precedes-link-loss-in-every-run-that-lost-it",
    "hedge-the-outage-began-within-one-poll-gap-of-the-last-failure-not-the-first",
    "hedge-ber-rises-across-more-than-one-poll-before-every-fec-change",
    "hedge-the-window-start-onset-marker-orders-the-formats-16qam-first",
    "hedge-the-fec-cliff-sits-decades-above-the-forecast-default-target",
    "hedge-every-failing-wavelength-scales-closer-to-coherent-than-direct",
    "hedge-every-pinned-file-is-present-and-matches-its-sha256",
    "hedge-the-four-servers-re-timing-anchors-agree-within-one-report-interval",
    "hedge-the-event-join-agrees-with-the-authors-index-aligned-reduction",
}


def test_missing_data_turns_every_measured_point_red_in_its_own_kind(monkeypatch):
    """No files, no green. Fifteen points fail; none skips; each keeps its kind.

    The kinds matter: a calibrated point that failed for want of data must
    still print as calibrated, or the counts in the summary line would drift
    with the presence of a directory.
    """
    from pathlib import Path

    patch_everywhere(monkeypatch, "HEDGE_DATA_DIR", Path("/no/such/directory"))
    points = registry.run_registry()
    failed = {p.name: p for p in points if not p.passed}
    assert set(failed) == MEASURED_POINTS
    kinds = {p.kind for p in failed.values()}
    assert kinds == {"calibrated", "emergent", "sanity"}
    assert sum(p.kind == "calibrated" for p in failed.values()) == 6
    assert sum(p.kind == "emergent" for p in failed.values()) == 6
    assert sum(p.kind == "sanity" for p in failed.values()) == 3
    assert all("no measurement" in p.detail or "missing" in p.detail for p in failed.values())
    assert not any("raised" in p.detail for p in failed.values())
    assert len(points) == 40


def test_read_the_last_fec_change_instead_of_the_first(monkeypatch):
    """The failure marker slides to the last sample whose counter differs.

    Eleven points go red. Only the file-integrity, the re-timing, the
    join-agreement and the format-bend points survive: the last change in
    the format-bend run is the same single wavelength failing, and the other
    three do not read FEC at all.
    """

    def last_change(samples):
        base = samples[0].fec
        idx = None
        for i, s in enumerate(samples):
            if s.fec != base:
                idx = i
        return idx

    patch_everywhere(monkeypatch, "first_counter_change", last_change)
    assert red_set() == MEASURED_POINTS - {
        "hedge-every-pinned-file-is-present-and-matches-its-sha256",
        "hedge-the-four-servers-re-timing-anchors-agree-within-one-report-interval",
        "hedge-the-event-join-agrees-with-the-authors-index-aligned-reduction",
        "hedge-format-bend-only-16qam-fails-and-the-link-stays-up",
    }


def test_the_registry_cannot_tell_all_quiet_from_any_quiet(monkeypatch):
    """A blind spot, asserted rather than hidden.

    The Layer-3 join calls the link down when every probe is quiet. Rewrite
    it to call the link down when *any* probe is quiet and nothing goes red,
    measured. The four probes ride one link and go quiet within a report
    interval of one another, so both rules draw the same intervals to within
    the bound the join-agreement point allows. The rule is a recorded
    decision (DECISIONS.md D14), not something this data can adjudicate.
    """

    def any_quiet(servers, window):
        events = sorted((r.t_s, r.start_s, k, r.gbps)
                        for k, reports in enumerate(servers) for r in reports)
        latest, down, out = {}, None, []
        lo, hi = window
        for _, start, k, gbps in events:
            latest[k] = gbps
            quiet = any(v <= 0.0 for v in latest.values())
            if quiet and down is None:
                down = start
            elif not quiet and down is not None:
                if down <= hi and start >= lo:
                    out.append((max(down, lo), min(start, hi)))
                down = None
        if down is not None and down <= hi:
            out.append((max(down, lo), None))
        return tuple(out)

    patch_everywhere(monkeypatch, "link_down_intervals", any_quiet)
    assert red_set() == set()


def test_ignore_the_authors_analysis_windows(monkeypatch):
    """Read every run end to end, including the unrelated experiments outside the window.

    Three points go red. Outside the format-bend window a later disturbance
    the paper does not describe fails the other three wavelengths within two
    polls of one another and takes the link down within a poll of the first
    of them, more than a poll before the last, so the only-16-QAM point and
    the outage-resolution point both go; and the window-start onset marker
    is defined against the window's first sample, so with the window gone it
    no longer orders the attenuation run's formats. The failure-time and
    power-drop orderings and the bare timings survive, which is a reminder
    that the windows are part of the authors' reading, not a tuning knob.
    The experiment prints what lies outside the windows.
    """
    import math

    patch_everywhere(monkeypatch, "WINDOWS", {k: (0.0, math.inf) for k in hedge_mod.WINDOWS})
    assert red_set() == {
        "hedge-format-bend-only-16qam-fails-and-the-link-stays-up",
        "hedge-the-outage-began-within-one-poll-gap-of-the-last-failure-not-the-first",
        "hedge-the-window-start-onset-marker-orders-the-formats-16qam-first",
    }


def test_swap_the_16qam_and_pm_qpsk_labels(monkeypatch):
    """Channel 28 is called PM-QPSK and channel 84 is called 16-QAM.

    Every point that reads a format label in the attenuation run goes red,
    four of them; the bend-run points and the label-free ones do not.
    """
    labels = {run: dict(chans) for run, chans in hedge_mod.LABELS.items()}
    labels["prototype"] = {28: "PM-QPSK", 52: "8-QAM", 74: "8-QAM", 84: "16-QAM"}
    patch_everywhere(monkeypatch, "LABELS", labels)
    assert red_set() == {
        "hedge-attenuation-traffic-continues-until-pm-qpsk-fails",
        "hedge-the-formats-fail-at-the-published-450-500-and-550-s",
        "hedge-the-higher-the-format-the-earlier-the-failure-and-the-less-loss",
        "hedge-the-window-start-onset-marker-orders-the-formats-16qam-first",
    }


def test_collapse_coherent_scaling_onto_direct(monkeypatch):
    """Both detections scale at ten decibels per decade of Q.

    The two predictions coincide, so no measurement can be closer to one of
    them, and exactly the scaling point goes red.
    """
    patch_everywhere(monkeypatch, "DB_PER_DECADE_OF_Q", {"direct": 10.0, "coherent": 10.0})
    assert red_set() == {"hedge-every-failing-wavelength-scales-closer-to-coherent-than-direct"}


def test_delete_the_recovery_marker(monkeypatch):
    """No wavelength ever re-stabilises. Exactly the recovery point goes red."""
    patch_everywhere(monkeypatch, "counter_restabilised", lambda samples, start, equal_run=5: None)
    assert red_set() == {
        "hedge-bend-released-every-failed-wavelength-restabilises-and-the-link-returns",
    }


def test_flatten_the_ber_onset_onto_the_cliff(monkeypatch):
    """The BER rise becomes invisible: onset and cliff are the same sample.

    The onset-lead point goes red because the rise no longer spans a poll,
    and the scaling point goes red because a zero-width span is not a span
    to compare against anything.
    """
    import dataclasses

    original = hedge_mod.timeline

    def flat(w):
        tl = original(w)
        if not tl.failed:
            return tl
        pre = w.samples[: hedge_mod.first_counter_change(w.samples)]
        return dataclasses.replace(
            tl, onset_s=pre[-1].t_s, ramp_start_s=pre[-1].t_s, ber_at_ramp_start=pre[-1].ber,
            attenuation_at_ramp_start_db=tl.attenuation_at_cliff_db,
        )

    patch_everywhere(monkeypatch, "timeline", flat)
    assert red_set() == {
        "hedge-ber-rises-across-more-than-one-poll-before-every-fec-change",
        "hedge-every-failing-wavelength-scales-closer-to-coherent-than-direct",
    }


def test_place_each_iperf_interval_at_its_flush_stamp(monkeypatch):
    """Delete the re-timing: every iperf interval is timed by the stamp on its log line.

    That is the reading DECISIONS.md D14 replaced. The stamps are stdout
    flushes that trail their intervals by up to five seconds, so the outage
    moves whole seconds later than the last wavelength's failure sample in
    both runs that lost the link, and the three points that read the outage
    to the transponder's resolution go red. The anchor-agreement point stays
    green, measured: with no re-timing every anchor is zero and the spread
    is zero, which is a reminder that it checks the consistency of the
    re-timing, not its presence.
    """

    def stamped(path, t0):
        reports = []
        with open(path, errors="replace") as fh:
            for line in fh:
                if "|" not in line or "[SUM]" not in line or "receiver" in line or "sender" in line:
                    continue
                stamp, text = line.split("|", 1)
                parts = text.split()
                if len(parts) < 7 or parts[6] not in hedge_mod._RATE_TO_GBPS:
                    continue
                t = float(stamp) / 1000.0 - t0
                reports.append(hedge_mod.Report(
                    t - hedge_mod.REPORT_INTERVAL_S, t,
                    float(parts[5]) * hedge_mod._RATE_TO_GBPS[parts[6]]))
        reports.sort(key=lambda r: r.t_s)
        return tuple(reports), hedge_mod.Retiming(0.0, 0.0, 0, len(reports))

    patch_everywhere(monkeypatch, "_parse_iperf", stamped)
    assert red_set() == {
        "hedge-attenuation-traffic-continues-until-pm-qpsk-fails",
        "hedge-bend-red-and-blue-fail-before-green-and-the-link-holds-until-green",
        "hedge-the-outage-began-within-one-poll-gap-of-the-last-failure-not-the-first",
    }


def test_the_registry_cannot_see_a_deleted_integrity_check_while_the_files_are_intact(monkeypatch):
    """A blind spot, asserted rather than hidden.

    Delete the SHA-256 verification and the registry stays green, measured,
    because the files on disk are the pinned ones and every point reads the
    same bytes either way. The check only bites on altered or missing files,
    which ``tests/test_hedge.py`` exercises on a tampered copy. A registry
    cannot prove its own lock works by never trying the door.
    """
    patch_everywhere(monkeypatch, "verify", lambda data_dir, run=None: ())
    assert red_set() == set()
