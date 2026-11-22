using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core.Geometry;
using Aveva.Core3D.Clasher;
using E3d21AiBridge;

// Production control-flow/contract tests only. Fakes cannot certify the native
// kernel's geometry, units, global diagnostic side effects or hidden cleanup.
internal static class NativeClashHarness
{
    internal static E3d21Context Context;
    internal static readonly Dictionary<string, DbElement> Elements = new Dictionary<string, DbElement>();
    internal static readonly List<string> Calls = new List<string>();
    internal static readonly List<Owned> Created = new List<Owned>();
    internal static readonly Dictionary<string, Clash[]> Categories = new Dictionary<string, Clash[]>();
    internal static readonly Dictionary<string, SpatialMapStatus> MapStatuses = new Dictionary<string, SpatialMapStatus>();
    internal static string FaultAt, ThrowCategory;
    internal static bool CheckValue, ThrowCheck, ExpandObstruction, IgnoreNonempty, WithinNoEffect, AlterOption, ChangeCe, ChangeProject;
    internal static string IgnoreInvalid;
    internal static DbElement[] Checked;
    internal static ObstructionList CheckedObstruction;
    internal static int NativeAfterFault, CheckCalls, checks;

    internal static void Touch(string name)
    {
        if (Context.Fault.HasFault) { NativeAfterFault++; throw new Exception("SDK called after fault: " + name); }
        Calls.Add(name);
        if (FaultAt == name) Context.Fault.Observe("Unhandled Database error 77 from test-clasher", "fixture");
    }
    private static void Check(bool condition, string message) { checks++; if (!condition) throw new Exception(message); }
    private static Dictionary<string, object> Row(object value) { return (Dictionary<string, object>)value; }
    private static Dictionary<string, object> Category(Dictionary<string, object> report, string category) { return Row(Row(report["results"])[category]); }
    private static List<object> Records(Dictionary<string, object> report, string category) { return (List<object>)Category(report, category)["records"]; }
    private static string Ref(int number) { return "=1/" + number; }
    internal static DbElement Add(int number, string type = "BOX")
    {
        var element = new DbElement { Number = number, Kind = type };
        Elements[Ref(number)] = element; return element;
    }
    private static void Child(DbElement parent, DbElement child)
    {
        child.Parent = parent;
        if (parent.Children.Count > 0) parent.Children.Last().Sibling = child;
        parent.Children.Add(child);
    }
    private static E3d21Context Reset()
    {
        Elements.Clear(); Calls.Clear(); Created.Clear(); Categories.Clear(); MapStatuses.Clear();
        FaultAt = ThrowCategory = null;
        IgnoreInvalid = null;
        CheckValue = true;
        ThrowCheck = ExpandObstruction = IgnoreNonempty = WithinNoEffect = AlterOption = ChangeCe = ChangeProject = false;
        NativeAfterFault = CheckCalls = 0; Checked = null; CheckedObstruction = null;
        foreach (string category in new[] { "clashes", "touches", "clearances", "not_proven" }) Categories[category] = new Clash[0];
        Context = new E3d21Context();
        Add(1); Add(2, "CYLI"); Add(3, "CONE");
        Child(Add(101, "EQUI"), Elements[Ref(1)]);
        Child(Add(102, "SUBE"), Elements[Ref(2)]);
        return Context;
    }
    private static Dictionary<string, object> Run(bool preview = false, int limit = 50)
    {
        return Row(Context.CheckNativeClashes(new[] { Ref(101) }, new[] { Ref(102) }, 0, limit, preview));
    }
    private static Clash Record(string name, int first = 1, int second = 2)
    {
        return new Clash(name, Elements[Ref(first)], Elements[Ref(second)]);
    }

