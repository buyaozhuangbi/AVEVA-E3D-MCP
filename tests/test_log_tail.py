"""Configured-file tails retain old text semantics with bounded chunk reads."""
from __future__ import annotations

import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp import log_tail
from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import _read_logs, create_server


class LogTailTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "configured.log"

    def assert_matches_previous(self, data, *, chunk_sizes=(1, 2, 3, 7, 64), tails=(1, 2, 5, 500)):
        self.path.write_bytes(data)
        previous = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        for chunk_size in chunk_sizes:
            with patch.object(log_tail, "_READ_CHUNK_SIZE", chunk_size):
                for tail in tails:
                    with self.subTest(data=data, chunk_size=chunk_size, tail=tail):
                        self.assertEqual(log_tail.read_log_tail(self.path, tail), previous[-tail:])

    def test_empty_final_newline_and_blank_lines_match_splitlines(self):
        for data in (b"", b"alpha", b"\n", b"\n\n", b"alpha\n", b"alpha\n\n",
                     b"\r", b"\r\r", b"alpha\r\nbeta\rgamma\n", b" \t \n\nlast  "):
            self.assert_matches_previous(data)

    def test_every_splitlines_separator_and_nonseparator_is_preserved(self):
        separators = "\n\r\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029"
        data = ("first" + separators + "last\x1f\t \u00a0  ").encode("utf-8")
        self.assert_matches_previous(data)
        self.path.write_bytes(data)
        self.assertEqual(log_tail.read_log_tail(self.path, 1), ["last\x1f\t \u00a0  "])

    def test_crlf_boundaries_do_not_create_extra_blank_lines(self):
        self.assert_matches_previous(b"one\r\ntwo\r\n\r\nthree\r\nfour\r\n", chunk_sizes=range(1, 10))

    def test_utf8_bom_multibyte_and_replacement_decoding_are_unchanged(self):
        self.assert_matches_previous(
            b"\xef\xbb\xbf" + "\u9519\u8bef\U0001f642\r\n\u4e8c\x85\u4e09\u2028\u56db\u2029".encode("utf-8")
            + b"\xff\xc0\xaf\xed\xa0\x80\r\nfinal\xe2\x82",
        )
        self.path.write_bytes(b"\xef\xbb\xbfline\n")
        self.assertEqual(log_tail.read_log_tail(self.path, 1), ["\ufeffline"])

    def test_differential_mixed_byte_fixtures_across_small_chunks(self):
        generator = random.Random(20261119)
        tokens = [b"a", b" ", b"\t", b"\r", b"\n", b"\r\n", b"\v", b"\f", b"\x1c",
                  b"\x1d", b"\x1e", b"\x1f", b"\xc2\x85", b"\xe2\x80\xa8",
                  b"\xe2\x80\xa9", b"\xff", b"\xc2", b"\x85", b"\xe2\x82",
                  "\u4e2d\U0001f642".encode("utf-8")]
        for _ in range(60):
            data = b"".join(generator.choice(tokens) for _ in range(generator.randrange(1, 80)))
            self.assert_matches_previous(data, chunk_sizes=(1, 4, 13), tails=(1, 3, 500))

    def test_long_lines_across_many_chunks_remain_exact(self):
        # Many fragments precede a separator and EOF; neither line is truncated.
        first = "first:" + "x" * (256 * 1024 + 3)
        final = "final:" + "\u9519\u8bef" * (128 * 1024 + 1)
        self.path.write_bytes((first + "\nshort\n" + final).encode("utf-8"))
        with patch.object(log_tail, "_READ_CHUNK_SIZE", 257):
            self.assertEqual(log_tail.read_log_tail(self.path, 3), [first, "short", final])
            self.assertEqual(log_tail.read_log_tail(self.path, 1), [final])

    def test_reads_use_fixed_sizes_and_never_read_text_or_unbounded_read(self):
        self.path.write_bytes((b"old diagnostic\n" * 20000) + b"latest\nlast")
        reads = []
        opened = []
        original_open = Path.open

        class TrackedStream:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *arguments):
                self.stream.close()

            def read(self, size=-1):
                reads.append(size)
                if size != log_tail._READ_CHUNK_SIZE:
                    raise AssertionError("Log reads must request the fixed positive chunk size")
                return self.stream.read(size)

        def tracked_open(path, *arguments, **keywords):
            opened.append((path, arguments, keywords))
            return TrackedStream(original_open(path, *arguments, **keywords))

        with patch.object(Path, "open", tracked_open), \
                patch.object(Path, "read_text", side_effect=AssertionError("Whole-file read is forbidden")):
            actual = log_tail.read_log_tail(self.path, 2)
        self.assertEqual(actual, ["latest", "last"])
        self.assertGreater(len(reads), 3)
        self.assertEqual(opened, [(self.path, ("r",), {"encoding": "utf-8", "errors": "replace"})])


class ConfiguredLogToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "configured.log"
        self.path.write_bytes(b"earlier\r\nlatest\xff\r\nlast")

    @staticmethod
    def call(server, arguments):
        return server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "e3d_read_logs", "arguments": arguments}})["result"]

    def test_production_tool_preserves_configured_file_shape_without_bridge_calls(self):
        for mode in ("bridge", "mock"):
            with self.subTest(mode=mode):
                settings = Settings(mode=mode, version="E3D21", allow_commands=False, log_file=str(self.path))
                server = create_server(settings)
                with patch.object(BridgeClient, "_request") as request:
                    response = self.call(server, {"tail": 2})
                request.assert_not_called()
                self.assertFalse(response.get("isError", False), response)
                self.assertEqual(response["structuredContent"], {
                    "ok": True, "source": str(self.path), "version": "E3D21", "lines": ["latest\ufffd", "last"]})

    def test_default_tail_remains_100(self):
        self.path.write_bytes(b"".join(f"line {index}\n".encode("utf-8") for index in range(130)))
        server = create_server(Settings(version="E3D21", log_file=str(self.path)))
        actual = self.call(server, {})["structuredContent"]
        self.assertEqual(actual["lines"], [f"line {index}" for index in range(30, 130)])

    def test_invalid_tail_is_rejected_before_file_or_bridge_reads(self):
        server = create_server(Settings(log_file=str(self.path)))
        with patch("aveva_e3d_mcp.tools.read_log_tail") as read, patch.object(BridgeClient, "logs") as logs:
            for tail in (0, 501, -1, True, False, 1.5, "2", None):
                with self.subTest(tail=tail):
                    response = self.call(server, {"tail": tail})
                    self.assertTrue(response["isError"])
                    self.assertIn("tail must be an integer between 1 and 500", response["content"][0]["text"])
            read.assert_not_called()
            logs.assert_not_called()

    def test_missing_file_or_directory_preserves_error_without_bridge_fallback(self):
        for path in (self.path.with_name("missing.log"), self.path.parent):
            with self.subTest(path=str(path)):
                server = create_server(Settings(mode="bridge", version="E3D21", log_file=str(path)))
                with patch("aveva_e3d_mcp.tools.read_log_tail") as read, patch.object(BridgeClient, "logs") as logs:
                    response = self.call(server, {"tail": 2})
                self.assertTrue(response["isError"])
                self.assertEqual(response["content"][0]["text"], f"E3D_LOG_FILE does not exist: {path}")
                read.assert_not_called()
                logs.assert_not_called()

    def test_configured_path_still_expands_user(self):
        settings = Settings(version="E3D21", log_file="~/configured.log")
        with patch.object(Path, "expanduser", return_value=self.path) as expand, \
                patch.object(BridgeClient, "logs") as logs:
            actual = _read_logs(BridgeClient(settings), settings, 1)
        expand.assert_called_once_with()
        logs.assert_not_called()
        self.assertEqual(actual, {"ok": True, "source": str(self.path), "version": "E3D21", "lines": ["last"]})

    def test_unconfigured_tool_preserves_native_or_mock_log_response(self):
        for mode in ("bridge", "mock"):
            with self.subTest(mode=mode):
                server = create_server(Settings(mode=mode, version="E3D21"))
                native = {"ok": True, "source": "existing source", "lines": ["native"], "extra": "preserved"}
                with patch.object(BridgeClient, "logs", return_value=native) as logs:
                    response = self.call(server, {"tail": 7})
                logs.assert_called_once_with(7)
                self.assertEqual(response["structuredContent"], native)

    def test_file_read_failure_is_not_hidden_by_bridge_fallback(self):
        server = create_server(Settings(mode="bridge", log_file=str(self.path)))
        with patch("aveva_e3d_mcp.tools.read_log_tail", side_effect=PermissionError("fixture access denied")), \
                patch.object(BridgeClient, "logs") as logs:
            response = self.call(server, {"tail": 2})
        logs.assert_not_called()
        self.assertTrue(response["isError"])
        self.assertEqual(response["content"][0]["text"], "fixture access denied")


if __name__ == "__main__":
    unittest.main()
