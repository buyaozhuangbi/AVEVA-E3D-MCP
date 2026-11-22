"""Permit one DESIGN recovery only from a verified managed CSG cache."""

from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings


CACHED_IDENTITY = "Cached identity from the last successful project read; no native database call was made."


def observations():
    quarantine = {
        "active": True, "stage": "options_after", "reason": "option_restoration_unverified",
        "message": "Export state could not be verified.", "source_refno": "=2000000004/41",
        "observed_utc": "2026-11-20T05:08:11.1648823Z", "scope": "bridge_process",
        "retry_safe": False, "reset_available": False, "retained_request_count": 1,
    }
    state = {"busy": False, "database_ready": False, "recovery_required": True,
             "generated_csg_quarantine": deepcopy(quarantine), "native_database_fault": {"has_fault": False}}
    health = {"ok": True, "connected": False, "version": "E3D21", "session_id": "original", "state": deepcopy(state)}
    project = {"ok": True, "project": {
        "name": "APS", "mdb": "ALL", "connected": False, "busy": False,
        "current_element": None, "recovery_only": True, "identity_source": CACHED_IDENTITY,
        "generated_csg_quarantine": deepcopy(quarantine), "native_database_fault": {"has_fault": False},
    }}
    command_state = {"ok": True, "version": "E3D21", "state": deepcopy(state)}
    ready = {"ok": True, "connected": True, "version": "E3D21", "session_id": "replacement",
             "state": {"busy": False, "database_ready": True, "recovery_required": False,
                       "generated_csg_quarantine": {"active": False}, "native_database_fault": {"has_fault": False}}}
    ready_project = {"ok": True, "project": {"name": "APS", "mdb": "ALL", "connected": True, "busy": False, "recovery_only": False}}
    return health, project, command_state, ready, ready_project


