using System;
using System.Collections.Generic;
using System.Globalization;
using System.Threading;
using System.Threading.Tasks;
using E3d21AiBridge;

internal static class NativeDatabaseFaultStateTests
{
    private static int checks;

    private static void Check(bool condition, string message)
    {
        Interlocked.Increment(ref checks);
        if (!condition) throw new Exception(message);
    }

    private static void Main()
    {
        TestRecognitionAndLatch();
        TestRecoveryCommands();
        TestDialogObservation();
        TestConcurrentObservations();
        TestException();
        Console.WriteLine("Native database fault state checks passed: " + checks);
    }

    private static void TestRecognitionAndLatch()
    {
        var state = new NativeDatabaseFaultState();
        Dictionary<string, object> clean = state.Snapshot();
        Check(!state.HasFault && state.Generation == 0L, "A new bridge state must be clean.");
        Check(!(bool)clean["has_fault"] && (long)clean["generation"] == 0L,
            "The initial snapshot must report no observations.");
        foreach (string field in new[] { "code", "routine", "raw", "native_date", "observed_utc" })
            Check(clean[field] == null, "A clean snapshot must have no " + field + ".");

        string[] ordinaryMessages =
        {
            null, "", " ", "Database error", "Database error 538", "Warning",
            "Warning: Unhandled Database error 538 from db_cxx_get_reference",
            "No current element", " - No current element", "Last Dabacon routines called:",
            "some text Unhandled Database error 538 from db_cxx_get_reference",
            "unhandled Database error 538 from db_cxx_get_reference",
            "Unhandled Database error -538 from db_cxx_get_reference",
            "Unhandled Database error 538.0 from db_cxx_get_reference",
            "Unhandled Database error \u0665\u0663\u0668 from db_cxx_get_reference",
            "Unhandled Database error 538 from", "Unhandled Database error 538 from   ",
            "Unhandled Database error 538 from db_cxx_get_reference\nWarning"
        };
        foreach (string description in ordinaryMessages)
            Check(!state.Observe(description, "ignored"), "Ordinary or malformed output must not latch: " + description);
        Check(!state.HasFault && state.Generation == 0L, "Nonmatching output cannot change fault state.");

        const string raw = " \tUnhandled Database error 538 from db_cxx_get_reference            ";
        DateTime before = DateTime.UtcNow;
        Check(state.Observe(raw, "20:03:35"), "The exact native fatal description must latch.");
        DateTime after = DateTime.UtcNow;
        Check(state.HasFault && state.Generation == 1L, "The first native fault must latch and advance generation.");
        Dictionary<string, object> first = state.Snapshot();
        Check((bool)first["has_fault"] && (int)first["code"] == 538, "Snapshot must retain the native error code.");
        Check((string)first["routine"] == "db_cxx_get_reference", "Routine padding must be trimmed.");
        Check((string)first["raw"] == raw && (string)first["native_date"] == "20:03:35",
            "Snapshot must preserve unmodified output and native time.");
        DateTime timestamp = DateTime.Parse((string)first["observed_utc"], CultureInfo.InvariantCulture,
            DateTimeStyles.RoundtripKind);
        Check(timestamp.Kind == DateTimeKind.Utc && timestamp >= before && timestamp <= after,
            "Each observation requires a real UTC observation timestamp.");
        Check((long)clean["generation"] == 0L && clean["code"] == null,
            "An earlier snapshot must not change when a fault is observed.");

        Check(state.Observe(raw, "20:03:35"), "An identical callback is another accepted observation.");
        Check(state.Generation == 2L && (long)state.Snapshot()["generation"] == 2L,
            "Repeated callbacks must advance the audit generation.");
        Check((long)first["generation"] == 1L, "An earlier fault snapshot must remain unchanged.");
        first.Clear();
        first["has_fault"] = false;
        first["generation"] = -1L;
        Check(state.HasFault && state.Generation == 2L && (int)state.Snapshot()["code"] == 538,
            "Mutating a returned dictionary cannot clear or alter the latch.");
        Check(!state.Observe("Command cancelled", "later") && !state.Observe("", null),
            "Cancellation and empty output are not new native fault observations.");
        Check(state.HasFault && state.Generation == 2L, "Unrelated output cannot clear an existing latch.");

        Check(state.Observe("\r\n\tUnhandled Database error 539 from db_restore_stack\r\n", null),
            "Outer whitespace is allowed around an exact native description.");
        Dictionary<string, object> latest = state.Snapshot();
        Check((int)latest["code"] == 539 && (string)latest["routine"] == "db_restore_stack"
            && latest["native_date"] == null && (long)latest["generation"] == 3L,
            "A new fault must update the whole snapshot atomically, including an absent native time.");
        const string largeCode = "999999999999999999999999999999999999";
        Check(state.Observe("Unhandled Database error " + largeCode + " from db_unknown", "later"),
            "An oversized numeric code must not suppress the fault latch.");
        Check((string)state.Snapshot()["code"] == largeCode && state.Generation == 4L,
            "An oversized code must be preserved exactly for diagnostics.");
        var fresh = new NativeDatabaseFaultState();
        Check(!fresh.HasFault && fresh.Generation == 0L && state.HasFault,
            "A new instance is clean without clearing another instance's latch.");
    }

