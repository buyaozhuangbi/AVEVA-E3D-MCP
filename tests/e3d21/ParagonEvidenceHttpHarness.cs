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

// Memory carriers replace only HTTP I/O; the adapter handler, helpers, request
// validators and optional interface declarations are extracted production code.
internal sealed class EvidenceCountingStream : MemoryStream
{
    internal int BytesRead;
    internal EvidenceCountingStream(byte[] bytes) : base(bytes) { }
    public override Task<int> ReadAsync(byte[] buffer, int offset, int count,
        System.Threading.CancellationToken cancellationToken)
    {
        int result = Read(buffer, offset, count);
        BytesRead += result;
        return Task.FromResult(result);
    }
}

internal sealed class EvidenceHttpInnerRequest
{
    internal NameValueCollection QueryString;
    internal EvidenceCountingStream InputStream;
    internal Encoding ContentEncoding = Encoding.UTF8;
    internal string ContentType = "application/json";
    internal string HttpMethod;
    internal Uri Url;
    internal bool HasEntityBody;
    internal long ContentLength64;
    internal readonly NameValueCollection Headers = new NameValueCollection();
}

internal sealed class EvidenceHttpResponse
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

internal sealed class EvidenceHttpRequest
{
    internal readonly EvidenceHttpInnerRequest Request;
    internal readonly EvidenceHttpResponse Response = new EvidenceHttpResponse();
    internal EvidenceHttpRequest(string json = "", string method = "GET", string path = "/api/v1/module/context",
        NameValueCollection query = null)
    {
        byte[] bytes = Encoding.UTF8.GetBytes(json);
        Request = new EvidenceHttpInnerRequest
        {
            InputStream = new EvidenceCountingStream(bytes), QueryString = query ?? new NameValueCollection(),
            HttpMethod = method, Url = new Uri("http://offline.invalid" + path),
            HasEntityBody = bytes.Length != 0, ContentLength64 = bytes.Length
        };
    }
}

internal class EvidenceBaseContext
{
    internal int ModuleCalls, SchemaCalls, ConnectivityCalls, CommandStateCalls, ConnectedCalls;
    internal string SessionId, Refno;
    internal List<string> Attributes;
    internal bool IncludeConstraints;
    internal Exception Failure;
    internal object Result = new { available = true, success = true, complete = true, marker = "native-evidence" };
    internal object CommandState() { CommandStateCalls++; return new { database_ready = true }; }
    internal bool Connected { get { ConnectedCalls++; return true; } }
    public object ReadConnectivitySnapshot(ConnectivitySnapshotRequest request, string sessionId, string bridgeBuild)
    {
        ConnectivityCalls++;
        throw new InvalidOperationException("PARAGON evidence tests must not invoke connectivity snapshot reads.");
    }
    protected object ModuleRead(string sessionId)
    {
        ModuleCalls++;
        SessionId = sessionId;
        if (Failure != null) throw Failure;
        return Result;
    }
    protected object SchemaRead(string refno, IList<string> attributes, bool includeConstraints, string sessionId)
    {
        SchemaCalls++;
        Refno = refno; Attributes = attributes == null ? null : attributes.ToList();
        IncludeConstraints = includeConstraints; SessionId = sessionId;
        if (Failure != null) throw Failure;
        return Result;
    }
}

internal sealed class ModuleOnlyContext : EvidenceBaseContext, IE3d21ModuleContext
{
    public object ReadModuleContext(string sessionId) { return ModuleRead(sessionId); }
}

internal sealed class SchemaOnlyContext : EvidenceBaseContext, IE3d21ParagonCatalogueSchemaContext
{
    public object ReadParagonCatalogueSchema(string refno, IList<string> attributes, bool includeConstraints, string sessionId)
    { return SchemaRead(refno, attributes, includeConstraints, sessionId); }
}

internal sealed class BothEvidenceContext : EvidenceBaseContext, IE3d21ModuleContext, IE3d21ParagonCatalogueSchemaContext
{
    public object ReadModuleContext(string sessionId) { return ModuleRead(sessionId); }
    public object ReadParagonCatalogueSchema(string refno, IList<string> attributes, bool includeConstraints, string sessionId)
    { return SchemaRead(refno, attributes, includeConstraints, sessionId); }
}

internal sealed class ConnectivityOnlyEvidenceContext : EvidenceBaseContext, IE3d21ConnectivitySnapshotContext { }

namespace E3d21AiBridge
{
    // Only the optional capability predicate and shared catch dependency occur
    // in this selected handler. The connectivity route itself is not included.
    internal sealed class ConnectivitySnapshotRequest { }

