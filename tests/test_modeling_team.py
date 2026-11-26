from __future__ import annotations

from copy import deepcopy
import threading
import time
from unittest.mock import patch

import pytest

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.modeling_team import ModelingTeamCoordinator, _normalize, register_modeling_team
from aveva_e3d_mcp.protocol import McpServer, _schema_matches


SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=True)
WORKFLOW_ID = "12345678-1234-4234-8234-123456789abc"
CONTEXT = {"version": "E3D21", "session_id": "session", "bridge_build": "build",
           "project": "PROJECT", "mdb": "ALL", "current_element": "=1/10"}


def route_task(task_id="route", sequence=10, *, parent="=1/1", name="/PIPE"):
    return {"task_id": task_id, "agent_id": "route-agent", "sequence": sequence,
            "operation": "piping_route", "arguments": {"parent_refno": parent, "pipe_name": name}}


def transform_task(task_id="move", sequence=20, *, refno="=1/2", target_task_id=None):
    arguments = {"position": [10, 20, 30], "wrt": "WORLD"}
    if target_task_id is None:
        arguments["refno"] = refno
    row = {"task_id": task_id, "agent_id": "equipment-agent", "sequence": sequence,
           "operation": "element_transform", "arguments": arguments}
    if target_task_id is not None:
        row["target_task_id"] = target_task_id
    return row


def create_task(task_id="body", sequence=10, *, parent="=1/2", name="/BODY", parent_task_id=None,
                depends_on=None, agent_id="equipment-agent"):
    args = {"element_type": "BOX", "name": name}
    if parent_task_id is None:
        args["parent_refno"] = parent
    row = {"task_id": task_id, "agent_id": agent_id, "sequence": sequence,
           "operation": "equipment_create", "arguments": args}
    if parent_task_id is not None:
        row["parent_task_id"] = parent_task_id
    if depends_on:
        row["depends_on"] = depends_on
    return row


def bundle(*tasks):
    return {"workflow_id": WORKFLOW_ID, "tasks": list(tasks)}


def response(result, *, operation="create", dry_run=True):
    if operation == "route":
        return {"ok": True, "version": "E3D21", "result": {
            "success": True, "status": "prepared" if dry_run else "created_verified",
            "revision": "a" * 64, "readback_verified": not dry_run,
            "created": [] if dry_run else [{"refno": result["refno"], "type": "PIPE"}],
            "readback": {"context": {"current_element": result.get("ce_after", "=1/10")}}}}
    if dry_run:
        return {"ok": True, "version": "E3D21", "result": {"success": True, "dry_run": True,
            "revision": result.get("revision", "b" * 64), "ce_unchanged": True}}
    return {"ok": True, "version": "E3D21", "result": {"success": result.get("success", True),
        "element": {"refno": result["refno"]}, "parent_membership_verified": True, "ce_unchanged": True,
        "ce_after": result.get("ce_after", "=1/10"), "captured_copy_identity_verified": True,
        "geometry_verified": True, **result.get("fields", {})}}


def fake_server(*, route=None, create=None, copy=None, transform=None):
    server = McpServer("team", "0")
    server._tools["e3d_piping_route_create"] = ({"inputSchema": {}}, route or (lambda args: response(
        {"refno": "=1/101", "ce_after": "=1/101"}, operation="route", dry_run=args["dry_run"])))
    server._tools["e3d_create_element"] = ({"inputSchema": {}}, create or (lambda args: response(
        {"refno": "=1/201"}, dry_run=args["dry_run"])))
    server._tools["e3d_copy_element"] = ({"inputSchema": {}}, copy or (lambda args: response(
        {"refno": "=1/301"}, dry_run=args["dry_run"])))
    server._tools["e3d_set_transform"] = ({"inputSchema": {}}, transform or (lambda args: {
        "ok": True, "version": "E3D21", "result": {
            "success": True, "dry_run": args["dry_run"], "revision": "c" * 64,
            "ce_unchanged": True, "revision_conflict": False, "write_attempted": not args["dry_run"],
            "write_applied": not args["dry_run"], "readback_verified": not args["dry_run"],
            "refno": args["refno"], "element": {"refno": args["refno"]}, "ce_after": "=1/10",
            "notification_update": {"attempted": not args["dry_run"], "completed": not args["dry_run"],
                                     "readback_verified": not args["dry_run"],
                                     "frame_identity_unchanged": True}}}))
    return server


def coordinator(server=None, settings=SETTINGS):
    instance = ModelingTeamCoordinator(server or fake_server(), BridgeClient(settings), settings)
    return instance


