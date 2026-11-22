"""Bounded measurements for caller-supplied indexed triangle meshes."""

from __future__ import annotations

import math
from typing import Any

from .triangle_mesh_validation import MAX_TRIANGLES, MAX_VERTICES, validate_triangle_mesh


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) else None


def measure_triangle_mesh(vertices: Any, triangles: Any) -> dict[str, Any]:
    """Return bounds and surface area when every supplied face is measurable."""
    validation = validate_triangle_mesh(vertices, triangles)
    counts = validation.get("counts", {})
    result: dict[str, Any] = {
        "success": validation.get("success") is True,
        "available": False,
        "complete": validation.get("complete") is True,
        "valid": validation.get("valid") is True,
        "coordinate_unit": "caller_supplied; not inferred",
        "area_unit": "coordinate_unit^2; not inferred",
        "bounds": None,
        "surface_area": None,
        "surface_area_definition": "sum of Euclidean areas of supplied triangle faces; duplicate faces count separately",
        "validation": validation,
    }
    if (not isinstance(vertices, (list, tuple)) or not isinstance(triangles, (list, tuple)) or
            len(vertices) > MAX_VERTICES or len(triangles) > MAX_TRIANGLES or
            not vertices or not triangles):
        return result
    checks = validation.get("checks", {})
    if (checks.get("finite_coordinates") is not True or checks.get("valid_indices") is not True or
            checks.get("nondegenerate_triangles") is not True):
        return result

    points: list[list[float]] = []
    for vertex in vertices:
        if not isinstance(vertex, (list, tuple)) or len(vertex) != 3:
            return result
        point = [_number(value) for value in vertex]
        if any(value is None for value in point):
            return result
        points.append([float(value) for value in point])

    minimum = [min(point[axis] for point in points) for axis in range(3)]
    maximum = [max(point[axis] for point in points) for axis in range(3)]
    extents = [maximum[axis] - minimum[axis] for axis in range(3)]
    scale = math.hypot(*extents)
    if not math.isfinite(scale) or scale == 0.0:
        return result
    origin = points[0]
    normalized = [[(point[axis] - origin[axis]) / scale for axis in range(3)] for point in points]
    if not all(math.isfinite(value) for point in normalized for value in point):
        return result

    normalized_area = 0.0
    for face in triangles:
        if not isinstance(face, (list, tuple)) or len(face) != 3:
            return result
        if any(isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= len(points)
               for index in face):
            return result
        a, b, c = (normalized[index] for index in face)
        u = [b[axis] - a[axis] for axis in range(3)]
        v = [c[axis] - a[axis] for axis in range(3)]
        cross = [u[1] * v[2] - u[2] * v[1],
                 u[2] * v[0] - u[0] * v[2],
                 u[0] * v[1] - u[1] * v[0]]
        normalized_area += math.hypot(*cross) * 0.5

    mantissa, exponent = math.frexp(scale)
    try:
        surface_area = math.ldexp(normalized_area * mantissa * mantissa, exponent * 2)
    except OverflowError:
        surface_area = math.inf
    if not math.isfinite(surface_area) or surface_area == 0.0:
        return result

    result.update({
        "available": True,
        "bounds": {"min": minimum, "max": maximum},
        "surface_area": surface_area,
    })
    return result
