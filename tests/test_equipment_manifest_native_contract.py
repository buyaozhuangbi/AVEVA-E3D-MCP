"""Exercise the E3D21 manifest route and reader with recording database fakes."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_model_validation_native_contract import block_end, extract_method


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"
SDK_JSON = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10")) / "Newtonsoft.Json.dll"


class EquipmentManifestNativeContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet") and SDK_JSON.is_file(), "Offline checks require dotnet and Newtonsoft.Json.")
    def test_manifest_route_and_reads_with_recording_sdk(self):
        adapter = (BRIDGE / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
        route = adapter.index('else if (method == "POST" && path == "/api/v1/equipment/manifest")')
        opening = adapter.index("{", route)
        error_start = adapter.index("int status = ex is ArgumentException || ex is JsonException ? 400 : 500;")
        error_handler = adapter.rindex("catch (Exception ex)", route, error_start)
        error_end = block_end(adapter, adapter.index("{", error_handler)) - 1
        reader = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        model = (BRIDGE / "E3d21ModelEditing.cs").read_text(encoding="utf-8-sig")
        harness = (ROOT / "tests/e3d21/EquipmentManifestHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_ROUTE", adapter[opening + 1:block_end(adapter, opening) - 1]
                                  .replace("typeof(E3d21HttpAdapter)", "typeof(ManifestRoute)"))
        harness = harness.replace("// PRODUCTION_HTTP_ERROR", adapter[error_start:error_end])
        harness = harness.replace("// PRODUCTION_HTTP_HELPERS", "\n\n".join(
            extract_method(adapter, name).replace("HttpListenerRequest request", "ManifestHttpRequest request", 1)
            for name in ("ReadUniqueBodyAsync", "ReadString", "ReadInteger")
        ))
        harness = harness.replace("// PRODUCTION_NATIVE_HELPERS", "\n\n".join(
            extract_method(source, name)
            for source, names in (
                (reader, ("NativeRead", "ThrowIfNativeFaulted", "OnUi")),
                (model, ("ModelReference", "ModelCurrentReference", "ModelCeUnchanged")),
            )
            for name in names
        ))
        with tempfile.TemporaryDirectory(prefix="equipment-manifest-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            for filename in ("EquipmentManifest.cs", "E3d21EquipmentManifest.cs", "ModelEditInput.cs",
                             "AttributeEditPolicy.cs", "NativeDatabaseFaultState.cs"):
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
            self.assertIn("Equipment manifest production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
