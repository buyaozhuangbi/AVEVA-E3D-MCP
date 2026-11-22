"""Bounded orthographic edge drawings from closed indexed triangle meshes.

This produces parameter-derived linework only. Optional hidden-line removal is
also projected-mesh analysis; it does not evaluate native E3D tessellation,
Boolean geometry, or engineering annotations.
"""

from __future__ import annotations

import html
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .triangle_mesh_validation import MAX_TRIANGLES, MAX_VERTICES, validate_triangle_mesh
from .triangle_mesh_visibility import projected_edge_visibility
from .section_hatch_precision import validate_section_hatch_precision


MAX_DRAWING_SEGMENTS = 12000
MAX_TOTAL_DRAWING_SEGMENTS = 20000
MAX_SVG_BYTES = 4_000_000
_VIEWS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]] = {
    # (screen right, screen up, toward viewer); FRONT convention looks from +Y.
    "FRONT": ((1, 0, 0), (0, 0, 1), (0, 1, 0)),
    "TOP": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "SIDE": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
}
_RIGHT_HANDED_VIEWS = {**_VIEWS, "FRONT": ((1, 0, 0), (0, 0, 1), (0, -1, 0))}


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return math.fsum(a[index] * b[index] for index in range(3))


def _cross(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _normal(points: Sequence[Sequence[float]], face: Sequence[int]) -> tuple[float, float, float] | None:
    a, b, c = (points[index] for index in face)
    u = tuple(b[axis] - a[axis] for axis in range(3))
    v = tuple(c[axis] - a[axis] for axis in range(3))
    su, sv = max(map(abs, u)), max(map(abs, v))
    if not su or not sv:
        return None
    cross = _cross(tuple(value / su for value in u), tuple(value / sv for value in v))
    magnitude = math.hypot(*cross)
    if not math.isfinite(magnitude) or magnitude == 0:
        return None
    return tuple(value / magnitude for value in cross)


def _edge_adjacency(triangles: Sequence[Sequence[int]]) -> dict[tuple[int, int], list[int]]:
    edges: dict[tuple[int, int], list[int]] = defaultdict(list)
    for face_index, face in enumerate(triangles):
        for left, right in zip(face, (*face[1:], face[0])):
            edges[(left, right) if left < right else (right, left)].append(face_index)
    return edges


def _fmt(value: float) -> str:
    # Stable decimal formatting, including normalization of negative zero.
    if value == 0.0:
        value = 0.0
    return format(value, ".6f")


def _project(points: Sequence[Sequence[float]], axis: Sequence[int]) -> list[float]:
    return [math.fsum(points[index][component] * axis[component] for component in range(3))
            for index in range(len(points))]


_ProjectedSegment = tuple[tuple[float, float], tuple[float, float]]


class _CurveEndpointIndex:
    """Join one curve's projected endpoints using local float precision."""

    def __init__(self, points: Sequence[Sequence[float]]):
        self._origin = tuple(min(point[axis] for point in points) for axis in range(2))
        # A view can contain both distant world coordinates and small local
        # details.  The largest ULP only sizes lookup cells; acceptance is
        # derived from the two endpoints being compared.
        self._cell_size = tuple(max(1e-9, 8.0 * max(
            (math.ulp(abs(point[axis])) for point in points if point[axis] != 0.0),
            default=math.ulp(1.0))) for axis in range(2))
        self._points: list[tuple[float, float]] = []
        self._buckets: dict[tuple[int, int], list[int]] = {}

    def _cell(self, point: Sequence[float]) -> tuple[int, int]:
        return tuple(math.floor((point[axis] - self._origin[axis]) / self._cell_size[axis])
                     for axis in range(2))  # type: ignore[return-value]

    @property
    def points(self) -> tuple[tuple[float, float], ...]:
        return tuple(self._points)

    @staticmethod
    def _tolerance(point: Sequence[float]) -> tuple[float, float]:
        return tuple(max(1e-9, 8.0 * math.ulp(abs(value)))
                     for value in point)  # type: ignore[return-value]

    def add(self, raw: Sequence[float]) -> int:
        point = (float(raw[0]), float(raw[1]))
        cell = self._cell(point)
        point_tolerance = self._tolerance(point)
        candidates: list[tuple[float, int]] = []
        for x_cell in range(cell[0] - 1, cell[0] + 2):
            for y_cell in range(cell[1] - 1, cell[1] + 2):
                for index in self._buckets.get((x_cell, y_cell), ()):
                    prior = self._points[index]
                    prior_tolerance = self._tolerance(prior)
                    tolerance = (max(point_tolerance[0], prior_tolerance[0]),
                                 max(point_tolerance[1], prior_tolerance[1]))
                    distance = math.hypot((point[0] - prior[0]) / tolerance[0],
                                          (point[1] - prior[1]) / tolerance[1])
                    if distance <= 1.0:
                        candidates.append((distance, index))
        if candidates:
            candidates.sort()
            ambiguity = 8.0 * math.ulp(max(1.0, candidates[0][0],
                                             candidates[1][0] if len(candidates) > 1 else 0.0))
            if len(candidates) == 1 or candidates[1][0] - candidates[0][0] > ambiguity:
                return candidates[0][1]
        index = len(self._points)
        self._points.append(point)
        self._buckets.setdefault(cell, []).append(index)
        return index


def _arc_extrema_points(curve: Mapping[str, Any]) -> list[tuple[float, float]]:
    """Return the endpoints and screen extrema of a validated CCW arc."""
    cx, cy = curve["center"]
    radius = curve["radius"]
    start, end = curve["start_angle_degrees"], curve["end_angle_degrees"]
    result = [(cx + radius * math.cos(math.radians(angle)),
               cy + radius * math.sin(math.radians(angle))) for angle in (start, end)]
    for angle, x, y in ((0.0, 1, 0), (90.0, 0, 1), (180.0, -1, 0), (270.0, 0, -1)):
        if angle + 360.0 * math.ceil((start - angle) / 360.0) <= end:
            result.append((cx + radius * x, cy + radius * y))
    return result


def _validated_curve_primitives(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate fitted curves against their visible source-line chains or cycles."""
    curves = view.get("curve_primitives", ())
    if curves is None:
        return []
    if not isinstance(curves, (list, tuple)):
        raise ValueError("invalid_curve_primitives")
    lines = view.get("lines")
    if not isinstance(lines, (list, tuple)):
        raise ValueError("invalid_curve_source_lines")
    used_indices: set[int] = set()
    validated: list[dict[str, Any]] = []
    for curve in curves:
        if not isinstance(curve, Mapping) or curve.get("type") not in {"circle", "ellipse", "arc"}:
            raise ValueError("invalid_curve_primitive")
        curve_type = curve["type"]
        if curve_type == "arc":
            center = curve.get("center")
            radius = _finite_number(curve.get("radius"))
            start_angle = _finite_number(curve.get("start_angle_degrees"))
            end_angle = _finite_number(curve.get("end_angle_degrees"))
            analytic_source = curve.get("analytic_source")
            indices = curve.get("source_line_indices")
            kind = curve.get("kind")
            if (not isinstance(center, (list, tuple)) or len(center) != 2
                    or radius is None or radius <= 0.0
                    or start_angle is None or end_angle is None
                    or not 0.0 < end_angle - start_angle < 360.0
                    or kind != "silhouette" or curve.get("visibility") != "visible"
                    or not isinstance(analytic_source, Mapping)
                    or analytic_source.get("shape") != "DISH"
                    or analytic_source.get("source_kind") != "spherical_silhouette_arc"
                    or analytic_source.get("refno") is None
                    or not isinstance(indices, (list, tuple)) or len(indices) < 8
                    or any(type(index) is not int or not 0 <= index < len(lines) for index in indices)
                    or len(set(indices)) != len(indices) or used_indices.intersection(indices)):
                raise ValueError("invalid_curve_primitive")
            cx, cy = _finite_number(center[0]), _finite_number(center[1])
            source_center = analytic_source.get("center")
            source_radius = _finite_number(analytic_source.get("source_radius_mm"))
            sphere_station = _finite_number(analytic_source.get("sphere_center_station_mm"))
            axial_domain = analytic_source.get("axial_domain_mm")
            axis_vertical_sign = _finite_number(analytic_source.get("axis_vertical_sign"))
            projected_axis = analytic_source.get("projected_axis_unit")
            if (cx is None or cy is None or not isinstance(source_center, (list, tuple))
                    or len(source_center) != 2 or source_radius is None or source_radius <= 0.0
                    or sphere_station is None or not isinstance(axial_domain, (list, tuple))
                    or len(axial_domain) != 2):
                raise ValueError("invalid_curve_source_section")
            if "projected_axis_unit" in analytic_source:
                if ("axis_vertical_sign" in analytic_source or not isinstance(projected_axis, (list, tuple))
                        or len(projected_axis) != 2):
                    raise ValueError("invalid_curve_source_section")
                axis_x, axis_y = (_finite_number(value) for value in projected_axis)
                if axis_x is None or axis_y is None:
                    raise ValueError("invalid_curve_source_section")
                axis_length = math.hypot(axis_x, axis_y)
                if abs(axis_length - 1.0) > 1e-10:
                    raise ValueError("invalid_curve_source_section")
                axis_x, axis_y = axis_x / axis_length, axis_y / axis_length
            else:
                if axis_vertical_sign not in {-1.0, 1.0}:
                    raise ValueError("invalid_curve_source_section")
                axis_x, axis_y = 0.0, axis_vertical_sign
            sx, sy = _finite_number(source_center[0]), _finite_number(source_center[1])
            axial_min, axial_max = (_finite_number(value) for value in axial_domain)
            if sx is None or sy is None:
                raise ValueError("invalid_curve_source_section")
            if (axial_min is None or axial_max is None or axial_min < 0.0
                    or axial_max <= axial_min):
                raise ValueError("invalid_curve_source_section")
            coordinate_ulps = [math.ulp(abs(value)) for value in (cx, cy, sx, sy, radius, source_radius)
                               if value != 0.0]
            coordinate_tolerance = max(1e-12, 32.0 * max(coordinate_ulps, default=math.ulp(1.0)))
            maximum_span = (2.0 * math.acos(-sphere_station / source_radius)
                            if abs(sphere_station) < source_radius else None)
            if (coordinate_tolerance > radius * 2e-5
                    or abs(cx - sx) > coordinate_tolerance
                    or abs(cy - sy) > coordinate_tolerance
                    or abs(radius - source_radius) > coordinate_tolerance
                    or maximum_span is None
                    or math.radians(end_angle - start_angle)
                       > maximum_span + max(0.02 * maximum_span, 1e-3)):
                raise ValueError("invalid_curve_source_section")

            raw_points: list[tuple[float, float]] = []
            for source_index in indices:
                line = lines[source_index]
                if not isinstance(line, Mapping):
                    raise ValueError("invalid_curve_source_line")
                for raw in (line.get("start"), line.get("end")):
                    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
                        raise ValueError("invalid_curve_source_line")
                    x, y = _finite_number(raw[0]), _finite_number(raw[1])
                    if x is None or y is None:
                        raise ValueError("invalid_curve_source_line")
                    raw_points.append((x, y))
            endpoint_index = _CurveEndpointIndex(raw_points)
            edge_vertices: list[tuple[int, int]] = []
            adjacency: dict[int, list[int]] = defaultdict(list)
            for local_index, source_index in enumerate(indices):
                line = lines[source_index]
                if (not isinstance(line, Mapping) or line.get("kind") != "silhouette"
                        or line.get("visibility", "visible") != "visible"):
                    raise ValueError("invalid_curve_source_line")
                first = endpoint_index.add(line["start"])
                second = endpoint_index.add(line["end"])
                if first == second:
                    raise ValueError("invalid_curve_source_line")
                edge_vertices.append((first, second))
                adjacency.setdefault(first, []).append(local_index)
                adjacency.setdefault(second, []).append(local_index)
            points = endpoint_index.points
            endpoints = [vertex for vertex, edges in adjacency.items() if len(edges) == 1]
            if (len(points) != len(indices) + 1 or len(endpoints) != 2
                    or any(len(edges) not in {1, 2} for edges in adjacency.values())):
                raise ValueError("invalid_curve_source_cycle")
            reached = {endpoints[0]}
            stack = list(reached)
            while stack:
                vertex = stack.pop()
                for edge_index in adjacency[vertex]:
                    first, second = edge_vertices[edge_index]
                    neighbor = second if first == vertex else first
                    if neighbor not in reached:
                        reached.add(neighbor)
                        stack.append(neighbor)
            if len(reached) != len(points):
                raise ValueError("invalid_curve_source_cycle")

            start_radians = math.radians(start_angle)
            end_radians = math.radians(end_angle)
            arc_span = math.radians(end_angle - start_angle)
            # The input mesh can round a shared endpoint, but its documented
            # angle must not extend or shorten the observed silhouette chain.
            endpoint_tolerance = max(1e-12, min(2e-5, max(coordinate_tolerance, 1e-9) / radius))
            start_vertex = min(endpoints, key=lambda vertex: abs(math.remainder(
                math.atan2(points[vertex][1] - cy, points[vertex][0] - cx)
                - start_radians, math.tau)))
            ordered_vertices = [start_vertex]
            previous_edge: int | None = None
            current = start_vertex
            while len(ordered_vertices) < len(indices) + 1:
                next_edge = next((edge for edge in adjacency[current] if edge != previous_edge), None)
                if next_edge is None:
                    break
                first, second = edge_vertices[next_edge]
                following = second if first == current else first
                ordered_vertices.append(following)
                previous_edge, current = next_edge, following
            if len(ordered_vertices) != len(indices) + 1 or current not in endpoints:
                raise ValueError("invalid_curve_source_cycle")
            angles = [math.atan2(points[vertex][1] - cy, points[vertex][0] - cx)
                      for vertex in ordered_vertices]
            steps = [((angles[index + 1] - angles[index]) % (2.0 * math.pi))
                     for index in range(len(angles) - 1)]
            if any(step <= 1e-12 or step >= math.pi for step in steps):
                raise ValueError("invalid_curve_fit")
            span = math.fsum(steps)
            start_error = abs(math.remainder(angles[0] - start_radians, math.tau))
            end_error = abs(math.remainder(angles[-1] - end_radians, math.tau))
            residual = max(abs(math.dist(points[vertex], (cx, cy)) - radius)
                           for vertex in ordered_vertices)
            axial_stations = [sphere_station + axis_x * (points[vertex][0] - cx)
                              + axis_y * (points[vertex][1] - cy)
                              for vertex in ordered_vertices]
            arc_angles = [start_radians, end_radians]
            axis_angle = math.atan2(axis_y, axis_x)
            for extremum in (axis_angle, axis_angle + math.pi):
                candidate = start_radians + (extremum - start_radians) % math.tau
                if candidate <= end_radians:
                    arc_angles.append(candidate)
            axial_stations.extend(sphere_station + radius * (axis_x * math.cos(angle)
                                                            + axis_y * math.sin(angle))
                                  for angle in arc_angles)
            axial_tolerance = max(coordinate_tolerance, radius * 2e-5)
            perimeter = math.fsum(math.dist(points[first], points[second])
                                  for first, second in edge_vertices)
            if (start_error > endpoint_tolerance or end_error > endpoint_tolerance
                    or abs(span - arc_span) > 2.0 * endpoint_tolerance
                    or residual > max(coordinate_tolerance, radius * 2e-5)
                    or min(axial_stations) < axial_min - axial_tolerance
                    or max(axial_stations) > axial_max + axial_tolerance
                    or abs(perimeter / (radius * arc_span) - 1.0) > 2e-2):
                raise ValueError("invalid_curve_fit")
            used_indices.update(indices)
            validated.append({"type": "arc", "center": (cx, cy), "radius": radius,
                              "start_angle_degrees": start_angle, "end_angle_degrees": end_angle,
                              "kind": kind, "source_line_indices": tuple(indices)})
            continue
        center = curve.get("center")
        radius = _finite_number(curve.get("radius")) if curve_type == "circle" else None
        major = _finite_number(curve.get("major_radius")) if curve_type == "ellipse" else radius
        minor = _finite_number(curve.get("minor_radius")) if curve_type == "ellipse" else radius
        major_axis = curve.get("major_axis") if curve_type == "ellipse" else None
        analytic_source = curve.get("analytic_source")
        indices = curve.get("source_line_indices")
        kind = curve.get("kind")
        if (not isinstance(center, (list, tuple)) or len(center) != 2
                or major is None or minor is None or minor <= 0 or major < minor
                or curve_type == "circle" and (radius is None or radius <= 0)
                or curve_type == "ellipse" and (not isinstance(major_axis, (list, tuple))
                                                or len(major_axis) != 2)
                or kind not in {"silhouette", "feature"} or curve.get("visibility") != "visible"
                or not isinstance(analytic_source, Mapping)
                or analytic_source.get("shape") not in {"CYLI", "CONE", "DISH"}
                or analytic_source.get("refno") is None
                or not isinstance(indices, (list, tuple)) or len(indices) < 8
                or any(type(index) is not int or not 0 <= index < len(lines) for index in indices)
                or len(set(indices)) != len(indices) or used_indices.intersection(indices)):
            raise ValueError("invalid_curve_primitive")
        cx, cy = _finite_number(center[0]), _finite_number(center[1])
        source_center = analytic_source.get("center")
        source_radius = _finite_number(analytic_source.get("source_radius_mm"))
        station = _finite_number(analytic_source.get("axial_station_mm"))
        station_candidates = analytic_source.get("axial_station_candidates_mm")
        if station is None:
            # A view looking down a cylinder axis can map both end stations to
            # the same projected circle.  The producer records that ambiguity
            # explicitly; the verified center/radius still identify one
            # analytic section and are sufficient for presentation validation.
            if (not isinstance(station_candidates, (list, tuple)) or not station_candidates
                    or any(_finite_number(value) is None for value in station_candidates)):
                raise ValueError("invalid_curve_center")
        if (cx is None or cy is None or not isinstance(source_center, (list, tuple))
                or len(source_center) != 2 or source_radius is None or source_radius <= 0):
            raise ValueError("invalid_curve_center")
        sx, sy = _finite_number(source_center[0]), _finite_number(source_center[1])
        if sx is None or sy is None:
            raise ValueError("invalid_curve_source_section")
        coordinate_ulps = [math.ulp(abs(value)) for value in (cx, cy, sx, sy, major, minor, source_radius)
                           if value != 0.0]
        coordinate_tolerance = max(1e-12, 32.0 * max(coordinate_ulps, default=math.ulp(1.0)))
        if coordinate_tolerance > major * 2e-5 or abs(cx - sx) > coordinate_tolerance or abs(cy - sy) > coordinate_tolerance:
            raise ValueError("invalid_curve_source_section")
        if curve_type == "circle":
            if abs(radius - source_radius) > coordinate_tolerance:
                raise ValueError("invalid_curve_source_section")
            major_unit = (1.0, 0.0)
        else:
            ax, ay = _finite_number(major_axis[0]), _finite_number(major_axis[1])
            source_major = _finite_number(analytic_source.get("major_radius_mm"))
            source_minor = _finite_number(analytic_source.get("minor_radius_mm"))
            source_axis = analytic_source.get("major_axis")
            if (ax is None or ay is None or source_major is None or source_minor is None
                    or not isinstance(source_axis, (list, tuple)) or len(source_axis) != 2):
                raise ValueError("invalid_curve_source_section")
            sx_axis, sy_axis = _finite_number(source_axis[0]), _finite_number(source_axis[1])
            if (sx_axis is None or sy_axis is None or source_major < source_minor or source_minor <= 0
                    or abs(major - source_major) > coordinate_tolerance
                    or abs(minor - source_minor) > coordinate_tolerance
                    or min(math.dist((ax, ay), (sx_axis, sy_axis)),
                           math.dist((ax, ay), (-sx_axis, -sy_axis))) > coordinate_tolerance
            or abs(math.hypot(ax, ay) - major) > coordinate_tolerance
                    or abs(source_radius - source_major) > coordinate_tolerance):
                raise ValueError("invalid_curve_source_section")
            major_unit = (ax / major, ay / major)
        perpendicular = (-major_unit[1], major_unit[0])
        raw_points: list[tuple[float, float]] = []
        for source_index in indices:
            line = lines[source_index]
            for raw in (line.get("start"), line.get("end")) if isinstance(line, Mapping) else ():
                if not isinstance(raw, (list, tuple)) or len(raw) != 2:
                    raise ValueError("invalid_curve_source_line")
                x, y = _finite_number(raw[0]), _finite_number(raw[1])
                if x is None or y is None:
                    raise ValueError("invalid_curve_source_line")
                raw_points.append((x, y))
        endpoint_index = _CurveEndpointIndex(raw_points)
        points: list[tuple[float, float]] = []
        edge_vertices: list[tuple[int, int]] = []
        adjacency: dict[int, list[int]] = defaultdict(list)
        source_kinds: set[str] = set()

        def endpoint_id(raw: Any) -> int:
            if not isinstance(raw, (list, tuple)) or len(raw) != 2:
                raise ValueError("invalid_curve_source_line")
            x, y = _finite_number(raw[0]), _finite_number(raw[1])
            if x is None or y is None:
                raise ValueError("invalid_curve_source_line")
            point = (x, y)
            point_id = endpoint_index.add(point)
            while len(points) <= point_id:
                points.append(endpoint_index.points[len(points)])
            return point_id

        for local_index, source_index in enumerate(indices):
            line = lines[source_index]
            if (not isinstance(line, Mapping) or line.get("kind") not in {"silhouette", "feature"}
                    or line.get("visibility", "visible") != "visible"):
                raise ValueError("invalid_curve_source_line")
            source_kinds.add(line["kind"])
            first, second = endpoint_id(line.get("start")), endpoint_id(line.get("end"))
            if first == second:
                raise ValueError("invalid_curve_source_line")
            edge_vertices.append((first, second))
            adjacency.setdefault(first, []).append(local_index)
            adjacency.setdefault(second, []).append(local_index)
        expected_kind = "silhouette" if "silhouette" in source_kinds else "feature"
        if (kind != expected_kind or len(points) != len(indices)
                or any(len(edges) != 2 for edges in adjacency.values())):
            raise ValueError("invalid_curve_source_cycle")
        reached = {next(iter(adjacency))}
        stack = list(reached)
        while stack:
            vertex = stack.pop()
            for edge_index in adjacency[vertex]:
                first, second = edge_vertices[edge_index]
                neighbor = second if first == vertex else first
                if neighbor not in reached:
                    reached.add(neighbor)
                    stack.append(neighbor)
        if len(reached) != len(points):
            raise ValueError("invalid_curve_source_cycle")
        normalized_radii = []
        for x, y in points:
            dx, dy = x - cx, y - cy
            u = (dx * major_unit[0] + dy * major_unit[1]) / major
            v = (dx * perpendicular[0] + dy * perpendicular[1]) / minor
            normalized_radii.append(math.hypot(u, v))
        residual = max(abs(value - 1.0) for value in normalized_radii) * major
        perimeter = math.fsum(math.dist(points[first], points[second]) for first, second in edge_vertices)
        fit_tolerance = max(coordinate_tolerance, major * 2e-5)
        ellipse_perimeter = math.pi * (3.0 * (major + minor)
                                       - math.sqrt((3.0 * major + minor) * (major + 3.0 * minor)))
        if residual > fit_tolerance or abs(perimeter / ellipse_perimeter - 1.0) > 2e-2:
            raise ValueError("invalid_curve_fit")
        used_indices.update(indices)
        normalized_curve = {"type": curve_type, "center": (cx, cy),
                            "kind": kind, "source_line_indices": tuple(indices)}
        if curve_type == "circle":
            normalized_curve["radius"] = radius
        else:
            normalized_curve.update(major_axis=(ax, ay), major_radius=major, minor_radius=minor)
        validated.append(normalized_curve)
    return validated


def _classified_segments(raw_segments: Sequence[dict[str, Any]], classified_edges: Sequence[dict[str, Any]],
                         visibility: str, *, excluded: set[_ProjectedSegment] | None = None
                         ) -> dict[_ProjectedSegment, dict[str, Any]]:
    result: dict[_ProjectedSegment, dict[str, Any]] = {}
    for segment, classified in zip(raw_segments, classified_edges):
        left, right = segment["start"], segment["end"]
        for low, high in classified[f"{visibility}_intervals"]:
            if high <= low:
                continue
            start = [left[axis] + (right[axis] - left[axis]) * low for axis in range(2)]
            end = [left[axis] + (right[axis] - left[axis]) * high for axis in range(2)]
            first, second = tuple(start), tuple(end)
            key = (first, second) if first <= second else (second, first)
            if first == second or (excluded is not None and key in excluded):
                continue
            if key in result:
                if segment["kind"] == "silhouette":
                    result[key]["kind"] = "silhouette"
                continue
            result[key] = {"edge": segment["edge"], "kind": segment["kind"], "start": start,
                           "end": end, "visibility": visibility, "parameter_interval": [low, high]}
    return result


def _make_svg(views: Sequence[dict[str, Any]], *, hidden_line_removal: bool = False,
              show_hidden_lines: bool = False, footer: str | None = None) -> str:
    if footer is not None and not isinstance(footer, str):
        raise ValueError("invalid_svg_footer")
    width, height, gap, margin, header = 1200, 440, 24, 20, 30
    column_count = len(views)
    panel_width = (width - 2 * margin - max(0, column_count - 1) * gap) / column_count
    panel_height = height - 2 * margin
    available_w, available_h = panel_width - 2 * margin, panel_height - header - margin
    scale_limits = []
    for view in views:
        bounds = view["bounds"]
        dx = bounds["max"][0] - bounds["min"][0]
        dy = bounds["max"][1] - bounds["min"][1]
        scale_limits.append(min(available_w / dx if dx else math.inf,
                                available_h / dy if dy else math.inf))
    # Keep all requested orthographic views at the same displayed scale.
    scale = min(scale_limits, default=1.0)
    if not math.isfinite(scale) or scale <= 0:
        scale = 1.0
    drawings: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<g fill="none" stroke="#17212b" stroke-width="1.25" stroke-linecap="round">',
    ]
    for view_index, view in enumerate(views):
        left = margin + view_index * (panel_width + gap)
        bounds = view["bounds"]
        xmin, xmax = bounds["min"][0], bounds["max"][0]
        ymin, ymax = bounds["min"][1], bounds["max"][1]
        dx, dy = xmax - xmin, ymax - ymin
        view["display_scale_px_per_mm"] = scale
        offset_x = left + (panel_width - dx * scale) / 2.0
        offset_y = margin + header + (available_h - dy * scale) / 2.0
        validate_section_hatch_precision(view["lines"], transform=lambda point: (
            offset_x + (point[0] - xmin) * scale,
            offset_y + (ymax - point[1]) * scale), transform_orientation=-1)
        view_dimensions = f"{_fmt(dx)} × {_fmt(dy)} {view['coordinate_unit']}"
        view["dimensions"] = {"width": dx, "height": dy, "unit": view["coordinate_unit"]}
        drawings.append(f'<text x="{_fmt(left + 4)}" y="{_fmt(margin + 16)}" fill="#17212b" stroke="none" font-family="sans-serif" font-size="14">{html.escape(view["name"])} — {html.escape(view_dimensions)}</text>')
        drawings.append(f'<g data-view="{view["name"]}">')
        fitted_curves = _validated_curve_primitives(view)
        fitted_source_indices = {source_index for curve in fitted_curves
                                 for source_index in curve["source_line_indices"]}
        for source_index, segment in sorted(enumerate(view["lines"]),
                                            key=lambda item: item[1]["kind"] != "hatch"):
            if source_index in fitted_source_indices:
                continue
            a, b = segment["start"], segment["end"]
            x1, y1 = offset_x + (a[0] - xmin) * scale, offset_y + (ymax - a[1]) * scale
            x2, y2 = offset_x + (b[0] - xmin) * scale, offset_y + (ymax - b[1]) * scale
            if segment["kind"] == "hatch":
                if view["name"] != "SECTION":
                    raise ValueError("invalid_line_kind")
                if segment.get("visibility") == "hidden":
                    raise ValueError("hidden_hatch_unsupported")
                klass = "hatch"
                style = ' stroke="#b0b0b0" stroke-width="0.6"'
            elif segment.get("visibility") == "hidden":
                klass = "hidden"
                style = ' stroke="#718096" stroke-dasharray="6 4"'
            else:
                klass = "silhouette" if segment["kind"] == "silhouette" else "feature"
                style = ""
            drawings.append(f'<path class="{klass}"{style} d="M {_fmt(x1)} {_fmt(y1)} L {_fmt(x2)} {_fmt(y2)}"/>')
            axis_name = segment.get("axis_name")
            if isinstance(axis_name, str) and axis_name:
                drawings.append(
                    f'<text x="{_fmt(x2 + 3)}" y="{_fmt(y2 - 3)}" fill="#17212b" stroke="none" '
                    f'font-family="sans-serif" font-size="9">{html.escape(axis_name)}</text>')
        # CSG adapters may provide a verified presentation-only conic fit for
        # a complete projected edge ring. The fitted source chords are omitted
        # from this SVG rendering pass to avoid a doubled polygonal outline;
        # the original linework remains in the structured drawing for
        # provenance and segment-count compatibility. Bounds and dimensions still come
        # exclusively from the projected mesh (or its conservative source
        # envelope), so fitting cannot change scale.
        for curve in fitted_curves:
            cx, cy = curve["center"]
            sx = offset_x + (cx - xmin) * scale
            sy = offset_y + (ymax - cy) * scale
            if curve["type"] == "arc":
                radius = curve["radius"] * scale
                start = math.radians(curve["start_angle_degrees"])
                end = math.radians(curve["end_angle_degrees"])
                start_x = offset_x + (cx + curve["radius"] * math.cos(start) - xmin) * scale
                start_y = offset_y + (ymax - (cy + curve["radius"] * math.sin(start))) * scale
                end_x = offset_x + (cx + curve["radius"] * math.cos(end) - xmin) * scale
                end_y = offset_y + (ymax - (cy + curve["radius"] * math.sin(end))) * scale
                large_arc = 1 if end - start > math.pi else 0
                drawings.append(
                    f'<path class="curve-fit" data-curve="arc" '
                    f'd="M {_fmt(start_x)} {_fmt(start_y)} A {_fmt(radius)} {_fmt(radius)} 0 '
                    f'{large_arc} 0 {_fmt(end_x)} {_fmt(end_y)}"/>')
            elif curve["type"] == "circle":
                drawings.append(
                    f'<circle class="curve-fit" data-curve="circle" '
                    f'cx="{_fmt(sx)}" cy="{_fmt(sy)}" r="{_fmt(curve["radius"] * scale)}" '
                    f'fill="none" stroke="#17212b" stroke-width="1.35"/>')
            else:
                axis = curve["major_axis"]
                angle = math.degrees(math.atan2(-axis[1], axis[0]))
                drawings.append(
                    f'<ellipse class="curve-fit" data-curve="ellipse" '
                    f'cx="{_fmt(sx)}" cy="{_fmt(sy)}" '
                    f'rx="{_fmt(curve["major_radius"] * scale)}" '
                    f'ry="{_fmt(curve["minor_radius"] * scale)}" '
                    f'transform="rotate({_fmt(angle)} {_fmt(sx)} {_fmt(sy)})" '
                    f'fill="none" stroke="#17212b" stroke-width="1.35"/>')
        drawings.append('</g>')
    if footer is None:
        if show_hidden_lines:
            footer = ("Parameter-derived approximate mesh linework; occluded portions are dashed; "
                      "cuts and hole semantics are not resolved.")
        elif hidden_line_removal:
            footer = ("Parameter-derived approximate mesh linework; bounded projected-depth hidden-line removal was applied; "
                      "cuts and hole semantics are not resolved.")
        else:
            footer = "Parameter-derived approximate mesh linework; hidden lines, cuts and holes are not resolved."
    drawings.extend(['</g>', f'<text x="20" y="430" fill="#59636e" font-family="sans-serif" font-size="10">{html.escape(footer)}</text>', '</svg>'])
    result = "\n".join(drawings)
    if len(result.encode("utf-8")) > MAX_SVG_BYTES:
        raise ValueError("drawing_output_limit_exceeded")
    return result


def generate_orthographic_drawing(
    vertices: Any,
    triangles: Any,
    *,
    views: Sequence[str] = ("FRONT", "TOP", "SIDE"),
    feature_angle_degrees: float = 30.0,
    coordinate_frame: str = "caller-supplied frame",
    coordinate_unit: str = "mm",
    hidden_line_removal: bool = True,
    show_hidden_lines: bool = False,
    projection_convention: str = "legacy",
    max_node_visits: int = 2_000_000,
    max_triangle_tests: int = 2_000_000,
    max_output_intervals: int = 100_000,
) -> dict[str, Any]:
    """Project a closed mesh after the existing strict outward validation."""
    return _generate_orthographic_drawing_from_validated_mesh(
        vertices, triangles, views=views, feature_angle_degrees=feature_angle_degrees,
        coordinate_frame=coordinate_frame, coordinate_unit=coordinate_unit,
        hidden_line_removal=hidden_line_removal, show_hidden_lines=show_hidden_lines,
        projection_convention=projection_convention, max_node_visits=max_node_visits,
        max_triangle_tests=max_triangle_tests, max_output_intervals=max_output_intervals,
    )


def _generate_orthographic_drawing_from_validated_mesh(
    vertices: Any,
    triangles: Any,
    *,
    views: Sequence[str] = ("FRONT", "TOP", "SIDE"),
    feature_angle_degrees: float = 30.0,
    coordinate_frame: str = "caller-supplied frame",
    coordinate_unit: str = "mm",
    hidden_line_removal: bool = True,
    show_hidden_lines: bool = False,
    projection_convention: str = "legacy",
    max_node_visits: int = 2_000_000,
    max_triangle_tests: int = 2_000_000,
    max_output_intervals: int = 100_000,
    _material_boundary_prevalidated: bool = False,
    _projection_bounds: Mapping[str, Mapping[str, Sequence[float]]] | None = None,
) -> dict[str, Any]:
    """Generate three-view silhouette/feature linework from a closed mesh.

    Silhouettes are classified from adjacent face orientation relative to the
    view direction. Feature edges exceed the requested dihedral threshold.
    When ``hidden_line_removal`` is true, bounded projected-depth interval
    analysis identifies portions hidden by nearer projected mesh triangles.
    They are omitted by default, or returned for dashed drawing when
    ``show_hidden_lines`` is true.

    The opt-in ``right_handed`` convention looks at FRONT from -Y, TOP
    from +Z and the right SIDE from +X. Screen-right cross screen-up equals
    toward-viewer in every view. It changes FRONT depth classification;
    rearranging or mirroring legacy linework cannot substitute for this.
    """
    if not isinstance(projection_convention, str) or projection_convention not in {"legacy", "right_handed"}:
        return {"success": False, "available": False, "complete": True,
                "error_code": "invalid_projection_convention", "views": []}
    view_axes = _VIEWS if projection_convention == "legacy" else _RIGHT_HANDED_VIEWS
    if not isinstance(views, (list, tuple)) or not views or any(
            not isinstance(view, str) or view not in _VIEWS for view in views):
        return {"success": False, "available": False, "complete": True,
                "error_code": "unsupported_orthographic_views", "views": []}
    if len(set(views)) != len(views):
        return {"success": False, "available": False, "complete": True,
                "error_code": "duplicate_orthographic_view", "views": []}
    threshold = _finite_number(feature_angle_degrees)
    if threshold is None or not 0.0 <= threshold <= 180.0:
        return {"success": False, "available": False, "complete": True,
                "error_code": "invalid_feature_angle", "views": []}
    if not isinstance(coordinate_frame, str) or not coordinate_frame.strip() or len(coordinate_frame) > 128 or \
            not isinstance(coordinate_unit, str) or not coordinate_unit.strip() or len(coordinate_unit) > 32:
        return {"success": False, "available": False, "complete": True,
                "error_code": "coordinate_metadata_required", "views": []}
    if not isinstance(hidden_line_removal, bool):
        return {"success": False, "available": False, "complete": True,
                "error_code": "invalid_hidden_line_removal", "views": []}
    if not isinstance(show_hidden_lines, bool):
        return {"success": False, "available": False, "complete": True,
                "error_code": "invalid_show_hidden_lines", "views": []}
    if show_hidden_lines and not hidden_line_removal:
        return {"success": False, "available": False, "complete": True,
                "error_code": "hidden_line_display_requires_removal", "views": []}
    if any(type(value) is not int or value < 1 for value in
           (max_node_visits, max_triangle_tests, max_output_intervals)):
        return {"success": False, "available": False, "complete": True,
                "error_code": "invalid_visibility_work_budget", "views": []}

    # Only the private CSG adapter may supply a prevalidated material boundary.
    # It checks the geometry hash, closed topology and winding, while retaining
    # inward cavity shells certified by its Boolean engine. Public callers use
    # the original strict validator and cannot opt out through their API.
    if not _material_boundary_prevalidated:
        validation = validate_triangle_mesh(vertices, triangles)
        if validation.get("complete") is not True or validation.get("valid") is not True:
            return {"success": False, "available": False,
                    "complete": validation.get("complete") is True,
                    "error_code": "closed_valid_outward_mesh_required",
                    "validation": validation, "views": []}
    if len(vertices) > MAX_VERTICES or len(triangles) > MAX_TRIANGLES:
        return {"success": False, "available": False, "complete": False,
                "error_code": "mesh_limit_exceeded", "views": []}

    points = [[float(value) for value in point] for point in vertices]
    faces = [tuple(face) for face in triangles]
    normals = [_normal(points, face) for face in faces]
    if any(normal is None for normal in normals):
        return {"success": False, "available": False, "complete": False,
                "error_code": "face_normal_unavailable", "views": []}
    edges = _edge_adjacency(faces)
    if any(len(adjacent) != 2 for adjacent in edges.values()):
        return {"success": False, "available": False, "complete": False,
                "error_code": "closed_edge_adjacency_required", "views": []}

    # Give near-parallel triangles a small numeric deadband to suppress their
    # shared triangulation diagonal without changing the requested angle.
    threshold_degrees = threshold
    threshold = math.radians(threshold_degrees)
    built_views: list[dict[str, Any]] = []
    total_segments = 0
    for view_name in views:
        horizontal, vertical, toward = view_axes[view_name]
        facing = [_dot(normal, toward) for normal in normals]  # type: ignore[arg-type]
        projected_x = _project(points, horizontal)
        projected_y = _project(points, vertical)
        view_segments: list[dict[str, Any]] = []
        raw_segments: list[dict[str, Any]] = []
        projected_segment_indexes: dict[tuple[tuple[float, float], tuple[float, float]], int] = {}
        for (left, right), adjacent in sorted(edges.items()):
            first, second = adjacent
            first_facing, second_facing = facing[first], facing[second]
            silhouette = ((first_facing > 1e-12 and second_facing <= 1e-12) or
                          (second_facing > 1e-12 and first_facing <= 1e-12))
            dot = min(1.0, max(-1.0, _dot(normals[first], normals[second])))  # type: ignore[arg-type]
            feature = math.acos(dot) > threshold + 1e-10
            if not silhouette and not feature:
                continue
            start = [projected_x[left], projected_y[left]]
            end = [projected_x[right], projected_y[right]]
            if start == end:
                continue
            first_point, second_point = tuple(start), tuple(end)
            key = ((first_point, second_point) if first_point <= second_point
                   else (second_point, first_point))
            kind = "silhouette" if silhouette else "feature"
            raw_segments.append({"edge": [left, right], "kind": kind, "start": start, "end": end,
                                 "incident_faces": [first, second]})
            if hidden_line_removal:
                continue
            existing_index = projected_segment_indexes.get(key)
            if existing_index is not None:
                if kind == "silhouette":
                    view_segments[existing_index]["kind"] = kind
                continue
            projected_segment_indexes[key] = len(view_segments)
            view_segments.append({"edge": [left, right], "kind": kind, "start": start, "end": end})
            if len(view_segments) > MAX_DRAWING_SEGMENTS:
                return {"success": False, "available": False, "complete": False,
                        "error_code": "drawing_segment_limit_exceeded", "views": []}
        visibility_summary = None
        if hidden_line_removal:
            projected_points = [[projected_x[index], projected_y[index], _dot(points[index], toward)]
                                for index in range(len(points))]
            visibility = projected_edge_visibility(
                projected_points, faces,
                [{"vertices": segment["edge"], "incident_faces": segment["incident_faces"]}
                 for segment in raw_segments],
                max_node_visits=max_node_visits,
                max_triangle_tests=max_triangle_tests,
                max_output_intervals=max_output_intervals,
            )
            if visibility.get("success") is not True or visibility.get("complete") is not True:
                return {"success": False, "available": False, "complete": False,
                        "error_code": visibility.get("error_code", "visibility_analysis_failed"),
                        "visibility": {key: value for key, value in visibility.items() if key != "edges"},
                        "views": []}
            classified_edges = visibility["edges"]
            visible = _classified_segments(raw_segments, classified_edges, "visible")
            visibility_summary = {key: value for key, value in visibility.items() if key != "edges"}
            visibility_summary["visible_edge_count"] = len(visible)
            if show_hidden_lines:
                hidden = _classified_segments(raw_segments, classified_edges, "hidden", excluded=set(visible))
                visibility_summary["hidden_edge_count"] = len(hidden)
                # Paint visible strokes over partially coincident hidden geometry.
                view_segments.extend(hidden.values())
            view_segments.extend(visible.values())
        total_segments += len(view_segments)
        if total_segments > MAX_TOTAL_DRAWING_SEGMENTS:
            return {"success": False, "available": False, "complete": False,
                    "error_code": "drawing_segment_limit_exceeded", "views": []}
        bounds = {"min": [min(projected_x), min(projected_y)],
                  "max": [max(projected_x), max(projected_y)]}
        analytic = _projection_bounds.get(view_name) if isinstance(_projection_bounds, Mapping) else None
        if isinstance(analytic, Mapping):
            for axis, key in enumerate(("horizontal", "vertical")):
                candidate = analytic.get(key)
                if (isinstance(candidate, (list, tuple)) and len(candidate) == 2
                        and all(_finite_number(value) is not None for value in candidate)):
                    bounds["min"][axis] = min(bounds["min"][axis], float(candidate[0]))
                    bounds["max"][axis] = max(bounds["max"][axis], float(candidate[1]))
        built_views.append({
            "name": view_name,
            "horizontal_axis": [int(value) for value in horizontal],
            "vertical_axis": [int(value) for value in vertical],
            "toward_viewer": [int(value) for value in toward],
            "bounds": bounds,
            "coordinate_frame": coordinate_frame,
            "coordinate_unit": coordinate_unit,
            "lines": view_segments,
            "line_count": len(view_segments),
            "visibility": visibility_summary,
        })
    try:
        svg = _make_svg(built_views, hidden_line_removal=hidden_line_removal,
                        show_hidden_lines=show_hidden_lines)
    except ValueError as exc:
        return {"success": False, "available": False, "complete": False,
                "error_code": str(exc), "views": []}
    return {
        "success": True, "available": True, "complete": True,
        "drawing_kind": "parameter-derived approximate mesh linework",
        "native_tessellation": False, "cuts_applied": False,
        "hidden_line_removal": hidden_line_removal, "occlusion_checked": hidden_line_removal,
        "show_hidden_lines": show_hidden_lines,
        "hole_semantics_inferred": False,
        "feature_angle_degrees": threshold_degrees,
        "visibility_work_budgets": {"max_node_visits": max_node_visits,
                                    "max_triangle_tests": max_triangle_tests,
                                    "max_output_intervals": max_output_intervals},
        "edge_policy": "adjacent face silhouette edges plus edges whose dihedral angle exceeds threshold; coplanar triangulation diagonals are suppressed",
        "coordinate_frame": coordinate_frame,
        "coordinate_unit": coordinate_unit,
        **({"projection_convention": projection_convention} if projection_convention != "legacy" else {}),
        "source_bounds": {
            "min": [min(point[axis] for point in points) for axis in range(3)],
            "max": [max(point[axis] for point in points) for axis in range(3)],
            "dimensions": [max(point[axis] for point in points) - min(point[axis] for point in points)
                           for axis in range(3)],
        },
        "views": built_views,
        "segment_count": total_segments,
        "svg": svg,
    }
