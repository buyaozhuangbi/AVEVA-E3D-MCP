"""Exercise the complete production selection writer against managed SDK fakes.

The temporary executable links no AVEVA assembly and never opens E3D. Shared
snapshot/hash/fault-dispatch methods are taken from the production sources too.
"""
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"
NEWTONSOFT = Path(r"C:\Program Files (x86)\AVEVA\Everything3D2.10\Newtonsoft.Json.dll")


class SelectionEditingNativeContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet") and NEWTONSOFT.is_file(), "Offline checks require dotnet and Newtonsoft.Json.")
    def test_production_replace_clear_guards_and_independent_readback(self) -> None:
        extract = runpy.run_path(str(ROOT / "tests" / "test_native_fault_barriers.py"))["method"]
        selected = {
            "E3d21Context.cs": ["NativeRead", "NativeWrite", "ThrowIfNativeFaulted", "OnUi",
                                 "TryBeginCommand", "EndCommand", "IsCommandActive", "BridgeOperationBusy",
                                 "NativeDispatchBusy", "ExecutionDiagnostics", "BusyResult", "IsUsable",
                                 "CaptureDrawListVisual", "CompareDrawListVisualSnapshots"],
            "E3d21PrimitiveVisual.cs": ["PrimitiveVisualReadMap", "PrimitiveVisualWriteMap",
                                       "PrimitiveVisualRequireComplete", "PrimitiveVisualKey",
                                       "PrimitiveVisualValuesEqual", "CompareDrawListPrimitiveSnapshots"],
            "E3d21DisplayState.cs": ["DisplayStateRequireDatabaseSubtree"],
            "E3d21PrimitiveEditing.cs": ["PrimitiveHash"],
        }
        methods = []
        for name, names in selected.items():
            source = (BRIDGE / name).read_text(encoding="utf-8-sig")
            methods.extend(extract(source, method_name) for method_name in names)
        harness = (ROOT / "tests" / "e3d21" / "SelectionEditingHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_METHODS", "\n\n".join(methods))
        syntax = runpy.run_path(str(ROOT / "tests" / "test_model_validation_native_contract.py"))
        adapter = (BRIDGE / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
        marker = 'else if (method == "POST" && path == "/api/v1/view/graphical-selection")'
        opening = adapter.index("{", adapter.index(marker) + len(marker))
        route = adapter[opening + 1:syntax["block_end"](adapter, opening) - 1]
        route_methods = [syntax["extract_method"](adapter, name) for name in
                         ("ReadUniqueBodyAsync", "ReadBoolean", "ReadString")]
        route_methods[0] = route_methods[0].replace("HttpListenerRequest request", "SelectionInnerRequest request")
        harness = harness.replace("// PRODUCTION_ROUTE", route).replace("// ROUTE_METHODS", "\n\n".join(route_methods))
        with tempfile.TemporaryDirectory(prefix="selection-editing-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            for filename in ("E3d21SelectionEditing.cs", "NativeDatabaseFaultState.cs"):
                shutil.copyfile(BRIDGE / filename, output / filename)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                '<TargetFramework>net48</TargetFramework><OutputType>Exe</OutputType>'
                '<LangVersion>latest</LangVersion></PropertyGroup>'
                '<ItemGroup><Reference Include="Newtonsoft.Json"><HintPath>' + escape(str(NEWTONSOFT)) +
                '</HintPath><Private>True</Private></Reference></ItemGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Selection editing production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
