"""Bounded, globally aligned pages of independent verified WORLD PANE curves.

Only already-verified curve boundaries enter this pure composition layer. Each
PANE is projected independently before its linework is overlaid; intersections
between different sources therefore imply neither an error nor a Boolean union.
All pages use the full request's sampled extents, never a page-specific fit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import math
import re
from typing import Any

from .panel_curve_drawing import (
    PANEL_CURVE_ASSEMBLY_DRAWING_KIND,
    PANEL_CURVE_ASSEMBLY_SUCCESS_SCOPE,
    build_panel_curve_drawing,
    export_panel_curve_drawing,
)


_MAX_SOURCES = 128
_MAX_SEGMENTS = 4096
_MAX_SAMPLED_EDGES = 12_000
_MAX_PROJECTED_LINES = 12_000
_MAX_EXTENT_MM = 1_000_000_000.0
_COUNT_FIELDS = ("loop_count", "segment_count", "line_count", "arc_count", "sampled_edge_count")
_NATIVE_FLAGS = (
    "native_boundary_points_complete", "native_boundary_points_read_complete", "native_existence_verified",
)
_UNRESOLVED = {
    "atomic_snapshot": False,
    "native_tessellation": False,
    "rendered_boundary": False,
    "cuts_applied": False,
    "thickness_resolved": False,
    "hole_roles_inferred": False,
    "hole_semantics_inferred": False,
    "visibility_classified": False,
    "hidden_line_removal": False,
    "occlusion_checked": False,
    "csg_evaluated": False,
    "cross_source_topology_checked": False,
    "sources_welded": False,
    "boolean_union_evaluated": False,
}


def _reference(value: Any) -> tuple[int, int]:
    match = re.fullmatch(r"=([0-9]+)/([0-9]+)", value) if isinstance(value, str) else None
    if match is None:
        raise ValueError("assembly_curve_resolved_numeric_refno_required")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _totals(drawings: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return {field: sum(drawing["curve_counts"][field] for drawing in drawings) for field in _COUNT_FIELDS}


def _native_flags(sources: Sequence[Mapping[str, Any]]) -> dict[str, bool | None]:
    """A false observation survives aggregation; unknown never becomes true."""
    result: dict[str, bool | None] = {}
    for field in _NATIVE_FLAGS:
        values = [source[field] for source in sources]
        result[field] = False if any(value is False for value in values) else (
            True if all(value is True for value in values) else None
        )
    result["native_complete"] = result["native_boundary_points_complete"]
    result["native_boundary_complete"] = result["native_boundary_points_complete"]
    return result


def _bounds(views: Sequence[Mapping[str, Any]]) -> dict[str, list[float]]:
    minimum = [min(view["bounds"]["min"][axis] for view in views) for axis in range(2)]
    maximum = [max(view["bounds"]["max"][axis] for view in views) for axis in range(2)]
    if any(not math.isfinite(maximum[axis] - minimum[axis])
           or maximum[axis] - minimum[axis] > _MAX_EXTENT_MM for axis in range(2)):
        raise ValueError("assembly_curve_extent_limit_exceeded")
    return {"min": minimum, "max": maximum}


def _dimensions(bounds: Mapping[str, Any]) -> dict[str, Any]:
    return {"width": bounds["max"][0] - bounds["min"][0],
            "height": bounds["max"][1] - bounds["min"][1], "unit": "mm"}


def _source_record(index: int, drawing: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_index": index, "source_refno": drawing["source"]["refno"],
        "source": deepcopy(drawing["source"]),
        "success": drawing["success"], "available": drawing["available"], "complete": drawing["complete"],
        "curve_boundary_complete": drawing["complete"],
        "coordinate_space": drawing["coordinate_space"], "coordinate_unit": drawing["coordinate_unit"],
        "coordinate_frame": drawing["coordinate_frame"], "chord_tolerance_mm": drawing["chord_tolerance_mm"],
        "curve_counts": deepcopy(drawing["curve_counts"]),
        "projected_line_count": drawing["projected_line_count"],
        "dropped_projected_edges": deepcopy(drawing["dropped_projected_edges"]),
        **{field: drawing[field] for field in _NATIVE_FLAGS},
        "native_complete": drawing["native_complete"], "native_boundary_complete": drawing["native_boundary_complete"],
        "source_diagnostics": deepcopy(drawing["source_diagnostics"]),
        "diagnostics": deepcopy(drawing["diagnostics"]),
        "atomic_snapshot": False,
    }


def _attribute(row: Mapping[str, Any], source: Mapping[str, Any], loop_indices: Mapping[int, int]) -> dict[str, Any]:
    result = deepcopy(dict(row))
    local_loop_index = row["loop_index"]
    result.update(
        source_index=source["source_index"], source_refno=source["source_refno"],
        source_loop_index=local_loop_index, source_loop_refno=row.get("source_refno"),
        loop_index=loop_indices[local_loop_index],
    )
    return result


def compose_panel_curve_pages(
    boundaries: Sequence[Mapping[str, Any]],
    *,
    panels_per_page: int = 16,
    views: Sequence[str] = ("FRONT", "TOP", "SIDE"),
    include_svg: bool = False,
    include_dxf: bool = False,
) -> dict[str, Any]:
    """Compose complete WORLD curve boundaries with one global view per axis.

    Source indices are zero-based and stable throughout the request. Pages are
    numbered from one; ``source_range`` is a zero-based half-open interval.
    ``loop_index`` is globally unique while ``source_loop_index`` retains its
    source-local value and ``source_loop_refno`` retains the original PLOO ref.
    Native false/unknown flags and original diagnostics stay in every source
    record. Successful composition describes derived chord linework only.

    Limits apply to the entire request: 128 sources, 4096 analytic segments,
    12000 sampled edges, and 12000 actual projected lines over all chosen views.
    Invalid or over-budget inputs raise ValueError; no partial pages escape.
    The caller must render all PNG pages at the same dimensions to retain the
    shared visual scale. Native context consistency belongs to the read layer.
    """
    if not isinstance(boundaries, Sequence) or isinstance(boundaries, (str, bytes, bytearray)) or not boundaries:
        raise ValueError("assembly_curve_sources_required")
    if len(boundaries) > _MAX_SOURCES:
        raise ValueError("assembly_curve_source_limit_exceeded")
    if type(panels_per_page) is not int or not 1 <= panels_per_page <= _MAX_SOURCES:
        raise ValueError("invalid_panels_per_page")
    if type(include_svg) is not bool or type(include_dxf) is not bool:
        raise ValueError("include_svg and include_dxf must be booleans")

    drawings: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    loop_maps: list[dict[int, int]] = []
    seen_sources: set[tuple[int, int]] = set()
    frame: str | None = None
    tolerance: float | None = None
    analytic_count = sampled_count = projected_count = loop_count = 0
    for index, boundary in enumerate(boundaries):
        if not isinstance(boundary, Mapping):
            raise ValueError("invalid_curve_boundary")
        if boundary.get("coordinate_space") != "WORLD":
            raise ValueError("assembly_curve_world_required")
        observed_frame = boundary.get("coordinate_frame")
        if not isinstance(observed_frame, str) or not observed_frame.strip():
            raise ValueError("curve_coordinate_frame_unavailable")
        if frame is not None and observed_frame != frame:
            raise ValueError("assembly_curve_frame_mismatch")
        frame = observed_frame
        identity = boundary.get("source")
        if not isinstance(identity, Mapping):
            raise ValueError("curve_source_unavailable")
        reference = _reference(identity.get("refno"))
        if reference in seen_sources:
            raise ValueError("assembly_curve_duplicate_source")
        seen_sources.add(reference)
        # Reject declared excess before projecting the source. The single-PANE
        # builder independently verifies all actual counts and bounded samples.
        for field in ("segment_count", "sampled_edge_count"):
            if type(boundary.get(field)) is not int or boundary[field] < 0:
                raise ValueError(f"invalid_{field}")
        analytic_count += boundary["segment_count"]
        sampled_count += boundary["sampled_edge_count"]
        if analytic_count > _MAX_SEGMENTS:
            raise ValueError("assembly_curve_segment_limit_exceeded")
        if sampled_count > _MAX_SAMPLED_EDGES:
            raise ValueError("assembly_curve_sample_limit_exceeded")
        try:
            drawing = build_panel_curve_drawing(boundary, views=views)
        except ValueError as failure:
            if str(failure) == "drawing_segment_limit_exceeded":
                raise ValueError("assembly_curve_projected_line_limit_exceeded") from failure
            raise
        if tolerance is not None and drawing["chord_tolerance_mm"] != tolerance:
            raise ValueError("assembly_curve_tolerance_mismatch")
        tolerance = drawing["chord_tolerance_mm"]
        projected_count += drawing["projected_line_count"]
        if projected_count > _MAX_PROJECTED_LINES:
            raise ValueError("assembly_curve_projected_line_limit_exceeded")
        source = _source_record(index, drawing)
        loop_maps.append({loop["loop_index"]: loop_count + offset for offset, loop in enumerate(drawing["loops"])})
        loop_count += drawing["loop_count"]
        sources.append(source)
        drawings.append(drawing)

    names = [view["name"] for view in drawings[0]["views"]]
    global_bounds = {name: _bounds([drawing["views"][offset] for drawing in drawings])
                     for offset, name in enumerate(names)}
    global_counts = _totals(drawings)
    scale_policy = {
        "png_svg": "one uniform scale across all requested views and pages at identical preview dimensions",
        "dxf": "1 model millimetre per drawing unit; identical global bounds determine translated view placement",
        "viewport": "global sampled WORLD extents shared by every page; no page-specific fit or crop",
    }
    page_count = (len(drawings) + panels_per_page - 1) // panels_per_page
    pages: list[dict[str, Any]] = []
    for start in range(0, len(drawings), panels_per_page):
        end = min(start + panels_per_page, len(drawings))
        footer = (f"PANE CURVE APPROX - PAGE {len(pages) + 1}/{page_count} - SOURCES {start + 1}-{end} "
                  f"- TOL {tolerance:.9g} MM - GLOBAL VIEWPORT - NO THICKNESS/CUTS")
        selected_drawings, selected_sources = drawings[start:end], sources[start:end]
        counts = _totals(selected_drawings)
        page_views: list[dict[str, Any]] = []
        dxf_ranges: list[dict[str, Any]] = []
        line_entity_index = 0
        for view_index, name in enumerate(names):
            selected_views = [drawing["views"][view_index] for drawing in selected_drawings]
            merged = deepcopy(selected_views[0])
            lines: list[dict[str, Any]] = []
            details: list[dict[str, Any]] = []
            dropped_counts = {"zero_length": 0, "numerical_residue": 0}
            for local_index, view in enumerate(selected_views):
                source_index = start + local_index
                source, loop_indices = sources[source_index], loop_maps[source_index]
                first_line = len(lines)
                lines.extend(_attribute(line, source, loop_indices) for line in view["lines"])
                details.extend(_attribute(detail, source, loop_indices)
                               for detail in view["dropped_projected_edge_details"])
                for reason in dropped_counts:
                    dropped_counts[reason] += view["dropped_projected_edge_counts"][reason]
                if include_dxf:
                    dxf_ranges.append({"view": name, "source_index": source_index,
                                       "source_refno": source["source_refno"],
                                       "line_entity_range": [line_entity_index, line_entity_index + len(view["lines"])],
                                       "view_line_range": [first_line, len(lines)]})
                line_entity_index += len(view["lines"])
            source_bounds = _bounds(selected_views)
            merged.update(
                bounds=deepcopy(global_bounds[name]), dimensions=_dimensions(global_bounds[name]),
                dimension_label="GLOBAL SAMPLED EXTENT", bounds_scope="all requested sources",
                bounds_semantics="global sampled chord approximation; analytic extrema may lie between samples",
                source_bounds=source_bounds, source_dimensions=_dimensions(source_bounds),
                lines=lines, line_count=len(lines), dropped_projected_edges=sum(dropped_counts.values()),
                dropped_projected_edge_counts=dropped_counts, dropped_projected_edge_details=details,
            )
            page_views.append(merged)
        loops = [_attribute(loop, sources[index], loop_maps[index])
                 for index in range(start, end) for loop in drawings[index]["loops"]]
        page_sources = deepcopy(selected_sources)
        drawing = {
            "success": True, "available": True, "complete": True,
            "drawing_kind": PANEL_CURVE_ASSEMBLY_DRAWING_KIND,
            "drawing_success_scope": PANEL_CURVE_ASSEMBLY_SUCCESS_SCOPE,
            "boundary_source": "projected_native_definition_plane_curves",
            "approximation": "bounded-chord polyline", "chord_tolerance_mm": tolerance,
            "coordinate_space": "WORLD", "coordinate_unit": "mm", "coordinate_frame": frame,
            "world_position_frame": frame,
            "source_count": end - start, "source_indices": list(range(start, end)), "source_range": [start, end],
            "sources": page_sources, "curve_counts": counts, "global_curve_counts": deepcopy(global_counts),
            "loop_count": counts["loop_count"], "curve_line_count": counts["line_count"],
            "arc_count": counts["arc_count"], "sampled_edge_count": counts["sampled_edge_count"],
            "segment_count": sum(view["line_count"] for view in page_views),
            "projected_line_count": sum(view["line_count"] for view in page_views),
            "global_projected_line_count": projected_count, "loops": loops, "views": page_views,
            "projection_degenerate_tolerance_mm": drawings[0]["projection_degenerate_tolerance_mm"],
            "projection_degenerate_filtering": drawings[0]["projection_degenerate_filtering"],
            "dropped_projected_edges": {view["name"]: view["dropped_projected_edges"] for view in page_views},
            "global_view_bounds": deepcopy(global_bounds),
            "shared_scale": True, "scale_policy": deepcopy(scale_policy),
            **_native_flags(page_sources), **_UNRESOLVED,
            "footer": footer,
        }
        if include_dxf:
            drawing["dxf_source_attribution"] = {
                "embedded": False,
                "method": "zero-based half-open LINE entity ranges in DXF export order; structured sidecar only",
                "ranges": dxf_ranges,
            }
        drawing = export_panel_curve_drawing(drawing, include_svg=include_svg, include_dxf=include_dxf)
        pages.append({"page_number": len(pages) + 1, "source_range": [start, end],
                      "source_indices": list(range(start, end)), "source_count": end - start, "drawing": drawing})

    return {
        "success": True, "available": True, "complete": True,
        "drawing_kind": PANEL_CURVE_ASSEMBLY_DRAWING_KIND,
        "drawing_success_scope": PANEL_CURVE_ASSEMBLY_SUCCESS_SCOPE,
        "boundary_source": "projected_native_definition_plane_curves",
        "approximation": "bounded-chord polyline", "chord_tolerance_mm": tolerance,
        "coordinate_space": "WORLD", "coordinate_unit": "mm", "coordinate_frame": frame,
        "world_position_frame": frame,
        "source_count": len(sources), "sources": sources,
        "page_count": len(pages), "panels_per_page": panels_per_page, "pages": pages,
        "curve_counts": global_counts, "global_curve_counts": deepcopy(global_counts),
        "global_projected_line_count": projected_count,
        "global_view_bounds": global_bounds,
        "shared_scale": True, "scale_policy": scale_policy,
        "limits": {"sources": _MAX_SOURCES, "analytic_segments": _MAX_SEGMENTS,
                   "sampled_edges": _MAX_SAMPLED_EDGES, "projected_lines": _MAX_PROJECTED_LINES},
        **_native_flags(sources), **_UNRESOLVED,
    }
