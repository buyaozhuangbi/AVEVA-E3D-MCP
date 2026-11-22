"""Pure historical diagnostic comparisons using independent synthetic evidence."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp import tools as tool_module
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.diagnostic_comparison import compare_diagnostic_receipts
from aveva_e3d_mcp.diagnostic_receipts import DiagnosticReceiptStore
from aveva_e3d_mcp.protocol import ImageToolResult
from test_diagnosis_receipt import REFNO, full_receipt, metadata
from test_diagnostic_receipts import FakeClock, no_bridge


SECTIONS = ("target", "snapshot", "schema", "bounds", "geometry", "display", "baseline_view", "final_view",
            "baseline_selection", "final_selection", "image")
CLIPPING_SECTIONS = ("baseline_clipping", "final_clipping")
FIRST_ID = "diag_" + "1" * 32
SECOND_ID = "diag_" + "2" * 32


def fixture(include_clipping=False):
    return copy.deepcopy(metadata(full_receipt(True, include_clipping=include_clipping)))


def change_context(value, field, new_value):
    evidence = value["result"]["evidence"]
    statuses = [evidence["initial_status"], evidence["final_status"]]
    statuses.extend(row["status"] for row in evidence["gates"])
    projects = [evidence["initial_project"], evidence["final_project"]]
    projects.extend(row["project"] for row in evidence["gates"])
    capture = evidence["capture"]["result"]["observation"]
    statuses.extend((capture["before"]["result"]["context_before"],
                     capture["before"]["result"]["context_after"], capture["context_after"]))
    if field in ("session_id", "bridge_build", "version"):
        for status in statuses:
            status[field] = new_value
    else:
        for project in projects:
            project["project"][field] = new_value


class DiagnosticComparisonTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = DiagnosticReceiptStore("E3D21", clock=self.clock, utc_clock=self.clock.utc)

    def save(self, value):
        with no_bridge():
            saved = self.store.put(value)
        self.assertEqual(saved["status"], "stored", saved)
        return saved["receipt_id"]

    def compare(self, before, after, *, difference_limit=100):
        before_id, after_id = self.save(before), self.save(after)
        with no_bridge(), patch.object(self.store, "get", wraps=self.store.get) as get:
            response = compare_diagnostic_receipts(self.store, before_id, after_id, difference_limit=difference_limit)
        self.assertEqual(get.call_args_list, [call(before_id, include_image=False), call(after_id, include_image=False)])
        self.assertIsInstance(response, dict)
        self.assertTrue(response["ok"])
        self.assertEqual(response["version"], "E3D21")
        result = response["result"]
        self.assertTrue(result["success"])
        self.assertTrue(result["historical"])
        self.assertFalse(result["live_read"])
        expected_sections = list(SECTIONS)
        if any(metadata(value)["result"].get("clipping_requested") is True or
               any(key in metadata(value)["result"]["evidence"] for key in CLIPPING_SECTIONS)
               for value in (before, after)):
            expected_sections.extend(CLIPPING_SECTIONS)
        self.assertEqual([section["name"] for section in result["sections"]], expected_sections)
        self.assertEqual(result["difference_limit"], difference_limit)
        return result

    def section(self, result, name):
        return next(section for section in result["sections"] if section["name"] == name)

    def assert_not_comparable(self, result):
        self.assertEqual(result["status"], "not_comparable")
        self.assertFalse(result["compatibility"]["comparable"])
        self.assertTrue(result["compatibility"]["reasons"])
        self.assertIsNone(result["difference_count"])
        self.assertIsNone(result["differences_found"])
        self.assertFalse(result["traversal_complete"])
        self.assertFalse(result["truncated"])
        self.assertEqual(result["differences"], [])
        self.assertTrue(all(section["structured_equal"] is None for section in result["sections"]))

    def test_equal_historical_evidence_is_comparable_despite_incomplete_engineering_coverage(self):
        before, after = fixture(), fixture()
        result = self.compare(before, after)
        self.assertEqual(result["status"], "compared")
        self.assertTrue(result["compatibility"]["comparable"])
        self.assertEqual(result["compatibility"]["reasons"], [])
        self.assertTrue(result["traversal_complete"])
        self.assertFalse(result["truncated"])
        self.assertIs(result["differences_found"], False)
        self.assertEqual(result["difference_count"], 0)
        self.assertEqual(result["returned_difference_count"], 0)
        self.assertEqual(result["differences"], [])
        self.assertTrue(all(section["structured_equal"] is True for section in result["sections"]))
        for side, original in (("before", before), ("after", after)):
            summary = result[side]["summary"]
            self.assertFalse(summary["complete"])
            self.assertEqual(summary["unknown"], original["result"]["unknown"])
            self.assertEqual(summary["errors"], original["result"]["errors"])

    def test_target_rename_is_a_difference_without_identity_incompatibility(self):
        before, after = fixture(), fixture()
        after["result"]["target"]["name"] = "/RENAMED-PART"
        result = self.compare(before, after)
        self.assertTrue(result["compatibility"]["comparable"])
        self.assertFalse(self.section(result, "target")["structured_equal"])
        self.assertEqual(result["difference_count"], 1)
        self.assertEqual(result["differences"][0]["path"], "/result/target/name")

    def test_two_legacy_or_unrequested_receipts_keep_the_original_section_selection(self):
        for before_flag, after_flag in ((None, None), (False, False), (None, False)):
            with self.subTest(before_flag=before_flag, after_flag=after_flag):
                before, after = fixture(), fixture()
                if before_flag is not None: before["result"]["clipping_requested"] = before_flag
                if after_flag is not None: after["result"]["clipping_requested"] = after_flag
                result = self.compare(before, after)
                self.assertEqual([row["name"] for row in result["sections"]], list(SECTIONS))
                self.assertEqual(result["scope"]["missing_sections"], [])
                self.assertFalse(result["differences_found"])

    def test_requested_clipping_samples_are_structurally_compared_with_future_fields(self):
        before, after = fixture(True), fixture(True)
        for value in (before, after):
            for name in CLIPPING_SECTIONS:
                value["result"]["evidence"][name]["result"]["future_native_field"] = {"raw": [1, True, None]}
        result = self.compare(before, after)
        self.assertTrue(result["compatibility"]["comparable"])
        self.assertEqual(result["difference_count"], 0)
        self.assertTrue(all(self.section(result, name)["structured_equal"] is True for name in CLIPPING_SECTIONS))
        self.assertTrue(result["before"]["summary"]["clipping_requested"])
        self.assertTrue(result["after"]["summary"]["clipping_requested"])
        for name in CLIPPING_SECTIONS:
            changed = copy.deepcopy(after)
            changed["result"]["evidence"][name]["result"]["future_native_field"]["raw"][0] = True
            compared = self.compare(before, changed)
            self.assertEqual(compared["difference_count"], 1)
            difference = compared["differences"][0]
            self.assertEqual(difference["section"], name)
            self.assertEqual(difference["path"], "/result/evidence/" + name + "/result/future_native_field/raw/0")
            self.assertEqual(difference["kind"], "type_changed")

    def test_old_new_receipts_treat_missing_clipping_as_unknown_in_both_directions(self):
        for old_side in ("before", "after"):
            with self.subTest(old_side=old_side):
                before, after = (fixture(), fixture(True)) if old_side == "before" else (fixture(True), fixture())
                result = self.compare(before, after)
                self.assertTrue(result["compatibility"]["comparable"])
                self.assertEqual(result["differences"], [])
                self.assertIsNone(result["differences_found"])
                self.assertEqual(result["scope"]["missing_sections"], list(CLIPPING_SECTIONS))
                for name in CLIPPING_SECTIONS:
                    section = self.section(result, name)
                    self.assertIsNone(section["structured_equal"])
                    self.assertEqual(section["before_present"], old_side != "before")
                    self.assertEqual(section["after_present"], old_side != "after")

    def test_clipping_evidence_selects_sections_even_without_request_metadata(self):
        for flag in (None, False):
            with self.subTest(flag=flag):
                before, after = fixture(True), fixture(True)
                for value in (before, after):
                    if flag is None: value["result"].pop("clipping_requested")
                    else: value["result"]["clipping_requested"] = flag
                after["result"]["evidence"]["final_clipping"]["result"]["clipping_enabled"] = True
                result = self.compare(before, after)
                self.assertEqual(result["difference_count"], 1)
                self.assertEqual(result["differences"][0]["path"],
                                 "/result/evidence/final_clipping/result/clipping_enabled")

    def test_requested_but_missing_or_partial_clipping_preserves_unknown_coverage(self):
        before, after = fixture(), fixture()
        before["result"]["clipping_requested"] = True
        result = self.compare(before, after)
        self.assertIsNone(result["differences_found"])
        self.assertEqual(result["scope"]["missing_sections"], list(CLIPPING_SECTIONS))
        for name in CLIPPING_SECTIONS:
            self.assertFalse(self.section(result, name)["before_present"])
            self.assertFalse(self.section(result, name)["after_present"])

        before, after = fixture(True), fixture(True)
        for value in (before, after):
            value["result"]["observations"].update(clipping_samples_unchanged=None, observed_window_unchanged=None)
            value["result"]["unknown"].append({"code": "clipping_observation_unverified", "details": ["partial"]})
            value["result"]["evidence"]["final_clipping"]["result"].update(
                success=False, complete=False, status="partial", box_data=None, read_errors={"box_data": "failed"})
        result = self.compare(before, after)
        self.assertTrue(result["compatibility"]["comparable"])
        self.assertTrue(self.section(result, "final_clipping")["structured_equal"])
        for side, value in (("before", before), ("after", after)):
            self.assertEqual(result[side]["summary"]["unknown"], value["result"]["unknown"])
            self.assertIsNone(result[side]["summary"]["observations"]["clipping_samples_unchanged"])
        del after["result"]["evidence"]["final_clipping"]
        missing = self.compare(before, after)
        self.assertIsNone(self.section(missing, "final_clipping")["structured_equal"])
        self.assertIsNone(missing["differences_found"])

    def test_each_evidence_section_reports_its_own_absolute_json_pointer(self):
        for section in SECTIONS:
            with self.subTest(section=section):
                before, after = fixture(), fixture()
                if section in {"target", "image"}:
                    selected, path = after["result"][section], "/result/" + section
                else:
                    selected, path = after["result"]["evidence"][section], "/result/evidence/" + section
                selected["synthetic_change"] = 42
                result = self.compare(before, after)
                self.assertTrue(result["compatibility"]["comparable"])
                self.assertEqual(result["difference_count"], 1)
                self.assertEqual(result["differences"][0]["section"], section)
                self.assertEqual(result["differences"][0]["path"], path + "/synthetic_change")

    def test_dictionary_order_does_not_change_structured_equality(self):
        before, after = fixture(), fixture()
        after["result"]["evidence"]["geometry"] = dict(reversed(list(after["result"]["evidence"]["geometry"].items())))
        result = self.compare(before, after)
        self.assertEqual(result["differences"], [])
        self.assertTrue(self.section(result, "geometry")["structured_equal"])

    def test_array_order_json_types_pointer_escaping_and_absent_null_are_distinct(self):
        before, after = fixture(), fixture()
        before["result"]["evidence"]["geometry"]["typed"] = {"a/b~c": [True, 1, 1.0], "removed": None}
        after["result"]["evidence"]["geometry"]["typed"] = {"a/b~c": [1, 1.0, True], "added": None}
        result = self.compare(before, after)
        rows = {row["path"]: row for row in result["differences"]}
        prefix = "/result/evidence/geometry/typed/"
        self.assertEqual(result["difference_count"], 5)
        for index, types in enumerate((("boolean", "integer"), ("integer", "number"), ("number", "boolean"))):
            row = rows[prefix + "a~1b~0c/" + str(index)]
            self.assertEqual(row["kind"], "type_changed")
            self.assertEqual((row["before"]["type"], row["after"]["type"]), types)
        self.assertEqual(rows[prefix + "removed"]["kind"], "removed")
        self.assertEqual(rows[prefix + "removed"]["after"], {"present": False})
        self.assertEqual(rows[prefix + "removed"]["before"]["type"], "null")
        self.assertEqual(rows[prefix + "added"]["kind"], "added")
        self.assertEqual(rows[prefix + "added"]["before"], {"present": False})
        self.assertEqual(rows[prefix + "added"]["after"]["value"], None)

    def test_missing_entire_section_is_unknown_instead_of_equal_or_deleted(self):
        for missing in ("before", "after", "both"):
            with self.subTest(missing=missing):
                before, after = fixture(), fixture()
                for side, value in (("before", before), ("after", after)):
                    if missing in (side, "both"):
                        del value["result"]["evidence"]["bounds"]
                        del value["result"]["image"]
                result = self.compare(before, after)
                self.assertTrue(result["compatibility"]["comparable"])
                self.assertEqual(result["differences"], [])
                self.assertIsNone(result["differences_found"])
                for name in ("bounds", "image"):
                    section = self.section(result, name)
                    self.assertIsNone(section["structured_equal"])
                    self.assertEqual(section["before_present"], missing not in {"before", "both"})
                    self.assertEqual(section["after_present"], missing not in {"after", "both"})

    def test_session_build_project_mdb_or_user_mismatch_prevents_comparison(self):
        for field, replacement in (("session_id", "another-session"), ("bridge_build", "another-build"),
                                   ("name", "ANOTHER_PROJECT"), ("mdb", "OTHER_MDB"), ("user", "ANOTHER_USER")):
            with self.subTest(field=field):
                before, after = fixture(), fixture()
                change_context(after, field, replacement)
                self.assert_not_comparable(self.compare(before, after))

    def test_target_reference_type_and_noncanonical_identity_prevent_comparison(self):
        for field, replacement in (("refno", "=2000000001/9999"), ("type", "BOX"), ("refno", "/ALIAS")):
            with self.subTest(field=field, replacement=replacement):
                before, after = fixture(), fixture()
                after["result"]["target"][field] = replacement
                self.assert_not_comparable(self.compare(before, after))

    def test_unstable_missing_context_and_incomplete_workflow_prevent_comparison(self):
        mutations = (
            lambda result: result.update(workflow_completed=False),
            lambda result: result["observations"].update(session_build_version_unchanged=False),
            lambda result: result["observations"].update(project_mdb_user_unchanged=None),
            lambda result: result["evidence"]["final_status"].update(session_id="drifted-session"),
            lambda result: result["evidence"].pop("initial_project"),
        )
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=index):
                before, after = fixture(), fixture()
                mutation(after["result"])
                self.assert_not_comparable(self.compare(before, after))

    def test_uncertain_window_observation_does_not_hide_comparable_historical_evidence(self):
        before, after = fixture(), fixture()
        after["result"]["observations"]["observed_window_unchanged"] = None
        after["result"]["unknown"].append({"code": "view_observation_unverified"})
        result = self.compare(before, after)
        self.assertTrue(result["compatibility"]["comparable"])
        self.assertEqual(result["after"]["summary"]["unknown"], after["result"]["unknown"])
        self.assertEqual(result["after"]["summary"]["observations"], after["result"]["observations"])
        self.assertFalse(result["after"]["summary"]["complete"])

    def test_failed_diagnosis_remains_successful_lookup_with_original_errors(self):
        before, after = fixture(), fixture()
        after["result"].update(success=False, workflow_completed=False, stopped_stage="geometry",
                               errors=[{"code": "native_fault", "message": "Synthetic historical failure"}])
        result = self.compare(before, after)
        self.assert_not_comparable(result)
        self.assertFalse(result["after"]["summary"]["success"])
        self.assertEqual(result["after"]["summary"]["errors"], after["result"]["errors"])
        self.assertEqual(result["after"]["summary"]["unknown"], after["result"]["unknown"])

    def test_exact_difference_limit_is_complete_but_next_difference_truncates(self):
        for difference_count, limit, truncated in ((1, 1, False), (2, 1, True), (200, 200, False), (201, 200, True)):
            with self.subTest(count=difference_count, limit=limit):
                before, after = fixture(), fixture()
                before["result"]["evidence"]["geometry"]["values"] = [0] * difference_count
                after["result"]["evidence"]["geometry"]["values"] = [1] * difference_count
                result = self.compare(before, after, difference_limit=limit)
                self.assertEqual(result["truncated"], truncated)
                self.assertEqual(result["traversal_complete"], not truncated)
                self.assertEqual(result["difference_count"], None if truncated else difference_count)
                self.assertEqual(result["returned_difference_count"], min(difference_count, limit))
                self.assertEqual(len(result["differences"]), min(difference_count, limit))

    def test_node_budget_prevents_false_equality_for_large_identical_evidence(self):
        before, after = fixture(), fixture()
        before["result"]["evidence"]["geometry"]["large"] = list(range(22000))
        after["result"]["evidence"]["geometry"]["large"] = list(range(22000))
        result = self.compare(before, after)
        self.assertTrue(result["compatibility"]["comparable"])
        self.assertFalse(result["traversal_complete"])
        self.assertTrue(result["truncated"])
        self.assertIsNone(result["difference_count"])
        self.assertIsNone(self.section(result, "geometry")["structured_equal"])

    def test_depth_budget_prevents_false_equality_for_deep_identical_evidence(self):
        before, after = fixture(), fixture()
        value = "leaf"
        for _ in range(70):
            value = {"nested": value}
        before["result"]["evidence"]["geometry"]["deep"] = copy.deepcopy(value)
        after["result"]["evidence"]["geometry"]["deep"] = copy.deepcopy(value)
        result = self.compare(before, after)
        self.assertFalse(result["traversal_complete"])
        self.assertTrue(result["truncated"])
        self.assertIsNone(result["difference_count"])
        self.assertIsNone(self.section(result, "geometry")["structured_equal"])

    def test_long_differing_values_use_bounded_hashed_previews(self):
        before, after = fixture(), fixture()
        before["result"]["evidence"]["geometry"]["large"] = "A" * 100000
        after["result"]["evidence"]["geometry"]["large"] = "B" * 100000
        result = self.compare(before, after)
        row = result["differences"][0]
        for side in ("before", "after"):
            self.assertNotIn("value", row[side])
            self.assertTrue(row[side]["preview"]["omitted"])
            self.assertEqual(row[side]["preview"]["encoding"], "canonical_json")
            self.assertRegex(row[side]["preview"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(row["before"]["preview"]["sha256"], row["after"]["preview"]["sha256"])
        self.assertLess(len(json.dumps(result)), 20000)

    def test_unbounded_values_and_json_pointer_keys_have_bounded_safe_previews(self):
        before, after = fixture(), fixture()
        huge_key = "x/~" * 2000
        huge_value = "long-value-" * 110000
        after["result"]["evidence"]["geometry"][huge_key] = huge_value
        result = self.compare(before, after)
        self.assertEqual(result["difference_count"], 1)
        row = result["differences"][0]
        self.assertIsNone(row["path"])
        self.assertTrue(row["path_truncated"])
        self.assertLessEqual(len(row["path_preview"].encode("utf-8")), 1024)
        self.assertRegex(row["path_sha256"], r"^[0-9a-f]{64}$")
        preview = row["after"]["preview"]
        self.assertTrue(preview["omitted"])
        self.assertIsNone(preview["sha256"])
        self.assertFalse(preview["hash_complete"])
        self.assertNotIn(huge_value, json.dumps(result))
        self.assertLess(len(json.dumps(result)), 20000)

    def test_same_receipt_is_labeled_as_one_observation_without_temporal_claims(self):
        receipt_id = self.save(fixture())
        with no_bridge(), patch.object(self.store, "get", wraps=self.store.get) as get:
            result = compare_diagnostic_receipts(self.store, receipt_id, receipt_id)["result"]
        self.assertEqual(get.call_args_list, [call(receipt_id, include_image=False), call(receipt_id, include_image=False)])
        self.assertTrue(result["same_receipt"])
        self.assertTrue(result["scope"]["ordering_not_verified"])
        self.assertFalse(result["scope"]["independent_observations_verified"])
        self.assertEqual(result["difference_count"], 0)

    def test_version_mismatch_in_historical_envelopes_prevents_comparison(self):
        before_id, after_id = self.save(fixture()), self.save(fixture())
        before = self.store.get(before_id)
        after = self.store.get(after_id)
        after["result"]["diagnosis"]["version"] = "E3D31"
        change_context(after["result"]["diagnosis"], "version", "E3D31")
        with no_bridge(), patch.object(self.store, "get", side_effect=[before, after]):
            result = compare_diagnostic_receipts(self.store, before_id, after_id)["result"]
        self.assert_not_comparable(result)

    def test_lookup_is_metadata_only_and_never_returns_png_payload(self):
        before, after = full_receipt(True), full_receipt(True)
        result = self.compare(before, after)
        encoded = json.dumps(result)
        self.assertNotIn(before.data, encoded)
        self.assertNotIn("image_base64", encoded)
        self.assertEqual(self.section(result, "image")["path"], "/result/image")

    def test_comparison_and_result_mutation_do_not_modify_stored_evidence(self):
        before, after = fixture(), fixture()
        after["result"]["target"]["name"] = "/NEW-NAME"
        frozen = copy.deepcopy((before, after))
        before_id, after_id = self.save(before), self.save(after)
        with no_bridge():
            result = compare_diagnostic_receipts(self.store, before_id, after_id)["result"]
            result["before"]["summary"]["unknown"].append({"code": "caller-mutated"})
            result["after"]["summary"]["target"]["name"] = "/CALLER-MUTATED"
            found_before = self.store.get(before_id)["result"]["diagnosis"]
            found_after = self.store.get(after_id)["result"]["diagnosis"]
        self.assertEqual((before, after), frozen)
        self.assertEqual((found_before, found_after), frozen)

    def test_unknown_expired_and_cross_instance_ids_never_trigger_new_observations(self):
        before_id, after_id = self.save(fixture()), self.save(fixture())
        other = DiagnosticReceiptStore("E3D21", clock=self.clock, utc_clock=self.clock.utc)
        for store, left, right in ((self.store, FIRST_ID, after_id), (other, before_id, after_id)):
            with self.subTest(cross_instance=store is other), no_bridge(), patch.object(store, "get", wraps=store.get) as get:
                result = compare_diagnostic_receipts(store, left, right)["result"]
                self.assertEqual(get.call_count, 2)
                self.assertEqual(result["status"], "receipt_unavailable")
                self.assertFalse(result["compatibility"]["comparable"])
                self.assertIsNone(result["difference_count"])
        self.clock.seconds = 1899.0
        with no_bridge():
            self.assertEqual(compare_diagnostic_receipts(self.store, before_id, after_id)["result"]["status"], "compared")
            self.clock.seconds = 1900.0
            expired = compare_diagnostic_receipts(self.store, before_id, after_id)["result"]
        self.assertEqual(expired["status"], "receipt_unavailable")
        self.assertFalse(expired["before"]["available"])
        self.assertFalse(expired["after"]["available"])

    def test_invalid_core_arguments_are_rejected_before_any_store_lookup(self):
        store = Mock(spec=DiagnosticReceiptStore)
        invalid_ids = (None, True, 12, "", "diag_" + "A" * 32, FIRST_ID + "\n", " " + FIRST_ID,
                       "diag_" + "a" * 31, "diag_" + "g" * 32)
        with no_bridge():
            for invalid in invalid_ids:
                for side in ("before", "after"):
                    with self.subTest(side=side, invalid=invalid), self.assertRaises(ValueError):
                        compare_diagnostic_receipts(store, invalid if side == "before" else FIRST_ID,
                                                    invalid if side == "after" else SECOND_ID)
            for limit in (None, True, False, 0, -1, 201, 1.0, "100", [], {}):
                with self.subTest(limit=limit), self.assertRaises(ValueError):
                    compare_diagnostic_receipts(store, FIRST_ID, SECOND_ID, difference_limit=limit)
        store.get.assert_not_called()


class DiagnosticComparisonToolTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=False)
        self.server = tool_module.create_server(self.settings)

    def rpc(self, method, params, server=None):
        output = StringIO()
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        with patch("sys.stdout", output):
            (server or self.server).serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def tool(self, name, arguments, server=None):
        return self.rpc("tools/call", {"name": name, "arguments": arguments}, server)

    def retain(self, original):
        with no_bridge(), patch.object(tool_module, "diagnose_element", return_value=original) as diagnose:
            response = self.tool("e3d_diagnose_element", {"refno": REFNO, "retain_receipt": True, "evidence_detail": "compact"})
        diagnose.assert_called_once()
        info = response["structuredContent"]["result"]["diagnostic_receipt"]
        self.assertEqual(info["status"], "stored")
        return info["receipt_id"]

    def test_catalog_exposes_strict_read_only_comparison_arguments(self):
        catalog = {item["name"]: item for item in self.rpc("tools/list", {})["tools"]}
        definition = catalog["e3d_compare_diagnostic_receipts"]
        schema = definition["inputSchema"]
        self.assertEqual(set(schema["properties"]), {"before_receipt_id", "after_receipt_id", "difference_limit"})
        self.assertEqual(set(schema["required"]), {"before_receipt_id", "after_receipt_id"})
        self.assertFalse(schema["additionalProperties"])
        for key in ("before_receipt_id", "after_receipt_id"):
            self.assertEqual(schema["properties"][key]["type"], "string")
            self.assertEqual(schema["properties"][key]["pattern"], "^diag_[0-9a-f]{32}$")
        limit = schema["properties"]["difference_limit"]
        self.assertEqual((limit["type"], limit["minimum"], limit["maximum"], limit["default"]), ("integer", 1, 200, 100))
        self.assertTrue(definition["annotations"]["readOnlyHint"])
        self.assertFalse(definition["annotations"]["destructiveHint"])

    def test_two_compact_retained_results_compare_the_original_full_evidence_without_native_calls(self):
        before, after = full_receipt(True), full_receipt(True)
        after.metadata["result"]["evidence"]["geometry"]["result"]["dimensions"]["DIAM"] = 150.0
        before_id, after_id = self.retain(before), self.retain(after)
        original_get = DiagnosticReceiptStore.get
        with no_bridge(), patch.object(tool_module, "diagnose_element", side_effect=AssertionError("Must not replay diagnosis")) as diagnose, \
                patch.object(DiagnosticReceiptStore, "get", autospec=True, side_effect=original_get) as get:
            response = self.tool("e3d_compare_diagnostic_receipts", {"before_receipt_id": before_id, "after_receipt_id": after_id})
        diagnose.assert_not_called()
        self.assertEqual(get.call_count, 2)
        self.assertEqual([row.kwargs for row in get.call_args_list], [{"include_image": False}, {"include_image": False}])
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertTrue(result["compatibility"]["comparable"])
        self.assertEqual(result["difference_count"], 1)
        self.assertEqual(result["differences"][0]["path"], "/result/evidence/geometry/result/dimensions/DIAM")
        self.assertFalse(any(part["type"] == "image" for part in response["content"]))
        self.assertNotIn(before.data, json.dumps(response))

    def test_invalid_rpc_arguments_never_read_store_or_replay_diagnosis(self):
        valid = {"before_receipt_id": FIRST_ID, "after_receipt_id": SECOND_ID}
        invalid = [{}, {"before_receipt_id": FIRST_ID}, {"after_receipt_id": SECOND_ID}, {**valid, "extra": True}]
        for key in ("before_receipt_id", "after_receipt_id"):
            invalid.extend({**valid, key: value} for value in (None, True, 1, "", FIRST_ID + "\n", "diag_" + "A" * 32))
        invalid.extend({**valid, "difference_limit": value} for value in (None, True, False, 0, -1, 201, 1.0, "100", [], {}))
        with no_bridge(), patch.object(DiagnosticReceiptStore, "get") as get, patch.object(tool_module, "diagnose_element") as diagnose:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self.tool("e3d_compare_diagnostic_receipts", arguments).get("isError"))
        get.assert_not_called()
        diagnose.assert_not_called()

    def test_failed_diagnosis_and_other_server_receipts_are_data_not_protocol_failures(self):
        before, after = fixture(), fixture()
        after["result"].update(success=False, workflow_completed=False, errors=[{"code": "native_fault"}])
        before_id, after_id = self.retain(before), self.retain(after)
        arguments = {"before_receipt_id": before_id, "after_receipt_id": after_id}
        other_server = tool_module.create_server(self.settings)
        with no_bridge(), patch.object(tool_module, "diagnose_element", side_effect=AssertionError("No diagnosis")):
            response = self.tool("e3d_compare_diagnostic_receipts", arguments)
            cross_server = self.tool("e3d_compare_diagnostic_receipts", arguments, other_server)
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"]["result"]["status"], "not_comparable")
        self.assertEqual(response["structuredContent"]["result"]["after"]["summary"]["errors"], [{"code": "native_fault"}])
        self.assertFalse(cross_server.get("isError", False), cross_server)
        self.assertEqual(cross_server["structuredContent"]["result"]["status"], "receipt_unavailable")


if __name__ == "__main__":
    unittest.main()
