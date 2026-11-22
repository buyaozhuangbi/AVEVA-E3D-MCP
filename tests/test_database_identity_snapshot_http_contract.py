"""Exercise the production database identity snapshot HTTP branch."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_model_validation_native_contract import block_end


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


class DatabaseIdentitySnapshotHttpContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline checks require dotnet.")
    def test_production_route_validates_context_before_native_call(self):
        adapter = (BRIDGE / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
        self.assertIn('"database_identity_snapshot"', adapter)
        marker = 'else if (method == "GET" && path == "/api/v1/database/identity-snapshot")'
        opening = adapter.index("{", adapter.index(marker) + len(marker))
        route = adapter[opening + 1:block_end(adapter, opening) - 1]
        harness = (ROOT / "tests/e3d21/DatabaseIdentitySnapshotHttpHarness.cs").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="database-identity-snapshot-http-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness.replace("// PRODUCTION_ROUTE", route), encoding="utf-8")
            shutil.copyfile(BRIDGE / "DatabaseStateRequest.cs", output / "DatabaseStateRequest.cs")
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin/Release/net48/Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Database identity snapshot HTTP checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
