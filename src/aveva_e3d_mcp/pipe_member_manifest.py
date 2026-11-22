"""Bounded E3D21 observations of the members of an explicit PIPE or BRAN."""
from __future__ import annotations

import math
import re
from typing import Any

from .bridge import BridgeClient, BridgeError, element_access_refnos
from .protocol import McpServer


TOOL = "e3d_pipe_member_manifest"
PATH = "/api/v1/piping/member-manifest"
VERSION = "E3D21"
DEFAULT_ATTRIBUTES = ("SPREF", "POS", "ORI", "ABOR", "LBOR")
DEFAULT_DEPTH, MAX_DEPTH = 8, 16
DEFAULT_LIMIT, MAX_LIMIT = 64, 100
MAX_ATTRIBUTES, MAX_CELLS = 10, 500
MAX_REF_LENGTH = 256
_ATTRIBUTE = re.compile(r"[A-Za-z0-9_:]{1,64}")
_TYPE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_DBREF = re.compile(r"=[1-9][0-9]{0,9}/(?:0|[1-9][0-9]{0,9})")


def _parameters(args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict) or set(args) - {"refno", "max_depth", "limit", "attributes"}:
        raise ValueError("Only refno, max_depth, limit and attributes are accepted")
    refno = element_access_refnos([args.get("refno")])[0]
    if len(refno) > MAX_REF_LENGTH or any(c in refno for c in "*?"):
        raise ValueError("refno must be a literal PIPE or BRAN reference of at most 256 characters")
    if refno.startswith("="):
        refno = "=" + "/".join(str(int(part)) for part in refno[1:].split("/"))
    depth, limit = args.get("max_depth", DEFAULT_DEPTH), args.get("limit", DEFAULT_LIMIT)
    if type(depth) is not int or not 1 <= depth <= MAX_DEPTH:
        raise ValueError(f"max_depth must be an integer between 1 and {MAX_DEPTH}")
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be an integer between 1 and {MAX_LIMIT}")
    fields = args.get("attributes", list(DEFAULT_ATTRIBUTES))
    if not isinstance(fields, list) or len(fields) > MAX_ATTRIBUTES or len(fields) * limit > MAX_CELLS:
        raise ValueError(f"At most {MAX_ATTRIBUTES} attributes and {MAX_CELLS} requested row/attribute cells are allowed")
    names = []
    for field in fields:
        if not isinstance(field, str) or not _ATTRIBUTE.fullmatch(field.strip()):
            raise ValueError("attributes must be explicit SDK names of 1 to 64 ASCII letters, digits, underscores or colons")
        names.append(field.strip().upper())
    if len(set(names)) != len(names):
        raise ValueError("attributes must be unique after normalization")
    return {"refno": refno, "max_depth": depth, "limit": limit, "attributes": names}


def _text(value: Any, limit: int = 4096, *, nonempty: bool = False) -> bool:
    return isinstance(value, str) and len(value) <= limit and (not nonempty or bool(value.strip()))


def _dbref(value: Any) -> bool:
    return (isinstance(value, str) and bool(_DBREF.fullmatch(value))
            and all(int(part) <= 2147483647 for part in value[1:].split("/")))


def _kind(value: Any) -> bool:
    return isinstance(value, str) and bool(_TYPE.fullmatch(value))


def _scalar(value: Any) -> bool:
    return _text(value) or type(value) in (bool, int) or type(value) is float and math.isfinite(value)


def _need(valid: bool, field: str) -> None:
    if not valid:
        raise ValueError(f"Invalid pipe member manifest evidence: {field}")


def _identity(row: Any, row_id: int) -> str:
    _need(isinstance(row, dict) and type(row.get("id")) is int and row["id"] == row_id, "row ID")
    refno, kind = row.get("refno"), row.get("type")
    _need(_dbref(refno) and _kind(kind) and row.get("identity_key") == f"{refno}|{kind}", "typed DBREF")
    return row["identity_key"]


