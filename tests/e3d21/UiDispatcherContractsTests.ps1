param(
    [string]$AssemblyPath = (Join-Path $PSScriptRoot '..\..\bridge\e3d21\bin\Debug\net48\E3d21AiAddin.dll'),
    [string]$SdkPath = 'C:\Program Files (x86)\AVEVA\Everything3D2.10',
    [string]$ReportPath
)

# Fake dispatch delegates exercise gate ownership and delayed callbacks. No
# live E3D window, UI callback, SDK initialization, or native action is used.
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) {
    $runner = Join-Path $env:WINDIR 'SysWOW64\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath,
        '-AssemblyPath', $AssemblyPath, '-SdkPath', $SdkPath)
    if ($ReportPath) { $arguments += @('-ReportPath', $ReportPath) }
    & $runner @arguments
    if ($LASTEXITCODE -ne 0) { throw ('The x86 dispatcher checks failed with exit code ' + $LASTEXITCODE) }
    return
}
foreach ($dependency in @('Aveva.ApplicationFramework.dll', 'Aveva.ApplicationFramework.Presentation.dll')) {
    [void][Reflection.Assembly]::LoadFrom((Join-Path $SdkPath $dependency))
}
$assemblyFile = (Resolve-Path -LiteralPath $AssemblyPath).Path
$assembly = [Reflection.Assembly]::LoadFrom($assemblyFile)
$addinType = $assembly.GetType('E3d21AiBridge.E3d21AiAddin', $true)

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.Serialization;
using System.Threading;
using System.Threading.Tasks;

public static class UiDispatcherContractRunner
{
    private static Type addinType;
    private static MethodInfo runMethod;
    private static FieldInfo gateField;
    private static readonly List<string> checks = new List<string>();
    private const BindingFlags PrivateInstance = BindingFlags.Instance | BindingFlags.NonPublic;

    private static object Addin() { return FormatterServices.GetUninitializedObject(addinType); }
    private static int Active(object addin) { return (int)gateField.GetValue(addin); }
    private static void Check(bool value, string name)
    {
        if (!value) throw new Exception("Check failed: " + name);
        checks.Add(name);
    }
    private static void Invoke(MethodInfo method, object addin, params object[] arguments)
    {
        try { method.Invoke(addin, arguments); }
        catch (TargetInvocationException error) { throw error.InnerException; }
    }
    private static void Run(object addin, Action action, Action<Action> dispatch, Action verify)
    {
        Invoke(runMethod, addin, action, dispatch, verify);
    }
    private static void Reject(Action operation, string name)
    {
        bool rejected = false;
        try { operation(); }
        catch (InvalidOperationException) { rejected = true; }
        Check(rejected, name);
    }
    private static void RunningCallbackFailure(bool dispatchThrows)
    {
        object addin = Addin();
        using (var entered = new ManualResetEventSlim(false))
        using (var release = new ManualResetEventSlim(false))
        using (var exited = new ManualResetEventSlim(false))
        {
            Task worker = null;
            int invoked = 0;
            try
            {
                Reject(delegate
                {
                    Run(addin, delegate
                    {
                        Interlocked.Increment(ref invoked);
                        entered.Set();
                        if (!release.Wait(5000)) throw new Exception("Fake action release timed out.");
                    }, delegate(Action callback)
                    {
                        worker = Task.Run(delegate { try { callback(); } finally { exited.Set(); } });
                        if (!entered.Wait(5000)) throw new Exception("Fake callback did not start.");
                        if (dispatchThrows) throw new InvalidOperationException("Fake dispatcher failed after callback started.");
                        // Deliberately return while callback is still running.
                    }, delegate { });
                }, dispatchThrows ? "dispatch failure after callback start is reported" : "early dispatcher return is reported");
                Check(Active(addin) == 1 && invoked == 1,
                    dispatchThrows ? "dispatch exception does not release a running callback gate" : "early return does not release a running callback gate");
                Reject(delegate { Run(addin, delegate { throw new Exception("Concurrent action ran."); }, callback => callback(), delegate { }); },
                    dispatchThrows ? "concurrent action rejected after dispatch exception" : "concurrent action rejected after early return");
            }
            finally
            {
                release.Set();
                if (worker != null)
                {
                    if (!exited.Wait(5000)) throw new Exception("Fake callback did not exit.");
                    worker.GetAwaiter().GetResult();
                }
            }
            Check(Active(addin) == 0, dispatchThrows ? "callback exit releases gate after dispatch exception" : "callback exit releases gate after early return");
        }
    }

