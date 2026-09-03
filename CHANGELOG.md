# Changelog

## 1.2.0 — 2026-09-03

The binding to a named plant.

- Added `ocintent.adapters.tapi`: compiles the generic plan to the RESTCONF
  calls of the ONF Transport API at its 2.1.5 data tree under the TR-547
  v1.2 reference implementation agreement (SOURCES.md S7, S8), and hands
  them back with the status, headers and fields each reply must carry.
  Nothing is sent. A request is two reads of the service interface points,
  one create with the Location header UC 1.0 requires, and two reads back; a
  failover adds one DELETE, last; a release is one DELETE; a hold extension
  is a PUT of the whole object, emitted only with the caller's copy of the
  service and refused, with no call, on a LOCKED one (DECISIONS.md D18). The plant owner's
  `hall:port` to SIP table is an input, an unknown endpoint is refused with
  no call, and what TAPI 2.1 cannot express — a reservation, a bandwidth
  floor at the photonic layer — is returned as unmapped with the reason.
- Vendored the three 2.1.5 YANG modules and their tree renderings
  (Apache-2.0) and recorded fourteen replies of the TeraFlowSDN hackfest
  mock, each directory with a manifest of digests the registry refuses to
  read past (D16). The first profile is 2.1.5 with TR-547 v1.2, not 2.6.0,
  whose release notes disclose an open YANG defect (D17).
- Added seventeen registry points: ten calibrated against the vendored tree
  and the agreement's text (Table 5 methods, the module-qualified root key,
  the children of `connectivity-service`, the client-mandatory attributes
  of Tables 23 and 24, the Location header, the lowercase uuid, GHz at the
  photonic layer, the schedule in the `date-and-time` typedef's own layout,
  the 204 on delete, the PUT on extension), four emergent
  (one destructive call per failover and last, refusal with no call,
  deterministic bytes, no capacity container without a slot width), three
  sanity on the recordings (manifests, the pinned departure list, the
  read-back echo). Six declined items cover the binding: the first says no
  controller was called, the others what TAPI 2.1 cannot express, what this
  release does not profile, and that the agreement calls its own status
  codes experimental.
- Added `experiments/tapi_departures.py`, which prints, reply by reply,
  where the mock departs from TR-547 (ten departures in seven replies:
  status codes, no Location header, a duplicate uuid accepted, the uint64
  capacity value echoed as a JSON number), and the CLI
  subcommand `ocintent tapi INTENT --sip-table TABLE [--layer] [--slot-width-ghz]
  [--restconf-root] [--now] [--current-service] [--boundary] [--json]`
  (exit 1 when the plan is refused, 2 when an input cannot be read).
- Added thirteen mutation tests with measured red sets. One exists because
  it first reddened nothing: a create that stopped expecting the Location
  header was invisible until the calibrated point for it was written. One
  takes the vendored YANG directory or the SIP table away and requires every
  point that reads it to fail in the kind it declares, the kind counts
  unmoved.
- Schedule times are written in the layout tapi-common's own `date-and-time`
  typedef describes (`yyyyMMddhhmmss.sZ`), not RFC 3339: the typedef is a
  bare string, the module imports nothing, and the agreement names no layout
  for the schedule (D19). The capacity value is the JSON string RFC 7951
  section 6.1 makes of a uint64 and Table 23 writes (D20); the mock echoes
  it as a number, which is the tenth departure on the printed list. The
  mock's replies were re-recorded with both changes, and a test holds the
  recorder's body equal to the adapter's.
- A refused plan carries no call: the first cut handed the GET back beside
  the refusal of a LOCKED service or of someone else's object.
- A registry point that raises fails in the kind it declares (D12); the
  first cut reported it as sanity, so a lost YANG file moved the kind counts.
- The PUT of a hold also strips the read-only leaves under
  `latency-characteristic`; a test derives the read-only set from the
  vendored tree.
- `ocintent tapi` exits 2 on a `--now` that is not finite or is before the
  epoch and on a SIP table that is not an object, and names a SIP whose uuid
  or qualifier is missing; each used to be a traceback.
- The README's checkpoint and ledger blocks are the experiments' full output
  again, byte for byte.
- `MANIFEST.in` keeps `tests/` out of the sdist; they read the registry and
  data the sdist never carried.
- Fixed the README's kind counts for 1.1.0, which read sixteen emergent and
  fourteen sanity where the registry printed seventeen and fifteen.
- Counts: 276 tests (41 mutation cases), 57 registry points (18 calibrated,
  21 emergent, 18 sanity), 19 declined, 29 examples, 5 experiments.

## 1.1.0 — 2026-09-03

The one measured link.

