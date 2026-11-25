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
MAX_TASKS = 16
_TASK_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}")
_AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_SHA = re.compile(r"[0-9a-f]{64}")
_OPERATIONS = {"piping_route": "e3d_piping_route_create",
               "equipment_create": "e3d_create_element",
               "equipment_copy": "e3d_copy_element"}
_CONTROLLED = {"dry_run", "expected_revision", "expected_session_id", "expected_bridge_build", "request_id",
               "capture_after", "capture_width", "capture_height"}


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


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
                                                       "parent_task_id", "source_task_id", "arguments"},
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
        _need(operation in _OPERATIONS, f"tasks[{index}].operation must be piping_route, equipment_create or equipment_copy")
        dependencies = raw.get("depends_on", [])
        _need(isinstance(dependencies, list) and len(dependencies) <= MAX_TASKS
              and all(isinstance(item, str) and bool(_TASK_ID.fullmatch(item)) for item in dependencies),
              f"tasks[{index}].depends_on must be an array of task IDs")
        _need(len({item.casefold() for item in dependencies}) == len(dependencies), f"tasks[{index}].depends_on contains duplicates")
        parent_task_id, source_task_id = raw.get("parent_task_id"), raw.get("source_task_id")
        for field, value in (("parent_task_id", parent_task_id), ("source_task_id", source_task_id)):
            _need(value is None or isinstance(value, str) and bool(_TASK_ID.fullmatch(value)), f"tasks[{index}].{field} is invalid")
        _need(operation == "equipment_copy" or source_task_id is None,
              "source_task_id is only supported by equipment_copy tasks")
        _need(operation != "piping_route" or (parent_task_id is None and source_task_id is None),
              "Piping routes require an explicit existing ZONE parent")
        arguments = raw.get("arguments")
        _need(isinstance(arguments, dict) and not set(arguments) & _CONTROLLED,
              f"tasks[{index}].arguments must omit execution controls, captures and caller-generated revisions")
        if parent_task_id is not None:
            _need("parent_refno" not in arguments, f"tasks[{index}] must use parent_task_id instead of parent_refno")
        if source_task_id is not None:
            _need("source_refno" not in arguments, f"tasks[{index}] must use source_task_id instead of source_refno")
        rows.append({"task_id": task_id, "agent_id": agent_id, "sequence": sequence,
                     "operation": operation, "depends_on": list(dependencies),
                     "parent_task_id": parent_task_id, "source_task_id": source_task_id,
                     "arguments": deepcopy(arguments)})
    for row in rows:
        implicit = [value for value in (row["parent_task_id"], row["source_task_id"]) if value is not None]
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


