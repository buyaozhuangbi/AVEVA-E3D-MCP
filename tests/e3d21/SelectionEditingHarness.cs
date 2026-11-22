using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Aveva.Core.Database;
using Aveva.Core3D.Graphics;

namespace Aveva.Core.Database
{
    public sealed class DbElementType
    {
        internal readonly string Value;
        internal DbElementType(string value) { Value = value; }
        public string ShortName { get { return Scenario.Read("type.short_name", Value); } }
        public string Name { get { return ShortName; } }
        public bool IsWorld { get { return Scenario.Read("type.is_world", Value == "WORL"); } }
    }
    public sealed class DbElement
    {
        internal int Id;
        internal string Type = "BOX";
        internal DbElement OwnerValue;
        internal DbElement(int id) { Id = id; }
        public bool IsNull { get { return Scenario.Read("element." + Id + ".is_null", false); } }
        public bool IsValid { get { return Scenario.Read("element." + Id + ".is_valid", true); } }
        public bool IsDeleted { get { return Scenario.Read("element." + Id + ".is_deleted", false); } }
        public int[] Ref { get { return Scenario.Read("element." + Id + ".ref", new[] { 1, Id }); } }
        public DbElement Owner { get { return Scenario.Read("element." + Id + ".owner", OwnerValue); } }
        public DbElementType GetElementType() { return Scenario.Read("element." + Id + ".type", new DbElementType(Type)); }
        public static DbElement GetElement(int[] reference)
        {
            DbElement value;
            Scenario.Database.TryGetValue("=" + reference[0] + "/" + reference[1], out value);
            return Scenario.Read("lookup." + reference[0] + "/" + reference[1], value);
        }
    }
    public sealed class Project
    {
        internal string NameValue = "PROJECT";
        public static Project CurrentProject { get { return Scenario.Read("project.current", Scenario.Project); } }
        public string Name { get { return Scenario.Read("project.name", NameValue); } }
    }
    public sealed class MDB
    {
        internal string NameValue = "MDB";
        public static MDB CurrentMDB { get { return Scenario.Read("mdb.current", Scenario.Mdb); } }
        public string Name { get { return Scenario.Read("mdb.name", NameValue); } }
        public DbElement FindElement(string reference)
        {
            DbElement value;
            Scenario.Database.TryGetValue(reference, out value);
            return Scenario.Read("mdb.lookup." + reference, value);
        }
    }
}

namespace Aveva.Core3D.Graphics
{
    public sealed class View3D
    {
        internal int Id = 7;
        public int ViewId { get { return Scenario.Read("view.id", Id); } }
        public bool IsValid() { return Scenario.Read("view.is_valid", true); }
    }
    public sealed class View3DManager
    {
        public static View3DManager Instance { get { return Scenario.Read("view_manager.instance", new View3DManager()); } }
        public View3D CurrentView { get { return Scenario.Read("current_view", Scenario.View); } }
    }
    public class VisualFields
    {
        internal int ColourValue = 4, TransparencyValue;
        internal bool EdgesValue = true, VisibilityValue = true, ShallowValue;
        internal string Label;
        internal DbElement ElementValue;
        public DbElement DbElement { get { return Scenario.Read(Label + ".element", ElementValue); } }
        public int Colour { get { return Scenario.Read(Label + ".colour", ColourValue); } }
        public int Transparency { get { return Scenario.Read(Label + ".transparency", TransparencyValue); } }
        public bool Edges { get { return Scenario.Read(Label + ".edges", EdgesValue); } }
        public bool Visibility { get { return Scenario.Read(Label + ".visibility", VisibilityValue); } }
        public bool ShallowRendering { get { return Scenario.Read(Label + ".shallow", ShallowValue); } }
    }
    public sealed class DrawListPrimitive : VisualFields { }
    public sealed class DrawListMember : VisualFields
    {
        internal DrawListPrimitive[] PrimitiveValues = new DrawListPrimitive[0];
        public DrawListPrimitive[] Primitives { get { return Scenario.Read(Label + ".primitives", PrimitiveValues); } }
    }
    public sealed class DrawList
    {
        internal int Id = 11;
        internal DrawListMember[] MemberValues;
        public int ID { get { return Scenario.Read("drawlist.id", Id); } }
        public DbElement[] GraphicalSelection() { return Scenario.Read("drawlist.selection", Scenario.Selected); }
        public DrawListMember[] Members() { return Scenario.Read("drawlist.members", MemberValues); }
        public void ClearGraphicalSelection()
        {
            Scenario.Selected = new DbElement[0];
            Scenario.Write("write.clear");
        }
        public void AddToSelection(DbElement[] elements, bool addGraphics = true)
        {
            Scenario.AddGraphicsArguments.Add(addGraphics);
            Scenario.Selected = elements.ToArray();
            Scenario.Write("write.add");
        }
        public void GraphicalSelectionHasChanged() { Scenario.Write("write.notify"); }
    }
    public sealed class DrawListManager
    {
        public static DrawListManager Instance { get { return Scenario.Read("manager.instance", Scenario.Manager); } }
        public DrawList CurrentDrawList { get { return Scenario.Read("manager.current_drawlist", Scenario.DrawList); } }
        public DrawList getViewDrawList(int id) { return Scenario.Read("manager.view_drawlist", Scenario.DrawList); }
    }
}

