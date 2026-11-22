"""Run actual panel creation/inspection/cleanup against a managed SDK simulator.

Frame conversion and final transform reporting are isolated stubs: this suite
checks provenance and native-fault control flow, not AVEVA rendering or geometry
frame conversion. Derived-point stubs return distinct sentinels for PANE/WORLD
to verify the production qualifier choices. No installed AVEVA assemblies or
production bridge build run.
"""
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


class PanelCreationProvenanceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline C# checks require dotnet.")
    def test_real_create_panel_provenance_and_cleanup(self):
        extract = runpy.run_path(str(ROOT / "tests" / "test_model_validation_native_contract.py"))["extract_method"]
        model = (BRIDGE / "E3d21ModelEditing.cs").read_text(encoding="utf-8-sig")
        context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        methods = [extract(model, name) for name in (
            "ModelEditResult", "ModelRequireElement", "ModelReference", "ModelRequireSettable",
            "ModelRequireSetter", "ModelWritePosition", "ModelWriteOrientation", "ModelFinite",
            "ModelCurrentReference", "ModelCeUnchanged", "ModelCaptureCeAfter",
            "ModelCleanupCreated", "ModelMemberReferences")]
        methods.extend(extract(context, name) for name in (
            "DirectSiblings", "IsUsable", "NativeRead", "NativeWrite", "ThrowIfNativeFaulted", "OnUi", "FiniteVector"))
        harness = (ROOT / "tests" / "e3d21" / "PanelCreationProvenanceHarness.cs").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="panel-provenance-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness.replace("// PRODUCTION_METHODS", "\n\n".join(methods)), encoding="utf-8")
            for filename in ("E3d21PanelGeometry.cs", "E3d21PanelBoundaryPoints.cs", "ModelEditInput.cs",
                             "AttributeEditPolicy.cs", "MemberPage.cs", "NativeDatabaseFaultState.cs", "PanelPolygon.cs"):
                shutil.copyfile(BRIDGE / filename, output / filename)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Panel provenance production checks passed:", ran.stdout)
            boundary = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe"), "--boundary-points"],
                                      cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            self.assertEqual(boundary.returncode, 0, boundary.stdout + boundary.stderr)
            self.assertIn("Panel boundary-point production checks passed:", boundary.stdout)


if __name__ == "__main__":
    unittest.main()
