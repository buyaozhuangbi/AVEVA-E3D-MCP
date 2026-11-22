"""Execute production batch measurements and shared readers with managed SDK fakes."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_model_validation_native_contract import extract_method


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


class MeasureBatchNativeContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "The offline C# checks require dotnet.")
    def test_production_batch_and_native_fault_boundaries(self) -> None:
        selected = {
            "E3d21Context.cs": ["GetSpatialInfo", "ReadSpatialInfoOnUi", "ReadSpatialField", "DirectionVector", "FiniteVector",
                                  "ThrowIfNativeFaulted", "NativeRead", "NativeWrite", "SafeString", "ElementRef", "ElementType",
                                  "IsUsable", "CurrentMdb", "OnUi"],
            "E3d21Schema.cs": ["ReadEndpointContext", "ApplyEndpointContext", "EndpointReference", "EndpointType",
                                "EndpointContextEqual", "EndpointContextText"],
            "E3d21ModelEditing.cs": ["ModelRequireElement", "ModelReference", "ModelIdentity", "ModelCurrentReference", "ModelCeUnchanged", "ModelCaptureCeAfter"],
            "E3d21GeometryOverlap.cs": ["CheckGeometryRelation", "CheckGeometryRelationBatch", "GeometryRelationExact",
                                        "GeometryRelationBox", "GeometryRelationPending", "GeometryRelationRead"],
            "ModelEditInput.cs": ["ExplicitReference", "IsReferencePair"],
        }
        methods = []
        for filename, names in selected.items():
            source = (BRIDGE / filename).read_text(encoding="utf-8-sig")
            if filename == "ModelEditInput.cs":
                source = source.replace("    internal static", "    public static")
            for name in names:
                body = extract_method(source, name)
                if name == "OnUi":
                    # Schedule a native output callback after the operation has
                    # returned but before the production dispatch post-check.
                    body = body.replace("result = operation();", "result = operation();\n                if (Scenario.AfterOperation != null) Scenario.AfterOperation();")
                methods.append(body)
        # The two pure input helpers belong to ModelEditInput, not the context.
        context_methods, input_methods = methods[:-2], methods[-2:]
        harness = (ROOT / "tests" / "e3d21" / "MeasureBatchHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_METHODS", "\n\n".join(context_methods))
        harness = harness.replace("// PRODUCTION_INPUT_METHODS", "\n\n".join(input_methods))
        with tempfile.TemporaryDirectory(prefix="measure-batch-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            for filename in ["E3d21MeasureBatch.cs", "E3d21GeometryBounds.cs", "E3d21GeometryVolume.cs", "NativeDatabaseFaultState.cs"]:
                shutil.copyfile(BRIDGE / filename, output / filename)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                '<TargetFramework>net48</TargetFramework><OutputType>Exe</OutputType>'
                '<LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Batch measurement production checks passed:", ran.stdout)
            self.assertIn("Standalone negative volume production checks passed.", ran.stdout)
            self.assertIn("Geometry relation production checks passed.", ran.stdout)
            self.assertIn("Geometry relation batch production checks passed.", ran.stdout)


if __name__ == "__main__":
    unittest.main()
