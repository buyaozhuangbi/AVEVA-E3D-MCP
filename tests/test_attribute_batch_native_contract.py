"""Exercise production batch attribute editing against an offline fake SDK."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"
HARNESS = ROOT / "tests" / "e3d21"


class AttributeBatchNativeContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "The offline C# contract requires dotnet.")
    def test_production_batch_attribute_editing(self):
        with tempfile.TemporaryDirectory(prefix="attribute-batch-native-") as directory:
            output = Path(directory)
            for source in (HARNESS / "AttributeBatchHarness.cs", BRIDGE / "E3d21AttributeBatchEditing.cs", BRIDGE / "AttributeEditPolicy.cs"):
                shutil.copyfile(source, output / source.name)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net10.0</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run(["dotnet", str(output / "bin" / "Release" / "net10.0" / "Offline.dll")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Attribute batch production contract checks passed:", ran.stdout)

    @unittest.skipUnless(shutil.which("dotnet"), "The offline C# HTTP contract requires dotnet.")
    def test_production_batch_http_route(self):
        path = HARNESS / "run-attribute-batch-http-harness.py"
        spec = spec_from_file_location("attribute_batch_http_contract_runner", path)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        if not (module.SDK / "Newtonsoft.Json.dll").is_file():
            self.skipTest("The installed E3D Newtonsoft.Json assembly is required.")
        module.main()


if __name__ == "__main__":
    unittest.main()