def test_multi_agent_plan_previews_independent_jobs_and_defers_new_parent_children():
    calls = []
    server = fake_server(create=lambda args: calls.append((args["dry_run"], args.get("parent_refno"), args["name"]))
                          or response({"refno": "=1/99"}, dry_run=args["dry_run"]))
    instance = coordinator(server)
    request = bundle(create_task("parent", 10, name="/NEW"),
                     create_task("child", 20, parent_task_id="parent", name="/CHILD", depends_on=["parent"], agent_id="second-agent"))
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        result = instance.plan(request)["result"]
    assert result["success"] is True and result["agent_count"] == 2
    assert [row["task_id"] for row in result["ordered_tasks"]] == ["parent", "child"]
    assert result["ordered_tasks"][0]["status"] == "ready"
    assert result["ordered_tasks"][1]["status"] == "deferred_dependency_preview"
    assert calls == [(True, "=1/2", "/NEW")]
    assert result["read_only"] is True and result["synchronous_native_writes"] is False


def test_transform_tasks_use_a_fresh_native_revision_immediately_before_the_guarded_write():
    revisions = iter(["a" * 64, "b" * 64])
    calls = []
    def transform(args):
        calls.append(deepcopy(args))
        dry_run = args["dry_run"]
        result = {"success": True, "dry_run": dry_run, "revision": next(revisions) if dry_run else args["expected_revision"],
            "revision_conflict": False, "ce_unchanged": True, "refno": "=1/2", "element": {"refno": "=1/2"},
            "ce_after": "=1/10", "write_attempted": not dry_run, "write_applied": not dry_run,
            "readback_verified": not dry_run, "notification_update": {
                "attempted": not dry_run, "completed": not dry_run,
                "readback_verified": not dry_run, "frame_identity_unchanged": True}}
        return {"ok": True, "version": "E3D21", "result": result}
    instance = coordinator(fake_server(transform=transform))
    request = bundle(transform_task())
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
        assert plan["ordered_tasks"][0]["native_preview"]["result"]["revision"] == "a" * 64
        result = instance.execute({**request, "expected_session_id": "session", "expected_bridge_build": "build",
            "expected_workflow_revision": plan["workflow_revision"]})["result"]
    assert result["status"] == "completed" and result["completed_count"] == 1
    assert result["task_receipts"][0]["status"] == "transform_verified"
    assert [call["dry_run"] for call in calls] == [True, True, False]
    assert calls[-1]["expected_revision"] == "b" * 64
    assert calls[-1]["refno"] == "=1/2"


def test_transform_task_can_target_a_verified_output_from_an_earlier_creation_task():
    transforms = []
    def transform(args):
        transforms.append(deepcopy(args))
        dry_run = args["dry_run"]
        refno = args["refno"]
        return {"ok": True, "version": "E3D21", "result": {
            "success": True, "dry_run": dry_run, "revision": "d" * 64,
            "revision_conflict": False, "ce_unchanged": True, "refno": refno, "element": {"refno": refno},
            "ce_after": "=1/10", "write_attempted": not dry_run, "write_applied": not dry_run,
            "readback_verified": not dry_run, "notification_update": {
                "attempted": not dry_run, "completed": not dry_run,
                "readback_verified": not dry_run, "frame_identity_unchanged": True}}}
    instance = coordinator(fake_server(transform=transform))
    request = bundle(create_task("base", 10, name="/BASE"), transform_task("place", 20, target_task_id="base"))
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
        assert plan["ordered_tasks"][1]["status"] == "deferred_dependency_preview"
        result = instance.execute({**request, "expected_session_id": "session", "expected_bridge_build": "build",
            "expected_workflow_revision": plan["workflow_revision"]})["result"]
    assert result["status"] == "completed"
    assert transforms[0]["refno"] == transforms[1]["refno"] == "=1/201"
    assert transforms[0]["dry_run"] is True and transforms[1]["dry_run"] is False
    assert result["task_receipts"][1]["status"] == "transform_verified"


def test_dependency_order_is_stable_even_when_agent_results_arrive_in_reverse_order():
    request = bundle(create_task("child", 20, parent_task_id="base", name="/CHILD", depends_on=["base"]),
                     create_task("base", 10, name="/BASE"))
    workflow, ordered, conflicts = _normalize(request)
    assert workflow == WORKFLOW_ID and conflicts == []
    assert [row["task_id"] for row in ordered] == ["base", "child"]


def test_cycles_unknown_dependencies_and_duplicate_sibling_names_are_rejected_or_reported():
    with pytest.raises(ValueError, match="cycle"):
        _normalize(bundle({**create_task("a"), "depends_on": ["b"]},
                          {**create_task("b", 20), "depends_on": ["a"]}))
    with pytest.raises(ValueError, match="unknown task"):
        _normalize(bundle({**create_task("a"), "depends_on": ["missing"]}))
    with pytest.raises(ValueError, match="sequence values must be unique"):
        _normalize(bundle(create_task("a", 10), create_task("b", 10, parent="=1/3", name="/B")))
    with pytest.raises(ValueError, match="caller-generated revisions"):
        _normalize(bundle({**transform_task(), "arguments": {"refno": "=1/2", "position": [1, 2, 3],
                                                           "expected_revision": "a" * 64}}))
    with pytest.raises(ValueError, match="target_task_id is only supported"):
        _normalize(bundle({**create_task(), "target_task_id": "other"}))
    instance = coordinator()
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        result = instance.plan(bundle(create_task("one", 1, name="/DUP"),
                                      create_task("two", 2, name="/dup", agent_id="other")))["result"]
    assert result["success"] is False
    assert result["conflicts"][0]["code"] == "duplicate_sibling_name"
    assert all(row["status"] == "conflict" for row in result["ordered_tasks"])


