"""One-request, bounded native connectivity evidence and a readable report.

This module deliberately does not register itself in the public server. The
matching native implementation is E3d21ConnectivitySnapshot.cs; there is no
fallback that silently fans out to existing routes with weaker binding.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any

from .branch_snapshot import ATTRIBUTES
from .bridge import BridgeError, element_access_refnos

TOOL = "e3d_piping_connectivity_snapshot"
PATH = "/api/v1/piping/connectivity-snapshot"
CONTEXT_FIELDS = ("session_id", "bridge_build", "project", "mdb", "current_element", "current_element_type")
_TYPE = re.compile(r"[A-Z][A-Z0-9_:]{0,63}")


def _native_fault(value: Any) -> bool:
    if isinstance(value, dict):
        code = value.get("error_code")
        return (value.get("has_fault") is True or value.get("native_fault") is True or
                value.get("native_fault_latched") is True or
                isinstance(code, str) and code in {"native_fault", "native_database_fault"} or
                any(_native_fault(item) for item in value.values()))
    return isinstance(value, list) and any(_native_fault(item) for item in value)


def _reference(value: Any) -> str:
    value = element_access_refnos([value])[0]
    if len(value) > 512 or any(char in value for char in "*?"):
        raise ValueError("A bounded literal /NAME or =database/element reference is required")
    return "=" + "/".join(str(int(part)) for part in value[1:].split("/")) if value[0] == "=" else value


def _kind(value: Any) -> str:
    if not isinstance(value, str) or not _TYPE.fullmatch(value.strip().upper()):
        raise ValueError("An explicit native expected_type is required")
    return value.strip().upper()


def _integer(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Expected an integer from {minimum} through {maximum}")
    return value


def _endpoint(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"refno", "expected_type", "point_number"}:
        raise ValueError("Each endpoint requires exactly refno, expected_type and point_number")
    return {"refno": _reference(value["refno"]), "expected_type": _kind(value["expected_type"]),
            "point_number": _integer(value["point_number"], 1, 10000)}


def _key(value: dict[str, Any]) -> tuple[str, str, int]:
    return value["refno"].casefold(), value["expected_type"], value["point_number"]


def connectivity_snapshot_parameters(args: Any) -> dict[str, Any]:
    if not isinstance(args, dict) or set(args) - {"branches", "pairs", "starts", "member_limit", "max_hops", "max_nodes", "expected_context"}:
        raise ValueError("Unknown connectivity snapshot fields")
    normalized: dict[str, Any] = {}
    for group, maximum in (("branches", 8), ("pairs", 16), ("starts", 16)):
        values = args.get(group, [])
        if not isinstance(values, list) or len(values) > maximum:
            raise ValueError(f"{group} must contain at most {maximum} entries")
        rows = []
        keys = set()
        for value in values:
            if group == "branches":
                if not isinstance(value, dict) or set(value) != {"refno", "expected_type"} or _kind(value["expected_type"]) != "BRAN":
                    raise ValueError("Each branch requires refno and expected_type BRAN")
                row = {"refno": _reference(value["refno"]), "expected_type": "BRAN"}
                key = row["refno"].casefold()
            elif group == "pairs":
                if not isinstance(value, dict) or set(value) != {"first", "second", "tolerance_mm"}:
                    raise ValueError("Each pair requires first, second and tolerance_mm")
                tolerance = value["tolerance_mm"]
                if type(tolerance) not in (int, float) or not 0 <= tolerance <= 1000000 or not math.isfinite(tolerance):
                    raise ValueError("tolerance_mm must be finite, from 0 through 1000000 mm")
                row = {"first": _endpoint(value["first"]), "second": _endpoint(value["second"]), "tolerance_mm": float(tolerance)}
                key = _key(row["first"]), _key(row["second"])
                if key[0] == key[1]:
                    raise ValueError("A pair requires distinct typed P-points")
            else:
                row = _endpoint(value)
                key = _key(row)
            if key in keys:
                raise ValueError(f"Duplicate typed selection in {group}")
            keys.add(key)
            rows.append(row)
        normalized[group] = rows
    if not any(normalized.values()):
        raise ValueError("At least one explicit branch, pair or trace start is required")
    normalized.update(member_limit=_integer(args.get("member_limit", 64), 1, 64),
                      max_hops=_integer(args.get("max_hops", 8), 0, 16),
                      max_nodes=_integer(args.get("max_nodes", 64), max(1, len(normalized["starts"])), 128))
    if "expected_context" in args:
        value = args["expected_context"]
        if not isinstance(value, dict) or set(value) != set(CONTEXT_FIELDS) or any(
                not isinstance(text, str) or not text.strip() or len(text) > 512 or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in text)
                for text in value.values()):
            raise ValueError("expected_context requires all six identity fields")
        expected = {**value, "current_element": _reference(value["current_element"]), "current_element_type": _kind(value["current_element_type"])}
        if not expected["current_element"].startswith("="):
            raise ValueError("expected_context.current_element must be numeric")
        normalized["expected_context"] = expected
    return normalized


def _agreement(checks: list[bool | None]) -> bool | None:
    return False if False in checks else True if all(value is True for value in checks) else None


def _context_binding(context: Any, expected: dict[str, Any], envelope: dict[str, Any]) -> bool | None:
    if not isinstance(context, dict):
        return None
    checks: list[bool | None] = []
    for field in ("same_project_instance", "same_mdb_instance", "boundary_agreement"):
        value = context.get(field)
        checks.append(value if type(value) is bool else None)
    for edge in ("before", "after"):
        sample = context.get(edge)
        for field in CONTEXT_FIELDS:
            value = sample.get(field) if isinstance(sample, dict) else None
            wanted = expected.get(field)
            checks.append(value == wanted if isinstance(value, str) and value.strip() and isinstance(wanted, str) and wanted.strip() else None)
        if isinstance(sample, dict):
            for field in ("current_element", "current_element_type"):
                value = sample.get(field)
                if not isinstance(value, str) or not value.strip():
                    checks.append(None)
                    continue
                try:
                    valid = _reference(value).startswith("=") if field == "current_element" else _kind(value) == value
                    checks.append(valid)
                except ValueError:
                    checks.append(False)
        for field in ("session_id", "bridge_build"):
            value = sample.get(field) if isinstance(sample, dict) else None
            wanted = envelope.get(field)
            checks.append(value == wanted if isinstance(value, str) and value.strip() and isinstance(wanted, str) and wanted.strip() else None)
    return _agreement(checks)


def _identity(identity: Any, request: dict[str, Any]) -> bool:
    if not isinstance(identity, dict):
        return False
    try:
        reference = _reference(identity.get("refno"))
        return (reference.startswith("=") and _kind(identity.get("type")) == request["expected_type"] and
                (reference == request["refno"] if request["refno"].startswith("=") else
                 isinstance(identity.get("name"), str) and identity["name"].casefold() == request["refno"].casefold()))
    except (ValueError, TypeError):
        return False


def _branch_coverage(data: Any, request: dict[str, Any], limit: int) -> bool:
    if not isinstance(data, dict) or not _identity(data.get("element"), request):
        return False
    rows, attrs = data.get("members"), data.get("attributes")
    if (not isinstance(rows, list) or len(rows) > limit or type(data.get("returned_count")) is not int or data["returned_count"] != len(rows) or
            type(data.get("member_limit")) is not int or data["member_limit"] != limit or data.get("has_more") is not False or data.get("truncated") is not False or
            type(data.get("member_probe_limit")) is not int or data["member_probe_limit"] != limit + 1 or
            type(data.get("member_probe_count")) is not int or data["member_probe_count"] != len(rows) + 1 or
            type(data.get("lookahead_probe_count")) is not int or data["lookahead_probe_count"] != int(len(rows) == limit) or
            not isinstance(data.get("read_errors"), list) or data["read_errors"] or not isinstance(attrs, dict)):
        return False
    seen = set()
    for ordinal, row in enumerate(rows, 1):
        try:
            reference, kind = _reference(row["refno"]), _kind(row["type"])
            key = reference, kind
            if not reference.startswith("=") or key in seen or type(row.get("sibling_index")) is not int or row["sibling_index"] != ordinal or row.get("complete") is not True:
                return False
            seen.add(key)
        except (ValueError, TypeError, KeyError):
            return False
    values = attrs.get("values")
    typed = attrs.get("typed_values")
    typed_valid = isinstance(typed, dict) and set(typed) <= set(ATTRIBUTES) and all(
        type(value) in (str, bool, int) or type(value) is float and math.isfinite(value) for value in typed.values())
    return (attrs.get("success") is True and attrs.get("refno") == data["element"]["refno"] and attrs.get("type") == "BRAN" and
            isinstance(values, dict) and set(values) == set(ATTRIBUTES) and all(isinstance(value, str) for value in values.values()) and
            attrs.get("errors") == {} and attrs.get("typed_read_errors") == {} and typed_valid)


def _pair_coverage(data: Any, request: dict[str, Any]) -> bool:
    if not isinstance(data, dict) or data.get("tolerance") != {"value": request["tolerance_mm"], "unit": "mm"}:
        return False
    endpoints = data.get("endpoints")
    if not isinstance(endpoints, dict):
        return False
    for side in ("first", "second"):
        endpoint = endpoints.get(side)
        wanted = request[side]
        if (not isinstance(endpoint, dict) or endpoint.get("complete") is not True or endpoint.get("status") != "exists" or
                endpoint.get("requested_refno") != wanted["refno"] or endpoint.get("requested_expected_type") != wanted["expected_type"] or
                type(endpoint.get("point_number")) is not int or endpoint["point_number"] != wanted["point_number"] or
                not _identity(endpoint.get("element"), wanted) or endpoint.get("errors") != {}):
            return False
    return all(isinstance(data.get(key), dict) for key in ("position_comparison", "direction_relationship", "pcref_correspondence"))


def _trace_coverage(data: Any, request: dict[str, Any]) -> bool:
    if not isinstance(data, dict):
        return False
    nodes, edges, starts = data.get("nodes"), data.get("edges"), data.get("starts")
    if (not isinstance(nodes, list) or not isinstance(edges, list) or not isinstance(starts, list) or
            len(starts) != len(request["starts"]) or not nodes or len(nodes) > request["max_nodes"] or len(edges) > len(nodes) or
            type(data.get("node_read_count")) is not int or not len(nodes) <= data["node_read_count"] <= request["max_nodes"] or
            type(data.get("returned_count")) is not int or data["returned_count"] != len(nodes) or
            type(data.get("max_nodes")) is not int or data["max_nodes"] != request["max_nodes"] or
            type(data.get("max_hops")) is not int or data["max_hops"] != request["max_hops"]):
        return False
    seen = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            return False
        try:
            key = _key({"refno": _reference(node["refno"]), "expected_type": _kind(node["type"]), "point_number": _integer(node["point_number"], 1, 10000)})
            _integer(node["hops"], 0, request["max_hops"])
        except (ValueError, TypeError, KeyError):
            return False
        endpoint = node.get("endpoint")
        point = endpoint.get("point") if isinstance(endpoint, dict) else None
        exists = endpoint.get("exists") if isinstance(endpoint, dict) else None
        if (key in seen or type(node.get("index")) is not int or node["index"] != index or node.get("identity_verified") is not True or
                node.get("expanded") is not True or node.get("read_complete") is not True or node.get("complete") is not True or
                not key[0].startswith("=")):
            return False
        if (not isinstance(endpoint, dict) or endpoint.get("status") != "exists" or endpoint.get("complete") is not True or endpoint.get("errors") != {} or
                not _identity(endpoint.get("element"), {"refno": node["refno"], "expected_type": node["type"]}) or
                type(endpoint.get("point_number")) is not int or endpoint["point_number"] != node["point_number"] or
                not isinstance(exists, dict) or exists.get("available") is not True or exists.get("value") is not True or
                not isinstance(point, dict) or point.get("complete") is not True or type(point.get("number")) is not int or point["number"] != node["point_number"]):
            return False
        seen.add(key)
    for index, start in enumerate(starts):
        if not isinstance(start, dict) or type(start.get("start_index")) is not int or start["start_index"] != index or start.get("requested") != request["starts"][index]:
            return False
        node_index = start.get("node_index")
        if type(node_index) is not int or not 0 <= node_index < len(nodes) or nodes[node_index]["hops"] != 0:
            return False
        node = nodes[node_index]
        if not _identity(node.get("endpoint", {}).get("element"), request["starts"][index]) or node["point_number"] != request["starts"][index]["point_number"]:
            return False
    sources = set()
    for edge in edges:
        if not isinstance(edge, dict):
            return False
        source, target = edge.get("from_index"), edge.get("to_index")
        if type(source) is not int or not 0 <= source < len(nodes) or source in sources:
            return False
        sources.add(source)
        link = nodes[source].get("endpoint", {}).get("point", {}).get("connection_reference", {})
        resolved = link.get("reference", {})
        if (link.get("available") is not True or link.get("resolution_complete") is not True or resolved.get("status") != "resolved" or
                (edge.get("target_refno"), edge.get("target_type"), edge.get("target_point_number")) !=
                (resolved.get("refno"), resolved.get("type"), link.get("point_number"))):
            return False
        if type(target) is not int or not 0 <= target < len(nodes) or edge.get("status") not in {"resolved", "target_already_visited"}:
            return False
        if (nodes[target]["refno"], nodes[target]["type"], nodes[target]["point_number"]) != (edge["target_refno"], edge["target_type"], edge["target_point_number"]):
            return False
    open_ends = []
    for index, node in enumerate(nodes):
        link = node["endpoint"]["point"].get("connection_reference")
        resolved = link.get("reference") if isinstance(link, dict) else None
        if not isinstance(link, dict) or link.get("available") is not True or link.get("resolution_complete") is not True or not isinstance(resolved, dict):
            return False
        if node.get("link_status") == "open_end" and resolved.get("status") == "unset" and index not in sources:
            open_ends.append(index)
        elif node.get("link_status") != "resolved" or resolved.get("status") != "resolved" or index not in sources:
            return False
    distances = {start["node_index"]: 0 for start in starts}
    frontier = list(distances)
    for source in frontier:
        for edge in edges:
            if edge["from_index"] == source and edge["to_index"] not in distances:
                distances[edge["to_index"]] = distances[source] + 1
                frontier.append(edge["to_index"])
    if len(distances) != len(nodes) or any(node["hops"] != distances[index] for index, node in enumerate(nodes)):
        return False
    return (data.get("truncated") is False and data.get("has_more") is False and data.get("scope_complete") is True and
            data.get("open_end_indices") == open_ends and data.get("unresolved_indices") == [] and data.get("read_errors") == [])


def _cell(value: Any) -> str:
    return str(value if value is not None else "unknown").replace("|", "\\|").replace("\r", " ").replace("\n", " ")[:256]


def _endpoint_label(endpoint: Any) -> str:
    if not isinstance(endpoint, dict):
        return "unknown"
    return f"{_cell(endpoint.get('refno'))} [{_cell(endpoint.get('expected_type'))}] P{_cell(endpoint.get('point_number'))}"


def _stop_native_fault(value: Any) -> None:
    """Inspect every native dict/list layer before copying or reporting it."""
    pending, seen = [value], set()
    while pending:
        item = pending.pop()
        if not isinstance(item, (dict, list)) or id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, dict):
            code = item.get("error_code")
            if (any(item.get(key) is True for key in ("has_fault", "native_fault", "native_fault_latched")) or
                    isinstance(code, str) and code in {"native_fault", "native_database_fault"}):
                raise BridgeError("A native database fault stopped connectivity observation; do not retry the read.",
                                  diagnostics=value if isinstance(value, dict) else {"native_evidence": value})
            pending.extend(item.values())
        else:
            pending.extend(item)


def connectivity_snapshot_report(reply: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Validate binding/coverage before reporting; retain raw partial evidence."""
    _stop_native_fault(reply)
    evidence = deepcopy(reply) if isinstance(reply, dict) else {}
    native = evidence.get("result")
    result = native if isinstance(native, dict) else {}
    issues: list[str] = []
    context = result.get("context")
    baseline = context.get("before", {}) if isinstance(context, dict) else {}
    baseline = baseline if isinstance(baseline, dict) else {}
    binding = _context_binding(context, baseline, evidence)
    if "expected_context" in request:
        binding = _agreement([binding, _context_binding(context, request["expected_context"], evidence)])
    if (evidence.get("ok") is not True or evidence.get("version") != "E3D21" or
            result.get("operation") != "piping_connectivity_snapshot" or result.get("read_only") is not True or
            result.get("single_dispatch") is not True or result.get("atomic_snapshot") is not False or
            result.get("available") is not True):
        issues.append("native_envelope_or_scope_unverified")
        binding = _agreement([binding, None if evidence.get("version") is None else False])
    if (type(result.get("success")) is not bool or type(result.get("complete")) is not bool or
            result.get("success") is not result.get("complete")):
        issues.append("native_success_complete_inconsistent")
    if binding is not True:
        issues.append("batch_context_unverified")
    groups = {"branches": [], "pairs": [], "trace": []}
    for group, kind in (("branches", "branch"), ("pairs", "pair"), ("trace", "trace")):
        raw = result.get(group)
        rows = [raw] if group == "trace" and raw is not None else [] if group == "trace" else raw
        expected = [{"starts": request["starts"], "max_hops": request["max_hops"], "max_nodes": request["max_nodes"]}] if group == "trace" and request["starts"] else [] if group == "trace" else request[group]
        if not isinstance(rows, list) or len(rows) != len(expected):
            issues.append(group + "_coverage_unverified")
            rows = rows if isinstance(rows, list) else []
        for index, wanted in enumerate(expected):
            item = rows[index] if index < len(rows) and isinstance(rows[index], dict) else {}
            item_binding = _context_binding(item.get("context"), baseline, evidence)
            item_binding = _agreement([binding, item_binding, item.get("matches_batch_context") if type(item.get("matches_batch_context")) is bool else None])
            data = item.get("data")
            try:
                coverage = (_branch_coverage(data, wanted, request["member_limit"]) if group == "branches" else
                            _pair_coverage(data, wanted) if group == "pairs" else _trace_coverage(data, request))
            except (ValueError, TypeError, KeyError, AttributeError):
                coverage = False
            complete = (item.get("kind") == kind and type(item.get("index")) is int and item["index"] == index and
                        item.get("requested") == wanted and item.get("read_complete") is True and item.get("complete") is True and
                        item.get("read_errors") == [] and coverage and item_binding is True)
            groups[group].append({"index": index, "complete": complete, "binding": item_binding,
                                  "read_coverage_verified": coverage, "requested": wanted, "data": data})
            if not complete:
                issues.append(f"{group}[{index}]_incomplete")
    complete = not issues and result.get("complete") is True and result.get("success") is True and result.get("read_errors") == []
    lines = [f"Connectivity evidence: {'complete' if complete else 'partial or unverified'}.",
             "Engineering connectivity and compatibility: not assessed."]
    if groups["branches"]:
        lines += ["", "| BRAN | Members returned | Member probes (incl. lookahead) | HREF (native) | TREF (native) | Coverage |", "|---|---:|---:|---|---|---|"]
        for item in groups["branches"]:
            data = item["data"] if isinstance(item["data"], dict) else {}
            values = data.get("attributes", {}).get("values", {}) if isinstance(data.get("attributes"), dict) else {}
            values = values if isinstance(values, dict) else {}
            lines.append(f"| {_cell(item['requested']['refno'])} | {_cell(data.get('returned_count'))}/{request['member_limit']} | {_cell(data.get('member_probe_count'))}/{request['member_limit'] + 1} | {_cell(values.get('HREF'))} | {_cell(values.get('TREF'))} | {'complete' if item['complete'] else 'partial/unverified'} |")
    if groups["pairs"]:
        lines += ["", "| Selected P-points | Position evidence | PCREF evidence | Coverage |", "|---|---|---|---|"]
        for item in groups["pairs"]:
            data = item["data"] if isinstance(item["data"], dict) else {}
            position = data.get("position_comparison", {})
            pcref = data.get("pcref_correspondence", {})
            lines.append(f"| {_endpoint_label(item['requested']['first'])} → {_endpoint_label(item['requested']['second'])} | {_cell(position.get('status') if isinstance(position, dict) else None)} | {_cell(pcref.get('status') if isinstance(pcref, dict) else None)} | {'complete' if item['complete'] else 'partial/unverified'} |")
    for item in groups["trace"]:
        data = item["data"] if isinstance(item["data"], dict) else {}
        lines += ["", f"Directed PCREF trace: {_cell(data.get('returned_count'))} nodes; {_cell(data.get('node_read_count'))}/{request['max_nodes']} endpoint inspections; max_hops={request['max_hops']} (starts are hop 0).",
                  f"Open ends: {_cell(data.get('open_end_indices'))}; unresolved: {_cell(data.get('unresolved_indices'))}; truncated: {_cell(data.get('truncated'))}.",
                  "No links between different P-points of a component are implied. This explicit scope may contain several disconnected sets."]
        nodes = data.get("nodes")
        edges = data.get("edges")
        if isinstance(nodes, list) and nodes:
            lines += ["", "| Node | Typed P-point | Hops | Native link | Endpoint read |", "|---:|---|---:|---|---|"]
            for node in nodes[:request["max_nodes"]]:
                if not isinstance(node, dict):
                    continue
                label = _endpoint_label({"refno": node.get("refno"), "expected_type": node.get("type"), "point_number": node.get("point_number")})
                coverage = "complete" if node.get("complete") is True and item["binding"] is True else "partial/unverified"
                lines.append(f"| {_cell(node.get('index'))} | {label} | {_cell(node.get('hops'))} | {_cell(node.get('link_status'))} | {coverage} |")
        if isinstance(edges, list) and edges:
            lines += ["", "| From node | Native PCREF target | Trace status | Reverse reference | Cycle observed |", "|---:|---|---|---|---|"]
            for edge in edges[:request["max_nodes"]]:
                if not isinstance(edge, dict):
                    continue
                label = _endpoint_label({"refno": edge.get("target_refno"), "expected_type": edge.get("target_type"), "point_number": edge.get("target_point_number")})
                lines.append(f"| {_cell(edge.get('from_index'))} | {label} | {_cell(edge.get('status'))} | {_cell(edge.get('relationship'))} | {_cell(edge.get('cycle'))} |")
    if issues:
        lines += ["", "Unverified items: " + ", ".join(issues) + "."]
    for rows in groups.values():
        for item in rows:
            item.pop("data", None)
    return {"complete": complete, "context_binding": binding, "issues": issues, "items": groups,
            "engineering_connectivity": "not_assessed", "markdown": "\n".join(lines)}


