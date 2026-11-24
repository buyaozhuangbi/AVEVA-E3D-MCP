"""MCP registration and forwarding checks for opt-in clipping diagnosis."""
from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class ClippingDiagnosisRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))

    def rpc(self, method, params):
        output = StringIO()
        request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        with patch("sys.stdout", output):
            self.server.serve([json.dumps(request) + "\n"])
        result = json.loads(output.getvalue())
        self.assertNotIn("error", result, result)
        return result["result"]

    def call(self, args):
        return self.rpc("tools/call", {"name": "e3d_diagnose_element", "arguments": args})

    def test_discovery_exposes_opt_in_without_new_tool_or_write_permission(self):
        catalog = self.rpc("tools/list", {})["tools"]
        self.assertEqual(len(catalog), 120)
        tool = next(row for row in catalog if row["name"] == "e3d_diagnose_element")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        field = tool["inputSchema"]["properties"]["include_clipping"]
        self.assertEqual(field["type"], "boolean")
        self.assertIs(field["default"], False)
        self.assertNotIn("include_clipping", tool["inputSchema"]["required"])

    def test_boolean_is_forwarded_without_enabling_writes(self):
        payload = {"ok": True, "version": "E3D21", "result": {"success": True}}
        for arguments, expected in (({}, False), ({"include_clipping": False}, False),
                                    ({"include_clipping": True}, True)):
            with self.subTest(arguments=arguments), patch(
                    "aveva_e3d_mcp.tools.diagnose_element", return_value=payload) as diagnose:
                response = self.call({"refno": "=1/2", **arguments})
                self.assertFalse(response.get("isError", False), response)
                self.assertIs(diagnose.call_args.kwargs["include_clipping"], expected)
                self.assertFalse(diagnose.call_args.args[0].settings.allow_commands)

    def test_invalid_control_never_starts_diagnosis(self):
        with patch("aveva_e3d_mcp.tools.diagnose_element") as diagnose:
            for value in (None, 0, 1, "true", [], {}):
                with self.subTest(value=value):
                    self.assertTrue(self.call({"refno": "=1/2", "include_clipping": value})["isError"])
            diagnose.assert_not_called()

    def test_diagnostic_prompt_offers_clipping_evidence_without_claiming_cause(self):
        result = self.rpc("prompts/get", {"name": "e3d_diagnose_element", "arguments": {
            "refno": "=1/2", "symptom": "Element exists but is not visible"}})
        text = result["messages"][0]["content"]["text"]
        self.assertIn("include_clipping=true", text)
        self.assertIn("unverified frame", text)


if __name__ == "__main__":
    unittest.main()
