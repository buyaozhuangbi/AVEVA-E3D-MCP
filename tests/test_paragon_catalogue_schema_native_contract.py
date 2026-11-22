"""Compile the production Catalogue schema reader with managed recording fakes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.paragon_schema import _parameters, _validate
from test_model_validation_native_contract import block_end, extract_method


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge/e3d21"
SDK_JSON = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10")) / "Newtonsoft.Json.dll"


@pytest.mark.skipif(not shutil.which("dotnet") or not SDK_JSON.is_file(),
                    reason="Offline native harness requires dotnet and Newtonsoft.Json.dll")
def test_production_catalogue_schema_budgets_scope_partial_unknown_and_native_faults():
    source = (ROOT / "tests/e3d21/ParagonCatalogueSchemaHarness.cs").read_text(encoding="utf-8")
    context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
    manifest = (BRIDGE / "E3d21ParagonCatalogueManifest.cs").read_text(encoding="utf-8-sig")
    identity_start = manifest.index("    private sealed class ParagonManifestIdentityValue")
    identity_open = manifest.index("{", identity_start)
    identity_class = manifest[identity_start:block_end(manifest, identity_open)]
    helpers = [extract_method(context, name) for name in ("NativeRead", "ThrowIfNativeFaulted", "OnUi")]
    helpers += [extract_method(manifest, name) for name in (
        "ParagonManifestIdentity", "ParagonManifestName", "ParagonManifestText", "ParagonManifestContextText",
        "ParagonManifestBoundary", "ParagonManifestAgreement")]
    source = source.replace("// PRODUCTION_HELPERS", identity_class + "\n\n" + "\n\n".join(helpers))
    with tempfile.TemporaryDirectory(prefix="paragon-catalogue-schema-native-") as temporary:
        target = Path(temporary)
        (target / "Harness.cs").write_text(source, encoding="utf-8")
        for name in ("ParagonCatalogueSchema.cs", "E3d21ParagonCatalogueSchema.cs", "ParagonCatalogueManifest.cs", "NativeDatabaseFaultState.cs"):
            shutil.copyfile(BRIDGE / name, target / name)
        shutil.copyfile(SDK_JSON, target / SDK_JSON.name)
        (target / "Offline.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
            '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup>'
            '<ItemGroup><Reference Include="Newtonsoft.Json"><HintPath>Newtonsoft.Json.dll</HintPath>'
            '</Reference></ItemGroup></Project>', encoding="utf-8")
        options = dict(cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace")
        built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"], timeout=90, **options)
        assert built.returncode == 0, built.stdout + built.stderr
        ran = subprocess.run([str(target / "bin/Release/net48/Offline.exe")], timeout=45, **options)
        assert ran.returncode == 0, ran.stdout + ran.stderr
        assert "Paragon catalogue schema production checks passed." in ran.stdout
        receipts = [json.loads(line.removeprefix("Paragon schema receipt: ")) for line in ran.stdout.splitlines()
                    if line.startswith("Paragon schema receipt: ")]
        assert receipts and any(receipt["reply"]["result"]["complete"] is False for receipt in receipts)
        for receipt in receipts:
            _validate(receipt["reply"], _parameters(receipt["payload"]))
        print(next(line for line in ran.stdout.splitlines() if line.startswith("Paragon schema native fault matrix: ")))
        print(f"Validated {len(receipts)} production schema receipts against the Python evidence contract.")
