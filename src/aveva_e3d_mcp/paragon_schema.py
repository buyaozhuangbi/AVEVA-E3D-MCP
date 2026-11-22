"""Bounded native schema observations on one explicit current Catalogue target.

MemberTypes and DatabaseTypes describe the native base type. IsCreatable is
only a current SDK observation, not a complete PARAGON creation check.
Attribute metadata and constraints concern this current target only; neither
future member attributes nor proposed values, permissions or engineering rules
are validated. No Design edit policy is consulted.
"""
from __future__ import annotations

import math
from typing import Any

from .bridge import BridgeClient, BridgeError
from .paragon_catalogue import (
    MAX_REF_LENGTH, _boundary, _identity, _native_fault, _parameters as _manifest_parameters, _text,
)
from .protocol import McpServer


TOOL = "e3d_paragon_catalogue_schema"
PATH = "/api/v1/paragon/catalogue-schema"
VERSION = "E3D21"
MAX_ATTRIBUTES, MAX_CHILD_TYPES, MAX_DATABASE_TYPES = 10, 200, 32
MAX_CONSTRAINT_ITEMS, MAX_CONSTRAINT_TEXT = 100, 1024
_METADATA = {"known", "supported", "name", "short_name", "data_type", "is_array", "is_pseudo",
             "is_uda", "unit_kind", "maximum_size"}
_SCOPE = {
    "kind": "catalogue_parent_schema", "attribute_target": "current_target", "member_type_basis": "native_base_type",
    "future_child_attributes_observed": False, "whole_project_coverage": False,
    "current_module": None, "permissions_verified": None, "proposed_values_validated": False,
    "engineering_validated": None, "range_unit_basis": "unknown", "range_boundary_encoding": "unknown",
    "optionality_is_requiredness": False,
}
_OUTPUT_LIMITS = {
    "child_types": MAX_CHILD_TYPES, "database_types_per_child": MAX_DATABASE_TYPES, "attributes": MAX_ATTRIBUTES,
    "values_or_boundaries_per_attribute": MAX_CONSTRAINT_ITEMS,
    "constraint_text_utf16_characters": MAX_CONSTRAINT_TEXT, "native_arrays_materialized_before_output_limits": True,
}


def _parameters(args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict) or set(args) - {"refno", "attributes", "include_constraints"}:
        raise ValueError("Only refno, attributes and include_constraints are accepted")
    include = args.get("include_constraints", False)
    if type(include) is not bool:
        raise ValueError("include_constraints must be a boolean")
    # Pure input normalization; a one-row budget lets all 10 explicit names pass.
    validated = _manifest_parameters({"refno": args.get("refno"), "limit": 1, "attributes": args.get("attributes", [])})
    if include and not validated["attributes"]:
        raise ValueError("include_constraints requires 1 to 10 explicitly named current-target attributes")
    return {"refno": validated["refno"], "attributes": validated["attributes"], "include_constraints": include}


def _need(condition: bool, field: str) -> None:
    if not condition:
        raise ValueError(f"Invalid PARAGON Catalogue schema evidence: {field}")


def _errors(value: Any, allowed: set[str], field: str) -> dict:
    _need(isinstance(value, dict) and value.keys() <= allowed and all(_text(message, nonempty=True) for message in value.values()), field)
    return value


def _int32(value: Any) -> bool:
    return type(value) is int and -2147483648 <= value <= 2147483647


def _constraint_text(value: Any) -> bool:
    return value is None or isinstance(value, str) and len(value.encode("utf-16-le", errors="surrogatepass")) // 2 <= MAX_CONSTRAINT_TEXT


