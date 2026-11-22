using System;
using System.Collections.Generic;
using E3d21AiBridge;

// Compiled only by test_cancel_completion.py with actual production methods.
// The managed fakes never load AVEVA assemblies or invoke Windows UI APIs.
internal sealed class CancelCompletionHarness
{
    private readonly Action<Action> postOnUi;
    private readonly E3d21PmlAlerts pmlAlerts = new E3d21PmlAlerts();
    private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
    private static int checks, beginCalls, endCalls, stateReads, abortCalls, eventCalls, dismissalCalls, pmlRuns;
    private static bool failAbort, failEvent, pmlSuccess;
    private static bool busy;

    private CancelCompletionHarness(Action<Action> dispatcher) { postOnUi = dispatcher; }

    private static void Reset()
    {
        beginCalls = endCalls = stateReads = abortCalls = eventCalls = dismissalCalls = pmlRuns = 0;
        failAbort = failEvent = false;
        busy = pmlSuccess = true;
    }

    private static void Check(bool condition, string description)
    {
        checks++;
        if (!condition) throw new Exception(description);
    }

    private static void Unconfirmed(Dictionary<string, object> result, string status, bool queued)
    {
        Check(Equals(result["success"], false), "Dispatch cannot establish success.");
        Check(Equals(result["completion_confirmed"], false), "Only observed completion may set completion_confirmed.");
        Check(Equals(result["requested"], true) && Equals(result["ui_cancel_queued"], queued), "Request and queue diagnostics must remain distinct.");
        Check(Equals(result["status"], status), "The request must expose pending or failed dispatch.");
        Check(result.ContainsKey("previous_state") && result.ContainsKey("native_dialog_dismissal"), "Observation diagnostics must survive.");
    }

