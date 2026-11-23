"""Stdio client for an explicitly invoked registered MCP tool (Python 3.11+).

Importing this module starts no process. The CLI dispatches the named tool with
the supplied arguments; callers choose the tool's dry_run setting explicitly.
"""
from __future__ import annotations

import base64
import codecs
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import tomllib
from collections import deque
from pathlib import Path


def structured_content(response):
    """Decode legacy JSON text without discarding the original MCP receipt."""
    value = response.get("structuredContent")
    if isinstance(value, dict):
        return value
    for item in response.get("content", []):
        if item.get("type") != "text":
            continue
        try:
            value = json.loads(item.get("text", ""))
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return response


class LiveMcpToolError(RuntimeError):
    """Retain the complete response, even when its creation journal is large."""

    def __init__(self, tool_name, response, *, expected_error=False):
        self.tool_name = tool_name
        self.response = response
        self.structured_content = structured_content(response)
        self.expected_error = expected_error
        messages = []

        def visit(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"error", "message", "reason", "error_message"} and isinstance(child, str) and child:
                        messages.append(child)
                    elif isinstance(child, (dict, list)):
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(self.structured_content)
        primary = []
        for value in (self.structured_content.get("result"), self.structured_content):
            if isinstance(value, dict):
                for key in ("error", "error_message"):
                    if isinstance(value.get(key), str) and value[key]:
                        primary.append(value[key])
        if not messages:
            messages = [item.get("text", "") for item in response.get("content", [])
                        if item.get("type") == "text"]
        # Preserve the actual operation error before secondary cleanup reasons.
        primary = list(dict.fromkeys(primary))
        secondary = [message for message in dict.fromkeys(messages) if message not in primary]
        selected = primary[:2] + secondary[-(4 - min(len(primary), 2)):]
        summary = " | ".join(message if len(message) <= 420 else message[:208] + " ... " + message[-207:]
                             for message in selected)
        prefix = "Expected an error but operation returned success" if expected_error else "MCP tool failed"
        super().__init__(f"{tool_name}: {prefix}" + (f": {summary}" if summary else ""))


class LiveMcpTimeoutError(RuntimeError):
    """A sent request remains unresolved; waiting again never resends it."""

    def __init__(self, request_id, method):
        self.request_id = request_id
        self.method = method
        self.outcome_uncertain = True
        self.retry_safe = False
        super().__init__(f"MCP observation timed out for request {request_id} ({method}); "
                         "operation outcome is unknown. Do not replay writes. "
                         "Use wait_response with the original request_id to wait without sending.")


class LiveMcpProtocolError(RuntimeError):
    """Keep the full JSON-RPC envelope or malformed frame on protocol failure."""

    def __init__(self, message, *, request_id=None, method=None, response=None,
                 raw_line=None, awaiting_request_id=None):
        self.request_id = request_id
        self.method = method
        self.response = response
        self.raw_line = raw_line
        self.awaiting_request_id = awaiting_request_id
        super().__init__(message)


class LiveMcpTransportError(RuntimeError):
    def __init__(self, message, *, request_id=None, method=None, reader_error=None,
                 stderr_tail="", stderr_error=None, stderr_complete=False,
                 stderr_truncated=False, stderr_dropped_characters=0):
        self.request_id = request_id
        self.method = method
        self.reader_error = reader_error
        self.stderr_tail = stderr_tail
        self.stderr_error = stderr_error
        self.stderr_complete = stderr_complete
        self.stderr_truncated = stderr_truncated
        self.stderr_dropped_characters = stderr_dropped_characters
        self.outcome_uncertain = request_id is not None
        self.retry_safe = False
        super().__init__(message)


class _ReaderTermination:
    def __init__(self, error=None):
        self.error = error


class _CloseWake:
    pass


