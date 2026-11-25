"""Pure receipt projections and the diagnostic tool's evidence-detail boundary.

Fixtures are independent synthetic observations and never read historical
artifacts or contact E3D. A compact receipt must preserve all unique evidence,
the original PNG and every uncertain/failing diagnostic observation.
"""
from __future__ import annotations

import base64
import copy
import json
import struct
import sys
import unittest
import zlib
from contextlib import ExitStack
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.diagnosis_receipt import compact_diagnosis
from aveva_e3d_mcp.protocol import ImageToolResult
from aveva_e3d_mcp import tools as tool_module


REFNO = "=2000000001/2633"
OBSERVATION_FIELDS = (
    "session_build_version_unchanged", "project_mdb_user_unchanged", "current_element_unchanged",
    "camera_and_top_level_drawlist_samples_unchanged", "selection_samples_unchanged", "observed_window_unchanged",
)


def envelope(result):
    return {"ok": True, "version": "E3D21", "result": result}


def status():
    return {"ok": True, "connected": True, "version": "E3D21", "session_id": "receipt-session",
        "bridge_build": "receipt-build", "state": {"database_ready": True, "busy": False,
            "recovery_required": False, "native_database_fault": {"has_fault": False},
            "execution": {"native_operation_active": False, "dispatcher_ready": True, "dispatch_count": 100},
            "native_dialogs": {"complete": True, "standard_dialog_present": False, "dialogs": [], "elapsed_ms": 0.1},
            "pml_alerts": {"complete": True, "alert_present": False, "alerts": [], "elapsed_ms": 0.2}}}


def project():
    return {"ok": True, "version": "E3D21", "project": {"connected": True, "recovery_only": False,
        "name": "APS", "mdb": "ALL", "user": "SYSTEM", "current_element": REFNO}}


def view():
    return envelope({"success": True, "available": True, "partial": False, "errors": {},
        "view_id": 11, "drawlist_id": 17, "centre": [12.0, 24.0, 36.0], "direction": [1.0, 1.0, -1.0],
        "radius": 300.0, "bearing_degrees": 45.0, "elevation_degrees": -35.26438968,
        "extra_twist_degrees": 0.0, "perspective": False, "shaded": True, "width": 1600, "height": 1200,
        "drawlist_member_count": 2, "drawlist_membership_fingerprint": "a" * 64,
        "drawlist_visuals_fingerprint": "b" * 64, "current_element_refno": REFNO})


def clipping():
    return envelope({"success": True, "available": True, "complete": True, "status": "complete",
        "source": "E3D SDK View3D.Clipping public getters", "view_id": 11,
        "clipping_enabled": False, "capping": False, "cap_colour": 0,
        "box_data": {"available": True, "lengths": [100.0, 200.0, 300.0],
            "position": [0.0, 0.0, 0.0], "orientation": [0.0, 0.0, 0.0],
            "source": "View3DClipping.GetClipBoxData", "interpretation": "SDK stored clip-box data"},
        "box_faces": [{"face": face, "defined": False, "active": False} for face in ("W", "S", "D", "E", "N", "U")],
        "read_errors": {}, "coordinate_frame": "SDK native (unverified)", "units": "SDK native (unverified)",
        "view_unchanged": True, "side_effects": "No setters or refresh requested"})


def image_data():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\x00" + bytes(64 * 3)) * 64)) + chunk(b"IEND", b""))
    return base64.b64encode(png).decode("ascii")


