"""Merge multi-agent modeling proposals and serialize guarded E3D writes.

Agents contribute data-only tasks. A single MCP workflow handler owns the
E3D callbacks, per-step previews, writes, and readbacks for the whole bundle.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import re
from threading import Lock
from typing import Any
from uuid import UUID, uuid5

from .bridge import BridgeClient, BridgeError
from .context_snapshot import context_snapshot
from .piping_route_plan import _identity
from .protocol import McpServer, _schema_matches
from .config import Settings


PLAN_TOOL = "e3d_modeling_team_plan"
EXECUTE_TOOL = "e3d_modeling_team_execute"
STATUS_TOOL = "e3d_modeling_team_status"
SUBMIT_TOOL = "e3d_modeling_team_submit"
MAX_TASKS = 16
MAX_STAGED_WORKFLOWS = 64
_TASK_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}")
_AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_SHA = re.compile(r"[0-9a-f]{64}")
_OPERATIONS = {"piping_route": "e3d_piping_route_create",
               "equipment_create": "e3d_create_element",
               "equipment_copy": "e3d_copy_element",
               "element_transform": "e3d_set_transform"}
_FAILED_TASK_STATUSES = {"failed", "outcome_uncertain", "created_verified_context_changed",
                        "created_verified_context_unverified", "transform_verified_context_changed",
                        "transform_verified_context_unverified"}
_COMPLETED_TASK_STATUSES = {"created_verified", "transform_verified"}
_NATIVE_KNOWN_TASK_STATUSES = _COMPLETED_TASK_STATUSES | {
    "created_verified_context_changed", "created_verified_context_unverified",
    "transform_verified_context_changed", "transform_verified_context_unverified",
}
_CONTROLLED = {"dry_run", "expected_revision", "expected_session_id", "expected_bridge_build", "request_id",
               "capture_after", "capture_width", "capture_height"}


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _verified_status(operation: str, suffix: str = "") -> str:
    prefix = "transform_verified" if operation == "element_transform" else "created_verified"
    return prefix + suffix


class _NativePreviewError(ValueError):
    def __init__(self, message: str, reply: Any):
        super().__init__(message)
        self.native_preview = deepcopy(reply) if isinstance(reply, dict) else None


class _WorkflowContextChanged(ValueError):
    """A post-write context read succeeded and proved that the guarded context changed."""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def _uuid(value: Any, field: str) -> str:
    _need(isinstance(value, str), f"{field} must be a canonical UUID")
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f"{field} must be a canonical UUID") from None
    _need(str(parsed) == value, f"{field} must be a canonical lowercase UUID")
    return value


def _workflow_context(bridge: BridgeClient) -> dict[str, Any]:
    snapshot = context_snapshot(bridge, bridge.settings)
    observed = _identity(snapshot)
    _need(observed.get("complete") is True, "Complete ready E3D session/project/MDB/CE context required")
    identity = observed["identity"]
    _need(all(isinstance(identity.get(key), str) and identity[key].strip()
              for key in ("version", "session_id", "bridge_build", "project", "mdb", "current_element")),
          "Explicit E3D session/build/project/MDB/current-element identity required")
    return identity


def _normalize(args: Any, *, execution: bool = False) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    allowed = {"workflow_id", "tasks"} | ({"expected_session_id", "expected_bridge_build", "expected_workflow_revision"} if execution else set())
    _need(isinstance(args, dict) and set(args) <= allowed and {"workflow_id", "tasks"} <= set(args),
          "Workflow requires only workflow_id and tasks, plus the execution guards when executing")
    workflow_id = _uuid(args["workflow_id"], "workflow_id")
    raw_tasks = args["tasks"]
    _need(isinstance(raw_tasks, list) and 1 <= len(raw_tasks) <= MAX_TASKS,
          f"tasks must contain 1 to {MAX_TASKS} agent proposals")
    rows: list[dict[str, Any]] = []
    identifiers: dict[str, str] = {}
    for index, raw in enumerate(raw_tasks):
        _need(isinstance(raw, dict) and not set(raw) - {"task_id", "agent_id", "sequence", "operation", "depends_on",
                                                       "parent_task_id", "source_task_id", "target_task_id", "arguments"},
              f"tasks[{index}] has unsupported fields")
        task_id, agent_id = raw.get("task_id"), raw.get("agent_id")
        _need(isinstance(task_id, str) and bool(_TASK_ID.fullmatch(task_id)), f"tasks[{index}].task_id is invalid")
        key = task_id.casefold()
        _need(key not in identifiers, "task_id values must be unique ignoring case")
        identifiers[key] = task_id
        _need(isinstance(agent_id, str) and bool(_AGENT_ID.fullmatch(agent_id)), f"tasks[{index}].agent_id is invalid")
        sequence = raw.get("sequence")
        _need(type(sequence) is int and 0 <= sequence <= 1000000, f"tasks[{index}].sequence must be an integer from 0 through 1000000")
        operation = raw.get("operation")
        _need(operation in _OPERATIONS,
              f"tasks[{index}].operation must be piping_route, equipment_create, equipment_copy or element_transform")
        dependencies = raw.get("depends_on", [])
        _need(isinstance(dependencies, list) and len(dependencies) <= MAX_TASKS
              and all(isinstance(item, str) and bool(_TASK_ID.fullmatch(item)) for item in dependencies),
              f"tasks[{index}].depends_on must be an array of task IDs")
        _need(len({item.casefold() for item in dependencies}) == len(dependencies), f"tasks[{index}].depends_on contains duplicates")
        parent_task_id, source_task_id, target_task_id = (raw.get("parent_task_id"), raw.get("source_task_id"),
                                                           raw.get("target_task_id"))
        for field, value in (("parent_task_id", parent_task_id), ("source_task_id", source_task_id),
                             ("target_task_id", target_task_id)):
            _need(value is None or isinstance(value, str) and bool(_TASK_ID.fullmatch(value)), f"tasks[{index}].{field} is invalid")
        _need(operation == "equipment_copy" or source_task_id is None,
              "source_task_id is only supported by equipment_copy tasks")
        _need(operation != "piping_route" or (parent_task_id is None and source_task_id is None),
              "Piping routes require an explicit existing ZONE parent")
        _need(operation == "element_transform" or target_task_id is None,
              "target_task_id is only supported by element_transform tasks")
        _need(operation != "element_transform" or parent_task_id is None and source_task_id is None,
              "element_transform requires target_task_id instead of parent/source task references")
        arguments = raw.get("arguments")
        _need(isinstance(arguments, dict) and not set(arguments) & _CONTROLLED,
              f"tasks[{index}].arguments must omit execution controls, captures and caller-generated revisions")
        if parent_task_id is not None:
            _need("parent_refno" not in arguments, f"tasks[{index}] must use parent_task_id instead of parent_refno")
        if source_task_id is not None:
            _need("source_refno" not in arguments, f"tasks[{index}] must use source_task_id instead of source_refno")
        if target_task_id is not None:
            _need("refno" not in arguments, f"tasks[{index}] must use target_task_id instead of refno")
        if operation == "element_transform":
            _need(target_task_id is not None or isinstance(arguments.get("refno"), str)
                  and bool(arguments["refno"].strip()),
                  f"tasks[{index}] element_transform requires refno or target_task_id")
        rows.append({"task_id": task_id, "agent_id": agent_id, "sequence": sequence,
                     "operation": operation, "depends_on": list(dependencies),
                     "parent_task_id": parent_task_id, "source_task_id": source_task_id,
                     "target_task_id": target_task_id,
                     "arguments": deepcopy(arguments)})
    _need(len({row["sequence"] for row in rows}) == len(rows), "sequence values must be unique")
    for row in rows:
        implicit = [value for value in (row["parent_task_id"], row["source_task_id"], row["target_task_id"])
                    if value is not None]
        deps = {value.casefold() for value in row["depends_on"]}
        for value in implicit:
            _need(value.casefold() in identifiers, f"Task {row['task_id']} depends on unknown task {value}")
            deps.add(value.casefold())
        row["dependency_keys"] = deps
        for value in row["depends_on"]:
            _need(value.casefold() in identifiers, f"Task {row['task_id']} depends on unknown task {value}")
        _need(row["task_id"].casefold() not in deps, f"Task {row['task_id']} cannot depend on itself")
    by_key = {row["task_id"].casefold(): row for row in rows}
    remaining = set(by_key)
    ordered: list[dict[str, Any]] = []
    while remaining:
        available = [by_key[key] for key in remaining if by_key[key]["dependency_keys"] <= {row["task_id"].casefold() for row in ordered}]
        if not available:
            raise ValueError("Workflow dependency graph contains a cycle")
        available.sort(key=lambda row: (row["sequence"], row["task_id"].casefold()))
        for row in available:
            ordered.append(row)
            remaining.remove(row["task_id"].casefold())
    conflicts: list[dict[str, Any]] = []
    claims: dict[tuple[str, str], dict[str, Any]] = {}
    for row in ordered:
        arguments = row["arguments"]
        parent = "task:" + row["parent_task_id"].casefold() if row["parent_task_id"] else str(arguments.get("parent_refno", "")).casefold()
        name = arguments.get("pipe_name") if row["operation"] == "piping_route" else arguments.get("name")
        if isinstance(name, str) and name.strip():
            claim = (parent, name.strip().casefold())
            if claim in claims:
                conflicts.append({"code": "duplicate_sibling_name", "task_ids": [claims[claim]["task_id"], row["task_id"]],
                                  "parent": parent, "name": name})
            else:
                claims[claim] = row
    return workflow_id, ordered, conflicts


def _canonical_submission_tasks(workflow_id: str, agent_id: str, raw_tasks: Any) -> list[dict[str, Any]]:
    _need(isinstance(raw_tasks, list) and 1 <= len(raw_tasks) <= MAX_TASKS,
          f"tasks must contain 1 to {MAX_TASKS} proposals")
    fields = {"task_id", "agent_id", "sequence", "operation", "depends_on",
              "parent_task_id", "source_task_id", "target_task_id", "arguments"}
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_sequences: set[int] = set()
    for index, raw in enumerate(raw_tasks):
        _need(isinstance(raw, dict) and set(raw) <= fields,
              f"tasks[{index}] has unsupported fields")
        task_agent = raw.get("agent_id", agent_id)
        _need(task_agent == agent_id, f"tasks[{index}].agent_id must match the submitting agent_id")
        row = {**raw, "agent_id": agent_id}
        task_id = row.get("task_id")
        _need(isinstance(task_id, str) and bool(_TASK_ID.fullmatch(task_id)),
              f"tasks[{index}].task_id is invalid")
        _need(task_id.casefold() not in seen_ids, "task_id values must be unique within one submission")
        seen_ids.add(task_id.casefold())
        sequence = row.get("sequence")
        _need(type(sequence) is int and 0 <= sequence <= 1000000,
              f"tasks[{index}].sequence must be an integer from 0 through 1000000")
        _need(sequence not in seen_sequences,
              "sequence values must be unique within one agent submission")
        seen_sequences.add(sequence)
        operation = row.get("operation")
        _need(operation in _OPERATIONS,
              f"tasks[{index}].operation must be piping_route, equipment_create, equipment_copy or element_transform")
        dependencies = row.get("depends_on", [])
        _need(isinstance(dependencies, list) and len(dependencies) <= MAX_TASKS
              and all(isinstance(item, str) and bool(_TASK_ID.fullmatch(item)) for item in dependencies),
              f"tasks[{index}].depends_on must be an array of task IDs")
        _need(len({item.casefold() for item in dependencies}) == len(dependencies),
              f"tasks[{index}].depends_on contains duplicates")
        _need(task_id.casefold() not in {item.casefold() for item in dependencies},
              f"Task {task_id} cannot depend on itself")
        parent_task_id, source_task_id, target_task_id = (row.get("parent_task_id"), row.get("source_task_id"),
                                                           row.get("target_task_id"))
        for field, value in (("parent_task_id", parent_task_id), ("source_task_id", source_task_id),
                             ("target_task_id", target_task_id)):
            _need(value is None or isinstance(value, str) and bool(_TASK_ID.fullmatch(value)),
                  f"tasks[{index}].{field} is invalid")
        _need(operation == "equipment_copy" or source_task_id is None,
              "source_task_id is only supported by equipment_copy tasks")
        _need(operation != "piping_route" or (parent_task_id is None and source_task_id is None),
              "Piping routes require an explicit existing ZONE parent")
        _need(operation == "element_transform" or target_task_id is None,
              "target_task_id is only supported by element_transform tasks")
        _need(operation != "element_transform" or parent_task_id is None and source_task_id is None,
              "element_transform requires target_task_id instead of parent/source task references")
        arguments = row.get("arguments")
        _need(isinstance(arguments, dict) and not set(arguments) & _CONTROLLED,
              f"tasks[{index}].arguments must omit execution controls, captures and caller-generated revisions")
        _need(parent_task_id is None or "parent_refno" not in arguments,
              f"tasks[{index}] must use parent_task_id instead of parent_refno")
        _need(source_task_id is None or "source_refno" not in arguments,
              f"tasks[{index}] must use source_task_id instead of source_refno")
        _need(target_task_id is None or "refno" not in arguments,
              f"tasks[{index}] must use target_task_id instead of refno")
        if operation == "element_transform":
            _need(target_task_id is not None or isinstance(arguments.get("refno"), str)
                  and bool(arguments["refno"].strip()),
                  f"tasks[{index}] element_transform requires refno or target_task_id")
        # Run the ordinary task contract without resolving dependencies that
        # may arrive from another agent later.
        local = {key: value for key, value in row.items()
                 if key not in {"depends_on", "parent_task_id", "source_task_id", "target_task_id"}}
        if operation == "element_transform" and target_task_id is not None:
            local["arguments"] = {**arguments, "refno": "=1/1"}
        _normalize({"workflow_id": workflow_id, "tasks": [local]})
        rows.append({"task_id": task_id, "agent_id": agent_id, "sequence": sequence,
                     "operation": operation, "depends_on": list(dependencies),
                     "parent_task_id": parent_task_id, "source_task_id": source_task_id,
                     "target_task_id": target_task_id,
                     "arguments": deepcopy(arguments)})
    rows.sort(key=lambda row: row["task_id"].casefold())
    _digest(rows)  # Reject non-JSON values before retaining caller data.
    return rows


class ModelingTeamCoordinator:
    """One deterministic proposal merge and one synchronous in-process writer."""

    def __init__(self, server: McpServer, bridge: BridgeClient, settings: Settings):
        self.server, self.bridge, self.settings = server, bridge, settings
        self._execution_lock = Lock()
        self._journal_lock = Lock()
        self._journal: OrderedDict[str, dict[str, Any]] = OrderedDict()
        # This lock also owns proposal admission/sealing. Never call E3D while
        # holding it; status and duplicate requests must remain available.
        self._proposals: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._legacy_plans: dict[str, set[object]] = {}

    def _make_room(self, workflow_id: str) -> None:
        """Reserve bounded retention under _journal_lock, evicting terminals only."""
        retained = set(self._journal) | set(self._proposals)
        if workflow_id in retained:
            return
        while len(retained) >= MAX_STAGED_WORKFLOWS:
            evict = next((key for key, row in self._journal.items() if row["completed"]), None)
            if evict is None:
                evict = next((key for key, row in self._proposals.items() if row.get("execution_completed")), None)
            _need(evict is not None, "Workflow capacity is full; unexecuted workflows are retained")
            self._journal.pop(evict, None)
            self._proposals.pop(evict, None)
            retained.remove(evict)

    def _proposal_metadata(self, entry: dict[str, Any]) -> dict[str, Any]:
        submissions = sorted(entry["submissions"].values(), key=lambda row: row["agent_id"].casefold())
        return {"proposal_state": entry["state"], "proposal_revision": entry.get("proposal_revision"),
                "agent_ids": [row["agent_id"] for row in submissions], "agent_count": len(submissions),
                "task_count": sum(len(row["tasks"]) for row in submissions),
                "submissions": [{key: row[key] for key in ("agent_id", "submission_id")}
                                | {"task_count": len(row["tasks"])} for row in submissions],
                "plan_receipt": deepcopy(entry.get("plan_receipt")),
                "proposal_durable": False}

    def _proposal_response(self, workflow_id: str, entry: dict[str, Any], operation: str) -> dict[str, Any]:
        state = entry["state"]
        reply = {"ok": True, "version": "E3D21", "result": {
            "operation": operation, "status": {"planning": "proposal_planning",
                "plan_failed": "proposal_plan_failed"}.get(state, state),
            "success": state != "plan_failed" and operation != "modeling_team_plan", "workflow_id": workflow_id,
            "workflow_state": state, "workflow_in_progress": state == "planning",
            "read_only": operation == "modeling_team_status", "native_read_only": True,
            "journal_available": False, "journal_durable": False,
            "execution_attempted": False, "write_tool_call_dispatched": False,
            "any_write_tool_call_dispatched": False, "current_task_write_tool_call_dispatched": None,
            "synchronous_native_writes": False, "native_write_state": "not_dispatched",
            "next_action": "submit_or_seal" if state == "collecting" else "poll_status" if state == "planning"
                else "correct_proposal_and_replan_with_new_workflow_id" if state == "plan_failed"
                or not entry["plan_receipt"]["result"].get("success") else "execute_frozen_workflow",
            **self._proposal_metadata(entry)}}
        journal = self._journal.get(workflow_id)
        if journal is not None:
            observed = journal["result"]["result"]
            reply["result"].update({key: deepcopy(observed.get(key)) for key in (
                "execution_attempted", "write_tool_call_dispatched", "any_write_tool_call_dispatched",
                "current_task_write_tool_call_dispatched", "native_write_state", "completed_count",
                "failed_count", "current_task_id")})
            reply["result"].update(journal_available=True, workflow_state=observed["status"],
                workflow_status=observed["status"], workflow_in_progress=not journal["completed"],
                next_action="read_workflow_status")
        return reply

    def submit(self, args: dict[str, Any]) -> dict[str, Any]:
        _need(isinstance(args, dict) and set(args) == {"workflow_id", "agent_id", "submission_id", "tasks"},
              "Submission requires only workflow_id, agent_id, submission_id and tasks")
        workflow_id = _uuid(args["workflow_id"], "workflow_id")
        submission_id = _uuid(args["submission_id"], "submission_id")
        agent_id = args["agent_id"]
        _need(isinstance(agent_id, str) and bool(_AGENT_ID.fullmatch(agent_id)), "agent_id is invalid")
        tasks = _canonical_submission_tasks(workflow_id, agent_id, args["tasks"])
        fingerprint = _digest({"agent_id": agent_id, "submission_id": submission_id, "tasks": tasks})
        with self._journal_lock:
            entry = self._proposals.get(workflow_id)
            if entry is not None:
                old = entry["submissions"].get(agent_id.casefold())
                if old is not None:
                    _need(old["fingerprint"] == fingerprint,
                          "Agent already submitted different content or a different submission_id for this workflow")
                    return {**self._proposal_response(workflow_id, entry, "modeling_team_submit"),
                            "duplicate_submission": True}
                _need(entry["state"] == "collecting", "Workflow proposals are sealed; late submissions are rejected")
                _need(all(row["submission_id"] != submission_id for row in entry["submissions"].values()),
                      "submission_id is already used by another agent in this workflow")
                _need(sum(len(row["tasks"]) for row in entry["submissions"].values()) + len(tasks) <= MAX_TASKS,
                      f"A workflow may contain at most {MAX_TASKS} tasks across all agents")
            else:
                _need(workflow_id not in self._journal and workflow_id not in self._legacy_plans,
                      "workflow_id is already in use by a legacy workflow")
                self._make_room(workflow_id)
                entry = {"state": "collecting", "submissions": {}, "plan_receipt": None,
                         "execution_completed": False}
                self._proposals[workflow_id] = entry
            entry["submissions"][agent_id.casefold()] = {"agent_id": agent_id, "submission_id": submission_id,
                                                       "tasks": tasks, "fingerprint": fingerprint}
            return self._proposal_response(workflow_id, entry, "modeling_team_submit")

    def _publish_progress(self, workflow_id: str, revision: str | None, receipts: list[dict[str, Any]],
                          task_count: int, current_task_id: str | None, execution_attempted: bool) -> None:
        active = (next((row for row in reversed(receipts) if row.get("task_id") == current_task_id), None)
                  if current_task_id is not None else
                  next((row for row in reversed(receipts) if row.get("execution_attempted")), None))
        result = {"ok": True, "version": "E3D21", "result": {
            "operation": "modeling_team_execute", "status": "workflow_in_progress", "success": False,
            "workflow_id": workflow_id, "workflow_revision": revision, "synchronous_native_writes": True,
            "execution_attempted": execution_attempted, "write_tool_call_dispatched": execution_attempted,
            "any_write_tool_call_dispatched": execution_attempted,
            "current_task_write_tool_call_dispatched": (active.get("execution_attempted")
                if current_task_id is not None and active else None),
            "native_write_state": active.get("native_write_state") if active else None,
            "phase": "processing_task" if current_task_id is not None else "between_tasks",
            "retry_safe": False if execution_attempted else None,
            "task_count": task_count,
            "completed_count": sum(row.get("status") in _COMPLETED_TASK_STATUSES for row in receipts),
            "failed_count": sum(row.get("status") in _FAILED_TASK_STATUSES for row in receipts),
            "current_task_id": current_task_id, "task_receipts": deepcopy(receipts), "errors": []}}
        with self._journal_lock:
            entry = self._journal.get(workflow_id)
            if entry is not None and not entry["completed"]:
                if entry.get("proposal_revision") is not None:
                    result["result"].update(proposal_revision=entry["proposal_revision"], proposal_state="frozen")
                entry["result"] = result

    def _workflow_revision(self, workflow_id: str, tasks: list[dict[str, Any]], identity: dict[str, Any],
                           conflicts: list[dict[str, Any]]) -> str:
        material = {"format": 1, "workflow_id": workflow_id, "tasks": [
            {key: row[key] for key in ("task_id", "agent_id", "sequence", "operation", "depends_on",
                                       "parent_task_id", "source_task_id", "target_task_id", "arguments")} for row in tasks],
            "session": {key: identity[key] for key in ("version", "session_id", "bridge_build", "project", "mdb", "current_element")},
            "conflicts": conflicts}
        return _digest(material)

    def _arguments(self, row: dict[str, Any], refs: dict[str, str] | None = None) -> dict[str, Any]:
        arguments = deepcopy(row["arguments"])
        refs = refs or {}
        if row["parent_task_id"] is not None:
            parent_key = row["parent_task_id"].casefold()
            _need(parent_key in refs, f"Parent task {row['parent_task_id']} has no verified output REF")
            arguments["parent_refno"] = refs[parent_key]
        if row["source_task_id"] is not None:
            source_key = row["source_task_id"].casefold()
            _need(source_key in refs, f"Source task {row['source_task_id']} has no verified output REF")
            arguments["source_refno"] = refs[source_key]
        if row["target_task_id"] is not None:
            target_key = row["target_task_id"].casefold()
            _need(target_key in refs, f"Target task {row['target_task_id']} has no verified output REF")
            arguments["refno"] = refs[target_key]
        return arguments

    def _handler(self, row: dict[str, Any]) -> tuple[str, Any, dict[str, Any]]:
        name = _OPERATIONS[row["operation"]]
        registered = self.server._tools.get(name)
        _need(registered is not None, f"Required native tool is unavailable: {name}")
        definition, handler = registered
        return name, handler, definition["inputSchema"]

    def _preview(self, row: dict[str, Any], refs: dict[str, str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        name, handler, schema = self._handler(row)
        arguments = self._arguments(row, refs)
        arguments["dry_run"] = True
        _need(_schema_matches(arguments, schema), f"Task {row['task_id']} does not satisfy {name} inputSchema")
        reply = handler(arguments)
        try:
            _need(isinstance(reply, dict) and reply.get("ok") is True and reply.get("version") == "E3D21"
                  and isinstance(reply.get("result"), dict), f"Task {row['task_id']} native preview envelope is unavailable")
            result = reply["result"]
            if row["operation"] == "piping_route":
                _need(result.get("success") is True and result.get("status") == "prepared"
                      and isinstance(result.get("revision"), str) and _SHA.fullmatch(result["revision"]),
                      f"Task {row['task_id']} native route preview failed")
            elif row["operation"] == "element_transform":
                _need(result.get("success") is True and result.get("dry_run") is True
                      and isinstance(result.get("revision"), str) and _SHA.fullmatch(result["revision"])
                      and result.get("ce_unchanged") is True and result.get("revision_conflict") is not True,
                      f"Task {row['task_id']} native transform preview failed")
            else:
                _need(result.get("success") is True and result.get("dry_run") is True
                      and isinstance(result.get("revision"), str) and _SHA.fullmatch(result["revision"])
                      and result.get("ce_unchanged") is True and result.get("revision_conflict") is not True,
                      f"Task {row['task_id']} native create/copy preview failed")
        except ValueError as exc:
            raise _NativePreviewError(str(exc), reply) from None
        return arguments, reply

    def plan(self, args: dict[str, Any]) -> dict[str, Any]:
        if isinstance(args, dict) and "seal" in args:
            return self._seal(args)
        workflow_id, tasks, conflicts = _normalize(args)
        token = object()
        try:
            with self._journal_lock:
                _need(workflow_id not in self._proposals, "Staged workflow requires seal instead of caller-supplied tasks")
                self._legacy_plans.setdefault(workflow_id, set()).add(token)
            with self._execution_lock:
                return self._plan_bundle(workflow_id, tasks, conflicts)
        finally:
            with self._journal_lock:
                active = self._legacy_plans.get(workflow_id)
                if active is not None:
                    active.discard(token)
                if active == set():
                    del self._legacy_plans[workflow_id]

    def _seal(self, args: dict[str, Any]) -> dict[str, Any]:
        claim: dict[str, Any] = {}
        try:
            return self._seal_reserved(args, claim)
        except BaseException as exc:
            if "entry" in claim:
                workflow_id, entry = claim["entry"]
                with self._journal_lock:
                    if self._proposals.get(workflow_id) is entry and entry["state"] in {"planning", "frozen"} \
                            and entry["plan_receipt"] is None:
                        entry["state"] = "plan_failed"
                        receipt = self._proposal_response(workflow_id, entry, "modeling_team_plan")
                        receipt["ok"] = False
                        receipt["result"].update(success=False,
                            errors=[{"type": type(exc).__name__, "message": str(exc)[:2048]}],
                            next_action="correct_proposal_and_replan_with_new_workflow_id")
                        entry["plan_receipt"] = deepcopy(receipt)
            raise

    def _seal_reserved(self, args: dict[str, Any], claim: dict[str, Any]) -> dict[str, Any]:
        _need(set(args) == {"workflow_id", "seal"}, "Seal requires only workflow_id and seal; omit tasks")
        workflow_id = _uuid(args["workflow_id"], "workflow_id")
        seal = args["seal"]
        _need(isinstance(seal, dict) and set(seal) == {"agent_ids"}, "seal requires only agent_ids")
        agent_ids = seal["agent_ids"]
        _need(isinstance(agent_ids, list) and 1 <= len(agent_ids) <= MAX_TASKS
              and all(isinstance(value, str) and _AGENT_ID.fullmatch(value) for value in agent_ids),
              f"seal.agent_ids must list 1 to {MAX_TASKS} agent labels")
        agent_keys = {value.casefold() for value in agent_ids}
        _need(len(agent_keys) == len(agent_ids), "seal.agent_ids must be unique ignoring case")
        with self._journal_lock:
            entry = self._proposals.get(workflow_id)
            _need(entry is not None, "No submitted proposals for workflow_id")
            if entry["state"] != "collecting":
                _need(agent_keys == set(entry["sealed_agents"]), "Workflow is already sealed with a different agent roster")
                if entry["plan_receipt"] is not None:
                    return deepcopy(entry["plan_receipt"])
                return self._proposal_response(workflow_id, entry, "modeling_team_plan")
            actual = set(entry["submissions"])
            if actual != agent_keys:
                reply = self._proposal_response(workflow_id, entry, "modeling_team_plan")
                reply["result"].update(status="awaiting_submissions", success=False,
                    missing_agents=sorted(value for value in agent_ids if value.casefold() not in actual),
                    unexpected_agents=sorted(row["agent_id"] for key, row in entry["submissions"].items()
                                             if key not in agent_keys))
                return reply
            submissions = deepcopy(sorted(entry["submissions"].values(), key=lambda row: row["agent_id"].casefold()))
            proposal_revision = _digest({"format": 1, "workflow_id": workflow_id,
                "agent_ids": [row["agent_id"] for row in submissions],
                "submissions": [{key: row[key] for key in ("agent_id", "submission_id", "tasks")}
                                for row in submissions]})
            # Claim the sole planning owner before releasing the state lock.
            claim["entry"] = (workflow_id, entry)
            entry.update(state="planning", sealed_agents=sorted(agent_keys), proposal_revision=proposal_revision)
        raw_tasks = [deepcopy(task) for row in submissions for task in row["tasks"]]
        raw_tasks.sort(key=lambda row: (row["sequence"], row["agent_id"].casefold(), row["task_id"].casefold()))
        submitted_sequences = {row["task_id"].casefold(): row["sequence"] for row in raw_tasks}
        for index, row in enumerate(raw_tasks):
            row["sequence"] = index
        _, tasks, conflicts = _normalize({"workflow_id": workflow_id, "tasks": raw_tasks})
        with self._execution_lock:
            receipt = self._plan_bundle(workflow_id, tasks, conflicts)
        with self._journal_lock:
            entry.update(state="frozen", tasks=deepcopy(tasks), conflicts=deepcopy(conflicts))
            receipt["result"].update(proposal_revision=proposal_revision, proposal_state="frozen",
                read_only=False, native_read_only=True,
                frozen_tasks=[{key: row[key] for key in ("task_id", "agent_id", "sequence", "operation",
                    "depends_on", "parent_task_id", "source_task_id", "target_task_id", "arguments")}
                    | {"submitted_sequence": submitted_sequences[row["task_id"].casefold()]} for row in tasks],
                agent_ids=[row["agent_id"] for row in submissions],
                submissions=[{"agent_id": row["agent_id"], "submission_id": row["submission_id"],
                              "task_count": len(row["tasks"])} for row in submissions],
                proposal_durable=False,
                execution_contract="Execute with workflow_id, expected_proposal_revision and the returned workflow/session/build guards; omit tasks. Frozen proposals are retained in this MCP process only.")
            entry["plan_receipt"] = deepcopy(receipt)
            return deepcopy(receipt)

    def _plan_bundle(self, workflow_id: str, tasks: list[dict[str, Any]],
                     conflicts: list[dict[str, Any]]) -> dict[str, Any]:
        identity = _workflow_context(self.bridge)
        workflow_revision = self._workflow_revision(workflow_id, tasks, identity, conflicts)
        outputs: list[dict[str, Any]] = []
        unavailable = False
        for row in tasks:
            base = {key: row[key] for key in ("task_id", "agent_id", "sequence", "operation", "depends_on",
                                                "parent_task_id", "source_task_id", "target_task_id")}
            if conflicts:
                outputs.append({**base, "status": "conflict"})
                continue
            unresolved = [dep for dep in row["dependency_keys"] if not any(item["task_id"].casefold() == dep and item["status"] == "ready" for item in outputs)]
            if (row["parent_task_id"] is not None or row["source_task_id"] is not None
                    or row["target_task_id"] is not None or unresolved):
                outputs.append({**base, "status": "deferred_dependency_preview",
                                "dependencies": sorted(row["dependency_keys"]),
                                "reason": "The dependent native parent/source/target REF does not exist until its prerequisite is executed."})
                continue
            if unavailable:
                outputs.append({**base, "status": "not_previewed_after_failure"})
                continue
            try:
                _, preview = self._preview(row)
                outputs.append({**base, "status": "ready", "native_preview": preview})
            except Exception as exc:
                unavailable = True
                outputs.append({**base, "status": "blocked", "error": str(exc)[:1024],
                                "native_preview": deepcopy(getattr(exc, "native_preview", None))})
        after = _workflow_context(self.bridge)
        stable = identity == after
        if not stable:
            for output in outputs:
                if output["status"] == "ready":
                    output["status"] = "context_changed"
        ready = not conflicts and stable and all(row["status"] in {"ready", "deferred_dependency_preview"} for row in outputs)
        return {"ok": True, "version": "E3D21", "result": {
            "operation": "modeling_team_plan", "status": "ready" if ready else "blocked",
            "success": ready, "read_only": True, "synchronous_native_writes": False,
            "workflow_id": workflow_id, "workflow_revision": workflow_revision,
            "expected_session_id": identity["session_id"], "expected_bridge_build": identity["bridge_build"],
            "context": identity, "context_stable": stable, "agent_count": len({row["agent_id"] for row in tasks}),
            "task_count": len(tasks), "ordered_tasks": outputs, "conflicts": conflicts,
            "execution_contract": "Submit the identical workflow_id/tasks with the returned revision and context guards. The executor refreshes each exact native preview immediately before its one write, serializes every task in dependency/sequence order, verifies its native receipt/readback, and stops on the first failure or uncertain result. A dependent create/copy/transform is previewed only after its parent/source/target REF is verified.",
            "scope": "Agent identities and proposals are caller-supplied data, not authenticated principals. The in-process writer lock covers this coordinator instance only; it is not a cross-MCP-process E3D lease or database transaction. Other clients can still race and E3D may reject a busy native callback. Spatial clashes, engineering suitability and full transaction rollback are not certified."
        }}

    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        claim: dict[str, Any] = {}
        try:
            return self._execute_reserved(args, claim)
        except BaseException as exc:
            # Admission itself is protected too: cancellation on releasing the
            # state lock must not leave a never-started writer permanently live.
            if "entry" in claim:
                workflow_id, entry, tasks = claim["entry"]
                with self._journal_lock:
                    if self._journal.get(workflow_id) is entry and not entry["completed"]:
                        terminal = deepcopy(entry["result"])
                        result = terminal["result"]
                        attempted = result["execution_attempted"]
                        receipts = result["task_receipts"]
                        for row in tasks[len(receipts):]:
                            receipts.append({**{key: row[key] for key in (
                                "task_id", "agent_id", "sequence", "operation", "depends_on")},
                                "status": "not_started", "execution_attempted": False,
                                "native_write_state": "not_dispatched"})
                        terminal["ok"] = False
                        result.update(status="workflow_uncertain" if attempted else "preflight_rejected",
                            success=False, current_task_id=None, current_task_write_tool_call_dispatched=None,
                            retry_safe=False if attempted else None,
                            errors=[{"type": type(exc).__name__, "message": "Workflow admission interrupted; inspect its original status before resubmission."}])
                        if entry.get("proposal_revision") is not None:
                            self._proposals[workflow_id]["execution_completed"] = True
                        entry.update(completed=True, result=terminal)
            raise

    def _execute_reserved(self, args: dict[str, Any], claim: dict[str, Any]) -> dict[str, Any]:
        _need(isinstance(args, dict), "Workflow execution requires an object")
        proposal_revision = args.get("expected_proposal_revision")
        with self._journal_lock:
            if "expected_proposal_revision" in args:
                fields = {"workflow_id", "expected_proposal_revision", "expected_session_id",
                          "expected_bridge_build", "expected_workflow_revision"}
                _need(set(args) == fields, "Frozen execution requires proposal and context guards; omit tasks")
                workflow_id = _uuid(args["workflow_id"], "workflow_id")
                _need(isinstance(proposal_revision, str) and bool(_SHA.fullmatch(proposal_revision)),
                      "expected_proposal_revision must be a SHA-256 revision")
                proposal = self._proposals.get(workflow_id)
                _need(proposal is not None and proposal["state"] == "frozen",
                      "Workflow proposals must be sealed and planned before execution")
                _need(proposal["proposal_revision"] == proposal_revision, "Frozen proposal revision does not match")
                _need(proposal["plan_receipt"]["result"].get("success") is True,
                      "Frozen workflow plan is blocked; correct proposals with a new workflow_id")
                planned = proposal["plan_receipt"]["result"]
                _need(all(args.get(guard) == planned[field] for guard, field in (
                    ("expected_session_id", "expected_session_id"), ("expected_bridge_build", "expected_bridge_build"),
                    ("expected_workflow_revision", "workflow_revision"))),
                    "Execution guards must match the original frozen plan")
                tasks, conflicts = deepcopy(proposal["tasks"]), deepcopy(proposal["conflicts"])
            else:
                workflow_id, tasks, conflicts = _normalize(args, execution=True)
                _need(workflow_id not in self._proposals,
                      "Staged workflow requires expected_proposal_revision instead of caller-supplied tasks")
            canonical_tasks = [{key: row[key] for key in ("task_id", "agent_id", "sequence", "operation", "depends_on",
                                                           "parent_task_id", "source_task_id", "target_task_id", "arguments")} for row in tasks]
            fingerprint_material = {"workflow_id": workflow_id, "tasks": canonical_tasks,
                "expected_session_id": args.get("expected_session_id"), "expected_bridge_build": args.get("expected_bridge_build"),
                "expected_workflow_revision": args.get("expected_workflow_revision")}
            if proposal_revision is not None:
                fingerprint_material["proposal_revision"] = proposal_revision
            request_fingerprint = _digest(fingerprint_material)
            old = self._journal.get(workflow_id)
            if old is not None:
                _need(old["fingerprint"] == request_fingerprint, "workflow_id was already used for different tasks or guards")
                return {**deepcopy(old["result"]), "duplicate_workflow": True,
                        "workflow_in_progress": not old["completed"]}
            self._make_room(workflow_id)
            pending = {"ok": True, "version": "E3D21", "result": {"operation": "modeling_team_execute",
                "status": "workflow_in_progress", "success": False, "workflow_id": workflow_id,
                "synchronous_native_writes": True, "execution_attempted": False, "write_tool_call_dispatched": False,
                "any_write_tool_call_dispatched": False, "current_task_write_tool_call_dispatched": None,
                "native_write_state": None, "phase": "waiting_for_writer", "task_count": len(tasks),
                "completed_count": 0, "failed_count": 0, "current_task_id": None,
                "task_receipts": [], "errors": []}}
            if proposal_revision is not None:
                pending["result"].update(proposal_revision=proposal_revision, proposal_state="frozen")
            entry = {"fingerprint": request_fingerprint, "completed": False,
                     "proposal_revision": proposal_revision, "result": pending}
            claim["entry"] = (workflow_id, entry, tasks)
            self._journal[workflow_id] = entry
        final = deepcopy(pending)
        receipts: list[dict[str, Any]] = []
        execution_attempted = False
        revision: str | None = None
        try:
            with self._execution_lock:
                _need(not conflicts, "Workflow contains sibling name conflicts; resolve them before execution")
                _need(self.settings.allow_commands, "Multi-agent modeling execution requires E3D_MCP_ALLOW_COMMANDS=true")
                identity = _workflow_context(self.bridge)
                _need(identity.get("session_id") == args.get("expected_session_id")
                      and identity.get("bridge_build") == args.get("expected_bridge_build"),
                      "Expected E3D session/build changed after planning")
                revision = self._workflow_revision(workflow_id, tasks, identity, conflicts)
                _need(revision == args.get("expected_workflow_revision"),
                      "Workflow inputs or current E3D context changed after planning; nothing submitted")
                refs: dict[str, str] = {}
                ce = identity["current_element"]
                stopped = False
                for row in tasks:
                    task = {key: row[key] for key in ("task_id", "agent_id", "sequence", "operation", "depends_on",
                                                        "parent_task_id", "source_task_id", "target_task_id")}
                    if stopped:
                        receipts.append({**task, "status": "not_started"})
                        self._publish_progress(workflow_id, revision, receipts, len(tasks), None, execution_attempted)
                        continue
                    receipts.append({**task, "status": "running", "execution_attempted": False,
                                     "native_write_state": "not_dispatched"})
                    self._publish_progress(workflow_id, revision, receipts, len(tasks), row["task_id"], execution_attempted)
                    task_attempted = False
                    task_verified = False
                    verified_refno: str | None = None
                    task_reply: dict[str, Any] | None = None
                    task_request_id: str | None = None
                    try:
                        current = _workflow_context(self.bridge)
                        _need(all(current.get(key) == identity.get(key) for key in ("version", "session_id", "bridge_build", "project", "mdb"))
                              and current.get("current_element") == ce,
                              "E3D context changed before this task; remaining workflow stopped")
                        arguments, preview = self._preview(row, refs)
                        native = preview["result"]
                        execution = deepcopy(arguments)
                        execution["dry_run"] = False
                        execution["expected_revision"] = native["revision"]
                        if row["operation"] == "piping_route":
                            execution["expected_session_id"] = current["session_id"]
                            execution["request_id"] = str(uuid5(UUID(workflow_id), row["task_id"].casefold()))
                            task_request_id = execution["request_id"]
                        _need(_schema_matches(execution, self._handler(row)[2]),
                              f"Task {row['task_id']} execution payload does not satisfy its native schema")
                        task_attempted = execution_attempted = True
                        receipts[-1].update({"execution_attempted": True, "native_write_state": "awaiting_receipt",
                                             "request_id": task_request_id,
                                             "expected_revision": native["revision"], "preview": preview})
                        self._publish_progress(workflow_id, revision, receipts, len(tasks), row["task_id"], execution_attempted)
                        task_reply = self._handler(row)[1](execution)
                        valid_envelope = (isinstance(task_reply, dict) and task_reply.get("ok") is True
                                          and task_reply.get("version") == "E3D21"
                                          and isinstance(task_reply.get("result"), dict))
                        receipts[-1].update({"status": "verifying",
                                             "native_write_state": "receipt_received" if valid_envelope else "no_valid_receipt",
                                             "native_receipt": deepcopy(task_reply)})
                        self._publish_progress(workflow_id, revision, receipts, len(tasks), row["task_id"], execution_attempted)
                        _need(isinstance(task_reply, dict) and task_reply.get("ok") is True and task_reply.get("version") == "E3D21"
                              and isinstance(task_reply.get("result"), dict), f"Task {row['task_id']} native receipt unavailable")
                        result = task_reply["result"]
                        if row["operation"] == "piping_route":
                            _need(result.get("success") is True and result.get("status") == "created_verified"
                                  and result.get("readback_verified") is True and isinstance(result.get("created"), list)
                                  and result["created"] and isinstance(result["created"][0].get("refno"), str),
                                  f"Task {row['task_id']} route creation/readback failed")
                            refno = result["created"][0]["refno"]
                            ce_after = result.get("readback", {}).get("context", {}).get("current_element")
                        elif row["operation"] == "element_transform":
                            notification = result.get("notification_update")
                            element = result.get("element")
                            refno = result.get("refno")
                            _need(result.get("success") is True and result.get("write_attempted") is True
                                  and result.get("write_applied") is True and result.get("readback_verified") is True
                                  and result.get("revision_conflict") is not True
                                  and result.get("ce_unchanged") is True
                                  and isinstance(refno, str) and isinstance(element, dict)
                                  and element.get("refno") == refno
                                  and isinstance(notification, dict) and notification.get("attempted") is True
                                  and notification.get("completed") is True
                                  and notification.get("readback_verified") is True
                                  and notification.get("frame_identity_unchanged") is True,
                                  f"Task {row['task_id']} transform write/notification/readback was not verified")
                            ce_after = result.get("ce_after")
                        else:
                            element = result.get("element")
                            _need(result.get("success") is True and result.get("revision_conflict") is not True
                                  and result.get("parent_membership_verified") is True and result.get("ce_unchanged") is True
                                  and isinstance(element, dict) and isinstance(element.get("refno"), str),
                                  f"Task {row['task_id']} equipment create/copy readback failed")
                            if row["operation"] == "equipment_copy":
                                _need(result.get("captured_copy_identity_verified") is True and result.get("geometry_verified") is True,
                                      f"Task {row['task_id']} copied subtree identity/geometry was not verified")
                            refno = element["refno"]
                            ce_after = result.get("ce_after")
                        _need(isinstance(ce_after, str) and (row["operation"] == "piping_route" or ce_after == current["current_element"]),
                              f"Task {row['task_id']} changed CE unexpectedly; remaining workflow stopped")
                        task_verified = True
                        verified_refno = refno
                        receipts[-1].update({"output_refno": refno, "native_write_state": "verified_context_pending"})
                        self._publish_progress(workflow_id, revision, receipts, len(tasks), row["task_id"], execution_attempted)
                        after = _workflow_context(self.bridge)
                        if not (after.get("current_element") == ce_after and all(
                                after.get(key) == identity.get(key)
                                for key in ("version", "session_id", "bridge_build", "project", "mdb"))):
                            raise _WorkflowContextChanged(
                                "E3D context changed after this task; remaining workflow stopped")
                        refs[row["task_id"].casefold()] = refno
                        receipts[-1] = {**task, "status": _verified_status(row["operation"]), "output_refno": refno,
                                        "execution_attempted": True, "expected_revision": native["revision"],
                                        "native_write_state": "verified", "preview": preview,
                                        "native_receipt": task_reply}
                        ce = after["current_element"]
                        self._publish_progress(workflow_id, revision, receipts, len(tasks), None, execution_attempted)
                    except Exception as exc:
                        native = task_reply.get("result") if isinstance(task_reply, dict) else None
                        native_error = native if isinstance(native, dict) else task_reply
                        valid_envelope = (isinstance(task_reply, dict) and task_reply.get("ok") is True
                                          and task_reply.get("version") == "E3D21"
                                          and isinstance(task_reply.get("result"), dict))
                        outcome_uncertain = bool(task_attempted and (not valid_envelope
                            or getattr(exc, "outcome_uncertain", False)
                            or getattr(exc, "transport_error", False) or getattr(exc, "response_invalid", False)
                            or isinstance(native_error, dict) and (native_error.get("outcome_uncertain") is True
                                or native_error.get("creation_result_unknown") is True or native_error.get("status") == "outcome_uncertain"
                                or row["operation"] == "element_transform" and native_error.get("write_applied") is True
                                    and native_error.get("readback_verified") is not True)))
                        diagnostics = getattr(exc, "diagnostics", None)
                        verified_context_state = ("changed" if isinstance(exc, _WorkflowContextChanged)
                                                  else "unverified") if task_verified else None
                        receipts[-1] = {**task, "status": _verified_status(row["operation"],
                            "_context_" + verified_context_state) if task_verified else "outcome_uncertain" if outcome_uncertain else "failed",
                            "output_refno": verified_refno,
                            "execution_attempted": task_attempted, "request_id": task_request_id,
                            "native_write_state": "verified_context_" + verified_context_state if task_verified else "outcome_uncertain" if outcome_uncertain else "failed" if task_attempted else "not_dispatched",
                            "retry_safe": not task_attempted, "error": str(exc)[:2048], "diagnostics": diagnostics,
                            "preview": deepcopy(getattr(exc, "native_preview", None)),
                            "native_receipt": task_reply}
                        stopped = True
                        self._publish_progress(workflow_id, revision, receipts, len(tasks), None, execution_attempted)
                complete = len(receipts) == len(tasks) and all(row.get("status") in _COMPLETED_TASK_STATUSES for row in receipts)
                status = ("completed" if complete else "stopped_uncertain"
                          if any(row.get("status") == "outcome_uncertain" for row in receipts)
                          else "workflow_context_unverified"
                          if any(row.get("native_write_state") == "verified_context_unverified" for row in receipts)
                          else "preflight_rejected" if not execution_attempted else "partial_failure")
                try:
                    final_context = _workflow_context(self.bridge)
                    context_read_error = None
                    final_context_matches_expected = (final_context.get("current_element") == ce and all(
                        final_context.get(key) == identity.get(key)
                        for key in ("version", "session_id", "bridge_build", "project", "mdb")))
                    if complete and not final_context_matches_expected:
                        status = "workflow_context_changed"
                        complete = False
                except Exception as exc:
                    final_context = None
                    final_context_matches_expected = None
                    context_read_error = str(exc)[:1024]
                    if complete:
                        status = "workflow_context_unverified"
                    complete = False
                final = {"ok": complete, "version": "E3D21", "result": {"operation": "modeling_team_execute",
                    "status": status, "success": complete, "workflow_id": workflow_id, "workflow_revision": revision,
                    "synchronous_native_writes": True, "execution_attempted": any(row.get("execution_attempted", row.get("status") in _COMPLETED_TASK_STATUSES) for row in receipts),
                    "write_tool_call_dispatched": execution_attempted,
                    "any_write_tool_call_dispatched": execution_attempted,
                    "current_task_write_tool_call_dispatched": None,
                    "native_write_state": next((row.get("native_write_state") for row in reversed(receipts)
                                                 if row.get("execution_attempted")), None),
                    "retry_safe": False if execution_attempted else None,
                    "task_count": len(tasks), "completed_count": sum(row.get("status") in _COMPLETED_TASK_STATUSES for row in receipts),
                    "failed_count": sum(row.get("status") in _FAILED_TASK_STATUSES for row in receipts),
                    "task_receipts": receipts, "final_context": final_context, "final_context_error": context_read_error,
                    "final_context_matches_expected": final_context_matches_expected,
                    "scope": "One in-process coordinator serializes tasks and stops at the first failed/uncertain task. Completed earlier writes are retained; no automatic rollback, replay, save, clash check or cross-process lease."}}
        except Exception as exc:
            post_write_failure = execution_attempted
            final = {"ok": False, "version": "E3D21", "result": {"operation": "modeling_team_execute",
                "status": "workflow_uncertain" if post_write_failure else "preflight_rejected", "success": False,
                "workflow_id": workflow_id, "workflow_revision": revision, "synchronous_native_writes": True,
                "execution_attempted": execution_attempted, "write_tool_call_dispatched": execution_attempted,
                "any_write_tool_call_dispatched": execution_attempted,
                "current_task_write_tool_call_dispatched": None,
                "native_write_state": next((row.get("native_write_state") for row in reversed(receipts)
                                             if row.get("execution_attempted")), None),
                "task_count": len(tasks), "completed_count": sum(row.get("status") in _COMPLETED_TASK_STATUSES for row in receipts),
                "failed_count": sum(row.get("status") in _FAILED_TASK_STATUSES for row in receipts),
                "task_receipts": receipts,
                "errors": [{"type": type(exc).__name__, "message": str(exc)[:2048]}],
                "retry_safe": False if post_write_failure else None}}
        except BaseException as exc:
            current = receipts[-1] if receipts else None
            current_is_active = current is not None and current.get("status") in {"running", "verifying"}
            current_attempted = bool(current_is_active and current.get("execution_attempted"))
            verified_context_pending = bool(current_attempted
                                            and current.get("native_write_state") == "verified_context_pending")
            if current_is_active:
                current.update({"status": _verified_status(current["operation"], "_context_unverified") if verified_context_pending
                                else "outcome_uncertain" if current_attempted else "failed",
                                "native_write_state": "verified_context_unverified" if verified_context_pending
                                else "outcome_uncertain" if current_attempted else "not_dispatched",
                                "retry_safe": False if current_attempted else True,
                                "error": f"Workflow interrupted: {type(exc).__name__}"})
            while len(receipts) < len(tasks):
                row = tasks[len(receipts)]
                receipts.append({**{key: row[key] for key in ("task_id", "agent_id", "sequence", "operation", "depends_on",
                                                               "parent_task_id", "source_task_id", "target_task_id")},
                                 "status": "not_started", "execution_attempted": False,
                                 "native_write_state": "not_dispatched"})
            all_native_writes_known = (len(receipts) == len(tasks)
                                       and all(row.get("status") in _NATIVE_KNOWN_TASK_STATUSES for row in receipts))
            has_uncertain_write = any(row.get("status") == "outcome_uncertain" for row in receipts)
            previous_final = final.get("result", {}) if isinstance(final, dict) else {}
            already_completed = previous_final.get("status") == "completed" and all_native_writes_known
            if already_completed:
                interrupted_final = deepcopy(final)
            else:
                workflow_status = ("workflow_uncertain" if has_uncertain_write else
                    "workflow_context_unverified" if all_native_writes_known else
                    "partial_failure" if execution_attempted else "preflight_rejected")
                interrupted_final = {"ok": False, "version": "E3D21", "result": {
                    "operation": "modeling_team_execute", "status": workflow_status, "success": False,
                    "workflow_id": workflow_id, "workflow_revision": revision, "synchronous_native_writes": True,
                    "execution_attempted": execution_attempted, "write_tool_call_dispatched": execution_attempted,
                    "any_write_tool_call_dispatched": execution_attempted,
                    "current_task_write_tool_call_dispatched": None,
                    "task_count": len(tasks), "completed_count": sum(row.get("status") in _COMPLETED_TASK_STATUSES for row in receipts),
                    "failed_count": sum(row.get("status") in _FAILED_TASK_STATUSES for row in receipts),
                    "native_write_state": next((row.get("native_write_state") for row in reversed(receipts)
                                                 if row.get("execution_attempted")), None),
                    "task_receipts": deepcopy(receipts), "final_context": None,
                    "errors": [{"type": type(exc).__name__, "message": "Workflow interrupted; inspect its task receipts before any resubmission."}],
                    "retry_safe": False if execution_attempted else None}}
            final = interrupted_final
            raise
        finally:
            with self._journal_lock:
                if proposal_revision is not None:
                    final["result"].update(proposal_revision=proposal_revision, proposal_state="frozen")
                    self._proposals[workflow_id]["execution_completed"] = True
                self._journal[workflow_id].update(completed=True, result=deepcopy(final))
        return final

    def status(self, args: dict[str, Any]) -> dict[str, Any]:
        _need(isinstance(args, dict) and set(args) == {"workflow_id"},
              "Workflow status requires only workflow_id")
        workflow_id = _uuid(args["workflow_id"], "workflow_id")
        with self._journal_lock:
            entry = self._journal.get(workflow_id)
            snapshot = deepcopy(entry) if entry is not None else None
            proposal = self._proposals.get(workflow_id)
            if snapshot is None and proposal is not None:
                return self._proposal_response(workflow_id, proposal, "modeling_team_status")
            proposal_metadata = self._proposal_metadata(proposal) if proposal is not None else {}
        if snapshot is None:
            return {"ok": True, "version": "E3D21", "result": {
                "operation": "modeling_team_status", "status": "not_found", "success": False,
                "workflow_id": workflow_id, "workflow_state": "unknown", "journal_available": False,
                "journal_durable": False, "execution_attempted": None, "write_tool_call_dispatched": None,
                "any_write_tool_call_dispatched": None, "current_task_write_tool_call_dispatched": None,
                "native_write_state": "unknown", "retry_safe": False,
                "next_action": "inspect_native_model_before_resubmission",
                "reason": "No matching workflow is retained in this MCP process; it may not have been submitted, the process may have restarted, or a completed journal entry may have been evicted."}}
        stored = snapshot["result"].get("result", {})
        result = deepcopy(stored)
        result.update({"operation": "modeling_team_status", "workflow_status": stored.get("status"),
                       "workflow_state": stored.get("status"), "workflow_in_progress": not snapshot["completed"],
                       "write_tool_call_dispatched": stored.get("execution_attempted"),
                       "any_write_tool_call_dispatched": stored.get("execution_attempted"),
                       "current_task_write_tool_call_dispatched": stored.get("current_task_write_tool_call_dispatched"),
                       "journal_available": True, "journal_durable": False,
                       "next_action": "poll_status" if not snapshot["completed"] else
                       "done" if stored.get("status") == "completed" else
                       "correct_proposal_and_replan_with_new_workflow_id" if stored.get("status") == "preflight_rejected" else
                       "verify_context_before_continuing" if stored.get("status") in {
                           "workflow_context_unverified", "workflow_context_changed"} else
                       "inspect_native_model_before_resubmission"})
        result.update(proposal_metadata)
        return {"ok": True, "version": "E3D21", "result": result}


def register_modeling_team(server: McpServer, bridge: BridgeClient, settings: Settings) -> None:
    coordinator = ModelingTeamCoordinator(server, bridge, settings)
    task_schema = {"type": "object", "properties": {
        "task_id": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]{0,31}$"},
        "agent_id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"},
        "sequence": {"type": "integer", "minimum": 0, "maximum": 1000000},
        "operation": {"type": "string", "enum": list(_OPERATIONS)},
        "depends_on": {"type": "array", "maxItems": MAX_TASKS,
            "items": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]{0,31}$"}, "uniqueItems": True},
        "parent_task_id": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]{0,31}$"},
        "source_task_id": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]{0,31}$"},
        "target_task_id": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]{0,31}$",
                            "description": "For element_transform, identify an earlier workflow task whose verified output REF is the target. Otherwise put an existing explicit REF in arguments.refno."},
        "arguments": {"type": "object"}},
        "required": ["task_id", "agent_id", "sequence", "operation", "arguments"], "additionalProperties": False}
    common = {"workflow_id": {"type": "string", "format": "uuid"},
              "tasks": {"type": "array", "minItems": 1, "maxItems": MAX_TASKS, "items": task_schema}}
    submission_task_schema = deepcopy(task_schema)
    submission_task_schema["required"].remove("agent_id")
    server.add_tool(SUBMIT_TOOL,
        "Collect one data-only proposal from an Agent in this MCP process without contacting E3D. Tasks support piping_route, equipment_create, equipment_copy and guarded element_transform; transform writes use a fresh native expected_revision at execution. Requires workflow_id, "
        "agent_id, a canonical submission_id UUID and 1 to 16 tasks; task agent_id may be omitted and is supplied by the "
        "outer label. One immutable submission per Agent per workflow; exact duplicate submissions are idempotent, "
        "including after sealing. Different content or submission IDs for that Agent are rejected. Other Agent dependencies "
        "may arrive later and are checked globally at seal. At most 16 tasks across all Agents. Each Agent uses unique local "
        "sequence values; equal sequences across Agents are resolved deterministically at seal. All Agents must reach this "
        "same MCP process; separate stdio instances do not share proposals. State is bounded to 64 retained workflows and "
        "lost on restart; unexecuted proposals are never silently evicted. No model edit, PML, Agent startup or authentication.",
        {"type": "object", "properties": {
            "workflow_id": deepcopy(common["workflow_id"]),
            "agent_id": deepcopy(task_schema["properties"]["agent_id"]),
            "submission_id": {"type": "string", "format": "uuid"},
            "tasks": {"type": "array", "minItems": 1, "maxItems": MAX_TASKS, "items": submission_task_schema}},
         "required": ["workflow_id", "agent_id", "submission_id", "tasks"], "additionalProperties": False},
        coordinator.submit, read_only=False, destructive=False)
    plan_properties = {**common, "seal": {"type": "object", "properties": {
        "agent_ids": {"type": "array", "minItems": 1, "maxItems": MAX_TASKS, "uniqueItems": True,
                      "items": deepcopy(task_schema["properties"]["agent_id"])}},
        "required": ["agent_ids"], "additionalProperties": False}}
    server.add_tool(PLAN_TOOL,
        "Validate and merge up to 16 caller-supplied data-only proposals from route/equipment Agents into a deterministic, dependency-checked "
        "workflow. Supports e3d_piping_route_create, e3d_create_element, e3d_copy_element and guarded e3d_set_transform tasks; arbitrary PML "
        "is excluded. Every transform preview supplies the native expected_revision token that the workflow uses for its immediate guarded write. "
        "Preview calls each available exact native dry_run, retains blocked native_preview receipts for diagnosis, "
        "detects dependency cycles and sibling-name collisions, "
        "and emits a workflow_revision bound to session/build/project/MDB/CE and all task arguments. Use parent_task_id/source_task_id/target_task_id "
        "for a parent/source/transform target created earlier in the same workflow; such previews are explicitly deferred. Assign each task a "
        "unique sequence to make completion order deterministic. This tool does not start or authenticate Agents; agent_id is a caller-supplied label. "
        "Accepts either the legacy tasks bundle or seal={agent_ids:[...]} for proposals collected with e3d_modeling_team_submit, "
        "never both. Seal requires the exact submitted Agent roster; missing/unexpected Agents leave collection open without "
        "E3D reads. Seal freezes all submissions and returns proposal_revision plus the native plan. Duplicate seals reuse the "
        "same receipt or return proposal_planning. Local coordination state changes, but no model writes are issued. "
        "Does not establish geometric non-interference, clash clearance or engineering suitability.",
        {"type": "object", "properties": plan_properties, "required": ["workflow_id"],
         "oneOf": [{"required": ["tasks"]}, {"required": ["seal"]}], "additionalProperties": False},
        coordinator.plan, read_only=False, destructive=False)
    execute_properties = {**common,
        "expected_session_id": {"type": "string", "minLength": 1, "maxLength": 256},
        "expected_bridge_build": {"type": "string", "minLength": 1, "maxLength": 256},
        "expected_workflow_revision": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
        "expected_proposal_revision": {"type": "string", "pattern": r"^[0-9a-f]{64}$"}}
    server.add_tool(EXECUTE_TOOL,
        "Execute the exact reviewed multi-Agent route/equipment proposal bundle as one synchronous, single-writer workflow. "
        "Requires the plan's workflow_id, workflow_revision and expected session/build, plus command permission. "
        "For sealed proposals supply expected_proposal_revision and omit tasks; the executor uses the immutable stored bundle. "
        "For legacy plans supply the identical tasks and omit expected_proposal_revision. The two modes cannot be mixed. "
        "Each task obtains a fresh exact native preview immediately before its sole create/copy/route/transform call, so shared-parent and target revisions "
        "are refreshed after prior writes. Transform writes pass that preview's native expected_revision and require verified setter, Command.Update, frame identity and readback evidence. Tasks run in dependency/sequence order; parent/source/target references are resolved only from a "
        "verified earlier output DBREF. Every native receipt/readback and context boundary is checked before continuing. A failure or "
        "uncertain outcome stops dependent and later tasks; completed writes remain, with no automatic rollback or retry. Reusing a "
        "workflow_id returns its original receipt and does not replay. The Python writer lock is process-local; other MCP processes can "
        "still race, and E3D's native busy gate may reject a callback. This is not a cross-process lease or transaction. No automatic "
        "connect, save or clash check; does not certify engineering suitability.",
        {"type": "object", "properties": execute_properties,
         "required": ["workflow_id", "expected_session_id", "expected_bridge_build", "expected_workflow_revision"],
         "oneOf": [{"required": ["tasks"]}, {"required": ["expected_proposal_revision"]}],
         "additionalProperties": False}, coordinator.execute, destructive=False)
    server.add_tool(STATUS_TOOL,
        "Read collected Agent submissions, frozen plan, execution progress or the final receipt in this MCP process "
        "without contacting E3D or resubmitting tasks. "
        "Returns verified task receipts, the active task, and whether its MCP write handler was dispatched. execution_attempted "
        "means dispatch was attempted, not that a database write succeeded; native_write_state distinguishes not_dispatched, "
        "awaiting_receipt, receipt_received, no_valid_receipt, verified, verified_context_pending, verified_context_changed, "
        "verified_context_unverified, failed and outcome_uncertain. Aggregate any_write_tool_call_dispatched is cumulative; "
        "current_task_write_tool_call_dispatched is null between tasks. "
        "Standard stdio dispatch is synchronous, so status requests queue behind a running execute; concurrent "
        "in-process embedded callers can read progress snapshots. "
        "This is a read-only "
        "view of an in-process journal retained for at most 64 workflows; it is lost on process restart and does not "
        "provide a cross-process or durable status store.",
        {"type": "object", "properties": {"workflow_id": {"type": "string", "format": "uuid"}},
         "required": ["workflow_id"], "additionalProperties": False}, coordinator.status, read_only=True)
