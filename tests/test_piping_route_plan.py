from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.piping_route_plan import TOOL, _member_specs, _write_sequence, piping_route_plan, register_piping_route_plan
from aveva_e3d_mcp.protocol import McpServer


SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=False)
ARGS = {
    "root_refno": "=1/1",
    "planned_members": [
        {"element_type": "BRAN", "name": "/B2"},
        {"element_type": "TUBI", "name": "/T1", "parent_index": 0,
         "attributes": {"SPREF": "/A3B/PA80"}, "set_attributes": {"DIAM": "80"}},
    ],
    "endpoints": [{"refno": "=1/9", "expected_type": "NOZZ", "point_number": 4}],
}
COMPLETE_ARGS = {**ARGS, "planned_members": [
    {key: value for key, value in member.items() if key != "parent_index"}
    for member in ARGS["planned_members"]
]}
MISSING = object()


@pytest.fixture(autouse=True)
def forbid_live_bridge_requests():
    with patch.object(BridgeClient, "_request", side_effect=AssertionError("Native reads must be mocked")) as request:
        yield
    request.assert_not_called()


def context(session="session"):
    identity = {"version": "E3D21", "session_id": session, "bridge_build": "build"}
    return {"ok": True, "version": "E3D21", "result": {
        "context_complete": True, "read_only": True,
        "readiness": {"before": "ready", "after": "ready"},
        "session_coherence": {"status": "consistent", "after": identity},
        "context": {"project": "APS", "mdb": "ALL", "current_element": "=1/44"},
    }}


def source_context(session="session"):
    return {"version": "E3D21", "session_id": session, "bridge_build": "build",
            "project": "APS", "mdb": "ALL", "current_element": "=1/44"}


def endpoint_context():
    return {"project": "APS", "mdb": "ALL", "current_element": "=1/44",
            "current_element_type": "EQUI"}


def endpoint_context_evidence():
    return {
        "context": {"before": endpoint_context(), "after": endpoint_context(),
                    "same_project_instance": True, "same_mdb_instance": True,
                    "identity_unchanged": True, "ce_unchanged": True, "boundary_agreement": True},
        "project": "APS", "project_after": "APS", "mdb": "ALL", "mdb_after": "ALL",
        "ce_before": "=1/44", "ce_after": "=1/44", "identity_unchanged": True, "ce_unchanged": True,
    }


def member_manifest():
    before = {"project": "APS", "mdb": "ALL", "current_element": "=1/44", "current_element_type": "EQUI"}
    return {"ok": True, "version": "E3D21", "session_id": "session", "bridge_build": "build",
            "result": {"operation": "pipe_member_manifest", "available": True, "success": True,
                "complete": True, "read_only": True, "single_dispatch": True, "atomic_snapshot": False,
                "root": {"id": 0, "refno": "=1/1", "type": "PIPE", "name": "/P1", "identity_key": "=1/1|PIPE", "depth": 0},
                "rows": [
                    {"id": 1, "refno": "=1/2", "type": "BRAN", "name": "/B1", "identity_key": "=1/2|BRAN",
                     "depth": 1, "parent_index": 0, "sibling_index": 1, "values": {}, "typed_values": {},
                     "errors": {}, "typed_read_errors": {}, "unavailable_attributes": [], "name_error": None, "complete": True},
                    {"id": 2, "refno": "=1/3", "type": "TUBI", "name": "ileave tube of =1/3", "identity_key": "=1/3|TUBI",
                     "depth": 2, "parent_index": 1, "sibling_index": 1, "values": {}, "typed_values": {},
                     "errors": {}, "typed_read_errors": {}, "unavailable_attributes": [], "name_error": None, "complete": True},
                ],
                "context": {"before": before, "after": dict(before),
                    "same_project_instance": True, "same_mdb_instance": True,
                    "boundary_agreement": True},
            }}


def branch_state():
    return {"ok": True, "version": "E3D21", "result": {
        "operation": "piping_branch_state", "element": {"refno": "=1/2", "type": "BRAN", "name": "/B1"},
        "success": True, "complete": True, "available": True,
        "context": {"before": source_context(), "after": source_context(),
                    "boundary_agreement": True},
        "attributes": {"values": {"HREF": "=1/3", "TREF": "=1/8"}},
    }}


