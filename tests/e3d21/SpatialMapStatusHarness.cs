using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Aveva.Core.Database;
using Aveva.Core3D.Clasher;
using E3d21AiBridge;

// These managed fakes verify production orchestration and report semantics.
// They cannot establish native spatial-map validity or native side effects.
internal static class SpatialMapStatusHarness
{
    private static int checks;
    private static void Check(bool condition, string message) { checks++; if (!condition) throw new Exception(message); }
    private static Dictionary<string, object> Row(object value) { return (Dictionary<string, object>)value; }
    private static bool Error(Dictionary<string, object> value, string code)
    {
        return ((List<object>)value["read_errors"]).Cast<Dictionary<string, object>>().Any(item => Equals(item["code"], code));
    }
    private static Dictionary<string, object> Run(int number = 7, int extract = 1, string expected = "", string session = "SESSION")
    {
        return Row(Scenario.Context.GetSpatialMapStatus(number, extract, expected, session));
    }
    private static void Throws<T>(Action action, string message) where T : Exception
    {
        try { action(); } catch (T) { Check(true, message); return; }
        throw new Exception("Expected " + typeof(T).Name + ": " + message);
    }

    private static void ValidationAndBusy()
    {
        Scenario.Reset();
        foreach (Action action in new Action[]
        {
            () => Run(number: 0), () => Run(number: -1), () => Run(extract: -1),
            () => Run(expected: null), () => Run(expected: "a"), () => Run(expected: new string('A', 64)),
            () => Run(expected: new string('a', 64) + "\n"), () => Run(expected: " " + new string('a', 64)),
            () => Run(session: null), () => Run(session: ""), () => Run(session: " \t")
        }) Throws<ArgumentException>(action, "Invalid requests fail before dispatch or SDK access.");
        Check(Scenario.Dispatches == 0 && Scenario.BusyChecks == 0 && Scenario.Reads.Count == 0, "Pure validation performs no native reads.");
        foreach (int at in new[] { 1, 2 })
        {
            Scenario.Reset(); Scenario.BusyAt = at;
            Throws<InvalidOperationException>(() => Run(), "Busy is checked before dispatch and inside the UI callback.");
            Check(Scenario.Reads.Count == 0 && Scenario.CheckCalls == 0 && Scenario.Dispatches == at - 1, "Busy rejection never queries native state.");
        }
        Scenario.Reset(); Scenario.Context.Fault();
        Throws<NativeDatabaseFaultException>(() => Run(), "A preexisting native fault rejects the query.");
        Check(Scenario.Dispatches == 0 && Scenario.Reads.Count == 0, "A latched fault prevents SDK dispatch.");
    }

    private static void StatusAndContext()
    {
        foreach (object configuredStatus in Enum.GetValues(typeof(SpatialMapStatus)).Cast<object>().Concat(new object[] { (SpatialMapStatus)999, new InvalidOperationException("unsafe native call") }))
        {
            Scenario.Reset(); Scenario.Overrides["map.check"] = configuredStatus;
            Scenario.Overrides["map.instance"] = new InvalidOperationException("unsafe service access");
            var report = Run();
            Check(Equals(report["success"], false) && Equals(report["available"], true) && Equals(report["complete"], false)
                && Equals(report["check_attempted"], false) && Equals(report["check_completed"], false), "Suspension preserves metadata availability but never reports a native observation.");
            Check(report["status"] == null && report["status_value"] == null && report["status_known"] == null
                && report["is_up_to_date"] == null && Equals(report["map_validity_verified"], false), "Unobserved native statuses remain unknown under every fake backend response.");
            Check(Error(report, "native_spatial_map_check_suspended") && Equals(report["reason"], "native_spatial_map_check_suspended"), "Fixed suspension has the explicit policy reason.");
            Check(((List<object>)report["read_errors"]).Cast<Dictionary<string, object>>().Any(value => ((string)value["message"]).Contains("2026-11-16 E3D21 native stack overflow remains unresolved")), "The unresolved native failure is disclosed.");
            var database = Row(report["database"]);
            Check(Equals(database["number"], 7) && Equals(database["extract_number"], 1) && Equals(database["name"], "DB1")
                && Equals(database["type"], "Design") && Equals(database["is_extract"], false) && Equals(database["is_primary"], true), "Primary ExtractNumber 1 identity metadata is retained.");
            Check(Scenario.CheckedDatabase == null && Scenario.CheckCalls == 0 && !Scenario.Reads.Any(value => value.StartsWith("map."))
                && Equals(report["enumeration_complete"], true) && Equals(report["exact_match_count"], 1), "Neither SpatialMap.Instance nor CheckSpatialMap is reached after successful metadata resolution.");
            Check(Equals(report["ce_unchanged"], true) && Equals(report["identity_unchanged_before_check"], true) && Equals(report["identity_unchanged"], true), "Metadata observation preserves context invariants.");
        }
        Scenario.Reset(); var first = Run(); string context = (string)first["context_id"];
        Check(context.Length == 64 && Equals(first["project"], "PROJECT") && Equals(first["mdb"], "MDB") && Equals(first["session_id"], "SESSION"), "Existing database-state context convention retained.");
        Check(Error(Run(expected: context), "native_spatial_map_check_suspended"), "Matching context reaches the fixed suspension, not a native query.");
        foreach (string change in new[] { "project", "mdb", "session" })
        {
            Scenario.Reset(); if (change == "project") Project.CurrentValue.Value = "OTHER"; if (change == "mdb") MDB.CurrentValue.Value = "OTHER";
            var report = Run(expected: context, session: change == "session" ? "OTHER" : "SESSION");
            Check(Error(report, "context_mismatch") && !Scenario.Reads.Contains("mdb.databases") && Scenario.CheckCalls == 0, "Stale context rejects before enumeration.");
        }
        Scenario.Reset(); Scenario.Context.AddMessage("old");
        Scenario.OnRead = path => { if (path == "db1.name") Scenario.Context.AddMessage("metadata diagnostic"); };
        var observed = Run(); var messages = ((List<object>)observed["messages"]).Cast<Dictionary<string, object>>().ToArray();
        Check(messages.Length == 1 && Equals(messages[0]["text"], "metadata diagnostic"), "Metadata diagnostics are preserved without old messages.");
    }

