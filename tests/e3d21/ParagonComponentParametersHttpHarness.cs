using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Globalization;
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
internal sealed class ComponentParametersCountingStream : MemoryStream
{
    internal int BytesRead;
    internal ComponentParametersCountingStream(byte[] bytes) : base(bytes) { }
    public override Task<int> ReadAsync(byte[] buffer, int offset, int count,
        System.Threading.CancellationToken cancellationToken)
    {
        int read = Read(buffer, offset, count);
        BytesRead += read;
        return Task.FromResult(read);
    }
}

internal sealed class ComponentParametersHttpInnerRequest
{
    internal NameValueCollection QueryString;
    internal ComponentParametersCountingStream InputStream;
    internal Encoding ContentEncoding = Encoding.UTF8;
    internal string ContentType = "application/json";
    internal string HttpMethod;
    internal Uri Url;
    internal bool HasEntityBody;
    internal long ContentLength64;
    internal readonly NameValueCollection Headers = new NameValueCollection();
}

internal sealed class ComponentParametersHttpResponse
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

internal sealed class ComponentParametersHttpRequest
{
    internal readonly ComponentParametersHttpInnerRequest Request;
    internal readonly ComponentParametersHttpResponse Response = new ComponentParametersHttpResponse();
    internal ComponentParametersHttpRequest(string body = "", string method = "POST", NameValueCollection query = null,
        string path = "/api/v1/paragon/component-parameters")
    {
        byte[] bytes = Encoding.UTF8.GetBytes(body);
        Request = new ComponentParametersHttpInnerRequest
        {
            InputStream = new ComponentParametersCountingStream(bytes), QueryString = query ?? new NameValueCollection(),
            HttpMethod = method, Url = new Uri("http://offline.invalid" + path),
            HasEntityBody = bytes.Length != 0, ContentLength64 = bytes.Length
        };
    }
}

internal class ComponentParametersBaseContext
{
    internal int SetCalls, StatusCalls, OldCopyCalls, OldCopyStatusCalls, CommandStateCalls, ConnectedCalls;
    internal string Refno, RequestId, ExpectedSessionId, ExpectedBridgeBuild, ExpectedRevision, SessionId;
    internal double[] Parameters;
    internal bool DryRun;
    internal Exception SetFailure, StatusFailure;
    internal ParagonComponentParametersReply Reply = new ParagonComponentParametersReply(200, true,
        new Dictionary<string, object> { ["status"] = "prepared", ["opaque"] = "native-parameters" });
    internal object StatusResult = new { status = "accepted", opaque = "managed-status", native_fault_latched = (bool?)null };
    internal object CommandState() { CommandStateCalls++; return new { database_ready = true }; }
    internal bool Connected { get { ConnectedCalls++; return true; } }
    public ParagonComponentParametersReply SetParagonComponentParameters(string refno, IList<double> parameters, bool dryRun,
        string requestId, string expectedSessionId, string expectedBridgeBuild, string expectedRevision, string sessionId)
    {
        SetCalls++; Refno = refno; Parameters = parameters.ToArray(); DryRun = dryRun;
        RequestId = requestId; ExpectedSessionId = expectedSessionId; ExpectedBridgeBuild = expectedBridgeBuild;
        ExpectedRevision = expectedRevision; SessionId = sessionId;
        if (SetFailure != null) throw SetFailure;
        return Reply;
    }
    public object GetParagonComponentParametersStatus(string requestId, string expectedSessionId, string sessionId)
    {
        StatusCalls++; RequestId = requestId; ExpectedSessionId = expectedSessionId; SessionId = sessionId;
        if (StatusFailure != null) throw StatusFailure;
        return StatusResult;
    }
    public ParagonComponentCopyReply CopyParagonComponent(string sourceRefno, string parentRefno, string name, string recipe,
        bool dryRun, string requestId, string expectedSessionId, string expectedBridgeBuild, string expectedRevision, string sessionId)
    { OldCopyCalls++; throw new InvalidOperationException("Parameter paths must never invoke the old copy operation."); }
    public object GetParagonComponentCopyStatus(string requestId, string expectedSessionId, string sessionId)
    { OldCopyStatusCalls++; throw new InvalidOperationException("Parameter status must never use the old copy journal."); }
}

