#!/usr/bin/env python3
"""Replay the fourteen recorded requests against a running TAPI server.

    python data/tapi/record_replies.py http://127.0.0.1:8182/restconf data/tapi/recorded

This is the only file in the repository that opens an HTTP connection, and it
is a recorder, not the adapter: it exists so the fixtures under ``recorded/``
can be regenerated against another server and the departure list re-measured.
It sends the same YANG-faithful body the adapter emits, with a fixed uuid so
the file names stay stable; tests/test_tapi.py checks that ``CS_BODY`` is what
the adapter compiles for the same intent, uuid aside.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

SERVICE_UUID = "9a1f6b2e-6c1a-5c0b-8e5d-3f1c2a7b9d10"
CS_BODY = {
    "tapi-connectivity:connectivity-service": [{
        "uuid": SERVICE_UUID,
        "name": [{"value-name": "SERVICE_NAME", "value": "stitch-ab-1"}],
        "administrative-state": "UNLOCKED",
        "service-layer": "PHOTONIC_MEDIA",
        "service-type": "POINT_TO_POINT_CONNECTIVITY",
        "requested-capacity": {"total-size": {"value": "50", "unit": "GHz"}},
        "connectivity-direction": "UNIDIRECTIONAL",
        "schedule": {"start-time": "20260903090000.0Z", "end-time": "20260903150000.0Z"},
        "end-point": [
            {"local-id": "csep-1", "name": [{"value-name": "CSEP_NAME", "value": "hall-a:ocs-1/4"}],
             "layer-protocol-name": "PHOTONIC_MEDIA",
             "layer-protocol-qualifier": "tapi-photonic-media:PHOTONIC_LAYER_QUALIFIER_NMC",
             "service-interface-point": {"service-interface-point-uuid": "node-1-port-13-input"},
             "direction": "INPUT", "role": "SYMMETRIC", "administrative-state": "UNLOCKED"},
            {"local-id": "csep-2", "name": [{"value-name": "CSEP_NAME", "value": "hall-b:ocs-3/9"}],
             "layer-protocol-name": "PHOTONIC_MEDIA",
             "layer-protocol-qualifier": "tapi-photonic-media:PHOTONIC_LAYER_QUALIFIER_NMC",
             "service-interface-point": {"service-interface-point-uuid": "node-2-port-14-output"},
             "direction": "OUTPUT", "role": "SYMMETRIC", "administrative-state": "UNLOCKED"},
        ],
    }]
}
CC = "/data/tapi-common:context/tapi-connectivity:connectivity-context"
CS = CC + "/connectivity-service=" + SERVICE_UUID
SEQUENCE = (
    ("01-get-context-fields-uuid", "GET", "/data/tapi-common:context/?fields=uuid", None),
    ("02-get-sip-node-1-port-13-input", "GET", "/data/tapi-common:context/service-interface-point=node-1-port-13-input/", None),
    ("03-get-sip-unknown", "GET", "/data/tapi-common:context/service-interface-point=no-such-sip/", None),
    ("04-get-connectivity-context-empty", "GET", CC + "/", None),
    ("05-post-connectivity-context-cs1", "POST", CC + "/", CS_BODY),
    ("06-get-connectivity-service-cs1", "GET", CS + "/", None),
    ("07-get-connection-cs1", "GET", CC + "/connection=" + SERVICE_UUID + "/", None),
    ("08-put-connectivity-service-cs1", "PUT", CS + "/", CS_BODY),
    ("09-post-connectivity-context-cs1-again", "POST", CC + "/", CS_BODY),
    ("10-delete-connectivity-service-cs1", "DELETE", CS + "/", None),
    ("11-get-connectivity-service-cs1-after-delete", "GET", CS + "/", None),
    ("12-delete-connectivity-service-unknown", "DELETE", CC + "/connectivity-service=no-such-cs/", None),
    ("13-post-connectivity-service-list-cs1", "POST", CC + "/connectivity-service/", CS_BODY),
    ("14-get-connectivity-context-after", "GET", CC + "/", None),
)


def call(base: str, method: str, path: str, body):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Accept", "application/yang-data+json, application/json")
    if data is not None:
        req.add_header("Content-Type", "application/yang-data+json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw, status, headers = resp.read(), resp.status, dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        raw, status, headers = exc.read(), exc.code, dict(exc.headers.items())
    try:
        decoded = json.loads(raw) if raw else None
    except ValueError:
        decoded = raw.decode(errors="replace")
    return {"method": method, "path": path, "request_body": body, "status": status,
            "headers": headers, "body": decoded}


def main(argv) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    base, out = argv[1].rstrip("/"), Path(argv[2])
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"files": {}}
    files = {}
    for name, method, path, body in SEQUENCE:
        rec = call(base, method, path, body)
        text = json.dumps(rec, indent=2, sort_keys=True) + "\n"
        fname = f"{name}.json"
        (out / fname).write_text(text)
        files[fname] = {"sha256": hashlib.sha256(text.encode()).hexdigest(), "bytes": len(text.encode())}
        print(f"{fname:48s} {method:6s} -> {rec['status']}  Location={rec['headers'].get('Location')}")
    manifest["files"] = files
    manifest["recorded_on"] = date.today().isoformat()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {len(files)} replies and {manifest_path}; now run experiments/tapi_departures.py "
          "and update TAPI_MOCK_DEPARTURES in validation/validate_intent.py if the list changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
