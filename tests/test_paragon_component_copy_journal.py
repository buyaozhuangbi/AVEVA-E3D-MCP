"""Compile and execute only the pure managed copy-journal production source."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import pytest

from test_model_validation_native_contract import block_end


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "bridge/e3d21/ParagonComponentCopyJournal.cs"
HARNESS = ROOT / "tests/e3d21/ParagonComponentCopyJournalHarness.cs"
SDK = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10"))
SECTIONS = (
    "validation and exact budgets",
    "admission and callback capabilities, deduplication",
    "immutable receipts and strict critical facts",
    "256 KiB and UTF8 omissions, pure JSON serialization",
    "concurrent dedup, first completion, start/cancel races, status reads",
    "running timeout, active capacity, completion expiry and eviction",
    "full 65536-ID session tombstone boundary",
    "isolated parameter facts survive full receipt omission",
)


@pytest.fixture(scope="module")
def journal_output():
    json_library = SDK / "Newtonsoft.Json.dll"
    if not shutil.which("dotnet") or not json_library.is_file():
        pytest.skip("Requires offline dotnet and the Newtonsoft JSON library; no AVEVA assembly is loaded")
    with tempfile.TemporaryDirectory(prefix="paragon-component-copy-journal-") as temporary:
        target = Path(temporary)
        shutil.copyfile(SOURCE, target / SOURCE.name)
        shutil.copyfile(HARNESS, target / "Harness.cs")
        shutil.copyfile(json_library, target / "Newtonsoft.Json.dll")
        (target / "Offline.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
            '<TargetFramework>net48</TargetFramework><OutputType>Exe</OutputType>'
            '<LangVersion>latest</LangVersion></PropertyGroup><ItemGroup>'
            '<Reference Include="Newtonsoft.Json"><HintPath>Newtonsoft.Json.dll</HintPath>'
            '</Reference></ItemGroup></Project>', encoding="utf-8")
        options = dict(cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace")
        compiled = subprocess.run(
            ["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"], timeout=90, **options)
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
        executed = subprocess.run([str(target / "bin/Release/net48/Offline.exe")], timeout=120, **options)
        assert executed.returncode == 0, executed.stdout + executed.stderr
        assert "PARAGON component copy journal production checks passed:" in executed.stdout
        assert "hostile serializers invoked=0" in executed.stdout
        print(executed.stdout)
        return executed.stdout


@pytest.mark.parametrize("section", SECTIONS)
def test_managed_production_journal_contract(journal_output, section):
    assert f"Copy journal section passed: {section}" in journal_output


def _method(source: str, name: str) -> str:
    declaration = re.search(
        rf"^    (?:internal|private) (?:static )?[^\n]+\b{re.escape(name)}\(", source, re.MULTILINE)
    assert declaration is not None, f"Missing production journal method: {name}"
    opening = source.index("{", declaration.end())
    return source[declaration.start():block_end(source, opening)]


def test_journal_is_pure_managed_and_completion_serializes_outside_the_claim_lock():
    source = SOURCE.read_text(encoding="utf-8-sig")
    for forbidden in (
        "using Aveva.", "NativeRead(", "NativeWrite(", "OnUi(", "CommandState(", "ExecutePmlScript(",
        "System.Reflection", "GetProperty(", "JsonSerializer.", ".Serialize(", "source_sha256", "byte_count",
    ):
        assert forbidden not in source
    assert 'Operation = "paragon_component_copy"' in source
    begin = _method(source, "Begin")
    assert "new AdmissionResult(false, null," in begin
    assert "seenIds.Add(id)" in begin and "seenIds.Remove" not in source
    start = _method(source, "TryStart")
    before = _method(source, "CompleteBeforeStart")
    started = _method(source, "CompleteStarted")
    assert "TryAdmission(admission, out" in start and "TryAdmission(admission, out" in before
    assert "ReferenceEquals(entry.Admission, admission)" in _method(source, "TryAdmission")
    assert "ReferenceEquals(entry.Callback, callback)" in started
    for completion in (before, started):
        assert completion.index("entry.CompletionClaimed = true") < completion.index("CompleteClaimed(")
        opening = completion.index("{", completion.index("lock (sync)"))
        assert block_end(completion, opening) < completion.index("return CompleteClaimed(")
    serialization = _method(source, "CompleteClaimed")
    assert serialization.index("SerializeBounded(receiptEnvelope)") < serialization.index("lock (sync)")
    assert "entry.Summary = summary" in serialization
    assert "entry.State = EntryState.Completed" in serialization
    assert "token.WriteTo" not in source and ").WriteTo" not in source
    assert "Type type = token.GetType()" in _method(source, "WritePureJson")
    snapshot = _method(source, "Snapshot")
    for capability in ("entry.Admission", "entry.Callback", "AdmissionToken", "CallbackToken"):
        assert capability not in snapshot
    assert '["retry_safe"] = false' in snapshot
