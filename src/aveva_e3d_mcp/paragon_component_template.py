"""Read bounded observations of one existing E3D21 Catalogue SCOM.

Only the explicit numeric REF is a caller argument. The native envelope and
its observation boundaries own the session and build. The reader issues one
POST and never replaces missing evidence with a different read.
"""
from __future__ import annotations

import re
from typing import Any

from .bridge import BridgeClient, BridgeError, _contains_native_fault
from .protocol import McpServer


VERSION = "E3D21"
TOOL = "e3d_paragon_component_template"
PATH = "/api/v1/paragon/component-template"
OPERATION = "paragon_component_template"

_INT32_TEN_DIGITS = (
    r"1[0-9]{9}|20[0-9]{8}|21[0-3][0-9]{7}|214[0-6][0-9]{6}|2147[0-3][0-9]{5}|"
    r"21474[0-7][0-9]{4}|214748[0-2][0-9]{3}|2147483[0-5][0-9]{2}|"
    r"21474836[0-3][0-9]|214748364[0-7]"
)
_POSITIVE_INT32 = rf"(?:[1-9][0-9]{{0,8}}|{_INT32_TEN_DIGITS})"
_REF_PATTERN = rf"^={_POSITIVE_INT32}/(?:0|{_POSITIVE_INT32})(?![\s\S])"
_REF = re.compile(_REF_PATTERN)
_BUILD = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")
_SHORT_NAME = re.compile(r"[A-Za-z0-9_:]{1,64}")
_INT32_MIN, _INT32_MAX = -2147483648, 2147483647
_MAX_DOUBLE = 1.7976931348623157e308
_MAX_PARAMETERS, _MAX_REFERENCE_ITEMS, _INSPECTION_LIMIT = 128, 16, 4096
_MAX_TEXT, _MAX_ERROR = 1024, 4096
_REFERENCES = ("PTREF", "GMREF", "DTREF", "BLRFA", "NGMREF", "PSTREF", "GSTREF")

# Installed E3D 2.10 DbType PE values. Native canonicalizes code 2 and
# otherwise uses Enum.ToString, including alias names or a raw numeric code.
_DB_TYPES = {
    -1: ("Invalid",), 0: ("All", "Unknown"), 1: ("Design",), 2: ("Catalogue",),
    3: ("Communications",), 4: ("Property",), 5: ("Miscellaneous",),
    6: ("Spooler", "Isodraft"), 7: ("Draft",), 8: ("Dictionary",), 9: ("Engineering",),
    10: ("System",), 11: ("Global",), 12: ("Transaction",), 13: ("Marine",),
    14: ("Schematic",), 15: ("Manu",), 16: ("NameSequence", "NSEQ"), 17: ("CrossProduct",),
    18: ("Configuration",), 19: ("Task",), 20: ("ConceptualModel",),
}
_IDENTITY = {"refno", "type", "db_type", "db_type_code", "identity_key"}
_ATTR_METADATA = {"sdk_type", "is_array", "unit_kind"}
_ATTRIBUTE = _ATTR_METADATA | {
    "requested_name", "expected_shape", "status", "supported", "shape", "reference_null",
    "unit", "unit_status", "total_count", "returned_count", "truncated", "inspection_limited",
    "native_array_materialized", "values", "errors", "complete",
}
_BOUNDARY_FIELDS = {"project", "mdb", "current_element"}
_SCOPE = {
    "kind": "fixed_catalogue_scom_template_observations",
    "database_type": "Catalogue",
    "database_type_code": 2,
    "actual_type_observed_separately": True,
    "reference_hops": 1,
    "reference_targets_expanded": False,
    "word_parameters_observed": False,
    "hidden_attributes_observed": False,
    "all_attributes_covered": False,
    "all_references_covered": False,
    "whole_clone_fingerprint": False,
    "geometry_observed": False,
    "geometry_created": False,
    "model_written": False,
    "permissions_verified": None,
    "write_permission_verified": None,
    "engineering_validated": None,
    "whole_project_coverage": False,
}
_OUTPUT_LIMITS = {
    "maximum_parameters": _MAX_PARAMETERS,
    "maximum_reference_items": _MAX_REFERENCE_ITEMS,
    "native_array_inspection_limit": _INSPECTION_LIMIT,
    "maximum_text_length": _MAX_TEXT,
    "native_arrays_materialized_before_output_limits": True,
    "native_array_allocation_bounded": False,
}