def test_workflow_revision_binds_agent_task_arguments_and_expected_context():
    base = bundle(create_task())
    instance = coordinator()
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        first = instance.plan(base)["result"]["workflow_revision"]
        changed = deepcopy(base)
        changed["tasks"][0]["arguments"]["name"] = "/OTHER"
        second = instance.plan(changed)["result"]["workflow_revision"]
        context = {**CONTEXT, "current_element": "=1/11"}
        third = instance.plan(base)["result"]["workflow_revision"]
    assert first != second and first == third
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=context):
        fourth = instance.plan(base)["result"]["workflow_revision"]
    assert first != fourth


def test_execution_repreviews_each_shared_parent_task_and_resolves_verified_parent_ref():
    events = []
    preview_index = 0
    def create(args):
        nonlocal preview_index
        if args["dry_run"]:
            preview_index += 1
        revision = format(preview_index, "064x")
        events.append(("create", args["dry_run"], args["parent_refno"], args["name"], args.get("expected_revision")))
        result = response({"refno": "=1/201" if args["name"] == "/BASE" else "=1/202"}, dry_run=args["dry_run"])
        if args["dry_run"]:
            result["result"]["revision"] = revision
        return result
    server = fake_server(create=create)
    instance = coordinator(server)
    request = bundle(create_task("base", 10, parent="=1/2", name="/BASE"),
                     create_task("child", 20, parent_task_id="base", name="/CHILD", depends_on=["base"]))
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    events.clear()
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        result = instance.execute({**request, "expected_session_id": CONTEXT["session_id"],
            "expected_bridge_build": CONTEXT["bridge_build"], "expected_workflow_revision": plan["workflow_revision"]})["result"]
    assert result["status"] == "completed" and result["completed_count"] == 2
    assert [event[0:2] for event in events] == [("create", True), ("create", False), ("create", True), ("create", False)]
    assert events[0][2:4] == ("=1/2", "/BASE") and events[2][2:4] == ("=1/201", "/CHILD")
    assert events[1][4] == "0" * 63 + "2" and events[3][4] == "0" * 63 + "3"
    assert result["task_receipts"][1]["output_refno"] == "=1/202"


def test_execution_serializes_multi_agent_route_and_equipment_writes_and_tracks_ce():
    events = []
    def route(args):
        events.append(("route", args["dry_run"]))
        return response({"refno": "=1/100", "ce_after": "=1/100"}, operation="route", dry_run=args["dry_run"])
    def create(args):
        events.append(("create", args["dry_run"], args["parent_refno"]))
        return response({"refno": "=1/200", "ce_after": "=1/100"}, dry_run=args["dry_run"])
    server = fake_server(route=route, create=create)
    instance = coordinator(server)
    request = bundle(route_task(sequence=10), create_task("body", 20))
    snapshots = [CONTEXT, CONTEXT, CONTEXT, {**CONTEXT, "current_element": "=1/100"},
                 {**CONTEXT, "current_element": "=1/100"}, {**CONTEXT, "current_element": "=1/100"},
                 {**CONTEXT, "current_element": "=1/100"}]
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", side_effect=snapshots):
        plan = instance.plan(request)["result"]
    events.clear()
    # Planning is complete; execution now begins in the same selected context.
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", side_effect=[CONTEXT, CONTEXT,
            {**CONTEXT, "current_element": "=1/100"}, {**CONTEXT, "current_element": "=1/100"},
            {**CONTEXT, "current_element": "=1/100"}, {**CONTEXT, "current_element": "=1/100"}]):
        result = instance.execute({**request, "expected_session_id": CONTEXT["session_id"],
            "expected_bridge_build": CONTEXT["bridge_build"], "expected_workflow_revision": plan["workflow_revision"]})["result"]
    assert result["status"] == "completed", result
    assert [item[0] for item in events] == ["route", "route", "create", "create"]


