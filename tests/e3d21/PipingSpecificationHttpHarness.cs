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
internal sealed class SpecificationCountingStream : MemoryStream
{
    internal int BytesRead;
    internal SpecificationCountingStream(byte[] bytes) : base(bytes) { }
    public override Task<int> ReadAsync(byte[] buffer, int offset, int count,
        System.Threading.CancellationToken cancellationToken)
    {
        int read = Read(buffer, offset, count);
        BytesRead += read;
        return Task.FromResult(read);
    }
}

internal sealed class SpecificationHttpInnerRequest
{
    internal NameValueCollection QueryString;
    internal SpecificationCountingStream InputStream;
    internal Encoding ContentEncoding = Encoding.UTF8;
    internal string ContentType = "application/json";
    internal string HttpMethod;
    internal Uri Url;
    internal bool HasEntityBody;
    internal long ContentLength64;
    internal readonly NameValueCollection Headers = new NameValueCollection();
}

internal sealed class SpecificationHttpResponse
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

internal sealed class SpecificationHttpRequest
{
    internal readonly SpecificationHttpInnerRequest Request;
    internal readonly SpecificationHttpResponse Response = new SpecificationHttpResponse();
    internal SpecificationHttpRequest(string body = "", string method = "POST", NameValueCollection query = null,
        string path = "/api/v1/piping/specification-read")
    {
        byte[] bytes = Encoding.UTF8.GetBytes(body);
        Request = new SpecificationHttpInnerRequest
        {
            InputStream = new SpecificationCountingStream(bytes), QueryString = query ?? new NameValueCollection(),
            HttpMethod = method, Url = new Uri("http://offline.invalid" + path),
            HasEntityBody = bytes.Length != 0, ContentLength64 = bytes.Length
        };
    }
}

internal class SpecificationBaseContext
{
    internal int NativeCalls, CommandStateCalls, ConnectedCalls;
    internal string Refno, ExpectedType, DatabaseType, SessionId;
    internal int MaxDepth, Limit;
    internal List<string> Attributes;
    internal Exception Failure;
    internal object Result = new { available = true, success = true, complete = true, marker = "native-specification" };
    internal object CommandState() { CommandStateCalls++; return new { database_ready = true }; }
    internal bool Connected { get { ConnectedCalls++; return true; } }
    public object ReadPipingSpecification(string refno, string expectedType, string databaseType,
        int maxDepth, int limit, IList<string> attributes, string sessionId)
    {
        NativeCalls++;
        Refno = refno; ExpectedType = expectedType; DatabaseType = databaseType;
        MaxDepth = maxDepth; Limit = limit; SessionId = sessionId;
        Attributes = attributes == null ? null : attributes.ToList();
        if (Failure != null) throw Failure;
        return Result;
    }
}

internal sealed class SpecificationOnlyContext : SpecificationBaseContext, IE3d21PipingSpecificationReadContext { }

namespace E3d21AiBridge
{
    internal sealed class E3d21HttpAdapter
    {
        internal const string SessionId = "piping-specification-http-session";
        private const int MaximumJsonRequestBodyBytes = 1024 * 1024;
        private readonly string sessionId = SessionId;
        private readonly SpecificationBaseContext context;
        internal int FallbackDiagnosticsCalls;
        internal E3d21HttpAdapter(SpecificationBaseContext context) { this.context = context; }
        internal Task Invoke(SpecificationHttpRequest request) { return HandleAsync(request); }
        private object SafeCommandStateDiagnostics()
        {
            FallbackDiagnosticsCalls++;
            return new { forbidden_native_diagnostics = true };
        }

        // PRODUCTION_HANDLER

        // PRODUCTION_HTTP_HELPERS
    }
}

