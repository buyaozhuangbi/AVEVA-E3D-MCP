using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using E3d21AiBridge;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

// Only HTTP memory carriers and recording optional capabilities are synthetic.
// Production setup, health, route, fallback/catches, parser and validator run.
internal sealed class ChildSchemaCountingStream : MemoryStream
{
    internal int BytesRead;
    internal ChildSchemaCountingStream(byte[] bytes) : base(bytes) { }
    public override Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken)
    {
        int result = Read(buffer, offset, count);
        BytesRead += result;
        return Task.FromResult(result);
    }
}

internal sealed class ChildSchemaHttpInnerRequest
{
    internal NameValueCollection QueryString;
    internal ChildSchemaCountingStream InputStream;
    internal Encoding ContentEncoding = Encoding.UTF8;
    internal string ContentType = "application/json";
    internal string HttpMethod;
    internal Uri Url;
    internal bool HasEntityBody;
    internal long ContentLength64;
    internal readonly NameValueCollection Headers = new NameValueCollection();
}

internal sealed class ChildSchemaHttpResponse
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

internal sealed class ChildSchemaHttpRequest
{
    internal readonly ChildSchemaHttpInnerRequest Request;
    internal readonly ChildSchemaHttpResponse Response = new ChildSchemaHttpResponse();
    internal ChildSchemaHttpRequest(string json = "", string method = "POST",
        string path = "/api/v1/equipment/child-schema", NameValueCollection query = null)
    {
        byte[] bytes = Encoding.UTF8.GetBytes(json);
        Request = new ChildSchemaHttpInnerRequest
        {
            InputStream = new ChildSchemaCountingStream(bytes), QueryString = query ?? new NameValueCollection(),
            HttpMethod = method, Url = new Uri("http://offline.invalid" + path),
            HasEntityBody = bytes.Length != 0, ContentLength64 = bytes.Length
        };
    }
}

internal class ChildSchemaBaseContext
{
    internal int SchemaCalls, ConnectivityCalls, CommandStateCalls, ConnectedCalls;
    internal string SessionId, ParentRefno, ElementType, ReadTargetRefno;
    internal List<string> Attributes;
    internal int Offset, Limit;
    internal Exception Failure;
    internal object Result = new { available = true, success = true, complete = true, marker = "native-child-schema" };
    internal object CommandState() { CommandStateCalls++; return new { database_ready = true }; }
    internal bool Connected { get { ConnectedCalls++; return true; } }
    public object ReadConnectivitySnapshot(ConnectivitySnapshotRequest request, string sessionId, string bridgeBuild)
    {
        ConnectivityCalls++;
        throw new InvalidOperationException("Child-schema HTTP tests must not invoke connectivity reads.");
    }
    protected object SchemaRead(string parentRefno, string elementType, string readTargetRefno,
        IList<string> attributes, int offset, int limit, string sessionId)
    {
        SchemaCalls++;
        ParentRefno = parentRefno; ElementType = elementType; ReadTargetRefno = readTargetRefno;
        Attributes = attributes == null ? null : attributes.ToList();
        Offset = offset; Limit = limit; SessionId = sessionId;
        if (Failure != null) throw Failure;
        return Result;
    }
}

internal class ChildSchemaOnlyContext : ChildSchemaBaseContext, IE3d21EquipmentChildSchemaContext
{
    public object ReadEquipmentChildSchema(string parentRefno, string elementType, string readTargetRefno,
        IList<string> attributes, int offset, int limit, string sessionId)
    { return SchemaRead(parentRefno, elementType, readTargetRefno, attributes, offset, limit, sessionId); }
}

internal sealed class ConnectivityOnlyChildContext : ChildSchemaBaseContext, IE3d21ConnectivitySnapshotContext { }
internal sealed class BothChildContext : ChildSchemaOnlyContext, IE3d21ConnectivitySnapshotContext { }

namespace E3d21AiBridge
{
    internal sealed class E3d21HttpAdapter
    {
        internal const string SessionId = "equipment-child-schema-session";
        private const int MaximumJsonRequestBodyBytes = 1024 * 1024;
        private readonly string sessionId = SessionId;
        private readonly ChildSchemaBaseContext context;
        internal int FallbackDiagnosticsCalls;
        internal E3d21HttpAdapter(ChildSchemaBaseContext context) { this.context = context; }
        internal Task Invoke(ChildSchemaHttpRequest request) { return HandleAsync(request); }
        private object SafeCommandStateDiagnostics()
        {
            FallbackDiagnosticsCalls++;
            return new { forbidden_native_diagnostics = true };
        }

