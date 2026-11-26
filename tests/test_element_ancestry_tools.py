"""Offline RPC and HTTP contracts for an explicit native element ancestry read."""
from __future__ import annotations

import copy
import json
import sys
import unittest
import urllib.error
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

from catalog_compatibility import CURRENT_MOCK_TOOL_COUNT

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.element_ancestry import element_ancestry
from aveva_e3d_mcp.tools import create_server


REFNO = "=2000000001/2617"
ROUTE = "/api/v1/element/ancestry"


def ancestry_receipt():
    # Deliberately different native observations: neither reference spelling
    # supplies Db.Number or Db.ExtractNumber, even when a raw read is unknown.
    return {"ok": True, "version": "E3D21", "session_id": "native-session", "result": {
        "success": True, "available": True, "complete": True, "read_only": True,
        "single_dispatch": True, "requested_refno": REFNO, "max_depth": 32,
        "reached_world": True, "truncated": False, "mdb_membership_complete": True,
        "ancestors": [
            {"depth": 0, "raw_ref": "2000000001 2617", "raw_refno": "2000000001 2617",
             "formatted_refno": REFNO, "type": "EQUI", "name": "/PUMP",
             "database": {"number": 12345, "extract_number": 1, "name": "/TEST/DESIGN",
                          "type": "Design", "is_primary": True, "is_extract": False},
             "in_current_mdb": True},
            {"depth": 1, "raw_ref": None, "raw_refno": None, "formatted_refno": "=2000000002/0",
             "type": "WORLD", "name": None,
             "database": {"number": None, "extract_number": None, "name": None, "type": "Design"},
             "in_current_mdb": None},
        ],
        "world": {"raw_ref": "2000000002 0", "raw_refno": "2000000002 0",
                  "formatted_refno": "=2000000002/0", "type": "WORLD"},
        "mdb_databases": [{"number": 12345, "extract_number": 1, "type": "Design",
                           "is_primary": True, "is_extract": False}],
        "context": {"project_before": "TEST", "project_after": "TEST", "mdb_before": "MDB",
                    "mdb_after": "MDB", "ce_before": REFNO, "ce_after": REFNO,
                    "identity_unchanged": True, "ce_unchanged": True},
        "read_errors": [],
    }}


class ElementAncestryToolTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", bridge_url="http://127.0.0.1:8766",
                                 allow_commands=False)
        self.server = create_server(self.settings)

    def rpc(self, method, params, server=None):
        return (server or self.server)._dispatch({"jsonrpc": "2.0", "id": 1,
                                                 "method": method, "params": params})

    def call(self, args, server=None):
        return self.rpc("tools/call", {"name": "e3d_element_ancestry", "arguments": args}, server)["result"]

    def test_catalog_and_initialize_describe_bounded_read_only_native_identity(self):
        definitions = self.rpc("tools/list", {})["result"]["tools"]
        self.assertEqual(len(definitions), CURRENT_MOCK_TOOL_COUNT)
        tools = [item for item in definitions if item["name"] == "e3d_element_ancestry"]
        self.assertEqual(len(tools), 1)
        tool = tools[0]
        self.assertEqual(tool["annotations"], {
            "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
        })
        schema = tool["inputSchema"]
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["required"], ["refno"])
        self.assertEqual(set(schema["properties"]), {"refno", "max_depth"})
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["refno"]["type"], "string")
        self.assertEqual(schema["properties"]["max_depth"]["type"], "integer")
        self.assertEqual(schema["properties"]["max_depth"]["minimum"], 1)
        self.assertEqual(schema["properties"]["max_depth"]["maximum"], 64)
        self.assertEqual(schema["properties"]["max_depth"]["default"], 32)
        for token in ("OWNER", "raw_ref/raw_refno", "formatted_refno", "Number/ExtractNumber", "current MDB",
                      "depth zero", "complete", "reached_world", "truncated", "mdb_membership_complete",
                      "context", "read_errors", "no implicit CE"):
            self.assertIn(token, tool["description"])
        initialization = self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "element-ancestry-test", "version": "1.0"}})["result"]
        self.assertIn("e3d_element_ancestry", initialization["instructions"])

    def test_missing_implicit_noncanonical_non_ascii_and_out_of_range_refnos_never_dispatch(self):
        invalid = [{}]
        invalid += [{"refno": value} for value in (
            None, True, False, 12, {}, [], "", " ", "CE", "ce", "OWNER", "WORLD", "/PUMP", "/WORLD", "/",
            "=", "=1", "=1/2/3", "=x/2", "=1/x", "=0/2", "=1/-1", "=-1/2", "=+1/2", "=1/+2",
            "=01/2", "=1/02", "=00/0", "=1/00", "=2147483648/2", "=1/2147483648",
            "=1.0/2", "=1/2.0", "=\u0661/2", "=1/\u0662", "=\uff11/2", "=1/\uff12",
            " =1/2", "=1/2 ", "\t=1/2", "=1/2\n", "=1/2\r\n", "=1/2\x00", "=1/2\x7f",
            "=1/ 2", "=1\u00a0/2", "=1/2;DELETE CE", "=1/" + "9" * 5000,
        )]
        with patch.object(BridgeClient, "element_ancestry") as ancestry, patch.object(BridgeClient, "_request") as request:
            for args in invalid:
                with self.subTest(args=str(args)[:100]):
                    self.assertTrue(self.call(args)["isError"])
        ancestry.assert_not_called()
        request.assert_not_called()

    def test_depth_requires_strict_integer_one_through_64_before_dispatch(self):
        with patch.object(BridgeClient, "element_ancestry") as ancestry, patch.object(BridgeClient, "_request") as request:
            for depth in (None, True, False, 0, -1, 65, 2147483647, 1.0, 32.0, "1", "32", [], {}, float("nan"), float("inf")):
                with self.subTest(depth=depth):
                    self.assertTrue(self.call({"refno": REFNO, "max_depth": depth})["isError"])
        ancestry.assert_not_called()
        request.assert_not_called()

    def test_closed_arguments_reject_write_capture_and_routing_controls(self):
        with patch.object(BridgeClient, "element_ancestry") as ancestry, patch.object(BridgeClient, "capture_view") as capture:
            for key, value in (("dry_run", True), ("capture_after", True), ("capture_width", 1280),
                               ("include_owner", True), ("refnos", [REFNO]), ("version", "E3D21"),
                               ("limit", 32), ("expected_context", "a" * 64), ("secret-field", "private")):
                with self.subTest(key=key):
                    result = self.call({"refno": REFNO, key: value})
                    self.assertTrue(result["isError"])
                    self.assertNotIn(key, result["content"][0]["text"])
        ancestry.assert_not_called()
        capture.assert_not_called()

    def test_nonobject_rpc_arguments_are_protocol_errors_without_native_read(self):
        with patch.object(BridgeClient, "element_ancestry") as ancestry:
            for args in (None, True, 12, [], REFNO):
                with self.subTest(args=args):
                    result = self.rpc("tools/call", {"name": "e3d_element_ancestry", "arguments": args})
                    self.assertEqual(result["error"]["code"], -32602)
        ancestry.assert_not_called()

    def test_direct_handler_rejects_unknown_or_missing_arguments_before_bridge(self):
        bridge = BridgeClient(self.settings)
        with patch.object(bridge, "element_ancestry") as ancestry:
            for args in (None, [], {}, {"max_depth": 32}, {"refno": REFNO, "unknown": True},
                         {"refno": " =1/2"}, {"refno": REFNO, "max_depth": True}):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    element_ancestry(bridge, args)
        ancestry.assert_not_called()

    def test_default_depth_posts_once_without_command_permission_or_followup_reads(self):
        native = ancestry_receipt()
        with patch.object(BridgeClient, "_request", return_value=native) as request, \
                patch.object(BridgeClient, "command") as command, patch.object(BridgeClient, "cancel_command") as cancel, \
                patch.object(BridgeClient, "capture_view") as capture, patch.object(BridgeClient, "read_attributes") as attributes, \
                patch.object(BridgeClient, "project") as project:
            result = self.call({"refno": REFNO})
        request.assert_called_once_with("POST", ROUTE, {"refno": REFNO, "max_depth": 32}, version="E3D21")
        self.assertEqual(result["structuredContent"], native)
        self.assertFalse(result.get("isError", False), result)
        for operation in (command, cancel, capture, attributes, project):
            operation.assert_not_called()

    def test_canonical_numeric_and_depth_boundaries_forward_without_rewriting(self):
        for refno, depth in (("=1/0", 1), ("=2147483647/2147483647", 64), (REFNO, 32)):
            with self.subTest(refno=refno, depth=depth), \
                    patch.object(BridgeClient, "element_ancestry", return_value={"ok": True}) as ancestry:
                result = self.call({"refno": refno, "max_depth": depth})
            self.assertFalse(result.get("isError", False))
            ancestry.assert_called_once_with(refno=refno, max_depth=depth)

    def test_direct_bridge_validates_canonical_refno_and_depth_before_request(self):
        client = BridgeClient(self.settings)
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.element_ancestry("=2147483647/0", 64, version="E3D21")
            request.assert_called_once_with("POST", ROUTE, {"refno": "=2147483647/0", "max_depth": 64}, version="E3D21")
            request.reset_mock()
            for refno, depth in (("/PUMP", 32), ("CE", 32), (None, 32), (" =1/2 ", 32), ("=01/2", 32),
                                 ("=1/02", 32), ("=0/0", 32), ("=\u0661/2", 32), ("=1/2147483648", 32),
                                 ("=1/2", True), ("=1/2", 1.0), ("=1/2", 0), ("=1/2", 65)):
                with self.subTest(refno=refno, depth=depth), self.assertRaises(ValueError):
                    client.element_ancestry(refno, depth)
            with self.assertRaises(ValueError):
                client.element_ancestry(REFNO, version="E3D31")
        request.assert_not_called()

    def test_http_posts_json_only_to_the_assigned_instance(self):
        native = ancestry_receipt()
        with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(native).encode())) as urlopen:
            result = BridgeClient(self.settings).element_ancestry(REFNO, 8)
        urlopen.assert_called_once()
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "http://127.0.0.1:8766" + ROUTE)
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(request.data), {"refno": REFNO, "max_depth": 8})
        self.assertEqual(urlopen.call_args.kwargs["timeout"], self.settings.bridge_timeout)
        self.assertEqual(result, native)

    def test_raw_formatted_nullable_database_identity_and_context_are_preserved(self):
        native = ancestry_receipt()
        original = copy.deepcopy(native)
        with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(native).encode())):
            result = self.call({"refno": REFNO})
        self.assertEqual(result["structuredContent"], original)
        self.assertEqual(json.loads(result["content"][0]["text"]), original)
        self.assertEqual(native, original)
        target, world = result["structuredContent"]["result"]["ancestors"]
        self.assertNotEqual(target["raw_refno"], target["formatted_refno"])
        self.assertNotEqual(target["database"]["number"], 2000000001)
        self.assertIsNone(world["raw_ref"])
        self.assertIsNone(world["raw_refno"])
        self.assertIsNone(world["database"]["number"])
        self.assertIsNone(world["database"]["extract_number"])
        self.assertIsNone(world["in_current_mdb"])
        for field in ("can_write", "permission_granted", "database_locked", "coordinate_frames_equivalent"):
            self.assertNotIn(field, json.dumps(result))

    def test_http_native_version_is_never_filled_from_configured_version(self):
        for value in ("missing", None, "", "E3D31", "native-build-version"):
            native = ancestry_receipt()
            if value == "missing":
                native.pop("version")
            else:
                native["version"] = value
            with self.subTest(value=value), patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(native).encode())):
                result = self.call({"refno": REFNO})
            self.assertEqual(result["structuredContent"], native)
            if value == "missing":
                self.assertNotIn("version", result["structuredContent"])

    def test_partial_truncated_cycle_and_changed_context_receipts_remain_native_evidence(self):
        for code in ("owner_read_failed", "max_depth_reached", "owner_cycle", "mdb_membership_incomplete", "context_changed"):
            native = ancestry_receipt()
            data = native["result"]
            data.update(success=False, complete=False, reached_world=None, truncated=code == "max_depth_reached",
                        mdb_membership_complete=False)
            data["ancestors"] = data["ancestors"][:1]
            data["ancestors"][0]["in_current_mdb"] = None
            data["context"].update(mdb_after="OTHER" if code == "context_changed" else "MDB",
                                   identity_unchanged=False if code == "context_changed" else None,
                                   ce_unchanged=None)
            data["read_errors"] = [{"path": "ancestors[0].owner", "code": code, "message": "native diagnostic"}]
            with self.subTest(code=code), patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(native).encode())):
                result = self.call({"refno": REFNO})
            self.assertTrue(result["isError"])
            self.assertEqual(result["structuredContent"], native)
            self.assertEqual(json.loads(result["content"][0]["text"]), native)

    def test_mock_and_other_versions_are_unavailable_without_native_or_fabricated_rows(self):
        for mode, version, code in (("mock", "E3D21", "native_unavailable"),
                                    ("bridge", "E3D31", "unsupported_version"),
                                    ("mock", "E3D31", "unsupported_version"),
                                    ("bridge", "default", "unsupported_version")):
            server = create_server(Settings(mode=mode, version=version, allow_commands=False))
            with self.subTest(mode=mode, version=version), patch.object(BridgeClient, "_request") as request, \
                    patch("urllib.request.urlopen") as urlopen:
                result = self.call({"refno": REFNO, "max_depth": 4}, server)
            request.assert_not_called()
            urlopen.assert_not_called()
            self.assertTrue(result["isError"])
            native = result["structuredContent"]["result"]
            for flag in ("success", "available", "complete", "mdb_membership_complete"):
                self.assertFalse(native[flag])
            self.assertEqual(native["requested_refno"], REFNO)
            self.assertEqual(native["max_depth"], 4)
            self.assertEqual(native["read_errors"][0]["code"], code)
            self.assertIsNone(native["reached_world"])
            self.assertIsNone(native["truncated"])
            for field in ("ancestors", "world", "mdb_databases", "database", "raw_ref", "raw_refno",
                          "formatted_refno", "in_current_mdb", "context", "ce_unchanged", "identity_unchanged"):
                self.assertNotIn(field, native)

    def test_transport_or_invalid_response_failure_never_retries_recovers_or_mutates(self):
        for failure in (BridgeError("closed", transport_error=True, diagnostics={"state": {"database_ready": False}}),
                        BridgeError("invalid JSON", response_invalid=True)):
            with self.subTest(failure=str(failure)), patch.object(BridgeClient, "_request", side_effect=failure) as request, \
                    patch.object(BridgeClient, "_recover_command_timeout") as recover, \
                    patch.object(BridgeClient, "command") as command, patch.object(BridgeClient, "cancel_command") as cancel, \
                    patch.object(BridgeClient, "command_state") as state, patch.object(BridgeClient, "capture_view") as capture:
                result = self.call({"refno": REFNO})
            self.assertTrue(result["isError"])
            self.assertNotIn("outcome_uncertain", json.dumps(result))
            self.assertNotIn("retry_safe", json.dumps(result))
            if failure.diagnostics:
                self.assertEqual(result["structuredContent"]["state"], failure.diagnostics["state"])
            request.assert_called_once()
            for operation in (recover, command, cancel, state, capture):
                operation.assert_not_called()

    def test_actual_http_transport_and_invalid_json_fail_once(self):
        for failure in (urllib.error.URLError("closed"), TimeoutError("timeout"), OSError("closed")):
            with self.subTest(failure=type(failure).__name__), patch("urllib.request.urlopen", side_effect=failure) as urlopen:
                result = self.call({"refno": REFNO})
            self.assertTrue(result["isError"])
            urlopen.assert_called_once()
        for body in (b"not JSON", b"[]", b'{"ok":1}', b'{"ok":true,"result":{"depth":NaN}}'):
            with self.subTest(body=body), patch("urllib.request.urlopen", return_value=BytesIO(body)) as urlopen:
                result = self.call({"refno": REFNO})
            self.assertTrue(result["isError"])
            urlopen.assert_called_once()

    def test_native_failure_envelopes_keep_partial_result_and_version_without_arbitrary_fields(self):
        for http_status in (None, 409, 503):
            for include_version in (True, False):
                native = ancestry_receipt()
                native.update(ok=False, error="native ancestry read failed", private_envelope="not diagnostic")
                if not include_version:
                    native.pop("version")
                native["result"].update(success=False, complete=False)
                native["result"]["read_errors"] = [{
                    "path": "ancestors[1]", "code": "owner_read_failed", "message": "native read error"}]
                body = json.dumps(native).encode()
                response = {"side_effect": urllib.error.HTTPError("http://127.0.0.1:8766" + ROUTE,
                    http_status, "native failure", {}, BytesIO(body))} if http_status else {"return_value": BytesIO(body)}
                with self.subTest(http_status=http_status, include_version=include_version), \
                        patch("urllib.request.urlopen", **response) as urlopen:
                    result = self.call({"refno": REFNO})
                self.assertTrue(result["isError"])
                self.assertEqual(result["structuredContent"]["result"], native["result"])
                if include_version:
                    self.assertEqual(result["structuredContent"]["version"], native["version"])
                else:
                    self.assertNotIn("version", result["structuredContent"])
                self.assertNotIn("private_envelope", result["structuredContent"])
                self.assertNotIn("outcome_uncertain", result["structuredContent"])
                urlopen.assert_called_once()

    def test_stdio_rpc_round_trip_keeps_native_nullable_evidence(self):
        native = ancestry_receipt()
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "ancestry-stdio-test", "version": "1.0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "e3d_element_ancestry", "arguments": {"refno": REFNO}}},
        ]
        output = StringIO()
        with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(native).encode())) as urlopen, \
                patch("sys.stdout", output):
            self.server.serve([json.dumps(request) + "\n" for request in requests])
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([reply["id"] for reply in replies], [1, 2])
        self.assertEqual(replies[1]["result"]["structuredContent"], native)
        self.assertEqual(json.loads(replies[1]["result"]["content"][0]["text"]), native)
        urlopen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
