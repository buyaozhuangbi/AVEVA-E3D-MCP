"""Orthographic chord approximations of verified PANE definition curves.

Only the curve core's bounded samples are projected here. This module does not
read native data, derive arcs, project a control polygon, or classify surfaces.
The native existence/completeness evidence remains independent of drawing
success. SVG and DXF contain straight chords, never exact native ARC entities.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import html
import math
from typing import Any

from .orthographic_dxf import export_orthographic_dxf
from .orthographic_png import rasterize_orthographic_png


_AXES = {"FRONT": (0, 2), "TOP": (0, 1), "SIDE": (1, 2)}
_DRAWING_KIND = "native PANE definition-curve approximation"
PANEL_CURVE_ASSEMBLY_DRAWING_KIND = "native PANE assembly definition-curve approximation"
PANEL_CURVE_ASSEMBLY_SUCCESS_SCOPE = (
    "independent verified WORLD PANE definition-curve samples overlaid without cross-source topology inference"
)
_BOUNDARY_SOURCE = "projected_native_definition_plane_curves"
_MAX_LOOPS = 32
_MAX_LINES = 12_000
_MAX_COORDINATE_MM = 1_000_000_000.0
_MAX_SVG_BYTES = 4_000_000
_PROJECTION_DEGENERATE_TOLERANCE_MM = 1e-8
_COUNT_FIELDS = ("loop_count", "segment_count", "line_count", "arc_count", "sampled_edge_count")


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


def _point(value: Any, field: str, dimensions: int = 3) -> tuple[float, ...]:
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray))
            or len(value) != dimensions):
        raise ValueError(f"invalid_{field}")
    result = tuple(_number(component, field) for component in value)
    if any(abs(component) > _MAX_COORDINATE_MM for component in result):
        raise ValueError("coordinate_out_of_bounds")
    return result


def _count(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid_{field}")
    return value


def _view_names(views: Any) -> list[str]:
    if (not isinstance(views, Sequence) or isinstance(views, (str, bytes, bytearray))
            or not 1 <= len(views) <= 3
            or any(not isinstance(name, str) or name not in _AXES for name in views)
            or len(set(views)) != len(views)):
        raise ValueError("views must contain one to three unique FRONT, TOP or SIDE values")
    return list(views)


def _samples(boundary: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if (boundary.get("success") is not True or boundary.get("available") is not True
            or boundary.get("complete") is not True
            or boundary.get("boundary_source") != _BOUNDARY_SOURCE):
        raise ValueError("successful_complete_curve_boundary_required")
    coordinate_space = boundary.get("coordinate_space")
    if not isinstance(coordinate_space, str) or coordinate_space not in {"panel_local", "WORLD"}:
        raise ValueError("invalid_coordinate_space")
    if boundary.get("coordinate_unit") != "mm":
        raise ValueError("curve_coordinate_unit_mismatch")
    if not isinstance(boundary.get("coordinate_frame"), str) or not boundary["coordinate_frame"].strip():
        raise ValueError("curve_coordinate_frame_unavailable")
    for field in ("native_boundary_points_complete", "native_boundary_points_read_complete",
                  "native_existence_verified"):
        if field not in boundary or boundary[field] is not None and type(boundary[field]) is not bool:
            raise ValueError(f"invalid_{field}")
    tolerance = _number(boundary.get("chord_tolerance_mm"), "chord_tolerance_mm")
    if tolerance <= 0 or not math.isfinite(2 * tolerance):
        raise ValueError("invalid_chord_tolerance_mm")
    loops = boundary.get("loops")
    if not isinstance(loops, list) or not 1 <= len(loops) <= _MAX_LOOPS:
        raise ValueError("invalid_curve_loops")
    prepared: list[dict[str, Any]] = []
    totals = {field: 0 for field in _COUNT_FIELDS}
    seen_indices: set[int] = set()
    for loop in loops:
        if not isinstance(loop, Mapping) or loop.get("closed") is not True:
            raise ValueError("closed_curve_loop_required")
        index = _count(loop.get("loop_index"), "loop_index")
        if index in seen_indices:
            raise ValueError("duplicate_loop_index")
        seen_indices.add(index)
        points = loop.get("sampled_points")
        if not isinstance(points, list) or len(points) < 3:
            raise ValueError("curve_samples_unavailable")
        totals["sampled_edge_count"] += len(points)
        if totals["sampled_edge_count"] > _MAX_LINES:
            raise ValueError("drawing_segment_limit_exceeded")
        ring = [_point(point, "sampled_point") for point in points]
        if any(point == ring[(offset + 1) % len(ring)] for offset, point in enumerate(ring)):
            raise ValueError("duplicate_curve_sample")
        segments = loop.get("segments")
        if not isinstance(segments, list) or not segments or len(segments) > _MAX_LINES:
            raise ValueError("curve_segments_unavailable")
        counts = {"segment_count": len(segments), "line_count": 0, "arc_count": 0,
                  "sampled_edge_count": len(ring)}
        for segment in segments:
            if (not isinstance(segment, Mapping) or not isinstance(segment.get("kind"), str)
                    or segment["kind"] not in {"line", "arc"}):
                raise ValueError("invalid_curve_segment")
            _point(segment.get("start"), "curve_segment_start")
            _point(segment.get("end"), "curve_segment_end")
            if segment["kind"] == "arc":
                _point(segment.get("center"), "curve_arc_center")
                _point(segment.get("normal"), "curve_arc_normal")
                if (_number(segment.get("radius_mm"), "curve_arc_radius") <= 0
                        or _number(segment.get("sweep_radians"), "curve_arc_sweep") == 0):
                    raise ValueError("invalid_curve_arc")
                counts["arc_count"] += 1
            else:
                counts["line_count"] += 1
        for field, expected in counts.items():
            if _count(loop.get(field), field) != expected:
                raise ValueError(f"curve_loop_{field}_mismatch")
            if field != "sampled_edge_count":
                totals[field] += expected
        totals["loop_count"] += 1
        prepared.append({"loop_index": index, "source_refno": loop.get("source_refno"),
                         "role": "not inferred", "closed": True, **counts,
                         "sampled_points": [list(point) for point in ring],
                         "plane": deepcopy(loop.get("plane")), "ring": ring})
    for field, expected in totals.items():
        if _count(boundary.get(field), field) != expected:
            raise ValueError(f"curve_{field}_mismatch")
    return prepared, totals


def _project(loops: list[dict[str, Any]], names: list[str], frame: str,
             tolerance: float) -> tuple[list[dict[str, Any]], int]:
    """Retain sample bounds, declaring the small projected-edge omissions.

    Orthographic projection cannot increase chord error. Each bound coordinate
    therefore differs by at most the chord tolerance, and an extent by twice it.
    """
    built: list[dict[str, Any]] = []
    total = 0
    for name in names:
        first, second = _AXES[name]
        projected = [(point[first], point[second]) for loop in loops for point in loop["ring"]]
        minimum = [min(point[axis] for point in projected) for axis in range(2)]
        maximum = [max(point[axis] for point in projected) for axis in range(2)]
        dimensions = [maximum[axis] - minimum[axis] for axis in range(2)]
        if any(not math.isfinite(value) or value > _MAX_COORDINATE_MM for value in dimensions):
            raise ValueError("coordinate_out_of_bounds")
        lines: list[dict[str, Any]] = []
        dropped_counts = {"zero_length": 0, "numerical_residue": 0}
        dropped_details: list[dict[str, Any]] = []
        for loop in loops:
            ring = loop["ring"]
            for index, point in enumerate(ring):
                other = ring[(index + 1) % len(ring)]
                start, end = [point[first], point[second]], [other[first], other[second]]
                projected_length = math.dist(start, end)
                if projected_length <= _PROJECTION_DEGENERATE_TOLERANCE_MM:
                    reason = "zero_length" if projected_length == 0 else "numerical_residue"
                    dropped_counts[reason] += 1
                    dropped_details.append({"loop_index": loop["loop_index"],
                                            "sampled_edge_index": index,
                                            "source_refno": loop["source_refno"],
                                            "projected_length_mm": projected_length,
                                            "reason": reason})
                    continue
                total += 1
                if total > _MAX_LINES:
                    raise ValueError("drawing_segment_limit_exceeded")
                lines.append({"start": start, "end": end, "kind": "feature", "visibility": "visible",
                              "loop_index": loop["loop_index"], "sampled_edge_index": index,
                              "source_refno": loop["source_refno"], "approximation": "curve chord"})
        built.append({"name": name, "bounds": {"min": minimum, "max": maximum},
                      "bounds_semantics": "sampled chord approximation; analytic extrema may lie between samples",
                      "bounds_coordinate_error_bound_mm": tolerance,
                      "extent_error_bound_mm": 2 * tolerance,
                      "dimensions": {"width": dimensions[0], "height": dimensions[1], "unit": "mm"},
                      "dimension_label": "SAMPLED EXTENT", "coordinate_unit": "mm", "coordinate_frame": frame,
                      "lines": lines, "line_count": len(lines),
                      "projection_degenerate_tolerance_mm": _PROJECTION_DEGENERATE_TOLERANCE_MM,
                      "dropped_projected_edges": sum(dropped_counts.values()),
                      "dropped_projected_edge_counts": dropped_counts,
                      "dropped_projected_edge_details": dropped_details})
    return built, total


def _footer(tolerance: float) -> str:
    return f"PANE DEFINITION CURVE APPROX - CHORD TOL {tolerance:.9g} MM - NO THICKNESS/CUTS"


def _svg(views: list[dict[str, Any]], footer: str, tolerance: float) -> str:
    width, height, margin, gap, header = 1200, 440, 20, 24, 30
    panel_width = (width - 2 * margin - gap * (len(views) - 1)) / len(views)
    content_height = height - 3 * margin - header
    limits = []
    for view in views:
        dx, dy = view["dimensions"]["width"], view["dimensions"]["height"]
        if dx > 0:
            limits.append((panel_width - 2 * margin) / dx)
        if dy > 0:
            limits.append(content_height / dy)
    scale = min(limits, default=1.0)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("invalid_curve_projection_scale")
    result = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" data-approximation="bounded-chord polyline" data-chord-tolerance-mm="{tolerance:.12g}" data-shared-scale="{scale:.12g}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]
    for index, view in enumerate(views):
        left = margin + index * (panel_width + gap)
        xmin, ymin = view["bounds"]["min"]
        xmax, ymax = view["bounds"]["max"]
        dx, dy = xmax - xmin, ymax - ymin
        ox = left + (panel_width - dx * scale) / 2
        oy = margin + header + (content_height - dy * scale) / 2
        result.append(f'<g data-view="{view["name"]}">')
        label = html.escape(view.get("dimension_label", "SAMPLED EXTENT"))
        result.append(f'<text x="{left + 4:.3f}" y="{margin + 16}" font-family="sans-serif" font-size="14">{view["name"]} {label} {dx:.3f} x {dy:.3f} mm</text>')
        for line in view["lines"]:
            a, b = line["start"], line["end"]
            x1, y1 = ox + (a[0] - xmin) * scale, oy + (ymax - a[1]) * scale
            x2, y2 = ox + (b[0] - xmin) * scale, oy + (ymax - b[1]) * scale
            attribution = "".join(
                f' data-{key.replace("_", "-")}="{html.escape(str(line[key]), quote=True)}"'
                for key in ("source_index", "source_refno", "source_loop_index", "source_loop_refno")
                if key in line and line[key] is not None
            )
            result.append(f'<line x1="{x1:.6f}" y1="{y1:.6f}" x2="{x2:.6f}" y2="{y2:.6f}" data-loop-index="{line["loop_index"]}" data-sampled-edge-index="{line["sampled_edge_index"]}"{attribution} stroke="#17212b" stroke-width="1.25"/>')
        result.append("</g>")
    result.append(f'<text x="20" y="430" fill="#59636e" font-family="sans-serif" font-size="10">{html.escape(footer)}</text></svg>')
    svg = "\n".join(result)
    if len(svg.encode("utf-8")) > _MAX_SVG_BYTES:
        raise ValueError("drawing_output_limit_exceeded")
    return svg


def build_panel_curve_drawing(boundary: Mapping[str, Any], *,
                             views: Sequence[str] = ("FRONT", "TOP", "SIDE"),
                             include_svg: bool = False, include_dxf: bool = False) -> dict[str, Any]:
    """Project complete curve samples without promoting native completeness."""
    if not isinstance(boundary, Mapping):
        raise ValueError("invalid_curve_boundary")
    if type(include_svg) is not bool or type(include_dxf) is not bool:
        raise ValueError("include_svg and include_dxf must be booleans")
    names = _view_names(views)
    loops, counts = _samples(boundary)
    source = boundary.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("curve_source_unavailable")
    diagnostics = boundary.get("diagnostics")
    source_diagnostics = boundary.get("source_diagnostics")
    if not isinstance(diagnostics, Mapping) or not isinstance(source_diagnostics, Mapping):
        raise ValueError("curve_source_diagnostics_unavailable")
    tolerance = _number(boundary["chord_tolerance_mm"], "chord_tolerance_mm")
    built, projected_count = _project(loops, names, boundary["coordinate_frame"], tolerance)
    footer = _footer(tolerance)
    drawing: dict[str, Any] = {
        "success": True, "available": True, "complete": True,
        "drawing_kind": _DRAWING_KIND, "boundary_source": _BOUNDARY_SOURCE,
        "drawing_success_scope": "projection of bounded definition-curve samples with declared degenerate-edge suppression",
        "approximation": "bounded-chord polyline", "chord_tolerance_mm": tolerance,
        "coordinate_space": boundary["coordinate_space"], "coordinate_unit": "mm",
        "coordinate_frame": boundary["coordinate_frame"], "source": deepcopy(dict(source)),
        "curve_counts": counts, "loop_count": counts["loop_count"],
        "curve_line_count": counts["line_count"], "arc_count": counts["arc_count"],
        "sampled_edge_count": counts["sampled_edge_count"],
        "loops": [{key: value for key, value in loop.items() if key != "ring"} for loop in loops],
        "views": built, "segment_count": projected_count, "projected_line_count": projected_count,
        "projection_degenerate_tolerance_mm": _PROJECTION_DEGENERATE_TOLERANCE_MM,
        "projection_degenerate_filtering": "projected edges at or below tolerance omitted; original 3D samples retained",
        "dropped_projected_edges": {view["name"]: view["dropped_projected_edges"] for view in built},
        "shared_scale": True, "scale_policy": {"png_svg": "one uniform scale across requested views",
                                              "dxf": "1 model millimetre per drawing unit; translated views"},
        "source_diagnostics": deepcopy(dict(source_diagnostics)), "diagnostics": deepcopy(dict(diagnostics)),
        "native_boundary_points_complete": boundary["native_boundary_points_complete"],
        "native_boundary_points_read_complete": boundary["native_boundary_points_read_complete"],
        "native_existence_verified": boundary["native_existence_verified"],
        "native_complete": boundary["native_boundary_points_complete"],
        "native_boundary_complete": boundary["native_boundary_points_complete"],
        "native_tessellation": False, "rendered_boundary": False, "cuts_applied": False,
        "thickness_resolved": False, "hole_roles_inferred": False, "hole_semantics_inferred": False,
        "visibility_classified": False, "hidden_line_removal": False, "occlusion_checked": False,
        "csg_evaluated": False, "footer": footer,
    }
    return export_panel_curve_drawing(drawing, include_svg=include_svg, include_dxf=include_dxf)


def _validate_drawing(drawing: Mapping[str, Any]) -> tuple[list[dict[str, Any]], float, str]:
    if (not isinstance(drawing, Mapping) or drawing.get("success") is not True
            or drawing.get("complete") is not True or not isinstance(drawing.get("drawing_kind"), str)
            or drawing["drawing_kind"] not in {_DRAWING_KIND, PANEL_CURVE_ASSEMBLY_DRAWING_KIND}
            or drawing.get("approximation") != "bounded-chord polyline" or drawing.get("coordinate_unit") != "mm"):
        raise ValueError("successful_complete_curve_drawing_required")
    if drawing["drawing_kind"] == PANEL_CURVE_ASSEMBLY_DRAWING_KIND:
        sources = drawing.get("sources")
        if (drawing.get("coordinate_space") != "WORLD"
                or drawing.get("drawing_success_scope") != PANEL_CURVE_ASSEMBLY_SUCCESS_SCOPE
                or type(drawing.get("source_count")) is not int
                or not 1 <= drawing["source_count"] <= 128
                or not isinstance(sources, list) or len(sources) != drawing["source_count"]
                or any(not isinstance(source, Mapping) or source.get("complete") is not True
                       for source in sources)):
            raise ValueError("successful_complete_curve_assembly_drawing_required")
    views = drawing.get("views")
    if not isinstance(views, list):
        raise ValueError("invalid_views")
    _view_names([view.get("name") if isinstance(view, Mapping) else None for view in views])
    total = 0
    for view in views:
        bounds = view.get("bounds")
        if not isinstance(bounds, Mapping):
            raise ValueError("invalid_bounds")
        minimum = _point(bounds.get("min"), "bounds", 2)
        maximum = _point(bounds.get("max"), "bounds", 2)
        if any(not 0 <= maximum[axis] - minimum[axis] <= _MAX_COORDINATE_MM for axis in range(2)):
            raise ValueError("coordinate_out_of_bounds")
        dimensions = view.get("dimensions")
        if (not isinstance(dimensions, Mapping)
                or _number(dimensions.get("width"), "view_width") != maximum[0] - minimum[0]
                or _number(dimensions.get("height"), "view_height") != maximum[1] - minimum[1]
                or not isinstance(view.get("dimension_label", "SAMPLED EXTENT"), str)):
            raise ValueError("curve_view_dimensions_mismatch")
        lines = view.get("lines")
        if not isinstance(lines, list):
            raise ValueError("invalid_lines")
        total += len(lines)
        if total > _MAX_LINES:
            raise ValueError("drawing_segment_limit_exceeded")
        for line in lines:
            if not isinstance(line, Mapping):
                raise ValueError("invalid_line")
            _point(line.get("start"), "line_start", 2)
            _point(line.get("end"), "line_end", 2)
    tolerance = _number(drawing.get("chord_tolerance_mm"), "chord_tolerance_mm")
    if tolerance <= 0:
        raise ValueError("invalid_chord_tolerance_mm")
    footer = drawing.get("footer")
    if (not isinstance(footer, str) or len(footer) > 200 or not footer.isascii()
            or any(ord(character) < 32 for character in footer)):
        raise ValueError("invalid_curve_drawing_footer")
    return views, tolerance, footer


def export_panel_curve_drawing(drawing: Mapping[str, Any], *, include_svg: bool = False,
                               include_dxf: bool = False) -> dict[str, Any]:
    """Attach selected chord exports to a validated curve drawing without mutation.

    View bounds drive placement, so assembly pages with identical global bounds
    retain the same SVG scale and DXF translations. DXF retains LINE geometry;
    source metadata belongs to the structured drawing rather than DXF entities.
    """
    if type(include_svg) is not bool or type(include_dxf) is not bool:
        raise ValueError("include_svg and include_dxf must be booleans")
    views, tolerance, footer = _validate_drawing(drawing)
    result = dict(drawing)
    if include_svg:
        result["svg"] = _svg(views, footer, tolerance)
    if include_dxf:
        result["dxf"] = export_orthographic_dxf(result, include_dimensions=False, footer=footer)
        result["dxf_geometry_entity"] = "LINE"
        result["dxf_approximation"] = "bounded-chord polyline"
        result["dxf_chord_tolerance_mm"] = tolerance
    return result


def render_panel_curve_png(drawing: Mapping[str, Any], *, width: int, height: int) -> bytes:
    """Render verified single-PANE or assembly chord linework at common scale."""
    views, _, footer = _validate_drawing(drawing)
    return rasterize_orthographic_png(views, width=width, height=height, footer=footer)