@pytest.mark.parametrize("failure,expected_status", [("known", "partial_failure"), ("uncertain", "stopped_uncertain")])
def test_first_failed_or_uncertain_task_stops_later_agent_writes_and_retains_receipts(failure, expected_status):
    calls = []
    def create(args):
        calls.append((args["name"], args["dry_run"]))
        if args["name"] == "/BAD" and not args["dry_run"]:
            native = {"success": False, "creation_attempted": True, "outcome_uncertain": failure == "uncertain",
                      "creation_result_unknown": failure == "uncertain"}
            return {"ok": True, "version": "E3D21", "result": native}
        return response({"refno": "=1/201"}, dry_run=args["dry_run"])
    instance = coordinator(fake_server(create=create))
    request = bundle(create_task("first", 1, name="/FIRST"), create_task("bad", 2, name="/BAD"),
                     create_task("last", 3, name="/LAST", agent_id="third-agent"))
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    calls.clear()
    snapshots = [CONTEXT] * 6
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", side_effect=snapshots):
        result = instance.execute({**request, "expected_session_id": CONTEXT["session_id"],
            "expected_bridge_build": CONTEXT["bridge_build"], "expected_workflow_revision": plan["workflow_revision"]})["result"]
    assert result["status"] == expected_status
    assert [row["status"] for row in result["task_receipts"]] == ["created_verified", expected_status.replace("partial_failure", "failed").replace("stopped_uncertain", "outcome_uncertain"), "not_started"]
    assert not any(name == "/LAST" for name, _ in calls)


def test_wrong_workflow_revision_or_session_never_submits_a_native_write():
    calls = []
    server = fake_server(create=lambda args: calls.append(args) or response({"refno": "=1/201"}, dry_run=args["dry_run"]))
    instance = coordinator(server)
    request = bundle(create_task())
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
        second_instance = coordinator(fake_server(create=server._tools["e3d_create_element"][1]))
        mismatch = instance.execute({**request, "expected_session_id": "other", "expected_bridge_build": "build",
                                     "expected_workflow_revision": plan["workflow_revision"]})["result"]
        wrong = second_instance.execute({**request, "expected_session_id": "session", "expected_bridge_build": "build",
                                  "expected_workflow_revision": "0" * 64})["result"]
    assert mismatch["status"] == wrong["status"] == "preflight_rejected"
    assert all(row["dry_run"] for row in calls)


def test_duplicate_completed_workflow_returns_original_without_native_replay():
    calls = []
    instance = coordinator(fake_server(create=lambda args: calls.append(args) or response({"refno": "=1/201"}, dry_run=args["dry_run"])))
    request = bundle(create_task())
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    calls.clear()
    execution = {**request, "expected_session_id": "session", "expected_bridge_build": "build",
                 "expected_workflow_revision": plan["workflow_revision"]}
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        first = instance.execute(execution)
        second = instance.execute(execution)
    assert first["result"]["status"] == "completed"
    assert second["duplicate_workflow"] is True
    assert len(calls) == 2


def test_context_failure_after_a_verified_write_preserves_the_completed_prefix():
    instance = coordinator()
    request = bundle(create_task())
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", side_effect=[CONTEXT, CONTEXT, CONTEXT,
             RuntimeError("late context observation failed")]):
        result = instance.execute({**request, "expected_session_id": "session", "expected_bridge_build": "build",
            "expected_workflow_revision": plan["workflow_revision"]})["result"]
    assert result["status"] == "workflow_context_unverified"
    assert result["execution_attempted"] is True
    assert result["task_receipts"][0]["status"] == "created_verified"
    assert result["task_receipts"][0]["output_refno"] == "=1/201"
    assert result["retry_safe"] is False
    assert result["task_receipts"][0]["native_write_state"] == "verified"
    assert result["final_context_matches_expected"] is None


@pytest.mark.parametrize("field", ["version", "session_id", "bridge_build", "project", "mdb", "current_element"])
@pytest.mark.parametrize("operation", ["equipment_create", "equipment_copy", "element_transform"])
def test_final_context_drift_preserves_verified_writes_without_reporting_workflow_success(field, operation):
    task = transform_task() if operation == "element_transform" else create_task()
    task["operation"] = operation
    if operation == "equipment_copy":
        task["arguments"] = {"source_refno": "=1/2", "parent_refno": "=1/3", "name": "/COPY"}
    server = fake_server()
    native_name = {"equipment_create": "e3d_create_element", "equipment_copy": "e3d_copy_element",
                   "element_transform": "e3d_set_transform"}[operation]
    definition, handler = server._tools[native_name]
    calls = []
    server._tools[native_name] = (definition, lambda args: calls.append(args["dry_run"]) or handler(args))
    instance = coordinator(server)
    request = bundle(task)
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    execution = {**request, "expected_session_id": "session", "expected_bridge_build": "build",
                 "expected_workflow_revision": plan["workflow_revision"]}
    changed = {**CONTEXT, field: "=1/999" if field == "current_element" else "CHANGED"}
    with patch("aveva_e3d_mcp.modeling_team._workflow_context",
               side_effect=[CONTEXT, CONTEXT, CONTEXT, changed]):
        envelope = instance.execute(execution)
    result = envelope["result"]
    receipt = result["task_receipts"][0]
    assert envelope["ok"] is False and result["success"] is False
    assert result["status"] == "workflow_context_changed"
    assert result["final_context_matches_expected"] is False
    assert result["final_context"] == changed and result["final_context_error"] is None
    assert result["completed_count"] == 1 and result["failed_count"] == 0
    assert result["retry_safe"] is False and result["native_write_state"] == "verified"
    assert receipt["status"] == ("transform_verified" if operation == "element_transform" else "created_verified")
    assert receipt["native_receipt"]["result"]["success"] is True
    assert receipt["output_refno"] == {"equipment_create": "=1/201", "equipment_copy": "=1/301",
                                       "element_transform": "=1/2"}[operation]
    calls_before_duplicate = list(calls)
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", side_effect=AssertionError("No replay")):
        duplicate = instance.execute(execution)
        status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
    assert duplicate["duplicate_workflow"] is True and duplicate["result"] == result
    assert status["workflow_status"] == "workflow_context_changed"
    assert status["next_action"] == "verify_context_before_continuing"
    assert calls == calls_before_duplicate and calls.count(False) == 1


