using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using Aveva.Core.Database;
using E3d21AiBridge;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Aveva.Core.Database
{
    public sealed class Project
    {
        internal static Project Current = new Project();
        internal string Value = "PROJECT";
        public static Project CurrentProject { get { return Scenario.Read("project.current", Current); } }
        public string Name { get { return Scenario.Read("project.name", Value); } }
    }

    public sealed class MDB
    {
        internal static MDB Current = new MDB();
        internal string Value = "MDB";
        internal Db[] Databases;
        public static MDB CurrentMDB { get { return Scenario.Read("mdb.current", Current); } }
        public string Name { get { return Scenario.Read("mdb.name", Value); } }
        public Db[] GetDBArray() { Scenario.DbArrayCalls++; return Scenario.Read("mdb.db_array", Databases); }
    }

    public sealed class Db
    {
        private readonly string path;
        internal string Value;
        internal int NumberValue;
        internal Db(string path, string name, int number) { this.path = path; Value = name; NumberValue = number; }
        public string Name { get { return Scenario.Read(path + ".name", Value); } }
        public int Number { get { return Scenario.Read(path + ".number", NumberValue); } }
    }

    public sealed class DbElementType
    {
        internal readonly string Value;
        internal DbElementType(string value) { Value = value; }
    }

    public static class DbElementTypeInstance
    {
        public static readonly DbElementType DB = new DbElementType("DB");
        public static readonly DbElementType TEAM = new DbElementType("TEAM");
    }

    public sealed class DbAttribute
    {
        internal readonly string Value;
        internal DbAttribute(string value) { Value = value; }
        public static DbAttribute GetDbAttribute(string name)
        {
            return Scenario.Read("attribute." + name,
                name == "NAMEDB" ? DbAttributeInstance.NAMEDB :
                name == "DBNO" ? DbAttributeInstance.DBNO :
                name == "NAMN" ? DbAttributeInstance.NAMN : null);
        }
    }

    public static class DbAttributeInstance
    {
        public static readonly DbAttribute NAMEDB = new DbAttribute("NAMEDB");
        public static readonly DbAttribute DBNO = new DbAttribute("DBNO");
        public static readonly DbAttribute NAMN = new DbAttribute("NAMN");
    }

    public sealed class DbElement
    {
        internal readonly string Path;
        internal string NameValue;
        internal int NumberValue;
        internal bool IsTeam;
        internal DbElement(string path, string name, int number = 0, bool team = false)
        { Path = path; NameValue = name; NumberValue = number; IsTeam = team; }
        public bool IsNull { get { return Scenario.Read(Path + ".is_null", false); } }
        public int[] Ref { get { return Scenario.Read(Path + ".ref", new[] { 1, 2 }); } }
        public bool GetValidString(DbAttribute attribute, ref string value)
        {
            bool available = Scenario.Read(Path + "." + attribute.Value + ".valid", true);
            string fallback = NameValue;
            value = Scenario.Read(Path + "." + attribute.Value, fallback);
            return available;
        }
        public bool GetValidInteger(DbAttribute attribute, ref int value)
        {
            bool available = Scenario.Read(Path + "." + attribute.Value + ".valid", true);
            value = Scenario.Read(Path + "." + attribute.Value, NumberValue);
            return available;
        }
    }

    public sealed class DbSystem
    {
        public static readonly DbSystem Instance = new DbSystem();
        internal static DbElement[] Databases;
        internal static DbElement[] Teams;
        internal static DbElement[] WorldDatabases;
        internal static DbElement[] WorldTeams;
        private DbSystem() { }
        public DbElement[] GetAll(DbElementType type)
        {
            if (type == DbElementTypeInstance.DB) { Scenario.DbArrayCalls++; return Scenario.Read("catalog.db_array", Databases); }
            if (type == DbElementTypeInstance.TEAM) { Scenario.TeamArrayCalls++; return Scenario.Read("catalog.team_array", Teams); }
            throw new ArgumentException("Unexpected catalog element type.");
        }
        public DbElement[] GetAllWorldLevel(DbElementType type)
        {
            if (type == DbElementTypeInstance.DB) { Scenario.WorldDbArrayCalls++; return Scenario.Read("world.db_array", WorldDatabases); }
            if (type == DbElementTypeInstance.TEAM) { Scenario.WorldTeamArrayCalls++; return Scenario.Read("world.team_array", WorldTeams); }
            throw new ArgumentException("Unexpected world-level catalog element type.");
        }
    }

    public static class CurrentElement
    {
        public static DbElement Element { get { return Scenario.Read("ce.element", Scenario.Ce); } }
    }
}

