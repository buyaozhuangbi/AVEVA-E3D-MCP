"""Expose the native bounded equipment manifest without composing multiple reads."""
from __future__ import annotations

import math
import re
from typing import Any

from .bridge import BridgeClient, BridgeError, element_access_refnos
from .protocol import McpServer


TOOL = "e3d_equipment_manifest"
PATH = "/api/v1/equipment/manifest"
VERSION = "E3D21"
_ATTRIBUTE = re.compile(r"[A-Za-z0-9_:]{1,64}")


def _parameters(args: dict[str, Any]) -> dict[str, Any]:
    if set(args) - {"refno", "max_depth", "limit", "attributes"}:
        raise ValueError("Only refno, max_depth, limit and attributes are accepted")
    refno = element_access_refnos([args.get("refno")])[0]
    if len(refno) > 256 or "*" in refno:
        raise ValueError("refno must be an explicit name or DBREF of at most 256 characters")
    if refno.startswith("="):
        refno = "=" + "/".join(str(int(part)) for part in refno[1:].split("/"))
    depth = args.get("max_depth", 8)
    limit = args.get("limit", 64)
    if type(depth) is not int or not 1 <= depth <= 32:
        raise ValueError("max_depth must be an integer between 1 and 32")
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit must be an integer between 1 and 200")
    fields = args.get("attributes", [])
    if not isinstance(fields, list) or len(fields) > 10 or len(fields) * limit > 500:
        raise ValueError("At most 10 attributes and 500 requested row/attribute cells are allowed")
    attributes = []
    for field in fields:
        if not isinstance(field, str) or not _ATTRIBUTE.fullmatch(field.strip()):
            raise ValueError("attributes must be explicit SDK names of 1 to 64 ASCII letters, digits, underscores or colons")
        attributes.append(field.strip().upper())
    if len(set(attributes)) != len(attributes):
        raise ValueError("attributes must be unique after normalization")
    return {"refno": refno, "max_depth": depth, "limit": limit, "attributes": attributes}


def _dbref(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"=[1-9][0-9]{0,9}/(?:0|[1-9][0-9]{0,9})", value):
        return False
    return all(int(part) <= 2147483647 for part in value[1:].split("/"))


def _text(value: Any, limit: int = 4096) -> bool:
    return isinstance(value, str) and len(value) <= limit


