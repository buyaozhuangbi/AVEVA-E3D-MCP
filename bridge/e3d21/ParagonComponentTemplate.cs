using System;
using System.Globalization;
using System.Text.RegularExpressions;

namespace E3d21AiBridge;

public interface IE3d21ParagonComponentTemplateContext
{
    object ReadParagonComponentTemplate(string refno, string sessionId);
}

// Fixed, bounded observations of one existing Catalogue SCOM, never a clone
// description, Design resolver, creation policy, or permission check.
internal sealed class ParagonComponentTemplateRequest
{
    internal const int MaximumParameters = 128;
    internal const int MaximumReferenceItems = 16;
    internal const int MaximumNativeArrayInspection = 4096;
    internal const int MaximumTextLength = 1024;
    internal const int MaximumErrorLength = 4096;
    internal static readonly string[] ReferenceAttributes = { "PTREF", "GMREF", "DTREF", "BLRFA", "NGMREF", "PSTREF", "GSTREF" };
    private static readonly Regex NumericReference = new Regex(@"\A=([1-9][0-9]*)/(0|[1-9][0-9]*)\z", RegexOptions.CultureInvariant);
    internal string Refno;
    internal int[] Pair;

    internal static ParagonComponentTemplateRequest Validate(string refno)
    {
        Match match = NumericReference.Match(refno ?? "");
        int database, element;
        if (refno == null || refno.Length > 32 || !match.Success
            || !int.TryParse(match.Groups[1].Value, NumberStyles.None, CultureInfo.InvariantCulture, out database)
            || !int.TryParse(match.Groups[2].Value, NumberStyles.None, CultureInfo.InvariantCulture, out element))
            throw new ArgumentException("refno must be an exact canonical =positiveInt32/nonnegativeInt32 numeric reference.");
        return new ParagonComponentTemplateRequest { Refno = refno, Pair = new[] { database, element } };
    }

    internal static string Reference(int[] pair)
    {
        if (pair == null || pair.Length != 2 || pair[0] < 1 || pair[1] < 0)
            throw new InvalidOperationException("The SDK returned an invalid numeric DBREF.");
        return "=" + pair[0].ToString(CultureInfo.InvariantCulture) + "/" + pair[1].ToString(CultureInfo.InvariantCulture);
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
