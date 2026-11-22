"""Verified owner-member parameter differences, never native tessellation.

The input is an unchanged complete native primitive-geometry receipt. Ordinary
positive operands and flat negative tools are derived independently, transformed
from their observed WORLD frames, and differenced in an isolated Mesh64 worker.
The native SDK does not expose every negative-application scope: the declared
owner-member interpretation remains separate from the computed solid Boolean.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any

from .primitive_mesh import _box_mesh, _round_mesh, _spherical_dish_mesh, _unit_normal, _world_transform, plan_primitive_tessellation
from .triangle_mesh_validation import validate_triangle_mesh


MAX_VERTICES = 10_000
MAX_TRIANGLES = 20_000
MAX_CUTTERS = 32
_MAX_ADAPTIVE_RADIAL_SEGMENTS = 256
MAX_IPC_BYTES = 8 * 1024 * 1024
WORKER_TIMEOUT_SECONDS = 30
WORLD_FRAME = "WORLD coordinates; millimetres"
CSG_SCOPE = "owner-member parameter difference; native application scope unverified"
MATERIAL_PROVENANCE = "manifold3d Mesh64 subtraction of validated positive closed operands"
_MAX_COORDINATE_MM = 1_000_000_000.0
_POSITION_TOLERANCE_MM = 1e-5
_DIRECTION_TOLERANCE = 1e-6
# A guarded thin wall is only admitted when the cutter geometry can be bounded
# against the positive cylinder in WORLD space.  The reserve scales with the
# largest reported length so the proof does not rely on a sub-ULP clearance.
_THIN_WALL_GUARD_RESERVE_FACTOR = 64
_THIN_WALL_AXIS_TILT_TOLERANCE = _DIRECTION_TOLERANCE
_SHAPES = {"BOX": ("XLEN", "YLEN", "ZLEN"), "CYLI": ("DIAM", "HEIG"),
           "CONE": ("DBOT", "DTOP", "HEIG"), "DISH": ("DIAM", "HEIG", "RADI")}
_NEGATIVE_TYPES = {"NBOX": "BOX", "NCYL": "CYLI", "NCON": "CONE"}


class CSGWorkerError(ValueError):
    def __init__(self, code: str, diagnostics: Any = None):
        self.code = code
        self.diagnostics = diagnostics
        super().__init__(code)


def _reject(code: str, **diagnostics: Any):
    raise CSGWorkerError(code, diagnostics or None)


def csg_geometry_sha256(vertices: Any, triangles: Any) -> str:
    """Hash only the exact indexed geometry, including its supplied ordering."""
    payload = json.dumps({"vertices": vertices, "triangles": triangles}, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _reference(value: Any) -> tuple[str, str]:
    match = re.fullmatch(r"=([0-9]+)/([0-9]+)", value) if isinstance(value, str) and len(value) <= 128 else None
    if match is None:
        _reject("csg_numeric_reference_required")
    return tuple(part.lstrip("0") or "0" for part in match.groups())  # type: ignore[return-value]


def _number(value: Any) -> float:
    if type(value) not in {int, float}:
        _reject("csg_invalid_dimension")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        _reject("csg_invalid_dimension")
    if not math.isfinite(number) or abs(number) > _MAX_COORDINATE_MM:
        _reject("csg_invalid_dimension")
    return number


def _transform(value: Any, field: str) -> tuple[list[float], list[list[float]]]:
    result = _world_transform({"world": value})
    if result is None or any(abs(number) > _MAX_COORDINATE_MM for number in result[0]):
        _reject("csg_invalid_frame", field=field)
    return result


def _placed(point, position, axes):
    return [math.fsum((position[coordinate], *(axes[axis][coordinate] * point[axis] for axis in range(3))))
            for coordinate in range(3)]


def _plan_world_operand_tessellation(node, radial_segments: int, tolerance_mm: float) -> dict[str, Any]:
    axes = node["world_frame"][1]
    if all(value in (-1.0, 0.0, 1.0) for axis in axes for value in axis):
        scale_upper = 1.0  # Already validated; signed axis permutations are exact.
    else:
        # The maximum absolute row sum of A^T A bounds the squared distance
        # amplification of the reported basis A, even with its admitted rounding.
        gram_rows = (math.fsum(abs(math.fsum(a * b for a, b in zip(left, right))) for right in axes)
                     for left in axes)
        scale_upper = math.sqrt(max(gram_rows))
        scale_upper = max(1.0, scale_upper + 4 * math.ulp(scale_upper))
    local_tolerance = tolerance_mm / scale_upper
    if local_tolerance * scale_upper > tolerance_mm:
        local_tolerance = math.nextafter(local_tolerance, 0.0)
    plan = plan_primitive_tessellation(node["shape"], node["dimensions"], radial_segments,
                                      local_tolerance, max_radial_segments=_MAX_ADAPTIVE_RADIAL_SEGMENTS)
    local_error = plan["max_surface_deviation_mm"]
    return {**plan, "chord_tolerance_mm": tolerance_mm,
            "max_surface_deviation_mm": local_error * scale_upper,
            "local_surface_deviation_bound_mm": local_error, "world_linear_scale_upper_bound": scale_upper,
            "error_bound_scope": "Analytic operand surface in WORLD before Boolean operations. "
                                 "Excludes floating-point placement, Boolean and native geometry errors."}


def _thin_wall_guard_reserve(*values: float) -> float:
    """Return a WORLD-space numerical reserve for the thin-wall proof."""
    scale = max((abs(value) for value in values if math.isfinite(value)), default=1.0)
    return max(1e-9, _THIN_WALL_GUARD_RESERVE_FACTOR * math.ulp(scale))


def _strict_basis_error(axes: list[list[float]]) -> float:
    lengths = [math.hypot(*axis) for axis in axes]
    dots = [math.fsum(a[index] * b[index] for index in range(3))
            for a, b in ((axes[0], axes[1]), (axes[0], axes[2]), (axes[1], axes[2]))]
    cross_xy = (axes[0][1] * axes[1][2] - axes[0][2] * axes[1][1],
                axes[0][2] * axes[1][0] - axes[0][0] * axes[1][2],
                axes[0][0] * axes[1][1] - axes[0][1] * axes[1][0])
    handedness = math.fsum(cross_xy[index] * axes[2][index] for index in range(3))
    return max(*(abs(length - 1.0) for length in lengths),
               *(abs(value) for value in dots), abs(handedness - 1.0))


def _guard_parallel_through_cylinder(root: dict[str, Any], cutters: list[dict[str, Any]],
                                     plans: list[dict[str, Any]], radial_segments: int,
                                     tolerance_mm: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Refine only a CYLI positive whose inscribed wall could be breached.

    The guard is deliberately a sufficient-condition check.  A child NCYL is
    considered only when its validated WORLD axis is nearly parallel to the
    positive axis and its projected axial span covers every point of the
    positive cylinder.  The cutter's radial envelope includes a conservative
    tilt term; the positive polygon's worst-case inward sagitta must then fit
    inside half the analytic radial clearance.  The same bound is applied to a
    cutter's inscribed profile so the represented bore remains accurate within
    the wall allowance.  Cases outside this proof retain the ordinary path.
    """
    if (root["shape"] != "CYLI" or not cutters
            or any(cutter["shape"] != "CYLI" or cutter["raw"]["element"].get("type") != "NCYL"
                   for cutter in cutters)):
        return plans, []
    parent_position, parent_axes = root["world_frame"]
    parent_basis_error = _strict_basis_error(parent_axes)
    if parent_basis_error > 1e-9:
        return plans, []
    parent_axis = parent_axes[2]
    parent_radius = root["dimensions"]["DIAM"] / 2.0
    parent_half_height = root["dimensions"]["HEIG"] / 2.0
    selected_segments = plans[0]["radial_segments"]
    selected_cutter_segments: dict[int, int] = {}
    obligations: list[dict[str, Any]] = []
    for cutter_offset, cutter in enumerate(cutters, start=1):
        cutter_position, cutter_axes = cutter["world_frame"]
        cutter_basis_error = _strict_basis_error(cutter_axes)
        if cutter_basis_error > 1e-9:
            return plans, []
        cutter_axis = cutter_axes[2]
        axis_dot = math.fsum(parent_axis[index] * cutter_axis[index] for index in range(3))
        axis_alignment = abs(axis_dot)
        axis_dot = max(-1.0, min(1.0, axis_dot))
        axis_sine = math.sqrt(max(0.0, 1.0 - axis_dot * axis_dot))
        if axis_sine > _THIN_WALL_AXIS_TILT_TOLERANCE:
            return plans, []
        delta = [cutter_position[index] - parent_position[index] for index in range(3)]
        axial_offset = math.fsum(delta[index] * parent_axis[index] for index in range(3))
        radial_delta = [delta[index] - axial_offset * parent_axis[index] for index in range(3)]
        planar_center_distance = math.hypot(*radial_delta)
        cutter_radius = cutter["dimensions"]["DIAM"] / 2.0
        cutter_half_height = cutter["dimensions"]["HEIG"] / 2.0
        geometry_scale = max(parent_radius, cutter_radius, parent_half_height,
                             cutter_half_height, planar_center_distance)
        reserve = max(_thin_wall_guard_reserve(parent_radius, cutter_radius, parent_half_height,
                                               cutter_half_height, planar_center_distance,
                                               *parent_position, *cutter_position),
                      _THIN_WALL_GUARD_RESERVE_FACTOR
                      * (parent_basis_error + cutter_basis_error + math.ulp(1.0)) * geometry_scale)

        # Project both finite cylinders conservatively onto the common parent
        # axis and radial plane.  The tilt terms cover the admitted tiny axis
        # mismatch instead of treating a merely near-parallel frame as exact.
        axial_coverage = (cutter_half_height * axis_alignment - abs(axial_offset)
                          - parent_half_height - parent_radius * axis_sine)
        if axial_coverage <= 0.0:
            # A blind cutter makes the whole CSG case ineligible for this
            # narrow through-hole guard.
            return plans, []
        if axial_coverage <= reserve:
            _reject("csg_thin_wall_guard_axial_margin_ambiguous", source_refno=root["refno"],
                    cutter_refno=cutter["refno"], axial_coverage_mm=axial_coverage,
                    reserve_mm=reserve)
        analytic_radial_clearance = parent_radius - planar_center_distance - cutter_radius
        # For the admitted tiny tilt, bound the cutter's radial envelope by
        # adding the maximum axial excursion into the parent radial plane.
        radial_clearance = analytic_radial_clearance - cutter_half_height * axis_sine
        if radial_clearance < -reserve:
            # A clearly open wall is not a thin positive wall; no guarantee is
            # claimed and the ordinary path remains available.
            return plans, []
        if radial_clearance <= reserve:
            _reject("csg_thin_wall_guard_clearance_ambiguous", source_refno=root["refno"],
                    cutter_refno=cutter["refno"], radial_clearance_mm=radial_clearance,
                    reserve_mm=reserve)

        guard_tolerance = (radial_clearance - reserve) / 2.0
        if not math.isfinite(guard_tolerance) or guard_tolerance <= 0.0:
            _reject("csg_thin_wall_guard_clearance_ambiguous", source_refno=root["refno"],
                    cutter_refno=cutter["refno"], radial_clearance_mm=radial_clearance,
                    reserve_mm=reserve)
        parent_needs_refinement = plans[0]["max_surface_deviation_mm"] > guard_tolerance
        cutter_needs_refinement = plans[cutter_offset]["max_surface_deviation_mm"] > guard_tolerance
        if not parent_needs_refinement and not cutter_needs_refinement:
            continue
        parent_candidate = None
        if parent_needs_refinement:
            try:
                parent_candidate = _plan_world_operand_tessellation(root, radial_segments, guard_tolerance)
            except (ValueError, ArithmeticError) as exc:
                if str(exc) == "chord_tolerance_exceeds_tessellation_budget":
                    _reject("csg_thin_wall_guard_tessellation_budget", source_refno=root["refno"],
                            cutter_refno=cutter["refno"], radial_clearance_mm=radial_clearance,
                            required_tolerance_mm=guard_tolerance,
                            max_radial_segments=_MAX_ADAPTIVE_RADIAL_SEGMENTS)
                _reject("csg_thin_wall_guard_planning_failed", source_refno=root["refno"],
                        cutter_refno=cutter["refno"], detail=str(exc)[:256])
            selected_segments = max(selected_segments, parent_candidate["radial_segments"])
        cutter_candidate = None
        if cutter_needs_refinement:
            try:
                cutter_candidate = _plan_world_operand_tessellation(cutter, radial_segments, guard_tolerance)
            except (ValueError, ArithmeticError) as exc:
                if str(exc) == "chord_tolerance_exceeds_tessellation_budget":
                    _reject("csg_thin_wall_guard_tessellation_budget", source_refno=root["refno"],
                            cutter_refno=cutter["refno"], radial_clearance_mm=radial_clearance,
                            required_tolerance_mm=guard_tolerance,
                            max_radial_segments=_MAX_ADAPTIVE_RADIAL_SEGMENTS)
                _reject("csg_thin_wall_guard_planning_failed", source_refno=root["refno"],
                        cutter_refno=cutter["refno"], detail=str(exc)[:256])
            selected_cutter_segments[cutter_offset] = max(
                plans[cutter_offset]["radial_segments"], cutter_candidate["radial_segments"])
        obligations.append({
            "cutter_refno": cutter["refno"],
            "analytic_radial_clearance_mm": analytic_radial_clearance,
            "radial_clearance_mm": radial_clearance,
            "numerical_reserve_mm": reserve,
            "axial_coverage_margin_mm": axial_coverage,
            "required_tolerance_mm": guard_tolerance,
            "positive_refinement_required": parent_needs_refinement,
            "cutter_refinement_required": cutter_needs_refinement,
            "required_positive_radial_segments": (parent_candidate["radial_segments"]
                                                   if parent_candidate is not None else plans[0]["radial_segments"]),
            "required_cutter_radial_segments": (cutter_candidate["radial_segments"]
                                                if cutter_candidate is not None else plans[cutter_offset]["radial_segments"]),
        })

    if not obligations:
        return plans, obligations
    if selected_segments == plans[0]["radial_segments"]:
        guarded_plan = plans[0]
    else:
        try:
            guarded_plan = _plan_world_operand_tessellation(root, selected_segments, tolerance_mm)
        except (ValueError, ArithmeticError) as exc:
            if str(exc) == "chord_tolerance_exceeds_tessellation_budget":
                _reject("csg_thin_wall_guard_tessellation_budget", source_refno=root["refno"],
                        required_radial_segments=selected_segments,
                        max_radial_segments=_MAX_ADAPTIVE_RADIAL_SEGMENTS)
            _reject("csg_thin_wall_guard_planning_failed", source_refno=root["refno"], detail=str(exc)[:256])
        plans[0] = guarded_plan
    guarded_plan["mode"] = "chord_tolerance_with_thin_wall_guard"
    guarded_plan["requested_radial_segments"] = radial_segments
    guarded_metadata = {
        "enabled": True,
        "proof": "positive and cutter inscribed radial sagittas are each at most half the conservative radial clearance",
        "obligations": obligations,
    }
    guarded_plan["thin_wall_guard"] = guarded_metadata
    for cutter_offset, cutter_segments in selected_cutter_segments.items():
        cutter_plan = plans[cutter_offset]
        if cutter_segments != cutter_plan["radial_segments"]:
            cutter_plan = _plan_world_operand_tessellation(cutters[cutter_offset - 1], cutter_segments,
                                                           tolerance_mm)
            plans[cutter_offset] = cutter_plan
        cutter_plan["mode"] = "chord_tolerance_with_thin_wall_guard"
        cutter_plan["requested_radial_segments"] = radial_segments
        cutter_plan["thin_wall_guard"] = guarded_metadata
    return plans, obligations


