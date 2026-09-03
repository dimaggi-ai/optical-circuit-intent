# The six models

Each is usable alone. This is what each one is for, and what question it
answers.

---

## `intent` — what to ask for

A circuit request is four things: a verb, two endpoints, a bandwidth, and how
long you need it.

```python
from ocintent import Intent, Endpoint, Verb, compile_intent

intent = Intent(
    verb=Verb.REQUEST,
    circuit_id="stitch-ab-1",
    endpoints=(Endpoint("hall-a", "ocs-1/4"), Endpoint("hall-b", "ocs-3/9")),
    min_bw_gbps=800.0,
    hold_s=6 * 3600.0,
    job_id="pretrain-7",
)
plan = compile_intent(intent)
```

Four verbs: `REQUEST`, `HOLD_UNTIL`, `RELEASE`, `FAILOVER_TO`. Three rules the
constructor enforces:

- **A request needs a hold time.** A circuit with no stated duration is one the
  scheduler cannot plan around, and the most common way that shows up is a
  circuit nobody releases.
- **A failover names what it replaces.** Otherwise it is a request with a
  misleading verb.
- **A circuit's two endpoints are in different halls.** A stitch to yourself is
  a configuration error, not a circuit.

`compile_intent` returns operations, never executes them (DECISIONS D2). A
failover always orders `verify_path` before `teardown`:

```
1. reserve_ports    circuit_id=stitch-ac-1 ...
2. cross_connect    a=hall-a:ocs-1/7 z=hall-c:ocs-2/2
3. verify_path      circuit_id=stitch-ac-1
4. teardown         circuit_id=stitch-ab-1
5. release_ports    circuit_id=stitch-ab-1
```

The reverse order converts a degraded circuit into no circuit. The plan says so
in a note, and also warns that this needs both paths' ports free at once — if
the radix cannot hold both, the failover is a retune and must be scheduled as
one.

`Intent.digest()` is a stable hash over the canonical form. Reordering labels
does not change it; changing a bandwidth does.

---

## `legality` — when it is safe to ask

Five boundaries, ordered by how long the job is quiet at each:

| boundary | quiet window at the reference rhythm |
| --- | --- |
| `MID_COLLECTIVE` | 0 s |
| `BETWEEN_STEPS` | 2.09 s |
| `BETWEEN_CHECKPOINTS` | 122.09 s |
| `BETWEEN_EPOCHS` | 167.09 s |
| `BETWEEN_JOBS` | unbounded |

A retune shorter than the quiet window is **invisible**. Longer, and it
**stalls** — the excess is time every accelerator spends idle. Past the
collective timeout, it is **fatal**.

```python
from ocintent import JobRhythm, ladder, cheapest_legal, soonest_legal

rhythm = JobRhythm(16_384, 2.4, 0.31, 250, 120.0, False, 4_000, 45.0)
for cost in ladder(rhythm, retune_s=60.0):
    print(cost.boundary.value, cost.legality.value, cost.lost_accelerator_hours)
```

The two selectors optimise different things and the repository will not pick
between them (D3). `disagreement_intervals(rhythm)` returns the exact set of
retune durations where they differ, as a tuple of half-open intervals — computed
by enumerating breakpoints, not by bisection, because the set is frequently not
contiguous (D4).

`MID_COLLECTIVE` appears in the ladder and is never selected (D10).

---

## `radix` — whether the ports exist

```python
from ocintent import OpticalSwitch, Trunk, Request

switch = OpticalSwitch("hall-a", (
    Trunk("hall-b", 32, 400.0),
    Trunk("hall-c", 8, 400.0),
))
switch.allocate(Request("stitch-ab-1", "hall-b", 4000.0))
switch.stranded_ports([Request("want", "hall-b", 12800.0)])   # -> 8
```

Ports round up: 401 Gbit/s on a 400G trunk is two ports. A request larger than
its trunk is refused even when the switch has free ports elsewhere, and the
error message says so, because that is the mistake the module exists to name.

`preemption_plan` finds the cheapest set of strictly-lower-priority circuits to
evict. It enumerates subsets exactly rather than greedily, because greedy by
cost-per-port picks one wide expensive victim over two narrow cheap ones (D8).
The cost function is supplied by the caller — what an eviction costs is a
property of the victim's job, not of the switch. The natural cost is the
victim's own retune cost, which `legality` computes, so the decision prices in
the same accelerator-hours as everything else.

---

## `checkpoint` — what durability costs

Four strategies:

| strategy | crosses stitch | on critical path | survives hall loss |
| --- | --- | --- | --- |
| `WRITE_LOCAL` | no | yes | **no** |
| `ASYNC_REPLICATE` | yes | no | yes |
| `SYNC_REPLICATE` | yes | yes | yes |
| `STAGE_THROUGH_OBJECT` | yes | yes | yes |

