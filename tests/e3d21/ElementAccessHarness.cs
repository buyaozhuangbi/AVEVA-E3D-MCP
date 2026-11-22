using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Globalization;
using System.Linq;
using System.Threading.Tasks;
using Aveva.Core.Database;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

// No AVEVA assemblies are loaded. Fake getters are a recording boundary; the
// complete production reader and native-fault/dispatch methods run unchanged.
namespace Aveva.Core.Database
{
    public enum DbAttributeType { BOOL, STRING, INTEGER, ELEMENT, WORD, DOUBLE }
    public enum DbType { Design }
    public sealed class Project
    {
        internal static Project Value;
        public static Project CurrentProject { get { return Scenario.Read("project.current", Value); } }
        public string Name { get { return Scenario.Read("project.name", "PROJECT"); } }
    }
    public sealed class MDB
    {
        internal static MDB Value;
        public static MDB CurrentMDB { get { return Scenario.Read("mdb.current", Value); } }
        public string Name { get { return Scenario.Read("mdb.name", "MDB"); } }
        public DbElement FindElement(string name)
        {
            DbElement value;
            return Scenario.Read("mdb.find." + name, Scenario.Elements.TryGetValue(name, out value) ? value : null);
        }
        public DbElement GetFirstWorld(DbType type) { Scenario.Forbidden("world_scan"); return null; }
        public void UpdateClaimsToLatest() { Scenario.Forbidden("claims_refresh"); }
        public void SaveWork() { Scenario.Forbidden("save"); }
        public void GetWork() { Scenario.Forbidden("get_work"); }
    }
    public static class CurrentElement
    {
        public static DbElement Element { get { return Scenario.Read("ce", Scenario.Ce); } set { Scenario.Forbidden("ce_set"); } }
    }
    public sealed class DbAttribute
    {
        internal readonly string Key;
        private DbAttribute(string key) { Key = key; }
        public static DbAttribute GetDbAttribute(string name) { return Scenario.Read("attribute.resolve." + name, new DbAttribute(name)); }
        public bool IsValid { get { return Scenario.Read("attribute." + Key + ".valid", true); } }
        public bool IsArray { get { return Scenario.Read("attribute." + Key + ".array", false); } }
        public DbAttributeType Type
        {
            get
            {
                var expected = Key == "PRMOWN" ? DbAttributeType.ELEMENT : Key == "CLMNUM" ? DbAttributeType.INTEGER :
                    new[] { "NAME", "USERC", "EXTRC" }.Contains(Key) ? DbAttributeType.STRING : DbAttributeType.BOOL;
                return Scenario.Read("attribute." + Key + ".type", expected);
            }
        }
    }
    public sealed class DbElementType
    {
        private readonly string path;
        internal DbElementType(string path) { this.path = path; }
        public string Name { get { return Scenario.Read(path + ".type.name", "BOX"); } }
    }
    public sealed class Db
    {
        private readonly string path;
        internal Db(string path) { this.path = path; }
        public int Number { get { return Scenario.Read(path + ".db.number", 1); } }
        public int ExtractNumber { get { return Scenario.Read(path + ".db.extract", 0); } }
        public string Name { get { return Scenario.Read(path + ".db.name", "DESIGN"); } }
        public DbType Type { get { return Scenario.Read(path + ".db.type", DbType.Design); } }
        public bool IsReadOnly { get { return Scenario.Read(path + ".db.read_only", false); } }
        public bool IsPrimary { get { return Scenario.Read(path + ".db.primary", true); } }
        public bool IsExtract { get { return Scenario.Read(path + ".db.is_extract", false); } }
    }
    public sealed class DbElement
    {
        internal readonly int Id;
        internal string Path { get { return "e" + Id; } }
        internal DbElement(int id) { Id = id; }
        public static DbElement GetElement(int[] reference)
        {
            string key = "=" + reference[0].ToString(CultureInfo.InvariantCulture) + "/" + reference[1].ToString(CultureInfo.InvariantCulture);
            DbElement value;
            return Scenario.Read("element.get." + key, Scenario.Elements.TryGetValue(key, out value) ? value : null);
        }
        public DbElement[] Members() { Scenario.Forbidden("member_scan"); return new DbElement[0]; }
        public bool IsValid { get { return Scenario.Read(Path + ".valid", true); } }
        public bool IsNull { get { return Scenario.Read(Path + ".null", false); } }
        public int[] Ref { get { return Scenario.Read(Path + ".ref", new[] { 1, Id }); } }
        public Db Db { get { return Scenario.Read(Path + ".db", new Db(Path)); } }
        public DbElementType GetElementType() { return Scenario.Read(Path + ".type", new DbElementType(Path)); }
        public bool IsAttributeValid(DbAttribute attribute) { return Scenario.Read(Path + ".supports." + attribute.Key, true); }
        public bool IsAttributeSetable(DbAttribute attribute)
        {
            if (attribute != null) throw new Exception("Generic permission accidentally received an attribute.");
            return Scenario.Read(Path + ".update_allowed", true);
        }
        public bool GetValidBool(DbAttribute attribute, ref bool value)
        {
            value = Scenario.Read(Path + ".bool." + attribute.Key, attribute.Key == "DBWRIT" || attribute.Key == "DACCLA" || attribute.Key == "OKCLA");
            return Scenario.Available(Path + ".available." + attribute.Key);
        }
        public bool GetValidInteger(DbAttribute attribute, ref int value)
        {
            value = Scenario.Read(Path + ".integer." + attribute.Key, 0);
            bool available = Scenario.Available(Path + ".available." + attribute.Key);
            if (!available) value = 0; // Installed SDK overwrites ref output even on false.
            return available;
        }
        public bool GetValidString(DbAttribute attribute, ref string value)
        {
            value = Scenario.Read(Path + ".string." + attribute.Key, attribute.Key == "NAME" ? "/E" + Id : attribute.Key == "USERC" ? "USER" : "EXTRACT");
            return Scenario.Available(Path + ".available." + attribute.Key);
        }
        public bool GetValidRef(DbAttribute attribute, ref DbElement value)
        {
            value = Scenario.Read(Path + ".reference." + attribute.Key, new DbElement(90));
            return Scenario.Available(Path + ".available." + attribute.Key);
        }
        public void Claim() { Scenario.Forbidden("claim"); }
        public void Release() { Scenario.Forbidden("release"); }
        public void SetAttribute() { Scenario.Forbidden("set_attribute"); }
    }
}