def connection_points(refno, kind):
    point_number = 4 if refno == "=1/9" else 1
    return {"ok": True, "version": "E3D21", "session_id": "session", "bridge_build": "build", "result": {
        "complete": True, "available": True, "element": {"refno": refno, "type": kind},
        "points": [{"number": point_number, "available": True}],
        **endpoint_context_evidence(),
    }}


def schema(refno, kind, names):
    rows = [{"requested_name": name, "known": True, "supported": name != "SPREF" or kind == "TUBI",
             "sdk_settable_now": False, "direct_edit_supported": False}
            for name in names]
    return {"ok": True, "version": "E3D21", "session_id": "session", "bridge_build": "build", "result": {
        "success": True, "element": {"refno": refno, "type": kind}, "child_types": [],
        "attributes": rows, "read_errors": {},
        **endpoint_context_evidence(),
    }}


def trace_reply(starts, _hops=None, _nodes=None):
    return {"ok": True, "version": "E3D21", "session_id": "session", "bridge_build": "build",
        "result": {"complete": True, "project_before": "APS", "project_after": "APS",
                   "mdb_before": "ALL", "mdb_after": "ALL", "ce_before": "=1/44",
                   "ce_after": "=1/44", "identity_unchanged": True, "ce_unchanged": True,
                   "nodes": [{"requested_refno": start["refno"], "requested_type": start.get("expected_type"),
                              "point_number": start["point_number"]} for start in starts]}}


def observed_plan(*, after=None, args=None, mutate_source=None, previews=None, preview_after=None):
    bridge = BridgeClient(SETTINGS)
    contexts = [context(), context() if after is None else after]
    if isinstance(args, dict) and args.get("include_native_previews") is True:
        contexts.append(context() if preview_after is None else preview_after)

    def source(kind, reply):
        if mutate_source is not None:
            mutate_source(kind, reply)
        return reply

    with patch("aveva_e3d_mcp.piping_route_plan.context_snapshot", side_effect=contexts), \
         patch("aveva_e3d_mcp.piping_route_plan.pipe_member_manifest", return_value=source("member_manifest", member_manifest())) as manifest, \
         patch("aveva_e3d_mcp.piping_route_plan.piping_branch_state", return_value=source("branch_state", branch_state())) as branch, \
         patch.object(BridgeClient, "connection_points", side_effect=lambda refno, _wrt, _points, **kw:
                      source("endpoint" if refno == "=1/9" else "connection_points",
                             connection_points(refno, kw["expected_type"]))) as points, \
         patch.object(BridgeClient, "trace_connection_network",
                      side_effect=lambda starts, _hops, _nodes: source("connection_trace", trace_reply(starts))) as trace, \
         patch.object(BridgeClient, "element_schema", side_effect=lambda refno, names, _offset, _limit,
                      expected_type="": source("schema", schema(refno, expected_type, names))) as read_schema, \
         patch.object(BridgeClient, "create_element", side_effect=previews) as create, \
         patch.object(BridgeClient, "set_attributes") as set_attributes, \
         patch.object(BridgeClient, "command") as command:
        response = piping_route_plan(bridge, ARGS if args is None else args)
    return response, (manifest, branch, points, trace, read_schema, create, set_attributes, command)


def source_records(result, source_kind):
    if source_kind == "member_manifest":
        return [result["sources"]["member_manifest"]]
    if source_kind == "endpoint":
        return result["endpoints"]
    group = {"branch_state": "branch_states", "schema": "schemas"}.get(source_kind, source_kind)
    return result["sources"][group]


def assert_source_downgraded(result, source_kind, expected):
    assert result["complete"] is False
    assert result["status"] == "partial_or_unknown"
    records = source_records(result, source_kind)
    assert records
    assert all(row["binding"]["matches_enclosing_context"] is expected for row in records)
    assert {row["status"] for row in result["property_candidates"]} == {"unknown"}
    assert {row["status"] for row in result["type_candidates"]} == {"unknown"}
    assert {row["status"] for row in result["name_candidates"]} == {"unknown"}
    assert {row["observed_status"] for row in result["property_candidates"]} == {
        "candidate", "not_direct_edit_supported"}


