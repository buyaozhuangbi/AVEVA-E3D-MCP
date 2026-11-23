"""Add one template read and permit only the exact generation-plan description update."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


BASELINE = ROOT / "tests/fixtures/paragon_component_template_catalog113.json"
BASELINE_SHA256 = "2362d9130af2d5a453e23325cc996143fa7beacf9fce5fe82cffed399b38e179"
ADDED_TOOLS = {"e3d_paragon_component_template", "e3d_paragon_reference_trace", "e3d_paragon_component_copy", "e3d_paragon_component_copy_status"}
PLAN = "e3d_paragon_generation_plan"
PLAN_DESCRIPTION = (
    "Compose one review draft for copying an existing SCOM leaf within the same explicit Catalogue CATE. "
    "The user configures the existing Catalogue database through ADMIN. Reads health, PARAGON module 81 "
    "identity, source specification, component-template actual type and bounded numeric PARAM evidence, "
    "parent specification and parent schema; retains all six raw receipts. Requires observed native base "
    "and actual SCOM identity and complete finite numeric PARAM coverage for a two-line PML draft and an "
    "unsubmitted dry_run=true script request only with sufficient observations. No PML preview or execution "
    "is submitted. Module 81 is identity, not permission. Word parameters, complete clone identity, runtime "
    "grammar/copy semantics, geometry/reference equivalence and units remain unverified; execution_ready "
    "is always false. Does not create a DB/CATE, select CE, deploy, save, claim, change parameters or "
    "automatically retry."
)


class ParagonComponentTemplateCatalogCompatibilityTests(unittest.TestCase):
    def test_saved113_definitions_allow_only_one_new_tool_and_exact_plan_description(self):
        saved = BASELINE.read_bytes()
        self.assertEqual(hashlib.sha256(saved).hexdigest(), BASELINE_SHA256)
        baseline = json.loads(saved.decode("utf-8-sig"))["tools"]
        current = create_server(Settings(mode="mock"))._dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )["result"]["tools"]

        self.assertEqual(len(baseline), 113)
        self.assertEqual(len(current), 117)
        self.assertEqual(len({tool["name"] for tool in baseline}), 113)
        self.assertEqual(len({tool["name"] for tool in current}), 117)
        old_by_name = {tool["name"]: tool for tool in baseline}
        current_by_name = {tool["name"]: tool for tool in current}
        self.assertEqual(set(current_by_name) - set(old_by_name), ADDED_TOOLS)
        self.assertEqual(set(old_by_name) - set(current_by_name), set())
        self.assertEqual(
            {name for name, historical in old_by_name.items() if current_by_name[name] != historical},
            {PLAN},
        )
        for name, historical in old_by_name.items():
            with self.subTest(tool=name):
                candidate = deepcopy(current_by_name[name])
                if name == PLAN:
                    self.assertEqual(candidate["description"], PLAN_DESCRIPTION)
                    self.assertNotEqual(candidate["description"], historical["description"])
                    self.assertEqual(candidate["inputSchema"], historical["inputSchema"])
                    self.assertEqual(candidate["annotations"], historical["annotations"])
                    self.assertEqual(candidate.get("title"), historical.get("title"))
                    candidate["description"] = historical["description"]
                self.assertEqual(candidate, historical)


if __name__ == "__main__":
    unittest.main()
