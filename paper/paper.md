# Optical Circuit Intent: What to Ask a Reconfigurable Plant For, and What the Answer Costs in Accelerator-Hours

**Margaret Nanyonga**, DIMAGGI AI

> Staged whitepaper. The claims, figures, and numbers here are reproduced
> by the accompanying open-source repository
> (`dimaggi-ai/optical-circuit-intent`, MIT).

## Abstract

A training job that crosses a data hall sits on a circuit somebody has to
provision, retune, and eventually admit is not what the inventory says it
is. We model six parts of that problem in one unit — accelerator-hours —
so the costs can be added up, and report three findings. **(1)** The two
defensible retune objectives disagree. Minimising accelerator-hours lost
(`cheapest_legal`) and minimising total delay (`soonest_legal`) pick
different boundaries in the job's rhythm for every retune between 2.09 and
767.09 seconds at a reference rhythm of 16,384 accelerators, a 2.4 s step,
and a checkpoint every 250 steps. The sharp part is who sets the price:
if checkpoints replicate *over the stitch being retuned*, no checkpoint
boundary is quiet, and the cheap option's wait grows 144× — from 300 s to
43,200 s. A storage decision, made by a different team, sets the cost of a
network operation. The finding is scoped honestly: a MEMS optical circuit
switch reconfigures below the bottom of every band measured here, so this
is a slow-reconfiguration result — ROADM provisioning, a metro turn-up, a
hand patch — not an intra-campus OCS result. **(2)** The cheapest durable
checkpoint strategy changes twice inside the range of real plants: async
replication wins at 100–200G, synchronous at 400G, staging through an
object store from 800G up; writing locally is cheapest everywhere and
durable nowhere. At 400G the strategy with the shortest checkpoint window
is *not* the cheapest — the difference is bandwidth stolen from the next
collective, which no training curve will show. **(3)** Optical ports
belong to trunks, and free ports on the wrong trunk are stranded: over 400
randomly generated switches, the median switch has 83% of its free ports
stranded and 36% have every free port stranded — a switch four-fifths
empty can be unable to admit anything. A capacity report that counts free
ports is counting the wrong thing.

## 1. Model

An intent is a verb, two endpoints, a bandwidth, and a hold time, compiled
into vendor-neutral operations; plans are returned, not executed, and a
failover always verifies the replacement circuit before tearing the old
one down, because the reverse order turns a degraded circuit into no
circuit. The drift model hashes the declared circuit against the measured
one and refuses to compare fields at all when plant and inventory disagree
about *which* circuit this is; it forecasts when a slowly-worsening path
crosses an error-rate target through the published `0.5·erfc(Q/√2)`
relation, steep enough that 2 dB of lost margin moves the error rate by
six orders of magnitude — which is why a gentle insertion-loss trend is
the only warning anyone gets. The ledger ages every open debt in the shape
of an accounts-receivable schedule and ranks by *daily rate* rather than
accrued total: a large old entry that has stopped bleeding is a worse use
of a maintenance window than a small new one that has not. The ledger
quotes no currency; converting accelerator-hours to money needs a rate
only the plant owner has, and one invented here would travel downstream
looking like a measurement.

## 2. Validation

The registry holds twenty-five points — two calibrated, eleven emergent,
twelve sanity — and prints eleven declined items before any result. The
suite is 137 tests and seventeen examples, each checked against its
documented output. Mutation tests delete machinery and assert the measured
set of points that go red against a green control. Emergent findings
(the disagreement band, the double crossover, the stranding fractions)
were required to survive independent reseeding.

## 3. What a skeptic should attack

The reference rhythm is one job, not a distribution over jobs, and every
band boundary moves with it. The contention tax assumes replication and
collectives share the stitch without a scheduler arbitrating between them.
Stranding fractions come from randomly generated switches, not a surveyed
plant. No circuit modelled here has been compared against a provisioned
one, and the two calibrated points pin published relations, not fleet
measurements.

## 4. Conclusion

The recurring shape of these findings is that the visible cost is not the
deciding one: the stall a retune causes is visible while the checkpoint
policy that priced it is not; the stop tax is visible while the contention
tax is not; the free-port count is visible while the trunk that strands it
is not. Pricing all of it in one unit, with the legality rules attached,
is what lets a plant operator and a training team disagree about a
decision instead of discovering it.