@pytest.mark.parametrize("final_ce,expected_status", [("=1/101", "completed"),
                                                     (CONTEXT["current_element"], "workflow_context_changed")])
def test_final_context_uses_the_route_verified_ce_instead_of_the_initial_ce(final_ce, expected_status):
    instance = coordinator()
    request = bundle(route_task())
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    route_context = {**CONTEXT, "current_element": "=1/101"}
    final_context = {**CONTEXT, "current_element": final_ce}
    with patch("aveva_e3d_mcp.modeling_team._workflow_context",
               side_effect=[CONTEXT, CONTEXT, route_context, final_context]):
        envelope = instance.execute({**request, "expected_session_id": "session", "expected_bridge_build": "build",
            "expected_workflow_revision": plan["workflow_revision"]})
    result = envelope["result"]
    assert result["status"] == expected_status
    assert envelope["ok"] is result["success"] is (expected_status == "completed")
    assert result["final_context_matches_expected"] is (expected_status == "completed")
    assert result["task_receipts"][0]["output_refno"] == "=1/101"
    assert result["task_receipts"][0]["status"] == "created_verified"


@pytest.mark.parametrize("uncertain,expected_status", [(False, "partial_failure"), (True, "stopped_uncertain")])
def test_final_context_drift_does_not_hide_an_earlier_native_failure(uncertain, expected_status):
    def create(args):
        return response({"refno": "=1/201"}, dry_run=True) if args["dry_run"] else {
            "ok": True, "version": "E3D21", "result": {
                "success": False, "creation_attempted": True, "outcome_uncertain": uncertain}}
    instance = coordinator(fake_server(create=create))
    request = bundle(create_task())
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    with patch("aveva_e3d_mcp.modeling_team._workflow_context",
               side_effect=[CONTEXT, CONTEXT, {**CONTEXT, "mdb": "CHANGED"}]):
        result = instance.execute({**request, "expected_session_id": "session", "expected_bridge_build": "build",
            "expected_workflow_revision": plan["workflow_revision"]})["result"]
    assert result["status"] == expected_status and result["success"] is False
    assert result["final_context_matches_expected"] is False
    assert result["task_receipts"][0]["status"] == ("outcome_uncertain" if uncertain else "failed")
    assert result["failed_count"] == 1 and result["completed_count"] == 0


@pytest.mark.parametrize("operation", ["equipment_create", "element_transform"])
def test_post_write_context_read_failure_is_unverified_not_changed(operation):
    if operation == "element_transform":
        task = transform_task()
        expected_status = "transform_verified_context_unverified"
    else:
        task = create_task()
        expected_status = "created_verified_context_unverified"
    server = fake_server()
    native_name = "e3d_set_transform" if operation == "element_transform" else "e3d_create_element"
    definition, native_handler = server._tools[native_name]
    calls = []
    server._tools[native_name] = (definition, lambda args: calls.append(args["dry_run"]) or native_handler(args))
    instance = coordinator(server)
    request = bundle(task)
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    with patch("aveva_e3d_mcp.modeling_team._workflow_context",
               side_effect=[CONTEXT, CONTEXT, RuntimeError("post-write context read failed"), CONTEXT]):
        result = instance.execute({**request, "expected_session_id": "session", "expected_bridge_build": "build",
            "expected_workflow_revision": plan["workflow_revision"]})["result"]

    receipt = result["task_receipts"][0]
    assert result["status"] == "workflow_context_unverified"
    assert result["execution_attempted"] is True and result["retry_safe"] is False
    assert receipt["status"] == expected_status
    assert receipt["native_write_state"] == "verified_context_unverified"
    expected_refno = "=1/2" if operation == "element_transform" else "=1/201"
    assert receipt["output_refno"] == expected_refno
    assert receipt["native_receipt"]["result"]["success"] is True
    assert result["native_write_state"] == "verified_context_unverified"
    assert calls.count(False) == 1


