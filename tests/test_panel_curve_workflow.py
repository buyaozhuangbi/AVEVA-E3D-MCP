from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeError
from aveva_e3d_mcp.panel_curve_workflow import PanelCurveReadError, read_panel_curve_source


def context(session="one"):
    return {"available": True, "session_id": session, "version": "E3D21",
            "project": {"name": "APS", "mdb": "ALL"}}


def native(count=4):
    return {"success": True, "complete": True, "read_errors": {}, "vertex_count": count,
            "element": {"type": "PANEL", "refno": "=1/2", "name": "/PANEL"},
            "loops": [{"complete": True, "read_errors": {},
                       "vertices": [{"refno": f"=1/{100 + i}"} for i in range(count)]}],
            "boundary_points_complete": False, "boundary_points_errors": {"P4.exists": "unset"},
            "topology_valid": None, "revision": None}


def envelope(result):
    return {"ok": True, "version": "E3D21", "result": result}


def descriptors(refs, attributes):
    assert attributes == ["DESP"]
    return envelope({"success": True, "complete": True, "ce_unchanged": True,
                     "requested_refnos": refs, "attributes": attributes, "row_count": len(refs),
                     "rows": [{"success": True, "refno": ref, "requested_refno": ref, "type": "PAVE",
                               "values": {"DESP": "unset"}, "errors": {}, "typed_read_errors": {}}
                              for ref in refs]})


class PanelCurveWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.source = native()
        self.bridge = Mock()
        self.bridge.panel_geometry.return_value = envelope(self.source)
        self.bridge.read_attributes_batch.side_effect = descriptors
        self.observe = Mock(side_effect=[context(), context()])

    def read(self, **kwargs):
        return read_panel_curve_source(self.bridge, kwargs.pop("refno", "=1/2"),
                                       coordinate_space=kwargs.pop("coordinate_space", "WORLD"),
                                       context_reader=self.observe, **kwargs)

    def assert_refused(self, code):
        with self.assertRaises(PanelCurveReadError) as raised:
            self.read()
        self.assertEqual(raised.exception.code, code)
        return raised.exception.evidence

    def test_enrichment_preserves_native_unknowns_without_mutating_receipt(self):
        before = deepcopy(self.source)
        enriched, evidence = self.read()
        self.assertEqual(self.source, before)
        self.assertFalse(enriched["boundary_points_complete"])
        self.assertIsNone(enriched["topology_valid"])
        self.assertIsNone(enriched["revision"])
        self.assertTrue(evidence["native_reads_agree"])
        self.assertFalse(evidence["atomic_snapshot"])
        self.assertEqual(evidence["native_read_before_sha256"], evidence["native_read_after_sha256"])
        self.assertEqual(self.bridge.panel_geometry.call_count, 2)
        for call in self.bridge.panel_geometry.call_args_list:
            self.assertEqual(call.kwargs, {"include_boundary": True, "include_world": True})
        for vertex in enriched["loops"][0]["vertices"]:
            self.assertEqual(vertex["curve_import_descriptor"]["refno"], vertex["refno"])
            self.assertTrue(vertex["curve_import_descriptor"]["checked"])
            self.assertFalse(vertex["curve_import_descriptor"]["tagged"])
        self.bridge.command.assert_not_called()
        self.bridge.capture_view.assert_not_called()

    def test_batching_fifty_rows_and_local_frame_requests_are_bounded(self):
        self.bridge.panel_geometry.return_value = envelope(native(51))
        enriched, evidence = self.read(coordinate_space="panel_local")
        self.assertEqual(len(enriched["loops"][0]["vertices"]), 51)
        self.assertEqual([len(call.args[0]) for call in self.bridge.read_attributes_batch.call_args_list], [50, 1])
        self.assertEqual(len(evidence["descriptor_batches"]), 2)
        self.assertEqual(self.bridge.panel_geometry.call_args.kwargs,
                         {"include_boundary": True, "include_world": False})

    def test_tags_are_retained_for_explicit_core_rejection(self):
        def tagged(refs, attributes):
            response = descriptors(refs, attributes)
            response["result"]["rows"][1]["values"]["DESP"] = "2 1 0"
            return response
        self.bridge.read_attributes_batch.side_effect = tagged
        enriched, _ = self.read()
        tag = enriched["loops"][0]["vertices"][1]["curve_import_descriptor"]
        self.assertTrue(tag["checked"])
        self.assertTrue(tag["tagged"])
        self.assertEqual(tag["value"], "2 1 0")

    def test_changed_geometry_produces_no_enriched_result(self):
        changed = deepcopy(self.source)
        changed["loops"][0]["vertices"][0]["position"] = [50, 0, 0]
        self.bridge.panel_geometry.side_effect = [envelope(self.source), envelope(changed)]
        evidence = self.assert_refused("panel_curve_source_changed")
        self.assertFalse(evidence["native_reads_agree"])
        self.assertEqual(self.bridge.panel_geometry.call_count, 2)

    def test_changed_session_refuses_same_geometry(self):
        self.observe.side_effect = [context(), context("replacement")]
        evidence = self.assert_refused("panel_curve_context_changed")
        self.assertFalse(evidence["context"]["boundary_agreement"])

    def test_wrong_panel_identity_prevents_descriptor_reads(self):
        self.source["element"]["refno"] = "=1/9"
        self.assert_refused("element_reference_mismatch")
        self.bridge.read_attributes_batch.assert_not_called()

    def test_native_failure_stops_without_recheck_cancel_or_replay(self):
        self.bridge.panel_geometry.side_effect = BridgeError("native fault", diagnostics={"code": 538})
        evidence = self.assert_refused("panel_curve_read_failed")
        self.assertEqual(evidence["read_error"]["native_diagnostics"], {"code": 538})
        self.assertEqual(self.bridge.panel_geometry.call_count, 1)
        self.bridge.read_attributes_batch.assert_not_called()
        self.bridge.cancel_command.assert_not_called()
        self.assertEqual(self.observe.call_count, 1)

    def test_bad_descriptor_identity_partial_blank_and_ce_drift_stop_before_recheck(self):
        for fault in ("reference", "type", "partial", "blank", "ce", "row_order", "error", "missing"):
            with self.subTest(fault=fault):
                self.setUp()
                def corrupted(refs, attributes):
                    response = descriptors(refs, attributes)
                    batch = response["result"]
                    row = batch["rows"][0]
                    if fault == "reference": row["refno"] = "=1/3"
                    elif fault == "type": row["type"] = "BOX"
                    elif fault == "partial": batch["complete"] = False
                    elif fault == "blank": row["values"]["DESP"] = ""
                    elif fault == "ce": batch["ce_unchanged"] = False
                    elif fault == "row_order": batch["rows"].reverse()
                    elif fault == "error": row["errors"] = {"DESP": "unreadable"}
                    elif fault == "missing": del row["values"]["DESP"]
                    return response
                self.bridge.read_attributes_batch.side_effect = corrupted
                with self.assertRaises(PanelCurveReadError): self.read()
                self.assertEqual(self.bridge.panel_geometry.call_count, 1)


if __name__ == "__main__":
    unittest.main()
