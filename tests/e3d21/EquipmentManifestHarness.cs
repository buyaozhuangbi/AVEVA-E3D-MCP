using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Aveva.Core.Database;
using E3d21AiBridge;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Aveva.Core.Database
{
    public enum DbType { Design, Catalogue }
    public enum DbAttributeType { STRING, DOUBLE, INTEGER, BOOL, ELEMENT, STRINGARRAY }

    public sealed class Project
    {
        internal string Id = "PROJECT";
        public static Project CurrentProject { get { return Scenario.Read("project.current", Scenario.Project); } }
        public string Name { get { return Scenario.Read("project.name", Id); } }
    }

    public sealed class MDB
    {
        internal string Id = "MDB";
        public static MDB CurrentMDB { get { return Scenario.Read("mdb.current", Scenario.Mdb); } }
        public string Name { get { return Scenario.Read("mdb.name", Id); } }
        public DbElement FindElement(string name)
        {
            DbElement value;
            return Scenario.Read("mdb.find." + name, Scenario.Names.TryGetValue(name, out value) ? value : null);
        }
        public DbElement GetFirstWorld(DbType type) { Scenario.Forbidden("world scan"); return null; }
    }

    public static class CurrentElement
    {
        public static DbElement Element
        {
            get { return Scenario.Read("ce", Scenario.Ce); }
            set { Scenario.Forbidden("set CE"); }
        }
    }

    public sealed class DbAttribute
    {
        internal readonly string Key;
        private readonly DbAttributeType kind;
        internal DbAttribute(string key, DbAttributeType kind = DbAttributeType.STRING) { Key = key; this.kind = kind; }
        public static DbAttribute GetDbAttribute(string name)
        {
            DbAttribute attribute;
            return Scenario.Read("attribute.resolve." + name, Scenario.Attributes.TryGetValue(name, out attribute) ? attribute : null);
        }
        public bool IsValid { get { return Scenario.Read("attribute." + Key + ".valid", true); } }
        public DbAttributeType Type { get { return Scenario.Read("attribute." + Key + ".type", kind); } }
        public bool IsArray { get { return Scenario.Read("attribute." + Key + ".array", kind == DbAttributeType.STRING || kind == DbAttributeType.STRINGARRAY); } }
    }

    public static class DbAttributeInstance
    {
        public static readonly DbAttribute NAME = new DbAttribute("NAME"), REF = new DbAttribute("REF");
    }

    public sealed class DbElementType
    {
        private readonly string path, type;
        internal DbElementType(string path, string type) { this.path = path; this.type = type; }
        public bool IsValid { get { return Scenario.Read(path + ".type.valid", true); } }
        public string ShortName { get { return Scenario.Read(path + ".type.short", type); } }
        public string Name { get { return Scenario.Read(path + ".type.name", type); } }
    }

    public sealed class DbElement
    {
        internal readonly int Id;
        internal readonly string Kind, Name;
        internal readonly List<DbElement> Children = new List<DbElement>();
        internal DbElement Parent;
        internal DbElement(int id, string kind, string name = null) { Id = id; Kind = kind; Name = name ?? "/E" + id; }
        internal string Path { get { return "e" + Id; } }
        internal string Reference { get { return "=1/" + Id; } }
        public static DbElement GetElement(int[] pair)
        {
            string reference = "=" + pair[0] + "/" + pair[1];
            DbElement value;
            return Scenario.Read("element.get." + reference, Scenario.Elements.TryGetValue(reference, out value) ? value : null);
        }
        public DbElement Member(int ordinal)
        {
            if (ordinal < 1) throw new ArgumentOutOfRangeException(nameof(ordinal));
            return Scenario.Read(Path + ".member." + ordinal, ordinal <= Children.Count ? Children[ordinal - 1] : null);
        }
        public DbElement[] Members() { Scenario.Forbidden("materialize members of " + Reference); return null; }
        public bool IsValid { get { return Scenario.Read(Path + ".valid", true); } }
        public bool IsNull { get { return Scenario.Read(Path + ".null", false); } }
        public bool IsDeleted { get { return Scenario.Read(Path + ".deleted", false); } }
        public int[] Ref { get { return Scenario.Read(Path + ".ref", new[] { 1, Id }); } }
        public DbType DbType { get { return Scenario.Read(Path + ".db_type", DbType.Design); } }
        public DbElementType ElementType { get { return Scenario.Read(Path + ".base_type", new DbElementType(Path, Kind)); } }
        public DbElementType GetElementType() { return Scenario.Read(Path + ".declared_type", new DbElementType(Path, Kind)); }
        public DbElement Owner { get { return Scenario.Read(Path + ".owner", Parent); } }
        public bool IsAttributeValid(DbAttribute attribute) { return Scenario.Read(Path + ".supports." + attribute.Key, true); }
        public string GetAsString(DbAttribute attribute)
        {
            string value = attribute.Key == "NAME" ? Name : attribute.Key == "REF" ? Reference : attribute.Key + " display";
            return Scenario.Read(Path + ".text." + attribute.Key, value);
        }
        public double GetDouble(DbAttribute attribute) { return Scenario.Read(Path + ".double." + attribute.Key, 42.25); }
        public int GetInteger(DbAttribute attribute) { return Scenario.Read(Path + ".integer." + attribute.Key, 0); }
        public string GetString(DbAttribute attribute) { return Scenario.Read(Path + ".string." + attribute.Key, attribute.Key == "NAME" ? Name : "raw text"); }
        public bool GetBool(DbAttribute attribute) { return Scenario.Read(Path + ".bool." + attribute.Key, false); }
        public void Claim() { Scenario.Forbidden("claim"); }
    }
}

