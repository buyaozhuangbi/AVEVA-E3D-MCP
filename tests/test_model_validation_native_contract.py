"""Run the production model validation method with managed database fakes."""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"
TOKENS = re.compile(r'@"(?:""|[^"])*"|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/|[{}]', re.S)


def block_end(source: str, opening: int) -> int:
    depth = 0
    for token in TOKENS.finditer(source, opening):
        if token.group() == "{":
            depth += 1
        elif token.group() == "}":
            depth -= 1
            if depth == 0:
                return token.end()
    raise AssertionError("Unterminated production block")


def extract_method(source: str, name: str) -> str:
    declaration = re.search(
        rf"^    (?:public|private) (?:static )?[^\n]+\b{re.escape(name)}(?:<[^>]+>)?\(",
        source,
        re.MULTILINE,
    )
    if declaration is None:
        raise AssertionError(f"Production method is missing: {name}")
    opening = source.index("{", declaration.end())
    extracted = source[declaration.start() : block_end(source, opening)]
    if name == "ThrowIfNativeFaulted":
        from generated_csg_gate_support import generated_csg_gate_members
        extracted += "\n\n" + generated_csg_gate_members(extract_method)
    return extracted


class ModelValidationNativeContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "The offline C# checks require dotnet.")
    def test_production_large_validation_and_read_failure_contract(self) -> None:
        source = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        methods = [extract_method(source, name) for name in (
            "ValidateModel", "ModelValidationShape", "ModelValidationAttributes",
            "ModelValidationDimensionRules", "ModelValidationDimensionIssues",
            "ValidationDistanceMillimetres", "DirectMembersAt", "NativeRead", "HierarchyInfo",
        )]
        harness = (ROOT / "tests" / "e3d21" / "ModelValidationHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_METHODS", "\n\n".join(methods))
        adapter = (BRIDGE / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
        route = adapter.index('else if (method == "GET" && path == "/api/v1/validate")')
        opening = adapter.index("{", route)
        harness = harness.replace("// PRODUCTION_VALIDATE_ROUTE", adapter[opening + 1 : block_end(adapter, opening) - 1])
        with tempfile.TemporaryDirectory(prefix="model-validation-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            shutil.copyfile(BRIDGE / "BoundedHierarchy.cs", output / "BoundedHierarchy.cs")
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                '<TargetFramework>net48</TargetFramework><OutputType>Exe</OutputType>'
                '<LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Model validation production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
