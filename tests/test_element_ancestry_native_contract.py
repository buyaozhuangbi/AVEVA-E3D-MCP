"""Run the production E3D21 ancestry reader against recording SDK fakes."""
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


class ElementAncestryNativeContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline C# checks require dotnet.")
    def test_production_reader_identity_membership_bounds_context_and_fault_barriers(self):
        extract = runpy.run_path(str(ROOT / "tests" / "test_native_fault_barriers.py"))["method"]
        reader = (BRIDGE / "E3d21ElementAncestry.cs").read_text(encoding="utf-8-sig")
        helpers = re.findall(r"^    private static [^\n]+\b(Ancestry\w+)\(", reader, re.MULTILINE)
        self.assertTrue(helpers, "The production ancestry reader must expose its Ancestry-prefixed helper methods.")
        self.assertTrue(all(name.startswith("Ancestry") for name in helpers))
        methods = [extract(reader, "ElementAncestry")] + [extract(reader, name) for name in helpers]

        model = (BRIDGE / "E3d21ModelEditing.cs").read_text(encoding="utf-8-sig")
        methods += [extract(model, name) for name in ("ModelReference", "ModelCurrentReference")]
        input_source = (BRIDGE / "ModelEditInput.cs").read_text(encoding="utf-8-sig").replace(
            "internal static", "private static")

        harness = (ROOT / "tests" / "e3d21" / "ElementAncestryHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_ANCESTRY_METHODS", "\n\n".join(methods))
        input_methods = extract(input_source, "IsReferencePair").replace("private static", "internal static", 1)
        harness = harness.replace("// PRODUCTION_INPUT_METHOD", input_methods)
        with tempfile.TemporaryDirectory(prefix="element-ancestry-native-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            shutil.copyfile(BRIDGE / "NativeDatabaseFaultState.cs", output / "NativeDatabaseFaultState.cs")
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>',
                encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=35)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Element ancestry native production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