internal static class Scenario
{
    internal static readonly List<string> Reads = new List<string>();
    internal static readonly Dictionary<string, object> Overrides = new Dictionary<string, object>();
    internal static readonly Dictionary<string, DbElement> Names = new Dictionary<string, DbElement>(StringComparer.OrdinalIgnoreCase);
    internal static readonly Dictionary<string, DbElement> Elements = new Dictionary<string, DbElement>();
    internal static readonly Dictionary<string, DbAttribute> Attributes = new Dictionary<string, DbAttribute>();
    internal static Action<string> OnRead;
    internal static int Dispatches, UiDepth, ForbiddenCalls;
    internal static bool Busy, BusyOnUi;
    internal static Project Project;
    internal static MDB Mdb;
    internal static DbElement Ce, Root, Zone, Left, Grandchild, Right;

    internal static T Read<T>(string key, T value)
    {
        if (UiDepth != 1) Forbidden("read outside one UI dispatch: " + key);
        Reads.Add(key);
        if (OnRead != null) OnRead(key);
        object replacement;
        if (!Overrides.TryGetValue(key, out replacement)) return value;
        if (replacement is Exception error) throw error;
        return (T)replacement;
    }

    internal static void Forbidden(string operation)
    {
        ForbiddenCalls++;
        throw new InvalidOperationException("Prohibited SDK operation: " + operation);
    }

    internal static DbElement Add(int id, string kind, DbElement owner = null, string name = null)
    {
        var element = new DbElement(id, kind, name) { Parent = owner };
        if (owner != null) owner.Children.Add(element);
        Names.Add(element.Name, element);
        Elements.Add(element.Reference, element);
        return element;
    }

    internal static void Reset()
    {
        Reads.Clear(); Overrides.Clear(); Names.Clear(); Elements.Clear(); Attributes.Clear();
        OnRead = null; Dispatches = UiDepth = ForbiddenCalls = 0; Busy = BusyOnUi = false;
        Project = new Project(); Mdb = new MDB();
        Zone = Add(99, "ZONE");
        Ce = Add(90, "EQUI", Zone);
        Root = Add(1, "EQUI", Zone, "/EQUIPMENT");
        Left = Add(2, "SUBE", Root);
        Grandchild = Add(3, "NOZZ", Left);
        Right = Add(4, "BOX", Root);
        Attributes["HEIG"] = new DbAttribute("HEIG", DbAttributeType.DOUBLE);
        Attributes["DESC"] = new DbAttribute("DESC", DbAttributeType.STRING);
        Attributes["LOCK"] = new DbAttribute("LOCK", DbAttributeType.BOOL);
        Attributes["SEQU"] = new DbAttribute("SEQU", DbAttributeType.INTEGER);
        Attributes["ARR"] = new DbAttribute("ARR", DbAttributeType.STRINGARRAY);
    }
}

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;
        private readonly Action<Action> invokeOnUi = action =>
        {
            Scenario.Dispatches++;
            Scenario.UiDepth++;
            try { action(); }
            finally { Scenario.UiDepth--; }
        };

        internal void Fault() { nativeFaultState.Observe("Unhandled Database error 538 from FakeDb", ""); }
        private bool IsCommandActive()
        {
            if (nativeFaultState.HasFault) throw new NativeDatabaseFaultException();
            return Scenario.Busy || (Scenario.UiDepth != 0 && Scenario.BusyOnUi);
        }

        // PRODUCTION_NATIVE_HELPERS
    }
}