def replace_evidence(mapping, key, value):
    if value is MISSING:
        mapping.pop(key, None)
    else:
        mapping[key] = value


def test_complete_observation_accepts_the_distinct_native_and_branch_binding_contracts():
    response, calls = observed_plan(args=COMPLETE_ARGS)
    result = response["result"]
    assert result["complete"] is result["success"] is True
    assert result["status"] == "complete_observation"
    assert result["read_errors"] == []
    for source_kind in ("member_manifest", "branch_state", "connection_points", "endpoint", "connection_trace", "schema"):
        assert all(row["binding"]["matches_enclosing_context"] is True for row in source_records(result, source_kind))
    branch = result["sources"]["branch_states"][0]
    assert "session_id" not in branch["response"] and "bridge_build" not in branch["response"]
    assert branch["binding"]["checks"]["source_context_before_session_id"] is True
    assert branch["binding"]["checks"]["source_context_after_bridge_build"] is True
    assert all(call.call_count == 0 for call in calls[5:])


def create_preview(parent, kind, name, attributes, position, x_axis, y_axis, wrt, dry_run):
    return {"ok": True, "version": "E3D21", "result": {
        "success": True, "dry_run": dry_run, "revision": "a" * 64, "parent": {"refno": parent},
        "frame": {"requested": wrt}, "ce_before": "=1/44", "ce_after": "=1/44", "ce_unchanged": True,
        "submitted": {"name": name, "attributes": attributes, "position": position,
                      "orientation": None if x_axis is None else {"x_axis": x_axis, "y_axis": y_axis}},
    }}


def test_optional_native_previews_bind_exact_root_children_and_defer_uncreated_targets():
    orientation = {"x_axis": [1, 0, 0], "y_axis": [0, 1, 0]}
    members = [{**ARGS["planned_members"][0], "orientation": orientation}, ARGS["planned_members"][1]]
    response, calls = observed_plan(args={**ARGS, "planned_members": members, "include_native_previews": True},
                                    previews=create_preview)
    result = response["result"]
    previews = result["native_previews"]
    assert result["complete"] is False
    assert previews["requested"] is True
    assert previews["prepared_count"] == 1
    assert previews["deferred_count"] == 2
    assert previews["complete"] is False
    assert previews["status"] == "partial_or_unknown"
    creates = [step for step in result["write_sequence"] if step["kind"] == "create_preview"]
    assert [step["preview_status"] for step in creates] == ["prepared", "deferred_parent_not_yet_created"]
    assert creates[0]["expected_revision"] == "a" * 64
    assert creates[0]["execution_arguments"]["dry_run"] is False
    assert creates[0]["execution_arguments"]["orientation"] == orientation
    assert creates[1]["expected_revision"] is None
    setters = [step for step in result["write_sequence"] if step["kind"] == "set_attributes_preview"]
    assert setters[0]["preview_status"] == "deferred_until_created_refno"
    calls[5].assert_called_once_with("=1/1", "BRAN", "/B2", {}, None, [1, 0, 0], [0, 1, 0], "OWNER", True)
    assert calls[6].call_count == 0
    assert previews["preview_context"]["boundary_agreement"] is True


def test_native_fault_during_optional_preview_stops_without_followup_context_or_create_reads():
    fault = BridgeError("native database fault", diagnostics={"native_fault_latched": True})
    response, calls = observed_plan(args={**COMPLETE_ARGS, "include_native_previews": True}, previews=[fault])
    result = response["result"]
    assert result["complete"] is True
    assert result["native_previews"]["status"] == "stopped_on_native_fault"
    assert result["native_previews"]["stopped_on_native_fault"] is True
    assert result["native_previews"]["preview_context"]["after"] is None
    assert calls[5].call_count == 1
    assert calls[6].call_count == 0
    assert calls[7].call_count == 0


