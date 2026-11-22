"""Run the child schema native contract against recording fakes, never E3D."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aveva_e3d_mcp.equipment_child_schema import _parameters, _validate
from test_model_validation_native_contract import extract_method


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge/e3d21"
SDK = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10"))


@pytest.fixture(scope="module")
def native_child_schema_receipts():
    if not shutil.which("dotnet") or not (SDK / "Newtonsoft.Json.dll").is_file():
        pytest.skip("Requires offline dotnet and SDK JSON library")
    harness = (ROOT / "tests/e3d21/EquipmentChildSchemaHarness.cs").read_text(encoding="utf-8")
    context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
    harness = harness.replace("// PRODUCTION_NATIVE_HELPERS", "\n\n".join(extract_method(context, name) for name in ("NativeRead", "ThrowIfNativeFaulted", "OnUi")))
    with tempfile.TemporaryDirectory(prefix="equipment-child-schema-") as temporary:
        target = Path(temporary)
        (target / "Harness.cs").write_text(harness, encoding="utf-8")
        for name in ("EquipmentChildSchema.cs", "E3d21EquipmentChildSchema.cs", "NativeDatabaseFaultState.cs"):
            shutil.copyfile(BRIDGE / name, target / name)
        shutil.copyfile(SDK / "Newtonsoft.Json.dll", target / "Newtonsoft.Json.dll")
        (target / "Offline.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
            '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup>'
            '<ItemGroup><Reference Include="Newtonsoft.Json"><HintPath>Newtonsoft.Json.dll</HintPath>'
            '</Reference></ItemGroup></Project>', encoding="utf-8")
        options = dict(cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace")
        built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"], timeout=90, **options)
        assert built.returncode == 0, built.stdout + built.stderr
        ran = subprocess.run([str(target / "bin/Release/net48/Offline.exe")], timeout=45, **options)
        assert ran.returncode == 0, ran.stderr + "\n" + "\n".join(line for line in ran.stdout.splitlines() if not line.startswith("Child schema receipt: "))
        assert "Equipment child schema production checks passed:" in ran.stdout
        receipts = [json.loads(line.removeprefix("Child schema receipt: ")) for line in ran.stdout.splitlines() if line.startswith("Child schema receipt: ")]
        for line in ran.stdout.splitlines():
            if line.startswith(("Child schema native fault matrix:", "Equipment child schema production checks passed:")):
                print(line)
        return receipts


def test_child_schema_production_scope_unknowns_and_native_fault_stop(native_child_schema_receipts):
    receipts = native_child_schema_receipts
    assert receipts and any(receipt["reply"]["result"]["instance_attributes"]["status"] == "unknown" for receipt in receipts)
    limited = [receipt["reply"]["result"]["type_system_attributes"] for receipt in receipts
               if receipt["reply"]["result"]["type_system_attributes"]["inspection_limited"] is True]
    assert limited and all(observation["native_array_inspection_limit"] == 4096
                           and observation["total_count"] > 4096 and observation["attributes"] == []
                           and observation["complete"] is False and observation["has_more"] is None
                           and observation["native_arrays_materialized_before_output_limits"] is True
                           and observation["errors"].keys() == {"inspection_limit"} for observation in limited)
    for receipt in receipts:
        reply = receipt["reply"]
        result = reply["result"]
        assert str(UUID(reply["bridge_build"])) == reply["bridge_build"]
        for observation in (result, result["context"]["before"], result["context"]["after"]):
            assert observation["session_id"] == reply["session_id"]
            assert observation["bridge_build"] == reply["bridge_build"]


def test_child_schema_native_receipts_satisfy_python_contract(native_child_schema_receipts):
    for index, receipt in enumerate(native_child_schema_receipts, start=1):
        try:
            _validate(receipt["reply"], _parameters(receipt["payload"]))
        except ValueError as error:
            raise AssertionError(f"Native child schema receipt {index}, payload={json.dumps(receipt['payload'], ensure_ascii=True)}: {error}") from error
    print(f"Validated {len(native_child_schema_receipts)} native receipts against the Python evidence contract.")


@pytest.mark.skipif(not shutil.which("dotnet") or not (SDK / "Aveva.Core.Database.dll").is_file(), reason="Requires installed SDK for compile-only signature checks")
def test_child_schema_compiles_against_real_sdk_without_running_it():
    """Compile a library only: do not load/init AVEVA assemblies or touch E3D."""
    import xml.etree.ElementTree as ET

    with tempfile.TemporaryDirectory(prefix="equipment-child-schema-sdk-") as temporary:
        target = Path(temporary)
        for name in ("EquipmentChildSchema.cs", "E3d21EquipmentChildSchema.cs"):
            shutil.copyfile(BRIDGE / name, target / name)
        (target / "CompileOnly.cs").write_text(
            "using System; namespace E3d21AiBridge { public sealed partial class E3d21Context {"
            "private bool IsCommandActive() => false; private static void ThrowIfNativeFaulted() {}"
            "private object CurrentElement() => null;"
            "private static T NativeRead<T>(Func<T> f) => f(); private T OnUi<T>(Func<T> f) => f(); }}", encoding="utf-8")
        project = ET.Element("Project", Sdk="Microsoft.NET.Sdk")
        properties = ET.SubElement(project, "PropertyGroup")
        ET.SubElement(properties, "TargetFramework").text = "net48"
        ET.SubElement(properties, "LangVersion").text = "latest"
        references = ET.SubElement(project, "ItemGroup")
        for name in ("Aveva.Core.Database", "Aveva.Core.Utilities", "Aveva.Core.Geometry"):
            reference = ET.SubElement(references, "Reference", Include=name)
            ET.SubElement(reference, "HintPath").text = str(SDK / (name + ".dll"))
            ET.SubElement(reference, "Private").text = "false"
        ET.ElementTree(project).write(target / "CompileOnly.csproj", encoding="unicode")
        built = subprocess.run(["dotnet", "build", "CompileOnly.csproj", "-c", "Release", "--nologo"], cwd=target,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        assert built.returncode == 0, built.stdout + built.stderr
