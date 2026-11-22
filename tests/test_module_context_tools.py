"""Offline public-contract and bridge-isolation checks for module context."""
from copy import deepcopy
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.module_context import PATH, TOOL, VERSION, module_context, register_module_context
from aveva_e3d_mcp.paragon_schema import TOOL as SCHEMA_TOOL, register_paragon_catalogue_schema
from aveva_e3d_mcp.protocol import McpServer

SESSION = "12345678-1234-4234-8234-123456789abc"
BUILD = "abcdef01-2345-4678-9234-abcdef012345"


def boundary(*, module=None, project=None, mdb=None, current_element=None):
    return {
        "session_id": SESSION,
        "bridge_build": BUILD,
        "module": module or {"status": "present", "name": "PARAGON", "number": 41, "description": "Catalogue"},
        "project": project or {"status": "present", "name": "PLANT", "user": "SYSTEM", "project_number": "7", "aduuid": "project-ad-7"},
        "mdb": mdb or {"status": "present", "name": "ADMIN", "element_is_null": False, "element": {
            "refno": "=1/1", "type": "MDB", "db_type": "Admin", "db_type_name": "Admin", "db_type_code": 0,
        }},
        "current_element": current_element or {"status": "present", "is_null": False, "refno": "=1/2", "type": "WORL"},
    }


def observed(before=None, after=None, *, errors=None, success=True, complete=True, unchanged=True):
    after = boundary() if after is None else deepcopy(after)
    before = boundary() if before is None else deepcopy(before)
    return {
        "ok": True,
        "version": VERSION,
        "session_id": SESSION,
        "bridge_build": BUILD,
        "result": {
            "operation": "module_context",
            "available": True,
            "success": success,
            "complete": complete,
            "read_only": True,
            "single_dispatch": True,
            "atomic_snapshot": False,
            "module": deepcopy(after["module"]),
            "context": {"before": before, "after": after, "unchanged": unchanged},
            "scope": {
                "kind": "active_module_identity",
                "authorization_status": "unknown",
                "admin_database_creation_permission_verified": None,
                "paragon_write_permission_verified": None,
                "database_lock_status": "not_assessed",
            },
            "read_errors": [] if errors is None else deepcopy(errors),
        },
    }


