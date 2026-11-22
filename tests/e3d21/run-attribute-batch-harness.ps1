$ErrorActionPreference = 'Stop'
$repo = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$project = Join-Path $PSScriptRoot 'AttributeBatchHarness.csproj'
dotnet run --project $project --no-restore
if ($LASTEXITCODE -ne 0) { throw "Attribute batch contract harness failed with exit code $LASTEXITCODE." }
dotnet build (Join-Path $repo 'bridge\e3d21\E3d21HttpAdapter.csproj') --no-restore --configuration Debug
if ($LASTEXITCODE -ne 0) { throw "E3D21 Debug build failed with exit code $LASTEXITCODE." }
dotnet build (Join-Path $repo 'bridge\e3d21\E3d21HttpAdapter.csproj') --no-restore --configuration Release
if ($LASTEXITCODE -ne 0) { throw "E3D21 Release build failed with exit code $LASTEXITCODE." }
