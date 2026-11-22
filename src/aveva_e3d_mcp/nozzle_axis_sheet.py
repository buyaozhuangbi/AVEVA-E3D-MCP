"""Nozzle radial position and PDIR sheets in an explicit native equipment frame.

The equipment's native POS defines a coordinate origin, not a certified physical
center. Selecting its coordinate axes does not establish nozzle ownership or a
physical equipment centerline. All computation is offline and preserves WORLD
PPOS/PDIR alongside the equipment-frame coordinates.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
import math
import re
from typing import Any

from .drawing_sheet_render import SHEET_TEXT_CHARACTERS, validate_drawing_sheet_scene
from .nozzle_orientation_sheet import (
    _auto_denominator,
    _fixed,
    _prepare_rows,
    normalize_nozzle_sheet_options,
)


_PAPERS = {"A3": (420.0, 297.0), "A4": (297.0, 210.0)}
_AXES = {"X": "x_axis", "Y": "y_axis", "Z": "z_axis"}
_LIMIT_MM = 1_000_000_000.0
_ORTHOGONAL_TOLERANCE = 1e-6
_POSITION_ANGLE_THRESHOLD_MM = 1e-9
_DIRECTION_ANGLE_THRESHOLD = 1e-9
_SYMBOL_PADDING_MM = 17.0
_DIRECTION_LENGTH_MM = 8.0
_COLUMNS = ("ID", "REF", "PPOINT", "R MM", "ANG DEG", "AXIAL MM", "DIR DEG", "TILT DEG")
_WIDTHS = (4, 26, 6, 15, 8, 15, 8, 8)
_CONVENTION = {
    "position_angle": "atan2(v, u) modulo 360; radial PPOS location about the selected equipment origin",
    "direction_azimuth": "atan2(PDIR dot v, PDIR dot u) modulo 360; PDIR direction, independent of point location",
    "positive_angle": "+u toward +v, right hand about +n",
    "axial_coordinate": "h = (WORLD PPOS minus native equipment POS) dot n; signed millimetres",
    "direction_tilt": "atan2(PDIR dot n, hypot(PDIR dot u, PDIR dot v)); positive toward +n",
    "position_angle_threshold_mm": _POSITION_ANGLE_THRESHOLD_MM,
    "direction_azimuth_threshold": _DIRECTION_ANGLE_THRESHOLD,
    "unavailable_position_angle": "radial distance at or below position_angle_threshold_mm",
    "unavailable_direction_azimuth": "normalized PDIR radial component at or below direction_azimuth_threshold",
}


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_nozzle_axis_{field}")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"invalid_nozzle_axis_{field}") from None
    if not math.isfinite(number):
        raise ValueError(f"invalid_nozzle_axis_{field}")
    return 0.0 if number == 0 else number


def _vector(value: Any, field: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"invalid_nozzle_axis_{field}")
    return [_number(component, field) for component in value]


def _dot(a: list[float], b: list[float]) -> float:
    value = math.fsum(left * right for left, right in zip(a, b))
    return 0.0 if value == 0 else value


def _cross(a: list[float], b: list[float]) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _reference_key(value: Any, *, numeric: bool = False) -> str:
    if (not isinstance(value, str) or not 2 <= len(value) <= 1024 or value != value.strip()
            or any(character.isspace() or ord(character) < 32 or 127 <= ord(character) < 160 for character in value)):
        raise ValueError("invalid_nozzle_axis_equipment_identity")
    if re.fullmatch(r"=[0-9]+/[0-9]+", value):
        components = [component.lstrip("0") or "0" for component in value[1:].split("/")]
        if components[0] == "0":
            raise ValueError("invalid_nozzle_axis_equipment_identity")
        return "=" + "/".join(components)
    if not numeric and value.startswith("/"):
        return value.casefold()
    raise ValueError("invalid_nozzle_axis_equipment_identity")


def _frame_key(value: Any) -> str:
    if (not isinstance(value, Mapping) or value.get("requested") != "WORLD" or value.get("type") != "WORL"):
        raise ValueError("nozzle_axis_native_world_frame_required")
    key = _reference_key(value.get("refno"), numeric=True)
    if key.rsplit("/", 1)[1] != "0":
        raise ValueError("nozzle_axis_native_world_frame_required")
    return key


def _equipment_frame(pose: Any, axial_axis: Any, zero_axis: Any) -> dict[str, Any]:
    if (not isinstance(axial_axis, str) or axial_axis not in _AXES
            or not isinstance(zero_axis, str) or zero_axis not in _AXES or axial_axis == zero_axis):
        raise ValueError("nozzle_axis_distinct_axial_and_zero_axes_required")
    if (not isinstance(pose, Mapping)
            or any(pose.get(flag) is not True for flag in ("success", "available", "complete"))):
        raise ValueError("nozzle_axis_complete_equipment_pose_required")
    if pose.get("coordinate_frame") != "WORLD" or pose.get("coordinate_unit") != "mm":
        raise ValueError("nozzle_axis_equipment_world_millimetres_required")
    requested = _reference_key(pose.get("requested_refno"))
    resolved = _reference_key(pose.get("resolved_refno"), numeric=True)
    name = pose.get("element_name")
    if requested.startswith("="):
        agrees = requested == resolved
    else:
        agrees = isinstance(name, str) and _reference_key(name) == requested
    if not agrees:
        raise ValueError("nozzle_axis_equipment_identity_mismatch")
    native_frame = _frame_key(pose.get("native_frame"))
    position = _vector(pose.get("position"), "equipment_position")
    if any(abs(value) > _LIMIT_MM for value in position):
        raise ValueError("nozzle_axis_equipment_position_limit_exceeded")
    orientation = pose.get("orientation")
    if not isinstance(orientation, Mapping):
        raise ValueError("nozzle_axis_complete_equipment_orientation_required")
    axes = {name: _vector(orientation.get(field), "equipment_orientation") for name, field in _AXES.items()}
    if (any(abs(math.hypot(*axis) - 1) > _ORTHOGONAL_TOLERANCE for axis in axes.values())
            or any(abs(_dot(axes[a], axes[b])) > _ORTHOGONAL_TOLERANCE for a, b in (("X", "Y"), ("X", "Z"), ("Y", "Z")))
            or _dot(_cross(axes["X"], axes["Y"]), axes["Z"]) < 1 - _ORTHOGONAL_TOLERANCE):
        raise ValueError("nozzle_axis_right_handed_orthonormal_equipment_orientation_required")
    unit_axes = {name: [value / math.hypot(*axis) for value in axis] for name, axis in axes.items()}
    u, n = unit_axes[zero_axis], unit_axes[axial_axis]
    v = _cross(n, u)
    v = [value / math.hypot(*v) for value in v]
    remaining = next(name for name in _AXES if name not in (axial_axis, zero_axis))
    v_sign = 1 if _dot(v, unit_axes[remaining]) > 0 else -1
    return {
        "requested_refno": pose["requested_refno"], "resolved_refno": pose["resolved_refno"],
        "element_name": name, "origin_world_mm": position,
        "native_orientation_world": deepcopy(dict(orientation)),
        "coordinate_frame": "WORLD", "coordinate_unit": "mm", "native_world_frame_refno": native_frame,
        "axial_axis": axial_axis, "zero_axis": zero_axis, "v_axis": remaining, "v_axis_sign": v_sign,
        "basis_world": {"u": u, "v": v, "n": n}, "basis_definition": "u = selected positive zero axis; n = selected positive axial axis; v = n cross u",
        "orientation_validation_tolerance": _ORTHOGONAL_TOLERANCE,
        "origin_source": "native equipment WORLD POS", "axis_source": "native equipment WORLD ORI",
        "physical_center_verified": False, "physical_centerline_verified": False, "nozzle_ownership_verified": False,
        "native_pose": deepcopy(dict(pose)),
    }


def _table(values) -> str:
    if len(values) != len(_WIDTHS) or any(len(value) > width for value, width in zip(values, _WIDTHS)):
        raise ValueError("nozzle_axis_table_value_does_not_fit")
    return " ".join(value.ljust(width) if index < 3 else value.rjust(width)
                    for index, (value, width) in enumerate(zip(values, _WIDTHS)))


def _angle_text(value: float | None) -> str:
    return "NA" if value is None else _fixed(round(value, 2) % 360, 2)


def _prepare_axis_rows(drawing: Any, frame: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(drawing, Mapping) or any(drawing.get(flag) is not True for flag in ("success", "available", "complete")):
        raise ValueError("nozzle_axis_complete_world_drawing_required")
    rows = _prepare_rows(drawing)
    basis, origin = frame["basis_world"], frame["origin_world_mm"]
    point_keys = set()
    for row in rows:
        if _frame_key(row.get("native_frame")) != frame["native_world_frame_refno"]:
            raise ValueError("nozzle_axis_equipment_and_nozzle_world_frame_mismatch")
        resolved = _reference_key(row.get("resolved_refno"), numeric=True)
        requested = _reference_key(row.get("refno"))
        name = row.get("element_name")
        if (requested != resolved if requested.startswith("=") else
                not isinstance(name, str) or _reference_key(name) != requested):
            raise ValueError("nozzle_axis_nozzle_identity_mismatch")
        key = resolved, row["point_number"]
        if key in point_keys:
            raise ValueError("nozzle_axis_duplicate_resolved_point")
        point_keys.add(key)
        delta = [row["position"][axis] - origin[index] for index, axis in enumerate(("x", "y", "z"))]
        u, v, h = (_dot(delta, basis[axis]) for axis in ("u", "v", "n"))
        radius = math.hypot(u, v)
        angle = math.degrees(math.atan2(v, u)) % 360 if radius > _POSITION_ANGLE_THRESHOLD_MM else None
        direction = [row["direction"][axis] for axis in ("x", "y", "z")]
        norm = math.hypot(*direction)
        if not math.isfinite(norm) or norm <= 0:
            raise ValueError("nozzle_axis_nonzero_finite_direction_required")
        direction = [value / norm for value in direction]
        du, dv, dh = (_dot(direction, basis[axis]) for axis in ("u", "v", "n"))
        radial_direction = math.hypot(du, dv)
        azimuth = math.degrees(math.atan2(dv, du)) % 360 if radial_direction > _DIRECTION_ANGLE_THRESHOLD else None
        tilt = math.degrees(math.atan2(dh, radial_direction))
        values = {"id": row["id"], "ref": row["reference_display"], "point_number": str(row["point_number"]),
                  "radius_mm": _fixed(radius, 3), "position_angle_deg": _angle_text(angle),
                  "axial_mm": _fixed(h, 3), "direction_azimuth_deg": _angle_text(azimuth),
                  "direction_tilt_deg": _fixed(tilt, 2)}
        row.update(world_table_values=deepcopy(row["table_values"]), equipment_frame=deepcopy(frame),
                   equipment_delta_world_mm=delta, equipment_coordinates_mm={"u": u, "v": v, "h": h},
                   radius_mm=radius, position_angle_deg=angle, position_angle_available=angle is not None,
                   axial_mm=h, direction_basis={"u": du, "v": dv, "h": dh},
                   direction_azimuth_deg=azimuth, direction_azimuth_available=azimuth is not None,
                   direction_tilt_deg=tilt, angle_convention=deepcopy(_CONVENTION),
                   table_values=values, table_row_text=_table(tuple(values.values())))
    return rows


def _plan(rows: list[dict[str, Any]], options: dict[str, Any], outline_projection=None) -> dict[str, Any]:
    width, height = _PAPERS[options["size"]]
    table_rows = min(len(rows), options["points_per_page"])
    row_height = 5.0 if options["size"] == "A3" else 4.1
    table_bottom, table_header = 32.0, 6.0
    table_top = table_bottom + table_header + table_rows * row_height
    plot_bottom, plot_top = table_top + 8.0, height - 45.0
    cell_width = (width - 24.0) / 2
    # Reserve a separate legend gutter so a dense point cloud cannot obscure
    # the angle convention. Both views keep identical point-plot widths.
    plot_width, plot_height = cell_width - 53.0, plot_top - plot_bottom
    if (plot_width <= 2 * _SYMBOL_PADDING_MM or plot_height <= 2 * _SYMBOL_PADDING_MM
            or table_rows > 1 and (plot_height - 4) / (table_rows - 1) < 2.6):
        raise ValueError("nozzle_axis_point_and_callout_area_does_not_fit")
    bounds = {}
    for name, vertical in (("TOP", "v"), ("FRONT", "h")):
        points = [[0.0, 0.0], *[[row["equipment_coordinates_mm"][axis] for axis in ("u", vertical)] for row in rows]]
        if outline_projection is not None:
            source = next(view for view in outline_projection["views"] if view["renderer_view"] == name)
            points.extend([source["bounds"]["min"], source["bounds"]["max"]])
        bounds[name] = {"min": [min(point[axis] for point in points) for axis in (0, 1)],
                        "max": [max(point[axis] for point in points) for axis in (0, 1)]}
    denominator = _auto_denominator(max(max((item["max"][0] - item["min"][0]) / (plot_width - 2 * _SYMBOL_PADDING_MM),
                                            (item["max"][1] - item["min"][1]) / (plot_height - 2 * _SYMBOL_PADDING_MM))
                                       for item in bounds.values()))
    if "scale_denominator" in options:
        denominator = options["scale_denominator"]
        if any((item["max"][0] - item["min"][0]) / denominator > plot_width - 2 * _SYMBOL_PADDING_MM
               or (item["max"][1] - item["min"][1]) / denominator > plot_height - 2 * _SYMBOL_PADDING_MM
               for item in bounds.values()):
            raise ValueError("nozzle_outline_explicit_scale_does_not_fit")
    return {"width": width, "height": height, "table_rows": table_rows, "row_height": row_height,
            "table_bottom": table_bottom, "table_top": table_top, "table_header": table_header,
            "plot_bottom": plot_bottom, "plot_top": plot_top, "cell_width": cell_width,
            "plot_width": plot_width, "plot_height": plot_height, "model_bounds": bounds,
            "scale_denominator": denominator}


def _page(rows, options, frame, plan, number, count, outline_projection=None):
    width, height, denominator = plan["width"], plan["height"], plan["scale_denominator"]
    strokes, texts, views, callouts = [], [], [], []

    def stroke(a, b, role="feature", view_name=None, **metadata):
        if tuple(a) != tuple(b):
            strokes.append({"start": list(a), "end": list(b), "role": role, "view_name": view_name,
                            "visibility": "visible", **metadata})

    def text(position, content, size=2.4, role="sheet_metadata", view_name=None, **metadata):
        texts.append({"position": list(position), "text": content, "height": size, "rotation": 0,
                      "role": role, "view_name": view_name, **metadata})

    def arrow(a, b, view_name, **metadata):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length <= 1e-4:
            return
        stroke(a, b, view_name=view_name, symbol_part="shaft", **metadata)
        ux, uy, head = dx / length, dy / length, min(2.0, length * .35)
        cosine, sine = math.cos(math.radians(25)), math.sin(math.radians(25))
        for sign in (-1, 1):
            stroke([b[0] - head * (cosine * ux - sign * sine * uy),
                    b[1] - head * (cosine * uy + sign * sine * ux)], b,
                   view_name=view_name, symbol_part="arrowhead", **metadata)

    outline_placement = None
    if outline_projection is not None:
        from .nozzle_outline_sheet import _place_outline_geometry
        outline_placement = _place_outline_geometry(strokes, outline_projection, plan)

    for a, b in (((8, 8), (width - 8, 8)), ((width - 8, 8), (width - 8, height - 8)),
                 ((width - 8, height - 8), (8, height - 8)), ((8, height - 8), (8, 8))):
        stroke(a, b, "border")
    stroke((8, height - 35), (width - 8, height - 35), "title_block")
    text((width / 2, height - 15), options["title"], 4.0, "title")
    scale_text = format(denominator, ".9f").rstrip("0").rstrip(".")
    text((width / 2, height - 23), f"EQUIPMENT LOCAL MM / POSITION SCALE 1:{scale_text} / PAGE {number}/{count}", 2.5)
    display = (frame.get("element_name") or frame["requested_refno"]).upper()
    abbreviated = len(display) > 30 or any(character not in SHEET_TEXT_CHARACTERS for character in display)
    display = "EQUIPMENT SRC" if abbreviated else display
    v_label = ("+" if frame["v_axis_sign"] > 0 else "-") + frame["v_axis"]
    text((width / 2, height - 30), f"{display} / N +{frame['axial_axis']} / U +{frame['zero_axis']} / V {v_label} / +U TO +V ABOUT +N", 2.4)

    for index, (name, semantic, vertical) in enumerate((("TOP", "RADIAL", "v"), ("FRONT", "AXIAL_ELEVATION", "h"))):
        left = 8 + index * (plan["cell_width"] + 8)
        plot_left, plot_right = left + 27, left + 27 + plan["plot_width"]
        bounds = plan["model_bounds"][name]
        span = [bounds["max"][axis] - bounds["min"][axis] for axis in (0, 1)]
        origin = [plot_left + (plan["plot_width"] - span[0] / denominator) / 2,
                  plan["plot_bottom"] + (plan["plot_height"] - span[1] / denominator) / 2]

        def project(point):
            return [origin[axis] + (point[axis] - bounds["min"][axis]) / denominator for axis in (0, 1)]

        text((left + plan["cell_width"] / 2, height - 40),
             "RADIAL - U/V" if name == "TOP" else "AXIAL ELEVATION - U/H", 3.0, "view_label", name)
        equipment_origin = project((0, 0))
        ox, oy = equipment_origin
        stroke((ox - 1.4, oy), (ox + 1.4, oy), "dimension", name, annotation_kind="equipment_origin")
        stroke((ox, oy - 1.4), (ox, oy + 1.4), "dimension", name, annotation_kind="equipment_origin")
        text((ox - 2.6, oy - 2.6), "O", 2.2, view_name=name, annotation_kind="equipment_origin_label")
        key_x, key_y = left + 14, (plan["plot_bottom"] + plan["plot_top"]) / 2
        if name == "TOP":
            ring = [[key_x + 5 * math.cos(step * math.tau / 48), key_y + 5 * math.sin(step * math.tau / 48)] for step in range(49)]
            for a, b in zip(ring, ring[1:]):
                stroke(a, b, "dimension", name, annotation_kind="angle_reference_ring")
            for offset, label in (((10.5, 0), "0 +U"), ((0, 9), "90 +V"), ((-10, 0), "180"), ((0, -9), "270")):
                text((key_x + offset[0], key_y + offset[1]), label, 2.0, view_name=name, annotation_kind="angle_reference_label")
            angle = math.pi / 3
            tip = [key_x + 5 * math.cos(angle), key_y + 5 * math.sin(angle)]
            tangent = [-math.sin(angle), math.cos(angle)]
            arrow([tip[0] - 1.6 * tangent[0], tip[1] - 1.6 * tangent[1]], tip, name,
                  annotation_kind="positive_angle_direction")
            text((key_x, key_y - 14), "ANGLE KEY", 2.0, view_name=name)
        else:
            arrow([key_x, key_y], [key_x + 6, key_y], name, annotation_kind="positive_u_axis")
            arrow([key_x, key_y], [key_x, key_y + 6], name, annotation_kind="positive_h_axis")
            text((key_x + 10, key_y), "+U", 2.0, view_name=name)
            text((key_x, key_y + 9), "+H", 2.0, view_name=name)
            text((key_x, key_y - 14), "U/H KEY", 2.0, view_name=name)
        for row in rows:
            coordinates = row["equipment_coordinates_mm"]
            anchor = project((coordinates["u"], coordinates[vertical]))
            metadata = {"source_index": row["source_index"], "id": row["id"]}
            if name == "TOP" and math.dist(equipment_origin, anchor) > 1e-4:
                stroke(equipment_origin, anchor, "dimension", name, annotation_kind="position_radius", **metadata)
            stroke((anchor[0] - .9, anchor[1]), (anchor[0] + .9, anchor[1]), view_name=name,
                   annotation_kind="point_position_symbol", **metadata)
            stroke((anchor[0], anchor[1] - .9), (anchor[0], anchor[1] + .9), view_name=name,
                   annotation_kind="point_position_symbol", **metadata)
            direction = row["direction_basis"]
            arrow(anchor, [anchor[0] + _DIRECTION_LENGTH_MM * direction["u"],
                           anchor[1] + _DIRECTION_LENGTH_MM * direction[vertical]], name,
                  annotation_kind="point_direction_symbol", **metadata)
        view_callouts = []
        ordered = sorted(rows, key=lambda row: (-row["equipment_coordinates_mm"][vertical], row["equipment_coordinates_mm"]["u"], row["source_index"]))
        label_x = left + plan["cell_width"] - 10
        for lane, row in enumerate(ordered):
            label_y = ((plan["plot_bottom"] + plan["plot_top"]) / 2 if len(rows) == 1 else
                       plan["plot_top"] - 2 - lane * (plan["plot_height"] - 4) / (len(rows) - 1))
            coordinates = row["equipment_coordinates_mm"]
            anchor = project((coordinates["u"], coordinates[vertical]))
            elbow, end = [plot_right + 2, label_y], [label_x - 6.5, label_y]
            stroke(anchor, elbow, view_name=name, annotation_kind="point_leader", source_index=row["source_index"], id=row["id"])
            stroke(elbow, end, view_name=name, annotation_kind="point_leader", source_index=row["source_index"], id=row["id"])
            text((label_x, label_y), row["id"], 2.5, view_name=name, source_index=row["source_index"])
            callout = {"id": row["id"], "source_index": row["source_index"], "point_number": row["point_number"],
                       "view_name": name, "semantic_view": semantic, "anchor_mm": anchor,
                       "position_mm": [label_x, label_y], "leader_end_mm": end}
            view_callouts.append(callout)
            callouts.append(deepcopy(callout))
        views.append({"name": name, "semantic_name": semantic, "horizontal_axis": "u", "vertical_axis": vertical,
                      "source_indices": [row["source_index"] for row in rows], "scale_denominator": denominator,
                      "paper_mm_per_model_mm": 1 / denominator, "model_bounds": deepcopy(bounds),
                      "paper_origin_mm": list(origin), "equipment_origin_paper_mm": equipment_origin,
                      "legend_center_paper_mm": [key_x, key_y],
                      "symbol_padding_mm": _SYMBOL_PADDING_MM, "direction_symbol_length_mm": _DIRECTION_LENGTH_MM,
                      "direction_symbols_to_model_scale": False,
                      "transform": {"scale_denominator": denominator, "paper_mm_per_model_mm": 1 / denominator,
                                    "model_origin_mm": list(bounds["min"]), "paper_origin_mm": list(origin)},
                      "plot_bounds_mm": {"min": [plot_left, plan["plot_bottom"]], "max": [plot_right, plan["plot_top"]]},
                      "callouts": view_callouts})

    table_height = 3.0 if options["size"] == "A3" else 2.6
    text((width / 2, plan["table_top"] - plan["table_header"] / 2), _table(_COLUMNS), table_height)
    for index, row in enumerate(rows):
        y = plan["table_top"] - plan["table_header"] - (index + .5) * plan["row_height"]
        text((width / 2, y), row["table_row_text"], table_height,
             source_index=row["source_index"], annotation_kind="point_table_row")
    stroke((8, plan["table_top"]), (width - 8, plan["table_top"]), "title_block")
    for index in range(plan["table_rows"] + 1):
        y = plan["table_top"] - plan["table_header"] - index * plan["row_height"]
        stroke((8, y), (width - 8, y), "title_block")
    stroke((8, plan["table_bottom"]), (8, plan["table_top"]), "title_block")
    stroke((width - 8, plan["table_bottom"]), (width - 8, plan["table_top"]), "title_block")
    text((width / 2, 27), "ANG PPOS RADIAL POSITION / DIR PDIR AZIMUTH / TILT PDIR TOWARD +N / H ALONG +N", 2.15, "disclaimer")
    text((width / 2, 22), "NATIVE POS ORIGIN - PHYSICAL CENTER UNVERIFIED / REFERENCE DOES NOT VERIFY NOZZLE OWNERSHIP", 2.15, "disclaimer")
    text((width / 2, 17), "O NATIVE POS / SYMBOLS NOT TO SCALE / NA ZERO R OR AXIAL PDIR / FULL WORLD VALUES IN SOURCE ROWS", 2.15, "disclaimer")
    text((width / 2, 12), options["drawing_number"], 2.5, "drawing_number")
    scene = {
        "success": True, "available": True, "complete": True, "coordinate_space": "paper", "coordinate_unit": "mm",
        "model_coordinate_unit": "mm", "model_coordinate_frame": "native equipment local axes", "native_draft_sheet": False,
        "native_world_frame_refno": frame["native_world_frame_refno"], "native_world_frame_complete": True,
        "paper": {"size": options["size"], "orientation": "landscape", "width_mm": width, "height_mm": height},
        "paper_size_mm": [width, height], "page_number": number, "page_count": count,
        "source_indices": [row["source_index"] for row in rows], "source_rows": deepcopy(rows),
        "equipment_frame": deepcopy(frame), "angle_convention": deepcopy(_CONVENTION),
        "scale_denominator": denominator, "scale_mode": "explicit" if "scale_denominator" in options else "shared_auto_fit", "options": deepcopy(options),
        "shared_layout": {"common_scale": True, "common_model_bounds": True, "common_transforms": True,
                          "table_rows": plan["table_rows"], "model_bounds": deepcopy(plan["model_bounds"])},
        "direction_symbol_length_mm": _DIRECTION_LENGTH_MM, "direction_symbols_to_model_scale": False,
        "angle_reference_ring_radius_mm": 5.0, "angle_reference_ring_to_model_scale": False,
        "symbol_padding_mm": _SYMBOL_PADDING_MM, "position_symbol_half_length_mm": .9,
        "views": views, "callouts": callouts, "strokes": strokes, "texts": texts,
        "reference_display_abbreviated": any(row["reference_display_abbreviated"] for row in rows),
        "equipment_reference_display": display, "equipment_reference_display_abbreviated": abbreviated,
        "table": {"columns": list(_COLUMNS), "coordinate_decimals": 3, "angle_decimals": 2,
                  "row_capacity": plan["table_rows"], "coordinate_frame": "native equipment local axes",
                  "position_angle_column": "ANG DEG", "pdir_azimuth_column": "DIR DEG",
                  "signed_axial_column": "AXIAL MM", "world_positions_retained_in": "source_rows.position",
                  "world_directions_retained_in": "source_rows.direction",
                  "bounds_mm": {"min": [8.0, plan["table_bottom"]], "max": [width - 8, plan["table_top"]]}},
        "counts": {"points": len(rows), "views": 2, "strokes": len(strokes), "texts": len(texts)},
        "scope": "PPOS radial position angles and independent PDIR azimuth/tilt in explicitly selected native equipment coordinate axes. Native POS is a coordinate origin; physical center, centerline, nozzle ownership, equipment shape and connection validity are unverified.",
    }
    if outline_projection is not None:
        from .nozzle_outline_sheet import _annotate_outline_scene
        _annotate_outline_scene(scene, outline_projection, outline_placement)
    validate_drawing_sheet_scene(scene)
    return scene


def build_nozzle_axis_sheets(drawing: Any, equipment_pose: Any, options: Any, *,
                             axial_axis: str, zero_axis: str,
                             outline_projection=None) -> list[dict[str, Any]]:
    """Build A3/A4 radial U/V and axial U/H sheets without native or UI calls.

    ``equipment_pose`` requires complete/available/success flags, WORLD/mm,
    requested_refno/resolved_refno/element_name, position[3], orientation with
    x_axis/y_axis/z_axis[3], and a verified native WORLD frame. ``axial_axis``
    and ``zero_axis`` explicitly select different positive local X/Y/Z axes.
    The positive radial basis is u=zero_axis, n=axial_axis, v=n cross u.
    ``outline_projection`` optionally supplies the separately validated selected
    material projection; omitting it preserves the original point-only output.
    """
    if not isinstance(options, Mapping) or "datum" in options:
        raise ValueError("invalid_nozzle_axis_sheet_options")
    adapted = dict(options)
    has_explicit_scale = outline_projection is not None and "scale_denominator" in adapted
    explicit_scale = adapted.pop("scale_denominator") if has_explicit_scale else None
    normalized = normalize_nozzle_sheet_options({"title": "NOZZLE AXIS ORIENTATION", "drawing_number": "E3D-NOZZLE-AXIS", **adapted})
    if has_explicit_scale:
        denominator = _number(explicit_scale, "scale_denominator")
        if denominator <= 0:
            raise ValueError("invalid_nozzle_axis_scale_denominator")
        normalized["scale_denominator"] = denominator
    frame = _equipment_frame(equipment_pose, axial_axis, zero_axis)
    rows = _prepare_axis_rows(drawing, frame)
    if outline_projection is not None:
        from .nozzle_outline_sheet import _validate_outline_projection
        _validate_outline_projection(outline_projection, frame)
        if outline_projection.get("nozzle_profiles"):
            from .nozzle_outline_sheet import _build_profile_sheets
            return _build_profile_sheets(rows, normalized, frame, outline_projection)
    plan = _plan(rows, normalized, outline_projection)
    size = normalized["points_per_page"]
    count = (len(rows) + size - 1) // size
    scenes = [_page(rows[start:start + size], normalized, frame, plan, start // size + 1, count, outline_projection)
              for start in range(0, len(rows), size)]
    try:
        json.dumps(scenes, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        raise ValueError("nozzle_axis_evidence_not_serializable") from None
    return scenes
