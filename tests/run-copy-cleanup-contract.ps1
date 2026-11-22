param([string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$copyTestRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $copyTestRoot 'artifacts/copy-cleanup-contract-20261116'
}
$copyOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $copyOutput -Force | Out-Null
$env:E3D_COPY_TEST_ROOT = $copyTestRoot
$env:E3D_COPY_TEST_OUTPUT = $copyOutput
@'
import os, pathlib, runpy
root = pathlib.Path(os.environ['E3D_COPY_TEST_ROOT'])
output = pathlib.Path(os.environ['E3D_COPY_TEST_OUTPUT'])
extract = runpy.run_path(str(root / 'tests/test_native_fault_barriers.py'))['method']
bridge = root / 'bridge/e3d21'
methods = []
for filename, names in [
    ('E3d21CopyEditing.cs', ['CopyCleanup', 'CopyReadIdentities', 'CopyChildren', 'CopyIdentity']),
    ('E3d21ModelEditing.cs', ['ModelCleanupCreated', 'ModelMemberReferences', 'ModelRequireElement', 'ModelReference']),
    ('E3d21Context.cs', ['DirectSiblings', 'IsUsable', 'NativeRead', 'NativeWrite', 'ThrowIfNativeFaulted', 'OnUi'])]:
    source = (bridge / filename).read_text(encoding='utf-8-sig')
    methods.extend(extract(source, name) for name in names)
harness = (root / 'tests/e3d21/CopyCleanupHarness.cs').read_text(encoding='utf-8')
(output / 'Harness.cs').write_text(harness.replace('// PRODUCTION_METHODS', '\n\n'.join(methods)), encoding='utf-8')
for filename in ['CopyTreePolicy.cs', 'MemberPage.cs', 'NativeDatabaseFaultState.cs', 'ModelEditInput.cs', 'AttributeEditPolicy.cs']:
    (output / filename).write_bytes((bridge / filename).read_bytes())
(output / 'Offline.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework><OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding='utf-8')
'@ | python -
if ($LASTEXITCODE -ne 0) { throw 'Copy cleanup test source extraction failed.' }
& dotnet build (Join-Path $copyOutput 'Offline.csproj') -c Release --nologo
if ($LASTEXITCODE -ne 0) { throw 'Offline copy cleanup test compilation failed.' }
& (Join-Path $copyOutput 'bin/Release/net48/Offline.exe')
if ($LASTEXITCODE -ne 0) { throw 'Offline copy cleanup contract checks failed.' }
