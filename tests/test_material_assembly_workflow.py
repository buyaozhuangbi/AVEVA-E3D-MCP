"""Bounded independent observations for mixed parameter/material assemblies."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeError
from aveva_e3d_mcp.material_assembly_workflow import normalize_material_targets, read_material_assembly_sources
from aveva_e3d_mcp.triangle_mesh_validation import validate_triangle_mesh
from test_material_assembly_drawing import (
    digest, geometry_digest, material_context, material_native, material_source, parameter_source,
)
from test_primitive_csg_mesh import cut


MODULE = "aveva_e3d_mcp.material_assembly_workflow"
_DEFAULT_TARGETS = object()


def envelope(result):
    return {"ok": True, "version": "E3D21", "result": deepcopy(result)}


def csg_callback_fixture(refno="=1/11", *, empty=False):
    """An engine-independent standard receipt for a mathematically disjoint cut.

    This tests the observation protocol, not execution of the Boolean engine.
    For an empty fixture the BOX cutter entirely contains the parent instead.
    """
    positive = parameter_source(refno)
    cutter = cut("BOX", {"XLEN": 200, "YLEN": 200, "ZLEN": 100}) if empty else cut(
        "BOX", {"XLEN": 2, "YLEN": 2, "ZLEN": 2}, position=(1000, 0, 0))
    native = material_native(refno, cutters=[cutter])
    mesh = deepcopy(positive["derived_mesh"])
    if empty:
        mesh.update(vertices=[], triangles=[], triangle_normals=[], counts={"vertices": 0, "triangles": 0})
        validation = {"success": True, "available": True, "complete": True, "valid": True, "empty": True,
            "components": [], "findings": [], "checks": {"closed_manifold": True, "winding_consistent": True,
                "orientation_outward": None, "component_count": 0},
            "counts": {"vertices": 0, "triangles": 0, "valid_triangles": 0},
            "scope": "empty result of successful manifold difference; no boundary shell"}
    else:
        validation = validate_triangle_mesh(mesh["vertices"], mesh["triangles"])
    mesh.update(empty=empty, coordinate_space="WORLD", coordinate_unit="mm", cuts_applied=True,
        csg_evaluated=True, material_boundary_validated=True, native_cut_application_verified=False,
        native_rendered_geometry_verified=False, source_geometry=deepcopy(native),
        geometry_sha256=geometry_digest(mesh), validation=validation,
        engine={"name": "manifold3d", "version": "callback-fixture", "mesh_api": "Mesh64",
                "numeric_precision": "float64", "operation": "Subtract", "status": "NoError", "empty": empty},
        material_boundary_validation={"validated": True, "closed_manifold": True, "winding_consistent": True,
            "provenance": "manifold3d Mesh64 subtraction of validated positive closed operands",
            "engine_status": "NoError", "empty": empty,
            "allowed_findings": ["component_winding_is_inward"], "cavity_shells": []})
    result = material_source(native, mesh, "owner_member_difference")
    result.update(empty=empty, cuts_applied=True, csg_evaluated=True, material_boundary_validated=True)
    return result


def target(refno, mode="parameter_mesh", expected_type="BOX"):
    return {"refno": refno, "expected_type": expected_type, "geometry_mode": mode}


class MaterialAssemblyWorkflowTests(unittest.TestCase):
    maxDiff = 1500

    def setUp(self):
        self.sources = {
            "=1/10": parameter_source(),
            "=1/11": csg_callback_fixture(),
            "=1/12": parameter_source("=1/12", position=(200, 0, 0)),
        }
        self.targets = [target("/MATERIAL-10"), target("=1/11", "owner_member_difference"), target("=1/12")]
        self.events = []
        self.observe = Mock(side_effect=self.context_read)
        self.parameter = Mock(side_effect=lambda item, segments: self.source_read("parameter", item, segments))
        self.csg = Mock(side_effect=lambda item, segments: self.source_read("csg", item, segments))

    def context_read(self):
        self.events.append("context")
        return material_context()

    def source_read(self, reader, item, segments):
        self.events.append((reader, deepcopy(item), segments))
        requested = item["refno"]
        if requested.startswith("="):
            numeric = "=" + "/".join(str(int(part)) for part in requested[1:].split("/"))
        else:
            numeric = next(ref for ref, source in self.sources.items()
                if source["source_geometry"]["element"]["name"].casefold() == requested.casefold())
        return envelope(self.sources[numeric])

    def read(self, targets=_DEFAULT_TARGETS, **options):
        return read_material_assembly_sources(self.targets if targets is _DEFAULT_TARGETS else targets,
            radial_segments=options.pop("radial_segments", 24),
            context_reader=options.pop("context_reader", self.observe),
            parameter_mesh_reader=options.pop("parameter_mesh_reader", self.parameter),
            csg_mesh_reader=options.pop("csg_mesh_reader", self.csg), **options)

    def assert_failed(self, result, code=None):
        for flag in ("success", "available", "complete"):
            self.assertFalse(result[flag], result.get("error_code"))
        self.assertEqual(result["mesh_sources"], [])
        self.assertFalse(result["atomic_snapshot"])
        self.assertTrue(result["error_code"])
        if code is not None:
            self.assertEqual(result["error_code"], code)

    def test_parameter_double_reads_and_csg_single_callback_keep_original_source_evidence(self):
        original = deepcopy(self.sources)
        result = self.read()
        self.assertTrue(result["success"], result)
        self.assertTrue(result["available"])
        self.assertTrue(result["complete"])
        self.assertFalse(result["atomic_snapshot"])
        self.assertEqual([source["geometry_mode"] for source in result["mesh_sources"]],
            ["parameter_mesh", "owner_member_difference", "parameter_mesh"])
        self.assertEqual(self.parameter.call_count, 4)
        self.assertEqual(self.csg.call_count, 1)
        parameter_requests = [call.args[0]["refno"] for call in self.parameter.call_args_list]
        self.assertEqual(parameter_requests, ["/MATERIAL-10", "=1/10", "=1/12", "=1/12"])
        for callback in (self.parameter, self.csg):
            self.assertTrue(all(call.args[1] == 24 for call in callback.call_args_list))
        self.assertEqual(self.observe.call_count, 8)
        self.assertTrue(result["context"]["boundary_agreement"])
        self.assertEqual([row["read_status"] for row in result["sources"]], ["complete"] * 3)
        self.assertEqual(result["observed_counts"]["vertices"], 24)
        self.assertEqual(result["observed_counts"]["triangles"], 36)
        self.assertEqual(result["mesh_sources"][1]["source_evidence"], self.sources["=1/11"]["source_evidence"])
        first = result["sources"][0]
        self.assertEqual(len(first["raw_receipts"]), 2)
        self.assertEqual([row["source_geometry_sha256"] for row in first["read_sequence"]],
                         [digest(self.sources["=1/10"]["source_geometry"])] * 2)
        self.assertEqual([row["geometry_sha256"] for row in first["read_sequence"]],
                         [geometry_digest(self.sources["=1/10"]["derived_mesh"])] * 2)
        result["mesh_sources"][0]["derived_mesh"]["vertices"][0][0] += 500
        result["sources"][1]["source_evidence"]["context"]["before"]["session_id"] = "result-only"
        self.assertEqual(self.sources, original)

    def test_normalization_is_strict_without_mutating_targets_or_reading_any_source(self):
        targets = [target(" /MATERIAL-10 "), target("=0001/0011", "owner_member_difference")]
        before = deepcopy(targets)
        normalized = normalize_material_targets(targets)
        self.assertEqual(normalized[0]["refno"], "/MATERIAL-10")
        self.assertEqual(targets, before)
        invalid = [None, [], "=1/10", [target("=1/10")] * 129, [{}], [target("CE")], [target("/")],
            [target("/bad\nname")], [target("=x/10")], [target("=1/10", "unknown")],
            [target("=1/10", "owner_member_difference", "PANE")],
            [{**target("=1/10"), "extra": True}], [target("=1/10"), target("=0001/0010")],
            [target("/MATERIAL-10"), target("/material-10")]]
        for items in invalid:
            with self.subTest(targets_type=type(items).__name__):
                with self.assertRaises(ValueError):
                    normalize_material_targets(items)
                self.assert_failed(self.read(items), "material_assembly_invalid_targets")
        self.observe.assert_not_called()
        self.parameter.assert_not_called()
        self.csg.assert_not_called()

    def test_invalid_radial_segments_fail_before_context_and_callbacks(self):
        for segments in (11, 100, True, "24", None, 24.5, float("nan")):
            with self.subTest(segments=segments):
                self.assert_failed(self.read(radial_segments=segments), "material_assembly_invalid_radial_segments")
        self.observe.assert_not_called()
        self.parameter.assert_not_called()
        self.csg.assert_not_called()

    def test_initial_context_fault_leaves_every_target_not_read(self):
        for fault in ("unavailable", "session", "mdb", "exception"):
            changed = material_context()
            if fault == "unavailable": changed["available"] = False
            elif fault == "session": changed["session_id"] = ""
            elif fault == "mdb": changed["project"]["mdb"] = ""
            reader = Mock(side_effect=RuntimeError("context failed")) if fault == "exception" else Mock(return_value=changed)
            with self.subTest(fault=fault):
                result = self.read(context_reader=reader)
                self.assert_failed(result, "material_assembly_context_unavailable")
                self.assertEqual([row["read_status"] for row in result["sources"]], ["not_read"] * 3)
        self.parameter.assert_not_called()
        self.csg.assert_not_called()

    def test_parameter_native_source_and_mesh_drift_are_compared_independently(self):
        for fault in ("native", "mesh", "revision"):
            changed = deepcopy(self.sources["=1/10"])
            if fault == "native": changed["source_geometry"]["dimensions"]["XLEN"] += 1
            elif fault == "mesh": changed["derived_mesh"]["vertices"][0][0] += 1
            else: changed["source_geometry"]["revision"] = "b" * 64
            reader = Mock(side_effect=[envelope(self.sources["=1/10"]), envelope(changed)])
            with self.subTest(fault=fault):
                result = self.read(parameter_mesh_reader=reader)
                self.assert_failed(result)
                self.assertIn(result["error_code"], {"material_assembly_source_changed", "material_assembly_source_geometry_hash_mismatch"})
                self.assertEqual(reader.call_count, 2)
                self.assertEqual([row["read_status"] for row in result["sources"]], ["failed", "not_read", "not_read"])
                self.assertEqual(len(result["sources"][0]["raw_receipts"]), 2)
        self.csg.assert_not_called()

    def test_opaque_receipt_metadata_may_change_when_native_and_geometry_are_identical(self):
        first, second = envelope(self.sources["=1/10"]), envelope(self.sources["=1/10"])
        first["transport_note"], second["transport_note"] = "first", "second"
        reader = Mock(side_effect=[first, second])
        result = self.read(self.targets[:1], parameter_mesh_reader=reader)
        self.assertTrue(result["success"], result)
        sequence = result["sources"][0]["read_sequence"]
        self.assertNotEqual(sequence[0]["receipt_sha256"], sequence[1]["receipt_sha256"])
        self.assertEqual(sequence[0]["source_geometry_sha256"], sequence[1]["source_geometry_sha256"])
        self.assertEqual(sequence[0]["geometry_sha256"], sequence[1]["geometry_sha256"])

    def test_identity_type_version_and_native_claim_mismatches_stop_later_sources(self):
        for fault in ("identity", "native_identity", "name", "type", "version", "incomplete", "space", "frame", "arrays", "cut", "native"):
            response = envelope(self.sources["=1/10"])
            source, mesh = response["result"], response["result"]["derived_mesh"]
            if fault == "identity": source["resolved_refno"] = "=1/99"
            elif fault == "native_identity": source["source_geometry"]["element"]["refno"] = "=1/99"
            elif fault == "name": source["source_geometry"]["element"]["name"] = "/ANOTHER"
            elif fault == "type": source["resolved_type"] = "CYLI"
            elif fault == "version": response["version"] = "E3D30"
            elif fault == "incomplete": source["complete"] = False
            elif fault == "space": source["coordinate_space"] = "local"
            elif fault == "frame": mesh["coordinate_frame"] = "WORLD assumed"
            elif fault == "arrays": mesh["vertices"] = None
            elif fault == "cut": mesh["cuts_applied"] = True
            else: mesh["native_tessellation"] = True
            reader = Mock(return_value=response)
            with self.subTest(fault=fault):
                result = self.read(parameter_mesh_reader=reader)
                self.assert_failed(result)
                self.assertEqual([row["read_status"] for row in result["sources"]], ["failed", "not_read", "not_read"])
                self.assertEqual(result["sources"][0]["raw_receipts"][0], response)
        self.csg.assert_not_called()

    def test_missing_or_forged_csg_read_evidence_is_refused_without_replaying_callback(self):
        for fault in ("reads", "floating_read_count", "agreement", "digest", "source_copy", "geometry_hash", "context", "native"):
            source = deepcopy(self.sources["=1/11"])
            evidence = source["source_evidence"]
            if fault == "reads": evidence["native_geometry_reads"] = 1
            elif fault == "floating_read_count": evidence["native_geometry_reads"] = 2.0
            elif fault == "agreement": evidence["native_reads_agree"] = False
            elif fault == "digest": evidence["native_read_after_sha256"] = "0" * 64
            elif fault == "source_copy": source["derived_mesh"]["source_geometry"]["revision"] = "b" * 64
            elif fault == "geometry_hash": source["derived_mesh"]["geometry_sha256"] = "0" * 64
            elif fault == "context": evidence["context"]["boundary_agreement"] = False
            else: source["derived_mesh"]["native_cut_application_verified"] = True
            callback = Mock(return_value=envelope(source))
            with self.subTest(fault=fault):
                result = self.read([self.targets[1], self.targets[2]], csg_mesh_reader=callback)
                self.assert_failed(result)
                callback.assert_called_once()
                self.assertEqual([row["read_status"] for row in result["sources"]], ["failed", "not_read"])
        self.parameter.assert_not_called()

    def test_internally_stable_csg_context_must_match_operation_session_project_and_mdb(self):
        for field in ("session_id", "version", "project", "mdb"):
            source = deepcopy(self.sources["=1/11"])
            different = material_context()
            if field in {"session_id", "version"}: different[field] = "OTHER"
            else: different["project"]["name" if field == "project" else "mdb"] = "OTHER"
            source["source_evidence"]["context"].update(before=deepcopy(different), after=deepcopy(different))
            callback = Mock(return_value=envelope(source))
            with self.subTest(field=field):
                result = self.read([self.targets[1]], csg_mesh_reader=callback)
                self.assert_failed(result)
                callback.assert_called_once()

    def test_external_context_drift_stops_before_next_source_callback(self):
        changed = material_context()
        changed["session_id"] = "material-session-B"
        observe = Mock(side_effect=[material_context(), material_context(), material_context(), changed])
        result = self.read(context_reader=observe)
        self.assert_failed(result, "material_assembly_context_changed")
        self.assertEqual(self.parameter.call_count, 2)
        self.csg.assert_not_called()
        self.assertEqual([row["read_status"] for row in result["sources"]], ["complete", "failed", "not_read"])

    def test_final_context_drift_discards_all_mesh_sources_but_keeps_completed_receipts(self):
        for field in ("session_id", "version", "project", "mdb", "unavailable"):
            changed = material_context()
            if field in {"session_id", "version"}: changed[field] = "OTHER"
            elif field == "unavailable": changed["available"] = False
            else: changed["project"]["name" if field == "project" else "mdb"] = "OTHER"
            observe = Mock(side_effect=[material_context() for _ in range(7)] + [changed])
            with self.subTest(field=field):
                result = self.read(context_reader=observe)
                self.assert_failed(result)
                self.assertEqual([row["read_status"] for row in result["sources"]], ["complete"] * 3)
                self.assertFalse(result["context"]["boundary_agreement"])

    def test_resolved_alias_duplicate_aborts_before_later_targets(self):
        targets = [target("/MATERIAL-10"), target("=1/10"), target("=1/12")]
        result = self.read(targets)
        self.assert_failed(result, "material_assembly_duplicate_source")
        self.assertEqual([row["read_status"] for row in result["sources"]], ["complete", "failed", "not_read"])
        self.assertTrue(all(call.args[0]["refno"] != "=1/12" for call in self.parameter.call_args_list))
        self.csg.assert_not_called()

    def test_cumulative_vertex_and_triangle_budgets_stop_with_unread_later_targets(self):
        for budget, limit in (("MAX_VERTICES", 15), ("MAX_TRIANGLES", 23)):
            with self.subTest(budget=budget), patch(f"{MODULE}.{budget}", limit):
                self.parameter.reset_mock()
                self.csg.reset_mock()
                result = self.read()
                self.assert_failed(result, "material_assembly_mesh_limit_exceeded")
                self.assertEqual([row["read_status"] for row in result["sources"]], ["complete", "failed", "not_read"])
                self.assertEqual(self.parameter.call_count, 2)
                self.csg.assert_called_once()
        with patch(f"{MODULE}.MAX_VERTICES", 16), patch(f"{MODULE}.MAX_TRIANGLES", 24):
            self.assertTrue(self.read(self.targets[:2])["success"])

    def test_raw_receipt_budget_stops_before_parameter_recheck_and_retains_bounded_diagnostics(self):
        response = envelope(self.sources["=1/10"])
        response["opaque_padding"] = "x" * 4096
        callback = Mock(return_value=response)
        with patch(f"{MODULE}.MAX_RAW_RECEIPT_BYTES", 2048):
            result = self.read(parameter_mesh_reader=callback)
        self.assert_failed(result, "material_assembly_raw_receipt_limit_exceeded")
        callback.assert_called_once()
        self.csg.assert_not_called()
        self.assertEqual([row["read_status"] for row in result["sources"]], ["failed", "not_read", "not_read"])
        self.assertNotIn("x" * 4096, json.dumps(result))

    def test_mcp_wrappers_are_decoded_once_and_raw_budget_counts_decoded_receipts(self):
        response = envelope(self.sources["=1/10"])
        wrapped = {"content": [{"type": "text", "text": json.dumps(response)}], "structuredContent": response}
        callback = Mock(return_value=wrapped)
        result = self.read(self.targets[:1], parameter_mesh_reader=callback)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["sources"][0]["raw_receipts"], [response, response])
        receipt_bytes = len(json.dumps(response, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        self.assertEqual(result["observed_counts"]["raw_receipt_bytes"], receipt_bytes * 2)
        with patch(f"{MODULE}.MAX_RAW_RECEIPT_BYTES", receipt_bytes * 2):
            result = self.read(self.targets[:1], parameter_mesh_reader=Mock(return_value=wrapped))
        self.assertTrue(result["success"], result.get("error_code"))

    def test_non_json_failure_receipt_keeps_upstream_error_and_omits_invalid_numbers(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            response = {"ok": False, "version": "E3D21", "result": {
                "success": False, "error_code": "native_invalid_volume", "value_mm3": value}}
            callback = Mock(return_value=response)
            with self.subTest(value=str(value)):
                result = self.read(parameter_mesh_reader=callback)
                self.assert_failed(result, "material_assembly_raw_receipt_not_serializable")
                callback.assert_called_once()
                self.assertEqual(result["sources"][0]["upstream_error_code"], "native_invalid_volume")
                self.assertTrue(result["sources"][0]["raw_receipts"][0]["raw_receipt_omitted"])
                self.assertEqual([row["read_status"] for row in result["sources"]], ["failed", "not_read", "not_read"])
                json.dumps(result, allow_nan=False)
        self.csg.assert_not_called()

    def test_reader_receives_private_target_copies_and_cannot_retarget_the_operation(self):
        requested = deepcopy(self.targets[:1])
        original = deepcopy(requested)
        def mutate_private_target(item, segments):
            receipt = self.source_read("parameter", item, segments)
            item.update(refno="CE", geometry_mode="unverified-native-mode")
            return receipt
        reader = Mock(side_effect=mutate_private_target)
        result = self.read(requested, parameter_mesh_reader=reader)
        self.assertTrue(result["success"], result.get("error_code"))
        self.assertEqual(requested, original)
        self.assertEqual(result["requested_targets"], original)

    def test_second_callback_cannot_mutate_the_retained_first_receipt(self):
        first = envelope(self.sources["=1/10"])
        second = deepcopy(first)
        original = deepcopy(first)
        calls = 0
        def mutate_earlier_receipt(_item, _segments):
            nonlocal calls
            calls += 1
            if calls == 1:
                return first
            first["result"]["derived_mesh"]["vertices"][0][0] += 500
            first["result"]["source_geometry"]["dimensions"]["XLEN"] += 50
            return second
        result = self.read(self.targets[:1], parameter_mesh_reader=mutate_earlier_receipt)
        self.assertTrue(result["success"], result.get("error_code"))
        self.assertEqual(result["sources"][0]["raw_receipts"][0], original)
        self.assertEqual(result["mesh_sources"][0]["derived_mesh"], original["result"]["derived_mesh"])
        self.assertEqual(result["mesh_sources"][0]["source_geometry"], original["result"]["source_geometry"])

    def test_native_failure_and_transport_exception_preserve_diagnostics_without_retry(self):
        failure = {"ok": False, "version": "E3D21", "error_code": "native_fault",
                   "diagnostics": {"raw": [False, None, "unavailable"]}}
        for response in (failure, BridgeError("read failed", diagnostics={"native_fault": {"code": 538}})):
            callback = Mock(side_effect=response) if isinstance(response, Exception) else Mock(return_value=response)
            with self.subTest(response_type=type(response).__name__):
                result = self.read(parameter_mesh_reader=callback)
                self.assert_failed(result, "material_assembly_source_read_failed" if isinstance(response, Exception)
                                   else "material_assembly_source_unavailable_or_incomplete")
                callback.assert_called_once()
                self.assertEqual([row["read_status"] for row in result["sources"]], ["failed", "not_read", "not_read"])
                if isinstance(response, dict): self.assertEqual(result["sources"][0]["raw_receipts"], [failure])
                else: self.assertIn("538", json.dumps(result))
        self.csg.assert_not_called()

    def test_failed_derived_mesh_errors_survive_summary_without_becoming_native_error_codes(self):
        reason = "unsupported_or_unreported_justification"
        for label, upstream_code, derived_code, errors in (
                ("actual_failure_shape", None, None, [reason]),
                ("separate_error_origins", "observed_native_error", "derived_mesh_unavailable", [reason]),
                ("bounded_diagnostics", None, "d" * 300, ["e" * 300] * 24)):
            response = {"ok": False, "version": "E3D21", "result": {
                "success": False, "available": False, "complete": False,
                "requested_refno": "/MATERIAL-10", "expected_type": "PANE",
                "resolved_refno": "=1/10", "resolved_type": "PANE", "coordinate_space": "WORLD",
                "source_geometry": {"success": True, "complete": True, "read_errors": {},
                    "element": {"refno": "=1/10", "type": "PANEL", "name": "/MATERIAL-10"},
                    "native_justification": "UNKNOWN"},
                "derived_mesh": {"success": False, "available": False, "complete": False,
                    "errors": errors, "vertices": [], "triangles": []}}}
            if upstream_code is not None:
                response["result"]["error_code"] = upstream_code
            if derived_code is not None:
                response["result"]["derived_mesh"]["error_code"] = derived_code
            callback = Mock(return_value=response)
            with self.subTest(case=label):
                targets = [target("/MATERIAL-10", expected_type="PANE"), *self.targets[1:]]
                result = self.read(targets, parameter_mesh_reader=callback)
                self.assert_failed(result, "material_assembly_source_unavailable_or_incomplete")
                callback.assert_called_once()
                self.assertEqual([row["read_status"] for row in result["sources"]], ["failed", "not_read", "not_read"])
                row = result["sources"][0]
                details = row["diagnostics"]["derived_mesh_diagnostics"]
                self.assertEqual(details["source"], "derived_mesh")
                self.assertEqual(details["errors"], [error[:256] for error in errors[:16]])
                self.assertEqual(details.get("error_code"), derived_code[:256] if derived_code else None)
                self.assertEqual(row.get("upstream_error_code"), upstream_code)
                self.assertEqual(row["diagnostics"].get("upstream_error_code"), upstream_code)
                self.assertEqual(result["diagnostics"]["derived_mesh_diagnostics"], details)
                self.assertEqual(row["raw_receipts"], [response])
                summary = {key: value for key, value in row.items() if key != "raw_receipts"}
                encoded = json.dumps(summary, allow_nan=False)
                self.assertNotIn('"raw_receipts"', encoded)
                if label == "bounded_diagnostics":
                    self.assertTrue(details["truncated"])
                    self.assertLess(len(json.dumps(details)), 5000)
                else:
                    self.assertIn(reason, encoded)
                    self.assertNotIn("truncated", details)
        self.csg.assert_not_called()

    def test_failed_parameter_recheck_keeps_both_receipts_and_stops_without_partial_assembly(self):
        failure = {"ok": False, "version": "E3D21", "error_code": "second_read_failed"}
        callback = Mock(side_effect=[envelope(self.sources["=1/10"]), failure])
        result = self.read(parameter_mesh_reader=callback)
        self.assert_failed(result, "material_assembly_source_unavailable_or_incomplete")
        self.assertEqual(callback.call_count, 2)
        self.assertEqual(len(result["sources"][0]["raw_receipts"]), 2)
        self.assertEqual(result["sources"][0]["raw_receipts"][1], failure)
        self.csg.assert_not_called()

    def test_csg_empty_source_is_complete_and_counts_zero_geometry(self):
        callback = Mock(return_value=envelope(csg_callback_fixture(empty=True)))
        result = self.read([self.targets[1], self.targets[2]], csg_mesh_reader=callback)
        self.assertTrue(result["success"], result)
        self.assertTrue(result["mesh_sources"][0]["derived_mesh"]["empty"])
        self.assertEqual(result["observed_counts"]["vertices"], 8)
        self.assertEqual(result["observed_counts"]["triangles"], 12)
        self.assertEqual([row["read_status"] for row in result["sources"]], ["complete", "complete"])
        callback.assert_called_once()

    def test_128_distinct_parameter_sources_fit_the_geometry_and_receipt_budgets(self):
        self.sources = {f"=1/{100 + index}": parameter_source(f"=1/{100 + index}",
            dimensions={"XLEN": 1, "YLEN": 1, "ZLEN": 1}, position=(index * 3, 0, 0)) for index in range(128)}
        result = self.read([target(refno) for refno in self.sources])
        self.assertTrue(result["success"], result)
        self.assertEqual(len(result["mesh_sources"]), 128)
        self.assertEqual(self.parameter.call_count, 256)
        self.assertEqual(result["observed_counts"]["vertices"], 1024)
        self.assertEqual(result["observed_counts"]["triangles"], 1536)
        self.csg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