class CsgQuarantineReentryTests(unittest.TestCase):
    succeeded = {"ok": True, "result": {"success": True}}

    def run_case(self, *, health=None, projects=None, states=None, saved=None, dispatch=None):
        before, project, state, ready, ready_project = observations()
        client = BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=True))
        clock = [0.0]

        def sequence(values):
            pending = list(values)
            last = pending[-1]

            def read(**_):
                value = pending.pop(0) if pending else last
                if isinstance(value, Exception):
                    raise value
                return deepcopy(value)
            return read

        with ExitStack() as stack:
            mocks = {}
            mocks["health"] = stack.enter_context(patch.object(client, "health", side_effect=sequence(
                health if health is not None else [before, before, ready, ready])))
            mocks["project"] = stack.enter_context(patch.object(client, "project", side_effect=sequence(
                projects if projects is not None else [project, project, ready_project])))
            mocks["state"] = stack.enter_context(patch.object(client, "command_state", side_effect=sequence(
                states if states is not None else [state, state])))
            for name, value in (("command", self.succeeded if saved is None else saved),
                                ("_request", self.succeeded if dispatch is None else dispatch)):
                mocks[name] = stack.enter_context(patch.object(client, name,
                    **({"side_effect": value} if isinstance(value, Exception) else {"return_value": value})))
            mocks["cancel"] = stack.enter_context(patch.object(client, "cancel_command"))
            stack.enter_context(patch("aveva_e3d_mcp.bridge.time.monotonic", side_effect=lambda: clock[0]))
            stack.enter_context(patch("aveva_e3d_mcp.bridge.time.sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)))
            result = client.reenter_design(wait_seconds=5)
        return result, mocks

    def refused_before_save(self, health, project, state):
        result, mocks = self.run_case(health=[health], projects=[project], states=[state])
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["phase"], "preflight", result)
        self.assertFalse(result["design_attempted"])
        mocks["command"].assert_not_called()
        mocks["_request"].assert_not_called()
        mocks["cancel"].assert_not_called()

    def test_matching_csg_cache_saves_once_dispatches_once_and_requires_new_ready_session(self):
        result, mocks = self.run_case()
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["project_ready"])
        self.assertEqual(result["phase"], "complete")
        self.assertEqual(result["recovery_scope"], "matched_managed_csg_quarantine_cache")
        self.assertEqual(result["previous_session_id"], "original")
        self.assertEqual(result["health"]["session_id"], "replacement")
        self.assertTrue(result["health"]["state"]["database_ready"])
        mocks["command"].assert_called_once_with("SAVEWORK", False, version=None, expected_session_id="original")
        mocks["_request"].assert_called_once_with("POST", "/api/v1/command",
            {"command": "DESIGN", "dry_run": False, "expected_session_id": "original"}, version=None)
        self.assertEqual(mocks["state"].call_count, 2, "The managed command state is observed again after SAVEWORK.")
        mocks["cancel"].assert_not_called()

    def test_disconnected_or_deceptive_initial_observations_never_gain_recovery_permission(self):
        changes = [
            lambda h, p, s: h.update(ok=False),
            lambda h, p, s: p.update(ok=False),
            lambda h, p, s: s.update(ok=False),
            lambda h, p, s: h.update(session_id=""),
            lambda h, p, s: h.update(session_id=" "),
            lambda h, p, s: h.update(version="E3D31"),
            lambda h, p, s: h.update(connected=None),
            lambda h, p, s: h.pop("state"),
            lambda h, p, s: h["state"].update(busy=True),
            lambda h, p, s: h["state"].pop("busy"),
            lambda h, p, s: h["state"].update(database_ready=True),
            lambda h, p, s: h["state"].update(recovery_required=False),
            lambda h, p, s: h["state"].update(generated_csg_quarantine={"active": True}),
            lambda h, p, s: h["state"]["generated_csg_quarantine"].update(active=False),
            lambda h, p, s: h["state"]["generated_csg_quarantine"].update(active=1),
            lambda h, p, s: h["state"]["generated_csg_quarantine"].update(source_refno=""),
            lambda h, p, s: h["state"]["generated_csg_quarantine"].update(observed_utc=""),
            lambda h, p, s: h["state"]["generated_csg_quarantine"].update(scope="unknown"),
            lambda h, p, s: h["state"]["generated_csg_quarantine"].update(retry_safe=True),
            lambda h, p, s: s.update(state=None),
            lambda h, p, s: s["state"].pop("generated_csg_quarantine"),
            lambda h, p, s: s["state"]["generated_csg_quarantine"].update(reason="different_latch"),
            lambda h, p, s: s["state"].update(busy=None),
            lambda h, p, s: s["state"].update(busy=True),
            lambda h, p, s: s["state"].pop("database_ready"),
            lambda h, p, s: s["state"].update(recovery_required=None),
            lambda h, p, s: p["project"].update(connected=None),
            lambda h, p, s: p["project"].update(recovery_only=False),
            lambda h, p, s: p["project"].update(identity_source="live query"),
            lambda h, p, s: p["project"].update(identity_source="Cached"),
            lambda h, p, s: p["project"].pop("busy"),
            lambda h, p, s: p["project"].update(busy=True),
            lambda h, p, s: p["project"].update(current_element="=1/2"),
            lambda h, p, s: p["project"].update(name=" "),
            lambda h, p, s: p["project"].update(mdb=None),
            lambda h, p, s: p["project"].update(name=123),
            lambda h, p, s: p["project"]["generated_csg_quarantine"].update(observed_utc="changed"),
            lambda h, p, s: p["project"].pop("native_database_fault"),
            lambda h, p, s: h["state"]["native_database_fault"].update(has_fault=True),
            lambda h, p, s: s["state"]["native_database_fault"].update(has_fault=True),
            lambda h, p, s: p["project"]["native_database_fault"].update(has_fault=None),
        ]
        for index, change in enumerate(changes):
            h, p, s, _, _ = observations()
            change(h, p, s)
            with self.subTest(change=index):
                self.refused_before_save(h, p, s)

    def test_native_fault_only_is_not_a_disconnected_csg_recovery_permission(self):
        h, p, s, _, _ = observations()
        for observation in (h["state"], p["project"], s["state"]):
            observation["native_database_fault"] = {"has_fault": True, "code": 41}
            observation["generated_csg_quarantine"] = {"active": False}
        self.refused_before_save(h, p, s)

    def test_failed_or_unconfirmed_save_never_dispatches_design_or_cancellation(self):
        for saved in ({"ok": True, "result": {"success": False}}, {"ok": True, "result": {}},
                      {"ok": False, "error": "save rejected"}, BridgeError("save response lost", transport_error=True)):
            with self.subTest(saved=saved):
                result, mocks = self.run_case(saved=saved)
                self.assertFalse(result["ok"])
                self.assertEqual(result["phase"], "save")
                self.assertFalse(result["design_attempted"])
                self.assertEqual(mocks["command"].call_count, 1)
                mocks["_request"].assert_not_called()
                mocks["cancel"].assert_not_called()

    def test_post_save_session_cache_identity_or_quarantine_drift_refuses_design(self):
        changes = [
            lambda h, p, s: h.update(session_id="different"),
            lambda h, p, s: h.update(version="E3D31"),
            lambda h, p, s: h.update(connected=True),
            lambda h, p, s: h["state"].update(busy=True),
            lambda h, p, s: h["state"]["generated_csg_quarantine"].update(active=False),
            lambda h, p, s: p["project"].update(name="OTHER"),
            lambda h, p, s: p["project"].update(mdb="OTHER"),
            lambda h, p, s: p["project"].update(recovery_only=False),
            lambda h, p, s: p["project"].update(identity_source="unknown"),
            lambda h, p, s: p["project"].update(busy=None),
            lambda h, p, s: s["state"].update(busy=True),
            lambda h, p, s: s["state"].update(database_ready=True),
            lambda h, p, s: s["state"]["generated_csg_quarantine"].update(reason="new_failure"),
        ]
        for index, change in enumerate(changes):
            before, original, state, _, _ = observations()
            h, p, s = deepcopy(before), deepcopy(original), deepcopy(state)
            change(h, p, s)
            with self.subTest(change=index):
                result, mocks = self.run_case(health=[before, h], projects=[original, p], states=[state, s])
                self.assertFalse(result["ok"])
                self.assertEqual(result["phase"], "save")
                self.assertIn("changed after SAVEWORK", result["error"])
                self.assertEqual(mocks["command"].call_count, 1)
                mocks["_request"].assert_not_called()
        before, original, state, _, _ = observations()
        changed = deepcopy(before["state"]["generated_csg_quarantine"])
        changed["observed_utc"] = "2026-11-20T07:00:00Z"
        h, p, s = deepcopy(before), deepcopy(original), deepcopy(state)
        for observation in (h["state"], p["project"], s["state"]):
            observation["generated_csg_quarantine"] = deepcopy(changed)
        result, mocks = self.run_case(health=[before, h], projects=[original, p], states=[state, s])
        self.assertFalse(result["ok"], "Even mutually matching post-save snapshots must match the original quarantine.")
        mocks["_request"].assert_not_called()

    def test_unknown_recheck_after_save_never_dispatches_design(self):
        before, original, state, _, _ = observations()
        for kwargs in (
            {"health": [before, BridgeError("health unavailable")]},
            {"projects": [original, BridgeError("cache unavailable")]},
            {"states": [state, BridgeError("state unavailable")]},
        ):
            with self.subTest(kwargs=kwargs):
                result, mocks = self.run_case(**kwargs)
                self.assertFalse(result["ok"])
                self.assertEqual(result["phase"], "save")
                self.assertEqual(mocks["command"].call_count, 1)
                mocks["_request"].assert_not_called()

    def test_design_transport_loss_is_not_cancelled_or_replayed(self):
        result, mocks = self.run_case(dispatch=BridgeError("connection reset", transport_error=True))
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["dispatch_uncertain"])
        self.assertEqual(mocks["_request"].call_count, 1)
        self.assertEqual(mocks["command"].call_count, 1)
        mocks["cancel"].assert_not_called()

    def test_design_rejection_never_retries_or_polls_a_new_session(self):
        for response in ({"ok": True, "result": {"success": False}}, BridgeError("rejected", http_status=409)):
            with self.subTest(response=response):
                result, mocks = self.run_case(dispatch=response)
                self.assertFalse(result["ok"])
                self.assertEqual(result["phase"], "dispatch")
                self.assertEqual(mocks["_request"].call_count, 1)
                self.assertEqual(mocks["health"].call_count, 2)
                mocks["cancel"].assert_not_called()

    def test_success_requires_explicit_new_session_database_readiness(self):
        before, _, _, ready, _ = observations()
        for state in (None, {}, {"database_ready": False}, {"database_ready": 1}):
            unknown = deepcopy(ready)
            unknown["state"] = state
            with self.subTest(state=state):
                result, mocks = self.run_case(health=[before, before, unknown])
                self.assertFalse(result["ok"])
                self.assertTrue(result["timeout"])
                self.assertFalse(result["project_ready"])
                self.assertEqual(mocks["_request"].call_count, 1)
                mocks["cancel"].assert_not_called()
        result, mocks = self.run_case(health=[before])
        self.assertFalse(result["ok"])
        self.assertTrue(result["timeout"])
        self.assertFalse(result["reconnected"])
        self.assertEqual(mocks["_request"].call_count, 1)


if __name__ == "__main__":
    unittest.main()
