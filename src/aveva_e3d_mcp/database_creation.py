"""Read-only CREATE DB candidates and bounded current-MDB conflict evidence.

No command dispatch, Admin initialization or database creation belongs here.
"""
from __future__ import annotations

import base64
import hashlib
import re
from typing import Any

from .bridge import BridgeClient, BridgeError
from .database_catalog_probe import observe_project_catalog
from .protocol import McpServer

MAX_DATABASES = 10000
MAX_MATCHES = 64
TOKEN_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,31}$"
DATABASE_TYPES = ("DESI", "CATA")
TOOL = "e3d_database_preflight"


def register_database_preflight(server: McpServer, bridge: BridgeClient) -> None:
    server.add_tool(
        TOOL,
        "Build a non-executable E3D21 CREATE DB candidate for DESI or CATA. Admin creates CATA databases for PARAGON; "
        "this tool does not specify or create a CATA root element. Inspect native readiness, project context and "
        "name/DBNO conflicts in the current MDB through read-only SDK-backed endpoints. UPDATE and MULTIWRITE are "
        "the installed Admin form's access choices; MULTIWRITE explicitly uses CLAIM IMPLICIT. CONTROLLED is a "
        "separate database flag and is not accepted as access. Complete means a bounded single-dispatch current-MDB "
        "coverage with matching boundary observations. The identity array is read in one native UI callback with a "
        "10000-entry limit. These reads do not lock the database or verify project-wide uniqueness, Admin permission "
        "or PARAGON write access. execution_supported, creation_attempted and created are always false. "
        "Does not execute PML/CMD, initialize Admin, write, save work or change CE/view. "
        "Mock mode and versions other than E3D21 report unavailable without native calls. "
        "Opt in with include_project_catalog to look for additional positive matches in the visible SDK catalog. "
        "A missing match does not prove project-wide absence; complete still describes the current MDB check.",
        {"type": "object", "properties": {
            "team": {"type": "string", "pattern": TOKEN_PATTERN, "maxLength": 32,
                     "description": "Single ASCII team component; normalized to uppercase. Team existence is not verified."},
            "name": {"type": "string", "pattern": TOKEN_PATTERN, "maxLength": 32,
                     "description": "Single ASCII database name component; normalized to uppercase. The 32-character cap is tool policy."},
            "database_type": {"type": "string", "enum": list(DATABASE_TYPES),
                              "description": "DESI is Design; CATA is the Catalogue database type. "
                                             "The candidate has no initial element or WITH clause."},
            "access": {"type": "string", "enum": ["UPDATE", "MULTIWRITE"]},
            "database_number": {"type": "integer", "minimum": 1, "maximum": 255000},
            "area": {"type": "integer", "minimum": 0, "maximum": 999,
                     "description": "Zero/omitted uses the system area. Other areas are candidates only; directory availability is unverified."},
            "include_project_catalog": {"type": "boolean", "default": False,
                                        "description": "Try a separate read-only SDK catalog probe for positive DB and team matches. Requires a supporting E3D21 Addin."},
        }, "required": ["team", "name", "database_type", "access"], "additionalProperties": False},
        lambda args: database_preflight(bridge, args), read_only=True,
    )