internal sealed class ManifestHttpRequest
{
    internal readonly NameValueCollection QueryString = new NameValueCollection();
    internal Stream InputStream;
    internal Encoding ContentEncoding = Encoding.UTF8;
}

internal sealed class ManifestHttpResponse
{
    internal object Value;
    internal int StatusCode = 200;
}

internal sealed class ManifestHttpContext
{
    internal ManifestHttpRequest Request;
    internal readonly ManifestHttpResponse Response = new ManifestHttpResponse();
}

internal static class ManifestRoute
{
    internal const string SessionId = "fixture-session";
    private const int MaximumJsonRequestBodyBytes = 1024 * 1024;

    internal static async Task<ManifestHttpResponse> RunAsync(string json, object context, bool hasQuery = false, bool handleErrors = false)
    {
        using (var stream = new MemoryStream(Encoding.UTF8.GetBytes(json)))
        {
            var request = new ManifestHttpContext { Request = new ManifestHttpRequest { InputStream = stream } };
            if (hasQuery) request.Request.QueryString.Add("limit", "1");
            try { await RunBranchAsync(request, context); }
            catch (Exception ex) when (handleErrors) { await WriteErrorAsync(request, ex); }
            return request.Response;
        }
    }

    private static async Task RunBranchAsync(ManifestHttpContext request, object context)
    {
        string sessionId = SessionId;
        object response;
        // PRODUCTION_ROUTE
        await WriteJsonAsync(request.Response, response, 200);
    }

    private static Task WriteJsonAsync(ManifestHttpResponse response, object value, int statusCode)
    {
        response.Value = value;
        response.StatusCode = statusCode;
        return Task.CompletedTask;
    }

    private static async Task WriteErrorAsync(ManifestHttpContext request, Exception ex)
    {
        string sessionId = SessionId;
        bool managedScriptStatusQuery = false, equipmentManifestQuery = true, projectCatalogProbe = false;
        // PRODUCTION_HTTP_ERROR
    }

    private static object SafeCommandStateDiagnostics() { Scenario.Forbidden("command diagnostics"); return null; }

    // PRODUCTION_HTTP_HELPERS
}

internal static class EquipmentManifestHarness
{
    private static int checks;

    private static void Check(bool condition, string label)
    {
        checks++;
        if (!condition)
            throw new Exception(label + "\nLast SDK reads: " + string.Join(", ", Scenario.Reads.Skip(Math.Max(0, Scenario.Reads.Count - 25))));
    }

    private static JObject Route(string json, object context, bool hasQuery = false)
    {
        ManifestHttpResponse response = ManifestRoute.RunAsync(json, context, hasQuery).GetAwaiter().GetResult();
        Check(response.StatusCode == 200, "valid route returned HTTP " + response.StatusCode);
        return JObject.FromObject(response.Value);
    }

    private static void Reject(string json, object context, bool hasQuery = false)
    {
        int reads = Scenario.Reads.Count, dispatches = Scenario.Dispatches;
        try { Route(json, context, hasQuery); }
        catch (ArgumentException)
        {
            Check(Scenario.Reads.Count == reads && Scenario.Dispatches == dispatches, "invalid input reached SDK: " + json);
            return;
        }
        throw new Exception("Accepted invalid request: " + json);
    }

