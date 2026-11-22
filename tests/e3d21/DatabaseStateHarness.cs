using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Linq;
using Aveva.Core.Database;

namespace Aveva.Core.Database
{
    public enum DbType { Design }
    public enum DbWriteMode { Write }
    public sealed class Project
    {
        public static Project CurrentProject;
        public string Value = "PROJECT";
        public string Name { get { return Scenario.Read("project.name", Value); } }
    }
    public sealed class MDB
    {
        public static MDB CurrentMDB;
        public string Value = "MDB";
        public Db[] Databases = new[] { new Db(1), new Db(2), new Db(3) };
        public string Name { get { return Scenario.Read("mdb.name", Value); } }
        public DbType DefaultType() { return Scenario.Read("mdb.default_type", DbType.Design); }
        public DbWriteMode WriteMode(DbType type) { return Scenario.Read("mdb.write_mode", DbWriteMode.Write); }
        public Db[] GetDBArray() { return Scenario.Read("mdb.databases", Databases); }
    }
    public sealed class Db
    {
        private readonly int id;
        public Db(int id) { this.id = id; }
        private T Read<T>(string key, T value) { return Scenario.Read("db" + id + "." + key, value); }
        public string Name { get { return Read("name", "DB" + id); } }
        public int Number { get { return Read("number", id); } }
        public int ExtractNumber { get { return Read("extract", 0); } }
        public DbType Type { get { return Read("type", DbType.Design); } }
        public bool IsReadOnly { get { return Read("readonly", id == 2); } }
        public bool IsPrimary { get { return Read("primary", true); } }
        public bool IsExtract { get { return Read("is_extract", false); } }
        public bool HasChangedThisSession { get { return Read("changed", id == 1); } }
        public DbSession CurrentSession { get { return Read("current", new DbSession("db" + id + ".current", 10 + id)); } }
        public DbSession LatestSessionOnDb { get { return Read("latest", new DbSession("db" + id + ".latest", 20 + id)); } }
    }
    public sealed class DbSession
    {
        private readonly string path;
        private readonly int number;
        public DbSession(string path, int number) { this.path = path; this.number = number; }
        public bool IsValid { get { return Scenario.Read(path + ".valid", true); } }
        public int SessionNumber { get { return Scenario.Read(path + ".number", number); } }
        public int Extract { get { return Scenario.Read(path + ".extract", 0); } }
    }
}

internal static class Scenario
{
    internal static readonly List<string> Reads = new List<string>();
    internal static readonly Dictionary<string, object> Overrides = new Dictionary<string, object>();
    internal static Action<string> OnRead;
    internal static int BusyAt, BusyChecks, Dispatches, CeReads;
    internal static string Ce = "=1/1";
    internal static T Read<T>(string path, T fallback)
    {
        Reads.Add(path);
        if (OnRead != null) OnRead(path);
        object value;
        if (!Overrides.TryGetValue(path, out value)) return fallback;
        if (value is Exception) throw (Exception)value;
        return (T)value;
    }
    internal static void Reset()
    {
        Reads.Clear(); Overrides.Clear(); OnRead = null;
        BusyAt = BusyChecks = Dispatches = CeReads = 0; Ce = "=1/1";
        Project.CurrentProject = new Project(); MDB.CurrentMDB = new MDB();
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
        public E3d21Context() { invokeOnUi = action => { Scenario.Dispatches++; action(); }; }
        internal void Fault() { nativeFaultState.Observe("Unhandled Database error 538 from FakeDb", ""); }
        private bool IsCommandActive()
        {
            if (nativeFaultState.HasFault) throw new NativeDatabaseFaultException();
            return ++Scenario.BusyChecks == Scenario.BusyAt;
        }
        private static string ModelCurrentReference() { Scenario.CeReads++; return Scenario.Read("ce", Scenario.Ce); }
        // PRODUCTION_METHODS
    }

