"""Closed meshes from explicitly resolved analytic DISH profiles, without SDK calls.

Raw generated-CSGDish RData is deliberately not an input. On the installed
export path its third value was observed as 0/1, and cannot be interpreted as
a physical knuckle radius. The workflow resolves a narrowly supported Design
DISH source using the separately audited Core3D BasicDIS generation branch.

The positive-knuckle shallow profile follows libgm GM_EDish, not an ellipse:
knuckleRadiusToUse (RVA 0x31a10), radiusOfHub (0x31af0), and the actual hub /
knuckle vertex formulas in calcFacetsWithoutSurfaces (0x30c17 / 0x30fc7).
Native profile equations are documented here to distinguish this generator from SDK tessellation.
Database RADI and generated export markers are not accepted or converted here.
The caller must supply an explicit profile kind and effective physical radii.

Only a generated analytic operand boundary is derived. This is not native
tessellation, a Boolean result, a wall/bore surface, or proof of the caller's
native observation or coordinate-frame provenance.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_NATIVE_EPSILON = 1e-6
_NATIVE_RADIUS_LIMIT = 1e7
_MAX_RADIAL_SEGMENTS = 128
_MAX_LATITUDE_SEGMENTS = 128
_MAX_VERTICES = 10_000
_MAX_TRIANGLES = 20_000
_BASIS_TOLERANCE = 1e-8
_SOURCE = "derived_from_explicit_analytic_dish_profile_parameters"
_ERROR_SCOPE = (
    "Bound on the analytic generated operand versus its planar facets, including "
    "triangle interiors; excludes floating-point placement, Boolean operations, "
    "native tessellation, and engineering accuracy."
)


@dataclass(frozen=True)
class _Arc:
    name: str
    radius: float
    center_r: float
    center_z: float
    start: float
    end: float

    @property
    def sweep(self) -> float:
        return self.end - self.start


def _number(value: Any, error: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(error)
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(error) from None
    if not math.isfinite(result):
        raise ValueError(error)
    return result


def _parameters(profile: Any) -> tuple[dict[str, Any], list[_Arc]]:
    if not isinstance(profile, Mapping) or set(profile) != {
            "profile_kind", "base_radius_mm", "height_mm", "effective_knuckle_radius_mm"}:
        raise ValueError("explicit_analytic_dish_profile_required")
    kind = profile.get("profile_kind")
    if kind not in ("spherical_cap", "torispherical"):
        raise ValueError("unsupported_analytic_dish_profile_kind")
    a = _number(profile["base_radius_mm"], "invalid_analytic_dish_profile")
    h = _number(profile["height_mm"], "invalid_analytic_dish_profile")
    knuckle = _number(profile["effective_knuckle_radius_mm"], "invalid_analytic_dish_profile")
    if a <= _NATIVE_EPSILON or h <= _NATIVE_EPSILON or knuckle < 0:
        raise ValueError("degenerate_or_negative_generated_dish_parameters")
    if max(a, h) > _NATIVE_RADIUS_LIMIT:
        raise ValueError("generated_dish_exceeds_native_radius_limit")
    result = {
        "input_profile_kind": kind,
        "base_radius_mm": a,
        "height_mm": h,
        "effective_knuckle_radius_mm": knuckle,
        "hub_radius_mm": None,
        "joint_radius_mm": None,
        "joint_z_mm": None,
    }
    if kind == "spherical_cap":
        if knuckle != 0.0:
            raise ValueError("spherical_profile_requires_zero_knuckle")
        radius = 0.5 * (a * (a / h) + h)
        if not math.isfinite(radius) or radius > _NATIVE_RADIUS_LIMIT:
            raise ValueError("generated_dish_exceeds_native_radius_limit")
        angle = 2.0 * math.atan2(h, a)
        if not 0 < angle < math.pi:
            raise ValueError("invalid_generated_spherical_dish")
        cuts = [0.0, angle] if angle <= math.pi / 2 else [0.0, math.pi / 2, angle]
        arcs = [_Arc("spherical", radius, 0.0, h - radius, start, end)
                for start, end in zip(cuts, cuts[1:])]
        result.update(profile="spherical_cap", hub_radius_mm=radius,
                      maximum_radius_mm=a if h <= a else radius)
        return result, arcs
    if knuckle <= _NATIVE_EPSILON:
        raise ValueError("degenerate_explicit_knuckle_radius")
    if a == h:
        if knuckle != h:
            raise ValueError("hemisphere_requires_resolved_knuckle")
        result.update(profile="hemisphere", effective_knuckle_radius_mm=h,
                      hub_radius_mm=h, maximum_radius_mm=h)
        return result, [_Arc("hemisphere", h, 0.0, 0.0, 0.0, math.pi / 2)]
    # Native uses a tolerance for its hemisphere branch. Do not silently alter
    # one unequal dimension to emulate an incompletely established boundary.
    if abs(a - h) <= _NATIVE_EPSILON:
        raise ValueError("near_hemisphere_generated_dish_not_supported")
    if h > a:
        raise ValueError("deep_positive_knuckle_generated_dish_not_supported")
    limit = h / (1.0 + (a - h) / math.hypot(a, h))
    if knuckle > limit:
        raise ValueError("effective_knuckle_exceeds_native_shallow_limit")
    # Algebraically identical to (a*a+h*h-2*a*k)/(2*(h-k)),
    # without subtracting large nearly equal squares near the hemisphere.
    hub = 0.5 * ((a - knuckle) * ((a - knuckle) / (h - knuckle)) + h + knuckle)
    angle = math.atan2(a - knuckle, hub - h)
    if (not all(math.isfinite(value) for value in (knuckle, hub, angle))
            or not 0 < knuckle < h < hub <= _NATIVE_RADIUS_LIMIT
            or not 0 < angle < math.pi / 2):
        raise ValueError("invalid_generated_torispherical_dish")
    joint_r = hub * math.sin(angle)
    joint_z = h - hub + hub * math.cos(angle)
    result.update(profile="torispherical_shallow", effective_knuckle_radius_mm=knuckle,
                  hub_radius_mm=hub, joint_radius_mm=joint_r, joint_z_mm=joint_z,
                  joint_polar_angle_radians=angle, maximum_radius_mm=a)
    return result, [
        _Arc("hub", hub, 0.0, h - hub, 0.0, angle),
        _Arc("knuckle", knuckle, a - knuckle, 0.0, angle, math.pi / 2),
    ]


def _sag(radius: float, sweep: float, count: int) -> float:
    return radius * 2.0 * math.sin(sweep / (4.0 * count)) ** 2


def _fixed_counts(arcs: list[_Arc], count: int) -> list[int]:
    total_angle = math.fsum(arc.sweep for arc in arcs)
    targets = [count * arc.sweep / total_angle for arc in arcs]
    counts = [max(1, math.floor(target)) for target in targets]
    while sum(counts) < count:
        index = max(range(len(arcs)), key=lambda i: targets[i] - counts[i])
        counts[index] += 1
    while sum(counts) > count:
        eligible = [i for i, value in enumerate(counts) if value > 1]
        index = max(eligible, key=lambda i: counts[i] - targets[i])
        counts[index] -= 1
    return counts


def _plan(parameters: Mapping[str, Any], arcs: list[_Arc], radial_segments: int,
          chord_tolerance_mm: float | None, max_radial_segments: int) -> dict[str, Any]:
    if (type(radial_segments) is not int or type(max_radial_segments) is not int
            or not 12 <= radial_segments <= max_radial_segments <= _MAX_RADIAL_SEGMENTS):
        raise ValueError("generated_dish_radial_segments_out_of_range")
    tolerance = None
    if chord_tolerance_mm is not None:
        tolerance = _number(chord_tolerance_mm, "invalid_chord_tolerance_mm")
        if tolerance <= 0:
            raise ValueError("invalid_chord_tolerance_mm")
    first_radial = 4 * math.ceil(radial_segments / 4)
    last_radial = first_radial if tolerance is None else max_radial_segments
    candidate = None
    for radial in range(first_radial, last_radial + 1, 4):
        if radial > max_radial_segments:
            continue
        azimuth_error = _sag(parameters["maximum_radius_mm"], 2.0 * math.pi, radial)
        if tolerance is None:
            latitudes = _fixed_counts(arcs, radial_segments)
        else:
            remaining = tolerance - azimuth_error
            if remaining <= 0:
                continue
            latitudes = []
            for arc in arcs:
                count = 1
                while count <= _MAX_LATITUDE_SEGMENTS and _sag(arc.radius, arc.sweep, count) > remaining:
                    count += 1
                latitudes.append(count)
        latitude_count = sum(latitudes)
        vertices = radial * latitude_count + 2
        triangles = 2 * radial * latitude_count
        if (latitude_count > _MAX_LATITUDE_SEGMENTS or vertices > _MAX_VERTICES
                or triangles > _MAX_TRIANGLES):
            continue
        meridian_error = max(_sag(arc.radius, arc.sweep, count) for arc, count in zip(arcs, latitudes))
        bound = azimuth_error + meridian_error
        if tolerance is not None and bound > tolerance:
            continue
        key = (radial * latitude_count, radial, tuple(latitudes))
        if candidate is None or key < candidate[0]:
            candidate = key, radial, latitudes, azimuth_error, meridian_error, bound
    if candidate is None:
        raise ValueError("chord_tolerance_exceeds_generated_dish_tessellation_budget")
    _, radial, latitudes, azimuth_error, meridian_error, bound = candidate
    # Each ring strip is one planar convex trapezoid, split into two triangles.
    # Project its meridian chord to the circular arc (error <= meridian sag),
    # and its azimuth chord to the revolution circle (error <= azimuth sag).
    # Their sum bounds both surfaces, including every diagonal/interior point.
    return {
        "mode": "fixed_segments" if tolerance is None else "chord_tolerance",
        "requested_radial_segments": radial_segments,
        "radial_segments": radial,
        "latitude_segments": sum(latitudes),
        "arc_segments": [{"name": arc.name, "segments": count,
                          "start_radians": arc.start, "end_radians": arc.end}
                         for arc, count in zip(arcs, latitudes)],
        "chord_tolerance_mm": tolerance,
        "azimuth_deviation_bound_mm": azimuth_error,
        "meridian_deviation_bound_mm": meridian_error,
        "max_surface_deviation_mm": bound,
        "error_bound_scope": _ERROR_SCOPE,
        "cardinal_meridians_preserved": True,
        "counts": {"vertices": radial * sum(latitudes) + 2,
                   "triangles": 2 * radial * sum(latitudes)},
        "budgets": {"max_radial_segments": max_radial_segments,
                    "max_latitude_segments": _MAX_LATITUDE_SEGMENTS,
                    "max_vertices": _MAX_VERTICES, "max_triangles": _MAX_TRIANGLES},
    }


def plan_generated_dish_tessellation(profile: Mapping[str, Any], radial_segments: int = 48,
                                     chord_tolerance_mm: float | None = None, *,
                                     max_radial_segments: int = 128) -> dict[str, Any]:
    """Plan an explicit analytic DISH profile; invalid input raises ValueError."""
    parameters, arcs = _parameters(profile)
    return _plan(parameters, arcs, radial_segments, chord_tolerance_mm, max_radial_segments)


def _world_frame(frame: Any) -> tuple[list[float], list[list[float]], float]:
    if not isinstance(frame, Mapping) or not isinstance(frame.get("world"), Mapping):
        raise ValueError("invalid_or_unavailable_world_frame")
    world = frame["world"]
    orientation = world.get("orientation")
    if not isinstance(orientation, Mapping):
        raise ValueError("invalid_or_unavailable_world_frame")

    def vector(raw: Any) -> list[float]:
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            raise ValueError("invalid_or_unavailable_world_frame")
        return [_number(value, "invalid_or_unavailable_world_frame") for value in raw]

    origin = vector(world.get("position"))
    axes = [vector(orientation.get(key)) for key in ("x_axis", "y_axis", "z_axis")]
    gram = [[math.fsum(a * b for a, b in zip(left, right)) for right in axes] for left in axes]
    x, y, z = axes
    cross = [x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0]]
    determinant = math.fsum(cross[i] * z[i] for i in range(3))
    if (any(abs(gram[i][j] - (1.0 if i == j else 0.0)) > _BASIS_TOLERANCE
            for i in range(3) for j in range(3)) or abs(determinant - 1.0) > _BASIS_TOLERANCE):
        raise ValueError("invalid_or_unavailable_world_frame")
    # A row-sum bound on the Gram matrix also covers the tiny admitted roundoff
    # from a reported orthonormal basis, without calling a scaling frame rigid.
    scale_upper = math.sqrt(max(math.fsum(abs(value) for value in row) for row in gram))
    return origin, axes, scale_upper


def _mesh(parameters: Mapping[str, Any], arcs: list[_Arc], plan: Mapping[str, Any]
          ) -> tuple[list[list[float]], list[list[int]], list[dict[str, Any]]]:
    radial = plan["radial_segments"]
    vertices = [[0.0, 0.0, parameters["height_mm"]]]
    rings = []
    profile = []
    cardinals = ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))
    for arc_index, (arc, arc_plan) in enumerate(zip(arcs, plan["arc_segments"])):
        count = arc_plan["segments"]
        for step in range(1, count + 1):
            angle = arc.start + arc.sweep * step / count
            r = arc.center_r + arc.radius * math.sin(angle)
            z = arc.center_z + arc.radius * math.cos(angle)
            if angle == math.pi / 2:
                r, z = arc.center_r + arc.radius, arc.center_z
            if arc_index == len(arcs) - 1 and step == count:
                r, z = parameters["base_radius_mm"], 0.0
            if not math.isfinite(r) or not math.isfinite(z) or r <= 0:
                raise ValueError("invalid_generated_dish_profile")
            start = len(vertices)
            profile.append({"arc": arc.name, "radius_mm": r, "z_mm": z,
                            "first_vertex_index": start})
            ring = []
            for longitude in range(radial):
                if longitude % (radial // 4) == 0:
                    cosine, sine = cardinals[longitude // (radial // 4)]
                else:
                    phi = 2.0 * math.pi * longitude / radial
                    cosine, sine = math.cos(phi), math.sin(phi)
                ring.append(len(vertices))
                vertices.append([r * cosine, r * sine, z])
            rings.append(ring)
    triangles = []
    for j in range(radial):
        triangles.append([0, rings[0][j], rings[0][(j + 1) % radial]])
    for upper, lower in zip(rings, rings[1:]):
        for j in range(radial):
            following = (j + 1) % radial
            triangles.extend(([upper[j], lower[j], lower[following]],
                              [upper[j], lower[following], upper[following]]))
    base = len(vertices)
    vertices.append([0.0, 0.0, 0.0])
    for j in range(radial):
        triangles.append([base, rings[-1][(j + 1) % radial], rings[-1][j]])
    return vertices, triangles, profile


def _quality(vertices: list[list[float]], triangles: list[list[int]]) -> tuple[list[list[float]], float, float]:
    normals = []
    areas = []
    volumes = []
    edges: Counter[tuple[int, int]] = Counter()
    directions: Counter[tuple[int, int]] = Counter()
    reference = vertices[0]
    for triangle in triangles:
        a, b, c = (vertices[index] for index in triangle)
        ab, ac = ([end[i] - a[i] for i in range(3)] for end in (b, c))
        cross = [ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2],
                 ab[0] * ac[1] - ab[1] * ac[0]]
        length = math.hypot(*cross)
        if not math.isfinite(length) or length <= 0:
            raise ValueError("generated_dish_degenerate_or_non_finite_facet")
        normals.append([value / length for value in cross])
        areas.append(length / 2.0)
        volumes.append(math.fsum((a[i] - reference[i]) * cross[i] for i in range(3)) / 6.0)
        for left, right in zip(triangle, triangle[1:] + triangle[:1]):
            key = (min(left, right), max(left, right))
            edges[key] += 1
            directions[key] += 1 if left < right else -1
    volume = math.fsum(volumes)
    if (not edges or set(edges.values()) != {2} or set(directions.values()) != {0}
            or not math.isfinite(volume) or volume <= 0):
        raise ValueError("generated_dish_not_closed_and_outward")
    return normals, math.fsum(areas), volume


def derive_generated_dish_mesh(profile: Mapping[str, Any], radial_segments: int = 48, *,
                               chord_tolerance_mm: float | None = None,
                               coordinate_space: str = "generated_local", frame: Any = None,
                               max_radial_segments: int = 128) -> dict[str, Any]:
    """Derive one closed positive operand from resolved analytic parameters.

    ``profile`` supplies ``profile_kind`` (spherical_cap or torispherical),
    ``base_radius_mm``, ``height_mm``, and ``effective_knuckle_radius_mm``.
    Its physical knuckle radius must already be resolved; raw arrays fail.
    A spherical cap requires zero knuckle; a torispherical profile requires
    the native shallow domain or an exactly resolved hemisphere. For WORLD,
    ``frame`` must contain ``world.position`` and ``world.orientation`` with
    x_axis/y_axis/z_axis in the established primitive-frame mapping format.
    Its numerical basis is checked; native provenance remains the caller's job.
    Unsupported branches, unmet tolerances, or invalid frames return no mesh.
    """
    result: dict[str, Any] = {
        "success": False, "available": False, "complete": False, "shape": "DISH",
        "mesh_source": _SOURCE, "native_tessellation": False, "cuts_applied": False,
        "whole_equipment_geometry_verified": False,
        "coordinate_space": coordinate_space if isinstance(coordinate_space, str) else None,
        "origin_convention": "Base circle centered at generated local origin in z=0; apex at local +Z height.",
        # ``profile_rings`` remains a compact meridian description in the
        # generated local frame even when indexed vertices are returned in
        # WORLD.  Keep this explicit so consumers do not mix the two frames.
        "profile_rings_coordinate_frame": "generated_local",
        "vertices": [], "triangles": [], "triangle_normals": [],
        "counts": {"vertices": 0, "triangles": 0}, "closed_manifold": False, "errors": [],
    }
    try:
        if coordinate_space not in ("generated_local", "WORLD"):
            raise ValueError("invalid_generated_dish_coordinate_space")
        parameters, arcs = _parameters(profile)
        transform = _world_frame(frame) if coordinate_space == "WORLD" else None
        adjusted_tolerance = chord_tolerance_mm
        if transform is not None and chord_tolerance_mm is not None:
            adjusted_tolerance = _number(chord_tolerance_mm, "invalid_chord_tolerance_mm") / transform[2]
        plan = _plan(parameters, arcs, radial_segments, adjusted_tolerance, max_radial_segments)
        if transform is not None:
            plan["local_surface_deviation_bound_mm"] = plan["max_surface_deviation_mm"]
            plan["world_linear_scale_upper_bound"] = transform[2]
            plan["max_surface_deviation_mm"] *= transform[2]
            plan["chord_tolerance_mm"] = None if chord_tolerance_mm is None else float(chord_tolerance_mm)
            if chord_tolerance_mm is not None and plan["max_surface_deviation_mm"] > float(chord_tolerance_mm):
                raise ValueError("chord_tolerance_exceeds_generated_dish_tessellation_budget")
        vertices, triangles, profile = _mesh(parameters, arcs, plan)
        if transform is not None:
            origin, axes, _ = transform
            vertices = [[math.fsum((origin[i], *(axes[j][i] * vertex[j] for j in range(3))))
                         for i in range(3)] for vertex in vertices]
        if not all(math.isfinite(value) for vertex in vertices for value in vertex):
            raise ValueError("generated_dish_world_transform_non_finite")
        normals, area, volume = _quality(vertices, triangles)
        minimum = [min(vertex[i] for vertex in vertices) for i in range(3)]
        maximum = [max(vertex[i] for vertex in vertices) for i in range(3)]
        result.update(success=True, available=True, complete=True, parameters=parameters,
                      input_profile_parameters={"profile_kind": parameters["input_profile_kind"],
                          "base_radius_mm": parameters["base_radius_mm"],
                          "height_mm": parameters["height_mm"],
                          "effective_knuckle_radius_mm": parameters["effective_knuckle_radius_mm"]},
                      tessellation=plan,
                      radial_segments=plan["radial_segments"], vertices=vertices,
                      triangles=triangles, triangle_normals=normals, profile_rings=profile,
                      counts={"vertices": len(vertices), "triangles": len(triangles)},
                      bounds={"min": minimum, "max": maximum,
                              "size": [maximum[i] - minimum[i] for i in range(3)]},
                      analytic_local_bounds={"min": [-parameters["maximum_radius_mm"],
                                                       -parameters["maximum_radius_mm"], 0.0],
                                             "max": [parameters["maximum_radius_mm"],
                                                       parameters["maximum_radius_mm"], parameters["height_mm"]]},
                      closed_manifold=True, surface_area_mm2=area, parameter_mesh_volume_mm3=volume,
                      coordinate_frame="WORLD coordinates; millimetres" if transform else
                      "Generated dish local coordinates; millimetres")
    except (ValueError, OverflowError, ArithmeticError) as exc:
        result["errors"] = [str(exc) or "invalid_generated_dish_geometry"]
    return result
