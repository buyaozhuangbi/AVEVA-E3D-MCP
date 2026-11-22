"""Bounded topology and orientation checks for caller-supplied triangle meshes."""

from __future__ import annotations

import math
from collections import Counter, defaultdict, deque
from collections.abc import Mapping
from typing import Any


MAX_VERTICES = 10000
MAX_TRIANGLES = 20000
MAX_FINDINGS = 100
_RELATIVE_AREA_TOLERANCE = 1e-12
_RELATIVE_VOLUME_TOLERANCE = 1e-15


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _result(findings: list[dict[str, Any]], counts: dict[str, int], checks: dict[str, Any],
            components: list[dict[str, Any]], *, complete: bool, success: bool = True) -> dict[str, Any]:
    return {
        "success": success,
        "available": success,
        "complete": complete,
        "valid": success and complete and not findings and checks.get("closed_manifold") is True and
        checks.get("winding_consistent") is True and checks.get("orientation_outward") is True,
        "coordinate_unit": "caller_supplied; not inferred",
        "volume_unit": "coordinate_unit^3; not inferred",
        "counts": counts,
        "checks": checks,
        "components": components,
        "findings": findings[:MAX_FINDINGS],
        "findings_truncated": len(findings) > MAX_FINDINGS,
        "scope": "Indexed triangle topology, scale-relative degeneracy, winding and per-component signed volume. Does not detect geometric self-intersections or certify native E3D CSG/rendering.",
    }


