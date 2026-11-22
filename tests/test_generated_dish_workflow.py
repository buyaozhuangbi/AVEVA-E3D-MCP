from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aveva_e3d_mcp.generated_dish_workflow import read_generated_dish_mesh
from test_generated_csg import effective_policy, native_response, recount

REF = "=2000000001/2740"
NAME = "/VESSEL-101/DISH"
ORIGIN = [100.0, -200.0, 720.0]
AXES = [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
ARRAY12 = [*AXES[0], *AXES[1], *AXES[2], *ORIGIN]


def envelope(result, session="session-1"):
    return {"ok": True, "version": "E3D21", "session_id": session, "result": deepcopy(result)}


def primitive(*, revision_seed="dish-1", radi=70.0, frame=None, **changes):
    if frame is None:
        frame = {"storage_frame_refno": "=1/0", "native": {"position": [0, 0, 0], "orientation": AXES},
                 "world": {"position": ORIGIN, "orientation": {
                     "x_axis": AXES[0], "y_axis": AXES[1], "z_axis": AXES[2]}}, "relative_to_owner": None}
    identity = {"refno": REF, "type": "DISH", "name": NAME}
    dimensions = {"DIAM": 600.0, "HEIG": 70.0, "RADI": radi}
    target = {"element": deepcopy(identity), "shape": "DISH", "negative": False, "is_negative": False,
              "unit": "mm", "dimensions": deepcopy(dimensions), "frame": deepcopy(frame),
              "negative_children": [], "member_refnos": [], "complete": True, "read_errors": {}}
    value = {"success": True, "available": True, "complete": True, "partial": False, "shape": "DISH",
        "element": identity, "negative": False, "is_negative": False, "negative_children": [],
        "target_geometry": target, "member_refnos": [], "read_errors": {}, "unit": "mm",
        "dimensions": dimensions, "frame": frame,
        "revision": hashlib.sha256(revision_seed.encode()).hexdigest()}
    value.update(changes)
    return value


def generated(*, transform=None, raw=None, options=None, extra_items=False, session="session-1"):
    response = native_response()
    response["session_id"] = session
    result = response["result"]
    source = {"refno": REF, "name": NAME, "type": "DISH"}
    result.update(requested_refno=REF, refno=REF, source=source, source_after=deepcopy(source))
    item = result["trees"][0]["items"][0]
    item["rdata"] = [300.0, 70.0, 1.0] if raw is None else list(raw)
    item["rdata_length"] = len(item["rdata"])
    item["idata"] = [0, 0]
    item["idata_length"] = 2
    item["transform_as_array12"] = list(ARRAY12 if transform is None else transform)
    item["local_limits"] = {"min": [-300.0, -300.0, 0.0], "max": [300.0, 300.0, 70.0], "frame": "untransformed"}
    items = [item]
    if extra_items:
        items.append(deepcopy(response["result"]["trees"][0]["items"][1]))
    result["trees"][0]["items"] = items
    result["trees"][0]["item_count"] = len(items)
    result["trees"][0]["complete"] = True
    result["counts"]["total_tree_count"] = 1
    result["limits"]["max_trees"] = 1
    result["limits"]["max_items"] = 1
    result["context"].update(project_before="DEMO-PROJECT", project_after="DEMO-PROJECT", mdb_before="DEMO-MDB", mdb_after="DEMO-MDB")
    result["options"]["before"].update(hard_obstructions=False, soft_obstructions=False, level=6)
    result["options"]["after"].update(hard_obstructions=False, soft_obstructions=False, level=6)
    bare = {"hard_obstructions": False, "soft_obstructions": False, "level": 6,
        "center_line": False, "tube": True, "insulation": False, "obstruction": False,
        "insulation_translucency": 0, "obstruction_translucency": 0,
        "holes": True, "solid_only": False, "pline": False, "wire_line": False}
    result["options"]["requested"].update(bare)
    if options:
        result["options"]["requested"].update(options)
    effective_policy(response)
    recount(response)
    return response


def health(session="session-1"):
    return {"ok": True, "version": "E3D21", "session_id": session, "connected": True,
            "state": {"database_ready": True, "busy": False, "recovery_required": False,
                      "native_database_fault": {"has_fault": False},
                      "generated_csg_quarantine": {"active": False}}}


def project(name="DEMO-PROJECT", mdb="DEMO-MDB", session=None):
    result = {"ok": True, "version": "E3D21", "project": {
        "name": name, "mdb": mdb, "connected": True, "recovery_only": False}}
    if session is not None:
        result["session_id"] = session
    return result


class GeneratedDishWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.bridge = Mock()
        self.bridge.health.return_value = health()
        self.bridge.project.return_value = project()
        source = primitive()
        self.bridge.primitive_geometry.side_effect = [envelope(source), envelope(source)]
        self.bridge.generated_csg.return_value = generated()

    def read(self, refno=REF, **kwargs):
        return read_generated_dish_mesh(self.bridge, refno, radial_segments=12, **kwargs)

    def result(self, **kwargs):
        response = self.read(**kwargs)
        self.assertTrue(response["ok"], response)
        return response["result"]

    def test_success_uses_basicdis_parameters_and_not_raw_marker_or_radi_magnitude(self):
        result = self.result(chord_tolerance_mm=1.0)
        self.assertTrue(result["success"], result["errors"])
        self.assertEqual(result["source_geometry"]["dimensions"], {"DIAM": 600.0, "HEIG": 70.0, "RADI": 70.0})
        mapping = result["source_evidence"]["mapping"]
        self.assertEqual(mapping["profile_parameter_source"], "installed_core3d_basicdis_design_dish_branch")
        self.assertTrue(mapping["limits_mapping_verified"])
        self.assertFalse(mapping["raw_third_value_used_as_radius"])
        self.assertFalse(mapping["export_marker_global_semantics_verified"])
        self.assertEqual(mapping["observed_export_marker"], 1.0)
        self.assertAlmostEqual(mapping["profile_parameters"]["effective_knuckle_radius_mm"],
                               70 / (1 + (300 - 70) / math.hypot(300, 70)))
        mesh = result["derived_mesh"]
        self.assertTrue(mesh["success"])
        self.assertEqual(mesh["refno"], REF)
        self.assertEqual(mesh["source_csg_rdata"], [300.0, 70.0, 1.0])
        self.assertEqual(mesh["parameters"]["effective_knuckle_radius_mm"],
                         mapping["profile_parameters"]["effective_knuckle_radius_mm"])
        self.assertFalse(mesh["native_tessellation"])
        self.assertFalse(result["cuts_applied"])
        self.assertFalse(result["native_shape_equivalence_verified"])
        self.assertEqual(self.bridge.generated_csg.call_args.args, (REF,))
        self.assertEqual(self.bridge.generated_csg.call_args.kwargs, {"max_trees": 1, "max_items": 1})
        self.assertEqual(self.bridge.primitive_geometry.call_count, 2)
        json.dumps(result, allow_nan=False)

    def test_zero_radi_selects_spherical_design_branch_and_zero_marker(self):
        source = primitive(radi=0.0)
        self.bridge.primitive_geometry.side_effect = [envelope(source), envelope(source)]
        self.bridge.generated_csg.return_value = generated(raw=[300, 70, 0])
        result = self.result()
        self.assertTrue(result["success"], result["errors"])
        self.assertEqual(result["derived_mesh"]["parameters"]["profile"], "spherical_cap")
        self.assertEqual(result["derived_mesh"]["parameters"]["effective_knuckle_radius_mm"], 0.0)

    def test_raw_third_value_is_only_a_marker_consistency_witness(self):
        self.bridge.generated_csg.return_value = generated(raw=[300, 70, 30])
        result = self.result()
        self.assertFalse(result["success"])
        self.assertEqual(result["errors"][0]["code"], "generated_dish_descriptor_marker_branch_mismatch")
        self.assertIsNone(result["derived_mesh"])
        self.assertEqual(self.bridge.primitive_geometry.call_count, 1)

    def test_native_observation_order_and_exactly_one_generation_request(self):
        events = []
        self.bridge.primitive_geometry.side_effect = lambda ref: (events.append(("primitive", ref)), envelope(primitive()))[1]
        self.bridge.generated_csg.side_effect = lambda ref, **kw: (events.append(("generated", ref, kw)), generated())[1]
        self.bridge.health.side_effect = lambda: (events.append(("health",)), health())[1]
        self.bridge.project.side_effect = lambda: (events.append(("project",)), project())[1]
        self.assertTrue(self.result()["success"])
        calls = [i for i, event in enumerate(events) if event[0] == "primitive"]
        generation = next(i for i, event in enumerate(events) if event[0] == "generated")
        self.assertEqual(len(calls), 2)
        self.assertLess(calls[0], generation)
        self.assertLess(generation, calls[1])

    def test_transform_dimension_and_extra_item_mismatches_fail_closed(self):
        cases = [
            (generated(transform=[1.0] * 12), "generated_dish_descriptor_transform_source_mismatch"),
            (generated(raw=[301, 70, 1]), "generated_dish_descriptor_radius_diam_mismatch"),
            (generated(raw=[300, 71, 1]), "generated_dish_descriptor_height_mismatch"),
            (generated(extra_items=True), "generated_dish_descriptor_invalid"),
        ]
        for response, code in cases:
            with self.subTest(code=code):
                self.bridge.generated_csg.return_value = response
                self.bridge.primitive_geometry.side_effect = [envelope(primitive()), envelope(primitive())]
                result = self.result()
                self.assertFalse(result["success"])
                self.assertEqual(result["errors"][0]["code"], code)
                self.assertIsNone(result["derived_mesh"])
                self.assertEqual(self.bridge.primitive_geometry.call_count, 1)
                self.bridge.primitive_geometry.reset_mock()

    def test_local_limits_are_required_and_match_source_dimensions(self):
        missing = generated()
        missing["result"]["trees"][0]["items"][0].pop("local_limits")
        unknown = generated()
        unknown["result"]["trees"][0]["items"][0]["local_limits"]["extra"] = 0
        frame = generated()
        frame["result"]["trees"][0]["items"][0]["local_limits"]["frame"] = "WORLD"
        min_mismatch = generated()
        min_mismatch["result"]["trees"][0]["items"][0]["local_limits"]["min"][1] -= 0.1
        mismatch = generated()
        mismatch["result"]["trees"][0]["items"][0]["local_limits"]["max"][0] += 0.1
        malformed = generated()
        malformed["result"]["trees"][0]["items"][0]["local_limits"]["min"] = [0.0, 0.0]
        nonfinite = generated()
        nonfinite["result"]["trees"][0]["items"][0]["local_limits"]["max"][2] = float("nan")
        cases = [
            (missing, "generated_dish_descriptor_invalid"),
            (unknown, "generated_dish_descriptor_local_limits_invalid"),
            (frame, "generated_dish_descriptor_invalid"),
            (min_mismatch, "generated_dish_descriptor_local_limits_mismatch"),
            (mismatch, "generated_dish_descriptor_local_limits_mismatch"),
            (malformed, "generated_dish_descriptor_invalid"),
            (nonfinite, "generated_dish_descriptor_invalid"),
        ]
        for response, code in cases:
            with self.subTest(code=code):
                self.bridge.primitive_geometry.reset_mock()
                self.bridge.generated_csg.return_value = response
                self.bridge.primitive_geometry.side_effect = [envelope(primitive()), envelope(primitive())]
                result = self.result()
                self.assertFalse(result["success"])
                self.assertEqual(result["errors"][0]["code"], code)
                self.assertIsNone(result["derived_mesh"])
                self.assertEqual(self.bridge.primitive_geometry.call_count, 1)

        within_tolerance = generated()
        within_tolerance["result"]["trees"][0]["items"][0]["local_limits"]["min"][0] += 1e-7
        self.bridge.primitive_geometry.reset_mock()
        self.bridge.generated_csg.return_value = within_tolerance
        self.bridge.primitive_geometry.side_effect = [envelope(primitive()), envelope(primitive())]
        result = self.result()
        self.assertTrue(result["success"], result["errors"])
        self.assertTrue(result["source_evidence"]["mapping"]["limits_mapping_verified"])

    def test_options_must_be_bare(self):
        self.bridge.generated_csg.return_value = generated(options={"center_line": True})
        result = self.result()
        self.assertFalse(result["success"])
        self.assertEqual(result["errors"][0]["code"], "generated_dish_descriptor_options_not_bare")
        self.assertIsNone(result["derived_mesh"])

    def test_stable_full_sha256_revision_and_equal_source_observations_are_required(self):
        changed = primitive(revision_seed="dish-2")
        self.bridge.primitive_geometry.side_effect = [envelope(primitive()), envelope(changed)]
        result = self.result()
        self.assertFalse(result["success"])
        self.assertEqual(result["errors"][0]["code"], "generated_dish_source_changed")
        self.assertIsNotNone(result["source_evidence"]["primitive_after"])
        self.assertIsNone(result["derived_mesh"])

    def test_context_change_is_rejected_and_native_fault_stops_followup_reads(self):
        self.bridge.health.side_effect = [health("session-1"), health("session-2")]
        result = self.result()
        self.assertFalse(result["success"])
        self.assertEqual(result["errors"][0]["code"], "generated_dish_context_changed")
        self.assertIsNone(result["derived_mesh"])
        self.bridge.health.reset_mock(return_value=True, side_effect=True)
        self.bridge.health.reset_mock(return_value=True, side_effect=True)
        self.bridge.project.reset_mock(return_value=True, side_effect=True)
        self.bridge.primitive_geometry.reset_mock(return_value=True, side_effect=True)
        bad = health()
        bad["state"]["native_database_fault"]["has_fault"] = True
        self.bridge.health.return_value = bad
        result = self.result()
        self.assertFalse(result["success"])
        self.assertEqual(self.bridge.project.call_count, 0)
        self.assertEqual(self.bridge.primitive_geometry.call_count, 0)

    def test_primitive_requires_complete_leaf_subtree_and_source_context(self):
        cases = [
            (primitive(negative_children=[{"refno": "=1/99"}]), "generated_dish_negative_children_present"),
            (primitive(target_geometry=None, member_refnos=None), "generated_dish_membership_unproven"),
            (primitive(revision="short"), "generated_dish_before_revision_missing"),
            (primitive(unit="m"), "generated_dish_native_unit_mismatch"),
        ]
        for value, code in cases:
            with self.subTest(code=code):
                self.bridge.primitive_geometry.side_effect = [envelope(value)]
                result = self.result()
                self.assertFalse(result["success"])
                self.assertEqual(result["errors"][0]["code"], code)
                self.bridge.generated_csg.assert_not_called()
                self.bridge.primitive_geometry.reset_mock()

    def test_descriptor_context_session_or_marker_mismatch_is_rejected(self):
        bad = generated()
        bad["session_id"] = "other-session"
        self.bridge.generated_csg.return_value = bad
        result = self.result()
        self.assertFalse(result["success"])
        self.assertEqual(result["errors"][0]["code"], "generated_dish_descriptor_session_mismatch")
        self.assertIsNone(result["derived_mesh"])

    def test_failed_generated_call_is_retained_with_no_retry_or_after_read(self):
        failure = {"ok": False, "version": "E3D21", "error": "unavailable"}
        self.bridge.generated_csg.return_value = failure
        result = self.result()
        self.assertFalse(result["success"])
        self.assertEqual(result["source_evidence"]["generated_csg"], failure)
        self.assertEqual(self.bridge.generated_csg.call_count, 1)
        self.assertEqual(self.bridge.primitive_geometry.call_count, 1)
        self.assertIsNone(result["derived_mesh"])

    def test_named_reference_resolves_to_numeric_identity(self):
        source = primitive()
        source["element"]["name"] = "/VESSEL-101/DISH"
        self.bridge.primitive_geometry.side_effect = [envelope(source), envelope(source)]
        result = self.result(refno="/vessel-101/dish")
        self.assertTrue(result["success"], result["errors"])
        self.assertEqual(result["resolved_refno"], REF)
        self.assertEqual(self.bridge.generated_csg.call_args.args, (REF,))

    def test_invalid_inputs_make_no_native_reads(self):
        for kwargs in ({"refno": ""}, {"refno": "CE"}, {"refno": "=1/2", "radial_segments": 11},
                       {"refno": "=1/2", "radial_segments": True}, {"refno": "=1/2", "chord_tolerance_mm": 0}):
            with self.subTest(kwargs=kwargs):
                result = read_generated_dish_mesh(self.bridge, **kwargs)["result"]
                self.assertFalse(result["success"])
                self.assertEqual(self.bridge.health.call_count, 0)
                self.assertEqual(self.bridge.primitive_geometry.call_count, 0)
                self.bridge.health.reset_mock()
                self.bridge.primitive_geometry.reset_mock()


if __name__ == "__main__":
    unittest.main()
