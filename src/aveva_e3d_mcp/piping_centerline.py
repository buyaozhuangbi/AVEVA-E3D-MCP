"""Analytic pipe centreline candidates from context-bound native P-points."""
from __future__ import annotations

import math
from typing import Any


MAX_WAYPOINTS = 32
MAX_COORDINATE_MM = 1_000_000_000
_FIELDS = {
    "start_endpoint_index", "end_endpoint_index", "waypoints_world_mm", "bend_radius_mm",
    "minimum_straight_mm", "position_tolerance_mm", "direction_tolerance_degrees",
    "start_direction_sense", "end_direction_sense",
}


def _number(value: Any, field: str, minimum: float, maximum: float) -> float:
    # Check the range before float conversion so arbitrary-size JSON integers
    # cannot overflow the converter or math.isfinite.
    if (type(value) not in (int, float) or not minimum <= value <= maximum):
        raise ValueError(f"{field} must be a finite number from {minimum} through {maximum}")
    return float(value)


def centerline_options(raw: Any, endpoint_count: int) -> dict[str, Any]:
    """Validate every caller choice before any E3D request is made."""
    if not isinstance(raw, dict) or set(raw) != _FIELDS:
        raise ValueError("centerline requires exactly " + ", ".join(sorted(_FIELDS)))
    result: dict[str, Any] = {}
    for key in ("start_endpoint_index", "end_endpoint_index"):
        index = raw[key]
        if type(index) is not int or not 0 <= index < endpoint_count:
            raise ValueError(f"centerline.{key} must index an explicitly supplied endpoint")
        result[key] = index
    if result["start_endpoint_index"] == result["end_endpoint_index"]:
        raise ValueError("centerline start and end must use distinct endpoint indices")
    for key in ("start_direction_sense", "end_direction_sense"):
        if type(raw[key]) is not int or raw[key] not in (-1, 1):
            raise ValueError(f"centerline.{key} must be 1 (along native PDIR) or -1 (opposed)")
        result[key] = raw[key]
    for key, minimum, maximum in (
        ("bend_radius_mm", 0.000001, 1_000_000),
        ("minimum_straight_mm", 0, 1_000_000),
        ("position_tolerance_mm", 0.000001, 1000),
        ("direction_tolerance_degrees", 0, 90),
    ):
        result[key] = _number(raw[key], "centerline." + key, minimum, maximum)
    points = raw["waypoints_world_mm"]
    if not isinstance(points, list) or len(points) > MAX_WAYPOINTS:
        raise ValueError(f"centerline.waypoints_world_mm must contain at most {MAX_WAYPOINTS} points")
    result["waypoints_world_mm"] = []
    for index, point in enumerate(points):
        if not isinstance(point, list) or len(point) != 3:
            raise ValueError(f"centerline.waypoints_world_mm[{index}] must contain three WORLD millimetre coordinates")
        result["waypoints_world_mm"].append([
            _number(value, f"centerline.waypoints_world_mm[{index}]", -MAX_COORDINATE_MM, MAX_COORDINATE_MM)
            for value in point
        ])
    return result


def _sub(a: list[float], b: list[float]) -> list[float]:
    return [a[i] - b[i] for i in range(3)]


def _offset(point: list[float], direction: list[float], distance: float) -> list[float]:
    return [point[i] + direction[i] * distance for i in range(3)]


def _dot(a: list[float], b: list[float]) -> float:
    return math.fsum(a[i] * b[i] for i in range(3))


