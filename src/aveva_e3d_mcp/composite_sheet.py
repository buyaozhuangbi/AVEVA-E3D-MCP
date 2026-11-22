"""Common-scale orthographic views and an independent plane section on paper."""
from __future__ import annotations

from copy import deepcopy
import math
from collections.abc import Mapping
from typing import Any

from .projection_sheet import _physical_views, _text_box, _validate_annotations
from .section_hatch_precision import validate_section_hatch_precision


def build_composite_sheet(drawing: Any, options: dict[str, Any]) -> dict[str, Any]:
    """Build a complete four-view scene, or fail before returning any output."""
    from .drawing_sheet import (
        MAX_SHEET_STROKES, MAX_SHEET_TEXTS, _PAPERS, _dimension_label,
        _dimension_margins, _fitted_height, _prepare_views, _scale_label,
    )
    from .composite_sheet_layout import plan_composite_sheet

    if not isinstance(drawing, Mapping) or drawing.get("sheet_composition") != "orthographic_section":
        raise ValueError("composite_sheet_composition_required")
    if options.get("projection_method") not in {"first_angle", "third_angle"}:
        raise ValueError("composite_sheet_projection_method_required")
    raw_views = drawing.get("views")
    if (not isinstance(raw_views, (list, tuple)) or len(raw_views) != 4 or
            any(not isinstance(view, Mapping) or not isinstance(view.get("name"), str) for view in raw_views) or
            {view["name"] for view in raw_views} != {"FRONT", "TOP", "SIDE", "SECTION"}):
        raise ValueError("composite_sheet_four_views_required")
    physical = _physical_views({**drawing, "views": [view for view in raw_views if view["name"] != "SECTION"]})
    section_raw = next(view for view in raw_views if view["name"] == "SECTION")
    plane = drawing.get("section_plane")
    if (not isinstance(plane, Mapping) or
            not all(isinstance(plane.get(key), (list, tuple)) and len(plane[key]) == 3
                    for key in ("origin", "right", "up", "normal"))):
        raise ValueError("composite_sheet_section_plane_required")
    views = _prepare_views(drawing, max_views=4)
    section = next(view for view in views if view["name"] == "SECTION")
    default_margin = {"left": 16.0, "right": 6.0, "bottom": 16.0, "top": 12.0}
    section_margin = _dimension_margins(section["linear_dimensions"]) if section["linear_dimensions"] else default_margin
    section_margin = {key: max(default_margin[key], section_margin[key]) for key in default_margin}
    # _dimension_margins reserves lane_offset + 6 mm on the side. A rotated
    # vertical-dimension label is centered at lane_offset + 4 mm and is 2.2 mm
    # wide; after the sheet-cell's own 1 mm safe border, the reservation is
    # short by 0.1 mm. Add explicit clearance only for occupied lateral lanes.
    if section["linear_dimensions"]:
        for item in section["linear_dimensions"]:
            if item["side"] in {"left", "right"}:
                section_margin[item["side"]] += 0.5
    short_side, long_side = _PAPERS[options["paper_size"]]
    width, height = ((long_side, short_side) if options["orientation"] == "landscape" else (short_side, long_side))
    plan = plan_composite_sheet(views, drawing, options, width, height, section_margins=section_margin)
    denominator = plan["scale_denominator"]
    if not math.isfinite(denominator) or denominator <= 0:
        raise ValueError("composite_sheet_invalid_scale")
    strokes, texts, placed_dimensions = [], [], []
    cells = {name: item["cell_bounds_mm"] for name, item in plan["views"].items()}
    eps = 1e-8

    def stroke(start, end, role, name=None, visibility="visible", **metadata):
        if any(not math.isfinite(value) or not -eps <= value <= limit + eps
               for point in (start, end) for value, limit in zip(point, (width, height))):
            raise ValueError("composite_sheet_layout_out_of_bounds")
        encoded = lambda point: tuple(float(format(value, ".6f")) for value in point)
        if (encoded(start) == encoded(end) or
                encoded((start[0], height - start[1])) == encoded((end[0], height - end[1]))):
            raise ValueError("composite_sheet_geometry_below_precision")
        strokes.append({"start": list(start), "end": list(end), "role": role, "view_name": name,
                        "visibility": visibility, **metadata})

    def text(position, value, preferred_height, role, name=None, rotation=0, maximum_width=None, **metadata):
        fitted = _fitted_height(value, maximum_width if maximum_width is not None else width - 24, preferred_height)
        if role in {"dimension", "view_label"} and fitted != preferred_height:
            raise ValueError("composite_sheet_annotation_does_not_fit")
        item = {"position": list(position), "text": value, "height": fitted, "rotation": float(rotation),
                "role": role, "view_name": name, **metadata}
        left, bottom, right, top = _text_box(item)
        if left < 10 - eps or bottom < 10 - eps or right > width - 10 + eps or top > height - 10 + eps:
            raise ValueError("composite_sheet_text_out_of_bounds")
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
    method = options["projection_method"]
    label = "FIRST ANGLE" if method == "first_angle" else "THIRD ANGLE"
    text((width / 2, 47.2), label + " / FULL MODEL VIEWS + INDEPENDENT SECTION / NOT NATIVE DRAFT", 1.7, "disclaimer")
    short_vector = lambda values: " ".join(format(float(value), ".4g").upper() for value in values)
    plane_label = "SECTION WORLD O " + short_vector(plane["origin"]) + " MM / N " + short_vector(plane["normal"])
    if len(plane_label) > 160:
        plane_label = "SECTION PLANE METADATA IN SCENE"
    text((width / 2, 43.2), plane_label, 1.7, "disclaimer")
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
        if name == "SECTION":
            validate_section_hatch_precision(view["lines"], transform=project)
            validate_section_hatch_precision(view["lines"], transform=lambda point: (project(point)[0], height - project(point)[1]),
                                             transform_orientation=-1)
        for line in view["lines"]:
            stroke(project(line["start"]), project(line["end"]), line["kind"], name, line["visibility"],
                   source_line_index=line["source_line_index"])
        for axis, extent in ((0, view["width"]), (1, view["height"])):
            if not extent:
                continue
            low, high = paper_min[axis], paper_max[axis]
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
        for item in view["linear_dimensions"] or []:
            dimension_index = len(placed_dimensions)
            dim_stroke_start, dim_text_start = len(strokes), len(texts)
            first, second = project(item["start"]), project(item["end"])
            axis = 0 if item["axis"] == "horizontal" else 1
            low, high = sorted((first[axis], second[axis]))
            if float(format(low, ".6f")) == float(format(high, ".6f")):
                raise ValueError("composite_sheet_dimension_below_paper_precision")
            side = item["side"]
            sign = 1 if side in {"top", "right"} else -1
            coordinate = ({"top": paper_max[1], "bottom": paper_min[1], "left": paper_min[0], "right": paper_max[0]}[side] +
                          sign * item["lane_offset_mm"])
            along = lambda value, level=coordinate: (value, level) if axis == 0 else (level, value)
            metadata = {"dimension_index": dimension_index, "dimension_axis": item["axis"],
                        "dimension_side": side, "dimension_value_mm": item["value_mm"]}
            for endpoint in (first, second):
                stroke(endpoint, along(endpoint[axis], coordinate + sign), "dimension", name,
                       dimension_part="extension", **metadata)
            outward = high - low < 6
            stroke(along(low - 2 if outward else low), along(high + 2 if outward else high),
                   "dimension", name, dimension_part="line", **metadata)
            for tip, direction in ((low, -1 if outward else 1), (high, 1 if outward else -1)):
                for lateral in (-1, 1):
                    stroke(along(tip), along(tip + direction * 2, coordinate + lateral * .64),
                           "dimension", name, dimension_part="arrow", **metadata)
            label_position = along((low + high) / 2, coordinate + sign * 4)
            text(label_position, item["label"], 2.2, "dimension", name, rotation=0 if axis == 0 else 90,
                 maximum_width=(cell_width if axis == 0 else cell_height) - 2, **metadata)
            placed_dimensions.append({key: deepcopy(value) for key, value in item.items() if key != "label"} | {
                "dimension_index": dimension_index, "view_name": name,
                "paper_start_mm": list(first), "paper_end_mm": list(second),
                "dimension_line_start_mm": list(along(low)), "dimension_line_end_mm": list(along(high)),
                "label_position_mm": list(label_position), "label_height_mm": 2.2,
                "arrow_direction": "outward" if outward else "inward",
                "stroke_range": [dim_stroke_start, len(strokes)], "text_range": [dim_text_start, len(texts)],
            })
        placed_views.append({
            "name": name, "model_bounds": {"min": list(view["minimum"]), "max": list(view["maximum"])},
            "canonical_model_bounds": deepcopy(canonical),
            "model_dimensions_mm": {"width": view["width"], "height": view["height"]},
            "paper_bounds_mm": {"min": paper_min, "max": paper_max}, "cell_bounds_mm": deepcopy(cell),
            "transform": {"model_min": list(canonical["min"]), "paper_origin_mm": list(origin),
                          "scale_denominator": denominator, "paper_mm_per_model_mm": 1 / denominator},
            **(physical[name] if name != "SECTION" else {
                "horizontal_axis": deepcopy(plane["right"]), "vertical_axis": deepcopy(plane["up"]),
                "toward_viewer": deepcopy(plane["normal"])}),
            "stroke_range": [stroke_start, len(strokes)], "text_range": [text_start, len(texts)],
            **({"origin": deepcopy(plane["origin"]), "section_plane": deepcopy(dict(plane)),
                "coordinate_frame": section_raw["coordinate_frame"], "independent_section_plane": True}
               if name == "SECTION" else {"coordinate_frame": drawing["coordinate_frame"]}),
        })
    if len(strokes) > MAX_SHEET_STROKES or len(texts) > MAX_SHEET_TEXTS:
        raise ValueError("composite_sheet_output_limit_exceeded")
    try:
        _validate_annotations(strokes, texts, cells)
    except ValueError as exc:
        raise ValueError(str(exc).replace("projection_sheet_", "composite_sheet_")) from None
    return {
        "success": True, "available": True, "complete": True,
        "sheet_composition": "orthographic_section",
        "paper": {"size": options["paper_size"], "orientation": options["orientation"], "width_mm": width, "height_mm": height},
        "paper_size_mm": [width, height], "coordinate_space": "paper", "coordinate_unit": "mm",
        "coordinate_frame": "paper millimetres; origin bottom-left; +X right; +Y up",
        "model_coordinate_unit": "mm", "source_coordinate_frame": drawing.get("coordinate_frame"),
        "projection_method": method, "projection_convention": "right_handed", "section_plane": deepcopy(dict(plane)),
        "canonical_axis_bounds_model_mm": deepcopy(plan["canonical_axis_bounds_model_mm"]),
        "scale_denominator": denominator, "scale_mode": "explicit" if "scale_denominator" in options else "auto_fit",
        "scale_scope": "One 1:N scale for full-model orthographic linework and independent section; dimension labels remain model mm",
        "dimension_label_resolution_mm": 0.000001,
        "options": deepcopy(options), "views": placed_views, "strokes": strokes, "texts": texts,
        **({"linear_dimensions": placed_dimensions} if placed_dimensions else {}),
        "counts": {"views": len(placed_views), "strokes": len(strokes), "texts": len(texts)},
        "text_alignment": "center", "text_character_policy": "uppercase ASCII A-Z 0-9 space . - + / : _",
        "layout_policy": plan["layout_policy"],
        "projection_standard_scope": "Declared orthographic arrangement only; no complete ISO certification or official projection symbol",
        "section_scope": "Independent section plane; orthographic views show the full model, not a retained cut solid",
        "native_draft_sheet": False, "engineering_dimensions_certified": False,
    }
