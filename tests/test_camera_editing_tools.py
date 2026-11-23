"""Exercise the numeric camera MCP boundary, transport and picture receipt."""
import copy
import json
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server
from test_capture_camera_consistency import camera, capture_payload


class CameraEditingToolTests(unittest.TestCase):
    def setUp(self):
        self.server = create_server(Settings(mode="bridge", allow_commands=True))
        self.pose = {"centre": [12, 24, 36], "direction": [2, 2, -2], "radius": 500,
                     "perspective": False, "extra_twist_degrees": 0}
        self.token = "ab" * 32

    def rpc(self, method, params, server=None):
        out = StringIO()
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        with patch("sys.stdout", out):
            (server or self.server).serve([request + "\n"])
        return json.loads(out.getvalue())["result"]

    def call(self, args, server=None):
        return self.rpc("tools/call", {"name": "e3d_set_camera", "arguments": args}, server)

    @staticmethod
    def result(**kwargs):
        return {"ok": True, "version": "E3D21", "result": {"success": True, **kwargs}}

    def test_discovery_required_fields_preview_revision_and_capture(self):
        catalog = self.rpc("tools/list", {})["tools"]
        self.assertEqual(len(catalog), 119)
        self.assertEqual(sum("capture_after" in t["inputSchema"]["properties"] for t in catalog), 19)
        tool = next(t for t in catalog if t["name"] == "e3d_set_camera")
        schema = tool["inputSchema"]
        self.assertEqual(set(schema["required"]), set(self.pose))
        self.assertFalse(schema["additionalProperties"])
        self.assertTrue(schema["properties"]["dry_run"]["default"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        self.assertFalse(tool["annotations"]["readOnlyHint"])
        self.assertEqual(schema["allOf"][0]["then"], {"required": ["expected_revision"]})

    def test_preview_forwards_literal_direction_without_permission_or_capture(self):
        server = create_server(Settings(mode="bridge", allow_commands=False))
        with patch.object(BridgeClient, "set_camera", return_value=self.result(dry_run=True)) as write, \
                patch.object(BridgeClient, "capture_view") as capture:
            response = self.call({**self.pose, "capture_after": True}, server)
        write.assert_called_once_with([12., 24., 36.], [2., 2., -2.], 500., False, 0., dry_run=True)
        capture.assert_not_called()
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "dry_run")

    def test_execution_requires_revision_and_commands(self):
        denied = create_server(Settings(mode="bridge", allow_commands=False))
        with patch.object(BridgeClient, "set_camera") as write:
            self.assertTrue(self.call({**self.pose, "dry_run": False})["isError"])
            self.assertTrue(self.call({**self.pose, "dry_run": False, "expected_revision": self.token}, denied)["isError"])
        write.assert_not_called()
        with patch.object(BridgeClient, "set_camera", return_value=self.result(dry_run=False)) as write:
            self.assertFalse(self.call({**self.pose, "dry_run": False, "expected_revision": self.token.upper()}).get("isError", False))
        self.assertEqual(write.call_args.kwargs, {"dry_run": False, "expected_revision": self.token})

    def test_missing_and_unknown_fields_never_dispatch(self):
        invalid = [{k:v for k,v in self.pose.items() if k != missing} for missing in self.pose]
        invalid += [{**self.pose, name: True} for name in ("fit", "refno", "shaded", "restore", "command")]
        with patch.object(BridgeClient, "set_camera") as write:
            for args in invalid:
                with self.subTest(args=args):
                    self.assertTrue(self.call(args)["isError"])
        write.assert_not_called()

    def test_nonfinite_bool_overflow_vectors_and_invalid_controls_are_rejected(self):
        invalid = []
        for name in ("centre", "direction"):
            invalid += [{**self.pose, name: v} for v in (None, "1 2 3", [], [1, 2], [1, 2, 3, 4],
                         [True, 1, 2], [1, None, 2], [1, float("nan"), 2], [1, float("inf"), 2], [10**400, 1, 2])]
        invalid += [{**self.pose, "centre": [3.5e38, 0, 0]}, {**self.pose, "direction": [0, 0, -0.]}]
        for field in ("radius", "extra_twist_degrees"):
            invalid += [{**self.pose, field: v} for v in (True, None, "1", float("inf"), float("nan"), 10**400)]
        invalid += [{**self.pose, "radius": v} for v in (0, -1)]
        for field in ("perspective", "dry_run"):
            invalid += [{**self.pose, field: v} for v in (0, 1, None, "false")]
        invalid += [{**self.pose, "expected_revision": v} for v in (None, 1, "", "ab", "g"*64, " " + self.token)]
        invalid += [{**self.pose, "capture_after": "true"}, {**self.pose, "capture_width": 63}, {**self.pose, "capture_height": True}]
        with patch.object(BridgeClient, "set_camera") as write, patch.object(BridgeClient, "capture_view") as capture:
            for args in invalid:
                with self.subTest(args=args):
                    self.assertTrue(self.call(args)["isError"])
        write.assert_not_called()
        capture.assert_not_called()

    def test_finite_direction_extremes_reach_native_normalization_unchanged(self):
        with patch.object(BridgeClient, "set_camera", return_value=self.result(dry_run=True)) as write:
            for direction in ([1e308, 1e308, -1e308], [5e-324, 0, 0]):
                self.assertFalse(self.call({**self.pose, "direction": direction}).get("isError", False))
                self.assertEqual(write.call_args.args[1], direction)
            self.assertFalse(self.call({**self.pose, "radius": 1e100, "extra_twist_degrees": 725.}).get("isError", False))

    def test_native_rejection_and_partial_result_remain_intact_without_capture(self):
        failed = self.result(success=False, revision_conflict=True, write_attempted=False,
                             actual_camera=None, requested_camera=self.pose, errors=["changed"])
        with patch.object(BridgeClient, "set_camera", return_value=failed) as write, \
                patch.object(BridgeClient, "capture_view") as capture:
            response = self.call({**self.pose, "dry_run": False, "expected_revision": self.token, "capture_after": True})
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"]["result"], failed["result"])
        write.assert_called_once()
        capture.assert_not_called()

    def test_receipt_compares_requested_and_readback_at_bitmap_and_never_replays(self):
        operation = self.result(dry_run=False, requested_camera=camera(), actual_camera=camera())
        for bitmap, expected in ((camera(), True), (camera(radius=900), False)):
            captured = capture_payload()
            captured["result"]["native_capture_observation"]["before_bitmap"] = bitmap
            with patch.object(BridgeClient, "set_camera", return_value=operation) as write, \
                    patch.object(BridgeClient, "capture_view", return_value=captured) as capture:
                response = self.call({**self.pose, "dry_run": False, "expected_revision": self.token,
                                      "capture_after": True, "capture_width": 64, "capture_height": 64})
            write.assert_called_once()
            capture.assert_called_once_with(64, 64)
            agreement = response["structuredContent"]["receipt"]["capture"]["operation_camera_agreement"]
            self.assertIs(agreement["requested_matches_capture"], expected)
            self.assertIs(agreement["readback_matches_capture"], expected)
            self.assertEqual([c["type"] for c in response["content"]], ["text", "image"])
            self.assertEqual(response["structuredContent"]["result"], operation["result"])

    def test_capture_failure_retains_confirmed_operation(self):
        operation = self.result(dry_run=False, camera_matches_request=True)
        with patch.object(BridgeClient, "set_camera", return_value=operation) as write, \
                patch.object(BridgeClient, "capture_view", side_effect=BridgeError("bitmap failed")) as capture:
            response = self.call({**self.pose, "dry_run": False, "expected_revision": self.token, "capture_after": True})
        write.assert_called_once()
        capture.assert_called_once()
        self.assertFalse(response.get("isError", False))
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["status"], "failed")

    def test_unknown_outcome_is_not_retried_or_captured(self):
        for error in (BridgeError("timeout", transport_error=True), BridgeError("invalid", response_invalid=True)):
            for capture_after in (False, True):
                with patch.object(BridgeClient, "set_camera", side_effect=error) as write, patch.object(BridgeClient, "capture_view") as capture:
                    response = self.call({**self.pose, "dry_run": False, "expected_revision": self.token, "capture_after": capture_after})
                self.assertTrue(response["structuredContent"]["outcome_uncertain"])
                self.assertFalse(response["structuredContent"]["retry_safe"])
                write.assert_called_once()
                capture.assert_not_called()

    def test_mock_cannot_invent_preview_revision_or_success(self):
        server = create_server(Settings(mode="mock", allow_commands=True))
        with patch.object(BridgeClient, "_request") as request, patch.object(BridgeClient, "capture_view") as capture:
            response = self.call({**self.pose, "capture_after": True}, server)
        self.assertTrue(response["isError"])
        result = response["structuredContent"]["result"]
        self.assertFalse(result["available"])
        self.assertFalse(result["write_attempted"])
        self.assertIsNone(result["revision"])
        request.assert_not_called()
        capture.assert_not_called()

    def test_bridge_route_version_payload_and_single_http_dispatch(self):
        client = BridgeClient(Settings(mode="bridge"))
        arguments = (self.pose["centre"], self.pose["direction"], 500, False, 0)
        payload = self.result(success=False, actual_camera=None)
        with patch.object(client, "_request", return_value=payload) as request:
            self.assertEqual(client.set_camera(*arguments, version="E3D21"), payload)
            request.assert_called_once_with("POST", "/api/v1/view/camera/set", {**self.pose, "dry_run": True}, version="E3D21")
        with patch("urllib.request.urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            self.assertEqual(client.set_camera(*arguments, dry_run=False, expected_revision=self.token), payload)
        request.assert_called_once()
        sent = request.call_args.args[0]
        self.assertEqual(sent.get_method(), "POST")
        self.assertTrue(sent.full_url.endswith("/api/v1/view/camera/set"))
        self.assertEqual(json.loads(sent.data), {**self.pose, "dry_run": False, "expected_revision": self.token})

    def test_http_timeout_no_retry_recovery_or_cancel(self):
        client = BridgeClient(Settings(mode="bridge"))
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timeout")) as request, \
                patch.object(client, "_recover_command_timeout") as recover, patch.object(client, "cancel_command") as cancel:
            with self.assertRaises(BridgeError):
                client.set_camera([1, 2, 3], [1, 2, 3], 500, False, 0, dry_run=False, expected_revision=self.token)
        request.assert_called_once()
        recover.assert_not_called()
        cancel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