namespace Aveva.Core.Utilities.CommandLine
{
    public static class Command
    {
        public static void Update() { Scenario.Write("write.update"); }
    }
}

internal static class Scenario
{
    internal static readonly List<string> Calls = new List<string>();
    internal static readonly List<string> Writes = new List<string>();
    internal static readonly List<bool> AddGraphicsArguments = new List<bool>();
    internal static readonly Dictionary<string, object> Overrides = new Dictionary<string, object>();
    internal static readonly Dictionary<string, DbElement> Database = new Dictionary<string, DbElement>();
    internal static Action<string> OnNative;
    internal static Action<int> BeforeDispatch;
    internal static DbElement[] Selected;
    internal static View3D View;
    internal static DrawList DrawList;
    internal static DrawListManager Manager;
    internal static Project Project;
    internal static MDB Mdb;
    internal static string Ce;
    internal static int Dispatches, Depth, MaximumDepth;
    internal static long PrimitiveReferenceReads;
    internal static bool ExternalBusy;
    internal static T Read<T>(string path, T fallback)
    {
        Calls.Add(path);
        if (OnNative != null) OnNative(path);
        object value;
        if (!Overrides.TryGetValue(path, out value)) return fallback;
        if (value is Exception) throw (Exception)value;
        return (T)value;
    }
    internal static void Write(string path)
    {
        Writes.Add(path);
        Read(path, true);
    }
    internal static DbElement Element(int id) { return Database["=1/" + id]; }
    internal static void Reset(params int[] selected)
    {
        Database.Clear(); Overrides.Clear();
        for (int id = 1; id <= 205; id++) Database.Add("=1/" + id, new DbElement(id));
        Element(2).OwnerValue = Element(1); Element(3).OwnerValue = Element(1);
        Selected = selected.Select(Element).ToArray();
        View = new View3D(); Manager = new DrawListManager(); Project = new Project(); Mdb = new MDB();
        DrawList = new DrawList { MemberValues = new[]
        {
            new DrawListMember { Label = "member.1", ElementValue = Element(1), PrimitiveValues = new[]
            {
                new DrawListPrimitive { Label = "primitive.2", ElementValue = Element(2) },
                new DrawListPrimitive { Label = "primitive.3", ElementValue = Element(3) }
            } },
            new DrawListMember { Label = "member.4", ElementValue = Element(4) }
        } };
        Ce = "=1/99"; ExternalBusy = false;
        ClearTrace();
    }
    internal static void ClearTrace()
    {
        Calls.Clear(); Writes.Clear(); AddGraphicsArguments.Clear(); OnNative = null; BeforeDispatch = null;
        Dispatches = Depth = MaximumDepth = 0;
        PrimitiveReferenceReads = 0;
    }
}

