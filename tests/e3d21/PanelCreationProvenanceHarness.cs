using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core.Geometry;
using Aveva.Core.Utilities.Messaging;
using E3d21AiBridge;

internal static class Program
{
    private static int checks;
    private static void Check(bool condition, string message)
    { checks++; if (!condition) throw new Exception(message + "\n" + string.Join("\n", Scenario.Trace.Skip(Math.Max(0, Scenario.Trace.Count - 15)))); }
    private static Dictionary<string, object> Run(bool preview = false)
    {
        return (Dictionary<string, object>)Scenario.Context.CreatePanel(Scenario.Parent.Key, "/PANEL",
            new[] { new[] { 0.0, 0 }, new[] { 100.0, 0 }, new[] { 0.0, 100 } }, 5,
            null, null, null, "OWNER", "positive", preview);
    }
    private static bool Flag(Dictionary<string, object> r, string key, bool expected = true)
    { return r.ContainsKey(key) && Equals(r[key], expected); }
    private static void Unknown(Dictionary<string, object> result, string message)
    {
        Check(!Flag(result, "success") && Flag(result, "creation_result_unknown") && Flag(result, "outcome_uncertain") &&
            Flag(result, "retry_safe", false) && Scenario.Deletes == 0, message);
    }
    private static void Cleaned(Dictionary<string, object> result, string message)
    {
        Check(!Flag(result, "success") && Flag(result, "cleanup_succeeded") && Flag(result, "retry_safe") &&
            Flag(result, "cleanup_tree_verified") && Flag(result, "cleanup_registered_nodes_verified_deleted") && Scenario.Deletes == 1 &&
            Scenario.Created.All(e => e.Deleted) && Scenario.Parent.Children.SequenceEqual(new[] { Scenario.Original }), message);
    }
    public static int Main(string[] arguments)
    {
        try
        {
            if (arguments.Length == 1 && arguments[0] == "--boundary-points") return BoundaryChecks();
            return Checks();
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
    private static int Checks()
    {
        Scenario.Reset(); var result = Run();
        Check(Flag(result, "success"), "Normal panel succeeds: " + (result.ContainsKey("error") ? result["error"] : ""));
        Check(Scenario.Creates == 5 && Scenario.Deletes == 0 && Flag(result, "creation_journal_complete"), "All five nodes registered without deletion.");
        var journal = (List<object>)result["creation_journal"];
        Check(journal.Count == 5 && ((List<string>)((Dictionary<string, object>)journal[0])["children"]).Count == 1 &&
            ((List<string>)((Dictionary<string, object>)journal[1])["children"]).Count == 3 &&
            ((List<string>)((Dictionary<string, object>)journal[4])["children"]).Count == 0, "Journal retains exact ordered children and empty leaves.");
        int[] successFaults = LegacyFaultPoints(Scenario.Trace);
        Scenario.Reset(); result = Run(true);
        Check(Flag(result, "success") && Scenario.Creates == 0 && Scenario.Writes == 0 && Scenario.Deletes == 0, "Preview never mutates.");
        Scenario.Reset(); Scenario.PreflightFailure = true; result = Run();
        Check(!Flag(result, "success") && Flag(result, "retry_safe") && Scenario.Creates == 0 && Scenario.Deletes == 0, "Preflight rejection never mutates.");

        // Failure at every registered creation stage must still clean up its own tree.
        foreach (string target in new[] { "set.1.NAME", "set.1.POS", "set.1.ORI", "set.2.HEIG", "set.2.SJUS", "set.2.LBLIND", "set.3.POS", "set.3.FRAD", "set.4.POS", "set.5.FRAD", "get.5.POS", "get.2.HEIG", "get.1.THICKN" })
        {
            Scenario.Reset(); Scenario.ThrowLabel = target; result = Run(); Cleaned(result, "Proven tree cleans after " + target);
        }
        // The second root member read is post-registration, before assignment to CreatePanel.pane.
        Scenario.Reset(); Scenario.ThrowLabel = "first.1"; Scenario.ThrowOccurrence = 2; result = Run();
        Cleaned(result, "Registered root is recoverable when tracked helper throws before returning.");

        foreach (int stage in new[] { 1, 2, 3, 5 })
        foreach (string mode in new[] { "throw-before", "throw-after", "null-before", "null-after", "existing", "registered", "wrong-type", "wrong-owner", "wrong-db", "resolve-alias", "nonempty", "extra-sibling", "prepend" })
        {
            if (mode == "registered" && stage == 1) continue;
            if (mode == "prepend" && (stage == 2 || stage == 3)) continue; // Empty lists have no alternate order.
            Scenario.Reset(); Scenario.BadCreate = stage; Scenario.CreateMode = mode; result = Run();
            Unknown(result, "Unverified create must block cleanup: " + mode + " at " + stage);
            Check(Scenario.Creates == stage && Scenario.UnverifiedWrites == 0, "Unverified return never receives attributes or triggers another create: " + mode);
            Check(Flag(result, "unverified_creation_attempt"), "Unknown creation reported explicitly.");
        }

        // Alter a tree after earlier attributes were written, before another CreateLast.
        foreach (string location in new[] { "root", "loop", "leaf", "external", "owner", "type", "order" })
        {
            Scenario.Reset(); Scenario.OnTouch = label => {
                if (label != "set.4.FRAD") return;
                Scenario.OnTouch = null;
                if (location == "root") Scenario.AddForeign(Scenario.Created[0]);
                else if (location == "loop") Scenario.AddForeign(Scenario.Created[1]);
                else if (location == "leaf") Scenario.AddForeign(Scenario.Created[2]);
                else if (location == "external") Scenario.AddForeign(Scenario.Parent);
                else if (location == "owner") Scenario.Created[2].Parent = Scenario.Parent;
                else if (location == "type") Scenario.Created[2].TypeValue = DbElementType.PLOO;
                else Scenario.Created[1].Children.Reverse();
            };
            result = Run(); Unknown(result, "Foreign tree state rejects cleanup: " + location);
            Check(Scenario.Creates == 4, "Tree is checked before the next create: " + location);
        }
        // Add a child to the final leaf and fail its setter: cleanup must inspect empty leaves too.
        Scenario.Reset(); Scenario.ThrowLabel = "set.5.FRAD";
        Scenario.OnTouch = label => { if (label == "set.5.FRAD") Scenario.AddForeign(Scenario.Created[4]); };
        result = Run(); Unknown(result, "Foreign child under final PAVE blocks cascading deletion.");

        foreach (string mode in new[] { "survives", "lookup-alias", "lookup-null" })
        {
            Scenario.Reset(); Scenario.ThrowLabel = "set.5.FRAD"; Scenario.DeleteMode = mode; result = Run();
            Check(Scenario.Deletes == 1 && Flag(result, "cleanup_succeeded", false) && Flag(result, "creation_result_unknown") &&
                Flag(result, "retry_safe", false) && Flag(result, "cleanup_registered_nodes_verified_deleted", false), "Every descendant needs exact deletion readback: " + mode);
        }
        // Preserve the original panel/journal/cleanup fault points. Qualifier
        // entries are tracked too; the new boundary helper is tested below.
        // Existing inline qualifier construction is outside this change's scope.
        foreach (int fault in successFaults)
        {
            Scenario.Reset(); Scenario.FaultAtCall = fault; ExpectFault();
        }
        Scenario.Reset(); Scenario.ThrowLabel = "set.5.FRAD"; result = Run(); Cleaned(result, "Cleanup baseline.");
        int[] cleanupFaults = LegacyFaultPoints(Scenario.Trace);
        foreach (int fault in cleanupFaults)
        {
            Scenario.Reset(); Scenario.ThrowLabel = "set.5.FRAD"; Scenario.FaultAtCall = fault; ExpectFault();
        }
        Console.WriteLine("Panel provenance production checks passed: " + checks + "; native injection points: " + (successFaults.Length + cleanupFaults.Length));
        return 0;
    }
    private static void ExpectFault()
    {
        bool caught = false;
        try { Run(); } catch (NativeDatabaseFaultException) { caught = true; }
        Check(caught && Scenario.NativeAfterFault == 0 && Scenario.Context.Ends == 1,
            "Native fault must stop all subsequent SDK calls at " + Scenario.FaultAtCall + ".");
    }

    private static DbElement BoundaryFixture(bool curved = true)
    {
        Scenario.Reset();
        Check(Flag(Run(), "success"), "Boundary fixture uses the unchanged successful creation path.");
        if (curved) Scenario.Created[2].Values["FRAD"] = 10.0;
        Scenario.Trace.Clear(); Scenario.QualifierAdds = 0;
        return Scenario.Created[0];
    }
    private static List<object> BoundaryVertices(Dictionary<string, object> geometry)
    { return (List<object>)((Dictionary<string, object>)((List<object>)geometry["loops"])[0])["vertices"]; }
    private static Dictionary<string, object> BoundaryOf(Dictionary<string, object> geometry, int vertex = 0)
    { return (Dictionary<string, object>)((Dictionary<string, object>)BoundaryVertices(geometry)[vertex])["boundary_points"]; }
    private static Dictionary<string, object> BoundaryFrame(Dictionary<string, object> geometry, string frame = "panel_local")
    { return (Dictionary<string, object>)BoundaryOf(geometry)[frame]; }
    private static Dictionary<string, object> BoundaryPoint(Dictionary<string, object> frame, int number)
    { return ((List<object>)frame["points"]).Cast<Dictionary<string, object>>().Single(point => Equals(point["number"], number)); }
    private static Dictionary<string, object> InspectBoundary(DbElement pane, bool includeWorld = false, bool includeBoundary = true)
    { return (Dictionary<string, object>)Scenario.Context.GetPanelGeometry(pane.Key, includeWorld, includeBoundary); }
    private static bool IsBoundaryRead(string label)
    { return label.StartsWith("boundary.", StringComparison.Ordinal); }
    private static bool IsQualifierEntry(string label)
    { return label.StartsWith("qualifier.", StringComparison.Ordinal); }
    private static int[] LegacyFaultPoints(IList<string> trace)
    { return Enumerable.Range(1, trace.Count).Where(index => !IsQualifierEntry(trace[index - 1])).ToArray(); }
    private static int BoundaryChecks()
    {
        DbElement pane = BoundaryFixture();
        int writes = Scenario.Writes;
        var geometry = (Dictionary<string, object>)Scenario.Context.GetPanelGeometry(pane.Key);
        string[] defaultTrace = Scenario.Trace.ToArray();
        Check(Flag(geometry, "complete") && !Scenario.Trace.Any(IsBoundaryRead) && Scenario.QualifierAdds == 0,
            "Default includeBoundary=false performs no derived-point reads, including at nonzero FRAD.");
        Scenario.Trace.Clear();
        var explicitFalse = InspectBoundary(pane, false, false);
        Check(Scenario.Trace.SequenceEqual(defaultTrace) && !explicitFalse.ContainsKey("boundary_points_requested"),
            "Explicit false and omitted includeBoundary have exactly the same SDK trace and result shape.");

        pane = BoundaryFixture(); geometry = InspectBoundary(pane);
        var local = BoundaryFrame(geometry);
        Check(Flag(geometry, "complete") && Flag(geometry, "success") && Flag(geometry, "boundary_points_requested") &&
            Flag(geometry, "boundary_points_complete") && Flag(geometry, "boundary_points_read_complete") &&
            Flag(geometry, "boundary_points_source_geometry_complete"), "Complete local derived evidence has independent successful completion.");
        Check(Equals(geometry["boundary_points_vertex_count"], 3) && Equals(geometry["boundary_points_curved_vertex_count"], 1),
            "Boundary counts cover the enumerated three vertices and one curved vertex.");
        Check(Flag(BoundaryOf(geometry), "required") && Flag(local, "complete") && Equals(local["frame_refno"], pane.Key) && Equals(local["unit"], "mm"),
            "Curved local evidence declares its exact PANE frame and units.");
        Check(((List<object>)local["points"]).Cast<Dictionary<string, object>>().Select(point => (int)point["number"]).SequenceEqual(new[] { 0, 4, 6, 8 }),
            "P0/P4/P6/P8 are retained in stable native point order.");
        var p0 = BoundaryPoint(local, 0);
        Check(p0["exists"] == null && Flag(p0, "exists_checked", false) && Flag(p0, "complete") && Flag(p0, "direction_complete") &&
            ((double[])p0["position_mm"]).SequenceEqual(new[] { 30.0, 0, 0 }) &&
            ((double[])p0["direction"]).SequenceEqual(new[] { 0.0, 1, 0 }), "P0 is read directly with native local position and direction, without fabricated existence.");
        Check(!Scenario.Trace.Any(label => label.Contains(".PPEXST.0")) &&
            Scenario.Trace.Count(label => label == "boundary.3.PDIR.0.wrt:1") == 1,
            "P0 never depends on PPEXST(0), and PDIR uses the PANE qualifier.");
        Check(Scenario.Trace.Where(IsBoundaryRead).Count() == 8 && !BoundaryOf(geometry).ContainsKey("WORLD") &&
            !Scenario.Trace.Any(label => label.StartsWith("frame.WORLD", StringComparison.Ordinal)),
            "One curved local vertex reads exactly four positions, one direction and three existence values; WORLD is opt-in.");
        Check(Flag(BoundaryOf(geometry, 1), "required", false) && Flag(BoundaryOf(geometry, 2), "required", false) &&
            !Scenario.Trace.Any(label => label.StartsWith("boundary.4.", StringComparison.Ordinal) || label.StartsWith("boundary.5.", StringComparison.Ordinal)),
            "FRAD-zero vertices skip all derived SDK reads.");
        Check(Scenario.Writes == writes && Scenario.Deletes == 0 && Scenario.Creates == 5, "Inspection adds no native writes, creates or deletes.");

        pane = BoundaryFixture(); geometry = InspectBoundary(pane, true);
        local = BoundaryFrame(geometry); var world = BoundaryFrame(geometry, "WORLD");
        Check(Flag(geometry, "complete") && Flag(geometry, "world_positions_complete") && Flag(geometry, "boundary_points_complete") &&
            Equals(world["frame_refno"], Scenario.World.Key) && Equals(geometry["world_position_frame"], Scenario.World.Key),
            "WORLD boundary evidence agrees with the production WORLD control-point frame.");
        Check(((double[])BoundaryPoint(world, 0)["position_mm"]).SequenceEqual(new[] { 1030.0, 2000, 3000 }) &&
            ((double[])BoundaryPoint(world, 0)["direction"]).SequenceEqual(new[] { 1.0, 0, 0 }) &&
            ((double[])BoundaryPoint(local, 0)["direction"]).SequenceEqual(new[] { 0.0, 1, 0 }),
            "Local/WORLD observations remain distinct native sentinels rather than copied coordinates or directions.");
        foreach (int number in new[] { 0, 4, 6, 8 })
            Check(Scenario.Trace.Contains("boundary.3.PPOS." + number + ".wrt:1") &&
                Scenario.Trace.Contains("boundary.3.PPOS." + number + ".wrt:-3"), "Each PPOS uses the explicit requested frame: P" + number);

        foreach (string failure in new[] { "boundary.3.PPOS.6.wrt:1", "boundary.3.PDIR.0.wrt:1", "boundary.3.PPEXST.4" })
        {
            pane = BoundaryFixture(); Scenario.ThrowLabel = failure; geometry = InspectBoundary(pane, true);
            local = BoundaryFrame(geometry); world = BoundaryFrame(geometry, "WORLD");
            Check(Flag(geometry, "complete") && Flag(geometry, "success") && Flag(geometry, "world_positions_complete") &&
                Flag(geometry, "boundary_points_complete", false) && Flag(geometry, "boundary_points_read_complete", false) &&
                Flag(geometry, "boundary_points_source_geometry_complete"), "Boundary failure never overwrites source geometry completion: " + failure);
            Check(!Flag(local, "complete") && Flag(world, "complete") && Flag(BoundaryPoint(local, 8), "complete") &&
                ((Dictionary<string, string>)geometry["boundary_points_errors"]).Count > 0,
                "A single failure preserves later points and the other coordinate frame: " + failure);
            if (failure.Contains("PPEXST"))
                Check(Flag(BoundaryPoint(local, 4), "position_complete") && Flag(BoundaryPoint(local, 4), "complete", false),
                    "PPOS is still observed independently when existence lookup fails.");
        }

        foreach (int failure in new[] { 1, 2, 3 })
        {
            pane = BoundaryFixture(); Scenario.QualifierFailureAt = failure; geometry = InspectBoundary(pane, true);
            local = BoundaryFrame(geometry); world = BoundaryFrame(geometry, "WORLD");
            Check(Flag(geometry, "complete") && Flag(geometry, "boundary_points_complete", false) &&
                !Flag(local, "complete") && Flag(world, "complete") && Flag(BoundaryPoint(local, 8), "complete"),
                "A qualifier failure preserves subsequent points and the other frame: qualifier " + failure);
            if (failure == 3)
                Check(Flag(BoundaryPoint(local, 4), "position_complete"), "An existence-qualifier failure never suppresses independent PPOS.");
        }

        pane = BoundaryFixture(); Scenario.AbsentPoint = 4; geometry = InspectBoundary(pane);
        var p4 = BoundaryPoint(BoundaryFrame(geometry), 4);
        Check(Flag(p4, "exists", false) && Flag(p4, "position_complete") && Flag(p4, "complete", false) &&
            Flag(BoundaryPoint(BoundaryFrame(geometry), 8), "complete") && Flag(geometry, "complete") &&
            Flag(geometry, "boundary_points_complete", false), "PPEXST false and successful PPOS remain separate evidence.");

        pane = BoundaryFixture(false); geometry = InspectBoundary(pane, true);
        Check(Flag(geometry, "boundary_points_complete") && Equals(geometry["boundary_points_curved_vertex_count"], 0) &&
            !Scenario.Trace.Any(IsBoundaryRead) && Scenario.QualifierAdds == 0,
            "All-straight PAVEs retain complete coverage without any derived point queries, even when WORLD is requested.");

        pane = BoundaryFixture(false); Scenario.ThrowLabel = "get.1.THICKN"; geometry = InspectBoundary(pane);
        Check(Flag(geometry, "complete", false) && Flag(geometry, "success", false) &&
            Flag(geometry, "boundary_points_source_geometry_complete", false) && Flag(geometry, "boundary_points_read_complete") &&
            Flag(geometry, "boundary_points_complete", false),
            "Complete returned boundary reads never promote an incomplete source geometry to whole-PANE coverage.");

        pane = BoundaryFixture(); Scenario.ThrowLabel = "get.3.FRAD"; geometry = InspectBoundary(pane);
        var unknown = BoundaryOf(geometry);
        Check(Flag(geometry, "complete", false) && Flag(geometry, "boundary_points_complete", false) &&
            Flag(geometry, "boundary_points_source_geometry_complete", false) && Flag(geometry, "boundary_points_read_complete", false) &&
            unknown["required"] == null && Flag(unknown, "complete", false) && !Scenario.Trace.Any(IsBoundaryRead),
            "Unknown FRAD does not become a straight vertex or successful boundary coverage.");

        pane = BoundaryFixture(); InspectBoundary(pane, true, false);
        string[] sourceTrace = Scenario.Trace.ToArray();
        pane = BoundaryFixture(); InspectBoundary(pane, true);
        string[] boundaryTrace = Scenario.Trace.ToArray();
        Check(boundaryTrace.Take(sourceTrace.Length).SequenceEqual(sourceTrace),
            "Derived boundary SDK calls follow the unchanged source geometry and WORLD inspection trace.");
        int[] boundaryFaults = Enumerable.Range(1, boundaryTrace.Length)
            .Where(index => index > sourceTrace.Length || !IsQualifierEntry(boundaryTrace[index - 1])).ToArray();
        int[] qualifierFaults = boundaryFaults.Where(index => IsQualifierEntry(boundaryTrace[index - 1])).ToArray();
        Check(qualifierFaults.Length == 42 &&
            qualifierFaults.Count(index => boundaryTrace[index - 1] == "qualifier.ctor") == 16 &&
            qualifierFaults.Count(index => boundaryTrace[index - 1].StartsWith("qualifier.wrt.", StringComparison.Ordinal)) == 10 &&
            qualifierFaults.Count(index => boundaryTrace[index - 1].StartsWith("qualifier.add.", StringComparison.Ordinal)) == 16,
            "Both frames expose every constructor, explicit WRT setter and numeric Add as independent SDK entries.");
        foreach (int fault in boundaryFaults)
        {
            pane = BoundaryFixture(); Scenario.FaultAtCall = fault;
            bool caught = false;
            try { InspectBoundary(pane, true); } catch (NativeDatabaseFaultException) { caught = true; }
            Check(caught && Scenario.NativeAfterFault == 0 && Scenario.Deletes == 0 && Scenario.Trace.Count == fault,
                "Production boundary reads stop all SDK access after native fault at " + fault + ".");
        }
        foreach (int fault in qualifierFaults)
        {
            pane = BoundaryFixture(); Scenario.FaultAtCall = fault; Scenario.ThrowAfterFault = true;
            bool caught = false;
            try { InspectBoundary(pane, true); } catch (NativeDatabaseFaultException) { caught = true; }
            Check(caught && Scenario.NativeAfterFault == 0 && Scenario.Deletes == 0 && Scenario.Trace.Count == fault,
                "A qualifier entry that throws after reporting a native fault stops all SDK access: " +
                boundaryTrace[fault - 1] + " at " + fault + ".");
        }
        Console.WriteLine("Panel boundary-point production checks passed: " + checks + "; native injection points: " + boundaryFaults.Length +
            "; qualifier return/throw cases: " + (qualifierFaults.Length * 2));
        return 0;
    }
}

internal static class Scenario
{
    internal static E3d21Context Context;
    internal static DbElement Parent, Original, World;
    internal static readonly Dictionary<string, DbElement> Elements = new Dictionary<string, DbElement>();
    internal static readonly List<DbElement> Created = new List<DbElement>();
    internal static readonly List<string> Trace = new List<string>();
    internal static int Creates, Writes, Deletes, NativeAfterFault, FaultAtCall, BadCreate, UnverifiedWrites, ThrowOccurrence, ThrowsSeen;
    internal static int QualifierAdds, QualifierFailureAt, AbsentPoint;
    internal static string CreateMode, DeleteMode, ThrowLabel;
    internal static bool PreflightFailure, ThrowAfterFault;
    internal static Action<string> OnTouch;
    internal static T Touch<T>(string label, T value)
    {
        if (Context.HasFault) { NativeAfterFault++; throw new Exception("SDK called after fault: " + label); }
        Trace.Add(label);
        if (Trace.Count == FaultAtCall)
        {
            Context.Fault();
            if (ThrowAfterFault) throw new InvalidOperationException("Injected native entry failure after fault: " + label);
        }
        if (OnTouch != null) OnTouch(label);
        if (label == ThrowLabel && ++ThrowsSeen == ThrowOccurrence) throw new InvalidOperationException("Injected ordinary failure: " + label);
        return value;
    }
    internal static void Reset()
    {
        Context = new E3d21Context(); Elements.Clear(); Created.Clear(); Trace.Clear();
        Creates = Writes = Deletes = NativeAfterFault = FaultAtCall = BadCreate = UnverifiedWrites = ThrowsSeen = 0;
        CreateMode = DeleteMode = ThrowLabel = null; ThrowOccurrence = 1; PreflightFailure = ThrowAfterFault = false; OnTouch = null;
        QualifierAdds = QualifierFailureAt = 0; AbsentPoint = -1; World = new DbElement(-3, DbElementType.WORL);
        Parent = new DbElement(-2, DbElementType.EQUI); Original = new DbElement(-1, DbElementType.EQUI) { Parent = Parent };
        Parent.Children.Add(Original); Elements.Add(Parent.Key, Parent); Elements.Add(Original.Key, Original);
    }
    internal static void AddForeign(DbElement parent)
    { var child = new DbElement(900 + Elements.Count, DbElementType.PAVE) { Parent = parent }; parent.Children.Add(child); Elements.Add(child.Key, child); }
}

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        private const int ModelMembershipVerificationLimit = 10000;
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;
        private readonly Action<Action> invokeOnUi = action => action();
        internal int Ends;
        internal bool HasFault { get { return nativeFaultState.HasFault; } }
        internal void Fault() { nativeFaultState.Observe("Unhandled Database error 538 from PanelFake", ""); }
        private bool IsCommandActive() { return false; }
        private bool TryBeginCommand(string command) { return true; }
        private void EndCommand() { Ends++; }
        private static object BusyResult(string command) { throw new Exception("Unexpected busy"); }
        private static string ElementRef(DbElement element) { return ModelReference(NativeRead(() => element.Ref)); }
        private static DbElement ResolveElement(string target)
        {
            DbElement element; Scenario.Elements.TryGetValue(target, out element);
            return NativeRead(() => Scenario.Touch("resolve." + (element == null ? "missing" : element.Id.ToString()), element));
        }
        // Shared transforms are isolated so this suite measures panel creation provenance.
        private static DbElement ModelFrame(string requested, DbElement owner)
        { return requested == "WORLD" ? Scenario.Touch("frame.WORLD", Scenario.World) : owner; }
        private static DbElement ModelStorageFrame(DbElement owner) { return owner; }
        private static ModelEditFrame ModelStorageTransform(DbElement storage, DbElement frame) { return ModelEditFrame.Identity(); }
        private static void ModelRequireStorageFrame(DbElement element, DbElement frame, ModelEditInput input) { }
        private static void ModelRequireTypeAttribute(DbElementType type, DbAttribute attribute) { }
        private static Position ModelPosition(double[] vector) { return new Position { Vector = vector }; }
        private static Orientation ModelOrientation(ModelEditInput input) { return new Orientation(); }
        private static object ModelSubmittedOrientation(ModelEditInput input) { return input.XAxis; }
        private static void ModelAddTransform(Dictionary<string, object> r, DbElement e, DbQualifier q, ModelEditInput i) { r["readback_verified"] = true; }
        private static Dictionary<string, object> ModelIdentity(DbElement e)
        { return new Dictionary<string, object> { ["refno"] = ModelReference(NativeRead(() => e.Ref)) }; }
        private static Dictionary<string, object> ModelFrameIdentity(DbElement e, string requested) { return ModelIdentity(e); }
        private static void PanelAddRevision(DbElement e, Dictionary<string, object> result) { }
        // PRODUCTION_METHODS
    }
}