        // PRODUCTION_HANDLER

        // PRODUCTION_HTTP_HELPERS
    }
}

internal static class EquipmentChildSchemaHttpHarness
{
    private const string Path = "/api/v1/equipment/child-schema";
    private const string Valid = "{\"parent_refno\":\"/EQUIPMENT\",\"element_type\":\"NOZZ\"}";
    private const string Feature = "equipment_child_schema";
    private static int checks;
    private static readonly List<string> failures = new List<string>();
    private static string Body(object value) { return JsonConvert.SerializeObject(value); }

    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) failures.Add(message);
    }

    private static ChildSchemaHttpResponse Run(ChildSchemaHttpRequest request, ChildSchemaBaseContext context, bool health = false)
    {
        var adapter = new E3d21HttpAdapter(context);
        adapter.Invoke(request).GetAwaiter().GetResult();
        Check(adapter.FallbackDiagnosticsCalls == 0, "child-schema route must not run fallback diagnostics");
        Check(context.CommandStateCalls == (health ? 1 : 0) && context.ConnectedCalls == (health ? 1 : 0),
            "only /health may observe command or connection diagnostics");
        Check(context.ConnectivityCalls == 0, "child-schema and health never invoke connectivity reads");
        Check(request.Response.CloseCalls == 1, "HTTP response closes exactly once");
        Check(request.Response.ContentType == "application/json; charset=utf-8" &&
            request.Response.ContentLength64 == request.Response.OutputStream.Length,
            "wire metadata describes serialized production JSON");
        JObject json = request.Response.Json;
        Check((string)json["version"] == "E3D21" && (string)json["session_id"] == E3d21HttpAdapter.SessionId,
            "every child-schema response binds version and adapter session");
        Check((string)json["bridge_build"] == typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
            request.Request.HttpMethod + " " + request.Request.Url.AbsolutePath + " HTTP " + request.Response.StatusCode +
            ": response binds the executing assembly MVID");
        if (!health)
            Check(json["state"] == null && json["diagnostic_error"] == null,
                "child-schema responses omit native diagnostic observations");
        return request.Response;
    }

    private static ChildSchemaHttpRequest Schema(string body = Valid, string method = "POST", NameValueCollection query = null)
    { return new ChildSchemaHttpRequest(body, method, Path, query); }

    private static ChildSchemaOnlyContext Accept(string body, string parent, string type, string target = null,
        int offset = 0, int limit = 50, params string[] attributes)
    {
        var context = new ChildSchemaOnlyContext();
        var response = Run(Schema(body), context);
        Check(response.StatusCode == 200 && (bool?)response.Json["ok"] == true, "valid request returns HTTP 200");
        Check(context.SchemaCalls == 1 && context.ParentRefno == parent && context.ElementType == type &&
            context.ReadTargetRefno == target && context.Offset == offset && context.Limit == limit &&
            context.Attributes != null && context.Attributes.SequenceEqual(attributes),
            "validated, normalized schema arguments forward exactly once and preserve attribute order");
        Check(context.SessionId == E3d21HttpAdapter.SessionId, "native read receives exact HTTP session identity");
        Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(context.Result)), "native result retained verbatim");
        return context;
    }

    private static void Reject(string body, string label, NameValueCollection query = null)
    {
        var context = new ChildSchemaOnlyContext();
        var request = Schema(body, query: query);
        var response = Run(request, context);
        Check(response.StatusCode == 400 && (bool?)response.Json["ok"] == false, label + ": returns HTTP 400");
        Check(context.SchemaCalls == 0 && response.Json["result"] == null, label + ": rejects before native read");
        Check(response.Json["error"] != null && ((string)response.Json["error"]).Length <= EquipmentChildSchemaRequest.MaximumTextLength,
            label + ": error text bounded");
        if (query != null) Check(request.Request.InputStream.BytesRead == 0, label + ": rejects query before body read");
    }

    private static void ValidAndPartialResults()
    {
        Accept(Valid, "/EQUIPMENT", "NOZZ");
        Accept(Body(new { parent_refno = " /Mixed/Name ", element_type = " nozz ", read_target_refno = (string)null }),
            "/Mixed/Name", "NOZZ");
        Accept(Body(new { parent_refno = "=0003/00000", element_type = " :u_type2 ", read_target_refno = " =0003/00015 ",
            attributes = new[] { " name ", ":u_attr" }, offset = 100000, limit = 200 }),
            "=3/0", ":U_TYPE2", "=3/15", 100000, 200, "NAME", ":U_ATTR");
        Accept(Body(new { parent_refno = "/設備", element_type = "BOX", attributes = new string[0], offset = 0, limit = 1 }),
            "/設備", "BOX", limit: 1);
        string[] maximum = Enumerable.Range(0, 20).Select(value => "A" + value).ToArray();
        Accept(Body(new { parent_refno = "/E", element_type = new string('a', 64), attributes = maximum }),
            "/E", new string('A', 64), attributes: maximum);

        object partial = new
        {
            operation = "equipment_child_schema", available = true, success = false, complete = false,
            parent = new { identity = (object)null, unknown_parent = true },
            child_type = new { type = "NOZZ", native_allowed = (bool?)null },
            instance_attributes = new { status = "unknown", read_target = (object)null, attributes = new object[0] },
            read_errors = new[] { new { code = "context_after_unavailable", message = (string)null } },
            context = new { before = new { session_id = E3d21HttpAdapter.SessionId }, after = (object)null,
                same_project_instance = (bool?)null, boundary_agreement = (bool?)null },
            future_unknown = new { keep = "opaque native evidence", array = new object[] { null, false, 3 } }
        };
        var context = new ChildSchemaOnlyContext { Result = partial };
        var response = Run(Schema(), context);
        Check(response.StatusCode == 200 && (bool?)response.Json["ok"] == true && context.SchemaCalls == 1,
            "partial native read remains an available HTTP success envelope");
        Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(partial)),
            "partial, nullable and future unknown native evidence is preserved exactly");
    }

    private static void RejectBeforeNative()
    {
        foreach (string body in new[] {
            "", " ", "null", "true", "[]", "[{}]", "{", "{} {}", "{} false", "{}",
            "{\"parent_refno\":\"/E\"}", "{\"element_type\":\"NOZZ\"}",
            "{\"parent_refno\":\"/E\",\"element_type\":\"NOZZ\",\"unknown\":true}",
            "{\"parent_refno\":\"/E\",\"parent_refno\":\"/OTHER\",\"element_type\":\"NOZZ\"}",
            "{\"parent_refno\":\"/E\",\"element_type\":\"NOZZ\",\"element_type\":\"BOX\"}",
            "{\"parent_refno\":\"/E\",\"element_type\":\"NOZZ\",\"attributes\":[],\"attributes\":[]}",
            "{\"parent_refno\":\"/E\",\"element_type\":\"NOZZ\",\"read_target_refno\":null,\"read_target_refno\":null}",
            "{\"parent_refno\":\"/E\",\"element_type\":\"NOZZ\",\"offset\":0,\"offset\":1}",
            "{\"parent_refno\":\"/E\",\"element_type\":\"NOZZ\",\"limit\":1,\"limit\":2}",
            "{\"parent_refno\":\"/E\",\"element_type\":\"NOZZ\",\"nested\":{\"x\":1,\"x\":2}}",
            "{\"Parent_refno\":\"/E\",\"element_type\":\"NOZZ\"}"
        }) Reject(body, "malformed, duplicate, unknown or missing JSON input");

        foreach (string field in new[] { "parent_refno", "element_type", "read_target_refno" })
        {
            foreach (object value in new object[] { "", " ", "CE", "*", "/E*", "/E?", "/E X", "/E\nX", 1, true, new object[0], new { x = 1 } })
            {
                // CE is a valid SDK type name; all explicit-reference fields reject it.
                if (field == "element_type" && (object.Equals(value, "CE"))) continue;
                var body = JObject.Parse(Valid);
                body[field] = JToken.FromObject(value);
                Reject(body.ToString(Formatting.None), "invalid " + field);
            }
        }
        foreach (string field in new[] { "parent_refno", "element_type" })
        {
            var body = JObject.Parse(Valid); body[field] = JValue.CreateNull();
            Reject(body.ToString(Formatting.None), "required field is null: " + field);
        }
        foreach (string reference in new[] { "/", "=0/1", "=-1/2", "=1/-1", "=1/2/3", "=2147483648/1", "=1/2147483648", "1/2", "=1/+2", new string('/', 257) })
        {
            foreach (string field in new[] { "parent_refno", "read_target_refno" })
            {
                var body = JObject.Parse(Valid); body[field] = reference;
                Reject(body.ToString(Formatting.None), "invalid explicit reference: " + field);
            }
        }
        foreach (string type in new[] { "NÖZZ", "NOZZ/BOX", "NOZZ.BOX", "NOZZ-BOX", new string('A', 65) })
            Reject(Body(new { parent_refno = "/E", element_type = type }), "invalid SDK type name");

        foreach (object attributes in new object[] { null, true, 1, "NAME", new { name = "NAME" },
            new object[] { null }, new object[] { true }, new object[] { 1 }, new object[] { new object[0] },
            new[] { "" }, new[] { "A B" }, new[] { "NAME", " name " }, new[] { "NÖME" },
            new[] { new string('A', 65) }, Enumerable.Range(0, 21).Select(value => "A" + value).ToArray() })
            Reject(Body(new { parent_refno = "/E", element_type = "NOZZ", attributes }), "invalid attributes");

        foreach (string field in new[] { "offset", "limit" })
        {
            foreach (JToken value in new JToken[] { JValue.CreateNull(), new JValue(true), new JValue(false),
                new JValue("1"), new JValue(1.0), JToken.Parse("1e0"), JToken.Parse("2147483648"),
                JToken.Parse("-2147483649"), JToken.Parse("999999999999999999999999999999"), new JArray(), new JObject() })
            {
                var body = JObject.Parse(Valid); body[field] = value;
                Reject(body.ToString(Formatting.None), "non-Int32 input: " + field);
            }
        }
        foreach (int offset in new[] { -1, 100001 })
            Reject(Body(new { parent_refno = "/E", element_type = "NOZZ", offset }), "offset outside range");
        foreach (int limit in new[] { -1, 0, 201 })
            Reject(Body(new { parent_refno = "/E", element_type = "NOZZ", limit }), "limit outside range");
        foreach (NameValueCollection query in new[] {
            new NameValueCollection { { "unknown", "1" } },
            new NameValueCollection { { "offset", "0" }, { "offset", "1" } },
            new NameValueCollection { { "parent_refno", "/E" } },
            new NameValueCollection { { null, "orphan" } }
        }) Reject("not JSON", "all query parameters rejected", query);
    }

    private static void BodyBudget()
    {
        const int budget = 65536;
        foreach (string body in new[] { Valid, Body(new { parent_refno = "/設備😀", element_type = "NOZZ" }) })
        {
            string exact = body + new string(' ', budget - Encoding.UTF8.GetByteCount(body));
            foreach (bool chunked in new[] { false, true })
            {
                var accepted = new ChildSchemaOnlyContext();
                var acceptedRequest = Schema(exact);
                if (chunked) { acceptedRequest.Request.ContentLength64 = -1; acceptedRequest.Request.Headers["Transfer-Encoding"] = "chunked"; }
                var acceptedResponse = Run(acceptedRequest, accepted);
                Check(acceptedResponse.StatusCode == 200 && accepted.SchemaCalls == 1 && acceptedRequest.Request.InputStream.BytesRead == budget,
                    "exact 65536 UTF8 bytes accepted with fixed or chunked transport");
                var rejected = new ChildSchemaOnlyContext();
                var rejectedRequest = Schema(exact + new string(' ', budget));
                if (chunked) { rejectedRequest.Request.ContentLength64 = -1; rejectedRequest.Request.Headers["Transfer-Encoding"] = "chunked"; }
                var rejectedResponse = Run(rejectedRequest, rejected);
                Check(rejectedResponse.StatusCode == 400 && rejected.SchemaCalls == 0,
                    "oversized UTF8 body rejected before native read");
                Check(rejectedRequest.Request.InputStream.BytesRead <= budget + 1 &&
                    rejectedRequest.Request.InputStream.BytesRead < rejectedRequest.Request.InputStream.Length,
                    "transport reads only request budget plus one detection byte");
            }
        }
    }

    private static void OptionalCapabilityAndMethods()
    {
        foreach (NameValueCollection query in new[] { new NameValueCollection(), new NameValueCollection { { "unexpected", "1" } } })
        {
            var context = new ChildSchemaBaseContext();
            var request = Schema("not JSON", query: query);
            var response = Run(request, context);
            Check(response.StatusCode == 404 && (bool?)response.Json["ok"] == false && context.SchemaCalls == 0 && request.Request.InputStream.BytesRead == 0,
                "missing optional capability returns 404 before body, query validation or native calls");
        }
        foreach (string method in new[] { "GET", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS" })
        {
            foreach (ChildSchemaBaseContext context in new ChildSchemaBaseContext[] {
                new ChildSchemaOnlyContext { Failure = new Exception("wrong method must not invoke") }, new ChildSchemaBaseContext()
            })
            {
                var request = Schema("not JSON", method, new NameValueCollection { { "unknown", "1" } });
                var response = Run(request, context);
                Check(response.StatusCode == 405 && response.Headers["Allow"] == "POST" && (bool?)response.Json["ok"] == false,
                    "wrong methods return 405 and Allow POST independent of optional capability");
                Check(context.SchemaCalls == 0 && request.Request.InputStream.BytesRead == 0,
                    "wrong method does not parse body or invoke optional capability");
            }
        }
    }

    private static void HealthFeatures()
    {
        foreach (ChildSchemaBaseContext context in new ChildSchemaBaseContext[] {
            new ChildSchemaBaseContext(), new ChildSchemaOnlyContext(), new ConnectivityOnlyChildContext(), new BothChildContext()
        })
        {
            var response = Run(new ChildSchemaHttpRequest("", "GET", "/health"), context, health: true);
            Check(response.StatusCode == 200 && (bool?)response.Json["ok"] == true, "health remains available");
            var features = ((JArray)response.Json["api_features"]).Values<string>().ToList();
            Check(features.Contains(Feature) == (context is IE3d21EquipmentChildSchemaContext),
                "child-schema health feature follows exact optional capability");
            Check(features.Contains("piping_connectivity_snapshot") == (context is IE3d21ConnectivitySnapshotContext),
                "unrelated optional feature remains independent");
            Check(features.Count == features.Distinct(StringComparer.Ordinal).Count(), "health feature identifiers unique");
            Check(context.SchemaCalls == 0 && context.ConnectivityCalls == 0, "feature discovery never probes either native reader");
        }
    }

    private static void ErrorChain()
    {
        foreach (Exception failure in new Exception[] {
            new ArgumentException("native input rejected"), new ArgumentOutOfRangeException("limit"),
            new JsonReaderException("bad JSON sentinel"), new JsonSerializationException("bad JSON contract"),
            new InvalidOperationException("ordinary native read failure"),
            new ArgumentException(new string('x', 10000)), new InvalidOperationException(new string('x', 10000)),
            new NativeDatabaseFaultException("native fault sentinel"), new NativeDatabaseFaultException(new string('x', 10000)),
            new InvalidOperationException(new string('x', 1023) + "😀" + new string('z', 10000)),
            new NativeDatabaseFaultException(new string('x', 1023) + "😀" + new string('z', 10000))
        })
        {
            var context = new ChildSchemaOnlyContext { Failure = failure };
            var response = Run(Schema(), context);
            bool fault = failure is NativeDatabaseFaultException;
            bool invalid = failure is ArgumentException || failure is JsonException;
            Check(response.StatusCode == (invalid ? 400 : 500) && (bool?)response.Json["ok"] == false,
                "input, ordinary and native-fault exception status distinguished");
            Check(context.SchemaCalls == 1 && response.Json["result"] == null, "failure is never retried or fabricated as a result");
            string error = (string)response.Json["error"];
            Check(!string.IsNullOrEmpty(error) && error.Length <= EquipmentChildSchemaRequest.MaximumTextLength && !char.IsHighSurrogate(error[error.Length - 1]),
                "all exception text bounded without orphaning UTF16 high surrogate");
            if (fault)
            {
                Check((string)response.Json["error_code"] == "native_database_fault" &&
                    (bool?)response.Json["native_fault_latched"] == true && (bool?)response.Json["retry_safe"] == false,
                    "dedicated native fault preserves code, latch and unsafe retry markers");
                if (failure.Message == "native fault sentinel") Check(error == failure.Message, "short native fault message preserved");
            }
            else Check(response.Json["error_code"] == null && response.Json["native_fault_latched"] == null && response.Json["retry_safe"] == null,
                "ordinary errors do not acquire native fault markers");
        }
    }

    private static int Main()
    {
        try
        {
            ValidAndPartialResults(); RejectBeforeNative(); BodyBudget();
            OptionalCapabilityAndMethods(); HealthFeatures(); ErrorChain();
            if (failures.Count != 0)
            {
                foreach (string failure in failures) Console.Error.WriteLine(failure);
                return 1;
            }
            Console.WriteLine("EQUIPMENT child schema HTTP production checks passed: " + checks);
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.GetType().FullName + ": " + error.Message);
            return 1;
        }
    }
}
