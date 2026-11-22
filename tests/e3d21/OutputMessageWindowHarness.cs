using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

internal sealed class PdmsOutput
{
    internal string Type;
    internal string Description;
    internal string Details;
    internal string Category;
    internal DateTime Date;
}

internal sealed class PdmsMessage
{
    internal static PdmsMessage GetLastError() { return null; }
}

internal sealed class NativeDatabaseFaultState
{
    internal void Observe(string description, string date) { }
}

internal sealed class OutputMessageWindowHarness
{
    private readonly object messageLock = new object();
    private readonly Queue<Dictionary<string, object>> outputMessages = new Queue<Dictionary<string, object>>();
    private long outputMessageSequence;
    private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
    private static int checks;

    private static void Check(bool condition, string label)
    {
        checks++;
        if (!condition) throw new InvalidOperationException("Check failed: " + label);
    }

    private void ThrowIfNativeFaulted() { }
    private T NativeRead<T>(Func<T> read) { return read(); }
    private string MessageText(PdmsMessage message) { return ""; }

    // PRODUCTION_METHODS

    private void Emit(string description)
    {
        CaptureOutput(new PdmsOutput { Type = "test", Description = description, Details = "", Category = "", Date = DateTime.UtcNow });
    }

    private Dictionary<string, object> Diagnose(long mark)
    {
        var result = new Dictionary<string, object>();
        AddDiagnostics(result, mark);
        return result;
    }

    private static Dictionary<string, object> Window(Dictionary<string, object> result)
    { return (Dictionary<string, object>)result["message_window"]; }

    private void Run()
    {
        var empty = Diagnose(0);
        Check((bool)Window(empty)["complete"] && Convert.ToInt64(Window(empty)["observed_count"], CultureInfo.InvariantCulture) == 0,
            "empty window is complete");
        Check((string)Window(empty)["status"] == "complete" && Window(empty)["first_retained_sequence"] == null &&
            (long)Window(empty)["start_sequence_exclusive"] == 0 && (long)Window(empty)["end_sequence_inclusive"] == 0,
            "empty ring preserves interval and nullable retention metadata");
        Check(!empty.ContainsKey("messages"), "empty window omits message array");

        for (int index = 1; index <= 3; index++) Emit("underfull-" + index);
        var underfull = Diagnose(0);
        Check((bool)Window(underfull)["complete"] && Convert.ToInt64(Window(underfull)["observed_count"], CultureInfo.InvariantCulture) == 3,
            "underfull ring retains the full window");
        Check(Convert.ToInt64(((Dictionary<string, object>)((IList<object>)underfull["messages"])[0])["sequence"],
            CultureInfo.InvariantCulture) == 1, "first sequence is stable");

        var exactContext = new OutputMessageWindowHarness();
        for (int index = 1; index <= 500; index++) exactContext.Emit("full-" + index);
        var exact = exactContext.Diagnose(0);
        Check((bool)Window(exact)["complete"] && Convert.ToInt64(Window(exact)["observed_count"], CultureInfo.InvariantCulture) == 500,
            "exact-capacity ring retains a complete window");
        Check((long)Window(exact)["omitted_count"] == 0, "exact-capacity window reports no omissions");

        long fullMark = exactContext.MessageSequence();
        exactContext.Emit("new-at-capacity");
        var fullOneNew = exactContext.Diagnose(fullMark);
        Check((bool)Window(fullOneNew)["complete"] &&
            Convert.ToInt64(Window(fullOneNew)["observed_count"], CultureInfo.InvariantCulture) == 1 &&
            Convert.ToInt64(Window(fullOneNew)["omitted_count"], CultureInfo.InvariantCulture) == 0,
            "one new message remains visible when the ring starts full");
        Check((string)((Dictionary<string, object>)((IList<object>)fullOneNew["messages"])[0])["description"] == "new-at-capacity",
            "full-ring receipt includes the new message instead of old retained messages");
        Check((long)Window(fullOneNew)["start_sequence_exclusive"] == 500 &&
            (long)Window(fullOneNew)["end_sequence_inclusive"] == 501,
            "full-ring receipt records the exact command interval");

        long nextMark = exactContext.MessageSequence();
        exactContext.Emit("subsequent-command");
        var subsequent = exactContext.Diagnose(nextMark);
        Check(Convert.ToInt64(Window(subsequent)["observed_count"], CultureInfo.InvariantCulture) == 1 &&
            (string)((Dictionary<string, object>)((IList<object>)subsequent["messages"])[0])["description"] == "subsequent-command",
            "subsequent windows exclude prior command messages");

        var singleWrap = new OutputMessageWindowHarness();
        for (int index = 1; index <= 500; index++) singleWrap.Emit("before-" + index);
        long start = singleWrap.MessageSequence();
        for (int index = 1; index <= 501; index++) singleWrap.Emit("during-" + index);
        var once = singleWrap.Diagnose(start);
        Check(!(bool)Window(once)["complete"] && (string)Window(once)["status"] == "overwritten",
            "one ring wrap is reported as incomplete");
        Check(Convert.ToInt64(Window(once)["omitted_count"], CultureInfo.InvariantCulture) == 1 &&
            Convert.ToInt64(Window(once)["observed_count"], CultureInfo.InvariantCulture) == 500,
            "single wrap reports the exact omitted count");
        Check((long)Window(once)["first_retained_sequence"] == 502,
            "single wrap reports the first retained sequence");

        var multipleWraps = new OutputMessageWindowHarness();
        for (int index = 1; index <= 1002; index++) multipleWraps.Emit("overflow-" + index);
        var many = multipleWraps.Diagnose(0);
        Check(Convert.ToInt64(Window(many)["omitted_count"], CultureInfo.InvariantCulture) == 502 &&
            Convert.ToInt64(Window(many)["observed_count"], CultureInfo.InvariantCulture) == 500,
            "multiple wraps report exact loss");
        Check((long)Window(many)["end_sequence_inclusive"] == 1002 &&
            (long)Window(many)["first_retained_sequence"] == 503,
            "multiple wraps preserve the sequence interval");

        long unchanged = multipleWraps.MessageSequence();
        var boundary = multipleWraps.Diagnose(unchanged);
        Check((bool)Window(boundary)["complete"] && Convert.ToInt64(Window(boundary)["observed_count"], CultureInfo.InvariantCulture) == 0,
            "empty interval at the current sequence is complete");

        var longSequence = new OutputMessageWindowHarness();
        longSequence.outputMessageSequence = int.MaxValue;
        long highMark = longSequence.MessageSequence();
        longSequence.Emit("past-int32");
        var high = longSequence.Diagnose(highMark);
        Check((long)Window(high)["end_sequence_inclusive"] == (long)int.MaxValue + 1 &&
            Convert.ToInt64(((Dictionary<string, object>)((IList<object>)high["messages"])[0])["sequence"], CultureInfo.InvariantCulture) == (long)int.MaxValue + 1 &&
            Convert.ToInt64(Window(high)["omitted_count"], CultureInfo.InvariantCulture) == 0,
            "sequence windows retain 64-bit values beyond Int32 range");
        Console.WriteLine("Output message window checks passed: " + checks);
    }

    private static int Main()
    {
        try { new OutputMessageWindowHarness().Run(); return 0; }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
