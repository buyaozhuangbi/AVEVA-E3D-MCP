"""Selected WORLD material outlines in an explicit native equipment basis.

The source receipt remains a WORLD/mm mesh. Only projected coordinates use the
equipment origin and axes. Visibility is evaluated jointly against every source
triangle, without welding, Boolean union, or whole-equipment geometry claims.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
from typing import Any

from .drawing_sheet_render import validate_drawing_sheet_scene
from .material_assembly_drawing import WORLD_FRAME, _revalidate_combined
from .material_assembly_sheet import _PROOF_FIELDS
from .nozzle_axis_sheet import _PAPERS, _equipment_frame, _fixed, _prepare_axis_rows, build_nozzle_axis_sheets
from .primitive_csg_mesh import csg_geometry_sha256
from .triangle_mesh_validation import MAX_TRIANGLES, MAX_VERTICES, validate_triangle_mesh
from .orthographic_drawing import (
    MAX_DRAWING_SEGMENTS, MAX_TOTAL_DRAWING_SEGMENTS, _dot, _edge_adjacency, _normal,
)
from .triangle_mesh_visibility import projected_edge_visibility


_KIND = "selected parameter nozzle outline projection"
_FRAME = "native equipment axes relative to native equipment WORLD POS"
_SCHEMA = "nozzle-outline-global-linework-v1"
_PRECISION = 15
_LIMIT_MM = 1_000_000_000.0
_PARALLEL_EDGE_TOLERANCE = 1e-12
_WORK_BUDGETS = {"max_node_visits": 2_000_000, "max_triangle_tests": 2_000_000,
                 "max_output_intervals": 100_000}
_OUTLINE_MARKERS = {"fully_occluded": " HID",
                    "no_projected_outline": " N/O",
                    "visibility_unresolved": " N/O"}


class NozzleOutlineScaleDoesNotFit(ValueError):
    def __init__(self, requested: float, minimum: float, recommended: float):
        super().__init__("nozzle_outline_explicit_scale_does_not_fit")
        self.diagnostics = {
            "requested_scale_denominator": requested,
            "minimum_scale_denominator": minimum,
            "recommended_scale_denominator": recommended,
        }


def _digest(value: Any) -> str:
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        raise ValueError("nozzle_outline_evidence_not_serializable") from None
    return hashlib.sha256(payload).hexdigest()


def _view_specs(frame: Mapping[str, Any]):
    u, v, n = (frame["basis_world"][axis] for axis in ("u", "v", "n"))
    return (("RADIAL", "TOP", "v", u, v, n),
            ("AXIAL_ELEVATION", "FRONT", "h", u, n, [-value for value in v]))


def _coordinates(point, origin, horizontal, vertical, toward):
    delta = [point[axis] - origin[axis] for axis in range(3)]
    projected = [_dot(delta, axis) for axis in (horizontal, vertical, toward)]
    if any(not math.isfinite(value) or abs(value) > _LIMIT_MM for value in projected):
        raise ValueError("nozzle_outline_model_coordinate_limit_exceeded")
    return [0.0 if value == 0 else value for value in projected]


def _projection_domain(projection):
    domain = {"schema": _SCHEMA, "geometry_sha256": projection["geometry_sha256"],
            "source_proof_sha256": projection["source_proof_sha256"],
            "equipment_frame": projection["equipment_frame"], "views": projection["views"],
            "feature_angle_degrees": projection["feature_angle_degrees"],
            "parallel_edge_projected_length_relative_tolerance": projection["parallel_edge_projected_length_relative_tolerance"],
            "hidden_line_removal": projection["hidden_line_removal"],
            "show_hidden_lines": projection["show_hidden_lines"]}
    if projection.get("nozzle_profiles"):
        domain.update(nozzle_profile_sha256=projection["nozzle_profile_sha256"],
                      projection_geometry_sha256=projection["projection_geometry_sha256"])
    return domain


def _profile_number(value, field):
    if type(value) not in {int, float}:
        raise ValueError(f"invalid_nozzle_profile_{field}")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise ValueError(f"invalid_nozzle_profile_{field}") from None
    if not math.isfinite(number) or abs(number) > _LIMIT_MM:
        raise ValueError(f"invalid_nozzle_profile_{field}")
    return number


def _profile_mesh(start, end, stations, frame, radial_segments=32):
    length = math.dist(start, end)
    axis = [(end[index] - start[index]) / length for index in range(3)]
    reference = next(vector for vector in frame["basis_world"].values() if abs(_dot(vector, axis)) < .9)
    perpendicular = [reference[index] - axis[index] * _dot(reference, axis) for index in range(3)]
    norm = math.hypot(*perpendicular)
    perpendicular = [value / norm for value in perpendicular]
    second = [axis[1] * perpendicular[2] - axis[2] * perpendicular[1],
              axis[2] * perpendicular[0] - axis[0] * perpendicular[2],
              axis[0] * perpendicular[1] - axis[1] * perpendicular[0]]
    radial = radial_segments
    vertices, triangles = [], []
    for station in stations:
        distance, radius = station["distance_mm"], station["outer_diameter_mm"] / 2
        for index in range(radial):
            angle = math.tau * index / radial
            vertices.append([math.fsum((start[component], axis[component] * distance,
                radius * math.cos(angle) * perpendicular[component],
                radius * math.sin(angle) * second[component])) for component in range(3)])
    for ring in range(len(stations) - 1):
        for index in range(radial):
            a, b = ring * radial + index, ring * radial + (index + 1) % radial
            c, d = a + radial, b + radial
            triangles.extend(([a, b, d], [a, d, c]))
    first_center, last_center = len(vertices), len(vertices) + 1
    vertices.extend([list(start), list(end)])
    for index in range(radial):
        following = (index + 1) % radial
        triangles.append([first_center, following, index])
        offset = (len(stations) - 1) * radial
        triangles.append([last_center, offset + index, offset + following])
    validation = validate_triangle_mesh(vertices, triangles)
    if any(validation.get(flag) is not True for flag in ("success", "available", "complete", "valid")):
        raise ValueError("nozzle_profile_closed_outward_mesh_required")
    return {"vertices": vertices, "triangles": triangles, "validation": validation,
            "geometry_sha256": csg_geometry_sha256(vertices, triangles)}


def _prepare_profiles(raw_profiles, drawing, frame, chord_tolerance_mm=None):
    if chord_tolerance_mm is not None:
        chord_tolerance_mm = _profile_number(chord_tolerance_mm, "chord_tolerance")
        if chord_tolerance_mm <= 0:
            raise ValueError("invalid_nozzle_profile_chord_tolerance")
    if not isinstance(raw_profiles, (list, tuple)) or not 1 <= len(raw_profiles) <= 32:
        raise ValueError("nozzle_profiles_require_one_to_32_explicit_nozzles")
    rows = _prepare_axis_rows(drawing, frame)
    targets = {}
    for row in rows:
        targets.setdefault(row["resolved_refno"], {})[row["point_number"]] = row
    profiles, seen = [], set()
    for raw in raw_profiles:
        if not isinstance(raw, Mapping):
            raise ValueError("invalid_nozzle_profile")
        refno = raw.get("resolved_refno")
        if not isinstance(refno, str) or refno not in targets or refno in seen:
            raise ValueError("nozzle_profile_unique_resolved_source_required")
        seen.add(refno)
        first, last = raw.get("axis_start_point_number"), raw.get("axis_end_point_number")
        if (type(first) is not int or type(last) is not int or first == last
                or first not in targets[refno] or last not in targets[refno]):
            raise ValueError("nozzle_profile_two_distinct_native_points_required")
        start_row, end_row = targets[refno][first], targets[refno][last]
        start = [start_row["position"][axis] for axis in ("x", "y", "z")]
        end = [end_row["position"][axis] for axis in ("x", "y", "z")]
        length = math.dist(start, end)
        if length <= 1e-9 or not math.isfinite(length):
            raise ValueError("nozzle_profile_nonzero_axis_required")
        for row, sign in ((start_row, -1), (end_row, 1)):
            direction = [row["direction"][axis] for axis in ("x", "y", "z")]
            norm = math.hypot(*direction)
            if _dot(direction, [(end[index] - start[index]) * sign / length for index in range(3)]) / norm < 1 - 1e-6:
                raise ValueError("nozzle_profile_axis_and_native_pdir_mismatch")
        evidence = raw.get("dimension_evidence")
        if not isinstance(evidence, Mapping) or not evidence:
            raise ValueError("nozzle_profile_dimension_evidence_required")
        stations_raw = raw.get("stations")
        if not isinstance(stations_raw, (list, tuple)) or not 2 <= len(stations_raw) <= 16:
            raise ValueError("nozzle_profile_two_to_16_stations_required")
        stations = []
        for station in stations_raw:
            if not isinstance(station, Mapping) or set(station) != {"distance_mm", "outer_diameter_mm"}:
                raise ValueError("invalid_nozzle_profile_station")
            distance = _profile_number(station["distance_mm"], "station_distance")
            diameter = _profile_number(station["outer_diameter_mm"], "outer_diameter")
            if not 0 <= distance <= length + 1e-6 or diameter <= 0:
                raise ValueError("invalid_nozzle_profile_station")
            if abs(distance - length) <= 1e-6:
                distance = length
            if stations and (distance < stations[-1]["distance_mm"]
                    or distance == stations[-1]["distance_mm"] and diameter == stations[-1]["outer_diameter_mm"]):
                raise ValueError("nozzle_profile_ordered_nonduplicate_stations_required")
            stations.append({"distance_mm": distance, "outer_diameter_mm": diameter})
        if stations[0]["distance_mm"] != 0 or abs(stations[-1]["distance_mm"] - length) > 1e-6:
            raise ValueError("nozzle_profile_stations_must_cover_native_axis")
        stations[-1]["distance_mm"] = length
        bore = raw.get("nominal_bore_mm")
        if bore is not None:
            bore = _profile_number(bore, "nominal_bore")
            if bore <= 0:
                raise ValueError("invalid_nozzle_profile_nominal_bore")
        radius = max(station["outer_diameter_mm"] for station in stations) / 2
        radial_segments = 32
        if chord_tolerance_mm is not None:
            radial_segments = next((segments for segments in range(12, 129, 4)
                                    if radius * (1 - math.cos(math.pi / segments)) <= chord_tolerance_mm), None)
            if radial_segments is None:
                raise ValueError("nozzle_profile_chord_tolerance_exceeds_segment_limit")
        mesh = _profile_mesh(start, end, stations, frame, radial_segments)
        label = str(end_row.get("element_name") or "").rsplit("/", 1)[-1].upper()
        if not label or len(label) > 12 or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in label):
            label = f"N{len(profiles) + 1:03d}"
        profiles.append({"resolved_refno": refno, "refno": end_row["refno"], "label": label,
            "axis_start_point_number": first, "axis_end_point_number": last,
            "axis_start_world_mm": start, "axis_end_world_mm": end, "axis_length_mm": length,
            "source_indices": [row["source_index"] for row in targets[refno].values()],
            "stations": stations, "dimension_evidence": deepcopy(dict(evidence)), "nominal_bore_mm": bore,
            "derived_mesh": mesh, "geometry_kind": "evidence-derived axisymmetric outer profile",
            "radial_segments": radial_segments, "chord_tolerance_mm": chord_tolerance_mm,
            "max_radial_chord_error_mm": radius * (1 - math.cos(math.pi / radial_segments)),
            "bore_used_as_outer_diameter": False, "internal_bore_geometry_modeled": False})
    if seen != set(targets):
        raise ValueError("nozzle_profile_required_for_every_resolved_nozzle")
    if len({profile["label"] for profile in profiles}) != len(profiles):
        raise ValueError("nozzle_profile_unique_labels_required")
    return profiles


def _projection_geometry(material, profiles):
    points, faces = deepcopy(material["vertices"]), deepcopy(material["triangles"])
    sources = deepcopy(material["source_ranges"])
    for profile in profiles or []:
        mesh = profile["derived_mesh"]
        start, triangle_start = len(points), len(faces)
        points.extend(deepcopy(mesh["vertices"]))
        faces.extend([[vertex + start for vertex in face] for face in mesh["triangles"]])
        sources.append({"source_index": len(sources), "refno": profile["resolved_refno"],
            "source_kind": "nozzle_profile", "nozzle_label": profile["label"],
            "geometry_mode": "evidence_derived_outer_profile", "source_mesh_sha256": mesh["geometry_sha256"],
            "vertex_range": [start, len(points)], "triangle_range": [triangle_start, len(faces)], "empty": False})
    if len(points) > MAX_VERTICES or len(faces) > MAX_TRIANGLES:
        raise ValueError("nozzle_outline_combined_projection_mesh_limit_exceeded")
    return points, faces, sources


def _profile_visibility_status(candidate_count, visible_count, fully_hidden_edge_count, checked):
    if candidate_count == 0:
        return "no_projected_outline"
    if not checked:
        return "not_evaluated"
    if visible_count > 0:
        return "visible"
    if fully_hidden_edge_count == candidate_count:
        return "fully_occluded"
    return "visibility_unresolved"


def _project_material_outline(combined, frame, feature_angle_degrees,
                              hidden_line_removal, show_hidden_lines, profiles=None):
    if (isinstance(feature_angle_degrees, bool)
            or not isinstance(feature_angle_degrees, (int, float))):
        raise ValueError("invalid_feature_angle")
    try:
        angle = float(feature_angle_degrees)
    except (ValueError, OverflowError):
        raise ValueError("invalid_feature_angle") from None
    if not math.isfinite(angle) or not 0 <= angle <= 180:
        raise ValueError("invalid_feature_angle")
    if type(hidden_line_removal) is not bool or type(show_hidden_lines) is not bool:
        raise ValueError("invalid_nozzle_outline_visibility_options")
    if show_hidden_lines and not hidden_line_removal:
        raise ValueError("hidden_line_display_requires_removal")
    material = _revalidate_combined(combined)
    if material.get("success") is not True:
        raise ValueError(material.get("error_code", "nozzle_outline_valid_material_required"))
    if material["empty"]:
        raise ValueError("nozzle_outline_nonempty_material_required")
    points, faces, sources = _projection_geometry(material, profiles)
    if any(abs(value) > _LIMIT_MM for point in points for value in point):
        raise ValueError("nozzle_outline_model_coordinate_limit_exceeded")
    normals = [_normal(points, face) for face in faces]
    if any(normal is None for normal in normals):
        raise ValueError("nozzle_outline_face_normal_unavailable")
    edges = _edge_adjacency(faces)
    if any(len(adjacent) != 2 for adjacent in edges.values()):
        raise ValueError("nozzle_outline_closed_edge_adjacency_required")
    owners = [-1] * len(points)
    for source in sources:
        start, end = source["vertex_range"]
        owners[start:end] = [source["source_index"]] * (end - start)
    views, total = [], 0
    for semantic, renderer, vertical_label, horizontal, vertical, toward in _view_specs(frame):
        projected = [_coordinates(point, frame["origin_world_mm"], horizontal, vertical, toward) for point in points]
        facing = [_dot(normal, toward) for normal in normals]
        candidates = []
        for (left, right), adjacent in sorted(edges.items()):
            first, second = adjacent
            silhouette = ((facing[first] > 1e-12 and facing[second] <= 1e-12)
                          or (facing[second] > 1e-12 and facing[first] <= 1e-12))
            cosine = min(1.0, max(-1.0, _dot(normals[first], normals[second])))
            if not silhouette and math.acos(cosine) <= math.radians(angle) + 1e-10:
                continue
            if math.dist(projected[left][:2], projected[right][:2]) <= math.dist(points[left], points[right]) * _PARALLEL_EDGE_TOLERANCE:
                continue
            if owners[left] < 0 or owners[left] != owners[right]:
                raise ValueError("nozzle_outline_line_source_attribution_failed")
            candidates.append({"edge": [left, right], "incident_faces": [first, second],
                               "kind": "silhouette" if silhouette else "feature"})
        if not candidates:
            raise ValueError("nozzle_outline_nonempty_view_linework_required")
        visibility = None
        if hidden_line_removal:
            visibility = projected_edge_visibility(projected, faces,
                [{"vertices": line["edge"], "incident_faces": line["incident_faces"]} for line in candidates],
                **_WORK_BUDGETS)
            if any(visibility.get(flag) is not True for flag in ("success", "available", "complete")):
                raise ValueError(visibility.get("error_code", "nozzle_outline_visibility_failed"))
        lines = []
        profile_visibility = {
            source["source_index"]: {"source_index": source["source_index"], "refno": source["refno"],
                "nozzle_label": source["nozzle_label"], "candidate_edge_count": 0,
                "occlusion_checked": hidden_line_removal, "visible_stroke_count": 0,
                "hidden_interval_count": 0 if hidden_line_removal else None,
                "fully_hidden_edge_count": 0 if hidden_line_removal else None}
            for source in sources if source.get("source_kind") == "nozzle_profile"
        }
        # Coincident source edges retain their independent attribution. No
        # cross-source deduplication can silently discard a source boundary.
        for index, candidate in enumerate(candidates):
            left, right = candidate["edge"]
            source = sources[owners[left]]
            profile_observation = profile_visibility.get(source["source_index"])
            if visibility is None:
                classes = [("visible", [[0.0, 1.0]])]
            else:
                visible_intervals = visibility["edges"][index]["visible_intervals"]
                hidden_intervals = visibility["edges"][index]["hidden_intervals"]
                classes = []
                if show_hidden_lines:
                    classes.append(("hidden", hidden_intervals))
                classes.append(("visible", visible_intervals))
            if profile_observation is not None:
                profile_observation["candidate_edge_count"] += 1
                if visibility is not None:
                    profile_observation["hidden_interval_count"] += len(hidden_intervals)
                    profile_observation["fully_hidden_edge_count"] += hidden_intervals == [[0.0, 1.0]]
            for classification, intervals in classes:
                for low, high in intervals:
                    if high <= low:
                        continue
                    start = [projected[left][axis] + (projected[right][axis] - projected[left][axis]) * low for axis in (0, 1)]
                    end = [projected[left][axis] + (projected[right][axis] - projected[left][axis]) * high for axis in (0, 1)]
                    if start == end:
                        raise ValueError("nozzle_outline_projected_interval_below_precision")
                    lines.append({**deepcopy(candidate), "start": start, "end": end,
                        "visibility": classification, "parameter_interval": [low, high],
                        "source_index": source["source_index"], "source_refno": source["refno"],
                        "source_geometry_mode": source["geometry_mode"],
                        "source_mesh_sha256": source["source_mesh_sha256"],
                        "source_edge": [vertex - source["vertex_range"][0] for vertex in (left, right)]})
                    if source.get("source_kind") == "nozzle_profile":
                        lines[-1].update(source_kind="nozzle_profile", nozzle_label=source["nozzle_label"])
                        if classification == "visible":
                            profile_observation["visible_stroke_count"] += 1
        # Paint solid strokes last while retaining the complete source map.
        lines.sort(key=lambda line: line["visibility"] != "hidden")
        total += len(lines)
        if len(lines) > MAX_DRAWING_SEGMENTS or total > MAX_TOTAL_DRAWING_SEGMENTS:
            raise ValueError("drawing_segment_limit_exceeded")
        views.append({"name": semantic, "semantic_name": semantic, "renderer_view": renderer,
            "horizontal_axis": "u", "vertical_axis": vertical_label,
            "horizontal_axis_world": list(horizontal), "vertical_axis_world": list(vertical),
            "toward_viewer_world": list(toward), "origin_world_mm": deepcopy(frame["origin_world_mm"]),
            "coordinate_frame": _FRAME, "coordinate_unit": "mm",
            "depth_convention": "larger dot(P minus native equipment POS, toward_viewer_world) is closer",
            "bounds": {"min": [min(point[axis] for point in projected) for axis in (0, 1)],
                       "max": [max(point[axis] for point in projected) for axis in (0, 1)]},
            "lines": lines, "line_count": len(lines),
            "visibility": {key: deepcopy(value) for key, value in visibility.items() if key != "edges"}
                          if visibility is not None else None})
        if profiles:
            for observation in profile_visibility.values():
                observation["status"] = _profile_visibility_status(
                    observation["candidate_edge_count"], observation["visible_stroke_count"],
                    observation["fully_hidden_edge_count"], hidden_line_removal)
            views[-1]["nozzle_profile_visibility"] = list(profile_visibility.values())
    proof = {key: deepcopy(material[key]) for key in _PROOF_FIELDS}
    result = {**proof, "success": True, "available": True, "complete": True, "empty": False,
        "drawing_kind": _KIND, "coordinate_space": "equipment local projection",
        "coordinate_frame": _FRAME, "coordinate_unit": "mm", "source_coordinate_space": "WORLD",
        "source_coordinate_frame": WORLD_FRAME, "source_material": material,
        "equipment_frame": deepcopy(frame), "basis_world": deepcopy(frame["basis_world"]),
        "origin_world_mm": deepcopy(frame["origin_world_mm"]), "projection_convention": "right_handed",
        "feature_angle_degrees": angle, "hidden_line_removal": hidden_line_removal,
        "show_hidden_lines": show_hidden_lines, "occlusion_checked": hidden_line_removal,
        "visibility_scope": "Every triangle of every explicitly supplied nonempty source, jointly per view.",
        "visibility_work_budgets": deepcopy(_WORK_BUDGETS), "views": views, "segment_count": total,
        "parallel_edge_projected_length_relative_tolerance": _PARALLEL_EDGE_TOLERANCE,
        "source_proof_sha256": _digest(proof), "global_linework_hash_schema": _SCHEMA,
        "geometry_scope": "selected parameter material sources", "whole_equipment_geometry_verified": False,
        "explicit_source_membership_verified": False, "native_draft_sheet": False,
        "scope": "Selected parameter material boundaries only; selected source membership is documented separately. Whole equipment and native cuts are unverified.",
        "native_cut_scope": "PARAMETER ASSEMBLY - NATIVE CUT SCOPE UNVERIFIED"}
    if profiles:
        result.update(nozzle_profiles=deepcopy(profiles), outline_source_ranges=sources,
                      nozzle_profile_sha256=_digest(profiles),
                      projection_geometry_sha256=csg_geometry_sha256(points, faces))
    result["global_linework_sha256"] = _digest(_projection_domain(result))
    return result


def _validate_outline_projection(projection, frame):
    """Check the private projection receipt before adding its paper strokes."""
    if (not isinstance(projection, Mapping) or projection.get("drawing_kind") != _KIND
            or any(projection.get(flag) is not True for flag in ("success", "available", "complete"))
            or projection.get("empty") is not False
            or projection.get("coordinate_space") != "equipment local projection"
            or projection.get("coordinate_frame") != _FRAME or projection.get("coordinate_unit") != "mm"
            or projection.get("source_coordinate_space") != "WORLD"
            or projection.get("source_coordinate_frame") != WORLD_FRAME
            or projection.get("projection_convention") != "right_handed"
            or projection.get("geometry_scope") != "selected parameter material sources"
            or projection.get("whole_equipment_geometry_verified") is not False
            or projection.get("explicit_source_membership_verified") is not False
            or projection.get("native_draft_sheet") is not False
            or projection.get("parallel_edge_projected_length_relative_tolerance") != _PARALLEL_EDGE_TOLERANCE
            or _digest(projection.get("equipment_frame")) != _digest(frame)
            or _digest(projection.get("basis_world")) != _digest(frame["basis_world"])
            or _digest(projection.get("origin_world_mm")) != _digest(frame["origin_world_mm"])):
        raise ValueError("nozzle_outline_equipment_projection_required")
    material = _revalidate_combined(projection.get("source_material"))
    if material.get("success") is not True or material.get("empty") is not False:
        raise ValueError("nozzle_outline_source_material_proof_invalid")
    proof = {key: material[key] for key in _PROOF_FIELDS}
    if (any(_digest(projection.get(key)) != _digest(value) for key, value in proof.items())
            or projection.get("source_proof_sha256") != _digest(proof)):
        raise ValueError("nozzle_outline_source_proof_mismatch")
    profiles = projection.get("nozzle_profiles")
    if profiles:
        if not isinstance(profiles, list) or not 1 <= len(profiles) <= 32:
            raise ValueError("invalid_nozzle_profile_receipt")
        for profile in profiles:
            if (not isinstance(profile, Mapping) or not profile.get("dimension_evidence")
                    or profile.get("bore_used_as_outer_diameter") is not False):
                raise ValueError("invalid_nozzle_profile_receipt")
            radial = profile.get("radial_segments")
            if type(radial) is not int or not 12 <= radial <= 128:
                raise ValueError("invalid_nozzle_profile_radial_segments")
            error = max(station["outer_diameter_mm"] for station in profile["stations"]) / 2 * (1 - math.cos(math.pi / radial))
            tolerance = profile.get("chord_tolerance_mm")
            if (profile.get("max_radial_chord_error_mm") != error
                    or tolerance is not None and (type(tolerance) not in {int, float} or not 0 < tolerance or error > tolerance)):
                raise ValueError("nozzle_profile_chord_error_proof_mismatch")
            mesh = _profile_mesh(profile["axis_start_world_mm"], profile["axis_end_world_mm"], profile["stations"], frame, radial)
            if _digest(mesh) != _digest(profile.get("derived_mesh")):
                raise ValueError("nozzle_profile_mesh_proof_mismatch")
        if projection.get("nozzle_profile_sha256") != _digest(profiles):
            raise ValueError("nozzle_profile_source_proof_mismatch")
    points_world, faces, sources = _projection_geometry(material, profiles)
    if profiles and (projection.get("projection_geometry_sha256") != csg_geometry_sha256(points_world, faces)
            or _digest(projection.get("outline_source_ranges")) != _digest(sources)):
        raise ValueError("nozzle_profile_projection_geometry_mismatch")
    if (type(projection.get("hidden_line_removal")) is not bool
            or type(projection.get("show_hidden_lines")) is not bool
            or projection["show_hidden_lines"] and not projection["hidden_line_removal"]):
        raise ValueError("invalid_nozzle_outline_visibility_options")
    views, total = projection.get("views"), 0
    adjacency = _edge_adjacency(faces)
    if not isinstance(views, list) or len(views) != 2:
        raise ValueError("nozzle_outline_radial_and_axial_views_required")
    for view, (semantic, renderer, vertical_label, horizontal, vertical, toward) in zip(views, _view_specs(frame)):
        if (not isinstance(view, Mapping) or view.get("name") != semantic or view.get("semantic_name") != semantic
                or view.get("renderer_view") != renderer or view.get("horizontal_axis") != "u"
                or view.get("vertical_axis") != vertical_label
                or view.get("coordinate_frame") != _FRAME or view.get("coordinate_unit") != "mm"
                or any(_digest(view.get(key)) != _digest(value) for key, value in (
                    ("horizontal_axis_world", list(horizontal)), ("vertical_axis_world", list(vertical)),
                    ("toward_viewer_world", list(toward)), ("origin_world_mm", frame["origin_world_mm"])))):
            raise ValueError("nozzle_outline_view_basis_mismatch")
        points = [_coordinates(point, frame["origin_world_mm"], horizontal, vertical, toward)
                  for point in points_world]
        bounds = {"min": [min(point[axis] for point in points) for axis in (0, 1)],
                  "max": [max(point[axis] for point in points) for axis in (0, 1)]}
        if _digest(view.get("bounds")) != _digest(bounds):
            raise ValueError("nozzle_outline_geometry_bounds_mismatch")
        lines = view.get("lines")
        if (not isinstance(lines, list) or len(lines) > MAX_DRAWING_SEGMENTS
                or type(view.get("line_count")) is not int or view["line_count"] != len(lines)):
            raise ValueError("nozzle_outline_line_count_mismatch")
        emitted_profile_strokes = {}
        for line in lines:
            source_index = line.get("source_index") if isinstance(line, Mapping) else None
            if type(source_index) is not int or not 0 <= source_index < len(sources):
                raise ValueError("nozzle_outline_line_source_invalid")
            source, edge, interval = sources[source_index], line.get("edge"), line.get("parameter_interval")
            if (source["empty"] or line.get("source_refno") != source["refno"]
                    or line.get("source_geometry_mode") != source["geometry_mode"]
                    or line.get("source_mesh_sha256") != source["source_mesh_sha256"]
                    or not isinstance(edge, list) or len(edge) != 2 or edge[0] == edge[1]
                    or any(type(vertex) is not int or not source["vertex_range"][0] <= vertex < source["vertex_range"][1] for vertex in edge)
                    or _digest(line.get("source_edge")) != _digest([vertex - source["vertex_range"][0] for vertex in edge])
                    or _digest(line.get("incident_faces")) != _digest(adjacency.get(tuple(sorted(edge))))
                    or not isinstance(line.get("kind"), str) or line["kind"] not in {"silhouette", "feature"}
                    or not isinstance(line.get("visibility"), str) or line["visibility"] not in {"visible", "hidden"}
                    or line["visibility"] == "hidden" and not projection["show_hidden_lines"]
                    or not isinstance(interval, list) or len(interval) != 2
                    or any(type(value) not in {int, float} for value in interval)
                    or not 0 <= interval[0] < interval[1] <= 1
                    or not projection["hidden_line_removal"] and interval != [0.0, 1.0]):
                raise ValueError("nozzle_outline_line_source_invalid")
            for field, parameter in zip(("start", "end"), interval):
                expected = [points[edge[0]][axis] + (points[edge[1]][axis] - points[edge[0]][axis]) * parameter for axis in (0, 1)]
                if _digest(line.get(field)) != _digest(expected):
                    raise ValueError("nozzle_outline_line_projection_mismatch")
            if source.get("source_kind") == "nozzle_profile":
                counts = emitted_profile_strokes.setdefault(source_index, [0, 0, 0])
                counts[0 if line["visibility"] == "visible" else 1] += 1
                if line["visibility"] == "hidden" and interval == [0.0, 1.0]:
                    counts[2] += 1
        if profiles:
            observations = view.get("nozzle_profile_visibility")
            if not isinstance(observations, list) or len(observations) != len(profiles):
                raise ValueError("nozzle_outline_profile_visibility_mismatch")
            for observation, source in zip(observations, sources[-len(profiles):], strict=True):
                source_index = source["source_index"]
                if (not isinstance(observation, Mapping)
                        or type(observation.get("source_index")) is not int or observation["source_index"] != source_index
                        or observation.get("refno") != source["refno"]
                        or observation.get("nozzle_label") != source["nozzle_label"]):
                    raise ValueError("nozzle_outline_profile_visibility_mismatch")
                checked, show_hidden = projection["hidden_line_removal"], projection["show_hidden_lines"]
                candidate_count, visible_count, hidden_count, full_count = (
                    observation.get(key) for key in
                    ("candidate_edge_count", "visible_stroke_count", "hidden_interval_count", "fully_hidden_edge_count"))
                numeric_counts = (candidate_count, visible_count, hidden_count, full_count) if checked else (candidate_count, visible_count)
                if (observation.get("occlusion_checked") is not checked
                        or any(type(count) is not int or count < 0 for count in numeric_counts)
                        or not checked and (hidden_count is not None or full_count is not None)):
                    raise ValueError("nozzle_outline_profile_visibility_mismatch")
                visible_emitted, hidden_emitted, full_emitted = emitted_profile_strokes.get(source_index, (0, 0, 0))
                if (visible_count != visible_emitted or checked and full_count > min(candidate_count, hidden_count)
                        or candidate_count == 0 and (visible_count or hidden_count)
                        or not checked and visible_count != candidate_count
                        or show_hidden and (hidden_count != hidden_emitted or full_count != full_emitted)
                        or observation.get("status") != _profile_visibility_status(
                            candidate_count, visible_count, full_count, checked)):
                    raise ValueError("nozzle_outline_profile_visibility_mismatch")
        total += len(lines)
    if total > MAX_TOTAL_DRAWING_SEGMENTS or type(projection.get("segment_count")) is not int or projection["segment_count"] != total:
        raise ValueError("nozzle_outline_line_count_mismatch")
    if (projection.get("global_linework_hash_schema") != _SCHEMA
            or projection.get("global_linework_sha256") != _digest(_projection_domain(projection))):
        raise ValueError("nozzle_outline_linework_hash_mismatch")


def _place_outline_geometry(strokes, projection, plan):
    """Place every retained source line before any nozzle annotation."""
    placement = {}
    denominator, height = plan["scale_denominator"], plan["height"]
    for index, view in enumerate(projection["views"]):
        name, bounds = view["renderer_view"], plan["model_bounds"][view["renderer_view"]]
        span = [bounds["max"][axis] - bounds["min"][axis] for axis in (0, 1)]
        if plan.get("profile_layout"):
            plot = plan["view_plots"][name]
            origin = [plot["min"][axis] + (plot["max"][axis] - plot["min"][axis] - span[axis] / denominator) / 2 for axis in (0, 1)]
        else:
            left = 8 + index * (plan["cell_width"] + 8)
            origin = [left + 27 + (plan["plot_width"] - span[0] / denominator) / 2,
                      plan["plot_bottom"] + (plan["plot_height"] - span[1] / denominator) / 2]
        def paper(point):
            return [origin[axis] + (point[axis] - bounds["min"][axis]) / denominator for axis in (0, 1)]
        start_index = len(strokes)
        for line_index, line in enumerate(view["lines"]):
            start, end = paper(line["start"]), paper(line["end"])
            encoded = lambda point: tuple(format(value, f".{_PRECISION}f") for value in point)
            if (encoded(start) == encoded(end)
                    or encoded([start[0], height - start[1]]) == encoded([end[0], height - end[1]])):
                raise ValueError("nozzle_outline_geometry_below_precision")
            retained = {key: deepcopy(value) for key, value in line.items() if key not in {"start", "end", "kind", "visibility"}}
            strokes.append({"start": start, "end": end, "role": line["kind"], "view_name": name,
                "visibility": line["visibility"], **retained, "source_kind": line.get("source_kind", "material"),
                "source_line_index": line_index, "global_line_index": line_index,
                "source_interval": [0.0, 1.0], "semantic_view": view["name"],
                "model_start": deepcopy(line["start"]), "model_end": deepcopy(line["end"]),
                "annotation_kind": "equipment_material_outline"})
        placement[name] = {"geometry_stroke_range": [start_index, len(strokes)],
            "geometry_model_bounds": deepcopy(view["bounds"]),
            "horizontal_axis_world": deepcopy(view["horizontal_axis_world"]),
            "vertical_axis_world": deepcopy(view["vertical_axis_world"]),
            "toward_viewer_world": deepcopy(view["toward_viewer_world"]),
            "origin_world_mm": deepcopy(view["origin_world_mm"]),
            "source_line_indices": list(range(len(view["lines"])))}
    return placement


_PROFILE_COLUMNS = ("ID", "REF", "BORE MM", "NECK OD", "MAX OD", "LEN MM", "H MM", "ANG DEG", "DIR DEG", "TILT DEG")
_PROFILE_WIDTHS = (4, 26, 9, 9, 9, 9, 14, 8, 8, 8)


def _profile_table(values):
    if any(len(value) > width for value, width in zip(values, _PROFILE_WIDTHS)):
        raise ValueError("nozzle_profile_table_value_does_not_fit")
    return " ".join(value.ljust(width) if index < 2 else value.rjust(width)
                    for index, (value, width) in enumerate(zip(values, _PROFILE_WIDTHS)))


def _engineering_scale(required):
    if not math.isfinite(required) or required < 0:
        raise ValueError("nozzle_outline_invalid_scale_requirement")
    for exponent in range(-6, 10):
        for value in (1, 2, 2.5, 5):
            candidate = value * 10 ** exponent
            if candidate >= required:
                return float(candidate)
    raise ValueError("nozzle_outline_scale_limit_exceeded")


def _build_profile_sheets(rows, options, frame, projection):
    """Keep native point rows while laying out one physical profile per nozzle."""
    by_ref = {}
    for row in rows:
        by_ref.setdefault(row["resolved_refno"], []).append(row)
    groups = []
    for profile in projection["nozzle_profiles"]:
        values = by_ref.get(profile["resolved_refno"])
        if not values or [row["source_index"] for row in values] != profile["source_indices"]:
            raise ValueError("nozzle_profile_point_source_mismatch")
        by_number = {row["point_number"]: row for row in values}
        start_row, end_row = (by_number[profile[key]] for key in ("axis_start_point_number", "axis_end_point_number"))
        for row, field in ((start_row, "axis_start_world_mm"), (end_row, "axis_end_world_mm")):
            if [row["position"][axis] for axis in ("x", "y", "z")] != profile[field]:
                raise ValueError("nozzle_profile_point_source_mismatch")
        group = {"profile": profile, "rows": values, "start": start_row, "end": end_row}
        groups.append(group)
    if set(by_ref) != {group["profile"]["resolved_refno"] for group in groups}:
        raise ValueError("nozzle_profile_required_for_every_resolved_nozzle")
    pages, page = [], []
    for group in groups:
        if len(group["rows"]) > options["points_per_page"]:
            raise ValueError("nozzle_profile_points_exceed_page_capacity")
        if page and sum(len(item["rows"]) for item in page) + len(group["rows"]) > options["points_per_page"]:
            pages.append(page)
            page = []
        page.append(group)
    if page:
        pages.append(page)
    capacity = max(len(page) for page in pages)
    width, height = _PAPERS[options["size"]]
    row_height = 4.0 if options["size"] == "A3" else 3.6
    table_bottom, table_header = 28.0, 5.0
    table_top = table_bottom + table_header + capacity * row_height
    cell_width = (width - 34.0) / 2
    plots = {name: {"min": [12 + index * (cell_width + 10), table_top + 6],
                    "max": [12 + index * (cell_width + 10) + cell_width, height - 40]}
             for index, name in enumerate(("TOP", "FRONT"))}
    bounds = {}
    required = 0.0
    for view, vertical in zip(projection["views"], ("v", "h")):
        name = view["renderer_view"]
        points = [[0.0, 0.0], view["bounds"]["min"], view["bounds"]["max"],
                  *[[row["equipment_coordinates_mm"][axis] for axis in ("u", vertical)] for row in rows]]
        bounds[name] = {"min": [min(point[axis] for point in points) for axis in (0, 1)],
                        "max": [max(point[axis] for point in points) for axis in (0, 1)]}
        for axis in (0, 1):
            available = plots[name]["max"][axis] - plots[name]["min"][axis] - 8
            if available <= 0:
                raise ValueError("nozzle_profile_plot_does_not_fit")
            required = max(required, (bounds[name]["max"][axis] - bounds[name]["min"][axis]) / available)
    recommended = _engineering_scale(required)
    denominator = options.get("scale_denominator", recommended)
    if denominator < required:
        raise NozzleOutlineScaleDoesNotFit(denominator, required, recommended)
    plan = {"width": width, "height": height, "scale_denominator": denominator, "model_bounds": bounds,
            "view_plots": plots, "profile_layout": True, "table_capacity": capacity, "table_bottom": table_bottom,
            "table_top": table_top, "table_header": table_header, "row_height": row_height}
    scenes = [_profile_page(page, options, frame, plan, projection, number + 1, len(pages), len(groups))
              for number, page in enumerate(pages)]
    _digest(scenes)
    return scenes


def _profile_page(groups, options, frame, plan, projection, number, count, total_nozzles):
    width, height, denominator = plan["width"], plan["height"], plan["scale_denominator"]
    strokes, texts, views, callouts = [], [], [], []
    placement = _place_outline_geometry(strokes, projection, plan)
    def stroke(a, b, role="feature", name=None, **metadata):
        if a != b:
            strokes.append({"start": list(a), "end": list(b), "role": role, "view_name": name,
                            "visibility": "visible", **metadata})
    def text(at, value, size=2.4, role="sheet_metadata", name=None, **metadata):
        texts.append({"position": list(at), "text": value, "height": size, "rotation": 0,
                      "role": role, "view_name": name, **metadata})
    for a, b in (((8, 8), (width - 8, 8)), ((width - 8, 8), (width - 8, height - 8)),
                 ((width - 8, height - 8), (8, height - 8)), ((8, height - 8), (8, 8))):
        stroke(a, b, "border")
    label = format(denominator, ".9f").rstrip("0").rstrip(".")
    if label == "0" or len(label) > 24:
        raise ValueError("nozzle_outline_scale_label_below_precision")
    display = (frame.get("element_name") or frame["requested_refno"]).upper()
    if len(display) > 30 or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .-+/:_" for character in display):
        display = "EQUIPMENT SRC"
    text((width / 2, height - 12), options["title"], 3.8, "title")
    text((width / 2, height - 20), f"{display} / MAIN SCALE 1:{label} / LOCAL MM / {total_nozzles} NOZZLES / PAGE {number}/{count}", 2.4)
    v_label = ("+" if frame["v_axis_sign"] > 0 else "-") + frame["v_axis"]
    text((width / 2, height - 27), f"N +{frame['axial_axis']} / U +{frame['zero_axis']} / V {v_label} / +U TO +V ABOUT +N", 2.2)
    markers = set()
    for projected_view, vertical in zip(projection["views"], ("v", "h"), strict=True):
        name, semantic = projected_view["renderer_view"], projected_view["name"]
        outline_visibility = {item["refno"]: item for item in projected_view["nozzle_profile_visibility"]}
        plot, bounds = plan["view_plots"][name], plan["model_bounds"][name]
        span = [bounds["max"][axis] - bounds["min"][axis] for axis in (0, 1)]
        origin = [plot["min"][axis] + (plot["max"][axis] - plot["min"][axis] - span[axis] / denominator) / 2 for axis in (0, 1)]
        def project(point):
            return [origin[axis] + (point[axis] - bounds["min"][axis]) / denominator for axis in (0, 1)]
        center_x = (plot["min"][0] + plot["max"][0]) / 2
        text((center_x, height - 34), "RADIAL +U RIGHT / +V UP" if name == "TOP" else "AXIAL +U RIGHT / +H UP", 2.6, "view_label", name)
        equipment_origin = project((0, 0))
        ox, oy = equipment_origin
        stroke((ox - .7, oy), (ox + .7, oy), "dimension", name, annotation_kind="equipment_origin")
        stroke((ox, oy - .7), (ox, oy + .7), "dimension", name, annotation_kind="equipment_origin")
        text((ox - 2, oy - 2), "O", 2.0, name=name, annotation_kind="equipment_origin_label")
        buckets, view_callouts, occupied = {}, [], []
        for group in groups:
            profile, end_row, start_row = group["profile"], group["end"], group["start"]
            outline = outline_visibility[profile["resolved_refno"]]
            marker = _OUTLINE_MARKERS.get(outline["status"], "")
            if marker:
                markers.add(marker)
            label_text = profile["label"] + marker
            end = project([end_row["equipment_coordinates_mm"][axis] for axis in ("u", vertical)])
            start = project([start_row["equipment_coordinates_mm"][axis] for axis in ("u", vertical)])
            common = {"nozzle_id": profile["label"], "nozzle_refno": profile["resolved_refno"],
                      "point_source_indices": list(profile["source_indices"])}
            stroke(start, end, "dimension", name, annotation_kind="nozzle_native_axis", **common)
            stroke((end[0] - .45, end[1]), (end[0] + .45, end[1]), "dimension", name,
                   annotation_kind="nozzle_connection_point", **common)
            stroke((end[0], end[1] - .45), (end[0], end[1] + .45), "dimension", name,
                   annotation_kind="nozzle_connection_point", **common)
            key = tuple(round(value, 3) for value in end)
            lane = buckets.get(key, 0)
            buckets[key] = lane + 1
            offset = 0 if lane == 0 else (1 if lane % 2 else -1) * ((lane + 1) // 2) * 5
            sign = 1 if end_row["equipment_coordinates_mm"]["u"] >= 0 else -1
            half_width = max(2, (len(label_text) * 6 - 1) * 2.6 / 14)
            label_margin = max(10, half_width + 1.5)
            label_offset = max(14, label_margin + 3)
            label_x = max(plot["min"][0] + label_margin,
                          min(plot["max"][0] - label_margin, end[0] + sign * label_offset))
            label_y = max(plot["min"][1] + 3, min(plot["max"][1] - 3, end[1] + offset + 3))
            alternatives = [label_y, *[label_y + sign * distance for distance in range(5, 81, 5) for sign in (1, -1)]]
            label_y = next((candidate for candidate in alternatives
                if plot["min"][1] + 3 <= candidate <= plot["max"][1] - 3
                and all(abs(candidate - old_y) >= 4.5 or abs(label_x - old_x) >= half_width + old_half + 1
                        for old_x, old_y, old_half in occupied)), None)
            if label_y is None:
                raise ValueError("nozzle_profile_labels_do_not_fit")
            occupied.append((label_x, label_y, half_width))
            approach = 1 if end[0] < label_x else -1
            leader_end = [label_x - approach * (half_width + 1.0), label_y]
            stroke(end, leader_end, "dimension", name, annotation_kind="nozzle_leader", **common)
            text((label_x, label_y), label_text, 2.6, name=name, annotation_kind="nozzle_label",
                 outline_status=outline["status"], **common)
            callout = {**common, "view_name": name, "semantic_view": semantic, "anchor_mm": end,
                       "position_mm": [label_x, label_y], "leader_end_mm": leader_end,
                       "label_text": label_text, "outline_status": outline["status"]}
            callouts.append(callout)
            view_callouts.append(deepcopy(callout))
        views.append({"name": name, "semantic_name": semantic, "horizontal_axis": "u", "vertical_axis": vertical,
            "source_indices": [row["source_index"] for group in groups for row in group["rows"]],
            "model_bounds": deepcopy(bounds), "plot_bounds_mm": deepcopy(plot), "scale_denominator": denominator,
            "paper_mm_per_model_mm": 1 / denominator, "paper_origin_mm": origin,
            "equipment_origin_paper_mm": equipment_origin, "callouts": view_callouts,
            "nozzle_profile_visibility": [deepcopy(outline_visibility[group["profile"]["resolved_refno"]])
                                          for group in groups],
            "transform": {"scale_denominator": denominator, "paper_mm_per_model_mm": 1 / denominator,
                          "model_origin_mm": list(bounds["min"]), "paper_origin_mm": origin}})
    table_height = 2.5 if options["size"] == "A3" else 2.2
    top, header, row_height = plan["table_top"], plan["table_header"], plan["row_height"]
    stroke((8, top), (width - 8, top), "title_block")
    for index in range(plan["table_capacity"] + 1):
        y = top - header - index * row_height
        stroke((8, y), (width - 8, y), "title_block")
    text((width / 2, top - header / 2), _profile_table(_PROFILE_COLUMNS), table_height)
    nozzle_rows = []
    for index, group in enumerate(groups):
        profile, row = group["profile"], group["end"]
        values = (profile["label"], row["reference_display"],
            "NA" if profile["nominal_bore_mm"] is None else _fixed(profile["nominal_bore_mm"], 1),
            _fixed(profile["stations"][0]["outer_diameter_mm"], 2),
            _fixed(max(station["outer_diameter_mm"] for station in profile["stations"]), 2),
            _fixed(profile["axis_length_mm"], 2), _fixed(row["axial_mm"], 3),
            row["table_values"]["position_angle_deg"], row["table_values"]["direction_azimuth_deg"],
            row["table_values"]["direction_tilt_deg"])
        content = _profile_table(values)
        text((width / 2, top - header - (index + .5) * row_height), content, table_height,
             annotation_kind="nozzle_schedule_row", nozzle_id=profile["label"])
        nozzle_rows.append({"id": profile["label"], "resolved_refno": profile["resolved_refno"],
            "source_indices": list(profile["source_indices"]), "table_row_text": content,
            "nominal_bore_mm": profile["nominal_bore_mm"], "axis_length_mm": profile["axis_length_mm"],
            "stations": deepcopy(profile["stations"]), "dimension_evidence": deepcopy(profile["dimension_evidence"])})
    legend = "OD FROM DIMENSION EVIDENCE / BORE IS NOMINAL PPBO / ANG FROM +U TOWARD +V"
    if " HID" in markers:
        legend += " / HID: OCCLUDED"
    if " N/O" in markers:
        legend += " / N/O: NO VISIBLE OUTLINE"
    text((width / 2, 23), legend, 1.85, "disclaimer")
    text((width / 2, 17), "SELECTED PARAMETER OUTLINE / NATIVE CUT SCOPE UNVERIFIED / O NATIVE POS", 1.85, "disclaimer")
    text((width / 2, 11), options["drawing_number"], 2.3, "drawing_number")
    page_rows = [deepcopy(row) for group in groups for row in group["rows"]]
    scene = {"success": True, "available": True, "complete": True, "coordinate_space": "paper", "coordinate_unit": "mm",
        "model_coordinate_unit": "mm", "model_coordinate_frame": "native equipment local axes", "native_draft_sheet": False,
        "native_world_frame_refno": frame["native_world_frame_refno"], "native_world_frame_complete": True,
        "paper": {"size": options["size"], "orientation": "landscape", "width_mm": width, "height_mm": height},
        "paper_size_mm": [width, height], "page_number": number, "page_count": count,
        "source_rows": page_rows, "source_indices": [row["source_index"] for row in page_rows],
        "nozzle_rows": nozzle_rows, "nozzle_profile_mode": True, "nozzle_profile_count": len(groups), "total_nozzle_count": total_nozzles,
        "equipment_frame": deepcopy(frame), "options": deepcopy(options), "scale_denominator": denominator,
        "scale_mode": "explicit" if "scale_denominator" in options else "shared_engineering_auto_fit",
        "shared_layout": {"common_scale": True, "common_model_bounds": True, "common_transforms": True,
                          "model_bounds": deepcopy(plan["model_bounds"]), "table_rows": plan["table_capacity"]},
        "views": views, "strokes": strokes, "texts": texts, "callouts": callouts,
        "table": {"columns": list(_PROFILE_COLUMNS), "row_capacity": plan["table_capacity"],
            "coordinate_frame": "native equipment local axes", "world_positions_retained_in": "source_rows.position",
            "world_directions_retained_in": "source_rows.direction", "one_row_per_resolved_nozzle": True},
        "counts": {"points": len(page_rows), "nozzles": len(groups), "views": 2, "strokes": len(strokes), "texts": len(texts)}}
    _annotate_outline_scene(scene, projection, placement)
    scene.update(nozzle_profile_proof_sha256=projection["nozzle_profile_sha256"],
                 projection_geometry_sha256=projection["projection_geometry_sha256"],
                 outline_source_ranges=deepcopy(projection["outline_source_ranges"]),
                 native_point_labels_grouped_by_resolved_nozzle=True,
                 physical_geometry_to_model_scale=True, direction_symbols_to_model_scale=True,
                 fixed_length_direction_arrows=False, radial_origin_rays=False)
    scene["nozzle_profile_approximation"] = [{"refno": profile["resolved_refno"],
        "radial_segments": profile["radial_segments"], "chord_tolerance_mm": profile["chord_tolerance_mm"],
        "max_radial_chord_error_mm": profile["max_radial_chord_error_mm"],
        "max_paper_chord_error_mm": profile["max_radial_chord_error_mm"] / denominator}
        for profile in projection["nozzle_profiles"]]
    scene["source_attribution"].update(
        source_line_index_scope="Per semantic projection view; source_index indexes outline_source_ranges, including selected material and separate catalogue nozzle profiles.",
        material_source_count=len(projection["source_ranges"]),
        nozzle_profile_source_count=len(projection["nozzle_profiles"]),
        selected_material_membership_scope="explicit_source_membership_verified applies to original selected material source_ranges only; catalogue profiles use native nozzle point and catalogue evidence separately.")
    scene["outline_source_membership_scope"] = "Selected material source_ranges only; generated nozzle profiles retain separate native point and catalogue evidence."
    for item in scene["texts"]:
        if item.get("role") == "disclaimer" and item["position"][1] == 17:
            item["text"] = "BARE CATALOGUE OD / MAIN GEOMETRY TO SCALE / NATIVE CUTS UNVERIFIED"
    validate_drawing_sheet_scene(scene)
    return scene


def _annotate_outline_scene(scene, projection, placement):
    for view in scene["views"]:
        view.update(deepcopy(placement[view["name"]]), renderer_view_identifier_only=True,
                    coordinate_frame=_FRAME, coordinate_unit="mm")
    scene.update({key: deepcopy(projection[key]) for key in _PROOF_FIELDS})
    scene.update(drawing_kind="selected parameter nozzle outline sheet",
        source_coordinate_space="WORLD", source_coordinate_frame=WORLD_FRAME,
        geometry_scope="selected parameter material sources", whole_equipment_geometry_verified=False,
        explicit_source_membership_verified=False, equipment_extents_verified=False,
        source_proof_sha256=projection["source_proof_sha256"],
        global_linework_sha256=projection["global_linework_sha256"], global_linework_hash_schema=_SCHEMA,
        hidden_line_removal=projection["hidden_line_removal"], show_hidden_lines=projection["show_hidden_lines"],
        outline_projection={key: deepcopy(projection[key]) for key in (
            "coordinate_frame", "basis_world", "origin_world_mm", "feature_angle_degrees",
            "visibility_scope", "visibility_work_budgets", "occlusion_checked", "native_cut_scope")},
        coverage={"complete": True, "input_line_count": projection["segment_count"],
                  "output_line_count": sum(item["geometry_stroke_range"][1] - item["geometry_stroke_range"][0] for item in placement.values()),
                  "views": deepcopy(placement), "cropping_applied": False, "visibility_recomputed": False,
                  "all_selected_material_repeated_on_each_page": True},
        source_attribution={"representation": "structured scene geometry strokes",
            "source_line_index_scope": "per semantic projection view; source_index for geometry refers to material source_ranges",
            "nozzle_source_index_scope": "source_rows; independent of material source_ranges",
            "vector_source_metadata_embedded": False},
        serialization_precision={"coordinate_decimal_places": _PRECISION, "coordinate_unit": "paper mm",
            "engineering_accuracy_claim": False},
        scope="Nozzle PPOS and PDIR in selected native equipment axes with selected parameter material outlines; selected source membership is documented separately. Whole-equipment geometry, physical center, centerline, native cuts and nozzle ownership are unverified.")
    scene["counts"].update(material_sources=len(projection["source_ranges"]),
                            geometry_strokes=projection["segment_count"])
    scene["shared_layout"].update(bounds_include_all_selected_material=True,
                                  bounds_include_all_nozzle_points_and_native_pos=True)
    for text in scene["texts"]:
        if text.get("role") == "disclaimer" and text["position"][1] == 17:
            text["text"] = "SELECTED PARAMETER OUTLINE / NATIVE CUT SCOPE UNVERIFIED / SYMBOLS NOT TO SCALE"


def build_nozzle_outline_sheets(drawing: Any, equipment_pose: Any, combined_material: Any,
                                sheet_options: Any, *, axial_axis: str, zero_axis: str,
                                feature_angle_degrees: float = 30,
                                hidden_line_removal: bool = True,
                                show_hidden_lines: bool = False,
                                nozzle_profiles: Any = None,
                                chord_tolerance_mm: float | None = None) -> dict[str, Any]:
    """Return ``{projection, sheets}`` or raise ValueError on incomplete evidence.

    ``combined_material`` must be the unmodified complete WORLD/mm receipt from
    combine_material_drawing_meshes. RADIAL uses (u,v,n); AXIAL_ELEVATION uses
    (u,n,-v). TOP/FRONT in paper scenes are renderer identifiers only. The
    returned projection retains the revalidated source WORLD arrays and proof;
    sheets retain proof and exact source-line mappings without copying arrays.
    Membership and ownership require the caller's separate native proof.
    Optional ``nozzle_profiles`` adds independently evidenced outer-diameter
    stations along two native connection points and groups point labels by
    nozzle. PPBO is retained as a nominal table value only. Positive
    ``chord_tolerance_mm`` selects a multiple of four from 12..128 profile
    segments; omitting it retains 32. No flange standard or bore-to-OD rule is
    inferred. Only outline sheets accept optional explicit scale_denominator.
    """
    frame = _equipment_frame(equipment_pose, axial_axis, zero_axis)
    profiles = _prepare_profiles(nozzle_profiles, drawing, frame, chord_tolerance_mm) if nozzle_profiles is not None else None
    projection = _project_material_outline(combined_material, frame, feature_angle_degrees,
                                           hidden_line_removal, show_hidden_lines, profiles)
    sheets = build_nozzle_axis_sheets(drawing, equipment_pose, sheet_options,
        axial_axis=axial_axis, zero_axis=zero_axis, outline_projection=projection)
    return {"projection": projection, "sheets": sheets}