@pytest.mark.parametrize("second", ["drift", "fault"])
def test_optional_preview_withholds_earlier_revisions_on_drift_or_fault(second):
    def preview(*args):
        if second == "fault" and args[2] == "/T1":
            raise BridgeError("native fault", diagnostics={"state": {"native_database_fault": {"has_fault": True}}})
        return create_preview(*args)

    response, calls = observed_plan(args={**COMPLETE_ARGS, "include_native_previews": True},
        previews=preview, preview_after=context(session="other") if second == "drift" else None)
    result = response["result"]
    assert result["native_previews"]["status"] == (
        "context_unverified" if second == "drift" else "stopped_on_native_fault")
    assert calls[5].call_count == 2
    assert all(step["expected_revision"] is None and "execution_arguments" not in step
               for step in result["write_sequence"])
    if second == "drift":
        assert all(step["preview_status"] == "context_unverified"
                   for step in result["write_sequence"] if step.get("native_preview"))


@pytest.mark.parametrize("source_kind", ["member_manifest", "connection_points", "endpoint", "schema", "connection_trace"])
@pytest.mark.parametrize("field", ["session_id", "bridge_build"])
@pytest.mark.parametrize("value,expected", [
    pytest.param("other", False, id="conflict"),
    pytest.param(None, None, id="null"),
    pytest.param(17, None, id="wrong-type"),
    pytest.param(MISSING, None, id="missing"),
])
def test_matching_inner_identity_cannot_replace_missing_or_conflicting_native_envelope(source_kind, field, value, expected):
    def mutate(kind, reply):
        if kind == source_kind:
            inner = reply["result"].setdefault("context", {"before": source_context(), "after": source_context()})
            for boundary in ("before", "after"):
                inner[boundary][field] = "session" if field == "session_id" else "build"
            replace_evidence(reply, field, value)

    response, _ = observed_plan(args=COMPLETE_ARGS, mutate_source=mutate)
    result = response["result"]
    assert_source_downgraded(result, source_kind, expected)
    for row in source_records(result, source_kind):
        checks = row["binding"]["checks"]
        assert checks[field] is expected
        assert checks["source_context_before_" + field] is True
        assert checks["source_context_after_" + field] is True


@pytest.mark.parametrize("source_kind", ["branch_state", "connection_points"])
@pytest.mark.parametrize("boundary", ["before", "after"])
@pytest.mark.parametrize("field", ["session_id", "bridge_build"])
@pytest.mark.parametrize("value,expected", [
    pytest.param("other", False, id="conflict"),
    pytest.param(None, None, id="null"),
    pytest.param(MISSING, None, id="missing"),
])
def test_each_inner_session_boundary_remains_required_when_the_envelope_matches(source_kind, boundary, field, value, expected):
    def mutate(kind, reply):
        if kind == source_kind:
            reply.update({"session_id": "session", "bridge_build": "build"})
            for sample in reply["result"]["context"]["before"], reply["result"]["context"]["after"]:
                sample.update({"session_id": "session", "bridge_build": "build"})
            replace_evidence(reply["result"]["context"][boundary], field, value)

    response, _ = observed_plan(args=COMPLETE_ARGS, mutate_source=mutate)
    result = response["result"]
    assert_source_downgraded(result, source_kind, expected)
    for row in source_records(result, source_kind):
        checks = row["binding"]["checks"]
        assert checks[field] is True
        assert checks[f"source_context_{boundary}_{field}"] is expected


@pytest.mark.parametrize("source_kind", ["member_manifest", "branch_state", "connection_points", "endpoint", "schema"])
@pytest.mark.parametrize("value", [pytest.param(MISSING, id="missing"), None, [], "invalid"])
def test_source_after_boundary_cannot_be_omitted_or_malformed(source_kind, value):
    def mutate(kind, reply):
        if kind == source_kind:
            replace_evidence(reply["result"]["context"], "after", value)

    response, _ = observed_plan(args=COMPLETE_ARGS, mutate_source=mutate)
    result = response["result"]
    assert_source_downgraded(result, source_kind, None)
    for row in source_records(result, source_kind):
        assert row["binding"]["checks"]["project_after"] is None
        assert row["binding"]["checks"]["mdb_after"] is None
        assert row["binding"]["checks"]["current_element_after"] is None