namespace E3d21AiBridge
{
    internal sealed class SelectionInnerRequest
    {
        internal NameValueCollection QueryString;
        internal MemoryStream InputStream;
        internal Encoding ContentEncoding = Encoding.UTF8;
    }
    internal sealed class SelectionRequest
    {
        internal SelectionInnerRequest Request;
        internal SelectionRequest(string body, NameValueCollection query = null)
        {
            Request = new SelectionInnerRequest { QueryString = query ?? new NameValueCollection(),
                InputStream = new MemoryStream(Encoding.UTF8.GetBytes(body)) };
        }
    }
    internal sealed class SelectionRouteContext
    {
        internal int Calls;
        internal IList<string> Refs;
        internal string ExpectedRevision;
        internal bool DryRun;
        internal readonly object Result = new { success = false, verification_state = "not_confirmed" };
        public object SetGraphicalSelection(IList<string> refs, string revision, bool dryRun)
        {
            Calls++; Refs = refs; ExpectedRevision = revision; DryRun = dryRun; return Result;
        }
    }
    internal static class SelectionProductionRoute
    {
        private const int MaximumJsonRequestBodyBytes = 1024 * 1024;
        internal static async Task<object> Invoke(SelectionRequest request, SelectionRouteContext context)
        {
            object response = null;
            string sessionId = "SELECTION-SESSION";
            // PRODUCTION_ROUTE
            return response;
        }
        // ROUTE_METHODS
    }
    public sealed partial class E3d21Context
    {
        private const int DisplayStateMemberLimit = 10000, DisplayStatePrimitiveLimit = 20000, DisplayStateOwnerDepthLimit = 1024;
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;
        private readonly Action<Action> invokeOnUi;
        private readonly Func<Dictionary<string, object>> executionState;
        private readonly object commandLock = new object();
        private bool commandActive;
        private string activeCommand = "";
        private readonly string primitiveVisualRevisionSession = Guid.NewGuid().ToString("N");
        private readonly string primitiveRevisionSession = Guid.NewGuid().ToString("N");
        public E3d21Context()
        {
            invokeOnUi = action =>
            {
                Scenario.Dispatches++;
                if (Scenario.BeforeDispatch != null) Scenario.BeforeDispatch(Scenario.Dispatches);
                Scenario.Depth++; Scenario.MaximumDepth = Math.Max(Scenario.MaximumDepth, Scenario.Depth);
                try { action(); } finally { Scenario.Depth--; }
            };
            executionState = () => new Dictionary<string, object>
            {
                ["native_operation_active"] = Scenario.ExternalBusy,
                ["dispatcher_ready"] = true
            };
        }
        internal void Fault() { nativeFaultState.Observe("Unhandled Database error 538 from FakeSelection", ""); }
        private static string ModelCurrentReference() { return NativeRead(() => Scenario.Read("ce", Scenario.Ce)); }
        private static object CurrentView3D() { return Scenario.Read("current_view", Scenario.View); }
        private static MDB CurrentMdb() { return NativeRead(() => MDB.CurrentMDB); }
        private static View3D CameraRequireView()
        {
            View3D view = NativeRead(() => CurrentView3D()) as View3D;
            if (view == null || !NativeRead(() => view.IsValid())) throw new InvalidOperationException("No active View3D.");
            return view;
        }
        private static string ModelReference(int[] reference)
        {
            if (reference == null || reference.Length != 2 || reference[0] <= 0 || reference[1] < 0)
                throw new InvalidOperationException("Invalid database reference.");
            return "=" + reference[0] + "/" + reference[1];
        }
        private static string ElementRef(DbElement element) { return ModelReference(NativeRead(() => element.Ref)); }
        private sealed class DrawListVisualEntry
        {
            public string refno;
            public int colour, transparency;
            public bool edges, visibility, shallow;
        }
        private sealed class DrawListPrimitiveVisualEntry
        {
            public string owner_refno;
            private string reference;
            public string refno
            {
                get { Scenario.PrimitiveReferenceReads++; return reference; }
                set { reference = value; }
            }
            public int colour, transparency;
            public bool edges, visibility, shallow;
        }
        private sealed class PrimitiveVisualMap
        {
            internal readonly Dictionary<string, DrawListVisualEntry> Members = new Dictionary<string, DrawListVisualEntry>(StringComparer.Ordinal);
            internal readonly Dictionary<string, DrawListPrimitiveVisualEntry> Entries = new Dictionary<string, DrawListPrimitiveVisualEntry>(StringComparer.Ordinal);
            internal readonly Dictionary<string, DrawListPrimitive> Wrappers = new Dictionary<string, DrawListPrimitive>(StringComparer.Ordinal);
            internal readonly HashSet<string> Owners = new HashSet<string>(StringComparer.Ordinal);
            internal readonly List<string> Errors = new List<string>();
        }
        internal static DbElement[] ResolveObservedForHarness(string[] requested, string[] members, string[] primitives, bool original)
        {
            var state = new SelectionEditingState { Display = new PrimitiveVisualMap() };
            foreach (string reference in members)
                state.Display.Members.Add(reference, new DrawListVisualEntry { refno = reference });
            for (int i = 0; i < primitives.Length; i++)
                state.Display.Entries.Add("observed-entry-" + i, new DrawListPrimitiveVisualEntry { refno = primitives[i] });
            Scenario.PrimitiveReferenceReads = 0;
            return original ? SelectionEditingResolveBeforeIndex(requested, state) : SelectionEditingResolve(requested, state);
        }

        // Frozen pre-optimization implementation: compare actual native traces,
        // result order and failure messages against the production resolver.
        private static DbElement[] SelectionEditingResolveBeforeIndex(string[] requested, SelectionEditingState state)
        {
            var resolved = new List<DbElement>();
            foreach (string reference in requested)
            {
                string[] parts = reference.Substring(1).Split('/');
                int[] pair = { int.Parse(parts[0], CultureInfo.InvariantCulture), int.Parse(parts[1], CultureInfo.InvariantCulture) };
                DbElement element = NativeRead(() => DbElement.GetElement(pair));
                if (SelectionEditingElementReference(element) != reference)
                    throw new InvalidOperationException("Direct lookup returned a different element: " + reference);
                if (!state.Display.Members.ContainsKey(reference) && !state.Display.Entries.Values.Any(entry => entry.refno == reference))
                    throw new InvalidOperationException("Requested reference has no exact observed Drawlist member or primitive entry: " + reference);
                resolved.Add(element);
            }
            return resolved.ToArray();
        }
        // PRODUCTION_METHODS
    }

