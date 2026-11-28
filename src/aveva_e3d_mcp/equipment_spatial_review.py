"""Conservative, advisory WORLD AABB checks for planned equipment transforms."""
from __future__ import annotations

import math
from typing import Any

from .primitive_mesh import derive_primitive_mesh

SPATIAL_REVIEW_SCOPE = ("Advisory WORLD bounds for explicitly planned transforms of standalone positive BOX/CYLI targets. "
    "BOX/BOX AABB overlaps are refined with oriented-box separating-axis checks; CYLI and mixed-shape comparisons "
    "remain AABB candidates. A candidate is not a native clash, clearance, or engineering verdict. Unsupported "
    "shapes or incomplete reads remain unknown.")


def _triple(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    if any(type(item) not in (int, float) for item in value):
        return None
    try:
        result = [float(item) for item in value]
    except (OverflowError, ValueError):
        return None
    return result if all(math.isfinite(item) for item in result) else None


def _basis(raw: Any) -> list[list[float]] | None:
    if not isinstance(raw, dict):
        return None
    axes = [_triple(raw.get(key)) for key in ("x_axis", "y_axis", "z_axis")]
    if any(axis is None for axis in axes):
        return None
    x, y, z = axes
    if any(abs(value) > 1.000001 for axis in axes for value in axis):
        return None
    if any(abs(math.hypot(*axis) - 1.0) > 1e-6 for axis in axes):
        return None
    if any(abs(math.fsum(a[i] * b[i] for i in range(3))) > 1e-6 for a, b in ((x, y), (x, z), (y, z))):
        return None
    cross = (x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0])
    if any(abs(cross[i] - z[i]) > 1e-6 for i in range(3)):
        return None
    return axes


def _same_vector(first: list[float], second: list[float], tolerance: float) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(first, second))


def primitive_world_aabb(geometry: Any, *, expected_refno: str, expected_type: str,
                         current_position: Any, current_orientation: Any,
                         planned_position: Any, planned_orientation: Any) -> dict[str, Any]:
    """Derive conservative bounds only for a verified standalone positive BOX/CYLI."""
    result = {"available": False, "aabb_world_mm": None, "reason": "geometry_unverified"}
    if not isinstance(geometry, dict):
        return result
    kind = str(expected_type).upper()
    if kind not in {"BOX", "CYLI"}:
        result["reason"] = "unsupported_shape"
        return result
    element = geometry.get("element")
    if (geometry.get("complete") is not True or geometry.get("success") is not True or
            geometry.get("read_errors") != {} or geometry.get("unit") != "mm" or
            geometry.get("negative") is not False or geometry.get("negative_children") != [] or
            not isinstance(element, dict) or element.get("refno") != expected_refno or
            str(element.get("type", "")).upper() != kind or geometry.get("shape") != kind):
        result["reason"] = "geometry_identity_or_completeness_unverified"
        return result

    local_mesh = derive_primitive_mesh(geometry, coordinate_space="primitive_local")
    if local_mesh.get("success") is not True or local_mesh.get("complete") is not True:
        result["reason"] = "positive_primitive_geometry_unavailable"
        return result
    dimensions = local_mesh.get("dimensions")
    if not isinstance(dimensions, dict):
        result["reason"] = "dimensions_unverified"
        return result

    current_origin = _triple(current_position)
    current_basis = _basis(current_orientation)
    world = geometry.get("frame", {}).get("world") if isinstance(geometry.get("frame"), dict) else None
    geometry_origin = _triple(world.get("position")) if isinstance(world, dict) else None
    geometry_basis = _basis(world.get("orientation")) if isinstance(world, dict) else None
    if current_origin is None or current_basis is None or geometry_origin is None or geometry_basis is None:
        result["reason"] = "current_world_pose_unverified"
        return result
    position_tolerance = max(1e-6, 32 * math.ulp(max(1.0, *(abs(value) for value in current_origin))))
    if (not _same_vector(current_origin, geometry_origin, position_tolerance) or
            any(not _same_vector(a, b, 1e-6) for a, b in zip(current_basis, geometry_basis))):
        result["reason"] = "geometry_pose_changed_between_reads"
        return result

    origin = _triple(planned_position)
    basis = _basis(planned_orientation)
    if origin is None or basis is None:
        result["reason"] = "planned_world_pose_unverified"
        return result
    if kind == "BOX":
        half = [dimensions[key] / 2.0 for key in ("XLEN", "YLEN", "ZLEN")]
        extents = [math.fsum(abs(basis[axis][coordinate]) * half[axis] for axis in range(3))
                   for coordinate in range(3)]
    else:
        radius, half_height = dimensions["DIAM"] / 2.0, dimensions["HEIG"] / 2.0
        extents = [radius * math.hypot(basis[0][coordinate], basis[1][coordinate]) +
                   half_height * abs(basis[2][coordinate]) for coordinate in range(3)]
    low = [origin[i] - extents[i] for i in range(3)]
    high = [origin[i] + extents[i] for i in range(3)]
    if not all(math.isfinite(value) for value in (*low, *high)):
        result["reason"] = "world_bounds_non_finite"
        return result
    bounds: dict[str, Any] = {"available": True, "aabb_world_mm": {"min": low, "max": high}, "reason": None}
    if kind == "BOX":
        bounds["oriented_box_world_mm"] = {
            "center": origin,
            "axes": {"x_axis": basis[0], "y_axis": basis[1], "z_axis": basis[2]},
            "half_extents": half,
        }
    return bounds


