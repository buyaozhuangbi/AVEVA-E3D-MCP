"""Exercise the production create-request revision and pre-create CAS gate offline."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"
TOKENS = re.compile(r'@"(?:""|[^"])*"|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/|[{}]', re.S)


def block_end(source: str, opening: int) -> int:
    depth = 0
    for token in TOKENS.finditer(source, opening):
        if token.group() == "{":
            depth += 1
        elif token.group() == "}":
            depth -= 1
            if depth == 0:
                return token.end()
    raise AssertionError("Unterminated C# method")


def extract_method(source: str, name: str, access: str = "private static") -> str:
    match = re.search(r"^    " + re.escape(access) + r" [^\n]+\b" + re.escape(name) + r"\(", source, re.M)
    if match is None:
        raise AssertionError(f"Missing production method {name}")
    opening = source.index("{", match.end())
    return source[match.start():block_end(source, opening)]


class CreateRevisionNativeContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline C# contract checks require dotnet.")
    def test_production_fingerprint_invalidates_create_inputs_and_conflicts_before_create_last(self) -> None:
        model = (BRIDGE / "E3d21ModelEditing.cs").read_text(encoding="utf-8-sig")
        primitive = (BRIDGE / "E3d21PrimitiveEditing.cs").read_text(encoding="utf-8-sig")
        create = extract_method(model, "CreateElement", "public")
        conflict = create.index("ModelCreateRevisionConflict(expected, state.Revision, immediate.Revision)")
        native_create = create.index("parent.CreateLast(type)")
        self.assertLess(conflict, native_create)
        self.assertLess(create.index('result["revision_conflict"] = true;', conflict), native_create)

        helpers = [extract_method(model, name) for name in (
            "ModelCreateRequestRevision", "ModelCreateRevisionConflict", "ModelCreateWriteVector",
            "ModelCreateWriteNullableString")]
        helpers.append(extract_method(primitive, "PrimitiveHash"))
        harness = (ROOT / "tests" / "e3d21" / "CreateRevisionHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_METHODS", "\n\n".join(helpers))
        with tempfile.TemporaryDirectory(prefix="create-revision-contract-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>',
                encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Create revision production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