    private static void ResolutionAndReadFailures()
    {
        Scenario.Reset(); MDB.CurrentValue = null; var report = Run();
        Check(Error(report, "no_project_mdb") && Equals(report["available"], false) && Scenario.CheckCalls == 0, "Missing project/MDB is explicit.");
        Scenario.Reset(); MDB.CurrentValue.Databases = null; report = Run();
        Check(Error(report, "database_array_unavailable") && report["database_count"] == null && Scenario.CheckCalls == 0, "An unavailable array cannot become an empty successful lookup.");
        Scenario.Reset(); MDB.CurrentValue.Databases = new Db[0]; report = Run();
        Check(Error(report, "database_not_found") && Equals(report["enumeration_complete"], true) && Equals(report["database_count"], 0), "A complete empty MDB is distinct from a failed enumeration.");
        Scenario.Reset(); MDB.CurrentValue.Databases = new[] { new Db(1, 7, 2), new Db(2, 8, 1) }; report = Run();
        Check(Error(report, "database_not_found") && Equals(report["number_match_count"], 1) && Equals(report["exact_match_count"], 0) && Scenario.CheckCalls == 0,
            "Database number alone never substitutes for the requested extract.");
        Scenario.Reset(); MDB.CurrentValue.Databases = new[] { new Db(1, 7, 1), new Db(2, 7, 1) }; report = Run();
        Check(Error(report, "ambiguous_database_identity") && Equals(report["exact_match_count"], 2) && Equals(report["available"], false)
            && Scenario.CheckCalls == 0, "Duplicate exact identities are rejected.");
        Scenario.Reset(); MDB.CurrentValue.Databases = new[] { new Db(1, 7, 1), new Db(2, 7, 2) }; report = Run();
        Check(Error(report, "ambiguous_spatial_map_database_number") && Equals(report["available"], true) && Row(report["database"])["name"] != null
            && ((int[])report["matching_extract_numbers"]).SequenceEqual(new[] { 1, 2 }) && Scenario.CheckCalls == 0,
            "Multiple extracts for the requested number retain target metadata but reject the SDK's ambiguous number-only lookup.");
        foreach (int extract in new[] { 0, 1 })
        {
            Scenario.Reset(); MDB.CurrentValue.Databases[0].ExtractValue = extract; MDB.CurrentValue.Databases[0].ExtractFlag = true;
            report = Run(extract: extract);
            Check(Error(report, "unsupported_spatial_map_extract_scope") && Equals(Row(report["database"])["is_extract"], true)
                && Equals(report["check_attempted"], false) && report["status"] == null && Scenario.CheckCalls == 0,
                "Db.IsExtract determines unsupported extract scope independently of the numeric ExtractNumber.");
        }
        Scenario.Reset(); MDB.CurrentValue.Databases[0].TypeValue = DbType.Catalog; report = Run();
        Check(Error(report, "unsupported_database_type") && Equals(Row(report["database"])["type"], "Catalog") && Scenario.CheckCalls == 0,
            "Only a Design database can reach native map checking.");
        foreach (string failed in new[] { "null_entry", "number", "matching_extract" })
        {
            Scenario.Reset();
            if (failed == "null_entry") MDB.CurrentValue.Databases[1] = null;
            if (failed == "number") Scenario.Overrides["db2.number"] = new InvalidOperationException("number unreadable");
            if (failed == "matching_extract") { MDB.CurrentValue.Databases[1].NumberValue = 7; Scenario.Overrides["db2.extract"] = new InvalidOperationException("extract unreadable"); }
            report = Run();
            Check(Error(report, "database_resolution_incomplete") && Equals(report["enumeration_complete"], false)
                && Equals(report["available"], false) && report["database"] != null && Equals(report["exact_match_count"], 1)
                && Scenario.CheckCalls == 0, "A failed possible match prevents Check even after an exact candidate has already been found.");
        }
        foreach (string field in new[] { "name", "type", "is_extract", "is_primary", "is_read_only" })
        {
            Scenario.Reset(); Scenario.Overrides["db1." + field] = new InvalidOperationException("metadata unavailable"); report = Run();
            Check(Error(report, "read_failed") && Row(report["database"])[field] == null && Equals(report["success"], false)
                && Equals(report["reason"], "database_metadata_incomplete") && Equals(report["complete"], false) && Scenario.CheckCalls == 0,
                "Unreadable target metadata stays unknown and blocks the native call with an explicit reason.");
        }
        Scenario.Reset(); Scenario.Overrides["db1.is_primary"] = false; Scenario.Overrides["db1.is_read_only"] = true; report = Run();
        Check(Error(report, "native_spatial_map_check_suspended") && Equals(Row(report["database"])["is_primary"], false)
            && Equals(Row(report["database"])["is_read_only"], true) && Scenario.CheckCalls == 0,
            "A read-only non-extract Design database can be queried without demanding write capability.");
        Scenario.Reset(); Scenario.Overrides["map.instance"] = null; report = Run();
        Check(Error(report, "native_spatial_map_check_suspended") && !Scenario.Reads.Contains("map.instance"), "Even an absent service is never accessed while suspended.");
    }