    private static void RequestValidation()
    {
        Scenario.Reset();
        var context = new E3d21Context();
        foreach (string json in new[] {
            "{}", "{\"refno\":null}", "{\"refno\":1}", "{\"refno\":\"CE\"}", "{\"refno\":\"=1/-1\"}", "{\"refno\":\"/EQUI*\"}",
            "{\"refno\":\"/EQUIPMENT\",\"refno\":\"=1/1\"}", "{\"refno\":\"/EQUIPMENT\",\"offset\":1}",
            "{\"refno\":\"/EQUIPMENT\",\"max_depth\":0}", "{\"refno\":\"/EQUIPMENT\",\"max_depth\":33}",
            "{\"refno\":\"/EQUIPMENT\",\"limit\":0}", "{\"refno\":\"/EQUIPMENT\",\"limit\":201}",
            "{\"refno\":\"/EQUIPMENT\",\"limit\":1.0}", "{\"refno\":\"/EQUIPMENT\",\"limit\":true}",
            "{\"refno\":\"/EQUIPMENT\",\"limit\":4294967296}", "{\"refno\":\"/EQUIPMENT\",\"limit\":\"1\"}",
            "{\"refno\":\"/EQUIPMENT\",\"attributes\":null}", "{\"refno\":\"/EQUIPMENT\",\"attributes\":\"HEIG\"}",
            "{\"refno\":\"/EQUIPMENT\",\"attributes\":[5]}", "{\"refno\":\"/EQUIPMENT\",\"attributes\":[\"HEIG\",\" heig \"]}",
            "{\"refno\":\"/EQUIPMENT\",\"attributes\":[\"POS WRT WORLD\"]}",
            "{\"refno\":\"/EQUIPMENT\",\"attributes\":[\"A\",\"B\",\"C\",\"D\",\"E\",\"F\",\"G\",\"H\",\"I\",\"J\",\"K\"]}",
            "{\"refno\":\"/EQUIPMENT\",\"limit\":167,\"attributes\":[\"HEIG\",\"DESC\",\"LOCK\"]}"
        }) Reject(json, context);
        Reject("{\"refno\":\"/EQUIPMENT\"}", context, true);
        Reject("{\"refno\":\"/EQUIPMENT\"}" + new string(' ', 8192), context);
        var unavailable = ManifestRoute.RunAsync("{\"refno\":\"/EQUIPMENT\"}", new object(), false).GetAwaiter().GetResult();
        Check(unavailable.StatusCode == 404 && Scenario.Dispatches == 0 && Scenario.Reads.Count == 0,
            "a context without the optional manifest interface returns HTTP 404 without SDK access");
    }

    private static JObject Read(E3d21Context context, string json = "{\"refno\":\"/EQUIPMENT\"}")
    {
        return (JObject)Route(json, context)["result"];
    }

    private static string[] References(JObject result)
    {
        return result["rows"].Select(row => (string)row["refno"]).ToArray();
    }

    private static bool IsNull(JToken token) { return token != null && token.Type == JTokenType.Null; }

    private static bool Incomplete(JObject result)
    {
        return (bool?)result["complete"] == false && (bool?)result["success"] == false;
    }

    private static void ReadContract()
    {
        Scenario.Reset();
        var context = new E3d21Context();
        JObject envelope = Route("{\"refno\":\" /equipment \"}", context);
        var result = (JObject)envelope["result"];
        Check((bool)envelope["ok"] && (string)envelope["version"] == "E3D21"
            && (string)result["session_id"] == ManifestRoute.SessionId, "the native result retains the route session and version");
        Check((string)envelope["session_id"] == ManifestRoute.SessionId &&
            (string)envelope["bridge_build"] == typeof(ManifestRoute).Module.ModuleVersionId.ToString("D"),
            "manifest envelope binds the adapter session and build");
        Check((bool)result["success"] && (bool)result["complete"] && !(bool)result["truncated"]
            && (string)result["order"] == "preorder" && (int)result["max_depth"] == 8 && (int)result["limit"] == 64
            && (bool)result["single_dispatch"] && !(bool)result["atomic_snapshot"], "default request returns bounded, non-atomic evidence");
        Check(References(result).SequenceEqual(new[] { "=1/1", "=1/2", "=1/3", "=1/4" })
            && (string)result["root_refno"] == "=1/1" && (int)result["returned_count"] == 4
            && (string)result["rows"][0]["name"] == "/EQUIPMENT" && (string)result["rows"][0]["type"] == "EQUI"
            && (string)result["rows"][2]["type"] == "NOZZ" && IsNull(result["rows"][0]["parent_index"])
            && IsNull(result["rows"][0]["sibling_index"]) && (int)result["rows"][2]["id"] == 2
            && (int)result["rows"][2]["parent_index"] == 1 && (int)result["rows"][2]["sibling_index"] == 1
            && (int)result["rows"][2]["depth"] == 2, "serialized preorder evidence includes exact identities and ancestry");
        Check((string)result["ce_before"] == "=1/90" && (string)result["ce_after"] == "=1/90"
            && (bool)result["ce_unchanged"] && (string)result["project"] == "PROJECT" && (string)result["mdb"] == "MDB"
            && (bool)result["identity_unchanged"], "stable current element, project and MDB are read before and after");
        Check(Scenario.Dispatches == 1 && Scenario.ForbiddenCalls == 0
            && Scenario.Reads.Count(key => key.StartsWith("mdb.find.")) == 1
            && !Scenario.Reads.Any(key => key.StartsWith("e99.") || key.StartsWith("attribute.")),
            "explicit named lookup does not read the root's owner or any unrequested attributes");

        Scenario.Reset();
        result = Read(new E3d21Context(), "{\"refno\":\"=01/001\",\"limit\":4,\"attributes\":[\"heig\",\" desc \",\"LOCK\",\"SEQU\",\"ARR\"]}");
        JToken row = result["rows"][0];
        Check((bool)result["complete"] && Scenario.Reads.Contains("element.get.=1/1")
            && !Scenario.Reads.Any(key => key.StartsWith("mdb.find.")), "numeric lookup is exact and accepts equivalent padded digits");
        Check(result["attributes"].Values<string>().SequenceEqual(new[] { "HEIG", "DESC", "LOCK", "SEQU", "ARR" })
            && (string)row["values"]["HEIG"] == "HEIG display" && (double)row["typed_values"]["HEIG"] == 42.25
            && (string)row["typed_values"]["DESC"] == "raw text" && (bool?)row["typed_values"]["LOCK"] == false
            && (int)row["typed_values"]["SEQU"] == 0 && (string)row["values"]["ARR"] == "ARR display"
            && row["typed_values"]["ARR"] == null, "formatted and typed scalar values preserve false and zero; arrays are formatted only");
        Check(Scenario.Reads.Count(key => key.StartsWith("attribute.resolve.")) == 5 && Scenario.ForbiddenCalls == 0,
            "requested SDK attribute descriptors are resolved once, outside the per-row loop");
    }

