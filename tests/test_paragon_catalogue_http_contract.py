"""Execute the production PARAGON HTTP route and validators without E3D.

The network carrier is replaced by memory streams.  Route setup, final method
dispatch, error handlers, JSON readers and request validation remain production
code; no listener, SDK database, PML or bridge deployment participates.
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
ROUTE = 'else if (method == "POST" && path == "/api/v1/paragon/catalogue-manifest")'


def _selected_handler(adapter: str) -> str:
    """Remove unrelated successful routes, retaining the actual failure paths."""
    handler = extract_method(adapter, "HandleAsync")
    declaration_end = handler.index("object response;") + len("object response;")
    route_start = handler.index(ROUTE)
    opening = handler.index("{", route_start + len(ROUTE))
    route_end = block_end(handler, opening)
    last_route = handler.index('else if (method == "POST" && path == "/api/v1/command-batch")')
    last_opening = handler.index("{", last_route)
    fallback = block_end(handler, last_opening)
    if not handler[fallback:].lstrip().startswith("else"):
        raise AssertionError("The adapter method fallback moved; update the extraction boundary.")
    selected = handler[:declaration_end] + "\n            " + handler[route_start:route_end].replace(
        "else if", "if", 1) + "\n" + handler[fallback:]
    return selected.replace("HttpListenerContext request", "ParagonHttpRequest request", 1)


def _http_helpers(adapter: str, handler: str) -> str:
    """Include the production helper dependency closure used by this route."""
    declarations = re.findall(
        r"^    private static [^\n]+?\b([A-Za-z_]\w*)(?:<[^>]+>)?\(", adapter, re.MULTILINE)
    available = {name for name in declarations if name.startswith("Read") or name.startswith("WriteJson")}
    selected = {}
    pending = [handler]
    while pending:
        source = pending.pop()
        for name in sorted(available - selected.keys()):
            if not re.search(rf"\b{re.escape(name)}\s*\(", source):
                continue
            helper = extract_method(adapter, name)
            selected[name] = helper
            pending.append(helper)
    return "\n\n".join(selected.values()).replace(
        "HttpListenerRequest request", "ParagonHttpInnerRequest request").replace(
        "HttpListenerResponse response", "ParagonHttpResponse response")


class ParagonCatalogueHttpContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet") and NEWTONSOFT.is_file(),
                         "Offline checks require dotnet and the installed E3D Newtonsoft.Json assembly.")
    def test_production_route_and_offline_http_contract(self):
        adapter = (BRIDGE / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
        handler = _selected_handler(adapter)
        harness = (ROOT / "tests/e3d21/ParagonCatalogueHttpHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_HANDLER", handler).replace(
            "// PRODUCTION_HTTP_HELPERS", _http_helpers(adapter, handler))
        with tempfile.TemporaryDirectory(prefix="paragon-catalogue-http-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            # The other managed request classes are referenced only by the
            # adapter's unchanged shared error/method branches.
            names = {"ParagonCatalogueManifest.cs", "NativeDatabaseFaultState.cs", "MessageCursor.cs",
                     "EquipmentManifest.cs", "EquipmentChildSchema.cs", "PipingSpecificationReadRequest.cs", "TeamDatabaseIdentityManifest.cs", "ParagonComponentTemplate.cs", "ParagonReferenceTrace.cs", "ParagonComponentCopy.cs", "ParagonComponentParameters.cs", "ParagonComponentCreate.cs", "PipeMemberManifest.cs", "ModelEditInput.cs", "AttributeEditPolicy.cs"}
            for name in sorted(names):
                shutil.copyfile(BRIDGE / name, output / name)
            shutil.copyfile(NEWTONSOFT, output / NEWTONSOFT.name)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup>'
                '<ItemGroup><Reference Include="Newtonsoft.Json"><HintPath>' + escape(NEWTONSOFT.name) +
                '</HintPath><Private>True</Private></Reference></ItemGroup></Project>', encoding="utf-8")
            options = dict(cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   timeout=90, **options)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin/Release/net48/Offline.exe")], timeout=30, **options)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("PARAGON catalogue HTTP production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
