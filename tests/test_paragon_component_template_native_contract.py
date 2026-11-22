"""Compile/run only managed recording fakes; real AVEVA is compile-only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from uuid import UUID
import xml.etree.ElementTree as ET

import pytest

from test_model_validation_native_contract import extract_method


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
BRIDGE = ROOT / "bridge/e3d21"
SDK = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10"))
SOURCES = ("ParagonComponentTemplate.cs", "E3d21ParagonComponentTemplate.cs")


@pytest.fixture(scope="module")
def component_template_receipts():
    if not shutil.which("dotnet") or not (SDK / "Newtonsoft.Json.dll").is_file():
        pytest.skip("Requires offline dotnet and JSON library")
    context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
    source = (ROOT / "tests/e3d21/ParagonComponentTemplateHarness.cs").read_text(encoding="utf-8")
    source = source.replace("// PRODUCTION_NATIVE_HELPERS", "\n\n".join(
        extract_method(context, name) for name in (
            "NativeRead", "ThrowIfNativeFaulted", "OnUi", "TryBeginCommand", "EndCommand",
            "IsCommandActive", "BridgeOperationBusy",
        )))
    with tempfile.TemporaryDirectory(prefix="paragon-component-template-") as temporary:
        target = Path(temporary)
        (target / "Harness.cs").write_text(source, encoding="utf-8")
        for name in SOURCES + ("NativeDatabaseFaultState.cs",):
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
        ran = subprocess.run([str(target / "bin/Release/net48/Offline.exe")], timeout=60, **options)
        output = "\n".join(line for line in ran.stdout.splitlines() if not line.startswith("Component template receipt: "))
        assert ran.returncode == 0, output + ran.stderr
        assert "Component template production checks passed:" in output
        assert "Component template native fault matrix: 741 read positions / 1482 return-or-throw cases" in output
        assert "Component template owned core native fault matrix: 741 read positions / 1482 return-or-throw cases" in output
        assert "Component template owned core checks passed:" in output
        print(output)
        return [json.loads(line.removeprefix("Component template receipt: ")) for line in ran.stdout.splitlines()
                if line.startswith("Component template receipt: ")]


def test_native_template_scope_array_limits_unknowns_and_context(component_template_receipts):
    receipts = component_template_receipts
    assert len(receipts) >= 38
    results = [receipt["reply"]["result"] for receipt in receipts]
    assert any(result["complete"] for result in results)
    assert any(result["parameters"]["inspection_limited"] is True for result in results)
    assert any(result["references"]["BLRFA"]["shape"] == "element_array" for result in results)
    assert any(result["target"] and result["target"]["actual_type"]
               and result["target"]["actual_type"]["short_name"] == ":CUSTOM_SCOM" for result in results)
    for receipt in receipts:
        reply, payload = receipt["reply"], receipt["payload"]
        result = reply["result"]
        assert str(UUID(reply["bridge_build"])) == reply["bridge_build"]
        assert result["requested_refno"] == payload["refno"] == "=2/1"
        assert result["operation"] == "paragon_component_template"
        assert result["available"] is True
        assert result["success"] is result["complete"]
        for observation in (result, result["context"]["before"], result["context"]["after"]):
            assert observation["session_id"] == reply["session_id"]
            assert observation["bridge_build"] == reply["bridge_build"]
        assert result["scope"]["whole_clone_fingerprint"] is False
        assert result["scope"]["word_parameters_observed"] is False
        assert result["scope"]["write_permission_verified"] is None
        assert result["output_limits"]["native_array_allocation_bounded"] is False
        for value in [result["parameters"], *result["references"].values()]:
            assert value["returned_count"] == len(value["values"])
            if value["inspection_limited"] is True:
                assert value["total_count"] > 4096
                assert value["values"] == [] and value["complete"] is False
                assert value["errors"]["inspection_limit"]
            assert len(value["values"]) <= (128 if value["requested_name"] == "PARAM" else 16)
        if result["complete"]:
            assert result["context"]["boundary_agreement"] is True
            assert result["target"]["complete"] is True


def test_every_public_native_template_receipt_still_satisfies_python_contract(component_template_receipts):
    from aveva_e3d_mcp.paragon_component_template import _parameters, _validate

    for index, receipt in enumerate(component_template_receipts, 1):
        before = json.dumps(receipt, sort_keys=True)
        try:
            _validate(receipt["reply"], _parameters(receipt["payload"]))
        except ValueError as error:
            pytest.fail(f"Production public component template receipt {index}: {error}")
        assert json.dumps(receipt, sort_keys=True) == before
    print(f"Validated all {len(component_template_receipts)} unchanged public template receipts through Python.")


def test_template_core_retains_only_existing_native_scope_and_no_command_or_dispatch_shell():
    source = (BRIDGE / "E3d21ParagonComponentTemplate.cs").read_text(encoding="utf-8-sig")
    public = extract_method(source, "ReadParagonComponentTemplate")
    core = extract_method(source, "ReadParagonComponentTemplateCore")
    assert public.count("OnUi(") == 1
    assert public.count("IsCommandActive()") == 2
    assert "ReadParagonComponentTemplateCore(request, sessionId, build, out _)" in public
    for forbidden in ("OnUi(", "IsCommandActive(", "TryBeginCommand(", "EndCommand("):
        assert forbidden not in core
    assert "ReferenceEquals(currentNativeFaultState, nativeFaultState)" in core
    assert "ThrowIfNativeFaulted();" in core
    assert core.index("capture = new ParagonComponentTemplateCapture") > core.index('["verified"] = true')
    assert 'private sealed class ParagonComponentTemplateCapture' in source


@pytest.mark.skipif(not shutil.which("dotnet") or not (SDK / "Aveva.Core.Database.dll").is_file(),
                    reason="Requires installed SDK for compile-only signature verification")
def test_template_reader_compiles_against_real_sdk_without_executing_sdk():
    """No AVEVA assembly is loaded or method executed by the test process."""
    with tempfile.TemporaryDirectory(prefix="paragon-component-template-sdk-") as temporary:
        target = Path(temporary)
        for name in SOURCES:
            shutil.copyfile(BRIDGE / name, target / name)
        (target / "CompileOnly.cs").write_text(
            "using System; namespace E3d21AiBridge { internal sealed class NativeDatabaseFaultException : Exception {} "
            "public sealed partial class E3d21Context { private bool IsCommandActive() => false;"
            "private readonly object nativeFaultState = new object(); [ThreadStatic] private static object currentNativeFaultState;"
            "private static void ThrowIfNativeFaulted() {} private object CurrentElement() => null;"
            "private static T NativeRead<T>(Func<T> f) => f(); private T OnUi<T>(Func<T> f) { currentNativeFaultState = nativeFaultState; return f(); } }}", encoding="utf-8")
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
