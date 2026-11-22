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

// Only transport carriers and the optional native capability are faked here.
// Every response is serialized by the production WriteJsonAsync method.
internal sealed class ParagonCountingStream : MemoryStream
{
    internal int BytesRead;
    internal ParagonCountingStream(byte[] bytes) : base(bytes) { }
    public override Task<int> ReadAsync(byte[] buffer, int offset, int count, System.Threading.CancellationToken cancellationToken)
    {
        int result = Read(buffer, offset, count);
        BytesRead += result;
        return Task.FromResult(result);
    }
}

internal sealed class ParagonHttpInnerRequest
{
    internal NameValueCollection QueryString;
    internal ParagonCountingStream InputStream;
    internal Encoding ContentEncoding = Encoding.UTF8;
    internal string ContentType = "application/json";
    internal string HttpMethod;
    internal Uri Url;
}

internal sealed class ParagonHttpResponse
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

internal sealed class ParagonHttpRequest
{
    internal readonly ParagonHttpInnerRequest Request;
    internal readonly ParagonHttpResponse Response = new ParagonHttpResponse();
    internal ParagonHttpRequest(string json, string method = "POST", NameValueCollection query = null,
        string path = "/api/v1/paragon/catalogue-manifest")
    {
        Request = new ParagonHttpInnerRequest
        {
            InputStream = new ParagonCountingStream(Encoding.UTF8.GetBytes(json)),
            QueryString = query ?? new NameValueCollection(), HttpMethod = method,
            Url = new Uri("http://offline.invalid" + path)
        };
    }
}

internal sealed class ParagonRecordingContext : IE3d21ParagonCatalogueManifestContext
{
    internal int Calls, MaxDepth, Limit;
    internal string Refno, SessionId;
    internal List<string> Attributes;
    internal Exception Failure;
    internal object Result = new { available = true, success = true, complete = true, marker = "native-manifest" };
    public object ReadParagonCatalogueManifest(string refno, int maxDepth, int limit, IList<string> attributes, string sessionId)
    {
        Calls++;
        Refno = refno; MaxDepth = maxDepth; Limit = limit; SessionId = sessionId;
        Attributes = attributes == null ? null : attributes.ToList();
        if (Failure != null) throw Failure;
        return Result;
    }
}

namespace E3d21AiBridge
{
    // The selected PARAGON route never enters the connectivity catch. Keep its
    // shared-router dependency local without loading connectivity/native code.
    internal static class ConnectivitySnapshotErrors
    {
        internal static string Text(string value)
        {
            if (value == null || value.Length <= 4096) return value;
            int length = 4093;
            if (char.IsHighSurrogate(value[length - 1])) length--;
            return value.Substring(0, length) + "...";
        }
    }

    internal sealed class E3d21HttpAdapter
    {
        internal const string SessionId = "paragon-http-session";
        private const int MaximumJsonRequestBodyBytes = 1024 * 1024;
        private readonly string sessionId = SessionId;
        private readonly object context;
        internal int DiagnosticsCalls;
        internal E3d21HttpAdapter(object context) { this.context = context; }
        internal Task Invoke(ParagonHttpRequest request) { return HandleAsync(request); }
        private object SafeCommandStateDiagnostics()
        {
            DiagnosticsCalls++;
            return new { forbidden_native_diagnostics = true };
        }

        // PRODUCTION_HANDLER

        // PRODUCTION_HTTP_HELPERS
    }
}

internal static class ParagonCatalogueHttpHarness
{
    private const string Valid = "{\"refno\":\"/CATALOGUE\"}";
    private static int checks;
    private static readonly List<string> failures = new List<string>();
    private static void Check(bool value, string label)
    {
        checks++;
        if (!value) failures.Add(label);
    }

    private static ParagonHttpResponse Run(ParagonHttpRequest request, object context)
    {
        var adapter = new E3d21HttpAdapter(context);
        adapter.Invoke(request).GetAwaiter().GetResult();
        Check(adapter.DiagnosticsCalls == 0, "PARAGON route called native command diagnostics");
        Check(request.Response.CloseCalls == 1, "response is closed exactly once");
        Check(request.Response.ContentType == "application/json; charset=utf-8"
            && request.Response.ContentLength64 == request.Response.OutputStream.Length, "JSON wire metadata matches serialized response");
        Check((string)request.Response.Json["version"] == "E3D21"
            && (string)request.Response.Json["session_id"] == E3d21HttpAdapter.SessionId,
            "every PARAGON response binds the route version and session");
        return request.Response;
    }

