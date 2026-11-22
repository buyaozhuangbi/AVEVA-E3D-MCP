from __future__ import annotations

import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings


class DesignReentryTests(unittest.TestCase):
    before = {"ok": True, "connected": True, "version": "E3D21", "session_id": "before"}
    after = {**before, "session_id": "after", "state": {"database_ready": True}}
    project = {"project": {"connected": True, "name": "APS", "mdb": "ALL"}}
    succeeded = {"ok": True, "result": {"success": True}}

    def run_case(self, *, health=None, projects=None, save=None, dispatch=None, busy=False):
        client = BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=True))
        clock = [0.0]

        def sequence(values):
            pending = list(values)
            last = pending[-1]

            def read(**_):
                value = pending.pop(0) if pending else last
                if isinstance(value, Exception):
                    raise value
                return value
            return read

        def sleep(seconds):
            clock[0] += seconds

        with ExitStack() as stack:
            mocks = {}
            mocks["health"] = stack.enter_context(patch.object(client, "health", side_effect=sequence(health or [self.before, self.before, self.after])))
            stack.enter_context(patch.object(client, "project", side_effect=sequence(projects or [self.project])))
            stack.enter_context(patch.object(client, "command_state", return_value={"state": {"busy": busy}}))
            save_value = self.succeeded if save is None else save
            mocks["save"] = stack.enter_context(patch.object(client, "command",
                **({"side_effect": save_value} if isinstance(save_value, Exception) else {"return_value": save_value})))
            # _request's positional arguments are also checked below.
            dispatch_value = self.succeeded if dispatch is None else dispatch
            mocks["design"] = stack.enter_context(patch.object(client, "_request",
                **({"side_effect": dispatch_value} if isinstance(dispatch_value, Exception) else {"return_value": dispatch_value})))
            mocks["cancel"] = stack.enter_context(patch.object(client, "cancel_command"))
            stack.enter_context(patch("aveva_e3d_mcp.bridge.time.monotonic", side_effect=lambda: clock[0]))
            stack.enter_context(patch("aveva_e3d_mcp.bridge.time.sleep", side_effect=sleep))
            result = client.reenter_design(wait_seconds=5)
        return result, mocks

    def test_only_new_session_with_ready_original_project_is_success(self):
        busy_project = {"project": {"connected": True, "busy": True}}
        result, mocks = self.run_case(
            health=[self.before, self.before, self.before, self.after, self.after, self.after],
            projects=[self.project, self.project, busy_project, self.project],
            dispatch=BridgeError("connection reset", transport_error=True))
        self.assertTrue(result["ok"])
        self.assertEqual(result["phase"], "complete")
        self.assertTrue(result["dispatch_uncertain"])
        self.assertTrue(result["project_ready"])
        self.assertEqual(result["health"]["session_id"], "after")
        self.assertEqual(result["previous_session_id"], "before")
        mocks["save"].assert_called_once_with("SAVEWORK", False, version=None, expected_session_id="before")
        mocks["design"].assert_called_once_with("POST", "/api/v1/command",
            {"command": "DESIGN", "dry_run": False, "expected_session_id": "before"}, version=None)
        mocks["cancel"].assert_not_called()

    def test_save_failure_or_missing_success_never_sends_design(self):
        for saved in ({"ok": True, "result": {"success": False, "error": "read only"}},
                      {"ok": False, "timeout": True}, {"ok": True, "result": None},
                      BridgeError("save response lost", transport_error=True)):
            with self.subTest(saved=saved):
                result, mocks = self.run_case(save=saved)
                self.assertFalse(result["ok"])
                self.assertEqual(result["phase"], "save")
                self.assertFalse(result["design_attempted"])
                mocks["design"].assert_not_called()

    def test_old_healthy_session_cannot_count_as_a_restart(self):
        result, mocks = self.run_case(health=[self.before])
        self.assertFalse(result["ok"])
        self.assertTrue(result["timeout"])
        self.assertFalse(result["reconnected"])
        self.assertFalse(result["project_ready"])
        self.assertIn("do not automatically", result["next_action"])
        self.assertEqual(mocks["design"].call_count, 1)

    def test_preflight_requires_idle_session_identity_and_project(self):
        for kwargs in ({"busy": True}, {"health": [{**self.before, "session_id": ""}]},
                       {"projects": [{"project": {"connected": True}}]},
                       {"health": [{**self.before, "version": "E3D31"}]}):
            with self.subTest(kwargs=kwargs):
                result, mocks = self.run_case(**kwargs)
                self.assertFalse(result["ok"])
                self.assertEqual(result["phase"], "preflight")
                mocks["save"].assert_not_called()
                mocks["design"].assert_not_called()

    def test_session_changed_during_save_is_not_restarted(self):
        result, mocks = self.run_case(health=[self.before, self.after])
        self.assertFalse(result["ok"])
        self.assertIn("changed after SAVEWORK", result["error"])
        mocks["design"].assert_not_called()

    def test_explicit_design_rejection_does_not_enter_polling_or_retry(self):
        for rejected in ({"ok": True, "result": {"success": False}},
                         BridgeError("HTTP 409: wrong session", http_status=409)):
            with self.subTest(rejected=rejected):
                result, mocks = self.run_case(dispatch=rejected)
                self.assertFalse(result["ok"])
                self.assertEqual(result["phase"], "dispatch")
                self.assertFalse(result["dispatch_uncertain"])
                self.assertEqual(mocks["health"].call_count, 2)
                self.assertEqual(mocks["design"].call_count, 1)

    def test_timeout_during_design_does_not_cancel_or_replay(self):
        result, mocks = self.run_case(dispatch=BridgeError("timed out", transport_error=True))
        self.assertTrue(result["ok"])
        self.assertTrue(result["dispatch_uncertain"])
        self.assertEqual(mocks["design"].call_count, 1)
        mocks["cancel"].assert_not_called()

    def test_wrong_mdb_is_reported_and_transient_read_failure_is_tolerated(self):
        result, mocks = self.run_case(projects=[self.project, self.project,
            {"project": {"connected": True, "name": "APS", "mdb": "OTHER"}}])
        self.assertFalse(result["ok"])
        self.assertIn("different project or MDB", result["error"])
        self.assertFalse(result["project_ready"])
        self.assertEqual(mocks["design"].call_count, 1)
        result, _ = self.run_case(health=[self.before, self.before, BridgeError("temporarily unavailable"), self.after])
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