internal static class Scenario
{
    internal static readonly List<string> Reads = new List<string>();
    internal static readonly Dictionary<string, object> Overrides = new Dictionary<string, object>();
    internal static Action<string> OnRead;
    internal static E3d21Context Context;
    internal static DbElement Ce;
    internal static bool InUi;
    internal static int Dispatches, InUiReads, ReadsAfterFault, RouteCalls, DbArrayCalls, TeamArrayCalls,
        WorldDbArrayCalls, WorldTeamArrayCalls;
    internal static string FaultAt;

    internal static T Read<T>(string path, T fallback)
    {
        if (Context != null && Context.HasFault)
        {
            ReadsAfterFault++;
            throw new Exception("SDK read attempted after a native fault: " + path);
        }
        Reads.Add(path);
        if (InUi) InUiReads++;
        else throw new Exception("SDK read occurred outside UI dispatch: " + path);
        if (OnRead != null) OnRead(path);
        if (FaultAt == path) Context.Fault();
        object value;
        if (!Overrides.TryGetValue(path, out value)) return fallback;
        if (value is Exception) throw (Exception)value;
        return (T)value;
    }

    internal static void Reset()
    {
        Reads.Clear(); Overrides.Clear(); OnRead = null; FaultAt = null;
        InUi = false; Dispatches = InUiReads = ReadsAfterFault = RouteCalls = DbArrayCalls = TeamArrayCalls =
            WorldDbArrayCalls = WorldTeamArrayCalls = 0;
        Project.Current = new Project(); MDB.Current = new MDB();
        Project.Current.Value = "PROJECT"; MDB.Current.Value = "MDB";
        MDB.Current.Databases = new[] { new Db("mdb.db[0]", "PIPING/EXISTING", 10), new Db("mdb.db[1]", "OTHER/LIVE", 20) };
        DbSystem.Databases = new[] {
            new DbElement("catalog.db[0]", "PIPING/EXISTING", 10),
            new DbElement("catalog.db[1]", "/PIPING/NEWDB", 30),
            new DbElement("catalog.db[2]", "OTHER/OTHERDB", 200),
            new DbElement("catalog.db[3]", "/OTHER/NEWDB", 40)
        };
        DbSystem.Teams = new[] {
            new DbElement("catalog.team[0]", "*PIPING", team: true),
            new DbElement("catalog.team[1]", "OTHER", team: true)
        };
        DbSystem.WorldDatabases = new[] {
            new DbElement("world.db[0]", "PIPING/EXISTING", 10),
            new DbElement("world.db[1]", "/PIPING/NEWDB", 30),
            new DbElement("world.db[2]", "OTHER/OTHERDB", 200),
            new DbElement("world.db[3]", "/OTHER/NEWDB", 40)
        };
        DbSystem.WorldTeams = new[] {
            new DbElement("world.team[0]", "*PIPING", team: true),
            new DbElement("world.team[1]", "OTHER", team: true)
        };
        Ce = new DbElement("ce", "CE"); Context = new E3d21Context();
    }
}

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;
        private readonly Action<Action> invokeOnUi;

        public E3d21Context()
        {
            invokeOnUi = action => { Scenario.Dispatches++; Scenario.InUi = true; try { action(); } finally { Scenario.InUi = false; } };
        }

        internal bool HasFault { get { return nativeFaultState.HasFault; } }
        internal void Fault() { nativeFaultState.Observe("Unhandled Database error 538 from FakeCatalog", ""); }
        private bool IsCommandActive() { Scenario.RouteCalls++; ThrowIfNativeFaulted(); return false; }
        // PRODUCTION_CONTEXT_FIELDS
        // PRODUCTION_NATIVE_HELPERS
        // PRODUCTION_CONTEXT_METHODS
    }
}

internal sealed class CatalogHttpRequest
{
    internal Stream InputStream;
    internal Encoding ContentEncoding = Encoding.UTF8;
    internal NameValueCollection QueryString = new NameValueCollection(StringComparer.Ordinal);
}

internal sealed class CatalogHttpResponse
{
    internal int StatusCode = 200;
    internal object Value;
}

