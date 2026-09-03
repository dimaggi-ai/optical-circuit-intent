# optical-circuit-intent

**What to ask an optical plant for, when it is legal to ask, and what the answer
costs.**

A training job that crosses a data hall boundary sits on a circuit somebody has
to provision, retune, and eventually admit is not what the inventory says it is.
This repository models six parts of that problem, in the unit the rest of the
series uses — accelerator-hours — so the numbers can be added up.

It is a companion to [span-contract](https://github.com/dimaggi-ai/span-contract),
which decides whether a job may span at all. This one is about the circuit
underneath that decision.

Since 1.2.0 the generic plan also compiles to one named controller
interface, the TAPI 2.1.5 data tree under the TR-547 v1.2 agreement, and
is handed back call by call with the reply each one expects. None is sent,
and no controller has been called ([below](#binding-to-a-named-plant)).

```
pip install optical-circuit-intent
ocintent ladder            # the retune legality ladder, at the reference rhythm
ocintent checkpoint        # what each checkpoint strategy actually costs
ocintent disagree          # where the two scheduling objectives part company
ocintent hedge             # the one measured link (from a clone, after `make data`)
ocintent tapi i.json --sip-table s.json   # the intent as one controller's calls, never sent
```

---

## Four findings

### 1. Two defensible retune objectives disagree over a 765-second band

A circuit retune has to wait for a boundary in the job's rhythm. Which boundary
you pick depends on what you are minimising, and there are two reasonable
answers:

- **`cheapest_legal`** minimises accelerator-hours lost. Waiting costs nothing,
  so it waits for a boundary where the retune is invisible.
- **`soonest_legal`** minimises total delay. Stalling costs time, so it takes
  the first boundary where the retune is merely expensive.

Both are defensible. At the reference rhythm — 16,384 accelerators, a 2.4 s
step, a checkpoint every 250 steps — they pick different boundaries for every
retune between **2.09 s and 767.09 s**:

```
retune of 60 s against a 16,384-accelerator job

  boundary             legality      quiet       wait    stall    accel-h
  mid-collective       stall          0.00        0.0    60.00      273.1
  between-steps        stall          2.09        1.2    57.91      263.6
  between-checkpoints  invisible    122.09      300.0     0.00        0.0
  between-epochs       invisible    167.09     4800.0     0.00        0.0
  between-jobs         invisible       inf    43200.0     0.00        0.0

  cheapest_legal  between-checkpoints  (0.0 accel-h, 300 s of delay)
  soonest_legal   between-steps  (263.6 accel-h, 59 s of delay)
```

The sharp part: **whether checkpoints cross the stitch changes that wait by
144×.** If they stay local, the cheap option waits 300 s for the next
checkpoint. If they replicate over the same circuit being retuned, no checkpoint
boundary is quiet, the cheap option falls through to the next *job* boundary,
and the wait becomes 43,200 s. A storage decision, made by a different team,
sets the cost of a network operation.

**Who this does not apply to.** A MEMS optical circuit switch reconfigures in
tens of milliseconds, which is below the lower edge of every band measured here.
For an intra-campus OCS the two objectives agree and the ladder is academic. The
disagreement is a *slow*-reconfiguration problem: ROADM provisioning, a metro
turn-up, a hand patch. That is the honest scope.

Reproduce: `make experiments`, or `python experiments/retune_disagreement.py`.

### 2. The cheapest durable checkpoint strategy changes twice inside the range of real plants

The stop tax — how long the job is halted — is visible in any training curve.
The contention tax — replication traffic stealing bandwidth from the next
collective — is invisible unless someone measures it. Counting both, the
cheapest strategy that survives losing a hall is not fixed:

```
  stitch           write-local       async-replicate        sync-replicate  stage-through-object
------------------------------------------------------------------------------------------------
    100G               10.45                 22.01*               35.90                35.90
    200G               10.45                 16.93*               21.88                21.88
    400G               10.45                 13.69                12.28*               12.28
    800G               10.45                 12.07                10.45                 6.54*
   1600G               10.45                 11.26                10.45                 3.38*
   3200G               10.45                 10.85                10.45                 1.72*
```

Percent of wall clock lost to checkpointing; `*` marks the cheapest strategy
that survives losing the hall it wrote in. `write-local` is never marked — it is
cheapest everywhere and durable nowhere.

At 400G the strategy with the **shortest checkpoint window** (async replicate,
70 s) is not the cheapest (sync replicate). The difference is entirely in
bandwidth stolen from collectives, which no training curve will show you.

### 3. A switch four-fifths empty can be unable to admit anything

Optical ports belong to trunks, and trunks lead somewhere specific. Free ports on
the wrong trunk are **stranded**: they exist, they are unallocated, and they
cannot serve the demand waiting for them.

```
  6/32 ports used, 26 free
  free on the trunk the demand wants: 2
  free on the trunk it does not:      24
  stranded: 26 (100% of free capacity)
```

Over 400 randomly generated switches and demands, the median switch has **83% of
its free ports stranded**, and 36% have every free port stranded. A capacity
report that counts free ports is counting the wrong thing.

### 4. On the one measured link, the first alarm ran 88 and 39 seconds ahead of the outage, and the outage began inside the poll in which the last wavelength failed

Everything above is a model. This one is a measurement, read from the raw
files of the HEDGE testbed (NSDI '26, `SOURCES.md` S6): more than 100 km of
fibre through four amplifiers and a ROADM, transponders polled about every
1.6 s for bit-error rate, uncorrectable-FEC count and received power, and
four UDP probes across the aggregated link. (That the transponders are
coherent and the error rate is the pre-FEC one is this repository's reading;
the paper says neither.) The authors bent the fibre in two runs and
attenuated it in a third. `make data` fetches the fifteen files from the
pinned commit and refuses any whose SHA-256 has changed; nothing is vendored,
because the upstream repository carries no license.

One thing had to be found out before the iperf logs could be read. Each
line's timestamp is the moment iperf's buffered output was flushed, not the
moment the interval ended, and it trails the interval by up to five seconds.
Every interval is therefore re-timed from iperf's own interval field,
anchored per log at its least-late line, and the four server logs' anchors
agree to within a few milliseconds (`DECISIONS.md` D14):

```
WHAT RE-TIMING THE IPERF LOGS MEASURED (per run, four server logs)
  run          intervals  anchor spread  lines >1 s late  latest stamp  probe median
  prototype         3888           5 ms    2900/3888            4.9 s   1.200 Gbit/s
  wdl               2580           4 ms    2306/2580            5.0 s   1.200 Gbit/s
  mod_formats       2412           3 ms    2353/2412            5.0 s   1.200 Gbit/s
```

```
ALARM-TO-OUTAGE LEAD, PER RUN (seconds after the run's first transponder sample)
  run           first FEC   last FEC  poll gap  link dark  lead, first   outage vs last alarm sample
  prototype       461.6 s    551.1 s     1.6 s    549.9 s       88.3 s     1.2 s before
  wdl             182.5 s    221.7 s     1.6 s    221.5 s       39.0 s     0.1 s before
  mod_formats     169.5 s    169.5 s     1.6 s      never        never         never
```

In both runs that lost the link, the first wavelength's uncorrectable-FEC
counter moved **88.3 s and 39.0 s** before the four probes went quiet, and
the probes went quiet **1.2 s and 0.1 s before** the *last* wavelength's alarm
sample, inside the 1.6 s poll interval in which that wavelength failed. The
aggregation did what it is for: the first alarm was a warning and the last
failure was the outage, to the transponder's own resolution and no finer.
In the third run only 16-QAM failed and traffic never stopped.

That is the shape of the published runs, not a law. Outside the authors'
analysis windows the same files hold further disturbances the paper does not
describe, and in one of them the link went dark 3.9 s before the last
wavelength's alarm sample, more than a poll; the experiment prints it.

The measurement changed two things in the drift model. Every wavelength that
failed did so at a pre-FEC error rate between 3.23e-2 and 3.45e-2, which is
10.5 decades above the 1e-12 target the forecast defaults to, so a forecast
for such a transponder has to count down to its FEC limit rather than to a
threshold-receiver figure. And on all nine failing wavelengths (eight of them
the paper's; the ninth is an unlabelled channel in the first bend run) the
received-power drop between BER onset and the cliff was closer to the 20 dB
per decade of Q that an OSNR-limited link implies than to the 10 dB per
decade of thermal-noise-limited direct detection the model shipped with, and
steeper than both:

```
  closer to the 20 dB branch on 9/9, to the 10 dB branch on 0, neither on 0; steeper than both on 9/9
  implied slopes run from 21.7 to 36.6 dB per decade of Q
```

The model now takes a `detection` argument. The default stays direct, because
the published anchors are written against it, and the registry prints the
disagreement, with each wavelength's implied slope, on every run.

**Who this does not apply to.** It is a laboratory link with two induced
faults. The Layer-3 outcome is availability from four iperf sessions carrying
a median 1.2 Gbit/s each (the paper's bend runs put them on a 600 Gbit/s
aggregate), not a collective and not capacity. The iperf side is re-timed
from iperf's own interval field; the transponder side is polled about every
1.6 s on a clock whose agreement with the iperf hosts' is undocumented, so
read a lead to the nearest poll, not to tenths. Nothing measured here
transfers to another plant as a constant, and none of it is a job's lead
time.

Reproduce from a clone: `make data && make experiments`, or
`python experiments/measured_lead_time.py`. `ocintent hedge` prints every
wavelength's onset, cliff, failure and recovery markers and what re-timing
measured; it needs the fetched files, which the pip package does not ship.

---

## The other three models

**Intent** (`ocintent.intent`) — a verb, two endpoints, a bandwidth, a hold time,
compiled into generic operations. No vendor session is opened; plans are
returned, not executed. A failover always verifies the replacement before
tearing the old path down, because the reverse order turns a degraded circuit
into no circuit.

**Drift** (`ocintent.drift`) — the campus bug: a YAML that says 800G at 8 µs when
the path is 40 µs with a dirty connector. Hashes the declared circuit, compares
it to the measured one, and refuses to compare fields at all when the plant and
the inventory disagree about *which* circuit this is. Forecasts when a
slowly-worsening path crosses an error-rate target, using the published
`0.5·erfc(Q/√2)` relation — steep enough that 2 dB of lost margin moves the error
rate by six orders of magnitude, which is why a gentle insertion-loss trend is
the only warning you get. The forecast takes a `detection` argument: direct
detection scales Q at 10 dB per decade, coherent at 20 dB, and on the one
measured link every failing wavelength's drop was closer to coherent than to
direct, and steeper than both (finding 4). A coherent transponder fails at
its pre-FEC limit, near 3e-2 on that link, so a forecast for one should be
given that as its target rather than the 1e-12 default.

**Ledger** (`ocintent.ledger`) — all of the above, aged and totalled in the shape
of an accounts-receivable schedule, because that is a format people already know
how to read:

```
outstanding capacity debt: 620,292 accelerator-hours
open entries: 4 of 4

by age
  0-7d                0    0.0%
  7-30d          57,014    9.2%
  30-90d          2,945    0.5%
  90d+          560,333   90.3%

by cause
  drift                         560,333
  checkpoint-contention          33,974
  stranded-ports                 23,040
  retune-stall                    2,945

by hall
  hall-a                        583,373
  hall-b                         36,919

fix these first (by daily rate, with payback)
  drift-stitch-ab-1       5,898/day     95 d old  pays back in 2.1 d
  ckpt-b                  4,247/day      8 d old  pays back in 1.9 d
  stranded-hall-a         1,152/day     20 d old  pays back in 0.8 d
  retune-ab                   0/day     40 d old  never pays back
```

Ranked by *daily rate*, not accrued total: a large old entry that has stopped
bleeding is a worse use of a maintenance window than a small new one that has
not. The ledger quotes no currency — that needs a rate only the plant owner has,
and one invented here would travel downstream looking like a measurement.

---

## Binding to a named plant

The six models agree on a generic plan: a verb, two endpoints, a hold, and
the operations that carry them out. `ocintent.adapters.tapi` writes that
plan as the calls one documented controller interface takes — the ONF
Transport API at its 2.1.5 data tree (SOURCES.md S7), under the TR-547 v1.2
reference implementation agreement (S8) — and hands them back with the
status, headers and fields each reply must carry. Nothing is sent. The plant
owner's table from `hall:port` to service interface point is an input, and
an endpoint the table does not know is refused with no call at all; the
adapter never guesses a SIP.

```
ocintent tapi examples/tapi-request.json --sip-table examples/tapi-sip-table.json \
    --slot-width-ghz 50 --now 2026-09-01T00:00:00Z
```

```
# request stitch-12-1 -> tapi-2.1.5/tr-547-v1.2  service 68e1972c-168f-5305-9db6-0fb5ac76cc58
1. [reserve_ports] GET /restconf/data/tapi-common:context/service-interface-point=node-1-port-13-input  expect 200  and administrative-state=UNLOCKED, operational-state=ENABLED
2. [reserve_ports] GET /restconf/data/tapi-common:context/service-interface-point=node-2-port-14-output  expect 200  and administrative-state=UNLOCKED, operational-state=ENABLED
3. [cross_connect] POST /restconf/data/tapi-common:context/tapi-connectivity:connectivity-context  expect 200/201  with Location
4. [verify_path] GET /restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service=68e1972c-168f-5305-9db6-0fb5ac76cc58  expect 200  and operational-state=ENABLED, lifecycle-state=INSTALLED, connection=non-empty
5. [verify_path] GET /restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/connection={connection-uuid}  expect 200
# unmapped reserve_ports: a read, not a reservation: TAPI 2.1 has no primitive that holds a service interface point for a caller (TR-547 Table 5 notes no use case modifies a SIP), so two requests can pass this read and race at the POST
# unmapped verify_path.expect_min_bw_gbps: 800.0 Gbit/s cannot be checked through TAPI 2.1 at PHOTONIC_MEDIA: requested capacity there is spectrum in GHz (TR-547 Table 23) and no modulation model here converts one to the other
# note: verify_path is not optional. A cross-connect that returns success and a path that carries traffic are different claims, and the gap between them is what the drift ledger measures.
```

The body of the POST follows: one `tapi-connectivity:connectivity-service`
entry carrying every client-mandatory attribute of the agreement's Tables 23
and 24, the two end points with the SIPs the table gave, a 50 GHz slot as
`requested-capacity` (spectrum, because the layer is photonic; the uint64
value goes as the JSON string RFC 7951 prescribes, DECISIONS.md D20) and
the hold as a `schedule` in the layout the `date-and-time` typedef's own
description gives, not RFC 3339 (D19). The uuid is derived from the circuit
id, so the same intent compiles to the same bytes every time. A failover adds the only destructive
call the binding ever emits, and it comes last, after the read that verifies
the replacement:

```
6. [teardown] DELETE /restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service=68e1972c-168f-5305-9db6-0fb5ac76cc58  expect 204
# unmapped reserve_ports: a read, not a reservation: TAPI 2.1 has no primitive that holds a service interface point for a caller (TR-547 Table 5 notes no use case modifies a SIP), so two requests can pass this read and race at the POST
# unmapped release_ports: nothing to call: the service interface points were never held, and deleting the service is what frees the plant's resources (TR-547 UC 10)
# note: the DELETE of the replaced service is the last call and follows the read that verifies the replacement; a controller that reorders these turns a degraded circuit into no circuit
# note: the replacement is verified before 'stitch-12-1' is torn down; the reverse order converts a degraded circuit into no circuit
```

```
# request stitch-19-1 -> tapi-2.1.5/tr-547-v1.2  service 5c9cec46-2f81-5b4c-854f-65d1cea21a65
# REFUSED: no SIP for endpoint node-9:port-1 in the table (examples/tapi-sip-table.json); the adapter does not guess one
```

What TAPI 2.1 cannot say, the plan says as *unmapped* rather than
pretending: there is no reservation primitive, so `reserve_ports` is two
reads and two callers can pass them and race at the create; a bandwidth
floor cannot be checked at the photonic layer, where capacity is spectrum; a
hold extension is a PUT of the whole service object under a use case the
agreement marks draft, so it is emitted only when the caller supplies the
object, and a LOCKED service is refused, with no call at all (DECISIONS.md
D18).

The one server the calls have been run through is a hackfest mock, not a
controller (S9). Its fourteen replies are committed under
`data/tapi/recorded` with their digests, and `experiments/tapi_departures.py`
prints where they depart from the agreement:

```
recorded calls: 14; on the adapter's table: 10; matching TR-547: 3; replies with a departure: 7
departures: 10 duplicate-accepted x1, encoding x1, no-location x2, status x6

what the mock does that a TR-547 controller must not:
  - answers a create with 204 and no Location header (UC 1.0: Location MUST; RFC 8040: 201)
  - answers a read or delete of an unknown uuid with 204 (UC 10 and RFC 8040 4.3: 404 invalid-value)
  - answers a PUT with 200 and the generator's stub text (UC 11a figure 6-43: 204)
  - accepts a second create with the same uuid (RFC 8040 4.4.1: 409)
  - echoes the uint64 capacity value as a JSON number (RFC 7951 6.1, which 2.6.1 mandates: a string)
so a green registry says the bodies parse on a 2.1.x server, not that a controller accepts them.
```

Seventeen registry points read the binding. Ten are calibrated against the
vendored YANG tree and the agreement's text: every emitted call is a Table 5
path with a standing method; the create body's keys are children of
`connectivity-service` in the 2.1.5 tree, carry the client-mandatory
attributes of Tables 23 and 24 under a module-qualified root key, and expect
the Location header UC 1.0 makes a MUST; the uuid is lowercase RFC 4122;
photonic capacity is in GHz and the unit is in the YANG enumeration; the
schedule times follow the layout the `date-and-time` typedef describes; a
delete names the service by uuid and expects 204; a hold extension is a PUT
of the whole object. Four are emergent: one destructive call per failover
and it is last, an unknown endpoint refuses with no call, the same intent
compiles to the same bytes, a service with no slot width omits the capacity
container. Three are sanity, on the recordings and manifests, which is all a
mock can support: a vendor's mock is not a conformance reference, and no
point here is calibrated against one.

---

## What this does not do

The validation registry prints its declined list *before* its results, every
run. The short version:

- **One measured link, and it is a laboratory.** The HEDGE testbed, two
  induced faults. Nothing has been compared against a production switch,
  ROADM or metro span, and the retune, radix, checkpoint and ledger models
  remain unmeasured.
- **The measured outcome is availability, not capacity.** Four UDP probes
  carrying a median 1.2 Gbit/s each, on a 600 Gbit/s aggregate in the bend
  runs. The iperf side is re-timed from iperf's own interval field; the
  transponder side is polled about every 1.6 s on a clock whose agreement
  with the iperf hosts' is undocumented. The leads are that link's, to the
  nearest poll and inside the authors' windows; none of them is a job's lead
  time and none transfers as a constant.
- **Two error-rate anchors, one relation.** Both pin `0.5·erfc(Q/√2)` at
  different places. A systematic error in that relation leaves both green. The
  six measured anchors pin the paper's reading of its own files, which checks
  the parsing and says nothing about the six models.
- **Connector contamination is invisible to the forecast.** It is an event, not a
  trend, and it is the most common cause of real insertion-loss faults. A green
  forecast is not a statement that a path is healthy.
- **No controller has been called.** The TAPI binding compiles calls and
  hands them back. The only replies on file are a hackfest mock's,
  generated from the 2.1.3 OpenAPI, and the departure experiment prints the
  ten places they contradict TR-547; a green registry says the bodies
  parse on a 2.1.x server, not that a controller accepts them. One profile
  ships, and TAPI 2.1 has no reservation primitive, so two callers can pass
  the reads and race at the create.
- **No queueing model.** Contention shares a circuit linearly between a
  replication and a collective, which is a simplification at every width quoted.

Run `make validate` for the other thirteen.

---

## Reproducing

```
make venv          # pinned virtual environment, Python 3.12
make data          # fetch and SHA-verify the 15 HEDGE testbed files (not vendored)
make test          # 276 tests, including 41 mutation cases
make validate      # 57 registry points, and the 19 things it declines to check
make examples      # 29 example inputs reach their documented results
make experiments   # the four figures above, and the mock's departure table
make smoke-test    # everything except experiments, under a minute
```

Every validation point is one of three kinds. **Calibrated** points are pinned
to a published figure or text (there are eighteen: two on the error-rate
relation, six on what the HEDGE paper says about its own runs, ten on the
TAPI 2.1.5 tree and the TR-547 v1.2 text). **Emergent** points are orderings
nothing was tuned to produce (twenty-one: six read from the measured link,
four from the compiled TAPI plans, eleven from the models). **Sanity** points
check this repository's own structure and are worth nothing as evidence about
optical plants (eighteen, three of them on the recorded mock) — they carry no
citation, and the code refuses to let them carry one. A point whose files are
missing — the measured link's, the vendored modules', the recordings' —
fails in the kind it declares; it never skips, and a mutation test measures
that with each file taken away.

The mutation tests break real machinery and assert the *exact* set of points
that turns red, measured rather than predicted. Three of them assert the
registry does **not** notice something, because it genuinely cannot: a
hundred-fold error in the fibre thermal coefficient, which is an input no
measurement here reaches; the Layer-3 join rewritten from "every probe quiet"
to "any probe quiet", because the four probes ride one link and go quiet
within a report interval of one another; and a deleted integrity check while
the files on disk are the pinned ones, which is what a tampered-copy unit test
is for.

Thirteen more break the binding — the module prefix dropped from the root
key, the constraints nested the way a 2.0 client did, PATCH for PUT,
gigabits at the photonic layer, a guessed SIP, the delete moved first, a
tampered recording, a vendored file or the SIP table taken away — and each
asserts its measured set. One is there because it first
reddened nothing: a create that stopped expecting the Location header UC 1.0
requires was invisible to the registry until a calibrated point was written
for it.

## Install

```
pip install optical-circuit-intent
```

No dependencies outside the standard library.

## Documents

- [`docs/the-models.md`](docs/the-models.md) — what each of the six is for
- [`docs/integration.md`](docs/integration.md) — wiring this to a scheduler
- [`data/hedge/README.md`](data/hedge/README.md) — the measured link's files: what they are, how they are fetched, why they are not vendored
- [`data/tapi/README.md`](data/tapi/README.md) — the binding's pinned specification, and the one mock's recorded replies
- [`DECISIONS.md`](DECISIONS.md) — twenty choices, and what each cost
- [`ASSUMPTIONS.md`](ASSUMPTIONS.md) — what is taken on faith
- [`SOURCES.md`](SOURCES.md) — the published figures the calibrated points use
- [`STATUS.md`](STATUS.md) — what is done, what is not, what would change it

Part of the [DIMAGGI usable-capacity series](https://dimaggi-ai.github.io/research).

MIT licensed. Copyright (c) 2026 Margaret Nanyonga.
