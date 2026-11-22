"""Plan a common-scale WORLD three-view and independent plane-section sheet.

Only FRONT/TOP/SIDE share WORLD projection datums. The SECTION cell holds its
own plane-relative millimetres and is centered without inventing axis links.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from fractions import Fraction
from typing import Any

from .drawing_assembly import WORLD_FRAME
from .projection_sheet_layout import _auto_denominator, plan_projection_sheet


_NAMES = ("FRONT", "TOP", "SIDE", "SECTION")
_DEFAULT_MARGINS = {"left": 16.0, "right": 6.0, "bottom": 16.0, "top": 12.0}
_BORDER, _BODY_BOTTOM, _GAP = 10.0, 50.0, 8.0
_FRAME_TOLERANCE = 1e-10
_MODEL_LIMIT = 1_000_000_000.0


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("composite_sheet_invalid_number")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise ValueError("composite_sheet_invalid_number") from None
    if not math.isfinite(result):
        raise ValueError("composite_sheet_invalid_number")
    return 0.0 if result == 0 else result


def _point(value: Any, length: int = 2) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError("composite_sheet_invalid_coordinates")
    result = [_number(component) for component in value]
    if any(abs(component) > _MODEL_LIMIT for component in result):
        raise ValueError("composite_sheet_model_coordinate_out_of_bounds")
    return result


def _named_views(values: Any) -> dict[str, Mapping]:
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        raise ValueError("composite_sheet_four_views_required")
    result = {}
    for value in values:
        name = value.get("name") if isinstance(value, Mapping) else None
        if not isinstance(name, str) or name not in _NAMES or name in result:
            raise ValueError("composite_sheet_four_views_required")
        result[name] = value
    return result


def _section_frame(raw: Mapping, drawing: Mapping) -> dict[str, list[float]]:
    frame = raw.get("coordinate_frame")
    if (raw.get("coordinate_unit") != "mm" or not isinstance(frame, str) or not frame.strip() or
            frame == drawing.get("coordinate_frame")):
        raise ValueError("composite_sheet_independent_section_frame_required")
    plane = drawing.get("section_plane")
    if not isinstance(plane, Mapping) or not {"origin", "right", "up", "normal"} <= set(plane):
        raise ValueError("composite_sheet_section_plane_required")
    vectors = {key: _point(plane[key], 3) for key in ("origin", "right", "up", "normal")}
    if _point(raw.get("origin"), 3) != vectors["origin"]:
        raise ValueError("composite_sheet_section_origin_mismatch")
    for key, field in (("right", "horizontal_axis"), ("up", "vertical_axis"), ("normal", "toward_viewer")):
        if _point(raw.get(field), 3) != vectors[key]:
            raise ValueError("composite_sheet_section_axes_mismatch")
        if abs(math.hypot(*vectors[key]) - 1) > _FRAME_TOLERANCE:
            raise ValueError("composite_sheet_section_orthonormal_frame_required")
    right, up, normal = (vectors[key] for key in ("right", "up", "normal"))
    for first, second in ((right, up), (right, normal), (up, normal)):
        if abs(math.fsum(a * b for a, b in zip(first, second))) > _FRAME_TOLERANCE:
            raise ValueError("composite_sheet_section_orthonormal_frame_required")
    cross = [right[1] * up[2] - right[2] * up[1], right[2] * up[0] - right[0] * up[2],
             right[0] * up[1] - right[1] * up[0]]
    if any(abs(a - b) > _FRAME_TOLERANCE for a, b in zip(cross, normal)):
        raise ValueError("composite_sheet_section_right_handed_frame_required")
    return vectors


def plan_composite_sheet(views: Any, drawing: Any, options: Any,
                         paper_width: Any, paper_height: Any,
                         section_margins: Any = None) -> dict[str, Any]:
    """Place four verified views with one scale and bounded annotation margins.

    ``views`` are prepared drawing-sheet rows. SECTION carries independent u/v
    bounds and its raw ``origin``/axes must match ``drawing.section_plane``.
    ``section_margins`` reserves paper millimetres for local dimension lanes;
    callers must supply those margins when prepared SECTION dimensions exist.
    Shared WORLD coordinates are anchored to canonical union bounds exactly as
    in the existing projection planner. Inputs are neither edited nor aliased.
    """
    if not isinstance(options, Mapping) or options.get("projection_method") not in ("first_angle", "third_angle"):
        raise ValueError("composite_sheet_invalid_method")
    if (not isinstance(drawing, Mapping) or drawing.get("coordinate_unit") != "mm" or
            drawing.get("coordinate_frame") != WORLD_FRAME):
        raise ValueError("composite_sheet_world_millimetres_required")
    prepared, raw = _named_views(views), _named_views(drawing.get("views"))
    plane = _section_frame(raw["SECTION"], drawing)
    section = prepared["SECTION"]
    minimum, maximum = _point(section.get("minimum")), _point(section.get("maximum"))
    if any(maximum[axis] <= minimum[axis] for axis in (0, 1)):
        raise ValueError("composite_sheet_degenerate_section_view")
    section_width, section_height = (maximum[axis] - minimum[axis] for axis in (0, 1))
    if (_number(section.get("width")) != section_width or _number(section.get("height")) != section_height):
        raise ValueError("composite_sheet_prepared_section_bounds_mismatch")
    raw_bounds = raw["SECTION"].get("bounds")
    if (not isinstance(raw_bounds, Mapping) or _point(raw_bounds.get("min")) != minimum or
            _point(raw_bounds.get("max")) != maximum):
        raise ValueError("composite_sheet_prepared_section_bounds_mismatch")
    if section_margins is None:
        if section.get("linear_dimensions") is not None:
            raise ValueError("composite_sheet_section_dimension_margins_required")
        margins = dict(_DEFAULT_MARGINS)
    else:
        if not isinstance(section_margins, Mapping) or set(section_margins) != set(_DEFAULT_MARGINS):
            raise ValueError("composite_sheet_invalid_section_margins")
        margins = {side: _number(section_margins[side]) for side in _DEFAULT_MARGINS}
        if any(value < _DEFAULT_MARGINS[side] for side, value in margins.items()):
            raise ValueError("composite_sheet_insufficient_section_margins")
    width, height = _number(paper_width), _number(paper_height)

    # Reuse the proven three-view checks/canonicalization with a temporary
    # three-view envelope. Its auto-fit scene does not constrain our final N.
    orthographic = {**drawing, "views": [raw[name] for name in _NAMES[:3]]}
    checked = plan_projection_sheet([prepared[name] for name in _NAMES[:3]], orthographic,
                                    {"projection_method": options["projection_method"]}, width, height)
    axes = checked["canonical_axis_bounds_model_mm"]
    extents = {axis: Fraction(interval["max"]) - Fraction(interval["min"]) for axis, interval in axes.items()}
    section_extents = [Fraction(maximum[axis]) - Fraction(minimum[axis]) for axis in (0, 1)]
    # The second column/row contains SECTION and must reserve the larger
    # geometry extent as well as the maximum annotation margin on each side.
    column_extents = [extents["X"], max(extents["Y"], section_extents[0])]
    row_extents = [extents["Z"], max(extents["Y"], section_extents[1])]
    column_margins = [dict(_DEFAULT_MARGINS),
                      {side: max(_DEFAULT_MARGINS[side], margins[side]) for side in ("left", "right")}]
    row_margins = [dict(_DEFAULT_MARGINS),
                   {side: max(_DEFAULT_MARGINS[side], margins[side]) for side in ("bottom", "top")}]
    available_width = (Fraction(width) - 2 * Fraction(_BORDER) - Fraction(_GAP) -
                       sum(Fraction(item["left"]) + Fraction(item["right"]) for item in column_margins))
    available_height = (Fraction(height) - Fraction(_BORDER) - Fraction(_BODY_BOTTOM) - Fraction(_GAP) -
                        sum(Fraction(item["bottom"]) + Fraction(item["top"]) for item in row_margins))
    if available_width <= 0 or available_height <= 0:
        raise ValueError("composite_sheet_content_area_unavailable")
    required = max(sum(column_extents) / available_width, sum(row_extents) / available_height)
    if "scale_denominator" in options:
        denominator = _number(options["scale_denominator"])
        if denominator <= 0:
            raise ValueError("composite_sheet_invalid_scale_denominator")
        if Fraction(denominator) < required:
            raise ValueError("composite_sheet_scale_does_not_fit")
    else:
        denominator = _auto_denominator(required)
    column_geometry = [float(value) / denominator for value in column_extents]
    row_geometry = [float(value) / denominator for value in row_extents]
    if any(not math.isfinite(value) or value <= 0 for value in (*column_geometry, *row_geometry)):
        raise ValueError("composite_sheet_geometry_not_representable")
    column_sizes = [extent + item["left"] + item["right"] for extent, item in zip(column_geometry, column_margins)]
    row_sizes = [extent + item["bottom"] + item["top"] for extent, item in zip(row_geometry, row_margins)]
    pack_x = (width - sum(column_sizes) - _GAP) / 2
    pack_y = _BODY_BOTTOM + (height - _BORDER - _BODY_BOTTOM - sum(row_sizes) - _GAP) / 2
    # Logical column 0 is FRONT/TOP, row 0 is FRONT/SIDE. First-angle flips
    # both physical positions without changing physical view direction.
    if options["projection_method"] == "third_angle":
        columns = [pack_x, pack_x + column_sizes[0] + _GAP]
        rows = [pack_y, pack_y + row_sizes[0] + _GAP]
    else:
        columns = [pack_x + column_sizes[1] + _GAP, pack_x]
        rows = [pack_y + row_sizes[1] + _GAP, pack_y]
    positions = {"FRONT": (0, 0), "TOP": (0, 1), "SIDE": (1, 0), "SECTION": (1, 1)}
    model_axes = {"FRONT": ("X", "Z"), "TOP": ("X", "Y"), "SIDE": ("Y", "Z")}
    planned = {}
    for name in _NAMES:
        column, row = positions[name]
        x, y = columns[column], rows[row]
        cell_width, cell_height = column_sizes[column], row_sizes[row]
        bounds = {"min": [x, y], "max": [x + cell_width, y + cell_height]}
        if name == "SECTION":
            low, high = minimum[:], maximum[:]
        else:
            low = [axes[axis]["min"] for axis in model_axes[name]]
            high = [axes[axis]["max"] for axis in model_axes[name]]
        spans = [(high[axis] - low[axis]) / denominator for axis in (0, 1)]
        origin = [x + column_margins[column]["left"] + (column_geometry[column] - spans[0]) / 2,
                  y + row_margins[row]["bottom"] + (row_geometry[row] - spans[1]) / 2]
        if any(not math.isfinite(value) for value in (*origin, *bounds["min"], *bounds["max"])):
            raise ValueError("composite_sheet_geometry_not_representable")
        if (bounds["min"][0] < _BORDER - 1e-9 or bounds["max"][0] > width - _BORDER + 1e-9 or
                bounds["min"][1] < _BODY_BOTTOM - 1e-9 or bounds["max"][1] > height - _BORDER + 1e-9):
            raise ValueError("composite_sheet_scale_does_not_fit")
        for axis in (0, 1):
            far = origin[axis] + spans[axis]
            if (not math.isfinite(far) or format(far, ".6f") == format(origin[axis], ".6f") or
                    (axis == 1 and format(height - far, ".6f") == format(height - origin[axis], ".6f"))):
                raise ValueError("composite_sheet_geometry_below_paper_precision")
        planned[name] = {
            "cell_bounds_mm": bounds, "paper_origin_mm": origin,
            "heading_position_mm": [x + cell_width / 2, y + cell_height - 5],
            "canonical_model_bounds": {"min": low, "max": high},
            "cell_margins_mm": {"left": origin[0] - x, "right": x + cell_width - origin[0] - spans[0],
                                "bottom": origin[1] - y, "top": y + cell_height - origin[1] - spans[1]},
        }
    return {
        "scale_denominator": denominator, "views": planned,
        "projection_method": options["projection_method"], "side_view": "right",
        "canonical_axis_bounds_model_mm": axes,
        "section_plane": plane,
        "section_alignment": "independent plane-relative u/v millimetres; centered in its cell",
        "layout_policy": options["projection_method"] + "; shared WORLD X/Z datums; independent SECTION in fourth cell",
        "canonical_bounds_policy": checked["canonical_bounds_policy"],
        "cell_gap_mm": _GAP, "section_margins_mm": margins,
    }
