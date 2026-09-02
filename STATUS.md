# Status

**Version 1.0.0, 2026-09-01.** Complete and reproducible as a model. Not
validated against any plant.

## What is done

| | |
| --- | --- |
| Tests | 137 pass, including 17 mutation tests |
| Validation registry | 25 points: 2 calibrated, 11 emergent, 12 sanity |
| Declared limitations | 11, printed before the results on every run |
| Examples | 17, each asserting its documented exit code |
| Experiments | 3, each failing loudly if its headline stops holding |
| Dependencies | none outside the standard library |

## What would change the findings

**A measured plant.** Item 1 of the declined list and the one that matters. Every
number here is a model output. The specific measurements that would move things:

- Round-trip, insertion loss and error rate from a real stitched path, over
  enough time to see a trend. This is what turns the drift forecast from a shape
  into a prediction, and it would close the thermal-coefficient blind spot the
  mutation tests currently assert.
- Reconfiguration timing distributions from a real controller. Finding 1's scope
  — that the objectives only disagree for slow reconfigurations — rests on a
  published range, not on a measurement.
- A collective's completion time with and without a concurrent replication.
  This is the single measurement that would validate or destroy the contention
  model in `checkpoint`, which is the weakest link in finding 2.

**A second calibration anchor that is not the erfc relation.** Both calibrated
points pin one relation. An independent anchor — a measured link budget, a
published latency figure for a named path — would be worth more than five more
points on the same curve.

## What is deliberately absent

- No vendor drivers, no controller sessions (DECISIONS D2).
- No currency conversion in the ledger (D6).
- No default between the two retune objectives (D3).
- No expected-cost netting of durability risk (D5).
- No wavelength-level grooming in the radix model (ASSUMPTIONS A7).

## Where it sits in the series

`span-contract` decides whether a job may cross a hall boundary at all. This
repository is about the circuit underneath that decision: what to ask for, when
it is legal to ask, and what the answer costs. The two share a unit —
accelerator-hours — so their numbers add.

Part of the [DIMAGGI usable-capacity series](https://dimaggi-ai.github.io/research).
