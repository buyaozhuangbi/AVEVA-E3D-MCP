using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using E3d21AiBridge;

// These tests execute the production partial class with recording SDK fakes.
// They do not emulate PML parsing, prove native input-stack cleanup, or establish
// E3D's interpretation of a code page; those require separate live acceptance.
internal static class PmlScriptHarness
{
    private static int checks;
    internal static string Mode, LastCommand, LastPath, DecodedMacro;
    internal static int Creates, Runs, DiagnosticReads;
    internal static FileStream HeldReader;
    internal static Action DuringRun;
    internal static E3d21Context Context;

    internal static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) throw new Exception(message);
    }

    private static void Reject(Action action, string message)
    {
        bool rejected = false;
        try { action(); }
        catch (ArgumentException) { rejected = true; }
        Check(rejected, message);
    }

    private static E3d21Context Reset(string mode = "success")
    {
        if (HeldReader != null) { HeldReader.Dispose(); HeldReader = null; }
        if (LastPath != null && File.Exists(LastPath)) File.Delete(LastPath);
        Mode = mode;
        LastCommand = LastPath = DecodedMacro = null;
        Creates = Runs = DiagnosticReads = 0;
        DuringRun = null;
        Context = new E3d21Context();
        return Context;
    }

    private static Dictionary<string, object> Row(object value) { return (Dictionary<string, object>)value; }
    private static Dictionary<string, object> Cleanup(Dictionary<string, object> value) { return Row(value["cleanup"]); }

    private static void InputPolicy()
    {
        Encoding ascii = Encoding.GetEncoding(20127);
        foreach (string source in new[] { null, "", " \r\n\t", "a\0b", "\ud800", "\udfff", "a\ud800b", new string('a', 262145) })
            Reject(() => PmlScriptInput.Prepare(source, ascii), "Invalid source must fail before encoding or file creation.");
        Reject(() => PmlScriptInput.Prepare("中文", ascii), "Unrepresentable text must not use replacement fallback.");
        Reject(() => PmlScriptInput.Prepare(new string('中', 87382), Encoding.UTF8), "UTF-8 byte budget must not be confused with UTF-16 length.");
        Check(PmlScriptInput.Prepare(new string('a', 262144), ascii).ByteCount == 262144, "Exact byte limit is accepted.");
        string original = "!a = 1\r\nif !a eq 1 then\n$P OK\rendif";
        var input = PmlScriptInput.Prepare(original, ascii);
        Check(input.LineCount == 4 && input.TerminalNewlineAdded, "CRLF, LF and CR preserve four physical source lines.");
        Check(ascii.GetString(input.MacroBytes) == original + "\r\n", "Only the explicitly reported terminal newline is appended.");
        string expectedHash;
        using (var sha = SHA256.Create()) expectedHash = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(original))).Replace("-", "").ToLowerInvariant();
        Check(input.SourceSha256 == expectedHash, "Hash covers the exact original UTF-8 source, not encoded or newline-adjusted bytes.");
        foreach (string ending in new[] { "\n", "\r", "\r\n" })
        {
            input = PmlScriptInput.Prepare("$P OK" + ending, ascii);
            Check(!input.TerminalNewlineAdded && input.LineCount == 2 && ascii.GetString(input.MacroBytes) == "$P OK" + ending,
                "An existing terminator is preserved, and final empty line counting is explicit.");
        }
        input = PmlScriptInput.Prepare("$P 中文\n", Encoding.GetEncoding(936));
        Check(input.CodePage == 936 && Encoding.GetEncoding(936).GetString(input.MacroBytes) == "$P 中文\n",
            "A representable local-code-page script round trips without UTF-8 substitution or BOM.");
        Check(PmlScriptInput.MacroCommand(@"C:\Temp folder\owned.pmlmac", 1252) == "$M \"C:/Temp folder/owned.pmlmac\"",
            "Fixed quoted macro invocation accepts absolute paths with spaces.");
        foreach (string unsafePath in new[] { "relative.pmlmac", "C:\\Temp\\bad\".pmlmac", "C:\\Temp\\$x.pmlmac", "C:\\%TMP%\\a.pmlmac", "C:\\Temp\\a\n.pmlmac", "C:\\Temp\\a\t.pmlmac" })
            Reject(() => PmlScriptInput.MacroCommand(unsafePath, 1252), "Unsafe path cannot inject or expand a second PML command.");
        Reject(() => PmlScriptInput.MacroCommand(@"C:\中文\a.pmlmac", 20127), "An unrepresentable generated path cannot silently change identity.");
    }

    private static void PreviewAndGates()
    {
        var context = Reset();
        var before = Directory.GetFiles(Path.GetTempPath(), "e3d-mcp-*.pmlmac").OrderBy(value => value).ToArray();
        var result = Row(context.ExecutePmlScript("!a = (\n", true));
        Check(Equals(result["success"], true) && Equals(result["syntax_checked"], false), "Preview validates representation, not PML syntax.");
        Check(context.Begins == 0 && context.Dispatches == 0 && Creates == 0 && Runs == 0, "Preview does not enter a gate or any SDK call.");
        Check(Equals(result["execution_attempted"], false) && Equals(Cleanup(result)["status"], "not_created"), "Preview cannot claim native execution or a created macro.");
        Check(before.SequenceEqual(Directory.GetFiles(Path.GetTempPath(), "e3d-mcp-*.pmlmac").OrderBy(value => value)), "Preview creates no temporary files.");
        context = Reset(); context.Busy = true;
        result = Row(context.ExecutePmlScript("$P OK", false));
        Check(Equals(result["busy"], true) && context.Dispatches == 0 && Creates == 0 && context.Ends == 0,
            "A busy rejection cannot create, dispatch or release another owner's gate.");
        context = Reset(); context.Fault = true;
        result = Row(context.ExecutePmlScript("$P OK", false));
        Check(Equals(result["execution_attempted"], false) && context.Dispatches == 0 && Creates == 0,
            "Pre-existing faults reject even macro source containing recovery commands.");
        context = Reset(); context.FailDispatch = true;
        result = Row(context.ExecutePmlScript("$P OK", false));
        Check(Equals(result["success"], false) && context.Ends == 1 && Equals(Cleanup(result)["status"], "not_created"),
            "Unavailable dispatcher releases its own gate without creating a file.");
        Reject(() => context.ExecutePmlScript("$P \ud800", false), "Invalid Unicode cannot reach a gate or SDK.");
    }

    private static void NormalAndReturnedFailure()
    {
        foreach (string mode in new[] { "success", "false" })
        {
            var context = Reset(mode);
            context.AddMessage("before");
            DuringRun = () => context.RotateMessages("during");
            var result = Row(context.ExecutePmlScript("!a = 1\n$P OK", false));
            Check(Creates == 1 && Runs == 1 && context.Begins == 1 && context.Dispatches == 1 && context.Ends == 1,
                "One script owns one guard, macro command, nested Run and UI dispatch.");
            Check(LastCommand.StartsWith("$M \"") && LastCommand.EndsWith(".pmlmac\"") && DecodedMacro == "!a = 1\n$P OK\r\n",
                "Native SDK receives only the generated macro invocation and intact script bytes.");
            Check(Equals(result["success"], mode == "success") && Equals(result["execution_completed"], true)
                && Equals(result["native_run_returned"], true) && Equals(result["outcome_uncertain"], false),
                "Returned false is a completed native call, preserving earlier effects rather than declaring rollback.");
            Check(Equals(Cleanup(result)["status"], "deleted") && !File.Exists(LastPath), "Both ordinary boolean returns clean up only the owned macro.");
            Check(Equals(result["source_echoed"], false) && !result.ContainsKey("source") && !result.ContainsKey("command"),
                "Result never automatically echoes original source or substitutes it for the macro command.");
            var messages = (List<object>)result["messages"];
            Check(messages.Count == 1 && Equals(Row(messages[0])["description"], "during"), "Message capture survives a rotated full queue without including the old row.");
            if (mode == "false")
            {
                var metadata = Row(result["error_metadata"]);
                Check(Equals(metadata["module_number"], 47) && Equals(metadata["message_number"], 15)
                    && metadata["line_number"] == null, "Error codes come from actual Command.Error metadata; source line is not fabricated.");
                Check(Equals(result["error"], "CP: Syntax error"), "Native command error is retained.");
            }
        }
    }

    private static void ExceptionsAndCleanup()
    {
        var context = Reset("throw");
        var result = Row(context.ExecutePmlScript("$P BEFORE", false));
        Check(Equals(result["success"], false) && Equals(result["execution_attempted"], true) && Equals(result["execution_completed"], false)
            && Equals(result["outcome_uncertain"], true) && Equals(result["retry_safe"], false), "Run exception remains uncertain and is not replayed.");
        Check(Equals(Cleanup(result)["status"], "retained_uncertain") && File.Exists(LastPath) && Runs == 1 && context.Ends == 1,
            "Uncertain Run retains the owned file for inspection and does not retry.");
        context = Reset("fault");
        result = Row(context.ExecutePmlScript("$P BEFORE", false));
        Check(Equals(result["native_run_returned"], true) && Equals(result["outcome_uncertain"], true) && DiagnosticReads == 0,
            "A fault after Run returns stops all subsequent native diagnostic reads.");
        Check(Equals(Cleanup(result)["status"], "retained_uncertain") && File.Exists(LastPath), "A faulted outcome retains evidence despite a boolean return.");
        context = Reset("create_throw");
        result = Row(context.ExecutePmlScript("$P BEFORE", false));
        Check(Equals(result["execution_attempted"], false) && Equals(result["outcome_uncertain"], false) && Equals(Cleanup(result)["status"], "deleted"),
            "A command-construction failure before Run can clean its never-executed macro.");
        context = Reset("hold_reader");
        result = Row(context.ExecutePmlScript("$P OK", false));
        Check(Equals(result["success"], true) && Equals(Cleanup(result)["status"], "delete_failed") && Equals(Cleanup(result)["retained"], true),
            "Cleanup failure is reported separately and cannot turn a completed operation into a replay candidate.");
        context = Reset("diagnostic_throw");
        result = Row(context.ExecutePmlScript("$P OK", false));
        Check(Equals(result["success"], true) && ((List<object>)result["diagnostic_errors"]).Count == 1
            && Equals(Cleanup(result)["status"], "deleted"), "A managed diagnostic read failure preserves returned execution success and cleanup.");
    }

    private static void DispatcherCompletionOwnership()
    {
        foreach (string mode in new[] { "late_return", "late_throw" })
        {
            var context = Reset();
            context.DispatchMode = mode;
            var result = Row(context.ExecutePmlScript("$P SHOULD_NOT_RUN", false));
            Check(Equals(result["success"], false) && Equals(result["execution_attempted"], false)
                && context.Ends == 1 && Creates == 0 && Runs == 0, "A never-started callback is cancelled before releasing the command gate.");
            Check(Equals(Cleanup(result)["status"], "not_created") && context.PendingCallback != null,
                "A late queued callback has not created a macro.");
            context.PendingCallback();
            Check(Creates == 0 && Runs == 0 && LastPath == null && context.Ends == 1,
                "The cancelled callback cannot create a file or run later after dispatch return/throw.");
        }

        foreach (string dispatchMode in new[] { "running_return", "running_throw" })
        foreach (string runMode in new[] { "blocked", "blocked_throw" })
        {
            var context = Reset(runMode);
            context.DispatchMode = dispatchMode;
            Dictionary<string, object> result = null;
            Exception callerFailure = null;
            using (var callerFinished = new ManualResetEventSlim())
            {
                var caller = new Thread(() =>
                {
                    try { result = Row(context.ExecutePmlScript("$P BLOCKED", false)); }
                    catch (Exception error) { callerFailure = error; }
                    finally { callerFinished.Set(); }
                });
                caller.Start();
                try
                {
                    Check(context.DispatchReturned.Wait(5000), "The test dispatcher must return or throw while Run is still blocked.");
                    Check(!callerFinished.Wait(100) && context.Ends == 0 && context.ActiveGate,
                        "A running callback owns the command gate until completion despite early dispatcher return/throw.");
                    Check(LastPath != null && File.Exists(LastPath), "The active macro survives a failed dispatch while Run is still running.");
                    bool locked = false;
                    try { using (var write = new FileStream(LastPath, FileMode.Open, FileAccess.Write, FileShare.ReadWrite)) { } }
                    catch (IOException) { locked = true; }
                    Check(locked, "The macro ownership handle remains held while Run is active.");
                }
                finally
                {
                    context.ReleaseRun.Set();
                    caller.Join(5000);
                    if (context.CallbackThread != null) context.CallbackThread.Join(5000);
                }
                Check(callerFinished.IsSet && callerFailure == null && context.Ends == 1 && !context.ActiveGate,
                    "Resource cleanup and receipt return happen only after the running callback ends.");
            }
            bool uncertain = dispatchMode == "running_throw" || runMode == "blocked_throw";
            Check(Equals(result["outcome_uncertain"], uncertain) && Equals(result["success"], !uncertain),
                "An eventual callback exception is not lost when the dispatcher already returned.");
            Check(Equals(Cleanup(result)["status"], uncertain ? "retained_uncertain" : "deleted")
                && File.Exists(LastPath) == uncertain && Creates == 1 && Runs == 1,
                "Completed callback outcome governs owned-file retention, without replay.");
        }
    }

    private static int Main()
    {
        try
        {
            InputPolicy(); PreviewAndGates(); NormalAndReturnedFailure(); ExceptionsAndCleanup(); DispatcherCompletionOwnership();
            Reset();
            Console.WriteLine("PML script checks passed: " + checks);
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); Reset(); return 1; }
    }
}

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        internal bool Busy, Fault, FailDispatch, ActiveGate;
        internal int Begins, Ends, Dispatches;
        internal string DispatchMode;
        internal Action PendingCallback;
        internal Thread CallbackThread;
        internal Exception CallbackFailure;
        internal readonly ManualResetEventSlim RunEntered = new ManualResetEventSlim();
        internal readonly ManualResetEventSlim ReleaseRun = new ManualResetEventSlim();
        internal readonly ManualResetEventSlim DispatchReturned = new ManualResetEventSlim();
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        private readonly object messageLock = new object();
        private readonly Queue<Dictionary<string, object>> outputMessages = new Queue<Dictionary<string, object>>();
        internal void AddMessage(string value) { outputMessages.Enqueue(new Dictionary<string, object> { ["description"] = value }); }
        internal void RotateMessages(string value) { outputMessages.Dequeue(); AddMessage(value); }
        private bool TryBeginCommand(string label) { Begins++; if (Fault) throw new NativeDatabaseFaultException(); ActiveGate = !Busy; return !Busy; }
        private void EndCommand() { Ends++; ActiveGate = false; }
        private T OnUi<T>(Func<T> action)
        {
            Dispatches++;
            if (FailDispatch) throw new InvalidOperationException("Dispatcher unavailable.");
            if (DispatchMode != null && DispatchMode.StartsWith("late_"))
            {
                PendingCallback = () => action();
                if (DispatchMode == "late_throw") throw new InvalidOperationException("Dispatcher failed after queueing.");
                return default(T);
            }
            if (DispatchMode != null && DispatchMode.StartsWith("running_"))
            {
                CallbackThread = new Thread(() => { try { action(); } catch (Exception error) { CallbackFailure = error; } });
                CallbackThread.Start();
                if (!RunEntered.Wait(5000)) throw new Exception("The callback did not enter Run.");
                DispatchReturned.Set();
                if (DispatchMode == "running_throw") throw new InvalidOperationException("Dispatcher failed with callback still running.");
                return default(T);
            }
            return action();
        }
        private static void ThrowIfNativeFaulted() { if (PmlScriptHarness.Context.Fault) throw new NativeDatabaseFaultException(); }
        private static T NativeRead<T>(Func<T> read) { ThrowIfNativeFaulted(); try { return read(); } finally { ThrowIfNativeFaulted(); } }
    }
}