    private static void InputAndPreview()
    {
        Reset();
        foreach (Action invalid in new Action[]
        {
            () => Context.CheckNativeClashes(null, new[] { Ref(2) }, 0, 50, true),
            () => Context.CheckNativeClashes(new string[0], new[] { Ref(2) }, 0, 50, true),
            () => Context.CheckNativeClashes(Enumerable.Range(1, 9).Select(Ref).ToArray(), new[] { Ref(20) }, 0, 50, true),
            () => Context.CheckNativeClashes(new[] { Ref(1), Ref(1) }, new[] { Ref(2) }, 0, 50, true),
            () => Context.CheckNativeClashes(new[] { Ref(1) }, new[] { Ref(1) }, 0, 50, true),
            () => Context.CheckNativeClashes(new[] { "CE" }, new[] { Ref(2) }, 0, 50, true),
            () => Context.CheckNativeClashes(new[] { Ref(1) }, new[] { Ref(2) }, double.NaN, 50, true),
            () => Context.CheckNativeClashes(new[] { Ref(1) }, new[] { Ref(2) }, double.PositiveInfinity, 50, true),
            () => Context.CheckNativeClashes(new[] { Ref(1) }, new[] { Ref(2) }, -1, 50, true),
            () => Context.CheckNativeClashes(new[] { Ref(1) }, new[] { Ref(2) }, 1000001, 50, true),
            () => Context.CheckNativeClashes(new[] { Ref(1) }, new[] { Ref(2) }, 0, 0, true),
            () => Context.CheckNativeClashes(new[] { Ref(1) }, new[] { Ref(2) }, 0, 201, true)
        })
        {
            bool threw = false; try { invalid(); } catch (ArgumentException) { threw = true; }
            Check(threw, "Invalid input must fail before any SDK access.");
        }
        Check(Calls.Count == 0 && Context.Dispatches == 0 && Context.Begins == 0, "Pure request validation never dispatches.");
        var report = Run(true);
        Check(Equals(report["success"], true) && Equals(report["preflight_complete"], true) && Equals(report["complete"], false),
            "Preview confirms bounded input admission without claiming a native result.");
        Check(Created.Count == 0 && CheckCalls == 0 && Context.Begins == 0 && Context.Dispatches == 1,
            "Preview reads input/context metadata without map access, Clasher objects or a write gate.");
        Check(Equals(report["no_clear_verdict"], true) && report["native_check_result"] == null && report["no_native_records"] == null,
            "Preview does not manufacture a clear/no-record result.");
        Reset(); Context.Busy = true; report = Run();
        Check(Equals(report["busy"], true) && Context.Dispatches == 0 && Created.Count == 0 && Context.Ends == 0, "Busy requests cannot acquire native objects or release another operation's gate.");
        Reset(); Elements["/ALIAS"] = Elements[Ref(1)];
        report = Row(Context.CheckNativeClashes(new[] { Ref(1) }, new[] { "/ALIAS" }, 0, 50, false));
        Check(Equals(report["success"], false) && CheckCalls == 0 && Created.Count == 0, "Resolved aliases cannot overlap across groups.");
        foreach (string unsupported in new[] { "NCYL", "NBOX", "NCON", "PANE" })
        {
            Reset(); Elements[Ref(101)].Kind = unsupported; report = Run();
            Check(Equals(report["success"], false) && Created.Count == 0, "Negative and unknown roots are rejected.");
        }
        Reset(); Elements[Ref(101)].DatabaseType = DbType.Catalog; report = Run();
        Check(Equals(report["success"], false) && Created.Count == 0 && CheckCalls == 0, "Non-Design roots are rejected before factories and Check.");
        Reset(); Child(Elements[Ref(1)], Add(10, "NCYL")); Elements[Ref(10)].DatabaseType = DbType.Catalog; report = Run();
        Check(Equals(report["success"], false) && Created.Count == 0 && CheckCalls == 0, "Non-Design negative members are rejected before factories and Check.");
    }

