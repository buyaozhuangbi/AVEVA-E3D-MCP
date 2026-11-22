from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.protocol import McpServer


class ProtocolValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = McpServer("test", "1")
        self.calls = []

        def mutate(arguments):
            self.calls.append(arguments)
            return {"ok": True}

        self.server.add_tool("mutate", "Test mutation", {"type": "object"}, mutate)

    def request(self, **changes):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "mutate"}}
        request.update(changes)
        return request

    @staticmethod
    def initialize_params(protocol_version="2024-11-05"):
        return {"protocolVersion": protocol_version, "capabilities": {},
                "clientInfo": {"name": "protocol-test", "version": "1.0"}}

    def assert_rejected(self, request, code):
        response = self.server._dispatch(request)
        self.assertEqual(response["error"]["code"], code)
        self.assertTrue(response["error"]["message"])
        self.assertEqual(self.calls, [])
        return response

    def test_invalid_envelopes_cannot_execute_tools(self):
        requests = [None, [], False, self.request(jsonrpc="1.0"), self.request(method=None), self.request(method=[])]
        missing_version = self.request()
        del missing_version["jsonrpc"]
        requests.append(missing_version)
        requests.extend([
            {"method": "tools/call", "params": {"name": "mutate"}},
            {"jsonrpc": "1.0", "method": "notifications/unknown"},
            {"jsonrpc": "2.0", "method": False},
        ])
        for request in requests:
            with self.subTest(request=request):
                self.assertIsNone(self.assert_rejected(request, -32600)["id"])

    def test_invalid_ids_cannot_execute_tools(self):
        for request_id in [None, True, False, 1.5, [], {}]:
            with self.subTest(request_id=request_id):
                self.assertIsNone(self.assert_rejected(self.request(id=request_id), -32600)["id"])

    def test_explicit_nonobject_params_and_arguments_are_rejected(self):
        for value in [None, False, True, [], [1], "", 0]:
            with self.subTest(value=value):
                self.assert_rejected(self.request(params=value), -32602)
                self.assert_rejected(self.request(params={"name": "mutate", "arguments": value}), -32602)
                for method in ["initialize", "ping", "tools/list"]:
                    self.assert_rejected(self.request(method=method, params=value), -32602)

    def test_notifications_are_silent_and_cannot_execute_tools(self):
        mutation = self.request()
        del mutation["id"]
        self.assertIsNone(self.server._dispatch(mutation))
        self.assertIsNone(self.server._dispatch({"jsonrpc": "2.0", "method": "notifications/unknown"}))
        for method, params in [("notifications/initialized", {}), ("notifications/cancelled", {"requestId": 0}),
                               ("notifications/cancelled", {"requestId": "request", "reason": "stop"})]:
            self.assertIsNone(self.server._dispatch({"jsonrpc": "2.0", "method": method, "params": params}))
            self.assert_rejected(self.request(method=method, params=params), -32600)
        for params in [{}, {"requestId": False}, {"requestId": None}, {"requestId": 1, "reason": False}]:
            self.assertIsNone(self.server._dispatch({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": params}))
        for method in ["tools/call", "notifications/initialized", "notifications/unknown"]:
            for params in [None, False, [], "bad"]:
                self.assertIsNone(self.server._dispatch({"jsonrpc": "2.0", "method": method, "params": params}))
        self.assertEqual(self.calls, [])

    def test_valid_ids_and_default_arguments_still_execute(self):
        for request_id in [0, -1, "", "request"]:
            response = self.server._dispatch(self.request(id=request_id))
            self.assertEqual(response["id"], request_id)
            self.assertEqual(response["result"]["structuredContent"], {"ok": True})
        self.assertEqual(self.calls, [{}, {}, {}, {}])

    def test_closed_tool_schema_rejects_unknown_arguments_before_handler(self):
        calls = []
        self.server.add_tool("closed", "Closed input contract", {
            "type": "object", "properties": {"value": {"type": "integer"}},
            "additionalProperties": False,
        }, lambda arguments: calls.append(arguments) or {"ok": True})
        response = self.server._dispatch({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "closed", "arguments": {"value": 1, "secret": "do not echo"}}})
        self.assertEqual(response["id"], 7)
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(response["result"]["content"][0]["text"], "Unknown closed argument")
        self.assertEqual(calls, [])

    def test_tool_schema_validates_required_types_enums_ranges_and_nested_arrays(self):
        calls = []
        self.server.add_tool("schema", "Schema fixture", {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["read", "write"]},
                "count": {"type": "integer", "minimum": 1, "maximum": 3},
                "samples": {
                    "type": "array", "minItems": 1, "maxItems": 2,
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "score": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "required": ["label", "score"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["mode", "count", "samples"],
            "additionalProperties": False,
        }, lambda arguments: calls.append(arguments) or {"ok": True})

        invalid = [
            {"mode": "read", "count": 1},  # missing required property
            {"mode": "read", "count": True, "samples": [{"label": "a", "score": 0.5}]},
            {"mode": "other", "count": 1, "samples": [{"label": "a", "score": 0.5}]},
            {"mode": "read", "count": 4, "samples": [{"label": "a", "score": 0.5}]},
            {"mode": "read", "count": 1, "samples": []},
            {"mode": "read", "count": 1, "samples": [{"label": "a", "score": 1.1}]},
            {"mode": "read", "count": 1, "samples": [{"label": "a"}]},
            {"mode": "read", "count": 1, "samples": [{"label": "a", "score": 0.5, "extra": 1}]},
            {"mode": "read", "count": 1, "samples": [{"label": "a", "score": 0.5}], "extra": 1},
        ]
        for request_id, arguments in enumerate(invalid, 1):
            with self.subTest(arguments=arguments):
                response = self.server._dispatch({"jsonrpc": "2.0", "id": request_id,
                    "method": "tools/call", "params": {"name": "schema", "arguments": arguments}})
                self.assertTrue(response["result"]["isError"])
                self.assertEqual(len(response["result"]["content"]), 1)
                self.assertEqual(response["result"]["content"][0]["type"], "text")
        self.assertEqual(calls, [])

        valid = {"mode": "write", "count": 3, "samples": [{"label": "a", "score": 0.0}]}
        response = self.server._dispatch({"jsonrpc": "2.0", "id": 20, "method": "tools/call",
                                          "params": {"name": "schema", "arguments": valid}})
        self.assertEqual(response["result"]["structuredContent"], {"ok": True})
        self.assertEqual(calls, [valid])

    def test_tool_schema_anyof_and_conditional_allof_validate_before_handler(self):
        calls = []
        self.server.add_tool("conditional", "Conditional fixture", {
            "type": "object",
            "properties": {
                "refno": {"type": "string"},
                "dimensions": {"type": "object"},
                "position": {"type": "array", "items": {"type": "number"}},
                "dry_run": {"type": "boolean"},
                "expected_revision": {"type": "string"},
            },
            "required": ["refno"],
            "anyOf": [{"required": ["dimensions"]}, {"required": ["position"]}],
            "allOf": [{
                "if": {"properties": {"dry_run": {"const": False}}, "required": ["dry_run"]},
                "then": {"required": ["expected_revision"]},
            }],
            "additionalProperties": False,
        }, lambda arguments: calls.append(arguments) or {"ok": True})

        invalid = [
            {"refno": "/PUMP"},
            {"refno": "/PUMP", "dimensions": {} , "dry_run": False},
            {"refno": "/PUMP", "position": [1, "bad"]},
        ]
        for request_id, arguments in enumerate(invalid, 1):
            with self.subTest(arguments=arguments):
                response = self.server._dispatch({"jsonrpc": "2.0", "id": request_id,
                    "method": "tools/call", "params": {"name": "conditional", "arguments": arguments}})
                self.assertTrue(response["result"]["isError"])
                self.assertEqual(response["result"]["content"][0]["type"], "text")
        self.assertEqual(calls, [])

        valid = {"refno": "/PUMP", "position": [1, 2, 3], "dry_run": False,
                 "expected_revision": "rev"}
        response = self.server._dispatch({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                                          "params": {"name": "conditional", "arguments": valid}})
        self.assertEqual(response["result"]["structuredContent"], {"ok": True})
        self.assertEqual(calls, [valid])

    def test_nested_property_names_are_validated(self):
        calls = []
        self.server.add_tool("named", "Property names fixture", {
            "type": "object",
            "properties": {
                "dimensions": {
                    "type": "object",
                    "propertyNames": {"pattern": r"^(?:DIAM|RADI)$"},
                    "additionalProperties": {"type": "number", "minimum": 0},
                },
            },
            "required": ["dimensions"],
            "additionalProperties": False,
        }, lambda arguments: calls.append(arguments) or {"ok": True})

        for dimensions in ({"BAD": 1}, {"DIAM": -1}, {"DIAM": "1"}):
            response = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                              "params": {"name": "named", "arguments": {"dimensions": dimensions}}})
            self.assertTrue(response["result"]["isError"])
            self.assertEqual(response["result"]["content"][0]["text"], "Invalid named arguments")
        response = self.server._dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                          "params": {"name": "named", "arguments": {"dimensions": {"DIAM": 1, "RADI": 0}}}})
        self.assertEqual(response["result"]["structuredContent"], {"ok": True})
        self.assertEqual(len(calls), 1)

    def test_malformed_schema_pattern_rejects_safely(self):
        calls = []
        self.server.add_tool("bad_pattern", "Malformed pattern fixture", {
            "type": "object", "properties": {"value": {"type": "string", "pattern": "["}},
            "additionalProperties": False,
        }, lambda arguments: calls.append(arguments) or {"ok": True})
        response = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                          "params": {"name": "bad_pattern", "arguments": {"value": "ok"}}})
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(response["result"]["content"][0]["text"], "Invalid bad_pattern arguments")
        self.assertEqual(calls, [])

    def test_schema_utf8_byte_limit_rejects_overflow_and_unpaired_surrogates(self):
        calls = []
        self.server.add_tool("utf8", "UTF-8 byte limit fixture", {
            "type": "object", "properties": {
                "value": {"type": "string", "minLength": 1, "maxLength": 4,
                          "pattern": r"\S", "not": {"pattern": r"\x00"},
                          "x-maxUtf8Bytes": 4},
            }, "required": ["value"], "additionalProperties": False,
        }, lambda arguments: calls.append(arguments) or {"ok": True})

        for value in ("汉汉", "\ud800"):
            response = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                              "params": {"name": "utf8", "arguments": {"value": value}}})
            self.assertTrue(response["result"]["isError"])
        response = self.server._dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                          "params": {"name": "utf8", "arguments": {"value": "汉"}}})
        self.assertEqual(response["result"]["structuredContent"], {"ok": True})
        self.assertEqual(calls, [{"value": "汉"}])

    def test_schema_rejection_preserves_capture_failure_receipt_shape(self):
        calls = []
        self.server.add_tool("captured", "Capture fixture", {
            "type": "object", "properties": {"value": {"type": "string"}},
            "required": ["value"], "additionalProperties": False,
        }, lambda arguments: calls.append(arguments) or {"ok": True}, capture_receipt=True)
        response = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "captured", "arguments": {"capture_after": True, "capture_width": 800}}})
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(response["result"]["content"][0]["text"], "value is required")
        self.assertEqual(response["result"]["structuredContent"], {
            "ok": False, "error": "value is required", "receipt": {
                "operation_replayed": False,
                "capture": {"success": False, "status": "skipped", "reason": "operation_failed",
                             "requested": True, "width": 800, "height": 960},
            },
        })
        self.assertEqual(calls, [])

    def test_cancellation_keeps_completed_receipt_and_does_not_run_recovery(self):
        recovery_calls = []
        self.server.add_tool("e3d_cancel_command", "Recovery", {"type": "object"},
                             lambda arguments: recovery_calls.append(arguments))
        requests = [
            self.request(id="write"),
            {"jsonrpc": "2.0", "method": "notifications/cancelled",
             "params": {"requestId": "write", "reason": "client stopped waiting"}},
            self.request(id="next", method="ping", params={}),
        ]
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve(json.dumps(request) + "\n" for request in requests)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([response["id"] for response in responses], ["write", "next"])
        self.assertEqual(responses[0]["result"]["structuredContent"], {"ok": True})
        self.assertEqual(self.calls, [{}])
        self.assertEqual(recovery_calls, [])

    def test_initialize_falls_back_to_latest_supported_protocol_version(self):
        for requested in ("2024-11-05", "2099-01-01", "garbage", ""):
            with self.subTest(requested=requested):
                response = self.server._dispatch({"jsonrpc": "2.0", "id": 1,
                    "method": "initialize", "params": self.initialize_params(requested)})
                self.assertEqual(response["result"]["protocolVersion"], "2024-11-05")

    def test_initialize_rejects_non_string_protocol_version(self):
        for requested in (None, False, 2024, [], {}):
            with self.subTest(requested=requested):
                response = self.server._dispatch({"jsonrpc": "2.0", "id": 1,
                    "method": "initialize", "params": {**self.initialize_params(),
                                                           "protocolVersion": requested}})
                self.assertEqual(response["error"]["code"], -32602)

    def test_initialize_requires_protocol_capabilities_and_client_info(self):
        valid = self.initialize_params()
        malformed = [
            {},
            {"capabilities": {}, "clientInfo": valid["clientInfo"]},
            {"protocolVersion": "2024-11-05", "clientInfo": valid["clientInfo"]},
            {"protocolVersion": "2024-11-05", "capabilities": [], "clientInfo": valid["clientInfo"]},
            {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {}},
            {"protocolVersion": "2024-11-05", "capabilities": {},
             "clientInfo": {"name": "client", "version": 1}},
        ]
        for params in malformed:
            with self.subTest(params=params):
                response = self.server._dispatch({"jsonrpc": "2.0", "id": 1,
                                                  "method": "initialize", "params": params})
                self.assertEqual(response["error"]["code"], -32602)

    def test_stdio_requires_completed_handshake_before_tool_execution(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "mutate"}},
            {"jsonrpc": "2.0", "id": 2, "method": "initialize",
             "params": self.initialize_params()},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "mutate"}},
        ]
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve((json.dumps(request) + "\n" for request in requests), enforce_lifecycle=True)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([item["id"] for item in responses], [1, 2, 3, 4])
        self.assertEqual(responses[0]["error"]["code"], -32600)
        self.assertEqual(responses[1]["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(responses[2]["error"]["code"], -32600)
        self.assertEqual(responses[3]["result"]["structuredContent"], {"ok": True})
        self.assertEqual(self.calls, [{}])

    def test_malformed_initialize_does_not_unlock_tools(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "mutate"}},
        ]
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve((json.dumps(request) + "\n" for request in requests), enforce_lifecycle=True)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([item["id"] for item in responses], [1, 2])
        self.assertEqual(responses[0]["error"]["code"], -32602)
        self.assertEqual(responses[1]["error"]["code"], -32600)
        self.assertEqual(self.calls, [])

    def test_initialized_notification_before_initialize_does_not_unlock_tools(self):
        requests = [
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "mutate"}},
        ]
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve((json.dumps(request) + "\n" for request in requests), enforce_lifecycle=True)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["error"]["code"], -32600)
        self.assertEqual(self.calls, [])

    def test_resource_list_and_read_return_registered_dynamic_content(self):
        values = iter([{"state": "first"}, {"state": "second"}])
        self.server.add_resource("e3d://E3D21/status", "e3d_status", "Current status", "application/json",
                                 lambda: next(values))
        listed = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}})
        self.assertEqual(listed["result"]["resources"][0]["uri"], "e3d://E3D21/status")
        self.assertEqual(listed["result"]["resources"][0]["mimeType"], "application/json")
        for request_id, expected in ((2, "first"), (3, "second")):
            response = self.server._dispatch({"jsonrpc": "2.0", "id": request_id,
                "method": "resources/read", "params": {"uri": "e3d://E3D21/status"}})
            self.assertEqual(response["result"]["contents"][0]["uri"], "e3d://E3D21/status")
            self.assertEqual(json.loads(response["result"]["contents"][0]["text"]), {"state": expected})

    def test_resource_read_rejects_missing_unknown_and_failed_resources(self):
        self.server.add_resource("e3d://E3D21/failure", "failure", "Failure fixture", "text/plain",
                                 lambda: (_ for _ in ()).throw(RuntimeError("resource unavailable")))
        missing = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {}})
        self.assertEqual(missing["error"]["code"], -32602)
        unknown = self.server._dispatch({"jsonrpc": "2.0", "id": 2, "method": "resources/read",
                                         "params": {"uri": "e3d://E3D21/unknown"}})
        self.assertEqual(unknown["error"]["code"], -32002)
        failed = self.server._dispatch({"jsonrpc": "2.0", "id": 3, "method": "resources/read",
                                        "params": {"uri": "e3d://E3D21/failure"}})
        self.assertEqual(failed["error"], {"code": -32603, "message": "resource unavailable"})
        self.assertEqual(self.calls, [])

    def test_resources_require_unique_uris(self):
        handler = lambda: {"ok": True}
        self.server.add_resource("e3d://E3D21/status", "status", "Status", "application/json", handler)
        with self.assertRaisesRegex(ValueError, "Duplicate resource URI"):
            self.server.add_resource("e3d://E3D21/status", "other", "Other", "text/plain", handler)

    def test_prompt_listing_and_get_return_standard_message_shape(self):
        calls = []
        self.server.add_prompt("diagnose", "Diagnose one target", [
            {"name": "refno", "description": "Explicit reference", "required": True},
        ], lambda arguments: calls.append(arguments) or f"Inspect {arguments['refno']} read-only.")
        initialized = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                             "params": self.initialize_params()})
        self.assertIn("prompts", initialized["result"]["capabilities"])
        listed = self.server._dispatch({"jsonrpc": "2.0", "id": 2, "method": "prompts/list", "params": {}})
        descriptor = listed["result"]["prompts"][0]
        self.assertEqual(descriptor["name"], "diagnose")
        self.assertTrue(descriptor["arguments"][0]["required"])
        response = self.server._dispatch({"jsonrpc": "2.0", "id": 3, "method": "prompts/get",
                                          "params": {"name": "diagnose", "arguments": {"refno": "/PUMP"},
                                                     "_meta": {"progressToken": "read-1"}}})
        self.assertEqual(response["result"]["messages"], [{"role": "user", "content": {
            "type": "text", "text": "Inspect /PUMP read-only."}}])
        self.assertEqual(calls, [{"refno": "/PUMP"}])

    def test_prompt_get_rejects_invalid_arguments_before_handler(self):
        calls = []
        self.server.add_prompt("diagnose", "Diagnose one target", [
            {"name": "refno", "description": "Explicit reference", "required": True},
        ], lambda arguments: calls.append(arguments) or "prompt")
        requests = [
            {"name": "missing"},
            {"name": "diagnose"},
            {"name": "diagnose", "arguments": None},
            {"name": "diagnose", "arguments": {"refno": 1}},
            {"name": "diagnose", "arguments": {"refno": "/PUMP", "other": "x"}},
            {"name": "diagnose", "arguments": {"refno": "/PUMP"}, "extra": "x"},
            {"name": "diagnose", "arguments": {"refno": "/PUMP"}, "_meta": None},
        ]
        for request_id, params in enumerate(requests, 1):
            response = self.server._dispatch({"jsonrpc": "2.0", "id": request_id,
                "method": "prompts/get", "params": params})
            self.assertEqual(response["error"]["code"], -32602)
        self.assertEqual(calls, [])

    def test_prompt_handler_errors_are_protocol_errors_and_notifications_do_not_render(self):
        calls = []
        def broken(_):
            calls.append("rendered")
            raise RuntimeError("render failed")
        self.server.add_prompt("broken", "Broken renderer", [], broken)
        self.server.add_prompt("invalid", "Invalid renderer", [], lambda _: (_ for _ in ()).throw(ValueError("invalid prompt data")))
        for name, code in (("broken", -32603), ("invalid", -32602)):
            response = self.server._dispatch({"jsonrpc": "2.0", "id": name,
                "method": "prompts/get", "params": {"name": name}})
            self.assertEqual(response["error"]["code"], code)
        notification = {"jsonrpc": "2.0", "method": "prompts/get", "params": {"name": "broken"}}
        self.assertIsNone(self.server._dispatch(notification))
        self.assertEqual(calls, ["rendered"])

    def test_prompt_names_and_argument_definitions_must_be_unique_and_valid(self):
        handler = lambda _: "prompt"
        argument = {"name": "refno", "description": "Target", "required": True}
        self.server.add_prompt("diagnose", "Diagnose", [argument], handler)
        with self.assertRaisesRegex(ValueError, "Duplicate prompt name"):
            self.server.add_prompt("diagnose", "Other", [], handler)
        with self.assertRaisesRegex(ValueError, "unique"):
            self.server.add_prompt("other", "Other", [argument, argument], handler)

    def test_stdio_recovers_after_bad_input_without_mutations(self):
        invalid = self.request(params={"name": "mutate", "arguments": False})
        notification = self.request()
        del notification["id"]
        lines = ["{broken", json.dumps(invalid), json.dumps(notification),
                 json.dumps({"jsonrpc": "2.0", "method": "notifications/unknown", "params": False}),
                 json.dumps(self.request(id="valid"))]
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve(lines)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(responses), 3)
        self.assertEqual([item["error"]["code"] for item in responses[:2]], [-32700, -32602])
        self.assertEqual(responses[2]["id"], "valid")
        self.assertEqual(responses[2]["result"]["structuredContent"], {"ok": True})
        self.assertEqual(self.calls, [{}])

    def test_stdio_rejects_nonstandard_json_numbers_and_recovers(self):
        requests = [
            '{"jsonrpc":"2.0","id":1,"method":"ping","params":{"value":NaN}}',
            '{"jsonrpc":"2.0","id":2,"method":"ping","params":{"value":Infinity}}',
            '{"jsonrpc":"2.0","id":3,"method":"ping","params":{"value":-Infinity}}',
            '{"jsonrpc":"2.0","id":"valid","method":"ping","params":{}}',
        ]
        env = {key: value for key, value in os.environ.items() if not key.startswith("E3D_")}
        env.update({"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                    "PYTHONIOENCODING": "utf-8:strict", "E3D_MCP_MODE": "mock"})
        process = subprocess.run([sys.executable, "-m", "aveva_e3d_mcp"],
                                input="\n".join(requests) + "\n", capture_output=True,
                                text=True, encoding="utf-8", env=env, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(len(responses), 4)
        for response in responses[:3]:
            with self.subTest(response=response):
                self.assertIsNone(response["id"])
                self.assertEqual(response["error"]["code"], -32700)
                self.assertIn("Non-standard JSON numeric constant", response["error"]["message"])
        self.assertEqual(responses[3], {"jsonrpc": "2.0", "id": "valid", "result": {}})
        self.assertEqual(self.calls, [])

    def test_stdio_never_emits_nonstandard_json_numbers(self):
        values = iter((math.nan, math.inf))
        self.server.add_tool("nonfinite", "Non-finite output fixture", {"type": "object"},
                             lambda _arguments: {"value": next(values)})
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "nonfinite", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "nonfinite", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}},
        ]
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve((json.dumps(request) + "\n" for request in requests))
        wire_lines = output.getvalue().splitlines()
        self.assertEqual(len(wire_lines), 3)
        responses = [json.loads(line, parse_constant=lambda value: (_ for _ in ()).throw(
            AssertionError(f"non-standard number on stdio wire: {value}"))) for line in wire_lines]
        for response in responses[:2]:
            self.assertEqual(response["error"]["code"], -32603)
            self.assertEqual(response["error"]["message"],
                             "Response cannot be encoded as strict JSON")
        self.assertEqual(responses[2], {"jsonrpc": "2.0", "id": 3, "result": {}})

    def test_real_utf8_stdio_survives_escaped_surrogates_and_preserves_unicode(self):
        # StringIO accepts lone surrogates, unlike the registered UTF-8 pipe.
        # Exercise the actual process entrypoint and its strict output encoder.
        requests = [
            {"jsonrpc": "2.0", "id": "\ud800", "method": "ping"},
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": self.initialize_params()},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "\udfff"}},
            {"jsonrpc": "2.0", "id": "中文回执🚀", "method": "ping"},
        ]
        env = {key: value for key, value in os.environ.items() if not key.startswith("E3D_")}
        env.update({"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                    "PYTHONIOENCODING": "utf-8:strict", "E3D_MCP_MODE": "mock"})
        process = subprocess.run(
            [sys.executable, "-m", "aveva_e3d_mcp"],
            input="".join(json.dumps(request) + "\n" for request in requests),
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=10,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(len(responses), 4)
        self.assertEqual([response["id"] for response in responses], ["\ud800", 1, 2, "中文回执🚀"])
        self.assertEqual(responses[0]["result"], {})
        self.assertEqual(responses[2]["error"]["code"], -32602)
        self.assertIn("\udfff", responses[2]["error"]["message"])
        self.assertEqual(responses[3]["result"], {})


if __name__ == "__main__":
    unittest.main()
