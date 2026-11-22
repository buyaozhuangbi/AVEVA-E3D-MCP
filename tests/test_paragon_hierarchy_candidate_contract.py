"""Build the isolated PARAGON MemberTypes/IsCreatable recording-fake contract."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests/e3d21/ParagonHierarchyCandidateHarness.cs"
PML_ROOT = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10")) / "PMLLIB/paragon"


@pytest.mark.skipif(not (PML_ROOT / "forms/catcreate.pmlfrm").is_file(), reason="Installed E3D21 PARAGON source is unavailable")
def test_reported_catalogue_chain_and_new_branches_match_installed_pml():
    form = (PML_ROOT / "forms/catcreate.pmlfrm").read_text(encoding="utf-8", errors="replace")
    command = (PML_ROOT / "commands/cataloguecreatecatalogue.pmlcmd").read_text(encoding="utf-8", errors="replace")
    for evidence in (
        r"!this\.ownerTypes\[13\]\s*=\s*\|CATA\|",
        r"!this\.ownerTypes\[19\]\s*=\s*\|SECT\|",
        r"!this\.refDataTypes\[13\]\s*=\s*\|CATE\|",
        r"--\s*if \(!!catRefData\.canCreate\(!this\.type\)\.not\(\)\) then",
        r"elseif \(!this\.type inset\(\|CATE\|, \|STCA\|\)\) then",
        r"!!catModelViews\.create\(!name, !this\.purposeType\)",
        r"NEW \$!<this\.type>",
    ):
        assert re.search(evidence, form), evidence
    assert "call !!catCreate('CATA')" in command


@pytest.mark.skipif(not shutil.which("dotnet"), reason="Offline recording-fake contract requires dotnet")
def test_paragon_membertypes_iscreatable_and_special_new_route_contract():
    with tempfile.TemporaryDirectory(prefix="paragon-hierarchy-candidate-") as temporary:
        target = Path(temporary)
        shutil.copyfile(HARNESS, target / "Harness.cs")
        (target / "Offline.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net10.0</TargetFramework>'
            '<OutputType>Exe</OutputType><ImplicitUsings>disable</ImplicitUsings><Nullable>disable</Nullable>'
            '<LangVersion>latest</LangVersion></PropertyGroup></Project>',
            encoding="utf-8",
        )
        options = dict(cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace")
        built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"], timeout=90, **options)
        assert built.returncode == 0, built.stdout + built.stderr
        ran = subprocess.run(["dotnet", str(target / "bin/Release/net10.0/Offline.dll")], timeout=45, **options)
        assert ran.returncode == 0, ran.stdout + ran.stderr
        assert "PARAGON hierarchy candidate offline contract passed (9 pairs; no mutation calls)." in ran.stdout
