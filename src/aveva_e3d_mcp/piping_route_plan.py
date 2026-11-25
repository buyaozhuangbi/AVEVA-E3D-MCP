"""Read-only PIPE/BRAN route planning over existing native observations."""
from __future__ import annotations

import math
import re
from typing import Any

from .branch_snapshot import piping_branch_state
from .bridge import BridgeClient, BridgeError, element_access_refnos, normalize_expected_element_type
from .context_snapshot import context_snapshot
from .pipe_member_manifest import pipe_member_manifest
from .model_plan_preview import model_edit_preview
from .protocol import McpServer


TOOL = "e3d_piping_route_plan"
VERSION = "E3D21"
MAX_PLANNED_MEMBERS = 16
MAX_MANIFEST_MEMBERS = 32
MAX_ENDPOINTS = 16
MAX_TRACE_STARTS = 64
_TYPE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_ATTRIBUTE = re.compile(r"[A-Za-z0-9_:]{1,64}")
_SET_ATTRIBUTES = frozenset({"DESC", "DUTY", "DIAM", "HEIG", "XLEN", "YLEN", "ZLEN", "DTOP", "DBOT"})


def _explicit_ref(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an explicit /NAME or =database/element reference")
    refs = element_access_refnos([value])
    ref = refs[0]
    if (len(ref) > 256 or any(c in ref for c in "*?") or
            not (ref.startswith("/") and len(ref) > 1 or re.fullmatch(r"=[0-9]+/[0-9]+", ref))):
        raise ValueError(f"{field} must be one literal /NAME or =database/element reference")
    if ref.startswith("="):
        ref = "=" + "/".join(str(int(part)) for part in ref[1:].split("/"))
    return ref


def _finite_vector(value: Any, field: str) -> list[float | int]:
    if (not isinstance(value, list) or len(value) != 3 or
            any(type(item) not in (int, float) or not math.isfinite(item) for item in value)):
        raise ValueError(f"{field} must contain three finite numbers")
    return list(value)


def _member_specs(raw: Any, root_refno: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_PLANNED_MEMBERS:
        raise ValueError(f"planned_members must contain 1 to {MAX_PLANNED_MEMBERS} members")
    members: list[dict[str, Any]] = []
    requested_attributes: set[str] = set()
    sibling_names: dict[int | None, set[str]] = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) - {
            "element_type", "name", "parent_index", "attributes", "set_attributes", "position", "orientation", "wrt"
        }:
            raise ValueError(f"planned_members[{index}] contains unsupported fields")
        kind = item.get("element_type")
        if not isinstance(kind, str) or not _TYPE.fullmatch(kind.strip().upper()):
            raise ValueError(f"planned_members[{index}].element_type must be an SDK type name")
        kind = kind.strip().upper()
        name = item.get("name")
        if (not isinstance(name, str) or not re.fullmatch(r"/[^\s\x00-\x1f\x7f-\x9f]{1,49}", name)):
            raise ValueError(f"planned_members[{index}].name must be a /NAME of at most 50 characters")
        name_key = name.lstrip("/").casefold()
        parent_index = item.get("parent_index")
        if parent_index is not None and (type(parent_index) is not int or not 0 <= parent_index < index):
            raise ValueError(f"planned_members[{index}].parent_index must refer to an earlier planned member")
        sibling_set = sibling_names.setdefault(parent_index, set())
        if name_key in sibling_set:
            raise ValueError(f"planned_members[{index}].name duplicates an earlier sibling name")
        sibling_set.add(name_key)
        attributes = item.get("attributes", {})
        if (not isinstance(attributes, dict) or len(attributes) > 20 or any(
                not isinstance(key, str) or not _ATTRIBUTE.fullmatch(key.strip()) or not isinstance(value, str)
                for key, value in attributes.items())):
            raise ValueError(f"planned_members[{index}].attributes must contain up to 20 named string values")
        attributes = {key.strip().upper(): value for key, value in attributes.items()}
        if len(attributes) != len(item.get("attributes", {})):
            raise ValueError(f"planned_members[{index}].attributes contains duplicate normalized names")
        set_attributes = item.get("set_attributes", {})
        if "set_attributes" in item and (
                not isinstance(set_attributes, dict) or not 1 <= len(set_attributes) <= 20):
            raise ValueError(f"planned_members[{index}].set_attributes must contain 1 to 20 values")
        if not isinstance(set_attributes, dict) or any(
                not isinstance(key, str) or key.strip().upper() not in _SET_ATTRIBUTES or not isinstance(value, str)
                for key, value in set_attributes.items()):
            raise ValueError(f"planned_members[{index}].set_attributes cannot rename a member and uses only DESC, DUTY, DIAM, HEIG, XLEN, YLEN, ZLEN, DTOP and DBOT")
        set_attributes = {key.strip().upper(): value for key, value in set_attributes.items()}
        if len(set_attributes) != len(item.get("set_attributes", {})):
            raise ValueError(f"planned_members[{index}].set_attributes contains duplicate normalized names")
        requested_attributes.update(attributes)
        requested_attributes.update(set_attributes)

        normalized: dict[str, Any] = {
            "index": index, "element_type": kind, "name": name,
            "parent_index": parent_index, "attributes": attributes,
            "set_attributes": set_attributes,
        }
        if "position" in item:
            normalized["position"] = _finite_vector(item["position"], f"planned_members[{index}].position")
        if "orientation" in item:
            orientation = item["orientation"]
            if not isinstance(orientation, dict) or set(orientation) != {"x_axis", "y_axis"}:
                raise ValueError(f"planned_members[{index}].orientation requires x_axis and y_axis")
            normalized["orientation"] = {
                "x_axis": _finite_vector(orientation["x_axis"], f"planned_members[{index}].orientation.x_axis"),
                "y_axis": _finite_vector(orientation["y_axis"], f"planned_members[{index}].orientation.y_axis"),
            }
        if "wrt" in item:
            wrt = item["wrt"]
            if not isinstance(wrt, str):
                raise ValueError(f"planned_members[{index}].wrt must be OWNER, WORLD, or an explicit reference")
            normalized["wrt"] = wrt.strip().upper() if wrt.strip().upper() in {"OWNER", "WORLD"} else _explicit_ref(wrt, f"planned_members[{index}].wrt")
        members.append(normalized)
    if len(requested_attributes) > 50:
        raise ValueError("planned member property candidates may contain at most 50 distinct attributes")
    return members


def _endpoints(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_ENDPOINTS:
        raise ValueError(f"endpoints must contain at most {MAX_ENDPOINTS} explicit endpoints")
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) != {"refno", "expected_type", "point_number"}:
            raise ValueError(f"endpoints[{index}] requires refno, expected_type and point_number")
        refno = _explicit_ref(item["refno"], f"endpoints[{index}].refno")
        expected_type = normalize_expected_element_type(item["expected_type"])
        if not expected_type:
            raise ValueError(f"endpoints[{index}].expected_type must identify the native element type")
        point = item["point_number"]
        if type(point) is not int or not 1 <= point <= 10000:
            raise ValueError(f"endpoints[{index}].point_number must be an integer from 1 through 10000")
        key = refno.casefold(), expected_type, point
        if key in seen:
            raise ValueError("endpoints must be unique by typed reference and P-point number")
        seen.add(key)
        result.append({"refno": refno, "expected_type": expected_type, "point_number": point})
    return result


def _identity(snapshot: Any) -> dict[str, Any]:
    result = snapshot.get("result") if isinstance(snapshot, dict) else None
    context = result.get("context") if isinstance(result, dict) else None
    coherence = result.get("session_coherence") if isinstance(result, dict) else None
    after = coherence.get("after") if isinstance(coherence, dict) else None
    identity = {
        "version": snapshot.get("version") if isinstance(snapshot, dict) else None,
        "session_id": after.get("session_id") if isinstance(after, dict) else None,
        "bridge_build": after.get("bridge_build") if isinstance(after, dict) else None,
        "project": context.get("project") if isinstance(context, dict) else None,
        "mdb": context.get("mdb") if isinstance(context, dict) else None,
        "current_element": context.get("current_element") if isinstance(context, dict) else None,
    }
    readiness = result.get("readiness") if isinstance(result, dict) else None
    coherence = result.get("session_coherence") if isinstance(result, dict) else None
    complete = (isinstance(snapshot, dict) and snapshot.get("ok") is True and
                isinstance(result, dict) and result.get("context_complete") is True and
                result.get("read_only") is True and isinstance(readiness, dict) and
                readiness.get("before") == "ready" and readiness.get("after") == "ready" and
                isinstance(coherence, dict) and coherence.get("status") == "consistent" and
                all(isinstance(value, str) and value.strip() for value in identity.values()))
    return {"complete": complete, "identity": identity, "evidence": snapshot}


def _context_agreement(before: dict[str, Any], after: dict[str, Any]) -> bool | None:
    if any(_text_agreement(after.get("identity", {}).get(key), value) is False
           for key, value in before.get("identity", {}).items()):
        return False
    if before.get("complete") is not True or after.get("complete") is not True:
        return None
    return before["identity"] == after["identity"]


def _text_agreement(observed: Any, expected: Any) -> bool | None:
    if not all(isinstance(value, str) and value.strip() for value in (observed, expected)):
        return None
    return observed == expected


def _reference_matches(requested: str | None, observed: Any, observed_name: Any = None) -> bool | None:
    if requested is None:
        return None
    if not isinstance(observed, str):
        return None
    if requested.startswith("="):
        return observed == requested
    return None if not isinstance(observed_name, str) else observed_name.casefold() == requested.casefold()


def _source_binding(reply: Any, before: dict[str, Any], after: dict[str, Any], *,
                    source_kind: str,
                    requested_refno: str | None = None, requested_type: str | None = None,
                    requested_point: int | None = None,
                    expected_starts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result = reply.get("result") if isinstance(reply, dict) else None
    identity = before.get("identity", {})
    checks: dict[str, bool | None] = {}
    checks["enclosing_context"] = _context_agreement(before, after)
    for key in ("version", "session_id", "bridge_build"):
        # BRAN is a Python workflow: its session/build evidence lives in both
        # context boundaries. Native responses require the HTTP envelope too.
        if key == "version" or source_kind != "branch_state" or isinstance(reply, dict) and key in reply:
            checks[key] = _text_agreement(reply.get(key) if isinstance(reply, dict) else None, identity.get(key))

    required_flags = {
        "member_manifest": ("same_project_instance", "same_mdb_instance", "boundary_agreement"),
        "branch_state": ("boundary_agreement",),
        "connection_points": ("same_project_instance", "same_mdb_instance", "identity_unchanged",
                              "ce_unchanged", "boundary_agreement"),
        "schema": ("same_project_instance", "same_mdb_instance", "identity_unchanged",
                   "ce_unchanged", "boundary_agreement"),
        "connection_trace": (),
    }[source_kind]
    source_context = result.get("context") if isinstance(result, dict) else None
    if source_kind != "connection_trace" or isinstance(result, dict) and "context" in result:
        start = source_context.get("before") if isinstance(source_context, dict) else None
        end = source_context.get("after") if isinstance(source_context, dict) else None
        for suffix, sample in (("", start), ("_after", end)):
            for key in ("project", "mdb", "current_element"):
                checks[key + suffix] = _text_agreement(
                    sample.get(key) if isinstance(sample, dict) else None, identity.get(key))
        for key in ("version", "session_id", "bridge_build"):
            if source_kind == "branch_state" or any(isinstance(sample, dict) and key in sample for sample in (start, end)):
                for boundary, sample in (("before", start), ("after", end)):
                    checks[f"source_context_{boundary}_{key}"] = _text_agreement(
                        sample.get(key) if isinstance(sample, dict) else None, identity.get(key))
        if source_kind in {"member_manifest", "connection_points", "schema"} or any(
                isinstance(sample, dict) and "current_element_type" in sample for sample in (start, end)):
            checks["current_element_type_boundary"] = _text_agreement(
                end.get("current_element_type") if isinstance(end, dict) else None,
                start.get("current_element_type") if isinstance(start, dict) else None)
        for key in ("same_project_instance", "same_mdb_instance", "identity_unchanged", "ce_unchanged",
                    "boundary_agreement"):
            if key in required_flags or isinstance(source_context, dict) and key in source_context:
                value = source_context.get(key) if isinstance(source_context, dict) else None
                checks["source_context_" + key] = value if type(value) is bool else None

    # Endpoint/schema replies contain both a context object and flat aliases;
    # trace replies contain flat fields only. Never let one layer replace another.
    native_flat = source_kind in {"connection_points", "schema", "connection_trace"}
    before_suffix = "_before" if source_kind == "connection_trace" else ""
    flat_fields = {"project" + before_suffix: "project", "project_after": "project",
                   "mdb" + before_suffix: "mdb", "mdb_after": "mdb",
                   "ce_before": "current_element", "ce_after": "current_element"}
    flat_fields.update({"project_before": "project", "mdb_before": "mdb"})
    for source_key, identity_key in flat_fields.items():
        required = native_flat and (source_key not in {"project_before", "mdb_before"} or source_kind == "connection_trace")
        if required or isinstance(result, dict) and source_key in result:
            checks["source_" + source_key] = _text_agreement(
                result.get(source_key) if isinstance(result, dict) else None, identity.get(identity_key))
    for key in ("identity_unchanged", "ce_unchanged"):
        if native_flat or isinstance(result, dict) and key in result:
            value = result.get(key) if isinstance(result, dict) else None
            checks["source_" + key] = value if type(value) is bool else None
    source_element = result.get("element") if isinstance(result, dict) else None
    if not isinstance(source_element, dict) and isinstance(result, dict):
        source_element = result.get("root")
    if requested_refno is not None:
        checks["requested_refno"] = _reference_matches(
            requested_refno, source_element.get("refno") if isinstance(source_element, dict) else None,
            source_element.get("name") if isinstance(source_element, dict) else None)
    if requested_type is not None:
        observed_type = ((source_element.get("type") or source_element.get("short_name"))
                         if isinstance(source_element, dict) else None)
        checks["requested_type"] = (observed_type.casefold() == requested_type.casefold()
                                     if isinstance(observed_type, str) else None)
    if requested_point is not None and isinstance(result, dict):
        points = result.get("points")
        checks["requested_point"] = (None if not isinstance(points, list) else any(
            isinstance(point, dict) and point.get("number") == requested_point for point in points))
    if expected_starts is not None and isinstance(result, dict):
        nodes = result.get("nodes")
        observed_starts = set()
        if isinstance(nodes, list):
            for node in nodes:
                if isinstance(node, dict):
                    observed_starts.add((node.get("requested_refno"), node.get("requested_type"), node.get("point_number")))
        checks["requested_starts"] = (None if not isinstance(nodes, list) else all(
            (start.get("refno"), start.get("expected_type"), start.get("point_number")) in observed_starts
            for start in expected_starts))
    status = False if any(value is False for value in checks.values()) else (
        True if all(value is True for value in checks.values()) else None)
    return {"matches_enclosing_context": status, "checks": checks,
            "scope": "Each required source boundary and identity layer is compared with surrounding context observations; missing or malformed evidence remains unknown and observed conflicts remain false."}


def _read(stage: str, operation: Any, errors: list[dict[str, Any]]) -> Any:
    try:
        return operation()
    except Exception as exc:
        error = {"stage": stage, "code": "read_error", "type": type(exc).__name__, "message": str(exc)[:1024]}
        if isinstance(exc, BridgeError):
            error.update({key: getattr(exc, key) for key in ("http_status", "transport_error", "response_invalid") if hasattr(exc, key)})
        errors.append(error)
        return None


def _response_ok(reply: Any) -> bool:
    return isinstance(reply, dict) and reply.get("ok") is True and isinstance(reply.get("result"), dict)


def _write_sequence(root_refno: str, members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    create_sequence_by_member: dict[int, int] = {}
    for member in members:
        index = member["index"]
        parent_index = member["parent_index"]
        parent = (root_refno if parent_index is None else
                  f"<created_refno_from_step_{create_sequence_by_member[parent_index]}>")
        create_args: dict[str, Any] = {
            "parent_refno": parent, "element_type": member["element_type"], "name": member["name"],
            "attributes": member["attributes"], "dry_run": True,
        }
        for key in ("position", "orientation", "wrt"):
            if key in member:
                create_args[key] = member[key]
        create_sequence = len(steps) + 1
        create_sequence_by_member[index] = create_sequence
        steps.append({"sequence": create_sequence, "planned_member_index": index,
                      "kind": "create_preview", "tool": "e3d_create_element",
                      "arguments": create_args, "revision": None, "expected_revision": None,
                      "created_refno": None,
                      "next_execution_requires": "A fresh revision from this exact native preview; this plan does not request or invent one."})
        if member["set_attributes"]:
            refno = f"<created_refno_from_step_{create_sequence}>"
            steps.append({"sequence": len(steps) + 1, "kind": "set_attributes_preview", "tool": "e3d_set_attributes",
                          "arguments": {"refno": refno, "values": member["set_attributes"], "dry_run": True},
                          "revision": None, "expected_revision": None,
                          "next_execution_requires": "Create the element, read its exact native reference, then obtain a fresh preview revision for these values."})
    return steps


def piping_route_plan(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict) or set(args) - {"root_refno", "planned_members", "endpoints", "max_depth", "member_limit", "include_native_previews"}:
        raise ValueError("Only root_refno, planned_members, endpoints, max_depth, member_limit and include_native_previews are accepted")
    include_native_previews = args.get("include_native_previews", False)
    if type(include_native_previews) is not bool:
        raise ValueError("include_native_previews must be a boolean")
    root_refno = _explicit_ref(args.get("root_refno"), "root_refno")
    members = _member_specs(args.get("planned_members"), root_refno)
    endpoints = _endpoints(args.get("endpoints"))
    max_depth = args.get("max_depth", 8)
    member_limit = args.get("member_limit", 16)
    if type(max_depth) is not int or not 1 <= max_depth <= 16:
        raise ValueError("max_depth must be an integer from 1 through 16")
    if type(member_limit) is not int or not 1 <= member_limit <= MAX_MANIFEST_MEMBERS:
        raise ValueError(f"member_limit must be an integer from 1 through {MAX_MANIFEST_MEMBERS}")

    version = bridge._selected_version(None)
    result: dict[str, Any] = {
        "operation": "piping_route_plan", "success": False, "available": False,
        "complete": False, "read_only": True, "atomic_snapshot": False,
        "consistency": "sequential_read_only_observations", "root_refno": root_refno,
        "context": {"before": None, "after": None, "boundary_agreement": None, "status": "unknown"},
        "revision": None,
        "revision_policy": {"value": None, "source": "not_observed", "execution_allowed": False,
                             "message": "The plan has no shared revision. Optional native previews provide per-step revisions."},
        "sources": {"member_manifest": None, "branch_states": [], "connection_points": [],
                    "connection_trace": [], "schemas": []},
        "explicit_members": [], "endpoints": [], "name_candidates": [], "type_candidates": [], "property_candidates": [],
        "planned_members": [{key: value for key, value in member.items()} for member in members],
        "write_sequence": _write_sequence(root_refno, members), "read_errors": [],
        "native_previews": {"requested": include_native_previews,
            "status": "not_attempted" if include_native_previews else "not_requested",
            "complete": False if include_native_previews else None,
            "prepared_count": 0, "deferred_count": 0, "stopped_on_native_fault": False,
            "preview_context": {"before": None, "after": None, "boundary_agreement": None},
            "scope": "Optional exact-request dry_run previews only. No model writes or set-attribute previews for not-yet-created elements."},
        "scope": "Read-only plan over live native evidence. Cross-request reads are not a database lock. "
                 "By default no create/set-attribute preview is issued. Optional native previews are dry_run only; no model write, command, CE switch, view change or capture is issued.",
    }
    envelope = {"ok": True, "version": version, "result": result}

    def error(stage: str, exc: Any, code: str = "read_error") -> None:
        result["read_errors"].append({"stage": stage, "code": code, "message": str(exc)[:1024]})

    if version != VERSION or bridge.settings.mode != "bridge":
        error("availability", "Piping route plans require E3D21 bridge mode", "native_unavailable")
        return envelope

    before_raw = _read("context_before", lambda: context_snapshot(bridge, bridge.settings), result["read_errors"])
    before = _identity(before_raw)
    result["context"]["before"] = before
    if before["complete"] is not True:
        error("context_before", "A fresh ready session/project/MDB/current-element boundary is unavailable", "context_unknown")
        return envelope

    manifest = _read("member_manifest", lambda: pipe_member_manifest(bridge, {
        "refno": root_refno, "max_depth": max_depth, "limit": member_limit,
        "attributes": ["SPREF", "POS", "ORI", "ABOR", "LBOR"],
    }), result["read_errors"])
    result["sources"]["member_manifest"] = {"requested_refno": root_refno, "response": manifest}
    manifest_result = manifest.get("result") if isinstance(manifest, dict) else None
    if isinstance(manifest_result, dict) and manifest_result.get("available") is True:
        root = manifest_result.get("root")
        rows = manifest_result.get("rows")
        if isinstance(root, dict) and isinstance(rows, list):
            result["explicit_members"] = [root, *rows]
    else:
        root, rows = None, []

    branch_refs = []
    if isinstance(root, dict) and root.get("type") == "BRAN":
        branch_refs.append(root)
    if isinstance(rows, list):
        branch_refs.extend(row for row in rows if isinstance(row, dict) and row.get("type") == "BRAN")
    for branch in branch_refs[:MAX_MANIFEST_MEMBERS]:
        reply = _read("branch_state", lambda branch=branch: piping_branch_state(bridge, {"refno": branch.get("refno")} ), result["read_errors"])
        result["sources"]["branch_states"].append({"requested_refno": branch.get("refno"),
            "requested_type": "BRAN", "response": reply})

    # BRAN is a route container, not a P-point component. Keep explicit TUBI
    # reads for their native evidence, but allow partial alias evidence to be
    # reported separately from root-level create previews.
    connection_members = [row for row in result["explicit_members"] if isinstance(row, dict)
                          and row.get("type") not in {"PIPE", "BRAN"}]
    starts: list[dict[str, Any]] = []
    omitted_starts = 0
    for row in connection_members:
        refno, kind = row.get("refno"), row.get("type")
        if not isinstance(refno, str) or not isinstance(kind, str):
            continue
        reply = _read("connection_points", lambda refno=refno, kind=kind: bridge.connection_points(
            refno, "WORLD", [], expected_type=kind), result["read_errors"])
        result["sources"]["connection_points"].append({"requested_refno": refno, "expected_type": kind,
                                                       "response": reply})
        native_result = reply.get("result") if isinstance(reply, dict) else None
        point_rows = native_result.get("points") if isinstance(native_result, dict) else None
        if isinstance(point_rows, list):
            for point in point_rows:
                number = point.get("number") if isinstance(point, dict) else None
                if type(number) is int and 1 <= number <= 10000:
                    if len(starts) < MAX_TRACE_STARTS:
                        starts.append({"refno": refno, "point_number": number, "expected_type": kind})
                    else:
                        omitted_starts += 1

    for endpoint in endpoints:
        reply = _read("endpoint", lambda endpoint=endpoint: bridge.connection_points(
            endpoint["refno"], "WORLD", [endpoint["point_number"]], expected_type=endpoint["expected_type"]),
            result["read_errors"])
        result["endpoints"].append({**endpoint, "response": reply})
        starts.append({"refno": endpoint["refno"], "point_number": endpoint["point_number"],
                       "expected_type": endpoint["expected_type"]})

    unique_starts: list[dict[str, Any]] = []
    seen_starts: set[tuple[str, str, int]] = set()
    for start in starts:
        key = start["refno"].casefold(), start["expected_type"], start["point_number"]
        if key not in seen_starts and len(unique_starts) < MAX_TRACE_STARTS:
            seen_starts.add(key)
            unique_starts.append(start)
    if omitted_starts or len(starts) > MAX_TRACE_STARTS:
        result["read_errors"].append({"stage": "connection_trace", "code": "trace_start_limit",
                                      "message": f"Only the first {MAX_TRACE_STARTS} automatic P-points were admitted to bounded tracing; omitted={omitted_starts}."})
        result["trace_scope"] = {"limit": MAX_TRACE_STARTS, "omitted_automatic_starts": omitted_starts,
                                  "complete": False}
    for offset in range(0, len(unique_starts), 16):
        chunk = unique_starts[offset:offset + 16]
        reply = _read("connection_trace", lambda chunk=chunk: bridge.trace_connection_network(chunk, 8, 64), result["read_errors"])
        result["sources"]["connection_trace"].append({"starts": chunk, "response": reply})

    requested_by_type: dict[str, set[str]] = {}
    for member in members:
        requested_by_type.setdefault(member["element_type"], set()).update(member["attributes"])
        requested_by_type[member["element_type"]].update(member["set_attributes"])
    exemplars: dict[str, dict[str, Any]] = {}
    for row in result["explicit_members"]:
        if isinstance(row, dict) and isinstance(row.get("type"), str) and row["type"] not in exemplars:
            exemplars[row["type"]] = row
    root_type = root.get("type") if isinstance(root, dict) else None
    root_attribute_names = sorted(requested_by_type.get(root_type, set()))
    schema_targets = [(root, root_type, root_attribute_names)] if isinstance(root, dict) and isinstance(root_type, str) else []
    for kind in sorted(requested_by_type):
        exemplar = exemplars.get(kind)
        if not isinstance(exemplar, dict):
            result["sources"]["schemas"].append({"expected_type": kind, "refno": None, "response": None,
                                                  "binding": {"matches_enclosing_context": None, "checks": {},
                                                              "scope": "No existing typed exemplar was observed."}})
            continue
        schema_targets.append((exemplar, kind, sorted(requested_by_type[kind])))

    schema_by_type: dict[str, Any] = {}
    for target, kind, names in schema_targets:
        if not isinstance(kind, str) or kind in schema_by_type:
            continue
        refno = target.get("refno")
        reply = _read("element_schema", lambda refno=refno, kind=kind, names=names: bridge.element_schema(
            refno, names, 0, 50, expected_type=kind), result["read_errors"])
        schema_by_type[kind] = reply
        result["sources"]["schemas"].append({"refno": refno, "expected_type": kind,
            "requested_refno": refno, "requested_attributes": names, "response": reply})

    for member in members:
        kind = member["element_type"]
        schema = schema_by_type.get(kind)
        schema_result = schema.get("result") if isinstance(schema, dict) else None
        schema_rows = schema_result.get("attributes") if isinstance(schema_result, dict) else None
        by_name = {str(row.get("requested_name", row.get("name", ""))).upper(): row
                   for row in schema_rows if isinstance(row, dict)} if isinstance(schema_rows, list) else {}
        for source_name, values in (("create", member["attributes"]), ("set", member["set_attributes"])):
            for name in values:
                evidence = by_name.get(name)
                if evidence is None or evidence.get("read_error"):
                    state = "unknown"
                elif evidence.get("supported") is False:
                    state = "unsupported"
                elif evidence.get("supported") is not True:
                    state = "unknown"
                elif source_name == "set" and evidence.get("direct_edit_supported") is not True:
                    state = "not_direct_edit_supported" if evidence.get("direct_edit_supported") is False else "unknown"
                elif source_name == "set" and evidence.get("sdk_settable_now") is not True:
                    state = "not_settable_now" if evidence.get("sdk_settable_now") is False else "unknown"
                else:
                    state = "candidate"
                result["property_candidates"].append({"planned_member_index": member["index"], "element_type": kind,
                    "attribute": name, "use": source_name, "status": state, "observed_status": state,
                    "evidence": evidence, "write_permission_verified": False,
                    "scope": "Schema metadata on an existing same-type exemplar; proposed value and new-element writability are unverified."})

        parent_kind = (root.get("type") if member["parent_index"] is None and isinstance(root, dict)
                       else members[member["parent_index"]]["element_type"])
        parent_schema = schema_by_type.get(parent_kind)
        parent_result = parent_schema.get("result") if isinstance(parent_schema, dict) else None
        child_rows = parent_result.get("child_types") if isinstance(parent_result, dict) else None
        child = next((row for row in child_rows if isinstance(row, dict) and
                      str(row.get("short_name", row.get("name", ""))).upper() == kind), None) \
            if isinstance(child_rows, list) else None
        if child is None:
            compatibility = "not_listed" if isinstance(child_rows, list) and not parent_result.get("read_errors") else "unknown"
        elif child.get("sdk_creatable_now") is True:
            compatibility = "candidate_now"
        elif child.get("sdk_creatable_now") is False:
            compatibility = "not_creatable_now"
        else:
            compatibility = "unknown"
        result["type_candidates"].append({
            "planned_member_index": member["index"], "parent_refno": root.get("refno") if member["parent_index"] is None and isinstance(root, dict) else None,
            "parent_type": parent_kind, "element_type": kind, "status": compatibility,
            "evidence": child, "write_permission_verified": False,
            "scope": "Current child-type/schema observation on the selected root or an existing same-type exemplar; does not authorize creation or prove compatibility on a future parent instance.",
        })

    after_raw = _read("context_after", lambda: context_snapshot(bridge, bridge.settings), result["read_errors"])
    after = _identity(after_raw)
    result["context"]["after"] = after
    agreement = _context_agreement(before, after)
    result["context"]["boundary_agreement"] = agreement
    result["context"]["status"] = "consistent" if agreement is True else "changed" if agreement is False else "unknown"
    if agreement is not True:
        error("context_after", "Session, build, project, MDB or current element changed or could not be verified across the plan", "context_unknown" if agreement is None else "context_changed")

    source_groups = (
        ("member_manifest", [result["sources"]["member_manifest"]]),
        ("branch_state", result["sources"]["branch_states"]),
        ("connection_points", [*result["sources"]["connection_points"], *result["endpoints"]]),
        ("connection_trace", result["sources"]["connection_trace"]),
        ("schema", result["sources"]["schemas"]),
    )
    for source_kind, groups in source_groups:
        for group in groups:
            if isinstance(group, dict):
                group["binding"] = _source_binding(
                    group.get("response"), before, after, source_kind=source_kind,
                    requested_refno=group.get("requested_refno", group.get("refno")),
                    requested_type=group.get("expected_type", group.get("requested_type")),
                    requested_point=group.get("point_number"),
                    expected_starts=group.get("starts"))

    manifest_rows = manifest_result.get("rows") if isinstance(manifest_result, dict) else None
    manifest_complete = isinstance(manifest_result, dict) and manifest_result.get("complete") is True
    for member in members:
        if member["parent_index"] is None and isinstance(manifest_rows, list):
            siblings = [row for row in manifest_rows if isinstance(row, dict) and row.get("parent_index") == 0]
            collisions = [row for row in siblings if isinstance(row.get("name"), str) and
                          row["name"].lstrip("/").casefold() == member["name"].lstrip("/").casefold()]
            name_status = "blocked_existing_collision" if collisions else (
                "no_observed_collision" if manifest_complete else "unknown")
        else:
            siblings, collisions = [], []
            name_status = "unknown"
        sibling_evidence = [{key: row.get(key) for key in ("refno", "type", "name", "sibling_index", "identity_key")}
                            for row in siblings]
        collision_evidence = [{key: row.get(key) for key in ("refno", "type", "name", "sibling_index", "identity_key")}
                              for row in collisions]
        result["name_candidates"].append({
            "planned_member_index": member["index"], "parent_index": member["parent_index"],
            "name": member["name"], "status": name_status,
            "existing_siblings": sibling_evidence if member["parent_index"] is None else [],
            "collisions": collision_evidence,
            "scope": "Exact case-insensitive name comparison against observed direct children of the existing route root; planned-parent uniqueness is validated in-request, future database constraints remain unknown.",
        })

    binding_rows = [group.get("binding") for group in (
        result["sources"]["member_manifest"], *result["sources"]["branch_states"],
        *result["sources"]["connection_points"], *result["endpoints"],
        *result["sources"]["connection_trace"], *result["sources"]["schemas"]
    ) if isinstance(group, dict)]
    any_context_mismatch = agreement is False or any(
        isinstance(binding, dict) and binding.get("matches_enclosing_context") is False
        for binding in binding_rows
    )
    if any_context_mismatch or any(binding.get("matches_enclosing_context") is not True
                                    for binding in binding_rows if isinstance(binding, dict)):
        for candidate in result["property_candidates"]:
            candidate["status"] = "unknown"
        for candidate in result["type_candidates"]:
            candidate["status"] = "unknown"
        for candidate in result["name_candidates"]:
            if candidate["status"] == "no_observed_collision":
                candidate["status"] = "unknown"

    for member in result["planned_members"]:
        name_candidate = next((row for row in result["name_candidates"]
                               if row["planned_member_index"] == member["index"]), None)
        if name_candidate and name_candidate["status"] == "blocked_existing_collision":
            member["status"] = "blocked"
        elif name_candidate and name_candidate["status"] == "no_observed_collision" and all(
                row.get("status") in {"candidate", "unsupported", "not_direct_edit_supported", "not_settable_now"}
                for row in result["property_candidates"] if row["planned_member_index"] == member["index"]):
            member["status"] = "candidate"
        else:
            member["status"] = "unknown"

    all_sources_bound = bool(binding_rows) and all(
        isinstance(binding, dict) and binding.get("matches_enclosing_context") is True
        for binding in binding_rows)
    structural_rows = [result["sources"]["member_manifest"], *result["sources"]["branch_states"],
                       *result["sources"]["schemas"]]
    structural_bindings = [row.get("binding") for row in structural_rows if isinstance(row, dict)]
    structural_sources_bound = bool(structural_bindings) and all(
        isinstance(binding, dict) and binding.get("matches_enclosing_context") is True
        for binding in structural_bindings)
    connectivity_rows = [*result["sources"]["connection_points"], *result["endpoints"],
                         *result["sources"]["connection_trace"]]
    connectivity_bindings = [row.get("binding") for row in connectivity_rows if isinstance(row, dict)]
    connectivity_sources_bound = all(
        isinstance(binding, dict) and binding.get("matches_enclosing_context") is True
        for binding in connectivity_bindings)
    names_clear = all(row.get("status") == "no_observed_collision" for row in result.get("name_candidates", []))
    structural_read_error_stages = {"context_before", "member_manifest", "branch_state", "element_schema", "context_after"}
    structural_read_errors = any(error.get("stage") in structural_read_error_stages
                                 for error in result["read_errors"])
    structural_sources_complete = (
        agreement is True and structural_sources_bound and not structural_read_errors and
        isinstance(manifest_result, dict) and manifest_result.get("complete") is True and
        all(isinstance(row.get("response"), dict) and row["response"].get("result", {}).get("complete") is True
            for row in result["sources"]["branch_states"]) and
        all(row.get("response") is not None and _response_ok(row.get("response")) and
            row["response"]["result"].get("success") is True and
            not row["response"]["result"].get("read_errors")
            for row in result["sources"]["schemas"]) and
        not result["read_errors"]
    )
    connectivity_complete = (
        connectivity_sources_bound and
        all(_response_ok(row.get("response")) and row["response"]["result"].get("complete") is True
            for row in result["sources"]["connection_points"]) and
        all(_response_ok(row.get("response")) and row["response"]["result"].get("complete") is True
            for row in result["endpoints"]) and
        all(_response_ok(row.get("response")) and row["response"]["result"].get("complete") is True
            for row in result["sources"]["connection_trace"])
    )
    read_complete = structural_sources_complete and connectivity_complete and names_clear
    result["structural_read_complete"] = structural_sources_complete
    result["connectivity_evidence_complete"] = connectivity_complete
    result["available"] = isinstance(manifest_result, dict) and manifest_result.get("available") is True
    result["complete"] = result["success"] = bool(read_complete)
    result["status"] = "complete_observation" if read_complete else "partial_or_unknown"
    if include_native_previews:
        if structural_sources_complete:
            _prepare_native_previews(bridge, result, manifest_result["root"], after)
        else:
            result["native_previews"]["status"] = "skipped_incomplete_structural_observation"
    return envelope


def _prepare_native_previews(bridge: BridgeClient, result: dict[str, Any],
                             root: dict[str, Any], before: dict[str, Any]) -> None:
    previews = result["native_previews"]
    previews["preview_context"]["before"] = before
    names = {row["planned_member_index"]: row["status"] for row in result["name_candidates"]}
    observed = []
    for step in result["write_sequence"]:
        arguments = step["arguments"]
        if step["kind"] == "set_attributes_preview":
            step["preview_status"] = "deferred_until_created_refno"
        elif arguments["parent_refno"].startswith("<created_refno_"):
            step["preview_status"] = "deferred_parent_not_yet_created"
        elif names[step["planned_member_index"]] != "no_observed_collision":
            step["preview_status"] = "blocked_or_unverified_name"
        else:
            arguments = {**arguments, "parent_refno": root["refno"], "wrt": arguments.get("wrt", "OWNER")}
            preview = model_edit_preview(bridge, step["tool"], arguments, before["identity"])
            step["native_preview"] = preview
            step["preview_status"] = preview["status"]
            if preview["status"] in {"native_fault", "dispatch_failed"}:
                previews["stopped_on_native_fault"] = preview["status"] == "native_fault"
                previews["status"] = ("stopped_on_native_fault" if previews["stopped_on_native_fault"]
                                      else "preview_dispatch_failed")
                return
            if preview["status"] == "observed":
                observed.append(step)
            continue
        previews["deferred_count"] += 1

    if observed:
        after = _identity(_read("native_preview_context_after", lambda: context_snapshot(bridge, bridge.settings),
                                previews.setdefault("read_errors", [])))
        previews["preview_context"]["after"] = after
        agreement = _context_agreement(before, after)
        previews["preview_context"]["boundary_agreement"] = agreement
        if agreement is not True:
            previews["status"] = "context_unverified"
            for step in observed:
                step["native_preview"]["status"] = "context_unverified"
                step["preview_status"] = "context_unverified"
            return
    for step in observed:
        preview = step["native_preview"]
        revision = preview["response"]["result"]["revision"]
        step.update(revision=revision, expected_revision=revision, preview_status="prepared",
                    execution_arguments={**preview["arguments"], "dry_run": False, "expected_revision": revision},
                    next_execution_requires="Use these exact arguments while the native revision remains current.")
        preview["status"] = "prepared"
    previews["prepared_count"] = len(observed)
    previews["complete"] = len(observed) == len(result["write_sequence"])
    previews["status"] = "prepared" if previews["complete"] else "partial_or_unknown"


def register_piping_route_plan(server: McpServer, bridge: BridgeClient) -> None:
    server.add_tool(
        TOOL,
        "Build a read-only plan for an explicit PIPE or BRAN. Reads the bounded member manifest, each observed BRAN state, "
        "actual typed member P-points, optional explicit endpoint P-points and PCREF traces, and schema candidates for existing same-type elements. "
        "Returns a proposed ordered create_element/set_attributes dry-run sequence. BRAN rows and implicit TUBI aliases are retained as structural evidence; P-points are read from component anchors. Set include_native_previews=true to request exact create dry_run receipts and revisions for currently existing root children. Partial connectivity evidence does not erase an independent root-level dry_run; descendants of planned new parents and post-create attribute setters stay deferred. No model write is issued. "
        "Cross-request reads are live, non-atomic observations; missing or changed session/project/MDB/CE context remains unknown. "
        "Schema and connection evidence do not validate proposed values, geometry, routing, catalogue compatibility or engineering suitability.",
        {"type": "object", "properties": {
            "root_refno": {"type": "string", "minLength": 2, "maxLength": 256,
                             "description": "One explicit Design PIPE or BRAN /NAME or =database/element."},
            "planned_members": {"type": "array", "minItems": 1, "maxItems": MAX_PLANNED_MEMBERS,
                "items": {"type": "object", "properties": {
                    "element_type": {"type": "string", "minLength": 1, "maxLength": 64},
                    "name": {"type": "string", "minLength": 2, "maxLength": 50},
                    "parent_index": {"type": "integer", "minimum": 0, "description": "Optional earlier planned member index; omission means root_refno."},
                    "attributes": {"type": "object", "maxProperties": 20, "additionalProperties": {"type": "string"}},
                    "set_attributes": {"type": "object", "minProperties": 1, "maxProperties": 20,
                        "description": "Set attributes after creation. NAME is excluded; use the planned member name field.",
                        "propertyNames": {"pattern": r"^\s*(?:[Dd][Ee][Ss][Cc]|[Dd][Uu][Tt][Yy]|[Dd][Ii][Aa][Mm]|[Hh][Ee][Ii][Gg]|[Xx][Ll][Ee][Nn]|[Yy][Ll][Ee][Nn]|[Zz][Ll][Ee][Nn]|[Dd][Tt][Oo][Pp]|[Dd][Bb][Oo][Tt])\s*$"},
                        "additionalProperties": {"type": "string"}},
                    "position": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
                    "orientation": {"type": "object", "properties": {
                        "x_axis": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
                        "y_axis": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
                    }, "required": ["x_axis", "y_axis"], "additionalProperties": False},
                    "wrt": {"type": "string", "default": "OWNER"},
                }, "required": ["element_type", "name"], "additionalProperties": False}},
            "endpoints": {"type": "array", "maxItems": MAX_ENDPOINTS, "items": {"type": "object", "properties": {
                "refno": {"type": "string", "minLength": 2, "maxLength": 256},
                "expected_type": {"type": "string", "minLength": 1, "maxLength": 64},
                "point_number": {"type": "integer", "minimum": 1, "maximum": 10000},
            }, "required": ["refno", "expected_type", "point_number"], "additionalProperties": False}, "default": []},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 16, "default": 8},
            "member_limit": {"type": "integer", "minimum": 1, "maximum": MAX_MANIFEST_MEMBERS, "default": 16},
            "include_native_previews": {"type": "boolean", "default": False,
                "description": "Request exact create dry_run previews for planned direct children of the observed root. No model edits are performed; planned descendants remain deferred."},
        }, "required": ["root_refno", "planned_members"], "additionalProperties": False},
        lambda args: piping_route_plan(bridge, args), read_only=True,
    )
