"""Read one explicit Catalogue subtree without selecting PARAGON or changing CE.

SDK evidence (installed E3D 2.1): DbType.Catalogue == DbType.Catalog == 2;
MDB.FindElement(DbType, string) filters named resolution by the real DB type.
PMLLIB/paragon/forms/catcreate.pmlfrm describes WORL -> CATA -> SECT -> CATE.
LIBY is a native noun, not evidence that a database belongs to Catalogue.
Admin CREATE DB and PARAGON NEW catalogue members are separate operations;
this observation certifies neither operation, permissions nor current module.
"""
from __future__ import annotations

import math
import re
from typing import Any

from .bridge import BridgeClient, BridgeError, element_access_refnos
from .protocol import McpServer


TOOL = "e3d_paragon_catalogue_manifest"
PATH = "/api/v1/paragon/catalogue-manifest"
VERSION = "E3D21"
DEFAULT_DEPTH, MAX_DEPTH = 8, 16
DEFAULT_LIMIT, MAX_LIMIT = 64, 100
MAX_ATTRIBUTES, MAX_CELLS = 10, 500
MAX_REF_LENGTH = 256
_ATTRIBUTE = re.compile(r"[A-Za-z0-9_:]{1,64}")
_TYPE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_DBTYPE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_DBREF = re.compile(r"=[1-9][0-9]{0,9}/(?:0|[1-9][0-9]{0,9})")
_IDENTITY_FIELDS = {"refno", "type", "db_type", "db_type_code", "identity_key"}


def _parameters(args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict) or set(args) - {"refno", "max_depth", "limit", "attributes"}:
        raise ValueError("Only refno, max_depth, limit and attributes are accepted")
    raw = args.get("refno")
    if not isinstance(raw, str) or len(raw) > MAX_REF_LENGTH:
        raise ValueError("refno must explicitly identify /NAME or =database/element, up to 256 characters")
    refno = element_access_refnos([raw])[0]
    if any(c in refno for c in "*?"):
        raise ValueError("refno must be a literal catalogue reference without wildcards")
    if refno.startswith("="):
        refno = "=" + "/".join(str(int(part)) for part in refno[1:].split("/"))
    depth, limit = args.get("max_depth", DEFAULT_DEPTH), args.get("limit", DEFAULT_LIMIT)
    if type(depth) is not int or not 1 <= depth <= MAX_DEPTH:
        raise ValueError(f"max_depth must be an integer between 1 and {MAX_DEPTH}")
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be an integer between 1 and {MAX_LIMIT}, including the root")
    attributes = args.get("attributes", [])
    if not isinstance(attributes, list) or len(attributes) > MAX_ATTRIBUTES or len(attributes) * limit > MAX_CELLS:
        raise ValueError(f"At most {MAX_ATTRIBUTES} attributes and {MAX_CELLS} requested row/attribute cells are allowed, including the root")
    names = []
    for name in attributes:
        if not isinstance(name, str) or not _ATTRIBUTE.fullmatch(name.strip()):
            raise ValueError("attributes require explicit SDK names of 1 to 64 ASCII letters, digits, underscores or colons")
        names.append(name.strip().upper())
    if len(names) != len(set(names)):
        raise ValueError("attributes must be unique after normalization")
    return {"refno": refno, "max_depth": depth, "limit": limit, "attributes": names}


def _text(value: Any, limit: int = 4096, *, nonempty: bool = False) -> bool:
    return isinstance(value, str) and len(value) <= limit and (not nonempty or bool(value.strip()))


def _need(condition: bool, field: str) -> None:
    if not condition:
        raise ValueError(f"Invalid PARAGON catalogue manifest evidence: {field}")


def _dbref(value: Any) -> bool:
    return (isinstance(value, str) and bool(_DBREF.fullmatch(value))
            and all(int(part) <= 2147483647 for part in value[1:].split("/")))


