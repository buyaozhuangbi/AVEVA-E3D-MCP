"""Run the production SetTransform revision path against managed SDK fakes."""
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


class TransformRevisionContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline C# checks require dotnet.")
    def test_production_revision_compare_and_set_and_fault_barriers(self):
        extract = runpy.run_path(str(ROOT / "tests" / "test_native_fault_barriers.py"))["method"]
        block_end = runpy.run_path(str(ROOT / "tests/e3d21/run-attribute-batch-http-harness.py"))["block_end"]
        model = (BRIDGE / "E3d21ModelEditing.cs").read_text(encoding="utf-8-sig")
        method_names = [
            "SetTransformRevisioned", "SetTransformCore", "ModelTransformRevision", "ModelEditResult",
            "ModelRequireSettable", "ModelRequireSetter", "ModelWritePosition", "ModelWriteOrientation",
            "ModelPosition", "ModelOrientation", "ModelSubmittedOrientation", "ModelReference", "ModelFinite",
            "ModelPositionValue", "ModelOrientationValue", "ModelAddTransform", "ModelRequireOrientationMatches",
            "ModelCurrentReference", "ModelCeUnchanged", "ModelCaptureCeAfter", "ModelRestoreTransform",
            "ModelVectorsEqual", "ModelCreateDatabaseKey", "ModelCreateReadDatabaseIdentity",
            "ModelCreateReadMdbIdentity", "ModelCreateRequireDatabase", "ModelCreateWorldReference",
            "ModelCreateDatabaseWorld", "ModelCreateWriteDatabaseIdentity", "ModelCreateWriteVector",
            "ModelCreateWriteOrientation", "ModelCreateWriteNullableString", "ModelCreateWriteAncestorChain",
        ]
        parts = [extract(model, name) for name in method_names]
        parts += [extract(model, "ModelStorageFrame").replace("ModelStorageFrame(", "ProbeStorageFrameCore("),
                  extract(model, "ModelRequireStableFrame").replace("ModelRequireStableFrame(", "ProbeStableFrameCore(")]
        for name in ("CreateDatabaseIdentity", "CreateMdbIdentity"):
            start = model.index("    private sealed class " + name + "\n")
            opening = model.index("{", start)
            parts.append(model[start:block_end(model, opening)])
        primitive = (BRIDGE / "E3d21PrimitiveEditing.cs").read_text(encoding="utf-8-sig")
        context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        parts += [extract(primitive, "PrimitiveHash"), extract(context, "DirectionVector"),
                  extract(context, "FiniteVector")]
        harness = (ROOT / "tests/e3d21/TransformRevisionHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_METHODS", "\n\n".join(parts))
        with tempfile.TemporaryDirectory(prefix="transform-revision-contract-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            shutil.copyfile(BRIDGE / "ModelEditInput.cs", output / "ModelEditInput.cs")
            shutil.copyfile(BRIDGE / "AttributeEditPolicy.cs", output / "AttributeEditPolicy.cs")
            shutil.copyfile(BRIDGE / "NativeDatabaseFaultState.cs", output / "NativeDatabaseFaultState.cs")
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>',
                encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=35)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Transform revision production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