Two taxes. The **stop tax** is how long the job halts — visible in any training
curve. The **contention tax** is bandwidth a background replication steals from
the next collective — invisible unless measured.

```python
from ocintent import CheckpointPlan, Strategy, compare_strategies, cheapest_durable

plan = CheckpointPlan(Strategy.WRITE_LOCAL, int(4.2e12), 60.0, 400.0, stitch_share=0.5)
for strategy, t in compare_strategies(plan, rhythm).items():
    print(strategy.value, t.stop_s, t.total_fraction)
```

`stitch_share` applies only to *background* transfers. A transfer on the critical
path gets the whole circuit, because the job is stopped and no collective is
competing for it. `ingest_budget_GBps` caps the transfer across the stitch, not
the local write (D9).

`classify_stall` is the fidelity rule: a PFS stall is not an ICI break, the two
look identical from the training loop, and it returns `AMBIGUOUS` rather than
picking the more likely one. A confident wrong answer here sends someone to
rebuild a fabric that was never broken.

---

## `drift` — what the plant is actually doing

```python
from ocintent import DeclaredCircuit, MeasuredCircuit, compare_circuit, forecast

declared = DeclaredCircuit("stitch-ab-1", "hall-a", "hall-b", 800.0, 8.0, 1.2, 3.0)
measured = MeasuredCircuit("stitch-ab-1", rtt_us=40.0, il_db=6.4, ber=2e-11, bw_gbps=800.0)
report = compare_circuit(declared, measured)
report.verdict          # DriftVerdict.DRIFTED
report.topology_hash    # what a compile cache keys against
```

Three verdicts. `MATCHES`, `DRIFTED`, and `WRONG_CIRCUIT` — the last when the
measurement's circuit id is not the declared one, in which case **no field
comparisons are produced at all**, because comparing circuit A's fields against
circuit B's measurements produces confident nonsense.

The forecast answers when a slowly-worsening path crosses an error-rate target:

```python
forecast("stitch-ab-1", il_now_db=14.0, il_rate_db_per_year=1.2,
         receiver_budget_db=18.0).explain()
# 14.00 dB now, rising 1.200 dB/year, 4.00 dB of margin left;
# crosses a 1e-12 error rate in 2.76 years
```

A trend that does not cross within the horizon says so, distinctly from a path
with no trend at all — the two were once reported identically, which made a
real trend look like a flat line.

The forecast takes a `detection` argument. Direct detection scales Q at 10 dB
per decade, coherent at 20 dB (`DB_PER_DECADE_OF_Q`); the default is direct
because the published anchors are written against it. A coherent transponder
does not fail at 1e-12 — on the one measured link every wavelength failed at
a pre-FEC error rate near 3e-2 — so a forecast for one is given that limit:

```python
forecast("stitch-ab-1", il_now_db=14.0, il_rate_db_per_year=1.2,
         receiver_budget_db=18.0, target_ber=3e-2, detection="coherent")
```

`margin_span_db(ber_from, ber_to, detection=...)` answers the reverse
question — how many decibels of margin separate two error rates — and is
what the measured link is compared against (`ocintent.hedge.scaling_check`).

---

## `ledger` — what it all costs

```python
from ocintent import Ledger, DebtEntry, Cause, debt_from_drift

led = Ledger()
led.open_entry(debt_from_drift(report, hall="hall-a", opened_at_s=0.0,
                               accelerators=4096, slowdown=0.06,
                               remediation_accelerator_hours=4096 * 3))
print(led.report(now_s=95 * 86_400))
```

Entries carry an upfront cost and a daily rate. Outstanding debt is the sum, so
it grows while nothing is fixed — which is the whole argument for a maintenance
window, and `payback_days()` says how long that window takes to pay for itself.

`worst_first` ranks by daily rate rather than accrued total. A large old entry
that has stopped bleeding is a worse use of a window than a small new one that
has not.

Two bridges convert other models into entries: `debt_from_drift` and
`debt_from_stranded_ports`. Both take the conversion factor as a caller argument
(ASSUMPTIONS A10), because neither the drift report nor the switch knows how
much the job cares.

---

## `hedge` — what one real link did

Not a model: a reader for the raw files of the HEDGE testbed (SOURCES.md S6),
fetched and SHA-pinned by `make data`. Three runs — a bend across three
wavelengths, the same bend across three modulation formats, and a
transmit-side attenuator sweep — each with a transponder log (BER,
uncorrectable-FEC counter, received power, per wavelength) and four iperf
server logs.

