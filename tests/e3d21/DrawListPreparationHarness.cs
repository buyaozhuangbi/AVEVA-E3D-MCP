using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

// Managed recording fakes exercise the extracted production setter, preparation
// validator, strict primitive restoration, rollback and residual reporting.
// Their refresh/materialization behavior does not claim to reproduce the SDK.
internal static class DrawListPreparationHarness
{
    private const int DisplayStateMemberLimit = 10000, DisplayStatePrimitiveLimit = 20000;
    private const string Target = "=1/2", OldTarget = "=1/3", Pending = "=1/4", Late = "=1/5";
    private const string Outside = "=1/10", OldOutside = "=1/11", OutsidePending = "=1/12";
    private static readonly Dictionary<string, DbElement> database = new Dictionary<string, DbElement>();
    private static readonly List<string> events = new List<string>();
    private static DrawList active;
    private static int checks, refreshes, setters, saves, adds, mapReadsAfterRefresh, nativeCalls, nativeAfterFault;
    private static bool fault;
    private static Action<int> onRefresh;
    private static Action onMapRead;
    private static Action<string> onMemberSetter;
    private static string nextMapError;

    private sealed class UnsupportedTransparencyStateException : InvalidOperationException
    { internal UnsupportedTransparencyStateException(string message) : base(message) { } }
    private sealed class NativeFaultException : InvalidOperationException { }
    private sealed class DrawListVisualEntry
    {
        internal string refno;
        internal int colour, transparency;
        internal bool edges, visibility = true, shallow;
    }
    private sealed class DrawListPrimitiveVisualEntry
    {
        internal string owner_refno, refno;
        internal int colour, transparency;
        internal bool edges, visibility = true, shallow;
    }
    private sealed class PrimitiveVisualMap
    {
        internal readonly Dictionary<string, DrawListVisualEntry> Members = new Dictionary<string, DrawListVisualEntry>();
        internal readonly Dictionary<string, DrawListPrimitiveVisualEntry> Entries = new Dictionary<string, DrawListPrimitiveVisualEntry>();
        internal readonly Dictionary<string, DrawListPrimitive> Wrappers = new Dictionary<string, DrawListPrimitive>();
        internal readonly List<string> Errors = new List<string>();
    }
    private sealed class DbElement
    {
        internal string Reference;
        internal DbElement Owner;
        internal bool TopLevel;
        internal bool IsNull { get { return false; } }
        internal bool IsValid { get { return true; } }
        internal int[] Ref { get { return Reference.Substring(1).Split('/').Select(int.Parse).ToArray(); } }
        internal FakeElementType GetElementType() { return new FakeElementType { IsWorld = Owner == null }; }
    }
    private sealed class FakeElementType { internal bool IsWorld; }
    private sealed class DrawListPrimitive
    {
        internal DbElement DbElement;
        internal DrawListPrimitiveVisualEntry State;
        internal string ResolvedOwner;
        private void Record(string field) { Touch("primitive-set:" + DbElement.Reference + ":" + field); setters++; }
        internal int Colour { get { return State.colour; } set { Record("colour"); State.colour = value; } }
        internal int Transparency { get { return State.transparency; } set { Record("transparency"); State.transparency = Math.Max(1, value); } }
        internal bool Edges { get { return State.edges; } set { Record("edges"); State.edges = value; } }
        internal bool Visibility { get { return State.visibility; } set { Record("visibility"); State.visibility = value; } }
        internal bool ShallowRendering { get { return State.shallow; } set { Record("shallow"); State.shallow = value; } }
    }
    private sealed class DrawListMember
    {
        internal DbElement DbElement;
        internal DrawListVisualEntry State;
        internal DrawList List;
        private void Write(string field, Action member, Action<DrawListPrimitiveVisualEntry> primitive)
        {
            Touch("member-set:" + DbElement.Reference + ":" + field); setters++;
            member();
            foreach (var entry in List.Primitives.Values.Where(p => IsInSubtree(p.DbElement, DbElement.Reference))) primitive(entry.State);
            if (onMemberSetter != null) onMemberSetter(field);
        }
        internal int Colour { get { return State.colour; } set { Write("colour", () => State.colour = value, p => p.colour = value); } }
        internal int Transparency { get { return State.transparency; } set { Write("transparency", () => State.transparency = value, p => p.transparency = value); } }
        internal bool Edges { get { return State.edges; } set { Write("edges", () => State.edges = value, p => p.edges = value); } }
        internal bool Visibility { get { return State.visibility; } set { Write("visibility", () => State.visibility = value, p => p.visibility = value); } }
        internal bool ShallowRendering { get { return State.shallow; } set { Write("shallow", () => State.shallow = value, p => p.shallow = value); } }
        internal void SetVisualProperties(bool visibility, int colour, int transparency, bool edges)
        {
            Write("aggregate", () => { State.visibility = visibility; State.colour = colour; State.transparency = transparency; State.edges = edges; },
                p => { p.visibility = visibility; p.colour = colour; p.transparency = transparency; p.edges = edges; });
        }
    }
    private sealed class DrawList
    {
        internal readonly Dictionary<string, DrawListMember> Items = new Dictionary<string, DrawListMember>();
        internal readonly Dictionary<string, DrawListPrimitive> Primitives = new Dictionary<string, DrawListPrimitive>();
        internal readonly List<Tuple<string, string>> Aliases = new List<Tuple<string, string>>();
        internal DrawListMember[] Members() { Touch("members"); return Items.Values.ToArray(); }
        internal void Add(DbElement element) { Touch("add:" + element.Reference); adds++; AddMember(element); }
        internal DrawListMember GetDrawListMember(DbElement element)
        { return Primitives.ContainsKey(element.Reference) ? Items[Primitives[element.Reference].ResolvedOwner] : null; }
    }
    private sealed class DrawListManager
    {
        internal static readonly DrawListManager Instance = new DrawListManager();
        internal void UpdateGraphics()
        {
            Touch("refresh:" + (++refreshes)); mapReadsAfterRefresh = 0;
            if (onRefresh != null) onRefresh(refreshes);
        }
    }
    private static void Touch(string label)
    {
        if (fault) { nativeAfterFault++; throw new Exception("SDK continued after native fault: " + label); }
        nativeCalls++; events.Add(label);
    }
    private static void ThrowIfNativeFaulted() { if (fault) throw new NativeFaultException(); }
    private static T NativeRead<T>(Func<T> action) { ThrowIfNativeFaulted(); T value = action(); ThrowIfNativeFaulted(); return value; }
    private static void NativeWrite(Action action) { ThrowIfNativeFaulted(); action(); ThrowIfNativeFaulted(); }
    private static object OnUi(Func<object> action) { ThrowIfNativeFaulted(); return action(); }
    private static DrawList CurrentDrawList() { return active; }
    private static int? ResolveColourIndex(string colour) { return string.IsNullOrWhiteSpace(colour) ? (int?)null : colour == "CYAN" ? 6 : throw new ArgumentException("unknown fake colour"); }
    private static DbElement ResolveElement(string reference) { DbElement element; return database.TryGetValue(reference, out element) ? element : null; }
    private static DbElement ModelRequireElement(string reference) { return database[reference]; }
    private static bool IsUsable(DbElement element) { return element != null; }
    private static string ElementRef(DbElement element) { return element.Reference; }
    private static string ElementType(DbElement element) { return element.TopLevel ? "EQUI" : "CYLI"; }
    private static bool DisplayStateTopLevelElement(DbElement element) { return element.TopLevel; }
    private static string ModelReference(int[] reference) { return "=" + reference[0] + "/" + reference[1]; }
    private static bool IsInSubtree(DbElement element, string reference)
    {
        for (DbElement current = element; current != null; current = current.Owner) if (current.Reference == reference) return true;
        return false;
    }
    private static void DisplayStateRequireDatabaseSubtree(DbElement element, string reference)
    { if (!IsInSubtree(element, reference)) throw new InvalidOperationException("outside target subtree: " + element.Reference); }
    private static Dictionary<string, object> DrawListPersistenceResult(string status, string error = null)
    { return new Dictionary<string, object> { ["attempted"] = false, ["status"] = status, ["error"] = error }; }
    private static object SaveDrawListState(DrawList list)
    { Touch("persist"); saves++; return new Dictionary<string, object> { ["attempted"] = true, ["succeeded"] = true }; }