    internal static class SelectionEditingHarness
    {
        private static int checks;
        private static void Check(bool condition, string label)
        { checks++; if (!condition) throw new Exception("Check failed: " + label); }
        private static Dictionary<string, object> Dict(object value) { return (Dictionary<string, object>)value; }
        private static bool Yes(Dictionary<string, object> result, string field) { object value; return result.TryGetValue(field, out value) && Equals(value, true); }
        private static string[] Refs(params int[] ids) { return ids.Select(id => "=1/" + id).ToArray(); }
        private static Dictionary<string, object> Preview(E3d21Context context, params int[] ids)
        { return Dict(context.SetGraphicalSelection(Refs(ids), null, true)); }
        private static string Revision(E3d21Context context, params int[] ids)
        {
            var preview = Preview(context, ids);
            Check(Yes(preview, "success"), "valid preview: " + (preview.ContainsKey("error") ? preview["error"] : ""));
            string revision = (string)preview["revision"];
            Check(revision != null && revision.Length == 64, "SHA256 revision");
            return revision;
        }
        private static Dictionary<string, object> Apply(E3d21Context context, string revision, params int[] ids)
        { return Dict(context.SetGraphicalSelection(Refs(ids), revision, false)); }
        private static void Throws<T>(Action action, string label) where T : Exception
        {
            try { action(); } catch (T) { Check(true, label); return; }
            throw new Exception("Expected " + typeof(T).Name + ": " + label);
        }

        private static void PreviewAndValidation()
        {
            Scenario.Reset(4);
            var context = new E3d21Context();
            Revision(context, 2, 3);
            Check(Scenario.Dispatches == 1 && Scenario.Writes.Count == 0, "preview dispatches once and never mutates");
            Check(Scenario.Selected.Select(e => e.Id).SequenceEqual(new[] { 4 }), "preview preserves selection");
            Scenario.ClearTrace();
            foreach (IList<string> invalid in new IList<string>[] { null, new[] { "" }, new[] { "CE" }, new[] { "/NAME" }, new[] { "=0/1" }, new[] { "=1/-1" }, new[] { "=01/2" }, new[] { "=1/02" }, new[] { " =1/2" }, new[] { "=1/2 " }, new[] { "=2147483648/2" }, Refs(Enumerable.Range(1, 33).ToArray()) })
                Throws<ArgumentException>(() => context.SetGraphicalSelection(invalid, null, true), "invalid explicit ref input");
            Throws<ArgumentException>(() => context.SetGraphicalSelection(Refs(2), null, false), "apply needs revision");
            Throws<ArgumentException>(() => context.SetGraphicalSelection(Refs(2), "bad", true), "bad revision");
            Check(Scenario.Calls.Count == 0 && Scenario.Dispatches == 0, "input invalid before SDK/dispatch");
            Throws<ArgumentException>(() => context.SetGraphicalSelection(new[] { "=1/2", "=1/3", "=1/2" }, null, true), "duplicate refs rejected explicitly");
            Scenario.Reset(4);
            context = new E3d21Context();
            Check(!Yes(Preview(context, 99), "success") && Scenario.Writes.Count == 0, "existing but undisplayed ref rejected");
            Scenario.Reset(4); Scenario.Database.Remove("=1/2");
            Check(!Yes(Preview(new E3d21Context(), 2), "success") && Scenario.Writes.Count == 0, "missing direct lookup rejected");
        }

        private static void ReplaceClearAndNoop()
        {
            Scenario.Reset(4);
            var context = new E3d21Context(); string revision = Revision(context, 2, 3);
            Scenario.ClearTrace();
            var result = Apply(context, revision, 2, 3);
            Check(Yes(result, "success") && Yes(result, "readback_verified"), "replace independently verified");
            Check(Scenario.Writes.SequenceEqual(new[] { "write.clear", "write.add", "write.notify", "write.update" }), "replace exactly once in order");
            Check(Scenario.AddGraphicsArguments.SequenceEqual(new[] { false }), "addGraphics fixed false");
            Check(Scenario.Dispatches == 2 && Scenario.MaximumDepth == 1, "follow-up readback is a separate dispatch");
            Check(Scenario.Selected.Select(e => e.Id).SequenceEqual(new[] { 2, 3 }), "desired native refs selected");
            Check(Yes(result, "write_attempted") && Yes(result, "native_write_returned") && Yes(result, "notification_completed") && Yes(result, "update_completed"), "phase receipts completed");
            Check(!Yes(result, "retry_safe") && !Yes(result, "outcome_uncertain"), "verified write is never replay-safe");

            revision = Revision(context);
            Scenario.ClearTrace(); result = Apply(context, revision);
            Check(Yes(result, "success") && Yes(result, "readback_verified") && Scenario.Selected.Length == 0, "explicit empty refs clears");
            Check(Scenario.Writes.SequenceEqual(new[] { "write.clear", "write.notify", "write.update" }), "clear never calls empty add");

            Scenario.Reset(3, 2); context = new E3d21Context(); revision = Revision(context, 2, 3);
            Scenario.ClearTrace(); result = Apply(context, revision, 2, 3);
            Check(Yes(result, "success") && Yes(result, "no_change") && !Yes(result, "write_attempted"), "equal set is no-op");
            Check(Scenario.Writes.Count == 0 && Scenario.Selected.Select(e => e.Id).SequenceEqual(new[] { 3, 2 }), "no-op preserves native order and does not notify/update");
        }