def _refno(value: Any) -> bool:
    return type(value) is str and 4 <= len(value) <= 22 and bool(_REF.fullmatch(value))


def _parameters(args: dict[str, Any]) -> dict[str, Any]:
    if type(args) is not dict or set(args) != {"refno"}:
        raise ValueError("Only refno is accepted and it is required.")
    if not _refno(args["refno"]):
        raise ValueError("refno must be a canonical numeric =database/element Int32 reference.")
    return {"refno": args["refno"]}


def _need(condition: bool, field: str) -> None:
    if not condition:
        raise ValueError(f"Invalid PARAGON component template evidence: {field}")


def _fields(value: Any, names: set[str], field: str) -> dict[str, Any]:
    _need(type(value) is dict and value.keys() == names, field)
    return value


def _fixed(value: Any, expected: dict[str, Any], field: str) -> None:
    _fields(value, set(expected), field)
    _need(all(type(value[key]) is type(item) and value[key] == item for key, item in expected.items()), field)


def _text(value: Any, limit: int) -> bool:
    if type(value) is not str or len(value) > limit or not value.strip():
        return False
    try:
        return len(value.encode("utf-16-le")) <= 2 * limit
    except UnicodeEncodeError:
        return False


def _errors(value: Any, allowed: set[str], field: str) -> dict[str, str]:
    _need(type(value) is dict and value.keys() <= allowed
          and all(_text(item, _MAX_ERROR) for item in value.values()), field + " errors")
    return value


def _short_name(value: Any) -> bool:
    return type(value) is str and bool(_SHORT_NAME.fullmatch(value))


def _core_identity(row: dict[str, Any], field: str) -> None:
    refno, kind, db_code = row["refno"], row["type"], row["db_type_code"]
    _need(_refno(refno) and _short_name(kind) and row["identity_key"] == f"{refno}|{kind}",
          field + " typed REF identity")
    _need(type(db_code) is int and _INT32_MIN <= db_code <= _INT32_MAX
          and row["db_type"] in _DB_TYPES.get(db_code, (str(db_code),)), field + " database type")


def _type_observation(value: Any, getter: str, field: str) -> bool:
    row = _fields(value, {"source", "name", "short_name", "status", "errors"}, field)
    _need(row["source"] == getter, field + " getter")
    errors = _errors(row["errors"], {"type", "name", "short_name"}, field)
    if "type" in errors:
        _need(errors.keys() == {"type"} and row["name"] is None and row["short_name"] is None,
              field + " invalid native type")
    else:
        _need(row["name"] is None if "name" in errors else _text(row["name"], 256), field + " name")
        _need(row["short_name"] is None if "short_name" in errors else _short_name(row["short_name"]),
              field + " short name")
    _need(row["status"] == ("unknown" if errors else "observed"), field + " status")
    return not errors


def _identity(value: Any, field: str, *, requested: str | None = None) -> tuple[bool, bool]:
    row = _fields(value, _IDENTITY | {"name", "name_error", "request_binding", "base_type", "actual_type",
                                     "errors", "complete"}, field)
    _core_identity(row, field)
    errors = _errors(row["errors"], {"name", "base_type", "actual_type", "request_binding"}, field)
    if "name" in errors:
        _need(row["name"] is None and row["name_error"] == errors["name"], field + " NAME error")
    else:
        _need(_text(row["name"], _MAX_TEXT) and row["name_error"] is None, field + " NAME")

    binding = row["request_binding"]
    bound = False
    if requested is None:
        _need(binding is None and "request_binding" not in errors, field + " one-hop identity scope")
    else:
        _fixed(binding, {"source": "explicit_request_resolution", "requested_refno": requested,
                         "resolved_refno": requested, "observed_name": None, "verified": True},
               field + " first numeric request binding")
        bound = row["refno"] == requested and row["type"] == "SCOM" and row["db_type_code"] == 2
        _need(("request_binding" not in errors) is bound, field + " later numeric request binding")

    types_complete = True
    for key, getter in (("base_type", "DbElement.GetElementType"), ("actual_type", "DbElement.GetActualType")):
        observation = row[key]
        if observation is None:
            _need(key in errors, field + " missing " + key)
            types_complete = False
        else:
            types_complete &= _type_observation(observation, getter, field + " " + key)
            drift = key == "base_type" and observation["short_name"] not in (None, row["type"])
            _need((key in errors) is drift, field + " " + key + " agreement")

    complete = not errors and types_complete
    _need(row["complete"] is complete, field + " completeness")
    return complete, bound


