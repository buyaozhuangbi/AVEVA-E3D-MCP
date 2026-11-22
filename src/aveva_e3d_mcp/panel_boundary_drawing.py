"""Direct orthographic drawings of native PANE control-point boundaries.

The native PAVE positions are the only geometry used here.  This deliberately
does not turn a PANE into a prism, run hidden-line classification, or assign
outer/inner meaning to multiple loops.  The result is useful for inspecting
large equipment panels whose native thickness/justification or Boolean cuts
cannot be represented by the parameter mesh path.
"""

from __future__ import annotations

import html
import math
from collections.abc import Mapping, Sequence
from typing import Any

from .orthographic_dxf import export_orthographic_dxf
from .orthographic_png import rasterize_orthographic_png


_AXES: dict[str, tuple[int, int]] = {
    "FRONT": (0, 2),  # +Y view: X/Z
    "TOP": (0, 1),    # +Z view: X/Y
    "SIDE": (1, 2),   # +X view: Y/Z
}
_MAX_LOOPS = 32
_MAX_VERTICES = 2048
_MAX_SEGMENTS = 12000
_MAX_ABS_COORDINATE_MM = 1_000_000_000.0


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_{field}")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"invalid_{field}") from None
    if not math.isfinite(result):
        raise ValueError(f"invalid_{field}")
    return result


