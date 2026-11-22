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

// Memory carriers and recording optional capabilities replace HTTP I/O and
// native SDK access. Handler setup, health predicates, selected route, complete
// fallback/catch chain, JSON helpers and request validator are production code.
internal sealed class TeamIdentityCountingStream : MemoryStream
{
    internal int BytesRead;
    internal TeamIdentityCountingStream(byte[] bytes) : base(bytes) { }
    public override Task<int> ReadAsync(byte[] buffer, int offset, int count,
        System.Threading.CancellationToken cancellationToken)
    {
        int read = Read(buffer, offset, count);
        BytesRead += read;
        return Task.FromResult(read);
    }
}

internal sealed class TeamIdentityHttpInnerRequest
{
    internal NameValueCollection QueryString;
    internal TeamIdentityCountingStream InputStream;
    internal Encoding ContentEncoding = Encoding.UTF8;
    internal string ContentType = "application/json";
    internal string HttpMethod;
    internal Uri Url;
    internal bool HasEntityBody;
    internal long ContentLength64;
    internal readonly NameValueCollection Headers = new NameValueCollection();
}

internal sealed class TeamIdentityHttpResponse
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

internal sealed class TeamIdentityHttpRequest
{
    internal readonly TeamIdentityHttpInnerRequest Request;
    internal readonly TeamIdentityHttpResponse Response = new TeamIdentityHttpResponse();
    internal TeamIdentityHttpRequest(string body = "", string method = "POST", NameValueCollection query = null,
        string path = "/api/v1/database/team-identity-manifest")
    {
        byte[] bytes = Encoding.UTF8.GetBytes(body);
        Request = new TeamIdentityHttpInnerRequest
        {
            InputStream = new TeamIdentityCountingStream(bytes), QueryString = query ?? new NameValueCollection(),
            HttpMethod = method, Url = new Uri("http://offline.invalid" + path),
            HasEntityBody = bytes.Length != 0, ContentLength64 = bytes.Length
        };
    }
}

internal class TeamIdentityBaseContext
{
    internal int NativeCalls, CommandStateCalls, ConnectedCalls;
    internal string SessionId, ExpectedContext;
    internal int Limit;
    internal Exception Failure;
    // The HTTP layer treats result as opaque. In particular, it must not add
    // available/session_id/bridge_build or rename the internal operation.
    internal object Result = new
    {
        operation = "internal_team_database_identity_manifest", schema_version = 1,
        success = true, complete = true, read_only = true, single_dispatch = true, atomic_snapshot = false,
        identity_consistent = true, all_team_relations_confirmed = true,
        teams = new object[0], databases = new object[0], current_mdb = new object[0],
        context = new { before = (object)null, after = (object)null, boundary_agreement = (bool?)null,
            session_id = E3d21HttpAdapter.SessionId, bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D") },
        read_errors = new object[0]
    };
    internal object CommandState() { CommandStateCalls++; return new { database_ready = true }; }
    internal bool Connected { get { ConnectedCalls++; return true; } }
    public object ReadTeamDatabaseIdentityManifest(string sessionId, string expectedContext, int limit = 64)
    {
        NativeCalls++;
        SessionId = sessionId; ExpectedContext = expectedContext; Limit = limit;
        if (Failure != null) throw Failure;
        return Result;
    }
}

internal sealed class TeamIdentityOnlyContext : TeamIdentityBaseContext, IE3d21TeamDatabaseIdentityManifestContext { }

namespace E3d21AiBridge
{
    internal sealed class E3d21HttpAdapter
    {
        internal const string SessionId = "team-database-identity-http-session";
        private const int MaximumJsonRequestBodyBytes = 1024 * 1024;
        private readonly string sessionId = SessionId;
        private readonly TeamIdentityBaseContext context;
        internal int FallbackDiagnosticsCalls;
        internal E3d21HttpAdapter(TeamIdentityBaseContext context) { this.context = context; }
        internal Task Invoke(TeamIdentityHttpRequest request) { return HandleAsync(request); }
        private object SafeCommandStateDiagnostics()
        {
            FallbackDiagnosticsCalls++;
            return new { forbidden_native_diagnostics = true };
        }