    public static string[] Execute(Type type)
    {
        addinType = type;
        runMethod = type.GetMethod("RunDispatchedAction", PrivateInstance);
        gateField = type.GetField("nativeOperationActive", PrivateInstance);
        if (runMethod == null || gateField == null) throw new Exception("The built assembly does not contain the explicit dispatcher.");
        object addin = Addin();
        int actionCount = 0;
        bool queuedWithGate = false, verifiedWithGate = false, actionWithGate = false;
        Run(addin, delegate { actionWithGate = Active(addin) == 1; actionCount++; },
            delegate(Action callback) { queuedWithGate = Active(addin) == 1; callback(); },
            delegate { verifiedWithGate = Active(addin) == 1; });
        Check(queuedWithGate && verifiedWithGate && actionWithGate && actionCount == 1 && Active(addin) == 0,
            "one gate covers prequeue, callback verification and action until completion");

        Run(addin, delegate
        {
            Reject(delegate { Run(addin, delegate { actionCount++; }, callback => callback(), delegate { }); },
                "nested SDK invocation is rejected before its action");
        }, callback => callback(), delegate { });
        Check(actionCount == 1 && Active(addin) == 0, "nested rejection preserves outer ownership and releases on outer completion");

        Reject(delegate
        {
            Run(addin, delegate { actionCount++; }, callback => callback(),
                delegate { throw new InvalidOperationException("Fake wrong-owner-thread verification."); });
        }, "invalid callback verification rejects the SDK action");
        Check(actionCount == 1 && Active(addin) == 0, "verification failure does not run action or retain gate");

        Reject(delegate
        {
            Run(addin, delegate { throw new InvalidOperationException("Fake SDK action error."); }, callback => callback(), delegate { });
        }, "action error reaches the caller");
        Check(Active(addin) == 0, "throwing action releases gate only in callback finally");

        Action delayed = null;
        Reject(delegate
        {
            Run(addin, delegate { actionCount++; }, delegate(Action callback)
            {
                delayed = callback;
                throw new InvalidOperationException("Fake queue failure.");
            }, delegate { });
        }, "queue failure before callback start is reported");
        Check(Active(addin) == 0, "queue failure cancels pending token and releases gate");
        delayed();
        Check(actionCount == 1 && Active(addin) == 0, "callback retained after failed queue cannot execute later");

        Reject(delegate
        {
            Run(addin, delegate { actionCount++; }, callback => { delayed = callback; }, delegate { });
        }, "return without starting callback fails closed");
        delayed();
        Check(actionCount == 1 && Active(addin) == 0, "never-started asynchronous callback is cancelled before gate release");

        RunningCallbackFailure(false);
        RunningCallbackFailure(true);

        Reject(delegate
        {
            Invoke(type.GetMethod("InvokeOnUi", PrivateInstance), addin, new Action(delegate { actionCount++; }));
        }, "missing real dispatcher never falls back to caller thread");
        Reject(delegate
        {
            Invoke(type.GetMethod("PostOnUi", PrivateInstance), addin, new Action(delegate { actionCount++; }));
        }, "missing cancellation dispatcher never runs inline");
        Check(actionCount == 1 && Active(addin) == 0, "unbound Invoke and Post do not execute SDK actions");
        var state = (Dictionary<string, object>)type.GetMethod("ExecutionState", PrivateInstance).Invoke(addin, null);
        Check((bool)state["dispatcher_ready"] == false && (bool)state["native_operation_active"] == false &&
            Convert.ToUInt32(state["ui_thread_id"]) == 0 && Convert.ToUInt32(state["last_callback_thread_id"]) == 0 &&
            Convert.ToInt32(state["dispatch_count"]) == 0,
            "unbound execution state is readable without SDK or a UI callback");
        return checks.ToArray();
    }
}
'@

$checks = [UiDispatcherContractRunner]::Execute($addinType)
$report = [ordered]@{
    assembly = $assemblyFile
    mvid = $assembly.ManifestModule.ModuleVersionId.ToString()
    sha256 = (Get-FileHash -LiteralPath $assemblyFile -Algorithm SHA256).Hash
    checks_passed = $checks.Count
    checks = $checks
    live_e3d_calls = 0
    actual_ui_callbacks = 0
}
$json = $report | ConvertTo-Json -Depth 5
if ($ReportPath) { [IO.File]::WriteAllText($ReportPath, $json) }
$json
