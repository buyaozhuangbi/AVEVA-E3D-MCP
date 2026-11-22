"""Verified, projected native definition-plane curves for ordinary PANE loops.

PAVE POS is a pole, not a fillet endpoint.  This module keeps the independent
PPOS/PDIR observations and projects P6/P4/P8 onto the P0 definition plane, as the
installed SDNF PAVE exporter does.  The result is neither a material surface nor
a rendered/Boolean boundary.  No native calls, input mutations or control-polygon
fallbacks are made here.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any


_MAX_LOOPS = 32
_MAX_VERTICES = 2048
_MAX_SEGMENTS = 4096
_MAX_SAMPLED_EDGES = 12000
_MAX_INTERSECTION_PAIRS = 4_000_000
_MAX_COORDINATE_MM = 1_000_000_000.0
_POSITION_TOLERANCE_MM = 1e-5
_DIRECTION_TOLERANCE = 1e-7
_ANGLE_TOLERANCE = 2e-7
_TAU = 2 * math.pi
_BOUNDARY_SOURCE = "projected_native_definition_plane_curves"
_UNSET_EXISTENCE = re.compile(r"^Attribute\s+PPEXST\s+is\s+unset(?:\s+for\s+element\s+.+)?\.?$", re.I)
_ZERO_ANGLE = re.compile(r"^[+-]?(?:0+(?:\.0*)?|\.0+)(?:deg(?:ree(?:s)?)?)?$", re.I)
_SOURCE_KEYS = (
    "success", "complete", "partial", "read_errors", "unsupported_features",
    "boundary_points_requested", "boundary_points_complete", "boundary_points_read_complete",
    "boundary_points_errors", "boundary_points_vertex_count", "boundary_points_curved_vertex_count",
    "boundary_points_source_geometry_complete", "boundary_points_scope",
    "world_positions_requested", "world_positions_complete", "world_position_frame",
    "world_read_errors", "world_position_count", "world_position_vertex_count",
)

Vec = tuple[float, float, float]
Point2 = tuple[float, float]


class _Rejected(ValueError):
    def __init__(self, code: str, **context: Any):
        super().__init__(code)
        self.code = code
        self.context = context


def _reject(code: str, **context: Any) -> None:
    raise _Rejected(code, **context)


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _reject("invalid_number", field=field)
    try:
        result = float(value)
    except (OverflowError, ValueError):
        _reject("invalid_number", field=field)
    if not math.isfinite(result):
        _reject("non_finite_number", field=field)
    return result


def _point(value: Any, field: str) -> Vec:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        _reject("invalid_point", field=field)
    result = tuple(_number(item, field) for item in value)
    if any(abs(item) > _MAX_COORDINATE_MM for item in result):
        _reject("coordinate_limit_exceeded", field=field)
    return result  # type: ignore[return-value]


def _reference(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _reject("reference_unavailable", field=field)
    return value


def _count(value: Any, expected: int, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        _reject("source_count_mismatch", field=field, expected=expected, observed=value)


def _add(a: Vec, b: Vec) -> Vec:
    return tuple(a[i] + b[i] for i in range(3))  # type: ignore[return-value]


def _sub(a: Vec, b: Vec) -> Vec:
    return tuple(a[i] - b[i] for i in range(3))  # type: ignore[return-value]


def _mul(a: Vec, scale: float) -> Vec:
    return tuple(item * scale for item in a)  # type: ignore[return-value]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return math.fsum(x * y for x, y in zip(a, b))


def _cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(value: Vec, field: str, *, native: bool = False) -> Vec:
    length = math.hypot(*value)
    if length == 0 or not math.isfinite(length):
        _reject("degenerate_direction", field=field)
    if native and abs(length - 1) > _DIRECTION_TOLERANCE:
        _reject("native_direction_not_unit", field=field, magnitude=length)
    return _mul(value, 1 / length)


def _tolerance(points: Sequence[Vec]) -> float:
    # Subtractions use nearby origins.  Absolute WORLD translations must not
    # expand the geometric acceptance tolerance in proportion to that offset.
    largest = max((abs(value) for point in points for value in point), default=0.0)
    return max(_POSITION_TOLERANCE_MM, 64 * math.ulp(largest))


def _unset(value: Any) -> bool:
    return isinstance(value, str) and value.strip().casefold() == "unset"


def _zero_angle(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return _number(value, "BANG") == 0
    return isinstance(value, str) and _ZERO_ANGLE.fullmatch(value.strip()) is not None


def _empty_result(native: Any, space: Any, chord: Any) -> dict[str, Any]:
    data = native if isinstance(native, Mapping) else {}
    element = data.get("element")
    identity = element if isinstance(element, Mapping) else {}
    return {
        "success": False, "available": False, "complete": False,
        "coordinate_space": space, "coordinate_unit": "mm",
        "coordinate_frame": data.get("world_position_frame") if space == "WORLD" else data.get("coordinate_frame"),
        "boundary_source": _BOUNDARY_SOURCE, "chord_tolerance_mm": chord,
        "source": {"refno": identity.get("refno"), "name": identity.get("name"), "revision": data.get("revision")},
        "source_diagnostics": {key: copy.deepcopy(data[key]) for key in _SOURCE_KEYS if key in data},
        "native_boundary_points_complete": data.get("boundary_points_complete"),
        "native_boundary_points_read_complete": data.get("boundary_points_read_complete"),
        "native_existence_verified": None,
        "native_tessellation": False, "rendered_boundary": False,
        "cuts_applied": False, "hole_roles_inferred": False,
        "atomic_snapshot": False,
        "validation_scope": "Ordinary independent PLOO line/positive-FRAD fillet loops projected onto the PANE definition plane; loop roles, thickness surfaces and Boolean/rendered geometry are not inferred.",
        "loops": [], "loop_count": 0, "segment_count": 0, "line_count": 0,
        "arc_count": 0, "sampled_edge_count": 0, "errors": [],
        "diagnostics": {"limits": {"loops": _MAX_LOOPS, "vertices": _MAX_VERTICES,
                                    "segments": _MAX_SEGMENTS, "sampled_edges": _MAX_SAMPLED_EDGES,
                                    "intersection_pairs": _MAX_INTERSECTION_PAIRS}},
    }


def _attributes(vertex: Mapping[str, Any], refno: str) -> None:
    descriptor = vertex.get("curve_import_descriptor")
    if (not isinstance(descriptor, Mapping) or descriptor.get("checked") is not True or
            descriptor.get("tagged") is not False):
        _reject("curve_import_descriptor_unverified", refno=refno)
    if "refno" in descriptor and descriptor["refno"] != refno:
        _reject("curve_import_descriptor_refno_mismatch", refno=refno)
    if "attribute" in descriptor and descriptor["attribute"] != "DESP":
        _reject("curve_import_descriptor_attribute_mismatch", refno=refno)
    if "value" in descriptor and not _unset(descriptor["value"]):
        _reject("curve_import_descriptor_tagged_or_unknown", refno=refno)
    for attribute in ("CTYA", "GTYP"):
        if not _unset(vertex.get(attribute)):
            _reject("unsupported_curve_attribute", refno=refno, attribute=attribute, value=vertex.get(attribute))
    if not _zero_angle(vertex.get("BANG")):
        _reject("unsupported_curve_attribute", refno=refno, attribute="BANG", value=vertex.get("BANG"))


def _read_native(native: Mapping[str, Any], world: bool) -> tuple[list[dict[str, Any]], str, str | None]:
    if native.get("success") is not True or native.get("complete") is not True or native.get("partial") is True:
        _reject("panel_geometry_incomplete")
    if native.get("read_errors") or native.get("unsupported_features"):
        _reject("panel_geometry_has_unresolved_features_or_errors")
    if native.get("unit") != "mm":
        _reject("panel_coordinate_unit_mismatch")
    if native.get("boundary_points_requested") is not True:
        _reject("native_boundary_points_not_requested")
    if ("boundary_points_source_geometry_complete" in native and
            native["boundary_points_source_geometry_complete"] is not True):
        _reject("native_boundary_source_geometry_incomplete")
    pane_ref = _reference(native.get("coordinate_frame"), "coordinate_frame")
    element = native.get("element")
    if not isinstance(element, Mapping) or element.get("refno") != pane_ref:
        _reject("panel_coordinate_frame_identity_mismatch")
    element_type = element.get("type", "PANE")
    if not isinstance(element_type, str) or element_type.upper() not in {"PANE", "PANEL"}:
        _reject("not_a_panel")
    world_ref = None
    if world:
        if (native.get("world_positions_requested") is not True or native.get("world_positions_complete") is not True or
                native.get("world_read_errors")):
            _reject("native_world_positions_incomplete")
        if native.get("world_position_unit") != "mm":
            _reject("world_coordinate_unit_mismatch")
        world_ref = _reference(native.get("world_position_frame"), "world_position_frame")
        if world_ref == pane_ref:
            _reject("world_and_panel_frame_identity_equal")
    loops = native.get("loops")
    if not isinstance(loops, list) or not loops:
        _reject("panel_loops_unavailable")
    if len(loops) > _MAX_LOOPS:
        _reject("panel_loop_limit_exceeded")
    _count(native.get("loop_count"), len(loops), "loop_count")
    output: list[dict[str, Any]] = []
    total = curved = 0
    seen_refs: set[str] = set()
    for index, loop in enumerate(loops):
        if not isinstance(loop, Mapping) or loop.get("complete") is not True or loop.get("read_errors"):
            _reject("panel_loop_incomplete", loop_index=index)
        if loop.get("blind") is not False:
            _reject("blind_or_unknown_loop", loop_index=index)
        features = loop.get("unsupported_features", [])
        if (not isinstance(features, list) or any(not isinstance(item, Mapping) or item.get("code") != "fillet_radius"
                                                  for item in features)):
            _reject("unsupported_loop_features", loop_index=index)
        if "loop_index" in loop:
            _count(loop["loop_index"], index, "loop_index")
        loop_identity = loop.get("element", {})
        if not isinstance(loop_identity, Mapping):
            _reject("loop_identity_unavailable", loop_index=index)
        loop_ref = _reference(loop_identity.get("refno"), "loop_refno")
        if loop_ref in seen_refs:
            _reject("duplicate_native_reference", refno=loop_ref)
        seen_refs.add(loop_ref)
        vertices = loop.get("vertices")
        if not isinstance(vertices, list) or len(vertices) < 3:
            _reject("panel_loop_vertices_unavailable", loop_index=index)
        total += len(vertices)
        if total > _MAX_VERTICES:
            _reject("panel_vertex_limit_exceeded")
        if "vertex_count" in loop:
            _count(loop["vertex_count"], len(vertices), "loop.vertex_count")
        records: list[dict[str, Any]] = []
        for cursor, vertex in enumerate(vertices):
            if not isinstance(vertex, Mapping):
                _reject("invalid_vertex_record", loop_index=index, vertex_index=cursor)
            refno = _reference(vertex.get("refno"), "vertex.refno")
            if refno in seen_refs:
                _reject("duplicate_native_reference", refno=refno)
            seen_refs.add(refno)
            if "vertex_index" in vertex:
                _count(vertex["vertex_index"], cursor, "vertex_index")
            _attributes(vertex, refno)
            radius = _number(vertex.get("fillet_radius_mm"), "fillet_radius_mm")
            if radius < 0:
                _reject("negative_frad_semantics_unverified", refno=refno)
            if radius > _MAX_COORDINATE_MM:
                _reject("radius_limit_exceeded", refno=refno)
            local = _point(vertex.get("position"), "PAVE.POS.panel_local")
            absolute = None
            if world:
                if vertex.get("world_position_complete") is not True:
                    _reject("world_control_position_incomplete", refno=refno)
                absolute = _point(vertex.get("position_world_mm"), "PAVE.POS.WORLD")
            boundary = vertex.get("boundary_points")
            if radius > 0:
                curved += 1
                if not isinstance(boundary, Mapping) or boundary.get("required") is not True:
                    _reject("native_curve_evidence_unavailable", refno=refno)
            elif isinstance(boundary, Mapping) and boundary.get("required") is not False:
                _reject("straight_vertex_evidence_inconsistent", refno=refno)
            records.append({"refno": refno, "index": cursor, "radius": radius,
                            "local": local, "world": absolute, "raw": vertex})
        output.append({"index": index, "refno": loop_ref, "vertices": records, "raw": loop})
    _count(native.get("vertex_count"), total, "vertex_count")
    for key, expected in (("boundary_points_vertex_count", total), ("boundary_points_curved_vertex_count", curved)):
        if key in native:
            _count(native[key], expected, key)
    if world:
        _count(native.get("world_position_count"), total, "world_position_count")
        _count(native.get("world_position_vertex_count"), total, "world_position_vertex_count")
    return output, pane_ref, world_ref


def _existence_error(value: Any) -> bool:
    return isinstance(value, str) and _UNSET_EXISTENCE.fullmatch(value.strip()) is not None


def _frame_evidence(vertex: dict[str, Any], space: str, frame_ref: str) -> dict[str, Any]:
    raw = vertex["raw"]["boundary_points"].get(space)
    if not isinstance(raw, Mapping):
        _reject("native_boundary_frame_unavailable", refno=vertex["refno"], coordinate_space=space)
    if raw.get("frame_refno") != frame_ref or raw.get("unit") != "mm":
        _reject("native_boundary_frame_or_unit_mismatch", refno=vertex["refno"], coordinate_space=space)
    points = raw.get("points")
    if not isinstance(points, list) or len(points) != 4:
        _reject("native_boundary_point_set_incomplete", refno=vertex["refno"], coordinate_space=space)
    by_number: dict[int, Mapping[str, Any]] = {}
    positions: dict[int, Vec] = {}
    existence: list[bool | None] = []
    for point in points:
        if not isinstance(point, Mapping):
            _reject("invalid_native_boundary_point", refno=vertex["refno"])
        number = point.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number not in {0, 4, 6, 8} or number in by_number:
            _reject("invalid_native_boundary_point_number", refno=vertex["refno"])
        by_number[number] = point
        if point.get("position_complete") is not True:
            _reject("native_boundary_position_incomplete", refno=vertex["refno"], number=number, coordinate_space=space)
        positions[number] = _point(point.get("position_mm"), f"PPOS({number}).{space}")
        errors = point.get("errors", {})
        if not isinstance(errors, Mapping):
            _reject("invalid_native_point_errors", refno=vertex["refno"], number=number)
        if number == 0:
            if errors or point.get("direction_complete") is not True:
                _reject("native_p0_plane_incomplete", refno=vertex["refno"], coordinate_space=space)
        else:
            exists = point.get("exists")
            if point.get("exists_checked") is not True or (exists is not True and exists is not None):
                _reject("native_derived_point_existence_rejected", refno=vertex["refno"], number=number,
                        exists=exists, coordinate_space=space)
            if exists is True:
                if errors:
                    _reject("native_derived_point_read_errors", refno=vertex["refno"], number=number, coordinate_space=space)
            elif set(errors) != {"exists"} or not _existence_error(errors["exists"]):
                _reject("native_derived_point_existence_unknown", refno=vertex["refno"], number=number, coordinate_space=space)
            existence.append(exists)
    frame_errors = raw.get("errors", {})
    if (not isinstance(frame_errors, Mapping) or any(key not in {"P4.exists", "P6.exists", "P8.exists"} or
            not _existence_error(value) for key, value in frame_errors.items())):
        _reject("native_boundary_frame_read_errors", refno=vertex["refno"], coordinate_space=space)
    direction_raw = _point(by_number[0].get("direction"), f"PDIR(0).{space}")
    normal = _unit(direction_raw, f"PDIR(0).{space}", native=True)
    return {"raw": raw, "positions": positions, "normal": normal, "raw_normal": direction_raw, "existence": existence}


def _fit_world(loops: list[dict[str, Any]], tolerance: float) -> dict[str, Any]:
    records = [record for loop in loops for record in loop["vertices"]]
    local = [record["local"] for record in records]
    world = [record["world"] for record in records]
    origin, observed_origin = local[0], world[0]
    differences = [_sub(point, origin) for point in local]
    first = max(range(1, len(local)), key=lambda i: math.hypot(differences[i][0], differences[i][1]))
    scale = math.hypot(differences[first][0], differences[first][1])
    if scale <= tolerance:
        _reject("world_frame_degenerate_controls")
    a = (differences[first][0] / scale, differences[first][1] / scale)
    second = max(range(len(local)), key=lambda i: abs(a[0] * differences[i][1] / scale - a[1] * differences[i][0] / scale))
    b = (differences[second][0] / scale, differences[second][1] / scale)
    determinant = a[0] * b[1] - a[1] * b[0]
    if abs(determinant) <= 1e-12:
        _reject("world_frame_degenerate_controls")
    wa = _mul(_sub(world[first], observed_origin), 1 / scale)
    wb = _mul(_sub(world[second], observed_origin), 1 / scale)
    x_raw = _mul(_sub(_mul(wa, b[1]), _mul(wb, a[1])), 1 / determinant)
    y_raw = _mul(_sub(_mul(wb, a[0]), _mul(wa, b[0])), 1 / determinant)
    frame_tolerance = max(64 * math.ulp(1.0), min(_DIRECTION_TOLERANCE, tolerance / (8 * scale)))
    lengths = (math.hypot(*x_raw), math.hypot(*y_raw))
    dot_xy = _dot(x_raw, y_raw)
    if any(abs(length - 1) > frame_tolerance for length in lengths) or abs(dot_xy) > frame_tolerance:
        _reject("world_controls_not_rigid", axis_lengths=lengths, axis_dot=dot_xy)
    x_axis = _unit(x_raw, "WORLD.x_axis")
    y_axis = _unit(_sub(y_raw, _mul(x_axis, _dot(x_axis, y_raw))), "WORLD.y_axis")
    z_axis = _unit(_cross(x_axis, y_axis), "WORLD.z_axis")
    fit = {"local_anchor": origin, "world_anchor": observed_origin, "axes": (x_axis, y_axis, z_axis),
           "raw_axis_lengths": lengths, "raw_axis_dot": dot_xy, "metric_tolerance": frame_tolerance}
    residual = max(math.dist(_mapped(point, fit), observed) for point, observed in zip(local, world))
    if residual > tolerance:
        _reject("world_control_rigid_fit_residual", residual_mm=residual, tolerance_mm=tolerance)
    fit["max_control_residual_mm"] = residual
    return fit


def _mapped(point: Vec, fit: Mapping[str, Any]) -> Vec:
    delta = _sub(point, fit["local_anchor"])
    return tuple(fit["world_anchor"][i] + math.fsum(delta[j] * fit["axes"][j][i] for j in range(3))
                 for i in range(3))  # type: ignore[return-value]


def _mapped_direction(direction: Vec, fit: Mapping[str, Any]) -> Vec:
    return tuple(math.fsum(direction[j] * fit["axes"][j][i] for j in range(3)) for i in range(3))  # type: ignore[return-value]


def _verify_world_evidence(loops: list[dict[str, Any]], fit: Mapping[str, Any], tolerance: float) -> dict[str, float]:
    max_point = max_direction = 0.0
    for loop in loops:
        for vertex in loop["vertices"]:
            if vertex["radius"] == 0:
                continue
            local, world = vertex["local_evidence"], vertex["world_evidence"]
            for number in (0, 4, 6, 8):
                residual = math.dist(_mapped(local["positions"][number], fit), world["positions"][number])
                max_point = max(max_point, residual)
                if residual > tolerance:
                    _reject("world_native_point_frame_mismatch", refno=vertex["refno"], number=number, residual_mm=residual)
            direction_residual = math.dist(_mapped_direction(local["raw_normal"], fit), world["raw_normal"])
            max_direction = max(max_direction, direction_residual)
            if direction_residual > _DIRECTION_TOLERANCE:
                _reject("world_native_p0_direction_mismatch", refno=vertex["refno"], residual=direction_residual)
    return {"max_native_point_residual_mm": max_point, "max_native_direction_residual": max_direction}


def _project(point: Vec, origin: Vec, normal: Vec) -> tuple[Vec, float]:
    offset = _dot(_sub(point, origin), normal)
    return _sub(point, _mul(normal, offset)), offset


def _arc(vertex: dict[str, Any], previous: Vec, pole: Vec, following: Vec, space: str,
         plane_normal: Vec, tolerance: float, loop_index: int) -> tuple[dict[str, Any], float]:
    evidence = vertex["world_evidence" if space == "WORLD" else "local_evidence"]
    normal, positions = evidence["normal"], evidence["positions"]
    origin = positions[0]
    refno = vertex["refno"]
    if math.dist(origin, pole) > tolerance:
        _reject("native_p0_does_not_match_pole", refno=refno, coordinate_space=space)
    if math.dist(normal, plane_normal) > _DIRECTION_TOLERANCE and math.dist(normal, _mul(plane_normal, -1)) > _DIRECTION_TOLERANCE:
        _reject("native_p0_not_panel_definition_plane", refno=refno, coordinate_space=space)
    projected: dict[int, Vec] = {}
    offsets: dict[int, float] = {}
    for number in (6, 4, 8):
        projected[number], offsets[number] = _project(positions[number], origin, normal)
    if max(offsets.values()) - min(offsets.values()) > tolerance:
        _reject("native_arc_points_not_in_one_parallel_plane", refno=refno, coordinate_space=space)
    incoming = _unit(_sub(pole, previous), "incoming_pole_edge")
    outgoing = _unit(_sub(following, pole), "outgoing_pole_edge")
    turn = math.atan2(_dot(normal, _cross(incoming, outgoing)), _dot(incoming, outgoing))
    if abs(turn) <= _ANGLE_TOLERANCE or math.pi - abs(turn) <= _ANGLE_TOLERANCE:
        _reject("arc_sweep_branch_ambiguous", refno=refno, turn_radians=turn)
    radius = vertex["radius"]
    trim = radius * math.tan(abs(turn) / 2)
    if not math.isfinite(trim) or trim <= tolerance:
        _reject("arc_trim_degenerate", refno=refno)
    expected_start = _sub(pole, _mul(incoming, trim))
    expected_end = _add(pole, _mul(outgoing, trim))
    sign = 1 if turn > 0 else -1
    expected_center = _add(expected_start, _mul(_cross(normal, incoming), sign * radius))
    for number, expected in ((6, expected_start), (4, expected_end), (8, expected_center)):
        residual = math.dist(projected[number], expected)
        if residual > tolerance:
            _reject("native_fillet_disagrees_with_pole_edges", refno=refno, number=number, residual_mm=residual)
    start, end, center = projected[6], projected[4], projected[8]
    radial_start, radial_end = _sub(start, center), _sub(end, center)
    radius_start, radius_end = math.hypot(*radial_start), math.hypot(*radial_end)
    if min(radius_start, radius_end) <= tolerance or max(abs(radius_start - radius), abs(radius_end - radius)) > tolerance:
        _reject("native_fillet_radius_mismatch", refno=refno)
    start_unit, end_unit = _mul(radial_start, 1 / radius_start), _mul(radial_end, 1 / radius_end)
    tangent_tolerance = _ANGLE_TOLERANCE + tolerance / max(radius, trim)
    if (math.dist(_mul(_cross(normal, start_unit), sign), incoming) > tangent_tolerance or
            math.dist(_mul(_cross(normal, end_unit), sign), outgoing) > tangent_tolerance):
        _reject("native_fillet_not_tangent", refno=refno)
    sweep = math.atan2(_dot(normal, _cross(start_unit, end_unit)), _dot(start_unit, end_unit))
    if abs(sweep - turn) > tangent_tolerance or sweep * turn <= 0 or math.pi - abs(sweep) <= _ANGLE_TOLERANCE:
        _reject("arc_sweep_branch_ambiguous", refno=refno, sweep_radians=sweep, turn_radians=turn)
    source = {
        "kind": "native_projected_P6_P4_P8_with_P0_plane", "pave_refno": refno,
        "vertex_index": vertex["index"], "loop_index": loop_index,
        "native_frad_mm": radius, "native_boundary_points": copy.deepcopy(evidence["raw"]),
        "curve_import_descriptor": copy.deepcopy(vertex["raw"]["curve_import_descriptor"]),
        "projection": {
            "method": "orthogonal projection of native P6/P4/P8 to native P0 position/direction plane",
            "plane_origin": list(origin), "plane_normal": list(normal),
            "raw_points_mm": {f"P{number}": list(point) for number, point in positions.items()},
            "projected_points_mm": {f"P{number}": list(point) for number, point in projected.items()},
            "signed_offsets_mm": {f"P{number}": value for number, value in offsets.items()},
            "raw_p0_direction": list(evidence["raw_normal"]),
        },
        "sweep_rule": "unique signed minor sweep consistent with both directed pole-edge tangents; major/180-degree branches rejected",
        "measured_radius_end_mm": radius_end,
    }
    if space == "WORLD":
        source["native_panel_local_boundary_points"] = copy.deepcopy(vertex["local_evidence"]["raw"])
    # The measured start radius defines the sampled circle.  The independently
    # observed end remains unchanged; its tiny radial residual is included in
    # the sampling error budget rather than silently normalized onto the circle.
    return {"kind": "arc", "start": list(start), "end": list(end), "center": list(center),
            "normal": list(normal), "radius_mm": radius_start, "sweep_radians": sweep, "source": source}, trim


def _derive_loop(loop: dict[str, Any], space: str, axes: tuple[Vec, Vec, Vec], tolerance: float) -> dict[str, Any]:
    vertices = loop["vertices"]
    poles = [vertex["world" if space == "WORLD" else "local"] for vertex in vertices]
    plane_origin, plane_normal = poles[0], axes[2]
    for point in poles:
        if abs(_dot(_sub(point, plane_origin), plane_normal)) > tolerance:
            _reject("nonplanar_control_loop", loop_index=loop["index"])
    if any(math.dist(poles[i], poles[(i + 1) % len(poles)]) <= tolerance for i in range(len(poles))):
        _reject("zero_length_control_edge", loop_index=loop["index"])
    arcs: dict[int, dict[str, Any]] = {}
    trims = [0.0] * len(vertices)
    starts, ends = list(poles), list(poles)
    for cursor, vertex in enumerate(vertices):
        if vertex["radius"] > 0:
            arc, trim = _arc(vertex, poles[cursor - 1], poles[cursor], poles[(cursor + 1) % len(poles)],
                             space, plane_normal, tolerance, loop["index"])
            arcs[cursor], trims[cursor] = arc, trim
            starts[cursor], ends[cursor] = tuple(arc["start"]), tuple(arc["end"])
    segments: list[dict[str, Any]] = []
    collapsed: list[int] = []
    for cursor, vertex in enumerate(vertices):
        following = (cursor + 1) % len(vertices)
        edge_length = math.dist(poles[cursor], poles[following])
        remaining = edge_length - trims[cursor] - trims[following]
        if remaining < -tolerance:
            _reject("adjacent_fillets_overrun_control_edge", loop_index=loop["index"], vertex_index=cursor,
                    edge_length_mm=edge_length, occupied_mm=trims[cursor] + trims[following])
        if cursor in arcs:
            segments.append(arcs[cursor])
        start, end = ends[cursor], starts[following]
        if math.dist(start, end) > tolerance:
            if remaining <= tolerance:
                _reject("trimmed_edge_geometry_inconsistent", loop_index=loop["index"], vertex_index=cursor)
            segments.append({"kind": "line", "start": list(start), "end": list(end),
                             "source": {"kind": "directed_pole_edge_trimmed_by_native_tangent_points",
                                        "loop_index": loop["index"], "from_vertex_index": cursor,
                                        "to_vertex_index": following, "from_pave_refno": vertex["refno"],
                                        "to_pave_refno": vertices[following]["refno"],
                                        "control_positions_mm": [list(poles[cursor]), list(poles[following])],
                                        "trim_start_mm": trims[cursor], "trim_end_mm": trims[following]}})
        else:
            collapsed.append(cursor)
    if len(segments) < 3:
        _reject("degenerate_curve_loop", loop_index=loop["index"])
    gaps = [math.dist(segment["end"], segments[(index + 1) % len(segments)]["start"])
            for index, segment in enumerate(segments)]
    if max(gaps) > tolerance:
        _reject("curve_loop_not_closed", loop_index=loop["index"], gap_mm=max(gaps))
    return {"loop_index": loop["index"], "source_refno": loop["refno"], "role": "not inferred", "closed": True,
            "plane": {"origin": list(plane_origin), "normal": list(plane_normal)},
            "segments": segments, "sampled_points": [], "segment_count": len(segments),
            "line_count": len(segments) - len(arcs), "arc_count": len(arcs), "sampled_edge_count": 0,
            "source_diagnostics": {
                "native_loop_complete": loop["raw"].get("complete"),
                "native_read_errors": copy.deepcopy(loop["raw"].get("read_errors")),
                "native_features": copy.deepcopy(loop["raw"].get("unsupported_features")),
                "control_vertices": [
                    {key: copy.deepcopy(vertex["raw"][key]) for key in (
                        "refno", "vertex_index", "position", "position_world_mm", "world_position_complete",
                        "fillet_radius_mm", "CTYA", "GTYP", "BANG", "curve_import_descriptor") if key in vertex["raw"]}
                    for vertex in vertices
                ],
            },
            "diagnostics": {"max_join_gap_mm": max(gaps), "collapsed_trimmed_edges": collapsed,
                            "definition_plane_projection": True}}


def _cross2(a: Point2, b: Point2) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _sub2(a: Point2, b: Point2) -> Point2:
    return a[0] - b[0], a[1] - b[1]


def _on_arc(point: Point2, arc: Mapping[str, Any], tolerance: float, *, interior: bool = False) -> bool:
    radial = _sub2(point, arc["center"])
    if abs(math.hypot(*radial) - arc["radius"]) > tolerance:
        return False
    angle = math.atan2(radial[1], radial[0])
    progress = ((angle - arc["angle"]) if arc["sweep"] > 0 else (arc["angle"] - angle)) % _TAU
    angle_tolerance = tolerance / arc["radius"] + 1e-12
    if progress > _TAU - angle_tolerance:
        progress = 0.0
    if interior:
        return angle_tolerance < progress < abs(arc["sweep"]) - angle_tolerance
    return progress <= abs(arc["sweep"]) + angle_tolerance


def _arc_point2(arc: Mapping[str, Any], fraction: float) -> Point2:
    angle = arc["angle"] + fraction * arc["sweep"]
    return (arc["center"][0] + arc["radius"] * math.cos(angle),
            arc["center"][1] + arc["radius"] * math.sin(angle))


def _curve2(segment: Mapping[str, Any], origin: Vec, axes: tuple[Vec, Vec, Vec]) -> dict[str, Any]:
    def point(value: Sequence[float]) -> Point2:
        delta = _sub(tuple(value), origin)
        return _dot(delta, axes[0]), _dot(delta, axes[1])
    result: dict[str, Any] = {"kind": segment["kind"], "start": point(segment["start"]), "end": point(segment["end"])}
    extremities = [result["start"], result["end"]]
    if segment["kind"] == "arc":
        center = point(segment["center"])
        result.update(center=center, radius=segment["radius_mm"],
                      angle=math.atan2(result["start"][1] - center[1], result["start"][0] - center[0]),
                      sweep=segment["sweep_radians"] * (1 if _dot(tuple(segment["normal"]), axes[2]) > 0 else -1))
        for angle in (0.0, math.pi / 2, math.pi, 3 * math.pi / 2):
            candidate = (center[0] + result["radius"] * math.cos(angle), center[1] + result["radius"] * math.sin(angle))
            if _on_arc(candidate, result, _POSITION_TOLERANCE_MM):
                extremities.append(candidate)
    result["bounds"] = (min(p[0] for p in extremities), min(p[1] for p in extremities),
                        max(p[0] for p in extremities), max(p[1] for p in extremities))
    return result


def _line_line(a: Mapping[str, Any], b: Mapping[str, Any], tolerance: float) -> tuple[list[Point2], bool]:
    u, v = _sub2(a["end"], a["start"]), _sub2(b["end"], b["start"])
    length_u, length_v = math.hypot(*u), math.hypot(*v)
    u, v = (u[0] / length_u, u[1] / length_u), (v[0] / length_v, v[1] / length_v)
    delta = _sub2(b["start"], a["start"])
    determinant = _cross2(u, v)
    if abs(determinant) <= 1e-12:
        if abs(_cross2(delta, u)) > tolerance:
            return [], False
        first = _dot(delta, u)
        second = first + _dot(_sub2(b["end"], b["start"]), u)
        low, high = max(0.0, min(first, second)), min(length_u, max(first, second))
        if low > high + tolerance:
            return [], False
        if high - low > tolerance:
            return [], True
        t = (low + high) / 2
        return [(a["start"][0] + u[0] * t, a["start"][1] + u[1] * t)], False
    t, s = _cross2(delta, v) / determinant, _cross2(delta, u) / determinant
    if -tolerance <= t <= length_u + tolerance and -tolerance <= s <= length_v + tolerance:
        return [(a["start"][0] + u[0] * t, a["start"][1] + u[1] * t)], False
    return [], False


def _line_arc(line: Mapping[str, Any], arc: Mapping[str, Any], tolerance: float) -> tuple[list[Point2], bool]:
    direction = _sub2(line["end"], line["start"])
    length = math.hypot(*direction)
    u = direction[0] / length, direction[1] / length
    delta = _sub2(arc["center"], line["start"])
    along, distance = _dot(delta, u), abs(_cross2(delta, u))
    radius = arc["radius"]
    if distance > radius + tolerance:
        return [], False
    offset = math.sqrt(max(0.0, (radius - distance) * (radius + distance)))
    points: list[Point2] = []
    for parameter in (along - offset, along + offset):
        candidate = (line["start"][0] + u[0] * parameter, line["start"][1] + u[1] * parameter)
        if (-tolerance <= parameter <= length + tolerance and _on_arc(candidate, arc, tolerance) and
                all(math.dist(candidate, point) > tolerance for point in points)):
            points.append(candidate)
    return points, False


def _arc_arc(a: Mapping[str, Any], b: Mapping[str, Any], tolerance: float) -> tuple[list[Point2], bool]:
    delta = _sub2(b["center"], a["center"])
    distance = math.hypot(*delta)
    ra, rb = a["radius"], b["radius"]
    if distance <= tolerance and abs(ra - rb) <= tolerance:
        for first, second in ((a, b), (b, a)):
            if any(_on_arc(point, second, tolerance, interior=True) for point in (first["start"], first["end"])):
                return [], True
            if _on_arc(_arc_point2(first, 0.5), second, tolerance):
                return [], True
        points = [point for point in (a["start"], a["end"], b["start"], b["end"])
                  if _on_arc(point, a, tolerance) and _on_arc(point, b, tolerance)]
        return points, False
    if distance <= tolerance or distance > ra + rb + tolerance or distance < abs(ra - rb) - tolerance:
        return [], False
    along = (distance + (ra - rb) * (ra + rb) / distance) / 2
    height_squared = (ra - along) * (ra + along)
    if height_squared < -tolerance * max(ra, rb) * 4:
        return [], False
    height = math.sqrt(max(0.0, height_squared))
    u = delta[0] / distance, delta[1] / distance
    base = (a["center"][0] + along * u[0], a["center"][1] + along * u[1])
    points: list[Point2] = []
    for sign in (-1, 1):
        point = (base[0] - sign * height * u[1], base[1] + sign * height * u[0])
        if (_on_arc(point, a, tolerance) and _on_arc(point, b, tolerance) and
                all(math.dist(point, old) > tolerance for old in points)):
            points.append(point)
    return points, False


def _intersections(loops: list[dict[str, Any]], axes: tuple[Vec, Vec, Vec], tolerance: float,
                   pair_budget: int = _MAX_INTERSECTION_PAIRS) -> int:
    origin = tuple(loops[0]["plane"]["origin"])
    entries: list[tuple[int, int, dict[str, Any]]] = []
    for loop_index, loop in enumerate(loops):
        curves = [_curve2(segment, origin, axes) for segment in loop["segments"]]
        perimeter = math.fsum(math.dist(curve["start"], curve["end"]) if curve["kind"] == "line" else
                              curve["radius"] * abs(curve["sweep"]) for curve in curves)
        area = math.fsum(_cross2(curve["start"], curve["end"]) if curve["kind"] == "line" else
                         _cross2(curve["center"], _sub2(curve["end"], curve["start"])) +
                         curve["radius"] ** 2 * curve["sweep"] for curve in curves) / 2
        if not math.isfinite(area) or abs(area) <= tolerance * perimeter:
            _reject("curve_loop_area_degenerate", loop_index=loop_index)
        loop["signed_area_mm2"] = area
        loop["perimeter_mm"] = perimeter
        entries.extend((loop_index, index, curve) for index, curve in enumerate(curves))
    entries.sort(key=lambda item: item[2]["bounds"][0])
    active: list[tuple[int, int, dict[str, Any]]] = []
    pairs = 0
    for entry in entries:
        loop_b, index_b, b = entry
        active = [old for old in active if old[2]["bounds"][2] >= b["bounds"][0] - tolerance]
        for loop_a, index_a, a in active:
            if a["bounds"][3] < b["bounds"][1] - tolerance or b["bounds"][3] < a["bounds"][1] - tolerance:
                continue
            pairs += 1
            if pairs > pair_budget:
                _reject("curve_intersection_budget_exceeded")
            if a["kind"] == b["kind"] == "line":
                contacts, overlap = _line_line(a, b, tolerance)
            elif a["kind"] == b["kind"] == "arc":
                contacts, overlap = _arc_arc(a, b, tolerance)
            else:
                contacts, overlap = _line_arc(a, b, tolerance) if a["kind"] == "line" else _line_arc(b, a, tolerance)
            if not contacts and not overlap:
                continue
            same_loop = loop_a == loop_b
            count = len(loops[loop_a]["segments"])
            adjacent = same_loop and ((index_a - index_b) % count in {1, count - 1})
            if adjacent and not overlap:
                if (index_a + 1) % count == index_b:
                    shared_a, shared_b = a["end"], b["start"]
                else:
                    shared_a, shared_b = a["start"], b["end"]
                if all(math.dist(point, shared_a) <= tolerance and math.dist(point, shared_b) <= tolerance for point in contacts):
                    continue
            _reject("curve_loop_self_intersection" if same_loop else "loop_boundaries_intersect_or_touch",
                    first_loop=loop_a, first_segment=index_a, second_loop=loop_b, second_segment=index_b, overlap=overlap)
        active.append(entry)
    return pairs


def _sample(loops: list[dict[str, Any]], chord: float) -> None:
    total_edges = 0
    for loop in loops:
        segments = loop["segments"]
        plans: list[tuple[dict[str, Any], int, float]] = []
        gap = loop["diagnostics"]["max_join_gap_mm"]
        all_coordinates = [abs(value) for segment in segments for value in (*segment["start"], *segment["end"])]
        rounding = 32 * math.ulp(max(all_coordinates, default=0.0))
        for segment in segments:
            if segment["kind"] == "line":
                if chord <= gap + rounding:
                    _reject("chord_tolerance_below_evidence_precision", loop_index=loop["loop_index"])
                plans.append((segment, 1, gap + rounding))
                total_edges += 1
                continue
            radius = segment["radius_mm"]
            radial_error = abs(math.dist(segment["center"], segment["end"]) - radius)
            reserved = radial_error + 2 * gap + rounding
            if chord <= reserved:
                _reject("chord_tolerance_below_evidence_precision", loop_index=loop["loop_index"])
            # r*(1-cos(theta/2)) = 2*r*sin(theta/4)^2.  asin is
            # stable for tiny tolerances where acos(1-t/r) loses precision.
            step = min(math.pi / 2, 4 * math.asin(math.sqrt(min(1.0, (chord - reserved) / (2 * radius)))))
            if step <= 0 or not math.isfinite(step):
                _reject("chord_tolerance_below_evidence_precision", loop_index=loop["loop_index"])
            count = max(1, math.ceil(abs(segment["sweep_radians"]) / step))
            total_edges += count
            if total_edges > _MAX_SAMPLED_EDGES:
                _reject("curve_sampling_limit_exceeded", limit=_MAX_SAMPLED_EDGES)
            bound = 2 * radius * math.sin(abs(segment["sweep_radians"]) / (4 * count)) ** 2 + reserved
            plans.append((segment, count, bound))
        if total_edges > _MAX_SAMPLED_EDGES:
            _reject("curve_sampling_limit_exceeded", limit=_MAX_SAMPLED_EDGES)
        samples: list[list[float]] = []
        for segment, count, _ in plans:
            samples.append(list(segment["start"]))
            if segment["kind"] == "arc":
                center = tuple(segment["center"])
                radial = _sub(tuple(segment["start"]), center)
                perpendicular = _cross(tuple(segment["normal"]), radial)
                for index in range(1, count):
                    angle = segment["sweep_radians"] * index / count
                    point = _add(center, _add(_mul(radial, math.cos(angle)), _mul(perpendicular, math.sin(angle))))
                    if not all(math.isfinite(value) for value in point):
                        _reject("non_finite_curve_sample")
                    samples.append(list(point))
        loop["sampled_points"] = samples
        loop["sampled_edge_count"] = len(samples)
        loop["diagnostics"]["chord_error_bound_mm"] = max(bound for _, _, bound in plans)
        loop["diagnostics"]["sampling"] = "Sagitta-bounded circular chords; the first point is not repeated at closure."


def _compare_world_curves(local: list[dict[str, Any]], world: list[dict[str, Any]], fit: Mapping[str, Any], tolerance: float) -> float:
    maximum = 0.0
    for local_loop, world_loop in zip(local, world):
        if len(local_loop["segments"]) != len(world_loop["segments"]):
            _reject("world_curve_topology_mismatch")
        for first, second in zip(local_loop["segments"], world_loop["segments"]):
            if first["kind"] != second["kind"]:
                _reject("world_curve_topology_mismatch")
            for key in (("start", "end", "center") if first["kind"] == "arc" else ("start", "end")):
                residual = math.dist(_mapped(tuple(first[key]), fit), second[key])
                maximum = max(maximum, residual)
                if residual > tolerance:
                    _reject("world_projected_curve_mismatch", field=key, residual_mm=residual)
            if first["kind"] == "arc":
                if (abs(first["radius_mm"] - second["radius_mm"]) > tolerance or
                        abs(first["sweep_radians"] - second["sweep_radians"]) > _ANGLE_TOLERANCE or
                        math.dist(_mapped_direction(tuple(first["normal"]), fit), second["normal"]) > _DIRECTION_TOLERANCE):
                    _reject("world_projected_arc_mismatch")
    return maximum


def derive_panel_curve_boundary(native: Mapping[str, Any], *, coordinate_space: str = "panel_local",
                                chord_tolerance_mm: float = 0.1) -> dict[str, Any]:
    """Derive verified line/minor-arc rings from a complete native panel read.

    ``native`` is the geometry result itself, with ``include_boundary`` evidence
    and independently checked, untagged DESP descriptors on every vertex.  WORLD
    additionally requires independent native WORLD control and derived points.
    A false raw boundary completeness flag caused solely by explicit PPEXST
    unset errors is retained; successful independent position/plane evidence can
    still support this narrower derived result.  Every unsupported or ambiguous
    branch returns an unavailable dictionary with empty geometry.
    """
    result = _empty_result(native, coordinate_space, chord_tolerance_mm)
    try:
        if not isinstance(coordinate_space, str) or coordinate_space not in {"panel_local", "WORLD"}:
            _reject("invalid_coordinate_space")
        chord = _number(chord_tolerance_mm, "chord_tolerance_mm")
        if chord <= 0:
            _reject("invalid_chord_tolerance_mm")
        result["chord_tolerance_mm"] = chord
        if not isinstance(native, Mapping):
            _reject("invalid_panel_geometry")
        records, pane_ref, world_ref = _read_native(native, coordinate_space == "WORLD")
        controls = [vertex["local"] for loop in records for vertex in loop["vertices"]]
        if coordinate_space == "WORLD":
            controls.extend(vertex["world"] for loop in records for vertex in loop["vertices"])
        tolerance = _tolerance(controls)
        if any(abs(vertex["local"][2]) > tolerance for loop in records for vertex in loop["vertices"]):
            _reject("controls_not_on_panel_local_definition_plane")
        existence: list[bool | None] = []
        for loop in records:
            for vertex in loop["vertices"]:
                if vertex["radius"] == 0:
                    continue
                vertex["local_evidence"] = _frame_evidence(vertex, "panel_local", pane_ref)
                if coordinate_space == "WORLD":
                    vertex["world_evidence"] = _frame_evidence(vertex, "WORLD", world_ref)
                selected = vertex["world_evidence"] if coordinate_space == "WORLD" else vertex["local_evidence"]
                existence.extend(selected["existence"])
        local_axes: tuple[Vec, Vec, Vec] = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        local = [_derive_loop(loop, "panel_local", local_axes, tolerance) for loop in records]
        if sum(loop["segment_count"] for loop in local) > _MAX_SEGMENTS:
            _reject("curve_segment_limit_exceeded")
        pairs = _intersections(local, local_axes, tolerance)
        selected_loops, axes = local, local_axes
        if coordinate_space == "WORLD":
            fit = _fit_world(records, tolerance)
            evidence_residuals = _verify_world_evidence(records, fit, tolerance)
            axes = fit["axes"]
            selected_loops = [_derive_loop(loop, "WORLD", axes, tolerance) for loop in records]
            curve_residual = _compare_world_curves(local, selected_loops, fit, tolerance)
            pairs += _intersections(selected_loops, axes, tolerance, _MAX_INTERSECTION_PAIRS - pairs)
            result["diagnostics"]["world_rigid_verification"] = {
                "source": "Rigid plane frame fitted from independent native local/WORLD PAVE POS; all native WORLD PPOS and PDIR checked independently.",
                "local_anchor": list(fit["local_anchor"]), "world_anchor": list(fit["world_anchor"]),
                "x_axis": list(axes[0]), "y_axis": list(axes[1]), "z_axis": list(axes[2]),
                "max_control_residual_mm": fit["max_control_residual_mm"],
                "max_projected_curve_residual_mm": curve_residual, **evidence_residuals,
                "raw_axis_lengths": list(fit["raw_axis_lengths"]), "raw_axis_dot": fit["raw_axis_dot"],
                "metric_tolerance": fit["metric_tolerance"],
            }
        _sample(selected_loops, chord)
        result.update(success=True, available=True, complete=True, loops=selected_loops,
                      loop_count=len(selected_loops),
                      segment_count=sum(loop["segment_count"] for loop in selected_loops),
                      line_count=sum(loop["line_count"] for loop in selected_loops),
                      arc_count=sum(loop["arc_count"] for loop in selected_loops),
                      sampled_edge_count=sum(loop["sampled_edge_count"] for loop in selected_loops),
                      native_existence_verified=all(value is True for value in existence) if existence else None)
        result["diagnostics"].update(position_tolerance_mm=tolerance, direction_tolerance=_DIRECTION_TOLERANCE,
                                     intersection_pairs_checked=pairs, analytic_intersection_checks=True,
                                     native_existence_unknown_count=sum(value is None for value in existence),
                                     p0_existence_inferred=False,
                                     max_chord_error_bound_mm=max(loop["diagnostics"]["chord_error_bound_mm"] for loop in selected_loops))
    except _Rejected as failure:
        result["errors"] = [failure.code]
        result["diagnostics"]["rejection"] = {"code": failure.code, **failure.context}
    except (OverflowError, ZeroDivisionError, ValueError) as failure:
        result["errors"] = ["non_finite_or_degenerate_curve_calculation"]
        result["diagnostics"]["rejection"] = {"code": result["errors"][0], "detail": str(failure)}
    return result
