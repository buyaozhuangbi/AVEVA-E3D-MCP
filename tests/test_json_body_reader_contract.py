"""Regression coverage for bounded, duplicate-aware E3D21 JSON readers."""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class JsonBodyReaderContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline checks require dotnet.")
    def test_production_readers_reject_duplicates_and_oversized_bodies(self):
        result = subprocess.run(
            ["python", str(ROOT / "tests" / "e3d21" / "run-json-body-harness.py")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("JSON body reader contract passed:", result.stdout)


if __name__ == "__main__":
    unittest.main()
