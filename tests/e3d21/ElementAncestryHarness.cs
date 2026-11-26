using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;

// Production ancestry methods execute against managed recording fakes. No
// AVEVA assemblies, E3D session, PML, or mutable database APIs are loaded.
namespace Aveva.Core.Database
{
    public enum DbType { Design, Catalogue }

    public sealed class DbAttribute
    {
        internal readonly string Name;
        internal DbAttribute(string name) { Name = name; }
    }

    internal static class DbAttributeInstance
    {
        internal static readonly DbAttribute REF = new DbAttribute("REF");
    }

    public sealed class Project
    {
        internal static Project Value;
        private readonly string name;
        internal Project(string name) { this.name = name; }
        public static Project CurrentProject { get { return Scenario.Read("project.current", Value); } }
        public string Name { get { return Scenario.Read("project.name", name); } }
    }

    public sealed class MDB
    {
        internal static MDB Value;
        internal string NameValue;
        internal Db[] Databases;
        internal DbElement FirstDesignWorld;
        public static MDB CurrentMDB { get { return Scenario.Read("mdb.current", Value); } }
        public string Name { get { return Scenario.Read("mdb.name", NameValue); } }
        public Db[] GetDBArray() { return Scenario.Read("mdb.databases", Databases); }
        public DbElement GetFirstWorld(DbType type)
        { return Scenario.Read("mdb.first_world", type == DbType.Design ? FirstDesignWorld : null); }
    }

    public static class CurrentElement
    {
        internal static DbElement Value;
        public static DbElement Element { get { return Scenario.Read("ce.element", Value); } }
    }

    public sealed class Db
    {
        internal string Path;
        internal string NameValue;
        internal int NumberValue, ExtractNumberValue;
        internal DbType TypeValue;
        internal DbElement WorldValue;
        public string Name { get { return Scenario.Read(Path + ".name", NameValue); } }
        public int Number { get { return Scenario.Read(Path + ".number", NumberValue); } }
        public int ExtractNumber { get { return Scenario.Read(Path + ".extract_number", ExtractNumberValue); } }
        public DbType Type { get { return Scenario.Read(Path + ".type", TypeValue); } }
        public DbElement World { get { return Scenario.Read(Path + ".world", WorldValue); } }
    }

    public sealed class DbElementType
    {
        private readonly string path;
        private readonly string shortName;
        internal DbElementType(string path, string shortName) { this.path = path; this.shortName = shortName; }
        public string ShortName { get { return Scenario.Read(path + ".type.short_name", shortName); } }
        public bool IsWorld { get { return Scenario.Read(path + ".type.is_world", shortName == "WORLD" || shortName == "WORL"); } }
    }

    public sealed class DbElement
    {
        internal readonly int Id;
        internal int[] Reference;
        internal string FormattedReference, TypeName;
        internal Db Database;
        internal DbElement OwnerValue;
        internal bool Valid = true, Null;
        internal DbElement(int id, int[] reference, string type, string formatted, Db database)
        {
            Id = id; Reference = reference; TypeName = type; FormattedReference = formatted; Database = database;
        }
        private string Path { get { return "e" + Id.ToString(CultureInfo.InvariantCulture); } }
        public static DbElement GetElement(int[] reference)
        {
            string key = Scenario.Pair(reference);
            return Scenario.Read("element.get." + key,
                Scenario.Elements.ContainsKey(key) ? Scenario.Elements[key] : null);
        }
        public bool IsValid { get { return Scenario.Read(Path + ".valid", Valid); } }
        public bool IsNull { get { return Scenario.Read(Path + ".null", Null); } }
        public int[] Ref
        {
            get
            {
                int[] value = Scenario.Read(Path + ".ref", Reference);
                return value == null ? null : (int[])value.Clone();
            }
        }
        public string GetAsString(DbAttribute attribute)
        { return Scenario.Read(Path + ".formatted_refno", FormattedReference); }
        public DbElementType GetElementType()
        { return Scenario.Read(Path + ".type", new DbElementType(Path, TypeName)); }
        public Db Db { get { return Scenario.Read(Path + ".database", Database); } }
        public DbElement Owner { get { return Scenario.Read(Path + ".owner", OwnerValue); } }

