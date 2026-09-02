# Sources

The published figures this repository is anchored to. Only S1 is used by a
calibrated validation point; the rest are inputs, and the difference matters —
an input can be wrong without any point turning red.

## S1 — The error-rate relation, `BER = 0.5 · erfc(Q / √2)`

The standard relation between the electrical Q factor and the bit error rate of
a threshold-detected binary optical signal, found in any optical communications
text (Agrawal, *Fiber-Optic Communication Systems*, is the usual reference) and
in the receiver specifications written against it.

Two values are quoted often enough to serve as anchors:

| Q | BER |
| --- | --- |
| 6 | ≈ 1 × 10⁻⁹ |
| 7 | ≈ 1.28 × 10⁻¹² |

Both calibrated points check these. **They pin one relation at two places** and
are not independent anchors — a systematic error in the relation would leave
both green. That limitation is item 2 of the registry's declined list.

Used by: `ocintent.drift.ber_from_q`.

## S2 — Thermal delay coefficient of standard single-mode fibre, ≈ 40 ps/km/K

Propagation delay through silica fibre moves with temperature through both the
refractive index and the physical length. Published figures for standard
single-mode fibre cluster in the range **37–40 ps/km/K**; 40 is used here.

This is an **input, not a calibration.** The only registry point that touches it
checks that the model is linear in distance and temperature, which it remains at
any coefficient — so the registry cannot tell a correct value from a wrong one.
`tests/test_mutations.py::test_the_registry_cannot_see_a_wrong_thermal_coefficient`
asserts that blind spot rather than hiding it.

Used by: `ocintent.drift.THERMAL_DELAY_PS_PER_KM_K`.

## S3 — MEMS optical circuit switch reconfiguration, tens of milliseconds

Published descriptions of MEMS-based optical circuit switching in production AI
and datacentre fabrics report reconfiguration times in the range of roughly
10–25 ms. The registry uses 25 ms — the slow end — as the figure a retune has to
beat to be irrelevant to the legality ladder.

This is what scopes finding 1: at OCS speed the two objectives agree at every
rhythm tested, so the disagreement belongs to slower operations — ROADM
provisioning, metro turn-up, a hand patch.

Used by:
`validation/validate_intent.py::point_published_ocs_switching_is_below_every_disagreement_band`.

## S4 — Fibre attenuation at 1550 nm, ≈ 0.20 dB/km

The conventional figure for G.652 single-mode fibre in the C band.

Used by: `ocintent.drift.FIBRE_LOSS_DB_PER_KM`.

## S5 — The scale-across specification

The internal write-up this repository implements: the span envelope, the
boundary taxonomy, the intent verbs, and the framing of drift as debt. Section
references in docstrings (W4, W5, W9) point at it.

