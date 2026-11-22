"""Fixed-scale TOP equipment pages from one global material projection.

Only the explicit source linework is clipped. Native reads, source membership
verification, mesh derivation and visibility classification belong to the
caller. Exact interval coverage and equipment-origin coverage are both checked
before any scene is returned. The existing material tiler remains unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from fractions import Fraction
from typing import Any

from .drawing_sheet_render import validate_drawing_sheet_scene
from .equipment_layout_sheet import (
    _COLUMNS, _PAPERS, _fixed, _prepare_rows, _reference_key, _table, _text_box,
    normalize_equipment_layout_datum,
)
from .equipment_outline_sheet import _assign_sources, normalize_equipment_outline_sheet_options
from .material_assembly_sheet import _PROOF_FIELDS, _material_sources
from .material_assembly_workflow import _request_reference, normalize_material_targets
from .material_drawing_tiles import (
    MAX_MATERIAL_TILE_CLIP_SEGMENTS, MAX_MATERIAL_TILE_LINE_TESTS,
    MAX_MATERIAL_TILE_PAGES, MAX_MATERIAL_TILE_SOURCE_LINES, _BOUNDARY_POLICY,
    _axis_count, _clip_interval, _digest, _float, _page_id, _union_covers,
    _view_and_lines, normalize_material_tiling_options,
)
from .material_tile_sheet import _line_metadata


MAX_EQUIPMENT_LAYOUT_TILE_EQUIPMENT = 64
MAX_EQUIPMENT_LAYOUT_TILE_ROWS = 16
_FOOTER = "SELECTED PARAMETER GEOMETRY / NOT WHOLE EQUIPMENT"
_NATIVE_SCOPE = "PARAMETER ASSEMBLY - NATIVE CUT SCOPE UNVERIFIED"
_PRECISION = 15


def normalize_equipment_layout_tile_targets(equipment: Any) -> list[dict[str, Any]]:
    """Validate 1..64 equipment groups and globally unique 1..128 sources."""
    if (not isinstance(equipment, (list, tuple))
            or not 1 <= len(equipment) <= MAX_EQUIPMENT_LAYOUT_TILE_EQUIPMENT):
        raise ValueError("equipment_layout_tiles_requires_one_to_64_equipment")
    output, seen, flattened = [], set(), []
    for index, group in enumerate(equipment):
        if not isinstance(group, Mapping) or set(group) != {"refno", "sources"}:
            raise ValueError(f"equipment_layout_tiles_group_{index}_requires_refno_and_sources")
        refno = _request_reference(group["refno"])
        key = _reference_key(refno)
        if key in seen:
            raise ValueError("equipment_layout_tiles_duplicate_equipment")
        seen.add(key)
        sources = normalize_material_targets(group["sources"])
        output.append({"refno": refno, "sources": sources})
        flattened.extend(sources)
    normalize_material_targets(flattened)
    return output


def normalize_equipment_layout_tile_sheet_options(sheet: Any) -> dict[str, Any]:
    """Validate A3/A4 landscape and require an explicit positive fixed scale."""
    if not isinstance(sheet, Mapping) or "scale_denominator" not in sheet:
        raise ValueError("equipment_layout_tiles_explicit_scale_required")
    adapted = dict(sheet)
    adapted.setdefault("title", "EQUIPMENT LAYOUT TILES")
    adapted.setdefault("drawing_number", "E3D-EQUIPMENT-TILES")
    normalized = normalize_equipment_outline_sheet_options(adapted)
    label = format(normalized["scale_denominator"], ".9f").rstrip("0").rstrip(".")
    if label == "0" or len(label) > 24:
        raise ValueError("equipment_layout_tiles_scale_label_below_precision_or_too_long")
    return normalized


def _paper_plan(sheet, equipment_count):
    if type(equipment_count) is not int or not 1 <= equipment_count <= MAX_EQUIPMENT_LAYOUT_TILE_EQUIPMENT:
        raise ValueError("equipment_layout_tiles_requires_one_to_64_equipment")
    width, height = _PAPERS[sheet["size"]]
    capacity = min(MAX_EQUIPMENT_LAYOUT_TILE_ROWS, equipment_count)
    row_height = 5.0 if sheet["size"] == "A3" else 3.8
    bottom, header = 43.0, 6.0
    top = bottom + header + capacity * row_height
    plot = {"min": [14.0, top + 10.0], "max": [width - 45.0, height - 42.0]}
    viewport = [plot["max"][axis] - plot["min"][axis] for axis in (0, 1)]
    if min(viewport) <= 0:
        raise ValueError("equipment_layout_tiles_viewport_does_not_fit")
    return {"paper": {"size": sheet["size"], "orientation": "landscape", "width_mm": width, "height_mm": height},
            "plot_bounds_mm": plot, "viewport_mm": {"width": viewport[0], "height": viewport[1]},
            "table_bottom": bottom, "table_top": top, "table_header": header, "row_height": row_height,
            "table_capacity": capacity}


def normalize_equipment_layout_tile_tiling_options(sheet: Any, tiling: Any,
                                                  equipment_count: Any) -> dict[str, Any]:
    """Preflight overlap using the shared viewport for the total equipment count.

    This pure options check needs no material or pose input. Every page reserves
    ``min(16, equipment_count)`` table rows, so all pages use the same viewport.
    The returned options contain only normalized overlap and the page limit.
    """
    options = normalize_equipment_layout_tile_sheet_options(sheet)
    paper = _paper_plan(options, equipment_count)
    normalized = normalize_material_tiling_options(tiling)
    if normalized["overlap_mm"] >= min(paper["viewport_mm"].values()) / 2:
        raise ValueError("equipment_layout_tiles_overlap_exceeds_viewport_limit")
    return normalized


def _grid_axis_origin(required_min, required_max, span, step, count, lines, rows, axis, scale):
    """Keep fitting source outlines together, then balance unavoidable margins.

    Only the already classified projected line intervals are inspected. The
    candidates are exact changes in source/pose containment, plus the centered
    placement and coverage endpoints. This does not derive equipment extents
    or alter geometry, scale, spans, overlap, page count or line visibility.
    """
    grid_span = (count - 1) * step + span
    first, last = required_max - grid_span, required_min
    centered = (first + last) / 2
    source_intervals = {}
    for line in lines:
        values = [Fraction(line[key][axis]) for key in ("start", "end")]
        index = line["source_index"]
        if index in source_intervals:
            old = source_intervals[index]
            old[0], old[1], old[2] = min(old[0], *values), max(old[1], *values), old[2] + 1
        else:
            source_intervals[index] = [min(values), max(values), 1]
    protected = []
    candidates = {first, last, centered}

    def admit_events(low, high):
        for offset in range(count):
            for candidate in (low - offset * step, high - span - offset * step):
                if first <= candidate <= last:
                    candidates.add(candidate)

    for low, high, weight in source_intervals.values():
        if high - low > span:
            continue
        margin = min(Fraction(2) * scale, (span - (high - low)) / 2)
        protected.append((low - margin, high + margin, weight))
        admit_events(low - margin, high + margin)
    origin_margin = min(Fraction(5) * scale, span / 4)
    positions = [Fraction(row["position"][axis]) for row in rows]
    for position in positions:
        admit_events(position - origin_margin, position + origin_margin)

    def score(origin):
        windows = [(origin + offset * step, origin + offset * step + span) for offset in range(count)]
        split = [weight for low, high, weight in protected
                 if not any(start <= low and high <= end for start, end in windows)]
        crowded_origins = sum(not any(start + origin_margin <= position <= end - origin_margin
                                      for start, end in windows) for position in positions)
        return sum(split), len(split), crowded_origins, abs(origin - centered), -origin

    chosen = min(candidates, key=score)
    chosen_score = score(chosen)
    return chosen, {"axis": "XY"[axis], "candidate_count": len(candidates),
                    "protected_projected_source_count": len(protected),
                    "split_projected_source_count": chosen_score[1],
                    "split_projected_source_line_weight": chosen_score[0],
                    "origin_clearance_miss_count": chosen_score[2],
                    "centered_origin": _float(centered, "equipment_layout_tiles_unrepresentable_bounds"),
                    "chosen_origin": _float(chosen, "equipment_layout_tiles_unrepresentable_bounds")}


def _plan_tiles(drawing, source_view, sources, lines, rows, groups, owners, sheet, tiling, datum, paper):
    geometry_bounds = deepcopy(source_view["bounds"])
    planning_bounds = {"min": [min(geometry_bounds["min"][axis], *[row["position"][axis] for row in rows]) for axis in (0, 1)],
                       "max": [max(geometry_bounds["max"][axis], *[row["position"][axis] for row in rows]) for axis in (0, 1)]}
    if any(abs(value) > 1e9 for values in planning_bounds.values() for value in values):
        raise ValueError("equipment_layout_tiles_model_coordinate_limit_exceeded")
    viewport = list(paper["viewport_mm"].values())
    if tiling["overlap_mm"] >= min(viewport) / 2:
        raise ValueError("equipment_layout_tiles_overlap_exceeds_viewport_limit")
    scale = Fraction(sheet["scale_denominator"])
    span_values = [_float(Fraction(value) * scale, "equipment_layout_tiles_invalid_span") for value in viewport]
    overlap_value = _float(Fraction(tiling["overlap_mm"]) * scale, "equipment_layout_tiles_invalid_span")
    spans = [Fraction(value) for value in span_values]
    overlap = Fraction(overlap_value)
    steps = [span - overlap for span in spans]
    if any(span <= 0 or step <= 0 for span, step in zip(spans, steps)):
        raise ValueError("equipment_layout_tiles_invalid_span")
    required_min = [Fraction(value) for value in planning_bounds["min"]]
    required_max = [Fraction(value) for value in planning_bounds["max"]]
    columns, grid_rows = [_axis_count(high - low, span, step)
                          for low, high, span, step in zip(required_min, required_max, spans, steps)]
    count = columns * grid_rows
    if count > tiling["max_pages"] or count > MAX_MATERIAL_TILE_PAGES:
        raise ValueError("equipment_layout_tiles_page_limit_exceeded")
    work = count * len(lines)
    if work > MAX_MATERIAL_TILE_LINE_TESTS:
        raise ValueError("equipment_layout_tiles_line_work_limit_exceeded")
    grid_spans = [(number - 1) * steps[axis] + spans[axis] for axis, number in enumerate((columns, grid_rows))]
    decisions = [_grid_axis_origin(required_min[axis], required_max[axis], spans[axis], steps[axis],
                                  number, lines, rows, axis, scale)
                 for axis, number in enumerate((columns, grid_rows))]
    origin = [decision[0] for decision in decisions]
    grid_max = [origin[axis] + grid_spans[axis] for axis in (0, 1)]
    if not all(low <= required_low and high >= required_high and step <= span
               for low, high, required_low, required_high, step, span in
               zip(origin, grid_max, required_min, required_max, steps, spans)):
        raise ValueError("equipment_layout_tiles_mathematical_grid_coverage_gap")
    pages, rectangles = [], []
    for row_index in range(grid_rows):
        for column_index in range(columns):
            offsets = column_index, grid_rows - row_index - 1
            low = [origin[axis] + offsets[axis] * steps[axis] for axis in (0, 1)]
            high = [low[axis] + spans[axis] for axis in (0, 1)]
            bounds = {"min": [_float(value, "equipment_layout_tiles_unrepresentable_bounds") for value in low],
                      "max": [_float(value, "equipment_layout_tiles_unrepresentable_bounds") for value in high]}
            if any(bounds["min"][axis] >= bounds["max"][axis] for axis in (0, 1)):
                raise ValueError("equipment_layout_tiles_unrepresentable_bounds")
            rectangles.append(tuple([Fraction(value) for value in bounds[key]] for key in ("min", "max")))
            neighbors = {direction: (_page_id("TOP", r, c) if 0 <= r < grid_rows and 0 <= c < columns else None)
                         for direction, r, c in (("top", row_index - 1, column_index), ("bottom", row_index + 1, column_index),
                                                 ("left", row_index, column_index - 1), ("right", row_index, column_index + 1))}
            pages.append({"page_number": len(pages) + 1, "id": _page_id("TOP", row_index, column_index),
                          "row_index": row_index, "column_index": column_index, "bounds": bounds,
                          "neighbors": neighbors, "lines": [], "segment_count": 0})
    if not all(_union_covers([(low[axis], high[axis]) for low, high in rectangles], required_min[axis], required_max[axis]) for axis in (0, 1)):
        raise ValueError("equipment_layout_tiles_emitted_grid_coverage_gap")
    rational_lines = []
    for line in lines:
        start = [Fraction(value) for value in line["start"]]
        rational_lines.append((start, [Fraction(value) - first for value, first in zip(line["end"], start)]))
    exact_intervals, returned_intervals = [[] for _ in lines], [[] for _ in lines]
    origin_pages = [[] for _ in rows]
    fragment_count = point_touches = 0
    for page, (minimum, maximum) in zip(pages, rectangles):
        geometry_equipment = set()
        for line_index, (line, (start, delta)) in enumerate(zip(lines, rational_lines)):
            clipped = _clip_interval(start, delta, minimum, maximum)
            if clipped is None:
                continue
            low, high = clipped
            if low == high:
                point_touches += 1
                continue
            endpoints = [[_float(start[axis] + delta[axis] * parameter, "equipment_layout_tiles_clip_below_float_precision")
                          for axis in (0, 1)] for parameter in (low, high)]
            interval = [float(low), float(high)]
            if endpoints[0] == endpoints[1] or interval[0] >= interval[1]:
                raise ValueError("equipment_layout_tiles_clip_below_float_precision")
            if any(not page["bounds"]["min"][axis] <= point[axis] <= page["bounds"]["max"][axis] for point in endpoints for axis in (0, 1)):
                raise ValueError("equipment_layout_tiles_clipped_endpoint_outside_rectangle")
            fragment_count += 1
            if fragment_count > MAX_MATERIAL_TILE_CLIP_SEGMENTS:
                raise ValueError("equipment_layout_tiles_clip_segment_limit_exceeded")
            equipment_index = owners[line["source_index"]]
            page["lines"].append({**deepcopy(line), "start": endpoints[0], "end": endpoints[1],
                                  "global_line_index": line_index, "source_interval": interval,
                                  "equipment_index": equipment_index, "equipment_id": rows[equipment_index]["id"],
                                  "equipment_refno": rows[equipment_index]["resolved_refno"]})
            geometry_equipment.add(equipment_index)
            exact_intervals[line_index].append((low, high))
            returned_intervals[line_index].append(tuple(Fraction(value) for value in interval))
        contained = []
        for index, row in enumerate(rows):
            if all(minimum[axis] <= Fraction(row["position"][axis]) <= maximum[axis] for axis in (0, 1)):
                contained.append(index)
                origin_pages[index].append(page["id"])
        equipment_indices = sorted(geometry_equipment | set(contained))
        if len(equipment_indices) > paper["table_capacity"]:
            raise ValueError("equipment_layout_tiles_page_equipment_table_limit_exceeded")
        page.update(segment_count=len(page["lines"]), origin_equipment_indices=contained,
                    geometry_equipment_indices=sorted(geometry_equipment), equipment_indices=equipment_indices,
                    equipment_ids=[rows[index]["id"] for index in equipment_indices], equipment_count=len(equipment_indices),
                    blank_geometry=not page["lines"], blank=not page["lines"] and not contained)
    for index, (exact, returned) in enumerate(zip(exact_intervals, returned_intervals)):
        if not _union_covers(exact) or not _union_covers(returned):
            raise ValueError(f"equipment_layout_tiles_source_coverage_gap:line={index}")
    if any(not page_ids for page_ids in origin_pages):
        raise ValueError("equipment_layout_tiles_equipment_origin_coverage_gap")
    axes = {key: list(source_view[key]) for key in ("horizontal_axis", "vertical_axis", "toward_viewer")}
    proof_keys = (*_PROOF_FIELDS, "scope", "footer", "coordinate_space", "coordinate_frame", "coordinate_unit", "counts")
    proof = {key: deepcopy(drawing[key]) for key in proof_keys}
    line_domain = {"schema": "material-global-linework-v1", "geometry_sha256": drawing["geometry_sha256"],
                   "projection_convention": "right_handed", "view_name": "TOP", **axes,
                   "global_bounds": geometry_bounds, "lines": lines}
    equipment_map = {row["id"]: {"equipment_index": index, "requested_refno": row["requested_refno"],
                     "resolved_refno": row["resolved_refno"], "element_name": deepcopy(row.get("element_name")),
                     "source_indices": deepcopy(groups[index]), "origin_world_mm": list(row["position"]),
                     "page_ids": [page["id"] for page in pages if index in page["equipment_indices"]],
                     "origin_page_ids": origin_pages[index],
                     "geometry_page_ids": [page["id"] for page in pages if index in page["geometry_equipment_indices"]]}
                     for index, row in enumerate(rows)}
    manifest = {"success": True, "available": True, "complete": True, "empty": False, "schema_version": 1,
                "manifest_kind": "selected parameter fixed-scale TOP equipment layout tiles",
                "coordinate_space": "WORLD", "coordinate_unit": "mm", "view_name": "TOP", "projection_convention": "right_handed", **axes,
                "sheet_options": deepcopy(sheet), "tiling_options": deepcopy(tiling), "paper": deepcopy(paper["paper"]),
                "viewport_mm": deepcopy(paper["viewport_mm"]), "plot_bounds_mm": deepcopy(paper["plot_bounds_mm"]),
                "scale_denominator": sheet["scale_denominator"], "overlap_mm": tiling["overlap_mm"], "overlap_model_mm": overlap_value,
                "geometry_bounds": geometry_bounds, "planning_bounds": planning_bounds, "global_bounds": deepcopy(planning_bounds),
                "grid_origin": [_float(value, "equipment_layout_tiles_unrepresentable_bounds") for value in origin],
                "grid_bounds": {"min": [_float(value, "equipment_layout_tiles_unrepresentable_bounds") for value in origin],
                    "max": [_float(value, "equipment_layout_tiles_unrepresentable_bounds") for value in grid_max]},
                "grid_placement": {"mode": "source_preserving_balanced_padding", "model_geometry_translated": False,
                    "scale_preserved": True, "outer_padding_model_mm": {
                        key: _float(value, "equipment_layout_tiles_unrepresentable_bounds") for key, value in
                        (("left", required_min[0] - origin[0]), ("right", grid_max[0] - required_max[0]),
                         ("bottom", required_min[1] - origin[1]), ("top", grid_max[1] - required_max[1]))},
                    "source_clearance_paper_mm": 2.0, "origin_clearance_paper_mm": 5.0,
                    "source_scope": "Already classified projected source line intervals; not native equipment envelopes.",
                    "axis_decisions": [decision[1] for decision in decisions]},
                "rows": grid_rows, "columns": columns, "page_count": count,
                "tile_span_model_mm": span_values, "tile_step_model_mm": [_float(value, "equipment_layout_tiles_invalid_span") for value in steps],
                "equipment_count": len(rows), "source_count": len(sources), "datum": deepcopy(datum),
                "equipment_source_indices": deepcopy(groups), "equipment_source_map": equipment_map,
                "source_equipment_map": [{"source_index": index, "source_refno": source["refno"], "equipment_index": owners[index],
                    "equipment_id": rows[owners[index]]["id"], "equipment_refno": rows[owners[index]]["resolved_refno"]} for index, source in enumerate(sources)],
                "source_proof": proof, "source_proof_sha256": _digest(proof), "geometry_sha256": drawing["geometry_sha256"],
                "global_linework_sha256": _digest(line_domain), "global_linework_hash_schema": "material-global-linework-v1",
                "position_proof_sha256": _digest({"rows": rows, "source_indices": groups, "datum": datum}),
                "boundary_policy": _BOUNDARY_POLICY,
                "coverage": {"complete": True, "exact_rational_grid_verified": True, "emitted_float_grid_verified": True,
                    "exact_source_interval_union_verified": True, "returned_source_interval_union_verified": True, "gap_tolerance": 0.0,
                    "input_line_count": len(lines), "covered_line_count": len(lines), "clip_segment_count": fragment_count,
                    "point_touch_count": point_touches, "line_tile_tests": work, "visibility_recomputed": False,
                    "parameter_domain": [0.0, 1.0], "equipment_origin_coverage_verified": True,
                    "equipment_origins": [{"equipment_index": index, "equipment_id": row["id"], "page_ids": origin_pages[index]}
                                          for index, row in enumerate(rows)]},
                "table_capacity": paper["table_capacity"], "table_location_legend": {"P": "Equipment POS lies in this closed tile.", "G": "Geometry fragment in this tile; equipment POS is outside."},
                "scope": _FOOTER, "footer": _FOOTER, "material_scope": _NATIVE_SCOPE,
                "source_membership_proof_scope": "Native descendant membership evidence is provided by the caller in the containing result; this manifest records geometry planning only.",
                "whole_equipment_geometry_verified": False,
                "engineering_accuracy_claim": False,
                "limits": {"equipment": MAX_EQUIPMENT_LAYOUT_TILE_EQUIPMENT, "sources": 128, "table_rows_per_page": 16,
                    "source_lines": MAX_MATERIAL_TILE_SOURCE_LINES, "clip_segments": MAX_MATERIAL_TILE_CLIP_SEGMENTS,
                    "line_tile_tests": MAX_MATERIAL_TILE_LINE_TESTS, "pages": MAX_MATERIAL_TILE_PAGES}, "pages": pages}
    manifest["manifest_sha256"] = _digest({"schema": "equipment-layout-tile-manifest-v1",
                                          "manifest": {key: value for key, value in manifest.items() if key != "source_proof"}})
    return manifest


def _sheet(drawing, originals, sources, rows, groups, manifest, page, options, datum, paper):
    width, height = paper["paper"]["width_mm"], paper["paper"]["height_mm"]
    denominator = manifest["scale_denominator"]
    origin = paper["plot_bounds_mm"]["min"]
    far = paper["plot_bounds_mm"]["max"]
    low, high = page["bounds"]["min"], page["bounds"]["max"]
    strokes, texts, callouts = [], [], []

    def project(position):
        point = [_float(Fraction(origin[axis]) + (Fraction(position[axis]) - Fraction(low[axis])) / Fraction(denominator),
                        "equipment_layout_tiles_paper_coordinate_unrepresentable") for axis in (0, 1)]
        return [max(origin[axis], min(far[axis], point[axis])) for axis in (0, 1)]

    def stroke(start, end, role="feature", *, view_name=None, visibility="visible", **metadata):
        encode = lambda point: tuple(float(format(value, f".{_PRECISION}f")) for value in point)
        if encode(start) == encode(end) or encode((start[0], height - start[1])) == encode((end[0], height - end[1])):
            raise ValueError("equipment_layout_tiles_geometry_below_precision")
        strokes.append({"start": list(start), "end": list(end), "role": role, "view_name": view_name,
                        "visibility": visibility, **metadata})

    def text(position, content, text_height=2.25, role="sheet_metadata", **metadata):
        texts.append({"position": list(position), "text": content, "height": text_height, "rotation": 0,
                      "role": role, "view_name": None, **metadata})

    corners = [(8, 8), (width - 8, 8), (width - 8, height - 8), (8, height - 8)]
    for index in range(4):
        stroke(corners[index], corners[(index + 1) % 4], "border")
    viewport_corners = [origin, [far[0], origin[1]], far, [origin[0], far[1]]]
    for index in range(4):
        stroke(viewport_corners[index], viewport_corners[(index + 1) % 4], "border")
    stroke((8, height - 32), (width - 8, height - 32), "title_block")
    text((width / 2, height - 13), options["title"], 3.5, "title")
    scale_label = format(denominator, ".9f").rstrip("0").rstrip(".")
    text((width / 2, height - 20), f"{page['id']} / PAGE {page['page_number']}/{manifest['page_count']} / WORLD MM / SCALE 1:{scale_label}", 2.1)
    coords = " / ".join(f"{axis} {_fixed(value)}" for axis, value in zip("XYZ", datum["origin_mm"]))
    text((width / 2, height - 27), f"DATUM {datum['label']} / {coords} MM", 1.8)
    window = f"TOP +X RIGHT +Y UP / X {_fixed(low[0])} TO {_fixed(high[0])} / Y {_fixed(low[1])} TO {_fixed(high[1])}"
    text((width / 2, height - 37), window, 1.7, "view_label")
    geometry_start = len(strokes)
    for page_line_index, line in enumerate(page["lines"]):
        metadata = _line_metadata(line, originals, sources, low, high, page_line_index)
        metadata.update(source_line_index=line["global_line_index"],
                        equipment_index=line["equipment_index"], equipment_id=line["equipment_id"],
                        equipment_refno=line["equipment_refno"], annotation_kind="equipment_material_outline")
        stroke(project(line["start"]), project(line["end"]), line["kind"], view_name="TOP", visibility=line["visibility"], **metadata)
    geometry_end = len(strokes)
    origin_indices = page["origin_equipment_indices"]
    ordered = sorted(origin_indices, key=lambda index: (-rows[index]["position"][1], rows[index]["position"][0], index))
    label_x = width - 18.0
    if len(ordered) > 1 and (far[1] - origin[1] - 4) / (len(ordered) - 1) < 2.5:
        raise ValueError("equipment_layout_tiles_callout_lanes_do_not_fit")
    for lane, index in enumerate(ordered):
        row = rows[index]
        anchor = project(row["position"])
        metadata = {"equipment_index": index, "equipment_id": row["id"], "equipment_refno": row["resolved_refno"],
                    "source_indices": deepcopy(groups[index]), "origin_in_tile": True}
        stroke((anchor[0] - .8, anchor[1]), (anchor[0] + .8, anchor[1]), view_name="TOP", **metadata, annotation_kind="equipment_origin_symbol")
        stroke((anchor[0], anchor[1] - .8), (anchor[0], anchor[1] + .8), view_name="TOP", **metadata, annotation_kind="equipment_origin_symbol")
        y = (origin[1] + far[1]) / 2 if len(ordered) == 1 else far[1] - 2 - lane * (far[1] - origin[1] - 4) / (len(ordered) - 1)
        elbow, end = [far[0] + 4, y], [label_x - 6, y]
        if anchor != elbow:
            stroke(anchor, elbow, view_name="TOP", **metadata, annotation_kind="equipment_leader", leader_part="diagonal")
        stroke(elbow, end, view_name="TOP", **metadata, annotation_kind="equipment_leader", leader_part="horizontal")
        text((label_x, y), row["id"], 2.25, **metadata, annotation_kind="equipment_callout")
        callouts.append({**metadata, "anchor_mm": anchor, "position_mm": [label_x, y], "leader_end_mm": end})
    if page["blank"]:
        text(((origin[0] + far[0]) / 2, (origin[1] + far[1]) / 2), "NO PROJECTED EDGES OR EQUIPMENT ORIGINS", 2.25)
    # This is a paper calibration bar, not a dimension attached to a model
    # feature. Its exact 50 paper mm length makes the declared fixed scale
    # independently checkable in the PNG, SVG and DXF at their paper size.
    bar_length = 50.0
    bar_model_length = _float(Fraction(bar_length) * Fraction(denominator),
                              "equipment_layout_tiles_scale_bar_unrepresentable")
    bar_y = origin[1] - 4.0
    bar_start, bar_end = [origin[0], bar_y], [origin[0] + bar_length, bar_y]
    bar_metadata = {"annotation_kind": "paper_scale_bar", "paper_length_mm": bar_length,
                    "model_length_mm": bar_model_length, "scale_denominator": denominator,
                    "model_feature_dimension": False}
    stroke(bar_start, bar_end, **bar_metadata, bar_part="baseline")
    for x in (bar_start[0], bar_end[0]):
        stroke([x, bar_y - 1], [x, bar_y + 1], **bar_metadata, bar_part="end_tick")
    text((bar_start[0], bar_y - 3), "0", 1.8, **bar_metadata)
    text((bar_end[0], bar_y - 3), format(bar_model_length, ".9g").upper() + " MM", 1.8, **bar_metadata)
    text((width / 2, bar_y - 1), "MODEL MM / PRINT PAPER TRUE SIZE", 1.8, **bar_metadata)
    top, header, row_height = paper["table_top"], paper["table_header"], paper["row_height"]
    stroke((8, top), (width - 8, top), "title_block")
    for line in range(paper["table_capacity"] + 1):
        y = top - header - line * row_height
        stroke((8, y), (width - 8, y), "title_block")
    table_height = 2.8 if options["size"] == "A3" else 2.25
    text((width / 2, top - header / 2), _table(_COLUMNS) + " LOC", table_height)
    page_rows = []
    for lane, index in enumerate(page["equipment_indices"]):
        row = deepcopy(rows[index])
        contained, geometry_here = index in origin_indices, index in page["geometry_equipment_indices"]
        marker = "P" if contained else "G"
        row.update(equipment_index=index, origin_in_tile=contained, geometry_in_tile=geometry_here,
                   page_relation="origin_and_geometry" if contained and geometry_here else "origin_only" if contained else "geometry_only",
                   location_marker=marker, material_source_indices=deepcopy(groups[index]),
                   table_row_text=row["table_row_text"] + " " + marker.rjust(3))
        row["table_values"]["location"] = marker
        page_rows.append(row)
        text((width / 2, top - header - (lane + .5) * row_height), row["table_row_text"], table_height,
             equipment_index=index, equipment_id=row["id"], origin_in_tile=contained, geometry_in_tile=geometry_here,
             annotation_kind="equipment_coordinate_row")
    text((width / 2, 39), "LOC P: POS IN TILE / G: GEOMETRY HERE - POS OFF PAGE / DX/DY/DEL FROM DATUM", 1.8)
    text((width / 2, 32), _FOOTER, 2.0, "disclaimer")
    text((width / 2, 25), _NATIVE_SCOPE, 2.0, "disclaimer")
    neighbor_note = " / ".join(f"{direction} {page['neighbors'][key] or 'NONE'}" for direction, key in (("N", "top"), ("S", "bottom"), ("W", "left"), ("E", "right")))
    text((width / 2, 18), neighbor_note, 1.7)
    text((width / 2, 11), options["drawing_number"], 2.2, "drawing_number")
    axes = {key: deepcopy(manifest[key]) for key in ("horizontal_axis", "vertical_axis", "toward_viewer")}
    scene = {"success": True, "available": True, "complete": True, "empty": False,
             "drawing_kind": "selected parameter fixed-scale TOP equipment layout tile", "view_name": "TOP",
             "coordinate_space": "paper", "coordinate_unit": "mm", "model_coordinate_unit": "mm", "source_coordinate_frame": "WORLD",
             "paper": deepcopy(paper["paper"]), "paper_size_mm": [width, height], "options": deepcopy(options),
             "page_number": page["page_number"], "page_count": manifest["page_count"], "id": page["id"], "neighbors": deepcopy(page["neighbors"]),
             "row_index": page["row_index"], "column_index": page["column_index"], "projection_convention": "right_handed",
             "model_bounds": deepcopy(page["bounds"]), "geometry_bounds": deepcopy(manifest["geometry_bounds"]),
             "planning_bounds": deepcopy(manifest["planning_bounds"]), "scale_denominator": denominator, "scale_mode": "fixed_global",
             "datum": deepcopy(datum), "datum_coordinate_frame": "WORLD", "datum_coordinate_unit": "mm",
             "equipment_count": len(page_rows), "total_equipment_count": len(rows), "source_count": len(sources),
             "equipment_indices": deepcopy(page["equipment_indices"]), "equipment_ids": deepcopy(page["equipment_ids"]),
             "origin_equipment_indices": deepcopy(origin_indices), "geometry_equipment_indices": deepcopy(page["geometry_equipment_indices"]),
             "source_indices": sorted({line["source_index"] for line in page["lines"]}), "source_rows": page_rows,
             "reference_map": {row["id"]: {key: deepcopy(row.get(key)) for key in ("equipment_index", "requested_refno", "resolved_refno", "element_name", "reference_display", "reference_display_abbreviated")} for row in page_rows},
             "views": [{"name": "TOP", "model_bounds": deepcopy(page["bounds"]), "plot_bounds_mm": deepcopy(paper["plot_bounds_mm"]),
                 "transform": {"model_min": list(low), "paper_origin_mm": list(origin), "scale_denominator": denominator},
                 "geometry_stroke_range": [geometry_start, geometry_end], **axes}],
             "callouts": callouts, "strokes": strokes, "texts": texts, "blank_geometry": page["blank_geometry"], "blank": page["blank"],
             "counts": {"equipment": len(page_rows), "origin_equipment": len(origin_indices), "source_lines": page["segment_count"],
                 "geometry_strokes": geometry_end - geometry_start, "strokes": len(strokes), "texts": len(texts)},
             "geometry_sha256": manifest["geometry_sha256"], "source_proof_sha256": manifest["source_proof_sha256"],
             "global_linework_sha256": manifest["global_linework_sha256"], "manifest_sha256": manifest["manifest_sha256"],
             "grid_placement": deepcopy(manifest["grid_placement"]),
             "paper_scale_bar": {**bar_metadata, "start_mm": bar_start, "end_mm": bar_end},
             "table_capacity": paper["table_capacity"], "table_location_legend": deepcopy(manifest["table_location_legend"]),
             "table_coordinate_decimal_places": 3, "table_coordinate_resolution_mm": .001,
             "serialization_precision": {"coordinate_decimal_places": _PRECISION, "engineering_accuracy_claim": False},
             "scope": _FOOTER, "footer": _FOOTER, "material_scope": _NATIVE_SCOPE,
             "explicit_source_membership_verified": False, "whole_equipment_geometry_verified": False,
             "extents_included": False, "equipment_extents_verified": False,
             "native_draft_sheet": False, "engineering_dimensions_certified": False, "plant_north_verified": False,
             "coverage": {"global_proof_in_manifest": True, "source_line_intervals_retained": True, "visibility_recomputed": False}}
    validate_drawing_sheet_scene(scene)
    boxes = [_text_box(item) for item in texts]
    for index, first in enumerate(boxes):
        for second in boxes[index + 1:]:
            if min(first[2], second[2]) > max(first[0], second[0]) and min(first[3], second[3]) > max(first[1], second[1]):
                raise ValueError("equipment_layout_tiles_text_collision")
    return scene


def build_equipment_layout_tiles(material_drawing: Any, equipment_drawing: Any, equipment_source_indices: Any,
                                 sheet: Any, tiling: Any = None, *, datum: Any = None) -> dict[str, Any]:
    """Return ``{manifest, sheets}`` only after all exact coverage/layout checks.

    Inputs are the existing complete material TOP/right-handed drawing, the
    complete WORLD/mm equipment pose drawing and one nonempty source-index
    group per equipment, in the same global input order. All sources must be
    assigned once; native descendant membership is not established here.
    IDs stay E001..E064 across pages. LOC=P draws a true in-window POS marker
    and leader. LOC=G identifies clipped geometry whose POS is off that page;
    no fictitious marker/leader is emitted for it. Blank grid pages are kept.
    Every page reserves min(16, total equipment count) table rows and uses the
    same viewport. Native membership evidence belongs to the caller's result.
    """
    options = normalize_equipment_layout_tile_sheet_options(sheet)
    reference = normalize_equipment_layout_datum(datum)
    rows = _prepare_rows(equipment_drawing, reference)
    if len(rows) > MAX_EQUIPMENT_LAYOUT_TILE_EQUIPMENT:
        raise ValueError("equipment_layout_tiles_requires_one_to_64_equipment")
    if any(row.get("resolved_type", "EQUI") != "EQUI" for row in rows):
        raise ValueError("equipment_layout_tiles_native_EQUI_required")
    tiling_options = normalize_equipment_layout_tile_tiling_options(options, tiling, len(rows))
    sources = _material_sources(material_drawing)
    source_view, _bounds, lines = _view_and_lines(material_drawing, sources)
    if len(lines) > MAX_MATERIAL_TILE_SOURCE_LINES:
        raise ValueError("equipment_layout_tiles_source_line_limit_exceeded")
    if source_view["name"] != "TOP":
        raise ValueError("equipment_layout_tiles_TOP_view_required")
    if not lines:
        raise ValueError("equipment_layout_tiles_nonempty_TOP_linework_required")
    groups, owners = _assign_sources(equipment_source_indices, rows, sources)
    paper = _paper_plan(options, len(rows))
    manifest = _plan_tiles(material_drawing, source_view, sources, lines, rows, groups, owners, options, tiling_options, reference, paper)
    scenes = [_sheet(material_drawing, lines, sources, rows, groups, manifest, page, options, reference, paper) for page in manifest["pages"]]
    return {"success": True, "available": True, "complete": True, "manifest": manifest, "sheets": scenes,
            "explicit_source_membership_verified": False, "whole_equipment_geometry_verified": False}
