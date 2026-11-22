"""Plan fixed-scale pages by clipping one globally classified material view.

No source or visibility reads occur here. Rational arithmetic over the admitted
binary floats distinguishes real coverage gaps from endpoint rounding. Closed
rectangles deliberately repeat shared-boundary and overlap-region linework.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import math
from typing import Any

from .drawing_sheet import _PAPERS, normalize_sheet_options
from .material_assembly_sheet import _material_sources, _PROOF_FIELDS
from .projection_sheet import _AXES


MAX_MATERIAL_TILE_SOURCE_LINES = 20_000
MAX_MATERIAL_TILE_CLIP_SEGMENTS = 40_000
MAX_MATERIAL_TILE_LINE_TESTS = 480_000
MAX_MATERIAL_TILE_PAGES = 24
_ZERO, _ONE = Fraction(0), Fraction(1)
_BOUNDARY_POLICY = (
    "Closed rectangles: boundary-coincident strokes and overlap regions are intentionally repeated "
    "across adjacent pages; point-only contacts produce no stroke."
)


def _number(value: Any, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(code)
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise ValueError(code) from None
    if not math.isfinite(result):
        raise ValueError(code)
    return 0.0 if result == 0 else result


def _point(value: Any, code: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(code)
    return [_number(component, code) for component in value]


def _float(value: Fraction, code: str) -> float:
    try:
        return _number(float(value), code)
    except OverflowError:
        raise ValueError(code) from None


def _digest(value: Any) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError):
        raise ValueError("material_tiles_metadata_not_serializable") from None
    return hashlib.sha256(encoded).hexdigest()


def normalize_material_tiling_options(tiling: Any) -> dict[str, Any]:
    """Normalize only paper-mm overlap and the bounded page-count limit.

    The overlap's viewport-dependent upper bound is checked by the planner.
    ``None`` selects 10 paper mm overlap and at most 24 pages.
    """
    if tiling is None:
        tiling = {}
    if not isinstance(tiling, Mapping) or set(tiling) - {"overlap_mm", "max_pages"}:
        raise ValueError("invalid_material_tiling_options")
    overlap = _number(tiling.get("overlap_mm", 10.0), "invalid_material_tiling_overlap_mm")
    if overlap < 0:
        raise ValueError("invalid_material_tiling_overlap_mm")
    max_pages = tiling.get("max_pages", MAX_MATERIAL_TILE_PAGES)
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_MATERIAL_TILE_PAGES:
        raise ValueError("invalid_material_tiling_max_pages")
    return {"overlap_mm": overlap, "max_pages": max_pages}


def _view_and_lines(drawing: Any, sources) -> tuple[Mapping[str, Any], dict[str, list[float]], list[dict[str, Any]]]:
    views = drawing.get("views")
    if (drawing.get("projection_convention") != "right_handed"
            or not isinstance(views, (list, tuple)) or len(views) != 1
            or not isinstance(views[0], Mapping) or not isinstance(views[0].get("name"), str)
            or views[0]["name"] not in _AXES):
        raise ValueError("material_tiles_one_right_handed_orthographic_view_required")
    view = views[0]
    if "linear_dimensions" in view:
        raise ValueError("material_tiles_linear_dimensions_unsupported")
    for field, expected in zip(("horizontal_axis", "vertical_axis", "toward_viewer"), _AXES[view["name"]]):
        values = view.get(field)
        if (not isinstance(values, (list, tuple)) or len(values) != 3
                or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values)
                or tuple(values) != expected):
            raise ValueError("material_tiles_physical_axes_mismatch")
    bounds = view.get("bounds")
    if not isinstance(bounds, Mapping):
        raise ValueError("material_tiles_invalid_global_bounds")
    minimum = _point(bounds.get("min"), "material_tiles_invalid_global_bounds")
    maximum = _point(bounds.get("max"), "material_tiles_invalid_global_bounds")
    extents = [maximum[axis] - minimum[axis] for axis in (0, 1)]
    if any(not math.isfinite(extent) or extent < 0 for extent in extents):
        raise ValueError("material_tiles_invalid_global_bounds")
    dimensions = view.get("dimensions")
    if not isinstance(dimensions, Mapping) or dimensions.get("unit") != "mm":
        raise ValueError("material_tiles_model_millimetres_required")
    tolerance = max(1e-9, *[extent * 1e-9 for extent in extents])
    if any(abs(_number(dimensions.get(key), "material_tiles_invalid_dimensions") - extent) > tolerance
           for key, extent in zip(("width", "height"), extents)):
        raise ValueError("material_tiles_dimensions_bounds_mismatch")
    raw_lines = view.get("lines")
    if not isinstance(raw_lines, (list, tuple)):
        raise ValueError("material_tiles_lines_required")
    if len(raw_lines) > MAX_MATERIAL_TILE_SOURCE_LINES:
        raise ValueError("material_tiles_source_line_limit_exceeded")
    if type(drawing.get("segment_count")) is not int or drawing["segment_count"] != len(raw_lines):
        raise ValueError("material_tiles_source_line_count_mismatch")
    lines = []
    for line in raw_lines:
        if (not isinstance(line, Mapping) or not isinstance(line.get("kind"), str)
                or line["kind"] not in {"silhouette", "feature"}
                or line.get("visibility", "visible") not in ("visible", "hidden")
                or type(line.get("source_index")) is not int
                or not 0 <= line["source_index"] < len(sources)):
            raise ValueError("material_tiles_line_source_invalid")
        source = sources[line["source_index"]]
        edge = line.get("edge")
        if (source["empty"] or line.get("source_refno") != source["refno"]
                or line.get("source_geometry_mode") != source["geometry_mode"]
                or not isinstance(edge, (list, tuple)) or len(edge) != 2
                or any(type(vertex) is not int or not source["vertex_range"][0] <= vertex < source["vertex_range"][1]
                       for vertex in edge)):
            raise ValueError("material_tiles_line_source_invalid")
        visibility = line.get("visibility", "visible")
        if visibility == "hidden" and drawing.get("show_hidden_lines") is not True:
            raise ValueError("material_tiles_hidden_lines_not_requested")
        start, end = [_point(line.get(key), "material_tiles_invalid_line") for key in ("start", "end")]
        if start == end:
            raise ValueError("material_tiles_degenerate_source_line")
        # No bounds epsilon or clamping can erase a real source interval.
        if any(not minimum[axis] <= point[axis] <= maximum[axis]
               for point in (start, end) for axis in (0, 1)):
            raise ValueError("material_tiles_source_line_outside_global_bounds")
        lines.append({**deepcopy(dict(line)), "start": start, "end": end,
                      "edge": list(edge), "visibility": visibility})
    return view, {"min": minimum, "max": maximum}, lines


def _axis_count(extent: Fraction, span: Fraction, step: Fraction) -> int:
    if extent <= span:
        return 1
    quotient = (extent - span) / step
    return 1 + (quotient.numerator + quotient.denominator - 1) // quotient.denominator


def _union_covers(intervals, minimum=_ZERO, maximum=_ONE) -> bool:
    """Require exact coverage; a positive gap of any size fails this proof."""
    cursor = minimum
    for low, high in sorted(intervals):
        if high < minimum or low > maximum:
            continue
        if low > cursor:
            return False
        cursor = max(cursor, high)
        if cursor >= maximum:
            return True
    return cursor >= maximum


def _clip_interval(start, delta, minimum, maximum):
    low, high = _ZERO, _ONE
    for axis in (0, 1):
        if delta[axis] == 0:
            if not minimum[axis] <= start[axis] <= maximum[axis]:
                return None
            continue
        first, last = sorted(((minimum[axis] - start[axis]) / delta[axis],
                              (maximum[axis] - start[axis]) / delta[axis]))
        low, high = max(low, first), min(high, last)
        if low > high:
            return None
    return low, high


def _page_id(name: str, row: int, column: int) -> str:
    return f"{name}-R{row + 1:02d}-C{column + 1:02d}"


def plan_and_clip_material_tiles(drawing: Any, sheet_options: Any, tiling: Any = None) -> dict[str, Any]:
    """Return a complete fixed-scale manifest, or raise before exposing pages.

    ``drawing`` contains one verified right-handed FRONT, TOP or SIDE view
    already classified against every material source. All rectangles retain
    the full model span, including blank space beyond the last row/column.
    ``source_interval`` is relative to each original projected line, not its
    mesh edge's potentially different visibility interval. This planner adds
    no model edges, reclassifies no visibility and drops no nonzero fragment.
    """
    if not isinstance(sheet_options, Mapping) or "scale_denominator" not in sheet_options:
        raise ValueError("material_tiles_explicit_scale_required")
    if "projection_method" in sheet_options:
        raise ValueError("material_tiles_projection_method_unsupported")
    sheet = normalize_sheet_options(sheet_options)
    options = normalize_material_tiling_options(tiling)
    sources = _material_sources(drawing)
    view, global_bounds, lines = _view_and_lines(drawing, sources)
    name = view["name"]
    short, long = _PAPERS[sheet["paper_size"]]
    width, height = (long, short) if sheet["orientation"] == "landscape" else (short, long)
    viewport = [width - 50.0, height - 94.0]
    if options["overlap_mm"] >= min(viewport) / 2:
        raise ValueError("material_tiles_overlap_exceeds_viewport_limit")
    scale = Fraction(sheet["scale_denominator"])
    # Round each public model span once, then use that very same nominal span
    # for exact grid arithmetic. A bound equal to the reported span fits one
    # page; an unreported sub-ulp product must not add an otherwise blank page.
    span_values = [_float(Fraction(value) * scale, "material_tiles_invalid_model_span") for value in viewport]
    overlap_value = _float(Fraction(options["overlap_mm"]) * scale, "material_tiles_invalid_model_span")
    spans = [Fraction(value) for value in span_values]
    overlap = Fraction(overlap_value)
    steps = [span - overlap for span in spans]
    if any(span <= 0 or step <= 0 for span, step in zip(spans, steps)):
        raise ValueError("material_tiles_invalid_model_span")
    origin = [Fraction(value) for value in global_bounds["min"]]
    global_maximum = [Fraction(value) for value in global_bounds["max"]]
    columns, rows = [_axis_count(high - low, span, step)
                     for low, high, span, step in zip(origin, global_maximum, spans, steps)]
    page_count = rows * columns
    if page_count > options["max_pages"] or page_count > MAX_MATERIAL_TILE_PAGES:
        raise ValueError("material_tiles_page_limit_exceeded")
    work = page_count * len(lines)
    if work > MAX_MATERIAL_TILE_LINE_TESTS:
        raise ValueError("material_tiles_line_work_limit_exceeded")
    step_values = [_float(value, "material_tiles_invalid_model_span") for value in steps]
    if any(value <= 0 for value in (*span_values, *step_values)):
        raise ValueError("material_tiles_unrepresentable_model_span")
    grid_maximum = [origin[axis] + (count - 1) * steps[axis] + spans[axis]
                    for axis, count in enumerate((columns, rows))]
    mathematical_grid_verified = all(high >= required and step <= span
                                     for high, required, step, span in zip(grid_maximum, global_maximum, steps, spans))
    if not mathematical_grid_verified:
        raise ValueError("material_tiles_mathematical_grid_coverage_gap")
    pages = []
    rectangles = []
    for row in range(rows):
        for column in range(columns):
            offsets = (column, rows - row - 1)
            low = [origin[axis] + offsets[axis] * steps[axis] for axis in (0, 1)]
            high = [low[axis] + spans[axis] for axis in (0, 1)]
            bounds = {"min": [_float(value, "material_tiles_unrepresentable_grid_bounds") for value in low],
                      "max": [_float(value, "material_tiles_unrepresentable_grid_bounds") for value in high]}
            if any(bounds["min"][axis] >= bounds["max"][axis] for axis in (0, 1)):
                raise ValueError("material_tiles_unrepresentable_grid_bounds")
            rectangles.append(tuple([Fraction(value) for value in bounds[key]] for key in ("min", "max")))
            neighbors = {direction: (_page_id(name, other_row, other_column)
                                     if 0 <= other_row < rows and 0 <= other_column < columns else None)
                         for direction, other_row, other_column in (
                             ("top", row - 1, column), ("bottom", row + 1, column),
                             ("left", row, column - 1), ("right", row, column + 1))}
            pages.append({"page_number": len(pages) + 1, "id": _page_id(name, row, column),
                          "row_index": row, "column_index": column, "bounds": bounds,
                          "neighbors": neighbors, "lines": [], "segment_count": 0})
    if not all(_union_covers([(low[axis], high[axis]) for low, high in rectangles], origin[axis], global_maximum[axis])
               for axis in (0, 1)):
        raise ValueError("material_tiles_emitted_grid_coverage_gap")
    rational_lines = []
    for line in lines:
        start = [Fraction(value) for value in line["start"]]
        rational_lines.append((start, [Fraction(value) - first for value, first in zip(line["end"], start)]))
    intervals = [[] for _ in lines]
    returned_intervals = [[] for _ in lines]
    fragment_count = point_touches = 0
    for page, (minimum, maximum) in zip(pages, rectangles):
        for index, (line, (start, delta)) in enumerate(zip(lines, rational_lines)):
            clipped = _clip_interval(start, delta, minimum, maximum)
            if clipped is None:
                continue
            low, high = clipped
            if low == high:
                point_touches += 1
                continue
            endpoints = [[_float(start[axis] + delta[axis] * parameter, "material_tiles_clip_below_float_precision")
                          for axis in (0, 1)] for parameter in (low, high)]
            if endpoints[0] == endpoints[1]:
                raise ValueError("material_tiles_clip_below_float_precision")
            source_interval = [float(low), float(high)]
            if source_interval[0] >= source_interval[1]:
                raise ValueError("material_tiles_source_interval_below_float_precision")
            if any(not page["bounds"]["min"][axis] <= point[axis] <= page["bounds"]["max"][axis]
                   for point in endpoints for axis in (0, 1)):
                raise ValueError("material_tiles_clipped_endpoint_outside_rectangle")
            fragment_count += 1
            if fragment_count > MAX_MATERIAL_TILE_CLIP_SEGMENTS:
                raise ValueError("material_tiles_clip_segment_limit_exceeded")
            page["lines"].append({**deepcopy(line), "start": endpoints[0], "end": endpoints[1],
                                  "global_line_index": index, "source_interval": source_interval})
            intervals[index].append((low, high))
            returned_intervals[index].append(tuple(Fraction(value) for value in source_interval))
        page["segment_count"] = len(page["lines"])
    for index, (exact, returned) in enumerate(zip(intervals, returned_intervals)):
        if not _union_covers(exact):
            raise ValueError(f"material_tiles_source_coverage_gap:line={index}")
        if not _union_covers(returned):
            raise ValueError(f"material_tiles_returned_source_coverage_gap:line={index}")
    axes = {field: list(view[field]) for field in ("horizontal_axis", "vertical_axis", "toward_viewer")}
    global_hash_domain = {"schema": "material-global-linework-v1", "geometry_sha256": drawing["geometry_sha256"],
                          "projection_convention": "right_handed", "view_name": name, **axes,
                          "global_bounds": global_bounds, "lines": lines}
    proof_keys = (*_PROOF_FIELDS, "scope", "footer", "coordinate_space", "coordinate_frame", "coordinate_unit", "counts")
    source_proof = {key: deepcopy(drawing[key]) for key in proof_keys}
    if "range_convention" in drawing:
        source_proof["range_convention"] = deepcopy(drawing["range_convention"])
    coverage = {"complete": True, "exact_rational_grid_verified": True, "emitted_float_grid_verified": True,
                "exact_source_interval_union_verified": True, "returned_source_interval_union_verified": True,
                "gap_tolerance": 0.0, "input_line_count": len(lines), "covered_line_count": len(lines),
                "clip_segment_count": fragment_count, "line_tile_tests": work, "point_touch_count": point_touches,
                "visibility_recomputed": False, "parameter_domain": [0.0, 1.0],
                "scope": "Exact rational interval unions over the input binary floats and the actual emitted rectangle bounds; no positive gap is accepted."}
    manifest = {"success": True, "available": True, "complete": True, "empty": False,
                "manifest_kind": "parameter material fixed-scale tiled view", "schema_version": 1,
                "coordinate_space": drawing["coordinate_space"], "coordinate_frame": drawing["coordinate_frame"],
                "coordinate_unit": "mm", "view_name": name, "projection_convention": "right_handed", **axes,
                "scope": drawing["scope"], "footer": drawing["footer"],
                "source_proof": source_proof, "source_proof_sha256": _digest(source_proof),
                "global_linework_sha256": _digest(global_hash_domain),
                "global_linework_hash_schema": "material-global-linework-v1",
                "sheet_options": sheet, "tiling_options": options,
                "paper": {"size": sheet["paper_size"], "orientation": sheet["orientation"], "width_mm": width, "height_mm": height},
                "viewport_mm": {"width": viewport[0], "height": viewport[1]},
                "scale_denominator": sheet["scale_denominator"], "overlap_mm": options["overlap_mm"],
                "overlap_model_mm": _float(overlap, "material_tiles_invalid_model_span"),
                "global_bounds": global_bounds, "grid_origin": list(global_bounds["min"]),
                "grid_bounds": {"min": list(global_bounds["min"]),
                                "max": [_float(value, "material_tiles_unrepresentable_grid_bounds") for value in grid_maximum]},
                "rows": rows, "columns": columns, "page_count": page_count,
                "tile_span_model_mm": span_values, "tile_step_model_mm": step_values,
                "boundary_policy": _BOUNDARY_POLICY,
                "clip_scope": "Clip existing global visibility classes to projection rectangles; clip intersections do not create material/model edges.",
                "engineering_accuracy_claim": False, "coverage": coverage,
                "limits": {"source_lines": MAX_MATERIAL_TILE_SOURCE_LINES, "clip_segments": MAX_MATERIAL_TILE_CLIP_SEGMENTS,
                           "line_tile_tests": MAX_MATERIAL_TILE_LINE_TESTS, "pages": MAX_MATERIAL_TILE_PAGES},
                "pages": pages}
    manifest["manifest_sha256"] = _digest({"schema": "material-tile-manifest-v1",
                                          "manifest": {key: value for key, value in manifest.items() if key != "source_proof"}})
    return manifest
