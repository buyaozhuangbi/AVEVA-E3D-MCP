"""Exercise the production create-failure cleanup and membership proof with SDK fakes."""
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


class ModelCleanupContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline C# checks require dotnet.")
    def test_exact_cleanup_identity_append_proof_and_fault_barriers(self):
        extract = runpy.run_path(str(ROOT / "tests" / "test_native_fault_barriers.py"))["method"]
        model = (BRIDGE / "E3d21ModelEditing.cs").read_text(encoding="utf-8-sig")
        context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        methods = [extract(model, name) for name in (
            "ModelCleanupCreated", "ModelMemberReferences", "ModelRequireElement", "ModelReference", "ModelVerifyDeletion")]
        methods.append(extract(context, "DeleteElement"))
        methods.extend(extract(context, name) for name in (
            "DirectSiblings", "IsUsable", "NativeRead", "NativeWrite", "ThrowIfNativeFaulted", "OnUi"))
        harness = (ROOT / "tests" / "e3d21" / "ModelCleanupHarness.cs").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="model-cleanup-contract-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness.replace("// PRODUCTION_METHODS", "\n\n".join(methods)), encoding="utf-8")
            for filename in ("ModelEditInput.cs", "AttributeEditPolicy.cs", "MemberPage.cs", "NativeDatabaseFaultState.cs"):
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
            self.assertIn("Model cleanup production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
