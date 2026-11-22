"""Project verified parameter-Boolean material boundaries without native reads.

Inward closed cavity shells describe the material boundary and retain their
winding. The public general mesh projector continues to require each component
to face outward. Neither path establishes E3D's native negative application.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any

from .orthographic_drawing import (
    _VIEWS, _arc_extrema_points, _dot, _finite_number, _generate_orthographic_drawing_from_validated_mesh,
    _make_svg, _validated_curve_primitives,
)
from .primitive_mesh import _spherical_dish_parameters, _world_transform
from .primitive_csg_mesh import csg_geometry_sha256
from .triangle_mesh_validation import MAX_TRIANGLES, MAX_VERTICES, validate_triangle_mesh


_FRAME = "WORLD coordinates; millimetres"
_KIND = "parameter-derived CSG mesh linework"
_PROVENANCE = "manifold3d Mesh64 subtraction of validated positive closed operands"
_ALLOWED_FINDINGS = ["component_winding_is_inward"]
_FOOTER = "PARAMETER CSG - NATIVE CUT SCOPE UNVERIFIED"
_CHECKS = ("finite_coordinates", "valid_indices", "nondegenerate_triangles", "duplicate_faces",
           "edge_manifold", "vertex_manifold", "winding_consistent", "closed_manifold")
_PARENT_ENVELOPE_SCOPE = (
    "union of projected CSG mesh and analytic positive-parent parameter envelope; "
    "conservative framing only, not the post-Boolean material extent"
)
_PARENT_ENVELOPE_FOOTER = "VIEW BOUNDS INCLUDE CONSERVATIVE SOURCE-PARENT ENVELOPE"


def _verified_curved_sources(mesh: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Recover curved operands only from a digest-verified native receipt."""
    source = mesh.get("source_geometry")
    digest = mesh.get("source_geometry_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return []
    try:
        observed = hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode("utf-8")).hexdigest()
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError):
        return []
    if observed != digest or not isinstance(source, Mapping):
        return []
    if (any(source.get(key) != expected for key, expected in (
            ("success", True), ("complete", True), ("partial", False), ("negative", False),
            ("is_negative", False), ("unit", "mm"), ("editable", True)))
            or not isinstance(source.get("read_errors"), Mapping) or source["read_errors"]
            or not isinstance(source.get("edit_reasons"), list) or source["edit_reasons"]
            or source.get("negative_owner") is not None):
        return []
    root = source.get("target_geometry")
    if not isinstance(root, Mapping) or any(
            source.get(key) != root.get(key) for key in (
                "element", "shape", "negative", "is_negative", "unit", "dimensions", "frame",
                "negative_children")):
        return []
    if (source.get("revision") != mesh.get("revision") or root.get("complete") is not True
            or root.get("negative") is not False or root.get("is_negative") is not False
            or root.get("unit") != "mm" or not isinstance(root.get("read_errors"), Mapping)
            or root["read_errors"] or not isinstance(root.get("edit_reasons"), list)
            or root["edit_reasons"]):
        return []
    children = root.get("negative_children")
    rows = mesh.get("source_operands")
    if not isinstance(children, list) or not isinstance(rows, (list, tuple)) or len(rows) != len(children) + 1:
        return []
    nodes = [root, *children]
    analytic: list[dict[str, Any]] = []
    native_types = {"CYLI": "CYLI", "CONE": "CONE", "BOX": "BOX", "DISH": "DISH"}
    negative_types = {"CYLI": "NCYL", "CONE": "NCON", "BOX": "NBOX", "DISH": "NDIS"}
    for index, (node, row) in enumerate(zip(nodes, rows)):
        if not isinstance(node, Mapping) or not isinstance(row, Mapping):
            return []
        shape, identity = node.get("shape"), node.get("element")
        negative = index > 0
        if (shape not in native_types or not isinstance(identity, Mapping)
                or identity.get("type") != (negative_types if negative else native_types).get(shape)
                or identity.get("refno") != row.get("refno") or row.get("shape") != shape
                or row.get("negative") is not negative):
            return []
        validation = row.get("validation")
        if (not isinstance(validation, Mapping) or validation.get("success") is not True
                or validation.get("complete") is not True or validation.get("valid") is not True):
            return []
        if (node.get("complete") is not True or node.get("negative") is not negative
                or node.get("is_negative") is not negative or node.get("unit") != "mm"
                or not isinstance(node.get("read_errors"), Mapping) or node["read_errors"]
                or not isinstance(node.get("edit_reasons"), list) or node["edit_reasons"]):
            return []
        if not negative and identity.get("refno") != mesh.get("refno"):
            return []
        if negative and (node.get("negative_children") != [] or node.get("member_refnos") != []):
            return []
        if shape not in {"CYLI", "CONE", "DISH"}:
            continue
        dimensions = node.get("dimensions")
        keys = (("DIAM", "HEIG") if shape == "CYLI" else
                ("DBOT", "DTOP", "HEIG") if shape == "CONE" else
                ("DIAM", "HEIG", "RADI"))
        if not isinstance(dimensions, Mapping) or set(dimensions) != set(keys):
            return []
        values: dict[str, float] = {}
        for key in keys:
            value = dimensions[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return []
            try:
                values[key] = float(value)
            except (OverflowError, ValueError):
                # A digest-valid payload can still contain a Python integer
                # outside IEEE-754 range.  It is not usable for analytic
                # matching, but the indexed CSG mesh remains drawable.
                return []
            if not math.isfinite(values[key]):
                return []
        if (shape == "CYLI" and (values["DIAM"] <= 0 or values["HEIG"] <= 0)
                or shape == "CONE" and (values["HEIG"] <= 0 or min(values["DBOT"], values["DTOP"]) < 0
                                         or max(values["DBOT"], values["DTOP"]) <= 0)
                or shape == "DISH" and (values["DIAM"] <= 0 or values["HEIG"] <= 0
                                         or values["RADI"] != 0)):
            return []
        frame = node.get("frame")
        transform = _world_transform({"world": frame.get("world")}) if isinstance(frame, Mapping) else None
        if transform is None:
            return []
        analytic.append({"refno": identity["refno"], "shape": shape, "dimensions": values,
                         "origin": transform[0], "axes": transform[1],
                         })
    return analytic


def _cross(left: Sequence[float], right: Sequence[float]) -> tuple[float, float, float]:
    return (left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0])