def _validate(reply: Any, payload: dict[str, Any]) -> None:
    def need(valid: bool, field: str) -> None:
        if not valid:
            raise ValueError(f"Invalid equipment manifest evidence: {field}")

    need(isinstance(reply, dict) and reply.get("ok") is True and reply.get("version") == VERSION, "envelope")
    result = reply.get("result")
    need(isinstance(result, dict), "result")
    need(all(type(result.get(k)) is bool for k in ("success", "complete", "truncated"))
         and all(k in result and (result[k] is None or type(result[k]) is bool)
                 for k in ("identity_unchanged", "ce_unchanged"))
         and result.get("single_dispatch") is True and result.get("atomic_snapshot") is False, "flags")
    need(result.get("order") == "preorder" and all(type(result.get(k)) is int and result[k] == payload[k]
         for k in ("limit", "max_depth")) and result.get("attributes") == payload["attributes"], "request and order")
    need(result.get("requested_refno") == payload["refno"], "request reference")
    session_id = result.get("session_id")
    need(_text(session_id, 256) and bool(session_id.strip()) and reply.get("session_id") == session_id, "session identity")
    reasons, errors = result.get("truncation_reasons"), result.get("read_errors")
    need(isinstance(reasons, list) and all(r in ("depth", "limit") for r in reasons)
         and bool(reasons) == result["truncated"], "truncation")
    need(isinstance(errors, list) and len(errors) <= payload["limit"] * 2 + 8
         and all(isinstance(item, dict) and _text(item.get("stage"), 64) and bool(item["stage"])
                 and _text(item.get("error")) and (item.get("refno") is None or _dbref(item["refno"]))
                 for item in errors), "read_errors")
    rows = result.get("rows")
    need(isinstance(rows, list) and type(result.get("returned_count")) is int
         and 1 <= len(rows) == result["returned_count"] <= payload["limit"], "returned_count")
    root = result.get("root_refno")
    need(_dbref(root), "root_refno")
    expected_ref = payload["refno"]
    if expected_ref.startswith("="):
        need(root == expected_ref, "requested DBREF")

    path: list[int] = []
    siblings: dict[int, int] = {}
    seen: set[str] = set()
    attributes = set(payload["attributes"])
    for index, row in enumerate(rows):
        need(isinstance(row, dict) and type(row.get("id")) is int and row["id"] == index, "row ID")
        depth, refno, kind, name = (row.get(k) for k in ("depth", "refno", "type", "name"))
        need(type(depth) is int and 0 <= depth <= payload["max_depth"] and _dbref(refno), "row depth/DBREF")
        need(type(row.get("success")) is bool, "row success")
        if index == 0:
            need(depth == 0 and refno == root and kind == "EQUI" and _text(name) and bool(name)
                 and row.get("parent_index") is None and row.get("sibling_index") is None, "EQUI root")
            if not expected_ref.startswith("="):
                need(name.removeprefix("/").upper() == expected_ref[1:].upper(), "requested EQUI name")
        else:
            need(0 < depth <= len(path) and type(row.get("parent_index")) is int, "row ancestry")
            parent = path[depth - 1]
            ordinal = siblings.get(parent, 0) + 1
            need(row["parent_index"] == parent and type(row.get("sibling_index")) is int
                 and row["sibling_index"] == ordinal, "preorder member indexes")
            siblings[parent] = ordinal
        need((_text(kind, 64) and bool(kind) or kind is None and not row["success"])
             and (_text(name) or name is None and not row["success"]), "row identity")
        identity_errors = row.get("identity_errors")
        need(isinstance(identity_errors, dict)
             and identity_errors.keys() == {k for k in ("type", "name") if row.get(k) is None}
             and all(_text(message) for message in identity_errors.values()), "row identity errors")
        need(refno not in seen, "duplicate row DBREF")
        seen.add(refno)
        path[depth:] = [index]
        for key in ("values", "typed_values", "errors", "typed_read_errors"):
            values = row.get(key)
            need(isinstance(values, dict) and values.keys() <= attributes, f"row {key}")
            if key == "typed_values":
                need(all(_text(v) or type(v) in (int, bool) or type(v) is float and math.isfinite(v)
                         for v in values.values()), f"row {key}")
            else:
                need(all(_text(v) for v in values.values()), f"row {key}")
        need(row["values"].keys() | row["errors"].keys() == attributes
             and not row["values"].keys() & row["errors"].keys()
             and not row["typed_values"].keys() & row["typed_read_errors"].keys(), "row attribute coverage")
        need(row["success"] is not bool(identity_errors or row["errors"] or row["typed_read_errors"]), "row success agreement")
    before_ce, after_ce = result.get("ce_before"), result.get("ce_after")
    need((before_ce == "" or _dbref(before_ce))
         and (after_ce is None or after_ce == "" or _dbref(after_ce)), "CE evidence")
    need(result["ce_unchanged"] is (None if after_ce is None else before_ce == after_ce), "CE agreement")
    need(all(_text(result.get(k), 256) and result[k].strip() for k in ("project", "mdb")), "context identity")
    project_after, mdb_after = result.get("project_after"), result.get("mdb_after")
    need(all(value is None or _text(value, 256) and value.strip() for value in (project_after, mdb_after)), "context readback")
    identity_agrees = (None if None in (project_after, mdb_after) else
                       result["project"] == project_after and result["mdb"] == mdb_after)
    need(result["identity_unchanged"] is identity_agrees, "context agreement")
    need(result["success"] == result["complete"], "success/complete disagreement")
    if result["complete"]:
        need(not result["truncated"] and not errors and result["ce_unchanged"] is True
             and result["identity_unchanged"] is True and all(row["success"] for row in rows),
             "complete evidence")


def equipment_manifest(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    payload = _parameters(args)
    version = bridge._selected_version(None)

    def unavailable(code: str, message: str) -> dict[str, Any]:
        return {"ok": True, "version": version, "result": {
            "success": False, "available": False, "complete": False, "truncated": False,
            "root_refno": None, "rows": [], "returned_count": 0, "atomic_snapshot": False,
            "single_dispatch": None, "read_errors": [{"code": code, "message": message}],
        }}

    if version != VERSION:
        return unavailable("unsupported_version", "Equipment manifests require E3D21.")
    if bridge.settings.mode != "bridge":
        return unavailable("native_unavailable", "Equipment manifests require the native E3D21 bridge.")
    try:
        reply = bridge._request("POST", PATH, payload, version=VERSION)
    except BridgeError as exc:
        if exc.http_status == 404:
            return unavailable("native_feature_unavailable", "The running E3D21 Addin does not expose equipment_manifest.")
        raise
    try:
        _validate(reply, payload)
    except ValueError as exc:
        return unavailable("invalid_native_response", str(exc))
    return reply


def register_equipment_manifest(server: McpServer, bridge: BridgeClient) -> None:
    server.add_tool(
        TOOL,
        "Read one explicit EQUI root and a bounded preorder prefix of its Design members with selected "
        "formatted and typed SDK attributes in one E3D21 UI callback. Row 0 is the root; each child "
        "has a zero-based parent_index and one-based sibling_index. A depth, count or read limit makes "
        "complete=false; inspect rows, read_errors and truncation_reasons. Single dispatch does not mean "
        "an atomic database snapshot. No PML, model writes or view changes. Requires an Addin exposing equipment_manifest.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "minLength": 1, "maxLength": 256, "description": "Explicit EQUI /NAME or =database/element."},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 32, "default": 8},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 64,
                      "description": "Maximum rows including the root; limit multiplied by attribute count cannot exceed 500."},
            "attributes": {"type": "array", "maxItems": 10, "uniqueItems": True,
                           "items": {"type": "string", "minLength": 1, "maxLength": 64}, "default": []},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: equipment_manifest(bridge, args), read_only=True,
    )
