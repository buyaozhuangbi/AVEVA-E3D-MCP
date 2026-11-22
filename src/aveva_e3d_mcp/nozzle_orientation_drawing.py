"""Bounded, read-only nozzle orientation drawing from native PPOS/PDIR data.

The module deliberately treats connection-point data as the source of truth. It
does not infer nominal diameter, connectivity, or a catalogue orientation.
"""

from __future__ import annotations

import math
from typing import Any

from .orthographic_png import rasterize_orthographic_png


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _vector(data: Any) -> tuple[float, float, float] | None:
    if not isinstance(data, dict) or data.get("available") is not True:
        return None
    result = tuple(_finite(data.get(axis)) for axis in ("x", "y", "z"))
    return result if all(value is not None for value in result) else None  # type: ignore[return-value]


def _bounds(points: list[tuple[float, float]]) -> dict[str, list[float]]:
    if not points:
        return {"min": [-100.0, -100.0], "max": [100.0, 100.0]}
    xs, ys = [point[0] for point in points], [point[1] for point in points]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    width, height = xmax - xmin, ymax - ymin
    pad = max(width, height, 1.0) * 0.2
    return {"min": [xmin - pad, ymin - pad], "max": [xmax + pad, ymax + pad]}


def _view(name: str, points: list[tuple[float, float]], lines: list[dict[str, Any]]) -> dict[str, Any]:
    # Include arrow endpoints in bounds so the renderer never clips or rejects
    # otherwise valid orientation vectors.
    geometry_points = list(points)
    for line in lines:
        for key in ("start", "end"):
            value = line.get(key)
            if isinstance(value, (list, tuple)) and len(value) == 2:
                x, y = _finite(value[0]), _finite(value[1])
                if x is not None and y is not None:
                    geometry_points.append((x, y))
    bounds = _bounds(geometry_points)
    return {"name": name, "bounds": bounds,
            "dimensions": {"width": bounds["max"][0] - bounds["min"][0],
                           "height": bounds["max"][1] - bounds["min"][1], "unit": "mm"},
            "coordinate_unit": "mm", "lines": lines}


def _dxf_representable_projection(start: list[float], end: list[float]) -> bool:
    """Drop direction glyphs that collapse in the DXF six-decimal mm format."""
    return tuple(round(value, 6) for value in start) != tuple(round(value, 6) for value in end)


def _direction_glyph(start: list[float], end: list[float]) -> list[dict[str, Any]]:
    if not _dxf_representable_projection(start, end):
        return []
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    head_length = min(12.0, length * 0.3)
    cosine, sine = math.cos(math.radians(25.0)), math.sin(math.radians(25.0))
    segments = [{"start": start, "end": end, "kind": "feature", "visibility": "visible"}]
    for sign in (-1.0, 1.0):
        head_end = [end[0] - head_length * (cosine * ux - sign * sine * uy),
                    end[1] - head_length * (cosine * uy + sign * sine * ux)]
        if _dxf_representable_projection(head_end, end):
            segments.append({"start": head_end, "end": end,
                             "kind": "feature", "visibility": "visible"})
    return segments


