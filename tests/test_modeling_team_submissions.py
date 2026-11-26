"""Independent Agent proposals are collected, sealed and executed exactly once."""
from __future__ import annotations

from copy import deepcopy
import json
import re
import threading
from unittest.mock import patch
from uuid import UUID

import pytest

from test_modeling_team import (
    CONTEXT, SETTINGS, WORKFLOW_ID, BridgeClient, coordinator, create_task, fake_server, response, transform_task,
)
from aveva_e3d_mcp.modeling_team import register_modeling_team
from aveva_e3d_mcp.protocol import _schema_matches


MODULE = "aveva_e3d_mcp.modeling_team."
SUBMISSION_A = str(UUID(int=101))
SUBMISSION_B = str(UUID(int=102))


@pytest.fixture(autouse=True)
def no_live_native_requests():
    with patch.object(BridgeClient, "_request", side_effect=AssertionError("Submission tests must never access live E3D")):
        yield


def task(task_id="body", *, sequence=10, name="/BODY", parent_task_id=None, depends_on=None):
    proposal = create_task(task_id, sequence, name=name, parent_task_id=parent_task_id, depends_on=depends_on)
    proposal.pop("agent_id")
    return proposal


def submission(agent_id, *tasks, submission_id=SUBMISSION_A, workflow_id=WORKFLOW_ID):
    return {"workflow_id": workflow_id, "agent_id": agent_id, "submission_id": submission_id,
            "tasks": list(tasks)}


def seal(*agents, workflow_id=WORKFLOW_ID):
    return {"workflow_id": workflow_id, "seal": {"agent_ids": list(agents)}}


def execution(plan):
    return {"workflow_id": plan["workflow_id"], "expected_proposal_revision": plan["proposal_revision"],
            "expected_session_id": plan["expected_session_id"], "expected_bridge_build": plan["expected_bridge_build"],
            "expected_workflow_revision": plan["workflow_revision"]}


def test_submit_and_open_status_are_local_receipts_without_native_observations():
    instance = coordinator()
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Submission must be local")):
        receipt = instance.submit(submission("equipment-a", task()))["result"]
        status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
    assert receipt["status"] == "collecting"
    assert receipt["task_count"] == receipt["agent_count"] == 1
    assert len(receipt["submissions"]) == 1
    assert status["status"] == "collecting" and status["proposal_state"] == "collecting"
    assert status["task_count"] == status["agent_count"] == 1


def test_submit_canonical_duplicate_is_idempotent_and_returned_data_is_isolated():
    calls = []
    instance = coordinator(fake_server(create=lambda arguments: calls.append(deepcopy(arguments))
        or response({"refno": "=1/201"}, dry_run=arguments["dry_run"])))
    original = submission("equipment-a", task("b", sequence=20, name="/B"), task("a", name="/A"))
    retained = deepcopy(original)
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Submission must be local")):
        first = instance.submit(original)["result"]
        original["tasks"][0]["arguments"]["name"] = "/CALLER-MUTATED"
        first["submissions"].clear()
        reordered = deepcopy(retained)
        reordered["tasks"].reverse()
        for row in reordered["tasks"]:
            row.update(agent_id="equipment-a", depends_on=[])
        duplicate_envelope = instance.submit(reordered)
        duplicate = duplicate_envelope["result"]
        status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
    assert duplicate_envelope["duplicate_submission"] is True
    assert duplicate["task_count"] == 2 and duplicate["agent_count"] == 1
    assert len(status["submissions"]) == 1
    with patch(MODULE + "_workflow_context", return_value=CONTEXT):
        frozen = instance.plan(seal("equipment-a"))["result"]
    assert frozen["status"] == "ready"
    assert [row["name"] for row in calls] == ["/A", "/B"]


@pytest.mark.parametrize("mutate", [
    lambda value: value["tasks"][0]["arguments"].update(name="/CHANGED"),
    lambda value: value.update(submission_id=SUBMISSION_B),
    lambda value: value.update(agent_id="EQUIPMENT-A", submission_id=SUBMISSION_B),
])
def test_one_agent_cannot_replace_its_submission_or_change_uuid_content(mutate):
    instance = coordinator()
    request = submission("equipment-a", task())
    instance.submit(request)
    changed = deepcopy(request)
    mutate(changed)
    with pytest.raises(ValueError):
        instance.submit(changed)
    status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
    assert status["agent_count"] == 1 and status["task_count"] == 1


def test_submission_id_is_unique_across_agents_within_the_same_workflow():
    instance = coordinator()
    instance.submit(submission("equipment-a", task("a", name="/A")))
    with pytest.raises(ValueError):
        instance.submit(submission("equipment-b", task("b", name="/B")))
    assert instance.status({"workflow_id": WORKFLOW_ID})["result"]["agent_count"] == 1