    private static ParagonHttpResponse Reject(string json, string label, NameValueCollection query = null)
    {
        var context = new ParagonRecordingContext();
        var response = Run(new ParagonHttpRequest(json, query: query), context);
        Check(response.StatusCode == 400 && (bool?)response.Json["ok"] == false, label + ": HTTP 400");
        Check(context.Calls == 0, label + ": zero native calls");
        return response;
    }

    private static ParagonRecordingContext Accept(string json, string reference, int depth, int limit, string[] attributes)
    {
        var context = new ParagonRecordingContext();
        var response = Run(new ParagonHttpRequest(json), context);
        var envelope = response.Json;
        Check(response.StatusCode == 200 && (bool?)envelope["ok"] == true, "valid request HTTP 200");
        Check(context.Calls == 1 && context.Refno == reference && context.MaxDepth == depth && context.Limit == limit
            && context.Attributes != null && context.Attributes.SequenceEqual(attributes), "validated arguments forwarded exactly once");
        Check(context.SessionId == E3d21HttpAdapter.SessionId && (string)envelope["session_id"] == context.SessionId
            && (string)envelope["version"] == "E3D21"
            && (string)envelope["bridge_build"] == typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
            "HTTP envelope binds the exact native session and assembly build");
        Check(JToken.DeepEquals(envelope["result"], JToken.FromObject(context.Result)), "native result retained exactly");
        return context;
    }

    private static string Body(object value) { return JsonConvert.SerializeObject(value); }

    private static void ValidRequests()
    {
        Accept(Valid, "/CATALOGUE", 8, 64, new string[0]);
        Accept("{\"refno\":\" /MixedCase \"}", "/MixedCase", 8, 64, new string[0]);
        Accept("{\"refno\":\"=0002/0000\",\"max_depth\":1,\"limit\":1,\"attributes\":[]}",
            "=2/0", 1, 1, new string[0]);
        Accept(Body(new { refno = "=2147483647/2147483647", max_depth = 16, limit = 100 }),
            "=2147483647/2147483647", 16, 100, new string[0]);
        Accept(Body(new { refno = "/CATALOGUE", max_depth = 16, limit = 100,
            attributes = new[] { " name ", "Desc", ":uda", "PSPE", "ABOR" } }), "/CATALOGUE", 16, 100,
            new[] { "NAME", "DESC", ":UDA", "PSPE", "ABOR" });
        string[] ten = Enumerable.Range(0, 10).Select(index => "A" + index).ToArray();
        Accept(Body(new { refno = "/CATALOGUE", max_depth = 8, limit = 50, attributes = ten }),
            "/CATALOGUE", 8, 50, ten);
        Accept(Body(new { refno = "/CATALOGUE", limit = 1, attributes = new[] { new string('A', 64) } }),
            "/CATALOGUE", 8, 1, new[] { new string('A', 64) });
    }

    private static void InvalidJsonAndParameters()
    {
        foreach (string json in new[] { "", " ", "null", "true", "123", "\"text\"", "[]", "[{}]", "{", "{} {}",
            "{\"refno\":\"/CATALOGUE\"} false", "{\"refno\":\"/CATALOGUE\",\"offset\":0}",
            "{\"refno\":\"/CATALOGUE\",\"dry_run\":true}", "{\"Refno\":\"/CATALOGUE\"}",
            "{\"refno\":\"/CATALOGUE\",\"request_id\":\"x\"}" })
            Reject(json, "nonobject JSON, malformed body or unknown parameter");
        foreach (string duplicate in new[] { "\"refno\":\"/OTHER\"", "\"refn\\u006f\":\"/OTHER\"",
            "\"max_depth\":1,\"max_depth\":2", "\"limit\":1,\"limit\":2",
            "\"attributes\":[],\"attributes\":[]" })
            Reject("{\"refno\":\"/CATALOGUE\"," + duplicate + "}", "duplicate decoded key");
        foreach (string key in new[] { "refno", "limit", "unexpected", null })
            Reject(Valid, "query parameters forbidden", new NameValueCollection { { key, "1" } });
    }

