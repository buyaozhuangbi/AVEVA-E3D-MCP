from __future__ import annotations

from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import runpy
import shutil
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


class ConnectionPointsNativeContractTests(unittest.TestCase):
    def test_connection_point_context_and_http_identity_contracts(self) -> None:
        points = (BRIDGE / "E3d21ConnectionPoints.cs").read_text(encoding="utf-8-sig")
        schema = (BRIDGE / "E3d21Schema.cs").read_text(encoding="utf-8-sig")
        self.assertIn("ReadEndpointContext(projectBefore, mdbBefore)", points)
        self.assertIn("ReadEndpointContext(projectAfter, mdbAfter)", points)
        self.assertIn("ApplyEndpointContext(result, projectBefore, mdbBefore, contextBefore", points)
        for field in ("current_element_type", "same_project_instance", "same_mdb_instance",
                      "identity_unchanged", "ce_unchanged", "boundary_agreement"):
            self.assertIn('["' + field + '"]', schema)

        adapter = (BRIDGE / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
        for path in ("/api/v1/element/connection-points", "/api/v1/element/connection-trace"):
            start = adapter.index('path == "' + path + '"')
            end = adapter.find("else if", start + len(path))
            route = adapter[start:] if end < 0 else adapter[start:end]
            self.assertIn("session_id = sessionId", route, path)
            self.assertIn('bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D")', route, path)

    def test_pair_expected_types_resolve_each_endpoint_independently(self) -> None:
        source = (BRIDGE / "E3d21ConnectionPoints.cs").read_text(encoding="utf-8-sig")
        pair = source[source.index("public object CheckConnectionPair"):source.index("private static Dictionary<string, object> ConnectionReadPairEndpoint")]
        endpoint = source[source.index("private static Dictionary<string, object> ConnectionReadPairEndpoint"):source.index("private static Dictionary<string, object> ConnectionPairElementIdentity")]
        self.assertIn("ConnectionReadPairEndpoint(firstTarget, firstPointNumber, requestedFirstType)", pair)
        self.assertIn("ConnectionReadPairEndpoint(secondTarget, secondPointNumber, requestedSecondType)", pair)
        self.assertIn("[\"requested_expected_type\"] = expectedType", endpoint)
        self.assertIn("ResolveElementByType(refno, expectedType)", endpoint)

    @unittest.skipUnless(shutil.which("dotnet"), "The offline C# checks require dotnet.")
    def test_production_pcref_and_selection_contracts(self) -> None:
        extract = runpy.run_path(str(ROOT / "tests" / "test_native_fault_barriers.py"))["method"]
        source = (BRIDGE / "E3d21ConnectionPoints.cs").read_text(encoding="utf-8-sig")
        methods = [extract(source, name) for name in (
            "ConnectionPointListedInPpls",
            "ConnectionCoverageComplete",
            "ConnectionDecodePcrefPointNumber",
            "ConnectionPcrefUsesImplicitTube",
            "ConnectionPcrefEncodingStatus",
            "ConnectionPcrefResolutionComplete",
            "ConnectionImplicitTubeExpression",
            "ConnectionUnitMetadataMatches",
            "ConnectionUnitFactorsMatch",
            "ConnectionBoreIsPositive",
            "ConnectionPairDirectionRelationship",
            "ConnectionPairVector",
            "ConnectionComparePairPositions",
            "ConnectionComparePairDirections",
            "ConnectionComparePairReferences",
            "ConnectionPairReferenceStatus",
        )]
        harness = (ROOT / "tests" / "e3d21" / "ConnectionPointsHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_METHODS", "\n\n".join(methods))
        with tempfile.TemporaryDirectory(prefix="connection-points-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
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
            self.assertIn("Connection point production checks passed:", ran.stdout)

    def test_partial_native_diagnostic_is_preserved_without_marking_call_failed(self) -> None:
        payload = {
            "ok": True,
            "version": "E3D21",
            "result": {
                "success": True,
                "complete": False,
                "element": {"refno": "=1/2", "type": "NOZZ"},
                "frame": {"requested": "WORLD", "refno": "=1/1", "type": "WORLD"},
                "requested_point_numbers": [],
                "point_numbers": [1],
                "enumeration": {
                    "source": "PPLS",
                    "available": True,
                    "complete": True,
                    "native_point_numbers": [1],
                    "error": None,
                },
                "points": [{
                    "number": 1,
                    "complete": False,
                    "position": {"available": True, "unit": "mm", "x": 10.0, "y": 20.0, "z": 30.0},
                    "direction": {"available": False, "reason": "read_failed", "error": "PDIR unavailable"},
                    "errors": {"direction": "PDIR unavailable"},
                }],
                "references": {},
                "errors": {"P1.direction": "PDIR unavailable"},
                "ce_before": "=1/2",
                "ce_after": "=1/2",
                "ce_unchanged": True,
                "source": "Public SDK PPLS/PPEXST and qualified PPOS/PDIR/PPBO/PPCO/PCREF reads.",
            },
        }
        original = deepcopy(payload)
        server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "e3d_connection_points", "arguments": {"refno": "=1/2"}},
        })
        output = StringIO()
        with patch.object(BridgeClient, "connection_points", return_value=payload), patch("sys.stdout", output):
            server.serve([request + "\n"])

        response = json.loads(output.getvalue())["result"]
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"], original)
        self.assertEqual(json.loads(response["content"][0]["text"]), original)
        self.assertEqual(payload, original)


if __name__ == "__main__":
    unittest.main()
