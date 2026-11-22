"""Fixed-scale printable windows of one globally projected material drawing.

Clipping and global visibility belong to ``material_drawing_tiles``. This
module places one planned page on paper, retaining source attribution only in
the structured scene. Window coordinate notes are not model dimensions.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from fractions import Fraction
import math
import re
from typing import Any

from .drawing_sheet import _fitted_height, build_drawing_sheet, normalize_sheet_options
from .material_assembly_sheet import _material_sources
from .projection_sheet import _AXES, _text_box


_FOOTER = "PARAMETER ASSEMBLY - NATIVE CUT SCOPE UNVERIFIED"
_PRECISION = 15
_SUMMARY_FIELDS = (
    "geometry_sha256", "geometry_hash_scope", "native_tessellation", "native_cut_application_verified",
    "boolean_union_applied", "vertices_welded", "source_intersections_checked",
    "global_negative_cut_scope_verified", "native_csg_evaluated", "cuts_applied",
    "source_parameter_boolean_evaluated", "material_boundary_validated", "closed_manifold", "winding_consistent",
)


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("material_tile_sheet_invalid_number")
    try:
        value = float(value)
    except (OverflowError, ValueError):
        raise ValueError("material_tile_sheet_invalid_number") from None
    if not math.isfinite(value):
        raise ValueError("material_tile_sheet_invalid_number")
    return value


def _point(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("material_tile_sheet_invalid_point")
    point = [_number(component) for component in value]
    if any(abs(component) > 1e9 for component in point):
        raise ValueError("material_tile_sheet_coordinate_limit_exceeded")
    return point


def _bounds(value: Any) -> tuple[list[float], list[float]]:
    if not isinstance(value, Mapping):
        raise ValueError("material_tile_sheet_invalid_bounds")
    low, high = _point(value.get("min")), _point(value.get("max"))
    if any(a >= b for a, b in zip(low, high)):
        raise ValueError("material_tile_sheet_invalid_bounds")
    return low, high


def _hash(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("material_tile_sheet_manifest_hash_required")
    return value


def _fmt(value: float) -> str:
    text = format(value, ".15f").rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _page_identity(manifest: Mapping[str, Any], page: Any, name: str) -> dict[str, Any]:
    if not isinstance(page, Mapping):
        raise ValueError("material_tile_sheet_page_required")
    rows, columns, count = (manifest.get(key) for key in ("rows", "columns", "page_count"))
    if (any(type(value) is not int or value < 1 for value in (rows, columns, count))
            or rows * columns != count or count > 64):
        raise ValueError("material_tile_sheet_invalid_grid")
    number, row, column = (page.get(key) for key in ("page_number", "row_index", "column_index"))
    if (any(type(value) is not int for value in (number, row, column)) or not 0 <= row < rows
            or not 0 <= column < columns or number != row * columns + column + 1):
        raise ValueError("material_tile_sheet_invalid_page_identity")
    identifier = lambda r, c: f"{name}-R{r + 1:02d}-C{c + 1:02d}"
    if page.get("id") != identifier(row, column):
        raise ValueError("material_tile_sheet_invalid_page_identity")
    neighbors = {"top": identifier(row - 1, column) if row else None,
                 "bottom": identifier(row + 1, column) if row + 1 < rows else None,
                 "left": identifier(row, column - 1) if column else None,
                 "right": identifier(row, column + 1) if column + 1 < columns else None}
    if page.get("neighbors") != neighbors:
        raise ValueError("material_tile_sheet_invalid_neighbors")
    pages = manifest.get("pages")
    if not isinstance(pages, list) or len(pages) != count or page != pages[number - 1]:
        raise ValueError("material_tile_sheet_page_not_in_manifest")
    return {"id": page["id"], "page_number": number, "page_count": count,
            "row_index": row, "column_index": column, "rows": rows, "columns": columns,
            "neighbors": neighbors}


def _line_metadata(line: Any, originals: list[Any] | tuple[Any, ...], sources: list[Mapping[str, Any]],
                   low: list[float], high: list[float], page_index: int) -> dict[str, Any]:
    if not isinstance(line, Mapping) or type(line.get("global_line_index")) is not int:
        raise ValueError("material_tile_sheet_line_mapping_invalid")
    index = line["global_line_index"]
    if not 0 <= index < len(originals) or not isinstance(originals[index], Mapping):
        raise ValueError("material_tile_sheet_line_mapping_invalid")
    original = originals[index]
    source_index = line.get("source_index")
    if (type(source_index) is not int or not 0 <= source_index < len(sources)
            or line.get("kind") not in ("silhouette", "feature")):
        raise ValueError("material_tile_sheet_line_source_invalid")
    source = sources[source_index]
    edge = line.get("edge")
    if (source["empty"] or line.get("source_refno") != source["refno"]
            or line.get("source_geometry_mode") != source["geometry_mode"]
            or not isinstance(edge, (list, tuple)) or len(edge) != 2
            or any(type(vertex) is not int or not source["vertex_range"][0] <= vertex < source["vertex_range"][1]
                   for vertex in edge)
            or any(line.get(key) != original.get(key) for key in
                   ("kind", "source_index", "source_refno", "source_geometry_mode", "edge"))
            or line.get("visibility", "visible") != original.get("visibility", "visible")):
        raise ValueError("material_tile_sheet_line_source_invalid")
    interval = line.get("source_interval")
    if not isinstance(interval, (list, tuple)) or len(interval) != 2:
        raise ValueError("material_tile_sheet_source_interval_invalid")
    interval = [_number(value) for value in interval]
    if not 0 <= interval[0] < interval[1] <= 1:
        raise ValueError("material_tile_sheet_source_interval_invalid")
    a, b = _point(original.get("start")), _point(original.get("end"))
    for endpoint, parameter in zip((line.get("start"), line.get("end")), interval):
        point = _point(endpoint)
        if any(not low[axis] <= point[axis] <= high[axis] for axis in (0, 1)):
            raise ValueError("material_tile_sheet_line_outside_window")
        for axis in (0, 1):
            expected = float(Fraction(a[axis]) + (Fraction(b[axis]) - Fraction(a[axis])) * Fraction(parameter))
            # The interval is serialized as a float after exact clipping. Its
            # half-ULP uncertainty and endpoint rounding are the only allowance.
            uncertainty = abs(b[axis] - a[axis]) * math.ulp(parameter) / 2
            uncertainty += math.ulp(expected) + math.ulp(point[axis])
            if abs(point[axis] - expected) > uncertainty:
                raise ValueError("material_tile_sheet_clip_endpoint_mismatch")
    result = {"page_line_index": page_index, "global_line_index": index,
              "source_interval": interval, "source_index": source_index,
              "source_refno": source["refno"], "source_geometry_mode": source["geometry_mode"],
              "edge": list(edge), "global_projection_start": _point(line["start"]),
              "global_projection_end": _point(line["end"]),
              "tile_clip": {"start_clipped": interval[0] != 0, "end_clipped": interval[1] != 1,
                            "creates_model_edges": False,
                            "scope": "Clipped intervals of the original projected source edge; paper window borders are not model edges."}}
    if "parameter_interval" in original:
        result["parameter_interval"] = deepcopy(original["parameter_interval"])
    return result


def build_material_tile_sheet(drawing: Any, manifest: Any, page: Any, sheet_options: Any) -> dict[str, Any]:
    """Place a planned single-view tile at its fixed declared paper scale.

    Geometry uses the absolute global projection U/V coordinates and the page
    window's minimum as its datum. Generic overall dimensions are removed;
    visible MIN/MAX notes describe the projection window only. Full material
    proofs remain shared in the manifest instead of being repeated per page.
    """
    sources = _material_sources(drawing)
    if (not isinstance(manifest, Mapping) or any(manifest.get(flag) is not True for flag in ("success", "available", "complete"))
            or manifest.get("empty") is not False or manifest.get("projection_convention") != "right_handed"
            or drawing.get("projection_convention") != "right_handed"
            or manifest.get("scope") != _FOOTER or manifest.get("footer") != _FOOTER):
        raise ValueError("material_tile_sheet_verified_manifest_required")
    name = manifest.get("view_name")
    if not isinstance(name, str) or name not in _AXES:
        raise ValueError("material_tile_sheet_view_required")
    views = drawing.get("views")
    if not isinstance(views, (list, tuple)):
        raise ValueError("material_tile_sheet_view_required")
    selected = [view for view in views if isinstance(view, Mapping) and view.get("name") == name]
    if len(selected) != 1:
        raise ValueError("material_tile_sheet_view_required")
    view = selected[0]
    for field, expected in zip(("horizontal_axis", "vertical_axis", "toward_viewer"), _AXES[name]):
        if (not isinstance(view.get(field), (list, tuple)) or tuple(view[field]) != expected
                or not isinstance(manifest.get(field), (list, tuple)) or tuple(manifest[field]) != expected
                or any(isinstance(value, bool) for value in (*view[field], *manifest[field]))):
            raise ValueError("material_tile_sheet_projection_axes_mismatch")
    originals = view.get("lines")
    proof = manifest.get("source_proof")
    if (not isinstance(originals, (list, tuple)) or not isinstance(proof, Mapping)
            or any(proof.get(key) != drawing.get(key) for key in (*_SUMMARY_FIELDS, "source_ranges", "scope", "footer"))):
        raise ValueError("material_tile_sheet_shared_source_proof_mismatch")
    manifest_hash = _hash(manifest.get("manifest_sha256"))
    linework_hash = _hash(manifest.get("global_linework_sha256"))
    proof_hash = _hash(manifest.get("source_proof_sha256"))
    coverage = manifest.get("coverage")
    if (not isinstance(coverage, Mapping) or any(coverage.get(flag) is not True for flag in
            ("complete", "exact_rational_grid_verified", "emitted_float_grid_verified",
             "exact_source_interval_union_verified", "returned_source_interval_union_verified"))
            or coverage.get("gap_tolerance") != 0):
        raise ValueError("material_tile_sheet_verified_coverage_required")
    identity = _page_identity(manifest, page, name)
    low, high = _bounds(page.get("bounds"))
    paper, viewport = manifest.get("paper"), manifest.get("viewport_mm")
    if not isinstance(paper, Mapping) or not isinstance(viewport, Mapping) or not isinstance(sheet_options, Mapping):
        raise ValueError("material_tile_sheet_paper_options_required")
    if "projection_method" in sheet_options:
        raise ValueError("material_tile_sheet_projection_method_unsupported")
    denominator = _number(manifest.get("scale_denominator"))
    if denominator <= 0:
        raise ValueError("material_tile_sheet_invalid_scale")
    options = normalize_sheet_options({"paper_size": paper.get("size"), "orientation": paper.get("orientation"),
                                       "scale_denominator": denominator, **dict(sheet_options)})
    if (options["paper_size"] != paper.get("size") or options["orientation"] != paper.get("orientation")
            or options["scale_denominator"] != denominator):
        raise ValueError("material_tile_sheet_paper_or_scale_mismatch")
    width, height = _number(paper.get("width_mm")), _number(paper.get("height_mm"))
    viewport_size = [_number(viewport.get("width")), _number(viewport.get("height"))]
    if viewport_size != [width - 50, height - 94] or any(value <= 0 for value in viewport_size):
        raise ValueError("material_tile_sheet_viewport_mismatch")
    raw_spans = manifest.get("tile_span_model_mm")
    if not isinstance(raw_spans, (list, tuple)) or len(raw_spans) != 2:
        raise ValueError("material_tile_sheet_window_span_mismatch")
    spans = [_number(value) for value in raw_spans]
    for axis in (0, 1):
        nominal = viewport_size[axis] * denominator
        uncertainty = 4 * max(math.ulp(low[axis]), math.ulp(high[axis]), math.ulp(nominal))
        if spans[axis] != nominal or abs((high[axis] - low[axis]) - nominal) > uncertainty:
            raise ValueError("material_tile_sheet_window_span_mismatch")
    lines = page.get("lines")
    if (not isinstance(lines, (list, tuple)) or type(page.get("segment_count")) is not int
            or page["segment_count"] != len(lines)):
        raise ValueError("material_tile_sheet_line_count_mismatch")
    metadata = [_line_metadata(line, originals, sources, low, high, index) for index, line in enumerate(lines)]
    suffix = " " + identity["id"]
    options["title"] = options["title"][:120 - len(suffix)].rstrip() + suffix
    number = options["drawing_number"]
    options["drawing_number"] = (number[:39 - len(identity["id"])] + "-" if number else "") + identity["id"]
    tile_view = {"name": name, "bounds": {"min": low, "max": high},
                 "dimensions": {"width": high[0] - low[0], "height": high[1] - low[1], "unit": "mm"},
                 "lines": lines}
    tile_drawing = {"success": True, "complete": True, "coordinate_unit": "mm", "show_hidden_lines": drawing.get("show_hidden_lines") is True,
                    "coordinate_frame": f"Global {name} projection U/V; millimetres; not WORLD XYZ", "views": [tile_view]}
    scene = build_drawing_sheet(tile_drawing, options, coordinate_decimal_places=_PRECISION)
    if scene["paper"] != dict(paper):
        raise ValueError("material_tile_sheet_paper_dimensions_mismatch")
    scene["strokes"] = [stroke for stroke in scene["strokes"] if stroke["role"] != "dimension"]
    scene["texts"] = [text for text in scene["texts"] if text["role"] != "dimension"]
    origin, far = [25.0, 67.0], [width - 25.0, height - 27.0]
    encoded = lambda point: tuple(float(format(value, ".15f")) for value in point)
    geometry_indices = []
    for index, stroke in enumerate(scene["strokes"]):
        if stroke["role"] not in ("silhouette", "feature"):
            continue
        line_index = stroke["source_line_index"]
        line = lines[line_index]
        for endpoint in ("start", "end"):
            point = [origin[axis] + (_number(line[endpoint][axis]) - low[axis]) / denominator for axis in (0, 1)]
            # A boundary subtraction can overshoot its fixed paper window by
            # an ULP. Confine that endpoint, then require a nonzero result.
            point = [max(origin[axis], min(far[axis], point[axis])) for axis in (0, 1)]
            stroke[endpoint] = point
        a, b = stroke["start"], stroke["end"]
        if encoded(a) == encoded(b) or encoded((a[0], height - a[1])) == encoded((b[0], height - b[1])):
            raise ValueError("material_tile_sheet_geometry_below_precision")
        stroke.update(metadata[line_index])
        geometry_indices.append(index)
    if len(geometry_indices) != len(lines):
        raise ValueError("material_tile_sheet_line_mapping_incomplete")
    corners = (origin, [far[0], origin[1]], far, [origin[0], far[1]])
    for index, start in enumerate(corners):
        scene["strokes"].append({"start": list(start), "end": list(corners[(index + 1) % 4]), "role": "title_block",
                                 "visibility": "visible", "view_name": None, "paper_guide": "projection tile window",
                                 "creates_model_edges": False})

    def note(position, text, role="sheet_metadata", preferred=3.0):
        item = {"position": list(position), "text": text, "height": _fitted_height(text, width - 24, preferred),
                "rotation": 0.0, "role": role, "view_name": name}
        box = _text_box(item)
        if box[0] < 10 or box[1] < 10 or box[2] > width - 10 or box[3] > height - 10:
            raise ValueError("material_tile_sheet_note_does_not_fit")
        scene["texts"].append(item)

    disclaimer = next(text for text in scene["texts"] if text["role"] == "disclaimer")
    disclaimer["text"] = _FOOTER + " / NOT NATIVE DRAFT"
    disclaimer["height"] = _fitted_height(disclaimer["text"], width - 24, 3.0)
    heading = next(text for text in scene["texts"] if text["role"] == "view_label")
    heading["text"] = (f'{name} / PAGE {identity["page_number"]}/{identity["page_count"]}'
                       f' / ROW {identity["row_index"] + 1} COL {identity["column_index"] + 1}')
    heading["height"] = _fitted_height(heading["text"], width - 24, 3.0)
    neighbors = identity["neighbors"]
    neighbor_labels = {"top": "TOP", "bottom": "BOT", "left": "LEFT", "right": "RIGHT"}
    note((width / 2, height - 21), "NEIGHBORS " + " ".join(f"{neighbor_labels[side]} {neighbors[side] or 'NONE'}" for side in ("top", "bottom", "left", "right")))
    note((width / 2, 61), f"PROJECTION WINDOW U MIN {_fmt(low[0])} MAX {_fmt(high[0])} MM")
    note((width / 2, 55), f"PROJECTION WINDOW V MIN {_fmt(low[1])} MAX {_fmt(high[1])} MM")
    if not lines:
        note((width / 2, (origin[1] + far[1]) / 2), "NO GLOBAL LINEWORK IN THIS TILE", "disclaimer", 3.0)
    placed = scene["views"][0]
    placed.pop("model_dimensions_mm", None)
    placed.update(projection_window_size_mm=[high[axis] - low[axis] for axis in (0, 1)],
                  bounds_scope="Global projection U/V tile window; not overall model dimensions.",
                  paper_bounds_mm={"min": origin, "max": far},
                  transform={"model_min": low, "paper_origin_mm": origin, "scale_denominator": denominator,
                             "paper_mm_per_model_mm": 1 / denominator},
                  stroke_range=([geometry_indices[0], geometry_indices[-1] + 1] if geometry_indices else
                                [len(scene["strokes"]) - 4, len(scene["strokes"]) - 4]),
                  text_range=[scene["texts"].index(heading), len(scene["texts"])],
                  **{field: list(view[field]) for field in ("horizontal_axis", "vertical_axis", "toward_viewer")})
    scene.pop("dimension_label_resolution_mm", None)
    scene.update({key: deepcopy(drawing[key]) for key in _SUMMARY_FIELDS})
    scene.update(drawing_kind="parameter material assembly tiled sheet", empty=False, tile_empty=not lines,
                 scope=_FOOTER, footer=_FOOTER, projection_convention="right_handed", source_count=len(sources),
                 source_model_coordinate_frame=drawing["coordinate_frame"],
                 source_attribution={"representation": "structured scene geometry strokes", "page_line_index_scope": "this tile's planned lines",
                                     "global_line_index_scope": f"global {name} projection lines", "source_interval_scope": "relative to the original global projected line",
                                     "vector_source_metadata_embedded": False},
                 shared_source_proof={"manifest_sha256": manifest_hash, "source_proof_sha256": proof_hash,
                                      "location": "manifest.source_proof", "full_proof_repeated_on_page": False},
                 global_linework_sha256=linework_hash,
                 tile_page={**identity, "global_projection_bounds": {"min": low, "max": high},
                            "coordinate_scope": f"Global {name} projection U/V in mm; not WORLD XYZ",
                            "viewport_paper_bounds_mm": {"min": origin, "max": far},
                            "overlap_paper_mm": deepcopy(manifest.get("overlap_mm")), "creates_model_edges": False},
                 dimension_scope="No model dimensions drawn; MIN/MAX coordinate notes identify the projection tile window.",
                 scale_scope="Fixed common 1:N scale from global projection millimetres to paper millimetres.",
                 layout_policy="single global projection tile; row zero is highest Y and column zero is leftmost",
                 serialization_precision={"coordinate_decimal_places": _PRECISION, "coordinate_unit": "paper mm",
                                          "engineering_accuracy_claim": False},
                 counts={"views": 1, "strokes": len(scene["strokes"]), "texts": len(scene["texts"]), "geometry_strokes": len(lines)})
    return scene