    private static void InvalidTypesAndBounds()
    {
        foreach (string value in new[] { "null", "true", "1", "[]", "{}" })
            Reject("{\"refno\":" + value + "}", "refno type");
        Reject("{}", "missing explicit root");
        foreach (string value in new[] { "", "CE", "ce", "WORLD", "*", "/", "/CAT*", "/CAT?", "/A B", "/A\nB",
            "=0/1", "=-1/1", "=1/-1", "=1/+1", "=1/1/1", "=2147483648/1", "=1/2147483648", "/" + new string('A', 256) })
            Reject(Body(new { refno = value }), "invalid literal root");
        foreach (string field in new[] { "max_depth", "limit" })
        {
            foreach (string value in new[] { "null", "true", "false", "\"1\"", "1.0", "[]", "{}", "NaN", "Infinity",
                "2147483648", "-2147483649", "9999999999999999999999999999999999999999999999999" })
                Reject("{\"refno\":\"/CATALOGUE\",\"" + field + "\":" + value + "}", field + " type/range");
        }
        foreach (int value in new[] { -1, 0, 17, int.MaxValue })
            Reject(Body(new { refno = "/CATALOGUE", max_depth = value }), "max depth bound");
        foreach (int value in new[] { -1, 0, 101, int.MaxValue })
            Reject(Body(new { refno = "/CATALOGUE", limit = value }), "root-inclusive row limit bound");
        foreach (string value in new[] { "null", "true", "1", "\"NAME\"", "{}", "[null]", "[1]", "[true]", "[[]]", "[{}]" })
            Reject("{\"refno\":\"/CATALOGUE\",\"attributes\":" + value + "}", "attributes type");
        foreach (string value in new[] { "", " ", "POS WRT WORLD", "A*", "A?", "A-B", "A.B", "中文", new string('A', 65) })
            Reject(Body(new { refno = "/CATALOGUE", attributes = new[] { value } }), "invalid attribute name");
        Reject(Body(new { refno = "/CATALOGUE", attributes = new[] { "NAME", " name " } }), "normalized attribute duplicate");
        Reject(Body(new { refno = "/CATALOGUE", limit = 1,
            attributes = Enumerable.Range(0, 11).Select(index => "A" + index).ToArray() }), "maximum attribute count");
        Reject(Body(new { refno = "/CATALOGUE", limit = 51,
            attributes = Enumerable.Range(0, 10).Select(index => "A" + index).ToArray() }), "510 cells exceeds budget");
        Reject(Body(new { refno = "/CATALOGUE", limit = 84,
            attributes = Enumerable.Range(0, 6).Select(index => "A" + index).ToArray() }), "504 cells exceeds budget");
        Reject(Body(new { refno = "/CATALOGUE",
            attributes = Enumerable.Range(0, 8).Select(index => "A" + index).ToArray() }), "default row limit still enforces 500 cells");
    }

    private static void BoundedTransport()
    {
        int budget = 8192;
        string exact = Valid + new string(' ', budget - Encoding.UTF8.GetByteCount(Valid));
        Accept(exact, "/CATALOGUE", 8, 64, new string[0]);
        string utf8 = "{\"refno\":\"/目录\"}";
        Accept(utf8 + new string(' ', budget - Encoding.UTF8.GetByteCount(utf8)), "/目录", 8, 64, new string[0]);
        var request = new ParagonHttpRequest(exact + new string(' ', 65536));
        var context = new ParagonRecordingContext();
        var response = Run(request, context);
        Check(response.StatusCode == 400 && context.Calls == 0, "oversized body rejected before native call");
        Check(request.Request.InputStream.BytesRead <= budget + 1 && request.Request.InputStream.BytesRead < request.Request.InputStream.Length,
            "oversized stream stops at the first over-budget byte");
        Reject(utf8 + new string(' ', budget - Encoding.UTF8.GetByteCount(utf8) + 1), "UTF-8 body budget is bytes");
    }