@pytest.mark.parametrize("source_kind,layer,flag", [
    ("member_manifest", "context", "same_project_instance"),
    ("member_manifest", "context", "same_mdb_instance"),
    ("member_manifest", "context", "boundary_agreement"),
    ("branch_state", "context", "boundary_agreement"),
    *[(kind, "context", flag) for kind in ("connection_points", "schema")
      for flag in ("same_project_instance", "same_mdb_instance", "identity_unchanged", "ce_unchanged", "boundary_agreement")],
    *[(kind, "result", flag) for kind in ("connection_points", "schema", "connection_trace")
      for flag in ("identity_unchanged", "ce_unchanged")],
])
@pytest.mark.parametrize("value,expected", [
    pytest.param(False, False, id="false"),
    pytest.param(None, None, id="null"),
    pytest.param(1, None, id="non-boolean"),
    pytest.param(MISSING, None, id="missing"),
])
def test_native_consistency_flags_require_explicit_true(source_kind, layer, flag, value, expected):
    def mutate(kind, reply):
        if kind == source_kind:
            evidence = reply["result"]["context"] if layer == "context" else reply["result"]
            replace_evidence(evidence, flag, value)

    response, _ = observed_plan(args=COMPLETE_ARGS, mutate_source=mutate)
    result = response["result"]
    assert_source_downgraded(result, source_kind, expected)
    check_name = ("source_context_" if layer == "context" else "source_") + flag
    assert all(row["binding"]["checks"][check_name] is expected for row in source_records(result, source_kind))


@pytest.mark.parametrize("field", ["project_after", "mdb_after", "ce_after"])
@pytest.mark.parametrize("value,expected", [
    pytest.param("changed", False, id="conflict"),
    pytest.param(None, None, id="null"),
    pytest.param([], None, id="wrong-type"),
    pytest.param("", None, id="empty"),
    pytest.param(MISSING, None, id="missing"),
])
def test_trace_after_context_requires_all_identity_fields(field, value, expected):
    def mutate(kind, reply):
        if kind == "connection_trace":
            replace_evidence(reply["result"], field, value)

    response, _ = observed_plan(args=COMPLETE_ARGS, mutate_source=mutate)
    result = response["result"]
    assert_source_downgraded(result, "connection_trace", expected)
    assert result["sources"]["connection_trace"][0]["binding"]["checks"]["source_" + field] is expected


@pytest.mark.parametrize("source_kind,field", [
    ("member_manifest", "project"), ("branch_state", "mdb"),
    ("connection_points", "current_element"), ("schema", "current_element_type"),
])
@pytest.mark.parametrize("value", [pytest.param(MISSING, id="missing"), None, 0, {}, " "])
def test_native_context_text_cannot_be_missing_null_or_the_wrong_type(source_kind, field, value):
    def mutate(kind, reply):
        if kind == source_kind:
            replace_evidence(reply["result"]["context"]["after"], field, value)

    response, _ = observed_plan(args=COMPLETE_ARGS, mutate_source=mutate)
    assert_source_downgraded(response["result"], source_kind, None)


@pytest.mark.parametrize("source_kind", ["connection_points", "schema"])
@pytest.mark.parametrize("field", ["project_after", "mdb_after", "ce_after"])
def test_flat_native_context_conflicts_are_preserved_when_nested_context_matches(source_kind, field):
    def mutate(kind, reply):
        if kind == source_kind:
            reply["result"][field] = "changed"

    response, _ = observed_plan(args=COMPLETE_ARGS, mutate_source=mutate)
    result = response["result"]
    assert_source_downgraded(result, source_kind, False)
    for row in source_records(result, source_kind):
        assert row["binding"]["checks"]["source_" + field] is False
        assert row["binding"]["checks"]["current_element_after"] is True


def test_known_source_conflict_stays_false_when_final_context_is_unknown():
    def mutate(kind, reply):
        if kind == "schema":
            reply["bridge_build"] = "other"

    incomplete = context()
    incomplete["result"]["readiness"] = None
    response, _ = observed_plan(args=COMPLETE_ARGS, after=incomplete, mutate_source=mutate)
    result = response["result"]
    assert result["context"]["boundary_agreement"] is None
    assert_source_downgraded(result, "schema", False)


