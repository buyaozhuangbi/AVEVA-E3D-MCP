"""Bounded topology guards for six-decimal section hatch vector output.

The hatch kernel classifies exact input float contours. Vector serializers
must additionally reject gaps or fill intervals their output precision cannot
preserve. These checks use the actual encoded endpoint integers, not a distance
tolerance, and run only when hatch linework is present.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from fractions import Fraction
from typing import Any


MAX_PRECISION_CHECKS = 2_000_000
MAX_PRECISION_LINES = 20_000


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _sub(a, b):
    return a[0] - b[0], a[1] - b[1]


def _orientation(a, b, c):
    return _cross(_sub(b, a), _sub(c, a))


def _on_segment(a, b, point):
    return _orientation(a, b, point) == 0 and all(
        min(a[axis], b[axis]) <= point[axis] <= max(a[axis], b[axis]) for axis in (0, 1))


def _intersection_kind(a, b, c, d):
    """Return 0 for separation, 1 for one contact/crossing, 2 for overlap."""
    values = (_orientation(a, b, c), _orientation(a, b, d),
              _orientation(c, d, a), _orientation(c, d, b))
    if all(value == 0 for value in values):
        axis = 0 if a[0] != b[0] else 1
        low = max(min(a[axis], b[axis]), min(c[axis], d[axis]))
        high = min(max(a[axis], b[axis]), max(c[axis], d[axis]))
        return 2 if low < high else 1 if low == high else 0
    if values[0] * values[1] < 0 and values[2] * values[3] < 0:
        return 1
    return int((values[0] == 0 and _on_segment(a, b, c)) or
               (values[1] == 0 and _on_segment(a, b, d)) or
               (values[2] == 0 and _on_segment(c, d, a)) or
               (values[3] == 0 and _on_segment(c, d, b)))


def validate_section_hatch_precision(
        lines: Iterable[Mapping[str, Any]], *,
        transform: Callable[[Any], tuple[float, float]] | None = None,
        transform_orientation: int = 1) -> None:
    """Reject changed topology or escaped fill at actual six-decimal precision.

    Source silhouette segments form the directed contour. Hatch segments may
    intersect each other in the input; newly introduced contacts or overlaps
    are rejected. Ordinary shared contour endpoints and collinear subdivisions
    remain valid. Failure returns no generated output through the caller.
    """
    rows = list(lines)
    if not any(row.get("kind") == "hatch" for row in rows):
        return
    if len(rows) > MAX_PRECISION_LINES:
        raise ValueError("section_hatch_precision_line_limit_exceeded")
    if transform_orientation not in (-1, 1):
        raise ValueError("invalid_section_hatch_precision_transform")
    if transform is None:
        transform = lambda point: (point[0], point[1])
    contours, hatches = [], []
    encoded_vertices = {}
    checks = 0

    def consume():
        nonlocal checks
        checks += 1
        if checks > MAX_PRECISION_CHECKS:
            raise ValueError("section_hatch_precision_work_budget_exceeded")

    def endpoint(point):
        original = tuple(Fraction(float(value)) for value in point)
        mapped = transform(point)
        if any(not math.isfinite(value) for value in mapped):
            raise ValueError("section_hatch_output_coordinate_not_representable")
        # Both SVG and DXF serializers use Python's fixed six-decimal format.
        encoded = tuple(int(format(value, ".6f").replace(".", "")) for value in mapped)
        return original, encoded

    for row in rows:
        kind = row.get("kind")
        if kind not in {"silhouette", "hatch"}:
            continue
        original_a, a = endpoint(row["start"])
        original_b, b = endpoint(row["end"])
        if a == b:
            raise ValueError("section_hatch_output_geometry_below_precision")
        record = (a, b, original_a, original_b)
        if kind == "hatch":
            hatches.append(record)
        else:
            for source, encoded in ((original_a, a), (original_b, b)):
                if encoded in encoded_vertices and encoded_vertices[encoded] != source:
                    raise ValueError("section_hatch_output_contour_topology_changed")
                encoded_vertices[encoded] = source
            contours.append(record)
    if not contours:
        raise ValueError("section_hatch_output_contours_required")

    def candidates(records):
        # Only encoded bounding-box overlaps can introduce an output contact.
        boxes = [(min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1]))
                 for a, b, *_ in records]
        active = []
        for index in sorted(range(len(records)), key=lambda value: (boxes[value][0], value)):
            box = boxes[index]
            active = [other for other in active if boxes[other][1] >= box[0]]
            for other in active:
                consume()
                if box[2] <= boxes[other][3] and boxes[other][2] <= box[3]:
                    yield other, index
            active.append(index)

    for left, right in candidates(contours):
        a, b, original_a, original_b = contours[left]
        c, d, original_c, original_d = contours[right]
        encoded_kind = _intersection_kind(a, b, c, d)
        shared = {original_a, original_b}.intersection((original_c, original_d))
        if encoded_kind and (len(shared) != 1 or encoded_kind == 2):
            raise ValueError("section_hatch_output_contour_topology_changed")

    # Recover directed source loops independently of source line order. Apart
    # from intersections, quantization can reverse a very thin simple loop or
    # move a tiny disjoint loop through another boundary. Preserve both signed
    # winding and pairwise containment before accepting the encoded contours.
    outgoing, incoming, source_to_encoded = {}, {}, {}
    for a, b, original_a, original_b in contours:
        if original_a in outgoing or original_b in incoming:
            raise ValueError("section_hatch_output_contours_not_closed")
        outgoing[original_a], incoming[original_b] = original_b, original_a
        source_to_encoded[original_a], source_to_encoded[original_b] = a, b
    if set(outgoing) != set(incoming):
        raise ValueError("section_hatch_output_contours_not_closed")
    unseen, loops = set(outgoing), []
    for start in outgoing:
        if start not in unseen:
            continue
        current, original_loop = start, []
        while True:
            consume()
            if current not in unseen:
                raise ValueError("section_hatch_output_contours_not_closed")
            unseen.remove(current)
            original_loop.append(current)
            current = outgoing[current]
            if current == start:
                break
        encoded_loop = [source_to_encoded[point] for point in original_loop]
        def signed_area2(points):
            anchor = points[0]
            return sum(_orientation(anchor, a, b) for a, b in zip(points, points[1:] + points[:1]))
        original_area, encoded_area = signed_area2(original_loop), signed_area2(encoded_loop)
        if original_area == 0 or encoded_area == 0 or original_area * encoded_area * transform_orientation <= 0:
            raise ValueError("section_hatch_output_contour_winding_changed")
        loops.append((original_loop, encoded_loop))

    def point_winding(point, loop):
        winding = 0
        for a, b in zip(loop, loop[1:] + loop[:1]):
            consume()
            if _on_segment(a, b, point):
                return None
            if a[1] <= point[1] < b[1] and _orientation(a, b, point) > 0:
                winding += 1
            elif b[1] <= point[1] < a[1] and _orientation(a, b, point) < 0:
                winding -= 1
        return winding

    for index, (original, encoded) in enumerate(loops):
        for other_original, other_encoded in loops[index + 1:]:
            for point, other_point, loop, other_loop in (
                    (original[0], encoded[0], other_original, other_encoded),
                    (other_original[0], other_encoded[0], original, encoded)):
                before, after = point_winding(point, loop), point_winding(other_point, other_loop)
                if before is None or after is None or (before != 0) != (after != 0):
                    raise ValueError("section_hatch_output_contour_containment_changed")
    for left, right in candidates(hatches):
        a, b, original_a, original_b = hatches[left]
        c, d, original_c, original_d = hatches[right]
        encoded_kind = _intersection_kind(a, b, c, d)
        if encoded_kind > _intersection_kind(original_a, original_b, original_c, original_d):
            raise ValueError("section_hatch_output_fill_topology_changed")

    def fill_relation(point):
        winding = 0
        for a, b, *_ in contours:
            consume()
            if _on_segment(a, b, point):
                return "boundary"
            if a[1] <= point[1] < b[1] and _orientation(a, b, point) > 0:
                winding += 1
            elif b[1] <= point[1] < a[1] and _orientation(a, b, point) < 0:
                winding -= 1
        return "filled" if winding else "outside"

    def in_edge_rounding_band(endpoint, crossing):
        # An encoded edge's one-grid Minkowski band is convex. If its actual
        # crossing and the hatch endpoint both belong to that band, the entire
        # first/last tail does too, including shallow-angle intersections.
        for c, d, *_ in contours:
            consume()
            if not _on_segment(c, d, crossing):
                continue
            minimum, maximum = Fraction(0), Fraction(1)
            for axis in (0, 1):
                delta = d[axis] - c[axis]
                if delta == 0:
                    if abs(endpoint[axis] - c[axis]) > 1:
                        minimum, maximum = Fraction(1), Fraction(0)
                        break
                else:
                    low = Fraction(endpoint[axis] - 1 - c[axis], delta)
                    high = Fraction(endpoint[axis] + 1 - c[axis], delta)
                    if low > high:
                        low, high = high, low
                    minimum, maximum = max(minimum, low), min(maximum, high)
            if minimum <= maximum:
                return True
        return False

    for a, b, *_ in hatches:
        direction = _sub(b, a)
        cuts = {Fraction(0), Fraction(1)}
        for c, d, *_ in contours:
            consume()
            boundary = _sub(d, c)
            determinant = _cross(direction, boundary)
            if determinant == 0:
                # Include ends of collinear boundary spans so a coincident
                # portion cannot mask an empty interval farther along a line.
                if _orientation(a, b, c) == 0:
                    axis = 0 if direction[0] else 1
                    for point in (c, d):
                        parameter = Fraction(point[axis] - a[axis], direction[axis])
                        if 0 < parameter < 1:
                            cuts.add(parameter)
                continue
            delta = _sub(c, a)
            parameter = Fraction(_cross(delta, boundary), determinant)
            along_boundary = Fraction(_cross(delta, direction), determinant)
            if 0 < parameter < 1 and 0 <= along_boundary <= 1:
                cuts.add(parameter)
        ordered = sorted(cuts)
        has_filled_interval = False
        has_outside_interval = False
        for low, high in zip(ordered, ordered[1:]):
            parameter = (low + high) / 2
            point = tuple(a[axis] + direction[axis] * parameter for axis in (0, 1))
            relation = fill_relation(point)
            if relation == "filled":
                has_filled_interval = True
            elif relation == "outside":
                has_outside_interval = True
                # Independently rounding a contour and its hatch endpoint can
                # leave a small endpoint tail outside the encoded contour.
                # Each nearest rounding moves at most half one output grid;
                # permit only a first/last tail contained in the summed
                # one-grid L-infinity band of its actual encoded boundary
                # edge. Never forgive an internal empty interval or a whole
                # line outside the fill.
                if low == 0 and high < 1:
                    endpoint, tail_end = a, high
                elif high == 1 and low > 0:
                    endpoint, tail_end = b, low
                else:
                    raise ValueError("section_hatch_output_fill_outside_contour")
                crossing = tuple(a[axis] + direction[axis] * tail_end for axis in (0, 1))
                if not in_edge_rounding_band(endpoint, crossing):
                    raise ValueError("section_hatch_output_fill_outside_contour")
        if has_outside_interval and not has_filled_interval:
            raise ValueError("section_hatch_output_fill_outside_contour")
