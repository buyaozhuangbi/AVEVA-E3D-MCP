"""Bounded, read-only E3D21 Team/database identity observations.

The public wrapper captures server session/build once from independent health,
then dispatches the manifest once and validates its original native receipt.
The caller supplies a previously observed database-state context_id; neither
that context nor the independent provenance is filled from the manifest reply.
There is no database-state, identity-snapshot, catalog, Admin or PML fallback.

The native signature is (server sessionId, expectedContext, limit=64). Session
and module MVID/build are provenance, not client-selectable native arguments.
The native result has no ``available`` field or top-level session/build fields:
those identities live in result.context. Health and manifest are sequential
observations, not an atomic two-request snapshot or an authorization check.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import re
import unicodedata
from typing import Any

from .bridge import BridgeClient, BridgeError
from .protocol import McpServer

VERSION = "E3D21"
TOOL = "e3d_team_database_identity_manifest"
PATH = "/api/v1/database/team-identity-manifest"
FEATURE = "team_database_identity_manifest"
OPERATION = "internal_team_database_identity_manifest"
DEFAULT_LIMIT, MAX_LIMIT, MAX_MATERIALIZED_ARRAY = 64, 250, 10000
MAX_TEXT, MAX_ROW_ERRORS = 256, 8
_INT_MAX = 2147483647
_CONTEXT = re.compile(r"[a-f0-9]{64}")
_BUILD = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")
_REF = re.compile(r"=([1-9][0-9]{0,9})/(0|[1-9][0-9]{0,9})")
_TYPE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_SOURCES = ("teams", "databases", "current_mdb")
_MATCH_FIELDS = ["canonical_name", "DBNO/Number", "raw_EXTNO/raw_ExtractNumber"]
# Installed E3D21 DbType metadata, including its documented enum aliases.
# Codes from managed fakes or other prototype key formats are not provenance.
_DB_TYPES = {
    "Design": 1, "Catalog": 2, "Catalogue": 2, "Communications": 3,
    "Property": 4, "Miscellaneous": 5, "Spooler": 6, "Isodraft": 6,
    "Draft": 7, "Dictionary": 8, "Engineering": 9, "System": 10,
    "Global": 11, "Transaction": 12, "Marine": 13, "Schematic": 14,
    "Manu": 15, "NameSequence": 16, "NSEQ": 16, "CrossProduct": 17,
    "Configuration": 18, "Task": 19, "ConceptualModel": 20,
}
_SCOPE = {
    "kind": "bounded_sdk_visible_arrays", "project_coverage_verified": False,
    "world_level_equivalence_inspected": False, "admin_permission_verified": False,
    "file_existence_verified": False, "database_creation_verified": False,
    "extract_number_semantics_verified": False, "mdb_record_join_verified": False,
}


def route_wiring_contract() -> dict[str, Any]:
    """Describe the reviewed public transport and native receipt contract."""
    return {
        "wired": True, "public_http_route": PATH,
        "native_method": "object ReadTeamDatabaseIdentityManifest(string sessionId, string expectedContext, int limit = 64)",
        "request_parameters": {
            "expected_context": {"required": True, "format": "64 lowercase hexadecimal characters"},
            "limit": {"type": "integer", "default": DEFAULT_LIMIT, "minimum": 1, "maximum": MAX_LIMIT},
        },
        "server_arguments": {"sessionId": "server-owned bridge session; never accept from request parameters"},
        "dispatch": "ReadTeamDatabaseIdentityManifest(sessionId, expected_context, limit) once in the existing supported SDK/UI context",
        "envelope_fields": ["ok", "version", "session_id", "bridge_build", "result"],
        "envelope_binding": "E3D21; session_id and bridge_build must equal result.context and independently expected session/build",
        "context_binding": "SHA256(UTF8(session_id + LF + project + LF + mdb)); typed CE and project/MDB before/after agreement",
        "native_result_operation": OPERATION, "schema_version": 1,
        "limits": {"per_source": MAX_LIMIT, "materialized_array": MAX_MATERIALIZED_ARRAY, "owner_hops": 2},
        "error_contract": "preserve recursive native fault diagnostics before HTTP/availability classification; never retry or fall back",
        "transport_requirement": "one independent E3D21 health read then one manifest POST; preserve original failure diagnostics and independently bind session/build",
        "coverage": "positive observations only; partial or unmatched evidence is unknown and cannot establish project-wide absence",
    }


def _need(condition: bool, field: str) -> None:
    if not condition:
        raise ValueError(f"Invalid Team/database identity evidence: {field}")


def _text(value: Any, maximum: int = MAX_TEXT, *, empty: bool = False, controls: bool = False) -> bool:
    if type(value) is not str or not empty and not value.strip():
        return False
    try:
        return (len(value.encode("utf-16-le")) // 2 <= maximum
                and (controls or not any(unicodedata.category(c) == "Cc" for c in value)))
    except UnicodeEncodeError:
        return False


def _integer(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= _INT_MAX


def _parameters(args: dict[str, Any]) -> dict[str, Any]:
    if type(args) is not dict or set(args) - {"expected_context", "limit"}:
        raise ValueError("Only expected_context and limit are accepted")
    context, limit = args.get("expected_context"), args.get("limit", DEFAULT_LIMIT)
    if type(context) is not str or not _CONTEXT.fullmatch(context):
        raise ValueError("expected_context must be a 64-character lowercase hexadecimal context_id")
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be an integer between 1 and {MAX_LIMIT}")
    return {"expected_context": context, "limit": limit}


def _native_fault(value: Any) -> bool:
    # Inspect faults before schema checks and output caps. Iteration also avoids
    # recursion limits and handles cycles in caller-supplied offline objects.
    pending, seen = [value], set()
    while pending:
        item = pending.pop()
        if not isinstance(item, (dict, list, tuple)) or id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, dict):
            if (any(item.get(key) is True for key in ("has_fault", "native_fault", "native_database_fault", "native_fault_latched"))
                    or any(item.get(key) in ("native_fault", "native_database_fault") for key in ("code", "error_code"))):
                return True
            pending.extend(item.values())
        else:
            pending.extend(item)
    return False


def _unavailable(payload: dict[str, Any], reason: str = "native_feature_unavailable") -> dict[str, Any]:
    return {
        "operation": OPERATION, "status": "unavailable", "available": False,
        "reason": reason, "success": False, "complete": False,
        "read_only": True, "single_dispatch": None, "atomic_snapshot": False,
        "request": payload, "positive_matches": [], "native_observation": None,
        "project_coverage_verified": False, "route_wiring_contract": route_wiring_contract(),
    }


def team_database_identity_manifest(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    """Read health once, then validate one source-bound manifest observation.

    Missing native capabilities are unavailable. Malformed/unready health and
    malformed receipts fail closed; failures never trigger another native read.
    """
    payload = _parameters(args)
    if bridge.settings.mode != "bridge" or bridge._selected_version(None) != VERSION:
        return _unavailable(payload, "native_unavailable")
    health = bridge.health(version=VERSION)
    if _native_fault(health):
        raise BridgeError("A native database fault stopped identity inspection; do not retry.", diagnostics=health)
    session, build = _health_provenance(health)
    features = health.get("api_features")
    _need(type(features) is list and all(type(item) is str for item in features), "health api_features")
    if FEATURE not in features:
        return _unavailable(payload)
    try:
        reply = bridge._request("POST", PATH, payload, version=VERSION)
    except BridgeError as error:
        # The validator scans the original diagnostics before classifying 404.
        reply = error
    return validate_team_database_identity_manifest(
        reply, payload, expected_session=session, expected_build=build,
    )


def _health_provenance(reply: Any) -> tuple[str, str]:
    _need(type(reply) is dict and reply.get("ok") is True and reply.get("version") == VERSION,
          "native E3D21 health envelope")
    session, build = reply.get("session_id"), reply.get("bridge_build")
    _need(_text(session) and type(build) is str and bool(_BUILD.fullmatch(build)),
          "independent health session/build")
    state = reply.get("state")
    _need(type(state) is dict and reply.get("connected") is True
          and state.get("database_ready") is True and state.get("busy") is False
          and state.get("recovery_required") is False, "native health readiness")
    fault, quarantine = state.get("native_database_fault"), state.get("generated_csg_quarantine")
    _need(type(fault) is dict and fault.get("has_fault") is False
          and type(quarantine) is dict and quarantine.get("active") is False,
          "native health fault/quarantine observations")
    _need(reply.get("diagnostic_error") is None, "native health diagnostics")
    return session, build


def register_team_database_identity_manifest(server: McpServer, bridge: BridgeClient) -> None:
    server.add_tool(
        TOOL,
        "Observe bounded typed System/Global TEAM, DBLI and DB identities and current MDB relations in E3D21 bridge mode. "
        "Supply expected_context from an earlier e3d_database_state context_id. Reads independent health once and then "
        "one manifest POST; native single_dispatch applies only to the manifest. Each SDK-visible source returns its "
        "own bounded prefix, not a stable paginated catalog. Unmatched or partial evidence remains unknown. "
        "Does not select a creation parent DBLI, establish project-wide absence, inspect Admin authorization, create "
        "databases, issue executable previews, execute PML or change models. Mock and other versions are unavailable.",
        {"type": "object", "properties": {
            "expected_context": {"type": "string", "pattern": "^[a-f0-9]{64}$", "minLength": 64, "maxLength": 64,
                                 "description": "Exact prior e3d_database_state context_id; never automatically refreshed."},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": DEFAULT_LIMIT,
                      "description": "Independent maximum for each of TEAM, system DB records and current MDB arrays."},
        }, "required": ["expected_context"], "additionalProperties": False},
        lambda args: team_database_identity_manifest(bridge, args), read_only=True,
    )


def _fields(value: Any, names: set[str], field: str) -> None:
    _need(type(value) is dict and set(value) == names, field + " fields")


def _errors(value: Any, maximum: int, field: str) -> list[dict[str, Any]]:
    _need(type(value) is list and len(value) <= maximum, field + " count")
    for item in value:
        _fields(item, {"path", "code", "message"}, field)
        _need(_text(item["path"]) and _text(item["code"], 64)
              and _text(item["message"], 512, empty=True, controls=True), field + " text")
    return value


def _has_error(errors: list[dict[str, Any]], path: str) -> bool:
    return any(error["path"] == path for error in errors)


def _identity(value: Any, types: dict[int, str], *, record_type: str | None = None) -> str:
    _fields(value, {"refno", "type", "db_type", "db_type_code", "identity_key"}, "typed identity")
    refno, kind, storage, code = (value[k] for k in ("refno", "type", "db_type", "db_type_code"))
    match = _REF.fullmatch(refno) if type(refno) is str else None
    _need(match is not None and all(int(part) <= _INT_MAX for part in match.groups()), "native DBREF")
    _need(type(kind) is str and bool(_TYPE.fullmatch(kind)) and type(storage) is str
          and bool(_TYPE.fullmatch(storage.upper())) and type(code) is int
          and -2147483648 <= code <= _INT_MAX, "native type and storage")
    _need(value["identity_key"] == f"{code}|{refno}|{kind}", "storage/DBREF/type identity key")
    _need(_DB_TYPES.get(storage) == code and types.get(code, storage) == storage
          and all(k == code or v != storage for k, v in types.items()),
          "consistent native storage code")
    types[code] = storage
    if record_type is not None:
        _need(kind == record_type and storage in {"System", "Global"}, "typed System/Global " + record_type)
    return value["identity_key"]


def _canonical(raw: str, database: bool) -> str:
    _need(_text(raw) and not any(c.isspace() for c in raw), "literal native name")
    name = raw[2:] if raw.startswith("/*") else raw[1:] if raw.startswith("/") else raw
    # E3D stores TEAM names with a leading `*` marker (for example
    # `*CATADMIN`).  It is a namespace marker, not a wildcard.  Database
    # names retain the strict wildcard rejection below.
    if not database and name.startswith("*"):
        name = name[1:]
    parts = name.split("/")
    _need(len(parts) == (2 if database else 1) and all(
        part and not any(c in part for c in "*?") and part.upper() != "UNSET" for part in parts), "full Team or Team/DB name")
    # Native ToUpperInvariant is simple casing; it does not expand one codepoint
    # into several characters (for example, sharp s into SS).
    return "".join(c.upper() if len(c.upper()) == 1 else c for c in name)


def _name(row: dict[str, Any], raw_key: str, database: bool, errors: list[dict[str, Any]],
          prefix: str, native_key: str, *, unread: bool = False) -> None:
    raw, canonical = row[raw_key], row["canonical_name"]
    _need(raw is None or _text(raw, empty=True, controls=True), prefix + " raw name bound")
    if unread:
        _need(raw is None and canonical is None, prefix + " unread name")
        return
    _need(raw is not None or _has_error(errors, prefix + "." + native_key), prefix + " missing name error")
    if canonical is None:
        _need(_has_error(errors, prefix + ".canonical_name"), prefix + " missing canonical-name error")
    else:
        _need(type(canonical) is str and raw is not None and canonical == _canonical(raw, database),
              prefix + " canonical name")


def _nullable_number(row: dict[str, Any], key: str, minimum: int, errors: list[dict[str, Any]],
                     path: str, *, unread: bool = False) -> None:
    value = row[key]
    _need(value is None if unread else _integer(value, minimum) or value is None and _has_error(errors, path), path)


def _boundary(sample: Any, session: str, types: dict[int, str], *, before: bool) -> None:
    _fields(sample, {"project", "mdb", "context_id", "ce", "ce_identity_key"}, "context boundary")
    for key in ("project", "mdb"):
        _need(_text(sample[key], empty=not before, controls=not before)
              or not before and sample[key] is None, "context " + key)
    expected = None
    if sample["project"] is not None and sample["mdb"] is not None:
        expected = hashlib.sha256((session + "\n" + sample["project"] + "\n" + sample["mdb"]).encode("utf-8")).hexdigest()
    _need(sample["context_id"] == expected, "session/project/MDB digest")
    if sample["ce"] is None:
        _need(not before and sample["ce_identity_key"] is None, "unreadable typed CE")
    else:
        _need(sample["ce_identity_key"] == _identity(sample["ce"], types), "typed CE identity key")


def _scan(scan: Any, rows: Any, limit: int, source: str) -> bool:
    _fields(scan, {"available", "materialized_count", "returned_count", "prefix_complete", "has_more",
                   "materializes_before_limit"}, source + " scan")
    _need(type(rows) is list and len(rows) <= limit, source + " row cap")
    total = scan["materialized_count"]
    _need(total is None or _integer(total), source + " materialized count")
    usable = total is not None and total <= MAX_MATERIALIZED_ARRAY
    _need(scan["available"] is usable and type(scan["returned_count"]) is int
          and scan["returned_count"] == len(rows) == (min(total, limit) if usable else 0)
          and scan["prefix_complete"] is (usable and len(rows) == total)
          and scan["has_more"] is ((len(rows) < total) if usable else None)
          and scan["materializes_before_limit"] is True, source + " scan accounting")
    return scan["prefix_complete"]


def _row_errors(row: Any, names: set[str], source: str, index: int,
                suffixes: set[str]) -> tuple[str, list[dict[str, Any]]]:
    prefix = f"{source}[{index}]"
    _fields(row, names, prefix)
    _need(type(row["index"]) is int and row["index"] == index, prefix + " index")
    errors = _errors(row["read_errors"], MAX_ROW_ERRORS, prefix + " errors")
    _need(all(error["path"] in {prefix + suffix for suffix in suffixes} for error in errors), prefix + " error paths")
    return prefix, errors


def _rows(result: dict[str, Any], types: dict[int, str]) -> list[dict[str, Any]]:
    collected = []
    for index, row in enumerate(result["teams"]):
        prefix, errors = _row_errors(row, {"index", "identity", "namn", "canonical_name", "read_errors"},
                                     "teams", index, {".identity", ".NAMN", ".canonical_name"})
        missing = row["identity"] is None
        _need(not missing or _has_error(errors, prefix + ".identity"), prefix + " identity error")
        if not missing:
            _identity(row["identity"], types, record_type="TEAM")
        _name(row, "namn", False, errors, prefix, "NAMN", unread=missing)
        collected.extend(errors)
    for index, row in enumerate(result["databases"]):
        prefix, errors = _row_errors(row, {
            "index", "identity", "namedb", "canonical_name", "dbno", "fino", "extno", "extract_owner_state",
            "extown", "extract_kind", "owner_path", "team_relation", "current_mdb", "read_errors",
        }, "databases", index, {".identity", ".NAMEDB", ".canonical_name", ".DBNO", ".FINO", ".EXTNO", ".EXTOWN", ".owner_path"})
        missing = row["identity"] is None
        _need(not missing or _has_error(errors, prefix + ".identity"), prefix + " identity error")
        if not missing:
            _identity(row["identity"], types, record_type="DB")
        _name(row, "namedb", True, errors, prefix, "NAMEDB", unread=missing)
        for key, attribute, minimum in (("dbno", "DBNO", 1), ("fino", "FINO", 0), ("extno", "EXTNO", 0)):
            _nullable_number(row, key, minimum, errors, prefix + "." + attribute, unread=missing)
        state, owner = row["extract_owner_state"], row["extown"]
        _need(state in ("unknown", "none", "observed") and (owner is not None) == (state == "observed"), prefix + " EXTOWN state")
        if owner is not None:
            _identity(owner, types, record_type="DB")
        _need(state != "unknown" or missing or _has_error(errors, prefix + ".EXTOWN"), prefix + " EXTOWN error")
        kind = ("unknown" if state == "unknown" else "master_candidate" if owner is None else
                "extract_candidate" if not missing and owner["identity_key"] != row["identity"]["identity_key"] else "unknown")
        _need(row["extract_kind"] == kind, prefix + " typed extract kind")
        path, relation = row["owner_path"], row["team_relation"]
        _need(type(path) is list and len(path) <= 2 and (not missing or not path and state == "unknown"), prefix + " owner hop cap")
        for identity in path:
            _identity(identity, types)
        _fields(relation, {"status", "source", "owner_chain_supported", "observed_team", "listed_team_index",
                           "name_agreement", "confirmed_team"}, prefix + " Team relation")
        supported = relation["owner_chain_supported"]
        _need(type(supported) is bool, prefix + " owner chain flag")
        if supported:
            identity = row["identity"]
            _need(not missing and len(path) == 2 and [part["type"] for part in path] == ["DBLI", "TEAM"]
                  and len({part["identity_key"] for part in [identity] + path}) == 3
                  and all(part["db_type_code"] == identity["db_type_code"]
                          and part["refno"].split("/")[0] == identity["refno"].split("/")[0] for part in path),
                  prefix + " same-database typed DB->DBLI->TEAM chain")
        else:
            _need(missing or _has_error(errors, prefix + ".owner_path"), prefix + " unsupported owner error")
        collected.extend(errors)
    for index, row in enumerate(result["current_mdb"]):
        prefix, errors = _row_errors(row, {"index", "name", "canonical_name", "database_number", "extract_number",
                                          "is_extract", "identity_coherent", "read_errors"}, "current_mdb", index,
                                     {"", ".Name", ".canonical_name", ".Number", ".ExtractNumber", ".IsExtract"})
        missing = _has_error(errors, prefix)
        _name(row, "name", True, errors, prefix, "Name", unread=missing)
        for key, attribute, minimum in (("database_number", "Number", 1), ("extract_number", "ExtractNumber", 0)):
            _nullable_number(row, key, minimum, errors, prefix + "." + attribute, unread=missing)
        flag = row["is_extract"]
        _need(flag is None if missing else type(flag) is bool or flag is None and _has_error(errors, prefix + ".IsExtract"), prefix + " extract flag")
        # ExtractNumber and IsExtract are independent SDK observations. A real
        # primary can report ExtractNumber=1 and IsExtract=false; no numbering
        # rule or cross-object EXTNO equivalence has been established.
        _need(row["identity_coherent"] is None, prefix + " unknown extract-number semantics")
        collected.extend(errors)
    return collected


def _record_key(row: dict[str, Any]) -> tuple[Any, ...] | None:
    if row["canonical_name"] is not None and row["dbno"] is not None and row["extno"] is not None:
        return row["canonical_name"], row["dbno"], row["extno"]
    return None


def _mdb_key(row: dict[str, Any]) -> tuple[Any, ...] | None:
    if row["canonical_name"] is not None and row["database_number"] is not None and row["extract_number"] is not None:
        return row["canonical_name"], row["database_number"], row["extract_number"]
    return None


def _relations(result: dict[str, Any], prefixes: dict[str, bool], stable: bool) -> None:
    teams, records, mdb = (result[key] for key in _SOURCES)
    team_keys, mdb_keys = defaultdict(list), defaultdict(list)
    for row in teams:
        if row["identity"] is not None:
            team_keys[row["identity"]["identity_key"]].append(row)
    for row in mdb:
        if _mdb_key(row) is not None:
            mdb_keys[_mdb_key(row)].append(row["index"])
    record_keys = Counter(row["identity"]["identity_key"] for row in records if row["identity"] is not None)
    teams_complete = prefixes["teams"] and all(row["identity"] is not None and row["canonical_name"] is not None for row in teams)
    record_identities_complete = prefixes["databases"] and all(row["identity"] is not None for row in records)
    expected_findings = [("teams", "duplicate_typed_identity") for rows in team_keys.values() if len(rows) > 1]
    for row in records:
        prefix, relation, identity = f"databases[{row['index']}]", row["team_relation"], row["identity"]
        duplicate = identity is not None and record_keys[identity["identity_key"]] > 1
        if duplicate:
            expected_findings.append((prefix, "duplicate_typed_identity"))
        ancestor = row["owner_path"][1] if relation["owner_chain_supported"] else None
        listed = team_keys.get(ancestor["identity_key"], []) if ancestor is not None else []
        team = listed[0] if len(listed) == 1 else None
        agreement = None if team is None or team["canonical_name"] is None or row["canonical_name"] is None else row["canonical_name"].split("/")[0] == team["canonical_name"]
        status = ("context_unverified" if not stable else "ambiguous" if duplicate or len(listed) > 1 else
                  "conflict" if agreement is False else "confirmed" if teams_complete and record_identities_complete
                  and relation["owner_chain_supported"] and agreement is True else "unverified")
        _need(relation["status"] == status and relation["source"] == "typed DB.Owner(DBLI).Owner(TEAM)"
              and relation["observed_team"] == ancestor and relation["name_agreement"] is agreement
              and (relation["listed_team_index"] is None if team is None else
                   type(relation["listed_team_index"]) is int and relation["listed_team_index"] == team["index"])
              and relation["confirmed_team"] == (ancestor if status == "confirmed" else None), prefix + " Team resolution")
        if status in ("conflict", "ambiguous"):
            expected_findings.append((prefix + ".team_relation", status))
        key = _record_key(row)
        candidates = mdb_keys.get(key, []) if key is not None else []
        match = row["current_mdb"]
        _fields(match, {"status", "match_fields", "candidate_indexes", "matched_index", "semantic_equivalence_verified"}, prefix + " MDB relation")
        _need(type(match["candidate_indexes"]) is list and all(type(item) is int for item in match["candidate_indexes"])
              and match["candidate_indexes"] == candidates and match["match_fields"] == _MATCH_FIELDS
              and match["status"] == "indeterminate" and match["matched_index"] is None
              and match["semantic_equivalence_verified"] is False, prefix + " unverified raw MDB candidates")
    _need(Counter((item["path"], item["code"]) for item in result["identity_findings"]) == Counter(expected_findings), "identity finding coverage")


def validate_team_database_identity_manifest(reply: Any, args: dict[str, Any], *,
                                             expected_session: str, expected_build: str) -> dict[str, Any]:
    """Validate a captured public route envelope without performing any I/O.

    Raises ValueError on malformed evidence. BridgeError objects are re-raised
    unchanged except for non-fault 404s; nested fault envelopes become a
    BridgeError with the original envelope as diagnostics, before any downgrade.
    Expected session/build must come from independently captured provenance.
    """
    diagnostics = reply.diagnostics if isinstance(reply, BridgeError) else reply
    if _native_fault(diagnostics):
        if isinstance(reply, BridgeError):
            raise reply
        raise BridgeError("A native database fault stopped identity inspection; do not retry.", diagnostics=reply)
    payload = _parameters(args)
    if isinstance(reply, BridgeError):
        if reply.http_status == 404:
            return _unavailable(payload)
        raise reply
    _need(_text(expected_session) and type(expected_build) is str and bool(_BUILD.fullmatch(expected_build)), "independent session/build")
    _need(type(reply) is dict and reply.get("ok") is True and reply.get("version") == VERSION
          and reply.get("session_id") == expected_session and reply.get("bridge_build") == expected_build, "envelope session/build")
    result = reply.get("result")
    _fields(result, {"operation", "schema_version", "read_only", "single_dispatch", "atomic_snapshot", "source",
                     "limit_per_source", "owner_hop_limit", "scans", "teams", "databases", "current_mdb", "context",
                     "complete", "identity_consistent", "all_team_relations_confirmed", "success", "read_errors", "identity_findings", "scope"}, "manifest")
    _need(result["operation"] == OPERATION and type(result["schema_version"]) is int and result["schema_version"] == 1
          and result["read_only"] is True and result["single_dispatch"] is True and result["atomic_snapshot"] is False
          and result["source"] == "SDK DbSystem.GetAll(TEAM/DB) and current MDB.GetDBArray"
          and type(result["limit_per_source"]) is int and result["limit_per_source"] == payload["limit"]
          and type(result["owner_hop_limit"]) is int and result["owner_hop_limit"] == 2, "native operation and request limits")
    _fields(result["scope"], set(_SCOPE), "scope")
    _need(all(result["scope"][key] is value if type(value) is bool else result["scope"][key] == value
              for key, value in _SCOPE.items()), "bounded scope claims")
    errors = _errors(result["read_errors"], 16 * payload["limit"] + 32, "manifest errors")
    _errors(result["identity_findings"], 6 * payload["limit"], "identity findings")
    context = result["context"]
    _fields(context, {"before", "after", "boundary_agreement", "session_id", "bridge_build"}, "context")
    _need(context["session_id"] == expected_session and context["bridge_build"] == expected_build, "native session/build binding")
    types: dict[int, str] = {}
    _boundary(context["before"], expected_session, types, before=True)
    _boundary(context["after"], expected_session, types, before=False)
    _need(context["before"]["context_id"] == payload["expected_context"], "expected_context binding")
    stable = context["boundary_agreement"]
    _need(type(stable) is bool and (not stable or context["before"] == context["after"]
          and not any(item["path"].startswith("after.") for item in errors)), "context boundary agreement")
    _need(stable or any(item["path"] == "context" and item["code"] == "context_changed_or_unreadable" for item in errors), "unverified context error")
    _fields(result["scans"], set(_SOURCES), "scans")
    prefixes = {source: _scan(result["scans"][source], result[source], payload["limit"], source) for source in _SOURCES}
    for source in _SOURCES:
        _need(result["scans"][source]["available"] or any(item["path"] == source + ".array" and item["code"] == "array_unavailable" for item in errors), source + " unavailable error")
    row_errors = _rows(result, types)
    error_key = lambda item: (item["path"], item["code"], item["message"])
    scoped = [item for item in errors if any(item["path"].startswith(source + "[") for source in _SOURCES)]
    _need(Counter(map(error_key, scoped)) == Counter(map(error_key, row_errors)), "row/global read error agreement")
    remaining = {"system", "teams.array", "databases.array", "current_mdb.array", "context", "after.project", "after.mdb",
                 "after.project.name", "after.mdb.name", "after.context", "after.ce"}
    _need(all(item in scoped or item["path"] in remaining for item in errors), "global read error paths")
    _relations(result, prefixes, stable)
    complete = stable and all(prefixes.values()) and not errors
    consistent = not result["identity_findings"]
    confirmed = all(row["team_relation"]["status"] == "confirmed" for row in result["databases"])
    _need(result["complete"] is complete and result["identity_consistent"] is consistent
          and result["all_team_relations_confirmed"] is confirmed
          and result["success"] is (complete and consistent and confirmed), "native completion flags")
    positive = []
    for row in result["databases"]:
        trusted = complete and consistent and not row["read_errors"] and row["team_relation"]["status"] == "confirmed"
        matched = trusted and row["current_mdb"]["status"] == "matched"
        owner_candidate_observed = trusted and row["extract_kind"] in ("master_candidate", "extract_candidate")
        positive.append({
            "index": row["index"], "team": True if trusted else None,
            "team_identity": row["team_relation"]["confirmed_team"] if trusted else None,
            "current_mdb": True if matched else None,
            "current_mdb_index": row["current_mdb"]["matched_index"] if matched else None,
            # EXTOWN supports only a candidate observation. It does not verify
            # master/extract classification or native extract-number semantics.
            "extract_identity": None,
            "extract_kind": row["extract_kind"] if owner_candidate_observed else "unknown",
            "extract_owner": row["extown"] if owner_candidate_observed else None,
        })
    return {"operation": OPERATION, "status": "observed" if result["success"] else "partial", "available": True,
            "success": result["success"], "complete": complete, "read_only": True, "atomic_snapshot": False,
            "project_coverage_verified": False, "positive_matches": positive, "native_observation": result}