```python
from pathlib import Path
from ocintent import hedge

summaries = hedge.load_summaries(Path("data/hedge"))   # raises if any file is missing or altered
wdl = summaries["wdl"]
wdl.link_lost_s                              # 221.5: the four probes all quiet
wdl.lead_first_failure_to_link_loss_s        # 39.0: first FEC alarm to outage
wdl.lead_last_failure_to_link_loss_s         # -0.1: the outage began just before the last alarm sample
wdl.last_failure_poll_gap_s                  # 1.6: the transponder's resolution on that failure
[(t.name, t.failed_s) for t in wdl.failures] # each wavelength's first uncorrectable FEC
wdl.retiming[0].max_late_s                   # 5.0: how far server 1's stamps trailed their intervals
hedge.scaling_check(wdl.failures[0]).closer  # "coherent"; .steeper_than_both is True
print(hedge.report(summaries))               # what `ocintent hedge` prints
```

The iperf logs' stamps are output flushes up to five seconds late, so every
interval is re-timed from iperf's own interval field, anchored per log at
its least-late line; `Run.retiming` carries what that measured, and the
Layer-3 dark intervals run from the start of the first interval that left
every server at zero (DECISIONS.md D14).

Every marker is the authors' own (DECISIONS.md D14): failure is the first
sample whose FEC counter exceeds its initial value, re-stabilisation the
first index followed by five equal readings, and the analysis window each
notebook's plot limit. The BER onset — the last pre-failure sample at or
below the window-start BER — is the one marker the paper does not define,
and it has no parameter. It is this repository's choice; the registry
prints what the obvious alternative gives beside it.

What the reader will not do: skip. A missing or altered file raises
`HedgeDataError` with the `make data` instruction in it, `ocintent hedge`
exits 2, and every registry point that reads the link fails in its own kind
(D13).

---

## `adapters.tapi` — the same plan, as one controller's calls

Not a seventh model: a binding. `compile_plan` takes the generic plan the
`intent` model returns and writes it as the RESTCONF calls of one documented
controller interface, the ONF Transport API at its 2.1.5 data tree under the
TR-547 v1.2 reference implementation agreement (SOURCES.md S7, S8). Each call
carries the status, headers and fields the agreement says a conforming reply
has. Nothing is sent.

```python
import json, time
from ocintent.intent import Intent
from ocintent.adapters import tapi

it = Intent.from_dict(json.load(open("examples/tapi-request.json")))
tp = tapi.compile_intent_to_tapi(
    it,
    profile=tapi.Profile(layer="PHOTONIC_MEDIA", slot_width_ghz=50),
    sip_table=tapi.SipTable.from_json("examples/tapi-sip-table.json"),
    now_s=time.time(),
)
tp.refused                        # None, or the sentence saying why no call was emitted
[c.method for c in tp.calls]      # ['GET', 'GET', 'POST', 'GET', 'GET']
tp.calls[2].body                  # the create body, under "tapi-connectivity:connectivity-service"
tp.calls[2].expect_status         # (200, 201); tp.calls[2].expect_headers is ('Location',)
[u.operation for u in tp.unmapped]  # ['reserve_ports', 'verify_path.expect_min_bw_gbps']
tp.destructive_calls              # () for a request; one DELETE, last, for a failover or release
print(tp.render())                # the numbered calls `ocintent tapi` prints
```

The SIP table is the plant owner's: `hall:port` to service interface point
uuid, qualifier and direction, committed beside the intents. An endpoint it
does not hold is refused with no call, and two endpoints whose directions
cannot form a service are refused the same way; the adapter never derives a
SIP from a port name, because the two are named by different people for
different reasons. The uuid of the service is derived from the circuit id
under a fixed namespace, so the same intent compiles to the same bytes and a
retry sends the same create.

What TAPI 2.1 cannot express is returned as `unmapped`, with the reason:
there is no reservation primitive, so `reserve_ports` is two reads of the
SIPs and `release_ports` is nothing; a bandwidth floor cannot be checked at
the photonic layer, where `requested-capacity` is a slot width in GHz.
`extend_hold` is a PUT of the whole service object under a use case TR-547
v1.2 marks draft (DECISIONS.md D18): it is emitted only when the caller
passes `current_service`, the object as the controller last returned it,
with its read-only attributes stripped and only the end time changed, and a
service whose administrative state is LOCKED is refused.

The profile is one dataclass (`tapi.Profile`) holding the YANG tag, the
RESTCONF root, the layer, the slot width and the service type; one ships,
and a 2.5.x profile is a second instance once public 2.5 fixtures exist
(DECISIONS.md D17). The vendored 2.1.5 modules and the fourteen recorded
replies of the one mock the calls have been run through live under
`data/tapi` with their manifests; `data/tapi/README.md` says what each is
and how to re-record.