def _constraints(result: Any) -> bool:
    _need(isinstance(result, dict) and set(result) == {
        "status", "allowed_values", "allowed_ranges", "values_or_ranges_optional"}, "constraint shape")
    has_error = truncated = False
    for key, list_key in (("allowed_values", "values"), ("allowed_ranges", "raw_boundaries")):
        row = result[key]
        expected_fields = {"status", "defined", list_key, "total_count", "returned_count", "truncated", "error"}
        if key == "allowed_ranges":
            expected_fields |= {"non_finite_indices", "unit_basis", "boundary_encoding"}
        _need(isinstance(row, dict) and set(row) == expected_fields and isinstance(row["status"], str)
              and row["status"] in {"defined", "not_defined", "read_error"}, key)
        items = row[list_key]
        count, total = row["returned_count"], row["total_count"]
        _need(isinstance(items, list) and type(count) is int and count == len(items) <= MAX_CONSTRAINT_ITEMS, key + " count")
        if row["status"] == "read_error":
            has_error = True
            _need(row["defined"] is None and total is None and row["truncated"] is None and not items
                  and _text(row["error"], nonempty=True), key + " unknown")
        elif row["status"] == "not_defined":
            _need(row["defined"] is False and type(total) is int and total == 0 and not items
                  and row["truncated"] is False and row["error"] is None, key + " not defined")
        else:
            _need(row["defined"] is True and type(total) is int and total >= count
                  and count == min(total, MAX_CONSTRAINT_ITEMS) and type(row["truncated"]) is bool
                  and row["error"] is None, key + " defined")
        text_cut = False
        if key == "allowed_values":
            for item in items:
                _need(isinstance(item, dict) and set(item) == {"value", "description", "value_truncated", "description_truncated"}
                      and all(_constraint_text(item[name]) for name in ("value", "description"))
                      and all(type(item[name]) is bool for name in ("value_truncated", "description_truncated")), "allowed value text")
                for name in ("value", "description"):
                    if item[name + "_truncated"]:
                        _need(isinstance(item[name], str) and len(item[name].encode("utf-16-le", errors="surrogatepass")) // 2
                              in (MAX_CONSTRAINT_TEXT, MAX_CONSTRAINT_TEXT - 1), "bounded UTF-16 text")
                text_cut |= item["value_truncated"] or item["description_truncated"]
        else:
            _need(row["unit_basis"] == "unknown" and row["boundary_encoding"] == "unknown", "raw range semantics")
            _need(all(type(value) is float and math.isfinite(value)
                      or type(value) is int and -1.7976931348623157e308 <= value <= 1.7976931348623157e308 or
                      isinstance(value, str) and value in {"NaN", "Infinity", "-Infinity"} for value in items), "raw boundaries")
            _need(row["non_finite_indices"] == [i for i, value in enumerate(items) if isinstance(value, str)], "non-finite boundary markers")
        if row["status"] == "defined":
            _need(row["truncated"] is (total > count or text_cut), key + " output truncation")
        truncated |= row["truncated"] is True
    optional = result["values_or_ranges_optional"]
    _need(isinstance(optional, dict) and set(optional) == {"status", "value", "error"}, "constraint optionality")
    if optional["status"] == "complete":
        _need(type(optional["value"]) is bool and optional["error"] is None, "values-or-ranges optionality")
    else:
        _need(optional["status"] == "read_error" and optional["value"] is None and _text(optional["error"], nonempty=True), "unknown optionality")
        has_error = True
    status = "partial" if has_error else "truncated" if truncated else "complete"
    _need(result["status"] == status, "constraint completeness")
    return status == "complete"


def _attribute(field: Any, requested: str, include: bool) -> tuple[bool, bool]:
    _need(isinstance(field, dict) and set(field) == _METADATA | {
        "requested_name", "errors", "metadata_complete", "complete", "constraints"} and field["requested_name"] == requested,
        "current-target attribute row")
    errors = _errors(field["errors"], _METADATA, "attribute metadata errors")
    known = field["known"]
    _need(known is None or type(known) is bool, "SDK attribute validity")
    if known is not True:
        _need(all(field[name] is None for name in _METADATA - {"known"})
              and (errors.keys() == {"known"} if known is None else not errors), "unverified attribute metadata")
    else:
        for name in _METADATA - {"known"}:
            if name in errors:
                _need(field[name] is None, "unknown metadata " + name)
            elif name in {"supported", "is_array", "is_pseudo", "is_uda"}:
                _need(type(field[name]) is bool, "metadata " + name)
            elif name == "maximum_size":
                _need(_int32(field[name]), "SDK maximum size")
            else:
                _need(_text(field[name], 256 if name == "name" else 64, nonempty=True), "metadata " + name)
    metadata_complete = known is True and not errors
    _need(field["metadata_complete"] is metadata_complete, "metadata completeness")
    constraints_complete = False
    if not include:
        _need(field["constraints"] is None, "constraints not requested")
    elif known is not True or field["supported"] is not True:
        reason = "unknown_attribute" if known is False else "attribute_not_verified" if known is None else \
            "attribute_not_supported" if field["supported"] is False else "attribute_support_unknown"
        _need(field["constraints"] == {"status": "not_attempted", "reason": reason}, "constraints remain unverified")
    else:
        constraints_complete = _constraints(field["constraints"])
    _need(field["complete"] is (metadata_complete and (not include or constraints_complete)), "attribute completeness")
    return metadata_complete, constraints_complete


