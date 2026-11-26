"""Build a read-only, context-bracketed plan for explicit equipment edits."""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from .bridge import BridgeClient, BridgeError, element_access_refnos
from .config import Settings
from .context_snapshot import context_snapshot
from .equipment_child_schema import equipment_child_schema, _native_fault as _child_schema_native_fault
from .equipment_manifest import equipment_manifest
from .model_plan_preview import model_edit_preview
from .protocol import McpServer


TOOL = "e3d_equipment_model_plan"
_ATTRIBUTE = re.compile(r"[A-Za-z0-9_:]{1,64}")
_TYPE = re.compile(r"[A-Za-z0-9_:]{1,64}")
_STEP_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}")
_COPY_ROOT_TYPES = {"BOX", "CYLI", "CONE", "DISH", "SUBE", "PANE"}
_EQUIPMENT_REF_MAX_LENGTH = 256
_SHA = re.compile(r"[0-9a-f]{64}")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _ref(value: Any, field: str) -> str:
    refno = element_access_refnos([value])[0] if isinstance(value, str) else _bad_ref(field)
    if len(refno) > _EQUIPMENT_REF_MAX_LENGTH or "*" in refno or "?" in refno:
        raise ValueError(f"{field} must be an explicit reference of at most 256 characters without wildcards")
    if refno.startswith("="):
        refno = "=" + "/".join(str(int(part)) for part in refno[1:].split("/"))
    return refno


def _bad_ref(field: str) -> str:
    raise ValueError(f"{field} must be an explicit /NAME or =database/element reference")


def _vector(value: Any, field: str) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{field} must contain exactly three finite numbers")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item)
           for item in value):
        raise ValueError(f"{field} must contain exactly three finite numbers")
    return [float(item) for item in value]


def _parameters(args: dict[str, Any]) -> dict[str, Any]:
    if set(args) - {"equipment_refno", "steps", "max_depth", "limit", "include_native_previews"}:
        raise ValueError("Only equipment_refno, steps, max_depth, limit and include_native_previews are accepted")
    include_native_previews = args.get("include_native_previews", False)
    if type(include_native_previews) is not bool:
        raise ValueError("include_native_previews must be a boolean")
    equipment_refno = _ref(args.get("equipment_refno"), "equipment_refno")
    if len(equipment_refno) > _EQUIPMENT_REF_MAX_LENGTH or "*" in equipment_refno:
        raise ValueError("equipment_refno must be an explicit reference of at most 256 characters without wildcards")
    max_depth = args.get("max_depth", 32)
    limit = args.get("limit", 200)
    if type(max_depth) is not int or not 1 <= max_depth <= 32:
        raise ValueError("max_depth must be an integer between 1 and 32")
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit must be an integer between 1 and 200")
    steps = args.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 16:
        raise ValueError("steps must contain between 1 and 16 planned operations")

    normalized: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    allowed_common = {"step_id", "operation", "parent_refno", "target_refno", "source_refno", "element_type",
                      "name", "attributes", "attribute_reference_refno", "position", "orientation", "wrt"}
    for index, raw in enumerate(steps):
        if not isinstance(raw, dict) or set(raw) - allowed_common:
            raise ValueError(f"steps[{index}] contains unknown fields or is not an object")
        step_id = raw.get("step_id")
        operation = raw.get("operation")
        if not isinstance(step_id, str) or not _STEP_ID.fullmatch(step_id):
            raise ValueError(f"steps[{index}].step_id must be a short identifier beginning with a letter")
        key = step_id.casefold()
        if key in identifiers:
            raise ValueError("step_id values must be unique ignoring case")
        identifiers.add(key)
        if operation not in {"create", "copy", "transform"}:
            raise ValueError(f"steps[{index}].operation must be create, copy or transform")

        operation_fields = {
            "create": {"step_id", "operation", "parent_refno", "element_type", "name", "attributes",
                        "attribute_reference_refno", "position", "orientation", "wrt"},
            "copy": {"step_id", "operation", "parent_refno", "source_refno", "name", "position", "orientation", "wrt"},
            "transform": {"step_id", "operation", "target_refno", "position", "orientation", "wrt"},
        }[operation]
        unexpected = set(raw) - operation_fields
        if unexpected:
            raise ValueError(f"steps[{index}] has fields not used by {operation}: {', '.join(sorted(unexpected))}")

        position = _vector(raw.get("position"), f"steps[{index}].position")
        orientation = raw.get("orientation")
        normalized_orientation = None
        if orientation is not None:
            if not isinstance(orientation, dict) or set(orientation) != {"x_axis", "y_axis"}:
                raise ValueError(f"steps[{index}].orientation requires x_axis and y_axis")
            x_axis = _vector(orientation.get("x_axis"), f"steps[{index}].orientation.x_axis")
            y_axis = _vector(orientation.get("y_axis"), f"steps[{index}].orientation.y_axis")
            if x_axis is None or y_axis is None:
                raise ValueError(f"steps[{index}].orientation requires x_axis and y_axis")
            if any(abs(math.hypot(*axis) - 1.0) > 1e-6 for axis in (x_axis, y_axis)) or \
                    abs(sum(x * y for x, y in zip(x_axis, y_axis))) > 1e-6:
                raise ValueError(f"steps[{index}].orientation axes must be unit length and orthogonal")
            normalized_orientation = {"x_axis": x_axis, "y_axis": y_axis}
        if position is None and normalized_orientation is None and operation == "transform":
            raise ValueError(f"steps[{index}] transform requires position or orientation")

        wrt = raw.get("wrt", "OWNER")
        if not isinstance(wrt, str) or not wrt.strip():
            raise ValueError(f"steps[{index}].wrt must be a non-empty frame")
        wrt = wrt.strip()
        if wrt.upper() in {"OWNER", "WORLD"}:
            wrt = wrt.upper()
        else:
            wrt = _ref(wrt, f"steps[{index}].wrt")

        step: dict[str, Any] = {"step_id": step_id, "operation": operation, "wrt": wrt}
        if position is not None:
            step["position"] = position
        if normalized_orientation is not None:
            step["orientation"] = normalized_orientation

        if operation in {"create", "copy"}:
            step["parent_refno"] = _ref(raw.get("parent_refno"), f"steps[{index}].parent_refno")
            name = raw.get("name")
            if not isinstance(name, str) or not 1 <= len(name) <= 50 or any(
                    char.isspace() or ord(char) < 32 or 127 <= ord(char) < 160 for char in name):
                raise ValueError(f"steps[{index}].name must be 1 to 50 characters without whitespace or controls")
            step["name"] = name
        if operation == "create":
            element_type = raw.get("element_type")
            if not isinstance(element_type, str) or not _TYPE.fullmatch(element_type.strip()):
                raise ValueError(f"steps[{index}].element_type must be an SDK type name")
            step["element_type"] = element_type.strip().upper()
            attributes = raw.get("attributes", {})
            if not isinstance(attributes, dict) or len(attributes) > 20:
                raise ValueError(f"steps[{index}].attributes must contain at most 20 string values")
            normalized_attributes: dict[str, str] = {}
            for name, value in attributes.items():
                if not isinstance(name, str) or not _ATTRIBUTE.fullmatch(name.strip()) or not isinstance(value, str):
                    raise ValueError(f"steps[{index}].attributes must use SDK names and string values")
                canonical = name.strip().upper()
                if canonical in normalized_attributes or canonical in {"NAME", "POS", "ORI"}:
                    raise ValueError(f"steps[{index}].attributes has a duplicate or dedicated field: {canonical}")
                normalized_attributes[canonical] = value
            step["attributes"] = normalized_attributes
            if "attribute_reference_refno" in raw:
                step["attribute_reference_refno"] = _ref(raw.get("attribute_reference_refno"),
                                                          f"steps[{index}].attribute_reference_refno")
        elif operation == "copy":
            step["source_refno"] = _ref(raw.get("source_refno"), f"steps[{index}].source_refno")
            if not name.startswith("/"):
                raise ValueError(f"steps[{index}].name for copy must start with /")
        else:
            step["target_refno"] = _ref(raw.get("target_refno"), f"steps[{index}].target_refno")
        normalized.append(step)
    return {"equipment_refno": equipment_refno, "steps": normalized, "max_depth": max_depth,
            "limit": limit, "include_native_previews": include_native_previews}