internal sealed class CatalogHttpContext
{
    internal CatalogHttpRequest Request;
    internal readonly CatalogHttpResponse Response = new CatalogHttpResponse();
}

internal static class CatalogRoute
{
    internal const string SessionId = "fixture-session";
    private const int MaximumJsonRequestBodyBytes = 1024 * 1024;

    internal static async Task<CatalogHttpResponse> RunAsync(string json, E3d21Context context,
        NameValueCollection query = null, bool handleErrors = false)
    {
        using (var stream = new MemoryStream(Encoding.UTF8.GetBytes(json)))
        {
            var request = new CatalogHttpContext { Request = new CatalogHttpRequest { InputStream = stream } };
            if (query != null) request.Request.QueryString = query;
            try { await RunBranchAsync(request, context); }
            catch (Exception ex) when (handleErrors)
            { request.Response.StatusCode = ex is ArgumentException || ex is JsonException ? 400 : 500; request.Response.Value = ex.Message; }
            return request.Response;
        }
    }

    private static async Task RunBranchAsync(CatalogHttpContext request, E3d21Context context)
    {
        string sessionId = SessionId;
        object response;
        // PRODUCTION_ROUTE
        await WriteJsonAsync(request.Response, response, 200);
    }

    private static Task WriteJsonAsync(CatalogHttpResponse response, object value, int statusCode)
    {
        response.Value = JToken.Parse(JsonConvert.SerializeObject(value));
        response.StatusCode = statusCode;
        return Task.CompletedTask;
    }
    // PRODUCTION_HTTP_HELPERS
}

internal static class ProjectCatalogProbeHarness
{
    private static int checks;
    private const string ContextId = "51a9440f496c9971359690461f975c15e693d60479e291db1eb79cefc81ab684";

    private static void Check(bool value, string label)
    {
        checks++;
        if (!value) throw new Exception("Check failed: " + label + "\nLast SDK reads: " +
            string.Join(", ", Scenario.Reads.Skip(Math.Max(0, Scenario.Reads.Count - 20))));
    }

    private static JObject Route(string body, E3d21Context context, NameValueCollection query = null)
    {
        CatalogHttpResponse response = CatalogRoute.RunAsync(body, context, query).GetAwaiter().GetResult();
        Check(response.StatusCode == 200, "valid route returned HTTP " + response.StatusCode + ": " + response.Value);
        return (JObject)response.Value;
    }

    private static CatalogHttpResponse Invalid(string body, NameValueCollection query = null)
    {
        Scenario.Reset();
        CatalogHttpResponse response = CatalogRoute.RunAsync(body, Scenario.Context, query, handleErrors: true).GetAwaiter().GetResult();
        Check(response.StatusCode == 400, "invalid route returned HTTP " + response.StatusCode);
        Check(Scenario.Dispatches == 0 && Scenario.Reads.Count == 0 && Scenario.RouteCalls == 0,
            "invalid input never reaches a native read or the context method");
        return response;
    }

    private static CatalogHttpResponse Failed(string body)
    {
        CatalogHttpResponse response = CatalogRoute.RunAsync(body, Scenario.Context, handleErrors: true).GetAwaiter().GetResult();
        Check(response.StatusCode == 500, "incomplete native observation returned HTTP " + response.StatusCode);
        Check(response.Value is string, "incomplete native observation has no successful probe result");
        return response;
    }

    private static object Result(JObject response) { return response["result"] as JObject; }
    private static JObject ResultObject(JObject response) { return (JObject)response["result"]; }
    private static JObject Counts(JObject result) { return (JObject)result["counts"]; }
    private static JArray Errors(JObject result) { return result["read_errors"] as JArray ?? new JArray(); }
    private static bool HasError(JObject result, string code)
    { return Errors(result).OfType<JObject>().Any(item => (string)(item["code"] ?? item["stage"]) == code); }
    private static bool PathRead(string path) { return Scenario.Reads.Contains(path); }

    private static string Request(string team = "piping", string name = "newdb", int? number = 200, string expected = ContextId)
    {
        var body = new JObject { ["team"] = team, ["name"] = name, ["expected_context"] = expected };
        if (number.HasValue) body["database_number"] = number.Value;
        return body.ToString(Newtonsoft.Json.Formatting.None);
    }

