using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace E3d21AiBridge;

// Read-only numeric targets only. The caller never supplies the bridge session.
public interface IE3d21PipingSpecificationReadContext
{
    object ReadPipingSpecification(string refno, string expectedType, string databaseType,
        int maxDepth, int limit, IList<string> attributes, string sessionId);
}

internal sealed class PipingSpecificationReadRequest
{
    private static readonly Regex TypeName = new Regex(@"\A[A-Z][A-Z0-9_]{0,63}\z", RegexOptions.CultureInvariant);
    private static readonly Regex NumericReference = new Regex(@"\A=([0-9]+)/([0-9]+)\z", RegexOptions.CultureInvariant);
    internal ParagonCatalogueManifestRequest Scope;
    internal string ExpectedType, DatabaseType;
    internal int DatabaseTypeCode;

    internal static PipingSpecificationReadRequest Validate(string refno, string expectedType,
        string databaseType, int maxDepth, int limit, IList<string> attributes, string sessionId)
    {
        // Resolve no names, CE, patterns, typed aliases or global search fallbacks.
        if (refno == null || refno.Length > 256 || !NumericReference.IsMatch(refno.Trim()))
            throw new ArgumentException("refno must be an explicit numeric =database/element reference.", "refno");
        // Reuse the numeric normalization, depth 1..16, total-row 1..100,
        // ten-attribute and 500-cell limits of the existing Catalogue reader.
        var scope = ParagonCatalogueManifestRequest.Validate(refno, maxDepth, limit, attributes);
        string type = (expectedType ?? "").Trim().ToUpperInvariant();
        if (!TypeName.IsMatch(type)) throw new ArgumentException("An explicit native expected_type ShortName is required.");
        int code;
        string canonical;
        switch ((databaseType ?? "").Trim().ToUpperInvariant())
        {
            case "DESIGN": canonical = "Design"; code = 1; break;
            case "CATALOGUE": canonical = "Catalogue"; code = 2; break;
            case "PROPERTY": canonical = "Property"; code = 4; break;
            case "DICTIONARY": canonical = "Dictionary"; code = 8; break;
            default: throw new ArgumentException("database_type must explicitly be Design, Catalogue, Property or Dictionary.");
        }
        if (string.IsNullOrWhiteSpace(sessionId) || sessionId.Length > 256)
            throw new ArgumentException("A bridge session identity is required.");
        return new PipingSpecificationReadRequest { Scope = scope, ExpectedType = type,
            DatabaseType = canonical, DatabaseTypeCode = code };
    }
}
