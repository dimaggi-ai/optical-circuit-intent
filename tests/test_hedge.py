"""The one measured link: reading the HEDGE files, and refusing to when they are wrong.

Two kinds of test. Synthetic fixtures pin what each marker and the Layer-3
join do, sample by sample. The regression tests read the real, SHA-pinned
files (``make data``) and pin the numbers the README quotes; they fail, not
skip, when the data is absent, because a suite that quietly narrows itself
when the measurement is missing is the failure mode DECISIONS.md D13 exists
to rule out.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ocintent import cli, hedge
from ocintent.drift import (
    DB_PER_DECADE_OF_Q,
    DEFAULT_TARGET_BER,
    ber_from_q,
    forecast,
    margin_span_db,
    q_from_ber,
    q_from_margin_db,
)
from ocintent.hedge import (
    HedgeDataError,
    Report,
    Sample,
    Wavelength,
    counter_restabilised,
    first_counter_change,
    link_down_intervals,
    scaling_check,
    timeline,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "hedge"


# --- the manifest and the refusal -------------------------------------------

def test_the_manifest_pins_fifteen_files_with_full_sha256s():
    assert len(hedge.MANIFEST) == len(hedge.RUNS) * len(hedge.FILES) == 15
    for rel, digest in hedge.MANIFEST.items():
        run, name = rel.split("/")
        assert run in hedge.RUNS and name in hedge.FILES
        assert len(digest) == 64 and int(digest, 16) >= 0
    assert hedge.HEDGE_COMMIT in hedge.HEDGE_RAW_BASE
    assert len(hedge.HEDGE_COMMIT) == 40


def test_verify_names_missing_and_altered_files(tmp_path):
    """One run copied, one byte changed in one file, one file removed."""
    src = DATA / "mod_formats"
    dst = tmp_path / "mod_formats"
    shutil.copytree(src, dst)
    log = dst / "server2.log"
    log.write_bytes(log.read_bytes() + b"\n")
    (dst / "server4.log").unlink()
    problems = hedge.verify(tmp_path, "mod_formats")
    assert {(p.path, p.what) for p in problems} == {
        ("mod_formats/server2.log", "sha256 mismatch"),
        ("mod_formats/server4.log", "missing"),
    }
    with pytest.raises(HedgeDataError) as err:
        hedge.load_run(tmp_path, "mod_formats")
    assert "make data" in str(err.value)
    assert "server2.log (sha256 mismatch)" in str(err.value)
    # the other two runs are not there at all
    assert len(hedge.verify(tmp_path)) == 2 + 10


def test_an_unknown_run_is_a_key_error():
    with pytest.raises(KeyError):
        hedge.load_run(DATA, "no-such-run")


def test_the_fetch_script_is_a_verified_no_op_once_the_files_are_present():
    """Runs without the network: everything is present, so nothing is fetched."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "data" / "hedge" / "fetch_hedge.py")],
        capture_output=True, text=True, cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "all 15 files present and verified" in proc.stdout


# --- the markers, on synthetic samples --------------------------------------

def _samples(fecs, bers=None, powers=None, dt=1.0):
    bers = bers or [1e-6] * len(fecs)
    powers = powers or [0.0] * len(fecs)
    return tuple(Sample(i * dt, b, f, p) for i, (f, b, p) in enumerate(zip(fecs, bers, powers)))


def test_the_failure_marker_is_the_first_sample_whose_counter_exceeds_the_initial():
    assert first_counter_change(_samples([7, 7, 7, 8, 9, 9])) == 3
    assert first_counter_change(_samples([7, 7, 7])) is None
    assert first_counter_change(()) is None
    # a counter that only ever goes down is not a failure
    assert first_counter_change(_samples([7, 6, 5])) is None


def test_the_recovery_marker_is_the_first_index_followed_by_five_equal_readings():
    fecs = [0, 0, 1, 2, 3, 4, 4, 4, 4, 4, 4, 5]
    assert first_counter_change(_samples(fecs)) == 2
    assert counter_restabilised(_samples(fecs), 2) == 5
    # four equal readings after the plateau start is one short
    assert counter_restabilised(_samples([0, 1, 2, 2, 2, 2, 2]), 1) is None
    assert counter_restabilised(_samples([0, 1, 2, 2, 2, 2, 2, 2]), 1) == 2


