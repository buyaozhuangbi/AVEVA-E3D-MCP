"""E3D 2.1 database observations; registration is explicit per server instance."""
from __future__ import annotations

import re
from typing import Any

from .bridge import BridgeClient
from .protocol import McpServer


def register_database_tools(server: McpServer, bridge: BridgeClient) -> None:
    server.add_tool(
        "e3d_database_state",
        "Read the current E3D 2.1 MDB database list, native read-only/primary flags, changed-session flags and current/latest session identifiers. Limits bound returned details; the SDK still materializes the complete database array. Arrays over 10000 databases fail before detail reads. Pagination is a live observation in MDB order and can change between calls; provide the returned context_id as expected_context after the first page to bind the bridge session/project/MDB. page_complete describes requested fields, while complete requires all databases in a single successful response. Native database flags do not guarantee permission for a particular element edit or prove SAVEWORK is safe. Does not save/get work, change CE or execute PML.",
        {"type": "object", "properties": {
            "offset": {"type": "integer", "minimum": 0, "maximum": 100000, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            "expected_context": {"type": "string", "pattern": "^(?:[a-f0-9]{64})?$", "default": "",
                                 "description": "context_id from the first page; required with offset > 0."},
        }, "additionalProperties": False},
        lambda args: database_state(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_database_identity_snapshot",
        "Read the current E3D21 MDB database identity list in one bounded native UI callback. Returns ordered name, database number and extract identity fields plus a SHA-256 fingerprint of those fields. The scan is limited to 10000 entries and is not a database lock or project-wide catalog. Supply the context_id from e3d_database_state to bind the request to the observed E3D session, project and MDB. Does not read sessions/access flags, initialize Admin, execute PML/CMD, write or save work.",
        {"type": "object", "properties": {
            "expected_context": {"type": "string", "pattern": "^[a-f0-9]{64}$",
                                 "description": "context_id returned by e3d_database_state for the current E3D session/project/MDB."},
        }, "required": ["expected_context"], "additionalProperties": False},
        lambda args: database_identity_snapshot(bridge, args["expected_context"]), read_only=True,
    )
    from .database_creation import register_database_preflight
    register_database_preflight(server, bridge)


def database_state(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"offset", "limit", "expected_context"}
    if unknown:
        raise ValueError(f"Unknown e3d_database_state arguments: {', '.join(sorted(unknown))}")
    payload: dict[str, Any] = {}
    for name, default, minimum, maximum in (("offset", 0, 0, 100000), ("limit", 50, 1, 200)):
        value = args.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
        payload[name] = value
    context = args.get("expected_context", "")
    if not isinstance(context, str) or (context and not re.fullmatch(r"[a-f0-9]{64}", context)):
        raise ValueError("expected_context must be a 64-character lowercase hexadecimal context_id")
    if payload["offset"] > 0 and not context:
        raise ValueError("expected_context is required when offset is greater than zero")
    payload["expected_context"] = context
    version = bridge._selected_version(None)
    if version != "E3D21" or bridge.settings.mode == "mock":
        code = "unsupported_version" if version != "E3D21" else "native_unavailable"
        message = ("Database state is implemented only for E3D21." if version != "E3D21"
                   else "Native database state is unavailable in mock mode.")
        return {"ok": True, "version": version, "result": {
            "success": False, "available": False, "complete": False, "page_complete": False,
            "source": "mock" if bridge.settings.mode == "mock" else "unsupported",
            "databases": [], "total_count": None, "returned_count": 0, "has_more": None,
            "next_offset": None, "read_errors": [
                {"path": "database_state", "code": code, "message": message}],
        }}
    return bridge._request("GET", "/api/v1/database/state", query={key: str(value) for key, value in payload.items()}, version=version)


def database_identity_snapshot(bridge: BridgeClient, expected_context: str) -> dict[str, Any]:
    if not isinstance(expected_context, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_context):
        raise ValueError("expected_context must be a 64-character lowercase hexadecimal context_id")
    return bridge.database_identity_snapshot(expected_context)
