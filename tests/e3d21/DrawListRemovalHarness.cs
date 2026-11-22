using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Aveva.Core.Database;
using Aveva.Core3D.Graphics;

namespace Aveva.Core.Database
{
    public enum DbType { Design, Catalogue }
    public enum DbNounField { TOPF }
    public sealed class DbAttributeInstance { public static readonly DbAttributeInstance NAME = new DbAttributeInstance(); }
    public sealed class DbElementType
    {
        internal string Value;
        internal bool Topf, Valid = true;
        public string ShortName { get { return Scenario.Read("type.short_name", Value); } }
        public string Name { get { return Scenario.Read("type.name", Value); } }
        public bool IsWorld { get { return Scenario.Read("type.is_world", Value == "WORL"); } }
        public bool IsValid { get { return Scenario.Read("type.valid", Valid); } }
        public bool GetBool(DbNounField field) { return Scenario.Read("type.topf", Topf); }
    }
    public sealed class DbElement
    {
        internal int Id;
        internal bool Deleted, Invalid;
        internal string NameValue;
        internal DbElementType TypeValue;
        internal DbElement OwnerValue;
        internal DbType DatabaseType = DbType.Design;
        internal DbElement(int id, string type, bool topf) { Id = id; NameValue = "/E" + id; TypeValue = new DbElementType { Value = type, Topf = topf }; }
        public bool IsNull { get { return Scenario.Read("element." + Id + ".is_null", false); } }
        public bool IsValid { get { return Scenario.Read("element." + Id + ".is_valid", !Invalid); } }
        public bool IsDeleted { get { return Scenario.Read("element." + Id + ".is_deleted", Deleted); } }
        public int[] Ref { get { return Scenario.Read("element." + Id + ".ref", new[] { 1, Id }); } }
        public DbElement Owner { get { return Scenario.Read("element." + Id + ".owner", OwnerValue); } }
        public DbType DbType { get { return Scenario.Read("element." + Id + ".db_type", DatabaseType); } }
        public DbElementType GetElementType() { return Scenario.Read("element." + Id + ".type", TypeValue); }
        public DbElementType GetActualType() { return Scenario.Read("element." + Id + ".actual_type", TypeValue); }
        public string GetAsString(DbAttributeInstance attribute) { return Scenario.Read("element." + Id + ".name", NameValue); }
    }
    public sealed class Project
    {
        internal string NameValue = "APS";
        public static Project CurrentProject { get { return Scenario.Read("project.current", Scenario.Project); } }
        public string Name { get { return Scenario.Read("project.name", NameValue); } }
    }
    public sealed class MDB
    {
        internal string NameValue = "ALL";
        public static MDB CurrentMDB { get { return Scenario.Read("mdb.current", Scenario.Mdb); } }
        public string Name { get { return Scenario.Read("mdb.name", NameValue); } }
    }
}
namespace Aveva.Core3D.Graphics
{
    public sealed class View3D
    {
        internal int Id = 7;
        internal double[] Centre = { 10, 20, 30 }, Direction = { 1, 1, -1 };
        internal double Radius = 2000, Twist;
        internal bool Perspective, Shaded = true;
        public int ViewId { get { return Scenario.Read("view.id", Id); } }
        public bool IsValid() { return Scenario.Read("view.valid", true); }
        public double[] GetThruPoint() { return Scenario.Read("camera.centre", Centre.ToArray()); }
        public double[] GetDirection() { return Scenario.Read("camera.direction", Direction.ToArray()); }
        public double GetRadius() { return Scenario.Read("camera.radius", Radius); }
        public double GetExtraTwistDegrees() { return Scenario.Read("camera.twist", Twist); }
        public bool PerspectiveMode { get { return Scenario.Read("camera.perspective", Perspective); } }
        public bool ShadeMode { get { return Scenario.Read("camera.shade", Shaded); } }
    }
    public class VisualFields
    {
        internal int ColourValue = 326, TransparencyValue;
        internal bool EdgesValue, VisibilityValue = true, ShallowValue;
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
        public DrawListMember GetDrawListMember(DbElement element)
        { return Scenario.Read("drawlist.resolve", MemberValues.FirstOrDefault(member => member.ElementValue == element)); }
        public void Remove(DbElement element)
        {
            Scenario.Removes++;
            if (!Scenario.SuppressRemoval) MemberValues = MemberValues.Where(member => member.ElementValue != element).ToArray();
            if (Scenario.AfterRemove != null) Scenario.AfterRemove();
            Scenario.Read("write.remove", true);
        }
    }
    public sealed class DrawListManager
    {
        public static DrawListManager Instance { get { return Scenario.Read("manager.instance", Scenario.Manager); } }
        public DrawList CurrentDrawList { get { return Scenario.Read("manager.current", Scenario.DrawList); } }
        public DrawList getViewDrawList(int view) { return Scenario.Read("manager.for_view", Scenario.DrawList); }
        public void UpdateGraphics()
        { Scenario.Refreshes++; if (Scenario.AfterRefresh != null) Scenario.AfterRefresh(); Scenario.Read("write.refresh", true); }
    }
}
internal static class Scenario
{
    internal static readonly Dictionary<string, DbElement> Database = new Dictionary<string, DbElement>();
    internal static readonly Dictionary<string, object> Overrides = new Dictionary<string, object>();
    internal static readonly List<string> Calls = new List<string>();
    internal static Action<string> OnNative;
    internal static Action AfterRemove, AfterRefresh;
    internal static bool SuppressRemoval, Busy, PersistFailure;
    internal static int Removes, Refreshes, Persists, NativeAfterFault;
    internal static E3d21AiBridge.E3d21Context Context;
    internal static View3D View;
    internal static DrawList DrawList;
    internal static DrawListManager Manager;
    internal static Project Project;
    internal static MDB Mdb;
    internal static DbElement[] Selected;
    internal static string Ce;
    internal static object Persisted;
    internal static bool PersistedAllowEmpty;
    internal static T Read<T>(string key, T fallback)
    {
        if (Context != null && Context.Faulted) NativeAfterFault++;
        Calls.Add(key);
        if (OnNative != null) OnNative(key);
        object value;
        if (!Overrides.TryGetValue(key, out value)) return fallback;
        if (value is Exception) throw (Exception)value;
        return (T)value;
    }
    internal static DbElement E(int id) { return Database["=1/" + id]; }
    internal static DrawListMember Member(int id, params int[] children)
    {
        return new DrawListMember { Label = "member." + id, ElementValue = E(id),
            PrimitiveValues = children.Select(child => new DrawListPrimitive { Label = "primitive." + child, ElementValue = E(child), ColourValue = 0 }).ToArray() };
    }
    internal static E3d21AiBridge.E3d21Context Reset()
    {
        Database.Clear(); Overrides.Clear(); Calls.Clear();
        Database.Add("=1/0", new DbElement(0, "WORL", false));
        for (int id = 1; id <= 8; id++) Database.Add("=1/" + id, new DbElement(id, id == 2 || id == 3 || id == 5 ? "CYLI" : "EQUI", id != 2 && id != 3 && id != 5));
        foreach (DbElement element in Database.Values.Where(e => e.Id > 0)) element.OwnerValue = E(0);
        E(2).OwnerValue = E(1); E(3).OwnerValue = E(1); E(5).OwnerValue = E(4);
        DrawList = new DrawList { MemberValues = new[] { Member(1, 2, 3), Member(4, 5) } };
        View = new View3D(); Manager = new DrawListManager(); Project = new Project(); Mdb = new MDB();
        Selected = new DbElement[0]; Ce = "=1/8";
        Removes = Refreshes = Persists = NativeAfterFault = 0; SuppressRemoval = Busy = PersistFailure = false;
        OnNative = null; AfterRemove = AfterRefresh = null; Persisted = null; PersistedAllowEmpty = false;
        Context = new E3d21AiBridge.E3d21Context();
        return Context;
    }
    internal static void TraceReset() { Calls.Clear(); OnNative = null; NativeAfterFault = Removes = Refreshes = Persists = 0; }
}
namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        private const int DisplayStateMemberLimit = 10000, DisplayStatePrimitiveLimit = 20000, DisplayStateOwnerDepthLimit = 1024;
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;
        private readonly Action<Action> invokeOnUi = action => action();
        private readonly Func<Dictionary<string, object>> executionState = () => new Dictionary<string, object> { ["native_operation_active"] = Scenario.Busy };
        private readonly object commandLock = new object();
        private bool commandActive;
        private string activeCommand = "";
        internal bool Faulted { get { return nativeFaultState.HasFault; } }
        internal void Fault() { nativeFaultState.Observe("Unhandled Database error 538 from OfflineRemoval", "offline"); }
        private static object CurrentView3D() { return Scenario.Read("current.view", Scenario.View); }
        private static string ModelCurrentReference() { return NativeRead(() => Scenario.Read("ce", Scenario.Ce)); }
        private static string ModelReference(int[] value)
        { if (value == null || value.Length != 2 || value[0] <= 0 || value[1] < 0) throw new InvalidOperationException("bad ref"); return "=" + value[0] + "/" + value[1]; }
        private static string ElementRef(DbElement element) { return ModelReference(NativeRead(() => element.Ref)); }
        private static DbElement ModelRequireElement(string reference)
        {
            DbElement element = reference.StartsWith("/") ? Scenario.Database.Values.FirstOrDefault(e => e.NameValue == reference) : (Scenario.Database.ContainsKey(reference) ? Scenario.Database[reference] : null);
            element = NativeRead(() => Scenario.Read("lookup." + reference, element));
            if (!IsUsable(element)) throw new InvalidOperationException("missing element");
            return element;
        }
        private sealed class DrawListVisualEntry { public string refno; public int colour, transparency; public bool edges, visibility, shallow; }
        private sealed class DrawListPrimitiveVisualEntry { public string owner_refno, refno; public int colour, transparency; public bool edges, visibility, shallow; }
        private sealed class PrimitiveVisualMap
        {
            internal readonly Dictionary<string, DrawListVisualEntry> Members = new Dictionary<string, DrawListVisualEntry>(StringComparer.Ordinal);
            internal readonly Dictionary<string, DrawListPrimitiveVisualEntry> Entries = new Dictionary<string, DrawListPrimitiveVisualEntry>(StringComparer.Ordinal);
            internal readonly Dictionary<string, DrawListPrimitive> Wrappers = new Dictionary<string, DrawListPrimitive>(StringComparer.Ordinal);
            internal readonly HashSet<string> Owners = new HashSet<string>(StringComparer.Ordinal);
            internal readonly List<string> Errors = new List<string>();
        }
        private sealed class SelectionEditingState
        {
            internal View3D View; internal DrawList DrawList; internal int ViewId, DrawListId;
            internal string Project, Mdb, Ce, DisplayFingerprint; internal string[] References; internal PrimitiveVisualMap Display;
        }
        private sealed class DrawListVisualDocument
        {
            public int schema_version; public string project, mdb;
            public List<DrawListVisualEntry> members = new List<DrawListVisualEntry>();
            public List<DrawListPrimitiveVisualEntry> primitive_entries = new List<DrawListPrimitiveVisualEntry>();
        }
        private static Dictionary<string, object> DrawListPersistenceResult(string status, string error = null)
        { return new Dictionary<string, object> { ["attempted"] = false, ["succeeded"] = null, ["status"] = status, ["error"] = error }; }
        private static Dictionary<string, object> PersistDrawListVisualDocument(Dictionary<string, object> result, DrawListVisualDocument document, bool allowEmpty = false)
        {
            Scenario.Persists++; Scenario.Persisted = document; Scenario.PersistedAllowEmpty = allowEmpty;
            result["attempted"] = true; result["succeeded"] = !Scenario.PersistFailure;
            result["status"] = Scenario.PersistFailure ? "save_failed" : "saved"; return result;
        }
        internal static int SavedMembers { get { return ((DrawListVisualDocument)Scenario.Persisted).members.Count; } }
        // PRODUCTION_METHODS
    }

    internal static class DrawListRemovalHarness
    {
        private static int checks, scenarios, faults;
        private static void Check(bool condition, string name) { checks++; if (!condition) throw new Exception(name); }
        private static Dictionary<string, object> D(object value) { return (Dictionary<string, object>)value; }
        private static bool Is(Dictionary<string, object> value, string key, object expected) { object actual; return value.TryGetValue(key, out actual) && Equals(actual, expected); }
        private static string Preview(E3d21Context context, string target = "=1/1")
        { var result = D(context.RemoveDrawListMember(target, null, true)); Check(Is(result, "success", true), "preview succeeded"); return (string)result["revision"]; }
        private static Dictionary<string, object> Execute(E3d21Context context, string revision, string target = "=1/1")
        { return D(context.RemoveDrawListMember(target, revision, false)); }
        private static void Run(string name, Action action) { scenarios++; action(); Console.WriteLine("PASS " + name); }
        private static void Rejected(string name, Action arrange, string target = "=1/1")
        {
            var context = Scenario.Reset(); arrange();
            var result = D(context.RemoveDrawListMember(target, null, true));
            Check(Is(result, "success", false) && Is(result, "remove_attempted", false), name + " rejected");
            Check(Scenario.Removes == 0 && Scenario.Refreshes == 0 && Scenario.Persists == 0, name + " zero effects");
        }
        private static void Success()
        {
            var context = Scenario.Reset(); Scenario.Selected = new[] { Scenario.E(5) };
            string revision = Preview(context);
            Check(Scenario.Removes == 0 && Scenario.Persists == 0, "preview has no effects");
            var result = Execute(context, revision);
            foreach (string key in new[] { "success", "readback_verified", "membership_verified", "remaining_members_verified", "remaining_primitives_verified", "target_identity_verified", "selection_unchanged", "camera_unchanged", "ce_unchanged", "removed", "write_applied" }) Check(Is(result, key, true), key);
            Check(Scenario.Removes == 1 && Scenario.Refreshes == 1 && Scenario.Persists == 1, "single remove refresh and persistence");
            Check(Scenario.DrawList.MemberValues.Length == 1 && Scenario.DrawList.MemberValues[0].ElementValue == Scenario.E(4), "only exact member gone");
            Check(Scenario.Database.Count == 9 && !Scenario.E(1).Deleted && Scenario.E(2).OwnerValue == Scenario.E(1), "database untouched");
            Check(Is(result, "rollback_attempted", false) && Is(result, "outcome_uncertain", false), "no rollback or uncertainty");
            Scenario.TraceReset(); var absent = Execute(context, Preview(context));
            Check(Is(absent, "success", true) && Is(absent, "no_change", true) && Is(absent, "removed", false), "absent no-op confirmed");
            Check(Scenario.Removes == 0 && Scenario.Refreshes == 0 && Scenario.Persists == 0, "no-op does not mutate or save");
        }
        private static void EmptyAndPersistence()
        {
            var context = Scenario.Reset(); Scenario.DrawList.MemberValues = new[] { Scenario.Member(1, 2, 3) };
            var result = Execute(context, Preview(context));
            Check(Is(result, "success", true) && E3d21Context.SavedMembers == 0 && Scenario.PersistedAllowEmpty, "verified empty map explicitly persisted");
            context = Scenario.Reset(); Scenario.PersistFailure = true; result = Execute(context, Preview(context));
            Check(Is(result, "success", true) && Is(D(result["persistence"]), "succeeded", false), "save failure separate from removal");
            Check(Scenario.Removes == 1 && Scenario.Persists == 1 && Is(result, "rollback_attempted", false), "save failure does not replay");
        }
        private static void InputAndScope()
        {
            var context = Scenario.Reset();
            foreach (var action in new Action[] { () => context.RemoveDrawListMember("", null, true), () => context.RemoveDrawListMember("CE", null, true),
                () => context.RemoveDrawListMember("=1/1", null, false), () => context.RemoveDrawListMember("=1/1", "z" + new string('a', 63), false) })
            { bool rejected = false; try { action(); } catch (ArgumentException) { rejected = true; } Check(rejected, "invalid input rejected"); }
            Check(Scenario.Calls.Count == 0, "invalid inputs before SDK");
            Rejected("non TOPF", () => { }, "=1/2");
            Rejected("non Design", () => Scenario.E(1).DatabaseType = DbType.Catalogue);
            Rejected("missing target", () => Scenario.Database.Remove("=1/1"));
            Rejected("lookup mismatch", () => Scenario.Overrides["drawlist.resolve"] = Scenario.DrawList.MemberValues[1]);
            Rejected("displayed descendant", () => { Scenario.E(4).OwnerValue = Scenario.E(1); });
            Rejected("displayed ancestor", () => { Scenario.E(1).OwnerValue = Scenario.E(4); });
            foreach (int id in new[] { 0, 1, 2 }) Rejected("selected overlap " + id, () => Scenario.Selected = new[] { Scenario.E(id) });
            Rejected("null selection", () => Scenario.Selected = null);
            Rejected("selection >200", () => Scenario.Selected = Enumerable.Repeat(Scenario.E(5), 201).ToArray());
            Rejected("duplicate selection", () => Scenario.Selected = new[] { Scenario.E(5), Scenario.E(5) });
            Rejected("owner cycle", () => Scenario.E(1).OwnerValue = Scenario.E(1));
            Rejected("incomplete map", () => Scenario.Overrides["primitive.2.visibility"] = new InvalidOperationException("unreadable"));
            Rejected("duplicate member", () => Scenario.DrawList.MemberValues = new[] { Scenario.Member(1, 2), Scenario.Member(1, 3) });
            Rejected("invalid camera", () => Scenario.View.Radius = double.NaN);
            Rejected("busy", () => Scenario.Busy = true);
        }
        private static void NumericLookupIdentity()
        {
            // A corrupt native resolver can return another valid TOPF member.
            // The target's own well-formed identity is insufficient evidence
            // that it matches the explicit requested numeric reference.
            Rejected("wrong numeric native lookup", () => Scenario.Overrides["lookup.=1/1"] = Scenario.E(4));
            Rejected("wrong zero-prefixed numeric native lookup", () => Scenario.Overrides["lookup.=01/0001"] = Scenario.E(4), "=01/0001");
            var context = Scenario.Reset();
            Scenario.Overrides["lookup.=01/0001"] = Scenario.E(1);
            string padded = Preview(context, "=01/0001");
            string canonical = Preview(context, "=1/1");
            Check(padded != canonical, "revision binds literal request despite equal canonical identity");
            var mismatched = Execute(context, padded, "=1/1");
            Check(Is(mismatched, "success", false) && Is(mismatched, "revision_conflict", true) && Scenario.Removes == 0,
                "zero-prefixed revision cannot authorize canonical spelling");
            var result = Execute(context, padded, "=01/0001");
            Check(Is(result, "success", true) && Is(result, "refno", "=1/1") && Is(result, "requested_refno", "=01/0001"),
                "zero-prefixed request resolves canonical identity and succeeds");
            Check(Scenario.Removes == 1 && Scenario.DrawList.MemberValues.Single().ElementValue == Scenario.E(4),
                "zero-prefixed request removes only exact intended member");

            context = Scenario.Reset(); string token = Preview(context);
            Scenario.Overrides["lookup.=1/1"] = Scenario.E(4);
            result = Execute(context, token);
            Check(Is(result, "success", false) && Is(result, "remove_attempted", false) && Scenario.Removes == 0,
                "wrong lookup at execution rejected before any removal");
        }
        private static void Stale()
        {
            foreach (string change in new[] { "colour", "primitive", "selection", "camera", "ce", "project", "mdb", "name", "owner", "type", "absent", "request", "session" })
            {
                var context = Scenario.Reset(); string revision = Preview(context); string target = "=1/1";
                if (change == "colour") Scenario.DrawList.MemberValues[1].ColourValue++;
                if (change == "primitive") Scenario.DrawList.MemberValues[1].PrimitiveValues[0].ShallowValue = true;
                if (change == "selection") Scenario.Selected = new[] { Scenario.E(5) };
                if (change == "camera") Scenario.View.Radius++;
                if (change == "ce") Scenario.Ce = "=1/7";
                if (change == "project") Scenario.Project.NameValue = "OTHER";
                if (change == "mdb") Scenario.Mdb.NameValue = "OTHER";
                if (change == "name") Scenario.E(1).NameValue = "/CHANGED";
                if (change == "owner") Scenario.E(1).OwnerValue = Scenario.E(7);
                if (change == "type") Scenario.E(1).TypeValue.Value = "SUBE";
                if (change == "absent") Scenario.DrawList.MemberValues = new[] { Scenario.Member(4, 5) };
                if (change == "request") target = "/E1";
                if (change == "session") { context = new E3d21Context(); Scenario.Context = context; }
                var result = Execute(context, revision, target);
                Check(Is(result, "success", false) && Is(result, "revision_conflict", true) && Scenario.Removes == 0 && Scenario.Persists == 0, "stale " + change);
            }
            var current = Scenario.Reset(); string token = Preview(current); Scenario.TraceReset(); int cameraReads = 0;
            Scenario.OnNative = key => { if (key == "camera.radius" && ++cameraReads == 2) Scenario.Overrides[key] = Scenario.View.Radius + 1; };
            var drift = Execute(current, token);
            Check(Is(drift, "revision_conflict", true) && Scenario.Removes == 0, "same-dispatch drift rejects");
        }
        private static void OrdinaryFailures()
        {
            foreach (string mode in new[] { "remove throws", "refresh throws", "remove no-op", "sibling colour", "sibling missing", "primitive colour", "extra member", "ce", "camera", "selection", "identity", "residual unreadable" })
            {
                var context = Scenario.Reset(); string revision = Preview(context);
                if (mode == "remove throws") Scenario.Overrides["write.remove"] = new InvalidOperationException("mutated then threw");
                if (mode == "refresh throws") Scenario.Overrides["write.refresh"] = new InvalidOperationException("refresh threw");
                if (mode == "remove no-op") Scenario.SuppressRemoval = true;
                Scenario.AfterRemove = () =>
                {
                    if (mode == "sibling colour") Scenario.DrawList.MemberValues[0].ColourValue++;
                    if (mode == "sibling missing") Scenario.DrawList.MemberValues = new DrawListMember[0];
                    if (mode == "primitive colour") Scenario.DrawList.MemberValues[0].PrimitiveValues[0].ColourValue++;
                    if (mode == "extra member") Scenario.DrawList.MemberValues = Scenario.DrawList.MemberValues.Concat(new[] { Scenario.Member(7) }).ToArray();
                    if (mode == "ce") Scenario.Ce = "=1/7";
                    if (mode == "camera") Scenario.View.Radius++;
                    if (mode == "selection") Scenario.Selected = new[] { Scenario.E(5) };
                    if (mode == "identity") Scenario.E(1).NameValue = "/DIFFERENT";
                    if (mode == "residual unreadable") Scenario.Overrides["drawlist.members"] = new InvalidOperationException("unreadable residual");
                };
                var result = Execute(context, revision);
                Check(Is(result, "success", false) && Scenario.Removes == 1 && Scenario.Persists == 0 && Is(result, "rollback_attempted", false), mode + " fails without replay/save");
                if (mode == "residual unreadable") Check(Is(result, "residual_complete", false) && Is(result, "outcome_uncertain", true) && result["removed"] == null, "unreadable residual honest");
                else if (mode == "identity") Check(Is(result, "outcome_uncertain", true) && Is(result, "target_identity_verified", false), "identity uncertainty");
                else Check(Is(result, "residual_complete", true) && Is(result, "outcome_uncertain", false), mode + " complete known residual");
                if (mode == "remove no-op") Check(Is(result, "partial_write", false) && Is(result, "original_observed_state_restored", true), "ordinary no-op known unchanged");
                if (mode == "remove throws") Check(Is(result, "removed", true) && Is(result, "partial_write", true) && Scenario.Refreshes == 0, "mutate-throw removed visible but not success");
            }
        }
        private static void FaultMatrix()
        {
            var context = Scenario.Reset(); string token = Preview(context); Scenario.TraceReset();
            var success = Execute(context, token); Check(Is(success, "success", true), "fault matrix baseline");
            int boundaries = Scenario.Calls.Count;
            for (int index = 1; index <= boundaries; index++) foreach (bool throws in new[] { false, true })
            {
                context = Scenario.Reset(); token = Preview(context); Scenario.TraceReset(); int at = index;
                Scenario.OnNative = key => { if (Scenario.Calls.Count == at) { context.Fault(); if (throws) throw new InvalidOperationException("native callback then throw"); } };
                var result = Execute(context, token); faults++;
                Check(Is(result, "success", false) && result["native_database_fault"] != null, "fault receipt " + index);
                Check(Scenario.Calls.Count == index && Scenario.NativeAfterFault == 0, "fault stops all SDK " + index);
                Check(Scenario.Removes <= 1 && Scenario.Persists == 0 && Is(result, "rollback_attempted", false), "fault no retry/persist " + index);
                if (Scenario.Removes == 1) Check(Is(result, "outcome_uncertain", true) && result["removed"] == null, "post-remove uncertainty " + index);
                int calls = Scenario.Calls.Count;
                var later = D(context.RemoveDrawListMember("=1/1", null, true));
                Check(Is(later, "success", false) && Scenario.Calls.Count == calls, "latched future call blocked " + index);
            }
            Console.WriteLine("FAULT_BOUNDARIES=" + boundaries);
        }
        private static int Main()
        {
            try
            {
                Run("success preview absence and exact remaining maps", Success);
                Run("empty display and persistence failure", EmptyAndPersistence);
                Run("input scope and complete-observation rejection", InputAndScope);
                Run("numeric lookup identity and literal revision binding", NumericLookupIdentity);
                Run("revision context and same-dispatch revalidation", Stale);
                Run("ordinary failures and residual honesty", OrdinaryFailures);
                Run("native fault stop matrix", FaultMatrix);
                Console.WriteLine("Drawlist removal production checks passed: " + checks + "; scenarios: " + scenarios + "; native faults: " + faults);
                return 0;
            }
            catch (Exception error) { Console.Error.WriteLine(error); return 1; }
        }
    }
}