def _row_evidence(row: dict, attributes: set[str]) -> tuple[bool, bool]:
    for key, check in (("values", _text), ("errors", lambda v: _text(v, nonempty=True)),
                       ("typed_values", _scalar), ("typed_read_errors", lambda v: _text(v, nonempty=True))):
        items = row.get(key)
        _need(isinstance(items, dict) and items.keys() <= attributes and all(check(v) for v in items.values()), key)
    values, errors = row["values"].keys(), row["errors"].keys()
    typed, typed_errors = row["typed_values"].keys(), row["typed_read_errors"].keys()
    unavailable = row.get("unavailable_attributes")
    _need(isinstance(unavailable, list) and len(unavailable) <= len(attributes)
          and all(isinstance(k, str) and k in attributes for k in unavailable), "unavailable attributes")
    absent = set(unavailable)
    _need(len(absent) == len(unavailable) and values | errors | absent == attributes
          and not (values & errors or (values | errors) & absent)
          and (typed | typed_errors) <= values and not typed & typed_errors, "attribute coverage")
    name, name_error = row.get("name"), row.get("name_error")
    _need("name" in row and "name_error" in row and (
        _text(name, nonempty=True) and name_error is None or name is None and _text(name_error, nonempty=True)
    ), "name evidence")
    complete = name_error is None and not (errors or typed_errors)
    _need(row.get("complete") is complete, "row completeness")
    return complete, not (errors or typed_errors or absent)


def _validate(reply: Any, payload: dict[str, Any]) -> None:
    _need(isinstance(reply, dict) and reply.get("ok") is True and reply.get("version") == VERSION
          and all(_text(reply.get(k), 256, nonempty=True) for k in ("session_id", "bridge_build")), "envelope identity")
    result = reply.get("result")
    _need(isinstance(result, dict) and result.get("operation") == "pipe_member_manifest"
          and all(result.get(k) is True for k in ("read_only", "available", "single_dispatch"))
          and result.get("atomic_snapshot") is False and result.get("order") == "database_preorder", "scope")
    _need(result.get("requested_refno") == payload["refno"] and result.get("attributes") == payload["attributes"]
          and all(type(result.get(k)) is int and result[k] == payload[k] for k in ("max_depth", "limit")), "request echo")

    root = result.get("root")
    root_key = _identity(root, 0)
    _need(root["type"] in ("PIPE", "BRAN") and _text(root.get("name"), nonempty=True)
          and type(root.get("depth")) is int and root["depth"] == 0, "PIPE or BRAN root")
    target = payload["refno"]
    _need(root["refno"] == target if target.startswith("=")
          else root["name"].removeprefix("/").upper() == target[1:].upper(), "resolved target")

    rows = result.get("rows")
    _need(isinstance(rows, list) and type(result.get("returned_count")) is int
          and len(rows) == result["returned_count"] <= payload["limit"], "returned row count")
    path = [0]
    siblings: dict[int, int] = {}
    seen = {root_key}
    fields = set(payload["attributes"])
    rows_complete = all_values_available = True
    for row_id, row in enumerate(rows, 1):
        key = _identity(row, row_id)
        _need(key not in seen, "duplicate typed DBREF")
        seen.add(key)
        depth = row.get("depth")
        _need(type(depth) is int and 1 <= depth <= min(len(path), payload["max_depth"]), "member depth")
        parent = path[depth - 1]
        ordinal = siblings.get(parent, 0) + 1
        _need(type(row.get("parent_index")) is int and row["parent_index"] == parent
              and type(row.get("sibling_index")) is int and row["sibling_index"] == ordinal, "preorder parent and sibling")
        siblings[parent] = ordinal
        path[depth:] = [row_id]
        row_complete, values_available = _row_evidence(row, fields)
        rows_complete &= row_complete
        all_values_available &= values_available
    _need(result.get("all_returned_values_available") is all_values_available, "attribute availability")

    reasons = result.get("truncation_reasons")
    _need(reasons in ([], ["depth"], ["limit"], ["depth", "limit"])
          and result.get("truncated") is bool(reasons), "truncation")
    if "limit" in reasons:
        _need(len(rows) == payload["limit"], "row limit truncation")
    if "depth" in reasons:
        _need(bool(rows) and rows[-1]["depth"] == payload["max_depth"], "depth truncation")
    errors = result.get("read_errors")
    _need(isinstance(errors, list) and len(errors) <= 2
          and all(isinstance(e, dict) and _text(e.get("message"), nonempty=True) for e in errors), "read errors")
    stages = [e.get("stage") for e in errors]
    _need(stages in ([], ["member"], ["context_after"], ["member", "context_after"]), "error stages")
    if "member" in stages:
        parent, ordinal = errors[0].get("parent_index"), errors[0].get("sibling_index")
        _need(not reasons and type(parent) is int and parent in path and type(ordinal) is int
              and ordinal == siblings.get(parent, 0) + 1, "member read error location")
    traversal_complete = not reasons and "member" not in stages
    _need(result.get("traversal_complete") is traversal_complete, "traversal completeness")

    context = result.get("context")
    _need(isinstance(context, dict) and set(context) == {"before", "after", "same_project_instance",
          "same_mdb_instance", "boundary_agreement"}, "context")
    before, after = context.get("before"), context["after"]
    for sample in (before,) if after is None else (before, after):
        _need(isinstance(sample, dict) and set(sample) == {"project", "mdb", "current_element", "current_element_type"}
              and all(_text(sample[k], 256, nonempty=True) for k in ("project", "mdb"))
              and _dbref(sample["current_element"]) and _kind(sample["current_element_type"]), "context boundary")
    instances = (context["same_project_instance"], context["same_mdb_instance"])
    _need(all(value is None if after is None else type(value) is bool for value in instances), "context instances")
    agreement = None if after is None else all(instances) and before == after
    _need(context["boundary_agreement"] is agreement and ("context_after" in stages) == (agreement is not True),
          "context boundary agreement")
    complete = traversal_complete and rows_complete and agreement is True
    _need(result.get("success") is complete and result.get("complete") is complete, "snapshot completeness")


