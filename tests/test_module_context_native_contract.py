"""Compile the production module-context reader against recording SDK fakes."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_model_validation_native_contract import block_end

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


def production_method(source: str, name: str) -> str:
    declaration = re.search(
        rf"^    (?:public|private) (?:static )?[^\n]+\b{re.escape(name)}(?:<[^>]+>)?\(",
        source, re.MULTILINE,
    )
    if declaration is None:
        raise AssertionError("Production method is missing: " + name)
    opening = source.index("{", declaration.end())
    return source[declaration.start():block_end(source, opening)]


class ModuleContextNativeContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline native checks require dotnet.")
    def test_production_observation_uses_one_ui_dispatch_and_stops_on_native_fault(self):
        context_source = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        module_source = (BRIDGE / "E3d21ModuleContext.cs").read_text(encoding="utf-8-sig")
        interface_source = (BRIDGE / "ModuleContext.cs").read_text(encoding="utf-8-sig")
        self.assertIn("public interface IE3d21ModuleContext", interface_source)
        self.assertIn("object ReadModuleContext(string sessionId);", interface_source)
        self.assertIn("new ModuleManager().CurrentModule", module_source)
        self.assertIn("ModuleContextAgreement(before, after)", module_source)
        self.assertNotRegex(module_source, r"CurrentModule\s*=")
        self.assertNotIn("DBModification", module_source)
        self.assertNotIn("PmlCommand", module_source)

        helper_methods = "\n\n".join(
            production_method(context_source, name)
            for name in ("ThrowIfNativeFaulted", "NativeRead", "OnUi")
        )
        harness = (ROOT / "tests/e3d21/ModuleContextHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_NATIVE_HELPERS", helper_methods)
        with tempfile.TemporaryDirectory(prefix="module-context-native-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            for name in ("ModuleContext.cs", "E3d21ModuleContext.cs", "NativeDatabaseFaultState.cs"):
                shutil.copyfile(BRIDGE / name, output / name)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>',
                encoding="utf-8",
            )
            options = dict(cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   timeout=90, **options)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin/Release/net48/Offline.exe")], timeout=30, **options)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Module-context production native checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
