"""Execute production spatial-map query control flow against managed SDK fakes."""
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


class SpatialMapStatusContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline C# checks require dotnet.")
    def test_production_resolution_status_semantics_and_fault_barriers(self):
        extract = runpy.run_path(str(ROOT / "tests" / "test_native_fault_barriers.py"))["method"]
        context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        database_state = (BRIDGE / "E3d21DatabaseState.cs").read_text(encoding="utf-8-sig")
        methods = [extract(context, name) for name in ("NativeRead", "ThrowIfNativeFaulted", "OnUi")]
        methods.extend(extract(database_state, name) for name in ("DatabaseStateContext", "DatabaseStateRead", "DatabaseStateError"))
        methods.append(extract((BRIDGE / "E3d21ModelEditing.cs").read_text(encoding="utf-8-sig"), "ModelCeUnchanged"))
        harness = (ROOT / "tests" / "e3d21" / "SpatialMapStatusHarness.cs").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="spatial-map-status-contract-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness.replace("// PRODUCTION_METHODS", "\n\n".join(methods)), encoding="utf-8")
            for filename in ("E3d21SpatialMapStatus.cs", "DatabaseStateRequest.cs", "NativeDatabaseFaultState.cs"):
                shutil.copyfile(BRIDGE / filename, output / filename)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Spatial map status production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
