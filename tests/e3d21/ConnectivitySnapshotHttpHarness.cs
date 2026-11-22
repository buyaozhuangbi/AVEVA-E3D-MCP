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

// Memory carriers replace only HttpListener I/O. The tested route, request
// parser, feature declaration, JSON helpers and selected error chain are copied
// directly from E3d21HttpAdapter.cs by the Python contract test.
internal sealed class ConnectivityCountingStream : MemoryStream
{
    internal int BytesRead;
    internal ConnectivityCountingStream(byte[] bytes) : base(bytes) { }
    public override Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken)
    {
        int result = Read(buffer, offset, count);
        BytesRead += result;
        return Task.FromResult(result);
    }
}

internal sealed class ConnectivityHttpInnerRequest
{
    internal NameValueCollection QueryString;
    internal ConnectivityCountingStream InputStream;
    internal Encoding ContentEncoding = Encoding.UTF8;
    internal string ContentType = "application/json";
    internal string HttpMethod;
    internal Uri Url;
    internal bool HasEntityBody;
    internal long ContentLength64;
    internal readonly NameValueCollection Headers = new NameValueCollection();
}

internal sealed class ConnectivityHttpResponse
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

internal sealed class ConnectivityHttpRequest
{
    internal readonly ConnectivityHttpInnerRequest Request;
    internal readonly ConnectivityHttpResponse Response = new ConnectivityHttpResponse();
    internal ConnectivityHttpRequest(string json = "", string method = "POST",
        string path = "/api/v1/piping/connectivity-snapshot", NameValueCollection query = null)
    {
        byte[] bytes = Encoding.UTF8.GetBytes(json);
        Request = new ConnectivityHttpInnerRequest
        {
            InputStream = new ConnectivityCountingStream(bytes),
            QueryString = query ?? new NameValueCollection(),
            HttpMethod = method,
            Url = new Uri("http://offline.invalid" + path),
            HasEntityBody = bytes.Length != 0,
            ContentLength64 = bytes.Length
        };
    }
}

internal class ConnectivityBaseContext
{
    internal int SnapshotCalls, CommandStateCalls, ConnectedCalls;
    internal string SessionId, BridgeBuild;
    internal ConnectivitySnapshotRequest Request;
    internal Exception Failure;
    internal object Result = new { available = true, success = true, complete = true, marker = "native-snapshot" };
    internal object CommandState() { CommandStateCalls++; return new { database_ready = true }; }
    internal bool Connected { get { ConnectedCalls++; return true; } }
    protected object SnapshotRead(ConnectivitySnapshotRequest request, string sessionId, string bridgeBuild)
    {
        SnapshotCalls++;
        Request = request;
        SessionId = sessionId;
        BridgeBuild = bridgeBuild;
        if (Failure != null) throw Failure;
        return Result;
    }
}

internal sealed class SnapshotOnlyContext : ConnectivityBaseContext, IE3d21ConnectivitySnapshotContext
{
    public object ReadConnectivitySnapshot(ConnectivitySnapshotRequest request, string sessionId, string bridgeBuild)
    { return SnapshotRead(request, sessionId, bridgeBuild); }
}

namespace E3d21AiBridge
{
    internal sealed class E3d21HttpAdapter
    {
        internal const string SessionId = "connectivity-snapshot-session";
        private const int MaximumJsonRequestBodyBytes = 1024 * 1024;
        private readonly string sessionId = SessionId;
        private readonly ConnectivityBaseContext context;
        internal int FallbackDiagnosticsCalls;
        internal E3d21HttpAdapter(ConnectivityBaseContext context) { this.context = context; }
        internal Task Invoke(ConnectivityHttpRequest request) { return HandleAsync(request); }
        private object SafeCommandStateDiagnostics()
        {
            FallbackDiagnosticsCalls++;
            return new { forbidden_native_diagnostics = true };
        }

        // PRODUCTION_HANDLER

        // PRODUCTION_HTTP_HELPERS
    }
}

