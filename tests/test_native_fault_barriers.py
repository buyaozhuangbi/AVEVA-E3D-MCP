"""Execute production fault barriers with managed fakes, without loading E3D.

Only the selected method bodies are extracted. The fake setters emit the same
fault callback while returning success (or throwing a different exception),
which exercises the dangerous continuation paths rather than source spelling.
"""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


def method(source: str, name: str) -> str:
    declaration = re.search(
        rf"^    (?:private|public) (?:static )?[^\n]+\b{re.escape(name)}(?:<[^>]+>)?\(",
        source,
        re.MULTILINE,
    )
    if declaration is None:
        raise AssertionError(f"Production method is missing: {name}")
    opening = source.index("{", declaration.end())
    # Match C# strings/comments as tokens so braces in diagnostics are ignored.
    tokens = re.compile(r'@"(?:""|[^"])*"|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/|[{}]', re.S)
    depth = 0
    for token in tokens.finditer(source, opening):
        if token.group() == "{":
            depth += 1
        elif token.group() == "}":
            depth -= 1
            if depth == 0:
                extracted = source[declaration.start() : token.end()]
                if name == "ThrowIfNativeFaulted":
                    from generated_csg_gate_support import generated_csg_gate_members
                    extracted += "\n\n" + generated_csg_gate_members(method)
                return extracted
    raise AssertionError(f"Unterminated production method: {name}")


class NativeFaultBarriersTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "The offline C# checks require dotnet.")
    def test_production_barriers_stop_native_continuation(self):
        selected = {
            "E3d21Context.cs": ["ThrowIfNativeFaulted", "NativeRead", "NativeWrite", "SafeString", "SafeInt", "SafeBool", "OnUi",
                                  "IsCommandActive", "BridgeOperationBusy", "NativeDispatchBusy", "ExecutionDiagnostics", "CurrentMdb", "IsUsable"],
            "E3d21ModelEditing.cs": ["ModelRequireSettable", "ModelRequireSetter", "ModelWritePosition", "ModelWriteOrientation",
                                      "ModelFrame", "ModelRequireElement"],
            "E3d21PrimitiveEditing.cs": ["PrimitiveWriteDimension"],
            "E3d21PrimitiveVisual.cs": ["PrimitiveVisualApplyExisting", "PrimitiveVisualValuesEqual"],
            "E3d21DisplayState.cs": ["DisplayStateReadField"],
        }
        extracted = []
        for filename, names in selected.items():
            source = (BRIDGE / filename).read_text(encoding="utf-8-sig")
            extracted.extend(method(source, name) for name in names)
        harness = (ROOT / "tests" / "e3d21" / "NativeFaultBarrierHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_METHODS", "\n\n".join(extracted))
        with tempfile.TemporaryDirectory(prefix="native-fault-barriers-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            for filename in ["NativeDatabaseFaultState.cs", "PrimitiveUpdateRunner.cs"]:
                shutil.copyfile(BRIDGE / filename, output / filename)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                '<TargetFramework>net48</TargetFramework><OutputType>Exe</OutputType>'
                '<LangVersion>latest</LangVersion></PropertyGroup></Project>',
                encoding="utf-8",
            )
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"], cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")], cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Native fault barrier checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