    internal interface IE3d21ConnectivitySnapshotContext
    {
        object ReadConnectivitySnapshot(ConnectivitySnapshotRequest request, string sessionId, string bridgeBuild);
    }

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
        internal const string SessionId = "paragon-evidence-session";
        private const int MaximumJsonRequestBodyBytes = 1024 * 1024;
        private readonly string sessionId = SessionId;
        private readonly EvidenceBaseContext context;
        internal int FallbackDiagnosticsCalls;
        internal E3d21HttpAdapter(EvidenceBaseContext context) { this.context = context; }
        internal Task Invoke(EvidenceHttpRequest request) { return HandleAsync(request); }
        private object SafeCommandStateDiagnostics()
        {
            FallbackDiagnosticsCalls++;
            return new { forbidden_native_diagnostics = true };
        }

        // PRODUCTION_HANDLER

        // PRODUCTION_HTTP_HELPERS
    }
}

internal static class ParagonEvidenceHttpHarness
{
    private const string ModulePath = "/api/v1/module/context";
    private const string SchemaPath = "/api/v1/paragon/catalogue-schema";
    private const string Valid = "{\"refno\":\"/CATALOGUE\"}";
    private static int checks;
    private static readonly List<string> failures = new List<string>();
    private static string Body(object value) { return JsonConvert.SerializeObject(value); }
    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) failures.Add(message);
    }

    private static EvidenceHttpResponse Run(EvidenceHttpRequest request, EvidenceBaseContext context, bool health = false)
    {
        var adapter = new E3d21HttpAdapter(context);
        adapter.Invoke(request).GetAwaiter().GetResult();
        Check(adapter.FallbackDiagnosticsCalls == 0, "evidence route must not run fallback diagnostics");
        Check(context.CommandStateCalls == (health ? 1 : 0) && context.ConnectedCalls == (health ? 1 : 0),
            "only /health may inspect native command or connection diagnostics");
        Check(context.ConnectivityCalls == 0, "PARAGON evidence and health never invoke connectivity snapshot reads");
        Check(request.Response.CloseCalls == 1, "response closes exactly once");
        Check(request.Response.ContentType == "application/json; charset=utf-8" &&
            request.Response.ContentLength64 == request.Response.OutputStream.Length,
            "wire metadata describes the serialized production JSON");
        JObject json = request.Response.Json;
        Check((string)json["version"] == "E3D21" && (string)json["session_id"] == E3d21HttpAdapter.SessionId,
            "every evidence response binds version and bridge session");
        Check((string)json["bridge_build"] == typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
            request.Request.HttpMethod + " " + request.Request.Url.AbsolutePath + " HTTP " + request.Response.StatusCode +
            ": response binds the executing assembly MVID");
        if (!health)
            Check(json["state"] == null && json["diagnostic_error"] == null,
                "evidence responses do not include fallback diagnostic observations");
        return request.Response;
    }

    private static EvidenceHttpRequest Schema(string json, string method = "POST", NameValueCollection query = null)
    { return new EvidenceHttpRequest(json, method, SchemaPath, query); }

    private static SchemaOnlyContext AcceptSchema(string body, string reference, bool constraints, params string[] attributes)
    {
        var context = new SchemaOnlyContext();
        EvidenceHttpResponse response = Run(Schema(body), context);
        Check(response.StatusCode == 200 && (bool?)response.Json["ok"] == true, "valid schema request HTTP 200");
        Check(context.SchemaCalls == 1 && context.ModuleCalls == 0 && context.Refno == reference &&
            context.IncludeConstraints == constraints && context.Attributes != null && context.Attributes.SequenceEqual(attributes),
            "normalized schema arguments forwarded exactly once in requested order");
        Check(context.SessionId == E3d21HttpAdapter.SessionId, "schema receives the exact adapter session");
        Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(context.Result)), "schema result retained verbatim");
        return context;
    }

    private static void RejectSchema(string body, string label, NameValueCollection query = null)
    {
        var context = new SchemaOnlyContext();
        EvidenceHttpResponse response = Run(Schema(body, query: query), context);
        Check(response.StatusCode == 400 && (bool?)response.Json["ok"] == false, label + ": HTTP 400");
        Check(context.SchemaCalls == 0 && context.ModuleCalls == 0, label + ": native capability not called");
    }

    private static void ModuleBodyAndQuery()
    {
        var context = new ModuleOnlyContext();
        var request = new EvidenceHttpRequest();
        EvidenceHttpResponse response = Run(request, context);
        Check(response.StatusCode == 200 && (bool?)response.Json["ok"] == true && context.ModuleCalls == 1 && context.SchemaCalls == 0,
            "module GET calls only the module capability once");
        Check(context.SessionId == E3d21HttpAdapter.SessionId && request.Request.InputStream.BytesRead == 0,
            "module GET forwards session and does not consume a body");
        Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(context.Result)), "module result retained verbatim");
        foreach (string body in new[] { " ", "{}", "null", "false", "not JSON", new string('x', 16384) })
        {
            context = new ModuleOnlyContext();
            request = new EvidenceHttpRequest(body);
            response = Run(request, context);
            Check(response.StatusCode == 400 && context.ModuleCalls == 0 && request.Request.InputStream.BytesRead == 0,
                "module GET rejects a declared body before native call or body consumption");
        }
        foreach (string key in new[] { "refno", "include_constraints", "unexpected", null })
        {
            context = new ModuleOnlyContext();
            request = new EvidenceHttpRequest(query: new NameValueCollection { { key, "1" } });
            response = Run(request, context);
            Check(response.StatusCode == 400 && context.ModuleCalls == 0 && request.Request.InputStream.BytesRead == 0,
                "module GET rejects every query parameter before native work");
        }
        context = new ModuleOnlyContext();
        request = new EvidenceHttpRequest("{}");
        request.Request.ContentLength64 = -1;
        request.Request.Headers["Transfer-Encoding"] = "chunked";
        response = Run(request, context);
        Check(response.StatusCode == 400 && context.ModuleCalls == 0 && request.Request.InputStream.BytesRead == 0,
            "module GET also rejects a declared chunked entity");
    }

    private static void ValidSchemaRequests()
    {
        AcceptSchema(Valid, "/CATALOGUE", false);
        AcceptSchema(Body(new { refno = " /MixedCase " }), "/MixedCase", false);
        AcceptSchema(Body(new { refno = "=0002/0000", attributes = new string[0], include_constraints = false }), "=2/0", false);
        AcceptSchema(Body(new { refno = "=2147483647/2147483647", attributes = new[] { " name ", "Desc", ":uda" } }),
            "=2147483647/2147483647", false, "NAME", "DESC", ":UDA");
        AcceptSchema(Body(new { refno = "/SCOM", attributes = new[] { " heig " }, include_constraints = true }),
            "/SCOM", true, "HEIG");
        string[] ten = Enumerable.Range(0, 10).Select(index => "A" + index).ToArray();
        AcceptSchema(Body(new { refno = "/CATA", attributes = ten, include_constraints = true }), "/CATA", true, ten);
        AcceptSchema(Body(new { refno = "/CATA", attributes = new[] { new string('A', 64) } }), "/CATA", false, new string('A', 64));
    }

    private static void InvalidSchemaJsonAndShape()
    {
        foreach (string body in new[] { "", " ", "null", "true", "123", "\"text\"", "[]", "[{}]", "{", "{} {}",
            "{\"refno\":\"/CATA\"} false", "{\"Refno\":\"/CATA\"}",
            "{\"refno\":\"/CATA\",\"dry_run\":true}", "{\"refno\":\"/CATA\",\"offset\":0}",
            "{\"refno\":\"/CATA\",\"limit\":1}", "{\"refno\":\"/CATA\",\"max_depth\":1}" })
            RejectSchema(body, "malformed/nonobject/unknown parameter");
        foreach (string duplicate in new[] { "\"refno\":\"/OTHER\"", "\"refn\\u006f\":\"/OTHER\"",
            "\"attributes\":[],\"attributes\":[]", "\"include_constraints\":false,\"include_constraints\":true" })
            RejectSchema("{\"refno\":\"/CATA\"," + duplicate + "}", "duplicate decoded key");
        foreach (string key in new[] { "refno", "attributes", "include_constraints", "unexpected", null })
            RejectSchema(Valid, "query is forbidden", new NameValueCollection { { key, "1" } });
        foreach (string value in new[] { "null", "true", "1", "[]", "{}" })
            RejectSchema("{\"refno\":" + value + "}", "refno type");
        RejectSchema("{}", "missing explicit refno");
        foreach (string value in new[] { "", "CE", "WORLD", "*", "/", "/CAT*", "/CAT?", "/A B", "/A\nB",
            "=0/1", "=-1/1", "=1/-1", "=1/+1", "=1/1/1", "=2147483648/1", "=1/2147483648", "/" + new string('A', 256) })
            RejectSchema(Body(new { refno = value }), "literal reference bounds");
        foreach (string value in new[] { "null", "true", "1", "\"NAME\"", "{}", "[null]", "[1]", "[true]", "[[]]", "[{}]" })
            RejectSchema("{\"refno\":\"/CATA\",\"attributes\":" + value + "}", "attributes type");
        foreach (string value in new[] { "", " ", "POS WRT WORLD", "A*", "A?", "A-B", "A.B", "中文", new string('A', 65) })
            RejectSchema(Body(new { refno = "/CATA", attributes = new[] { value } }), "attribute literal bounds");
        RejectSchema(Body(new { refno = "/CATA", attributes = new[] { "NAME", " name " } }), "duplicate normalized attribute");
        RejectSchema(Body(new { refno = "/CATA", attributes = Enumerable.Range(0, 11).Select(index => "A" + index).ToArray() }),
            "eleven attributes exceed the endpoint bound");
        foreach (string value in new[] { "null", "0", "1", "\"true\"", "[]", "{}" })
            RejectSchema("{\"refno\":\"/CATA\",\"attributes\":[\"NAME\"],\"include_constraints\":" + value + "}",
                "include_constraints requires an actual boolean");
        RejectSchema("{\"refno\":\"/CATA\",\"include_constraints\":true}", "constraints require explicitly named attributes");
        RejectSchema("{\"refno\":\"/CATA\",\"attributes\":[],\"include_constraints\":true}", "constraints reject empty attributes");
    }

    private static void SchemaByteBudget()
    {
        const int budget = 8192;
        string exact = Valid + new string(' ', budget - Encoding.UTF8.GetByteCount(Valid));
        AcceptSchema(exact, "/CATALOGUE", false);
        string unicode = Body(new { refno = "/目录" });
        AcceptSchema(unicode + new string(' ', budget - Encoding.UTF8.GetByteCount(unicode)), "/目录", false);
        var context = new SchemaOnlyContext();
        var request = Schema(exact + new string(' ', 65536));
        var response = Run(request, context);
        Check(response.StatusCode == 400 && context.SchemaCalls == 0, "oversized body rejected before native capability");
        Check(request.Request.InputStream.BytesRead <= budget + 1 &&
            request.Request.InputStream.BytesRead < request.Request.InputStream.Length,
            "body reader stops at the first over-budget byte");
        RejectSchema(unicode + new string(' ', budget - Encoding.UTF8.GetByteCount(unicode) + 1), "UTF-8 bound is bytes");
    }

    private static void OptionalCapabilitiesAndMethods()
    {
        foreach (bool module in new[] { false, true })
        {
            EvidenceBaseContext absent = module ? (EvidenceBaseContext)new SchemaOnlyContext() : new ModuleOnlyContext();
            var request = new EvidenceHttpRequest("not JSON", module ? "GET" : "POST", module ? ModulePath : SchemaPath);
            var response = Run(request, absent);
            Check(response.StatusCode == 404 && (bool?)response.Json["ok"] == false,
                "absent optional capability gives HTTP 404 even when the other capability exists");
            Check(absent.ModuleCalls == 0 && absent.SchemaCalls == 0 && request.Request.InputStream.BytesRead == 0,
                "unavailable capability does not inspect body or call either native capability");
            foreach (string method in new[] { "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS" }.Where(
                value => value != (module ? "GET" : "POST")))
            {
                foreach (EvidenceBaseContext context in new EvidenceBaseContext[] {
                    new BothEvidenceContext { Failure = new Exception("wrong method must not call") }, new EvidenceBaseContext() })
                {
                    request = new EvidenceHttpRequest("not JSON", method, module ? ModulePath : SchemaPath);
                    response = Run(request, context);
                    Check(response.StatusCode == 405 && response.Headers["Allow"] == (module ? "GET" : "POST"),
                        "wrong method is HTTP 405 with the exact Allow verb, independent of optional capability");
                    Check(context.ModuleCalls == 0 && context.SchemaCalls == 0 && request.Request.InputStream.BytesRead == 0,
                        "wrong methods neither read JSON nor invoke any capability");
                }
            }
        }
    }

    private static void DynamicHealthFeatures()
    {
        foreach (EvidenceBaseContext context in new EvidenceBaseContext[] {
            new EvidenceBaseContext(), new ModuleOnlyContext(), new SchemaOnlyContext(), new BothEvidenceContext(),
            new ConnectivityOnlyEvidenceContext() })
        {
            var response = Run(new EvidenceHttpRequest(path: "/health"), context, health: true);
            Check(response.StatusCode == 200 && (bool?)response.Json["ok"] == true, "health observation succeeds");
            var features = ((JArray)response.Json["api_features"]).Values<string>().ToList();
            Check(features.Contains("module_context") == (context is IE3d21ModuleContext),
                "module feature follows only its actual optional interface");
            Check(features.Contains("paragon_catalogue_schema") == (context is IE3d21ParagonCatalogueSchemaContext),
                "schema feature follows only its actual optional interface");
            Check(features.Contains("piping_connectivity_snapshot") == (context is IE3d21ConnectivitySnapshotContext),
                "unrelated connectivity feature has both present and absent optional-interface controls");
            Check(features.Count == features.Distinct(StringComparer.Ordinal).Count(), "health feature identifiers are unique");
            Check(context.ModuleCalls == 0 && context.SchemaCalls == 0, "health feature discovery does not probe native evidence");
        }
    }

    private static void FailureAndPartialResults()
    {
        object partial = new
        {
            available = true, success = false, complete = false, context_unchanged = (bool?)null,
            before = new { session_id = E3d21HttpAdapter.SessionId }, after = (object)null,
            read_errors = new[] { new { stage = "context.after", error = "SDK failed" } },
            rows = new[] { new { id = 0, name = (string)null, complete = false, future_unknown = true } },
            future_unknown = new { retain = "unaltered future evidence" }
        };
        foreach (bool module in new[] { false, true })
        {
            var context = new BothEvidenceContext { Result = partial };
            var response = Run(new EvidenceHttpRequest(module ? "" : Valid, module ? "GET" : "POST", module ? ModulePath : SchemaPath), context);
            Check(response.StatusCode == 200 && (bool?)response.Json["ok"] == true &&
                context.ModuleCalls == (module ? 1 : 0) && context.SchemaCalls == (module ? 0 : 1),
                "partial native result keeps ordinary success envelope and one native invocation");
            Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(partial)),
                "unknown/partial/null/extra evidence is not reinterpreted or discarded by HTTP");
            foreach (Exception failure in new Exception[] { new ArgumentException("native argument failed"),
                new InvalidOperationException("native read failed"), new InvalidOperationException(new string('x', 10000)),
                new NativeDatabaseFaultException("native fault sentinel"), new NativeDatabaseFaultException(new string('x', 10000)) })
            {
                context = new BothEvidenceContext { Failure = failure };
                response = Run(new EvidenceHttpRequest(module ? "" : Valid, module ? "GET" : "POST", module ? ModulePath : SchemaPath), context);
                bool fault = failure is NativeDatabaseFaultException;
                Check(response.StatusCode == (failure is ArgumentException ? 400 : 500) && (bool?)response.Json["ok"] == false,
                    "ordinary 400/500 and native-fault status remain distinct");
                Check(context.ModuleCalls == (module ? 1 : 0) && context.SchemaCalls == (module ? 0 : 1),
                    "native failure is never retried or followed by another capability");
                Check(response.Json["result"] == null && ((string)response.Json["error"]).Length <= 4096,
                    (module ? "module" : "schema") + " " + failure.GetType().Name +
                    ": failed observation does not fabricate a result and error text stays within 4096 characters");
                if (fault)
                {
                    Check((string)response.Json["error_code"] == "native_database_fault" &&
                        (bool?)response.Json["native_fault_latched"] == true && (bool?)response.Json["retry_safe"] == false,
                        "dedicated native fault catch preserves its latch/code/unsafe-retry contract");
                    if (failure.Message == "native fault sentinel")
                        Check((string)response.Json["error"] == failure.Message, "short native-fault message retained exactly");
                }
                else Check(response.Json["error_code"] == null && response.Json["native_fault_latched"] == null,
                    "ordinary exception does not fabricate a native database fault");
            }
        }
    }

    private static int Main()
    {
        try
        {
            ModuleBodyAndQuery();
            ValidSchemaRequests();
            InvalidSchemaJsonAndShape();
            SchemaByteBudget();
            OptionalCapabilitiesAndMethods();
            DynamicHealthFeatures();
            FailureAndPartialResults();
            if (failures.Count != 0)
            {
                foreach (string failure in failures) Console.Error.WriteLine(failure);
                return 1;
            }
            Console.WriteLine("PARAGON evidence HTTP production checks passed: " + checks);
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.GetType().FullName + ": " + error.Message);
            return 1;
        }
    }
}