def _identity(value: Any, *, catalogue: bool = False) -> str:
    _need(isinstance(value, dict) and _IDENTITY_FIELDS <= value.keys(), "native identity fields")
    refno, kind, db_type, code = (value.get(k) for k in ("refno", "type", "db_type", "db_type_code"))
    _need(_dbref(refno) and isinstance(kind, str) and bool(_TYPE.fullmatch(kind))
          and value.get("identity_key") == f"{refno}|{kind}", "typed native DBREF")
    _need(isinstance(db_type, str) and bool(_DBTYPE.fullmatch(db_type))
          and type(code) is int and -1 <= code <= 2147483647, "native database type")
    _need((code == 2) == (db_type == "Catalogue") and (code != 1 or db_type == "Design"), "canonical native database type")
    _need(not catalogue or db_type == "Catalogue" and code == 2, "Catalogue database membership")
    return value["identity_key"]


def _row_values(row: dict, attributes: set[str]) -> bool:
    def scalar(value: Any) -> bool:
        return _text(value) or type(value) in (bool, int) or type(value) is float and math.isfinite(value)

    for name, check in (("values", _text), ("errors", lambda v: _text(v, nonempty=True)),
                        ("typed_values", scalar), ("typed_read_errors", lambda v: _text(v, nonempty=True))):
        items = row.get(name)
        _need(isinstance(items, dict) and items.keys() <= attributes and all(check(v) for v in items.values()), name)
    unavailable = row.get("unavailable_attributes")
    _need(isinstance(unavailable, list) and len(unavailable) <= len(attributes)
          and all(isinstance(k, str) and k in attributes for k in unavailable), "unavailable attributes")
    absent = set(unavailable)
    values, errors, typed, typed_errors = (row[k].keys() for k in ("values", "errors", "typed_values", "typed_read_errors"))
    _need(len(absent) == len(unavailable) and values | errors | absent == attributes
          and not (values & errors or (values | errors) & absent)
          and (typed | typed_errors) <= values and not typed & typed_errors, "attribute coverage")
    return not (errors or typed_errors or absent)


def _boundary(sample: Any, session: str, build: str) -> None:
    _need(isinstance(sample, dict) and set(sample) == {
        "session_id", "bridge_build", "project", "mdb", "current_element", "errors"}, "context sample fields")
    _need(sample["session_id"] == session and sample["bridge_build"] == build, "session and build binding")
    errors = sample["errors"]
    _need(isinstance(errors, dict) and errors.keys() <= {"project", "mdb", "current_element"}
          and all(_text(v, nonempty=True) for v in errors.values()), "context read errors")
    for name in ("project", "mdb"):
        _need(sample[name] is None if name in errors else _text(sample[name], 256, nonempty=True), "context " + name)
    if "current_element" in errors:
        _need(sample["current_element"] is None, "unknown typed CE")
    else:
        _identity(sample["current_element"])