    private static void StrictPayloadAndBounds()
    {
        Scenario.Reset();
        JObject envelope = Route(Request(), Scenario.Context);
        Check((bool)envelope["ok"] && (string)envelope["version"] == "E3D21" &&
            (string)envelope["session_id"] == CatalogRoute.SessionId, "route envelope carries version and session");
        Check(Scenario.RouteCalls == 2 && Scenario.Dispatches == 1, "valid request performs guarded entry checks around one native-context operation");

        foreach (string bad in new[] {
            "{}", "[]", "null", "{", Request("", "newdb"), Request("1PIPING", "newdb"),
            Request(new string('A', 33), "newdb"), Request("TRANSACTION", "newdb"), Request("UNSET", "newdb"),
            Request("piping", "UNSET"), Request("piping", "new/db"), Request("piping", "newdb", 0),
            Request("piping", "newdb", 255001), Request("piping", "newdb", -1),
            Request("piping", "newdb", 1, ""), Request("piping", "newdb", 1, new string('A', 64)),
            "{\"team\":\"PIPING\",\"name\":\"NEWDB\",\"database_number\":1.5,\"expected_context\":\"" + ContextId + "\"}",
            "{\"team\":\"PIPING\",\"name\":\"NEWDB\",\"database_number\":null,\"expected_context\":\"" + ContextId + "\"}",
            "{\"team\":\"PIPING\",\"team\":\"OTHER\",\"name\":\"NEWDB\",\"expected_context\":\"" + ContextId + "\"}",
            "{\"team\":\"PIPING\",\"name\":\"NEWDB\",\"expected_context\":\"" + ContextId + "\",\"extra\":true}"
        }) Invalid(bad);

        Invalid(Request(), new NameValueCollection { { "unexpected", "1" } });
        Invalid(new string(' ', 4097));
        Scenario.Reset();
        CatalogHttpResponse tooLong = CatalogRoute.RunAsync("{\"team\":\"" + new string('A', 4100) + "\"}",
            Scenario.Context, handleErrors: true).GetAwaiter().GetResult();
        Check(tooLong.StatusCode == 400 && Scenario.Dispatches == 0 && Scenario.Reads.Count == 0,
            "oversized payload is rejected before native dispatch");

        Scenario.Reset();
        string maximumBody = Request(new string('A', 32), "B", 255000);
        maximumBody += new string(' ', 4096 - Encoding.UTF8.GetByteCount(maximumBody));
        JObject maximum = Route(maximumBody, Scenario.Context);
        JObject maximumCandidate = ResultObject(maximum)["candidate"] as JObject;
        Check(maximumBody.Length == 4096 && (string)maximumCandidate["team"] == new string('A', 32) &&
            (int)maximumCandidate["database_number"] == 255000,
            "the payload, component and database-number upper boundaries are inclusive");

        Scenario.Reset(); string team = "piping", name = "newdb";
        DatabaseStateRequest.ValidateProjectCatalogProbe(ContextId, ref team, ref name, 255000);
        Check(team == "PIPING" && name == "NEWDB", "valid component normalization and inclusive number bound");
    }

