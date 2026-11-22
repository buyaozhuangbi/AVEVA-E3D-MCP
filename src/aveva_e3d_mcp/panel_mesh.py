"""Fail-closed local prism meshes derived from native PANE geometry."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from typing import Any


_MESH_SOURCE = "derived_from_e3d_panel_parameters"
_COORDINATE_FRAME = "PANE local coordinates; millimetres"
_WORLD_COORDINATE_FRAME = "WORLD coordinates; millimetres"
_COORDINATE_TOLERANCE = 1e-5
_WORLD_FRAME_TOLERANCE = 1e-5
_WORLD_POSITION_TOLERANCE_MM = 1e-4
_MAX_VERTICES = 2048
_RELATIVE_EAR_TOLERANCE = 1e-12


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _cross2(a: list[float], b: list[float], c: list[float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _point_in_triangle(point: list[float], a: list[float], b: list[float], c: list[float],
                       tolerance: float = _RELATIVE_EAR_TOLERANCE) -> bool:
    return (_cross2(a, b, point) >= -tolerance and
            _cross2(b, c, point) >= -tolerance and
            _cross2(c, a, point) >= -tolerance)


def _triangulate(points: list[list[float]]) -> list[tuple[int, int, int]]:
    """Ear clip a simple CCW outline, retaining the original point indices."""
    # Ear tests use normalized coordinates because cross products have area
    # units; comparing them to the millimetre position tolerance rejects valid
    # small profiles and makes the result depend on the panel's scale.
    origin = points[0]
    relative = [[point[axis] - origin[axis] for axis in range(2)] for point in points]
    scale = max((max(abs(value) for value in point) for point in relative), default=0.0)
    if not math.isfinite(scale) or scale == 0.0:
        raise ValueError("outline has no finite extent")
    normalized = [[value / scale for value in point] for point in relative]
    if not all(math.isfinite(value) for point in normalized for value in point):
        raise ValueError("outline normalization is non-finite")
    remaining = list(range(len(points)))
    triangles: list[tuple[int, int, int]] = []
    while len(remaining) > 3:
        ear_found = False
        for cursor, current in enumerate(remaining):
            previous = remaining[cursor - 1]
            following = remaining[(cursor + 1) % len(remaining)]
            if _cross2(normalized[previous], normalized[current], normalized[following]) <= _RELATIVE_EAR_TOLERANCE:
                continue
            if any(_point_in_triangle(normalized[index], normalized[previous], normalized[current], normalized[following])
                   for index in remaining if index not in {previous, current, following}):
                continue
            triangles.append((previous, current, following))
            del remaining[cursor]
            ear_found = True
            break
        if not ear_found:
            raise ValueError("ear clipping could not find a nondegenerate ear")
    if len(remaining) != 3 or _cross2(*(normalized[index] for index in remaining)) <= _RELATIVE_EAR_TOLERANCE:
        raise ValueError("final ear is degenerate")
    triangles.append(tuple(remaining))
    return triangles


def _normal(a: list[float], b: list[float], c: list[float]) -> list[float]:
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    scale_u, scale_v = max(map(abs, u)), max(map(abs, v))
    if scale_u == 0.0 or scale_v == 0.0:
        raise ValueError("degenerate triangle")
    u = [value / scale_u for value in u]
    v = [value / scale_v for value in v]
    cross = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
    length = math.hypot(*cross)
    if not math.isfinite(length) or length == 0.0:
        raise ValueError("degenerate triangle")
    return [component / length for component in cross]


def _closed(triangles: list[list[int]]) -> bool:
    counts: Counter[tuple[int, int]] = Counter()
    directions: Counter[tuple[int, int]] = Counter()
    for tri in triangles:
        if len(set(tri)) != 3:
            return False
        for left, right in zip(tri, tri[1:] + tri[:1]):
            key = (min(left, right), max(left, right))
            counts[key] += 1
            directions[key] += 1 if left < right else -1
    return bool(counts) and all(value == 2 for value in counts.values()) and all(value == 0 for value in directions.values())


def _failure(native: Mapping[str, Any], errors: list[str], thickness: float | None = None,
             justification: str | None = None, coordinate_space: str = "panel_local") -> dict[str, Any]:
    element = native.get("element")
    world = coordinate_space == "WORLD"
    return {
        "success": False, "available": False, "complete": False, "errors": errors,
        "refno": element.get("refno") if isinstance(element, Mapping) else None,
        "revision": native.get("revision"), "coordinate_frame": _WORLD_COORDINATE_FRAME if world else _COORDINATE_FRAME,
        "coordinate_space": coordinate_space,
        "thickness_mm": thickness, "native_justification": justification,
        "parameter_convention": "BOTT=[0,h], CENT=[-h/2,h/2], TOP=[-h,0] in PANE local Z",
        "mesh_source": _MESH_SOURCE, "native_tessellation": False, "cuts_applied": False,
        "closed_manifold": False, "vertices": [], "triangles": [], "triangle_normals": [],
        "counts": {"vertices": 0, "triangles": 0},
    }


def _fit_world_frame(loop: Mapping[str, Any], points: list[list[float]]) -> tuple[list[float], list[list[float]]] | None:
    vertices = loop.get("vertices")
    if not isinstance(vertices, (list, tuple)) or len(vertices) != len(points):
        return None
    if loop.get("world_positions_complete") is False:
        return None
    world_points: list[list[float]] = []
    for vertex in vertices:
        if not isinstance(vertex, Mapping) or vertex.get("world_position_complete") is not True:
            return None
        raw = vertex.get("position_world_mm")
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            return None
        coordinates = [_number(value) for value in raw]
        if any(value is None for value in coordinates):
            return None
        world_points.append([float(value) for value in coordinates])

    # Use the strongest pair of local directions from one anchor to fit the plane frame.
    anchor = points[0]
    candidates = [(index, points[index][0] - anchor[0], points[index][1] - anchor[1])
                  for index in range(1, len(points))]
    first_index, first_x, first_y = max(candidates, key=lambda item: math.hypot(item[1], item[2]))
    scale = max(abs(first_x), abs(first_y), *(abs(value) for _, x, y in candidates for value in (x, y)))
    if not math.isfinite(scale) or scale == 0.0:
        return None
    ux, uy = first_x / scale, first_y / scale
    second = max(((index, x, y, abs(ux * (y / scale) - (x / scale) * uy))
                  for index, x, y in candidates if index != first_index), key=lambda item: item[3], default=None)
    if second is None or not math.isfinite(second[3]) or second[3] <= 1e-12:
        return None
    second_index, second_x, second_y, _ = second
    vx, vy = second_x / scale, second_y / scale
    determinant = ux * vy - vx * uy
    if not math.isfinite(determinant) or abs(determinant) <= 1e-12:
        return None

    world_origin = world_points[0]
    first_world = [(world_points[first_index][axis] - world_origin[axis]) / scale for axis in range(3)]
    second_world = [(world_points[second_index][axis] - world_origin[axis]) / scale for axis in range(3)]
    if not all(math.isfinite(value) for value in (*first_world, *second_world)):
        return None
    x_axis = [(first_world[axis] * vy - second_world[axis] * uy) / determinant for axis in range(3)]
    y_axis = [(second_world[axis] * ux - first_world[axis] * vx) / determinant for axis in range(3)]
    cross = [x_axis[1] * y_axis[2] - x_axis[2] * y_axis[1],
             x_axis[2] * y_axis[0] - x_axis[0] * y_axis[2],
             x_axis[0] * y_axis[1] - x_axis[1] * y_axis[0]]
    normal_length = math.hypot(*cross)
    if not math.isfinite(normal_length) or normal_length == 0.0:
        return None
    z_axis = [value / normal_length for value in cross]
    axes = [x_axis, y_axis, z_axis]
    lengths = [math.hypot(*axis) for axis in axes]
    dots = [math.fsum(left[axis] * right[axis] for axis in range(3))
            for left, right in ((x_axis, y_axis), (x_axis, z_axis), (y_axis, z_axis))]
    if (not all(math.isfinite(value) for value in (*lengths, *dots)) or
            any(abs(length - 1.0) > _WORLD_FRAME_TOLERANCE for length in lengths) or
            any(abs(dot) > _WORLD_FRAME_TOLERANCE for dot in dots)):
        return None

    translation = [world_origin[axis] - anchor[0] * x_axis[axis] - anchor[1] * y_axis[axis]
                   for axis in range(3)]
    position_scale = max((math.dist(world_origin, point) for point in world_points), default=0.0)
    tolerance = _WORLD_POSITION_TOLERANCE_MM + position_scale * 1e-8
    for local, observed in zip(points, world_points):
        expected = [math.fsum((translation[axis], local[0] * x_axis[axis], local[1] * y_axis[axis]))
                    for axis in range(3)]
        if not all(math.isfinite(value) for value in expected) or math.dist(expected, observed) > tolerance:
            return None
    if not all(math.isfinite(value) for value in translation):
        return None
    return translation, axes


def _world_frame(loop: Mapping[str, Any], points: list[list[float]]) -> tuple[list[float], list[list[float]]] | None:
    try:
        return _fit_world_frame(loop, points)
    except (ArithmeticError, OverflowError, ValueError):
        return None


def _to_world(vertices: list[list[float]], normals: list[list[float]], translation: list[float],
              axes: list[list[float]]) -> bool:
    for vertex in vertices:
        local = vertex[:]
        for coordinate in range(3):
            vertex[coordinate] = math.fsum((translation[coordinate], *(local[axis] * axes[axis][coordinate]
                                                                          for axis in range(3))))
    for normal in normals:
        local = normal[:]
        rotated = [math.fsum(local[axis] * axes[axis][coordinate] for axis in range(3)) for coordinate in range(3)]
        length = math.hypot(*rotated)
        if not math.isfinite(length) or length == 0.0:
            return False
        normal[:] = [value / length for value in rotated]
    return (all(math.isfinite(value) for vertex in vertices for value in vertex) and
            all(math.isfinite(value) for normal in normals for value in normal))


def derive_panel_mesh(native_geometry: Mapping[str, Any], coordinate_space: str = "panel_local") -> dict[str, Any]:
    """Derive one closed triangular prism from a complete simple native PANE loop."""
    if not isinstance(coordinate_space, str) or coordinate_space not in {"panel_local", "WORLD"}:
        coordinate_space = "panel_local"
        coordinate_error = ["unsupported_coordinate_space"]
    else:
        coordinate_error = []
    if not isinstance(native_geometry, Mapping):
        return _failure({}, ["invalid_native_geometry", *coordinate_error], coordinate_space=coordinate_space)
    loops = native_geometry.get("loops")
    loop = loops[0] if isinstance(loops, (list, tuple)) and len(loops) == 1 and isinstance(loops[0], Mapping) else None
    thickness = _number(loop.get("thickness_mm")) if loop else None
    justification = loop.get("native_justification") if loop else None
    errors: list[str] = list(coordinate_error)
    if native_geometry.get("success") is not True:
        errors.append("native_read_not_successful")
    if native_geometry.get("complete") is not True:
        errors.append("native_read_incomplete")
    if native_geometry.get("topology_valid") is not True:
        errors.append("native_topology_not_valid")
    if native_geometry.get("loop_count") != 1 or loop is None:
        errors.append("single_loop_required")
    if native_geometry.get("read_errors") != {}:
        errors.append("native_read_errors_present_or_unreported")
    if native_geometry.get("unsupported_features") != []:
        errors.append("native_unsupported_features_present_or_unreported")
    if loop is not None:
        if loop.get("complete") is not True:
            errors.append("native_loop_incomplete")
        if loop.get("polygon_valid") is not True:
            errors.append("native_polygon_not_valid")
        if loop.get("read_errors") != {}:
            errors.append("loop_read_errors_present_or_unreported")
        if loop.get("unsupported_features") != []:
            errors.append("loop_unsupported_features_present_or_unreported")
        if loop.get("blind") is not False:
            errors.append("blind_loop_rejected_or_unreported")
        if thickness is None or thickness <= 0:
            errors.append("invalid_or_nonpositive_thickness")
        if justification not in {"BOTT", "BOTTOM", "CENT", "TOP"}:
            errors.append("unsupported_or_unreported_justification")
        if loop.get("planar_parallel_xy") is not True or loop.get("coplanar_in_local_xy") is not True:
            errors.append("vertices_not_coplanar_in_panel_xy")
        vertices = loop.get("vertices")
        if not isinstance(vertices, (list, tuple)) or not 3 <= len(vertices) <= _MAX_VERTICES:
            errors.append("invalid_vertex_count")
            points = []
        else:
            points = []
            for vertex in vertices:
                position = vertex.get("position") if isinstance(vertex, Mapping) else None
                if not isinstance(position, (list, tuple)) or len(position) != 3:
                    points = []
                    errors.append("invalid_vertex_position")
                    break
                coordinates = [_number(value) for value in position]
                if any(value is None for value in coordinates) or abs(float(coordinates[2])) > _COORDINATE_TOLERANCE:
                    points = []
                    errors.append("nonfinite_or_nonplanar_vertex")
                    break
                radius = _number(vertex.get("fillet_radius_mm"))
                if radius is None or radius != 0.0:
                    errors.append("fillet_or_unreported_radius")
                    break
                if any(vertex.get(key) not in {None, "unset", "0degree", "0deg"} for key in ("GTYP",)):
                    errors.append("unsupported_vertex_geometry_type")
                    break
                if any(vertex.get(key) not in {None, "unset", "0degree", "0deg"} for key in ("CTYA", "BANG")):
                    errors.append("unsupported_vertex_curve_or_connection")
                    break
                points.append([float(coordinates[0]), float(coordinates[1])])
    else:
        points = []
    if not errors and points:
        try:
            signed_area2 = math.fsum(points[i][0] * points[(i + 1) % len(points)][1] -
                                     points[(i + 1) % len(points)][0] * points[i][1] for i in range(len(points)))
        except (ArithmeticError, OverflowError, ValueError):
            signed_area2 = None
        if signed_area2 is None or not math.isfinite(signed_area2) or signed_area2 <= 0:
            errors.append("counterclockwise_outline_required")
    if errors:
        return _failure(native_geometry, errors, thickness, justification if isinstance(justification, str) else None,
                        coordinate_space)

    pane_thickness = _number(native_geometry.get("thickness_mm"))
    pane_justification = native_geometry.get("native_justification")
    if pane_thickness is None or pane_thickness != thickness:
        errors.append("pane_loop_thickness_mismatch_or_unreported")
    if pane_justification not in {"unset", "BOTT", "BOTTOM", "CENT", "TOP"}:
        errors.append("unsupported_pane_justification")
    if pane_justification != justification and not (pane_justification == "BOTT" and justification == "BOTTOM"):
        errors.append("pane_loop_justification_mismatch")
    if errors:
        return _failure(native_geometry, errors, thickness, justification, coordinate_space)

    z0, z1 = {
        "BOTT": (0.0, thickness), "BOTTOM": (0.0, thickness),
        "CENT": (-thickness / 2.0, thickness / 2.0), "TOP": (-thickness, 0.0),
    }[justification]
    count = len(points)
    vertices3 = [[x, y, z0] for x, y in points] + [[x, y, z1] for x, y in points]
    triangles: list[list[int]] = []
    try:
        cap = _triangulate(points)
        triangles.extend([list(reversed(tri)) for tri in cap])
        triangles.extend([[index + count for index in tri] for tri in cap])
        for index in range(count):
            following = (index + 1) % count
            triangles.extend(([index, following, following + count],
                              [index, following + count, index + count]))
        normals = [_normal(*(vertices3[index] for index in tri)) for tri in triangles]
    except (ArithmeticError, OverflowError, ValueError):
        return _failure(native_geometry, ["mesh_derivation_failed"], thickness, justification, coordinate_space)
    if not all(math.isfinite(value) for point in vertices3 for value in point):
        return _failure(native_geometry, ["mesh_derivation_non_finite"], thickness, justification, coordinate_space)
    closed = _closed(triangles)
    if not closed:
        return _failure(native_geometry, ["indexed_edge_incidence_not_closed"], thickness, justification, coordinate_space)
    world_transform = None
    if coordinate_space == "WORLD":
        if native_geometry.get("world_positions_requested") is not True or native_geometry.get("world_positions_complete") is not True:
            return _failure(native_geometry, ["complete_native_world_positions_required"], thickness, justification,
                            coordinate_space)
        world_transform = _world_frame(loop, points)
        if world_transform is None:
            return _failure(native_geometry, ["world_positions_do_not_define_valid_panel_frame"], thickness,
                            justification, coordinate_space)
        if not _to_world(vertices3, normals, *world_transform):
            return _failure(native_geometry, ["world_mesh_transform_non_finite"], thickness, justification,
                            coordinate_space)
    element = native_geometry.get("element")
    return {
        "success": True, "available": True, "complete": True, "errors": [],
        "refno": element.get("refno") if isinstance(element, Mapping) else None,
        "revision": native_geometry.get("revision"),
        "coordinate_frame": _WORLD_COORDINATE_FRAME if coordinate_space == "WORLD" else _COORDINATE_FRAME,
        "coordinate_space": coordinate_space,
        "thickness_mm": thickness, "native_justification": justification,
        "parameter_convention": "BOTT=[0,h], CENT=[-h/2,h/2], TOP=[-h,0] in PANE local Z",
        "surface_approximation": "planar caps and straight side facets",
        "mesh_source": _MESH_SOURCE, "native_tessellation": False, "cuts_applied": False,
        "closed_manifold": True, "vertices": vertices3, "triangles": triangles,
        "triangle_normals": normals,
        "world_frame": ({"origin": world_transform[0], "x_axis": world_transform[1][0],
                         "y_axis": world_transform[1][1], "z_axis": world_transform[1][2]}
                        if world_transform is not None else None),
        "counts": {"vertices": len(vertices3), "triangles": len(triangles)},
    }