internal static class Scenario
{
    internal static readonly List<string> Reads = new List<string>();
    internal static readonly Dictionary<string, object> Overrides = new Dictionary<string, object>();
    internal static readonly Dictionary<string, DbElement> Elements = new Dictionary<string, DbElement>(StringComparer.OrdinalIgnoreCase);
    internal static Action<string> OnRead;
    internal static Action AfterOperation;
    internal static DbElement Ce;
    internal static int Dispatches, ForbiddenCalls;
    internal static bool Busy;
    internal static T Read<T>(string path, T value)
    {
        Reads.Add(path);
        if (OnRead != null) OnRead(path);
        object replacement;
        if (!Overrides.TryGetValue(path, out replacement)) return value;
        if (replacement is Exception) throw (Exception)replacement;
        return (T)replacement;
    }
    // The availability return and ref output belong to the same SDK call.
    // Do not count an internal fake lookup as a second native getter.
    internal static bool Available(string path)
    { object value; return !Overrides.TryGetValue(path, out value) || (bool)value; }
    internal static void Forbidden(string path) { ForbiddenCalls++; throw new Exception("Prohibited write: " + path); }
    internal static void Reset()
    {
        Reads.Clear(); Overrides.Clear(); Elements.Clear(); OnRead = null; AfterOperation = null; Busy = false; Dispatches = ForbiddenCalls = 0;
        Project.Value = new Project(); MDB.Value = new MDB(); Ce = new DbElement(99);
        for (int id = 1; id <= 32; id++) { var element = new DbElement(id); Elements["/E" + id] = element; Elements["=1/" + id] = element; }
    }
}

