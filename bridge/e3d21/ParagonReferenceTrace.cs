using System;
using System.Globalization;
using System.Text.RegularExpressions;

namespace E3d21AiBridge;

public interface IE3d21ParagonReferenceTraceContext
{
    object ReadParagonReferenceTrace(string refno, int traceLimit, string sessionId);
}

// Literal input only. This contract has no Design name resolver or SDK dependency.
internal sealed class ParagonReferenceTraceRequest
{
    internal const int DefaultTraceLimit = 24;
    internal const int MaximumTraceLimit = 32;
    internal const int MaximumErrorLength = 4096;
    private static readonly Regex NumericReference = new Regex(@"\A=([1-9][0-9]{0,9})/(0|[1-9][0-9]{0,9})\z", RegexOptions.CultureInvariant);

    internal string Refno { get; private set; }
    internal int TraceLimit { get; private set; }
    internal int[] Pair { get; private set; }

    internal static ParagonReferenceTraceRequest Validate(string refno, int traceLimit)
    {
        Match match = NumericReference.Match(refno ?? "");
        int database, element;
        if (!match.Success
            || !int.TryParse(match.Groups[1].Value, NumberStyles.None, CultureInfo.InvariantCulture, out database)
            || !int.TryParse(match.Groups[2].Value, NumberStyles.None, CultureInfo.InvariantCulture, out element))
            throw new ArgumentException("refno must be an exact canonical =positiveInt32/nonnegativeInt32 numeric reference.", nameof(refno));
        if (traceLimit < 1 || traceLimit > MaximumTraceLimit)
            throw new ArgumentOutOfRangeException(nameof(traceLimit), "trace_limit must be between 1 and 32.");
        return new ParagonReferenceTraceRequest { Refno = refno, TraceLimit = traceLimit, Pair = new[] { database, element } };
    }

    internal static string ErrorText(string value)
    {
        if (string.IsNullOrWhiteSpace(value)) return "The SDK read failed without a message.";
        if (value.Length <= MaximumErrorLength) return value;
        int length = MaximumErrorLength - 3;
        if (char.IsHighSurrogate(value[length - 1])) length--;
        return value.Substring(0, length) + "...";
    }
}