def _point(value: Any, field: str) -> tuple[float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or len(value) != 3:
        raise ValueError(f"invalid_{field}")
    point = tuple(_number(item, field) for item in value)
    if any(abs(item) > _MAX_ABS_COORDINATE_MM for item in point):
        raise ValueError("coordinate_out_of_bounds")
    return point  # type: ignore[return-value]


def _read_points(native: Mapping[str, Any], coordinate_space: str) -> tuple[list[list[tuple[float, float, float]]], dict[str, Any]]:
    loops = native.get("loops")
    if not isinstance(loops, list) or not loops:
        raise ValueError("panel_loops_unavailable")
    if len(loops) > _MAX_LOOPS:
        raise ValueError("panel_loop_limit_exceeded")
    if native.get("success") is not True or native.get("complete") is not True:
        raise ValueError("panel_geometry_incomplete")
    if coordinate_space == "WORLD" and native.get("world_read_errors"):
        raise ValueError("world_panel_position_read_errors")
    if coordinate_space == "WORLD" and "world_position_unit" in native and native["world_position_unit"] != "mm":
        raise ValueError("world_panel_position_unit_mismatch")
    if coordinate_space == "WORLD" and native.get("world_positions_complete") is not True:
        raise ValueError("world_panel_positions_incomplete")
    if coordinate_space == "WORLD" and (
            not isinstance(native.get("world_position_frame"), str) or
            not native["world_position_frame"].strip()):
        raise ValueError("world_panel_position_frame_unavailable")
    if native.get("read_errors"):
        raise ValueError("panel_geometry_read_errors")
    points_by_loop: list[list[tuple[float, float, float]]] = []
    total = 0
    loop_records: list[dict[str, Any]] = []
    for index, loop in enumerate(loops):
        if not isinstance(loop, Mapping) or loop.get("complete") is not True:
            raise ValueError(f"panel_loop_{index}_incomplete")
        if loop.get("read_errors"):
            raise ValueError(f"panel_loop_{index}_read_errors")
        vertices = loop.get("vertices")
        if not isinstance(vertices, list) or len(vertices) < 3:
            raise ValueError(f"panel_loop_{index}_vertices_unavailable")
        total += len(vertices)
        if total > _MAX_VERTICES:
            raise ValueError("panel_vertex_limit_exceeded")
        ring: list[tuple[float, float, float]] = []
        for vertex_index, vertex in enumerate(vertices):
            if not isinstance(vertex, Mapping):
                raise ValueError(f"panel_vertex_{index}_invalid")
            field = "position_world_mm" if coordinate_space == "WORLD" else "position"
            if coordinate_space == "WORLD" and vertex.get("world_position_complete") is not True:
                raise ValueError(f"panel_world_vertex_{index}_unavailable")
            ring.append(_point(vertex.get(field), field))
        points_by_loop.append(ring)
        loop_records.append({"loop_index": index, "vertex_count": len(ring),
                             "role": "not inferred", "polygon_valid": loop.get("polygon_valid"),
                             "native_features": loop.get("unsupported_features", []),
                             "boundary_semantics": "raw PAVE control-point loop; native curve/fillet semantics are not resolved"})
        for key in ("source_index", "source_refno"):
            if key in loop:
                loop_records[-1][key] = loop[key]
    if total != native.get("vertex_count", total) and native.get("vertex_count") is not None:
        raise ValueError("panel_vertex_count_mismatch")
    if coordinate_space == "WORLD" and (
            native.get("world_position_count") != total or
            native.get("world_position_vertex_count") != total):
        raise ValueError("world_panel_position_count_mismatch")
    return points_by_loop, {"loop_count": len(points_by_loop), "vertex_count": total, "loops": loop_records}


def _build_views(points_by_loop: list[list[tuple[float, float, float]]], views: Sequence[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {"dropped_projected_edges": {}}
    total_segments = 0
    for name in views:
        first, second = _AXES[name]
        points = [point for ring in points_by_loop for point in ring]
        projected = [(point[first], point[second]) for point in points]
        lines: list[dict[str, Any]] = []
        dropped = 0
        offset = 0
        for ring in points_by_loop:
            # `offset` tracks the native PLOO order across the selected PANE set.
            for index, point in enumerate(ring):
                other = ring[(index + 1) % len(ring)]
                start = [point[first], point[second]]
                end = [other[first], other[second]]
                if start == end:
                    dropped += 1
                    continue
                lines.append({"start": start, "end": end, "kind": "feature",
                              "visibility": "visible", "loop_index": offset})
            offset += 1
        total_segments += len(lines)
        if total_segments > _MAX_SEGMENTS:
            raise ValueError("drawing_segment_limit_exceeded")
        minimum = [min(item[index] for item in projected) for index in range(2)]
        maximum = [max(item[index] for item in projected) for index in range(2)]
        output.append({"name": name, "bounds": {"min": minimum, "max": maximum},
                       "dimensions": {"width": maximum[0] - minimum[0],
                                      "height": maximum[1] - minimum[1], "unit": "mm"},
                       "dimension_label": "EXTENT",
                       "coordinate_unit": "mm", "coordinate_frame": "E3D WORLD or PANE local",
                       "lines": lines, "line_count": len(lines),
                       "dropped_projected_edges": dropped})
        diagnostics["dropped_projected_edges"][name] = dropped
    return output, {**diagnostics, "segment_count": total_segments}


def _svg(views: Sequence[Mapping[str, Any]], footer: str) -> str:
    width, height, margin, gap, header = 1200, 440, 20, 24, 30
    columns = len(views)
    panel_width = (width - 2 * margin - gap * (columns - 1)) / columns
    panel_height = height - 2 * margin
    all_widths = [float(v["dimensions"]["width"]) for v in views]
    all_heights = [float(v["dimensions"]["height"]) for v in views]
    scale_limits = [(panel_width - 2 * margin) / value for value in all_widths if value > 0]
    scale_limits.extend((panel_height - header - margin) / value for value in all_heights if value > 0)
    scale = min(scale_limits, default=1.0)
    if not math.isfinite(scale) or scale <= 0:
        scale = 1.0
    result = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
              '<rect width="100%" height="100%" fill="white"/>']
    for index, view in enumerate(views):
        left = margin + index * (panel_width + gap)
        bounds = view["bounds"]
        xmin, ymin = bounds["min"]
        xmax, ymax = bounds["max"]
        dx, dy = xmax - xmin, ymax - ymin
        ox = left + (panel_width - dx * scale) / 2
        oy = margin + header + (panel_height - header - margin - dy * scale) / 2
        result.append(f'<text x="{left + 4:.3f}" y="{margin + 16:.3f}" font-family="sans-serif" font-size="14">{html.escape(str(view["name"]))} EXTENT — {dx:.3f} × {dy:.3f} mm</text>')
        for line in view["lines"]:
            a, b = line["start"], line["end"]
            x1, y1 = ox + (a[0] - xmin) * scale, oy + (ymax - a[1]) * scale
            x2, y2 = ox + (b[0] - xmin) * scale, oy + (ymax - b[1]) * scale
            result.append(f'<line x1="{x1:.6f}" y1="{y1:.6f}" x2="{x2:.6f}" y2="{y2:.6f}" stroke="#17212b" stroke-width="1.25"/>')
    result.append(f'<text x="20" y="430" fill="#59636e" font-family="sans-serif" font-size="10">{html.escape(footer)}</text></svg>')
    return "\n".join(result)


def build_panel_boundary_drawing(native: Mapping[str, Any], *, coordinate_space: str = "WORLD",
                                 views: Sequence[str] = ("FRONT", "TOP", "SIDE"),
                                 include_svg: bool = False, include_dxf: bool = False) -> dict[str, Any]:
    if coordinate_space not in {"WORLD", "panel_local"}:
        raise ValueError("coordinate_space must be WORLD or panel_local")
    if (not isinstance(views, Sequence) or isinstance(views, (str, bytes, bytearray)) or
            not 1 <= len(views) <= 3 or any(not isinstance(item, str) or item not in _AXES for item in views) or
            len(set(views)) != len(views)):
        raise ValueError("views must contain one to three unique FRONT, TOP or SIDE values")
    points_by_loop, source = _read_points(native, coordinate_space)
    identity = native.get("element") if isinstance(native.get("element"), Mapping) else {}
    if isinstance(identity.get("refno"), str):
        source["refno"] = identity["refno"]
    if isinstance(identity.get("name"), str):
        source["name"] = identity["name"]
    if isinstance(native.get("revision"), str):
        source["revision"] = native["revision"]
    if coordinate_space == "WORLD":
        source["world_position_frame"] = native["world_position_frame"]
    source["topology_valid"] = native.get("topology_valid")
    built, diagnostics = _build_views(points_by_loop, views)
    footer = "PANE control-point polygons; native boundary, thickness, holes and visibility not resolved."
    drawing: dict[str, Any] = {
        "success": True, "available": True, "complete": True,
        "drawing_kind": "native PANE control-point polygon",
        "coordinate_space": coordinate_space, "coordinate_unit": "mm",
        "coordinate_frame": (f"E3D Design WORLD ({native.get('world_position_frame')})"
                              if coordinate_space == "WORLD" else f"PANE local ({native.get('coordinate_frame')})"),
        "native_tessellation": False, "thickness_resolved": False,
        "hole_semantics_inferred": False, "visibility_classified": False,
        "hidden_line_removal": False, "occlusion_checked": False,
        "native_polygon_validity_required": False,
        "drawing_success_scope": "complete PAVE control-point reads and projected control-polygon linework only; not a rendered outline",
        "boundary_semantics": "raw PAVE control-point polygon; correspondence to rendered/curved native boundary is not resolved",
        "source": source, "source_geometry_complete": native.get("complete") is True,
        "topology_valid": native.get("topology_valid"),
        "views": built, **diagnostics, "footer": footer,
    }
    if coordinate_space == "WORLD":
        drawing["world_position_frame"] = native["world_position_frame"]
    if include_svg:
        drawing["svg"] = _svg(built, footer)
        if len(drawing["svg"].encode("utf-8")) > 4_000_000:
            raise ValueError("drawing_output_limit_exceeded")
    if include_dxf:
        drawing["dxf"] = export_orthographic_dxf(drawing, include_dimensions=False,
                                                   footer="PANE control-point boundaries; thickness, holes and visibility not resolved.")
    return drawing


def render_panel_boundary_png(drawing: Mapping[str, Any], *, width: int, height: int) -> bytes:
    footer = "PANE CONTROL-POLYGONS; BOUNDARY/THICKNESS/HOLES UNRESOLVED"
    return rasterize_orthographic_png(drawing["views"], width=width, height=height, footer=footer)


def compose_panel_boundary_page(drawings: Sequence[Mapping[str, Any]], *, coordinate_space: str,
                                include_svg: bool = False, include_dxf: bool = False,
                                source_indices: Sequence[int] | None = None) -> dict[str, Any]:
    """Merge several already-read PANE boundary drawings into one page.

    This is a linework overlay only.  Sources remain independent and are never
    welded, unioned, depth-classified, or assigned outer/inner meanings.
    """
    if not drawings:
        raise ValueError("panel_page_requires_source")
    if source_indices is not None and (len(source_indices) != len(drawings) or
                                       any(type(index) is not int or index < 0 for index in source_indices)):
        raise ValueError("invalid_source_indices")
    world_position_frame: str | None = None
    if coordinate_space == "WORLD":
        frames = [drawing.get("world_position_frame") for drawing in drawings]
        if any(not isinstance(frame, str) or not frame.strip() for frame in frames):
            raise ValueError("world_panel_position_frame_unavailable")
        if any(frame != frames[0] for frame in frames[1:]):
            raise ValueError("world_position_frame_mismatch")
        world_position_frame = frames[0]
    coordinate_frame = (f"E3D Design WORLD ({world_position_frame})"
                        if coordinate_space == "WORLD" else "PANE local")
    names = [view.get("name") for view in drawings[0].get("views", [])]
    if not names:
        raise ValueError("panel_page_views_unavailable")
    pages: list[dict[str, Any]] = []
    for name in names:
        selected = []
        for drawing in drawings:
            found = next((view for view in drawing.get("views", []) if view.get("name") == name), None)
            if not isinstance(found, Mapping):
                raise ValueError("panel_page_view_mismatch")
            selected.append(found)
        minimum = [min(view["bounds"]["min"][axis] for view in selected) for axis in range(2)]
        maximum = [max(view["bounds"]["max"][axis] for view in selected) for axis in range(2)]
        lines: list[dict[str, Any]] = []
        dropped = 0
        for local_index, view in enumerate(selected):
            source_index = source_indices[local_index] if source_indices is not None else local_index
            dropped += int(view.get("dropped_projected_edges", 0))
            for line in view.get("lines", []):
                row = dict(line)
                row["source_index"] = source_index
                source = drawings[local_index].get("source")
                if isinstance(source, Mapping) and isinstance(source.get("refno"), str):
                    row["source_refno"] = source["refno"]
                lines.append(row)
        pages.append({"name": name, "bounds": {"min": minimum, "max": maximum},
                      "dimensions": {"width": maximum[0] - minimum[0],
                                     "height": maximum[1] - minimum[1], "unit": "mm"},
                      "dimension_label": "EXTENT", "coordinate_unit": "mm",
                      "coordinate_frame": coordinate_frame,
                      "lines": lines, "line_count": len(lines),
                      "dropped_projected_edges": dropped})
    footer = "PANE control-point polygons; native boundary, thickness, holes and visibility not resolved."
    result: dict[str, Any] = {
        "success": True, "available": True, "complete": True,
        "drawing_kind": "native PANE control-point polygon",
        "coordinate_space": coordinate_space, "coordinate_unit": "mm",
        "coordinate_frame": coordinate_frame,
        "native_tessellation": False, "thickness_resolved": False,
        "hole_semantics_inferred": False, "visibility_classified": False,
        "hidden_line_removal": False, "occlusion_checked": False,
        "boundary_semantics": "raw PAVE control-point polygons; loop roles not inferred",
        "views": pages, "segment_count": sum(page["line_count"] for page in pages),
        "dropped_projected_edges": {page["name"]: page["dropped_projected_edges"] for page in pages},
        "source_count": len(drawings), "source_drawings": [drawing.get("source") for drawing in drawings],
        "footer": footer,
    }
    if coordinate_space == "WORLD":
        result["world_position_frame"] = world_position_frame
    if include_svg:
        result["svg"] = _svg(pages, footer)
        if len(result["svg"].encode("utf-8")) > 4_000_000:
            raise ValueError("drawing_output_limit_exceeded")
    if include_dxf:
        result["dxf"] = export_orthographic_dxf(result, include_dimensions=False, footer=footer)
    return result