        private static long[] CompareResolvers(string[] requested, string[] members, string[] primitives, bool expectedSuccess, string label)
        {
            string[] originalTrace = null;
            DbElement[] originalElements = null;
            Exception originalError = null;
            var reads = new long[2];
            for (int pass = 0; pass < 2; pass++)
            {
                Scenario.ClearTrace();
                DbElement[] elements = null;
                Exception error = null;
                try { elements = E3d21Context.ResolveObservedForHarness(requested, members, primitives, pass == 0); }
                catch (Exception caught) { error = caught; }
                reads[pass] = Scenario.PrimitiveReferenceReads;
                Check((error == null) == expectedSuccess, label + " expected resolution outcome");
                if (pass == 0)
                {
                    originalTrace = Scenario.Calls.ToArray(); originalElements = elements; originalError = error;
                }
                else
                {
                    Check(Scenario.Calls.SequenceEqual(originalTrace), label + " exact native call trace retained");
                    if (error == null)
                        Check(elements.SequenceEqual(originalElements), label + " exact resolved objects and order retained");
                    else
                        Check(error.GetType() == originalError.GetType() && error.Message == originalError.Message,
                            label + " exact rejection type and message retained");
                }
            }
            return reads;
        }

        private static void IndexedResolutionParityAndWork()
        {
            Scenario.Reset(4);
            CompareResolvers(Refs(1, 3, 2, 4), Refs(1, 4), Refs(2, 3, 2, 1), true, "mixed members/primitives/aliases");
            long[] reads = CompareResolvers(Refs(1, 4), Refs(1, 4), Refs(2, 3, 2), true, "members only");
            Check(reads[0] == 0 && reads[1] == 0, "member-only resolution never traverses primitives");
            reads = CompareResolvers(Refs(), Refs(1, 4), Refs(2, 3), true, "empty clear target");
            Check(reads[0] == 0 && reads[1] == 0 && Scenario.Calls.Count == 0, "empty resolution never builds index or reads native state");
            CompareResolvers(Refs(2, 99), Refs(1, 4), Refs(2, 3), false, "existing undisplayed reference");
            foreach (string invalid in new[] { "missing", "identity", "null", "invalid", "deleted" })
            {
                Scenario.Reset(4);
                if (invalid == "missing") Scenario.Database.Remove("=1/2");
                if (invalid == "identity") Scenario.Overrides["lookup.1/2"] = Scenario.Element(3);
                if (invalid == "null") Scenario.Overrides["element.2.is_null"] = true;
                if (invalid == "invalid") Scenario.Overrides["element.2.is_valid"] = false;
                if (invalid == "deleted") Scenario.Overrides["element.2.is_deleted"] = true;
                reads = CompareResolvers(Refs(2), Refs(1, 4), Refs(2, 3), false, "direct lookup " + invalid);
                Check(reads[0] == 0 && reads[1] == 0, "native identity failure precedes managed membership: " + invalid);
            }

            const int primitiveCount = 20000, requestCount = 32;
            Scenario.Reset(4);
            for (int id = 206; id <= primitiveCount + 1; id++) Scenario.Database.Add("=1/" + id, new DbElement(id));
            string[] primitives = Refs(Enumerable.Range(2, primitiveCount).ToArray());
            reads = CompareResolvers(Refs(2), Refs(1), primitives, true, "single early primitive");
            Check(reads[0] == 1 && reads[1] == 1, "single-target resolution retains early-exit work");
            reads = CompareResolvers(Refs(Enumerable.Range(2, requestCount).ToArray()), Refs(1), primitives, true, "multiple early primitives");
            Check(reads[0] == 528 && reads[1] == requestCount, "early targets stop the shared traversal without indexing the tail");
            reads = CompareResolvers(Refs(Enumerable.Range(2, requestCount).Reverse().ToArray()), Refs(1), primitives, true, "reverse early primitives");
            Check(reads[0] == 528 && reads[1] == requestCount, "previously traversed targets resolve from the index in reverse order");
            string[] requested = Refs(Enumerable.Range(primitiveCount + 2 - requestCount, requestCount).ToArray());
            reads = CompareResolvers(requested, Refs(1), primitives, true, "maximum-size unique snapshot");
            Check(reads[1] == primitiveCount && reads[0] > 30 * reads[1], "one primitive pass replaces repeated large scans");
            Check(Scenario.Calls.Count == requestCount * 5, "all 32 direct lookups and four identity reads retained");
            Console.WriteLine("Selection membership work: P=" + primitiveCount + ", Q=" + requestCount +
                ", original primitive ref reads=" + reads[0] + ", indexed primitive ref reads=" + reads[1] + ", native calls=" + Scenario.Calls.Count);

            primitives = Enumerable.Range(0, primitiveCount).Select(i => "=1/" + (i / 2 + 2)).ToArray();
            requested = Refs(Enumerable.Range(primitiveCount / 2 + 2 - requestCount, requestCount).ToArray());
            reads = CompareResolvers(requested, Refs(1), primitives, true, "maximum-size aliased snapshot");
            Check(reads[1] == primitiveCount - 1 && reads[0] > 30 * reads[1], "aliases collapse in index without rescanning or reading beyond final match");
            Check(Scenario.Calls.Count == requestCount * 5, "aliased display preserves every direct resolution");
        }