def validate_triangle_mesh(vertices: Any, triangles: Any) -> dict[str, Any]:
    """Validate a bounded indexed mesh without native reads or geometry writes."""
    findings: list[dict[str, Any]] = []
    counts = {"vertices": len(vertices) if isinstance(vertices, (list, tuple)) else 0,
              "triangles": len(triangles) if isinstance(triangles, (list, tuple)) else 0,
              "valid_triangles": 0}
    checks: dict[str, Any] = {
        "finite_coordinates": False, "valid_indices": False, "nondegenerate_triangles": False,
        "duplicate_faces": False, "edge_manifold": None, "vertex_manifold": None,
        "winding_consistent": None, "closed_manifold": None, "orientation_outward": None,
    }
    if not isinstance(vertices, (list, tuple)) or not isinstance(triangles, (list, tuple)):
        findings.append({"code": "vertices_and_triangles_must_be_arrays"})
        return _result(findings, counts, checks, [], complete=True)
    if len(vertices) > MAX_VERTICES or len(triangles) > MAX_TRIANGLES:
        findings.append({"code": "mesh_limit_exceeded", "max_vertices": MAX_VERTICES,
                         "max_triangles": MAX_TRIANGLES})
        return _result(findings, counts, checks, [], complete=False)
    if not vertices or not triangles:
        findings.append({"code": "nonempty_vertices_and_triangles_required"})
        return _result(findings, counts, checks, [], complete=True)

    points: list[list[float]] = []
    finite = True
    for index, vertex in enumerate(vertices):
        if not isinstance(vertex, (list, tuple)) or len(vertex) != 3:
            findings.append({"code": "vertex_must_have_three_coordinates", "vertex": index})
            finite = False
            continue
        point = [_number(value) for value in vertex]
        if any(value is None for value in point):
            findings.append({"code": "vertex_coordinate_not_finite_number", "vertex": index})
            finite = False
            continue
        points.append([float(value) for value in point])
    if not finite or len(points) != len(vertices):
        checks["finite_coordinates"] = False
        return _result(findings, counts, checks, [], complete=True)
    checks["finite_coordinates"] = True

    minima = [min(point[axis] for point in points) for axis in range(3)]
    maxima = [max(point[axis] for point in points) for axis in range(3)]
    extents = [maxima[axis] - minima[axis] for axis in range(3)]
    scale = math.hypot(*extents)
    if not math.isfinite(scale):
        findings.append({"code": "coordinate_extent_overflow"})
        return _result(findings, counts, checks, [], complete=True)
    if scale == 0.0:
        findings.append({"code": "zero_mesh_extent"})
        return _result(findings, counts, checks, [], complete=True)
    origin = points[0]
    scaled = [[(point[axis] - origin[axis]) / scale for axis in range(3)] for point in points]
    if not all(math.isfinite(value) for point in scaled for value in point):
        findings.append({"code": "coordinate_difference_overflow"})
        return _result(findings, counts, checks, [], complete=True)

    parsed: list[tuple[int, int, int]] = []
    valid_indices = True
    degenerate = False
    duplicate_faces = False
    seen_faces: set[tuple[int, int, int]] = set()
    for face_index, face in enumerate(triangles):
        if not isinstance(face, (list, tuple)) or len(face) != 3 or any(
                isinstance(value, bool) or not isinstance(value, int) for value in face):
            findings.append({"code": "triangle_must_have_three_integer_indices", "triangle": face_index})
            valid_indices = False
            continue
        tri = tuple(face)
        if any(index < 0 or index >= len(points) for index in tri):
            findings.append({"code": "triangle_index_out_of_range", "triangle": face_index})
            valid_indices = False
            continue
        if len(set(tri)) != 3:
            findings.append({"code": "triangle_repeats_vertex_index", "triangle": face_index})
            degenerate = True
            continue
        key = tuple(sorted(tri))
        if key in seen_faces:
            findings.append({"code": "duplicate_triangle", "triangle": face_index})
            duplicate_faces = True
        seen_faces.add(key)
        a, b, c = (scaled[index] for index in tri)
        u = [b[axis] - a[axis] for axis in range(3)]
        v = [c[axis] - a[axis] for axis in range(3)]
        cross = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
                 u[0] * v[1] - u[1] * v[0]]
        area2 = math.hypot(*cross)
        if not math.isfinite(area2) or area2 <= _RELATIVE_AREA_TOLERANCE:
            findings.append({"code": "degenerate_or_near_degenerate_triangle", "triangle": face_index})
            degenerate = True
            continue
        parsed.append(tri)

    checks["valid_indices"] = valid_indices
    counts["valid_triangles"] = len(parsed)
    checks["nondegenerate_triangles"] = not degenerate
    checks["duplicate_faces"] = not duplicate_faces
    if not valid_indices or degenerate or not parsed:
        if not parsed:
            findings.append({"code": "no_valid_triangles"})
        return _result(findings, counts, checks, [], complete=True)

    edge_uses: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    vertex_links: dict[int, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    adjacency: list[set[int]] = [set() for _ in parsed]
    for face_index, tri in enumerate(parsed):
        for left, right in zip(tri, tri[1:] + tri[:1]):
            key = (left, right) if left < right else (right, left)
            edge_uses[key].append((face_index, 1 if left < right else -1))
        for center, left, right in ((tri[0], tri[1], tri[2]), (tri[1], tri[2], tri[0]), (tri[2], tri[0], tri[1])):
            vertex_links[center][left].append(right)
            vertex_links[center][right].append(left)

    boundary_edges = sum(len(uses) == 1 for uses in edge_uses.values())
    overused_edges = sum(len(uses) > 2 for uses in edge_uses.values())
    inconsistent_edges = sum(len(uses) == 2 and uses[0][1] == uses[1][1] for uses in edge_uses.values())
    for uses in edge_uses.values():
        if len(uses) > 1:
            face_ids = [face for face, _ in uses]
            for face in face_ids[1:]:
                adjacency[face].add(face_ids[0])
                adjacency[face_ids[0]].add(face)

    edge_manifold = overused_edges == 0
    winding_consistent = inconsistent_edges == 0
    vertex_manifold = True
    for center, link in vertex_links.items():
        unseen = set(link)
        stack = [next(iter(unseen))] if unseen else []
        while stack:
            node = stack.pop()
            if node not in unseen:
                continue
            unseen.remove(node)
            stack.extend(neighbor for neighbor in link[node] if neighbor in unseen)
        connected = not unseen
        degrees = [len(neighbors) for neighbors in link.values()]
        cycle = bool(degrees) and all(degree == 2 for degree in degrees)
        path = degrees.count(1) == 2 and all(degree in {1, 2} for degree in degrees)
        if not connected or not (cycle or path):
            vertex_manifold = False
            findings.append({"code": "nonmanifold_vertex_link", "vertex": center})
    checks["edge_manifold"] = edge_manifold
    checks["vertex_manifold"] = vertex_manifold
    checks["winding_consistent"] = winding_consistent
    checks["closed_manifold"] = edge_manifold and vertex_manifold and boundary_edges == 0 and not duplicate_faces
    if boundary_edges:
        findings.append({"code": "boundary_edges_present", "count": boundary_edges})
    if overused_edges:
        findings.append({"code": "nonmanifold_edges_present", "count": overused_edges})
    if inconsistent_edges:
        findings.append({"code": "inconsistent_shared_edge_winding", "count": inconsistent_edges})

    components: list[dict[str, Any]] = []
    unseen_faces = set(range(len(parsed)))
    while unseen_faces:
        seed = min(unseen_faces)
        unseen_faces.remove(seed)
        component_faces: list[int] = []
        queue = deque([seed])
        while queue:
            current = queue.popleft()
            component_faces.append(current)
            for neighbor in adjacency[current]:
                if neighbor in unseen_faces:
                    unseen_faces.remove(neighbor)
                    queue.append(neighbor)
        component_set = set(component_faces)
        component_edges = [uses for uses in edge_uses.values() if any(face in component_set for face, _ in uses)]
        component_closed = all(len(uses) == 2 and all(face in component_set for face, _ in uses)
                               for uses in component_edges)
        component_winding = all(not (len(uses) == 2 and uses[0][1] == uses[1][1]) for uses in component_edges)
        component_vertices = {index for face in component_faces for index in parsed[face]}
        normalized_volume = 0.0
        anchor = scaled[min(component_vertices)]
        for face_index in component_faces:
            a, b, c = (scaled[index] for index in parsed[face_index])
            av = [a[axis] - anchor[axis] for axis in range(3)]
            bv = [b[axis] - anchor[axis] for axis in range(3)]
            cv = [c[axis] - anchor[axis] for axis in range(3)]
            cross = [bv[1] * cv[2] - bv[2] * cv[1], bv[2] * cv[0] - bv[0] * cv[2],
                     bv[0] * cv[1] - bv[1] * cv[0]]
            normalized_volume += math.fsum(av[axis] * cross[axis] for axis in range(3)) / 6.0
        signed_volume = None
        volume_available = False
        orientation = "unknown"
        if component_closed and component_winding and checks["vertex_manifold"]:
            if math.isfinite(normalized_volume) and abs(normalized_volume) > _RELATIVE_VOLUME_TOLERANCE:
                orientation = "outward" if normalized_volume > 0 else "inward"
                # Combine mantissas before restoring the scale.  Computing
                # scale**3 first can overflow even when the final volume is
                # representable (for example, a unit tetrahedron scaled by
                # 1e103 has volume about 1.67e308).
                volume_mantissa, volume_exponent = math.frexp(normalized_volume)
                scale_mantissa, scale_exponent = math.frexp(scale)
                try:
                    candidate_volume = math.ldexp(
                        volume_mantissa * scale_mantissa * scale_mantissa * scale_mantissa,
                        volume_exponent + 3 * scale_exponent,
                    )
                except OverflowError:
                    candidate_volume = math.inf
                if math.isfinite(candidate_volume) and candidate_volume != 0.0:
                    signed_volume = candidate_volume
                    volume_available = True
            else:
                orientation = "indeterminate"
                findings.append({"code": "component_volume_indeterminate", "component": len(components)})
        if orientation == "inward":
            findings.append({"code": "component_winding_is_inward", "component": len(components)})
        components.append({"component": len(components), "triangle_count": len(component_faces),
                           "vertex_count": len(component_vertices), "closed": component_closed,
                           "winding_consistent": component_winding, "signed_volume": signed_volume,
                           "volume_available": volume_available,
                           "orientation": orientation})
    checks["component_count"] = len(components)
    checks["orientation_outward"] = bool(components) and all(component["orientation"] == "outward"
                                                               for component in components)
    return _result(findings, counts, checks, components, complete=True)