        // Any accidental mutation is an immediate harness failure.
        public void SetAttribute() { Scenario.Forbidden("set_attribute"); }
        public void Claim() { Scenario.Forbidden("claim"); }
        public void Release() { Scenario.Forbidden("release"); }
    }
}

internal static class Scenario
{
    internal static readonly List<string> Reads = new List<string>();
    internal static readonly Dictionary<string, object> Overrides = new Dictionary<string, object>();
    internal static readonly Dictionary<string, DbElement> Elements = new Dictionary<string, DbElement>(StringComparer.Ordinal);
    internal static Action<string> OnRead;
    internal static Action AfterOperation;
    internal static E3d21AiBridge.E3d21Context Context;
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

    internal static string Pair(int[] pair)
    { return pair == null || pair.Length != 2 ? "null" : pair[0].ToString(CultureInfo.InvariantCulture) + "/" + pair[1].ToString(CultureInfo.InvariantCulture); }

    internal static void Register(DbElement element)
    { Elements[Pair(element.Reference)] = element; }

    internal static void Forbidden(string operation)
    { ForbiddenCalls++; throw new Exception("Prohibited database mutation: " + operation); }

    internal static void Reset()
    {
        Reads.Clear(); Overrides.Clear(); Elements.Clear(); OnRead = null; AfterOperation = null;
        Dispatches = ForbiddenCalls = 0; Busy = false; Context = null;

        Db first = Database("dbA", 101, 0, "DESIGN-A");
        Db second = Database("dbB", 202, 3, "DESIGN-B");
        DbElement worldA = World(1, new[] { 707, 0 }, "=1707/0", first);
        DbElement worldB = World(2, new[] { 808, 0 }, "=2808/0", second);
        first.WorldValue = worldA; second.WorldValue = worldB;
        var mdb = new MDB { NameValue = "MDB", Databases = new[] { first, second }, FirstDesignWorld = worldA };
        MDB.Value = mdb; Project.Value = new Project("PROJECT");

        DbElement middle = Element(12, new[] { 902, 7 }, "SUBE", "=9902/7", second);
        DbElement target = Element(11, new[] { 901, 23 }, "EQUI", "=9901/23", first);
        target.OwnerValue = middle; middle.OwnerValue = worldB;
        DbElement ce = Element(90, new[] { 990, 5 }, "EQUI", "=9990/5", first);
        CurrentElement.Value = ce;
    }

    internal static Db Database(string path, int number, int extract, string name)
    { return new Db { Path = path, NumberValue = number, ExtractNumberValue = extract, NameValue = name, TypeValue = DbType.Design }; }

    internal static DbElement World(int id, int[] reference, string formatted, Db database)
    {
        var value = new DbElement(id, reference, "WORLD", formatted, database);
        Register(value); return value;
    }

    internal static DbElement Element(int id, int[] reference, string type, string formatted, Db database)
    {
        var value = new DbElement(id, reference, type, formatted, database);
        Register(value); return value;
    }
}

namespace E3d21AiBridge
{
    public interface IElementAncestryContext { object ElementAncestry(string refno, int maxDepth); }

    internal static class ModelEditInput
    {
        // PRODUCTION_INPUT_METHOD
    }

    public sealed partial class E3d21Context : IElementAncestryContext
    {
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;

        internal void Fault() { nativeFaultState.Observe("Unhandled Database error 538 from FakeDb", ""); }

        private static void ThrowIfNativeFaulted()
        {
            NativeDatabaseFaultState state = currentNativeFaultState;
            if (state != null && state.HasFault && state.Generation > permittedNativeFaultGeneration)
                throw new NativeDatabaseFaultException();
        }

        private static T NativeRead<T>(Func<T> operation)
        {
            ThrowIfNativeFaulted();
            try { return operation(); }
            finally { ThrowIfNativeFaulted(); }
        }

