param(
    [string]$AssemblyPath = (Join-Path $PSScriptRoot '..\..\bridge\e3d21\bin\Debug\net48\E3d21AiAddin.dll'),
    [string]$SdkPath = 'C:\Program Files (x86)\AVEVA\Everything3D2.10',
    [string]$ReportPath
)

# Pure managed colour fixtures and temporary file tests. No E3D connection,
# native initialization, constructor side effects, or display writes.
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) {
    # E3D 2.1 SDK assemblies target x86; use an isolated x86 PowerShell process.
    $runner = Join-Path $env:WINDIR 'SysWOW64\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath,
        '-AssemblyPath', $AssemblyPath, '-SdkPath', $SdkPath)
    if ($ReportPath) { $arguments += @('-ReportPath', $ReportPath) }
    & $runner @arguments
    if ($LASTEXITCODE -ne 0) { throw ('The x86 contract checks failed with exit code ' + $LASTEXITCODE) }
    return
}
Add-Type -Path (Join-Path $SdkPath 'Newtonsoft.Json.dll')
Add-Type -Path (Join-Path $SdkPath 'Aveva.Core3D.Graphics.dll')
[void][Reflection.Assembly]::LoadFrom((Join-Path $SdkPath 'Aveva.Core.Database.dll'))
[void][Reflection.Assembly]::LoadFrom((Join-Path $SdkPath 'Aveva.Core.Utilities.dll'))
Add-Type -TypeDefinition @'
using Aveva.Core3D.Graphics;
using System;
using System.Collections.Generic;
public sealed class DrawListTestColour : Colour
{
    private readonly int index;
    private readonly string name;
    public DrawListTestColour(int index, string name) { this.index = index; this.name = name; }
    public override int Index { get { return index; } }
    public override string Name { get { return name; } }
    public override int Red { get { return 0; } }
    public override int Green { get { return 0; } }
    public override int Blue { get { return 0; } }
}
public sealed class DrawListTestColours : Colours
{
    private readonly Colour[] table;
    private static Colours previous;
    private DrawListTestColours(Colour[] table) { this.table = table; }
    protected override Colour[] InternalColourTable() { return table; }
    public static void Install(Colour[] table) { previous = coloursInstance; coloursInstance = new DrawListTestColours(table); }
    public static void Reset() { coloursInstance = previous; }
    public static readonly Action<Action> DirectUi = action => action();
    public static readonly Action RefreshOk = () => { };
    public static readonly Action RefreshFails = () => { throw new InvalidOperationException("simulated refresh failure"); };
    public static readonly Action<string> FixtureSubtree = reference =>
    {
        if (reference != "=1/2" && reference != "=1/3") throw new InvalidOperationException("outside fixture subtree");
    };
    public static void SetPaths(Dictionary<string, object> result, string json, string legacy)
    {
        result["path"] = json;
        ((Dictionary<string, object>)result["legacy_export"])["path"] = legacy;
    }
}
'@ -ReferencedAssemblies (Join-Path $SdkPath 'Aveva.Core3D.Graphics.dll')