def _position_tolerance(*positions):
    largest = max((abs(value) for position in positions for value in position), default=0.0)
    return max(_POSITION_TOLERANCE_MM, 64 * math.ulp(largest))


def _same_transform(first, second):
    return (math.dist(first[0], second[0]) <= _position_tolerance(first[0], second[0])
            and all(math.dist(a, b) <= _DIRECTION_TOLERANCE for a, b in zip(first[1], second[1])))


def _validate_node(node: Any, *, negative: bool) -> dict[str, Any]:
    if not isinstance(node, Mapping):
        _reject("csg_invalid_native_node")
    if node.get("complete") is not True or not isinstance(node.get("read_errors"), Mapping) or node["read_errors"]:
        _reject("csg_native_node_incomplete")
    if (not isinstance(node.get("edit_reasons"), list) or node["edit_reasons"]
            or node.get("unit") != "mm" or node.get("negative") is not negative
            or node.get("is_negative") is not negative):
        _reject("csg_native_node_admission_rejected")
    identity = node.get("element")
    if not isinstance(identity, Mapping):
        _reject("csg_native_identity_unavailable")
    reference = _reference(identity.get("refno"))
    native_type, shape = identity.get("type"), node.get("shape")
    if not isinstance(native_type, str) or not isinstance(shape, str):
        _reject("csg_unsupported_shape")
    expected = _NEGATIVE_TYPES.get(native_type) if negative else native_type
    if expected not in _SHAPES or shape != expected or negative and native_type not in _NEGATIVE_TYPES:
        _reject("csg_unsupported_shape")
    dimensions = node.get("dimensions")
    if not isinstance(dimensions, Mapping) or set(dimensions) != set(_SHAPES[shape]):
        _reject("csg_invalid_dimensions")
    values = {name: _number(dimensions[name]) for name in _SHAPES[shape]}
    if shape == "CONE":
        valid = values["HEIG"] > 0 and min(values["DBOT"], values["DTOP"]) >= 0 and max(values["DBOT"], values["DTOP"]) > 0
    elif shape == "DISH":
        valid = values["DIAM"] > 0 and values["HEIG"] > 0 and values["RADI"] == 0
    else:
        valid = all(value > 0 for value in values.values())
    if not valid:
        _reject("csg_invalid_dimensions")
    frame = node.get("frame")
    if not isinstance(frame, Mapping):
        _reject("csg_invalid_frame")
    storage = _reference(frame.get("storage_frame_refno"))
    native_frame = _transform(frame.get("native"), "native")
    world_frame = _transform(frame.get("world"), "world")
    children, members = node.get("negative_children"), node.get("member_refnos")
    if not isinstance(children, list) or not isinstance(members, list):
        _reject("csg_native_members_unavailable")
    if "children" in node and (not isinstance(node["children"], list) or node["children"]):
        _reject("csg_unsupported_children")
    return {"raw": node, "refno": identity["refno"], "reference": reference, "shape": shape,
            "dimensions": values, "storage_reference": storage, "native_frame": native_frame,
            "world_frame": world_frame, "children": children, "members": members}


