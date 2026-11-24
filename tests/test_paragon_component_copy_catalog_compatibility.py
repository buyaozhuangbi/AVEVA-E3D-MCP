"""The SCOM writer and its managed status lookup are strictly additive."""

import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


BASELINE = ROOT / "tests/fixtures/paragon_component_copy_catalog115.json"
BASELINE_SHA256 = "9417991056fc947fa754122572ddb9b41039643332197802d8446268a1c07da0"


class ParagonComponentCopyCatalogCompatibilityTests(unittest.TestCase):
    def test_previous_115_definitions_unchanged_and_exactly_two_tools_added(self):
        saved = BASELINE.read_bytes()
        self.assertEqual(hashlib.sha256(saved).hexdigest(), BASELINE_SHA256)
        baseline = json.loads(saved)["tools"]
        current = create_server(Settings(mode="mock"))._dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        })["result"]["tools"]
        self.assertEqual(len(baseline), 115)
        self.assertEqual(len(current), 122)
        before = {tool["name"]: tool for tool in baseline}
        after = {tool["name"]: tool for tool in current}
        self.assertEqual(len(before), 115)
        self.assertEqual(len(after), 122)
        self.assertEqual(set(after) - set(before), {"e3d_paragon_component_copy", "e3d_paragon_component_copy_status", "e3d_paragon_component_parameters_set", "e3d_paragon_component_parameters_status", "e3d_paragon_generate_from_standard", "e3d_paragon_component_create", "e3d_paragon_component_create_status"})
        self.assertEqual(set(before) - set(after), set())
        for name, definition in before.items():
            with self.subTest(tool=name):
                self.assertEqual(after[name], definition)
        self.assertFalse(after["e3d_paragon_component_copy"]["annotations"]["readOnlyHint"])
        self.assertTrue(after["e3d_paragon_component_copy_status"]["annotations"]["readOnlyHint"])


if __name__ == "__main__":
    unittest.main()
