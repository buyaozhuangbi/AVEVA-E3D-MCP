from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.bridge import BridgeError
from aveva_e3d_mcp.equipment_child_schema import _SCOPE as CHILD_SCOPE
from aveva_e3d_mcp.equipment_model_plan import _parameters, equipment_model_plan, register_equipment_model_plan


def context(*, project: str = "PROJECT", mdb: str = "DESIGN", ce: str = "=1/90",
            session: str = "session-a") -> dict:
    health = {"version": "E3D21", "session_id": session, "bridge_build": "build-a"}
    return {"ok": True, "version": "E3D21", "session_id": session, "bridge_build": "build-a", "result": {
        "context_complete": True,
        "readiness": {"before": "ready", "after": "ready"},
        "session_coherence": {"status": "consistent"},
        "context": {"project": project, "mdb": mdb, "current_element": ce},
        "evidence": {"health_before": health, "health_after": deepcopy(health)},
    }}


def row(index: int, refno: str, kind: str, name: str, parent: int | None,
        depth: int, sibling: int | None) -> dict:
    return {"id": index, "refno": refno, "type": kind, "name": name,
            "parent_index": parent, "depth": depth, "sibling_index": sibling,
            "success": True, "values": {}, "typed_values": {}, "errors": {},
            "typed_read_errors": {}, "identity_errors": {}}


def manifest() -> dict:
    rows = [
        row(0, "=1/1", "EQUI", "/UNIT", None, 0, None),
        row(1, "=1/2", "SUBE", "/UNIT/FRAME", 0, 1, 1),
        row(2, "=1/3", "CYLI", "/UNIT/BODY", 1, 2, 1),
        row(3, "=1/4", "BOX", "/UNIT/BOX-SEED", 1, 2, 2),
    ]
    result = {
        "success": True, "complete": True, "requested_refno": "=1/1", "root_refno": "=1/1",
        "rows": rows, "returned_count": len(rows), "truncated": False,
        "truncation_reasons": [], "read_errors": [], "session_id": "session-a",
        "ce_before": "=1/90", "ce_after": "=1/90", "ce_unchanged": True,
        "project": "PROJECT", "project_after": "PROJECT", "mdb": "DESIGN",
        "mdb_after": "DESIGN", "identity_unchanged": True,
    }
    return {"ok": True, "version": "E3D21", "session_id": "session-a", "bridge_build": "build-a", "result": result}


def endpoint_context() -> dict:
    sample = {"project": "PROJECT", "mdb": "DESIGN", "current_element": "=1/90", "current_element_type": "EQUI"}
    return {"before": sample, "after": deepcopy(sample), "same_project_instance": True,
            "same_mdb_instance": True, "identity_unchanged": True, "ce_unchanged": True,
            "boundary_agreement": True}


def constraints() -> dict:
    return {"status": "complete", "unit_kind": "DISTANCE",
            "allowed_values": {"status": "not_defined", "defined": False, "values": [],
                               "total_count": 0, "returned_count": 0, "truncated": False},
            "allowed_ranges": {"status": "not_defined", "defined": False, "raw_boundaries": [],
                               "total_count": 0, "returned_count": 0, "truncated": False, "unit_basis": "unknown"},
            "values_or_ranges_optional": {"status": "complete", "value": False}}


def schema(refno: str, kind: str, *, child_type: str | None = None,
           attributes: list[str] = (), include_constraints: bool = False) -> dict:
    children = [] if child_type is None else [{"name": child_type, "short_name": child_type,
                                                "sdk_creatable_now": True}]
    rows = [{"name": name, "short_name": name, "requested_name": name, "known": True,
             "supported": True, "sdk_settable_now": True, "direct_edit_supported": True,
             "data_type": "DOUBLE" if name == "POS" else "VECTOR"}
            for name in attributes]
    result = {
        "success": True, "element": {"refno": refno, "type": kind},
        "element_type": {"short_name": kind, "name": kind}, "child_types": children,
        "owner_types": [], "attributes": rows, "read_errors": {},
        "project": "PROJECT", "project_after": "PROJECT", "mdb": "DESIGN", "mdb_after": "DESIGN",
        "ce_before": "=1/90", "ce_after": "=1/90", "identity_unchanged": True, "ce_unchanged": True,
        "context": endpoint_context(),
    }
    if include_constraints:
        result.update(constraints_requested=True, constraints_complete=True)
        for attribute in rows:
            attribute["constraints"] = constraints()
    return {"ok": True, "version": "E3D21", "session_id": "session-a", "bridge_build": "build-a", "result": result}


def child_schema(payload: dict, *, actual: str | None = None, creatable: bool | None = True) -> dict:
    def sdk_type(kind, source):
        return {"source": source, "valid": True, "name": kind, "short_name": kind, "errors": {}, "complete": True}

    def identity(refno, base, actual_type):
        return {"refno": refno, "name": "/EXISTING", "base_type": sdk_type(base, "DbElement.ElementType"),
            "actual_type": sdk_type(actual_type, "DbElement.GetActualType"), "errors": {}, "complete": True,
            "request_binding": {"source": "explicit_request_resolution", "requested_refno": refno,
                                "resolved_refno": refno, "observed_name": None, "verified": True}}

    def metadata(name):
        return {"name": name, "short_name": name, "known": True, "data_type": "DOUBLE", "is_array": False,
            "is_pseudo": False, "is_uda": name.startswith(":"), "unit_kind": "DISTANCE", "maximum_size": 1,
            "errors": {}, "complete": True}

    kind, target = payload["element_type"], payload["read_target_refno"]
    actual = actual or kind
    base = {"=1/2": "SUBE", "=1/3": "CYLI", "=1/4": "BOX", "=1/1": "EQUI"}
    matches = None if target is None else actual == kind
    instance = {"source": "explicit_existing_read_target", "scope": "read_target_only", "refno": target,
        "status": "unknown", "reason": "read_target_required" if target is None else "actual_type_mismatch",
        "attributes": [], "complete": False}
    if matches:
        instance.update(status="observed", reason=None, complete=True, attributes=[{**metadata(name),
            "requested_name": name, "supported": True, "sdk_settable_now": True} for name in payload["attributes"]])
    boundary = {"session_id": "session-a", "bridge_build": "build-a", "project": "PROJECT", "mdb": "DESIGN",
        "ce_refno": "=1/90", "ce_base_type": "EQUI", "ce_actual_type": "EQUI", "errors": {}}
    complete = matches is True and creatable is not None
    result = {"operation": "equipment_child_schema", "available": True, "read_only": True, "single_dispatch": True,
        "atomic_snapshot": False, "success": complete, "complete": complete,
        "session_id": "session-a", "bridge_build": "build-a",
        **{"requested_" + field: deepcopy(payload[field]) for field in ("parent_refno", "element_type", "read_target_refno", "attributes")},
        "parent": identity(payload["parent_refno"], base.get(payload["parent_refno"], "SUBE"), base.get(payload["parent_refno"], "SUBE")),
        "requested_type": sdk_type(kind, "DbElementType.GetElementType"),
        "parent_creatability": {"source": "DbElement.IsCreatable", "scope": "current_parent_requested_type",
            "parent_refno": payload["parent_refno"], "requested_element_type": kind, "sdk_creatable_now": creatable,
            "error": "unavailable" if creatable is None else None, "complete": creatable is not None},
        "type_system_attributes": {"source": "DbElementType.SystemAttributes", "scope": "requested_type_only",
            "offset": payload["offset"], "limit": payload["limit"], "total_count": 1, "returned_count": 1,
            "has_more": False, "next_offset": None, "attributes": [{**metadata("HEIG"), "sdk_index": 0}],
            "errors": {}, "complete": True, "whole_type_coverage": True,
            "native_arrays_materialized_before_output_limits": True, "native_array_inspection_limit": 4096, "inspection_limited": False},
        "read_target": None if target is None else identity(target, base.get(target, "CYLI"), actual),
        "read_target_actual_type_matches": matches, "instance_attributes": instance, "read_errors": {},
        "context": {"before": boundary, "after": deepcopy(boundary), "identity_unchanged": True, "ce_unchanged": True},
        "scope": dict(CHILD_SCOPE)}
    return {"ok": True, "version": "E3D21", "session_id": "session-a", "bridge_build": "build-a", "result": result}


