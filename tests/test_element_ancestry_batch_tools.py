"""Offline contract tests for the bounded native ancestry batch tool."""
from __future__ import annotations

import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


REFNOS = ["=2000000001/2617", "=2000000002/42"]
ROUTE = "/api/v1/element/ancestry-batch"


def receipt():
    return {"ok": True, "version": "E3D21", "result": {
        "success": True, "complete": True, "read_only": True,
        "refnos": REFNOS, "max_depth": 8,
        "results": [
            {"index": 0, "requested_refno": REFNOS[0], "status": "complete",
             "success": True, "complete": True, "ancestors": [], "read_errors": []},
            {"index": 1, "requested_refno": REFNOS[1], "status": "complete",
             "success": True, "complete": True, "ancestors": [], "read_errors": []},
        ],
        "mdb_databases": [], "mdb_database_count": 0, "mdb_scan_complete": True,
        "mdb_membership_complete": True, "world_frame": None,
        "context": {"ce_unchanged": True, "identity_unchanged": True},
        "read_errors": [], "native_database_fault": None,
    }}


class ElementAncestryBatchToolTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", bridge_url="http://127.0.0.1:8766",
                                 allow_commands=False)
        self.server = create_server(self.settings)

    def rpc(self, method, params):
        return self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})

    def call(self, args):
        return self.rpc("tools/call", {"name": "e3d_element_ancestry_batch", "arguments": args})["result"]

    def test_schema_and_description_are_bounded_read_only(self):
        definitions = self.rpc("tools/list", {})["result"]["tools"]
        tool = next(item for item in definitions if item["name"] == "e3d_element_ancestry_batch")
        self.assertEqual(tool["annotations"], {
            "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
        })
        schema = tool["inputSchema"]
        self.assertEqual(schema["required"], ["refnos"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]), {"refnos", "max_depth"})
        self.assertEqual(schema["properties"]["refnos"]["minItems"], 1)
        self.assertEqual(schema["properties"]["refnos"]["maxItems"], 16)
        self.assertTrue(schema["properties"]["refnos"]["uniqueItems"])
        self.assertEqual(schema["properties"]["max_depth"]["default"], 32)
        for token in ("one UI callback", "results", "not_started", "mdb_membership_complete", "no fallback"):
            self.assertIn(token, tool["description"])

    def test_valid_batch_forwards_one_ordered_request_and_native_payload_unchanged(self):
        native = receipt()
        with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(native).encode())) as urlopen:
            result = self.call({"refnos": REFNOS, "max_depth": 8})
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"], native)
        urlopen.assert_called_once()
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8766" + ROUTE)
        self.assertEqual(json.loads(request.data), {"refnos": REFNOS, "max_depth": 8})

    def test_invalid_batch_never_dispatches(self):
        invalid = [
            {}, {"refnos": []}, {"refnos": ["=1/0"] * 2},
            {"refnos": ["=01/2"]}, {"refnos": ["/PUMP"]},
            {"refnos": ["=1/2"], "max_depth": True},
            {"refnos": ["=1/2"], "max_depth": 0},
            {"refnos": ["=1/2"], "max_depth": 65},
            {"refnos": ["=2147483648/0"]},
        ]
        with patch.object(BridgeClient, "element_ancestry_batch") as batch, \
                patch.object(BridgeClient, "_request") as request:
            for args in invalid:
                with self.subTest(args=args):
                    self.assertTrue(self.call(args)["isError"])
        batch.assert_not_called()
        request.assert_not_called()

    def test_mock_and_other_versions_return_unavailable_without_rows(self):
        for mode, version, code in (("mock", "E3D21", "native_unavailable"),
                                    ("bridge", "E3D31", "unsupported_version")):
            server = create_server(Settings(mode=mode, version=version, allow_commands=False))
            with self.subTest(mode=mode, version=version), \
                    patch.object(BridgeClient, "_request") as request:
                result = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "e3d_element_ancestry_batch", "arguments": {"refnos": REFNOS}}})["result"]
            request.assert_not_called()
            self.assertTrue(result["isError"])
            native = result["structuredContent"]["result"]
            self.assertEqual(native["refnos"], REFNOS)
            self.assertEqual(native["max_depth"], 32)
            self.assertFalse(native["success"])
            self.assertFalse(native["complete"])
            self.assertFalse(native["mdb_membership_complete"])
            self.assertEqual(native["read_errors"][0]["code"], code)
            self.assertNotIn("results", native)
            self.assertNotIn("mdb_databases", native)

    def test_native_partial_failure_is_transparent_and_transport_is_single_shot(self):
        native = receipt()
        native["ok"] = False
        native["error"] = "native batch ancestry read failed"
        native["result"].update(success=False, complete=False, mdb_membership_complete=False)
        native["result"]["results"][1] = {"index": 1, "requested_refno": REFNOS[1],
            "status": "not_started", "success": False, "complete": False, "read_errors": []}
        native["result"]["read_errors"] = [{"path": "results[0]", "code": "native_database_fault", "message": "fault"}]
        with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(native).encode())) as urlopen:
            result = self.call({"refnos": REFNOS})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["result"], native["result"])
        urlopen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