    internal static class DatabaseStateHarness
    {
        private static int checks;
        private static void Check(bool value, string label)
        {
            checks++;
            if (!value) throw new Exception("Check failed: " + label);
        }
        private static Dictionary<string, object> Dict(object value) { return (Dictionary<string, object>)value; }
        private static List<object> List(Dictionary<string, object> value, string key) { return (List<object>)value[key]; }
        private static Dictionary<string, object> Read(E3d21Context context, int offset = 0, int limit = 50,
            string expected = "", string session = "SESSION")
        { return Dict(context.GetDatabaseState(offset, limit, expected, session)); }
        private static Dictionary<string, object> ReadIdentity(E3d21Context context, string expected,
            string session = "SESSION")
        { return Dict(context.GetDatabaseIdentitySnapshot(expected, session)); }
        private static bool Error(Dictionary<string, object> value, string code, string path = null)
        {
            return List(value, "read_errors").Select(Dict).Any(item => Equals(item["code"], code) &&
                (path == null || Equals(item["path"], path)));
        }
        private static void Throws<T>(Action action, string label) where T : Exception
        {
            try { action(); }
            catch (T) { Check(true, label); return; }
            throw new Exception("Expected " + typeof(T).Name + ": " + label);
        }

        private static void QueryValidation()
        {
            DatabaseStateRequest empty = DatabaseStateRequest.Parse(new NameValueCollection());
            Check(empty.Offset == 0 && empty.Limit == 50 && empty.ExpectedContext == "", "query defaults");
            foreach (string key in new[] { "offset", "limit", "expected_context" })
            {
                var duplicate = new NameValueCollection(); duplicate.Add(key, "1"); duplicate.Add(key, "2");
                Throws<ArgumentException>(() => DatabaseStateRequest.Parse(duplicate), "duplicate " + key);
            }
            foreach (string bad in new[] { "", "1.0", "+1", "-1", " 1", "1 ", "true", "2147483648", "\n1" })
                Throws<ArgumentException>(() => DatabaseStateRequest.Parse(new NameValueCollection { { "limit", bad } }), "bad integer");
            foreach (var query in new[] {
                new NameValueCollection { { "limit", "201" } }, new NameValueCollection { { "limit", "0" } },
                new NameValueCollection { { "offset", "100001" } }, new NameValueCollection { { "offset", "1" } },
                new NameValueCollection { { "expected_context", new string('A', 64) } },
                new NameValueCollection { { "expected_context", new string('a', 64) + "\n" } },
                new NameValueCollection { { "include_claims", "true" } },
                new NameValueCollection { { null, "offset" } }, new NameValueCollection { { "Limit", "1" } } })
                Throws<ArgumentException>(() => DatabaseStateRequest.Parse(query), "invalid query");
            var valid = DatabaseStateRequest.Parse(new NameValueCollection {
                { "offset", "100000" }, { "limit", "200" }, { "expected_context", new string('a', 64) } });
            Check(valid.Offset == 100000 && valid.Limit == 200, "valid boundaries");
            Scenario.Reset(); var context = new E3d21Context();
            Throws<ArgumentException>(() => Read(context, offset: 1), "direct continuation context required");
            Throws<ArgumentException>(() => Read(context, expected: null), "direct null context rejected");
            Throws<ArgumentException>(() => Read(context, limit: 201), "direct size validation");
            Throws<ArgumentException>(() => Read(context, session: ""), "session identity required");
            Check(Scenario.Dispatches == 0 && Scenario.Reads.Count == 0, "invalid inputs never reach SDK");
        }