class ModuleContextToolTests(unittest.TestCase):
    def server(self, settings=None):
        bridge = BridgeClient(settings or Settings(mode="bridge", version=VERSION, allow_commands=False))
        server = McpServer("module-context-test", "1")
        register_module_context(server, bridge)
        return server, bridge

    def call(self, server, arguments=None):
        return server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": TOOL, "arguments": {} if arguments is None else arguments,
        }})["result"]

    def test_tool_is_zero_argument_read_only_and_observes_one_bridge_route(self):
        server, bridge = self.server()
        catalog = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]
        self.assertEqual(len(catalog), 1)
        tool = catalog[0]
        self.assertEqual(tool["name"], TOOL)
        self.assertEqual(tool["inputSchema"], {"type": "object", "properties": {}, "additionalProperties": False})
        self.assertEqual(tool["annotations"], {"readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False})
        for text in ("Admin database creation", "PARAGON write permission", "unknown", "database lock"):
            self.assertIn(text.lower(), tool["description"].lower())

        with patch.object(bridge, "_request", return_value=observed()) as request:
            response = self.call(server)
        request.assert_called_once_with("GET", PATH, version=VERSION)
        self.assertFalse(response.get("isError", False))
        result = response["structuredContent"]["result"]
        self.assertTrue(result["complete"])
        self.assertEqual(result["module"]["name"], "PARAGON")
        self.assertEqual(result["context"]["before"]["current_element"]["type"], "WORL")
        self.assertEqual(result["context"]["before"]["mdb"]["name"], "ADMIN")
        self.assertIsNone(result["scope"]["admin_database_creation_permission_verified"])
        self.assertIsNone(result["scope"]["paragon_write_permission_verified"])
        json.dumps(response["structuredContent"], allow_nan=False)

    def test_registered_paragon_schema_names_the_limits_and_non_authorizing_create_observation(self):
        server = McpServer("paragon-schema-test", "1")
        register_paragon_catalogue_schema(server, BridgeClient(Settings(mode="mock", version=VERSION)))
        tool, = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]
        self.assertEqual(tool["name"], SCHEMA_TOOL)
        self.assertEqual(tool["annotations"]["readOnlyHint"], True)
        self.assertEqual(tool["inputSchema"]["required"], ["refno"])
        for premise in ("`sdk_creatable_now`", "current parent's SDK state", "does not authorize",
                        "`future_child_attributes_observed=false`", "already materialized before output limits"):
            self.assertIn(premise, tool["description"])

    def test_unknown_arguments_are_rejected_before_bridge_reads(self):
        server, bridge = self.server()
        with patch.object(bridge, "_request", side_effect=AssertionError("Unexpected bridge request")) as request:
            for arguments in ({"module": "ADMIN"}, {"version": VERSION}, {"include_permissions": True}):
                with self.subTest(arguments=arguments):
                    response = self.call(server, arguments)
                    self.assertTrue(response.get("isError", False))
                    self.assertIn("takes no arguments", response["content"][0]["text"])
            request.assert_not_called()

    def test_mock_other_versions_and_unimplemented_endpoint_are_unavailable_without_claims(self):
        cases = (Settings(mode="mock", version=VERSION), Settings(mode="bridge", version="E3D31"))
        for settings in cases:
            with self.subTest(mode=settings.mode, version=settings.version):
                server, bridge = self.server(settings)
                with patch.object(bridge, "_request", side_effect=AssertionError("Unsupported context was requested")) as request:
                    result = self.call(server)["structuredContent"]["result"]
                self.assertFalse(result["available"])
                self.assertFalse(result["complete"])
                self.assertEqual(result["module"]["status"], "unknown")
                self.assertIsNone(result["scope"]["admin_database_creation_permission_verified"])
                request.assert_not_called()

        server, bridge = self.server()
        with patch.object(bridge, "_request", side_effect=BridgeError("HTTP 404", http_status=404)):
            result = self.call(server)["structuredContent"]["result"]
        self.assertFalse(result["available"])
        self.assertEqual(result["read_errors"][0]["code"], "native_feature_unavailable")

    def test_stable_verified_absence_is_distinct_from_unknown_context(self):
        server, bridge = self.server()
        absent = boundary(
            module={"status": "absent", "name": None, "number": None, "description": None},
            project={"status": "not_open", "name": None, "user": None, "project_number": None, "aduuid": None},
            mdb={"status": "absent", "name": None, "element_is_null": True, "element": None},
            current_element={"status": "absent", "is_null": True, "refno": None, "type": None},
        )
        with patch.object(bridge, "_request", return_value=observed(absent, absent)):
            result = self.call(server)["structuredContent"]["result"]
        self.assertTrue(result["complete"])
        self.assertEqual(result["context"]["after"]["module"]["status"], "absent")
        self.assertEqual(result["context"]["after"]["mdb"]["status"], "absent")
        self.assertEqual(result["context"]["after"]["current_element"]["status"], "absent")

        for absent_path in ("mdb", "current_element"):
            forged_absence = deepcopy(observed(absent, absent))
            for sample in (forged_absence["result"]["context"]["before"],
                           forged_absence["result"]["context"]["after"]):
                sample[absent_path]["element_is_null" if absent_path == "mdb" else "is_null"] = None
            with self.subTest(absent_path=absent_path), patch.object(
                    bridge, "_request", return_value=forged_absence):
                result = self.call(server)["structuredContent"]["result"]
            self.assertFalse(result["available"])
            self.assertEqual(result["read_errors"][0]["code"], "invalid_native_response")

        unknown = boundary(
            module={"status": "unknown", "name": None, "number": None, "description": None},
            project={"status": "unknown", "name": "PLANT", "user": "SYSTEM", "project_number": None, "aduuid": None},
            mdb={"status": "unknown", "name": None, "element_is_null": None, "element": None},
            current_element={"status": "unknown", "is_null": None, "refno": None, "type": None},
        )
        errors = [{"stage": "before.module", "message": "Getter unavailable"}]
        with patch.object(bridge, "_request", return_value=observed(
                unknown, unknown, errors=errors, success=False, complete=False, unchanged=None)):
            result = self.call(server)["structuredContent"]["result"]
        self.assertFalse(result["complete"])
        self.assertEqual(result["context"]["after"]["module"]["status"], "unknown")
        self.assertEqual(result["read_errors"], errors)

    def test_boundary_drift_and_malformed_or_unbounded_responses_fail_closed(self):
        server, bridge = self.server()
        changed = boundary()
        changed["module"]["name"] = "ADMIN"
        cases = [
            observed(after=changed, success=False, complete=False, unchanged=False),
            observed(errors=[{"stage": "x" * 321, "message": "y"}], success=False, complete=False, unchanged=None),
            observed(errors=[{"stage": "error", "message": "e" * 321}], success=False, complete=False, unchanged=None),
            observed(errors=[{"stage": "error", "message": "e"}] * 25, success=False, complete=False, unchanged=None),
        ]
        with patch.object(bridge, "_request", return_value=cases[0]):
            drift = self.call(server)["structuredContent"]["result"]
        self.assertTrue(drift["available"])
        self.assertFalse(drift["complete"])
        self.assertIs(drift["context"]["unchanged"], False)
        for reply in cases[1:]:
            with self.subTest(error_count=len(reply["result"]["read_errors"])):
                with patch.object(bridge, "_request", return_value=reply):
                    result = self.call(server)["structuredContent"]["result"]
                self.assertFalse(result["available"])
                self.assertEqual(result["read_errors"][0]["code"], "invalid_native_response")

    def test_typed_mdb_and_project_identity_drift_remain_available_partial_observations(self):
        server, bridge = self.server()
        before = boundary()

        changed_mdb = boundary()
        changed_mdb["mdb"]["element"]["refno"] = "=1/10"
        cases = [(before, changed_mdb)]

        for field, value in (("project_number", "8"), ("aduuid", "project-ad-8")):
            changed_project = boundary()
            changed_project["project"][field] = value
            cases.append((before, changed_project))

        for first, last in cases:
            with self.subTest(first=first["project"], last=last["project"], mdb=last["mdb"]):
                with patch.object(bridge, "_request", return_value=observed(
                        first, last, success=False, complete=False, unchanged=False)):
                    result = self.call(server)["structuredContent"]["result"]
                self.assertTrue(result["available"])
                self.assertFalse(result["success"])
                self.assertFalse(result["complete"])
                self.assertIs(result["context"]["unchanged"], False)
                self.assertEqual(result["context"]["after"]["mdb"]["name"], "ADMIN")

    def test_partial_current_element_identity_is_preserved_and_unknown_cannot_be_complete(self):
        server, bridge = self.server()
        partial_mdb = boundary(mdb={
            "status": "unknown", "name": "ADMIN", "element_is_null": False,
            "element": {"refno": "=1/1", "type": None, "db_type": None,
                        "db_type_name": None, "db_type_code": None},
        })
        mdb_errors = [{"stage": "after.mdb.element.type", "message": "MDB element type lookup failed."}]
        with patch.object(bridge, "_request", return_value=observed(
                partial_mdb, partial_mdb, errors=mdb_errors, success=False, complete=False, unchanged=None)):
            mdb_result = self.call(server)["structuredContent"]["result"]
        self.assertTrue(mdb_result["available"])
        self.assertFalse(mdb_result["complete"])
        self.assertEqual(mdb_result["context"]["after"]["mdb"]["element"]["refno"], "=1/1")
        self.assertEqual(mdb_result["read_errors"], mdb_errors)

        partial = boundary(current_element={"status": "unknown", "is_null": False, "refno": "=1/2", "type": None})
        errors = [{"stage": "after.current_element.type", "message": "Current element type lookup failed."}]
        reply = observed(partial, partial, errors=errors, success=False, complete=False, unchanged=None)
        with patch.object(bridge, "_request", return_value=reply):
            result = self.call(server)["structuredContent"]["result"]
        self.assertTrue(result["available"])
        self.assertFalse(result["success"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["context"]["after"]["current_element"], partial["current_element"])
        self.assertEqual(result["read_errors"], errors)

        forged = deepcopy(reply)
        forged["result"]["complete"] = True
        with patch.object(bridge, "_request", return_value=forged):
            result = self.call(server)["structuredContent"]["result"]
        self.assertFalse(result["available"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["read_errors"][0]["code"], "invalid_native_response")

    def test_native_fault_is_preserved_and_other_transport_errors_propagate(self):
        server, bridge = self.server()
        fault_cases = (
            {"native_fault_latched": True},
            {"native_fault": True},
            {"has_fault": True},
            {"error_code": "native_database_fault"},
            {"error_code": "native_fault"},
            {"nested": [{"has_fault": True}]},
        )
        for diagnostics in fault_cases:
            fault = BridgeError("Native fault", http_status=404, diagnostics=diagnostics)
            with patch.object(bridge, "_request", side_effect=fault):
                with self.assertRaises(BridgeError) as raised:
                    module_context(bridge, {})
            self.assertIs(raised.exception, fault)
            with patch.object(bridge, "_request", side_effect=fault) as request:
                response = self.call(server)
            request.assert_called_once_with("GET", PATH, version=VERSION)
            self.assertTrue(response.get("isError", False))
            self.assertFalse(response["structuredContent"]["ok"])
            for key, value in diagnostics.items():
                self.assertEqual(response["structuredContent"][key], value)

        raw = observed()
        raw["result"]["read_errors"] = [{"details": {"error_code": "native_database_fault"}}]
        with patch.object(bridge, "_request", return_value=raw) as request:
            with self.assertRaises(BridgeError) as raised:
                module_context(bridge, {})
        request.assert_called_once_with("GET", PATH, version=VERSION)
        self.assertIs(raised.exception.diagnostics, raw)
        with patch.object(bridge, "_request", return_value=raw) as request:
            response = self.call(server)
        request.assert_called_once_with("GET", PATH, version=VERSION)
        self.assertTrue(response.get("isError", False))
        self.assertEqual(response["structuredContent"]["result"], raw["result"])

        failure = BridgeError("bridge offline", transport_error=True)
        with patch.object(bridge, "_request", side_effect=failure):
            with self.assertRaises(BridgeError) as raised:
                module_context(bridge, {})
        self.assertIs(raised.exception, failure)


if __name__ == "__main__":
    unittest.main()
