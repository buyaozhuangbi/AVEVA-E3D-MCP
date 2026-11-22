"""Submitted writes need execution evidence even when error JSON is readable."""
from __future__ import annotations

import copy
import io
import json
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class UninformativeWriteFailureTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=True)
        self.state = {"busy": False, "database_ready": False, "recovery_required": True,
                      "native_database_fault": {"has_fault": True, "code": 538}}

    def envelope(self, **fields):
        return {"ok": False, "version": "E3D21", "session_id": "session-one",
                "error": "Native operation failed", "state": copy.deepcopy(self.state), **fields}

    @staticmethod
    def http_patch(envelope, http_error):
        encoded = json.dumps(envelope).encode("utf-8")
        if http_error:
            failure = HTTPError("http://bridge/api/v1/test", 500, "failure", {}, io.BytesIO(encoded))
            return patch("urllib.request.urlopen", side_effect=failure)
        return patch("urllib.request.urlopen", return_value=io.BytesIO(encoded))

    def call(self, tool, arguments, envelope, http_error):
        original = copy.deepcopy(envelope)
        server = create_server(self.settings)
        with ExitStack() as stack:
            http = stack.enter_context(self.http_patch(envelope, http_error))
            quiet = [stack.enter_context(patch.object(BridgeClient, name)) for name in
                     ("_recover_command_timeout", "cancel_command", "capture_view", "command_state", "health")]
            response = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments}})["result"]
        http.assert_called_once()
        for method in quiet:
            method.assert_not_called()
        self.assertEqual(envelope, original)
        self.assertTrue(response["isError"])
        return response

    def test_generic_fault_missing_empty_or_nonexecution_receipts_stay_uncertain(self):
        envelopes = [self.envelope(), self.envelope(result={}), self.envelope(result=[]),
                     self.envelope(result={"success": False}),
                     self.envelope(result={"success": False, "retry_safe": True}),
                     self.envelope(result={"success": False, "retry_safe": False}),
                     self.envelope(result={"success": False, "write_attempted": "false"}),
                     self.envelope(result={"success": False, "creation_attempted": False})]
        for envelope in envelopes:
            for http_error in (False, True):
                for capture in (False, True):
                    with self.subTest(result=envelope.get("result"), http_error=http_error, capture=capture):
                        response = self.call("e3d_set_attribute", {
                            "refno": "=1/2", "attribute": "DESC", "value": "changed",
                            "dry_run": False, "capture_after": capture}, envelope, http_error)
                        operation = response["structuredContent"]
                        self.assertTrue(operation["outcome_uncertain"])
                        self.assertFalse(operation["retry_safe"])
                        self.assertEqual(operation["state"], self.state)
                        if isinstance(envelope.get("result"), dict):
                            self.assertEqual(operation["result"], envelope["result"])
                        if http_error:
                            self.assertEqual(operation["http_status"], 500)
                        if capture:
                            self.assertEqual(operation["receipt"]["capture"]["reason"], "operation_outcome_unknown")

    def test_current_idle_busy_or_fault_state_is_not_execution_evidence(self):
        for state in ({"busy": False, "database_ready": True}, {"busy": True}, self.state):
            with self.subTest(state=state):
                response = self.call("e3d_set_attribute", {
                    "refno": "=1/2", "attribute": "DESC", "value": "changed", "dry_run": False},
                    self.envelope(state=state), True)
                self.assertTrue(response["structuredContent"]["outcome_uncertain"])

    def test_complete_failure_json_is_not_mislabeled_transport_or_parser_failure(self):
        client = BridgeClient(self.settings)
        for http_error in (False, True):
            with self.subTest(http_error=http_error), self.http_patch(self.envelope(), http_error):
                with self.assertRaises(BridgeError) as caught:
                    client.set_attribute("=1/2", "DESC", "changed", False)
            error = caught.exception
            self.assertTrue(error.outcome_uncertain)
            self.assertFalse(error.transport_error)
            self.assertFalse(error.response_invalid)
            self.assertEqual(error.http_status, 500 if http_error else None)
            self.assertEqual(error.diagnostics, {"state": self.state})

    def test_native_known_outcomes_and_no_attempt_receipts_are_preserved(self):
        receipts = [
            {"success": False, "write_attempted": False, "write_applied": False},
            {"success": False, "outcome_uncertain": False, "retry_safe": True},
            {"success": False, "write_attempted": True, "write_applied": None, "retry_safe": False},
            {"success": False, "write_attempted": True, "write_applied": True},
            {"success": False, "busy": True},
            {"success": True, "write_applied": True},
        ]
        for native in receipts:
            for http_error in (False, True):
                with self.subTest(native=native, http_error=http_error):
                    response = self.call("e3d_set_attribute", {
                        "refno": "=1/2", "attribute": "DESC", "value": "changed",
                        "dry_run": False, "capture_after": True}, self.envelope(result=native), http_error)
                    operation = response["structuredContent"]
                    self.assertIsNot(operation.get("outcome_uncertain"), True)
                    self.assertEqual(operation["result"], native)
                    self.assertEqual(operation["receipt"]["capture"]["reason"], "operation_failed")

    def test_exact_native_attempt_fields_are_used_for_each_route(self):
        contracts = {
            "/api/v1/element/create": {"creation_attempted": False},
            "/api/v1/element/copy": {"creation_attempted": False},
            "/api/v1/panel/create": {"creation_attempted": False},
            "/api/v1/element/delete": {"delete_attempted": False},
            "/api/v1/element/attribute": {"write_attempted": False},
            "/api/v1/element/transform": {"write_attempted": False},
            "/api/v1/view/graphical-selection": {"write_attempted": False},
            "/api/v1/clash/check": {"native_check_attempted": False},
            "/api/v1/view/fit": {"write_attempted": False, "drawlist_write_attempted": False},
            "/api/v1/drawlist/visual": {"write_attempted": False, "add_attempted": False},
        }
        client = BridgeClient(self.settings)
        for path, fields in contracts.items():
            with self.subTest(path=path), self.http_patch(self.envelope(result={"success": False, **fields}), True):
                with self.assertRaises(BridgeError) as caught:
                    client._request("POST", path, {"dry_run": False})
            self.assertFalse(caught.exception.outcome_uncertain)
            self.assertEqual(caught.exception.diagnostics["result"], {"success": False, **fields})
        for path, fields in (
            ("/api/v1/view/fit", {"write_attempted": False, "drawlist_write_attempted": True}),
            ("/api/v1/drawlist/visual", {"write_attempted": False, "add_attempted": True}),
        ):
            with self.subTest(path=path, partial=fields), self.http_patch(self.envelope(result=fields), True):
                with self.assertRaises(BridgeError) as caught:
                    client._request("POST", path, {"dry_run": False})
            self.assertTrue(caught.exception.outcome_uncertain)

    def test_script_and_commands_keep_lookup_without_forwarding_private_results(self):
        request_id = "12345678-1234-1234-abcd-1234567890ab"
        cases = [
            ("e3d_execute_pml", {"command": "NEW BOX"}),
            ("e3d_execute_pml_batch", {"commands": ["NEW BOX"]}),
            ("e3d_execute_pml_script", {"source": "NEW BOX", "expected_session_id": "session-one",
                                        "request_id": request_id}),
        ]
        for tool, args in cases:
            for http_error in (False, True):
                for capture in (False, True):
                    with self.subTest(tool=tool, http_error=http_error, capture=capture):
                        envelope = self.envelope(result={"success": False, "source": "private-source",
                                                         "command": "private-command", "password": "private-password"})
                        response = self.call(tool, {**args, "dry_run": False, "capture_after": capture}, envelope, http_error)
                        operation = response["structuredContent"]
                        self.assertTrue(operation["outcome_uncertain"])
                        self.assertFalse(operation["retry_safe"])
                        self.assertNotIn("private-", json.dumps(response))
                        if tool == "e3d_execute_pml_script":
                            self.assertEqual(operation["pml_script_request"], {
                                "request_id": request_id, "expected_session_id": "session-one",
                                "status_tool": "e3d_pml_script_status"})
                        if capture:
                            self.assertEqual(operation["receipt"]["capture"]["reason"], "operation_outcome_unknown")

    def test_script_known_no_execution_and_duplicate_submission_remain_authoritative(self):
        args = {"source": "NEW BOX", "expected_session_id": "session-one",
                "request_id": "12345678-1234-1234-abcd-1234567890ab", "dry_run": False, "capture_after": True}
        for fields in ({"execution_attempted": False}, {"outcome_uncertain": False},
                       {"this_submission_executed": False, "duplicate_request": True}):
            for http_error in (False, True):
                with self.subTest(fields=fields, http_error=http_error):
                    response = self.call("e3d_execute_pml_script", args, self.envelope(**fields), http_error)
                    self.assertIsNot(response["structuredContent"].get("outcome_uncertain"), True)
                    self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "operation_failed")

    def test_private_pml_result_outcome_flags_survive_without_forwarding_the_result(self):
        request_id = "12345678-1234-1234-abcd-1234567890ab"
        cases = [
            ("e3d_execute_pml", {"command": "NEW BOX"}),
            ("e3d_execute_pml_batch", {"commands": ["NEW BOX"]}),
            ("e3d_execute_pml_script", {"source": "NEW BOX", "expected_session_id": "session-one",
                                        "request_id": request_id}),
        ]
        for tool, args in cases:
            for uncertain in (False, True):
                for http_error in (False, True):
                    for capture in (False, True):
                        with self.subTest(tool=tool, uncertain=uncertain, http_error=http_error, capture=capture):
                            envelope = self.envelope(result={"success": False, "outcome_uncertain": uncertain,
                                "retry_safe": False, "source": "private-source", "command": "private-command"})
                            response = self.call(tool, {**args, "dry_run": False, "capture_after": capture},
                                                 envelope, http_error)
                            operation = response.get("structuredContent", {})
                            self.assertNotIn("private-", json.dumps(response))
                            self.assertNotIn("result", operation)
                            if uncertain:
                                self.assertTrue(operation["outcome_uncertain"])
                                self.assertFalse(operation["retry_safe"])
                            else:
                                self.assertIsNot(operation.get("outcome_uncertain"), True)
                            if capture:
                                self.assertEqual(operation["receipt"]["capture"]["reason"],
                                    "operation_outcome_unknown" if uncertain else "operation_failed")
                            if tool == "e3d_execute_pml_script":
                                self.assertEqual(operation["pml_script_request"]["request_id"], request_id)

    def test_command_session_guard_is_preserved_but_unsupported_attempt_flags_are_not_evidence(self):
        for tool, arguments in (("e3d_execute_pml", {"command": "NEW BOX"}),
                                ("e3d_execute_pml_batch", {"commands": ["NEW BOX"]})):
            with self.subTest(tool=tool):
                response = self.call(tool, {**arguments, "dry_run": False},
                    self.envelope(result={"success": False, "write_attempted": False}), True)
                self.assertTrue(response["structuredContent"]["outcome_uncertain"])
        response = self.call("e3d_execute_pml", {
            "command": "NEW BOX", "dry_run": False, "expected_session_id": "old-session",
            "capture_after": True}, self.envelope(session_id="new-session"), True)
        self.assertIsNot(response["structuredContent"].get("outcome_uncertain"), True)
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "operation_failed")

    def test_previews_and_read_only_routes_never_claim_submitted_mutation(self):
        for http_error in (False, True):
            for capture in (False, True):
                with self.subTest(http_error=http_error, capture=capture):
                    response = self.call("e3d_set_attribute", {
                        "refno": "=1/2", "attribute": "DESC", "value": "preview",
                        "dry_run": True, "capture_after": capture}, self.envelope(), http_error)
                    self.assertIsNot(response.get("structuredContent", {}).get("outcome_uncertain"), True)
        client = BridgeClient(self.settings)
        for method, path, payload in (("GET", "/health", None), ("GET", "/api/v1/project", None),
                                      ("GET", "/api/v1/pml-script/status", None),
                                      ("POST", "/api/v1/query", {"dry_run": False}),
                                      ("POST", "/api/v1/element/create/extra", {"dry_run": False})):
            with self.subTest(method=method, path=path), self.http_patch(self.envelope(), True):
                with self.assertRaises(BridgeError) as caught:
                    client._request(method, path, payload)
            self.assertFalse(caught.exception.outcome_uncertain)

    def test_failed_preflight_health_is_not_treated_as_a_submitted_script(self):
        server = create_server(self.settings)
        with self.http_patch(self.envelope(), True) as http:
            response = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "e3d_execute_pml_script", "arguments": {"source": "NEW BOX", "dry_run": False}}})["result"]
        http.assert_called_once()
        self.assertEqual(http.call_args.args[0].get_method(), "GET")
        self.assertEqual(http.call_args.args[0].selector, "/health")
        self.assertFalse(response["structuredContent"]["execution_attempted"])
        self.assertFalse(response["structuredContent"]["outcome_uncertain"])

    def test_always_mutating_view_routes_do_not_require_a_dry_run_argument(self):
        for tool in ("e3d_fit_view", "e3d_restore_view"):
            for http_error in (False, True):
                with self.subTest(tool=tool, http_error=http_error):
                    response = self.call(tool, {"capture_after": True}, self.envelope(), http_error)
                    operation = response["structuredContent"]
                    self.assertTrue(operation["outcome_uncertain"])
                    self.assertFalse(operation["retry_safe"])
                    self.assertEqual(operation["receipt"]["capture"]["reason"], "operation_outcome_unknown")

    def test_selection_and_native_check_handlers_propagate_unknown_failure(self):
        for tool, arguments in (
            ("e3d_set_graphical_selection", {"refnos": ["=1/2"], "expected_revision": "a" * 64}),
            ("e3d_native_clash_check", {"first_refnos": ["=1/2"], "second_refnos": ["=1/3"]}),
        ):
            for http_error in (False, True):
                with self.subTest(tool=tool, http_error=http_error):
                    response = self.call(tool, {**arguments, "dry_run": False}, self.envelope(result={}), http_error)
                    self.assertTrue(response["structuredContent"]["outcome_uncertain"])
                    self.assertFalse(response["structuredContent"]["retry_safe"])

    def test_successful_envelopes_and_configuration_failures_are_unchanged(self):
        client = BridgeClient(self.settings)
        envelope = {"ok": True, "version": "E3D21", "result": {"success": True, "write_applied": True}}
        with self.http_patch(envelope, False):
            self.assertEqual(client.set_attribute("=1/2", "DESC", "changed", False), envelope)
        with patch("urllib.request.urlopen") as http:
            with self.assertRaises(ValueError):
                client._request("POST", "/api/v1/element/attribute", {"dry_run": False}, version="E3D31")
        http.assert_not_called()


if __name__ == "__main__":
    unittest.main()
