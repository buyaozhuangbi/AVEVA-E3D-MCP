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
internal sealed class ComponentCopyCountingStream : MemoryStream
{
    internal int BytesRead;
    internal ComponentCopyCountingStream(byte[] bytes) : base(bytes) { }
    public override Task<int> ReadAsync(byte[] buffer, int offset, int count,
        System.Threading.CancellationToken cancellationToken)
    {
        int read = Read(buffer, offset, count);
        BytesRead += read;
        return Task.FromResult(read);
    }
}

internal sealed class ComponentCopyHttpInnerRequest
{
    internal NameValueCollection QueryString;
    internal ComponentCopyCountingStream InputStream;
    internal Encoding ContentEncoding = Encoding.UTF8;
    internal string ContentType = "application/json";
    internal string HttpMethod;
    internal Uri Url;
    internal bool HasEntityBody;
    internal long ContentLength64;
    internal readonly NameValueCollection Headers = new NameValueCollection();
}

internal sealed class ComponentCopyHttpResponse
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

internal sealed class ComponentCopyHttpRequest
{
    internal readonly ComponentCopyHttpInnerRequest Request;
    internal readonly ComponentCopyHttpResponse Response = new ComponentCopyHttpResponse();
    internal ComponentCopyHttpRequest(string body = "", string method = "POST", NameValueCollection query = null,
        string path = "/api/v1/paragon/component-copy")
    {
        byte[] bytes = Encoding.UTF8.GetBytes(body);
        Request = new ComponentCopyHttpInnerRequest
        {
            InputStream = new ComponentCopyCountingStream(bytes), QueryString = query ?? new NameValueCollection(),
            HttpMethod = method, Url = new Uri("http://offline.invalid" + path),
            HasEntityBody = bytes.Length != 0, ContentLength64 = bytes.Length
        };
    }
}

internal class ComponentCopyBaseContext
{
    internal int CopyCalls, StatusCalls, CommandStateCalls, ConnectedCalls;
    internal string SourceRefno, ParentRefno, Name, Recipe, RequestId, ExpectedSessionId, ExpectedBridgeBuild, ExpectedRevision, SessionId;
    internal bool DryRun;
    internal Exception CopyFailure, StatusFailure;
    internal ParagonComponentCopyReply Reply = new ParagonComponentCopyReply(200, true,
        new Dictionary<string, object> { ["status"] = "prepared", ["opaque"] = "native-copy" });
    internal object StatusResult = new { status = "accepted", opaque = "managed-status", available = (bool?)null };
    internal object CommandState() { CommandStateCalls++; return new { database_ready = true }; }
    internal bool Connected { get { ConnectedCalls++; return true; } }
    public ParagonComponentCopyReply CopyParagonComponent(string sourceRefno, string parentRefno, string name, string recipe,
        bool dryRun, string requestId, string expectedSessionId, string expectedBridgeBuild, string expectedRevision, string sessionId)
    {
        CopyCalls++;
        SourceRefno = sourceRefno; ParentRefno = parentRefno; Name = name; Recipe = recipe; DryRun = dryRun;
        RequestId = requestId; ExpectedSessionId = expectedSessionId; ExpectedBridgeBuild = expectedBridgeBuild;
        ExpectedRevision = expectedRevision; SessionId = sessionId;
        if (CopyFailure != null) throw CopyFailure;
        return Reply;
    }
    public object GetParagonComponentCopyStatus(string requestId, string expectedSessionId, string sessionId)
    {
        StatusCalls++;
        RequestId = requestId; ExpectedSessionId = expectedSessionId; SessionId = sessionId;
        if (StatusFailure != null) throw StatusFailure;
        return StatusResult;
    }
}

internal sealed class ComponentCopyOnlyContext : ComponentCopyBaseContext, IE3d21ParagonComponentCopyContext { }

namespace E3d21AiBridge
{
    internal sealed class E3d21HttpAdapter
    {
        internal const string SessionId = "component-copy-server-session";
        private const int MaximumJsonRequestBodyBytes = 1024 * 1024;
        private readonly string sessionId = SessionId;
        private readonly ComponentCopyBaseContext context;
        internal int FallbackDiagnosticsCalls;
        internal E3d21HttpAdapter(ComponentCopyBaseContext context) { this.context = context; }
        internal Task Invoke(ComponentCopyHttpRequest request) { return HandleAsync(request); }
        private object SafeCommandStateDiagnostics()
        {
            FallbackDiagnosticsCalls++;
            return new { forbidden_native_diagnostics = true };
        }

