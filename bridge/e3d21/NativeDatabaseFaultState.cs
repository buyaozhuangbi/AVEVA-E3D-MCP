using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace E3d21AiBridge;

// Pure managed state: independent of the output queue and command cancellation.
// A latch belongs to one bridge instance and has deliberately no reset method.
internal sealed class NativeDatabaseFaultState
{
    private static readonly Regex FaultDescription = new Regex(
        @"\AUnhandled Database error (?<code>[0-9]+) from (?<routine>[^\r\n]+)\z",
        RegexOptions.CultureInvariant);

    private readonly object sync = new object();
    private bool hasFault;
    private object code;
    private string routine;
    private string raw;
    private string nativeDate;
    private string observedUtc;
    private string source;
    private long generation;

    internal bool HasFault
    {
        get { lock (sync) return hasFault; }
    }

    internal long Generation
    {
        get { lock (sync) return generation; }
    }

    // True means this callback was recognized as a fault observation. Repeated
    // identical callbacks are new observations too and advance the generation.
    internal bool Observe(string description, string nativeDate)
    {
        if (description == null) return false;
        Match match = FaultDescription.Match(description.Trim());
        if (!match.Success) return false;

        string observedRoutine = match.Groups["routine"].Value.Trim();
        if (observedRoutine.Length == 0) return false;
        string codeText = match.Groups["code"].Value;
        int numericCode;
        // Preserve an unusually large code instead of failing to latch a
        // recognized native fault merely because its digits overflow Int32.
        object observedCode = int.TryParse(codeText, NumberStyles.None,
            CultureInfo.InvariantCulture, out numericCode) ? (object)numericCode : codeText;

        lock (sync)
        {
            code = observedCode;
            routine = observedRoutine;
            raw = description;
            this.nativeDate = nativeDate;
            observedUtc = DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture);
            source = "native_output";
            generation++;
            hasFault = true;
        }
        return true;
    }

    // A verified native dialog can establish the latch when output was missed.
    // Repeated dialog scans must not disguise a new output callback generation.
    internal bool ObserveDialog(int code)
    {
        if (code < 0) return false;
        lock (sync)
        {
            if (hasFault) return false;
            this.code = code;
            routine = null;
            raw = "Database error " + code.ToString(CultureInfo.InvariantCulture);
            nativeDate = null;
            observedUtc = DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture);
            source = "native_dialog";
            generation++;
            hasFault = true;
            return true;
        }
    }

    internal Dictionary<string, object> Snapshot()
    {
        lock (sync)
        {
            // All values are immutable scalars and the dictionary is a copy.
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "has_fault", hasFault },
                { "code", code },
                { "routine", routine },
                { "raw", raw },
                { "native_date", nativeDate },
                { "observed_utc", observedUtc },
                { "source", source },
                { "generation", generation }
            };
        }
    }

    internal static bool IsExactRecoveryCommand(string command)
    {
        // Check before Trim: a trailing newline is still a command separator.
        if (command == null || command.IndexOf('\r') >= 0 || command.IndexOf('\n') >= 0)
            return false;
        string candidate = command.Trim();
        return string.Equals(candidate, "SAVEWORK", StringComparison.OrdinalIgnoreCase)
            || string.Equals(candidate, "DESIGN", StringComparison.OrdinalIgnoreCase);
    }
}

internal sealed class NativeDatabaseFaultException : InvalidOperationException
{
    internal NativeDatabaseFaultException()
        : base("A native database fault is latched; do not replay the interrupted operation.") { }

    internal NativeDatabaseFaultException(string message) : base(message) { }

    internal NativeDatabaseFaultException(string message, Exception innerException)
        : base(message, innerException) { }
}
