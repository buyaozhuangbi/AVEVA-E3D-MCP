"""Offline contract for optional clipping samples in the existing view observer."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server
from aveva_e3d_mcp.view_observation import view_observation_result


def health(**changes):
    return {"ok": True, "connected": True, "version": "E3D21", "session_id": "session-1",
            "bridge_build": "build-1", **changes}


def view(**changes):
    return {"ok": True, "version": "E3D21", "result": {
        "success": True, "available": True, "partial": False, "errors": {},
        "view_id": 11, "drawlist_id": 17, "centre": [12.0, 24.0, 36.0],
        "direction": [1.0, 1.0, -1.0], "radius": 300.0, "bearing_degrees": 45.0,
        "elevation_degrees": -35.0, "extra_twist_degrees": 0.0, "perspective": False,
        "shaded": True, "width": 1600, "height": 1200, "drawlist_member_count": 2,
        "drawlist_membership_fingerprint": "a" * 64, "drawlist_visuals_fingerprint": "b" * 64,
        **changes,
    }}


def clipping(**changes):
    return {"ok": True, "version": "E3D21", "result": {
        "success": True, "available": True, "complete": True, "status": "complete",
        "source": "E3D SDK View3D.Clipping public getters", "view_id": 11,
        "clipping_enabled": True, "capping": False, "cap_colour": 326,
        "box_data": {"available": True, "lengths": [100.0, 200.0, 300.0],
                     "position": [1.0, 2.0, 3.0], "orientation": [0.0, 0.0, 0.0]},
        "box_faces": [{"face": face, "defined": False, "active": False}
                      for face in ("W", "S", "D", "E", "N", "U")],
        "read_errors": {}, "coordinate_frame": "SDK native (unverified)",
        "units": "SDK native (unverified)", "view_unchanged": True,
        "side_effects": "May initialize managed clipping cache; no setters or refresh.",
        **changes,
    }}


class ViewObservationClippingTests(unittest.TestCase):
    def setUp(self):
        self.client = BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=False))
        self.server = create_server(self.client.settings)
        sleep = patch("aveva_e3d_mcp.bridge.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def call(self, arguments):
        response = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "e3d_observe_view", "arguments": arguments}})
        self.assertNotIn("error", response, response)
        return response["result"]

    def observe(self, *, states=None, clips=None, contexts=None, samples=2):
        states = iter(states if states is not None else [view() for _ in range(samples)])
        clips = iter(clips if clips is not None else [clipping(), clipping()])
        contexts = iter(contexts if contexts is not None else [health(), health()])

        def request(method, path, *args, **kwargs):
            self.assertEqual(method, "GET")
            self.assertEqual(args, ())
            self.assertEqual(kwargs, {"version": "E3D21" if path == "/api/v1/view/clipping-state" else None})
            # A new mutation, capture, PML, or spatial-map route fails this test.
            self.assertIn(path, ("/health", "/api/v1/view/state", "/api/v1/view/clipping-state"))
            values = {"/health": contexts, "/api/v1/view/state": states,
                      "/api/v1/view/clipping-state": clips}
            value = next(values[path])
            if isinstance(value, Exception):
                raise value
            return value

        with patch.object(self.client, "_request", side_effect=request) as native:
            response = self.client.observe_view(samples, 50, include_clipping=True)
        return response, native.call_args_list

    def test_discovery_adds_only_an_optional_boolean_on_existing_read_only_tool(self):
        response = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        tool = next(item for item in response["result"]["tools"] if item["name"] == "e3d_observe_view")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        self.assertFalse(tool["inputSchema"]["additionalProperties"])
        option = tool["inputSchema"]["properties"]["include_clipping"]
        self.assertEqual(option["type"], "boolean")
        self.assertIs(option["default"], False)
        self.assertNotIn("include_clipping", tool["inputSchema"].get("required", []))
        self.assertIn("managed caches", tool["description"])
        self.assertIn("pixel visibility", tool["description"])

    def test_default_and_explicit_false_keep_the_original_tool_forwarding_shape(self):
        for arguments in ({}, {"include_clipping": False}):
            with self.subTest(arguments=arguments), patch.object(
                    BridgeClient, "observe_view", return_value={"ok": True, "result": {"success": True}}) as observe:
                response = self.call({"samples": 2, "interval_ms": 50, **arguments})
            self.assertFalse(response.get("isError", False), response)
            observe.assert_called_once_with(2, 50)

    def test_tool_forwards_true_without_requiring_commands(self):
        payload = {"ok": True, "result": {"success": True, "stable": None, "complete": False}}
        with patch.object(BridgeClient, "observe_view", return_value=payload) as observe:
            response = self.call({"samples": 2, "interval_ms": 50, "include_clipping": True})
        observe.assert_called_once_with(2, 50, include_clipping=True)
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"], payload)
        self.assertFalse(self.client.settings.allow_commands)

    def test_invalid_boolean_controls_are_rejected_before_any_native_io(self):
        with patch.object(BridgeClient, "_request") as native:
            for value in (None, 0, 1, "true", [], {}):
                with self.subTest(value=value):
                    with self.assertRaisesRegex(ValueError, "include_clipping must be a boolean"):
                        self.client.observe_view(include_clipping=value)
                    self.assertTrue(self.call({"include_clipping": value})["isError"])
            for name in ("capture_after", "dry_run", "version", "include_planes"):
                with self.subTest(name=name):
                    self.assertTrue(self.call({name: True})["isError"])
        native.assert_not_called()
        self.sleep.assert_not_called()

    def test_default_output_and_reads_remain_identical_with_no_clipping_fields(self):
        responses = []
        for options in ({}, {"include_clipping": False}):
            with patch.object(self.client, "health", side_effect=[health(), health()]), \
                    patch.object(self.client, "view_state", side_effect=[view(), view()]), \
                    patch.object(self.client, "view_clipping_state") as clip:
                response = self.client.observe_view(2, 50, **options)
            clip.assert_not_called()
            expected = {"ok": True, "version": "E3D21", "result": view_observation_result(
                [view(), view()], health(), health(), {})}
            self.assertEqual(json.dumps(response), json.dumps(expected))
            self.assertEqual(set(response["result"]), {
                "success", "stable", "complete", "states", "context_before", "context_after",
                "changed_fields", "read_errors", "scope"})
            self.assertNotIn("database_readiness_verified", response["result"]["scope"])
            self.assertNotIn("native_fault_absence_verified", response["result"]["scope"])
            responses.append(response)
        self.assertEqual(json.dumps(responses[0]), json.dumps(responses[1]))

    def test_existing_positional_version_remains_compatible_and_clipping_is_keyword_only(self):
        with patch.object(self.client, "health", side_effect=[health(), health()]) as context, \
                patch.object(self.client, "view_state", side_effect=[view(), view()]) as state, \
                patch.object(self.client, "view_clipping_state", side_effect=[clipping(), clipping()]) as clip:
            response = self.client.observe_view(2, 50, "E3D21", include_clipping=True)
        self.assertIs(response["result"]["stable"], True)
        for operation in (context, state, clip):
            self.assertEqual(len(operation.call_args_list), 2)
            for call in operation.call_args_list:
                self.assertEqual(call.kwargs, {"version": "E3D21"})
        with patch.object(self.client, "_request") as native:
            with self.assertRaises(TypeError):
                self.client.observe_view(2, 50, "E3D21", True)
        native.assert_not_called()

    def test_bounded_workflow_reads_clipping_only_after_first_and_final_view_samples(self):
        for samples in (2, 3, 10):
            with self.subTest(samples=samples):
                self.sleep.reset_mock()
                response, calls = self.observe(samples=samples)
                paths = [call.args[1] for call in calls]
                self.assertEqual(paths, ["/health", "/api/v1/view/state", "/api/v1/view/clipping-state"] +
                    ["/api/v1/view/state"] * (samples - 1) + ["/api/v1/view/clipping-state", "/health"])
                self.assertEqual(len(paths), samples + 4)
                self.assertEqual(paths.count("/api/v1/view/clipping-state"), 2)
                self.assertEqual(self.sleep.call_count, samples - 1)
                self.assertIs(response["result"]["stable"], True)

    def test_equal_complete_clipping_retains_both_raw_receipts_and_conservative_scope(self):
        baseline, final = clipping(), clipping()
        original = copy.deepcopy([baseline, final])
        response, _ = self.observe(clips=[baseline, final])
        result = response["result"]
        self.assertIs(result["baseline_clipping"], baseline)
        self.assertIs(result["final_clipping"], final)
        self.assertEqual([baseline, final], original)
        self.assertIs(result["stable"], True)
        self.assertIs(result["complete"], True)
        self.assertIs(result["clipping_observation"]["unchanged"], True)
        self.assertIs(result["clipping_observation"]["complete"], True)
        self.assertEqual(result["changed_fields"], [])
        self.assertEqual(result["read_errors"], {})
        scope = result["clipping_observation"]["scope"]
        self.assertFalse(scope["pixel_visibility_verified"])
        self.assertFalse(scope["target_clipped_verified"])
        self.assertFalse(scope["future_stability_guaranteed"])
        self.assertEqual(scope["coordinate_frame"], "SDK native (unverified)")
        self.assertIn("managed caches", result["scope"]["clipping"])

    def test_clipping_field_changes_are_included_in_aggregate_stability(self):
        changes = {
            "clipping_enabled": lambda row: row.update(clipping_enabled=False),
            "capping": lambda row: row.update(capping=True),
            "cap_colour": lambda row: row.update(cap_colour=1),
            "box_data.lengths": lambda row: row["box_data"].update(lengths=[1.0, 2.0, 3.0]),
            "box_data.position": lambda row: row["box_data"].update(position=[0.0, 0.0, 0.0]),
            "box_data.orientation": lambda row: row["box_data"].update(orientation=[1.0, 0.0, 0.0]),
            "box_faces": lambda row: row["box_faces"][0].update(defined=True, active=True),
        }
        for field, change in changes.items():
            with self.subTest(field=field):
                final = clipping()
                change(final["result"])
                response, _ = self.observe(clips=[clipping(), final])
                result = response["result"]
                self.assertIs(result["stable"], False)
                self.assertIs(result["complete"], True)
                self.assertIs(result["clipping_observation"]["unchanged"], False)
                self.assertIn("clipping." + field, result["changed_fields"])

    def test_face_order_does_not_create_a_false_change(self):
        final = clipping()
        final["result"]["box_faces"].reverse()
        response, _ = self.observe(clips=[clipping(), final])
        self.assertIs(response["result"]["stable"], True)
        self.assertIs(response["result"]["clipping_observation"]["unchanged"], True)
        self.assertEqual(response["result"]["final_clipping"], final)

    def test_partial_malformed_or_unsupported_clipping_leaves_stability_unknown(self):
        changes = {
            "unavailable": lambda row: row.update(success=False, available=False),
            "partial": lambda row: row.update(complete=False, status="partial", read_errors={"getter": "failed"}),
            "unknown_coverage": lambda row: row.pop("status"),
            "missing_face": lambda row: row["box_faces"].pop(),
            "nonfinite": lambda row: row["box_data"].update(position=[float("nan"), 0.0, 0.0]),
            "boolean_number": lambda row: row["box_data"].update(lengths=[True, 2.0, 3.0]),
            "future_field": lambda row: row.update(individual_planes=[]),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                final = clipping()
                change(final["result"])
                response, _ = self.observe(clips=[clipping(), final])
                result = response["result"]
                self.assertIsNone(result["stable"])
                self.assertIsNone(result["clipping_observation"]["unchanged"])
                self.assertIs(result["complete"], False)
                self.assertTrue(result["read_errors"])
                self.assertIs(result["final_clipping"], final)

    def test_known_clipping_change_stays_false_when_other_observations_are_incomplete(self):
        response, _ = self.observe(clips=[clipping(), clipping(capping=True)],
                                   contexts=[health(), health(session_id="")])
        result = response["result"]
        self.assertIs(result["complete"], False)
        self.assertIs(result["stable"], False)
        self.assertIs(result["clipping_observation"]["unchanged"], False)
        self.assertIn("clipping.capping", result["changed_fields"])
        self.assertIn("context_after.session_id", result["read_errors"])

    def test_known_camera_change_stays_false_when_clipping_is_incomplete(self):
        response, _ = self.observe(states=[view(), view(radius=450.0)],
                                   clips=[clipping(), clipping(complete=False, status="partial")])
        result = response["result"]
        self.assertIs(result["complete"], False)
        self.assertIs(result["stable"], False)
        self.assertIsNone(result["clipping_observation"]["unchanged"])
        self.assertIn("radius", result["changed_fields"])

    def test_view_identity_drift_is_retained_even_with_partial_clipping(self):
        final = clipping(view_id=12, view_unchanged=False, complete=False, status="context_changed")
        final["result"]["box_data"] = None
        response, _ = self.observe(clips=[clipping(), final])
        result = response["result"]
        self.assertIs(result["stable"], False)
        self.assertIs(result["complete"], False)
        self.assertIs(result["clipping_observation"]["unchanged"], False)
        self.assertIn("clipping.view_id", result["changed_fields"])
        self.assertIn("clipping.view_object", result["changed_fields"])

    def test_failed_clipping_reads_preserve_the_other_receipt_without_retries(self):
        for failed_index in (0, 1):
            with self.subTest(failed_index=failed_index):
                clips = [clipping(), clipping()]
                retained = clips[1 - failed_index]
                clips[failed_index] = BridgeError("clipping transport failed", transport_error=True)
                response, calls = self.observe(clips=clips)
                result = response["result"]
                self.assertIsNone(result["stable"])
                self.assertIs(result["complete"], False)
                self.assertIs(result["baseline_clipping" if failed_index else "final_clipping"], retained)
                self.assertIsNone(result["final_clipping" if failed_index else "baseline_clipping"])
                self.assertIn("final_clipping" if failed_index else "baseline_clipping", result["read_errors"])
                self.assertEqual(len(calls), 6)

    def test_error_envelopes_are_retained_without_inventing_clipping_equality(self):
        error = {"ok": False, "version": "E3D21", "error": "native clipping failed", "result": None}
        response, _ = self.observe(clips=[clipping(), error])
        result = response["result"]
        self.assertIs(result["final_clipping"], error)
        self.assertIsNone(result["stable"])
        self.assertIsNone(result["clipping_observation"]["unchanged"])
        self.assertFalse(result["complete"])

    def test_equal_samples_preserve_final_health_diagnostics_without_certifying_readiness(self):
        for final in (
            health(state={"native_database_fault": {"has_fault": True},
                          "recovery_required": True, "database_ready": False}),
            health(diagnostic_error="Final health diagnostic read failed"),
        ):
            with self.subTest(final=final):
                response, _ = self.observe(contexts=[health(), final])
                result = response["result"]
                self.assertIs(result["context_after"], final)
                self.assertIs(result["stable"], True)
                self.assertIs(result["complete"], True)
                self.assertIs(result["clipping_observation"]["unchanged"], True)
                self.assertIs(result["scope"]["database_readiness_verified"], False)
                self.assertIs(result["scope"]["native_fault_absence_verified"], False)

    def test_observed_changes_stay_false_with_final_health_faults_or_diagnostic_errors(self):
        for final in (
            health(state={"native_database_fault": {"has_fault": True},
                          "recovery_required": True, "database_ready": False}),
            health(diagnostic_error="Final health diagnostic read failed"),
        ):
            for change in ("view", "clipping"):
                with self.subTest(final=final, change=change):
                    response, _ = self.observe(
                        contexts=[health(), final],
                        states=[view(), view(radius=450.0)] if change == "view" else None,
                        clips=[clipping(), clipping(capping=True)] if change == "clipping" else None)
                    result = response["result"]
                    self.assertIs(result["context_after"], final)
                    self.assertIs(result["stable"], False)
                    self.assertIs(result["complete"], True)
                    self.assertIn("radius" if change == "view" else "clipping.capping", result["changed_fields"])
                    self.assertIs(result["scope"]["database_readiness_verified"], False)
                    self.assertIs(result["scope"]["native_fault_absence_verified"], False)

    def test_mock_and_unsupported_versions_preserve_explicit_clipping_unavailability(self):
        for settings in (Settings(mode="mock", version="E3D21"), Settings(mode="bridge", version="E3D31")):
            with self.subTest(settings=settings):
                client = BridgeClient(settings)
                with patch.object(client, "health", return_value=health()), \
                        patch.object(client, "view_state", return_value=view()), \
                        patch.object(client, "_request") as native:
                    response = client.observe_view(2, 50, include_clipping=True)
                native.assert_not_called()
                result = response["result"]
                code = "native_unavailable" if settings.mode == "mock" else "unsupported_version"
                self.assertEqual(result["baseline_clipping"]["result"]["error_code"], code)
                self.assertEqual(result["final_clipping"]["result"]["error_code"], code)
                self.assertIsNone(result["clipping_observation"]["unchanged"])
                self.assertIsNone(result["stable"])
                self.assertFalse(result["complete"])


if __name__ == "__main__":
    unittest.main()