def _source_projected_circle(source: Mapping[str, Any], station: float, radius: float,
                             horizontal: Sequence[int], vertical: Sequence[int]
                             ) -> dict[str, Any] | None:
    """Project a source circle to its exact orthographic ellipse parameters."""
    axes, origin = source["axes"], source["origin"]
    center_world = [origin[index] + axes[2][index] * station for index in range(3)]
    center = [_dot(center_world, horizontal), _dot(center_world, vertical)]
    first = (_dot(axes[0], horizontal), _dot(axes[0], vertical))
    second = (_dot(axes[1], horizontal), _dot(axes[1], vertical))
    xx = first[0] * first[0] + second[0] * second[0]
    xy = first[0] * first[1] + second[0] * second[1]
    yy = first[1] * first[1] + second[1] * second[1]
    spread = math.hypot(xx - yy, 2.0 * xy)
    major_sq = max(0.0, (xx + yy + spread) * 0.5)
    minor_sq = max(0.0, (xx + yy - spread) * 0.5)
    major_radius, minor_radius = radius * math.sqrt(major_sq), radius * math.sqrt(minor_sq)
    if (not all(math.isfinite(value) for value in (*center, major_radius, minor_radius))
            or major_radius <= 0.0 or minor_radius <= major_radius * 1e-10):
        return None
    angle = 0.5 * math.atan2(2.0 * xy, xx - yy)
    direction = (math.cos(angle), math.sin(angle))
    major_axis = [direction[0] * major_radius, direction[1] * major_radius]
    is_circle = abs(major_radius - minor_radius) <= max(1e-12, major_radius * 1e-12)
    return {"type": "circle" if is_circle else "ellipse", "center": center,
            "radius": major_radius if is_circle else None,
            "major_axis": major_axis if not is_circle else None,
            "major_radius": major_radius, "minor_radius": minor_radius}


def _curve_parameter(point: Sequence[float], geometry: Mapping[str, Any]) -> float:
    major = geometry.get("major_axis") or [geometry["major_radius"], 0.0]
    major_radius = geometry["major_radius"]
    minor_radius = geometry["minor_radius"]
    direction = (major[0] / major_radius, major[1] / major_radius)
    perpendicular = (-direction[1], direction[0])
    delta = (point[0] - geometry["center"][0], point[1] - geometry["center"][1])
    return math.atan2((delta[0] * perpendicular[0] + delta[1] * perpendicular[1]) / minor_radius,
                      (delta[0] * direction[0] + delta[1] * direction[1]) / major_radius)


class _ProjectedVertexIndex:
    """Find projected mesh vertices within a tolerance derived from float precision."""

    def __init__(self, vertices: Sequence[Sequence[float]], horizontal: Sequence[int],
                 vertical: Sequence[int]):
        projected = [(_dot(point, horizontal), _dot(point, vertical), point) for point in vertices]
        ulps = [max((math.ulp(abs(row[axis])) for row in projected
                     if math.isfinite(row[axis]) and row[axis] != 0.0),
                    default=math.ulp(1.0)) for axis in range(2)]
        # Bucket scale bounds local ULP-sized searches; acceptance below uses
        # each candidate's own coordinate precision.
        self._cell_size = tuple(max(1e-9, 8.0 * ulp) for ulp in ulps)
        self._buckets: dict[tuple[int, int], list[tuple[float, float, Sequence[float]]]] = {}
        for x, y, point in projected:
            if not math.isfinite(x) or not math.isfinite(y):
                continue
            self._buckets.setdefault(self._cell((x, y)), []).append((x, y, point))

    def _cell(self, point: Sequence[float]) -> tuple[int, int]:
        return (math.floor(point[0] / self._cell_size[0]),
                math.floor(point[1] / self._cell_size[1]))

    def nearby(self, point: Sequence[float]) -> list[Sequence[float]]:
        tolerances = tuple(max(1e-9, 8.0 * math.ulp(abs(value))) for value in point)
        center = self._cell(point)
        result: list[Sequence[float]] = []
        for x_cell in range(center[0] - 1, center[0] + 2):
            for y_cell in range(center[1] - 1, center[1] + 2):
                for x, y, vertex in self._buckets.get((x_cell, y_cell), ()):
                    dx, dy = abs(x - point[0]), abs(y - point[1])
                    candidate_tolerances = (max(tolerances[0], 8.0 * math.ulp(abs(x))),
                                            max(tolerances[1], 8.0 * math.ulp(abs(y))))
                    if (dx <= candidate_tolerances[0] and dy <= candidate_tolerances[1]
                            and math.hypot(dx / candidate_tolerances[0],
                                           dy / candidate_tolerances[1]) <= 1.0):
                        result.append(vertex)
        return result


class _ProjectedEndpointIndex:
    """Join projected endpoints within local float precision, preserving ambiguity."""

    def __init__(self, points: Sequence[Sequence[float]]):
        self._origin = tuple(min(point[axis] for point in points) for axis in range(2))
        # The largest ULP is used only to size lookup cells.  Acceptance below
        # is calculated from each candidate pair so a distant large model does
        # not widen the tolerance of small local geometry.
        self._cell_size = tuple(max(1e-9, 8.0 * max(
            (math.ulp(abs(point[axis])) for point in points if point[axis] != 0.0),
            default=math.ulp(1.0))) for axis in range(2))
        self._points: list[tuple[float, float]] = []
        self._buckets: dict[tuple[int, int], list[int]] = {}

    def _cell(self, point: Sequence[float]) -> tuple[int, int]:
        return tuple(math.floor((point[axis] - self._origin[axis]) / self._cell_size[axis])
                     for axis in range(2))  # type: ignore[return-value]

    @staticmethod
    def _point_tolerance(point: Sequence[float]) -> tuple[float, float]:
        return tuple(max(1e-9, 8.0 * math.ulp(abs(value)))
                     for value in point)  # type: ignore[return-value]

    @property
    def points(self) -> tuple[tuple[float, float], ...]:
        """Return canonical points without exposing the mutable index storage."""
        return tuple(self._points)

    def add(self, raw: Sequence[float]) -> int:
        point = (float(raw[0]), float(raw[1]))
        cell = self._cell(point)
        point_tolerance = self._point_tolerance(point)
        candidates: list[tuple[float, int]] = []
        for x_cell in range(cell[0] - 1, cell[0] + 2):
            for y_cell in range(cell[1] - 1, cell[1] + 2):
                for index in self._buckets.get((x_cell, y_cell), ()):
                    prior = self._points[index]
                    prior_tolerance = self._point_tolerance(prior)
                    tolerance = (max(point_tolerance[0], prior_tolerance[0]),
                                 max(point_tolerance[1], prior_tolerance[1]))
                    dx = abs(point[0] - prior[0]) / tolerance[0]
                    dy = abs(point[1] - prior[1]) / tolerance[1]
                    distance = math.hypot(dx, dy)
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


