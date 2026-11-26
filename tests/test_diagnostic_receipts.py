"""Offline receipt-store limits, snapshots, and diagnostic JSON-RPC integration.

The compact-workflow fixture is synthetic and shared with the projection tests;
these tests never load historical artifacts, MCP registration, or E3D state.
"""
from __future__ import annotations
from catalog_compatibility import CURRENT_MOCK_TOOL_COUNT

import copy
import json
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.diagnostic_receipts import DiagnosticReceiptStore
from aveva_e3d_mcp.protocol import ImageToolResult
from aveva_e3d_mcp import tools as tool_module
from test_diagnosis_receipt import full_receipt, image_data, metadata


REFNO = "=2000000001/2633"


class FakeClock:
    def __init__(self):
        self.seconds = 100.0
        self.origin = datetime(2026, 11, 17, 8, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.seconds

    def utc(self):
        return self.origin + timedelta(seconds=self.seconds - 100)


def diagnosis(marker="A", *, image=False, failed=False, padding=""):
    value = {"ok": True, "version": "E3D21", "result": {
        "schema": 1, "success": not failed, "workflow_completed": not failed,
        "complete": False, "available": True, "requested_refno": REFNO,
        "target": {"refno": REFNO, "type": "CYLI", "name": "/FROZEN-PUMP"},
        "evidence": {"gates": [], "initial_status": {"session_id": "historical-old-session"},
                     "snapshot": {"marker": marker, "dimensions": {"DIAM": 120}, "padding": padding}},
        "observations": {"observed_window_unchanged": None}, "unknown": [{"code": "unverified"}],
        "errors": [{"code": "native_fault", "message": "Historical SDK fault"}] if failed else [],
        "stopped_stage": "geometry" if failed else None,
        "marker": marker, "read_only": True}}
    return ImageToolResult(image_data(), "image/png", value) if image else value


@contextmanager
def no_bridge():
    with ExitStack() as stack:
        for name in ("_request", "health", "project", "view_state", "capture_view", "graphical_selection",
                     "element", "element_schema", "geometry_bounds", "primitive_geometry", "panel_geometry", "display_state"):
            stack.enter_context(patch.object(BridgeClient, name, side_effect=AssertionError("Historical receipt performed a live read: " + name)))
        yield


def stored_size(value):
    payload = metadata(value)
    json_bytes = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    image_bytes = len(value.data.encode("ascii")) if isinstance(value, ImageToolResult) else 0
    return json_bytes + image_bytes


class DiagnosticReceiptStoreTests(unittest.TestCase):
    def store(self, **options):
        clock = FakeClock()
        value = DiagnosticReceiptStore("E3D21", clock=clock, utc_clock=clock.utc, **options)
        return value, clock

    def put(self, store, value):
        with no_bridge():
            info = store.put(value)
        self.assertEqual(info["status"], "stored", info)
        self.assertRegex(info["receipt_id"], r"^diag_[0-9a-f]{32}$")
        self.assertEqual(info["storage"], "mcp_process_memory")
        self.assertEqual(info["stored_bytes"], stored_size(value))
        self.assertEqual(info["image_available"], isinstance(value, ImageToolResult))
        return info

    def get(self, store, receipt_id, *, include_image=False):
        with no_bridge():
            value = store.get(receipt_id, include_image=include_image)
        response = metadata(value)
        self.assertTrue(response["ok"])
        self.assertEqual(response["version"], "E3D21")
        self.assertTrue(response["result"]["success"])
        self.assertTrue(response["result"]["historical"])
        self.assertFalse(response["result"]["live_read"])
        return value

    def test_put_and_get_round_trip_full_old_session_evidence_without_live_connection(self):
        store, _ = self.store(ttl_seconds=10)
        original = diagnosis()
        info = self.put(store, original)
        self.assertEqual(info["ttl_seconds"], 10)
        self.assertEqual(info["created_utc"], "2026-11-17T08:00:00Z")
        self.assertEqual(info["expires_utc"], "2026-11-17T08:00:10Z")
        result = self.get(store, info["receipt_id"])["result"]
        self.assertEqual(result["status"], "found")
        self.assertTrue(result["available"])
        self.assertEqual(result["diagnosis"], original)
        self.assertEqual(result["image"], {"requested": False, "available": False, "included": False})

    def test_default_retention_is_thirty_minutes_and_sixteen_fifo_entries(self):
        store, _ = self.store()
        self.assertEqual(store.max_bytes, 64 * 1024 * 1024)
        infos = [self.put(store, diagnosis(str(index))) for index in range(17)]
        self.assertEqual(infos[-1]["ttl_seconds"], 1800)
        self.assertEqual(infos[-1]["expires_utc"], "2026-11-17T08:30:00Z")
        self.assertEqual(self.get(store, infos[0]["receipt_id"])["result"]["status"], "unknown_or_expired")
        for info in infos[1:]:
            self.assertEqual(self.get(store, info["receipt_id"])["result"]["status"], "found")

    def test_configured_version_matches_history_and_rejects_cross_version_evidence(self):
        clock = FakeClock()
        store = DiagnosticReceiptStore("E3D31", max_entries=1, clock=clock, utc_clock=clock.utc)
        matching = diagnosis()
        matching["version"] = "E3D31"
        with no_bridge():
            info = store.put(matching)
            rejected = store.put(diagnosis())
            found = store.get(info["receipt_id"])
        self.assertEqual(info["status"], "stored")
        self.assertEqual(rejected["status"], "failed")
        self.assertEqual(rejected["reason"], "invalid_diagnosis")
        self.assertEqual(found["version"], "E3D31")
        self.assertEqual(found["result"]["diagnosis"], matching)

    def test_callable_utc_clock_is_used_even_when_its_truth_value_is_false(self):
        class FalseyUtcClock:
            def __bool__(self):
                return False

            def __call__(self):
                return datetime(2020, 2, 3, 4, 5, 6, tzinfo=timezone.utc)

        store = DiagnosticReceiptStore("E3D21", clock=FakeClock(), utc_clock=FalseyUtcClock())
        info = self.put(store, diagnosis())
        self.assertEqual(info["created_utc"], "2020-02-03T04:05:06Z")
        self.assertEqual(info["expires_utc"], "2020-02-03T04:35:06Z")

    def test_store_rejects_invalid_lookup_arguments_before_reading_clock(self):
        store, _ = self.store()
        receipt_id = "diag_" + "a" * 32
        with no_bridge(), patch.object(store, "_clock", side_effect=AssertionError("Invalid lookup read clock")):
            for invalid in (None, True, 123, "", "diag_" + "A" * 32, receipt_id + "\n", receipt_id + " "):
                with self.subTest(receipt_id=invalid), self.assertRaises(ValueError):
                    store.get(invalid)
            for invalid in (None, 0, 1, "false", [], {}):
                with self.subTest(include_image=invalid), self.assertRaises(ValueError):
                    store.get(receipt_id, include_image=invalid)

    def test_png_is_optional_on_lookup_and_never_reencoded(self):
        store, _ = self.store()
        original = diagnosis(image=True)
        info = self.put(store, original)
        without = self.get(store, info["receipt_id"])
        self.assertIsInstance(without, dict)
        self.assertEqual(without["result"]["image"], {"requested": False, "available": True, "included": False})
        with_image = self.get(store, info["receipt_id"], include_image=True)
        self.assertIsInstance(with_image, ImageToolResult)
        self.assertEqual((with_image.data, with_image.mime_type), (original.data, original.mime_type))
        self.assertEqual(with_image.metadata["result"]["diagnosis"], original.metadata)
        self.assertEqual(with_image.metadata["result"]["image"], {"requested": True, "available": True, "included": True})

    def test_failed_diagnosis_is_historical_data_not_a_failed_lookup(self):
        store, _ = self.store()
        original = diagnosis(failed=True, image=True)
        info = self.put(store, original)
        found = self.get(store, info["receipt_id"], include_image=True)
        self.assertTrue(found.metadata["result"]["success"])
        self.assertFalse(found.metadata["result"]["diagnosis"]["result"]["success"])
        self.assertEqual(found.metadata["result"]["diagnosis"], original.metadata)
        self.assertEqual(found.data, original.data)

    def test_ttl_expires_at_exact_boundary_and_reads_do_not_extend_it(self):
        store, clock = self.store(ttl_seconds=10)
        info = self.put(store, diagnosis())
        clock.seconds = 109.999
        self.assertEqual(self.get(store, info["receipt_id"])["result"]["status"], "found")
        clock.seconds = 110.0
        expired = self.get(store, info["receipt_id"])["result"]
        self.assertEqual(expired["status"], "unknown_or_expired")
        self.assertFalse(expired["available"])
        self.assertIsNone(expired["diagnosis"])
        clock.seconds = 111.0
        self.assertEqual(self.get(store, info["receipt_id"])["result"]["status"], "unknown_or_expired")

    def test_wall_clock_changes_do_not_shorten_or_extend_monotonic_ttl(self):
        store, clock = self.store(ttl_seconds=10)
        info = self.put(store, diagnosis())
        clock.origin += timedelta(days=365)
        clock.seconds = 109
        found = self.get(store, info["receipt_id"])["result"]
        self.assertEqual(found["status"], "found")
        self.assertEqual(found["created_utc"], info["created_utc"])
        self.assertEqual(found["expires_utc"], info["expires_utc"])
        clock.origin -= timedelta(days=730)
        clock.seconds = 110
        self.assertEqual(self.get(store, info["receipt_id"])["result"]["status"], "unknown_or_expired")

    def test_count_limit_evicts_fifo_and_lookup_does_not_reorder_entries(self):
        store, _ = self.store(max_entries=2)
        first = self.put(store, diagnosis("A"))
        second = self.put(store, diagnosis("B"))
        self.get(store, first["receipt_id"])
        third = self.put(store, diagnosis("C"))
        self.assertEqual(self.get(store, first["receipt_id"])["result"]["status"], "unknown_or_expired")
        for info in (second, third):
            self.assertEqual(self.get(store, info["receipt_id"])["result"]["status"], "found")

    def test_byte_cap_counts_utf8_json_and_independent_image_base64_bytes(self):
        value = diagnosis("中", image=True)
        size = stored_size(value)
        store, _ = self.store(max_entries=10, max_bytes=size)
        first = self.put(store, value)
        second = self.put(store, value)
        self.assertEqual(first["stored_bytes"], size)
        self.assertEqual(self.get(store, first["receipt_id"])["result"]["status"], "unknown_or_expired")
        self.assertEqual(self.get(store, second["receipt_id"])["result"]["status"], "found")

    def test_oversize_and_serialization_failures_do_not_evict_existing_receipts(self):
        store, _ = self.store(max_bytes=stored_size(diagnosis()) * 2)
        info = self.put(store, diagnosis())
        invalid_values = [diagnosis(padding="x" * 100000), diagnosis()]
        invalid_values[1]["result"]["evidence"]["unserializable"] = object()
        for value, reason in zip(invalid_values, ("too_large", "serialization_failed")):
            with self.subTest(reason=reason), no_bridge():
                failed = store.put(value)
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["reason"], reason)
            self.assertEqual(self.get(store, info["receipt_id"])["result"]["status"], "found")

    def test_nonfinite_and_invalid_unicode_metadata_serialization_fails_without_storage_loss(self):
        store, _ = self.store()
        info = self.put(store, diagnosis())
        for invalid in (float("nan"), float("inf"), "/x\ud800"):
            with self.subTest(invalid=invalid):
                value = diagnosis()
                value["result"]["evidence"]["invalid"] = invalid
                with no_bridge():
                    failed = store.put(value)
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(failed["reason"], "serialization_failed")
                self.assertEqual(self.get(store, info["receipt_id"])["result"]["status"], "found")

    def test_clock_and_identifier_failures_never_evict_valid_receipts(self):
        store, _ = self.store(max_entries=1)
        original = diagnosis()
        info = self.put(store, original)
        failures = (
            patch.object(store, "_clock", side_effect=RuntimeError("Clock unavailable")),
            patch.object(store, "_utc_clock", side_effect=RuntimeError("UTC unavailable")),
            patch("aveva_e3d_mcp.diagnostic_receipts.secrets.token_hex", side_effect=RuntimeError("Entropy unavailable")),
            patch("aveva_e3d_mcp.diagnostic_receipts.secrets.token_hex", return_value=info["receipt_id"][5:]),
        )
        for index, failure in enumerate(failures):
            with self.subTest(failure=index):
                with no_bridge(), failure:
                    rejected = store.put(diagnosis("replacement"))
                self.assertEqual(rejected["status"], "failed")
                self.assertEqual(rejected["reason"], "storage_error")
                found = self.get(store, info["receipt_id"])
                self.assertEqual(found["result"]["diagnosis"], original)

    def test_inputs_and_every_returned_diagnosis_are_isolated_snapshots(self):
        store, _ = self.store()
        original = diagnosis(image=True)
        frozen = copy.deepcopy(original)
        info = self.put(store, original)
        original.metadata["result"]["evidence"]["snapshot"]["dimensions"]["DIAM"] = 999
        first = self.get(store, info["receipt_id"], include_image=True)
        self.assertEqual(first.metadata["result"]["diagnosis"], frozen.metadata)
        first.metadata["result"]["diagnosis"]["result"]["evidence"]["snapshot"]["dimensions"]["DIAM"] = -1
        second = self.get(store, info["receipt_id"], include_image=True)
        self.assertEqual(second.metadata["result"]["diagnosis"], frozen.metadata)
        self.assertEqual(second.data, frozen.data)

    def test_compacted_or_non_diagnostic_metadata_cannot_be_saved_as_full(self):
        store, _ = self.store()
        compacted = diagnosis()
        compacted["result"]["evidence_compaction"] = {"applied": True}
        summary = diagnosis()
        summary["result"]["evidence"]["gates"] = {"count": 11, "details_omitted": True}
        for value in ({}, {"ok": True, "result": []}, {"ok": True, "result": {"success": True}}, compacted, summary):
            with self.subTest(value=value), no_bridge():
                info = store.put(value)
            self.assertEqual(info["status"], "failed")
            self.assertIn(info["reason"], {"invalid_diagnosis", "compacted_diagnosis"})

    def test_concurrent_puts_keep_unique_ids_and_obey_both_capacity_limits(self):
        value = diagnosis(image=True)
        size = stored_size(value)
        store, _ = self.store(max_entries=7, max_bytes=size * 4)
        with no_bridge(), ThreadPoolExecutor(max_workers=12) as executor:
            infos = list(executor.map(lambda _: store.put(value), range(48)))
        self.assertTrue(all(info["status"] == "stored" for info in infos), infos)
        self.assertEqual(len({info["receipt_id"] for info in infos}), 48)
        retained = [info for info in infos if self.get(store, info["receipt_id"])["result"]["status"] == "found"]
        self.assertGreater(len(retained), 0)
        self.assertLessEqual(len(retained), 7)
        self.assertLessEqual(sum(info["stored_bytes"] for info in retained), size * 4)

    def test_concurrent_readers_and_writers_keep_complete_independent_snapshots(self):
        store, _ = self.store(max_entries=4)

        def write_and_read(index):
            original = diagnosis(str(index), image=True)
            info = store.put(original)
            self.assertEqual(info["status"], "stored")
            lookup = store.get(info["receipt_id"], include_image=True)
            result = metadata(lookup)["result"]
            if result["available"]:
                self.assertEqual(result["diagnosis"], original.metadata)
                self.assertEqual(lookup.data, original.data)
                result["diagnosis"]["result"]["marker"] = "changed by reader"
            else:
                self.assertEqual(result["status"], "unknown_or_expired")
                self.assertIsNone(result["diagnosis"])
            return info, original.metadata

        with no_bridge(), ThreadPoolExecutor(max_workers=12) as executor:
            snapshots = list(executor.map(write_and_read, range(48)))
        retained = 0
        for info, original in snapshots:
            result = self.get(store, info["receipt_id"])["result"]
            if result["available"]:
                retained += 1
                self.assertEqual(result["diagnosis"], original)
        self.assertEqual(retained, 4)


class DiagnosticReceiptToolTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=False)
        self.server = tool_module.create_server(self.settings)

    def rpc(self, method, params, server=None):
        output = StringIO()
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        with patch("sys.stdout", output):
            (server or self.server).serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def tool(self, name, arguments, server=None):
        return self.rpc("tools/call", {"name": name, "arguments": arguments}, server)

    def test_catalog_adds_history_lookup_and_opt_in_retention(self):
        catalog = {row["name"]: row for row in self.rpc("tools/list", {})["tools"]}
        self.assertEqual(len(catalog), CURRENT_MOCK_TOOL_COUNT)
        retain = catalog["e3d_diagnose_element"]["inputSchema"]["properties"]["retain_receipt"]
        self.assertEqual(retain["type"], "boolean")
        self.assertIs(retain["default"], False)
        lookup = catalog["e3d_diagnostic_receipt"]
        schema = lookup["inputSchema"]
        self.assertEqual(set(schema["properties"]), {"receipt_id", "include_image"})
        self.assertEqual(schema["required"], ["receipt_id"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["receipt_id"]["pattern"], "^diag_[0-9a-f]{32}$")
        self.assertFalse(schema["properties"]["include_image"]["default"])
        self.assertTrue(lookup["annotations"]["readOnlyHint"])

    def test_default_false_and_explicit_false_do_not_store_or_change_full_or_compact_returns(self):
        original = full_receipt(True)
        for detail in ("full", "compact"):
            with self.subTest(detail=detail), no_bridge(), \
                    patch.object(tool_module, "diagnose_element", return_value=original) as diagnose, \
                    patch.object(DiagnosticReceiptStore, "put", side_effect=AssertionError("Default must not retain")) as put:
                default = self.tool("e3d_diagnose_element", {"refno": REFNO, "evidence_detail": detail})
                explicit = self.tool("e3d_diagnose_element", {"refno": REFNO, "evidence_detail": detail, "retain_receipt": False})
            self.assertEqual(default, explicit)
            self.assertNotIn("diagnostic_receipt", default["structuredContent"]["result"])
            self.assertEqual(diagnose.call_count, 2)
            put.assert_not_called()
            if detail == "full": self.assertEqual(default["structuredContent"], original.metadata)

    def test_full_default_and_false_retention_return_the_original_object_without_store_access(self):
        for original in (diagnosis(), diagnosis(image=True)):
            with self.subTest(image=isinstance(original, ImageToolResult)), no_bridge(), \
                    patch.object(tool_module, "diagnose_element", return_value=original), \
                    patch.object(DiagnosticReceiptStore, "put", side_effect=AssertionError("Must not store")) as put, \
                    patch.object(DiagnosticReceiptStore, "get", side_effect=AssertionError("Must not read store")) as get:
                for arguments in ({"refno": REFNO}, {"refno": REFNO, "retain_receipt": False}):
                    self.assertIs(tool_module._diagnose_element(object(), arguments), original)
                put.assert_not_called()
                get.assert_not_called()

    def test_invalid_retention_or_lookup_parameters_have_zero_diagnosis_and_bridge_calls(self):
        with no_bridge(), patch.object(tool_module, "diagnose_element") as diagnose, \
                patch.object(DiagnosticReceiptStore, "put") as put, patch.object(DiagnosticReceiptStore, "get") as get:
            for value in (None, 0, 1, "true", "false", [], {}):
                with self.subTest(retain_receipt=value):
                    response = self.tool("e3d_diagnose_element", {"refno": REFNO, "retain_receipt": value})
                    self.assertTrue(response.get("isError"))
            invalid = [{}, {"receipt_id": None}, {"receipt_id": True}, {"receipt_id": ""},
                {"receipt_id": "diag_" + "a" * 31}, {"receipt_id": "diag_" + "g" * 32},
                {"receipt_id": "diag_" + "A" * 32}, {"receipt_id": " diag_" + "a" * 32},
                {"receipt_id": "diag_" + "a" * 32 + "\n"}, {"receipt_id": "diag_" + "a" * 32, "refno": REFNO}]
            invalid += [{"receipt_id": "diag_" + "a" * 32, "include_image": value} for value in (None, 0, 1, "true", [], {})]
            for arguments in invalid:
                with self.subTest(lookup=arguments):
                    self.assertTrue(self.tool("e3d_diagnostic_receipt", arguments).get("isError"))
            diagnose.assert_not_called()
            put.assert_not_called()
            get.assert_not_called()

    def test_retained_full_diagnosis_is_retrievable_even_when_first_response_was_compact(self):
        original = full_receipt(True)
        frozen = copy.deepcopy(original)
        with no_bridge(), patch.object(tool_module, "diagnose_element", return_value=original) as diagnose:
            first = self.tool("e3d_diagnose_element", {"refno": REFNO, "include_image": True,
                "evidence_detail": "compact", "retain_receipt": True})
            result = first["structuredContent"]["result"]
            self.assertTrue(result["evidence_compaction"]["applied"])
            info = result["diagnostic_receipt"]
            self.assertEqual(info["status"], "stored")
            lookup = self.tool("e3d_diagnostic_receipt", {"receipt_id": info["receipt_id"], "include_image": True})
        diagnose.assert_called_once()
        self.assertFalse(lookup.get("isError", False), lookup)
        historical = lookup["structuredContent"]["result"]
        self.assertEqual(historical["diagnosis"], frozen.metadata)
        self.assertNotIn("evidence_compaction", historical["diagnosis"]["result"])
        self.assertNotIn("diagnostic_receipt", historical["diagnosis"]["result"])
        self.assertEqual([part for part in lookup["content"] if part["type"] == "image"],
                         [{"type": "image", "data": original.data, "mimeType": original.mime_type}])
        self.assertEqual(original, frozen)

    def test_failed_diagnosis_can_be_saved_and_lookup_succeeds_with_same_failure_and_png(self):
        original = diagnosis(failed=True, image=True)
        with no_bridge(), patch.object(tool_module, "diagnose_element", return_value=original):
            first = self.tool("e3d_diagnose_element", {"refno": REFNO, "retain_receipt": True, "include_image": True})
            self.assertTrue(first.get("isError"))
            info = first["structuredContent"]["result"]["diagnostic_receipt"]
            lookup = self.tool("e3d_diagnostic_receipt", {"receipt_id": info["receipt_id"], "include_image": True})
        self.assertFalse(lookup.get("isError", False), lookup)
        self.assertTrue(lookup["structuredContent"]["result"]["success"])
        self.assertEqual(lookup["structuredContent"]["result"]["diagnosis"], original.metadata)
        self.assertEqual(next(part for part in lookup["content"] if part["type"] == "image")["data"], original.data)

    def test_put_failure_is_additional_metadata_and_never_replaces_diagnosis_or_png(self):
        original = full_receipt(True)
        failure = {"status": "failed", "reason": "too_large", "storage": "mcp_process_memory"}
        with no_bridge(), patch.object(tool_module, "diagnose_element", return_value=original), \
                patch.object(DiagnosticReceiptStore, "put", return_value=failure) as put:
            response = self.tool("e3d_diagnose_element", {"refno": REFNO, "retain_receipt": True})
        self.assertFalse(response.get("isError", False), response)
        copied = copy.deepcopy(response["structuredContent"])
        self.assertEqual(copied["result"].pop("diagnostic_receipt"), failure)
        self.assertEqual(copied, original.metadata)
        self.assertEqual(next(part for part in response["content"] if part["type"] == "image")["data"], original.data)
        put.assert_called_once_with(original)

    def test_unexpected_store_exception_preserves_original_failed_diagnosis_and_png(self):
        original = diagnosis(failed=True, image=True)
        frozen = copy.deepcopy(original)
        with no_bridge(), patch.object(tool_module, "diagnose_element", return_value=original), \
                patch.object(DiagnosticReceiptStore, "put", side_effect=RuntimeError("Storage unavailable")):
            response = self.tool("e3d_diagnose_element", {"refno": REFNO, "retain_receipt": True})
        self.assertTrue(response.get("isError"))
        copied = copy.deepcopy(response["structuredContent"])
        info = copied["result"].pop("diagnostic_receipt")
        self.assertEqual(info["status"], "failed")
        self.assertEqual(info["reason"], "storage_error")
        self.assertEqual(copied, frozen.metadata)
        self.assertEqual(original, frozen)
        self.assertEqual(next(part for part in response["content"] if part["type"] == "image")["data"], frozen.data)

    def test_retention_failure_skips_compaction_and_preserves_full_evidence_without_replay(self):
        for failure in ({"status": "failed", "reason": "too_large", "storage": "mcp_process_memory"},
                        RuntimeError("Storage unavailable")):
            original = full_receipt(True)
            frozen = copy.deepcopy(original)
            behavior = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
            with self.subTest(failure=type(failure).__name__), no_bridge(), \
                    patch.object(tool_module, "diagnose_element", return_value=original) as diagnose, \
                    patch.object(DiagnosticReceiptStore, "put", **behavior) as put, \
                    patch.object(tool_module, "compact_diagnosis", side_effect=AssertionError("Must preserve full evidence")) as compact:
                response = self.tool("e3d_diagnose_element", {"refno": REFNO, "include_image": True,
                    "evidence_detail": "compact", "retain_receipt": True})
            self.assertFalse(response.get("isError", False), response)
            copied = copy.deepcopy(response["structuredContent"])
            info = copied["result"].pop("diagnostic_receipt")
            self.assertEqual(info["status"], "failed")
            self.assertEqual(info["reason"], "storage_error" if isinstance(failure, Exception) else "too_large")
            self.assertEqual(copied["result"].pop("evidence_compaction"), {
                "requested": "compact", "applied": False, "reason": "receipt_retention_failed"})
            self.assertEqual(copied, frozen.metadata)
            self.assertEqual(original, frozen)
            self.assertEqual(next(part for part in response["content"] if part["type"] == "image")["data"], frozen.data)
            diagnose.assert_called_once()
            put.assert_called_once_with(original)
            compact.assert_not_called()

    def test_stores_are_isolated_between_mcp_server_instances(self):
        second_server = tool_module.create_server(self.settings)
        with no_bridge(), patch.object(tool_module, "diagnose_element", return_value=diagnosis()):
            first = self.tool("e3d_diagnose_element", {"refno": REFNO, "retain_receipt": True})
            receipt_id = first["structuredContent"]["result"]["diagnostic_receipt"]["receipt_id"]
            unknown = self.tool("e3d_diagnostic_receipt", {"receipt_id": receipt_id}, second_server)
            found = self.tool("e3d_diagnostic_receipt", {"receipt_id": receipt_id})
        self.assertFalse(unknown.get("isError", False), unknown)
        self.assertEqual(unknown["structuredContent"]["result"]["status"], "unknown_or_expired")
        self.assertIsNone(unknown["structuredContent"]["result"]["diagnosis"])
        self.assertEqual(found["structuredContent"]["result"]["status"], "found")


if __name__ == "__main__":
    unittest.main()