def _leaf(value: Any, *, read: bool) -> bool:
    row = _fields(value, {"source", "status", "is_leaf", "first_member", "complete", "errors"}, "leaf")
    _need(row["source"] == "DbElement.Member(1)", "leaf source")
    errors = _errors(row["errors"], {"member"} if read else set(), "leaf")
    is_leaf, first = row["is_leaf"], row["first_member"]
    if not read or errors:
        # A non-null Member(1) can be seen before its typed identity fails.
        _need(row["status"] == "unknown" and first is None and row["complete"] is False
              and (is_leaf is None or (read and is_leaf is False)), "unknown leaf observation")
        return False
    _need(row["status"] == "observed" and type(is_leaf) is bool, "observed leaf status")
    if is_leaf:
        _need(first is None, "leaf has no first member")
        complete = True
    else:
        complete, _ = _identity(first, "first member")
    _need(row["complete"] is complete, "leaf observation completeness")
    return complete


def _unread_value(row: dict[str, Any], field: str) -> None:
    _need(row["status"] == "unknown" and row["total_count"] is None and row["returned_count"] == 0
          and row["truncated"] is None and row["inspection_limited"] is None
          and row["native_array_materialized"] is False and not row["values"]
          and row["complete"] is False, field + " unread value")


def _parameter_item(value: Any, index: int, field: str) -> bool:
    row = _fields(value, {"index", "value", "status", "error"}, field)
    _need(type(row["index"]) is int and row["index"] == index, field + " index")
    item = row["value"]
    if row["status"] == "observed":
        _need(type(item) in (int, float) and -_MAX_DOUBLE <= item <= _MAX_DOUBLE and row["error"] is None,
              field + " finite typed DOUBLE")
        return True
    _need(row["status"] == "unknown" and item is None and row["error"] == "non_finite_native_value",
          field + " unknown non-finite native value")
    return False


def _reference_item(value: Any, index: int, field: str) -> bool:
    row = _fields(value, {"index", "status", "identity", "reference_null", "error"}, field)
    _need(type(row["index"]) is int and row["index"] == index, field + " index")
    identity, is_null, error = row["identity"], row["reference_null"], row["error"]
    if row["status"] == "unset":
        _need(identity is None and is_null is True and error is None,
              field + " observed unset reference value")
        return True
    if identity is None:
        _need(row["status"] == "unknown" and (is_null is None or type(is_null) is bool)
              and _text(error, _MAX_ERROR),
              field + " unknown reference identity")
        return False
    complete, _ = _identity(identity, field + " identity")
    _need(is_null is False and row["status"] == ("observed" if complete else "partial")
          and (error is None if complete else _text(error, _MAX_ERROR)), field + " reference identity status")
    return complete