    private static void MaterializedResultsAndMatches()
    {
        Scenario.Reset();
        JObject result = ResultObject(Route(Request(), Scenario.Context));
        JObject candidate = (JObject)result["candidate"];
        Check((bool)result["success"] && (bool)result["available"], "complete catalogs with positive controls are a successful observation");
        Check((string)candidate["team"] == "PIPING" && (string)candidate["name"] == "NEWDB" &&
            (string)candidate["full_database_name"] == "PIPING/NEWDB", "candidate is canonicalized once at the route boundary");
        Check((int)result["database_match_count"] == 2 && (int)result["team_match_count"] == 1,
            "name and database-number collisions and an existing team are both reported");
        JArray matches = (JArray)result["database_matches"];
        Check(matches.Count == 2 && matches.OfType<JObject>().Any(row => (string)row["name"] == "PIPING/NEWDB" &&
            (bool?)row["name_match"] == true && (bool?)row["database_number_match"] == false) &&
            matches.OfType<JObject>().Any(row => (string)row["name"] == "OTHER/OTHERDB" &&
            (bool?)row["name_match"] == false && (bool?)row["database_number_match"] == true),
            "canonical names and independent collision flags are materialized for all matches");
        Check(!PathRead("catalog.db_array[0]") && DbSystem.Databases.Length == 4 &&
            Scenario.Reads.Count(path => path == "catalog.db_array") == 1 &&
            Scenario.Reads.Count(path => path == "catalog.team_array") == 1,
            "native catalog arrays are fetched once and each materialized SDK element is inspected by fields");
        Check(Enumerable.Range(0, DbSystem.Databases.Length).All(index =>
            PathRead("catalog.db[" + index + "].is_null") && PathRead("catalog.db[" + index + "].NAMEDB") &&
            PathRead("catalog.db[" + index + "].DBNO")) &&
            Enumerable.Range(0, DbSystem.Teams.Length).All(index =>
                PathRead("catalog.team[" + index + "].is_null") && PathRead("catalog.team[" + index + "].NAMN")),
            "every materialized SDK DB and TEAM row is inspected through its native fields");
        Check((int)Counts(result)["catalog_database_inspected_count"] == DbSystem.Databases.Length &&
            (int)Counts(result)["catalog_team_inspected_count"] == DbSystem.Teams.Length &&
            (int)Counts(result)["mdb_database_positive_control_count"] > 0 &&
            (int)Counts(result)["mdb_team_positive_control_count"] > 0,
            "counts cover inspected arrays and both MDB positive controls");
        Check(Scenario.Dispatches == 1 && Scenario.InUiReads == Scenario.Reads.Count,
            "entire SDK observation uses one UI dispatch");
        Check(Counts(result)["mdb_database_count"] != null && Counts(result)["catalog_database_count"] != null &&
            (bool)result["ce_unchanged"] && (bool)result["identity_unchanged"],
            "counts and stable context observations accompany results");

        JObject world = (JObject)result["world_level"];
        Check((bool)world["available"] && (bool)world["success"] && (string)world["status"] == "observed" &&
            (string)world["source"] == "E3D21 SDK DbSystem.GetAllWorldLevel(DB/TEAM)" &&
            (string)world["visibility"] == "world_level",
            "world-level observation declares its separate source and visibility scope");
        Check((int)world["database_match_count"] == 2 && (int)world["team_match_count"] == 1 &&
            (bool?)((JObject)((JArray)world["database_matches"])[0])["name_match"] == true &&
            (int)((JObject)world["counts"])["world_database_inspected_count"] == DbSystem.WorldDatabases.Length &&
            (int)((JObject)world["counts"])["world_team_inspected_count"] == DbSystem.WorldTeams.Length,
            "world-level DB/TEAM rows produce bounded positive candidate matches");
        Check((bool)world["admin_permission_verified"] == false &&
            (bool)world["area_availability_verified"] == false &&
            (bool)world["project_coverage_verified"] == false &&
            (string)world["context_id"] == ContextId &&
            (string)world["ce_before"] == (string)world["ce_after"],
            "world-level context and non-authorization claims are explicit");
        Check(Scenario.Reads.Count(path => path == "world.db_array") == 1 &&
            Scenario.Reads.Count(path => path == "world.team_array") == 1 &&
            Enumerable.Range(0, DbSystem.WorldDatabases.Length).All(index =>
                PathRead("world.db[" + index + "].is_null") && PathRead("world.db[" + index + "].NAMEDB") &&
                PathRead("world.db[" + index + "].DBNO")) &&
            Enumerable.Range(0, DbSystem.WorldTeams.Length).All(index =>
                PathRead("world.team[" + index + "].is_null") && PathRead("world.team[" + index + "].NAMN")),
            "world-level SDK arrays are each materialized once and rows are field inspected");

        Scenario.Reset();
        JObject currentMatch = ResultObject(Route(Request("piping", "existing", 10), Scenario.Context));
        JObject existing = ((JArray)currentMatch["database_matches"]).OfType<JObject>().Single();
        Check((int)currentMatch["database_match_count"] == 1 && (bool)existing["name_match"] &&
            (bool)existing["database_number_match"] && (string)existing["current_mdb_match"] == "name_and_database_number",
            "an exact MDB name-and-number pair is distinguished from an out-of-MDB collision");

        Scenario.Reset();
        JObject nameOnly = ResultObject(Route(Request("piping", "newdb", null), Scenario.Context));
        JObject nameOnlyMatch = ((JArray)nameOnly["database_matches"]).OfType<JObject>().Single();
        Check(nameOnly["candidate"]["database_number"].Type == JTokenType.Null &&
            (int)nameOnly["database_match_count"] == 1 && (bool)nameOnlyMatch["name_match"] &&
            nameOnlyMatch["database_number_match"].Type == JTokenType.Null,
            "an omitted database number leaves number matching explicitly unknown");

        Scenario.Reset();
        DbSystem.WorldDatabases = new[] { DbSystem.WorldDatabases[0] };
        DbSystem.WorldTeams = new[] { DbSystem.WorldTeams[1] };
        JObject worldNoMatches = ResultObject(Route(Request(), Scenario.Context));
        JObject absentWorld = (JObject)worldNoMatches["world_level"];
        Check((bool)absentWorld["success"] && (int)absentWorld["database_match_count"] == 0 &&
            (int)absentWorld["team_match_count"] == 0 &&
            (bool)absentWorld["project_coverage_verified"] == false,
            "complete world-level arrays can report no positive matches without claiming absence or coverage");
    }

