"""Run production reference trace with recording fakes; AVEVA is metadata/compile-only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from uuid import UUID
import xml.etree.ElementTree as ET

import pytest
from test_model_validation_native_contract import extract_method

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge/e3d21"
SOURCES = ("ParagonReferenceTrace.cs", "E3d21ParagonReferenceTrace.cs")
DOTNET = shutil.which("dotnet")
SDK = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10"))
sys.path.insert(0, str(ROOT / "src"))


def checked(command, workdir, *, timeout=90):
    result = subprocess.run(command, cwd=workdir, timeout=timeout, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def reference_trace_receipts():
    if not DOTNET or os.name != "nt" or not (SDK / "Newtonsoft.Json.dll").is_file():
        pytest.skip("Requires offline Windows .NET Framework build tools and JSON library")
    helpers = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
    source = (ROOT / "tests/e3d21/ParagonReferenceTraceHarness.cs").read_text(encoding="utf-8")
    source = source.replace("// PRODUCTION_NATIVE_HELPERS", "\n\n".join(
        extract_method(helpers, name) for name in (
            "NativeRead", "ThrowIfNativeFaulted", "OnUi", "IsCommandActive", "BridgeOperationBusy")))
    with tempfile.TemporaryDirectory(prefix="paragon-reference-trace-") as temporary:
        target = Path(temporary)
        (target / "Harness.cs").write_text(source, encoding="utf-8")
        for name in SOURCES + ("NativeDatabaseFaultState.cs",):
            shutil.copyfile(BRIDGE / name, target / name)
        shutil.copyfile(SDK / "Newtonsoft.Json.dll", target / "Newtonsoft.Json.dll")
        (target / "Offline.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
            '<LangVersion>latest</LangVersion><OutputType>Exe</OutputType></PropertyGroup>'
            '<ItemGroup><Reference Include="Newtonsoft.Json"><HintPath>Newtonsoft.Json.dll</HintPath>'
            '</Reference></ItemGroup></Project>', encoding="utf-8")
        checked([DOTNET, "build", "Offline.csproj", "-c", "Release", "--nologo"], target)
        run = subprocess.run([str(target / "bin/Release/net48/Offline.exe")], cwd=target, timeout=60,
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        output = "\n".join(line for line in run.stdout.splitlines() if not line.startswith("Reference trace receipt: "))
        assert run.returncode == 0, output + run.stderr
        assert "Reference trace production checks passed:" in output
        matrix = re.search(r"Reference trace native fault matrix: (\d+) read positions / (\d+) return-or-throw cases", output)
        assert matrix and int(matrix[1]) >= 250 and int(matrix[2]) == int(matrix[1]) * 2, output
        print(output)
        return [json.loads(line.removeprefix("Reference trace receipt: ")) for line in run.stdout.splitlines()
                if line.startswith("Reference trace receipt: ")]


def test_paragon_reference_trace_against_recording_sdk(reference_trace_receipts):
    results = [receipt["reply"]["result"] for receipt in reference_trace_receipts]
    assert len(results) >= 100
    assert {result["status"] for result in results} == {"observed", "partial", "unavailable", "busy", "context_unverified"}
    assert any(result["trace_limit"]["limit"] == 32 and result["trace_limit"]["reached"] for result in results)
    assert any(result["status"] == "context_unverified" and result["trace_limit"]["used"] > 0 for result in results)
    for receipt in reference_trace_receipts:
        reply, payload = receipt["reply"], receipt["payload"]
        assert str(UUID(reply["bridge_build"])) == reply["bridge_build"]
        result = reply["result"]
        assert set(result) == {"status", "trace_limit", "trace", "context", "db_type_codes"}
        assert result["trace_limit"]["limit"] == payload["trace_limit"]
        assert set(result["db_type_codes"]) <= set(result["trace"])
        for sample in (result["context"]["before"], result["context"]["after"]):
            if sample is not None:
                assert sample["session_id"] == reply["session_id"]
                assert sample["bridge_build"] == reply["bridge_build"]
                ce = sample["current_element"]
                assert ce is None or set(ce) == {"refno", "type", "db_type_code"}
        if result["status"] in {"busy", "context_unverified"}:
            assert result["trace"] == result["db_type_codes"] == {}


def test_native_receipts_satisfy_python_contract(reference_trace_receipts):
    from aveva_e3d_mcp.paragon_reference_trace import validate_paragon_reference_trace_receipt
    for index, receipt in enumerate(reference_trace_receipts, 1):
        before = json.dumps(receipt, sort_keys=True)
        try:
            validated = validate_paragon_reference_trace_receipt(receipt["reply"], receipt["payload"])
        except Exception as error:
            pytest.fail(f"Production reference trace receipt {index}: {error}")
        assert validated is receipt["reply"]
        assert json.dumps(receipt, sort_keys=True) == before
    print(f"Validated all {len(reference_trace_receipts)} unchanged native reference trace receipts through Python.")


@pytest.mark.skipif(not DOTNET or os.name != "nt" or not (SDK / "Aveva.Core.Database.dll").is_file(),
                    reason="Requires E3D21 SDK for compile-only verification")
def test_trace_compiles_against_installed_e3d21_sdk(tmp_path):
    # No SDK assembly is loaded and no SDK entry point is executed.
    for name in SOURCES:
        shutil.copyfile(BRIDGE / name, tmp_path / name)
    (tmp_path / "ContextSignatures.cs").write_text(
        "using System; namespace E3d21AiBridge; internal sealed class NativeDatabaseFaultException : Exception {} "
        "internal sealed class NativeDatabaseFaultState { internal bool HasFault => false; } "
        "public sealed partial class E3d21Context {\n"
        "private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();\n"
        "private bool IsCommandActive() => throw new NotImplementedException();\n"
        "private T OnUi<T>(Func<T> op, bool allowNativeFault = false) => throw new NotImplementedException();\n"
        "private static T NativeRead<T>(Func<T> op) => throw new NotImplementedException();\n"
        "private static void ThrowIfNativeFaulted() => throw new NotImplementedException();\n"
        "private static Aveva.Core.Database.DbElement CurrentElement() => throw new NotImplementedException();\n"
        "}", encoding="utf-8")
    project = ET.Element("Project", Sdk="Microsoft.NET.Sdk")
    properties = ET.SubElement(project, "PropertyGroup")
    ET.SubElement(properties, "TargetFramework").text = "net48"
    ET.SubElement(properties, "LangVersion").text = "latest"
    references = ET.SubElement(project, "ItemGroup")
    for name in ("Aveva.Core.Database", "Aveva.Core.Utilities"):
        reference = ET.SubElement(references, "Reference", Include=name)
        ET.SubElement(reference, "HintPath").text = str(SDK / (name + ".dll"))
        ET.SubElement(reference, "Private").text = "false"
    ET.ElementTree(project).write(tmp_path / "SdkCompile.csproj", encoding="unicode")
    checked([DOTNET, "build", "SdkCompile.csproj", "-c", "Release", "--nologo"], tmp_path)


@pytest.mark.skipif(not DOTNET or os.name != "nt" or not (SDK / "Aveva.Core.Database.dll").is_file(),
                    reason="Requires offline SDK metadata")
def test_sdk_enum_and_base_type_contract_are_read_from_metadata_only(tmp_path):
    (tmp_path / "Inspect.cs").write_text(
        'using System; using System.Reflection; class Inspect { static void Main(string[] args) {'
        'var assembly = Assembly.ReflectionOnlyLoadFrom(args[0]);'
        'var type = assembly.GetType("Aveva.Core.Database.DbType", true);'
        'Console.WriteLine("Underlying=" + type.GetEnumUnderlyingType().FullName);'
        'foreach (var name in new[] { "Design", "Catalog", "Catalogue", "Property" })'
        'Console.WriteLine(name + "=" + type.GetField(name).GetRawConstantValue()); }}', encoding="utf-8")
    (tmp_path / "Inspect.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
        '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
    checked([DOTNET, "build", "Inspect.csproj", "-c", "Release", "--nologo"], tmp_path)
    output = checked([str(tmp_path / "bin/Release/net48/Inspect.exe"), str(SDK / "Aveva.Core.Database.dll")], tmp_path)
    assert output.splitlines() == ["Underlying=System.Int32", "Design=1", "Catalog=2", "Catalogue=2", "Property=4"]
    document = ET.parse(SDK / "Aveva.Core.Database.xml")
    for name in ("P:Aveva.Core.Database.DbElement.ElementType", "M:Aveva.Core.Database.DbElement.GetElementType"):
        member = document.find(f"./members/member[@name='{name}']/summary")
        assert member is not None and "base type is returned" in " ".join("".join(member.itertext()).split())
    print("Metadata-only SDK contract: Int32 Design=1, Catalog=Catalogue=2, Property=4; ElementType is the base noun.")
