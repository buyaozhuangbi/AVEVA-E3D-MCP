"""Validate positive-only evidence from the optional E3D21 catalog probe."""
from __future__ import annotations

from typing import Any

from .bridge import BridgeClient, BridgeError

PATH = "/api/v1/database/project-catalog-probe"
MAX_MATCHES = 64
MAX_CATALOG_ENTRIES = 10000


def _check(condition: bool, field: str) -> None:
    if not condition:
        raise ValueError(f"Invalid native project catalog evidence: {field}")


def _text(value: Any, maximum: int) -> bool:
    return (type(value) is str and 0 < len(value) <= maximum and bool(value.strip())
            and not any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value))


def _count(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= 2147483647


def _validate_match_rows(result: dict[str, Any], payload: dict[str, Any], scope: str) -> dict[str, bool | None]:
    """Return positive identity evidence from a bounded match collection.

    An empty collection remains unknown. The reader must never turn bounded
    visibility into a project-wide absence claim.
    """
    team = payload["team"]
    name = f"{team}/{payload['name']}"
    number = payload.get("database_number")
    matches = result.get("database_matches")
    teams = result.get("team_matches")
    _check(type(matches) is list and len(matches) <= MAX_MATCHES
           and type(teams) is list and len(teams) <= MAX_MATCHES, f"{scope} match rows")
    _check(all(team_name == team for team_name in teams), f"{scope} team matches")
    for key, values in (("database", matches), ("team", teams)):
        count = result.get(key + "_match_count")
        _check((count is None and not values or _count(count) and len(values) == min(count, MAX_MATCHES))
               and result.get(key + "_matches_truncated") is (_count(count) and count > MAX_MATCHES),
               f"{scope} {key} match count")

    positive: dict[str, bool | None] = {"name": None, "database_number": None,
                                        "team": True if teams else None}
    for match in matches:
        _check(type(match) is dict and "name" in match and "database_number" in match
               and (match["name"] is None or _text(match["name"], 512))
               and (match["database_number"] is None or _count(match["database_number"], 1)),
               f"{scope} database match identity")
        name_match = None if match["name"] is None else match["name"].isascii() and match["name"].upper() == name
        number_match = None if number is None or match["database_number"] is None else match["database_number"] == number
        _check((name_match is True or number_match is True) and match.get("name_match") is name_match
               and match.get("database_number_match") is number_match
               and match.get("current_mdb_match") in {"name_and_database_number", "no_name_or_number_match", "indeterminate"},
               f"{scope} database match evidence")
        _check(match["current_mdb_match"] == "indeterminate" or match["name"] is not None
               and match["database_number"] is not None, f"{scope} current MDB comparison identity")
        if name_match:
            positive["name"] = True
        if number_match:
            positive["database_number"] = True
    return positive


def _validate_world_level(result: Any, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Validate optional GetAllWorldLevel evidence without Admin claims."""
    if result is None:
        return {"status": "unavailable", "reason": "native_feature_not_returned",
                "positive_matches": {"name": None, "database_number": None, "team": None}}
    try:
        _check(type(result) is dict, "world_level envelope")
        _check(result.get("source") == "E3D21 SDK DbSystem.GetAllWorldLevel(DB/TEAM)", "world_level source")
        _check(result.get("visibility") == "world_level", "world_level visibility")
        _check(type(result.get("visibility_scope")) is str and "Admin permission" in result["visibility_scope"],
               "world_level scope")
        _check(result.get("admin_permission_verified") is False
               and result.get("area_availability_verified") is False
               and result.get("project_coverage_verified") is False, "world_level claims")
        _check(result.get("single_dispatch") is True and result.get("atomic_snapshot") is False,
               "world_level consistency")
        _check(type(result.get("success")) is bool and type(result.get("available")) is bool
               and (not result["success"] or result["available"]), "world_level availability")
        before = context["before"]
        _check(result.get("session_id") == before["session_id"]
               and result.get("project") == before["name"]
               and result.get("mdb") == before["mdb"]
               and result.get("context_id") == payload["expected_context"]
               and result.get("expected_context") == payload["expected_context"]
               and result.get("ce_before") == before["current_element"]
               and result.get("ce_after") == before["current_element"]
               and result.get("ce_unchanged") is True
               and result.get("identity_unchanged") is True, "world_level context")
        candidate = result.get("candidate")
        _check(type(candidate) is dict and candidate.get("team") == payload["team"]
               and candidate.get("name") == payload["name"]
               and candidate.get("full_database_name") == f"{payload['team']}/{payload['name']}"
               and candidate.get("database_number") == payload.get("database_number")
               and type(candidate.get("database_number")) is type(payload.get("database_number")),
               "world_level candidate")
        counts = result.get("counts")
        _check(type(counts) is dict and all(key in counts for key in (
            "world_database_count", "world_database_inspected_count",
            "world_team_count", "world_team_inspected_count"))
               and all(value is None or _count(value) for value in counts.values()), "world_level counts")
        positive = _validate_match_rows(result, payload, "world_level")
        errors = result.get("read_errors")
        _check(type(errors) is list and len(errors) <= 128 and all(type(item) is dict for item in errors),
               "world_level read errors")
        status = result.get("status")
        expected_status = "observed" if result["success"] else "partial" if result["available"] else "unavailable"
        _check(status == expected_status, "world_level status")
        for kind in ("database", "team"):
            total = counts[f"world_{kind}_count"]
            inspected = counts[f"world_{kind}_inspected_count"]
            matched = result[kind + "_match_count"]
            if result["available"]:
                _check(_count(total) and total <= MAX_CATALOG_ENTRIES and _count(inspected)
                       and inspected <= total and _count(matched) and matched <= inspected,
                       f"world_level {kind} inspection counts")
                if result["success"]:
                    _check(inspected == total, f"world_level {kind} inspection completeness")
            else:
                _check(total is None and inspected is None and matched is None
                       and not result[kind + "_matches"], f"world_level unavailable {kind} evidence")
        if result["success"]:
            _check(not errors and result.get("read_errors_truncated", False) is False,
                   "world_level success without read errors")
            _check(all(match["name"] is not None and match["database_number"] is not None
                       for match in result["database_matches"]), "world_level successful match identity")
        return {"status": status, "positive_matches": positive, "native_observation": result,
                "project_coverage_verified": False}
    except (ValueError, TypeError, KeyError) as exc:
        return {"status": "unverified", "reason": "invalid_native_response",
                "message": str(exc)[:2048],
                "positive_matches": {"name": None, "database_number": None, "team": None}}


def _validate(reply: Any, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    before = context["before"]
    _check(type(reply) is dict and reply.get("ok") is True and reply.get("version") == "E3D21"
           and reply.get("session_id") == before["session_id"], "envelope")
    result = reply.get("result")
    _check(type(result) is dict and type(result.get("success")) is bool
           and result.get("available") is True, "availability")
    _check(result.get("single_dispatch") is True and result.get("atomic_snapshot") is False, "consistency")
    _check(all(result.get(k) == v for k, v in {
        "session_id": before["session_id"], "project": before["name"], "mdb": before["mdb"],
        "context_id": payload["expected_context"], "expected_context": payload["expected_context"],
        "ce_before": before["current_element"], "ce_after": before["current_element"],
    }.items()) and result.get("ce_unchanged") is True and result.get("identity_unchanged") is True, "context")
    candidate = result.get("candidate")
    name = f"{payload['team']}/{payload['name']}"
    number = payload.get("database_number")
    _check(type(candidate) is dict and candidate.get("team") == payload["team"]
           and candidate.get("name") == payload["name"] and candidate.get("full_database_name") == name
           and "database_number" in candidate and candidate["database_number"] == number
           and type(candidate["database_number"]) is type(number), "candidate")

    positive = _validate_match_rows(result, payload, "catalog")

    counts = result.get("counts")
    _check(type(counts) is dict and counts and all(v is None or _count(v) for v in counts.values()), "observed counts")
    errors = result.get("read_errors")
    _check(type(errors) is list and len(errors) <= 128 and all(type(e) is dict for e in errors), "read errors")
    _check(not result["success"] or not errors and counts.get("mdb_database_positive_control_count", 0) > 0
           and counts.get("mdb_team_positive_control_count", 0) > 0
           and result.get("database_match_count") is not None and result.get("team_match_count") is not None,
           "success without catalog controls")
    world = _validate_world_level(result.get("world_level"), payload, context)
    for field, matched in world.get("positive_matches", {}).items():
        if matched is True:
            positive[field] = True
    return {"status": "observed" if result["success"] else "partial", "positive_matches": positive,
            "project_coverage_verified": False, "native_observation": result,
            "world_level": world}


def observe_project_catalog(bridge: BridgeClient, candidate: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    normalized = candidate["normalized_input"]
    payload = {"expected_context": context["context_id"], "team": normalized["team"], "name": normalized["name"]}
    if "database_number" in normalized:
        payload["database_number"] = normalized["database_number"]
    try:
        reply = bridge._request("POST", PATH, payload, version="E3D21")
    except BridgeError as exc:
        if exc.http_status == 404:
            return {"status": "unavailable", "reason": "native_feature_unavailable"}
        return {"status": "unavailable", "reason": "native_read_failed", "message": str(exc)[:2048]}
    try:
        return _validate(reply, payload, context)
    except (ValueError, TypeError) as exc:
        return {"status": "unverified", "reason": "invalid_native_response", "message": str(exc)[:2048]}
