"""Paper-mm scenes for explicitly requested aligned projection arrangements.

The projection generator owns viewing directions and visibility. This module
only arranges its verified right-handed FRONT/TOP/SIDE views on one sheet.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


_AXES = {
    "FRONT": ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "TOP": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "SIDE": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
}
_EPSILON = 1e-8


def _physical_views(drawing):
    if not isinstance(drawing, Mapping) or drawing.get("projection_convention") != "right_handed":
        raise ValueError("projection_sheet_right_handed_convention_required")
    views = drawing.get("views")
    if (not isinstance(views, (list, tuple)) or len(views) != 3 or
            any(not isinstance(view, Mapping) for view in views) or
            any(not isinstance(view.get("name"), str) for view in views) or
            {view.get("name") for view in views} != set(_AXES)):
        raise ValueError("projection_sheet_three_orthographic_views_required")
    physical = {}
    for view in views:
        if "linear_dimensions" in view:
            raise ValueError("projection_sheet_linear_dimensions_unsupported")
        name = view["name"]
        axes = {}
        for field, expected in zip(("horizontal_axis", "vertical_axis", "toward_viewer"), _AXES[name]):
            values = view.get(field)
            if (not isinstance(values, (list, tuple)) or len(values) != 3 or
                    any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values) or
                    tuple(values) != expected):
                raise ValueError("projection_sheet_physical_axes_mismatch")
            axes[field] = list(expected)
        physical[name] = axes
    return physical


def _text_box(item):
    width = (6 * len(item["text"]) - 1) / 7 * item["height"] if item["text"] else 0.0
    dx, dy = (item["height"], width) if item["rotation"] == 90 else (width, item["height"])
    x, y = item["position"]
    return (x - dx / 2, y - dy / 2, x + dx / 2, y + dy / 2)


def _line_crosses_text(start, end, box):
    low, high = 0.0, 1.0
    for axis in (0, 1):
        delta = end[axis] - start[axis]
        if not delta:
            if not box[axis] < start[axis] < box[axis + 2]:
                return False
        else:
            limits = sorted(((box[axis] - start[axis]) / delta, (box[axis + 2] - start[axis]) / delta))
            low, high = max(low, limits[0]), min(high, limits[1])
            if low >= high:
                return False
    return low < high


def _validate_annotations(strokes, texts, cells):
    boxes = [_text_box(item) for item in texts]
    for index, item in enumerate(texts):
        name = item.get("view_name")
        if name is None:
            continue
        box, cell = boxes[index], cells[name]
        if (box[0] < cell["min"][0] + 1 - _EPSILON or box[1] < cell["min"][1] + 1 - _EPSILON or
                box[2] > cell["max"][0] - 1 + _EPSILON or box[3] > cell["max"][1] - 1 + _EPSILON):
            raise ValueError("projection_sheet_label_outside_cell")
        if item["role"] == "dimension":
            for other_index, other in enumerate(boxes):
                if (other_index != index and min(box[2], other[2]) > max(box[0], other[0]) and
                        min(box[3], other[3]) > max(box[1], other[1])):
                    raise ValueError("projection_sheet_label_collision")
    for stroke in strokes:
        if stroke.get("view_name") is not None:
            cell = cells[stroke["view_name"]]
            if any(not cell["min"][axis] + 1 - _EPSILON <= point[axis] <= cell["max"][axis] - 1 + _EPSILON
                   for point in (stroke["start"], stroke["end"]) for axis in (0, 1)):
                raise ValueError("projection_sheet_stroke_outside_cell")
        if stroke["role"] == "dimension" and any(
                _line_crosses_text(stroke["start"], stroke["end"], box) for box in boxes):
            raise ValueError("projection_sheet_label_line_collision")


def build_projection_sheet(drawing: Any, options: dict[str, Any], *, coordinate_decimal_places: int = 6) -> dict[str, Any]:
    """Build an opt-in aligned scene; the existing default scene is untouched."""
    from .drawing_sheet import (
        MAX_SHEET_STROKES, MAX_SHEET_TEXTS, _PAPERS, _dimension_label,
        _fitted_height, _prepare_views, _scale_label, _validate_coordinate_decimal_places,
    )
    from .projection_sheet_layout import plan_projection_sheet

    _validate_coordinate_decimal_places(coordinate_decimal_places)
    axes = _physical_views(drawing)
    views = _prepare_views(drawing)
    method = options["projection_method"]
    short_side, long_side = _PAPERS[options["paper_size"]]
    width, height = ((long_side, short_side) if options["orientation"] == "landscape" else (short_side, long_side))
    plan = plan_projection_sheet(views, drawing, options, width, height,
                                 coordinate_decimal_places=coordinate_decimal_places)
    denominator = plan["scale_denominator"]
    if not math.isfinite(denominator) or denominator <= 0:
        raise ValueError("projection_sheet_invalid_scale")
    strokes, texts = [], []
    cells = {name: item["cell_bounds_mm"] for name, item in plan["views"].items()}

    def stroke(start, end, role, name=None, visibility="visible", **metadata):
        if any(not math.isfinite(value) or not -_EPSILON <= value <= limit + _EPSILON
               for point in (start, end) for value, limit in zip(point, (width, height))):
            raise ValueError("projection_sheet_layout_out_of_bounds")
        # DXF uses paper coordinates while SVG serializes the actual floating
        # subtraction H-Y before formatting. At an ULP rounding boundary those
        # two representations can collapse different endpoints. Check both
        # for every role, including dimensions and page/titleblock strokes.
        # Parsing the formatted numbers also equates the renderers' signed
        # zero spellings instead of admitting "-0.000000" versus "0.000000".
        encoded = lambda point: tuple(float(format(value, f".{coordinate_decimal_places}f")) for value in point)
        svg_start, svg_end = (start[0], height - start[1]), (end[0], height - end[1])
        if encoded(start) == encoded(end) or encoded(svg_start) == encoded(svg_end):
            raise ValueError("projection_sheet_geometry_below_precision")
        strokes.append({"start": list(start), "end": list(end), "role": role, "view_name": name,
                        "visibility": visibility, **metadata})

    def text(position, value, preferred_height, role, name=None, rotation=0, maximum_width=None, **metadata):
        fitted = _fitted_height(value, maximum_width if maximum_width is not None else width - 24, preferred_height)
        if role in {"dimension", "view_label"} and fitted != preferred_height:
            raise ValueError("projection_sheet_annotation_does_not_fit")
        item = {"position": list(position), "text": value, "height": fitted, "rotation": float(rotation),
                "role": role, "view_name": name, **metadata}
        left, bottom, right, top = _text_box(item)
        if (left < 10 - _EPSILON or bottom < 10 - _EPSILON or
                right > width - 10 + _EPSILON or top > height - 10 + _EPSILON):
            raise ValueError("projection_sheet_text_out_of_bounds")
        texts.append(item)

    corners = ((10, 10), (width - 10, 10), (width - 10, height - 10), (10, height - 10))
    for index in range(4):
        stroke(corners[index], corners[(index + 1) % 4], "border")
    for y in (40, 25):
        stroke((10, y), (width - 10, y), "title_block")
    body_width = width - 20
    for x in (10 + body_width * .5, 10 + body_width * .75):
        stroke((x, 10), (x, 25), "title_block")
    text((width / 2, 32.5), options["title"], 4, "title", maximum_width=body_width - 8)
    text((10 + body_width * .25, 17.5), "NO " + (options["drawing_number"] or "UNASSIGNED"),
         2.5, "drawing_number", maximum_width=body_width * .5 - 6)
    text((10 + body_width * .625, 17.5), _scale_label(denominator), 2.5, "sheet_metadata",
         maximum_width=body_width * .25 - 6)
    text((10 + body_width * .875, 17.5), options["paper_size"] + " " + options["orientation"].upper(),
         2.2, "sheet_metadata", maximum_width=body_width * .25 - 6)
    label = "FIRST ANGLE" if method == "first_angle" else "THIRD ANGLE"
    text((width / 2, 45), label + " / PARAMETER MESH / NOT NATIVE DRAFT / MODEL MM", 2, "disclaimer")

    placed_views = []
    for view in views:
        name = view["name"]
        placement = plan["views"][name]
        cell = placement["cell_bounds_mm"]
        cell_width, cell_height = (cell["max"][axis] - cell["min"][axis] for axis in (0, 1))
        canonical = placement["canonical_model_bounds"]
        origin = placement["paper_origin_mm"]
        project = lambda point: [origin[axis] + (point[axis] - canonical["min"][axis]) / denominator for axis in (0, 1)]
        paper_min, paper_max = project(view["minimum"]), project(view["maximum"])
        stroke_start, text_start = len(strokes), len(texts)
        text(placement["heading_position_mm"], name, 3, "view_label", name, maximum_width=cell_width - 2)
        for line in view["lines"]:
            stroke(project(line["start"]), project(line["end"]), line["kind"], name, line["visibility"],
                   source_line_index=line["source_line_index"])
        for axis, extent in ((0, view["width"]), (1, view["height"])):
            if not extent:
                continue
            low, high = paper_min[axis], paper_max[axis]
            if format(low, f".{coordinate_decimal_places}f") == format(high, f".{coordinate_decimal_places}f"):
                raise ValueError("projection_sheet_dimension_below_paper_precision")
            coordinate = paper_min[1 - axis] - 7
            along = lambda value, level=coordinate: (value, level) if axis == 0 else (level, value)
            for point in (paper_min, [paper_max[0], paper_min[1]] if axis == 0 else [paper_min[0], paper_max[1]]):
                stroke(point, along(point[axis], coordinate - 1), "dimension", name, dimension_part="extension")
            outward = high - low < 6
            stroke(along(low - 2 if outward else low), along(high + 2 if outward else high),
                   "dimension", name, dimension_part="line")
            for tip, direction in ((low, -1 if outward else 1), (high, 1 if outward else -1)):
                for lateral in (-1, 1):
                    stroke(along(tip), along(tip + direction * 2, coordinate + lateral * .64),
                           "dimension", name, dimension_part="arrow")
            text(along((low + high) / 2, coordinate - 4), _dimension_label(extent), 2.2, "dimension", name,
                 rotation=0 if axis == 0 else 90, maximum_width=(cell_width if axis == 0 else cell_height) - 2,
                 dimension_axis="width" if axis == 0 else "height", dimension_value_mm=extent,
                 arrow_direction="outward" if outward else "inward")
        placed_views.append({
            "name": name, "model_bounds": {"min": list(view["minimum"]), "max": list(view["maximum"])},
            "canonical_model_bounds": canonical,
            "model_dimensions_mm": {"width": view["width"], "height": view["height"]},
            "paper_bounds_mm": {"min": paper_min, "max": paper_max}, "cell_bounds_mm": cell,
            "transform": {"model_min": list(canonical["min"]), "paper_origin_mm": list(origin),
                          "scale_denominator": denominator, "paper_mm_per_model_mm": 1 / denominator},
            **axes[name], "stroke_range": [stroke_start, len(strokes)], "text_range": [text_start, len(texts)],
        })
    if len(strokes) > MAX_SHEET_STROKES or len(texts) > MAX_SHEET_TEXTS:
        raise ValueError("projection_sheet_output_limit_exceeded")
    _validate_annotations(strokes, texts, cells)
    return {
        "success": True, "available": True, "complete": True,
        "paper": {"size": options["paper_size"], "orientation": options["orientation"],
                  "width_mm": width, "height_mm": height},
        "paper_size_mm": [width, height], "coordinate_space": "paper", "coordinate_unit": "mm",
        "coordinate_frame": "paper millimetres; origin bottom-left; +X right; +Y up",
        "model_coordinate_unit": "mm", "source_coordinate_frame": drawing.get("coordinate_frame"),
        "projection_method": method, "projection_convention": "right_handed",
        "canonical_axis_bounds_model_mm": plan["canonical_axis_bounds_model_mm"],
        "scale_denominator": denominator, "scale_mode": "explicit" if "scale_denominator" in options else "auto_fit",
        "scale_scope": "1:N applies to model linework on paper; dimension labels retain model millimetres",
        "dimension_label_resolution_mm": 0.000001,
        "options": options, "views": placed_views, "strokes": strokes, "texts": texts,
        "counts": {"views": len(placed_views), "strokes": len(strokes), "texts": len(texts)},
        "text_alignment": "center", "text_character_policy": "uppercase ASCII A-Z 0-9 space . - + / : _",
        "layout_policy": plan["layout_policy"],
        "projection_standard_scope": "Declared view arrangement and physical viewing axes only; no complete ISO certification or official projection symbol.",
        "native_draft_sheet": False, "engineering_dimensions_certified": False,
    }
