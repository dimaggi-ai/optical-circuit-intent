# Wiring this to a scheduler

Nothing here is a service. Every model is a function over data you already have,
and this is the order to call them in.

## The sequence

```
                job wants to span
                        |
             [span-contract] may it?          <- separate repository
                        |  yes
                        v
        1.  radix        are the ports there, on the right trunk?
                        |
        2.  drift        is the path what the inventory says it is?
                        |
        3.  legality     when may we retune, and which objective?
                        |
        4.  intent       compile the plan; a human or a controller runs it
                        |
        5.  checkpoint   price durability against the width we actually got
                        |
        6.  ledger       whatever we refused or degraded becomes a line
```

Steps 1–3 can refuse. Step 6 runs on every refusal, which is the point: a
refused span is not a non-event, it is capacity the owner paid for and did not
get, and it belongs in the balance.

## 1. Ports, before anything else

```python
from ocintent import OpticalSwitch, Trunk, Request, preemption_plan

switch = OpticalSwitch(hall_id, trunks)          # from your inventory
request = Request(circuit_id, peer_hall, bw_gbps, priority, job_id)

try:
    switch.allocate(request)
except RadixExhausted:
    plan = preemption_plan(switch, request, cost_of_evicting=retune_cost)
    if plan is None:
        # nothing to take. Log the stranded ports as debt and refuse.
        ...
```

Use `legality` for `cost_of_evicting` so the eviction decision prices in the same
unit as everything else:

```python
def retune_cost(allocation):
    rhythm = rhythms[allocation.job_id]
    chosen = cheapest_legal(rhythm, expected_retune_s)
    return chosen.lost_accelerator_hours if chosen else float("inf")
```

## 2. Check the path before you plan against it

```python
report = compare_circuit(declared_from_inventory, measured_from_telemetry)
if report.verdict is DriftVerdict.WRONG_CIRCUIT:
    # stop. The inventory and the plant disagree about what this is.
    # Every downstream number would be about a different circuit.
    raise InventoryError(report.note)
if report.verdict is DriftVerdict.DRIFTED:
    # plan against the measured path, not the declared one,
    # and open a ledger entry for the difference.
```

Planning against a declared 8 µs path that measures 40 µs is the campus bug. The
compiler produces a schedule that cannot be met and the job discovers it at run
time.

If the path is a coherent transponder, forecast it to its FEC limit with the
coherent scaling — `forecast(..., target_ber=3e-2, detection="coherent")` —
rather than the 1e-12 threshold-receiver default. That is the direction the
one measured link points (`ocintent hedge`; README finding 4): every failing
wavelength was closer to that scaling than to the default, and steeper than
both. It is the difference between an alarm with tens of seconds of lead and
a forecast that fires years early.

## 3. Decide when, and say which objective you used

```python
chosen = cheapest_legal(rhythm, retune_s) if job.is_batch else soonest_legal(rhythm, retune_s)
if chosen is None:
    raise NoLegalBoundary(...)
```

Record which one you called. `objectives_disagree(rhythm, retune_s)` returns the
other answer and the gap; logging it is how anyone later understands why a job
waited four hours for a sixty-second operation.

## 4. Compile, do not execute

```python
plan = compile_intent(intent, boundary=chosen.boundary)
for op in plan.operations:
    controller.dispatch(op.call, **op.args)      # your driver, not ours
```

`verify_path` is in every plan that creates a circuit and is not optional. A
cross-connect that returns success and a path that carries traffic are different
claims; the gap between them is exactly what step 2 measures next time.

When the plant speaks TAPI, the binding writes the same plan as the calls
TR-547 v1.2 documents and hands them back with what each reply must contain
(`docs/the-models.md`, `adapters.tapi`):

```python
from ocintent.adapters import tapi

tp = tapi.compile_plan(plan, profile=tapi.Profile(slot_width_ghz=50),
                       sip_table=tapi.SipTable.from_json("sips.json"), now_s=now)
if tp.refused:
    raise NoSuchEndpoint(tp.refused)                      # nothing was emitted
for call in tp.calls:
    reply = session.request(call.method, call.path, json=call.body)   # your session
    if reply.status not in call.expect_status or any(h not in reply.headers for h in call.expect_headers):
        break                                             # the destructive call, if any, is last
```

Log `tp.unmapped` beside the plan. It lists what TAPI 2.1 cannot express — a
reservation, a bandwidth floor at the photonic layer — and a controller that
returned success has checked none of it.

## 5. Price durability against the width you got

Not the width you asked for. The cheapest durable checkpoint strategy changes
with circuit width, so a job that was admitted at 400G instead of 800G may want
a different strategy than the one in its config.

```python
plan = CheckpointPlan(current_strategy, state_bytes, local_GBps, actual_gbps, share)
best = cheapest_durable(plan, rhythm)
if best.strategy is not current_strategy:
    # worth telling someone. The difference is usually invisible in the curve.
```

## 6. Everything you refused becomes a line

```python
led.open_entry(debt_from_stranded_ports(hall, switch.stranded_ports(pending),
                                        accelerators_per_port, opened_at_s=now))
led.open_entry(debt_from_drift(report, hall=hall, opened_at_s=now,
                               accelerators=job.accelerators, slowdown=profile.slowdown,
                               remediation_accelerator_hours=window_cost))
```

Close entries when the underlying thing is fixed, not when the ticket is closed.
`Ledger.total(now, open_only=False)` keeps the history.

## Exit codes, if you shell out

Every `ocintent` subcommand uses the same three:

| code | meaning |
| --- | --- |
| 0 | legal, healthy, or satisfiable |
| 1 | refused, illegal, unsatisfiable, or over budget — an answer, not an error |
| 2 | the input could not be read — an error |

## What you still have to supply

- **A rhythm per job.** Step time, collective time, checkpoint cadence. Most
  schedulers have these or can measure them in one epoch.
- **A slowdown fraction** for `debt_from_drift`. It depends on the job's
  communication profile and nothing here can derive it (ASSUMPTIONS A10).
- **Accelerators per port** for `debt_from_stranded_ports`. A property of your
  build.
- **A currency rate**, if you want one. `Ledger.priced()` requires it and has no
  default (DECISIONS D6).