def _operand(node, anchor, radial_segments, *, latitude_segments=None):
    values, shape = node["dimensions"], node["shape"]
    if shape == "BOX":
        vertices, triangles, _ = _box_mesh(values["XLEN"], values["YLEN"], values["ZLEN"])
    elif shape == "CYLI":
        vertices, triangles, _ = _round_mesh(values["DIAM"], values["DIAM"], values["HEIG"], radial_segments)
    elif shape == "CONE":
        vertices, triangles, _ = _round_mesh(values["DBOT"], values["DTOP"], values["HEIG"], radial_segments)
    else:
        vertices, triangles, _ = _spherical_dish_mesh(values["DIAM"], values["HEIG"], radial_segments,
                                                   latitude_segments=latitude_segments)
    position, axes = node["world_frame"]
    offset = [position[axis] - anchor[axis] for axis in range(3)]
    vertices = [_placed(point, offset, axes) for point in vertices]
    if any(not math.isfinite(value) or abs(value) > 2 * _MAX_COORDINATE_MM for point in vertices for value in point):
        _reject("csg_operand_coordinate_limit_exceeded")
    validation = validate_triangle_mesh(vertices, triangles)
    if validation.get("valid") is not True or validation.get("complete") is not True:
        _reject("csg_operand_mesh_invalid", source_refno=node["refno"], validation=validation)
    return {"vertices": vertices, "triangles": triangles}, validation


