"""Exercise client correlation through a real local stdio subprocess, not E3D."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from live_mcp import LiveMcp, LiveMcpTimeoutError, LiveMcpTransportError


class LiveMcpProcessTests(unittest.TestCase):
    @staticmethod
    def offline_config(directory, source):
        config = Path(directory) / "config.toml"
        config.write_text(
            "[mcp_servers.offline]\ncommand=" + json.dumps(sys.executable) +
            "\nargs=" + json.dumps(["-u", "-c", source]) + "\n",
            encoding="utf-8",
        )
        return config

    def test_startup_failure_retains_recent_stderr_diagnostic(self):
        source = "import sys; sys.stderr.write('offline startup diagnostic: bridge config rejected\\n'); sys.stderr.flush(); raise SystemExit(23)"
        with tempfile.TemporaryDirectory() as temporary:
            config = self.offline_config(temporary, source)
            with self.assertRaises(LiveMcpTransportError) as caught:
                LiveMcp(config=config, name="offline")
            error = caught.exception
            self.assertEqual(error.stderr_tail, "offline startup diagnostic: bridge config rejected" + os.linesep)
            self.assertIsNone(error.stderr_error)

    def test_huge_unterminated_invalid_utf8_stderr_keeps_only_character_tail(self):
        source = """
import sys
block = b'\\xff' + b'x' * 8191
for _ in range(256):
    sys.stderr.buffer.write(block)
sys.stderr.buffer.write(b'final diagnostic: ' + b'z' * 300 + b'\\xfe')
sys.stderr.buffer.flush()
"""
        with tempfile.TemporaryDirectory() as temporary:
            config = self.offline_config(temporary, source)
            with self.assertRaises(LiveMcpTransportError) as caught:
                LiveMcp(config=config, name="offline")
            error = caught.exception
            raw = (b"\xff" + b"x" * 8191) * 256 + b"final diagnostic: " + b"z" * 300 + b"\xfe"
            expected = raw.decode("utf-8", errors="replace")[-LiveMcp.MAX_STDERR_TAIL:]
            self.assertEqual(error.stderr_tail, expected)
            self.assertEqual(len(error.stderr_tail), LiveMcp.MAX_STDERR_TAIL)
            self.assertIsNone(error.stderr_error)

    def test_successful_replies_keep_their_shape_when_server_writes_stderr(self):
        source = """
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    sys.stderr.write('incidental server diagnostic\\n')
    sys.stderr.flush()
    if request['method'] == 'initialize':
        result = {'protocolVersion':'2024-11-05', 'capabilities':{},
                  'serverInfo':{'name':'offline-stderr-fixture','version':'1'}}
    else:
        result = {'ok':True, 'project':{'name':'fixture'}}
    print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}), flush=True)
"""
        with tempfile.TemporaryDirectory() as temporary:
            config = self.offline_config(temporary, source)
            with LiveMcp(config=config, name="offline") as client:
                result = client.rpc("tools/call", {"name": "e3d_project_info"}, timeout=5)
                self.assertEqual(result, {"ok": True, "project": {"name": "fixture"}})
                self.assertFalse(hasattr(result, "stderr_tail"))
                tail, error, complete, dropped = client._stderr_snapshot()
                self.assertIn("incidental server diagnostic", tail)
                self.assertIsNone(error)
                self.assertFalse(complete)
                self.assertEqual(dropped, 0)

    def test_late_tool_receipt_survives_a_later_ping_over_real_pipes(self):
        source = """
import json, sys, time
seen, tool_calls = [], 0
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    seen.append(request['id'])
    if request['method'] == 'initialize':
        result = {'protocolVersion':'2024-11-05', 'capabilities':{},
                  'serverInfo':{'name':'offline-delay-fixture','version':'1'}}
    elif request['method'] == 'tools/call':
        tool_calls += 1
        time.sleep(0.2)
        result = {'content':[{'type':'text','text':'original tool result'}],
                  'structuredContent':{'ok':True, 'detail':'x' * 20000, 'tool_calls':tool_calls}}
    else:
        result = {'seen':seen[:], 'tool_calls':tool_calls}
    print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}), flush=True)