    private static void NegativeBudgets()
    {
        Reset();
        DbElement parent = Elements[Ref(1)];
        for (int index = 10; index < 17; index++) { var child = Add(index, index % 2 == 0 ? "NCYL" : "NBOX"); Child(parent, child); parent = child; }
        var report = Run(true);
        Check(Equals(report["success"], true) && Equals(report["input_nodes_read"], 11)
            && Equals(report["negative_geometry_count"], 7) && Equals(report["positive_geometry_count"], 2)
            && Equals(report["negative_cut_evaluation_verified"], false), "Depth-eight negative inventory is retained without claiming hole evaluation.");
        Child(parent, Add(17, "NCON")); report = Run();
        Check(Equals(report["success"], false) && Created.Count == 0, "Depth-nine membership rejects before native factories.");
        Reset();
        for (int n = 1000; n < 1252; n++) Child(Elements[Ref(1)], Add(n, "NCYL"));
        report = Run(true);
        Check(Equals(report["success"], true) && Equals(report["input_nodes_read"], 256), "Two aggregate roots, two positive primitives and 252 negatives share one 256-node request budget.");
        Child(Elements[Ref(2)], Add(1300, "NBOX")); report = Run();
        Check(Equals(report["success"], false) && Created.Count == 0, "The second group's children do not receive a fresh per-root budget.");
        Reset(); Child(Elements[Ref(1)], Add(10, "BOX")); report = Run();
        Check(Equals(report["success"], false) && Created.Count == 0, "Unknown/nonnegative children reject admission.");
        Reset(); Child(Elements[Ref(1)], Add(10, "NCYL")); Elements[Ref(10)].Sibling = Elements[Ref(10)]; report = Run();
        Check(Equals(report["success"], false) && Created.Count == 0, "A repeated member/cycle fails bounded admission.");
    }

    private static void AggregateScopeAndGranularity()
    {
        foreach (string primitive in new[] { "BOX", "CYLI", "CONE" })
        {
            Reset(); Elements[Ref(1)].Kind = primitive;
            var preview = Row(Context.CheckNativeClashes(new[] { Ref(1) }, new[] { Ref(2) }, 0, 50, true));
            Check(Equals(preview["success"], true) && Equals(preview["native_check_eligible"], false)
                && Equals(preview["native_check_ineligibility_reason"], "native_spatial_map_check_suspended") && Equals(preview["input_geometry_native_granularity_supported"], false) && Created.Count == 0,
                "Primitive inputs remain inspectable through preview without claiming a supported native check granularity.");
            var actual = Row(Context.CheckNativeClashes(new[] { Ref(1) }, new[] { Ref(2) }, 0, 50, false));
            Check(Equals(actual["success"], false) && Equals(actual["complete"], false) && Equals(actual["native_check_attempted"], false)
                && Equals(actual["reason"], "native_spatial_map_check_suspended") && Created.Count == 0 && CheckCalls == 0,
                "Direct primitive execution fails closed before every Clasher factory.");
        }
        Reset(); Child(Elements[Ref(101)], Add(201, "SUBE")); Child(Elements[Ref(201)], Add(202, "CONE"));
        Child(Elements[Ref(202)], Add(203, "NCON")); Categories["clashes"] = new[] { Record("nested", 202, 2), Record("negative", 203, 2) };
        var report = Run(true);
        Check(Equals(report["success"], true) && Equals(report["positive_geometry_count"], 3) && Equals(report["negative_geometry_count"], 1)
            && Equals(report["input_nodes_read"], 7) && report["scope_complete"] == null,
            "All admitted descendants, including negatives and nested subequipment, receive their input group's membership.");
        var accepted = report["accepted_inputs"];
        var first = (List<object>)accepted.GetType().GetProperty("first").GetValue(accepted, null);
        var root = Row(first[0]);
        Check(Equals(root["subtree_membership_complete"], true) && Equals(root["descendant_count"], 4)
            && Equals(root["positive_geometry_count"], 2) && Equals(root["negative_geometry_count"], 1)
            && ((List<object>)root["descendants"]).Count == 4, "Each root reports bounded descendants and positive/negative geometry counts.");
        foreach (bool sameGroup in new[] { false, true })
        {
            Reset(); Child(Elements[Ref(101)], Add(201, "SUBE")); Child(Elements[Ref(201)], Add(202, "BOX"));
            report = sameGroup ? Row(Context.CheckNativeClashes(new[] { Ref(101), Ref(201) }, new[] { Ref(102) }, 0, 50, false)) :
                Row(Context.CheckNativeClashes(new[] { Ref(101) }, new[] { Ref(201) }, 0, 50, false));
            Check(Equals(report["success"], false) && CheckCalls == 0 && Created.Count == 0,
                "A descendant cannot also be a second input root, within a group or across groups.");
        }
        Reset(); Elements["/NESTED_ALIAS"] = Elements[Ref(1)];
        report = Row(Context.CheckNativeClashes(new[] { Ref(101) }, new[] { "/NESTED_ALIAS" }, 0, 50, false));
        Check(Equals(report["success"], false) && Created.Count == 0, "Canonical descendant aliases cannot cross groups.");
        foreach (string childType in new[] { "PANE", "NCYL" })
        {
            Reset(); Child(Elements[Ref(101)], Add(201, childType)); report = Run();
            Check(Equals(report["success"], false) && Created.Count == 0, "Unknown children and negatives directly under aggregates fail closed.");
        }
        Reset(); Elements[Ref(101)].Children.Clear(); report = Run();
        Check(Equals(report["success"], false) && Created.Count == 0 && CheckCalls == 0, "Each aggregate root needs at least one positive primitive.");
        Reset(); Elements[Ref(1)].Parent = Elements[Ref(102)]; report = Run();
        Check(Equals(report["success"], false) && Created.Count == 0, "Aggregate descendant ownership must match the observed tree.");
    }