        private static void AliasedDisplayNoopAndFreshObservation()
        {
            Scenario.Reset(2, 4);
            Scenario.Element(1).OwnerValue = Scenario.Element(4);
            Scenario.DrawList.MemberValues[1].PrimitiveValues = new[]
            {
                new DrawListPrimitive { Label = "alias.2", ElementValue = Scenario.Element(2), ColourValue = 6 },
                new DrawListPrimitive { Label = "alias.1", ElementValue = Scenario.Element(1) }
            };
            var context = new E3d21Context();
            string revision = Revision(context, 4, 2);
            Scenario.ClearTrace();
            var result = Apply(context, revision, 4, 2);
            Check(Yes(result, "success") && Yes(result, "no_change") && Yes(result, "readback_verified"), "aliases and member/primitive overlap preserve verified no-op");
            Check(Scenario.Writes.Count == 0 && Scenario.Dispatches == 2, "aliased no-op keeps independent readback and avoids writes");
            Check(Scenario.Calls.Count(path => path == "lookup.1/2") == 2 && Scenario.Calls.Count(path => path == "lookup.1/4") == 2,
                "aliased no-op retains both direct resolution passes");
            Check(Scenario.Calls.Count(path => path == "drawlist.members") == 4, "aliased no-op retains all four complete display observations");
            Check(Scenario.Selected.Select(element => element.Id).SequenceEqual(new[] { 2, 4 }), "aliased no-op retains native selection order");

            Scenario.DrawList.MemberValues[0].PrimitiveValues = new[] { Scenario.DrawList.MemberValues[0].PrimitiveValues[1] };
            Scenario.DrawList.MemberValues[1].PrimitiveValues = new[] { Scenario.DrawList.MemberValues[1].PrimitiveValues[1] };
            Scenario.ClearTrace();
            result = Preview(context, 2);
            Check(!Yes(result, "success") && Scenario.Writes.Count == 0, "later request observes removed aliases without stale membership");
        }

        private static void StalePreviews()
        {
            foreach (string change in new[] { "selection", "selection_order", "ce", "view", "drawlist", "project", "mdb", "display", "desired", "instance" })
            {
                Scenario.Reset(4, 1); var context = new E3d21Context(); string revision = Revision(context, 2, 3);
                if (change == "selection") Scenario.Selected = new[] { Scenario.Element(1) };
                if (change == "selection_order") Scenario.Selected = new[] { Scenario.Element(1), Scenario.Element(4) };
                if (change == "ce") Scenario.Ce = "=1/98";
                if (change == "view") Scenario.View.Id++;
                if (change == "drawlist") Scenario.DrawList.Id++;
                if (change == "project") Scenario.Project.NameValue = "OTHER";
                if (change == "mdb") Scenario.Mdb.NameValue = "OTHER";
                if (change == "display") Scenario.DrawList.MemberValues[0].PrimitiveValues[0].ColourValue++;
                if (change == "instance") context = new E3d21Context();
                Scenario.ClearTrace();
                var result = change == "desired" ? Apply(context, revision, 2) : Apply(context, revision, 2, 3);
                Check(!Yes(result, "success") && !Yes(result, "write_attempted") && Scenario.Writes.Count == 0, "stale preview fails before write: " + change);
            }
        }

        private static void IncompleteOriginalAndDisplay()
        {
            foreach (string error in new[] { "null", "large", "null_row", "invalid_row", "deleted_row", "bad_ref", "display_null", "primitive_null" })
            {
                Scenario.Reset(4);
                if (error == "null") Scenario.Selected = null;
                if (error == "large") Scenario.Selected = Enumerable.Range(1, 201).Select(Scenario.Element).ToArray();
                if (error == "null_row") Scenario.Selected = new DbElement[] { Scenario.Element(4), null };
                if (error == "invalid_row") Scenario.Overrides["element.4.is_valid"] = false;
                if (error == "deleted_row") Scenario.Overrides["element.4.is_deleted"] = true;
                if (error == "bad_ref") Scenario.Overrides["element.4.ref"] = new[] { 1 };
                if (error == "display_null") Scenario.DrawList.MemberValues = null;
                if (error == "primitive_null") Scenario.DrawList.MemberValues[0].PrimitiveValues = null;
                var result = Preview(new E3d21Context(), 2);
                Check(!Yes(result, "success") && Scenario.Writes.Count == 0, "incomplete original/display rejects: " + error);
            }
        }

