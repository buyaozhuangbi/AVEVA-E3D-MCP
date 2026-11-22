"""Run production persistence methods with actual files and no native E3D."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from xml.sax.saxutils import escape

from test_native_fault_barriers import method

ROOT = Path(__file__).resolve().parents[1]


def run_harness():
    source = ROOT / "bridge/e3d21/E3d21Context.cs"
    harness_path = ROOT / "tests/e3d21/EmptyDrawListPersistenceHarness.cs"
    methods = ["PersistDrawListVisualDocument", "ValidateDrawListVisualDocument", "IsNumericVisualReference",
               "ReadDrawListState", "WriteDrawListStateFile"]
    text = source.read_text(encoding="utf-8-sig")
    harness = harness_path.read_text(encoding="utf-8").replace("// PRODUCTION_METHODS", "\n\n".join(method(text, name) for name in methods))
    newtonsoft = Path("C:/Program Files (x86)/AVEVA/Everything3D2.10/Newtonsoft.Json.dll")
    result = {"source": str(source), "production_methods": methods,
              "sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, harness_path, Path(__file__))},
              "scope": "actual production validation, JSON serialization, atomic writes and reads on temporary files; no E3D native runtime"}
    with tempfile.TemporaryDirectory(prefix="empty-drawlist-test-") as temp:
        output = Path(temp)
        (output / "Harness.cs").write_text(harness, encoding="utf-8")
        (output / "Offline.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
            '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup>'
            '<ItemGroup><Reference Include="Newtonsoft.Json"><HintPath>' + escape(str(newtonsoft)) +
            '</HintPath><Private>True</Private></Reference></ItemGroup></Project>', encoding="utf-8")
        command = ["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"]
        built = subprocess.run(command, cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        result["build"] = {"command": command, "returncode": built.returncode, "stdout": built.stdout, "stderr": built.stderr}
        if built.returncode == 0:
            command = [str(output / "bin/Release/net48/Offline.exe")]
            ran = subprocess.run(command, cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            result["run"] = {"command": command, "returncode": ran.returncode, "stdout": ran.stdout, "stderr": ran.stderr}
    return result


class EmptyDrawlistPersistenceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Requires dotnet for offline managed tests")
    def test_explicit_empty_document_persistence_and_default_preservation(self):
        result = run_harness()
        self.assertEqual(result["build"]["returncode"], 0, result["build"]["stdout"] + result["build"]["stderr"])
        self.assertEqual(result["run"]["returncode"], 0, result["run"]["stdout"] + result["run"]["stderr"])
        self.assertIn("Empty Drawlist persistence checks passed:", result["run"]["stdout"])


if __name__ == "__main__":
    if "--result" in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--result", type=Path, required=True)
        args = parser.parse_args()
        result = run_harness()
        args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(result.get("run", result["build"])["stdout"])
        print(result.get("run", result["build"])["stderr"])
        sys.exit(result.get("run", result["build"])["returncode"])
    unittest.main()
