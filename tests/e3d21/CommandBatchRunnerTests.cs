#if COMMAND_BATCH_RUNNER_TESTS
using System;
using System.Collections.Generic;
using E3d21AiBridge;

internal static class CommandBatchRunnerTests
{
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    private static Dictionary<string, object> Result(bool success)
    {
        return new Dictionary<string, object> { ["success"] = success };
    }

    private static void Throws<T>(Action action) where T : Exception
    {
        try { action(); }
        catch (T) { return; }
        throw new Exception("Expected " + typeof(T).Name);
    }

    private static void CheckException(bool stopOnError)
    {
        int calls = 0;
        var first = Result(true);
        var results = CommandBatchRunner.Run(new[] { "first", "second", "third" }, stopOnError, command =>
        {
            calls++;
            if (command == "second") throw new InvalidOperationException("SDK failed after dispatch");
            return first;
        });
        Check(calls == 2 && results.Count == 2, "Exception must stop the batch without padding skipped commands");
        Check(ReferenceEquals(results[0], first), "Prior results must be preserved unchanged");
        var failed = (Dictionary<string, object>)results[1];
        Check((bool)failed["success"] == false && (bool)failed["outcome_uncertain"], "Exception outcome must be marked uncertain");
        Check((string)failed["command"] == "second" && (string)failed["error"] == "SDK failed after dispatch", "Failure must identify command and error");
        Check((string)failed["exception_type"] == typeof(InvalidOperationException).FullName, "Failure must identify exception type");
    }

    private static void CheckOrdinaryFailure(bool stopOnError)
    {
        int calls = 0;
        var failure = Result(false);
        var results = CommandBatchRunner.Run(new[] { "first", "bad", "third" }, stopOnError, command =>
        {
            calls++;
            return command == "bad" ? failure : Result(true);
        });
        Check(calls == (stopOnError ? 2 : 3) && results.Count == calls, "Ordinary failure must obey stopOnError");
        Check(ReferenceEquals(results[1], failure) && !failure.ContainsKey("outcome_uncertain"), "Ordinary failure must remain unchanged");
    }

    private static void CheckEmptyCommands(bool stopOnError)
    {
        int calls = 0;
        var results = CommandBatchRunner.Run(new[] { null, " ", "valid" }, stopOnError, command =>
        {
            calls++;
            return Result(true);
        });
        Check(calls == (stopOnError ? 0 : 1), "Empty commands must not reach executor");
        Check(results.Count == (stopOnError ? 1 : 3), "Empty commands must obey stopOnError");
        var first = (Dictionary<string, object>)results[0];
        Check((bool)first["success"] == false && (string)first["command"] == "" && !first.ContainsKey("outcome_uncertain"), "Empty command must be a definite validation failure");
    }

    private static int Main()
    {
        foreach (bool stopOnError in new[] { true, false })
        {
            CheckException(stopOnError);
            CheckOrdinaryFailure(stopOnError);
            CheckEmptyCommands(stopOnError);
        }
        var seen = new List<string>();
        var all = CommandBatchRunner.Run(new[] { "one", "two", "three" }, true, command =>
        {
            seen.Add(command);
            return Result(true);
        });
        Check(all.Count == 3 && string.Join(",", seen) == "one,two,three", "Successful commands must execute in order");
        Throws<ArgumentException>(() => CommandBatchRunner.Run(null, true, command => Result(true)));
        Throws<ArgumentException>(() => CommandBatchRunner.Run(new string[0], true, command => Result(true)));
        Throws<ArgumentNullException>(() => CommandBatchRunner.Run(new[] { "one" }, true, null));
        Console.WriteLine("CommandBatchRunner tests passed.");
        return 0;
    }
}
#endif