- Added `ocintent.hedge`: reads the raw files of the HEDGE testbed
  (Devraj et al., NSDI '26; SOURCES.md S6) — transponder BER, uncorrectable-FEC
  counter and received power for every wavelength, and the four iperf server
  logs — and produces, per run, each wavelength's BER onset, ramp, FEC cliff,
  failure and re-stabilisation markers, the Layer-3 dark intervals, and the
  alarm-to-outage leads. The markers and analysis windows are the authors'
  own (DECISIONS.md D14).
- Found, while reading the iperf server logs, that each line's stamp is a
  stdout flush that trails its interval by up to five seconds, so every
  interval is re-timed from iperf's own interval field, anchored per log at
  its least-late line; the four logs' anchors agree within 5 ms in every
  run, and `Run.retiming` carries what was measured (D14). Where a check
  compares the outage with a transponder sample it is read to the
  transponder's resolution, one poll gap of about 1.6 s.
- Added `data/hedge/fetch_hedge.py` and `make data`: fifteen files fetched
  from the pinned commit and refused unless their SHA-256 matches the
  manifest in `ocintent.hedge`. Fetched, not vendored: the upstream
  repository carries no license (D13). `make test`, `make validate`,
  `make examples` and `make experiments` depend on it, and CI fetches first.
- Added fifteen registry points that read the measured link: six calibrated
  against what the paper says about its own runs (the 450/500/550 s format
  failures, traffic continuing until PM-QPSK fails, the format ordering in
  failure time and loss, red and blue failing before green with the LAG
  holding until green, every labelled bend failure re-stabilising, only
  16-QAM failing under the format bend); six emergent (the first FEC alarm
  led the outage by 88.3 s and 39.0 s, the outage began within one poll gap
  of the last failure and long after the first, BER rose across more than
  one poll before every FEC change, the window-start onset marker orders the
  formats 16-QAM first with the alternative marker printed beside it, every
  cliff sat decades above the forecast's default target, every failing
  wavelength was closer to the 20 dB branch than the 10 and steeper than
  both); three sanity (all fifteen pins match; the four servers' re-timing
  anchors agree within one report interval; the event-driven Layer-3 join
  agrees with the authors' index-aligned reduction within two report
  intervals). A missing or altered file turns every one of them red in its
  own kind, and so does a check that raises; none skips.
- Changed `ocintent.drift`: `q_from_margin_db`, `ber_from_margin_db`,
  `forecast` and the new `margin_span_db` take `detection="direct"|"coherent"`
  (`DB_PER_DECADE_OF_Q`: 10 and 20 dB per decade of Q). The default stays
  direct because the published anchors are written against it; on all nine
  failing wavelengths the measured link was closer to coherent than direct,
  and steeper than both (D15). Added
  `q_from_ber` (the inverse of `ber_from_q`, by bisection) and
  `DEFAULT_TARGET_BER`.
- Added the CLI subcommand `ocintent hedge [--data DIR] [--run RUN] [--json]`
  (exit 2 when the files are missing or altered) and `--detection` on
  `ocintent drift`.
- Added `experiments/measured_lead_time.py`, the fourth figure the README
  quotes, which also prints what lies outside the authors' analysis windows,
  and ten mutation tests with measured red sets, two of which are asserted
  blind spots: the Layer-3 join rule cannot be adjudicated by this data, and
  a deleted integrity check is invisible while the files are intact. A third
  deletes the re-timing and places each interval at its stamp, and exactly
  the three outage-resolution points go red.
- Rewrote the declined list: one measured plant and it is a laboratory; the
  measured outcome is availability, not capacity, on undocumented clocks;
  nothing measured transfers as a constant. Eleven items became thirteen.
- Counts: 174 tests (27 mutation), 40 registry points (8 calibrated,
  17 emergent, 15 sanity), 13 declined, 21 examples, 4 experiments.

## 1.0.1 — 2026-09-02

- Fixed: the validation registry mangled the name of any point that raised,
  because it stripped `point_` everywhere in the function name rather than as a
  prefix. `point_crossing_checkpoints_never_widens_a_quiet_window` was reported
  as `crossing-checks-never-widens-a-quiet-window`, and
  `point_the_intervals_are_pointwise_correct` as
  `the-intervals-are-wise-correct`. Only the failure path was affected, so a
  green run never showed it. Found while building `slice-packer-torus`, which
  had inherited the same line.

## 1.0.0 — 2026-09-01

First release.

- `intent` — verbs, endpoints, boundaries, and a compiler to vendor-neutral
  operations. Failover verifies the replacement before teardown.
- `legality` — the retune ladder, two objectives, and an exact piecewise
  decomposition of the retune times where they disagree.
- `radix` — trunk-level port allocation, stranded-port accounting, and exact
  preemption planning.
- `checkpoint` — stop tax versus contention tax across four strategies, and a
  stall classifier that returns AMBIGUOUS rather than guessing.
- `drift` — declared-versus-measured comparison and an insertion-loss forecast
  built on the published error-rate relation.
- `ledger` — capacity debt, aged and totalled in the shape of an
  accounts-receivable schedule.
- 137 tests including 17 mutation tests; 25 validation points across three
  kinds, with 11 declared limitations printed on every run; 17 examples; three
  reproducible experiments.