def _ring_matches_curved_source(
    samples: Sequence[Sequence[float]], *, horizontal: Sequence[int], vertical: Sequence[int],
    projected_vertices: _ProjectedVertexIndex,
    sources: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Match a complete projected ellipse to one verified WORLD circle section."""
    source_matches: list[dict[str, Any]] = []
    for source in sources:
        shape, dimensions = source["shape"], source["dimensions"]
        axes, origin = source["axes"], source["origin"]
        height = dimensions["HEIG"]
        sphere_parameters = None
        if shape == "CYLI":
            low_radius = high_radius = dimensions["DIAM"] * 0.5
        elif shape == "CONE":
            low_radius, high_radius = dimensions["DBOT"] * 0.5, dimensions["DTOP"] * 0.5
        else:
            try:
                sphere_parameters = _spherical_dish_parameters(dimensions["DIAM"], height)
            except (TypeError, ValueError, OverflowError):
                continue
            low_radius = high_radius = dimensions["DIAM"] * 0.5
        station_min, station_max = ((0.0, height) if shape == "DISH" else
                                    (-height * 0.5, height * 0.5))
        coordinate_ulps = [math.ulp(abs(value))
                           for value in (*origin, *(component for sample in samples for component in sample))
                           if math.isfinite(value) and value != 0.0]
        coordinate_tolerance = 32.0 * max(coordinate_ulps, default=math.ulp(1.0))
        numeric_tolerance = max(1e-12, coordinate_tolerance)
        radial_tolerance = max(numeric_tolerance, max(low_radius, high_radius) * 2e-5)
        sample_stations: list[list[float]] = []
        for sample in samples:
            candidates = projected_vertices.nearby(sample)
            sample_matches: list[float] = []
            for point in candidates:
                delta = [point[axis] - origin[axis] for axis in range(3)]
                local = [_dot(delta, axis) for axis in axes]
                axial = local[2]
                if axial < station_min - numeric_tolerance or axial > station_max + numeric_tolerance:
                    continue
                if shape == "CYLI":
                    expected = low_radius
                elif shape == "CONE":
                    expected = low_radius + (high_radius - low_radius) * (axial + height * 0.5) / height
                else:
                    assert sphere_parameters is not None
                    sphere_radius, sphere_center, _ = sphere_parameters
                    sphere_offset = abs(axial - sphere_center)
                    if sphere_offset > sphere_radius + numeric_tolerance:
                        continue
                    expected = math.sqrt(max(0.0, (sphere_radius - sphere_offset)
                                              * (sphere_radius + sphere_offset)))
                if abs(math.hypot(local[0], local[1]) - expected) <= radial_tolerance:
                    sample_matches.append(axial)
            if not sample_matches:
                sample_stations = []
                break
            sample_stations.append(sample_matches)
        if len(sample_stations) != len(samples):
            continue

        candidate_stations: list[float] = []
        for candidate in sample_stations[0]:
            if all(any(abs(candidate - other) <= numeric_tolerance for other in row)
                   for row in sample_stations[1:]) and not any(
                       abs(candidate - existing) <= numeric_tolerance for existing in candidate_stations):
                candidate_stations.append(candidate)
        valid_sections: list[tuple[float, dict[str, Any], float]] = []
        for station in candidate_stations:
            if shape == "DISH":
                assert sphere_parameters is not None
                sphere_radius, sphere_center, _ = sphere_parameters
                sphere_offset = abs(station - sphere_center)
                source_radius = math.sqrt(max(0.0, (sphere_radius - sphere_offset)
                                              * (sphere_radius + sphere_offset)))
            elif shape == "CONE" and abs(high_radius - low_radius) > 1e-14:
                if not -height * 0.5 - numeric_tolerance <= station <= height * 0.5 + numeric_tolerance:
                    continue
                source_radius = low_radius + (high_radius - low_radius) * (station + height * 0.5) / height
            else:
                source_radius = low_radius
            geometry = _source_projected_circle(source, station, source_radius, horizontal, vertical)
            if geometry is None:
                continue
            major = geometry["major_axis"] or [geometry["radius"], 0.0]
            major_radius, minor_radius = geometry["major_radius"], geometry["minor_radius"]
            direction = (major[0] / major_radius, major[1] / major_radius)
            perpendicular = (-direction[1], direction[0])
            residual = 0.0
            for sample in samples:
                delta = (sample[0] - geometry["center"][0], sample[1] - geometry["center"][1])
                major_coordinate = (delta[0] * direction[0] + delta[1] * direction[1]) / major_radius
                minor_coordinate = (delta[0] * perpendicular[0] + delta[1] * perpendicular[1]) / minor_radius
                residual = max(residual, abs(math.hypot(major_coordinate, minor_coordinate) - 1.0)
                               * major_radius)
            if residual <= max(numeric_tolerance, major_radius * 2e-5):
                valid_sections.append((station, geometry, residual))
        if not valid_sections:
            continue
        # Multiple axial sections can project to the same closed loop when the
        # view looks down the source axis. Preserve that ambiguity explicitly.
        first_geometry = valid_sections[0][1]
        same_projection = all(
            math.dist(item[1]["center"], first_geometry["center"]) <= numeric_tolerance
            and abs(item[1]["major_radius"] - first_geometry["major_radius"]) <= numeric_tolerance
            and abs(item[1]["minor_radius"] - first_geometry["minor_radius"]) <= numeric_tolerance
            and (item[1]["type"] == "circle" or min(
                math.dist(item[1]["major_axis"], first_geometry["major_axis"]),
                math.dist(item[1]["major_axis"], (-first_geometry["major_axis"][0],
                                                    -first_geometry["major_axis"][1])))
                 <= numeric_tolerance)
            for item in valid_sections[1:])
        if not same_projection:
            continue
        stations = sorted({item[0] for item in valid_sections})
        source_matches.append({"refno": source["refno"], "shape": shape,
                               "axial_station_mm": stations[0] if len(stations) == 1 else None,
                               "axial_station_candidates_mm": stations,
                               "source_radius_mm": (low_radius if shape == "CYLI" else
                                   math.sqrt(max(0.0, (sphere_parameters[0] - abs(stations[0] - sphere_parameters[1]))
                                                 * (sphere_parameters[0] + abs(stations[0] - sphere_parameters[1]))))
                                   if shape == "DISH" and sphere_parameters is not None else
                                   low_radius + (high_radius - low_radius) * (stations[0] + height * 0.5) / height),
                               # Keep the in-memory receipt immutable-like and
                               # aligned with the fitted curve representation;
                               # JSON serialization will normalize tuples to
                               # arrays at the API boundary.
                               "center": tuple(first_geometry["center"]),
                               "major_axis": first_geometry["major_axis"],
                               "major_radius_mm": first_geometry["major_radius"],
                               "minor_radius_mm": first_geometry["minor_radius"],
                               "geometry": first_geometry,
                               "fit_max_residual_mm": max(item[2] for item in valid_sections)})
    # A projected ring may coincide with more than one native operand. Without
    # a unique source, retain the mesh edges instead of assigning false lineage.
    return source_matches[0] if len(source_matches) == 1 else None


def _fitted_projected_circles(
    view: Mapping[str, Any], *, mesh_vertices: Sequence[Sequence[float]],
    curved_sources: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Recover only complete, regular circular edge rings from projected edges.

    CSG remains evaluated on the indexed mesh. A projected cycle is smoothed
    only when its WORLD vertices also match a verified CYLI/CONE cross-section.
    Open, clipped, tilted, or source-unmatched loops retain their original lines.
    """
    if not curved_sources:
        return []
    lines = view.get("lines")
    if not isinstance(lines, (list, tuple)):
        return []
    eligible = [index for index, line in enumerate(lines)
                if isinstance(line, Mapping) and line.get("visibility", "visible") == "visible"
                and line.get("kind") in {"silhouette", "feature"}]
    if len(eligible) < 8:
        return []
    name = view.get("name")
    view_axes = _VIEWS.get(name) if isinstance(name, str) else None
    if view_axes is None:
        return []
    horizontal, vertical, _toward = view_axes
    normal = _cross(horizontal, vertical)
    projected_points: list[Sequence[float]] = []
    for point in mesh_vertices:
        if not isinstance(point, (list, tuple)) or len(point) != 3:
            return []
        projected_points.append(point)
    projected_vertices = _ProjectedVertexIndex(projected_points, horizontal, vertical)
    endpoint_values: dict[int, tuple[tuple[float, float], tuple[float, float]]] = {}
    all_endpoints: list[tuple[float, float]] = []
    for line_index in eligible:
        line = lines[line_index]
        parsed = []
        for raw in (line.get("start"), line.get("end")):
            if not isinstance(raw, (list, tuple)) or len(raw) != 2:
                parsed = []
                break
            try:
                point = (float(raw[0]), float(raw[1]))
            except (TypeError, ValueError, OverflowError):
                parsed = []
                break
            if not all(math.isfinite(value) for value in point):
                parsed = []
                break
            parsed.append(point)
        if len(parsed) == 2:
            endpoint_values[line_index] = (parsed[0], parsed[1])
            all_endpoints.extend(parsed)
    if not all_endpoints:
        return []
    endpoint_index = _ProjectedEndpointIndex(all_endpoints)
    edge_points: dict[int, tuple[int, int]] = {}
    adjacency: dict[int, list[int]] = {}

    for line_index in eligible:
        line = lines[line_index]
        endpoints = endpoint_values.get(line_index)
        if endpoints is None:
            continue
        first, second = (endpoint_index.add(point) for point in endpoints)
        if first == second:
            continue
        edge_points[line_index] = (first, second)
        adjacency.setdefault(first, []).append(line_index)
        adjacency.setdefault(second, []).append(line_index)
    points = endpoint_index.points
    unvisited = set(edge_points)
    curves: list[dict[str, Any]] = []
    while unvisited:
        seed = unvisited.pop()
        component = [seed]
        stack = [seed]
        while stack:
            edge_index = stack.pop()
            for vertex in edge_points[edge_index]:
                for adjacent in adjacency.get(vertex, ()):
                    if adjacent in unvisited:
                        unvisited.remove(adjacent)
                        component.append(adjacent)
                        stack.append(adjacent)
        vertices = {vertex for edge_index in component for vertex in edge_points[edge_index]}
        if len(component) < 8 or len(vertices) != len(component) or any(
                len([edge for edge in adjacency.get(vertex, ()) if edge in component]) != 2
                for vertex in vertices):
            continue
        samples = [points[vertex] for vertex in sorted(vertices)]
        kinds = {lines[index].get("kind") for index in component}
        if not kinds or not kinds.issubset({"silhouette", "feature"}):
            continue
        # A tilted analytic rim can straddle the silhouette classifier: some
        # tessellated chords are tagged feature while adjacent chords are
        # tagged silhouette.  The closed, source-matched cycle is still one
        # geometric boundary; preserve the stronger silhouette classification
        # for the fitted presentation entity.
        curve_kind = "silhouette" if "silhouette" in kinds else "feature"
        chords = [math.dist(points[first], points[second])
                  for first, second in (edge_points[index] for index in component)]
        perimeter = math.fsum(chords)
        source_match = _ring_matches_curved_source(
            samples, horizontal=horizontal, vertical=vertical,
            projected_vertices=projected_vertices, sources=curved_sources)
        if source_match is None:
            continue
        geometry = source_match["geometry"]
        ordered_vertices = [edge_points[component[0]][0]]
        ordered_edges: list[int] = []
        previous_edge: int | None = None
        current_vertex = ordered_vertices[0]
        while len(ordered_edges) < len(component):
            next_edge = next((edge for edge in adjacency.get(current_vertex, ())
                              if edge in component and edge != previous_edge), None)
            if next_edge is None:
                break
            first, second = edge_points[next_edge]
            next_vertex = second if first == current_vertex else first
            ordered_edges.append(next_edge)
            if next_vertex == ordered_vertices[0]:
                current_vertex = next_vertex
                break
            ordered_vertices.append(next_vertex)
            previous_edge, current_vertex = next_edge, next_vertex
        if len(ordered_edges) != len(component) or current_vertex != ordered_vertices[0]:
            continue
        angles = [_curve_parameter(points[vertex], geometry) for vertex in ordered_vertices]
        signed_steps = [((angles[(index + 1) % len(angles)] - angle + math.pi) % (2.0 * math.pi) - math.pi)
                        for index, angle in enumerate(angles)]
        angular_length = math.fsum(abs(step) for step in signed_steps)
        circumference = math.pi * (3.0 * (geometry["major_radius"] + geometry["minor_radius"])
                                   - math.sqrt((3.0 * geometry["major_radius"] + geometry["minor_radius"])
                                               * (geometry["major_radius"] + 3.0 * geometry["minor_radius"])))
        if (not signed_steps or any(abs(step) <= 1e-12 or abs(step) >= math.pi for step in signed_steps)
                or len({step > 0 for step in signed_steps}) != 1
                or not math.isfinite(angular_length) or abs(angular_length / (2.0 * math.pi) - 1.0) > 2e-2
                or not math.isfinite(perimeter) or abs(perimeter / circumference - 1.0) > 2e-2):
            continue
        angle_steps = [abs(step) for step in signed_steps]
        # The second derivative of an ellipse's angular parameterization is
        # bounded by its major radius, so a*delta^2/8 bounds chord sagitta.
        chord_error = max((geometry["major_radius"] * delta * delta / 8.0 for delta in angle_steps), default=0.0)
        curve = {key: deepcopy(value) for key, value in geometry.items() if value is not None}
        curve.update({
            "kind": curve_kind, "visibility": "visible",
            "plane": {"view": view.get("name"), "coordinate_unit": view.get("coordinate_unit", "mm")},
            "start_angle_degrees": 0.0, "end_angle_degrees": 360.0,
            "source_line_indices": sorted(component), "edge_count": len(component),
            "fit_max_residual_mm": source_match["fit_max_residual_mm"],
            "fit_max_chord_error_mm": chord_error,
            "analytic_source": source_match,
        })
        curve.pop("geometry", None)
        curves.append(curve)
    return curves


def _fitted_dish_silhouette_arcs(
    view: Mapping[str, Any], *, mesh_vertices: Sequence[Sequence[float]],
    curved_sources: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Map uniquely verified side-on DISH spherical silhouette chains to arcs.

    A Boolean cutter can terminate a spherical silhouette before the source
    dish's natural rim.  Each projected chain is therefore validated against
    the source sphere and its own endpoint span; requiring the full source
    crown span would either reject valid cut arcs or draw through a hole.
    """
    if not curved_sources:
        return []
    lines = view.get("lines")
    name = view.get("name")
    view_axes = _VIEWS.get(name) if isinstance(name, str) else None
    if not isinstance(lines, (list, tuple)) or view_axes is None:
        return []
    horizontal, vertical, toward = view_axes
    world_points: list[Sequence[float]] = []
    for point in mesh_vertices:
        if not isinstance(point, (list, tuple)) or len(point) != 3:
            return []
        world_points.append(point)

    matches: dict[tuple[int, ...], list[dict[str, Any]]] = defaultdict(list)
    for source in curved_sources:
        if source.get("shape") != "DISH":
            continue
        axes, origin = source["axes"], source["origin"]
        try:
            sphere_radius, sphere_center_station, _ = _spherical_dish_parameters(
                source["dimensions"]["DIAM"], source["dimensions"]["HEIG"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        axis_screen = (_dot(axes[2], horizontal), _dot(axes[2], vertical))
        axis_screen_length = math.hypot(*axis_screen)
        axis_toward = _dot(axes[2], toward)
        radial_alignment = max(abs(_dot(axes[0], toward)), abs(_dot(axes[1], toward)))
        if (abs(axis_screen_length - 1.0) > 1e-10 or abs(axis_toward) > 1e-10
                or radial_alignment < 1.0 - 1e-10):
            continue
        axis_evidence = (
            {"axis_vertical_sign": 1.0 if axis_screen[1] >= 0.0 else -1.0}
            if abs(axis_screen[0]) <= 1e-12 else
            {"projected_axis_unit": [value / axis_screen_length for value in axis_screen]}
        )
        center_world = [origin[index] + axes[2][index] * sphere_center_station
                        for index in range(3)]
        center = (_dot(center_world, horizontal), _dot(center_world, vertical))
        coordinate_ulps = [math.ulp(abs(value)) for value in (*origin, *center_world)
                           if value != 0.0]
        tolerance = max(1e-8, sphere_radius * 1e-10,
                        32.0 * max(coordinate_ulps, default=math.ulp(1.0)))

        edge_points: dict[int, tuple[int, int]] = {}
        adjacency: dict[int, list[int]] = defaultdict(list)
        endpoint_screen: dict[int, tuple[float, float]] = {}
        endpoint_station: dict[int, float] = {}
        for line_index, line in enumerate(lines):
            if (not isinstance(line, Mapping) or line.get("visibility", "visible") != "visible"
                    or line.get("kind") != "silhouette"):
                continue
            edge = line.get("edge")
            start, end = line.get("start"), line.get("end")
            if (not isinstance(edge, (list, tuple)) or len(edge) != 2
                    or any(type(index) is not int or not 0 <= index < len(world_points) for index in edge)
                    or not isinstance(start, (list, tuple)) or len(start) != 2
                    or not isinstance(end, (list, tuple)) or len(end) != 2):
                continue
            valid = True
            for vertex_index, projected in zip(edge, (start, end)):
                point = world_points[vertex_index]
                delta = [point[index] - center_world[index] for index in range(3)]
                axial = _dot([point[index] - origin[index] for index in range(3)], axes[2])
                projected_delta = (float(projected[0]) - center[0], float(projected[1]) - center[1])
                if (axial < -tolerance or axial > source["dimensions"]["HEIG"] + tolerance
                        or abs(math.hypot(*delta) - sphere_radius) > tolerance
                        or abs(_dot(delta, toward)) > tolerance
                        or abs(math.hypot(*projected_delta) - sphere_radius) > tolerance):
                    valid = False
                    break
                endpoint_screen[vertex_index] = (float(projected[0]), float(projected[1]))
                endpoint_station[vertex_index] = axial
            if not valid or all(endpoint_station[index] <= tolerance for index in edge):
                continue
            first, second = edge
            if first == second:
                continue
            edge_points[line_index] = (first, second)
            adjacency[first].append(line_index)
            adjacency[second].append(line_index)

        unvisited = set(edge_points)
        while unvisited:
            seed = unvisited.pop()
            component = [seed]
            stack = [seed]
            while stack:
                line_index = stack.pop()
                for vertex in edge_points[line_index]:
                    for adjacent in adjacency[vertex]:
                        if adjacent in unvisited:
                            unvisited.remove(adjacent)
                            component.append(adjacent)
                            stack.append(adjacent)
            vertices = {vertex for line_index in component for vertex in edge_points[line_index]}
            endpoints = [vertex for vertex in vertices
                         if sum(line_index in component for line_index in adjacency[vertex]) == 1]
            if (len(component) < 8 or len(endpoints) != 2 or len(vertices) != len(component) + 1
                    or any(sum(line_index in component for line_index in adjacency[vertex]) not in {1, 2}
                           for vertex in vertices)):
                continue
            # A 3-D edge component can collapse or branch after projection
            # (for example when front/back edges share the same screen point).
            # Require a unique 2-D open chain before emitting one analytic arc;
            # otherwise retain the original Mesh64 edges.
            projected_raw = [
                endpoint
                for line_index in component
                for endpoint in (lines[line_index]["start"], lines[line_index]["end"])
            ]
            projected_index = _ProjectedEndpointIndex(projected_raw)
            projected_edges: list[tuple[int, int]] = []
            projected_adjacency: dict[int, list[int]] = defaultdict(list)
            for line_index in component:
                line = lines[line_index]
                first = projected_index.add(line["start"])
                second = projected_index.add(line["end"])
                if first == second:
                    projected_edges = []
                    break
                local_edge = len(projected_edges)
                projected_edges.append((first, second))
                projected_adjacency.setdefault(first, []).append(local_edge)
                projected_adjacency.setdefault(second, []).append(local_edge)
            if not projected_edges:
                continue
            projected_points = projected_index.points
            projected_endpoints = [vertex for vertex, incident in projected_adjacency.items()
                                   if len(incident) == 1]
            if (len(projected_points) != len(component) + 1
                    or len(projected_endpoints) != 2
                    or any(len(incident) not in {1, 2}
                           for incident in projected_adjacency.values())):
                continue
            start_vertex = min(endpoints, key=lambda index: endpoint_screen[index])
            ordered_vertices = [start_vertex]
            ordered_lines: list[int] = []
            previous_line: int | None = None
            current = start_vertex
            while len(ordered_lines) < len(component):
                next_line = next((line_index for line_index in adjacency[current]
                                  if line_index in component and line_index != previous_line), None)
                if next_line is None:
                    break
                first, second = edge_points[next_line]
                following = second if first == current else first
                ordered_lines.append(next_line)
                ordered_vertices.append(following)
                previous_line, current = next_line, following
            if len(ordered_lines) != len(component) or ordered_vertices[-1] not in endpoints:
                continue

            points = [endpoint_screen[index] for index in ordered_vertices]
            angles = [math.atan2(point[1] - center[1], point[0] - center[0]) for point in points]
            steps = [((angles[index + 1] - angles[index] + math.pi) % (2.0 * math.pi) - math.pi)
                     for index in range(len(angles) - 1)]
            if (not steps or any(abs(step) <= 1e-12 or abs(step) >= math.pi for step in steps)
                    or len({step > 0 for step in steps}) != 1):
                continue
            signed_span = math.fsum(steps)
            span = abs(signed_span)
            if (span <= 0.0 or span >= 2.0 * math.pi
                    or span > 2.0 * math.acos(-sphere_center_station / sphere_radius) + 1e-8):
                continue
            if signed_span < 0.0:
                ordered_vertices.reverse()
                angles.reverse()
                span = -signed_span
            start_angle = angles[0] % (2.0 * math.pi)
            end_angle = start_angle + span
            residual = max(abs(math.dist(point, center) - sphere_radius) for point in points)
            curve = {
                "type": "arc", "center": center, "radius": sphere_radius,
                "start_angle_degrees": math.degrees(start_angle),
                "end_angle_degrees": math.degrees(end_angle),
                "kind": "silhouette", "visibility": "visible",
                "plane": {"view": name, "coordinate_unit": view.get("coordinate_unit", "mm")},
                "source_line_indices": sorted(component), "edge_count": len(component),
                "fit_max_residual_mm": residual,
                "fit_max_chord_error_mm": max(sphere_radius * abs(step) ** 2 / 8.0 for step in steps),
                "analytic_source": {
                    "shape": "DISH", "refno": source["refno"], "source_kind": "spherical_silhouette_arc",
                    "center": center, "source_radius_mm": sphere_radius,
                    "sphere_center_station_mm": sphere_center_station,
                    "axial_domain_mm": [0.0, float(source["dimensions"]["HEIG"])],
                    **axis_evidence,
                },
            }
            matches[tuple(sorted(component))].append(curve)
    return [curves[0] for curves in matches.values() if len(curves) == 1]


def _analytic_parent_source(mesh: Mapping[str, Any]) -> tuple[str, dict[str, float], list[float], list[list[float]]] | None:
    """Read a strictly self-consistent CYLI/CONE source envelope receipt."""
    source = mesh.get("source_geometry")
    digest = mesh.get("source_geometry_sha256")
    if (not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
        return None
    try:
        observed = hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode("utf-8")).hexdigest()
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError):
        return None
    if observed != digest:
        return None
    if not isinstance(source, Mapping) or any(source.get(key) != expected for key, expected in (
            ("success", True), ("complete", True), ("partial", False), ("negative", False),
            ("is_negative", False), ("unit", "mm"), ("editable", True))):
        return None
    if (not isinstance(source.get("read_errors"), Mapping) or source["read_errors"]
            or not isinstance(source.get("edit_reasons"), list) or source["edit_reasons"]
            or source.get("negative_owner") is not None):
        return None
    target = source.get("target_geometry")
    if not isinstance(target, Mapping):
        return None
    duplicated = ("element", "shape", "negative", "is_negative", "unit", "dimensions", "frame",
                  "negative_children")
    if any(key not in source or source[key] != target.get(key) for key in duplicated):
        return None
    shape = target.get("shape")
    identity = target.get("element")
    if (not isinstance(shape, str) or shape not in {"CYLI", "CONE"} or mesh.get("shape") != shape
            or not isinstance(identity, Mapping) or identity.get("type") != shape
            or identity.get("refno") != mesh.get("refno")
            or source.get("revision") != mesh.get("revision")
            or target.get("complete") is not True or target.get("negative") is not False
            or target.get("is_negative") is not False or target.get("unit") != "mm"
            or not isinstance(target.get("read_errors"), Mapping) or target["read_errors"]
            or not isinstance(target.get("edit_reasons"), list) or target["edit_reasons"]):
        return None
    dimensions = target.get("dimensions")
    keys = ("DIAM", "HEIG") if shape == "CYLI" else ("DBOT", "DTOP", "HEIG")
    if not isinstance(dimensions, Mapping) or set(dimensions) != set(keys):
        return None
    values: dict[str, float] = {}
    for key in keys:
        value = dimensions[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            values[key] = float(value)
        except (OverflowError, ValueError):
            return None
        if not math.isfinite(values[key]):
            return None
    if shape == "CYLI":
        if values["DIAM"] <= 0 or values["HEIG"] <= 0:
            return None
    elif (values["HEIG"] <= 0 or min(values["DBOT"], values["DTOP"]) < 0
          or max(values["DBOT"], values["DTOP"]) <= 0):
        return None
    frame = target.get("frame")
    if (not isinstance(frame, Mapping) or frame.get("relative_to_owner") is not None
            or not isinstance(frame.get("storage_frame_refno"), str)):
        return None
    transform = _world_transform({"world": frame.get("world")})
    if transform is None:
        return None
    origin, axes = transform
    return shape, values, origin, axes


def _support_radius(shape: str, dimensions: Mapping[str, float], axes: list[list[float]],
                    direction: Sequence[int]) -> float:
    local = [math.fsum(direction[world] * axes[index][world] for world in range(3))
             for index in range(3)]
    radial = math.hypot(local[0], local[1])
    axial = local[2]
    height = dimensions["HEIG"]
    if shape == "CYLI":
        return dimensions["DIAM"] * 0.5 * radial + height * 0.5 * abs(axial)
    bottom = dimensions["DBOT"] * 0.5 * radial - height * 0.5 * axial
    top = dimensions["DTOP"] * 0.5 * radial + height * 0.5 * axial
    return max(bottom, top)


def _analytic_parent_projection_bounds(
    parent: tuple[str, dict[str, float], list[float], list[list[float]]],
    horizontal: Sequence[int], vertical: Sequence[int],
) -> dict[str, list[float]] | None:
    shape, dimensions, origin, axes = parent
    result: dict[str, list[float]] = {}
    for name, direction in (("horizontal", horizontal), ("vertical", vertical)):
        center = math.fsum(origin[index] * direction[index] for index in range(3))
        negative = [-value for value in direction]
        low = center - _support_radius(shape, dimensions, axes, negative)
        high = center + _support_radius(shape, dimensions, axes, direction)
        if not math.isfinite(low) or not math.isfinite(high):
            return None
        result[name] = [low, high]
    return result


def _parent_projection_tolerance(mesh: Mapping[str, Any], parent: tuple[str, dict[str, float], list[float], list[list[float]]]) -> float:
    """Bound the source-parent sampling gap before accepting a parent edge.

    A Boolean result may retain only a small part of its positive operand. In
    that case the analytic parent envelope must not enlarge the material
    drawing bounds. The allowance covers the inscribed circular sampling used
    by the producer, while an explicit tolerance receipt is preferred when it
    is available.
    """
    operand_rows = mesh.get("source_operands")
    if isinstance(operand_rows, (list, tuple)) and operand_rows:
        first = operand_rows[0]
        tessellation = first.get("tessellation") if isinstance(first, Mapping) else None
        if isinstance(tessellation, Mapping):
            value = _finite_number(tessellation.get("max_surface_deviation_mm"))
            if value is not None and value >= 0.0:
                return max(1e-7, 2.0 * value + 1e-7)
    tessellation = mesh.get("tessellation")
    if isinstance(tessellation, Mapping):
        value = _finite_number(tessellation.get("max_operand_surface_deviation_mm"))
        if value is not None and value >= 0.0:
            return max(1e-7, 2.0 * value + 1e-7)
    shape, dimensions, _, _ = parent
    segments = mesh.get("radial_segments", 48)
    if type(segments) is not int or segments < 12:
        segments = 48
    radius = (dimensions["DIAM"] / 2.0 if shape == "CYLI" else
              max(dimensions["DBOT"], dimensions["DTOP"]) / 2.0)
    return max(1e-7, 2.0 * radius * (1.0 - math.cos(math.pi / segments)) + 1e-7)


def _parent_envelope_supported(
    mesh: Mapping[str, Any],
    parent: tuple[str, dict[str, float], list[float], list[list[float]]],
    view_name: str,
    candidate: Mapping[str, Sequence[float]],
) -> bool:
    """Accept a parent envelope only when material reaches all four edges."""
    vertices = mesh.get("vertices")
    if not isinstance(vertices, (list, tuple)) or not vertices:
        return False
    horizontal, vertical, _ = _VIEWS[view_name]
    try:
        projected_x = [_dot(point, horizontal) for point in vertices]
        projected_y = [_dot(point, vertical) for point in vertices]
        candidate_x = candidate["horizontal"]
        candidate_y = candidate["vertical"]
        if (len(candidate_x) != 2 or len(candidate_y) != 2 or
                any(_finite_number(value) is None for value in (*candidate_x, *candidate_y))):
            return False
        tolerance = _parent_projection_tolerance(mesh, parent)
        return (float(min(projected_x)) - float(candidate_x[0]) <= tolerance and
                float(candidate_x[1]) - float(max(projected_x)) <= tolerance and
                float(min(projected_y)) - float(candidate_y[0]) <= tolerance and
                float(candidate_y[1]) - float(max(projected_y)) <= tolerance)
    except (TypeError, ValueError, OverflowError):
        return False


def _failure(code: str, **evidence: Any) -> dict[str, Any]:
    return {"success": False, "available": False, "complete": False, "error_code": code,
            "drawing_kind": _KIND, "coordinate_space": "WORLD", "coordinate_frame": _FRAME,
            "coordinate_unit": "mm", "views": [], **evidence}


def _metadata(mesh: Mapping[str, Any], validation: Mapping[str, Any]) -> dict[str, Any]:
    native = mesh.get("source_geometry")
    native = native if isinstance(native, Mapping) else {}
    return {
        "drawing_kind": _KIND, "coordinate_space": "WORLD", "coordinate_frame": _FRAME,
        "coordinate_unit": "mm", "geometry_sha256": mesh["geometry_sha256"],
        "source": {"refno": deepcopy(mesh.get("refno")), "revision": deepcopy(mesh.get("revision"))},
        "native_tessellation": False, "cuts_applied": True, "csg_evaluated": True,
        "parameter_boolean_evaluated": True, "native_cut_application_verified": False,
        "native_csg_evaluated": False, "material_boundary_validated": True,
        "closed_manifold": True, "winding_consistent": True, "boolean_union_applied": False,
        "hole_semantics_inferred": False,
        "engine": deepcopy(dict(mesh["engine"])),
        "material_boundary_validation": deepcopy(dict(mesh["material_boundary_validation"])),
        "validation": deepcopy(dict(validation)),
        "native_cut_scope": deepcopy(native.get("native_cut_scope")),
        "scope": "Orthographic projection of the verified owner-member parameter difference; native negative application, final native CSG and native tessellation are unverified.",
        "footer": _FOOTER,
    }


def _validate_primitive_csg_material_boundary(mesh: Any) -> dict[str, Any]:
    """Recheck one complete Mesh64 material receipt without projecting it.

    This private boundary is shared with material assembly drawing. Inward
    shells are accepted only when the geometry, original validation and
    producing engine/material evidence all agree.
    """
    if not isinstance(mesh, Mapping):
        return _failure("successful_material_csg_mesh_required")
    if (any(mesh.get(flag) is not True for flag in (
            "success", "available", "complete", "material_boundary_validated", "closed_manifold",
            "cuts_applied", "csg_evaluated"))
            or mesh.get("native_tessellation") is not False
            or mesh.get("native_cut_application_verified") is not False):
        return _failure("successful_material_csg_mesh_required")
    if (mesh.get("coordinate_space") != "WORLD" or mesh.get("coordinate_unit") != "mm"
            or mesh.get("coordinate_frame") != _FRAME):
        return _failure("csg_world_millimetres_required")
    engine, material = mesh.get("engine"), mesh.get("material_boundary_validation")
    if (not isinstance(engine, Mapping)
            or any(engine.get(name) != value for name, value in (
                ("name", "manifold3d"), ("mesh_api", "Mesh64"), ("numeric_precision", "float64"),
                ("operation", "Subtract"), ("status", "NoError")))
            or not isinstance(engine.get("version"), str) or not engine["version"].strip()):
        return _failure("csg_engine_provenance_required")
    if (not isinstance(material, Mapping)
            or any(material.get(flag) is not True for flag in ("validated", "closed_manifold", "winding_consistent"))
            or material.get("provenance") != _PROVENANCE or material.get("engine_status") != "NoError"
            or material.get("allowed_findings") != _ALLOWED_FINDINGS
            or not isinstance(material.get("cavity_shells"), list)
            or any(type(index) is not int or index < 0 for index in material["cavity_shells"])):
        return _failure("csg_material_provenance_required")
    vertices, triangles = mesh.get("vertices"), mesh.get("triangles")
    if not isinstance(vertices, (list, tuple)) or not isinstance(triangles, (list, tuple)):
        return _failure("csg_geometry_arrays_required")
    if len(vertices) > MAX_VERTICES or len(triangles) > MAX_TRIANGLES:
        return _failure("mesh_limit_exceeded")
    digest = mesh.get("geometry_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return _failure("csg_geometry_hash_required")
    try:
        observed_digest = csg_geometry_sha256(vertices, triangles)
    except (TypeError, ValueError, OverflowError):
        return _failure("csg_geometry_hash_unavailable")
    if observed_digest != digest:
        return _failure("csg_geometry_hash_mismatch")
    empty = mesh.get("empty", False)
    if type(empty) is not bool:
        return _failure("invalid_csg_empty_state")
    if empty:
        validation = mesh.get("validation")
        checks = validation.get("checks") if isinstance(validation, Mapping) else None
        if (vertices or triangles or engine.get("empty") is not True or material.get("empty") is not True
                or material.get("cavity_shells") != [] or not isinstance(validation, Mapping)
                or any(validation.get(flag) is not True for flag in ("success", "available", "complete", "valid", "empty"))
                or validation.get("components") != [] or validation.get("findings") != []
                or not isinstance(checks, Mapping) or checks.get("closed_manifold") is not True
                or checks.get("winding_consistent") is not True or checks.get("component_count") != 0):
            return _failure("csg_empty_material_validation_required")
        return {"success": True, "available": True, "complete": True, "empty": True,
                "validation": validation}
    if not vertices or not triangles or engine.get("empty") is True or material.get("empty") is True:
        return _failure("invalid_csg_empty_state")
    validation = validate_triangle_mesh(vertices, triangles)
    checks, findings, components = validation.get("checks"), validation.get("findings"), validation.get("components")
    if (any(validation.get(flag) is not True for flag in ("success", "available", "complete"))
            or validation.get("findings_truncated") is not False or not isinstance(checks, Mapping)
            or any(checks.get(flag) is not True for flag in _CHECKS)
            or not isinstance(findings, list) or any(
                not isinstance(finding, Mapping) or finding.get("code") not in _ALLOWED_FINDINGS for finding in findings)
            or not isinstance(components, list) or not components):
        return _failure("closed_material_csg_boundary_required", validation=validation)
    inward = []
    volume = []
    for component in components:
        signed = component.get("signed_volume")
        if (component.get("closed") is not True or component.get("winding_consistent") is not True
                or component.get("volume_available") is not True or isinstance(signed, bool)
                or not isinstance(signed, (int, float)) or not math.isfinite(signed) or signed == 0
                or component.get("orientation") != ("outward" if signed > 0 else "inward")):
            return _failure("closed_material_csg_boundary_required", validation=validation)
        volume.append(signed)
        if signed < 0:
            inward.append(component["component"])
    try:
        total_volume = math.fsum(volume)
    except (ValueError, OverflowError):
        return _failure("csg_material_validation_mismatch", validation=validation)
    if (total_volume <= 0 or material.get("cavity_shells") != inward
            or [finding.get("component") for finding in findings] != inward):
        return _failure("csg_material_validation_mismatch", validation=validation)
    if mesh.get("validation") != validation:
        return _failure("csg_source_validation_mismatch", validation=validation)
    return {"success": True, "available": True, "complete": True, "empty": False,
            "validation": validation}


def generate_primitive_csg_drawing(
    mesh: Mapping[str, Any],
    *,
    views: Sequence[str] = ("FRONT", "TOP", "SIDE"),
    feature_angle_degrees: float = 30.0,
    hidden_line_removal: bool = True,
    show_hidden_lines: bool = False,
) -> dict[str, Any]:
    """Project a complete, hash-matched Mesh64 parameter difference.

    The geometry is independently checked for closed manifold topology and
    consistent winding. Only inward cavity-component findings admitted by the
    producing Boolean core are allowed. An empty material result is successful
    and returns no views or vector image. All failures contain empty views.
    """
    if (not isinstance(views, (list, tuple)) or not 1 <= len(views) <= 3
            or any(not isinstance(name, str) or name not in {"FRONT", "TOP", "SIDE"} for name in views)):
        return _failure("unsupported_orthographic_views")
    if len(set(views)) != len(views):
        return _failure("duplicate_orthographic_view")
    if (isinstance(feature_angle_degrees, bool) or not isinstance(feature_angle_degrees, (int, float))):
        return _failure("invalid_feature_angle")
    try:
        threshold = float(feature_angle_degrees)
    except (OverflowError, ValueError):
        return _failure("invalid_feature_angle")
    if not math.isfinite(threshold) or not 0 <= threshold <= 180:
        return _failure("invalid_feature_angle")
    if type(hidden_line_removal) is not bool:
        return _failure("invalid_hidden_line_removal")
    if type(show_hidden_lines) is not bool:
        return _failure("invalid_show_hidden_lines")
    if show_hidden_lines and not hidden_line_removal:
        return _failure("hidden_line_display_requires_removal")
    receipt = _validate_primitive_csg_material_boundary(mesh)
    if receipt.get("success") is not True:
        return receipt
    validation = receipt["validation"]
    if receipt["empty"]:
        return {"success": True, "available": True, "complete": True, "empty": True,
                "views": [], "segment_count": 0, "hidden_line_removal": hidden_line_removal,
                "show_hidden_lines": show_hidden_lines, **_metadata(mesh, validation)}
    vertices, triangles = mesh["vertices"], mesh["triangles"]
    parent = _analytic_parent_source(mesh)
    projection_bounds = None
    envelope_views: list[str] = []
    if parent is not None:
        candidates = {}
        for name in views:
            horizontal, vertical, _ = _VIEWS[name]
            projected = _analytic_parent_projection_bounds(parent, horizontal, vertical)
            if projected is not None and _parent_envelope_supported(mesh, parent, name, projected):
                candidates[name] = projected
                envelope_views.append(name)
        projection_bounds = candidates or None
    try:
        drawing = _generate_orthographic_drawing_from_validated_mesh(
            vertices, triangles, views=views, feature_angle_degrees=threshold,
            coordinate_frame=_FRAME, coordinate_unit="mm", hidden_line_removal=hidden_line_removal,
            show_hidden_lines=show_hidden_lines, _material_boundary_prevalidated=True,
            _projection_bounds=projection_bounds,
        )
        if drawing.get("success") is not True:
            return drawing
        drawing.update(empty=False, **_metadata(mesh, validation))
        curved_sources = _verified_curved_sources(mesh)
        curved_source_verified = bool(curved_sources)
        for view in drawing.get("views", []):
            curves = _fitted_projected_circles(view, mesh_vertices=vertices, curved_sources=curved_sources)
            curves.extend(_fitted_dish_silhouette_arcs(
                view, mesh_vertices=vertices, curved_sources=curved_sources))
            if curves:
                view["curve_primitives"] = curves
                view["mesh_edge_bounds"] = deepcopy(view["bounds"])
                if any(curve["type"] == "arc" for curve in curves):
                    # An analytic arc can reach beyond the extremal tessellation vertex.
                    for curve in _validated_curve_primitives(view):
                        if curve["type"] == "arc":
                            for point in _arc_extrema_points(curve):
                                for axis, value in enumerate(point):
                                    bounds = view["bounds"]
                                    bounds["min"][axis] = min(bounds["min"][axis], value)
                                    bounds["max"][axis] = max(bounds["max"][axis], value)
        drawing["curve_refinement"] = {
            "mode": "source_verified_projected_curve_fit",
            "curve_count": sum(len(view.get("curve_primitives", ())) for view in drawing.get("views", [])),
            "curve_types": sorted({curve["type"] for view in drawing.get("views", [])
                                    for curve in view.get("curve_primitives", ())}),
            "source_edge_count": sum(curve["edge_count"] for view in drawing.get("views", [])
                                      for curve in view.get("curve_primitives", ())),
            "max_chord_error_mm": max((curve.get("fit_max_chord_error_mm", 0.0)
                                        for view in drawing.get("views", [])
                                        for curve in view.get("curve_primitives", ())), default=0.0),
            "scope": "Presentation-only analytic circles or ellipses from complete projected Mesh64 cycles matched to digest-verified CYLI/CONE/DISH sections, plus uniquely verified side-on DISH spherical silhouette sub-arcs; source edges and geometry hash are retained.",
            "analytic_source_required": True,
            "analytic_source_verified": curved_source_verified,
        }
        # Source edges remain in the structured drawing for provenance.
        drawing["rendered_entity_count"] = (drawing["segment_count"]
                                             - drawing["curve_refinement"]["source_edge_count"]
                                             + drawing["curve_refinement"]["curve_count"])
        drawing["curve_representation"] = "fitted_projected_curves_with_source_edges_retained"
        footer = _FOOTER
        if projection_bounds is not None:
            drawing["source_parameter_envelope_applied"] = True
            drawing["source_parameter_envelope_views"] = envelope_views
            drawing["projection_bounds_scope"] = _PARENT_ENVELOPE_SCOPE
            for view in drawing["views"]:
                view["bounds_scope"] = _PARENT_ENVELOPE_SCOPE
            footer += "; " + _PARENT_ENVELOPE_FOOTER
        drawing["svg"] = _make_svg(drawing["views"], hidden_line_removal=hidden_line_removal,
                                   show_hidden_lines=show_hidden_lines, footer=footer)
        return drawing
    except (TypeError, ValueError, OverflowError) as exc:
        return _failure("csg_projection_failed", projection_error=str(exc))