def _child(row: Any, index: int) -> bool:
    _need(isinstance(row, dict) and set(row) == {"sdk_index", "valid", "name", "short_name", "database_type_codes",
          "database_types_total_count", "database_types_truncated", "catalogue_supported", "sdk_creatable_now", "errors", "complete"}
          and type(row["sdk_index"]) is int and row["sdk_index"] == index, "native member type row")
    errors = _errors(row["errors"], {"type", "valid", "name", "short_name", "database_type_codes", "sdk_creatable_now"}, "child type errors")
    valid = row["valid"]
    _need(valid is None or type(valid) is bool, "native member type validity")
    if valid is not True:
        _need(all(row[name] is None for name in {"name", "short_name", "database_type_codes", "database_types_total_count",
              "database_types_truncated", "catalogue_supported", "sdk_creatable_now"}) and bool(errors), "unknown member type")
    else:
        for name, maximum in (("name", 256), ("short_name", 64)):
            _need(row[name] is None if name in errors else _text(row[name], maximum, nonempty=True), "native child " + name)
        _need(row["sdk_creatable_now"] is None if "sdk_creatable_now" in errors else type(row["sdk_creatable_now"]) is bool,
              "current parent SDK IsCreatable")
        codes, count = row["database_type_codes"], row["database_types_total_count"]
        if "database_type_codes" in errors:
            _need(codes is None and count is None and row["database_types_truncated"] is None
                  and row["catalogue_supported"] is None, "unknown DatabaseTypes")
        else:
            _need(isinstance(codes, list) and type(count) is int and count >= 0 and len(codes) == min(count, MAX_DATABASE_TYPES)
                  and all(_int32(code) for code in codes) and row["database_types_truncated"] is (count > len(codes)), "native database codes")
            support = True if 2 in codes else None if count > len(codes) else False
            _need(row["catalogue_supported"] is support, "Catalogue compatibility observation")
    complete = valid is True and not errors and row["database_types_truncated"] is False
    _need(row["complete"] is complete, "child type observation completeness")
    return complete


def _validate(reply: Any, payload: dict[str, Any]) -> None:
    _need(isinstance(reply, dict) and reply.get("ok") is True and reply.get("version") == VERSION
          and all(_text(reply.get(name), 256, nonempty=True) for name in ("session_id", "bridge_build")), "native envelope")
    result = reply.get("result")
    _need(isinstance(result, dict) and result.get("operation") == "paragon_catalogue_schema"
          and all(result.get(name) is True for name in ("available", "read_only", "single_dispatch"))
          and result.get("atomic_snapshot") is False, "read-only observation")
    _need(result.get("requested_refno") == payload["refno"] and result.get("requested_attributes") == payload["attributes"]
          and result.get("include_constraints") is payload["include_constraints"], "request echo")
    target = result.get("target")
    _identity(target, catalogue=True)
    _need("name" in target and "name_error" in target and (
        target["name"] is None and _text(target["name_error"], nonempty=True)
        or _text(target["name"], nonempty=True) and target["name_error"] is None), "native target NAME")
    refno = payload["refno"]
    _need(target["refno"] == refno if refno.startswith("=") else target["name"] is not None
          and target["name"].removeprefix("/").upper() == refno[1:].upper(), "explicit target binding")
    read_errors = _errors(result.get("read_errors"), {"member_types"}, "top-level read errors")
    children, observation = result.get("child_types"), result.get("child_types_observation")
    _need(isinstance(children, list) and len(children) <= MAX_CHILD_TYPES and isinstance(observation, dict)
          and set(observation) == {"limit", "returned_count", "total_count", "truncated", "complete", "order", "native_arrays_materialized_before_output_limits"}
          and observation["limit"] == MAX_CHILD_TYPES and type(observation["returned_count"]) is int
          and observation["returned_count"] == len(children) and observation["order"] == "sdk_member_types"
          and observation["native_arrays_materialized_before_output_limits"] is True, "bounded MemberTypes observation")
    total = observation["total_count"]
    if total is None:
        _need(not children and observation["truncated"] is None and "member_types" in read_errors, "unknown MemberTypes")
    else:
        _need(type(total) is int and total >= len(children) and len(children) == min(total, MAX_CHILD_TYPES)
              and observation["truncated"] is (total > len(children)), "MemberTypes count and truncation")
    child_flags = [_child(row, index) for index, row in enumerate(children)]
    children_complete = total is not None and observation["truncated"] is False and all(child_flags) and not read_errors
    _need(observation["complete"] is children_complete, "MemberTypes completeness")
    fields = result.get("attributes")
    _need(isinstance(fields, list) and len(fields) == len(payload["attributes"]), "explicit attribute count")
    attribute_flags = [_attribute(row, name, payload["include_constraints"]) for row, name in zip(fields, payload["attributes"])]
    metadata_complete = all(item[0] for item in attribute_flags)
    constraints_complete = all(item[1] for item in attribute_flags) if payload["include_constraints"] else None
    _need(result.get("attributes_complete") is metadata_complete and result.get("constraints_complete") is constraints_complete,
          "attribute section completeness")
    _need(result.get("output_limits") == _OUTPUT_LIMITS and result.get("scope") == _SCOPE, "schema budgets and semantic scope")
    context = result.get("context")
    _need(isinstance(context, dict) and set(context) == {"before", "after", "same_project_instance", "same_mdb_instance", "boundary_agreement"}, "context boundaries")
    before, after = context["before"], context["after"]
    for sample in (before, after):
        _boundary(sample, reply["session_id"], reply["bridge_build"])
    instances = context["same_project_instance"], context["same_mdb_instance"]
    _need(all(value is None or type(value) is bool for value in instances), "context instance observations")
    mismatch = any(value is False for value in instances) or any(
        before[name] is not None and after[name] is not None and before[name] != after[name]
        for name in ("session_id", "bridge_build", "project", "mdb", "current_element"))
    unknown = any(value is None for value in instances) or bool(before["errors"] or after["errors"])
    agreement = False if mismatch else None if unknown else True
    _need(context["boundary_agreement"] is agreement, "boundary agreement")
    complete = (target["name_error"] is None and children_complete and metadata_complete
                and (not payload["include_constraints"] or constraints_complete is True) and agreement is True)
    _need(result.get("complete") is complete and result.get("success") is complete, "observation completeness")


