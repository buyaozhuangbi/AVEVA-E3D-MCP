"""Bounded orthographic visibility intervals for indexed triangle meshes.

Coordinates are caller-supplied projected ``[u, v, depth]`` triples. Larger
depth values are closer to the viewer. The helper uses projected triangle
interiors and affine depth interpolation; it does not infer E3D surface or
Boolean semantics.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


MAX_VERTICES = 10_000
MAX_TRIANGLES = 20_000
MAX_EDGES = 20_000
MAX_NODE_VISITS = 10_000_000
MAX_TRIANGLE_TESTS = 5_000_000
MAX_OUTPUT_INTERVALS = 1_000_000
_LEAF_TRIANGLES = 8
_PARAMETER_TOLERANCE = 1e-12
_DEPTH_TOLERANCE = 1e-10
_AREA_TOLERANCE = 1e-14


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _cross2(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not intervals:
        return []
    ordered = sorted((max(0.0, low), min(1.0, high)) for low, high in intervals if high > low)
    merged: list[list[float]] = []
    for low, high in ordered:
        if not merged or low > merged[-1][1] + _PARAMETER_TOLERANCE:
            merged.append([low, high])
        else:
            merged[-1][1] = max(merged[-1][1], high)
    return [(low, high) for low, high in merged]


def _clip_segment_to_triangle(p0: tuple[float, float], p1: tuple[float, float],
                              triangle: tuple[tuple[float, float, float], ...]) -> tuple[float, float] | None:
    """Clip a segment against a projected triangle using normalized half-planes."""
    a, b, c = triangle
    signed_area = _cross2(b[0] - a[0], b[1] - a[1], c[0] - a[0], c[1] - a[1])
    if not math.isfinite(signed_area) or abs(signed_area) <= _AREA_TOLERANCE:
        return None
    orientation = 1.0 if signed_area > 0.0 else -1.0
    lower, upper = 0.0, 1.0
    for left, right in ((a, b), (b, c), (c, a)):
        ex, ey = right[0] - left[0], right[1] - left[1]

        def signed_distance(point: tuple[float, float]) -> float:
            return orientation * _cross2(ex, ey, point[0] - left[0], point[1] - left[1])

        f0, f1 = signed_distance(p0), signed_distance(p1)
        delta = f1 - f0
        if abs(delta) <= _AREA_TOLERANCE:
            if f0 < -_AREA_TOLERANCE:
                return None
            continue
        crossing = (-_AREA_TOLERANCE - f0) / delta
        if delta > 0.0:
            lower = max(lower, crossing)
        else:
            upper = min(upper, crossing)
        if lower > upper + _PARAMETER_TOLERANCE:
            return None
    lower, upper = max(0.0, lower), min(1.0, upper)
    return (lower, upper) if upper - lower > _PARAMETER_TOLERANCE else None


def _triangle_depth_at(triangle: tuple[tuple[float, float, float], ...],
                       point: tuple[float, float]) -> float | None:
    a, b, c = triangle
    denominator = _cross2(b[0] - a[0], b[1] - a[1], c[0] - a[0], c[1] - a[1])
    if not math.isfinite(denominator) or abs(denominator) <= _AREA_TOLERANCE:
        return None
    wa = _cross2(b[0] - point[0], b[1] - point[1], c[0] - point[0], c[1] - point[1]) / denominator
    wb = _cross2(c[0] - point[0], c[1] - point[1], a[0] - point[0], a[1] - point[1]) / denominator
    wc = 1.0 - wa - wb
    depth = wa * a[2] + wb * b[2] + wc * c[2]
    return depth if math.isfinite(depth) else None


def _closer_interval(edge: tuple[tuple[float, float, float], tuple[float, float, float]],
                     triangle: tuple[tuple[float, float, float], ...],
                     clipped: tuple[float, float], depth_epsilon: float,
                     shared_endpoints: tuple[bool, bool],
                     ) -> tuple[float, float] | None:
    low, high = clipped
    p0, p1 = edge

    def depth_difference(parameter: float) -> float | None:
        point = (p0[0] + (p1[0] - p0[0]) * parameter,
                 p0[1] + (p1[1] - p0[1]) * parameter)
        triangle_depth = _triangle_depth_at(triangle, point)
        if triangle_depth is None:
            return None
        edge_depth = p0[2] + (p1[2] - p0[2]) * parameter
        difference = triangle_depth - edge_depth - depth_epsilon
        return difference if math.isfinite(difference) else None

    f0, f1 = depth_difference(low), depth_difference(high)
    if f0 is None or f1 is None or (f0 <= 0.0 and f1 <= 0.0):
        return None
    # A shared vertex at a clipped endpoint establishes exact depth equality
    # there. Depth difference is affine on the clipped interval, so if the
    # other endpoint is definitely occluded, the interior is too. Solving a
    # crossing with the depth tolerance would leave a spurious visible stub.
    if (low == 0.0 and shared_endpoints[0] and f1 > 0.0) or (
            high == 1.0 and shared_endpoints[1] and f0 > 0.0):
        return low, high
    if f0 > 0.0 and f1 > 0.0:
        return low, high
    if f0 == f1:
        return None
    crossing = low + (high - low) * (-f0) / (f1 - f0)
    crossing = min(high, max(low, crossing))
    if f0 > 0.0:
        return (low, crossing) if crossing - low > _PARAMETER_TOLERANCE else None
    return (crossing, high) if high - crossing > _PARAMETER_TOLERANCE else None


class _BudgetExceeded(Exception):
    pass


class _BVHNode:
    __slots__ = ("bounds", "left", "right", "indices")

    def __init__(self, bounds: tuple[float, float, float, float], *,
                 left: _BVHNode | None = None, right: _BVHNode | None = None,
                 indices: tuple[int, ...] = ()) -> None:
        self.bounds = bounds
        self.left = left
        self.right = right
        self.indices = indices


def _build_bvh(indices: list[int], bounds: list[tuple[float, float, float, float]], depth: int = 0) -> _BVHNode:
    node_bounds = (min(bounds[index][0] for index in indices), min(bounds[index][1] for index in indices),
                   max(bounds[index][2] for index in indices), max(bounds[index][3] for index in indices))
    if len(indices) <= _LEAF_TRIANGLES:
        return _BVHNode(node_bounds, indices=tuple(sorted(indices)))
    axis = depth % 2
    ordered = sorted(indices, key=lambda index: ((bounds[index][axis] + bounds[index][axis + 2]) * 0.5, index))
    middle = len(ordered) // 2
    return _BVHNode(node_bounds,
                    left=_build_bvh(ordered[:middle], bounds, depth + 1),
                    right=_build_bvh(ordered[middle:], bounds, depth + 1))


def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def projected_edge_visibility(
    vertices: Any,
    triangles: Any,
    edges: Any,
    *,
    max_node_visits: int = 2_000_000,
    max_triangle_tests: int = 2_000_000,
    max_output_intervals: int = 100_000,
) -> dict[str, Any]:
    """Classify visible and occluded parameter intervals for projected edges.

    ``vertices`` contains finite ``[u, v, depth]`` values in one orthographic
    coordinate frame. Larger depth means closer to the viewer. ``triangles``
    contains indexed triangle triples. ``edges`` contains mappings with a
    ``vertices`` pair; ``incident_faces`` may name additional source faces to
    exclude. Topological incident faces are always inferred and excluded.

    Each returned interval is a closed-range approximation ``[t0, t1]`` on the
    original directed edge (0 at its first vertex, 1 at its second). Inputs and
    work are bounded. If a hard work budget is exhausted, no partial intervals
    are returned and ``complete`` is false.
    """
    counts = {"vertices": 0, "triangles": 0, "edges": 0,
              "bvh_node_visits": 0, "edge_triangle_tests": 0}
    if (not isinstance(vertices, (list, tuple)) or not isinstance(triangles, (list, tuple)) or
            not isinstance(edges, (list, tuple))):
        return _failure("vertices_triangles_edges_must_be_arrays", counts)
    counts.update({"vertices": len(vertices), "triangles": len(triangles), "edges": len(edges)})
    if len(vertices) > MAX_VERTICES or len(triangles) > MAX_TRIANGLES or len(edges) > MAX_EDGES:
        return _failure("visibility_input_limit_exceeded", counts, complete=False)
    if (type(max_node_visits) is not int or not 1 <= max_node_visits <= MAX_NODE_VISITS or
            type(max_triangle_tests) is not int or not 1 <= max_triangle_tests <= MAX_TRIANGLE_TESTS or
            type(max_output_intervals) is not int or not 1 <= max_output_intervals <= MAX_OUTPUT_INTERVALS):
        return _failure("invalid_visibility_work_budget", counts)
    if not vertices or not triangles or not edges:
        return _failure("nonempty_vertices_triangles_and_edges_required", counts)

    parsed_vertices: list[tuple[float, float, float]] = []
    for index, vertex in enumerate(vertices):
        if not isinstance(vertex, (list, tuple)) or len(vertex) != 3:
            return _failure("vertex_must_have_projected_u_v_depth", counts, vertex=index)
        point = tuple(_finite_number(value) for value in vertex)
        if any(value is None for value in point):
            return _failure("vertex_coordinate_not_finite_number", counts, vertex=index)
        parsed_vertices.append(tuple(float(value) for value in point))

    # Normalize the projection plane independently from depth. A single scale
    # would flatten otherwise valid projected triangles for a very deep scene.
    minima = [min(point[axis] for point in parsed_vertices) for axis in range(3)]
    maxima = [max(point[axis] for point in parsed_vertices) for axis in range(3)]
    extents = [maxima[axis] - minima[axis] for axis in range(3)]
    if not all(math.isfinite(value) for value in extents):
        return _failure("coordinate_extent_overflow", counts)
    projected_scale = max(extents[0], extents[1])
    if projected_scale == 0.0:
        projected_scale = 1.0
    depth_scale = max(extents[2], projected_scale)
    points = [((point[0] - minima[0]) / projected_scale,
               (point[1] - minima[1]) / projected_scale,
               (point[2] - minima[2]) / depth_scale) for point in parsed_vertices]
    if not all(math.isfinite(value) for point in points for value in point):
        return _failure("coordinate_normalization_failed", counts)
    depth_epsilon = _DEPTH_TOLERANCE

    parsed_triangles: list[tuple[int, int, int]] = []
    face_edges: dict[tuple[int, int], set[int]] = defaultdict(set)
    projected_triangles: list[tuple[tuple[float, float, float], ...]] = []
    projected_source_faces: list[int] = []
    triangle_bounds: list[tuple[float, float, float, float]] = []
    for face_index, face in enumerate(triangles):
        if not isinstance(face, (list, tuple)) or len(face) != 3 or any(
                type(value) is not int or not 0 <= value < len(points) for value in face):
            return _failure("triangle_must_have_three_valid_indices", counts, triangle=face_index)
        if len(set(face)) != 3:
            return _failure("triangle_repeats_vertex_index", counts, triangle=face_index)
        tri = tuple(face)
        projected = tuple(points[index] for index in tri)
        parsed_triangles.append(tri)
        for left, right in zip(tri, (tri[1], tri[2], tri[0])):
            face_edges[(left, right) if left < right else (right, left)].add(face_index)
        area = abs(_cross2(projected[1][0] - projected[0][0], projected[1][1] - projected[0][1],
                           projected[2][0] - projected[0][0], projected[2][1] - projected[0][1]))
        if area <= _AREA_TOLERANCE:
            continue
        projected_triangles.append(projected)
        projected_source_faces.append(face_index)
        triangle_bounds.append((min(point[0] for point in projected), min(point[1] for point in projected),
                                max(point[0] for point in projected), max(point[1] for point in projected)))

    parsed_edges: list[tuple[int, int, set[int]]] = []
    for edge_index, edge in enumerate(edges):
        if not isinstance(edge, Mapping):
            return _failure("edge_must_be_an_object", counts, edge=edge_index)
        pair = edge.get("vertices")
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or any(
                type(value) is not int or not 0 <= value < len(points) for value in pair) or pair[0] == pair[1]:
            return _failure("edge_must_have_two_distinct_valid_vertex_indices", counts, edge=edge_index)
        source = set(face_edges.get((min(pair), max(pair)), set()))
        supplied = edge.get("incident_faces", ())
        if not isinstance(supplied, (list, tuple)) or any(
                type(value) is not int or not 0 <= value < len(triangles) for value in supplied):
            return _failure("incident_faces_must_be_valid_indices", counts, edge=edge_index)
        source.update(supplied)
        parsed_edges.append((pair[0], pair[1], source))

    root = _build_bvh(list(range(len(projected_triangles))), triangle_bounds) if projected_triangles else None
    result_edges: list[dict[str, Any]] = []
    output_interval_count = 0
    try:
        for edge_index, (left, right, incident_faces) in enumerate(parsed_edges):
            p0, p1 = points[left], points[right]
            edge_bbox = (min(p0[0], p1[0]), min(p0[1], p1[1]),
                         max(p0[0], p1[0]), max(p0[1], p1[1]))
            hidden: list[tuple[float, float]] = []
            candidates: list[int] = []
            if root is not None:
                stack = [root]
                while stack:
                    node = stack.pop()
                    counts["bvh_node_visits"] += 1
                    if counts["bvh_node_visits"] > max_node_visits:
                        raise _BudgetExceeded
                    if not _overlaps(edge_bbox, node.bounds):
                        continue
                    if node.indices:
                        candidates.extend(node.indices)
                    else:
                        # Push right first so left nodes are visited deterministically.
                        if node.right is not None:
                            stack.append(node.right)
                        if node.left is not None:
                            stack.append(node.left)
            for triangle_index in sorted(candidates):
                source_face = projected_source_faces[triangle_index]
                if source_face in incident_faces:
                    continue
                counts["edge_triangle_tests"] += 1
                if counts["edge_triangle_tests"] > max_triangle_tests:
                    raise _BudgetExceeded
                tri = projected_triangles[triangle_index]
                clipped = _clip_segment_to_triangle((p0[0], p0[1]), (p1[0], p1[1]), tri)
                if clipped is None:
                    continue
                source_vertices = parsed_triangles[source_face]
                occlusion = _closer_interval((p0, p1), tri, clipped, depth_epsilon,
                                             (left in source_vertices, right in source_vertices))
                if occlusion is not None:
                    hidden.append(occlusion)
            merged_hidden = _merge_intervals(hidden)
            visible: list[tuple[float, float]] = []
            cursor = 0.0
            for low, high in merged_hidden:
                if low > cursor + _PARAMETER_TOLERANCE:
                    visible.append((cursor, low))
                cursor = max(cursor, high)
            if cursor < 1.0 - _PARAMETER_TOLERANCE:
                visible.append((cursor, 1.0))
            output_interval_count += len(visible) + len(merged_hidden)
            if output_interval_count > max_output_intervals:
                return _failure("visibility_output_interval_limit_exceeded", counts, complete=False,
                                max_output_intervals=max_output_intervals)
            result_edges.append({
                "edge_index": edge_index,
                "vertices": [left, right],
                "visible_intervals": [[low, high] for low, high in visible],
                "hidden_intervals": [[low, high] for low, high in merged_hidden],
            })
    except _BudgetExceeded:
        return _failure("visibility_work_budget_exceeded", counts, complete=False,
                        max_node_visits=max_node_visits, max_triangle_tests=max_triangle_tests)

    return {
        "success": True, "available": True, "complete": True,
        "coordinate_unit": "caller_supplied; not inferred",
        "depth_convention": "larger depth is closer to viewer",
        "depth_tolerance_relative_to_larger_mesh_extent": _DEPTH_TOLERANCE,
        "edges": result_edges,
        "counts": counts,
        "limits": {"max_node_visits": max_node_visits, "max_triangle_tests": max_triangle_tests,
                   "max_output_intervals": max_output_intervals},
        "output_interval_count": output_interval_count,
        "scope": "Orthographic occlusion intervals against projected triangle interiors. Input geometry is not interpreted as E3D CSG or native tessellation.",
    }


def _failure(code: str, counts: dict[str, int], *, complete: bool = True, **details: Any) -> dict[str, Any]:
    return {"success": False, "available": False, "complete": complete,
            "error_code": code, "edges": [], "counts": counts, **details}