def build_nozzle_orientation_drawing(records: list[dict[str, Any]], *, width: int = 1200,
                                     height: int = 700) -> tuple[dict[str, Any], bytes | None]:
    """Build structured plan/elevation evidence and a PNG when geometry is complete.

    ``records`` contains one native connection-points response per explicit
    target. The response is retained under ``native`` for auditability.
    """
    nozzle_rows: list[dict[str, Any]] = []
    top_points: list[tuple[float, float]] = []
    front_points: list[tuple[float, float]] = []
    top_lines: list[dict[str, Any]] = []
    front_lines: list[dict[str, Any]] = []
    complete = True
    arrow_length = 100.0
    for record in records:
        refno = record.get("refno")
        native = record.get("native")
        native_result = native.get("result") if isinstance(native, dict) else None
        if not isinstance(native_result, dict):
            complete = False
            nozzle_rows.append({"source_index": len(nozzle_rows), "refno": refno,
                                "available": False, "error": "invalid_native_result"})
            continue
        if not isinstance(native, dict) or native.get("ok") is not True:
            # Do not emit a successful PNG from an error envelope even if a
            # malformed or older bridge also included optimistic result fields.
            complete = False
        # The native connection-points contract has success/complete flags,
        # while geometry availability is reported on each point field. Older
        # envelopes may also carry an aggregate available flag: preserve an
        # explicit negative observation without requiring the optional field.
        if (native_result.get("success") is not True or native_result.get("available", True) is not True or
                native_result.get("complete") is not True):
            complete = False
        points = native_result.get("points")
        if not isinstance(points, list):
            complete = False
            nozzle_rows.append({"source_index": len(nozzle_rows), "refno": refno,
                                "available": False, "error": "points_unavailable"})
            continue
        if not points:
            complete = False
            nozzle_rows.append({"source_index": len(nozzle_rows), "refno": refno,
                                "available": False, "error": "no_connection_points"})
            continue
        for point in points:
            if not isinstance(point, dict):
                complete = False
                continue
            position = _vector(point.get("position"))
            direction = _vector(point.get("direction"))
            number = point.get("number")
            source_index = len(nozzle_rows)
            row: dict[str, Any] = {"source_index": source_index, "refno": refno, "point_number": number,
                                   "available": position is not None, "position": point.get("position"),
                                   "direction": point.get("direction"), "bearing_deg": None,
                                   "bearing_available": False, "tilt_deg": None,
                                   "direction_available": direction is not None}
            identity = native_result.get("element")
            if isinstance(identity, dict):
                row["resolved_refno"] = identity.get("refno")
                row["element_name"] = identity.get("name")
            row["native_frame"] = native_result.get("frame")
            if position is None:
                complete = False
                row["error"] = "position_unavailable"
                nozzle_rows.append(row)
                continue
            if direction is None:
                # An orientation drawing without native PDIR is incomplete even
                # when the bridge envelope incorrectly claims complete=true.
                complete = False
                row["error"] = "direction_unavailable"
            x, y, z = position
            top_points.append((x, y))
            front_points.append((x, z))
            cross = max(arrow_length * 0.08, 1.0)
            top_lines.extend([
                {"start": [x - cross, y], "end": [x + cross, y], "kind": "feature", "visibility": "visible",
                 "source_index": source_index},
                {"start": [x, y - cross], "end": [x, y + cross], "kind": "feature", "visibility": "visible",
                 "source_index": source_index},
            ])
            front_lines.append({"start": [x - cross, z], "end": [x + cross, z], "kind": "feature", "visibility": "visible",
                                "source_index": source_index})
            if direction is not None:
                dx, dy, dz = direction
                norm = math.sqrt(dx * dx + dy * dy + dz * dz)
                if norm > 0 and math.isfinite(norm):
                    dx, dy, dz = dx / norm, dy / norm, dz / norm
                    horizontal = math.hypot(dx, dy)
                    tilt = math.degrees(math.atan2(dz, horizontal))
                    row["tilt_deg"] = tilt
                    # A near-vertical axis has no stable horizontal azimuth;
                    # keep bearing explicitly unavailable instead of inventing
                    # a value from floating-point noise.
                    if horizontal >= 1e-9:
                        row["bearing_deg"] = math.degrees(math.atan2(dx, dy)) % 360.0
                        row["bearing_available"] = True
                    # These are PDIR direction glyphs, not equipment outlines.
                    # Keep them on the feature layer in SVG/DXF exports.
                    top_start, top_end = [x, y], [x + dx * arrow_length, y + dy * arrow_length]
                    front_start, front_end = [x, z], [x + dx * arrow_length, z + dz * arrow_length]
                    top_lines.extend({**line, "source_index": source_index}
                                     for line in _direction_glyph(top_start, top_end))
                    front_lines.extend({**line, "source_index": source_index}
                                       for line in _direction_glyph(front_start, front_end))
                else:
                    row["direction_error"] = "zero_or_nonfinite_direction"
                    complete = False
            nozzle_rows.append(row)
    if not nozzle_rows:
        complete = False
    views = [_view("TOP", top_points, top_lines), _view("FRONT", front_points, front_lines)]
    drawing: dict[str, Any] = {
        "success": bool(nozzle_rows) and complete and bool(top_points),
        "available": bool(top_points), "complete": complete,
        "coordinate_frame": "WORLD", "coordinate_unit": "mm", "views": views,
        "nozzle_count": len(nozzle_rows), "nozzles": nozzle_rows,
        "nozzle_count_scope": "connection-point rows; not a count of physical nozzles",
        "point_count": len(nozzle_rows), "target_count": len(records),
        "orientation_convention": {
            "bearing_zero": "+Y", "bearing_positive": "toward +X (clockwise in TOP plan)",
            "tilt_zero": "horizontal", "tilt_positive": "+Z",
            "vertical_bearing": "unavailable",
        },
        "inference": {"nominal_diameter": False, "connectivity": False, "engineering_validity": False},
    }
    if not drawing["success"]:
        return drawing, None
    return drawing, rasterize_orthographic_png(views, width=width, height=height)
