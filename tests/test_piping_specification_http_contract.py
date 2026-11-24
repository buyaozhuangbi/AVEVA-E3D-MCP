"""Execute the production specification HTTP route without an E3D process.

Only the adapter's selected route, /health capability predicates, complete
method fallback/catch chain and JSON helper closure are compiled. Managed
recording contexts replace the optional native implementation; no listener,
AVEVA assembly, PML command or database is opened.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
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
    'else if (method == "POST" && path == "/api/v1/piping/specification-read")',
)


def _selected_handler(adapter: str) -> str:
    handler = extract_method(adapter, "HandleAsync")
    declaration_end = handler.index("object response;") + len("object response;")
    selected = []
    for marker in ROUTES:
        if marker not in handler:
            raise AssertionError("Production piping specification HTTP route is not connected: " + marker)
        start = handler.index(marker)
        opening = handler.index("{", start + len(marker))
        selected.append(handler[start:block_end(handler, opening)])
    # Every production catch participates. Removing the dedicated catch must
    # expose the forbidden fallback diagnostics to the executable tests.
    marker = 'else if (method == "POST" && path == "/api/v1/command-batch")'
    if marker not in handler:
        raise AssertionError("The adapter method fallback moved; update the extraction boundary.")
    fallback = block_end(handler, handler.index("{", handler.index(marker)))
    if not handler[fallback:].lstrip().startswith("else"):
        raise AssertionError("The adapter method fallback moved; update the extraction boundary.")
    return (handler[:declaration_end] + "\n            " + "\n            ".join(selected)
            + "\n" + handler[fallback:]).replace(
                "HttpListenerContext request", "SpecificationHttpRequest request", 1)


def _http_helpers(adapter: str, handler: str) -> str:
    declarations = re.findall(r"^    private static [^\n]+?\b([A-Za-z_]\w*)(?:<[^>]+>)?\(", adapter, re.MULTILINE)
    available = {name for name in declarations
                 if name.startswith(("Read", "WriteJson", "SanitizeDiagnostic"))}
    selected, pending = {}, [handler]
    while pending:
        source = pending.pop()
        for name in sorted(available - selected.keys()):
            if re.search(rf"\b{re.escape(name)}\s*\(", source):
                helper = extract_method(adapter, name)
                selected[name] = helper
                pending.append(helper)
    return "\n\n".join(selected.values()).replace(
        "HttpListenerRequest request", "SpecificationHttpInnerRequest request").replace(
        "HttpListenerResponse response", "SpecificationHttpResponse response")


class PipingSpecificationHttpContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet") and NEWTONSOFT.is_file(),
                         "Offline HTTP checks require dotnet and the installed Newtonsoft.Json assembly.")
    def test_production_route_features_validation_and_error_chain(self):
        adapter = (BRIDGE / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
        handler = _selected_handler(adapter)
        harness = (ROOT / "tests/e3d21/PipingSpecificationHttpHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_HANDLER", handler).replace(
            "// PRODUCTION_HTTP_HELPERS", _http_helpers(adapter, handler))
        # The managed production request/interface supplies validation. The SDK
        # reader is intentionally absent from this project and from its inputs.
        names = (
            "PipingSpecificationReadRequest.cs", "TeamDatabaseIdentityManifest.cs", "ParagonComponentTemplate.cs", "ParagonReferenceTrace.cs", "ParagonComponentCopy.cs", "ParagonComponentParameters.cs", "ParagonComponentCreate.cs", "ConnectivitySnapshot.cs", "ModuleContext.cs",
            "ParagonCatalogueSchema.cs", "ParagonCatalogueManifest.cs", "NativeDatabaseFaultState.cs",
            "MessageCursor.cs", "EquipmentManifest.cs", "EquipmentChildSchema.cs", "PipeMemberManifest.cs", "ModelEditInput.cs",
            "AttributeEditPolicy.cs",
        )
        with tempfile.TemporaryDirectory(prefix="piping-specification-http-") as temporary:
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
            self.assertRegex(ran.stdout, r"PIPING specification HTTP production checks passed: [1-9][0-9]*")
            print(ran.stdout.strip())


if __name__ == "__main__":
    unittest.main()