namespace Aveva.Core.Utilities.Messaging
{
    public sealed class PdmsMessage
    {
        public int ModuleNumber { get { PmlScriptHarness.DiagnosticReads++; return 47; } }
        public int MessageNumber { get { PmlScriptHarness.DiagnosticReads++; return 15; } }
        public string MessageText() { PmlScriptHarness.DiagnosticReads++; return "CP: Syntax error"; }
    }
}

namespace Aveva.Core.Utilities.CommandLine
{
    public sealed class Command
    {
        public static Command CreateCommand(string command)
        {
            PmlScriptHarness.Creates++;
            PmlScriptHarness.LastCommand = command;
            PmlScriptHarness.LastPath = command.Substring(4, command.Length - 5).Replace('/', '\\');
            if (PmlScriptHarness.Mode == "create_throw") throw new InvalidOperationException("Command construction failed.");
            return new Command();
        }
        public bool Run()
        {
            PmlScriptHarness.Runs++;
            using (var stream = new FileStream(PmlScriptHarness.LastPath, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var reader = new StreamReader(stream, Encoding.Default))
                PmlScriptHarness.DecodedMacro = reader.ReadToEnd();
            bool locked = false;
            try { using (var write = new FileStream(PmlScriptHarness.LastPath, FileMode.Open, FileAccess.Write, FileShare.ReadWrite)) { } }
            catch (IOException) { locked = true; }
            PmlScriptHarness.Check(locked, "The running macro cannot be overwritten while native code reads it.");
            if (PmlScriptHarness.DuringRun != null) PmlScriptHarness.DuringRun();
            if (PmlScriptHarness.Mode == "blocked" || PmlScriptHarness.Mode == "blocked_throw")
            {
                PmlScriptHarness.Context.RunEntered.Set();
                if (!PmlScriptHarness.Context.ReleaseRun.Wait(5000)) throw new Exception("Blocked Run was not released by the test.");
                if (PmlScriptHarness.Mode == "blocked_throw") throw new InvalidOperationException("Native Run failed after dispatch returned.");
            }
            if (PmlScriptHarness.Mode == "throw") throw new InvalidOperationException("Native Run did not return.");
            if (PmlScriptHarness.Mode == "fault") PmlScriptHarness.Context.Fault = true;
            if (PmlScriptHarness.Mode == "hold_reader")
                PmlScriptHarness.HeldReader = new FileStream(PmlScriptHarness.LastPath, FileMode.Open, FileAccess.Read, FileShare.Read);
            return PmlScriptHarness.Mode != "false";
        }
        public string Result
        {
            get
            {
                PmlScriptHarness.DiagnosticReads++;
                if (PmlScriptHarness.Mode == "diagnostic_throw") throw new InvalidOperationException("Result read failed.");
                return "native result";
            }
        }
        public Messaging.PdmsMessage Error
        {
            get { PmlScriptHarness.DiagnosticReads++; return PmlScriptHarness.Mode == "false" ? new Messaging.PdmsMessage() : null; }
        }
    }
}