internal static class PipingSpecificationHttpHarness
{
    private const string Route = "/api/v1/piping/specification-read";
    private const string Feature = "piping_specification_read";
    private const string Valid = "{\"refno\":\"=1/2\",\"expected_type\":\"BRAN\",\"database_type\":\"Design\"}";
    private static int checks;
    private static readonly List<string> failures = new List<string>();
    private static string Body(object value) { return JsonConvert.SerializeObject(value); }
    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) failures.Add(message);
    }

    private static SpecificationHttpResponse Run(SpecificationHttpRequest request, SpecificationBaseContext context, bool health = false)
    {
        var adapter = new E3d21HttpAdapter(context);
        adapter.Invoke(request).GetAwaiter().GetResult();
        Check(adapter.FallbackDiagnosticsCalls == 0, "specification path must not invoke fallback command diagnostics");
        Check(context.CommandStateCalls == (health ? 1 : 0) && context.ConnectedCalls == (health ? 1 : 0),
            "only /health explicitly observes command/connection state, never specification failure recovery");
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
            Check(json["state"] == null && json["diagnostic_error"] == null, "specification replies do not include unrequested diagnostics");
        return request.Response;
    }

    private static SpecificationOnlyContext Accept(string body, string reference = "=1/2", string kind = "BRAN",
        string database = "Design", int depth = 8, int limit = 64, params string[] attributes)
    {
        var context = new SpecificationOnlyContext();
        var response = Run(new SpecificationHttpRequest(body), context);
        Check(response.StatusCode == 200 && context.NativeCalls == 1, "valid request invokes native reader once with HTTP 200");
        Check(context.Refno == reference && context.ExpectedType == kind && context.DatabaseType == database &&
            context.MaxDepth == depth && context.Limit == limit && context.Attributes != null && context.Attributes.SequenceEqual(attributes),
            "normalized scope/type/database/attribute arguments forwarded exactly in order");
        Check(context.SessionId == E3d21HttpAdapter.SessionId, "native reader receives this adapter session");
        Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(context.Result)), "native result retained without reinterpretation");
        return context;
    }

    private static void Reject(string body, string label, NameValueCollection query = null)
    {
        var context = new SpecificationOnlyContext();
        var response = Run(new SpecificationHttpRequest(body, query: query), context);
        Check(response.StatusCode == 400 && context.NativeCalls == 0, label + ": reject before every native read");
    }

    private static void ValidRequests()
    {
        Accept(Valid);
        Accept(Body(new { refno = " =0002/0000 ", expected_type = " bran ", database_type = " design " }), "=2/0");
        foreach (string database in new[] { "Design", "Catalogue", "Property", "Dictionary" })
            Accept(Body(new { refno = "=2147483647/2147483647", expected_type = "SPEC", database_type = database.ToLowerInvariant(),
                max_depth = 16, limit = 100, attributes = new string[0] }), "=2147483647/2147483647", "SPEC", database, 16, 100);
        Accept(Body(new { refno = "=1/0", expected_type = "A_1", database_type = "Catalogue", max_depth = 1, limit = 1,
            attributes = new[] { " name ", "Desc", ":uda" } }), "=1/0", "A_1", "Catalogue", 1, 1, "NAME", "DESC", ":UDA");
        string[] ten = Enumerable.Range(0, 10).Select(index => "A" + index).ToArray();
        Accept(Body(new { refno = "=1/2", expected_type = "BRAN", database_type = "Design", limit = 50, attributes = ten }),
            limit: 50, attributes: ten);
        Accept(Body(new { refno = "=1/2", expected_type = new string('A', 64), database_type = "Design", limit = 1,
            attributes = new[] { new string('A', 64) } }), kind: new string('A', 64), limit: 1, attributes: new[] { new string('A', 64) });
    }

    private static void InvalidJsonAndKeys()
    {
        foreach (string body in new[] { "", " ", "null", "true", "123", "\"text\"", "[]", "[{}]", "{", "{} {}", Valid + " false" })
            Reject(body, "malformed/nonobject/trailing JSON");
        foreach (string key in new[] { "dry_run", "version", "request_id", "session_id", "expected_session_id", "bridge_build",
            "command", "source", "include_constraints", "cursor", "offset", "Refno", "unexpected" })
        {
            var body = JObject.Parse(Valid);
            body[key] = true;
            Reject(body.ToString(Formatting.None), "unknown exact-case request field " + key);
        }
        foreach (string key in new[] { "refno", "expected_type", "database_type", "max_depth", "limit", "attributes" })
        {
            string value = key == "attributes" ? "[]" : key == "max_depth" || key == "limit" ? "1" :
                key == "refno" ? "\"=1/2\"" : key == "expected_type" ? "\"BRAN\"" : "\"Design\"";
            string prefix = Valid.Substring(0, Valid.Length - 1);
            if (key == "attributes" || key == "max_depth" || key == "limit") prefix += ",\"" + key + "\":" + value;
            Reject(prefix + ",\"" + key + "\":" + value + "}", "duplicate literal field " + key);
        }
        Reject(Valid.Substring(0, Valid.Length - 1) + ",\"refn\\u006f\":\"=1/2\"}", "duplicate decoded JSON field");
        foreach (string key in new[] { "refno", "expected_type", "database_type", "limit", "unexpected", null })
        {
            var query = new NameValueCollection { { key, "1" } };
            var request = new SpecificationHttpRequest("not JSON", query: query);
            var context = new SpecificationOnlyContext();
            var response = Run(request, context);
            Check(response.StatusCode == 400 && context.NativeCalls == 0 && request.Request.InputStream.BytesRead == 0,
                "query rejected before body parsing or native work");
        }
    }

    private static void InvalidTypesAndBounds()
    {
        foreach (string field in new[] { "refno", "expected_type", "database_type" })
        {
            var missing = JObject.Parse(Valid);
            missing.Remove(field);
            Reject(missing.ToString(Formatting.None), "missing required " + field);
            foreach (string value in new[] { "null", "true", "1", "[]", "{}" })
            {
                var body = JObject.Parse(Valid);
                body[field] = JToken.Parse(value);
                Reject(body.ToString(Formatting.None), "required string type " + field);
            }
        }
        foreach (string reference in new[] { "", "CE", "WORLD", "*", "/", "/P/B", "/=1/2", "/123/456", "1/2", "=1/2/3",
            "=0/1", "=-1/1", "=1/-1", "=1/+1", "=1/2*", "=1/?", "=1/ 2", "=1/2\n=1/3", "=2147483648/1", "=1/2147483648",
            "=" + new string('0', 256) + "1/2" })
        {
            var body = JObject.Parse(Valid);
            body["refno"] = reference;
            Reject(body.ToString(Formatting.None), "HTTP requires one numeric Int32 DBREF, never a name");
        }
        foreach (string kind in new[] { "", " ", "1BRAN", ":CUSTOM", "BRAN;", "A*", "A?", "A-B", "BRAN PIPE", "Brán", new string('A', 65) })
        {
            var body = JObject.Parse(Valid);
            body["expected_type"] = kind;
            Reject(body.ToString(Formatting.None), "literal native SDK ShortName bound");
        }
        foreach (string database in new[] { "", " ", "DESI", "CATA", "PROP", "DICT", "Administration", "2", "Design;" })
        {
            var body = JObject.Parse(Valid);
            body["database_type"] = database;
            Reject(body.ToString(Formatting.None), "explicit allowed database type");
        }
        foreach (string field in new[] { "max_depth", "limit" })
            foreach (string value in new[] { "null", "true", "false", "\"1\"", "1.0", "[]", "{}", "NaN", "Infinity", "2147483648",
                "-2147483649", "9999999999999999999999999999999999999999999" })
                Reject(Valid.Substring(0, Valid.Length - 1) + ",\"" + field + "\":" + value + "}", field + " requires an actual bounded integer");
        foreach (int depth in new[] { -1, 0, 17, int.MaxValue })
            Reject(Body(new { refno = "=1/2", expected_type = "BRAN", database_type = "Design", max_depth = depth }), "depth 1..16");
        foreach (int limit in new[] { -1, 0, 101, int.MaxValue })
            Reject(Body(new { refno = "=1/2", expected_type = "BRAN", database_type = "Design", limit = limit }), "root-inclusive limit 1..100");
        foreach (string value in new[] { "null", "true", "1", "\"NAME\"", "{}", "[null]", "[1]", "[true]", "[[]]", "[{}]" })
            Reject(Valid.Substring(0, Valid.Length - 1) + ",\"attributes\":" + value + "}", "attributes requires an explicit string array");
        foreach (string name in new[] { "", " ", "NAME DESC", "A*", "A?", "A-B", "A.B", "中文", new string('A', 65) })
            Reject(Body(new { refno = "=1/2", expected_type = "BRAN", database_type = "Design", attributes = new[] { name } }), "attribute literal bound");
        Reject(Body(new { refno = "=1/2", expected_type = "BRAN", database_type = "Design", attributes = new[] { "NAME", " name " } }),
            "normalized duplicate attributes");
        Reject(Body(new { refno = "=1/2", expected_type = "BRAN", database_type = "Design", limit = 1,
            attributes = Enumerable.Range(0, 11).Select(index => "A" + index).ToArray() }), "maximum ten requested attributes");
        foreach (int limit in new[] { 51, 100 })
            Reject(Body(new { refno = "=1/2", expected_type = "BRAN", database_type = "Design", limit = limit,
                attributes = Enumerable.Range(0, 10).Select(index => "A" + index).ToArray() }), "500 row/attribute cell budget");
        Reject(Body(new { refno = "=1/2", expected_type = "BRAN", database_type = "Design",
            attributes = Enumerable.Range(0, 8).Select(index => "A" + index).ToArray() }), "default limit is also subject to cell budget");
    }

    private static void ByteBudget()
    {
        const int budget = 8192;
        string exact = Valid + new string(' ', budget - Encoding.UTF8.GetByteCount(Valid));
        Accept(exact);
        string unicode = Body(new { refno = "=1/2", expected_type = "\u3000BRAN\u3000", database_type = "Design" });
        Check(Encoding.UTF8.GetByteCount(unicode) > unicode.Length, "UTF-8 budget test contains literal multibyte input");
        Accept(unicode + new string(' ', budget - Encoding.UTF8.GetByteCount(unicode)));
        var context = new SpecificationOnlyContext();
        var request = new SpecificationHttpRequest(exact + new string(' ', 65536));
        var response = Run(request, context);
        Check(response.StatusCode == 400 && context.NativeCalls == 0, "over-budget JSON rejected before SDK work");
        Check(request.Request.InputStream.BytesRead <= budget + 1 && request.Request.InputStream.BytesRead < request.Request.InputStream.Length,
            "JSON reader stops at the first over-budget byte");
        Reject(unicode + new string(' ', budget - Encoding.UTF8.GetByteCount(unicode) + 1), "8192 budget counts UTF-8 bytes");
    }

    private static void MethodsAndOptionalCapability()
    {
        var absent = new SpecificationBaseContext();
        var request = new SpecificationHttpRequest("not JSON");
        var response = Run(request, absent);
        Check(response.StatusCode == 404 && absent.NativeCalls == 0 && request.Request.InputStream.BytesRead == 0,
            "unavailable capability is HTTP 404 without reading body or SDK");
        foreach (string method in new[] { "GET", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS" })
            foreach (SpecificationBaseContext context in new SpecificationBaseContext[] {
                new SpecificationBaseContext(), new SpecificationOnlyContext { Failure = new Exception("must not call") } })
            {
                request = new SpecificationHttpRequest("not JSON", method);
                response = Run(request, context);
                Check(response.StatusCode == 405 && response.Headers["Allow"] == "POST", "wrong method is 405 Allow POST regardless of optional capability");
                Check(context.NativeCalls == 0 && request.Request.InputStream.BytesRead == 0, "wrong method performs no parse or SDK work");
            }
        foreach (SpecificationBaseContext context in new SpecificationBaseContext[] { new SpecificationBaseContext(), new SpecificationOnlyContext() })
        {
            request = new SpecificationHttpRequest(method: "GET", path: "/health");
            response = Run(request, context, health: true);
            var features = ((JArray)response.Json["api_features"]).Values<string>().ToList();
            Check(features.Contains(Feature) == (context is IE3d21PipingSpecificationReadContext), "health advertises only an implemented optional specification capability");
            Check(features.Count == features.Distinct(StringComparer.Ordinal).Count(), "health features contain no duplicates");
            Check(context.NativeCalls == 0 && request.Request.InputStream.BytesRead == 0, "health discovery never probes the specification SDK reader");
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
        var context = new SpecificationOnlyContext { Result = new
        {
            available = true, success = false, complete = false, atomic_snapshot = false, read_only = true,
            requested = new { refno = "=1/2", expected_type = "BRAN", database_type = "Design" },
            read_errors = new[] { new { stage = "member", refno = (string)null, message = "SDK stopped" } },
            context = new { before = new { session_id = E3d21HttpAdapter.SessionId }, after = (object)null, unchanged = (bool?)null },
            rows = new[] { new { id = 0, name = (string)null, complete = false, future_unknown = true } },
            future_evidence = "retain exact native partial result"
        } };
        var response = Run(new SpecificationHttpRequest(Valid), context);
        Check(response.StatusCode == 200 && context.NativeCalls == 1 && (bool?)response.Json["ok"] == true,
            "partial result retains HTTP success envelope without claiming native completeness");
        Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(context.Result)), "partial/null/read_errors/extra fields survive serialization unchanged");
        string surrogateBoundary = new string('x', 4092) + char.ConvertFromUtf32(0x1F600) + new string('y', 8000);
        string surrogateAtLimit = new string('x', 4095) + char.ConvertFromUtf32(0x1F600) + new string('y', 8000);
        foreach (Exception failure in new Exception[] {
            new ArgumentException("native argument invalid"), new JsonReaderException("native JSON invalid"),
            new InvalidOperationException("native read failed"), new InvalidOperationException(new string('x', 10000)),
            new InvalidOperationException(surrogateBoundary), new InvalidOperationException(surrogateAtLimit),
            new NativeDatabaseFaultException("native fault sentinel"), new NativeDatabaseFaultException(new string('x', 10000)),
            new NativeDatabaseFaultException(surrogateBoundary), new NativeDatabaseFaultException(surrogateAtLimit) })
        {
            context = new SpecificationOnlyContext { Failure = failure };
            response = Run(new SpecificationHttpRequest(Valid), context);
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
            Console.WriteLine("PIPING specification HTTP production checks passed: " + checks);
            return 0;
        }
        catch (Exception failure)
        {
            Console.Error.WriteLine(failure.GetType().FullName + ": " + failure.Message);
            return 1;
        }
    }
}