    // The map adapter represents bounded SDK observations and checks owner/key
    // consistency. Production comparison, preparation and restoration consume it.
    private static PrimitiveVisualMap PrimitiveVisualReadMap(DrawList list, bool memberValues = true, bool consistentAliases = true)
    {
        ThrowIfNativeFaulted(); Touch("map");
        if (refreshes > 0) mapReadsAfterRefresh++;
        if (onMapRead != null) onMapRead();
        var result = new PrimitiveVisualMap();
        if (memberValues) foreach (var member in list.Items) result.Members.Add(member.Key, CloneDrawListVisual(member.Value.State));
        var seen = new Dictionary<string, DrawListPrimitiveVisualEntry>();
        foreach (var alias in list.Aliases)
        {
            var primitive = list.Primitives[alias.Item2];
            var entry = PrimitiveVisualClone(primitive.State); entry.owner_refno = alias.Item1;
            if (!list.Items.ContainsKey(alias.Item1) || !IsInSubtree(primitive.DbElement, alias.Item1)) result.Errors.Add("invalid owner");
            string key = PrimitiveVisualKey(entry);
            if (result.Entries.ContainsKey(key)) { result.Errors.Add("duplicate primitive key"); continue; }
            result.Entries.Add(key, entry); result.Wrappers.Add(key, primitive);
            if (consistentAliases && seen.ContainsKey(entry.refno) && !PrimitiveVisualValuesEqual(seen[entry.refno], entry)) result.Errors.Add("alias mismatch");
            seen[entry.refno] = entry;
        }
        if (nextMapError != null) { result.Errors.Add(nextMapError); nextMapError = null; }
        return result;
    }
    private static DbElement Element(int id, DbElement owner, bool top = false)
    {
        var element = new DbElement { Reference = "=1/" + id, Owner = owner, TopLevel = top };
        database.Add(element.Reference, element); return element;
    }
    private static void AddMember(DbElement element)
    {
        active.Items[element.Reference] = new DrawListMember { DbElement = element, List = active,
            State = new DrawListVisualEntry { refno = element.Reference, colour = 326, shallow = true } };
    }
    private static void AddPrimitive(string reference, string owner, int colour = 0)
    {
        active.Primitives.Add(reference, new DrawListPrimitive { DbElement = database[reference], ResolvedOwner = owner,
            State = new DrawListPrimitiveVisualEntry { owner_refno = owner, refno = reference, colour = colour } });
        active.Aliases.Add(Tuple.Create(owner, reference));
    }
    private static void RemovePrimitive(string reference)
    { active.Primitives.Remove(reference); active.Aliases.RemoveAll(alias => alias.Item2 == reference); }
    private static void Fixture(bool pending = true)
    {
        database.Clear(); events.Clear(); refreshes = setters = saves = adds = mapReadsAfterRefresh = nativeCalls = nativeAfterFault = 0;
        fault = false; onMapRead = null; onMemberSetter = null; nextMapError = null; active = new DrawList();
        var world = Element(0, null); var ancestor = Element(1, world, true); var target = Element(2, ancestor, true);
        var outside = Element(10, world, true);
        foreach (var element in new[] { ancestor, target, outside }) AddMember(element);
        Element(3, target); Element(4, target); Element(5, target); Element(11, outside); Element(12, outside);
        AddPrimitive(OldTarget, Target, 12); AddPrimitive(OldOutside, Outside, 17);
        active.Primitives[OldOutside].State.edges = true; active.Primitives[OldOutside].State.shallow = true;
        onRefresh = count => { if (count == 1 && pending) AddPrimitive(Pending, Target); };
    }
    private static Dictionary<string, object> Run(bool dryRun = false, bool change = true)
    { return (Dictionary<string, object>)SetDrawListVisual(Target, change ? "CYAN" : null, null, change ? (bool?)true : null, true, null, true, dryRun); }
    private static void Check(bool condition, string label)
    { if (!condition) throw new Exception(label + "\nEvents: " + string.Join(", ", events)); checks++; }
    private static bool Flag(Dictionary<string, object> result, string field, bool expected)
    { object value; return result.TryGetValue(field, out value) && Equals(value, expected); }
    private static Dictionary<string, string> Differences(Dictionary<string, object> result, string field)
    { return (Dictionary<string, string>)result[field]; }
    private static void PreparationRejected(string label, Action<int> behavior, bool completeResidual = true)
    {
        Fixture(); onRefresh = behavior;
        var result = Run();
        Check(Flag(result, "success", false) && Flag(result, "preparation_refresh_attempted", true), label + ": failure receipt");
        Check(setters == 0 && adds == 0 && refreshes == 1 && saves == 0 && Flag(result, "write_attempted", false)
            && Flag(result, "rollback_attempted", false), label + ": preparation failure never writes, compensates, refreshes twice or persists");
        Check(Flag(result, "residual_complete", completeResidual) && Flag(result, "outcome_uncertain", !completeResidual),
            label + ": residual completeness controls uncertainty");
    }
    private static void CheckNativeFault(bool duringSetter)
    {
        Fixture();
        if (duringSetter) onMemberSetter = field => { fault = true; events.Add("fault"); };
        else onRefresh = count => { AddPrimitive(Pending, Target); fault = true; events.Add("fault"); };
        bool stopped = false;
        try { Run(); } catch (NativeFaultException) { stopped = true; }
        Check(stopped && nativeAfterFault == 0 && events.Last() == "fault" && saves == 0,
            (duringSetter ? "setter" : "preparation") + " fault stops all later SDK, rollback, residual and persistence calls");
        Check(refreshes == 1 && setters == (duringSetter ? 1 : 0), "fault does not replay the request or perform a second refresh");
    }
    private static int Main()
    {
        try
        {
            Fixture(); var old = PrimitiveVisualReadMap(active); events.Clear();
            var result = Run();
            Check(Flag(result, "success", true) && Flag(result, "preparation_verified", true)
                && Flag(result, "preparation_refresh_completed", true), "delayed cylinder materializes before successful member update");
            Check(((string[])result["materialized_primitive_keys"]).SequenceEqual(new[] { Target + "|" + Pending })
                && (int)result["materialized_primitive_count"] == 1, "exact accepted materialization keys are returned");
            Check(events.IndexOf("refresh:1") < events.FindIndex(item => item.StartsWith("member-set:", StringComparison.Ordinal))
                && adds == 0 && saves == 1 && refreshes == 3, "one preparation precedes setters and normal strict readback; no Add or duplicate persistence");
            Check(active.Items[Target].State.colour == 6 && active.Items[Target].State.edges,
                "requested colour and edges read back on exact target member");
            var finalMap = PrimitiveVisualReadMap(active);
            Check(old.Entries.All(entry => PrimitiveVisualValuesEqual(entry.Value, finalMap.Entries[entry.Key]))
                && old.Members.Where(entry => entry.Key != Target).All(entry =>
                    CompareDrawListVisualSnapshots(new Dictionary<string, DrawListVisualEntry> { [entry.Key] = entry.Value },
                        new Dictionary<string, DrawListVisualEntry> { [entry.Key] = finalMap.Members[entry.Key] }).Count == 0),
                "all previously observed primitives and unrelated member values survive propagating setters");
            Check(finalMap.Entries[Target + "|" + Pending].colour == 0 && !finalMap.Entries[Target + "|" + Pending].edges,
                "new primitive's prepared values remain the exact expected baseline");

            Fixture(); result = Run(true);
            Check(Flag(result, "success", true) && Flag(result, "dry_run", true) && refreshes == 0 && setters == 0 && saves == 0,
                "preview never materializes, sets, or persists");
            Fixture(); result = Run(false, false);
            Check(Flag(result, "success", true) && Flag(result, "no_change", true) && refreshes == 0 && setters == 0 && saves == 1
                && !active.Primitives.ContainsKey(Pending), "exact no-op retains unmaterialized state and only saves its observed map");

            Fixture(); onRefresh = count => { if (count == 1) { AddPrimitive(Pending, Target); active.Aliases.Add(Tuple.Create("=1/1", Pending)); } };
            result = Run();
            Check(Flag(result, "success", true) && (int)result["materialized_primitive_count"] == 2,
                "verified target primitive aliases under a pre-existing ancestor may materialize together");

            PreparationRejected("unrelated new primitive", count => { AddPrimitive(Pending, Target); AddPrimitive(OutsidePending, Outside); });
            Check(active.Items[Target].State.colour == 326, "rejected unrelated materialization leaves requested target colour unapplied");
            PreparationRejected("existing primitive removal", count => RemovePrimitive(OldOutside));
            PreparationRejected("existing member removal", count => { active.Items.Remove(Outside); RemovePrimitive(OldOutside); });
            PreparationRejected("unexpected member addition", count => AddMember(Element(20, database["=1/0"], true)));
            PreparationRejected("refresh throws after materialization", count => { AddPrimitive(Pending, Target); throw new InvalidOperationException("refresh failed"); });
            PreparationRejected("invalid new owner", count => { AddPrimitive(Pending, Outside); }, false);
            foreach (string field in new[] { "colour", "transparency", "edges", "visibility", "shallow" })
            {
                PreparationRejected("existing primitive " + field + " changed", count => ChangePrimitive(active.Primitives[OldOutside].State, field));
                PreparationRejected("existing member " + field + " changed", count => ChangeMember(active.Items[Outside].State, field));
            }

            Fixture(); onRefresh = count => { if (count == 1) { AddPrimitive(Pending, Target); nextMapError = "incomplete prepared collection"; } };
            result = Run();
            Check(Flag(result, "success", false) && setters == 0 && refreshes == 1 && saves == 0
                && Flag(result, "preparation_verified", false) && Flag(result, "residual_complete", true),
                "incomplete preparation rejects before setters even if later residual reads complete");

            Fixture(); onMapRead = () => { if (refreshes == 1 && mapReadsAfterRefresh == 2) AddPrimitive(Late, Target); };
            result = Run();
            Check(Flag(result, "success", false) && Flag(result, "preparation_verified", true) && setters == 0 && refreshes == 1
                && Flag(result, "rollback_attempted", false) && saves == 0,
                "extra target entry between accepted preparation and first setter still rejects without compensation");

            Fixture(); onRefresh = count => { if (count == 1) AddPrimitive(Pending, Target); if (count == 2) AddPrimitive(Late, Target); };
            result = Run();
            Check(Flag(result, "success", false) && Flag(result, "preparation_verified", true) && Flag(result, "rollback_attempted", true)
                && result["error"].ToString().Contains("membership differs") && saves == 0,
                "second refresh cannot silently adopt another target primitive into the strict expected set");
            Check(Flag(result, "rollback_verified_original", false) && Flag(result, "rollback_succeeded", false)
                && Flag(result, "residual_complete", true) && Flag(result, "outcome_uncertain", false)
                && Differences(result, "residual_primitive_differences").ContainsKey(Target + "|" + Pending)
                && Differences(result, "residual_primitive_differences").ContainsKey(Target + "|" + Late),
                "failure diagnostics retain the actual entry-time baseline and disclose preparation plus later extras");

            CheckNativeFault(false); CheckNativeFault(true);
            Console.WriteLine("Drawlist preparation production checks passed: " + checks); return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
    private static void ChangePrimitive(DrawListPrimitiveVisualEntry entry, string field)
    {
        if (field == "colour") entry.colour++;
        else if (field == "transparency") entry.transparency++;
        else if (field == "edges") entry.edges = !entry.edges;
        else if (field == "visibility") entry.visibility = !entry.visibility;
        else entry.shallow = !entry.shallow;
    }
    private static void ChangeMember(DrawListVisualEntry entry, string field)
    {
        if (field == "colour") entry.colour++;
        else if (field == "transparency") entry.transparency++;
        else if (field == "edges") entry.edges = !entry.edges;
        else if (field == "visibility") entry.visibility = !entry.visibility;
        else entry.shallow = !entry.shallow;
    }

    // PRODUCTION_METHODS
}
