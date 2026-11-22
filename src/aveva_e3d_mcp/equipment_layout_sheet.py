"""Indexed equipment location sheets from complete WORLD position/orientation.

All pages share position bounds and a scale. Paper symbols describe origins
and local axes; they do not describe equipment shapes or physical axis lengths.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
import math
import re
from typing import Any

from .drawing_sheet_render import SHEET_TEXT_CHARACTERS, validate_drawing_sheet_scene


_PAPERS = {"A3": (420.0, 297.0), "A4": (297.0, 210.0)}
_OPTIONS = {"size", "title", "drawing_number", "equipment_per_page"}
_LIMIT = 1_000_000_000.0
_MAX_EQUIPMENT = 256
_AXIS_LENGTH = 6.0
_MARKER_HALF = .8
_PADDING = 9.0
_MIN_PROJECTED_AXIS = .0001
_FOOTER = "LOCATION/POSE ONLY - NO EQUIPMENT ENVELOPE"
_COLUMNS = ("ID", "DEVICE", "X MM", "Y MM", "Z MM", "DX MM", "DY MM", "DEL MM")
_WIDTHS = (4, 24, 15, 15, 15, 15, 15, 15)
_VIEW_AXES = {"TOP": ([1, 0, 0], [0, 1, 0], [0, 0, 1]),
              "FRONT": ([1, 0, 0], [0, 0, 1], [0, -1, 0])}


def _number(value: Any, field: str, *, limit: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_equipment_sheet_{field}")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"invalid_equipment_sheet_{field}") from None
    if not math.isfinite(number) or limit is not None and abs(number) > limit:
        raise ValueError(f"invalid_equipment_sheet_{field}")
    return 0.0 if number == 0 else number


def _vector(value: Any, field: str, *, limit: float | None = None) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"invalid_equipment_sheet_{field}")
    return [_number(component, field, limit=limit) for component in value]


def _text_option(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum or any(ord(character) < 32 or ord(character) >= 127 for character in value):
        raise ValueError(f"invalid_equipment_sheet_{field}")
    text = value.strip().upper()
    if not text or any(character not in SHEET_TEXT_CHARACTERS for character in text):
        raise ValueError(f"invalid_equipment_sheet_{field}")
    return text


def normalize_equipment_layout_sheet_options(options: Any) -> dict[str, Any]:
    """Validate A3/A4 paper, titles and a capacity of eight or sixteen rows."""
    if not isinstance(options, Mapping) or set(options) - _OPTIONS:
        raise ValueError("invalid_equipment_sheet_options")
    size, count = options.get("size", "A3"), options.get("equipment_per_page", 8)
    if not isinstance(size, str) or size not in _PAPERS:
        raise ValueError("invalid_equipment_sheet_size")
    if type(count) is not int or count not in (8, 16):
        raise ValueError("invalid_equipment_sheet_equipment_per_page")
    return {"size": size, "title": _text_option(options.get("title", "EQUIPMENT LOCATION"), "title", 48),
            "drawing_number": _text_option(options.get("drawing_number", "E3D-EQUIPMENT"), "drawing_number", 32),
            "equipment_per_page": count}


def normalize_equipment_layout_datum(datum: Any = None) -> dict[str, Any]:
    """Return the declared WORLD datum; equipment centers never set the datum."""
    if datum is None:
        datum = {"label": "WORLD ORIGIN", "origin_mm": [0, 0, 0]}
    if not isinstance(datum, Mapping) or set(datum) != {"label", "origin_mm"}:
        raise ValueError("invalid_equipment_sheet_datum")
    return {"label": _text_option(datum["label"], "datum_label", 32),
            "origin_mm": _vector(datum["origin_mm"], "datum_origin", limit=_LIMIT)}


def _reference(value: Any) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > 1024
            or any(ord(character) < 32 for character in value)):
        raise ValueError("invalid_equipment_sheet_reference")
    return value


def _reference_key(value: str) -> str:
    if re.fullmatch(r"=[0-9]+/[0-9]+", value):
        return "=" + "/".join(str(int(part)) for part in value[1:].split("/"))
    return value.casefold()


def _fixed(value: float) -> str:
    text = format(value, ".3f")
    return text[1:] if text.startswith("-") and float(text) == 0 else text


def _table(values: Sequence[str]) -> str:
    if len(values) != len(_WIDTHS) or any(len(value) > width for value, width in zip(values, _WIDTHS)):
        raise ValueError("equipment_sheet_table_value_does_not_fit")
    return " ".join(value.ljust(width) if index < 2 else value.rjust(width)
                    for index, (value, width) in enumerate(zip(values, _WIDTHS)))


def _orientation(value: Any) -> dict[str, list[float]]:
    if not isinstance(value, Mapping):
        raise ValueError("equipment_sheet_world_orientation_required")
    axes = {name: _vector(value.get(name), "orientation") for name in ("x_axis", "y_axis", "z_axis")}
    x, y, z = axes.values()
    dot = lambda a, b: math.fsum(first * second for first, second in zip(a, b))
    if (any(abs(dot(axis, axis) - 1) > 1e-6 for axis in axes.values())
            or any(abs(dot(a, b)) > 1e-6 for a, b in ((x, y), (x, z), (y, z)))):
        raise ValueError("equipment_sheet_orthonormal_orientation_required")
    cross = [x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0]]
    if dot(cross, z) < 1 - 1e-6:
        raise ValueError("equipment_sheet_right_handed_orientation_required")
    return axes


def _prepare_rows(drawing: Any, datum: Mapping[str, Any]) -> list[dict[str, Any]]:
    if (not isinstance(drawing, Mapping)
            or any(drawing.get(flag) is not True for flag in ("success", "available", "complete"))):
        raise ValueError("successful_complete_equipment_drawing_required")
    if drawing.get("coordinate_frame") != "WORLD" or drawing.get("coordinate_unit") != "mm":
        raise ValueError("equipment_sheet_world_millimetres_required")
    if drawing.get("extents_included") is not False:
        raise ValueError("equipment_sheet_pose_only_drawing_required")
    raw_rows = drawing.get("equipment")
    if not isinstance(raw_rows, (list, tuple)) or not 1 <= len(raw_rows) <= _MAX_EQUIPMENT:
        raise ValueError("equipment_sheet_equipment_limit_exceeded_or_invalid")
    if "equipment_count" in drawing and (type(drawing["equipment_count"]) is not int or drawing["equipment_count"] != len(raw_rows)):
        raise ValueError("equipment_sheet_equipment_count_mismatch")
    rows, identities = [], set()
    for index, raw in enumerate(raw_rows):
        if (not isinstance(raw, Mapping)
                or any(raw.get(flag) is not True for flag in
                       ("position_available", "orientation_available", "read_success", "read_complete"))):
            raise ValueError("equipment_sheet_source_pose_unavailable_or_incomplete")
        if "source_index" in raw and (type(raw["source_index"]) is not int or raw["source_index"] != index):
            raise ValueError("equipment_sheet_source_index_mismatch")
        if raw.get("coordinate_frame", "WORLD") != "WORLD" or raw.get("coordinate_unit", "mm") != "mm":
            raise ValueError("equipment_sheet_source_world_millimetres_required")
        refno = _reference(raw.get("requested_refno"))
        resolved = _reference(raw["resolved_refno"]) if "resolved_refno" in raw else refno
        key = _reference_key(resolved)
        if key in identities:
            raise ValueError("equipment_sheet_duplicate_source_identity")
        identities.add(key)
        position = _vector(raw.get("position"), "world_position", limit=_LIMIT)
        orientation = _orientation(raw.get("orientation"))
        relative = [position[axis] - datum["origin_mm"][axis] for axis in (0, 1, 2)]
        identifier = f"E{index + 1:03d}"
        name = raw.get("element_name")
        name = name if isinstance(name, str) and name.strip() else refno
        display = name.upper()
        abbreviated = len(display) > _WIDTHS[1] or any(character not in SHEET_TEXT_CHARACTERS for character in display)
        if abbreviated:
            display = f"SRC{index + 1:03d}"
        values = {"id": identifier, "device": display,
                  **dict(zip(("x_mm", "y_mm", "z_mm"), map(_fixed, position))),
                  **dict(zip(("dx_mm", "dy_mm", "del_mm"), map(_fixed, relative)))}
        row = {**deepcopy(dict(raw)), "source_index": index, "id": identifier,
               "requested_refno": refno, "resolved_refno": resolved, "position": position,
               "orientation": orientation, "coordinate_frame": "WORLD", "coordinate_unit": "mm",
               "relative_position_mm": relative, "reference_display": display,
               "reference_display_abbreviated": abbreviated, "table_values": values,
               "table_row_text": _table(tuple(values.values()))}
        rows.append(row)
    try:
        json.dumps(rows, allow_nan=False)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ValueError("equipment_sheet_source_evidence_not_serializable") from None
    return rows


def _scale(required: float) -> float:
    required = max(1.0, required)
    base = 10.0 ** math.floor(math.log10(required))
    for multiple in (1.0, 2.0, 5.0, 10.0):
        candidate = multiple * base
        if candidate >= required:
            return candidate
    raise ValueError("equipment_sheet_scale_unavailable")


def _plan(rows: list[dict[str, Any]], options: Mapping[str, Any]) -> dict[str, Any]:
    width, height = _PAPERS[options["size"]]
    border, gap, table_bottom, header = 8.0, 8.0, 42.0, 6.0
    row_height = 5.0 if options["size"] == "A3" else 3.8
    table_top = table_bottom + header + options["equipment_per_page"] * row_height
    plot_bottom, plot_top = table_top + 8, height - 45
    cell_width = (width - border * 2 - gap) / 2
    plot_width, plot_height = cell_width - 29, plot_top - plot_bottom
    if plot_width <= _PADDING * 2 or plot_height <= _PADDING * 2:
        raise ValueError("equipment_sheet_symbol_area_does_not_fit")
    if options["equipment_per_page"] > 1 and (plot_height - 4) / (options["equipment_per_page"] - 1) <= 2.4:
        raise ValueError("equipment_sheet_callout_lanes_do_not_fit")
    minimum = [min(row["position"][axis] for row in rows) for axis in (0, 1, 2)]
    maximum = [max(row["position"][axis] for row in rows) for axis in (0, 1, 2)]
    bounds = {name: {"min": [minimum[0], minimum[vertical]], "max": [maximum[0], maximum[vertical]]}
              for name, vertical in (("TOP", 1), ("FRONT", 2))}
    denominator = _scale(max(max((item["max"][0] - item["min"][0]) / (plot_width - 2 * _PADDING),
                                 (item["max"][1] - item["min"][1]) / (plot_height - 2 * _PADDING))
                             for item in bounds.values()))
    return {"width": width, "height": height, "border": border, "gap": gap, "cell_width": cell_width,
            "plot_width": plot_width, "plot_height": plot_height, "plot_bottom": plot_bottom, "plot_top": plot_top,
            "table_bottom": table_bottom, "table_top": table_top, "table_header": header, "row_height": row_height,
            "global_model_bounds": bounds, "position_bounds_world_mm": {"min": minimum, "max": maximum},
            "scale_denominator": denominator}


def _text_box(text: Mapping[str, Any]) -> tuple[float, float, float, float]:
    width = max(0, len(text["text"]) * 6 - 1) * text["height"] / 7
    x, y = text["position"]
    return x - width / 2, y - text["height"] / 2, x + width / 2, y + text["height"] / 2


def _page(rows: list[dict[str, Any]], options: Mapping[str, Any], datum: Mapping[str, Any],
          plan: Mapping[str, Any], number: int, count: int, total: int) -> dict[str, Any]:
    width, height, denominator = plan["width"], plan["height"], plan["scale_denominator"]
    strokes, texts, views, callouts = [], [], [], []

    def stroke(a, b, role="feature", view_name=None, **metadata):
        if tuple(a) != tuple(b):
            strokes.append({"start": list(a), "end": list(b), "role": role, "view_name": view_name,
                            "visibility": "visible", **metadata})

    def text(position, content, text_height=2.25, role="sheet_metadata", view_name=None, **metadata):
        texts.append({"position": list(position), "text": content, "height": text_height, "rotation": 0,
                      "role": role, "view_name": view_name, **metadata})

    border = plan["border"]
    corners = [(border, border), (width - border, border), (width - border, height - border), (border, height - border)]
    for index in range(4):
        stroke(corners[index], corners[(index + 1) % 4], "border")
    stroke((border, height - 33), (width - border, height - 33), "title_block")
    text((width / 2, height - 14), options["title"], 3.8, "title")
    scale_label = format(denominator, ".9f").rstrip("0").rstrip(".")
    text((width / 2, height - 21), f"WORLD MM / GLOBAL POSITION SCALE 1:{scale_label} / PAGE {number}/{count}", 2.2)
    coords = " / ".join(f"{axis} {_fixed(value)}" for axis, value in zip("XYZ", datum["origin_mm"]))
    text((width / 2, height - 28), f"DATUM {datum['label']} / {coords} MM", 1.9)
    for cell, (name, vertical) in enumerate((("TOP", 1), ("FRONT", 2))):
        left = border + cell * (plan["cell_width"] + plan["gap"])
        plot_left, plot_right = left + 5, left + 5 + plan["plot_width"]
        bounds = plan["global_model_bounds"][name]
        span = [bounds["max"][axis] - bounds["min"][axis] for axis in (0, 1)]
        origin = [plot_left + (plan["plot_width"] - span[0] / denominator) / 2,
                  plan["plot_bottom"] + (plan["plot_height"] - span[1] / denominator) / 2]

        def project(position):
            return [origin[0] + (position[0] - bounds["min"][0]) / denominator,
                    origin[1] + (position[vertical] - bounds["min"][1]) / denominator]

        text((left + plan["cell_width"] / 2, height - 39),
             f"{name} - WORLD +X RIGHT / +{'Y' if name == 'TOP' else 'Z'} UP", 2.3, "view_label", name)
        start_stroke, start_text = len(strokes), len(texts) - 1
        symbol_rows = []
        for row in rows:
            anchor = project(row["position"])
            common = {"source_index": row["source_index"], "id": row["id"],
                      "requested_refno": row["requested_refno"], "resolved_refno": row["resolved_refno"]}
            position_metadata = {**common, "annotation_kind": "equipment_origin_symbol"}
            stroke((anchor[0] - _MARKER_HALF, anchor[1]), (anchor[0] + _MARKER_HALF, anchor[1]), "feature", name, **position_metadata)
            stroke((anchor[0], anchor[1] - _MARKER_HALF), (anchor[0], anchor[1] + _MARKER_HALF), "feature", name, **position_metadata)
            projected_axes, omitted_axes = [], []
            for ticks, axis_name in enumerate(("x_axis", "y_axis", "z_axis"), 1):
                axis = row["orientation"][axis_name]
                dx, dy = axis[0] * _AXIS_LENGTH, axis[vertical] * _AXIS_LENGTH
                length = math.hypot(dx, dy)
                if length <= _MIN_PROJECTED_AXIS:
                    omitted_axes.append(axis_name)
                    continue
                projected_axes.append(axis_name)
                end, ux, uy = [anchor[0] + dx, anchor[1] + dy], dx / length, dy / length
                metadata = {**common, "annotation_kind": "equipment_axis_symbol", "axis_name": axis_name[0].upper(),
                            "native_axis_world": list(axis), "tick_count": ticks}
                stroke(anchor, end, "feature", name, **metadata, symbol_part="shaft")
                head = min(1.2, length * .3)
                for sign in (-1, 1):
                    cosine, sine = math.cos(math.radians(25)), math.sin(math.radians(25))
                    point = [end[0] - head * (cosine * ux - sign * sine * uy),
                             end[1] - head * (cosine * uy + sign * sine * ux)]
                    stroke(point, end, "feature", name, **metadata, symbol_part="arrowhead")
                for tick in range(ticks):
                    fraction = .38 + (.24 * tick / (ticks - 1) if ticks > 1 else .12)
                    center = [anchor[0] + dx * fraction, anchor[1] + dy * fraction]
                    half_tick = min(.55, length * .12)
                    a = [center[0] - uy * half_tick, center[1] + ux * half_tick]
                    b = [center[0] + uy * half_tick, center[1] - ux * half_tick]
                    stroke(a, b, "feature", name, **metadata, symbol_part="tick", tick_index=tick)
            symbol_rows.append({**common, "anchor_mm": anchor, "projected_axes": projected_axes,
                                "omitted_projected_axes": omitted_axes})
        ordered = sorted(rows, key=lambda row: (-row["position"][vertical], row["position"][0], row["source_index"]))
        label_x = left + plan["cell_width"] - 8
        for lane, row in enumerate(ordered):
            y = ((plan["plot_bottom"] + plan["plot_top"]) / 2 if len(rows) == 1 else
                 plan["plot_top"] - 2 - lane * (plan["plot_height"] - 4) / (len(rows) - 1))
            anchor, elbow, end = project(row["position"]), [plot_right + 2, y], [label_x - 6, y]
            metadata = {"source_index": row["source_index"], "id": row["id"], "annotation_kind": "equipment_leader",
                        "requested_refno": row["requested_refno"], "resolved_refno": row["resolved_refno"]}
            stroke(anchor, elbow, "feature", name, **metadata, leader_part="diagonal")
            stroke(elbow, end, "feature", name, **metadata, leader_part="horizontal")
            text((label_x, y), row["id"], 2.4, view_name=name, source_index=row["source_index"], id=row["id"],
                 annotation_kind="equipment_callout")
            callouts.append({**metadata, "view_name": name, "anchor_mm": anchor, "position_mm": [label_x, y], "leader_end_mm": end})
        axes = dict(zip(("horizontal_axis", "vertical_axis", "toward_viewer"), deepcopy(_VIEW_AXES[name])))
        views.append({"name": name, "coordinate_frame": "WORLD", "coordinate_unit": "mm",
                      "model_bounds": deepcopy(bounds), "source_indices": [row["source_index"] for row in rows],
                      "transform": {"model_min": list(bounds["min"]), "paper_origin_mm": origin,
                                    "scale_denominator": denominator, "paper_mm_per_model_mm": 1 / denominator},
                      "plot_bounds_mm": {"min": [plot_left, plan["plot_bottom"]], "max": [plot_right, plan["plot_top"]]},
                      "stroke_range": [start_stroke, len(strokes)], "text_range": [start_text, len(texts)],
                      "symbols": symbol_rows, **axes})
    table_height = 2.8 if options["size"] == "A3" else 2.25
    top, bottom, row_height, header = plan["table_top"], plan["table_bottom"], plan["row_height"], plan["table_header"]
    stroke((border, top), (width - border, top), "title_block")
    for line in range(options["equipment_per_page"] + 1):
        y = top - header - line * row_height
        stroke((border, y), (width - border, y), "title_block")
    text((width / 2, top - header / 2), _table(_COLUMNS), table_height)
    for index, row in enumerate(rows):
        text((width / 2, top - header - (index + .5) * row_height), row["table_row_text"], table_height,
             source_index=row["source_index"], id=row["id"], annotation_kind="equipment_coordinate_row")
    text((width / 2, 37), "LOCAL AXIS ARROWS: X 1 TICK / Y 2 TICKS / Z 3 TICKS - SYMBOLS NOT TO SCALE", 1.8)
    text((width / 2, 30), "DX/DY/DEL: WORLD X/Y/Z MINUS DECLARED DATUM / MM", 1.9)
    text((width / 2, 23), _FOOTER, 2.0, "disclaimer")
    text((width / 2, 14), options["drawing_number"] + " / FULL REFERENCES IN SOURCE_ROWS", 2.2, "drawing_number")
    scene = {"success": True, "available": True, "complete": True,
             "drawing_kind": "WORLD equipment location and pose sheet", "coordinate_space": "paper", "coordinate_unit": "mm",
             "coordinate_frame": "paper millimetres; origin bottom-left; +X right; +Y up", "model_coordinate_unit": "mm",
             "source_coordinate_frame": "WORLD", "paper": {"size": options["size"], "orientation": "landscape", "width_mm": width, "height_mm": height},
             "paper_size_mm": [width, height], "options": deepcopy(dict(options)), "datum": deepcopy(dict(datum)),
             "datum_coordinate_frame": "WORLD", "datum_coordinate_unit": "mm",
             "datum_scope": "Declared coordinate reference; not an inferred equipment center.",
             "page_number": number, "page_count": count, "total_equipment_count": total, "equipment_count": len(rows),
             "source_indices": [row["source_index"] for row in rows], "source_rows": deepcopy(rows),
             "reference_map": {row["id"]: {key: deepcopy(row.get(key)) for key in
                               ("requested_refno", "resolved_refno", "element_name", "reference_display", "reference_display_abbreviated")} for row in rows},
             "position_bounds_world_mm": deepcopy(plan["position_bounds_world_mm"]),
             "global_model_bounds": deepcopy(plan["global_model_bounds"]), "scale_denominator": denominator,
             "scale_mode": "global_auto_fit", "scale_scope": "All pages and both views share one position scale and global WORLD bounds.",
             "relative_coordinate_convention": "DX/DY/DEL = WORLD X/Y/Z of equipment origin minus declared WORLD datum; millimetres.",
             "table_coordinate_decimal_places": 3, "table_coordinate_resolution_mm": .001,
             "table_precision_scope": "Rounded coordinate labels; source_rows retain complete input floating point coordinates and orientation.",
             "pose_symbol_length_paper_mm": _AXIS_LENGTH, "origin_symbol_half_paper_mm": _MARKER_HALF,
             "pose_axis_tick_legend": {"X": 1, "Y": 2, "Z": 3}, "minimum_projected_axis_paper_mm": _MIN_PROJECTED_AXIS,
             "symbol_scope": "Fixed paper-mm origin and local positive-axis symbols; symbols are not model lengths.",
             "plant_north_verified": False, "direction_scope": "WORLD axis directions only; no inferred plant north or east.",
             "views": views, "callouts": callouts, "strokes": strokes, "texts": texts,
             "counts": {"equipment": len(rows), "views": 2, "strokes": len(strokes), "texts": len(texts)},
             "footer": _FOOTER, "scope": _FOOTER, "extents_included": False,
             "native_draft_sheet": False, "engineering_dimensions_certified": False,
             "layout_policy": "Indexed equipment pages; fixed table capacity, common global WORLD datums and one position scale."}
    validate_drawing_sheet_scene(scene)
    boxes = [_text_box(item) for item in texts]
    for index, first in enumerate(boxes):
        for second in boxes[index + 1:]:
            if min(first[2], second[2]) > max(first[0], second[0]) and min(first[3], second[3]) > max(first[1], second[1]):
                raise ValueError("equipment_sheet_text_collision")
    return scene


def build_equipment_layout_sheets(drawing: Any, options: Any, *, datum: Any = None) -> list[dict[str, Any]]:
    """Paginate complete equipment poses at one global position scale.

    Stable E001 IDs follow ``drawing.equipment`` order. Geometry is regenerated
    from its verified WORLD origins and orientation rather than from glyphs
    that have no per-equipment source indices. This function performs no reads.
    """
    normalized = normalize_equipment_layout_sheet_options(options)
    reference = normalize_equipment_layout_datum(datum)
    rows = _prepare_rows(drawing, reference)
    plan = _plan(rows, normalized)
    capacity = normalized["equipment_per_page"]
    count = (len(rows) + capacity - 1) // capacity
    return [_page(rows[start:start + capacity], normalized, reference, plan, start // capacity + 1, count, len(rows))
            for start in range(0, len(rows), capacity)]