def _validate(reply: Any, payload: dict[str, Any]) -> None:
    _need(isinstance(reply, dict) and reply.get("ok") is True and reply.get("version") == VERSION
          and all(_text(reply.get(k), 256, nonempty=True) for k in ("session_id", "bridge_build")), "envelope")
    result = reply.get("result")
    _need(isinstance(result, dict) and result.get("operation") == "paragon_catalogue_manifest"
          and all(result.get(k) is True for k in ("read_only", "available", "single_dispatch"))
          and result.get("atomic_snapshot") is False and result.get("order") == "database_preorder", "scope")
    _need(result.get("requested_refno") == payload["refno"] and result.get("attributes") == payload["attributes"]
          and all(type(result.get(k)) is int and result[k] == payload[k] for k in ("max_depth", "limit")), "request echo")
    _need(result.get("scope") == {"kind": "explicit_catalogue_subtree", "database_type": "Catalogue", "database_type_code": 2,
          "whole_project_coverage": False, "current_module": None, "database_creation_verified": None,
          "engineering_validated": None}, "bounded catalogue scope and unknowns")
    _need(type(result.get("root_id")) is int and result["root_id"] == 0, "root ID")
    rows = result.get("rows")
    _need(isinstance(rows, list) and type(result.get("returned_count")) is int
          and 1 <= len(rows) == result["returned_count"] <= payload["limit"], "returned count including root")
    seen: set[str] = set()
    path: list[int] = []
    siblings: dict[int, int] = {}
    rows_complete = all_values_available = True
    for row_id, row in enumerate(rows):
        key = _identity(row, catalogue=True)
        _need(key not in seen and type(row.get("id")) is int and row["id"] == row_id, "preorder row identity")
        seen.add(key)
        depth = row.get("depth")
        _need(type(depth) is int and (depth == 0 if row_id == 0 else 1 <= depth <= min(len(path), payload["max_depth"])), "depth")
        if row_id == 0:
            _need(row.get("parent_index") is None and row.get("sibling_index") is None, "subtree root indices")
        else:
            parent = path[depth - 1]
            ordinal = siblings.get(parent, 0) + 1
            _need(type(row.get("parent_index")) is int and row["parent_index"] == parent
                  and type(row.get("sibling_index")) is int and row["sibling_index"] == ordinal, "native parent and sibling")
            siblings[parent] = ordinal
            _need(_identity(row.get("owner"), catalogue=True) == rows[parent]["identity_key"], "typed native owner")
        path[depth:] = [row_id]
        _need("owner" in row and "owner_error" in row, "native owner evidence")
        if row["owner_error"] is None:
            if row["owner"] is not None:
                _identity(row["owner"])
        else:
            _need(row_id == 0 and row["owner"] is None and _text(row["owner_error"], nonempty=True), "unknown external root owner")
        name, error = row.get("name"), row.get("name_error")
        _need("name" in row and "name_error" in row and (
            _text(name, nonempty=True) and error is None or name is None and _text(error, nonempty=True)), "native name evidence")
        values_available = _row_values(row, set(payload["attributes"]))
        complete = values_available and error is None and row["owner_error"] is None
        _need(row.get("complete") is complete, "row completeness")
        rows_complete &= complete
        all_values_available &= values_available
    root, target = rows[0], payload["refno"]
    _need(root["refno"] == target if target.startswith("=") else _text(root["name"], nonempty=True)
          and root["name"].removeprefix("/").upper() == target[1:].upper(), "explicit resolved root")
    _need(result.get("all_returned_values_available") is all_values_available, "returned attribute availability")

    reasons = result.get("truncation_reasons")
    _need(reasons in ([], ["depth"], ["limit"], ["depth", "limit"])
          and result.get("truncated") is bool(reasons), "truncation")
    if "limit" in reasons:
        _need(len(rows) == payload["limit"], "count limit")
    if "depth" in reasons:
        _need(rows[-1]["depth"] == payload["max_depth"], "depth limit")
    errors = result.get("read_errors")
    _need(isinstance(errors, list) and len(errors) <= 1, "traversal errors")
    if errors:
        error = errors[0]
        _need(not reasons and isinstance(error, dict) and error.get("stage") == "member"
              and _text(error.get("message"), nonempty=True), "member read error")
        parent_key, ordinal = error.get("parent_identity_key"), error.get("sibling_index")
        parent_ids = [i for i in path if rows[i]["identity_key"] == parent_key]
        _need(len(parent_ids) == 1 and type(ordinal) is int and ordinal == siblings.get(parent_ids[0], 0) + 1,
              "failed next native member")
    traversal_complete = not reasons and not errors
    _need(result.get("traversal_complete") is traversal_complete, "traversal completeness")
    context = result.get("context")
    _need(isinstance(context, dict) and set(context) == {"before", "after", "same_project_instance",
          "same_mdb_instance", "boundary_agreement"}, "context")
    before, after = context["before"], context["after"]
    for sample in (before, after):
        _boundary(sample, reply["session_id"], reply["bridge_build"])
    instances = (context["same_project_instance"], context["same_mdb_instance"])
    _need(all(value is None or type(value) is bool for value in instances), "project and MDB instance evidence")
    different = False in instances
    different |= any(before[k] is not None and after[k] is not None and before[k] != after[k]
                     for k in ("session_id", "bridge_build", "project", "mdb", "current_element"))
    unknown = any(value is None for value in instances) or bool(before["errors"] or after["errors"])
    agreement = False if different else None if unknown else True
    _need(context["boundary_agreement"] is agreement, "boundary agreement")
    complete = traversal_complete and rows_complete and agreement is True
    _need(result.get("success") is complete and result.get("complete") is complete, "observation completeness")