namespace E3d21AiBridge
{
    internal sealed class ModelEditInput
    {
        // PRODUCTION_INPUT_METHODS
    }
    public sealed partial class E3d21Context
    {
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;
        private readonly Action<Action> invokeOnUi;
        public E3d21Context() { invokeOnUi = action => { Scenario.Dispatches++; action(); }; }
        internal void Fault() { nativeFaultState.Observe("Unhandled Database error 538 from FakeDb", ""); }
        private bool IsCommandActive()
        { if (nativeFaultState.HasFault) throw new NativeDatabaseFaultException(); return Scenario.Busy; }
        // PRODUCTION_METHODS
    }
}

internal sealed class AccessHttpInnerRequest
{
    internal readonly NameValueCollection QueryString = new NameValueCollection();
    internal JObject Body;
}
internal sealed class AccessHttpRequest { internal AccessHttpInnerRequest Request = new AccessHttpInnerRequest(); }
internal static class AccessHttpRoute
{
    internal static Task<JObject> ReadUniqueBodyAsync(AccessHttpInnerRequest request) { return Task.FromResult(request.Body); }
    internal static async Task<object> Run(AccessHttpRequest request, E3d21AiBridge.E3d21Context context)
    {
        object response;
        string sessionId = "fixture-session";
        // PRODUCTION_ROUTE
        return response;
    }
}