        private static void NormalPaginationAndContext()
        {
            Scenario.Reset(); var context = new E3d21Context();
            var first = Read(context, limit: 1);
            Check(Equals(first["success"], true) && Equals(first["page_complete"], true), "page succeeds");
            Check(Equals(first["complete"], false) && Equals(first["has_more"], true) && Equals(first["next_offset"], 1), "page continuation");
            Check(Equals(first["total_count"], 3) && Equals(first["returned_count"], 1), "counts reflect materialized array");
            Check(!Scenario.Reads.Any(item => item.StartsWith("db2.")), "only requested details read");
            Check(Equals(first["ce_unchanged"], true) && Equals(first["identity_unchanged"], true), "observational invariants");
            string contextId = (string)first["context_id"];
            Scenario.Reset(); context = new E3d21Context();
            var identity = ReadIdentity(context, contextId);
            Check(Equals(identity["success"], true) && Equals(identity["complete"], true), "identity snapshot succeeds");
            Check(Equals(identity["total_count"], 3) && Equals(identity["returned_count"], 3), "identity snapshot covers all databases");
            Check(((string)identity["identity_snapshot_sha256"]).Length == 64, "identity snapshot fingerprint is bounded");
            Check(Scenario.Reads.Count(item => item == "mdb.databases") == 1, "identity snapshot reads MDB array once");
            Scenario.Reset(); context = new E3d21Context();
            var staleIdentity = ReadIdentity(context, new string('a', 64));
            Check(Error(staleIdentity, "context_mismatch") && Equals(staleIdentity["success"], false), "identity snapshot rejects stale context");
            var next = Read(context, 1, 2, contextId);
            Check(Equals(next["page_complete"], true) && Equals(next["complete"], false) && Equals(next["has_more"], false), "tail page is not full snapshot");
            var second = Dict(List(next, "databases")[0]);
            Check(Equals(second["number"], 2) && Equals(second["is_read_only"], true), "native flags preserved");
            Check(Equals(Dict(second["current_session"])["number"], 12) &&
                Equals(Dict(second["latest_session"])["number"], 22), "current/latest are distinct SDK sessions");
            Check(Equals(Read(context)["complete"], true), "single full successful observation");
            foreach (int variant in new[] { 0, 1, 2 })
            {
                Scenario.Reset(); context = new E3d21Context();
                if (variant == 1) Project.CurrentProject.Value = "OTHER";
                if (variant == 2) MDB.CurrentMDB.Value = "OTHER";
                var stale = Read(context, 1, 1, contextId, variant == 0 ? "OTHER" : "SESSION");
                Check(Error(stale, "context_mismatch") && Equals(stale["success"], false), "stale context fails");
                Check(stale["next_offset"] == null && !Scenario.Reads.Contains("mdb.databases"), "stale context prevents enumeration");
            }
        }

        private static void UnavailableAndPartialReads()
        {
            Scenario.Reset(); MDB.CurrentMDB = null;
            var value = Read(new E3d21Context());
            Check(Error(value, "no_project_mdb") && Equals(value["available"], false), "missing MDB explicit");
            Scenario.Reset(); MDB.CurrentMDB.Databases = null;
            value = Read(new E3d21Context());
            Check(Error(value, "read_failed", "databases") && value["total_count"] == null && value["has_more"] == null, "null array not empty success");
            Scenario.Reset(); MDB.CurrentMDB.Databases = new Db[0];
            value = Read(new E3d21Context());
            Check(Equals(value["complete"], true) && Equals(value["total_count"], 0), "known empty array");
            Scenario.Reset(); MDB.CurrentMDB.Databases = new Db[10001];
            value = Read(new E3d21Context());
            Check(Error(value, "database_count_limit") && Equals(value["total_count"], 10001), "full array inspection cap");
            Check(List(value, "databases").Count == 0 && value["next_offset"] == null, "cap blocks all detail reads");
            Scenario.Reset(); MDB.CurrentMDB.Databases[0] = null;
            value = Read(new E3d21Context(), limit: 1);
            Check(Error(value, "null_database", "databases[0]") && Equals(value["returned_count"], 1), "null entry retained as unavailable");
            Scenario.Reset(); Scenario.Overrides["db1.readonly"] = new InvalidOperationException("unreadable flag");
            value = Read(new E3d21Context(), limit: 1);
            var item = Dict(List(value, "databases")[0]);
            Check(Error(value, "read_failed", "databases[0].is_read_only") && item["is_read_only"] == null, "failed flag stays unknown");
            Check(Equals(item["number"], 1) && Equals(item["has_changed_this_session"], true), "other fields retained");
            Check(Equals(value["success"], false) && Equals(value["page_complete"], false) && value["next_offset"] == null, "partial page cannot continue blindly");
            Scenario.Reset(); Scenario.Overrides["db1.current"] = null;
            Check(Error(Read(new E3d21Context(), limit: 1), "read_failed", "databases[0].current_session"), "null session explicit");
            Scenario.Reset(); Scenario.Overrides["db1.current.valid"] = false;
            value = Read(new E3d21Context(), limit: 1);
            Check(Error(value, "invalid_session") && !Scenario.Reads.Contains("db1.current.number"), "invalid session never dereferenced");
            Scenario.Reset(); Scenario.Overrides["project.name"] = "";
            value = Read(new E3d21Context());
            Check(Error(value, "identity_unavailable") && !Scenario.Reads.Contains("mdb.databases"), "empty context blocks enumeration");
        }

