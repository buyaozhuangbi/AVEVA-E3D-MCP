"""Keep the fixed 112-tool catalog exact while adding PARAGON generation and template reads."""

import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


BASELINE = ROOT / "tests/fixtures/paragon_generation_catalog112.json"
BASELINE_SHA256 = "853476f36000ee7cc7a21875e4bb30cfd2bd26f2d5c25e5311cff65e3039aa8d"
ADDED_TOOLS = {"e3d_paragon_generation_plan", "e3d_paragon_component_template", "e3d_paragon_reference_trace", "e3d_paragon_component_copy", "e3d_paragon_component_copy_status", "e3d_paragon_component_parameters_set", "e3d_paragon_component_parameters_status"}


class ParagonGenerationCatalogCompatibilityTests(unittest.TestCase):
    def test_saved112_definitions_unchanged_and_only_expected_tools_added(self):
        saved = BASELINE.read_bytes()
        self.assertEqual(hashlib.sha256(saved).hexdigest(), BASELINE_SHA256)
        baseline = json.loads(saved.decode("utf-8-sig"))["tools"]
        current = create_server(Settings(mode="mock"))._dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )["result"]["tools"]

        self.assertEqual(len(baseline), 112)
        self.assertEqual(len(current), 119)
        self.assertEqual(len({tool["name"] for tool in baseline}), 112)
        self.assertEqual(len({tool["name"] for tool in current}), 119)
        old_by_name = {tool["name"]: tool for tool in baseline}
        current_by_name = {tool["name"]: tool for tool in current}
        self.assertEqual(set(current_by_name) - set(old_by_name), ADDED_TOOLS)
        self.assertEqual(set(old_by_name) - set(current_by_name), set())
        for name, historical in old_by_name.items():
            with self.subTest(tool=name):
                self.assertEqual(current_by_name[name], historical)


if __name__ == "__main__":
    unittest.main()