class LiveMcp:
    MAX_PENDING_REQUESTS = 64
    MAX_INGRESS_FRAMES = 128
    MAX_DEADLINE_DRAIN = 128
    MAX_STDERR_TAIL = 65536
    STDERR_READ_SIZE = 4096
    _CONSUMER_IDLE_TIMEOUT = 0.2
    _READER_JOIN_TIMEOUT = 0.2

    def __init__(self, config: Path | None = None, name: str = "aveva-e3d21"):
        self.config = config or Path.home() / ".codex" / "config.toml"
        registration = tomllib.loads(self.config.read_text(encoding="utf-8"))["mcp_servers"][name]
        if not registration.get("enabled", True):
            raise RuntimeError("MCP registration is disabled")
        env = os.environ.copy()
        env.update(registration.get("env", {}))
        self._initialize_transport_state()
        self.process = subprocess.Popen(
            [registration["command"], *registration.get("args", [])],
            cwd=registration.get("cwd", str(Path.home())), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        try:
            self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
            self._stderr_thread.start()
            self._reader_thread = threading.Thread(target=self._read, daemon=True)
            self._reader_thread.start()
            self.initialization = self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                    "clientInfo": {"name": "e3d-live-development", "version": "1"}})
            self.process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            self.process.stdin.flush()
        except BaseException as exc:
            try:
                self.close()
            except Exception as cleanup_error:
                exc.add_note(f"Owned MCP subprocess cleanup failed: {cleanup_error}")
            raise

    def _initialize_transport_state(self):
        self.responses = queue.Queue(maxsize=self.MAX_INGRESS_FRAMES)
        self.request_id = 0
        self.calls = 0
        self._consumer_lock = threading.RLock()
        self._close_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._cleanup_complete = False
        self._pending = {}
        self._available = {}
        self._notifications = deque(maxlen=64)
        self._reader_terminal = None
        self._reader_deferred = None
        self._reader_thread = None
        self._stderr_thread = None
        self._stderr_lock = threading.Lock()
        self._stderr_chunks = deque()
        self._stderr_length = 0
        self._stderr_error = None
        self._stderr_complete = False
        self._stderr_dropped_characters = 0
        self._write_error = None
        self._closed = False

    @property
    def pending_request_ids(self):
        """Sent request IDs whose result/error has not yet been consumed."""
        with self._consumer_lock:
            return tuple(sorted(self._pending))

    @property
    def available_response_ids(self):
        """Pending IDs with a complete response already routed to its buffer."""
        with self._consumer_lock:
            return tuple(sorted(self._available))

    def _read(self):
        terminal = _ReaderTermination()
        try:
            if self._shutdown.is_set():
                return
            for line in self.process.stdout:
                if not self._enqueue_incoming(line) or self._shutdown.is_set():
                    break
        except Exception as exc:
            terminal = _ReaderTermination(exc)
        finally:
            # A process can close stdout before its stderr reader observes EOF.
            # Join only for a bounded interval; descendants may inherit the
            # pipe and keep the dedicated reader blocked.
            self._join_stderr_reader()
            # Publish persistent terminal state as well as waking a current
            # waiter. All preceding frames remain queued for their original IDs.
            self._reader_terminal = terminal
            self._wake_consumer(terminal)
            self._close_stream(self.process.stdout)

    def _read_stderr(self):
        error = None
        complete = False
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                # read1 returns currently available bytes without waiting for
                # a full chunk or a newline; an inherited pipe must not hide a
                # short startup diagnostic while its owner remains alive.
                raw = self.process.stderr.buffer.read1(self.STDERR_READ_SIZE)
                if not raw:
                    tail = decoder.decode(b"", final=True)
                    if tail:
                        self._append_stderr(tail)
                    complete = True
                    break
                chunk = decoder.decode(raw, final=False)
                if chunk:
                    self._append_stderr(chunk)
        except Exception as exc:
            error = exc
        finally:
            with self._stderr_lock:
                self._stderr_error = error
                self._stderr_complete = complete
            # The stderr reader owns this wrapper. close() never closes it
            # from another thread while a descendant may still hold the pipe.
            self._close_stream(self.process.stderr)

    def _append_stderr(self, chunk):
        with self._stderr_lock:
            self._stderr_chunks.append(chunk)
            self._stderr_length += len(chunk)
            while self._stderr_length > self.MAX_STDERR_TAIL:
                oldest = self._stderr_chunks.popleft()
                excess = self._stderr_length - self.MAX_STDERR_TAIL
                if len(oldest) <= excess:
                    self._stderr_length -= len(oldest)
                    self._stderr_dropped_characters += len(oldest)
                else:
                    self._stderr_chunks.appendleft(oldest[excess:])
                    self._stderr_length -= excess
                    self._stderr_dropped_characters += excess

    def _join_stderr_reader(self):
        reader = self._stderr_thread
        if reader is not None and reader is not threading.current_thread() and reader.is_alive():
            reader.join(timeout=self._READER_JOIN_TIMEOUT)

    def _stderr_snapshot(self):
        with self._stderr_lock:
            tail = "".join(self._stderr_chunks)
            error = self._stderr_error
            complete = self._stderr_complete
            dropped = self._stderr_dropped_characters
        return tail, error, complete, dropped

    @staticmethod
    def _close_stream(stream):
        try:
            close = getattr(stream, "close", None)
            if close is not None:
                close()
        except (OSError, ValueError):
            pass

    def _wake_consumer(self, frame):
        try:
            self.responses.put_nowait(frame)
        except queue.Full:
            # A full queue already gives the consumer work. Persistent closed/
            # terminal state wakes it logically without removing any receipt.
            pass

    def _enqueue_incoming(self, line):
        while not self._shutdown.is_set():
            try:
                self.responses.put(line, timeout=0.05)
                return True
            except queue.Full:
                continue
        try:
            self.responses.put_nowait(line)
        except queue.Full:
            # Preserve the single frame already read when shutdown interrupts
            # producer backpressure. The consumer drains it after the queue.
            self._reader_deferred = line
        return False

    def _incoming_nowait(self):
        try:
            return self.responses.get_nowait()
        except queue.Empty:
            if self._reader_deferred is not None:
                line = self._reader_deferred
                self._reader_deferred = None
                return line
            raise

    @staticmethod
    def _validate_timeout(timeout):
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0
                or timeout > threading.TIMEOUT_MAX or not math.isfinite(timeout)):
            raise ValueError(f"timeout must be a finite number between 0 and {threading.TIMEOUT_MAX}")

    def _transport_error(self, request_id=None, method=None):
        if self.process.poll() is not None:
            self._join_stderr_reader()
        terminal = self._reader_terminal
        error = terminal.error if terminal is not None and terminal.error is not None else self._write_error
        message = "MCP process closed without a response" if error is None else f"MCP transport failed: {error}"
        stderr_tail, stderr_error, stderr_complete, stderr_dropped = self._stderr_snapshot()
        return LiveMcpTransportError(message, request_id=request_id, method=method, reader_error=error,
                                     stderr_tail=stderr_tail, stderr_error=stderr_error,
                                     stderr_complete=stderr_complete,
                                     stderr_truncated=stderr_dropped > 0,
                                     stderr_dropped_characters=stderr_dropped)

    def rpc(self, method, params, timeout=180):
        """Send once and consume only this request's result; timeout keeps it pending."""
        self._validate_timeout(timeout)
        with self._consumer_lock:
            if self._closed or self._reader_terminal is not None or self._write_error is not None:
                raise self._transport_error(method=method)
            if len(self._pending) >= self.MAX_PENDING_REQUESTS:
                raise RuntimeError(f"MCP pending-request limit reached ({self.MAX_PENDING_REQUESTS}); consume an original response "
                                   "with wait_response before sending another request.")
            request_id = self.request_id + 1
            line = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}) + "\n"
            self.request_id = request_id
            self._pending[request_id] = method
            try:
                self.process.stdin.write(line)
                self.process.stdin.flush()
            except Exception as exc:
                # A failed write/flush may have sent a prefix or the full request.
                # Preserve its identity and never replay it automatically.
                self._write_error = exc
                raise self._transport_error(request_id, method) from exc
            finally:
                if self._closed:
                    # Only the writer itself may close its stream while it is
                    # returning from an interrupted write/flush.
                    self._close_stream(self.process.stdin)
            if self._closed:
                raise self._transport_error(request_id, method)
            return self._wait_response(request_id, timeout)

    def wait_response(self, request_id, timeout=180):
        """Consume an original pending result/error without sending any bytes.

        Timed-out IDs remain pending, including responses buffered while a later
        RPC waits. Unknown/already-consumed IDs raise ValueError. This helper is
        serialized with rpc; it does not introduce parallel client consumers.
        """
        self._validate_timeout(timeout)
        with self._consumer_lock:
            if type(request_id) is not int or request_id not in self._pending:
                raise ValueError("request_id is unknown or its response has already been consumed")
            return self._wait_response(request_id, timeout)

    def _wait_response(self, request_id, timeout):
        method = self._pending[request_id]
        deadline = time.monotonic() + timeout
        drain_remaining = None
        closed_at_start = self._closed
        while True:
            if request_id in self._available:
                response = self._available.pop(request_id)
                self._pending.pop(request_id)
                if "error" in response:
                    raise LiveMcpProtocolError(f"MCP JSON-RPC error for request {request_id} ({method})",
                        request_id=request_id, method=method, response=response, awaiting_request_id=request_id)
                return response["result"]
            if self._closed and not closed_at_start:
                raise self._transport_error(request_id, method)
            if ((self._reader_terminal is not None or self._closed) and self.responses.empty()
                    and self._reader_deferred is None):
                raise self._transport_error(request_id, method)
            remaining = 0 if self._closed else deadline - time.monotonic()
            if remaining <= 0:
                # The reader can have routed several frames into the queue
                # before this consumer resumes. Drain only this bounded
                # snapshot; later arrivals belong to a future wait and do not
                # extend the deadline.
                if drain_remaining is None:
                    drain_remaining = min(self.MAX_DEADLINE_DRAIN,
                        self.responses.qsize() + int(self._reader_deferred is not None))
                if drain_remaining <= 0:
                    raise LiveMcpTimeoutError(request_id, method)
                try:
                    line = self._incoming_nowait()
                except queue.Empty as exc:
                    raise LiveMcpTimeoutError(request_id, method) from exc
                drain_remaining -= 1
            else:
                try:
                    try:
                        line = self._incoming_nowait()
                    except queue.Empty:
                        line = self.responses.get(timeout=remaining)
                except queue.Empty as exc:
                    if self._reader_terminal is not None or self._closed:
                        raise self._transport_error(request_id, method) from exc
                    # Capture already-enqueued frames on the next iteration.
                    continue
            if isinstance(line, _CloseWake):
                if closed_at_start:
                    continue
                raise self._transport_error(request_id, method)
            if line is None or isinstance(line, _ReaderTermination):
                self._reader_terminal = line if isinstance(line, _ReaderTermination) else _ReaderTermination()
                raise self._transport_error(request_id, method)
            try:
                response = json.loads(line)
            except (TypeError, ValueError) as exc:
                raise LiveMcpProtocolError("MCP response is not valid JSON", request_id=request_id,
                    method=method, raw_line=line, awaiting_request_id=request_id) from exc
            if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
                raise LiveMcpProtocolError("Invalid MCP JSON-RPC response", response=response, raw_line=line,
                    awaiting_request_id=request_id)
            if "id" not in response and isinstance(response.get("method"), str):
                self._notifications.append(response)
                continue
            response_id = response.get("id")
            if (type(response_id) is not int or response_id not in self._pending or
                    response_id in self._available or ("result" in response) == ("error" in response)):
                raise LiveMcpProtocolError("Unexpected, duplicate or malformed MCP response", request_id=response_id,
                    response=response, raw_line=line, awaiting_request_id=request_id)
            self._available[response_id] = response

    def call(self, name, arguments=None, *, expect_error=False, image_path=None):
        response = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        self.calls += 1
        if bool(response.get("isError")) != expect_error:
            raise LiveMcpToolError(name, response, expected_error=expect_error)
        if image_path is not None:
            images = [item for item in response.get("content", []) if item.get("type") == "image"]
            if len(images) != 1 or images[0].get("mimeType") != "image/png":
                raise RuntimeError("Expected one MCP PNG image")
            path = Path(image_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(images[0]["data"], validate=True))
        return structured_content(response)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        """Close/reap only the subprocess owned by this client; never replay work."""
        # Do not wait for an RPC's consumer lock before interrupting its wait.
        self._closed = True
        self._shutdown.set()
        self._wake_consumer(_CloseWake())
        with self._close_lock:
            if self._cleanup_complete:
                return
            idle = self._consumer_lock.acquire(timeout=self._CONSUMER_IDLE_TIMEOUT)
            if idle:
                try:
                    self._close_stream(self.process.stdin)
                finally:
                    self._consumer_lock.release()
            else:
                # An active writer can hold both the consumer and TextIO locks.
                # Reap only our owned child to unblock it; never close its stdin
                # wrapper from another thread while write/flush is in progress.
                self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            finally:
                if not idle:
                    idle = self._consumer_lock.acquire(timeout=self._CONSUMER_IDLE_TIMEOUT)
                    if idle:
                        try:
                            self._close_stream(self.process.stdin)
                        finally:
                            self._consumer_lock.release()
                reader = self._reader_thread
                if reader is not None and reader is not threading.current_thread() and reader.is_alive():
                    reader.join(timeout=self._READER_JOIN_TIMEOUT)
                # A descendant can inherit stdout after the owned child exits.
                # Its blocked reader owns TextIO's lock; it alone closes stdout
                # in _read's finally when that read eventually finishes.
                if reader is None or not reader.is_alive():
                    self._close_stream(self.process.stdout)
                stderr_reader = self._stderr_thread
                if stderr_reader is not None and stderr_reader is not threading.current_thread() and stderr_reader.is_alive():
                    stderr_reader.join(timeout=self._READER_JOIN_TIMEOUT)
                elif stderr_reader is None or stderr_reader.ident is None:
                    # Initialization may fail before the drainer starts. In
                    # that case no other thread owns the pipe wrapper.
                    self._close_stream(self.process.stderr)
                self._cleanup_complete = True


