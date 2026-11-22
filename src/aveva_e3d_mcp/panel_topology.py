"""Conservative pairwise relations for native PANE loop outlines.

This module is deliberately independent of AVEVA SDK objects. It accepts the
JSON-shaped result of a native panel geometry read and only classifies the
2-D boundaries it can fully inspect. It never assigns outer/hole/blind roles.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


COORDINATE_TOLERANCE_MM = 0.00001
MAX_LOOPS = 32
MAX_TOTAL_VERTICES = 2048
_ROUND_OFF = 32.0 * 2.220446049250313e-16


def analyze_panel_loop_relations(native_geometry: dict[str, Any]) -> dict[str, Any]:
    """Return bounded, JSON-safe pair relations for complete local-XY PLOO rings.

    ``native_geometry`` has the shape returned by the bridge panel reader:
    ``{"complete": bool, "loops": [{"complete": bool, "vertices":
    [{"position": [x, y, z]}], ...}]}``. A loop is left unknown when its read
    was incomplete, it has errors/unsupported features, or its polygon is not a
    finite simple ring. The input's ``complete`` flag is retained because
    relationships among returned loops do not cover unread native members.
    """

    base: dict[str, Any] = {
        "available": False,
        "complete": False,
        "status": "unavailable",
        "tolerance_mm": COORDINATE_TOLERANCE_MM,
        "coordinate_frame": "PANE local XY",
        "limitations": [
            "Relations describe only 2-D boundaries in PANE-local XY.",
            "Outer, hole, blind-loop, solid, generated-geometry, and display semantics are not inferred.",
            "Incomplete or unsupported loop geometry is reported as unknown.",
        ],
        "loop_count": 0,
        "analyzed_loop_count": 0,
        "pair_count": 0,
        "analyzed_pair_count": 0,
        "omitted_pair_count": 0,
        "known_pair_count": 0,
        "unknown_pair_count": 0,
        "pairs": [],
        "issues": [],
    }
    if not isinstance(native_geometry, Mapping):
        base["issues"] = [{"code": "invalid_geometry_input"}]
        return base

    loops_value = native_geometry.get("loops")
    if not isinstance(loops_value, Sequence) or isinstance(loops_value, (str, bytes, bytearray)):
        base["issues"] = [{"code": "loops_unavailable"}]
        return base

    base["available"] = True
    loop_count = len(loops_value)
    analyzed_loop_count = min(loop_count, MAX_LOOPS)
    loops = [loops_value[index] for index in range(analyzed_loop_count)]
    pair_count = loop_count * (loop_count - 1) // 2
    base.update({
        "loop_count": loop_count,
        "analyzed_loop_count": analyzed_loop_count,
        "pair_count": pair_count,
        "omitted_pair_count": pair_count - analyzed_loop_count * (analyzed_loop_count - 1) // 2,
    })

    loop_records: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    top_level_errors = native_geometry.get("read_errors")
    if top_level_errors:
        issues.append({"code": "native_geometry_read_errors"})
    top_level_unsupported = native_geometry.get("unsupported_features")
    if top_level_unsupported:
        issues.append({"code": "native_geometry_unsupported_features"})
    vertices_used = 0
    all_loops_known = True
    for index, loop in enumerate(loops):
        remaining_vertices = MAX_TOTAL_VERTICES - vertices_used
        points, reason, vertex_count = _read_loop(loop, remaining_vertices)
        if vertex_count > remaining_vertices:
            vertices_used = MAX_TOTAL_VERTICES
        else:
            vertices_used += vertex_count
        if reason is None and points is not None:
            reason = _simple_ring_error(points)
        record = {"points": points if reason is None else None, "reason": reason}
        loop_records.append(record)
        if reason is not None:
            all_loops_known = False
            issues.append({"loop_index": index, "code": reason})

    pairs: list[dict[str, Any]] = []
    unknown_pair_count = 0
    for first in range(analyzed_loop_count):
        for second in range(first + 1, analyzed_loop_count):
            left, right = loop_records[first], loop_records[second]
            pair: dict[str, Any] = {"first_loop_index": first, "second_loop_index": second}
            if left["reason"] is not None:
                pair.update({"relation": "unknown", "complete": False, "reason": left["reason"]})
                unknown_pair_count += 1
            elif right["reason"] is not None:
                pair.update({"relation": "unknown", "complete": False, "reason": right["reason"]})
                unknown_pair_count += 1
            else:
                pair["relation"] = _relate(left["points"], right["points"])
                pair["complete"] = pair["relation"] != "unknown"
                if not pair["complete"]:
                    pair["reason"] = "numeric_range"
                    unknown_pair_count += 1
                else:
                    base["known_pair_count"] += 1
            pairs.append(pair)

    geometry_complete = native_geometry.get("complete") is True
    if not geometry_complete:
        issues.append({"code": "native_geometry_incomplete"})
    if loop_count == 0:
        issues.append({"code": "no_loops"})
    omitted_pair_count = base["omitted_pair_count"]
    if omitted_pair_count:
        issues.append({"code": "loop_limit"})
        unknown_pair_count += omitted_pair_count
    if vertices_used >= MAX_TOTAL_VERTICES and any(
        record["reason"] == "vertex_limit" for record in loop_records
    ):
        if not any(issue["code"] == "vertex_limit" for issue in issues):
            issues.append({"code": "vertex_limit"})

    complete = (geometry_complete and not top_level_errors and not top_level_unsupported and loop_count > 0 and
                all_loops_known and unknown_pair_count == 0 and omitted_pair_count == 0)
    base.update({
        "complete": complete,
        "status": "complete" if complete else "partial",
        "analyzed_pair_count": len(pairs),
        "known_pair_count": base["known_pair_count"],
        "unknown_pair_count": unknown_pair_count,
        "pairs": pairs,
        "issues": issues,
    })
    return base


def _read_loop(loop: Any, vertex_budget: int) -> tuple[list[tuple[float, float]] | None, str | None, int]:
    if not isinstance(loop, Mapping):
        return None, "invalid_loop", 0
    vertices = loop.get("vertices")
    if not isinstance(vertices, Sequence) or isinstance(vertices, (str, bytes, bytearray)):
        return None, "vertices_unavailable", 0
    count = len(vertices)
    if loop.get("complete") is not True:
        return None, "loop_incomplete", count
    if loop.get("read_errors"):
        return None, "loop_read_errors", count
    if loop.get("unsupported_features"):
        return None, "unsupported_geometry", count
    if loop.get("planar_parallel_xy") is not True or loop.get("coplanar_in_local_xy") is not True:
        return None, "unsupported_plane", count
    if loop.get("polygon_valid") is False:
        return None, "invalid_polygon", count
    if loop.get("polygon_valid") is not True:
        return None, "polygon_validity_unknown", count
    if count > vertex_budget:
        return None, "vertex_limit", count

    points: list[tuple[float, float]] = []
    for vertex in vertices:
        position = vertex.get("position") if isinstance(vertex, Mapping) else vertex
        if not isinstance(position, Sequence) or isinstance(position, (str, bytes, bytearray)):
            return None, "vertex_position_unavailable", count
        if len(position) != 3:
            return None, "invalid_vertex", count
        values: list[float] = []
        for value in position:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None, "invalid_vertex", count
            try:
                converted = float(value)
            except (TypeError, ValueError, OverflowError):
                return None, "invalid_vertex", count
            if not math.isfinite(converted):
                return None, "non_finite_coordinate", count
            values.append(converted)
        if len(values) == 3 and abs(values[2]) > COORDINATE_TOLERANCE_MM:
            return None, "unsupported_plane", count
        points.append((values[0], values[1]))
    if count < 3:
        return None, "invalid_polygon", count
    return points, None, count


def _simple_ring_error(points: list[tuple[float, float]]) -> str | None:
    count = len(points)
    for index in range(count):
        length = _distance(points[index], points[(index + 1) % count])
        if not math.isfinite(length):
            return "numeric_range"
        if length <= COORDINATE_TOLERANCE_MM:
            return "invalid_polygon"

    # The shoelace sum is evaluated after translating and scaling the ring to
    # avoid cancellation for small profiles far from the PANE origin.
    origin_x, origin_y = points[0]
    relative: list[tuple[float, float]] = []
    for x, y in points:
        dx, dy = x - origin_x, y - origin_y
        if not math.isfinite(dx) or not math.isfinite(dy):
            return "numeric_range"
        relative.append((dx, dy))
    scale = max(max(abs(x), abs(y)) for x, y in relative)
    if not math.isfinite(scale) or scale <= COORDINATE_TOLERANCE_MM:
        return "invalid_polygon"
    scaled = [(x / scale, y / scale) for x, y in relative]
    terms = [
        scaled[index][0] * scaled[(index + 1) % count][1]
        - scaled[(index + 1) % count][0] * scaled[index][1]
        for index in range(count)
    ]
    twice_area = math.fsum(terms)
    normalized_perimeter = math.fsum(
        math.hypot(
            scaled[(index + 1) % count][0] - scaled[index][0],
            scaled[(index + 1) % count][1] - scaled[index][1],
        )
        for index in range(count)
    )
    roundoff = _ROUND_OFF * math.fsum(abs(term) for term in terms)
    area_tolerance = COORDINATE_TOLERANCE_MM / scale * normalized_perimeter + roundoff
    if not math.isfinite(twice_area) or abs(twice_area) <= area_tolerance:
        return "invalid_polygon"

    for first in range(count):
        first_next = (first + 1) % count
        for second in range(first + 1, count):
            second_next = (second + 1) % count
            if second == first_next or second_next == first:
                if second == first_next and _adjacent_backtrack(
                    points[first], points[first_next], points[second_next]
                ):
                    return "invalid_polygon"
                if second_next == first and _adjacent_backtrack(
                    points[second], points[first], points[first_next]
                ):
                    return "invalid_polygon"
                continue
            segment_relation = _segment_relation(points[first], points[first_next], points[second], points[second_next])
            if segment_relation == "unknown":
                return "numeric_range"
            if segment_relation != "disjoint":
                return "invalid_polygon"
    return None


def _relate(first: list[tuple[float, float]], second: list[tuple[float, float]]) -> str:
    crossing = False
    touching = False
    unknown = False
    for first_index, a in enumerate(first):
        b = first[(first_index + 1) % len(first)]
        for second_index, c in enumerate(second):
            d = second[(second_index + 1) % len(second)]
            relation = _segment_relation(a, b, c, d)
            if relation == "crosses":
                crossing = True
            elif relation == "touches":
                touching = True
            elif relation == "unknown":
                unknown = True
    if crossing:
        return "boundary_crossing"
    if unknown:
        return "unknown"
    if touching:
        return "boundary_contact"

    first_contains = _point_in_ring(second[0], first)
    second_contains = _point_in_ring(first[0], second)
    if first_contains is None or second_contains is None:
        return "unknown"
    if first_contains and second_contains:
        return "unknown"
    if first_contains:
        return "contains"
    if second_contains:
        return "contained_by"
    return "disjoint"


def _segment_relation(a: tuple[float, float], b: tuple[float, float],
                      c: tuple[float, float], d: tuple[float, float]) -> str:
    if _separated(a[0], b[0], c[0], d[0]) or _separated(a[1], b[1], c[1], d[1]):
        return "disjoint"
    first_c, first_d = _orientation(a, b, c), _orientation(a, b, d)
    second_a, second_b = _orientation(c, d, a), _orientation(c, d, b)
    if first_c is None or first_d is None or second_a is None or second_b is None:
        return "unknown"
    if first_c * first_d < 0 and second_a * second_b < 0:
        return "crosses"
    if ((first_c == 0 and _within(c, a, b)) or (first_d == 0 and _within(d, a, b)) or
            (second_a == 0 and _within(a, c, d)) or (second_b == 0 and _within(b, c, d))):
        return "touches"
    return "disjoint"


def _orientation(a: tuple[float, float], b: tuple[float, float],
                 point: tuple[float, float]) -> int | None:
    abx, aby = b[0] - a[0], b[1] - a[1]
    apx, apy = point[0] - a[0], point[1] - a[1]
    if not all(math.isfinite(value) for value in (abx, aby, apx, apy)):
        return None
    length = math.hypot(abx, aby)
    if not math.isfinite(length) or length <= COORDINATE_TOLERANCE_MM:
        return 0
    signed_distance = (abx / length) * apy - (aby / length) * apx
    if not math.isfinite(signed_distance):
        return None
    if signed_distance > COORDINATE_TOLERANCE_MM:
        return 1
    if signed_distance < -COORDINATE_TOLERANCE_MM:
        return -1
    return 0


def _point_in_ring(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool | None:
    origin_x, origin_y = ring[0]
    relative = [(x - origin_x, y - origin_y) for x, y in ring]
    px, py = point[0] - origin_x, point[1] - origin_y
    if not math.isfinite(px) or not math.isfinite(py) or any(
        not math.isfinite(x) or not math.isfinite(y) for x, y in relative
    ):
        return None
    scale = max(max(abs(x), abs(y)) for x, y in relative)
    if scale <= COORDINATE_TOLERANCE_MM or not math.isfinite(scale):
        return None
    ring_scaled = [(x / scale, y / scale) for x, y in relative]
    point_scaled = (px / scale, py / scale)
    if not math.isfinite(point_scaled[0]) or not math.isfinite(point_scaled[1]):
        return None
    inside = False
    for index, (x1, y1) in enumerate(ring_scaled):
        x2, y2 = ring_scaled[(index + 1) % len(ring_scaled)]
        if (y1 > point_scaled[1]) != (y2 > point_scaled[1]):
            crossing_x = x1 + (point_scaled[1] - y1) * (x2 - x1) / (y2 - y1)
            if not math.isfinite(crossing_x):
                return None
            if point_scaled[0] < crossing_x:
                inside = not inside
    return inside


def _adjacent_backtrack(a: tuple[float, float], b: tuple[float, float],
                        c: tuple[float, float]) -> bool:
    orientation = _orientation(a, b, c)
    if orientation != 0:
        return False
    first = (a[0] - b[0], a[1] - b[1])
    second = (c[0] - b[0], c[1] - b[1])
    first_length, second_length = math.hypot(*first), math.hypot(*second)
    if not first_length or not second_length or not math.isfinite(first_length + second_length):
        return False
    dot = (first[0] / first_length) * (second[0] / second_length) + \
        (first[1] / first_length) * (second[1] / second_length)
    return dot > _ROUND_OFF


def _separated(a: float, b: float, c: float, d: float) -> bool:
    return min(c, d) - max(a, b) > COORDINATE_TOLERANCE_MM or \
        min(a, b) - max(c, d) > COORDINATE_TOLERANCE_MM


def _within(point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> bool:
    return (point[0] - max(a[0], b[0]) <= COORDINATE_TOLERANCE_MM and
            min(a[0], b[0]) - point[0] <= COORDINATE_TOLERANCE_MM and
            point[1] - max(a[1], b[1]) <= COORDINATE_TOLERANCE_MM and
            min(a[1], b[1]) - point[1] <= COORDINATE_TOLERANCE_MM)


def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    dx, dy = second[0] - first[0], second[1] - first[1]
    if not math.isfinite(dx) or not math.isfinite(dy):
        return math.inf
    return math.hypot(dx, dy)
