"""Bounded indexed plane sections of closed outward triangle meshes.

Contours join by source vertex/edge identity, never by nearby coordinates.
They describe intersections with the input parameter mesh, not E3D CSG, hole
roles, native curves, or certified engineering cross-section properties.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from fractions import Fraction
from typing import Any

from .triangle_mesh_validation import validate_triangle_mesh


MAX_SECTION_SEGMENTS = 20_000
MAX_SECTION_DIAGNOSTICS = 100
_RELATION_TOLERANCE = 1e-12
_PARALLEL_TOLERANCE = 1e-12
_Key = tuple[Any, ...]


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_section_{field}")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"invalid_section_{field}") from None
    if not math.isfinite(number):
        raise ValueError(f"invalid_section_{field}")
    return number


def _vector(value: Any, field: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"invalid_section_{field}")
    return [_number(component, field) for component in value]


def _unit(value: list[float], field: str) -> list[float]:
    scale = max(map(abs, value))
    if scale == 0:
        raise ValueError(f"zero_section_{field}")
    scaled = [component / scale for component in value]
    magnitude = math.hypot(*scaled)
    return [component / magnitude for component in scaled]


def _cross(first, second) -> list[float]:
    return [first[1] * second[2] - first[2] * second[1],
            first[2] * second[0] - first[0] * second[2],
            first[0] * second[1] - first[1] * second[0]]


def _dot(first, second) -> float:
    return math.fsum(a * b for a, b in zip(first, second))


def normalize_section_plane(plane: Any) -> dict[str, Any]:
    """Validate a plane and return a right-handed orthonormal projection basis.

    Normal/up vectors are scaled before normalization, accepting finite huge
    and subnormal nonzero vectors without squaring them. Explicit near-parallel
    up is rejected. Default up tries Z, then Y, then X deterministically.
    """
    if not isinstance(plane, Mapping) or not {"origin", "normal"} <= set(plane) or set(plane) - {"origin", "normal", "up"}:
        raise ValueError("invalid_section_plane")
    origin = _vector(plane["origin"], "origin")
    if any(abs(component) > 1_000_000_000.0 for component in origin):
        raise ValueError("section_origin_out_of_bounds")
    normal = _unit(_vector(plane["normal"], "normal"), "normal")
    if "up" in plane:
        candidate = _unit(_vector(plane["up"], "up"), "up")
        cross = _cross(candidate, normal)
        if math.hypot(*cross) <= _PARALLEL_TOLERANCE:
            raise ValueError("section_up_parallel_to_normal")
        up_source = "explicit"
    else:
        for label, candidate in (("Z", [0.0, 0.0, 1.0]), ("Y", [0.0, 1.0, 0.0]), ("X", [1.0, 0.0, 0.0])):
            cross = _cross(candidate, normal)
            if math.hypot(*cross) > _PARALLEL_TOLERANCE:
                up_source = "default_" + label
                break
    right = _unit(cross, "right")
    up = _unit(_cross(normal, right), "orthogonal_up")
    return {"origin": origin, "normal": normal, "right": right, "up": up,
            "up_source": up_source,
            "coordinate_convention": "u along right=up cross normal; v along orthogonal up; origin is plane origin"}


def _failure(code: str, *, plane=None, validation=None, counts=None, diagnostics=None, complete=True) -> dict[str, Any]:
    return {"success": False, "available": False, "complete": complete, "error_code": code,
            "intersection": None, "plane": plane, "vertices": [], "segments": [], "loops": [], "bounds": None,
            "counts": {"source_vertices": (counts or {}).get("source_vertices", 0),
                       "source_triangles": (counts or {}).get("source_triangles", 0),
                       "vertices": 0, "segments": 0, "loops": 0},
            "diagnostics": (diagnostics or [])[:MAX_SECTION_DIAGNOSTICS], "validation": validation}


def _restore_power(value: float, scale: float, exponent: int) -> float | None:
    mantissa, binary_exponent = math.frexp(value)
    scale_mantissa, scale_exponent = math.frexp(scale)
    try:
        result = math.ldexp(mantissa * scale_mantissa ** exponent, binary_exponent + scale_exponent * exponent)
    except OverflowError:
        return None
    return result if math.isfinite(result) and (result != 0 or value == 0) else None


def _loop_metrics(indices: list[int], points: list[list[float]]) -> dict[str, Any]:
    anchor = points[indices[0]]
    relative = [[points[index][axis] - anchor[axis] for axis in range(2)] for index in indices]
    if any(not math.isfinite(value) for point in relative for value in point):
        return {"signed_area": None, "perimeter": None, "area_available": False, "perimeter_available": False}
    scale = max(abs(value) for point in relative for value in point)
    if scale == 0:
        return {"signed_area": 0.0, "perimeter": 0.0, "area_available": True, "perimeter_available": True}
    normalized = [[value / scale for value in point] for point in relative]
    area = math.fsum(left[0] * right[1] - right[0] * left[1]
                     for left, right in zip(normalized, normalized[1:])) / 2
    perimeter = math.fsum(math.hypot(right[0] - left[0], right[1] - left[1])
                          for left, right in zip(normalized, normalized[1:]))
    restored_area, restored_perimeter = _restore_power(area, scale, 2), _restore_power(perimeter, scale, 1)
    return {"signed_area": restored_area, "perimeter": restored_perimeter,
            "area_available": restored_area is not None, "perimeter_available": restored_perimeter is not None}


def slice_triangle_mesh(vertices: Any, triangles: Any, *, plane: Any) -> dict[str, Any]:
    """Intersect a validated closed outward mesh with an arbitrary plane.

    Vertex crossings and native edges shared by triangles on strictly opposite
    sides are supported. Coplanar triangles, tangent edges/isolated vertices,
    uncertain near-plane signs or contour branching fail without partial
    geometry. Nonempty successful contours are directed degree-two loops.
    """
    counts = {"source_vertices": len(vertices) if isinstance(vertices, (list, tuple)) else 0,
              "source_triangles": len(triangles) if isinstance(triangles, (list, tuple)) else 0}
    try:
        normalized_plane = normalize_section_plane(plane)
    except ValueError as exc:
        return _failure(str(exc), counts=counts)
    validation = validate_triangle_mesh(vertices, triangles)
    if validation.get("valid") is not True or validation.get("complete") is not True:
        return _failure("closed_valid_outward_mesh_required", plane=normalized_plane, validation=validation,
                        counts=counts, complete=validation.get("complete") is True)

    def fail(code, diagnostics=None):
        return _failure(code, plane=normalized_plane, validation=validation, counts=counts, diagnostics=diagnostics)

    origin, normal = normalized_plane["origin"], normalized_plane["normal"]
    points = [[float(value) for value in point] for point in vertices]
    faces = [tuple(face) for face in triangles]
    relative = [[point[axis] - origin[axis] for axis in range(3)] for point in points]
    if any(not math.isfinite(value) for point in relative for value in point):
        return fail("section_coordinate_difference_overflow")
    scale = max(abs(value) for point in relative for value in point)
    if scale == 0:
        return fail("section_zero_relative_extent")
    projected = [[value / scale for value in point] for point in relative]
    mesh_extent = max(max(point[axis] for point in points) - min(point[axis] for point in points) for axis in range(3))
    tolerance = _RELATION_TOLERANCE * (mesh_extent / scale)
    distances = [_dot(point, normal) for point in projected]
    # Normalization can turn an exact plane vertex into a tiny nonzero dot,
    # or underflow a small nonzero dot to zero. Confirm only these uncertain
    # cases against the original finite floats; never snap a nearby vertex.
    raw_normal = [Fraction(float(value)) for value in plane["normal"]]
    exact_origin = [Fraction(value) for value in origin]
    ambiguous = []
    for index, (point, distance) in enumerate(zip(projected, distances)):
        error = 64 * math.ulp(1.0) * math.fsum(abs(a * b) for a, b in zip(point, normal))
        if abs(distance) > max(tolerance, error):
            continue
        exact_distance = sum((Fraction(points[index][axis]) - exact_origin[axis]) * raw_normal[axis]
                             for axis in range(3))
        if exact_distance == 0:
            distances[index] = 0.0
        else:
            ambiguous.append(index)
    if ambiguous:
        return fail("section_plane_vertex_relation_ambiguous", [{"code": "near_plane_vertex", "vertex": index} for index in ambiguous[:MAX_SECTION_DIAGNOSTICS]])
    signs = [1 if distance > 0 else -1 if distance < 0 else 0 for distance in distances]
    coplanar = [index for index, face in enumerate(faces) if all(signs[vertex] == 0 for vertex in face)]
    if coplanar:
        return fail("section_coplanar_triangles_unsupported", [{"code": "coplanar_triangle", "triangle": index} for index in coplanar[:MAX_SECTION_DIAGNOSTICS]])

    edge_faces: dict[tuple[int, int], list[int]] = defaultdict(list)
    for face_index, face in enumerate(faces):
        for a, b in zip(face, (face[1], face[2], face[0])):
            edge_faces[(min(a, b), max(a, b))].append(face_index)
    keyed_points: dict[_Key, list[float]] = {}
    raw_segments: list[tuple[_Key, _Key, list[int]]] = []

    def vertex_key(index: int) -> _Key:
        key = ("vertex", index)
        keyed_points.setdefault(key, projected[index])
        return key

    def edge_key(a: int, b: int) -> _Key:
        a, b = min(a, b), max(a, b)
        key = ("edge", a, b)
        if key not in keyed_points:
            parameter = distances[a] / (distances[a] - distances[b])
            if not math.isfinite(parameter) or not 0 < parameter < 1:
                raise ValueError("section_edge_intersection_ambiguous")
            keyed_points[key] = [projected[a][axis] + (projected[b][axis] - projected[a][axis]) * parameter for axis in range(3)]
        return key

    for edge, adjacent in sorted(edge_faces.items()):
        if any(signs[vertex] != 0 for vertex in edge):
            continue
        third = [next(vertex for vertex in faces[index] if vertex not in edge) for index in adjacent]
        if len(adjacent) != 2 or signs[third[0]] * signs[third[1]] != -1:
            return fail("section_tangent_edges_unsupported", [{"code": "tangent_plane_edge", "edge": list(edge), "source_triangles": adjacent}])
        raw_segments.append((vertex_key(edge[0]), vertex_key(edge[1]), sorted(adjacent)))
    try:
        for face_index, face in enumerate(faces):
            zero = [vertex for vertex in face if signs[vertex] == 0]
            if len(zero) == 2:
                continue  # Opposite-side shared plane edge was emitted once above.
            if len(zero) == 1:
                remaining = [vertex for vertex in face if signs[vertex] != 0]
                if signs[remaining[0]] * signs[remaining[1]] == -1:
                    raw_segments.append((vertex_key(zero[0]), edge_key(*remaining), [face_index]))
            elif min(signs[vertex] for vertex in face) < 0 < max(signs[vertex] for vertex in face):
                cuts = [edge_key(a, b) for a, b in zip(face, (face[1], face[2], face[0])) if signs[a] * signs[b] == -1]
                if len(cuts) != 2:
                    return fail("section_triangle_intersection_ambiguous", [{"triangle": face_index}])
                raw_segments.append((cuts[0], cuts[1], [face_index]))
    except ValueError as exc:
        return fail(str(exc))
    if len(raw_segments) > MAX_SECTION_SEGMENTS:
        return fail("section_segment_limit_exceeded", [{"observed_segments": len(raw_segments), "limit": MAX_SECTION_SEGMENTS}])
    tangent_vertices = [index for index, sign in enumerate(signs) if sign == 0 and ("vertex", index) not in keyed_points]
    if tangent_vertices:
        return fail("section_tangent_vertices_unsupported", [{"code": "isolated_plane_vertex", "vertex": index} for index in tangent_vertices[:MAX_SECTION_DIAGNOSTICS]])
    sorted_keys = sorted(keyed_points)
    key_indices = {key: index for index, key in enumerate(sorted_keys)}
    result_points: list[list[float]] = []
    try:
        for key in sorted_keys:
            point = keyed_points[key]
            uv = [_dot(point, normalized_plane[axis]) * scale for axis in ("right", "up")]
            if any(not math.isfinite(value) for value in uv):
                return fail("section_projection_not_representable")
            result_points.append(uv)
    except OverflowError:
        return fail("section_projection_not_representable")

    result_segments = []
    seen_segments: set[tuple[int, int]] = set()
    degree: dict[int, list[int]] = defaultdict(list)
    outgoing: dict[int, list[int]] = defaultdict(list)
    incoming: dict[int, list[int]] = defaultdict(list)
    for a_key, b_key, source_faces in raw_segments:
        a, b = key_indices[a_key], key_indices[b_key]
        if result_points[a] == result_points[b]:
            return fail("section_zero_length_segment")
        source_face = faces[source_faces[0]]
        anchor, left, right = [projected[index] for index in source_face]
        try:
            face_normal = _unit(_cross(_unit([left[i] - anchor[i] for i in range(3)], "face_edge"),
                                      _unit([right[i] - anchor[i] for i in range(3)], "face_edge")), "face_normal")
            tangent = _unit(_cross(normal, face_normal), "section_tangent")
            direction = _unit([keyed_points[b_key][i] - keyed_points[a_key][i] for i in range(3)], "section_segment")
        except ValueError:
            return fail("section_segment_orientation_ambiguous")
        orientation = _dot(direction, tangent)
        if abs(orientation) <= _RELATION_TOLERANCE:
            return fail("section_segment_orientation_ambiguous")
        if orientation < 0:
            a, b = b, a
        undirected = (min(a, b), max(a, b))
        if undirected in seen_segments:
            return fail("section_duplicate_segment_ambiguous")
        seen_segments.add(undirected)
        segment_index = len(result_segments)
        result_segments.append({"vertices": [a, b], "source_triangles": source_faces})
        degree[a].append(segment_index)
        degree[b].append(segment_index)
        outgoing[a].append(segment_index)
        incoming[b].append(segment_index)
    bad_degree = [index for index in range(len(result_points)) if len(degree[index]) != 2]
    if bad_degree:
        return fail("section_contour_not_degree_two", [{"vertex": index, "degree": len(degree[index])} for index in bad_degree[:MAX_SECTION_DIAGNOSTICS]])
    if any(len(outgoing[index]) != 1 or len(incoming[index]) != 1 for index in range(len(result_points))):
        return fail("section_contour_orientation_ambiguous")
    unseen = set(range(len(result_points)))
    loops = []
    for start in range(len(result_points)):
        if start not in unseen:
            continue
        current = start
        loop_vertices, loop_segments = [start], []
        while True:
            if current not in unseen:
                return fail("section_loop_traversal_ambiguous")
            unseen.remove(current)
            segment_index = outgoing[current][0]
            current = result_segments[segment_index]["vertices"][1]
            loop_segments.append(segment_index)
            loop_vertices.append(current)
            if current == start:
                break
        if len(loop_segments) < 3:
            return fail("section_degenerate_loop")
        metrics = _loop_metrics(loop_vertices, result_points)
        if metrics["area_available"] and metrics["signed_area"] == 0:
            return fail("section_degenerate_loop")
        loops.append({"vertices": loop_vertices, "segment_indices": loop_segments, **metrics})
    bounds = ({"min": [min(point[axis] for point in result_points) for axis in range(2)],
               "max": [max(point[axis] for point in result_points) for axis in range(2)]} if result_points else None)
    metric_diagnostics = [{"code": "section_loop_metric_not_representable", "loop": index,
                           "area_available": loop["area_available"], "perimeter_available": loop["perimeter_available"]}
                          for index, loop in enumerate(loops)
                          if not loop["area_available"] or not loop["perimeter_available"]]
    return {
        "success": True, "available": True, "complete": True, "error_code": None,
        "intersection": bool(result_segments), "plane": normalized_plane, "vertices": result_points,
        "segments": result_segments, "loops": loops, "bounds": bounds,
        "counts": {**counts, "vertices": len(result_points), "segments": len(result_segments), "loops": len(loops)},
        "diagnostics": metric_diagnostics[:MAX_SECTION_DIAGNOSTICS], "validation": validation,
        "coordinate_frame": "plane-relative coordinates along right/up basis", "coordinate_unit": "caller_supplied; not inferred",
        "area_unit": "coordinate_unit^2; not inferred", "length_unit": "coordinate_unit; not inferred",
        "join_policy": "source vertex and indexed edge identity; no coordinate-neighbour welding",
        "hole_semantics_inferred": False, "boolean_operations_applied": False,
        "geometric_self_intersection_checked": False,
        "scope": "Intersection of an indexed parameter mesh with one plane. No geometric self-intersection check, CSG, hole roles or net section area certification.",
    }
