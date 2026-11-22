from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


TOOL = "e3d_equipment_manifest"
ROUTE = "/api/v1/equipment/manifest"
BRIDGE = Settings(mode="bridge", version="E3D21", allow_commands=False)


def row(index, refno, kind, name, parent_index, depth, sibling_index, *, attributes=()):
    values = {key: {"DESC": "native description", "DIAM": "125mm"}[key] for key in attributes}
    return {"id": index, "refno": refno, "type": kind, "name": name,
            "parent_index": parent_index, "depth": depth, "sibling_index": sibling_index,
            "values": values, "typed_values": {"DIAM": 125.0} if "DIAM" in values else {},
            "errors": {}, "typed_read_errors": {}, "identity_errors": {}, "success": True}


def native_response(*, attributes=(), limit=64, max_depth=8, refno="/PUMP"):
    rows = [
        row(0, "=1/2", "EQUI", "/PUMP", None, 0, None, attributes=attributes),
        row(1, "=1/3", "SUBE", "/PUMP/BODY", 0, 1, 1, attributes=attributes),
        row(2, "=1/4", "CYLI", "CYLI 1", 1, 2, 1, attributes=attributes),
        row(3, "=1/5", "NOZZ", "/PUMP/N1", 0, 1, 2, attributes=attributes),
    ]
    return {"ok": True, "version": "E3D21", "session_id": "test-session", "result": {
        "requested_refno": refno, "root_refno": "=1/2", "rows": rows, "returned_count": len(rows),
        "order": "preorder", "limit": limit, "max_depth": max_depth, "attributes": list(attributes),
        "complete": True, "truncated": False, "truncation_reasons": [], "read_errors": [],
        "ce_before": "=1/9", "ce_after": "=1/9", "ce_unchanged": True,
        "project": "P", "project_after": "P", "mdb": "ALL", "mdb_after": "ALL", "identity_unchanged": True,
        "session_id": "test-session", "atomic_snapshot": False,
        "single_dispatch": True, "success": True,
    }}