        private static void PartialWritesAndLateVerification()
        {
            foreach (string failure in new[] { "write.clear", "write.add", "write.notify", "write.update" })
            {
                Scenario.Reset(4); var context = new E3d21Context(); string revision = Revision(context, 2, 3);
                Scenario.ClearTrace(); Scenario.Overrides[failure] = new InvalidOperationException("Failed after mutation: " + failure);
                var result = Apply(context, revision, 2, 3);
                Check(!Yes(result, "success") && Yes(result, "write_attempted") && Yes(result, "outcome_uncertain"), "partial write is uncertain: " + failure);
                Check(Scenario.Calls.Last() == failure && Scenario.Dispatches == 1, "exception stops SDK continuation and later verification: " + failure);
                Check(Scenario.Writes.Last() == failure && Scenario.Writes.Count == new[] { "write.clear", "write.add", "write.notify", "write.update" }.ToList().IndexOf(failure) + 1, "no replay/rollback after partial: " + failure);
            }
            foreach (string change in new[] { "selection", "ce", "view", "drawlist", "display", "read_error", "project", "mdb" })
            {
                Scenario.Reset(4); var context = new E3d21Context(); string revision = Revision(context, 2, 3);
                Scenario.ClearTrace();
                Scenario.BeforeDispatch = number =>
                {
                    if (number != 2) return;
                    if (change == "selection") Scenario.Selected = new[] { Scenario.Element(1) };
                    if (change == "ce") Scenario.Ce = "=1/98";
                    if (change == "view") Scenario.View = new View3D { Id = 8 };
                    if (change == "drawlist") Scenario.DrawList.Id = 12;
                    if (change == "display") Scenario.DrawList.MemberValues[0].PrimitiveValues[0].ColourValue++;
                    if (change == "read_error") Scenario.Overrides["drawlist.selection"] = new InvalidOperationException("Later read unavailable");
                    if (change == "project") Scenario.Project.NameValue = "OTHER";
                    if (change == "mdb") Scenario.Mdb.NameValue = "OTHER";
                };
                var result = Apply(context, revision, 2, 3);
                Check(!Yes(result, "success") && !Yes(result, "readback_verified"), "independent readback mismatch/error cannot succeed: " + change);
                Check(Scenario.Writes.Count == 4 && Scenario.Dispatches == 2, "late mismatch never retries/rolls back: " + change);
            }
        }

        private static void WithinCallbackChangesAndDelayedSelection()
        {
            foreach (string change in new[] { "selection", "display" })
            {
                Scenario.Reset(4); var context = new E3d21Context(); string revision = Revision(context, 2, 3);
                Scenario.ClearTrace(); int lookups = 0;
                Scenario.OnNative = path =>
                {
                    if (path != "lookup.1/2" || ++lookups != 2) return;
                    if (change == "selection") Scenario.Selected = new[] { Scenario.Element(1) };
                    else Scenario.DrawList.MemberValues[0].PrimitiveValues[0].ColourValue++;
                };
                var result = Apply(context, revision, 2, 3);
                Check(!Yes(result, "success") && Yes(result, "revision_conflict") && Scenario.Writes.Count == 0,
                    "changes during second direct resolution caught by complete revalidation: " + change);
            }
            foreach (string change in new[] { "ce", "view", "drawlist" })
            {
                Scenario.Reset(4); var context = new E3d21Context(); string revision = Revision(context, 2, 3);
                Scenario.ClearTrace();
                Scenario.OnNative = path =>
                {
                    if (path != "write.clear") return;
                    if (change == "ce") Scenario.Ce = "=1/98";
                    if (change == "view") Scenario.View = new View3D { Id = 8 };
                    if (change == "drawlist") Scenario.DrawList.Id = 12;
                };
                var result = Apply(context, revision, 2, 3);
                Check(!Yes(result, "success") && Yes(result, "outcome_uncertain") && Scenario.Writes.SequenceEqual(new[] { "write.clear" }),
                    "context changed after clear blocks add/notification/update: " + change);
                Check(Scenario.Dispatches == 1, "context change never starts verification dispatch: " + change);
            }

            Scenario.Reset(4); var delayed = new E3d21Context(); string token = Revision(delayed, 2, 3);
            Scenario.ClearTrace();
            Scenario.OnNative = path => { if (path == "write.update") Scenario.Selected = new[] { Scenario.Element(4) }; };
            Scenario.BeforeDispatch = number => { if (number == 2) Scenario.Selected = new[] { Scenario.Element(3), Scenario.Element(2) }; };
            var confirmed = Apply(delayed, token, 2, 3);
            Check(Yes(confirmed, "success") && Yes(confirmed, "readback_verified") && !Yes(confirmed, "selection_order_matches"),
                "later exact set can confirm pending immediate selection without requiring requested order");
            Check(((string[])Dict(confirmed["immediate_readback"])["refnos"]).SequenceEqual(Refs(4)), "receipt retains earlier mismatching readback");
            Check(Scenario.Writes.Count == 4 && Scenario.Dispatches == 2, "pending selection never replays write");

            Scenario.Reset(4); delayed = new E3d21Context(); token = Revision(delayed, 2, 3);
            Scenario.ClearTrace();
            Scenario.OnNative = path => { if (path == "write.update") Scenario.DrawList.MemberValues[0].ColourValue++; };
            var changedDisplay = Apply(delayed, token, 2, 3);
            Check(!Yes(changedDisplay, "success") && Equals(changedDisplay["display_unchanged"], false), "immediate display change is not confirmed");
            Check(Scenario.Dispatches == 1 && Scenario.Writes.Count == 4, "immediate display mismatch stops subsequent readback without rollback");

            Scenario.Reset(4); Scenario.ExternalBusy = true;
            var busy = Preview(new E3d21Context(), 2);
            Check(!Yes(busy, "success") && Scenario.Calls.Count == 0 && Scenario.Dispatches == 0, "busy gate rejects before any SDK dispatch");
        }

