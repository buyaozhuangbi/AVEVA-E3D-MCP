"""Read native panel curves and import tags with explicit consistency evidence."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Callable

from .bridge import BridgeClient, BridgeError


class PanelCurveReadError(ValueError):
    def __init__(self, code: str, evidence: dict[str, Any]):
        self.code = code
        self.evidence = evidence
        super().__init__(code)


def _reference(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"=([0-9]+)/([0-9]+)", value)
    return tuple(map(int, match.groups())) if match else None


def _digest(native: dict[str, Any]) -> str:
    encoded = json.dumps(native, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_panel_curve_source(
    bridge: BridgeClient,
    refno: str,
    *,
    coordinate_space: str,
    context_reader: Callable[[], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Retain two native reads around DESP batches; never claim a snapshot."""
    evidence: dict[str, Any] = {
        "requested_refno": refno, "atomic_snapshot": False,
        "consistency_scope": "Matching complete native panel reads around bounded DESP batches, with matching session/project/MDB boundary observations; intervening changes can remain unobserved.",
        "context": {"before": None, "after": None, "boundary_agreement": None},
        "descriptor_batches": [], "native_reads": 0,
    }

    def fail(code: str) -> None:
        raise PanelCurveReadError(code, evidence)

    before = context_reader()
    evidence["context"]["before"] = before
    if before.get("available") is not True:
        fail("panel_curve_context_unavailable")
    try:
        response = bridge.panel_geometry(refno, include_boundary=True, include_world=coordinate_space == "WORLD")
        evidence["native_reads"] += 1
        if not isinstance(response, dict) or response.get("ok") is not True:
            evidence["native_read_error"] = response
            fail("panel_curve_native_read_failed")
        native = response.get("result")
        if not isinstance(native, dict):
            fail("invalid_panel_geometry_result")
        if response.get("version") != before.get("version"):
            fail("panel_curve_version_mismatch")
        identity = native.get("element")
        if not isinstance(identity, dict) or identity.get("type") not in {"PANE", "PANEL"}:
            fail("element_type_mismatch")
        resolved = identity.get("refno")
        if _reference(resolved) is None:
            fail("resolved_reference_unavailable")
        if refno.startswith("=") and _reference(refno) != _reference(resolved):
            fail("element_reference_mismatch")
        if refno.startswith("/") and (
            not isinstance(identity.get("name"), str)
            or ("/" + identity["name"].lstrip("/")).casefold() != refno.casefold()
        ):
            fail("element_name_mismatch")
        evidence["resolved_refno"] = resolved
        evidence["native_read_before_sha256"] = _digest(native)
        evidence["native_geometry"] = {key: deepcopy(native.get(key)) for key in (
            "success", "complete", "read_errors", "world_positions_complete", "world_read_errors",
            "boundary_points_complete", "boundary_points_errors", "topology_valid", "revision",
        )}
        if native.get("success") is not True or native.get("complete") is not True or native.get("read_errors"):
            fail("panel_geometry_incomplete")
        loops = native.get("loops")
        if not isinstance(loops, list) or not 1 <= len(loops) <= 32:
            fail("panel_loops_unavailable")
        vertices: list[dict[str, Any]] = []
        for loop in loops:
            if not isinstance(loop, dict) or loop.get("complete") is not True or loop.get("read_errors"):
                fail("panel_loop_incomplete")
            members = loop.get("vertices")
            if not isinstance(members, list) or len(members) < 3 or any(not isinstance(v, dict) for v in members):
                fail("panel_vertices_unavailable")
            vertices.extend(members)
            if len(vertices) > 2048:
                fail("panel_vertex_limit_exceeded")
        if native.get("vertex_count") != len(vertices):
            fail("panel_vertex_count_mismatch")
        references = [vertex.get("refno") for vertex in vertices]
        keys = [_reference(value) for value in references]
        if any(key is None for key in keys) or len(set(keys)) != len(keys):
            fail("panel_vertex_reference_invalid")
        descriptors: dict[tuple[int, int], dict[str, Any]] = {}
        for offset in range(0, len(references), 50):
            chunk = references[offset:offset + 50]
            descriptor_response = bridge.read_attributes_batch(chunk, ["DESP"])
            evidence["descriptor_batches"].append(descriptor_response)
            batch = descriptor_response.get("result") if isinstance(descriptor_response, dict) else None
            if (not isinstance(descriptor_response, dict) or descriptor_response.get("ok") is not True
                    or descriptor_response.get("version") != before.get("version") or not isinstance(batch, dict)
                    or batch.get("success") is not True or batch.get("complete") is not True
                    or batch.get("ce_unchanged") is not True or batch.get("attributes") != ["DESP"]
                    or batch.get("requested_refnos") != chunk or batch.get("row_count") != len(chunk)):
                fail("panel_curve_import_descriptor_incomplete")
            rows = batch.get("rows")
            if not isinstance(rows, list) or len(rows) != len(chunk):
                fail("panel_curve_import_descriptor_incomplete")
            for requested, row in zip(chunk, rows):
                if (not isinstance(row, dict) or row.get("success") is not True
                        or row.get("type") not in {"PAVE", "PAVERT"}
                        or _reference(row.get("refno")) != _reference(requested)
                        or _reference(row.get("requested_refno")) != _reference(requested)
                        or row.get("errors") or row.get("typed_read_errors")):
                    fail("panel_curve_import_descriptor_identity_mismatch")
                values = row.get("values")
                value = values.get("DESP") if isinstance(values, dict) else None
                if not isinstance(value, str) or not value.strip():
                    fail("panel_curve_import_descriptor_unavailable")
                descriptors[_reference(requested)] = {
                    "checked": True, "tagged": value.strip().casefold() != "unset",
                    "attribute": "DESP", "value": value,
                    "source": "Native read_attributes_batch formatted DESP",
                    "refno": row["refno"], "type": row["type"],
                }

        repeated = bridge.panel_geometry(resolved, include_boundary=True, include_world=coordinate_space == "WORLD")
        evidence["native_reads"] += 1
        if (not isinstance(repeated, dict) or repeated.get("ok") is not True
                or repeated.get("version") != before.get("version") or not isinstance(repeated.get("result"), dict)):
            evidence["native_read_error"] = repeated
            fail("panel_curve_native_recheck_failed")
        evidence["native_read_after_sha256"] = _digest(repeated["result"])
        evidence["native_reads_agree"] = native == repeated["result"]
        after = context_reader()
        evidence["context"]["after"] = after
        agreement = after.get("available") is True and all(
            before.get(key) == after.get(key) for key in ("session_id", "version", "project")
        )
        evidence["context"]["boundary_agreement"] = agreement
        if not agreement:
            fail("panel_curve_context_changed")
        if not evidence["native_reads_agree"]:
            fail("panel_curve_source_changed")
        enriched = deepcopy(native)
        for loop in enriched["loops"]:
            for vertex in loop["vertices"]:
                vertex["curve_import_descriptor"] = descriptors[_reference(vertex["refno"])]
        return enriched, evidence
    except PanelCurveReadError:
        raise
    except Exception as exc:
        evidence["read_error"] = {"error_type": type(exc).__name__, "message": str(exc)[:1024]}
        if isinstance(exc, BridgeError):
            evidence["read_error"]["native_diagnostics"] = exc.diagnostics
        fail("panel_curve_read_failed")