    private static void TestRecoveryCommands()
    {
        foreach (string command in new[] { "SAVEWORK", "DESIGN", "savework", "DeSiGn", " SAVEWORK ", "\t DESIGN\t" })
            Check(NativeDatabaseFaultState.IsExactRecoveryCommand(command), "Exact standalone recovery command must be accepted: " + command);

        string[] rejected =
        {
            null, "", " ", "CANCEL", "SAVE", "SAVEWORKNOW", "DESIGNER", "SAVE WORK",
            "SAVEWORK ALL", "DESIGN /SITE", "SAVEWORK;", ";DESIGN", "SAVEWORK; DESIGN",
            "SAVEWORK DESIGN", "SAVEWORK\tDESIGN", "SAVEWORK\nDESIGN", "SAVEWORK\rDESIGN",
            "\rSAVEWORK", "SAVEWORK\r", "\nDESIGN", "DESIGN\n", " \r\nDESIGN \t",
            "SAVEWORK !comment", "['SAVEWORK']", "[\"SAVEWORK\",\"DESIGN\"]", "SAVEWORK\0"
        };
        foreach (string command in rejected)
            Check(!NativeDatabaseFaultState.IsExactRecoveryCommand(command),
                "Extra tokens, batches, chains, and all CR/LF input must be rejected: " + command);
    }

    private static void TestDialogObservation()
    {
        var state = new NativeDatabaseFaultState();
        Check(state.ObserveDialog(538) && state.HasFault && state.Generation == 1L,
            "Verified native dialog evidence must latch even if its output callback was missed.");
        Dictionary<string, object> snapshot = state.Snapshot();
        Check((int)snapshot["code"] == 538 && (string)snapshot["raw"] == "Database error 538"
            && snapshot["routine"] == null && snapshot["native_date"] == null
            && (string)snapshot["source"] == "native_dialog",
            "Dialog evidence must not invent a native routine or native output timestamp.");
        Check(!state.ObserveDialog(539) && state.Generation == 1L
            && (int)state.Snapshot()["code"] == 538,
            "Repeated dialog scans must not overwrite an existing fault or advance its generation.");
        state.Observe("Unhandled Database error 539 from db_output", "later");
        Check(state.Generation == 2L && (string)state.Snapshot()["source"] == "native_output"
            && !state.ObserveDialog(538) && (int)state.Snapshot()["code"] == 539,
            "Native output must remain a new observation and subsequent dialog scans must preserve it.");
    }

    private static void TestConcurrentObservations()
    {
        const int writerCount = 6;
        const int readerCount = 3;
        const int writesPerWriter = 400;
        var state = new NativeDatabaseFaultState();
        state.Observe(Description(0), "0");
        var tasks = new Task[writerCount + readerCount];
        using (var start = new ManualResetEventSlim(false))
        {
            for (int writer = 0; writer < writerCount; writer++)
            {
                int writerIndex = writer;
                tasks[writer] = Task.Run(() =>
                {
                    start.Wait();
                    for (int index = 0; index < writesPerWriter; index++)
                    {
                        int token = writerIndex * writesPerWriter + index + 1;
                        Check(state.Observe(Description(token), token.ToString(CultureInfo.InvariantCulture)),
                            "Every concurrent native fault must be observed.");
                    }
                });
            }
            for (int reader = 0; reader < readerCount; reader++)
            {
                tasks[writerCount + reader] = Task.Run(() =>
                {
                    start.Wait();
                    long lastGeneration = 0L;
                    for (int index = 0; index < writerCount * writesPerWriter; index++)
                    {
                        Dictionary<string, object> snapshot = state.Snapshot();
                        int token = (int)snapshot["code"];
                        long currentGeneration = (long)snapshot["generation"];
                        string tokenText = token.ToString(CultureInfo.InvariantCulture);
                        Check((bool)snapshot["has_fault"] && (string)snapshot["raw"] == Description(token)
                            && (string)snapshot["routine"] == "db_test_" + tokenText
                            && (string)snapshot["native_date"] == tokenText,
                            "Concurrent snapshot fields must belong to one complete observation.");
                        Check(currentGeneration >= lastGeneration && state.Generation >= currentGeneration,
                            "Observed generations must never go backwards or tear.");
                        lastGeneration = currentGeneration;
                        snapshot["code"] = -1;
                        snapshot["generation"] = -1L;
                    }
                });
            }
            start.Set();
            Task.WaitAll(tasks);
        }
        Check(state.HasFault && state.Generation == 1L + writerCount * writesPerWriter,
            "Concurrent callbacks must never lose an audit generation.");
        Check((long)state.Snapshot()["generation"] == state.Generation,
            "The final snapshot and cheap generation getter must agree.");
    }

    private static string Description(int token)
    {
        string text = token.ToString(CultureInfo.InvariantCulture);
        return "Unhandled Database error " + text + " from db_test_" + text;
    }

    private static void TestException()
    {
        var original = new Exception("original");
        var exception = new NativeDatabaseFaultException("native fault", original);
        Check(exception is InvalidOperationException && exception.Message == "native fault"
            && ReferenceEquals(exception.InnerException, original),
            "The dedicated managed exception must retain normal exception details.");
        Check(new NativeDatabaseFaultException().Message.Contains("do not replay"),
            "The default fault exception must warn against replaying the interrupted operation.");
    }
}