    private static void ExpectRootRejected(string json, string expectedFragment)
    {
        try { Read(new E3d21Context(), json); }
        catch (Exception error) when (error is ArgumentException || error is InvalidOperationException)
        {
            Check(error.Message.IndexOf(expectedFragment, StringComparison.OrdinalIgnoreCase) >= 0
                && MemberReads().Length == 0 && Scenario.ForbiddenCalls == 0, "invalid root must fail before reading any member: " + json);
            return;
        }
        throw new Exception("Accepted invalid root: " + json);
    }

    private static void ExactRoot()
    {
        Scenario.Reset();
        ExpectRootRejected("{\"refno\":\"=1/99\"}", "EQUI");
        Scenario.Reset();
        Scenario.Overrides["mdb.find./EQUIPMENT"] = Scenario.Ce;
        ExpectRootRejected("{\"refno\":\"/EQUIPMENT\"}", "does not match");
        Scenario.Reset();
        Scenario.Overrides["element.get.=1/1"] = Scenario.Ce;
        ExpectRootRejected("{\"refno\":\"=1/1\"}", "does not match");
        Scenario.Reset();
        Scenario.Overrides["e1.deleted"] = true;
        ExpectRootRejected("{\"refno\":\"/EQUIPMENT\"}", "unavailable or deleted");
        Scenario.Reset();
        Scenario.Overrides["e1.db_type"] = DbType.Catalogue;
        ExpectRootRejected("{\"refno\":\"/EQUIPMENT\"}", "Design database");
    }