    private static void OptionalInterfaceAndMethodDispatch()
    {
        var unavailable = new ParagonHttpRequest("not JSON");
        var response = Run(unavailable, new object());
        Check(response.StatusCode == 404 && (bool?)response.Json["ok"] == false, "missing optional interface returns HTTP 404");
        Check(unavailable.Request.InputStream.BytesRead == 0, "missing optional interface does not consume the body");
        foreach (string method in new[] { "GET", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS" })
        {
            var request = new ParagonHttpRequest("not JSON", method);
            var context = new ParagonRecordingContext { Failure = new Exception("must not call") };
            response = Run(request, context);
            Check(response.StatusCode == 405 && response.Headers["Allow"] == "POST", method + ": HTTP 405 with Allow POST");
            Check(context.Calls == 0 && request.Request.InputStream.BytesRead == 0, method + ": no body or native read");
        }
    }

    private static void FailureAndPartialResults()
    {
        var context = new ParagonRecordingContext { Result = new
        {
            available = true, success = false, complete = false,
            before = new { session_id = E3d21HttpAdapter.SessionId, bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D") },
            after = (object)null, context_unchanged = (bool?)null,
            read_errors = new[] { new { stage = "context.after", message = "SDK read failed" } },
            rows = new[] { new { id = 0, name = (string)null, complete = false, values = new { NAME = (string)null }, future_unknown = true } },
            future_unknown = new { opaque = "retain this native evidence" }
        } };
        var response = Run(new ParagonHttpRequest(Valid), context);
        Check(response.StatusCode == 200 && (bool?)response.Json["ok"] == true && context.Calls == 1,
            "ordinary partial native observation retains HTTP success envelope");
        Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(context.Result)),
            "partial, unknown, read_error and null fields are retained without reinterpretation");

        context = new ParagonRecordingContext { Failure = new ArgumentException("native argument validation") };
        response = Run(new ParagonHttpRequest(Valid), context);
        Check(response.StatusCode == 400 && context.Calls == 1, "native argument exception maps to existing HTTP 400");

        context = new ParagonRecordingContext { Failure = new InvalidOperationException(new string('x', 10000)) };
        response = Run(new ParagonHttpRequest(Valid), context);
        Check(response.StatusCode == 500 && context.Calls == 1 && (bool?)response.Json["ok"] == false,
            "unexpected native exception returns HTTP 500 without another native call");
        Check(((string)response.Json["error"]).Length <= 4096, "ordinary SDK error text is bounded");

        context = new ParagonRecordingContext { Failure = new NativeDatabaseFaultException("native fault sentinel") };
        response = Run(new ParagonHttpRequest(Valid), context);
        Check(response.StatusCode == 500 && (bool?)response.Json["ok"] == false && context.Calls == 1,
            "native fault terminates with one native call and HTTP 500");
        Check((string)response.Json["error_code"] == "native_database_fault"
            && (bool?)response.Json["native_fault_latched"] == true && (bool?)response.Json["retry_safe"] == false,
            "native fault retains its dedicated code, latch and unsafe-retry evidence");
        Check((string)response.Json["bridge_build"] == typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D")
            && (string)response.Json["error"] == "native fault sentinel", "native fault preserves the build and short native message");
        Check(response.Json["result"] == null, "native fault cannot become a partial success manifest");

        context = new ParagonRecordingContext { Failure = new NativeDatabaseFaultException(new string('x', 10000)) };
        response = Run(new ParagonHttpRequest(Valid), context);
        Check(response.StatusCode == 500 && context.Calls == 1 && (string)response.Json["error_code"] == "native_database_fault"
            && ((string)response.Json["error"]).Length <= 4096, "long nativefault retains its dedicated path with bounded text");
    }

    private static void Main()
    {
        ValidRequests();
        InvalidJsonAndParameters();
        InvalidTypesAndBounds();
        BoundedTransport();
        OptionalInterfaceAndMethodDispatch();
        FailureAndPartialResults();
        if (failures.Count != 0) throw new Exception(string.Join("\n", failures));
        Console.WriteLine("PARAGON catalogue HTTP production checks passed: " + checks);
    }
}
