#!/usr/bin/env python3
"""Where the recorded TAPI mock departs from TR-547 v1.2, call by call.

The replies under ``data/tapi/recorded`` came from the TeraFlowSDN hackfest
mock (SOURCES.md S9), a swagger-codegen server generated from the 2.1.3
OpenAPI. It is evidence that the adapter's paths and bodies parse on a
2.1.x server; it is not a conformant controller, and this table is the list
of places where its answers are not what the agreement says a controller
returns. The registry pins the same list (``TAPI_MOCK_DEPARTURES``), so a
re-recording that changes it fails there until the list is updated in the
same commit.

Every number in the README's TAPI section is printed here.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from ocintent.adapters import tapi  # noqa: E402

RECORDED = os.path.join(ROOT, "data", "tapi", "recorded")
SIP_TABLE = os.path.join(ROOT, "examples", "tapi-sip-table.json")


def main() -> int:
    with open(os.path.join(RECORDED, "MANIFEST.json")) as fh:
        manifest = json.load(fh)
    replies = []
    for name, entry in sorted(manifest["files"].items()):
        path = os.path.join(RECORDED, name)
        with open(path, "rb") as fh:
            raw = fh.read()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != entry["sha256"]:
            print(f"{name}: sha256 {digest} does not match the manifest; refusing to read it")
            return 2
        replies.append((name, json.loads(raw)))
    table = tapi.SipTable.from_json(SIP_TABLE)
    checks = tapi.check_recorded(replies, {s.uuid for s in table.entries.values()})

    print(f"mock: {manifest['mock']['repository']} @ {manifest['mock']['commit'][:12]} "
          f"({manifest['mock']['generator']}), recorded {manifest['recorded_on']}")
    print(f"agreement: TR-547 v{tapi.TR547_VERSION}; YANG {tapi.TAPI_YANG_TAG}")
    print()
    print(f"  {'recorded reply':<48} {'method':<7} {'got':>4} {'TR-547 says':<12} departure")
    departures = []
    on_table = 0
    for c in checks:
        if not c.on_table:
            print(f"  {c.file:<48} {c.method:<7} {c.status:>4} {'-':<12} off the adapter's table; not judged")
            continue
        on_table += 1
        exp = "/".join(str(s) for s in c.expected)
        if c.departures:
            for d in c.departures:
                print(f"  {c.file:<48} {c.method:<7} {c.status:>4} {exp:<12} {d.kind}: {d.detail}")
                departures.append(d)
        else:
            print(f"  {c.file:<48} {c.method:<7} {c.status:>4} {exp:<12} matches")
    print()
    kinds = {}
    for d in departures:
        kinds[d.kind] = kinds.get(d.kind, 0) + 1
    print(f"recorded calls: {len(checks)}; on the adapter's table: {on_table}; "
          f"matching TR-547: {on_table - len({d.file for d in departures})}; "
          f"replies with a departure: {len({d.file for d in departures})}")
    print(f"departures: {len(departures)} "
          + ", ".join(f"{k} x{v}" for k, v in sorted(kinds.items())))
    print()
    print("what the mock does that a TR-547 controller must not:")
    print("  - answers a create with 204 and no Location header (UC 1.0: Location MUST; RFC 8040: 201)")
    print("  - answers a read or delete of an unknown uuid with 204 (UC 10 and RFC 8040 4.3: 404 invalid-value)")
    print("  - answers a PUT with 200 and the generator's stub text (UC 11a figure 6-43: 204)")
    print("  - accepts a second create with the same uuid (RFC 8040 4.4.1: 409)")
    print("  - echoes the uint64 capacity value as a JSON number (RFC 7951 6.1, which 2.6.1 mandates: a string)")
    print("so a green registry says the bodies parse on a 2.1.x server, not that a controller accepts them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
