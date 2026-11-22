"""Read separate type, parent, and explicit existing-exemplar evidence once.

Neither a manifest base type nor SystemAttributes witnesses a future instance.
Only an explicit existing read target with a matching actual SDK type can supply
instance support/settable observations. No convenient exemplar is searched for.
"""
from __future__ import annotations

import re
from typing import Any

from .bridge import BridgeClient, BridgeError, element_access_refnos
from .protocol import McpServer


VERSION = "E3D21"
TOOL = "e3d_equipment_child_schema"
PATH = "/api/v1/equipment/child-schema"
MAX_ATTRIBUTES, MAX_PAGE_SIZE = 20, 200
MAX_REF_LENGTH, MAX_NATIVE_ARRAY_INSPECTION = 256, 4096
_NAME = re.compile(r"[A-Za-z0-9_:]{1,64}")
_INT32_TEN_DIGITS = (r"1[0-9]{9}|20[0-9]{8}|21[0-3][0-9]{7}|214[0-6][0-9]{6}|2147[0-3][0-9]{5}|"
                     r"21474[0-7][0-9]{4}|214748[0-2][0-9]{3}|2147483[0-5][0-9]{2}|21474836[0-3][0-9]|214748364[0-7]")
_REFERENCE_PATTERN = (r"^\s*(?=[^\s]{2,256}\s*$)(?:/[^\s*?\x00-\x1f\x7f-\x9f]+|=0*(?:[1-9][0-9]{0,8}|"
                      + _INT32_TEN_DIGITS + r")/0*(?:[0-9]{1,9}|" + _INT32_TEN_DIGITS + r"))\s*$")
_DBREF = re.compile(r"=[1-9][0-9]{0,9}/(?:0|[1-9][0-9]{0,9})")
_SCOPE = {
    "future_child_attributes_observed": False,
    "actual_type_requires_existing_object": True,
    "instance_attribute_target": "explicit_existing_read_target_only",
    "type_system_attributes_are_instance_attributes": False,
    "sdk_creatable_now_is_execution_authorization": False,
    "permissions_verified": None,
    "proposed_values_validated": False,
    "engineering_validated": None,
    "constraints_observed": False,
    "whole_equipment_coverage": False,
}
_METADATA = {"known", "name", "short_name", "data_type", "is_array", "is_pseudo", "is_uda", "unit_kind", "maximum_size"}