$assemblyFile = (Resolve-Path -LiteralPath $AssemblyPath).Path
$assembly = [Reflection.Assembly]::LoadFrom($assemblyFile)
$contextType = $assembly.GetType('E3d21AiBridge.E3d21Context', $true)
$documentType = $assembly.GetType('E3d21AiBridge.E3d21Context+DrawListVisualDocument', $true)
$flags = [Reflection.BindingFlags]'Static,NonPublic'
$resolveColour = $contextType.GetMethod('ResolveColourIndex', $flags)
$newPersistence = $contextType.GetMethod('DrawListPersistenceResult', $flags)
$persistDocument = $contextType.GetMethod('PersistDrawListVisualDocument', $flags)
$save = $contextType.GetMethod('SaveDrawListState', $flags)
$setVisual = $contextType.GetMethod('SetDrawListVisual')
$requestChanges = $contextType.GetMethod('DrawListVisualRequestChanges', $flags)
$refreshAttempt = $contextType.GetMethod('AttemptDrawListVisualRefresh', $flags)
$restoreKeys = $contextType.GetMethod('RequireDrawListVisualRestoreKeys', $flags)
$addScope = $contextType.GetMethod('ValidateDrawListVisualAddScope', $flags)
$memberType = $assembly.GetType('E3d21AiBridge.E3d21Context+DrawListVisualEntry', $true)
$primitiveType = $assembly.GetType('E3d21AiBridge.E3d21Context+DrawListPrimitiveVisualEntry', $true)
$mapType = $assembly.GetType('E3d21AiBridge.E3d21Context+PrimitiveVisualMap', $true)
$instanceFlags = [Reflection.BindingFlags]'Instance,Public,NonPublic'
$checks = [Collections.Generic.List[string]]::new()
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('drawlist-contracts-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($scratch)
$statePath = Join-Path $scratch 'state.drawlist'
$jsonPath = $statePath + '.json'
$validJson = '{"schema_version":1,"project":"TEST","mdb":"MDB","members":[{"refno":"=1/1","colour":1,"transparency":0,"edges":true,"visibility":true,"shallow":false}]}'

function Check([bool]$condition, [string]$name) {
    if (-not $condition) { throw ('Check failed: ' + $name) }
    $checks.Add($name)
}

function Reject([string]$name, [scriptblock]$action, [type]$exceptionType) {
    $failure = $null
    try { & $action } catch { $failure = $_.Exception }
    if ($null -eq $failure) { throw ('Expected rejection: ' + $name) }
    while ($null -ne $failure.InnerException) { $failure = $failure.InnerException }
    if (-not ($failure -is $exceptionType)) {
        throw ('Unexpected rejection for ' + $name + ': ' + $failure.GetType().FullName + ': ' + $failure.Message)
    }
    Check ($failure -is $exceptionType) $name
}

function Colour-Index($value) { $resolveColour.Invoke($null, [object[]]@($value)) }
function Document([string]$json = $validJson) { [Newtonsoft.Json.JsonConvert]::DeserializeObject($json, $documentType) }
function Result([string]$status = 'snapshot_failed') {
    $result = $newPersistence.Invoke($null, [object[]]@($status, $null))
    [DrawListTestColours]::SetPaths($result, $jsonPath, $statePath)
    return $result
}
function Persist($document) { $persistDocument.Invoke($null, [object[]]@((Result), $document, $false)) }

try {
    $member = [Newtonsoft.Json.JsonConvert]::DeserializeObject('{"refno":"=1/2","colour":0,"transparency":0,"edges":false,"visibility":true,"shallow":false}', $memberType)
    Check (-not $requestChanges.Invoke($null, [object[]]@($member, $null, $null, $null, $null, $null))) 'omitted five fields identify an exact-member no-op'
    Check (-not $requestChanges.Invoke($null, [object[]]@($member, [int]0, [int]0, $false, $true, $false))) 'same values including native zero identify a no-op'
    foreach ($index in 1..5) {
        $arguments = [object[]]@($member, $null, $null, $null, $null, $null)
        $arguments[$index] = if ($index -le 2) { [int]1 } elseif ($index -eq 4) { $false } else { $true }
        Check ($requestChanges.Invoke($null, $arguments)) ('changed field ' + $index + ' does not take the no-op branch')
    }
    $refresh = [Collections.Generic.Dictionary[string,object]]::new()
    Reject 'refresh failure is propagated rather than treated as unchanged' {
        $refreshAttempt.Invoke($null, [object[]]@($refresh, [DrawListTestColours]::RefreshFails))
    } ([InvalidOperationException])
    Check ($refresh['refresh_attempted'] -eq $true -and $refresh['refresh_attempt_count'] -eq 1 -and $refresh['graphics_updated'] -eq $false) 'attempt is recorded before a failing graphics callback'
    $refreshAttempt.Invoke($null, [object[]]@($refresh, [DrawListTestColours]::RefreshOk))
    Check ($refresh['refresh_attempt_count'] -eq 2 -and $refresh['graphics_updated'] -eq $true) 'successful refresh retains cumulative attempts'
    $restoreKeys.Invoke($null, [object[]]@([string[]]@('old'), [string[]]@('saved'), [string[]]@('saved','old'), 'fixture'))
    Check $true 'schema-2 restore permits exactly the initial and persisted key union'
    foreach ($actual in @(@('old','saved','extra'), @('old'), @('saved'))) {
        Reject ('schema-2 restore rejects key set ' + ($actual -join ',')) {
            $restoreKeys.Invoke($null, [object[]]@([string[]]@('old'), [string[]]@('saved'), [string[]]$actual, 'fixture'))
        } ([InvalidOperationException])
    }
    $beforeMap = [Activator]::CreateInstance($mapType, $true)
    $afterMap = [Activator]::CreateInstance($mapType, $true)
    $beforeMembers = $mapType.GetField('Members', $instanceFlags).GetValue($beforeMap)
    $beforeEntries = $mapType.GetField('Entries', $instanceFlags).GetValue($beforeMap)
    $afterMembers = $mapType.GetField('Members', $instanceFlags).GetValue($afterMap)
    $afterEntries = $mapType.GetField('Entries', $instanceFlags).GetValue($afterMap)
    $afterMembers.Add('=1/2', $member)
    $entry = [Newtonsoft.Json.JsonConvert]::DeserializeObject('{"owner_refno":"=1/2","refno":"=1/3","colour":0,"transparency":0,"edges":false,"visibility":true,"shallow":false}', $primitiveType)
    $afterEntries.Add('=1/2|=1/3', $entry)
    $scopeArgs = [object[]]@('=1/2', $beforeMembers, $beforeEntries, $afterMembers, $afterEntries, [DrawListTestColours]::FixtureSubtree)
    $addScope.Invoke($null, $scopeArgs)
    Check $true 'explicit Add accepts verified members and primitives inside only its requested subtree'
    $outside = [Newtonsoft.Json.JsonConvert]::DeserializeObject('{"owner_refno":"=1/2","refno":"=9/9","colour":0,"transparency":0,"edges":false,"visibility":true,"shallow":false}', $primitiveType)
    $afterEntries.Add('=1/2|=9/9', $outside)
    Reject 'Add cannot adopt an arbitrary new primitive key as expected state' { $addScope.Invoke($null, $scopeArgs) } ([InvalidOperationException])
    [void]$afterEntries.Remove('=1/2|=9/9')
    $beforeEntries.Add('=1/2|=1/3', $entry)
    [void]$afterEntries.Remove('=1/2|=1/3')
    Reject 'Add must retain every previously observed primitive key' { $addScope.Invoke($null, $scopeArgs) } ([InvalidOperationException])

    # Sparse fixture deliberately includes an index above 255: only the native
    # table's members define validity, not a fabricated contiguous numeric range.
    $table = [Aveva.Core3D.Graphics.Colour[]]@(
        [DrawListTestColour]::new(1, 'Black'),
        [DrawListTestColour]::new(12, 'Blue'),
        [DrawListTestColour]::new(511, 'Custom'))
    [DrawListTestColours]::Install($table)
    Check ($null -eq (Colour-Index $null) -and $null -eq (Colour-Index '  ')) 'omitted colour stays omitted'
    Check ((Colour-Index '1') -eq 1 -and (Colour-Index '511') -eq 511) 'indices are validated against the supplied native table'
    Check ((Colour-Index ' bLuE ') -eq 12) 'native colour names ignore case and surrounding whitespace'
    Check ((Colour-Index 'CoLoUr_511') -eq 511) 'SDK colour_N alias uses the same table'
    foreach ($bad in @('missing-colour', '-1', '0', '2', '512', '2147483648', 'colour_bad', 'colour_512')) {
        Reject ('invalid colour rejected: ' + $bad) { Colour-Index $bad } ([ArgumentException])
    }

    # Skip the live-wiring constructor. Invalid execution calls must reject
    # before accessing CurrentDrawList/Add. A valid preview now also requires
    # a live target, TOPF and membership; that contract is verified live.
    $context = [Runtime.Serialization.FormatterServices]::GetUninitializedObject($contextType)
    $contextType.GetField('invokeOnUi', $instanceFlags).SetValue($context, [DrawListTestColours]::DirectUi)
    foreach ($preview in @($true, $false)) {
        Reject ('invalid colour rejected before display access; preview=' + $preview) {
            $setVisual.Invoke($context, [object[]]@('=1/1', 'missing-colour', $null, $null, $null, $null, $true, $preview))
        } ([ArgumentException])
    }
    $previewResult = Result 'preview'
    Check (-not $previewResult['attempted'] -and $null -eq $previewResult['succeeded'] -and $previewResult['status'] -eq 'preview') 'preview never claims persistence'
    [DrawListTestColours]::Reset()
    [DrawListTestColours]::Install($null)
    Reject 'unavailable native table is not an omitted colour' { Colour-Index 'Blue' } ([InvalidOperationException])
    Check ($null -eq (Colour-Index '')) 'omission does not require a native colour table'

    $notNeeded = Result 'not_needed'
    Check (-not $notNeeded['attempted'] -and $null -eq $notNeeded['succeeded'] -and $notNeeded['legacy_export']['status'] -eq 'not_attempted') 'skipped save and legacy export are explicitly unattempted'
    $unavailable = $save.Invoke($null, [object[]]@($null))
    Check ($unavailable['attempted'] -and $unavailable['succeeded'] -eq $false -and $unavailable['status'] -eq 'drawlist_unavailable' -and $unavailable['error']) 'missing Drawlist returns an honest save failure without throwing'
    $saved = Persist (Document)
    Check ($saved['attempted'] -and $saved['succeeded'] -and $saved['status'] -eq 'saved' -and $null -eq $saved['error']) 'complete authoritative snapshot reports successful atomic persistence'
    if (-not ($saved['path'] -eq $jsonPath -and $saved['legacy_export']['path'] -eq $statePath -and $saved['legacy_export']['succeeded'])) {
        throw ('Unexpected persistence result: ' + ($saved | ConvertTo-Json -Depth 5))
    }
    Check ($true) 'JSON and legacy paths and success are distinct'
    $originalJson = [IO.File]::ReadAllText($jsonPath)
    $originalLegacy = [IO.File]::ReadAllText($statePath)

    $locked = [IO.File]::Open($jsonPath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try {
        $failed = Persist (Document ($validJson.Replace('"colour":1', '"colour":12')))
        Check ($failed['attempted'] -and $failed['succeeded'] -eq $false -and $failed['status'] -eq 'write_failed' -and $failed['error']) 'locked authoritative file returns a write failure rather than throwing'
        Check (-not $failed['legacy_export']['attempted'] -and $null -eq $failed['legacy_export']['succeeded']) 'legacy export does not run after authoritative write failure'
    }
    finally { $locked.Dispose() }
    Check ([IO.File]::ReadAllText($jsonPath) -eq $originalJson -and [IO.File]::ReadAllText($statePath) -eq $originalLegacy) 'authoritative write failure retains both prior files'

    $locked = [IO.File]::Open($statePath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try {
        $partial = Persist (Document ($validJson.Replace('"colour":1', '"colour":12')))
        Check ($partial['succeeded'] -and $partial['status'] -eq 'saved' -and $null -eq $partial['error']) 'legacy export failure does not undo authoritative success'
        Check ($partial['legacy_export']['attempted'] -and $partial['legacy_export']['succeeded'] -eq $false -and $partial['legacy_export']['status'] -eq 'write_failed' -and $partial['legacy_export']['error']) 'legacy export failure retains its own diagnostic'
    }
    finally { $locked.Dispose() }
    Check ([IO.File]::ReadAllText($jsonPath).Contains('"colour": 12') -and [IO.File]::ReadAllText($statePath) -eq $originalLegacy) 'legacy export failure leaves newly committed JSON authoritative'
    $committedJson = [IO.File]::ReadAllText($jsonPath)
    $empty = Document
    $empty.members.Clear()
    $emptyResult = Persist $empty
    Check ($emptyResult['succeeded'] -eq $false -and $emptyResult['status'] -eq 'empty_drawlist' -and $emptyResult['error']) 'empty snapshots report refusal to replace the previous state'
    $incomplete = Document
    $incomplete.members.Add($null)
    $incompleteResult = Persist $incomplete
    Check ($incompleteResult['succeeded'] -eq $false -and $incompleteResult['status'] -eq 'snapshot_failed' -and $incompleteResult['error']) 'incomplete snapshots cannot be committed'
    Check ([IO.File]::ReadAllText($jsonPath) -eq $committedJson) 'empty and incomplete snapshots retain previous JSON'
    Check ([IO.Directory]::GetFiles($scratch, '*.tmp').Count -eq 0) 'successful and failed atomic writes leave no temporary files'

    $primitiveJson = '{"owner_refno":"=1/1","refno":"=1/3","colour":0,"transparency":0,"edges":false,"visibility":true,"shallow":false}'
    $v2Empty = $validJson.Replace('"schema_version":1', '"schema_version":2').TrimEnd('}') + ',"primitive_entries":[]}'
    $v2 = $v2Empty.Replace('"primitive_entries":[]', '"primitive_entries":[' + $primitiveJson + ']')
    $v2Saved = Persist (Document $v2)
    Check ($v2Saved['succeeded'] -and $v2Saved['schema_version'] -eq 2 -and $v2Saved['primitive_entry_count'] -eq 1) 'schema 2 persists an observed primitive including native colour zero'
    Check ((Persist (Document $v2Empty))['succeeded']) 'schema 2 accepts a completely observed empty primitive collection'
    $v2Previous = [IO.File]::ReadAllText($jsonPath)
    $badV2 = @{
        'schema 2 rejects missing primitive collection' = $validJson.Replace('"schema_version":1', '"schema_version":2')
        'schema 1 rejects primitive-bearing documents' = $v2.Replace('"schema_version":2', '"schema_version":1')
        'schema 2 rejects unknown owner' = $v2.Replace('"owner_refno":"=1/1"', '"owner_refno":"=1/2"')
        'schema 2 rejects noncanonical primitive ref' = $v2.Replace('"refno":"=1/3"', '"refno":"=01/3"')
        'schema 2 rejects noncanonical member ref' = $v2.Replace('"refno":"=1/1"', '"refno":"=01/1"')
        'schema 2 rejects duplicate primitive entry' = $v2Empty.Replace('"primitive_entries":[]', '"primitive_entries":[' + $primitiveJson + ',' + $primitiveJson + ']')
        'unknown persistence version is rejected' = $v2.Replace('"schema_version":2', '"schema_version":3')
    }
    foreach ($name in $badV2.Keys) {
        $rejected = Persist (Document $badV2[$name])
        Check ($rejected['succeeded'] -eq $false -and $rejected['status'] -eq 'snapshot_failed' -and $rejected['error']) $name
        Check ([IO.File]::ReadAllText($jsonPath) -eq $v2Previous) ($name + ' retains previous file')
    }
    $aliasJson = '{"schema_version":2,"project":"TEST","mdb":"MDB","members":[{"refno":"=1/1","colour":1,"transparency":0,"edges":true,"visibility":true,"shallow":false},{"refno":"=1/2","colour":1,"transparency":0,"edges":true,"visibility":true,"shallow":false}],"primitive_entries":[' + $primitiveJson + ',' + $primitiveJson.Replace('"owner_refno":"=1/1"', '"owner_refno":"=1/2"') + ']}'
    Check ((Persist (Document $aliasJson))['succeeded']) 'consistent primitive aliases across owner containers are supported'
    $conflict = $aliasJson.Replace('"owner_refno":"=1/2","refno":"=1/3","colour":0', '"owner_refno":"=1/2","refno":"=1/3","colour":12')
    Check (-not (Persist (Document $conflict))['succeeded']) 'conflicting primitive aliases cannot be persisted'
    Reject 'missing primitive property is not invented during parsing' { Document ($v2.Replace('"owner_refno":"=1/1",', '')) } ([Newtonsoft.Json.JsonSerializationException])

    $report = [ordered]@{
        assembly = $assemblyFile
        mvid = $assembly.ManifestModule.ModuleVersionId.ToString()
        sha256 = (Get-FileHash -LiteralPath $assemblyFile -Algorithm SHA256).Hash
        checks_passed = $checks.Count
        checks = $checks
        live_e3d_calls = 0
    }
    $json = $report | ConvertTo-Json -Depth 5
    if ($ReportPath) { [IO.File]::WriteAllText($ReportPath, $json) }
    $json
}
finally {
    [DrawListTestColours]::Reset()
    foreach ($file in [IO.Directory]::GetFiles($scratch)) { [IO.File]::Delete($file) }
    [IO.Directory]::Delete($scratch, $false)
}
