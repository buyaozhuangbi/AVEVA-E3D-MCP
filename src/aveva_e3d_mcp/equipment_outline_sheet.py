"""One TOP equipment-location sheet from selected material boundaries.

The caller reads and verifies equipment poses, material sources and their
native context. This module only checks supplied receipts and places the one
already globally classified TOP projection. Explicit source assignments do
not, by themselves, prove native ownership or whole-equipment completeness.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .drawing_sheet_render import validate_drawing_sheet_scene
from .equipment_layout_sheet import (
    _COLUMNS, _PAPERS, _fixed, _number, _prepare_rows, _reference_key, _scale,
    _table, _text_box, normalize_equipment_layout_datum,
    normalize_equipment_layout_sheet_options,
)
from .material_assembly_sheet import _PROOF_FIELDS, _material_sources
from .material_assembly_workflow import _request_reference, normalize_material_targets
from .material_drawing_tiles import _digest, _view_and_lines


MAX_EQUIPMENT_OUTLINE_EQUIPMENT = 16
_OPTIONS = {"size", "title", "drawing_number", "scale_denominator"}
_FOOTER = "SELECTED PARAMETER GEOMETRY / NOT WHOLE EQUIPMENT"
_NATIVE_SCOPE = "PARAMETER ASSEMBLY - NATIVE CUT SCOPE UNVERIFIED"
_PRECISION = 15


def normalize_equipment_outline_targets(equipment: Any) -> list[dict[str, Any]]:
    """Validate 1..16 explicit equipment groups and at most 128 sources.

    Each group has exactly ``refno`` and ``sources``. Each source has exactly
    ``refno``, ``expected_type`` and ``geometry_mode`` under the existing
    material workflow contract. No source may be assigned to two groups.
    """
    if (not isinstance(equipment, (list, tuple))
            or not 1 <= len(equipment) <= MAX_EQUIPMENT_OUTLINE_EQUIPMENT):
        raise ValueError("equipment_outline_requires_one_to_16_equipment")
    normalized, seen, flattened = [], set(), []
    for index, item in enumerate(equipment):
        if not isinstance(item, Mapping) or set(item) != {"refno", "sources"}:
            raise ValueError(f"equipment_outline_group_{index}_requires_refno_and_sources")
        refno = _request_reference(item["refno"])
        identity = _reference_key(refno)
        if identity in seen:
            raise ValueError("equipment_outline_duplicate_equipment")
        seen.add(identity)
        sources = normalize_material_targets(item["sources"])
        normalized.append({"refno": refno, "sources": sources})
        flattened.extend(sources)
    normalize_material_targets(flattened)
    return normalized


def normalize_equipment_outline_sheet_options(options: Any) -> dict[str, Any]:
    """Return A3/A4 landscape options with optional explicit position scale."""
    if not isinstance(options, Mapping) or set(options) - _OPTIONS:
        raise ValueError("invalid_equipment_outline_sheet_options")
    adapted = {key: value for key, value in options.items() if key != "scale_denominator"}
    adapted.setdefault("title", "EQUIPMENT OUTLINE / TOP")
    adapted.setdefault("drawing_number", "E3D-EQUIP-OUTLINE")
    normalized = normalize_equipment_layout_sheet_options(adapted)
    normalized.pop("equipment_per_page")
    if "scale_denominator" in options:
        denominator = _number(options["scale_denominator"], "outline_scale_denominator")
        if denominator <= 0:
            raise ValueError("invalid_equipment_outline_scale_denominator")
        normalized["scale_denominator"] = denominator
    return normalized


def _assign_sources(groups: Any, rows, sources) -> tuple[list[list[int]], list[int]]:
    if not isinstance(groups, (list, tuple)) or len(groups) != len(rows):
        raise ValueError("equipment_outline_source_group_count_mismatch")
    owners, normalized = [-1] * len(sources), []
    for equipment_index, indices in enumerate(groups):
        if not isinstance(indices, (list, tuple)) or not indices:
            raise ValueError("equipment_outline_each_equipment_requires_sources")
        values = []
        for source_index in indices:
            if type(source_index) is not int or not 0 <= source_index < len(sources):
                raise ValueError("equipment_outline_source_index_invalid")
            if owners[source_index] != -1:
                raise ValueError("equipment_outline_source_assigned_more_than_once")
            owners[source_index] = equipment_index
            values.append(source_index)
        if not any(sources[index]["empty"] is False for index in values):
            raise ValueError("equipment_outline_equipment_has_no_nonempty_material")
        normalized.append(values)
    if -1 in owners:
        raise ValueError("equipment_outline_unassigned_material_source")
    return normalized, owners


def build_equipment_outline_sheet(
    material_drawing: Any, equipment_drawing: Any,
    equipment_source_indices: Any, options: Any, *, datum: Any = None,
) -> dict[str, Any]:
    """Place one complete, explicitly scoped TOP outline and its POS table.

    ``material_drawing`` is the existing material assembly drawing generated
    once with ``views=["TOP"]`` and ``projection_convention="right_handed"``.
    ``equipment_drawing`` is a complete WORLD/mm equipment layout receipt.
    ``equipment_source_indices`` is a sequence of nonempty source-index
    sequences, aligned with ``equipment_drawing.equipment`` input order. Every
    material source must be assigned exactly once. Every group must contain
    nonempty material, even if it is wholly occluded in the joint TOP view.

    Global visibility is retained without recomputation. A supplied scale
    must fit all material bounds and equipment origins; cropping is refused.
    No native reads, traversal, Boolean evaluation or mesh projection occurs.
    """
    normalized = normalize_equipment_outline_sheet_options(options)
    reference = normalize_equipment_layout_datum(datum)
    rows = _prepare_rows(equipment_drawing, reference)
    if len(rows) > MAX_EQUIPMENT_OUTLINE_EQUIPMENT:
        raise ValueError("equipment_outline_requires_one_to_16_equipment")
    if any(row.get("resolved_type", "EQUI") != "EQUI" for row in rows):
        raise ValueError("equipment_outline_equipment_type_must_be_EQUI")
    sources = _material_sources(material_drawing)
    source_view, geometry_bounds, lines = _view_and_lines(material_drawing, sources)
    if source_view["name"] != "TOP":
        raise ValueError("equipment_outline_TOP_view_required")
    if not lines:
        raise ValueError("equipment_outline_nonempty_TOP_linework_required")
    groups, owners = _assign_sources(equipment_source_indices, rows, sources)
    if any(abs(value) > 1_000_000_000 for values in geometry_bounds.values() for value in values):
        raise ValueError("equipment_outline_model_coordinate_limit_exceeded")

    width, height = _PAPERS[normalized["size"]]
    border, table_bottom, table_header = 8.0, 47.0, 6.0
    row_height = 5.0 if normalized["size"] == "A3" else 3.8
    table_top = table_bottom + table_header + len(rows) * row_height
    plot = {"min": [14.0, table_top + 12.0], "max": [width - 45.0, height - 46.0]}
    plot_size = [plot["max"][axis] - plot["min"][axis] for axis in (0, 1)]
    padding = 5.0
    if min(plot_size) <= 2 * padding:
        raise ValueError("equipment_outline_single_page_viewport_does_not_fit")
    if len(rows) > 1 and (plot_size[1] - 4.0) / (len(rows) - 1) <= 2.5:
        raise ValueError("equipment_outline_single_page_callouts_do_not_fit")
    bounds = {"min": [min(geometry_bounds["min"][axis], *[row["position"][axis] for row in rows]) for axis in (0, 1)],
              "max": [max(geometry_bounds["max"][axis], *[row["position"][axis] for row in rows]) for axis in (0, 1)]}
    extents = [bounds["max"][axis] - bounds["min"][axis] for axis in (0, 1)]
    required = max(extents[axis] / (plot_size[axis] - 2 * padding) for axis in (0, 1))
    denominator = normalized.get("scale_denominator", _scale(required))
    if any(extents[axis] / denominator > plot_size[axis] - 2 * padding for axis in (0, 1)):
        raise ValueError("equipment_outline_explicit_scale_does_not_fit")
    scale_label = format(denominator, ".9f").rstrip("0").rstrip(".")
    if scale_label == "0" or len(scale_label) > 24:
        raise ValueError("equipment_outline_scale_label_below_precision_or_too_long")
    origin = [plot["min"][axis] + (plot_size[axis] - extents[axis] / denominator) / 2 for axis in (0, 1)]

    def project(position):
        return [origin[axis] + (position[axis] - bounds["min"][axis]) / denominator for axis in (0, 1)]

    strokes, texts, callouts = [], [], []

    def stroke(start, end, role="feature", *, view_name=None, visibility="visible", **metadata):
        encoded = lambda point: tuple(float(format(value, f".{_PRECISION}f")) for value in point)
        svg_start, svg_end = [start[0], height - start[1]], [end[0], height - end[1]]
        if encoded(start) == encoded(end) or encoded(svg_start) == encoded(svg_end):
            raise ValueError("equipment_outline_geometry_below_precision")
        strokes.append({"start": list(start), "end": list(end), "role": role, "view_name": view_name,
                        "visibility": visibility, **metadata})

    def text(position, content, text_height=2.25, role="sheet_metadata", *, view_name=None, **metadata):
        texts.append({"position": list(position), "text": content, "height": text_height,
                      "rotation": 0, "role": role, "view_name": view_name, **metadata})

    corners = [(border, border), (width - border, border), (width - border, height - border), (border, height - border)]
    for index in range(4):
        stroke(corners[index], corners[(index + 1) % 4], "border")
    stroke((border, height - 33), (width - border, height - 33), "title_block")
    text((width / 2, height - 14), normalized["title"], 3.8, "title")
    text((width / 2, height - 21), f"WORLD MM / SCALE 1:{scale_label} / {len(rows)} EQUIPMENT / PAGE 1/1", 2.2)
    datum_coordinates = " / ".join(f"{axis} {_fixed(value)}" for axis, value in zip("XYZ", reference["origin_mm"]))
    text((width / 2, height - 28), f"DATUM {reference['label']} / {datum_coordinates} MM", 1.9)
    text((width / 2, height - 39), "TOP - WORLD +X RIGHT / +Y UP / VIEW FROM +Z", 2.3, "view_label", view_name="TOP")

    geometry_start = len(strokes)
    for index, line in enumerate(lines):
        equipment_index = owners[line["source_index"]]
        row = rows[equipment_index]
        retained = {key: deepcopy(value) for key, value in line.items()
                    if key not in {"start", "end", "kind", "visibility"}}
        stroke(project(line["start"]), project(line["end"]), line["kind"], view_name="TOP",
               visibility=line["visibility"], **retained, source_line_index=index,
               global_line_index=index, source_interval=[0.0, 1.0],
               equipment_index=equipment_index, equipment_id=row["id"],
               equipment_requested_refno=row["requested_refno"], equipment_refno=row["resolved_refno"],
               annotation_kind="equipment_material_outline")
    geometry_end = len(strokes)
    symbols = []
    for equipment_index, row in enumerate(rows):
        anchor = project(row["position"])
        common = {"equipment_index": equipment_index, "equipment_id": row["id"], "id": row["id"],
                  "equipment_requested_refno": row["requested_refno"], "equipment_refno": row["resolved_refno"],
                  "source_indices": list(groups[equipment_index])}
        marker = {**common, "annotation_kind": "equipment_origin_symbol"}
        stroke((anchor[0] - .8, anchor[1]), (anchor[0] + .8, anchor[1]), view_name="TOP", **marker)
        stroke((anchor[0], anchor[1] - .8), (anchor[0], anchor[1] + .8), view_name="TOP", **marker)
        symbols.append({**common, "anchor_mm": anchor})
    label_x = width - 18.0
    ordered = sorted(enumerate(rows), key=lambda item: (-item[1]["position"][1], item[1]["position"][0], item[0]))
    for lane, (equipment_index, row) in enumerate(ordered):
        y = ((plot["min"][1] + plot["max"][1]) / 2 if len(rows) == 1 else
             plot["max"][1] - 2 - lane * (plot_size[1] - 4) / (len(rows) - 1))
        anchor, elbow, end = project(row["position"]), [plot["max"][0] + 6, y], [label_x - 6, y]
        common = {"equipment_index": equipment_index, "equipment_id": row["id"], "id": row["id"],
                  "equipment_requested_refno": row["requested_refno"], "equipment_refno": row["resolved_refno"],
                  "source_indices": list(groups[equipment_index]), "annotation_kind": "equipment_leader"}
        if anchor != elbow:
            stroke(anchor, elbow, view_name="TOP", **common, leader_part="diagonal")
        stroke(elbow, end, view_name="TOP", **common, leader_part="horizontal")
        text((label_x, y), row["id"], 2.5, view_name="TOP", equipment_index=equipment_index,
             equipment_id=row["id"], annotation_kind="equipment_callout")
        callouts.append({**common, "view_name": "TOP", "anchor_mm": anchor,
                         "position_mm": [label_x, y], "leader_end_mm": end})

    table_height = 2.8 if normalized["size"] == "A3" else 2.25
    stroke((border, table_top), (width - border, table_top), "title_block")
    for line in range(len(rows) + 1):
        y = table_top - table_header - line * row_height
        stroke((border, y), (width - border, y), "title_block")
    text((width / 2, table_top - table_header / 2), _table(_COLUMNS), table_height)
    for index, row in enumerate(rows):
        row["material_source_indices"] = list(groups[index])
        text((width / 2, table_top - table_header - (index + .5) * row_height), row["table_row_text"], table_height,
             equipment_index=index, equipment_id=row["id"], annotation_kind="equipment_coordinate_row")
    text((width / 2, 42), "POS CROSS: EQUIPMENT ORIGIN / DX/DY/DEL: WORLD X/Y/Z MINUS DATUM / MM", 1.8)
    text((width / 2, 35), _FOOTER, 2.0, "disclaimer")
    text((width / 2, 28), _NATIVE_SCOPE, 2.0, "disclaimer")
    text((width / 2, 21), "SOURCE ASSIGNMENTS IN EQUIPMENT_SOURCE_MAP / NOT NATIVE DRAFT", 1.8)
    text((width / 2, 13), normalized["drawing_number"], 2.2, "drawing_number")

    axes = {key: deepcopy(source_view[key]) for key in ("horizontal_axis", "vertical_axis", "toward_viewer")}
    source_proof = {key: deepcopy(material_drawing[key]) for key in _PROOF_FIELDS}
    global_domain = {"schema": "material-global-linework-v1", "geometry_sha256": material_drawing["geometry_sha256"],
                     "projection_convention": "right_handed", "view_name": "TOP", **axes,
                     "global_bounds": geometry_bounds, "lines": lines}
    equipment_map = {row["id"]: {"equipment_index": index, "requested_refno": row["requested_refno"],
                    "resolved_refno": row["resolved_refno"], "element_name": deepcopy(row.get("element_name")),
                    "reference_display": row["reference_display"],
                    "reference_display_abbreviated": row["reference_display_abbreviated"],
                    "source_indices": list(groups[index]),
                    "sources": [{key: deepcopy(sources[source][key]) for key in
                                 ("source_index", "refno", "resolved_type", "geometry_mode", "empty", "source_mesh_sha256")}
                                for source in groups[index]]} for index, row in enumerate(rows)}
    scene = {**source_proof, "success": True, "available": True, "complete": True, "empty": False,
             "drawing_kind": "selected parameter equipment outline sheet", "view_name": "TOP",
             "coordinate_space": "paper", "coordinate_unit": "mm", "model_coordinate_unit": "mm",
             "coordinate_frame": "paper millimetres; origin bottom-left; +X right; +Y up",
             "source_coordinate_frame": "WORLD", "projection_convention": "right_handed",
             "paper": {"size": normalized["size"], "orientation": "landscape", "width_mm": width, "height_mm": height},
             "paper_size_mm": [width, height], "options": deepcopy(normalized),
             "datum": deepcopy(reference), "datum_coordinate_frame": "WORLD", "datum_coordinate_unit": "mm",
             "page_number": 1, "page_count": 1, "equipment_count": len(rows), "total_equipment_count": len(rows),
             "source_count": len(sources), "source_rows": deepcopy(rows), "equipment_source_indices": deepcopy(groups),
             "equipment_source_map": equipment_map, "reference_map": deepcopy(equipment_map),
             "source_equipment_map": [{"source_index": index, "source_refno": source["refno"],
                                       "equipment_index": owners[index], "equipment_id": rows[owners[index]]["id"],
                                       "equipment_refno": rows[owners[index]]["resolved_refno"]}
                                      for index, source in enumerate(sources)],
             "equipment_indices": list(range(len(rows))), "source_indices": list(range(len(sources))),
             "global_model_bounds": deepcopy(bounds), "geometry_model_bounds": deepcopy(geometry_bounds),
             "scale_denominator": denominator, "scale_mode": "explicit" if "scale_denominator" in normalized else "global_auto_fit",
             "views": [{"name": "TOP", "model_bounds": deepcopy(bounds), "geometry_model_bounds": deepcopy(geometry_bounds),
                        "plot_bounds_mm": deepcopy(plot), "cell_bounds_mm": {"min": [border, table_top + 7], "max": [width - border, height - 34]},
                        "scale_denominator": denominator, "transform": {"model_min": deepcopy(bounds["min"]),
                            "paper_origin_mm": origin, "scale_denominator": denominator},
                        "source_indices": list(range(len(sources))), "equipment_indices": list(range(len(rows))),
                        "geometry_stroke_range": [geometry_start, geometry_end], "symbols": symbols, **axes}],
             "callouts": callouts, "strokes": strokes, "texts": texts,
             "counts": {"equipment": len(rows), "sources": len(sources), "views": 1,
                        "source_lines": len(lines), "geometry_strokes": geometry_end - geometry_start,
                        "strokes": len(strokes), "texts": len(texts)},
             "source_attribution": {"representation": "structured scene geometry strokes",
                 "source_and_equipment_indices_preserved": True, "vector_source_metadata_embedded": False},
             "source_proof_sha256": _digest(source_proof), "global_linework_sha256": _digest(global_domain),
             "global_linework_hash_schema": "material-global-linework-v1",
             "coverage": {"complete": True, "input_line_count": len(lines), "output_line_count": geometry_end - geometry_start,
                          "source_interval": [0.0, 1.0], "source_line_indices": list(range(len(lines))),
                          "cropping_applied": False, "visibility_recomputed": False},
             "hidden_line_removal": material_drawing.get("hidden_line_removal"),
             "show_hidden_lines": material_drawing.get("show_hidden_lines"),
             "table_coordinate_decimal_places": 3, "table_coordinate_resolution_mm": .001,
             "table_precision_scope": "Rounded labels only; source_rows retain original WORLD positions and orientation.",
             "relative_coordinate_convention": "DX/DY/DEL: equipment WORLD X/Y/Z minus declared WORLD datum; mm.",
             "serialization_precision": {"coordinate_decimal_places": _PRECISION, "engineering_accuracy_claim": False},
             "footer": _FOOTER, "scope": _FOOTER, "material_scope": _NATIVE_SCOPE,
             "complete_scope": "All explicitly supplied material source linework and equipment positions.",
             "whole_equipment_geometry_verified": False, "explicit_source_membership_verified": False,
             "equipment_extents_verified": False, "plant_north_verified": False,
             "extents_included": True, "extents_scope": "Selected parameter geometry only; not whole-equipment extents.",
             "native_draft_sheet": False, "engineering_dimensions_certified": False}
    validate_drawing_sheet_scene(scene)
    boxes = [_text_box(item) for item in texts]
    for index, first in enumerate(boxes):
        for second in boxes[index + 1:]:
            if min(first[2], second[2]) > max(first[0], second[0]) and min(first[3], second[3]) > max(first[1], second[1]):
                raise ValueError("equipment_outline_text_collision")
    return scene
