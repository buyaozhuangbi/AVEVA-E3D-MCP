param(
    [string]$AssemblyPath = (Join-Path $PSScriptRoot '..\..\bridge\e3d21\bin\Debug\net48\E3d21AiAddin.dll'),
    [string]$SdkPath = 'C:\Program Files (x86)\AVEVA\Everything3D2.10',
    [string]$ReportPath
)

# Pure policy and structured-diagnostic tests. Inspect/DismissKnownErrors are
# deliberately never invoked: no window scan, message, UI action or SDK call.
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) {
    $runner = Join-Path $env:WINDIR 'SysWOW64\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath,
        '-AssemblyPath', $AssemblyPath, '-SdkPath', $SdkPath)
    if ($ReportPath) { $arguments += @('-ReportPath', $ReportPath) }
    & $runner @arguments
    if ($LASTEXITCODE -ne 0) { throw ('The x86 native-dialog checks failed with exit code ' + $LASTEXITCODE) }
    return
}
[void][Reflection.Assembly]::LoadFrom((Join-Path $SdkPath 'Newtonsoft.Json.dll'))
$assemblyFile = (Resolve-Path -LiteralPath $AssemblyPath).Path
$assembly = [Reflection.Assembly]::LoadFrom($assemblyFile)
$dialogs = $assembly.GetType('E3d21AiBridge.E3d21NativeDialogs', $true)
$adapter = $assembly.GetType('E3d21AiBridge.E3d21HttpAdapter', $true)
$flags = [Reflection.BindingFlags]'Static,NonPublic'
$exactError = $dialogs.GetMethod('ExactDatabaseError', $flags)
$knownTitle = $dialogs.GetMethod('KnownTitle', $flags)
$knownButton = $dialogs.GetMethod('KnownButton', $flags)
$sanitize = $adapter.GetMethod('SanitizeDiagnostics', $flags)
$checks = [Collections.Generic.List[string]]::new()
function Check([bool]$condition, [string]$name) {
    if (-not $condition) { throw ('Check failed: ' + $name) }
    $checks.Add($name)
}
function Button([string]$caption, [int]$identifier = 1, [string]$className = 'Button', [bool]$complete = $true) {
    $value = [Collections.Generic.Dictionary[string,object]]::new()
    $value.Add('class', $className)
    $value.Add('read_complete', $complete)
    $value.Add('control_id', $identifier)
    $value.Add('text', $caption)
    return ,$value
}
foreach ($value in @('Database error 538', '  Database error 538  ', 'Unhandled Database error 538')) {
    Check ($exactError.Invoke($null, [object[]]@($value))) ('known database error caption recognized: ' + $value.Trim())
}
foreach ($value in @('Database error 538; execute command', 'not Database error 538', "Database error 538`nAdditional action", 'Database error', 'Database error -1', 'Database error 538X', '', $null)) {
    Check (-not $exactError.Invoke($null, [object[]]@($value))) 'unrecognized or mixed error caption is not dismissible'
}
Check ($knownTitle.Invoke($null, [object[]]@('AVEVA Everything3D Model'))) 'AVEVA standard error dialog title is recognized'
Check (-not $knownTitle.Invoke($null, [object[]]@('Other Application Error'))) 'unrelated application title is rejected'
Check ($knownButton.Invoke($null, [object[]]@((Button '&OK'), [int]1))) 'verified IDOK and OK caption are accepted'
Check ($knownButton.Invoke($null, [object[]]@((Button 'Cancel' 2), [int]2))) 'verified IDCANCEL and Cancel caption are accepted'
foreach ($caption in @('Relaunch', 'Save Support Info...', 'Save', 'Yes', 'No', 'Cancel')) {
    Check (-not $knownButton.Invoke($null, [object[]]@((Button $caption), [int]1))) ('IDOK with unsafe or mismatched caption is rejected: ' + $caption)
}
Check (-not $knownButton.Invoke($null, [object[]]@((Button 'OK' 99), [int]1))) 'arbitrary button identifiers are rejected'
Check (-not $knownButton.Invoke($null, [object[]]@((Button 'OK' 1 'Edit'), [int]1))) 'Edit controls cannot become button targets'
Check (-not $knownButton.Invoke($null, [object[]]@((Button 'OK' 1 'Button' $false), [int]1))) 'incomplete button text is rejected'

$inputState = [Newtonsoft.Json.Linq.JObject]::Parse('{"busy":true,"command":"LOGIN USER secret-password","execution":{"dispatcher_ready":true,"native_operation_active":true,"ui_thread_id":77},"native_database_fault":{"has_fault":true,"code":538,"routine":"db_cxx_get_reference"},"nested":[{"password":"do-not-output","api_key":"do-not-output","authorization":"do-not-output"}]}')
$before = $inputState.ToString()
$sanitizeArguments = New-Object object[] 1
$sanitizeArguments[0] = $inputState
$safe = $sanitize.Invoke($null, $sanitizeArguments)
Check ($safe['command'].ToString() -eq '[active command]') 'HTTP diagnostics do not copy active PML text'
Check (-not $safe.ToString().Contains('secret-password') -and -not $safe.ToString().Contains('do-not-output')) 'credential-labelled structured fields are redacted recursively'
Check ($safe['native_database_fault']['code'].ToObject([int]) -eq 538 -and $safe['execution']['ui_thread_id'].ToObject([int]) -eq 77) 'structured native fault and dispatcher evidence survive sanitization'
Check ($inputState.ToString() -eq $before) 'sanitization does not mutate command or fault state'
$idleArguments = New-Object object[] 1
$idleArguments[0] = [Newtonsoft.Json.Linq.JObject]::Parse('{"command":"","busy":false}')
$idle = $sanitize.Invoke($null, $idleArguments)
Check ($idle['command'].ToString() -eq '') 'idle diagnostics retain an empty command'

$report = [ordered]@{
    assembly = $assemblyFile
    mvid = $assembly.ManifestModule.ModuleVersionId.ToString()
    sha256 = (Get-FileHash -LiteralPath $assemblyFile -Algorithm SHA256).Hash
    checks_passed = $checks.Count
    checks = $checks
    live_e3d_calls = 0
    window_scans = 0
    posted_messages = 0
}
$json = $report | ConvertTo-Json -Depth 5
if ($ReportPath) { [IO.File]::WriteAllText($ReportPath, $json) }
$json