def test_plan_reads_manifest_branch_points_trace_and_schema_then_returns_null_revision_dry_runs():
    response, calls = observed_plan()
    result = response["result"]
    assert result["read_only"] is True
    assert result["context"]["boundary_agreement"] is True
    assert result["complete"] is False
    assert result["status"] == "partial_or_unknown"
    assert len(result["explicit_members"]) == 3
    assert [row["requested_refno"] for row in result["sources"]["branch_states"]] == ["=1/2"]
    assert [row["expected_type"] for row in result["sources"]["connection_points"]] == ["BRAN", "TUBI"]
    assert result["endpoints"][0]["binding"]["matches_enclosing_context"] is True
    trace_starts = [start for row in result["sources"]["connection_trace"] for start in row["starts"]]
    assert len(trace_starts) == 3
    assert {row["expected_type"] for row in result["sources"]["schemas"]} == {"PIPE", "BRAN", "TUBI"}
    assert {row["status"] for row in result["property_candidates"]} == {"candidate", "not_direct_edit_supported"}
    assert {row["observed_status"] for row in result["property_candidates"]} == {"candidate", "not_direct_edit_supported"}
    assert result["sources"]["connection_points"][0]["binding"]["matches_enclosing_context"] is True
    assert [row["status"] for row in result["name_candidates"]] == ["no_observed_collision", "unknown"]
    steps = result["write_sequence"]
    assert [row["tool"] for row in steps] == ["e3d_create_element", "e3d_create_element", "e3d_set_attributes"]
    assert all(row["revision"] is None and row["arguments"]["dry_run"] is True for row in steps)
    assert steps[1]["arguments"]["parent_refno"] == "<created_refno_from_step_1>"
    assert steps[2]["arguments"]["refno"] == "<created_refno_from_step_2>"
    assert all(call.call_count == 1 for call in calls[:2])
    assert calls[3].call_count == 1
    assert calls[5].call_count == calls[6].call_count == calls[7].call_count == 0


def test_context_drift_preserves_reads_but_downgrades_candidates_to_unknown():
    response, _ = observed_plan(after=context(session="changed"))
    result = response["result"]
    assert result["context"]["boundary_agreement"] is False
    assert result["complete"] is False
    assert result["sources"]["member_manifest"]["response"]["result"]["rows"]
    assert {row["status"] for row in result["property_candidates"]} == {"unknown"}
    assert any(error["code"] == "context_changed" for error in result["read_errors"])


def test_existing_root_sibling_name_collision_is_blocked_and_preserved_as_evidence():
    args = {**ARGS, "planned_members": [{"element_type": "BRAN", "name": "/b1"}], "endpoints": []}
    bridge = BridgeClient(SETTINGS)
    with patch("aveva_e3d_mcp.piping_route_plan.context_snapshot", side_effect=[context(), context()]), \
         patch("aveva_e3d_mcp.piping_route_plan.pipe_member_manifest", return_value=member_manifest()), \
         patch("aveva_e3d_mcp.piping_route_plan.piping_branch_state", return_value=branch_state()), \
         patch.object(BridgeClient, "connection_points", side_effect=lambda refno, _wrt, _points, **kw:
                      connection_points(refno, kw["expected_type"])), \
         patch.object(BridgeClient, "trace_connection_network", side_effect=trace_reply), \
         patch.object(BridgeClient, "element_schema", side_effect=lambda refno, names, _offset, _limit,
                      expected_type="": schema(refno, expected_type, names)):
        result = piping_route_plan(bridge, args)["result"]
    assert result["name_candidates"][0]["status"] == "blocked_existing_collision"
    assert result["planned_members"][0]["status"] == "blocked"
    assert result["name_candidates"][0]["collisions"][0]["refno"] == "=1/2"


