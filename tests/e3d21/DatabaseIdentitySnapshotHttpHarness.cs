using System;
using System.Collections.Specialized;
using System.Linq;
using E3d21AiBridge;

internal sealed class SnapshotInnerRequest { internal NameValueCollection QueryString; }
internal sealed class SnapshotOuterRequest { internal SnapshotInnerRequest Request; }
internal sealed class SnapshotContext
{
    internal int Calls;
    internal string ExpectedContext;
    internal string SessionId;
    internal readonly object Result = new { success = true, complete = true, identity_snapshot_sha256 = new string('a', 64) };
    internal object GetDatabaseIdentitySnapshot(string expectedContext, string sessionId)
    {
        Calls++;
        ExpectedContext = expectedContext;
        SessionId = sessionId;
        return Result;
    }
}

internal static class DatabaseIdentitySnapshotHttpHarness
{
    private static readonly SnapshotContext context = new SnapshotContext();
    private const string sessionId = "test-session";
    private static int assertions;

    private static object Route(NameValueCollection values)
    {
        var request = new SnapshotOuterRequest { Request = new SnapshotInnerRequest { QueryString = values } };
        object response;
        // PRODUCTION_ROUTE
        return response;
    }

    private static void Check(bool value, string label)
    {
        assertions++;
        if (!value) throw new Exception(label);
    }

    private static void Reject(NameValueCollection query)
    {
        int before = context.Calls;
        bool rejected = false;
        try { Route(query); }
        catch (ArgumentException) { rejected = true; }
        Check(rejected && context.Calls == before, "Invalid query reached the native snapshot method");
    }

    private static NameValueCollection Valid(string contextId = null)
    {
        var query = new NameValueCollection();
        query.Add("expected_context", contextId ?? new string('a', 64));
        return query;
    }

    private static void Main()
    {
        Reject(new NameValueCollection());
        Reject(new NameValueCollection { { "expected_context", "bad" } });
        Reject(new NameValueCollection { { "expected_context", new string('A', 64) } });
        Reject(new NameValueCollection { { "expected_context", new string('a', 65) } });
        var duplicate = Valid(); duplicate.Add("expected_context", new string('b', 64)); Reject(duplicate);
        var unknown = Valid(); unknown.Add("offset", "0"); Reject(unknown);
        object response = Route(Valid());
        Check(context.Calls == 1 && context.ExpectedContext == new string('a', 64) && context.SessionId == sessionId,
            "Valid context and session forwarded exactly once");
        Check(object.ReferenceEquals(response.GetType().GetProperty("result").GetValue(response), context.Result),
            "Native snapshot result is preserved");
        Console.WriteLine("Database identity snapshot HTTP checks passed: " + assertions);
    }
}
