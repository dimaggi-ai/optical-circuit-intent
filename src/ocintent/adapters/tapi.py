"""The TAPI binding: a generic plan compiled to RESTCONF calls, never made.

Section 9 of the specification asks, in items 8 and 9, for a named plant with
a documented circuit API and an adapter that speaks it. The plant chosen is
"a TR-547 v1.2 conformant TAPI 2.1.x controller": the Transport API's RESTCONF
data tree, pinned to the 2.1.5 YANG modules and the v1.2 reference
implementation agreement (SOURCES.md S7 and S8, DECISIONS.md D17). That line
is the one the TAPI project itself calls the most deployed, and the only one
with public fixtures that need no live plant.

What this module does: take the plan :func:`ocintent.intent.compile_intent`
produced --- generic operations in a fixed order --- and emit, for each
operation, the HTTP call a TR-547 controller documents for it: method, path,
JSON body, the status the agreement says to expect, and which table or use
case says so. The calls come back in a :class:`TapiPlan`. They are not made.
There is no HTTP client in this file, and DECISIONS.md D2 is why.

What it refuses to do, and says so rather than guessing:

- An endpoint with no entry in the :class:`SipTable` gets a refusal, not an
  invented service interface point. The table is the plant owner's, committed
  beside the adapter; the one shipped in ``examples/`` is the TeraFlowSDN
  mock's context, which stands in for a plant (ASSUMPTIONS.md A13).
- ``reserve_ports`` and ``release_ports`` have no TAPI equivalent. The 2.1
  data tree exposes no reservation primitive --- TR-547's API table lists the
  service interface point as readable and notes that no use case modifies it
  --- so the reserve step compiles to two *reads* that check the points are
  unlocked and enabled, and the release step is listed as unmapped.
- ``extend_hold`` is a PUT of the whole service object (TR-547 UC 11a, which
  its own text marks draft). The intent does not carry that object, so without
  it the adapter emits the read and lists the write as unmapped, with the
  exact end time it would set; given the current object it emits the PUT, and
  refuses if the object is administratively locked, which UC 11a makes a
  precondition.
- A bandwidth expectation at the photonic layer. TAPI's requested capacity
  at PHOTONIC_MEDIA is spectrum in GHz, not a bit rate, and turning one into
  the other needs a modulation model this repository does not have.

Every number and string the calls carry comes from one of three places: the
intent, the SIP table, or a constant in this file that cites its source.
"""

from __future__ import annotations

import json
import math
import re
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..intent import Endpoint, Intent, Operation, Plan, Verb, compile_intent

# --- the pinned specification -------------------------------------------------

#: The YANG modules the body shape is checked against. Tag ``v2.1.5`` of the
#: LF ONMI TAPI repository, Apache-2.0, vendored under ``data/tapi/``.
TAPI_YANG_TAG = "v2.1.5"
TAPI_YANG_COMMIT = "dfe7a811e624b03b234e836f5a213d14bb600c69"
#: The reference implementation agreement the paths, methods and status
#: expectations are transcribed from.
TR547_VERSION = "1.2"
TR547_SHA256 = "0b9691162f349f956b271dbf8730b715ec556764c7745680e8cb4ee82df9b210"

#: RESTCONF root, RFC 8040 section 3.1. TR-547 writes every path as
#: ``{+restconf}/data/...``; the mock and the TeraFlowSDN driver both mount at
#: ``/restconf``. A plant with a different root sets ``Profile.restconf_root``.
DEFAULT_RESTCONF_ROOT = "/restconf"

#: Path templates, TR-547 v1.2 Table 5 ("Minimum subset required of TAPI
#: RESTCONF Data API", pp. 42-45). ``{uuid}`` is filled by the adapter.
PATH_CONTEXT = "/data/tapi-common:context"
PATH_SIP = PATH_CONTEXT + "/service-interface-point={uuid}"
PATH_CONNECTIVITY_CONTEXT = PATH_CONTEXT + "/tapi-connectivity:connectivity-context"
PATH_CONNECTIVITY_SERVICE = PATH_CONNECTIVITY_CONTEXT + "/connectivity-service={uuid}"
PATH_CONNECTION = PATH_CONNECTIVITY_CONTEXT + "/connection={uuid}"

#: Table 5 rows this adapter relies on: path template -> the RESTCONF
#: operations the agreement leaves standing on it. Transcribed from the
#: rendered pages 43-44, not from a text dump: the printed table strikes
#: through GET, PUT and PATCH on the connectivity context ("No UC addresses
#: PUT or PATCH for the whole context") and PATCH on the service ("PATCH
#: operation is unspecified"), and a text extraction drops the strike. The
#: struck operations are not in this mapping. Anything not in it is a call
#: this adapter must not emit.
TABLE_5_ALLOWED: Mapping[str, Tuple[str, ...]] = {
    PATH_SIP: ("GET", "PUT", "PATCH"),
    PATH_CONNECTIVITY_CONTEXT: ("POST",),
    PATH_CONNECTIVITY_SERVICE: ("GET", "PUT", "DELETE"),
    PATH_CONNECTION: ("GET",),
}