class EquipmentManifestToolsTests(unittest.TestCase):
    def rpc(self, method, params, settings=BRIDGE):
        output = StringIO()
        server = create_server(settings)
        with patch("sys.stdout", output):
            server.serve([json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}) + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def call(self, arguments, settings=BRIDGE):
        return self.rpc("tools/call", {"name": TOOL, "arguments": arguments}, settings)

    def test_discovery_exposes_only_bounded_read_controls(self):
        tool = next(tool for tool in self.rpc("tools/list", {})["tools"] if tool["name"] == TOOL)
        self.assertEqual(tool["annotations"], {"readOnlyHint": True, "destructiveHint": False,
                                               "idempotentHint": True, "openWorldHint": False})
        schema = tool["inputSchema"]
        self.assertEqual(schema["required"], ["refno"])
        self.assertFalse(schema["additionalProperties"])
        properties = schema["properties"]
        self.assertEqual(set(properties), {"refno", "max_depth", "limit", "attributes"})
        self.assertEqual(properties["refno"]["maxLength"], 256)
        for name, maximum, default in (("max_depth", 32, 8), ("limit", 200, 64)):
            self.assertEqual([properties[name][key] for key in ("type", "minimum", "maximum", "default")],
                             ["integer", 1, maximum, default])
        self.assertEqual(properties["attributes"]["maxItems"], 10)
        self.assertEqual(properties["attributes"]["default"], [])

    def test_defaults_read_explicit_equipment_in_one_post_with_commands_disabled(self):
        payload = native_response()
        with patch.object(BridgeClient, "_request", return_value=payload) as request:
            response = self.call({"refno": " /PUMP "})
        request.assert_called_once_with("POST", ROUTE, {"refno": "/PUMP", "max_depth": 8,
                                                        "limit": 64, "attributes": []}, version="E3D21")
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"], payload)

    def test_native_hierarchy_attribute_maps_and_context_are_preserved(self):
        payload = native_response(attributes=("DESC", "DIAM"), limit=200, max_depth=32, refno="=1/2")
        expected = deepcopy(payload)
        with patch.object(BridgeClient, "_request", return_value=payload) as request:
            response = self.call({"refno": "=01/002", "limit": 200, "max_depth": 32,
                                  "attributes": [" desc ", "diam"]})
        request.assert_called_once_with("POST", ROUTE, {"refno": "=1/2", "max_depth": 32,
                                                        "limit": 200, "attributes": ["DESC", "DIAM"]}, version="E3D21")
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"], expected)
        self.assertEqual(json.loads(response["content"][0]["text"]), expected)
        self.assertEqual(payload, expected)

    def test_invalid_arguments_never_contact_bridge(self):
        invalid = [{}, {"refno": "/PUMP", "cursor": "opaque"}, {"refno": "/PUMP", "capture_after": False}]
        invalid += [{"refno": value} for value in (None, True, 2, "", "CE", "/", "/*", "/PUMP*", "=1/x",
                                                  "=0/2", "=2147483648/2", "/PUMP\n/OTHER", "/" + "X" * 256)]
        for name, values in {
            "max_depth": (None, True, 0, 33, 1.0, "2"),
            "limit": (None, True, 0, 201, 1.0, "2"),
            "attributes": (None, "DESC", [None], [""], ["DESC", " desc "], ["\x00DESC"], ["DIAM;"],
                           [f"ATTR{i}" for i in range(11)]),
        }.items():
            invalid.extend({"refno": "/PUMP", name: value} for value in values)
        invalid.append({"refno": "/PUMP", "limit": 51, "attributes": [f"ATTR{i}" for i in range(10)]})
        with patch.object(BridgeClient, "_request") as request:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self.call(arguments)["isError"])
            request.assert_not_called()

    def test_partial_native_read_preserves_rows_and_diagnostics(self):
        payload = native_response(attributes=("DESC", "DIAM"))
        result = payload["result"]
        failed_row = result["rows"][2]
        del failed_row["values"]["DIAM"], failed_row["typed_values"]["DIAM"]
        failed_row.update(success=False, errors={"DIAM": "Attribute unavailable"})
        result.update(success=False, complete=False, read_errors=[{
            "stage": "attributes", "refno": failed_row["refno"], "error": "DIAM unavailable",
        }])
        with patch.object(BridgeClient, "_request", return_value=payload) as request:
            response = self.call({"refno": "/PUMP", "attributes": ["DESC", "DIAM"]})
        self.assertTrue(response["isError"], response)
        self.assertEqual(response["structuredContent"], payload)
        request.assert_called_once()

    def test_limits_preserve_bounded_rows_without_claiming_a_complete_manifest(self):
        for reason, arguments in (("limit", {"limit": 1}), ("depth", {"max_depth": 1})):
            with self.subTest(reason=reason):
                payload = native_response(**arguments)
                result = payload["result"]
                if reason == "limit":
                    result["rows"] = result["rows"][:1]
                else:
                    result["rows"] = result["rows"][:2]
                result.update(returned_count=len(result["rows"]), success=False, complete=False,
                              truncated=True, truncation_reasons=[reason])
                with patch.object(BridgeClient, "_request", return_value=payload) as request:
                    response = self.call({"refno": "/PUMP", **arguments})
                self.assertTrue(response["isError"], response)
                self.assertEqual(response["structuredContent"], payload)
                request.assert_called_once()

    def test_changed_mdb_and_unreadable_ce_remain_visible_in_partial_manifest(self):
        payload = native_response()
        payload["result"].update(success=False, complete=False, mdb_after="OTHER", identity_unchanged=False,
                                 ce_after=None, ce_unchanged=None, read_errors=[
                                     {"stage": "context_after", "refno": None, "error": "MDB changed; CE unavailable"},
                                 ])
        with patch.object(BridgeClient, "_request", return_value=payload) as request:
            response = self.call({"refno": "/PUMP"})
        self.assertTrue(response["isError"], response)
        self.assertEqual(response["structuredContent"], payload)
        request.assert_called_once()

    def test_row_identity_failure_preserves_other_rows_and_native_evidence(self):
        payload = native_response()
        result = payload["result"]
        result["rows"][1].update(type=None, success=False, identity_errors={"type": "SDK type unavailable"})
        result.update(success=False, complete=False, read_errors=[
            {"stage": "identity", "refno": "=1/3", "field": "type", "error": "SDK type unavailable"},
        ])
        with patch.object(BridgeClient, "_request", return_value=payload):
            response = self.call({"refno": "/PUMP"})
        self.assertTrue(response["isError"], response)
        self.assertEqual(response["structuredContent"], payload)

    def test_invalid_native_replies_cannot_claim_a_complete_manifest(self):
        tamper = {
            "wrong version": lambda reply: reply.update(version="E3D31"),
            "wrong session": lambda reply: reply.update(session_id="other-session"),
            "wrong request": lambda reply: reply["result"].update(requested_refno="=1/400"),
            "different root": lambda reply: reply["result"].update(root_refno="=1/400"),
            "count mismatch": lambda reply: reply["result"].update(returned_count=1),
            "invalid parent": lambda reply: reply["result"]["rows"][2].update(parent_index=3),
            "invalid sibling": lambda reply: reply["result"]["rows"][3].update(sibling_index=1),
            "duplicated row": lambda reply: reply["result"]["rows"][3].update(id=2),
            "duplicated refno": lambda reply: reply["result"]["rows"][3].update(refno="=1/4"),
            "bad identity diagnostics": lambda reply: reply["result"]["rows"][1].update(identity_errors={"name": "wrong"}),
            "wrong type": lambda reply: reply["result"]["rows"][0].update(type="ZONE"),
            "unrequested attribute": lambda reply: reply["result"]["rows"][0]["values"].update(DESC="unsolicited"),
            "contradictory truncation": lambda reply: reply["result"].update(truncated=True),
            "CE drift": lambda reply: reply["result"].update(ce_after="=1/90"),
            "MDB drift": lambda reply: reply["result"].update(mdb_after="OTHER"),
            "unexpected atomic flag": lambda reply: reply["result"].update(atomic_snapshot=True),
        }
        for name, mutate in tamper.items():
            with self.subTest(name=name), patch.object(BridgeClient, "_request") as request:
                payload = native_response(refno="=1/2")
                mutate(payload)
                request.return_value = payload
                response = self.call({"refno": "=1/2"})
                self.assertTrue(response["isError"], response)
                result = response.get("structuredContent", {}).get("result", {})
                self.assertFalse(result.get("complete", False), result)
                request.assert_called_once()

    def test_mock_and_unsupported_bridge_return_unavailable_without_native_calls(self):
        for settings in (Settings(mode="mock", version="E3D21"), Settings(mode="bridge", version="E3D31")):
            with self.subTest(settings=settings), patch.object(BridgeClient, "_request") as request:
                response = self.call({"refno": "/PUMP"}, settings)
                self.assertTrue(response["isError"], response)
                self.assertFalse(response["structuredContent"]["result"]["complete"])
                request.assert_not_called()

    def test_missing_endpoint_and_transport_error_never_fall_back_or_retry(self):
        cases = (BridgeError("No route for manifest", http_status=404),
                 BridgeError("connection closed", transport_error=True))
        for error in cases:
            with self.subTest(error=error), patch.object(BridgeClient, "_request", side_effect=error) as request:
                response = self.call({"refno": "/PUMP"})
                self.assertTrue(response["isError"], response)
                if error.http_status == 404:
                    result = response["structuredContent"]["result"]
                    self.assertFalse(result["complete"])
                    self.assertEqual(result["rows"], [])
                    self.assertEqual(result["read_errors"][0]["code"], "native_feature_unavailable")
                else:
                    self.assertIn("connection closed", response["content"][0]["text"])
                    self.assertNotIn("outcome_uncertain", json.dumps(response))
                request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