class ModelingTeamCoordinator:
    """One deterministic proposal merge and one synchronous in-process writer."""

    def __init__(self, server: McpServer, bridge: BridgeClient, settings: Settings):
        self.server, self.bridge, self.settings = server, bridge, settings
        self._execution_lock = Lock()
        self._journal_lock = Lock()
        self._journal: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def _workflow_revision(self, workflow_id: str, tasks: list[dict[str, Any]], identity: dict[str, Any],
                           conflicts: list[dict[str, Any]]) -> str:
        material = {"format": 1, "workflow_id": workflow_id, "tasks": [
            {key: row[key] for key in ("task_id", "agent_id", "sequence", "operation", "depends_on",
                                       "parent_task_id", "source_task_id", "arguments")} for row in tasks],
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
        _need(isinstance(reply, dict) and reply.get("ok") is True and reply.get("version") == "E3D21"
              and isinstance(reply.get("result"), dict), f"Task {row['task_id']} native preview envelope is unavailable")
        result = reply["result"]
        if row["operation"] == "piping_route":
            _need(result.get("success") is True and result.get("status") == "prepared"
                  and isinstance(result.get("revision"), str) and _SHA.fullmatch(result["revision"]),
                  f"Task {row['task_id']} native route preview failed")
        else:
            _need(result.get("success") is True and result.get("dry_run") is True
                  and isinstance(result.get("revision"), str) and _SHA.fullmatch(result["revision"])
                  and result.get("ce_unchanged") is True and result.get("revision_conflict") is not True,
                  f"Task {row['task_id']} native create/copy preview failed")
        return arguments, reply

    def plan(self, args: dict[str, Any]) -> dict[str, Any]:
        workflow_id, tasks, conflicts = _normalize(args)
        identity = _workflow_context(self.bridge)
        workflow_revision = self._workflow_revision(workflow_id, tasks, identity, conflicts)
        outputs: list[dict[str, Any]] = []
        unavailable = False
        for row in tasks:
            base = {key: row[key] for key in ("task_id", "agent_id", "sequence", "operation", "depends_on")}
            if conflicts:
                outputs.append({**base, "status": "conflict"})
                continue
            unresolved = [dep for dep in row["dependency_keys"] if not any(item["task_id"].casefold() == dep and item["status"] == "ready" for item in outputs)]
            if row["parent_task_id"] is not None or row["source_task_id"] is not None or unresolved:
                outputs.append({**base, "status": "deferred_dependency_preview",
                                "dependencies": sorted(row["dependency_keys"]),
                                "reason": "The dependent native parent/source REF does not exist until its prerequisite is executed."})
                continue
            if unavailable:
                outputs.append({**base, "status": "not_previewed_after_failure"})
                continue
            try:
                _, preview = self._preview(row)
                outputs.append({**base, "status": "ready", "native_preview": preview})
            except Exception as exc:
                unavailable = True
                outputs.append({**base, "status": "blocked", "error": str(exc)[:1024]})
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
            "execution_contract": "Submit the identical workflow_id/tasks with the returned revision and context guards. The executor refreshes each exact native preview immediately before its one write, serializes every task in dependency/sequence order, verifies its native receipt/readback, and stops on the first failure or uncertain result. A child of a new parent is previewed only after its parent REF is verified.",
            "scope": "Agent identities and proposals are caller-supplied data, not authenticated principals. The in-process writer lock covers this coordinator instance only; it is not a cross-MCP-process E3D lease or database transaction. Other clients can still race and E3D may reject a busy native callback. Spatial clashes, engineering suitability and full transaction rollback are not certified."
        }}

    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        workflow_id, tasks, conflicts = _normalize(args, execution=True)
        canonical_tasks = [{key: row[key] for key in ("task_id", "agent_id", "sequence", "operation", "depends_on",
                                                       "parent_task_id", "source_task_id", "arguments")} for row in tasks]
        request_fingerprint = _digest({"workflow_id": workflow_id, "tasks": canonical_tasks,
            "expected_session_id": args.get("expected_session_id"), "expected_bridge_build": args.get("expected_bridge_build"),
            "expected_workflow_revision": args.get("expected_workflow_revision")})
        with self._journal_lock:
            old = self._journal.get(workflow_id)
            if old is not None:
                _need(old["fingerprint"] == request_fingerprint, "workflow_id was already used for different tasks or guards")
                return {**deepcopy(old["result"]), "duplicate_workflow": True,
                        "workflow_in_progress": not old["completed"]}
            while len(self._journal) >= 64:
                evict = next((key for key, entry in self._journal.items() if entry["completed"]), None)
                _need(evict is not None, "Workflow journal is full")
                del self._journal[evict]
            pending = {"ok": True, "version": "E3D21", "result": {"operation": "modeling_team_execute",
                "status": "workflow_in_progress", "success": False, "workflow_id": workflow_id,
                "synchronous_native_writes": True, "execution_attempted": False, "task_receipts": [], "errors": []}}
            self._journal[workflow_id] = {"fingerprint": request_fingerprint, "completed": False, "result": pending}
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
                    task = {key: row[key] for key in ("task_id", "agent_id", "sequence", "operation", "depends_on")}
                    if stopped:
                        receipts.append({**task, "status": "not_started"})
                        continue
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
                        task_reply = self._handler(row)[1](execution)
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
                        after = _workflow_context(self.bridge)
                        _need(after.get("current_element") == ce_after and all(after.get(key) == identity.get(key)
                              for key in ("version", "session_id", "bridge_build", "project", "mdb")),
                              "E3D context changed after this task; remaining workflow stopped")
                        refs[row["task_id"].casefold()] = refno
                        receipts.append({**task, "status": "created_verified", "output_refno": refno,
                                         "expected_revision": native["revision"], "preview": preview, "native_receipt": task_reply})
                        ce = after["current_element"]
                    except Exception as exc:
                        native = task_reply.get("result") if isinstance(task_reply, dict) else None
                        native_error = native if isinstance(native, dict) else task_reply
                        outcome_uncertain = bool(getattr(exc, "outcome_uncertain", False)
                            or getattr(exc, "transport_error", False) or getattr(exc, "response_invalid", False)
                            or isinstance(native_error, dict) and (native_error.get("outcome_uncertain") is True
                                or native_error.get("creation_result_unknown") is True or native_error.get("status") == "outcome_uncertain"))
                        diagnostics = getattr(exc, "diagnostics", None)
                        receipts.append({**task, "status": "created_verified_context_changed" if task_verified else "outcome_uncertain" if outcome_uncertain else "failed",
                            "output_refno": verified_refno,
                            "execution_attempted": task_attempted, "request_id": task_request_id,
                            "retry_safe": False, "error": str(exc)[:2048], "diagnostics": diagnostics,
                            "native_receipt": task_reply})
                        stopped = True
                complete = len(receipts) == len(tasks) and all(row.get("status") == "created_verified" for row in receipts)
                status = "completed" if complete else "stopped_uncertain" if any(row.get("status") == "outcome_uncertain" for row in receipts) else "partial_failure"
                try:
                    final_context = _workflow_context(self.bridge)
                    context_read_error = None
                except Exception as exc:
                    final_context = None
                    context_read_error = str(exc)[:1024]
                    complete = False
                    status = "stopped_uncertain"
                final = {"ok": complete, "version": "E3D21", "result": {"operation": "modeling_team_execute",
                    "status": status, "success": complete, "workflow_id": workflow_id, "workflow_revision": revision,
                    "synchronous_native_writes": True, "execution_attempted": any(row.get("execution_attempted", row.get("status") == "created_verified") for row in receipts),
                    "retry_safe": False if execution_attempted else None,
                    "task_count": len(tasks), "completed_count": sum(row.get("status") == "created_verified" for row in receipts),
                    "failed_count": sum(row.get("status") in {"failed", "outcome_uncertain"} for row in receipts),
                    "task_receipts": receipts, "final_context": final_context, "final_context_error": context_read_error,
                    "scope": "One in-process coordinator serializes tasks and stops at the first failed/uncertain task. Completed earlier writes are retained; no automatic rollback, replay, save, clash check or cross-process lease."}}
        except Exception as exc:
            post_write_failure = execution_attempted or bool(receipts)
            final = {"ok": False, "version": "E3D21", "result": {"operation": "modeling_team_execute",
                "status": "workflow_uncertain" if post_write_failure else "preflight_rejected", "success": False,
                "workflow_id": workflow_id, "workflow_revision": revision, "synchronous_native_writes": True,
                "execution_attempted": execution_attempted, "task_receipts": receipts,
                "errors": [{"type": type(exc).__name__, "message": str(exc)[:2048]}],
                "retry_safe": False if post_write_failure else None}}
        finally:
            with self._journal_lock:
                self._journal[workflow_id].update(completed=True, result=deepcopy(final))
        return final


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
        "arguments": {"type": "object"}},
        "required": ["task_id", "agent_id", "sequence", "operation", "arguments"], "additionalProperties": False}
    common = {"workflow_id": {"type": "string", "format": "uuid"},
              "tasks": {"type": "array", "minItems": 1, "maxItems": MAX_TASKS, "items": task_schema}}
    server.add_tool(PLAN_TOOL,
        "Merge up to 16 data-only proposals from independent route/equipment Agents into a deterministic, dependency-checked "
        "workflow. Supports e3d_piping_route_create, e3d_create_element and e3d_copy_element only; transforms and arbitrary PML "
        "are excluded. Preview calls each available exact native dry_run, detects dependency cycles and sibling-name collisions, "
        "and emits a workflow_revision bound to session/build/project/MDB/CE and all task arguments. Use parent_task_id/source_task_id "
        "for a new parent/source created earlier in the same workflow; such previews are explicitly deferred. Assign each task a "
        "unique sequence to make completion order deterministic. Agent IDs are labels supplied by the caller, not authenticated identities. "
        "No writes are issued. Does not establish geometric non-interference, clash clearance or engineering suitability.",
        {"type": "object", "properties": common, "required": ["workflow_id", "tasks"], "additionalProperties": False},
        coordinator.plan, read_only=True)
    execute_properties = {**common,
        "expected_session_id": {"type": "string", "minLength": 1, "maxLength": 256},
        "expected_bridge_build": {"type": "string", "minLength": 1, "maxLength": 256},
        "expected_workflow_revision": {"type": "string", "pattern": r"^[0-9a-f]{64}$"}}
    server.add_tool(EXECUTE_TOOL,
        "Execute the exact reviewed multi-Agent route/equipment proposal bundle as one synchronous, single-writer workflow. "
        "Requires the plan's identical workflow_id/tasks, workflow_revision and expected session/build, plus command permission. "
        "Each task obtains a fresh exact native preview immediately before its sole create/copy/route call, so shared-parent revisions "
        "are refreshed after prior writes. Tasks run in dependency/sequence order; child-parent references are resolved only from a "
        "verified earlier output DBREF. Every native receipt/readback and context boundary is checked before continuing. A failure or "
        "uncertain outcome stops dependent and later tasks; completed writes remain, with no automatic rollback or retry. Reusing a "
        "workflow_id returns its original receipt and does not replay. The Python writer lock is process-local; other MCP processes can "
        "still race, and E3D's native busy gate may reject a callback. This is not a cross-process lease or transaction. No automatic "
        "transform, connect, save or clash check; does not certify engineering suitability.",
        {"type": "object", "properties": execute_properties,
         "required": ["workflow_id", "tasks", "expected_session_id", "expected_bridge_build", "expected_workflow_revision"],
         "additionalProperties": False}, coordinator.execute, destructive=False)