def piping_connectivity_snapshot(bridge: Any, args: Any) -> dict[str, Any]:
    request = connectivity_snapshot_parameters(args)
    version = bridge._selected_version(None)
    if version != "E3D21" or bridge.settings.mode != "bridge":
        return {"ok": True, "version": version, "result": {"operation": "piping_connectivity_snapshot",
            "success": False, "available": False, "complete": False, "read_only": True,
            "read_errors": [{"code": "native_unavailable", "message": "Requires the E3D21 native connectivity-snapshot route."}]}}
    # BridgeError (including native-fault diagnostics) propagates unchanged:
    # no report rewrite, retry, fallback or diagnostic follow-up is attempted.
    reply = bridge._request("POST", PATH, request, version="E3D21")
    if _native_fault(reply):
        raise BridgeError("A native database fault stopped the connectivity snapshot; do not retry.", diagnostics=reply)
    _stop_native_fault(reply)
    report = connectivity_snapshot_report(reply, request)
    output = deepcopy(reply) if isinstance(reply, dict) else {"ok": False, "version": version}
    result = output.get("result")
    if not isinstance(result, dict):
        result = {}; output["result"] = result
    result["native_success"] = result.get("success")
    result["native_complete"] = result.get("complete")
    result["complete"] = result["success"] = report["complete"]
    result["report"] = report
    return output


