"""Regression coverage for the legacy adapter's shared bounded JSON reader."""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LegacyJsonRequestBodyContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline checks require dotnet.")
    def test_bounded_reader_and_duplicate_key_validation(self):
        result = subprocess.run(
            ["dotnet", "run", "--project", str(ROOT / "tests" / "e3d21" / "LegacyJsonRequestBodyHarness.csproj"), "-c", "Release"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Legacy JSON request-body checks:", result.stdout)


if __name__ == "__main__":
    unittest.main()
