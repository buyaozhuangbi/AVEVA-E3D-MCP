using System;
using System.Collections.Generic;

namespace E3d21AiBridge;

public interface IE3d21ParagonCatalogueSchemaContext
{
    object ReadParagonCatalogueSchema(string refno, IList<string> attributes, bool includeConstraints, string sessionId);
}

internal sealed class ParagonCatalogueSchemaRequest
{
    internal const int MaximumAttributes = 10;
    internal const int MaximumChildTypes = 200;
    internal const int MaximumDatabaseTypes = 32;
    internal const int MaximumConstraintItems = 100;
    internal const int MaximumConstraintTextLength = 1024;
    internal string Refno;
    internal List<string> Attributes;
    internal bool IncludeConstraints;

    internal static ParagonCatalogueSchemaRequest Validate(string refno, IList<string> attributes, bool includeConstraints)
    {
        // This validator is pure managed input normalization, with no Design
        // resolver, edit policy or implicit CE. The row budget is irrelevant here.
        var input = ParagonCatalogueManifestRequest.Validate(refno, 1, 1, attributes);
        if (includeConstraints && input.Attributes.Count == 0)
            throw new ArgumentException("include_constraints requires 1 to 10 explicitly named attributes on the current target.");
        return new ParagonCatalogueSchemaRequest
        {
            Refno = input.Refno, Attributes = input.Attributes, IncludeConstraints = includeConstraints
        };
    }
}