"""
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_text(
                "[mcp_servers.offline]\ncommand=" + json.dumps(sys.executable) +
                "\nargs=" + json.dumps(["-u", "-c", source]) + "\n",
                encoding="utf-8",
            )
            with LiveMcp(config=config, name="offline") as client:
                with self.assertRaises(LiveMcpTimeoutError) as caught:
                    client.rpc("tools/call", {"name": "offline/read"}, timeout=0.01)
                original_id = caught.exception.request_id
                self.assertEqual(original_id, 2)
                ping = client.rpc("ping", {}, timeout=5)
                self.assertEqual(ping, {"seen": [1, 2, 3], "tool_calls": 1})
                self.assertEqual(client.available_response_ids, (original_id,))
                original = client.wait_response(original_id, timeout=0)
                self.assertTrue(original["structuredContent"]["ok"])
                self.assertEqual(original["structuredContent"]["detail"], "x" * 20000)
                self.assertEqual(original["structuredContent"]["tool_calls"], 1)
                self.assertEqual(client.pending_request_ids, ())
                final_ping = client.rpc("ping", {}, timeout=5)
                self.assertEqual(final_ping, {"seen": [1, 2, 3, 4], "tool_calls": 1})
            self.assertIsNotNone(client.process.poll())

    def test_close_does_not_wait_for_descendant_inheriting_stdout(self):
        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary) / "release-descendant"
            child_source = """
import pathlib, sys, time
release = pathlib.Path(sys.argv[1])
deadline = time.monotonic() + 8
while not release.exists() and time.monotonic() < deadline:
    time.sleep(0.02)
"""
            source = f"""
import json, subprocess, sys
subprocess.Popen([sys.executable, '-c', {child_source!r}, {str(release)!r}],
                 stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    result = {{'protocolVersion':'2024-11-05', 'capabilities':{{}},
              'serverInfo':{{'name':'offline-inherited-pipe','version':'1'}}}}
    print(json.dumps({{'jsonrpc':'2.0','id':request['id'],'result':result}}), flush=True)
"""
            config = Path(temporary) / "config.toml"
            config.write_text(
                "[mcp_servers.offline]\ncommand=" + json.dumps(sys.executable) +
                "\nargs=" + json.dumps(["-u", "-c", source]) + "\n",
                encoding="utf-8",
            )
            client = None
            try:
                client = LiveMcp(config=config, name="offline")
                started = time.monotonic()
                client.close()
                self.assertLess(time.monotonic() - started, 2.5)
                self.assertIsNotNone(client.process.poll())
            finally:
                # Only this fixture descendant watches this private file.
                # Let it exit naturally; do not terminate any other process.
                release.touch()
                if client is not None:
                    client.close()
                    client._reader_thread.join(timeout=2)
            if client is not None:
                self.assertFalse(client._reader_thread.is_alive())

    def test_close_does_not_wait_for_descendant_inheriting_stderr(self):
        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary) / "release-descendant"
            child_source = """
import pathlib, sys, time
release = pathlib.Path(sys.argv[1])
sys.stderr.write('descendant stderr fixture\\n')
sys.stderr.flush()
deadline = time.monotonic() + 8
while not release.exists() and time.monotonic() < deadline:
    time.sleep(0.02)
"""
            source = f"""
import json, subprocess, sys
subprocess.Popen([sys.executable, '-c', {child_source!r}, {str(release)!r}],
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    result = {{'protocolVersion':'2024-11-05', 'capabilities':{{}},
              'serverInfo':{{'name':'offline-inherited-stderr','version':'1'}}}}
    print(json.dumps({{'jsonrpc':'2.0','id':request['id'],'result':result}}), flush=True)
"""
            config = self.offline_config(temporary, source)
            client = None
            try:
                client = LiveMcp(config=config, name="offline")
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    tail, _, _, _ = client._stderr_snapshot()
                    if "descendant stderr fixture" in tail:
                        break
                    time.sleep(0.01)
                self.assertIn("descendant stderr fixture", tail)
                started = time.monotonic()
                client.close()
                self.assertLess(time.monotonic() - started, 2.5)
                self.assertIsNotNone(client.process.poll())
            finally:
                # This fixture child observes only its private release file;
                # let it exit naturally and then verify its pipe reader ends.
                release.touch()
                if client is not None:
                    client.close()
                    client._stderr_thread.join(timeout=2)
            if client is not None:
                self.assertFalse(client._stderr_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
