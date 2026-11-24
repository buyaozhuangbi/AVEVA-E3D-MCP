"""Optional typed connection-point reads preserve legacy calls and fail closed."""

from contextlib import ExitStack
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server
from catalog_compatibility import CONNECTION_POINTS_EXPECTED_TYPE_SCHEMA


class ConnectionPointsExpectedTypeTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=False)
        self.client = BridgeClient(self.settings)

    def call(self, arguments, settings=None):
        server = create_server(settings or self.settings)
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "e3d_connection_points", "arguments": arguments}}
        output = StringIO()
        with patch("sys.stdout", output):
            server.serve([json.dumps(request) + "\n"])
        return json.loads(output.getvalue())["result"]

    @staticmethod
    def native(kind="TUBI", **changes):
        return {"ok": True, "version": "E3D21", "result": {
            "success": True, "complete": False, "element": {"refno": "=1/2", "type": kind},
            "points": [], "errors": {"enumeration": "PPLS unavailable"}, **changes,
        }}

    def test_schema_adds_only_an_optional_type_selector(self):
        server = create_server(self.settings)
        tools = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]
        self.assertEqual(len(tools), 120)
        tool = next(item for item in tools if item["name"] == "e3d_connection_points")
        self.assertEqual(tool["inputSchema"]["properties"]["expected_type"], CONNECTION_POINTS_EXPECTED_TYPE_SCHEMA)
        self.assertEqual(tool["inputSchema"]["required"], ["refno"])
        self.assertFalse(tool["inputSchema"]["additionalProperties"])
        self.assertTrue(tool["annotations"]["readOnlyHint"])

    def test_tool_normalizes_selector_and_keeps_partial_native_evidence(self):
        payload = self.native()
        original = deepcopy(payload)
        with patch.object(BridgeClient, "connection_points", return_value=payload) as read:
            response = self.call({"refno": " =1/2 ", "wrt": " owner ",
                                  "point_numbers": [2, 1], "expected_type": " tubi "})
        read.assert_called_once_with("=1/2", "OWNER", [2, 1], expected_type="TUBI")
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"], original)
        self.assertEqual(payload, original)

    def test_invalid_selector_never_dispatches(self):
        for value in (None, 7, True, [], {}, "TU\nBI", "TUBI; DELETE CE", "T" * 65):
            with self.subTest(value=value), patch.object(BridgeClient, "connection_points") as read:
                self.assertTrue(self.call({"refno": "=1/2", "expected_type": value})["isError"])
                read.assert_not_called()
            with self.subTest(bridge_value=value), patch.object(self.client, "_request") as request:
                with self.assertRaises(ValueError):
                    self.client.connection_points("=1/2", expected_type=value)
                request.assert_not_called()

    def test_bridge_payload_preserves_old_positional_version_and_omits_empty_selector(self):
        for selector in ("", "  "):
            with self.subTest(selector=selector), patch.object(self.client, "_request", return_value=self.native("BRAN")) as request:
                self.client.connection_points("=1/2", "WORLD", [2, 1], "E3D21", expected_type=selector)
                request.assert_called_once_with("POST", "/api/v1/element/connection-points",
                    {"refno": "=1/2", "wrt": "WORLD", "point_numbers": [2, 1]}, version="E3D21")
        with patch.object(BridgeClient, "connection_points", return_value=self.native("BRAN")) as read:
            self.call({"refno": "=1/2", "expected_type": ""})
            read.assert_called_once_with("=1/2", "WORLD", [])

    def test_bridge_sends_normalized_selector_and_checks_actual_element_type(self):
        payload = self.native()
        with patch.object(self.client, "_request", return_value=payload) as request:
            response = self.client.connection_points("=1/2", "OWNER", [2], expected_type=" tubi ")
        request.assert_called_once_with("POST", "/api/v1/element/connection-points",
            {"refno": "=1/2", "wrt": "OWNER", "point_numbers": [2], "expected_type": "TUBI"}, version=None)
        self.assertEqual(response, payload)
        for actual in ("BRAN", "ELBO", None):
            payload = self.native(actual)
            original = deepcopy(payload)
            with self.subTest(actual=actual), patch.object(self.client, "_request", return_value=payload):
                response = self.client.connection_points("=1/2", expected_type="TUBI")
                self.assertIs(response["ok"], False)
                self.assertIn("did not resolve expected_type TUBI", response["error"])
                self.assertEqual(response["result"], original["result"])
                self.assertEqual(payload, original)

    def test_mock_failure_and_native_failure_do_not_fabricate_typed_points(self):
        mock = BridgeClient(Settings(mode="mock", allow_commands=False))
        with patch.object(mock, "_request") as request:
            result = mock.connection_points("=1/2", expected_type="TUBI")["result"]
        request.assert_not_called()
        self.assertFalse(result["success"])
        self.assertFalse(result["available"])
        self.assertEqual(result["points"], [])
        failure = {"ok": False, "error": "older add-in rejected expected_type"}
        with patch.object(self.client, "_request", return_value=failure) as request:
            self.assertEqual(self.client.connection_points("=1/2", expected_type="TUBI"), failure)
        self.assertEqual(request.call_count, 1)

    def test_typed_read_uses_no_command_model_display_or_capture_writes(self):
        forbidden = ("command", "command_batch", "create_element", "copy_element", "set_transform",
                     "set_attribute", "set_attributes", "set_drawlist_visual", "set_primitive_visual",
                     "fit_view", "set_view", "restore_view", "capture_view")
        with ExitStack() as stack:
            writes = [stack.enter_context(patch.object(BridgeClient, name)) for name in forbidden]
            request = stack.enter_context(patch.object(BridgeClient, "_request", return_value=self.native()))
            response = self.call({"refno": "=1/2", "expected_type": "TUBI", "point_numbers": [1]})
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(request.call_count, 1)
        for write in writes:
            write.assert_not_called()


class ConnectionPointsExpectedTypeNativeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline native contracts require dotnet.")
    def test_production_selection_and_http_route_without_loading_e3d(self):
        bridge = ROOT / "bridge/e3d21"
        sdk = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10"))
        json_dll = sdk / "Newtonsoft.Json.dll"
        if not json_dll.is_file():
            self.skipTest("The installed Newtonsoft.Json assembly is required for the HTTP contract.")
        extract = runpy.run_path(str(ROOT / "tests/test_native_fault_barriers.py"))["method"]
        block_end = runpy.run_path(str(ROOT / "tests/e3d21/run-attribute-batch-http-harness.py"))["block_end"]
        points = (bridge / "E3d21ConnectionPoints.cs").read_text(encoding="utf-8-sig")
        context = (bridge / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        editing = (bridge / "E3d21ModelEditing.cs").read_text(encoding="utf-8-sig")
        schema = (bridge / "E3d21Schema.cs").read_text(encoding="utf-8-sig")
        adapter = (bridge / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
        methods = [extract(points, name) for name in ("GetConnectionPoints", "ConnectionResolveImplicitTubeElement",
            "ConnectionImplicitTubeExpression", "ConnectionIdentity", "ConnectionPointListedInPpls",
            "ConnectionCoverageComplete", "ConnectionUnavailable")]
        methods.extend((extract(context, "ResolveElementByType"), extract(editing, "ModelRequireElement")))
        endpoint_context = [extract(schema, name) for name in ("ReadEndpointContext", "ApplyEndpointContext",
            "EndpointReference", "EndpointType", "EndpointContextEqual", "EndpointContextText")]
        marker = 'else if (method == "POST" && path == "/api/v1/element/connection-points")'
        opening = adapter.index("{", adapter.index(marker) + len(marker))
        route = adapter[opening + 1:block_end(adapter, opening) - 1]
        harness = (ROOT / "tests/e3d21/ConnectionPointsExpectedTypeHarness.cs").read_text(encoding="utf-8")
        harness = (harness.replace("// PRODUCTION_READER", "\n\n".join(methods))
                   .replace("// PRODUCTION_CONTEXT", "\n\n".join(endpoint_context))
                   .replace("// PRODUCTION_ROUTE", route).replace("// PRODUCTION_READ_STRING", extract(adapter, "ReadString")))
        with tempfile.TemporaryDirectory(prefix="connection-points-typed-") as directory:
            output = Path(directory)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            for name in ("ModelEditInput.cs", "AttributeEditPolicy.cs"):
                shutil.copyfile(bridge / name, output / name)
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup><ItemGroup>'
                '<Reference Include="Newtonsoft.Json"><HintPath>' + escape(str(json_dll)) +
                '</HintPath><Private>True</Private></Reference></ItemGroup></Project>', encoding="utf-8")
            build = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"], cwd=output,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(output / "bin/Release/net48/Offline.exe")], cwd=output,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("Typed connection point production checks passed:", run.stdout)


if __name__ == "__main__":
    unittest.main()
