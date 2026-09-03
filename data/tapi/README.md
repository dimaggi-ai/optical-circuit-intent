# data/tapi

The pinned specification the TAPI binding (`src/ocintent/adapters/tapi.py`) is
checked against, and the replies of the one server it has been run through.
Nothing here was fetched at test time: every file is committed with its
SHA-256 in a `MANIFEST.json` beside it, and the registry refuses to read a
file whose digest has moved.

## `yang/` — TAPI 2.1.5, Apache-2.0

Three YANG modules and their pyang tree renderings from tag `v2.1.5` of
github.com/OpenNetworkingFoundation/TAPI (commit `dfe7a811`), with the
repository's `LICENSE`. Vendored rather than fetched because the licence
permits it and the schema check must not depend on the network
(DECISIONS.md D16). The HEDGE data under `../hedge` is fetched instead, for
the opposite reason: it has no licence.

The adapter reads these files for four things and nothing else: the direct
children of `connectivity-service` and of its `end-point` in the tree, the
enumerations (`capacity-unit`, `layer-protocol-name`, `port-direction`, ...),
the uuid pattern, which in this release sits in the typedef's description
rather than a `pattern` statement, and the `date-and-time` layout, which
sits in its typedef's description too (DECISIONS.md D19).

## `recorded/` — one mock, fourteen replies

Recorded on 2026-09-03 from the TeraFlowSDN hackfest TAPI server
(labs.etsi.org/rep/tfs/controller, commit `fb870787`, `hackfest/tapi/server`,
Apache-2.0), a swagger-codegen 3.0.35 server generated from the 2.1.3
OpenAPI, run locally with its shipped `mini-ols-context.json`. One request
per file, in file-name order; each file holds the method, path, request body,
status, headers and body exactly as sent and received. The recorder's
request body is the adapter's for the same intent (a test holds them equal),
so the schedule is in the typedef's `yyyyMMddhhmmss.sZ` layout and the
capacity value is the string RFC 7951 makes of a uint64; the mock, generated
from an OpenAPI stub that types the value `integer`, accepts the string and
echoes a number.

The mock is a schema fixture, not a conformance reference. It shows that the
adapter's paths and the YANG-faithful body parse on a 2.1.x server and are
echoed back whole; it also answers a create with 204 and no Location header,
answers reads and deletes of unknown uuids with 204, answers a PUT with the
generator's stub text, accepts the same uuid twice, and echoes the uint64
capacity value as a JSON number where RFC 7951 section 6.1, which the
agreement's section 2.6.1 mandates, writes a string.
`experiments/tapi_departures.py` prints that list call by call, and the
registry pins it. **No call in this directory was made against a real
controller.** A plant owner who has one re-records with `record_replies.py`
and expects the departure list to shrink.

## Re-recording

```
# the mock, as GitLab's subtree archive of the pinned commit and path; its
# SHA-256 is the archive_sha256 in recorded/MANIFEST.json:
#   https://labs.etsi.org/rep/tfs/controller/-/archive/fb8707871eba26806cac7ac373c70b2bb5bd26fc/controller-fb8707871eba26806cac7ac373c70b2bb5bd26fc.tar.gz?path=hackfest/tapi/server
# in a venv with connexion 2.14.2, Flask 2.2.5, Werkzeug 2.2.3, python-dateutil
# 2.6.0 and swagger-ui-bundle 0.0.9, from the archive's hackfest/tapi/server:
python3 -m tapi_server 8182 database/mini-ols-context.json
# then, from this repository:
python data/tapi/record_replies.py http://127.0.0.1:8182/restconf data/tapi/recorded
```

The recorder replays the same fourteen requests and rewrites `MANIFEST.json`
with fresh digests. Commit the new departure list with the new files.
