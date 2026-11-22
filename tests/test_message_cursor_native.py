"""Compile exact production managed message-window route and snapshot offline."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_model_validation_native_contract import block_end, extract_method


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"


class MessageCursorNativeTests(unittest.TestCase):
    def production_parts(self):
        adapter = (BRIDGE / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
        marker = 'else if (method == "GET" && path == "/api/v1/messages/window")'
        opening = adapter.index("{", adapter.index(marker) + len(marker))
        route = adapter[opening + 1:block_end(adapter, opening) - 1]
        catch = adapter.index("catch (Exception ex)", opening)
        guard = adapter.index("if (managedMessageWindowQuery)", catch)
        guard_opening = adapter.index("{", guard)
        failure = adapter[guard:block_end(adapter, guard_opening)]
        return adapter, route, failure

    def test_routes_advertise_and_bypass_native_diagnostics(self):
        adapter, route, failure = self.production_parts()
        self.assertIn('"message_cursor_v1"', adapter)
        self.assertIn('managedMessageWindowQuery = path == "/api/v1/messages/window";', adapter)
        self.assertNotIn("SafeCommandStateDiagnostics", failure)
        self.assertNotIn("context.", failure)
        self.assertIn("return;", failure)
        wrong_method = adapter.index('"Message windows require GET."')
        legacy_not_found = adapter.index('error = "Not found"', wrong_method)
        self.assertLess(wrong_method, legacy_not_found)
        for forbidden in ("OnUi(", "NativeRead(", "CommandState(", "Connected", "ThrowIfNativeFaulted(",
                          "ReadMessages(", "GetProject(", "SpatialMap"):
            self.assertNotIn(forbidden, route + failure)
        context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        method = extract_method(context, "ReadMessageWindow")
        self.assertIn("lock (messageLock)", method)
        self.assertIn("outputMessageSequence", method)
        self.assertNotIn("ReadMessageWindow", adapter[adapter.index("public interface IE3d21Context"):])

    @unittest.skipUnless(shutil.which("dotnet"), "Offline managed checks require dotnet.")
    def test_production_cursor_route_retention_authentication_and_precision(self):
        _, route, failure = self.production_parts()
        source = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        methods = "\n\n".join(extract_method(source, name) for name in ("ReadMessageWindow", "ReadMessages"))
        harness = (ROOT / "tests" / "e3d21" / "MessageCursorHarness.cs").read_text(encoding="utf-8")
        harness = harness.replace("// PRODUCTION_ROUTE", route).replace("// PRODUCTION_FAILURE", failure)
        harness = harness.replace("// PRODUCTION_CONTEXT_METHODS", methods)
        self.assertNotIn("// PRODUCTION_", harness)
        with tempfile.TemporaryDirectory(prefix="message-cursor-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness, encoding="utf-8")
            shutil.copyfile(BRIDGE / "MessageCursor.cs", output / "MessageCursor.cs")
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                '<TargetFramework>net48</TargetFramework><OutputType>Exe</OutputType>'
                '<LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Message cursor production checks passed:", ran.stdout)


if __name__ == "__main__":
    unittest.main()
