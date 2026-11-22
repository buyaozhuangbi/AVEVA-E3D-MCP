using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Web.Script.Serialization;
using E3d21AiBridge;

internal static class PrimitiveVisualRestoreHarness
{
    private const int DisplayStatePrimitiveLimit = 20000;
    private static int checks, writes, zeroPrimitiveWrites, nativeCalls, faultAt, nativeAfterFault, faultCases, scenarios;
    private static bool faultThrows;
    private static NativeDatabaseFaultState currentNativeFaultState;
    private static long permittedNativeFaultGeneration;
    private static readonly List<string> nativeTrace = new List<string>();
    private static readonly List<string> writeTrace = new List<string>();
    private static readonly List<object> observations = new List<object>();
    private static readonly List<object> scenarioResults = new List<object>();
    private static string activeScenario;
    private static long managedEntryReferenceReads;
    private static readonly Dictionary<string, DbElement> database = new Dictionary<string, DbElement>();
    private sealed class UnsupportedTransparencyStateException : InvalidOperationException
    { internal UnsupportedTransparencyStateException(string message) : base(message) { } }
    private sealed class DrawListVisualEntry
    {
        internal string refno;
        internal int colour, transparency;
        internal bool edges, visibility = true, shallow;
    }
    private sealed class DrawListPrimitiveVisualEntry
    {
        internal string owner_refno;
        private string reference;
        internal string refno { get { managedEntryReferenceReads++; return reference; } set { reference = value; } }
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
        internal int[] Ref { get { NativeCall("reference:" + Reference); return Reference.Substring(1).Split('/').Select(int.Parse).ToArray(); } }
        internal ElementType GetElementType() { return new ElementType { IsWorld = Owner == null }; }
    }
    private sealed class ElementType { internal bool IsWorld; }
    private sealed class DrawListPrimitive
    {
        internal DbElement Element;
        internal string WrapperId;
        private string TraceReference { get { return Element.Reference + (WrapperId == null ? "" : "@" + WrapperId); } }
        internal DbElement DbElement { get { NativeCall("identity:" + TraceReference); return Element; } set { Element = value; } }
        internal DrawListPrimitiveVisualEntry State;
        internal string ResolvedOwner;
        internal DrawList List;
        internal bool HasOverride = true, MaterializedShallow;
        internal int Materializations;
        internal string ThrowAfterField;
        internal Action<DrawListPrimitive, string> AfterSetter;
        private DrawListPrimitiveVisualEntry Observed(string field)
        {
            NativeCall("get:" + TraceReference + ":" + field);
            if (HasOverride) return State;
            var member = List.Members[ResolvedOwner].State;
            return new DrawListPrimitiveVisualEntry { owner_refno = ResolvedOwner, refno = Element.Reference,
                colour = member.colour, transparency = member.transparency, edges = member.edges,
                visibility = member.visibility, shallow = member.shallow };
        }
        private void Write(string field, Action assign)
        {
            // The public wrapper already exists. Any first setter creates the
            // native override; subsequent setters modify only their own slot.
            // Shallow's creation value is adversarial fixture data, not an SDK
            // promise that this field is always false (or that visibility clears it).
            writes++; writeTrace.Add(TraceReference + ":" + field);
            if (!HasOverride)
            {
                HasOverride = true; Materializations++;
                State.colour = State.transparency = 0; State.edges = false;
                State.visibility = true; State.shallow = MaterializedShallow;
            }
            assign();
            if (AfterSetter != null) AfterSetter(this, field);
            NativeCall("set:" + TraceReference + ":" + field);
            if (ThrowAfterField == field) throw new InvalidOperationException("setter mutated then threw: " + field);
        }
        internal int Colour { get { return Observed("colour").colour; } set { Write("colour", () => State.colour = value); } }
        internal int Transparency
        {
            get { return Observed("transparency").transparency; }
            set { Write("transparency", () => { if (value == 0) zeroPrimitiveWrites++; State.transparency = Math.Max(1, value); }); }
        }
        internal bool Edges { get { return Observed("edges").edges; } set { Write("edges", () => State.edges = value); } }
        internal bool Visibility { get { return Observed("visibility").visibility; } set { Write("visibility", () => State.visibility = value); } }
        internal bool ShallowRendering { get { return Observed("shallow").shallow; } set { Write("shallow", () => State.shallow = value); } }
    }
    private sealed class DrawListMember
    {
        internal DbElement DbElement;
        internal DrawListVisualEntry State;
        internal DrawList List;
        internal bool ThrowAfterAggregate;
        internal void SetVisualProperties(bool visibility, int colour, int transparency, bool edges)
        {
            writes++;
            writeTrace.Add("aggregate:" + DbElement.Reference);
            State.visibility = visibility; State.colour = colour; State.transparency = transparency; State.edges = edges;
            foreach (var primitive in List.Primitives.Values.Where(p => p.ResolvedOwner == DbElement.Reference))
            {
                if (!primitive.HasOverride) continue;
                primitive.State.visibility = visibility; primitive.State.colour = colour;
                primitive.State.transparency = transparency; primitive.State.edges = edges;
            }
            NativeCall("aggregate:" + DbElement.Reference);
            if (ThrowAfterAggregate) throw new InvalidOperationException("aggregate mutated then threw");
        }
    }
    private sealed class DrawList
    {
        internal readonly Dictionary<string, DrawListMember> Members = new Dictionary<string, DrawListMember>();
        internal readonly Dictionary<string, DrawListPrimitive> Primitives = new Dictionary<string, DrawListPrimitive>();
        internal readonly Dictionary<string, DrawListPrimitive> AliasWrappers = new Dictionary<string, DrawListPrimitive>(StringComparer.Ordinal);
        internal readonly List<Tuple<string, string>> Aliases = new List<Tuple<string, string>>();
        internal DrawListMember GetDrawListMember(DbElement element)
        { return Primitives.ContainsKey(element.Reference) ? Members[Primitives[element.Reference].ResolvedOwner] : null; }
    }
    private static void NativeCall(string name)
    {
        if (currentNativeFaultState.HasFault) nativeAfterFault++;
        nativeCalls++; nativeTrace.Add(name);
        if (nativeCalls != faultAt) return;
        currentNativeFaultState.Observe("Unhandled Database error 789 from VISUAL_RESTORE_TEST", "offline");
        if (faultThrows) throw new InvalidOperationException("native call also threw");
    }
    private static void ResetTrace()
    {
        currentNativeFaultState = new NativeDatabaseFaultState(); permittedNativeFaultGeneration = 0;
        nativeCalls = nativeAfterFault = faultAt = writes = zeroPrimitiveWrites = 0;
        nativeTrace.Clear(); writeTrace.Clear(); faultThrows = false;
    }
    private static bool IsUsable(DbElement element) { return element != null; }
    private static string ElementRef(DbElement element) { return element.Reference; }
    private static string ModelReference(int[] reference) { return "=" + reference[0] + "/" + reference[1]; }
    private static DbElement ModelRequireElement(string reference) { return database[reference]; }
    private static DrawListVisualEntry CloneDrawListVisual(DrawListVisualEntry entry)
    { return new DrawListVisualEntry { refno = entry.refno, colour = entry.colour, transparency = entry.transparency, edges = entry.edges, visibility = entry.visibility, shallow = entry.shallow }; }
    private static void DisplayStateRequireDatabaseSubtree(DbElement element, string owner)
    {
        for (DbElement current = element; current != null; current = current.Owner)
            if (current.Reference == owner) return;
        throw new InvalidOperationException("not in database subtree");
    }
    private static DrawListMember RequireExactDrawListMember(DrawList list, DbElement element) { return list.Members[element.Reference]; }
    private static PrimitiveVisualMap PrimitiveVisualReadMap(DrawList list, bool memberValues = true, bool consistentAliases = true)
    {
        var map = new PrimitiveVisualMap();
        ThrowIfNativeFaulted();
        if (memberValues) foreach (var member in list.Members)
            map.Members.Add(member.Key, NativeRead(() => { NativeCall("member:" + member.Key); return CloneDrawListVisual(member.Value.State); }));
        foreach (var alias in list.Aliases)
        {
            DrawListPrimitive primitive;
            if (!list.AliasWrappers.TryGetValue(alias.Item1 + "|" + alias.Item2, out primitive)) primitive = list.Primitives[alias.Item2];
            var entry = new DrawListPrimitiveVisualEntry { owner_refno = alias.Item1, refno = alias.Item2,
                colour = NativeRead(() => primitive.Colour), transparency = NativeRead(() => primitive.Transparency),
                edges = NativeRead(() => primitive.Edges), visibility = NativeRead(() => primitive.Visibility),
                shallow = NativeRead(() => primitive.ShallowRendering) };
            map.Entries.Add(PrimitiveVisualKey(entry), entry);
            map.Wrappers.Add(PrimitiveVisualKey(entry), primitive);
        }
        return map;
    }
    private static Dictionary<string, DrawListPrimitiveVisualEntry> CaptureDrawListPrimitiveSnapshot(DrawList list)
    { return PrimitiveVisualReadMap(list).Entries; }
    private static Dictionary<string, DrawListVisualEntry> CaptureDrawListVisualSnapshot(DrawList list)
    { return PrimitiveVisualReadMap(list).Members; }
    private static Dictionary<string, string> CompareDrawListVisualSnapshots(IDictionary<string, DrawListVisualEntry> wanted, IDictionary<string, DrawListVisualEntry> actual)
    {
        var diff = new Dictionary<string, string>();
        foreach (var member in wanted)
        {
            DrawListVisualEntry got;
            if (!actual.TryGetValue(member.Key, out got) || got.colour != member.Value.colour || got.transparency != member.Value.transparency ||
                got.visibility != member.Value.visibility || got.edges != member.Value.edges || got.shallow != member.Value.shallow) diff[member.Key] = "member differs";
        }
        foreach (string key in actual.Keys) if (!wanted.ContainsKey(key)) diff[key] = "extra member";
        return diff;
    }
    // Record only managed fixture fields. Evidence capture must never invoke a
    // native getter, especially after the real production fault latch has fired.
    private static object Values(DrawListPrimitiveVisualEntry entry)
    { return new object[] { entry.owner_refno, entry.refno, entry.colour, entry.transparency, entry.edges, entry.visibility, entry.shallow }; }
    private static object Values(DrawListVisualEntry entry)
    { return new object[] { entry.refno, entry.colour, entry.transparency, entry.edges, entry.visibility, entry.shallow }; }
    private static DrawListPrimitive AliasWrapper(DrawList list, Tuple<string, string> alias)
    {
        DrawListPrimitive wrapper;
        return list.AliasWrappers.TryGetValue(alias.Item1 + "|" + alias.Item2, out wrapper) ? wrapper : list.Primitives[alias.Item2];
    }
    private static object ManagedState(DrawList list)
    {
        return new
        {
            members = list.Members.OrderBy(item => item.Key, StringComparer.Ordinal).Select(item => Values(item.Value.State)).ToArray(),
            alias_order = list.Aliases.Select(alias => alias.Item1 + "|" + alias.Item2).ToArray(),
            aliases = list.Aliases.OrderBy(alias => alias.Item1 + "|" + alias.Item2, StringComparer.Ordinal).Select(alias =>
            {
                var primitive = AliasWrapper(list, alias);
                var observed = primitive.HasOverride ? primitive.State : new DrawListPrimitiveVisualEntry
                {
                    owner_refno = alias.Item1, refno = alias.Item2,
                    colour = list.Members[primitive.ResolvedOwner].State.colour,
                    transparency = list.Members[primitive.ResolvedOwner].State.transparency,
                    edges = list.Members[primitive.ResolvedOwner].State.edges,
                    visibility = list.Members[primitive.ResolvedOwner].State.visibility,
                    shallow = list.Members[primitive.ResolvedOwner].State.shallow
                };
                return new { key = alias.Item1 + "|" + alias.Item2, wrapper = primitive.WrapperId,
                    identity = primitive.Element.Reference, resolved_owner = primitive.ResolvedOwner,
                    observed = Values(observed), backing = Values(primitive.State),
                    has_override = primitive.HasOverride, materializations = primitive.Materializations };
            }).ToArray()
        };
    }
    private static object ExceptionEvidence(Exception error)
    { return error == null ? null : new { type = error.GetType().FullName, message = error.Message, inner = ExceptionEvidence(error.InnerException) }; }
    private static void ApplyDrawListPrimitiveSnapshot(DrawList drawList,
        IDictionary<string, DrawListPrimitiveVisualEntry> expected, bool requireExactMembership = true,
        IDictionary<string, DrawListVisualEntry> expectedMembers = null, Action<string> attempted = null,
        IList<string> resetMembers = null)
    {
        var before = ManagedState(drawList);
        var request = expected.OrderBy(item => item.Key, StringComparer.Ordinal).Select(item => new { key = item.Key, value = Values(item.Value) }).ToArray();
        var memberRequest = expectedMembers == null ? null : expectedMembers.OrderBy(item => item.Key, StringComparer.Ordinal).Select(item => Values(item.Value)).ToArray();
        int nativeStart = nativeTrace.Count, writeStart = writeTrace.Count, callsStart = nativeCalls,
            writesStart = writes, zeroStart = zeroPrimitiveWrites, afterFaultStart = nativeAfterFault;
        long referencesStart = managedEntryReferenceReads;
        var attempts = new List<string>();
        var resets = resetMembers ?? new List<string>();
        int resetStart = resets.Count;
        Exception failure = null;
        try
        {
            ApplyDrawListPrimitiveSnapshotProduction(drawList, expected, requireExactMembership, expectedMembers,
                field => { attempts.Add(field); if (attempted != null) attempted(field); }, resets);
        }
        catch (Exception error) { failure = error; throw; }
        finally
        {
            long referenceReads = managedEntryReferenceReads - referencesStart;
            observations.Add(new
            {
                scenario = activeScenario, require_exact_membership = requireExactMembership,
                requested = request, requested_members = memberRequest, before = before, after = ManagedState(drawList),
                native_trace = nativeTrace.Skip(nativeStart).ToArray(), write_trace = writeTrace.Skip(writeStart).ToArray(),
                setter_attempts = attempts.ToArray(), reset_members = resets.Skip(resetStart).ToArray(),
                exception = ExceptionEvidence(failure), native_calls = nativeCalls - callsStart, writes = writes - writesStart,
                zero_primitive_writes = zeroPrimitiveWrites - zeroStart, native_after_fault = nativeAfterFault - afterFaultStart,
                fault_at = faultAt, fault_throws = faultThrows, fault_latched = currentNativeFaultState.HasFault,
                fault_generation = currentNativeFaultState.Generation, managed_entry_ref_reads = referenceReads
            });
        }
    }
    private static void Check(bool ok, string label) { if (!ok) throw new Exception(label); checks++; }
    private static void Reject(Action action, string label)
    {
        try { action(); } catch (UnsupportedTransparencyStateException) { Check(writes == 0, label + " writes"); return; }
        throw new Exception("Expected unsupported: " + label);
    }
    private static DbElement Element(int id, DbElement owner)
    { var element = new DbElement { Reference = "=1/" + id, Owner = owner }; database[element.Reference] = element; return element; }
    private static DrawList Fixture(int targetTransparency = 35, int memberTransparency = 0)
    {
        database.Clear(); ResetTrace();
        var world = Element(0, null); var root = Element(1, world); var owner = Element(2, root);
        var list = new DrawList();
        foreach (var element in new[] { root, owner })
            list.Members[element.Reference] = new DrawListMember { DbElement = element, List = list,
                State = new DrawListVisualEntry { refno = element.Reference, colour = 326, transparency = memberTransparency, edges = false, shallow = true } };
        AddPrimitive(list, Element(3, owner), targetTransparency, 0, false, true, false);
        AddPrimitive(list, Element(4, owner), 71, 12, true, false, true);
        return list;
    }
    private static void AddPrimitive(DrawList list, DbElement element, int transparency, int colour, bool edges, bool visibility, bool shallow)
    {
        list.Primitives[element.Reference] = new DrawListPrimitive { DbElement = element, List = list, ResolvedOwner = "=1/2",
            State = new DrawListPrimitiveVisualEntry { owner_refno = "=1/2", refno = element.Reference, transparency = transparency,
                colour = colour, edges = edges, visibility = visibility, shallow = shallow } };
        foreach (string owner in new[] { "=1/1", "=1/2" }) list.Aliases.Add(Tuple.Create(owner, element.Reference));
    }
    private static void SetTarget(IDictionary<string, DrawListPrimitiveVisualEntry> expected, int transparency)
    { foreach (var entry in expected.Values.Where(entry => entry.refno == "=1/3")) entry.transparency = transparency; }
    private static void TransparencyChecks()
    {
            var list = Fixture(); var before = PrimitiveVisualReadMap(list); var wanted = CaptureDrawListPrimitiveSnapshot(list); SetTarget(wanted, 0);
            var resets = new List<string>(); var attempts = new List<string>();
            ApplyDrawListPrimitiveSnapshot(list, wanted, true, before.Members, attempts.Add, resets);
            Check(CompareDrawListPrimitiveSnapshots(wanted, CaptureDrawListPrimitiveSnapshot(list)).Count == 0, "two aliases and all sibling fields restored");
            Check(CompareDrawListVisualSnapshots(before.Members, CaptureDrawListVisualSnapshot(list)).Count == 0, "all five member values preserved");
            Check(resets.SequenceEqual(new[] { "=1/2" }) && attempts[0] == "member_visual_properties:=1/2" && zeroPrimitiveWrites == 0, "resolved member aggregate reset is recorded and zero setter never called");
            writes = 0; ApplyDrawListPrimitiveSnapshot(list, wanted); Check(writes == 0, "repeated zero restore is a no-op");

            list = Fixture(35, 30); wanted = CaptureDrawListPrimitiveSnapshot(list); SetTarget(wanted, 0);
            Reject(() => ApplyDrawListPrimitiveSnapshot(list, wanted), "nonzero resolved member cannot restore primitive zero");
            list = Fixture(0); before = PrimitiveVisualReadMap(list); var memberWanted = CaptureDrawListVisualSnapshot(list); memberWanted["=1/2"].transparency = 25;
            Reject(() => PrimitiveVisualPreflightTransparency(list, before, before.Entries, memberWanted), "member mutation preserving primitive zero rejected before writes");
            list = Fixture(0, 25); before = PrimitiveVisualReadMap(list);
            ApplyDrawListPrimitiveSnapshot(list, before.Entries); Check(writes == 0, "existing nonzero owner plus primitive zero observation remains valid no-op");

            list = Fixture(0); before = PrimitiveVisualReadMap(list); memberWanted = CaptureDrawListVisualSnapshot(list); memberWanted["=1/2"].transparency = 100;
            Reject(() => PrimitiveVisualPreflightTransparency(list, before, before.Entries, memberWanted), "member hundred is rejected before its native 99 clamp");
            wanted = CaptureDrawListPrimitiveSnapshot(list); SetTarget(wanted, 100); ApplyDrawListPrimitiveSnapshot(list, wanted);
            Check(list.Primitives["=1/3"].Transparency == 100 && zeroPrimitiveWrites == 0, "primitive hundred remains exact");

            list = Fixture(); wanted = CaptureDrawListPrimitiveSnapshot(list); SetTarget(wanted, 0);
            AddPrimitive(list, Element(5, database["=1/2"]), 63, 17, true, false, true);
            var extraBefore = CaptureDrawListPrimitiveSnapshot(list).Where(item => item.Value.refno == "=1/5").ToDictionary(item => item.Key, item => item.Value);
            ApplyDrawListPrimitiveSnapshot(list, wanted, false);
            var after = CaptureDrawListPrimitiveSnapshot(list);
            Check(extraBefore.All(item => PrimitiveVisualValuesEqual(item.Value, after[item.Key])) && after.Count == 6,
                "compensation preserves all extra aliases and their five current values");

            list = Fixture(0, 25); before = PrimitiveVisualReadMap(list); list.Members.Clear(); list.Primitives.Clear(); list.Aliases.Clear();
            Reject(() => PrimitiveVisualPreflightTransparency(list, PrimitiveVisualReadMap(list), before.Entries, before.Members),
                "empty startup restore rejects saved nonzero owner plus primitive zero before Add");
            list = Fixture(0); before = PrimitiveVisualReadMap(list); list.Members.Clear(); list.Primitives.Clear(); list.Aliases.Clear();
            Check(PrimitiveVisualPreflightTransparency(list, PrimitiveVisualReadMap(list), before.Entries, before.Members).Length == 0 && writes == 0,
                "empty startup saved zero owner and primitive zero passes read-only preflight");

            list = Fixture(); wanted = CaptureDrawListPrimitiveSnapshot(list); SetTarget(wanted, 0);
            list.Members["=1/2"].ThrowAfterAggregate = true; attempts.Clear();
            try { ApplyDrawListPrimitiveSnapshot(list, wanted, true, null, attempts.Add); throw new Exception("expected injected failure"); }
            catch (InvalidOperationException error) { Check(error.Message == "aggregate mutated then threw" && attempts.Count == 1 && writes == 1,
                "aggregate attempt is recorded before mutation-then-throw and no later writes run"); }
            list.Members["=1/2"].ThrowAfterAggregate = false;
            ApplyDrawListPrimitiveSnapshot(list, wanted); Check(CompareDrawListPrimitiveSnapshots(wanted, CaptureDrawListPrimitiveSnapshot(list)).Count == 0,
                "partial aggregate mutation can be compensated to the exact complete target map");
    }