def _cross(a: list[float], b: list[float]) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _segment_distance_mm(p0: list[float], p1: list[float], q0: list[float], q1: list[float]) -> float:
    """Return the minimum 3D distance between two finite line segments."""
    u, v, w = _sub(p1, p0), _sub(q1, q0), _sub(p0, q0)
    a, b, c = _dot(u, u), _dot(u, v), _dot(v, v)
    d, e = _dot(u, w), _dot(v, w)
    cross_uv = _cross(u, v)
    denominator = _dot(cross_uv, cross_uv)
    product_scale = max(abs(u[i] * v[j]) for i in range(3) for j in range(3) if i != j)
    parallel = math.hypot(*cross_uv) <= 8 * math.ulp(product_scale) if product_scale else True

    if parallel:
        s_num, s_den, t_num, t_den = 0.0, 1.0, e, c
    else:
        s_den = t_den = denominator
        # Cross-product forms avoid subtracting two nearly equal O(L^4)
        # terms when long segments meet at a shallow angle.
        s_num = -_dot(_cross(w, v), cross_uv)
        t_num = -_dot(_cross(w, u), cross_uv)
        if s_num < 0:
            s_num, t_num, t_den = 0.0, e, c
        elif s_num > s_den:
            s_num, t_num, t_den = s_den, e + b, c

    if t_num < 0:
        t_num = 0.0
        if -d < 0:
            s_num, s_den = 0.0, 1.0
        elif -d > a:
            s_num, s_den = 1.0, 1.0
        else:
            s_num, s_den = -d, a
    elif t_num > t_den:
        t_num = t_den
        if -d + b < 0:
            s_num, s_den = 0.0, 1.0
        elif -d + b > a:
            s_num, s_den = 1.0, 1.0
        else:
            s_num, s_den = -d + b, a

    s = 0.0 if abs(s_num) <= 1e-30 else s_num / s_den
    t = 0.0 if abs(t_num) <= 1e-30 else t_num / t_den
    return math.hypot(*(w[axis] + s * u[axis] - t * v[axis] for axis in range(3)))


def _unit(vector: list[float]) -> list[float]:
    length = math.hypot(*vector)
    return [value / length for value in vector]


def _native_vector(field: Any, *, position: bool) -> list[float] | None:
    if (not isinstance(field, dict) or field.get("available") is not True or field.get("error") is not None
            or field.get("attribute") != ("PPOS" if position else "PDIR")
            or position and field.get("unit") != "mm"):
        return None
    values = [field.get(axis) for axis in ("x", "y", "z")]
    bound = MAX_COORDINATE_MM if position else 1.000001
    if any(type(value) not in (int, float) or not -bound <= value <= bound for value in values):
        return None
    vector = [float(value) for value in values]
    if not position and abs(math.hypot(*vector) - 1) > 0.000001:
        return None
    return vector


def _endpoint(groups: list[dict[str, Any]], index: int) -> dict[str, Any]:
    result: dict[str, Any] = {"endpoint_index": index, "position_world_mm": None, "pdir_world": None,
                              "element": None, "point_number": None, "errors": []}
    if index >= len(groups):
        result["errors"].append("endpoint_not_observed")
        return result
    group = groups[index]
    binding = group.get("binding")
    reply = group.get("response")
    native = reply.get("result") if isinstance(reply, dict) else None
    if (not isinstance(binding, dict) or binding.get("matches_enclosing_context") is not True
            or not isinstance(reply, dict) or reply.get("ok") is not True or reply.get("version") != "E3D21"
            or not isinstance(native, dict) or native.get("success") is not True):
        result["errors"].append("endpoint_context_or_identity_unverified")
        return result
    frame = native.get("frame")
    if (not isinstance(frame, dict) or frame.get("requested") != "WORLD" or frame.get("type") != "WORL"):
        result["errors"].append("endpoint_world_frame_unverified")
        return result
    number = group.get("point_number")
    points = native.get("points")
    selected = [point for point in points if isinstance(point, dict) and type(point.get("number")) is int
                and point["number"] == number] if isinstance(points, list) else []
    if len(selected) != 1:
        result["errors"].append("endpoint_point_not_uniquely_observed")
        return result
    point = selected[0]
    exists = point.get("exists")
    if (not isinstance(exists, dict) or exists.get("available") is not True or exists.get("value") is not True
            or exists.get("attribute") != "PPEXST" or exists.get("error") is not None):
        result["errors"].append("endpoint_existence_unverified")
        return result
    result.update(element=native.get("element"), point_number=number)
    point_errors = point.get("errors")
    native_errors = native.get("errors")
    for field, output in (("position", "position_world_mm"), ("direction", "pdir_world")):
        vector = _native_vector(point.get(field), position=field == "position")
        # A contradictory per-field error must not be hidden by available=true.
        if ((isinstance(point_errors, dict) and point_errors.get(field)) or
                (isinstance(native_errors, dict) and native_errors.get(f"P{number}.{field}"))):
            vector = None
        result[output] = vector
        if vector is None:
            result["errors"].append("endpoint_" + field + "_unverified")
    return result


