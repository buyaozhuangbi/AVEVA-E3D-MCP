"""Bounded, read-only connection/project observations with explicit limits.

The health samples bracket the project request. They neither bind that response
to a session nor make the reads atomic; no readiness or permission is promised
after the final sample.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .config import Settings


_IDENTITY_FIELDS = ("version", "session_id", "bridge_build")
_ERROR_FIELDS = {"error", "errors", "read_error", "read_errors", "diagnostic_error",
                 "native_attribute_errors", "typed_read_errors"}
_GATES = (
    (("connected",), True),
    (("state", "database_ready"), True),
    (("state", "busy"), False),
    (("state", "recovery_required"), False),
    (("state", "native_database_fault", "has_fault"), False),
    (("state", "execution", "dispatcher_ready"), True),
    (("state", "execution", "native_operation_active"), False),
    (("state", "native_dialogs", "complete"), True),
    (("state", "native_dialogs", "standard_dialog_present"), False),
    (("state", "pml_alerts", "complete"), True),
    (("state", "pml_alerts", "alert_present"), False),
)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _reported_errors(value: Any, path: str = "response") -> list[str]:
    paths = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key in _ERROR_FIELDS:
                if item:
                    paths.append(child)
            else:
                paths.extend(_reported_errors(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(_reported_errors(item, f"{path}[{index}]"))
    return paths


def _read(bridge: Any, method: str, stage: str, result: dict[str, Any]) -> Any:
    result["calls"].append(stage)
    try:
        response = getattr(bridge, method)()
    except Exception as exc:
        error = {"stage": stage, "code": "read_error", "type": type(exc).__name__,
                 "message": str(exc)}
        for name in ("http_status", "transport_error", "response_invalid", "diagnostics"):
            if hasattr(exc, name):
                error[name] = deepcopy(getattr(exc, name))
        result["evidence"][stage] = {"exception": deepcopy(error)}
        result["errors"].append(error)
        return None
    result["evidence"][stage] = deepcopy(response)
    if not isinstance(response, dict) or response.get("ok") is not True:
        result["errors"].append({"stage": stage, "code": "invalid_response",
                                 "message": "Read did not return an ok=true object."})
        return None
    paths = _reported_errors(response)
    if paths:
        result["errors"].append({"stage": stage, "code": "reported_read_error",
                                 "message": "Read reported diagnostic errors.", "paths": paths})
        return None
    return response


def _readiness(value: Any, version: str, mode: str) -> tuple[str, list[dict[str, str]]]:
    reasons: list[dict[str, str]] = []
    explicit_failure = False
    for path, expected in _GATES:
        item = value
        for key in path:
            item = item.get(key) if isinstance(item, dict) else None
        name = ".".join(path)
        if type(item) is not bool:
            reasons.append({"path": name, "code": "unknown",
                            "message": "Required boolean observation is missing or malformed."})
        elif item is not expected:
            if path[-1] == "complete":
                reasons.append({"path": name, "code": "incomplete_observation",
                                "message": "Incomplete observations cannot establish absence of dialogs/alerts."})
            else:
                explicit_failure = True
                reasons.append({"path": name, "code": "not_ready",
                                "message": f"Observed {item}; readiness requires {expected}."})
    for field in _IDENTITY_FIELDS:
        item = value.get(field) if isinstance(value, dict) else None
        if not _text(item):
            reasons.append({"path": field, "code": "unknown",
                            "message": "Nonempty native identity is unavailable."})
        elif field == "version" and item != version:
            explicit_failure = True
            reasons.append({"path": field, "code": "version_mismatch",
                            "message": "Observed version differs from the configured instance."})
    if mode != "bridge":
        reasons.append({"path": "mcp.mode", "code": "native_unavailable",
                        "message": "Mock observations cannot establish live native readiness."})
    state = ("unknown" if mode != "bridge" else "not_ready" if explicit_failure
             else "unknown" if reasons else "ready")
    return state, reasons


def _project_context(value: Any, version: str, result: dict[str, Any]) -> dict[str, Any]:
    context = {"project": None, "mdb": None, "user": None, "current_element": None,
               "fresh": False, "available_fields": [], "issues": []}
    if value is None:
        return context
    project = value.get("project")
    if not isinstance(project, dict):
        result["errors"].append({"stage": "project", "code": "invalid_response",
                                 "message": "Project read has no project object."})
        return context
    if value.get("version") != version:
        context["issues"].append({"path": "version", "code": "version_mismatch" if
                                  _text(value.get("version")) else "unknown"})
    for field, expected in (("connected", True), ("recovery_only", False)):
        item = project.get(field)
        if item is not expected:
            context["issues"].append({"path": f"project.{field}",
                                      "code": "unavailable" if type(item) is bool else "unknown"})
    # Successful native project reads omit busy; an absent flag does not invent
    # an explicit false observation, but an asserted/malformed one is rejected.
    if "busy" in project and project["busy"] is not False:
        context["issues"].append({"path": "project.busy",
                                  "code": "unavailable" if project["busy"] is True else "unknown"})
    for field in ("name", "mdb"):
        if not _text(project.get(field)):
            context["issues"].append({"path": f"project.{field}", "code": "unknown"})
    if context["issues"]:
        # Cached/busy/partial identity remains in raw evidence, never in fresh
        # context fields where a caller might accidentally treat it as current.
        return context
    context["fresh"] = True
    for source, destination in (("name", "project"), ("mdb", "mdb"), ("user", "user"),
                                ("current_element", "current_element")):
        if _text(project.get(source)):
            context[destination] = project[source]
            context["available_fields"].append(destination)
    return context


def _coherence(before: Any, after: Any) -> dict[str, Any]:
    identities = {}
    for label, value in (("before", before), ("after", after)):
        identities[label] = {field: value.get(field) if isinstance(value, dict) and
                             _text(value.get(field)) else None for field in _IDENTITY_FIELDS}
    changed = [field for field in _IDENTITY_FIELDS
               if identities["before"][field] is not None and identities["after"][field] is not None
               and identities["before"][field] != identities["after"][field]]
    complete = all(item is not None for sample in identities.values() for item in sample.values())
    return {"status": "changed" if changed else "consistent" if complete else "unverified",
            "atomic": False, "project_session_bound": False, "identity_fields": list(_IDENTITY_FIELDS),
            "changed_fields": changed, **identities,
            "scope": "Health identity samples before and after the project request only; "
                     "the project response carries no session/build identity."}


def context_snapshot(bridge: Any, settings: Settings) -> dict[str, Any]:
    """Observe context in at most health/project/health order; never retry.

    ``ok`` and ``workflow_completed`` describe successful attempted reads, even
    when readiness is explicitly false or unknown. ``context_complete`` means
    fresh project/MDB identity was read between matching ready health samples;
    optional user/current-element availability is reported independently.
    """
    version = settings.version or settings.default_version
    result: dict[str, Any] = {
        "schema": 1, "workflow_completed": False, "context_complete": False,
        "readiness": {"before": "unknown", "after": "unknown", "reasons": {
            "before": [], "after": [{"path": "health_after", "code": "not_sampled",
                                      "message": "No final health sample was requested."}]}},
        "session_coherence": _coherence(None, None),
        "context": {"project": None, "mdb": None, "user": None, "current_element": None,
                    "fresh": False, "available_fields": [], "issues": []},
        "evidence": {}, "errors": [], "calls": [], "skipped": [],
        "read_only": True,
        "scope": {"maximum_bridge_calls": 3, "retries": 0, "model_writes": 0,
                  "view_setters": 0, "captures": 0, "commands": 0,
                  "future_readiness_guaranteed": False, "write_permission_verified": False,
                  "empty_pml_input_stack_verified": False,
                  "readiness": "Bridge execution and observed standard/Druid error windows only; "
                               "no future readiness, write permission, SAVEWORK safety or empty PML input stack proof."},
    }
    before = _read(bridge, "health", "health_before", result)
    state, reasons = _readiness(before, version, settings.mode)
    result["readiness"]["before"] = state
    result["readiness"]["reasons"]["before"] = reasons
    after = None
    if state == "ready":
        project = _read(bridge, "project", "project", result)
        # Always bracket an attempted project read, including a raised exception
        # or malformed project response. No other read is admitted here.
        after = _read(bridge, "health", "health_after", result)
        state, reasons = _readiness(after, version, settings.mode)
        result["readiness"]["after"] = state
        result["readiness"]["reasons"]["after"] = reasons
        result["context"] = _project_context(project, version, result)
    else:
        result["skipped"] = [
            {"stage": "project", "reason": "Initial native readiness was not positively established."},
            {"stage": "health_after", "reason": "No project read was attempted."},
        ]
    result["session_coherence"] = _coherence(before, after)
    result["workflow_completed"] = not result["errors"]
    result["context_complete"] = (result["workflow_completed"] and result["context"]["fresh"]
        and result["readiness"]["before"] == result["readiness"]["after"] == "ready"
        and result["session_coherence"]["status"] == "consistent")
    return {"ok": result["workflow_completed"], "version": version,
            "mcp": {"mode": settings.mode, "commands_enabled": settings.allow_commands}, "result": result}
