"""Bounded local meshes derived from a small set of native primitive dimensions.

This module deliberately does not call E3D or claim to reproduce native
tessellation, negative cuts, or rendered geometry.  It accepts an already-read
primitive geometry mapping and only derives positive BOX, CYLI, and CONE meshes.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


_COORDINATE_FRAME = "primitive local coordinates; origin at native POS; +Z follows the primitive local axis"
_WORLD_COORDINATE_FRAME = "WORLD coordinates; millimetres"
_BASIS_TOLERANCE = 1e-5
_MESH_SOURCE = "derived_from_e3d_primitive_parameters"
_ADAPTIVE_MAX_VERTICES = 10_000
_ADAPTIVE_MAX_TRIANGLES = 20_000
_MAX_TESSELLATION_RADIAL_SEGMENTS = 256
_SUPPORTED = {
    "BOX": ("XLEN", "YLEN", "ZLEN"),
    "CYLI": ("DIAM", "HEIG"),
    "CONE": ("DTOP", "DBOT", "HEIG"),
    "DISH": ("DIAM", "HEIG", "RADI"),
}


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    """Copy small metadata values into a JSON-safe, bounded representation."""
    if value is None or isinstance(value, (str, bool, int)):
        return value if not isinstance(value, str) else value[:512]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if depth >= 4:
        return None
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 32:
                break
            if isinstance(key, str):
                result[key[:128]] = _json_safe(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, depth=depth + 1) for item in value[:32]]
    return None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _world_transform(frame: Any) -> tuple[list[float], list[list[float]]] | None:
    if not isinstance(frame, Mapping):
        return None
    world = frame.get("world")
    if not isinstance(world, Mapping):
        return None
    position = world.get("position")
    orientation = world.get("orientation")
    if not isinstance(position, (list, tuple)) or len(position) != 3:
        return None
    origin = [_finite_number(value) for value in position]
    if any(value is None for value in origin) or not isinstance(orientation, Mapping):
        return None
    axes = []
    for key in ("x_axis", "y_axis", "z_axis"):
        raw_axis = orientation.get(key)
        if not isinstance(raw_axis, (list, tuple)) or len(raw_axis) != 3:
            return None
        axis = [_finite_number(value) for value in raw_axis]
        if any(value is None for value in axis):
            return None
        axes.append([float(value) for value in axis])
    x, y, z = axes
    try:
        lengths = [math.hypot(*axis) for axis in axes]
        dots = [math.fsum(a[i] * b[i] for i in range(3)) for a, b in ((x, y), (x, z), (y, z))]
        cross_xy = (x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0])
        handedness = math.fsum(cross_xy[i] * z[i] for i in range(3))
    except (ArithmeticError, OverflowError, ValueError):
        return None
    if (not all(math.isfinite(value) for value in (*lengths, *dots, handedness)) or
            any(abs(length - 1.0) > _BASIS_TOLERANCE for length in lengths) or
            any(abs(dot) > _BASIS_TOLERANCE for dot in dots) or
            handedness < 1.0 - _BASIS_TOLERANCE):
        return None
    return [float(value) for value in origin], axes


def _transform_world(vertices: list[list[float]], normals: list[list[float]],
                     origin: list[float], axes: list[list[float]]) -> bool:
    for vertex in vertices:
        local = vertex[:]
        for coordinate in range(3):
            vertex[coordinate] = math.fsum((origin[coordinate], *(axes[axis][coordinate] * local[axis]
                                                                    for axis in range(3))))
    for normal in normals:
        local = normal[:]
        transformed = [math.fsum(axes[axis][coordinate] * local[axis] for axis in range(3))
                       for coordinate in range(3)]
        length = math.hypot(*transformed)
        if not math.isfinite(length) or length == 0.0:
            return False
        normal[:] = [component / length for component in transformed]
    return all(math.isfinite(value) for vertex in vertices for value in vertex) and \
        all(math.isfinite(value) for normal in normals for value in normal)


def _empty_result(geometry: Mapping[str, Any], *, shape: Any, dimensions: dict[str, Any],
                  errors: list[str], radial_segments: Any, coordinate_space: str) -> dict[str, Any]:
    frame = geometry.get("frame", geometry.get("coordinate_frame"))
    element = geometry.get("element")
    element_refno = element.get("refno") if isinstance(element, Mapping) else None
    refno = geometry.get("refno", element_refno)
    safe_shape = shape if isinstance(shape, str) else None
    conventions = {
        "BOX": "centered at native POS; half XLEN/YLEN/ZLEN along the local axes",
        "CYLI": "centered at native POS along local Z; radius DIAM/2",
        "CONE": "centered at native POS along local Z; DBOT at -HEIG/2 and DTOP at +HEIG/2",
        "DISH": "base circle centered at native POS in local z=0; spherical cap extends along local +Z to HEIG",
    }
    return {
        "success": False,
        "available": False,
        "complete": False,
        "errors": errors,
        "shape": safe_shape,
        "dimensions": dimensions,
        "refno": _json_safe(refno),
        "revision": _json_safe(geometry.get("revision")),
        "frame": _json_safe(frame),
        "coordinate_frame": _WORLD_COORDINATE_FRAME if coordinate_space == "WORLD" else _COORDINATE_FRAME,
        "parameter_convention": conventions.get(safe_shape, "unknown; no shape convention applied"),
        "parameter_convention_evidence": (
            "Installed CSG evidence identifies RADI=0 as a spherical dish; PML and live bounds evidence place its base circle at local z=0 centered on POS. This mesh is a mathematical parameter-derived approximation, not native tessellation."
            if safe_shape == "DISH" else
            "BOX and CYLI positioning were compared with existing native bounds; CONE end-to-local-Z orientation follows the parameter convention and was not independently verified against native tessellation."
            if safe_shape == "CONE" else
            "Derived from the supported native shape dimensions and element-local placement; not an E3D tessellation contract."
        ),
        "surface_approximation": "planar BOX faces" if safe_shape == "BOX" else (
            "spherical cap with planar triangular facets" if safe_shape == "DISH" else
            "inscribed radial polygon with planar facets" if safe_shape in {"CYLI", "CONE"} else "not generated"
        ),
        "mesh_source": _MESH_SOURCE,
        "native_tessellation": False,
        "cuts_applied": False,
        "closed_manifold": False,
        "radial_segments": radial_segments if isinstance(radial_segments, int) and not isinstance(radial_segments, bool) else None,
        "vertices": [],
        "triangles": [],
        "triangle_normals": [],
        "counts": {"vertices": 0, "triangles": 0},
    }


def _unit_normal(a: list[float], b: list[float], c: list[float]) -> tuple[float, float, float] | None:
    """Return a finite unit normal, scaling edge vectors before the cross product."""
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    u_scale = max(abs(component) for component in u)
    v_scale = max(abs(component) for component in v)
    if not math.isfinite(u_scale) or not math.isfinite(v_scale) or u_scale == 0.0 or v_scale == 0.0:
        return None
    u = [component / u_scale for component in u]
    v = [component / v_scale for component in v]
    cross = (u[1] * v[2] - u[2] * v[1],
             u[2] * v[0] - u[0] * v[2],
             u[0] * v[1] - u[1] * v[0])
    length = math.hypot(*cross)
    if not math.isfinite(length) or length == 0.0:
        return None
    return cross[0] / length, cross[1] / length, cross[2] / length


def _oriented_triangle(vertices: list[list[float]], indices: tuple[int, int, int],
                       expected: tuple[float, float, float]) -> tuple[list[int], list[float]] | None:
    a, b, c = (vertices[index] for index in indices)
    normal = _unit_normal(a, b, c)
    if normal is None:
        return None
    scale = max(abs(component) for component in expected)
    if not math.isfinite(scale) or scale == 0.0:
        return None
    direction = tuple(component / scale for component in expected)
    alignment = sum(normal[index] * direction[index] for index in range(3))
    if not math.isfinite(alignment) or alignment == 0.0:
        return None
    if alignment < 0.0:
        return [indices[0], indices[2], indices[1]], [-component for component in normal]
    return list(indices), list(normal)


def _append_triangle(vertices: list[list[float]], triangles: list[list[int]], normals: list[list[float]],
                     indices: tuple[int, int, int], expected: tuple[float, float, float]) -> None:
    oriented = _oriented_triangle(vertices, indices, expected)
    if oriented is None:
        raise ValueError("derived triangle is degenerate or has an indeterminate outward direction")
    triangle, normal = oriented
    triangles.append(triangle)
    normals.append(normal)


def _centroid(vertices: list[list[float]], indices: tuple[int, int, int]) -> tuple[float, float, float]:
    # Divide each term first to avoid overflowing while averaging finite coordinates.
    return tuple(sum(vertices[index][axis] / 3.0 for index in indices) for axis in range(3))  # type: ignore[return-value]


def _closed_edge_incidence(triangles: list[list[int]]) -> bool:
    incidence: dict[tuple[int, int], tuple[int, int]] = {}
    for triangle in triangles:
        if len(triangle) != 3 or len(set(triangle)) != 3:
            return False
        for left, right in ((triangle[0], triangle[1]), (triangle[1], triangle[2]), (triangle[2], triangle[0])):
            edge = (left, right) if left < right else (right, left)
            count, orientation_sum = incidence.get(edge, (0, 0))
            incidence[edge] = count + 1, orientation_sum + (1 if left < right else -1)
    return bool(incidence) and all(count == 2 and orientation_sum == 0
                                    for count, orientation_sum in incidence.values())


def _box_mesh(xlen: float, ylen: float, zlen: float) -> tuple[list[list[float]], list[list[int]], list[list[float]]]:
    hx, hy, hz = xlen / 2.0, ylen / 2.0, zlen / 2.0
    vertices = [
        [-hx, -hy, -hz], [hx, -hy, -hz], [hx, hy, -hz], [-hx, hy, -hz],
        [-hx, -hy, hz], [hx, -hy, hz], [hx, hy, hz], [-hx, hy, hz],
    ]
    faces = (
        ((0, 3, 7, 4), (-1.0, 0.0, 0.0)),
        ((1, 5, 6, 2), (1.0, 0.0, 0.0)),
        ((0, 4, 5, 1), (0.0, -1.0, 0.0)),
        ((3, 2, 6, 7), (0.0, 1.0, 0.0)),
        ((0, 1, 2, 3), (0.0, 0.0, -1.0)),
        ((4, 7, 6, 5), (0.0, 0.0, 1.0)),
    )
    triangles: list[list[int]] = []
    normals: list[list[float]] = []
    for loop, direction in faces:
        _append_triangle(vertices, triangles, normals, (loop[0], loop[1], loop[2]), direction)
        _append_triangle(vertices, triangles, normals, (loop[0], loop[2], loop[3]), direction)
    return vertices, triangles, normals


def _circle_ring(vertices: list[list[float]], radius: float, z: float, segments: int) -> list[int]:
    start = len(vertices)
    for index in range(segments):
        angle = (2.0 * math.pi * index) / segments
        vertices.append([radius * math.cos(angle), radius * math.sin(angle), z])
    return list(range(start, start + segments))


def _cap(vertices: list[list[float]], triangles: list[list[int]], normals: list[list[float]],
         ring: list[int], z: float, direction: float) -> None:
    center_index = len(vertices)
    vertices.append([0.0, 0.0, z])
    expected = (0.0, 0.0, direction)
    for index, current in enumerate(ring):
        following = ring[(index + 1) % len(ring)]
        _append_triangle(vertices, triangles, normals, (center_index, current, following), expected)


def _side_expected(vertices: list[list[float]], indices: tuple[int, int, int]) -> tuple[float, float, float]:
    center = _centroid(vertices, indices)
    return center[0], center[1], 0.0


def _round_mesh(bottom_diameter: float, top_diameter: float, height: float,
                segments: int) -> tuple[list[list[float]], list[list[int]], list[list[float]]]:
    vertices: list[list[float]] = []
    triangles: list[list[int]] = []
    normals: list[list[float]] = []
    z_bottom, z_top = -height / 2.0, height / 2.0
    radius_bottom, radius_top = bottom_diameter / 2.0, top_diameter / 2.0
    bottom = _circle_ring(vertices, radius_bottom, z_bottom, segments) if radius_bottom else None
    top = _circle_ring(vertices, radius_top, z_top, segments) if radius_top else None

    if bottom is not None and top is not None:
        for index in range(segments):
            following = (index + 1) % segments
            for triangle in ((bottom[index], bottom[following], top[following]),
                             (bottom[index], top[following], top[index])):
                _append_triangle(vertices, triangles, normals, triangle, _side_expected(vertices, triangle))
        _cap(vertices, triangles, normals, bottom, z_bottom, -1.0)
        _cap(vertices, triangles, normals, top, z_top, 1.0)
    elif bottom is not None:
        apex = len(vertices)
        vertices.append([0.0, 0.0, z_top])
        for index in range(segments):
            triangle = (bottom[index], bottom[(index + 1) % segments], apex)
            _append_triangle(vertices, triangles, normals, triangle, _side_expected(vertices, triangle))
        _cap(vertices, triangles, normals, bottom, z_bottom, -1.0)
    elif top is not None:
        apex = len(vertices)
        vertices.append([0.0, 0.0, z_bottom])
        for index in range(segments):
            triangle = (apex, top[(index + 1) % segments], top[index])
            _append_triangle(vertices, triangles, normals, triangle, _side_expected(vertices, triangle))
        _cap(vertices, triangles, normals, top, z_top, 1.0)
    else:
        raise ValueError("both cone end diameters are zero")
    return vertices, triangles, normals


def _spherical_dish_parameters(diameter: float, height: float) -> tuple[float, float, float]:
    base_radius = diameter / 2.0
    sphere_radius = 0.5 * (base_radius * (base_radius / height) + height)
    center_z = height - sphere_radius
    cosine = (sphere_radius - height) / sphere_radius
    cosine = min(1.0, max(-1.0, cosine))
    base_angle = math.acos(cosine)
    if not all(math.isfinite(value) for value in (sphere_radius, center_z, base_angle)) or \
            sphere_radius <= 0.0 or base_angle <= 0.0 or base_angle >= math.pi:
        raise ValueError("invalid spherical dish parameters")
    return sphere_radius, center_z, base_angle


def _circular_chord_error(radius: float, segments: int) -> float:
    # 1-cos(pi/n) loses small errors to cancellation; this identity does not.
    return radius * (2.0 * math.sin(math.pi / (2.0 * segments)) ** 2)


def _dish_facet_error(sphere_radius: float, base_angle: float,
                      radial_segments: int, latitude_segments: int) -> float:
    """Bound the inward distance of every triangular spherical-cap facet.

    A latitude strip's four corners are coplanar. In an azimuth-midpoint
    section, its face normal is proportional to (sin(m), cos(pi/n)*cos(m)),
    where m is the strip's middle polar angle. The closest facet plane to the
    sphere centre is the strip nearest the equator. Its plane distance bounds
    all points in both triangles, including diagonal interiors, not just edges.
    """
    step = base_angle / latitude_segments
    equator = math.pi / (2.0 * step) - 0.5
    candidates = {0, latitude_segments - 1,
                  max(0, min(latitude_segments - 1, math.floor(equator))),
                  max(0, min(latitude_segments - 1, math.ceil(equator)))}
    sine_middle = max(math.sin((index + 0.5) * step) ** 2 for index in candidates)
    cosine_half_longitude = math.cos(math.pi / radial_segments)
    sine_half_longitude_sq = math.sin(math.pi / radial_segments) ** 2
    denominator_sq = cosine_half_longitude ** 2 + sine_half_longitude_sq * sine_middle
    denominator = math.sqrt(denominator_sq)
    plane_numerator = cosine_half_longitude * math.cos(step / 2.0)
    # Stable form of R*(1-plane_numerator/denominator).
    difference = (sine_half_longitude_sq * sine_middle
                  + cosine_half_longitude ** 2 * math.sin(step / 2.0) ** 2)
    return sphere_radius * (difference / (denominator * (denominator + plane_numerator)))


def plan_primitive_tessellation(shape: str, dimensions: Mapping[str, Any], radial_segments: int = 48,
                                 chord_tolerance_mm: float | None = None, *,
                                 max_radial_segments: int = 128) -> dict[str, Any]:
    """Plan bounded parameter-surface sampling without changing legacy defaults.

    An explicit positive chord tolerance refines circular profiles according
    to their actual radius. Spherical DISH facets additionally need independent
    polar sampling; testing circular edges alone misses triangle-interior
    error. The bound is local model millimetres before Boolean operations and
    excludes numerical placement/Boolean error and native E3D tessellation.
    This helper is shared with callers that derive CSG operands independently.
    """
    if (type(radial_segments) is not int or type(max_radial_segments) is not int
            or not 12 <= radial_segments <= max_radial_segments <= _MAX_TESSELLATION_RADIAL_SEGMENTS):
        raise ValueError("radial_segments_out_of_range")
    if not isinstance(shape, str) or shape not in _SUPPORTED or not isinstance(dimensions, Mapping):
        raise ValueError("unsupported_tessellation_shape")
    values = {key: _finite_number(dimensions.get(key)) for key in _SUPPORTED[shape]}
    if any(value is None for value in values.values()):
        raise ValueError("invalid_tessellation_dimensions")
    if shape == "CONE":
        valid = values["HEIG"] > 0 and min(values["DTOP"], values["DBOT"]) >= 0 and max(values["DTOP"], values["DBOT"]) > 0
    elif shape == "DISH":
        valid = values["DIAM"] > 0 and values["HEIG"] > 0 and values["RADI"] == 0
    else:
        valid = all(value > 0 for value in values.values())
    if not valid:
        raise ValueError("invalid_tessellation_dimensions")
    tolerance = _finite_number(chord_tolerance_mm) if chord_tolerance_mm is not None else None
    if chord_tolerance_mm is not None and (tolerance is None or tolerance <= 0):
        raise ValueError("invalid_chord_tolerance_mm")
    plan = {"mode": "fixed_radial_segments" if tolerance is None else "chord_tolerance",
            "requested_radial_segments": radial_segments, "radial_segments": radial_segments,
            "latitude_segments": radial_segments if shape == "DISH" else None,
            "chord_tolerance_mm": tolerance,
            "error_bound_scope": "Analytic parameter surface before placement and Boolean operations; not native tessellation or engineering accuracy."}
    if shape == "BOX":
        plan.update(max_surface_deviation_mm=0.0, counts={"vertices": 8, "triangles": 12})
        return plan
    if shape in {"CYLI", "CONE"}:
        radius = max(values[key] / 2.0 for key in (("DIAM",) if shape == "CYLI" else ("DTOP", "DBOT")))
        # Adaptive sampling includes all four local cardinal meridians, so
        # tighter curves retain their actual parameter diameter and BOX-style
        # drawing extents instead of losing a quadrant on an odd polygon.
        segments = radial_segments if tolerance is None else 4 * math.ceil(radial_segments / 4)
        if segments > max_radial_segments:
            raise ValueError("chord_tolerance_exceeds_tessellation_budget")
        while tolerance is not None and _circular_chord_error(radius, segments) > tolerance:
            segments += 4
            if segments > max_radial_segments:
                raise ValueError("chord_tolerance_exceeds_tessellation_budget")
        full_rings = shape == "CYLI" or min(values["DTOP"], values["DBOT"]) > 0
        plan.update(radial_segments=segments, max_surface_deviation_mm=_circular_chord_error(radius, segments),
                    counts={"vertices": (2 * segments + 2) if full_rings else segments + 2,
                            "triangles": (4 * segments) if full_rings else 2 * segments})
        if tolerance is not None:
            plan["cardinal_meridians_preserved"] = True
        return plan
    sphere_radius, _, base_angle = _spherical_dish_parameters(values["DIAM"], values["HEIG"])
    selected = None
    for segments in range(radial_segments, (max_radial_segments if tolerance is not None else radial_segments) + 1):
        if tolerance is not None and segments % 4:
            continue
        base_error = _circular_chord_error(values["DIAM"] / 2.0, segments)
        if tolerance is not None and base_error > tolerance:
            continue
        max_latitudes = min((_ADAPTIVE_MAX_VERTICES - 2) // segments,
                            _ADAPTIVE_MAX_TRIANGLES // (2 * segments)) if tolerance is not None else segments
        latitudes = segments if tolerance is None else 1
        while latitudes <= max_latitudes:
            error = max(base_error, _dish_facet_error(sphere_radius, base_angle, segments, latitudes))
            if tolerance is None or error <= tolerance:
                candidate = (segments * latitudes, segments, latitudes, error)
                if selected is None or candidate[:3] < selected[:3]:
                    selected = candidate
                break
            latitudes += 1
    if selected is None:
        raise ValueError("chord_tolerance_exceeds_tessellation_budget")
    _, segments, latitudes, error = selected
    plan.update(radial_segments=segments, latitude_segments=latitudes, max_surface_deviation_mm=error,
                counts={"vertices": segments * latitudes + 2, "triangles": 2 * segments * latitudes})
    return plan


def _spherical_dish_mesh(diameter: float, height: float,
                         segments: int, *, latitude_segments: int | None = None
                         ) -> tuple[list[list[float]], list[list[int]], list[list[float]]]:
    """Derive a closed spherical cap with its circular base in local z=0."""
    sphere_radius, center_z, base_angle = _spherical_dish_parameters(diameter, height)

    vertices: list[list[float]] = [[0.0, 0.0, height]]
    triangles: list[list[int]] = []
    normals: list[list[float]] = []
    latitude_segments = segments if latitude_segments is None else latitude_segments
    if type(latitude_segments) is not int or latitude_segments < 1:
        raise ValueError("invalid_dish_latitude_segments")
    rings: list[list[int]] = []
    for latitude in range(1, latitude_segments + 1):
        theta = base_angle * latitude / latitude_segments
        ring: list[int] = []
        for longitude in range(segments):
            angle = (2.0 * math.pi * longitude) / segments
            ring.append(len(vertices))
            vertices.append([
                sphere_radius * math.sin(theta) * math.cos(angle),
                sphere_radius * math.sin(theta) * math.sin(angle),
                center_z + sphere_radius * math.cos(theta),
            ])
        rings.append(ring)

    def sphere_outward(indices: tuple[int, int, int]) -> tuple[float, float, float]:
        center = _centroid(vertices, indices)
        return center[0], center[1], center[2] - center_z

    first = rings[0]
    for longitude in range(segments):
        following = (longitude + 1) % segments
        triangle = (0, first[longitude], first[following])
        _append_triangle(vertices, triangles, normals, triangle, sphere_outward(triangle))

    for lower, upper in zip(rings, rings[1:]):
        for longitude in range(segments):
            following = (longitude + 1) % segments
            for triangle in ((lower[longitude], lower[following], upper[following]),
                             (lower[longitude], upper[following], upper[longitude])):
                _append_triangle(vertices, triangles, normals, triangle, sphere_outward(triangle))

    base_ring = rings[-1]
    base_center = len(vertices)
    vertices.append([0.0, 0.0, 0.0])
    for longitude in range(segments):
        following = (longitude + 1) % segments
        _append_triangle(vertices, triangles, normals,
                         (base_center, base_ring[following], base_ring[longitude]),
                         (0.0, 0.0, -1.0))
    return vertices, triangles, normals


def derive_primitive_mesh(native_geometry: Mapping[str, Any], radial_segments: int = 48,
                          coordinate_space: str = "primitive_local", *,
                          chord_tolerance_mm: float | None = None) -> dict[str, Any]:
    """Derive a bounded, closed triangle mesh for a supported positive primitive.

    Input fields are the native geometry readback: ``success``, ``complete``,
    ``read_errors``, ``type`` (or ``shape``), ``dimensions``, ``negative``,
    ``negative_children``, and optional ``children``, ``refno``, ``revision``,
    and ``frame``.  Invalid or incomplete input returns an empty mesh with
    explanatory error codes; no partial mesh is returned. Optional positive
    ``chord_tolerance_mm`` refines the analytic parameter surface under explicit
    resource bounds. Omitting it preserves the original mesh and result fields.
    """
    if not isinstance(coordinate_space, str) or coordinate_space not in {"primitive_local", "WORLD"}:
        coordinate_space = "primitive_local"
        error = "unsupported_coordinate_space"
    else:
        error = None
    if not isinstance(native_geometry, Mapping):
        return _empty_result({}, shape=None, dimensions={}, errors=["invalid_native_geometry"],
                             radial_segments=radial_segments, coordinate_space=coordinate_space)

    type_value = native_geometry.get("type")
    shape_value = native_geometry.get("shape")
    candidates = [value for value in (type_value, shape_value) if value is not None]
    shape: Any = candidates[0] if candidates else None
    errors: list[str] = []
    if error:
        errors.append(error)
    if not candidates or any(value != shape for value in candidates):
        errors.append("unsupported_or_conflicting_shape")
    elif not isinstance(shape, str) or shape not in _SUPPORTED:
        errors.append("unsupported_shape")
    element = native_geometry.get("element")
    element_type = element.get("type") if isinstance(element, Mapping) else None
    if element_type is not None and shape is not None and element_type != shape:
        errors.append("element_type_shape_mismatch")

    keys = _SUPPORTED.get(shape, ()) if isinstance(shape, str) else ()
    raw_dimensions = native_geometry.get("dimensions")
    if not isinstance(raw_dimensions, Mapping):
        raw_dimensions = {}
        errors.append("invalid_dimensions")
    dimensions: dict[str, Any] = {}
    for key in keys:
        number = _finite_number(raw_dimensions.get(key))
        dimensions[key] = number
        if number is None:
            errors.append(f"invalid_dimension_{key.lower()}")

    if native_geometry.get("success") is not True:
        errors.append("native_read_not_successful")
    if native_geometry.get("complete") is not True:
        errors.append("native_read_incomplete")
    read_errors = native_geometry.get("read_errors")
    if not isinstance(read_errors, (list, tuple, dict)) or len(read_errors) != 0:
        errors.append("native_read_errors_present_or_unreported")
    if native_geometry.get("negative") is not False:
        errors.append("negative_primitive_rejected")
    negative_children = native_geometry.get("negative_children")
    if not isinstance(negative_children, (list, tuple)) or len(negative_children) != 0:
        errors.append("negative_children_present_or_unreported")
    children = native_geometry.get("children")
    if children is not None and (not isinstance(children, (list, tuple)) or len(children) != 0):
        errors.append("children_present")
    if isinstance(radial_segments, bool) or not isinstance(radial_segments, int) or not 12 <= radial_segments <= 128:
        errors.append("radial_segments_out_of_range")
    transform = _world_transform(native_geometry.get("frame")) if coordinate_space == "WORLD" else None
    if coordinate_space == "WORLD" and transform is None:
        errors.append("invalid_or_unavailable_world_frame")

    if not errors:
        if shape == "BOX" and any(dimensions[key] <= 0.0 for key in keys):
            errors.append("nonpositive_box_dimension")
        elif shape == "CYLI" and any(dimensions[key] <= 0.0 for key in keys):
            errors.append("nonpositive_cylinder_dimension")
        elif shape == "CONE":
            if dimensions["HEIG"] <= 0.0:
                errors.append("nonpositive_cone_height")
            if dimensions["DTOP"] < 0.0 or dimensions["DBOT"] < 0.0:
                errors.append("negative_cone_diameter")
            if dimensions["DTOP"] == 0.0 and dimensions["DBOT"] == 0.0:
                errors.append("both_cone_ends_are_apices")
        elif shape == "DISH":
            if dimensions["DIAM"] <= 0.0 or dimensions["HEIG"] <= 0.0:
                errors.append("nonpositive_dish_dimension")
            if dimensions["RADI"] < 0.0:
                errors.append("negative_dish_radius")
            elif dimensions["RADI"] != 0.0:
                errors.append("nonzero_dish_radius_unsupported")

    result = _empty_result(native_geometry, shape=shape, dimensions=dimensions,
                           errors=errors, radial_segments=radial_segments, coordinate_space=coordinate_space)
    if errors:
        return result

    latitude_segments = None
    if chord_tolerance_mm is not None:
        try:
            plan = plan_primitive_tessellation(shape, dimensions, radial_segments, chord_tolerance_mm)
        except (ArithmeticError, OverflowError, ValueError) as exc:
            result["errors"] = [str(exc) if isinstance(exc, ValueError) else "mesh_derivation_failed"]
            return result
        radial_segments = plan["radial_segments"]
        latitude_segments = plan["latitude_segments"]
        result.update(radial_segments=radial_segments, tessellation=plan)

    try:
        if shape == "BOX":
            vertices, triangles, normals = _box_mesh(dimensions["XLEN"], dimensions["YLEN"], dimensions["ZLEN"])
        elif shape == "CYLI":
            diameter, height = dimensions["DIAM"], dimensions["HEIG"]
            vertices, triangles, normals = _round_mesh(diameter, diameter, height, radial_segments)
        elif shape == "CONE":
            vertices, triangles, normals = _round_mesh(
                dimensions["DBOT"], dimensions["DTOP"], dimensions["HEIG"], radial_segments,
            )
        else:  # DISH
            vertices, triangles, normals = _spherical_dish_mesh(
                dimensions["DIAM"], dimensions["HEIG"], radial_segments, latitude_segments=latitude_segments,
            )
    except (ArithmeticError, OverflowError, ValueError):
        result["errors"] = ["mesh_derivation_failed"]
        return result

    if not all(math.isfinite(value) for vertex in vertices for value in vertex) or \
            not all(math.isfinite(value) for normal in normals for value in normal):
        result["errors"] = ["mesh_derivation_non_finite"]
        return result
    if coordinate_space == "WORLD":
        try:
            transformed = _transform_world(vertices, normals, *transform)
        except (ArithmeticError, OverflowError, ValueError):
            transformed = False
        if not transformed:
            result["errors"] = ["world_transform_non_finite"]
            return result

    result.update({
        "success": True,
        "available": True,
        "complete": True,
        "errors": [],
        "closed_manifold": _closed_edge_incidence(triangles),
        "vertices": vertices,
        "triangles": triangles,
        "triangle_normals": normals,
        "counts": {"vertices": len(vertices), "triangles": len(triangles)},
    })
    if result["closed_manifold"] is not True:
        result.update({
            "success": False, "available": False, "complete": False,
            "errors": ["indexed_edge_incidence_not_closed"],
            "vertices": [], "triangles": [], "triangle_normals": [],
            "counts": {"vertices": 0, "triangles": 0},
        })
    return result
