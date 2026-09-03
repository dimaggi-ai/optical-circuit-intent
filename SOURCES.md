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

## S7 — The TAPI 2.1.5 YANG modules

Open Networking Foundation (now Open Network Models and Interfaces, ONMI),
*Transport API (TAPI)*, tag `v2.1.5` (2026-02-17), commit
`dfe7a811e624b03b234e836f5a213d14bb600c69`, Apache-2.0. Repository
https://github.com/Open-Network-Models-and-Interfaces-ONMI/TAPI (the former
`OpenNetworkingFoundation/TAPI` address redirects). Three modules —
`tapi-common`, `tapi-connectivity`, `tapi-photonic-media` — and their pyang
tree renderings are vendored under `data/tapi/yang` with the repository's
LICENSE and a manifest of digests (DECISIONS.md D16).

What is taken from them, and nothing else: the direct children of
`connectivity-service` and of its `end-point` in the tree; the enumerations
`capacity-unit`, `layer-protocol-name`, `administrative-state`,
`operational-state`, `lifecycle-state`, `forwarding-direction`,
`port-direction` and `port-role`; the uuid typedef, whose canonical
lowercase form sits in the typedef's description in this release rather
than in a `pattern` statement; the `date-and-time` typedef, a bare string
whose description gives the layout `yyyyMMddhhmmss.s[Z|{+|-}HHMm]` (the
module imports nothing, so RFC 6991's type is not in the tree; DECISIONS.md
D19); and `capacity-value`, a uint64 (D20).

Used by: the `tapi-*` calibrated points on the tree, the enumerations, the
uuid and the schedule layout; `ocintent.adapters.tapi.tree_children`,
`yang_enum`, `yang_uuid_pattern`, `yang_block`.

## S8 — TR-547 v1.2, the TAPI v2.1.5 reference implementation agreement

ONF TR-547, *TAPI V2.1.5 Reference Implementation Agreement*, version 1.2,
from the `v2.1.5` tag of the TAPI documentation repository
(`ReferenceImplementationAgreements/TR-547`), PDF SHA-256
`0b9691162f349f956b271dbf8730b715ec556764c7745680e8cb4ee82df9b210`
(`ocintent.adapters.tapi.TR547_SHA256`). The agreement is not vendored; the
digest is, so a reader can check they hold the same document.

What is taken from it, with the page of the rendered PDF each was read on:

- Table 5 (pp. 42–45): the RESTCONF operations the agreement leaves
  standing on each resource — GET, PUT and PATCH on a service interface
  point, POST on the connectivity context, GET, PUT and DELETE on a
  connectivity service, GET on a connection — with the struck-through
  operations read as not allowed, and the note that no use case modifies a
  service interface point.
- Section 2.6.1 (p. 21): the JSON encoding, which MUST follow RFC 7951, so
  the root key of a body is module-qualified
  (`tapi-connectivity:connectivity-service`), the children are not (RFC 7951
  section 4), and a uint64 is a JSON string (section 6.1).
- UC 1.0 (pp. 114–115): the unconstrained point-to-point service, whose
  create is a POST on the connectivity context answered with 200 or 201, and
  for which, if the operation is successful, "the NBI server MUST return an
  http response message with the Location Header as specified in [W3
  HEADER]" (p. 115); figure 6-10, in which every GET is answered 200.
- Tables 23 and 24 (pp. 115–118): the attributes of a connectivity service
  the client must send, marked RW and M — uuid, name with SERVICE_NAME,
  service layer, end points — and those the adapter sends beside them that
  the table marks optional or conditional: administrative state, service
  type and direction (O), requested capacity (C; a slot width in GHz at the
  photonic layer, its value written `"[0-9]{8}"`, quoted, on p. 116); and
  of its end points the RW and M local id, name with CSEP_NAME, layer,
  qualifier and service interface point, beside direction, role and
  administrative state (O).
- p. 188, the CREATION_TIME row of the node table: the one timestamp layout
  the text names, an IETF date-and-time for a name value; the text names no
  layout for a schedule, which is why the schedule follows the typedef's
  own description (S7; DECISIONS.md D19).
- UC 10 (pp. 220–221): deletion of a connectivity service by uuid, "a 204
  No Content status-line is returned" on success, 404 for an unknown uuid.
- UC 11a (pp. 222–223), marked draft: modification of a connectivity
  service by a PUT of the whole object, answered 204 (figure 6-43, p. 223),
  on an UNLOCKED service.
- Section 5.2: the agreement's own statement that its status codes are
  experimental, which is why a controller that differs is a departure to
  print rather than a failure to hide.

Used by: the `tapi-*` calibrated points on Table 5, Tables 23 and 24, the
root key, the Location header, the capacity value, the delete and the hold
extension;
`ocintent.adapters.tapi.expected_reply`, `EXPECT_*`.

## S9 — The one server the calls have been run through

ETSI TeraFlowSDN controller, https://labs.etsi.org/rep/tfs/controller,
commit `fb8707871eba26806cac7ac373c70b2bb5bd26fc`, `hackfest/tapi/server`,
Apache-2.0: a swagger-codegen 3.0.35 server generated from the TAPI 2.1.3
OpenAPI (`packageVersion` 2.1.3), run locally with its shipped
`mini-ols-context.json`. Fourteen replies recorded on 2026-09-03 are
committed under `data/tapi/recorded` with a manifest of digests
(DECISIONS.md D16, `data/tapi/README.md`). The mock was taken as GitLab's
subtree archive of that commit and path; the archive's SHA-256 is in the
recordings' manifest and reproduced on a second download on 2026-09-03. It
ran under connexion 2.14.2, Flask 2.2.5 and Werkzeug 2.2.3 on Python
3.12.6.

A mock, not a conformance reference. It shows that the adapter's paths and
bodies parse on a 2.1.x server and are echoed back whole; it also answers a
create with 204 and no Location header, answers reads and deletes of
unknown uuids with 204, answers a PUT with the generator's stub text,
accepts the same uuid twice, and echoes the uint64 capacity value as a JSON
number where RFC 7951 writes a string. `experiments/tapi_departures.py` prints that
list and the registry pins it. No point is calibrated against this source;
the three that read it are sanity.

Used by: the three `tapi-*` sanity points on the manifests, the departures
and the read-back echo; `ocintent.adapters.tapi.check_recorded`.
