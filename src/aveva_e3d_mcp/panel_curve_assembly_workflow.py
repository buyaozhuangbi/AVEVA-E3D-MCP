"""Read bounded independent PANE definition curves in one observed WORLD frame.

Each source retains its existing two-read/DESP evidence. Assembly context checks
compare every source with the operation's initial observation; none establish a
locked or atomic database snapshot. A failure exposes no assembled boundaries.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import math
import re
from typing import Any, Callable

from .bridge import BridgeClient, BridgeError
from .panel_curve_boundary import derive_panel_curve_boundary
from .panel_curve_workflow import PanelCurveReadError, read_panel_curve_source


_LIMITS = {"panels": 128, "vertices": 2048, "analytic_segments": 4096, "sampled_edges": 12000}
_CONTEXT_KEYS = ("session_id", "version", "project")


class _SourceContextError(ValueError):
    """Stop a source before further native reads after a context discrepancy."""


def _reference(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"=([0-9]+)/([0-9]+)", value)
    if match is None:
        return None
    return tuple(part.lstrip("0") or "0" for part in match.groups())  # type: ignore[return-value]


def _read_error(exc: Exception) -> dict[str, Any]:
    error: dict[str, Any] = {"error_type": type(exc).__name__, "message": str(exc)[:1024]}
    if isinstance(exc, BridgeError):
        error["native_diagnostics"] = deepcopy(exc.diagnostics)
        if exc.http_status is not None:
            error["http_status"] = exc.http_status
        if exc.transport_error:
            error["transport_error"] = True
        if exc.response_invalid:
            error["response_invalid"] = True
        if exc.outcome_uncertain:
            error["outcome_uncertain"] = True
    return error


def _observe_context(reader: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        observed = reader()
    except Exception as exc:
        return {"available": False, "error_code": "context_read_failed", "read_error": _read_error(exc)}
    if not isinstance(observed, Mapping):
        return {"available": False, "error_code": "invalid_context_response", "raw_context": deepcopy(observed)}
    return deepcopy(dict(observed))


def _context_available(context: Any) -> bool:
    if not isinstance(context, Mapping) or context.get("available") is not True:
        return False
    if any(not isinstance(context.get(key), str) or not context[key].strip()
           for key in ("session_id", "version")):
        return False
    project = context.get("project")
    return isinstance(project, Mapping) and all(
        isinstance(project.get(key), str) and bool(project[key].strip()) for key in ("name", "mdb")
    )


def _same_context(before: Mapping[str, Any], after: Any) -> bool:
    return _context_available(after) and all(before[key] == after[key] for key in _CONTEXT_KEYS)


def _count(value: Any) -> bool:
    return type(value) is int and value >= 0


def read_panel_curve_assembly(
    bridge: BridgeClient,
    targets: Sequence[str],
    *,
    chord_tolerance_mm: float,
    context_reader: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Read explicit sources once through the existing verified curve workflow.

    ``boundaries`` is populated only when all requested sources and the final
    context agree. Successful source summaries and raw read evidence survive a
    later failure; later sources remain ``not_read`` and no read is retried.
    """
    requested = list(targets) if isinstance(targets, (list, tuple)) else []
    sources = [{"source_index": index, "requested_refno": refno, "read_status": "not_read",
                "success": False, "available": False, "complete": False}
               for index, refno in enumerate(requested)]
    result: dict[str, Any] = {
        "success": False, "available": False, "complete": False,
        "coordinate_space": "WORLD", "coordinate_unit": "mm", "world_position_frame": None,
        "requested_targets": deepcopy(requested), "sources": sources, "boundaries": [],
        "context": {"before": None, "after": None, "boundary_agreement": None},
        "observed_counts": {key: 0 for key in _LIMITS}, "limits": dict(_LIMITS),
        "atomic_snapshot": False,
        "read_consistency": "Sequential two-read/DESP source observations, each checked against the assembly's initial session/version/project; no database lock or atomic snapshot.",
    }

    def failure(code: str) -> dict[str, Any]:
        result["error_code"] = code
        return result

    def source_failure(row: dict[str, Any], code: str, *, overall: str | None = None) -> dict[str, Any]:
        row.update(read_status="failed", success=False, available=False, complete=False, error_code=code)
        return failure(overall or code)

    if (not 1 <= len(requested) <= _LIMITS["panels"]
            or any(not isinstance(refno, str) or not refno.strip() or refno != refno.strip()
                   or len(refno) > 1024 or any(ord(character) < 32 for character in refno)
                   or not (refno.startswith("/") and len(refno) > 1 or _reference(refno) is not None)
                   for refno in requested)):
        return failure("invalid_panel_curve_assembly_targets")
    if (isinstance(chord_tolerance_mm, bool) or not isinstance(chord_tolerance_mm, (int, float))):
        return failure("invalid_chord_tolerance_mm")
    try:
        tolerance = float(chord_tolerance_mm)
    except (OverflowError, ValueError):
        return failure("invalid_chord_tolerance_mm")
    if not math.isfinite(tolerance) or tolerance <= 0:
        return failure("invalid_chord_tolerance_mm")
    result["chord_tolerance_mm"] = tolerance

    before = _observe_context(context_reader)
    result["context"]["before"] = before
    if not _context_available(before):
        return failure("panel_curve_assembly_context_unavailable")

    boundaries: list[dict[str, Any]] = []
    resolved: set[tuple[str, str]] = set()
    counts = result["observed_counts"]
    for row in sources:
        observations: list[dict[str, Any]] = []
        context_fault: dict[str, str] = {}

        def checked_context() -> dict[str, Any]:
            observed = _observe_context(context_reader)
            observations.append(observed)
            if not _context_available(observed):
                context_fault["code"] = "panel_curve_assembly_context_unavailable"
            elif not _same_context(before, observed):
                context_fault["code"] = "panel_curve_assembly_context_changed"
            else:
                return observed
            raise _SourceContextError(context_fault["code"])

        try:
            native, evidence = read_panel_curve_source(
                bridge, row["requested_refno"], coordinate_space="WORLD", context_reader=checked_context)
            row["source_evidence"] = deepcopy(evidence)
        except PanelCurveReadError as exc:
            row["source_evidence"] = deepcopy(exc.evidence)
            row["context_observations"] = deepcopy(observations)
            code = context_fault.get("code")
            if code:
                result["context"]["boundary_agreement"] = False
                row["source_read_error_code"] = exc.code
            return source_failure(row, code or exc.code, overall=code or "panel_curve_assembly_source_failed")
        except Exception as exc:
            row["read_error"] = _read_error(exc)
            row["context_observations"] = deepcopy(observations)
            interrupted_evidence = getattr(exc, "evidence", None)
            row["source_evidence"] = (deepcopy(interrupted_evidence)
                                      if isinstance(interrupted_evidence, Mapping) else {
                                          "requested_refno": row["requested_refno"], "atomic_snapshot": False,
                                          "context_observations": deepcopy(observations),
                                          "read_error": deepcopy(row["read_error"]),
                                          "complete_source_evidence_available": False,
                                      })
            code = context_fault.get("code")
            if code:
                result["context"]["boundary_agreement"] = False
            return source_failure(row, code or "panel_curve_assembly_read_failed",
                                  overall=code or "panel_curve_assembly_source_failed")
        if observations:
            row["context_observations"] = deepcopy(observations)
        source_context = evidence.get("context") if isinstance(evidence, Mapping) else None
        if (not isinstance(source_context, Mapping)
                or source_context.get("boundary_agreement") is not True
                or not all(_same_context(before, source_context.get(key)) for key in ("before", "after"))):
            result["context"]["boundary_agreement"] = False
            return source_failure(row, "panel_curve_assembly_context_changed")
        if not isinstance(native, Mapping):
            return source_failure(row, "invalid_panel_geometry_result", overall="panel_curve_assembly_source_failed")
        identity = native.get("element")
        refno = identity.get("refno") if isinstance(identity, Mapping) else None
        key = _reference(refno)
        row.update(resolved_refno=refno, native_boundary_points_complete=deepcopy(native.get("boundary_points_complete")),
                   native_boundary_points_read_complete=deepcopy(native.get("boundary_points_read_complete")),
                   native_existence_verified=None)
        if key is None:
            return source_failure(row, "resolved_reference_unavailable", overall="panel_curve_assembly_source_failed")
        if not _count(native.get("vertex_count")):
            return source_failure(row, "invalid_panel_vertex_count", overall="panel_curve_assembly_source_failed")
        counts["panels"] += 1
        counts["vertices"] += native["vertex_count"]
        if key in resolved:
            return source_failure(row, "duplicate_panel_curve_source")
        resolved.add(key)
        if counts["vertices"] > _LIMITS["vertices"]:
            return source_failure(row, "panel_curve_assembly_limit_exceeded")

        try:
            boundary = derive_panel_curve_boundary(native, coordinate_space="WORLD", chord_tolerance_mm=tolerance)
        except Exception as exc:
            row["read_error"] = _read_error(exc)
            return source_failure(row, "panel_curve_assembly_derivation_failed")
        if not isinstance(boundary, Mapping):
            row["curve_boundary"] = deepcopy(boundary)
            return source_failure(row, "panel_curve_assembly_curve_unavailable")
        row["curve_summary"] = {name: deepcopy(value) for name, value in boundary.items() if name != "loops"}
        for field in ("native_boundary_points_complete", "native_boundary_points_read_complete", "native_existence_verified"):
            if field in boundary:
                row[field] = deepcopy(boundary[field])
        if any(boundary.get(flag) is not True for flag in ("success", "available", "complete")):
            row["curve_boundary"] = deepcopy(dict(boundary))
            return source_failure(row, "panel_curve_assembly_curve_unavailable")
        frame = boundary.get("coordinate_frame")
        boundary_source = boundary.get("source")
        if (boundary.get("coordinate_space") != "WORLD" or boundary.get("coordinate_unit") != "mm"
                or not isinstance(frame, str) or not frame.strip()
                or not isinstance(boundary_source, Mapping) or _reference(boundary_source.get("refno")) != key):
            return source_failure(row, "invalid_panel_curve_world_source")
        if result["world_position_frame"] is None:
            result["world_position_frame"] = frame
        elif result["world_position_frame"] != frame:
            return source_failure(row, "panel_curve_assembly_world_frame_mismatch")
        if not _count(boundary.get("segment_count")) or not _count(boundary.get("sampled_edge_count")):
            return source_failure(row, "invalid_panel_curve_counts")
        counts["analytic_segments"] += boundary["segment_count"]
        counts["sampled_edges"] += boundary["sampled_edge_count"]
        if any(counts[name] > limit for name, limit in _LIMITS.items()):
            return source_failure(row, "panel_curve_assembly_limit_exceeded")
        row.update(read_status="returned", success=True, available=True, complete=True)
        boundaries.append(deepcopy(dict(boundary)))

    after = _observe_context(context_reader)
    result["context"]["after"] = after
    agreement = _same_context(before, after)
    result["context"]["boundary_agreement"] = agreement
    if not _context_available(after):
        return failure("panel_curve_assembly_context_unavailable")
    if not agreement:
        return failure("panel_curve_assembly_context_changed")
    result.update(success=True, available=True, complete=True, boundaries=boundaries)
    return result
