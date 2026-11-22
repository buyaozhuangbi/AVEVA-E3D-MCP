"""Generated-CSG transport admits descriptors, never guessed native meshes."""

from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aveva_e3d_mcp import generated_csg as contract


REFERENCE = "=2000000001/2740"
TRANSFORM = [0.0, -1.0, 0.0, 100.0, 1.0, 0.0, 0.0, -200.0, 0.0, 0.0, 1.0, 720.0]


def native_item(index=0, *, code=9, name="CSG_DISH", rdata=None):
    values = [300.0, 70.0, 70.0] if rdata is None else rdata
    return {"index": index, "gml_item": -1000 + index,
        "type": {"name": name, "value": code, "known": code in {9, 19, 59}},
        "rdata": list(values), "rdata_length": len(values), "idata": [], "idata_length": 0,
        "transform_as_array12": list(TRANSFORM),
        "local_limits": {"min": [-300.0, -300.0, 0.0], "max": [300.0, 300.0, 70.0], "frame": "untransformed"},
        "translucency": 0, "complete": True, "errors": []}


def native_response():
    source = {"refno": REFERENCE, "name": "/VESSEL-101/DISH", "type": "DISH"}
    options = {"hard_obstructions": True, "soft_obstructions": False, "level": 6,
        "center_line": True, "tube": False, "insulation": True, "insulation_translucency": 40,
        "obstruction": True, "obstruction_translucency": 20, "holes": False,
        "solid_only": True, "pline": True, "wire_line": True}
    requested = {**options, "center_line": False, "tube": True, "insulation": False,
                 "obstruction": False, "holes": True, "solid_only": False, "pline": False, "wire_line": False}
    items = [native_item(), native_item(1, code=59, name="CSG_NEG_DISH"),
             native_item(2, code=999, name="999", rdata=[1.5, -2.0]), native_item(3, code=19, name="CSG_FACET_GROUP")]
    for field in ("rdata", "idata", "rdata_length", "idata_length", "transform_as_array12", "local_limits"):
        items[-1][field] = None
    value = {"ok": True, "version": "E3D21", "result": {
        "operation": "generated_csg", "requested_refno": REFERENCE, "refno": REFERENCE,
        "source": source, "source_after": deepcopy(source), "source_unchanged": True,
        "source_subtree_before": [], "source_subtree_after": [], "source_subtree_unchanged": True,
        "success": True, "available": True, "complete": True,
        "generated_csg": True, "generated_geometry_available": True,
        "native_tessellation": False, "cuts_applied": False, "post_boolean_mesh_verified": False,
        "raw_data_layout_verified": False, "transform_convention_verified": False,
        "units": "sdk_raw_unverified", "coordinate_frame": "sdk_item_transform_output_unverified",
        "single_dispatch": True, "consistency": "single_ui_dispatch",
        "generation_attempted": True, "generation_returned": True,
        "native_work_bounded": False, "native_cancellation_supported": False,
        "trees": [{"index": 0, "source": deepcopy(source), "source_matches_requested": True,
                   "item_count": 4, "items": items, "complete": True}],
        "counts": {}, "limits": {"max_trees": 8, "max_items": 64,
            "max_array_values": 4096, "max_total_array_values": 65536},
        "truncated": False, "truncation_reasons": [], "errors": [],
        "options": {"before": options, "requested": requested, "after": deepcopy(options), "restored": True},
        "context": {"ce_before": "=2000000001/2617", "ce_after": "=2000000001/2617", "ce_unchanged": True,
            "project_before": "DEMO-PROJECT", "project_after": "DEMO-PROJECT", "mdb_before": "DEMO-MDB", "mdb_after": "DEMO-MDB", "identity_unchanged": True},
        "resources": {"owned_options_registered_count": 2, "disposed_count": 2,
                      "borrowed_dispose_attempted_count": 0, "complete": True, "errors": []},
        "native_database_fault": None, "generated_csg_quarantine": {"active": False, "retry_safe": False},
    }}
    recount(value)
    return value


