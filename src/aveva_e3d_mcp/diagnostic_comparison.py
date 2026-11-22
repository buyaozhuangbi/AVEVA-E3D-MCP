"""Bounded structural comparison of retained diagnostic evidence, without live reads."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .diagnostic_receipts import DiagnosticReceiptStore


_ID = re.compile(r"diag_[0-9a-f]{32}\Z")
_REFERENCE = re.compile(r"=([0-9]+)/([0-9]+)\Z")
_TYPE = re.compile(r":?[A-Z][A-Z0-9_]{0,63}\Z")
_TYPE_ALIASES = {
    "CYLINDER": "CYLI", "NCYLINDER": "NCYL", "NCONE": "NCON",
    "PANEL": "PANE", "PAVERT": "PAVE", "PLOOP": "PLOO",
    "EQUIPMENT": "EQUI", "NOZZLE": "NOZZ", "BRANCH": "BRAN",
}
_SECTIONS = (
    "target", "snapshot", "schema", "bounds", "geometry", "display", "baseline_view",
    "final_view", "baseline_selection", "final_selection", "image",
)
_CLIPPING_SECTIONS = ("baseline_clipping", "final_clipping")
_NODE_LIMIT = 20000
_DEPTH_LIMIT = 64
_VALUE_BYTES = 512
_PATH_BYTES = 1024
_SUMMARY_BYTES = 4096
_HASH_BYTES = 1024 * 1024
_MISSING = object()


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _kind(value: Any) -> str:
    return {type(None): "null", bool: "boolean", int: "integer", float: "number",
            str: "string", list: "array", dict: "object"}.get(type(value), "unsupported")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _preview(value: Any) -> dict[str, Any]:
    kind = _kind(value)
    size = len(value) if isinstance(value, (str, list, dict)) else None
    digest = hashlib.sha256()
    hashed = 0
    complete = True
    try:
        encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        for chunk in encoder.iterencode(value):
            encoded = chunk.encode("utf-8")
            hashed += len(encoded)
            if hashed > _HASH_BYTES:
                complete = False
                break
            digest.update(encoded)
    except (TypeError, ValueError, RecursionError, UnicodeError):
        complete = False
    return {"type": kind, "size": size, "sha256": digest.hexdigest() if complete else None,
            "hash_complete": complete, "encoding": "canonical_json", "omitted": True}


def _summary_value(value: Any) -> Any:
    try:
        if len(_json(value).encode("utf-8")) <= _SUMMARY_BYTES:
            return value
    except (TypeError, ValueError, RecursionError, UnicodeError):
        pass
    return {"preview": _preview(value)}


def _describe(value: Any) -> dict[str, Any]:
    if value is _MISSING:
        return {"present": False}
    result = {"present": True, "type": _kind(value)}
    if not isinstance(value, (dict, list)) and len(_json(value).encode("utf-8")) <= _VALUE_BYTES:
        result["value"] = value
    else:
        result["preview"] = _preview(value)
    return result


def _pointer(parent: str, key: str | int) -> str:
    return parent + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _path_fields(path: str) -> dict[str, Any]:
    encoded = path.encode("utf-8")
    if len(encoded) <= _PATH_BYTES:
        return {"path": path}
    return {"path": None, "path_preview": encoded[:_PATH_BYTES].decode("utf-8", errors="ignore"),
            "path_sha256": hashlib.sha256(encoded).hexdigest(), "path_truncated": True}


def _receipt(value: Any, receipt_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    lookup = _object(_object(value).get("result"))
    available = lookup.get("available") is True
    diagnosis = _object(lookup.get("diagnosis")) if available else {}
    result = _object(diagnosis.get("result"))
    summary = {key: _summary_value(result.get(key))
               for key in ("target", "success", "workflow_completed", "complete", "unknown", "errors", "observations")}
    if "clipping_requested" in result:
        summary["clipping_requested"] = _summary_value(result["clipping_requested"])
    return ({"receipt_id": receipt_id, "available": available,
             "status": lookup.get("status", "unknown_or_expired"),
             "created_utc": lookup.get("created_utc"), "expires_utc": lookup.get("expires_utc"),
             "summary": summary}, diagnosis)


def _text(value: Any) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= 256 and
            not any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value))


def _identity(target: Any) -> dict[str, str] | None:
    target = _object(target)
    reference = target.get("refno")
    match = _REFERENCE.fullmatch(reference) if isinstance(reference, str) and len(reference) <= 32 else None
    native_type = target.get("type")
    if match is None or not isinstance(native_type, str):
        return None
    database, element = (int(part) for part in match.groups())
    if not 1 <= database <= 2147483647 or not 0 <= element <= 2147483647:
        return None
    native_type = native_type.strip().upper()
    if _TYPE.fullmatch(native_type) is None:
        return None
    return {"refno": f"={database}/{element}", "type": _TYPE_ALIASES.get(native_type, native_type)}


def _context(diagnosis: dict[str, Any], side: str, version: str,
             reasons: list[dict[str, str]]) -> dict[str, Any]:
    result = _object(diagnosis.get("result"))
    evidence = _object(result.get("evidence"))

    def reason(code: str, path: str) -> None:
        row = {"code": code, "side": side, "path": path}
        if row not in reasons:
            reasons.append(row)

    if diagnosis.get("ok") is not True or type(result.get("schema")) is not int or result.get("schema") != 1:
        reason("invalid_diagnosis", "/result/schema")
    if diagnosis.get("version") != version:
        reason("version_mismatch", "/version")
    if result.get("success") is not True:
        reason("diagnosis_failed", "/result/success")
    if result.get("workflow_completed") is not True:
        reason("workflow_incomplete", "/result/workflow_completed")
    target = _identity(result.get("target"))
    if target is None:
        reason("target_invalid", "/result/target")
    observations = _object(result.get("observations"))
    for key in ("session_build_version_unchanged", "project_mdb_user_unchanged"):
        if observations.get(key) is not True:
            reason("context_unverified", "/result/observations/" + key)
    context: dict[str, Any] = {"version": _summary_value(diagnosis.get("version")), "target": target}
    for phase in ("initial", "final"):
        status = _object(evidence.get(phase + "_status"))
        project_response = _object(evidence.get(phase + "_project"))
        project = _object(project_response.get("project"))
        sample: dict[str, Any] = {}
        for key in ("session_id", "bridge_build", "version"):
            value = status.get(key)
            sample[key] = value if _text(value) else None
            if sample[key] is None:
                reason("context_missing", f"/result/evidence/{phase}_status/{key}")
        sample["project"] = {key: project.get(key) if _text(project.get(key)) else None
                             for key in ("name", "mdb", "user")}
        for key, value in sample["project"].items():
            if value is None:
                reason("context_missing", f"/result/evidence/{phase}_project/project/{key}")
        if status.get("ok") is not True or project_response.get("ok") is not True:
            reason("context_unverified", f"/result/evidence/{phase}_status")
        if status.get("version") != version or project_response.get("version") != version:
            reason("version_mismatch", f"/result/evidence/{phase}_status/version")
        context[phase] = sample
    if context["initial"] != context["final"]:
        reason("context_changed", "/result/evidence")
    return context


class _Comparison:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.nodes = 0
        self.differences: list[dict[str, Any]] = []
        self.stopped = False
        self.stop_reason: str | None = None
        self.found = False

    def stop(self, reason: str) -> None:
        self.stopped = True
        self.stop_reason = reason

    def difference(self, before: Any, after: Any, section: str, path: str) -> bool:
        self.found = True
        if len(self.differences) >= self.limit:
            self.stop("difference_limit")
            return False
        kind = ("added" if before is _MISSING else "removed" if after is _MISSING else
                "type_changed" if type(before) is not type(after) else "value_changed")
        self.differences.append({"section": section, **_path_fields(path), "kind": kind,
                                 "before": _describe(before), "after": _describe(after)})
        return False

    def walk(self, before: Any, after: Any, section: str, path: str, depth: int = 0) -> bool | None:
        if self.stopped:
            return None
        if self.nodes >= _NODE_LIMIT or depth > _DEPTH_LIMIT:
            self.stop("node_limit" if self.nodes >= _NODE_LIMIT else "depth_limit")
            return None
        self.nodes += 1
        if before is _MISSING or after is _MISSING or type(before) is not type(after):
            return self.difference(before, after, section, path)
        if isinstance(before, dict):
            keys = sorted(before.keys() | after.keys())
            values = ((before.get(key, _MISSING), after.get(key, _MISSING), key) for key in keys)
        elif isinstance(before, list):
            values = ((before[index] if index < len(before) else _MISSING,
                       after[index] if index < len(after) else _MISSING, index)
                      for index in range(max(len(before), len(after))))
        else:
            return True if before == after else self.difference(before, after, section, path)
        equal: bool | None = True
        for left, right, key in values:
            child = self.walk(left, right, section, _pointer(path, key), depth + 1)
            if child is False:
                equal = False
            elif child is None and equal is True:
                equal = None
            if self.stopped:
                return False if equal is False else None
        return equal


def compare_diagnostic_receipts(
    store: DiagnosticReceiptStore, before_receipt_id: str, after_receipt_id: str, *, difference_limit: int = 100,
) -> dict[str, Any]:
    """Compare two stored JSON observations; never request PNGs or live evidence."""
    for name, receipt_id in (("before_receipt_id", before_receipt_id), ("after_receipt_id", after_receipt_id)):
        if not isinstance(receipt_id, str) or _ID.fullmatch(receipt_id) is None:
            raise ValueError(name + " must match diag_ followed by 32 lowercase hexadecimal characters")
    if type(difference_limit) is not int or not 1 <= difference_limit <= 200:
        raise ValueError("difference_limit must be an integer from 1 to 200")
    before, left = _receipt(store.get(before_receipt_id, include_image=False), before_receipt_id)
    after, right = _receipt(store.get(after_receipt_id, include_image=False), after_receipt_id)
    reasons: list[dict[str, str]] = []
    contexts = []
    for side, receipt, diagnosis in (("before", before, left), ("after", after, right)):
        if not receipt["available"]:
            reasons.append({"code": "receipt_unavailable", "side": side, "path": ""})
            contexts.append(None)
        else:
            contexts.append(_context(diagnosis, side, store.version, reasons))
    left_context, right_context = contexts
    if left_context is not None and right_context is not None:
        if left_context["target"] != right_context["target"]:
            reasons.append({"code": "target_mismatch", "side": "both", "path": "/result/target"})
        if left_context["initial"] != right_context["initial"]:
            reasons.append({"code": "context_mismatch", "side": "both", "path": "/result/evidence"})
    available = before["available"] and after["available"]
    comparable = available and not reasons
    comparison = _Comparison(difference_limit)
    sections = []
    missing_sections = []
    selected_sections = list(_SECTIONS)
    if any(_object(value.get("result")).get("clipping_requested") is True or
           any(key in _object(_object(value.get("result")).get("evidence")) for key in _CLIPPING_SECTIONS)
           for value in (left, right)):
        selected_sections.extend(_CLIPPING_SECTIONS)
    for name in selected_sections:
        path = "/result/" + ("" if name in ("target", "image") else "evidence/") + name
        left_result, right_result = _object(left.get("result")), _object(right.get("result"))
        left_parent = left_result if name in ("target", "image") else _object(left_result.get("evidence"))
        right_parent = right_result if name in ("target", "image") else _object(right_result.get("evidence"))
        left_present, right_present = name in left_parent, name in right_parent
        equal = None
        if not left_present or not right_present:
            missing_sections.append(name)
        elif comparable:
            equal = comparison.walk(left_parent[name], right_parent[name], name, path)
        sections.append({"name": name, "path": path, "before_present": left_present,
                         "after_present": right_present, "structured_equal": equal})
    complete = comparable and not comparison.stopped
    found = True if comparison.found else False if complete and not missing_sections else None
    same_receipt = before_receipt_id == after_receipt_id
    return {"ok": True, "version": store.version, "result": {
        "schema": 1, "success": True, "historical": True, "live_read": False,
        "status": "compared" if comparable else "not_comparable" if available else "receipt_unavailable",
        "same_receipt": same_receipt, "before": before, "after": after,
        "compatibility": {"comparable": comparable, "reasons": reasons,
                          "before_context": left_context, "after_context": right_context},
        "sections": sections, "differences": comparison.differences,
        "traversal_complete": complete, "differences_found": found,
        "difference_count": len(comparison.differences) if complete else None,
        "returned_difference_count": len(comparison.differences), "difference_limit": difference_limit,
        "truncated": comparison.stopped,
        "scope": {
            "comparison": "Strict JSON structure in the selected, mutually present evidence sections; dictionary order is ignored, array order and scalar types are preserved.",
            "missing_sections": missing_sections,
            "missing_evidence": "An absent section is unknown, not an added or deleted model element. Field presence differences describe JSON evidence only.",
            "completeness": "Traversal completeness and difference_count cover common selected structures only. Original completeness, unknown findings and errors remain in each summary. Equal evidence does not establish an unchanged model.",
            "interpretation": "No coordinate equivalence, verified WORLD bounds, geometric or engineering validity, pixel equality, change cause, or current model state is established.",
            "image": "Only retained image metadata is compared; original PNG bytes are neither requested nor compared.",
            "ordering_not_verified": True, "independent_observations_verified": False,
            "excluded_evidence_sections": ["gates", "initial_status", "initial_project", "final_status",
                                           "final_project", "pre_capture_view", "capture"],
            "excluded_evidence": "Status and project samples are checked only for compatibility. Gate samples, capture internals, unknown and error summaries, and other unselected evidence are not structurally compared.",
            "node_limit": _NODE_LIMIT, "depth_limit": _DEPTH_LIMIT, "visited_nodes": comparison.nodes,
            "stop_reason": comparison.stop_reason, "value_preview_bytes": _VALUE_BYTES,
            "path_bytes": _PATH_BYTES, "summary_value_bytes": _SUMMARY_BYTES, "preview_hash_bytes": _HASH_BYTES,
        },
    }}
