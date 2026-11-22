from __future__ import annotations

import base64
import copy
import json
import struct
import sys
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


def camera(**changes):
    return {
        "success": True, "available": True, "partial": False, "errors": {},
        "view_id": 11, "drawlist_id": 17, "centre": [12.0, 24.0, 36.0],
        "direction": [1.0, 1.0, -1.0], "radius": 300.0,
        "bearing_degrees": 45.0, "elevation_degrees": -35.26438968,
        "extra_twist_degrees": 0.0, "perspective": False, "shaded": True,
        "width": 1600, "height": 1200, "drawlist_member_count": 2,
        "drawlist_membership_fingerprint": "a" * 64,
        "drawlist_visuals_fingerprint": "b" * 64,
        "current_element_refno": "=1/2", "source": "E3D SDK View3D getters",
        **changes,
    }


def state(**changes):
    return {"ok": True, "version": "E3D21", "result": camera(**changes)}


def context(**changes):
    return {"ok": True, "version": "E3D21", "session_id": "session-1",
            "bridge_build": "build-1", "connected": True, **changes}


def native_observation(**changes):
    return {
        "before_refresh": camera(), "before_bitmap": camera(), "after_bitmap": camera(),
        "complete": True, "view_object_unchanged": True, "read_errors": {},
        "scope": "Camera and Drawlist observations around the native bitmap call.", **changes,
    }


def capture_payload(*, native=True):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
           + chunk(b"IDAT", zlib.compress((b"\x00" + bytes(64 * 3)) * 64))
           + chunk(b"IEND", b""))
    result = {"success": True, "mime_type": "image/png", "width": 64, "height": 64,
              "image_base64": base64.b64encode(png).decode("ascii"),
              "source": "E3D View3D.GetBitmap", "drawlist_id": 17}
    if native:
        result["native_capture_observation"] = native_observation()
    return {"ok": True, "version": "E3D21", "result": result}


class CaptureCameraConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=True))
        self.addCleanup(patch.stopall)
        patch("aveva_e3d_mcp.bridge.time.sleep").start()

    def rpc(self, method, params, *, server=None):
        response = (server or self.server)._dispatch({
            "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
        })
        self.assertNotIn("error", response, response)
        return response["result"]

    def call(self, name, arguments=None, *, server=None):
        return self.rpc("tools/call", {"name": name, "arguments": arguments or {}}, server=server)

    @staticmethod
    def observation(status="consistent"):
        return {
            "schema": 1, "status": status,
            "consistent": {"consistent": True, "changed": False, "unverified": None}[status],
            "complete": status != "unverified", "before": {"ok": True, "result": {"stable": True}},
            "after": state(), "context_after": context(), "native": native_observation(),
            "changed_fields": ["centre"] if status == "changed" else [],
            "read_errors": {"native": "Older adapter lacks native observation"} if status == "unverified" else {},
            "scope": "Finite observations do not establish pixel visibility or uninterrupted stability.",
        }

    def request_fixture(self, *, states=None, contexts=None, captured=None):
        states = iter(states or [state()] * 4)
        contexts = iter(contexts or [context()] * 3)
        captured = captured if captured is not None else capture_payload()

        def request(method, path, payload=None, query=None, version=None):
            self.assertEqual(method, "GET", "Observation must never submit a native write")
            self.assertEqual(version, "E3D21")
            if path == "/health":
                response = next(contexts)
            elif path == "/api/v1/view/state":
                response = next(states)
            elif path == "/api/v1/view/capture":
                self.assertEqual(query, {"width": "64", "height": "64"})
                response = captured
            else:
                self.fail("Unexpected request: " + path)
            if isinstance(response, Exception):
                raise response
            return response if path == "/api/v1/view/capture" else copy.deepcopy(response)

        return request

    def test_observe_tool_is_read_only_and_exposes_bounded_sampling(self):
        definitions = {item["name"]: item for item in self.rpc("tools/list", {})["tools"]}
        tool = definitions["e3d_observe_view"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        schema = tool["inputSchema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]), {"samples", "interval_ms", "include_clipping"})
        for name, default, minimum, maximum in (("samples", 3, 2, 10), ("interval_ms", 100, 50, 500)):
            prop = schema["properties"][name]
            self.assertEqual((prop["type"], prop["default"], prop["minimum"], prop["maximum"]),
                             ("integer", default, minimum, maximum))

    def test_observation_invalid_controls_never_reach_native_bridge(self):
        invalid = [{"samples": value} for value in (None, True, 1, 11, 2.0, "3")]
        invalid += [{"interval_ms": value} for value in (None, False, 49, 501, 100.0, "100")]
        invalid += [{"capture_after": True}, {"fit": "drawlist"}, {"dry_run": False}]
        with patch.object(BridgeClient, "_request") as native:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self.call("e3d_observe_view", arguments)["isError"])
        native.assert_not_called()

    def test_observation_runs_without_command_permission_and_keeps_raw_evidence(self):
        server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))
        evidence = {"ok": True, "version": "E3D21", "result": {
            "success": True, "stable": None, "complete": False,
            "states": [state(), {"ok": False, "error": "view closing"}],
            "context_before": context(), "context_after": context(),
            "changed_fields": [], "read_errors": {"state[1]": "view closing"}, "scope": "bounded samples",
        }}
        with patch.object(BridgeClient, "observe_view", return_value=evidence) as observe:
            response = self.call("e3d_observe_view", {"samples": 2, "interval_ms": 50}, server=server)
        observe.assert_called_once_with(2, 50)
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"], evidence)

    def test_stable_observation_samples_only_reads_and_preserves_all_states(self):
        samples = [state(), state(), state()]
        with patch.object(self.client, "_request", side_effect=self.request_fixture(states=samples)) as request:
            response = self.client.observe_view(3, 50, version="E3D21")
        result = response["result"]
        self.assertIs(result["stable"], True)
        self.assertIs(result["complete"], True)
        self.assertEqual(result["states"], samples)
        self.assertEqual(result["changed_fields"], [])
        self.assertEqual(result["read_errors"], {})
        self.assertFalse(result["scope"]["pixel_visibility_verified"])
        self.assertFalse(result["scope"]["render_completion_verified"])
        self.assertFalse(result["scope"]["future_stability_guaranteed"])
        self.assertEqual([item.args[1] for item in request.call_args_list],
                         ["/health", "/api/v1/view/state", "/api/v1/view/state", "/api/v1/view/state", "/health"])

    def test_equal_endpoints_do_not_hide_an_intermediate_camera_change(self):
        states = [state(), state(radius=600.0), state()]
        with patch.object(self.client, "_request", side_effect=self.request_fixture(states=states)):
            result = self.client.observe_view(3, 50, version="E3D21")["result"]
        self.assertIs(result["stable"], False)
        self.assertIs(result["complete"], True)
        self.assertIn("radius", json.dumps(result["changed_fields"]))
        self.assertEqual(result["states"], states)

    def test_observation_reports_small_motion_and_display_changes(self):
        changes = [
            {"centre": [12.00000001, 24.0, 36.0]}, {"direction": [2.0, 2.0, -2.0]},
            {"view_id": 12}, {"drawlist_id": 18}, {"perspective": True},
            {"drawlist_membership_fingerprint": "c" * 64},
            {"drawlist_visuals_fingerprint": "d" * 64},
        ]
        for change in changes:
            with self.subTest(change=change), patch.object(self.client, "_request", side_effect=self.request_fixture(
                    states=[state(), state(**change)])):
                result = self.client.observe_view(2, 50, version="E3D21")["result"]
            self.assertIs(result["stable"], False)
            self.assertIn(next(iter(change)), json.dumps(result["changed_fields"]))

    def test_session_or_build_change_prevents_stable_claim(self):
        for changed in (context(session_id="session-2"), context(bridge_build="build-2"), context(version="E3D30")):
            with self.subTest(changed=changed), patch.object(self.client, "_request", side_effect=self.request_fixture(
                    states=[state(), state()], contexts=[context(), changed])):
                result = self.client.observe_view(2, 50, version="E3D21")["result"]
            self.assertIs(result["stable"], False)
            self.assertTrue(result["changed_fields"])
            self.assertEqual(result["context_after"], changed)

    def test_partial_nonfinite_or_missing_evidence_never_claims_stable(self):
        incomplete = [state(partial=True, errors={"view_limits": "unavailable"}),
                      state(centre=[float("nan"), 24.0, 36.0]), state(direction=[0.0, 0.0, 0.0]),
                      state(drawlist_membership_fingerprint=""), state(view_id=True),
                      BridgeError("Observation timed out", transport_error=True)]
        for broken in incomplete:
            with self.subTest(broken=broken), patch.object(self.client, "_request", side_effect=self.request_fixture(
                    states=[state(), broken])):
                result = self.client.observe_view(2, 50, version="E3D21")["result"]
            self.assertIsNone(result["stable"])
            self.assertFalse(result["complete"])
            self.assertTrue(result["read_errors"])

    def test_disconnected_or_unidentified_context_is_not_stable(self):
        for broken in (context(connected=False), context(session_id=""), context(bridge_build=""), context(version="")):
            with self.subTest(broken=broken), patch.object(self.client, "_request", side_effect=self.request_fixture(
                    states=[state(), state()], contexts=[context(), broken])):
                result = self.client.observe_view(2, 50, version="E3D21")["result"]
            self.assertIsNot(result["stable"], True)
            self.assertFalse(result["complete"])
            self.assertTrue(result["read_errors"])

    def test_capture_preserves_native_image_and_reports_consistency(self):
        captured = capture_payload()
        original = copy.deepcopy(captured)
        with patch.object(self.client, "_request", side_effect=self.request_fixture(captured=captured)) as request:
            response = self.client.capture_view(64, 64, version="E3D21")
        observation = response["result"]["observation"]
        self.assertEqual(observation["schema"], 1)
        self.assertEqual(observation["status"], "consistent")
        self.assertIs(observation["consistent"], True)
        self.assertIs(observation["complete"], True)
        self.assertEqual(observation["native"], captured["result"]["native_capture_observation"])
        self.assertEqual({k: v for k, v in response["result"].items() if k != "observation"}, captured["result"])
        self.assertEqual(captured, original)
        self.assertEqual(sum(item.args[1] == "/api/v1/view/capture" for item in request.call_args_list), 1)

    def test_graphics_refresh_camera_drift_survives_a_later_matching_read(self):
        captured = capture_payload()
        captured["result"]["native_capture_observation"]["before_bitmap"] = camera(centre=[99.0, 24.0, 36.0])
        with patch.object(self.client, "_request", side_effect=self.request_fixture(captured=captured)):
            response = self.client.capture_view(64, 64, version="E3D21")
        observation = response["result"]["observation"]
        self.assertEqual(observation["status"], "changed")
        self.assertIs(observation["consistent"], False)
        self.assertIn("centre", json.dumps(observation["changed_fields"]))
        self.assertEqual(response["result"]["image_base64"], captured["result"]["image_base64"])

    def test_old_adapter_keeps_picture_without_fabricating_native_consistency(self):
        captured = capture_payload(native=False)
        with patch.object(self.client, "_request", side_effect=self.request_fixture(captured=captured)):
            response = self.client.capture_view(64, 64, version="E3D21")
        observation = response["result"]["observation"]
        self.assertEqual(observation["status"], "unverified")
        self.assertIsNone(observation["consistent"])
        self.assertFalse(observation["complete"])
        self.assertIsNone(observation["native"])
        self.assertTrue(observation["read_errors"])
        self.assertEqual(response["result"]["image_base64"], captured["result"]["image_base64"])

    def test_observation_failure_after_bitmap_keeps_image_and_diagnostic(self):
        captured = capture_payload()
        with patch.object(self.client, "_request", side_effect=self.request_fixture(
                captured=captured, states=[state(), state(), state(), BridgeError("View closed after capture")])):
            response = self.client.capture_view(64, 64, version="E3D21")
        observation = response["result"]["observation"]
        self.assertEqual(observation["status"], "unverified")
        self.assertIsNone(observation["consistent"])
        self.assertFalse(observation["complete"])
        self.assertIn("View closed after capture", json.dumps(observation["read_errors"]))
        self.assertEqual(response["result"]["image_base64"], captured["result"]["image_base64"])

    def test_capture_transport_failure_is_never_retried(self):
        with patch.object(self.client, "_request", side_effect=self.request_fixture(
                captured=BridgeError("Capture timed out", transport_error=True))) as request:
            with self.assertRaises(BridgeError):
                self.client.capture_view(64, 64, version="E3D21")
        self.assertEqual(sum(item.args[1] == "/api/v1/view/capture" for item in request.call_args_list), 1)

    def test_capture_and_receipt_preserve_observation_without_losing_successful_edit(self):
        operation = {"ok": True, "version": "E3D21", "result": {
            "success": True, "refno": "=1/2", "value": "updated", "dry_run": False,
        }}
        for status in ("consistent", "changed", "unverified"):
            captured = capture_payload()
            captured["result"]["observation"] = self.observation(status)
            original = copy.deepcopy(captured)
            with self.subTest(status=status), patch.object(BridgeClient, "capture_view", return_value=captured) as capture, \
                    patch.object(BridgeClient, "set_attribute", return_value=operation) as write, \
                    patch.object(BridgeClient, "fit_view") as fit, patch.object(BridgeClient, "restore_view") as restore, \
                    patch.object(BridgeClient, "set_view") as camera_write, \
                    patch.object(BridgeClient, "set_drawlist_visual") as drawlist_write:
                direct = self.call("e3d_capture_view", {"width": 64, "height": 64})
                receipt = self.call("e3d_set_attribute", {"refno": "=1/2", "attribute": "DESC", "value": "updated",
                    "dry_run": False, "capture_after": True, "capture_width": 64, "capture_height": 64})
            write.assert_called_once_with("=1/2", "DESC", "updated", False)
            self.assertEqual(capture.call_count, 2)
            for forbidden in (fit, restore, camera_write, drawlist_write):
                forbidden.assert_not_called()
            for response in (direct, receipt):
                self.assertFalse(response.get("isError", False), response)
                self.assertEqual(len([item for item in response["content"] if item["type"] == "image"]), 1)
                self.assertNotIn("image_base64", json.dumps(response["structuredContent"]))
                text = next(item["text"] for item in response["content"] if item["type"] == "text")
                self.assertEqual(json.loads(text), response["structuredContent"])
            self.assertEqual(direct["structuredContent"]["result"]["observation"], captured["result"]["observation"])
            metadata = receipt["structuredContent"]
            self.assertEqual(metadata["result"], operation["result"])
            self.assertFalse(metadata["receipt"]["operation_replayed"])
            self.assertEqual(metadata["receipt"]["capture"]["observation"], captured["result"]["observation"])
            self.assertEqual(metadata["receipt"]["capture"]["native_capture_observation"],
                             captured["result"]["native_capture_observation"])
            self.assertEqual(metadata["receipt"]["capture"]["status"], "captured")
            self.assertNotIn("outcome_uncertain", metadata)
            self.assertEqual(captured, original)

    def camera_receipt(self, operation_result, captured):
        operation = {"ok": True, "version": "E3D21", "result": {
            "success": True, "dry_run": False, "write_applied": True, **operation_result,
        }}
        with patch.object(BridgeClient, "set_view", return_value=operation) as write, \
                patch.object(BridgeClient, "capture_view", return_value=captured) as capture, \
                patch.object(BridgeClient, "fit_view") as fit, \
                patch.object(BridgeClient, "restore_view") as restore:
            response = self.call("e3d_set_view", {"preset": "isometric", "dry_run": False,
                "capture_after": True, "capture_width": 64, "capture_height": 64})
        write.assert_called_once()
        capture.assert_called_once_with(64, 64)
        fit.assert_not_called()
        restore.assert_not_called()
        self.assertFalse(response.get("isError", False), response)
        metadata = response["structuredContent"]
        self.assertEqual(metadata["result"], operation["result"])
        self.assertFalse(metadata["receipt"]["operation_replayed"])
        self.assertEqual(len([item for item in response["content"] if item["type"] == "image"]), 1)
        return metadata["receipt"]["capture"]["operation_camera_agreement"]

    def test_camera_receipt_matches_requested_core_and_native_readback(self):
        requested = {key: camera()[key] for key in (
            "centre", "direction", "radius", "perspective", "extra_twist_degrees",
        )}
        requested["direction"] = [2.0, 2.0, -2.0]
        requested["centre"] = [12.0000001, 24.0, 36.0]
        agreement = self.camera_receipt({"requested_camera": requested, "actual_camera": camera()}, capture_payload())
        self.assertIs(agreement["requested_matches_capture"], True)
        self.assertIs(agreement["readback_matches_capture"], True)

    def test_camera_agreement_uses_bitmap_time_not_later_matching_observation(self):
        captured = capture_payload()
        captured["result"]["native_capture_observation"]["before_bitmap"] = camera(radius=600.0)
        captured["result"]["observation"] = self.observation("changed")
        agreement = self.camera_receipt({"requested_camera": camera(), "after": camera()}, captured)
        self.assertIs(agreement["requested_matches_capture"], False)
        self.assertIs(agreement["readback_matches_capture"], False)

    def test_readback_identity_change_is_not_a_matching_camera_receipt(self):
        agreement = self.camera_receipt({"requested_camera": camera(), "actual_camera": camera(view_id=99)}, capture_payload())
        self.assertIs(agreement["requested_matches_capture"], True)
        self.assertIs(agreement["readback_matches_capture"], False)

    def test_explicit_null_actual_camera_does_not_fall_back_to_older_after(self):
        agreement = self.camera_receipt({"requested_camera": camera(), "actual_camera": None, "after": camera()}, capture_payload())
        self.assertIs(agreement["requested_matches_capture"], True)
        self.assertIsNone(agreement["readback_matches_capture"])

    def test_unavailable_or_invalid_bitmap_camera_never_claims_agreement(self):
        payloads = [capture_payload(native=False)]
        for broken in (None, camera(available=False), camera(radius=float("nan")), camera(radius=10 ** 400),
                       camera(centre=[True, 24.0, 36.0]), camera(direction=[0.0, 0.0, 0.0]),
                       camera(view_id=True), camera(view_id=-1), camera(perspective=0)):
            captured = capture_payload()
            captured["result"]["native_capture_observation"]["before_bitmap"] = broken
            payloads.append(captured)
        for captured in payloads:
            with self.subTest(native=captured["result"].get("native_capture_observation")):
                agreement = self.camera_receipt({"requested_camera": camera(), "actual_camera": camera()}, captured)
                self.assertIsNone(agreement["requested_matches_capture"])
                self.assertIsNone(agreement["readback_matches_capture"])

    def test_invalid_requested_camera_does_not_hide_valid_actual_readback(self):
        agreement = self.camera_receipt({"requested_camera": camera(radius=0), "actual_camera": camera()}, capture_payload())
        self.assertIsNone(agreement["requested_matches_capture"])
        self.assertIs(agreement["readback_matches_capture"], True)


if __name__ == "__main__":
    unittest.main()