        private static void FaultAtEveryNativeBoundary()
        {
            Scenario.Reset(4); var baseline = new E3d21Context(); string revision = Revision(baseline, 2, 3);
            Scenario.ClearTrace(); Check(Yes(Apply(baseline, revision, 2, 3), "success"), "fault enumeration baseline");
            int boundaries = Scenario.Calls.Count;
            Check(boundaries > 20, "exercise substantial real native-read flow");
            for (int at = 1; at <= boundaries; at++)
            {
                Scenario.Reset(4); var context = new E3d21Context(); revision = Revision(context, 2, 3);
                Scenario.ClearTrace(); int wanted = at;
                Scenario.OnNative = path => { if (Scenario.Calls.Count == wanted) context.Fault(); };
                var result = Apply(context, revision, 2, 3);
                Check(!Yes(result, "success") && !Yes(result, "readback_verified"), "native fault never succeeds at " + at);
                Check(Scenario.Calls.Count == at, "zero SDK calls after native fault at " + at + ": " + string.Join(", ", Scenario.Calls.Skip(Math.Max(0, at - 2))));
            }
        }

        private static void HttpRoute()
        {
            var context = new SelectionRouteContext();
            var response = JObject.FromObject(SelectionProductionRoute.Invoke(new SelectionRequest("{\"refnos\":[\"=1/2\",\"=1/3\"]}"), context).GetAwaiter().GetResult());
            Check(context.Calls == 1 && context.DryRun && context.ExpectedRevision == null && context.Refs.SequenceEqual(Refs(2, 3)), "HTTP preview defaults and refs");
            Check((string)response["session_id"] == "SELECTION-SESSION" && (bool)response["dry_run"] && !(bool)response["result"]["success"], "HTTP preserves session and negative result without relabeling");
            context = new SelectionRouteContext();
            string revision = new string('a', 64);
            SelectionProductionRoute.Invoke(new SelectionRequest("{\"refnos\":[],\"dry_run\":false,\"expected_revision\":\"" + revision + "\"}"), context).GetAwaiter().GetResult();
            Check(context.Calls == 1 && !context.DryRun && context.Refs.Count == 0 && context.ExpectedRevision == revision, "HTTP explicit clear execution");
            var invalid = new List<string>
            {
                "{}", "{\"refnos\":null}", "{\"refnos\":\"=1/2\"}", "{\"refnos\":[1]}", "{\"refnos\":[null]}",
                "{\"refnos\":[],\"unknown\":true}", "{\"refnos\":[],\"dry_run\":null}", "{\"refnos\":[],\"dry_run\":\"false\"}",
                "{\"refnos\":[],\"expected_revision\":null}", "{\"refnos\":[],\"expected_revision\":3}",
                "{\"refnos\":[],\"Refnos\":[]}", "{\"refnos\":[],\"refnos\":[]}",
                "{\"refnos\":[],\"dry_run\":true,\"dry_run\":false}",
                "{\"refnos\":[" + string.Join(",", Enumerable.Repeat("\"=1/2\"", 33)) + "]}"
            };
            foreach (string body in invalid)
            {
                context = new SelectionRouteContext();
                Throws<ArgumentException>(() => SelectionProductionRoute.Invoke(new SelectionRequest(body), context).GetAwaiter().GetResult(), "HTTP invalid JSON parameter rejected");
                Check(context.Calls == 0, "HTTP invalid before native context dispatch");
            }
            context = new SelectionRouteContext();
            Throws<ArgumentException>(() => SelectionProductionRoute.Invoke(new SelectionRequest("{\"refnos\":[]}", new NameValueCollection { { "dry_run", "false" } }), context).GetAwaiter().GetResult(), "HTTP forbids query parameters");
            Check(context.Calls == 0, "HTTP query rejected before context");
        }

        public static void Main()
        {
            try
            {
                PreviewAndValidation(); ReplaceClearAndNoop(); IndexedResolutionParityAndWork(); AliasedDisplayNoopAndFreshObservation();
                StalePreviews(); IncompleteOriginalAndDisplay();
                PartialWritesAndLateVerification(); WithinCallbackChangesAndDelayedSelection(); HttpRoute(); FaultAtEveryNativeBoundary();
                Console.WriteLine("Selection editing production checks passed: " + checks);
            }
            catch (Exception error)
            {
                Console.Error.WriteLine(error);
                Environment.ExitCode = 1;
            }
        }
    }
}
