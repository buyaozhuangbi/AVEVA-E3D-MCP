"""Compile the production graphical-selection reader with managed SDK fakes."""
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


def graphical_selection_route(source: str) -> str:
    marker = 'else if (method == "GET" && path == "/api/v1/view/graphical-selection")'
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
    raise AssertionError("Unterminated graphical-selection route")


class GraphicalSelectionNativeContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "The offline C# checks require dotnet.")
    def test_complete_production_reader_fault_barriers_and_http_route(self) -> None:
        extract = runpy.run_path(str(ROOT / "tests" / "test_native_fault_barriers.py"))["method"]
        context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        methods = [extract(context, name) for name in ("NativeRead", "ThrowIfNativeFaulted", "OnUi")]
        harness = (ROOT / "tests" / "e3d21" / "GraphicalSelectionHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_METHODS", "\n\n".join(methods))
        route = graphical_selection_route((BRIDGE / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig"))
        harness = harness.replace("// PRODUCTION_ROUTE", route)
        with tempfile.TemporaryDirectory(prefix="graphical-selection-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            for filename in ("E3d21GraphicalSelection.cs", "NativeDatabaseFaultState.cs"):
                shutil.copyfile(BRIDGE / filename, output / filename)
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
            self.assertIn("Graphical selection production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
