"""Bounded BRAN observations using the existing E3D21 readers."""
from __future__ import annotations

import math
import re
from typing import Any

from .bridge import BridgeClient, BridgeError, element_access_refnos
from .context_snapshot import context_snapshot
from .protocol import McpServer


TOOL = "e3d_piping_branch_state"
VERSION = "E3D21"
MAX_MEMBERS = 200
ATTRIBUTES = (
    "HREF", "TREF", "HPOS", "TPOS", "HDIR", "TDIR", "HBOR", "TBOR",
    "HCON", "TCON", "LHEAD", "LTAIL", "PSPEC", "ISPEC", "TSPEC", "PRES", "TEMP", "SLOREF",
)


def _numeric_ref(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"=[0-9]{1,10}/[0-9]{1,10}", value):
        db, element = map(int, value[1:].split("/"))
        if 0 < db <= 2147483647 and 0 <= element <= 2147483647:
            return f"={db}/{element}"
    return None


def _boundary(bridge: BridgeClient) -> dict[str, str]:
    sample = context_snapshot(bridge, bridge.settings)["result"]
    if sample["context_complete"] is not True:
        details = {"readiness": sample["readiness"],
                   "changed_identity_fields": sample["session_coherence"]["changed_fields"],
                   "project_issues": sample["context"]["issues"],
                   "errors": sample["errors"]}
        raise ValueError("E3D context unverified: " + str(details)[:1600])
    observed = sample["context"]
    if not observed["current_element"]:
        raise ValueError("Current element identity is unverified")
    return {**sample["session_coherence"]["after"], **{
        key: observed[key] for key in ("project", "mdb", "current_element")}}


def _native_result(reply: Any) -> dict:
    if (not isinstance(reply, dict) or reply.get("ok") is not True or reply.get("version") != VERSION
            or not isinstance(reply.get("result"), dict)):
        raise ValueError("Invalid E3D21 response envelope")
    return reply["result"]