#: Status expectations. Where TR-547 states one it is cited; where it does not,
#: RFC 8040 section 4 is the source and the note says so.
#: Creation: TR-547 UC 1.0 says the server MUST return the Location header and
#: its figure 6-10 draws "HTTP/1.1 200 OK"; RFC 8040 4.4.1 says 201 Created.
#: Both are accepted, the header is required.
EXPECT_CREATED = (200, 201)
EXPECT_OK = (200,)          # TR-547 UC 1.0 figure 6-10: "HTTP/1.1 200 OK" on every GET
EXPECT_DELETED = (204,)     # TR-547 UC 10: "If the DELETE request succeeds, a 204 No Content status-line is returned"
EXPECT_REPLACED = (204,)    # TR-547 UC 11a figure 6-43: "HTTP/1.1 204 No Content"; RFC 8040 4.5: a modified resource returns 204

#: JSON namespace qualification, TR-547 section 2.6.1 / RFC 7951 section 4: the
#: root object of a POST to the connectivity context is qualified by its
#: module name; children of the same module are not.
ROOT_KEY = "tapi-connectivity:connectivity-service"

#: tapi-common ``global-class`` uuid: "Pattern: [0-9a-fA-F]{8}-... The canonical
#: representation uses lowercase characters." The adapter always emits lowercase.
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

#: Enumerations copied from the 2.1.5 YANG (tapi-common.yang typedefs). The
#: adapter checks its own output against these, so a typo here fails a test
#: rather than a plant.
LAYER_PROTOCOL_NAMES = ("ODU", "ETH", "DSR", "PHOTONIC_MEDIA")
CAPACITY_UNITS = ("TB", "TBPS", "GB", "GBPS", "MB", "MBPS", "KB", "KBPS", "GHz", "MHz")
ADMINISTRATIVE_STATES = ("LOCKED", "UNLOCKED")
OPERATIONAL_STATES = ("DISABLED", "ENABLED")
LIFECYCLE_STATES = ("PLANNED", "POTENTIAL_AVAILABLE", "POTENTIAL_BUSY", "INSTALLED", "PENDING_REMOVAL")
FORWARDING_DIRECTIONS = ("BIDIRECTIONAL", "UNIDIRECTIONAL", "UNDEFINED_OR_UNKNOWN")
PORT_DIRECTIONS = ("BIDIRECTIONAL", "INPUT", "OUTPUT", "UNIDENTIFIED_OR_UNKNOWN")
PORT_ROLES = ("SYMMETRIC", "ROOT", "LEAF", "TRUNK", "UNKNOWN")
SERVICE_TYPES = (
    "POINT_TO_POINT_CONNECTIVITY", "POINT_TO_MULTIPOINT_CONNECTIVITY",
    "MULTIPOINT_CONNECTIVITY", "ROOTED_MULTIPOINT_CONNECTIVITY",
)

#: TR-547 Table 23 capacity units by layer: "GBPS" for DSR, "GHz" for
#: PHOTONIC_MEDIA. ODU and ETH services are outside this adapter.
CAPACITY_UNIT_BY_LAYER: Mapping[str, str] = {"PHOTONIC_MEDIA": "GHz", "DSR": "GBPS"}

#: The service uuid is derived, not random, so the same intent compiles to the
#: same bytes and a re-run of a plan targets the service it created. RFC 4122
#: version 5 under a namespace that is itself derived from this project's URL.
UUID_NAMESPACE = _uuid.uuid5(_uuid.NAMESPACE_URL, "https://github.com/dimaggi-ai/optical-circuit-intent")


class TapiAdapterError(ValueError):
    """A SIP table or profile that cannot be used."""


# --- inputs -------------------------------------------------------------------

@dataclass(frozen=True)
class Sip:
    """One service interface point, as the plant's context exposes it.

    ``qualifier`` is the one entry of the SIP's
    ``supported-layer-protocol-qualifier`` the table author chose; TR-547
    Table 24 has the server provide it on the end point, and the TeraFlowSDN
    driver sends it, so the adapter sends it too. ``direction`` is the SIP's
    ``direction`` leaf, which the YANG says means BIDIRECTIONAL when absent.
    """

    uuid: str
    qualifier: str
    layer: str = "PHOTONIC_MEDIA"
    direction: str = "BIDIRECTIONAL"

    def __post_init__(self) -> None:
        if not isinstance(self.uuid, str) or not self.uuid:
            raise TapiAdapterError(f"a SIP needs a uuid, a non-empty string, not {self.uuid!r}")
        if self.layer not in LAYER_PROTOCOL_NAMES:
            raise TapiAdapterError(f"SIP {self.uuid}: unknown layer-protocol-name {self.layer!r}")
        if self.direction not in PORT_DIRECTIONS:
            raise TapiAdapterError(f"SIP {self.uuid}: unknown direction {self.direction!r}")
        if not isinstance(self.qualifier, str) or not self.qualifier:
            raise TapiAdapterError(
                f"SIP {self.uuid}: a layer-protocol-qualifier, a non-empty string, is required"
            )


