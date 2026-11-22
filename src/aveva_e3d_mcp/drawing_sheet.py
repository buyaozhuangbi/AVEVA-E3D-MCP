"""Bounded paper-mm scene shared by orthographic drawing renderers.

The default is a horizontal arrangement of parameter-derived linework. Explicit
first/third-angle options use a separate aligned, physical-view branch. Neither
is an E3D Draft sheet or certified engineering drawing. Model dimensions stay
in mm; only the linework positions use the declared paper scale.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from .section_hatch_precision import validate_section_hatch_precision


MAX_SHEET_SOURCE_LINES = 20_000
MAX_SHEET_STROKES = 22_000
MAX_SHEET_TEXTS = 128
_PAPERS = {"A4": (210.0, 297.0), "A3": (297.0, 420.0)}
_TEXT = re.compile(r"[A-Z0-9 .+/:_\-]*\Z")
_ALLOWED_OPTIONS = {"paper_size", "orientation", "scale_denominator", "title", "drawing_number", "projection_method"}
_MAX_MODEL_COORDINATE = 1_000_000_000.0
_MIN_TEXT_HEIGHT = 1.5
_LABEL_RESOLUTION_MM = 0.000001


def _validate_coordinate_decimal_places(value: Any) -> None:
    if type(value) is not int or not 6 <= value <= 15:
        raise ValueError("invalid_coordinate_decimal_places")


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_{field}")
    try:
        value = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"invalid_{field}") from None
    if not math.isfinite(value):
        raise ValueError(f"invalid_{field}")
    return value


def _point(value: Any, field: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"invalid_{field}")
    point = (_number(value[0], field), _number(value[1], field))
    if any(abs(coordinate) > _MAX_MODEL_COORDINATE for coordinate in point):
        raise ValueError("drawing_sheet_model_coordinate_limit_exceeded")
    return point


def _text_option(value: Any, field: str, maximum: int, *, allow_empty: bool) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"invalid_sheet_{field}")
    # Reject controls before strip: a newline is not an acceptable title suffix.
    if any(ord(character) < 32 or ord(character) >= 127 for character in value):
        raise ValueError(f"unsupported_sheet_{field}_characters")
    value = value.strip().upper()
    if (not allow_empty and not value) or _TEXT.fullmatch(value) is None:
        raise ValueError(f"unsupported_sheet_{field}_characters")
    return value


def normalize_sheet_options(options: Any) -> dict[str, Any]:
    """Validate without geometry or native reads; return stable public options.

    Titles use the bounded built-in bitmap font's ASCII alphabet so every
    accepted character is represented consistently in PNG, SVG and DXF.
    Omit scale_denominator for an automatically fitted reduction, at least 1:1.
    """
    if not isinstance(options, Mapping) or set(options) - _ALLOWED_OPTIONS:
        raise ValueError("invalid_sheet_options")
    paper = options.get("paper_size", "A3")
    orientation = options.get("orientation", "landscape")
    if not isinstance(paper, str) or paper not in _PAPERS:
        raise ValueError("invalid_sheet_paper_size")
    if not isinstance(orientation, str) or orientation not in {"landscape", "portrait"}:
        raise ValueError("invalid_sheet_orientation")
    result = {
        "paper_size": paper, "orientation": orientation,
        "title": _text_option(options.get("title", "E3D ORTHOGRAPHIC DRAWING"), "title", 120, allow_empty=False),
        "drawing_number": _text_option(options.get("drawing_number", ""), "drawing_number", 40, allow_empty=True),
    }
    if "scale_denominator" in options:
        denominator = _number(options["scale_denominator"], "sheet_scale_denominator")
        if denominator <= 0:
            raise ValueError("invalid_sheet_scale_denominator")
        result["scale_denominator"] = denominator
    if "projection_method" in options:
        method = options["projection_method"]
        if not isinstance(method, str) or method not in {"first_angle", "third_angle"}:
            raise ValueError("invalid_sheet_projection_method")
        result["projection_method"] = method
    return result


def _dimension_label(value: float) -> str:
    text = format(value, ".6f").rstrip("0").rstrip(".") or "0"
    if value > 0 and text == "0":
        raise ValueError("drawing_sheet_dimension_below_label_precision")
    return text + " MM"


def _scale_label(denominator: float) -> str:
    # Fixed notation fits the built-in font without relying on an exponent
    # glyph or describing a tiny positive denominator as zero.
    text = format(denominator, ".9f").rstrip("0").rstrip(".") or "0"
    if denominator > 0 and text == "0":
        raise ValueError("drawing_sheet_scale_below_label_precision")
    if len(text) > 24:
        raise ValueError("drawing_sheet_scale_label_too_long")
    return "SCALE 1:" + text


def _linear_dimensions(view: Mapping[str, Any], minimum, maximum) -> list[dict[str, Any]] | None:
    if "linear_dimensions" not in view:
        return None
    values = view["linear_dimensions"]
    if view.get("name") != "SECTION" or not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 8:
        raise ValueError("invalid_sheet_linear_dimensions")
    prepared = []
    for item in values:
        if not isinstance(item, Mapping) or set(item) != {"first_vertex", "second_vertex", "start", "end", "axis", "side", "value_mm"}:
            raise ValueError("invalid_sheet_linear_dimension")
        first, second = item["first_vertex"], item["second_vertex"]
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (first, second)) or first == second:
            raise ValueError("invalid_sheet_linear_dimension_vertices")
        axis, side = item["axis"], item["side"]
        if (not isinstance(axis, str) or not isinstance(side, str) or
                (axis == "horizontal" and side not in {"top", "bottom"}) or
                (axis == "vertical" and side not in {"left", "right"}) or axis not in {"horizontal", "vertical"}):
            raise ValueError("invalid_sheet_linear_dimension_axis_or_side")
        start, end = _point(item["start"], "dimension_endpoint"), _point(item["end"], "dimension_endpoint")
        if any(not minimum[i] <= point[i] <= maximum[i] for point in (start, end) for i in (0, 1)):
            raise ValueError("sheet_linear_dimension_endpoint_outside_bounds")
        value = _number(item["value_mm"], "dimension_value_mm")
        expected = abs(end[0 if axis == "horizontal" else 1] - start[0 if axis == "horizontal" else 1])
        if value <= 0 or value != expected:
            raise ValueError("sheet_linear_dimension_value_mismatch")
        prepared.append({"first_vertex": first, "second_vertex": second, "start": list(start), "end": list(end),
                         "axis": axis, "side": side, "value_mm": value, "label": _dimension_label(value)})
    return prepared


def _dimension_margins(dimensions: list[dict[str, Any]]) -> dict[str, float]:
    # The old overall dimensions stay at 7 mm, with their labels at 11 mm.
    # Custom lanes begin 12 mm farther out on occupied bottom/left sides.
    counts = {side: 0 for side in ("top", "bottom", "left", "right")}
    margins = {"left": 14.0, "right": 6.0, "bottom": 14.0, "top": 6.0}
    for item in dimensions:
        side = item["side"]
        lane = counts[side]
        counts[side] += 1
        item["lane_index"] = lane
        item["lane_offset_mm"] = (19.0 if side in {"bottom", "left"} else 7.0) + lane * 12.0
        margins[side] = max(margins[side], item["lane_offset_mm"] + 6.0)
        half_label = (6 * len(item["label"]) - 1) / 7 * 2.2 / 2
        for orthogonal in (("left", "right") if item["axis"] == "horizontal" else ("top", "bottom")):
            margins[orthogonal] = max(margins[orthogonal], half_label + 3.0)
    margins["top"] += 12.0  # Keep geometry, lanes and vertical labels below the view heading.
    return margins


def _prepare_views(drawing: Any, *, max_views: int = 3) -> list[dict[str, Any]]:
    if not isinstance(drawing, Mapping) or drawing.get("success") is not True or drawing.get("complete") is not True:
        raise ValueError("successful_complete_drawing_required")
    if drawing.get("coordinate_unit") != "mm":
        raise ValueError("drawing_sheet_model_millimetres_required")
    views = drawing.get("views")
    if not isinstance(views, (list, tuple)) or not 1 <= len(views) <= max_views:
        raise ValueError("invalid_views")
    prepared: list[dict[str, Any]] = []
    names: set[str] = set()
    total_lines = 0
    for view in views:
        if not isinstance(view, Mapping):
            raise ValueError("invalid_view")
        name = view.get("name")
        if not isinstance(name, str) or name not in {"FRONT", "TOP", "SIDE", "SECTION"} or name in names:
            raise ValueError("invalid_view_name")
        names.add(name)
        bounds = view.get("bounds")
        if not isinstance(bounds, Mapping):
            raise ValueError("invalid_bounds")
        minimum = _point(bounds.get("min"), "bounds")
        maximum = _point(bounds.get("max"), "bounds")
        width, height = maximum[0] - minimum[0], maximum[1] - minimum[1]
        if width < 0 or height < 0 or not math.isfinite(width) or not math.isfinite(height):
            raise ValueError("invalid_bounds")
        dimensions = view.get("dimensions")
        if not isinstance(dimensions, Mapping) or dimensions.get("unit") != "mm":
            raise ValueError("drawing_sheet_model_millimetres_required")
        tolerance = max(1e-9, width * 1e-9, height * 1e-9)
        if any(abs(_number(dimensions.get(key), "dimensions") - extent) > tolerance
               for key, extent in (("width", width), ("height", height))):
            raise ValueError("dimensions_bounds_mismatch")
        source_lines = view.get("lines")
        if not isinstance(source_lines, (list, tuple)):
            raise ValueError("invalid_lines")
        total_lines += len(source_lines)
        if total_lines > MAX_SHEET_SOURCE_LINES:
            raise ValueError("drawing_sheet_line_limit_exceeded")
        lines = []
        for source_index, line in enumerate(source_lines):
            if (not isinstance(line, Mapping) or line.get("kind") not in ("silhouette", "feature", "hatch") or
                    (line["kind"] == "hatch" and name != "SECTION")):
                raise ValueError("invalid_line_kind")
            visibility = line.get("visibility", "visible")
            if visibility not in ("visible", "hidden"):
                raise ValueError("invalid_line_visibility")
            if line["kind"] == "hatch" and visibility == "hidden":
                raise ValueError("hidden_hatch_unsupported")
            if visibility == "hidden" and drawing.get("show_hidden_lines") is not True:
                raise ValueError("drawing_sheet_hidden_lines_not_requested")
            start, end = _point(line.get("start"), "line"), _point(line.get("end"), "line")
            if start == end:
                raise ValueError("degenerate_line")
            if any(point[axis] < minimum[axis] - tolerance or point[axis] > maximum[axis] + tolerance
                   for point in (start, end) for axis in range(2)):
                raise ValueError("line_outside_bounds")
            # Floating projection arithmetic can be a few ulps outside a bound.
            # Preserve admitted geometry on that exact printable boundary.
            clamp = lambda point: tuple(max(minimum[axis], min(maximum[axis], point[axis])) for axis in range(2))
            start, end = clamp(start), clamp(end)
            if start == end:
                # A nonzero admitted segment can lie wholly in the tolerance
                # strip outside a bound. Clamping both endpoints would hide
                # real input linework rather than just repair a rounding ulp.
                raise ValueError("drawing_sheet_geometry_collapsed_by_bounds_tolerance")
            lines.append({"start": start, "end": end, "kind": line["kind"],
                          "visibility": visibility, "source_line_index": source_index})
        prepared.append({"name": name, "minimum": minimum, "maximum": maximum,
                         "width": width, "height": height, "lines": lines,
                         "linear_dimensions": _linear_dimensions(view, minimum, maximum),
                         "width_label": _dimension_label(width), "height_label": _dimension_label(height)})
    return prepared


def _fitted_height(text: str, maximum_width: float, preferred_height: float) -> float:
    # Both SVG and the built-in 5x7 renderer use six cells of advance per
    # character, with no trailing blank column after the final glyph.
    width_at_unit_height = (6 * len(text) - 1) / 7 if text else 0.0
    height = min(preferred_height, maximum_width / width_at_unit_height) if text else preferred_height
    if height < _MIN_TEXT_HEIGHT:
        raise ValueError("drawing_sheet_text_does_not_fit")
    return height


def _auto_denominator(required: float) -> float:
    required = max(1.0, required)
    exponent = math.floor(math.log10(required))
    base = 10.0 ** exponent
    for multiple in (1.0, 2.0, 5.0, 10.0):
        candidate = multiple * base
        if candidate >= required * (1 - 1e-12):
            return candidate
    raise ValueError("drawing_sheet_scale_unavailable")


def _check_dimension_annotations(strokes, texts, border, content_bottom, content_top, cell_width, gap):
    """Reject unresolved label/line collisions rather than hiding annotations."""
    view_indices = {item["view_name"]: index for index, item in enumerate(
        item for item in texts if item["role"] == "view_label")}

    def rectangle(item):
        width = (6 * len(item["text"]) - 1) / 7 * item["height"] if item["text"] else 0
        half_x, half_y = ((item["height"] / 2, width / 2) if item["rotation"] else
                          (width / 2, item["height"] / 2))
        x, y = item["position"]
        return x - half_x, y - half_y, x + half_x, y + half_y

    def overlaps(first, second):
        return (min(first[2], second[2]) > max(first[0], second[0]) and
                min(first[3], second[3]) > max(first[1], second[1]))

    def crosses_box(start, end, box):
        low, high = 0.0, 1.0
        for axis in (0, 1):
            delta = end[axis] - start[axis]
            if delta == 0:
                if not box[axis] < start[axis] < box[axis + 2]:
                    return False
            else:
                entry, leave = (box[axis] - start[axis]) / delta, (box[axis + 2] - start[axis]) / delta
                if entry > leave:
                    entry, leave = leave, entry
                low, high = max(low, entry), min(high, leave)
                if low >= high:
                    return False
        return low < high

    boxes = [rectangle(item) for item in texts]
    for index, item in enumerate(texts):
        if "dimension_index" not in item:
            continue
        box = boxes[index]
        left = border + view_indices[item["view_name"]] * (cell_width + gap)
        if (box[0] < left + 1 - 1e-8 or box[2] > left + cell_width - 1 + 1e-8 or
                box[1] < content_bottom + 1 - 1e-8 or box[3] > content_top - 12 + 1e-8):
            raise ValueError("drawing_sheet_linear_dimension_label_outside_lane_area")
        if any(overlaps(box, other) for other_index, other in enumerate(boxes) if other_index != index):
            raise ValueError("drawing_sheet_linear_dimension_label_collision")
    dimension_texts = [(item, box) for item, box in zip(texts, boxes) if item["role"] == "dimension"]
    for item in strokes:
        if item["role"] != "dimension":
            continue
        custom = "dimension_index" in item
        if custom:
            left = border + view_indices[item["view_name"]] * (cell_width + gap)
            if any(not left + 1 - 1e-8 <= point[0] <= left + cell_width - 1 + 1e-8 or
                   not content_bottom + 1 - 1e-8 <= point[1] <= content_top - 12 + 1e-8
                   for point in (item["start"], item["end"])):
                raise ValueError("drawing_sheet_linear_dimension_outside_lane_area")
        for label, box in dimension_texts:
            if (custom or "dimension_index" in label) and crosses_box(item["start"], item["end"], box):
                raise ValueError("drawing_sheet_linear_dimension_label_line_collision")


def build_drawing_sheet(drawing: Any, options: Any, *, coordinate_decimal_places: int = 6) -> dict[str, Any]:
    """Build one deterministic printable scene using bottom-left paper-mm axes.

    All texts are centered on their position and rotated by 0 or 90 degrees.
    View transforms map [u,v] to paper_origin + ([u,v]-model_min)/denominator.
    Dimension labels represent model mm and are rounded to 1e-6 mm; titleblock
    SCALE applies to geometry only. Failure raises ValueError before returning
    a partial scene. Input arrays and options are never modified.
    """
    _validate_coordinate_decimal_places(coordinate_decimal_places)
    normalized = normalize_sheet_options(options)
    if isinstance(drawing, Mapping) and drawing.get("sheet_composition") == "orthographic_section":
        from .composite_sheet import build_composite_sheet
        if coordinate_decimal_places != 6:
            raise ValueError("composite_sheet_coordinate_precision_unsupported")
        return build_composite_sheet(drawing, normalized)
    if "projection_method" in normalized:
        from .projection_sheet import build_projection_sheet
        return build_projection_sheet(drawing, normalized, coordinate_decimal_places=coordinate_decimal_places)
    views = _prepare_views(drawing)
    short_side, long_side = _PAPERS[normalized["paper_size"]]
    paper_width, paper_height = ((long_side, short_side) if normalized["orientation"] == "landscape"
                                 else (short_side, long_side))
    border = 10.0
    title_top = border + 30.0
    content_bottom, content_top = title_top + 10.0, paper_height - border
    gap = 6.0
    cell_width = (paper_width - 2 * border - gap * (len(views) - 1)) / len(views)
    cell_height = content_top - content_bottom
    available_width, available_height = cell_width - 30.0, cell_height - 34.0
    if available_width <= 0 or available_height <= 0:
        raise ValueError("drawing_sheet_content_area_unavailable")
    margins = [_dimension_margins(view["linear_dimensions"]) if view["linear_dimensions"] is not None else None for view in views]
    requirements = []
    for view, margin in zip(views, margins):
        w = cell_width - margin["left"] - margin["right"] if margin else available_width
        h = cell_height - margin["bottom"] - margin["top"] if margin else available_height
        if w <= 0 or h <= 0:
            raise ValueError("drawing_sheet_dimension_lanes_do_not_fit")
        requirements.append(max(view["width"] / w, view["height"] / h))
    required = max(requirements)
    denominator = normalized.get("scale_denominator", _auto_denominator(required))
    if denominator < required * (1 - 1e-12):
        raise ValueError("drawing_sheet_scale_does_not_fit")
    scale_label = _scale_label(denominator)
    strokes: list[dict[str, Any]] = []
    texts: list[dict[str, Any]] = []
    placed_dimensions: list[dict[str, Any]] = []

    def stroke(start, end, role, name=None, visibility="visible", **metadata):
        if any(not math.isfinite(value) or value < -1e-8 or value > limit + 1e-8
               for point in (start, end) for value, limit in zip(point, (paper_width, paper_height))):
            raise ValueError("drawing_sheet_layout_out_of_bounds")
        if start == end:
            if role in {"silhouette", "feature", "hatch"} or "dimension_index" in metadata:
                raise ValueError("drawing_sheet_geometry_below_precision")
            return
        if coordinate_decimal_places == 6:
            # Preserve the existing default's exact threshold and output.
            if (role in {"silhouette", "feature", "hatch"} or "dimension_index" in metadata) and all(round(a, 6) == round(b, 6) for a, b in zip(start, end)):
                raise ValueError("drawing_sheet_geometry_below_precision")
        else:
            encoded = lambda point: tuple(float(format(value, f".{coordinate_decimal_places}f")) for value in point)
            if (encoded(start) == encoded(end)
                    or encoded((start[0], paper_height - start[1])) == encoded((end[0], paper_height - end[1]))):
                raise ValueError("drawing_sheet_geometry_below_precision")
        strokes.append({"start": list(start), "end": list(end), "role": role,
                        "view_name": name, "visibility": visibility, **metadata})

    def text(position, value, preferred_height, role, name=None, rotation=0.0, maximum_width=None, **metadata):
        height = _fitted_height(value, maximum_width if maximum_width is not None else paper_width - 24.0,
                                preferred_height)
        rendered_width = (6 * len(value) - 1) / 7 * height if value else 0.0
        half_x, half_y = ((height / 2, rendered_width / 2) if rotation == 90.0 else
                          (rendered_width / 2, height / 2))
        if (position[0] - half_x < border - 1e-8 or position[0] + half_x > paper_width - border + 1e-8 or
                position[1] - half_y < border - 1e-8 or position[1] + half_y > paper_height - border + 1e-8):
            raise ValueError("drawing_sheet_text_out_of_bounds")
        texts.append({"position": list(position), "text": value, "height": height, "rotation": rotation,
                      "role": role, "view_name": name, **metadata})

    corners = ((border, border), (paper_width - border, border),
               (paper_width - border, paper_height - border), (border, paper_height - border))
    for index in range(4):
        stroke(corners[index], corners[(index + 1) % 4], "border")
    stroke((border, title_top), (paper_width - border, title_top), "title_block")
    stroke((border, border + 15.0), (paper_width - border, border + 15.0), "title_block")
    body_width = paper_width - 2 * border
    separators = (border + body_width * .5, border + body_width * .75)
    for x in separators:
        stroke((x, border), (x, border + 15.0), "title_block")
    text((paper_width / 2, border + 22.5), normalized["title"], 4.0, "title", maximum_width=body_width - 8.0)
    number_label = "NO " + (normalized["drawing_number"] or "UNASSIGNED")
    text((border + body_width * .25, border + 7.5), number_label, 2.5, "drawing_number",
         maximum_width=body_width * .5 - 6.0)
    text((border + body_width * .625, border + 7.5), scale_label, 2.5, "sheet_metadata",
         maximum_width=body_width * .25 - 6.0)
    paper_label = normalized["paper_size"] + " " + normalized["orientation"].upper()
    text((border + body_width * .875, border + 7.5), paper_label, 2.2, "sheet_metadata",
         maximum_width=body_width * .25 - 6.0)
    disclaimer = ("NONZERO CONTOUR FILL / NOT NATIVE DRAFT / MODEL MM" if drawing.get("hatching_applied") is True else
                  "PARAMETER MESH / NOT NATIVE DRAFT / MODEL MM")
    text((paper_width / 2, title_top + 5.0), disclaimer, 2.0, "disclaimer")

    placed_views = []
    for index, view in enumerate(views):
        name = view["name"]
        cell_left = border + index * (cell_width + gap)
        cx, cy = cell_left + cell_width / 2, (content_bottom + content_top) / 2
        width, height = view["width"] / denominator, view["height"] / denominator
        origin = (cx - width / 2, cy - height / 2)
        margin = margins[index]
        if margin:
            usable_width = cell_width - margin["left"] - margin["right"]
            usable_height = cell_height - margin["bottom"] - margin["top"]
            origin = (cell_left + margin["left"] + (usable_width - width) / 2,
                      content_bottom + margin["bottom"] + (usable_height - height) / 2)
            cx, cy = origin[0] + width / 2, origin[1] + height / 2
        stroke_start, text_start = len(strokes), len(texts)
        text((cx, content_top - 5.0), name, 3.0, "view_label", name, maximum_width=cell_width - 4.0)
        def project(point):
            return tuple(origin[axis] + (point[axis] - view["minimum"][axis]) / denominator for axis in range(2))
        validate_section_hatch_precision(view["lines"], transform=project)
        for line in view["lines"]:
            stroke(project(line["start"]), project(line["end"]), line["kind"], name,
                   line["visibility"], source_line_index=line["source_line_index"])
        x_min, y_min = origin
        x_max, y_max = x_min + width, y_min + height
        horizontal_y, vertical_x = y_min - 7.0, x_min - 7.0
        arrow = min(2.0, width / 3) if width > 0 else 0.0
        if view["width"] > 0:
            stroke((x_min, y_min), (x_min, horizontal_y - 1.0), "dimension", name)
            stroke((x_max, y_min), (x_max, horizontal_y - 1.0), "dimension", name)
            stroke((x_min, horizontal_y), (x_max, horizontal_y), "dimension", name)
            for tip, direction in ((x_min, 1.0), (x_max, -1.0)):
                for sign in (-1.0, 1.0):
                    stroke((tip, horizontal_y), (tip + direction * arrow, horizontal_y + sign * arrow * .32), "dimension", name)
            text((cx, horizontal_y - 4.0), view["width_label"], 2.2, "dimension", name,
                 maximum_width=cell_width - 4.0, dimension_axis="width", dimension_value_mm=view["width"])
        arrow = min(2.0, height / 3) if height > 0 else 0.0
        if view["height"] > 0:
            stroke((x_min, y_min), (vertical_x - 1.0, y_min), "dimension", name)
            stroke((x_min, y_max), (vertical_x - 1.0, y_max), "dimension", name)
            stroke((vertical_x, y_min), (vertical_x, y_max), "dimension", name)
            for tip, direction in ((y_min, 1.0), (y_max, -1.0)):
                for sign in (-1.0, 1.0):
                    stroke((vertical_x, tip), (vertical_x + sign * arrow * .32, tip + direction * arrow), "dimension", name)
            text((vertical_x - 4.0, cy), view["height_label"], 2.2, "dimension", name, rotation=90.0,
                 maximum_width=cell_height - 8.0, dimension_axis="height", dimension_value_mm=view["height"])
        for dimension in view["linear_dimensions"] or []:
            dimension_index = len(placed_dimensions)
            dim_stroke_start, dim_text_start = len(strokes), len(texts)
            first, second = project(dimension["start"]), project(dimension["end"])
            horizontal = dimension["axis"] == "horizontal"
            axis = 0 if horizontal else 1
            low, high = sorted((first[axis], second[axis]))
            if format(low, f".{coordinate_decimal_places}f") == format(high, f".{coordinate_decimal_places}f"):
                raise ValueError("drawing_sheet_linear_dimension_below_paper_precision")
            side = dimension["side"]
            sign = 1 if side in {"top", "right"} else -1
            line_coordinate = ({"top": y_max, "bottom": y_min, "left": x_min, "right": x_max}[side] +
                               sign * dimension["lane_offset_mm"])
            def along(value, coordinate=line_coordinate):
                return (value, coordinate) if horizontal else (coordinate, value)
            annotation = {"dimension_index": dimension_index, "dimension_axis": dimension["axis"],
                          "dimension_side": side, "dimension_value_mm": dimension["value_mm"]}
            for endpoint in (first, second):
                stroke(endpoint, along(endpoint[axis], line_coordinate + sign), "dimension", name,
                       dimension_part="extension", **annotation)
            outward = high - low < 6.0
            arrow = 2.0
            # Short dimensions use outward arrows and a longer dimension line;
            # the requested distance remains the two extension-line tips.
            stroke(along(low - arrow if outward else low), along(high + arrow if outward else high),
                   "dimension", name, dimension_part="line", **annotation)
            for tip, direction in ((low, -1.0 if outward else 1.0), (high, 1.0 if outward else -1.0)):
                for lateral in (-1.0, 1.0):
                    stroke(along(tip), along(tip + direction * arrow, line_coordinate + lateral * arrow * .32),
                           "dimension", name, dimension_part="arrow", **annotation)
            label_position = along((low + high) / 2, line_coordinate + sign * 4.0)
            text(label_position, dimension["label"], 2.2, "dimension", name,
                 rotation=0.0 if horizontal else 90.0,
                 maximum_width=cell_width - 4 if horizontal else cell_height - 16,
                 **annotation)
            if texts[-1]["height"] != 2.2:
                raise ValueError("drawing_sheet_linear_dimension_label_does_not_fit")
            placed_dimensions.append({key: value for key, value in dimension.items() if key != "label"} | {
                "dimension_index": dimension_index, "view_name": name,
                "paper_start_mm": list(first), "paper_end_mm": list(second),
                "dimension_line_start_mm": list(along(low)), "dimension_line_end_mm": list(along(high)),
                "label_position_mm": list(label_position), "label_height_mm": 2.2,
                "arrow_direction": "outward" if outward else "inward",
                "stroke_range": [dim_stroke_start, len(strokes)], "text_range": [dim_text_start, len(texts)],
            })
        placed_views.append({
            "name": name, "model_bounds": {"min": list(view["minimum"]), "max": list(view["maximum"])},
            "model_dimensions_mm": {"width": view["width"], "height": view["height"]},
            "paper_bounds_mm": {"min": list(origin), "max": [x_max, y_max]},
            "transform": {"model_min": list(view["minimum"]), "paper_origin_mm": list(origin),
                          "scale_denominator": denominator, "paper_mm_per_model_mm": 1 / denominator},
            "stroke_range": [stroke_start, len(strokes)], "text_range": [text_start, len(texts)],
        })
    if len(strokes) > MAX_SHEET_STROKES or len(texts) > MAX_SHEET_TEXTS:
        raise ValueError("drawing_sheet_output_limit_exceeded")
    if placed_dimensions:
        _check_dimension_annotations(strokes, texts, border, content_bottom, content_top, cell_width, gap)
    return {
        "success": True, "available": True, "complete": True,
        "paper": {"size": normalized["paper_size"], "orientation": normalized["orientation"],
                  "width_mm": paper_width, "height_mm": paper_height},
        "paper_size_mm": [paper_width, paper_height], "coordinate_space": "paper", "coordinate_unit": "mm",
        "coordinate_frame": "paper millimetres; origin bottom-left; +X right; +Y up",
        "model_coordinate_unit": "mm", "source_coordinate_frame": drawing.get("coordinate_frame"),
        "scale_denominator": denominator, "scale_mode": "explicit" if "scale_denominator" in normalized else "auto_fit",
        "scale_scope": "1:N applies to model linework on paper; dimension labels retain model millimetres",
        "dimension_label_resolution_mm": _LABEL_RESOLUTION_MM,
        "options": normalized, "views": placed_views, "strokes": strokes, "texts": texts,
        **({"linear_dimensions": placed_dimensions} if placed_dimensions else {}),
        "counts": {"views": len(views), "strokes": len(strokes), "texts": len(texts)},
        "text_alignment": "center", "text_character_policy": "uppercase ASCII A-Z 0-9 space . - + / : _",
        "layout_policy": "horizontal view columns; no first-angle or third-angle standard claimed",
        "native_draft_sheet": False, "engineering_dimensions_certified": False,
    }