    private static void PartialResults()
    {
        Scenario.Reset();
        var result = Read(new E3d21Context(), "{\"refno\":\"/EQUIPMENT\",\"max_depth\":1}");
        Check(Incomplete(result) && (bool)result["truncated"]
            && result["truncation_reasons"].Values<string>().SequenceEqual(new[] { "depth" })
            && References(result).SequenceEqual(new[] { "=1/1", "=1/2" }) && !Scenario.Reads.Contains("e1.member.2"),
            "a depth cap reports the verified preorder prefix in the HTTP result");

        Scenario.Reset();
        Scenario.Overrides["e2.member.1"] = new InvalidOperationException("member read failure");
        result = Read(new E3d21Context(), "{\"refno\":\"/EQUIPMENT\",\"limit\":2}");
        JToken issue = result["read_errors"][0];
        Check(Incomplete(result) && !(bool)result["truncated"] && (int)result["returned_count"] == 2
            && (string)issue["stage"] == "member" && (string)issue["refno"] == "=1/2" && (int)issue["sibling_index"] == 1
            && (bool)result["ce_unchanged"] && (bool)result["identity_unchanged"], "failed lookahead preserves rows and exact error location");

        Scenario.Reset();
        Scenario.Overrides["e3.db_type"] = DbType.Catalogue;
        result = Read(new E3d21Context());
        Check(Incomplete(result) && References(result).SequenceEqual(new[] { "=1/1", "=1/2" })
            && (string)result["read_errors"][0]["stage"] == "member" && !Scenario.Reads.Contains("e1.member.2"),
            "a non-Design child cannot enter the manifest or allow unsafe continuation");

        Scenario.Reset();
        Scenario.Overrides["e2.supports.DESC"] = false;
        Scenario.Overrides["e2.double.HEIG"] = double.PositiveInfinity;
        Scenario.Overrides["e3.type.short"] = new InvalidOperationException("type read failure");
        result = Read(new E3d21Context(), "{\"refno\":\"/EQUIPMENT\",\"attributes\":[\"HEIG\",\"DESC\",\":UNKNOWN\"]}");
        JToken row = result["rows"][1];
        Check(Incomplete(result) && !(bool)result["truncated"] && (int)result["returned_count"] == 4
            && (bool?)row["success"] == false && row["errors"]["DESC"] != null && row["errors"][":UNKNOWN"] != null
            && row["typed_read_errors"]["HEIG"] != null && row["typed_values"]["HEIG"] == null
            && (string)row["values"]["HEIG"] == "HEIG display"
            && !Scenario.Reads.Contains("e2.text.DESC") && !Scenario.Reads.Contains("e2.string.DESC")
            && IsNull(result["rows"][2]["type"]) && result["rows"][2]["identity_errors"]["type"] != null
            && (string)result["rows"][3]["refno"] == "=1/4", "attribute and row identity errors remain separate without discarding verified identities");

        Scenario.Reset();
        Scenario.Overrides["e2.text.DESC"] = null;
        Scenario.Overrides["e3.string.DESC"] = null;
        result = Read(new E3d21Context(), "{\"refno\":\"/EQUIPMENT\",\"attributes\":[\"DESC\"]}");
        Check(Incomplete(result) && result["rows"][1]["values"]["DESC"] == null
            && result["rows"][1]["errors"]["DESC"] != null && result["rows"][2]["typed_values"]["DESC"] == null
            && result["rows"][2]["typed_read_errors"]["DESC"] != null,
            "null SDK output is documented as a field error instead of a successful string");

        Scenario.Reset();
        Scenario.OnRead = key => { if (key == "e1.member.1") { Scenario.Ce = Scenario.Right; Scenario.Mdb = new MDB { Id = "OTHER" }; } };
        result = Read(new E3d21Context());
        Check(Incomplete(result) && (bool?)result["ce_unchanged"] == false && (string)result["ce_after"] == "=1/4"
            && (bool?)result["identity_unchanged"] == false && (string)result["mdb_after"] == "OTHER"
            && (int)result["returned_count"] == 4 && Scenario.ForbiddenCalls == 0,
            "CE and MDB drift are disclosed while retaining the read rows");

        Scenario.Reset();
        Scenario.OnRead = key =>
        {
            if (key == "e1.member.1")
            {
                Scenario.Project = new Project();
                Scenario.Mdb = new MDB();
            }
        };
        result = Read(new E3d21Context());
        Check(Incomplete(result) && (string)result["project_after"] == "PROJECT" && (string)result["mdb_after"] == "MDB"
            && (bool?)result["identity_unchanged"] == false
            && result["read_errors"].Values<JToken>().Any(error => (string)error["stage"] == "identity_unchanged"),
            "same-name project/MDB object replacement is reported as context drift");

        Scenario.Reset();
        Scenario.OnRead = key => { if (key == "e1.member.1") Scenario.Overrides["ce"] = new InvalidOperationException("CE read failure"); };
        result = Read(new E3d21Context());
        Check(Incomplete(result) && IsNull(result["ce_unchanged"]) && IsNull(result["ce_after"])
            && (string)result["read_errors"][0]["stage"] == "ce_after" && (bool)result["identity_unchanged"],
            "failed CE readback stays unknown and does not erase other context evidence");
    }

    private static void ExpectNativeFault(E3d21Context context, string json)
    {
        try { Read(context, json); }
        catch (NativeDatabaseFaultException) { return; }
        throw new Exception("A native fault escaped the route as a normal manifest.");
    }

