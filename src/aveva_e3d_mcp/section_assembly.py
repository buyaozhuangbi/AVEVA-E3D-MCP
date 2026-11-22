"""Attach independent source identities to bounded assembly section contours.

Global triangle indices retain their merged-mesh meaning. This module only
records provenance; it does not join contours or evaluate Boolean material.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from .drawing_assembly import MAX_DRAWING_SOURCES
from .triangle_mesh_validation import MAX_TRIANGLES, MAX_VERTICES


MAX_SECTION_SEGMENTS = 20_000
MAX_SECTION_DIAGNOSTICS = 100
_SOURCE_TYPES = {"PANE", "BOX", "CYLI", "CONE", "DISH"}


def _index(value: Any, upper: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < upper


def _diagnostic_source_ranges(source_ranges: Any) -> tuple[list[dict[str, Any]], dict[str, list[tuple[int, int]]]]:
    if not isinstance(source_ranges, (list, tuple)) or not 1 <= len(source_ranges) <= MAX_DRAWING_SOURCES:
        raise ValueError("invalid_source_ranges")
    sources, ranges = [], {"triangle": [], "vertex": []}
    seen = set()
    for position, source in enumerate(source_ranges):
        if (not isinstance(source, Mapping) or not _index(source.get("source_index"), MAX_DRAWING_SOURCES) or
                source["source_index"] != position):
            raise ValueError("invalid_source_ranges")
        refno = source.get("refno")
        if not isinstance(refno, str) or len(refno) > 128 or re.fullmatch(r"=[0-9]+/[0-9]+", refno) is None:
            raise ValueError("invalid_source_ranges")
        key = "=" + "/".join(part.lstrip("0") or "0" for part in refno[1:].split("/"))
        if key in seen:
            raise ValueError("invalid_source_ranges")
        seen.add(key)
        for kind, maximum in (("triangle", MAX_TRIANGLES), ("vertex", MAX_VERTICES)):
            interval = source.get(kind + "_range")
            previous = ranges[kind][-1][1] if ranges[kind] else 0
            if (not isinstance(interval, (list, tuple)) or len(interval) != 2 or
                    any(isinstance(value, bool) or not isinstance(value, int) for value in interval) or
                    interval[0] != previous or not interval[0] < interval[1] <= maximum):
                raise ValueError("invalid_source_ranges")
            if kind + "_count" in source:
                count = source[kind + "_count"]
                if isinstance(count, bool) or not isinstance(count, int) or count != interval[1] - interval[0]:
                    raise ValueError("invalid_source_ranges")
            ranges[kind].append(tuple(interval))
        sources.append({"source_index": position, "refno": refno})
    return sources, ranges


def annotate_section_diagnostics(section: Any, source_ranges: Any) -> dict[str, Any]:
    """Copy slice diagnostics and map known source-global index evidence.

    Unknown diagnostic semantics and invalid ranges/indices never receive a
    guessed identity. In particular a degree-two failure's ``vertex`` denotes
    a generated section vertex, unlike a near-plane/tangent source vertex.
    Mapping is ancillary evidence: it preserves the slice's geometry, status,
    global index fields and error code even when attribution is unavailable.
    """
    if not isinstance(section, Mapping):
        raise ValueError("section_assembly_section_object_required")
    output = copy.deepcopy(dict(section))
    diagnostics = section.get("diagnostics")
    if not isinstance(diagnostics, (list, tuple)):
        return output
    try:
        sources, ranges = _diagnostic_source_ranges(source_ranges)
        counts = section.get("counts")
        for kind, key in (("triangle", "source_triangles"), ("vertex", "source_vertices")):
            if isinstance(counts, Mapping) and key in counts:
                value = counts[key]
                if isinstance(value, bool) or not isinstance(value, int) or value != ranges[kind][-1][1]:
                    raise ValueError("source_range_count_mismatch")
        range_error = None
    except ValueError as exc:
        sources, ranges, range_error = [], {}, str(exc)
    copied = []
    for position, diagnostic in enumerate(diagnostics):
        if not isinstance(diagnostic, Mapping):
            copied.append(copy.deepcopy(diagnostic))
            continue
        row = copy.deepcopy(dict(diagnostic))
        row.update(source_evidence=[], source_mapping_available=False)
        row.pop("source_mapping_reason", None)
        if position >= MAX_SECTION_DIAGNOSTICS:
            row["source_mapping_reason"] = "diagnostic_mapping_limit_exceeded"
            copied.append(row)
            continue
        code, error = diagnostic.get("code"), section.get("error_code")
        fields = []
        if code == "coplanar_triangle" or (code is None and error == "section_triangle_intersection_ambiguous" and "triangle" in diagnostic):
            fields = [("triangle", [diagnostic.get("triangle")])]
        elif code in {"near_plane_vertex", "isolated_plane_vertex"}:
            fields = [("vertex", [diagnostic.get("vertex")])]
        elif code == "tangent_plane_edge":
            edge, triangles = diagnostic.get("edge"), diagnostic.get("source_triangles")
            if not isinstance(edge, (list, tuple)) or len(edge) != 2 or not isinstance(triangles, (list, tuple)) or not 1 <= len(triangles) <= 2:
                row["source_mapping_reason"] = "invalid_source_index_evidence"
                copied.append(row)
                continue
            fields = [("vertex", edge), ("triangle", triangles)]
        else:
            row["source_mapping_reason"] = ("section_vertex_indices_are_not_source_vertices"
                                            if error == "section_contour_not_degree_two" and "vertex" in diagnostic
                                            else "diagnostic_has_no_known_source_indices")
            copied.append(row)
            continue
        if range_error:
            row["source_mapping_reason"] = range_error
            copied.append(row)
            continue
        found, valid = {}, True
        for kind, indices in fields:
            for index in indices:
                if isinstance(index, bool) or not isinstance(index, int):
                    valid = False
                    break
                matches = [(source_index, start) for source_index, (start, end) in enumerate(ranges[kind]) if start <= index < end]
                if len(matches) != 1:
                    valid = False
                    break
                source_index, start = matches[0]
                evidence = found.setdefault(source_index, {**sources[source_index]})
                local_key = "local_" + kind + "_indices"
                evidence.setdefault(local_key, []).append(index - start)
            if not valid:
                break
        if not valid:
            row["source_mapping_reason"] = "source_index_out_of_bounds_or_invalid"
        else:
            for evidence in found.values():
                for key in ("local_triangle_indices", "local_vertex_indices"):
                    if key in evidence:
                        evidence[key] = sorted(set(evidence[key]))
            row.update(source_mapping_available=True, source_evidence=[found[index] for index in sorted(found)])
        copied.append(row)
    output["diagnostics"] = copied
    return output


def annotate_section_sources(section: Any, source_ranges: Any) -> dict[str, Any]:
    """Copy a complete section and attach one source per segment and loop.

    Source triangle ranges are zero-based, contiguous, half-open ranges from
    ``combine_drawing_meshes``. Every segment's global source triangle indices
    must resolve to one range. Every closed loop must consist solely of that
    source's segments, with complete unique coverage of the section. Invalid
    evidence raises ``ValueError`` before a copied result is returned.
    """
    if (not isinstance(section, Mapping) or
            any(section.get(flag) is not True for flag in ("success", "available", "complete")) or
            not isinstance(section.get("intersection"), bool)):
        raise ValueError("section_assembly_successful_complete_section_required")
    if not isinstance(source_ranges, (list, tuple)) or not 1 <= len(source_ranges) <= MAX_DRAWING_SOURCES:
        raise ValueError("section_assembly_invalid_source_ranges")
    sources, triangle_sources, seen_refs = [], [], set()
    for position, source in enumerate(source_ranges):
        source_index = source.get("source_index") if isinstance(source, Mapping) else None
        if not _index(source_index, MAX_DRAWING_SOURCES) or source_index != position:
            raise ValueError("section_assembly_invalid_source_ranges")
        refno, resolved_type, interval = (source.get(key) for key in ("refno", "resolved_type", "triangle_range"))
        if (not isinstance(refno, str) or len(refno) > 128 or re.fullmatch(r"=[0-9]+/[0-9]+", refno) is None or
                not isinstance(resolved_type, str) or resolved_type not in _SOURCE_TYPES or
                not isinstance(interval, (list, tuple)) or len(interval) != 2 or
                any(isinstance(value, bool) or not isinstance(value, int) for value in interval)):
            raise ValueError("section_assembly_invalid_source_ranges")
        key = "=" + "/".join(part.lstrip("0") or "0" for part in refno[1:].split("/"))
        if key in seen_refs:
            raise ValueError("section_assembly_duplicate_source_reference")
        seen_refs.add(key)
        start, end = interval
        if start != len(triangle_sources) or not start < end <= MAX_TRIANGLES:
            raise ValueError("section_assembly_invalid_source_ranges")
        if "triangle_count" in source and (isinstance(source["triangle_count"], bool) or
                                            not isinstance(source["triangle_count"], int) or
                                            source["triangle_count"] != end - start):
            raise ValueError("section_assembly_invalid_source_ranges")
        triangle_sources.extend([position] * (end - start))
        sources.append({"source_index": position, "refno": refno, "resolved_type": resolved_type,
                        "intersection": False, "segment_indices": [], "loop_indices": []})
    counts = section.get("counts")
    if isinstance(counts, Mapping) and "source_triangles" in counts:
        count = counts["source_triangles"]
        if isinstance(count, bool) or not isinstance(count, int) or count != len(triangle_sources):
            raise ValueError("section_assembly_source_triangle_count_mismatch")
    vertices, segments, loops = (section.get(key) for key in ("vertices", "segments", "loops"))
    if (not isinstance(vertices, (list, tuple)) or len(vertices) > MAX_VERTICES * 2 or
            not isinstance(segments, (list, tuple)) or len(segments) > MAX_SECTION_SEGMENTS or
            not isinstance(loops, (list, tuple)) or len(loops) > len(segments) // 3):
        raise ValueError("section_assembly_invalid_section_geometry")
    if section["intersection"] != bool(segments) or bool(segments) != bool(loops) or bool(segments) != bool(vertices):
        raise ValueError("section_assembly_intersection_geometry_mismatch")
    annotations, used_vertices = [], set()
    for segment_index, segment in enumerate(segments):
        triangles = segment.get("source_triangles") if isinstance(segment, Mapping) else None
        endpoints = segment.get("vertices") if isinstance(segment, Mapping) else None
        if (not isinstance(triangles, (list, tuple)) or not 1 <= len(triangles) <= 2 or
                any(not _index(value, len(triangle_sources)) for value in triangles) or len(set(triangles)) != len(triangles)):
            raise ValueError("section_assembly_invalid_source_triangles")
        if (not isinstance(endpoints, (list, tuple)) or len(endpoints) != 2 or endpoints[0] == endpoints[1] or
                any(not _index(value, len(vertices)) for value in endpoints)):
            raise ValueError("section_assembly_invalid_section_segment")
        used_vertices.update(endpoints)
        source_indices = {triangle_sources[triangle] for triangle in triangles}
        if len(source_indices) != 1:
            raise ValueError("section_assembly_segment_crosses_sources")
        source_index = source_indices.pop()
        source = sources[source_index]
        source["intersection"] = True
        source["segment_indices"].append(segment_index)
        annotations.append({"source_index": source_index, "refno": source["refno"],
                            "source_triangle_indices": [triangle - source_ranges[source_index]["triangle_range"][0]
                                                        for triangle in triangles]})
    if used_vertices != set(range(len(vertices))):
        raise ValueError("section_assembly_incomplete_vertex_coverage")
    seen_segments, loop_annotations = set(), []
    for loop_index, loop in enumerate(loops):
        indices = loop.get("segment_indices") if isinstance(loop, Mapping) else None
        points = loop.get("vertices") if isinstance(loop, Mapping) else None
        if (not isinstance(indices, (list, tuple)) or not 3 <= len(indices) <= len(segments) or
                any(not _index(index, len(segments)) for index in indices) or
                len(set(indices)) != len(indices) or seen_segments.intersection(indices) or
                not isinstance(points, (list, tuple)) or len(points) != len(indices) + 1 or
                any(not _index(point, len(vertices)) for point in points) or points[0] != points[-1] or
                len(set(points[:-1])) != len(points) - 1):
            raise ValueError("section_assembly_invalid_section_loop")
        for position, segment_index in enumerate(indices):
            if tuple(segments[segment_index]["vertices"]) != tuple(points[position:position + 2]):
                raise ValueError("section_assembly_loop_segment_mismatch")
        source_indices = {annotations[index]["source_index"] for index in indices}
        if len(source_indices) != 1:
            raise ValueError("section_assembly_loop_crosses_sources")
        source_index = source_indices.pop()
        sources[source_index]["loop_indices"].append(loop_index)
        seen_segments.update(indices)
        loop_annotations.append({"source_index": source_index, "refno": sources[source_index]["refno"]})
    if seen_segments != set(range(len(segments))):
        raise ValueError("section_assembly_incomplete_segment_coverage")
    output = copy.deepcopy(dict(section))
    output["segments"] = [dict(segment, **annotation) for segment, annotation in zip(output["segments"], annotations)]
    output["loops"] = [dict(loop, **annotation) for loop, annotation in zip(output["loops"], loop_annotations)]
    output["source_sections"] = sources
    return output
