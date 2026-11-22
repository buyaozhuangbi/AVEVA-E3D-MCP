"""Exercise the pure managed session receipt store with no E3D process."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[1]
NEWTONSOFT = Path(r"C:\Program Files (x86)\AVEVA\Everything3D2.10\Newtonsoft.Json.dll")


class PmlScriptReceiptStoreTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet") and NEWTONSOFT.exists(),
                         "Offline checks require dotnet and the installed Newtonsoft.Json assembly.")
    def test_production_store_lifecycle_limits_and_concurrency(self):
        with tempfile.TemporaryDirectory(prefix="pml-receipt-store-") as temporary:
            output = Path(temporary)
            shutil.copyfile(ROOT / "bridge" / "e3d21" / "PmlScriptReceiptStore.cs", output / "PmlScriptReceiptStore.cs")
            shutil.copyfile(ROOT / "tests" / "e3d21" / "PmlScriptReceiptStoreHarness.cs", output / "Harness.cs")
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
            self.assertIn("PML receipt store checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
