"""Freeze all 114 existing tool definitions when publishing reference tracing."""

import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


BASELINE = ROOT / "tests/fixtures/paragon_reference_trace_catalog114.json"
BASELINE_SHA256 = "1f3548dd61609474723951ea2e415b79b5117b54bc71e6859861d1db4829b1c0"


class ParagonReferenceTraceCatalogCompatibilityTests(unittest.TestCase):
    def test_existing_definitions_unchanged_and_exactly_one_reader_added(self):
        saved = BASELINE.read_bytes()
        self.assertEqual(hashlib.sha256(saved).hexdigest(), BASELINE_SHA256)
        baseline = json.loads(saved)["tools"]
        current = create_server(Settings(mode="mock"))._dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        })["result"]["tools"]
        self.assertEqual(len(baseline), 114)
        self.assertEqual(len(current), 119)
        old_by_name = {tool["name"]: tool for tool in baseline}
        current_by_name = {tool["name"]: tool for tool in current}
        self.assertEqual(len(old_by_name), 114)
        self.assertEqual(len(current_by_name), 119)
        self.assertEqual(set(current_by_name) - set(old_by_name), {"e3d_paragon_reference_trace", "e3d_paragon_component_copy", "e3d_paragon_component_copy_status", "e3d_paragon_component_parameters_set", "e3d_paragon_component_parameters_status"})
        self.assertEqual(set(old_by_name) - set(current_by_name), set())
        for name, historical in old_by_name.items():
            with self.subTest(tool=name):
                self.assertEqual(current_by_name[name], historical)


if __name__ == "__main__":
    unittest.main()