internal static class ConnectivitySnapshotHttpHarness
{
    private const string Path = "/api/v1/piping/connectivity-snapshot";
    private const string Valid = "{\"branches\":[{\"refno\":\"/B\",\"expected_type\":\"BRAN\"}]}";
    private const string Feature = "piping_connectivity_snapshot";
    private static int checks;
    private static readonly List<string> failures = new List<string>();

    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) failures.Add(message);
    }

    private static ConnectivityHttpResponse Run(ConnectivityHttpRequest request,
        ConnectivityBaseContext context, bool health = false)
    {
        var adapter = new E3d21HttpAdapter(context);
        adapter.Invoke(request).GetAwaiter().GetResult();
        Check(adapter.FallbackDiagnosticsCalls == 0,
            "connectivity HTTP route must not execute command-state fallback diagnostics");
        Check(context.CommandStateCalls == (health ? 1 : 0) && context.ConnectedCalls == (health ? 1 : 0),
            "only /health may observe command or connection diagnostics");
        Check(request.Response.CloseCalls == 1, "response closes exactly once");
        Check(request.Response.ContentType == "application/json; charset=utf-8" &&
            request.Response.ContentLength64 == request.Response.OutputStream.Length,
            "wire metadata describes serialized production JSON");
        JObject json = request.Response.Json;
        Check((string)json["version"] == "E3D21" && (string)json["session_id"] == E3d21HttpAdapter.SessionId,
            "connectivity response binds version and adapter session");
        Check((string)json["bridge_build"] == typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
            request.Request.HttpMethod + " " + request.Request.Url.AbsolutePath + " HTTP " + request.Response.StatusCode +
            ": response binds executing assembly MVID");
        if (!health)
            Check(json["state"] == null && json["diagnostic_error"] == null,
                "connectivity responses omit native diagnostics");
        return request.Response;
    }

    private static ConnectivityHttpRequest Snapshot(string json = Valid, string method = "POST",
        NameValueCollection query = null)
    { return new ConnectivityHttpRequest(json, method, Path, query); }

    private static void ValidAndPartialResults()
    {
        object partial = new
        {
            operation = "piping_connectivity_snapshot",
            available = true,
            success = false,
            complete = false,
            read_only = true,
            single_dispatch = true,
            atomic_snapshot = false,
            context = new
            {
                before = new { session_id = E3d21HttpAdapter.SessionId, bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D") },
                after = (object)null,
                same_project_instance = (bool?)null,
                same_mdb_instance = (bool?)null,
                boundary_agreement = (bool?)null
            },
            branches = new[] { new { complete = false, data = (object)null, read_errors = new[] { new { code = "read_failed" } } } },
            pairs = new object[0],
            trace = (object)null,
            read_errors = new[] { new { code = "context_after_unavailable" } },
            future_unknown = new { keep = "opaque native receipt" }
        };
        var context = new SnapshotOnlyContext { Result = partial };
        var response = Run(Snapshot(), context);
        Check(response.StatusCode == 200 && (bool?)response.Json["ok"] == true && context.SnapshotCalls == 1,
            "valid snapshot route performs one optional native read");
        Check(context.SessionId == E3d21HttpAdapter.SessionId &&
            context.BridgeBuild == typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
            "native boundary receives exact HTTP session and build identity");
        Check(context.Request != null && context.Request.Branches.Count == 1 &&
            context.Request.Branches[0] == "/B" && context.Request.MembersPerBranch == 64 &&
            context.Request.MaxHops == 8 && context.Request.MaxNodes == 64,
            "validated parser defaults and normalized branch are forwarded");
        Check(JToken.DeepEquals(response.Json["result"], JToken.FromObject(partial)),
            "partial, null and unknown native evidence is retained verbatim");
    }

    private static void RejectBeforeNative()
    {
        foreach (string body in new[] {
            "", " ", "null", "true", "[]", "[{}]", "{", "{} {}", "{} false",
            "{\"branches\":[{\"refno\":\"/B\",\"expected_type\":\"BRAN\"}],\"unknown\":true}",
            "{\"branches\":[{\"refno\":\"/B\",\"expected_type\":\"BRAN\"}],\"branches\":[]}",
            "{\"branches\":[{\"refno\":\"/B\",\"expected_type\":\"BRAN\",\"refno\":\"/OTHER\"}]}",
            "{\"branches\":[{\"refno\":\"/B\",\"expected_type\":\"PIPE\"}]}",
            "{\"starts\":[{\"refno\":\"=1/2\",\"expected_type\":\"ELBO\",\"point_number\":true}]}",
            "{\"branches\":[],\"pairs\":[],\"starts\":[]}"
        })
        {
            var context = new SnapshotOnlyContext();
            var request = Snapshot(body);
            var response = Run(request, context);
            Check(response.StatusCode == 400 && (bool?)response.Json["ok"] == false,
                "invalid JSON, duplicates, unknown fields and malformed contracts return 400");
            Check(context.SnapshotCalls == 0 && request.Request.InputStream.BytesRead <= 8193,
                "invalid input stops before native read");
        }

        var queryContext = new SnapshotOnlyContext();
        var queryRequest = Snapshot(Valid, query: new NameValueCollection { { "trace", "1" } });
        var queryResponse = Run(queryRequest, queryContext);
        Check(queryResponse.StatusCode == 400 && queryContext.SnapshotCalls == 0 &&
            queryRequest.Request.InputStream.BytesRead == 0, "query parameters are rejected before body/native reads");
    }

    private static void BodyBudget()
    {
        const int budget = 65536;
        string exact = Valid + new string(' ', budget - Encoding.UTF8.GetByteCount(Valid));
        var accepted = new SnapshotOnlyContext();
        var acceptedRequest = Snapshot(exact);
        var acceptedResponse = Run(acceptedRequest, accepted);
        Check(acceptedResponse.StatusCode == 200 && accepted.SnapshotCalls == 1 &&
            acceptedRequest.Request.InputStream.BytesRead == budget, "exactly 65536 UTF-8 request bytes are accepted");

        var rejected = new SnapshotOnlyContext();
        var rejectedRequest = Snapshot(exact + new string(' ', 65536));
        var rejectedResponse = Run(rejectedRequest, rejected);
        Check(rejectedResponse.StatusCode == 400 && rejected.SnapshotCalls == 0,
            "oversized body is rejected before native work");
        Check(rejectedRequest.Request.InputStream.BytesRead <= budget + 1 &&
            rejectedRequest.Request.InputStream.BytesRead < rejectedRequest.Request.InputStream.Length,
            "body parser reads at most the configured 65536-byte cap plus one detection byte");
    }

    private static void OptionalCapabilityAndMethods()
    {
        var absent = new ConnectivityBaseContext();
        var unavailableRequest = Snapshot("not JSON");
        var unavailable = Run(unavailableRequest, absent);
        Check(unavailable.StatusCode == 404 && (bool?)unavailable.Json["ok"] == false &&
            absent.SnapshotCalls == 0 && unavailableRequest.Request.InputStream.BytesRead == 0,
            "missing optional interface returns 404 without reading input or touching native context");

        foreach (string method in new[] { "GET", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS" })
        {
            foreach (ConnectivityBaseContext context in new ConnectivityBaseContext[] {
                new SnapshotOnlyContext { Failure = new Exception("wrong method must not call") },
                new ConnectivityBaseContext()
            })
            {
                var request = Snapshot("not JSON", method);
                var response = Run(request, context);
                Check(response.StatusCode == 405 && response.Headers["Allow"] == "POST",
                    "wrong methods return 405 with Allow: POST, regardless of optional capability");
                Check(context.SnapshotCalls == 0 && request.Request.InputStream.BytesRead == 0,
                    "wrong methods do not read input or invoke the snapshot capability");
            }
        }
    }

    private static void HealthFeature()
    {
        foreach (ConnectivityBaseContext context in new ConnectivityBaseContext[] {
            new ConnectivityBaseContext(), new SnapshotOnlyContext()
        })
        {
            var response = Run(new ConnectivityHttpRequest("", "GET", "/health"), context, health: true);
            Check(response.StatusCode == 200 && (bool?)response.Json["ok"] == true, "health route is available");
            var features = ((JArray)response.Json["api_features"]).Values<string>().ToList();
            Check(features.Contains(Feature) == (context is IE3d21ConnectivitySnapshotContext),
                "connectivity feature follows its optional interface");
            Check(features.Count == features.Distinct(StringComparer.Ordinal).Count(), "health feature identifiers are unique");
            Check(context.SnapshotCalls == 0, "feature discovery never probes the native snapshot");
        }
    }

    private static void ErrorChain()
    {
        foreach (Exception failure in new Exception[] {
            new ArgumentException("native parameter rejected"),
            new InvalidOperationException("ordinary native read failed"),
            new InvalidOperationException(new string('x', 10000)),
            new NativeDatabaseFaultException("native fault sentinel"),
            new NativeDatabaseFaultException(new string('x', 10000))
        })
        {
            var context = new SnapshotOnlyContext { Failure = failure };
            var response = Run(Snapshot(), context);
            bool fault = failure is NativeDatabaseFaultException;
            Check(response.StatusCode == (failure is ArgumentException ? 400 : 500) && (bool?)response.Json["ok"] == false,
                "argument, ordinary and native-fault statuses are distinguished");
            Check(context.SnapshotCalls == 1 && response.Json["result"] == null,
                "native exception is not retried and does not fabricate a result");
            Check(((string)response.Json["error"]).Length <= 4096,
                "error response text is bounded");
            if (fault)
            {
                Check((string)response.Json["error_code"] == "native_database_fault" &&
                    (bool?)response.Json["native_fault_latched"] == true && (bool?)response.Json["retry_safe"] == false,
                    "native-fault marker and unsafe-retry diagnostics are preserved");
                if (failure.Message == "native fault sentinel")
                    Check((string)response.Json["error"] == failure.Message, "short native-fault text is retained");
            }
            else Check(response.Json["error_code"] == null && response.Json["native_fault_latched"] == null,
                "ordinary exception does not acquire native-fault markers");
        }
    }

    private static int Main()
    {
        try
        {
            ValidAndPartialResults();
            RejectBeforeNative();
            BodyBudget();
            OptionalCapabilityAndMethods();
            HealthFeature();
            ErrorChain();
            if (failures.Count != 0)
            {
                foreach (string failure in failures) Console.Error.WriteLine(failure);
                return 1;
            }
            Console.WriteLine("CONNECTIVITY snapshot HTTP production checks passed: " + checks);
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.GetType().FullName + ": " + error.Message);
            return 1;
        }
    }
}
