"""Run the real Catalogue reader and fault guards against recording SDK fakes.

No live E3D endpoint, PML interpreter or AVEVA database assembly is used. The
only SDK-distributed dependency is the managed Newtonsoft JSON serializer.
The fixture reuses database-neutral recording fakes and the pure bounded
hierarchy helper; named lookup only exposes the typed Catalogue overload.
"""
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

from aveva_e3d_mcp.paragon_catalogue import _parameters, _validate
from test_model_validation_native_contract import extract_method


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "e3d21"
SDK_JSON = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10")) / "Newtonsoft.Json.dll"


@pytest.mark.skipif(not shutil.which("dotnet") or not SDK_JSON.is_file(),
                    reason="Offline native harness requires dotnet and Newtonsoft.Json.dll")
def test_catalogue_reader_native_types_budgets_partial_context_and_fault_barriers():
    fakes = (ROOT / "tests/e3d21/EquipmentManifestHarness.cs").read_text(encoding="utf-8").split(
        "internal sealed class ManifestHttpRequest", 1)[0]
    fakes = fakes.replace("public enum DbType { Design, Catalogue }",
                          "public enum DbType { Design = 1, Catalog = 2, Catalogue = 2, Draft = 7 }")
    original = '''public DbElement FindElement(string name)
        {
            DbElement value;
            return Scenario.Read("mdb.find." + name, Scenario.Names.TryGetValue(name, out value) ? value : null);
        }'''
    replacement = '''public DbElement FindElement(DbType type, string name)
        {
            if (type != DbType.Catalogue) Scenario.Forbidden("non-Catalogue lookup");
            DbElement value;
            return Scenario.Read("mdb.catalogue.find." + name, Scenario.Names.TryGetValue(name, out value) ? value : null);
        }'''
    assert original in fakes
    fakes = fakes.replace(original, replacement)
    context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
    fakes = fakes.replace("// PRODUCTION_NATIVE_HELPERS", "\n\n".join(
        extract_method(context, name) for name in ("NativeRead", "ThrowIfNativeFaulted", "OnUi")))
    main = (ROOT / "tests/e3d21/ParagonCatalogueManifestHarness.cs").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="paragon-catalogue-native-") as temporary:
        target = Path(temporary)
        (target / "Harness.cs").write_text(fakes + "\n" + main, encoding="utf-8")
        for name in ("ParagonCatalogueManifest.cs", "E3d21ParagonCatalogueManifest.cs", "NativeDatabaseFaultState.cs"):
            shutil.copyfile(BRIDGE / name, target / name)
        hierarchy = (BRIDGE / "EquipmentManifest.cs").read_text(encoding="utf-8-sig")
        declaration = "internal sealed class EquipmentManifestTraversal<T>"
        (target / "Hierarchy.cs").write_text(
            "using System;\nusing System.Collections.Generic;\nnamespace E3d21AiBridge;\n"
            + declaration + hierarchy.split(declaration, 1)[1], encoding="utf-8")
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
        assert "Paragon catalogue manifest production checks passed." in ran.stdout
        assert "Paragon native fault matrix: " in ran.stdout
        receipts = [json.loads(line.removeprefix("Paragon native receipt: "))
                    for line in ran.stdout.splitlines() if line.startswith("Paragon native receipt: ")]
        assert receipts and any(item["reply"]["result"]["complete"] is False for item in receipts)
        assert any(item["reply"]["result"]["context"]["boundary_agreement"] is None for item in receipts)
        for receipt in receipts:
            _validate(receipt["reply"], _parameters(receipt["payload"]))
        matrix = next(line for line in ran.stdout.splitlines() if line.startswith("Paragon native fault matrix: "))
        print(matrix)
        print(f"Validated {len(receipts)} production native receipts against the Python evidence contract.")
