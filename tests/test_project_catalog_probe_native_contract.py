"""Run the E3D21 project catalog route and reader against recording SDK fakes."""

import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest

from test_model_validation_native_contract import block_end


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"
SDK_JSON = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10")) / "Newtonsoft.Json.dll"


class ProjectCatalogProbeNativeContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet") and SDK_JSON.is_file(), "Offline checks require dotnet and Newtonsoft.Json.")
    def test_production_route_and_catalog_observation(self):
        adapter = (BRIDGE / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
        marker = 'else if (method == "POST" && path == "/api/v1/database/project-catalog-probe")'
        opening = adapter.index("{", adapter.index(marker) + len(marker))
        harness = (ROOT / "tests/e3d21/ProjectCatalogProbeHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_ROUTE", adapter[opening + 1:block_end(adapter, opening) - 1])
        extract = runpy.run_path(str(ROOT / "tests" / "test_native_fault_barriers.py"))["method"]
        probe = (BRIDGE / "E3d21ProjectCatalogProbe.cs").read_text(encoding="utf-8-sig")
        harness = harness.replace("// PRODUCTION_CONTEXT_FIELDS", "\n".join(
            line for line in probe.splitlines() if line.startswith("    private const int ProjectCatalog")))
        names = re.findall(r"^    private static [^\n]+?\b([A-Za-z_]\w*)\(", probe, re.MULTILINE)
        native_methods = [extract(probe, name) for name in ("GetProjectCatalogProbe", *names)]
        harness = harness.replace("// PRODUCTION_CONTEXT_METHODS", "\n\n".join(native_methods))
        harness = harness.replace("// PRODUCTION_HTTP_HELPERS", "\n\n".join(
            extract(adapter, name).replace("HttpListenerRequest request", "CatalogHttpRequest request", 1)
            for name in ("ReadUniqueBodyAsync", "ReadString", "ReadInteger")
        ))
        harness = harness.replace("// PRODUCTION_NATIVE_HELPERS", "\n\n".join(
            extract((BRIDGE / filename).read_text(encoding="utf-8-sig"), name)
            for filename, names in (
                ("E3d21Context.cs", ("NativeRead", "ThrowIfNativeFaulted", "OnUi")),
                ("E3d21DatabaseState.cs", ("DatabaseStateContext", "DatabaseStateRead", "DatabaseStateError")),
                ("E3d21ModelEditing.cs", ("ModelCurrentReference", "ModelCeUnchanged", "ModelReference")),
            )
            for name in names
        ))
        with tempfile.TemporaryDirectory(prefix="project-catalog-probe-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            for filename in ("DatabaseStateRequest.cs", "NativeDatabaseFaultState.cs",
                             "ModelEditInput.cs", "AttributeEditPolicy.cs"):
                shutil.copyfile(BRIDGE / filename, output / filename)
            shutil.copyfile(SDK_JSON, output / "Newtonsoft.Json.dll")
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup>'
                '<ItemGroup><Reference Include="Newtonsoft.Json"><HintPath>Newtonsoft.Json.dll</HintPath>'
                '</Reference></ItemGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin/Release/net48/Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Project catalog probe production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
