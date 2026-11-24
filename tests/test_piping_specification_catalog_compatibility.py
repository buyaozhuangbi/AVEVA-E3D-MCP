"""Keep the fixed 110-tool catalog exact while adding bounded reads and PARAGON tools."""

import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


BASELINE = ROOT / "tests/fixtures/piping_specification_catalog110.json"
BASELINE_SHA256 = "be91774d3c8f21a43bfa03386f38bf1f724ba5d94fac58b236e8958ff0393964"
ADDED_TOOLS = {"e3d_piping_specification_read", "e3d_team_database_identity_manifest", "e3d_paragon_generation_plan", "e3d_paragon_component_template", "e3d_paragon_reference_trace", "e3d_paragon_component_copy", "e3d_paragon_component_copy_status", "e3d_paragon_component_parameters_set", "e3d_paragon_component_parameters_status", "e3d_paragon_generate_from_standard", "e3d_paragon_component_create", "e3d_paragon_component_create_status"}


class PipingSpecificationCatalogCompatibilityTests(unittest.TestCase):
    def test_saved110_definitions_unchanged_and_only_expected_tools_added(self):
        saved = BASELINE.read_bytes()
        self.assertEqual(hashlib.sha256(saved).hexdigest(), BASELINE_SHA256)
        baseline = json.loads(saved.decode("utf-8-sig"))["tools"]
        current = create_server(Settings(mode="mock"))._dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )["result"]["tools"]

        self.assertEqual(len(baseline), 110)
        self.assertEqual(len(current), 122)
        self.assertEqual(len({tool["name"] for tool in baseline}), 110)
        self.assertEqual(len({tool["name"] for tool in current}), 122)
        old_by_name = {tool["name"]: tool for tool in baseline}
        current_by_name = {tool["name"]: tool for tool in current}
        self.assertEqual(set(current_by_name) - set(old_by_name), ADDED_TOOLS)
        self.assertEqual(set(old_by_name) - set(current_by_name), set())
        for name, historical in old_by_name.items():
            with self.subTest(tool=name):
                self.assertEqual(current_by_name[name], historical)


if __name__ == "__main__":
    unittest.main()