    private static void SuspendedNativeMapPolicy()
    {
        foreach (bool preview in new[] { false, true })
        foreach (SpatialMapStatus ignoredStatus in Enum.GetValues(typeof(SpatialMapStatus)).Cast<SpatialMapStatus>().Concat(new[] { (SpatialMapStatus)999 }))
        {
            Reset(); MapStatuses["1/0"] = ignoredStatus;
            var report = Run(preview);
            Check(Equals(report["success"], preview) && Equals(report["complete"], false) && Equals(report["preflight_complete"], true), "Preview remains useful; actual execution fails under every configured fake native status.");
            Check(Equals(report["native_check_eligible"], false) && Equals(report["native_check_ineligibility_reason"], "native_spatial_map_check_suspended")
                && Equals(report["spatial_map_check_reason"], "native_spatial_map_check_suspended"), "Both preview and execution expose the fixed suspension reason.");
            if (!preview) Check(Equals(report["reason"], "native_spatial_map_check_suspended"), "Actual request has the explicit root failure reason.");
            Check(((string)report["spatial_map_check_message"]).Contains("2026-11-16 E3D21 native stack overflow remains unresolved"), "The unresolved native stack overflow is explicit.");
            Check(Equals(report["spatial_map_check_attempted"], false) && Equals(report["spatial_map_check_completed"], false)
                && Equals(report["spatial_map_preflight_complete"], false) && report["input_spatial_maps_up_to_date"] == null && report["input_spatial_maps_accepted"] == null, "Unknown statuses are never fabricated as accepted or up-to-date.");
            var rows = ((List<object>)report["input_databases"]).Cast<Dictionary<string, object>>().ToArray();
            Check(rows.Length == 1 && Equals(rows[0]["number"], 1) && Equals(rows[0]["extract_number"], 0)
                && Equals(rows[0]["is_extract"], false) && Equals(rows[0]["is_primary"], true), "Input database metadata and deduplication survive suspension.");
            Check(rows.All(row => row["status"] == null && row["status_value"] == null && row["up_to_date"] == null && row["accepted"] == null
                && Equals(row["check_attempted"], false) && Equals(row["check_completed"], false) && Equals(row["reason"], "native_spatial_map_check_suspended")), "Every database row states that checking never began.");
            Check(Created.Count == 0 && CheckCalls == 0 && !Calls.Any(value => value == "SpatialMap.Instance" || value.StartsWith("CheckSpatialMap:") || value.StartsWith("Create") || value.Contains("BuildSpatialMap")), "No map singleton, map check, Clasher factory or rebuild is reached.");
            Check(Equals(Row(report["spatial_map_admission_policy"])["mode"], "suspended") && ((string[])Row(report["spatial_map_admission_policy"])["accepted_statuses"]).Length == 0
                && Equals(Row(report["override_policy"])["enabled"], false), "Policy and override never advertise active admission.");
            Check(report["native_check_result"] == null && report["no_native_records"] == null && Equals(report["no_clear_verdict"], true), "Suspension cannot claim no clashes or clearance.");
        }
        Reset(); foreach (DbElement element in Elements.Values) element.Database = new Db { NumberValue = 1, ExtractValue = 1, IsExtractValue = false, IsPrimaryValue = true };
        var primary = Run(true);
        Check(Equals(Row(((List<object>)primary["input_databases"])[0])["extract_number"], 1) && Equals(primary["success"], true) && CheckCalls == 0, "Primary extract-number 1 metadata remains inspectable.");
        Reset(); Elements[Ref(102)].Database = new Db { NumberValue = 1, ExtractValue = 2, IsExtractValue = true };
        Elements[Ref(2)].Database = Elements[Ref(102)].Database; var different = Run(true);
        Check(((List<object>)different["input_databases"]).Count == 2 && Equals(different["spatial_map_input_scope_supported"], false) && CheckCalls == 0, "Different extract identities remain separate metadata rows.");
        foreach (string fault in new[] { "db", "dbnumber", "dbextract", "dbisextract", "dbisprimary" })
        {
            Reset(); FaultAt = fault; var report = Run();
            Check(Equals(report["success"], false) && Equals(report["recovery_required"], true) && NativeAfterFault == 0 && Created.Count == 0 && CheckCalls == 0, "Metadata native faults retain the existing stop barrier.");
        }
        foreach (string unreachable in new[] { "SpatialMap.Instance", "CheckSpatialMap:1/0", "CreateOptions", "Check" })
        {
            Reset(); FaultAt = unreachable; var report = Run();
            Check(!Calls.Contains(unreachable) && !report.ContainsKey("recovery_required") && Equals(report["reason"], "native_spatial_map_check_suspended"), "Dangerous native callback cannot be reached under the fixed policy.");
        }
    }