class FakeBridge:
    settings = Settings(mode="bridge", version="E3D21", allow_commands=False)

    def __init__(self, *, drift: bool = False) -> None:
        self.schema_calls: list[tuple] = []
        self.child_schema_calls: list[dict] = []
        self.transform_calls: list[tuple] = []
        self.spatial_calls: list[tuple] = []
        self.drift = drift

    def _selected_version(self, version):
        return version or "E3D21"

    def _request(self, method, path, payload, *, version):
        if (method, path, version) != ("POST", "/api/v1/equipment/child-schema", "E3D21"):
            raise AssertionError("Only the offline child schema route is supported")
        self.child_schema_calls.append(deepcopy(payload))
        return self.child_schema(payload)

    def child_schema(self, payload):
        return child_schema(payload)

    def element_schema(self, refno, attributes, offset, limit, *, include_constraints=False, expected_type=""):
        self.schema_calls.append((refno, tuple(attributes), include_constraints, expected_type))
        if refno == "=1/2":
            return schema(refno, "SUBE", child_type="BOX")
        if refno == "=1/3":
            return schema(refno, "CYLI", attributes=attributes)
        if refno == "=1/4":
            return schema(refno, "BOX", attributes=attributes, include_constraints=include_constraints)
        return {"ok": True, "version": "E3D21", "result": {"success": False}}

    def transform_coordinates(self, from_wrt, to_wrt, points, vectors):
        self.transform_calls.append((from_wrt, to_wrt, points, vectors))
        result = {
            "success": True, "complete": True, "available": True, "read_errors": [],
            "from_wrt": from_wrt, "to_wrt": to_wrt,
            "from_frame": {"requested": from_wrt, "refno": "=1/0" if from_wrt == "WORLD" else
                           "=1/2" if from_wrt.startswith("/") else from_wrt,
                           "type": "WORL" if from_wrt == "WORLD" else "SUBE", "name": from_wrt},
            "to_frame": {"requested": "WORLD", "refno": "=1/0", "type": "WORL"},
            "points": [{"index": index, "input": point, "output": point, "success": True, "unit": "mm"}
                       for index, point in enumerate(points)],
            "vectors": [{"index": index, "input": vector, "output": vector, "success": True}
                        for index, vector in enumerate(vectors)],
            "ce_before": "=1/90", "ce_after": "=1/90", "ce_unchanged": True,
            "project_before": "PROJECT", "project_after": "PROJECT",
            "mdb_before": "DESIGN", "mdb_after": "DESIGN", "identity_unchanged": True,
        }
        return {"ok": True, "version": "E3D21", "session_id": "session-a", "bridge_build": "build-a", "result": result}

    def spatial_info(self, refno, wrt):
        self.spatial_calls.append((refno, wrt))
        return {"ok": True, "version": "E3D21", "session_id": "session-a", "bridge_build": "build-a", "result": {
            "success": True, "complete": True, "refno": refno, "type": "CYLI",
            "project": "PROJECT", "project_after": "PROJECT", "mdb": "DESIGN", "mdb_after": "DESIGN",
            "ce_before": "=1/90", "ce_after": "=1/90", "identity_unchanged": True, "ce_unchanged": True,
            "context": endpoint_context(), "errors": {},
            "frame": {"requested": "WORLD", "refno": "=1/0", "type": "WORL"},
            "position_complete": True, "orientation_complete": True,
            "position": {"available": True, "unit": "mm", "x": 0.0, "y": 0.0, "z": 0.0},
            "orientation": {"available": True, "x_axis": [1.0, 0.0, 0.0],
                            "y_axis": [0.0, 1.0, 0.0], "z_axis": [0.0, 0.0, 1.0]},
        }}


