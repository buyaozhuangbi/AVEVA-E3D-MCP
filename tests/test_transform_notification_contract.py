"""Run production transform writes, rollback, notification and verification against SDK fakes."""
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


class TransformNotificationContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline C# checks require dotnet.")
    def test_write_rollback_notification_callbacks_and_fault_barriers(self):
        extract = runpy.run_path(str(ROOT / "tests" / "test_native_fault_barriers.py"))["method"]
        source = (BRIDGE / "E3d21ModelEditing.cs").read_text(encoding="utf-8-sig")
        transform = extract(source.replace("public object SetTransform(", "private object SetTransform("), "SetTransform")
        methods = [transform.replace("private object SetTransform(", "public object SetTransform(")]
        # SetTransform now delegates to the shared guarded core. This legacy
        # notification contract deliberately exercises the unrevisioned path;
        # keep the revision fingerprint out of this harness while compiling
        # the shared method body.
        methods.append(extract(source, "SetTransformCore"))
        methods.append("private string ModelTransformRevision(string target, ModelEditInput input) { return null; }")
        methods.extend(extract(source, name) for name in (
            "ModelEditResult", "ModelReference", "ModelRequireSettable", "ModelRequireSetter",
            "ModelWritePosition", "ModelWriteOrientation", "ModelPosition", "ModelOrientation",
            "ModelSubmittedOrientation", "ModelPositionValue", "ModelOrientationValue", "ModelFinite",
            "ModelAddTransform", "ModelRequireOrientationMatches", "ModelCurrentReference",
            "ModelCeUnchanged", "ModelCaptureCeAfter", "ModelRestoreTransform", "ModelVectorsEqual"))
        context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        methods.extend(extract(context, name) for name in (
            "ThrowIfNativeFaulted", "NativeRead", "NativeWrite", "OnUi", "DirectionVector", "FiniteVector"))
        harness = (ROOT / "tests" / "e3d21" / "TransformNotificationHarness.cs").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="transform-notification-contract-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness.replace("// PRODUCTION_METHODS", "\n\n".join(methods)), encoding="utf-8")
            for filename in ("ModelEditInput.cs", "AttributeEditPolicy.cs", "NativeDatabaseFaultState.cs"):
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
            self.assertIn("Transform notification production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