def _attribute(value: Any, name: str, *, read: bool) -> bool:
    row = _fields(value, _ATTRIBUTE, name)
    parameters = name == "PARAM"
    expected_shape = "double_array" if parameters else "element_or_element_array"
    _need(row["requested_name"] == name and row["expected_shape"] == expected_shape, name + " request binding")
    _need(row["unit"] is None and row["unit_status"] == "unknown", name + " unverified physical units")
    values, returned_count = row["values"], row["returned_count"]
    limit = _MAX_PARAMETERS if parameters else _MAX_REFERENCE_ITEMS
    _need(type(values) is list and type(returned_count) is int
          and 0 <= returned_count == len(values) <= limit, name + " output count")
    errors = _errors(row["errors"], _ATTR_METADATA | {
        "attribute", "support", "value", "inspection_limit", "output_limit",
        "non_finite" if parameters else "items",
    }, name)

    supported = row["supported"]
    if not read or supported is not True:
        if not read:
            _need(supported is None and not errors, name + " unattempted attribute")
        elif supported is None:
            _need(errors.keys() == {"attribute"}, name + " unresolved attribute")
        else:
            _fixed(errors, {"support": "unsupported_attribute_value_unknown"}, name + " unsupported attribute")
            _need(supported is False, name + " attribute support status")
        _need(all(row[key] is None for key in _ATTR_METADATA | {"shape", "reference_null"}),
              name + " unobserved attribute metadata")
        _unread_value(row, name)
        return False

    expected_errors = set(errors) & _ATTR_METADATA
    for key in _ATTR_METADATA:
        observed = type(row[key]) is bool if key == "is_array" else _text(row[key], 64)
        _need(row[key] is None if key in errors else observed, name + " " + key)
    sdk_type, is_array = row["sdk_type"], row["is_array"]
    shape = None
    if parameters:
        if sdk_type == "DOUBLE" and is_array is True:
            shape = "double_array"
    elif sdk_type == "ELEMENT" and type(is_array) is bool:
        shape = "element_array" if is_array else "element"
    _need(row["shape"] == shape, name + " typed getter shape")
    total = row["total_count"]
    if total is None:
        _need(errors.keys() == expected_errors | {"value"}, name + " typed value error")
        _need(row["reference_null"] is None or (shape == "element_array" and row["reference_null"] is True),
              name + " null native array")
        _unread_value(row, name)
        return False

    _need(shape is not None and type(total) is int and 0 <= total <= _INT32_MAX,
          name + " typed value count")
    _need(shape != "element" or total == 1, name + " scalar reference count")
    _need(row["native_array_materialized"] is (is_array is True), name + " SDK array materialization")
    truncated, inspection_limited = total > limit, total > _INSPECTION_LIMIT
    _need(row["truncated"] is truncated and row["inspection_limited"] is inspection_limited,
          name + " output and inspection bounds")
    if inspection_limited:
        _need(errors.keys() == expected_errors | {"inspection_limit"} and not values
              and row["status"] == "unknown" and row["reference_null"] is None and row["complete"] is False,
              name + " materialized array not inspected")
        return False

    _need(returned_count == min(total, limit), name + " bounded prefix length")
    item_validator = _parameter_item if parameters else _reference_item
    items_complete = True
    for index, item in enumerate(values):
        items_complete &= item_validator(item, index, f"{name}[{index}]")
    reference_null = values[0]["reference_null"] if shape == "element" else None
    _need(row["reference_null"] is reference_null, name + " reference null binding")
    if truncated:
        expected_errors.add("output_limit")
    if not items_complete:
        expected_errors.add("non_finite" if parameters else "items")
    _need(errors.keys() == expected_errors, name + " typed value errors")
    complete = not expected_errors
    _need(row["complete"] is complete and row["status"] == ("observed" if complete else "partial"),
          name + " typed value completeness")
    return complete


def _context(value: Any, session: str, build: str, *, target_observed: bool) -> bool | None:
    context = _fields(value, {"before", "after", "same_project_instance", "same_mdb_instance",
                              "boundary_agreement"}, "context")
    for side in ("before", "after"):
        field = "context " + side
        boundary = _fields(context[side], _BOUNDARY_FIELDS | {"session_id", "bridge_build", "errors"}, field)
        _need(boundary["session_id"] == session and boundary["bridge_build"] == build, field + " session/build")
        errors = _errors(boundary["errors"], _BOUNDARY_FIELDS, field)
        for key in _BOUNDARY_FIELDS:
            observed = boundary[key]
            if key in errors:
                _need(observed is None, field + " unknown " + key)
            elif key == "current_element":
                _core_identity(_fields(observed, _IDENTITY, field + " typed CE"), field + " typed CE")
            else:
                _need(_text(observed, 256), field + " " + key)

    before, after = context["before"], context["after"]
    instance_flags = []
    for key in ("project", "mdb"):
        same = context[f"same_{key}_instance"]
        _need(type(same) is bool or (same is None and (before[key] is None or after[key] is None)),
              "context " + key + " instance observation")
        # Resolving a target requires both before instances. If an after
        # name was observed, a missing instance must have been at before.
        _need(not target_observed or same is not None or after[key] is None,
              "context " + key + " instance required to resolve target")
        instance_flags.append(same)
    drift = any(flag is False for flag in instance_flags) or any(
        before[key] is not None and after[key] is not None and before[key] != after[key]
        for key in _BOUNDARY_FIELDS
    )
    agreement = False if drift else (
        None if any(flag is None for flag in instance_flags) or before["errors"] or after["errors"] else True
    )
    _need(context["boundary_agreement"] is agreement, "context boundary agreement")
    return agreement


