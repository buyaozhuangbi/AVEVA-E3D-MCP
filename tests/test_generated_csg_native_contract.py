"""Run the production generated-CSG reader harness and guard its fault gates."""

from pathlib import Path
import re
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"
HARNESS = ROOT / "tests" / "e3d21" / "GeneratedCsgHarness.csproj"


class GeneratedCsgNativeContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline C# checks require dotnet.")
    def test_production_reader_harness(self):
        self.assertTrue(HARNESS.is_file(), "The production CSG harness project must remain present.")
        run = subprocess.run(
            ["dotnet", "run", "--project", str(HARNESS), "--configuration", "Release", "--nologo"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        match = re.search(r"Generated CSG production harness checks passed:\s*(\d+)", run.stdout)
        self.assertIsNotNone(match, run.stdout + run.stderr)
        self.assertGreaterEqual(int(match.group(1)), 503, run.stdout)

    def test_context_gate_recovery_and_managed_health_are_static_contracts(self):
        context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        csg = (BRIDGE / "E3d21GeneratedCsg.cs").read_text(encoding="utf-8-sig")

        native_gate_start = context.index("private static void ThrowIfNativeFaulted()")
        native_gate_end = context.index("private static T NativeRead", native_gate_start)
        native_gate = context[native_gate_start:native_gate_end]
        self.assertIn("ThrowIfGeneratedCsgQuarantined();", native_gate)
        self.assertIn("state.HasFault && state.Generation > permittedNativeFaultGeneration", native_gate)

        on_ui_start = context.index("private T OnUi<T>(Func<T> operation, bool allowNativeFault = false)")
        on_ui = context[on_ui_start:]
        self.assertIn("if (!allowNativeFault) ThrowIfGeneratedCsgQuarantined();", on_ui)
        self.assertIn("generatedCsgRecoveryPermitted = allowNativeFault;", on_ui)
        self.assertIn("generatedCsgRecoveryPermitted = previousCsgRecovery;", on_ui)
        self.assertIn("permittedNativeFaultGeneration = allowNativeFault && nativeFaultState != null ? nativeFaultState.Generation : 0;", on_ui)

        command_start = context.index("public object CommandState()")
        command_end = context.index("public object CancelCommand()", command_start)
        command_state = context[command_start:command_end]
        self.assertIn("[\"generated_csg_quarantine\"] = GetGeneratedCsgQuarantineState()", command_state)
        self.assertIn("[\"database_ready\"] = !faulted && !csgQuarantined", command_state)
        self.assertIn("[\"recovery_required\"] = faulted || csgQuarantined", command_state)

        self.assertIn("private static bool IsGeneratedCsgQuarantined()", csg)
        self.assertIn("public object GetGeneratedCsgQuarantineState()", csg)
        self.assertIn("public object GetGeneratedCsg(string refno, int maxTrees, int maxItems)", csg)
        self.assertIn("[\"comparison_policy\"] = \"enabled_geometry_options_v1\"", csg)
        self.assertIn("[\"raw_mismatched_fields\"]", csg)
        self.assertIn("[\"effective_mismatched_fields\"]", csg)
        self.assertIn("[\"inactive_fields\"]", csg)


if __name__ == "__main__":
    unittest.main()
