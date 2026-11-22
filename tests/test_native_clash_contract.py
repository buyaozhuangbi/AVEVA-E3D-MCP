"""Run production native-clash orchestration against SDK fakes, without E3D."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_model_validation_native_contract import extract_method

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


class NativeClashContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline C# checks require dotnet.")
    def test_production_scope_results_and_resource_ownership(self):
        source = (BRIDGE / "ModelEditInput.cs").read_text(encoding="utf-8-sig")
        explicit_reference = extract_method(source.replace("    internal static", "    public static"), "ExplicitReference")
        policy_source = (BRIDGE / "E3d21SpatialMapStatus.cs").read_text(encoding="utf-8-sig")
        suspension_policy = extract_method(policy_source.replace("    private static", "    public static"), "NativeSpatialMapCheckAllowed")
        harness = (ROOT / "tests" / "e3d21" / "NativeClashHarness.cs").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="native-clash-contract-") as temporary:
            output = Path(temporary)
            for filename in ("E3d21NativeClash.cs", "NativeClashResources.cs", "NativeDatabaseFaultState.cs"):
                shutil.copyfile(BRIDGE / filename, output / filename)
            (output / "Harness.cs").write_text(harness.replace("// PRODUCTION_REFERENCE_VALIDATOR", explicit_reference)
                                               .replace("// PRODUCTION_SUSPENSION_POLICY", suspension_policy), encoding="utf-8")
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Native clash production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