def typed_facet_response():
    """Build a bounded typed-facet receipt while retaining raw coordinates."""
    response = native_response()
    result = response["result"]
    result["facet_group_policy"] = "sdk_typed_v1"
    result["limits"]["facet_groups"] = deepcopy(contract._FACET_GROUP_LIMITS)
    group = {
        "source": "CSGFacetGroup.Facets",
        "units": "sdk_raw_unverified",
        "coordinate_frame": "sdk_facet_coordinates_unverified",
        "facets": [{"loops": [{
            "vertices": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]],
            "normals": [[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]],
        }]}],
    }
    for item in result["trees"][0]["items"]:
        item["facet_group"] = group if item["type"]["value"] == 19 else None
    result["counts"]["facet_groups"] = {
        "returned_group_count": 1, "returned_facet_count": 1,
        "returned_loop_count": 1, "copied_coordinate_value_count": 18,
    }
    return response


def recount(response, *, total_trees=None):
    result, copied = response["result"], 0
    for tree in result["trees"]:
        for item in tree["items"]:
            copied += sum(len(item[field]) for field in ("rdata", "idata", "transform_as_array12") if item[field] is not None)
            if item["local_limits"] is not None:
                copied += 6
    result["counts"] = {
        "total_tree_count": len(result["trees"]) if total_trees is None else total_trees,
        "observed_tree_count": sum(tree["item_count"] is not None for tree in result["trees"]),
        "observed_item_count": sum(tree["item_count"] or 0 for tree in result["trees"]),
        "returned_tree_count": len(result["trees"]),
        "returned_item_count": sum(len(tree["items"]) for tree in result["trees"]),
        "copied_array_value_count": copied, "item_count_scope": "inspected_trees_only",
    }


def effective_policy(response):
    options = response["result"]["options"]
    options.update(comparison_policy="enabled_geometry_options_v1",
                   comparison_scope="All geometry switches/level; translucency only when enabled.",
                   effective_before=None, effective_after=None, raw_restored=None,
                   effective_restored=None, raw_mismatched_fields=[], effective_mismatched_fields=[], inactive_fields=[])
    for raw_field, effective_field in (("before", "effective_before"), ("after", "effective_after")):
        raw = options[raw_field]
        if raw is None:
            continue
        options[effective_field] = deepcopy(raw)
        for channel in ("insulation", "obstruction"):
            if raw[channel] is False:
                options[effective_field][channel + "_translucency"] = None
    if options["before"] is not None and options["after"] is not None:
        for raw_field, effective_field in (("before", "after"),):
            options["raw_mismatched_fields"] = [key for key in options[raw_field] if options[raw_field][key] != options[effective_field][key]]
        options["effective_mismatched_fields"] = [key for key in options["effective_before"]
            if options["effective_before"][key] != options["effective_after"][key]]
        options["inactive_fields"] = [channel + "_translucency" for channel in ("insulation", "obstruction")
            if options["before"][channel] is False and options["after"][channel] is False]
        options["raw_restored"] = not options["raw_mismatched_fields"]
        options["effective_restored"] = not options["effective_mismatched_fields"]
        options["restored"] = options["effective_restored"]
    return response


