"""Observe independent WORLD material sources without an atomic snapshot.

Parameter sources are read twice. Owner-member differences reuse their existing
two-read CSG workflow exactly once. No callback is retried and no partial mesh
list is exposed when any source, identity, context or resource check fails.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any, Callable

from .bridge import BridgeError


MAX_SOURCES = 128
MAX_VERTICES = 10_000
MAX_TRIANGLES = 20_000
MAX_RAW_RECEIPT_BYTES = 16 * 1024 * 1024
WORLD_FRAME = "WORLD coordinates; millimetres"
_TYPES = {"parameter_mesh": frozenset(("PANE", "BOX", "CYLI", "CONE", "DISH")),
          "owner_member_difference": frozenset(("BOX", "CYLI", "CONE"))}
_CONTEXT_KEYS = ("session_id", "version", "project")


class _SourceFailure(ValueError):
    def __init__(self, code: str, **diagnostics: Any):
        self.code = "material_assembly_" + code
        self.diagnostics = diagnostics
        super().__init__(self.code)


def _reject(code: str, **diagnostics: Any) -> None:
    raise _SourceFailure(code, **diagnostics)


def _numeric_reference(value: Any) -> tuple[str, str] | None:
    match = re.fullmatch(r"=([0-9]+)/([0-9]+)", value) if isinstance(value, str) else None
    return tuple(part.lstrip("0") or "0" for part in match.groups()) if match else None


def _request_reference(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("refno must be an explicit /NAME or =database/element reference")
    refno = value.strip()
    if (not refno or len(refno) > 1024
            or any(char.isspace() or ord(char) < 32 or 127 <= ord(char) < 160 for char in refno)
            or not (refno.startswith("/") and len(refno) > 1 or _numeric_reference(refno) is not None)):
        raise ValueError("refno must be an explicit /NAME or =database/element reference")
    return refno


def normalize_material_targets(targets: Any) -> list[dict[str, str]]:
    """Validate every request and reject duplicate names/numeric aliases up front."""
    if not isinstance(targets, (list, tuple)) or not 1 <= len(targets) <= MAX_SOURCES:
        raise ValueError("targets must contain 1 to 128 material sources")
    normalized: list[dict[str, str]] = []
    seen: set[tuple[Any, ...]] = set()
    for index, target in enumerate(targets):
        if not isinstance(target, Mapping) or set(target) != {"refno", "expected_type", "geometry_mode"}:
            raise ValueError(f"targets[{index}] requires exactly refno, expected_type and geometry_mode")
        refno = _request_reference(target["refno"])
        mode, expected = target["geometry_mode"], target["expected_type"]
        if not isinstance(mode, str) or mode not in _TYPES or not isinstance(expected, str) or expected not in _TYPES[mode]:
            raise ValueError(f"targets[{index}] has an unsupported geometry_mode/expected_type pair")
        numeric = _numeric_reference(refno)
        key = ("numeric", *numeric) if numeric is not None else ("name", refno.casefold())
        if key in seen:
            raise ValueError(f"targets[{index}] duplicates a requested material source")
        seen.add(key)
        normalized.append({"refno": refno, "expected_type": expected, "geometry_mode": mode})
    return normalized


def _encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _error(exc: Exception) -> dict[str, Any]:
    error: dict[str, Any] = {"error_type": type(exc).__name__, "message": str(exc)[:1024]}
    if isinstance(exc, BridgeError):
        error["native_diagnostics"] = deepcopy(exc.diagnostics)
        if exc.http_status is not None:
            error["http_status"] = exc.http_status
        for field in ("transport_error", "response_invalid", "outcome_uncertain"):
            if getattr(exc, field):
                error[field] = True
    return error


def _observe_context(reader: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        observed = reader()
        if isinstance(observed, Mapping):
            return deepcopy(dict(observed))
        return {"available": False, "error_code": "invalid_context_response", "response_type": type(observed).__name__}
    except Exception as exc:
        return {"available": False, "error_code": "context_read_failed", "read_error": _error(exc)}


def _context_available(context: Any) -> bool:
    if (not isinstance(context, Mapping) or context.get("available") is not True
            or any(not isinstance(context.get(key), str) or not context[key].strip() for key in ("session_id", "version"))):
        return False
    project = context.get("project")
    if not isinstance(project, Mapping) or any(
            not isinstance(project.get(key), str) or not project[key].strip() for key in ("name", "mdb")):
        return False
    for key, required in (("connected", True), ("database_ready", True), ("busy", False), ("recovery_required", False)):
        if key in context and context[key] is not required:
            return False
    fault = context.get("native_database_fault")
    return fault is None or isinstance(fault, Mapping) and fault.get("has_fault") is False


def _same_context(initial: Mapping[str, Any], observed: Any) -> bool:
    return _context_available(observed) and all(initial.get(key) == observed.get(key) for key in _CONTEXT_KEYS)


def _context_code(initial: Mapping[str, Any], observed: Any) -> str | None:
    if not _context_available(observed):
        return "context_unavailable"
    return None if _same_context(initial, observed) else "context_changed"


def _payload(receipt: Any) -> Mapping[str, Any] | None:
    if not isinstance(receipt, Mapping):
        return None
    if "ok" in receipt:
        return receipt
    structured = receipt.get("structuredContent")
    if isinstance(structured, Mapping):
        return structured
    content = receipt.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, Mapping) or block.get("type") != "text":
                continue
            try:
                decoded = json.loads(block.get("text", ""))
            except (TypeError, ValueError):
                continue
            if isinstance(decoded, Mapping):
                return decoded
    return None


def _upstream_code(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    result = payload.get("result")
    for value in (result, payload):
        if isinstance(value, Mapping) and isinstance(value.get("error_code"), str):
            return value["error_code"][:256]
    return None


def _derived_mesh_failure_diagnostics(payload: Any) -> dict[str, Any] | None:
    """Keep a bounded derived error summary without relabeling it as native."""
    source = payload.get("result") if isinstance(payload, Mapping) else None
    mesh = source.get("derived_mesh") if isinstance(source, Mapping) else None
    if not isinstance(mesh, Mapping):
        return None
    details: dict[str, Any] = {"source": "derived_mesh"}
    truncated = False
    code = mesh.get("error_code")
    if isinstance(code, str) and code:
        details["error_code"] = code[:256]
        truncated = len(code) > 256
    errors = mesh.get("errors")
    if isinstance(errors, (list, tuple)) and errors:
        details["errors"] = [error[:256] if isinstance(error, str) else {
            "value_omitted": True, "value_type": type(error).__name__} for error in errors[:16]]
        truncated = truncated or len(errors) > 16 or any(
            not isinstance(error, str) or len(error) > 256 for error in errors[:16])
    if len(details) == 1:
        return None
    if truncated:
        details["truncated"] = True
    return details


def _read_once(reader, target, radial_segments, row, counts, sequence):
    event = {"read_index": len(row["read_sequence"]) + 1, "sequence_index": sequence,
             "requested_refno": target["refno"], "geometry_mode": target["geometry_mode"], "status": "dispatched"}
    row["read_sequence"].append(event)
    row["reader_calls"] += 1
    try:
        receipt = reader(deepcopy(target), radial_segments)
    except Exception as exc:
        event.update(status="failed", read_error=_error(exc))
        evidence = getattr(exc, "evidence", None)
        if isinstance(evidence, Mapping):
            row["source_evidence"] = deepcopy(dict(evidence))
        _reject("source_read_failed", read_error=_error(exc))
    payload = _payload(receipt)
    # MCP can duplicate its JSON in text and structuredContent. Retain the
    # decoded envelope once; the transport error bit remains separate evidence.
    retained = payload if payload is not None else receipt
    event["mcp_envelope_unwrapped"] = payload is not None and payload is not receipt
    event["mcp_is_error"] = isinstance(receipt, Mapping) and receipt.get("isError") is True
    original_code = _upstream_code(payload)
    if original_code:
        row["upstream_error_code"] = original_code
    try:
        encoded = _encoded(retained)
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        marker = {"raw_receipt_omitted": True, "reason": "not_json_serializable",
                  "upstream_error_code": original_code, "serialization_error": _error(exc)}
        row["raw_receipts"].append(marker)
        event.update(status="returned", raw_receipt_omitted=True, receipt_sha256=None)
        _reject("raw_receipt_not_serializable", **marker)
    size = len(encoded)
    counts["raw_receipt_bytes"] += size
    event.update(status="returned", receipt_bytes=size, receipt_sha256=hashlib.sha256(encoded).hexdigest())
    if counts["raw_receipt_bytes"] > MAX_RAW_RECEIPT_BYTES:
        row["raw_receipts"].append({"raw_receipt_omitted": True, "reason": "raw_receipt_limit_exceeded",
                                    "receipt_bytes": size, "receipt_sha256": event["receipt_sha256"],
                                    "upstream_error_code": original_code})
        _reject("raw_receipt_limit_exceeded", observed_bytes=counts["raw_receipt_bytes"], limit=MAX_RAW_RECEIPT_BYTES)
    row["raw_receipts"].append(deepcopy(retained))
    if payload is not None:
        payload = row["raw_receipts"][-1]
    source_result = payload.get("result") if isinstance(payload, Mapping) else None
    if isinstance(source_result, Mapping) and isinstance(source_result.get("source_evidence"), Mapping):
        row["source_evidence"] = deepcopy(dict(source_result["source_evidence"]))
    if (not isinstance(receipt, Mapping) or receipt.get("isError") is True
            or not isinstance(payload, Mapping) or payload.get("ok") is not True):
        _reject("source_unavailable_or_incomplete", upstream_error_code=original_code)
    return payload, event


def _admit(payload, target, initial, row, event):
    if payload.get("version") != initial["version"]:
        _reject("source_version_mismatch", observed_version=payload.get("version"))
    source = payload.get("result")
    if not isinstance(source, Mapping) or any(source.get(flag) is not True for flag in ("success", "available", "complete")):
        _reject("source_unavailable_or_incomplete", upstream_error_code=_upstream_code(payload))
    native, mesh = source.get("source_geometry"), source.get("derived_mesh")
    if (not isinstance(native, Mapping) or native.get("success") is not True or native.get("complete") is not True
            or native.get("partial") is True or native.get("read_errors") or native.get("geometry_arrays_included") is False):
        _reject("source_unavailable_or_incomplete")
    identity = native.get("element")
    identity = identity if isinstance(identity, Mapping) else {}
    resolved = _numeric_reference(source.get("resolved_refno"))
    native_ref = _numeric_reference(identity.get("refno"))
    if resolved is None or resolved != native_ref:
        _reject("source_identity_mismatch")
    requested = _numeric_reference(target["refno"])
    name = identity.get("name")
    if requested is not None:
        if requested != resolved:
            _reject("source_identity_mismatch")
    elif not isinstance(name, str) or ("/" + name.strip().lstrip("/")).casefold() != target["refno"].casefold():
        _reject("source_identity_mismatch")
    native_type = "PANE" if identity.get("type") == "PANEL" else identity.get("type")
    if source.get("resolved_type") != target["expected_type"] or native_type != target["expected_type"]:
        _reject("source_type_mismatch")
    row.update(resolved_refno="=" + "/".join(resolved), resolved_type=source["resolved_type"])
    if (not isinstance(mesh, Mapping) or any(mesh.get(flag) is not True for flag in ("success", "available", "complete"))
            or source.get("mesh_detail", "full") != "full" or mesh.get("geometry_arrays_included") is False):
        _reject("source_unavailable_or_incomplete")
    if _numeric_reference(mesh.get("refno")) != resolved:
        _reject("source_identity_mismatch")
    if (source.get("coordinate_space") != "WORLD" or mesh.get("coordinate_frame") != WORLD_FRAME
            or mesh.get("coordinate_space", "WORLD") != "WORLD" or mesh.get("coordinate_unit", "mm") != "mm"
            or source.get("coordinate_unit", "mm") != "mm"):
        _reject("source_world_mesh_required")
    vertices, triangles = mesh.get("vertices"), mesh.get("triangles")
    if not isinstance(vertices, (list, tuple)) or not isinstance(triangles, (list, tuple)):
        _reject("source_geometry_arrays_required")
    if any(not isinstance(point, (list, tuple)) or len(point) != 3
           or any(type(value) not in (int, float) or not math.isfinite(value) for value in point) for point in vertices):
        _reject("source_geometry_arrays_required")
    if any(not isinstance(face, (list, tuple)) or len(face) != 3
           or any(type(index) is not int or not 0 <= index < len(vertices) for index in face) for face in triangles):
        _reject("source_geometry_arrays_required")
    actual_counts = {"vertices": len(vertices), "triangles": len(triangles)}
    if "counts" in mesh and mesh["counts"] != actual_counts:
        _reject("source_geometry_arrays_required")
    if mesh.get("native_tessellation") is not False:
        _reject("source_parameter_mesh_required")
    try:
        native_hash = _digest(native)
        geometry_hash = _digest({"vertices": vertices, "triangles": triangles})
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        _reject("raw_receipt_not_serializable", hash_error=_error(exc))
    event.update(source_geometry_sha256=native_hash, geometry_sha256=geometry_hash,
                 resolved_refno=row["resolved_refno"], resolved_type=source["resolved_type"])
    supplied_hash = mesh.get("geometry_sha256")
    if supplied_hash is not None and supplied_hash != geometry_hash:
        _reject("source_geometry_hash_mismatch")
    if target["geometry_mode"] == "parameter_mesh":
        if mesh.get("cuts_applied") is not False or mesh.get("csg_evaluated") is True or not vertices or not triangles:
            _reject("source_parameter_mesh_required")
    else:
        evidence = source.get("source_evidence")
        if (not isinstance(evidence, Mapping) or type(evidence.get("native_geometry_reads")) is not int
                or evidence.get("native_geometry_reads") != 2
                or evidence.get("native_reads_agree") is not True or evidence.get("atomic_snapshot") is not False
                or evidence.get("native_cut_application_verified") is not False
                or evidence.get("native_read_before_sha256") != native_hash
                or evidence.get("native_read_after_sha256") != native_hash):
            _reject("source_csg_evidence_invalid")
        context = evidence.get("context")
        if (not isinstance(context, Mapping) or context.get("boundary_agreement") is not True
                or not all(_same_context(initial, context.get(side)) for side in ("before", "after"))):
            _reject("context_changed", source_context=deepcopy(context))
        for field in ("revision_before", "revision_after"):
            if field in evidence and evidence[field] != native.get("revision"):
                _reject("source_csg_evidence_invalid")
        if (supplied_hash != geometry_hash or mesh.get("cuts_applied") is not True
                or mesh.get("csg_evaluated") is not True or mesh.get("material_boundary_validated") is not True
                or mesh.get("native_cut_application_verified") is not False
                or source.get("native_cut_application_verified") is not False
                or source.get("atomic_snapshot") is not False):
            _reject("source_csg_evidence_invalid")
        if type(mesh.get("empty")) is not bool or bool(vertices) != bool(triangles) or mesh["empty"] != (not vertices):
            _reject("source_geometry_arrays_required")
        if not isinstance(mesh.get("source_geometry"), Mapping) or _digest(mesh["source_geometry"]) != native_hash:
            _reject("source_csg_evidence_invalid")
    return source, resolved, native_hash, geometry_hash, actual_counts


def read_material_assembly_sources(
    targets: Sequence[Mapping[str, str]],
    *,
    radial_segments: int,
    context_reader: Callable[[], dict[str, Any]],
    parameter_mesh_reader: Callable[[dict[str, str], int], dict[str, Any]],
    csg_mesh_reader: Callable[[dict[str, str], int], dict[str, Any]],
) -> dict[str, Any]:
    """Read 1–128 independent material sources and return all meshes or none.

    Readers return full existing MCP envelopes in WORLD/mm. Every raw receipt
    is retained once, without an additional encoded JSON copy; an oversized or
    non-JSON receipt is represented by a diagnostic marker. Source summaries
    remain traceable even when a later source or final boundary fails.
    """
    requested = list(targets) if isinstance(targets, (list, tuple)) else []
    rows = [{"source_index": index,
             "requested_refno": target.get("refno") if isinstance(target, Mapping) else None,
             "expected_type": target.get("expected_type") if isinstance(target, Mapping) else None,
             "geometry_mode": target.get("geometry_mode") if isinstance(target, Mapping) else None,
             "read_status": "not_read", "success": False, "available": False, "complete": False,
             "reader_calls": 0, "raw_receipts": [], "read_sequence": [], "source_evidence": {},
             "context": {"before": None, "after": None, "boundary_agreement": None}}
            for index, target in enumerate(requested)]
    result: dict[str, Any] = {
        "success": False, "available": False, "complete": False, "errors": [],
        "mesh_sources": [], "sources": rows,
        "context": {"before": None, "after": None, "boundary_agreement": None},
        "coordinate_space": "WORLD", "coordinate_frame": WORLD_FRAME, "coordinate_unit": "mm",
        "atomic_snapshot": False, "native_tessellation": False, "native_cut_application_verified": False,
        "observed_counts": {"sources": 0, "vertices": 0, "triangles": 0, "raw_receipt_bytes": 0},
        "limits": {"sources": MAX_SOURCES, "vertices": MAX_VERTICES, "triangles": MAX_TRIANGLES,
                   "raw_receipt_bytes": MAX_RAW_RECEIPT_BYTES},
        "consistency_scope": "Sequential source observations compared with one initial session/version/project/MDB; no database lock or atomic snapshot.",
    }

    def fail(code, **diagnostics):
        full_code = code if code.startswith("material_assembly_") else "material_assembly_" + code
        if "error_code" not in result:
            result.update(error_code=full_code, errors=[full_code], diagnostics=diagnostics)
        elif diagnostics:
            result.setdefault("additional_diagnostics", []).append({"error_code": full_code, **diagnostics})

    try:
        normalized = normalize_material_targets(targets)
    except (ValueError, TypeError) as exc:
        fail("invalid_targets", validation_error=str(exc))
        return result
    result["requested_targets"] = deepcopy(normalized)
    if type(radial_segments) is not int or not 12 <= radial_segments <= 99:
        fail("invalid_radial_segments")
        return result
    if not all(callable(reader) for reader in (context_reader, parameter_mesh_reader, csg_mesh_reader)):
        fail("invalid_readers")
        return result
    result["radial_segments"] = radial_segments
    for row, target in zip(rows, normalized):
        row.update(requested_refno=target["refno"], expected_type=target["expected_type"], geometry_mode=target["geometry_mode"])
    initial = _observe_context(context_reader)
    result["context"]["before"] = initial
    if not _context_available(initial):
        fail("context_unavailable")
        return result

    counts = result["observed_counts"]
    prepared: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    sequence = 0
    source_contexts_agree = True
    for row, target in zip(rows, normalized):
        row["read_status"] = "reading"
        before = _observe_context(context_reader)
        row["context"]["before"] = before
        fault: _SourceFailure | None = None
        source = None
        try:
            context_error = _context_code(initial, before)
            if context_error:
                _reject(context_error)
            if counts["raw_receipt_bytes"] >= MAX_RAW_RECEIPT_BYTES:
                _reject("raw_receipt_limit_exceeded", observed_bytes=counts["raw_receipt_bytes"], limit=MAX_RAW_RECEIPT_BYTES)
            reader = parameter_mesh_reader if target["geometry_mode"] == "parameter_mesh" else csg_mesh_reader
            sequence += 1
            payload, event = _read_once(reader, target, radial_segments, row, counts, sequence)
            source, resolved, native_hash, geometry_hash, mesh_counts = _admit(payload, target, initial, row, event)
            row.update(source_geometry_sha256=native_hash, geometry_sha256=geometry_hash,
                       counts=mesh_counts, native_revision=deepcopy(source["source_geometry"].get("revision")))
            if resolved in seen:
                _reject("duplicate_source", resolved_refno=row["resolved_refno"])
            seen.add(resolved)
            counts["sources"] += 1
            for name in ("vertices", "triangles"):
                counts[name] += mesh_counts[name]
            if counts["vertices"] > MAX_VERTICES or counts["triangles"] > MAX_TRIANGLES:
                _reject("mesh_limit_exceeded", observed_counts=dict(counts))
            if target["geometry_mode"] == "parameter_mesh":
                row["source_evidence"] = {
                    "atomic_snapshot": False, "native_geometry_reads": 1, "native_reads_agree": None,
                    "native_read_before_sha256": native_hash, "native_read_after_sha256": None,
                    "geometry_before_sha256": geometry_hash, "geometry_after_sha256": None,
                    "revision_before": deepcopy(source["source_geometry"].get("revision")), "revision_after": None,
                    "resolved_refno": row["resolved_refno"], "resolved_type": row["resolved_type"],
                    "native_cut_application_verified": False,
                }
                repeated_target = {**target, "refno": row["resolved_refno"]}
                sequence += 1
                repeated_payload, repeated_event = _read_once(reader, repeated_target, radial_segments, row, counts, sequence)
                _, repeated_ref, repeated_native, repeated_geometry, _ = _admit(
                    repeated_payload, repeated_target, initial, row, repeated_event)
                row["source_evidence"].update(
                    native_geometry_reads=2, native_reads_agree=repeated_native == native_hash,
                    native_read_after_sha256=repeated_native, geometry_after_sha256=repeated_geometry,
                    ordered_mesh_reads_agree=repeated_geometry == geometry_hash,
                    revision_after=deepcopy(repeated_payload["result"]["source_geometry"].get("revision")))
                if (repeated_ref != resolved or repeated_native != native_hash or repeated_geometry != geometry_hash):
                    _reject("source_changed", native_geometry_agrees=repeated_native == native_hash,
                            ordered_mesh_geometry_agrees=repeated_geometry == geometry_hash)
        except _SourceFailure as exc:
            if exc.code == "material_assembly_source_unavailable_or_incomplete" and row["raw_receipts"]:
                derived = _derived_mesh_failure_diagnostics(row["raw_receipts"][-1])
                if derived is not None:
                    exc.diagnostics["derived_mesh_diagnostics"] = derived
            fault = exc
        except Exception as exc:
            fault = _SourceFailure("source_read_failed", read_error=_error(exc))
        if row["reader_calls"]:
            after = _observe_context(context_reader)
            row["context"]["after"] = after
            context_error = _context_code(initial, after)
            if context_error:
                if fault is None:
                    fault = _SourceFailure(context_error)
                else:
                    row["context_error_code"] = "material_assembly_" + context_error
        else:
            after = None
        agreement = _same_context(initial, before) and after is not None and _same_context(initial, after)
        row["context"]["boundary_agreement"] = agreement
        internal_context_failed = fault is not None and fault.code in {
            "material_assembly_context_unavailable", "material_assembly_context_changed"}
        source_contexts_agree = source_contexts_agree and agreement and not internal_context_failed
        if target["geometry_mode"] == "parameter_mesh":
            row["source_evidence"]["context"] = deepcopy(row["context"])
        if fault is not None:
            row.update(read_status="failed", success=False, available=False, complete=False,
                       error_code=fault.code, diagnostics=fault.diagnostics)
            fail(fault.code, source_index=row["source_index"], **fault.diagnostics)
            break
        row.update(read_status="complete", success=True, available=True, complete=True)
        prepared.append({**deepcopy(dict(source)), "geometry_mode": target["geometry_mode"]})

    final = _observe_context(context_reader)
    result["context"]["after"] = final
    agreement = source_contexts_agree and _same_context(initial, final)
    result["context"]["boundary_agreement"] = agreement
    if not agreement:
        code = _context_code(initial, final) or "context_changed"
        fail(code, operation_boundary_agreement=False)
    if "error_code" not in result:
        result.update(success=True, available=True, complete=True, mesh_sources=prepared)
    return result
