"""DISH transport and complete production primitive workflow with managed SDK fakes.

The C# executable loads no AVEVA assembly. It compiles the production primitive
reader/writer unchanged; only surrounding session/frame facilities are fakes.
"""
from pathlib import Path
from io import StringIO
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"
sys.path.insert(0, str(ROOT / "src"))
from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class DishGeometryContractsTests(unittest.TestCase):
    def rpc(self, name, arguments):
        server = create_server(Settings(mode="bridge", allow_commands=True))
        output = StringIO()
        with patch("sys.stdout", output):
            server.serve([json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                     "params": {"name": name, "arguments": arguments}}) + "\n"])
        return json.loads(output.getvalue())["result"]

    def test_radius_transport_normalization_and_invalid_values(self):
        payload = {"ok": True, "result": {"success": True, "dry_run": True}}
        with patch.object(BridgeClient, "update_primitive", return_value=payload) as update:
            for radius in (0, 0.25, 1234.5):
                response = self.rpc("e3d_update_primitive", {"refno": "/DISH", "dimensions": {" radi ": radius}})
                self.assertFalse(response.get("isError", False), response)
                update.assert_called_with("/DISH", dimensions={"RADI": float(radius)}, wrt="OWNER", dry_run=True)
        with patch.object(BridgeClient, "update_primitive") as update:
            for radius in (-1, True, False, None, "12", float("nan"), float("inf"), 10 ** 400):
                response = self.rpc("e3d_update_primitive", {"refno": "/DISH", "dimensions": {"RADI": radius}})
                self.assertTrue(response["isError"])
            response = self.rpc("e3d_update_primitive", {"refno": "/DISH", "dimensions": {"radi": 1, " RADI ": 2}})
            self.assertTrue(response["isError"])
        update.assert_not_called()

    def test_radius_read_and_failed_native_receipt_are_preserved(self):
        payload = {"ok": True, "result": {"success": True, "shape": "DISH", "negative": False,
                   "unit": "mm", "dimensions": {"DIAM": 100, "HEIG": 25, "RADI": 0},
                   "complete": True, "editable": True, "revision": "a" * 64}}
        with patch.object(BridgeClient, "primitive_geometry", return_value=payload):
            response = self.rpc("e3d_primitive_geometry", {"refno": "/DISH"})
        self.assertEqual(response["structuredContent"], payload)
        failed = {"ok": True, "result": {"success": False, "write_attempted": False,
                  "edit_reasons": ["DISH must have no children"], "revision": None}}
        with patch.object(BridgeClient, "update_primitive", return_value=failed) as update:
            response = self.rpc("e3d_update_primitive", {"refno": "/DISH", "dimensions": {"RADI": 2}})
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"], failed)
        update.assert_called_once()

    @unittest.skipUnless(shutil.which("dotnet"), "Offline checks require dotnet.")
    def test_complete_production_reader_writer_and_fault_boundaries(self):
        extract = runpy.run_path(str(ROOT / "tests" / "test_native_fault_barriers.py"))["method"]
        methods = []
        selected = {
            "E3d21Context.cs": ("NativeRead", "NativeWrite", "ThrowIfNativeFaulted", "IsUsable", "FiniteVector", "DirectionVector"),
            "E3d21ModelEditing.cs": ("ModelRequireSettable", "ModelRequireSetter", "ModelReference"),
        }
        for filename, names in selected.items():
            source = (BRIDGE / filename).read_text(encoding="utf-8-sig")
            methods.extend(extract(source, name) for name in names)
        # Execute the real copy-node admission prefix for negative DISH exclusions.
        # Full positive DISH copy admission is covered by test_copy_dish_contracts.
        copy = extract((BRIDGE / "E3d21CopyEditing.cs").read_text(encoding="utf-8-sig"), "CopyReadNode")
        copy = copy[:copy.index("        var node = new CopyNode")]
        methods.append(copy + "    }\n")
        harness = (ROOT / "tests" / "e3d21" / "DishGeometryHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_METHODS", "\n\n".join(methods))
        with tempfile.TemporaryDirectory(prefix="dish-geometry-contract-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            for filename in ("E3d21PrimitiveEditing.cs", "PrimitiveUpdateRunner.cs", "ModelEditInput.cs",
                             "AttributeEditPolicy.cs", "MemberPage.cs", "NativeDatabaseFaultState.cs"):
                shutil.copyfile(BRIDGE / filename, output / filename)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("DISH production checks passed:", ran.stdout)
            report = os.environ.get("E3D_DISH_CONTRACT_OUTPUT")
            if report:
                Path(report).write_text(ran.stdout, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
