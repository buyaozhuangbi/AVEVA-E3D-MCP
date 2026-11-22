"""Offline context workflow tests: only synthetic method-level bridge fixtures."""

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
from aveva_e3d_mcp.context_snapshot import context_snapshot


def healthy():
    return {"ok": True, "version": "E3D21", "session_id": "session-one", "bridge_build": "build-one",
            "connected": True, "state": {
                "database_ready": True, "busy": False, "recovery_required": False,
                "native_database_fault": {"has_fault": False},
                "execution": {"dispatcher_ready": True, "native_operation_active": False},
                "native_dialogs": {"complete": True, "standard_dialog_present": False},
                "pml_alerts": {"complete": True, "alert_present": False}}}


def project():
    return {"ok": True, "version": "E3D21", "project": {"connected": True, "recovery_only": False,
            "name": "APS", "mdb": "ALL", "user": "SYSTEM", "current_element": "=123/456"}}


class FakeBridge:
    def __init__(self, before=None, project_value=None, after=None):
        self.events = []
        self.values = [healthy() if before is None else before,
                       project() if project_value is None else project_value,
                       healthy() if after is None else after]

    def _next(self, method):
        expected = ("health", "project", "health")[len(self.events)]
        if method != expected:
            raise AssertionError(f"Unexpected call {method}; expected {expected}")
        self.events.append(method)
        value = self.values[len(self.events) - 1]
        if isinstance(value, BaseException):
            raise value
        return value

    def health(self):
        return self._next("health")

    def project(self):
        return self._next("project")

    def __getattr__(self, name):
        raise AssertionError(f"Non-workflow bridge access: {name}")


class ContextSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=False)

    def run_snapshot(self, **overrides):
        bridge = FakeBridge(**overrides)
        response = context_snapshot(bridge, self.settings)
        self.assertLessEqual(len(bridge.events), 3)
        json.dumps(response, allow_nan=False)
        return response, bridge

    def assert_blocked(self, before, expected="unknown"):
        response, bridge = self.run_snapshot(before=before)
        result = response["result"]
        self.assertEqual(bridge.events, ["health"])
        self.assertEqual(result["readiness"]["before"], expected)
        self.assertEqual(result["readiness"]["after"], "unknown")
        self.assertFalse(result["context_complete"])
        self.assertFalse(result["context"]["fresh"])
        self.assertEqual(result["session_coherence"]["status"], "unverified")
        self.assertEqual([item["stage"] for item in result["skipped"]], ["project", "health_after"])
        self.assertNotIn("success", result)
        return response

    def test_valid_order_and_complete_nonatomic_contract(self):
        response, bridge = self.run_snapshot()
        result = response["result"]
        self.assertTrue(response["ok"])
        self.assertTrue(result["workflow_completed"])
        self.assertTrue(result["context_complete"])
        self.assertEqual(bridge.events, ["health", "project", "health"])
        self.assertEqual(result["calls"], ["health_before", "project", "health_after"])
        self.assertEqual(result["readiness"]["before"], "ready")
        self.assertEqual(result["readiness"]["after"], "ready")
        self.assertEqual(result["context"]["project"], "APS")
        self.assertEqual(result["context"]["mdb"], "ALL")
        self.assertEqual(result["session_coherence"]["status"], "consistent")
        self.assertFalse(result["session_coherence"]["atomic"])
        self.assertFalse(result["session_coherence"]["project_session_bound"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["skipped"], [])

    def test_every_boolean_gate_missing_or_malformed_blocks_project(self):
        paths = ["connected", "state.database_ready", "state.busy", "state.recovery_required",
                 "state.native_database_fault.has_fault", "state.execution.dispatcher_ready",
                 "state.execution.native_operation_active", "state.native_dialogs.complete",
                 "state.native_dialogs.standard_dialog_present", "state.pml_alerts.complete",
                 "state.pml_alerts.alert_present"]
        for path in paths:
            for invalid in ("missing", None, "false", 0, 1, [], {}):
                with self.subTest(path=path, invalid=invalid):
                    status = healthy()
                    target = status
                    pieces = path.split(".")
                    for key in pieces[:-1]:
                        target = target[key]
                    if invalid == "missing":
                        del target[pieces[-1]]
                    else:
                        target[pieces[-1]] = invalid
                    response = self.assert_blocked(status)
                    self.assertTrue(response["ok"])
                    self.assertTrue(response["result"]["workflow_completed"])

    def test_every_gate_container_missing_or_malformed_blocks_project(self):
        for path in ("state", "state.native_database_fault", "state.execution",
                     "state.native_dialogs", "state.pml_alerts"):
            for value in ("missing", None, False, [], "state"):
                with self.subTest(path=path, value=value):
                    status = healthy()
                    pieces = path.split(".")
                    target = status if len(pieces) == 1 else status[pieces[0]]
                    if value == "missing":
                        del target[pieces[-1]]
                    else:
                        target[pieces[-1]] = value
                    self.assert_blocked(status)

    def test_explicit_unready_boolean_observations_are_data(self):
        paths = ["connected", "state.database_ready", "state.busy", "state.recovery_required",
                 "state.native_database_fault.has_fault", "state.execution.dispatcher_ready",
                 "state.execution.native_operation_active", "state.native_dialogs.complete",
                 "state.native_dialogs.standard_dialog_present", "state.pml_alerts.complete",
                 "state.pml_alerts.alert_present"]
        for path in paths:
            with self.subTest(path=path):
                status = healthy()
                pieces = path.split(".")
                target = status
                for key in pieces[:-1]:
                    target = target[key]
                target[pieces[-1]] = not target[pieces[-1]]
                response = self.assert_blocked(status, "unknown" if path.endswith(".complete") else "not_ready")
                self.assertTrue(response["ok"])
                self.assertEqual(response["result"]["errors"], [])

    def test_explicit_not_ready_wins_over_additional_unknowns(self):
        status = healthy()
        status["state"]["busy"] = True
        del status["session_id"]
        self.assert_blocked(status, "not_ready")

    def test_all_native_identity_fields_required_before_project(self):
        for field in ("version", "session_id", "bridge_build"):
            for value in ("missing", None, "", " ", 123, False, []):
                with self.subTest(field=field, value=value):
                    status = healthy()
                    if value == "missing":
                        del status[field]
                    else:
                        status[field] = value
                    self.assert_blocked(status)
        status = healthy()
        status["version"] = "E3D22"
        self.assert_blocked(status, "not_ready")

    def test_initial_read_failure_and_bad_envelopes_are_errors(self):
        for value in (RuntimeError("offline"), [], "bad", {"ok": False}, {"ok": 1}, {}):
            with self.subTest(value=value):
                response = self.assert_blocked(value)
                self.assertFalse(response["ok"])
                self.assertFalse(response["result"]["workflow_completed"])
                self.assertEqual(response["result"]["errors"][0]["stage"], "health_before")

    def test_health_reported_diagnostic_errors_do_not_authorize_project(self):
        for path in ("diagnostic_error", "state.execution.read_errors"):
            with self.subTest(path=path):
                status = healthy()
                target = status
                pieces = path.split(".")
                for key in pieces[:-1]:
                    target = target[key]
                target[pieces[-1]] = "read failed"
                response = self.assert_blocked(status)
                self.assertFalse(response["ok"])
                self.assertEqual(response["result"]["errors"][0]["code"], "reported_read_error")

    def test_each_final_identity_change_is_reported_and_context_not_complete(self):
        for field in ("version", "session_id", "bridge_build"):
            with self.subTest(field=field):
                after = healthy()
                after[field] += "-changed"
                response, bridge = self.run_snapshot(after=after)
                result = response["result"]
                self.assertTrue(response["ok"])
                self.assertFalse(result["context_complete"])
                self.assertTrue(result["context"]["fresh"])
                self.assertEqual(result["session_coherence"]["status"], "changed")
                self.assertEqual(result["session_coherence"]["changed_fields"], [field])
                self.assertEqual(bridge.events, ["health", "project", "health"])

    def test_each_final_identity_missing_is_unverified(self):
        for field in ("version", "session_id", "bridge_build"):
            with self.subTest(field=field):
                after = healthy()
                del after[field]
                response, _ = self.run_snapshot(after=after)
                self.assertFalse(response["result"]["context_complete"])
                self.assertEqual(response["result"]["session_coherence"]["status"], "unverified")
                self.assertEqual(response["result"]["readiness"]["after"], "unknown")

    def test_final_unknown_or_not_ready_gate_never_produces_complete_context(self):
        for field, invalid, expected in (("database_ready", None, "unknown"),
                                         ("database_ready", False, "not_ready"),
                                         ("busy", "false", "unknown"), ("busy", True, "not_ready")):
            with self.subTest(field=field, invalid=invalid):
                after = healthy()
                after["state"][field] = invalid
                response, bridge = self.run_snapshot(after=after)
                self.assertTrue(response["ok"])
                self.assertFalse(response["result"]["context_complete"])
                self.assertEqual(response["result"]["readiness"]["after"], expected)
                self.assertEqual(bridge.events, ["health", "project", "health"])

    def test_project_exception_keeps_diagnostics_and_always_reads_final_health(self):
        failure = BridgeError("offline", http_status=503, transport_error=True,
                              diagnostics={"state": {"busy": True}})
        response, bridge = self.run_snapshot(project_value=failure)
        result = response["result"]
        self.assertEqual(bridge.events, ["health", "project", "health"])
        self.assertFalse(response["ok"])
        self.assertFalse(result["workflow_completed"])
        self.assertFalse(result["context_complete"])
        self.assertEqual(result["readiness"]["after"], "ready")
        self.assertEqual(result["session_coherence"]["status"], "consistent")
        error = result["errors"][0]
        self.assertEqual(error["stage"], "project")
        self.assertEqual(error["http_status"], 503)
        self.assertTrue(error["transport_error"])
        self.assertEqual(error["diagnostics"], failure.diagnostics)
        self.assertIn("exception", result["evidence"]["project"])

    def test_project_bad_envelope_or_payload_still_gets_final_health(self):
        for value in ([], "bad", {}, {"ok": False}, {"ok": True}, {"ok": True, "project": []}):
            with self.subTest(value=value):
                response, bridge = self.run_snapshot(project_value=value)
                self.assertEqual(bridge.events, ["health", "project", "health"])
                self.assertFalse(response["ok"])
                self.assertFalse(response["result"]["context_complete"])
                self.assertEqual(response["result"]["errors"][0]["stage"], "project")
                self.assertEqual(response["result"]["evidence"]["project"], value)

    def test_project_busy_disconnected_cached_or_partial_is_not_fresh(self):
        changes = [{"busy": True}, {"busy": "false"}, {"connected": False}, {"connected": 1},
                   {"recovery_only": True}, {"recovery_only": 0}, {"name": ""}, {"mdb": " "}]
        for change in changes:
            with self.subTest(change=change):
                value = project()
                value["project"].update(change)
                response, _ = self.run_snapshot(project_value=value)
                result = response["result"]
                self.assertTrue(response["ok"])
                self.assertFalse(result["context_complete"])
                self.assertFalse(result["context"]["fresh"])
                self.assertIsNone(result["context"]["project"])
                self.assertIsNone(result["context"]["mdb"])
                self.assertTrue(result["context"]["issues"])
                self.assertEqual(result["evidence"]["project"], value)

    def test_missing_project_required_fields_cannot_become_fresh(self):
        for field in ("connected", "recovery_only", "name", "mdb"):
            with self.subTest(field=field):
                value = project()
                del value["project"][field]
                response, _ = self.run_snapshot(project_value=value)
                self.assertFalse(response["result"]["context"]["fresh"])
                self.assertFalse(response["result"]["context_complete"])

    def test_project_version_mismatch_or_unknown_cannot_become_fresh(self):
        for version in ("missing", None, "E3D22", "", False, 21):
            with self.subTest(version=version):
                value = project()
                if version == "missing":
                    del value["version"]
                else:
                    value["version"] = version
                response, _ = self.run_snapshot(project_value=value)
                self.assertFalse(response["result"]["context"]["fresh"])
                self.assertFalse(response["result"]["context_complete"])
                self.assertEqual(response["result"]["context"]["issues"][0], {
                    "path": "version", "code": "version_mismatch" if version == "E3D22" else "unknown"})
                self.assertEqual(response["result"]["evidence"]["project"], value)

    def test_missing_user_or_current_element_remains_unknown(self):
        for field in ("user", "current_element"):
            for value in ("missing", None, "", " ", False):
                with self.subTest(field=field, value=value):
                    response_value = project()
                    if value == "missing":
                        del response_value["project"][field]
                    else:
                        response_value["project"][field] = value
                    response, _ = self.run_snapshot(project_value=response_value)
                    context = response["result"]["context"]
                    self.assertTrue(context["fresh"])
                    self.assertTrue(response["result"]["context_complete"])
                    self.assertIsNone(context[field])
                    self.assertNotIn(field, context["available_fields"])

    def test_final_read_failure_retains_project_and_partial_evidence(self):
        response, bridge = self.run_snapshot(after=RuntimeError("final health failed"))
        result = response["result"]
        self.assertEqual(bridge.events, ["health", "project", "health"])
        self.assertFalse(response["ok"])
        self.assertFalse(result["context_complete"])
        self.assertTrue(result["context"]["fresh"])
        self.assertEqual(result["context"]["project"], "APS")
        self.assertEqual(result["readiness"]["after"], "unknown")
        self.assertEqual(result["session_coherence"]["status"], "unverified")
        self.assertEqual(result["errors"][0]["stage"], "health_after")

    def test_final_not_ready_is_data_and_context_incomplete(self):
        after = healthy()
        after["state"]["busy"] = True
        response, _ = self.run_snapshot(after=after)
        self.assertTrue(response["ok"])
        self.assertTrue(response["result"]["workflow_completed"])
        self.assertFalse(response["result"]["context_complete"])
        self.assertEqual(response["result"]["readiness"]["after"], "not_ready")

    def test_mock_mode_never_establishes_live_readiness_even_with_healthy_fixture(self):
        self.settings = Settings(mode="mock", version="E3D21")
        response, bridge = self.run_snapshot()
        self.assertEqual(bridge.events, ["health"])
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["readiness"]["before"], "unknown")
        self.assertFalse(response["result"]["context_complete"])
        self.assertEqual(response["result"]["session_coherence"]["status"], "unverified")

    def test_no_mutations_retries_extra_reads_or_unsupported_claims(self):
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=True)
        response, bridge = self.run_snapshot()
        result = response["result"]
        self.assertTrue(response["mcp"]["commands_enabled"])
        self.assertEqual(bridge.events, ["health", "project", "health"])
        self.assertTrue(result["read_only"])
        for field in ("retries", "model_writes", "view_setters", "captures", "commands"):
            self.assertEqual(result["scope"][field], 0)
        for field in ("future_readiness_guaranteed", "write_permission_verified", "empty_pml_input_stack_verified"):
            self.assertIs(result["scope"][field], False)

    def test_evidence_is_copied_without_mutating_bridge_responses(self):
        bridge = FakeBridge()
        before = copy.deepcopy(bridge.values)
        response = context_snapshot(bridge, self.settings)
        self.assertEqual(bridge.values, before)
        bridge.values[0]["state"]["busy"] = True
        self.assertFalse(response["result"]["evidence"]["health_before"]["state"]["busy"])


    def test_real_bridge_does_not_invent_missing_initial_native_health_version(self):
        value = healthy()
        del value["version"]
        with patch("urllib.request.urlopen", side_effect=lambda *args, **kwargs:
                   BytesIO(json.dumps(value).encode("utf-8"))) as http:
            response = context_snapshot(BridgeClient(self.settings), self.settings)
        self.assertEqual(http.call_count, 1)
        result = response["result"]
        self.assertTrue(response["ok"])
        self.assertFalse(result["context_complete"])
        self.assertEqual(result["readiness"]["before"], "unknown")
        self.assertEqual(result["session_coherence"]["status"], "unverified")
        self.assertNotIn("version", result["evidence"]["health_before"])
        self.assertNotIn("project", result["evidence"])

    def test_real_bridge_missing_final_native_health_version_keeps_context_unverified(self):
        final = healthy()
        del final["version"]
        project_value = project()
        # Native /project omits its version; that route intentionally retains
        # configured routing metadata, which is not native session identity.
        del project_value["version"]
        values = iter([healthy(), project_value, final])
        with patch("urllib.request.urlopen", side_effect=lambda *args, **kwargs:
                   BytesIO(json.dumps(next(values)).encode("utf-8"))) as http:
            response = context_snapshot(BridgeClient(self.settings), self.settings)
        self.assertEqual([call.args[0].selector for call in http.call_args_list],
                         ["/health", "/api/v1/project", "/health"])
        result = response["result"]
        self.assertTrue(response["ok"])
        self.assertTrue(result["context"]["fresh"])
        self.assertFalse(result["context_complete"])
        self.assertEqual(result["readiness"]["after"], "unknown")
        self.assertEqual(result["session_coherence"]["status"], "unverified")
        self.assertNotIn("version", result["evidence"]["health_after"])


if __name__ == "__main__":
    unittest.main()
