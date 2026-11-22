param(
    [string]$AssemblyPath = (Join-Path $PSScriptRoot '..\..\bridge\e3d21\bin\Debug\net48\E3d21AiAddin.dll'),
    [string]$SdkPath = 'C:\Program Files (x86)\AVEVA\Everything3D2.10',
    [string]$ReportPath
)

# Only pure managed maps, request fingerprints and input validation are called.
# No E3D context constructor, Drawlist, DbElement or native setter is invoked.
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) {
    $runner = Join-Path $env:WINDIR 'SysWOW64\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath,
        '-AssemblyPath', $AssemblyPath, '-SdkPath', $SdkPath)
    if ($ReportPath) { $arguments += @('-ReportPath', $ReportPath) }
    & $runner @arguments
    if ($LASTEXITCODE -ne 0) { throw ('The x86 primitive visual contract checks failed with exit code ' + $LASTEXITCODE) }
    return
}
foreach ($dependency in @('Newtonsoft.Json.dll', 'Aveva.Core3D.Graphics.dll', 'Aveva.Core.Database.dll', 'Aveva.Core.Utilities.dll')) {
    [void][Reflection.Assembly]::LoadFrom((Join-Path $SdkPath $dependency))
}
$assemblyFile = (Resolve-Path -LiteralPath $AssemblyPath).Path
$assembly = [Reflection.Assembly]::LoadFrom($assemblyFile)
$contextType = $assembly.GetType('E3d21AiBridge.E3d21Context', $true)
$entryType = $assembly.GetType('E3d21AiBridge.E3d21Context+DrawListPrimitiveVisualEntry', $true)
$memberType = $assembly.GetType('E3d21AiBridge.E3d21Context+DrawListVisualEntry', $true)
$mapType = $assembly.GetType('E3d21AiBridge.E3d21Context+PrimitiveVisualMap', $true)
$stateType = $assembly.GetType('E3d21AiBridge.E3d21Context+PrimitiveVisualState', $true)
$targetType = $assembly.GetType('E3d21AiBridge.E3d21Context+PrimitiveVisualTarget', $true)
$staticFlags = [Reflection.BindingFlags]'Static,NonPublic'
$instanceFlags = [Reflection.BindingFlags]'Instance,Public,NonPublic'
$validate = $contextType.GetMethod('PrimitiveVisualValidateExpected', $staticFlags)
$compare = $contextType.GetMethod('CompareDrawListPrimitiveSnapshots', $staticFlags)
$revisionMethod = $contextType.GetMethod('PrimitiveVisualRevision', $instanceFlags)
$setter = $contextType.GetMethod('SetPrimitiveVisual')
$checks = [Collections.Generic.List[string]]::new()

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
    Check $true $name
}
function Field($value, [string]$name) {
    return ,$value.GetType().GetField($name, $instanceFlags).GetValue($value)
}
function Set-Field($value, [string]$name, $replacement) {
    $value.GetType().GetField($name, $instanceFlags).SetValue($value, $replacement)
}
function New-Map([bool]$reverse = $false) {
    $map = [Activator]::CreateInstance($mapType, $true)
    $members = Field $map 'Members'
    $entries = Field $map 'Entries'
    $memberJson = @(
        '{"refno":"=1/1","colour":0,"transparency":0,"edges":false,"visibility":true,"shallow":false}',
        '{"refno":"=1/2","colour":12,"transparency":0,"edges":true,"visibility":true,"shallow":false}')
    $entryJson = @(
        '{"owner_refno":"=1/1","refno":"=1/3","colour":0,"transparency":0,"edges":false,"visibility":true,"shallow":false}',
        '{"owner_refno":"=1/2","refno":"=1/3","colour":0,"transparency":0,"edges":false,"visibility":true,"shallow":false}',
        '{"owner_refno":"=1/1","refno":"=1/4","colour":12,"transparency":10,"edges":true,"visibility":false,"shallow":true}')
    if ($reverse) { [Array]::Reverse($memberJson); [Array]::Reverse($entryJson) }
    foreach ($json in $memberJson) {
        $row = [Newtonsoft.Json.JsonConvert]::DeserializeObject($json, $memberType)
        $members.Add($row.refno, $row)
    }
    foreach ($json in $entryJson) {
        $row = [Newtonsoft.Json.JsonConvert]::DeserializeObject($json, $entryType)
        $entries.Add(($row.owner_refno + '|' + $row.refno), $row)
    }
    return $map
}
function Revision([object[]]$arguments = $revisionArguments) {
    return $revisionMethod.Invoke($context, $arguments)
}

