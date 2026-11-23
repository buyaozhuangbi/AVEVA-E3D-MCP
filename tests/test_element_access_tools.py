from __future__ import annotations

import copy
import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


def field(value, source, status="ok", refno="=2000000001/2617", **extra):
    return {"value": value, "status": status, "source": source, "source_refno": refno, **extra}


def access_receipt():
    row = {
        "requested_refno": "/PUMP", "refno": "=2000000001/2617", "resolved": True, "status": "ok",
        "scan_complete": True, "all_values_available": False, "duplicate_of_index": None,
        "fields": {
            "type": field("EQUI", "DbElement.GetElementType"), "name": field("/PUMP", "NAME"),
            "update_allowed": field(False, "DbElement.IsAttributeSetable(null)"),
            "read_only": field(True, "DbElement.IsReadOnly"), "locked": field(True, "LOCK"),
            "database_writable": field(True, "DBWRIT"), "claim_allowed": field(None, "OKCLA", "not_available"),
            "dac_claim_allowed": field(None, "DACCLA", "not_available"),
            "primary_owner_refno": field("=2000000001/2600", "PRMOWN"),
        },
        "database": {
            "number": field(12345, "Db.Number"), "extract_number": field(1, "Db.ExtractNumber"),
            "name": field("/TEST/DESIGN", "Db.Name"), "type": field("Design", "Db.DbType"),
            "is_read_only": field(False, "Db.IsReadOnly"), "is_primary": field(True, "Db.IsPrimary"),
            "is_extract": field(False, "Db.IsExtract"),
        },
        "target_claim": {
            "claimed": field(False, "LCLM"), "hierarchy_claimed": field(False, "LCLMH"),
            "claimant_user": field(None, "USERC", "unset"),
            "claimant_number": field(None, "CLMNUM", "unset"),
            "claimant_extract": field(None, "EXTRC", "unset"),
        }, "errors": [],
    }
    return {"ok": True, "version": "E3D21", "result": {
        "success": True, "scan_complete": True, "all_values_available": False,
        "read_only": True, "single_dispatch": True, "claims_freshness": "current_session_unverified",
        "claims_refresh_attempted": False, "requested_refnos": ["/PUMP"], "row_count": 1,
        "rows": [row], "read_errors": [], "ce_before": "=2000000001/2617", "ce_after": "=2000000001/2617",
        "ce_unchanged": True, "project_before": "TEST", "project_after": "TEST", "mdb_before": "MDB",
        "mdb_after": "MDB", "identity_unchanged": True, "native_database_fault": None,
    }}


class ElementAccessToolTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", bridge_url="http://127.0.0.1:8766", allow_commands=False)
        self.server = create_server(self.settings)

    def rpc(self, method, params, server=None):
        return (server or self.server)._dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})["result"]

    def call(self, args, server=None):
        return self.rpc("tools/call", {"name": "e3d_element_access", "arguments": args}, server)

    def test_catalog_and_initialize_describe_read_only_bounded_observation(self):
        tools = self.rpc("tools/list", {})["tools"]
        self.assertEqual(len(tools), 119)
        tool = next(item for item in tools if item["name"] == "e3d_element_access")
        self.assertEqual(tool["annotations"], {
            "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
        })
        schema = tool["inputSchema"]
        self.assertEqual(schema["required"], ["refnos"])
        self.assertEqual(set(schema["properties"]), {"refnos"})
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["refnos"]["minItems"], 1)
        self.assertEqual(schema["properties"]["refnos"]["maxItems"], 32)
        self.assertTrue(schema["properties"]["refnos"]["uniqueItems"])
        for token in ("scan_complete", "all_values_available", "current_session_unverified", "claims_refresh_attempted",
                      "PRMOWN", "LOCK/DBWRIT/OKCLA/DACCLA", "LCLM/LCLMH/USERC/CLMNUM/EXTRC", "not a database lock"):
            self.assertIn(token, tool["description"])
        self.assertIn("e3d_element_access", self.rpc("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "element-access-test", "version": "1.0"}
        })["instructions"])

    def test_missing_empty_implicit_malformed_overbudget_and_duplicate_inputs_never_dispatch(self):
        invalid = [{}, {"refnos": []}, {"refnos": None}, {"refnos": "/PUMP"}, {"refnos": True},
                   {"refnos": [f"/P{i}" for i in range(33)]}, {"refnos": ["/PUMP", " /pump "]},
                   {"refnos": ["=1/2", " =1/2 "]}]
        invalid.extend({"refnos": [value]} for value in (
            None, True, 12, {}, [], "", " ", "CE", "ce", "OWNER", "WORLD", "EQUI", "/", "=", "=1", "=1/2/3",
            "=x/2", "=1/x", "=0/2", "=1/-1", "=-1/2", "=+1/2", "=2147483648/2", "=1/2147483648",
            "=1.0/2", "=1/2.0", "=\u0661/2", "=1/" + "9" * 5000, "/PUMP\nDELETE CE", "/PUMP X", "/PUMP\x00", "/PUMP\x7f", "/PUMP\x80",
        ))
        with patch.object(BridgeClient, "element_access") as read, patch.object(BridgeClient, "_request") as request:
            for args in invalid:
                with self.subTest(args=str(args)[:120]):
                    self.assertTrue(self.call(args)["isError"])
        read.assert_not_called()
        request.assert_not_called()

    def test_unknown_controls_rejected_before_any_access_or_capture(self):
        with patch.object(BridgeClient, "element_access") as read, patch.object(BridgeClient, "capture_view") as capture:
            for key, value in (("dry_run", True), ("capture_after", False), ("capture_width", 1280),
                               ("refresh_claims", False), ("include_owner", True), ("attributes", ["LOCK"]),
                               ("refno", "/PUMP"), ("limit", 32), ("version", "E3D21"), ("expected_context", "a" * 64)):
                with self.subTest(key=key):
                    self.assertTrue(self.call({"refnos": ["/PUMP"], key: value})["isError"])
        read.assert_not_called()
        capture.assert_not_called()

    def test_normalizes_ordered_references_once_without_write_permission_or_followup_reads(self):
        native = access_receipt()
        with patch.object(BridgeClient, "_request", return_value=native) as request, \
                patch.object(BridgeClient, "command") as command, patch.object(BridgeClient, "cancel_command") as cancel, \
                patch.object(BridgeClient, "capture_view") as capture, patch.object(BridgeClient, "read_attributes") as attributes:
            result = self.call({"refnos": [" /PUMP ", " =2147483647/0 "]})
        request.assert_called_once_with("POST", "/api/v1/elements/access", {"refnos": ["/PUMP", "=2147483647/0"]}, version=None)
        self.assertEqual(result["structuredContent"], native)
        self.assertFalse(result.get("isError", False), result)
        for operation in (command, cancel, capture, attributes):
            operation.assert_not_called()

    def test_32_inputs_and_distinct_alias_strings_are_preserved(self):
        refnos = ["/PUMP", "=2000000001/2617", "=001/02", "=1/2"] + [f"/P{i}" for i in range(28)]
        native = access_receipt()
        duplicate = copy.deepcopy(native["result"]["rows"][0])
        duplicate.update(requested_refno="=2000000001/2617", duplicate_of_index=0)
        native["result"]["rows"].append(duplicate)
        native["result"]["row_count"] = 2
        with patch.object(BridgeClient, "element_access", return_value=native) as read:
            result = self.call({"refnos": refnos})
        read.assert_called_once_with(refnos)
        self.assertEqual(result["structuredContent"], native)

    def test_nullable_status_and_source_preserved_without_permission_inference(self):
        native = access_receipt()
        original = copy.deepcopy(native)
        with patch.object(BridgeClient, "element_access", return_value=native):
            result = self.call({"refnos": ["/PUMP"]})
        self.assertFalse(result.get("isError", False))
        self.assertEqual(result["structuredContent"], original)
        self.assertEqual(json.loads(result["content"][0]["text"]), original)
        self.assertEqual(native, original)
        self.assertNotIn("can_write", json.dumps(result))
        self.assertNotIn("permission_granted", json.dumps(result))

    def test_read_errors_context_changes_and_native_fault_partial_rows_are_preserved(self):
        for status in ("read_error", "unresolved_reference", "not_attempted"):
            native = access_receipt()
            data = native["result"]
            data.update(success=False, scan_complete=status != "not_attempted", all_values_available=False)
            data["rows"][0]["fields"]["locked"] = field(None, "LOCK", status, reason="native diagnostic")
            data["read_errors"] = [{"path": "rows[0].fields.locked", "code": status, "message": "native diagnostic"}]
            if status == "unresolved_reference":
                data["rows"][0].update(refno=None, resolved=False)
            if status == "not_attempted":
                data.update(native_database_fault={"has_fault": True, "code": 538}, identity_unchanged=None, ce_unchanged=None)
            with self.subTest(status=status), patch.object(BridgeClient, "element_access", return_value=native):
                result = self.call({"refnos": ["/PUMP"]})
            self.assertTrue(result["isError"])
            self.assertEqual(result["structuredContent"], native)

    def test_direct_bridge_validates_and_preserves_explicit_version(self):
        client = BridgeClient(self.settings)
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.element_access([" /PUMP ", "=2147483647/2147483647"], "E3D21")
            request.assert_called_once_with("POST", "/api/v1/elements/access",
                {"refnos": ["/PUMP", "=2147483647/2147483647"]}, version="E3D21")
            request.reset_mock()
            for refnos in ([], ["CE"], ["/PUMP", "/pump"], ["=0/1"], ["=1/2147483648"], ["/P"] * 33, ("/PUMP",)):
                with self.subTest(refnos=refnos), self.assertRaises(ValueError):
                    client.element_access(refnos)
            request.assert_not_called()

    def test_http_uses_only_json_body_and_assigned_instance(self):
        client = BridgeClient(self.settings)
        with patch("urllib.request.urlopen", return_value=BytesIO(b'{"ok":true,"result":{"success":true}}')) as urlopen:
            result = client.element_access(["/PUMP"])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "http://127.0.0.1:8766/api/v1/elements/access")
        self.assertEqual(json.loads(request.data), {"refnos": ["/PUMP"]})
        self.assertEqual(result["version"], "E3D21")
        with patch("urllib.request.urlopen") as urlopen, self.assertRaises(ValueError):
            client.element_access(["/PUMP"], "E3D31")
        urlopen.assert_not_called()

    def test_mock_is_unavailable_and_does_not_fabricate_access_or_claim_rows(self):
        server = create_server(Settings(mode="mock", version="E3D21", allow_commands=False))
        with patch.object(BridgeClient, "_request") as request:
            result = self.call({"refnos": ["/PUMP"]}, server)
        self.assertTrue(result["isError"])
        native = result["structuredContent"]["result"]
        for flag in ("success", "available", "scan_complete", "all_values_available", "claims_refresh_attempted"):
            self.assertFalse(native[flag])
        self.assertEqual(native["claims_freshness"], "current_session_unverified")
        self.assertEqual(native["requested_refnos"], ["/PUMP"])
        self.assertEqual(native["read_errors"][0]["code"], "native_unavailable")
        for field_name in ("rows", "fields", "database", "target_claim", "ce_unchanged", "identity_unchanged"):
            self.assertNotIn(field_name, native)
        request.assert_not_called()

    def test_transport_or_invalid_response_failure_never_triggers_mutation_recovery_or_retry(self):
        for failure in (BridgeError("closed", transport_error=True, diagnostics={"state": {"database_ready": False}}),
                        BridgeError("invalid JSON", response_invalid=True)):
            with self.subTest(failure=str(failure)), patch.object(BridgeClient, "_request", side_effect=failure) as request, \
                    patch.object(BridgeClient, "command") as command, patch.object(BridgeClient, "cancel_command") as cancel, \
                    patch.object(BridgeClient, "command_state") as state, patch.object(BridgeClient, "capture_view") as capture:
                result = self.call({"refnos": ["/PUMP"]})
            self.assertTrue(result["isError"])
            self.assertNotIn("outcome_uncertain", json.dumps(result))
            self.assertNotIn("retry_safe", json.dumps(result))
            self.assertNotIn("receipt", result.get("structuredContent", {}))
            if failure.diagnostics:
                self.assertEqual(result["structuredContent"]["state"], failure.diagnostics["state"])
            request.assert_called_once()
            for operation in (command, cancel, state, capture):
                operation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