    private static void FaultBarriers()
    {
        const string json = "{\"refno\":\"/EQUIPMENT\",\"attributes\":[\"HEIG\",\"DESC\"]}";
        foreach (string lastRead in new[] { "e2.member.1", "e3.owner", "attribute.HEIG.type", "e1.text.HEIG", "e2.double.HEIG" })
        {
            Scenario.Reset();
            var context = new E3d21Context();
            Scenario.OnRead = key => { if (key == lastRead) context.Fault(); };
            ExpectNativeFault(context, json);
            Check(Scenario.Reads.Last() == lastRead && Scenario.Dispatches == 1 && Scenario.ForbiddenCalls == 0,
                "faulted " + lastRead + " is the final SDK read even when its getter returns a value");
            int reads = Scenario.Reads.Count;
            ExpectNativeFault(context, json);
            Check(Scenario.Reads.Count == reads && Scenario.Dispatches == 1, "latched fault blocks subsequent SDK dispatches and reads");
        }

        foreach (bool becomesBusyOnUi in new[] { false, true })
        {
            Scenario.Reset();
            Scenario.Busy = !becomesBusyOnUi;
            Scenario.BusyOnUi = becomesBusyOnUi;
            var context = new E3d21Context();
            try { Read(context, json); throw new Exception("Read available while E3D is busy."); }
            catch (InvalidOperationException error)
            {
                Check(error.Message.Contains("busy") && Scenario.Dispatches == (becomesBusyOnUi ? 1 : 0) && Scenario.Reads.Count == 0,
                    "busy command check also covers the dispatch transition");
            }
        }
    }

    private static void HttpErrors()
    {
        Scenario.Reset();
        var context = new E3d21Context();
        var badRequest = ManifestRoute.RunAsync("{\"refno\":", context, handleErrors: true).GetAwaiter().GetResult();
        Check(badRequest.StatusCode == 400 && Scenario.Dispatches == 0 && Scenario.ForbiddenCalls == 0,
            "malformed JSON returns HTTP 400 without dispatch or command diagnostics");

        Scenario.OnRead = key => { if (key == "e2.member.1") context.Fault(); };
        var nativeFault = ManifestRoute.RunAsync("{\"refno\":\"/EQUIPMENT\"}", context, handleErrors: true).GetAwaiter().GetResult();
        var body = JObject.FromObject(nativeFault.Value);
        Check(nativeFault.StatusCode == 500 && !(bool)body["ok"] && IsNull(body["state"])
            && Scenario.Dispatches == 1 && Scenario.Reads.Last() == "e2.member.1" && Scenario.ForbiddenCalls == 0,
            "the HTTP error path emits no manifest and does not perform further SDK diagnostics after a fault");
    }

    private static EquipmentManifestTraversal<DbElement> Walk(int depth = 8, int limit = 64, Action<Exception> onError = null)
    {
        Scenario.UiDepth++;
        try
        {
            return EquipmentManifestTraversal<DbElement>.Read(Scenario.Root, Scenario.Root.Reference, depth, limit,
                (element, ordinal) => element.Member(ordinal),
                element => "=" + string.Join("/", element.Ref),
                element => element.Owner.Reference, onError);
        }
        finally { Scenario.UiDepth--; }
    }

    private static string[] MemberReads()
    {
        return Scenario.Reads.Where(key => key.Contains(".member.")).ToArray();
    }

