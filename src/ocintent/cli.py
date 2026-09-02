"""Command line for the six models.

Exit codes are the same across every subcommand, because a caller wiring this
into a pipeline should not have to remember which one it invoked:

``0``
    The thing is legal, healthy, or satisfiable.
``1``
    The thing is refused, illegal, unsatisfiable, or over budget. Not an error
    --- an answer, and usually the answer that matters.
``2``
    The input could not be read. This is an error.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from .checkpoint import CheckpointPlan, Strategy, cheapest_durable
from .checkpoint import compare as compare_strategies
from .drift import DeclaredCircuit, DriftVerdict, MeasuredCircuit, forecast
from .drift import compare as compare_circuit
from .intent import Boundary, Endpoint, Intent, Verb, compile_intent
from .ledger import Ledger
from .legality import (
    JobRhythm,
    Legality,
    cheapest_legal,
    disagreement_intervals,
    ladder,
    soonest_legal,
)
from .radix import OpticalSwitch, Request, Trunk, preemption_plan

OK, REFUSED, UNREADABLE = 0, 1, 2

#: The rhythm quoted throughout the documentation. A 16k-accelerator synchronous
#: pretrain with a 2.4 s step. Every default below is one of its fields, so a
#: bare ``ocintent ladder`` prints the worked example rather than an error.
REFERENCE_RHYTHM = dict(
    accelerators=16_384,
    step_s=2.4,
    cross_stitch_collective_s=0.31,
    steps_per_checkpoint=250,
    checkpoint_window_s=120.0,
    checkpoint_crosses_stitch=False,
    steps_per_epoch=4_000,
    epoch_gap_s=45.0,
)


def _load(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    with open(path) as fh:
        return json.load(fh)


def _rhythm_from(args: argparse.Namespace) -> JobRhythm:
    return JobRhythm(
        accelerators=args.accelerators,
        step_s=args.step_s,
        cross_stitch_collective_s=args.collective_s,
        steps_per_checkpoint=args.steps_per_checkpoint,
        checkpoint_window_s=args.checkpoint_window_s,
        checkpoint_crosses_stitch=args.checkpoint_crosses_stitch,
        steps_per_epoch=args.steps_per_epoch,
        epoch_gap_s=args.epoch_gap_s,
    )


def _add_rhythm_flags(p: argparse.ArgumentParser) -> None:
    r = REFERENCE_RHYTHM
    p.add_argument("--accelerators", type=int, default=r["accelerators"])
    p.add_argument("--step-s", type=float, default=r["step_s"])
    p.add_argument("--collective-s", type=float, default=r["cross_stitch_collective_s"])
    p.add_argument("--steps-per-checkpoint", type=int, default=r["steps_per_checkpoint"])
    p.add_argument("--checkpoint-window-s", type=float, default=r["checkpoint_window_s"])
    p.add_argument("--steps-per-epoch", type=int, default=r["steps_per_epoch"])
    p.add_argument("--epoch-gap-s", type=float, default=r["epoch_gap_s"])
    p.add_argument(
        "--checkpoint-crosses-stitch",
        action="store_true",
        default=r["checkpoint_crosses_stitch"],
        help="checkpoints replicate over the stitch, so a retune cannot hide in one",
    )


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------


def cmd_compile(args: argparse.Namespace) -> int:
    try:
        intent = Intent.from_dict(_load(args.intent))
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        print(f"cannot read intent: {exc}", file=sys.stderr)
        return UNREADABLE
    boundary = Boundary(args.boundary) if args.boundary else None
    plan = compile_intent(intent, boundary=boundary)
    if args.json:
        print(json.dumps(
            {
                "intent": intent.to_dict(),
                "digest": intent.digest(),
                "boundary": plan.boundary.value if plan.boundary else None,
                "operations": [{"call": o.call, "args": o.args} for o in plan.operations],
                "notes": list(plan.notes),
            },
            indent=2,
        ))
        return OK
    print(f"intent  {intent.verb.value} {intent.circuit_id}  digest {intent.digest()[:16]}")
    if plan.boundary:
        print(f"boundary {plan.boundary.value}")
    print(f"plan    {len(plan.operations)} operations")
    for i, op in enumerate(plan.operations, 1):
        rendered = " ".join(f"{k}={v}" for k, v in sorted(op.args.items()))
        print(f"  {i}. {op.call:<16} {rendered}")
    for note in plan.notes:
        print(f"  note: {note}")
    return OK


def cmd_ladder(args: argparse.Namespace) -> int:
    rhythm = _rhythm_from(args)
    rows = ladder(rhythm, args.retune_s)
    print(f"retune of {args.retune_s:g} s against a {rhythm.accelerators:,}-accelerator job")
    print()
    print(f"  {'boundary':<20} {'legality':<10} {'quiet':>8} {'wait':>10} "
          f"{'stall':>8} {'accel-h':>10}")
    for r in rows:
        print(f"  {r.boundary.value:<20} {r.legality.value:<10} {r.quiet_window_s:>8.2f} "
              f"{r.wait_s:>10.1f} {r.stall_s:>8.2f} {r.lost_accelerator_hours:>10.1f}")
    cheap = cheapest_legal(rhythm, args.retune_s)
    soon = soonest_legal(rhythm, args.retune_s)
    print()
    print(f"  cheapest_legal  {cheap.boundary.value if cheap else 'none'}"
          + (f"  ({cheap.lost_accelerator_hours:,.1f} accel-h, "
             f"{cheap.total_delay_s:,.0f} s of delay)" if cheap else ""))
    print(f"  soonest_legal   {soon.boundary.value if soon else 'none'}"
          + (f"  ({soon.lost_accelerator_hours:,.1f} accel-h, "
             f"{soon.total_delay_s:,.0f} s of delay)" if soon else ""))
    if cheap and soon and cheap.boundary is not soon.boundary:
        print(f"  the two objectives disagree here: "
              f"{abs(cheap.total_delay_s - soon.total_delay_s):,.0f} s of delay separates them")
    return OK if cheap else REFUSED


def cmd_disagree(args: argparse.Namespace) -> int:
    rhythm = _rhythm_from(args)
    intervals = disagreement_intervals(rhythm)
    if not intervals:
        print("the two objectives agree at every retune time")
        return OK
    total = sum(hi - lo for lo, hi in intervals)
    print(f"cheapest_legal and soonest_legal disagree over {total:,.1f} s of retune time, "
          f"in {len(intervals)} interval(s):")
    for lo, hi in intervals:
        print(f"  [{lo:,.3f}, {hi:,.3f}) s   width {hi - lo:,.1f} s")
    return OK


def cmd_radix(args: argparse.Namespace) -> int:
    try:
        data = _load(args.switch)
        switch = OpticalSwitch(
            hall_id=data["hall_id"],
            trunks=tuple(Trunk(**t) for t in data["trunks"]),
        )
        for a in data.get("allocations", []):
            switch.allocate(Request(**a))
        demand = [Request(**r) for r in data.get("demand", [])]
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        print(f"cannot read switch: {exc}", file=sys.stderr)
        return UNREADABLE

    print(f"switch {switch.hall_id}: {switch.used_ports()}/{switch.total_ports()} ports used, "
          f"{switch.free_ports()} free")
    for t in switch.trunks:
        print(f"  trunk -> {t.peer_hall:<16} {switch.used_ports(t.peer_hall)}/{t.ports} "
              f"ports @ {t.port_bw_gbps:g}G")
    if not demand:
        return OK
    stranded = switch.stranded_ports(demand)
    frag = switch.fragmentation(demand)
    print()
    print(f"pending demand: {len(demand)} request(s)")
    print(f"  stranded ports  {stranded} of {switch.free_ports()} free "
          f"({frag * 100:.0f}% of free capacity cannot serve it)")
    unmet = 0
    for r in demand:
        trunk = switch.trunk(r.peer_hall)
        needed = trunk.ports_for(r.bw_gbps)
        plan = preemption_plan(switch, r, cost_of_evicting=lambda a: float(a.priority))
        if plan is None:
            unmet += 1
            print(f"  {r.circuit_id}: unsatisfiable -- needs {needed} port(s) to "
                  f"{r.peer_hall}, and no set of lower-priority circuits frees enough")
        elif plan.victims:
            print(f"  {r.circuit_id}: needs {needed} port(s); evict "
                  + ", ".join(f"{v.circuit_id} ({v.ports}p, job {v.job_id or '-'})"
                              for v in plan.victims)
                  + f" to free {plan.ports_freed}")
        else:
            print(f"  {r.circuit_id}: fits in free ports ({needed} needed, "
                  f"{switch.free_ports(r.peer_hall)} free on that trunk)")
    return REFUSED if unmet else OK


def cmd_checkpoint(args: argparse.Namespace) -> int:
    rhythm = _rhythm_from(args)
    base = CheckpointPlan(
        strategy=Strategy.WRITE_LOCAL,
        state_bytes=int(args.state_tb * 1e12),
        local_write_GBps=args.local_write_gbps,
        stitch_bw_gbps=args.stitch_gbps,
        stitch_share=args.stitch_share,
    )
    table = compare_strategies(base, rhythm)
    print(f"{args.state_tb:g} TB of state, {args.local_write_gbps:g} GB/s local, "
          f"{args.stitch_gbps:g}G stitch at a {args.stitch_share:g} share")
    print()
    print(f"  {'strategy':<24} {'stop s':>8} {'stop %':>8} {'contend %':>10} "
          f"{'total %':>9}  durable")
    for strategy, t in table.items():
        print(f"  {strategy.value:<24} {t.stop_s:>8.2f} {t.stop_fraction * 100:>8.2f} "
              f"{t.contention_fraction * 100:>10.2f} {t.total_fraction * 100:>9.2f}  "
              f"{'yes' if t.survives_hall_loss else 'no'}")
    best = cheapest_durable(base, rhythm)
    print()
    print(f"  cheapest durable: {best.strategy.value} "
          f"({best.total_fraction * 100:.2f}% of wall clock)")
    return OK


def cmd_drift(args: argparse.Namespace) -> int:
    try:
        data = _load(args.circuit)
        declared = DeclaredCircuit(**data["declared"])
        measured = MeasuredCircuit(**data["measured"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        print(f"cannot read circuit: {exc}", file=sys.stderr)
        return UNREADABLE

    report = compare_circuit(declared, measured, data.get("tolerances"))
    print(f"declared topology hash {report.topology_hash[:16]}")
    print(f"verdict: {report.verdict.value}")
    for f in report.fields:
        flag = "  <-- outside tolerance" if f.exceeded else ""
        print(f"  {f}{flag}")
    if report.verdict is DriftVerdict.WRONG_CIRCUIT:
        print(f"  {report.note}")

    if args.il_rate is not None:
        fc = forecast(
            declared.circuit_id,
            il_now_db=measured.il_db,
            il_rate_db_per_year=args.il_rate,
            receiver_budget_db=args.receiver_budget_db,
            target_ber=args.target_ber,
        )
        print()
        print(fc.explain())
        if fc.actionable:
            print("  actionable: schedule a window inside the year")
    return OK if report.verdict is DriftVerdict.MATCHES else REFUSED


def cmd_ledger(args: argparse.Namespace) -> int:
    try:
        with open(args.ledger) as fh:
            led = Ledger.from_json(fh.read())
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        print(f"cannot read ledger: {exc}", file=sys.stderr)
        return UNREADABLE
    print(led.report(args.now_s))
    if args.budget is not None and led.total(args.now_s) > args.budget:
        print()
        print(f"over budget: {led.total(args.now_s):,.0f} > {args.budget:,.0f} accelerator-hours")
        return REFUSED
    return OK


def cmd_example(args: argparse.Namespace) -> int:
    intent = Intent(
        verb=Verb.REQUEST,
        circuit_id="stitch-ab-1",
        endpoints=(Endpoint("hall-a", "ocs-1/4"), Endpoint("hall-b", "ocs-3/9")),
        min_bw_gbps=800.0,
        hold_s=6.0 * 3600.0,
        job_id="pretrain-7",
    )
    examples: Dict[str, Any] = {
        "intent": intent.to_dict(),
        "switch": {
            "hall_id": "hall-a",
            "trunks": [
                {"peer_hall": "hall-b", "ports": 32, "port_bw_gbps": 400.0},
                {"peer_hall": "hall-c", "ports": 8, "port_bw_gbps": 400.0},
            ],
            "allocations": [
                {"circuit_id": "stitch-ab-1", "peer_hall": "hall-b",
                 "bw_gbps": 4000.0, "priority": 3, "job_id": "pretrain-7"}
            ],
            "demand": [
                {"circuit_id": "stitch-ab-2", "peer_hall": "hall-b",
                 "bw_gbps": 6000.0, "priority": 1, "job_id": "eval-2"}
            ],
        },
        "circuit": {
            "declared": {
                "circuit_id": "stitch-ab-1", "a_hall": "hall-a", "z_hall": "hall-b",
                "bw_gbps": 800.0, "rtt_us": 8.0, "path_km": 1.2, "il_db": 3.0,
                "connectors": 4,
            },
            "measured": {
                "circuit_id": "stitch-ab-1", "rtt_us": 40.0, "il_db": 6.4,
                "ber": 2e-11, "bw_gbps": 800.0, "temperature_c": 31.0, "age_s": 12.0,
            },
        },
        "rhythm": REFERENCE_RHYTHM,
    }
    if args.which:
        if args.which not in examples:
            print(f"no example named {args.which!r}; have "
                  + ", ".join(sorted(examples)), file=sys.stderr)
            return UNREADABLE
        print(json.dumps(examples[args.which], indent=2, sort_keys=True))
    else:
        print(json.dumps(examples, indent=2, sort_keys=True))
    return OK


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ocintent",
        description="What to ask an optical plant for, when it is legal, and what it costs.",
    )
    p.add_argument("--version", action="version", version=f"ocintent {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("compile", help="compile an intent into a vendor-neutral plan")
    c.add_argument("intent", help="path to an intent JSON, or - for stdin")
    c.add_argument("--boundary", choices=[b.value for b in Boundary])
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_compile)

    c = sub.add_parser("ladder", help="the retune legality ladder for a job rhythm")
    c.add_argument("--retune-s", type=float, default=60.0)
    _add_rhythm_flags(c)
    c.set_defaults(func=cmd_ladder)

    c = sub.add_parser("disagree", help="retune times where the two objectives differ")
    _add_rhythm_flags(c)
    c.set_defaults(func=cmd_disagree)

    c = sub.add_parser("radix", help="port occupancy, stranded ports, preemption")
    c.add_argument("switch", help="path to a switch JSON, or - for stdin")
    c.set_defaults(func=cmd_radix)

    c = sub.add_parser("checkpoint", help="stop tax and contention tax by strategy")
    c.add_argument("--state-tb", type=float, default=4.2)
    c.add_argument("--local-write-gbps", type=float, default=60.0,
                   help="local aggregate write bandwidth in GB/s")
    c.add_argument("--stitch-gbps", type=float, default=400.0)
    c.add_argument("--stitch-share", type=float, default=0.5)
    _add_rhythm_flags(c)
    c.set_defaults(func=cmd_checkpoint)

    c = sub.add_parser("drift", help="declared circuit against measured, and a forecast")
    c.add_argument("circuit", help="path to a circuit JSON, or - for stdin")
    c.add_argument("--il-rate", type=float, default=None,
                   help="measured insertion-loss trend in dB/year; enables the forecast")
    c.add_argument("--receiver-budget-db", type=float, default=18.0)
    c.add_argument("--target-ber", type=float, default=1e-12)
    c.set_defaults(func=cmd_drift)

    c = sub.add_parser("ledger", help="the capacity-debt report")
    c.add_argument("ledger", help="path to a ledger JSON")
    c.add_argument("--now-s", type=float, default=0.0,
                   help="the instant to age against, in the ledger's own clock")
    c.add_argument("--budget", type=float, default=None,
                   help="exit 1 if outstanding debt exceeds this many accelerator-hours")
    c.set_defaults(func=cmd_ledger)

    c = sub.add_parser("example", help="print example inputs for the other subcommands")
    c.add_argument("which", nargs="?", help="intent, switch, circuit, or rhythm")
    c.set_defaults(func=cmd_example)

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
