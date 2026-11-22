using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Globalization;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace E3d21AiBridge
{
    internal sealed class ManagedRing : IE3d21MessageWindowContext
    {
        private readonly object messageLock = new object();
        private readonly Queue<Dictionary<string, object>> outputMessages = new Queue<Dictionary<string, object>>();
        private long outputMessageSequence;
        internal ManagedRing(long sequence = 0) { outputMessageSequence = sequence; }
        internal void Emit(string text = "message")
        {
            lock (messageLock)
            {
                if (outputMessages.Count >= 500) outputMessages.Dequeue();
                outputMessages.Enqueue(new Dictionary<string, object>
                { ["sequence"] = ++outputMessageSequence, ["description"] = text, ["type"] = "INFO" });
            }
        }
        // No SDK, dispatch, fault-barrier or CommandState stubs are present. A
        // production message method acquiring such a dependency fails to build.
        // PRODUCTION_CONTEXT_METHODS
    }

    internal sealed class FailingRing : IE3d21MessageWindowContext
    {
        public MessageWindowSnapshot ReadMessageWindow(long afterSequence, int limit, bool checkpoint)
        { throw new InvalidOperationException("SECRET source not for diagnostics"); }
    }
    internal sealed class CountingRing : IE3d21MessageWindowContext
    {
        internal int Calls;
        internal readonly ManagedRing Ring = new ManagedRing();
        public MessageWindowSnapshot ReadMessageWindow(long afterSequence, int limit, bool checkpoint)
        { Calls++; return Ring.ReadMessageWindow(afterSequence, limit, checkpoint); }
    }
    internal sealed class WindowInnerRequest { internal NameValueCollection QueryString; }
    internal sealed class WindowResponse { internal object Value; internal int Status; }
    internal sealed class WindowRequest
    {
        internal readonly WindowInnerRequest Request = new WindowInnerRequest();
        internal readonly WindowResponse Response = new WindowResponse();
        internal WindowRequest(NameValueCollection query) { Request.QueryString = query; }
    }
    internal sealed class E3d21HttpAdapter
    {
        private readonly string sessionId = Guid.NewGuid().ToString("D");
        private readonly MessageCursorCodec messageCursors = new MessageCursorCodec();
        internal string SessionId { get { return sessionId; } }
        internal string Cursor(long position) { return messageCursors.Encode(sessionId, position); }
        internal string CursorForSession(string session, long position) { return messageCursors.Encode(session, position); }
        internal async Task<WindowResponse> Invoke(object context, NameValueCollection query = null, string method = "GET")
        {
            var request = new WindowRequest(query ?? new NameValueCollection());
            await Handle(request, context, method);
            return request.Response;
        }
        private async Task Handle(WindowRequest request, object context, string method)
        {
            bool managedMessageWindowQuery = true;
            try
            {
                if (method != "GET") throw new MessageCursorException("message_cursor_request_invalid", "Message windows require GET.", 405);
                object response;
                // PRODUCTION_ROUTE
                await WriteJsonAsync(request.Response, response, 200).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                // PRODUCTION_FAILURE
                throw;
            }
        }
        private static Task WriteJsonAsync(WindowResponse response, object value, int status)
        { response.Value = value; response.Status = status; return Task.CompletedTask; }
    }

    internal static class MessageCursorHarness
    {
        private static int checks;
        private static void Check(bool condition, string label)
        { checks++; if (!condition) throw new InvalidOperationException("Check failed: " + label); }
        private static object Field(object value, string name)
        { return value.GetType().GetProperty(name).GetValue(value); }
        private static Dictionary<string, object> Result(WindowResponse response)
        { return (Dictionary<string, object>)Field(response.Value, "result"); }
        private static List<object> Rows(Dictionary<string, object> result) { return (List<object>)result["messages"]; }
        private static NameValueCollection Query(params string[] pairs)
        {
            var result = new NameValueCollection();
            for (int index = 0; index < pairs.Length; index += 2) result.Add(pairs[index], pairs[index + 1]);
            return result;
        }
        private static WindowResponse Read(E3d21HttpAdapter adapter, object ring, NameValueCollection query = null, string method = "GET")
        { return adapter.Invoke(ring, query, method).GetAwaiter().GetResult(); }
        private static Dictionary<string, object> Valid(WindowResponse response, E3d21HttpAdapter adapter)
        {
            Check(response.Status == 200 && (bool)Field(response.Value, "ok"), "successful window envelope");
            Check((string)Field(response.Value, "version") == "E3D21" && (string)Field(response.Value, "session_id") == adapter.SessionId,
                "envelope identity is real adapter session");
            Guid build;
            Check(Guid.TryParseExact((string)Field(response.Value, "bridge_build"), "D", out build), "build identity present");
            var result = Result(response);
            Check((int)result["schema_version"] == 1 && (bool)result["success"] && (bool)result["available"], "explicit result schema and availability");
            Check((bool)result["managed_only"] && !(bool)result["operation_correlated"], "managed evidence never claims causation");
            Check((int)result["returned_count"] == Rows(result).Count, "returned count matches rows");
            Check((bool)result["has_more"] == ((int)result["remaining_count"] > 0), "remaining count determines pagination");
            Check((bool)result["complete_since_after_sequence"] == ((string)result["overwritten_count"] == "0" && !(bool)result["has_more"]),
                "completeness distinguishes pagination and ring loss");
            foreach (string key in new[] { "after_sequence", "latest_sequence", "next_sequence", "overwritten_count" })
            {
                long value;
                Check(result[key] is string && long.TryParse((string)result[key], NumberStyles.None, CultureInfo.InvariantCulture, out value) && value >= 0,
                    key + " is a lossless nonnegative decimal string");
            }
            return result;
        }
        private static void Error(WindowResponse response, E3d21HttpAdapter adapter, string code, int status)
        {
            Check(response.Status == status && !(bool)Field(response.Value, "ok"), "structured failure HTTP " + status);
            Check((string)Field(response.Value, "error_code") == code, "structured failure code " + code);
            Check((string)Field(response.Value, "session_id") == adapter.SessionId && (string)Field(response.Value, "version") == "E3D21",
                "errors retain real adapter identity");
            Check((bool)Field(response.Value, "managed_only") && !(bool)Field(response.Value, "operation_correlated"), "failure is managed only");
            Check(response.Value.GetType().GetProperty("result") == null && response.Value.GetType().GetProperty("next_cursor") == null &&
                response.Value.GetType().GetProperty("state") == null, "failure has no ring, cursor or native state");
            Check(!((string)Field(response.Value, "error")).Contains("SECRET"), "unexpected error text is not echoed");
        }

        private static void EmptyAndCheckpoint()
        {
            var adapter = new E3d21HttpAdapter();
            var ring = new ManagedRing();
            var empty = Valid(Read(adapter, ring), adapter);
            Check((string)empty["status"] == "complete" && empty["first_retained_sequence"] == null &&
                (string)empty["next_sequence"] == "0" && (int)empty["requested_limit"] == 100, "empty initial default window");
            for (int index = 0; index < 500; index++) ring.Emit();
            var checkpoint = Valid(Read(adapter, ring, Query("checkpoint", "true")), adapter);
            Check((string)checkpoint["status"] == "checkpoint" && (bool)checkpoint["checkpoint"] &&
                (string)checkpoint["after_sequence"] == "500" && (string)checkpoint["latest_sequence"] == "500" &&
                (string)checkpoint["next_sequence"] == "500" && Rows(checkpoint).Count == 0 &&
                (string)checkpoint["overwritten_count"] == "0", "checkpoint snapshots high water without old rows");
            ring.Emit("after checkpoint");
            var delta = Valid(Read(adapter, ring, Query("cursor", (string)checkpoint["next_cursor"])), adapter);
            Check(Rows(delta).Count == 1 && (string)((Dictionary<string, object>)Rows(delta)[0])["description"] == "after checkpoint" &&
                (string)delta["after_sequence"] == "500" && (string)delta["next_sequence"] == "501", "checkpoint resumes after exclusive position");
            var current = Valid(Read(adapter, ring, Query("cursor", (string)delta["next_cursor"])), adapter);
            Check(Rows(current).Count == 0 && (string)current["status"] == "complete", "unchanged cursor interval is empty and complete");
            var newEmpty = new ManagedRing();
            var emptyAdapter = new E3d21HttpAdapter();
            var emptyCheckpoint = Valid(Read(emptyAdapter, newEmpty, Query("checkpoint", "true")), emptyAdapter);
            Check((string)emptyCheckpoint["status"] == "checkpoint" && emptyCheckpoint["first_retained_sequence"] == null &&
                (string)emptyCheckpoint["next_sequence"] == "0", "empty checkpoint retains nullable first sequence");
            var explicitEmpty = Valid(Read(emptyAdapter, newEmpty, Query("checkpoint", "true", "cursor", "")), emptyAdapter);
            Check((string)explicitEmpty["status"] == "checkpoint", "empty cursor is absence for checkpoint compatibility");
        }

        private static void PagesAndLoss()
        {
            var adapter = new E3d21HttpAdapter();
            var ring = new ManagedRing();
            for (int index = 0; index < 1002; index++) ring.Emit();
            var page = Valid(Read(adapter, ring, Query("limit", "100")), adapter);
            Check((string)page["status"] == "overwritten" && (string)page["overwritten_count"] == "502" &&
                (string)page["first_retained_sequence"] == "503" && (int)page["remaining_count"] == 400,
                "overflow reports exact retained boundary and loss separately from page truncation");
            var seen = new List<string>();
            while (true)
            {
                seen.AddRange(Rows(page).Select(row => (string)((Dictionary<string, object>)row)["sequence"]));
                if (!(bool)page["has_more"]) break;
                page = Valid(Read(adapter, ring, Query("cursor", (string)page["next_cursor"], "limit", "100")), adapter);
                Check((string)page["overwritten_count"] == "0", "later retained page has no fabricated loss");
            }
            Check(seen.Count == 500 && seen.Distinct().Count() == 500 && seen[0] == "503" && seen[499] == "1002",
                "pagination returns every retained row once in sequence order");
            Check((string)page["status"] == "complete" && (bool)page["complete_since_after_sequence"], "last page completes its own interval");
            var exact = new ManagedRing();
            for (int index = 0; index < 500; index++) exact.Emit();
            var all = Valid(Read(adapter, exact, Query("limit", "500")), adapter);
            Check(Rows(all).Count == 500 && (string)all["overwritten_count"] == "0" && !(bool)all["has_more"],
                "exact capacity and maximum limit have no omission");
        }

        private static void PrecisionAndLegacy()
        {
            const long before = 9007199254740992L;
            var adapter = new E3d21HttpAdapter();
            var ring = new ManagedRing(before);
            ring.Emit("beyond JS precision");
            var result = Valid(Read(adapter, ring, Query("cursor", adapter.Cursor(before))), adapter);
            Check((string)result["next_sequence"] == "9007199254740993" &&
                (string)((Dictionary<string, object>)Rows(result)[0])["sequence"] == "9007199254740993", "wire strings retain sequence beyond 2^53");
            var legacy = (Dictionary<string, object>)ring.ReadMessages(1)[0];
            Check(legacy["sequence"] is long && (long)legacy["sequence"] == before + 1, "window row copies preserve legacy numeric ring sequence");
            var maxRing = new ManagedRing(long.MaxValue - 1);
            maxRing.Emit();
            var max = Valid(Read(adapter, maxRing, Query("cursor", adapter.Cursor(long.MaxValue - 1))), adapter);
            Check((string)max["next_sequence"] == "9223372036854775807", "cursor and snapshot retain Int64 maximum exactly");
            var codec = new MessageCursorCodec();
            Check(codec.Decode(codec.Encode(adapter.SessionId, long.MaxValue), adapter.SessionId) == long.MaxValue, "authenticated Int64 maximum roundtrip");
            maxRing.Emit();
            Error(Read(adapter, maxRing), adapter, "message_cursor_read_failed", 500);
            Error(Read(adapter, maxRing, Query("checkpoint", "true")), adapter, "message_cursor_read_failed", 500);
        }

        private static void Rejections()
        {
            var adapter = new E3d21HttpAdapter();
            var ring = new CountingRing();
            foreach (string value in new[] { "", "0", "501", "-1", "+1", " 1", "1 ", "1.5", "1e2", "999999999999" })
                Error(Read(adapter, ring, Query("limit", value)), adapter, "message_cursor_request_invalid", 400);
            foreach (string value in new[] { "", "True", "FALSE", "1", " true", "false " })
                Error(Read(adapter, ring, Query("checkpoint", value)), adapter, "message_cursor_request_invalid", 400);
            Error(Read(adapter, ring, Query("unknown", "1")), adapter, "message_cursor_request_invalid", 400);
            Error(Read(adapter, ring, Query("limit", "1", "limit", "2")), adapter, "message_cursor_request_invalid", 400);
            Error(Read(adapter, ring, Query("cursor", "", "cursor", "")), adapter, "message_cursor_request_invalid", 400);
            Error(Read(adapter, ring, Query("checkpoint", "true", "checkpoint", "true")), adapter, "message_cursor_request_invalid", 400);
            Error(Read(adapter, ring, Query("Limit", "1")), adapter, "message_cursor_request_invalid", 400);
            Error(Read(adapter, ring, Query("checkpoint", "true", "cursor", adapter.Cursor(0))), adapter, "message_cursor_request_invalid", 400);
            foreach (string cursor in new[] { " ", "invalid", ".", "x.y.z", new string('a', 1025), adapter.Cursor(0) + "=", adapter.Cursor(0).Replace('.', '/') })
                Error(Read(adapter, ring, Query("cursor", cursor)), adapter, "message_cursor_invalid", 400);
            string valid = adapter.Cursor(0);
            int dot = valid.IndexOf('.');
            string altered = valid.Substring(0, dot + 1) + (valid[dot + 1] == 'A' ? "B" : "A") + valid.Substring(dot + 2);
            Error(Read(adapter, ring, Query("cursor", altered)), adapter, "message_cursor_invalid", 400);
            var other = new E3d21HttpAdapter();
            // A cursor signed by this adapter but carrying another valid UUID is
            // authentic yet stale, and therefore receives session_changed.
            Error(Read(adapter, ring, Query("cursor", adapter.CursorForSession(other.SessionId, 0))), adapter, "message_cursor_session_changed", 409);
            // Reusing the valid payload with a fabricated session and its old
            // signature is unsigned tampering, so it must remain generic invalid.
            string fabricated = adapter.CursorForSession(other.SessionId, 0);
            int fabricatedDot = fabricated.IndexOf('.');
            string unsignedPayload = adapter.CursorForSession(adapter.SessionId, 0);
            Error(Read(adapter, ring, Query("cursor", fabricated.Substring(0, fabricatedDot) + unsignedPayload.Substring(unsignedPayload.IndexOf('.')))),
                adapter, "message_cursor_invalid", 400);
            var foreignKey = new MessageCursorCodec();
            Error(Read(adapter, ring, Query("cursor", foreignKey.Encode(adapter.SessionId, 0))), adapter, "message_cursor_invalid", 400);
            Check(ring.Calls == 0, "all invalid requests/cursors are rejected before ring access");
            Error(Read(adapter, ring, Query("cursor", adapter.Cursor(1))), adapter, "message_cursor_ahead", 409);
            Check(ring.Calls == 1, "future cursor is checked inside one atomic ring snapshot");
            Error(Read(adapter, new object()), adapter, "message_cursor_unavailable", 501);
            Error(Read(adapter, new FailingRing()), adapter, "message_cursor_read_failed", 500);
            Error(Read(adapter, new object(), method: "POST"), adapter, "message_cursor_request_invalid", 405);
        }

        private static void ConcurrentSnapshots()
        {
            var ring = new ManagedRing();
            Exception readerFailure = null;
            var writer = Task.Run(() => { for (int index = 0; index < 5000; index++) ring.Emit(); });
            for (int index = 0; index < 100; index++)
            {
                try
                {
                    MessageWindowSnapshot snapshot = ring.ReadMessageWindow(0, 137, false);
                    long previous = 0;
                    foreach (Dictionary<string, object> row in snapshot.Messages)
                    {
                        long sequence = long.Parse((string)row["sequence"], CultureInfo.InvariantCulture);
                        Check(sequence > previous && sequence <= snapshot.LatestSequence, "concurrent rows match sampled high water");
                        previous = sequence;
                    }
                    Check(snapshot.Messages.Count + snapshot.RemainingCount + snapshot.OverwrittenCount == snapshot.LatestSequence,
                        "concurrent snapshot metadata and retained selection are atomic");
                }
                catch (Exception error) { readerFailure = error; break; }
            }
            writer.GetAwaiter().GetResult();
            if (readerFailure != null) throw readerFailure;
        }

        private static int Main()
        {
            try
            {
                EmptyAndCheckpoint(); PagesAndLoss(); PrecisionAndLegacy(); Rejections(); ConcurrentSnapshots();
                Console.WriteLine("Message cursor production checks passed: " + checks);
                return 0;
            }
            catch (Exception error) { Console.Error.WriteLine(error); return 1; }
        }
    }
}