namespace Aveva.Core.Database
{
    public enum DbType { Design, Catalog }
    public sealed class DbAttribute
    {
        internal readonly string Name;
        internal DbAttribute(string name) { Name = name; }
        public string ShortName { get { return Scenario.Touch("attribute." + Name, Name); } }
    }
    public static class DbAttributeInstance
    {
        public static readonly DbAttribute POS = new DbAttribute("POS"), ORI = new DbAttribute("ORI"), NAME = new DbAttribute("NAME"),
            HEIG = new DbAttribute("HEIG"), SJUS = new DbAttribute("SJUS"), LBLIND = new DbAttribute("LBLIND"), FRAD = new DbAttribute("FRAD"),
            THICKN = new DbAttribute("THICKN"), CTYA = new DbAttribute("CTYA"), GTYP = new DbAttribute("GTYP"), BANG = new DbAttribute("BANG"),
            PPOS = new DbAttribute("PPOS"), PDIR = new DbAttribute("PDIR"), PPEXST = new DbAttribute("PPEXST");
    }
    public sealed class DbQualifier
    {
        private DbElement frame;
        public DbQualifier() { Scenario.Touch("qualifier.ctor", true); }
        public DbElement wrtQualifier
        {
            get { return frame; }
            set { Scenario.Touch("qualifier.wrt." + (value == null ? "null" : value.Id.ToString()), true); frame = value; }
        }
        internal double Number; internal bool HasNumber;
        public void Add(double value)
        {
            Scenario.Touch("qualifier.add." + value.ToString(CultureInfo.InvariantCulture), true);
            Scenario.QualifierAdds++;
            if (Scenario.QualifierAdds == Scenario.QualifierFailureAt) throw new InvalidOperationException("Injected qualifier failure.");
            Number = value; HasNumber = true;
        }
    }
    public static class CurrentElement { public static DbElement Element { get { return Scenario.Touch("ce", Scenario.Parent); } } }
    public sealed class DbElementType
    {
        internal readonly string Value;
        internal DbElementType(string value) { Value = value; }
        public static readonly DbElementType PANE = new DbElementType("PANE"), PLOO = new DbElementType("PLOO"), PAVE = new DbElementType("PAVE"), EQUI = new DbElementType("EQUI"), WORL = new DbElementType("WORL");
        public static DbElementType GetElementType(string name)
        { return Scenario.Touch("type.resolve." + name, new[] { PANE, PLOO, PAVE, EQUI }.Single(t => t.Value == name)); }
        public bool IsValid { get { return Scenario.Touch("type.valid." + Value, true); } }
        public string ShortName { get { return Scenario.Touch("type.name." + Value, Value); } }
        public int[] DatabaseTypes() { return Scenario.Touch("type.databases." + Value, new[] { (int)DbType.Design }); }
        public DbElementType[] MemberTypes() { return Scenario.Touch("type.members." + Value, Value == "PANE" ? new[] { PLOO } : new[] { PAVE }); }
    }
    public sealed class DbElement
    {
        internal readonly int Id;
        internal readonly int[] Reference;
        internal readonly List<DbElement> Children = new List<DbElement>();
        internal readonly Dictionary<string, object> Values = new Dictionary<string, object>();
        internal DbElement Parent;
        internal DbElementType TypeValue;
        internal DbType Database = DbType.Design;
        internal bool Valid = true, Deleted;
        internal DbElement(int id, DbElementType type) { Id = id; TypeValue = type; Reference = new[] { id < 0 ? 2000000001 : 2000000003, id + 500 }; }
        internal string Key { get { return "=" + Reference[0] + "/" + Reference[1]; } }
        public int[] Ref { get { return Scenario.Touch("ref." + Id, (int[])Reference.Clone()); } }
        public bool IsNull { get { return Scenario.Touch("null." + Id, false); } }
        public bool IsValid { get { return Scenario.Touch("valid." + Id, Valid); } }
        public bool IsDeleted { get { return Scenario.Touch("deleted." + Id, Deleted); } }
        public DbType DbType { get { return Scenario.Touch("dbtype." + Id, Database); } }
        public DbElement Owner { get { return Scenario.Touch("owner." + Id, Parent); } }
        public DbElementType GetElementType() { return Scenario.Touch("gettype." + Id, TypeValue); }
        public bool IsCreatable(DbElementType type) { return Scenario.Touch("creatable." + Id, !Scenario.PreflightFailure); }
        public bool IsAttributeValid(DbAttribute a) { return Scenario.Touch("attrvalid." + Id + "." + a.Name, true); }
        public bool IsAttributeSetable(DbAttribute a) { return Scenario.Touch("settable." + Id + "." + a.Name, true); }
        public DbElement FirstMember() { return Scenario.Touch("first." + Id, Children.FirstOrDefault()); }
        public DbElement Next()
        { int i = Parent.Children.IndexOf(this); return Scenario.Touch("next." + Id, i + 1 < Parent.Children.Count ? Parent.Children[i + 1] : null); }
        public static DbElement GetElement(int[] reference)
        {
            DbElement element; Scenario.Elements.TryGetValue("=" + reference[0] + "/" + reference[1], out element);
            DbElement returned = element;
            if (element != null && Scenario.Creates == Scenario.BadCreate && element.Id == Scenario.BadCreate && Scenario.CreateMode == "resolve-alias") returned = Scenario.Original;
            if (Scenario.Deletes > 0 && element != null && element.Id == 5)
            {
                if (Scenario.DeleteMode == "lookup-alias") returned = Scenario.Original;
                if (Scenario.DeleteMode == "lookup-null") returned = null;
            }
            return Scenario.Touch("lookup." + (element == null ? "missing" : element.Id.ToString()), returned);
        }
        public DbElement CreateLast(DbElementType type)
        {
            int id = ++Scenario.Creates; Scenario.Touch("create." + id, true);
            string mode = id == Scenario.BadCreate ? Scenario.CreateMode : null;
            if (mode == "throw-before") throw new InvalidOperationException("Create failed before return");
            if (mode == "null-before") return null;
            if (mode == "existing") return Scenario.Original;
            if (mode == "registered") return Scenario.Created[0];
            var created = new DbElement(id, type) { Parent = this };
            Scenario.Created.Add(created); Scenario.Elements.Add(created.Key, created);
            if (mode == "prepend") Children.Insert(0, created); else Children.Add(created);
            if (mode == "throw-after") throw new InvalidOperationException("Created but no returned reference");
            if (mode == "null-after") return null;
            if (mode == "wrong-type") created.TypeValue = DbElementType.EQUI;
            if (mode == "wrong-owner") created.Parent = Scenario.Original;
            if (mode == "wrong-db") created.Database = DbType.Catalog;
            if (mode == "nonempty") Scenario.AddForeign(created);
            if (mode == "extra-sibling") Scenario.AddForeign(this);
            return created;
        }
        public bool SetAttribute<T>(DbAttribute attribute, DbQualifier qualifier, T value, out PdmsMessage message)
        {
            Scenario.Writes++;
            if (Scenario.BadCreate > 0 && Scenario.Creates >= Scenario.BadCreate) Scenario.UnverifiedWrites++;
            message = null; Values[attribute.Name] = value;
            return Scenario.Touch("set." + Id + "." + attribute.Name, true);
        }
        private object Value(DbAttribute a)
        {
            if (TypeValue == DbElementType.PANE && (a == DbAttributeInstance.THICKN || a == DbAttributeInstance.SJUS))
                return Children[0].Values[a == DbAttributeInstance.THICKN ? "HEIG" : "SJUS"];
            object value; return Values.TryGetValue(a.Name, out value) ? value : (object)"unset";
        }
        public string GetString(DbAttribute a) { return Scenario.Touch("get." + Id + "." + a.Name, (string)Value(a)); }
        public string GetAsString(DbAttribute a) { return GetString(a); }
        public double GetDouble(DbAttribute a) { return Scenario.Touch("get." + Id + "." + a.Name, (double)Value(a)); }
        public bool GetBool(DbAttribute a) { return Scenario.Touch("get." + Id + "." + a.Name, (bool)Value(a)); }
        private int PointNumber(DbQualifier q)
        {
            if (q == null || !q.HasNumber || q.Number != Math.Truncate(q.Number)) throw new Exception("Expected an explicit numeric P-point qualifier.");
            return (int)q.Number;
        }
        public bool GetBool(DbAttribute a, DbQualifier q)
        {
            int number = PointNumber(q);
            if (a != DbAttributeInstance.PPEXST) throw new Exception("Unexpected qualified boolean attribute.");
            if (number == 0) throw new Exception("PPEXST(0) is deliberately unsupported by this native simulator.");
            return Scenario.Touch("boundary." + Id + ".PPEXST." + number, number != Scenario.AbsentPoint);
        }
        public Position GetPosition(DbAttribute a, DbQualifier q)
        {
            if (a != DbAttributeInstance.PPOS) return Scenario.Touch("get." + Id + "." + a.Name, (Position)Value(a));
            int number = PointNumber(q);
            if (q.wrtQualifier == null) throw new Exception("PPOS requires explicit WRT.");
            bool world = q.wrtQualifier == Scenario.World;
            if (!world && q.wrtQualifier.TypeValue != DbElementType.PANE) throw new Exception("PPOS requires PANE or WORLD WRT.");
            var point = new Position { Vector = new[] { Id * 10.0 + number + (world ? 1000 : 0), number * 2.0 + (world ? 2000 : 0), number * 3.0 + (world ? 3000 : 0) } };
            return Scenario.Touch("boundary." + Id + ".PPOS." + number + ".wrt:" + q.wrtQualifier.Id, point);
        }
        public Direction GetDirection(DbAttribute a, DbQualifier q)
        {
            int number = PointNumber(q);
            if (a != DbAttributeInstance.PDIR || number != 0 || q.wrtQualifier == null) throw new Exception("Expected PDIR(0) with explicit WRT.");
            bool world = q.wrtQualifier == Scenario.World;
            if (!world && q.wrtQualifier.TypeValue != DbElementType.PANE) throw new Exception("PDIR requires PANE or WORLD WRT.");
            return Scenario.Touch("boundary." + Id + ".PDIR." + number + ".wrt:" + q.wrtQualifier.Id,
                new Direction { Vector = world ? new[] { 1.0, 0, 0 } : new[] { 0.0, 1, 0 } });
        }
        public bool Delete(out PdmsMessage message)
        {
            Scenario.Deletes++; message = null; Scenario.Touch("delete." + Id, true);
            Cascade(this); Parent.Children.Remove(this); return true;
        }
        private static void Cascade(DbElement element)
        { if (!(Scenario.DeleteMode == "survives" && element.Id == 5)) { element.Valid = false; element.Deleted = true; } foreach (DbElement child in element.Children) Cascade(child); }
    }
}

namespace Aveva.Core.Geometry
{
    public sealed class Position
    {
        internal double[] Vector;
        public double X { get { return Scenario.Touch("position.x", Vector[0]); } }
        public double Y { get { return Scenario.Touch("position.y", Vector[1]); } }
        public double Z { get { return Scenario.Touch("position.z", Vector[2]); } }
    }
    public sealed class Orientation { }
    public sealed class Direction
    {
        internal double[] Vector;
        public double East { get { return Scenario.Touch("direction.east", Vector[0]); } }
        public double North { get { return Scenario.Touch("direction.north", Vector[1]); } }
        public double Up { get { return Scenario.Touch("direction.up", Vector[2]); } }
    }
}
namespace Aveva.Core.Utilities.Messaging
{
    public sealed class PdmsMessage { public string MessageText() { return Scenario.Touch("message", "native diagnostic"); } }
}
