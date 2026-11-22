"""Remove verified duplicate diagnostic samples without new native reads."""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from .clipping_observation import clipping_observation_result
from .protocol import ImageToolResult
from .view_observation import view_observation_result


_UNCHANGED = (
    "session_build_version_unchanged", "project_mdb_user_unchanged",
    "current_element_unchanged", "camera_and_top_level_drawlist_samples_unchanged",
    "selection_samples_unchanged", "observed_window_unchanged",
)
_VOLATILE_STATUS_PATHS = (
    ("state", "execution", "dispatch_count"),
    ("state", "native_dialogs", "elapsed_ms"),
    ("state", "pml_alerts", "elapsed_ms"),
)
_ERROR_FIELDS = {
    "error", "errors", "read_error", "read_errors", "diagnostic_error",
    "native_attribute_errors", "typed_read_errors",
}
_OBSERVATION_UNKNOWN = {
    "view_observation_unverified", "selection_observation_unverified",
    "clipping_observation_unverified", "capture_unverified", "observed_context_changed",
}
_DETAIL = (
    "Only verified duplicate samples were omitted. Request evidence_detail='full' "
    "for a new full diagnostic read; it is a new observation, not retrieval or "
    "reproduction of these omitted samples. If result.diagnostic_receipt reports "
    "status='stored', use e3d_diagnostic_receipt with its receipt_id to retrieve "
    "the same historical full evidence while retained; otherwise these samples "
    "have no lookup token. Retention requires explicit retain_receipt=true."
)


class _Fallback(Exception):
    pass


def _ensure(condition: bool, reason: str = "malformed_evidence") -> None:
    if not condition:
        raise _Fallback(reason)


def _object(value: Any) -> dict[str, Any]:
    _ensure(isinstance(value, dict))
    return value