def test_duplicate_planned_sibling_names_fail_before_native_reads():
    args = {**ARGS, "planned_members": [
        {"element_type": "BRAN", "name": "/B2"},
        {"element_type": "BRAN", "name": "/b2"},
    ]}
    bridge = BridgeClient(SETTINGS)
    with patch("aveva_e3d_mcp.piping_route_plan.context_snapshot") as read:
        with pytest.raises(ValueError, match="duplicate.*sibling"):
            piping_route_plan(bridge, args)
    read.assert_not_called()


def test_wrong_connection_point_identity_downgrades_source_binding():
    bridge = BridgeClient(SETTINGS)

    def wrong_connection(refno, _wrt, _numbers, **kwargs):
        response = connection_points(refno, kwargs["expected_type"])
        if refno == "=1/3":
            response["result"]["element"] = {"refno": "=1/999", "type": "ELBO"}
        return response

    with patch("aveva_e3d_mcp.piping_route_plan.context_snapshot", side_effect=[context(), context()]), \
         patch("aveva_e3d_mcp.piping_route_plan.pipe_member_manifest", return_value=member_manifest()), \
         patch("aveva_e3d_mcp.piping_route_plan.piping_branch_state", return_value=branch_state()), \
         patch.object(BridgeClient, "connection_points", side_effect=wrong_connection), \
         patch.object(BridgeClient, "trace_connection_network", side_effect=trace_reply), \
         patch.object(BridgeClient, "element_schema", side_effect=lambda refno, names, _offset, _limit,
                      expected_type="": schema(refno, expected_type, names)):
        result = piping_route_plan(bridge, ARGS)["result"]
    bad = next(row for row in result["sources"]["connection_points"] if row["requested_refno"] == "=1/3")
    assert bad["binding"]["checks"]["requested_refno"] is False
    assert bad["binding"]["checks"]["requested_type"] is False
    assert bad["binding"]["matches_enclosing_context"] is False
    assert result["complete"] is False
    assert all(row["status"] == "unknown" for row in result["property_candidates"])


def test_wrong_schema_reference_downgrades_type_and_attribute_candidates():
    bridge = BridgeClient(SETTINGS)

    def wrong_schema(refno, names, _offset, _limit, expected_type=""):
        response = schema(refno, expected_type, names)
        if expected_type == "TUBI":
            response["result"]["element"]["refno"] = "=1/999"
        return response

    with patch("aveva_e3d_mcp.piping_route_plan.context_snapshot", side_effect=[context(), context()]), \
         patch("aveva_e3d_mcp.piping_route_plan.pipe_member_manifest", return_value=member_manifest()), \
         patch("aveva_e3d_mcp.piping_route_plan.piping_branch_state", return_value=branch_state()), \
         patch.object(BridgeClient, "connection_points", side_effect=lambda refno, _wrt, _numbers, **kw:
                      connection_points(refno, kw["expected_type"])), \
         patch.object(BridgeClient, "trace_connection_network", side_effect=trace_reply), \
         patch.object(BridgeClient, "element_schema", side_effect=wrong_schema):
        result = piping_route_plan(bridge, ARGS)["result"]
    bad = next(row for row in result["sources"]["schemas"] if row["expected_type"] == "TUBI")
    assert bad["binding"]["checks"]["requested_refno"] is False
    assert bad["binding"]["matches_enclosing_context"] is False
    assert result["complete"] is False
    assert all(row["status"] == "unknown" for row in result["property_candidates"])
    assert all(row["status"] == "unknown" for row in result["type_candidates"])


def test_unverified_initial_context_stops_before_model_reads():
    bridge = BridgeClient(SETTINGS)
    incomplete = {"ok": True, "version": "E3D21", "result": {"context_complete": False}}
    with patch("aveva_e3d_mcp.piping_route_plan.context_snapshot", return_value=incomplete), \
         patch("aveva_e3d_mcp.piping_route_plan.pipe_member_manifest") as manifest, \
         patch.object(BridgeClient, "connection_points") as points, \
         patch.object(BridgeClient, "element_schema") as read_schema:
        response = piping_route_plan(bridge, ARGS)
    assert response["result"]["complete"] is False
    assert response["result"]["context"]["boundary_agreement"] is None
    manifest.assert_not_called()
    points.assert_not_called()
    read_schema.assert_not_called()


