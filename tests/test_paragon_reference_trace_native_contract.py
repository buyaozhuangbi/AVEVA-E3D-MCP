"""Exercise the isolated reference trace without loading E3D or opening a DB."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_model_validation_native_contract import extract_method


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge/e3d21"
READER = "E3d21ParagonReferenceTrace.cs"
DOTNET = shutil.which("dotnet")
SDK = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10"))


def checked(command, workdir, *, timeout=90):
    result = subprocess.run(command, cwd=workdir, timeout=timeout, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.skipif(not DOTNET or os.name != "nt", reason="Requires the offline Windows .NET Framework build tools")
def test_paragon_reference_trace_against_recording_sdk(tmp_path):
    helpers = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
    source = (ROOT / "tests/e3d21/ParagonReferenceTraceHarness.cs").read_text(encoding="utf-8")
    (tmp_path / "Harness.cs").write_text(source.replace("// PRODUCTION_NATIVE_HELPERS", "\n\n".join(
        extract_method(helpers, name) for name in ("NativeRead", "ThrowIfNativeFaulted", "OnUi"))), encoding="utf-8")
    for name in (READER, "NativeDatabaseFaultState.cs"):
        shutil.copyfile(BRIDGE / name, tmp_path / name)
    (tmp_path / "Offline.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
        '<LangVersion>latest</LangVersion><OutputType>Exe</OutputType></PropertyGroup></Project>', encoding="utf-8")
    checked([DOTNET, "build", "Offline.csproj", "-c", "Release", "--nologo"], tmp_path)
    receipt = checked([str(tmp_path / "bin/Release/net48/Offline.exe")], tmp_path, timeout=30)
    assert "Reference trace checks passed" in receipt
    print(receipt.strip())


@pytest.mark.skipif(not DOTNET or os.name != "nt" or not (SDK / "Aveva.Core.Database.dll").is_file(),
                    reason="The E3D21 SDK is required for the compile-only verification")
def test_trace_compiles_against_installed_e3d21_sdk(tmp_path):
    # Compile the actual reader against installed metadata only. The separate
    # fake harness exercises the real guards. This DLL is never loaded.
    shutil.copyfile(BRIDGE / READER, tmp_path / READER)
    (tmp_path / "ContextSignatures.cs").write_text(
        "using System; namespace E3d21AiBridge; public sealed partial class E3d21Context {\n"
        "private bool IsCommandActive() => throw new NotImplementedException();\n"
        "private T OnUi<T>(Func<T> op, bool allowNativeFault = false) => throw new NotImplementedException();\n"
        "private static T NativeRead<T>(Func<T> op) => throw new NotImplementedException();\n"
        "private static void ThrowIfNativeFaulted() => throw new NotImplementedException();\n"
        "private static Aveva.Core.Database.DbElement CurrentElement() => throw new NotImplementedException();\n"
        "}", encoding="utf-8")
    project = "SdkCompile.csproj"
    inputs = "".join(f'<Reference Include="{name}"><HintPath>$(E3D21_ROOT)\\{name}.dll</HintPath>'
                     '<Private>False</Private></Reference>' for name in ("Aveva.Core.Database", "Aveva.Core.Utilities"))
    (tmp_path / project).write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
        f'<LangVersion>latest</LangVersion></PropertyGroup><ItemGroup>{inputs}</ItemGroup></Project>', encoding="utf-8")
    checked([DOTNET, "build", project, "-c", "Release", "--nologo", f"-p:E3D21_ROOT={SDK}"], tmp_path)
