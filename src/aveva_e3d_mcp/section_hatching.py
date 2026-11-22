"""Bounded, exact-predicate nonzero hatching of indexed planar section loops.

The fill describes supplied oriented contours. It does not infer E3D holes,
apply native CSG, or certify engineering section properties.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Mapping
from fractions import Fraction
from typing import Any


MAX_SOURCE_SEGMENTS = 20_000
MAX_TOTAL_LINES = 20_000
MAX_SCANLINES = 4_096
MAX_TOPOLOGY_PAIR_CHECKS = 2_000_000
MAX_EDGE_SCANLINE_CHECKS = 2_000_000
MAX_DIAGNOSTICS = 100


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid_section_hatch_" + field)
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError("invalid_section_hatch_" + field) from None
    if not math.isfinite(result):
        raise ValueError("invalid_section_hatch_" + field)
    return result


def normalize_section_hatch(options: Any) -> dict[str, Any]:
    """Validate explicit model-mm spacing and a closed set of hatch options."""
    allowed = {"spacing_mm", "angle_degrees", "phase_mm", "fill_rule"}
    if not isinstance(options, Mapping) or "spacing_mm" not in options or set(options) - allowed:
        raise ValueError("invalid_section_hatch_options")
    spacing = _number(options["spacing_mm"], "spacing_mm")
    if not 1e-6 <= spacing <= 1e9:
        raise ValueError("section_hatch_spacing_out_of_bounds")
    angle = _number(options.get("angle_degrees", 45), "angle_degrees")
    if not 0 <= angle < 180:
        raise ValueError("section_hatch_angle_out_of_bounds")
    phase = _number(options.get("phase_mm", 0), "phase_mm")
    if abs(phase) > 1e9:
        raise ValueError("section_hatch_phase_out_of_bounds")
    fill_rule = options.get("fill_rule", "nonzero")
    if fill_rule != "nonzero":
        raise ValueError("invalid_section_hatch_fill_rule")
    remainder = Fraction(phase) % Fraction(spacing)
    phase = float(remainder)
    if not 0 <= phase < spacing:
        raise ValueError("section_hatch_phase_not_representable")
    return {"spacing_mm": spacing, "angle_degrees": angle, "phase_mm": phase, "fill_rule": fill_rule}


def _orientation(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, p):
    return _orientation(a, b, p) == 0 and all(min(a[i], b[i]) <= p[i] <= max(a[i], b[i]) for i in range(2))


def _bad_intersection(first, second, points, allowed_endpoint):
    a, b = (points[index] for index in first)
    c, d = (points[index] for index in second)
    orientations = (_orientation(a, b, c), _orientation(a, b, d),
                    _orientation(c, d, a), _orientation(c, d, b))
    if all(value == 0 for value in orientations):
        axis = 0 if a[0] != b[0] else 1
        left = max(min(a[axis], b[axis]), min(c[axis], d[axis]))
        right = min(max(a[axis], b[axis]), max(c[axis], d[axis]))
        return left < right or (left == right and not allowed_endpoint)
    if orientations[0] * orientations[1] < 0 and orientations[2] * orientations[3] < 0:
        return True
    touching = ((orientations[0] == 0 and _on_segment(a, b, c)) or
                (orientations[1] == 0 and _on_segment(a, b, d)) or
                (orientations[2] == 0 and _on_segment(c, d, a)) or
                (orientations[3] == 0 and _on_segment(c, d, b)))
    return touching and not allowed_endpoint


def build_section_hatching(section: Any, options: Any) -> dict[str, Any]:
    """Validate complete simple contours, then hatch their nonzero fill.

    Exact rational predicates operate on the supplied finite float coordinates
    and on the represented sine/cosine basis. Scanlines include their lower
    edge endpoint and exclude their upper endpoint. Collinear edges contribute
    no crossings. Every failure discards all generated lines.
    """
    counts = {"source_vertices": 0, "source_segments": 0, "loops": 0,
              "scanlines": 0, "hatch_lines": 0, "total_lines": 0,
              "topology_pair_checks": 0, "edge_scanline_checks": 0}
    normalized = None

    def fail(code, diagnostics=None, *, complete=True):
        return {"success": False, "available": False, "complete": complete, "error_code": code,
                "lines": [], "counts": {**counts, "hatch_lines": 0, "total_lines": counts["source_segments"]},
                "options": normalized, "coordinate_unit": "mm", "diagnostics": (diagnostics or [{"code": code}])[:MAX_DIAGNOSTICS]}

    try:
        normalized = normalize_section_hatch(options)
    except ValueError as exc:
        return fail(str(exc))
    if (not isinstance(section, Mapping) or section.get("success") is not True or
            section.get("available") is not True or section.get("complete") is not True or
            not isinstance(section.get("intersection"), bool)):
        return fail("section_hatch_complete_section_required")
    if section.get("coordinate_unit") != "mm":
        return fail("section_hatch_coordinate_unit_mm_required")
    vertices, segments, loops = (section.get(name) for name in ("vertices", "segments", "loops"))
    if any(not isinstance(values, (list, tuple)) for values in (vertices, segments, loops)):
        return fail("section_hatch_invalid_contours")
    counts.update(source_vertices=len(vertices), source_segments=len(segments), loops=len(loops), total_lines=len(segments))
    if len(segments) > MAX_SOURCE_SEGMENTS or len(segments) > MAX_TOTAL_LINES:
        return fail("section_hatch_source_limit_exceeded", complete=False)
    if not section["intersection"]:
        if vertices or segments or loops:
            return fail("section_hatch_intersection_geometry_mismatch")
        return {"success": True, "available": True, "complete": True, "error_code": None,
                "lines": [], "counts": counts, "options": normalized, "coordinate_unit": "mm",
                "diagnostics": [], "fill_rule": "nonzero", "hole_semantics_inferred": False,
                "boolean_operations_applied": False, "geometric_self_intersection_checked": True,
                "boundary_policy": "scanline lower endpoint inclusive, upper endpoint exclusive; collinear edges do not contribute crossings"}
    if (not vertices or not segments or not loops or len(vertices) > 2 * MAX_SOURCE_SEGMENTS or
            len(loops) > len(segments) // 3):
        return fail("section_hatch_invalid_contours")
    points = []
    for index, vertex in enumerate(vertices):
        try:
            if not isinstance(vertex, (list, tuple)) or len(vertex) != 2:
                raise ValueError()
            point = [_number(value, "coordinate") for value in vertex]
            if any(abs(value) > 1e9 for value in point):
                raise ValueError()
        except ValueError:
            return fail("section_hatch_invalid_coordinate", [{"vertex": index}])
        points.append(tuple(Fraction(value) for value in point))
    parsed_segments = []
    for index, segment in enumerate(segments):
        edge = segment.get("vertices") if isinstance(segment, Mapping) else None
        if (not isinstance(edge, (list, tuple)) or len(edge) != 2 or
                any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < len(points) for value in edge) or
                edge[0] == edge[1] or points[edge[0]] == points[edge[1]]):
            return fail("section_hatch_invalid_segment", [{"segment": index}])
        parsed_segments.append(tuple(edge))
    seen_segments, seen_vertices = set(), set()
    edge_loops = {}
    winding = []
    for loop_index, loop in enumerate(loops):
        indices = loop.get("vertices") if isinstance(loop, Mapping) else None
        source = loop.get("segment_indices") if isinstance(loop, Mapping) else None
        if (not isinstance(indices, (list, tuple)) or not 4 <= len(indices) <= len(segments) + 1 or
                any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < len(points) for value in indices) or
                indices[0] != indices[-1] or len(set(indices[:-1])) != len(indices) - 1 or
                not isinstance(source, (list, tuple)) or len(source) != len(indices) - 1):
            return fail("section_hatch_invalid_loop", [{"loop": loop_index}])
        for position, source_index in enumerate(source):
            if (isinstance(source_index, bool) or not isinstance(source_index, int) or
                    not 0 <= source_index < len(parsed_segments) or source_index in seen_segments or
                    parsed_segments[source_index] != tuple(indices[position:position + 2])):
                return fail("section_hatch_loop_segment_mismatch", [{"loop": loop_index}])
            seen_segments.add(source_index)
            edge_loops[source_index] = loop_index
        if seen_vertices.intersection(indices[:-1]):
            return fail("section_hatch_touching_contours", [{"loop": loop_index}])
        seen_vertices.update(indices[:-1])
        anchor = points[indices[0]]
        area2 = sum(_orientation(anchor, points[left], points[right]) for left, right in zip(indices, indices[1:]))
        if area2 == 0:
            return fail("section_hatch_degenerate_loop", [{"loop": loop_index}])
        winding.append(1 if area2 > 0 else -1)
    if seen_segments != set(range(len(parsed_segments))) or seen_vertices != set(range(len(points))):
        return fail("section_hatch_incomplete_contour_coverage")
    # A bounded X sweep avoids testing every pair for ordinary short edges.
    boxes = [(min(points[a][0], points[b][0]), max(points[a][0], points[b][0]),
              min(points[a][1], points[b][1]), max(points[a][1], points[b][1]))
             for a, b in parsed_segments]
    active, expiry = {}, []
    for index in sorted(range(len(boxes)), key=lambda item: (boxes[item][0], item)):
        box = boxes[index]
        while expiry and expiry[0][0] < box[0]:
            _, expired = heapq.heappop(expiry)
            active.pop(expired, None)
        for other in active:
            counts["topology_pair_checks"] += 1
            if counts["topology_pair_checks"] > MAX_TOPOLOGY_PAIR_CHECKS:
                return fail("section_hatch_topology_work_budget_exceeded", complete=False)
            other_box = boxes[other]
            if box[2] > other_box[3] or other_box[2] > box[3]:
                continue
            shared = set(parsed_segments[index]).intersection(parsed_segments[other])
            allowed = len(shared) == 1 and edge_loops[index] == edge_loops[other]
            if _bad_intersection(parsed_segments[index], parsed_segments[other], points, allowed):
                return fail("section_hatch_crossing_or_touching_contours", [{"segments": [other, index]}])
        active[index] = True
        heapq.heappush(expiry, (box[1], index))
    angle = normalized["angle_degrees"]
    if angle == 0:
        cosine, sine = Fraction(1), Fraction(0)
    elif angle == 90:
        cosine, sine = Fraction(0), Fraction(1)
    else:
        radians = math.radians(angle)
        cosine, sine = Fraction(math.cos(radians)), Fraction(math.sin(radians))
    basis_norm = cosine * cosine + sine * sine
    transformed = [(x * cosine + y * sine, -x * sine + y * cosine) for x, y in points]
    minimum, maximum = min(point[1] for point in transformed), max(point[1] for point in transformed)
    spacing, phase = Fraction(normalized["spacing_mm"]), Fraction(normalized["phase_mm"])
    first, last_exclusive = math.ceil((minimum - phase) / spacing), math.ceil((maximum - phase) / spacing)
    counts["scanlines"] = last_exclusive - first
    if counts["scanlines"] > MAX_SCANLINES:
        return fail("section_hatch_scanline_limit_exceeded", complete=False)
    lines = []
    for scanline in range(first, last_exclusive):
        level = phase + scanline * spacing
        events = []
        for edge_index, (a, b) in enumerate(parsed_segments):
            counts["edge_scanline_checks"] += 1
            if counts["edge_scanline_checks"] > MAX_EDGE_SCANLINE_CHECKS:
                return fail("section_hatch_scan_work_budget_exceeded", complete=False)
            sa, ta = transformed[a]
            sb, tb = transformed[b]
            if ta == tb or not min(ta, tb) <= level < max(ta, tb):
                continue
            position = sa + (level - ta) * (sb - sa) / (tb - ta)
            events.append((position, -1 if tb > ta else 1, edge_index))
        events.sort()
        event_index, current_winding, start = 0, 0, None
        previous_end = None
        while event_index < len(events):
            position, delta, source_index = events[event_index]
            sources = [source_index]
            event_index += 1
            while event_index < len(events) and events[event_index][0] == position:
                delta += events[event_index][1]
                sources.append(events[event_index][2])
                event_index += 1
            after = current_winding + delta
            if current_winding == 0 and after != 0:
                start = position, sorted(sources)
            elif current_winding != 0 and after == 0:
                if start is None or start[0] >= position:
                    return fail("section_hatch_scan_winding_ambiguous")
                endpoints = []
                for value in (start[0], position):
                    exact = ((value * cosine - level * sine) / basis_norm,
                             (value * sine + level * cosine) / basis_norm)
                    endpoint = [float(component) for component in exact]
                    if any(not math.isfinite(component) for component in endpoint):
                        return fail("section_hatch_projection_not_representable")
                    endpoints.append(endpoint)
                rounded_positions = [Fraction(endpoint[0]) * cosine + Fraction(endpoint[1]) * sine for endpoint in endpoints]
                if (rounded_positions[0] >= rounded_positions[1] or
                        (previous_end is not None and rounded_positions[0] <= previous_end)):
                    return fail("section_hatch_coordinate_rounding_ambiguous")
                previous_end = rounded_positions[1]
                lines.append({"start": endpoints[0], "end": endpoints[1], "scanline_index": scanline,
                              "boundary_source_segments": {"start": start[1], "end": sorted(sources)}})
                if len(lines) + len(parsed_segments) > MAX_TOTAL_LINES:
                    return fail("section_hatch_total_line_limit_exceeded", complete=False)
                start = None
            current_winding = after
        if current_winding != 0 or start is not None:
            return fail("section_hatch_scan_winding_ambiguous")
    counts.update(hatch_lines=len(lines), total_lines=len(lines) + len(parsed_segments))
    return {"success": True, "available": True, "complete": True, "error_code": None,
            "lines": lines, "counts": counts, "options": normalized, "coordinate_unit": "mm",
            "diagnostics": [], "fill_rule": "nonzero", "loop_winding": winding,
            "hole_semantics_inferred": False, "boolean_operations_applied": False,
            "geometric_self_intersection_checked": True,
            "boundary_policy": "scanline lower endpoint inclusive, upper endpoint exclusive; collinear edges do not contribute crossings",
            "predicate_policy": "exact rational predicates on input float coordinates and represented trigonometric basis; no tolerance snapping",
            "scope": "Nonzero fill of supplied oriented section contours in model millimetres; no native E3D hole inference or CSG."}
