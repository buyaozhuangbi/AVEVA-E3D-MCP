"""Keep the fixed 111-tool catalog exact while adding team identity and PARAGON tools."""

import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server
from catalog_compatibility import with_modeling_additions


BASELINE = ROOT / "tests/fixtures/team_database_identity_catalog111.json"
BASELINE_SHA256 = "d978cd04ad41be67dfbcc4618fd2dbc55ee02b939622f9153b8a76e8d55f92db"
ADDED_TOOLS = {"e3d_team_database_identity_manifest", "e3d_paragon_generation_plan", "e3d_paragon_component_template", "e3d_paragon_reference_trace", "e3d_paragon_component_copy", "e3d_paragon_component_copy_status", "e3d_paragon_component_parameters_set", "e3d_paragon_component_parameters_status", "e3d_paragon_generate_from_standard", "e3d_paragon_component_create", "e3d_paragon_component_create_status"}


class TeamDatabaseIdentityCatalogCompatibilityTests(unittest.TestCase):
    def test_saved111_definitions_unchanged_and_only_expected_tools_added(self):
        saved = BASELINE.read_bytes()
        self.assertEqual(hashlib.sha256(saved).hexdigest(), BASELINE_SHA256)
        baseline = json.loads(saved.decode("utf-8-sig"))["tools"]
        current = create_server(Settings(mode="mock"))._dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )["result"]["tools"]

        self.assertEqual(len(baseline), 111)
        self.assertEqual(len(current), 122)
        self.assertEqual(len({tool["name"] for tool in baseline}), 111)
        self.assertEqual(len({tool["name"] for tool in current}), 122)
        old_by_name = {tool["name"]: tool for tool in baseline}
        current_by_name = {tool["name"]: tool for tool in current}
        self.assertEqual(set(current_by_name) - set(old_by_name), ADDED_TOOLS)
        self.assertEqual(set(old_by_name) - set(current_by_name), set())
        for name, historical in old_by_name.items():
            with self.subTest(tool=name):
                self.assertEqual(current_by_name[name], with_modeling_additions(historical))


if __name__ == "__main__":
    unittest.main()