def _members(listing: dict, requested: str, limit: int, result: dict) -> str:
    parent = listing.get("parent")
    refno = _numeric_ref(parent.get("refno")) if isinstance(parent, dict) else None
    name = parent.get("name") if isinstance(parent, dict) else None
    matches = (refno == requested if requested.startswith("=") else
               isinstance(name, str) and name.casefold() == requested.casefold())
    if refno is None or parent.get("type") != "BRAN" or not matches:
        raise ValueError("Native member list did not resolve the requested BRAN")
    result["element"] = {"refno": refno, "type": "BRAN", "name": name if isinstance(name, str) else None}
    result["available"] = True
    rows = listing.get("members")
    count = listing.get("returned_count")
    more = listing.get("has_more")
    cursor = listing.get("next_cursor")
    if not (isinstance(rows, list) and type(count) is int and count == len(rows) <= limit
            and type(listing.get("limit")) is int and listing["limit"] == limit
            and listing.get("consistency") == "live"
            and isinstance(listing.get("read_errors"), list)):
        raise ValueError("Malformed native member page")
    members = []
    seen = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError("Malformed native member identity")
        member_refno = _numeric_ref(row.get("refno"))
        kind = row.get("type")
        ordinal = row.get("sibling_index")
        if (member_refno is None or not isinstance(kind, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", kind)
                or type(ordinal) is not int or ordinal != index):
            raise ValueError("Invalid or out-of-order native member identity")
        if (member_refno, kind) in seen:
            raise ValueError("Repeated native member reference and type")
        seen.add((member_refno, kind))
        members.append({"refno": member_refno, "type": kind, "sibling_index": ordinal,
                        "name": row.get("name") if isinstance(row.get("name"), str) else None,
                        "identity_key": f"{member_refno}|{kind}|{ordinal}"})
    result["members"] = members
    result["member_listing"] = {"limit": limit, "returned_count": count, "has_more": more if type(more) is bool else None}
    if listing.get("success") is not True or listing["read_errors"] or more is None:
        raise ValueError("Native member read failed: " + str(listing["read_errors"])[:1024])
    if more is True and count == limit and isinstance(cursor, str) and cursor:
        result["read_errors"].append({"stage": "members", "message": "Direct member limit reached"})
    elif more is not False or cursor is not None:
        raise ValueError("Inconsistent native member continuation")
    return refno


def _attributes(payload: dict, refno: str, result: dict) -> None:
    if _numeric_ref(payload.get("refno")) != refno or payload.get("type") != "BRAN":
        raise ValueError("Native attribute identity does not match the resolved BRAN")
    attrs = result["attributes"]
    for key in ("values", "typed_values", "errors", "typed_read_errors"):
        source = payload.get(key)
        if not isinstance(source, dict):
            raise ValueError(f"Malformed native attribute map: {key}")
        for name, value in source.items():
            valid = (isinstance(value, str) or type(value) in {bool, int}
                     or type(value) is float and math.isfinite(value)) if key == "typed_values" else isinstance(value, str)
            if name not in ATTRIBUTES or not valid:
                raise ValueError(f"Malformed native attribute entry in {key}")
            attrs[key][name] = value
    if (payload.get("success") is not True or attrs["errors"] or attrs["typed_read_errors"]
            or set(attrs["values"]) != set(ATTRIBUTES)):
        raise ValueError("Native BRAN attribute read is incomplete; inspect the attribute maps")


def piping_branch_state(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict) or set(args) - {"refno", "member_limit"}:
        raise ValueError("Only refno and member_limit are accepted")
    requested = element_access_refnos([args.get("refno")])[0]
    refno = _numeric_ref(requested) if requested.startswith("=") else requested
    if len(requested) > 512 or not refno or refno == "/*":
        raise ValueError("refno must identify one explicit BRAN")
    limit = args.get("member_limit", 64)
    if type(limit) is not int or not 1 <= limit <= MAX_MEMBERS:
        raise ValueError(f"member_limit must be an integer between 1 and {MAX_MEMBERS}")

    version = bridge._selected_version(None)
    context = {"before": None, "after": None, "boundary_agreement": None}
    result: dict[str, Any] = {
        "operation": "piping_branch_state", "success": False, "available": False,
        "complete": False, "read_only": True, "atomic_snapshot": False,
        "consistency": "sequential_live_reads", "refno": requested, "element": None,
        "attributes": {"requested": list(ATTRIBUTES), "values": {}, "typed_values": {},
                        "errors": {}, "typed_read_errors": {}},
        "members": [], "member_listing": None, "context": context, "read_errors": [],
        "scope": "Native formatted attributes in the SDK's units/frame; no reference traversal, "
                 "PPLS/PCREF endpoint inference, coordinate conversion, or engineering verdict.",
    }
    reply = {"ok": True, "version": version, "result": result}

    def error(stage: str, message: Any) -> None:
        result["read_errors"].append({"stage": stage, "message": str(message)[:2048]})

    if version != VERSION or bridge.settings.mode != "bridge":
        error("availability", "Native piping branch state requires E3D21 bridge mode")
        return reply
    try:
        context["before"] = _boundary(bridge)
    except (BridgeError, ValueError) as exc:
        error("context_before", exc)
        return reply

    stage = "members"
    try:
        listing = _native_result(bridge.list_members(refno, "", limit, expected_type="BRAN"))
        resolved = _members(listing, refno, limit, result)
        stage = "attributes"
        _attributes(_native_result(bridge.read_attributes(resolved, list(ATTRIBUTES), expected_type="BRAN")), resolved, result)
    except (BridgeError, ValueError) as exc:
        error(stage, exc)

    try:
        context["after"] = _boundary(bridge)
        context["boundary_agreement"] = context["before"] == context["after"]
        if not context["boundary_agreement"]:
            error("context_after", "Session, build, project, MDB or current element changed during reads")
    except (BridgeError, ValueError) as exc:
        error("context_after", exc)
    result["complete"] = result["success"] = context["boundary_agreement"] is True and not result["read_errors"]
    return reply


def register_branch_tools(server: McpServer, bridge: BridgeClient) -> None:
    server.add_tool(
        TOOL,
        "Read one explicit E3D21 BRAN's native head/tail attributes, specifications, service attributes "
        "and bounded direct member identities in database order. Keeps SDK formatted text and native typed "
        "scalars when present; does not convert frames or units. Member identities contain refno, type and "
        "sibling_index to distinguish implicit TUBI from an anchor sharing its DBREF. Inspect complete, "
        "read_errors and context.boundary_agreement. Separate native reads with matching boundary observations "
        "are not an atomic snapshot. Does not run PML, infer endpoints from PPLS/PCREF, validate engineering "
        "compatibility or change the model, current element or view.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "minLength": 1, "maxLength": 512,
                      "description": "Explicit BRAN /NAME or =database/element."},
            "member_limit": {"type": "integer", "minimum": 1, "maximum": MAX_MEMBERS, "default": 64,
                             "description": "Maximum direct members to return. Truncation makes the result incomplete."},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: piping_branch_state(bridge, args), read_only=True,
    )
