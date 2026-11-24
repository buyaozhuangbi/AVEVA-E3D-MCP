"""Execute the component-create HTTP boundary without loading the E3D SDK.

The production handler setup, health predicates, selected routes, complete
fallback/catch chain and JSON helper closure are compiled with the production
managed request/interface. Only the HTTP carrier and native context are fakes.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from xml.sax.saxutils import escape

from test_model_validation_native_contract import block_end, extract_method


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"
SDK = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10"))
NEWTONSOFT = SDK / "Newtonsoft.Json.dll"
ROUTES = (
    'if (method == "GET" && path == "/health")',
    'else if (method == "POST" && path == "/api/v1/paragon/component-create")',
    'else if (method == "GET" && path == "/api/v1/paragon/component-create/status")',
)
# Captured before component-create registration, from all 120 public tool
# definitions (including their descriptions and complete input schemas).
OLD_CATALOG_SHA256 = "90efe4ee3b16651726cd59931b6717f0efa98b3957e1746fb9fd7fe22e109b73"
NEW_TOOLS = {"e3d_paragon_component_create", "e3d_paragon_component_create_status"}


def _selected_handler(adapter: str) -> str:
    handler = extract_method(adapter, "HandleAsync")
    declaration_end = handler.index("object response;") + len("object response;")
    selected = []
    for marker in ROUTES:
        if marker not in handler:
            raise AssertionError("Production component-create HTTP route is not connected: " + marker)
        start = handler.index(marker)
        opening = handler.index("{", start + len(marker))
        selected.append(handler[start:block_end(handler, opening)])
    marker = 'else if (method == "POST" && path == "/api/v1/command-batch")'
    if marker not in handler:
        raise AssertionError("The adapter method fallback moved; update the extraction boundary.")
    fallback = block_end(handler, handler.index("{", handler.index(marker)))
    if not handler[fallback:].lstrip().startswith("else"):
        raise AssertionError("The adapter method fallback moved; update the extraction boundary.")
    # No flags, capabilities, fallbacks or catches are filtered. In particular,
    # a missing create-specific catch must run the forbidden diagnostic fake.
    return (handler[:declaration_end] + "\n            " + "\n            ".join(selected)
            + "\n" + handler[fallback:]).replace(
                "HttpListenerContext request", "ComponentCreateHttpRequest request", 1)


def _http_helpers(adapter: str, handler: str) -> str:
    declarations = re.findall(r"^    private static [^\n]+?\b([A-Za-z_]\w*)(?:<[^>]+>)?\(", adapter, re.MULTILINE)
    available = set(declarations)
    selected, pending = {}, [handler]
    while pending:
        source = pending.pop()
        for name in sorted(available - selected.keys()):
            if re.search(rf"\b{re.escape(name)}\s*\(", source):
                helper = extract_method(adapter, name)
                selected[name] = helper
                pending.append(helper)
    return "\n\n".join(selected.values()).replace(
        "HttpListenerRequest request", "ComponentCreateHttpInnerRequest request").replace(
        "HttpListenerResponse response", "ComponentCreateHttpResponse response")


class ParagonComponentCreateHttpContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet") and NEWTONSOFT.is_file(),
                         "Offline HTTP checks require dotnet and the installed Newtonsoft.Json assembly.")
    def test_production_route_features_validation_and_error_chain(self):
        adapter = (BRIDGE / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
        handler = _selected_handler(adapter)
        harness = (ROOT / "tests/e3d21/ParagonComponentCreateHttpHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_HANDLER", handler).replace(
            "// PRODUCTION_HTTP_HELPERS", _http_helpers(adapter, handler))
        names = (
            "TeamDatabaseIdentityManifest.cs", "ParagonComponentTemplate.cs", "ParagonReferenceTrace.cs",
            "ParagonComponentCopy.cs", "ParagonComponentParameters.cs", "ParagonComponentCreate.cs",
            "PipingSpecificationReadRequest.cs", "ConnectivitySnapshot.cs", "ModuleContext.cs",
            "ParagonCatalogueSchema.cs", "ParagonCatalogueManifest.cs", "NativeDatabaseFaultState.cs",
            "MessageCursor.cs", "EquipmentManifest.cs", "EquipmentChildSchema.cs", "PipeMemberManifest.cs",
            "ModelEditInput.cs", "AttributeEditPolicy.cs",
        )
        with tempfile.TemporaryDirectory(prefix="paragon-component-create-http-") as temporary:
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
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   timeout=90, **options)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin/Release/net48/Offline.exe")], timeout=30, **options)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertRegex(ran.stdout, r"PARAGON component create HTTP production checks passed: [1-9][0-9]*")
            print(ran.stdout.strip())

    def test_component_create_preserves_all_120_existing_tool_definitions(self):
        sys.path.insert(0, str(ROOT / "src"))
        from aveva_e3d_mcp.config import Settings
        from aveva_e3d_mcp.tools import create_server
        from catalog_compatibility import OPTIONAL_MODELING_FIELDS, with_modeling_additions

        server = create_server(Settings(mode="mock"))
        response = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        current = response["result"]["tools"]
        by_name = {tool["name"]: tool for tool in current}
        self.assertEqual(len(current), len(by_name), "Public tool names must be unique.")
        self.assertTrue(NEW_TOOLS <= by_name.keys())
        previous = [tool for tool in current if tool["name"] not in NEW_TOOLS]
        self.assertEqual(len(previous), 120)
        self.assertEqual(len(current), 122)
        baseline = json.loads((ROOT / "tests/fixtures/paragon_standard_generation_catalog119.json").read_bytes())
        predecessors = {row["name"]: row for row in baseline["tools"] if row["name"] in OPTIONAL_MODELING_FIELDS}
        for name, definition in predecessors.items():
            self.assertEqual(by_name[name], with_modeling_additions(definition))
        previous = [predecessors.get(tool["name"], tool) for tool in previous]
        canonical = json.dumps(sorted(previous, key=lambda tool: tool["name"]),
                               sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), OLD_CATALOG_SHA256,
                         "Creating from PTREF/GMREF evidence must preserve every old public tool definition.")


if __name__ == "__main__":
    unittest.main()
