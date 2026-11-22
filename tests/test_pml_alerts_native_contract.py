"""Exercise the production managed-alert helper without E3D or visible forms."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PmlAlertsNativeContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "The offline C# checks require dotnet.")
    def test_exact_alert_policy_dispatch_deadlines_and_stale_targets(self):
        with tempfile.TemporaryDirectory(prefix="pml-alert-") as temporary:
            output = Path(temporary)
            shutil.copyfile(ROOT / "bridge/e3d21/E3d21PmlAlerts.cs", output / "PmlAlerts.cs")
            shutil.copyfile(ROOT / "tests/e3d21/PmlAlertsHarness.cs", output / "Harness.cs")
            # The fake official form lives in a DruidNet-named test assembly so
            # production assembly/type identity checks run unchanged. No real
            # WinForms or AVEVA assembly is loaded by this managed-only harness.
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                '<TargetFramework>net48</TargetFramework><OutputType>Exe</OutputType>'
                '<AssemblyName>DruidNet</AssemblyName><LangVersion>latest</LangVersion>'
                '</PropertyGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin/Release/net48/DruidNet.exe")], cwd=output,
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("PML alert production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
