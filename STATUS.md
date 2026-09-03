# Status

**Version 1.1.0, 2026-09-03.** Complete and reproducible as a model, and read
against one measured link: a laboratory testbed, not a plant.

## What is done

| | |
| --- | --- |
| Tests | 174 pass, including 27 mutation tests |
| Validation registry | 40 points: 8 calibrated, 17 emergent, 15 sanity |
| Declared limitations | 13, printed before the results on every run |
| Examples | 21, each asserting its documented exit code |
| Experiments | 4, each failing loudly if its headline stops holding |
| Measured data | 15 HEDGE testbed files, fetched by `make data` and SHA-pinned |
| Dependencies | none outside the standard library |

## What would change the findings

**A production plant.** Item 1 of the declined list. One laboratory link has
been measured (SOURCES.md S6), and it moved one thing: on every wavelength
that failed, the measured drop was closer to the 20 dB per decade branch
than to the 10 dB default, and steeper than both, so the model now carries
both branches and says which it used (DECISIONS.md D15). What the
laboratory could not give, and a plant could:

- Round-trip time from a real stitched path, over enough time to see a trend.
  The testbed measured error rate, FEC and power; it took no delay
  measurement, so the thermal-coefficient blind spot the mutation tests
  assert is still open.
- Reconfiguration timing distributions from a real controller. Finding 1's
  scope — that the objectives only disagree for slow reconfigurations — rests
  on a published range, not on a measurement.
- A collective's completion time with and without a concurrent replication.
  This is the single measurement that would validate or destroy the
  contention model in `checkpoint`, which is the weakest link in finding 2.
- The same alarm-to-outage lead on a production aggregated link, with
  synchronised clocks. Finding 4's 88 s and 39 s are one testbed's numbers;
  whether the shape survives a real plant is exactly what is not known, and
  outside the authors' windows the same files already hold one disturbance
  in which the link went dark more than a poll before the last alarm sample.

**A second calibration anchor that is not the erfc relation.** Both error-rate
anchors pin one relation. The six measured anchors pin the HEDGE paper's
reading of its own raw files, which makes the parsing trustworthy and is not
an anchor on the physics. An independent one — a measured link budget for a
named receiver, a published latency figure for a named path — would be worth
more than five more points on the same curve.

## What is deliberately absent

- No vendor drivers, no controller sessions (DECISIONS D2).
- No currency conversion in the ledger (D6).
- No default between the two retune objectives (D3).
- No expected-cost netting of durability risk (D5).
- No wavelength-level grooming in the radix model (ASSUMPTIONS A7).
- No vendored copy of the measured data, and no skip when it is absent (D13).

## Where it sits in the series

`span-contract` decides whether a job may cross a hall boundary at all. This
repository is about the circuit underneath that decision: what to ask for, when
it is legal to ask, and what the answer costs. The two share a unit —
accelerator-hours — so their numbers add.

Part of the [DIMAGGI usable-capacity series](https://dimaggi-ai.github.io/research).
