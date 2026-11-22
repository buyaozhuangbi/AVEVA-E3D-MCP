from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class PipingBranchStateTests(unittest.TestCase):
    def call(self, arguments: dict, settings=None) -> dict:
        server = create_server(settings or Settings(mode="bridge", version="E3D21", allow_commands=False))
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "e3d_piping_branch_state", "arguments": arguments}}
        output = StringIO()
        with patch("sys.stdout", output):
            server.serve([json.dumps(request) + "\n"])
        return json.loads(output.getvalue())["result"]

    @staticmethod
    def context():
        return {"ok": True, "version": "E3D21", "session_id": "s", "bridge_build": "b",
                "connected": True, "state": {"database_ready": True, "busy": False,
                "recovery_required": False, "native_database_fault": {"has_fault": False},
                "execution": {"dispatcher_ready": True, "native_operation_active": False},
                "native_dialogs": {"complete": True, "standard_dialog_present": False},
                "pml_alerts": {"complete": True, "alert_present": False}}}

    @staticmethod
    def project():
        return {"ok": True, "version": "E3D21", "project": {"name": "P", "mdb": "ALL",
                "current_element": "=1/2", "connected": True, "recovery_only": False}}

    def native_reads(self, *, kind="BRAN"):
        attrs = {"ok": True, "version": "E3D21", "result": {
            "success": True, "refno": "=1/2", "type": "BRAN",
            "values": {"HREF": "/P1", "TREF": "/P2", "HPOS": "W 1mm N 2mm U 3mm",
                       "TPOS": "W 4mm N 5mm U 6mm", "HDIR": "D", "TDIR": "U",
                       "HBOR": "80mm", "TBOR": "80mm", "HCON": "FBD", "TCON": "FBD",
                       "LHEAD": "true", "LTAIL": "true", "PSPEC": "/A3B", "ISPEC": "Nulref",
                       "TSPEC": "Nulref", "PRES": "0pascal", "TEMP": "50degC", "SLOREF": "Nulref"},
            "typed_values": {"HBOR": 80.0, "TBOR": 80.0}, "errors": {}, "typed_read_errors": {}}}
        members = {"ok": True, "version": "E3D21", "result": {
            "success": True, "parent": {"refno": "=1/2", "type": kind, "name": "/B-1"},
            "members": [{"refno": "=1/3", "type": "FLAN", "name": "f", "sibling_index": 1},
                        {"refno": "=1/3", "type": "TUBI", "name": "t", "sibling_index": 2}],
            "returned_count": 2, "limit": 64, "has_more": False, "next_cursor": None,
            "consistency": "live", "read_errors": []}}
        return attrs, members

    def observe(self, attrs, members, *, args=None, health=None):
        with patch.object(BridgeClient, "health", side_effect=health or [self.context()] * 4), \
             patch.object(BridgeClient, "project", side_effect=[self.project(), self.project()]), \
             patch.object(BridgeClient, "read_attributes", return_value=attrs) as read_attrs, \
             patch.object(BridgeClient, "list_members", return_value=members) as read_members:
            response = self.call({"refno": "=1/2"} if args is None else args)
        return response, read_attrs, read_members

    def test_schema_and_read_only_registration(self):
        server = create_server(Settings(mode="mock", version="E3D21", allow_commands=False))
        tools = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]
        self.assertEqual(len(tools), 114)
        tool = next(item for item in tools if item["name"] == "e3d_piping_branch_state")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertEqual(set(tool["inputSchema"]["properties"]), {"refno", "member_limit"})
        self.assertFalse(tool["inputSchema"]["additionalProperties"])

    def test_reads_attributes_and_preserves_alias_identity(self):
        for requested, lookup in ((" =01/002 ", "=1/2"), ("/b-1", "/b-1")):
            with self.subTest(requested=requested):
                response, read_attrs, read_members = self.observe(*self.native_reads(), args={"refno": requested})
                self.assertFalse(response.get("isError", False), response)
                result = response["structuredContent"]["result"]
                self.assertTrue(result["success"])
                self.assertTrue(result["context"]["boundary_agreement"])
                self.assertFalse(result["atomic_snapshot"])
                self.assertEqual(result["attributes"]["values"]["HREF"], "/P1")
                self.assertEqual(result["attributes"]["values"]["ISPEC"], "Nulref")
                self.assertEqual([row["identity_key"] for row in result["members"]],
                                 ["=1/3|FLAN|1", "=1/3|TUBI|2"])
                read_attrs.assert_called_once_with("=1/2", result["attributes"]["requested"], expected_type="BRAN")
                read_members.assert_called_once_with(lookup, "", 64, expected_type="BRAN")

    def test_invalid_input_fails_before_reads_and_wrong_type_stops_before_attributes(self):
        for arguments in ({"refno": "=1/2", "member_limit": 0}, {"refno": "CE"},
                          {"refno": "PIPE"}, {"refno": "=1/x"}, {"refno": "/*"},
                          {"refno": "=1/2", "extra": 1}):
            with self.subTest(arguments=arguments), patch.object(BridgeClient, "health") as health, \
                 patch.object(BridgeClient, "list_members") as members:
                response = self.call(arguments)
                self.assertTrue(response["isError"])
                health.assert_not_called()
                members.assert_not_called()
        response, read_attrs, _ = self.observe(*self.native_reads(kind="PIPE"))
        result = response["structuredContent"]["result"]
        self.assertFalse(result["complete"])
        self.assertFalse(result["available"])
        self.assertEqual(result["members"], [])
        read_attrs.assert_not_called()

    def test_member_overflow_and_context_drift_never_claim_complete(self):
        attrs, members = self.native_reads()
        members["result"].update(limit=1, members=members["result"]["members"][:1],
                                 returned_count=1, has_more=True, next_cursor="opaque")
        contexts = [self.context()] * 2 + [{**self.context(), "session_id": "changed"}] * 2
        response, _, _ = self.observe(attrs, members, args={"refno": "=1/2", "member_limit": 1}, health=contexts)
        result = response["structuredContent"]["result"]
        self.assertFalse(result["success"])
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["members"]), 1)
        self.assertTrue(result["member_listing"]["has_more"])
        self.assertFalse(result["context"]["boundary_agreement"])
        self.assertTrue(any(error["stage"] == "members" for error in result["read_errors"]))
        self.assertTrue(any(error["stage"] == "context_after" for error in result["read_errors"]))

    def test_readiness_failure_stops_before_project_and_branch(self):
        health = self.context()
        health["state"]["native_database_fault"]["has_fault"] = True
        with patch.object(BridgeClient, "health", return_value=health), \
             patch.object(BridgeClient, "project") as project, patch.object(BridgeClient, "list_members") as members:
            response = self.call({"refno": "=1/2"})
        self.assertTrue(response["isError"])
        project.assert_not_called()
        members.assert_not_called()

    def test_wrong_identity_and_malformed_native_data_do_not_complete(self):
        for scenario in ("attribute_identity", "null_position", "missing_error_map", "member_index",
                         "member_count", "duplicate_member", "wrong_parent", "wrong_envelope"):
            with self.subTest(scenario=scenario):
                attrs, members = self.native_reads()
                if scenario == "attribute_identity":
                    attrs["result"]["refno"] = "=1/3"
                elif scenario == "null_position":
                    attrs["result"]["values"]["HPOS"] = None
                elif scenario == "missing_error_map":
                    del attrs["result"]["errors"]
                elif scenario == "member_index":
                    members["result"]["members"][1]["sibling_index"] = 1
                elif scenario == "member_count":
                    members["result"]["returned_count"] = True
                elif scenario == "duplicate_member":
                    members["result"]["members"][1].update(refno="=01/003", type="FLAN")
                elif scenario == "wrong_parent":
                    members["result"]["parent"]["refno"] = "=1/3"
                else:
                    members["ok"] = False
                response, read_attrs, _ = self.observe(attrs, members)
                result = response["structuredContent"]["result"]
                self.assertTrue(response["isError"])
                self.assertFalse(result["complete"])
                if scenario == "attribute_identity":
                    self.assertEqual(result["attributes"]["values"], {})
                if scenario in {"member_index", "member_count", "duplicate_member", "wrong_parent", "wrong_envelope"}:
                    self.assertEqual(result["members"], [])
                    read_attrs.assert_not_called()

    def test_mock_and_unsupported_version_do_not_contact_native_bridge(self):
        for settings in (Settings(mode="mock", version="E3D21"), Settings(mode="bridge", version="E3D31")):
            with self.subTest(settings=settings), patch.object(BridgeClient, "health") as health:
                response = self.call({"refno": "=1/2"}, settings)
                self.assertTrue(response["isError"])
                health.assert_not_called()


if __name__ == "__main__":
    unittest.main()
