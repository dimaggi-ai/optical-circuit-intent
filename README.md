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

```
pip install optical-circuit-intent
ocintent ladder            # the retune legality ladder, at the reference rhythm
ocintent checkpoint        # what each checkpoint strategy actually costs
ocintent disagree          # where the two scheduling objectives part company
```

---

## Three findings

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
179×.** If they stay local, the cheap option waits 300 s for the next
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
the only warning you get.

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

## What this does not do

The validation registry prints its declined list *before* its results, every
run. The short version:

- **No measured plant.** Every number is a model output. Nothing has been
  compared against a real optical switch, ROADM, or metro span.
- **Two calibrated points, one relation.** Both anchors pin `0.5·erfc(Q/√2)` at
  different places. A systematic error in that relation leaves both green.
- **Connector contamination is invisible to the forecast.** It is an event, not a
  trend, and it is the most common cause of real insertion-loss faults. A green
  forecast is not a statement that a path is healthy.
- **No queueing model.** Contention shares a circuit linearly between a
  replication and a collective, which is a simplification at every width quoted.

Run `make validate` for the other seven.

---

## Reproducing

```
make venv          # pinned virtual environment, Python 3.12
make test          # 137 tests, including 17 mutation tests
make validate      # 25 registry points, and the 11 things it declines to check
make examples      # 17 example inputs reach their documented results
make experiments   # the three figures above
make smoke-test    # everything except experiments, under a minute
```

Every validation point is one of three kinds. **Calibrated** points are pinned
to a published figure (there are two). **Emergent** points are orderings nothing
was tuned to produce (eleven). **Sanity** points check this repository's own
structure and are worth nothing as evidence about optical plants (twelve) — they
carry no citation, and the code refuses to let them carry one.

The mutation tests break real machinery and assert the *exact* set of points that
turns red. One of them asserts the registry does **not** notice a hundred-fold
error in the fibre thermal coefficient, because it genuinely cannot: that value
is an input, and closing the gap needs a measurement from a real span.

## Install

```
pip install optical-circuit-intent
```

No dependencies outside the standard library.

## Documents

- [`docs/the-models.md`](docs/the-models.md) — what each of the six is for
- [`docs/integration.md`](docs/integration.md) — wiring this to a scheduler
- [`DECISIONS.md`](DECISIONS.md) — twelve choices, and what each cost
- [`ASSUMPTIONS.md`](ASSUMPTIONS.md) — what is taken on faith
- [`SOURCES.md`](SOURCES.md) — the published figures the calibrated points use
- [`STATUS.md`](STATUS.md) — what is done, what is not, what would change it

Part of the [DIMAGGI usable-capacity series](https://dimaggi-ai.github.io/research).

MIT licensed. Copyright (c) 2026 Margaret Nanyonga.
