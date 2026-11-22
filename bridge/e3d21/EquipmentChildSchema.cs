using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace E3d21AiBridge;

// Optional native read capability. The caller supplies its server-owned session
// identity; this reader never performs creation, preview, or model mutation.
public interface IE3d21EquipmentChildSchemaContext
{
    object ReadEquipmentChildSchema(string parentRefno, string elementType, string readTargetRefno,
        IList<string> attributes, int offset, int limit, string sessionId);
}

internal sealed class EquipmentChildSchemaRequest
{
    internal const int MaximumAttributes = 20;
    internal const int MaximumPageSize = 200;
    // SystemAttributes materializes its array inside the SDK before this reader
    // can inspect Length. This is an inspection cutoff, not a native memory cap.
    internal const int MaximumNativeArrayInspection = 4096;
    internal const int MaximumTextLength = 1024;
    private static readonly Regex SdkName = new Regex(@"\A[A-Za-z0-9_:]{1,64}\z", RegexOptions.CultureInvariant);
    private static readonly Regex ReferencePair = new Regex(@"\A=([0-9]+)/([0-9]+)\z", RegexOptions.CultureInvariant);
    internal string ParentRefno, ElementType, ReadTargetRefno;
    internal List<string> Attributes;
    internal int Offset, Limit;

    internal static EquipmentChildSchemaRequest Validate(string parentRefno, string elementType,
        string readTargetRefno, IList<string> attributes, int offset, int limit)
    {
        string parent = Reference(parentRefno, "parent_refno");
        string type = Name(elementType, "element_type");
        // Only null means omitted. Empty/CE/wildcard targets must never become
        // an implicit exemplar, nor cause a search for a convenient instance.
        string target = readTargetRefno == null ? null : Reference(readTargetRefno, "read_target_refno");
        if (attributes == null || attributes.Count > MaximumAttributes)
            throw new ArgumentException("attributes must be an array of at most 20 explicit SDK names.");
        var names = new List<string>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (string attribute in attributes)
        {
            string name = Name(attribute, "attributes");
            if (!seen.Add(name)) throw new ArgumentException("attributes must be unique after normalization.");
            names.Add(name);
        }
        if (offset < 0 || offset > 100000) throw new ArgumentOutOfRangeException("offset");
        if (limit < 1 || limit > MaximumPageSize) throw new ArgumentOutOfRangeException("limit");
        return new EquipmentChildSchemaRequest
        {
            ParentRefno = parent, ElementType = type, ReadTargetRefno = target,
            Attributes = names, Offset = offset, Limit = limit
        };
    }

    private static string Name(string value, string field)
    {
        string text = (value ?? "").Trim();
        if (!SdkName.IsMatch(text)) throw new ArgumentException(field + " must contain explicit ASCII SDK names.");
        return text.ToUpperInvariant();
    }

    private static string Reference(string value, string field)
    {
        string text = (value ?? "").Trim();
        if (text.Length < 2 || text.Length > 256 || text.IndexOfAny(new[] { '*', '?' }) >= 0)
            throw new ArgumentException(field + " must explicitly identify /NAME or =database/element, without wildcards.");
        foreach (char character in text)
            if (char.IsWhiteSpace(character) || char.IsControl(character))
                throw new ArgumentException(field + " must not contain whitespace or controls.");
        if (text[0] == '/') return text;
        Match match = ReferencePair.Match(text);
        int database, element;
        if (!match.Success || !int.TryParse(match.Groups[1].Value, NumberStyles.None, CultureInfo.InvariantCulture, out database)
            || database <= 0 || !int.TryParse(match.Groups[2].Value, NumberStyles.None, CultureInfo.InvariantCulture, out element)
            || element < 0)
            throw new ArgumentException(field + " must explicitly identify /NAME or =database/element; CE is not a default.");
        return "=" + database.ToString(CultureInfo.InvariantCulture) + "/" + element.ToString(CultureInfo.InvariantCulture);
    }

    internal static string ErrorText(string value)
    {
        string text = string.IsNullOrEmpty(value) ? "SDK read failed." : value;
        if (text.Length <= MaximumTextLength) return text;
        int count = MaximumTextLength;
        if (char.IsHighSurrogate(text[count - 1])) count--;
        return text.Substring(0, count);
    }
}
