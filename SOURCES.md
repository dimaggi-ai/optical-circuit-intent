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

## S6 — The HEDGE hardware experiments

Devraj et al., *HEDGE*, 23rd USENIX Symposium on Networked Systems Design
and Implementation (NSDI '26). Raw files and analysis notebooks at
https://github.com/hedge-wan/hedge, commit
`9c6540cf042a4933e918f9b306fcf116d8776e2f`, under
`hardware-experiments/`. The repository carries no license file; the files
are fetched and SHA-pinned by `make data`, never redistributed (DECISIONS.md
D13).

What the files are, since it matters for reading them: the transponder log
is polled about every 1.6 s; each iperf server-log line carries a
millisecond epoch stamp added when iperf's buffered output was flushed, not
when the interval ended, so the stamps trail their intervals by up to five
seconds and every interval is re-timed from iperf's own interval field
(DECISIONS.md D14). The paper reports a bit error rate under a 25% FEC
overhead; that the transponders are coherent and the rate pre-FEC is this
repository's reading (ASSUMPTIONS.md A12).

What is taken from the paper and its notebooks:

- The failure and re-stabilisation markers (figure 4 caption; the notebooks'
  `first_fec_change` and `first_fec_change_stop`), the per-run analysis
  windows (each notebook's plot limits, with comments on what lies outside),
  and the channel-to-format and channel-to-wavelength maps.
- Section 3.1, Finding 1 (figure 4a/4b): the blue and red wavelengths fail
  before the green one under the bend, the LAG stays up until green fails,
  and all three recover when the bend is released.
- Section 3.1, Finding 2 (figure 4c): under the same bend across formats,
  16-QAM is the first to show a BER rise and fails while the lower formats
  hold.
- Appendix A.5 (figure 14b/14c): under the attenuator the higher the format
  the earlier the BER spikes and the earlier it fails; 16-QAM and 8-QAM stop
  at about 450 and 500 s and UDP traffic continues until PM-QPSK fails at
  about 550 s.

Used by: the six `hedge-*` calibrated points in
`validation/validate_intent.py`; `ocintent.hedge.LABELS`, `WINDOWS`,
`first_counter_change`, `counter_restabilised`.
