"""Readable paper-mm nozzle point sheets from complete native WORLD evidence.

These scenes retain P-point identities and the supplied PPOS/PDIR values. They
do not infer an equipment envelope, nominal diameter or connection validity.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from typing import Any

from .drawing_sheet_render import SHEET_TEXT_CHARACTERS, validate_drawing_sheet_scene


_OPTIONS = {"size", "title", "drawing_number", "points_per_page", "datum"}
_PAPERS = {"A3": (420.0, 297.0), "A4": (297.0, 210.0)}
_MAX_POINTS = 128
_MAX_LINES = 20_000
_MODEL_LIMIT = 1_000_000_000.0
_DIRECTION_SYMBOL_LENGTH_MM = 8.0
_POSITION_SYMBOL_HALF_MM = .9
_SYMBOL_PADDING_MM = 10.0
_COLUMNS = ("ID", "REF", "PPOINT", "X MM", "Y MM", "Z MM", "AZ DEG", "TILT DEG")
_DATUM_COLUMNS = ("ID", "REF", "PPOINT", "DX MM", "DY MM", "DEL MM", "AZ DEG", "TILT DEG")
_COLUMN_WIDTHS = (4, 26, 6, 15, 15, 15, 8, 8)


def _option_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"invalid_nozzle_sheet_{field}")
    if any(ord(character) < 32 or ord(character) >= 127 for character in value):
        raise ValueError(f"unsupported_nozzle_sheet_{field}_characters")
    result = value.strip().upper()
    if not result or any(character not in SHEET_TEXT_CHARACTERS for character in result):
        raise ValueError(f"unsupported_nozzle_sheet_{field}_characters")
    return result


def normalize_nozzle_datum(value: Any) -> dict[str, Any]:
    """Validate an explicit user-declared WORLD origin without native reads."""
    if not isinstance(value, Mapping) or set(value) != {"label", "origin_mm"}:
        raise ValueError("invalid_nozzle_datum")
    label = _option_text(value["label"], "datum_label", 32)
    origin = value["origin_mm"]
    if not isinstance(origin, (list, tuple)) or len(origin) != 3:
        raise ValueError("invalid_nozzle_datum_origin_mm")
    origin = [_number(coordinate, "datum_origin_mm") for coordinate in origin]
    if any(abs(coordinate) > _MODEL_LIMIT for coordinate in origin):
        raise ValueError("nozzle_datum_origin_limit_exceeded")
    return {"label": label, "origin_mm": origin}


def normalize_nozzle_sheet_options(options: Any) -> dict[str, Any]:
    """Validate legacy options and an optional internal datum without E3D."""
    if not isinstance(options, Mapping) or set(options) - _OPTIONS:
        raise ValueError("invalid_nozzle_sheet_options")
    size = options.get("size", "A3")
    if not isinstance(size, str) or size not in _PAPERS:
        raise ValueError("invalid_nozzle_sheet_size")
    count = options.get("points_per_page", 8)
    if isinstance(count, bool) or not isinstance(count, int) or count not in (8, 16):
        raise ValueError("invalid_nozzle_sheet_points_per_page")
    result = {
        "size": size,
        "title": _option_text(options.get("title", "NOZZLE ORIENTATION"), "title", 48),
        "drawing_number": _option_text(options.get("drawing_number", "E3D-NOZZLE"), "drawing_number", 32),
        "points_per_page": count,
    }
    if "datum" in options:
        result["datum"] = normalize_nozzle_datum(options["datum"])
    return result


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_nozzle_sheet_{field}")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"invalid_nozzle_sheet_{field}") from None
    if not math.isfinite(result):
        raise ValueError(f"invalid_nozzle_sheet_{field}")
    return 0.0 if result == 0 else result


def _point(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("invalid_nozzle_sheet_line_point")
    result = [_number(component, "line_point") for component in value]
    if any(abs(component) > _MODEL_LIMIT for component in result):
        raise ValueError("nozzle_sheet_model_coordinate_limit_exceeded")
    return result


def _vector(value: Any, field: str) -> list[float]:
    if not isinstance(value, Mapping) or value.get("available") is not True:
        raise ValueError(f"nozzle_sheet_{field}_unavailable")
    result = [_number(value.get(axis), field) for axis in ("x", "y", "z")]
    if field == "position" and (value.get("unit") != "mm" or
                                 any(abs(component) > _MODEL_LIMIT for component in result)):
        raise ValueError("nozzle_sheet_world_millimetre_position_required")
    return result


def _fixed(value: float, precision: int) -> str:
    result = format(value, f".{precision}f")
    return result[1:] if result.startswith("-") and float(result) == 0 else result


def _table_text(values: Sequence[str]) -> str:
    if any(len(value) > width for value, width in zip(values, _COLUMN_WIDTHS)):
        raise ValueError("nozzle_sheet_table_value_does_not_fit")
    return " ".join(value.ljust(width) if index < 3 else value.rjust(width)
                    for index, (value, width) in enumerate(zip(values, _COLUMN_WIDTHS)))


def _native_frame_refno(row: Mapping[str, Any]) -> str | None:
    frame = row.get("native_frame")
    if frame is None:
        return None
    if (not isinstance(frame, Mapping) or frame.get("requested") != "WORLD" or
            frame.get("type") != "WORL" or not isinstance(frame.get("refno"), str) or
            not frame["refno"].strip()):
        raise ValueError("nozzle_sheet_native_world_frame_required")
    return frame["refno"]


def _prepare_rows(drawing: Any) -> list[dict[str, Any]]:
    if (not isinstance(drawing, Mapping) or drawing.get("success") is not True or
            drawing.get("complete") is not True or drawing.get("available", True) is not True):
        raise ValueError("successful_complete_nozzle_drawing_required")
    if drawing.get("coordinate_frame") != "WORLD" or drawing.get("coordinate_unit") != "mm":
        raise ValueError("nozzle_sheet_world_millimetres_required")
    raw_rows = drawing.get("nozzles")
    if not isinstance(raw_rows, (list, tuple)) or not 1 <= len(raw_rows) <= _MAX_POINTS:
        raise ValueError("nozzle_sheet_point_limit_exceeded_or_invalid")
    if "nozzle_count" in drawing and drawing["nozzle_count"] != len(raw_rows):
        raise ValueError("nozzle_sheet_point_count_mismatch")
    prepared: list[dict[str, Any]] = []
    seen: set[int] = set()
    world_frame_refno: str | None = None
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            raise ValueError("invalid_nozzle_sheet_source_row")
        native_frame = _native_frame_refno(raw)
        if native_frame is not None:
            if world_frame_refno is not None and native_frame != world_frame_refno:
                raise ValueError("nozzle_sheet_native_world_frame_mismatch")
            world_frame_refno = native_frame
        index, point_number, refno = raw.get("source_index"), raw.get("point_number"), raw.get("refno")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < _MAX_POINTS or index in seen:
            raise ValueError("invalid_or_duplicate_nozzle_sheet_source_index")
        seen.add(index)
        if isinstance(point_number, bool) or not isinstance(point_number, int) or not 1 <= point_number <= 10_000:
            raise ValueError("invalid_nozzle_sheet_point_number")
        if not isinstance(refno, str) or not refno.strip() or len(refno) > 1024:
            raise ValueError("invalid_nozzle_sheet_reference")
        if raw.get("available") is not True or raw.get("direction_available") is not True or raw.get("complete", True) is not True:
            raise ValueError("nozzle_sheet_source_row_unavailable_or_incomplete")
        position, direction = _vector(raw.get("position"), "position"), _vector(raw.get("direction"), "direction")
        norm = math.hypot(*direction)
        if not math.isfinite(norm) or norm <= 0:
            raise ValueError("nozzle_sheet_nonzero_direction_required")
        dx, dy, dz = (component / norm for component in direction)
        horizontal = math.hypot(dx, dy)
        tilt = _number(raw.get("tilt_deg"), "tilt_deg")
        if not -90 <= tilt <= 90 or abs(tilt - math.degrees(math.atan2(dz, horizontal))) > 1e-6:
            raise ValueError("nozzle_sheet_tilt_direction_mismatch")
        if horizontal < 1e-9:
            if raw.get("bearing_available") is not False or raw.get("bearing_deg") is not None:
                raise ValueError("nozzle_sheet_vertical_bearing_must_be_unavailable")
            azimuth = "NA"
        else:
            bearing = _number(raw.get("bearing_deg"), "bearing_deg")
            expected = math.degrees(math.atan2(dx, dy)) % 360.0
            difference = (bearing - expected + 180.0) % 360.0 - 180.0
            if raw.get("bearing_available") is not True or not 0 <= bearing < 360 or abs(difference) > 1e-6:
                raise ValueError("nozzle_sheet_bearing_direction_mismatch")
            azimuth = _fixed(bearing, 2)
        identifier = f"N{index + 1:03d}"
        abbreviated = len(refno) > _COLUMN_WIDTHS[1] or any(character not in SHEET_TEXT_CHARACTERS for character in refno)
        display_ref = f"SRC{index + 1:03d}" if abbreviated else refno
        table_values = {
            "id": identifier, "ref": display_ref, "point_number": str(point_number),
            "x_mm": _fixed(position[0], 3), "y_mm": _fixed(position[1], 3), "z_mm": _fixed(position[2], 3),
            "azimuth_deg": azimuth, "tilt_deg": _fixed(tilt, 2),
        }
        row = copy.deepcopy(dict(raw))
        row.update(id=identifier, reference_display=display_ref, reference_display_abbreviated=abbreviated,
                   table_values=table_values, table_row_text=_table_text(tuple(table_values.values())))
        prepared.append(row)
    return sorted(prepared, key=lambda row: row["source_index"])


def _prepare_lines(drawing: Mapping[str, Any], indices: set[int]) -> dict[str, list[dict[str, Any]]]:
    raw_views = drawing.get("views")
    if not isinstance(raw_views, (list, tuple)) or len(raw_views) != 2:
        raise ValueError("nozzle_sheet_top_front_views_required")
    views: dict[str, list[dict[str, Any]]] = {}
    total = 0
    for raw in raw_views:
        if not isinstance(raw, Mapping):
            raise ValueError("invalid_nozzle_sheet_view")
        name = raw.get("name")
        if not isinstance(name, str) or name not in ("TOP", "FRONT") or name in views:
            raise ValueError("nozzle_sheet_top_front_views_required")
        if raw.get("coordinate_unit", "mm") != "mm" or raw.get("coordinate_frame", "WORLD") != "WORLD":
            raise ValueError("nozzle_sheet_view_world_millimetres_required")
        raw_lines = raw.get("lines")
        if not isinstance(raw_lines, (list, tuple)):
            raise ValueError("invalid_nozzle_sheet_lines")
        total += len(raw_lines)
        if total > _MAX_LINES:
            raise ValueError("nozzle_sheet_line_limit_exceeded")
        lines = []
        represented: set[int] = set()
        for line_index, line in enumerate(raw_lines):
            if not isinstance(line, Mapping):
                raise ValueError("invalid_nozzle_sheet_line")
            source_index = line.get("source_index")
            if isinstance(source_index, bool) or not isinstance(source_index, int) or source_index not in indices:
                raise ValueError("nozzle_sheet_line_source_mismatch")
            if line.get("kind") not in ("feature", "silhouette") or line.get("visibility", "visible") != "visible":
                raise ValueError("invalid_nozzle_sheet_line_kind_or_visibility")
            start, end = _point(line.get("start")), _point(line.get("end"))
            if start == end:
                raise ValueError("nozzle_sheet_degenerate_line")
            represented.add(source_index)
            lines.append({"source_index": source_index, "source_line_index": line_index,
                          "kind": line["kind"], "start": start, "end": end})
        if represented != indices:
            raise ValueError("nozzle_sheet_source_view_linework_missing")
        views[name] = lines
    return views


def _auto_denominator(required: float) -> float:
    required = max(1.0, required)
    base = 10.0 ** math.floor(math.log10(required))
    for multiple in (1.0, 2.0, 5.0, 10.0):
        candidate = multiple * base
        if candidate >= required:
            return candidate
    raise ValueError("nozzle_sheet_scale_unavailable")


def _prepare_datum_rows(rows: list[dict[str, Any]], datum: dict[str, Any]) -> None:
    for row in rows:
        relative = [_number(row["position"][axis] - origin, "relative_position")
                    for axis, origin in zip(("x", "y", "z"), datum["origin_mm"])]
        original = row["table_values"]
        values = {"id": original["id"], "ref": original["ref"], "point_number": original["point_number"],
                  "dx_mm": _fixed(relative[0], 3), "dy_mm": _fixed(relative[1], 3), "del_mm": _fixed(relative[2], 3),
                  "azimuth_deg": original["azimuth_deg"], "tilt_deg": original["tilt_deg"]}
        row.update(relative_position_mm=relative, table_values=values, table_row_text=_table_text(tuple(values.values())))


def _datum_shared_extent(rows: list[dict[str, Any]], capacity: int) -> dict[str, Any]:
    bounds = {}
    for name, vertical in (("TOP", "y"), ("FRONT", "z")):
        points = [[row["position"]["x"], row["position"][vertical]] for row in rows]
        bounds[name] = {"min": [min(point[axis] for point in points) for axis in (0, 1)],
                        "max": [max(point[axis] for point in points) for axis in (0, 1)]}
    return {"model_bounds": bounds, "table_rows": min(len(rows), capacity)}


def _build_page(rows: list[dict[str, Any]], lines: dict[str, list[dict[str, Any]]], options: dict[str, Any],
                page_number: int, page_count: int, *, shared_extent: dict[str, Any] | None = None) -> dict[str, Any]:
    width, height = _PAPERS[options["size"]]
    indices = [row["source_index"] for row in rows]
    selected = set(indices)
    page_lines = {name: [line for line in source if line["source_index"] in selected] for name, source in lines.items()}
    border, gap, table_bottom, table_header = 8.0, 8.0, 28.0, 6.0
    row_height = 5.4 if options["size"] == "A3" else 4.8
    table_rows = len(rows) if shared_extent is None else shared_extent["table_rows"]
    table_top = table_bottom + table_header + table_rows * row_height
    plot_bottom, plot_top = table_top + 8.0, height - 39.0
    cell_width = (width - 2 * border - gap) / 2
    plot_width, plot_height = cell_width - 31.0, plot_top - plot_bottom
    if plot_height <= 0 or (len(rows) > 1 and (plot_height - 4) / (len(rows) - 1) < 3.0):
        raise ValueError("nozzle_sheet_callout_lanes_do_not_fit")
    point_width, point_height = plot_width - 2 * _SYMBOL_PADDING_MM, plot_height - 2 * _SYMBOL_PADDING_MM
    if point_width <= 0 or point_height <= 0:
        raise ValueError("nozzle_sheet_symbol_padding_does_not_fit")
    model_bounds = {}
    for name in ("TOP", "FRONT"):
        vertical = "y" if name == "TOP" else "z"
        points = [[row["position"]["x"], row["position"][vertical]] for row in rows]
        model_bounds[name] = {"min": [min(point[axis] for point in points) for axis in (0, 1)],
                              "max": [max(point[axis] for point in points) for axis in (0, 1)]}
    if shared_extent is not None:
        model_bounds = copy.deepcopy(shared_extent["model_bounds"])
    # Both cells use one X datum as well as exactly the same model/paper scale.
    xmin = min(bounds["min"][0] for bounds in model_bounds.values())
    xmax = max(bounds["max"][0] for bounds in model_bounds.values())
    for bounds in model_bounds.values():
        bounds["min"][0], bounds["max"][0] = xmin, xmax
    required = max(max((bounds["max"][0] - bounds["min"][0]) / point_width,
                       (bounds["max"][1] - bounds["min"][1]) / point_height) for bounds in model_bounds.values())
    denominator = _auto_denominator(required)
    strokes: list[dict[str, Any]] = []
    texts: list[dict[str, Any]] = []

    def stroke(start, end, role="feature", view_name=None, **metadata):
        if tuple(start) != tuple(end):
            strokes.append({"start": list(start), "end": list(end), "role": role,
                            "view_name": view_name, "visibility": "visible", **metadata})

    def text(position, content, text_height, role="sheet_metadata", view_name=None, **metadata):
        texts.append({"position": list(position), "text": content, "height": text_height,
                      "rotation": 0, "role": role, "view_name": view_name, **metadata})

    for start, end in (((border, border), (width - border, border)),
                       ((width - border, border), (width - border, height - border)),
                       ((width - border, height - border), (border, height - border)),
                       ((border, height - border), (border, border))):
        stroke(start, end, "border")
    stroke((border, height - 28), (width - border, height - 28), "title_block")
    text((width / 2, height - 15), options["title"], 4.0, "title")
    scale_text = format(denominator, ".9f").rstrip("0").rstrip(".")
    summary = f"WORLD MM / POSITION SCALE 1:{scale_text} / PAGE {page_number}/{page_count}"
    if "datum" in options:
        summary += " / AZ 0 +Y TO +X / TILT +Z / NA VERTICAL"
    text((width / 2, height - 23), summary, 2.5)

    placed_views, callouts = [], []
    for cell_index, name in enumerate(("TOP", "FRONT")):
        cell_left = border + cell_index * (cell_width + gap)
        plot_left = cell_left + 5.0
        plot_right = plot_left + plot_width
        bounds = model_bounds[name]
        span = [bounds["max"][axis] - bounds["min"][axis] for axis in (0, 1)]
        origin = [plot_left + (plot_width - span[0] / denominator) / 2,
                  plot_bottom + (plot_height - span[1] / denominator) / 2]

        def project(point):
            return [origin[axis] + (point[axis] - bounds["min"][axis]) / denominator for axis in (0, 1)]

        text((cell_left + cell_width / 2, height - 33), f"{name} - X/{'Y' if name == 'TOP' else 'Z'}", 3.0,
             "view_label", name)
        vertical = "y" if name == "TOP" else "z"
        for row in rows:
            anchor = project((row["position"]["x"], row["position"][vertical]))
            metadata = {"source_index": row["source_index"], "id": row["id"],
                        "annotation_kind": "point_position_symbol"}
            half = _POSITION_SYMBOL_HALF_MM
            stroke((anchor[0] - half, anchor[1]), (anchor[0] + half, anchor[1]), "feature", name, **metadata)
            stroke((anchor[0], anchor[1] - half), (anchor[0], anchor[1] + half), "feature", name, **metadata)
            direction = [row["direction"][axis] for axis in ("x", "y", "z")]
            norm = math.hypot(*direction)
            dx = direction[0] / norm * _DIRECTION_SYMBOL_LENGTH_MM
            dy = direction[1 if name == "TOP" else 2] / norm * _DIRECTION_SYMBOL_LENGTH_MM
            projected_length = math.hypot(dx, dy)
            if projected_length <= 1e-6:
                # End-on directions have no stable representable paper arrow.
                # The point marker, native direction and table remain present.
                continue
            end = [anchor[0] + dx, anchor[1] + dy]
            metadata["annotation_kind"] = "point_direction_symbol"
            stroke(anchor, end, "feature", name, **metadata, symbol_part="shaft")
            ux, uy = dx / projected_length, dy / projected_length
            head = min(2.0, projected_length * .35)
            cosine, sine = math.cos(math.radians(25)), math.sin(math.radians(25))
            for sign in (-1.0, 1.0):
                head_end = [end[0] - head * (cosine * ux - sign * sine * uy),
                            end[1] - head * (cosine * uy + sign * sine * ux)]
                stroke(head_end, end, "feature", name, **metadata, symbol_part="arrowhead")
        ordered = sorted(rows, key=lambda row: (-row["position"][vertical], row["position"]["x"], row["source_index"]))
        label_x = cell_left + cell_width - 10.0
        view_callouts = []
        for lane, row in enumerate(ordered):
            label_y = ((plot_bottom + plot_top) / 2 if len(rows) == 1 else
                       plot_top - 2.0 - lane * (plot_height - 4.0) / (len(rows) - 1))
            anchor = project((row["position"]["x"], row["position"][vertical]))
            elbow, end = [plot_right + 2.0, label_y], [label_x - 6.5, label_y]
            metadata = {"source_index": row["source_index"], "annotation_kind": "point_leader", "id": row["id"]}
            stroke(anchor, elbow, "feature", name, **metadata)
            stroke(elbow, end, "feature", name, **metadata)
            text((label_x, label_y), row["id"], 2.5, "sheet_metadata", name, source_index=row["source_index"])
            callout = {"id": row["id"], "source_index": row["source_index"], "view_name": name,
                       "point_number": row["point_number"], "anchor_mm": anchor,
                       "position_mm": [label_x, label_y], "leader_end_mm": end}
            view_callouts.append(callout)
            callouts.append(copy.deepcopy(callout))
        placed_views.append({"name": name, "source_indices": list(indices), "scale_denominator": denominator,
                             "paper_mm_per_model_mm": 1 / denominator, "model_bounds": copy.deepcopy(bounds),
                             "paper_origin_mm": origin,
                             "source_line_count": len(page_lines[name]), "symbol_padding_mm": _SYMBOL_PADDING_MM,
                             "direction_symbol_length_mm": _DIRECTION_SYMBOL_LENGTH_MM,
                             "direction_symbols_to_model_scale": False,
                             "transform": {"scale_denominator": denominator, "paper_mm_per_model_mm": 1 / denominator,
                                           "model_origin_mm": list(bounds["min"]), "paper_origin_mm": list(origin)},
                             "plot_bounds_mm": {"min": [plot_left, plot_bottom], "max": [plot_right, plot_top]},
                             "callouts": view_callouts})

    # One fixed-width text per table row stays within the shared renderer's
    # 128-text budget even for sixteen points and two sets of point callouts.
    table_text_height = 3.0 if options["size"] == "A3" else 2.6
    columns = _DATUM_COLUMNS if "datum" in options else _COLUMNS
    header = _table_text(columns)
    text((width / 2, table_top - table_header / 2), header, table_text_height)
    for row_index, row in enumerate(rows):
        center = table_top - table_header - (row_index + .5) * row_height
        text((width / 2, center), row["table_row_text"], table_text_height,
             source_index=row["source_index"], annotation_kind="point_table_row")
    stroke((border, table_top), (width - border, table_top), "title_block")
    for row_index in range(table_rows + 1):
        y = table_top - table_header - row_index * row_height
        stroke((border, y), (width - border, y), "title_block")
    stroke((border, table_bottom), (border, table_top), "title_block")
    stroke((width - border, table_bottom), (width - border, table_top), "title_block")
    abbreviated = any(row["reference_display_abbreviated"] for row in rows)
    disclaimer = ("SYMBOLS NOT TO SCALE / SRC REFS ABBREVIATED - FULL REFS IN SOURCE ROWS" if abbreviated else
                  "SYMBOLS NOT TO SCALE / NATIVE PPOS/PDIR - NO EQUIPMENT ENVELOPE")
    if "datum" in options:
        datum = options["datum"]
        text((width / 2, 22.0), f'USER DECLARED WORLD DATUM {datum["label"]} / NOT EQUIPMENT AXIS OR CENTER', 2.5, "disclaimer")
        x0, y0, z0 = (_fixed(value, 3) for value in datum["origin_mm"])
        text((width / 2, 17.0), f"X0 {x0} Y0 {y0} Z0 {z0} MM / OFFSETS PPOS MINUS DATUM / SYMBOLS NOT TO SCALE", 2.5, "disclaimer")
    else:
        text((width / 2, 22.0), disclaimer, 2.0, "disclaimer")
        text((width / 2, 17.0), "AZ 0 +Y TO +X / TILT +Z / NA VERTICAL / PPOINT NOT CONNECTION STATUS", 1.8, "disclaimer")
    text((width / 2, 12.0), options["drawing_number"], 2.5, "drawing_number")
    scene = {
        "success": True, "available": True, "complete": True,
        "coordinate_space": "paper", "coordinate_unit": "mm", "model_coordinate_unit": "mm",
        "model_coordinate_frame": "WORLD", "native_draft_sheet": False,
        "native_world_frame_refno": next((_native_frame_refno(row) for row in rows
                                          if row.get("native_frame") is not None), None),
        "native_world_frame_complete": all(row.get("native_frame") is not None for row in rows),
        "paper": {"size": options["size"], "orientation": "landscape", "width_mm": width, "height_mm": height},
        "paper_size_mm": [width, height], "page_number": page_number, "page_count": page_count,
        "source_indices": list(indices), "source_rows": copy.deepcopy(rows), "scale_denominator": denominator,
        "direction_symbol_length_mm": _DIRECTION_SYMBOL_LENGTH_MM, "direction_symbols_to_model_scale": False,
        "position_symbol_half_length_mm": _POSITION_SYMBOL_HALF_MM, "symbol_padding_mm": _SYMBOL_PADDING_MM,
        "scale_mode": "auto_fit", "options": dict(options), "views": placed_views, "callouts": callouts,
        "reference_display_abbreviated": abbreviated,
        "table": {"columns": list(columns), "coordinate_decimals": 3, "angle_decimals": 2,
                  "bounds_mm": {"min": [border, table_bottom], "max": [width - border, table_top]}},
        "strokes": strokes, "texts": texts,
        "counts": {"points": len(rows), "views": 2, "strokes": len(strokes), "texts": len(texts)},
        "scope": "Native WORLD PPOS/PDIR point locations and directions; no inferred equipment shape, nominal diameter or connection validity.",
    }
    if "datum" in options:
        scene.update(datum={**copy.deepcopy(options["datum"]), "coordinate_frame": "WORLD", "coordinate_unit": "mm",
                            "source": "user_declared", "origin_display_decimals": 3,
                            "equipment_axis_verified": False, "equipment_center_verified": False},
                     datum_scope="Signed offsets along unchanged WORLD axes from the user-declared origin; DEL is Z minus Z0. No equipment center, axis or radial nozzle bearing is inferred.",
                     orientation_convention={
                         "bearing_zero": "+Y", "bearing_positive": "toward +X (clockwise in TOP plan)",
                         "tilt_zero": "horizontal", "tilt_positive": "+Z", "vertical_bearing": "unavailable"},
                     scale_mode="shared_auto_fit", options=copy.deepcopy(options),
                     shared_layout={"common_scale": True, "common_model_bounds": True, "table_rows": table_rows,
                                    "model_bounds": copy.deepcopy(model_bounds)})
        scene["table"].update(coordinate_frame="user-declared WORLD datum offsets", elevation_scope="DEL is signed Z minus Z0",
                              world_positions_retained_in="source_rows.position", row_capacity=table_rows)
    validate_drawing_sheet_scene(scene)
    return scene


def build_nozzle_orientation_sheets(drawing: Any, options: Any) -> list[dict[str, Any]]:
    """Paginate complete WORLD/mm point evidence into renderer-compatible scenes.

    IDs are ``N{source_index + 1:03d}`` throughout the views and detail table.
    Every page preserves its full source rows and only its own source linework.
    An optional explicit datum changes the schedule to signed DX/DY/DEL and
    uses common WORLD bounds, scale and paper transforms across its pages.
    Scene construction copies input data and never reads or modifies E3D.
    """
    normalized = normalize_nozzle_sheet_options(options)
    rows = _prepare_rows(drawing)
    lines = _prepare_lines(drawing, {row["source_index"] for row in rows})
    size = normalized["points_per_page"]
    page_count = (len(rows) + size - 1) // size
    if "datum" in normalized:
        _prepare_datum_rows(rows, normalized["datum"])
        shared_extent = _datum_shared_extent(rows, size)
        return [_build_page(rows[start:start + size], lines, normalized, start // size + 1, page_count,
                            shared_extent=shared_extent) for start in range(0, len(rows), size)]
    return [_build_page(rows[start:start + size], lines, normalized, start // size + 1, page_count)
            for start in range(0, len(rows), size)]