class GeneratedCsgTests(unittest.TestCase):
    def validate(self, response=None, **kwargs):
        return contract.validate_generated_csg_response(native_response() if response is None else response,
            kwargs.get("requested_refno", REFERENCE), kwargs.get("max_trees", 8), kwargs.get("max_items", 64))

    def refused(self, response, code=None, **kwargs):
        output = self.validate(response, **kwargs)
        self.assertFalse(output["ok"], output)
        self.assertFalse(output["result"]["success"])
        self.assertFalse(output["result"]["complete"])
        self.assertEqual(output["result"]["trees"], [])
        self.assertFalse(output["result"]["native_tessellation"])
        self.assertFalse(output["result"]["cuts_applied"])
        self.assertFalse(output["result"]["descriptor_validation"]["mesh_conversion_admitted"])
        if code:
            self.assertEqual(output["result"]["error_code"], code)
        return output

    def test_complete_native_descriptor_preserves_negative_unknown_null_and_raw_transform(self):
        response = native_response()
        before = deepcopy(response)
        output = self.validate(response)
        metadata = output["result"].pop("descriptor_validation")
        self.assertEqual(output, before)
        self.assertEqual(response, before)
        self.assertTrue(metadata["valid"])
        self.assertEqual(metadata["unknown_type_count"], 1)
        self.assertEqual(metadata["negative_type_count"], 1)
        self.assertFalse(metadata["mesh_conversion_admitted"])
        self.assertFalse(metadata["coordinate_frame_verified"])
        self.assertFalse(metadata["units_verified"])
        items = output["result"]["trees"][0]["items"]
        self.assertEqual(items[0]["rdata"], [300.0, 70.0, 70.0])
        self.assertEqual(items[0]["transform_as_array12"], TRANSFORM)
        self.assertEqual(items[0]["local_limits"]["frame"], "untransformed")
        self.assertEqual(items[1]["type"]["name"], "CSG_NEG_DISH")
        self.assertEqual(items[2]["type"], {"name": "999", "value": 999, "known": False})
        self.assertIsNone(items[-1]["transform_as_array12"])
        self.assertIsNone(items[-1]["rdata"])
        self.assertEqual(items[0]["idata"], [])
        items[0]["rdata"][0] = 999.0
        self.assertEqual(response, before, "No native arrays or cross-request cache may be aliased.")

    def test_name_and_numeric_alias_requests_match_observed_identity(self):
        for request in ("/vessel-101/dish", "=02000000001/02740"):
            response = native_response()
            response["result"]["requested_refno"] = request
            self.assertTrue(self.validate(response, requested_refno=request)["ok"])
        for request in ("/OTHER", "=1/123"):
            response = native_response()
            response["result"]["requested_refno"] = request
            self.refused(response, "generated_csg_source_mismatch", requested_refno=request)

    def test_additional_and_repeated_tree_sources_keep_order_and_item_identity(self):
        response = native_response()
        second = deepcopy(response["result"]["trees"][0])
        second.update(index=1, source={"refno": "=2000000001/9876", "name": "/GENERATED/SOURCE", "type": "SCOM"}, source_matches_requested=False)
        response["result"]["trees"].append(second)
        repeated = deepcopy(second)
        repeated["index"] = 2
        response["result"]["trees"].append(repeated)
        recount(response)
        output = self.validate(response)
        self.assertTrue(output["ok"], output)
        self.assertEqual(output["result"]["trees"], response["result"]["trees"])
        self.assertEqual(output["result"]["counts"]["returned_item_count"], 12)

    def test_transform_is_exactly_twelve_finite_raw_values_without_matrix_guessing(self):
        for length in (0, 11, 13, 16):
            response = native_response()
            response["result"]["trees"][0]["items"][0]["transform_as_array12"] = [1.0] * length
            self.refused(response, "generated_csg_transform_length_invalid")
        response = native_response()
        response["result"]["trees"][0]["items"][0]["transform_as_array12"] = [2.5] * 12
        recount(response)
        output = self.validate(response)
        self.assertTrue(output["ok"], output)
        self.assertEqual(output["result"]["trees"][0]["items"][0]["transform_as_array12"], [2.5] * 12)
        self.assertFalse(output["result"]["descriptor_validation"]["coordinate_frame_verified"])

    def test_claims_of_native_mesh_world_units_or_bounded_native_work_are_rejected(self):
        for field in ("native_tessellation", "cuts_applied", "post_boolean_mesh_verified", "transform_convention_verified",
                      "raw_data_layout_verified", "native_work_bounded", "native_cancellation_supported", "atomic_snapshot"):
            response = native_response()
            response["result"][field] = True
            with self.subTest(field=field):
                self.refused(response, "generated_csg_unverified_claim")
        for field, value in (("units", "mm"), ("coordinate_frame", "WORLD")):
            response = native_response()
            response["result"][field] = value
            self.refused(response, "generated_csg_coordinate_claim_invalid")

    def test_counts_indices_limits_and_type_contradictions_fail_closed(self):
        faults = [
            lambda r: r["counts"].update(returned_item_count=3),
            lambda r: r["counts"].update(observed_item_count=5),
            lambda r: r["counts"].update(copied_array_value_count=0),
            lambda r: r["limits"].update(max_items=63),
            lambda r: r["trees"][0].update(index=True),
            lambda r: r["trees"][0].update(item_count=5),
            lambda r: r["trees"][0].update(source_matches_requested=False),
            lambda r: r["trees"][0]["items"][0].update(index=1),
            lambda r: r["trees"][0]["items"][0]["type"].update(name="CSG_NEG_DISH"),
            lambda r: r["trees"][0]["items"][1]["type"].update(value=9),
            lambda r: r["trees"][0]["items"][2]["type"].update(known=True),
            lambda r: r["trees"][0]["items"][0].update(rdata_length=4),
            lambda r: r["trees"][0]["items"][0].update(idata=[True], idata_length=1),
            lambda r: r["trees"][0]["items"][0]["local_limits"].update(frame="WORLD"),
            lambda r: r["trees"][0]["items"][0]["local_limits"].update(min=[301.0, 0.0, 0.0]),
        ]
        for index, fault in enumerate(faults):
            response = native_response()
            fault(response["result"])
            with self.subTest(fault=index):
                self.refused(response)

    def test_options_context_source_cleanup_and_quarantine_cannot_claim_success(self):
        faults = [
            lambda r: r["options"]["after"].update(holes=True),
            lambda r: r["options"].update(restored=False),
            lambda r: r["options"]["before"].pop("level"),
            lambda r: r["options"]["requested"].update(tube=1),
            lambda r: r["context"].update(ce_after="=1/999"),
            lambda r: r["context"].update(project_after="CHANGED"),
            lambda r: r["context"].update(mdb_after="CHANGED"),
            lambda r: r["context"].update(identity_unchanged=False),
            lambda r: r["source_after"].update(name="/CHANGED"),
            lambda r: r.update(source_unchanged=False),
            lambda r: r["resources"].update(complete=False),
            lambda r: r["resources"].update(borrowed_dispose_attempted_count=1),
            lambda r: r["generated_csg_quarantine"].update(active=True),
        ]
        for index, fault in enumerate(faults):
            response = native_response()
            fault(response["result"])
            with self.subTest(fault=index):
                self.refused(response)

    def test_effective_options_preserve_raw_drift_only_for_both_disabled_channels(self):
        response = native_response()
        options = response["result"]["options"]
        for field in ("before", "after"):
            options[field].update(insulation=False, obstruction=False)
        options["before"].update(insulation_translucency=1351829404, obstruction_translucency=6605876)
        options["after"].update(insulation_translucency=1037075232, obstruction_translucency=9259688)
        effective_policy(response)
        output = self.validate(response)
        self.assertTrue(output["ok"], output)
        retained = output["result"]["options"]
        self.assertFalse(retained["raw_restored"])
        self.assertTrue(retained["effective_restored"])
        self.assertEqual(retained["raw_mismatched_fields"], ["insulation_translucency", "obstruction_translucency"])
        self.assertEqual(retained["before"]["insulation_translucency"], 1351829404)
        self.assertIsNone(retained["effective_before"]["insulation_translucency"])
        self.assertTrue(output["result"]["success"])
        for channel in ("insulation", "obstruction"):
            for side in ("before", "after"):
                corrupted = deepcopy(response)
                corrupted["result"]["options"][side][channel] = True
                effective_policy(corrupted)
                self.refused(corrupted, "generated_csg_options_not_restored")

    def test_effective_options_are_independently_recomputed_from_every_raw_field(self):
        baseline = effective_policy(native_response())
        for key, raw_value in baseline["result"]["options"]["before"].items():
            for field in ("effective_before", "effective_after"):
                response = deepcopy(baseline)
                value = response["result"]["options"][field][key]
                response["result"]["options"][field][key] = not value if type(value) is bool else 99
                self.refused(response, "generated_csg_options_effective_mismatch")
        for field, value in (("raw_mismatched_fields", ["level"]), ("effective_mismatched_fields", ["level"]),
                             ("inactive_fields", ["level"]), ("raw_restored", False), ("effective_restored", False)):
            response = deepcopy(baseline)
            response["result"]["options"][field] = value
            self.refused(response)
        for key in contract._BOOL_OPTIONS | {"level"}:
            response = native_response()
            value = response["result"]["options"]["after"][key]
            response["result"]["options"]["after"][key] = not value if type(value) is bool else value + 1
            effective_policy(response)
            self.refused(response, "generated_csg_options_not_restored")

    def test_effective_policy_keeps_partial_generation_failure_without_comparison_claim(self):
        response = native_response()
        result = response["result"]
        result.update(success=False, complete=False, errors=[{"stage": "generation", "code": "generated_csg_quarantined", "message": "failed"}])
        result["options"].update(after=None, restored=None)
        result["generated_csg_quarantine"]["active"] = True
        effective_policy(response)
        output = self.validate(response)
        self.assertTrue(output["ok"], output)
        self.assertFalse(output["result"]["success"])
        self.assertIsNone(output["result"]["options"]["effective_after"])
        self.assertIsNone(output["result"]["options"]["effective_restored"])
        self.assertTrue(output["result"]["generated_csg_quarantine"]["active"])

    def test_truncated_prefix_remains_failed_and_preserves_observed_total_scope(self):
        response = native_response()
        result = response["result"]
        result.update(success=False, complete=False, truncated=True, truncation_reasons=["item_limit"])
        result["limits"]["max_items"] = 1
        result["trees"][0]["items"] = result["trees"][0]["items"][:1]
        result["trees"][0]["complete"] = False
        recount(response, total_trees=3)
        output = self.validate(response, max_items=1)
        self.assertTrue(output["ok"], output)
        self.assertFalse(output["result"]["success"])
        self.assertFalse(output["result"]["complete"])
        self.assertEqual(output["result"]["counts"]["observed_item_count"], 4)
        self.assertEqual(output["result"]["counts"]["returned_item_count"], 1)
        self.assertEqual(output["result"]["counts"]["item_count_scope"], "inspected_trees_only")
        self.assertFalse(output["result"]["descriptor_validation"]["generation_complete"])
        self.assertFalse(output["result"]["descriptor_validation"]["mesh_conversion_admitted"])

    def test_failed_raw_array_copy_keeps_null_and_reported_native_length(self):
        response = native_response()
        result = response["result"]
        error = {"stage": "trees[0].items[0]", "code": "array_value_limit", "message": "No prefix substituted.\nStopped inspection."}
        item = result["trees"][0]["items"][0]
        item.update(rdata=None, rdata_length=4097, idata=None, idata_length=None,
                    transform_as_array12=None, local_limits=None, complete=False, errors=[error])
        result["trees"][0].update(items=[item], complete=False)
        result.update(success=False, complete=False, truncated=True, truncation_reasons=["array_value_limit"], errors=[error])
        recount(response)
        output = self.validate(response)
        self.assertTrue(output["ok"], output)
        retained = output["result"]["trees"][0]["items"][0]
        self.assertIsNone(retained["rdata"])
        self.assertEqual(retained["rdata_length"], 4097)
        self.assertEqual(retained["errors"], [error])
        self.assertFalse(output["result"]["success"])

    def test_native_generation_failure_preserves_quarantine_and_unobserved_fields(self):
        response = native_response()
        result = response["result"]
        result.update(success=False, complete=False, trees=[], generation_returned=False,
                      generated_geometry_available=False, source_after=None, source_unchanged=None,
                      errors=[{"stage": "generation", "code": "generated_csg_quarantined", "message": "Shared export state is uncertain."}])
        result["counts"].update(total_tree_count=None, observed_tree_count=0, observed_item_count=0,
                                returned_tree_count=0, returned_item_count=0, copied_array_value_count=0)
        result["options"].update(after=None, restored=None)
        result["context"].update(ce_after=None, ce_unchanged=None, project_after=None, mdb_after=None, identity_unchanged=None)
        result["resources"]["complete"] = False
        result["generated_csg_quarantine"]["active"] = True
        output = self.validate(response)
        self.assertTrue(output["ok"], output)
        self.assertFalse(output["result"]["success"])
        self.assertTrue(output["result"]["generated_csg_quarantine"]["active"])
        self.assertIsNone(output["result"]["options"]["restored"])
        self.assertIsNone(output["result"]["counts"]["total_tree_count"])

    def test_transport_preflight_and_mock_failures_keep_their_original_reason(self):
        empty_result = {"success": False, "available": False, "complete": False, "trees": []}
        for response in ({"ok": False, "error": "Transport disconnected", "outcome_uncertain": True},
                         {"ok": False, "version": "E3D21", "error": "generated CSG is unavailable in mock mode"},
                         {"ok": False, "error": "E3D session changed during generated CSG observation.",
                          "result": deepcopy(empty_result), "expected_session_id": "one", "observed_session_id": "two"},
                         {"ok": True, "version": "E3D21", "result": {**deepcopy(empty_result), "source": "mock",
                          "refno": REFERENCE, "error": "Native generated CSG requires a running E3D bridge"}}):
            output = self.validate(response)
            self.assertEqual(output, response)
            self.assertIsNot(output, response)

    def test_empty_failure_bypass_never_admits_descriptors_or_success_claims(self):
        original = {"ok": True, "version": "E3D21", "result": {"source": "mock", "refno": REFERENCE,
            "success": False, "available": False, "complete": False, "trees": [], "error": "mock unavailable"}}
        mutations = [("success", True), ("available", True), ("complete", True),
                     ("trees", [native_response()["result"]["trees"][0]]),
                     ("operation", "generated_csg"), ("native_tessellation", True)]
        for field, value in mutations:
            with self.subTest(field=field):
                response = deepcopy(original)
                response["result"][field] = value
                self.refused(response)

    def test_arrays_json_and_string_work_budgets_are_enforced_before_return(self):
        response = native_response()
        item = response["result"]["trees"][0]["items"][0]
        item.update(rdata=[1.0] * 4097, rdata_length=4097)
        self.refused(response, "generated_csg_array_limit_exceeded")
        response = native_response()
        items = []
        for index in range(17):
            item = native_item(index, rdata=[1.0] * 4096)
            items.append(item)
        response["result"]["trees"][0].update(items=items, item_count=len(items))
        recount(response)
        self.refused(response, "generated_csg_total_array_limit_exceeded")
        with patch.object(contract, "MAX_RECEIPT_BYTES", 1024):
            self.refused(native_response(), "generated_csg_output_limit_exceeded")
        response = native_response()
        response["oversized"] = "x" * 8193
        self.refused(response, "generated_csg_json_structure_limit_exceeded")
        cyclic = native_response()
        cyclic["cycle"] = cyclic
        self.refused(cyclic, "generated_csg_invalid_json")

    def test_malformed_values_and_request_parameters_always_return_structured_failure(self):
        for value in (None, [], True, {"ok": 1}, {"ok": True, "version": "E3D3"}):
            self.refused(value if value is not None else {"bad": None})
        for field in ("rdata", "idata", "transform_as_array12"):
            for number in (math.nan, math.inf, True, "1", 10 ** 400):
                response = native_response()
                item = response["result"]["trees"][0]["items"][0]
                if field == "idata":
                    item[field] = [number]
                    item["idata_length"] = 1
                else:
                    item[field][0] = number
                self.refused(response)
        for kwargs in ({"requested_refno": "CE"}, {"requested_refno": "=0/1"}, {"max_trees": True},
                       {"max_trees": 17}, {"max_items": 257}, {"max_items": 0}):
            self.refused(native_response(), **kwargs)

    def test_typed_facet_group_preserves_raw_coordinates_and_counts(self):
        response = typed_facet_response()
        before = deepcopy(response)
        output = self.validate(response)
        metadata = output["result"].pop("descriptor_validation")
        self.assertTrue(output["ok"], output)
        self.assertEqual(output, before)
        item = output["result"]["trees"][0]["items"][-1]
        group = item["facet_group"]
        self.assertEqual(group["source"], "CSGFacetGroup.Facets")
        self.assertEqual(group["units"], "sdk_raw_unverified")
        self.assertEqual(group["coordinate_frame"], "sdk_facet_coordinates_unverified")
        self.assertEqual(group["facets"][0]["loops"][0]["vertices"][1], [4.0, 5.0, 6.0])
        self.assertEqual(output["result"]["counts"]["facet_groups"], {
            "returned_group_count": 1, "returned_facet_count": 1,
            "returned_loop_count": 1, "copied_coordinate_value_count": 18,
        })
        self.assertFalse(metadata["coordinate_frame_verified"])
        self.assertFalse(metadata["units_verified"])

    def test_typed_facet_group_requires_complete_group_and_exact_structure(self):
        faults = [
            lambda r: r["result"]["trees"][0]["items"][-1].pop("facet_group"),
            lambda r: r["result"]["trees"][0]["items"][-1].update(facet_group=None),
            lambda r: r["result"]["trees"][0]["items"][-1]["facet_group"].update(source="raw"),
            lambda r: r["result"]["trees"][0]["items"][-1]["facet_group"]["facets"][0]["loops"][0]["vertices"][0].append(4.0),
            lambda r: r["result"]["trees"][0]["items"][-1]["facet_group"]["facets"][0]["loops"][0]["normals"][0].__setitem__(0, math.nan),
        ]
        for index, fault in enumerate(faults):
            response = typed_facet_response()
            fault(response)
            with self.subTest(fault=index):
                self.refused(response)

    def test_typed_facet_group_loop_and_request_coordinate_budgets_fail_closed(self):
        response = typed_facet_response()
        group = response["result"]["trees"][0]["items"][-1]["facet_group"]
        group["facets"][0]["loops"][0]["vertices"] = [[1.0, 2.0, 3.0]] * 1025
        self.refused(response, "generated_csg_facet_loop_entry_limit_exceeded")

        response = typed_facet_response()
        group = response["result"]["trees"][0]["items"][-1]["facet_group"]
        loop = group["facets"][0]["loops"][0]
        group["facets"] = [{"loops": [deepcopy(loop)]} for _ in range(1024)]
        self.refused(response, "generated_csg_facet_coordinate_value_limit_exceeded")