        private static void ChangedIdentityAndFaults()
        {
            foreach (bool changeMdb in new[] { false, true })
            {
                Scenario.Reset();
                Scenario.OnRead = path => { if (path == "mdb.databases") {
                    if (changeMdb) MDB.CurrentMDB = new MDB(); else Project.CurrentProject = new Project(); } };
                var value = Read(new E3d21Context(), limit: 1);
                Check(Error(value, "consistency_unverified", "identity_unchanged") && Equals(value["success"], false), "replacement identity detected");
            }
            Scenario.Reset(); Scenario.OnRead = path => { if (path == "mdb.databases") Scenario.Ce = "=1/2"; };
            Check(Error(Read(new E3d21Context()), "consistency_unverified", "ce_unchanged"), "CE change detected");
            foreach (int busy in new[] { 1, 2 })
            {
                Scenario.Reset(); Scenario.BusyAt = busy;
                Throws<InvalidOperationException>(() => Read(new E3d21Context()), "busy at both dispatch boundaries");
                Check(Scenario.Reads.Count == 0, "busy never reads SDK");
            }
            foreach (bool throws in new[] { false, true })
            {
                Scenario.Reset(); var context = new E3d21Context();
                Scenario.OnRead = path => { if (path == "db1.name") { context.Fault(); if (throws) throw new Exception("secondary"); } };
                Throws<NativeDatabaseFaultException>(() => Read(context), "native fault propagated");
                Check(Scenario.Reads.Last() == "db1.name" && Scenario.CeReads == 1, "no later SDK or CE read after fault");
            }
            Scenario.Reset(); var preFaulted = new E3d21Context(); preFaulted.Fault();
            Throws<NativeDatabaseFaultException>(() => Read(preFaulted), "pre-existing native fault rejected");
            Check(Scenario.Reads.Count == 0 && Scenario.Dispatches == 0, "pre-existing fault never dispatches");
        }

        private static void IdentitySnapshotCoverageAndFingerprint()
        {
            Scenario.Reset(); var context = new E3d21Context();
            string expected = (string)Read(context, limit: 1)["context_id"];
            Scenario.Reset(); context = new E3d21Context();
            string baseline = (string)ReadIdentity(context, expected)["identity_snapshot_sha256"];
            foreach (string path in new[] { "db1.name", "db1.number", "db1.extract" })
            {
                Scenario.Reset(); Scenario.Overrides[path] = path == "db1.name" ? (object)"CHANGED/NAME" :
                    path == "db1.number" ? (object)99 : 9;
                string changed = (string)ReadIdentity(new E3d21Context(), expected)["identity_snapshot_sha256"];
                Check(changed != baseline, "fingerprint changes for " + path);
            }
            Scenario.Reset(); Array.Reverse(MDB.CurrentMDB.Databases);
            string reordered = (string)ReadIdentity(new E3d21Context(), expected)["identity_snapshot_sha256"];
            Check(reordered != baseline, "fingerprint includes database order");

            Scenario.Reset(); MDB.CurrentMDB.Databases = new Db[10001];
            var oversized = ReadIdentity(new E3d21Context(), expected);
            Check(Error(oversized, "database_count_limit") && oversized["identity_snapshot_sha256"] == null,
                "oversized identity scan fails closed");
            Check(Scenario.Reads.Count(item => item == "mdb.databases") == 1 &&
                !Scenario.Reads.Any(item => item.StartsWith("db")), "oversized scan reads no database identities");

            Scenario.Reset(); MDB.CurrentMDB.Databases[0] = null;
            var nullEntry = ReadIdentity(new E3d21Context(), expected);
            Check(Error(nullEntry, "null_database", "databases[0]") && nullEntry["identity_snapshot_sha256"] == null,
                "null database entry has no complete fingerprint");
            Scenario.Reset(); Scenario.Overrides["db1.name"] = "";
            var missingName = ReadIdentity(new E3d21Context(), expected);
            Check(Error(missingName, "identity_incomplete") && missingName["identity_snapshot_sha256"] == null,
                "missing name prevents complete fingerprint");

            Scenario.Reset(); var busy = new E3d21Context(); Scenario.BusyAt = 1;
            Throws<InvalidOperationException>(() => ReadIdentity(busy, expected), "busy identity scan performs no reads");
            Check(Scenario.Reads.Count == 0, "busy identity scan avoids SDK");
        }

        private static void Main()
        {
            QueryValidation(); NormalPaginationAndContext(); UnavailableAndPartialReads(); ChangedIdentityAndFaults();
            IdentitySnapshotCoverageAndFingerprint();
            Console.WriteLine("Database state production checks passed: " + checks);
        }
    }
}
