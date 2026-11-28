from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.equipment_spatial_review import compare_world_aabbs, compare_world_bounds, primitive_world_aabb


IDENTITY = {"x_axis": [1, 0, 0], "y_axis": [0, 1, 0], "z_axis": [0, 0, 1]}


def geometry(kind="BOX", dimensions=None, *, refno="=1/2", position=None, orientation=None):
    return {"success": True, "complete": True, "read_errors": {}, "shape": kind,
        "negative": False, "negative_children": [], "unit": "mm",
        "dimensions": dimensions or {"XLEN": 10, "YLEN": 20, "ZLEN": 30},
        "element": {"refno": refno, "type": kind},
        "frame": {"world": {"position": position or [0, 0, 0], "orientation": orientation or IDENTITY}}}


def bounds(native, *, kind="BOX", current_position=None, current_orientation=None,
           planned_position=None, planned_orientation=None):
    return primitive_world_aabb(native, expected_refno="=1/2", expected_type=kind,
        current_position=current_position or [0, 0, 0], current_orientation=current_orientation or IDENTITY,
        planned_position=planned_position or [0, 0, 0], planned_orientation=planned_orientation or IDENTITY)


def test_box_aabb_tracks_planned_translation_and_rotation():
    quarter_turn = {"x_axis": [0, 1, 0], "y_axis": [-1, 0, 0], "z_axis": [0, 0, 1]}
    result = bounds(geometry(), planned_position=[100, 200, 300], planned_orientation=quarter_turn)
    assert result["available"] is True
    assert result["aabb_world_mm"] == {"min": [90, 195, 285], "max": [110, 205, 315]}
    assert result["oriented_box_world_mm"] == {
        "center": [100.0, 200.0, 300.0],
        "axes": {"x_axis": [0, 1, 0], "y_axis": [-1, 0, 0], "z_axis": [0, 0, 1]},
        "half_extents": [5.0, 10.0, 15.0],
    }


def test_cylinder_aabb_uses_analytic_radial_extent_in_its_planned_world_pose():
    cylinder_orientation = {"x_axis": [0, 1, 0], "y_axis": [0, 0, 1], "z_axis": [1, 0, 0]}
    current = geometry("CYLI", {"DIAM": 10, "HEIG": 20}, orientation=IDENTITY)
    result = primitive_world_aabb(current, expected_refno="=1/2", expected_type="CYLI",
        current_position=[0, 0, 0], current_orientation=IDENTITY,
        planned_position=[10, 20, 30], planned_orientation=cylinder_orientation)
    assert result["available"] is True
    assert result["aabb_world_mm"] == {"min": [0, 15, 25], "max": [20, 25, 35]}


@pytest.mark.parametrize("mutation,reason", [
    (lambda row: row.update(unit="m"), "geometry_identity_or_completeness_unverified"),
    (lambda row: row.update(negative=True), "geometry_identity_or_completeness_unverified"),
    (lambda row: row["dimensions"].update(XLEN=float("nan")), "positive_primitive_geometry_unavailable"),
    (lambda row: row["element"].update(refno="=1/99"), "geometry_identity_or_completeness_unverified"),
])
def test_unverified_native_shape_evidence_never_produces_bounds(mutation, reason):
    native = geometry()
    mutation(native)
    assert bounds(native)["reason"] == reason


def test_geometry_and_spatial_pose_must_describe_the_same_observation():
    native = geometry(position=[1, 0, 0])
    result = bounds(native)
    assert result["available"] is False
    assert result["reason"] == "geometry_pose_changed_between_reads"


@pytest.mark.parametrize("second,status", [
    ({"available": True, "aabb_world_mm": {"min": [5, 0, 0], "max": [15, 10, 10]}}, "overlap_candidate"),
    ({"available": True, "aabb_world_mm": {"min": [10, 0, 0], "max": [20, 10, 10]}}, "contact_candidate"),
    ({"available": True, "aabb_world_mm": {"min": [11, 0, 0], "max": [20, 10, 10]}}, "separate"),
    ({"available": False, "aabb_world_mm": None}, "unknown"),
])
def test_aabb_pair_classification_is_explicit_and_conservative(second, status):
    first = {"available": True, "aabb_world_mm": {"min": [0, 0, 0], "max": [10, 10, 10]}}
    result = compare_world_aabbs(first, second)
    assert result["status"] == status
    if status == "separate":
        assert result["separation_mm"] == pytest.approx(1)


def test_box_pair_refines_aabb_overlap_when_oriented_boxes_are_separate():
    first = {"available": True, "aabb_world_mm": {"min": [-0.5, -0.5, -0.5], "max": [0.5, 0.5, 0.5]},
             "oriented_box_world_mm": {"center": [0, 0, 0], "axes": IDENTITY,
                                        "half_extents": [0.5, 0.5, 0.5]}}
    rotated = {"x_axis": [2 ** -0.5, 2 ** -0.5, 0], "y_axis": [-2 ** -0.5, 2 ** -0.5, 0],
               "z_axis": [0, 0, 1]}
    second = {"available": True, "aabb_world_mm": {"min": [-3.889, 0.411, -0.5], "max": [3.889, 8.189, 0.5]},
              "oriented_box_world_mm": {"center": [0, 4.3, 0], "axes": rotated,
                                         "half_extents": [5, 0.5, 0.5]}}

    result = compare_world_bounds(first, second)

    assert result["aabb_status"] == "overlap_candidate"
    assert result["status"] == "aabb_overlap_only"
    assert result["oriented_box_status"] == "separate"
    assert result["narrow_phase"] == "oriented_box_separating_axes"
    assert result["oriented_separation"]["projected_gap_mm"] > 0


def test_box_pair_with_oriented_overlap_remains_an_overlap_candidate():
    first = {"available": True, "aabb_world_mm": {"min": [-1, -1, -1], "max": [1, 1, 1]},
             "oriented_box_world_mm": {"center": [0, 0, 0], "axes": IDENTITY,
                                        "half_extents": [1, 1, 1]}}
    second = {"available": True, "aabb_world_mm": {"min": [0, 0, 0], "max": [2, 2, 2]},
              "oriented_box_world_mm": {"center": [1, 1, 1], "axes": IDENTITY,
                                         "half_extents": [1, 1, 1]}}

    result = compare_world_bounds(first, second)

    assert result["status"] == "overlap_candidate"
    assert result["aabb_status"] == "overlap_candidate"
    assert result["oriented_box_status"] == "overlap_candidate"
