using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

// No SDK assemblies or PML harness state participate in this executable.
namespace NativeClashHttpContract
{
    internal sealed class ClashInnerRequest
    {
        internal NameValueCollection QueryString;
        internal MemoryStream InputStream;
        internal Encoding ContentEncoding = Encoding.UTF8;
    }
    internal sealed class ClashResponse
    {
        internal int Status;
        internal object Value;
    }
    internal sealed class ClashRequest
    {
        internal readonly ClashInnerRequest Request;
        internal readonly ClashResponse Response = new ClashResponse();
        internal ClashRequest(string body, NameValueCollection query = null)
        {
            Request = new ClashInnerRequest { QueryString = query ?? new NameValueCollection(),
                InputStream = new MemoryStream(Encoding.UTF8.GetBytes(body)) };
        }
    }
    internal sealed class ClashContext
    {
        internal int Calls, Limit;
        internal List<string> First, Second;
        internal double Clearance;
        internal bool DryRun;
        internal readonly object Result = new { success = true, no_clear_verdict = true, test_marker = "native-result" };
        public object CheckNativeClashes(List<string> first, List<string> second, double clearance, int limit, bool dryRun)
        {
            Calls++; First = first; Second = second; Clearance = clearance; Limit = limit; DryRun = dryRun;
            return Result;
        }
    }
    internal static class ClashProductionRoute
    {
        private const int MaximumJsonRequestBodyBytes = 1024 * 1024;
        internal static async Task Invoke(ClashRequest request, ClashContext context)
        {
            object response = null;
            string sessionId = "CLASH-SESSION";
            // PRODUCTION_ROUTE
            await WriteJsonAsync(request.Response, response, 200);
        }
        private static Task WriteJsonAsync(ClashResponse response, object value, int status)
        { response.Status = status; response.Value = value; return Task.CompletedTask; }
        // PRODUCTION_METHODS
    }
    internal static class NativeClashHttpHarness
    {
        private const string Valid = "\"first_refnos\":[\"/A\"],\"second_refnos\":[\"=1/2\"]";
        private static int checks;
        private static readonly List<string> failures = new List<string>();
        private static void Check(bool condition, string label) { checks++; if (!condition) failures.Add(label); }
        private static Exception Run(ClashRequest request, ClashContext context)
        {
            try { ClashProductionRoute.Invoke(request, context).GetAwaiter().GetResult(); return null; }
            catch (Exception error) { return error; }
        }
        private static void Rejected(string body, string label, NameValueCollection query = null)
        {
            var request = new ClashRequest(body, query);
            var context = new ClashContext();
            Exception error = Run(request, context);
            Check(error is ArgumentException || error is JsonReaderException,
                label + ": validation error before dispatch");
            Check(context.Calls == 0, label + ": zero native context calls");
        }
        private static void Accepted(string body, string[] first, string[] second, double clearance, int limit, bool preview, string label)
        {
            var request = new ClashRequest(body);
            var context = new ClashContext();
            Exception error = Run(request, context);
            Check(error == null, label + ": accepted without exception");
            Check(context.Calls == 1, label + ": exactly one context call");
            Check(context.First != null && context.First.SequenceEqual(first), label + ": first group exact forwarding");
            Check(context.Second != null && context.Second.SequenceEqual(second), label + ": second group exact forwarding");
            Check(context.Clearance == clearance && context.Limit == limit && context.DryRun == preview,
                label + ": numeric and preview options exact forwarding");
            Check(request.Response.Status == 200 && request.Response.Value != null, label + ": response written");
            if (request.Response.Value != null)
            {
                JObject envelope = JObject.FromObject(request.Response.Value);
                Check((bool)envelope["ok"] && (string)envelope["version"] == "E3D21" &&
                    (string)envelope["session_id"] == "CLASH-SESSION" && (bool)envelope["dry_run"] == preview,
                    label + ": route envelope session/preview fields");
                Check(JToken.DeepEquals(envelope["result"], JObject.FromObject(context.Result)),
                    label + ": native result retained without reinterpretation");
            }
        }
        private static void ValidRequests()
        {
            Accepted("{" + Valid + "}", new[] { "/A" }, new[] { "=1/2" }, 0, 50, true, "default preview");
            Accepted(JsonConvert.SerializeObject(new { first_refnos = new[] { " /MixedCase ", "/中文" },
                second_refnos = new[] { "=20/30", "/Other" }, clearance = 0.125, result_limit = 200, dry_run = false }),
                new[] { " /MixedCase ", "/中文" }, new[] { "=20/30", "/Other" }, 0.125, 200, false,
                "explicit execution forwards all five arguments verbatim");
            Accepted("{" + Valid + ",\"clearance\":1000000,\"result_limit\":1,\"dry_run\":true}",
                new[] { "/A" }, new[] { "=1/2" }, 1000000, 1, true, "integer clearance and explicit preview");
        }
        private static void InvalidJsonAndUnknownParameters()
        {
            foreach (string body in new[] { "", "null", "true", "[]", "[{}]", "{} {}", "{", "\"text\"",
                "{" + Valid + ",\"unexpected\":1}", "{" + Valid + ",\"version\":\"E3D21\"}",
                "{" + Valid + ",\"capture_after\":false}", "{" + Valid + ",\"clearance_mm\":0}" })
                Rejected(body, "invalid body or unknown parameter");
            foreach (string duplicate in new[] {
                "\"first_refnos\":[\"/Other\"]", "\"first_refno\\u0073\":[\"/Other\"]",
                "\"second_refnos\":[\"/Other\"]", "\"clearance\":1,\"clearance\":2",
                "\"result_limit\":1,\"result_limit\":2", "\"dry_run\":true,\"dry_run\":false"
            }) Rejected("{" + Valid + "," + duplicate + "}", "duplicate decoded key " + duplicate);
            Rejected("{" + Valid + "}", "query parameters forbidden", new NameValueCollection { { "dry_run", "false" } });
            Rejected("{" + Valid + "}", "unknown query forbidden", new NameValueCollection { { "extra", "x" } });
        }
        private static void InvalidArrayTypes()
        {
            Rejected("{}", "both groups missing");
            Rejected("{\"first_refnos\":[\"/A\"]}", "second group missing");
            Rejected("{\"second_refnos\":[\"/B\"]}", "first group missing");
            foreach (string value in new[] { "null", "false", "123", "\"/A\"", "{}", "[null]", "[false]", "[123]", "[[]]", "[{}]",
                "[\"/VALID\",null]", "[\"/VALID\",123]" })
            {
                Rejected("{\"first_refnos\":" + value + ",\"second_refnos\":[\"/B\"]}", "invalid first array " + value);
                Rejected("{\"first_refnos\":[\"/A\"],\"second_refnos\":" + value + "}", "invalid second array " + value);
            }
            // Array cardinality, reference content, resolved aliases and type
            // eligibility are native-context concerns tested by its harness.
        }
        private static void InvalidScalarTypes()
        {
            foreach (string value in new[] { "null", "0", "1", "\"false\"", "[]", "{}" })
                Rejected("{" + Valid + ",\"dry_run\":" + value + "}", "invalid dry_run " + value);
            foreach (string value in new[] { "null", "true", "false", "\"1\"", "[]", "{}", "NaN", "Infinity", "-Infinity", "1e999" })
                Rejected("{" + Valid + ",\"clearance\":" + value + "}", "invalid clearance " + value);
            foreach (string value in new[] { "null", "true", "false", "\"50\"", "1.0", "[]", "{}", "NaN", "Infinity",
                "2147483648", "-2147483649", "99999999999999999999999999999999999" })
                Rejected("{" + Valid + ",\"result_limit\":" + value + "}", "invalid result_limit " + value);
        }
        internal static int Main()
        {
            ValidRequests(); InvalidJsonAndUnknownParameters(); InvalidArrayTypes(); InvalidScalarTypes();
            if (failures.Count != 0)
            {
                Console.Error.WriteLine("Native clash HTTP contract failed: " + failures.Count + " / " + checks);
                foreach (string failure in failures) Console.Error.WriteLine(failure);
                return 1;
            }
            Console.WriteLine("Native clash HTTP production checks passed: " + checks);
            return 0;
        }
    }
}
