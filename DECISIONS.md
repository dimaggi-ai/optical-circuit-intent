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

## D13 — The measured link's files are fetched and pinned, never vendored, and their absence is red, not skipped

The HEDGE repository publishes its raw hardware-experiment files with no
license grant. Redistributing them is not this repository's call to make, so
`make data` fetches the fifteen files from one pinned commit and refuses any
whose SHA-256 does not match the manifest in `ocintent.hedge`. The same
posture `slice-packer-torus` takes with the Titan lifetime data.

The consequence that matters: a registry point that cannot find its data
**fails**, in its own kind, with the reason in its detail. It does not skip.
A registry that quietly dropped its thirteen measured points when the
directory was empty would print a green summary that meant less than it
appeared to, and nothing in the counts would say so. The unit tests follow
the same rule; `make test` depends on `make data`.

**Cost.** A network fetch before the first test run, and a test suite that
cannot run offline until it has happened once.

## D14 — The authors' markers and windows are adopted; the iperf logs are re-timed from iperf's own interval field; the Layer-3 join is event-driven and cross-checked against theirs

Every marker the measured points use is the one the HEDGE notebooks use:
failure is the first sample whose uncorrectable-FEC counter exceeds its
initial value; re-stabilisation is the first index followed by five equal
counter readings; the analysis window of each run is the notebook's plot
limit, and the notebook comments say what lies outside it (an unrelated
attenuator experiment after the bend runs; the prototype notebook calls
250–650 s "the relevant time period with VOA tuning", so the attenuator
sweep begins inside that window, not before it). None of these was chosen
here, and none was chosen after seeing the data. The BER onset marker, which
the paper does not define, is parameter-free — the last pre-failure sample
at or below the window-start BER — and it is this repository's choice: the
obvious alternative, the first sample strictly above the start, reads the
poll after the first on a noisy error rate, and the registry prints what
both markers give rather than asserting the alternative.

**The iperf stamps are flush times, not interval times.** Each line of a
server log carries a millisecond epoch stamp, and the first reading of these
files took it as the time of the interval on that line. It is not. iperf
writes to a block-buffered stdout; the stamps arrive in bursts, three
quarters or more of the interval lines were stamped more than a second after
their interval, and a stamp trails the interval it describes by up to five
seconds. Every interval is therefore re-timed from iperf's own
`a.aa-b.bb sec` field, which runs on iperf's clock, anchored to the run's
clock per log at the least-late line (the line whose stamp is closest to its
own interval end). The four server logs, re-timed independently, anchor
within 5 ms of one another in every run, which is what four processes
reporting one client's streams should do and what noise would not produce;
a sanity point checks that spread against a bound of one report interval,
fixed before the comparison ran. The `Accepted connection` header's stamp
is not used as a zero, because it is itself a flushed line and trails the
anchor in every log. Under the stamps the outage appeared whole seconds
after the last wavelength's alarm sample; re-timed, it begins 1.2 s and
0.1 s before that sample, inside the 1.6 s poll interval in which the
wavelength failed. The link is called down from the start of the first
interval that leaves every server at zero, and up from the start of the
first interval on any server that carried traffic again, so both edges are
early by less than one interval and never late.

The Layer-3 reduction differs from the notebooks in one respect. They align
the four server logs by report index and sum the i-th intervals; this
repository joins the four logs on their own re-timed intervals and calls the
link down from the interval that leaves every server's latest report at
zero. Every log is a contiguous run of one-second intervals, so once the
stamps are re-timed the i-th reports coincide to within the anchors' spread
and the two reductions agree to 0.0 s at every transition; a sanity point
checks that against a bound of two report intervals, one for a possible
index misalignment and one of interval jitter, fixed before the comparison
ran. Before re-timing, the raw stamps' i-th spread reached several seconds
in the bend runs, which the first reading took for servers skipping
intervals; no server skips one.

Where a check compares the outage with a transponder sample, it is read to
the transponder's own resolution: the outage must begin within one poll gap
(about 1.6 s) of the failure sample, on either side, because the failure
itself happened somewhere inside that gap. The bound is the data's
resolution, not a tolerance chosen after seeing the number.

The join's rule ("every probe quiet", not "any probe quiet") is a decision.
A mutation test asserts, measured, that this data cannot tell the two rules
apart, because the four probes ride one link and go quiet together. A second
mutation deletes the re-timing and places each interval at its stamp:
exactly the three points that read the outage to the transponder's
resolution go red.

**Cost.** One more reduction to maintain, a re-timing step that any reader of
the raw files has to know about, and a blind spot that has to be printed
rather than closed.

## D15 — The drift model gained a `detection` argument because the measured link disagreed with its default on every wavelength; the default stays direct

`q_from_margin_db` shipped with Q scaling at 10 dB per decade, the
thermal-noise-limited direct-detection relation, anchored at Q of six. On
all nine wavelengths that failed on the measured link, the received-power
drop between BER onset and the FEC cliff was closer to the 20 dB per decade
relation of an OSNR-limited link (in which Q squared follows the
signal-to-noise ratio; the code calls it `coherent`) than to the direct one,
and steeper than both: the implied slopes run from 21.7 to 36.6 dB per
decade, and the experiment prints each. "Closer" is the whole claim; the
measurement matches neither branch. A model that was measured and stayed silent
about the result would be a worse model, so `q_from_margin_db`,
`ber_from_margin_db`, `forecast` and `margin_span_db` now take
`detection="direct"` or `"coherent"`, and `forecast` also documents that a
coherent transponder counts down to its FEC limit, near 3e-2 on that link,
rather than to the 1e-12 default.

The default stays `direct`. The two error-rate anchors (SOURCES.md S1) are
written against it, every existing example and figure was produced with it,
and changing a default on the strength of one laboratory link would be the
kind of quiet retune the honesty rules exist to prevent. The registry prints
the disagreement on every run instead, and the CLI takes `--detection`.

**Cost.** A caller who models a coherent link and forgets the argument gets
the direct-detection shape, which is steeper. The docstrings and the
`ocintent hedge` output both say so.
