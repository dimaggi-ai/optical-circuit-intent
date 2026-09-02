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

`Q = 6 · 10^(margin_db/10)`, anchored at the receiver's rated threshold. It
ignores chromatic dispersion, fibre nonlinearity, and every amplifier's noise
contribution. Its job is to get the *shape* of the cliff right — steep, and
steeper the closer you are — not to predict a number for a particular receiver.

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