def _alignment(tangent: list[float], pdir: list[float] | None, sense: int, tolerance: float) -> dict[str, Any]:
    result: dict[str, Any] = {"direction_sense": sense, "tolerance_degrees": tolerance,
                              "angle_degrees": None, "within_tolerance": None}
    if pdir is not None:
        expected = [sense * value for value in _unit(pdir)]
        angle = math.degrees(math.atan2(math.hypot(*_cross(tangent, expected)), _dot(tangent, expected)))
        result.update(angle_degrees=angle, within_tolerance=angle <= tolerance)
    return result


def plan_centerline(options: dict[str, Any], endpoints: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate a bounded filleted polyline; never call SDK or write model data."""
    first = _endpoint(endpoints, options["start_endpoint_index"])
    last = _endpoint(endpoints, options["end_endpoint_index"])
    result: dict[str, Any] = {
        "requested": True, "available": False, "complete": False, "status": "endpoint_evidence_unavailable",
        "frame": "WORLD", "unit": "mm", "options": options, "endpoints": {"start": first, "end": last},
        "vertices_world_mm": [], "omitted_collinear_vertex_indices": [], "legs": [], "bend_candidates": [],
        "elements": [], "polyline_length_mm": None, "centerline_length_mm": None,
        "geometry_fits": None, "geometric_fit": None, "endpoint_alignment": None,
        "diagnostics": [], "catalogue_verified": False, "planned_member_geometry_verified": False,
        "execution_prepared": False,
        "scope": "Analytic straight/circular centreline with caller-supplied radius, minimum straight and direction senses. "
                 "Checks local bend space, endpoint tangents and non-adjacent intersections in the input waypoint polyline. "
                 "The polyline check does not test the filleted arcs, obstacles, clearance, selected catalogue dimensions, "
                 "pipe outside diameter, specifications or engineering suitability. "
                 "Does not assign E3D ELBO POS/ORI/SPREF or verify the separate planned_members geometry.",
    }
    if first["position_world_mm"] is None or last["position_world_mm"] is None:
        return result
    result["available"] = True
    vertices = [first["position_world_mm"], *options["waypoints_world_mm"], last["position_world_mm"]]
    result["vertices_world_mm"] = vertices
    epsilon = max(1e-9, 32 * math.ulp(max(abs(value) for vertex in vertices for value in vertex)))
    lengths = [math.dist(a, b) for a, b in zip(vertices, vertices[1:])]
    result["polyline_length_mm"] = math.fsum(lengths)
    result["complete"] = first["pdir_world"] is not None and last["pdir_world"] is not None
    for index, length in enumerate(lengths):
        if length <= max(options["position_tolerance_mm"], epsilon):
            result["diagnostics"].append({"code": "coincident_or_short_leg", "vertex_indices": [index, index + 1],
                                           "length_mm": length})
    if result["diagnostics"]:
        result.update(status="geometrically_blocked", geometry_fits=False, geometric_fit=False)
        return result

    for index in range(1, len(vertices) - 1):
        incoming = _unit(_sub(vertices[index], vertices[index - 1]))
        outgoing = _unit(_sub(vertices[index + 1], vertices[index]))
        if math.hypot(*_cross(incoming, outgoing)) <= 1e-12 and _dot(incoming, outgoing) < 0:
            result["diagnostics"].append({"code": "route_reversal", "vertex_index": index})
    if result["diagnostics"]:
        result.update(status="geometrically_blocked", geometry_fits=False, geometric_fit=False)
        return result

    # Only non-adjacent input edges can cross without sharing an intentional
    # waypoint. This finite-segment distance test is in 3D, not an arc or
    # clearance test; the threshold covers floating-point rounding at WORLD scale.
    for first_segment in range(len(vertices) - 1):
        for second_segment in range(first_segment + 2, len(vertices) - 1):
            separation = _segment_distance_mm(vertices[first_segment], vertices[first_segment + 1],
                                              vertices[second_segment], vertices[second_segment + 1])
            if separation <= epsilon:
                result["diagnostics"].append({
                    "code": "waypoint_polyline_self_intersection",
                    "segment_indices": [first_segment, second_segment],
                    "separation_mm": separation,
                    "rounding_tolerance_mm": epsilon,
                })
    if result["diagnostics"]:
        result.update(status="geometrically_blocked", geometry_fits=False, geometric_fit=False)
        return result

    # Collinear intermediate markers do not create artificial short spools.
    indices: list[int] = []
    for index in range(len(vertices)):
        indices.append(index)
        while len(indices) >= 3:
            a, b, c = (vertices[i] for i in indices[-3:])
            incoming, outgoing = _sub(b, a), _sub(c, b)
            distance_from_line = math.hypot(*_cross(_unit(incoming), outgoing))
            if _dot(_unit(incoming), _unit(outgoing)) <= 0 or distance_from_line > epsilon:
                break
            result["omitted_collinear_vertex_indices"].append(indices.pop(-2))
    directions = [_unit(_sub(vertices[b], vertices[a])) for a, b in zip(indices, indices[1:])]
    lengths = [math.dist(vertices[a], vertices[b]) for a, b in zip(indices, indices[1:])]
    alignment = {
        "start": _alignment(directions[0], first["pdir_world"], options["start_direction_sense"], options["direction_tolerance_degrees"]),
        "end": _alignment(directions[-1], last["pdir_world"], options["end_direction_sense"], options["direction_tolerance_degrees"]),
    }
    result["endpoint_alignment"] = alignment
    radius = options["bend_radius_mm"]
    bends: dict[int, dict[str, Any]] = {}
    for index in range(1, len(indices) - 1):
        incoming, outgoing = directions[index - 1:index + 1]
        cross = _cross(incoming, outgoing)
        sine, cosine = math.hypot(*cross), _dot(incoming, outgoing)
        if sine <= 1e-12 and cosine < 0:
            result["diagnostics"].append({"code": "route_reversal", "vertex_index": indices[index]})
            continue
        angle = math.atan2(sine, cosine)
        takeout = radius * math.tan(angle / 2)
        normal = _unit(cross)
        vertex = vertices[indices[index]]
        entry = _offset(vertex, incoming, -takeout)
        exit_point = _offset(vertex, outgoing, takeout)
        center = _offset(entry, _cross(normal, incoming), radius)
        radial = [-value for value in _cross(normal, incoming)]
        bends[index] = {
            "kind": "circular_bend", "vertex_index": indices[index], "intersection_world_mm": vertex,
            "radius_mm": radius, "turn_degrees": math.degrees(angle), "sweep_radians": angle,
            "takeout_mm": takeout, "length_mm": radius * angle,
            "start_world_mm": entry, "end_world_mm": exit_point, "center_world_mm": center,
            "incoming_direction": incoming, "outgoing_direction": outgoing,
            "normal_world": normal, "radial_x_world": radial, "radial_y_world": _cross(normal, radial),
        }
    result["bend_candidates"] = list(bends.values())
    if result["diagnostics"]:
        result.update(status="geometrically_blocked", geometry_fits=False, geometric_fit=False)
        return result

    elements = []
    for index, length in enumerate(lengths):
        preceding, following = bends.get(index), bends.get(index + 1)
        takeout = (preceding["takeout_mm"] if preceding else 0) + (following["takeout_mm"] if following else 0)
        remaining = length - takeout
        leg = {"vertex_indices": indices[index:index + 2], "length_mm": length,
               "required_takeout_mm": takeout, "remaining_straight_mm": remaining,
               "minimum_straight_mm": options["minimum_straight_mm"]}
        result["legs"].append(leg)
        # Only floating-point roundoff can be clamped to zero. Caller position
        # tolerance never admits overlapping bends or negative pipe lengths.
        if remaining < -epsilon:
            result["diagnostics"].append({"code": "bend_takeouts_overlap", **leg, "deficit_mm": -remaining})
        elif max(0, remaining) + epsilon < options["minimum_straight_mm"]:
            result["diagnostics"].append({"code": "minimum_straight_not_met", **leg,
                                           "deficit_mm": options["minimum_straight_mm"] - max(0, remaining)})
        if remaining > epsilon:
            elements.append({"kind": "straight", "vertex_indices": indices[index:index + 2],
                             "start_world_mm": preceding["end_world_mm"] if preceding else vertices[indices[index]],
                             "end_world_mm": following["start_world_mm"] if following else vertices[indices[index + 1]],
                             "direction_world": directions[index], "length_mm": remaining})
        if following:
            elements.append(following)
    result["geometry_fits"] = not result["diagnostics"]
    if result["geometry_fits"]:
        result["elements"] = elements
        result["centerline_length_mm"] = math.fsum(element["length_mm"] for element in elements)
    alignment_values = [item["within_tolerance"] for item in alignment.values()]
    for endpoint, check in alignment.items():
        if check["within_tolerance"] is False:
            result["diagnostics"].append({"code": "endpoint_direction_mismatch", "endpoint": endpoint, **check})
    if not result["geometry_fits"] or False in alignment_values:
        result.update(status="geometrically_blocked", geometric_fit=False)
    elif None in alignment_values:
        result["status"] = "endpoint_direction_unavailable"
    else:
        result.update(status="computed", geometric_fit=True)
    return result


def centerline_schema() -> dict[str, Any]:
    return {"type": "object", "description": "Optional analytic WORLD centreline using two existing endpoint indices. "
            "Radius, minimum straight, tolerances and tangent/PDIR senses are explicit caller choices. "
            "The centreline does not populate or verify planned_members and is not a native create preview.",
        "properties": {
            "start_endpoint_index": {"type": "integer", "minimum": 0, "maximum": 15},
            "end_endpoint_index": {"type": "integer", "minimum": 0, "maximum": 15},
            "waypoints_world_mm": {"type": "array", "maxItems": MAX_WAYPOINTS, "items": {
                "type": "array", "minItems": 3, "maxItems": 3, "items": {
                    "type": "number", "minimum": -MAX_COORDINATE_MM, "maximum": MAX_COORDINATE_MM}}},
            "bend_radius_mm": {"type": "number", "minimum": 0.000001, "maximum": 1_000_000},
            "minimum_straight_mm": {"type": "number", "minimum": 0, "maximum": 1_000_000},
            "position_tolerance_mm": {"type": "number", "minimum": 0.000001, "maximum": 1000},
            "direction_tolerance_degrees": {"type": "number", "minimum": 0, "maximum": 90},
            "start_direction_sense": {"type": "integer", "enum": [-1, 1],
                "description": "Required start-to-end travel tangent: 1 along native start PDIR, -1 opposed."},
            "end_direction_sense": {"type": "integer", "enum": [-1, 1],
                "description": "Required start-to-end arrival tangent: 1 along native end PDIR, -1 opposed."},
        }, "required": sorted(_FIELDS), "additionalProperties": False}