    private static int Main()
    {
        try { InputAndPreview(); NegativeBudgets(); AggregateScopeAndGranularity(); SuspendedNativeMapPolicy(); Console.WriteLine("Native clash production checks passed: " + checks); return 0; }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}

internal abstract class Owned : IDisposable
{
    internal readonly string Label;
    internal int DisposeCount;
    internal Owned(string label) { Label = label; NativeClashHarness.Created.Add(this); }
    public void Dispose() { DisposeCount++; NativeClashHarness.Touch("dispose:" + Label); if (DisposeCount > 1) throw new Exception("Double Dispose"); }
}

namespace E3d21AiBridge
{
    internal static class ModelEditInput
    {
        // PRODUCTION_REFERENCE_VALIDATOR
    }
    public sealed partial class E3d21Context
    {
        // PRODUCTION_SUSPENSION_POLICY
        internal readonly NativeDatabaseFaultState Fault = new NativeDatabaseFaultState();
        private NativeDatabaseFaultState nativeFaultState { get { return Fault; } }
        internal bool Busy;
        internal int Begins, Ends, Dispatches;
        private readonly object messageLock = new object();
        private readonly Queue<Dictionary<string, object>> outputMessages = new Queue<Dictionary<string, object>>();
        private bool TryBeginCommand(string label) { Begins++; ThrowIfNativeFaulted(); return !Busy; }
        private void EndCommand() { Ends++; }
        private bool IsCommandActive() { ThrowIfNativeFaulted(); return Busy; }
        private T OnUi<T>(Func<T> action) { Dispatches++; return action(); }
        private static void ThrowIfNativeFaulted() { if (NativeClashHarness.Context.Fault.HasFault) throw new NativeDatabaseFaultException(); }
        private static T NativeRead<T>(Func<T> read) { ThrowIfNativeFaulted(); try { return read(); } finally { ThrowIfNativeFaulted(); } }
        private static void NativeWrite(Action write) { ThrowIfNativeFaulted(); try { write(); } finally { ThrowIfNativeFaulted(); } }
        private static bool IsUsable(DbElement value) { return value != null && !value.IsNull; }
        private static string ModelReference(int[] reference) { return "=" + reference[0] + "/" + reference[1]; }
        private static DbElement ModelRequireElement(string reference)
        {
            NativeClashHarness.Touch("resolve");
            DbElement result;
            if (!NativeClashHarness.Elements.TryGetValue(reference, out result)) throw new InvalidOperationException("Element missing");
            return result;
        }
        private static string ModelCurrentReference()
        {
            NativeClashHarness.Touch("ce");
            return NativeClashHarness.ChangeCe && NativeClashHarness.CheckCalls > 0 ? "=9/2" : "=9/1";
        }
    }
}

namespace Aveva.Core.Database
{
    public enum DbType { Design, Catalog }
    public sealed class Db
    {
        internal int NumberValue = 1, ExtractValue;
        internal bool IsExtractValue, IsPrimaryValue = true;
        public int Number { get { NativeClashHarness.Touch("dbnumber"); return NumberValue; } }
        public int ExtractNumber { get { NativeClashHarness.Touch("dbextract"); return ExtractValue; } }
        public bool IsExtract { get { NativeClashHarness.Touch("dbisextract"); return IsExtractValue; } }
        public bool IsPrimary { get { NativeClashHarness.Touch("dbisprimary"); return IsPrimaryValue; } }
    }
    public sealed class DbAttribute { }
    public static class DbAttributeInstance { public static readonly DbAttribute NAME = new DbAttribute(); }
    public sealed class DbElementType
    {
        internal string Value;
        public string ShortName { get { NativeClashHarness.Touch("type"); return Value; } }
    }
    public sealed class DbElement
    {
        internal int Number;
        internal string Kind;
        internal DbType DatabaseType = DbType.Design;
        internal Db Database = new Db();
        internal DbElement Parent, Sibling;
        internal readonly List<DbElement> Children = new List<DbElement>();
        public int[] Ref { get { NativeClashHarness.Touch("ref"); return new[] { 1, Number }; } }
        public DbType DbType { get { NativeClashHarness.Touch("dbtype"); return DatabaseType; } }
        public Db Db { get { NativeClashHarness.Touch("db"); return Database; } }
        public bool IsNull { get { NativeClashHarness.Touch("isnull"); return false; } }
        public DbElement Owner { get { NativeClashHarness.Touch("owner"); return Parent; } }
        public DbElementType GetElementType() { NativeClashHarness.Touch("elementtype"); return new DbElementType { Value = Kind }; }
        public string GetAsString(DbAttribute attribute) { NativeClashHarness.Touch("name"); return "/" + Kind + Number; }
        public DbElement FirstMember() { NativeClashHarness.Touch("firstmember"); return Children.FirstOrDefault(); }
        public DbElement Next() { NativeClashHarness.Touch("next"); return Sibling; }
    }
    public sealed class Project
    {
        public static Project CurrentProject { get { NativeClashHarness.Touch("project"); return new Project(); } }
        public string Name { get { NativeClashHarness.Touch("projectname"); return NativeClashHarness.ChangeProject && NativeClashHarness.CheckCalls > 0 ? "CHANGED" : "PROJECT"; } }
    }
    public sealed class MDB
    {
        public static MDB CurrentMDB { get { NativeClashHarness.Touch("mdb"); return new MDB(); } }
        public string Name { get { NativeClashHarness.Touch("mdbname"); return "MDB"; } }
    }
}

namespace Aveva.Core.Geometry
{
    public abstract class Position
    {
        public abstract double X { get; }
        public abstract double Y { get; }
        public abstract double Z { get; }
    }
}

namespace Aveva.Core.Geometry.Implementation
{
    public sealed class PositionImpl : Position
    {
        private readonly object point;
        internal PositionImpl(string owner) { point = new OwnedPoint("position:" + owner); }
        public object ToD3Point() { return point; } // Exactly the audited managed-only child access.
        public override double X { get { NativeClashHarness.Touch("x"); return 1; } }
        public override double Y { get { NativeClashHarness.Touch("y"); return 2; } }
        public override double Z { get { NativeClashHarness.Touch("z"); return 3; } }
    }
    internal sealed class OwnedPoint : Owned
    {
        internal OwnedPoint(string label) : base(label) { }
    }
}

namespace Aveva.Core3D.Clasher
{
    public enum SpatialMapStatus { UNKNOWN, UPTODATE, NOWARNINGS, WARNINGS, ERRORS, POSERRORS, UNCHECKED }
    internal sealed class SpatialMap
    {
        public static SpatialMap Instance { get { NativeClashHarness.Touch("SpatialMap.Instance"); return new SpatialMap(); } }
        public SpatialMapStatus CheckSpatialMap(Db database)
        {
            string key = database.NumberValue + "/" + database.ExtractValue;
            NativeClashHarness.Touch("CheckSpatialMap:" + key);
            SpatialMapStatus status;
            return NativeClashHarness.MapStatuses.TryGetValue(key, out status) ? status : SpatialMapStatus.UPTODATE;
        }
    }
    public enum BranchCheck { ACHECK, BCHECK }
    public enum LMIDisplayType { CELLS, EXPANDCELLS, TIGHTBOXES, EXACTGEOMETRY }
    public enum ClashType { HH, HS, NP }
    internal sealed class ClashOptions : Owned
    {
        private bool withinApplied;
        private ClashOptions() : base("options") { }
        public static ClashOptions Create() { var value = new ClashOptions(); NativeClashHarness.Touch("CreateOptions"); return value; }
        public double TouchGap { get; set; }
        public double Clearance { get; set; }
        public double TouchOverlap { get; set; }
        public bool Override { get; set; }
        public bool Midpoint { get; set; }
        public bool Segments { get; set; }
        public BranchCheck BranchCheckType { get; set; }
        public bool IncludeConnections { get; set; }
        public bool IgnoreConnectionsWithSpecifications { get; set; }
        private bool touches;
        public bool IncludeTouches { get { return NativeClashHarness.AlterOption ? !touches : touches; } set { touches = value; } }
        public bool HullDesign { get; set; }
        public bool HullProduction { get; set; }
        public bool LMITestExact { get; set; }
        public LMIDisplayType LMIDisplay { get; set; }
        public double LMIDisplayExpandCells { get; set; }
        public DbElementType[] IgnoreClashes
        {
            get
            {
                NativeClashHarness.Touch("ignoreclashes");
                if (NativeClashHarness.IgnoreInvalid == "null") return null;
                if (NativeClashHarness.IgnoreInvalid == "null_member") return new DbElementType[] { null };
                if (NativeClashHarness.IgnoreInvalid == "over_limit") return Enumerable.Range(0, 65).Select(value => new DbElementType { Value = "BOX" }).ToArray();
                return NativeClashHarness.IgnoreNonempty && !withinApplied ?
                    new[] { "PCOJ", "WALL", "TMPL", "SCTN", "GENSEC", "HPANEL", "STWALL", "PLTFRM", "CWBRAN" }.Select(value => new DbElementType { Value = value }).ToArray() : new DbElementType[0];
            }
        }
        public void Within(DbElementType[] ignored)
        {
            NativeClashHarness.Touch("Within");
            if (!NativeClashHarness.WithinNoEffect) withinApplied = true;
        }
    }
    internal sealed class ObstructionList : Owned
    {
        private readonly List<DbElement> values = new List<DbElement>();
        private ObstructionList() : base("obstructions") { AllObstructions = true; }
        public static ObstructionList Create() { var value = new ObstructionList(); NativeClashHarness.Touch("CreateObstructions"); return value; }
        public bool AllObstructions { get; set; }
        public void RemoveAllObstructions() { NativeClashHarness.Touch("clearobstructions"); values.Clear(); }
        public void RemoveAllExclusions() { NativeClashHarness.Touch("clearexclusions"); }
        public void AddObstructions(DbElement[] input) { NativeClashHarness.Touch("addobstructions"); values.AddRange(input); if (NativeClashHarness.ExpandObstruction) values.Add(NativeClashHarness.Elements["=1/3"]); }
        public DbElement[] Obstructions { get { NativeClashHarness.Touch("obstructions"); return values.ToArray(); } }
        public DbElement[] Exclusions { get { NativeClashHarness.Touch("exclusions"); return new DbElement[0]; } }
    }
    internal sealed class ClashSet : Owned
    {
        private ClashSet() : base("set") { }
        public static ClashSet Create() { var value = new ClashSet(); NativeClashHarness.Touch("CreateSet"); return value; }
        private Clash[] Read(string category)
        {
            NativeClashHarness.Touch("get:" + category);
            if (NativeClashHarness.ThrowCategory == category) throw new InvalidOperationException("category read failed");
            return NativeClashHarness.Categories[category];
        }
        public Clash[] Clashes { get { return Read("clashes"); } }
        public Clash[] Touches { get { return Read("touches"); } }
        public Clash[] Clearances { get { return Read("clearances"); } }
        public Clash[] NotProven { get { return Read("not_proven"); } }
    }
    internal sealed class Clash : Owned
    {
        private readonly DbElement first, second;
        internal bool BadPosition;
        internal Clash(string label, DbElement first, DbElement second) : base(label) { this.first = first; this.second = second; }
        public DbElement First { get { NativeClashHarness.Touch("record:" + Label + ":first"); return first; } }
        public DbElement Second { get { NativeClashHarness.Touch("record:" + Label + ":second"); return second; } }
        public ClashType Type { get { NativeClashHarness.Touch("record:" + Label + ":type"); return Label == "not_proven" ? ClashType.NP : ClashType.HH; } }
        public int InstanceNumber { get { NativeClashHarness.Touch("record:" + Label + ":number"); return 7; } }
        public Position ClashPosition
        {
            get
            {
                if (BadPosition) { NativeClashHarness.Touch("record:" + Label + ":position"); throw new InvalidOperationException("position unavailable"); }
                var position = new Aveva.Core.Geometry.Implementation.PositionImpl(Label);
                NativeClashHarness.Touch("record:" + Label + ":position");
                return position;
            }
        }
    }
    internal sealed class Clasher
    {
        public static Clasher Instance { get { NativeClashHarness.Touch("engine"); return new Clasher(); } }
        public bool Check(DbElement[] first, ClashOptions options, ObstructionList obstruction, ClashSet set)
        {
            if (!options.Override) throw new InvalidOperationException("The controlled native-clash contract requires explicit Override=true.");
            NativeClashHarness.CheckCalls++; NativeClashHarness.Checked = first; NativeClashHarness.CheckedObstruction = obstruction;
            NativeClashHarness.Touch("Check");
            if (NativeClashHarness.ThrowCheck) throw new InvalidOperationException("Check did not return");
            return NativeClashHarness.CheckValue;
        }
    }
}