    private static void TraversalLimits()
    {
        Scenario.Reset();
        var result = Walk(limit: 4);
        Check(result.Rows.Select(row => row.Reference).SequenceEqual(new[] { "=1/1", "=1/2", "=1/3", "=1/4" }),
            "an exactly full manifest returns all rows in database preorder");
        Check(result.Rows.Select(row => row.Index).SequenceEqual(new[] { 0, 1, 2, 3 })
            && result.Rows.Select(row => row.ParentIndex).SequenceEqual(new int?[] { null, 0, 1, 0 })
            && result.Rows.Select(row => row.Depth).SequenceEqual(new[] { 0, 1, 2, 1 })
            && result.Rows.Select(row => row.SiblingIndex).SequenceEqual(new int?[] { null, 1, 1, 2 }),
            "preorder rows retain parent indexes, depth and direct member ordinals");
        Check(result.TruncationReasons.Count == 0 && result.ReadErrors.Count == 0 && MemberReads().Length == 7,
            "bounded lookahead distinguishes exact fit from truncation");

        Scenario.Reset();
        result = Walk(depth: 1, limit: 2);
        Check(result.Rows.Select(row => row.Reference).SequenceEqual(new[] { "=1/1", "=1/2" })
            && result.TruncationReasons.OrderBy(reason => reason).SequenceEqual(new[] { "depth", "limit" })
            && MemberReads().SequenceEqual(new[] { "e1.member.1", "e2.member.1" }),
            "the first omitted child can hit both caps; the result stops before later siblings");

        Scenario.Reset();
        Scenario.Left.Children.Clear();
        result = Walk(depth: 1, limit: 3);
        Check(result.Rows.Count == 3 && result.TruncationReasons.Count == 0, "leaves on the depth cap do not make it incomplete");

        Scenario.Reset();
        Scenario.Root.Children.Clear();
        for (int id = 1000; id < 6000; id++) Scenario.Add(id, "BOX", Scenario.Root);
        result = Walk(limit: 200);
        Check(result.Rows.Count == 200 && result.Rows.Last().Reference == "=1/1198"
            && result.TruncationReasons.SequenceEqual(new[] { "limit" }) && result.ReadErrors.Count == 0,
            "a wide tree returns only the requested preorder prefix");
        Check(MemberReads().Length <= 400 && MemberReads().Last() == "e1.member.200"
            && !Scenario.Reads.Any(read => read.StartsWith("e1200.") || read.StartsWith("e5999."))
            && Scenario.ForbiddenCalls == 0,
            "the wide tail is not materialized or probed beyond the first omitted child");

        Scenario.Reset();
        Scenario.Root.Children.Clear();
        DbElement parent = Scenario.Root;
        for (int id = 1000; id < 1080; id++) parent = Scenario.Add(id, "SUBE", parent);
        result = Walk(depth: 32, limit: 200);
        Check(result.Rows.Count == 33 && result.Rows.Last().Depth == 32
            && result.TruncationReasons.SequenceEqual(new[] { "depth" }) && MemberReads().Length == 33,
            "a deep tree reaches the hard depth cap without descending into its omitted subtree");
    }

    private static void StopOnUnsafeHierarchy(string read, object replacement, string parent, int ordinal, string[] prefix, string forbiddenRead)
    {
        Scenario.Overrides[read] = replacement;
        var result = Walk();
        Check(result.Rows.Select(row => row.Reference).SequenceEqual(prefix)
            && result.ReadErrors.Count == 1 && result.ReadErrors[0].ParentReference == parent
            && result.ReadErrors[0].SiblingIndex == ordinal && result.TruncationReasons.Count == 0
            && !Scenario.Reads.Contains(forbiddenRead),
            "a broken hierarchy stops at the unsafe edge with the verified prefix: " + read);
    }

    private static void TraversalFailures()
    {
        Scenario.Reset();
        StopOnUnsafeHierarchy("e2.member.1", Scenario.Ce, "=1/2", 1, new[] { "=1/1", "=1/2" }, "e1.member.2");
        Scenario.Reset();
        StopOnUnsafeHierarchy("e1.member.2", new DbElement(2, "SUBE") { Parent = Scenario.Root }, "=1/1", 2,
            new[] { "=1/1", "=1/2", "=1/3" }, "e1.member.3");
        Scenario.Reset();
        var aliasOfAncestor = new DbElement(1, "EQUI") { Parent = Scenario.Left };
        StopOnUnsafeHierarchy("e2.member.1", aliasOfAncestor, "=1/2", 1, new[] { "=1/1", "=1/2" }, "e1.member.2");
        Scenario.Reset();
        StopOnUnsafeHierarchy("e3.ref", new InvalidOperationException("fixture ref failure"), "=1/2", 1,
            new[] { "=1/1", "=1/2" }, "e1.member.2");
        Scenario.Reset();
        StopOnUnsafeHierarchy("e3.owner", new InvalidOperationException("fixture owner failure"), "=1/2", 1,
            new[] { "=1/1", "=1/2" }, "e1.member.2");
        Scenario.Reset();
        Scenario.Overrides["e2.member.1"] = new InvalidOperationException("fixture navigation failure");
        var result = Walk(limit: 2);
        Check(result.Rows.Count == 2 && result.TruncationReasons.Count == 0 && result.ReadErrors.Count == 1,
            "a failed lookahead is a navigation error rather than confirmed truncation or exhaustion");
    }

    private static int Main()
    {
        try
        {
            RequestValidation();
            ReadContract();
            ExactRoot();
            PartialResults();
            FaultBarriers();
            HttpErrors();
            TraversalLimits();
            TraversalFailures();
            Console.WriteLine("Equipment manifest production checks passed: " + checks);
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error);
            return 1;
        }
    }
}