    private static int Main()
    {
        try
        {
            TestQueuedAliases();
            TestMissingOrFailingDispatcher();
            TestCallbackBoundaries();
            TestNativeFault();
            TestPreviewAndNormalPml();
            Console.WriteLine("Cancellation completion checks passed: " + checks);
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }

    private static void TestQueuedAliases()
    {
        foreach (string alias in new[] { "CANCEL", "ABORT", "ESC", "ESCAPE", "^CANCEL", " cancel " })
        {
            Reset();
            Action queued = null;
            var bridge = new CancelCompletionHarness(action => queued = action);
            var result = (Dictionary<string, object>)bridge.ExecutePml(alias, false);
            Unconfirmed(result, "pending", true);
            Check(queued != null && abortCalls == 0 && eventCalls == 0, "A queued callback must not be described as executed.");
            Check(beginCalls == 0 && pmlRuns == 0 && Equals(result["command"], alias), "Aliases use cancellation and retain the requested alias.");
            Check(Equals(result["dry_run"], false), "An executed cancellation request is not a preview.");
            queued();
            Check(abortCalls == 1 && eventCalls == 1 && busy, "Posting CANCEL does not prove the command is no longer busy.");
            Unconfirmed(result, "pending", true);
        }
    }

    private static void TestMissingOrFailingDispatcher()
    {
        foreach (Action<Action> dispatcher in new Action<Action>[] { null, action => { throw new InvalidOperationException("queue rejected"); } })
        {
            Reset();
            var bridge = new CancelCompletionHarness(dispatcher);
            var result = (Dictionary<string, object>)bridge.ExecutePml("CANCEL", false);
            Unconfirmed(result, "failed", false);
            Check(result.ContainsKey("error") && abortCalls == 0 && eventCalls == 0, "Dispatcher rejection must remain a failure without cleanup.");
            Check(dismissalCalls == 1, "Existing native dialog diagnostics are preserved even if UI dispatch fails.");
        }
    }

    private static void TestCallbackBoundaries()
    {
        foreach (string callback in new[] { "succeeds", "abort throws", "event throws" })
        {
            Reset();
            failAbort = callback == "abort throws";
            failEvent = callback == "event throws";
            var bridge = new CancelCompletionHarness(action => action());
            var result = (Dictionary<string, object>)bridge.ExecutePml("ABORT", false);
            Unconfirmed(result, "pending", true);
            Check(abortCalls == 1 && eventCalls == (failAbort ? 0 : 1), "Callback failures must not invent execution of the remaining cleanup.");
            Check(stateReads == 1 && busy, "A returned callback has not observed completion after the cancellation request.");
        }
        Reset();
        busy = false;
        var idle = new CancelCompletionHarness(action => action());
        Unconfirmed((Dictionary<string, object>)idle.CancelCommand(), "pending", true);
        Check(stateReads == 1, "Pre-request idle state cannot prove completion of a later cancellation request.");
    }

    private static void TestNativeFault()
    {
        Reset();
        var bridge = new CancelCompletionHarness(action => action());
        bridge.nativeFaultState.Observe("Unhandled Database error 538 from fake", "test");
        var result = (Dictionary<string, object>)bridge.ExecutePml("CANCEL", false);
        Unconfirmed(result, "pending", true);
        Check(abortCalls == 0 && eventCalls == 0 && bridge.nativeFaultState.HasFault, "A latched fault must skip interaction cleanup and remain latched.");
        var state = (Dictionary<string, object>)result["previous_state"];
        Check(Equals(state["recovery_required"], true), "Native fault diagnostics survive the alias response.");
    }

    private static void TestPreviewAndNormalPml()
    {
        Reset();
        var bridge = new CancelCompletionHarness(action => { throw new Exception("Preview must never dispatch cancellation."); });
        var preview = (Dictionary<string, object>)bridge.ExecutePml("CANCEL", true);
        Check(Equals(preview["dry_run"], true) && stateReads == 0 && dismissalCalls == 0, "Preview cannot dispatch or dismiss dialogs.");
        foreach (bool success in new[] { true, false })
        {
            pmlSuccess = success;
            var executed = (Dictionary<string, object>)bridge.ExecutePml("$P TEST", false);
            Check(Equals(executed["success"], success), "Ordinary commands retain the actual nested Run result.");
        }
        Check(pmlRuns == 2 && beginCalls == 2 && endCalls == 2 && dismissalCalls == 0, "Only ordinary PML uses the guarded command execution path.");
    }

    private Dictionary<string, object> CommandState()
    {
        stateReads++;
        return new Dictionary<string, object> { ["busy"] = busy, ["recovery_required"] = nativeFaultState.HasFault,
            ["native_database_fault"] = nativeFaultState.Snapshot() };
    }
    private static bool TryBeginCommand(string command) { beginCalls++; return true; }
    private static void EndCommand() { endCalls++; }
    private static T OnUi<T>(Func<T> action, bool allowNativeFault = false) { return action(); }
    private static long MessageSequence() { return 0; }
    private static object CurrentElement() { return new object(); }
    private static Dictionary<string, object> ValidateCommand(string command, object element) { return null; }
    private static void ThrowIfNativeFaulted() { }
    private static string SafeString(Func<string> read) { return read(); }
    private static string MessageText(string message) { return message; }
    private static void AddDiagnostics(Dictionary<string, object> result, long mark) { }
    private static DocEngine CurrentInteractionEngine() { return new DocEngine(); }

    private sealed class PmlCommand
    {
        internal static PmlCommand CreateCommand(string command) { return new PmlCommand(); }
        internal bool Run() { pmlRuns++; return pmlSuccess; }
        internal bool RunInCurrentScope() { pmlRuns++; return pmlSuccess; }
        internal string Result { get { return "native result"; } }
        internal string Error { get { return pmlSuccess ? "" : "native failure"; } }
    }
    private sealed class DatalListing
    {
        internal static DatalListing Instance { get { return new DatalListing(); } }
        internal void inputAbort() { abortCalls++; if (failAbort) throw new InvalidOperationException("abort failed"); }
    }
    private sealed class DocEngine
    {
        internal void PostEvent(StEvent value) { eventCalls++; if (failEvent) throw new InvalidOperationException("event failed"); }
    }
    private sealed class StEvent
    {
        internal enum EventType { CANCEL }
        internal StEvent(EventType type, object value) { }
    }
    private static class E3d21NativeDialogs
    {
        internal static object DismissKnownErrors()
        {
            dismissalCalls++;
            return new Dictionary<string, object> { ["attempted"] = false, ["complete"] = true };
        }
    }

    private sealed class E3d21PmlAlerts
    {
        internal object DismissKnownErrors()
        {
            return new Dictionary<string, object> { ["requested"] = false, ["input_stack_cleared"] = null };
        }
    }

    // PRODUCTION_METHODS
}
