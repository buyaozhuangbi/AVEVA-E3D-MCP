"""Bounded raw/typed observations from the E3D21 specification reader.

The public server opts into POST /api/v1/piping/specification-read after
reviewing its exact native envelope and fault transport contract. Standalone
consumers must still explicitly supply a reviewed PipingSpecificationReadRoute;
importing this module never registers or probes an endpoint.

Requests require a numeric root, explicit native base-type ShortName and
explicit database kind. The native adapter owns session_id and bridge_build;
the validator binds both native context boundaries to that outer envelope.
Formatted and typed values retain independent errors. SDK unit categories are
not physical units; references expose one-hop identities without expansion.
No selection, compatibility, material or engineering inference occurs.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .bridge import BridgeClient, BridgeError
from .protocol import McpServer

TOOL = "e3d_piping_specification_read"
VERSION = "E3D21"
PATH_CANDIDATE = "/api/v1/piping/specification-read"
DEFAULT_DEPTH, MAX_DEPTH = 8, 16
DEFAULT_LIMIT, MAX_LIMIT = 64, 100
MAX_ATTRIBUTES, MAX_CELLS = 10, 500
_INT32_MAX = 2147483647
_REF = re.compile(r"=([0-9]+)/([0-9]+)")
_TYPE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_ATTRIBUTE = re.compile(r"[A-Za-z0-9_:]{1,64}")
_GUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_DATABASE_TYPES = {"Design": 1, "Catalogue": 2, "Property": 4, "Dictionary": 8}
_IDENTITY = {"refno", "type", "identity_key", "db_type", "db_type_code"}
_OBSERVATION = _IDENTITY | {"name", "name_error", "database"}
_VALUE_FIELDS = {
    "requested_name", "status", "supported", "sdk_type", "is_array", "unit_kind",
    "unit", "unit_status", "unit_reason", "formatted_value", "formatted_status",
    "typed_value", "typed_status", "reference_null", "errors",
}
_VALUE_ERRORS = {
    "attribute", "value", "sdk_type", "is_array", "unit_kind", "formatted", "typed", "reference_metadata",
}
_COMPLETENESS_SCOPE = (
    "Requested raw/typed observations and bounded ownership traversal only; "
    "units and engineering suitability remain unverified."
)


@dataclass(frozen=True)
class PipingSpecificationReadRoute:
    """Integrator assertions about an implemented route, never auto-discovered.

    envelope_context_verified includes preserving native version and binding
    top-level session/build to both native boundaries. The second assertion
    includes BridgeClient's HTTP/ok:false diagnostic preservation. Defaults
    keep an incomplete integration unavailable without probing any route.
    """

    path: str
    envelope_context_verified: bool = False
    native_fault_passthrough_verified: bool = False

    def ready(self) -> bool:
        return (
            type(self.path) is str
            and bool(re.fullmatch(r"/api/v1/[a-z0-9]+(?:[-/][a-z0-9]+)*", self.path))
            and self.path not in {"/api/v1/piping/member-manifest", "/api/v1/paragon/catalogue-manifest"}
            and self.envelope_context_verified is True
            and self.native_fault_passthrough_verified is True
        )


def _need(condition: bool, field: str) -> None:
    if not condition:
        raise ValueError(f"Invalid piping specification read evidence: {field}")


def _text(value: Any, limit: int = 4096, *, nonempty: bool = False) -> bool:
    return type(value) is str and len(value) <= limit and (not nonempty or bool(value.strip()))


def _refno(value: Any, *, canonical: bool = True) -> str:
    _need(type(value) is str and len(value) <= 256, "explicit numeric REF")
    match = _REF.fullmatch(value)
    _need(match is not None, "explicit numeric REF")
    database, element = (int(part) for part in match.groups())
    _need(1 <= database <= _INT32_MAX and 0 <= element <= _INT32_MAX, "Int32 REF pair")
    normalized = f"={database}/{element}"
    _need(not canonical or normalized == value, "canonical native REF")
    return normalized


def _parameters(args: dict[str, Any]) -> dict[str, Any]:
    fields = {"refno", "expected_type", "database_type", "max_depth", "limit", "attributes"}
    if type(args) is not dict or args.keys() - fields:
        raise ValueError("Only refno, expected_type, database_type, max_depth, limit and attributes are accepted")
    raw = args.get("refno")
    if type(raw) is not str or len(raw) > 256:
        raise ValueError("refno must be an explicit numeric =database/element reference")
    refno = _refno(raw.strip(), canonical=False)
    kind = args.get("expected_type")
    if type(kind) is not str or not _TYPE.fullmatch(kind.strip().upper()):
        raise ValueError("expected_type must explicitly name a native SDK type ShortName")
    database = args.get("database_type")
    canonical_database = next((name for name in _DATABASE_TYPES
                               if type(database) is str and name.upper() == database.strip().upper()), None)
    if canonical_database is None:
        raise ValueError("database_type must explicitly be Design, Catalogue, Property or Dictionary")
    depth, limit = args.get("max_depth", DEFAULT_DEPTH), args.get("limit", DEFAULT_LIMIT)
    if type(depth) is not int or not 1 <= depth <= MAX_DEPTH:
        raise ValueError("max_depth must be an integer from 1 through 16")
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("limit must be an integer from 1 through 100, including the root")
    attributes = args.get("attributes", [])
    if type(attributes) is not list or len(attributes) > MAX_ATTRIBUTES or len(attributes) * limit > MAX_CELLS:
        raise ValueError("At most 10 attributes and 500 requested row/attribute cells are allowed")
    names = []
    for name in attributes:
        if type(name) is not str or not _ATTRIBUTE.fullmatch(name.strip()):
            raise ValueError("attributes must be explicit SDK names of 1 to 64 ASCII characters")
        names.append(name.strip().upper())
    if len(set(names)) != len(names):
        raise ValueError("attributes must be unique after normalization")
    return {"refno": refno, "expected_type": kind.strip().upper(), "database_type": canonical_database,
            "max_depth": depth, "limit": limit, "attributes": names}


def _errors(value: Any, allowed: set[str], field: str) -> dict[str, str]:
    _need(type(value) is dict and value.keys() <= allowed
          and all(_text(message, nonempty=True) for message in value.values()), field)
    return value


def _identity(value: Any) -> str:
    _need(type(value) is dict and _IDENTITY <= value.keys(), "typed native identity")
    reference = _refno(value["refno"])
    kind, database, code = (value[key] for key in ("type", "db_type", "db_type_code"))
    _need(type(kind) is str and bool(_TYPE.fullmatch(kind))
          and value["identity_key"] == f"{reference}|{kind}", "typed REF key")
    _need(_text(database, 64, nonempty=True) and type(code) is int
          and -2147483648 <= code <= _INT32_MAX, "native database type")
    for name, number in _DATABASE_TYPES.items():
        _need((code == number) == (database == name), "canonical native database type")
    return value["identity_key"]


def _database(value: Any, identity: dict[str, Any]) -> bool:
    _need(type(value) is dict and set(value) == {"status", "name", "number", "type", "type_code", "errors"},
          "database fields")
    errors = _errors(value["errors"], {"database", "name", "number", "type"}, "database errors")
    observed = not errors
    _need(value["status"] == ("observed" if observed else "unknown"), "database status")
    if "database" in errors:
        _need(set(errors) == {"database"} and all(value[k] is None for k in ("name", "number", "type", "type_code")),
              "unknown database getter")
        return False
    _need(value["name"] is None if "name" in errors else _text(value["name"], 256, nonempty=True), "database name")
    number = value["number"]
    _need(number is None if "number" in errors else type(number) is int and 1 <= number <= _INT32_MAX,
          "independent native database number")
    name, code = value["type"], value["type_code"]
    if "type" in errors and name is None and code is None:
        return False
    _need(_text(name, 64, nonempty=True) and type(code) is int and -2147483648 <= code <= _INT32_MAX,
          "independent database type observation")
    agrees = code > 0 and code == identity["db_type_code"] and name == identity["db_type"]
    _need(("type" not in errors) is agrees, "database type agreement/error evidence")
    return observed


def _observation(value: Any, *, reference: bool = False) -> bool:
    _identity(value)
    _need(_OBSERVATION <= value.keys() and (not reference or set(value) == _OBSERVATION),
          "one-hop observation fields")
    name, error = value["name"], value["name_error"]
    _need(_text(name, nonempty=True) and error is None
          or name is None and _text(error, nonempty=True), "native name or field error")
    database_complete = _database(value["database"], value)
    return error is None and database_complete


def _attribute(value: Any, name: str) -> bool:
    _need(type(value) is dict and set(value) == _VALUE_FIELDS and value["requested_name"] == name,
          "attribute fields and request binding")
    errors = _errors(value["errors"], _VALUE_ERRORS, "attribute field errors")
    _need(value["unit"] is None and value["unit_status"] == "unknown"
          and value["unit_reason"] == "no_explicit_unit_conversion", "unknown physical unit")
    supported = value["supported"]
    _need(supported is None or type(supported) is bool, "attribute support status")
    if supported is not True:
        _need(set(errors) == ({"attribute"} if supported is None else {"value"})
              and all(value[k] is None for k in ("sdk_type", "is_array", "unit_kind", "formatted_value", "typed_value", "reference_null"))
              and all(value[k] == "unknown" for k in ("status", "formatted_status", "typed_status")),
              "unsupported or undefined attribute remains unknown")
        return False
    _need(not ({"attribute", "value"} & errors.keys()), "supported attribute errors")
    for field in ("sdk_type", "is_array", "unit_kind"):
        check = type(value[field]) is bool if field == "is_array" else _text(value[field], 64, nonempty=True)
        _need(value[field] is None if field in errors else check, "attribute metadata " + field)
    formatted = value["formatted_status"] == "observed"
    _need(value["formatted_status"] in ("observed", "unknown")
          and formatted is ("formatted" not in errors)
          and (_text(value["formatted_value"]) if formatted else value["formatted_value"] is None), "formatted evidence")
    typed = value["typed_status"] == "observed"
    _need(value["typed_status"] in ("observed", "unknown") and typed is ("typed" not in errors), "typed status/error")
    kind, item, reference_null = value["sdk_type"], value["typed_value"], value["reference_null"]
    _need(reference_null is None or type(reference_null) is bool, "reference null status")
    if typed:
        _need(value["is_array"] is False, "scalar typed value")
        if kind == "ELEMENT":
            _need(reference_null is False, "observed reference is non-null")
            metadata_complete = _observation(item, reference=True)
            _need(("reference_metadata" not in errors) is metadata_complete, "reference metadata error evidence")
        else:
            _need(reference_null is None and "reference_metadata" not in errors, "scalar reference metadata")
            valid = (
                kind == "DOUBLE" and type(item) in (int, float) and -1.7976931348623157e308 <= item <= 1.7976931348623157e308
                or kind == "INTEGER" and type(item) is int and -2147483648 <= item <= _INT32_MAX
                or kind == "BOOL" and type(item) is bool
                or kind == "STRING" and _text(item)
            )
            _need(valid, "SDK scalar type/value")
    else:
        _need(item is None and "reference_metadata" not in errors, "unknown typed value")
        _need(reference_null is None or kind == "ELEMENT" and value["is_array"] is False,
              "unknown reference null evidence")
    complete = not errors and formatted and typed
    _need(value["status"] == ("observed" if complete else "unknown"), "attribute completeness")
    return complete


def _boundary(value: Any, session: str, build: str) -> None:
    _need(type(value) is dict and set(value) == {
        "session_id", "bridge_build", "project", "mdb", "current_element", "errors"}, "context boundary fields")
    _need(value["session_id"] == session and value["bridge_build"] == build, "envelope session/build binding")
    errors = _errors(value["errors"], {"project", "mdb", "current_element"}, "context field errors")
    for field in ("project", "mdb"):
        _need(value[field] is None if field in errors else _text(value[field], 256, nonempty=True), "context " + field)
    if "current_element" in errors:
        _need(value["current_element"] is None, "unknown typed CE")
    else:
        _identity(value["current_element"])
        _need(set(value["current_element"]) == _IDENTITY, "typed CE fields")


def _scope() -> dict[str, Any]:
    return {"kind": "explicit_typed_subtree_and_reference_identities", "reference_hops": 1,
            "reference_targets_expanded": False, "whole_project_coverage": False, "units_verified": False,
            "engineering_compatible": None, "material_selected": None, "database_access_authorized": None}


def _validate(reply: Any, payload: dict[str, Any]) -> None:
    _need(type(reply) is dict and reply.get("ok") is True and reply.get("version") == VERSION,
          "native E3D21 envelope")
    _need(all(_text(reply.get(k), 256) and bool(_GUID.fullmatch(reply[k]))
              for k in ("session_id", "bridge_build")), "native envelope session and build")
    result = reply.get("result")
    _need(type(result) is dict and result.get("operation") == "piping_specification_read"
          and all(result.get(k) is True for k in ("available", "read_only", "single_dispatch"))
          and result.get("atomic_snapshot") is False and result.get("order") == "database_preorder", "operation scope")
    for field in ("expected_type", "database_type", "attributes"):
        _need(result.get(field) == payload[field], "request echo " + field)
    _need(result.get("requested_refno") == payload["refno"]
          and type(result.get("database_type_code")) is int
          and result["database_type_code"] == _DATABASE_TYPES[payload["database_type"]], "typed request echo")
    for field in ("max_depth", "limit"):
        _need(type(result.get(field)) is int and result[field] == payload[field], "request bound " + field)
    scope = result.get("scope")
    _need(type(scope) is dict and set(scope) == set(_scope())
          and all(type(scope[k]) is type(v) and scope[k] == v for k, v in _scope().items())
          and result.get("completeness_scope") == _COMPLETENESS_SCOPE, "bounded scope and unverified engineering claims")
    rows = result.get("rows")
    _need(type(rows) is list and type(result.get("returned_count")) is int
          and 1 <= len(rows) == result["returned_count"] <= payload["limit"], "bounded row count including root")
    seen: set[str] = set()
    path: list[int] = []
    siblings: dict[int, int] = {}
    rows_complete = True
    for index, row in enumerate(rows):
        metadata_complete = _observation(row)
        key = row["identity_key"]
        _need(key not in seen and type(row.get("id")) is int and row["id"] == index, "typed preorder identity")
        seen.add(key)
        _need(row["db_type"] == payload["database_type"] and row["db_type_code"] == result["database_type_code"],
              "requested native database type")
        depth = row.get("depth")
        _need(type(depth) is int and (depth == 0 if index == 0 else 1 <= depth <= min(len(path), payload["max_depth"])),
              "preorder depth")
        _need({"owner", "owner_error", "parent_index", "sibling_index", "attributes", "complete"} <= row.keys(), "row fields")
        if index == 0:
            _need(row["refno"] == payload["refno"] and row["type"] == payload["expected_type"], "explicit numeric typed root")
            _need(row["parent_index"] is None and row["sibling_index"] is None, "root indices")
        else:
            parent = path[depth - 1]
            ordinal = siblings.get(parent, 0) + 1
            _need(type(row["parent_index"]) is int and row["parent_index"] == parent
                  and type(row["sibling_index"]) is int and row["sibling_index"] == ordinal, "native member indices")
            siblings[parent] = ordinal
            _need(_identity(row["owner"]) == rows[parent]["identity_key"]
                  and row["owner"]["db_type"] == rows[parent]["db_type"]
                  and row["owner"]["db_type_code"] == rows[parent]["db_type_code"], "typed member ownership")
        path[depth:] = [index]
        if row["owner_error"] is None:
            _identity(row["owner"])
            _need(set(row["owner"]) == _IDENTITY, "typed owner fields")
        else:
            _need(index == 0 and row["owner"] is None and _text(row["owner_error"], nonempty=True), "root owner field error")
        attributes = row["attributes"]
        _need(type(attributes) is dict and set(attributes) == set(payload["attributes"]), "requested attribute coverage")
        values_complete = True
        for name in payload["attributes"]:
            values_complete &= _attribute(attributes[name], name)
        complete = metadata_complete and row["owner_error"] is None and values_complete
        _need(row["complete"] is complete, "row completeness")
        rows_complete &= complete

    reasons = result.get("truncation_reasons")
    _need(type(reasons) is list and reasons in ([], ["depth"], ["limit"], ["depth", "limit"])
          and result.get("truncated") is bool(reasons), "truncation evidence")
    _need("limit" not in reasons or len(rows) == payload["limit"], "row limit truncation")
    _need("depth" not in reasons or rows[-1]["depth"] == payload["max_depth"], "depth truncation")
    errors = result.get("read_errors")
    _need(type(errors) is list and len(errors) <= 1, "bounded traversal errors")
    if errors:
        error = errors[0]
        _need(not reasons and type(error) is dict and set(error) == {
            "stage", "parent_identity_key", "sibling_index", "message"}
            and error["stage"] == "member" and _text(error["message"], nonempty=True), "member read error")
        parents = [i for i in path if rows[i]["identity_key"] == error["parent_identity_key"]]
        _need(len(parents) == 1 and type(error["sibling_index"]) is int
              and error["sibling_index"] == siblings.get(parents[0], 0) + 1, "failed next native member")
    traversal_complete = not reasons and not errors
    _need(result.get("traversal_complete") is traversal_complete, "traversal completeness")
    context = result.get("context")
    _need(type(context) is dict and set(context) == {
        "before", "after", "same_project_instance", "same_mdb_instance", "boundary_agreement"}, "context fields")
    before, after = context["before"], context["after"]
    for sample in (before, after):
        _boundary(sample, reply["session_id"], reply["bridge_build"])
    instances = (context["same_project_instance"], context["same_mdb_instance"])
    _need(all(item is None or type(item) is bool for item in instances), "native project/MDB instance evidence")
    different = any(item is False for item in instances)
    different |= any(before[k] is not None and after[k] is not None and before[k] != after[k]
                     for k in ("project", "mdb", "current_element"))
    unknown = any(item is None for item in instances) or bool(before["errors"] or after["errors"])
    agreement = False if different else None if unknown else True
    _need(context["boundary_agreement"] is agreement, "project/MDB/typed CE boundary agreement")
    complete = traversal_complete and rows_complete and agreement is True
    _need(result.get("complete") is complete and result.get("success") is complete, "observation completeness")


def _native_fault(value: Any) -> bool:
    if type(value) is dict:
        if any(value.get(key) is True for key in ("has_fault", "native_fault", "native_database_fault", "native_fault_latched")):
            return True
        code = value.get("error_code")
        return (type(code) is str and code in {"native_fault", "native_database_fault"}
                or any(_native_fault(item) for item in value.values()))
    return type(value) is list and any(_native_fault(item) for item in value)


def _unavailable(version: str, payload: dict[str, Any], code: str, message: str) -> dict[str, Any]:
    return {"ok": True, "version": version, "result": {
        "operation": "piping_specification_read", "requested_refno": payload["refno"],
        "expected_type": payload["expected_type"], "database_type": payload["database_type"],
        "database_type_code": _DATABASE_TYPES[payload["database_type"]],
        "attributes": payload["attributes"], "max_depth": payload["max_depth"], "limit": payload["limit"],
        "available": False, "success": False, "complete": False, "traversal_complete": False,
        "read_only": True, "single_dispatch": None, "atomic_snapshot": False,
        "rows": [], "returned_count": 0, "truncated": False, "truncation_reasons": [],
        "context": {"before": None, "after": None, "same_project_instance": None,
                    "same_mdb_instance": None, "boundary_agreement": None},
        "scope": _scope(), "completeness_scope": _COMPLETENESS_SCOPE,
        "read_errors": [{"code": code, "message": message}],
    }}


def piping_specification_read(
    bridge: BridgeClient, args: dict[str, Any], *, route: PipingSpecificationReadRoute | None = None,
) -> dict[str, Any]:
    payload = _parameters(args)
    version = bridge._selected_version(None)
    if version != VERSION or bridge.settings.mode != "bridge":
        return _unavailable(version, payload, "native_unavailable", "Specification reads require E3D21 bridge mode.")
    if type(route) is not PipingSpecificationReadRoute or not route.ready():
        return _unavailable(version, payload, "native_route_unverified",
                            "The specification reader has no reviewed route/envelope/fault transport contract.")
    try:
        reply = bridge._request("POST", route.path, payload, version=VERSION)
    except BridgeError as error:
        if _native_fault(error.diagnostics):
            raise
        if error.http_status == 404:
            return _unavailable(version, payload, "native_feature_unavailable", "The running Addin does not expose the reviewed specification reader.")
        raise
    if _native_fault(reply):
        raise BridgeError("A native database fault stopped specification inspection; do not retry the read.", diagnostics=reply)
    try:
        _validate(reply, payload)
    except ValueError as error:
        return _unavailable(version, payload, "invalid_native_response", str(error))
    return reply


def register_piping_specification_read(
    server: McpServer, bridge: BridgeClient, *, route: PipingSpecificationReadRoute | None = None,
) -> None:
    server.add_tool(
        TOOL,
        "Read bounded raw/typed SDK observations for one explicit numeric E3D21 REF, native expected_type and "
        "database_type. Keep independent formatted/typed statuses, per-field errors, typed owners and one-hop reference "
        "identities. Units remain unknown; complete covers only requested observations, bounded ownership traversal and "
        "matching session/build/project/MDB/typed CE boundaries. No compatibility check, automatic selection, PML or "
        "model changes. Requires a reviewed native route and envelope/fault transport contract.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "minLength": 1, "maxLength": 256,
                      "description": "Explicit numeric =database/element; names, CE, patterns and inferred aliases are excluded."},
            "expected_type": {"type": "string", "minLength": 1, "maxLength": 64,
                              "description": "Explicit native SDK type ShortName; a shared BRAN/TUBI REF does not infer an alias."},
            "database_type": {"type": "string", "enum": list(_DATABASE_TYPES),
                              "description": "Explicit native database type, independent of the numeric REF database number."},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": MAX_DEPTH, "default": DEFAULT_DEPTH},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": DEFAULT_LIMIT,
                      "description": "Rows including root; limit times attribute count must be at most 500."},
            "attributes": {"type": "array", "maxItems": MAX_ATTRIBUTES, "uniqueItems": True, "default": [],
                           "items": {"type": "string", "minLength": 1, "maxLength": 64},
                           "description": "Explicit SDK attribute names; empty requests identities only."},
        }, "required": ["refno", "expected_type", "database_type"], "additionalProperties": False},
        lambda args: piping_specification_read(bridge, args, route=route), read_only=True,
    )
