using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace E3d21AiBridge
{
    internal sealed class TrackingStream : MemoryStream
    {
        internal int ReadCalls, BytesRead;
        private readonly int chunkSize;
        internal TrackingStream(byte[] data, int chunkSize) : base(data) { this.chunkSize = chunkSize; }
        public override Task<int> ReadAsync(byte[] buffer, int offset, int count, System.Threading.CancellationToken token)
        {
            ReadCalls++;
            int read = base.Read(buffer, offset, Math.Min(count, chunkSize));
            BytesRead += read;
            return Task.FromResult(read);
        }
    }
    internal sealed class RouteInnerRequest
    {
        internal NameValueCollection QueryString;
        internal TrackingStream InputStream;
        // Deliberately supplied by tests: the production UTF-8-only reader must
        // not use a declared alternate character set to decode source.
        internal Encoding ContentEncoding = Encoding.UTF8;
    }
    internal sealed class RouteResponse
    {
        internal int Status;
        internal object Value;
        internal bool Disconnected;
    }
    internal sealed class RouteRequest
    {
        internal RouteInnerRequest Request;
        internal readonly RouteResponse Response = new RouteResponse();
        internal RouteRequest(byte[] body, NameValueCollection query = null, int chunkSize = int.MaxValue)
        {
            Request = new RouteInnerRequest { QueryString = query ?? new NameValueCollection(),
                                             InputStream = new TrackingStream(body, chunkSize) };
        }
    }
    internal sealed class RouteContext
    {
        internal int Calls;
        internal string Source;
        internal bool DryRun;
        internal ManualResetEventSlim Entered, Release;
        internal bool Throw;
        public object ExecutePmlScript(string source, bool dryRun)
        {
            Calls++; Source = source; DryRun = dryRun;
            if (Entered != null) Entered.Set();
            if (Release != null && !Release.Wait(10000)) throw new TimeoutException("Test release timed out");
            if (Throw) throw new InvalidOperationException("Synthetic SDK exception");
            return new { success = true, dry_run = dryRun, execution_scope = "nested", source_echoed = false };
        }
    }
    internal static class ProductionRoute
    {
        private static readonly PmlScriptReceiptStore scriptReceipts = new PmlScriptReceiptStore();
        internal static async Task Invoke(RouteRequest request, RouteContext context)
        {
            object response = null;
            string sessionId = "SESSION";
            // PRODUCTION_ROUTE
            await WriteJsonAsync(request.Response, response, 200);
        }
        internal static async Task Status(RouteRequest request)
        {
            object response = null;
            string sessionId = "SESSION";
            // PRODUCTION_STATUS_ROUTE
            await WriteJsonAsync(request.Response, response, 200);
        }
        private static object SafeCommandStateDiagnostics() { return new { busy = false, database_ready = true }; }
        private static Task WriteJsonAsync(RouteResponse response, object value, int status)
        {
            if (response.Disconnected) throw new IOException("Synthetic disconnected HTTP client");
            response.Status = status; response.Value = value; return Task.CompletedTask;
        }
        // PRODUCTION_METHODS
    }
    internal static class PmlScriptHttpHarness
    {
        private static int checks;
        private static readonly List<string> failures = new List<string>();
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
        private static void Check(bool value, string label) { checks++; if (!value) failures.Add(label); }
        private static byte[] Bytes(string value) { return Utf8.GetBytes(value); }
        private static Exception Run(RouteRequest request, RouteContext context)
        {
            try { ProductionRoute.Invoke(request, context).GetAwaiter().GetResult(); return null; }
            catch (Exception error) { return error; }
        }
        private static void Accepted(string json, string source, bool dryRun, string label, int chunkSize = int.MaxValue)
        {
            var request = new RouteRequest(Bytes(json), chunkSize: chunkSize);
            var context = new RouteContext();
            Exception error = Run(request, context);
            Check(error == null, label + ": valid request did not throw" + (error == null ? "" : " (" + error.Message + ")"));
            Check(context.Calls == 1 && context.Source == source && context.DryRun == dryRun,
                label + ": exactly one call preserves source and preview flag");
            Check(request.Response.Status == 200, label + ": HTTP 200");
            Check(request.Response.Value != null, label + ": response envelope present");
            if (request.Response.Value != null)
            {
                JObject payload = JObject.FromObject(request.Response.Value);
                Check((bool)payload["ok"] && (string)payload["session_id"] == "SESSION" && (bool)payload["dry_run"] == dryRun,
                    label + ": envelope preserves session and preview");
                Check((string)payload["result"]["execution_scope"] == "nested", label + ": native result retained");
            }
        }
        private static void Rejected(byte[] bytes, string label, NameValueCollection query = null, Encoding claimedEncoding = null)
        {
            var request = new RouteRequest(bytes, query);
            if (claimedEncoding != null) request.Request.ContentEncoding = claimedEncoding;
            var context = new RouteContext();
            Exception error = Run(request, context);
            Check(error is ArgumentException || error is JsonReaderException,
                label + ": rejects malformed request with a validation error");
            Check(context.Calls == 0, label + ": zero ExecutePmlScript calls");
        }
        private static void Rejected(string json, string label)
        { Rejected(Bytes(json), label); }

        private static void DefaultsAndUnicode()
        {
            Accepted("{\"source\":\"$P OK\"}", "$P OK", true, "default preview");
            string source = "  -- 中文与 'quoted' content\r\n!n = 2\n$P END\r\n ";
            Accepted(JsonConvert.SerializeObject(new { source = source, dry_run = false, expected_session_id = "SESSION" }),
                source, false, "explicit execution preserves whitespace/Chinese/apostrophes", 1);
            Accepted("{\"source\":\"2026-11-16T12:34:56Z\"}", "2026-11-16T12:34:56Z", true, "date-like source stays string");
            Accepted("{\"source\":\"\\uD83D\\uDE00\"}", char.ConvertFromUtf32(0x1F600), true, "valid escaped surrogate pair");
            Accepted("{\"source\":\"😀\"}", char.ConvertFromUtf32(0x1F600), true, "valid raw UTF-8 scalar", 1);
            Accepted("{\"source\":\"\\ud83d\\ude00\"}", char.ConvertFromUtf32(0x1F600), true, "lowercase Unicode escapes");
            Accepted("{\"source\":\"\\\\uD800\"}", @"\uD800", true, "escaped backslash before Unicode-shaped literal");
            Accepted("{\"source\":\"\\\\\\\\uD800\"}", @"\\uD800", true, "two literal backslashes do not decode as surrogate");
            Accepted("{\"source\":\"\\uFFFD\"}", "\uFFFD", true, "explicit replacement character is valid source");
            Accepted("{\"sou\\u0072ce\":\"$P OK\",\"dry_run\":true}", "$P OK", true, "escaped known property accepted");
        }

        private static void BadUnicodeAndEncoding()
        {
            foreach (string encoded in new[] { @"\uD800", @"\uDBFF", @"\uDC00", @"\uDFFF", @"\uD800x",
                @"\uDC00\uD800", @"\uD800\uD800", @"\uD800\u0041", @"\uD800\\uDC00", @"\uD800\n\uDC00" })
                Rejected("{\"source\":\"" + encoded + "\"}", "unpaired/reordered surrogate " + encoded);
            Rejected("{\"sou\\uD800rce\":\"x\"}", "unpaired surrogate in JSON key");
            Rejected("{\"source\":\"x\",\"expected_session_id\":\"\\uDFFF\"}", "unpaired surrogate in session guard");
            foreach (byte[] bad in new[] {
                new byte[] { 0xC3, 0x28 }, new byte[] { 0xED, 0xA0, 0x80 }, new byte[] { 0xED, 0xB0, 0x80 },
                new byte[] { 0xF4, 0x90, 0x80, 0x80 }, new byte[] { 0xC0, 0xAF }, new byte[] { 0x80 },
                new byte[] { 0xE2, 0x82 }, new byte[] { 0xF0, 0x9F, 0x98 }
            })
            {
                byte[] body = Bytes("{\"source\":\"").Concat(bad).Concat(Bytes("\"}")).ToArray();
                Rejected(body, "invalid raw UTF-8 " + BitConverter.ToString(bad));
            }
            Rejected(Encoding.Unicode.GetBytes("{\"source\":\"中文\"}"), "UTF-16 body is not UTF-8", claimedEncoding: Encoding.Unicode);
            Rejected(Encoding.GetEncoding(936).GetBytes("{\"source\":\"中文\"}"), "legacy code-page body is not UTF-8", claimedEncoding: Encoding.GetEncoding(936));
        }

        private static void BadJsonAndParameters()
        {
            foreach (string body in new[] {
                "", "null", "true", "[]", "[{}]", "\"text\"", "{} {}", "{} false",
                "{\"source\":'x'}",
                "/* comment */ {\"source\":\"$P OK\"}", "{\"source\":\"$P OK\"}// trailing comment",
                "{\"source\":undefined}", "{\"source\":new Date(1)}", "{\"source\":NaN}",
                "{\"source\":\"\\v\"}", "{\"source\":\"\\x41\"}", "{\"source\":\"\\uXYZ1\"}",
                "{\"source\":\"\\u123\"}", "{\"source\":\"unterminated}", "{\"source\":\"raw\nline\"}",
                "{\"source\":null}", "{\"source\":false}", "{\"source\":12}", "{\"source\":[]}", "{\"source\":{}}",
                "{\"source\":\"x\",\"unknown\":1}", "{\"Source\":\"x\"}",
                "{\"source\":\"x\",\"dry_run\":null}", "{\"source\":\"x\",\"dry_run\":0}",
                "{\"source\":\"x\",\"dry_run\":\"false\"}", "{\"source\":\"x\",\"dry_run\":[]}",
                "{\"source\":\"x\",\"expected_session_id\":null}", "{\"source\":\"x\",\"expected_session_id\":2}"
            }) Rejected(body, "invalid JSON/parameter " + body);
            foreach (string body in new[] {
                "{\"source\":\"x\",\"source\":\"y\"}",
                "{\"source\":\"x\",\"unknown\":{\"key\":1,\"key\":2}}",
                "{\"source\":\"x\",\"sou\\u0072ce\":\"y\"}",
                "{\"sou\\u0072ce\":\"x\",\"source\":\"y\"}",
                "{\"source\":\"x\",\"dry_run\":true,\"dry_run\":false}",
                "{\"source\":\"x\",\"expected_session_id\":\"SESSION\",\"expected_session_\\u0069d\":\"OTHER\"}"
            }) Rejected(body, "duplicate decoded key " + body);
            var query = new NameValueCollection { { "dry_run", "false" } };
            Rejected(Bytes("{\"source\":\"$P OK\"}"), "query parameters forbidden", query);
        }

        private static void SessionGuard()
        {
            foreach (string expected in new[] { "OTHER", "", " SESSION", "SESSION " })
            {
                var request = new RouteRequest(Bytes(JsonConvert.SerializeObject(new {
                    source = "$P OK", dry_run = false, expected_session_id = expected })));
                var context = new RouteContext();
                Exception error = Run(request, context);
                Check(error == null && request.Response.Status == 409, "session mismatch returns HTTP 409");
                Check(context.Calls == 0, "session mismatch executes zero scripts");
                if (request.Response.Value != null)
                {
                    JObject payload = JObject.FromObject(request.Response.Value);
                    Check((bool)payload["ok"] == false && (bool)payload["execution_attempted"] == false &&
                        (bool)payload["outcome_uncertain"] == false && (string)payload["session_id"] == "SESSION",
                        "session rejection has explicit known non-execution");
                }
            }
        }

        private static void BoundedTransport()
        {
            const string body = "{\"source\":\"$P OK\"}";
            string exact = body + new string(' ', PmlScriptJson.MaximumBodyBytes - Bytes(body).Length);
            Accepted(exact, "$P OK", true, "exact 2 MiB body accepted");
            byte[] tooLarge = Bytes(exact + new string(' ', 65536));
            var request = new RouteRequest(tooLarge);
            var context = new RouteContext();
            Exception error = Run(request, context);
            Check(error != null && context.Calls == 0, "oversized body rejected before Execute");
            Check(request.Request.InputStream.BytesRead <= PmlScriptJson.MaximumBodyBytes + 8192 &&
                  request.Request.InputStream.BytesRead < tooLarge.Length, "oversized stream stops at first over-budget block");
            bool parseRejected = false;
            try { PmlScriptJson.Parse(tooLarge); } catch (ArgumentException) { parseRejected = true; }
            Check(parseRejected, "standalone parser independently caps body bytes");
            parseRejected = false;
            try { PmlScriptJson.Parse(null); } catch (ArgumentException) { parseRejected = true; }
            Check(parseRejected, "standalone parser rejects null bytes");
        }

        private static RouteRequest Submission(string id, string source = "$P TRACKED", bool dryRun = false)
        { return new RouteRequest(Bytes(JsonConvert.SerializeObject(new { source = source, dry_run = dryRun, request_id = id, expected_session_id = "SESSION" }))); }

        private static RouteRequest Lookup(string id, string session = "SESSION")
        {
            var request = new RouteRequest(new byte[0], new NameValueCollection { { "request_id", id }, { "expected_session_id", session } });
            ProductionRoute.Status(request).GetAwaiter().GetResult();
            return request;
        }

        private static JObject LookupResult(string id)
        { return (JObject)JObject.FromObject(Lookup(id).Response.Value)["result"]; }

        private static void TrackedExecution()
        {
            string id = Guid.NewGuid().ToString("D");
            var context = new RouteContext { Entered = new ManualResetEventSlim(), Release = new ManualResetEventSlim() };
            var original = Submission(id);
            Task worker = Task.Run(() => ProductionRoute.Invoke(original, context));
            try
            {
                Check(context.Entered.Wait(5000), "tracked callback entered");
                JObject pending = LookupResult(id.ToUpperInvariant());
                Check((bool)pending["available"] && (string)pending["status"] == "pending", "status available while callback is blocked");
                Check(context.Calls == 1 && !worker.IsCompleted, "status does not execute or wait for SDK");
                var duplicate = Submission(id.ToUpperInvariant(), "$P CHANGED_SOURCE");
                Check(Run(duplicate, context) == null && duplicate.Response.Status == 409 && context.Calls == 1,
                    "pending duplicate with changed source and case is rejected without re-execution");
                Check(!(bool)JObject.FromObject(duplicate.Response.Value)["this_submission_executed"], "duplicate labels this submission only");
            }
            finally { context.Release.Set(); }
            worker.GetAwaiter().GetResult();
            JObject complete = LookupResult(id);
            Check((string)complete["status"] == "completed" && (bool)complete["receipt_available"], "completed receipt query");
            Check(JToken.DeepEquals(complete["receipt"], JObject.FromObject(original.Response.Value)), "stored receipt equals original envelope");
            var again = Submission(id);
            Check(Run(again, context) == null && again.Response.Status == 409 && context.Calls == 1, "completed duplicate not replayed");
            Check(Lookup(id, "PREVIOUS_SESSION").Response.Status == 409, "status rejects previous session before lookup");
            JObject missing = LookupResult(Guid.NewGuid().ToString("D"));
            Check(!(bool)missing["available"] && (string)missing["status"] == "unknown_or_expired", "unknown is not a fabricated success");

            string previewId = Guid.NewGuid().ToString("D");
            var previewContext = new RouteContext();
            Check(Run(Submission(previewId, dryRun: true), previewContext) == null, "preview succeeds with valid optional id");
            Check(!(bool)LookupResult(previewId)["available"], "preview does not create execution history");
            Check(Run(Submission(previewId), previewContext) == null && previewContext.Calls == 2,
                "preview does not reserve id against future real execution");

            string disconnectedId = Guid.NewGuid().ToString("D");
            var disconnected = Submission(disconnectedId);
            disconnected.Response.Disconnected = true;
            var disconnectedContext = new RouteContext();
            Check(Run(disconnected, disconnectedContext) is IOException, "HTTP client disconnect is surfaced");
            JObject recovered = LookupResult(disconnectedId);
            Check((string)recovered["status"] == "completed" && (bool)recovered["receipt"]["result"]["success"] && disconnectedContext.Calls == 1,
                "receipt survives lost HTTP response without replay");

            string failedId = Guid.NewGuid().ToString("D");
            var failed = Submission(failedId);
            Check(Run(failed, new RouteContext { Throw = true }) == null && failed.Response.Status == 500, "native exception stored as HTTP 500");
            JObject error = LookupResult(failedId);
            Check((string)error["status"] == "completed" && (int)error["http_status"] == 500 &&
                (bool)error["receipt"]["outcome_uncertain"], "completed record does not invent a known execution outcome");
        }

        private static void StatusValidation()
        {
            string id = Guid.NewGuid().ToString("D");
            var cases = new[] {
                new NameValueCollection(),
                new NameValueCollection { { "request_id", id } },
                new NameValueCollection { { "expected_session_id", "SESSION" } },
                new NameValueCollection { { "request_id", "bad" }, { "expected_session_id", "SESSION" } },
                new NameValueCollection { { "request_id", id }, { "expected_session_id", " " } },
                new NameValueCollection { { "request_id", id }, { "request_id", id }, { "expected_session_id", "SESSION" } },
                new NameValueCollection { { "request_id", id }, { "expected_session_id", "SESSION" }, { "unexpected", "x" } },
                new NameValueCollection { { "Request_Id", id }, { "expected_session_id", "SESSION" } },
            };
            foreach (NameValueCollection query in cases)
            {
                var request = new RouteRequest(new byte[0], query);
                ProductionRoute.Status(request).GetAwaiter().GetResult();
                Check(request.Response.Status == 400 && !(bool)JObject.FromObject(request.Response.Value)["ok"],
                    "invalid status query rejected without native diagnostics");
            }
            foreach (object invalid in new object[] { null, "", "bad", id.Replace("-", ""), " " + id, 1, false })
                Rejected(JsonConvert.SerializeObject(new { source = "$P OK", request_id = invalid }), "invalid submitted request id");
        }

        internal static int Main()
        {
            DefaultsAndUnicode(); BadUnicodeAndEncoding(); BadJsonAndParameters(); SessionGuard(); BoundedTransport();
            TrackedExecution(); StatusValidation();
            if (failures.Count != 0)
            {
                Console.Error.WriteLine("PML script HTTP contract failed: " + failures.Count + " / " + checks);
                foreach (string failure in failures) Console.Error.WriteLine(failure);
                return 1;
            }
            Console.WriteLine("PML script HTTP production checks passed: " + checks);
            return 0;
        }
    }
}
