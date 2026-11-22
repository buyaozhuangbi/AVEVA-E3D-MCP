"""Run the production snapshot and bounded walker against recording SDK readers."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aveva_e3d_mcp.piping_connectivity_snapshot import connectivity_snapshot_parameters, connectivity_snapshot_report
BRIDGE = ROOT / "bridge/e3d21"
NEWTONSOFT = Path(r"C:\Program Files (x86)\AVEVA\Everything3D2.10\Newtonsoft.Json.dll")


class ConnectivitySnapshotNativeContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet") and NEWTONSOFT.is_file(), "Requires dotnet and the local Newtonsoft assembly")
    def test_production_batch_has_one_dispatch_exact_budgets_and_typed_context(self):
        with tempfile.TemporaryDirectory(prefix="connectivity-snapshot-native-") as temporary:
            output = Path(temporary)
            for source in (BRIDGE / "ConnectivitySnapshot.cs", BRIDGE / "E3d21ConnectivitySnapshot.cs",
                           ROOT / "tests/e3d21/ConnectivitySnapshotHarness.cs"):
                shutil.copyfile(source, output / source.name)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup>'
                '<ItemGroup><Reference Include="Newtonsoft.Json"><HintPath>' + escape(str(NEWTONSOFT)) +
                '</HintPath></Reference></ItemGroup></Project>', encoding="utf-8")
            options = dict(cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"], timeout=90, **options)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin/Release/net48/Offline.exe")], timeout=30, **options)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Connectivity snapshot native checks passed:", ran.stdout)
            receipt = json.loads(next(line.removeprefix("SNAPSHOT_RECEIPT:") for line in ran.stdout.splitlines() if line.startswith("SNAPSHOT_RECEIPT:")))
            branch, pair, trace = receipt["result"]["branches"][0], receipt["result"]["pairs"][0], receipt["result"]["trace"]
            request = connectivity_snapshot_parameters({"branches": [branch["requested"]], "pairs": [pair["requested"]], **trace["requested"]})
            report = connectivity_snapshot_report(receipt, request)
            self.assertTrue(report["complete"], report)
            self.assertIn("reciprocal", report["markdown"])
            self.assertIn("[TUBI] P2", report["markdown"])


if __name__ == "__main__":
    unittest.main()
