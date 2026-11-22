using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace E3d21AiBridge;

public interface IE3d21ParagonCatalogueManifestContext
{
    object ReadParagonCatalogueManifest(string refno, int maxDepth, int limit, IList<string> attributes, string sessionId);
}

// Catalogue inspection does not create an Admin database or authorize a PARAGON
// NEW operation. No Design-only resolver or model-edit policy is used here.
internal sealed class ParagonCatalogueManifestRequest
{
    internal const int DefaultMaxDepth = 8;
    internal const int DefaultLimit = 64;
    internal const int MaximumDepth = 16;
    internal const int MaximumLimit = 100;
    internal const int MaximumAttributes = 10;
    internal const int MaximumCells = 500;
    internal const int MaximumTextLength = 4096;
    private static readonly Regex NumericReference = new Regex(@"\A=([0-9]+)/([0-9]+)\z", RegexOptions.CultureInvariant);
    private static readonly Regex AttributeName = new Regex(@"\A[A-Za-z0-9_:]{1,64}\z", RegexOptions.CultureInvariant);

    internal string Refno;
    internal int MaxDepth;
    internal int Limit;
    internal List<string> Attributes;

    internal static ParagonCatalogueManifestRequest Validate(string refno, int maxDepth, int limit, IList<string> attributes)
    {
        if (refno == null || refno.Length > 256)
            throw new ArgumentException("refno must explicitly identify /NAME or =database/element, up to 256 characters.");
        string target = refno.Trim();
        if (target.Length < 2 || target.IndexOfAny(new[] { '*', '?' }) >= 0)
            throw new ArgumentException("refno must be a literal /NAME or =database/element reference.");
        foreach (char c in target)
            if (char.IsWhiteSpace(c) || char.IsControl(c))
                throw new ArgumentException("refno must not contain whitespace or control characters.");
        if (target[0] != '/')
        {
            Match match = NumericReference.Match(target);
            int database, element;
            if (!match.Success || !int.TryParse(match.Groups[1].Value, NumberStyles.None, CultureInfo.InvariantCulture, out database)
                || database < 1 || !int.TryParse(match.Groups[2].Value, NumberStyles.None, CultureInfo.InvariantCulture, out element)
                || element < 0)
                throw new ArgumentException("refno requires a positive Int32 database and a nonnegative Int32 element number.");
            target = "=" + database.ToString(CultureInfo.InvariantCulture) + "/" + element.ToString(CultureInfo.InvariantCulture);
        }
        if (maxDepth < 1 || maxDepth > MaximumDepth) throw new ArgumentOutOfRangeException("max_depth");
        if (limit < 1 || limit > MaximumLimit) throw new ArgumentOutOfRangeException("limit");
        IList<string> fields = attributes ?? new string[0];
        if (fields.Count > MaximumAttributes || limit * fields.Count > MaximumCells)
            throw new ArgumentException("At most 10 attributes and 500 requested row/attribute cells are allowed, including the root.");
        var names = new List<string>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (string field in fields)
        {
            string name = (field ?? "").Trim();
            if (!AttributeName.IsMatch(name)) throw new ArgumentException("Use explicit SDK attribute names of 1 to 64 ASCII characters.");
            name = name.ToUpperInvariant();
            if (!seen.Add(name)) throw new ArgumentException("attributes must be unique after normalization.");
            names.Add(name);
        }
        return new ParagonCatalogueManifestRequest { Refno = target, MaxDepth = maxDepth, Limit = limit, Attributes = names };
    }

    internal static string Reference(int[] pair)
    {
        if (pair == null || pair.Length != 2 || pair[0] < 1 || pair[1] < 0)
            throw new InvalidOperationException("The SDK returned no valid database reference.");
        return "=" + pair[0].ToString(CultureInfo.InvariantCulture) + "/" + pair[1].ToString(CultureInfo.InvariantCulture);
    }

    internal static string ErrorText(string value)
    {
        if (string.IsNullOrWhiteSpace(value)) return "The SDK read failed without a message.";
        if (value.Length <= MaximumTextLength) return value;
        int length = MaximumTextLength - 3;
        if (char.IsHighSurrogate(value[length - 1])) length--;
        return value.Substring(0, length) + "...";
    }
}