internal sealed class ParametersOnlyContext : ComponentParametersBaseContext, IE3d21ParagonComponentParametersContext { }
internal sealed class OldCopyOnlyContext : ComponentParametersBaseContext, IE3d21ParagonComponentCopyContext { }
internal sealed class ParametersAndCopyContext : ComponentParametersBaseContext,
    IE3d21ParagonComponentParametersContext, IE3d21ParagonComponentCopyContext { }

namespace E3d21AiBridge
{
    internal sealed class E3d21HttpAdapter
    {
        internal const string SessionId = "component-parameters-server-session";
        private const int MaximumJsonRequestBodyBytes = 1024 * 1024;
        private readonly string sessionId = SessionId;
        private readonly ComponentParametersBaseContext context;
        internal int FallbackDiagnosticsCalls;
        internal E3d21HttpAdapter(ComponentParametersBaseContext context) { this.context = context; }
        internal Task Invoke(ComponentParametersHttpRequest request) { return HandleAsync(request); }
        private object SafeCommandStateDiagnostics()
        { FallbackDiagnosticsCalls++; return new { forbidden_native_diagnostics = true }; }

        // PRODUCTION_HANDLER

        // PRODUCTION_HTTP_HELPERS
    }
}

internal static class ParagonComponentParametersHttpHarness
{
    private const string Route = "/api/v1/paragon/component-parameters";
    private const string StatusRoute = Route + "/status";
    private const string Feature = "paragon_component_parameters";
    private const string StatusFeature = "paragon_component_parameters_status";
    private const string RequestId = "baba1111-2222-3333-8444-555555555555";
    private const string ExpectedBuild = "caca1111-2222-3333-8444-555555555555";
    private const string Valid = "{\"refno\":\"=7/103\",\"parameters\":[1,2.5],\"parameter_bits\":[\"3ff0000000000000\",\"4004000000000000\"]}";
    private static readonly string Revision = new string('a', 64);
    private static readonly string[] ExecutionFields = { "request_id", "expected_session_id", "expected_bridge_build", "expected_revision" };
    private static readonly List<string> failures = new List<string>();
    private static int checks;
    private static void Check(bool condition, string message) { checks++; if (!condition) failures.Add(message); }
    private static string Body(object value) { return JsonConvert.SerializeObject(value); }
    private static string Bits(double value) => unchecked((ulong)BitConverter.DoubleToInt64Bits(value)).ToString("x16");
    private static double FromBits(ulong bits) => BitConverter.Int64BitsToDouble(unchecked((long)bits));
    private static JObject Wire(params double[] values)
    {
        return new JObject { ["refno"] = "=7/103", ["parameters"] = JArray.FromObject(values),
            ["parameter_bits"] = JArray.FromObject(values.Select(Bits)) };
    }
    private static JObject ExecuteBody()
    {
        var body = JObject.Parse(Valid); body["dry_run"] = false; body["request_id"] = RequestId;
        body["expected_session_id"] = "expected-session"; body["expected_bridge_build"] = ExpectedBuild;
        body["expected_revision"] = Revision; return body;
    }
    private static NameValueCollection Query(string requestId = RequestId, string expectedSession = E3d21HttpAdapter.SessionId)
        => new NameValueCollection { { "request_id", requestId }, { "expected_session_id", expectedSession } };
    private static ComponentParametersHttpRequest StatusRequest(string body = "", string method = "GET", NameValueCollection query = null)
        => new ComponentParametersHttpRequest(body, method, query ?? Query(), StatusRoute);
    private static ComponentParametersHttpResponse Run(ComponentParametersHttpRequest request, ComponentParametersBaseContext context, bool health = false)
    {
        var adapter = new E3d21HttpAdapter(context); adapter.Invoke(request).GetAwaiter().GetResult();
        Check(adapter.FallbackDiagnosticsCalls == 0, "parameter paths never use fallback diagnostics");
        Check(context.CommandStateCalls == (health ? 1 : 0) && context.ConnectedCalls == (health ? 1 : 0),
            "only explicit health calls command-state or connection getters");
        Check(context.OldCopyCalls == 0 && context.OldCopyStatusCalls == 0, "new parameter paths never call old copy/status");
        Check(request.Response.CloseCalls == 1, "response closes exactly once");
        Check(request.Response.ContentType == "application/json; charset=utf-8" &&
            request.Response.ContentLength64 == request.Response.OutputStream.Length, "production JSON headers bind wire length");
        JObject json = request.Response.Json;
        string where = request.Request.HttpMethod + " " + request.Request.Url.AbsolutePath + " HTTP " + request.Response.StatusCode;
        Check((string)json["version"] == "E3D21" && (string)json["session_id"] == E3d21HttpAdapter.SessionId,
            where + ": every status binds version and server-owned session");
        Check((string)json["bridge_build"] == typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
            where + ": every status binds the executing assembly MVID");
        Check((bool?)json["ok"] == (request.Response.StatusCode == 200), where + ": ok agrees with HTTP result");
        if (!health) Check(json["state"] == null && json["diagnostic_error"] == null, "no unrequested diagnostics after parameter outcomes");
        return request.Response;
    }
    private static ParametersOnlyContext Accept(string body, string refno = "=7/103", double[] expected = null,
        bool dryRun = true, string expectedSession = "expected-session", bool chunked = false)
    {
        var context = new ParametersOnlyContext(); var request = new ComponentParametersHttpRequest(body);
        if (chunked) { request.Request.ContentLength64 = -1; request.Request.Headers["Transfer-Encoding"] = "chunked"; }
        var response = Run(request, context); expected = expected ?? new[] { 1.0, 2.5 };
        Check(response.StatusCode == 200 && context.SetCalls == 1 && context.StatusCalls == 0, "valid wire request dispatches exactly one setter callback");
        Check(context.Refno == refno && context.DryRun == dryRun && context.SessionId == E3d21HttpAdapter.SessionId,
            "refno/mode/server-owned session forward without rewriting");
        Check(context.Parameters != null && context.Parameters.Select(Bits).SequenceEqual(expected.Select(Bits)),
            "ValidateWire forwards exact finite DOUBLE bits in caller order, including signed zero");
        Check(dryRun ? context.RequestId == null && context.ExpectedSessionId == null && context.ExpectedBridgeBuild == null && context.ExpectedRevision == null :
            context.RequestId == RequestId && context.ExpectedSessionId == expectedSession && context.ExpectedBridgeBuild == ExpectedBuild && context.ExpectedRevision == Revision,
            "prepare has no execution fields; execute forwards exact request/session/build/revision");
        Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(context.Reply.Result)), "native result stays opaque");
        Check(response.Json.Properties().Select(p => p.Name).OrderBy(n => n).SequenceEqual(
            new[] { "ok", "version", "session_id", "bridge_build", "result" }.OrderBy(n => n)), "success has exactly the standard five envelope fields");
        return context;
    }
    private static void NoWrite(JObject json, string label)
    {
        Check((bool?)json["write_attempted"] == false && (bool?)json["outcome_uncertain"] == false && (bool?)json["retry_safe"] == false,
            label + ": failure before write preserves known no-attempt and forbids automatic retry");
    }
    private static void Reject(string body, string label, NameValueCollection query = null)
    {
        var context = new ParametersOnlyContext(); var response = Run(new ComponentParametersHttpRequest(body, query: query), context);
        Check(response.StatusCode == 400 && context.SetCalls == 0 && context.StatusCalls == 0, label + ": rejected before dispatch");
        NoWrite(response.Json, label);
    }
    private static void RejectStatus(ComponentParametersHttpRequest request, string label)
    {
        var context = new ParametersOnlyContext(); var response = Run(request, context);
        Check(response.StatusCode == 400 && context.SetCalls == 0 && context.StatusCalls == 0, label + ": status rejected without callback");
        Check(request.Request.InputStream.BytesRead == 0, label + ": GET never parses body"); NoWrite(response.Json, label);
    }
    private static string[] InvalidIds() => new[] { "", "not-uuid", RequestId.ToUpperInvariant(), " " + RequestId, RequestId + " ",
        RequestId + "\n", "{" + RequestId + "}", RequestId.Replace("-", ""), new string('a', 257) };
    private static string[] InvalidSessions() => new[] { "", " ", "\t", "session\n", "session\0", "session\u007f", "private\ue000", "format\u200b",
        new string('s', 257), new string('s', 255) + char.ConvertFromUtf32(0x1F600) };

    private static void ValidRequests()
    {
        Accept(Valid);
        var body = JObject.Parse(Valid); body["dry_run"] = true; Accept(body.ToString(Formatting.None));
        foreach (string refno in new[] { "=1/0", "=1/1", "=2147483647/2147483647" })
        { body = JObject.Parse(Valid); body["refno"] = refno; Accept(body.ToString(Formatting.None), refno: refno); }
        Accept(Valid.Replace("refno", "refn\\u006f"));
        Accept(Valid.Replace("parameter_bits", "parameter_\\u0062its"));
        foreach (double[] values in new[] { new[] { 0.0 }, new[] { -1.0, 2.5, 125.5 },
            new[] { double.Epsilon, -double.Epsilon, double.MaxValue, -double.MaxValue, 2.2250738585072014e-308 },
            Enumerable.Range(0, 128).Select(i => (double)i).ToArray() })
            Accept(Wire(values).ToString(Formatting.None), expected: values);
        Accept(ExecuteBody().ToString(Formatting.None), dryRun: false);
        foreach (string session in new[] { " expected-session ", " 中文🌍 session ", new string('s', 256), new string('s', 254) + char.ConvertFromUtf32(0x1F600) })
        { body = ExecuteBody(); body["expected_session_id"] = session; Accept(body.ToString(Formatting.None), dryRun: false, expectedSession: session); }
    }

    private static void SignedZeroAndBits()
    {
        double negativeZero = FromBits(0x8000000000000000UL);
        foreach (string zero in new[] { "0", "-0", "0.0", "-0.0", "0e0", "-0e0" })
            foreach (string bits in new[] { "0000000000000000", "8000000000000000" })
                Accept("{\"refno\":\"=7/103\",\"parameters\":[" + zero + "],\"parameter_bits\":[\"" + bits + "\"]}",
                    expected: new[] { bits[0] == '8' ? negativeZero : 0.0 });
        var mixed = new[] { negativeZero, 0.0, negativeZero, 80.0, 125.5, 0.0 };
        Accept(Wire(mixed).ToString(Formatting.None), expected: mixed);
        foreach (double[] zeros in new[] { new[] { negativeZero, 0.0 }, new[] { 0.0, negativeZero } })
            Accept(Wire(zeros).ToString(Formatting.None), expected: zeros);
        Accept("{\"refno\":\"=7/103\",\"parameters\":[2.5,1],\"parameter_bits\":[\"4004000000000000\",\"3ff0000000000000\"]}",
            expected: new[] { 2.5, 1.0 });
        foreach (string parameters in new[] { "[null]", "[true]", "[false]", "[\"1\"]", "[[]]", "[{}]", "[1e309]", "[-1e309]", "[NaN]", "[Infinity]", "[-Infinity]" })
            Reject("{\"refno\":\"=7/103\",\"parameters\":" + parameters + ",\"parameter_bits\":[\"3ff0000000000000\"]}", "actual finite JSON numeric values");
        foreach (string value in new[] { "null", "true", "1", "\"3ff0000000000000\"", "{}", "[]", "[null]", "[true]", "[1]", "[[]]", "[{}]",
            "[\"\"]", "[\"3ff000000000000\"]", "[\"03ff0000000000000\"]", "[\"3FF0000000000000\"]", "[\"ggg0000000000000\"]",
            "[\" 3ff0000000000000\"]", "[\"3ff0000000000000 \"]", "[\"7ff0000000000000\"]", "[\"fff0000000000000\"]", "[\"7ff8000000000000\"]",
            "[\"7ff0000000000001\"]", "[\"fff8000000000001\"]" })
            Reject("{\"refno\":\"=7/103\",\"parameters\":[1],\"parameter_bits\":" + value + "}", "exact finite lowercase hex64 bit list");
        foreach (string pair in new[] {
            "\"parameters\":[1],\"parameter_bits\":[\"4000000000000000\"]",
            "\"parameters\":[0],\"parameter_bits\":[\"0000000000000001\"]",
            "\"parameters\":[5e-324],\"parameter_bits\":[\"0000000000000000\"]",
            "\"parameters\":[1,2.5],\"parameter_bits\":[\"4004000000000000\",\"3ff0000000000000\"]",
            "\"parameters\":[1,2.5],\"parameter_bits\":[\"3ff0000000000000\"]",
            "\"parameters\":[1],\"parameter_bits\":[\"3ff0000000000000\",\"4004000000000000\"]" })
            Reject("{\"refno\":\"=7/103\"," + pair + "}", "count/order/nonzero bit mismatch is rejected");
        var numbers = new List<double> { 0.0, 1.0 };
        var bitsList = new List<string> { "8000000000000000", "3ff0000000000000" };
        var captured = ParagonComponentParametersRequest.ValidateWire("=7/103", numbers, bitsList, true, null, null, null, null);
        numbers[1] = 999.0; bitsList[0] = "0000000000000000";
        Check(captured.Parameters.Select(Bits).SequenceEqual(new[] { "8000000000000000", "3ff0000000000000" }) &&
            captured.ParameterBits.SequenceEqual(new[] { "8000000000000000", "3ff0000000000000" }),
            "managed request owns private copies of exact binary64 intent after caller lists mutate");
    }

    private static void InvalidRequests()
    {
        foreach (string body in new[] { "", " ", "null", "true", "false", "1", "\"text\"", "[]", "[{}]", "{", "{} {}", Valid + " false", "{}" })
            Reject(body, "malformed/nonobject/missing required fields");
        foreach (string field in new[] { "refno", "parameters", "parameter_bits" })
        { var body = JObject.Parse(Valid); body.Remove(field); Reject(body.ToString(Formatting.None), "required wire field " + field); }
        foreach (string field in new[] { "Refno", "Parameters", "source_refno", "parent_refno", "name", "recipe", "attribute", "attributes", "index", "append",
            "source", "command", "execute", "version", "session_id", "bridge_build" })
        { var body = JObject.Parse(Valid); body[field] = true; Reject(body.ToString(Formatting.None), "unknown exact-case field " + field); }
        foreach (string field in new[] { "refno", "parameters", "parameter_bits", "dry_run", "request_id", "expected_session_id", "expected_bridge_build", "expected_revision" })
        {
            var body = ExecuteBody(); string suffix = "," + Body(field) + ":" + body[field].ToString(Formatting.None) + "}";
            Reject(body.ToString(Formatting.None).TrimEnd('}') + suffix, "duplicate wire field " + field);
        }
        Reject(Valid.TrimEnd('}') + ",\"parameter_\\u0062its\":[\"3ff0000000000000\",\"4004000000000000\"]}", "duplicate decoded bit-list field");
        foreach (string value in new[] { "null", "true", "false", "1", "1.0", "[]", "{}" })
        { var body = JObject.Parse(Valid); body["refno"] = JToken.Parse(value); Reject(body.ToString(Formatting.None), "refno is a true string"); }
        foreach (string value in new[] { "null", "true", "false", "0", "1.0", "\"1\"", "{}", "[]" })
        { var body = JObject.Parse(Valid); body["parameters"] = JToken.Parse(value); Reject(body.ToString(Formatting.None), "parameters must be nonempty numeric array"); }
        var oversized = Wire(Enumerable.Range(0, 129).Select(i => (double)i).ToArray());
        Reject(oversized.ToString(Formatting.None), "129 parameter values exceed the explicit item budget");
        foreach (string refno in new[] { "", "CE", "WORLD", "/SCOM", "=0/0", "=0/1", "=-1/1", "=1/-1", "=+1/1", "=1/+1", "=01/1", "=1/00",
            " =1/1", "=1/1 ", "=1/1\n", "=1/1\0", "=1/*", "=1/1;SAVEWORK", "=1/１", "=2147483648/1", "=1/2147483648" })
        { var body = JObject.Parse(Valid); body["refno"] = refno; Reject(body.ToString(Formatting.None), "canonical Int32 target/no trim/leading zeros"); }
        foreach (string value in new[] { "null", "1", "0", "1.0", "\"true\"", "[]", "{}" })
        { var body = JObject.Parse(Valid); body["dry_run"] = JToken.Parse(value); Reject(body.ToString(Formatting.None), "dry_run strict boolean"); }
        foreach (string field in ExecutionFields)
        {
            var body = JObject.Parse(Valid); body[field] = JValue.CreateNull(); Reject(body.ToString(Formatting.None), "prepare rejects execution key presence even null");
            body = JObject.Parse(Valid); body["dry_run"] = true; body[field] = ExecuteBody()[field];
            Reject(body.ToString(Formatting.None), "prepare rejects any execution token");
            body = ExecuteBody(); body.Remove(field); Reject(body.ToString(Formatting.None), "execute requires " + field);
            foreach (string value in new[] { "null", "true", "1", "1.0", "[]", "{}" })
            { body = ExecuteBody(); body[field] = JToken.Parse(value); Reject(body.ToString(Formatting.None), "execute requires a true string " + field); }
        }
        foreach (string field in new[] { "request_id", "expected_bridge_build" })
            foreach (string id in InvalidIds())
            { var body = ExecuteBody(); body[field] = id; Reject(body.ToString(Formatting.None), "exact lowercase UUID D " + field); }
        foreach (string session in InvalidSessions())
        { var body = ExecuteBody(); body["expected_session_id"] = session; Reject(body.ToString(Formatting.None), "bounded printable server session"); }
        foreach (string revision in new[] { "", new string('a', 63), new string('a', 65), new string('A', 64), new string('g', 64), " " + new string('a', 63), new string('a', 63) + "\n" })
        { var body = ExecuteBody(); body["expected_revision"] = revision; Reject(body.ToString(Formatting.None), "exact lowercase SHA256 revision"); }
        foreach (string field in new[] { "refno", "parameters", "parameter_bits", "unexpected", null })
        {
            var request = new ComponentParametersHttpRequest("not JSON", query: new NameValueCollection { { field, "x" } });
            var context = new ParametersOnlyContext(); var response = Run(request, context);
            Check(response.StatusCode == 400 && context.SetCalls == 0 && request.Request.InputStream.BytesRead == 0, "POST query rejected before parse or dispatch");
        }
    }

    private static void StatusQueries()
    {
        foreach (string state in new[] { "accepted", "running", "completed", "unknown_or_expired" })
        {
            var context = new ParametersOnlyContext { StatusResult = new { state = state, request_id = RequestId,
                write_attempted = (bool?)null, completion = (object)null, future_unknown = new[] { 1, 2, 3 } } };
            var response = Run(StatusRequest(), context);
            Check(response.StatusCode == 200 && context.StatusCalls == 1 && context.SetCalls == 0, "status is one managed-only callback");
            Check(context.RequestId == RequestId && context.ExpectedSessionId == E3d21HttpAdapter.SessionId && context.SessionId == E3d21HttpAdapter.SessionId,
                "status forwards exact identity and server-owned session");
            Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(context.StatusResult)), state + ": managed status fields stay opaque");
        }
        var history = new ParametersOnlyContext { StatusResult = new { status = "completed", receipt = new {
            ok = false, error = "historical native fault", error_code = "native_database_fault", native_fault_latched = true,
            result = new { progress = new { known_target_refno = "=7/103", parameter_write_attempted = true,
                parameter_write_returned = false, outcome_uncertain = true, native_fault_latched = true, retry_safe = false } } } } };
        var historical = Run(StatusRequest(), history);
        Check(historical.StatusCode == 200 && history.StatusCalls == 1 && history.SetCalls == 0 && historical.Json["error_code"] == null &&
            JToken.DeepEquals(historical.Json["result"], JToken.FromObject(history.StatusResult)),
            "managed status retains historical fault data without promoting it to a new HTTP fault or invoking a setter");
        foreach (string session in new[] { "expected-session", " " + E3d21HttpAdapter.SessionId + " ", " 中文🌍 session ", new string('s', 256) })
        {
            var context = new ParametersOnlyContext();
            var response = Run(StatusRequest(query: Query(expectedSession: session)), context);
            Check(response.StatusCode == 409 && context.StatusCalls == 0 && context.SetCalls == 0,
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
        foreach (string field in new[] { "Request_id", "Expected_session_id", "refno", "parameters", "parameter_bits", "dry_run", "unexpected", null })
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
                var request = new ComponentParametersHttpRequest(exact + new string(' ', 65536));
                if (chunked) { request.Request.ContentLength64 = -1; request.Request.Headers["Transfer-Encoding"] = "chunked"; }
                var context = new ParametersOnlyContext();
                var response = Run(request, context);
                Check(response.StatusCode == 400 && context.SetCalls == 0, "8193+ ordinary/BOM/chunked UTF8 bytes reject before setter");
                Check(request.Request.InputStream.BytesRead <= budget + 1 && request.Request.InputStream.BytesRead < request.Request.InputStream.Length,
                    "bounded body reader stops at first over-budget byte");
                Reject(exact + " ", "8193 UTF8 bytes including BOM over budget");
            }
        var unicode = ExecuteBody(); unicode["expected_session_id"] = "世界🌍";
        string unicodeBody = unicode.ToString(Formatting.None);
        Accept(unicodeBody + new string(' ', budget - Encoding.UTF8.GetByteCount(unicodeBody)), dryRun: false, expectedSession: "世界🌍", chunked: true);
        var maximum = Enumerable.Repeat(double.MaxValue, 128).ToArray();
        string maximumBody = Wire(maximum).ToString(Formatting.None);
        Accept(maximumBody + new string(' ', budget - Encoding.UTF8.GetByteCount(maximumBody)), expected: maximum, chunked: true);
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
        foreach (string status in new[] { "prepared", "blocked", "updated", "unchanged", "failed", "rejected", "duplicate" })
            foreach (bool ok in new[] { true, false })
            {
                var result = new Dictionary<string, object> { ["status"] = status, ["write_attempted"] = (object)null,
                    ["future_unknown"] = new { exact = true, identity = (string)null }, ["rows"] = new[] { 1, 2, 3 } };
                var context = new ParametersOnlyContext { Reply = new ParagonComponentParametersReply(ok ? 200 : 409, ok, result, ok ? null : "native reply sentinel") };
                var response = Run(new ComponentParametersHttpRequest(Valid), context);
                Check(response.StatusCode == (ok ? 200 : 409) && context.SetCalls == 1, "reader reply controls native HTTP outcome without retry");
                Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(result)), status + ": opaque native evidence retained verbatim");
                Check(ok ? response.Json["error"] == null : (string)response.Json["error"] == "native reply sentinel", "native error accompanies failed result");
                Check(response.Json["write_attempted"] == null && response.Json["outcome_uncertain"] == null,
                    "returned native progress remains in its opaque result; HTTP does not fabricate top-level attempts");
            }
        var faultResult = new Dictionary<string, object> { ["operation"] = "paragon_component_parameters_set", ["status"] = "failed",
            ["progress"] = new Dictionary<string, object> { ["known_target_refno"] = "=7/103", ["known_target_refno_verified"] = true,
                ["parameter_write_attempted"] = true, ["parameter_write_returned"] = true, ["parameters_equal_request"] = null,
                ["readback_complete"] = false, ["context_unchanged"] = null, ["no_change"] = false,
                ["outcome_uncertain"] = true, ["native_fault_latched"] = true, ["retry_safe"] = false },
            ["after"] = (object)null, ["opaque"] = new[] { "journal", "must survive" } };
        var nativeFault = new ParametersOnlyContext { Reply = new ParagonComponentParametersReply(500, false, faultResult, "latched reply", true) };
        var faultResponse = Run(new ComponentParametersHttpRequest(Valid), nativeFault);
        Check(faultResponse.StatusCode == 500 && JToken.DeepEquals(faultResponse.Json["result"], JToken.FromObject(faultResult)),
            "native fault reply preserves all eleven parameter progress fields and partial journal evidence");
        Check((string)faultResponse.Json["error_code"] == "native_database_fault" && (bool?)faultResponse.Json["native_fault_latched"] == true &&
              (bool?)faultResponse.Json["retry_safe"] == false, "native fault reply retains dedicated flags");
        foreach (bool fault in new[] { false, true })
        {
            string longError = new string('x', 4092) + char.ConvertFromUtf32(0x1F600) + new string('y', 8000);
            var context = new ParametersOnlyContext { Reply = new ParagonComponentParametersReply(500, false, faultResult, longError, fault) };
            var response = Run(new ComponentParametersHttpRequest(Valid), context);
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
                var context = new ParametersOnlyContext { SetFailure = statusRoute ? null : error, StatusFailure = statusRoute ? error : null };
                var response = Run(statusRoute ? StatusRequest() : new ComponentParametersHttpRequest(ExecuteBody().ToString(Formatting.None)), context);
                bool fault = error is NativeDatabaseFaultException;
                string text = (string)response.Json["error"];
                Check((statusRoute ? context.StatusCalls == 1 && context.SetCalls == 0 : context.SetCalls == 1 && context.StatusCalls == 0),
                    "callback failure never retries or falls back to another endpoint");
                Check(!string.IsNullOrEmpty(text) && text.Length <= 4096 && WellFormedUtf16(text) && text.IndexOf('\uFFFD') < 0,
                    "callback failure text bounded at 4096 UTF16 without surrogate replacement");
                Check(response.Json["result"] == null, "thrown error does not fabricate a native receipt");
                Check(response.StatusCode == (statusRoute && (error is ArgumentException || error is JsonException) ? 400 : 500),
                      "setter dispatch exceptions always report 500; status argument/JSON exceptions are managed 400");
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
            var context = new ParametersOnlyContext { SetFailure = error };
            var response = Run(new ComponentParametersHttpRequest(Valid), context);
            Check(context.SetCalls == 1 && (bool?)response.Json["write_attempted"] == false &&
                  (bool?)response.Json["outcome_uncertain"] == false && (bool?)response.Json["retry_safe"] == false,
                  "prepare callback throw has no execution attempt or replay");
        }
        foreach (bool dryRun in new[] { true, false })
            foreach (bool nullReply in new[] { true, false })
            {
                var context = new ParametersOnlyContext { Reply = nullReply ? null : new ParagonComponentParametersReply(200, true, null) };
                var response = Run(new ComponentParametersHttpRequest(dryRun ? Valid : ExecuteBody().ToString(Formatting.None)), context);
                Check(response.StatusCode == 500 && context.SetCalls == 1 && context.StatusCalls == 0 && response.Json["result"] == null,
                    "missing native reply/result fails once without fabricated receipt");
                Check((bool?)response.Json["outcome_uncertain"] == !dryRun && (bool?)response.Json["retry_safe"] == false,
                    "missing native receipt preserves prepare versus execute uncertainty");
                Check(dryRun ? (bool?)response.Json["write_attempted"] == false : response.Json["write_attempted"] != null &&
                      response.Json["write_attempted"].Type == JTokenType.Null, "missing receipt never upgrades an uncertain execution to no-write");
            }
    }


    private static ComponentParametersBaseContext[] Contexts() => new ComponentParametersBaseContext[] {
        new ComponentParametersBaseContext(), new ParametersOnlyContext(), new OldCopyOnlyContext(), new ParametersAndCopyContext() };
    private static void MethodsAndCapability()
    {
        foreach (string route in new[] { Route, StatusRoute })
        {
            bool status = route == StatusRoute;
            foreach (ComponentParametersBaseContext context in Contexts())
            {
                var request = new ComponentParametersHttpRequest(status ? "" : Valid, status ? "GET" : "POST", status ? Query() : null, route);
                var response = Run(request, context); bool implemented = context is IE3d21ParagonComponentParametersContext;
                Check(response.StatusCode == (implemented ? 200 : 404), "parameter capability is independent of the old copy optional interface");
                Check(implemented ? (status ? context.StatusCalls == 1 && context.SetCalls == 0 : context.SetCalls == 1 && context.StatusCalls == 0) :
                    context.SetCalls == 0 && context.StatusCalls == 0 && request.Request.InputStream.BytesRead == 0,
                    "optional feature absence never parses body or falls back to old copy");
            }
            foreach (string method in new[] { "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS" }.Where(m => m != (status ? "GET" : "POST")))
                foreach (ComponentParametersBaseContext context in Contexts())
                {
                    var request = new ComponentParametersHttpRequest("not JSON", method, Query(), route);
                    var response = Run(request, context);
                    Check(response.StatusCode == 405 && response.Headers["Allow"] == (status ? "GET" : "POST"), "wrong method has 405 and exact Allow independent of optional interface");
                    Check(context.SetCalls == 0 && context.StatusCalls == 0 && request.Request.InputStream.BytesRead == 0, "wrong method never parses or dispatches");
                }
        }
        foreach (ComponentParametersBaseContext context in Contexts())
        {
            var request = new ComponentParametersHttpRequest(method: "GET", path: "/health");
            var response = Run(request, context, health: true);
            var features = ((JArray)response.Json["api_features"]).Values<string>().ToList();
            bool setter = context is IE3d21ParagonComponentParametersContext, copy = context is IE3d21ParagonComponentCopyContext;
            Check(features.Contains(Feature) == setter && features.Contains(StatusFeature) == setter, "new setter/status features require their own optional interface");
            Check(features.Contains("paragon_component_copy") == copy && features.Contains("paragon_component_copy_status") == copy,
                "new parameter interface does not fabricate old copy capability");
            Check(features.Count == features.Distinct(StringComparer.Ordinal).Count(), "health features remain unique");
            Check(context.SetCalls == 0 && context.StatusCalls == 0, "health does not probe setter/status");
        }
    }
    private static int Main()
    {
        try
        {
            ValidRequests(); SignedZeroAndBits(); InvalidRequests(); StatusQueries(); ByteBudget(); MethodsAndCapability(); OpaqueRepliesAndFailures();
            if (failures.Count != 0)
            {
                foreach (string failure in failures) Console.Error.WriteLine(failure);
                return 1;
            }
            Console.WriteLine("PARAGON component parameters HTTP production checks passed: " + checks);
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
