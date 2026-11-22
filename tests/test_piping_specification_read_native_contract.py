"""Compile production reads with managed fakes and compile-only real SDK types.

No E3D process, listener, vendor database assembly execution or model write is
performed. Every fake-produced result is checked by the production Python
consumer; the independently constructed HTTP wrapper does not certify routing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from xml.sax.saxutils import escape

import pytest

from aveva_e3d_mcp.piping_specification_read import _validate
from test_model_validation_native_contract import extract_method


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge/e3d21"
SDK = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10"))
SDK_JSON = SDK / "Newtonsoft.Json.dll"
SOURCES = ("PipingSpecificationReadRequest.cs", "E3d21PipingSpecificationRead.cs",
           "ParagonCatalogueManifest.cs", "EquipmentManifest.cs", "NativeDatabaseFaultState.cs")


def _guard_helpers() -> str:
    context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
    return "\n\n".join(extract_method(context, name) for name in ("NativeRead", "ThrowIfNativeFaulted", "OnUi"))


def _copy_sources(target: Path) -> None:
    for name in SOURCES:
        shutil.copyfile(BRIDGE / name, target / name)


def _build(target: Path, references: str, *, executable: bool) -> str:
    (target / "Offline.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
        + ('<OutputType>Exe</OutputType>' if executable else '')
        + '<LangVersion>latest</LangVersion></PropertyGroup><ItemGroup>'
        + references + '</ItemGroup></Project>', encoding="utf-8")
    options = dict(cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace")
    built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo", "-warnaserror"], timeout=90, **options)
    assert built.returncode == 0, built.stdout + built.stderr
    if not executable:
        return built.stdout
    ran = subprocess.run([str(target / "bin/Release/net48/Offline.exe")], timeout=45, **options)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return ran.stdout


@pytest.fixture(scope="module")
def native_run() -> tuple[str, list[dict]]:
    if not shutil.which("dotnet") or not SDK_JSON.is_file():
        pytest.skip("Offline fake harness requires dotnet and Newtonsoft.Json.dll")
    harness = (ROOT / "tests/e3d21/PipingSpecificationReadHarness.cs").read_text(encoding="utf-8")
    harness = harness.replace("// PRODUCTION_HELPERS", _guard_helpers())
    with tempfile.TemporaryDirectory(prefix="piping-specification-reader-fakes-") as temporary:
        target = Path(temporary)
        (target / "Harness.cs").write_text(harness, encoding="utf-8")
        _copy_sources(target)
        shutil.copyfile(SDK_JSON, target / SDK_JSON.name)
        output = _build(target, '<Reference Include="Newtonsoft.Json"><HintPath>Newtonsoft.Json.dll</HintPath>'
                        '<Private>true</Private></Reference>', executable=True)
    prefix = "Piping spec contract receipt: "
    receipts = [json.loads(line[len(prefix):]) for line in output.splitlines() if line.startswith(prefix)]
    assert receipts, output
    return output, receipts


def test_production_spec_reader_typed_scope_unknowns_budgets_and_fault_barrier(native_run):
    output, _ = native_run
    assert "Piping specification production checks passed:" in output
    assert "rejected replay" in output
    for line in output.splitlines():
        if line.startswith(("Piping specification native fault matrix:", "Piping specification production checks passed:",
                            "Piping specification production receipts:")):
            print(line)


def test_native_receipts_satisfy_python_contract(native_run):
    _, receipts = native_run
    for index, receipt in enumerate(receipts):
        try:
            _validate(receipt["reply"], receipt["payload"])
        except ValueError as error:
            pytest.fail(f"Production native receipt {index} failed Python evidence validation: {error}\n"
                        + json.dumps(receipt, ensure_ascii=True))
    assert any(row["reply"]["result"]["complete"] is True for row in receipts)
    assert any(row["reply"]["result"]["complete"] is False for row in receipts)


def test_production_reader_compiles_against_installed_sdk_without_loading_it():
    assemblies = ("Aveva.Core.Database", "Aveva.Core.Utilities")
    if not shutil.which("dotnet") or not all((SDK / (name + ".dll")).is_file() for name in assemblies):
        pytest.skip("Compile-only check requires dotnet and installed E3D21 SDK assemblies")
    skeleton = '''using System;
using Aveva.Core.Database;
namespace E3d21AiBridge {
internal static class ModelEditInput {
    internal static string ExplicitReference(string text, string field) { throw new NotSupportedException("Compile-only dependency."); }
}
public sealed partial class E3d21Context {
    private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
    [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
    [ThreadStatic] private static long permittedNativeFaultGeneration;
    private readonly Action<Action> invokeOnUi = null;
    private bool IsCommandActive() { throw new NotSupportedException("Compile-only dependency."); }
    private static DbElement CurrentElement() { throw new NotSupportedException("Compile-only dependency."); }
    // PRODUCTION_HELPERS
}}
'''.replace("// PRODUCTION_HELPERS", _guard_helpers())
    references = ''.join('<Reference Include="' + name + '"><HintPath>' + escape(str(SDK / (name + '.dll')))
                         + '</HintPath><Private>false</Private></Reference>' for name in assemblies)
    with tempfile.TemporaryDirectory(prefix="piping-specification-sdk-compile-") as temporary:
        target = Path(temporary)
        _copy_sources(target)
        (target / "CompileOnlyContext.cs").write_text(skeleton, encoding="utf-8")
        _build(target, references, executable=False)
