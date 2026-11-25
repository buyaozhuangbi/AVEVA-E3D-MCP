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


def fake_server(*, route=None, create=None, copy=None):
    server = McpServer("team", "0")
    server._tools["e3d_piping_route_create"] = ({"inputSchema": {}}, route or (lambda args: response(
        {"refno": "=1/101", "ce_after": "=1/101"}, operation="route", dry_run=args["dry_run"])))
    server._tools["e3d_create_element"] = ({"inputSchema": {}}, create or (lambda args: response(
        {"refno": "=1/201"}, dry_run=args["dry_run"])))
    server._tools["e3d_copy_element"] = ({"inputSchema": {}}, copy or (lambda args: response(
        {"refno": "=1/301"}, dry_run=args["dry_run"])))
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
    assert result["status"] == "stopped_uncertain"
    assert result["execution_attempted"] is True
    assert result["task_receipts"][0]["status"] == "created_verified"
    assert result["task_receipts"][0]["output_refno"] == "=1/201"
    assert result["retry_safe"] is False


def test_simultaneous_duplicate_workflow_does_not_enter_the_native_writer_twice():
    entered, release = threading.Event(), threading.Event()
    calls = []
    def create(args):
        calls.append(args["dry_run"])
        if not args["dry_run"]:
            entered.set()
            release.wait(2)
        return response({"refno": "=1/201"}, dry_run=args["dry_run"])
    instance = coordinator(fake_server(create=create))
    request = bundle(create_task())
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        plan = instance.plan(request)["result"]
    calls.clear()
    execute = {**request, "expected_session_id": "session", "expected_bridge_build": "build",
               "expected_workflow_revision": plan["workflow_revision"]}
    first = {}
    thread = threading.Thread(target=lambda: first.update(instance.execute(execute)))
    with patch("aveva_e3d_mcp.modeling_team._workflow_context", return_value=CONTEXT):
        thread.start()
        assert entered.wait(2)
        second = instance.execute(execute)
        release.set()
        thread.join(2)
    assert second["workflow_in_progress"] is True
    assert first["result"]["status"] == "completed"
    assert calls.count(False) == 1


def test_tool_discovery_and_closed_contract_include_explicit_agent_workflow_controls():
    server = McpServer("team", "0")
    register_modeling_team(server, BridgeClient(SETTINGS), SETTINGS)
    catalog = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    tools = {row["name"]: row for row in catalog}
    assert tools["e3d_modeling_team_plan"]["annotations"]["readOnlyHint"] is True
    assert tools["e3d_modeling_team_execute"]["annotations"]["readOnlyHint"] is False
    valid = bundle(route_task())
    assert _schema_matches(valid, tools["e3d_modeling_team_plan"]["inputSchema"]) is True
    assert _schema_matches({**valid, "unexpected": True}, tools["e3d_modeling_team_plan"]["inputSchema"]) is False