        private bool IsCommandActive()
        {
            if (nativeFaultState.HasFault) throw new NativeDatabaseFaultException();
            return Scenario.Busy;
        }

        private T OnUi<T>(Func<T> operation)
        {
            NativeDatabaseFaultState previous = currentNativeFaultState;
            long previousGeneration = permittedNativeFaultGeneration;
            Scenario.Dispatches++;
            currentNativeFaultState = nativeFaultState;
            permittedNativeFaultGeneration = 0;
            try
            {
                ThrowIfNativeFaulted();
                T result = operation();
                if (Scenario.AfterOperation != null) Scenario.AfterOperation();
                ThrowIfNativeFaulted();
                return result;
            }
            finally
            {
                currentNativeFaultState = previous;
                permittedNativeFaultGeneration = previousGeneration;
            }
        }

        // PRODUCTION_ANCESTRY_METHODS
    }
}

internal static class Program
{
    private static int checks;
    private static Dictionary<string, object> D(object value) { return (Dictionary<string, object>)value; }
    private static List<Dictionary<string, object>> Ancestors(Dictionary<string, object> report)
    { return (List<Dictionary<string, object>>)report["ancestors"]; }
    private static List<Dictionary<string, object>> Errors(Dictionary<string, object> report)
    { return ((List<object>)report["read_errors"]).Select(D).ToList(); }
    private static E3d21AiBridge.E3d21Context Fresh()
    {
        Scenario.Reset(); var context = new E3d21AiBridge.E3d21Context(); Scenario.Context = context; return context;
    }
    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) throw new Exception(message + "\nSDK reads: " + string.Join(", ", Scenario.Reads));
    }
    private static bool HasCode(Dictionary<string, object> report, string code)
    { return Errors(report).Any(error => Equals(error["code"], code)); }

    private static void Reject(Action operation, string label)
    {
        int reads = Scenario.Reads.Count, dispatches = Scenario.Dispatches;
        try { operation(); throw new Exception("Accepted invalid ancestry input: " + label); }
        catch (ArgumentException)
        {
            Check(Scenario.Reads.Count == reads && Scenario.Dispatches == dispatches,
                label + " is rejected before SDK access or UI dispatch");
        }
    }

    private static DbElement Target()
    { return Scenario.Elements["901/23"]; }

    private static int Main()
    {
        try
        {
            var context = Fresh();
            foreach (string invalid in new[] { "", "CE", "=01/2", "=1/02", "=0/1", "=1/-1", "=1/x", "=1/2/3", " =1/2", "=1/2 " })
                Reject(() => context.ElementAncestry(invalid, 1), "invalid explicit reference " + invalid);
            Reject(() => context.ElementAncestry("=1/2", 0), "zero depth");
            Reject(() => context.ElementAncestry("=1/2", 65), "depth above limit");

            context = Fresh();
            var report = D(context.ElementAncestry("=901/23", 4));
            var rows = Ancestors(report);
            Check(Equals(report["success"], true) && Equals(report["complete"], true)
                && Equals(report["read_only"], true) && Equals(report["reached_world"], true)
                && Equals(report["truncated"], false) && Equals(report["mdb_scan_complete"], true)
                && Equals(report["mdb_membership_complete"], true) && rows.Count == 3,
                "a complete cross-database OWNER chain reaches a verified WORLD");
            Check(Equals(rows[0]["depth"], 0) && Equals(rows[1]["depth"], 1) && Equals(rows[2]["depth"], 2),
                "the target is returned at depth zero in native ancestry order");

            var target = rows[0];
            int[] targetRaw = (int[])target["raw_ref"];
            var targetDb = D(target["database"]);
            Check(targetRaw.SequenceEqual(new[] { 901, 23 }) && Equals(target["raw_refno"], "=901/23")
                && Equals(target["formatted_refno"], "=9901/23") && Equals(target["type"], "EQUI")
                && Equals(targetDb["number"], 101) && Equals(targetDb["extract_number"], 0)
                && Equals(targetDb["name"], "DESIGN-A") && Equals(targetDb["type"], "Design")
                && Equals(targetDb["in_current_mdb"], true),
                "raw REF, formatted REF, type, and actual Db identity remain distinct");
            Check(Equals(D(rows[1]["database"])["number"], 202)
                && Equals(D(rows[1]["database"])["extract_number"], 3),
                "a cross-database owner carries its own database identity");
            var worldFrame = D(report["world_frame"]);
            Check(((int[])worldFrame["raw_ref"]).SequenceEqual(new[] { 707, 0 })
                && Equals(worldFrame["raw_refno"], "=707/0") && Equals(worldFrame["formatted_refno"], "=1707/0")
                && !Equals(worldFrame["raw_refno"], worldFrame["formatted_refno"]),
                "the SDK-selected WORLD frame preserves distinct raw and formatted references");
            Check(((List<Dictionary<string, object>>)report["mdb_databases"]).Count == 2
                && Equals(D(report["context"])["identity_unchanged"], true)
                && Equals(D(report["context"])["ce_unchanged"], true)
                && Scenario.Dispatches == 1 && Scenario.ForbiddenCalls == 0,
                "the complete MDB scan and before/after context are read in one dispatch without writes");
            string[] completeReadPaths = Scenario.Reads.Distinct(StringComparer.Ordinal).ToArray();

            context = Fresh();
            Scenario.Elements["902/7"].TypeName = "SITE";
            Scenario.Elements["707/0"].TypeName = "WORL";
            Scenario.Elements["808/0"].TypeName = "WORL";
            Scenario.Overrides["e12.type.is_world"] = true;
            Scenario.Overrides["e1.type.is_world"] = false;
            Scenario.Overrides["e2.type.is_world"] = false;
            report = D(context.ElementAncestry("=901/23", 4));
            rows = Ancestors(report);
            Check(Equals(report["success"], true) && Equals(report["complete"], true)
                && Equals(report["reached_world"], true) && rows.Count == 3
                && Equals(rows[1]["type"], "SITE") && Equals(rows[1]["is_world"], true)
                && Equals(rows[2]["type"], "WORL") && Equals(rows[2]["is_world"], false)
                && Equals(D(report["world_frame"])["is_world"], false)
                && !Scenario.Reads.Contains("e2.owner"),
                "native IsWorld flags remain raw evidence: SITE true is traversed and verified WORL false terminates");

            context = Fresh();
            Scenario.Elements["808/0"].TypeName = "WORL";
            Scenario.Overrides["e2.type.is_world"] = false;
            report = D(context.ElementAncestry("=808/0", 1));
            Check(Equals(report["complete"], true) && Equals(report["reached_world"], true)
                && Equals(report["truncated"], false) && Ancestors(report).Count == 1
                && Equals(Ancestors(report)[0]["is_world"], false),
                "a direct verified WORL endpoint succeeds at depth one regardless of its native IsWorld flag");

            context = Fresh();
            Scenario.Elements["902/7"].TypeName = "WORL";
            report = D(context.ElementAncestry("=901/23", 4));
            Check(Equals(report["complete"], false) && Equals(report["reached_world"], false)
                && HasCode(report, "invalid_world"),
                "a WORLD type name cannot certify a nonzero raw element reference");

            context = Fresh();
            Scenario.Elements["808/0"].Database = Aveva.Core.Database.MDB.Value.Databases[0];
            report = D(context.ElementAncestry("=901/23", 4));
            Check(Equals(report["complete"], false) && Equals(report["reached_world"], false)
                && HasCode(report, "invalid_world"),
                "a zero-element WORLD must match the exact SDK database WORLD identity");

            context = Fresh();
            Scenario.Overrides["e1.valid"] = false;
            report = D(context.ElementAncestry("=901/23", 4));
            Check(Equals(report["complete"], false) && HasCode(report, "invalid_world")
                && Equals(D(report["world_frame"])["available"], false)
                && Equals(report["reached_world"], true),
                "an unavailable selected WORLD frame preserves partial ancestry without missing-field exceptions");

            context = Fresh();
            Db catalogueEndpoint = Scenario.Database("dbCatalogueEndpoint", 404, 0, "CATALOGUE-ENDPOINT");
            catalogueEndpoint.TypeValue = Aveva.Core.Database.DbType.Catalogue;
            catalogueEndpoint.WorldValue = Scenario.World(71, new[] { 971, 0 }, "=9971/0", catalogueEndpoint);
            Aveva.Core.Database.MDB.Value.Databases = Aveva.Core.Database.MDB.Value.Databases
                .Concat(new[] { catalogueEndpoint }).ToArray();
            Scenario.Elements["902/7"].OwnerValue = catalogueEndpoint.WorldValue;
            report = D(context.ElementAncestry("=901/23", 4));
            Check(Equals(report["complete"], false) && Equals(report["reached_world"], false)
                && HasCode(report, "invalid_world"),
                "the ancestry endpoint must belong to a Design database even when another database has a WORLD root");

            // A getter can latch a fatal database callback while still returning a
            // normal value. Every subsequent SDK getter must then be blocked,
            // including the second half of an element-availability check.
            foreach (string faultPath in completeReadPaths)
            foreach (bool throws in new[] { false, true })
            {
                context = Fresh();
                int matrixReadsAtFault = 0;
                Scenario.OnRead = path =>
                {
                    if (path != faultPath) return;
                    context.Fault();
                    matrixReadsAtFault = Scenario.Reads.Count;
                    if (throws) Scenario.Overrides[path] = new InvalidOperationException("SDK getter failed after fault callback");
                };
                report = D(context.ElementAncestry("=901/23", 4));
                Check(matrixReadsAtFault > 0 && Scenario.Reads.Count == matrixReadsAtFault
                    && Scenario.Reads[Scenario.Reads.Count - 1] == faultPath
                    && report["native_database_fault"] != null && Equals(report["success"], false)
                    && Equals(report["complete"], false) && D(report["context"])["ce_after"] == null
                    && Scenario.ForbiddenCalls == 0,
                    "a native fault at " + faultPath + (throws ? " throwing" : " returning")
                    + " blocks all later SDK getters");
            }

            context = Fresh();
            var catalogue = Scenario.Database("dbCatalogue", 303, 0, "CATALOGUE");
            catalogue.TypeValue = Aveva.Core.Database.DbType.Catalogue;
            catalogue.WorldValue = Scenario.World(70, new[] { 970, 0 }, "=9970/0", catalogue);
            Aveva.Core.Database.MDB.Value.Databases = Aveva.Core.Database.MDB.Value.Databases.Concat(new[] { catalogue }).ToArray();
            Aveva.Core.Database.MDB.Value.FirstDesignWorld = catalogue.WorldValue;
            report = D(context.ElementAncestry("=901/23", 4));
            Check(HasCode(report, "invalid_world") && Equals(report["complete"], false),
                "the adapter-selected WORLD frame must resolve to a Design database");

            context = Fresh();
            report = D(context.ElementAncestry("=901/23", 1));
            Check(Ancestors(report).Count == 1 && Equals(report["truncated"], true)
                && Equals(report["reached_world"], false) && Equals(report["complete"], false),
                "the depth bound reports truncation without claiming the WORLD was reached");

            context = Fresh();
            report = D(context.ElementAncestry("=901/23", 64));
            Check(Equals(report["max_depth"], 64) && Equals(report["complete"], true),
                "the maximum supported traversal depth is accepted");

            context = Fresh();
            DbElement targetElement = Target();
            DbElement middle = Scenario.Elements["902/7"];
            middle.OwnerValue = targetElement;
            report = D(context.ElementAncestry("=901/23", 5));
            Check(HasCode(report, "cycle") && Equals(report["complete"], false)
                && Equals(report["reached_world"], false),
                "a repeated raw identity terminates an OWNER cycle");

            context = Fresh();
            Aveva.Core.Database.MDB.Value.Databases = new[] { Aveva.Core.Database.MDB.Value.Databases[0],
                Aveva.Core.Database.MDB.Value.Databases[0], Aveva.Core.Database.MDB.Value.Databases[1] };
            report = D(context.ElementAncestry("=901/23", 4));
            Check(HasCode(report, "duplicate_identity") && Equals(report["mdb_scan_complete"], false)
                && Equals(report["mdb_membership_complete"], false)
                && D(Ancestors(report)[0]["database"])["in_current_mdb"] == null,
                "duplicate exact MDB identities invalidate membership and leave it unknown");

            context = Fresh();
            Db extraExtract = Scenario.Database("dbExtra", 101, 1, "DESIGN-A-EXTRACT");
            extraExtract.WorldValue = Scenario.World(31, new[] { 931, 0 }, "=9931/0", extraExtract);
            Aveva.Core.Database.MDB.Value.Databases = new[] { Aveva.Core.Database.MDB.Value.Databases[0], extraExtract, Aveva.Core.Database.MDB.Value.Databases[1] };
            report = D(context.ElementAncestry("=901/23", 4));
            targetDb = D(Ancestors(report)[0]["database"]);
            Check(Equals(report["mdb_scan_complete"], true) && Equals(report["mdb_membership_complete"], true)
                && Equals(targetDb["matching_number_count"], 2) && targetDb["in_current_mdb"] == null
                && HasCode(report, "ambiguous_number"),
                "complete same-number extracts do not let DbElement.Db claim an ambiguous extract identity");

            context = Fresh();
            Db outsider = Scenario.Database("outside", 909, 4, "OUTSIDE");
            outsider.WorldValue = Scenario.World(40, new[] { 940, 0 }, "=9940/0", outsider);
            Target().Database = outsider;
            report = D(context.ElementAncestry("=901/23", 4));
            Check(Equals(report["mdb_membership_complete"], true)
                && Equals(D(Ancestors(report)[0]["database"])["in_current_mdb"], false),
                "a known database outside a complete MDB scan is explicitly false");

            context = Fresh();
            Scenario.Overrides["dbB.name"] = new InvalidOperationException("database name read failed");
            report = D(context.ElementAncestry("=901/23", 4));
            Check(HasCode(report, "read_failed") && Equals(report["mdb_scan_complete"], false)
                && Equals(report["mdb_membership_complete"], false)
                && D(Ancestors(report)[0]["database"])["in_current_mdb"] == null,
                "a partial database identity read never produces a negative membership claim");

            context = Fresh();
            int projectReads = 0;
            Scenario.OnRead = path =>
            {
                if (path == "project.current" && ++projectReads == 2)
                    Scenario.Overrides[path] = new Aveva.Core.Database.Project("PROJECT");
            };
            report = D(context.ElementAncestry("=901/23", 4));
            Check(Equals(D(report["context"])["identity_unchanged"], false)
                && Equals(report["mdb_membership_complete"], false)
                && D(Ancestors(report)[0]["database"])["in_current_mdb"] == null,
                "equal context names do not hide replacement of the live project object");

            context = Fresh();
            int readsAtFault = 0;
            Scenario.OnRead = path =>
            {
                if (path == "e11.ref") { context.Fault(); readsAtFault = Scenario.Reads.Count; }
            };
            report = D(context.ElementAncestry("=901/23", 4));
            Check(readsAtFault > 0 && Scenario.Reads.Count == readsAtFault
                && Scenario.Reads[Scenario.Reads.Count - 1] == "e11.ref"
                && report["native_database_fault"] != null && Equals(report["complete"], false)
                && D(report["context"])["ce_after"] == null && Scenario.ForbiddenCalls == 0,
                "a latched native fault ends further SDK reads and preserves only managed partial evidence");

            context = Fresh();
            Scenario.AfterOperation = context.Fault;
            report = D(context.ElementAncestry("=901/23", 4));
            Check(report["native_database_fault"] != null && Equals(report["success"], false)
                && Scenario.ForbiddenCalls == 0,
                "a native fault latched immediately after the UI callback invalidates success");

            Console.WriteLine("Element ancestry native production checks passed: " + checks);
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