def full_receipt(include_image=False, include_clipping=False):
    target = {"refno": REFNO, "type": "CYLI", "name": "/FROZEN-RECEIPT-PART"}
    stages = ["initial", "baseline_view", "baseline_selection", "snapshot", "schema", "bounds", "geometry", "display"]
    if include_image:
        stages += ["pre_capture_view", "capture"]
    stages += ["final_view", "final_selection", "final"]
    selection = envelope({"success": True, "available": True, "complete": True, "read_errors": [],
        "ce_unchanged": True, "context_unchanged": True, "selection_count": 0, "elements": [],
        "view_id": 11, "drawlist_id": 17, "ce_before": REFNO, "ce_after": REFNO})
    evidence = {
        "gates": [{"stage": stage, "status": status(), "project": project()} for stage in stages],
        "initial_status": status(), "initial_project": project(), "final_status": status(), "final_project": project(),
        "baseline_view": view(), "final_view": view(),
        "baseline_selection": copy.deepcopy(selection), "final_selection": copy.deepcopy(selection),
        "snapshot": {"ok": True, "version": "E3D21", "element": {**target, "members": [],
            "snapshot_info": {"complete": True, "truncated": False, "read_errors": []}}},
        "schema": envelope({"success": True, "element": copy.deepcopy(target), "read_errors": {},
            "attributes": [{"requested_name": "DIAM", "supported": True}], "attribute_page": {"has_more": False}}),
        "bounds": envelope({"success": True, "available": True, "partial": False, **target,
            "min": [-60.0, -60.0, -75.0], "max": [60.0, 60.0, 75.0],
            "coordinate_frame": "SDK native (unverified)", "errors": {}, "native_attribute_errors": {}}),
        "geometry": envelope({"success": True, "complete": True, "element": copy.deepcopy(target),
            "dimensions": {"DIAM": 120.0, "HEIG": 150.0}, "read_errors": {},
            "negative_children": [{"element": {"refno": "=2000000001/2634", "type": "NCYL"},
                                   "dimensions": {"DIAM": 60.0, "HEIG": 120.0}}]}),
        "display": envelope({"success": True, "complete": True, "available": True, "element": copy.deepcopy(target),
            "sdk_exists": False, "exact_member": False, "primitive_scan_complete": True, "primitive_matches": [],
            "read_errors": [], "ce_before": REFNO, "ce_after": REFNO, "ce_unchanged": True,
            "pixel_visibility_verified": False}),
        "future_native_evidence": {"unique_value": ["must", "survive", {"all": "projection"}]},
    }
    observations = {key: True for key in OBSERVATION_FIELDS}
    observations["capture"] = {"status": "not_requested", "consistent": None, "complete": None}
    result = {"schema": 1, "success": True, "workflow_completed": True, "complete": False, "available": True,
        "requested_refno": REFNO, "target": target, "evidence": evidence, "observations": observations,
        "unknown": [{"stage": "bounds", "code": "bounds_coordinate_frame_unverified", "message": "Frame remains unverified"}],
        "errors": [], "stopped_stage": None, "read_only": True, "model_writes": 0, "view_setters": 0,
        "restarts": 0, "retries": 0, "cancellations": 0, "capture_requested": include_image,
        "capture_attempted": include_image, "source": "bridge", "scope": {"pixel_visibility_verified": False},
        "future_result_field": {"must": ["remain", "identical"]}}
    if include_clipping:
        result["clipping_requested"] = True
        evidence["baseline_clipping"] = clipping()
        evidence["final_clipping"] = clipping()
        observations["clipping_samples_unchanged"] = True
    if include_image:
        evidence["pre_capture_view"] = view()
        native = {"schema": 1, "complete": True, "view_object_unchanged": True, "read_errors": {},
            "before_refresh": view()["result"], "before_bitmap": view()["result"], "after_bitmap": view()["result"],
            "bitmap_started_utc": "2026-11-17T08:00:00Z", "bitmap_finished_utc": "2026-11-17T08:00:01Z",
            "scope": "Existing native bitmap samples"}
        observation = {"schema": 1, "status": "consistent", "consistent": True, "complete": True,
            "changed_fields": [], "read_errors": {}, "scope": {"pixel_visibility_verified": False},
            "before": envelope({"success": True, "complete": True, "stable": True, "read_errors": {},
                "states": [view(), view(), view()], "context_before": status(), "context_after": status(),
                "changed_fields": []}),
            "after": view(), "context_after": status(), "native": copy.deepcopy(native)}
        evidence["capture"] = envelope({"success": True, "mime_type": "image/png", "width": 64, "height": 64,
            "source": "E3D View3D.GetBitmap", "view_id": 11, "drawlist_id": 17,
            "observation": observation, "native_capture_observation": native,
            "image_base64_omitted": True, "image_base64_characters": len(image_data())})
        observations["capture"] = {"status": "consistent", "consistent": True, "complete": True}
        result["image"] = {"mime_type": "image/png", "width": 64, "height": 64, "source": "frozen image metadata"}
    metadata = envelope(result)
    return ImageToolResult(image_data(), "image/png", metadata) if include_image else metadata


