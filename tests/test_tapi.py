"""The TAPI binding: calls argued from the plan, checked against the pinned spec.

These tests read the vendored 2.1.5 tree and YANG where a fact comes from
them, so a change to the vendored files is a change to what passes here.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
from pathlib import Path

import pytest

from ocintent.adapters import tapi
from ocintent.cli import main as cli_main
from ocintent.intent import Endpoint, Intent, Verb, compile_intent
import validate_intent as registry

ROOT = Path(__file__).resolve().parents[1]
SIP_TABLE = ROOT / "examples" / "tapi-sip-table.json"
YANG = ROOT / "data" / "tapi" / "yang"
RECORDED = ROOT / "data" / "tapi" / "recorded"

NOW = 1_788_220_800.0  # 2026-09-01T00:00:00Z


@pytest.fixture(scope="module")
def table() -> tapi.SipTable:
    return tapi.SipTable.from_json(SIP_TABLE)


@pytest.fixture(scope="module")
def profile() -> tapi.Profile:
    return tapi.Profile(slot_width_ghz=50)


def request_intent(**over) -> Intent:
    kw = dict(
        verb=Verb.REQUEST, circuit_id="stitch-12-1",
        endpoints=(Endpoint("node-1", "port-13-input"), Endpoint("node-2", "port-14-output")),
        hold_s=6 * 3600.0, min_bw_gbps=800.0, job_id="pretrain-7",
    )
    kw.update(over)
    return Intent(**kw)


def compile_(intent: Intent, table, profile, **kw) -> tapi.TapiPlan:
    return tapi.compile_intent_to_tapi(intent, profile=profile, sip_table=table, now_s=NOW, **kw)


# --- inputs -----------------------------------------------------------------------

def test_the_example_table_is_the_mock_context(table):
    assert len(table.entries) == 38
    sip = table.lookup(Endpoint("node-1", "port-13-input"))
    assert sip is not None and sip.uuid == "node-1-port-13-input"
    assert sip.direction == "INPUT" and sip.layer == "PHOTONIC_MEDIA"
    assert sip.qualifier == "tapi-photonic-media:PHOTONIC_LAYER_QUALIFIER_NMC"
    assert table.lookup(Endpoint("node-9", "port-1")) is None


@pytest.mark.parametrize("bad", [
    {},
    {"sips": {}},
    {"sips": {"no-colon": {"uuid": "x", "qualifier": "q"}}},
    {"sips": {"a:b:c": {"uuid": "x", "qualifier": "q"}}},
    {"sips": {":b": {"uuid": "x", "qualifier": "q"}}},
    {"sips": {"a:b": "not-an-object"}},
    {"sips": {"a:b": {"uuid": "x", "qualifier": "q", "colour": "blue"}}},
    {"sips": {"a:b": {"qualifier": "q"}}},
    {"sips": {"a:b": {"uuid": "x", "qualifier": "q", "layer": "OTN"}}},
    {"sips": {"a:b": {"uuid": "x", "qualifier": "q", "direction": "SIDEWAYS"}}},
    {"sips": {"a:b": {"uuid": "x", "qualifier": ""}}},
])
def test_a_malformed_sip_table_is_refused(bad):
    with pytest.raises(tapi.TapiAdapterError):
        tapi.SipTable.from_dict(bad)


@pytest.mark.parametrize("kw", [
    {"layer": "ODU"}, {"layer": "ETH"}, {"slot_width_ghz": 0}, {"slot_width_ghz": -50},
    {"slot_width_ghz": True}, {"slot_width_ghz": 37.5}, {"service_type": "MESH"},
    {"role": "BOSS"}, {"restconf_root": "restconf"}, {"restconf_root": "/restconf/"},
])
def test_a_malformed_profile_is_refused(kw):
    with pytest.raises(tapi.TapiAdapterError):
        tapi.Profile(**kw)


def test_the_default_profile_names_the_pin():
    p = tapi.Profile()
    assert p.yang_tag == "v2.1.5" and p.yang_commit == tapi.TAPI_YANG_COMMIT
    assert p.name == "tapi-2.1.5/tr-547-v1.2" and p.slot_width_ghz is None


# --- derivations --------------------------------------------------------------------

def test_the_service_uuid_is_deterministic_lowercase_and_well_formed():
    u = tapi.service_uuid("stitch-12-1")
    assert u == tapi.service_uuid("stitch-12-1")
    assert u == u.lower() and tapi.UUID_PATTERN.match(u)
    assert u != tapi.service_uuid("stitch-12-2")
    with pytest.raises(tapi.TapiAdapterError):
        tapi.service_uuid("")


def test_the_uuid_matches_the_pattern_the_yang_description_carries():
    pattern = tapi.yang_uuid_pattern((YANG / "tapi-common.yang").read_text())
    assert re.fullmatch(pattern, tapi.service_uuid("anything"))
    # and the typedef really is a bare string: nothing for a server to enforce
    block = tapi.yang_block((YANG / "tapi-common.yang").read_text(), "typedef", "uuid")
    assert "type string;" in block and "\n" + " " * 8 + "pattern" not in block


def test_date_and_time_is_the_typedef_layout_utc_whole_seconds():
    assert tapi.date_and_time(NOW) == "20260901000000.0Z"
    assert tapi.date_and_time(NOW + 6 * 3600 + 0.9) == "20260901060000.0Z"
    assert tapi.DATE_AND_TIME_PATTERN.match(tapi.date_and_time(NOW))
    block = tapi.yang_block((YANG / "tapi-common.yang").read_text(), "typedef", "date-and-time")
    assert tapi.DATE_AND_TIME_LAYOUT in block and "type string;" in block
    assert "import" not in (YANG / "tapi-common.yang").read_text().split("organization", 1)[0]
    for bad in (-1, float("inf"), float("nan"), 1e300, "now", None, True):
        with pytest.raises(tapi.TapiAdapterError):
            tapi.date_and_time(bad)


@pytest.mark.parametrize("a,z,want", [
    ("BIDIRECTIONAL", "BIDIRECTIONAL", "BIDIRECTIONAL"),
    ("INPUT", "OUTPUT", "UNIDIRECTIONAL"),
    ("OUTPUT", "INPUT", "UNIDIRECTIONAL"),
    ("INPUT", "INPUT", None),
    ("OUTPUT", "OUTPUT", None),
    ("BIDIRECTIONAL", "INPUT", None),
    ("UNIDENTIFIED_OR_UNKNOWN", "BIDIRECTIONAL", None),
])
def test_two_sips_form_a_direction_or_nothing(a, z, want):
    sa = tapi.Sip("a", "q", direction=a)
    sz = tapi.Sip("z", "q", direction=z)
    assert tapi.connectivity_direction(sa, sz) == want


# --- the create body ------------------------------------------------------------------

def test_the_create_body_is_the_table_23_shape(table, profile):
    plan = compile_(request_intent(), table, profile)
    post = [c for c in plan.calls if c.method == "POST"]
    assert len(post) == 1
    body = post[0].body
    assert list(body) == [tapi.ROOT_KEY]
    (svc,) = body[tapi.ROOT_KEY]
    assert svc["uuid"] == tapi.service_uuid("stitch-12-1")
    assert {"value-name": "SERVICE_NAME", "value": "stitch-12-1"} in svc["name"]
    assert svc["service-layer"] == "PHOTONIC_MEDIA"
    assert svc["service-type"] == "POINT_TO_POINT_CONNECTIVITY"
    assert svc["connectivity-direction"] == "UNIDIRECTIONAL"
    assert svc["administrative-state"] == "UNLOCKED"
    assert svc["schedule"] == {"start-time": "20260901000000.0Z", "end-time": "20260901060000.0Z"}
    assert svc["requested-capacity"] == {"total-size": {"value": "50", "unit": "GHz"}}
    assert len(svc["end-point"]) == 2
    a, z = svc["end-point"]
    assert a["local-id"] == "csep-1" and z["local-id"] == "csep-2"
    assert a["service-interface-point"] == {"service-interface-point-uuid": "node-1-port-13-input"}
    assert z["service-interface-point"] == {"service-interface-point-uuid": "node-2-port-14-output"}
    assert a["direction"] == "INPUT" and z["direction"] == "OUTPUT"
    assert {"value-name": "CSEP_NAME", "value": "node-1:port-13-input"} in a["name"]
    for ep in (a, z):
        assert ep["layer-protocol-name"] == "PHOTONIC_MEDIA"
        assert ep["layer-protocol-qualifier"] == "tapi-photonic-media:PHOTONIC_LAYER_QUALIFIER_NMC"
        assert ep["role"] == "SYMMETRIC" and ep["administrative-state"] == "UNLOCKED"


def test_no_slot_width_omits_requested_capacity(table):
    plan = compile_(request_intent(), table, tapi.Profile())
    (svc,) = next(c for c in plan.calls if c.method == "POST").body[tapi.ROOT_KEY]
    assert "requested-capacity" not in svc


def test_every_body_key_is_a_child_in_the_vendored_tree(table, profile):
    tree = (YANG / "tapi-connectivity.tree").read_text()
    cs_children = set(tapi.tree_children(tree, ("connectivity-service",)))
    ep_children = set(tapi.tree_children(tree, ("connectivity-service", "end-point")))
    plan = compile_(request_intent(), table, profile)
    (svc,) = next(c for c in plan.calls if c.method == "POST").body[tapi.ROOT_KEY]
    top, csep = tapi.body_keys(svc)
    assert set(top) <= cs_children, set(top) - cs_children
    assert set(csep) <= ep_children, set(csep) - ep_children
    # the 2.0-era wrapper is not a child, which is the whole reason for this test
    assert "connectivity-constraint" not in cs_children


def test_the_body_needs_a_hold(table, profile):
    intent = Intent(Verb.FAILOVER_TO, "c", request_intent().endpoints, replaces="old")
    sips = (table.lookup(intent.endpoints[0]), table.lookup(intent.endpoints[1]))
    with pytest.raises(tapi.TapiAdapterError):
        tapi.connectivity_service_body(intent, profile, sips, now_s=NOW)


def test_the_enums_the_adapter_uses_are_the_yang_enums():
    common = (YANG / "tapi-common.yang").read_text()
    conn = (YANG / "tapi-connectivity.yang").read_text()
    assert tapi.CAPACITY_UNITS == tapi.yang_enum(common, "capacity-unit")
    assert tapi.LAYER_PROTOCOL_NAMES == tapi.yang_enum(common, "layer-protocol-name")
    assert tapi.PORT_DIRECTIONS == tapi.yang_enum(common, "port-direction")
    assert tapi.FORWARDING_DIRECTIONS == tapi.yang_enum(common, "forwarding-direction")
    assert tapi.ADMINISTRATIVE_STATES == tapi.yang_enum(common, "administrative-state")
    assert tapi.OPERATIONAL_STATES == tapi.yang_enum(common, "operational-state")
    assert tapi.LIFECYCLE_STATES == tapi.yang_enum(common, "lifecycle-state")
    assert tapi.PORT_ROLES == tapi.yang_enum(common, "port-role")
    assert tapi.SERVICE_TYPES == tapi.yang_enum(conn, "service-type")


# --- the four verbs -------------------------------------------------------------------

def test_a_request_reads_twice_creates_once_and_verifies(table, profile):
    plan = compile_(request_intent(), table, profile)
    assert plan.refused is None
    assert plan.methods == ("GET", "GET", "POST", "GET", "GET")
    assert [c.operation for c in plan.calls] == [
        "reserve_ports", "reserve_ports", "cross_connect", "verify_path", "verify_path"]
    assert plan.destructive_calls == ()
    sip_reads = plan.calls[:2]
    assert sip_reads[0].path.endswith("service-interface-point=node-1-port-13-input")
    assert sip_reads[0].expect_fields == {"administrative-state": "UNLOCKED", "operational-state": "ENABLED"}
    create = plan.calls[2]
    assert create.expect_status == (200, 201) and create.expect_headers == ("Location",)
    verify = plan.calls[3]
    assert verify.expect_fields["lifecycle-state"] == "INSTALLED"
    assert verify.expect_fields["connection"] == "non-empty"
    assert {u.operation for u in plan.unmapped} == {"reserve_ports", "verify_path.expect_min_bw_gbps"}


def test_a_request_with_no_bandwidth_floor_has_no_bandwidth_gap(table, profile):
    plan = compile_(request_intent(min_bw_gbps=0.0), table, profile)
    assert {u.operation for u in plan.unmapped} == {"reserve_ports"}


def test_a_failover_deletes_exactly_once_and_last(table, profile):
    intent = Intent(
        Verb.FAILOVER_TO, "stitch-13-1",
        (Endpoint("node-1", "port-15-input"), Endpoint("node-3", "port-16-output")),
        hold_s=3600.0, replaces="stitch-12-1",
    )
    plan = compile_(intent, table, profile)
    assert plan.methods == ("GET", "GET", "POST", "GET", "GET", "DELETE")
    assert len(plan.destructive_calls) == 1
    delete = plan.calls[-1]
    assert delete.destructive and delete.method == "DELETE"
    assert delete.path.endswith("connectivity-service=" + tapi.service_uuid("stitch-12-1"))
    assert delete.expect_status == (204,)
    # the create targets the new service, the delete the old one
    assert plan.calls[2].body[tapi.ROOT_KEY][0]["uuid"] == tapi.service_uuid("stitch-13-1")
    assert any("last call" in n for n in plan.notes)


def test_a_release_is_one_delete(table, profile):
    plan = compile_(Intent(Verb.RELEASE, "stitch-12-1"), table, profile)
    assert plan.methods == ("DELETE",)
    assert plan.calls[0].destructive
    assert [u.operation for u in plan.unmapped] == ["release_ports"]


def test_a_hold_without_the_object_reads_and_names_the_write_it_cannot_make(table, profile):
    plan = compile_(Intent(Verb.HOLD_UNTIL, "stitch-12-1", hold_s=12 * 3600.0), table, profile)
    assert plan.refused is None
    assert plan.methods == ("GET",)
    assert plan.calls[0].expect_fields == {"administrative-state": "UNLOCKED"}
    (gap,) = plan.unmapped
    assert gap.operation == "extend_hold"
    assert "20260901120000.0Z" in gap.reason and "PUT" in gap.reason


def current_object(table, profile) -> dict:
    plan = compile_(request_intent(), table, profile)
    obj = dict(plan.calls[2].body[tapi.ROOT_KEY][0])
    obj.update({
        "operational-state": "ENABLED", "lifecycle-state": "INSTALLED",
        "connection": [{"connection-uuid": obj["uuid"]}],
    })
    obj["end-point"] = [dict(ep, **{"connection-end-point": [{"connection-end-point-uuid": "cep"}],
                                    "operational-state": "ENABLED"}) for ep in obj["end-point"]]
    return obj


@pytest.mark.parametrize("wrap", ["bare", "root-list", "unqualified-list"])
def test_a_hold_with_the_object_puts_it_back_with_only_the_end_time_changed(table, profile, wrap):
    obj = current_object(table, profile)
    current = {"bare": obj, "root-list": {tapi.ROOT_KEY: [obj]},
               "unqualified-list": {"connectivity-service": [obj]}}[wrap]
    plan = compile_(Intent(Verb.HOLD_UNTIL, "stitch-12-1", hold_s=12 * 3600.0), table, profile,
                    current_service=current)
    assert plan.refused is None and plan.unmapped == ()
    assert plan.methods == ("GET", "PUT")
    put = plan.calls[1]
    assert put.expect_status == (204,) and not put.destructive
    (sent,) = put.body[tapi.ROOT_KEY]
    assert sent["schedule"] == {"start-time": "20260901000000.0Z", "end-time": "20260901120000.0Z"}
    for ro in ("connection", "operational-state", "lifecycle-state"):
        assert ro not in sent
    for ep in sent["end-point"]:
        assert "connection-end-point" not in ep and "operational-state" not in ep
    unchanged = {k: v for k, v in obj.items() if k not in ("schedule", "connection", "operational-state",
                                                            "lifecycle-state", "end-point")}
    assert {k: sent[k] for k in unchanged} == unchanged


def test_a_hold_on_a_locked_service_is_refused(table, profile):
    obj = dict(current_object(table, profile), **{"administrative-state": "LOCKED"})
    plan = compile_(Intent(Verb.HOLD_UNTIL, "stitch-12-1", hold_s=3600.0), table, profile,
                    current_service=obj)
    assert plan.refused and "LOCKED" in plan.refused
    assert plan.calls == () and plan.methods == ()


def test_a_hold_with_someone_elses_object_is_refused(table, profile):
    obj = dict(current_object(table, profile), uuid=tapi.service_uuid("other"))
    plan = compile_(Intent(Verb.HOLD_UNTIL, "stitch-12-1", hold_s=3600.0), table, profile,
                    current_service=obj)
    assert plan.refused and "not" in plan.refused
    assert plan.calls == ()


@pytest.mark.parametrize("bad", [{}, {"name": []}, {tapi.ROOT_KEY: []}, {tapi.ROOT_KEY: [{"uuid": "a"}, {"uuid": "b"}]}])
def test_an_object_that_is_not_a_service_raises(bad):
    with pytest.raises(tapi.TapiAdapterError):
        tapi._unwrap_service(bad)


# --- refusals ---------------------------------------------------------------------------

def test_an_unknown_endpoint_refuses_with_no_calls(table, profile):
    intent = request_intent(endpoints=(Endpoint("node-1", "port-13-input"), Endpoint("node-9", "port-1")))
    plan = compile_(intent, table, profile)
    assert plan.calls == () and plan.refused and "node-9:port-1" in plan.refused
    assert "does not guess" in plan.refused


def test_two_inputs_refuse(table, profile):
    intent = request_intent(endpoints=(Endpoint("node-1", "port-13-input"), Endpoint("node-2", "port-14-input")))
    plan = compile_(intent, table, profile)
    assert plan.calls == () and "cannot form a service" in plan.refused


def test_a_layer_mismatch_refuses(profile):
    t = tapi.SipTable.from_dict({"sips": {
        "h1:p1": {"uuid": "s1", "qualifier": "q", "layer": "DSR"},
        "h2:p2": {"uuid": "s2", "qualifier": "q", "layer": "DSR"},
    }})
    plan = compile_(request_intent(endpoints=(Endpoint("h1", "p1"), Endpoint("h2", "p2"))), t, profile)
    assert plan.calls == () and "DSR" in plan.refused


def test_a_dsr_profile_uses_gbps():
    t = tapi.SipTable.from_dict({"sips": {
        "h1:p1": {"uuid": "s1", "qualifier": "tapi-dsr:DIGITAL_SIGNAL_TYPE_100_GigE", "layer": "DSR"},
        "h2:p2": {"uuid": "s2", "qualifier": "tapi-dsr:DIGITAL_SIGNAL_TYPE_100_GigE", "layer": "DSR"},
    }})
    prof = tapi.Profile(layer="DSR", slot_width_ghz=100)
    plan = compile_(request_intent(endpoints=(Endpoint("h1", "p1"), Endpoint("h2", "p2"))), t, prof)
    (svc,) = plan.calls[2].body[tapi.ROOT_KEY]
    assert svc["requested-capacity"]["total-size"]["unit"] == "GBPS"
    assert svc["connectivity-direction"] == "BIDIRECTIONAL"


# --- table 5 and the rendering ------------------------------------------------------------

def all_plans(table, profile):
    yield compile_(request_intent(), table, profile)
    yield compile_(Intent(Verb.FAILOVER_TO, "n", request_intent().endpoints, hold_s=10.0, replaces="o"),
                   table, profile)
    yield compile_(Intent(Verb.RELEASE, "stitch-12-1"), table, profile)
    yield compile_(Intent(Verb.HOLD_UNTIL, "stitch-12-1", hold_s=10.0), table, profile,
                   current_service=current_object(table, profile))


def test_every_call_is_a_table_5_path_with_a_standing_method(table, profile):
    for plan in all_plans(table, profile):
        for call in plan.calls:
            template = tapi.path_template(call.path)
            assert template in tapi.TABLE_5_ALLOWED, call.path
            assert call.method in tapi.TABLE_5_ALLOWED[template], (call.method, template)


def test_path_template_strips_root_and_keys():
    assert tapi.path_template("/restconf/data/tapi-common:context/service-interface-point=abc") == tapi.PATH_SIP
    assert tapi.path_template(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/connection={connection-uuid}"
    ) == tapi.PATH_CONNECTION
    assert tapi.path_template("/x/data/tapi-common:context", root="/x") == tapi.PATH_CONTEXT


def test_the_plan_serialises_and_renders(table, profile):
    for plan in all_plans(table, profile):
        d = plan.to_dict()
        text = json.dumps(d, sort_keys=True)
        assert json.loads(text) == d
        assert d["service_uuid"] == plan.service_uuid
        rendered = plan.render()
        assert rendered.startswith("# " + plan.plan.intent.verb.value)
        for u in plan.unmapped:
            assert u.operation in rendered
    refused = compile_(request_intent(endpoints=(Endpoint("node-1", "port-13-input"), Endpoint("z", "z"))),
                       table, profile)
    assert "REFUSED" in refused.render()


def test_compiling_the_same_intent_twice_gives_the_same_bytes(table, profile):
    a = compile_(request_intent(), table, profile).to_dict()
    b = compile_(request_intent(), table, profile).to_dict()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_compile_plan_accepts_a_plan_from_compile_intent(table, profile):
    plan = compile_intent(request_intent())
    tp = tapi.compile_plan(plan, profile=profile, sip_table=table, now_s=NOW)
    assert tp.plan is plan and tp.methods[2] == "POST"


# --- the recorded mock ----------------------------------------------------------------------

def load_recorded():
    man = json.loads((RECORDED / "MANIFEST.json").read_text())
    return [(n, json.loads((RECORDED / n).read_text())) for n in sorted(man["files"])]


def test_the_recorded_replies_are_judged_where_they_are_on_the_table(table):
    known = {s.uuid for s in table.entries.values()}
    checks = tapi.check_recorded(load_recorded(), known)
    by_file = {c.file: c for c in checks}
    assert not by_file["01-get-context-fields-uuid.json"].on_table
    assert not by_file["13-post-connectivity-service-list-cs1.json"].on_table
    assert by_file["02-get-sip-node-1-port-13-input.json"].departures == ()
    assert by_file["10-delete-connectivity-service-cs1.json"].departures == ()
    kinds = {(d.file, d.kind) for c in checks for d in c.departures}
    assert ("05-post-connectivity-context-cs1.json", "no-location") in kinds
    assert ("12-delete-connectivity-service-unknown.json", "status") in kinds
    assert ("09-post-connectivity-context-cs1-again.json", "duplicate-accepted") in kinds
    assert ("06-get-connectivity-service-cs1.json", "encoding") in kinds
    assert not any(c.departures for c in checks if not c.on_table)  # off the table is judged on nothing


def test_a_read_back_that_echoes_the_uint64_as_a_number_is_an_encoding_departure(table):
    """RFC 7951 6.1 writes a uint64 as a string; a number back is the mock's, not the agreement's (D20)."""
    known = {s.uuid for s in table.entries.values()}
    rec = dict(load_recorded())
    create = rec["05-post-connectivity-context-cs1.json"]
    for value, kinds in ((50, {"encoding"}), ("50", set()), (None, set())):
        read = copy.deepcopy(rec["06-get-connectivity-service-cs1.json"])
        svc = tapi._services_in_reply(read["body"])[0]
        if value is None:
            del svc["requested-capacity"]
        else:
            svc["requested-capacity"]["total-size"]["value"] = value
        checks = tapi.check_recorded([("05-post-connectivity-context-cs1.json", create),
                                      ("06-get-connectivity-service-cs1.json", read)], known)
        got = {d.kind for c in checks for d in c.departures if c.file.startswith("06")}
        assert got == kinds, (value, got)


def test_expected_reply_table():
    assert tapi.expected_reply("GET", tapi.PATH_SIP, True) == ((200,), ())
    assert tapi.expected_reply("GET", tapi.PATH_SIP, False) == ((404,), ())
    assert tapi.expected_reply("POST", tapi.PATH_CONNECTIVITY_CONTEXT, True) == ((200, 201), ("Location",))
    assert tapi.expected_reply("DELETE", tapi.PATH_CONNECTIVITY_SERVICE, False) == ((404,), ())
    with pytest.raises(tapi.TapiAdapterError):
        tapi.expected_reply("PATCH", tapi.PATH_CONNECTIVITY_SERVICE, True)


# --- the command line -----------------------------------------------------------------------

def run(argv, capsys):
    code = cli_main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_compiles_a_request(capsys):
    code, out, _ = run(["tapi", "examples/tapi-request.json", "--sip-table", str(SIP_TABLE),
                        "--slot-width-ghz", "50", "--now", "2026-09-01T00:00:00Z"], capsys)
    assert code == 0
    assert "POST /restconf/data/tapi-common:context/tapi-connectivity:connectivity-context" in out
    assert "unmapped reserve_ports" in out


def test_cli_json_is_the_plan_dict(capsys):
    code, out, _ = run(["tapi", "examples/tapi-request.json", "--sip-table", str(SIP_TABLE),
                        "--now", "2026-09-01T00:00:00Z", "--json"], capsys)
    assert code == 0
    d = json.loads(out)
    assert d["calls"][2]["method"] == "POST" and d["refused"] is None


def test_cli_refuses_an_unknown_endpoint(capsys):
    code, out, _ = run(["tapi", "examples/tapi-request-unknown-port.json", "--sip-table", str(SIP_TABLE),
                        "--now", "2026-09-01T00:00:00Z"], capsys)
    assert code == 1 and "REFUSED" in out


def test_cli_exit_2_on_unreadable_inputs(capsys, tmp_path):
    code, _, err = run(["tapi", "examples/tapi-request.json", "--sip-table", str(tmp_path / "none.json")], capsys)
    assert code == 2 and "cannot read" in err
    code, _, err = run(["tapi", str(tmp_path / "none.json"), "--sip-table", str(SIP_TABLE)], capsys)
    assert code == 2
    code, _, err = run(["tapi", "examples/tapi-request.json", "--sip-table", str(SIP_TABLE),
                        "--now", "yesterday"], capsys)
    assert code == 2 and "now" in err
    bad = tmp_path / "bad.json"
    bad.write_text('{"sips": {"a:b": {"uuid": "x", "qualifier": "q", "layer": "OTN"}}}')
    code, _, err = run(["tapi", "examples/tapi-request.json", "--sip-table", str(bad)], capsys)
    assert code == 2


def test_cli_hold_with_current_object(capsys):
    code, out, _ = run(["tapi", "examples/tapi-hold-until.json", "--sip-table", str(SIP_TABLE),
                        "--now", "2026-09-01T00:00:00Z",
                        "--current-service", "examples/tapi-current-service.json"], capsys)
    assert code == 0 and "PUT " in out
    code, out, _ = run(["tapi", "examples/tapi-hold-until.json", "--sip-table", str(SIP_TABLE),
                        "--now", "2026-09-01T00:00:00Z"], capsys)
    assert code == 0 and "unmapped extend_hold" in out


# --- what the QA round added ------------------------------------------------------------------

def test_the_registry_lists_seventeen_tapi_points_each_declaring_the_kind_it_builds():
    """A raising point fails in its declared kind (DECISIONS.md D12); the declaration must match."""
    assert len(registry.TAPI_REGISTRY) == 17
    names = {p.name for p in registry.run_registry()}
    for fn in registry.TAPI_REGISTRY:
        point = fn()
        assert point.name == fn.__name__[len("point_"):].replace("_", "-")
        assert point.name in names
        assert (point.kind, point.reference) == (fn.kind, fn.reference), fn.__name__


def test_a_hold_accepts_the_controllers_uppercase_uuid_and_writes_it_back_lowercase(table, profile):
    obj = dict(current_object(table, profile))
    obj["uuid"] = obj["uuid"].upper()
    plan = compile_(Intent(Verb.HOLD_UNTIL, "stitch-12-1", hold_s=3600.0), table, profile, current_service=obj)
    assert plan.refused is None and plan.methods == ("GET", "PUT")
    (sent,) = plan.calls[1].body[tapi.ROOT_KEY]
    assert sent["uuid"] == tapi.service_uuid("stitch-12-1")
    assert plan.calls[1].path.endswith("=" + sent["uuid"])


def test_the_put_strips_the_read_only_leaves_under_latency_characteristic(table, profile):
    obj = dict(current_object(table, profile))
    obj["latency-characteristic"] = [{
        "traffic-property-name": "p", "queing-latency-characteristic": "1",
        "fixed-latency-characteristic": "2", "jitter-characteristic": "3", "wander-characteristic": "4",
    }]
    plan = compile_(Intent(Verb.HOLD_UNTIL, "stitch-12-1", hold_s=3600.0), table, profile, current_service=obj)
    (sent,) = plan.calls[1].body[tapi.ROOT_KEY]
    assert sent["latency-characteristic"] == [{"traffic-property-name": "p", "queing-latency-characteristic": "1"}]


def _read_only_leaves_under_service(tree: str):
    """(path, name) of every ro node under connectivity-service whose ancestors are all rw."""
    lines = tree.splitlines()
    start = next(i for i, line in enumerate(lines) if "+--rw connectivity-service*" in line)
    base = lines[start].index("+--")
    stack, found = [], set()
    for line in lines[start + 1:]:
        if "+--" not in line:
            continue
        depth = line.index("+--")
        if depth <= base:
            break
        tokens = line.split("+--", 1)[1].split()
        flag, name = tokens[0], tokens[1].rstrip("*?") if len(tokens) > 1 else ""
        while stack and stack[-1][0] >= depth:
            stack.pop()
        read_only = flag == "ro"
        if read_only and not any(s[2] for s in stack):
            found.add((tuple(s[1] for s in stack), name))
        stack.append((depth, name, read_only))
    return found


def test_the_read_only_strip_is_exactly_the_trees_ro_leaves_under_a_service():
    """Derived from the vendored tree, so a leaf the strip forgets fails here, not on a server."""
    found = _read_only_leaves_under_service((YANG / "tapi-connectivity.tree").read_text())
    declared = ({((), n) for n in tapi._READ_ONLY_TOP}
                | {(("end-point",), n) for n in tapi._READ_ONLY_CSEP}
                | {(("latency-characteristic",), n) for n in tapi._READ_ONLY_LATENCY})
    assert found == declared
    assert len(found) == 9


@pytest.mark.parametrize("bad", [
    [], [{"uuid": "x", "qualifier": "q"}], "sips", 3, None,
    {"sips": {"a:b": {"uuid": 7, "qualifier": "q"}}},
    {"sips": {"a:b": {"uuid": "x", "qualifier": 7}}},
    {"sips": {"a:b": {"uuid": None, "qualifier": "q"}}},
])
def test_a_sip_table_of_the_wrong_shape_is_refused_with_a_reason_not_a_signature(bad):
    with pytest.raises(tapi.TapiAdapterError) as info:
        tapi.SipTable.from_dict(bad)
    assert "__init__" not in str(info.value)


def test_a_missing_sip_field_is_named():
    with pytest.raises(tapi.TapiAdapterError, match=r"missing \['uuid'\]"):
        tapi.SipTable.from_dict({"sips": {"a:b": {"qualifier": "q"}}})
    with pytest.raises(tapi.TapiAdapterError, match=r"missing \['qualifier'\]"):
        tapi.SipTable.from_dict({"sips": {"a:b": {"uuid": "x"}}})


@pytest.mark.parametrize("now", ["inf", "nan", "-5", "1e300"])
def test_cli_refuses_a_now_it_cannot_write_into_a_schedule(capsys, now):
    code, _, err = run(["tapi", "examples/tapi-request.json", "--sip-table", str(SIP_TABLE), "--now", now], capsys)
    assert code == 2 and "cannot read the inputs" in err


def test_cli_refuses_a_sip_table_that_is_a_list(capsys, tmp_path):
    bad = tmp_path / "list.json"
    bad.write_text('[{"uuid": "x", "qualifier": "q"}]')
    code, _, err = run(["tapi", "examples/tapi-request.json", "--sip-table", str(bad)], capsys)
    assert code == 2 and "JSON object" in err


def test_the_recorder_sends_the_body_the_adapter_compiles():
    """data/tapi/record_replies.py carries the body by hand; this keeps it the adapter's."""
    spec = importlib.util.spec_from_file_location("record_replies", ROOT / "data" / "tapi" / "record_replies.py")
    recorder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recorder)
    t = tapi.SipTable.from_dict({"sips": {
        "hall-a:ocs-1/4": {"uuid": "node-1-port-13-input", "direction": "INPUT",
                           "qualifier": "tapi-photonic-media:PHOTONIC_LAYER_QUALIFIER_NMC"},
        "hall-b:ocs-3/9": {"uuid": "node-2-port-14-output", "direction": "OUTPUT",
                           "qualifier": "tapi-photonic-media:PHOTONIC_LAYER_QUALIFIER_NMC"},
    }})
    intent = Intent(Verb.REQUEST, "stitch-ab-1", (Endpoint("hall-a", "ocs-1/4"), Endpoint("hall-b", "ocs-3/9")),
                    hold_s=6 * 3600.0)
    plan = tapi.compile_intent_to_tapi(intent, profile=tapi.Profile(slot_width_ghz=50), sip_table=t,
                                       now_s=1_788_426_000.0)  # 2026-09-03T09:00:00Z, the recording's start
    body = json.loads(json.dumps(plan.calls[2].body))
    body[tapi.ROOT_KEY][0]["uuid"] = recorder.SERVICE_UUID
    assert body == recorder.CS_BODY
    assert recorder.SERVICE_UUID == recorder.SERVICE_UUID.lower() and tapi.UUID_PATTERN.match(recorder.SERVICE_UUID)
