using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using E3d21AiBridge;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

// Only the network carrier and optional native implementation are fakes.
// The handler, capability predicates, JSON reader, request validator, serializer
// and complete fallback/error chain are extracted production code.
internal sealed class ReferenceTraceCountingStream : MemoryStream
{
    internal int BytesRead;
    internal ReferenceTraceCountingStream(byte[] bytes) : base(bytes) { }
    public override Task<int> ReadAsync(byte[] buffer, int offset, int count,
        System.Threading.CancellationToken cancellationToken)
    {
        int read = Read(buffer, offset, count);
        BytesRead += read;
        return Task.FromResult(read);
    }
}

internal sealed class ReferenceTraceHttpInnerRequest
{
    internal NameValueCollection QueryString;
    internal ReferenceTraceCountingStream InputStream;
    internal Encoding ContentEncoding = Encoding.UTF8;
    internal string ContentType = "application/json";
    internal string HttpMethod;
    internal Uri Url;
    internal bool HasEntityBody;
    internal long ContentLength64;
    internal readonly NameValueCollection Headers = new NameValueCollection();
}

internal sealed class ReferenceTraceHttpResponse
{
    internal int StatusCode;
    internal string ContentType;
    internal long ContentLength64;
    internal readonly MemoryStream OutputStream = new MemoryStream();
    internal readonly NameValueCollection Headers = new NameValueCollection();
    internal int CloseCalls;
    internal void AddHeader(string name, string value) { Headers.Add(name, value); }
    internal void Close() { CloseCalls++; }
    internal JObject Json { get { return JObject.Parse(Encoding.UTF8.GetString(OutputStream.ToArray())); } }
}

internal sealed class ReferenceTraceHttpRequest
{
    internal readonly ReferenceTraceHttpInnerRequest Request;
    internal readonly ReferenceTraceHttpResponse Response = new ReferenceTraceHttpResponse();
    internal ReferenceTraceHttpRequest(string body = "", string method = "POST", NameValueCollection query = null,
        string path = "/api/v1/paragon/reference-trace")
    {
        byte[] bytes = Encoding.UTF8.GetBytes(body);
        Request = new ReferenceTraceHttpInnerRequest
        {
            InputStream = new ReferenceTraceCountingStream(bytes), QueryString = query ?? new NameValueCollection(),
            HttpMethod = method, Url = new Uri("http://offline.invalid" + path),
            HasEntityBody = bytes.Length != 0, ContentLength64 = bytes.Length
        };
    }
}

internal class ReferenceTraceBaseContext
{
    internal int NativeCalls, CommandStateCalls, ConnectedCalls;
    internal int TraceLimit;
    internal string Refno, SessionId;
    internal Exception Failure;
    internal object Result = new { operation = "paragon_reference_trace", status = "observed",
        complete = true, marker = "native-reference-trace" };
    internal object CommandState() { CommandStateCalls++; return new { database_ready = true }; }
    internal bool Connected { get { ConnectedCalls++; return true; } }
    public object ReadParagonReferenceTrace(string refno, int traceLimit, string sessionId)
    {
        NativeCalls++;
        Refno = refno; TraceLimit = traceLimit; SessionId = sessionId;
        if (Failure != null) throw Failure;
        return Result;
    }
}

internal sealed class ReferenceTraceOnlyContext : ReferenceTraceBaseContext, IE3d21ParagonReferenceTraceContext { }

namespace E3d21AiBridge
{
    internal sealed class E3d21HttpAdapter
    {
        internal const string SessionId = "paragon-reference-trace-http-session";
        private const int MaximumJsonRequestBodyBytes = 1024 * 1024;
        private readonly string sessionId = SessionId;
        private readonly ReferenceTraceBaseContext context;
        internal int FallbackDiagnosticsCalls;
        internal E3d21HttpAdapter(ReferenceTraceBaseContext context) { this.context = context; }
        internal Task Invoke(ReferenceTraceHttpRequest request) { return HandleAsync(request); }
        private object SafeCommandStateDiagnostics()
        {
            FallbackDiagnosticsCalls++;
            return new { forbidden_native_diagnostics = true };
        }

        // PRODUCTION_HANDLER

        // PRODUCTION_HTTP_HELPERS
    }
}

