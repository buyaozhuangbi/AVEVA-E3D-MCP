"""Run the production command-message window methods against a managed ring fake."""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


def method(source: str, name: str) -> str:
    declaration = re.search(
        rf"^    private (?:static )?[^\n]+\b{re.escape(name)}(?:<[^>]+>)?\(",
        source,
        re.MULTILINE,
    )
    if declaration is None:
        raise AssertionError(f"Production method is missing: {name}")
    opening = source.index("{", declaration.end())
    tokens = re.compile(r'@"(?:""|[^"])*"|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/|[{}]', re.S)
    depth = 0
    for token in tokens.finditer(source, opening):
        if token.group() == "{":
            depth += 1
        elif token.group() == "}":
            depth -= 1
            if depth == 0:
                return source[declaration.start() : token.end()]
    raise AssertionError(f"Unterminated production method: {name}")


class OutputMessageWindowTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "The offline C# checks require dotnet.")
    def test_operation_windows_survive_and_report_ring_overflow(self):
        source = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        extracted = "\n\n".join(method(source, name) for name in
                                   ("OutputString", "CaptureOutput", "MessageSequence", "AddDiagnostics"))
        harness = (ROOT / "tests" / "e3d21" / "OutputMessageWindowHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_METHODS", extracted)
        self.assertNotIn("// PRODUCTION_METHODS", harness)
        with tempfile.TemporaryDirectory(prefix="output-message-window-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                '<TargetFramework>net10.0</TargetFramework><OutputType>Exe</OutputType>'
                '<LangVersion>latest</LangVersion></PropertyGroup></Project>',
                encoding="utf-8",
            )
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net10.0" / "Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Output message window checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
