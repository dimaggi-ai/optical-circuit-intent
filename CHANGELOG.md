# Changelog

## Unreleased

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
