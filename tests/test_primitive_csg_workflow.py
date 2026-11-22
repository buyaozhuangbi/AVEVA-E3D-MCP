"""Native observation ordering and failure evidence for parameter Boolean reads."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aveva_e3d_mcp.bridge import BridgeError
from aveva_e3d_mcp.primitive_csg_workflow import (
    PrimitiveCsgReadError, compare_primitive_csg_volumes, read_primitive_csg_source,
)
from test_primitive_csg_mesh import csg_native


def context():
    return {"available": True, "session_id": "session-A", "version": "E3D21",
            "project": {"name": "APS", "mdb": "ALL", "connected": True, "recovery_only": False}}


def envelope(native):
    return {"ok": True, "version": "E3D21", "result": deepcopy(native)}


def native_volume():
    return {"success": True, "scope": "element", "native_qualifier": "ONLY", "unit": "mm3",
            "element": {"refno": "=1/10", "type": "BOX"}, "ce_unchanged": True,
            "volume_pair_applicable": True, "volume_pair_coherent": True,
            "gross": {"available": True, "value_mm3": 320000.0, "unit": "mm3", "error": None},
            "net": {"available": True, "value_mm3": 300000.0, "unit": "mm3", "error": None},
            "opaque_native_evidence": [False, None, "not inferred"]}


class PrimitiveCsgWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.native = csg_native()
        self.bridge = Mock()
        self.bridge.primitive_geometry.side_effect = lambda ref: envelope(self.native)
        self.bridge.geometry_volume.return_value = envelope(native_volume())
        self.observe = Mock(side_effect=lambda: context())

    def read(self, refno="=1/10", include_native_volume=False):
        return read_primitive_csg_source(self.bridge, refno,
            include_native_volume=include_native_volume, context_reader=self.observe)

    def failed(self, code, **options):
        with self.assertRaises(PrimitiveCsgReadError) as caught:
            self.read(**options)
        self.assertEqual(caught.exception.code, code, caught.exception.evidence)
        self.bridge.command.assert_not_called()
        self.bridge.cancel_command.assert_not_called()
        self.bridge.capture_view.assert_not_called()
        return caught.exception.evidence

    def test_two_complete_observations_are_copied_and_do_not_imply_atomicity(self):
        original = deepcopy(self.native)
        native, evidence = self.read("/CSG-PARENT")
        self.assertEqual(native, original)
        self.assertEqual(self.bridge.primitive_geometry.call_args_list[0].args, ("/CSG-PARENT",))
        self.assertEqual(self.bridge.primitive_geometry.call_args_list[1].args, ("=1/10",))
        self.assertEqual(evidence["native_geometry_reads"], 2)
        self.assertTrue(evidence["native_reads_agree"])
        self.assertTrue(evidence["context"]["boundary_agreement"])
        self.assertFalse(evidence["atomic_snapshot"])
        self.assertFalse(evidence["native_cut_application_verified"])
        self.assertEqual(evidence["native_read_before_sha256"], evidence["native_read_after_sha256"])
        native["negative_children"].clear()
        self.assertEqual(self.native, original)
        self.bridge.geometry_volume.assert_not_called()

    def test_native_volume_is_bracketed_by_source_reads_and_retained_unchanged(self):
        order = []
        self.observe.side_effect = lambda: (order.append("context"), context())[1]
        self.bridge.primitive_geometry.side_effect = lambda ref: (order.append("geometry"), envelope(self.native))[1]
        self.bridge.geometry_volume.side_effect = lambda ref, **kw: (order.append("volume"), envelope(native_volume()))[1]
        _, evidence = self.read(include_native_volume=True)
        self.assertEqual(order, ["context", "geometry", "volume", "geometry", "context"])
        self.bridge.geometry_volume.assert_called_once_with("=1/10", scope="element")
        self.assertEqual(evidence["native_volume_observation"], envelope(native_volume()))

    def test_incomplete_context_stops_before_database_reads(self):
        for key in ("available", "session_id", "version", "project"):
            with self.subTest(key=key):
                observed = context()
                del observed[key]
                self.observe.side_effect = None
                self.observe.return_value = observed
                self.failed("primitive_csg_context_unavailable")
        self.bridge.primitive_geometry.assert_not_called()

    def test_changed_identity_incomplete_and_envelope_failures_stop_without_replay(self):
        cases = [("primitive_csg_native_read_failed", {"ok": False}),
                 ("primitive_csg_native_read_failed", {**envelope(self.native), "version": "E3D30"})]
        for key, value, code in (("complete", False, "primitive_csg_native_geometry_incomplete"),
                                 ("read_errors", {"DIM": "no read"}, "primitive_csg_native_geometry_incomplete"),
                                 ("negative", True, "primitive_csg_positive_parent_required")):
            cases.append((code, envelope({**self.native, key: value})))
        changed = deepcopy(self.native)
        changed["element"]["refno"] = "=1/11"
        cases.append(("primitive_csg_element_reference_mismatch", envelope(changed)))
        for code, response in cases:
            with self.subTest(code=code):
                self.bridge.primitive_geometry.reset_mock()
                self.bridge.primitive_geometry.side_effect = None
                self.bridge.primitive_geometry.return_value = response
                evidence = self.failed(code)
                self.assertEqual(self.bridge.primitive_geometry.call_count, 1)
                self.assertEqual(evidence["native_observations"]["before"], response)

    def test_resolved_names_and_numeric_aliases_require_matching_identity(self):
        self.read("=0001/0010")
        self.read("/csg-parent")
        self.failed("primitive_csg_element_name_mismatch", refno="/OTHER")

    def test_changed_source_and_session_preserve_both_native_observations(self):
        changed = deepcopy(self.native)
        changed["dimensions"]["XLEN"] += 1
        self.bridge.primitive_geometry.side_effect = [envelope(self.native), envelope(changed)]
        evidence = self.failed("primitive_csg_source_changed")
        self.assertFalse(evidence["native_reads_agree"])
        self.assertEqual(evidence["native_observations"]["after"], envelope(changed))
        for key in ("session_id", "version", "project"):
            with self.subTest(key=key):
                self.bridge.primitive_geometry.side_effect = lambda ref: envelope(self.native)
                observed = context()
                observed[key] = "changed" if key != "project" else {**observed[key], "mdb": "OTHER"}
                self.observe.side_effect = [context(), observed]
                self.failed("primitive_csg_context_changed")

    def test_volume_failures_preserve_receipt_and_do_not_retry_or_continue(self):
        variants = []
        for key, value in (("success", False), ("unit", "cm3"), ("scope", "tree"),
                           ("native_qualifier", "ALL"), ("ce_unchanged", False),
                           ("volume_pair_applicable", False), ("volume_pair_coherent", False)):
            variants.append(({**native_volume(), key: value}, "primitive_csg_native_volume_unavailable"))
        variants.append(({**native_volume(), "element": {"refno": "=1/11", "type": "BOX"}},
                         "primitive_csg_native_volume_identity_mismatch"))
        for value in (True, None, -1, float("nan"), float("inf"), 10**400):
            modified = native_volume()
            modified["net"]["value_mm3"] = value
            variants.append((modified, "primitive_csg_native_volume_unavailable"))
        for native, code in variants:
            with self.subTest(code=code, net_type=type(native["net"]["value_mm3"]).__name__):
                self.bridge.primitive_geometry.reset_mock()
                response = envelope(native)
                self.bridge.geometry_volume.return_value = response
                evidence = self.failed(code, include_native_volume=True)
                self.assertEqual(self.bridge.primitive_geometry.call_count, 1)
                self.assertEqual(evidence["native_volume_observation"], response)

    def test_read_transport_exception_retains_diagnostics_without_automatic_recovery(self):
        self.bridge.primitive_geometry.side_effect = BridgeError("connection closed", transport_error=True)
        evidence = self.failed("primitive_csg_read_failed")
        self.assertEqual(evidence["read_error"]["error_type"], "BridgeError")
        self.assertEqual(self.bridge.primitive_geometry.call_count, 1)

    def test_recheck_failure_retains_both_receipts_and_is_never_replayed(self):
        failure = {"ok": False, "version": "E3D21", "error": "native read unavailable"}
        self.bridge.primitive_geometry.side_effect = [envelope(self.native), failure]
        evidence = self.failed("primitive_csg_native_recheck_failed")
        self.assertEqual(evidence["native_geometry_reads"], 2)
        self.assertEqual(evidence["native_observations"]["after"], failure)
        self.assertEqual(self.bridge.primitive_geometry.call_count, 2)

    def test_native_cylinder_volume_long_type_is_accepted_and_incoherent_mass_is_rejected(self):
        self.native["element"]["type"] = "CYLI"
        volume = native_volume()
        volume["element"]["type"] = "CYLINDER"
        self.bridge.geometry_volume.return_value = envelope(volume)
        _, evidence = self.read(include_native_volume=True)
        self.assertEqual(evidence["resolved_type"], "CYLI")
        volume["net"]["value_mm3"] = volume["gross"]["value_mm3"] + 100
        self.bridge.geometry_volume.return_value = envelope(volume)
        self.failed("primitive_csg_native_volume_incoherent", include_native_volume=True)

    def test_volume_comparison_records_disagreement_without_verifying_shape(self):
        evidence = {"native_volume_observation": envelope(native_volume())}
        result = compare_primitive_csg_volumes(
            {"volume": {"gross_polyhedral_mm3": 320000, "net_mm3": 299000}}, evidence)
        self.assertTrue(result["available"])
        self.assertEqual(result["net"]["derived_minus_native_mm3"], -1000)
        self.assertAlmostEqual(result["net"]["relative_delta"], -1000/300000)
        self.assertFalse(result["native_cut_application_verified"])
        self.assertFalse(result["shape_equivalence_verified"])
        for value in (True, float("inf"), 10**400):
            self.assertFalse(compare_primitive_csg_volumes(
                {"volume": {"gross_polyhedral_mm3": value, "net_mm3": 1}}, evidence)["available"])


if __name__ == "__main__":
    unittest.main()