internal static class ParagonReferenceTraceHttpHarness
{
    private const string Route = "/api/v1/paragon/reference-trace";
    private const string Feature = "paragon_reference_trace";
    private const string Valid = "{\"refno\":\"=7/101\"}";
    private static int checks;
    private static readonly List<string> failures = new List<string>();
    private static string Body(object value) { return JsonConvert.SerializeObject(value); }
    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) failures.Add(message);
    }

    private static ReferenceTraceHttpResponse Run(ReferenceTraceHttpRequest request, ReferenceTraceBaseContext context, bool health = false)
    {
        var adapter = new E3d21HttpAdapter(context);
        adapter.Invoke(request).GetAwaiter().GetResult();
        Check(adapter.FallbackDiagnosticsCalls == 0, "reference trace path must not invoke fallback command diagnostics");
        Check(context.CommandStateCalls == (health ? 1 : 0) && context.ConnectedCalls == (health ? 1 : 0),
            "only /health explicitly observes command/connection state, never reference trace failure recovery");
        Check(request.Response.CloseCalls == 1, "HTTP response closes exactly once");
        Check(request.Response.ContentType == "application/json; charset=utf-8" &&
            request.Response.ContentLength64 == request.Response.OutputStream.Length,
            "HTTP headers match the production JSON wire body");
        JObject json = request.Response.Json;
        string location = request.Request.HttpMethod + " " + request.Request.Url.AbsolutePath + " HTTP " + request.Response.StatusCode;
        Check((string)json["version"] == "E3D21" && (string)json["session_id"] == E3d21HttpAdapter.SessionId,
            location + ": version and session bind every response");
        Check((string)json["bridge_build"] == typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
            location + ": build is the executing assembly MVID");
        Check((bool?)json["ok"] == (request.Response.StatusCode == 200), location + ": envelope ok matches HTTP result");
        if (!health)
            Check(json["state"] == null && json["diagnostic_error"] == null, "reference trace replies do not include unrequested diagnostics");
        return request.Response;
    }

    private static ReferenceTraceOnlyContext Accept(string body, string reference = "=7/101", int traceLimit = 24,
        bool chunked = false)
    {
        var context = new ReferenceTraceOnlyContext();
        var request = new ReferenceTraceHttpRequest(body);
        if (chunked) { request.Request.ContentLength64 = -1; request.Request.Headers["Transfer-Encoding"] = "chunked"; }
        var response = Run(request, context);
        Check(response.StatusCode == 200 && context.NativeCalls == 1, "valid request invokes native reader once with HTTP 200");
        Check(context.Refno == reference, "exact canonical numeric reference forwarded without rewriting");
        Check(context.TraceLimit == traceLimit, "exact default or explicit trace_limit forwarded as an integer");
        Check(context.SessionId == E3d21HttpAdapter.SessionId, "native reader receives this adapter session");
        Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(context.Result)), "native result retained without reinterpretation");
        Check(response.Json["result"]["available"] == null && response.Json["result"]["success"] == null,
            "adapter does not invent native available or success fields");
        return context;
    }

    private static void Reject(string body, string label, NameValueCollection query = null)
    {
        var context = new ReferenceTraceOnlyContext();
        var response = Run(new ReferenceTraceHttpRequest(body, query: query), context);
        Check(response.StatusCode == 400 && context.NativeCalls == 0, label + ": reject before every native read");
    }

    private static void ValidRequests()
    {
        Accept(Valid);
        foreach (string reference in new[] { "=1/0", "=1/1", "=2147483647/2147483647" })
            Accept(Body(new { refno = reference }), reference);
        Accept("{\"refn\\u006f\":\"=7/101\"}");
        foreach (int traceLimit in new[] { 1, 24, 32 })
            Accept(Body(new { refno = "=7/101", trace_limit = traceLimit }), traceLimit: traceLimit);
        Accept("{\"trace_limit\":1,\"refno\":\"=1/0\"}", "=1/0", 1);
        Accept("{\"refno\":\"=7/101\",\"trace_\\u006cimit\":32}", traceLimit: 32);
    }

    private static void InvalidJsonAndKeys()
    {
        foreach (string body in new[] { "", " ", "null", "true", "false", "0", "123", "\"text\"", "[]", "[{}]", "{", "{} {}", Valid + " false", "{}" })
            Reject(body, "malformed, nonobject or missing refno");
        foreach (string name in new[] { "Refno", "Trace_Limit", "TraceLimit", "session_id", "bridge_build", "version", "expected_type", "database_type", "attributes", "limit", "offset",
            "recipe", "params", "source", "command", "dry_run", "execute", "request_id", "expected_revision" })
        {
            var body = JObject.Parse(Valid); body[name] = true;
            Reject(body.ToString(Formatting.None), "unknown exact-case request key " + name);
        }
        Reject("{\"refno\":\"=7/101\",\"refno\":\"=7/102\"}", "duplicate refno");
        Reject("{\"refno\":\"=7/101\",\"refn\\u006f\":\"=7/102\"}", "duplicate decoded refno");
        Reject("{\"refno\":\"=7/101\",\"trace_limit\":24,\"trace_limit\":1}", "duplicate trace_limit");
        Reject("{\"refno\":\"=7/101\",\"trace_limit\":24,\"trace_\\u006cimit\":1}", "duplicate decoded trace_limit");
        foreach (string name in new[] { "refno", "trace_limit", "unexpected", null })
        {
            var request = new ReferenceTraceHttpRequest("not JSON", query: new NameValueCollection { { name, "x" } });
            var context = new ReferenceTraceOnlyContext();
            var response = Run(request, context);
            Check(response.StatusCode == 400 && context.NativeCalls == 0 && request.Request.InputStream.BytesRead == 0,
                "query rejected before body parse or SDK read");
        }
    }

    private static void InvalidTypesAndBounds()
    {
        foreach (string value in new[] { "null", "true", "false", "1", "1.0", "[]", "{}" })
            Reject("{\"refno\":" + value + "}", "refno is an actual string");
        foreach (string value in new[] { "null", "true", "false", "1.0", "24.0", "1e0", "\"24\"", "[]", "{}", "0", "-1", "33",
            "2147483648", "-2147483649", "99999999999999999999999999999999999" })
            Reject("{\"refno\":\"=7/101\",\"trace_limit\":" + value + "}", "trace_limit is an actual bounded integer 1..32");
        foreach (string reference in new[] { "", "CE", "WORLD", "/", "/SCOM", "=0/0", "=0/1", "=-1/1", "=1/-1", "=+1/1", "=1/+1", "=01/1", "=1/00", "=00/1",
            " =1/1", "=1/1 ", "=1/1\n", "=1/1\r\n", "=1/1\0", "=1/1/1", "=1/*", "=1/?", "=1/1;SAVEWORK", "=1/１",
            "=2147483648/1", "=1/2147483648", "=99999999999999999999/1" })
            Reject(Body(new { refno = reference }), "exact canonical Int32 numeric ref; no trim, names, CE or leading zeros");
    }

    private static void ByteBudget()
    {
        const int budget = 4096;
        string exact = Valid + new string(' ', budget - Encoding.UTF8.GetByteCount(Valid));
        Accept(exact);
        Accept(exact, chunked: true);
        string bom = "\ufeff" + Valid;
        Accept(bom + new string(' ', budget - Encoding.UTF8.GetByteCount(bom)));
        Accept(bom + new string(' ', budget - Encoding.UTF8.GetByteCount(bom)), chunked: true);
        Reject(exact + " ", "4097 raw bytes is over budget");
        Reject(bom + new string(' ', budget - Encoding.UTF8.GetByteCount(bom) + 1), "raw budget includes all UTF8 BOM bytes");
        foreach (bool chunked in new[] { false, true })
        {
            var request = new ReferenceTraceHttpRequest(exact + new string(' ', 65536));
            if (chunked) { request.Request.ContentLength64 = -1; request.Request.Headers["Transfer-Encoding"] = "chunked"; }
            var context = new ReferenceTraceOnlyContext();
            var response = Run(request, context);
            Check(response.StatusCode == 400 && context.NativeCalls == 0, "oversized ordinary/chunked body rejected before SDK");
            Check(request.Request.InputStream.BytesRead <= budget + 1 && request.Request.InputStream.BytesRead < request.Request.InputStream.Length,
                "reader stops at first over-budget byte");
        }
    }

    private static void MethodsAndOptionalCapability()
    {
        var absent = new ReferenceTraceBaseContext();
        var request = new ReferenceTraceHttpRequest("not JSON");
        var response = Run(request, absent);
        Check(response.StatusCode == 404 && absent.NativeCalls == 0 && request.Request.InputStream.BytesRead == 0,
            "unavailable capability is HTTP 404 without reading body or SDK");
        foreach (string method in new[] { "GET", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS" })
            foreach (ReferenceTraceBaseContext context in new ReferenceTraceBaseContext[] {
                new ReferenceTraceBaseContext(), new ReferenceTraceOnlyContext { Failure = new Exception("must not call") } })
            {
                request = new ReferenceTraceHttpRequest("not JSON", method);
                response = Run(request, context);
                Check(response.StatusCode == 405 && response.Headers["Allow"] == "POST", "wrong method is 405 Allow POST regardless of optional capability");
                Check(context.NativeCalls == 0 && request.Request.InputStream.BytesRead == 0, "wrong method performs no parse or SDK work");
            }
        foreach (ReferenceTraceBaseContext context in new ReferenceTraceBaseContext[] { new ReferenceTraceBaseContext(), new ReferenceTraceOnlyContext() })
        {
            request = new ReferenceTraceHttpRequest(method: "GET", path: "/health");
            response = Run(request, context, health: true);
            var features = ((JArray)response.Json["api_features"]).Values<string>().ToList();
            Check(features.Contains(Feature) == (context is IE3d21ParagonReferenceTraceContext), "health advertises only an implemented optional reference trace capability");
            Check(features.Count == features.Distinct(StringComparer.Ordinal).Count(), "health features contain no duplicates");
            Check(context.NativeCalls == 0 && request.Request.InputStream.BytesRead == 0, "health discovery never probes the reference trace SDK reader");
        }
    }

    private static bool WellFormedUtf16(string value)
    {
        for (int index = 0; index < value.Length; index++)
        {
            if (char.IsHighSurrogate(value[index]))
            {
                if (++index >= value.Length || !char.IsLowSurrogate(value[index])) return false;
            }
            else if (char.IsLowSurrogate(value[index])) return false;
        }
        return true;
    }

    private static void PartialAndFailureResults()
    {
        ReferenceTraceOnlyContext context;
        ReferenceTraceHttpResponse response;
        foreach (string status in new[] { "observed", "partial", "busy", "context_unverified" })
        {
            context = new ReferenceTraceOnlyContext { Result = new
            {
                operation = "paragon_reference_trace", status = status, complete = false, atomic_snapshot = false, read_only = true,
                requested = new { refno = "=7/101", trace_limit = 24 },
                read_errors = new[] { new { stage = "reference", refno = (string)null, message = "SDK stopped" } },
                context = new { before = new { session_id = E3d21HttpAdapter.SessionId }, after = (object)null, unchanged = (bool?)null },
                rows = new[] { new { id = 0, name = (string)null, complete = false, future_unknown = true } },
                future_evidence = "retain exact native opaque result"
            } };
            response = Run(new ReferenceTraceHttpRequest(Valid), context);
            Check(response.StatusCode == 200 && context.NativeCalls == 1 && (bool?)response.Json["ok"] == true,
                status + ": native status retains HTTP success envelope without claiming native completeness");
            Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(context.Result)),
                status + ": opaque null/read_errors/unknown fields survive serialization unchanged");
            Check((string)response.Json["result"]["status"] == status && response.Json["result"]["available"] == null &&
                response.Json["result"]["success"] == null, status + ": HTTP does not add native status or availability claims");
        }
        var failuresToRun = new List<Exception> {
            new ArgumentException("native argument invalid"), new JsonReaderException("native JSON invalid"),
            new InvalidOperationException("native read failed"), new InvalidOperationException(new string('x', 10000)),
            new NativeDatabaseFaultException("native fault sentinel"), new NativeDatabaseFaultException(new string('x', 10000)) };
        foreach (int boundary in new[] { 4092, 4093, 4095, 4096 })
        {
            string message = new string('x', boundary) + char.ConvertFromUtf32(0x1F600) + new string('y', 8000);
            failuresToRun.Add(new InvalidOperationException(message));
            failuresToRun.Add(new NativeDatabaseFaultException(message));
        }
        foreach (Exception failure in failuresToRun)
        {
            context = new ReferenceTraceOnlyContext { Failure = failure };
            response = Run(new ReferenceTraceHttpRequest(Valid), context);
            bool fault = failure is NativeDatabaseFaultException;
            int status = failure is ArgumentException || failure is JsonException ? 400 : 500;
            Check(response.StatusCode == status && context.NativeCalls == 1 && response.Json["result"] == null,
                "native error status is retained with one SDK call and no fabricated result");
            string error = (string)response.Json["error"];
            Check(!string.IsNullOrEmpty(error) && error.Length <= 4096 && WellFormedUtf16(error) && error.IndexOf('\uFFFD') < 0,
                "ordinary/native fault error is bounded without cutting or replacing a surrogate pair");
            if (fault)
            {
                Check((string)response.Json["error_code"] == "native_database_fault" &&
                    (bool?)response.Json["native_fault_latched"] == true && (bool?)response.Json["retry_safe"] == false,
                    "dedicated native-fault path retains code, latch and unsafe retry state");
                if (failure.Message == "native fault sentinel")
                    Check(error == failure.Message, "short native fault message retained verbatim");
            }
            else
                Check(response.Json["error_code"] == null && response.Json["native_fault_latched"] == null,
                    "ordinary SDK exception does not fabricate native fault flags");
            if (failure is JsonReaderException)
                Check(error == "Request body is not valid JSON.", "JSON parse exception never echoes native body text");
            else if (failure.Message.Length <= 4096)
                Check(error == failure.Message, "short ordinary/native fault error message survives unchanged");
        }
    }

    private static int Main()
    {
        try
        {
            ValidRequests();
            InvalidJsonAndKeys();
            InvalidTypesAndBounds();
            ByteBudget();
            MethodsAndOptionalCapability();
            PartialAndFailureResults();
            if (failures.Count != 0)
            {
                foreach (string failure in failures) Console.Error.WriteLine(failure);
                return 1;
            }
            Console.WriteLine("PARAGON reference trace HTTP production checks passed: " + checks);
            return 0;
        }
        catch (Exception failure)
        {
            Console.Error.WriteLine(failure.GetType().FullName + ": " + failure.Message);
            return 1;
        }
    }
}