$map = New-Map
$entries = Field $map 'Entries'
$targets = $validate.Invoke($null, [object[]]@($entries))
Check ($entries.Count -eq 3 -and $targets.Count -eq 2) 'owner aliases remain distinct entries and select one native target per DB reference'
Check ($targets['=1/3'].colour -eq 0) 'native observed colour zero is preserved without inheritance interpretation'
Check (($compare.Invoke($null, [object[]]@($entries, $entries))).Count -eq 0) 'equal complete primitive maps compare equal'

foreach ($field in @('colour', 'transparency', 'edges', 'visibility', 'shallow')) {
    $changed = Field (New-Map) 'Entries'
    $row = $changed['=1/1|=1/4']
    if ($field -eq 'colour') { $row.colour = 511 }
    elseif ($field -eq 'transparency') { $row.transparency = 45 }
    else { Set-Field $row $field (-not (Field $row $field)) }
    $diff = $compare.Invoke($null, [object[]]@($entries, $changed))
    Check ($diff.Count -eq 1 -and $diff['=1/1|=1/4'].Contains($field)) ('complete comparison detects unrelated primitive ' + $field)
}
$missing = Field (New-Map) 'Entries'
[void]$missing.Remove('=1/2|=1/3')
Check (($compare.Invoke($null, [object[]]@($entries, $missing)))['=1/2|=1/3'] -like '*missing*') 'removing one target alias is not equivalent to keeping another alias'
Check (($compare.Invoke($null, [object[]]@($missing, $entries)))['=1/2|=1/3'] -like '*Unexpected*') 'extra primitive entries remain explicit residuals'

foreach ($field in @('colour', 'transparency', 'edges', 'visibility', 'shallow')) {
    $conflict = Field (New-Map) 'Entries'
    $row = $conflict['=1/2|=1/3']
    if ($field -eq 'colour') { $row.colour = 12 }
    elseif ($field -eq 'transparency') { $row.transparency = 1 }
    else { Set-Field $row $field (-not (Field $row $field)) }
    Reject ('conflicting owner aliases rejected for ' + $field) { $validate.Invoke($null, [object[]]@($conflict)) } ([InvalidOperationException])
}
$wrongKey = Field (New-Map) 'Entries'
$wrongKey['=1/1|=1/4'].owner_refno = '=1/2'
Reject 'entry owner and tuple key must agree' { $validate.Invoke($null, [object[]]@($wrongKey)) } ([InvalidOperationException])
$noncanonical = Field (New-Map) 'Entries'
$noncanonical['=1/1|=1/4'].refno = '=01/4'
Reject 'noncanonical numeric identity is rejected' { $validate.Invoke($null, [object[]]@($noncanonical)) } ([InvalidOperationException])
$badTransparency = Field (New-Map) 'Entries'
$badTransparency['=1/1|=1/4'].transparency = -1
Reject 'invalid observed transparency is rejected before apply' { $validate.Invoke($null, [object[]]@($badTransparency)) } ([InvalidOperationException])

# These types are data containers. DrawList remains null and context is allocated
# without its live event-subscribing constructor; only pure hashing is invoked.
$context = [Runtime.Serialization.FormatterServices]::GetUninitializedObject($contextType)
Set-Field $context 'primitiveVisualRevisionSession' 'session-a'
$state = [Activator]::CreateInstance($stateType, $true)
Set-Field $state 'Project' 'TEST'
Set-Field $state 'Mdb' 'MDB'
Set-Field $state 'DrawListId' 7
Set-Field $state 'Map' $map
$target = [Activator]::CreateInstance($targetType, $true)
Set-Field $target 'Reference' '=1/3'
Set-Field $target 'Type' 'CYLI'
Set-Field $target 'OwnerReference' '=1/2'
Set-Field $target 'ResolvedMemberReference' '=1/2'
Set-Field $target 'Keys' ([string[]]@('=1/1|=1/3', '=1/2|=1/3'))
$revisionArguments = [object[]]@($state, $target, '=1/3', $null, $null, [int]10, $null, $null, $null)
$baseline = Revision
Check ($baseline -match '^[0-9a-f]{64}$' -and (Revision) -eq $baseline) 'complete request revision is deterministic SHA256'
Set-Field $state 'Map' (New-Map $true)
Check ((Revision) -eq $baseline) 'dictionary enumeration order does not change a revision'
Set-Field $state 'Map' $map

