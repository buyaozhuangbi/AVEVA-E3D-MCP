"""Run the piping connectivity-snapshot HTTP route entirely offline.

The test extracts the production HandleAsync route, health capability list,
fallback/catch chain and the bounded JSON helpers into a managed harness.  A
recording optional context stands in for the native SDK, so malformed input,
unavailable capabilities, diagnostics and native faults can be checked without
opening an E3D database or issuing model writes.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from xml.sax.saxutils import escape

from http_contract_capabilities import append_element_ancestry_interface
from test_model_validation_native_contract import block_end, extract_method


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"
SDK = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10"))
NEWTONSOFT = SDK / "Newtonsoft.Json.dll"


def _route_block(source: str, marker: str) -> str:
    """Return one selected route body, preserving its if/else-if prefix."""
    start = source.index(marker)
    opening = source.index("{", start + len(marker))
    return source[start:block_end(source, opening)]


def _selected_handler(adapter: str) -> str:
    handler = extract_method(adapter, "HandleAsync")
    declaration_end = handler.index("object response;") + len("object response;")
    markers = (
        'if (method == "GET" && path == "/health")',
        'else if (method == "POST" && path == "/api/v1/piping/connectivity-snapshot")',
    )
    for marker in markers:
        if marker not in handler:
            raise AssertionError("Production connectivity HTTP route is not connected: " + marker)
    selected = [_route_block(handler, marker) for marker in markers]
    # Preserve the production method fallback and every route-specific catch.
    fallback_marker = 'else if (method == "POST" && path == "/api/v1/command-batch")'
    if fallback_marker not in handler:
        raise AssertionError("The adapter method fallback moved; update the extraction boundary.")
    fallback_opening = handler.index("{", handler.index(fallback_marker))
    fallback_end = block_end(handler, fallback_opening)
    if not handler[fallback_end:].lstrip().startswith("else"):
        raise AssertionError("The adapter method fallback moved; update the extraction boundary.")
    return (handler[:declaration_end] + "\n            " + "\n            ".join(selected) +
            "\n" + handler[fallback_end:]).replace(
                "HttpListenerContext request", "ConnectivityHttpRequest request", 1)


def _http_helpers(adapter: str, handler: str) -> str:
    declarations = re.findall(
        r"^    private static [^\n]+?\b([A-Za-z_]\w*)(?:<[^>]+>)?\(",
        adapter, re.MULTILINE)
    available = {
        name for name in declarations
        if name.startswith(("Read", "WriteJson", "SanitizeDiagnostic"))
    }
    selected, pending = {}, [handler]
    while pending:
        source = pending.pop()
        for name in sorted(available - selected.keys()):
            if re.search(rf"\b{re.escape(name)}\s*\(", source):
                helper = extract_method(adapter, name)
                selected[name] = helper
                pending.append(helper)
    return "\n\n".join(selected.values()).replace(
        "HttpListenerRequest request", "ConnectivityHttpInnerRequest request").replace(
        "HttpListenerResponse response", "ConnectivityHttpResponse response")


class ConnectivitySnapshotHttpContractTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("dotnet") and NEWTONSOFT.is_file(),
        "Offline HTTP checks require dotnet and the installed Newtonsoft.Json assembly.",
    )
    def test_production_route_features_validation_and_error_chain(self):
        adapter = (BRIDGE / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
        handler = _selected_handler(adapter)
        harness = (ROOT / "tests" / "e3d21" / "ConnectivitySnapshotHttpHarness.cs").read_text(
            encoding="utf-8"
        )
        harness = harness.replace("// PRODUCTION_HANDLER", handler).replace(
            "// PRODUCTION_HTTP_HELPERS", _http_helpers(adapter, handler)
        )
        harness = append_element_ancestry_interface(adapter, harness)
        with tempfile.TemporaryDirectory(prefix="connectivity-snapshot-http-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            for name in (
                "ConnectivitySnapshot.cs",
                "ModuleContext.cs",
                "ParagonCatalogueSchema.cs",
                "ParagonCatalogueManifest.cs",
                "NativeDatabaseFaultState.cs",
                "MessageCursor.cs",
                "EquipmentManifest.cs",
                "EquipmentChildSchema.cs", "PipingSpecificationReadRequest.cs", "TeamDatabaseIdentityManifest.cs", "ParagonComponentTemplate.cs", "ParagonReferenceTrace.cs", "ParagonComponentCopy.cs", "ParagonComponentParameters.cs", "ParagonComponentCreate.cs",
                "PipeMemberManifest.cs",
                "ModelEditInput.cs",
                "AttributeEditPolicy.cs",
            ):
                shutil.copyfile(BRIDGE / name, output / name)
            shutil.copyfile(NEWTONSOFT, output / NEWTONSOFT.name)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                '<TargetFramework>net48</TargetFramework><OutputType>Exe</OutputType>'
                '<LangVersion>latest</LangVersion></PropertyGroup>'
                '<ItemGroup><Reference Include="Newtonsoft.Json"><HintPath>'
                + escape(NEWTONSOFT.name)
                + '</HintPath><Private>True</Private></Reference></ItemGroup></Project>',
                encoding="utf-8",
            )
            options = dict(
                cwd=output,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            built = subprocess.run(
                ["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                timeout=90,
                **options,
            )
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run(
                [str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                timeout=30,
                **options,
            )
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertRegex(
                ran.stdout,
                r"CONNECTIVITY snapshot HTTP production checks passed: [1-9][0-9]*",
            )
            print(ran.stdout.strip())


if __name__ == "__main__":
    unittest.main()
