"""Execute direct Catalogue SCOM creation on managed fakes; real SDK is compile-only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

import pytest

from test_model_validation_native_contract import extract_method

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge/e3d21"
SDK = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10"))
SOURCES = (
    "ParagonComponentCreate.cs", "E3d21ParagonComponentCreate.cs",
    "ParagonComponentCopy.cs", "ParagonComponentCopyJournal.cs", "E3d21ParagonComponentCopy.cs",
    "ParagonComponentParameters.cs", "ParagonComponentTemplate.cs", "E3d21ParagonComponentTemplate.cs",
    "PmlScriptInput.cs", "NativeDatabaseFaultState.cs",
)
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(scope="module")
def component_create_receipts():
    if not shutil.which("dotnet") or not (SDK / "Newtonsoft.Json.dll").is_file():
        pytest.skip("Requires offline dotnet and JSON library")
    helpers = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
    harness = (ROOT / "tests/e3d21/ParagonComponentCreateHarness.cs").read_text(encoding="utf-8-sig")
    harness = harness.replace("// PRODUCTION_NATIVE_HELPERS", "\n\n".join(extract_method(helpers, name) for name in (
        "NativeRead", "NativeWrite", "ThrowIfNativeFaulted", "OnUi", "TryBeginCommand", "EndCommand",
        "IsCommandActive", "BridgeOperationBusy",
    )))
    with tempfile.TemporaryDirectory(prefix="paragon-component-create-") as temporary:
        target = Path(temporary)
        (target / "Harness.cs").write_text(harness, encoding="utf-8")
        for name in SOURCES:
            shutil.copyfile(BRIDGE / name, target / name)
        shutil.copyfile(SDK / "Newtonsoft.Json.dll", target / "Newtonsoft.Json.dll")
        (target / "Offline.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
            '<LangVersion>latest</LangVersion><OutputType>Exe</OutputType></PropertyGroup>'
            '<ItemGroup><Reference Include="Newtonsoft.Json"><HintPath>Newtonsoft.Json.dll</HintPath>'
            '</Reference></ItemGroup></Project>', encoding="utf-8",
        )
        options = dict(cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace")
        built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"], timeout=90, **options)
        assert built.returncode == 0, built.stdout + built.stderr
        run = subprocess.run([str(target / "bin/Release/net48/Offline.exe")], timeout=150, **options)
        output = "\n".join(line for line in run.stdout.splitlines() if not line.startswith("Component create receipt: "))
        assert run.returncode == 0, output + run.stderr
        assert "Component create production checks passed:" in output
        matrix = re.search(r"Component create native fault matrix: (\d+) SDK read/write positions / (\d+) return-or-throw cases", output)
        assert matrix and int(matrix[1]) > 500 and int(matrix[2]) == int(matrix[1]) * 2
        print(output)
        return [json.loads(line.removeprefix("Component create receipt: ")) for line in run.stdout.splitlines()
                if line.startswith("Component create receipt: ")]


def test_direct_component_create_native_receipts_and_faults(component_create_receipts):
    assert len(component_create_receipts) >= 60
    results = [row["reply"]["result"] for row in component_create_receipts]
    assert {row["status"] for row in results} >= {"prepared", "blocked", "created", "failed", "rejected", "duplicate"}
    for result in results:
        assert result["operation"] == "paragon_component_create"
        assert len(result["progress"]) == 21
        assert result["scope"] == {
            "kind": "catalogue_scom_create", "backend": "native_sdk", "set_membership_only": True,
            "shared_sets": True, "geometry_evaluated": False, "engineering_validated": None,
            "savework_performed": False, "atomic_snapshot": False,
        }
        assert result["request_replayed"] is False and result["progress"]["retry_safe"] is False
        assert result["this_submission_executed"] == result["progress"]["creation_attempted"]
        if result["status"] == "created":
            assert result["progress"]["known_new_refno"] == "=2/3"
            assert result["progress"]["known_new_refno_verified"] is True
            assert result["progress"]["readback_complete"] is True


def test_native_receipts_satisfy_component_create_python_contract(component_create_receipts):
    from aveva_e3d_mcp import paragon_component_create as consumer

    validated = 0
    for index, row in enumerate(component_create_receipts, 1):
        if row["reply"]["ok"] is not True:
            continue
        original = json.dumps(row, sort_keys=True)
        native = {key: row["reply"][key] for key in ("ok", "version", "session_id", "bridge_build", "result")}
        try:
            consumer._validate(native, row["payload"])
        except Exception as error:
            pytest.fail(f'Production component-create receipt {index} ({row["reply"]["result"]["status"]}): {error}')
        assert json.dumps(row, sort_keys=True) == original
        validated += 1
    assert validated >= 20
    print(f"Validated {validated} unchanged successful HTTP component-create native receipts through Python.")
    statuses = 0
    for index, row in enumerate(component_create_receipts, 1):
        result = row["reply"]["result"]
        if result["journal"] is None:
            continue
        envelope = {key: row["reply"][key] for key in ("version", "session_id", "bridge_build")}
        envelope.update(ok=True, result=result["journal"])
        try:
            consumer._validate_status(envelope, {
                "request_id": result["request_id"], "expected_session_id": row["reply"]["session_id"],
            })
        except Exception as error:
            pytest.fail(f"Production component-create journal {index}: {error}")
        statuses += 1
    assert statuses >= 30
    print(f"Validated {statuses} managed native creation journals, including failed/rejected/duplicate evidence.")


@pytest.mark.skipif(not shutil.which("dotnet") or not (SDK / "Aveva.Core.Database.dll").is_file(),
                    reason="Requires real SDK for offline compile-only check")
def test_component_create_compiles_against_installed_sdk_without_execution(tmp_path):
    for name in SOURCES:
        shutil.copyfile(BRIDGE / name, tmp_path / name)
    (tmp_path / "CompileOnly.cs").write_text('''using System; namespace E3d21AiBridge;
    public sealed partial class E3d21Context {
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        private bool IsCommandActive() => throw new NotImplementedException();
        private bool TryBeginCommand(string name) => throw new NotImplementedException();
        private void EndCommand() => throw new NotImplementedException();
        private static T NativeRead<T>(Func<T> f) => throw new NotImplementedException();
        private static void NativeWrite(Action f) => throw new NotImplementedException();
        private static void ThrowIfNativeFaulted() => throw new NotImplementedException();
        private T OnUi<T>(Func<T> f, bool allowNativeFault = false) => throw new NotImplementedException();
        private object CurrentElement() => throw new NotImplementedException();
    }''', encoding="utf-8")
    project = ET.Element("Project", Sdk="Microsoft.NET.Sdk")
    properties = ET.SubElement(project, "PropertyGroup")
    ET.SubElement(properties, "TargetFramework").text = "net48"
    ET.SubElement(properties, "LangVersion").text = "latest"
    refs = ET.SubElement(project, "ItemGroup")
    for name in ("Aveva.Core.Database", "Aveva.Core.Utilities", "Aveva.Core.Geometry", "Newtonsoft.Json"):
        reference = ET.SubElement(refs, "Reference", Include=name)
        ET.SubElement(reference, "HintPath").text = str(SDK / (name + ".dll"))
        ET.SubElement(reference, "Private").text = "false"
    ET.ElementTree(project).write(tmp_path / "CompileOnly.csproj", encoding="unicode")
    built = subprocess.run(["dotnet", "build", "CompileOnly.csproj", "-c", "Release", "--nologo"],
                           cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    assert built.returncode == 0, built.stdout + built.stderr


@pytest.mark.skipif(not (SDK / "PMLLIB/paragon/forms/catmodifyattribs.pmlfrm").is_file(),
                    reason="Installed PARAGON source is unavailable")
def test_installed_paragon_point_and_geometry_reference_type_basis():
    form = (SDK / "PMLLIB/paragon/forms/catmodifyattribs.pmlfrm").read_text(encoding="utf-8", errors="replace")
    for evidence in (
        r"!this\.refAttributes\[9\]\s*=\s*\|GMREF\|", r"!this\.refTypes\[9\]\s*=\s*\|GMSE\|",
        r"!this\.refAttributes\[10\]\s*=\s*\|PTREF\|", r"!this\.refTypes\[10\]\s*=\s*\|PTSE\|",
    ):
        assert re.search(evidence, form), evidence
    xml = (SDK / "Aveva.Core.Database.xml").read_text(encoding="utf-8", errors="replace")
    for signature in (
        "DbElement.CreateLast(Aveva.Core.Database.DbElementType)",
        "DbElement.SetAttribute(Aveva.Core.Database.DbAttribute,Aveva.Core.Database.DbElement)",
        "DbElement.SetAttribute(Aveva.Core.Database.DbAttribute,System.Double[])",
    ):
        assert signature in xml
