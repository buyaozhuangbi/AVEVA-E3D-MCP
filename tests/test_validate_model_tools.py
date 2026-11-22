from __future__ import annotations

import json
import sys
import unittest
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class ValidateModelToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=False)
        self.server = create_server(self.settings)

    def _rpc(self, method: str, params: dict, *, server=None) -> dict:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        output = StringIO()
        with patch("sys.stdout", output):
            (server or self.server).serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def _call(self, arguments: dict, *, server=None) -> dict:
        return self._rpc("tools/call", {"name": "e3d_validate_model", "arguments": arguments}, server=server)

    def test_schema_preserves_defaults_and_exposes_compact_read_only_inspection(self) -> None:
        tool = next(item for item in self._rpc("tools/list", {})["tools"]
                    if item["name"] == "e3d_validate_model")
        schema = tool["inputSchema"]
        self.assertEqual(set(schema["properties"]),
                         {"refno", "depth", "limit", "required_types", "include_snapshot"})
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["limit"],
                         {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200})
        self.assertEqual(schema["properties"]["depth"],
                         {"type": "integer", "minimum": 0, "maximum": 8, "default": 4})
        self.assertEqual(schema["properties"]["include_snapshot"], {"type": "boolean", "default": True})
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        self.assertIn("include_snapshot=false", tool["description"])
        self.assertIn("database lock", tool["description"])

    def test_default_call_keeps_snapshot_request_enabled(self) -> None:
        with patch.object(BridgeClient, "validate", return_value={"ok": True}) as validate:
            result = self._call({})
        self.assertFalse(result.get("isError", False), result)
        validate.assert_called_once_with("", 4, 200, [], include_snapshot=True)

    def test_explicit_scope_and_new_limit_forward_without_command_permission(self) -> None:
        arguments = {"refno": "=123/45", "depth": 8, "limit": 2000,
                     "required_types": ["EQUI", "CYLI"], "include_snapshot": False}
        with patch.object(BridgeClient, "validate", return_value={"ok": True}) as validate:
            result = self._call(arguments)
        self.assertFalse(result.get("isError", False), result)
        validate.assert_called_once_with("=123/45", 8, 2000, ["EQUI", "CYLI"], include_snapshot=False)

    def test_old_cap_and_lower_bound_remain_valid(self) -> None:
        with patch.object(BridgeClient, "validate", return_value={"ok": True}) as validate:
            for limit in (1, 500, 501):
                with self.subTest(limit=limit):
                    self.assertFalse(self._call({"limit": limit}).get("isError", False))
                    validate.assert_called_with("", 4, limit, [], include_snapshot=True)

    def test_invalid_arguments_fail_before_any_bridge_request(self) -> None:
        invalid = []
        for value in (0, -1, 2001, True, False, 2000.0, "2000", None):
            invalid.append({"limit": value})
        for value in (-1, 9, True, 4.0, "4", None):
            invalid.append({"depth": value})
        for value in (None, 0, 1, "false", "true", [], {}):
            invalid.append({"include_snapshot": value})
        for value in (None, "EQUI", {}, [None], [True], [1]):
            invalid.append({"required_types": value})
        for value in (None, True, 123, []):
            invalid.append({"refno": value})
        invalid.extend(({"includeSnapshot": False}, {"dry_run": True}, {"cursor": "legacy"}))
        with patch.object(BridgeClient, "validate") as validate:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self._call(arguments).get("isError"))
            validate.assert_not_called()

    def test_false_or_unknown_validation_is_data_and_preserves_compact_response(self) -> None:
        for valid in (False, None):
            payload = {"ok": True, "validation": {"valid": valid, "complete": False,
                       "checks_complete": False, "snapshot": None, "snapshot_included": False,
                       "snapshot_info": {"limit": 2000, "truncation_reasons": ["limit"]},
                       "issues": ["Bad dimension"] if valid is False else [],
                       "unverified_required_types": ["CYLI"]}}
            with self.subTest(valid=valid), patch.object(BridgeClient, "validate", return_value=payload):
                result = self._call({"limit": 2000, "include_snapshot": False})
            self.assertFalse(result.get("isError", False), result)
            self.assertEqual(result["structuredContent"], payload)
            self.assertEqual(json.loads(result["content"][0]["text"]), payload)

    def test_bridge_keeps_fifth_positional_version_and_keyword_only_snapshot_flag(self) -> None:
        client = BridgeClient(self.settings)
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.validate("/PUMP", 8, 2000, ["EQUI", "CYLI"], "E3D21", include_snapshot=False)
        request.assert_called_once_with("GET", "/api/v1/validate", query={
            "refno": "/PUMP", "depth": "8", "limit": "2000",
            "required_types": "EQUI,CYLI", "include_snapshot": "false",
        }, version="E3D21")
        with self.assertRaises(TypeError):
            client.validate("/PUMP", 8, 2000, [], "E3D21", False)

    def test_bridge_default_serializes_snapshot_true(self) -> None:
        client = BridgeClient(self.settings)
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.validate("/PUMP", 4, 200, [])
        self.assertEqual(request.call_args.kwargs["query"]["include_snapshot"], "true")

    def test_http_query_contains_encoded_scope_and_lowercase_boolean(self) -> None:
        client = BridgeClient(self.settings)
        response = BytesIO(b'{"ok":true,"validation":{"valid":null}}')
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            result = client.validate("/PUMP A", 8, 2000, ["EQUI", "CYLI"], include_snapshot=False)
        request = urlopen.call_args.args[0]
        url = urlparse(request.full_url)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(url.path, "/api/v1/validate")
        self.assertEqual(parse_qs(url.query), {
            "refno": ["/PUMP A"], "depth": ["8"], "limit": ["2000"],
            "required_types": ["EQUI,CYLI"], "include_snapshot": ["false"],
        })
        self.assertEqual(urlopen.call_args.kwargs["timeout"], self.settings.bridge_timeout)
        self.assertIsNone(result["validation"]["valid"])

    def test_bridge_rejects_invalid_snapshot_flags_in_mock_and_bridge_modes(self) -> None:
        for mode in ("mock", "bridge"):
            client = BridgeClient(Settings(mode=mode))
            with patch.object(client, "_request") as request:
                for value in (None, 0, 1, "false", [], {}):
                    with self.subTest(mode=mode, value=value), self.assertRaisesRegex(ValueError, "boolean"):
                        client.validate("/PUMP", 4, 200, [], include_snapshot=value)
                request.assert_not_called()

    def test_mock_does_not_claim_native_validation_or_snapshot(self) -> None:
        server = create_server(Settings(mode="mock", allow_commands=False))
        for include_snapshot in (True, False):
            with self.subTest(include_snapshot=include_snapshot):
                result = self._call({"include_snapshot": include_snapshot}, server=server)
                self.assertFalse(result.get("isError", False), result)
                validation = result["structuredContent"]["validation"]
                self.assertIsNone(validation["valid"])
                self.assertIsNone(validation["checked_elements_valid"])
                self.assertIsNone(validation["snapshot"])
                self.assertFalse(validation["available"])
                self.assertFalse(validation["complete"])
                self.assertFalse(validation["checks_complete"])
                self.assertFalse(validation["snapshot_included"])
                self.assertEqual(validation["requested_include_snapshot"], include_snapshot)
                self.assertEqual(validation["source"], "mock")
                self.assertTrue(validation["read_errors"])


if __name__ == "__main__":
    unittest.main()