@pytest.mark.parametrize("changes", [
    {"submission_id": "not-a-uuid"},
    {"agent_id": "bad agent"},
    {"tasks": []},
    {"tasks": [{**task(), "agent_id": "someone-else"}]},
    {"tasks": [{**task(), "arguments": {"dry_run": False}}]},
    {"tasks": [{**task(), "unexpected": "DELETE CE"}]},
])
def test_invalid_submission_never_reaches_context_or_native_handlers(changes):
    instance = coordinator()
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Invalid submission reached E3D")):
        with pytest.raises(ValueError):
            instance.submit({**submission("equipment-a", task()), **changes})


def test_seal_waits_for_exact_agent_set_without_reading_e3d_then_freezes_when_complete():
    instance = coordinator()
    instance.submit(submission("equipment-a", task("a", name="/A")))
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Incomplete seal must not read E3D")):
        awaiting = instance.plan(seal("equipment-a", "equipment-b"))["result"]
        state = instance.status({"workflow_id": WORKFLOW_ID})["result"]
    assert awaiting["status"] == "awaiting_submissions"
    assert state["proposal_state"] == "collecting"
    instance.submit(submission("equipment-b", task("b", name="/B"), submission_id=SUBMISSION_B))
    with patch(MODULE + "_workflow_context", return_value=CONTEXT):
        frozen = instance.plan(seal("EQUIPMENT-B", "equipment-a"))["result"]
    assert frozen["status"] == "ready" and frozen["proposal_state"] == "frozen"
    assert re.fullmatch(r"[0-9a-f]{64}", frozen["proposal_revision"])
    assert frozen["task_count"] == frozen["agent_count"] == 2


def test_seal_reports_unexpected_submitted_agent_and_rejects_duplicate_casefolded_agents():
    instance = coordinator()
    instance.submit(submission("equipment-a", task("a", name="/A")))
    instance.submit(submission("equipment-b", task("b", name="/B"), submission_id=SUBMISSION_B))
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Invalid roster must not read E3D")):
        excluded = instance.plan(seal("equipment-a"))["result"]
        with pytest.raises(ValueError):
            instance.plan(seal("equipment-a", "EQUIPMENT-A", "equipment-b"))
    assert excluded["status"] == "awaiting_submissions"
    assert excluded["proposal_state"] == "collecting"
    assert excluded["unexpected_agents"] == ["equipment-b"]


def test_equal_local_sequences_get_deterministic_global_order_independent_of_arrival():
    orders = []
    revisions = []
    for agents in [("equipment-z", "equipment-a"), ("equipment-a", "equipment-z")]:
        instance = coordinator()
        for index, agent in enumerate(agents):
            instance.submit(submission(agent, task(agent.replace("-", ""), name="/" + agent.upper()),
                                       submission_id=SUBMISSION_A if agent.endswith("-a") else SUBMISSION_B))
        with patch(MODULE + "_workflow_context", return_value=CONTEXT):
            frozen = instance.plan(seal("equipment-z", "equipment-a"))["result"]
        ordered = frozen["ordered_tasks"]
        orders.append([(row["agent_id"], row["task_id"], row["sequence"]) for row in ordered])
        revisions.append(frozen["proposal_revision"])
        assert len({row["sequence"] for row in ordered}) == 2
    assert orders[0] == orders[1]
    assert [row[0] for row in orders[0]] == ["equipment-a", "equipment-z"]
    assert revisions[0] == revisions[1]


def test_cross_agent_new_parent_dependency_is_deferred_then_resolved_for_tasks_free_execution():
    calls = []
    def create(arguments):
        calls.append(deepcopy(arguments))
        refno = "=1/201" if arguments["name"] == "/BASE" else "=1/202"
        return response({"refno": refno}, dry_run=arguments["dry_run"])
    instance = coordinator(fake_server(create=create))
    child = task("child", name="/CHILD", parent_task_id="base", depends_on=["base"])
    instance.submit(submission("equipment-child", child))
    instance.submit(submission("equipment-parent", task("base", name="/BASE"), submission_id=SUBMISSION_B))
    with patch(MODULE + "_workflow_context", return_value=CONTEXT):
        frozen = instance.plan(seal("equipment-child", "equipment-parent"))["result"]
    assert [row["task_id"] for row in frozen["ordered_tasks"]] == ["base", "child"]
    assert frozen["ordered_tasks"][1]["status"] == "deferred_dependency_preview"
    calls.clear()
    with patch(MODULE + "_workflow_context", return_value=CONTEXT):
        receipt = instance.execute(execution(frozen))["result"]
        repeated = instance.execute(execution(frozen))
    writes = [row for row in calls if not row["dry_run"]]
    assert receipt["status"] == "completed" and receipt["completed_count"] == 2
    assert [(row["name"], row["parent_refno"]) for row in writes] == [("/BASE", "=1/2"), ("/CHILD", "=1/201")]
    assert repeated["duplicate_workflow"] is True
    final_status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
    assert final_status["status"] == "completed"
    assert final_status["proposal_revision"] == frozen["proposal_revision"]
    assert final_status["proposal_state"] == "frozen"