class GeneratedCsgNativeReplayTests(unittest.TestCase):
    """Replay retained native evidence without contacting the bridge or SDK."""

    def replay(self, relative_path):
        saved = json.loads((ROOT / relative_path).read_text(encoding="utf-8-sig"))
        response = saved["structuredContent"]
        original = deepcopy(response)
        result = response["result"]
        output = contract.validate_generated_csg_response(response, result["requested_refno"],
            result["limits"]["max_trees"], result["limits"]["max_items"])
        self.assertEqual(response, original, "Offline validation must not mutate retained native evidence.")
        metadata = output["result"].pop("descriptor_validation")
        original["result"].pop("descriptor_validation", None)
        self.assertEqual(output, original, "Validation must retain every raw observation and status.")
        self.assertTrue(metadata["valid"])
        self.assertFalse(metadata["mesh_conversion_admitted"])
        self.assertFalse(metadata["coordinate_frame_verified"])
        self.assertFalse(metadata["units_verified"])
        return output["result"], metadata

    @unittest.skipUnless((ROOT / "artifacts/generated-csg-native-20261120-r4").is_dir(), "requires local native replay receipts")
    def test_successful_native_cylinder_dishes_and_facet_group_preserve_raw_receipts(self):
        cases = [
            ("generated-csg-native-20261120-r3", "source-01", "CSG_CYLI"),
            ("generated-csg-native-20261120-r4", "source-01", "CSG_DISH"),
            ("generated-csg-native-20261120-r4", "source-02", "CSG_DISH"),
            ("generated-csg-native-20261120-r4", "source-03", "CSG_DISH"),
            ("generated-csg-native-20261120-r4", "source-04", "CSG_FACET_GROUP"),
        ]
        for directory, source, native_type in cases:
            with self.subTest(source=f"{directory}/{source}"):
                result, metadata = self.replay(f"artifacts/{directory}/{source}-generated.mcp.json")
                self.assertTrue(result["success"])
                self.assertTrue(result["complete"])
                self.assertTrue(metadata["generation_complete"])
                self.assertEqual([item["type"]["name"] for tree in result["trees"] for item in tree["items"]],
                                 [native_type])
                self.assertTrue(result["options"]["effective_restored"])
                self.assertFalse(result["options"]["raw_restored"])
                self.assertFalse(result["native_tessellation"])
                self.assertFalse(result["raw_data_layout_verified"])
                self.assertFalse(result["transform_convention_verified"])

    @unittest.skipUnless((ROOT / "artifacts/generated-csg-native-20261120-r1").is_dir(), "requires local native replay receipts")
    def test_native_quarantine_failure_remains_a_failure_with_original_observations(self):
        result, metadata = self.replay(
            "artifacts/generated-csg-native-20261120-r1/source-01-generated.mcp.json")
        self.assertFalse(result["success"])
        self.assertFalse(result["complete"])
        self.assertFalse(metadata["generation_complete"])
        self.assertTrue(result["generated_csg_quarantine"]["active"])
        self.assertEqual(result["trees"], [])
        self.assertTrue(result["errors"])


if __name__ == "__main__":
    unittest.main()