    private static void BudgetsAndIdentity()
    {
        Scenario.Reset(); MDB.CurrentValue.Databases = new Db[10001]; var report = Run();
        Check(Error(report, "database_count_limit") && Equals(report["database_count"], 10001)
            && !Scenario.Reads.Any(value => value.StartsWith("db")) && Scenario.CheckCalls == 0, "An oversized array is rejected before inspecting entries.");
        Scenario.Reset(); MDB.CurrentValue.Databases = Enumerable.Range(1, 10000).Select(index => index == 1 ? new Db(1, 7, 1) : new Db(index, index + 10000, 0)).ToArray();
        report = Run();
        Check(Error(report, "native_spatial_map_check_suspended") && Equals(report["database_count"], 10000)
            && Scenario.Reads.Count(value => value.EndsWith(".number")) == 10000, "The inclusive 10000-entry budget still verifies every possible number match.");
        foreach (string at in new[] { "mdb.databases", "db1.name" })
        foreach (bool changeMdb in new[] { false, true })
        {
            Scenario.Reset(); Scenario.OnRead = path => { if (path == at) { if (changeMdb) MDB.CurrentValue = new MDB(); else Project.CurrentValue = new Project(); } };
            report = Run();
            Check(Equals(report["success"], false) && Equals(report["complete"], false) && Equals(report["identity_unchanged"], false)
                && Scenario.CheckCalls == 0, "Project/MDB object replacement prevents success and, when observed before Check, prevents the native call.");
        }
        Scenario.Reset(); Scenario.OnRead = path => { if (path == "db1.name") Scenario.Ce = "=9/2"; }; report = Run();
        Check(Equals(report["ce_unchanged"], false) && Equals(report["success"], false) && report["status"] == null, "A changed CE is reported while native status remains unknown.");
        Scenario.Reset(); Scenario.Overrides["ce"] = new InvalidOperationException("CE unavailable"); report = Run();
        Check(Equals(report["success"], false) && Scenario.CheckCalls == 0 && report["ce_before"] == null, "Failed initial context observation prevents the native query.");
    }

    private static void NativeFaultBarriers()
    {
        foreach (string path in new[] { "ce", "mdb.current", "project.current", "project.name", "mdb.name", "mdb.databases",
            "db1.number", "db2.number", "db1.extract", "db1.name", "db1.type", "db1.is_extract", "db1.is_primary", "db1.is_read_only" })
        {
            Scenario.Reset(); Scenario.FaultAt = path;
            Throws<NativeDatabaseFaultException>(() => Run(), "A native fault must escape through the shared fault barrier.");
            Check(Scenario.NativeAfterFault == 0, "No bridge SDK getter follows a native fault at " + path);
            if (path != "map.check") Check(Scenario.CheckCalls == 0, "A pre-check fault prevents native map checking.");
        }
    }

