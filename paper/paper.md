# Optical Circuit Intent: What to Ask a Reconfigurable Plant For, and What the Answer Costs in Accelerator-Hours

**Margaret Nanyonga**, DIMAGGI AI

> Staged whitepaper. The claims, figures, and numbers here are reproduced
> by the accompanying open-source repository
> (`dimaggi-ai/optical-circuit-intent`, MIT).

## Abstract

A training job that crosses a data hall sits on a circuit somebody has to
provision, retune, and eventually admit is not what the inventory says it
is. We model six parts of that problem in one unit — accelerator-hours —
so the costs can be added up, and report four findings. **(1)** The two
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
ports is counting the wrong thing. **(4)** On the one measured link — the
HEDGE testbed, read from its raw files — the physical layer's first alarm
ran 88.3 s and 39.0 s ahead of the outage in the two runs that lost the
link, and the outage began inside the transponder poll in which the *last*
wavelength failed, 1.2 s and 0.1 s before its alarm sample, not after the
first's. Reading that required finding that the iperf logs' timestamps are
output flushes up to five seconds late and re-timing every interval from
iperf's own interval field. Every failing wavelength did so at a pre-FEC
error rate near 3e-2, decades above the 1e-12 target a threshold-receiver
forecast counts down to, and its power drop from BER onset to the cliff was
closer to the 20 dB per decade of Q an OSNR-limited link implies than to
the 10 dB relation the drift model shipped with, and steeper than both; the
model now carries both branches. It is a laboratory link with two induced
faults and an availability probe, and nothing measured on it transfers to
another plant as a constant.

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
the only warning anyone gets. The relation between margin and Q carries a
`detection` argument, direct at 10 dB per decade of Q or coherent at 20,
because on all nine wavelengths of the measured link that failed the drop
was closer to the 20 dB branch than to the 10, and steeper than both; the
default remains direct, since the published anchors are written against
it. The ledger ages every open debt in the shape
of an accounts-receivable schedule and ranks by *daily rate* rather than
accrued total: a large old entry that has stopped bleeding is a worse use
of a maintenance window than a small new one that has not. The ledger
quotes no currency; converting accelerator-hours to money needs a rate
only the plant owner has, and one invented here would travel downstream
looking like a measurement.

## 2. Validation

The registry holds forty points — eight calibrated, seventeen emergent,
fifteen sanity — and prints thirteen declined items before any result.
The suite is 174 tests and twenty-one examples, each checked
against its documented output. Mutation tests delete machinery and assert
the measured set of points that go red against a green control. Emergent
findings (the disagreement band, the double crossover, the stranding
fractions) were required to survive independent reseeding.

Fifteen points read a measurement. The HEDGE hardware-experiment files —
fifteen of them, three runs — are fetched from one pinned commit and
refused unless their SHA-256 matches; they are not redistributed, because
the upstream repository carries no license. Six points are calibrated
against what the paper says about its own runs, using the authors' own
failure and re-stabilisation markers and analysis windows: the format
failures at about 450, 500 and 550 s; traffic continuing until PM-QPSK
fails; the higher the format the earlier the failure and the less loss;
red and blue failing before green with the aggregate holding until green;
every labelled bend failure re-stabilising; only 16-QAM failing under the
format bend. "Until" is read to the transponder's own resolution: the
outage must begin within one poll gap of the failure sample, a bound set by
the data's resolution, not by the number. Six are emergent: the first-alarm
leads; the outage beginning within one poll of the last failure and long
after the first; a BER rise visible across more than one poll before every
FEC change; the format ordering of BER onsets under this repository's
onset marker, printed beside the marker it is not; every cliff decades
above the forecast's default target; and the scaling on all nine failures.
Three are sanity: the pins match; the four server logs' re-timing anchors
agree within one report interval; and the event-driven Layer-3 join agrees
with the authors' index-aligned reduction within two report intervals. A
missing file turns all fifteen red in their own kinds; none skips. Two
mutations are asserted blind spots rather than hidden ones: the join rule
("every probe quiet" against "any probe quiet") cannot be adjudicated by
data in which the four probes go quiet together, and a deleted integrity
check is invisible while the files on disk are the pinned ones. A third
mutation deletes the re-timing and places each interval at its flush
stamp, which is the reading this revision replaced: exactly the three
points that read the outage to the transponder's resolution go red.

## 3. What a skeptic should attack

The reference rhythm is one job, not a distribution over jobs, and every
band boundary moves with it. The contention tax assumes replication and
collectives share the stitch without a scheduler arbitrating between them.
Stranding fractions come from randomly generated switches, not a surveyed
plant. One link has been measured and it is a laboratory: two induced
faults, four UDP probes carrying a median 1.2 Gbit/s each (on a 600 Gbit/s
aggregate in the bend runs) as the Layer-3 outcome, iperf logs whose
stamps had to be re-timed from iperf's own interval field, and a
transponder polled about every 1.6 s on a clock whose agreement with the
iperf hosts' is undocumented, so a lead is good to the nearest poll. The
shape of the alarm-to-outage lead is the published runs'; outside the
authors' analysis windows the files hold further disturbances the paper
does not describe, and in one of them the link went dark more than a poll
before the last wavelength's alarm sample. That the transponders are
coherent and the error rate pre-FEC is this repository's reading of a
paper that says neither. The six measured anchors pin the paper's reading of its own
files, which validates the parsing and not the models; the retune, radix,
checkpoint and ledger models remain unmeasured, and the two error-rate
anchors pin a published relation, not a fleet measurement.

## 4. Conclusion

The recurring shape of these findings is that the visible cost is not the
deciding one: the stall a retune causes is visible while the checkpoint
policy that priced it is not; the stop tax is visible while the contention
tax is not; the free-port count is visible while the trunk that strands it
is not. The one measured link says the same thing one layer down: the
alarm that mattered was a counter on a transponder, moving 88 and 39
seconds before anything at Layer 3 noticed. Pricing all of it in one unit,
with the legality rules attached, is what lets a plant operator and a
training team disagree about a decision instead of discovering it.