    private static void MissingPartialAndNonMatches()
    {
        Scenario.Reset();
        DbSystem.Databases = null; DbSystem.Teams = null;
        CatalogHttpResponse missing = Failed(Request());
        Check(missing.Value.ToString().Contains("null catalog array") &&
            PathRead("mdb.db_array") && PathRead("catalog.db_array") && PathRead("catalog.team_array"),
            "missing SDK arrays cannot certify absence");

        Scenario.Reset();
        DbSystem.Databases = new[] { DbSystem.Databases[0], DbSystem.Databases[3] };
        DbSystem.Teams = new[] { new DbElement("catalog.team[0]", "*OTHER", team: true) };
        JObject noMatches = ResultObject(Route(Request(), Scenario.Context));
        Check((bool)noMatches["success"] && (int)noMatches["database_match_count"] == 0 &&
            (int)noMatches["team_match_count"] == 0 && (bool)noMatches["available"],
            "complete positive-control catalogs establish that the candidate name and number were not found");

        Scenario.Reset();
        Scenario.Overrides["catalog.db[1].DBNO.valid"] = false;
        JObject unreadable = ResultObject(Route(Request(), Scenario.Context));
        Check(!(bool)unreadable["success"] && HasError(unreadable, "catalog_database_number"),
            "partial SDK fields invalidate a negative catalog conclusion");
        Check((int)unreadable["database_match_count"] >= 1,
            "unreadable identity fields do not erase positive name evidence from other rows");

        Scenario.Reset();
        Scenario.Overrides["catalog.db[1].DBNO"] = new InvalidOperationException("DBNO read failed");
        JObject failedNumber = ResultObject(Route(Request(), Scenario.Context));
        JObject partialNameMatch = ((JArray)failedNumber["database_matches"]).OfType<JObject>()
            .Single(row => (string)row["name"] == "PIPING/NEWDB");
        Check(!(bool)failedNumber["success"] && Errors(failedNumber).Count > 0 &&
            (int)failedNumber["database_match_count"] >= 1,
            "an ordinary SDK field exception is returned as a partial observation and does not discard matches");
        Check((bool)partialNameMatch["name_match"] && partialNameMatch["database_number"].Type == JTokenType.Null &&
            partialNameMatch["database_number_match"].Type == JTokenType.Null,
            "a readable name remains a positive match while the failed number stays unknown");

        Scenario.Reset();
        DbSystem.Databases = new[] { DbSystem.Databases[0] }.Concat(Enumerable.Range(0, 100).Select(index =>
            new DbElement("catalog.db.match[" + index + "]", "/piping/newdb", index == 0 ? 200 : 300 + index))).ToArray();
        DbSystem.Teams = Enumerable.Range(0, 70).Select(index =>
            new DbElement("catalog.team.match[" + index + "]", "*piping", team: true)).ToArray();
        JObject truncated = ResultObject(Route(Request(), Scenario.Context));
        Check((bool)truncated["success"] && (int)truncated["database_match_count"] == 100 &&
            ((JArray)truncated["database_matches"]).Count == 64 && (bool)truncated["database_matches_truncated"] &&
            (int)truncated["team_match_count"] == 70 && ((JArray)truncated["team_matches"]).Count == 64 &&
            (bool)truncated["team_matches_truncated"],
            "total matches remain accurate while retained match lists stop at their 64-entry cap");
    }

