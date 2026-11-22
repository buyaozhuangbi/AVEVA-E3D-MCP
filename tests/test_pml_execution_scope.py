"""Check production command routing, without claiming to emulate the PML parser."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_cancel_completion import extract_method


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


class PmlExecutionScopeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "The offline C# checks require dotnet.")
    def test_production_single_and_batch_use_explicit_execution_scope(self):
        source = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        methods = "\n\n".join(extract_method(source, name) for name in
                               ("ExecutePml", "ExecutePmlBatch", "IsCancellationCommand"))
        harness = (ROOT / "tests" / "e3d21" / "PmlExecutionScopeHarness.cs").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="pml-scope-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness.replace("// PRODUCTION_METHODS", methods), encoding="utf-8")
            for filename in ("CommandBatchRunner.cs", "NativeDatabaseFaultState.cs"):
                shutil.copyfile(BRIDGE / filename, output / filename)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("PML execution scope checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