def paragon_catalogue_schema(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    payload = _parameters(args)
    version = bridge._selected_version(None)

    def unavailable(code: str, message: str) -> dict[str, Any]:
        return {"ok": True, "version": version, "result": {
            "operation": "paragon_catalogue_schema", "available": False, "success": False, "complete": False,
            "read_only": True, "single_dispatch": None, "atomic_snapshot": False,
            "requested_refno": payload["refno"], "requested_attributes": payload["attributes"],
            "include_constraints": payload["include_constraints"], "target": None,
            "child_types": [], "child_types_observation": None, "attributes": [],
            "attributes_complete": False, "constraints_complete": None,
            "read_errors": [{"code": code, "message": message}],
            "context": {"before": None, "after": None, "same_project_instance": None, "same_mdb_instance": None, "boundary_agreement": None},
            "scope": dict(_SCOPE),
        }}

    if version != VERSION or bridge.settings.mode != "bridge":
        return unavailable("native_unavailable", "PARAGON Catalogue schema requires E3D21 bridge mode.")
    try:
        reply = bridge._request("POST", PATH, payload, version=VERSION)
    except BridgeError as error:
        if _native_fault(error.diagnostics):
            raise
        if error.http_status == 404:
            return unavailable("native_feature_unavailable", "The Addin does not expose paragon_catalogue_schema.")
        raise
    if _native_fault(reply):
        raise BridgeError("A native database fault stopped Catalogue schema inspection; do not retry the read.", diagnostics=reply)
    try:
        _validate(reply, payload)
    except ValueError as error:
        return unavailable("invalid_native_response", str(error))
    return reply


def register_paragon_catalogue_schema(server: McpServer, bridge: BridgeClient) -> None:
    server.add_tool(
        TOOL,
        "Inspect one explicit current E3D21 Catalogue parent in one UI callback. Returns native base-type MemberTypes, "
        "their raw DbType codes and parent IsCreatable observations, capped at 200 member types and 32 database "
        "codes per type. `sdk_creatable_now` only records the current parent's SDK state; it does not authorize "
        "PARAGON NEW or establish complete creation validation. Explicit attributes and optional "
        "SDK allowed values/descriptions, raw ranges and values-or-ranges optionality concern only this current target; "
        "future child attributes, proposed values, permissions and engineering rules remain unverified. Constraints are "
        "capped at 100 values/boundaries per attribute and 1024 UTF-16 characters per text. The result sets "
        "`future_child_attributes_observed=false`. Native arrays are already materialized before output limits apply. "
        "Inspect errors and session/build/project/MDB/typed CE boundaries. No model or CE changes.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "minLength": 1, "maxLength": MAX_REF_LENGTH,
                      "description": "Explicit Catalogue /NAME or =database/element reference; never CE or wildcards."},
            "attributes": {"type": "array", "maxItems": MAX_ATTRIBUTES, "uniqueItems": True, "default": [],
                           "items": {"type": "string", "minLength": 1, "maxLength": 64},
                           "description": "SDK metadata on the explicit current target. Empty reads member types only."},
            "include_constraints": {"type": "boolean", "default": False,
                                    "description": "Requires 1 to 10 explicit current-target attributes. Not future child attributes."},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: paragon_catalogue_schema(bridge, args), read_only=True,
    )