def build_database_candidate(args: dict[str, Any]) -> dict[str, Any]:
    if type(args) is not dict or set(args) - {"team", "name", "database_type", "access", "database_number", "area"}:
        raise ValueError("Only team, name, database_type, access, database_number and area are accepted")
    normalized: dict[str, Any] = {}
    for field in ("team", "name"):
        value = args.get(field)
        if type(value) is not str or not re.fullmatch(TOKEN_PATTERN, value, re.ASCII) or value.upper() == "UNSET":
            raise ValueError(field + " must be one ASCII letter-led component of at most 32 letters, digits, underscores or hyphens")
        normalized[field] = value.upper()
    if normalized["team"] == "TRANSACTION":
        raise ValueError("The installed Admin form reserves the TRANSACTION team for TRAN databases")
    database_type = args.get("database_type")
    if type(database_type) is not str or database_type not in DATABASE_TYPES:
        raise ValueError("database_type must be DESI or CATA")
    if args.get("access") not in ("UPDATE", "MULTIWRITE"):
        raise ValueError("access must be UPDATE or MULTIWRITE; CONTROLLED is a separate database flag")
    normalized.update(database_type=database_type, access=args["access"])
    for key, low, high in (("database_number", 1, 255000), ("area", 0, 999)):
        if key in args:
            value = args[key]
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{key} must be an integer from {low} through {high}")
            normalized[key] = value
    full_name = normalized["team"] + "/" + normalized["name"]
    parts = ["CREATE DB", full_name, normalized["database_type"]]
    if normalized.get("area", 0):
        parts += ["IN AREA", str(normalized["area"])]
    parts += ["ACCESS", normalized["access"]]
    if normalized["access"] == "MULTIWRITE":
        parts += ["CLAIM IMPLICIT"]
    if "database_number" in normalized:
        parts += ["DBNO", str(normalized["database_number"])]
    number = normalized.get("database_number", 0)
    return {
        "normalized_input": normalized, "full_database_name": full_name, "candidate_command": " ".join(parts),
        "command_source": "Installed E3D21 PMLLIB/admin/forms/admdb.pmlfrm cbApply CREATE branch",
        "native_syntax_validated": False,
        "claim_policy": "explicit_tool_policy_implicit" if normalized["access"] == "MULTIWRITE" else "not_applicable",
        "warnings": (["Installed Admin form requests confirmation for this reserved-range DBNO; no confirmation or native validation was performed."]
                     if 7000 < number < 8000 or 250000 < number < 255000 else []),
    }