        // PRODUCTION_HANDLER

        // PRODUCTION_HTTP_HELPERS
    }
}

internal static class ParagonComponentCopyHttpHarness
{
    private const string Route = "/api/v1/paragon/component-copy";
    private const string StatusRoute = Route + "/status";
    private const string Feature = "paragon_component_copy";
    private const string StatusFeature = "paragon_component_copy_status";
    private const string RecipeName = "same_category_scom_copy_v1";
    private const string RequestId = "baba1111-2222-3333-8444-555555555555";
    private const string ExpectedBuild = "caca1111-2222-3333-8444-555555555555";
    private const string Valid = "{\"source_refno\":\"=7/101\",\"parent_refno\":\"=7/100\",\"name\":\"/EXAMPLE-SCOM\"}";
    private static readonly string Revision = new string('a', 64);
    private static readonly List<string> failures = new List<string>();
    private static int checks;
    private static string Body(object value) { return JsonConvert.SerializeObject(value); }
    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) failures.Add(message);
    }
    private static JObject ExecuteBody()
    {
        var body = JObject.Parse(Valid);
        body["recipe"] = RecipeName; body["dry_run"] = false; body["request_id"] = RequestId;
        body["expected_session_id"] = "expected-session"; body["expected_bridge_build"] = ExpectedBuild;
        body["expected_revision"] = Revision;
        return body;
    }
    private static NameValueCollection Query(string requestId = RequestId, string expectedSession = E3d21HttpAdapter.SessionId)
    {
        return new NameValueCollection { { "request_id", requestId }, { "expected_session_id", expectedSession } };
    }
    private static ComponentCopyHttpRequest StatusRequest(string body = "", string method = "GET", NameValueCollection query = null)
    {
        return new ComponentCopyHttpRequest(body, method, query ?? Query(), StatusRoute);
    }
    private static ComponentCopyHttpResponse Run(ComponentCopyHttpRequest request, ComponentCopyBaseContext context, bool health = false)
    {
        var adapter = new E3d21HttpAdapter(context);
        adapter.Invoke(request).GetAwaiter().GetResult();
        Check(adapter.FallbackDiagnosticsCalls == 0, "copy/status never invokes fallback command diagnostics");
        Check(context.CommandStateCalls == (health ? 1 : 0) && context.ConnectedCalls == (health ? 1 : 0),
            "only explicit health observes command and connection state");
        Check(request.Response.CloseCalls == 1, "response closes exactly once");
        Check(request.Response.ContentType == "application/json; charset=utf-8" &&
              request.Response.ContentLength64 == request.Response.OutputStream.Length, "JSON headers match wire bytes");
        JObject json = request.Response.Json;
        string location = request.Request.HttpMethod + " " + request.Request.Url.AbsolutePath + " HTTP " + request.Response.StatusCode;
        Check((string)json["version"] == "E3D21" && (string)json["session_id"] == E3d21HttpAdapter.SessionId,
            location + ": all replies bind version and server session");
        Check((string)json["bridge_build"] == typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
            location + ": build is the executing production assembly MVID");
        Check((bool?)json["ok"] == (request.Response.StatusCode == 200), location + ": envelope ok agrees with HTTP status");
        if (!health)
            Check(json["state"] == null && json["diagnostic_error"] == null, "copy/status does not add diagnostics or SDK postreads");
        return request.Response;
    }
    private static ComponentCopyOnlyContext Accept(string body, string source = "=7/101", string parent = "=7/100",
        string name = "/EXAMPLE-SCOM", bool dryRun = true, string expectedSession = "expected-session", bool chunked = false)
    {
        var context = new ComponentCopyOnlyContext();
        var request = new ComponentCopyHttpRequest(body);
        if (chunked) { request.Request.ContentLength64 = -1; request.Request.Headers["Transfer-Encoding"] = "chunked"; }
        var response = Run(request, context);
        Check(response.StatusCode == 200 && context.CopyCalls == 1 && context.StatusCalls == 0,
            "valid POST invokes exactly one copy callback");
        Check(context.SourceRefno == source && context.ParentRefno == parent && context.Name == name &&
              context.Recipe == RecipeName && context.DryRun == dryRun, "copy request forwards exact targets/name/recipe/mode");
        Check(context.SessionId == E3d21HttpAdapter.SessionId, "copy callback receives immutable server-owned session");
        Check(dryRun ? context.RequestId == null && context.ExpectedSessionId == null && context.ExpectedBridgeBuild == null && context.ExpectedRevision == null :
              context.RequestId == RequestId && context.ExpectedSessionId == expectedSession && context.ExpectedBridgeBuild == ExpectedBuild && context.ExpectedRevision == Revision,
              "prepare omits execution identities; execute forwards exact request/session/build/revision");
        Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(context.Reply.Result)), "successful native result survives opaquely");
        Check(response.Json.Properties().Select(p => p.Name).OrderBy(n => n).SequenceEqual(
              new[] { "ok", "version", "session_id", "bridge_build", "result" }.OrderBy(n => n)), "success envelope has exactly the standard five fields");
        return context;
    }
    private static void Reject(string body, string label, NameValueCollection query = null)
    {
        var context = new ComponentCopyOnlyContext();
        var response = Run(new ComponentCopyHttpRequest(body, query: query), context);
        Check(response.StatusCode == 400 && context.CopyCalls == 0 && context.StatusCalls == 0, label + ": validation rejects before dispatch");
        Check((bool?)response.Json["write_attempted"] == false && (bool?)response.Json["outcome_uncertain"] == false &&
              (bool?)response.Json["retry_safe"] == false,
              label + ": parse/validation states no write and no automatic retry");
    }
    private static void RejectStatus(ComponentCopyHttpRequest request, string label)
    {
        var context = new ComponentCopyOnlyContext();
        var response = Run(request, context);
        Check(response.StatusCode == 400 && context.StatusCalls == 0 && context.CopyCalls == 0, label + ": no status/copy call");
        Check(request.Request.InputStream.BytesRead == 0, label + ": status never reads a request body");
        Check((bool?)response.Json["write_attempted"] == false && (bool?)response.Json["outcome_uncertain"] == false &&
              (bool?)response.Json["retry_safe"] == false, label + ": status validation cannot imply an attempted write");
    }

    private static void ValidRequests()
    {
        Accept(Valid);
        var body = JObject.Parse(Valid); body["dry_run"] = true; body["recipe"] = RecipeName;
        Accept(body.ToString(Formatting.None));
        foreach (string reference in new[] { "=1/0", "=1/1", "=2147483647/2147483647" })
        {
            body = JObject.Parse(Valid); body["source_refno"] = reference;
            Accept(body.ToString(Formatting.None), source: reference);
            body = JObject.Parse(Valid); body["parent_refno"] = reference;
            Accept(body.ToString(Formatting.None), parent: reference);
        }
        foreach (string name in new[] { "/A", "/A/B_1-C", "/" + new string('A', 49) })
        {
            body = JObject.Parse(Valid); body["name"] = name;
            Accept(body.ToString(Formatting.None), name: name);
        }
        Accept(Valid.Replace("source_refno", "source_refn\\u006f"));
        Accept(ExecuteBody().ToString(Formatting.None), dryRun: false);
        foreach (string session in new[] { "expected-session", " 中文🌍 session ", new string('s', 256),
                                          new string('s', 254) + char.ConvertFromUtf32(0x1F600) })
        {
            body = ExecuteBody(); body["expected_session_id"] = session;
            Accept(body.ToString(Formatting.None), dryRun: false, expectedSession: session);
        }
    }

    private static void InvalidCopyInputs()
    {
        foreach (string body in new[] { "", " ", "null", "true", "false", "1", "\"text\"", "[]", "[{}]", "{", "{} {}", Valid + " false", "{}" })
            Reject(body, "malformed JSON, nonobject or required fields missing");
        foreach (string field in new[] { "Source_refno", "parent", "refno", "attributes", "command", "execute", "version", "session_id", "bridge_build", "limit" })
        {
            var body = JObject.Parse(Valid); body[field] = true;
            Reject(body.ToString(Formatting.None), "unknown exact-case key " + field);
        }
        foreach (string field in new[] { "source_refno", "parent_refno", "name" })
        {
            var body = JObject.Parse(Valid); body.Remove(field);
            Reject(body.ToString(Formatting.None), "required literal target field " + field);
        }
        foreach (string field in new[] { "source_refno", "parent_refno", "name", "recipe", "dry_run", "request_id", "expected_session_id", "expected_bridge_build", "expected_revision" })
        {
            var body = ExecuteBody();
            string suffix = "," + Body(field) + ":" + body[field].ToString(Formatting.None) + "}";
            Reject(body.ToString(Formatting.None).TrimEnd('}') + suffix, "duplicate key " + field);
        }
        Reject(Valid.TrimEnd('}') + ",\"source_refn\\u006f\":\"=7/102\"}", "duplicate decoded source key");
        foreach (string field in new[] { "source_refno", "parent_refno", "name", "recipe" })
            foreach (string value in new[] { "null", "true", "false", "1", "1.0", "[]", "{}" })
            {
                var body = JObject.Parse(Valid); body[field] = JToken.Parse(value);
                Reject(body.ToString(Formatting.None), field + " is a true string");
            }
        foreach (string value in new[] { "null", "1", "0", "1.0", "\"true\"", "[]", "{}" })
        {
            var body = JObject.Parse(Valid); body["dry_run"] = JToken.Parse(value);
            Reject(body.ToString(Formatting.None), "dry_run is a true JSON boolean");
        }
        foreach (string reference in new[] { "", "CE", "WORLD", "/SCOM", "=0/0", "=0/1", "=-1/1", "=1/-1", "=+1/1", "=1/+1", "=01/1", "=1/00",
            " =1/1", "=1/1 ", "=1/1\n", "=1/1\0", "=1/*", "=1/1;SAVEWORK", "=1/１", "=2147483648/1", "=1/2147483648" })
            foreach (string field in new[] { "source_refno", "parent_refno" })
            {
                var body = JObject.Parse(Valid); body[field] = reference;
                Reject(body.ToString(Formatting.None), field + " canonical Int32/no trim/leading zeros");
            }
        var same = JObject.Parse(Valid); same["parent_refno"] = "=7/101";
        Reject(same.ToString(Formatting.None), "source and parent are distinct explicit refs");
        foreach (string name in new[] { "", "/", "SCOM", "//SCOM", "/SCOM/", "/A//B", "/1A", "/A/2B", "/A.B", "/A B", " /A", "/A ", "/A\n", "/A\0",
                                       "/SCOM;SAVEWORK", "/Ä", "/A/../B", "/A/*", "/" + new string('A', 50) })
        {
            var body = JObject.Parse(Valid); body["name"] = name;
            Reject(body.ToString(Formatting.None), "bounded literal ASCII slash-segment name");
        }
        foreach (string recipe in new[] { "", " " + RecipeName, RecipeName + " ", RecipeName.ToUpperInvariant(), "copy", RecipeName + ";SAVEWORK" })
        {
            var body = JObject.Parse(Valid); body["recipe"] = recipe;
            Reject(body.ToString(Formatting.None), "exact fixed recipe only");
        }
        foreach (string field in new[] { "request_id", "expected_session_id", "expected_bridge_build", "expected_revision" })
        {
            var body = ExecuteBody(); body["dry_run"] = true;
            Reject(body.ToString(Formatting.None), "prepare forbids execution-only keys");
            body = JObject.Parse(Valid); body[field] = JValue.CreateNull();
            Reject(body.ToString(Formatting.None), "prepare forbids presence even null: " + field);
            body = JObject.Parse(Valid); body["dry_run"] = true; body[field] = ExecuteBody()[field];
            Reject(body.ToString(Formatting.None), "prepare forbids single valid execution-only field: " + field);
            body = ExecuteBody(); body.Remove(field);
            Reject(body.ToString(Formatting.None), "execute requires " + field);
            foreach (string value in new[] { "null", "true", "1", "1.0", "[]", "{}" })
            {
                body = ExecuteBody(); body[field] = JToken.Parse(value);
                Reject(body.ToString(Formatting.None), "execution identity requires string: " + field);
            }
        }
        foreach (string field in new[] { "request_id", "expected_bridge_build" })
            foreach (string id in InvalidIds())
            {
                var body = ExecuteBody(); body[field] = id;
                Reject(body.ToString(Formatting.None), field + " exact lowercase UUID D");
            }
        foreach (string session in InvalidSessions())
        {
            var body = ExecuteBody(); body["expected_session_id"] = session;
            Reject(body.ToString(Formatting.None), "bounded printable expected session without rewrite");
        }
        foreach (string revision in new[] { "", new string('a', 63), new string('a', 65), new string('A', 64), new string('g', 64), " " + new string('a', 63),
                                           new string('a', 63) + "\n" })
        {
            var body = ExecuteBody(); body["expected_revision"] = revision;
            Reject(body.ToString(Formatting.None), "revision exact lowercase 64 hex");
        }
        foreach (string field in new[] { "source_refno", "request_id", "unexpected", null })
        {
            var request = new ComponentCopyHttpRequest("not JSON", query: new NameValueCollection { { field, "x" } });
            var context = new ComponentCopyOnlyContext();
            var response = Run(request, context);
            Check(response.StatusCode == 400 && context.CopyCalls == 0 && request.Request.InputStream.BytesRead == 0,
                "POST query rejected before parse/dispatch");
        }
    }

    private static string[] InvalidIds()
    {
        return new[] { "", "not-a-uuid", RequestId.ToUpperInvariant(), " " + RequestId, RequestId + " ", RequestId + "\n",
                       "{" + RequestId + "}", RequestId.Replace("-", ""), new string('a', 257) };
    }
    private static string[] InvalidSessions()
    {
        return new[] { "", " ", "\t", "session\n", "session\0", "session\u007f", new string('s', 257),
                       new string('s', 255) + char.ConvertFromUtf32(0x1F600) };
    }

    private static void StatusQueries()
    {
        foreach (string state in new[] { "accepted", "running", "completed", "unknown_or_expired" })
        {
            var context = new ComponentCopyOnlyContext { StatusResult = new { state = state, request_id = RequestId,
                write_attempted = (bool?)null, completion = (object)null, future_unknown = new[] { 1, 2, 3 } } };
            var response = Run(StatusRequest(), context);
            Check(response.StatusCode == 200 && context.StatusCalls == 1 && context.CopyCalls == 0, "status is one managed-only callback");
            Check(context.RequestId == RequestId && context.ExpectedSessionId == E3d21HttpAdapter.SessionId && context.SessionId == E3d21HttpAdapter.SessionId,
                "status forwards exact identity and server-owned session");
            Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(context.StatusResult)), state + ": managed status fields stay opaque");
        }
        foreach (string session in new[] { "expected-session", " " + E3d21HttpAdapter.SessionId + " ", " 中文🌍 session ", new string('s', 256) })
        {
            var context = new ComponentCopyOnlyContext();
            var response = Run(StatusRequest(query: Query(expectedSession: session)), context);
            Check(response.StatusCode == 409 && context.StatusCalls == 0 && context.CopyCalls == 0,
                "foreign or whitespace-padded status session rejected before callback without trimming");
            Check((bool?)response.Json["write_attempted"] == false && (bool?)response.Json["outcome_uncertain"] == false &&
                  (bool?)response.Json["retry_safe"] == false, "status session mismatch neither observes another session nor implies a write");
        }
        RejectStatus(StatusRequest(query: new NameValueCollection()), "missing both required query keys");
        foreach (string field in new[] { "request_id", "expected_session_id" })
        {
            var query = Query(); query.Remove(field);
            RejectStatus(StatusRequest(query: query), "missing query key " + field);
            query = Query(); query.Add(field, "another");
            RejectStatus(StatusRequest(query: query), "duplicate query key " + field);
            query = Query(); query[field] = null;
            RejectStatus(StatusRequest(query: query), "null query value " + field);
        }
        foreach (string field in new[] { "Request_id", "Expected_session_id", "source_refno", "dry_run", "unexpected", null })
        {
            var query = Query(); query.Add(field, "x");
            RejectStatus(StatusRequest(query: query), "unknown/exact-case query key " + field);
        }
        foreach (string id in InvalidIds()) RejectStatus(StatusRequest(query: Query(requestId: id)), "invalid exact status UUID");
        foreach (string session in InvalidSessions()) RejectStatus(StatusRequest(query: Query(expectedSession: session)), "invalid status session");
        foreach (string body in new[] { "{}", " ", "\ufeff", "not JSON" })
            RejectStatus(StatusRequest(body: body), "GET status rejects every nonempty body");
        foreach (bool hasBody in new[] { false, true })
        {
            var request = StatusRequest(); request.Request.HasEntityBody = hasBody;
            request.Request.ContentLength64 = -1; request.Request.Headers["Transfer-Encoding"] = "chunked";
            RejectStatus(request, "GET status rejects unknown/chunked entity framing");
        }
        var declaredBody = StatusRequest(); declaredBody.Request.HasEntityBody = false; declaredBody.Request.ContentLength64 = 1;
        RejectStatus(declaredBody, "GET status rejects nonzero Content-Length before reading even if entity flag is false");
    }

    private static void ByteBudget()
    {
        const int budget = 8192;
        foreach (bool chunked in new[] { false, true })
            foreach (bool includeBom in new[] { false, true })
            {
                string basis = (includeBom ? "\ufeff" : "") + Valid;
                string exact = basis + new string(' ', budget - Encoding.UTF8.GetByteCount(basis));
                Accept(exact, chunked: chunked);
                var request = new ComponentCopyHttpRequest(exact + new string(' ', 65536));
                if (chunked) { request.Request.ContentLength64 = -1; request.Request.Headers["Transfer-Encoding"] = "chunked"; }
                var context = new ComponentCopyOnlyContext();
                var response = Run(request, context);
                Check(response.StatusCode == 400 && context.CopyCalls == 0, "8193+ ordinary/BOM/chunked UTF8 bytes reject before copy");
                Check(request.Request.InputStream.BytesRead <= budget + 1 && request.Request.InputStream.BytesRead < request.Request.InputStream.Length,
                    "bounded body reader stops at first over-budget byte");
                Reject(exact + " ", "8193 UTF8 bytes including BOM over budget");
            }
        var unicode = ExecuteBody(); unicode["expected_session_id"] = "世界🌍";
        string unicodeBody = unicode.ToString(Formatting.None);
        Accept(unicodeBody + new string(' ', budget - Encoding.UTF8.GetByteCount(unicodeBody)), dryRun: false, expectedSession: "世界🌍", chunked: true);
    }

    private static void MethodsAndCapability()
    {
        foreach (string route in new[] { Route, StatusRoute })
        {
            bool status = route == StatusRoute;
            var absent = new ComponentCopyBaseContext();
            var request = new ComponentCopyHttpRequest("not JSON", status ? "GET" : "POST", status ? Query() : null, route);
            var response = Run(request, absent);
            Check(response.StatusCode == 404 && absent.CopyCalls == 0 && absent.StatusCalls == 0 && request.Request.InputStream.BytesRead == 0,
                "unavailable optional interface is 404 without body parse or callback");
            foreach (string method in new[] { "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS" }.Where(m => m != (status ? "GET" : "POST")))
                foreach (ComponentCopyBaseContext context in new ComponentCopyBaseContext[] { new ComponentCopyBaseContext(), new ComponentCopyOnlyContext() })
                {
                    request = new ComponentCopyHttpRequest("not JSON", method, Query(), route);
                    response = Run(request, context);
                    Check(response.StatusCode == 405 && response.Headers["Allow"] == (status ? "GET" : "POST"), "wrong method uses 405 with exact Allow");
                    Check(context.CopyCalls == 0 && context.StatusCalls == 0 && request.Request.InputStream.BytesRead == 0,
                        "wrong method never parses or dispatches regardless of capability");
                }
        }
        foreach (ComponentCopyBaseContext context in new ComponentCopyBaseContext[] { new ComponentCopyBaseContext(), new ComponentCopyOnlyContext() })
        {
            var request = new ComponentCopyHttpRequest(method: "GET", path: "/health");
            var response = Run(request, context, health: true);
            var features = ((JArray)response.Json["api_features"]).Values<string>().ToList();
            bool implemented = context is IE3d21ParagonComponentCopyContext;
            Check(features.Contains(Feature) == implemented && features.Contains(StatusFeature) == implemented,
                "health advertises both optional features iff the shared copy interface exists");
            Check(features.Count == features.Distinct(StringComparer.Ordinal).Count(), "health features are unique");
            Check(context.CopyCalls == 0 && context.StatusCalls == 0, "health never probes a copy/status callback");
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

    private static void OpaqueRepliesAndFailures()
    {
        foreach (string status in new[] { "prepared", "blocked", "executed", "failed", "rejected", "duplicate" })
            foreach (bool ok in new[] { true, false })
            {
                var result = new Dictionary<string, object> { ["status"] = status, ["write_attempted"] = (object)null,
                    ["future_unknown"] = new { exact = true, identity = (string)null }, ["rows"] = new[] { 1, 2, 3 } };
                var context = new ComponentCopyOnlyContext { Reply = new ParagonComponentCopyReply(ok ? 200 : 409, ok, result, ok ? null : "native reply sentinel") };
                var response = Run(new ComponentCopyHttpRequest(Valid), context);
                Check(response.StatusCode == (ok ? 200 : 409) && context.CopyCalls == 1, "reader reply controls native HTTP outcome without retry");
                Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(result)), status + ": opaque native evidence retained verbatim");
                Check(ok ? response.Json["error"] == null : (string)response.Json["error"] == "native reply sentinel", "native error accompanies failed result");
                Check(response.Json["write_attempted"] == null && response.Json["outcome_uncertain"] == null,
                    "returned native progress remains in its opaque result; HTTP does not fabricate top-level attempts");
            }
        var faultResult = new Dictionary<string, object> { ["status"] = "failed", ["write_attempted"] = true,
            ["retained_refno"] = "=7/999", ["readback"] = (object)null, ["opaque"] = new[] { "journal", "must survive" } };
        var nativeFault = new ComponentCopyOnlyContext { Reply = new ParagonComponentCopyReply(500, false, faultResult, "latched reply", true) };
        var faultResponse = Run(new ComponentCopyHttpRequest(Valid), nativeFault);
        Check(faultResponse.StatusCode == 500 && JToken.DeepEquals(faultResponse.Json["result"], JToken.FromObject(faultResult)),
            "native fault reply preserves partial copy/journal evidence");
        Check((string)faultResponse.Json["error_code"] == "native_database_fault" && (bool?)faultResponse.Json["native_fault_latched"] == true &&
              (bool?)faultResponse.Json["retry_safe"] == false, "native fault reply retains dedicated flags");
        foreach (bool fault in new[] { false, true })
        {
            string longError = new string('x', 4092) + char.ConvertFromUtf32(0x1F600) + new string('y', 8000);
            var context = new ComponentCopyOnlyContext { Reply = new ParagonComponentCopyReply(500, false, faultResult, longError, fault) };
            var response = Run(new ComponentCopyHttpRequest(Valid), context);
            string text = (string)response.Json["error"];
            Check(text != null && text.Length <= 4096 && WellFormedUtf16(text) && text.IndexOf('\uFFFD') < 0,
                "returned native error text is bounded without cutting surrogate pairs");
            Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(faultResult)), "error text clipping never alters native journal evidence");
        }
        var errors = new List<Exception> { new ArgumentException("callback argument failure"), new JsonReaderException("callback JSON failure"),
            new InvalidOperationException("callback ordinary failure"), new InvalidOperationException(new string('x', 10000)),
            new NativeDatabaseFaultException("native fault sentinel"), new NativeDatabaseFaultException(new string('x', 10000)) };
        foreach (int boundary in new[] { 4092, 4093, 4095, 4096 })
        {
            string message = new string('x', boundary) + char.ConvertFromUtf32(0x1F600) + new string('y', 8000);
            errors.Add(new InvalidOperationException(message)); errors.Add(new NativeDatabaseFaultException(message));
        }
        foreach (bool statusRoute in new[] { false, true })
            foreach (Exception error in errors)
            {
                var context = new ComponentCopyOnlyContext { CopyFailure = statusRoute ? null : error, StatusFailure = statusRoute ? error : null };
                var response = Run(statusRoute ? StatusRequest() : new ComponentCopyHttpRequest(ExecuteBody().ToString(Formatting.None)), context);
                bool fault = error is NativeDatabaseFaultException;
                string text = (string)response.Json["error"];
                Check((statusRoute ? context.StatusCalls == 1 && context.CopyCalls == 0 : context.CopyCalls == 1 && context.StatusCalls == 0),
                    "callback failure never retries or falls back to another endpoint");
                Check(!string.IsNullOrEmpty(text) && text.Length <= 4096 && WellFormedUtf16(text) && text.IndexOf('\uFFFD') < 0,
                    "callback failure text bounded at 4096 UTF16 without surrogate replacement");
                Check(response.Json["result"] == null, "thrown error does not fabricate a native receipt");
                Check(response.StatusCode == (statusRoute && (error is ArgumentException || error is JsonException) ? 400 : 500),
                      "copy dispatch exceptions always report 500; status argument/JSON exceptions are managed 400");
                if (fault)
                    Check(response.StatusCode == 500 && (string)response.Json["error_code"] == "native_database_fault" &&
                          (bool?)response.Json["native_fault_latched"] == true && (bool?)response.Json["retry_safe"] == false,
                          "thrown native fault takes dedicated catch before generic diagnostics");
                else
                    Check(response.Json["native_fault_latched"] == null && response.Json["error_code"] == null,
                        "ordinary callback failure never invents a native fault");
                if (statusRoute)
                    Check((bool?)response.Json["write_attempted"] == false && (bool?)response.Json["outcome_uncertain"] == false &&
                          (bool?)response.Json["retry_safe"] == false, "status callback error remains managed and never reports a write");
                else
                    Check(response.Json["write_attempted"] != null && response.Json["write_attempted"].Type == JTokenType.Null &&
                          (bool?)response.Json["outcome_uncertain"] == true && (bool?)response.Json["retry_safe"] == false,
                          "unexpected execute callback throw preserves unknown write outcome without retry");
            }
        foreach (Exception error in errors)
        {
            var context = new ComponentCopyOnlyContext { CopyFailure = error };
            var response = Run(new ComponentCopyHttpRequest(Valid), context);
            Check(context.CopyCalls == 1 && (bool?)response.Json["write_attempted"] == false &&
                  (bool?)response.Json["outcome_uncertain"] == false && (bool?)response.Json["retry_safe"] == false,
                  "prepare callback throw has no execution attempt or replay");
        }
        foreach (bool dryRun in new[] { true, false })
            foreach (bool nullReply in new[] { true, false })
            {
                var context = new ComponentCopyOnlyContext { Reply = nullReply ? null : new ParagonComponentCopyReply(200, true, null) };
                var response = Run(new ComponentCopyHttpRequest(dryRun ? Valid : ExecuteBody().ToString(Formatting.None)), context);
                Check(response.StatusCode == 500 && context.CopyCalls == 1 && context.StatusCalls == 0 && response.Json["result"] == null,
                    "missing native reply/result fails once without fabricated receipt");
                Check((bool?)response.Json["outcome_uncertain"] == !dryRun && (bool?)response.Json["retry_safe"] == false,
                    "missing native receipt preserves prepare versus execute uncertainty");
                Check(dryRun ? (bool?)response.Json["write_attempted"] == false : response.Json["write_attempted"] != null &&
                      response.Json["write_attempted"].Type == JTokenType.Null, "missing receipt never upgrades an uncertain execution to no-write");
            }
    }

    private static int Main()
    {
        try
        {
            ValidRequests(); InvalidCopyInputs(); StatusQueries(); ByteBudget(); MethodsAndCapability(); OpaqueRepliesAndFailures();
            if (failures.Count != 0)
            {
                foreach (string failure in failures) Console.Error.WriteLine(failure);
                return 1;
            }
            Console.WriteLine("PARAGON component copy HTTP production checks passed: " + checks);
            return 0;
        }
        catch (Exception error)
        {
            foreach (string failure in failures) Console.Error.WriteLine(failure);
            Console.Error.WriteLine(error.ToString());
            return 1;
        }
    }
}
