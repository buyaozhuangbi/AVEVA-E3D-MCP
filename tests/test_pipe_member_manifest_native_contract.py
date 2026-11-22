"""Run the production native manifest and HTTP branch against recording SDK fakes."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from test_model_validation_native_contract import block_end, extract_method
from test_pipe_member_manifest import DEFAULT_ATTRIBUTES
from aveva_e3d_mcp.pipe_member_manifest import _validate


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"
FIXTURES = ROOT / "tests" / "e3d21"
SDK_JSON = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10")) / "Newtonsoft.Json.dll"


def _branch(source: str, marker: str) -> str:
    opening = source.index("{", source.index(marker) + len(marker))
    return source[opening + 1:block_end(source, opening) - 1]


@pytest.mark.skipif(not shutil.which("dotnet") or not SDK_JSON.is_file(),
                    reason="Offline native contract requires dotnet and the E3D21 SDK Newtonsoft.Json.dll")
def test_production_read_route_and_native_receipt():
    adapter = (BRIDGE / "E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
    reader = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
    model = (BRIDGE / "E3d21ModelEditing.cs").read_text(encoding="utf-8-sig")
    fakes = (FIXTURES / "EquipmentManifestHarness.cs").read_text(encoding="utf-8").split(
        "internal static class EquipmentManifestHarness", 1)[0]
    fakes = fakes.replace("// PRODUCTION_ROUTE", _branch(adapter,
        'else if (method == "POST" && path == "/api/v1/piping/member-manifest")'))
    fakes = fakes.replace("// PRODUCTION_HTTP_ERROR", _branch(adapter, "catch (Exception ex) when (pipeManifestQuery)"))
    fakes = fakes.replace(
        "bool managedScriptStatusQuery = false, equipmentManifestQuery = true, projectCatalogProbe = false;", "")
    fakes = fakes.replace("// PRODUCTION_HTTP_HELPERS", "\n\n".join(
        extract_method(adapter, name).replace("HttpListenerRequest request", "ManifestHttpRequest request", 1)
        for name in ("ReadUniqueBodyAsync", "ReadString", "ReadInteger")
    ))
    fakes = fakes.replace("// PRODUCTION_NATIVE_HELPERS", "\n\n".join(
        [extract_method(reader, name) for name in ("NativeRead", "ThrowIfNativeFaulted", "OnUi")]
        + [extract_method(model, name) for name in ("ModelReference", "ModelCurrentReference")]
    ))
    test_main = (FIXTURES / "PipeMemberManifestHarness.cs").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="pipe-member-manifest-") as temporary:
        target = Path(temporary)
        (target / "Harness.cs").write_text(fakes + "\n" + test_main, encoding="utf-8")
        sources = ("PipeMemberManifest.cs", "E3d21PipeMemberManifest.cs", "EquipmentManifest.cs",
                   "E3d21EquipmentManifest.cs", "ModelEditInput.cs", "AttributeEditPolicy.cs", "NativeDatabaseFaultState.cs")
        for name in sources:
            shutil.copyfile(BRIDGE / name, target / name)
        shutil.copyfile(SDK_JSON, target / SDK_JSON.name)
        (target / "Offline.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
            '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup>'
            '<ItemGroup><Reference Include="Newtonsoft.Json"><HintPath>' + escape(SDK_JSON.name)
            + '</HintPath></Reference></ItemGroup></Project>', encoding="utf-8")
        options = dict(cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace")
        result = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"],
                                timeout=90, **options)
        assert result.returncode == 0, result.stdout + result.stderr
        result = subprocess.run([str(target / "bin/Release/net48/Offline.exe")], timeout=30, **options)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Pipe member manifest production checks passed." in result.stdout
        line = next(line for line in result.stdout.splitlines() if line.startswith("Pipe manifest receipt: "))
        _validate(json.loads(line.removeprefix("Pipe manifest receipt: ")), {
            "refno": "=1/1", "max_depth": 8, "limit": 64, "attributes": list(DEFAULT_ATTRIBUTES)})