        // PRODUCTION_HANDLER

        // PRODUCTION_HTTP_HELPERS
    }
}

internal static class TeamDatabaseIdentityHttpHarness
{
    private const string Feature = "team_database_identity_manifest";
    private static readonly string Digest = string.Concat(Enumerable.Repeat("0123456789abcdef", 4));
    private static string Valid { get { return Body(new { expected_context = Digest }); } }
    private static int checks;
    private static readonly List<string> failures = new List<string>();
    private static string Body(object value) { return JsonConvert.SerializeObject(value); }
    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) failures.Add(message);
    }

    private static TeamIdentityHttpResponse Run(TeamIdentityHttpRequest request, TeamIdentityBaseContext context, bool health = false)
    {
        var adapter = new E3d21HttpAdapter(context);
        adapter.Invoke(request).GetAwaiter().GetResult();
        Check(adapter.FallbackDiagnosticsCalls == 0, "Team identity route never invokes fallback command diagnostics");
        Check(context.CommandStateCalls == (health ? 1 : 0) && context.ConnectedCalls == (health ? 1 : 0),
            "only explicit /health may observe command/connection state, never failure recovery");
        Check(request.Response.CloseCalls == 1, "response closes exactly once");
        Check(request.Response.ContentType == "application/json; charset=utf-8" &&
            request.Response.ContentLength64 == request.Response.OutputStream.Length,
            "wire metadata matches production JSON serialization");
        JObject json = request.Response.Json;
        string location = request.Request.HttpMethod + " " + request.Request.Url.AbsolutePath + " HTTP " + request.Response.StatusCode;
        Check((string)json["version"] == "E3D21" && (string)json["session_id"] == E3d21HttpAdapter.SessionId,
            location + ": server version/session preserved");
        Check((string)json["bridge_build"] == typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
            location + ": native bridge build is the executing adapter assembly MVID");
        Check((bool?)json["ok"] == (request.Response.StatusCode == 200), location + ": HTTP ok agrees with status");
        if (!health)
            Check(json["state"] == null && json["diagnostic_error"] == null, "route does not append diagnostics or state after an observation");
        return request.Response;
    }

    private static void Accept(string body, string expectedContext = null, int limit = 64)
    {
        var context = new TeamIdentityOnlyContext();
        var response = Run(new TeamIdentityHttpRequest(body), context);
        Check(response.StatusCode == 200 && context.NativeCalls == 1, "valid manifest calls native capability once with HTTP 200");
        Check(context.SessionId == E3d21HttpAdapter.SessionId && context.ExpectedContext == (expectedContext ?? Digest) && context.Limit == limit,
            "server-owned session, exact expected digest and limit are forwarded in interface parameter order");
        Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(context.Result)), "native receipt passes through without projection or additions");
        JObject result = (JObject)response.Json["result"];
        Check(result["available"] == null && result["session_id"] == null && result["bridge_build"] == null &&
            (string)result["operation"] == "internal_team_database_identity_manifest", "HTTP preserves existing internal operation and absence of top-level native fields");
    }

    private static void Reject(string body, string label, NameValueCollection query = null)
    {
        var context = new TeamIdentityOnlyContext();
        var response = Run(new TeamIdentityHttpRequest(body, query: query), context);
        Check(response.StatusCode == 400 && context.NativeCalls == 0, label + ": HTTP 400 before native capability");
    }

    private static void ValidRequests()
    {
        Accept(Valid); // This manifest defaults to 64, not database-state's 50.
        Accept(Body(new { expected_context = Digest, limit = 64 }));
        Accept(Body(new { expected_context = Digest, limit = 1 }), limit: 1);
        Accept(Body(new { expected_context = Digest, limit = 200 }), limit: 200);
        Accept(Body(new { expected_context = new string('0', 64) }), expectedContext: new string('0', 64));
        Accept(Body(new { expected_context = new string('f', 64) }), expectedContext: new string('f', 64));
        Accept("{\"expected_contex\\u0074\":\"" + Digest + "\"}");
    }

    private static void InvalidJsonAndFields()
    {
        foreach (string body in new[] { "", " ", "null", "true", "false", "0", "123", "\"text\"", "[]", "[{}]", "{", "{} {}", Valid + " false" })
            Reject(body, "malformed/nonobject/trailing JSON");
        Reject("{}", "expected_context is required");
        foreach (string name in new[] { "session_id", "bridge_build", "expected_session_id", "team", "refno", "offset", "cursor",
            "database_type", "request_id", "expected_revision", "path", "command", "source", "preview", "execute", "dry_run",
            "create", "database_number", "area", "Expected_context", "Limit", "unexpected" })
        {
            var body = JObject.Parse(Valid);
            body[name] = true;
            Reject(body.ToString(Formatting.None), "unknown exact-case field " + name);
        }
        Reject("{\"expected_context\":\"" + Digest + "\",\"expected_context\":\"" + Digest + "\"}", "duplicate expected_context");
        Reject("{\"expected_context\":\"" + Digest + "\",\"expected_contex\\u0074\":\"" + Digest + "\"}", "duplicate decoded expected_context");
        Reject("{\"expected_context\":\"" + Digest + "\",\"limit\":1,\"limit\":2}", "duplicate limit");
        Reject("{\"expected_context\":\"" + Digest + "\",\"limit\":1,\"limi\\u0074\":2}", "duplicate decoded limit");
        foreach (string name in new[] { "expected_context", "limit", "unexpected", null })
        {
            var request = new TeamIdentityHttpRequest("not JSON", query: new NameValueCollection { { name, "1" } });
            var context = new TeamIdentityOnlyContext();
            var response = Run(request, context);
            Check(response.StatusCode == 400 && context.NativeCalls == 0 && request.Request.InputStream.BytesRead == 0,
                "query rejected before body parsing or native read");
        }
        var repeated = new NameValueCollection { { "limit", "1" }, { "limit", "2" } };
        Reject(Valid, "repeated query forbidden too", repeated);
    }

    private static void InvalidTypesAndBounds()
    {
        foreach (string value in new[] { "null", "true", "false", "0", "1.0", "[]", "{}" })
            Reject("{\"expected_context\":" + value + "}", "expected_context requires a string");
        foreach (string digest in new[] { "", " ", new string('a', 63), new string('a', 65), Digest.ToUpperInvariant(),
            new string('g', 64), new string('A', 64), " " + Digest, Digest + " ", "\t" + Digest, Digest + "\n",
            Digest + "\r\n", Digest.Substring(0, 63) + "\0", new string('\uff41', 64) })
            Reject(Body(new { expected_context = digest }), "digest requires exact lowercase hex with no trimming");
        foreach (string value in new[] { "null", "true", "false", "\"64\"", "64.0", "1e0", "[]", "{}", "NaN", "Infinity",
            "2147483648", "-2147483649", "999999999999999999999999999999999999" })
            Reject("{\"expected_context\":\"" + Digest + "\",\"limit\":" + value + "}", "limit requires a real JSON bounded integer");
        foreach (int limit in new[] { int.MinValue, -1, 0, 201, int.MaxValue })
            Reject(Body(new { expected_context = Digest, limit = limit }), "limit range 1..200");
    }

    private static void BodyBudget()
    {
        const int budget = 4096;
        string exact = Valid + new string(' ', budget - Encoding.UTF8.GetByteCount(Valid));
        Accept(exact);
        // StreamReader accepts a UTF-8 BOM. The transport budget must count all
        // three original bytes even though they do not become JSON characters.
        string bom = "\ufeff" + Valid;
        Check(Encoding.UTF8.GetByteCount(bom) > bom.Length, "byte-budget control contains a real multibyte UTF-8 BOM");
        Accept(bom + new string(' ', budget - Encoding.UTF8.GetByteCount(bom)));
        Reject(exact + " ", "first byte over the raw 4096-byte budget");
        Reject(bom + new string(' ', budget - Encoding.UTF8.GetByteCount(bom) + 1), "budget uses raw UTF-8 bytes rather than characters");
        foreach (bool chunked in new[] { false, true })
        {
            var context = new TeamIdentityOnlyContext();
            var request = new TeamIdentityHttpRequest(exact + new string(' ', 65536));
            if (chunked)
            {
                request.Request.ContentLength64 = -1;
                request.Request.Headers["Transfer-Encoding"] = "chunked";
            }
            var response = Run(request, context);
            Check(response.StatusCode == 400 && context.NativeCalls == 0, "ordinary/chunked oversized body rejected before native work");
            Check(request.Request.InputStream.BytesRead <= budget + 1 && request.Request.InputStream.BytesRead < request.Request.InputStream.Length,
                "bounded reader stops after first over-budget byte rather than draining the request");
        }
        var exactChunk = new TeamIdentityHttpRequest(exact);
        exactChunk.Request.ContentLength64 = -1;
        exactChunk.Request.Headers["Transfer-Encoding"] = "chunked";
        var exactContext = new TeamIdentityOnlyContext();
        var exactResponse = Run(exactChunk, exactContext);
        Check(exactResponse.StatusCode == 200 && exactContext.NativeCalls == 1 && exactContext.Limit == 64,
            "exact-budget chunked request still has the manifest-specific default 64");
    }

    private static void OptionalCapabilityAndMethods()
    {
        var absent = new TeamIdentityBaseContext();
        var request = new TeamIdentityHttpRequest("not JSON");
        var response = Run(request, absent);
        Check(response.StatusCode == 404 && absent.NativeCalls == 0 && request.Request.InputStream.BytesRead == 0,
            "missing optional capability returns 404 before parsing or native calls");
        foreach (string method in new[] { "GET", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS" })
            foreach (TeamIdentityBaseContext context in new TeamIdentityBaseContext[] { new TeamIdentityBaseContext(),
                new TeamIdentityOnlyContext { Failure = new Exception("must not invoke") } })
            {
                request = new TeamIdentityHttpRequest("not JSON", method);
                response = Run(request, context);
                Check(response.StatusCode == 405 && response.Headers["Allow"] == "POST",
                    "wrong method is 405 Allow POST whether optional reader is available or not");
                Check(context.NativeCalls == 0 && request.Request.InputStream.BytesRead == 0,
                    "wrong method never consumes body or invokes SDK reader");
            }
        foreach (TeamIdentityBaseContext context in new TeamIdentityBaseContext[] { new TeamIdentityBaseContext(), new TeamIdentityOnlyContext() })
        {
            response = Run(new TeamIdentityHttpRequest(method: "GET", path: "/health"), context, health: true);
            var features = ((JArray)response.Json["api_features"]).Values<string>().ToList();
            Check(features.Contains(Feature) == (context is IE3d21TeamDatabaseIdentityManifestContext),
                "health feature appears only when the actual optional interface is implemented");
            Check(features.Count == features.Distinct(StringComparer.Ordinal).Count(), "health features remain unique");
            Check(context.NativeCalls == 0, "health feature discovery never probes native manifest");
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
        object boundary = new { project = "PROJECT", mdb = "MDB", context_id = Digest,
            ce = new { refno = "=1/9", type = "DB", db_type = "System" }, ce_identity_key = "16|=1/9|DB" };
        var partial = new TeamIdentityOnlyContext { Result = new
        {
            operation = "internal_team_database_identity_manifest", schema_version = 1, read_only = true,
            single_dispatch = true, atomic_snapshot = false, success = false, complete = false,
            source = "SDK visible bounded arrays", limit_per_source = 64, owner_hop_limit = 2,
            identity_consistent = (bool?)null, all_team_relations_confirmed = false,
            scans = new { teams = new { complete = false, truncated = true }, databases = new { complete = false }, current_mdb = new { complete = true } },
            teams = new[] { new { index = 0, identity = (object)null, canonical_name = "TEAM", read_errors = new object[0] } },
            databases = new[] { new { index = 0, dbno = 1, fino = (int?)null, extno = 0, future_field = "retain" } },
            current_mdb = new object[0],
            context = new { before = boundary, after = (object)null, boundary_agreement = (bool?)null,
                session_id = E3d21HttpAdapter.SessionId, bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D") },
            read_errors = new[] { new { path = "context.after", code = "read_failed", message = "SDK stopped" } },
            identity_findings = new[] { new { code = "unknown", message = "not a create authorization" } },
            scope = new { project_coverage_verified = false, database_creation_verified = false }, future_unknown = new { marker = "opaque evidence" }
        } };
        var response = Run(new TeamIdentityHttpRequest(Valid), partial);
        Check(response.StatusCode == 200 && partial.NativeCalls == 1, "native partial retains HTTP success with one read");
        Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(partial.Result)), "opaque partial/errors/unknown/truncation fields retained exactly");
        JObject result = (JObject)response.Json["result"];
        Check(result["available"] == null && result["session_id"] == null && result["bridge_build"] == null &&
            result["context"]["before"]["session_id"] == null && result["context"]["before"]["bridge_build"] == null,
            "adapter does not manufacture available or per-boundary/native-top identity aliases");

        string edge4093 = new string('x', 4092) + char.ConvertFromUtf32(0x1F600) + new string('y', 8000);
        string edge4096 = new string('x', 4095) + char.ConvertFromUtf32(0x1F600) + new string('y', 8000);
        foreach (Exception failure in new Exception[] { new ArgumentException("native argument error"), new JsonReaderException("native JSON error"),
            new InvalidOperationException("native read failed"), new InvalidOperationException(new string('x', 10000)),
            new InvalidOperationException(edge4093), new InvalidOperationException(edge4096),
            new NativeDatabaseFaultException("native fault sentinel"), new NativeDatabaseFaultException(new string('x', 10000)),
            new NativeDatabaseFaultException(edge4093), new NativeDatabaseFaultException(edge4096) })
        {
            var context = new TeamIdentityOnlyContext { Failure = failure };
            response = Run(new TeamIdentityHttpRequest(Valid), context);
            bool fault = failure is NativeDatabaseFaultException;
            int status = failure is ArgumentException || failure is JsonException ? 400 : 500;
            Check(response.StatusCode == status && context.NativeCalls == 1 && response.Json["result"] == null,
                "native exception retains status and one read, with no retry or fabricated partial result");
            string message = (string)response.Json["error"];
            Check(!string.IsNullOrEmpty(message) && message.Length <= 4096 && WellFormedUtf16(message) && message.IndexOf('\ufffd') < 0,
                "ordinary/native error is at most 4096 UTF-16 code units without splitting or replacing a surrogate pair");
            if (fault)
            {
                Check((string)response.Json["error_code"] == "native_database_fault" &&
                    (bool?)response.Json["native_fault_latched"] == true && (bool?)response.Json["retry_safe"] == false,
                    "native fault uses its dedicated code, latched state and unsafe-retry evidence");
                if (failure.Message == "native fault sentinel") Check(message == failure.Message, "short native fault message retained exactly");
            }
            else Check(response.Json["error_code"] == null && response.Json["native_fault_latched"] == null,
                "ordinary exception does not fabricate native fault markers");
        }
    }

    private static int Main()
    {
        try
        {
            ValidRequests();
            InvalidJsonAndFields();
            InvalidTypesAndBounds();
            BodyBudget();
            OptionalCapabilityAndMethods();
            PartialAndFailureResults();
            if (failures.Count != 0)
            {
                foreach (string failure in failures) Console.Error.WriteLine(failure);
                return 1;
            }
            Console.WriteLine("TEAM database identity HTTP production checks passed: " + checks);
            return 0;
        }
        catch (Exception failure)
        {
            Console.Error.WriteLine(failure.GetType().FullName + ": " + failure.Message);
            return 1;
        }
    }
}