def _ref(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must explicitly identify /NAME or =database/element")
    ref = element_access_refnos([value])[0]
    if len(ref) > MAX_REF_LENGTH or any(char in ref for char in "*?"):
        raise ValueError(f"{field} must be an explicit reference of at most 256 characters without wildcards")
    if ref.startswith("="):
        ref = "=" + "/".join(str(int(part)) for part in ref[1:].split("/"))
    return ref


def _parameters(args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict) or set(args) - {"parent_refno", "element_type", "read_target_refno", "attributes", "offset", "limit"}:
        raise ValueError("Only parent_refno, element_type, read_target_refno, attributes, offset and limit are accepted")
    parent = _ref(args.get("parent_refno"), "parent_refno")
    kind = args.get("element_type")
    if not isinstance(kind, str) or not _NAME.fullmatch(kind.strip()):
        raise ValueError("element_type must be an explicit ASCII SDK type name; include the colon for a UDET")
    raw = args.get("attributes", [])
    if not isinstance(raw, list) or len(raw) > MAX_ATTRIBUTES:
        raise ValueError("attributes must contain at most 20 explicit SDK names")
    names = []
    for name in raw:
        if not isinstance(name, str) or not _NAME.fullmatch(name.strip()):
            raise ValueError("attributes must contain explicit ASCII SDK names")
        names.append(name.strip().upper())
    if len(names) != len(set(names)):
        raise ValueError("attributes must be unique after normalization")
    offset, limit = args.get("offset", 0), args.get("limit", 50)
    if type(offset) is not int or not 0 <= offset <= 100000:
        raise ValueError("offset must be an integer between 0 and 100000")
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("limit must be an integer between 1 and 200")
    target = args.get("read_target_refno")
    return {"parent_refno": parent, "element_type": kind.strip().upper(),
            "read_target_refno": None if target is None else _ref(target, "read_target_refno"),
            "attributes": names, "offset": offset, "limit": limit}


def _need(condition: bool, field: str) -> None:
    if not condition:
        raise ValueError(f"Invalid equipment child schema evidence: {field}")


def _text(value: Any, limit: int = 1024, *, empty: bool = False) -> bool:
    return isinstance(value, str) and len(value.encode("utf-16-le", errors="surrogatepass")) // 2 <= limit and (empty or bool(value.strip()))


def _errors(value: Any, allowed: set[str]) -> dict[str, str]:
    _need(isinstance(value, dict) and value.keys() <= allowed and all(_text(message) for message in value.values()), "read errors")
    return value


def _dbref(value: Any) -> bool:
    return isinstance(value, str) and bool(_DBREF.fullmatch(value)) and all(int(part) <= 2147483647 for part in value[1:].split("/"))


def _type(row: Any, source: str) -> bool:
    _need(isinstance(row, dict) and set(row) == {"source", "valid", "name", "short_name", "errors", "complete"}
          and row["source"] == source, "SDK type provenance")
    errors = _errors(row["errors"], {"resolve", "valid", "name", "short_name"})
    _need(row["valid"] is None or type(row["valid"]) is bool, "type validity")
    for field, maximum in (("name", 256), ("short_name", 64)):
        _need(row[field] is None if row["valid"] is not True or field in errors else _text(row[field], maximum), "type " + field)
    complete = row["valid"] is True and not errors
    _need(row["complete"] is complete, "type completeness")
    return complete


def _identity(row: Any) -> bool:
    _need(isinstance(row, dict) and set(row) == {"refno", "name", "base_type", "actual_type", "request_binding", "errors", "complete"}
          and _dbref(row["refno"]), "existing object identity")
    binding = row["request_binding"]
    _need(isinstance(binding, dict) and set(binding) == {"source", "requested_refno", "resolved_refno", "observed_name", "verified"}
          and binding["source"] == "explicit_request_resolution" and binding["verified"] is True
          and binding["resolved_refno"] == row["refno"]
          and isinstance(binding["requested_refno"], str), "captured explicit request resolution")
    errors = _errors(row["errors"], {"name", "base_type", "actual_type", "request_binding"})
    _need(row["name"] is None if "name" in errors else _text(row["name"], empty=True), "existing object NAME")
    base = _type(row["base_type"], "DbElement.ElementType")
    actual = _type(row["actual_type"], "DbElement.GetActualType")
    complete = not errors and base and actual
    _need(row["complete"] is complete, "object identity completeness")
    return complete


def _binding(row: dict[str, Any], requested: str) -> None:
    captured = row["request_binding"]
    _need(captured["requested_refno"] == requested, "captured request binding")
    if requested.startswith("="):
        _need(row["refno"] == requested and captured["observed_name"] is None
              and "request_binding" not in row["errors"], "numeric explicit reference binding")
    else:
        observed = captured["observed_name"]
        _need(_text(observed) and observed.lstrip("/").casefold() == requested.lstrip("/").casefold(), "captured explicit NAME binding")
        # A second NAME getter may fail after resolution already verified the
        # captured name and canonical DBREF. A real later NAME drift retains
        # both readings only with the native binding error and an incomplete
        # identity; the overall completeness check also stays false.
        drift = row["name"] is not None and row["name"].lstrip("/").casefold() != observed.lstrip("/").casefold()
        _need(("request_binding" in row["errors"]) is drift and (not drift or row["complete"] is False), "second NAME binding")


def _attribute(row: Any, *, requested: str | None = None, index: int | None = None) -> bool:
    extra = {"requested_name", "supported", "sdk_settable_now"} if requested is not None else {"sdk_index"}
    _need(isinstance(row, dict) and set(row) == _METADATA | {"errors", "complete"} | extra, "attribute scope and shape")
    allowed = _METADATA | ({"resolve", "supported", "sdk_settable_now"} if requested is not None else set())
    errors = _errors(row["errors"], allowed)
    _need(row["known"] is None or type(row["known"]) is bool, "attribute validity")
    for field in _METADATA - {"known"}:
        value = row[field]
        if row["known"] is not True or field in errors:
            _need(value is None, "unknown attribute " + field)
        elif field in {"is_array", "is_pseudo", "is_uda"}:
            _need(type(value) is bool, "attribute " + field)
        elif field == "maximum_size":
            _need(type(value) is int and -2147483648 <= value <= 2147483647, "SDK maximum size")
        else:
            _need(_text(value, 256 if field == "name" else 64), "attribute " + field)
    if requested is None:
        _need(type(row["sdk_index"]) is int and row["sdk_index"] == index, "SystemAttributes index")
    else:
        _need(row["requested_name"] == requested, "requested attribute")
        for field in ("supported", "sdk_settable_now"):
            unknown = row["known"] is not True or field in errors or field == "sdk_settable_now" and row["supported"] is not True
            _need(row[field] is None if unknown else type(row[field]) is bool, "existing-target " + field)
    complete = row["known"] is True and not errors
    _need(row["complete"] is complete, "attribute completeness")
    return complete


def _boundary(row: Any, session: str, build: str) -> None:
    fields = {"project", "mdb", "ce_refno", "ce_base_type", "ce_actual_type"}
    _need(isinstance(row, dict) and set(row) == fields | {"session_id", "bridge_build", "errors"}, "context boundary")
    _need(row["session_id"] == session and row["bridge_build"] == build, "boundary session and build binding")
    errors = _errors(row["errors"], fields)
    for field in fields:
        _need(row[field] is None if field in errors else _dbref(row[field]) if field == "ce_refno" else _text(row[field], 256), "boundary " + field)


def _validate(reply: Any, payload: dict[str, Any]) -> None:
    _need(isinstance(reply, dict) and reply.get("ok") is True and reply.get("version") == VERSION
          and all(_text(reply.get(field), 256) for field in ("session_id", "bridge_build")), "envelope")
    result = reply.get("result")
    _need(isinstance(result, dict) and result.get("operation") == "equipment_child_schema"
          and all(result.get(field) is True for field in ("available", "read_only", "single_dispatch"))
          and result.get("atomic_snapshot") is False and result.get("scope") == _SCOPE, "observation scope")
    _need(set(result) == {"operation", "available", "read_only", "single_dispatch", "atomic_snapshot", "success", "complete", "session_id", "bridge_build",
          "requested_parent_refno", "requested_element_type", "requested_read_target_refno", "requested_attributes", "parent", "requested_type",
          "parent_creatability", "type_system_attributes", "read_target", "read_target_actual_type_matches", "instance_attributes", "read_errors", "context", "scope"}, "result shape")
    _need(all(type(result["scope"][key]) is type(value) for key, value in _SCOPE.items()), "scope flag types")
    _need(all(result[field] == reply[field] for field in ("session_id", "bridge_build")), "result session and build binding")
    for key in ("parent_refno", "element_type", "read_target_refno", "attributes"):
        _need(result.get("requested_" + key) == payload[key], "request " + key)
    parent_complete = _identity(result.get("parent"))
    _binding(result["parent"], payload["parent_refno"])
    type_complete = _type(result.get("requested_type"), "DbElementType.GetElementType")
    creatable = result.get("parent_creatability")
    _need(isinstance(creatable, dict) and set(creatable) == {"source", "scope", "parent_refno", "requested_element_type", "sdk_creatable_now", "error", "complete"}
          and creatable["source"] == "DbElement.IsCreatable" and creatable["scope"] == "current_parent_requested_type"
          and creatable["parent_refno"] == result["parent"]["refno"] and creatable["requested_element_type"] == payload["element_type"], "parent IsCreatable provenance")
    if not type_complete:
        _need(creatable["sdk_creatable_now"] is None and creatable["error"] is None, "unresolved type creatability")
    elif creatable["error"] is not None:
        _need(_text(creatable["error"]) and creatable["sdk_creatable_now"] is None, "unknown creatability")
    else:
        _need(type(creatable["sdk_creatable_now"]) is bool, "creatability observation")
    creatable_complete = type_complete and creatable["error"] is None
    _need(creatable["complete"] is creatable_complete, "creatability completeness")
    system = result.get("type_system_attributes")
    _need(isinstance(system, dict) and set(system) == {"source", "scope", "offset", "limit", "total_count", "returned_count", "has_more", "next_offset", "attributes", "errors", "complete", "whole_type_coverage", "native_arrays_materialized_before_output_limits", "native_array_inspection_limit", "inspection_limited"}
          and system["source"] == "DbElementType.SystemAttributes" and system["scope"] == "requested_type_only"
          and system["native_arrays_materialized_before_output_limits"] is True
          and type(system["native_array_inspection_limit"]) is int and system["native_array_inspection_limit"] == MAX_NATIVE_ARRAY_INSPECTION
          and type(system["offset"]) is int and system["offset"] == payload["offset"]
          and type(system["limit"]) is int and system["limit"] == payload["limit"], "type-only SystemAttributes provenance")
    system_errors = _errors(system["errors"], {"type", "system_attributes", "inspection_limit"})
    rows, total = system["attributes"], system["total_count"]
    _need(isinstance(rows, list) and type(system["returned_count"]) is int and system["returned_count"] == len(rows) <= payload["limit"], "SystemAttributes count")
    if total is None:
        _need(bool(system_errors) and not rows and system["has_more"] is None and system["next_offset"] is None
              and system["complete"] is False and system["whole_type_coverage"] is False
              and system["inspection_limited"] is None and "inspection_limit" not in system_errors, "unknown type attributes")
    elif system["inspection_limited"] is True:
        _need(type(total) is int and MAX_NATIVE_ARRAY_INSPECTION < total <= 2147483647
              and system_errors.keys() == {"inspection_limit"} and not rows
              and system["has_more"] is None and system["next_offset"] is None
              and system["complete"] is False and system["whole_type_coverage"] is False, "SystemAttributes inspection cutoff")
    else:
        _need(not system_errors and system["inspection_limited"] is False
              and type(total) is int and 0 <= total <= MAX_NATIVE_ARRAY_INSPECTION
              and len(rows) == min(payload["limit"], max(0, total - payload["offset"])), "SDK attribute page")
        has_more = payload["offset"] + len(rows) < total
        _need(system["has_more"] is has_more and (type(system["next_offset"]) is int and system["next_offset"] == payload["offset"] + len(rows)
              if has_more else system["next_offset"] is None), "attribute page continuation")
        observations = [_attribute(row, index=payload["offset"] + i) for i, row in enumerate(rows)]
        _need(system["complete"] is all(observations)
              and system["whole_type_coverage"] is (all(observations) and payload["offset"] == 0 and not has_more), "attribute page coverage")
    target, matches = result.get("read_target"), result.get("read_target_actual_type_matches")
    target_complete = False
    if target is not None:
        _need(payload["read_target_refno"] is not None, "target was explicit")
        target_complete = _identity(target)
        _binding(target, payload["read_target_refno"])
    _need(matches is None or type(matches) is bool, "actual type match")
    if matches is not None:
        _need(target is not None and type_complete and target["actual_type"]["complete"] is True, "actual type match witnesses")
        same = all(result["requested_type"][key].casefold() == target["actual_type"][key].casefold() for key in ("name", "short_name"))
        _need(matches is same, "actual UDET equality; a base-type match is insufficient")
    instance = result.get("instance_attributes")
    _need(isinstance(instance, dict) and set(instance) == {"source", "scope", "refno", "status", "reason", "attributes", "complete"}
          and instance["source"] == "explicit_existing_read_target" and instance["scope"] == "read_target_only"
          and instance["refno"] == (target["refno"] if target is not None else None), "instance evidence provenance")
    if matches is True:
        values = instance["attributes"]
        _need(isinstance(values, list) and len(values) == len(payload["attributes"]) and instance["reason"] is None, "explicit-target attribute selection")
        observed = [_attribute(row, requested=name) for row, name in zip(values, payload["attributes"])]
        _need(instance["complete"] is all(observed) and instance["status"] == ("observed" if all(observed) else "partial"), "instance completeness")
    else:
        expected = "read_target_required" if payload["read_target_refno"] is None else "read_target_unavailable" if target is None \
            else "requested_type_unknown" if not type_complete else "actual_type_mismatch" if matches is False else "actual_type_unknown"
        _need(instance["status"] == "unknown" and instance["reason"] == expected
              and instance["attributes"] == [] and instance["complete"] is False, "unobserved instance attributes remain unknown")
    errors = _errors(result.get("read_errors"), {"read_target"})
    context = result.get("context")
    _need(isinstance(context, dict) and set(context) == {"before", "after", "identity_unchanged", "ce_unchanged"}, "context observations")
    _boundary(context["before"], reply["session_id"], reply["bridge_build"])
    _boundary(context["after"], reply["session_id"], reply["bridge_build"])
    for flag, keys in (("identity_unchanged", ("project", "mdb")), ("ce_unchanged", ("ce_refno", "ce_base_type", "ce_actual_type"))):
        left, right = context["before"], context["after"]
        known_drift = any(left[key] is not None and right[key] is not None and left[key] != right[key] for key in keys)
        unknown = any(left[key] is None or right[key] is None for key in keys)
        value = context[flag]
        _need(value is None or type(value) is bool, "context agreement flag")
        if known_drift:
            _need(value is False, "context drift")
        elif unknown:
            # A native project/MDB object replacement is still a known drift
            # even if a name could not be read. Text alone cannot disprove it.
            _need(value is None or flag == "identity_unchanged" and value is False, "unknown context")
        elif flag == "ce_unchanged":
            _need(value is True, "CE agreement")
        else:
            _need(type(value) is bool, "project/MDB object agreement")
    complete = not errors and type_complete and parent_complete and creatable_complete and system["complete"] is True \
        and instance["complete"] is True and target_complete and context["identity_unchanged"] is True and context["ce_unchanged"] is True
    _need(result.get("complete") is complete and result.get("success") is complete, "overall evidence completeness")


def _native_fault(value: Any) -> bool:
    if isinstance(value, dict):
        return (value.get("has_fault") is True or value.get("native_fault") is True
                or value.get("native_fault_latched") is True
                or value.get("error_code") in ("native_fault", "native_database_fault")
                or any(_native_fault(item) for item in value.values()))
    return isinstance(value, list) and any(_native_fault(item) for item in value)


def equipment_child_schema(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    """Read the optional native endpoint once; never fall back to parent schema."""
    payload = _parameters(args)
    version = bridge._selected_version(None)

    def unavailable(code: str, message: str) -> dict[str, Any]:
        return {"ok": True, "version": version, "result": {
            "operation": "equipment_child_schema", "available": False, "success": False, "complete": False,
            "read_only": True, "atomic_snapshot": False, "scope": dict(_SCOPE),
            "instance_attributes": {"status": "unknown", "attributes": [], "reason": code},
            "read_errors": [{"code": code, "message": message}],
        }}

    if version != VERSION or bridge.settings.mode != "bridge":
        return unavailable("native_unavailable", "The child schema reader requires the native E3D21 bridge.")
    try:
        reply = bridge._request("POST", PATH, payload, version=VERSION)
    except BridgeError as error:
        if _native_fault(error.diagnostics):
            raise
        if error.http_status == 404:
            return unavailable("native_feature_unavailable", "The child schema endpoint is not exposed by this Addin.")
        raise
    if _native_fault(reply):
        raise BridgeError("A native database fault stopped Equipment child schema inspection; do not retry the read.", diagnostics=reply)
    try:
        _validate(reply, payload)
    except ValueError as error:
        return unavailable("invalid_native_response", str(error))
    return reply


def register_equipment_child_schema(server: McpServer, bridge: BridgeClient) -> None:
    server.add_tool(
        TOOL,
        "Read three independent E3D21 SDK evidence scopes in one UI callback: the explicit existing Design parent's "
        "IsCreatable observation for the requested type; that type's SystemAttributes metadata; and optional "
        "explicit existing read-target attribute support/settable observations only when its actual SDK type "
        "matches the requested type, including UDETs. The read target is an exemplar and need not be a child of "
        "the parent; no ownership relationship is asserted. Without a read target, instance attributes remain unknown "
        "while parent/type evidence may be returned. Type SystemAttributes are not future-instance attributes. "
        "SDK arrays materialize before paging; pages and the 4096-entry inspection cutoff are not hard SDK memory "
        "limits. At most 20 explicit exemplar attributes and 200 type metadata rows per page are returned. "
        "This is read-only evidence, not a creation preview, permission/revision check, proposed-value validation, "
        "constraint observation, or engineering validation. Inspect partial read errors and session/build/project/MDB/"
        "typed CE boundaries. No model or CE changes.",
        {"type": "object", "properties": {
            "parent_refno": {"type": "string", "minLength": 2, "pattern": _REFERENCE_PATTERN,
                             "description": "Explicit existing Design /NAME or =database/element; trimmed reference is at most 256 characters. No CE or wildcards."},
            "element_type": {"type": "string", "pattern": r"^\s*[A-Za-z0-9_:]{1,64}\s*$",
                             "description": "Explicit SDK type name, including the colon for a UDET; trimmed and uppercased."},
            "read_target_refno": {"type": ["string", "null"], "default": None, "minLength": 2, "pattern": _REFERENCE_PATTERN,
                                  "description": "Optional explicit existing Design exemplar, with the same reference rules as parent_refno. Does not establish parent/child ownership."},
            "attributes": {"type": "array", "maxItems": MAX_ATTRIBUTES, "uniqueItems": True, "default": [],
                           "items": {"type": "string", "pattern": r"^\s*[A-Za-z0-9_:]{1,64}\s*$"},
                           "description": "0 to 20 explicit existing-target SDK attribute names, unique after trimming and uppercasing; never future child attributes."},
            "offset": {"type": "integer", "minimum": 0, "maximum": 100000, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_PAGE_SIZE, "default": 50},
        }, "required": ["parent_refno", "element_type"], "additionalProperties": False},
        lambda args: equipment_child_schema(bridge, args), read_only=True,
    )