@dataclass(frozen=True)
class SipTable:
    """Endpoint ``hall:port`` -> SIP. The plant owner's mapping, committed.

    No entry, no call. The adapter never derives a SIP uuid from a port name,
    because the two are named by different people for different reasons.
    """

    entries: Mapping[str, Sip]
    source: str = "-"

    def lookup(self, endpoint: Endpoint) -> Optional[Sip]:
        return self.entries.get(f"{endpoint.hall_id}:{endpoint.port}")

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], *, source: str = "-") -> "SipTable":
        entries: Dict[str, Sip] = {}
        if not isinstance(d, Mapping):
            raise TapiAdapterError("a SIP table is a JSON object, not a list or a scalar")
        raw = d.get("sips", d)
        if not isinstance(raw, Mapping) or not raw:
            raise TapiAdapterError("a SIP table is a non-empty object of 'hall:port' -> sip")
        for key, val in raw.items():
            if not isinstance(key, str) or key.count(":") != 1 or not all(key.split(":")):
                raise TapiAdapterError(f"SIP table key {key!r} is not 'hall:port'")
            if not isinstance(val, Mapping):
                raise TapiAdapterError(f"SIP table entry {key!r} must be an object")
            unknown = set(val) - {"uuid", "qualifier", "layer", "direction"}
            if unknown:
                raise TapiAdapterError(f"SIP table entry {key!r}: unknown fields {sorted(unknown)}")
            missing = [k for k in ("uuid", "qualifier") if k not in val]
            if missing:
                raise TapiAdapterError(f"SIP table entry {key!r}: missing {missing}")
            try:
                entries[key] = Sip(**val)
            except TypeError as exc:
                raise TapiAdapterError(f"SIP table entry {key!r}: {exc}") from exc
        return cls(entries=entries, source=source)

    @classmethod
    def from_json(cls, path: "str | Path") -> "SipTable":
        p = Path(path)
        with p.open() as fh:
            return cls.from_dict(json.load(fh), source=str(p))


@dataclass(frozen=True)
class Profile:
    """Which TAPI line the calls are written against, and the plant's constants.

    One profile ships. A 2.5.x profile is a second instance of this class with
    a different body template once public 2.5 fixtures exist (DECISIONS.md D17).
    """

    name: str = "tapi-2.1.5/tr-547-v1.2"
    yang_tag: str = TAPI_YANG_TAG
    yang_commit: str = TAPI_YANG_COMMIT
    restconf_root: str = DEFAULT_RESTCONF_ROOT
    layer: str = "PHOTONIC_MEDIA"
    #: Requested capacity at the photonic layer is a slot width in GHz (TR-547
    #: Table 23). ``None`` omits the container, which UC 1d allows for an
    #: unconstrained service.
    slot_width_ghz: Optional[int] = None
    service_type: str = "POINT_TO_POINT_CONNECTIVITY"
    role: str = "SYMMETRIC"

    def __post_init__(self) -> None:
        if self.layer not in CAPACITY_UNIT_BY_LAYER:
            raise TapiAdapterError(
                f"profile layer {self.layer!r}: this adapter binds PHOTONIC_MEDIA (and DSR) services only"
            )
        if self.slot_width_ghz is not None and (
            isinstance(self.slot_width_ghz, bool) or not isinstance(self.slot_width_ghz, int)
            or self.slot_width_ghz <= 0
        ):
            raise TapiAdapterError("slot_width_ghz must be a positive integer (YANG capacity-value is uint64)")
        if self.service_type not in SERVICE_TYPES:
            raise TapiAdapterError(f"unknown service-type {self.service_type!r}")
        if self.role not in PORT_ROLES:
            raise TapiAdapterError(f"unknown port role {self.role!r}")
        if not self.restconf_root.startswith("/") or self.restconf_root.endswith("/"):
            raise TapiAdapterError("restconf_root is an absolute path without a trailing slash")


# --- outputs ------------------------------------------------------------------

@dataclass(frozen=True)
class Call:
    """One HTTP call a TR-547 controller documents, argued but not made."""

    method: str
    path: str
    #: Which generic operation this call serves.
    operation: str
    purpose: str
    #: Where the method, path and expectation come from.
    citation: str
    body: Optional[Dict[str, Any]] = None
    expect_status: Tuple[int, ...] = ()
    #: Leaves the reply must carry, for the reads that verify.
    expect_fields: Mapping[str, Any] = field(default_factory=dict)
    #: Headers the reply must carry.
    expect_headers: Tuple[str, ...] = ()
    #: Is the call destructive on the plant. Only DELETE is.
    destructive: bool = False

    def render(self) -> str:
        parts = [f"{self.method} {self.path}"]
        if self.expect_status:
            parts.append("expect " + "/".join(str(s) for s in self.expect_status))
        if self.expect_headers:
            parts.append("with " + ", ".join(self.expect_headers))
        if self.expect_fields:
            parts.append("and " + ", ".join(f"{k}={v}" for k, v in self.expect_fields.items()))
        return "  ".join(parts)


@dataclass(frozen=True)
class Unmapped:
    """A generic operation, or part of one, that TAPI 2.1 has no call for."""

    operation: str
    reason: str