    private static void ContextMismatchAndInspectionCap()
    {
        Scenario.Reset(); Project.Current.Value = "OTHER";
        CatalogHttpResponse stale = Failed(Request());
        Check(stale.Value.ToString().Contains("changed before the catalog probe"), "a stale project/MDB context is rejected");
        Check(!Scenario.Reads.Contains("mdb.db_array") && !Scenario.Reads.Contains("catalog.db_array") &&
            !Scenario.Reads.Contains("catalog.team_array"), "context mismatch is detected before every catalog scan");

        foreach (string replaced in new[] { "project", "mdb" })
        {
            Scenario.Reset();
            Scenario.OnRead = path =>
            {
                if (path != "catalog.team[1].NAMN") return;
                if (replaced == "project")
                {
                    Project.Current = new Project();
                    Project.Current.Value = "PROJECT";
                }
                else
                {
                    MDB.Current = new MDB();
                    MDB.Current.Value = "MDB";
                    MDB.Current.Databases = new[] { new Db("mdb.db[0]", "PIPING/EXISTING", 10),
                        new Db("mdb.db[1]", "OTHER/LIVE", 20) };
                }
            };
            CatalogHttpResponse changed = Failed(Request());
            Check(changed.Value.ToString().Contains("changed during the catalog probe"),
                "same-name current " + replaced + " replacement is rejected");
        }

        Scenario.Reset();
        DbSystem.Databases = Enumerable.Range(0, 10001).Select(index =>
            new DbElement("catalog.db[" + index + "]", "PIPING/ROW" + index, index + 1)).ToArray();
        CatalogHttpResponse capped = Failed(Request());
        Check(capped.Value.ToString().Contains("10000-entry inspection limit"),
            "an oversized materialized SDK result is reported as capped");
        Check(!Scenario.Reads.Any(path => path.StartsWith("catalog.db[", StringComparison.Ordinal)),
            "the inclusive SDK array bound is checked before inspecting any catalog member");

        Scenario.Reset();
        DbSystem.WorldDatabases = Enumerable.Range(0, 10001).Select(index =>
            new DbElement("world.db[" + index + "]", "PIPING/ROW" + index, index + 1)).ToArray();
        JObject worldCapped = ResultObject(Route(Request(), Scenario.Context));
        JObject worldCap = (JObject)worldCapped["world_level"];
        Check(!(bool)worldCap["available"] && (string)worldCap["status"] == "unavailable" &&
            Errors(worldCap).OfType<JObject>().Any(item => (string)(item["stage"] ?? item["code"]) == "world_level_limit") &&
            !Scenario.Reads.Any(path => path.StartsWith("world.db[", StringComparison.Ordinal)),
            "an oversized world-level materialized array is unavailable before any row inspection");

        Scenario.Reset();
        DbSystem.Databases = Enumerable.Range(0, 10000).Select(index => index == 0
            ? new DbElement("catalog.db[0]", "PIPING/EXISTING", 10)
            : new DbElement("catalog.db[" + index + "]", "OTHER/CAT" + index, 1000 + index)).ToArray();
        JObject atLimit = ResultObject(Route(Request(), Scenario.Context));
        Check((bool)atLimit["success"] && (int)Counts(atLimit)["catalog_database_count"] == 10000 &&
            (int)Counts(atLimit)["catalog_database_inspected_count"] == 10000,
            "the documented inspection limit is inclusive");
    }

