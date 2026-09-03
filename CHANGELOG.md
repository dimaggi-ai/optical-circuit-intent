# Changelog

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
