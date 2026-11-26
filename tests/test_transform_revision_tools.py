from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class TransformRevisionToolsTests(unittest.TestCase):
    def _call(self, arguments: dict, *, allow_commands: bool = False) -> dict:
        server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=allow_commands))
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "e3d_set_transform", "arguments": arguments}})
        output = io.StringIO()
        with patch("sys.stdout", output):
            server.serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def test_schema_requires_revision_only_for_explicit_execution(self) -> None:
        server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=True))
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        output = io.StringIO()
        with patch("sys.stdout", output):
            server.serve([request + "\n"])
        tools = json.loads(output.getvalue())["result"]["tools"]
        schema = next(tool["inputSchema"] for tool in tools if tool["name"] == "e3d_set_transform")
        revision = schema["properties"]["expected_revision"]
        self.assertEqual(revision["pattern"], r"^[0-9a-fA-F]{64}$")
        self.assertEqual(revision["minLength"], 64)
        self.assertEqual(revision["maxLength"], 64)
        self.assertEqual(schema["allOf"], [{
            "if": {"properties": {"dry_run": {"const": False}}, "required": ["dry_run"]},
            "then": {"required": ["expected_revision"]},
        }])

    def test_write_missing_or_malformed_revision_never_reaches_bridge(self) -> None:
        valid_request = {"refno": "=1/2", "position": [1, 2, 3], "dry_run": False}
        malformed = ["", "g" * 64, "a" * 63, "a" * 64 + "0", " " + "a" * 64]
        with patch.object(BridgeClient, "set_transform") as transform:
            missing = self._call(valid_request, allow_commands=True)
            self.assertTrue(missing["isError"])
            for revision in malformed:
                with self.subTest(revision=revision):
                    result = self._call({**valid_request, "expected_revision": revision}, allow_commands=True)
                    self.assertTrue(result["isError"])
            transform.assert_not_called()

    def test_write_normalizes_revision_and_passes_it_by_keyword(self) -> None:
        native = {"ok": True, "version": "E3D21", "result": {
            "success": True, "dry_run": False, "write_attempted": True,
            "write_applied": True, "readback_verified": True,
        }}
        with patch.object(BridgeClient, "set_transform", return_value=native) as transform:
            result = self._call({"refno": "=1/2", "position": [1, 2, 3], "dry_run": False,
                                 "expected_revision": "A" * 64}, allow_commands=True)
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"], native)
        transform.assert_called_once_with("=1/2", [1.0, 2.0, 3.0], None, None, "OWNER", False,
                                          expected_revision="a" * 64)

    def test_preview_can_omit_revision_and_preserves_native_preview_receipt(self) -> None:
        native = {"ok": True, "version": "E3D21", "result": {
            "success": True, "dry_run": True, "revision": "b" * 64,
            "submitted": {"position": [1, 2, 3], "orientation": None},
        }}
        with patch.object(BridgeClient, "set_transform", return_value=native) as transform:
            result = self._call({"refno": "=1/2", "position": [1, 2, 3]})
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"], native)
        transform.assert_called_once_with("=1/2", [1.0, 2.0, 3.0], None, None, "OWNER", True)

    def test_preview_optional_token_is_normalized_and_forwarded(self) -> None:
        native = {"ok": True, "version": "E3D21", "result": {"success": True, "dry_run": True}}
        with patch.object(BridgeClient, "set_transform", return_value=native) as transform:
            result = self._call({"refno": "=1/2", "position": [1, 2, 3],
                                 "expected_revision": "C" * 64})
        self.assertFalse(result.get("isError", False), result)
        transform.assert_called_once_with("=1/2", [1.0, 2.0, 3.0], None, None, "OWNER", True,
                                          expected_revision="c" * 64)

    def test_bridge_posts_revisioned_payload_only_to_new_route(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        preview = {"ok": True, "version": "E3D21", "result": {"success": True, "dry_run": True,
                                                                     "revision": "d" * 64}}
        with patch.object(client, "_request", return_value=preview) as request:
            result = client.set_transform("=1/2", [1.0, 2.0, 3.0], None, None, "OWNER", True)
        self.assertEqual(result, preview)
        request.assert_called_once_with("POST", "/api/v1/element/transform/revisioned", {
            "refno": "=1/2", "position": [1.0, 2.0, 3.0], "x_axis": None, "y_axis": None,
            "wrt": "OWNER", "dry_run": True,
        }, version=None)

        with patch.object(client, "_request", return_value=preview) as request:
            client.set_transform("=1/2", [1.0, 2.0, 3.0], None, None, "OWNER", True,
                                 expected_revision="E" * 64)
        self.assertEqual(request.call_args.args[1], "/api/v1/element/transform/revisioned")
        self.assertEqual(request.call_args.args[2]["expected_revision"], "e" * 64)

    def test_bridge_rejects_missing_or_invalid_execution_revision_before_request(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch.object(client, "_request") as request:
            with self.assertRaisesRegex(ValueError, "expected_revision"):
                client.set_transform("=1/2", [1.0, 2.0, 3.0], None, None, "OWNER", False)
            with self.assertRaisesRegex(ValueError, "expected_revision"):
                client.set_transform("=1/2", [1.0, 2.0, 3.0], None, None, "OWNER", False,
                                     expected_revision="z" * 64)
        request.assert_not_called()

    def test_old_bridge_404_fails_without_legacy_fallback(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        attempted_urls = []

        def old_bridge_404(request, timeout=None):
            attempted_urls.append(request.full_url)
            raise HTTPError(request.full_url, 404, "Not Found", {},
                            io.BytesIO(b'{"ok":false,"error":"revisioned transform route unavailable"}'))

        with patch("urllib.request.urlopen", side_effect=old_bridge_404):
            with self.assertRaises(BridgeError) as caught:
                client.set_transform("=1/2", [1.0, 2.0, 3.0], None, None, "OWNER", False,
                                     expected_revision="f" * 64)
        self.assertEqual(caught.exception.http_status, 404)
        self.assertFalse(caught.exception.outcome_uncertain)
        self.assertEqual(len(attempted_urls), 1)
        self.assertTrue(attempted_urls[0].endswith("/api/v1/element/transform/revisioned"))
        self.assertNotIn("/api/v1/element/transform?", attempted_urls[0])

    def test_stale_http_409_preserves_native_receipt_and_known_no_write_outcome(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        native = {"success": False, "dry_run": False, "revision_conflict": True,
                  "write_attempted": False, "write_applied": False, "outcome_uncertain": False}
        envelope = json.dumps({"ok": False, "error": "Transform preview is stale.", "result": native}).encode()

        def stale_409(request, timeout=None):
            raise HTTPError(request.full_url, 409, "Conflict", {}, io.BytesIO(envelope))

        with patch("urllib.request.urlopen", side_effect=stale_409):
            with self.assertRaises(BridgeError) as caught:
                client.set_transform("=1/2", [1.0, 2.0, 3.0], None, None, "OWNER", False,
                                     expected_revision="a" * 64)
        self.assertEqual(caught.exception.http_status, 409)
        self.assertFalse(caught.exception.outcome_uncertain)
        self.assertEqual(caught.exception.diagnostics["result"], native)

    def test_stale_json_failure_preserves_native_receipt(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        native = {"success": False, "dry_run": False, "revision_conflict": True,
                  "write_attempted": False, "write_applied": False}
        envelope = {"ok": False, "error": "Transform preview is stale.", "result": native}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(envelope).encode())):
            with self.assertRaises(BridgeError) as caught:
                client.set_transform("=1/2", [1.0, 2.0, 3.0], None, None, "OWNER", False,
                                     expected_revision="a" * 64)
        self.assertFalse(caught.exception.outcome_uncertain)
        self.assertEqual(caught.exception.diagnostics["result"], native)


if __name__ == "__main__":
    unittest.main()
