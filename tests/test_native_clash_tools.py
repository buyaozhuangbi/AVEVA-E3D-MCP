"""Offline MCP contracts for the native clash engine; never requests E3D."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class NativeClashToolsTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=True)
        self.client = BridgeClient(self.settings)
        self.server = create_server(self.settings)
        self.arguments = {"first_refnos": ["/A"], "second_refnos": ["=1/2"]}

    def invoke(self, arguments, server=None):
        return (server or self.server)._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "e3d_native_clash_check", "arguments": arguments}})["result"]

    @staticmethod
    def native_result(*, dry_run=False):
        return {"ok": True, "version": "E3D21", "result": {
            "success": True, "dry_run": dry_run,
            "native_check_attempted": not dry_run, "native_check_returned": not dry_run,
            "native_check_result": None if dry_run else True,
            "complete": not dry_run, "truncated": False, "scope_complete": not dry_run,
            "no_clear_verdict": True, "global_diagnostic_state_unchanged": None,
            "results": {name: {"native_count": None if dry_run else 0, "returned_count": 0,
                               "records": [], "complete": not dry_run, "truncated": False, "read_errors": []}
                        for name in ("clashes", "touches", "clearances", "not_proven")},
        }}

    def test_discovery_is_non_readonly_default_preview_and_has_no_capture_or_version(self):
        discovery = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertEqual(len(discovery), 119)
        definitions = {item["name"]: item for item in discovery}
        tool = definitions["e3d_native_clash_check"]
        self.assertFalse(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["idempotentHint"])
        self.assertTrue(tool["annotations"]["destructiveHint"])
        schema = tool["inputSchema"]
        self.assertEqual(set(schema["properties"]), {"first_refnos", "second_refnos", "clearance", "result_limit", "dry_run"})
        self.assertEqual(schema["required"], ["first_refnos", "second_refnos"])
        self.assertFalse(schema["additionalProperties"])
        self.assertIs(schema["properties"]["dry_run"]["default"], True)
        for field in ("first_refnos", "second_refnos"):
            self.assertEqual(schema["properties"][field]["minItems"], 1)
            self.assertEqual(schema["properties"][field]["maxItems"], 8)
            self.assertTrue(schema["properties"][field]["uniqueItems"])
        self.assertEqual(schema["properties"]["clearance"]["maximum"], 1000000)
        self.assertEqual(schema["properties"]["result_limit"]["default"], 50)
        self.assertEqual(schema["properties"]["result_limit"]["maximum"], 200)
        self.assertEqual(sum("capture_after" in item["inputSchema"]["properties"] for item in discovery), 19)
        for phrase in ("EQUI/SUBE", "BOX/CYLI/CONE", "not_proven", "no_clear_verdict", "unverified native units",
                       "not native computation", "UPTODATE", "NOWARNINGS", "input_spatial_maps_accepted",
                       "input_spatial_maps_up_to_date", "spatial_map_admission_policy", "spatial_map_not_accepted",
                       "zero discrepancies", "256 nodes", "depth 8", "preview only",
                       "unsupported_native_check_granularity", "changes native global diagnostic settings",
                       "not automatically restored", "verified descendants", "Db.IsExtract=true",
                       "unsupported_spatial_map_extract_scope", "extract_number=1", "override_policy",
                       "options.override=true", "(69,85)", "does not prove geometry was computed"):
            self.assertIn(phrase, tool["description"])

    def test_default_preview_allowed_without_commands_permission_and_preserves_native_result(self):
        server = create_server(Settings(mode="bridge", allow_commands=False))
        envelope = self.native_result(dry_run=True)
        with patch.object(BridgeClient, "native_clash_check", return_value=envelope) as check, \
                patch.object(BridgeClient, "capture_view") as capture:
            result = self.invoke(self.arguments, server=server)
        check.assert_called_once_with(**self.arguments, clearance=0.0, result_limit=50, dry_run=True)
        capture.assert_not_called()
        self.assertEqual(result["structuredContent"], envelope)
        self.assertFalse(result.get("isError", False))

    def test_real_execution_is_permission_gated_before_bridge(self):
        server = create_server(Settings(mode="bridge", allow_commands=False))
        with patch.object(BridgeClient, "native_clash_check") as check:
            result = self.invoke({**self.arguments, "dry_run": False}, server=server)
        check.assert_not_called()
        self.assertTrue(result["isError"])
        self.assertIn("E3D_MCP_ALLOW_COMMANDS", result["content"][0]["text"])

    def test_strict_input_validation_at_mcp_and_bridge_boundaries(self):
        invalid = [
            {}, {"first_refnos": ["/A"]}, {"second_refnos": ["/B"]},
            *[{**self.arguments, name: value} for name in ("first_refnos", "second_refnos")
              for value in (None, False, "/A", (), [], [f"/REF{i}" for i in range(9)], ["/A", " /A "], ["/A", "/a"])],
            *[{**self.arguments, "first_refnos": [value]} for value in (None, 1, True, [], {}, "", " ", "CE", "/", "=x/2",
                "/A\nDELETE CE", "/A B", "/A\x00X", "/A\x7fX", "/A\x85X")],
            {"first_refnos": [" /A "], "second_refnos": ["/A"]},
            {"first_refnos": ["/A"], "second_refnos": ["/a"]},
            *[{**self.arguments, "clearance": value} for value in (None, True, False, "0", -1, 1000001,
                float("inf"), -float("inf"), float("nan"), 10**400)],
            *[{**self.arguments, "result_limit": value} for value in (None, True, False, 0, 201, 1.0, "1")],
            *[{**self.arguments, "dry_run": value} for value in (None, 0, 1, "false")],
        ]
        with patch.object(BridgeClient, "native_clash_check") as check:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self.invoke(arguments)["isError"])
            for key in ("capture_after", "version", "expected_session_id", "request_id", "clearance_mm", "secret-extra-field"):
                result = self.invoke({**self.arguments, key: True})
                self.assertTrue(result["isError"])
                self.assertNotIn("secret-extra-field", json.dumps(result))
        check.assert_not_called()
        with patch.object(self.client, "_request") as request:
            for arguments in invalid:
                with self.subTest(direct_arguments=arguments), self.assertRaises((ValueError, TypeError)):
                    self.client.native_clash_check(**arguments)
        request.assert_not_called()

    def test_boundaries_normalize_explicit_text_and_do_not_claim_alias_identity(self):
        # Resolved alias identity belongs to the native validator, not Python.
        first = [f" /FIRST{i} " for i in range(8)]
        second = [f" ={i}/2 " for i in range(8)]
        with patch.object(BridgeClient, "native_clash_check", return_value=self.native_result()) as check:
            response = self.invoke({"first_refnos": first, "second_refnos": second,
                                    "clearance": 1000000, "result_limit": 200, "dry_run": False})
        check.assert_called_once_with(first_refnos=[item.strip() for item in first],
            second_refnos=[item.strip() for item in second], clearance=1000000.0, result_limit=200, dry_run=False)
        self.assertFalse(response.get("isError", False))

    def test_all_native_classifications_scope_unknown_and_truncation_are_preserved(self):
        envelope = self.native_result()
        native = envelope["result"]
        native.update(complete=False, truncated=True, scope_complete=False)
        for index, name in enumerate(("clashes", "touches", "clearances", "not_proven")):
            native["results"][name] = {"native_count": 10 + index, "returned_count": 1,
                "complete": False, "truncated": True, "read_errors": [], "records": [{
                "first": {"refno": "=1/1", "type": "BOX"}, "second": {"refno": "=2/9", "type": "CYLI"},
                "native_type": name, "instance_number": index, "scope": "unknown",
                "position": {"value": [1.0, 2.0, 3.0], "units": "native (unverified)", "frame": "native (unverified)"},
            }]}
        with patch.object(BridgeClient, "native_clash_check", return_value=envelope), \
                patch.object(BridgeClient, "geometry_bounds") as bounds, patch.object(BridgeClient, "capture_view") as capture:
            response = self.invoke({**self.arguments, "dry_run": False, "result_limit": 4})
        self.assertEqual(response["structuredContent"], envelope)
        self.assertTrue(response["structuredContent"]["result"]["no_clear_verdict"])
        bounds.assert_not_called()
        capture.assert_not_called()

    def test_native_granularity_and_map_gate_failures_are_preserved_without_retry(self):
        for reason in ("unsupported_native_check_granularity", "spatial_map_not_accepted",
                       "unsupported_spatial_map_extract_scope"):
            envelope = self.native_result()
            native = envelope["result"]
            native.update(success=False, complete=False, scope_complete=None,
                          native_check_attempted=False, native_check_returned=False, native_check_result=None,
                          errors=[{"path": "preflight", "code": reason, "message": "Native eligibility gate rejected execution."}],
                          input_databases=[{"number": 12345, "extract_number": 1,
                                            "is_extract": reason == "unsupported_spatial_map_extract_scope",
                                            "status": "WARNINGS" if reason == "spatial_map_not_accepted" else "UPTODATE"}],
                          spatial_map_extract_targeting_verified=False,
                          input_node_limit=256, input_depth_limit=8)
            with self.subTest(reason=reason), patch.object(BridgeClient, "_request", return_value=envelope) as request, \
                    patch.object(BridgeClient, "_recover_command_timeout") as recover, \
                    patch.object(BridgeClient, "command") as command:
                response = self.invoke({**self.arguments, "dry_run": False})
            request.assert_called_once()
            recover.assert_not_called()
            command.assert_not_called()
            self.assertTrue(response["isError"])
            self.assertEqual(response["structuredContent"], envelope)
            self.assertNotIn("available", response["structuredContent"]["result"])

    def test_accepted_nowarnings_remains_distinct_from_exact_uptodate(self):
        for status, value, up_to_date in (("UPTODATE", 1, True), ("NOWARNINGS", 2, False)):
            envelope = self.native_result()
            native = envelope["result"]
            native.update(input_spatial_maps_accepted=True, input_spatial_maps_up_to_date=up_to_date,
                          spatial_map_preflight_complete=True, geometry_classification_verified=False,
                          options={"override": True, "readback_verified": True},
                          override_policy={"enabled": True, "scope": "Accepted input DBs and exact EQUI scopes.",
                              "reason": "Local PML OFF returned (69,85); ON found the known positive clash.",
                              "effect": "Other MDB map warnings may be bypassed, but remain reported.",
                              "validation_scope": "PML evidence; SDK positive fixture pending."},
                          messages=[{"type": "SYSTEM", "description": "Other DB spatial-map warning."}],
                          spatial_map_admission_policy={
                              "mode": "experimental", "accepted_statuses": ["UPTODATE", "NOWARNINGS"],
                              "reason": "A local single-DB MAP CHECK reported zero discrepancies with NOWARNINGS.",
                              "validation_scope": "Controlled probes; native positive geometry remains unverified.",
                              "map_validity_verified": False},
                          input_databases=[{"number": 12345, "extract_number": 1, "is_extract": False,
                                            "status": status, "status_value": value, "up_to_date": up_to_date,
                                            "accepted": True}])
            with self.subTest(status=status), patch.object(BridgeClient, "_request", return_value=envelope) as request, \
                    patch.object(BridgeClient, "command") as command:
                response = self.invoke({**self.arguments, "dry_run": False})
            request.assert_called_once()
            command.assert_not_called()
            self.assertFalse(response.get("isError", False))
            self.assertEqual(response["structuredContent"], envelope)
            self.assertIs(response["structuredContent"]["result"]["input_spatial_maps_up_to_date"], up_to_date)
            self.assertTrue(response["structuredContent"]["result"]["input_spatial_maps_accepted"])
            self.assertEqual(response["structuredContent"]["result"]["override_policy"], native["override_policy"])
            self.assertTrue(response["structuredContent"]["result"]["options"]["override"])
            self.assertEqual(response["structuredContent"]["result"]["messages"], native["messages"])
            self.assertFalse(response["structuredContent"]["result"]["geometry_classification_verified"])
            self.assertTrue(response["structuredContent"]["result"]["no_clear_verdict"])

    def test_native_false_return_is_distinct_from_not_returned_and_stays_failure(self):
        envelope = self.native_result()
        envelope["result"].update(success=False, native_check_attempted=True, native_check_returned=True,
                                  native_check_result=False, complete=False)
        with patch.object(BridgeClient, "native_clash_check", return_value=envelope):
            response = self.invoke({**self.arguments, "dry_run": False})
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"], envelope)

    def test_empty_result_is_kept_without_fabricating_clear_or_distance(self):
        envelope = self.native_result()
        with patch.object(BridgeClient, "native_clash_check", return_value=envelope):
            response = self.invoke({**self.arguments, "dry_run": False})
        self.assertEqual(response["structuredContent"], envelope)
        for field in ("clear", "minimum_distance", "overlap_volume", "exact"):
            self.assertNotIn(field, response["structuredContent"]["result"])
        self.assertTrue(response["structuredContent"]["result"]["no_clear_verdict"])

    def test_transport_failures_have_uncertainty_without_recovery_cancel_or_replay(self):
        state = {"busy": True, "database_ready": False}
        for dry_run in (True, False):
            for failure in (BridgeError("timed out", transport_error=True, diagnostics={"state": state}),
                            BridgeError("connection closed", transport_error=True, diagnostics={"state": state}),
                            BridgeError("invalid JSON", response_invalid=True, diagnostics={"state": state})):
                with self.subTest(preview=dry_run, error=str(failure)), \
                        patch.object(BridgeClient, "_request", side_effect=failure) as request, \
                        patch.object(BridgeClient, "_recover_command_timeout") as recover, \
                        patch.object(BridgeClient, "cancel_command") as cancel, \
                        patch.object(BridgeClient, "capture_view") as capture:
                    response = self.invoke({**self.arguments, "dry_run": dry_run})
                request.assert_called_once()
                recover.assert_not_called()
                cancel.assert_not_called()
                capture.assert_not_called()
                self.assertTrue(response["isError"])
                metadata = response["structuredContent"]
                self.assertEqual(metadata["state"], state)
                self.assertIs(metadata["native_check_returned"], False if dry_run else None)
                self.assertIs(metadata["native_check_attempted"], False if dry_run else None)
                self.assertIsNone(metadata["native_check_result"])
                self.assertIs(metadata["outcome_uncertain"], not dry_run)
                self.assertFalse(metadata["retry_safe"])
                self.assertTrue(metadata["no_clear_verdict"])

    def test_http_failure_keeps_native_diagnostics_and_does_not_invent_return_value(self):
        error = BridgeError("native error", http_status=500, diagnostics={"state": {"fault_latched": True}})
        with patch.object(self.client, "_request", side_effect=error) as request, \
                patch.object(self.client, "_recover_command_timeout") as recover:
            with self.assertRaises(BridgeError) as caught:
                self.client.native_clash_check(**self.arguments, dry_run=False)
        self.assertIs(caught.exception, error)
        self.assertEqual(error.diagnostics, {"state": {"fault_latched": True}})
        request.assert_called_once()
        recover.assert_not_called()

    def test_http_contract_posts_once_and_preserves_raw_envelope(self):
        envelope = self.native_result()
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(envelope).encode())) as request:
            response = self.client.native_clash_check([" /A "], ["=1/2"], 0.25, 1, False, version="E3D21")
        request.assert_called_once()
        actual = request.call_args.args[0]
        self.assertEqual(actual.method, "POST")
        self.assertTrue(actual.full_url.endswith("/api/v1/clash/check"))
        self.assertEqual(json.loads(actual.data), {**self.arguments, "clearance": 0.25, "result_limit": 1, "dry_run": False})
        self.assertEqual(response, envelope)

    def test_mock_reports_unavailable_without_classifications_or_clear_claim(self):
        server = create_server(Settings(mode="mock", version="E3D21", allow_commands=True))
        for dry_run in (True, False):
            with self.subTest(preview=dry_run), patch.object(BridgeClient, "_request") as request:
                response = self.invoke({**self.arguments, "dry_run": dry_run}, server=server)
            request.assert_not_called()
            self.assertTrue(response["isError"])
            native = response["structuredContent"]["result"]
            self.assertFalse(native["available"])
            self.assertFalse(native["success"])
            self.assertFalse(native["native_check_attempted"])
            self.assertFalse(native["native_check_returned"])
            self.assertIsNone(native["native_check_result"])
            self.assertTrue(native["no_clear_verdict"])
            self.assertFalse(native["complete"])
            self.assertNotIn("results", native)


if __name__ == "__main__":
    unittest.main()
