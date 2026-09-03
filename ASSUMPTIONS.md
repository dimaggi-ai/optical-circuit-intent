# Assumptions

What this repository takes on faith. Each entry says what would be needed to
stop assuming it.

## A1 — The reference rhythm is illustrative

16,384 accelerators, a 2.4 s step, a 0.31 s cross-stitch collective, a
checkpoint every 250 steps taking 120 s, an epoch every 4,000 steps. These are
plausible for a large synchronous pretrain and they are not measured from any
named job. Every headline figure is quoted against them.

*To stop assuming:* a rhythm measured from a real run. The random-population
results are there partly because they do not depend on this one.

## A2 — Contention shares a circuit linearly

While a background replication is in flight, a collective gets
`1 - stitch_share` of the circuit and everything scales proportionally. There is
no queueing model, no congestion control, no incast, and no accounting for the
fact that a collective's completion time is set by its slowest participant
rather than by mean bandwidth.

*To stop assuming:* a fluid or packet-level model, and a measurement to
calibrate it against.

## A3 — The drift tolerances are conventions

10% on round-trip time, 20% on insertion loss, 25% on bandwidth. Round-trip is
tightest because it is the field a compiler plans against; bandwidth is loosest
because a circuit rarely delivers nameplate. These are house conventions, not
measurements, and `compare()` takes an override.

## A4 — The margin-to-Q relation is first order

`Q = 6 · 10^(margin_db/10)`, the thermal-noise-limited direct-detection
relation, or `6 · 10^(margin_db/20)`, the OSNR-limited relation in which Q
squared follows the signal-to-noise ratio (the code calls it `coherent`),
anchored at the receiver's rated threshold. Both ignore chromatic
dispersion, fibre nonlinearity, and every amplifier's noise contribution.
Their job is to get the *shape* of the cliff right — steep, and steeper the
closer you are — not to predict a number for a particular receiver. On every
wavelength of the one measured link (S6) that failed, the drop was closer to
the 20 dB branch than to the 10 dB one, and steeper than both; the default
remains direct (DECISIONS.md D15).

*To stop assuming:* a link budget for a named receiver, with an OSNR model.

## A5 — Connector contamination is not modelled

The forecast extrapolates a loss *trend*. Contamination is an event: someone
unmates a connector and mates it dirty, and the loss steps. It is the most
common cause of real insertion-loss faults and the predictor is structurally
blind to it, so a green forecast is not a statement that a path is healthy.

## A6 — A retune is one scalar duration

Real reconfiguration has a distribution, a failure probability, and a rollback
cost. The legality ladder takes a single number.

*To stop assuming:* per-operation timing distributions from a plant, which turns
every point estimate here into a quantile.

## A7 — Ports are the unit of allocation

A request consumes whole ports on one trunk. Wavelength-level grooming,
sub-rating, and muxponders that let two circuits share a port are not modelled,
and any of them would reduce the stranding this repository reports.

## A8 — A job's checkpoint state is one number

`state_bytes` is a total. Sharded checkpoints where each rank writes its own
slice, optimiser state that is materialised differently from weights, and
incremental checkpointing are all outside the model.

## A9 — Debt accrues linearly while an entry stays open

A drifted circuit costs the same number of accelerator-hours on day 90 as on day
1. Real degradation is not linear, and a job may be rescheduled away from a bad
path, which would stop the accrual without anyone closing the entry.

## A10 — The slowdown fraction is the caller's number

`debt_from_drift` needs to know what fraction of a job's wall clock a drifted
path adds. That depends entirely on the job's communication profile: a 5×
round-trip on a path touched once per epoch is nearly free, and the same path
under a tensor-parallel collective is fatal. The function takes it as an
argument and refuses to guess.

## A11 — Pending demand is known

Stranded-port counts take the pending demand as given. In a plant, pending
demand is itself a forecast, and a wrong one changes the answer in both
directions.

## A12 — A laboratory link stands in for a plant

The measured points read one testbed: more than 100 km of fibre through four
amplifiers and a ROADM, with a fixed 16 dB attenuator for realism, and two
induced faults — a 3/4-inch macrobend and a transmit-side variable
attenuator. That its transponders are coherent and its error rate the
pre-FEC one is this repository's reading: the paper reports a bit error rate
under a 25% FEC overhead and uses neither word. The Layer-3 outcome is
availability from four UDP iperf sessions carrying a median 1.2 Gbit/s each
(the reader prints it); the paper's bend runs put them on a 600 Gbit/s
aggregated link. The iperf logs' stamps are output flushes and every
interval is re-timed from iperf's own interval field (DECISIONS.md D14); the
transponder is polled about every 1.6 s on a clock whose agreement with the
iperf hosts' the authors do not document, and this repository assumes they
agree to within a poll; every cross-clock lead is read at that precision.
Nothing measured there is assumed to hold on another plant.

*To stop assuming:* the same alarm-to-outage measurement on a production
aggregated link with synchronised clocks, a documented transponder type, and
a collective rather than a probe on the far side of it.

## A13 — A hackfest mock's context stands in for a plant's, and the example SIP table is illustrative

The only server the TAPI calls have been run through is a mock generated
from the 2.1.3 OpenAPI, run with the context file it ships (SOURCES.md S9).
Its service interface points are that file's, and the example table under
`examples/tapi-sip-table.json` names its SIPs after the ports they map
(`node-1-port-13-input`) so the renders can be read. That is a choice for
the examples, not a rule: on a plant the uuids are whatever the controller
reports, and the table is written by the plant owner, never derived. That a
photonic-layer point-to-point service with a media-channel qualifier and a
slot width is the right shape for a stitched circuit is this repository's
reading of the agreement's photonic use cases, and a plant may well want a
DSR service over it instead; the profile takes the layer as an argument.

*To stop assuming:* a SIP table exported from a real controller's context,
and one plan run against that controller.