@dataclass(frozen=True)
class TapiPlan:
    """The generic plan, and the calls it compiles to under one profile.

    ``refused`` set means no call was emitted: the adapter would have had to
    guess. ``unmapped`` lists what the calls do not cover. A caller who prints
    only ``calls`` has thrown away the part of the answer that matters most.
    """

    plan: Plan
    profile: Profile
    service_uuid: str
    calls: Tuple[Call, ...]
    unmapped: Tuple[Unmapped, ...] = ()
    refused: Optional[str] = None
    notes: Tuple[str, ...] = ()

    @property
    def methods(self) -> Tuple[str, ...]:
        return tuple(c.method for c in self.calls)

    @property
    def destructive_calls(self) -> Tuple[Call, ...]:
        return tuple(c for c in self.calls if c.destructive)

    def render(self) -> str:
        i = self.plan.intent
        lines = [f"# {i.verb.value} {i.circuit_id} -> {self.profile.name}  service {self.service_uuid}"]
        if self.refused:
            lines.append(f"# REFUSED: {self.refused}")
        for n, c in enumerate(self.calls, 1):
            lines.append(f"{n}. [{c.operation}] {c.render()}")
        for u in self.unmapped:
            lines.append(f"# unmapped {u.operation}: {u.reason}")
        for n in self.notes:
            lines.append(f"# note: {n}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": i_to_dict(self.plan.intent),
            "profile": {
                "name": self.profile.name, "yang_tag": self.profile.yang_tag,
                "yang_commit": self.profile.yang_commit, "restconf_root": self.profile.restconf_root,
                "layer": self.profile.layer, "slot_width_ghz": self.profile.slot_width_ghz,
            },
            "service_uuid": self.service_uuid,
            "refused": self.refused,
            "calls": [
                {
                    "method": c.method, "path": c.path, "operation": c.operation,
                    "purpose": c.purpose, "citation": c.citation, "body": c.body,
                    "expect_status": list(c.expect_status),
                    "expect_fields": dict(c.expect_fields),
                    "expect_headers": list(c.expect_headers),
                    "destructive": c.destructive,
                }
                for c in self.calls
            ],
            "unmapped": [{"operation": u.operation, "reason": u.reason} for u in self.unmapped],
            "notes": list(self.notes),
        }


def i_to_dict(intent: Intent) -> Dict[str, Any]:
    return intent.to_dict()


# --- derivations ---------------------------------------------------------------

def service_uuid(circuit_id: str) -> str:
    """The TAPI uuid for a circuit id: RFC 4122 v5, lowercase, deterministic."""
    if not circuit_id:
        raise TapiAdapterError("a circuit id is required")
    return str(_uuid.uuid5(UUID_NAMESPACE, circuit_id)).lower()


#: The layout tapi-common.yang's own ``date-and-time`` typedef gives in its
#: description. The typedef is a bare string and the module imports nothing,
#: so this is the schema's word, not RFC 6991's (DECISIONS.md D19).
DATE_AND_TIME_LAYOUT = "yyyyMMddhhmmss.s[Z|{+|-}HHMm]"
DATE_AND_TIME_PATTERN = re.compile(r"^[0-9]{14}\.[0-9]+(Z|[+-][0-9]{4})$")