    private static DrawList InheritedFixture(bool visibility = true, bool shallow = true, bool creationShallow = false)
    {
        var list = Fixture(35, 37);
        var member = list.Members["=1/2"].State;
        member.colour = 326; member.edges = true; member.visibility = visibility; member.shallow = shallow;
        var primitive = list.Primitives["=1/3"];
        primitive.HasOverride = false; primitive.MaterializedShallow = creationShallow;
        return list;
    }
    private static void SetField(DrawListPrimitiveVisualEntry value, string field)
    {
        if (field == "colour") value.colour = 421;
        else if (field == "transparency") value.transparency = 61;
        else if (field == "edges") value.edges = !value.edges;
        else if (field == "visibility") value.visibility = !value.visibility;
        else if (field == "shallow") value.shallow = !value.shallow;
        else throw new Exception("Unknown fixture field");
    }
    private static void RequireCompleteState(DrawList list, IDictionary<string, DrawListPrimitiveVisualEntry> wanted,
        IDictionary<string, DrawListVisualEntry> members, string label)
    {
        var actual = PrimitiveVisualReadMap(list);
        Check(CompareDrawListPrimitiveSnapshots(wanted, actual.Entries).Count == 0, label + ": complete aliases and sibling fields");
        Check(CompareDrawListVisualSnapshots(members, actual.Members).Count == 0, label + ": complete member fields");
        Check(actual.Entries.Count == 4 && list.Primitives.Count == 2, label + ": wrapper membership retained");
        Check(zeroPrimitiveWrites == 0, label + ": primitive zero setter forbidden");
    }
    private static void AuditVisibility(bool originalVisibility, bool originalShallow)
    {
        var list = InheritedFixture(originalVisibility, originalShallow, !originalShallow);
        var member = list.Members["=1/2"].State;
        member.colour = member.transparency = 0; member.edges = false;
        var wanted = CaptureDrawListPrimitiveSnapshot(list);
        // A member-only visibility request changes the inherited observation.
        // Primitive compensation must preserve the original five values.
        member.visibility = !originalVisibility;
        var wantedMembers = CaptureDrawListVisualSnapshot(list);
        ResetTrace();
        ApplyDrawListPrimitiveSnapshot(list, wanted, true, wantedMembers);
        RequireCompleteState(list, wanted, wantedMembers, "member visibility request");
        Check(writeTrace.SequenceEqual(new[] { "=1/3:visibility", "=1/3:shallow" }), "only visibility and newly changed shallow need compensation");
        Check(list.Primitives["=1/3"].Materializations == 1, "first visibility setter materialized once");
    }
    private static void Materialization(string firstField, bool initialVisibility, bool initialShallow, bool creationShallow)
    {
        var list = InheritedFixture(initialVisibility, initialShallow, creationShallow);
        var before = PrimitiveVisualReadMap(list);
        var wanted = CaptureDrawListPrimitiveSnapshot(list);
        foreach (var entry in wanted.Values.Where(entry => entry.refno == "=1/3")) SetField(entry, firstField);
        var attempts = new List<string>();
        ResetTrace();
        ApplyDrawListPrimitiveSnapshot(list, wanted, true, before.Members, attempts.Add);
        RequireCompleteState(list, wanted, before.Members, "first setter " + firstField);
        Check(attempts.Count > 0 && attempts[0] == firstField, "first actual setter is " + firstField);
        Check(attempts.Count <= 10 && list.Primitives["=1/3"].Materializations == 1, "one materialization with bounded writes");
        Check(!writeTrace.Any(value => value.StartsWith("=1/4:", StringComparison.Ordinal)), "unrequested sibling needs no writes");
        int count = writes;
        ApplyDrawListPrimitiveSnapshot(list, wanted, true, before.Members);
        Check(writes == count, "already converged map has no extra writes");
    }
    private static void NoOpAndExistingOverride()
    {
        var list = InheritedFixture(); var before = PrimitiveVisualReadMap(list);
        ResetTrace(); ApplyDrawListPrimitiveSnapshot(list, before.Entries, true, before.Members);
        Check(writes == 0 && !list.Primitives["=1/3"].HasOverride && list.Primitives["=1/3"].Materializations == 0,
            "matching inherited observations never create an override");
        RequireCompleteState(list, before.Entries, before.Members, "inherited no-op");
        list = Fixture(); before = PrimitiveVisualReadMap(list);
        var wanted = CaptureDrawListPrimitiveSnapshot(list);
        foreach (var entry in wanted.Values.Where(entry => entry.refno == "=1/3")) entry.visibility = !entry.visibility;
        ResetTrace(); ApplyDrawListPrimitiveSnapshot(list, wanted, true, before.Members);
        RequireCompleteState(list, wanted, before.Members, "existing override visibility");
        Check(writeTrace.SequenceEqual(new[] { "=1/3:visibility" }), "ordinary visibility writes do not change or rewrite shallow");
        Check(list.Primitives["=1/3"].Materializations == 0, "existing override was reused");
    }
    private static void OrdinaryFailureRollback(string field)
    {
        var list = InheritedFixture(); var before = PrimitiveVisualReadMap(list);
        var wanted = CaptureDrawListPrimitiveSnapshot(list);
        foreach (var entry in wanted.Values.Where(entry => entry.refno == "=1/3")) SetField(entry, field);
        var primitive = list.Primitives["=1/3"]; primitive.ThrowAfterField = field;
        var attempts = new List<string>(); ResetTrace();
        bool rejected = false;
        try { ApplyDrawListPrimitiveSnapshot(list, wanted, true, before.Members, attempts.Add); }
        catch (InvalidOperationException error)
        { rejected = error.Message == "setter mutated then threw: " + field; }
        Check(rejected && writes == 1 && attempts.SequenceEqual(new[] { field }), "ordinary failure stops after first mutation: " + field);
        Check(nativeTrace.Last() == "set:=1/3:" + field, "no read or write follows thrown setter");
        // Exercise the same real compensation entry point with the original map.
        // This checks rollback's restoration engine, not the host's transaction wrapper.
        primitive.ThrowAfterField = null;
        ApplyDrawListPrimitiveSnapshot(list, before.Entries, true, before.Members);
        RequireCompleteState(list, before.Entries, before.Members, "ordinary rollback " + field);
        Check(primitive.Materializations == 1, "rollback restored observed values after one materialization");
    }
    private static void Nonconvergence()
    {
        var list = InheritedFixture(); var before = PrimitiveVisualReadMap(list);
        var wanted = CaptureDrawListPrimitiveSnapshot(list);
        foreach (var entry in wanted.Values.Where(entry => entry.refno == "=1/3")) entry.shallow = false;
        var primitive = list.Primitives["=1/3"];
        // Deliberately unstable behavior is separate from ordinary materialization.
        // It never allows the requested shallow observation to survive a setter.
        primitive.AfterSetter = (item, field) => item.State.shallow = true;
        ResetTrace(); bool rejected = false;
        try { ApplyDrawListPrimitiveSnapshot(list, wanted, true, before.Members); }
        catch (InvalidOperationException) { rejected = true; }
        Check(rejected && writes > 0 && writes <= 10 && nativeCalls < 400, "nonconvergence rejects within explicit write/read bounds");
        Check(!writeTrace.Any(value => value.StartsWith("=1/4:", StringComparison.Ordinal)), "nonconvergence stops before sibling writes");
        primitive.AfterSetter = null;
        ApplyDrawListPrimitiveSnapshot(list, before.Entries, true, before.Members);
        RequireCompleteState(list, before.Entries, before.Members, "nonconvergence rollback");
    }
    private static void ZeroTransparencyDrift()
    {
        var list = InheritedFixture(); var member = list.Members["=1/2"].State; member.transparency = 0;
        var before = PrimitiveVisualReadMap(list); var wanted = CaptureDrawListPrimitiveSnapshot(list);
        foreach (var entry in wanted.Values.Where(entry => entry.refno == "=1/3")) entry.visibility = false;
        var primitive = list.Primitives["=1/3"];
        // This is adversarial cross-field drift, not the native creation defaults.
        primitive.AfterSetter = (item, field) => item.State.transparency = 19;
        ResetTrace(); bool rejected = false;
        try { ApplyDrawListPrimitiveSnapshot(list, wanted, true, before.Members); }
        catch (InvalidOperationException) { rejected = true; }
        Check(rejected && writes == 1 && zeroPrimitiveWrites == 0, "new zero-transparency mismatch rejects without a primitive zero write");
        Check(!nativeTrace.Any(value => value.StartsWith("aggregate:", StringComparison.Ordinal)), "no unplanned aggregate reset after drift");
        primitive.AfterSetter = null;
        ApplyDrawListPrimitiveSnapshot(list, before.Entries, true, before.Members);
        RequireCompleteState(list, before.Entries, before.Members, "zero drift rollback through normal preflight");
    }
    private static void InvalidExpectedAliases()
    {
        var list = InheritedFixture(); var wanted = CaptureDrawListPrimitiveSnapshot(list);
        wanted["=1/1|=1/3"].shallow = false;
        ResetTrace(); bool rejected = false;
        try { ApplyDrawListPrimitiveSnapshot(list, wanted); }
        catch (InvalidOperationException) { rejected = true; }
        Check(rejected && writes == 0, "conflicting owner aliases reject before writes");
        wanted = CaptureDrawListPrimitiveSnapshot(list); wanted.Remove("=1/1|=1/3");
        ResetTrace(); rejected = false;
        try { ApplyDrawListPrimitiveSnapshot(list, wanted); }
        catch (InvalidOperationException) { rejected = true; }
        Check(rejected && writes == 0, "exact membership rejects missing alias before writes");
    }
    private static void DistinguishAliases(DrawList list)
    {
        list.AliasWrappers.Clear();
        foreach (var alias in list.Aliases)
        {
            var original = list.Primitives[alias.Item2];
            Check(original.HasOverride, "distinct-wrapper fixture uses shared existing override state");
            list.AliasWrappers.Add(alias.Item1 + "|" + alias.Item2, new DrawListPrimitive
            {
                Element = original.Element, WrapperId = alias.Item1, List = list, ResolvedOwner = original.ResolvedOwner,
                State = original.State, HasOverride = true
            });
        }
    }
    private static DrawList ScrambledAliasFixture()
    {
        var list = Fixture();
        var intermediate = Element(10, database["=1/1"]); database["=1/2"].Owner = intermediate;
        list.Members.Add(intermediate.Reference, new DrawListMember { DbElement = intermediate, List = list,
            State = new DrawListVisualEntry { refno = intermediate.Reference, colour = 326, shallow = true } });
        foreach (int id in new[] { 20, 100, 11 }) AddPrimitive(list, Element(id, database["=1/2"]), 43, 16, false, true, false);
        list.Aliases.Clear();
        foreach (string reference in new[] { "=1/3", "=1/20", "=1/4", "=1/11", "=1/100" })
        foreach (string owner in new[] { "=1/2", "=1/1", "=1/10" }) list.Aliases.Add(Tuple.Create(owner, reference));
        DistinguishAliases(list);
        return list;
    }
    private static void OrdinalReferencesAndKeys()
    {
        var list = ScrambledAliasFixture(); var wanted = CaptureDrawListPrimitiveSnapshot(list);
        foreach (var entry in wanted.Values) entry.colour = 421;
        ResetTrace(); ApplyDrawListPrimitiveSnapshot(list, wanted);
        string[] ordered = new[] { "=1/100", "=1/11", "=1/20", "=1/3", "=1/4" };
        Check(writeTrace.SequenceEqual(ordered.Select(reference => reference + "@=1/10:colour")),
            "target references and first alias keys use Ordinal order despite scrambled insertion");
        Check(CompareDrawListPrimitiveSnapshots(wanted, CaptureDrawListPrimitiveSnapshot(list)).Count == 0,
            "Ordinal target traversal restores every alias");
    }
    private static void DistinctAliasWrapperSelection()
    {
        var list = ScrambledAliasFixture(); var wanted = CaptureDrawListPrimitiveSnapshot(list);
        foreach (var entry in wanted.Values.Where(entry => entry.refno == "=1/3")) { entry.colour = 421; entry.visibility = false; }
        ResetTrace(); ApplyDrawListPrimitiveSnapshot(list, wanted);
        Check(writeTrace.SequenceEqual(new[] { "=1/3@=1/10:colour", "=1/3@=1/10:visibility" }),
            "writes reuse the first Ordinal existing alias wrapper");
        var identities = nativeTrace.Where(value => value.StartsWith("identity:=1/3@", StringComparison.Ordinal)).ToArray();
        var oneRead = new[] { "=1/10", "=1/1", "=1/2" }.SelectMany(owner => Enumerable.Repeat("identity:=1/3@" + owner, 3));
        Check(identities.SequenceEqual(Enumerable.Range(0, 3).SelectMany(index => oneRead)),
            "every distinct alias is re-read in Ordinal order initially and after each of two setters");
        Check(CompareDrawListPrimitiveSnapshots(wanted, CaptureDrawListPrimitiveSnapshot(list)).Count == 0,
            "all distinct wrappers observe the converged shared native state");
        ResetTrace(); ApplyDrawListPrimitiveSnapshot(list, wanted);
        Check(writes == 0, "distinct aliases retain a no-op after convergence");
    }
    private static void NonExactExtrasAfterAggregateReset()
    {
        var list = ScrambledAliasFixture(); var wanted = CaptureDrawListPrimitiveSnapshot(list);
        SetTarget(wanted, 0);
        AddPrimitive(list, Element(12, database["=1/2"]), 63, 17, true, false, true);
        list.Aliases.Add(Tuple.Create("=1/10", "=1/12"));
        DistinguishAliases(list);
        var complete = CaptureDrawListPrimitiveSnapshot(list); SetTarget(complete, 0);
        var members = CaptureDrawListVisualSnapshot(list);
        var resets = new List<string>(); var attempts = new List<string>();
        ResetTrace(); ApplyDrawListPrimitiveSnapshot(list, wanted, false, members, attempts.Add, resets);
        Check(resets.SequenceEqual(new[] { "=1/2" }) && attempts[0] == "member_visual_properties:=1/2",
            "non-exact request resets the resolved member before primitive compensation");
        Check(nativeTrace.IndexOf("aggregate:=1/2") < nativeTrace.FindIndex(value => value.StartsWith("set:", StringComparison.Ordinal)),
            "aggregate reset precedes every primitive setter");
        Check(writeTrace.Any(value => value.StartsWith("=1/12@=1/10:", StringComparison.Ordinal)),
            "extras missing from the supplied expected map are indexed and compensated");
        Check(CompareDrawListPrimitiveSnapshots(complete, CaptureDrawListPrimitiveSnapshot(list)).Count == 0 && list.Aliases.Count == 18,
            "all eighteen aliases including extras retain five exact requested or original values");
        Check(CompareDrawListVisualSnapshots(members, CaptureDrawListVisualSnapshot(list)).Count == 0 && zeroPrimitiveWrites == 0,
            "aggregate reset preserves members and never invokes the clamped primitive zero setter");
    }
    private static void ManagedGroupingWork()
    {
        foreach (int count in new[] { 32, 512 })
        {
            var list = Fixture(); list.Primitives.Clear(); list.Aliases.Clear();
            for (int id = count + 100; id >= 101; id--) AddPrimitive(list, Element(id, database["=1/2"]), 37, 12, false, true, false);
            var wanted = CaptureDrawListPrimitiveSnapshot(list);
            ResetTrace(); ApplyDrawListPrimitiveSnapshot(list, wanted);
            Check(writes == 0 && wanted.Count == count * 2, "managed work fixture retains all aliases without writes: " + count);
        }
    }
    private static void NativeFaultStopping()
    {
        var list = InheritedFixture(false, true, false); var before = PrimitiveVisualReadMap(list);
        var wanted = CaptureDrawListPrimitiveSnapshot(list);
        foreach (var entry in wanted.Values.Where(entry => entry.refno == "=1/3")) entry.shallow = false;
        ResetTrace(); ApplyDrawListPrimitiveSnapshot(list, wanted, true, before.Members);
        var boundaries = nativeTrace.ToArray();
        Check(boundaries.Any(value => value.StartsWith("set:", StringComparison.Ordinal)), "fault matrix includes real setters");
        foreach (bool alsoThrows in new[] { false, true })
        for (int index = 1; index <= boundaries.Length; index++)
        {
            list = InheritedFixture(false, true, false); before = PrimitiveVisualReadMap(list);
            wanted = CaptureDrawListPrimitiveSnapshot(list);
            foreach (var entry in wanted.Values.Where(entry => entry.refno == "=1/3")) entry.shallow = false;
            ResetTrace(); faultAt = index; faultThrows = alsoThrows; bool rejected = false;
            try { ApplyDrawListPrimitiveSnapshot(list, wanted, true, before.Members); }
            catch (NativeDatabaseFaultException) { rejected = true; }
            string label = "fault " + index + " " + boundaries[index - 1] + " throw=" + alsoThrows;
            Check(rejected && currentNativeFaultState.HasFault, label + ": real fault barrier rejects");
            Check(nativeCalls == index && nativeAfterFault == 0, label + ": no later native operation");
            int writesAtFault = writes;
            rejected = false;
            try { ApplyDrawListPrimitiveSnapshot(list, before.Entries, true, before.Members); }
            catch (NativeDatabaseFaultException) { rejected = true; }
            Check(rejected && nativeCalls == index && writes == writesAtFault, label + ": fault blocks attempted rollback entry");
            faultCases++;
        }
        ResetTrace();
        Console.WriteLine("Native fault matrix: " + boundaries.Length + " boundaries x returning/throwing callbacks = " + faultCases + " cases");
    }
    private static int failures;
    private static void Run(string label, Action action)
    {
        scenarios++;
        activeScenario = label;
        try { action(); scenarioResults.Add(new { scenario = label, passed = true, exception = (object)null }); Console.WriteLine("PASS " + label); }
        catch (Exception error) { failures++; scenarioResults.Add(new { scenario = label, passed = false, exception = ExceptionEvidence(error) }); Console.Error.WriteLine("FAIL " + label + ": " + error); }
    }
    private static int Main(string[] args)
    {
        Run("existing transparency clamp and aggregate restoration", TransparencyChecks);
        foreach (bool visibility in new[] { false, true })
        foreach (bool shallow in new[] { false, true })
        {
            bool v = visibility, s = shallow;
            Run("audit visibility=" + v + " shallow=" + s, () => AuditVisibility(v, s));
            foreach (string field in new[] { "visibility", "shallow" })
            foreach (bool creation in new[] { false, true })
            {
                string f = field; bool c = creation;
                Run("materialize via " + f + " visibility=" + v + " shallow=" + s + " creationShallow=" + c,
                    () => Materialization(f, v, s, c));
            }
        }
        foreach (string field in new[] { "colour", "transparency", "edges" })
        {
            string f = field; Run("materialize via " + f, () => Materialization(f, false, true, false));
        }
        Run("no-op and existing override", NoOpAndExistingOverride);
        foreach (string field in new[] { "colour", "transparency", "edges", "visibility", "shallow" })
        {
            string f = field; Run("ordinary failure rollback " + f, () => OrdinaryFailureRollback(f));
        }
        Run("bounded nonconvergence and rollback", Nonconvergence);
        Run("zero transparency drift and rollback", ZeroTransparencyDrift);
        Run("invalid aliases and membership", InvalidExpectedAliases);
        Run("index Ordinal references and keys", OrdinalReferencesAndKeys);
        Run("index distinct alias wrapper selection", DistinctAliasWrapperSelection);
        Run("index non-exact extras and aggregate reset", NonExactExtrasAfterAggregateReset);
        Run("index managed grouping work", ManagedGroupingWork);
        Run("native fault stopping", NativeFaultStopping);
        if (args.Length != 0)
        {
            var serializer = new JavaScriptSerializer { MaxJsonLength = int.MaxValue, RecursionLimit = 100 };
            File.WriteAllText(args[0], serializer.Serialize(new { checks = checks, scenarios = scenarios,
                native_fault_cases = faultCases, failures = failures, results = scenarioResults, applies = observations }));
        }
        if (failures != 0) { Console.Error.WriteLine("Failed scenarios: " + failures + "/" + scenarios + "; passed checks: " + checks); return 1; }
        Console.WriteLine("Primitive visual restoration behavior checks passed: " + checks + "; scenarios: " + scenarios + "; native faults: " + faultCases);
        return 0;
    }

    // PRODUCTION_METHODS
}
