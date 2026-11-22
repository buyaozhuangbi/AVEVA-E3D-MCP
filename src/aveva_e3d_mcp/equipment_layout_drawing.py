"""Bounded orthographic equipment-layout geometry from verified WORLD poses.

The layout is intentionally a pose drawing: it shows equipment origins and
local axes, while leaving dimensions and extents to a separate native source.
This avoids treating SDK ``LimitsBox`` coordinates as WORLD coordinates when
their frame has not been verified.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


_VIEWS: dict[str, tuple[int, int, int]] = {
    "FRONT": (0, 2, 1),
    "TOP": (0, 1, 2),
    "SIDE": (1, 2, 0),
}
_MAX_ROWS = 256
_MAX_SEGMENTS = 4096


def _vector(value: Any, field: str) -> tuple[float, float, float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        return None
    values: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        number = float(item)
        if not math.isfinite(number):
            return None
        values.append(number)
    return tuple(values)  # type: ignore[return-value]


def _spatial(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    measurements = row.get("measurements")
    if not isinstance(measurements, Mapping):
        return None
    field = measurements.get("spatial")
    if not isinstance(field, Mapping):
        return None
    result = field.get("result")
    return result if isinstance(result, Mapping) else None


def _point(spatial: Mapping[str, Any]) -> tuple[float, float, float] | None:
    position = spatial.get("position")
    if not isinstance(position, Mapping) or position.get("available") is not True:
        return None
    return _vector((position.get("x"), position.get("y"), position.get("z")), "position")


def _axes(spatial: Mapping[str, Any]) -> dict[str, tuple[float, float, float]] | None:
    orientation = spatial.get("orientation")
    if not isinstance(orientation, Mapping) or orientation.get("available") is not True:
        return None
    axes: dict[str, tuple[float, float, float]] = {}
    for name in ("x_axis", "y_axis", "z_axis"):
        axis = _vector(orientation.get(name), name)
        if axis is None:
            return None
        axes[name] = axis
    x_axis, y_axis, z_axis = (axes[name] for name in ("x_axis", "y_axis", "z_axis"))
    def dot(first: Sequence[float], second: Sequence[float]) -> float:
        return sum(left * right for left, right in zip(first, second))
    if any(abs(dot(axis, axis) - 1.0) > 1e-6 for axis in (x_axis, y_axis, z_axis)):
        return None
    if any(abs(dot(first, second)) > 1e-6
           for first, second in ((x_axis, y_axis), (y_axis, z_axis), (z_axis, x_axis))):
        return None
    cross_xy = (x_axis[1] * y_axis[2] - x_axis[2] * y_axis[1],
                x_axis[2] * y_axis[0] - x_axis[0] * y_axis[2],
                x_axis[0] * y_axis[1] - x_axis[1] * y_axis[0])
    if dot(cross_xy, z_axis) < 1.0 - 1e-6:
        return None
    return axes


def build_equipment_layout_drawing(rows: Sequence[Mapping[str, Any]], *, views: Sequence[str] = ("FRONT", "TOP", "SIDE"),
                                   axis_length_mm: float = 1000.0) -> dict[str, Any]:
    """Build pose linework and per-row evidence without inventing extents.

    Rows with an unavailable position are retained in ``equipment`` and are
    excluded from linework. Rows with position but no orientation still get a
    point marker; the missing orientation is explicit in the row evidence.
    """
    # An empty native batch is a valid failure observation. Preserve it as a
    # structured no-position result instead of raising before evidence can be
    # returned by the tool boundary.
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or len(rows) > _MAX_ROWS:
        raise ValueError("layout_rows_limit_exceeded")
    names = list(views)
    if not 1 <= len(names) <= 3 or len(set(names)) != len(names) or any(name not in _VIEWS for name in names):
        raise ValueError("invalid_layout_views")
    if isinstance(axis_length_mm, bool) or not isinstance(axis_length_mm, (int, float)):
        raise ValueError("invalid_axis_length_mm")
    axis_length = float(axis_length_mm)
    if not math.isfinite(axis_length) or axis_length <= 0 or axis_length > 1_000_000:
        raise ValueError("invalid_axis_length_mm")

    equipment: list[dict[str, Any]] = []
    points: list[tuple[float, float, float]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ValueError(f"layout_rows[{index}]_invalid")
        requested_refno = raw.get("requested_refno")
        resolved_refno = raw.get("refno")
        refno = requested_refno if isinstance(requested_refno, str) and requested_refno.strip() else resolved_refno
        if not isinstance(refno, str) or not refno.strip():
            refno = f"ROW_{index + 1}"
        spatial = _spatial(raw)
        position = _point(spatial) if spatial is not None else None
        axes = _axes(spatial) if spatial is not None else None
        spatial_field = raw.get("measurements", {}).get("spatial", {}) if isinstance(raw.get("measurements"), Mapping) else {}
        # The public batch contract carries success/complete flags at both
        # levels. Direct callers may provide only the geometry payload; in
        # that case valid position and orientation are sufficient evidence,
        # while any explicit false flag remains authoritative.
        field_success = spatial_field.get("success", True) if isinstance(spatial_field, Mapping) else False
        field_complete = spatial_field.get("complete", True) if isinstance(spatial_field, Mapping) else False
        result_success = spatial.get("success", True) if isinstance(spatial, Mapping) else False
        result_complete = spatial.get("complete", True) if isinstance(spatial, Mapping) else False
        read_success = field_success is True and result_success is True
        read_complete = read_success and field_complete is True and result_complete is True
        evidence: dict[str, Any] = {
            "requested_refno": refno, "position": list(position) if position else None,
            "orientation": {name: list(value) for name, value in axes.items()} if axes else None,
            "position_available": position is not None, "orientation_available": axes is not None,
            "read_success": read_success, "read_complete": read_complete,
            "read_errors": raw.get("errors", []), "linework_included": position is not None,
        }
        element = raw.get("element")
        if isinstance(element, Mapping):
            name = element.get("name")
            if isinstance(name, str) and name.strip():
                evidence["element_name"] = name
        if isinstance(resolved_refno, str) and resolved_refno != refno:
            evidence["resolved_refno"] = resolved_refno
        if position is None:
            evidence["error_code"] = "world_position_unavailable"
        else:
            points.append(position)
            if axes is None:
                evidence["error_code"] = "world_orientation_unavailable"
        equipment.append(evidence)

    if not points:
        return {"success": False, "available": False, "complete": False,
                "error_code": "no_world_positions", "equipment": equipment, "views": []}

    views_out: list[dict[str, Any]] = []
    for name in names:
        horizontal, vertical, depth = _VIEWS[name]
        lines: list[dict[str, Any]] = []
        for item in equipment:
            position = item.get("position")
            if not isinstance(position, list):
                continue
            origin = [position[horizontal], position[vertical]]
            # A short cross is stable at any scale and identifies the pose.
            cross = max(axis_length * 0.08, 1.0)
            lines.extend(({
                "kind": "feature", "visibility": "visible",
                "start": [origin[0] - cross, origin[1]], "end": [origin[0] + cross, origin[1]],
            }, {
                "kind": "feature", "visibility": "visible",
                "start": [origin[0], origin[1] - cross], "end": [origin[0], origin[1] + cross],
            }))
            orientation = item.get("orientation")
            if isinstance(orientation, Mapping):
                for axis_name in ("x_axis", "y_axis", "z_axis"):
                    axis = orientation.get(axis_name)
                    if not isinstance(axis, list) or len(axis) != 3:
                        continue
                    end = [origin[0] + axis[horizontal] * axis_length,
                           origin[1] + axis[vertical] * axis_length]
                    # An axis parallel to the view direction has no projected
                    # line. Omitting that zero-length glyph keeps PNG/SVG/DXF
                    # exporters from treating it as invalid geometry.
                    if abs(end[0] - origin[0]) <= 1e-12 and abs(end[1] - origin[1]) <= 1e-12:
                        continue
                    lines.append({"kind": "feature", "axis_name": axis_name[0].upper(),
                                  "visibility": "visible", "start": origin, "end": end})
        if len(lines) > _MAX_SEGMENTS:
            raise ValueError("equipment_layout_segment_limit_exceeded")
        if lines:
            xmin = min(point[0] for line in lines for point in (line["start"], line["end"]))
            xmax = max(point[0] for line in lines for point in (line["start"], line["end"]))
            ymin = min(point[1] for line in lines for point in (line["start"], line["end"]))
            ymax = max(point[1] for line in lines for point in (line["start"], line["end"]))
        else:
            xmin = xmax = ymin = ymax = 0.0
        views_out.append({"name": name, "bounds": {"min": [xmin, ymin], "max": [xmax, ymax]},
                          "dimensions": {"width": xmax - xmin, "height": ymax - ymin, "unit": "mm"},
                          "coordinate_unit": "mm", "coordinate_frame": "WORLD",
                          "horizontal_axis": horizontal, "vertical_axis": vertical,
                          "toward_viewer_axis": depth, "lines": lines, "line_count": len(lines)})
    complete = all(item["position_available"] and item["orientation_available"] and item["read_complete"]
                    for item in equipment)
    return {"success": True, "available": True, "complete": complete,
            "drawing_kind": "WORLD equipment pose layout", "coordinate_unit": "mm",
            "coordinate_frame": "WORLD", "extents_included": False,
            "equipment": equipment, "views": views_out, "segment_count": sum(view["line_count"] for view in views_out)}
