"""Execute the complete production schema reader against recording managed SDK fakes."""

from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


class SchemaConstraintsNativeContractTests(unittest.TestCase):
    def test_schema_spatial_and_transform_routes_expose_session_and_build_identity(self):
        adapter = (BRIDGE / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
        for route in (
            'path == "/api/v1/element/schema"',
            'path == "/api/v1/element/spatial"',
            'path == "/api/v1/coordinates/transform"',
        ):
            start = adapter.index(route)
            end = adapter.find("else if", start + len(route))
            fragment = adapter[start:] if end < 0 else adapter[start:end]
            self.assertIn("session_id = sessionId", fragment, route)
            self.assertIn('bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D")', fragment, route)

        schema = (BRIDGE / "E3d21Schema.cs").read_text(encoding="utf-8-sig")
        self.assertIn("ReadEndpointContext(projectBefore, mdbBefore)", schema)
        self.assertIn("ApplyEndpointContext(result, projectBefore, mdbBefore, contextBefore", schema)
        for field in ("current_element", "current_element_type", "same_project_instance", "same_mdb_instance",
                      "identity_unchanged", "ce_unchanged"):
            self.assertIn('["' + field + '"]', schema)

        context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        spatial = context[context.index("public object GetSpatialInfo") :]
        self.assertIn("ReadEndpointContext(projectBefore, mdbBefore)", spatial)
        self.assertIn("ApplyEndpointContext(result, projectBefore, mdbBefore, contextBefore", spatial)

    @unittest.skipUnless(shutil.which("dotnet"), "The offline C# checks require dotnet.")
    def test_production_constraint_reads_gates_bounds_partial_results_and_native_faults(self):
        extract = runpy.run_path(str(ROOT / "tests" / "test_native_fault_barriers.py"))["method"]
        context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        methods = [extract(context, name) for name in ("NativeRead", "ThrowIfNativeFaulted")]
        typed_methods = [extract(context, name) for name in (
            "ResolveElementByType", "ReadAttributes", "NormalizeReadAttributes", "ReadAttributesOnUiThread")]
        harness = (ROOT / "tests" / "e3d21" / "SchemaConstraintsHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_NATIVE_READ_METHODS", "\n\n".join(methods))
        harness = harness.replace("// PRODUCTION_EXPECTED_TYPE_METHODS", "\n\n".join(typed_methods))
        with tempfile.TemporaryDirectory(prefix="schema-constraints-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            for filename in ("E3d21Schema.cs", "AttributeEditPolicy.cs", "ModelEditInput.cs", "NativeDatabaseFaultState.cs"):
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
            match = re.search(r"Schema constraints production checks passed: (\d+) assertions\.", ran.stdout)
            self.assertIsNotNone(match, ran.stdout)
            self.assertGreaterEqual(int(match.group(1)), 30)
            print(match.group(0))


if __name__ == "__main__":
    unittest.main()