def pipe_member_manifest(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    payload = _parameters(args)
    version = bridge._selected_version(None)

    def unavailable(code: str, message: str) -> dict[str, Any]:
        return {"ok": True, "version": version, "result": {
            "operation": "pipe_member_manifest", "requested_refno": payload["refno"],
            "success": False, "available": False, "complete": False, "traversal_complete": False,
            "all_returned_values_available": None, "single_dispatch": None, "atomic_snapshot": False,
            "read_only": True, "root": None, "rows": [], "returned_count": 0,
            "truncated": False, "truncation_reasons": [],
            "context": {"before": None, "after": None, "same_project_instance": None,
                        "same_mdb_instance": None, "boundary_agreement": None},
            "read_errors": [{"code": code, "message": message}],
        }}

    if version != VERSION or bridge.settings.mode != "bridge":
        return unavailable("native_unavailable", "Pipe member manifests require E3D21 bridge mode.")
    try:
        reply = bridge._request("POST", PATH, payload, version=VERSION)
    except BridgeError as exc:
        if exc.http_status == 404:
            return unavailable("native_feature_unavailable", "The running E3D21 Addin does not expose pipe_member_manifest.")
        raise
    try:
        _validate(reply, payload)
    except ValueError as exc:
        return unavailable("invalid_native_response", str(exc))
    return reply


def register_pipe_member_manifest(server: McpServer, bridge: BridgeClient) -> None:
    server.add_tool(
        TOOL,
        "Read a bounded database preorder prefix beneath one explicit E3D21 Design PIPE or BRAN in one native "
        "UI callback. Root is ID 0; each member has a typed DBREF, native name, depth, parent_index and sibling_index. "
        "An implicit TUBI can share its DBREF with a distinct component. Keeps SDK formatted attributes and scalar "
        "values when present; inspect unavailable_attributes, row errors, traversal_complete, truncation_reasons "
        "and context.boundary_agreement. complete describes this bounded observation, not an atomic snapshot, "
        "connectivity or engineering validation. No PML, model or view changes. Requires a supporting E3D21 Addin.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "minLength": 1, "maxLength": MAX_REF_LENGTH,
                      "description": "Explicit PIPE or BRAN /NAME or =database/element."},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": MAX_DEPTH, "default": DEFAULT_DEPTH},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": DEFAULT_LIMIT,
                      "description": "Maximum member rows, excluding the root. limit times attribute count cannot exceed 500."},
            "attributes": {"type": "array", "maxItems": MAX_ATTRIBUTES, "uniqueItems": True,
                           "items": {"type": "string", "minLength": 1, "maxLength": 64},
                           "default": list(DEFAULT_ATTRIBUTES),
                           "description": "SDK attribute names for every member. An empty array reads identities only."},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: pipe_member_manifest(bridge, args), read_only=True,
    )