def test_write_sequence_parent_placeholders_follow_create_steps_after_interleaved_previews():
    members = _member_specs([
        {"element_type": "BRAN", "name": "/B1"},
        {"element_type": "TUBI", "name": "/T1", "parent_index": 0,
         "set_attributes": {"DIAM": "80"}},
        {"element_type": "ELBO", "name": "/E1", "parent_index": 1},
    ], "=1/1")
    steps = _write_sequence("=1/1", members)
    assert [step["sequence"] for step in steps] == [1, 2, 3, 4]
    assert steps[2]["arguments"]["refno"] == "<created_refno_from_step_2>"
    assert steps[3]["arguments"]["parent_refno"] == "<created_refno_from_step_2>"
    assert all(step["revision"] is step["expected_revision"] is None for step in steps)


@pytest.mark.parametrize("args", [
    {},
    {**ARGS, "root_refno": "CE"},
    {**ARGS, "planned_members": []},
    {**ARGS, "planned_members": [{"element_type": "TUBI", "name": "/T1", "parent_index": 0}]},
    {**ARGS, "planned_members": [{"element_type": "TUBI", "name": "/T1", "set_attributes": {"SPREF": "x"}}]},
    {**ARGS, "planned_members": [{"element_type": "TUBI", "name": "/T1", "set_attributes": {"NAME": "/RENAMED"}}]},
    {**ARGS, "planned_members": [
        {"element_type": "TUBI", "name": "/T1"},
        {"element_type": "ELBO", "name": "/T1"},
    ]},
    {**ARGS, "endpoints": [{"refno": "=1/9", "expected_type": "NOZZ", "point_number": True}]},
    {**ARGS, "include_native_previews": 1},
    {**ARGS, "unexpected": True},
])
def test_invalid_plan_inputs_fail_before_context_reads(args):
    bridge = BridgeClient(SETTINGS)
    with patch("aveva_e3d_mcp.piping_route_plan.context_snapshot") as read:
        with pytest.raises(ValueError):
            piping_route_plan(bridge, args)
    read.assert_not_called()


def test_tool_definition_is_bounded_read_only_and_contains_no_execution_switch():
    server = McpServer("route-plan", "0")
    register_piping_route_plan(server, BridgeClient(SETTINGS))
    tool = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"][0]
    assert tool["name"] == TOOL
    assert tool["annotations"]["readOnlyHint"] is True
    assert set(tool["inputSchema"]["properties"]) == {"root_refno", "planned_members", "endpoints", "max_depth", "member_limit", "include_native_previews"}
    assert "dry_run" not in tool["inputSchema"]["properties"]
    assert "expected_revision" not in tool["inputSchema"]["properties"]
    assert tool["inputSchema"]["properties"]["include_native_previews"]["default"] is False
    assert tool["inputSchema"]["additionalProperties"] is False


def test_automatic_point_overflow_is_reported_and_incomplete():
    many_points = connection_points("=1/2", "BRAN")
    many_points["result"]["points"] = [{"number": index, "available": True}
                                        for index in range(1, 66)]
    bridge = BridgeClient(SETTINGS)
    with patch("aveva_e3d_mcp.piping_route_plan.context_snapshot", side_effect=[context(), context()]), \
         patch("aveva_e3d_mcp.piping_route_plan.pipe_member_manifest", return_value=member_manifest()), \
         patch("aveva_e3d_mcp.piping_route_plan.piping_branch_state", return_value=branch_state()), \
         patch.object(BridgeClient, "connection_points", side_effect=lambda refno, _wrt, _points, **kw:
                      many_points if refno == "=1/2" else connection_points(refno, kw["expected_type"])), \
         patch.object(BridgeClient, "trace_connection_network", side_effect=trace_reply), \
         patch.object(BridgeClient, "element_schema", side_effect=lambda refno, names, _offset, _limit,
                      expected_type="": schema(refno, expected_type, names)):
        result = piping_route_plan(bridge, ARGS)["result"]
    assert result["trace_scope"] == {"limit": 64, "omitted_automatic_starts": 2, "complete": False}
    assert result["complete"] is False
    assert any(item["code"] == "trace_start_limit" for item in result["read_errors"])