def test_context_drift_after_a_verified_write_counts_the_task_as_a_workflow_failure():
    instance = coordinator()
    request = bundle(create_task())
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    changed = {**CONTEXT, "current_element": "=1/999"}
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", side_effect=[CONTEXT, CONTEXT, changed, changed]):
        result = instance.execute({**request, "expected_session_id": "session", "expected_bridge_build": "build",
            "expected_workflow_revision": plan["workflow_revision"]})["result"]
    assert result["status"] == "partial_failure" and result["failed_count"] == 1
    assert result["task_receipts"][0]["status"] == "created_verified_context_changed"
    assert result["task_receipts"][0]["native_write_state"] == "verified_context_changed"


def test_missing_write_receipt_is_reported_as_uncertain_after_dispatch():
    calls = []
    def create(args):
        calls.append(args["dry_run"])
        return response({"refno": "=1/201"}, dry_run=True) if args["dry_run"] else None
    instance = coordinator(fake_server(create=create))
    request = bundle(create_task())
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", side_effect=[CONTEXT, CONTEXT, CONTEXT]):
        result = instance.execute({**request, "expected_session_id": "session", "expected_bridge_build": "build",
            "expected_workflow_revision": plan["workflow_revision"]})["result"]
    assert calls.count(False) == 1
    assert result["status"] == "stopped_uncertain" and result["retry_safe"] is False
    assert result["task_receipts"][0]["native_write_state"] == "outcome_uncertain"


def test_interruption_after_write_dispatch_leaves_an_uncertain_terminal_status():
    class WorkflowInterrupted(BaseException):
        pass
    def create(args):
        if not args["dry_run"]:
            raise WorkflowInterrupted()
        return response({"refno": "=1/201"}, dry_run=True)
    instance = coordinator(fake_server(create=create))
    request = bundle(create_task())
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    execution = {**request, "expected_session_id": "session", "expected_bridge_build": "build",
                 "expected_workflow_revision": plan["workflow_revision"]}
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", side_effect=[CONTEXT, CONTEXT]):
        with pytest.raises(WorkflowInterrupted):
            instance.execute(execution)
    status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
    assert status["workflow_status"] == "workflow_uncertain" and status["workflow_in_progress"] is False
    assert status["task_receipts"][0]["status"] == "outcome_uncertain"
    assert status["task_receipts"][0]["native_write_state"] == "outcome_uncertain"


def test_interruption_during_post_write_context_check_preserves_verified_ref_and_receipt():
    class WorkflowInterrupted(BaseException):
        pass
    instance = coordinator()
    request = bundle(create_task())
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    execution = {**request, "expected_session_id": "session", "expected_bridge_build": "build",
                 "expected_workflow_revision": plan["workflow_revision"]}
    # Initial context, pre-task context, then cancellation after the successful
    # native receipt and immediate create/copy readback have produced a REF.
    with patch("aveva_e3d_mcp.modeling_team._workflow_context",
               side_effect=[CONTEXT, CONTEXT, WorkflowInterrupted()]):
        with pytest.raises(WorkflowInterrupted):
            instance.execute(execution)
    status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
    receipt = status["task_receipts"][0]
    assert status["workflow_status"] == "workflow_context_unverified"
    assert status["any_write_tool_call_dispatched"] is True
    assert status["native_write_state"] == "verified_context_unverified"
    assert receipt["status"] == "created_verified_context_unverified"
    assert receipt["output_refno"] == "=1/201"
    assert receipt["native_receipt"]["result"]["element"]["refno"] == "=1/201"
    assert receipt["retry_safe"] is False


def test_interruption_between_tasks_is_partial_and_marks_remaining_tasks_not_started():
    class WorkflowInterrupted(BaseException):
        pass
    calls = []
    instance = coordinator(fake_server(create=lambda args: calls.append((args["name"], args["dry_run"]))
        or response({"refno": "=1/201" if args["name"] == "/FIRST" else "=1/202"}, dry_run=args["dry_run"])))
    request = bundle(create_task("first", 10, name="/FIRST"), create_task("second", 20, name="/SECOND"))
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    original_publish = instance._publish_progress
    def interrupt_between(workflow_id, revision, receipts, task_count, current_task_id, execution_attempted):
        original_publish(workflow_id, revision, receipts, task_count, current_task_id, execution_attempted)
        if current_task_id is None and receipts and receipts[-1].get("status") == "created_verified":
            raise WorkflowInterrupted()
    execution = {**request, "expected_session_id": "session", "expected_bridge_build": "build",
                 "expected_workflow_revision": plan["workflow_revision"]}
    with patch.object(instance, "_publish_progress", side_effect=interrupt_between), \
         patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        with pytest.raises(WorkflowInterrupted):
            instance.execute(execution)
    status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
    assert status["workflow_status"] == "partial_failure"
    assert [row["status"] for row in status["task_receipts"]] == ["created_verified", "not_started"]
    assert status["task_count"] == 2 and status["completed_count"] == 1 and status["failed_count"] == 0
    assert status["any_write_tool_call_dispatched"] is True
    assert status["current_task_write_tool_call_dispatched"] is None
    assert status["native_write_state"] == "verified"
    assert sum(not dry_run for _, dry_run in calls) == 1


