"""Public optional SDK reader exercised with managed-only recording fakes.

No AVEVA assembly is loaded. The real SDK guards, dispatcher and context digest
are compiled with the production manifest; no HTTP or live SDK is invoked.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import pytest

from test_model_validation_native_contract import extract_method

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge/e3d21"
SDK_ROOT = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10"))


def test_manifest_public_optional_contract_is_read_only():
    managed = (BRIDGE / "TeamDatabaseIdentityManifest.cs").read_text(encoding="utf-8-sig")
    native = (BRIDGE / "E3d21TeamDatabaseIdentityManifest.cs").read_text(encoding="utf-8-sig")
    assert "public interface IE3d21TeamDatabaseIdentityManifestContext" in managed
    assert "object ReadTeamDatabaseIdentityManifest(string sessionId, string expectedContext, int limit = 64);" in managed
    assert "public sealed partial class E3d21Context : IE3d21TeamDatabaseIdentityManifestContext" in native
    assert "public object ReadTeamDatabaseIdentityManifest(string sessionId, string expectedContext, int limit = 64)" in native
    assert "TeamDatabaseIdentityManifestRequest.Validate(expectedContext, limit)" in native
    assert not re.search(r"\b(?:NativeWrite|SetAttribute|CreateFirst|CreateLast|SaveWork|CreateDBModification|Standalone|Initialise|ExecutePml|GetAllWorldLevel)\s*[.(]", native)
    project = (BRIDGE / "E3d21HttpAdapter.csproj").read_text(encoding="utf-8-sig")
    assert '<Compile Include="TeamDatabaseIdentityManifest.cs" />' in project
    assert '<Compile Include="E3d21TeamDatabaseIdentityManifest.cs" />' in project


@pytest.mark.skipif(not shutil.which("dotnet") or not (SDK_ROOT / "Aveva.Core.Database.dll").is_file(),
                    reason="Public SDK signature compilation requires dotnet and installed Core Database metadata")
def test_manifest_public_sdk_signatures_compile_without_execution(record_property):
    with tempfile.TemporaryDirectory(prefix="team-database-sdk-compile-") as temporary:
        target = Path(temporary)
        built = subprocess.run([
            "dotnet", "build", str(ROOT / "tests/e3d21/TeamDatabaseIdentityManifestSdkCompile.csproj"),
            "-c", "Release", "--nologo", "-o", str(target / "bin"),
            "-p:BaseIntermediateOutputPath=" + str(target / "obj") + os.sep,
            "-p:E3D21_ROOT=" + str(SDK_ROOT),
        ], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        assert built.returncode == 0, built.stdout + built.stderr
        assert (target / "bin/TeamDatabaseIdentitySdkContract.dll").is_file()
        record_property("sdk_signature_compile_log", built.stdout + built.stderr)
        print("Public E3D21 SDK signatures compiled as a library; no SDK code executed.")


@pytest.fixture(scope="module")
def managed_receipts():
    if not shutil.which("dotnet"):
        pytest.skip("Managed offline harness requires dotnet")
    context = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
    database = (BRIDGE / "E3d21DatabaseState.cs").read_text(encoding="utf-8-sig")
    harness = (ROOT / "tests/e3d21/TeamDatabaseIdentityManifestHarness.cs").read_text(encoding="utf-8-sig")
    helpers = [extract_method(context, name) for name in ("NativeRead", "ThrowIfNativeFaulted", "OnUi")]
    helpers.append(extract_method(database, "DatabaseStateContext"))
    harness = harness.replace("// PRODUCTION_HELPERS", "\n\n".join(helpers))
    with tempfile.TemporaryDirectory(prefix="team-database-identity-managed-") as temporary:
        target = Path(temporary)
        (target / "Harness.cs").write_text(harness, encoding="utf-8")
        for name in ("TeamDatabaseIdentityManifest.cs", "E3d21TeamDatabaseIdentityManifest.cs", "NativeDatabaseFaultState.cs"):
            shutil.copyfile(BRIDGE / name, target / name)
        (target / "Offline.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
            '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup>'
            '<ItemGroup><Reference Include="System.Web.Extensions" /></ItemGroup></Project>', encoding="utf-8")
        options = dict(cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace")
        built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"], timeout=90, **options)
        assert built.returncode == 0, built.stdout + built.stderr
        ran = subprocess.run([str(target / "bin/Release/net48/Offline.exe")], timeout=45, **options)
        assert ran.returncode == 0, "\n".join(line for line in ran.stdout.splitlines() if not line.startswith("MANIFEST_")) + ran.stderr
        assert "Team/DB identity manifest production checks passed: " in ran.stdout
        assert "Team/DB native fault matrix: " in ran.stdout
        lines = ran.stdout.splitlines()
        return {
            "normal": json.loads(next(line.removeprefix("MANIFEST_NORMAL=") for line in lines if line.startswith("MANIFEST_NORMAL="))),
            "conflict": json.loads(next(line.removeprefix("MANIFEST_CONFLICT=") for line in lines if line.startswith("MANIFEST_CONFLICT="))),
            "expected_build": next(line.removeprefix("EXPECTED_BUILD=") for line in lines if line.startswith("EXPECTED_BUILD=")),
            "receipts": [json.loads(line.removeprefix("MANIFEST_RECEIPT=")) for line in lines if line.startswith("MANIFEST_RECEIPT=")],
            "matrix": "\n".join(line for line in lines if line.startswith("Team/DB ")),
        }


def test_team_database_identity_manifest_with_managed_fakes(managed_receipts, record_property):
    normal, conflict = managed_receipts["normal"], managed_receipts["conflict"]
    assert normal["read_only"] is True and normal["success"] is True
    assert [row["current_mdb"]["candidate_indexes"] for row in normal["databases"]] == [[1], [2], [0]]
    assert all(row["current_mdb"]["status"] == "indeterminate"
               and row["current_mdb"]["matched_index"] is None
               and row["current_mdb"]["semantic_equivalence_verified"] is False for row in normal["databases"])
    assert all(row["identity_coherent"] is None for row in normal["current_mdb"])
    assert normal["scope"]["extract_number_semantics_verified"] is False
    assert normal["scope"]["mdb_record_join_verified"] is False
    assert normal["databases"][0]["team_relation"]["confirmed_team"]["type"] == "TEAM"
    assert all(row["team_relation"]["confirmed_team"] is None for row in conflict["databases"])
    assert all(row["team_relation"]["status"] == "conflict" for row in conflict["databases"])
    record_property("managed_normal_manifest", json.dumps(normal, ensure_ascii=False))
    record_property("managed_conflict_manifest", json.dumps(conflict, ensure_ascii=False))
    record_property("managed_matrix", managed_receipts["matrix"])
    print(managed_receipts["matrix"])


def test_native_receipts_satisfy_final_python_contract(managed_receipts, record_property):
    from aveva_e3d_mcp.team_database_identity import validate_team_database_identity_manifest

    receipts = managed_receipts["receipts"]
    assert len(receipts) >= 50
    for index, captured in enumerate(receipts):
        try:
            validated = validate_team_database_identity_manifest(
                captured["reply"], captured["arguments"],
                expected_session="SESSION", expected_build=managed_receipts["expected_build"],
            )
        except (ValueError, AssertionError) as error:
            pytest.fail(f"Production native receipt {index} failed final Python contract: {error}")
        assert validated["available"] is True
    record_property("managed_production_receipts", len(receipts))
    print(f"Team/DB production native receipts validated by Python: {len(receipts)}.")