class EquipmentModelPlanTests(unittest.TestCase):
    settings = Settings(mode="bridge", version="E3D21", allow_commands=False)

    def setUp(self):
        no_http = patch("urllib.request.urlopen", side_effect=AssertionError("Plan tests must never use real HTTP"))
        no_http.start()
        self.addCleanup(no_http.stop)

    def test_parameters_are_bounded_and_normalized(self):
        normalized = _parameters({
            "equipment_refno": " =01/001 ",
            "steps": [{"step_id": "new-body", "operation": "create", "parent_refno": "/UNIT/FRAME",
                       "element_type": "box", "name": "/BODY", "attributes": {"heig": "10"}}],
        })
        self.assertEqual(normalized["equipment_refno"], "=1/1")
        self.assertEqual(normalized["steps"][0]["element_type"], "BOX")
        self.assertEqual(normalized["steps"][0]["attributes"], {"HEIG": "10"})
        invalid = [
            {"equipment_refno": "/UNIT", "steps": []},
            {"equipment_refno": "/UNIT", "steps": [{"step_id": "1bad", "operation": "create"}]},
            {"equipment_refno": "/UNIT", "steps": [{"step_id": "x", "operation": "transform", "target_refno": "/B"}]},
            {"equipment_refno": "/UNIT", "steps": [{"step_id": "x", "operation": "create", "parent_refno": "/P",
                                                        "element_type": "BOX", "name": "/B", "attributes": {"POS": "1"}}]},
            {"equipment_refno": "/UNIT", "steps": [{"step_id": "x", "operation": "create", "parent_refno": "/P",
                                                        "element_type": "BOX", "name": "/B", "attributes": {"name": "/OTHER"}}]},
            {"equipment_refno": "/UNIT", "steps": [{"step_id": "x", "operation": "transform", "target_refno": "/B",
                                                        "position": [0, 0, 0], "name": "/ignored"}]},
        ]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                _parameters(args)
        with self.assertRaises(ValueError):
            _parameters({"equipment_refno": "=1/1", "steps": [{"step_id": "x", "operation": "transform",
                "target_refno": "=1/3", "position": [0, 0, 0]}], "include_native_previews": 1})

    def test_root_wildcards_and_overlength_are_rejected_before_context_reads(self):
        invalid = ["/UNIT*", "/UNIT?", "/" + "X" * 256]
        with patch("aveva_e3d_mcp.equipment_model_plan.context_snapshot") as snapshot, \
             patch("aveva_e3d_mcp.equipment_model_plan.equipment_manifest") as read_manifest:
            for equipment_refno in invalid:
                with self.subTest(equipment_refno=equipment_refno), self.assertRaises(ValueError):
                    equipment_model_plan(FakeBridge(), self.settings, {
                        "equipment_refno": equipment_refno,
                        "steps": [{"step_id": "new", "operation": "create", "parent_refno": "/UNIT",
                                   "element_type": "BOX", "name": "/BODY"}],
                    })
            snapshot.assert_not_called()
            read_manifest.assert_not_called()

    def test_reference_schema_is_bounded_to_256_characters(self):
        class CaptureServer:
            tool = None

            def add_tool(self, name, description, input_schema, handler, **options):
                self.tool = {"name": name, "schema": input_schema, "read_only": options.get("read_only")}

        server = CaptureServer()
        register_equipment_model_plan(server, FakeBridge(), self.settings)
        self.assertEqual(server.tool["schema"]["properties"]["equipment_refno"]["maxLength"], 256)
        reference = server.tool["schema"]["properties"]["steps"]["items"]["properties"]["parent_refno"]
        self.assertEqual(reference["maxLength"], 256)
        self.assertFalse(server.tool["schema"]["properties"]["include_native_previews"]["default"])

    def _plan(self, bridge: FakeBridge, *, project_after: str = "PROJECT", include_native_previews: bool = False,
              preview_project: str = "PROJECT") -> dict:
        args = {"equipment_refno": "=1/1", "steps": [
            {"step_id": "create-box", "operation": "create", "parent_refno": "=1/2",
             "element_type": "BOX", "name": "/NEW", "wrt": "OWNER",
             "position": [10, 20, 30], "attributes": {"HEIG": "100"},
             "attribute_reference_refno": "=1/4"},
            {"step_id": "move-body", "operation": "transform", "target_refno": "=1/3",
             "wrt": "OWNER", "position": [2, 3, 4]},
        ]}
        if include_native_previews:
            args["include_native_previews"] = True
        after = context(project=project_after)
        contexts = [context(), after]
        if include_native_previews:
            contexts.append(context(project=preview_project))
        with patch("aveva_e3d_mcp.equipment_model_plan.context_snapshot",
                   side_effect=contexts) as context_read, \
             patch("aveva_e3d_mcp.equipment_model_plan.equipment_manifest", return_value=manifest()):
            result = equipment_model_plan(bridge, self.settings, args)
        result["test_context_read_count"] = context_read.call_count
        return result

    def test_ready_plan_is_read_only_and_has_explicit_write_steps(self):
        bridge = FakeBridge()
        result = self._plan(bridge)
        self.assertTrue(result["success"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["read_coverage_complete"])
        self.assertEqual(result["write_viability"], "candidate_for_native_preview")
        self.assertTrue(result["plan_only"])
        self.assertFalse(result["writes_attempted"])
        self.assertEqual(result["model_writes"], 0)
        self.assertEqual(result["context_consistency"]["status"], "consistent")
        self.assertEqual([step["status"] for step in result["steps"]], ["ready", "ready"])
        create, transform = result["steps"]
        self.assertIsNone(create["revision"]["native_token"])
        self.assertTrue(create["revision"]["required_for_execution"])
        self.assertEqual(transform["revision"]["source"], "not_supported_by_e3d_set_transform")
        self.assertEqual(transform["requested_transform"]["converted"]["status"], "complete")
        self.assertEqual(bridge.transform_calls[0][0], "=1/2")
        self.assertEqual(bridge.spatial_calls, [("=1/3", "WORLD")])
        self.assertNotIn("create_element", bridge.__dict__)

    @staticmethod
    def _preview_bridge(revision):
        bridge = FakeBridge()
        native = {"success": True, "dry_run": True, "ce_before": "=1/90", "ce_after": "=1/90",
                  "ce_unchanged": True, "frame": {"requested": "OWNER"}}
        envelope = {"ok": True, "version": "E3D21"}
        bridge.create_element = Mock(return_value={**envelope, "result": {
            **native, "parent": {"refno": "=1/2"}, "revision": revision,
            "submitted": {"name": "/NEW", "attributes": {"HEIG": "100"}, "position": [10, 20, 30], "orientation": None},
        }})
        bridge.set_transform = Mock(return_value={**envelope, "result": {
            **native, "element": {"refno": "=1/3"}, "submitted": {"position": [2, 3, 4], "orientation": None},
        }})
        return bridge

    def test_optional_native_previews_return_exact_revisions_and_context_binding(self):
        revision = "b" * 64
        bridge = self._preview_bridge(revision)
        result = self._plan(bridge, include_native_previews=True)
        self.assertTrue(result["success"])
        self.assertFalse(result["writes_attempted"])
        self.assertEqual(result["native_previews"]["status"], "prepared")
        self.assertTrue(result["native_previews"]["complete"])
        self.assertEqual(result["native_previews"]["prepared_count"], 2)
        self.assertTrue(result["native_previews"]["preview_context"]["boundary_agreement"])
        create, transform = result["steps"]
        self.assertEqual(create["native_preview"]["status"], "prepared")
        self.assertEqual(create["revision"]["native_token"], revision)
        self.assertEqual(create["revision"]["source"], "native_dry_run")
        bridge.create_element.assert_called_once_with("=1/2", "BOX", "/NEW", {"HEIG": "100"},
            [10.0, 20.0, 30.0], None, None, "OWNER", True)
        self.assertEqual(transform["native_preview"]["status"], "prepared")
        bridge.set_transform.assert_called_once_with("=1/3", [2.0, 3.0, 4.0], None, None, "OWNER", True)

    def test_context_drift_after_previews_withholds_the_revision(self):
        result = self._plan(self._preview_bridge("b" * 64), include_native_previews=True, preview_project="OTHER")
        self.assertEqual(result["native_previews"]["status"], "context_unverified")
        self.assertFalse(result["native_previews"]["preview_context"]["boundary_agreement"])
        self.assertEqual(result["steps"][0]["native_preview"]["status"], "context_unverified")
        self.assertIsNone(result["steps"][0]["revision"]["native_token"])

    def test_copy_preview_uses_resolved_refs_and_rejects_different_source(self):
        bridge = FakeBridge()
        preview = {
            "ok": True, "version": "E3D21", "result": {
                "success": True, "dry_run": True, "ce_before": "=1/90", "ce_after": "=1/90",
                "ce_unchanged": True, "frame": {"requested": "OWNER"}, "revision": "b" * 64,
                "parent": {"refno": "=1/2"}, "source": {"refno": "=1/5"},
                "submitted": {"name": "/NEW", "position": None, "orientation": None},
            },
        }
        bridge.copy_element = Mock(return_value=preview)
        args = {"equipment_refno": "=1/1", "include_native_previews": True, "steps": [{
            "step_id": "copy-box", "operation": "copy", "parent_refno": "/UNIT/FRAME",
            "source_refno": "/UNIT/BOX-SEED", "name": "/NEW",
        }]}
        with patch("aveva_e3d_mcp.equipment_model_plan.context_snapshot", side_effect=[context() for _ in range(3)]), \
             patch("aveva_e3d_mcp.equipment_model_plan.equipment_manifest", return_value=manifest()):
            result = equipment_model_plan(bridge, self.settings, args)
        bridge.copy_element.assert_called_once_with("=1/4", "=1/2", "/NEW",
            position=None, x_axis=None, y_axis=None, wrt="OWNER", dry_run=True)
        self.assertEqual(result["steps"][0]["native_preview"]["status"], "unverified")
        self.assertIsNone(result["steps"][0]["revision"]["native_token"])

    def test_native_fault_during_optional_preview_stops_without_followup_context_read(self):
        bridge = FakeBridge()
        bridge.create_element = Mock(side_effect=BridgeError("native fault", diagnostics={"native_fault_latched": True}))
        result = self._plan(bridge, include_native_previews=True)
        self.assertEqual(result["native_previews"]["status"], "stopped_on_native_fault")
        self.assertTrue(result["native_previews"]["stopped_on_native_fault"])
        self.assertIsNone(result["native_previews"]["preview_context"]["after"])
        self.assertEqual(result["test_context_read_count"], 2)
        bridge.create_element.assert_called_once()
        self.assertFalse(hasattr(bridge, "copy_element"))

    def test_context_drift_downgrades_all_steps_to_unknown(self):
        result = self._plan(FakeBridge(), project_after="OTHER")
        self.assertFalse(result["success"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["context_consistency"]["status"], "changed")
        self.assertTrue(all(step["status"] == "unknown" for step in result["steps"]))
        self.assertTrue(all(step["write_viability"] == "unknown" and not step["read_coverage_complete"]
                            for step in result["steps"]))
        self.assertTrue(any(issue.get("field") == "project" for issue in result["issues"]))

    def test_missing_attribute_probe_keeps_create_unknown_without_fabricating_constraints(self):
        bridge = FakeBridge()
        args = {"equipment_refno": "=1/1", "steps": [{
            "step_id": "create-box", "operation": "create", "parent_refno": "=1/2",
            "element_type": "BOX", "name": "/NEW", "attributes": {"HEIG": "100"},
        }]}
        with patch("aveva_e3d_mcp.equipment_model_plan.context_snapshot",
                   side_effect=[context(), context()]), \
             patch("aveva_e3d_mcp.equipment_model_plan.equipment_manifest", return_value=manifest()):
            result = equipment_model_plan(bridge, self.settings, args)
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["success"])
        self.assertTrue(any(item.get("code") == "create_attribute_constraints_unobserved"
                            for item in result["steps"][0]["unknowns"]))
        self.assertEqual(result["steps"][0]["revision"]["native_token"], None)
        self.assertFalse(result["read_coverage_complete"])
        self.assertEqual(result["write_viability"], "unknown")

    def test_manifest_context_mismatch_stops_schema_reads(self):
        bridge = FakeBridge()
        bad_manifest = manifest()
        bad_manifest["result"]["mdb"] = "OTHER"
        args = {"equipment_refno": "=1/1", "steps": [{
            "step_id": "create-box", "operation": "create", "parent_refno": "=1/2",
            "element_type": "BOX", "name": "/NEW",
        }]}
        with patch("aveva_e3d_mcp.equipment_model_plan.context_snapshot",
                   side_effect=[context(), context()]), \
             patch("aveva_e3d_mcp.equipment_model_plan.equipment_manifest", return_value=bad_manifest):
            result = equipment_model_plan(bridge, self.settings, args)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(bridge.schema_calls, [])
        self.assertTrue(any(issue.get("code") == "context_mismatch" and issue.get("field") == "mdb"
                            for issue in result["issues"]))
        self.assertEqual(result["steps"][0]["status"], "unknown")

    def test_copy_plan_reports_source_type_and_revision_gate(self):
        bridge = FakeBridge()
        args = {"equipment_refno": "=1/1", "steps": [{
            "step_id": "copy-box", "operation": "copy", "parent_refno": "=1/2",
            "source_refno": "=1/4", "name": "/COPIED",
        }]}
        with patch("aveva_e3d_mcp.equipment_model_plan.context_snapshot",
                   side_effect=[context(), context()]), \
             patch("aveva_e3d_mcp.equipment_model_plan.equipment_manifest", return_value=manifest()):
            result = equipment_model_plan(bridge, self.settings, args)
        step = result["steps"][0]
        self.assertEqual(step["status"], "ready")
        self.assertEqual(step["source"]["identity"]["type"], "BOX")
        self.assertEqual(step["revision"]["required_for_execution"], True)
        self.assertEqual(step["write_steps"][0]["tool"], "e3d_copy_element")

    def test_schema_and_spatial_identity_mismatch_downgrade_plan(self):
        bridge = FakeBridge()
        original_schema = bridge.element_schema

        def wrong_schema_context(refno, attributes, offset, limit, **kwargs):
            response = original_schema(refno, attributes, offset, limit, **kwargs)
            response["result"]["project"] = "OTHER"
            response["result"]["project_after"] = "OTHER"
            return response

        bridge.element_schema = wrong_schema_context
        result = self._plan(bridge)
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["complete"])
        self.assertTrue(any(issue.get("code") == "context_mismatch" and issue.get("stage") == "attribute_schema"
                            for issue in result["steps"][0]["context_issues"]))

        bridge = FakeBridge()
        original_spatial = bridge.spatial_info

        def wrong_spatial_session(refno, wrt):
            response = original_spatial(refno, wrt)
            response["session_id"] = "other-session"
            return response

        bridge.spatial_info = wrong_spatial_session
        result = self._plan(bridge)
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["complete"])
        self.assertTrue(any(issue.get("code") == "session_mismatch" and issue.get("stage") == "spatial_target_1"
                            for issue in result["steps"][1]["context_issues"]))

    def _mutated_plan(self, method: str, mutate) -> dict:
        bridge = FakeBridge()
        original = getattr(bridge, method)

        def read(*args, **kwargs):
            response = original(*args, **kwargs)
            mutate(response)
            return response

        setattr(bridge, method, read)
        return self._plan(bridge)

    def assert_unverified(self, result: dict):
        self.assertFalse(result["success"])
        self.assertFalse(result["complete"])
        self.assertFalse(result["read_coverage_complete"])
        self.assertEqual(result["write_viability"], "unknown")
        self.assertTrue(all(step["write_viability"] == "unknown" for step in result["steps"]
                            if step["status"] == "unknown"))
        if result["context_consistency"]["status"] != "consistent":
            self.assertTrue(all(step["write_viability"] == "unknown" for step in result["steps"]))

    def test_endpoint_end_context_and_flags_cannot_be_inferred_from_before(self):
        for method in ("element_schema", "spatial_info", "transform_coordinates"):
            for field in ("project_after", "mdb_after", "ce_after", "identity_unchanged", "ce_unchanged"):
                for missing in (True, False):
                    with self.subTest(method=method, field=field, missing=missing):
                        def mutate(response):
                            result = response["result"]
                            if missing:
                                result.pop(field)
                            else:
                                result[field] = False if field.endswith("unchanged") else "OTHER"

                        result = self._mutated_plan(method, mutate)
                        self.assert_unverified(result)
                        self.assertNotEqual(result["context_consistency"]["status"], "consistent")

    def test_manifest_end_context_and_conflicting_envelope_stop_followup_reads(self):
        for field in ("project_after", "mdb_after", "ce_after", "identity_unchanged", "ce_unchanged", "bridge_build"):
            with self.subTest(field=field):
                sample = manifest()
                if field == "bridge_build":
                    sample["bridge_build"] = "other-build"
                    sample["result"]["bridge_build"] = "build-a"
                else:
                    sample["result"].pop(field)
                bridge = FakeBridge()
                with patch("aveva_e3d_mcp.equipment_model_plan.equipment_manifest", return_value=sample), \
                     patch("aveva_e3d_mcp.equipment_model_plan.context_snapshot", side_effect=[context(), context()]):
                    result = equipment_model_plan(bridge, self.settings, {
                        "equipment_refno": "=1/1", "steps": [{"step_id": "new", "operation": "create",
                        "parent_refno": "=1/2", "element_type": "BOX", "name": "/NEW"}]})
                self.assert_unverified(result)
                self.assertEqual(bridge.schema_calls, [])
                self.assertEqual(bridge.spatial_calls, [])
                self.assertEqual(bridge.transform_calls, [])

    def test_all_identity_layers_are_checked_without_result_overrides(self):
        for method in ("element_schema", "spatial_info", "transform_coordinates"):
            for field, expected in (("session_id", "session-a"), ("bridge_build", "build-a")):
                for conflicting_layer in ("envelope", "result"):
                    with self.subTest(method=method, field=field, conflicting_layer=conflicting_layer):
                        def mutate(response):
                            response[field] = "OTHER" if conflicting_layer == "envelope" else expected
                            response["result"][field] = "OTHER" if conflicting_layer == "result" else expected

                        self.assert_unverified(self._mutated_plan(method, mutate))

    def test_nested_endpoint_evidence_cannot_disagree_with_flat_flags(self):
        for method in ("element_schema", "spatial_info"):
            for change in ("project_after", "mdb_before", "ce_after", "ce_type", "same_mdb_instance", "boundary_agreement"):
                with self.subTest(method=method, change=change):
                    def mutate(response):
                        nested = response["result"]["context"]
                        if change == "project_after":
                            nested["after"]["project"] = "OTHER"
                        elif change == "mdb_before":
                            nested["before"].pop("mdb")
                        elif change == "ce_after":
                            nested["after"]["current_element"] = "=1/99"
                        elif change == "ce_type":
                            nested["after"]["current_element_type"] = "BOX"
                        else:
                            nested[change] = False

                    self.assert_unverified(self._mutated_plan(method, mutate))

    def test_initial_snapshot_internal_identity_mismatch_prevents_endpoint_reads(self):
        for field in ("version", "session_id", "bridge_build"):
            with self.subTest(field=field):
                before = context()
                before["result"]["evidence"]["health_after"][field] = "OTHER"
                bridge = FakeBridge()
                with patch("aveva_e3d_mcp.equipment_model_plan.context_snapshot", side_effect=[before, context()]), \
                     patch("aveva_e3d_mcp.equipment_model_plan.equipment_manifest") as read_manifest:
                    result = equipment_model_plan(bridge, self.settings, {
                        "equipment_refno": "=1/1", "steps": [{"step_id": "new", "operation": "create",
                        "parent_refno": "=1/2", "element_type": "BOX", "name": "/NEW"}]})
                self.assert_unverified(result)
                read_manifest.assert_not_called()
                self.assertEqual(bridge.schema_calls, [])

    def test_unverified_final_snapshot_invalidates_each_step_viability(self):
        for change in ("missing_health", "internal_session", "envelope_session", "readiness", "ok"):
            with self.subTest(change=change):
                after = context()
                if change == "missing_health":
                    after["result"]["evidence"].pop("health_before")
                elif change == "internal_session":
                    after["result"]["evidence"]["health_before"]["session_id"] = "OTHER"
                elif change == "envelope_session":
                    after["session_id"] = "OTHER"
                elif change == "readiness":
                    after["result"]["readiness"].pop("after")
                else:
                    after["ok"] = False
                with patch("aveva_e3d_mcp.equipment_model_plan.context_snapshot", side_effect=[context(), after]), \
                     patch("aveva_e3d_mcp.equipment_model_plan.equipment_manifest", return_value=manifest()):
                    result = equipment_model_plan(FakeBridge(), self.settings, {
                        "equipment_refno": "=1/1", "steps": [{"step_id": "new", "operation": "create",
                        "parent_refno": "=1/2", "element_type": "BOX", "name": "/NEW"}]})
                self.assert_unverified(result)

    def test_create_constraints_require_complete_reads_and_explicit_booleans(self):
        for change in ("constraints_requested", "constraints_complete", "constraints", "partial_values",
                       "truncated_ranges", "missing_optional", "known", "supported", "sdk_settable_now", "direct_edit_supported"):
            with self.subTest(change=change):
                def mutate(response):
                    result = response["result"]
                    if result["element"]["refno"] != "=1/4":
                        return
                    attribute = result["attributes"][0]
                    if change in {"constraints_requested", "constraints_complete"}:
                        result.pop(change)
                    elif change in {"constraints", "known", "supported", "sdk_settable_now", "direct_edit_supported"}:
                        attribute.pop(change)
                    elif change == "partial_values":
                        attribute["constraints"]["allowed_values"]["status"] = "read_error"
                    elif change == "truncated_ranges":
                        attribute["constraints"]["allowed_ranges"]["truncated"] = True
                    else:
                        attribute["constraints"].pop("values_or_ranges_optional")

                self.assert_unverified(self._mutated_plan("element_schema", mutate))

    def test_create_attribute_coverage_matches_requested_names_not_row_count(self):
        for change in ("wrong_name", "duplicate", "missing"):
            with self.subTest(change=change):
                def mutate(response):
                    result = response["result"]
                    if result["element"]["refno"] != "=1/4":
                        return
                    if change == "wrong_name":
                        result["attributes"][0]["requested_name"] = "DIAM"
                    elif change == "duplicate":
                        result["attributes"].append(deepcopy(result["attributes"][0]))
                    else:
                        result["attributes"] = []

                self.assert_unverified(self._mutated_plan("element_schema", mutate))

    def test_native_long_attribute_name_keeps_submitted_value_bound_to_requested_alias(self):
        def mutate(response):
            if response["result"]["element"]["refno"] == "=1/4":
                response["result"]["attributes"][0]["name"] = "HEIGHT"

        result = self._mutated_plan("element_schema", mutate)
        self.assertEqual(result["status"], "ready")
        details = next(value for value in result["steps"][0]["constraints"] if value.get("name") == "HEIGHT")
        self.assertEqual(details["submitted_value"], "100")
        self.assertEqual(details["requested_name"], "HEIG")

    def test_schema_identity_and_read_errors_must_be_complete(self):
        for change in ("target_type", "read_errors", "missing_transform_flag"):
            with self.subTest(change=change):
                def mutate(response):
                    result = response["result"]
                    refno = result["element"]["refno"]
                    if change == "target_type" and refno == "=1/3":
                        result["element"]["type"] = "OTHER"
                    elif change == "read_errors":
                        result.pop("read_errors")
                    elif change == "missing_transform_flag" and refno == "=1/3":
                        result["attributes"][0].pop("known")

                self.assert_unverified(self._mutated_plan("element_schema", mutate))

    def test_world_pose_requires_native_frame_finite_axes_and_complete_flags(self):
        for change in ("complete", "frame", "unit", "position", "orientation", "available", "errors"):
            with self.subTest(change=change):
                def mutate(response):
                    result = response["result"]
                    if change == "complete":
                        result.pop("complete")
                    elif change == "frame":
                        result["frame"]["requested"] = "OWNER"
                    elif change == "unit":
                        result["position"]["unit"] = "m"
                    elif change == "position":
                        result["position"]["x"] = float("nan")
                    elif change == "orientation":
                        result["orientation"]["z_axis"] = [0, 0, -1]
                    elif change == "available":
                        result["orientation"]["available"] = False
                    else:
                        result["errors"] = {"orientation": "read failed"}

                result = self._mutated_plan("spatial_info", mutate)
                self.assertFalse(result["success"])
                self.assertFalse(result["read_coverage_complete"])
                self.assertEqual(result["steps"][1]["write_viability"], "unknown")

    def test_coordinate_conversion_covers_exact_inputs_and_native_world_type(self):
        ready = self._plan(FakeBridge())
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["evidence"]["step_0_coordinates"]["result"]["to_frame"]["type"], "WORL")
        for change in ("input", "index", "extra_point", "missing_point", "available", "read_errors", "source_frame", "world_frame"):
            with self.subTest(change=change):
                def mutate(response):
                    result = response["result"]
                    if change == "input":
                        result["points"][0]["input"] = [99, 99, 99]
                    elif change == "index":
                        result["points"][0]["index"] = 1
                    elif change == "extra_point":
                        result["points"].append(deepcopy(result["points"][0]))
                    elif change == "missing_point":
                        result["points"] = []
                    elif change == "available":
                        result.pop("available")
                    elif change == "read_errors":
                        result["read_errors"] = [{"path": "from_frame", "code": "frame_pose_invalid"}]
                    elif change == "source_frame":
                        result["from_frame"]["refno"] = "=1/99"
                    else:
                        result["to_frame"]["type"] = "SUBE"

                result = self._mutated_plan("transform_coordinates", mutate)
                self.assert_unverified(result)

    def test_every_reference_role_is_bounded_before_reads(self):
        cases = [
            ("create", "parent_refno"), ("create", "attribute_reference_refno"),
            ("copy", "source_refno"), ("transform", "target_refno"), ("transform", "wrt"),
        ]
        for operation, field in cases:
            for value in ("/UNIT*", "/UNIT?", "/" + "x" * 256, "=2147483648/1", "=0/1", "CE", "/UNIT\x00"):
                with self.subTest(operation=operation, field=field, value=value):
                    step = {"step_id": "new", "operation": operation}
                    if operation in {"create", "copy"}:
                        step.update(parent_refno="=1/2", name="/NEW")
                        step.update(element_type="BOX") if operation == "create" else step.update(source_refno="=1/4")
                    else:
                        step.update(target_refno="=1/3", position=[1, 2, 3])
                    step[field] = value
                    with patch("aveva_e3d_mcp.equipment_model_plan.context_snapshot") as snapshot, self.assertRaises(ValueError):
                        equipment_model_plan(FakeBridge(), self.settings, {"equipment_refno": "=1/1", "steps": [step]})
                    snapshot.assert_not_called()

    def test_missing_dbref_cannot_resolve_as_a_manifest_name(self):
        sample = manifest()
        sample["result"]["rows"][1]["name"] = "/=1/999"
        bridge = FakeBridge()
        with patch("aveva_e3d_mcp.equipment_model_plan.context_snapshot", side_effect=[context(), context()]), \
             patch("aveva_e3d_mcp.equipment_model_plan.equipment_manifest", return_value=sample):
            result = equipment_model_plan(bridge, self.settings, {"equipment_refno": "=1/1", "steps": [{
                "step_id": "new", "operation": "create", "parent_refno": "=1/999", "element_type": "BOX", "name": "/NEW"}]})
        self.assert_unverified(result)
        self.assertEqual(bridge.schema_calls, [])
        self.assertEqual(result["steps"][0]["parent"]["resolution"], "outside_manifest")

    def test_copy_source_and_named_references_must_resolve_inside_manifest(self):
        cases = [
            {"step_id": "copy", "operation": "copy", "parent_refno": "=1/2", "source_refno": "=1/99", "name": "/NEW"},
            {"step_id": "create", "operation": "create", "parent_refno": "//UNIT/FRAME", "element_type": "BOX", "name": "/NEW"},
        ]
        for step in cases:
            with self.subTest(step=step), \
                 patch("aveva_e3d_mcp.equipment_model_plan.context_snapshot", side_effect=[context(), context()]), \
                 patch("aveva_e3d_mcp.equipment_model_plan.equipment_manifest", return_value=manifest()):
                result = equipment_model_plan(FakeBridge(), self.settings, {"equipment_refno": "=1/1", "steps": [step]})
            self.assert_unverified(result)
            self.assertEqual(result["status"], "unknown")

    def test_explicit_name_frame_binding_and_orientation_inputs_are_verified(self):
        args = {"equipment_refno": "=1/1", "steps": [{"step_id": "move", "operation": "transform",
            "target_refno": "=1/3", "wrt": "/OUTSIDE-FRAME", "orientation": {
                "x_axis": [1, 0, 0], "y_axis": [0, 1, 0]}}]}
        for change in (None, "name", "vector_order", "vector_output"):
            with self.subTest(change=change):
                bridge = FakeBridge()
                original = bridge.transform_coordinates

                def transform(*params):
                    response = original(*params)
                    result = response["result"]
                    if change == "name":
                        result["from_frame"]["name"] = "/OTHER-FRAME"
                    elif change == "vector_order":
                        result["vectors"].reverse()
                    elif change == "vector_output":
                        result["vectors"][0]["output"] = [float("inf"), 0, 0]
                    return response

                bridge.transform_coordinates = transform
                with patch("aveva_e3d_mcp.equipment_model_plan.context_snapshot", side_effect=[context(), context()]), \
                     patch("aveva_e3d_mcp.equipment_model_plan.equipment_manifest", return_value=manifest()):
                    result = equipment_model_plan(bridge, self.settings, args)
                if change is None:
                    self.assertEqual(result["write_viability"], "candidate_for_native_preview")
                    self.assertFalse(result["writes_attempted"])
                    self.assertEqual(result["model_writes"], 0)
                else:
                    self.assert_unverified(result)

    def _steps(self, bridge, steps, sample=None):
        with patch("aveva_e3d_mcp.equipment_model_plan.context_snapshot", side_effect=[context(), context()]), \
             patch("aveva_e3d_mcp.equipment_model_plan.equipment_manifest", return_value=sample or manifest()):
            return equipment_model_plan(bridge, self.settings, {"equipment_refno": "=1/1", "steps": steps})

    def _create(self, bridge, **updates):
        return self._steps(bridge, [{"step_id": "new", "operation": "create", "parent_refno": "=1/2",
            "element_type": "BOX", "name": "/NEW", "attributes": {"HEIG": "100"},
            "attribute_reference_refno": "=1/4", **updates}])

    def test_create_required_slices_and_plain_base_constraints_remain_ready(self):
        bridge = FakeBridge()
        result = self._create(bridge)
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["read_coverage_complete"])
        self.assertEqual(bridge.child_schema_calls, [{"parent_refno": "=1/2", "element_type": "BOX",
            "read_target_refno": "=1/4", "attributes": ["HEIG"], "offset": 0, "limit": 50}])
        self.assertEqual(bridge.schema_calls, [("=1/4", ("HEIG",), True, "BOX")])
        item = result["steps"][0]
        self.assertTrue(item["create_type_evidence"]["required_read_coverage_complete"])
        gate = next(row for row in item["constraints"] if row["name"] == "parent_child_type")
        self.assertEqual(gate["source"], "DbElement.IsCreatable")
        self.assertFalse(gate["execution_authorization"])
        self.assertTrue(gate["native_create_preview_required"])
        self.assertIsNone(item["revision"]["native_token"])
        self.assertFalse(item["create_type_evidence"]["scope"]["future_child_attributes_observed"])

    def test_numeric_identity_can_have_complete_empty_native_name(self):
        bridge = FakeBridge()
        def read(payload):
            sample = child_schema(payload)
            sample["result"]["parent"]["name"] = ""
            sample["result"]["read_target"]["name"] = ""
            return sample
        bridge.child_schema = read
        self.assertEqual(self._create(bridge)["status"], "ready")

    def test_create_never_accepts_base_type_as_actual_udet_exemplar(self):
        for kind, actual in (("CYLI", ":CUSTOM_CYLI"), (":CUSTOM_CYLI", "CYLI"),
                             (":CUSTOM_CYLI", ":OTHER_CYLI")):
            with self.subTest(kind=kind, actual=actual):
                bridge = FakeBridge()
                bridge.child_schema = lambda payload: child_schema(payload, actual=actual)
                result = self._create(bridge, element_type=kind, attribute_reference_refno="=1/3")
                self.assert_unverified(result)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(bridge.schema_calls, [])
                self.assertTrue(any(row["code"] == "attribute_reference_actual_type_unverified"
                                    for row in result["steps"][0]["unknowns"]))

    def test_matching_actual_udet_keeps_legacy_constraints_unknown_without_base_downgrade(self):
        bridge = FakeBridge()
        result = self._create(bridge, element_type=":CUSTOM_CYLI", attribute_reference_refno="=1/3")
        self.assert_unverified(result)
        item = result["steps"][0]
        self.assertEqual(item["issues"], [])
        self.assertTrue(item["create_type_evidence"]["read_target_actual_type_matches"])
        self.assertTrue(item["create_type_evidence"]["required_read_coverage_complete"])
        self.assertTrue(any(row["code"] == "create_attribute_constraints_actual_type_unavailable" for row in item["unknowns"]))
        self.assertEqual(bridge.schema_calls, [])

    def test_no_attribute_create_can_be_candidate_without_existing_exemplar(self):
        bridge = FakeBridge()
        result = self._create(bridge, attributes={}, element_type=":CUSTOM_CYLI")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["write_viability"], "candidate_for_native_preview")
        self.assertTrue(result["read_coverage_complete"])
        self.assertIsNone(bridge.child_schema_calls[0]["read_target_refno"])
        sample = result["evidence"]["create_child_schema_1"]["result"]
        self.assertFalse(sample["complete"])
        self.assertFalse(sample["success"])
        self.assertFalse(sample["instance_attributes"]["complete"])
        self.assertEqual(bridge.schema_calls, [])

    def test_create_creatability_observation_has_three_states_and_never_authorizes(self):
        for value, status in ((True, "ready"), (False, "blocked"), (None, "unknown")):
            with self.subTest(creatable=value):
                bridge = FakeBridge()
                bridge.child_schema = lambda payload: child_schema(payload, creatable=value)
                result = self._create(bridge, attributes={})
                self.assertEqual(result["status"], status)
                gate = next(row for row in result["steps"][0]["constraints"] if row["name"] == "parent_child_type")
                self.assertIs(gate["sdk_creatable_now"], value)
                self.assertFalse(gate["execution_authorization"])
                self.assertEqual(result["model_writes"], 0)
                self.assertFalse(result["writes_attempted"])

    def test_type_metadata_page_and_inspection_cutoff_are_separate_from_required_create_slices(self):
        for mode in ("paged", "cutoff", "unreadable"):
            with self.subTest(mode=mode):
                bridge = FakeBridge()
                def read(payload):
                    sample = child_schema(payload)
                    page = sample["result"]["type_system_attributes"]
                    if mode == "paged":
                        template = page["attributes"][0]
                        page.update(total_count=51, returned_count=50, has_more=True, next_offset=50,
                                    attributes=[{**template, "sdk_index": i} for i in range(50)], whole_type_coverage=False)
                    else:
                        page.update(total_count=4097 if mode == "cutoff" else None, returned_count=0,
                            has_more=None, next_offset=None, attributes=[], complete=False, whole_type_coverage=False,
                            inspection_limited=True if mode == "cutoff" else None,
                            errors={"inspection_limit" if mode == "cutoff" else "system_attributes": "unavailable"})
                        sample["result"].update(success=False, complete=False)
                    return sample
                bridge.child_schema = read
                result = self._create(bridge)
                self.assertEqual(result["status"], "ready")
                self.assertTrue(result["read_coverage_complete"])
                item = result["steps"][0]["create_type_evidence"]
                self.assertFalse(item["type_system_attributes"]["whole_type_coverage"])
                self.assertFalse(item["scope"]["type_system_attributes_are_instance_attributes"])
                self.assertFalse(item["scope"]["future_child_attributes_observed"])
                self.assertEqual(len(bridge.child_schema_calls), 1)

    def test_explicit_parent_and_exemplar_are_bound_to_manifest_numeric_identities(self):
        bridge = FakeBridge()
        result = self._create(bridge, parent_refno="/UNIT/FRAME", attribute_reference_refno="/UNIT/BOX-SEED")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(bridge.child_schema_calls[0]["parent_refno"], "=1/2")
        self.assertEqual(bridge.child_schema_calls[0]["read_target_refno"], "=1/4")
        for role in ("parent_refno", "attribute_reference_refno"):
            with self.subTest(role=role):
                bridge = FakeBridge()
                result = self._create(bridge, **{role: "=1/999"})
                self.assert_unverified(result)
                self.assertEqual(bridge.schema_calls, [])
                if role == "parent_refno":
                    self.assertEqual(bridge.child_schema_calls, [])
                else:
                    self.assertIsNone(bridge.child_schema_calls[0]["read_target_refno"])

    def test_create_parent_actual_identity_and_partial_exemplar_are_required(self):
        for mode in ("parent_actual", "parent_base", "parent_name", "read_target_name", "instance_partial", "request_echo", "exemplar_base_drift"):
            with self.subTest(mode=mode):
                bridge = FakeBridge()
                def read(payload):
                    sample = child_schema(payload)
                    result = sample["result"]
                    if mode in ("parent_actual", "parent_base"):
                        field = "actual_type" if mode == "parent_actual" else "base_type"
                        typed = result["parent"][field]
                        typed.update(name=None, complete=False, errors={"name": "unavailable"})
                        result["parent"]["complete"] = False
                    elif mode in ("parent_name", "read_target_name"):
                        target = result["parent" if mode == "parent_name" else "read_target"]
                        target.update(name=None, complete=False, errors={"name": "unavailable"})
                    elif mode == "instance_partial":
                        row = result["instance_attributes"]["attributes"][0]
                        row.update(sdk_settable_now=None, complete=False, errors={"sdk_settable_now": "unavailable"})
                        result["instance_attributes"].update(status="partial", complete=False)
                    elif mode == "exemplar_base_drift":
                        result["read_target"]["base_type"].update(name="OTHER", short_name="OTHER")
                    else:
                        result["requested_element_type"] = "OTHER"
                    if mode != "exemplar_base_drift":
                        result.update(success=False, complete=False)
                    return sample
                bridge.child_schema = read
                result = self._create(bridge)
                self.assert_unverified(result)
                self.assertEqual(bridge.schema_calls, [])

    def test_child_schema_boundary_identity_layers_cannot_override_outer_bracket(self):
        for layer in ("envelope", "result", "before", "after"):
            for field in ("session_id", "bridge_build"):
                for value in (None, "OTHER"):
                    with self.subTest(layer=layer, field=field, value=value):
                        bridge = FakeBridge()
                        def read(payload):
                            sample = child_schema(payload)
                            target = sample if layer == "envelope" else sample["result"] if layer == "result" else sample["result"]["context"][layer]
                            target[field] = value
                            return sample
                        bridge.child_schema = read
                        result = self._create(bridge)
                        self.assert_unverified(result)
                        self.assertEqual(bridge.schema_calls, [])
                        self.assertEqual(bridge.transform_calls, [])

    def test_all_child_identity_layers_matching_each_other_still_must_match_outer_session_build(self):
        for field in ("session_id", "bridge_build"):
            with self.subTest(field=field):
                bridge = FakeBridge()
                def read(payload):
                    sample = child_schema(payload)
                    result = sample["result"]
                    for layer in sample, result, result["context"]["before"], result["context"]["after"]:
                        layer[field] = "OTHER"
                    return sample
                bridge.child_schema = read
                result = self._create(bridge)
                self.assert_unverified(result)
                self.assertTrue(any(issue["field"] == field for issue in result["steps"][0]["context_issues"]))
                self.assertEqual(bridge.schema_calls, [])

    def test_complete_false_reader_does_not_hide_unknown_requested_type_or_missing_parent_binding(self):
        for mode in ("requested_type", "parent_binding"):
            with self.subTest(mode=mode):
                bridge = FakeBridge()
                def read(payload):
                    sample = child_schema(payload)
                    if mode == "requested_type":
                        sample["result"]["requested_type"].update(name="OTHER", short_name="OTHER")
                        sample["result"]["read_target"]["actual_type"].update(name="OTHER", short_name="OTHER")
                    else:
                        sample["result"]["parent"]["request_binding"].pop("verified")
                    return sample
                bridge.child_schema = read
                result = self._create(bridge)
                self.assert_unverified(result)
                self.assertEqual(bridge.schema_calls, [])

    def test_child_boundary_errors_flags_and_typed_ce_do_not_allow_followup_reads(self):
        for mode in ("project", "mdb", "ce_refno", "ce_base_type", "ce_actual_type", "identity_flag", "ce_flag", "missing_errors", "partial_errors"):
            with self.subTest(mode=mode):
                bridge = FakeBridge()
                def read(payload):
                    sample = child_schema(payload)
                    result = sample["result"]
                    nested = result["context"]
                    if mode == "identity_flag":
                        nested["identity_unchanged"] = False  # Same-name native object replacement.
                    elif mode == "ce_flag":
                        nested.pop("ce_unchanged")
                    elif mode == "missing_errors":
                        nested["after"].pop("errors")
                    elif mode == "partial_errors":
                        nested["after"].update(project=None, errors={"project": "unavailable"})
                        nested["identity_unchanged"] = None
                    else:
                        nested["after"][mode] = "=1/99" if mode == "ce_refno" else "OTHER"
                        nested["identity_unchanged" if mode in ("project", "mdb") else "ce_unchanged"] = False
                    result.update(success=False, complete=False)
                    return sample
                bridge.child_schema = read
                result = self._plan(bridge)
                self.assert_unverified(result)
                self.assertEqual(bridge.schema_calls, [])
                self.assertEqual(bridge.spatial_calls, [])
                self.assertEqual(bridge.transform_calls, [])
                self.assertEqual(len(bridge.child_schema_calls), 1)
                self.assertEqual(result["steps"][1]["status"], "unknown")

    def test_child_context_must_match_outer_names_and_typed_ce_across_separate_calls(self):
        for mode in ("project", "mdb", "ce_refno", "typed_ce_between_calls"):
            with self.subTest(mode=mode):
                bridge = FakeBridge()
                def read(payload):
                    sample = child_schema(payload)
                    if mode != "typed_ce_between_calls" or len(bridge.child_schema_calls) == 2:
                        field = "ce_actual_type" if mode == "typed_ce_between_calls" else mode
                        for boundary in sample["result"]["context"]["before"], sample["result"]["context"]["after"]:
                            boundary[field] = "=1/99" if field == "ce_refno" else "OTHER"
                    return sample
                bridge.child_schema = read
                steps = [{"step_id": "first", "operation": "create", "parent_refno": "=1/2", "element_type": "BOX", "name": "/FIRST"},
                         {"step_id": "second", "operation": "create", "parent_refno": "=1/2", "element_type": "CYLI", "name": "/SECOND"}]
                result = self._steps(bridge, steps)
                self.assert_unverified(result)
                self.assertEqual(len(bridge.child_schema_calls), 2 if mode == "typed_ce_between_calls" else 1)

    def test_create_cache_keys_include_parent_requested_type_exemplar_and_attributes(self):
        bridge = FakeBridge()
        base = {"operation": "create", "parent_refno": "=1/2", "element_type": "BOX",
                "attributes": {"HEIG": "100"}, "attribute_reference_refno": "=1/4"}
        variants = [{}, {}, {"element_type": ":CUSTOM"}, {"attribute_reference_refno": "=1/3"},
                    {"attributes": {"DIAM": "5"}}, {"parent_refno": "=1/1"}]
        steps = [{**base, **variant, "step_id": "new" + str(index), "name": "/NEW" + str(index)}
                 for index, variant in enumerate(variants)]
        result = self._steps(bridge, steps)
        self.assertEqual(len(bridge.child_schema_calls), 5)
        self.assertEqual(len([key for key in result["evidence"] if key.startswith("create_child_schema_")]), 5)
        self.assertEqual(bridge.child_schema_calls[0]["offset"], 0)
        self.assertEqual(bridge.child_schema_calls[0]["limit"], 50)
        self.assertTrue(result["steps"][0]["create_type_evidence"]["required_read_coverage_complete"])
        self.assertTrue(result["steps"][1]["create_type_evidence"]["required_read_coverage_complete"])

    def test_copy_and_transform_keep_original_routes_and_parent_membertype_gates(self):
        for mutation in (None, "parent_type", "ambiguous_child"):
            with self.subTest(mutation=mutation):
                bridge = FakeBridge()
                original = bridge.element_schema
                def read(*args, **kwargs):
                    sample = original(*args, **kwargs)
                    if args[0] == "=1/2":
                        if mutation == "parent_type":
                            sample["result"]["element"]["type"] = "OTHER"
                        elif mutation == "ambiguous_child":
                            sample["result"]["child_types"].append({"short_name": "BOX", "sdk_creatable_now": False})
                    return sample
                bridge.element_schema = read
                result = self._steps(bridge, [
                    {"step_id": "copy", "operation": "copy", "parent_refno": "=1/2", "source_refno": "=1/4", "name": "/COPY"},
                    {"step_id": "move", "operation": "transform", "target_refno": "=1/3", "position": [1, 2, 3]}])
                self.assertEqual(bridge.child_schema_calls, [])
                self.assertEqual(result["status"], "ready" if mutation is None else "unknown")
                self.assertEqual(result["model_writes"], 0)
                self.assertEqual(result["commands"], 0)

    def test_mixed_plan_reads_child_schema_only_for_create_and_never_writes(self):
        bridge = FakeBridge()
        result = self._steps(bridge, [
            {"step_id": "create", "operation": "create", "parent_refno": "=1/2", "element_type": "BOX", "name": "/NEW"},
            {"step_id": "copy", "operation": "copy", "parent_refno": "=1/2", "source_refno": "=1/4", "name": "/COPY"},
            {"step_id": "move", "operation": "transform", "target_refno": "=1/3", "position": [1, 2, 3]}])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(bridge.child_schema_calls), 1)
        self.assertEqual(bridge.schema_calls, [("=1/2", ("NAME",), False, ""), ("=1/3", ("POS", "ORI"), False, "")])
        for field in ("model_writes", "view_writes", "commands"):
            self.assertEqual(result[field], 0)
        self.assertFalse(result["writes_attempted"])

    def test_optional_child_endpoint_and_ordinary_transport_failures_stay_unknown_without_fallback(self):
        for code in (404, 500):
            with self.subTest(code=code):
                bridge = FakeBridge()
                diagnostics = {"original": "ordinary failure"}
                def read(payload):
                    raise BridgeError("ordinary failure", http_status=code, diagnostics=diagnostics)
                bridge.child_schema = read
                result = self._create(bridge)
                self.assert_unverified(result)
                self.assertEqual(len(bridge.child_schema_calls), 1)
                self.assertEqual(bridge.schema_calls, [])
                self.assertEqual(bridge.transform_calls, [])
                if code == 500:
                    self.assertEqual(result["evidence"]["create_child_schema_1"]["diagnostics"], diagnostics)

    def test_nativefault_diagnostics_propagate_and_stop_all_remaining_reads(self):
        markers = ({"has_fault": True}, {"native_fault": True}, {"native_fault_latched": True},
                   {"error_code": "native_fault"}, {"error_code": "native_database_fault"})
        for marker in markers:
            for raised in (False, True):
                with self.subTest(marker=marker, raised=raised):
                    bridge = FakeBridge()
                    diagnostics = {"nested": [marker], "original": "unaltered"}
                    def read(payload):
                        if raised:
                            raise BridgeError("original failure", http_status=404, diagnostics=diagnostics)
                        return diagnostics
                    bridge.child_schema = read
                    with patch("aveva_e3d_mcp.equipment_model_plan.context_snapshot", side_effect=[context(), context()]) as snapshots, \
                         patch("aveva_e3d_mcp.equipment_model_plan.equipment_manifest", return_value=manifest()), \
                         self.assertRaises(BridgeError) as caught:
                        equipment_model_plan(bridge, self.settings, {"equipment_refno": "=1/1", "steps": [
                            {"step_id": "create", "operation": "create", "parent_refno": "=1/2", "element_type": "BOX", "name": "/NEW"},
                            {"step_id": "move", "operation": "transform", "target_refno": "=1/3", "position": [1, 2, 3]}]})
                    self.assertEqual(caught.exception.diagnostics, diagnostics)
                    self.assertEqual(snapshots.call_count, 1)
                    self.assertEqual(len(bridge.child_schema_calls), 1)
                    self.assertEqual(bridge.schema_calls, [])
                    self.assertEqual(bridge.spatial_calls, [])
                    self.assertEqual(bridge.transform_calls, [])


if __name__ == "__main__":
    unittest.main()