def test_interruption_after_every_task_is_verified_does_not_report_uncertain_write():
    class WorkflowInterrupted(BaseException):
        pass
    instance = coordinator()
    request = bundle(create_task())
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    execution = {**request, "expected_session_id": "session", "expected_bridge_build": "build",
                 "expected_workflow_revision": plan["workflow_revision"]}
    # Cancel the final workflow context bracket after this task is verified.
    with patch("aveva_e3d_mcp.modeling_team._workflow_context",
               side_effect=[CONTEXT, CONTEXT, CONTEXT, WorkflowInterrupted()]):
        with pytest.raises(WorkflowInterrupted):
            instance.execute(execution)
    status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
    assert status["workflow_status"] == "workflow_context_unverified"
    assert status["task_receipts"][0]["status"] == "created_verified"
    assert status["completed_count"] == status["task_count"] == 1
    assert status["failed_count"] == 0
    assert status["native_write_state"] == "verified"
    assert status["any_write_tool_call_dispatched"] is True


def test_first_task_preview_failure_is_no_write_preflight_rejection_and_can_replan_with_new_id():
    previews = 0
    def create(args):
        nonlocal previews
        if args["dry_run"]:
            previews += 1
            if previews > 1:  # Planning succeeded; execution's fresh preview fails.
                return {"ok": True, "version": "E3D21", "result": {"success": False, "error": "preview rejected"}}
            return response({"refno": "=1/201"}, dry_run=True)
        pytest.fail("No native write should be dispatched after a failed preview")
    instance = coordinator(fake_server(create=create))
    request = bundle(create_task())
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    execution = {**request, "expected_session_id": "session", "expected_bridge_build": "build",
                 "expected_workflow_revision": plan["workflow_revision"]}
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        result = instance.execute(execution)["result"]
    assert result["status"] == "preflight_rejected"
    assert result["execution_attempted"] is False and result["write_tool_call_dispatched"] is False
    assert result["any_write_tool_call_dispatched"] is False
    assert result["task_receipts"][0]["status"] == "failed"
    assert result["task_receipts"][0]["native_write_state"] == "not_dispatched"
    assert result["task_receipts"][0]["retry_safe"] is True
    status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
    assert status["next_action"] == "correct_proposal_and_replan_with_new_workflow_id"


def test_simultaneous_duplicate_workflow_does_not_enter_the_native_writer_twice():
    preview_entered, preview_release = threading.Event(), threading.Event()
    write_entered, write_release = threading.Event(), threading.Event()
    block_second_preview = [False]
    calls = []
    def create(args):
        calls.append((args["name"], args["dry_run"]))
        if args["name"] == "/SECOND" and not args["dry_run"]:
            write_entered.set()
            write_release.wait(2)
        return response({"refno": "=1/201"}, dry_run=args["dry_run"])
    instance = coordinator(fake_server(create=create))
    request = bundle(create_task("first", 10, name="/FIRST"), create_task("second", 20, name="/SECOND"))
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    calls.clear()
    execute = {**request, "expected_session_id": "session", "expected_bridge_build": "build",
               "expected_workflow_revision": plan["workflow_revision"]}
    original_preview = instance._preview
    def gated_preview(row, refs=None):
        value = original_preview(row, refs)
        if block_second_preview[0] and row["task_id"] == "second":
            preview_entered.set()
            preview_release.wait(2)
        return value
    block_second_preview[0] = True
    first = {}
    with patch.object(instance, "_preview", side_effect=gated_preview):
      thread = threading.Thread(target=lambda: first.update(instance.execute(execute)))
      with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        thread.start()
        assert preview_entered.wait(2)
        second = instance.execute(execute)
        progress = second["result"]
        assert progress["status"] == "workflow_in_progress"
        assert progress["current_task_id"] == "second"
        assert progress["completed_count"] == 1
        assert [row["status"] for row in progress["task_receipts"]] == ["created_verified", "running"]
        assert progress["execution_attempted"] is True and progress["retry_safe"] is False
        assert progress["write_tool_call_dispatched"] is True
        assert progress["any_write_tool_call_dispatched"] is True
        assert progress["current_task_write_tool_call_dispatched"] is False
        assert progress["native_write_state"] == "not_dispatched"
        status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
        assert status["operation"] == "modeling_team_status"
        assert status["workflow_status"] == "workflow_in_progress" and status["current_task_id"] == "second"
        assert status["journal_available"] is True and status["journal_durable"] is False
        assert status["any_write_tool_call_dispatched"] is True
        assert status["current_task_write_tool_call_dispatched"] is False
        status["task_receipts"][0]["status"] = "caller_mutation"
        assert instance.status({"workflow_id": WORKFLOW_ID})["result"]["task_receipts"][0]["status"] == "created_verified"
        preview_release.set()
        assert write_entered.wait(2)
        dispatched = instance.status({"workflow_id": WORKFLOW_ID})["result"]
        assert dispatched["any_write_tool_call_dispatched"] is True
        assert dispatched["current_task_write_tool_call_dispatched"] is True
        assert dispatched["native_write_state"] == "awaiting_receipt"
        write_release.set()
        thread.join(2)
    assert second["workflow_in_progress"] is True
    assert first["result"]["status"] == "completed"
    final_status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
    assert final_status["status"] == final_status["workflow_status"] == "completed"
    assert final_status["workflow_in_progress"] is False
    assert final_status["native_write_state"] == "verified"
    assert sum(not dry_run for _, dry_run in calls) == 2


