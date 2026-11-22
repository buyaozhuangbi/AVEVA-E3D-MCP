"""Revision-bound model-mm annotations between explicit section vertices.

Values are distances along one returned plane axis, not spatial point-to-point
distances or certified engineering dimensions. This module never edits input.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any


MAX_SECTION_DIMENSIONS = 8
MAX_SECTION_SEGMENTS = 20_000
MAX_ABS_COORDINATE_MM = 1_000_000_000.0
MAX_IDENTITY_NODES = 10_000
MAX_IDENTITY_BYTES = 1_000_000
_REVISION = re.compile(r"[0-9a-fA-F]{64}\Z")


def _index(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _number(value: Any, *, bounded: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("section_dimensions_invalid_coordinate")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise ValueError("section_dimensions_invalid_coordinate") from None
    if not math.isfinite(result) or (bounded and abs(result) > MAX_ABS_COORDINATE_MM):
        raise ValueError("section_dimensions_coordinate_out_of_bounds")
    return 0.0 if result == 0 else result


def _point(value: Any, length: int) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError("section_dimensions_invalid_coordinate")
    return [_number(component) for component in value]


def normalize_section_dimensions(options: Any) -> dict[str, Any]:
    """Validate the complete request before any source reads or index lookup."""
    if not isinstance(options, Mapping) or set(options) != {"expected_section_revision", "items"}:
        raise ValueError("invalid_section_dimensions_options")
    revision, items = options["expected_section_revision"], options["items"]
    if not isinstance(revision, str) or _REVISION.fullmatch(revision) is None:
        raise ValueError("invalid_section_dimensions_revision")
    if not isinstance(items, (list, tuple)) or not 1 <= len(items) <= MAX_SECTION_DIMENSIONS:
        raise ValueError("section_dimensions_items_must_contain_one_to_eight")
    normalized, seen = [], set()
    for item in items:
        if (not isinstance(item, Mapping) or not {"first_vertex", "second_vertex", "axis"} <= set(item) or
                set(item) - {"first_vertex", "second_vertex", "axis", "side"}):
            raise ValueError("invalid_section_dimension_item")
        first, second, axis = (item[key] for key in ("first_vertex", "second_vertex", "axis"))
        if not _index(first) or not _index(second):
            raise ValueError("invalid_section_dimension_vertex_index")
        if axis not in ("horizontal", "vertical"):
            raise ValueError("invalid_section_dimension_axis")
        side = item.get("side", "top" if axis == "horizontal" else "right")
        if side not in (("top", "bottom") if axis == "horizontal" else ("left", "right")):
            raise ValueError("invalid_section_dimension_side")
        if first == second:
            raise ValueError("section_dimension_identical_vertices")
        key = (min(first, second), max(first, second), axis, side)
        if key in seen:
            raise ValueError("duplicate_section_dimension")
        seen.add(key)
        normalized.append({"first_vertex": first, "second_vertex": second, "axis": axis, "side": side})
    return {"expected_section_revision": revision.lower(), "items": normalized}


def _section_geometry(section: Any) -> dict[str, Any]:
    if (not isinstance(section, Mapping) or
            any(section.get(flag) is not True for flag in ("success", "available", "complete")) or
            not isinstance(section.get("intersection"), bool)):
        raise ValueError("section_dimensions_successful_complete_section_required")
    if section.get("coordinate_unit") != "mm":
        raise ValueError("section_dimensions_model_millimetres_required")
    plane = section.get("plane")
    if not isinstance(plane, Mapping):
        raise ValueError("section_dimensions_invalid_plane")
    normalized_plane = {key: _point(plane.get(key), 3) for key in ("origin", "normal", "right", "up")}
    vertices, segments, loops = (section.get(key) for key in ("vertices", "segments", "loops"))
    if (not isinstance(vertices, (list, tuple)) or len(vertices) > MAX_SECTION_SEGMENTS or
            not isinstance(segments, (list, tuple)) or len(segments) > MAX_SECTION_SEGMENTS or
            not isinstance(loops, (list, tuple)) or len(loops) > len(segments) // 3):
        raise ValueError("section_dimensions_invalid_geometry")
    if section["intersection"] != bool(segments) or bool(segments) != bool(vertices) or bool(segments) != bool(loops):
        raise ValueError("section_dimensions_intersection_geometry_mismatch")
    points = [_point(point, 2) for point in vertices]
    normalized_segments = []
    for segment in segments:
        edge, triangles = ((segment.get(key) if isinstance(segment, Mapping) else None)
                           for key in ("vertices", "source_triangles"))
        if (not isinstance(edge, (list, tuple)) or len(edge) != 2 or
                any(not _index(value) or value >= len(points) for value in edge) or edge[0] == edge[1] or
                not isinstance(triangles, (list, tuple)) or not 1 <= len(triangles) <= 2 or
                any(not _index(value) for value in triangles) or len(set(triangles)) != len(triangles)):
            raise ValueError("section_dimensions_invalid_segment")
        normalized_segments.append({"vertices": list(edge), "source_triangles": list(triangles)})
    normalized_loops, seen_segments, seen_vertices = [], set(), set()
    for loop in loops:
        indices, source = ((loop.get(key) if isinstance(loop, Mapping) else None)
                           for key in ("vertices", "segment_indices"))
        if (not isinstance(indices, (list, tuple)) or not 4 <= len(indices) <= len(segments) + 1 or
                any(not _index(value) or value >= len(points) for value in indices) or indices[0] != indices[-1] or
                len(set(indices[:-1])) != len(indices) - 1 or not isinstance(source, (list, tuple)) or
                len(source) != len(indices) - 1 or
                any(not _index(value) or value >= len(segments) for value in source) or
                len(set(source)) != len(source) or seen_segments.intersection(source)):
            raise ValueError("section_dimensions_invalid_loop")
        for position, source_index in enumerate(source):
            if normalized_segments[source_index]["vertices"] != list(indices[position:position + 2]):
                raise ValueError("section_dimensions_loop_segment_mismatch")
        seen_segments.update(source)
        seen_vertices.update(indices[:-1])
        normalized_loops.append({"vertices": list(indices), "segment_indices": list(source)})
    if seen_segments != set(range(len(segments))) or seen_vertices != set(range(len(points))):
        raise ValueError("section_dimensions_incomplete_geometry_coverage")
    return {"plane": normalized_plane, "vertices": points, "segments": normalized_segments,
            "loops": normalized_loops, "intersection": section["intersection"]}


def _source_identity(value: Any) -> Any:
    nodes = 0

    def copy_value(item, depth):
        nonlocal nodes
        nodes += 1
        if nodes > MAX_IDENTITY_NODES or depth > 16:
            raise ValueError("section_dimension_source_identity_limit_exceeded")
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("invalid_section_dimension_source_identity")
            return 0.0 if item == 0 else item
        if isinstance(item, str):
            if len(item) > 8192:
                raise ValueError("section_dimension_source_identity_limit_exceeded")
            return item
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("invalid_section_dimension_source_identity")
            return {key: copy_value(component, depth + 1) for key, component in item.items()}
        if isinstance(item, (list, tuple)):
            return [copy_value(component, depth + 1) for component in item]
        raise ValueError("invalid_section_dimension_source_identity")

    if not isinstance(value, (Mapping, list, tuple)) or not value:
        raise ValueError("invalid_section_dimension_source_identity")
    return copy_value(value, 0)


def section_dimension_revision(section: Any, source_identity: Any) -> str:
    """Hash only indexed geometry, physical plane basis and explicit identity.

    Diagnostic text, timing, annotation results and existing revision fields do
    not participate. Source identity is supplied by the caller, normally its
    explicit resolved references and source geometry revision evidence.
    """
    payload = {"contract": "section-vertex-dimensions-v1", "section": _section_geometry(section),
               "source_identity": _source_identity(source_identity)}
    try:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                             allow_nan=False).encode("ascii")
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid_section_dimension_revision_input") from None
    if len(encoded) > MAX_IDENTITY_BYTES + 8_000_000:
        raise ValueError("section_dimension_revision_input_limit_exceeded")
    return hashlib.sha256(encoded).hexdigest()


def resolve_section_dimensions(section: Any, options: Any) -> list[dict[str, Any]]:
    """Resolve explicit section indices against the caller-bound revision."""
    normalized = normalize_section_dimensions(options)
    revision = section.get("dimension_revision") if isinstance(section, Mapping) else None
    if not isinstance(revision, str) or _REVISION.fullmatch(revision) is None:
        raise ValueError("section_dimension_revision_unavailable")
    if revision.lower() != normalized["expected_section_revision"]:
        raise ValueError("section_dimension_revision_mismatch")
    points = _section_geometry(section)["vertices"]
    result = []
    for item in normalized["items"]:
        first, second = item["first_vertex"], item["second_vertex"]
        if first >= len(points) or second >= len(points):
            raise ValueError("section_dimension_vertex_out_of_bounds")
        start, end = points[first], points[second]
        axis = 0 if item["axis"] == "horizontal" else 1
        value = abs(end[axis] - start[axis])
        if not math.isfinite(value) or value > MAX_ABS_COORDINATE_MM:
            raise ValueError("section_dimension_value_out_of_bounds")
        if value == 0:
            raise ValueError("section_dimension_zero_axis_distance")
        if float(format(value, ".6f")) == 0:
            raise ValueError("section_dimension_value_below_label_precision")
        result.append({**item, "start": list(start), "end": list(end), "value_mm": value})
    return result