internal static class Program
{
    private static int checks;
    private static Dictionary<string, object> D(object value) { return (Dictionary<string, object>)value; }
    private static List<Dictionary<string, object>> Rows(Dictionary<string, object> report) { return (List<Dictionary<string, object>>)report["rows"]; }
    private static Dictionary<string, object> Field(Dictionary<string, object> report, int row, string group, string key)
    { return D(D(Rows(report)[row][group])[key]); }
    private static Dictionary<string, object> Batch(E3d21AiBridge.E3d21Context context, string[] refs = null)
    { return D(context.GetElementAccess(refs ?? new[] { "/E1", "/E2" })); }
    private static void Check(bool condition, string message)
    { checks++; if (!condition) throw new Exception(message + "\nReads: " + string.Join(", ", Scenario.Reads)); }
    private static bool Is(Dictionary<string, object> value, string key, object expected) { return Equals(value[key], expected); }
    private static void Reject(Action operation, string label)
    {
        int reads = Scenario.Reads.Count, dispatches = Scenario.Dispatches;
        try { operation(); throw new Exception("Accepted invalid input: " + label); }
        catch (ArgumentException) { Check(Scenario.Reads.Count == reads && Scenario.Dispatches == dispatches, label + " rejected before SDK dispatch"); }
    }
    private static void Status(Dictionary<string, object> report, string group, string key, string status, object value)
    { var field = Field(report, 0, group, key); Check(Is(field, "status", status) && Is(field, "value", value), group + "." + key + " status/value"); }
    private static int Main()
    {
        try
        {
            Scenario.Reset(); var context = new E3d21AiBridge.E3d21Context();
            Reject(() => context.GetElementAccess(null), "null refs");
            Reject(() => Batch(context, new string[0]), "empty refs");
            Reject(() => Batch(context, Enumerable.Range(1, 33).Select(i => "/E" + i).ToArray()), "33 refs");
            foreach (string invalid in new[] { "", "CE", "OWNER", "WORLD", "/", "=0/1", "=1/-1", "=1/1\nX", "/E 1" })
                Reject(() => Batch(context, new[] { invalid }), invalid);
            Reject(() => Batch(context, new[] { "/E1", " /e1 " }), "duplicate references");
            var report = Batch(context);
            Check(Is(report, "success", true) && Is(report, "scan_complete", true) && Is(report, "all_values_available", true)
                && Scenario.Dispatches == 1 && Scenario.ForbiddenCalls == 0, "complete fixed reads in one dispatch without writes");
            Check(Is(report, "claims_freshness", "current_session_unverified") && Is(report, "claims_refresh_attempted", false)
                && Is(report, "read_only", true) && Is(report, "ce_unchanged", true) && Is(report, "identity_unchanged", true), "scope and consistency are explicit");
            Status(report, "fields", "locked", "ok", false);
            Status(report, "fields", "update_allowed", "ok", true);
            Status(report, "fields", "read_only", "ok", false);
            Status(report, "target_claim", "claimant_number", "ok", 0);
            Status(report, "fields", "primary_owner_refno", "ok", "=1/90");
            Check(!Scenario.Reads.Any(path => path.StartsWith("e90.") && path != "e90.null" && path != "e90.valid" && path != "e90.ref"),
                "PRMOWN identity is returned without owner claims or database reads");
            Check(Is(Field(report, 0, "target_claim", "claimed"), "source", "LCLM")
                && Is(Field(report, 0, "target_claim", "claimed"), "source_refno", "=1/1"), "every claim identifies its requested source element");

            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
            report = Batch(context, Enumerable.Range(1, 32).Select(i => "/E" + i).ToArray());
            Check(Is(report, "success", true) && Rows(report).Count == 32 && Scenario.Dispatches == 1
                && Rows(report).Select(row => (string)row["refno"]).SequenceEqual(Enumerable.Range(1, 32).Select(i => "=1/" + i)), "32 input order and exact identities");
            Check(Scenario.Reads.Count(path => path.StartsWith("mdb.find.")) == 32 && Scenario.ForbiddenCalls == 0,
                "32 names use one direct lookup each without world/member scans");
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); report = Batch(context, new[] { "/E1", "=1/1" });
            Check(Rows(report)[0]["duplicate_of_index"] == null && Is(Rows(report)[1], "duplicate_of_index", 0)
                && Rows(report).Count == 2, "resolved aliases retain separate rows and disclose repeated identity");
            CheckResolution();

            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
            Scenario.Overrides["e1.update_allowed"] = false;
            Scenario.Overrides["e1.available.LOCK"] = false;
            Scenario.Overrides["e1.integer.CLMNUM"] = 999;
            Scenario.Overrides["e1.available.CLMNUM"] = false;
            Scenario.Overrides["e1.string.USERC"] = "unset";
            Scenario.Overrides["e1.string.EXTRC"] = "";
            report = Batch(context);
            Status(report, "fields", "update_allowed", "ok", false); Status(report, "fields", "read_only", "ok", true);
            Status(report, "fields", "locked", "not_available", null);
            Status(report, "target_claim", "claimant_number", "not_available", null);
            Status(report, "target_claim", "claimant_user", "unset", null); Status(report, "target_claim", "claimant_extract", "unset", null);
            Check(Is(report, "success", true) && Is(report, "scan_complete", true) && Is(report, "all_values_available", false),
                "unavailable optional values and actual false permission are a complete successful observation, not a write authorization");
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); Scenario.Overrides["e1.integer.CLMNUM"] = -7;
            report = Batch(context); Status(report, "target_claim", "claimant_number", "ok", -7);