def _sample_identity(sample: Any) -> dict[str, Any]:
    result = sample.get("result") if isinstance(sample, dict) else None
    evidence = result.get("evidence") if isinstance(result, dict) else None
    health_before = evidence.get("health_before") if isinstance(evidence, dict) else None
    health_after = evidence.get("health_after") if isinstance(evidence, dict) else None
    context = result.get("context") if isinstance(result, dict) else None
    context = context if isinstance(context, dict) else {}
    return {
        "version": (health_before.get("version") if isinstance(health_before, dict) else None,
                    health_after.get("version") if isinstance(health_after, dict) else None),
        "session_id": (health_before.get("session_id") if isinstance(health_before, dict) else None,
                       health_after.get("session_id") if isinstance(health_after, dict) else None),
        "bridge_build": (health_before.get("bridge_build") if isinstance(health_before, dict) else None,
                         health_after.get("bridge_build") if isinstance(health_after, dict) else None),
        "project": (context.get("project"), context.get("project")),
        "mdb": (context.get("mdb"), context.get("mdb")),
        "current_element": (context.get("current_element"), context.get("current_element")),
    }


def _context_bracket(before: Any, after: Any) -> dict[str, Any]:
    before_identity, after_identity = _sample_identity(before), _sample_identity(after)
    fields = ("version", "session_id", "bridge_build", "project", "mdb", "current_element")
    comparisons: dict[str, Any] = {}
    issues = []
    for field in fields:
        left = before_identity[field][0]
        right = after_identity[field][1]
        observations = [*before_identity[field], *after_identity[field]]
        if field in {"version", "session_id", "bridge_build"}:
            observations.extend(sample[field] for sample in (before, after)
                                if isinstance(sample, dict) and field in sample)
        known = all(_text(value) for value in observations)
        differs = any(_text(value) and _text(left) and not _same_context_value(field, left, value)
                      for value in observations)
        equal = False if differs else True if known else None
        comparisons[field] = {"before": left, "after": right, "unchanged": equal}
        if equal is False:
            issues.append({"code": "context_changed", "field": field})
        elif equal is None:
            issues.append({"code": "context_unknown", "field": field})
    before_result = before.get("result") if isinstance(before, dict) else None
    after_result = after.get("result") if isinstance(after, dict) else None
    bracket_ready = all(isinstance(sample, dict) and sample.get("ok") is True and
                        sample.get("version") == "E3D21" and isinstance(item, dict) and
                        item.get("context_complete") is True and isinstance(item.get("readiness"), dict) and
                        item["readiness"].get("before") == item["readiness"].get("after") == "ready" and
                        isinstance(item.get("session_coherence"), dict) and
                        item["session_coherence"].get("status") == "consistent"
                        for sample, item in ((before, before_result), (after, after_result)))
    if not bracket_ready:
        issues.append({"code": "context_snapshot_not_verified"})
    same = all(value["unchanged"] is True for value in comparisons.values())
    status = "changed" if any(value["unchanged"] is False for value in comparisons.values()) else \
        "consistent" if bracket_ready and same else "unknown"
    return {"status": status, "atomic": False, "bracketed": True, "fields": comparisons,
            "health_project_health_complete": bracket_ready,
            "schema_route_context_binding": "Required for each schema read; inspect endpoint_context_verified and per-step context_issues.",
            "scope": "Outer health/project/health snapshots and endpoint-specific context readbacks; separate calls are not an atomic database snapshot.",
            "issues": issues}


