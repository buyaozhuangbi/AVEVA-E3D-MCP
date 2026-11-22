"""Exercise extracted production cancellation methods without the E3D runtime."""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"
sys.path.insert(0, str(ROOT / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server
from generated_csg_gate_support import generated_csg_gate_members


def extract_method(source, name):
    declaration = re.search(
        rf"^    (?:public|private) (?:static )?[^\n]+\b{re.escape(name)}\(", source, re.MULTILINE)
    if declaration is None:
        raise AssertionError(f"Production method is missing: {name}")
    opening = source.index("{", declaration.end())
    tokens = re.compile(r'@"(?:""|[^"])*"|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/|[{}]', re.S)
    depth = 0
    for token in tokens.finditer(source, opening):
        if token.group() == "{":
            depth += 1
        elif token.group() == "}":
            depth -= 1
            if depth == 0:
                return source[declaration.start():token.end()]
    raise AssertionError(f"Unterminated production method: {name}")


class CancellationCompletionTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "The offline C# checks require dotnet.")
    def test_production_cancel_dispatch_is_never_reported_as_completion(self):
        source = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
        methods = "\n\n".join(extract_method(source, name) for name in
                               ("ExecutePml", "CancelCommand", "IsCancellationCommand"))
        methods += "\n\n" + generated_csg_gate_members(extract_method)
        harness = (ROOT / "tests" / "e3d21" / "CancelCompletionHarness.cs").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="cancel-completion-") as temporary:
            output = Path(temporary)
            (output / "Harness.cs").write_text(harness.replace("// PRODUCTION_METHODS", methods), encoding="utf-8")
            shutil.copyfile(BRIDGE / "NativeDatabaseFaultState.cs", output / "NativeDatabaseFaultState.cs")
            (output / "Offline.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
            built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                   cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")],
                                 cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Cancellation completion checks passed:", ran.stdout)

    def test_unconfirmed_cancel_alias_skips_capture_and_preserves_diagnostics(self):
        server = create_server(Settings(mode="bridge", allow_commands=True))
        for status, queued in (("pending", True), ("failed", False)):
            with self.subTest(status=status):
                cancellation = {"success": False, "requested": True, "completion_confirmed": False,
                                "status": status, "ui_cancel_queued": queued,
                                "previous_state": {"busy": True}, "native_dialog_dismissal": {"attempted": False}}
                with patch.object(BridgeClient, "command", return_value={"ok": True, "result": cancellation}) as command, \
                        patch.object(BridgeClient, "capture_view") as capture:
                    result = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                        "name": "e3d_execute_pml", "arguments": {"command": "CANCEL", "dry_run": False, "capture_after": True},
                    }})["result"]
                command.assert_called_once()
                capture.assert_not_called()
                self.assertTrue(result["isError"])
                self.assertEqual(result["structuredContent"]["result"], cancellation)
                self.assertEqual(result["structuredContent"]["receipt"]["capture"]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
