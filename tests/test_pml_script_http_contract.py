"""Compile the production PML script JSON parser and HTTP branch offline."""
from pathlib import Path
import os
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


class PmlScriptHttpContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet") and NEWTONSOFT.is_file(),
                         "Offline checks require dotnet and the installed E3D Newtonsoft.Json assembly.")
    def test_production_unicode_validation_and_guarded_http_route(self):
        adapter = (BRIDGE / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
        marker = 'else if (method == "POST" && path == "/api/v1/pml-script")'
        opening = adapter.index("{", adapter.index(marker) + len(marker))
        route = adapter[opening + 1:block_end(adapter, opening) - 1]
        status_marker = 'else if (method == "GET" && path == "/api/v1/pml-script/status")'
        status_opening = adapter.index("{", adapter.index(status_marker) + len(status_marker))
        status_route = adapter[status_opening + 1:block_end(adapter, status_opening) - 1]
        methods = [extract_method(adapter, name) for name in ("ReadPmlScriptBodyAsync", "ReadString", "ReadBoolean", "ReadPmlRequestId")]
        # Substitute only the network carrier type. The exact production body
        # reader still performs bounded async stream reads and calls Parse.
        methods[0] = methods[0].replace("HttpListenerRequest request", "RouteInnerRequest request")
        harness = (ROOT / "tests" / "e3d21" / "PmlScriptHttpHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_ROUTE", route).replace("// PRODUCTION_STATUS_ROUTE", status_route).replace("// PRODUCTION_METHODS", "\n\n".join(methods))
        with tempfile.TemporaryDirectory(prefix="pml-script-http-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            for filename in ("PmlScriptJson.cs", "PmlScriptInput.cs", "PmlScriptReceiptStore.cs"):
                shutil.copyfile(BRIDGE / filename, output / filename)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup>'
                '<ItemGroup><Reference Include="Newtonsoft.Json"><HintPath>' + escape(str(NEWTONSOFT)) +
                '</HintPath><Private>True</Private></Reference></ItemGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("PML script HTTP production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
