"""Execute the complete Drawlist-removal implementation without AVEVA native code."""
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
BRIDGE = ROOT / "bridge/e3d21"


def run_harness(result_path=None):
    selected = {
        "E3d21Context.cs": ["NativeRead", "NativeWrite", "ThrowIfNativeFaulted", "OnUi", "TryBeginCommand",
            "EndCommand", "IsCommandActive", "BridgeOperationBusy", "NativeDispatchBusy", "ExecutionDiagnostics",
            "IsUsable", "CaptureDrawListVisual", "CompareDrawListVisualSnapshots"],
        "E3d21PrimitiveVisual.cs": ["PrimitiveVisualReadMap", "PrimitiveVisualWriteMap", "PrimitiveVisualRequireComplete",
            "PrimitiveVisualKey", "PrimitiveVisualValuesEqual", "PrimitiveVisualValidateExpected", "PrimitiveVisualCanonicalReference",
            "CompareDrawListPrimitiveSnapshots", "PrimitiveVisualRefresh"],
        "E3d21SelectionEditing.cs": ["SelectionEditingReadState", "SelectionEditingReadContext", "SelectionEditingElementReference", "SelectionEditingSameContext"],
        "E3d21DisplayState.cs": ["DisplayStateRequireDatabaseSubtree"],
        "E3d21PrimitiveEditing.cs": ["PrimitiveHash"],
        "E3d21ViewCamera.cs": ["CameraRequireVector", "CameraNormalized", "CameraFinite", "CameraNumberMatches", "CameraVectorMatches", "CameraMatches"],
    }
    extracted = []
    for filename, names in selected.items():
        source = (BRIDGE / filename).read_text(encoding="utf-8-sig")
        extracted.extend(method(source, name) for name in names)
    template_path = ROOT / "tests/e3d21/DrawListRemovalHarness.cs"
    harness = template_path.read_text(encoding="utf-8").replace("// PRODUCTION_METHODS", "\n\n".join(extracted))
    files = ["E3d21DrawListRemoval.cs", "NativeDatabaseFaultState.cs", "ModelEditInput.cs", "AttributeEditPolicy.cs"]
    inputs = [BRIDGE / name for name in set(selected) | set(files)] + [template_path, Path(__file__).resolve()]
    result = {"utc": datetime.now(timezone.utc).isoformat(), "scope": "complete removal production file plus real observation/hash/fault helpers; managed SDK and persistence fakes; no AVEVA runtime",
              "sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs},
              "production_methods": selected, "generated_harness_sha256": hashlib.sha256(harness.encode()).hexdigest()}
    with tempfile.TemporaryDirectory(prefix="drawlist-removal-") as temporary:
        output = Path(temporary)
        (output / "Harness.cs").write_text(harness, encoding="utf-8")
        for filename in files:
            shutil.copyfile(BRIDGE / filename, output / filename)
        (output / "Offline.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework><OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
        command = ["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"]
        build = subprocess.run(command, cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        result["build"] = {"command": command, "returncode": build.returncode, "stdout": build.stdout, "stderr": build.stderr}
        if build.returncode == 0:
            command = [str(output / "bin/Release/net48/Offline.exe")]
            run = subprocess.run(command, cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            result["run"] = {"command": command, "returncode": run.returncode, "stdout": run.stdout, "stderr": run.stderr}
    if result_path:
        Path(result_path).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


class DrawListRemovalContracts(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline C# checks require dotnet.")
    def test_complete_production_removal_guards_observation_and_faults(self):
        result = run_harness()
        for phase in ("build", "run"):
            self.assertEqual(result[phase]["returncode"], 0, result[phase]["stdout"] + result[phase]["stderr"])
        self.assertIn("Drawlist removal production checks passed:", result["run"]["stdout"])


if __name__ == "__main__":
    if "--result" in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--result", type=Path, required=True)
        result = run_harness(parser.parse_args().result)
        for phase in ("build", "run"):
            if phase in result:
                print(result[phase]["stdout"], end="")
                print(result[phase]["stderr"], end="", file=sys.stderr)
        sys.exit(result.get("run", result["build"])["returncode"])
    unittest.main()