class _InvalidObservation(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise _InvalidObservation(message)


def _text(value: Any, maximum: int = 512) -> bool:
    return type(value) is str and 0 < len(value) <= maximum and bool(value.strip()) and not any(ord(c) < 32 for c in value)


def _int(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _health(reply: Any) -> tuple[dict[str, Any], bool | None]:
    _require(type(reply) is dict and reply.get("ok") is True and reply.get("version") == "E3D21", "Invalid E3D21 health envelope")
    state = reply.get("state")
    _require(type(state) is dict, "Native readiness state is unavailable")
    summary = {key: reply[key] if _text(reply.get(key), 128) else None for key in ("session_id", "bridge_build")}
    summary.update({key: state[key] if type(state.get(key)) is bool else None for key in ("database_ready", "busy", "recovery_required")})
    summary["connected"] = reply.get("connected") if type(reply.get("connected")) is bool else None
    ready = (summary["connected"] is True and summary["database_ready"] is True
             and summary["busy"] is False and summary["recovery_required"] is False)
    if any(summary[key] is None for key in summary):
        ready = None
    fault = state.get("native_database_fault")
    quarantine = state.get("generated_csg_quarantine")
    if (type(fault) is dict and fault.get("has_fault") is True
            or type(quarantine) is dict and quarantine.get("active") is True):
        ready = False
    summary["ready"] = ready
    return summary, ready


def _project(reply: Any) -> dict[str, str]:
    _require(type(reply) is dict and reply.get("ok") is True and reply.get("version", "E3D21") == "E3D21", "Invalid project envelope")
    value = reply.get("project")
    _require(type(value) is dict and value.get("connected") is True and value.get("recovery_only") is False
             and value.get("busy") is not True, "Fresh project/MDB observation is unavailable")
    _require(all(_text(value.get(key)) for key in ("name", "mdb", "current_element")), "Incomplete project/MDB/CE identity")
    return {key: value[key] for key in ("name", "mdb", "current_element")}


def _database_preflight_identity_snapshot(bridge: BridgeClient, candidate: dict[str, Any],
                                          version: str, result: dict[str, Any], *, include_project_catalog: bool) -> dict[str, Any]:
    errors = result["read_errors"]

    def error(stage: str, exc: Any) -> None:
        if len(errors) < 128:
            errors.append({"stage": stage, "message": str(exc)[:2048]})

    if include_project_catalog:
        result["project_catalog"] = {"status": "not_attempted", "reason": "current_mdb_snapshot_unverified"}
    if bridge.settings.mode != "bridge" or version != "E3D21":
        error("availability", "native_unavailable_in_mock" if bridge.settings.mode != "bridge" else "unsupported_version")
        return {"ok": True, "version": version, "result": result}

    before = project_before = None
    snapshot_complete = False
    context_valid = True
    try:
        before, ready = _health(bridge.health())
        result["native_readiness"]["before"] = before
        result["native_readiness"]["ready"] = ready
        _require(ready is True, "Native database is not explicitly ready and idle; no database reads were issued")
        project_before = _project(bridge.project())
        result["context"]["before"] = {**project_before, "session_id": before["session_id"], "bridge_build": before["bridge_build"]}
        expected_context = hashlib.sha256((before["session_id"] + "\n" + project_before["name"] + "\n" + project_before["mdb"]).encode("utf-8")).hexdigest()
        result["context"]["context_id"] = expected_context
        from .database_tools import database_identity_snapshot
        reply = database_identity_snapshot(bridge, expected_context)
        _require(type(reply) is dict and reply.get("ok") is True and reply.get("version") == "E3D21",
                 "Invalid database identity snapshot envelope")
        snapshot = reply.get("result")
        _require(type(snapshot) is dict, "Missing database identity snapshot result")
        result["snapshot"] = {key: snapshot.get(key) for key in
                               ("source", "consistency", "atomic_snapshot", "context_id", "total_count",
                                "returned_count", "identity_snapshot_sha256", "complete")}
        result["identity_snapshot_sha256"] = snapshot.get("identity_snapshot_sha256")
        if not (snapshot.get("session_id") == before["session_id"] and snapshot.get("project") == project_before["name"]
                and snapshot.get("mdb") == project_before["mdb"] and snapshot.get("context_id") == expected_context
                and snapshot.get("ce_before") == snapshot.get("ce_after") == project_before["current_element"]
                and snapshot.get("ce_unchanged") is True and snapshot.get("identity_unchanged") is True):
            context_valid = False
            raise _InvalidObservation("Database identity snapshot session/project/MDB/CE context changed or is unverified")
        rows = snapshot.get("databases")
        _require(type(rows) is list and len(rows) <= MAX_DATABASES, "Invalid database identity snapshot rows")
        result["available"] = snapshot.get("available") is True
        result["total_count"] = snapshot.get("total_count")
        result["returned_count"] = len(rows)
        seen = set()
        canonical = []
        for index, row in enumerate(rows):
            if not (type(row) is dict and type(row.get("index")) is int and row["index"] == index
                    and row.get("available") is True and _text(row.get("name"))
                    and _int(row.get("number"), 1, 2147483647)
                    and _int(row.get("extract_number"), 0, 2147483647)):
                raise _InvalidObservation("Incomplete or malformed database identity at index " + str(index))
            identity = (row["number"], row["extract_number"])
            if identity in seen:
                raise _InvalidObservation("Repeated database identity in one native snapshot")
            seen.add(identity)
            encoded_name = base64.b64encode(row["name"].encode("utf-8")).decode("ascii")
            canonical.append(f"{index}|{encoded_name}|{row['number']}|{row['extract_number']}\n")
            observed = {key: row[key] for key in ("index", "name", "number", "extract_number")}
            for field, match in (("name", row["name"].casefold() == candidate["full_database_name"].casefold()),
                                 ("database_number", row["number"] == candidate.get("normalized_input", {}).get("database_number"))):
                if match:
                    conflicts = result["conflicts"]
                    conflicts[field + "_match_count"] += 1
                    matches = conflicts[field + "_matches"]
                    if len(matches) < MAX_MATCHES:
                        matches.append(observed)
                    else:
                        conflicts["matches_truncated"] = True
        expected_fingerprint = hashlib.sha256("".join(canonical).encode("ascii")).hexdigest()
        native_errors = snapshot.get("read_errors")
        if type(native_errors) is list:
            for item in native_errors[:32]:
                error("native_snapshot", item)
        _require(_int(snapshot.get("total_count"), 0, MAX_DATABASES)
                 and snapshot["total_count"] == len(rows)
                 and snapshot.get("returned_count") == len(rows)
                 and snapshot.get("available") is True
                 and snapshot.get("success") is True and snapshot.get("complete") is True
                 and native_errors == []
                 and type(snapshot.get("identity_snapshot_sha256")) is str
                 and snapshot["identity_snapshot_sha256"] == expected_fingerprint,
                 "Database identity snapshot is incomplete or contains native read errors")
        snapshot_complete = True
    except (BridgeError, _InvalidObservation, ValueError, TypeError, KeyError) as exc:
        context_valid = False if isinstance(exc, _InvalidObservation) and "context" in str(exc).lower() else context_valid
        error("snapshot", exc)
    if snapshot_complete and include_project_catalog:
        result["project_catalog"] = observe_project_catalog(bridge, candidate, result["context"])
    if before is not None and project_before is not None:
        try:
            after, ready = _health(bridge.health())
            result["native_readiness"]["after"] = after
            result["native_readiness"]["ready"] = ready
            _require(ready is True, "Native readiness changed or is unverified after database identity scan")
            project_after = _project(bridge.project())
            result["context"]["after"] = {**project_after, "session_id": after["session_id"], "bridge_build": after["bridge_build"]}
            unchanged = before["session_id"] == after["session_id"] and before["bridge_build"] == after["bridge_build"] and project_before == project_after
            result["context"]["unchanged"] = unchanged and context_valid
            _require(unchanged and context_valid, "Boundary session/build/project/MDB/CE observations changed")
        except (BridgeError, _InvalidObservation, ValueError, TypeError, KeyError) as exc:
            error("readback", exc)
    result["snapshot_complete"] = snapshot_complete
    result["page_complete"] = snapshot_complete
    result["complete"] = snapshot_complete and result["context"]["unchanged"] is True and not errors
    result["success"] = result["complete"]
    if include_project_catalog and snapshot_complete:
        if result["context"]["unchanged"] is not True:
            result["project_catalog"] = {"status": "unverified", "reason": "boundary_context_unverified"}
        else:
            catalog = result["project_catalog"]
            if catalog["status"] in {"observed", "partial"}:
                blockers = result["blockers"]
                blockers[blockers.index("project_catalog_unavailable")] = "project_catalog_coverage_unverified"
                if catalog["positive_matches"]["team"] is True:
                    result["team_exists"] = True
                    blockers.remove("team_existence_unverified")
                for field in ("name", "database_number"):
                    if catalog["positive_matches"][field] is True:
                        result["project_uniqueness"][field] = False
    if result["context"]["unchanged"] is True:
        for field in ("name", "database_number"):
            if field == "database_number" and "database_number" not in candidate.get("normalized_input", {}):
                continue
            count = result["conflicts"][field + "_match_count"]
            result["conflicts"][field + "_in_current_mdb"] = True if count else False if result["complete"] else None
    return {"ok": True, "version": version, "result": result}


def database_preflight(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    if type(args) is not dict or type(args.get("include_project_catalog", False)) is not bool:
        raise ValueError("include_project_catalog must be a boolean")
    include_project_catalog = args.get("include_project_catalog", False)
    candidate = build_database_candidate({key: value for key, value in args.items() if key != "include_project_catalog"})
    version = bridge._selected_version(None)
    result: dict[str, Any] = {
        "operation": "database_preflight", **candidate,
        "success": False, "available": False, "complete": False, "page_complete": False,
        "execution_supported": False, "creation_attempted": False, "created": False,
        "scope": "current_mdb_only", "atomic_snapshot": False,
        "consistency": "sequential_context_observations_single_dispatch_identity_scan_not_a_database_lock",
        "native_readiness": {"before": None, "after": None, "ready": None},
        "context": {"before": None, "after": None, "context_id": None, "unchanged": None},
        "total_count": None, "returned_count": 0, "pages": [], "snapshot": None,
        "identity_snapshot_sha256": None, "snapshot_complete": False,
        "conflicts": {"name_in_current_mdb": None, "database_number_in_current_mdb": None,
                      "name_matches": [], "database_number_matches": [], "name_match_count": 0,
                      "database_number_match_count": 0, "matches_truncated": False},
        "project_uniqueness": {"name": None, "database_number": None},
        "team_exists": None, "area_available": None, "admin_context_verified": None,
        "mcp_commands_enabled": bridge.settings.allow_commands,
        "blockers": ["execution_not_supported", "admin_context_unverified", "project_catalog_unavailable",
                     "team_existence_unverified", "area_availability_unverified"],
        "read_errors": [],
    }
    return _database_preflight_identity_snapshot(bridge, candidate, version, result,
                                                 include_project_catalog=include_project_catalog)
