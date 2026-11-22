"""Operation-wide consistency and bounded early-stop tests for curve reads."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeError
from aveva_e3d_mcp.panel_curve_assembly_workflow import read_panel_curve_assembly
from aveva_e3d_mcp.panel_curve_boundary import derive_panel_curve_boundary
from aveva_e3d_mcp.panel_curve_workflow import PanelCurveReadError
from test_panel_curve_assembly import panel_native
from test_panel_curve_workflow import context, descriptors, envelope


MODULE = "aveva_e3d_mcp.panel_curve_assembly_workflow"


def stable_evidence(observed=None):
    observed = context() if observed is None else observed
    return {"atomic_snapshot": False, "native_reads": 2, "native_reads_agree": True,
            "context": {"before": deepcopy(observed), "after": deepcopy(observed), "boundary_agreement": True},
            "descriptor_batches": [{"opaque_native_evidence": {"preserve": [False, None, "unset"]}}],
            "native_geometry": {"complete": True, "topology_valid": None, "revision": None}}


class PanelCurveAssemblyWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.natives = [panel_native(index, origin=(index * 200.0, 0.0), unknown_existence=index == 0)
                        for index in range(3)]
        self.bridge = Mock()
        self.bridge.panel_geometry.side_effect = self.geometry
        self.bridge.read_attributes_batch.side_effect = descriptors
        self.observe = Mock(side_effect=lambda: context())

    def geometry(self, refno, **options):
        self.assertEqual(options, {"include_boundary": True, "include_world": True})
        for native in self.natives:
            numeric = native["element"]["refno"]
            if refno == native["element"]["name"] or refno.startswith("=") and tuple(map(int, refno[1:].split("/"))) == tuple(map(int, numeric[1:].split("/"))):
                return envelope(deepcopy(native))
        raise AssertionError(f"Unexpected source read: {refno}")

    def read(self, targets=None, **options):
        return read_panel_curve_assembly(self.bridge,
            targets if targets is not None else [native["element"]["refno"] for native in self.natives],
            chord_tolerance_mm=options.pop("chord_tolerance_mm", 0.1),
            context_reader=options.pop("context_reader", self.observe), **options)

    def assert_failed(self, result, code):
        self.assertFalse(result["success"])
        self.assertFalse(result["available"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["error_code"], code)
        self.assertEqual(result["boundaries"], [], "A failed operation must expose no assembled geometry.")
        self.assertFalse(result["atomic_snapshot"])

    def test_world_reads_preserve_native_evidence_and_count_only_bounded_sources(self):
        before = deepcopy(self.natives)
        result = self.read()
        self.assertTrue(result["success"], result)
        self.assertTrue(result["available"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["coordinate_space"], "WORLD")
        self.assertEqual(result["coordinate_unit"], "mm")
        self.assertEqual(result["world_position_frame"], "=1/0")
        self.assertEqual(result["observed_counts"], {"panels": 3, "vertices": 12, "analytic_segments": 24,
            "sampled_edges": sum(boundary["sampled_edge_count"] for boundary in result["boundaries"])})
        self.assertEqual([source["read_status"] for source in result["sources"]], ["returned"] * 3)
        self.assertEqual([source["source_index"] for source in result["sources"]], [0, 1, 2])
        self.assertEqual(self.bridge.panel_geometry.call_count, 6)
        self.assertEqual(self.bridge.read_attributes_batch.call_count, 3)
        self.assertEqual(self.observe.call_count, 8)
        self.assertTrue(result["context"]["boundary_agreement"])
        self.assertFalse(result["atomic_snapshot"])
        for source, boundary in zip(result["sources"], result["boundaries"]):
            evidence = source["source_evidence"]
            self.assertEqual(evidence["native_reads"], 2)
            self.assertTrue(evidence["native_reads_agree"])
            self.assertEqual(evidence["native_read_before_sha256"], evidence["native_read_after_sha256"])
            self.assertIn("descriptor_batches", evidence)
            self.assertEqual(source["curve_summary"], {key: value for key, value in boundary.items() if key != "loops"})
            self.assertNotIn("loops", source["curve_summary"])
        first = result["sources"][0]
        for field in ("native_boundary_points_complete", "native_boundary_points_read_complete", "native_existence_verified"):
            self.assertIs(first[field], False)
        self.assertIs(first["source_evidence"]["native_geometry"]["topology_valid"], None)
        self.assertEqual(self.natives, before)
        self.bridge.command.assert_not_called()
        self.bridge.capture_view.assert_not_called()
        self.bridge.cancel_command.assert_not_called()

    def test_source_helper_evidence_is_copied_without_discarding_false_or_opaque_fields(self):
        native = self.natives[0]
        evidence = stable_evidence()
        original = deepcopy(evidence)
        with patch(f"{MODULE}.read_panel_curve_source", return_value=(native, evidence)) as source_reader:
            result = self.read([native["element"]["refno"]])
        self.assertTrue(result["success"], result)
        self.assertEqual(result["sources"][0]["source_evidence"], evidence)
        self.assertEqual(source_reader.call_args.kwargs["coordinate_space"], "WORLD")
        result["sources"][0]["source_evidence"]["descriptor_batches"][0]["opaque_native_evidence"]["preserve"].append("result only")
        self.assertEqual(evidence, original)

    def test_invalid_targets_and_tolerances_do_not_start_native_or_context_reads(self):
        for targets in ([], ["=1/10"] * 129, "=1/10", None, [""], ["=1/10 "], ["/"] , ["/bad\nname"], ["=x/1"], [17]):
            with self.subTest(targets=targets):
                result = read_panel_curve_assembly(self.bridge, targets, chord_tolerance_mm=0.1, context_reader=self.observe)
                self.assert_failed(result, "invalid_panel_curve_assembly_targets")
        for tolerance in (0, -0.1, True, None, "0.1", float("nan"), float("inf"), 10**10000):
            with self.subTest(tolerance_type=type(tolerance).__name__):
                self.assert_failed(self.read(chord_tolerance_mm=tolerance), "invalid_chord_tolerance_mm")
        self.observe.assert_not_called()
        self.bridge.panel_geometry.assert_not_called()

    def test_invalid_initial_context_stops_before_first_source(self):
        for fault in ("unavailable", "missing_session", "blank_version", "project", "mdb", "not_mapping", "exception"):
            with self.subTest(fault=fault):
                observed = context()
                if fault == "unavailable": observed["available"] = False
                elif fault == "missing_session": del observed["session_id"]
                elif fault == "blank_version": observed["version"] = " "
                elif fault == "project": observed["project"] = {}
                elif fault == "mdb": observed["project"]["mdb"] = ""
                elif fault == "not_mapping": observed = None
                reader = Mock(side_effect=RuntimeError("context fault")) if fault == "exception" else Mock(return_value=observed)
                result = self.read(context_reader=reader)
                self.assert_failed(result, "panel_curve_assembly_context_unavailable")
                self.assertEqual([source["read_status"] for source in result["sources"]], ["not_read"] * 3)
                self.assertEqual(reader.call_count, 1)
        self.bridge.panel_geometry.assert_not_called()

    def test_source_context_is_compared_with_operation_before_not_just_with_itself(self):
        # The second source could be internally stable in the replacement
        # session. The operation's first observation must still reject it.
        self.observe.side_effect = [context(), context(), context(), context("replacement"), context("replacement")]
        result = self.read()
        self.assert_failed(result, "panel_curve_assembly_context_changed")
        self.assertEqual([source["read_status"] for source in result["sources"]], ["returned", "failed", "not_read"])
        self.assertEqual(self.bridge.panel_geometry.call_count, 2)
        self.assertEqual(self.observe.call_count, 4)
        self.assertFalse(result["context"]["boundary_agreement"])
        self.assertEqual(result["sources"][1]["context_observations"][0]["session_id"], "replacement")

    def test_internally_stable_but_operation_mismatched_source_evidence_is_refused(self):
        native = self.natives[0]
        for fault in ("session", "version", "project", "mdb", "extra_project", "unavailable"):
            with self.subTest(fault=fault):
                observed = context()
                if fault == "session": observed["session_id"] = "replacement"
                elif fault == "version": observed["version"] = "E3D31"
                elif fault == "project": observed["project"]["name"] = "OTHER"
                elif fault == "mdb": observed["project"]["mdb"] = "DESIGN"
                elif fault == "extra_project": observed["project"]["extra"] = "changed"
                else: observed["available"] = False
                evidence = stable_evidence(observed)
                with patch(f"{MODULE}.read_panel_curve_source", return_value=(native, evidence)) as source_reader:
                    result = self.read()
                self.assert_failed(result, "panel_curve_assembly_context_changed")
                self.assertEqual(source_reader.call_count, 1)
                self.assertEqual([source["read_status"] for source in result["sources"]], ["failed", "not_read", "not_read"])
                self.assertEqual(result["sources"][0]["source_evidence"], evidence)

    def test_final_context_drift_discards_all_boundaries_but_preserves_successful_sources(self):
        for fault in ("session", "project", "mdb", "unavailable"):
            with self.subTest(fault=fault):
                self.setUp()
                changed = context()
                if fault == "session": changed["session_id"] = "replacement"
                elif fault == "project": changed["project"]["name"] = "OTHER"
                elif fault == "mdb": changed["project"]["mdb"] = "DESIGN"
                else: changed["available"] = False
                self.observe.side_effect = [context() for _ in range(7)] + [changed]
                result = self.read()
                self.assert_failed(result, "panel_curve_assembly_context_unavailable" if fault == "unavailable" else "panel_curve_assembly_context_changed")
                self.assertEqual([source["read_status"] for source in result["sources"]], ["returned"] * 3)
                self.assertEqual(self.bridge.panel_geometry.call_count, 6)
                self.assertEqual(self.observe.call_count, 8)
                self.assertFalse(result["context"]["boundary_agreement"])

    def test_context_drift_inside_source_recheck_stops_before_later_panels(self):
        self.observe.side_effect = [context(), context(), context("replacement")]
        result = self.read()
        self.assert_failed(result, "panel_curve_assembly_context_changed")
        self.assertEqual(self.bridge.panel_geometry.call_count, 2)
        self.assertEqual([source["read_status"] for source in result["sources"]], ["failed", "not_read", "not_read"])
        self.assertEqual(result["sources"][0]["source_evidence"]["native_reads"], 2)

    def test_native_fault_aborts_without_retries_and_retains_original_diagnostics(self):
        original_geometry = self.geometry
        def fault(refno, **options):
            if refno == "=1/11":
                raise BridgeError("native panel fault", diagnostics={"code": 538, "raw": [False, None]})
            return original_geometry(refno, **options)
        self.bridge.panel_geometry.side_effect = fault
        result = self.read()
        self.assert_failed(result, "panel_curve_assembly_source_failed")
        self.assertEqual([source["read_status"] for source in result["sources"]], ["returned", "failed", "not_read"])
        self.assertEqual(self.bridge.panel_geometry.call_count, 3)
        row = result["sources"][1]
        self.assertEqual(row["error_code"], "panel_curve_read_failed")
        self.assertEqual(row["source_evidence"]["read_error"]["native_diagnostics"], {"code": 538, "raw": [False, None]})
        self.assertEqual(result["observed_counts"]["panels"], 1)
        self.bridge.cancel_command.assert_not_called()
        self.bridge.command.assert_not_called()

    def test_curve_rejection_retains_full_failed_boundary_and_aborts_later_sources(self):
        self.natives[1]["loops"][0]["vertices"][0]["CTYA"] = "unsupported"
        result = self.read()
        self.assert_failed(result, "panel_curve_assembly_curve_unavailable")
        self.assertEqual([source["read_status"] for source in result["sources"]], ["returned", "failed", "not_read"])
        self.assertEqual(result["observed_counts"]["panels"], 2)
        self.assertEqual(result["observed_counts"]["vertices"], 8)
        failed = result["sources"][1]["curve_boundary"]
        self.assertFalse(failed["success"])
        self.assertEqual(failed["loops"], [])
        self.assertIn("unsupported_curve_attribute", failed["errors"])
        self.assertEqual(self.bridge.panel_geometry.call_count, 4)

    def test_resolved_numeric_aliases_and_names_cannot_read_one_panel_twice(self):
        for duplicate in ("=01/010", "/ASSEMBLY-PANEL-0"):
            with self.subTest(duplicate=duplicate):
                self.setUp()
                result = self.read(["=1/10", duplicate, "=1/12"])
                self.assert_failed(result, "duplicate_panel_curve_source")
                self.assertEqual([source["read_status"] for source in result["sources"]], ["returned", "failed", "not_read"])
                self.assertEqual(self.bridge.panel_geometry.call_count, 4)
                self.assertEqual(result["sources"][1]["resolved_refno"], "=1/10")
                self.assertEqual(result["observed_counts"]["analytic_segments"], 8)

    def test_world_frames_cannot_be_mixed_across_sources(self):
        native = self.natives[1]
        native["world_position_frame"] = "=99/0"
        for loop in native["loops"]:
            for vertex in loop["vertices"]:
                vertex["boundary_points"]["WORLD"]["frame_refno"] = "=99/0"
        result = self.read()
        self.assert_failed(result, "panel_curve_assembly_world_frame_mismatch")
        self.assertEqual(result["world_position_frame"], "=1/0")
        self.assertEqual([source["read_status"] for source in result["sources"]], ["returned", "failed", "not_read"])
        self.assertEqual(self.bridge.panel_geometry.call_count, 4)

    def test_all_three_cumulative_resource_budgets_stop_before_third_source(self):
        # Reader/core outputs are isolated here so each cumulative budget is
        # tested independently of the single-source parser's own limits.
        for field, per_source, limit in (("vertices", 1025, 2048), ("analytic_segments", 2049, 4096),
                                         ("sampled_edges", 6001, 12000)):
            with self.subTest(field=field):
                self.setUp()
                native_results = deepcopy(self.natives)
                boundaries = [derive_panel_curve_boundary(native, coordinate_space="WORLD") for native in native_results]
                if field == "vertices":
                    for native in native_results: native["vertex_count"] = per_source
                else:
                    key = "segment_count" if field == "analytic_segments" else "sampled_edge_count"
                    for boundary in boundaries: boundary[key] = per_source
                with patch(f"{MODULE}.read_panel_curve_source", side_effect=[(native, stable_evidence()) for native in native_results]) as reader, \
                     patch(f"{MODULE}.derive_panel_curve_boundary", side_effect=boundaries) as core:
                    result = self.read()
                self.assert_failed(result, "panel_curve_assembly_limit_exceeded")
                self.assertEqual(reader.call_count, 2)
                self.assertEqual(core.call_count, 1 if field == "vertices" else 2)
                self.assertEqual(result["observed_counts"][field], per_source * 2)
                self.assertGreater(result["observed_counts"][field], limit)
                self.assertEqual([source["read_status"] for source in result["sources"]], ["returned", "failed", "not_read"])

    def test_exact_resource_limits_succeed_without_reading_any_extra_source(self):
        natives = deepcopy(self.natives[:2])
        boundaries = [derive_panel_curve_boundary(native, coordinate_space="WORLD") for native in natives]
        for native, boundary in zip(natives, boundaries):
            native["vertex_count"] = 1024
            boundary.update(segment_count=2048, sampled_edge_count=6000)
        with patch(f"{MODULE}.read_panel_curve_source", side_effect=[(native, stable_evidence()) for native in natives]) as reader, \
             patch(f"{MODULE}.derive_panel_curve_boundary", side_effect=boundaries):
            result = self.read([native["element"]["refno"] for native in natives])
        self.assertTrue(result["success"], result)
        self.assertEqual(result["observed_counts"], {"panels": 2, "vertices": 2048, "analytic_segments": 4096, "sampled_edges": 12000})
        self.assertEqual(reader.call_count, 2)

    def test_source_and_derivation_exceptions_are_structured_without_partial_geometry(self):
        evidence = stable_evidence()
        with patch(f"{MODULE}.read_panel_curve_source", side_effect=PanelCurveReadError("native_specific_fault", evidence)) as reader:
            result = self.read()
        self.assert_failed(result, "panel_curve_assembly_source_failed")
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(result["sources"][0]["source_evidence"], evidence)
        self.assertEqual(result["sources"][0]["error_code"], "native_specific_fault")
        for exc in (RuntimeError("unexpected fault"), BridgeError("bridge fault", diagnostics={"code": 538})):
            with self.subTest(error_type=type(exc).__name__):
                with patch(f"{MODULE}.read_panel_curve_source", side_effect=exc) as reader:
                    result = self.read()
                self.assert_failed(result, "panel_curve_assembly_source_failed")
                self.assertEqual(reader.call_count, 1)
                self.assertEqual(result["sources"][0]["read_error"]["error_type"], type(exc).__name__)
        with patch(f"{MODULE}.derive_panel_curve_boundary", side_effect=RuntimeError("core fault")):
            result = self.read()
        self.assert_failed(result, "panel_curve_assembly_derivation_failed")
        self.assertEqual(result["sources"][0]["read_error"]["error_type"], "RuntimeError")
        self.assertEqual(result["sources"][1]["read_status"], "not_read")


if __name__ == "__main__":
    unittest.main()