def _run_csg_worker(operands: list[dict[str, Any]]) -> dict[str, Any]:
    """Run once with file IPC, bounded bytes and a hard process timeout."""
    encoded = json.dumps({"operands": operands}, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_IPC_BYTES:
        _reject("csg_worker_input_limit_exceeded")
    with tempfile.TemporaryDirectory(prefix="aveva-csg-") as directory:
        input_path, output_path = Path(directory) / "input.json", Path(directory) / "output.json"
        input_path.write_bytes(encoded)
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("primitive_csg_worker.py")), str(input_path), str(output_path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=WORKER_TIMEOUT_SECONDS, check=False, **kwargs)
        if completed.returncode != 0 or not output_path.is_file():
            _reject("csg_worker_failed", returncode=completed.returncode)
        with output_path.open("rb") as stream:
            encoded = stream.read(MAX_IPC_BYTES + 1)
        if len(encoded) > MAX_IPC_BYTES:
            _reject("csg_worker_output_limit_exceeded")
        try:
            result = json.loads(encoded)
        except (ValueError, UnicodeDecodeError):
            _reject("csg_worker_invalid_response")
    if not isinstance(result, dict) or type(result.get("success")) is not bool:
        _reject("csg_worker_invalid_response")
    if result["success"] is not True:
        raise CSGWorkerError(result.get("error_code", "csg_worker_calculation_failed"), result)
    return result


