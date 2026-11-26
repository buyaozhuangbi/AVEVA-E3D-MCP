"""Freeze the 117 tools preceding the numeric PARAM writer."""

import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server
from catalog_compatibility import CURRENT_MOCK_TOOL_COUNT, MODELING_TEAM_TOOLS, E3D_IDENTITY_TOOLS, with_modeling_additions


BASELINE = ROOT / "tests/fixtures/paragon_component_parameters_catalog117.json"
BASELINE_SHA256 = "d9cb586289dc584e05b6ad84379cdb9b05b7096abffb9d6aadddc14c8e9fb04e"
ADDED = {"e3d_paragon_component_parameters_set", "e3d_paragon_component_parameters_status", "e3d_paragon_generate_from_standard", "e3d_paragon_component_create", "e3d_paragon_component_create_status", "e3d_piping_route_create", "e3d_piping_route_verify", *MODELING_TEAM_TOOLS, *E3D_IDENTITY_TOOLS}


class ParagonComponentParametersCatalogCompatibilityTests(unittest.TestCase):
    def test_previous_117_definitions_unchanged_and_only_parameter_tools_added(self):
        raw = BASELINE.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), BASELINE_SHA256)
        baseline = json.loads(raw)["tools"]
        current = create_server(Settings(mode="mock"))._dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        })["result"]["tools"]
        before = {tool["name"]: tool for tool in baseline}
        after = {tool["name"]: tool for tool in current}
        self.assertEqual(len(baseline), 117)
        self.assertEqual(len(before), 117)
        self.assertEqual(len(current), CURRENT_MOCK_TOOL_COUNT)
        self.assertEqual(len(after), CURRENT_MOCK_TOOL_COUNT)
        self.assertEqual(set(after) - set(before), ADDED)
        self.assertEqual(set(before) - set(after), set())
        for name, definition in before.items():
            with self.subTest(tool=name):
                self.assertEqual(after[name], with_modeling_additions(definition))
        writer, status = (after[name] for name in (
            "e3d_paragon_component_parameters_set", "e3d_paragon_component_parameters_status"))
        self.assertFalse(writer["annotations"]["readOnlyHint"])
        self.assertTrue(status["annotations"]["readOnlyHint"])
        self.assertNotIn("parameter_bits", writer["inputSchema"]["properties"])


if __name__ == "__main__":
    unittest.main()
