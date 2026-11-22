using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

internal sealed class BatchInnerRequest
{
    internal NameValueCollection QueryString;
    internal MemoryStream InputStream;
    internal Encoding ContentEncoding = Encoding.UTF8;
}
internal sealed class BatchResponse
{
    internal int Status;
    internal object Value;
}
internal sealed class BatchRequest
{
    internal readonly BatchInnerRequest Request;
    internal readonly BatchResponse Response = new BatchResponse();
    internal BatchRequest(string body, NameValueCollection query = null)
    {
        Request = new BatchInnerRequest
        {
            QueryString = query ?? new NameValueCollection(),
            InputStream = new MemoryStream(Encoding.UTF8.GetBytes(body))
        };
    }
}
internal sealed class BatchContext
{
    internal int Calls;
    internal string Reference, Revision;
    internal IDictionary<string, string> Values;
    internal bool DryRun;
    internal readonly object Result = new { success = true, test_marker = "native-result" };
    public object SetAttributes(string reference, IDictionary<string, string> values, string revision, bool preview)
    {
        Calls++; Reference = reference; Values = values; Revision = revision; DryRun = preview;
        return Result;
    }
}
internal static class BatchProductionRoute
{
    private const int MaximumJsonRequestBodyBytes = 1024 * 1024;
    internal static async Task Invoke(BatchRequest request, BatchContext context)
    {
        object response = null;
        // PRODUCTION_ROUTE
        await WriteJsonAsync(request.Response, response, 200);
    }
    private static Task WriteJsonAsync(BatchResponse response, object value, int status)
    { response.Status = status; response.Value = value; return Task.CompletedTask; }
    // PRODUCTION_METHODS
}
internal static class AttributeBatchHttpHarness
{
    private static int checks;
    private static readonly List<string> failures = new List<string>();
    private const string Valid = "\"refno\":\"=1/1\",\"values\":{\"DESC\":\"Pump\",\"DIAM\":\"12.5\"}";
    private static void Check(bool condition, string label) { checks++; if (!condition) failures.Add(label); }
    private static Exception Run(BatchRequest request, BatchContext context)
    {
        try { BatchProductionRoute.Invoke(request, context).GetAwaiter().GetResult(); return null; }
        catch (Exception error) { return error; }
    }
    private static void Rejected(string body, string label, NameValueCollection query = null)
    {
        var request = new BatchRequest(body, query);
        var context = new BatchContext();
        Exception error = Run(request, context);
        Check(error is ArgumentException || error is JsonReaderException, label + ": validation error");
        Check(context.Calls == 0, label + ": zero context calls");
    }
    private static void Accepted(string body, bool preview, string revision, string label)
    {
        var request = new BatchRequest(body);
        var context = new BatchContext();
        Exception error = Run(request, context);
        Check(error == null, label + ": accepted");
        Check(context.Calls == 1, label + ": one context call");
        Check(context.Reference == "=1/1" && context.Revision == revision && context.DryRun == preview, label + ": options forwarded");
        Check(context.Values != null && context.Values.Keys.SequenceEqual(new[] { "DESC", "DIAM" }) &&
            context.Values["DESC"] == "Pump" && context.Values["DIAM"] == "12.5", label + ": values and order preserved");
        Check(request.Response.Status == 200 && request.Response.Value != null, label + ": response envelope");
        if (request.Response.Value != null)
        {
            JObject payload = JObject.FromObject(request.Response.Value);
            Check((bool)payload["ok"] && (string)payload["version"] == "E3D21" &&
                JToken.DeepEquals(payload["result"], JObject.FromObject(context.Result)), label + ": native result preserved");
        }
    }
    private static int Main()
    {
        Accepted("{" + Valid + "}", true, null, "default preview");
        string revision = new string('a', 64);
        Accepted("{" + Valid + ",\"dry_run\":false,\"expected_revision\":\"" + revision + "\"}", false, revision, "execute");
        Rejected("{" + Valid + ",\"extra\":1}", "unknown key");
        Rejected("{" + Valid + "}", "query key", new NameValueCollection { { "dry_run", "false" } });
        foreach (string duplicate in new[]
        {
            "{\"refno\":\"=1/1\",\"refno\":\"=1/2\",\"values\":{\"DESC\":\"X\"}}",
            "{\"refno\":\"=1/1\",\"refn\\u006f\":\"=1/2\",\"values\":{\"DESC\":\"X\"}}",
            "{\"refno\":\"=1/1\",\"values\":{\"DESC\":\"X\",\"DESC\":\"Y\"}}",
            "{\"refno\":\"=1/1\",\"values\":{\"DESC\":\"X\",\"DE\\u0053C\":\"Y\"}}"
        }) Rejected(duplicate, "duplicate decoded JSON property");
        foreach (string body in new[] { "", "null", "true", "[]", "{", "{} {}",
            "{\"refno\":1,\"values\":{\"DESC\":\"X\"}}", "{\"refno\":\"=1/1\",\"values\":{}}",
            "{\"refno\":\"=1/1\",\"values\":{\"DESC\":null}}", "{\"refno\":\"=1/1\",\"values\":{\"DIAM\":12.5}}",
            "{" + Valid + ",\"dry_run\":1}", "{" + Valid + ",\"expected_revision\":null}" })
            Rejected(body, "invalid body or type");
        Rejected("{" + Valid + "}" + new string(' ', 65536), "oversized body");
        if (failures.Count != 0)
        {
            foreach (string failure in failures) Console.Error.WriteLine(failure);
            return 1;
        }
        Console.WriteLine("Attribute batch HTTP production checks passed: " + checks);
        return 0;
    }
}