def _empty_validation():
    return {"success": True, "available": True, "complete": True, "valid": True, "empty": True,
            "counts": {"vertices": 0, "triangles": 0, "valid_triangles": 0},
            "checks": {"closed_manifold": True, "winding_consistent": True,
                       "orientation_outward": None, "component_count": 0},
            "components": [], "findings": [],
            "scope": "empty result of successful manifold difference; no boundary shell"}


def _failure(native, code, diagnostics=None):
    identity = native.get("element") if isinstance(native, Mapping) else None
    reference = identity.get("refno") if isinstance(identity, Mapping) else None
    reference = reference[:128] if isinstance(reference, str) else None
    if not isinstance(code, str) or not code or len(code) > 128:
        code = "csg_worker_invalid_error_response"
    if diagnostics is not None:
        try:
            encoded = json.dumps(diagnostics, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            if len(encoded) > 64 * 1024:
                diagnostics = {"omitted": "oversized failure diagnostics", "original_bytes": len(encoded)}
        except (ValueError, TypeError, OverflowError):
            diagnostics = {"omitted": "non-JSON failure diagnostics"}
    return {"success": False, "available": False, "complete": False, "empty": False,
            "errors": [code], "error_code": code, "diagnostics": diagnostics,
            "refno": reference,
            "coordinate_space": "WORLD", "coordinate_frame": WORLD_FRAME, "coordinate_unit": "mm",
            "native_tessellation": False, "cuts_applied": False, "csg_evaluated": False,
            "material_boundary_validated": False, "native_cut_application_verified": False,
            "scope": CSG_SCOPE, "vertices": [], "triangles": [], "triangle_normals": [],
            "counts": {"vertices": 0, "triangles": 0}, "atomic_snapshot": False}


def derive_primitive_csg_mesh(native: Mapping[str, Any], *, radial_segments: int = 48,
                              chord_tolerance_mm: float | None = None) -> dict[str, Any]:
    """Derive a bounded material boundary for one positive with flat cut tools.

    The original native negative flags, frames and member order are never
    rewritten. Correct enclosed cavity shells keep their inward winding. An
    entirely removed solid is a successful explicitly empty Boolean result.
    No native volume observation is used to fit or alter source dimensions.
    """
    try:
        if type(radial_segments) is not int or not 12 <= radial_segments <= 99:
            _reject("csg_radial_segments_out_of_range")
        if chord_tolerance_mm is not None and (type(chord_tolerance_mm) not in {int, float}
                or not math.isfinite(chord_tolerance_mm) or chord_tolerance_mm <= 0):
            _reject("csg_invalid_chord_tolerance_mm")
        if not isinstance(native, Mapping):
            _reject("csg_invalid_native_geometry")
        try:
            encoded = json.dumps(native, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, OverflowError):
            _reject("csg_invalid_native_geometry")
        if len(encoded) > MAX_IPC_BYTES:
            _reject("csg_native_input_limit_exceeded")
        if (native.get("success") is not True or native.get("complete") is not True
                or native.get("partial") is not False or native.get("negative") is not False
                or native.get("is_negative") is not False or native.get("unit") != "mm"
                or native.get("editable") is not True or not isinstance(native.get("edit_reasons"), list)
                or native["edit_reasons"] or not isinstance(native.get("read_errors"), Mapping) or native["read_errors"]):
            _reject("csg_native_geometry_incomplete_or_ineligible")
        if not isinstance(native.get("revision"), str) or re.fullmatch(r"[0-9a-fA-F]{64}", native["revision"]) is None:
            _reject("csg_native_revision_required")
        root_raw = native.get("target_geometry")
        if not isinstance(root_raw, Mapping):
            _reject("csg_target_geometry_required")
        for field in ("element", "shape", "negative", "is_negative", "unit", "dimensions", "frame", "negative_children"):
            if field not in native or native[field] != root_raw.get(field):
                _reject("csg_target_geometry_mismatch", field=field)
        root = _validate_node(root_raw, negative=False)
        owner = native.get("owner")
        if not isinstance(owner, Mapping):
            _reject("csg_owner_identity_unavailable")
        owner_reference = _reference(owner.get("refno"))
        if owner_reference == root["reference"] or root["storage_reference"] == root["reference"]:
            _reject("csg_owner_identity_mismatch")
        if root_raw["frame"].get("relative_to_owner") is not None:
            _reject("csg_root_relative_frame_unexpected")
        if native.get("negative_owner") is not None:
            _reject("csg_unexpected_negative_owner")
        if "member_refnos" in native and native["member_refnos"] != root["members"]:
            _reject("csg_member_reference_mismatch")
        if "children" in native and (not isinstance(native["children"], list) or native["children"]):
            _reject("csg_unsupported_children")
        owner_members = native.get("owner_member_refnos")
        if not isinstance(owner_members, list) or not 1 <= len(owner_members) <= 10_000:
            _reject("csg_owner_membership_unavailable")
        owner_keys = [_reference(value) for value in owner_members]
        if len(set(owner_keys)) != len(owner_keys) or owner_keys.count(root["reference"]) != 1:
            _reject("csg_owner_membership_mismatch")
        if not 1 <= len(root["children"]) <= MAX_CUTTERS:
            _reject("csg_cutter_limit_exceeded")
        if len(root["members"]) != len(root["children"]):
            _reject("csg_member_reference_mismatch")
        nodes, seen = [root], {root["reference"]}
        for offset, child in enumerate(root["children"]):
            node = _validate_node(child, negative=True)
            if node["reference"] in seen:
                _reject("csg_duplicate_reference")
            if node["reference"] == owner_reference or node["reference"] in owner_keys:
                _reject("csg_child_owner_membership_mismatch")
            seen.add(node["reference"])
            if _reference(root["members"][offset]) != node["reference"]:
                _reject("csg_member_reference_mismatch")
            if node["children"] or node["members"]:
                _reject("csg_nested_negative_unsupported")
            if node["storage_reference"] != root["reference"]:
                _reject("csg_child_storage_frame_mismatch")
            relative = _transform(child["frame"].get("relative_to_owner"), "relative_to_owner")
            if not _same_transform(relative, node["native_frame"]):
                _reject("csg_child_native_relative_frame_mismatch")
            parent_position, parent_axes = root["world_frame"]
            expected_position = _placed(relative[0], parent_position, parent_axes)
            expected_axes = [[math.fsum(parent_axes[axis][coordinate] * vector[axis] for axis in range(3))
                              for coordinate in range(3)] for vector in relative[1]]
            if not _same_transform((expected_position, expected_axes), node["world_frame"]):
                _reject("csg_child_world_frame_mismatch")
            nodes.append(node)
        anchor = root["world_frame"][0]
        plans: list[dict[str, Any] | None] = [None] * len(nodes)
        thin_wall_obligations: list[dict[str, Any]] = []
        if chord_tolerance_mm is not None:
            for index, node in enumerate(nodes):
                try:
                    plans[index] = _plan_world_operand_tessellation(node, radial_segments,
                                                                    float(chord_tolerance_mm))
                except (ValueError, ArithmeticError) as exc:
                    _reject("csg_" + str(exc), source_refno=node["refno"])
            plans, thin_wall_obligations = _guard_parallel_through_cylinder(
                root, nodes[1:], plans,
                radial_segments, float(chord_tolerance_mm))
            # Refined positive operands consume the same bounded input budget
            # as ordinary explicit-tolerance plans.  Fail before constructing
            # or dispatching any worker input when the guard cannot fit.
            planned_vertices = sum(plan["counts"]["vertices"] for plan in plans if plan is not None)
            planned_triangles = sum(plan["counts"]["triangles"] for plan in plans if plan is not None)
            if thin_wall_obligations and (planned_vertices > MAX_VERTICES or planned_triangles > MAX_TRIANGLES):
                _reject("csg_thin_wall_guard_mesh_budget", vertices=planned_vertices,
                        triangles=planned_triangles, max_vertices=MAX_VERTICES,
                        max_triangles=MAX_TRIANGLES)
        operands, operand_evidence = [], []
        input_vertices = input_triangles = 0
        for index, node in enumerate(nodes):
            plan = plans[index]
            operand, validation = _operand(node, anchor, plan["radial_segments"] if plan else radial_segments,
                                           latitude_segments=plan["latitude_segments"] if plan else None)
            input_vertices += len(operand["vertices"])
            input_triangles += len(operand["triangles"])
            if input_vertices > MAX_VERTICES or input_triangles > MAX_TRIANGLES:
                _reject("csg_input_mesh_limit_exceeded", vertices=input_vertices, triangles=input_triangles)
            operands.append(operand)
            operand_evidence.append({"refno": node["refno"], "negative": node["raw"]["negative"],
                                     "shape": node["shape"], "validation": validation})
            if plan is not None:
                operand_evidence[-1]["tessellation"] = plan
        computed = _run_csg_worker(operands)
        engine = computed.get("engine")
        if (not isinstance(engine, Mapping) or engine.get("name") != "manifold3d"
                or engine.get("mesh_api") != "Mesh64" or engine.get("numeric_precision") != "float64"
                or engine.get("operation") != "Subtract" or engine.get("status") != "NoError"
                or not isinstance(engine.get("version"), str) or not engine["version"]):
            _reject("csg_engine_receipt_invalid")
        anchored, triangles = computed.get("vertices"), computed.get("triangles")
        empty = computed.get("empty")
        if (type(empty) is not bool or engine.get("empty") is not empty
                or not isinstance(anchored, list) or not isinstance(triangles, list)
                or len(anchored) > MAX_VERTICES or len(triangles) > MAX_TRIANGLES):
            _reject("csg_worker_invalid_geometry")
        if empty and (anchored or triangles) or not empty and (not anchored or not triangles):
            _reject("csg_worker_invalid_empty_result")
        vertices = []
        for point in anchored:
            if not isinstance(point, list) or len(point) != 3:
                _reject("csg_worker_invalid_geometry")
            point = [_number(value) for value in point]
            world = [math.fsum((point[axis], anchor[axis])) for axis in range(3)]
            if any(not math.isfinite(value) or abs(value) > _MAX_COORDINATE_MM for value in world):
                _reject("csg_output_coordinate_limit_exceeded")
            vertices.append(world)
        validation = _empty_validation() if empty else validate_triangle_mesh(vertices, triangles)
        checks = validation.get("checks", {})
        if (validation.get("complete") is not True or validation.get("success") is not True
                or checks.get("closed_manifold") is not True or checks.get("winding_consistent") is not True
                or validation.get("findings_truncated") is True
                or any(finding.get("code") != "component_winding_is_inward" for finding in validation.get("findings", []))):
            _reject("csg_output_material_topology_invalid", validation=validation)
        normals = []
        for face in triangles:
            # Normals describe the geometry that is actually returned. Adding
            # the WORLD anchor can round nearby vertices differently from the
            # worker's small-coordinate mesh, especially for distant objects;
            # deriving normals before that translation can therefore disagree
            # with the returned triangle planes.
            normal = _unit_normal(*(vertices[index] for index in face))
            if normal is None:
                _reject("csg_output_normal_unavailable")
            normals.append(list(normal))
        gross = computed.get("gross_polyhedral_volume_mm3")
        net = computed.get("net_volume_mm3")
        if (type(gross) not in {int, float} or type(net) not in {int, float}
                or not math.isfinite(gross) or not math.isfinite(net) or gross <= 0):
            _reject("csg_output_volume_invalid")
        tolerance = max(abs(gross) * 1e-9, 1e-8)
        expected_gross = math.fsum(component["signed_volume"] for component in operand_evidence[0]["validation"]["components"])
        if abs(expected_gross - gross) > tolerance:
            _reject("csg_gross_volume_validation_failed", operand_volume_mm3=expected_gross, engine_volume_mm3=gross)
        if net < -tolerance or net > gross + tolerance or empty and net != 0:
            _reject("csg_output_volume_invalid")
        signed_volume = math.fsum(component["signed_volume"] for component in validation.get("components", [])
                                  if component.get("volume_available") is True)
        if not empty and (net <= 0 or any(component.get("volume_available") is not True for component in validation["components"])
                          or signed_volume <= 0 or abs(signed_volume - net) > tolerance):
            _reject("csg_output_volume_validation_failed", signed_volume_mm3=signed_volume, engine_volume_mm3=net)
        gross = float(gross)
        raw_net = float(net)
        net = max(0.0, min(raw_net, gross))
        removed = gross - net
        cavity_shells = [component["component"] for component in validation["components"]
                         if component.get("orientation") == "inward"]
        result = {
            "success": True, "available": True, "complete": True, "empty": empty, "errors": [],
            "refno": root["refno"], "revision": native["revision"], "shape": root["shape"],
            "coordinate_space": "WORLD", "coordinate_frame": WORLD_FRAME, "coordinate_unit": "mm",
            "mesh_source": "derived_from_e3d_primitive_parameters_with_owner_member_boolean_difference",
            "native_tessellation": False, "cuts_applied": True, "csg_evaluated": True,
            "closed_manifold": True, "material_boundary_validated": True,
            "native_cut_application_verified": False, "native_rendered_geometry_verified": False,
            "scope": CSG_SCOPE, "atomic_snapshot": False, "radial_segments": radial_segments,
            "vertices": vertices, "triangles": triangles, "triangle_normals": normals,
            "counts": {"vertices": len(vertices), "triangles": len(triangles)}, "cutter_count": len(nodes) - 1,
            "source_geometry": deepcopy(dict(native)), "source": deepcopy(dict(native["element"])),
            "source_geometry_sha256": hashlib.sha256(encoded).hexdigest(),
            "source_operands": operand_evidence, "operation_anchor_world_mm": list(anchor),
            "geometry_sha256": csg_geometry_sha256(vertices, triangles),
            "geometry_hash_scope": "canonical ordered vertices and triangles; metadata excluded",
            "engine": deepcopy(dict(engine)), "validation": validation,
            "material_boundary_validation": {
                "validated": True, "provenance": MATERIAL_PROVENANCE, "engine_status": "NoError", "empty": empty,
                "closed_manifold": True, "winding_consistent": True,
                "allowed_findings": ["component_winding_is_inward"], "cavity_shells": cavity_shells,
            },
            "volume": {"gross_polyhedral_mm3": gross, "net_mm3": net, "removed_mm3": removed, "unit": "mm3"},
            "gross_polyhedral_volume_mm3": gross, "net_volume_mm3": net, "removed_volume_mm3": removed,
            "volume_validation_tolerance_mm3": tolerance,
            "engine_net_volume_mm3": raw_net,
            "volume_roundoff_adjustment_mm3": net - raw_net,
            "approximation": "parameter-derived polyhedral operands; circular surfaces are inscribed radial facets",
            "limits": {"cutters": MAX_CUTTERS, "vertices": MAX_VERTICES, "triangles": MAX_TRIANGLES,
                       "ipc_bytes": MAX_IPC_BYTES, "worker_timeout_seconds": WORKER_TIMEOUT_SECONDS},
        }
        if chord_tolerance_mm is not None:
            result["tessellation"] = {
                "mode": "per_operand_chord_tolerance", "chord_tolerance_mm": float(chord_tolerance_mm),
                "requested_radial_segments": radial_segments,
                "max_operand_surface_deviation_mm": max(row["tessellation"]["max_surface_deviation_mm"] for row in operand_evidence),
                "error_bound_scope": "Analytic operand surfaces in WORLD before Boolean operations. "
                                     "Excludes floating-point placement, Boolean and native geometry errors.",
            }
            if thin_wall_obligations:
                result["tessellation"]["thin_wall_guard"] = {
                    "enabled": True,
                    "positive_refno": root["refno"],
                    "proof": "positive and cutter inscribed radial sagittas are each at most half the conservative radial clearance",
                    "obligations": deepcopy(thin_wall_obligations),
                }
        if len(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")) > MAX_IPC_BYTES:
            _reject("csg_result_output_limit_exceeded")
        return result
    except subprocess.TimeoutExpired:
        return _failure(native, "csg_worker_timeout", {"timeout_seconds": WORKER_TIMEOUT_SECONDS, "partial_result_used": False})
    except CSGWorkerError as failure:
        return _failure(native, failure.code, failure.diagnostics)
    except Exception as failure:
        return _failure(native, "csg_derivation_failed", {"error_type": type(failure).__name__, "detail": str(failure)[:1024]})