def date_and_time(epoch_s: float) -> str:
    """tapi-common ``date-and-time``: ``yyyyMMddhhmmss.sZ``, UTC, whole seconds.

    The tenth of a second is written ``.0``, as the typedef's description says
    to do when the clock has none.
    """
    if isinstance(epoch_s, bool) or not isinstance(epoch_s, (int, float)) or not math.isfinite(epoch_s):
        raise TapiAdapterError(f"time must be a finite number of seconds since the epoch, not {epoch_s!r}")
    if epoch_s < 0:
        raise TapiAdapterError("time before the epoch")
    try:
        stamp = datetime.fromtimestamp(int(epoch_s), tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise TapiAdapterError(f"time out of range: {exc}") from exc
    return stamp.strftime("%Y%m%d%H%M%S") + ".0Z"


def connectivity_direction(a: Sip, z: Sip) -> Optional[str]:
    """The forwarding direction two SIPs can form, or None when they cannot.

    Two bidirectional points make a BIDIRECTIONAL service. An INPUT paired
    with an OUTPUT makes a UNIDIRECTIONAL one --- the mock's OLS context
    exposes every port twice, once each way. Two inputs, two outputs, or an
    unknown direction cannot form a service, and the adapter refuses rather
    than picking one.
    """
    pair = (a.direction, z.direction)
    if pair == ("BIDIRECTIONAL", "BIDIRECTIONAL"):
        return "BIDIRECTIONAL"
    if pair in (("INPUT", "OUTPUT"), ("OUTPUT", "INPUT")):
        return "UNIDIRECTIONAL"
    return None


def _endpoint_body(local_id: str, endpoint: Endpoint, sip: Sip, profile: Profile) -> Dict[str, Any]:
    # TR-547 Table 24: local-id (M), name CSEP_NAME (M), layer-protocol-name (M),
    # layer-protocol-qualifier (M, server-provided; sent as the driver does),
    # service-interface-point (M), direction (O), role (O), administrative-state (O).
    return {
        "local-id": local_id,
        "name": [{"value-name": "CSEP_NAME", "value": str(endpoint)}],
        "layer-protocol-name": sip.layer,
        "layer-protocol-qualifier": sip.qualifier,
        "service-interface-point": {"service-interface-point-uuid": sip.uuid},
        "direction": sip.direction,
        "role": profile.role,
        "administrative-state": "UNLOCKED",
    }


def connectivity_service_body(
    intent: Intent, profile: Profile, sips: Tuple[Sip, Sip], *, now_s: float
) -> Dict[str, Any]:
    """The POST body for a REQUEST or FAILOVER_TO intent, TR-547 Table 23 shape.

    Every key is a direct child of ``connectivity-service`` in the 2.1.5 tree:
    the constraint leaves (``service-layer``, ``service-type``,
    ``requested-capacity``, ``connectivity-direction``, ``schedule``) are
    ``uses``-expanded into the service, not nested under a container, which
    is where the 2.0-era client shape differs and why the registry checks
    the key set against the vendored tree.
    """
    if intent.hold_s is None:
        raise TapiAdapterError("a service body needs a hold time for its schedule")
    a, z = sips
    direction = connectivity_direction(a, z)
    if direction is None:
        raise TapiAdapterError(
            f"SIPs {a.uuid} ({a.direction}) and {z.uuid} ({z.direction}) cannot form a service"
        )
    body: Dict[str, Any] = {
        "uuid": service_uuid(intent.circuit_id),
        "name": [{"value-name": "SERVICE_NAME", "value": intent.circuit_id}],
        "administrative-state": "UNLOCKED",
        "service-layer": profile.layer,
        "service-type": profile.service_type,
        "connectivity-direction": direction,
        "schedule": {"start-time": date_and_time(now_s), "end-time": date_and_time(now_s + intent.hold_s)},
        "end-point": [
            _endpoint_body("csep-1", intent.endpoints[0], a, profile),
            _endpoint_body("csep-2", intent.endpoints[1], z, profile),
        ],
    }
    if profile.slot_width_ghz is not None:
        unit = CAPACITY_UNIT_BY_LAYER[profile.layer]
        # capacity-value is a uint64: RFC 7951 6.1 encodes it as a JSON string,
        # and TR-547 Table 23 writes it "[0-9]{8}" (DECISIONS.md D20).
        body["requested-capacity"] = {"total-size": {"value": str(profile.slot_width_ghz), "unit": unit}}
    return body


# --- the compilation -----------------------------------------------------------

def _resolve(intent: Intent, table: SipTable, profile: Profile) -> "Tuple[Sip, Sip] | str":
    found: List[Sip] = []
    for e in intent.endpoints:
        sip = table.lookup(e)
        if sip is None:
            return f"no SIP for endpoint {e} in the table ({table.source}); the adapter does not guess one"
        if sip.layer != profile.layer:
            return f"SIP {sip.uuid} for {e} is a {sip.layer} point; the profile binds {profile.layer}"
        found.append(sip)
    if connectivity_direction(found[0], found[1]) is None:
        return (
            f"SIPs {found[0].uuid} ({found[0].direction}) and {found[1].uuid} "
            f"({found[1].direction}) cannot form a service"
        )
    return (found[0], found[1])


def compile_plan(
    plan: Plan,
    *,
    profile: Profile,
    sip_table: SipTable,
    now_s: float,
    current_service: Optional[Mapping[str, Any]] = None,
) -> TapiPlan:
    """Compile a generic plan to TAPI calls. Nothing is sent.

    ``now_s`` is passed in, never read from a clock, so the same plan compiles
    to the same bytes twice and a test can pin them. ``current_service`` is
    the GET reply for an existing service, needed only for ``extend_hold``.
    """
    intent = plan.intent
    root = profile.restconf_root
    svc = service_uuid(intent.circuit_id)
    calls: List[Call] = []
    unmapped: List[Unmapped] = []
    notes: List[str] = []

    sips: Optional[Tuple[Sip, Sip]] = None
    if intent.verb in (Verb.REQUEST, Verb.FAILOVER_TO):
        resolved = _resolve(intent, sip_table, profile)
        if isinstance(resolved, str):
            return TapiPlan(plan, profile, svc, (), (), refused=resolved)
        sips = resolved

    for op in plan.operations:
        if op.call == "reserve_ports":
            assert sips is not None
            for e, sip in zip(intent.endpoints, sips):
                calls.append(Call(
                    "GET", root + PATH_SIP.format(uuid=sip.uuid), op.call,
                    f"read the service interface point for {e}: it must exist, be unlocked and enabled",
                    "TR-547 v1.2 Table 5 (UC 0a); tapi-common admin-state-pac",
                    expect_status=EXPECT_OK,
                    expect_fields={"administrative-state": "UNLOCKED", "operational-state": "ENABLED"},
                ))
            unmapped.append(Unmapped(
                op.call,
                "a read, not a reservation: TAPI 2.1 has no primitive that holds a service "
                "interface point for a caller (TR-547 Table 5 notes no use case modifies a "
                "SIP), so two requests can pass this read and race at the POST",
            ))
        elif op.call == "cross_connect":
            assert sips is not None
            body = connectivity_service_body(intent, profile, sips, now_s=now_s)
            calls.append(Call(
                "POST", root + PATH_CONNECTIVITY_CONTEXT, op.call,
                f"create connectivity service {svc} ({intent.circuit_id}) between the two SIPs",
                "TR-547 v1.2 Table 5 (POST), UC 1.0 / UC 1d (Location MUST; figure 6-10 shows 200 OK), Tables 23-24; section 2.6.1 for the qualified root key; RFC 8040 4.4.1 for 201",
                body={ROOT_KEY: [body]},
                expect_status=EXPECT_CREATED,
                expect_headers=("Location",),  # the MUST in UC 1.0
            ))
        elif op.call == "verify_path":
            target = op.args.get("circuit_id", intent.circuit_id)
            tuuid = service_uuid(target)
            calls.append(Call(
                "GET", root + PATH_CONNECTIVITY_SERVICE.format(uuid=tuuid), op.call,
                f"read service {tuuid} back: it must be enabled, installed, and list its top connection",
                "TR-547 v1.2 Table 5 (UC 0c), Table 23 (operational-state, lifecycle-state, connection M), REQ-3",
                expect_status=EXPECT_OK,
                expect_fields={"operational-state": "ENABLED", "lifecycle-state": "INSTALLED",
                               "connection": "non-empty"},
            ))
            calls.append(Call(
                "GET", root + PATH_CONNECTION.format(uuid="{connection-uuid}"), op.call,
                "read each top connection the service lists (uuid taken from the previous reply)",
                "TR-547 v1.2 Table 5 (UC 0c), REQ-4 (route)",
                expect_status=EXPECT_OK,
            ))
            if op.args.get("expect_min_bw_gbps"):
                unmapped.append(Unmapped(
                    "verify_path.expect_min_bw_gbps",
                    f"{op.args['expect_min_bw_gbps']} Gbit/s cannot be checked through TAPI 2.1 at "
                    f"{profile.layer}: requested capacity there is spectrum in GHz (TR-547 Table 23) "
                    "and no modulation model here converts one to the other",
                ))
        elif op.call == "teardown":
            target = op.args.get("circuit_id", intent.circuit_id)
            tuuid = service_uuid(target)
            calls.append(Call(
                "DELETE", root + PATH_CONNECTIVITY_SERVICE.format(uuid=tuuid), op.call,
                f"delete service {tuuid} ({target}); the controller removes the connections only it supports",
                "TR-547 v1.2 Table 5, UC 10 (204 on success, 404 invalid-value if unknown)",
                expect_status=EXPECT_DELETED,
                destructive=True,
            ))
        elif op.call == "release_ports":
            unmapped.append(Unmapped(
                op.call,
                "nothing to call: the service interface points were never held, and deleting "
                "the service is what frees the plant's resources (TR-547 UC 10)",
            ))
        elif op.call == "extend_hold":
            path = root + PATH_CONNECTIVITY_SERVICE.format(uuid=svc)
            end = date_and_time(now_s + float(op.args["hold_s"]))
            calls.append(Call(
                "GET", path, op.call,
                f"read service {svc} before extending its schedule",
                "TR-547 v1.2 Table 5 (UC 0c); UC 11a requires administrative-state UNLOCKED",
                expect_status=EXPECT_OK,
                expect_fields={"administrative-state": "UNLOCKED"},
            ))
            if current_service is None:
                unmapped.append(Unmapped(
                    op.call,
                    f"the write is a PUT of the whole service object with schedule/end-time set to {end} "
                    "(TR-547 UC 11a, draft: modification is by PUT of the object by uuid; PATCH is for "
                    "further study). The intent does not carry the object, so pass the GET reply as "
                    "current_service to have the PUT emitted",
                ))
            else:
                obj = _unwrap_service(current_service)
                # RFC 4122 3: uuids compare case-insensitively; the body goes back lowercase
                if str(obj.get("uuid", "")).lower() != svc:
                    return TapiPlan(plan, profile, svc, (), tuple(unmapped),
                                    refused=f"current_service is {obj.get('uuid')!r}, not {svc}; "
                                            "no call was emitted")
                if obj.get("administrative-state", "UNLOCKED") != "UNLOCKED":
                    return TapiPlan(plan, profile, svc, (), tuple(unmapped),
                                    refused="the service is administratively LOCKED; UC 11a makes "
                                            "UNLOCKED a precondition of modification; no call was emitted")
                new_obj = _replace_end_time(dict(obj, uuid=svc), end)
                calls.append(Call(
                    "PUT", path, op.call,
                    f"replace service {svc} with the same object and schedule/end-time {end}",
                    "TR-547 v1.2 Table 5, UC 11a (PUT by uuid, figure 6-43 shows 204; PATCH is for further study); RFC 8040 4.5",
                    body={ROOT_KEY: [new_obj]},
                    expect_status=EXPECT_REPLACED,
                ))
                notes.append(
                    "UC 11a is marked draft in TR-547 v1.2 and discusses path constraints; a schedule "
                    "change rides the same PUT and no use case covers it (DECISIONS.md D18)"
                )
        else:  # pragma: no cover - the plan vocabulary is closed
            unmapped.append(Unmapped(op.call, "no TAPI mapping for this operation"))

    if intent.verb is Verb.FAILOVER_TO:
        notes.append(
            "the DELETE of the replaced service is the last call and follows the read that "
            "verifies the replacement; a controller that reorders these turns a degraded "
            "circuit into no circuit"
        )
    notes.extend(plan.notes)
    return TapiPlan(plan, profile, svc, tuple(calls), tuple(unmapped), notes=tuple(notes))


def compile_intent_to_tapi(
    intent: Intent,
    *,
    profile: Profile,
    sip_table: SipTable,
    now_s: float,
    current_service: Optional[Mapping[str, Any]] = None,
    boundary=None,
) -> TapiPlan:
    """``compile_intent`` then :func:`compile_plan`, in one call."""
    return compile_plan(
        compile_intent(intent, boundary=boundary),
        profile=profile, sip_table=sip_table, now_s=now_s, current_service=current_service,
    )


def _unwrap_service(reply: Mapping[str, Any]) -> Dict[str, Any]:
    """Accept a GET reply in any of the shapes a controller returns it."""
    obj: Any = reply
    for key in (ROOT_KEY, "connectivity-service"):
        if isinstance(obj, Mapping) and key in obj:
            obj = obj[key]
    if isinstance(obj, list):
        if len(obj) != 1:
            raise TapiAdapterError("current_service must hold exactly one service")
        obj = obj[0]
    if not isinstance(obj, Mapping) or "uuid" not in obj:
        raise TapiAdapterError("current_service is not a connectivity-service object")
    return dict(obj)


#: Leaves the server owns (config false in the 2.1.5 tree). A PUT body must
#: not carry them back: RFC 8040 4.5 replaces the target resource with the
#: body, and read-only data in a body is an error on a strict server.
#: tests/test_tapi.py derives this set from the tree and fails if it drifts.
_READ_ONLY_TOP = ("connection", "operational-state", "lifecycle-state")
_READ_ONLY_CSEP = ("connection-end-point", "operational-state", "lifecycle-state")
_READ_ONLY_LATENCY = ("fixed-latency-characteristic", "jitter-characteristic", "wander-characteristic")


def _replace_end_time(obj: Mapping[str, Any], end: str) -> Dict[str, Any]:
    new = {k: v for k, v in obj.items() if k not in _READ_ONLY_TOP}
    schedule = dict(new.get("schedule", {}))
    schedule["end-time"] = end
    new["schedule"] = schedule
    if "end-point" in new:
        new["end-point"] = [
            {k: v for k, v in ep.items() if k not in _READ_ONLY_CSEP} for ep in new["end-point"]
        ]
    if "latency-characteristic" in new:
        new["latency-characteristic"] = [
            {k: v for k, v in lc.items() if k not in _READ_ONLY_LATENCY}
            for lc in new["latency-characteristic"]
        ]
    return new


# --- self-description, for the registry and the docs ---------------------------

def body_keys(body: Mapping[str, Any]) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """The top-level and end-point key sets of a create body, for schema checks."""
    top = tuple(sorted(body))
    csep = tuple(sorted({k for ep in body.get("end-point", []) for k in ep}))
    return top, csep


def path_template(path: str, root: str = DEFAULT_RESTCONF_ROOT) -> str:
    """Turn an emitted path back into its Table 5 template."""
    if path.startswith(root):
        path = path[len(root):]
    path = re.sub(r"=(?:\{[^}]*\}|[^/]+)", "={uuid}", path)
    return path


def tree_children(tree_text: str, path: Tuple[str, ...]) -> Tuple[str, ...]:
    """Direct children of a node in a pyang tree rendering, by node-name path.

    ``path`` names nodes from the first one to find down to the target, each
    searched inside the previous one's subtree. pyang indents three columns
    per level and marks every node with ``+--``, which is all this relies on.
    """
    lines = tree_text.splitlines()

    def depth(line: str) -> int:
        return line.index("+--")

    def name(line: str) -> str:
        tokens = line.split("+--", 1)[1].split()
        # tokens[0] is rw/ro/-x/-u/mp; the node name follows, with * or ? marks.
        return tokens[1].rstrip("*?") if len(tokens) > 1 else ""

    start, end = 0, len(lines)
    target_depth = -1
    for node in path:
        found = None
        for i in range(start, end):
            line = lines[i]
            if "+--" not in line:
                continue
            d = depth(line)
            if target_depth >= 0 and d <= target_depth:
                break
            if name(line) == node and (target_depth < 0 or d == target_depth + 3):
                found = i
                break
        if found is None:
            raise KeyError("/".join(path))
        target_depth = depth(lines[found])
        start = found + 1
    out: List[str] = []
    for line in lines[start:end]:
        if "+--" not in line:
            continue
        d = depth(line)
        if d <= target_depth:
            break
        if d == target_depth + 3:
            out.append(name(line))
    return tuple(out)


def yang_block(yang_text: str, keyword: str, name: str) -> str:
    """The body of one ``keyword name { ... }`` statement, braces matched."""
    m = re.search(r"\n\s*" + re.escape(keyword) + r"\s+" + re.escape(name) + r"\s*\{", yang_text)
    if not m:
        raise KeyError(f"{keyword} {name}")
    i = m.end()
    depth = 1
    in_string = False
    j = i
    while j < len(yang_text) and depth:
        ch = yang_text[j]
        if ch == '"' and yang_text[j - 1] != "\\":
            in_string = not in_string
        elif not in_string:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
        j += 1
    return yang_text[i:j - 1]


def yang_enum(yang_text: str, typedef: str) -> Tuple[str, ...]:
    """The enum names of one typedef in a YANG module's text."""
    return tuple(re.findall(r"\benum\s+\"?([^\s\";{]+)\"?\s*[{;]", yang_block(yang_text, "typedef", typedef)))


def yang_uuid_pattern(yang_text: str) -> str:
    """The RFC 4122 pattern the 2.1.5 ``uuid`` typedef carries.

    It is not a YANG ``pattern`` statement: the typedef is ``type string`` and
    the pattern sits in its description, spliced with a stray ``' + '`` from
    whatever generated the module. A server therefore enforces nothing here,
    which is one more reason the adapter checks its own output.
    """
    block = yang_block(yang_text, "typedef", "uuid")
    m = re.search(r"Pattern:[ \t]*(.+)", block)
    if not m:
        raise KeyError("uuid: no pattern in the description")
    return m.group(1).strip().replace("' + '", "")


# --- the recorded mock, read back against the expectations above ------------------

def expected_reply(method: str, template: str, resource_exists: bool) -> Tuple[Tuple[int, ...], Tuple[str, ...]]:
    """What a TR-547 controller would answer, as (statuses, required headers).

    A read or delete of a resource that does not exist is a 404 with
    error-tag invalid-value (TR-547 UC 10 for DELETE; RFC 8040 4.3 for GET).
    """
    if method == "GET":
        return (EXPECT_OK if resource_exists else (404,)), ()
    if method == "POST":
        return EXPECT_CREATED, ("Location",)
    if method == "PUT":
        return EXPECT_REPLACED, ()
    if method == "DELETE":
        return (EXPECT_DELETED if resource_exists else (404,)), ()
    raise TapiAdapterError(f"no expectation for {method}")


@dataclass(frozen=True)
class Departure:
    """One place a recorded reply is not what TR-547 says a controller returns."""

    file: str
    kind: str      # "status", "no-location", "duplicate-accepted", "encoding"
    detail: str


@dataclass(frozen=True)
class RecordedCheck:
    file: str
    method: str
    template: str
    on_table: bool
    status: int
    expected: Tuple[int, ...]
    departures: Tuple[Departure, ...]


def _services_in_reply(body: Any) -> List[Mapping[str, Any]]:
    """Connectivity-service objects in a GET reply, whatever the server wrapped them in."""
    obj: Any = body
    for key in ("tapi-connectivity:connectivity-context", "connectivity-context",
                ROOT_KEY, "connectivity-service"):
        if isinstance(obj, Mapping) and key in obj:
            obj = obj[key]
    if isinstance(obj, Mapping):
        return [obj] if "uuid" in obj else []
    if isinstance(obj, list):
        return [s for s in obj if isinstance(s, Mapping) and "uuid" in s]
    return []


def _capacity_value(service: Mapping[str, Any]) -> Any:
    """The requested capacity's value as a reply carries it, or None when it carries none."""
    cap = service.get("requested-capacity")
    size = cap.get("total-size") if isinstance(cap, Mapping) else None
    return size.get("value") if isinstance(size, Mapping) else None


def check_recorded(replies: "List[Tuple[str, Mapping[str, Any]]]", known_sips: "set[str]",
                   root: str = DEFAULT_RESTCONF_ROOT) -> Tuple[RecordedCheck, ...]:
    """Walk recorded replies in order and list every departure from TR-547.

    ``replies`` are (file name, record) pairs, each record holding ``method``,
    ``path``, ``request_body``, ``status``, ``headers`` and ``body`` as the
    recorder wrote them. Service existence is tracked from the POSTs and
    DELETEs in the sequence itself, connection existence from the connection
    uuids the service read-backs list, and SIP existence comes from
    ``known_sips``. Calls whose path is not one of this adapter's Table 5
    templates are reported with ``on_table=False`` and judged on nothing. A
    read-back on the table that echoes the uint64 capacity value as a JSON
    number is a departure of kind ``encoding`` (RFC 7951 6.1; DECISIONS.md
    D20).
    """
    services: set = set()
    connections: Dict[str, set] = {}  # service uuid -> connection uuids its read-backs listed
    out: List[RecordedCheck] = []
    for fname, rec in replies:
        method = rec["method"]
        raw_path = rec["path"].split("?", 1)[0].rstrip("/")
        template = path_template(raw_path, "")
        allowed = TABLE_5_ALLOWED.get(template, ())
        on_table = method in allowed and "?" not in rec["path"]
        status = int(rec["status"])
        deps: List[Departure] = []
        expected: Tuple[int, ...] = ()
        if on_table:
            uuid = raw_path.rsplit("=", 1)[1] if "=" in raw_path.rsplit("/", 1)[1] else None
            if template == PATH_SIP:
                exists = uuid in known_sips
            elif template == PATH_CONNECTIVITY_SERVICE:
                exists = uuid in services
            elif template == PATH_CONNECTION:
                exists = any(uuid in listed for listed in connections.values())
            else:
                exists = True
            expected, headers = expected_reply(method, template, exists)
            if method == "POST":
                body = rec.get("request_body") or {}
                posted = [s.get("uuid") for s in body.get(ROOT_KEY, [])]
                dup = [u for u in posted if u in services]
                if dup and 200 <= status < 300:
                    deps.append(Departure(fname, "duplicate-accepted",
                                          f"uuid {dup[0]} already existed; RFC 8040 4.4.1 says 409"))
                services.update(u for u in posted if u)
            if method == "DELETE" and 200 <= status < 300 and uuid:
                services.discard(uuid)
                connections.pop(uuid, None)
            if method == "GET" and 200 <= status < 300:
                for svc in _services_in_reply(rec.get("body")):
                    listed = {c.get("connection-uuid") for c in (svc.get("connection") or [])
                              if isinstance(c, Mapping)} - {None}
                    if svc.get("uuid") and listed:
                        connections.setdefault(str(svc["uuid"]), set()).update(listed)
                    value = _capacity_value(svc)
                    if value is not None and not isinstance(value, str) \
                            and not any(d.kind == "encoding" for d in deps):
                        deps.append(Departure(fname, "encoding",
                                              f"capacity-value echoed as a JSON number ({value!r}); RFC 7951 6.1, "
                                              f"which section 2.6.1 mandates, writes a uint64 as a string"))
            if status not in expected:
                deps.append(Departure(fname, "status",
                                      f"{method} returned {status}; TR-547 says {'/'.join(map(str, expected))}"))
            for h in headers:
                if h not in rec.get("headers", {}):
                    deps.append(Departure(fname, "no-location", f"no {h} header on a create"))
        out.append(RecordedCheck(fname, method, template, on_table, status, expected, tuple(deps)))
    return tuple(out)
