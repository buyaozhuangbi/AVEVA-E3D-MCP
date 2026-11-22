using System;
using System.Collections.Generic;

namespace E3d21AiBridge;

internal static class CommandBatchRunner
{
    public static IList<object> Run(
        IList<string> commands,
        bool stopOnError,
        Func<string, Dictionary<string, object>> execute)
    {
        if (commands == null || commands.Count == 0)
            throw new ArgumentException("commands must not be empty", "commands");
        if (execute == null)
            throw new ArgumentNullException("execute");

        var results = new List<object>();
        foreach (string command in commands)
        {
            Dictionary<string, object> result;
            if (string.IsNullOrWhiteSpace(command))
            {
                result = Failure(command, "command must not be empty");
            }
            else
            {
                try
                {
                    result = execute(command);
                    if (result == null)
                        throw new InvalidOperationException("Command executor returned no result.");
                }
                catch (Exception error)
                {
                    result = Failure(command, error.Message);
                    result["exception_type"] = error.GetType().FullName;
                    result["outcome_uncertain"] = true;
                    results.Add(result);
                    // An SDK exception may invalidate the context. Never execute more commands.
                    break;
                }
            }

            results.Add(result);
            object success;
            if (stopOnError && result.TryGetValue("success", out success) && success is bool && !(bool)success)
                break;
        }
        return results;
    }

    private static Dictionary<string, object> Failure(string command, string error)
    {
        return new Dictionary<string, object>
        {
            ["dry_run"] = false,
            ["success"] = false,
            ["command"] = command ?? "",
            ["error"] = error
        };
    }
}