def test_progress_distinguishes_prior_dispatch_from_current_task_during_preview():
    entered, release = threading.Event(), threading.Event()
    block_second_preview = False
    def create(args):
        if block_second_preview and args["name"] == "/SECOND" and args["dry_run"]:
            entered.set()
            release.wait(2)
        refno = "=1/201" if args["name"] == "/FIRST" else "=1/202"
        return response({"refno": refno}, dry_run=args["dry_run"])
    instance = coordinator(fake_server(create=create))
    request = bundle(create_task("first", 10, name="/FIRST"), create_task("second", 20, name="/SECOND"))
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    execution = {**request, "expected_session_id": "session", "expected_bridge_build": "build",
                 "expected_workflow_revision": plan["workflow_revision"]}
    block_second_preview = True
    first = {}
    thread = threading.Thread(target=lambda: first.update(instance.execute(execution)))
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        thread.start()
        assert entered.wait(2)
        progress = instance.status({"workflow_id": WORKFLOW_ID})["result"]
        assert progress["status"] == "workflow_in_progress"
        assert progress["current_task_id"] == "second"
        assert progress["completed_count"] == 1
        assert progress["any_write_tool_call_dispatched"] is True
        assert progress["current_task_write_tool_call_dispatched"] is False
        assert progress["native_write_state"] == "not_dispatched"
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    assert first["result"]["status"] == "completed"


def test_status_reports_unknown_workflows_without_native_calls_and_rejects_extra_fields():
    instance = coordinator()
    with patch("aveva_e3d_mcp.modeling_team._workflow_context") as context:
        result = instance.status({"workflow_id": WORKFLOW_ID})["result"]
    assert result["status"] == "not_found" and result["journal_available"] is False
    assert result["workflow_state"] == "unknown"
    assert result["execution_attempted"] is None and result["retry_safe"] is False
    assert result["next_action"] == "inspect_native_model_before_resubmission"
    context.assert_not_called()
    with pytest.raises(ValueError, match="only workflow_id"):
        instance.status({"workflow_id": WORKFLOW_ID, "tasks": []})


def test_tool_discovery_and_closed_contract_include_explicit_agent_workflow_controls():
    server = McpServer("team", "0")
    register_modeling_team(server, BridgeClient(SETTINGS), SETTINGS)
    catalog = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    tools = {row["name"]: row for row in catalog}
    assert tools["e3d_modeling_team_plan"]["annotations"]["readOnlyHint"] is False
    assert tools["e3d_modeling_team_submit"]["annotations"]["readOnlyHint"] is False
    assert tools["e3d_modeling_team_execute"]["annotations"]["readOnlyHint"] is False
    assert tools["e3d_modeling_team_status"]["annotations"]["readOnlyHint"] is True
    task_schema = tools["e3d_modeling_team_plan"]["inputSchema"]["properties"]["tasks"]["items"]
    assert "element_transform" in task_schema["properties"]["operation"]["enum"]
    assert "target_task_id" in task_schema["properties"]
    valid = bundle(route_task())
    assert _schema_matches(valid, tools["e3d_modeling_team_plan"]["inputSchema"]) is True
    assert _schema_matches(bundle(transform_task()), tools["e3d_modeling_team_plan"]["inputSchema"]) is True
    assert _schema_matches({**valid, "unexpected": True}, tools["e3d_modeling_team_plan"]["inputSchema"]) is False
    status = {"workflow_id": WORKFLOW_ID}
    assert _schema_matches(status, tools["e3d_modeling_team_status"]["inputSchema"]) is True
    assert _schema_matches({**status, "tasks": []}, tools["e3d_modeling_team_status"]["inputSchema"]) is False