    private static void ReadFailuresAndNativeFaultStop()
    {
        Scenario.Reset();
        MDB.Current.Databases = new[] { MDB.Current.Databases[0], null };
        JObject partialMdb = ResultObject(Route(Request(), Scenario.Context));
        foreach (JObject scope in new[] { partialMdb, (JObject)partialMdb["world_level"] })
        {
            JObject candidate = ((JArray)scope["database_matches"]).OfType<JObject>()
                .Single(row => (string)row["name"] == "PIPING/NEWDB");
            Check((string)candidate["current_mdb_match"] == "indeterminate" &&
                (bool)candidate["name_match"],
                "an incomplete current MDB roster preserves a positive catalog match without claiming outside-MDB absence");
        }

        foreach (string path in new[] { "world.db[0].is_null", "world.team[0].is_null" })
        {
            Scenario.Reset();
            Scenario.Overrides[path] = new InvalidOperationException("Optional world element is unavailable");
            JObject observed = ResultObject(Route(Request(), Scenario.Context));
            JObject world = (JObject)observed["world_level"];
            Check((bool)observed["success"] && Errors(observed).Count == 0 &&
                ((JArray)observed["database_matches"]).Count > 0,
                "an ordinary world element exception preserves ordinary catalog evidence at " + path);
            Check(!(bool)world["success"] && (bool)world["available"] &&
                (string)world["status"] == "partial" && Errors(world).Count == 1 &&
                ((JArray)world["database_matches"]).Count > 0,
                "an unreadable world element preserves readable world matches as partial evidence at " + path);
            Check((bool)world["ce_unchanged"] && (bool)world["identity_unchanged"] && Scenario.Dispatches == 1,
                "ordinary world element failures still verify final context at " + path);
        }

        foreach (string path in new[] { "world.db_array", "world.team_array" })
        {
            Scenario.Reset();
            Scenario.Overrides[path] = new InvalidOperationException("Optional world array is unavailable");
            JObject observed = ResultObject(Route(Request(), Scenario.Context));
            JObject world = (JObject)observed["world_level"];
            Check((bool)observed["success"] && (bool)observed["available"] &&
                ((JArray)observed["database_matches"]).Count > 0 &&
                ((JArray)observed["team_matches"]).Count > 0 && Errors(observed).Count == 0,
                "an ordinary optional world array exception preserves ordinary catalog evidence at " + path);
            Check(!(bool)world["success"] && !(bool)world["available"] &&
                (string)world["status"] == "unavailable" && Errors(world).Count == 1 &&
                (string)Errors(world)[0]["stage"] == "world_level_array" &&
                world["database_match_count"].Type == JTokenType.Null &&
                world["team_match_count"].Type == JTokenType.Null &&
                ((JArray)world["database_matches"]).Count == 0 && ((JArray)world["team_matches"]).Count == 0,
                "failed optional world array evidence remains unknown at " + path);
            Check(!Scenario.Reads.Any(read => read.StartsWith("world.db[", StringComparison.Ordinal) ||
                read.StartsWith("world.team[", StringComparison.Ordinal)),
                "no world-level row is inspected after a failed array read at " + path);
            Check((bool)world["ce_unchanged"] && (bool)world["identity_unchanged"] && Scenario.Dispatches == 1,
                "ordinary world failures still verify final context in the original UI dispatch at " + path);
        }

        foreach (string path in new[] { "ce.element", "project.current", "project.name", "mdb.current", "mdb.name",
            "mdb.db_array", "mdb.db[0].name", "mdb.db[0].number", "catalog.db_array", "catalog.db[0].is_null",
            "catalog.db[0].NAMEDB", "catalog.db[0].DBNO", "catalog.team_array", "catalog.team[0].is_null",
            "catalog.team[0].NAMN", "world.db_array", "world.db[0].is_null", "world.db[0].NAMEDB",
            "world.db[0].DBNO", "world.team_array", "world.team[0].is_null", "world.team[0].NAMN" })
        {
            Scenario.Reset(); Scenario.FaultAt = path;
            try { Route(Request(), Scenario.Context); throw new Exception("Native fault did not stop catalog observation at " + path); }
            catch (NativeDatabaseFaultException) { }
            Check(Scenario.ReadsAfterFault == 0, "no SDK read may follow native fault at " + path);
            Check(Scenario.Dispatches == 1, "faulting observation remains within a single UI dispatch at " + path);
            Check(!Scenario.Reads.Contains("catalog.team_array") || path == "catalog.team_array" ||
                Scenario.Reads.IndexOf("catalog.team_array") < Scenario.Reads.IndexOf(path),
                "fault stops before later catalog arrays at " + path);
            Check(!Scenario.Reads.Contains("world.db_array") || path == "world.db_array" ||
                Scenario.Reads.IndexOf("world.db_array") < Scenario.Reads.IndexOf(path),
                "fault prevents or stops later world-level reads at " + path);
        }
    }

    private static int Main()
    {
        try
        {
            StrictPayloadAndBounds(); MaterializedResultsAndMatches(); MissingPartialAndNonMatches();
            ContextMismatchAndInspectionCap(); ReadFailuresAndNativeFaultStop();
            Console.WriteLine("Project catalog probe production checks passed: " + checks); return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
