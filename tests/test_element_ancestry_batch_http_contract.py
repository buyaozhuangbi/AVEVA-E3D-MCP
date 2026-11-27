"""Exercise the optional native ancestry-batch HTTP route without E3D."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from xml.sax.saxutils import escape

from http_contract_capabilities import append_element_ancestry_interface
from test_element_ancestry_http_contract import _http_helpers
from test_model_validation_native_contract import block_end, extract_method

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"
SDK = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10"))
NEWTONSOFT = SDK / "Newtonsoft.Json.dll"


def _handler(adapter: str) -> str:
    source = extract_method(adapter, "HandleAsync")
    declaration_end = source.index("object response;") + len("object response;")
    markers = [
        'if (method == "GET" && path == "/health")',
        'else if (method == "POST" && path == "/api/v1/element/ancestry-batch")',
    ]
    selected = []
    for marker in markers:
        if marker not in source:
            raise AssertionError("Production batch route is not connected: " + marker)
        start = source.index(marker)
        selected.append(source[start:block_end(source, source.index("{", start + len(marker)))])
    fallback_marker = 'else if (method == "POST" && path == "/api/v1/command-batch")'
    fallback = block_end(source, source.index("{", source.index(fallback_marker)))
    if not source[fallback:].lstrip().startswith("else"):
        raise AssertionError("The adapter method fallback moved; update extraction boundary.")
    return (source[:declaration_end] + "\n            " + "\n            ".join(selected)
            + "\n" + source[fallback:]).replace(
                "HttpListenerContext request", "ElementAncestryHttpRequest request", 1)


class ElementAncestryBatchHttpContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet") and NEWTONSOFT.is_file(),
                         "Offline HTTP checks require dotnet and Newtonsoft.Json.")
    def test_batch_route_validation_capability_and_error_chain(self):
        adapter = (BRIDGE / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
        handler = _handler(adapter)
        source = (ROOT / "tests/e3d21/ElementAncestryHttpHarness.cs").read_text(encoding="utf-8")
        prefix = source[:source.index("internal static class ElementAncestryHttpHarness")]
        prefix = prefix.replace(
            "internal object Result = new { operation = \"element_ancestry\", available = true,",
            "internal string[] BatchRefnos; internal int BatchMaxDepth; internal int BatchCalls;\n"
            "    internal object Result = new { operation = \"element_ancestry\", available = true,")
        prefix = prefix.replace(
            "    public object ElementAncestry(string refno, int maxDepth)\n    {",
            "    public object ElementAncestryBatch(string[] refnos, int maxDepth)\n    {\n"
            "        BatchCalls++; BatchRefnos = refnos; BatchMaxDepth = maxDepth;\n"
            "        if (Failure != null) throw Failure; return new { operation = \"element_ancestry_batch\", "
            "available = true, success = true, complete = true, marker = \"native-element-ancestry-batch\" };\n    }\n\n"
            "    public object ElementAncestry(string refno, int maxDepth)\n    {")
        prefix = prefix.replace(
            "internal sealed class ElementAncestryOnlyContext : ElementAncestryBaseContext, IElementAncestryContext { }",
            "internal sealed class ElementAncestryOnlyContext : ElementAncestryBaseContext, IElementAncestryContext, IElementAncestryBatchContext { }\n"
            "internal sealed class ElementAncestrySingleContext : ElementAncestryBaseContext, IElementAncestryContext { }")
        harness = prefix.replace("// PRODUCTION_HANDLER", handler).replace(
            "// PRODUCTION_HTTP_HELPERS", _http_helpers(adapter, handler))
        harness = append_element_ancestry_interface(adapter, harness)
        harness += r'''

internal static class ElementAncestryBatchHttpHarness
{
    private static int checks;
    private static void Check(bool value, string message)
    { checks++; if (!value) throw new Exception(message); }
    private static ElementAncestryHttpResponse Run(ElementAncestryHttpRequest request,
        ElementAncestryBaseContext context, bool health = false)
    {
        var adapter = new E3d21HttpAdapter(context);
        adapter.Invoke(request).GetAwaiter().GetResult();
        Check(adapter.FallbackDiagnosticsCalls == 0, "batch route must not invoke fallback diagnostics");
        Check(context.CommandStateCalls == (health ? 1 : 0) && context.ConnectedCalls == (health ? 1 : 0),
            "only health observes command state and connection");
        Check(request.Response.CloseCalls == 1 && request.Response.ContentType == "application/json; charset=utf-8" &&
            request.Response.ContentLength64 == request.Response.OutputStream.Length, "wire response is complete");
        JObject json = request.Response.Json;
        Check((string)json["version"] == "E3D21" && (string)json["session_id"] == E3d21HttpAdapter.SessionId,
            "version/session envelope is bound");
        Check((string)json["bridge_build"] == typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
            "bridge build is current");
        Check((bool?)json["ok"] == (request.Response.StatusCode == 200), "ok matches status");
        return request.Response;
    }
    private static string Body(object value) { return JsonConvert.SerializeObject(value); }
    private static void Accept(string body, string[] refs, int depth)
    {
        var context = new ElementAncestryOnlyContext();
        var response = Run(new ElementAncestryHttpRequest(body, path: "/api/v1/element/ancestry-batch"), context);
        Check(response.StatusCode == 200 && context.BatchCalls == 1, "valid batch invokes native once");
        Check(context.BatchMaxDepth == depth && context.BatchRefnos.SequenceEqual(refs), "batch values preserve order");
        Check((string)response.Json["result"]["marker"] == "native-element-ancestry-batch", "native batch result preserved");
    }
    private static void Reject(string body, string label)
    {
        var context = new ElementAncestryOnlyContext();
        var response = Run(new ElementAncestryHttpRequest(body, path: "/api/v1/element/ancestry-batch"), context);
        Check(response.StatusCode == 400 && context.BatchCalls == 0, label + " rejects before native");
    }
    public static int Main()
    {
        try
        {
            Accept("{\"refnos\":[\"=7/101\",\"=7/102\"],\"max_depth\":1}", new[] { "=7/101", "=7/102" }, 1);
            Accept(Body(new { refnos = new[] { "=1/0", "=2147483647/2147483647" } }),
                new[] { "=1/0", "=2147483647/2147483647" }, 32);
            Accept(Body(new { refnos = Enumerable.Range(1, 16).Select(index => "=1/" + index).ToArray() }),
                Enumerable.Range(1, 16).Select(index => "=1/" + index).ToArray(), 32);
            Reject(Body(new { refnos = Enumerable.Range(1, 17).Select(index => "=1/" + index).ToArray() }),
                "more than sixteen refs");
            foreach (string value in new[] { "", "=01/1", "=1/00", "=0/1", "CE", "/NAME", "=1/2147483648" })
                Reject(Body(new { refnos = new[] { value } }), "invalid canonical ref");
            foreach (string value in new[] { "null", "true", "1", "1.0", "{}", "[[\"=1/1\"]]", "[null]" })
                Reject("{\"refnos\":" + value + "}", "refnos must be string array");
            Reject("{\"refnos\":[]}", "empty array");
            Reject("{\"refnos\":[\"=1/1\",\"=1/1\"]}", "duplicate refs");
            Reject("{\"refnos\":[\"=1/1\"],\"refn\\u006fs\":[\"=1/2\"]}", "duplicate decoded key");
            Reject("{\"refnos\":[\"=1/1\"],\"max_depth\":1,\"max_depth\":32}", "duplicate max_depth");
            Reject("{\"refnos\":[\"=1/1\"],\"max_depth\":0}", "depth lower bound");
            Reject("{\"refnos\":[\"=1/1\"],\"max_depth\":65}", "depth upper bound");
            Reject("{\"refnos\":[\"=1/1\"],\"unexpected\":true}", "unknown key");
            Reject("null", "non-object JSON");
            Reject("not JSON", "malformed JSON");
            var queryRequest = new ElementAncestryHttpRequest("not JSON", query: new NameValueCollection { { "max_depth", "3" } },
                path: "/api/v1/element/ancestry-batch");
            var queryContext = new ElementAncestryOnlyContext();
            var queryResponse = Run(queryRequest, queryContext);
            Check(queryResponse.StatusCode == 400 && queryContext.BatchCalls == 0 && queryRequest.Request.InputStream.BytesRead == 0,
                "query parameters are rejected before parsing or native access");
            foreach (string method in new[] { "GET", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS" })
            {
                var context = new ElementAncestryOnlyContext();
                var request = new ElementAncestryHttpRequest("not JSON", method, path: "/api/v1/element/ancestry-batch");
                var response = Run(request, context);
                Check(response.StatusCode == 405 && response.Headers["Allow"] == "POST" && context.BatchCalls == 0,
                    "wrong method is 405 Allow POST without native");
            }
            var unavailable = new ElementAncestryBaseContext();
            var missing = Run(new ElementAncestryHttpRequest("not JSON", path: "/api/v1/element/ancestry-batch"), unavailable);
            Check(missing.StatusCode == 404 && unavailable.BatchCalls == 0 && missing.Json["error"] != null,
                "optional batch capability is explicit unavailable");
            var singleOnly = new ElementAncestrySingleContext();
            var singleOnlyResponse = Run(new ElementAncestryHttpRequest("not JSON", path: "/api/v1/element/ancestry-batch"), singleOnly);
            Check(singleOnlyResponse.StatusCode == 404 && singleOnly.BatchCalls == 0,
                "the batch route never falls back to a single-item ancestry capability");
            var healthContext = new ElementAncestryOnlyContext();
            var health = Run(new ElementAncestryHttpRequest(method: "GET", path: "/health"), healthContext, true);
            var healthFeatures = ((JArray)health.Json["api_features"]).Values<string>().ToArray();
            Check(healthFeatures.Contains("element_ancestry_batch") &&
                healthFeatures.Count(feature => feature == "element_ancestry_batch") == 1 &&
                healthFeatures.Length == healthFeatures.Distinct(StringComparer.Ordinal).Count(),
                "health advertises batch only when interface is implemented");
            var singleOnlyHealth = Run(new ElementAncestryHttpRequest(method: "GET", path: "/health"),
                new ElementAncestrySingleContext(), true);
            Check(!((JArray)singleOnlyHealth.Json["api_features"]).Values<string>().Contains("element_ancestry_batch"),
                "health hides batch when only the single-item interface exists");
            var failed = new ElementAncestryOnlyContext { Failure = new NativeDatabaseFaultException("batch fault") };
            var error = Run(new ElementAncestryHttpRequest("{\"refnos\":[\"=1/1\"]}", path: "/api/v1/element/ancestry-batch"), failed);
            Check(error.StatusCode == 500 && (string)error.Json["error_code"] == "native_database_fault" &&
                (bool?)error.Json["native_fault_latched"] == true && failed.BatchCalls == 1,
                "native fault envelope is explicit and has no fallback");
            Console.WriteLine("Element ancestry batch HTTP production checks passed: " + checks);
            return 0;
        }
        catch (Exception failure) { Console.Error.WriteLine(failure); return 1; }
    }
}
'''
        names = (
            "TeamDatabaseIdentityManifest.cs", "ParagonComponentTemplate.cs", "ParagonReferenceTrace.cs",
            "ParagonComponentCopy.cs", "ParagonComponentParameters.cs", "ParagonComponentCreate.cs",
            "PipingSpecificationReadRequest.cs", "ConnectivitySnapshot.cs", "ModuleContext.cs",
            "ParagonCatalogueSchema.cs", "ParagonCatalogueManifest.cs", "NativeDatabaseFaultState.cs",
            "MessageCursor.cs", "EquipmentManifest.cs", "EquipmentChildSchema.cs", "PipeMemberManifest.cs",
            "ModelEditInput.cs", "AttributeEditPolicy.cs",
        )
        with tempfile.TemporaryDirectory(prefix="element-ancestry-batch-http-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            for name in names:
                shutil.copyfile(BRIDGE / name, output / name)
            shutil.copyfile(NEWTONSOFT, output / NEWTONSOFT.name)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup>'
                '<ItemGroup><Reference Include="Newtonsoft.Json"><HintPath>' + escape(NEWTONSOFT.name)
                + '</HintPath><Private>True</Private></Reference></ItemGroup></Project>', encoding="utf-8")
            options = dict(cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"], timeout=90, **options)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin/Release/net48/Offline.exe")], timeout=30, **options)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertRegex(ran.stdout, r"Element ancestry batch HTTP production checks passed: [1-9][0-9]*")