def register_connectivity_snapshot(server: Any, bridge: Any) -> None:
    """Opt-in registration for the main task's public catalog integration."""
    endpoint = {"type": "object", "properties": {
        "refno": {"type": "string", "minLength": 2, "maxLength": 512},
        "expected_type": {"type": "string", "minLength": 1, "maxLength": 64},
        "point_number": {"type": "integer", "minimum": 1, "maximum": 10000}},
        "required": ["refno", "expected_type", "point_number"], "additionalProperties": False}
    server.add_tool(TOOL,
        "Read a bounded batch of explicit E3D21 BRAN states, typed P-point pairs and one directed PCREF trace in one native UI callback. "
        "Each item keeps read coverage and session/build/project/MDB/typed-CE binding. max_nodes counts trace endpoint inspections, "
        "including alias resolutions; pair endpoints and branch members have independent disclosed bounds. Starts are hop 0. "
        "No traversal between component P-points, geometric edges or engineering connectivity/compatibility verdict is inferred. "
        "Inspect report, each complete flag, read_errors and truncated; one callback is not a database lock.",
        {"type": "object", "properties": {
            "branches": {"type": "array", "maxItems": 8, "default": [], "items": {"type": "object", "properties": {
                "refno": {"type": "string", "minLength": 2, "maxLength": 512}, "expected_type": {"type": "string", "enum": ["BRAN"]}},
                "required": ["refno", "expected_type"], "additionalProperties": False}},
            "pairs": {"type": "array", "maxItems": 16, "default": [], "items": {"type": "object", "properties": {
                "first": endpoint, "second": endpoint, "tolerance_mm": {"type": "number", "minimum": 0, "maximum": 1000000}},
                "required": ["first", "second", "tolerance_mm"], "additionalProperties": False}},
            "starts": {"type": "array", "maxItems": 16, "default": [], "items": endpoint},
            "member_limit": {"type": "integer", "minimum": 1, "maximum": 64, "default": 64},
            "max_hops": {"type": "integer", "minimum": 0, "maximum": 16, "default": 8},
            "max_nodes": {"type": "integer", "minimum": 1, "maximum": 128, "default": 64},
            "expected_context": {"type": "object", "properties": {field: {"type": "string", "minLength": 1, "maxLength": 512} for field in CONTEXT_FIELDS},
                                 "required": list(CONTEXT_FIELDS), "additionalProperties": False}},
         "additionalProperties": False}, lambda args: piping_connectivity_snapshot(bridge, args), read_only=True)