def _canonical_ref(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if re.fullmatch(r"=[0-9]+/[0-9]+", text):
        return "=" + "/".join(str(int(part)) for part in text[1:].split("/"))
    return text.casefold()


def _same_context_value(field: str, left: Any, right: Any) -> bool:
    if field == "current_element" and isinstance(left, str) and isinstance(right, str):
        return _canonical_ref(left) == _canonical_ref(right)
    return left == right


def _valid_triple(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(
        type(item) in (int, float) and math.isfinite(item) for item in value)


def _name_key(value: Any) -> str | None:
    if not _text(value):
        return None
    return value.strip().removeprefix("/").casefold()


def _manifest_index(manifest: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    rows = manifest["rows"]
    by_ref: dict[str, dict[str, Any]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_ref[_canonical_ref(row["refno"])] = row
        key = _name_key(row.get("name"))
        if key:
            by_name.setdefault(key, []).append(row)
    return by_ref, by_name


def _resolve_manifest_ref(value: str, by_ref: dict[str, dict[str, Any]],
                          by_name: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, Any] | None, str]:
    row = by_ref.get(_canonical_ref(value))
    if row is not None:
        return row, "matched_refno"
    if value.startswith("="):
        return None, "outside_manifest"
    candidates = by_name.get(_name_key(value) or "", [])
    if len(candidates) == 1:
        return candidates[0], "matched_name"
    if len(candidates) > 1:
        return None, "ambiguous_name"
    return None, "outside_manifest"


def _path(row: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chain = []
    current = row
    seen: set[int] = set()
    while current is not None:
        index = current.get("id")
        if type(index) is not int or index in seen or not 0 <= index < len(rows):
            return []
        seen.add(index)
        chain.append({"refno": current.get("refno"), "name": current.get("name"), "type": current.get("type")})
        parent = current.get("parent_index")
        current = rows[parent] if type(parent) is int and 0 <= parent < len(rows) else None
    return list(reversed(chain))


def _schema_read(bridge: BridgeClient, refno: str, attributes: list[str] | None = None, *,
                 include_constraints: bool = False, expected_type: str = "") -> dict[str, Any]:
    return bridge.element_schema(refno, attributes if attributes is not None else ["NAME"], 0, 50,
                                 include_constraints=include_constraints, expected_type=expected_type)


def _schema_result(response: Any, expected_refno: str, expected_type: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(response, dict) or response.get("ok") is not True or response.get("version") != "E3D21":
        return None, "schema_response_unavailable"
    result = response.get("result")
    element = result.get("element") if isinstance(result, dict) else None
    if not isinstance(result, dict) or not isinstance(element, dict):
        return None, "schema_identity_missing"
    if _canonical_ref(element.get("refno")) != _canonical_ref(expected_refno):
        return None, "schema_identity_mismatch"
    if expected_type and str(element.get("type", "")).upper() != expected_type.upper():
        return None, "schema_type_mismatch"
    if result.get("success") is not True or result.get("read_errors") != {} or any(
            not isinstance(result.get(field), list) or any(not isinstance(row, dict) for row in result[field])
            for field in ("child_types", "attributes")):
        return result, "schema_incomplete"
    return result, None


def _context_endpoint_issues(response: Any, expected: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not isinstance(response, dict) or response.get("ok") is not True or response.get("version") != "E3D21":
        return [{"code": "endpoint_unavailable", "stage": stage}]
    result = response.get("result")
    if not isinstance(result, dict):
        return [{"code": "endpoint_result_missing", "stage": stage}]
    for field, code in (("session_id", "session_mismatch"), ("bridge_build", "bridge_build_mismatch")):
        # The native envelope supplies both fields. Any result-level copy is
        # additional evidence and cannot hide a conflicting envelope identity.
        for layer, sample in (("envelope", response), ("result", result)):
            if layer == "result" and field not in sample:
                continue
            observed = sample.get(field)
            if not _text(observed) or observed != expected.get(field):
                issues.append({"code": code, "stage": stage, "field": field, "layer": layer,
                               "expected": expected.get(field), "observed": observed})

    def check(source: str, target: str, observed: Any) -> None:
        context_value = expected.get(target)
        if not _text(observed) or not _text(context_value):
            issues.append({"code": "context_unknown", "stage": stage, "field": source,
                           "observed": observed, "context": context_value})
        elif not _same_context_value(target, observed, context_value):
            issues.append({"code": "context_mismatch", "stage": stage, "field": source,
                           "observed": observed, "context": context_value})

    for target in ("project", "mdb"):
        before_fields = [field for field in (target, target + "_before") if field in result]
        for source in before_fields or [target]:
            check(source, target, result.get(source))
        check(target + "_after", target, result.get(target + "_after"))
    for source in ("ce_before", "ce_after"):
        check(source, "current_element", result.get(source))
    if result.get("identity_unchanged") is not True:
        issues.append({"code": "endpoint_identity_not_verified", "stage": stage,
                       "observed": result.get("identity_unchanged")})
    if result.get("ce_unchanged") is not True:
        issues.append({"code": "endpoint_ce_not_verified", "stage": stage,
                       "observed": result.get("ce_unchanged")})
    if "context" in result:
        nested = result["context"]
        if not isinstance(nested, dict):
            issues.append({"code": "endpoint_context_missing", "stage": stage})
        else:
            for boundary in ("before", "after"):
                sample = nested.get(boundary)
                for field in ("project", "mdb", "current_element"):
                    check(f"context.{boundary}.{field}", field,
                          sample.get(field) if isinstance(sample, dict) else None)
            before_context, after_context = nested.get("before"), nested.get("after")
            before_type = before_context.get("current_element_type") if isinstance(before_context, dict) else None
            after_type = after_context.get("current_element_type") if isinstance(after_context, dict) else None
            if not _text(before_type) or not _text(after_type):
                issues.append({"code": "context_unknown", "stage": stage, "field": "context.current_element_type"})
            elif before_type.upper() != after_type.upper():
                issues.append({"code": "context_mismatch", "stage": stage, "field": "context.current_element_type",
                               "observed": after_type, "context": before_type})
            for flag in ("same_project_instance", "same_mdb_instance", "identity_unchanged",
                         "ce_unchanged", "boundary_agreement"):
                if nested.get(flag) is not True:
                    issues.append({"code": "endpoint_context_not_verified", "stage": stage,
                                   "field": "context." + flag, "observed": nested.get(flag)})
    return issues


def _child_schema_read(bridge: BridgeClient, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        sample = equipment_child_schema(bridge, payload)
    except BridgeError as error:
        if _child_schema_native_fault(error.diagnostics):
            raise
        return {"ok": False, "version": "E3D21", "error": str(error), "diagnostics": error.diagnostics}
    if _child_schema_native_fault(sample):
        raise BridgeError("A native database fault stopped Equipment child schema inspection; do not retry the read.",
                          diagnostics=sample)
    return sample


def _child_context_issues(sample: Any, expected: dict[str, Any], typed_ce: dict[str, str]) -> list[dict[str, Any]]:
    """Bind the child reader's distinct boundary shape without inventing flat fields."""
    stage = "create_child_schema"
    if not isinstance(sample, dict) or sample.get("ok") is not True or sample.get("version") != "E3D21":
        return [{"code": "endpoint_unavailable", "stage": stage}]
    result = sample.get("result")
    if not isinstance(result, dict) or result.get("available") is not True:
        return [{"code": "endpoint_result_unavailable", "stage": stage}]
    context = result.get("context")
    context = context if isinstance(context, dict) else {}
    before, after = (context.get(side) if isinstance(context.get(side), dict) else {} for side in ("before", "after"))
    issues: list[dict[str, Any]] = []
    for layer, observed in (("envelope", sample), ("result", result), ("context.before", before), ("context.after", after)):
        for field, code in (("session_id", "session_mismatch"), ("bridge_build", "bridge_build_mismatch")):
            value = observed.get(field)
            if not _text(value) or value != expected.get(field):
                issues.append({"code": code, "stage": stage, "field": field, "layer": layer,
                               "expected": expected.get(field), "observed": value})
    for side, boundary in (("before", before), ("after", after)):
        if boundary.get("errors") != {}:
            issues.append({"code": "endpoint_context_read_errors", "stage": stage,
                           "field": "context." + side + ".errors", "observed": boundary.get("errors")})
        for field, target in (("project", "project"), ("mdb", "mdb"), ("ce_refno", "current_element")):
            value, wanted = boundary.get(field), expected.get(target)
            if not _text(value) or not _text(wanted) or not _same_context_value(target, value, wanted):
                issues.append({"code": "context_mismatch" if _text(value) and _text(wanted) else "context_unknown",
                               "stage": stage, "field": "context." + side + "." + field,
                               "observed": value, "context": wanted})
        for field in ("ce_base_type", "ce_actual_type"):
            value, wanted = boundary.get(field), typed_ce.get(field, before.get(field))
            if not _text(value) or not _text(wanted) or value.casefold() != wanted.casefold():
                issues.append({"code": "context_mismatch" if _text(value) and _text(wanted) else "context_unknown",
                               "stage": stage, "field": "context." + side + "." + field,
                               "observed": value, "context": wanted})
    for field in ("identity_unchanged", "ce_unchanged"):
        if context.get(field) is not True:
            issues.append({"code": "endpoint_context_not_verified", "stage": stage,
                           "field": "context." + field, "observed": context.get(field)})
    if not issues:
        typed_ce.update({field: before[field] for field in ("ce_base_type", "ce_actual_type")})
    return issues


def _child_type_complete(value: Any, source: str) -> bool:
    return (isinstance(value, dict) and value.get("source") == source and value.get("valid") is True
            and value.get("complete") is True and value.get("errors") == {}
            and all(_text(value.get(field)) for field in ("name", "short_name")))


def _child_identity_complete(value: Any, refno: str, base_type: str | None = None) -> bool:
    if not isinstance(value, dict):
        return False
    binding = value.get("request_binding")
    return (value.get("refno") == refno and value.get("complete") is True and value.get("errors") == {}
            and isinstance(value.get("name"), str) and isinstance(binding, dict) and binding.get("verified") is True
            and binding.get("source") == "explicit_request_resolution"
            and binding.get("requested_refno") == binding.get("resolved_refno") == refno
            and _child_type_complete(value.get("base_type"), "DbElement.ElementType")
            and _child_type_complete(value.get("actual_type"), "DbElement.GetActualType")
            and (base_type is None or value["base_type"]["short_name"].upper() == base_type.upper()))


def _create_child_evidence(sample: Any, payload: dict[str, Any], parent: dict[str, Any], probe: dict[str, Any] | None,
                           item: dict[str, Any]) -> bool:
    """Required create slices exclude the optional type metadata page and future instances."""
    result = sample.get("result") if isinstance(sample, dict) else None
    if not isinstance(result, dict) or sample.get("ok") is not True or result.get("available") is not True:
        item["unknowns"].append({"code": "create_child_schema_unavailable"})
        return False
    summary = {field: result.get(field) for field in ("parent", "requested_type", "parent_creatability",
        "read_target", "read_target_actual_type_matches", "instance_attributes", "type_system_attributes", "scope")}
    summary["required_read_coverage_complete"] = False
    summary["coverage_scope"] = "Required parent/type/creatability and explicitly requested exemplar slices only; type metadata page coverage is separate."
    item["create_type_evidence"] = summary
    valid_request = (result.get("operation") == "equipment_child_schema" and result.get("read_only") is True
        and result.get("single_dispatch") is True and result.get("atomic_snapshot") is False
        and all(result.get("requested_" + key) == payload[key]
                for key in ("parent_refno", "element_type", "read_target_refno", "attributes")))
    parent_ok = valid_request and _child_identity_complete(result.get("parent"), parent["refno"], parent.get("type"))
    kind = result.get("requested_type")
    type_ok = (_child_type_complete(kind, "DbElementType.GetElementType") and
               payload["element_type"].casefold() in {kind["name"].casefold(), kind["short_name"].casefold()})
    creatability = result.get("parent_creatability")
    creatability = creatability if isinstance(creatability, dict) else {}
    creatable = creatability.get("sdk_creatable_now")
    creatability_ok = (parent_ok and type_ok and creatability.get("source") == "DbElement.IsCreatable"
        and creatability.get("scope") == "current_parent_requested_type" and creatability.get("parent_refno") == parent["refno"]
        and creatability.get("requested_element_type") == payload["element_type"]
        and creatability.get("complete") is True and creatability.get("error") is None and type(creatable) is bool)
    for okay, code in ((parent_ok, "create_parent_identity_unverified"), (type_ok, "create_requested_type_unverified"),
                       (creatability_ok, "sdk_creatable_state_unknown"), (result.get("read_errors") == {}, "create_child_schema_read_errors")):
        if not okay:
            item["unknowns"].append({"code": code})
    item["constraints"].append({"name": "parent_child_type", "status":
        "allowed_now" if creatability_ok and creatable is True else "disallowed_now" if creatability_ok and creatable is False else "unknown",
        "parent_type": parent.get("type"), "element_type": payload["element_type"],
        "sdk_creatable_now": creatable if creatability_ok else None, "source": "DbElement.IsCreatable",
        "execution_authorization": False, "native_create_preview_required": True})
    if creatability_ok and creatable is False:
        item["issues"].append({"code": "parent_disallows_child_type"})
    required = parent_ok and type_ok and creatability_ok and result.get("read_errors") == {} and not item["context_issues"]
    probe_ready = False
    if payload["attributes"]:
        target = result.get("read_target")
        target_ok = (payload["read_target_refno"] is not None and
                     _child_identity_complete(target, payload["read_target_refno"], probe.get("type") if probe else None))
        matches = (target_ok and type_ok and result.get("read_target_actual_type_matches") is True
            and all(kind[field].casefold() == target["actual_type"][field].casefold() for field in ("name", "short_name")))
        instance = result.get("instance_attributes")
        instance = instance if isinstance(instance, dict) else {}
        rows = instance.get("attributes")
        rows_ok = (isinstance(rows, list) and len(rows) == len(payload["attributes"])
            and all(isinstance(row, dict) and row.get("requested_name") == name and row.get("complete") is True
                and row.get("errors") == {} and row.get("known") is True and type(row.get("supported")) is bool
                and (row.get("supported") is False or type(row.get("sdk_settable_now")) is bool)
                for row, name in zip(rows, payload["attributes"])))
        instance_ok = (matches and instance.get("source") == "explicit_existing_read_target"
            and instance.get("scope") == "read_target_only" and instance.get("refno") == payload["read_target_refno"]
            and instance.get("complete") is True and instance.get("status") == "observed" and instance.get("reason") is None and rows_ok)
        if not matches:
            item["unknowns"].append({"code": "attribute_reference_actual_type_unverified",
                                     "expected": payload["element_type"], "observed": target.get("actual_type") if isinstance(target, dict) else None})
        elif not instance_ok:
            item["unknowns"].append({"code": "create_exemplar_attributes_incomplete"})
        if instance_ok:
            for row in rows:
                if row["supported"] is False:
                    item["issues"].append({"code": "create_attribute_not_supported", "attribute": row["requested_name"],
                                           "scope": "explicit_existing_read_target_only"})
                elif row["sdk_settable_now"] is not True:
                    item["unknowns"].append({"code": "create_attribute_direct_edit_unavailable", "attribute": row["requested_name"],
                                             "scope": "explicit_existing_read_target_only"})
        required = required and instance_ok
        probe_ready = required and all(row["supported"] is True and row["sdk_settable_now"] is True for row in rows)
    summary["required_read_coverage_complete"] = required
    return probe_ready


def _constraint_read_complete(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("status") != "complete":
        return False
    for field, items_field in (("allowed_values", "values"), ("allowed_ranges", "raw_boundaries")):
        component = value.get(field)
        if not isinstance(component, dict) or component.get("status") not in ("defined", "not_defined"):
            return False
        items = component.get(items_field)
        if component.get("defined") is not (component["status"] == "defined") or \
                component.get("truncated") is not False or not isinstance(items, list) or \
                type(component.get("returned_count")) is not int or \
                type(component.get("total_count")) is not int or \
                len(items) != component["returned_count"] or len(items) != component["total_count"] or \
                component["status"] == "not_defined" and items:
            return False
    optional = value.get("values_or_ranges_optional")
    return isinstance(optional, dict) and optional.get("status") == "complete" and type(optional.get("value")) is bool


def _frame_matches(frame: Any, requested: str) -> bool:
    if not isinstance(frame, dict) or _canonical_ref(frame.get("requested")) != _canonical_ref(requested):
        return False
    try:
        refno = _ref(frame.get("refno"), "frame.refno")
    except ValueError:
        return False
    if not refno.startswith("="):
        return False
    if requested == "WORLD":
        return str(frame.get("type", "")).upper() in {"WORL", "WORLD"}
    if requested.startswith("="):
        return _canonical_ref(refno) == _canonical_ref(requested)
    return _name_key(frame.get("name")) == _name_key(requested)


def _world_pose_complete(result: dict[str, Any]) -> bool:
    position, orientation = result.get("position"), result.get("orientation")
    if result.get("complete") is not True or result.get("errors") != {} or \
            result.get("position_complete") is not True or result.get("orientation_complete") is not True or \
            not _frame_matches(result.get("frame"), "WORLD") or not isinstance(position, dict) or \
            position.get("available") is not True or position.get("unit") != "mm" or \
            not _valid_triple([position.get(axis) for axis in ("x", "y", "z")]) or \
            not isinstance(orientation, dict) or orientation.get("available") is not True:
        return False
    axes = [orientation.get(axis) for axis in ("x_axis", "y_axis", "z_axis")]
    if any(not _valid_triple(axis) or abs(math.hypot(*axis) - 1.0) > 1e-6 for axis in axes):
        return False
    x, y, z = axes
    cross = [x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0]]
    return abs(sum(a * b for a, b in zip(x, y))) <= 1e-6 and all(abs(a - b) <= 1e-6 for a, b in zip(cross, z))


def _frame_ref(wrt: str, parent: dict[str, Any] | None, target: dict[str, Any] | None) -> tuple[str | None, str]:
    if wrt == "WORLD":
        return "WORLD", "explicit_world"
    if wrt.startswith(("/", "=")):
        return wrt, "explicit_frame"
    owner = parent
    if owner is not None:
        return owner.get("refno"), "owner_resolved_to_parent"
    return None, "owner_outside_manifest"


def _transform_read(bridge: BridgeClient, step: dict[str, Any], frame_ref: str | None,
                    step_result: dict[str, Any], evidence: dict[str, Any], index: int) -> dict[str, Any]:
    requested = {"position": step.get("position"), "orientation": step.get("orientation"),
                 "wrt": step.get("wrt"), "resolved_wrt_refno": frame_ref}
    converted = {"status": "not_requested", "position_world": None, "orientation_world": None}
    if frame_ref is None:
        converted.update(status="unknown", reason="The OWNER frame could not be resolved to an observed explicit reference.")
        return {"requested": requested, "converted": converted}
    points = [step["position"]] if step.get("position") is not None else []
    vectors = []
    if step.get("orientation"):
        vectors = [step["orientation"]["x_axis"], step["orientation"]["y_axis"]]
    if not points and not vectors:
        return {"requested": requested, "converted": converted}
    response = bridge.transform_coordinates(frame_ref, "WORLD", points, vectors)
    evidence[f"step_{index}_coordinates"] = response
    result = response.get("result") if isinstance(response, dict) else None
    endpoint_issues = _context_endpoint_issues(response, step_result["context_identity"], f"step_{index}_coordinates")
    step_result["context_issues"].extend(endpoint_issues)
    if endpoint_issues or not isinstance(result, dict) or result.get("complete") is not True or \
            result.get("success") is not True or result.get("available") is not True or result.get("read_errors") != []:
        converted.update(status="unknown", reason="Native coordinate conversion was not complete.")
        return {"requested": requested, "converted": converted}
    from_frame, to_frame = result.get("from_frame"), result.get("to_frame")
    if _canonical_ref(result.get("from_wrt")) != _canonical_ref(frame_ref) or result.get("to_wrt") != "WORLD" or \
            not _frame_matches(from_frame, frame_ref) or not _frame_matches(to_frame, "WORLD"):
        converted.update(status="unknown", reason="Coordinate conversion did not verify the requested source and WORLD frames.")
        return {"requested": requested, "converted": converted}
    output_points, output_vectors = result.get("points"), result.get("vectors")
    if any(not isinstance(outputs, list) or len(outputs) != len(inputs) or any(
            not isinstance(item, dict) or type(item.get("index")) is not int or item["index"] != index or
            not _valid_triple(item.get("input")) or item["input"] != inputs[index]
            for index, item in enumerate(outputs))
            for inputs, outputs in ((points, output_points), (vectors, output_vectors))):
        converted.update(status="unknown", reason="Coordinate results did not cover the exact requested inputs.")
        return {"requested": requested, "converted": converted}
    if points:
        item = output_points[0] if isinstance(output_points, list) and output_points else None
        if not isinstance(item, dict) or item.get("success") is not True or item.get("unit") != "mm" or \
                not _valid_triple(item.get("output")):
            converted.update(status="unknown", reason="The requested position was not converted.")
            return {"requested": requested, "converted": converted}
        converted["position_world"] = item["output"]
    if vectors:
        if not isinstance(output_vectors, list) or len(output_vectors) != 2 or any(
                not isinstance(item, dict) or item.get("success") is not True or not _valid_triple(item.get("output"))
                for item in output_vectors):
            converted.update(status="unknown", reason="The requested orientation was not converted.")
            return {"requested": requested, "converted": converted}
        x_axis, y_axis = output_vectors[0]["output"], output_vectors[1]["output"]
        if any(abs(math.hypot(*axis) - 1.0) > 1e-6 for axis in (x_axis, y_axis)) or \
                abs(sum(x * y for x, y in zip(x_axis, y_axis))) > 1e-6:
            converted.update(status="unknown", reason="Converted orientation axes are not unit and orthogonal.")
            return {"requested": requested, "converted": converted}
        converted["orientation_world"] = {"x_axis": x_axis, "y_axis": y_axis}
    converted["status"] = "complete"
    return {"requested": requested, "converted": converted}


def equipment_model_plan(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    request = _parameters(args)
    response: dict[str, Any] = {
        "schema": 1, "success": False, "complete": False, "status": "unknown",
        "read_coverage_complete": False, "write_viability": "unknown",
        "plan_only": True, "read_only": True, "writes_attempted": False,
        "model_writes": 0, "view_writes": 0, "commands": 0,
        "equipment_refno": request["equipment_refno"], "steps": [],
        "context_consistency": {"status": "unknown", "atomic": False},
        "evidence": {}, "issues": [],
        "native_previews": {"requested": request["include_native_previews"],
            "status": "not_requested" if not request["include_native_previews"] else "not_attempted",
            "complete": None if not request["include_native_previews"] else False,
            "prepared_count": 0, "stopped_on_native_fault": False,
            "preview_context": {"before": None, "after": None, "boundary_agreement": None}},
        "scope": "Read-only plan from separate live E3D observations. Optional create/copy/transform previews use dry_run only and do not perform model edits.",
        "write_viability_scope": "Native preview revisions are valid only for the exact reviewed request and matching session/build/context; they are not engineering approval or permission.",
    }
    before = context_snapshot(bridge, settings)
    response["evidence"]["context_before"] = before
    before_result = before.get("result") if isinstance(before, dict) else None
    if _context_bracket(before, before)["status"] != "consistent":
        response["issues"].append({"code": "initial_context_not_complete"})
        response["steps"] = [{"step_id": step["step_id"], "operation": step["operation"], "status": "unknown",
                              "issues": [], "unknowns": [{"code": "not_read_due_to_context"}],
                              "read_coverage_complete": False, "write_viability": "unknown", "write_steps": []}
                             for step in request["steps"]]
        after = context_snapshot(bridge, settings)
        response["evidence"]["context_after"] = after
        response["context_consistency"] = _context_bracket(before, after)
        response["issues"].extend({"stage": "context_bracket", **issue}
                                   for issue in response["context_consistency"]["issues"])
        return response

    context = before_result.get("context", {})
    health = before.get("result", {}).get("evidence", {}).get("health_before", {})
    context_identity = {"version": health.get("version"), "session_id": health.get("session_id"),
                        "bridge_build": health.get("bridge_build"), "project": context.get("project"),
                        "mdb": context.get("mdb"), "current_element": context.get("current_element")}
    if not all(_text(context_identity.get(field)) for field in ("version", "session_id", "bridge_build", "project", "mdb", "current_element")):
        response["issues"].append({"code": "initial_context_identity_unknown"})
        can_read = False
    else:
        can_read = context_identity["version"] == "E3D21" and settings.mode == "bridge"
        if not can_read:
            response["issues"].append({"code": "native_e3d21_bridge_required"})

    manifest_response = None
    manifest_result = None
    context_tainted = False
    if can_read:
        manifest_response = equipment_manifest(bridge, {"refno": request["equipment_refno"],
            "max_depth": request["max_depth"], "limit": request["limit"], "attributes": []})
        response["evidence"]["equipment_manifest"] = manifest_response
        manifest_result = manifest_response.get("result") if isinstance(manifest_response, dict) else None
        endpoint_issues = _context_endpoint_issues(manifest_response, context_identity, "equipment_manifest")
        response["issues"].extend({"stage": "equipment_manifest", **issue} for issue in endpoint_issues)
        context_tainted = bool(endpoint_issues)
        if not isinstance(manifest_result, dict) or manifest_result.get("complete") is not True:
            response["issues"].append({"code": "equipment_manifest_incomplete"})
            can_read = False
        if context_tainted:
            can_read = False

    rows: list[dict[str, Any]] = manifest_result.get("rows", []) if isinstance(manifest_result, dict) else []
    by_ref, by_name = _manifest_index(manifest_result) if manifest_result and rows else ({}, {})
    schema_cache: dict[tuple[str, tuple[str, ...], bool, str], dict[str, Any]] = {}
    child_schema_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
    child_typed_ce: dict[str, str] = {}
    planned_names: dict[str, set[str]] = {}
    if can_read:
        for index, step in enumerate(request["steps"]):
            item: dict[str, Any] = {"step_id": step["step_id"], "operation": step["operation"],
                                    "status": "unknown", "issues": [], "unknowns": [], "constraints": [],
                                    "parent": None, "target": None, "source": None, "requested_transform": None,
                                    "revision": {}, "write_steps": [], "context_issues": []}
            parent = target = source = probe = None
            child_probe_ready = False
            if step["operation"] in {"create", "copy"}:
                parent, how = _resolve_manifest_ref(step["parent_refno"], by_ref, by_name)
                item["parent"] = {"requested_refno": step["parent_refno"], "resolution": how,
                                  "identity": {key: parent.get(key) for key in ("refno", "name", "type")} if parent else None,
                                  "hierarchy_path": _path(parent, rows) if parent else []}
                if parent is None:
                    item["unknowns"].append({"code": "parent_not_in_complete_equipment_manifest"})
            if step["operation"] == "transform":
                target, how = _resolve_manifest_ref(step["target_refno"], by_ref, by_name)
                item["target"] = {"requested_refno": step["target_refno"], "resolution": how,
                                  "identity": {key: target.get(key) for key in ("refno", "name", "type")} if target else None,
                                  "hierarchy_path": _path(target, rows) if target else []}
                if target is None:
                    item["unknowns"].append({"code": "target_not_in_complete_equipment_manifest"})
            if step["operation"] == "copy":
                source, how = _resolve_manifest_ref(step["source_refno"], by_ref, by_name)
                item["source"] = {"requested_refno": step["source_refno"], "resolution": how,
                                  "identity": {key: source.get(key) for key in ("refno", "name", "type")} if source else None,
                                  "hierarchy_path": _path(source, rows) if source else []}
                if source is None:
                    item["unknowns"].append({"code": "source_not_in_complete_equipment_manifest"})

            if step["operation"] == "create":
                if step.get("attributes") and step.get("attribute_reference_refno"):
                    probe, how = _resolve_manifest_ref(step["attribute_reference_refno"], by_ref, by_name)
                    if probe is None:
                        item["unknowns"].append({"code": "attribute_reference_not_in_equipment_manifest", "resolution": how})
                if parent is not None:
                    child_payload = {"parent_refno": parent["refno"], "element_type": step["element_type"],
                        "read_target_refno": probe["refno"] if probe else None,
                        "attributes": list(step.get("attributes", {})), "offset": 0, "limit": 50}
                    child_key = (parent["refno"], step["element_type"], child_payload["read_target_refno"],
                                 tuple(child_payload["attributes"]), child_payload["offset"], child_payload["limit"])
                    if child_key not in child_schema_cache:
                        child_schema_cache[child_key] = _child_schema_read(bridge, child_payload)
                        response["evidence"][f"create_child_schema_{len(child_schema_cache)}"] = child_schema_cache[child_key]
                    sample = child_schema_cache[child_key]
                    item["context_issues"].extend(_child_context_issues(sample, context_identity, child_typed_ce))
                    child_probe_ready = _create_child_evidence(sample, child_payload, parent, probe, item)

            if parent is not None and step["operation"] == "copy":
                schema_key = (_canonical_ref(parent["refno"]), (), False, "")
                if schema_key not in schema_cache:
                    schema_cache[schema_key] = _schema_read(bridge, parent["refno"])
                    response["evidence"][f"schema_parent_{len(schema_cache)}"] = schema_cache[schema_key]
                item["context_issues"].extend(_context_endpoint_issues(
                    schema_cache[schema_key], context_identity, "parent_schema"))
                parent_schema, schema_error = _schema_result(schema_cache[schema_key], parent["refno"], parent.get("type"))
                if schema_error:
                    item["unknowns"].append({"code": schema_error, "stage": "parent_schema"})
                else:
                    wanted_type = step.get("element_type") or (source.get("type") if source else None)
                    matches = [entry for entry in parent_schema.get("child_types", [])
                               if isinstance(entry, dict) and str(entry.get("short_name", entry.get("name", ""))).upper() ==
                               str(wanted_type).upper()]
                    if not _text(wanted_type):
                        item["unknowns"].append({"code": "child_type_unobserved"})
                    elif not matches:
                        item["constraints"].append({"name": "parent_child_type", "status": "not_listed",
                                                    "parent_type": parent.get("type"), "element_type": wanted_type})
                        item["issues"].append({"code": "child_type_not_advertised_by_parent"})
                    elif len(matches) != 1:
                        item["unknowns"].append({"code": "parent_child_type_schema_ambiguous"})
                    else:
                        creatable = matches[0].get("sdk_creatable_now")
                        item["constraints"].append({"name": "parent_child_type", "status":
                            "allowed_now" if creatable is True else "disallowed_now" if creatable is False else "unknown",
                            "parent_type": parent.get("type"), "element_type": wanted_type,
                            "sdk_creatable_now": creatable})
                        if creatable is False:
                            item["issues"].append({"code": "parent_disallows_child_type"})
                        elif creatable is not True:
                            item["unknowns"].append({"code": "sdk_creatable_state_unknown"})

            if step["operation"] == "create" and step.get("attributes"):
                probe_ref = step.get("attribute_reference_refno")
                if not probe_ref:
                    item["constraints"].append({"name": "create_attributes", "status": "unknown",
                                                "reason": "No existing same-type attribute_reference_refno was supplied."})
                    item["unknowns"].append({"code": "create_attribute_constraints_unobserved"})
                else:
                    if probe is None:
                        pass  # The explicit manifest lookup above already recorded the missing exemplar.
                    elif not child_probe_ready:
                        item["unknowns"].append({"code": "create_attribute_constraints_unobserved"})
                    elif probe.get("type") != step["element_type"]:
                        # The legacy constraints route proves only its base type.
                        # Never downgrade a matching actual UDET to that base.
                        item["unknowns"].append({"code": "create_attribute_constraints_actual_type_unavailable",
                                                 "expected": step["element_type"], "legacy_base_type": probe.get("type")})
                    else:
                        details = []
                        observed_names: list[str] = []
                        names = list(step["attributes"])
                        for chunk_index in range(0, len(names), 10):
                            chunk = names[chunk_index:chunk_index + 10]
                            key = (_canonical_ref(probe["refno"]), tuple(chunk), True, step["element_type"])
                            if key not in schema_cache:
                                schema_cache[key] = _schema_read(bridge, probe["refno"], chunk,
                                    include_constraints=True, expected_type=step["element_type"])
                                response["evidence"][f"schema_attributes_{len(schema_cache)}"] = schema_cache[key]
                            item["context_issues"].extend(_context_endpoint_issues(
                                schema_cache[key], context_identity, "attribute_schema"))
                            probe_schema, schema_error = _schema_result(schema_cache[key], probe["refno"], step["element_type"])
                            if schema_error:
                                item["unknowns"].append({"code": schema_error, "stage": "attribute_schema"})
                                continue
                            if probe_schema.get("constraints_requested") is not True or \
                                    probe_schema.get("constraints_complete") is not True:
                                item["unknowns"].append({"code": "attribute_constraints_incomplete"})
                            for attribute in probe_schema.get("attributes", []):
                                requested_name = attribute.get("requested_name")
                                if requested_name not in chunk or requested_name in observed_names:
                                    item["unknowns"].append({"code": "attribute_schema_coverage_mismatch"})
                                    continue
                                observed_names.append(requested_name)
                                details.append({"name": attribute.get("name", attribute.get("requested_name")),
                                    "requested_name": requested_name,
                                    "submitted_value": step["attributes"][requested_name],
                                    "known": attribute.get("known"), "supported": attribute.get("supported"),
                                    "data_type": attribute.get("data_type"),
                                    "sdk_settable_now_on_probe": attribute.get("sdk_settable_now"),
                                    "direct_edit_supported": attribute.get("direct_edit_supported"),
                                    "constraints": attribute.get("constraints"),
                                    "value_validation": "not_performed; native create preview/setter is authoritative"})
                                if attribute.get("supported") is False or attribute.get("known") is False:
                                    item["issues"].append({"code": "create_attribute_not_supported", "attribute":
                                        attribute.get("name", attribute.get("requested_name")),
                                        "supported": attribute.get("supported"), "known": attribute.get("known")})
                                elif any(attribute.get(flag) is not True for flag in
                                         ("known", "supported", "direct_edit_supported", "sdk_settable_now")):
                                    item["unknowns"].append({"code": "create_attribute_direct_edit_unavailable", "attribute":
                                        attribute.get("name", attribute.get("requested_name")),
                                        "direct_edit_supported": attribute.get("direct_edit_supported"),
                                        "sdk_settable_now": attribute.get("sdk_settable_now")})
                                if not _constraint_read_complete(attribute.get("constraints")):
                                    item["unknowns"].append({"code": "attribute_constraints_incomplete",
                                                             "attribute": requested_name})
                        item["constraints"].extend(details)
                        if set(observed_names) != set(names):
                            item["unknowns"].append({"code": "attribute_schema_incomplete"})

            if step["operation"] == "copy" and source is not None:
                source_type = str(source.get("type", "")).upper()
                item["constraints"].append({"name": "copy_source_type", "status":
                    "potentially_supported" if source_type in _COPY_ROOT_TYPES else
                    "known_unsupported_root" if source_type == "EQUI" else "unknown",
                    "type": source_type, "native_copy_preview_required": True})
                if source_type == "EQUI":
                    item["issues"].append({"code": "equipment_root_copy_not_supported"})
                elif source_type not in _COPY_ROOT_TYPES:
                    item["unknowns"].append({"code": "copy_source_type_support_unknown"})

            if step["operation"] == "transform" and target is not None:
                target_schema_key = (_canonical_ref(target["refno"]), ("ORI", "POS"), False, "")
                if target_schema_key not in schema_cache:
                    schema_cache[target_schema_key] = _schema_read(bridge, target["refno"], ["POS", "ORI"])
                    response["evidence"][f"schema_transform_{len(schema_cache)}"] = schema_cache[target_schema_key]
                item["context_issues"].extend(_context_endpoint_issues(
                    schema_cache[target_schema_key], context_identity, "target_schema"))
                target_schema, schema_error = _schema_result(schema_cache[target_schema_key], target["refno"], target.get("type"))
                if schema_error:
                    item["unknowns"].append({"code": schema_error, "stage": "target_schema"})
                else:
                    for field in ("POS", "ORI"):
                        matches = [attribute for attribute in target_schema.get("attributes", [])
                                   if str(attribute.get("requested_name", "")).upper() == field]
                        row = matches[0] if len(matches) == 1 else None
                        required = step.get("position") is not None if field == "POS" else step.get("orientation") is not None
                        if required:
                            flags = ("known", "supported", "sdk_settable_now")
                            if row is None or any(type(row.get(flag)) is not bool for flag in flags):
                                item["unknowns"].append({"code": "transform_attribute_settable_unverified", "attribute": field})
                            elif any(row[flag] is False for flag in flags):
                                item["issues"].append({"code": "transform_attribute_not_settable", "attribute": field,
                                    "supported": row.get("supported"), "sdk_settable_now": row.get("sdk_settable_now")})
                spatial = bridge.spatial_info(target["refno"], "WORLD")
                response["evidence"][f"spatial_target_{index}"] = spatial
                item["context_issues"].extend(_context_endpoint_issues(
                    spatial, context_identity, f"spatial_target_{index}"))
                spatial_result = spatial.get("result") if isinstance(spatial, dict) else None
                if not isinstance(spatial_result, dict) or spatial_result.get("success") is not True or \
                        _canonical_ref(spatial_result.get("refno")) != _canonical_ref(target.get("refno")) or \
                        str(spatial_result.get("type", "")).upper() != str(target.get("type", "")).upper():
                    item["unknowns"].append({"code": "target_world_pose_unavailable_or_identity_mismatch"})
                else:
                    item["target"]["world_pose"] = {"position": spatial_result.get("position"),
                        "orientation": spatial_result.get("orientation"),
                        "position_complete": spatial_result.get("position_complete"),
                        "orientation_complete": spatial_result.get("orientation_complete")}
                    if not _world_pose_complete(spatial_result):
                        item["unknowns"].append({"code": "target_world_pose_partial"})

            frame_parent = parent
            if frame_parent is None and target is not None and type(target.get("parent_index")) is int:
                parent_index = target["parent_index"]
                if 0 <= parent_index < len(rows):
                    frame_parent = rows[parent_index]
            frame_ref, frame_resolution = _frame_ref(step["wrt"], frame_parent, target)
            if step["operation"] == "create" and item["context_issues"]:
                item["requested_transform"] = {"requested": {"wrt": step["wrt"]},
                    "converted": {"status": "unknown", "reason": "Child schema context was not verified; no further reads were attempted."}}
            else:
                item["requested_transform"] = _transform_read(bridge, step, frame_ref, {
                    "context_identity": context_identity, "context_issues": item["context_issues"]}, response["evidence"], index)
            item["unknowns"].extend(item["context_issues"])
            if item["context_issues"]:
                context_tainted = True
            item["frame_resolution"] = frame_resolution
            if item["requested_transform"]["converted"]["status"] == "unknown":
                item["unknowns"].append({"code": "requested_transform_not_world_verified"})

            name_parent = parent.get("refno") if parent else None
            if name_parent and "name" in step:
                existing_keys = {_name_key(row.get("name")) for row in rows
                                 if row.get("parent_index") == parent.get("id") and _name_key(row.get("name"))}
                plan_key = (name_parent, _name_key(step["name"]))
                duplicates = planned_names.setdefault(name_parent, set())
                if plan_key[1] in existing_keys or plan_key[1] in duplicates:
                    item["issues"].append({"code": "observed_sibling_name_collision", "name": step["name"]})
                duplicates.add(plan_key[1])
                item["constraints"].append({"name": "sibling_name_collision", "status":
                    "collision" if plan_key[1] in existing_keys or sum(1 for prior in response["steps"]
                       if prior.get("parent", {}).get("identity", {}).get("refno") == name_parent and
                       _name_key(prior.get("planned_name")) == plan_key[1]) > 0 else "no_observed_sibling_collision",
                    "scope": "Complete manifest direct siblings only; native preview remains authoritative."})
            item["planned_name"] = step.get("name")

            if step["operation"] == "create":
                item["revision"] = {"native_token": None, "source": "not_requested",
                    "required_for_execution": True,
                    "next_step": "Call e3d_create_element with the exact request and dry_run=true; use only its returned revision for a later execution."}
                item["write_steps"] = [
                    {"order": 1, "tool": "e3d_create_element", "dry_run": True,
                     "purpose": "Native create validation and current revision; this plan did not call it."},
                    {"order": 2, "tool": "e3d_create_element", "dry_run": False,
                     "precondition": "Same request, expected_revision from step 1, and explicitly enabled command permission."},
                    {"order": 3, "tool": "e3d_element_snapshot", "purpose": "Verify exact created identity, parentage and initialized attributes."},
                ]
            elif step["operation"] == "copy":
                item["revision"] = {"native_token": None, "source": "not_requested",
                    "required_for_execution": True,
                    "next_step": "Call e3d_copy_element with the exact request and dry_run=true; inspect name_mapping and use only its returned revision for a later execution."}
                item["write_steps"] = [
                    {"order": 1, "tool": "e3d_copy_element", "dry_run": True,
                     "purpose": "Native copy eligibility, name mapping and revision; this plan did not call it."},
                    {"order": 2, "tool": "e3d_copy_element", "dry_run": False,
                     "precondition": "Same request, expected_revision from step 1, and explicitly enabled command permission."},
                    {"order": 3, "tool": "e3d_element_snapshot", "purpose": "Verify copied subtree identities and destination parentage."},
                ]
            else:
                item["revision"] = {"native_token": None, "source": "not_requested",
                    "required_for_execution": True,
                    "next_step": "Call e3d_set_transform with the exact request and dry_run=true; use only its returned revision for a later execution."}
                item["write_steps"] = [
                    {"order": 1, "tool": "e3d_spatial_info", "purpose": "Re-read target pose and parent immediately before any edit."},
                    {"order": 2, "tool": "e3d_transform_coordinates", "purpose": "Re-convert the requested values from the same explicit frame."},
                    {"order": 3, "tool": "e3d_set_transform", "dry_run": True,
                     "purpose": "Native validation and current target revision."},
                    {"order": 4, "tool": "e3d_set_transform", "dry_run": False,
                     "precondition": "Same request and expected_revision from step 3; explicitly enabled command permission."},
                    {"order": 5, "tool": "e3d_spatial_info", "purpose": "Verify WORLD position/orientation and inspect notification/readback result."},
                ]
            if context_tainted:
                item["unknowns"].append({"code": "context_changed_or_unverified_during_plan"})
            item["status"] = "blocked" if item["issues"] else "unknown" if item["unknowns"] else "ready"
            item["write_viability"] = ("blocked" if item["issues"] else
                "candidate_for_native_preview" if item["status"] == "ready" else "unknown")
            response["steps"].append(item)
            if context_tainted:
                break

    if len(response["steps"]) < len(request["steps"]):
        completed = {item["step_id"].casefold() for item in response["steps"]}
        response["steps"].extend({"step_id": step["step_id"], "operation": step["operation"],
            "status": "unknown", "issues": [], "unknowns": [{"code": "step_not_inspected_due_to_incomplete_context_or_manifest"}],
            "constraints": [], "write_steps": [], "revision": {}, "context_issues": [],
            "write_viability": "unknown"}
            for step in request["steps"] if step["step_id"].casefold() not in completed)

    after = context_snapshot(bridge, settings)
    response["evidence"]["context_after"] = after
    bracket = _context_bracket(before, after)
    response["context_consistency"] = bracket
    response["issues"].extend({"stage": "context_bracket", **issue} for issue in bracket["issues"])
    response["context_consistency"]["endpoint_checks"] = {
        "equipment_manifest": [issue for issue in response["issues"] if issue.get("stage") == "equipment_manifest"],
        "schema_spatial_and_coordinate_endpoints": [issue for step in response["steps"]
            for issue in step.get("context_issues", [])]}
    endpoint_issues = [*bracket["endpoint_checks"]["equipment_manifest"],
                       *bracket["endpoint_checks"]["schema_spatial_and_coordinate_endpoints"]]
    bracket["endpoint_context_verified"] = not endpoint_issues
    if endpoint_issues and bracket["status"] == "consistent":
        changed = any(issue.get("code") == "context_mismatch" or
                      issue.get("code") in {"session_mismatch", "bridge_build_mismatch"} and _text(issue.get("observed")) or
                      issue.get("observed") is False for issue in endpoint_issues)
        bracket["status"] = "changed" if changed else "unknown"
    context_changed = bracket["status"] != "consistent"
    if context_changed:
        response["status"] = "unknown"
        for step in response["steps"]:
            step["status"] = "unknown"
            step["unknowns"].append({"code": "outer_context_changed_or_unverified"})
    elif any(step.get("status") == "blocked" for step in response["steps"]):
        response["status"] = "blocked"
    elif any(step.get("status") != "ready" for step in response["steps"]) or bracket["status"] != "consistent":
        response["status"] = "unknown"
    else:
        response["status"] = "ready"
    response["success"] = response["status"] == "ready"
    response["complete"] = response["success"]
    response["read_coverage_complete"] = _plan_read_coverage_complete(response, bracket)
    for step in response["steps"]:
        step["read_coverage_complete"] = bracket["status"] == "consistent" and not step.get("unknowns") and not step.get("context_issues")
        step["write_viability"] = ("blocked" if step["status"] == "blocked" else
            "candidate_for_native_preview" if step["status"] == "ready" and step["read_coverage_complete"] else "unknown")
    response["write_viability"] = ("blocked" if response["status"] == "blocked" else
        "candidate_for_native_preview" if response["status"] == "ready" and response["read_coverage_complete"]
        else "unknown")
    if request.pop("include_native_previews"):
        _prepare_native_previews(bridge, settings, response, request["steps"], context_identity)
    response["plan_fingerprint"] = hashlib.sha256(json.dumps(
        {"request": request, "equipment_refno": request["equipment_refno"],
         "session_id": context_identity.get("session_id"), "steps": response["steps"]},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")).hexdigest()
    response["plan_fingerprint_scope"] = "Advisory hash of this plan/evidence only; never accepted as expected_revision."
    return response


def _prepare_native_previews(bridge: BridgeClient, settings: Settings, response: dict[str, Any],
                             steps: list[dict[str, Any]], context: dict[str, Any]) -> None:
    previews = response["native_previews"]
    if response["status"] != "ready" or not response["read_coverage_complete"]:
        previews["status"] = "skipped_plan_not_ready"
        return
    before = response["evidence"]["context_after"]
    previews["preview_context"]["before"] = before
    observed = []
    for item, step in zip(response["steps"], steps):
        operation = step["operation"]
        tool = "e3d_set_transform" if operation == "transform" else f"e3d_{operation}_element"
        arguments = {"dry_run": True, **{key: step[key] for key in
                     ("name", "element_type", "attributes", "position", "orientation", "wrt") if key in step}}
        for argument, subject in (("parent_refno", "parent"), ("source_refno", "source"), ("refno", "target")):
            if item[subject] is not None:
                arguments[argument] = item[subject]["identity"]["refno"]
        preview = model_edit_preview(bridge, tool, arguments, context)
        item["native_preview"] = preview
        if preview["status"] in {"native_fault", "dispatch_failed"}:
            previews["stopped_on_native_fault"] = preview["status"] == "native_fault"
            previews["status"] = ("stopped_on_native_fault" if previews["stopped_on_native_fault"]
                                  else "preview_dispatch_failed")
            return
        if preview["status"] == "observed":
            native_result = preview.get("response", {}).get("result", {})
            token = native_result.get("revision") if isinstance(native_result, dict) else None
            if not isinstance(token, str) or _SHA.fullmatch(token) is None:
                preview["status"] = "unverified"
                continue
            observed.append((item, token))

    after = context_snapshot(bridge, settings)
    previews["preview_context"]["after"] = after
    bracket = _context_bracket(before, after)
    agreement = {"consistent": True, "changed": False}.get(bracket["status"])
    previews["preview_context"]["boundary_agreement"] = agreement
    if agreement is not True:
        previews["status"] = "context_unverified"
        for item, _ in observed:
            item["native_preview"]["status"] = "context_unverified"
        return

    for item, token in observed:
        preview = item["native_preview"]
        item["revision"].update(native_token=token, source="native_dry_run",
                                next_step="Use the exact native preview arguments and its revision.")
        preview["status"] = "prepared"
    previews["prepared_count"] = len(observed)
    previews["complete"] = len(observed) == len(steps)
    previews["status"] = "prepared" if previews["complete"] else "partial_or_unknown"


def _plan_read_coverage_complete(response: dict[str, Any], bracket: dict[str, Any]) -> bool:
    evidence = response.get("evidence")
    steps = response.get("steps")
    if not isinstance(evidence, dict) or not isinstance(steps, list) or bracket.get("status") != "consistent":
        return False
    manifest = evidence.get("equipment_manifest")
    manifest_result = manifest.get("result") if isinstance(manifest, dict) else None
    if not steps or not isinstance(manifest, dict) or manifest.get("ok") is not True or manifest.get("version") != "E3D21" or \
            not isinstance(manifest_result, dict) or manifest_result.get("complete") is not True:
        return False
    for key, sample in evidence.items():
        if key.startswith("create_child_schema_"):
            # The reader's overall complete flag also requires an exemplar and
            # its metadata page. Plans consume the named required slices only.
            continue
        if key.startswith("schema_"):
            result = sample.get("result") if isinstance(sample, dict) else None
            if not isinstance(sample, dict) or sample.get("ok") is not True or \
                    sample.get("version") != "E3D21" or not isinstance(result, dict) or \
                    result.get("success") is not True or result.get("read_errors") != {}:
                return False
        elif key.startswith("spatial_target_"):
            result = sample.get("result") if isinstance(sample, dict) else None
            if not isinstance(sample, dict) or sample.get("ok") is not True or sample.get("version") != "E3D21" or \
                    not isinstance(result, dict) or result.get("success") is not True or not _world_pose_complete(result):
                return False
        elif key.startswith("step_") and key.endswith("_coordinates"):
            result = sample.get("result") if isinstance(sample, dict) else None
            if not isinstance(sample, dict) or sample.get("ok") is not True or sample.get("version") != "E3D21" or \
                    not isinstance(result, dict) or result.get("success") is not True or \
                    result.get("complete") is not True or result.get("read_errors") != []:
                return False
    return all(not step.get("unknowns") and not step.get("context_issues")
               and (step.get("operation") != "create" or
                    step.get("create_type_evidence", {}).get("required_read_coverage_complete") is True)
               for step in steps)


def register_equipment_model_plan(server: McpServer, bridge: BridgeClient, settings: Settings) -> None:
    reference = {"type": "string", "minLength": 2, "maxLength": _EQUIPMENT_REF_MAX_LENGTH,
                 "pattern": r"^(?:/[^*?\s]+|=[0-9]+/[0-9]+)$",
                 "description": "Explicit /NAME or =database/element; must resolve inside the supplied EQUI manifest where noted."}
    vector = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
    step_properties = {
        "step_id": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]{0,31}$"},
        "operation": {"type": "string", "enum": ["create", "copy", "transform"]},
        "parent_refno": reference, "target_refno": reference, "source_refno": reference,
        "element_type": {"type": "string", "minLength": 1, "maxLength": 64},
        "name": {"type": "string", "minLength": 1, "maxLength": 50},
        "attributes": {"type": "object", "maxProperties": 20, "additionalProperties": {"type": "string"}},
        "attribute_reference_refno": reference,
        "position": vector,
        "orientation": {"type": "object", "properties": {"x_axis": vector, "y_axis": vector},
                        "required": ["x_axis", "y_axis"], "additionalProperties": False},
        "wrt": {"type": "string", "minLength": 1, "maxLength": 1024, "default": "OWNER"},
    }
    step_schema = {"type": "object", "properties": step_properties,
                   "required": ["step_id", "operation"],
                   "oneOf": [
                       {"properties": {"operation": {"const": "create"}},
                        "required": ["parent_refno", "element_type", "name"]},
                       {"properties": {"operation": {"const": "copy"}},
                        "required": ["parent_refno", "source_refno", "name"]},
                       {"properties": {"operation": {"const": "transform"}},
                        "required": ["target_refno"],
                        "anyOf": [{"required": ["position"]}, {"required": ["orientation"]}]},
                   ], "additionalProperties": False}
    server.add_tool(
        TOOL,
        "Build a read-only, bounded plan for up to 16 explicit create, copy or transform steps under one EQUI. "
        "Reads one complete equipment manifest, native parent/target schemas, optional same-type attribute constraints, "
        "WORLD poses and frame conversions, bracketed by fresh context snapshots. Set include_native_previews=true to request the exact create/copy/transform dry_run routes and receive native create/copy revisions when context remains stable. Inspect status, context_consistency, "
        "step parent paths, constraints, unknowns and write_steps. A ready result is not engineering approval. "
        "e3d_set_transform uses a native expected_revision guard; the separate e3d_modeling_team workflow can execute guarded transform tasks. This planner remains read-only and does not execute model writes, PML, selection, display or capture.",
        {"type": "object", "properties": {
            "equipment_refno": {**reference, "description": "Explicit EQUI /NAME or DBREF; the complete bounded manifest root."},
            "steps": {"type": "array", "items": step_schema, "minItems": 1, "maxItems": 16},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 32, "default": 32},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 200},
            "include_native_previews": {"type": "boolean", "default": False,
                "description": "Request exact native dry_run previews after the read-only plan is complete. No model writes are performed."},
        }, "required": ["equipment_refno", "steps"], "additionalProperties": False},
        lambda args: equipment_model_plan(bridge, settings, args), read_only=True,
    )
