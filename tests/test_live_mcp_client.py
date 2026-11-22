from __future__ import annotations

import io
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import live_mcp


class LiveMcpClientTests(unittest.TestCase):
    def client(self, response):
        client = live_mcp.LiveMcp.__new__(live_mcp.LiveMcp)
        client.rpc = Mock(return_value=response)
        client.calls = 0
        return client

    def transport_client(self):
        client = live_mcp.LiveMcp.__new__(live_mcp.LiveMcp)
        client._initialize_transport_state()
        client.process = Mock()
        client.process.stdin = io.StringIO()
        client.process.stdout = io.StringIO()
        return client

    @staticmethod
    def frame(request_id, *, result=None, error=None):
        response = {"jsonrpc": "2.0", "id": request_id}
        if error is None:
            response["result"] = result
        else:
            response["error"] = error
        return json.dumps(response)

    def failure(self):
        native = {"success": False, "creation_journal": [{"refno": "=1/2", "detail": "x" * 7000}],
                  "error": "PAVE creation failed at vertex 91", "outcome_uncertain": True,
                  "cleanup_succeeded": False, "residual_refno": "=1/2"}
        return {"isError": True, "structuredContent": {"ok": True, "result": native},
                "content": [{"type": "text", "text": json.dumps(native)}]}

    def test_long_failure_retains_full_receipt_and_actionable_tail_without_replay(self):
        response = self.failure()
        client = self.client(response)
        with self.assertRaises(live_mcp.LiveMcpToolError) as caught:
            client.call("e3d_create_panel", {"dry_run": False})
        self.assertIsInstance(caught.exception, RuntimeError)
        self.assertIs(caught.exception.response, response)
        self.assertEqual(caught.exception.structured_content, response["structuredContent"])
        self.assertIn("vertex 91", str(caught.exception))
        self.assertLess(len(str(caught.exception)), 2000)
        client.rpc.assert_called_once()

    def test_legacy_text_json_is_available_on_success_and_failure(self):
        body = {"ok": False, "result": {"error": "native tail", "cleanup_succeeded": False}}
        response = {"isError": True, "content": [{"type": "text", "text": json.dumps(body)}]}
        with self.assertRaises(live_mcp.LiveMcpToolError) as caught:
            self.client(response).call("create")
        self.assertEqual(caught.exception.structured_content, body)
        self.assertEqual(self.client(response).call("create", expect_error=True), body)
        success = {"content": [{"type": "text", "text": "plain"},
                               {"type": "text", "text": '{"ok":true}'}]}
        self.assertEqual(self.client(success).call("read"), {"ok": True})

    def test_expected_failure_does_not_hide_unexpected_success(self):
        response = {"structuredContent": {"ok": True}}
        with self.assertRaisesRegex(live_mcp.LiveMcpToolError, "returned success") as caught:
            self.client(response).call("read", expect_error=True)
        self.assertIs(caught.exception.response, response)

    def test_secondary_cleanup_messages_do_not_hide_primary_native_error(self):
        response = {"isError": True, "structuredContent": {"ok": False,
            "result": {"error": "Primary native create error 538",
                       "cleanup": {"errors": [{"reason": f"secondary {i}"} for i in range(8)]}},
            "error": "Generic operation failure"}}
        error = live_mcp.LiveMcpToolError("create", response)
        self.assertIn("Primary native create error 538", str(error))
        self.assertIn("Generic operation failure", str(error))
        self.assertIn("secondary 7", str(error))
        self.assertIs(error.response, response)

    def test_cli_saves_complete_error_receipt_before_nonzero_exit(self):
        response = self.failure()
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.call.side_effect = live_mcp.LiveMcpToolError("create", response)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "nested" / "failure.json"
            with patch.object(live_mcp, "LiveMcp", return_value=client), patch("sys.stderr", new_callable=io.StringIO) as stderr:
                self.assertEqual(live_mcp.main(["create", "--output", str(output)]), 1)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), response)
            self.assertIn("vertex 91", stderr.getvalue())
        client.call.assert_called_once()

    def test_timeout_late_response_is_buffered_during_later_rpc_and_recoverable(self):
        client = self.transport_client()
        with self.assertRaises(live_mcp.LiveMcpTimeoutError) as caught:
            client.rpc("tools/call", {"name": "create", "arguments": {"dry_run": False}}, timeout=0)
        self.assertEqual(caught.exception.request_id, 1)
        self.assertEqual(caught.exception.method, "tools/call")
        self.assertTrue(caught.exception.outcome_uncertain)
        self.assertFalse(caught.exception.retry_safe)

        receipt = {"structuredContent": {"ok": True, "result": {"success": True,
                    "creation_journal": [{"sequence": i, "detail": "x" * 2048} for i in range(100)]}}}
        client.responses.put(self.frame(1, result=receipt))
        client.responses.put(self.frame(2, result={"alive": True}))
        self.assertEqual(client.rpc("ping", {}, timeout=1), {"alive": True})
        self.assertEqual(client.available_response_ids, (1,))
        self.assertEqual(client.pending_request_ids, (1,))
        writes = client.process.stdin.getvalue().splitlines()
        self.assertEqual(len(writes), 2)
        self.assertEqual(json.loads(writes[0])["id"], 1)
        self.assertEqual(json.loads(writes[1])["id"], 2)
        self.assertEqual(client.wait_response(1, timeout=1), receipt)
        self.assertEqual(client.pending_request_ids, ())
        self.assertEqual(client.process.stdin.getvalue().splitlines(), writes)
        with self.assertRaisesRegex(ValueError, "unknown or its response"):
            client.wait_response(1, timeout=0)
        with self.assertRaisesRegex(ValueError, "unknown or its response"):
            client.wait_response(999, timeout=0)

    def test_capacity_refuses_before_sending_or_evicting_pending_receipts(self):
        client = self.transport_client()
        for request_id in range(1, 65):
            with self.assertRaises(live_mcp.LiveMcpTimeoutError):
                client.rpc("write", {"request": request_id}, timeout=0)
        # Route one unread original receipt while waiting for another ID. It
        # still counts against capacity and cannot be evicted to admit writes.
        receipt = {"first": True, "journal": "retained" * 1000}
        client.responses.put(self.frame(1, result=receipt))
        with self.assertRaises(live_mcp.LiveMcpTimeoutError):
            client.wait_response(64, timeout=0)
        self.assertEqual(client.available_response_ids, (1,))
        writes_before = client.process.stdin.getvalue()
        with self.assertRaisesRegex(RuntimeError, "pending-request limit"):
            client.rpc("overflow", {}, timeout=0)
        self.assertEqual(client.process.stdin.getvalue(), writes_before)
        self.assertEqual(client.pending_request_ids, tuple(range(1, 65)))
        self.assertEqual(client.request_id, 64)
        self.assertEqual(client.wait_response(1, timeout=0), receipt)
        self.assertEqual(client.process.stdin.getvalue(), writes_before)
        with self.assertRaises(live_mcp.LiveMcpTimeoutError):
            client.rpc("next", {}, timeout=0)
        self.assertEqual(client.request_id, 65)
        self.assertEqual(len(client.process.stdin.getvalue().splitlines()), 65)

    def test_standard_json_rpc_error_is_correlated_and_preserved_in_full(self):
        client = self.transport_client()
        error = {"code": -32000, "message": "native failure", "data": {"receipt": "full" * 1000}}
        client.responses.put(self.frame(1, error=error))
        with self.assertRaises(live_mcp.LiveMcpProtocolError) as caught:
            client.rpc("tools/call", {"name": "write"}, timeout=0)
        self.assertEqual(caught.exception.request_id, 1)
        self.assertEqual(caught.exception.method, "tools/call")
        self.assertEqual(caught.exception.response["error"], error)
        self.assertEqual(caught.exception.response["id"], 1)
        self.assertEqual(client.pending_request_ids, ())

    def test_late_json_rpc_error_waits_for_its_original_consumer(self):
        client = self.transport_client()
        with self.assertRaises(live_mcp.LiveMcpTimeoutError):
            client.rpc("original/write", {}, timeout=0)
        error = {"code": -32099, "message": "late error", "data": {"journal": "x" * 10000}}
        client.responses.put(self.frame(1, error=error))
        client.responses.put(self.frame(2, result={"read": "current"}))
        self.assertEqual(client.rpc("later/read", {}, timeout=0), {"read": "current"})
        self.assertEqual(client.available_response_ids, (1,))
        writes = client.process.stdin.getvalue()
        with self.assertRaises(live_mcp.LiveMcpProtocolError) as caught:
            client.wait_response(1, timeout=0)
        self.assertEqual(caught.exception.request_id, 1)
        self.assertEqual(caught.exception.method, "original/write")
        self.assertEqual(caught.exception.response, {"jsonrpc": "2.0", "id": 1, "error": error})
        self.assertEqual(client.process.stdin.getvalue(), writes)
        self.assertEqual(client.pending_request_ids, ())

    def test_unknown_response_id_preserves_frame_and_keeps_expected_request_pending(self):
        for response_id in (99, True, 1.0, "1", None):
            with self.subTest(response_id=response_id):
                client = self.transport_client()
                frame = self.frame(response_id, result={"unexpected": "x" * 7000})
                client.responses.put(frame)
                with self.assertRaises(live_mcp.LiveMcpProtocolError) as caught:
                    client.rpc("read", {}, timeout=0)
                self.assertEqual(caught.exception.response, json.loads(frame))
                self.assertEqual(caught.exception.raw_line, frame)
                self.assertEqual(caught.exception.awaiting_request_id, 1)
                self.assertEqual(client.pending_request_ids, (1,))
                writes = client.process.stdin.getvalue()
                client.responses.put(self.frame(1, result={"correct": True}))
                self.assertEqual(client.wait_response(1, timeout=0), {"correct": True})
                self.assertEqual(client.process.stdin.getvalue(), writes)

    def test_unknown_consumed_and_invalid_wait_ids_do_not_send(self):
        client = self.transport_client()
        client.responses.put(self.frame(1, result={"first": True}))
        self.assertEqual(client.rpc("read", {}, timeout=0), {"first": True})
        writes = client.process.stdin.getvalue()
        for request_id in (1, 2, True, "1", None, []):
            with self.subTest(request_id=request_id):
                with self.assertRaises(ValueError):
                    client.wait_response(request_id, timeout=0)
        self.assertEqual(client.process.stdin.getvalue(), writes)
        with self.assertRaises(AttributeError):
            client.pending_request_ids = (3,)
        with self.assertRaises(AttributeError):
            client.available_response_ids = (3,)

    def test_one_deadline_across_unrelated_frames(self):
        client = self.transport_client()
        clock = Mock()
        clock.value = 0.0

        class TimedQueue(queue.Queue):
            timeouts = []

            def get(self, block=True, timeout=None):
                # A notification takes 0.6 seconds to arrive. The second one
                # cannot arrive in the remaining 0.4 seconds of the original
                # deadline, proving unrelated frames do not restart it.
                if not block:
                    # These frames represent future arrivals rather than
                    # already-buffered ingress for the snapshot drain.
                    raise queue.Empty
                self.timeouts.append(timeout)
                if timeout is not None and timeout < 0.6:
                    clock.value += timeout
                    raise queue.Empty
                value = super().get(block=False)
                clock.value += 0.6
                return value

        client.responses = TimedQueue()
        for progress in (1, 2):
            client.responses.put(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress",
                                            "params": {"progress": progress}}))
        with patch.object(live_mcp.time, "monotonic", side_effect=lambda: clock.value):
            with self.assertRaises(live_mcp.LiveMcpTimeoutError):
                client.rpc("ping", {}, timeout=1)
        self.assertEqual(len(client.responses.timeouts), 2)
        self.assertAlmostEqual(client.responses.timeouts[0], 1)
        self.assertAlmostEqual(client.responses.timeouts[1], 0.4)
        self.assertEqual(client.pending_request_ids, (1,))

    def test_deadline_drain_consumes_only_the_initial_queue_snapshot(self):
        client = self.transport_client()
        notification = json.dumps({"jsonrpc": "2.0", "method": "notifications/progress"})

        class ReplenishingQueue(queue.Queue):
            reads = 0

            def get(self, block=True, timeout=None):
                value = super().get(block=block, timeout=timeout)
                self.reads += 1
                self.put(notification)
                return value

        client.responses = ReplenishingQueue()
        client.responses.put(notification)
        client.responses.put(notification)
        with self.assertRaises(live_mcp.LiveMcpTimeoutError):
            client.rpc("read", {}, timeout=0)
        self.assertEqual(client.responses.reads, 2)
        self.assertEqual(client.responses.qsize(), 2)
        self.assertEqual(len(client._notifications), 2)
        with self.assertRaises(live_mcp.LiveMcpTimeoutError):
            client.wait_response(1, timeout=0)
        self.assertEqual(client.pending_request_ids, (1,))

    def test_notifications_do_not_satisfy_pending_rpc(self):
        client = self.transport_client()
        client.responses.put(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress",
                                        "params": {"progress": 50}}))
        client.responses.put(self.frame(1, result={"ok": True}))
        self.assertEqual(client.rpc("slow/read", {}, timeout=1), {"ok": True})
        self.assertEqual(client._notifications[0]["method"], "notifications/progress")

    def test_notification_retention_is_bounded(self):
        client = self.transport_client()
        for index in range(70):
            client.responses.put(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress",
                                             "params": {"progress": index}}))
        client.responses.put(self.frame(1, result={"ok": True}))
        self.assertEqual(client.rpc("read", {}, timeout=1), {"ok": True})
        self.assertEqual(len(client._notifications), 64)
        self.assertEqual(client._notifications[0]["params"]["progress"], 6)
        self.assertEqual(client._notifications[-1]["params"]["progress"], 69)

    def test_invalid_and_overflowing_timeouts_fail_before_request_is_sent(self):
        client = self.transport_client()
        for timeout in (True, -1, float("inf"), float("nan"), threading.TIMEOUT_MAX + 1,
                        10**1000, "1"):
            with self.subTest(timeout=repr(timeout)[:40]):
                with self.assertRaises(ValueError):
                    client.rpc("write", {"dry_run": False}, timeout=timeout)
                self.assertEqual(client.request_id, 0)
                self.assertEqual(client.pending_request_ids, ())
                self.assertEqual(client.process.stdin.getvalue(), "")

    def test_eof_and_reader_failure_wake_pending_waiters_promptly(self):
        for reader_error in (None, UnicodeError("broken stdout decoding")):
            with self.subTest(reader_error=reader_error):
                client = self.transport_client()
                entered = threading.Event()
                release = threading.Event()

                class Stdout:
                    def __iter__(self):
                        entered.set()
                        release.wait(1)
                        if reader_error is not None:
                            raise reader_error
                        return iter(())

                client.process.stdout = Stdout()
                reader = threading.Thread(target=client._read)
                reader.start()
                self.assertTrue(entered.wait(1))
                errors = []

                def invoke():
                    try:
                        client.rpc("ping", {}, timeout=5)
                    except Exception as exc:
                        errors.append(exc)

                caller = threading.Thread(target=invoke)
                caller.start()
                deadline = time.monotonic() + 1
                while client.request_id == 0 and time.monotonic() < deadline:
                    time.sleep(0.001)
                self.assertEqual(client.request_id, 1)
                release.set()
                caller.join(1)
                reader.join(1)
                self.assertFalse(caller.is_alive())
                self.assertFalse(reader.is_alive())
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], live_mcp.LiveMcpTransportError)
                self.assertEqual(errors[0].request_id, 1)
                self.assertEqual(errors[0].retry_safe, False)
                self.assertIs(errors[0].reader_error, reader_error)
                writes = client.process.stdin.getvalue().splitlines()
                self.assertEqual(len(writes), 1)
                with self.assertRaises(live_mcp.LiveMcpTransportError):
                    client.wait_response(1, timeout=0)
                with self.assertRaises(live_mcp.LiveMcpTransportError):
                    client.rpc("next", {}, timeout=0)
                self.assertEqual(client.process.stdin.getvalue().splitlines(), writes)

    def test_buffered_original_response_remains_recoverable_after_eof(self):
        client = self.transport_client()
        with self.assertRaises(live_mcp.LiveMcpTimeoutError):
            client.rpc("write", {}, timeout=0)
        client.process.stdout = io.StringIO(self.frame(1, result={"late": "receipt"}) + "\n")
        client._read()
        self.assertEqual(client.wait_response(1, timeout=0), {"late": "receipt"})
        self.assertEqual(len(client.process.stdin.getvalue().splitlines()), 1)

    def test_constructor_handshake_failure_closes_and_reaps_owned_process(self):
        class Process:
            def __init__(self):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO()
                self.wait_calls = []
                self.terminate_calls = 0
                self.kill_calls = 0

            def wait(self, timeout):
                self.wait_calls.append(timeout)
                if len(self.wait_calls) == 1:
                    raise subprocess.TimeoutExpired("offline-fixture", timeout)
                return 0

            def terminate(self):
                self.terminate_calls += 1

            def kill(self):
                self.kill_calls += 1

        process = Process()
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_text('[mcp_servers.test]\ncommand="fake"\n', encoding="utf-8")
            with patch.object(live_mcp.subprocess, "Popen", return_value=process), \
                    patch.object(live_mcp.LiveMcp, "rpc", side_effect=RuntimeError("handshake failed")), \
                    patch.object(live_mcp.threading, "Thread") as thread:
                thread.return_value.is_alive.return_value = False
                with self.assertRaisesRegex(RuntimeError, "handshake failed"):
                    live_mcp.LiveMcp(config=config, name="test")
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertEqual(process.wait_calls, [3, 3])
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 0)
        self.assertEqual(thread.return_value.start.call_count, 2)
        self.assertEqual({call.kwargs["target"].__name__ for call in thread.call_args_list},
                         {"_read", "_read_stderr"})

    def test_initialized_notification_failure_also_cleans_up_owned_process(self):
        process = Mock()
        process.stdin.write.side_effect = BrokenPipeError("initialized write failed")
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_text('[mcp_servers.test]\ncommand="fake"\n', encoding="utf-8")
            with patch.object(live_mcp.subprocess, "Popen", return_value=process), \
                    patch.object(live_mcp.LiveMcp, "rpc", return_value={}), \
                    patch.object(live_mcp.threading, "Thread") as thread:
                thread.return_value.is_alive.return_value = False
                with self.assertRaisesRegex(BrokenPipeError, "initialized write failed"):
                    live_mcp.LiveMcp(config=config, name="test")
        process.stdin.close.assert_called_once()
        process.stdout.close.assert_called_once()
        process.wait.assert_called_once_with(timeout=3)

    def test_timeout_does_not_resend_and_pending_cap_never_evicts(self):
        client = self.transport_client()
        client.MAX_PENDING_REQUESTS = 1
        with self.assertRaises(live_mcp.LiveMcpTimeoutError):
            client.rpc("write", {"dry_run": False}, timeout=0)
        writes = client.process.stdin.getvalue()
        with self.assertRaisesRegex(RuntimeError, "pending-request limit"):
            client.rpc("write", {"dry_run": False}, timeout=0)
        self.assertEqual(client.process.stdin.getvalue(), writes)
        self.assertEqual(client.pending_request_ids, (1,))
        with self.assertRaises(live_mcp.LiveMcpTimeoutError) as caught:
            client.wait_response(1, timeout=0)
        self.assertTrue(caught.exception.outcome_uncertain)
        self.assertEqual(client.process.stdin.getvalue(), writes)

    def test_ingress_backpressure_without_consumer_retains_queued_and_inflight_frames(self):
        client = self.transport_client()
        full = threading.Event()
        produced = []

        class FloodStdout:
            closed = False

            def __iter__(self):
                for index in range(1000):
                    line = LiveMcpClientTests.frame(index + 1, result={"receipt": "x" * 1000, "index": index})
                    produced.append(line)
                    if len(produced) == client.MAX_INGRESS_FRAMES + 1:
                        full.set()
                    yield line

            def close(self):
                self.closed = True

        client.process.stdout = FloodStdout()
        reader = threading.Thread(target=client._read, daemon=True)
        client._reader_thread = reader
        reader.start()
        self.assertTrue(full.wait(1))
        self.assertEqual(client.responses.maxsize, 128)
        self.assertEqual(client.responses.qsize(), 128)
        self.assertEqual(len(produced), 129)
        client.close()
        reader.join(1)
        self.assertFalse(reader.is_alive())
        retained = []
        while True:
            try:
                value = client._incoming_nowait()
            except queue.Empty:
                break
            if isinstance(value, str):
                retained.append(value)
        self.assertEqual(retained, produced)
        self.assertTrue(client.process.stdout.closed)

    def test_deadline_drain_has_fixed_cap_even_with_unbounded_substitute_queue(self):
        client = self.transport_client()
        client.responses = queue.Queue()
        for index in range(1000):
            client.responses.put(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress",
                                             "params": {"index": index}}))
        with self.assertRaises(live_mcp.LiveMcpTimeoutError):
            client.rpc("read", {}, timeout=0)
        self.assertEqual(client.responses.qsize(), 1000 - client.MAX_DEADLINE_DRAIN)
        self.assertEqual(client.MAX_DEADLINE_DRAIN, 128)

    def test_close_interrupts_long_wait_before_waiting_for_consumer_lock(self):
        client = self.transport_client()
        waiting = threading.Event()

        class WaitingQueue(queue.Queue):
            def get(self, block=True, timeout=None):
                if block:
                    waiting.set()
                return super().get(block=block, timeout=timeout)

        client.responses = WaitingQueue(maxsize=128)
        errors = []

        def invoke():
            try:
                client.rpc("write", {}, timeout=180)
            except Exception as exc:
                errors.append(exc)

        caller = threading.Thread(target=invoke, daemon=True)
        caller.start()
        self.assertTrue(waiting.wait(1))
        closer = threading.Thread(target=client.close, daemon=True)
        closer.start()
        closer.join(1)
        caller.join(1)
        self.assertFalse(closer.is_alive())
        self.assertFalse(caller.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], live_mcp.LiveMcpTransportError)
        self.assertEqual(errors[0].request_id, 1)
        self.assertTrue(errors[0].outcome_uncertain)
        self.assertFalse(errors[0].retry_safe)
        self.assertEqual(client.pending_request_ids, (1,))
        client.process.wait.assert_called_once_with(timeout=3)
        client.close()
        client.process.wait.assert_called_once_with(timeout=3)

    def test_close_leaves_reader_owned_stdout_to_reader_after_bounded_join(self):
        client = self.transport_client()
        reading = threading.Event()
        release = threading.Event()
        closed_by = []

        class ReaderOwnedStdout:
            def __iter__(self):
                reading.set()
                release.wait(5)
                return iter(())

            def close(self):
                # Closing from the main/closer thread would block on a real
                # TextIOWrapper lock while its reader is waiting for EOF.
                if not release.is_set():
                    raise AssertionError("cross-thread stdout close during read")
                closed_by.append(threading.current_thread())

        client.process.stdout = ReaderOwnedStdout()
        reader = threading.Thread(target=client._read, daemon=True)
        client._reader_thread = reader
        reader.start()
        self.assertTrue(reading.wait(1))
        closer = threading.Thread(target=client.close, daemon=True)
        try:
            closer.start()
            closer.join(1)
            self.assertFalse(closer.is_alive())
            self.assertTrue(reader.is_alive())
            self.assertEqual(closed_by, [])
        finally:
            release.set()
            reader.join(1)
        self.assertFalse(reader.is_alive())
        self.assertEqual(closed_by, [reader])

    def test_close_does_not_close_stdin_while_writer_owns_it(self):
        client = self.transport_client()
        writing = threading.Event()
        release = threading.Event()
        errors = []

        class BlockingStdin:
            def write(self, value):
                writing.set()
                release.wait(5)
                writing.clear()
                raise BrokenPipeError("owned child stopped")

            def close(self):
                if writing.is_set():
                    raise AssertionError("cross-thread stdin close during write")

        client.process.stdin = BlockingStdin()
        client.process.terminate.side_effect = release.set

        def invoke():
            try:
                client.rpc("write", {}, timeout=180)
            except Exception as exc:
                errors.append(exc)

        caller = threading.Thread(target=invoke, daemon=True)
        caller.start()
        self.assertTrue(writing.wait(1))
        closer = threading.Thread(target=client.close, daemon=True)
        try:
            closer.start()
            closer.join(1)
            caller.join(1)
            self.assertFalse(closer.is_alive())
            self.assertFalse(caller.is_alive())
        finally:
            release.set()
        client.process.terminate.assert_called_once()
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], live_mcp.LiveMcpTransportError)
        self.assertTrue(errors[0].outcome_uncertain)
        self.assertFalse(errors[0].retry_safe)
        self.assertEqual(client.pending_request_ids, (1,))


if __name__ == "__main__":
    unittest.main()