def _cli_failure_receipt(error):
    """Preserve server envelopes verbatim, or describe an incomplete observation."""
    if (isinstance(error, LiveMcpProtocolError) and error.response is not None
            and error.raw_line is None):
        return error.response
    receipt = {"ok": False, "error": str(error), "error_type": type(error).__name__,
               "request_id": error.request_id, "method": error.method}
    if isinstance(error, LiveMcpProtocolError):
        receipt.update(response=error.response, raw_line=error.raw_line,
                       awaiting_request_id=error.awaiting_request_id,
                       outcome_uncertain=error.awaiting_request_id is not None, retry_safe=False)
    else:
        receipt.update(outcome_uncertain=error.outcome_uncertain, retry_safe=error.retry_safe)
        if isinstance(error, LiveMcpTransportError) and error.reader_error is not None:
            receipt["reader_error"] = {"type": type(error.reader_error).__name__,
                                       "message": str(error.reader_error)}
        if isinstance(error, LiveMcpTransportError):
            receipt["stderr_tail"] = error.stderr_tail
            receipt["stderr_complete"] = error.stderr_complete
            receipt["stderr_truncated"] = error.stderr_truncated
            receipt["stderr_dropped_characters"] = error.stderr_dropped_characters
            if error.stderr_error is not None:
                receipt["stderr_error"] = {"type": type(error.stderr_error).__name__,
                                            "message": str(error.stderr_error)}
    return receipt


