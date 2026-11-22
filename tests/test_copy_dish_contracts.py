"""Compile the complete production copy workflow against recording managed SDK fakes.

No AVEVA assembly/native engine is loaded. Session/frame resolution and the panel
state reader are peripheral fakes; CopyEditing, cleanup and polygon policy are real.
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


class CopyDishContractsTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline checks require dotnet.")
    def test_production_copy_dish_and_legacy_geometry_contracts(self):
        extract = runpy.run_path(str(ROOT / "tests" / "test_native_fault_barriers.py"))["method"]
        selected = {
            "E3d21Context.cs": ("NativeRead", "NativeWrite", "ThrowIfNativeFaulted", "IsUsable", "FiniteVector", "DirectionVector", "DirectSiblings"),
            "E3d21ModelEditing.cs": ("ModelEditResult", "ModelRequireElement", "ModelRequireSettable", "ModelRequireSetter", "ModelReference",
                "ModelIdentity", "ModelFrameIdentity", "ModelSubmittedOrientation", "ModelFinite", "ModelCaptureCeAfter", "ModelMemberReferences", "ModelCleanupCreated"),
            "E3d21PrimitiveEditing.cs": ("PrimitiveShape", "PrimitiveIsNegative", "PrimitiveShapeFields", "Pair", "PrimitiveValidateDimensions",
                "PrimitiveFinite", "PrimitiveHash", "PrimitiveWritePositionBytes", "PrimitiveWriteOrientationBytes", "PrimitivePositionVector", "PrimitiveOrientationValue",
                "PrimitiveOrientationToken", "PrimitiveNumberToken", "PrimitivePositionMatches", "PrimitiveVectorMatches", "PrimitiveNumbersMatch",
                "PrimitiveDirectMembers", "PrimitiveGuardElement", "PrimitiveUnset", "PrimitiveAppendTransform", "PrimitiveComposePosition", "PrimitiveComposeOrientation", "PrimitiveMultiplyAxes"),
        }
        methods = []
        for filename, names in selected.items():
            source = (BRIDGE / filename).read_text(encoding="utf-8-sig")
            methods.extend(extract(source, name) for name in names)
        harness = (ROOT / "tests" / "e3d21" / "CopyDishGeometryHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_METHODS", "\n\n".join(methods))
        with tempfile.TemporaryDirectory(prefix="copy-dish-contract-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            for filename in ("E3d21CopyEditing.cs", "CopyTreePolicy.cs", "ModelEditInput.cs", "AttributeEditPolicy.cs",
                             "MemberPage.cs", "NativeDatabaseFaultState.cs", "PanelPolygon.cs"):
                shutil.copyfile(BRIDGE / filename, output / filename)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"], cwd=output,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")], cwd=output,
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("DISH copy production checks passed:", ran.stdout)
            report = os.environ.get("E3D_COPY_DISH_CONTRACT_OUTPUT")
            if report:
                Path(report).write_text(ran.stdout, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