def _validate(reply: Any, payload: dict[str, Any]) -> None:
    envelope = _fields(reply, {"ok", "version", "session_id", "bridge_build", "result"}, "native envelope")
    _need(envelope["ok"] is True and envelope["version"] == VERSION, "native E3D21 envelope")
    session, build = envelope["session_id"], envelope["bridge_build"]
    _need(_text(session, 256) and type(build) is str and bool(_BUILD.fullmatch(build)), "native session/build")
    result = _fields(envelope["result"], {
        "operation", "available", "success", "complete", "read_only", "single_dispatch", "atomic_snapshot",
        "session_id", "bridge_build", "requested_refno", "expected_base_type", "target", "leaf",
        "parameters", "references", "read_errors", "context", "output_limits", "scope",
    }, "result")
    _need(result["operation"] == OPERATION and all(result[key] is True for key in
          ("available", "read_only", "single_dispatch")) and result["atomic_snapshot"] is False, "read operation")
    _need(result["session_id"] == session and result["bridge_build"] == build, "result session/build")
    requested = payload["refno"]
    _need(result["requested_refno"] == requested and result["expected_base_type"] == "SCOM",
          "request binding and fixed SCOM base type")
    _fixed(result["scope"], _SCOPE, "fixed observation scope")
    _fixed(result["output_limits"], _OUTPUT_LIMITS, "fixed output limits")
    target, errors = result["target"], result["read_errors"]
    agreement = _context(result["context"], session, build, target_observed=target is not None)
    _need(type(errors) is list and len(errors) == (1 if target is None else 0), "target resolution read errors")
    target_complete = bound = False
    if target is None:
        error = _fields(errors[0], {"stage", "message"}, "unresolved target error")
        _need(error["stage"] == "target" and _text(error["message"], _MAX_ERROR), "unresolved target evidence")
    else:
        target_complete, bound = _identity(target, "target", requested=requested)
    leaf_complete = _leaf(result["leaf"], read=bound)
    parameters_complete = _attribute(result["parameters"], "PARAM", read=bound)
    references = _fields(result["references"], set(_REFERENCES), "fixed reference attributes")
    references_complete = True
    for name in _REFERENCES:
        references_complete &= _attribute(references[name], name, read=bound)
    complete = target_complete and leaf_complete and parameters_complete and references_complete and agreement is True
    _need(result["success"] is complete and result["complete"] is complete, "overall observation completeness")


def _unavailable(version: str, payload: dict[str, Any], code: str, message: str) -> dict[str, Any]:
    return {
        "ok": True,
        "version": version,
        "result": {
            "operation": OPERATION,
            "requested_refno": payload["refno"],
            "available": False,
            "status": "unknown",
            "success": False,
            "complete": False,
            "read_only": True,
            "single_dispatch": None,
            "atomic_snapshot": False,
            "read_errors": [{"code": code, "message": message}],
        },
    }


def paragon_component_template(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    """Return the validated native receipt intact, including partial evidence."""
    payload = _parameters(args)
    version = bridge._selected_version(None)
    if version != VERSION or bridge.settings.mode != "bridge":
        return _unavailable(version, payload, "native_unavailable",
                            "Component template observations require E3D21 bridge mode.")
    try:
        reply = bridge._request("POST", PATH, payload, version=VERSION)
    except BridgeError as error:
        if _contains_native_fault(error.diagnostics):
            raise
        if error.transport_error:
            raise
        if error.response_invalid:
            return _unavailable(version, payload, "invalid_native_response", str(error))
        if error.http_status == 404:
            return _unavailable(version, payload, "native_feature_unavailable",
                                "The running Addin does not expose the component template reader.")
        raise
    if _contains_native_fault(reply):
        raise BridgeError("A native database fault stopped component template inspection. Do not retry.",
                          diagnostics=reply)
    try:
        _validate(reply, payload)
    except ValueError as error:
        return _unavailable(version, payload, "invalid_native_response", str(error))
    return reply


def register_paragon_component_template(server: McpServer, bridge: BridgeClient) -> None:
    server.add_tool(
        TOOL,
        "Read bounded native evidence for one existing Catalogue SCOM in E3D21. Observe its typed identity, "
        "first member, up to 128 numeric PARAM items, and up to 16 one-hop identity items per fixed reference "
        "attribute. The SDK materializes native arrays before the output bounds and the 4096-item inspection "
        "cutoff. Project, MDB and typed current element are compared at both boundaries of the read. "
        "Completeness covers this scope. Word parameters, reference contents, engineering validation and a "
        "complete clone fingerprint remain outside it.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "minLength": 4, "maxLength": 22, "pattern": _REF_PATTERN,
                      "description": "Canonical numeric =database/element for one existing Catalogue SCOM."},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: paragon_component_template(bridge, args), read_only=True,
    )
