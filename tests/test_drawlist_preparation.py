"""Exercise production drawlist preparation and setter flow with recording fakes.

Only the managed control flow is under test. The fake's delayed primitive
materialization is a scenario, not evidence that native UpdateGraphics alone
materializes an E3D primitive or preserves native inheritance metadata.
"""
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


class DrawListPreparationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline C# checks require dotnet.")
    def test_production_preparation_setter_strict_readback_and_failure_flow(self):
        extract = runpy.run_path(str(ROOT / "tests" / "test_native_fault_barriers.py"))["method"]
        context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        visual = (BRIDGE / "E3d21PrimitiveVisual.cs").read_text(encoding="utf-8-sig")
        setter = extract(context.replace("public object SetDrawListVisual(",
                                         "private static object SetDrawListVisual("), "SetDrawListVisual")
        methods = [setter]
        methods.extend(extract(context, name) for name in (
            "DrawListVisualRequestChanges", "AttemptDrawListVisualRefresh", "RefreshDrawListVisual",
            "ValidateDrawListVisualPreparationScope", "ValidateDrawListVisualAddScope",
            "CaptureDrawListVisualSnapshot", "CaptureDrawListVisual", "CloneDrawListVisual",
            "CompareDrawListVisualSnapshots", "RequireExactDrawListMember",
            "ApplyExactDrawListVisual", "VerifyExactDrawListVisual", "OrderDrawListElementsByOwner",
            "RollbackDrawListVisualSnapshot", "CaptureDrawListVisualResidual"))
        methods.extend(extract(visual, name) for name in (
            "CaptureDrawListPrimitiveSnapshot", "ApplyDrawListPrimitiveSnapshot",
            "PrimitiveVisualPreflightTransparency", "PrimitiveVisualInSubtree",
            "PrimitiveVisualValidateExpected", "PrimitiveVisualCanonicalReference",
            "PrimitiveVisualApplyExisting", "PrimitiveVisualReadExistingAliases",
            "PrimitiveVisualKey", "PrimitiveVisualClone", "PrimitiveVisualValuesEqual",
            "CompareDrawListPrimitiveSnapshots", "PrimitiveVisualRequireComplete"))
        harness = (ROOT / "tests" / "e3d21" / "DrawListPreparationHarness.cs").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="drawlist-preparation-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness.replace("// PRODUCTION_METHODS", "\n\n".join(methods)),
                                               encoding="utf-8")
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>',
                encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Drawlist preparation production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
