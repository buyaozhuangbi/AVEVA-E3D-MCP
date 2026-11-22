"""Exercise production create-state capture and execution with managed SDK fakes."""
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge/e3d21"


class ModelCreateRevisionContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline C# checks require dotnet.")
    def test_production_state_capture_and_create_reject_stale_siblings_and_frames(self):
        extract = runpy.run_path(str(ROOT / "tests/test_native_fault_barriers.py"))["method"]
        block_end = runpy.run_path(str(ROOT / "tests/e3d21/run-attribute-batch-http-harness.py"))["block_end"]
        model = (BRIDGE / "E3d21ModelEditing.cs").read_text(encoding="utf-8-sig")
        names = re.findall(r"^    private (?:static )?[^\n]+\b(ModelCreate\w+)\(", model, re.M)
        self.assertIn("ModelCreateReadState", names)
        names += ["CreateElement", "ModelInitialAttributes", "ModelEditResult", "ModelReference", "ModelIdentity",
                  "ModelFrameIdentity", "ModelSubmittedOrientation", "ModelFinite", "ModelPosition", "ModelOrientation",
                  "ModelPositionValue", "ModelOrientationValue", "ModelAddTransform", "ModelRequireOrientationMatches",
                  "ModelCeUnchanged", "ModelCaptureCeAfter", "ModelMemberReferences", "ModelRequireTypeAttribute"]
        parts = [extract(model, name) for name in names]
        for name in ("CreateState", "CreateMemberIdentity", "ModelInitialAttribute"):
            start = model.index("    private sealed class " + name + "\n")
            opening = model.index("{", start)
            parts.append(model[start:block_end(model, opening)])
        primitive = (BRIDGE / "E3d21PrimitiveEditing.cs").read_text(encoding="utf-8-sig")
        context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        parts += [extract(primitive, "PrimitiveHash"), extract(context, "DirectionVector"), extract(context, "FiniteVector")]
        harness = (ROOT / "tests/e3d21/ModelCreateRevisionHarness.cs").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="model-create-revision-") as directory:
            output = Path(directory)
            (output / "Harness.cs").write_text(harness.replace("// PRODUCTION_CREATE_METHODS", "\n\n".join(parts)), encoding="utf-8")
            for name in ("ModelEditInput.cs", "AttributeEditPolicy.cs", "MemberPage.cs"):
                shutil.copyfile(BRIDGE / name, output / name)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"], cwd=output,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin/Release/net48/Offline.exe")], cwd=output,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            match = re.search(r"Model create revision production checks passed: (\d+)", ran.stdout)
            self.assertIsNotNone(match, ran.stdout)
            self.assertGreaterEqual(int(match[1]), 90)


if __name__ == "__main__":
    unittest.main()