def _cli_failure_summary(error):
    message = str(error)
    if isinstance(error, LiveMcpProtocolError) and isinstance(error.response, dict):
        detail = error.response.get("error")
        detail = detail.get("message") if isinstance(detail, dict) else None
        if isinstance(detail, str) and detail:
            # Match the existing tool-error convention: a bounded reason on
            # stderr, with complete data confined to the requested receipt.
            detail = detail if len(detail) <= 420 else detail[:208] + " ... " + detail[-207:]
            message += ": " + detail
    if isinstance(error, LiveMcpTransportError) and error.stderr_tail:
        lines = error.stderr_tail.rstrip("\r\n").splitlines()
        detail = lines[-1] if lines else error.stderr_tail
        # Keep raw stderr in the explicit receipt only. Human terminal output
        # strips control characters to prevent escape-sequence injection.
        detail = "".join(character for character in detail if character.isprintable() or character == "\t")
        if len(detail) > 420:
            detail = detail[-420:]
        qualifier = " (partial)" if not error.stderr_complete else ""
        message += ": stderr" + qualifier + ": " + detail
    return message.encode("utf-8", errors="backslashreplace").decode("utf-8")


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("tool")
    parser.add_argument("--arguments", type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    arguments = json.loads(args.arguments.read_text(encoding="utf-8")) if args.arguments else {}
    failure = None
    try:
        with LiveMcp() as client:
            result = client.call(args.tool, arguments, image_path=args.image)
    except LiveMcpToolError as exc:
        result = exc.response
        failure = exc
    except (LiveMcpProtocolError, LiveMcpTimeoutError, LiveMcpTransportError) as exc:
        result = _cli_failure_receipt(exc)
        failure = exc
    # Failed server frames can contain escaped unpaired Unicode. Escape those
    # losslessly so writing current evidence cannot itself leave a stale file.
    text = json.dumps(result, ensure_ascii=failure is not None, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    if failure is not None:
        print(_cli_failure_summary(failure), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
