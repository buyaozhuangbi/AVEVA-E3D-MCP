using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Aveva.Core.Utilities.Messaging;
using PmlCommand = Aveva.Core.Utilities.CommandLine.Command;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    public object ExecutePmlScript(string source, bool dryRun)
    {
        PmlScriptInput input = PmlScriptInput.Prepare(source, Encoding.Default);
        string id = Guid.NewGuid().ToString("N");
        var cleanup = new Dictionary<string, object>
        {
            ["attempted"] = false, ["status"] = "not_created", ["retained"] = false,
            ["path"] = null, ["error"] = null
        };
        var diagnostics = new List<object>();
        var result = new Dictionary<string, object>
        {
            ["success"] = dryRun, ["dry_run"] = dryRun, ["script_id"] = id,
            ["source_sha256"] = input.SourceSha256, ["byte_count"] = input.ByteCount,
            ["line_count"] = input.LineCount,
            ["line_count_semantics"] = "Original source: one plus CRLF/CR/LF separators; CRLF counts once, including a final empty line.",
            ["source_echoed"] = false, ["execution_attempted"] = false,
            ["execution_completed"] = false, ["native_run_returned"] = false,
            ["execution_scope"] = "nested", ["syntax_checked"] = false,
            ["result"] = "", ["error"] = "", ["messages"] = new List<object>(),
            ["message_scope"] = "Adapter messages observed during this callback, not proven exclusively attributable to this script; ring-buffer coverage may be incomplete.",
            ["error_metadata"] = null, ["diagnostic_errors"] = diagnostics,
            ["outcome_uncertain"] = false, ["retry_safe"] = false,
            ["pml_input_cleared"] = null,
            ["encoding"] = new Dictionary<string, object>
            {
                ["code_page"] = input.CodePage, ["name"] = input.EncodingName, ["strict"] = true,
                ["byte_count"] = input.MacroBytes.Length, ["bom_written"] = false,
                ["terminal_newline_added"] = input.TerminalNewlineAdded,
                ["terminal_newline"] = input.TerminalNewlineAdded ? "CRLF" : null
            },
            ["cleanup"] = cleanup
        };
        if (dryRun) return result;

        bool began = false, owned = false, attempted = false, uncertain = false;
        string path = null;
        FileStream file = null;
        HashSet<Dictionary<string, object>> priorMessages = null;
        try
        {
            began = TryBeginCommand("PML script " + id);
            if (!began)
            {
                result["busy"] = true;
                result["error"] = "Another E3D command is still running or is waiting on an E3D dialog.";
                return result;
            }
            // Own callback completion independently of the dispatcher. A
            // dispatcher can fail after queuing or starting a callback: cancel
            // a callback that has not begun, or wait for the running callback
            // before reading its receipt or releasing its macro/command gate.
            int invocationState = 0; // 0 queued, 1 started, 3 cancelled before start
            var completed = new TaskCompletionSource<bool>();
            try
            {
                OnUi(delegate
                {
                    if (Interlocked.CompareExchange(ref invocationState, 1, 0) != 0)
                        return false;
                    Exception callbackFailure = null;
                    try
                    {
                        ThrowIfNativeFaulted();
                        path = Path.GetFullPath(Path.Combine(Path.GetTempPath(), "e3d-mcp-" + id + ".pmlmac"));
                        string command = PmlScriptInput.MacroCommand(path, input.CodePage);
                        // CreateNew never overwrites a prior file. After writing, keep
                        // a read-only, read-shared handle until Run returns or throws.
                        // Keeping a writable handle would reject ordinary readers
                        // whose FileShare mode does not permit an existing writer.
                        file = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read);
                        owned = true;
                        cleanup["path"] = path;
                        cleanup["retained"] = true;
                        cleanup["status"] = "created";
                        file.Write(input.MacroBytes, 0, input.MacroBytes.Length);
                        file.Flush(true);
                        file.Dispose();
                        file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
                        lock (messageLock)
                            priorMessages = new HashSet<Dictionary<string, object>>(outputMessages);
                        ThrowIfNativeFaulted();
                        PmlCommand operation = PmlCommand.CreateCommand(command);
                        ThrowIfNativeFaulted();
                        attempted = true;
                        result["execution_attempted"] = true;
                        bool success = operation.Run();
                        result["native_run_returned"] = true;
                        result["execution_completed"] = true;
                        result["success"] = success;
                        ThrowIfNativeFaulted();
                        ReadPmlScriptDiagnostic(result, "result", diagnostics, () => operation.Result ?? "");
                        PdmsMessage error = null;
                        ReadPmlScriptDiagnostic(result, "error", diagnostics, delegate
                        {
                            error = NativeRead(() => operation.Error);
                            return error == null ? "" : NativeRead(() => error.MessageText()) ?? "";
                        });
                        if (error != null)
                        {
                            var metadata = new Dictionary<string, object>
                            {
                                ["source"] = "Command.Error", ["module_number"] = null,
                                ["message_number"] = null, ["line_number"] = null,
                                ["line_number_available"] = false
                            };
                            result["error_metadata"] = metadata;
                            ReadPmlScriptDiagnostic(metadata, "module_number", diagnostics, () => error.ModuleNumber);
                            ReadPmlScriptDiagnostic(metadata, "message_number", diagnostics, () => error.MessageNumber);
                            // The public PdmsMessage metadata supplies module/message
                            // identifiers, not a documented source-line getter.
                        }
                        return true;
                    }
                    catch (Exception error)
                    {
                        // A broken dispatcher may already have returned, so
                        // preserve the callback's eventual failure here too.
                        callbackFailure = error;
                        throw;
                    }
                    finally
                    {
                        if (callbackFailure == null) completed.TrySetResult(true);
                        else completed.TrySetException(callbackFailure);
                    }
                });
            }
            finally
            {
                int previous = Interlocked.CompareExchange(ref invocationState, 3, 0);
                if (previous == 1)
                    completed.Task.GetAwaiter().GetResult();
            }
            if (Volatile.Read(ref invocationState) == 3)
                throw new InvalidOperationException("The UI dispatcher returned before the script callback started; the queued callback was cancelled without execution.");
        }
        catch (Exception error)
        {
            uncertain = attempted;
            result["success"] = false;
            result["outcome_uncertain"] = uncertain;
            result["error"] = error is NativeDatabaseFaultException
                ? "A native database fault interrupted script execution. Inspect current state; do not replay the script."
                : error.Message;
            if (nativeFaultState != null && nativeFaultState.HasFault)
            {
                result["recovery_required"] = true;
                result["native_database_fault"] = nativeFaultState.Snapshot();
            }
        }
        finally
        {
            // This copies managed observations only; no SDK access after a
            // native fault. Reference identity avoids the full-ring count bug.
            if (priorMessages != null)
                lock (messageLock)
                    result["messages"] = outputMessages.Where(message => !priorMessages.Contains(message)).Cast<object>().ToList();
            if (file != null)
            {
                try { file.Dispose(); }
                catch (Exception error)
                {
                    cleanup["error"] = error.Message;
                    cleanup["status"] = "close_failed";
                }
            }
            if (owned)
            {
                if (uncertain)
                    cleanup["status"] = "retained_uncertain";
                else
                {
                    cleanup["attempted"] = true;
                    try
                    {
                        File.Delete(path);
                        cleanup["retained"] = false;
                        cleanup["status"] = "deleted";
                    }
                    catch (Exception error)
                    {
                        cleanup["status"] = "delete_failed";
                        cleanup["error"] = error.Message;
                    }
                }
            }
            if (began) EndCommand();
        }
        return result;
    }

    private static void ReadPmlScriptDiagnostic(Dictionary<string, object> destination, string key,
        List<object> errors, Func<object> read)
    {
        try { destination[key] = NativeRead(read); }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            errors.Add(new Dictionary<string, object> { ["field"] = key, ["error"] = error.Message });
        }
    }
}
