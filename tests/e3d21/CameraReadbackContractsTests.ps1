param(
    [string]$ReportPath
)

# Compile current production camera methods against test-only SDK substitutes.
# Requires a .NET 8 or newer SDK; does not load AVEVA assemblies or contact E3D.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$cameraPath = Join-Path $repository 'bridge\e3d21\E3d21ViewCamera.cs'
$contextPath = Join-Path $repository 'bridge\e3d21\E3d21Context.cs'
$fakePath = Join-Path $PSScriptRoot 'CameraReadbackFakes.cs'
$harnessPath = Join-Path $PSScriptRoot 'CameraReadbackHarness.cs'
$sdkVersion = (& dotnet --version | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $sdkVersion -notmatch '^(\d+)\.') {
    throw 'Could not determine the installed .NET SDK version.'
}
$sdkMajor = [int]$Matches[1]
if ($sdkMajor -lt 8) { throw 'Camera readback tests require a .NET 8 or newer SDK.' }

# Extract the live FitView method on every invocation. Never check in a second
# implementation; fail closed if the production signature or boundary changes.
$context = [IO.File]::ReadAllText($contextPath)
$signature = '    public object FitView(bool shaded, bool isometric, bool autoAddCurrent)'
$methodStart = $context.IndexOf($signature, [StringComparison]::Ordinal)
if ($methodStart -lt 0 -or $context.IndexOf($signature, $methodStart + 1, [StringComparison]::Ordinal) -ge 0) {
    throw 'Expected exactly one production FitView method with the verified signature.'
}
$methodEnd = $context.IndexOf('    // E3D does not persist', $methodStart, [StringComparison]::Ordinal)
if ($methodEnd -le $methodStart) { throw 'The verified end boundary of FitView was not found.' }
$fitMethod = $context.Substring($methodStart, $methodEnd - $methodStart).TrimEnd()
if (-not $fitMethod.EndsWith('}') -or ([regex]::Matches($fitMethod, '(?m)^    public ').Count -ne 1)) {
    throw 'FitView extraction included an unexpected method boundary.'
}

$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/')
$scratch = Join-Path $tempRoot ('camera-readback-contracts-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($scratch)
$utf8 = New-Object Text.UTF8Encoding($false)
try {
    $fitSource = @"
using System;
using System.Collections.Generic;
using Aveva.Core.Database;
using Aveva.Core3D.Graphics;
namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
$fitMethod
    }
}
"@
    [IO.File]::WriteAllText((Join-Path $scratch 'FitView.cs'), $fitSource, $utf8)
    $cameraXml = [Security.SecurityElement]::Escape($cameraPath)
    $fakeXml = [Security.SecurityElement]::Escape($fakePath)
    $harnessXml = [Security.SecurityElement]::Escape($harnessPath)
    $project = @"
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net$sdkMajor.0</TargetFramework>
    <OutputType>Exe</OutputType>
    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>
    <LangVersion>latest</LangVersion>
    <DefineConstants>CAMERA_READBACK_TEST_HARNESS</DefineConstants>
  </PropertyGroup>
  <ItemGroup>
    <Compile Include="$cameraXml" Link="E3d21ViewCamera.cs" />
    <Compile Include="$fakeXml" Link="CameraReadbackFakes.cs" />
    <Compile Include="$harnessXml" Link="CameraReadbackHarness.cs" />
    <Compile Include="FitView.cs" />
  </ItemGroup>
</Project>
"@
    $projectPath = Join-Path $scratch 'CameraReadbackHarness.csproj'
    [IO.File]::WriteAllText($projectPath, $project, $utf8)
    # Windows PowerShell wraps native stderr as ErrorRecord objects. Retain all
    # diagnostics and use the process exit code, rather than aborting on stderr.
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& dotnet run --project $projectPath --verbosity quiet 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = 'Stop' }
    $output | ForEach-Object { Write-Output $_ }
    if ($exitCode -ne 0) { throw ('Offline camera readback checks failed with exit code ' + $exitCode) }
    $match = [regex]::Match(($output -join "`n"), 'PASS: (\d+) offline camera readback checks')
    if (-not $match.Success) { throw 'The test harness returned no completion evidence.' }
    if ($ReportPath) {
        $report = [ordered]@{
            success = $true
            checks = [int]$match.Groups[1].Value
            sdk_version = $sdkVersion
            execution = 'Offline fake SDK; no AVEVA references, native initialization, or E3D calls.'
            source = @{
                view_camera_sha256 = (Get-FileHash -LiteralPath $cameraPath -Algorithm SHA256).Hash
                context_sha256 = (Get-FileHash -LiteralPath $contextPath -Algorithm SHA256).Hash
                fakes_sha256 = (Get-FileHash -LiteralPath $fakePath -Algorithm SHA256).Hash
                harness_sha256 = (Get-FileHash -LiteralPath $harnessPath -Algorithm SHA256).Hash
            }
            output = @($output | ForEach-Object { [string]$_ })
        }
        $resolvedReport = [IO.Path]::GetFullPath($ReportPath)
        [IO.File]::WriteAllText($resolvedReport, ($report | ConvertTo-Json -Depth 5), $utf8)
    }
}
finally {
    # Remove only this script's freshly created child of the resolved temp root.
    $resolvedScratch = [IO.Path]::GetFullPath($scratch)
    if ([IO.Path]::GetDirectoryName($resolvedScratch) -ne $tempRoot -or
        [IO.Path]::GetFileName($resolvedScratch) -notmatch '^camera-readback-contracts-[0-9a-f]{32}$') {
        throw 'Refusing to remove a test directory outside the verified temporary root.'
    }
    Remove-Item -LiteralPath $resolvedScratch -Recurse -Force
}
