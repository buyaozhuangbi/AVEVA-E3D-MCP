"""Guarded native PIPE/BRAN/ELBO creation through the existing PML journal.

Only generated database commands are admitted. The macro never connects to,
copies, deletes or edits an existing Design element. Native TUBI are implicit.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import math
import re
from threading import Lock
from typing import Any
from uuid import uuid4

from .bridge import BridgeClient, BridgeError, element_access_refnos, pml_script_request_id
from .context_snapshot import context_snapshot
from .pipe_member_manifest import pipe_member_manifest
from .paragon_reference_trace import paragon_reference_trace
from .piping_centerline import _cross, _dot, _native_vector, _sub, _unit, centerline_options, centerline_schema, plan_centerline
from .piping_route_plan import _endpoints, _identity, _source_binding
from .protocol import McpServer


TOOL = "e3d_piping_route_create"
VERIFY_TOOL = "e3d_piping_route_verify"
_NAME = re.compile(r"/[A-Za-z][A-Za-z0-9_/-]{0,34}")
_REF = re.compile(r"=[1-9][0-9]{0,9}/(?:0|[1-9][0-9]{0,9})")
_SAFE_NAME = re.compile(r"/[A-Za-z0-9_./-]{1,255}")
_REQUIRED = {"parent_refno", "pipe_name", "template_branch_refno", "template_elbow_refno", "endpoints", "centerline"}
_EXECUTION = {"dry_run", "expected_revision", "expected_session_id", "request_id"}
_VERIFICATION = {"pipe_refno", "expected_session_id", "expected_plan_revision"}
MAX_BENDS = 16


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _numeric(value: Any) -> str:
    _require(isinstance(value, str) and bool(_REF.fullmatch(value)), "Explicit canonical numeric REF required")
    return element_access_refnos([value])[0]


def _parameters(args: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    _require(isinstance(args, dict) and not set(args) - _REQUIRED - _EXECUTION and _REQUIRED <= set(args),
             "Native route requires explicit parent, name, templates, endpoints and centerline")
    dry_run = args.get("dry_run", True)
    _require(type(dry_run) is bool, "dry_run must be a boolean")
    request = {key: deepcopy(args[key]) for key in _REQUIRED}
    for key in ("parent_refno", "template_branch_refno", "template_elbow_refno"):
        request[key] = _numeric(request[key])
    _require(isinstance(request["pipe_name"], str) and bool(_NAME.fullmatch(request["pipe_name"]))
             and "//" not in request["pipe_name"] and not request["pipe_name"].endswith("/"),
             "pipe_name must be one ASCII /NAME of at most 36 characters, without empty path segments")
    request["endpoints"] = _endpoints(request["endpoints"])
    _require(len(request["endpoints"]) == 2, "Exactly two explicit endpoints are required")
    for endpoint in request["endpoints"]:
        endpoint["refno"] = _numeric(endpoint["refno"])
    request["centerline"] = centerline_options(request["centerline"], 2)
    _require(0.01 <= request["centerline"]["position_tolerance_mm"] <= 1,
             "Native route position_tolerance_mm must be from 0.01 through 1 mm")
    for field in ("expected_revision", "request_id", "expected_session_id"):
        if field in args:
            _require(isinstance(args[field], str) and bool(args[field]) and len(args[field]) <= 256, field + " must be explicit text")
    if "expected_revision" in args:
        _require(bool(re.fullmatch(r"[0-9a-f]{64}", args["expected_revision"])), "expected_revision must be 64 lowercase hex characters")
    if "request_id" in args:
        _require(args["request_id"] == pml_script_request_id(args["request_id"]), "request_id must be a canonical UUID")
    if not dry_run:
        _require({"expected_revision", "expected_session_id", "request_id"} <= set(args),
                 "Execution requires the reviewed revision, session ID and a caller-owned request UUID")
    return request, dry_run


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _plan_revision(request: dict[str, Any], state: dict[str, Any]) -> str:
    """Bind the intended geometry, sources and session independently of CE/order.

    This is a comparison fingerprint, not authentication or creation provenance.
    The ordinary create revision still binds the complete prewrite state.
    """
    return _hash({"format": 1, "request": request,
        "context": {key: value for key, value in state["context"].items() if key != "current_element"},
        "references": {key: state[key]["refno"] for key in ("spec", "tube_spec", "elbow_spec", "catalogue")},
        "template": state["template"], "endpoint_points": state["endpoint_points"], "bore_mm": state["bore_mm"],
        "elements": state["centerline"]["elements"], "elbows": state["elbows"],
        "start_tangent": state["start_tangent"], "end_tangent": state["end_tangent"]})


def _result(reply: Any) -> dict[str, Any]:
    _require(isinstance(reply, dict) and reply.get("ok") is True and reply.get("version") == "E3D21"
             and isinstance(reply.get("result"), dict) and reply["result"].get("success") is True,
             "Native E3D21 response did not report success")
    return reply["result"]


def _script_completed(reply: Any, result: dict[str, Any], source_hash: str, marker: str) -> None:
    native = reply.get("result") if isinstance(reply, dict) else None
    result["outcome_uncertain"] = (not isinstance(native, dict)
        or reply.get("ok") is not True or reply.get("version") != "E3D21"
        or native.get("outcome_uncertain") is not False or native.get("source_sha256") != source_hash
        or native.get("success") is True and (native.get("execution_completed") is not True
                                             or native.get("native_run_returned") is not True))
    native = _result(reply)
    _require(native.get("execution_completed") is True and native.get("native_run_returned") is True
             and result["outcome_uncertain"] is False, "Exact native script completion unverified")
    _require(any(isinstance(message, dict) and message.get("description") == marker
                 for message in native.get("messages", [])), "Native route completion marker was not observed")


def _context(bridge: BridgeClient) -> dict[str, Any]:
    observed = _identity(context_snapshot(bridge, bridge.settings))
    _require(observed["complete"] is True, "Ready native session/project/MDB/CE evidence required")
    return observed


def _attributes(bridge: BridgeClient, refno: str, kind: str, names: list[str]) -> dict[str, Any]:
    observed = _result(bridge.read_attributes(refno, names, expected_type=kind))
    _require(observed.get("refno") == refno and observed.get("type") == kind and not observed.get("errors")
             and not observed.get("typed_read_errors") and set(observed.get("values", {})) == set(names),
             "Native attribute identity or read coverage changed: " + refno)
    return {"values": observed["values"], "typed_values": observed.get("typed_values", {})}


def _millimetres(attributes: dict[str, Any], key: str) -> float:
    # The generic SDK DOUBLE reader retains native units. Require independent
    # formatted mm evidence before interpreting its scalar as millimetres.
    text = attributes["values"].get(key)
    value = attributes["typed_values"].get(key)
    match = re.fullmatch(r"([-+]?[0-9]+(?:\.[0-9]+)?)mm", text) if isinstance(text, str) else None
    _require(match is not None and type(value) in (int, float) and math.isfinite(value)
             and abs(value - float(match[1])) <= 0.005, "Native scalar millimetre unit evidence unavailable: " + key)
    return float(value)


def _query(bridge: BridgeClient, refno: str, kind: str | None = None) -> dict[str, Any]:
    _require(bool(_REF.fullmatch(refno) or _SAFE_NAME.fullmatch(refno)), "Unsafe or unresolved native reference text")
    reply = bridge.query(refno, 2)
    _require(isinstance(reply, dict) and reply.get("ok") is True and reply.get("version") == "E3D21"
             and reply.get("count") == 1 and isinstance(reply.get("elements"), list) and len(reply["elements"]) == 1,
             "Native reference must resolve uniquely: " + refno)
    row = reply["elements"][0]
    _numeric(row.get("refno"))
    _require((kind is None or row.get("type") == kind) and
             (row["refno"] == refno if refno.startswith("=") else
              isinstance(row.get("name"), str) and row["name"].lstrip("/").casefold() == refno.lstrip("/").casefold()),
             "Resolved reference identity/type differs: " + refno)
    return {key: row.get(key) for key in ("refno", "type", "name")}


def _spec_ancestor(bridge: BridgeClient, identity: dict[str, Any], before: dict[str, Any]) -> str:
    reply = paragon_reference_trace(bridge, {"refno": identity["refno"], "trace_limit": 32})
    _require(reply.get("ok") is True and reply.get("version") == "E3D21" and
             all(reply.get(key) == before["identity"][key] for key in ("session_id", "bridge_build")),
             "Specification owner trace envelope changed")
    native = reply.get("result", {})
    context = native.get("context", {})
    _require(all(context.get(key) is True for key in ("same_project_instance", "same_mdb_instance", "boundary_agreement")),
             "Specification owner trace instance boundary unverified")
    for sample in (context.get("before", {}), context.get("after", {})):
        _require(all(sample.get(key) == before["identity"][key] for key in ("session_id", "bridge_build", "project", "mdb"))
                 and sample.get("current_element", {}).get("refno") == before["identity"]["current_element"],
                 "Specification owner trace context changed")
    trace = native.get("trace", {})
    seen = set()
    path, expected = "root", identity["refno"]
    for _ in range(10):
        current = trace.get(path, {})
        _require(current.get("status") == "resolved" and current.get("ref") == expected
                 and native.get("db_type_codes", {}).get(path) == 2, "Specification owner chain incomplete")
        _require(expected not in seen, "Cyclic specification ownership")
        seen.add(expected)
        if current.get("type") == "SPEC":
            return expected
        _require(current.get("type") in {"SPCO", "SELE"}, "Unexpected specification owner type")
        expected = _numeric(current.get("owner"))
        path += ".OWNER"
    raise ValueError("Specification owner traversal limit reached")


def _members(bridge: BridgeClient, refno: str, kind: str) -> dict[str, Any]:
    native = _result(bridge.list_members(refno, "", 200, expected_type=kind))
    parent, members = native.get("parent"), native.get("members")
    _require(isinstance(parent, dict) and parent.get("refno") == refno and parent.get("type") == kind
             and isinstance(members, list) and native.get("has_more") is False and native.get("next_cursor") is None
             and not native.get("read_errors") and native.get("returned_count") == len(members) <= 200,
             "Complete bounded native parent membership required")
    rows = []
    for index, row in enumerate(members, 1):
        _numeric(row.get("refno"))
        _require(row.get("sibling_index") == index and isinstance(row.get("type"), str), "Invalid member sequence")
        rows.append({key: row.get(key) for key in ("refno", "type", "name", "sibling_index")})
    return {"parent": parent, "members": rows}


def _pose(bridge: BridgeClient, refno: str, before: dict[str, Any]) -> dict[str, Any]:
    reply = bridge.spatial_info(refno, "WORLD")
    native = _result(reply)
    # Spatial and connection readers use the same native endpoint context layer.
    binding = _source_binding({**reply, "result": {**native, "element": {"refno": native.get("refno"), "type": native.get("type")}}},
                              before, before, source_kind="connection_points", requested_refno=refno, requested_type="ELBO")
    _require(binding["matches_enclosing_context"] is True and native.get("complete") is True
             and native.get("refno") == refno and native.get("type") == "ELBO"
             and native.get("frame", {}).get("requested") == "WORLD", "Template WORLD pose unverified")
    position, orientation = native.get("position", {}), native.get("orientation", {})
    _require(position.get("unit") == "mm" and position.get("available") is True and orientation.get("available") is True,
             "Template pose unavailable")
    axes = [orientation.get(axis) for axis in ("x_axis", "y_axis", "z_axis")]
    xyz = [position.get(axis) for axis in ("x", "y", "z")]
    _require(all(isinstance(vector, list) and len(vector) == 3 and all(type(v) in (int, float) and math.isfinite(v) for v in vector)
                 for vector in [xyz, *axes]), "Template pose contains invalid vectors")
    _require(all(abs(math.hypot(*axis) - 1) < 1e-6 for axis in axes)
             and math.dist(_cross(axes[0], axes[1]), axes[2]) < 1e-6, "Template pose is not right-handed orthonormal")
    return {"position": xyz, "axes": axes}


def _points(bridge: BridgeClient, endpoint: dict[str, Any], before: dict[str, Any], numbers: list[int]) -> dict[str, Any]:
    reply = bridge.connection_points(endpoint["refno"], "WORLD", numbers, expected_type=endpoint["expected_type"])
    native = _result(reply)
    binding = _source_binding(reply, before, before, source_kind="connection_points",
        requested_refno=endpoint["refno"], requested_type=endpoint["expected_type"], requested_point=endpoint.get("point_number"))
    _require(binding["matches_enclosing_context"] is True, "P-point context or typed identity unverified")
    # Use the centreline validator for independent PPEXST/PPOS/PDIR coverage.
    return {**endpoint, "response": reply, "binding": binding}


def _point(group: dict[str, Any], number: int) -> dict[str, Any]:
    rows = [row for row in group["response"]["result"].get("points", []) if row.get("number") == number]
    _require(len(rows) == 1, "Native P-point must be observed exactly once")
    row = rows[0]
    _require(row.get("exists", {}).get("value") is True and row["exists"].get("available") is True
             and row["exists"].get("attribute") == "PPEXST" and not row["exists"].get("error"), "Native P-point does not exist")
    for key in ("position", "direction", "bore"):
        _require(row.get(key, {}).get("available") is True and not row[key].get("error"), "Native P-point " + key + " unavailable")
    bore = row["bore"]
    _require(_native_vector(row["position"], position=True) is not None
             and _native_vector(row["direction"], position=False) is not None
             and not row.get("errors"), "Native P-point field evidence incomplete")
    _require(bore.get("unit") == "mm" and type(bore.get("value_mm")) in (int, float)
             and math.isfinite(bore["value_mm"]) and bore["value_mm"] > 0, "Native nominal bore in mm required")
    return {"number": number, "position": [row["position"][axis] for axis in ("x", "y", "z")],
            "direction": [row["direction"][axis] for axis in ("x", "y", "z")], "bore_mm": bore["value_mm"]}


def _template_bend(pose: dict[str, Any], first: dict[str, Any], second: dict[str, Any], tolerance: float) -> dict[str, Any]:
    u, v = [-x for x in first["direction"]], second["direction"]
    _require(abs(_dot(u, v)) < 1e-6 and abs(math.hypot(*u) - 1) < 1e-6 and abs(math.hypot(*v) - 1) < 1e-6,
             "Only native two-port 90 degree elbow templates are supported")
    delta = _sub(second["position"], first["position"])
    arrive, leave = _dot(delta, u), _dot(delta, v)
    _require(arrive > 0 and leave > 0 and abs(arrive - leave) <= tolerance
             and math.dist(delta, [arrive * u[i] + leave * v[i] for i in range(3)]) <= tolerance,
             "Template P-points do not describe a symmetric circular elbow")
    return {"radius_mm": (arrive + leave) / 2, "u": u, "v": v, "n": _cross(u, v),
            "first": first, "second": second, "pose": pose}


def _place(template: dict[str, Any], bend: dict[str, Any], name: str) -> dict[str, Any]:
    _require(abs(bend["turn_degrees"] - 90) <= 1e-6, "Native route currently supports 90 degree turns only")
    source = [template[key] for key in ("u", "v", "n")]
    target = [bend[key] for key in ("incoming_direction", "outgoing_direction", "normal_world")]
    def rotate(vector):
        return [math.fsum(_dot(vector, source[j]) * target[j][i] for j in range(3)) for i in range(3)]
    displacement = rotate(_sub(template["pose"]["position"], template["first"]["position"]))
    return {"name": name, "position_world_mm": [bend["start_world_mm"][i] + displacement[i] for i in range(3)],
            "axes_world": [rotate(axis) for axis in template["pose"]["axes"]],
            "expected_points": [{"number": 1, "position": bend["start_world_mm"],
                                 "direction": [-v for v in bend["incoming_direction"]]},
                                {"number": 2, "position": bend["end_world_mm"], "direction": bend["outgoing_direction"]}]}


def _num(value: float) -> str:
    return format(float(value), ".9f").rstrip("0").rstrip(".") or "0"


def _position(vector: list[float]) -> str:
    return " ".join(axis + " " + _num(value) + "mm" for axis, value in zip(("E", "N", "U"), vector)) + " WRT /*"


def _direction(vector: list[float]) -> str:
    x, y, z = _unit(vector)
    for index, component in enumerate((x, y, z)):
        if abs(abs(component) - 1) < 1e-12 and all(abs(v) < 1e-12 for i, v in enumerate((x, y, z)) if i != index):
            return (("E", "W"), ("N", "S"), ("U", "D"))[index][component < 0]
    bearing = math.degrees(math.atan2(abs(x), abs(y)))
    elevation = math.degrees(math.atan2(abs(z), math.hypot(x, y)))
    return f"{'N' if y >= 0 else 'S'} {_num(bearing)} {'E' if x >= 0 else 'W'} {_num(elevation)} {'U' if z >= 0 else 'D'}"


def _branch_endpoint_guards(request: dict[str, Any], state: dict[str, Any]) -> list[str]:
    """Read only native WORLD head/tail values on the local !newbranch DBREF."""
    lines = []
    for key, expected in (("hpos", state["centerline"]["vertices_world_mm"][0]),
                          ("tpos", state["centerline"]["vertices_world_mm"][-1])):
        lines += [f"!actualpos = !newbranch.{key}", "!actualpos = !actualpos.wrt( /* )",
                  f"!expectedpos = object POSITION('{_position(expected)}')",
                  "!distance = !actualpos.distance(!expectedpos)",
                  f"if (!distance gt {_num(request['centerline']['position_tolerance_mm'])}) then",
                  "return error 1 'Native branch endpoint position mismatch'", "endif"]
    for key, expected in (("hdir", state["start_tangent"]), ("tdir", [-v for v in state["end_tangent"]])):
        lines += [f"!actualdir = !newbranch.{key}", "!actualdir = !actualdir.wrt( /* )",
                  f"!expecteddir = object DIRECTION('{_direction(expected)}')",
                  "!angle = !actualdir.angle(!expecteddir)", "if (!angle gt 0.001degree) then",
                  "return error 1 'Native branch endpoint direction mismatch'", "endif"]
    return lines


def _verification_macro(request: dict[str, Any], state: dict[str, Any], branch_refno: str) -> str:
    branch_refno = _numeric(branch_refno)
    lines = ["-- Generated read-only native piping endpoint verification.",
             f"var !kind TYPE OF {branch_refno}", "if (!kind ne 'BRAN') then",
             "return error 1 'Native verification branch type changed'", "endif",
             f"!s = '{branch_refno}'", "!newbranch = !s.dbref()"]
    return "\n".join(lines + _branch_endpoint_guards(request, state) + ["$p MCP_NATIVE_PIPE_ROUTE_VERIFICATION_COMPLETE", ""])


def _macro(request: dict[str, Any], state: dict[str, Any]) -> str:
    """All inserted text is numeric, fixed keywords, or restricted ASCII NAME."""
    name, parent = request["pipe_name"], request["parent_refno"]
    members = state["parent"]["members"]
    lines = ["-- Generated native piping route; no existing model setter is emitted.",
        f"!s = '{parent}'", "!p = !s.dbref()", "!m = !p.members", "!count = !m.size()", f"if (!count ne {len(members)}) then",
        "return error 1 'Destination membership changed'", "endif"]
    for index, row in enumerate(members, 1):
        lines += [f"!member = !m[{index}]", "!ref = !member.string()", f"if (!ref ne '{row['refno']}') then",
                  "return error 1 'Destination member order changed'", "endif"]
    for target, kind in ((parent, "ZONE"), (request["template_branch_refno"], "BRAN"), (request["template_elbow_refno"], "ELBO")):
        lines += [f"var !kind TYPE OF {target}", f"if (!kind ne '{kind}') then", "return error 1 'Native type changed'", "endif"]
    for target, attribute, expected in (
        (request["template_branch_refno"], "PSPE", state["spec"]["refno"]),
        (request["template_branch_refno"], "HSTU", state["tube_spec"]["refno"]),
        (request["template_elbow_refno"], "SPRE", state["elbow_spec"]["refno"]),
        (request["template_elbow_refno"], "LSTU", state["tube_spec"]["refno"]),
        (request["template_elbow_refno"], "CATR", state["catalogue"]["refno"]),
    ):
        lines += [f"var !observedref REFNO OF {attribute} OF {target}", f"if (!observedref ne '{expected}') then",
                  "return error 1 'Native template specification changed'", "endif"]
    guarded_points = [(endpoint["refno"], observed) for endpoint, observed in zip(request["endpoints"], state["endpoint_points"])]
    guarded_points += [(request["template_elbow_refno"], state["template"][key]) for key in ("first", "second")]
    for target, point in guarded_points:
        lines += [f"!s = '{target}'", "!source = !s.dbref()", f"!actualpos = !source.ppos[{point['number']}]",
                  "!actualpos = !actualpos.wrt( /* )", f"!expectedpos = object POSITION('{_position(point['position'])}')",
                  "!distance = !actualpos.distance(!expectedpos)",
                  f"if (!distance gt {_num(request['centerline']['position_tolerance_mm'])}) then",
                  "return error 1 'Native anchor or template point moved'", "endif",
                  f"!actualdir = !source.pdir[{point['number']}]", "!actualdir = !actualdir.wrt( /* )",
                  f"!expecteddir = object DIRECTION('{_direction(point['direction'])}')",
                  "!angle = !actualdir.angle(!expecteddir)", "if (!angle gt 0.001degree) then",
                  "return error 1 'Native anchor or template direction changed'", "endif"]
    # NEW ... /NAME also enforces database-wide name uniqueness before each
    # new element is configured; no wildcard lookup or existing-element reuse.
    lines += [parent, f"NEW PIPE {name}", f"PSPEC {state['spec']['refno']}",
              f"NEW BRAN {name}/B1", f"PSPEC {state['spec']['refno']}", f"HSTU {state['tube_spec']['refno']}",
              "HREF =0", "TREF =0", f"HBOR {_num(state['bore_mm'])}mm", f"TBOR {_num(state['bore_mm'])}mm",
              "HCON OPEN", "TCON OPEN", "LHEAD TRUE", "LTAIL TRUE",
              "HPOS " + _position(state["centerline"]["vertices_world_mm"][0]),
              "TPOS " + _position(state["centerline"]["vertices_world_mm"][-1]),
              "HDIR " + _direction(state["start_tangent"]) + " WRT /*",
              "TDIR " + _direction([-v for v in state["end_tangent"]]) + " WRT /*", "!newbranch = !!ce"]
    for item in state["elbows"]:
        lines += [f"NEW ELBO {item['name']}", f"SPREF {state['elbow_spec']['refno']}",
                  f"LSTU {state['tube_spec']['refno']}", "ANGLE 90degree",
                  "POS " + _position(item["position_world_mm"]),
                  "ORI X IS " + _direction(item["axes_world"][0]) + " AND Y IS " + _direction(item["axes_world"][1]) + " WRT /*"]
    lines += _branch_endpoint_guards(request, state)
    lines += [name, "$p MCP_NATIVE_PIPE_ROUTE_COMPLETE"]
    return "\n".join(lines) + "\n"


class NativePipingRouteCreator:
    """One bounded in-process admission table plus the native PML request journal."""
    def __init__(self, bridge: BridgeClient):
        self.bridge = bridge
        self._lock = Lock()
        self._requests: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def _prepare(self, request: dict[str, Any], *, existing_pipe_refno: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        bridge = self.bridge
        before = _context(bridge)
        parent = _members(bridge, request["parent_refno"], "ZONE")
        matching = [row for row in parent["members"] if isinstance(row.get("name"), str)
                    and row["name"].lstrip("/").casefold() == request["pipe_name"].lstrip("/").casefold()]
        if existing_pipe_refno is None:
            _require(len(parent["members"]) < 200, "Destination must leave one member slot within the 200-member readback limit")
            schema = _result(bridge.element_schema(request["parent_refno"], ["NAME"], 0, 1, expected_type="ZONE"))
            _require(schema.get("element", {}).get("refno") == request["parent_refno"]
                     and schema.get("element", {}).get("type") == "ZONE" and not schema.get("read_errors"),
                     "Native parent schema identity/coverage unverified")
            _require(any(row.get("short_name") == "PIPE" and row.get("sdk_creatable_now") is True
                         for row in schema.get("child_types", [])), "Native parent does not currently admit PIPE")
            _require(not matching, "Destination PIPE name already exists")
        else:
            _require(len(matching) == 1 and matching[0]["refno"] == existing_pipe_refno and matching[0]["type"] == "PIPE",
                     "Explicit verification PIPE/name does not belong to the requested ZONE")
        branch = _attributes(bridge, request["template_branch_refno"], "BRAN", ["PSPE", "HSTU"])
        spec = _query(bridge, branch["values"]["PSPE"], "SPEC")
        tube = _query(bridge, branch["values"]["HSTU"], "SPCO")
        elbow_attrs = _attributes(bridge, request["template_elbow_refno"], "ELBO", ["SPRE", "LSTU", "ANGL", "RADI", "CREF"])
        _require(elbow_attrs["values"]["CREF"].casefold() == "nulref", "Linked elbow templates are not supported")
        _require(abs(elbow_attrs["typed_values"].get("ANGL", -1) - 90) < 1e-6
                 and elbow_attrs["typed_values"].get("RADI") == 0, "Only fixed-catalogue 90 degree ELBO templates are supported")
        elbow_spec = _query(bridge, elbow_attrs["values"]["SPRE"], "SPCO")
        _require(_query(bridge, elbow_attrs["values"]["LSTU"], "SPCO") == tube,
                 "Template leave-tube specification differs from the branch HSTU")
        _require(all(_spec_ancestor(bridge, identity, before) == spec["refno"] for identity in (tube, elbow_spec)),
                 "Tube/elbow SPCO do not belong to the selected branch SPEC")
        source_tube = _attributes(bridge, request["template_branch_refno"], "TUBI", ["SPRE", "ABOR", "LBOR"])
        _require(_query(bridge, source_tube["values"]["SPRE"], "SPCO")["refno"] == tube["refno"],
                 "Template branch implicit tube differs from HSTU")
        endpoint_groups = [_points(bridge, ep, before, [ep["point_number"]]) for ep in request["endpoints"]]
        centerline = plan_centerline(request["centerline"], endpoint_groups)
        _require(centerline["complete"] is True and centerline["geometric_fit"] is True,
                 "Centreline is blocked or native endpoint position/direction is unverified")
        bends = centerline["bend_candidates"]
        _require(len(bends) <= MAX_BENDS, "Native route is limited to 16 elbows")
        # Keep at least one explicit straight run before, between and after
        # elbows, giving an unambiguous expected native TUBI/member sequence.
        _require(len(centerline["elements"]) == 2 * len(bends) + 1
                 and all(item["kind"] == ("straight" if index % 2 == 0 else "circular_bend")
                         for index, item in enumerate(centerline["elements"])), "Native route requires positive straight runs between all bends")
        template_group = _points(bridge, {"refno": request["template_elbow_refno"], "expected_type": "ELBO"}, before, [1, 2])
        _require(not _members(bridge, request["template_elbow_refno"], "ELBO")["members"],
                 "Native elbow template must have no explicit child overrides")
        p1, p2 = _point(template_group, 1), _point(template_group, 2)
        pose = _pose(bridge, request["template_elbow_refno"], before)
        template = _template_bend(pose, p1, p2, request["centerline"]["position_tolerance_mm"])
        _require(not bends or abs(template["radius_mm"] - request["centerline"]["bend_radius_mm"]) <= request["centerline"]["position_tolerance_mm"],
                 "Requested radius differs from measured native catalogue elbow radius")
        endpoint_points = [_point(group, group["point_number"]) for group in endpoint_groups]
        bore = p1["bore_mm"]
        _require(all(abs(point["bore_mm"] - bore) < 1e-6 for point in [p2, *endpoint_points]),
                 "Endpoint and template nominal bores differ")
        _require(all(abs(_millimetres(source_tube, key) - bore) < 1e-6 for key in ("ABOR", "LBOR")),
                 "Native tube specification nominal bore differs from endpoints")
        references = template_group["response"]["result"].get("references", {})
        _require(references.get("SPRE", {}).get("refno") == elbow_spec["refno"]
                 and references.get("CATR", {}).get("status") == "resolved", "Template catalogue reference evidence unverified")
        elbows = [_place(template, bend, request["pipe_name"] + f"/E{index:02}") for index, bend in enumerate(bends, 1)]
        after = _context(bridge)
        _require(before["identity"] == after["identity"], "Session/project/MDB/current element changed during route preparation")
        for group in [*endpoint_groups, template_group]:
            _require(_source_binding(group["response"], before, after, source_kind="connection_points",
                requested_refno=group["refno"], requested_type=group["expected_type"],
                requested_point=group.get("point_number"))["matches_enclosing_context"] is True, "Native point boundary changed")
        state = {"context": before["identity"], "parent": parent, "branch": branch, "source_tube": source_tube, "elbow_attributes": elbow_attrs,
                 "spec": spec, "tube_spec": tube, "elbow_spec": elbow_spec, "catalogue": references["CATR"],
                 "template": template, "bore_mm": bore, "endpoint_points": endpoint_points,
                 "centerline": centerline, "elbows": elbows,
                 "start_tangent": centerline["elements"][0]["direction_world"],
                 "end_tangent": centerline["elements"][-1]["direction_world"]}
        prepared = {"plan_revision": _plan_revision(request, state), "expected_session_id": before["identity"]["session_id"]}
        if existing_pipe_refno is None:
            source = _macro(request, state)
            state["source"] = source
            prepared.update(revision=_hash({"request": request, "state": state}),
                            source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest())
        return state, prepared

    def __call__(self, args: dict[str, Any]) -> dict[str, Any]:
        request, dry_run = _parameters(args)
        bridge = self.bridge
        if not dry_run and not bridge.settings.allow_commands:
            raise PermissionError("Native route execution requires E3D_MCP_ALLOW_COMMANDS=true")
        result: dict[str, Any] = {"operation": "piping_route_create", "success": False, "available": False,
            "dry_run": dry_run, "status": "unavailable", "execution_attempted": False,
            "readback_verified": False, "outcome_uncertain": False, "retry_safe": False,
            "created": [], "errors": [], "external_connections_written": False,
            "scope": "New independent native PIPE/BRAN/ELBO and implicit TUBI. No existing endpoint connections, "
                     "catalogue edits, automatic rollback/delete, SAVEWORK, CE restoration or view changes. "
                     "Revision and macro membership guards are not a database lock. Partial writes may remain on failure."}
        envelope = {"ok": True, "version": bridge._selected_version(None), "result": result}
        if bridge.settings.mode != "bridge" or envelope["version"] != "E3D21":
            return envelope
        fingerprint = _hash(args)
        request_id = args.get("request_id")
        if not dry_run:
            with self._lock:
                if request_id in self._requests:
                    retained = self._requests[request_id]
                    if retained["fingerprint"] != fingerprint:
                        raise ValueError("request_id was already used for different route arguments")
                    return {**envelope, "result": {**deepcopy(retained["result"]), "duplicate_request": True,
                                                  "request_in_progress": not retained["completed"]}}
                while len(self._requests) >= 64:
                    completed = next((key for key, value in self._requests.items() if value["completed"]), None)
                    _require(completed is not None, "Native route request table is full")
                    del self._requests[completed]
                self._requests[request_id] = {"fingerprint": fingerprint, "completed": False,
                                              "result": {**deepcopy(result), "status": "request_in_progress"}}
        try:
            state, prepared = self._prepare(request)
            result.update(available=True, **prepared, centerline_plan=state["centerline"],
                          elbow_placements=state["elbows"], native_source=state["source"],
                          planned_names=[request["pipe_name"], request["pipe_name"] + "/B1", *[item["name"] for item in state["elbows"]]])
            result["verification_request"] = {"tool": VERIFY_TOOL, "arguments": {**deepcopy(request),
                "expected_session_id": prepared["expected_session_id"], "expected_plan_revision": prepared["plan_revision"]},
                "required_after_discovery": ["pipe_refno"]}
            if dry_run:
                preview = bridge.execute_pml_script(state["source"], True)
                result["encoding_preview"] = preview
                native = _result(preview)
                _require(native.get("dry_run") is True and native.get("execution_attempted") is False
                         and native.get("source_sha256") == prepared["source_sha256"], "Exact native encoding preview unverified")
                result.update(success=True, status="prepared", syntax_checked=False)
            else:
                _require(args["expected_session_id"] == prepared["expected_session_id"]
                         and args["expected_revision"] == prepared["revision"], "Native route revision/session changed; nothing submitted")
                result.update(status="submitted", execution_attempted=True,
                    pml_script_request={"request_id": request_id, "expected_session_id": args["expected_session_id"], "status_tool": "e3d_pml_script_status"})
                executed = bridge.execute_pml_script(state["source"], False, request_id=request_id,
                                                     expected_session_id=args["expected_session_id"])
                result["native_execution"] = executed
                _script_completed(executed, result, prepared["source_sha256"], "MCP_NATIVE_PIPE_ROUTE_COMPLETE")
                result["readback"] = self._verify(request, state)
                result.update(success=True, status="created_verified", readback_verified=True,
                              created=result["readback"]["created"])
                result["verification_request"]["arguments"]["pipe_refno"] = result["created"][0]["refno"]
                result["verification_request"]["required_after_discovery"] = []
        except Exception as exc:
            result["errors"].append({"type": type(exc).__name__, "message": str(exc)[:2048]})
            if isinstance(exc, BridgeError):
                result["bridge_diagnostics"] = exc.diagnostics
                result["outcome_uncertain"] = result["execution_attempted"] and bool(exc.outcome_uncertain or exc.transport_error or exc.response_invalid)
            result["status"] = "outcome_uncertain" if result["outcome_uncertain"] else "failed_after_submission" if result["execution_attempted"] else "preflight_rejected"
            result["residual_state_unknown"] = result["execution_attempted"]
        finally:
            if not dry_run:
                with self._lock:
                    self._requests[request_id].update(completed=True, result=deepcopy(result))
        return envelope

    def _verify(self, request: dict[str, Any], state: dict[str, Any], *, existing_pipe_refno: str | None = None) -> dict[str, Any]:
        bridge, tolerance = self.bridge, request["centerline"]["position_tolerance_mm"]
        before = _context(bridge)
        _require(all(before["identity"][key] == state["context"][key] for key in ("version", "session_id", "bridge_build", "project", "mdb")),
                 "Native context changed before readback")
        pipe = _query(bridge, request["pipe_name"], "PIPE")
        branch = _query(bridge, request["pipe_name"] + "/B1", "BRAN")
        if existing_pipe_refno is None:
            _require(before["identity"]["current_element"] == pipe["refno"], "Native script did not finish at the created PIPE")
        else:
            _require(pipe["refno"] == existing_pipe_refno, "Verification PIPE numeric identity/name changed")
            _require(before["identity"] == state["context"], "Native verification context changed since planning")
        parent = _members(bridge, request["parent_refno"], "ZONE")
        # PML NEW at an explicit owner inserts the first member. All existing
        # members must retain their relative order and typed identities.
        prior = [{key: row[key] for key in ("refno", "type", "name")} for row in state["parent"]["members"]]
        if existing_pipe_refno is None:
            observed = [{key: row[key] for key in ("refno", "type", "name")} for row in parent["members"][1:]]
            _require(bool(parent["members"]) and parent["members"][0]["refno"] == pipe["refno"] and observed == prior,
                     "Destination membership did not insert only the new PIPE with existing order preserved")
        else:
            observed = [{key: row[key] for key in ("refno", "type", "name")} for row in parent["members"]]
            _require(observed == prior and sum(row["refno"] == pipe["refno"] and row["type"] == "PIPE" for row in observed) == 1,
                     "Current verification destination membership changed")
        manifest = pipe_member_manifest(bridge, {"refno": pipe["refno"], "max_depth": 2, "limit": 40, "attributes": []})
        tree = _result(manifest)
        _require(tree.get("complete") is True and tree.get("traversal_complete") is True,
                 "Created PIPE hierarchy readback incomplete")
        _require(all(manifest.get(key) == before["identity"][key] for key in ("session_id", "bridge_build"))
                 and tree.get("root", {}).get("refno") == pipe["refno"] and tree["root"].get("type") == "PIPE",
                 "PIPE manifest session/root identity changed")
        manifest_context = tree.get("context", {})
        _require(all(manifest_context.get(key) is True for key in ("same_project_instance", "same_mdb_instance", "boundary_agreement"))
                 and all(all(manifest_context.get(sample, {}).get(key) == before["identity"][key]
                             for key in ("project", "mdb", "current_element")) for sample in ("before", "after")),
                 "PIPE manifest enclosing context changed")
        rows = tree["rows"]
        expected_types = ["BRAN", "TUBI"] + [kind for _ in state["elbows"] for kind in ("ELBO", "TUBI")]
        _require([row["type"] for row in rows] == expected_types and rows[0]["refno"] == branch["refno"]
                 and all(row.get("parent_index") == (0 if index == 0 else 1) for index, row in enumerate(rows)),
                 "Native branch/elbow/implicit tube membership differs from the plan")
        pipe_attrs = _attributes(bridge, pipe["refno"], "PIPE", ["PSPE"])
        _require(_query(bridge, pipe_attrs["values"]["PSPE"], "SPEC")["refno"] == state["spec"]["refno"], "PIPE specification readback mismatch")
        branch_attrs = _attributes(bridge, branch["refno"], "BRAN", ["PSPE", "HSTU", "HREF", "TREF", "HBOR", "TBOR", "LHEAD", "LTAIL", "HCON", "TCON"])
        _require(all(branch_attrs["values"][key].casefold() == "nulref" for key in ("HREF", "TREF")), "New branch unexpectedly has external references")
        for key, identity in (("PSPE", state["spec"]), ("HSTU", state["tube_spec"])):
            _require(_query(bridge, branch_attrs["values"][key], identity["type"])["refno"] == identity["refno"], "Branch specification readback mismatch")
        _require(all(branch_attrs["typed_values"].get(key) is True for key in ("LHEAD", "LTAIL")), "Branch endpoints are not explicitly fixed")
        _require(all(branch_attrs["values"][key] == "OPEN" for key in ("HCON", "TCON")), "Branch endpoint connection code is not OPEN")
        _require(all(abs(_millimetres(branch_attrs, key) - state["bore_mm"]) < 1e-6 for key in ("HBOR", "TBOR")),
                 "Native branch nominal bore mismatch")
        tubes, elbows = [], []
        for row, planned in zip([row for row in rows if row["type"] == "ELBO"], state["elbows"]):
            _require(row["name"] == planned["name"], "Native elbow name/order mismatch")
            group = _points(bridge, {"refno": row["refno"], "expected_type": "ELBO"}, before, [1, 2])
            for expected in planned["expected_points"]:
                observed = _point(group, expected["number"])
                _require(math.dist(observed["position"], expected["position"]) <= tolerance, "Native elbow PPOS readback mismatch")
                _require(math.dist(observed["direction"], expected["direction"]) <= 1e-5, "Native elbow PDIR readback mismatch")
                _require(abs(observed["bore_mm"] - state["bore_mm"]) < 1e-6, "Native elbow bore readback mismatch")
            _require(group["response"]["result"]["references"]["SPRE"]["refno"] == state["elbow_spec"]["refno"]
                     and group["response"]["result"]["references"]["CATR"]["refno"] == state["catalogue"]["refno"], "Native elbow catalogue reference mismatch")
            elbows.append({"element": row, "connection_points": group["response"]})
        straights = [item for item in state["centerline"]["elements"] if item["kind"] == "straight"]
        for row, planned in zip([row for row in rows if row["type"] == "TUBI"], straights):
            attrs = _attributes(bridge, row["refno"], "TUBI", ["SPRE", "ITLEN", "ABOR", "LBOR"])
            length = _millimetres(attrs, "ITLEN")
            _require(abs(length - planned["length_mm"]) <= tolerance, "Native implicit tube length readback mismatch")
            _require(_query(bridge, attrs["values"]["SPRE"], "SPCO")["refno"] == state["tube_spec"]["refno"], "Native tube specification mismatch")
            _require(all(abs(_millimetres(attrs, key) - state["bore_mm"]) < 1e-6 for key in ("ABOR", "LBOR")),
                     "Native implicit tube nominal bore mismatch")
            tubes.append({"element": row, "attributes": attrs, "planned_length_mm": planned["length_mm"]})
        _require(_members(bridge, request["parent_refno"], "ZONE") == parent, "Destination membership changed during readback")
        after = _context(bridge)
        _require(before["identity"] == after["identity"], "Native readback context changed")
        return {"created": [pipe, branch, *[row for row in rows if row["type"] == "ELBO"]], "manifest": manifest,
                "pipe_attributes": pipe_attrs, "branch_attributes": branch_attrs, "elbows": elbows, "implicit_tubes": tubes,
                "context": before["identity"], "external_connections_verified_unset": True}

    def verify(self, args: dict[str, Any]) -> dict[str, Any]:
        _require(isinstance(args, dict) and _REQUIRED | {"pipe_refno", "expected_session_id"} <= set(args)
                 and not set(args) - _REQUIRED - _VERIFICATION,
                 "Verification requires explicit route parameters, pipe_refno and expected_session_id")
        request, _ = _parameters({key: args[key] for key in _REQUIRED})
        pipe_refno = _numeric(args["pipe_refno"])
        session = args["expected_session_id"]
        _require(isinstance(session, str) and bool(session) and len(session) <= 256, "Explicit expected_session_id required")
        expected_plan = args.get("expected_plan_revision")
        if "expected_plan_revision" in args:
            _require(isinstance(expected_plan, str) and bool(re.fullmatch(r"[0-9a-f]{64}", expected_plan)),
                     "expected_plan_revision must be 64 lowercase hex characters")
        bridge = self.bridge
        result: dict[str, Any] = {"operation": "piping_route_verify", "status": "unavailable", "success": False,
            "available": False, "read_only": True, "model_write_attempted": False, "readback_verified": False,
            "native_script_execution_attempted": False, "outcome_uncertain": False, "retry_safe": False,
            "plan_binding_verified": False, "creation_attribution_verified": False, "errors": [],
            "scope": "Current native model compared with explicit route expectations and re-read sources. "
                     "Generated PML only reads WORLD BRAN endpoints; it requires command permission and does not change CE. "
                     "Optional plan fingerprint binds the comparison, not authorship or an original creation receipt. "
                     "Historical parent changes and obstacle/engineering suitability are not certified. "
                     "No create, repair, connect, replay, save or view update. Sequential observations are not a database lock."}
        envelope = {"ok": True, "version": bridge._selected_version(None), "result": result}
        if bridge.settings.mode != "bridge" or envelope["version"] != "E3D21":
            return envelope
        if not bridge.settings.allow_commands:
            raise PermissionError("Native WORLD endpoint verification uses a generated read-only PML script and requires E3D_MCP_ALLOW_COMMANDS=true")
        stage = "context"
        try:
            _require(_context(bridge)["identity"]["session_id"] == session, "Verification session changed")
            stage = "plan"
            state, prepared = self._prepare(request, existing_pipe_refno=pipe_refno)
            _require(prepared["expected_session_id"] == session, "Verification session changed during preparation")
            result.update(available=True, **prepared)
            _require(expected_plan is None or expected_plan == prepared["plan_revision"],
                     "Verification plan fingerprint changed; original plan comparison refused")
            result["plan_binding_verified"] = expected_plan is not None
            stage = "branch_endpoints"
            branch = _query(bridge, request["pipe_name"] + "/B1", "BRAN")
            source = _verification_macro(request, state, branch["refno"])
            request_id = str(uuid4())
            result.update(native_script_execution_attempted=True,
                          pml_script_request={"request_id": request_id, "expected_session_id": session,
                                              "status_tool": "e3d_pml_script_status"})
            executed = bridge.execute_pml_script(source, False, request_id=request_id, expected_session_id=session)
            result["native_endpoint_verification"] = executed
            _script_completed(executed, result, hashlib.sha256(source.encode("utf-8")).hexdigest(),
                              "MCP_NATIVE_PIPE_ROUTE_VERIFICATION_COMPLETE")
            stage = "model_readback"
            readback = self._verify(request, state, existing_pipe_refno=pipe_refno)
            readback["elements"] = readback.pop("created")
            readback.update(branch_endpoints_verified=True, historical_parent_changes_verified=False)
            result.update(success=True, status="model_matches_plan", readback_verified=True, readback=readback)
        except Exception as exc:
            result["errors"].append({"stage": stage, "type": type(exc).__name__, "message": str(exc)[:2048]})
            if isinstance(exc, BridgeError):
                result["bridge_diagnostics"] = exc.diagnostics
                result["outcome_uncertain"] = bool(exc.outcome_uncertain or exc.transport_error or exc.response_invalid)
            result["status"] = "verification_uncertain" if result["outcome_uncertain"] else "verification_failed"
        return envelope


def register_native_piping_route(server: McpServer, bridge: BridgeClient) -> None:
    creator = NativePipingRouteCreator(bridge)
    endpoint_schema = {"type": "object", "properties": {
        "refno": {"type": "string", "pattern": "^" + _REF.pattern + "$"}, "expected_type": {"type": "string", "minLength": 1, "maxLength": 64},
        "point_number": {"type": "integer", "minimum": 1, "maximum": 10000}},
        "required": ["refno", "expected_type", "point_number"], "additionalProperties": False}
    properties = {key: {"type": "string", "pattern": "^" + _REF.pattern + "$"} for key in ("parent_refno", "template_branch_refno", "template_elbow_refno")}
    properties.update({"pipe_name": {"type": "string", "pattern": "^" + _NAME.pattern + "$", "maxLength": 36},
        "endpoints": {"type": "array", "minItems": 2, "maxItems": 2, "items": endpoint_schema}, "centerline": centerline_schema(),
        "dry_run": {"type": "boolean", "default": True}, "expected_revision": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "expected_session_id": {"type": "string", "minLength": 1, "maxLength": 256},
        "request_id": {"type": "string", "format": "uuid"}})
    properties["centerline"]["properties"]["position_tolerance_mm"].update(minimum=0.01, maximum=1)
    server.add_tool(TOOL,
        "Prepare or create a new native PIPE and BRAN under one explicit ZONE, using an existing BRAN's SPEC/HSTU and "
        "a fixed-catalogue two-port 90-degree ELBO template. Uses bound WORLD endpoint P-points, explicit centreline choices "
        "and measured template PPOS/PDIR to place up to 16 native ELBO, with E3D-generated implicit TUBI. Radius and nominal "
        "bores must match; positive straight runs are required. Preview is read-only and returns generated PML, encoding "
        "evidence and a revision, not syntax proof. Execution requires command permission, exact revision/session and a "
        "caller-owned request UUID. Rechecks state, submits the generated macro once through the existing native PML "
        "journal and verifies hierarchy, elbow PPOS/PDIR/catalogue refs and native TUBI ITLEN. Leaves CE at the new PIPE. "
        "Both branch external references remain unset; existing endpoints are geometric anchors only. No automatic "
        "connect, rollback/delete, SAVEWORK, view update or retry. On uncertain transport use e3d_pml_script_status with "
        "the returned IDs; e3d_piping_route_verify compares an explicit existing PIPE with the plan without replaying creation. "
        "Revision/membership guards are not a database lock; failed prefixes may remain. "
        "Does not certify obstacle clearance, pressure class or engineering suitability.",
        {"type": "object", "properties": properties, "required": sorted(_REQUIRED), "additionalProperties": False,
         "allOf": [{"if": {"properties": {"dry_run": {"const": False}}, "required": ["dry_run"]},
                    "then": {"required": ["expected_revision", "expected_session_id", "request_id"]}}]},
        creator, destructive=False)
    verification_properties = {key: deepcopy(value) for key, value in properties.items() if key in _REQUIRED}
    verification_properties.update({"pipe_refno": {"type": "string", "pattern": "^" + _REF.pattern + "$",
        "description": "Exact numeric REF of the existing PIPE to inspect; name-only attribution is not accepted."},
        "expected_session_id": deepcopy(properties["expected_session_id"]),
        "expected_plan_revision": {"type": "string", "pattern": "^[0-9a-f]{64}$",
            "description": "Optional plan_revision from the original create preview/receipt. Omission compares current sources only."}})
    server.add_tool(VERIFY_TOOL,
        "Read-only comparison of one explicit existing PIPE against the same route inputs used for native creation. "
        "Works after MCP restart or CE changes; requires the original E3D session and explicit numeric PIPE identity. "
        "Re-reads sources and compares optional expected_plan_revision independently of CE and unrelated parent members. "
        "Uses a generated PML script containing only WORLD branch endpoint reads/assertions, so command permission is "
        "required despite no model writes. Then verifies PIPE/BRAN membership/specification, OPEN unset external ends, "
        "ELBO PPOS/PDIR/catalogue refs, and implicit TUBI ITLEN/bores. Retains an independent verification receipt and "
        "PML journal IDs on uncertainty; never replays creation or rewrites its historical receipt. A matching model or "
        "caller-provided fingerprint does not prove creation authorship, historical parent changes or engineering validity. "
        "No CE/model/view changes, repair, connect, rollback or SAVEWORK. Sequential reads are not atomic.",
        {"type": "object", "properties": verification_properties,
         "required": sorted(_REQUIRED | {"pipe_refno", "expected_session_id"}), "additionalProperties": False},
        creator.verify, read_only=True)