def _same(left: Any, right: Any) -> bool:
    """Compare JSON values without merging bool/int or int/float identities."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same(item, right[key]) for key, item in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    if isinstance(left, float):
        return math.isfinite(left) and math.isfinite(right) and left == right
    return isinstance(left, (str, int, bool, type(None))) and left == right


def _problem(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _ERROR_FIELDS and item:
                return True
            if key in {"has_fault", "native_fault"} and item is True:
                return True
            if (key.endswith("_unchanged") or key in {"stable", "consistent"}) and item is False:
                return True
            if key == "changed_fields" and item:
                return True
            if _problem(item):
                return True
    elif isinstance(value, list):
        return any(_problem(item) for item in value)
    return False


def _status(value: Any) -> dict[str, Any]:
    status = _object(value)
    state = _object(status.get("state"))
    execution = _object(state.get("execution"))
    fault = _object(state.get("native_database_fault"))
    _ensure(status.get("ok") is True and status.get("connected") is True and
            status.get("version") == "E3D21" and state.get("database_ready") is True and
            state.get("busy") is False and state.get("recovery_required") is False and
            fault.get("has_fault") is False and execution.get("dispatcher_ready") is True and
            execution.get("native_operation_active") is False)
    _ensure(all(isinstance(status.get(key), str) and status[key].strip()
                for key in ("session_id", "bridge_build")))
    for key, absent in (("native_dialogs", "standard_dialog_present"), ("pml_alerts", "alert_present")):
        dialogs = _object(state.get(key))
        _ensure(dialogs.get("complete") is True and dialogs.get(absent) is False)
    return status


def _normalized_status(value: dict[str, Any]) -> dict[str, Any]:
    # Only these observed counters/timings may vary when every diagnostic value
    # remains the same. Unknown fields are retained in the equality comparison.
    status = deepcopy(value)
    for path in _VOLATILE_STATUS_PATHS:
        parent = status
        for key in path[:-1]:
            parent = parent[key]
        if path[-1] in parent:
            value = parent[path[-1]]
            if path[-1] == "dispatch_count":
                _ensure(type(value) is int and value >= 0)
            else:
                _ensure(type(value) in (int, float) and math.isfinite(value) and value >= 0)
            # Preserve key presence, so a newly appearing or disappearing field
            # is not silently merged with the baseline's response structure.
            parent[path[-1]] = None
    return status


def _project(value: Any) -> dict[str, Any]:
    response = _object(value)
    project = _object(response.get("project"))
    _ensure(response.get("ok") is True and response.get("version") == "E3D21" and
            project.get("connected") is True and project.get("recovery_only") is False and
            project.get("busy") is not True and all(
                isinstance(project.get(key), str) and project[key].strip()
                for key in ("name", "mdb", "user", "current_element")))
    return response


def _same_status(value: Any, baseline: dict[str, Any]) -> None:
    _ensure(_same(_normalized_status(_status(value)), baseline), "evidence_changed")


def _same_project(value: Any, baseline: dict[str, Any]) -> None:
    _ensure(_same(_project(value), baseline), "evidence_changed")


def _verify_clipping(result: dict[str, Any], evidence: dict[str, Any],
                     observations: dict[str, Any]) -> None:
    requested = result.get("clipping_requested", False)
    _ensure(type(requested) is bool, "clipping_unverified")
    keys = ("baseline_clipping", "final_clipping")
    if not requested:
        _ensure(not any(key in evidence for key in keys) and
                "clipping_samples_unchanged" not in observations, "clipping_unverified")
        return
    _ensure(observations.get("clipping_samples_unchanged") is True, "clipping_unverified")
    comparison = clipping_observation_result(
        evidence.get("baseline_clipping"), evidence.get("final_clipping"),
        evidence.get("baseline_view"), evidence.get("final_view"),
        complete=result.get("workflow_completed") is True,
    )
    _ensure(comparison.get("complete") is True and comparison.get("unchanged") is True and
            not comparison.get("read_errors"), "clipping_unverified")
    # The shared native contract checks known fields. Preserve and compare every
    # original field as well, so future evidence cannot be silently normalized.
    _ensure(_same(evidence.get("baseline_clipping"), evidence.get("final_clipping")),
            "evidence_changed")


def _capture_plan(
    evidence: dict[str, Any], baseline_status: dict[str, Any], current_element: str,
) -> tuple[list[str], dict[str, int]]:
    capture = _object(evidence.get("capture"))
    result = _object(capture.get("result"))
    observation = _object(result.get("observation"))
    native = _object(result.get("native_capture_observation"))
    _ensure(capture.get("ok") is True and capture.get("version") == "E3D21" and
            result.get("success") is True and observation.get("status") == "consistent" and
            observation.get("complete") is True and observation.get("consistent") is True and
            observation.get("changed_fields") == [] and observation.get("read_errors") == {} and
            native.get("complete") is True and native.get("view_object_unchanged") is True and
            native.get("read_errors") == {}, "capture_unverified")
    # This branch is a duplicate of native_capture_observation, not a separate
    # bitmap callback. Refuse to remove an unexpectedly different copy.
    _ensure(_same(observation.get("native"), native), "evidence_changed")
    before_envelope = _object(observation.get("before"))
    before = _object(before_envelope.get("result"))
    _ensure(set(before_envelope) <= {"ok", "version", "result"} and
            before_envelope.get("ok") is True and before_envelope.get("version") == "E3D21")
    _ensure(set(before) <= {"success", "stable", "complete", "states", "context_before",
                           "context_after", "changed_fields", "read_errors", "scope"})
    _ensure(before.get("success") is True and before.get("stable") is True and
            before.get("complete") is True and before.get("changed_fields") == [] and
            before.get("read_errors") == {}, "capture_unverified")
    if "scope" in before:
        _ensure(_same(before["scope"], observation.get("scope")), "evidence_changed")
    previous = before.get("states")
    _ensure(isinstance(previous, list) and 2 <= len(previous) <= 10)
    frames = [_object(evidence.get(key)) for key in ("baseline_view", "pre_capture_view", "final_view")]
    frames += [_object(frame) for frame in previous]
    frames.append(_object(observation.get("after")))
    baseline_frame = frames[0]
    for frame in frames:
        _ensure(_same(frame, baseline_frame), "evidence_changed")
    baseline_camera = _object(baseline_frame.get("result"))
    native_frames = [_object(native.get(key)) for key in ("before_refresh", "before_bitmap", "after_bitmap")]
    _ensure(all(_same(frame, baseline_camera) for frame in native_frames), "evidence_changed")
    _ensure(all(_object(frame.get("result")).get("current_element_refno") == current_element
                for frame in frames) and
            all(frame.get("current_element_refno") == current_element for frame in native_frames), "evidence_changed")
    comparison = view_observation_result(
        frames + [{"ok": True, "result": frame} for frame in native_frames],
        evidence["initial_status"], evidence["final_status"], {},
    )
    _ensure(comparison.get("complete") is True and comparison.get("stable") is True, "capture_unverified")
    for status in (before.get("context_before"), before.get("context_after"), observation.get("context_after")):
        _same_status(status, baseline_status)
    paths = ["result.evidence.capture.result.observation." + key
             for key in ("before", "after", "context_after", "native")]
    paths += ["result.evidence.capture.result.native_capture_observation." + key
              for key in ("before_refresh", "before_bitmap", "after_bitmap")]
    return paths, {
        "capture_pre_view_samples": len(previous), "capture_post_view_samples": 1,
        "capture_native_view_samples": 3, "capture_context_samples": 3,
        "duplicate_native_view_samples": 3,
    }


def _plan(result: dict[str, Any]) -> tuple[dict[str, Any], list[str], dict[str, int]]:
    _ensure(result.get("success") is True and result.get("workflow_completed") is True,
            "workflow_not_completed")
    _ensure(isinstance(result.get("errors"), list), "malformed_result")
    _ensure(not result["errors"], "errors_present")
    observations = _object(result.get("observations"))
    _ensure(all(observations.get(key) is True for key in _UNCHANGED) and
            all(value is True for key, value in observations.items() if key != "capture"),
            "observations_unverified")
    unknown = result.get("unknown")
    _ensure(isinstance(unknown, list), "malformed_result")
    _ensure(not any(isinstance(row, dict) and row.get("code") in _OBSERVATION_UNKNOWN for row in unknown),
            "observations_unverified")
    evidence = _object(result.get("evidence"))
    _ensure(not _problem(evidence), "evidence_has_errors")
    _verify_clipping(result, evidence, observations)
    initial_status = _normalized_status(_status(evidence.get("initial_status")))
    initial_project = _project(evidence.get("initial_project"))
    _same_status(evidence.get("final_status"), initial_status)
    _same_project(evidence.get("final_project"), initial_project)
    gates = evidence.get("gates")
    _ensure(isinstance(gates, list) and len(gates) >= 2)
    stage_order = []
    for row in gates:
        row = _object(row)
        stage = row.get("stage")
        _ensure(set(row) == {"stage", "status", "project"} and
                isinstance(stage, str) and bool(stage.strip()) and stage not in stage_order)
        _same_status(row["status"], initial_status)
        _same_project(row["project"], initial_project)
        stage_order.append(stage)
    _ensure(stage_order[0] == "initial" and stage_order[-1] == "final")
    summary = {"stage_order": stage_order, "count": len(gates), "details_omitted": True}
    paths = ["result.evidence.gates"]
    counts = {"gate_stages": len(gates), "gate_status_samples": len(gates), "gate_project_samples": len(gates),
              "capture_pre_view_samples": 0, "capture_post_view_samples": 0,
              "capture_native_view_samples": 0, "capture_context_samples": 0,
              "duplicate_native_view_samples": 0}
    capture = _object(observations.get("capture"))
    if result.get("capture_requested") is False:
        _ensure(result.get("capture_attempted") is False and capture.get("status") == "not_requested" and
                capture.get("consistent") is None and capture.get("complete") is None and "capture" not in evidence,
                "capture_unverified")
    else:
        _ensure(result.get("capture_requested") is True and result.get("capture_attempted") is True and
                capture.get("status") == "consistent" and capture.get("consistent") is True and
                capture.get("complete") is True, "capture_unverified")
        capture_paths, capture_counts = _capture_plan(
            evidence, initial_status, initial_project["project"]["current_element"],
        )
        paths.extend(capture_paths)
        counts.update(capture_counts)
    return summary, paths, counts


def compact_diagnosis(value: dict[str, Any] | ImageToolResult) -> dict[str, Any] | ImageToolResult:
    """Copy a receipt and compact only positively verified duplicate evidence.

    Failures and malformed observations retain their complete evidence. An input
    without an object result cannot accept compaction metadata safely and is
    returned unchanged. Image data is never decoded, transformed or regenerated.
    """
    envelope = value.metadata if isinstance(value, ImageToolResult) else value
    if not isinstance(envelope, dict) or not isinstance(envelope.get("result"), dict):
        return value
    try:
        copied = deepcopy(envelope)
    except Exception:
        return value
    result = copied["result"]
    if "evidence_compaction" not in result:
        try:
            _ensure(copied.get("ok") is True, "workflow_not_completed")
            summary, paths, counts = _plan(result)
        except _Fallback as exc:
            result["evidence_compaction"] = {"requested": "compact", "applied": False, "reason": str(exc)}
        except Exception:
            # Plan first and mutate later: a malformed candidate cannot leave a
            # half-compacted receipt behind or lose previously collected data.
            result["evidence_compaction"] = {
                "requested": "compact", "applied": False, "reason": "malformed_evidence"}
        else:
            evidence = result["evidence"]
            evidence["gates"] = summary
            if result["capture_requested"]:
                captured = evidence["capture"]["result"]
                for key in ("before", "after", "context_after", "native"):
                    del captured["observation"][key]
                for key in ("before_refresh", "before_bitmap", "after_bitmap"):
                    del captured["native_capture_observation"][key]
            result["evidence_compaction"] = {
                "requested": "compact", "applied": True, "reason": "verified_unchanged",
                "omitted_paths": paths, "sample_counts": counts,
                "ignored_status_paths": [".".join(path) for path in _VOLATILE_STATUS_PATHS],
                "sample_count_scope": "Logical sample entries; observation.native duplicates the same three native bitmap samples, not three additional callbacks.",
                "detail": _DETAIL,
            }
    if isinstance(value, ImageToolResult):
        return ImageToolResult(value.data, value.mime_type, copied)
    return copied