def _oriented_box(value: Any) -> tuple[list[float], list[list[float]], list[float]] | None:
    if not isinstance(value, dict):
        return None
    center = _triple(value.get("center"))
    axes = _basis(value.get("axes"))
    half = _triple(value.get("half_extents"))
    if center is None or axes is None or half is None or any(extent < 0.0 for extent in half):
        return None
    return center, axes, half


def _box_separating_axis(first: tuple[list[float], list[list[float]], list[float]],
                         second: tuple[list[float], list[list[float]], list[float]]) -> dict[str, Any] | None:
    """Return a WORLD separating axis when two oriented boxes are disjoint."""
    center_a, axes_a, half_a = first
    center_b, axes_b, half_b = second
    delta = [center_b[index] - center_a[index] for index in range(3)]
    magnitude = max(1.0, *(abs(value) for value in (*center_a, *center_b, *half_a, *half_b)))
    tolerance = max(1e-6, 64 * math.ulp(magnitude))
    candidates: list[tuple[str, list[float]]] = []
    for index, axis in enumerate(axes_a):
        candidates.append((f"first.{('x', 'y', 'z')[index]}", axis))
    for index, axis in enumerate(axes_b):
        candidates.append((f"second.{('x', 'y', 'z')[index]}", axis))
    for first_index, first_axis in enumerate(axes_a):
        for second_index, second_axis in enumerate(axes_b):
            cross = [first_axis[1] * second_axis[2] - first_axis[2] * second_axis[1],
                     first_axis[2] * second_axis[0] - first_axis[0] * second_axis[2],
                     first_axis[0] * second_axis[1] - first_axis[1] * second_axis[0]]
            length = math.hypot(*cross)
            if length > 1e-12:
                candidates.append((f"first.{('x', 'y', 'z')[first_index]}_cross_second.{('x', 'y', 'z')[second_index]}",
                                   [value / length for value in cross]))
    for label, axis in candidates:
        distance = abs(math.fsum(delta[index] * axis[index] for index in range(3)))
        radius_a = math.fsum(half_a[index] * abs(math.fsum(axes_a[index][coordinate] * axis[coordinate]
                                                              for coordinate in range(3))) for index in range(3))
        radius_b = math.fsum(half_b[index] * abs(math.fsum(axes_b[index][coordinate] * axis[coordinate]
                                                              for coordinate in range(3))) for index in range(3))
        gap = distance - radius_a - radius_b
        if gap > tolerance:
            return {"axis": axis, "source": label, "projected_gap_mm": gap, "rounding_tolerance_mm": tolerance}
    return None


def compare_world_bounds(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Compare bounds, refining BOX/BOX AABB overlaps with an OBB SAT check."""
    result = compare_world_aabbs(first, second)
    if result.get("status") not in {"contact_candidate", "overlap_candidate"}:
        return result
    box_a = _oriented_box(first.get("oriented_box_world_mm"))
    box_b = _oriented_box(second.get("oriented_box_world_mm"))
    if box_a is None or box_b is None:
        return result
    result = {**result, "aabb_status": result["status"], "narrow_phase": "oriented_box_separating_axes"}
    separated = _box_separating_axis(box_a, box_b)
    if separated is None:
        result["oriented_box_status"] = "overlap_candidate"
    else:
        result.update(status="aabb_overlap_only", oriented_box_status="separate",
                      oriented_separation=separated)
    return result


def compare_world_aabbs(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Classify conservative axis-aligned bounding-box overlap or contact."""
    first_box, second_box = first.get("aabb_world_mm"), second.get("aabb_world_mm")
    if (first.get("available") is not True or second.get("available") is not True or
            not isinstance(first_box, dict) or not isinstance(second_box, dict)):
        return {"status": "unknown", "overlap_depth_mm": None}
    first_min, first_max = _triple(first_box.get("min")), _triple(first_box.get("max"))
    second_min, second_max = _triple(second_box.get("min")), _triple(second_box.get("max"))
    if any(value is None for value in (first_min, first_max, second_min, second_max)):
        return {"status": "unknown", "overlap_depth_mm": None}
    magnitude = max(1.0, *(abs(value) for bounds in (first_min, first_max, second_min, second_max)
                           for value in bounds))
    tolerance = max(1e-6, 32 * math.ulp(magnitude))
    depths = [min(first_max[i], second_max[i]) - max(first_min[i], second_min[i]) for i in range(3)]
    if not all(math.isfinite(depth) for depth in depths):
        return {"status": "unknown", "overlap_depth_mm": None}
    if any(depth < -tolerance for depth in depths):
        gaps = [max(first_min[i] - second_max[i], second_min[i] - first_max[i], 0.0) for i in range(3)]
        return {"status": "separate", "separation_mm": math.hypot(*gaps),
                "rounding_tolerance_mm": tolerance}
    if any(depth <= tolerance for depth in depths):
        return {"status": "contact_candidate", "overlap_depth_mm": max(0.0, min(depths)),
                "rounding_tolerance_mm": tolerance}
    return {"status": "overlap_candidate", "overlap_depth_mm": min(depths),
            "rounding_tolerance_mm": tolerance}
