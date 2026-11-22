"""Run actual restoration/fault barriers against offline native-behavior fakes.

An existing public wrapper may inherit member values until its first setter
materializes an override. Subsequent setters modify only their own field.
The suite also retains zero clamping and aggregate propagation checks.
No AVEVA assembly or live E3D is loaded. --source and --result permit a saved
before/after source comparison without swapping the production file.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_native_fault_barriers import method


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "bridge/e3d21/E3d21PrimitiveVisual.cs"


def run_harness(source_path=DEFAULT_SOURCE, result_path=None):
    source_path = Path(source_path).resolve()
    context_path = ROOT / "bridge/e3d21/E3d21Context.cs"
    fault_path = ROOT / "bridge/e3d21/NativeDatabaseFaultState.cs"
    harness_path = ROOT / "tests/e3d21/PrimitiveVisualRestoreHarness.cs"
    source = source_path.read_text(encoding="utf-8-sig")
    context = context_path.read_text(encoding="utf-8-sig")
    names = ["ApplyDrawListPrimitiveSnapshot", "PrimitiveVisualPreflightTransparency",
             "PrimitiveVisualValidateExpected", "PrimitiveVisualCanonicalReference",
             "PrimitiveVisualApplyExisting", "PrimitiveVisualReadExistingAliases",
             "PrimitiveVisualKey", "PrimitiveVisualClone", "PrimitiveVisualValuesEqual",
             "CompareDrawListPrimitiveSnapshots", "PrimitiveVisualRequireComplete"]
    extracted = [method(source, name) for name in names]
    # The body is unchanged. A harness-owned wrapper records calls, requests,
    # attempts, state and exceptions around every invocation of the real method.
    extracted[0] = extracted[0].replace(
        "private static void ApplyDrawListPrimitiveSnapshot(",
        "private static void ApplyDrawListPrimitiveSnapshotProduction(", 1)
    extracted += [method(context, name) for name in ["ThrowIfNativeFaulted", "NativeRead", "NativeWrite"]]
    harness = harness_path.read_text(encoding="utf-8").replace("// PRODUCTION_METHODS", "\n\n".join(extracted))
    result = {
        "utc": datetime.now(timezone.utc).isoformat(),
        "source": str(source_path),
        "sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in [source_path, context_path, fault_path, harness_path, Path(__file__).resolve()]},
        "generated_harness_sha256": hashlib.sha256(harness.encode("utf-8")).hexdigest(),
        "production_methods": names + ["ThrowIfNativeFaulted", "NativeRead", "NativeWrite"],
        "scope": "offline managed fakes; no AVEVA assemblies or live E3D; rollback restoration engine only",
        "evidence_scope": "every real Apply invocation; managed snapshots make no native calls, including after faults; entry ref reads are an instrumented work counter, not elapsed time",
    }
    with tempfile.TemporaryDirectory(prefix="primitive-visual-restore-") as temporary:
        output = Path(temporary)
        (output / "Harness.cs").write_text(harness, encoding="utf-8")
        shutil.copyfile(fault_path, output / fault_path.name)
        (output / "Offline.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
            '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup>'
            '<ItemGroup><Reference Include="System.Web.Extensions" /></ItemGroup></Project>', encoding="utf-8")
        build_command = ["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"]
        built = subprocess.run(build_command, cwd=output, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=90)
        result["build"] = {"command": build_command, "returncode": built.returncode,
                           "stdout": built.stdout, "stderr": built.stderr}
        if built.returncode == 0:
            evidence_path = output / "evidence.json"
            run_command = [str(output / "bin/Release/net48/Offline.exe"), str(evidence_path)]
            ran = subprocess.run(run_command, cwd=output, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=30)
            result["run"] = {"command": run_command, "returncode": ran.returncode,
                             "stdout": ran.stdout, "stderr": ran.stderr}
            if evidence_path.is_file():
                result["evidence"] = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
    if result_path is not None:
        result_path = Path(result_path)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


class PrimitiveVisualRestoreTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "The offline C# checks require dotnet.")
    def test_production_restore_with_native_materialization_clamping_and_faults(self):
        result = run_harness()
        self.assertEqual(result["build"]["returncode"], 0, result["build"]["stdout"] + result["build"]["stderr"])
        self.assertEqual(result["run"]["returncode"], 0, result["run"]["stdout"] + result["run"]["stderr"])
        self.assertIn("Primitive visual restoration behavior checks passed:", result["run"]["stdout"])


if __name__ == "__main__":
    if "--source" in sys.argv or "--result" in sys.argv:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
        parser.add_argument("--result", type=Path, required=True)
        args = parser.parse_args()
        result = run_harness(args.source, args.result)
        for phase in ["build", "run"]:
            if phase in result:
                print(result[phase]["stdout"], end="")
                print(result[phase]["stderr"], end="", file=sys.stderr)
        sys.exit(result.get("run", result["build"])["returncode"])
    else:
        unittest.main()
