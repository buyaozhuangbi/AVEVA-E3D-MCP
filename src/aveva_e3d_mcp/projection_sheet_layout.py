"""Optional physically aligned first/third-angle paper layout planning.

The input linework must already use the declared right-handed orthographic
view bases. This planner never repairs view direction or hidden-line results.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from fractions import Fraction
from typing import Any


_NAMES = ("FRONT", "TOP", "SIDE")
_BASES = {
    "FRONT": ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "TOP": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "SIDE": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
}
_BORDER = 10.0
_CONTENT_BOTTOM = 50.0
_GAP = 8.0
_LEFT, _RIGHT, _BOTTOM, _TOP = 16.0, 6.0, 16.0, 12.0
_MAX_MODEL_COORDINATE = 1_000_000_000.0


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("projection_sheet_invalid_number")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise ValueError("projection_sheet_invalid_number") from None
    if not math.isfinite(result):
        raise ValueError("projection_sheet_invalid_number")
    return 0.0 if result == 0 else result


def _point(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("projection_sheet_invalid_bounds")
    result = [_number(component) for component in value]
    if any(abs(component) > _MAX_MODEL_COORDINATE for component in result):
        raise ValueError("projection_sheet_model_coordinate_out_of_bounds")
    return result


def _basis(value: Any, expected) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == 3 and
            all(not isinstance(component, bool) and isinstance(component, (int, float)) and component == reference
                for component, reference in zip(value, expected)))


def _auto_denominator(required: Fraction) -> float:
    required = max(Fraction(1), required)
    exponent = math.floor(math.log10(float(required)))
    base = 10.0 ** exponent
    for multiple in (1.0, 2.0, 5.0, 10.0, 20.0):
        candidate = multiple * base
        if math.isfinite(candidate) and Fraction(candidate) >= required:
            return candidate
    raise ValueError("projection_sheet_scale_unavailable")


def plan_projection_sheet(views: Any, drawing: Any, options: Any,
                          paper_width: Any, paper_height: Any, *,
                          coordinate_decimal_places: int = 6) -> dict[str, Any]:
    """Plan three common-scale views without changing source or old layouts.

    Prepared view bounds must match the raw drawing and share a declared
    coordinate frame and exact physical bases. Nearly equal shared intervals
    are explicitly canonicalized to their union, retaining all admitted model
    extents. Returned origins anchor ``canonical_model_bounds.min``; renderers
    must use that common datum rather than independent per-view minima.
    Overall paper extents use this call's 6–15 decimal coordinate precision;
    the default retains the existing six-place representability guard.
    """
    if type(coordinate_decimal_places) is not int or not 6 <= coordinate_decimal_places <= 15:
        raise ValueError("invalid_coordinate_decimal_places")
    coordinate_format = f".{coordinate_decimal_places}f"
    if not isinstance(options, Mapping) or options.get("projection_method") not in ("first_angle", "third_angle"):
        raise ValueError("projection_sheet_invalid_method")
    if not isinstance(drawing, Mapping) or drawing.get("projection_convention") != "right_handed":
        raise ValueError("projection_sheet_right_handed_drawing_required")
    frame = drawing.get("coordinate_frame")
    if not isinstance(frame, str) or not frame.strip() or drawing.get("coordinate_unit") != "mm":
        raise ValueError("projection_sheet_shared_model_frame_required")
    raw_views = drawing.get("views")
    if not isinstance(raw_views, (list, tuple)) or len(raw_views) != 3:
        raise ValueError("projection_sheet_three_views_required")
    raw_by_name = {}
    for row in raw_views:
        name = row.get("name") if isinstance(row, Mapping) else None
        if not isinstance(name, str) or name not in _NAMES or name in raw_by_name:
            raise ValueError("projection_sheet_three_views_required")
        if row.get("coordinate_frame") != frame or row.get("coordinate_unit") != "mm":
            raise ValueError("projection_sheet_shared_model_frame_required")
        if not all(_basis(row.get(key), expected) for key, expected in zip(
                ("horizontal_axis", "vertical_axis", "toward_viewer"), _BASES[name])):
            raise ValueError("projection_sheet_view_basis_mismatch")
        raw_by_name[name] = row
    if not isinstance(views, (list, tuple)) or len(views) != 3:
        raise ValueError("projection_sheet_three_views_required")
    prepared = {}
    for row in views:
        name = row.get("name") if isinstance(row, Mapping) else None
        if not isinstance(name, str) or name not in _NAMES or name in prepared:
            raise ValueError("projection_sheet_three_views_required")
        minimum, maximum = _point(row.get("minimum")), _point(row.get("maximum"))
        if any(maximum[axis] <= minimum[axis] for axis in (0, 1)):
            raise ValueError("projection_sheet_degenerate_view")
        width, height = maximum[0] - minimum[0], maximum[1] - minimum[1]
        if _number(row.get("width")) != width or _number(row.get("height")) != height:
            raise ValueError("projection_sheet_prepared_bounds_mismatch")
        raw_bounds = raw_by_name[name].get("bounds")
        if (not isinstance(raw_bounds, Mapping) or _point(raw_bounds.get("min")) != minimum or
                _point(raw_bounds.get("max")) != maximum):
            raise ValueError("projection_sheet_prepared_bounds_mismatch")
        if row.get("linear_dimensions") is not None:
            raise ValueError("projection_sheet_section_dimensions_unsupported")
        prepared[name] = {"minimum": minimum, "maximum": maximum}

    def shared_interval(first, first_axis, second, second_axis):
        left = [prepared[first][key][first_axis] for key in ("minimum", "maximum")]
        right = [prepared[second][key][second_axis] for key in ("minimum", "maximum")]
        tolerance = max(1e-9, (left[1] - left[0]) * 1e-9, (right[1] - right[0]) * 1e-9,
                        4 * max(math.ulp(value) for value in (*left, *right)))
        if any(abs(a - b) > tolerance for a, b in zip(left, right)):
            raise ValueError("projection_sheet_shared_axis_bounds_mismatch")
        return {"min": min(left[0], right[0]), "max": max(left[1], right[1]),
                "tolerance_mm": tolerance, "canonicalized": left != right}

    axes = {"X": shared_interval("FRONT", 0, "TOP", 0),
            "Y": shared_interval("TOP", 1, "SIDE", 0),
            "Z": shared_interval("FRONT", 1, "SIDE", 1)}
    extents = {axis: Fraction(value["max"]) - Fraction(value["min"]) for axis, value in axes.items()}
    width, height = _number(paper_width), _number(paper_height)
    available_width = width - 2 * _BORDER - 2 * (_LEFT + _RIGHT) - _GAP
    available_height = height - _BORDER - _CONTENT_BOTTOM - 2 * (_BOTTOM + _TOP) - _GAP
    if available_width <= 0 or available_height <= 0:
        raise ValueError("projection_sheet_content_area_unavailable")
    required = max((extents["X"] + extents["Y"]) / Fraction(available_width),
                   (extents["Z"] + extents["Y"]) / Fraction(available_height))
    if "scale_denominator" in options:
        denominator = _number(options["scale_denominator"])
        if denominator <= 0:
            raise ValueError("projection_sheet_invalid_scale_denominator")
        if Fraction(denominator) < required:
            raise ValueError("projection_sheet_scale_does_not_fit")
    else:
        denominator = _auto_denominator(required)
    model_width, model_depth, model_height = (float(extents[axis]) / denominator for axis in ("X", "Y", "Z"))
    if any(not math.isfinite(value) or value <= 0 for value in (model_width, model_depth, model_height)):
        raise ValueError("projection_sheet_geometry_not_representable")
    front_width, side_width = model_width + _LEFT + _RIGHT, model_depth + _LEFT + _RIGHT
    front_height, top_height = model_height + _BOTTOM + _TOP, model_depth + _BOTTOM + _TOP
    packed_width, packed_height = front_width + side_width + _GAP, front_height + top_height + _GAP
    pack_x = (width - packed_width) / 2
    pack_y = _CONTENT_BOTTOM + (height - _BORDER - _CONTENT_BOTTOM - packed_height) / 2
    if options["projection_method"] == "third_angle":
        front_x, front_y = pack_x, pack_y
        top_x, top_y = front_x, front_y + front_height + _GAP
        side_x, side_y = front_x + front_width + _GAP, front_y
    else:
        side_x, top_y = pack_x, pack_y
        front_x = side_x + side_width + _GAP
        front_y = top_y + top_height + _GAP
        side_y, top_x = front_y, front_x
    origins = {"FRONT": [front_x + _LEFT, front_y + _BOTTOM],
               "TOP": [front_x + _LEFT, top_y + _BOTTOM],
               "SIDE": [side_x + _LEFT, front_y + _BOTTOM]}
    cells = {"FRONT": (front_x, front_y, front_width, front_height),
             "TOP": (top_x, top_y, front_width, top_height),
             "SIDE": (side_x, side_y, side_width, front_height)}
    model_axes = {"FRONT": ("X", "Z"), "TOP": ("X", "Y"), "SIDE": ("Y", "Z")}
    planned = {}
    for name in _NAMES:
        x, y, cell_width, cell_height = cells[name]
        a, b = model_axes[name]
        minimum, maximum = [axes[axis]["min"] for axis in (a, b)], [axes[axis]["max"] for axis in (a, b)]
        bounds = {"min": [x, y], "max": [x + cell_width, y + cell_height]}
        origin = origins[name]
        if any(not math.isfinite(value) for value in (*origin, *bounds["min"], *bounds["max"])):
            raise ValueError("projection_sheet_geometry_not_representable")
        if (bounds["min"][0] < _BORDER - 1e-9 or bounds["max"][0] > width - _BORDER + 1e-9 or
                bounds["min"][1] < _CONTENT_BOTTOM - 1e-9 or bounds["max"][1] > height - _BORDER + 1e-9):
            raise ValueError("projection_sheet_scale_does_not_fit")
        for axis in (0, 1):
            far = origin[axis] + float(extents[(a, b)[axis]]) / denominator
            if not math.isfinite(far) or format(far, coordinate_format) == format(origin[axis], coordinate_format):
                raise ValueError("projection_sheet_geometry_below_paper_precision")
        planned[name] = {"cell_bounds_mm": bounds, "paper_origin_mm": origin,
                         "heading_position_mm": [x + cell_width / 2, y + cell_height - 5.0],
                         "canonical_model_bounds": {"min": minimum, "max": maximum}}
    return {"scale_denominator": denominator, "views": planned,
            "layout_policy": options["projection_method"] + "; shared X/Z datums; right-handed FRONT/TOP/right SIDE",
            "projection_method": options["projection_method"], "side_view": "right",
            "canonical_axis_bounds_model_mm": axes,
            "canonical_bounds_policy": "shared bounds must agree within explicit tolerance; canonical interval is their union",
            "cell_margins_mm": {"left": _LEFT, "right": _RIGHT, "bottom": _BOTTOM, "top": _TOP},
            "cell_gap_mm": _GAP}