foreach ($case in @(
    @{ Index = 2; Value = '/SAME-TARGET'; Name = 'requested target spelling' },
    @{ Index = 3; Value = 'Blue'; Name = 'requested colour text' },
    @{ Index = 4; Value = [int]12; Name = 'resolved colour index' },
    @{ Index = 5; Value = [int]11; Name = 'requested transparency' },
    @{ Index = 6; Value = $false; Name = 'explicit false edges versus omitted' },
    @{ Index = 7; Value = $false; Name = 'explicit false visibility versus omitted' },
    @{ Index = 8; Value = $false; Name = 'explicit false shallow versus omitted' }
)) {
    $arguments = [object[]]$revisionArguments.Clone()
    $arguments[$case.Index] = $case.Value
    Check ((Revision $arguments) -ne $baseline) ('revision binds ' + $case.Name)
}
foreach ($case in @(
    @{ Value = $context; Field = 'primitiveVisualRevisionSession'; Changed = 'session-b' },
    @{ Value = $state; Field = 'Project'; Changed = 'OTHER' },
    @{ Value = $state; Field = 'Mdb'; Changed = 'OTHER' },
    @{ Value = $state; Field = 'DrawListId'; Changed = [int]8 },
    @{ Value = $target; Field = 'Reference'; Changed = '=1/4' },
    @{ Value = $target; Field = 'Type'; Changed = 'BOX' },
    @{ Value = $target; Field = 'OwnerReference'; Changed = '=1/1' },
    @{ Value = $target; Field = 'ResolvedMemberReference'; Changed = '=1/1' }
)) {
    $original = Field $case.Value $case.Field
    Set-Field $case.Value $case.Field $case.Changed
    Check ((Revision) -ne $baseline) ('revision binds ' + $case.Field)
    Set-Field $case.Value $case.Field $original
}
foreach ($field in @('colour', 'transparency', 'edges', 'visibility', 'shallow')) {
    $changedMap = New-Map
    $row = (Field $changedMap 'Entries')['=1/1|=1/4']
    if ($field -eq 'colour') { $row.colour = 511 }
    elseif ($field -eq 'transparency') { $row.transparency = 40 }
    else { Set-Field $row $field (-not (Field $row $field)) }
    Set-Field $state 'Map' $changedMap
    Check ((Revision) -ne $baseline) ('revision invalidates when a non-target primitive changes ' + $field)
}
$changedMap = New-Map
(Field $changedMap 'Members')['=1/1'].colour = 12
Set-Field $state 'Map' $changedMap
Check ((Revision) -ne $baseline) 'revision invalidates when a top-level member observation changes'
$changedMap = New-Map
[void](Field $changedMap 'Entries').Remove('=1/2|=1/3')
Set-Field $state 'Map' $changedMap
Check ((Revision) -ne $baseline) 'revision invalidates when one native alias disappears'
Set-Field $state 'Map' $map
Check ((Revision) -eq $baseline) 'restoring the full request and maps restores their deterministic revision'

# commandLock is intentionally uninitialized. Each rejection below must happen
# before command dispatch, UI invocation, current-element access or native reads.
Reject 'execution without a preview revision rejects before SDK access' {
    $setter.Invoke($context, [object[]]@('=1/3', $null, $null, [int]10, $null, $null, $null, $false))
} ([ArgumentException])
Reject 'malformed revision rejects before SDK access' {
    $setter.Invoke($context, [object[]]@('=1/3', 'bad-revision', $null, [int]10, $null, $null, $null, $true))
} ([ArgumentException])
Reject 'invalid transparency rejects before SDK access' {
    $setter.Invoke($context, [object[]]@('=1/3', $null, $null, [int]101, $null, $null, $null, $true))
} ([ArgumentOutOfRangeException])
Reject 'implicit current element is never a primitive target' {
    $setter.Invoke($context, [object[]]@('', $null, $null, [int]10, $null, $null, $null, $true))
} ([ArgumentException])

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
