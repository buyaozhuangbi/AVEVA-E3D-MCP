using System;
using System.Collections.Generic;
using System.Linq;
using E3d21AiBridge;

// Executes extracted production entry points with recording command objects.
// It checks routing and result/control-flow contracts, not native parser scope,
// input-stack cleanup, real variable lifetime, or rollback semantics.
internal sealed class PmlExecutionScopeHarness
{
    private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
    private static readonly List<string> calls = new List<string>();
    private static readonly List<string> effects = new List<string>();
    private static int checks, creates, begins, ends, dispatches, cancels;
    private static bool busy;

    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) throw new Exception(message);
    }

    private static PmlExecutionScopeHarness Reset()
    {
        calls.Clear();
        effects.Clear();
        creates = begins = ends = dispatches = cancels = 0;
        busy = false;
        return new PmlExecutionScopeHarness();
    }

    private static Dictionary<string, object> Row(object value) { return (Dictionary<string, object>)value; }

    private static void Scope(Dictionary<string, object> row, string expected, bool success)
    {
        Check(Equals(row["execution_scope"], expected), "The response must identify the actual execution scope.");
        Check(Equals(row["success"], success) && Equals(row["dry_run"], false), "Raw success/preview status must survive.");
    }

    private static void SingleRoutes()
    {
        foreach (string command in new[] { "$P TEST", "!local = 1\n!!global = !local", "fail", "DESIGN\n", "SAVEWORK\r\n", "DESIGN; $P TEST" })
        {
            var bridge = Reset();
            var row = Row(bridge.ExecutePml(command, false));
            Scope(row, "nested", command != "fail");
            Check(calls.SequenceEqual(new[] { "nested:" + command }), "Ordinary scripts run unchanged as one nested command.");
            Check(creates == 1 && begins == 1 && ends == 1 && dispatches == 1, "One single request owns one command execution.");
            Check(Equals(row["result"], "result:" + command) && Equals(row["error"], command == "fail" ? "native failure" : ""), "Native error/result are not replaced.");
            Check(effects.SequenceEqual(new[] { command }), "Failed status does not erase effects or replay a command.");
        }
        foreach (string command in new[] { "SAVEWORK", "DESIGN", " design ", "savework" })
        {
            var bridge = Reset();
            var row = Row(bridge.ExecutePml(command, false));
            Scope(row, "current", true);
            Check(calls.SequenceEqual(new[] { "current:" + command }), "Only exact recovery commands retain the tested current scope.");
            Check(Equals(row["result"], "") && Equals(row["error"], ""), "Recovery keeps its existing post-execution observation contract.");
        }
    }

    private static void BatchRoutes()
    {
        var bridge = Reset();
        string[] commands = { "!a = 1\n!!b = !a", "SAVEWORK", "DESIGN", "!!b = 2" };
        IList<object> rows = bridge.ExecutePmlBatch(commands, false, true);
        Check(rows.Count == commands.Length, "Successful batch retains every item.");
        Check(calls.SequenceEqual(new[] { "nested:" + commands[0], "current:SAVEWORK", "current:DESIGN", "nested:!!b = 2" }), "Batch chooses scope per complete item, without joining or splitting scripts.");
        for (int index = 0; index < commands.Length; index++)
        {
            Scope(Row(rows[index]), index == 1 || index == 2 ? "current" : "nested", true);
            Check(Equals(Row(rows[index])["command"], commands[index]), "Batch input identity is preserved.");
        }
        Check(creates == 4 && begins == 1 && ends == 1 && dispatches == 1, "A batch remains one UI callback with separate command objects.");

        foreach (bool stop in new[] { true, false })
        {
            bridge = Reset();
            rows = bridge.ExecutePmlBatch(new[] { "first", "fail", "last" }, false, stop);
            int count = stop ? 2 : 3;
            Check(rows.Count == count && creates == count && calls.Count == count, "stopOnError controls later batch items after returned failure.");
            Check(effects.SequenceEqual(stop ? new[] { "first", "fail" } : new[] { "first", "fail", "last" }), "Earlier effects remain; no rollback or automatic replay is introduced.");
            Scope(Row(rows[1]), "nested", false);
            Check(Equals(Row(rows[1])["error"], "native failure"), "Failed item preserves the original diagnostic.");
            Check(ends == 1, "Command guard is released after an ordinary returned failure.");
        }

        foreach (bool stop in new[] { true, false })
        {
            bridge = Reset();
            rows = bridge.ExecutePmlBatch(new[] { "first", "throws", "last" }, false, stop);
            Check(rows.Count == 2 && calls.SequenceEqual(new[] { "nested:first", "nested:throws" }), "An SDK exception stops the batch regardless of stopOnError.");
            Check(Equals(Row(rows[1])["outcome_uncertain"], true), "An exception retains unknown outcome.");
            Check(effects.SequenceEqual(new[] { "first", "throws" }) && ends == 1, "Possible failed-command effects are preserved without retry.");
        }
    }

    private static void PreviewAndBusy()
    {
        var bridge = Reset();
        var preview = Row(bridge.ExecutePml("!a = 1\n!!b = !a", true));
        var previews = bridge.ExecutePmlBatch(new[] { "first", "fail", "SAVEWORK" }, true, true);
        Check(Equals(preview["dry_run"], true) && previews.Count == 3, "Previews return without native evaluation.");
        Check(creates == 0 && begins == 0 && dispatches == 0 && calls.Count == 0 && effects.Count == 0, "Preview cannot create, execute or queue native commands.");
        Check(!preview.ContainsKey("execution_scope") && previews.All(value => !Row(value).ContainsKey("execution_scope")), "Preview does not claim an executed scope.");

        bridge = Reset();
        busy = true;
        var rejected = Row(bridge.ExecutePml("first", false));
        var rejectedBatch = bridge.ExecutePmlBatch(new[] { "first" }, false, true);
        Check(Equals(rejected["busy"], true) && Equals(Row(rejectedBatch[0])["busy"], true), "Busy state rejects both entry points.");
        Check(creates == 0 && dispatches == 0 && calls.Count == 0, "Busy commands do not enter either native scope.");

        bridge = Reset();
        Row(bridge.ExecutePml("CANCEL", false));
        Check(cancels == 1 && calls.Count == 0 && creates == 0 && dispatches == 0, "Cancellation still bypasses ordinary PML execution.");
    }

    private static int Main()
    {
        try
        {
            SingleRoutes();
            BatchRoutes();
            PreviewAndBusy();
            Console.WriteLine("PML execution scope checks passed: " + checks);
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }

    private static bool TryBeginCommand(string command) { begins++; return !busy; }
    private static void EndCommand() { ends++; }
    private static T OnUi<T>(Func<T> action, bool allowNativeFault = false) { dispatches++; return action(); }
    private static long MessageSequence() { return 0; }
    private static object CurrentElement() { return new object(); }
    private static Dictionary<string, object> ValidateCommand(string command, object element) { return null; }
    private static void ThrowIfNativeFaulted() { }
    private static string SafeString(Func<string> read) { return read(); }
    private static string MessageText(string message) { return message; }
    private static void AddDiagnostics(Dictionary<string, object> result, long mark) { }
    private static object CancelCommand() { cancels++; return new Dictionary<string, object>(); }

    private sealed class PmlCommand
    {
        private readonly string command;
        private PmlCommand(string value) { command = value; }
        internal static PmlCommand CreateCommand(string command) { creates++; return new PmlCommand(command); }
        private bool Record(string scope)
        {
            calls.Add(scope + ":" + command);
            effects.Add(command);
            if (command == "throws") throw new InvalidOperationException("native invocation failed");
            return command != "fail";
        }
        internal bool Run() { return Record("nested"); }
        internal bool RunInCurrentScope() { return Record("current"); }
        internal string Result { get { return "result:" + command; } }
        internal string Error { get { return command == "fail" ? "native failure" : ""; } }
    }

    // PRODUCTION_METHODS
}