            foreach (var item in new[] { Tuple.Create("LOCK", DbAttributeType.INTEGER, "fields", "locked", "bool"),
                Tuple.Create("CLMNUM", DbAttributeType.STRING, "target_claim", "claimant_number", "integer"),
                Tuple.Create("USERC", DbAttributeType.WORD, "target_claim", "claimant_user", "string"),
                Tuple.Create("PRMOWN", DbAttributeType.INTEGER, "fields", "primary_owner_refno", "reference") })
            {
                Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); Scenario.Overrides["attribute." + item.Item1 + ".type"] = item.Item2;
                report = Batch(context); Status(report, item.Item3, item.Item4, "not_available", null);
                Check(!Scenario.Reads.Contains("e1." + item.Item5 + "." + item.Item1), "wrong native attribute type never reaches its typed getter");
            }
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); Scenario.Overrides["attribute.LOCK.array"] = true;
            report = Batch(context); Status(report, "fields", "locked", "not_available", null);
            Check(!Scenario.Reads.Contains("e1.bool.LOCK"), "array shape cannot pass scalar typed read");
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); Scenario.Overrides["attribute.USERC.array"] = true;
            report = Batch(context); Status(report, "target_claim", "claimant_user", "ok", "USER");
            Check(Scenario.Reads.Contains("e1.string.USERC"), "STRING character capacity does not turn a typed string into a rejected scalar array");
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); Scenario.Overrides["e1.supports.LOCK"] = false;
            report = Batch(context); Status(report, "fields", "locked", "not_available", null);
            Check(Is(Field(report, 0, "fields", "locked"), "reason", "attribute_value_invalid"), "attribute validity false does not assert unsupported type");

            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); Scenario.Overrides["e1.db"] = null;
            report = Batch(context); Status(report, "database", "number", "not_available", null);
            Check(Is(report, "scan_complete", true) && Is(report, "all_values_available", false) && Scenario.Reads.Contains("e1.bool.LCLM"),
                "missing database remains nullable while independent claim reads continue");
            foreach (var mode in new[] { "null", "invalid", "pair", "unavailable" })
            {
                Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
                if (mode == "null") Scenario.Overrides["e1.reference.PRMOWN"] = null;
                if (mode == "invalid") Scenario.Overrides["e90.valid"] = false;
                if (mode == "pair") Scenario.Overrides["e90.ref"] = new[] { -1, 1 };
                if (mode == "unavailable") Scenario.Overrides["e1.available.PRMOWN"] = false;
                report = Batch(context);
                Status(report, "fields", "primary_owner_refno", mode == "null" ? "unset" : mode == "unavailable" ? "not_available" : "unresolved_reference", null);
            }
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); Scenario.Overrides["e1.valid"] = false;
            report = Batch(context, new[] { "/E1", "/MISSING", "/E2" });
            Check(Is(report, "success", false) && Is(report, "scan_complete", true) && Is(Rows(report)[0], "resolved", false)
                && Is(Rows(report)[1], "status", "not_found") && Is(Rows(report)[2], "resolved", true)
                && !Scenario.Reads.Any(path => path.StartsWith("e1.bool.")), "invalid/missing refs retain null rows without stopping independent inputs");

            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); Scenario.Overrides["e1.bool.LOCK"] = new InvalidOperationException("ordinary read failure");
            Scenario.Overrides["e1.db.number"] = new InvalidOperationException(new string('x', 1000)); report = Batch(context);
            Status(report, "fields", "locked", "read_error", null); Status(report, "database", "number", "read_error", null);
            Check(Is(report, "scan_complete", true) && Is(report, "success", false) && Is(Rows(report)[0], "status", "partial")
                && Is(Rows(report)[1], "status", "returned") && ((List<object>)report["read_errors"]).All(error => ((string)D(error)["message"]).Length <= 512),
                "ordinary read errors retain other fields/rows and bounded diagnostics");
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); Scenario.Overrides["e1.update_allowed"] = new InvalidOperationException("permission failed");
            report = Batch(context); Status(report, "fields", "read_only", "not_available", null);
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); Scenario.Overrides["e1.db"] = new InvalidOperationException("db unavailable");
            report = Batch(context); Status(report, "database", "name", "read_error", null);
            Check(Scenario.Reads.Contains("e1.bool.LCLM"), "ordinary Db getter exception does not erase later claim observations");

            var faultRefs = new[] { "/E1", "=1/2" };
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); Batch(context, faultRefs);
            string[] readPaths = Scenario.Reads.ToArray();
            for (int faultIndex = 0; faultIndex < readPaths.Length; faultIndex++)
            {
                Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
                bool triggered = false; int readsAtFault = 0;
                Scenario.OnRead = path => { if (Scenario.Reads.Count == faultIndex + 1) { triggered = true; context.Fault(); readsAtFault = Scenario.Reads.Count; } };
                report = Batch(context, faultRefs);
                Check(triggered && Scenario.Reads.Count == readsAtFault && Scenario.ForbiddenCalls == 0
                    && Is(report, "success", false) && Is(report, "scan_complete", false) && report["ce_after"] == null
                    && report["native_database_fault"] != null, "native fault stops every later SDK call at " + faultIndex + ": " + readPaths[faultIndex]);
            }
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
            Scenario.OnRead = path => { if (path == "e1.bool.LOCK") context.Fault(); };
            report = Batch(context);
            Check(Is(Rows(report)[1], "status", "not_attempted") && Is(Field(report, 1, "target_claim", "claimed"), "status", "not_attempted"),
                "fault preserves not-attempted later rows and fields instead of fabricating false");
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); Scenario.AfterOperation = context.Fault;
            report = Batch(context);
            Check(Is(report, "success", false) && Is(report, "scan_complete", false) && report["identity_unchanged"] == null,
                "post-callback fault invalidates top-level completion without more SDK reads");

            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); int ceReads = 0;
            Scenario.OnRead = path => { if (path == "ce" && ++ceReads == 2) Scenario.Overrides[path] = new DbElement(98); };
            report = Batch(context);
            Check(Is(report, "success", false) && Is(report, "scan_complete", true) && Is(report, "ce_unchanged", false),
                "changed CE is observed without restoring it or claiming consistency");
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); int projectReads = 0;
            Scenario.OnRead = path => { if (path == "project.current" && ++projectReads == 2) Scenario.Overrides[path] = new Project(); };
            report = Batch(context); Check(Is(report, "identity_unchanged", false) && Is(report, "success", false), "equal project names do not hide changed native context identity");

            CheckHttp();
            Console.WriteLine("Element access production checks passed: " + checks); return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }

    private static void CheckResolution()
    {
        Scenario.Reset(); var context = new E3d21AiBridge.E3d21Context();
        var report = Batch(context, new[] { "=001/01" });
        Check(Is(report, "success", true) && Is(Rows(report)[0], "requested_refno", "=001/01") && Is(Rows(report)[0], "refno", "=1/1")
            && Scenario.Reads.Count(path => path == "element.get.=1/1") == 1
            && !Scenario.Reads.Any(path => path.StartsWith("mdb.find.")) && Scenario.Reads.Count(path => path == "mdb.current") == 2,
            "numeric references use the validated pair with no name lookup or resolver MDB read");
        Check(Scenario.Reads.Count(path => path == "ce") == 2 && !Scenario.Reads.Any(path => path.StartsWith("e99.bool.")),
            "CE is observed only before/after and is never an implicit resolution target");

        foreach (string missing in new[] { "/MISSING", "=1/987" })
        {
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
            report = Batch(context, new[] { missing, "/E2" });
            Check(Is(Rows(report)[0], "status", "not_found") && Is(Rows(report)[0], "resolved", false)
                && Is(Rows(report)[1], "status", "returned") && Is(report, "scan_complete", true) && Is(report, "success", false),
                "a direct lookup returning null reports not_found and continues at " + missing);
            Check(Is(Field(report, 0, "fields", "update_allowed"), "value", null)
                && Is(Field(report, 0, "target_claim", "claimed"), "value", null) && Scenario.ForbiddenCalls == 0,
                "a missing reference has no invented permission, claim, or hierarchy fallback");
        }

        foreach (var lookup in new[] { Tuple.Create("/E1", "mdb.find./E1"), Tuple.Create("=1/1", "element.get.=1/1") })
        {
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
            Scenario.Overrides[lookup.Item2] = new InvalidOperationException("ordinary lookup failure");
            report = Batch(context, new[] { lookup.Item1, "/E2" });
            Check(Is(Rows(report)[0], "status", "read_error") && Is(Rows(report)[0], "resolved", false)
                && Is(Rows(report)[1], "status", "returned") && Is(report, "scan_complete", true) && Is(report, "success", false)
                && ((List<object>)Rows(report)[0]["errors"]).Any(error => Is(D(error), "code", "element_read_error")),
                "ordinary SDK lookup failures report read_error and preserve later rows at " + lookup.Item2);
            Check(Scenario.Reads.Count(path => path == lookup.Item2) == 1 && !Scenario.Reads.Any(path => path.StartsWith("e1."))
                && Scenario.ForbiddenCalls == 0, "failed lookup is not retried and reads no target fields or hierarchy");
        }

        foreach (object mdbFailure in new object[] { null, new InvalidOperationException("MDB unavailable") })
        {
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); int mdbReads = 0;
            Scenario.OnRead = path =>
            {
                if (path == "mdb.current")
                {
                    if (++mdbReads == 2) Scenario.Overrides[path] = mdbFailure;
                    else Scenario.Overrides.Remove(path);
                }
            };
            report = Batch(context, new[] { "/E1", "=1/2" });
            Check(Is(Rows(report)[0], "status", "read_error") && Is(Rows(report)[1], "status", "returned")
                && !Scenario.Reads.Contains("mdb.find./E1") && !Scenario.Reads.Any(path => path.StartsWith("e1.")),
                "missing or failed MDB getter is a read_error, with no name lookup or CE fallback");
        }

        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        report = Batch(context, Enumerable.Range(1, 32).Select(i => "/MISSING" + i).ToArray());
        Check(Rows(report).All(row => Is(row, "status", "not_found")) && Is(report, "scan_complete", true)
            && Scenario.Reads.Count(path => path.StartsWith("mdb.find.")) == 32
            && !Scenario.Reads.Any(path => path.StartsWith("element.get.")) && Scenario.ForbiddenCalls == 0
            && Scenario.Reads.Count(path => path == "ce") == 2,
            "32 missing names remain 32 direct lookups with no numeric, world, member, or CE fallback");
        Console.WriteLine("Element access direct resolution checks passed.");
    }

    private static void CheckHttp()
    {
        foreach (string text in new[] { "{}", "{\"refnos\":null}", "{\"refnos\":true}", "{\"refnos\":[1]}",
            "{\"refnos\":[null]}", "{\"refnos\":[]}", "{\"refnos\":[\"CE\"]}",
            "{\"refnos\":[\"/E1\"],\"dry_run\":true}", "{\"refnos\":[\"/E1\"],\"refresh\":true}",
            "{\"refnos\":[\"/E1\"],\"capture_after\":true}" })
        {
            Scenario.Reset(); var request = new AccessHttpRequest(); request.Request.Body = JObject.Parse(text);
            Reject(() => AccessHttpRoute.Run(request, new E3d21AiBridge.E3d21Context()).GetAwaiter().GetResult(), "HTTP " + text);
        }
        Scenario.Reset(); var queryRequest = new AccessHttpRequest(); queryRequest.Request.QueryString.Add("refnos", "/E1");
        queryRequest.Request.Body = JObject.Parse("{\"refnos\":[\"/E1\"]}");
        Reject(() => AccessHttpRoute.Run(queryRequest, new E3d21AiBridge.E3d21Context()).GetAwaiter().GetResult(), "HTTP query parameters");
        Scenario.Reset(); var requestOk = new AccessHttpRequest(); requestOk.Request.Body = JObject.Parse("{\"refnos\":[\"/E1\",\"=1/1\"]}");
        var response = JObject.FromObject(AccessHttpRoute.Run(requestOk, new E3d21AiBridge.E3d21Context()).GetAwaiter().GetResult());
        Check((bool)response["ok"] && (string)response["version"] == "E3D21" && (string)response["session_id"] == "fixture-session"
            && (int)response["result"]["row_count"] == 2 && (int)response["result"]["rows"][1]["duplicate_of_index"] == 0,
            "production HTTP body maps exactly to the read-only backend and preserves result envelope");
    }
}
