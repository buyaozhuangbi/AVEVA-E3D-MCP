using System;
using System.Collections.Specialized;
using System.Globalization;
using System.Text.RegularExpressions;

namespace E3d21AiBridge;

// A strict, read-only query contract. No silent defaults for supplied invalid values.
internal sealed class DatabaseStateRequest
{
    internal int Offset;
    internal int Limit;
    internal string ExpectedContext;

    internal static DatabaseStateRequest Parse(NameValueCollection query)
    {
        if (query == null) throw new ArgumentNullException("query");
        foreach (string key in query.AllKeys)
        {
            if (key != "offset" && key != "limit" && key != "expected_context")
                throw new ArgumentException("Unknown database state parameter.");
            string[] values = query.GetValues(key);
            if (values == null || values.Length != 1)
                throw new ArgumentException("Database state parameters must occur exactly once.");
        }
        var result = new DatabaseStateRequest
        {
            Offset = ReadInteger(query, "offset", 0),
            Limit = ReadInteger(query, "limit", 50),
            ExpectedContext = query["expected_context"] ?? ""
        };
        Validate(result.Offset, result.Limit, result.ExpectedContext);
        return result;
    }

    internal static void Validate(int offset, int limit, string expectedContext)
    {
        if (offset < 0 || offset > 100000) throw new ArgumentOutOfRangeException("offset");
        if (limit < 1 || limit > 200) throw new ArgumentOutOfRangeException("limit");
        if (expectedContext == null || expectedContext.Length != 0 &&
            !Regex.IsMatch(expectedContext, "\\A[a-f0-9]{64}\\z", RegexOptions.CultureInvariant))
            throw new ArgumentException("expected_context must be the context_id from a previous database-state response.");
        if (offset > 0 && expectedContext.Length == 0)
            throw new ArgumentException("expected_context is required when offset is greater than zero.");
    }

    internal static void ValidateProjectCatalogProbe(string expectedContext, ref string team, ref string name, int? databaseNumber)
    {
        Validate(0, 1, expectedContext);
        if (expectedContext.Length == 0)
            throw new ArgumentException("expected_context is required for a project database catalog probe.");
        team = ValidateCatalogComponent(team, "team");
        name = ValidateCatalogComponent(name, "name");
        if (team == "TRANSACTION")
            throw new ArgumentException("The TRANSACTION team is reserved for TRAN databases.");
        if (databaseNumber.HasValue && (databaseNumber.Value < 1 || databaseNumber.Value > 255000))
            throw new ArgumentOutOfRangeException("database_number", "database_number must be an integer from 1 through 255000.");
    }

    private static string ValidateCatalogComponent(string value, string parameter)
    {
        if (value == null || value.Length > 32 ||
            !Regex.IsMatch(value, "\\A[A-Za-z][A-Za-z0-9_-]{0,31}\\z", RegexOptions.CultureInvariant) ||
            StringComparer.OrdinalIgnoreCase.Equals(value, "UNSET"))
            throw new ArgumentException(parameter + " must be one ASCII letter-led component of at most 32 letters, digits, underscores or hyphens.");
        return value.ToUpperInvariant();
    }

    private static int ReadInteger(NameValueCollection query, string key, int fallback)
    {
        string value = query[key];
        if (value == null) return fallback;
        int parsed;
        if (!int.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out parsed))
            throw new ArgumentException(key + " must be an unsigned decimal integer.");
        return parsed;
    }
}
