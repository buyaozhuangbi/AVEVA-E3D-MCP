"""Run production access resolution, reads, and HTTP routing with recording SDK fakes."""
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"
SDK_JSON = Path(r"C:\Program Files (x86)\AVEVA\Everything3D2.10\Newtonsoft.Json.dll")


def route_body(source):
    marker = 'else if (method == "POST" && path == "/api/v1/elements/access")'
    opening = source.index("{", source.index(marker) + len(marker))
    tokens = re.compile(r'@"(?:""|[^"])*"|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/|[{}]', re.S)
    depth = 0
    for token in tokens.finditer(source, opening):
        if token.group() == "{":
            depth += 1
        elif token.group() == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1:token.start()]
    raise AssertionError("Unterminated element-access route")


class ElementAccessNativeContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet") and SDK_JSON.is_file(), "Offline checks require dotnet and Newtonsoft.Json.")
    def test_production_resolution_reader_fault_barriers_and_http_contract(self):
        extract = runpy.run_path(str(ROOT / "tests" / "test_native_fault_barriers.py"))["method"]
        context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        methods = [extract(context, name) for name in ("NativeRead", "ThrowIfNativeFaulted", "OnUi")]
        methods[-1] = methods[-1].replace("result = operation();", "result = operation();\n                if (Scenario.AfterOperation != null) Scenario.AfterOperation();")
        model = (BRIDGE / "E3d21ModelEditing.cs").read_text(encoding="utf-8-sig")
        methods += [extract(model, name) for name in ("ModelReference", "ModelCurrentReference", "ModelCeUnchanged")]
        input_source = (BRIDGE / "ModelEditInput.cs").read_text(encoding="utf-8-sig").replace("internal static", "private static")
        input_methods = [extract(input_source, name).replace("private static", "internal static", 1)
                         for name in ("ExplicitReference", "IsReferencePair")]
        harness = (ROOT / "tests" / "e3d21" / "ElementAccessHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_METHODS", "\n\n".join(methods))
        harness = harness.replace("// PRODUCTION_INPUT_METHODS", "\n\n".join(input_methods))
        harness = harness.replace("// PRODUCTION_ROUTE", route_body((BRIDGE / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")))
        with tempfile.TemporaryDirectory(prefix="element-access-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            for filename in ("E3d21ElementAccess.cs", "NativeDatabaseFaultState.cs"):
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
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Element access direct resolution checks passed.", ran.stdout)
            self.assertIn("Element access production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
