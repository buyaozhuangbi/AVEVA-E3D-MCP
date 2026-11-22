"""Run extracted production snapshot readers against recording managed SDK fakes.

No AVEVA assembly is loaded. The benchmark compares a fixed pre-change
ElementInfo oracle with current production code; it does not predict native time.
"""
from pathlib import Path
import os
import runpy
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


class SnapshotFormattedAttributesTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline checks require dotnet.")
    def test_production_snapshot_equivalence_and_fault_barriers(self):
        extract = runpy.run_path(str(ROOT / "tests" / "test_native_fault_barriers.py"))["method"]
        source = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        methods = [extract(source, name) for name in (
            "ElementInfo", "SafeFormattedAttribute", "SafeString", "SafeInt", "ElementRef", "ElementName",
            "ElementType", "IsUsable", "NativeRead", "ThrowIfNativeFaulted", "ReadHierarchy", "ReadChildren",
            "HierarchyInfo", "BuildSnapshot", "SnapshotNode")]
        harness = (ROOT / "tests" / "e3d21" / "SnapshotFormattedAttributesHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_METHODS", "\n\n".join(methods))
        with tempfile.TemporaryDirectory(prefix="snapshot-formatted-attributes-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            for filename in ("NativeDatabaseFaultState.cs", "BoundedHierarchy.cs"):
                shutil.copyfile(BRIDGE / filename, output / filename)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                '<TargetFramework>net48</TargetFramework><OutputType>Exe</OutputType>'
                '<LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Snapshot formatted attribute checks passed:", ran.stdout)
            destination = os.environ.get("E3D_SNAPSHOT_OFFLINE_BENCHMARK_OUTPUT")
            if destination:
                Path(destination).write_text(ran.stdout, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