def _native_fault(value: Any) -> bool:
    if isinstance(value, dict):
        return (value.get("has_fault") is True or value.get("native_fault") is True or value.get("native_fault_latched") is True
                or value.get("error_code") in ("native_fault", "native_database_fault")
                or any(_native_fault(v) for v in value.values()))
    return isinstance(value, list) and any(_native_fault(v) for v in value)


def paragon_catalogue_manifest(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    payload = _parameters(args)
    version = bridge._selected_version(None)

    def unavailable(code: str, message: str) -> dict[str, Any]:
        return {"ok": True, "version": version, "result": {
            "operation": "paragon_catalogue_manifest", "requested_refno": payload["refno"],
            "success": False, "available": False, "complete": False, "traversal_complete": False,
            "all_returned_values_available": None, "read_only": True, "single_dispatch": None, "atomic_snapshot": False,
            "root_id": None, "rows": [], "returned_count": 0, "truncated": False, "truncation_reasons": [],
            "context": {"before": None, "after": None, "same_project_instance": None,
                        "same_mdb_instance": None, "boundary_agreement": None},
            "scope": {"kind": "explicit_catalogue_subtree", "whole_project_coverage": False,
                      "current_module": None, "database_creation_verified": None, "engineering_validated": None},
            "read_errors": [{"code": code, "message": message}],
        }}

    if version != VERSION or bridge.settings.mode != "bridge":
        return unavailable("native_unavailable", "Catalogue manifests require E3D21 bridge mode.")
    try:
        reply = bridge._request("POST", PATH, payload, version=VERSION)
    except BridgeError as error:
        if _native_fault(error.diagnostics):
            raise
        if error.http_status == 404:
            return unavailable("native_feature_unavailable", "The running Addin does not expose paragon_catalogue_manifest.")
        raise
    if _native_fault(reply):
        raise BridgeError("A native database fault stopped catalogue inspection; do not retry the read.", diagnostics=reply)
    try:
        _validate(reply, payload)
    except ValueError as error:
        return unavailable("invalid_native_response", str(error))
    return reply


def register_paragon_catalogue_manifest(server: McpServer, bridge: BridgeClient) -> None:
    server.add_tool(
        TOOL,
        "Read a bounded native preorder manifest beneath an explicit E3D21 Catalogue /NAME or =database/element "
        "in one UI callback. limit includes root ID 0; depth is measured from that root. Rows retain native base "
        "type, NAME, typed owner, parent_index and native Member sibling_index, SDK formatted attributes and scalar "
        "values. Inspect unavailable_attributes, row errors, traversal_complete, truncation_reasons and before/after "
        "session/build/project/MDB/typed CE evidence. Unknown context stays unknown. complete concerns only this "
        "bounded observation; Admin database creation, PARAGON permissions, current module and engineering validity "
        "are unverified. No PML, CE selection, module switch or model writes. Requires a supporting E3D21 Addin.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "minLength": 1, "maxLength": MAX_REF_LENGTH,
                      "description": "One explicit literal Catalogue /NAME or =database/element; never CE or a wildcard."},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": MAX_DEPTH, "default": DEFAULT_DEPTH},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": DEFAULT_LIMIT,
                      "description": "Maximum rows including the root; limit times attribute count must not exceed 500."},
            "attributes": {"type": "array", "maxItems": MAX_ATTRIBUTES, "uniqueItems": True, "default": [],
                           "items": {"type": "string", "minLength": 1, "maxLength": 64},
                           "description": "Explicit SDK attribute names. Empty reads native identities and hierarchy only."},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: paragon_catalogue_manifest(bridge, args), read_only=True,
    )
