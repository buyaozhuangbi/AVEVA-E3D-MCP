using System;
using System.Collections.Specialized;
using E3d21AiBridge;

internal sealed class InnerRequest
{
    internal NameValueCollection QueryString;
}
internal sealed class OuterRequest
{
    internal InnerRequest Request;
}
internal sealed class MapContext
{
    internal int Calls;
    internal int DatabaseNumber;
    internal int ExtractNumber;
    internal string ExpectedContext;
    internal string SessionId;
    internal readonly object Result = new { success = false, status = (string)null, check_attempted = false };
    internal object GetSpatialMapStatus(int databaseNumber, int extractNumber, string expectedContext, string sessionId)
    {
        Calls++;
        DatabaseNumber = databaseNumber;
        ExtractNumber = extractNumber;
        ExpectedContext = expectedContext;
        SessionId = sessionId;
        return Result;
    }
}
internal static class Program
{
    private static readonly MapContext context = new MapContext();
    private static readonly string sessionId = "test-session";
    private static int assertions;

    private static object Route(NameValueCollection queryValues)
    {
        var request = new OuterRequest { Request = new InnerRequest { QueryString = queryValues } };
        object response;
        // PRODUCTION_ROUTE
        return response;
    }

    private static void Require(bool value, string reason)
    {
        assertions++;
        if (!value) throw new Exception(reason);
    }

    private static NameValueCollection Query(string number = "12345", string extract = "1")
    {
        var query = new NameValueCollection();
        if (number != null) query.Add("database_number", number);
        if (extract != null) query.Add("extract_number", extract);
        return query;
    }

    private static void Rejected(NameValueCollection query)
    {
        int before = context.Calls;
        bool rejected = false;
        try { Route(query); }
        catch (ArgumentException) { rejected = true; }
        Require(rejected && context.Calls == before, "Invalid query reached native context");
    }

    private static void Main()
    {
        foreach (string value in new[] { null, "", "0", "-1", "+1", "1.0", "true", " 1", "1 ", "2147483648" })
            Rejected(Query(value));
        foreach (string value in new[] { null, "", "-1", "+1", "1.0", "true", " 1", "2147483648" })
            Rejected(Query("1", value));
        foreach (string key in new[] { "database_number", "extract_number", "expected_context" })
        {
            var query = Query();
            if (key == "expected_context") query.Add(key, "");
            query.Add(key, "1");
            Rejected(query);
        }
        foreach (string key in new[] { "dry_run", "rebuild", "capture_after", "DATABASE_NUMBER", null })
        {
            var query = key == "DATABASE_NUMBER" ? Query(null) : Query();
            query.Add(key, "1");
            Rejected(query);
        }
        foreach (string value in new[] { "bad", new string('a', 63), new string('A', 64), new string('g', 64), new string('0', 65) })
        {
            var query = Query(); query.Add("expected_context", value); Rejected(query);
        }
        object result = Route(Query());
        Require(context.DatabaseNumber == 12345 && context.ExtractNumber == 1 && context.ExpectedContext == "" &&
            context.SessionId == sessionId, "Required database identity changed in transport");
        Require(object.ReferenceEquals(result.GetType().GetProperty("result").GetValue(result), context.Result),
            "Partial native result was replaced");
        var bounded = Query("2147483647", "0"); bounded.Add("expected_context", new string('a', 64));
        Route(bounded);
        Require(context.DatabaseNumber == int.MaxValue && context.ExtractNumber == 0 && context.ExpectedContext == new string('a', 64),
            "Boundary or context was not passed through");
        Console.WriteLine("Spatial-map HTTP production checks passed: " + assertions);
    }
}