def test_the_timeline_reads_onset_ramp_cliff_failure_and_recovery():
    fecs = [0, 0, 0, 0, 0, 1, 2, 3, 3, 3, 3, 3, 3]
    bers = [1e-5, 1e-5, 2e-5, 5e-5, 1e-3, 3e-2, 3e-2, 3e-2, 1e-5, 1e-5, 1e-5, 1e-5, 1e-5]
    powers = [0.0, 0.0, -0.5, -1.0, -2.0, -3.0, -3.0, -3.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    tl = timeline(Wavelength(9, "16-QAM", _samples(fecs, bers, powers)))
    assert tl.failed and tl.failed_s == 5.0
    assert tl.onset_s == 1.0          # the last pre-failure sample at or below the start BER
    assert tl.ramp_start_s == 2.0
    assert tl.ber_at_ramp_start == 2e-5
    assert tl.ber_at_cliff == 1e-3    # the last sample before the counter moved
    assert tl.attenuation_at_cliff_db == 2.0
    assert tl.attenuation_at_ramp_start_db == 0.5
    assert tl.restabilised_s == 7.0
    assert tl.onset_lead_s == 4.0
    assert tl.cliff_s == 4.0             # the last sample before the counter moved
    assert tl.failure_poll_gap_s == 1.0  # so the failure itself sits in (4.0, 5.0]
    assert tl.name == "ch9 16-QAM"


def test_a_wavelength_that_never_fails_has_no_markers():
    tl = timeline(Wavelength(4, None, _samples([3, 3, 3, 3], [1e-6, 2e-6, 3e-6, 4e-6])))
    assert not tl.failed
    assert tl.onset_s is None and tl.failed_s is None and tl.restabilised_s is None
    assert tl.onset_lead_s is None and tl.cliff_s is None and tl.failure_poll_gap_s is None
    assert tl.name == "ch4 (unlabelled)"
    assert scaling_check(tl) is None


def test_an_empty_window_is_an_error_not_a_quiet_wavelength():
    with pytest.raises(HedgeDataError):
        timeline(Wavelength(1, None, ()))


# --- the Layer-3 join --------------------------------------------------------

def _server(times_and_gbps):
    """Synthetic re-timed reports: a one-second interval ending at each ``t``."""
    return tuple(Report(t - 1.0, t, g) for t, g in times_and_gbps)


def test_the_link_is_down_only_when_every_server_is_quiet():
    a = _server([(0.0, 1.2), (1.0, 0.0), (2.0, 0.0), (3.0, 1.2)])
    b = _server([(0.1, 1.2), (1.1, 1.2), (2.1, 0.0), (3.1, 1.2)])
    # server a alone going quiet is not an outage. Both are quiet once b's
    # interval ending at 2.1 is in, and that interval began at 1.1; a carried
    # traffic again in the interval ending at 3.0, which began at 2.0.
    assert link_down_intervals((a, b), (0.0, 10.0)) == ((1.1, 2.0),)


def test_an_outage_still_open_at_the_end_has_no_end():
    a = _server([(0.0, 1.0), (5.0, 0.0)])
    b = _server([(0.0, 1.0), (5.5, 0.0)])
    assert link_down_intervals((a, b), (0.0, 10.0)) == ((4.5, None),)


def test_outages_are_clipped_to_the_window_and_dropped_outside_it():
    a = _server([(0.0, 1.0), (5.0, 0.0), (8.0, 1.0), (20.0, 0.0), (22.0, 1.0)])
    b = _server([(0.0, 1.0), (5.0, 0.0), (8.0, 1.0), (20.0, 0.0), (22.0, 1.0)])
    # dark from the start of the empty interval (4.0) to the start of the one
    # that carried again (7.0); the second outage begins after the window
    assert link_down_intervals((a, b), (6.0, 15.0)) == ((6.0, 7.0),)
    assert link_down_intervals((a, b), (0.0, 15.0)) == ((4.0, 7.0),)


def test_iperf_parsing_retimes_late_stamps_skips_the_closing_totals_and_converts_units(tmp_path):
    """Two of the three interval lines were flushed late, one by 4.5 s.

    The intervals are timed from iperf's own field, anchored at the line
    that was not late, so the late stamps change the re-timing statistics
    and nothing else. With the run's first transponder sample ten seconds
    earlier, every interval moves ten seconds later on the run's clock.
    """
    log = tmp_path / "server1.log"
    log.write_text(
        "1700000000000|Initial timestamp: whatever\n"
        "1700000001000|[SUM]   0.00-1.00   sec   150 MBytes  1.26 Gbits/sec  0.010 ms  0/1234 (0%)\n"
        "1700000002000|[  5]   1.00-2.00   sec   12 MBytes   100 Mbits/sec  0.010 ms  0/123 (0%)\n"
        "1700000006500|[SUM]   1.00-2.00   sec   0.00 Bytes  0.00 bits/sec  0.000 ms  0/0 (0%)\n"
        "1700000006500|[SUM]   2.00-3.00   sec   500 KBytes  4.10 Mbits/sec  0.010 ms  0/12 (0%)\n"
        "1700000007000|[SUM]   0.00-3.00   sec   650 MBytes  1.80 Gbits/sec  0.000 ms  0/1369 (0%)  receiver\n"
    )
    reports, retiming = hedge._parse_iperf(log, 1700000000.0)
    assert [(r.start_s, r.t_s, round(r.gbps, 5)) for r in reports] == [
        (0.0, 1.0, 1.26), (1.0, 2.0, 0.0), (2.0, 3.0, 0.0041),
    ]
    assert retiming == hedge.Retiming(anchor_s=0.0, max_late_s=4.5, late_lines=2, lines=3)
    shifted, retiming_again = hedge._parse_iperf(log, 1699999990.0)
    assert [(r.start_s, r.t_s) for r in shifted] == [(10.0, 11.0), (11.0, 12.0), (12.0, 13.0)]
    assert retiming_again.anchor_s == 10.0 and retiming_again.max_late_s == 4.5
    empty = tmp_path / "server2.log"
    empty.write_text("1700000000000|nothing here\n")
    with pytest.raises(HedgeDataError):
        hedge._parse_iperf(empty, 1700000000.0)


def test_transponder_rows_are_sorted_per_channel_and_timed_from_the_earliest(tmp_path):
    csv_path = tmp_path / "transponder_data.csv"
    csv_path.write_text(
        "1700000010.0,52,1e-5,3,-4.0\n"
        "1700000000.0,28,2e-5,7,-3.0\n"
        "\n"
        "1700000005.0,52,1e-6,3,-4.0\n"
    )
    t0, by_channel = hedge._parse_transponder(csv_path)
    assert t0 == 1700000000.0
    assert [r[0] for r in by_channel[52]] == [1700000005.0, 1700000010.0]
    assert set(by_channel) == {28, 52}


# --- the scaling shape check --------------------------------------------------

def _tl(ber_from, ber_to, measured_db):
    from ocintent.hedge import WavelengthTimeline
    return WavelengthTimeline(
        1, "x", ber_from, 0.0, 0.0, 1.0, ber_from, 0.0, 5.0, 4.0, ber_to, measured_db, None, 10,
    )


def test_the_scaling_check_reports_which_prediction_the_measurement_is_nearer():
    direct = margin_span_db(1e-4, 3e-2, detection="direct")
    coherent = margin_span_db(1e-4, 3e-2, detection="coherent")
    assert coherent == pytest.approx(2 * direct)
    assert scaling_check(_tl(1e-4, 3e-2, coherent)).closer == "coherent"
    assert scaling_check(_tl(1e-4, 3e-2, direct)).closer == "direct"
    # nearer the coherent figure by a hair still counts as coherent; there is no band
    assert scaling_check(_tl(1e-4, 3e-2, (direct + coherent) / 2 + 1e-9)).closer == "coherent"
    assert scaling_check(_tl(1e-4, 3e-2, (direct + coherent) / 2 - 1e-9)).closer == "direct"
    # a BER that did not rise is not a span
    assert scaling_check(_tl(3e-2, 1e-4, 1.0)) is None
    assert scaling_check(_tl(0.0, 3e-2, 1.0)) is None


def test_the_scaling_check_prints_the_slope_the_data_implies_and_says_when_it_is_steeper_than_both():
    direct = margin_span_db(1e-4, 3e-2, detection="direct")
    coherent = margin_span_db(1e-4, 3e-2, detection="coherent")
    on_direct = scaling_check(_tl(1e-4, 3e-2, direct))
    on_coherent = scaling_check(_tl(1e-4, 3e-2, coherent))
    assert on_direct.decades_of_q == pytest.approx(direct / 10.0)
    assert on_direct.implied_db_per_decade == pytest.approx(10.0)
    assert on_coherent.implied_db_per_decade == pytest.approx(20.0)
    assert not on_direct.steeper_than_both and not on_coherent.steeper_than_both
    steeper = scaling_check(_tl(1e-4, 3e-2, coherent + 1.0))
    assert steeper.closer == "coherent" and steeper.steeper_than_both
    assert steeper.implied_db_per_decade > 20.0


def test_q_from_ber_inverts_ber_from_q_and_refuses_nonsense():
    for q in (3.0, 6.0, 7.0, 12.0):
        assert q_from_ber(ber_from_q(q)) == pytest.approx(q, abs=1e-6)
    assert q_from_ber(0.5) == 0.0
    with pytest.raises(ValueError):
        q_from_ber(0.0)
    with pytest.raises(ValueError):
        q_from_ber(-1e-3)


def test_coherent_detection_needs_twice_the_decibels_per_decade_of_q():
    assert DB_PER_DECADE_OF_Q == {"direct": 10.0, "coherent": 20.0}
    assert q_from_margin_db(20.0, detection="coherent") == pytest.approx(
        q_from_margin_db(10.0, detection="direct"))
    with pytest.raises(KeyError):
        q_from_margin_db(1.0, detection="quantum")


def test_the_forecast_takes_the_detection_and_a_fec_limit_target():
    """A coherent transponder counts down to its FEC limit, not to 1e-12."""
    assert DEFAULT_TARGET_BER == 1e-12
    direct = forecast("c", il_now_db=10.0, il_rate_db_per_year=1.0, receiver_budget_db=18.0)
    coherent = forecast("c", il_now_db=10.0, il_rate_db_per_year=1.0, receiver_budget_db=18.0,
                        target_ber=3e-2, detection="coherent")
    assert direct.target_ber == DEFAULT_TARGET_BER
    assert coherent.target_ber == 3e-2
    # both cross inside the horizon, and the coherent one has further to run to its cliff
    assert direct.years_to_threshold < direct.horizon_years
    assert coherent.years_to_threshold < coherent.horizon_years
    assert coherent.years_to_threshold > direct.years_to_threshold


# --- the real files: numbers the README quotes --------------------------------

@pytest.fixture(scope="module")
def summaries():
    return hedge.load_summaries(DATA)


def test_the_attenuation_run_fails_its_formats_when_the_paper_says(summaries):
    proto = summaries["prototype"]
    failed = {tl.channel: round(tl.failed_s, 1) for tl in proto.wavelengths}
    assert failed == {28: 461.6, 52: 517.4, 74: 512.6, 84: 551.1}
    assert proto.link_down == ((pytest.approx(549.9, abs=0.1), None),)
    assert proto.lead_first_failure_to_link_loss_s == pytest.approx(88.3, abs=0.1)
    # the outage began inside the poll gap in which PM-QPSK failed, just before its alarm sample
    assert proto.lead_last_failure_to_link_loss_s == pytest.approx(-1.2, abs=0.1)
    assert proto.last_failure_poll_gap_s == pytest.approx(1.6, abs=0.05)
    assert proto.outage_within_a_poll_of_last_failure
    assert proto.reports == 3888


def test_the_bend_run_loses_the_link_with_green_and_gets_it_back(summaries):
    wdl = summaries["wdl"]
    assert [tl.channel for tl in wdl.labelled()] == [3, 52, 74]
    assert wdl.link_lost_s == pytest.approx(221.5, abs=0.1)
    assert wdl.link_back_s == pytest.approx(250.5, abs=0.1)
    assert wdl.link_up_at_window_end
    assert wdl.lead_first_failure_to_link_loss_s == pytest.approx(39.0, abs=0.1)
    assert wdl.lead_last_failure_to_link_loss_s == pytest.approx(-0.1, abs=0.1)
    assert wdl.outage_within_a_poll_of_last_failure
    green = wdl.by_label("1547 nm")[0]
    assert green.failed_s == pytest.approx(221.7, abs=0.1)


def test_the_iperf_stamps_were_flushes_and_the_four_servers_re_time_to_one_anchor(summaries):
    """The finding behind DECISIONS.md D14, pinned on the real files."""
    for name, s in summaries.items():
        anchors = [rt.anchor_s for rt in s.retiming]
        assert len(anchors) == 4
        assert max(anchors) - min(anchors) < 0.01, name
        assert max(rt.max_late_s for rt in s.retiming) > 4.0, name
        assert all(rt.late_lines > rt.lines // 2 for rt in s.retiming), name
        assert s.probe_gbps_median == pytest.approx(1.2, abs=0.01), name
    assert summaries["prototype"].retiming[0].anchor_s == pytest.approx(12.87, abs=0.01)
    assert summaries["wdl"].retiming[0].anchor_s == pytest.approx(-12.46, abs=0.01)
    assert summaries["mod_formats"].retiming[0].anchor_s == pytest.approx(-9.17, abs=0.01)


def test_the_format_bend_run_never_loses_the_link(summaries):
    mod = summaries["mod_formats"]
    assert mod.link_down == ()
    assert [tl.label for tl in mod.failures] == ["16-QAM"]
    assert mod.first_failure_s == pytest.approx(169.5, abs=0.1)


def test_every_cliff_sits_near_the_fec_limit_and_every_ramp_is_steeper_than_both_branches(summaries):
    cliffs = [tl.ber_at_cliff for s in summaries.values() for tl in s.failures]
    assert len(cliffs) == 9
    assert 3e-2 < min(cliffs) <= max(cliffs) < 3.5e-2
    checks = [scaling_check(tl) for s in summaries.values() for tl in s.failures]
    assert [c.closer for c in checks] == ["coherent"] * 9
    assert all(c.steeper_than_both for c in checks)
    assert all(c.implied_db_per_decade > DB_PER_DECADE_OF_Q["coherent"] for c in checks)


def test_the_report_names_every_run_and_the_commit(summaries):
    text = hedge.report(summaries)
    assert hedge.HEDGE_COMMIT[:12] in text
    for run in hedge.RUNS:
        assert f"run {run}:" in text
    assert "layer 3: carried traffic for the whole window" in text
    assert "3/4-inch macrobend" in text


# --- the CLI -------------------------------------------------------------------

def test_the_hedge_subcommand_prints_or_dumps_and_refuses_missing_data(capsys, tmp_path):
    assert cli.main(["hedge", "--data", str(DATA), "--run", "wdl"]) == 0
    out = capsys.readouterr().out
    assert "run wdl:" in out and "run prototype:" not in out

    assert cli.main(["hedge", "--data", str(DATA), "--run", "mod_formats", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["commit"] == hedge.HEDGE_COMMIT
    run = doc["runs"]["mod_formats"]
    assert run["link_down_s"] == []
    assert [w["label"] for w in run["wavelengths"] if w["failed"]] == ["16-QAM"]
    assert all(w["samples"] > 0 for w in run["wavelengths"])
    assert run["scaling"][0]["closer"] == "coherent" and run["scaling"][0]["steeper_than_both"]
    assert len(run["retiming"]) == 4 and run["retiming"][0]["max_late_s"] > 4.0
    assert run["probe_gbps_median"] == pytest.approx(1.2, abs=0.01)
    assert run["last_failure_poll_gap_s"] == pytest.approx(1.6, abs=0.05)
    assert run["outage_within_a_poll_of_last_failure"] is None

    assert cli.main(["hedge", "--data", str(tmp_path)]) == 2
    assert "make data" in capsys.readouterr().err


def test_the_drift_subcommand_accepts_the_detection_flag(capsys):
    circuit = ROOT / "examples" / "circuit-drifted.json"
    assert cli.main(["drift", str(circuit), "--il-rate", "0.9",
                     "--target-ber", "3e-2", "--detection", "coherent"]) == 1
    out = capsys.readouterr().out
    assert "3e-02" in out
    with pytest.raises(SystemExit):
        cli.main(["drift", str(circuit), "--detection", "quantum"])
