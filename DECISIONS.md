# Decisions

Twelve choices this repository made, why, and what each one cost. Several were
made *after* an earlier version shipped something wrong; those say so.

## D1 — Six models in one repository, not six repositories

The retune ladder, the radix model, the checkpoint tax, the drift comparison and
the ledger are separately useful. They are together because the ledger needs all
of them to produce a balance, and a balance that only covers one failure mode is
worse than no balance — it makes the covered mode look like the whole problem.

**Cost.** Someone who wants only the intent language installs five models they
will not use. Mitigated by keeping every runtime edge between modules absent:
`checkpoint` imports `legality` only for type checking, and `ledger` imports
`drift` inside a function body.

## D2 — No vendor session. Plans are returned, not executed

`compile_intent` produces a list of generic operations — `reserve_ports`,
`cross_connect`, `verify_path` — and hands them back. Nothing here opens a
TL1 session, a NETCONF session, or a REST client.

Partly this is scope. Mostly it is that a plan you can read is reviewable and a
side effect is not, and the operations that matter here are the ones a human
should see before they run.

**Cost.** "Compiles" means "produces operations", not "works". No plant has
accepted one of these plans. That is item 9 of the declined list and it stays
there until there is a plant to try it on.

## D3 — Two objectives, both kept, neither defaulted

`cheapest_legal` and `soonest_legal` disagree over a wide band, and the
repository refuses to pick. Making one the default would encode a judgement
about whether an idle accelerator-hour is worth more than a delayed result,
which depends on the contract the plant owner signed and is not a fact about
optics.

**Cost.** A caller has to choose. `ocintent ladder` prints both and names the
gap, which is the most this can honestly do.

## D4 — The disagreement set is computed exactly, not bisected

An earlier version bisected for a single upper edge and returned one band. Two
things were wrong with it. First, it bisected the wrong predicate — it searched
for where the urgent objective stopped choosing `between-steps`, but the
disagreement continued past that point. Second, and worse, once the predicate
was fixed, a dense scan found **99 of 300 random rhythms had a non-contiguous
disagreement set**, so the returned "edge" was not an edge at all. The registry's
own population, generated differently, puts it at 50 of 400 — a smaller fraction,
and still far too many for a single band to be the answer.

The replacement enumerates the three finite families of breakpoints — the quiet
window at each boundary, that window plus the collective timeout, and that
window plus the difference of two waits — and decomposes the set exactly. No
grid, no tolerance, no bisection.

**Cost.** More code, and a result that is a tuple of intervals rather than a
number. Worth it: a number that is wrong a third of the time is not a result.

## D5 — Durability is reported, not netted off as a tax

`write-local` is cheapest at every stitch width in the table and does not
survive losing the hall it wrote in. It would be easy to express that as an
expected cost — probability of hall loss times the cost of losing the run — and
produce one comparable number.

This does not, because the probability is not knowable from anything in this
repository and a made-up one would silently dominate the answer. `Tax` carries
`survives_hall_loss` as a flag and `cheapest_durable` filters on it.

**Cost.** The caller supplies the risk judgement. The table cannot be sorted into
a single ranking.

## D6 — The ledger quotes accelerator-hours, never currency

Converting requires a rate — what an accelerator-hour is worth to *this* owner,
which depends on their contract, their utilisation, and whether an alternative
use of that hour exists. `Ledger.priced()` takes the rate as a required argument
and has no default.

**Cost.** A finance reader wanted a currency figure and gets a capacity figure.
The judgement is that a wrong currency figure is worse than an honest capacity
one, because the currency figure is the one that gets quoted in a slide with the
rate stripped off.

## D7 — A discriminator demoted because it did not discriminate

`StallEvidence.non_stitched_ranks_stalled` was originally read as evidence for a
fabric cause, on the reasoning that "a circuit fault propagates to off-stitch
ranks and a storage stall does not."

That is false. In a synchronous job every rank waits at the same barrier, so
*both* failures propagate to every rank and the signal separates neither. The
field survives because it is informative in exactly one case — when nothing else
is lit, it says the problem is neither the circuit nor the checkpoint — and it no
longer influences the storage-versus-fabric verdict at all.

**Cost.** A field that looks like it should do more than it does, which is why
this entry exists and why the code carries the explanation inline.

## D8 — Preemption enumerates subsets exactly, and refuses above twenty candidates

An earlier version stopped at the first subset *size* that produced any answer,
reasoning that a larger subset can only cost more. That is false the moment
costs differ per circuit: one wide expensive victim frees the same ports as two
narrow cheap ones, and the early exit picked the expensive one every time. Over
259 contended cases in the registry, greedy-by-cost-per-port is more expensive
than the exact answer in 52 of them.

Exact enumeration is `2**n`, so above `MAX_EXACT_CANDIDATES = 20` evictable
circuits on one trunk it raises rather than silently taking exponential time.

**Cost.** A hard ceiling. A trunk carrying more than twenty distinct evictable
circuits needs partitioning first, and the error message says so.

## D9 — The ingest budget belongs to the destination, not the local write

`ingest_budget_GBps` originally capped the local write, which meant it changed
`write-local`'s answer — a strategy that never touches a remote destination —
and had no effect at all on `stage-through-object`, the one strategy where an
object store's ingest rate is obviously the binding constraint.

It now caps the transfer across the stitch, alongside the circuit bandwidth, and
does not touch the local write.

## D10 — `MID_COLLECTIVE` is shown but never selected

Retuning inside a collective is legal only for an instantaneous change, which no
plant offers. `ladder()` still reports the row, because seeing what it would
cost is the point; `_selectable()` excludes it, so neither objective can pick it.

**Cost.** Two code paths where one would do. Worth it — deleting the row would
hide the reason the other four exist.

## D11 — All three port accessors are methods

`total_ports` was a property while `used_ports` and `free_ports` were methods
taking an optional trunk. The first caller outside the module formatted a bound
method into a report and Python did not complain. All three are now methods with
the same signature.

## D12 — A registry point that raises becomes a failing point

An exception inside a check used to abort the run and hide every later result,
so one broken check made a red registry look like a crash. `run_registry()`
wraps each point and converts a raised exception into a failing point carrying
the exception text.