def test_frozen_proposals_reject_late_mutations_but_exact_submit_and_seal_remain_idempotent():
    instance = coordinator()
    original = submission("equipment-a", task("a", name="/A"))
    instance.submit(original)
    with patch(MODULE + "_workflow_context", return_value=CONTEXT):
        frozen = instance.plan(seal("equipment-a"))["result"]
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Duplicate seal must reuse original receipt")):
        repeated = instance.plan(seal("EQUIPMENT-A"))["result"]
        duplicate_envelope = instance.submit(original)
        duplicate = duplicate_envelope["result"]
        with pytest.raises(ValueError):
            instance.submit(submission("equipment-b", task("b", name="/B"), submission_id=SUBMISSION_B))
        changed = deepcopy(original)
        changed["tasks"][0]["arguments"]["name"] = "/CHANGED"
        with pytest.raises(ValueError):
            instance.submit(changed)
    assert repeated["proposal_revision"] == frozen["proposal_revision"]
    assert repeated["workflow_revision"] == frozen["workflow_revision"]
    assert duplicate_envelope["duplicate_submission"] is True and duplicate["proposal_state"] == "frozen"
    repeated["ordered_tasks"].clear()
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Frozen duplicate must be local")):
        assert len(instance.plan(seal("equipment-a"))["result"]["ordered_tasks"]) == 1


@pytest.mark.parametrize("frozen", [False, True])
def test_staged_workflow_cannot_be_bypassed_with_legacy_task_bundle(frozen):
    instance = coordinator()
    proposal = task()
    instance.submit(submission("equipment-a", proposal))
    if frozen:
        with patch(MODULE + "_workflow_context", return_value=CONTEXT):
            instance.plan(seal("equipment-a"))
    legacy = {"workflow_id": WORKFLOW_ID, "tasks": [{**proposal, "agent_id": "equipment-a"}]}
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Legacy bypass must reject before E3D")):
        with pytest.raises(ValueError):
            instance.plan(legacy)
        with pytest.raises(ValueError):
            instance.execute({**legacy, "expected_session_id": "session", "expected_bridge_build": "build",
                              "expected_workflow_revision": "0" * 64})


def test_staged_execution_requires_exact_proposal_revision_and_forbids_replacement_tasks():
    instance = coordinator()
    instance.submit(submission("equipment-a", task()))
    with patch(MODULE + "_workflow_context", return_value=CONTEXT):
        frozen = instance.plan(seal("equipment-a"))["result"]
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Bad staged execution must reject before E3D")):
        with pytest.raises(ValueError):
            instance.execute({**execution(frozen), "expected_proposal_revision": "0" * 64})
        with pytest.raises(ValueError):
            instance.execute({**execution(frozen), "tasks": [{**task(), "agent_id": "equipment-a"}]})


def test_simultaneous_duplicate_submission_retains_one_agent_proposal():
    instance = coordinator()
    request = submission("equipment-a", task())
    barrier = threading.Barrier(3)
    replies, failures = [], []
    def invoke():
        try:
            barrier.wait(timeout=2)
            replies.append(instance.submit(deepcopy(request)))
        except BaseException as exc:
            failures.append(exc)
    threads = [threading.Thread(target=invoke) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)
    assert not failures and all(not thread.is_alive() for thread in threads)
    assert len(replies) == 2 and sum(row.get("duplicate_submission") is True for row in replies) == 1
    assert instance.status({"workflow_id": WORKFLOW_ID})["result"]["task_count"] == 1