def metadata(value):
    return value.metadata if isinstance(value, ImageToolResult) else value


def without_compaction(value):
    result = copy.deepcopy(value)
    result["result"].pop("evidence_compaction", None)
    return result


def status_samples(result):
    evidence = result["evidence"]
    samples = [evidence["initial_status"], evidence["final_status"]]
    samples += [row["status"] for row in evidence["gates"]]
    if "capture" in evidence:
        observation = evidence["capture"]["result"]["observation"]
        before = observation["before"]["result"]
        samples += [before["context_before"], before["context_after"], observation["context_after"]]
    return samples


def camera_samples(result):
    evidence = result["evidence"]
    samples = [evidence[key]["result"] for key in ("baseline_view", "pre_capture_view", "final_view")]
    captured = evidence["capture"]["result"]
    samples += [row["result"] for row in captured["observation"]["before"]["result"]["states"]]
    samples += [captured["observation"]["after"]["result"]]
    for native in (captured["native_capture_observation"], captured["observation"]["native"]):
        samples += [native[key] for key in ("before_refresh", "before_bitmap", "after_bitmap")]
    return samples


class DiagnosisReceiptTests(unittest.TestCase):
    def guarded_compact(self, value):
        before = copy.deepcopy(value)
        with ExitStack() as stack:
            for name in ("_request", "health", "project", "view_state", "capture_view", "graphical_selection",
                         "element", "element_schema", "geometry_bounds", "primitive_geometry", "panel_geometry", "display_state",
                         "view_clipping_state"):
                stack.enter_context(patch.object(BridgeClient, name, side_effect=AssertionError("Projection must not read " + name)))
            projected = compact_diagnosis(value)
        self.assertEqual(value, before, "Projection mutated caller-owned evidence")
        if isinstance(value, ImageToolResult):
            self.assertIsInstance(projected, ImageToolResult)
            self.assertEqual((projected.data, projected.mime_type), (value.data, value.mime_type))
        else:
            self.assertIsInstance(projected, dict)
        return projected

    def assert_fallback(self, value, reason=None):
        projected = self.guarded_compact(value)
        before, after = metadata(value), metadata(projected)
        self.assertEqual(without_compaction(after), before)
        mark = after["result"]["evidence_compaction"]
        self.assertEqual(mark["requested"], "compact")
        self.assertIs(mark["applied"], False)
        self.assertTrue(isinstance(mark["reason"], str) and mark["reason"])
        if reason is not None:
            self.assertEqual(mark["reason"], reason)
        return projected

    def test_verified_receipt_only_removes_declared_duplicate_samples(self):
        original = full_receipt(True)
        projected = self.guarded_compact(original)
        before, after = original.metadata, projected.metadata
        mark = after["result"]["evidence_compaction"]
        self.assertEqual((mark["requested"], mark["applied"], mark["reason"]), ("compact", True, "verified_unchanged"))
        expected = copy.deepcopy(before)
        gates = expected["result"]["evidence"]["gates"]
        summary = after["result"]["evidence"]["gates"]
        self.assertEqual(summary, {"stage_order": [row["stage"] for row in gates],
                                   "count": len(gates), "details_omitted": True})
        expected["result"]["evidence"]["gates"] = summary
        captured = expected["result"]["evidence"]["capture"]["result"]
        for key in ("before", "after", "context_after", "native"):
            captured["observation"].pop(key)
        for key in ("before_refresh", "before_bitmap", "after_bitmap"):
            captured["native_capture_observation"].pop(key)
        expected["result"]["evidence_compaction"] = mark
        self.assertEqual(after, expected, "Projection changed evidence outside its declared omissions")
        self.assertTrue(mark["omitted_paths"])
        self.assertTrue(all(isinstance(path, str) and path for path in mark["omitted_paths"]))
        self.assertTrue(mark["sample_counts"])
        self.assertTrue(all(isinstance(count, int) and count >= 0 for count in mark["sample_counts"].values()))
        self.assertEqual(mark["sample_counts"], {"gate_stages": 13, "gate_status_samples": 13,
            "gate_project_samples": 13, "capture_pre_view_samples": 3, "capture_post_view_samples": 1,
            "capture_native_view_samples": 3, "capture_context_samples": 3, "duplicate_native_view_samples": 3})
        self.assertIn("new", mark["detail"].lower())
        self.assertIn("observation", mark["detail"].lower())
        self.assertLess(len(json.dumps(after)), len(json.dumps(before)))

    def test_no_capture_does_not_claim_an_image_was_verified(self):
        original = full_receipt(False)
        projected = self.guarded_compact(original)
        mark = projected["result"]["evidence_compaction"]
        self.assertTrue(mark["applied"])
        self.assertEqual(projected["result"]["observations"]["capture"],
                         {"status": "not_requested", "consistent": None, "complete": None})
        self.assertFalse(projected["result"]["capture_requested"])
        self.assertFalse(projected["result"]["capture_attempted"])
        self.assertNotIn("capture", projected["result"]["evidence"])
        self.assertNotIn("image", projected["result"])
        self.assertEqual(mark["sample_counts"], {"gate_stages": 11, "gate_status_samples": 11,
            "gate_project_samples": 11, "capture_pre_view_samples": 0, "capture_post_view_samples": 0,
            "capture_native_view_samples": 0, "capture_context_samples": 0, "duplicate_native_view_samples": 0})
        expected = copy.deepcopy(original)
        expected["result"]["evidence"]["gates"] = projected["result"]["evidence"]["gates"]
        self.assertEqual(without_compaction(projected), expected)

    def test_requested_clipping_is_verified_and_both_raw_samples_are_retained(self):
        for include_image in (False, True):
            with self.subTest(include_image=include_image):
                original = full_receipt(include_image, include_clipping=True)
                original_result = metadata(original)["result"]
                for key in ("baseline_clipping", "final_clipping"):
                    native = original_result["evidence"][key]["result"]
                    native["source"] = "E3D SDK View3D.Clipping public getters"
                    native["side_effects"] = (
                        "May initialize or warm the current view's managed clipping cache; no SDK setter, "
                        "view refresh, model write, camera change or Drawlist change is requested.")
                    native["box_data"]["source"] = "View3DClipping.GetClipBoxData"
                    native["box_data"]["interpretation"] = (
                        "SDK stored clip-box data; it is not verified to describe planes defined individually.")
                projected = self.guarded_compact(original)
                result = metadata(projected)["result"]
                self.assertTrue(result["evidence_compaction"]["applied"])
                self.assertIs(result["clipping_requested"], True)
                self.assertIs(result["observations"]["clipping_samples_unchanged"], True)
                for key in ("baseline_clipping", "final_clipping"):
                    self.assertEqual(result["evidence"][key], original_result["evidence"][key])
                self.assertFalse(any("clipping" in path for path in result["evidence_compaction"]["omitted_paths"]))

    def test_equal_unknown_clipping_fields_force_full_evidence_at_every_supported_level(self):
        for include_image in (False, True):
            for location in ("envelope", "result", "box_data", "face"):
                with self.subTest(include_image=include_image, location=location):
                    original = full_receipt(include_image, include_clipping=True)
                    evidence = metadata(original)["result"]["evidence"]
                    for key in ("baseline_clipping", "final_clipping"):
                        envelope = evidence[key]
                        native = envelope["result"]
                        target = {"envelope": envelope, "result": native,
                                  "box_data": native["box_data"], "face": native["box_faces"][0]}[location]
                        target["future_native_field"] = {"retained": [1, True, None]}
                    # Equal raw values and forged true summaries do not validate an unknown field.
                    projected = self.assert_fallback(original, "clipping_unverified")
                    for key in ("baseline_clipping", "final_clipping"):
                        self.assertEqual(metadata(projected)["result"]["evidence"][key], evidence[key])

    def test_unrequested_clipping_keeps_legacy_compaction_without_new_observation(self):
        original = full_receipt()
        original["result"]["clipping_requested"] = False
        projected = self.guarded_compact(original)
        self.assertTrue(projected["result"]["evidence_compaction"]["applied"])
        self.assertNotIn("clipping_samples_unchanged", projected["result"]["observations"])
        self.assertNotIn("baseline_clipping", projected["result"]["evidence"])
        self.assertNotIn("final_clipping", projected["result"]["evidence"])

    def test_clipping_observation_summary_cannot_override_missing_partial_or_changed_evidence(self):
        mutations = (
            lambda result: result["evidence"].pop("baseline_clipping"),
            lambda result: result["evidence"].pop("final_clipping"),
            lambda result: result["evidence"]["final_clipping"]["result"].update(clipping_enabled=True),
            lambda result: result["evidence"]["final_clipping"]["result"].update(complete=False, status="partial"),
            lambda result: result["evidence"]["final_clipping"]["result"].update(view_id=22),
            lambda result: result["evidence"]["final_clipping"]["result"].update(view_unchanged=False),
            lambda result: result["evidence"]["final_clipping"]["result"]["read_errors"].update(box_data="failed"),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                original = full_receipt(True, include_clipping=True)
                mutate(original.metadata["result"])
                # Deliberately leave both summary observations forged true.
                self.assert_fallback(original)

    def test_equal_but_invalid_native_clipping_contract_cannot_be_compacted(self):
        mutations = (
            lambda row: row.update(version="E3D31"),
            lambda row: row["result"].update(available=False),
            lambda row: row["result"].update(view_id=99),
            lambda row: row["result"].update(clipping_enabled=0),
            lambda row: row["result"].update(cap_colour=True),
            lambda row: row["result"]["box_data"].update(available=False),
            lambda row: row["result"]["box_data"].update(position=[0.0, 0.0]),
            lambda row: row["result"]["box_faces"].pop(),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                original = full_receipt(include_clipping=True)
                for key in ("baseline_clipping", "final_clipping"):
                    mutate(original["result"]["evidence"][key])
                self.assert_fallback(original, "clipping_unverified")

    def test_requested_clipping_requires_verified_observation_and_explicit_request_flag(self):
        for observation in (False, None, "missing"):
            with self.subTest(observation=observation):
                original = full_receipt(include_clipping=True)
                if observation == "missing":
                    original["result"]["observations"].pop("clipping_samples_unchanged")
                else:
                    original["result"]["observations"]["clipping_samples_unchanged"] = observation
                self.assert_fallback(original)
        for flag in (False, None, 1, "true", "missing"):
            with self.subTest(flag=flag):
                original = full_receipt(include_clipping=True)
                if flag == "missing": original["result"].pop("clipping_requested")
                else: original["result"]["clipping_requested"] = flag
                self.assert_fallback(original, "clipping_unverified")

    def test_future_clipping_fields_are_compared_strictly_before_any_omission(self):
        for before, after in (("original", "changed"), (1, True), (None, "missing")):
            with self.subTest(before=before, after=after):
                original = full_receipt(True, include_clipping=True)
                evidence = original.metadata["result"]["evidence"]
                evidence["baseline_clipping"]["result"]["future_field"] = before
                if after != "missing": evidence["final_clipping"]["result"]["future_field"] = after
                self.assert_fallback(original)

    def test_non_observation_unknowns_and_complete_false_are_preserved_and_allow_compaction(self):
        original = full_receipt()
        original["result"]["unknown"].append({"stage": "geometry", "code": "geometry_incomplete", "details": [1, 2, 3]})
        original["result"]["evidence"]["geometry"]["result"]["complete"] = False
        projected = self.guarded_compact(original)
        self.assertTrue(projected["result"]["evidence_compaction"]["applied"])
        self.assertEqual(projected["result"]["unknown"], original["result"]["unknown"])
        self.assertIs(projected["result"]["complete"], False)

    def test_failed_workflow_and_reported_errors_fall_back_without_losing_any_evidence(self):
        for key in ("success", "workflow_completed"):
            with self.subTest(key=key):
                value = full_receipt(True)
                value.metadata["result"][key] = False
                self.assert_fallback(value, "workflow_not_completed")
        value = full_receipt(True)
        value.metadata["result"]["errors"] = [{"stage": "capture", "code": "native_fault", "message": "retained"}]
        self.assert_fallback(value, "errors_present")

    def test_every_false_unknown_or_missing_observation_falls_back_to_full(self):
        for key in OBSERVATION_FIELDS:
            for condition in (False, None, "missing"):
                with self.subTest(key=key, condition=condition):
                    value = full_receipt(True)
                    if condition == "missing": value.metadata["result"]["observations"].pop(key)
                    else: value.metadata["result"]["observations"][key] = condition
                    self.assert_fallback(value, "observations_unverified")

    def test_capture_changed_unknown_or_incomplete_falls_back_with_identical_png(self):
        mutations = (
            lambda result: result["observations"]["capture"].update(consistent=None),
            lambda result: result["observations"]["capture"].update(status="changed", consistent=False),
            lambda result: result["observations"]["capture"].update(complete=False),
            lambda result: result["evidence"]["capture"]["result"]["observation"].update(complete=False),
            lambda result: result["evidence"]["capture"]["result"]["native_capture_observation"].update(view_object_unchanged=False),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                value = full_receipt(True)
                mutate(value.metadata["result"])
                self.assert_fallback(value)

    def test_hidden_native_fault_read_error_or_transient_context_change_cannot_be_omitted(self):
        mutations = (
            lambda result: result["evidence"]["gates"][2]["status"]["state"]["native_database_fault"].update(has_fault=True),
            lambda result: result["evidence"]["geometry"].update(native_database_fault={"has_fault": True}),
            lambda result: result["evidence"]["geometry"]["result"]["read_errors"].update(DIAM="SDK failed"),
            lambda result: result["evidence"]["gates"][2]["status"].update(session_id="temporary-session"),
            lambda result: result["evidence"]["gates"][2]["project"]["project"].update(mdb="OTHER"),
            lambda result: result["evidence"]["gates"][2]["project"]["project"].update(current_element="=1/999"),
            lambda result: result["evidence"]["capture"]["result"]["native_capture_observation"]["before_bitmap"].update(current_element_refno="=1/999"),
            lambda result: result["evidence"]["capture"]["result"]["observation"]["before"]["result"]["context_before"].update(session_id="temporary-session"),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                value = full_receipt(True)
                mutate(value.metadata["result"])
                self.assert_fallback(value)

    def test_malformed_evidence_or_capture_samples_are_never_replaced_by_a_summary(self):
        mutations = (
            lambda result: result.update(evidence=[]),
            lambda result: result["evidence"].update(gates=[]),
            lambda result: result["evidence"].update(gates={"stage": "initial"}),
            lambda result: result["evidence"]["gates"][1].pop("project"),
            lambda result: result["evidence"]["gates"][1].update(stage="initial"),
            lambda result: result["evidence"].pop("final_status"),
            lambda result: result["evidence"]["capture"].update(result=[]),
            lambda result: result["evidence"]["capture"]["result"].update(observation=None),
            lambda result: result["evidence"]["capture"]["result"]["observation"]["before"]["result"].update(states=[]),
            lambda result: result["evidence"]["capture"]["result"]["native_capture_observation"].pop("after_bitmap"),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                value = full_receipt(True)
                mutate(value.metadata["result"])
                self.assert_fallback(value)

    def test_unknown_status_project_or_camera_details_cannot_be_silently_omitted(self):
        mutations = (
            lambda result: result["evidence"]["gates"][2]["status"].update(diagnostic_error="new diagnostic"),
            lambda result: result["evidence"]["gates"][2]["status"].update(warning="unique warning"),
            lambda result: result["evidence"]["gates"][2]["status"]["state"]["native_database_fault"].update(generation=2),
            lambda result: result["evidence"]["gates"][2]["project"]["project"].update(future_field="unique metadata"),
            lambda result: result["evidence"]["capture"]["result"]["native_capture_observation"]["before_bitmap"].update(read_warning="retained warning"),
            lambda result: result["evidence"]["capture"]["result"]["native_capture_observation"]["before_bitmap"].update(drawlist_limits={"min": [0, 0, 0], "max": [9, 9, 9]}),
            lambda result: result["evidence"]["capture"]["result"]["observation"]["before"]["result"]["states"][1]["result"].update(view_limits={"min": [0, 0, 0], "max": [9, 9, 9]}),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                value = full_receipt(True)
                mutate(value.metadata["result"])
                self.assert_fallback(value)

    def test_only_known_dispatch_and_observation_elapsed_differences_can_be_omitted(self):
        value = full_receipt(True)
        evidence = value.metadata["result"]["evidence"]
        for index, row in enumerate(evidence["gates"]):
            row["status"]["state"]["execution"]["dispatch_count"] = 100 + index
            row["status"]["state"]["native_dialogs"]["elapsed_ms"] = index * 0.1
            row["status"]["state"]["pml_alerts"]["elapsed_ms"] = index * 0.2
        projected = self.guarded_compact(value)
        self.assertIs(projected.metadata["result"]["evidence_compaction"]["applied"], True)
        for key in ("initial_status", "final_status", "initial_project", "final_project"):
            self.assertEqual(projected.metadata["result"]["evidence"][key], evidence[key])

    def test_invalid_counter_and_elapsed_values_never_enter_the_ignored_field_whitelist(self):
        for container, field in (("execution", "dispatch_count"), ("native_dialogs", "elapsed_ms"), ("pml_alerts", "elapsed_ms")):
            values = ["1", True, False, -1, float("nan"), float("inf")]
            if field == "dispatch_count": values.append(1.5)
            for invalid in values:
                with self.subTest(container=container, field=field, invalid=invalid):
                    value = full_receipt(True)
                    value.metadata["result"]["evidence"]["gates"][2]["status"]["state"][container][field] = invalid
                    self.assert_fallback(value)

    def test_counter_or_elapsed_presence_changes_fall_back_but_consistent_absence_is_allowed(self):
        paths = (("execution", "dispatch_count"), ("native_dialogs", "elapsed_ms"), ("pml_alerts", "elapsed_ms"))
        for container, field in paths:
            for sample in ("initial", "middle"):
                with self.subTest(container=container, field=field, sample=sample):
                    value = full_receipt(True)
                    evidence = value.metadata["result"]["evidence"]
                    chosen = evidence["initial_status"] if sample == "initial" else evidence["gates"][2]["status"]
                    chosen["state"][container].pop(field)
                    self.assert_fallback(value)
        value = full_receipt(True)
        for sample in status_samples(value.metadata["result"]):
            for container, field in paths:
                sample["state"][container].pop(field)
        projected = self.guarded_compact(value)
        self.assertIs(projected.metadata["result"]["evidence_compaction"]["applied"], True)

    def test_unknown_numeric_and_boolean_values_are_not_equal_for_omission(self):
        for location in ("project", "camera"):
            with self.subTest(location=location):
                value = full_receipt(True)
                result = value.metadata["result"]
                evidence = result["evidence"]
                if location == "project":
                    for sample in [evidence["initial_project"], evidence["final_project"]] + [row["project"] for row in evidence["gates"]]:
                        sample["project"]["future_alias"] = {"code": 1}
                    evidence["gates"][2]["project"]["project"]["future_alias"]["code"] = True
                else:
                    for sample in camera_samples(result):
                        sample["future_geometry_metadata"] = {"quality_code": 1}
                    captured = evidence["capture"]["result"]
                    for native in (captured["native_capture_observation"], captured["observation"]["native"]):
                        native["before_bitmap"]["future_geometry_metadata"]["quality_code"] = True
                self.assert_fallback(value)

    def test_malformed_result_shapes_preserve_original_wrapper_and_content(self):
        cases = ({}, {"ok": True}, {"ok": True, "result": None}, {"ok": True, "result": []})
        for original in cases:
            for image in (False, True):
                with self.subTest(value=original, image=image):
                    value = ImageToolResult(image_data(), "image/png", copy.deepcopy(original)) if image else copy.deepcopy(original)
                    self.assertEqual(self.guarded_compact(value), value)

    def test_already_projected_receipt_is_not_compacted_a_second_time(self):
        first = self.guarded_compact(full_receipt(True))
        second = self.guarded_compact(first)
        self.assertEqual(second, first)
        self.assertEqual(second.metadata["result"]["evidence_compaction"]["omitted_paths"],
                         first.metadata["result"]["evidence_compaction"]["omitted_paths"])


class DiagnosisEvidenceDetailBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=False)
        self.server = tool_module.create_server(self.settings)

    def rpc(self, method, params):
        output = StringIO()
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        with patch("sys.stdout", output):
            self.server.serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def call(self, arguments):
        return self.rpc("tools/call", {"name": "e3d_diagnose_element", "arguments": arguments})

    def test_catalog_keeps_full_default_and_exposes_only_two_literal_detail_values(self):
        catalog = self.rpc("tools/list", {})["tools"]
        self.assertEqual(len(catalog), 124)
        definition = next(row for row in catalog if row["name"] == "e3d_diagnose_element")
        field = definition["inputSchema"]["properties"]["evidence_detail"]
        self.assertEqual(field["type"], "string")
        self.assertEqual(field["enum"], ["full", "compact"])
        self.assertEqual(field["default"], "full")
        self.assertEqual(definition["inputSchema"]["required"], ["refno"])
        self.assertFalse(definition["inputSchema"]["additionalProperties"])
        self.assertTrue(definition["annotations"]["readOnlyHint"])

    def test_default_and_explicit_full_return_the_original_wrapper_object(self):
        bridge = BridgeClient(self.settings)
        for value in (full_receipt(False), full_receipt(True)):
            with self.subTest(image=isinstance(value, ImageToolResult)), \
                    patch.object(tool_module, "diagnose_element", return_value=value) as diagnose, \
                    patch.object(tool_module, "compact_diagnosis", side_effect=AssertionError("Full must not project")):
                for extra in ({}, {"evidence_detail": "full"}):
                    returned = tool_module._diagnose_element(bridge, {"refno": REFNO, **extra})
                    self.assertIs(returned, value)
                self.assertEqual(diagnose.call_count, 2)
                diagnose.assert_called_with(bridge, REFNO, include_image=False, capture_width=1280, capture_height=960,
                                            include_clipping=False)

    def test_default_and_explicit_full_have_identical_json_rpc_bytes_and_png(self):
        for original in (full_receipt(False), full_receipt(True)):
            with self.subTest(image=isinstance(original, ImageToolResult)), \
                    patch.object(tool_module, "diagnose_element", return_value=original):
                default = self.call({"refno": REFNO})
                explicit = self.call({"refno": REFNO, "evidence_detail": "full"})
            self.assertEqual(json.dumps(default, ensure_ascii=False), json.dumps(explicit, ensure_ascii=False))
            self.assertEqual(default["structuredContent"], metadata(original))
            self.assertEqual(default["content"][0]["text"], json.dumps(metadata(original), ensure_ascii=False, indent=2))

    def test_invalid_detail_never_calls_the_diagnosis_or_any_bridge_method(self):
        with ExitStack() as stack:
            diagnose = stack.enter_context(patch.object(tool_module, "diagnose_element"))
            guards = [stack.enter_context(patch.object(BridgeClient, name)) for name in (
                "health", "project", "_request", "element", "view_state", "capture_view")]
            for value in (None, True, False, 0, 1, [], {}, "", "FULL", "Compact", " full ", "compact ", "summary"):
                with self.subTest(value=value):
                    self.assertTrue(self.call({"refno": REFNO, "evidence_detail": value}).get("isError"))
            diagnose.assert_not_called()
            for guard in guards:
                guard.assert_not_called()

    def test_compact_projects_the_one_diagnosis_without_new_bridge_calls_or_png_changes(self):
        original = full_receipt(True)
        with ExitStack() as stack:
            diagnose = stack.enter_context(patch.object(tool_module, "diagnose_element", return_value=original))
            for name in ("_request", "health", "project", "capture_view", "view_state", "element"):
                stack.enter_context(patch.object(BridgeClient, name, side_effect=AssertionError("Projection performed a read")))
            response = self.call({"refno": REFNO, "include_image": True, "evidence_detail": "compact"})
        diagnose.assert_called_once()
        self.assertTrue(response["structuredContent"]["result"]["evidence_compaction"]["applied"])
        images = [part for part in response["content"] if part["type"] == "image"]
        self.assertEqual(images, [{"type": "image", "data": original.data, "mimeType": original.mime_type}])
        self.assertEqual(original, full_receipt(True), "The live diagnosis response was mutated by projection")


if __name__ == "__main__":
    unittest.main()