    private static int Main()
    {
        try
        {
            ValidationAndBusy(); StatusAndContext(); ResolutionAndReadFailures(); BudgetsAndIdentity(); NativeFaultBarriers();
            Console.WriteLine("Spatial map status production checks passed: " + checks); return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}

internal static class Scenario
{
    internal static E3d21Context Context;
    internal static readonly List<string> Reads = new List<string>();
    internal static readonly Dictionary<string, object> Overrides = new Dictionary<string, object>();
    internal static Action<string> OnRead;
    internal static string FaultAt, Ce;
    internal static int Dispatches, BusyAt, BusyChecks, CheckCalls, NativeAfterFault;
    internal static Db CheckedDatabase;
    internal static T Read<T>(string path, T fallback)
    {
        if (Context.HasFault) { NativeAfterFault++; throw new Exception("SDK read after fault: " + path); }
        Reads.Add(path);
        if (OnRead != null) OnRead(path);
        if (FaultAt == path) Context.Fault();
        object value;
        if (!Overrides.TryGetValue(path, out value)) return fallback;
        if (value is Exception) throw (Exception)value;
        return (T)value;
    }
    internal static void Reset()
    {
        Reads.Clear(); Overrides.Clear(); OnRead = null; FaultAt = null; Ce = "=9/1";
        Dispatches = BusyAt = BusyChecks = CheckCalls = NativeAfterFault = 0; CheckedDatabase = null;
        Project.CurrentValue = new Project(); MDB.CurrentValue = new MDB(); Context = new E3d21Context();
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
        private readonly object messageLock = new object();
        private readonly Queue<Dictionary<string, object>> outputMessages = new Queue<Dictionary<string, object>>();
        public E3d21Context() { invokeOnUi = action => { Scenario.Dispatches++; action(); }; }
        internal bool HasFault { get { return nativeFaultState.HasFault; } }
        internal void Fault() { nativeFaultState.Observe("Unhandled Database error 538 from FakeSpatialMap", ""); }
        internal void AddMessage(string text) { lock (messageLock) outputMessages.Enqueue(new Dictionary<string, object> { ["text"] = text }); }
        private bool IsCommandActive()
        {
            if (nativeFaultState.HasFault) throw new NativeDatabaseFaultException();
            return ++Scenario.BusyChecks == Scenario.BusyAt;
        }
        private static string ModelCurrentReference() { return Scenario.Read("ce", Scenario.Ce); }
        // PRODUCTION_METHODS
    }
}

namespace Aveva.Core.Database
{
    public enum DbType { Design, Catalog }
    public sealed class Project
    {
        internal static Project CurrentValue;
        internal string Value = "PROJECT";
        public static Project CurrentProject { get { return Scenario.Read("project.current", CurrentValue); } }
        public string Name { get { return Scenario.Read("project.name", Value); } }
    }
    public sealed class MDB
    {
        internal static MDB CurrentValue;
        internal string Value = "MDB";
        internal Db[] Databases = { new Db(1, 7, 1), new Db(2, 8, 1), new Db(3, 9, 0) };
        public static MDB CurrentMDB { get { return Scenario.Read("mdb.current", CurrentValue); } }
        public string Name { get { return Scenario.Read("mdb.name", Value); } }
        public Db[] GetDBArray() { return Scenario.Read("mdb.databases", Databases); }
    }
    public sealed class Db
    {
        private readonly int id;
        internal int NumberValue, ExtractValue;
        internal bool ExtractFlag;
        internal DbType TypeValue = DbType.Design;
        internal Db(int id, int number, int extract) { this.id = id; NumberValue = number; ExtractValue = extract; }
        private T Read<T>(string field, T value) { return Scenario.Read("db" + id + "." + field, value); }
        public int Number { get { return Read("number", NumberValue); } }
        public int ExtractNumber { get { return Read("extract", ExtractValue); } }
        public string Name { get { return Read("name", "DB" + id); } }
        public DbType Type { get { return Read("type", TypeValue); } }
        public bool IsExtract { get { return Read("is_extract", ExtractFlag); } }
        public bool IsPrimary { get { return Read("is_primary", true); } }
        public bool IsReadOnly { get { return Read("is_read_only", false); } }
    }
}

namespace Aveva.Core3D.Clasher
{
    public enum SpatialMapStatus { UNKNOWN, UPTODATE, NOWARNINGS, WARNINGS, ERRORS, POSERRORS, UNCHECKED }
    public sealed class SpatialMap
    {
        public static SpatialMap Instance { get { return Scenario.Read("map.instance", new SpatialMap()); } }
        public SpatialMapStatus CheckSpatialMap(Db database)
        {
            Scenario.CheckCalls++; Scenario.CheckedDatabase = database;
            return Scenario.Read("map.check", SpatialMapStatus.UPTODATE);
        }
    }
}