def test_simultaneous_duplicate_seal_observes_planning_without_running_preview_twice():
    instance = coordinator()
    instance.submit(submission("equipment-a", task()))
    entered, release = threading.Event(), threading.Event()
    original = instance._preview
    calls = []
    def blocked_preview(*args, **kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(timeout=2)
        return original(*args, **kwargs)
    output, failures = {}, []
    def invoke():
        try:
            output.update(instance.plan(seal("equipment-a")))
        except BaseException as exc:
            failures.append(exc)
    thread = threading.Thread(target=invoke)
    with patch(MODULE + "_workflow_context", return_value=CONTEXT), patch.object(instance, "_preview", side_effect=blocked_preview):
        thread.start()
        try:
            assert entered.wait(timeout=2)
            concurrent = instance.plan(seal("EQUIPMENT-A"))["result"]
            assert concurrent["status"] == "proposal_planning"
            assert concurrent["success"] is False
        finally:
            release.set()
            thread.join(timeout=2)
    assert not failures and not thread.is_alive()
    assert output["result"]["status"] == "ready" and len(calls) == 1
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Frozen duplicate must be local")):
        assert instance.plan(seal("equipment-a"))["result"]["proposal_revision"] == output["result"]["proposal_revision"]


@pytest.mark.parametrize("failure", [RuntimeError("context failed"), KeyboardInterrupt()])
def test_seal_failure_or_cancellation_retains_terminal_plan_failure_without_native_retry(failure):
    instance = coordinator()
    instance.submit(submission("equipment-a", task()))
    with patch(MODULE + "_workflow_context", side_effect=failure):
        try:
            instance.plan(seal("equipment-a"))
        except (RuntimeError, KeyboardInterrupt):
            pass
    status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
    assert status["status"] == "proposal_plan_failed" and status["proposal_state"] == "plan_failed"
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Failed seal must not be replayed")):
        try:
            duplicate = instance.plan(seal("equipment-a"))["result"]
        except ValueError:
            pass
        else:
            assert duplicate["status"] == "proposal_plan_failed"
    with pytest.raises(ValueError):
        instance.submit(submission("equipment-b", task("other", name="/OTHER"), submission_id=SUBMISSION_B))


@pytest.mark.parametrize("graph", ["cycle", "duplicate_task_id", "unknown_dependency"])
def test_invalid_cross_agent_task_graph_is_terminal_and_never_reaches_native_previews(graph):
    instance = coordinator()
    if graph == "cycle":
        first = task("a", name="/A", depends_on=["b"])
        second = task("b", name="/B", depends_on=["a"])
    elif graph == "duplicate_task_id":
        first, second = task("same", name="/A"), task("SAME", name="/B")
    else:
        first, second = task("a", name="/A", depends_on=["missing"]), task("b", name="/B")
    instance.submit(submission("equipment-a", first))
    instance.submit(submission("equipment-b", second, submission_id=SUBMISSION_B))
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Invalid task graph must be local")):
        with pytest.raises(ValueError):
            instance.plan(seal("equipment-a", "equipment-b"))
    status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
    assert status["status"] == "proposal_plan_failed" and status["proposal_state"] == "plan_failed"


def test_frozen_but_unexecuted_proposal_is_not_evicted_at_capacity():
    instance = coordinator()
    other_workflow = str(UUID(int=2001))
    with patch(MODULE + "MAX_STAGED_WORKFLOWS", 2):
        instance.submit(submission("equipment-a", task()))
        with patch(MODULE + "_workflow_context", return_value=CONTEXT):
            frozen = instance.plan(seal("equipment-a"))["result"]
        instance.submit(submission("equipment-b", task(), workflow_id=other_workflow))
        with pytest.raises(ValueError):
            instance.submit(submission("equipment-c", task(), workflow_id=str(UUID(int=2002))))
        assert instance.status({"workflow_id": WORKFLOW_ID})["result"]["proposal_revision"] == frozen["proposal_revision"]


def test_capacity_does_not_evict_unexecuted_proposals_to_accept_a_new_workflow():
    from aveva_e3d_mcp.modeling_team import MAX_STAGED_WORKFLOWS
    instance = coordinator()
    workflow_ids = [str(UUID(int=index + 1000)) for index in range(MAX_STAGED_WORKFLOWS)]
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Collecting capacity must be local")):
        for workflow_id in workflow_ids:
            instance.submit(submission("equipment-a", task(), workflow_id=workflow_id))
        with pytest.raises(ValueError):
            instance.submit(submission("equipment-a", task(), workflow_id=str(UUID(int=9999))))
        assert instance.status({"workflow_id": workflow_ids[0]})["result"]["status"] == "collecting"
        assert instance.status({"workflow_id": workflow_ids[-1]})["result"]["status"] == "collecting"


def registered_server(create=None):
    server = fake_server(create=create)
    register_modeling_team(server, BridgeClient(SETTINGS), SETTINGS)
    return server


def rpc(server, tool, arguments):
    # Exercise the published tools/call boundary using a JSON round trip.
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": tool, "arguments": arguments}}
    return server._dispatch(json.loads(json.dumps(request)))["result"]


def test_registered_schemas_close_the_new_and_legacy_input_modes():
    server = registered_server()
    definitions = {name: entry[0] for name, entry in server._tools.items()}
    plan_schema = definitions["e3d_modeling_team_plan"]["inputSchema"]
    execute_schema = definitions["e3d_modeling_team_execute"]["inputSchema"]
    submit_schema = definitions["e3d_modeling_team_submit"]["inputSchema"]
    legacy = {"workflow_id": WORKFLOW_ID, "tasks": [create_task()]}
    guards = {"expected_session_id": "session", "expected_bridge_build": "build",
              "expected_workflow_revision": "a" * 64}
    assert _schema_matches(legacy, plan_schema)
    assert _schema_matches(seal("equipment-a"), plan_schema)
    assert _schema_matches({**legacy, **guards}, execute_schema)
    frozen_execution = {"workflow_id": WORKFLOW_ID, "expected_proposal_revision": "b" * 64, **guards}
    assert _schema_matches(frozen_execution, execute_schema)
    for invalid in [{"workflow_id": WORKFLOW_ID}, {**legacy, "seal": {"agent_ids": ["equipment-a"]}},
                    {**seal("equipment-a"), "unexpected": True}]:
        assert not _schema_matches(invalid, plan_schema)
    for invalid in [{"workflow_id": WORKFLOW_ID, **guards}, {**frozen_execution, "tasks": [create_task()]},
                    {**frozen_execution, "dry_run": False}]:
        assert not _schema_matches(invalid, execute_schema)
    valid_submit = submission("equipment-a", task())
    assert _schema_matches(valid_submit, submit_schema)
    for field in ("workflow_id", "agent_id", "submission_id", "tasks"):
        missing = {key: value for key, value in valid_submit.items() if key != field}
        assert not _schema_matches(missing, submit_schema)
    assert not _schema_matches({**valid_submit, "unexpected": True}, submit_schema)
    assert not _schema_matches(submission("equipment-a", {**task(), "unexpected": True}), submit_schema)
    for tool in ("e3d_modeling_team_submit", "e3d_modeling_team_plan"):
        assert definitions[tool]["annotations"]["readOnlyHint"] is False
        assert definitions[tool]["annotations"]["destructiveHint"] is False
    assert definitions["e3d_modeling_team_status"]["annotations"]["readOnlyHint"] is True


def test_rpc_collects_seals_and_executes_stored_cross_agent_tasks_without_resubmission():
    calls = []
    def create(arguments):
        calls.append(deepcopy(arguments))
        refno = "=1/201" if arguments["name"] == "/BASE" else "=1/202"
        return response({"refno": refno}, dry_run=arguments["dry_run"])
    server = registered_server(create)
    with patch(MODULE + "_workflow_context", return_value=CONTEXT) as context:
        child = submission("equipment-child", task("child", name="/CHILD", parent_task_id="base"))
        parent = submission("equipment-parent", task("base", name="/BASE"), submission_id=SUBMISSION_B)
        for submitted in (child, parent):
            receipt = rpc(server, "e3d_modeling_team_submit", submitted)
            assert receipt.get("isError", False) is False
        context.assert_not_called()
        planned = rpc(server, "e3d_modeling_team_plan", seal("equipment-parent", "equipment-child"))
        assert planned.get("isError", False) is False
        frozen = planned["structuredContent"]["result"]
        assert [row["task_id"] for row in frozen["frozen_tasks"]] == ["base", "child"]
        assert [row["submitted_sequence"] for row in frozen["frozen_tasks"]] == [10, 10]
        result = rpc(server, "e3d_modeling_team_execute", execution(frozen))
        assert result.get("isError", False) is False
        assert result["structuredContent"]["result"]["status"] == "completed"
        count = len(calls)
        duplicate = rpc(server, "e3d_modeling_team_execute", execution(frozen))
        assert duplicate["structuredContent"]["duplicate_workflow"] is True
        assert len(calls) == count
    writes = [row for row in calls if not row["dry_run"]]
    assert [(row["name"], row["parent_refno"]) for row in writes] == [("/BASE", "=1/2"), ("/CHILD", "=1/201")]


def test_seal_and_status_return_deeply_isolated_frozen_tasks_and_dependencies():
    calls = []
    instance = coordinator(fake_server(create=lambda args: calls.append(deepcopy(args))
        or response({"refno": "=1/201"}, dry_run=args["dry_run"])))
    instance.submit(submission("equipment-a", task("a", name="/A"), task("b", sequence=20, name="/B", depends_on=["a"])))
    with patch(MODULE + "_workflow_context", return_value=CONTEXT):
        frozen = instance.plan(seal("equipment-a"))["result"]
        request = execution(frozen)
        frozen["ordered_tasks"][0]["depends_on"].append("caller-mutation")
        frozen["frozen_tasks"][0]["arguments"]["name"] = "/CALLER-MUTATED"
        status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
        status["plan_receipt"]["result"]["frozen_tasks"][1]["arguments"].clear()
        result = instance.execute(request)["result"]
    assert result["status"] == "completed"
    assert [row["name"] for row in calls if not row["dry_run"]] == ["/A", "/B"]


@pytest.mark.parametrize("field,value", [
    ("expected_session_id", "another-session"), ("expected_bridge_build", "another-build"),
    ("expected_workflow_revision", "0" * 64),
])
def test_frozen_execution_cannot_replace_the_original_plan_guards(field, value):
    instance = coordinator()
    instance.submit(submission("equipment-a", task()))
    with patch(MODULE + "_workflow_context", return_value=CONTEXT):
        frozen = instance.plan(seal("equipment-a"))["result"]
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Changed guards must be rejected before native reads")):
        with pytest.raises(ValueError, match="original frozen plan"):
            instance.execute({**execution(frozen), field: value})
    # A malformed request did not consume the reviewed workflow's execution.
    with patch(MODULE + "_workflow_context", return_value=CONTEXT):
        assert instance.execute(execution(frozen))["result"]["status"] == "completed"


class InterruptOnRelease:
    """Inject one cancellation after a state mutation, before its caller resumes."""
    def __init__(self, predicate):
        self.lock = threading.Lock()
        self.predicate = predicate
        self.armed = True

    def __enter__(self):
        self.lock.acquire()
        return self

    def __exit__(self, *_):
        self.lock.release()
        if self.armed and self.predicate():
            self.armed = False
            raise KeyboardInterrupt()


def test_cancellation_on_seal_admission_cannot_leave_a_permanent_planning_record():
    instance = coordinator()
    instance.submit(submission("equipment-a", task()))
    instance._journal_lock = InterruptOnRelease(lambda: instance._proposals[WORKFLOW_ID]["state"] == "planning")
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Cancelled admission must not reach native reads")):
        with pytest.raises(KeyboardInterrupt):
            instance.plan(seal("equipment-a"))
        status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
        assert status["proposal_state"] == "plan_failed" and status["workflow_in_progress"] is False
        assert instance.plan(seal("equipment-a"))["result"]["status"] == "proposal_plan_failed"


@pytest.mark.parametrize("boundary", ["admission", "writer"])
def test_cancellation_before_a_writer_is_dispatched_finishes_as_preflight_rejected(boundary):
    instance = coordinator()
    instance.submit(submission("equipment-a", task()))
    with patch(MODULE + "_workflow_context", return_value=CONTEXT):
        frozen = instance.plan(seal("equipment-a"))["result"]
    if boundary == "admission":
        instance._journal_lock = InterruptOnRelease(lambda: WORKFLOW_ID in instance._journal)
    else:
        class CancelWriter:
            def __enter__(self):
                raise KeyboardInterrupt()
            def __exit__(self, *_):
                pass
        instance._execution_lock = CancelWriter()
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Cancelled admission must not reach native reads")):
        with pytest.raises(KeyboardInterrupt):
            instance.execute(execution(frozen))
        status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
        assert status["status"] == "preflight_rejected" and status["workflow_in_progress"] is False
        assert status["any_write_tool_call_dispatched"] is False
        assert [row["status"] for row in status["task_receipts"]] == ["not_started"]
        assert instance.execute(execution(frozen))["duplicate_workflow"] is True


def test_cancellation_on_legacy_plan_admission_releases_its_mode_reservation():
    instance = coordinator()
    instance._journal_lock = InterruptOnRelease(lambda: bool(instance._legacy_plans))
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Cancelled admission must not reach native reads")):
        with pytest.raises(KeyboardInterrupt):
            instance.plan({"workflow_id": WORKFLOW_ID, "tasks": [create_task()]})
        assert instance.submit(submission("equipment-a", task()))["result"]["status"] == "collecting"


def test_duplicate_submission_after_execution_preserves_cumulative_write_evidence():
    instance = coordinator()
    original = submission("equipment-a", task())
    instance.submit(original)
    with patch(MODULE + "_workflow_context", return_value=CONTEXT):
        frozen = instance.plan(seal("equipment-a"))["result"]
        instance.execute(execution(frozen))
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Duplicate submit must be local")):
        duplicate = instance.submit(original)["result"]
    assert duplicate["any_write_tool_call_dispatched"] is True
    assert duplicate["execution_attempted"] is True
    assert duplicate["workflow_state"] == "completed" and duplicate["workflow_in_progress"] is False


@pytest.mark.parametrize("mode", ["legacy", "staged"])
def test_terminal_capacity_eviction_removes_one_whole_workflow_and_retains_open_submissions(mode):
    instance = coordinator()
    open_id, third_id = str(UUID(int=2001)), str(UUID(int=2002))
    with patch(MODULE + "MAX_STAGED_WORKFLOWS", 2), patch(MODULE + "_workflow_context", return_value=CONTEXT):
        if mode == "staged":
            instance.submit(submission("equipment-a", task()))
            frozen = instance.plan(seal("equipment-a"))["result"]
            request = execution(frozen)
        else:
            legacy = {"workflow_id": WORKFLOW_ID, "tasks": [create_task()]}
            planned = instance.plan(legacy)["result"]
            request = {**legacy, "expected_session_id": planned["expected_session_id"],
                       "expected_bridge_build": planned["expected_bridge_build"],
                       "expected_workflow_revision": planned["workflow_revision"]}
        instance.execute(request)
        instance.submit(submission("equipment-b", task(), workflow_id=open_id))
        instance.submit(submission("equipment-c", task(), workflow_id=third_id))
    assert instance.status({"workflow_id": WORKFLOW_ID})["result"]["status"] == "not_found"
    assert instance.status({"workflow_id": open_id})["result"]["status"] == "collecting"
    assert instance.status({"workflow_id": third_id})["result"]["status"] == "collecting"


@pytest.mark.parametrize("fail_final_context", [False, True])
def test_preview_transport_failure_cannot_be_reported_as_an_uncertain_model_write(fail_final_context):
    class PreviewTransportError(RuntimeError):
        outcome_uncertain = True
        transport_error = True
    instance = coordinator()
    instance.submit(submission("equipment-a", task()))
    with patch(MODULE + "_workflow_context", return_value=CONTEXT):
        frozen = instance.plan(seal("equipment-a"))["result"]
    reads = [CONTEXT, CONTEXT, RuntimeError("Final context unavailable") if fail_final_context else CONTEXT]
    with patch(MODULE + "_workflow_context", side_effect=reads), \
            patch.object(instance, "_preview", side_effect=PreviewTransportError("Preview timed out")):
        result = instance.execute(execution(frozen))["result"]
    assert result["status"] == "preflight_rejected"
    assert result["any_write_tool_call_dispatched"] is False
    assert result["task_receipts"][0]["status"] == "failed"
    assert result["task_receipts"][0]["native_write_state"] == "not_dispatched"
    assert result["task_receipts"][0]["retry_safe"] is True


def test_frozen_execution_retains_final_context_drift_and_verified_refs_without_replay():
    calls = []
    instance = coordinator(fake_server(create=lambda args: calls.append(args["dry_run"])
        or response({"refno": "=1/201"}, dry_run=args["dry_run"])))
    instance.submit(submission("equipment-a", task()))
    with patch(MODULE + "_workflow_context", return_value=CONTEXT):
        frozen = instance.plan(seal("equipment-a"))["result"]
    changed = {**CONTEXT, "mdb": "OTHER_MDB"}
    with patch(MODULE + "_workflow_context", side_effect=[CONTEXT, CONTEXT, CONTEXT, changed]):
        receipt = instance.execute(execution(frozen))["result"]
    before_duplicate = list(calls)
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Final context drift must not replay writes")):
        repeated = instance.execute(execution(frozen))
        status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
    assert receipt["status"] == status["workflow_status"] == "workflow_context_changed"
    assert receipt["final_context_matches_expected"] is status["final_context_matches_expected"] is False
    assert receipt["task_receipts"][0]["status"] == "created_verified"
    assert receipt["task_receipts"][0]["output_refno"] == "=1/201"
    assert receipt["proposal_revision"] == status["proposal_revision"] == frozen["proposal_revision"]
    assert status["next_action"] == "verify_context_before_continuing"
    assert repeated["duplicate_workflow"] is True and repeated["result"] == receipt
    assert calls == before_duplicate and calls.count(False) == 1


def test_blocked_native_preview_keeps_original_failure_evidence_and_cannot_execute():
    native = {"ok": True, "version": "E3D21", "result": {"success": False, "dry_run": True,
        "creation_attempted": False, "error": "Parent ancestry reached a different WORLD"}}
    calls = []
    instance = coordinator(fake_server(create=lambda args: calls.append(deepcopy(args)) or deepcopy(native)))
    instance.submit(submission("equipment-a", task()))
    with patch(MODULE + "_workflow_context", return_value=CONTEXT):
        blocked = instance.plan(seal("equipment-a"))["result"]
    assert blocked["status"] == "blocked" and blocked["success"] is False
    assert blocked["ordered_tasks"][0]["native_preview"] == native
    blocked["ordered_tasks"][0]["native_preview"]["result"]["error"] = "caller mutation"
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Blocked plan must not execute")):
        status = instance.status({"workflow_id": WORKFLOW_ID})["result"]
        assert status["plan_receipt"]["result"]["ordered_tasks"][0]["native_preview"] == native
        with pytest.raises(ValueError, match="blocked"):
            instance.execute(execution(blocked))
    assert all(call["dry_run"] is True for call in calls)


def test_active_legacy_plan_blocks_submission_and_a_later_staged_claim_blocks_its_old_execution():
    entered, release = threading.Event(), threading.Event()
    calls, replies, failures = [], [], []
    def create(args):
        calls.append(deepcopy(args))
        entered.set()
        assert release.wait(timeout=2)
        return response({"refno": "=1/201"}, dry_run=args["dry_run"])
    instance = coordinator(fake_server(create=create))
    legacy = {"workflow_id": WORKFLOW_ID, "tasks": [create_task()]}
    def plan():
        try:
            replies.append(instance.plan(legacy)["result"])
        except BaseException as exc:
            failures.append(exc)
    thread = threading.Thread(target=plan)
    with patch(MODULE + "_workflow_context", return_value=CONTEXT):
        thread.start()
        try:
            assert entered.wait(timeout=2)
            with pytest.raises(ValueError, match="legacy workflow"):
                instance.submit(submission("equipment-a", task()))
        finally:
            release.set()
            thread.join(timeout=2)
    assert not thread.is_alive() and not failures and replies[0]["success"]
    instance.submit(submission("equipment-a", task()))
    with patch(MODULE + "_workflow_context", side_effect=AssertionError("Legacy bypass must not reach native context")):
        with pytest.raises(ValueError, match="expected_proposal_revision"):
            instance.execute({**legacy, "expected_session_id": replies[0]["expected_session_id"],
                "expected_bridge_build": replies[0]["expected_bridge_build"],
                "expected_workflow_revision": replies[0]["workflow_revision"]})
    assert all(call["dry_run"] for call in calls)


@pytest.mark.parametrize("changed", ["submission_id", "local_sequence"])
def test_proposal_revision_binds_original_submission_material_independently_of_native_plan(changed):
    plans = []
    for index in range(2):
        instance = coordinator()
        instance.submit(submission("equipment-a", task(sequence=100 if index and changed == "local_sequence" else 10),
            submission_id=SUBMISSION_B if index and changed == "submission_id" else SUBMISSION_A))
        with patch(MODULE + "_workflow_context", return_value=CONTEXT):
            plans.append(instance.plan(seal("equipment-a"))["result"])
    assert plans[0]["proposal_revision"] != plans[1]["proposal_revision"]
    assert plans[0]["workflow_revision"] == plans[1]["workflow_revision"]


def test_cross_agent_copy_source_uses_the_verified_earlier_output_ref():
    copies = []
    instance = coordinator(fake_server(copy=lambda args: copies.append(deepcopy(args))
        or response({"refno": "=1/301"}, dry_run=args["dry_run"])))
    copy = {"task_id": "copy", "sequence": 10, "operation": "equipment_copy", "source_task_id": "body",
            "arguments": {"parent_refno": "=1/2", "name": "/COPY"}}
    instance.submit(submission("equipment-copy", copy))
    instance.submit(submission("equipment-source", task(), submission_id=SUBMISSION_B))
    with patch(MODULE + "_workflow_context", return_value=CONTEXT):
        frozen = instance.plan(seal("equipment-source", "equipment-copy"))["result"]
        assert frozen["ordered_tasks"][1]["status"] == "deferred_dependency_preview"
        assert not copies
        result = instance.execute(execution(frozen))["result"]
    assert result["status"] == "completed"
    assert [row["source_refno"] for row in copies] == ["=1/201", "=1/201"]
    assert [row["dry_run"] for row in copies] == [True, False]


def test_cross_agent_transform_target_is_deferred_then_bound_to_verified_output_and_revision():
    transforms = []
    def transform(args):
        transforms.append(deepcopy(args))
        dry_run = args["dry_run"]
        refno = args["refno"]
        return {"ok": True, "version": "E3D21", "result": {
            "success": True, "dry_run": dry_run, "revision": "e" * 64,
            "revision_conflict": False, "ce_unchanged": True, "refno": refno, "element": {"refno": refno},
            "ce_after": "=1/10", "write_attempted": not dry_run, "write_applied": not dry_run,
            "readback_verified": not dry_run, "notification_update": {
                "attempted": not dry_run, "completed": not dry_run,
                "readback_verified": not dry_run, "frame_identity_unchanged": True}}}
    instance = coordinator(fake_server(transform=transform))
    move = transform_task("move", target_task_id="base")
    move.pop("agent_id")
    instance.submit(submission("equipment-transform", move))
    instance.submit(submission("equipment-base", task("base", name="/BASE"), submission_id=SUBMISSION_B))
    with patch(MODULE + "_workflow_context", return_value=CONTEXT):
        plan = instance.plan(seal("equipment-base", "equipment-transform"))["result"]
        assert plan["ordered_tasks"][1]["status"] == "deferred_dependency_preview"
        assert plan["frozen_tasks"][1]["target_task_id"] == "base"
        result = instance.execute(execution(plan))["result"]
    assert result["status"] == "completed"
    assert [row["dry_run"] for row in transforms] == [True, False]
    assert [row["refno"] for row in transforms] == ["=1/201", "=1/201"]
    assert transforms[1]["expected_revision"] == "e" * 64
    assert result["task_receipts"][1]["status"] == "transform_verified"
