// SDK substitutes adapted from DrawListRemovalHarness; no AVEVA assemblies are loaded.
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
        internal int Width = 1280, Height = 960;
        internal int AttachedId = 11;
        public int ViewId { get { return Scenario.Read("view.id", Id); } }
        public bool IsValid() { return Scenario.Read("view.valid", true); }
        public double[] GetThruPoint() { return Scenario.Read("camera.centre", Centre.ToArray()); }
        public double[] GetDirection() { return Scenario.Read("camera.direction", Direction.ToArray()); }
        public double GetRadius() { return Scenario.Read("camera.radius", Radius); }
        public double GetExtraTwistDegrees() { return Scenario.Read("camera.twist", Twist); }
        public bool PerspectiveMode { get { return Scenario.Read("camera.perspective", Perspective); } }
        public bool ShadeMode { get { return Scenario.Read("camera.shade", Shaded); } }
        public int GetWindowWidth() { return Scenario.Read("camera.width", Width); }
        public int GetWindowHeight() { return Scenario.Read("camera.height", Height); }
        public int GetAttachedDrawList() { return Scenario.Read("view.attached", AttachedId); }
        public bool SetCentreAndDirNoMapUpdate(double[] centre, double[] direction)
        {
            return Scenario.Write("write.centre_direction", () =>
            {
                if (!Scenario.RejectCentre) { Centre = centre.ToArray(); Direction = direction.ToArray(); }
            }, !Scenario.RejectCentre);
        }
        public void SetRadius(double value) { Scenario.Write("write.radius", () => Radius = value, true); }
        public void SetPerspectiveModeNoMapUpdate(bool value) { Scenario.Write("write.perspective", () => Perspective = value, true); }
        public void SetExtraTwistDegrees(double value) { Scenario.Write("write.twist", () => Twist = value, true); }
        public void MapView() { Scenario.Maps++; Scenario.Write("write.map", () => { }, true); }
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
    internal static readonly List<string> Writes = new List<string>();
    internal static Action<string> OnNative;
    internal static Action<string> AfterWrite;
    internal static Action AfterRemove, AfterRefresh;
    internal static bool SuppressRemoval, Busy, PersistFailure;
    internal static int Removes, Refreshes, Persists, NativeAfterFault;
    internal static int Maps;
    internal static bool RejectCentre, FloatCamera;
    internal static string ThrowBeforeWrite, ThrowAfterWrite;
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
    internal static T Write<T>(string key, Action mutation, T value)
    {
        Writes.Add(key);
        if (key == ThrowBeforeWrite)
        {
            Read(key, value);
            throw new InvalidOperationException("ordinary failure before mutation: " + key);
        }
        mutation();
        if (FloatCamera && View != null)
        {
            View.Centre = View.Centre.Select(number => (double)(float)number).ToArray();
            View.Direction = View.Direction.Select(number => (double)(float)number).ToArray();
        }
        if (AfterWrite != null) AfterWrite(key);
        T result = Read(key, value);
        if (key == ThrowAfterWrite) throw new InvalidOperationException("ordinary failure after mutation: " + key);
        return result;
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
        AfterWrite = null; ThrowBeforeWrite = ThrowAfterWrite = null; RejectCentre = FloatCamera = false;
        Maps = 0; Writes.Clear();
        Context = new E3d21AiBridge.E3d21Context();
        return Context;
    }
    internal static void TraceReset() { Calls.Clear(); Writes.Clear(); OnNative = null; NativeAfterFault = Removes = Refreshes = Persists = Maps = 0; }
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
        internal void Fault() { nativeFaultState.Observe("Unhandled Database error 538 from OfflineCamera", "offline"); }
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


    internal static class CameraEditingHarness
    {
        private static int checks, scenarios, faults;
        private static readonly string[] ExpectedWrites = { "write.centre_direction", "write.perspective", "write.radius", "write.twist", "write.map" };
        private sealed class Request
        {
            internal double[] Centre = { 123.4567, -654.321, 789.0123 }, Direction = { 3, -4, 5 };
            internal double Radius = 1800.75, Twist = 33.25;
            internal bool Perspective = true;
        }
        private static void Check(bool condition, string name)
        {
            checks++;
            if (!condition) throw new Exception(name + "\nNative tail: " + string.Join(", ", Scenario.Calls.Skip(Math.Max(0, Scenario.Calls.Count - 12))));
        }
        private static Dictionary<string, object> D(object value) { return (Dictionary<string, object>)value; }
        private static bool Is(Dictionary<string, object> result, string key, object expected)
        { object actual; return result.TryGetValue(key, out actual) && Equals(actual, expected); }
        private static Dictionary<string, object> Call(E3d21Context context, Request request, string revision = null, bool preview = true)
        { return D(context.SetCamera(request.Centre, request.Direction, request.Radius, request.Perspective, request.Twist, revision, preview)); }
        private static string Preview(E3d21Context context, Request request)
        {
            var result = Call(context, request);
            Check(Is(result, "success", true) && Is(result, "dry_run", true), "preview succeeds");
            Check(Is(result, "write_attempted", false) && Scenario.Writes.Count == 0 && Scenario.Maps == 0, "preview has no native setter/map");
            Check(result["actual_camera"] == null && result["before"] != null && Is(result, "readback_verified", false),
                "preview keeps observed before separate from post-operation actual camera");
            Check(result["revision"] is string && ((string)result["revision"]).Length == 64, "preview has revision");
            return (string)result["revision"];
        }
        private static void NoOtherEffects(string label)
        {
            Check(Scenario.Removes == 0 && Scenario.Refreshes == 0 && Scenario.Persists == 0, label + " no removal/graphics-refresh/persistence");
            Check(Scenario.Ce == "=1/8" && Scenario.DrawList.MemberValues.Length == 2 && Scenario.Database.Count == 9,
                label + " no CE/model/membership changes");
        }
        private static void Run(string name, Action action) { scenarios++; action(); Console.WriteLine("PASS " + name); }
        private static bool Close(double actual, double expected)
        { return Math.Abs(actual - expected) <= Math.Max(1e-5, Math.Abs(expected) * 1e-6); }
        private static double[] Unit(double[] value)
        {
            double scale = value.Max(number => Math.Abs(number));
            double norm = Math.Sqrt(value.Sum(number => (number / scale) * (number / scale)));
            return value.Select(number => number / scale / norm).ToArray();
        }
        private static void SuccessfulRequest(Request request, string label)
        {
            var context = Scenario.Reset(); Scenario.Selected = new[] { Scenario.E(5), Scenario.E(2) };
            Scenario.FloatCamera = true;
            string revision = Preview(context, request);
            var secondPreview = Call(context, request);
            Check((string)secondPreview["revision"] == revision, label + " unchanged previews retain revision");
            Scenario.TraceReset();
            var result = Call(context, request, revision, false);
            foreach (string field in new[] { "success", "readback_verified", "camera_matches_request", "context_unchanged", "ce_unchanged",
                "selection_unchanged", "members_unchanged", "primitives_unchanged", "shaded_unchanged", "dimensions_unchanged", "camera_write_completed" })
                Check(Is(result, field, true), label + " " + field);
            Check(Scenario.Writes.SequenceEqual(ExpectedWrites) && Scenario.Maps == 1, label + " five ordered native calls and single MapView");
            Check(Is(result, "write_attempted", true) && Is(result, "rollback_attempted", false) && Is(result, "retry_safe", false),
                label + " deliberate one-shot write without replay");
            Check(Scenario.View.Centre.Zip(request.Centre, Close).All(value => value) &&
                Scenario.View.Direction.Zip(Unit(request.Direction), Close).All(value => value), label + " arbitrary centre and normalized direction applied");
            Check(Close(Scenario.View.Radius, request.Radius) && Close(Scenario.View.Twist, request.Twist) &&
                Scenario.View.Perspective == request.Perspective, label + " radius roll and perspective applied");
            Check(Scenario.Selected.Select(element => element.Id).SequenceEqual(new[] { 5, 2 }), label + " exact selection order retained");
            NoOtherEffects(label);
            Scenario.TraceReset();
            var afterPreview = Call(context, request);
            Check(Is(afterPreview, "success", true) && Is(afterPreview, "no_change", true) && (string)afterPreview["revision"] != revision,
                label + " fresh preview binds changed observed camera");
            var noChange = Call(context, request, (string)afterPreview["revision"], false);
            Check(Is(noChange, "success", true) && Is(noChange, "no_change", true) && Is(noChange, "write_attempted", false) &&
                Scenario.Writes.Count == 0 && Scenario.Maps == 0, label + " verified matching camera is a no-op");
        }
        private static void BasicSuccess()
        {
            SuccessfulRequest(new Request(), "perspective oblique with roll");
            SuccessfulRequest(new Request { Direction = new[] { -0.125, 7.25, -4.5 }, Twist = -127.5, Perspective = false,
                Centre = new[] { -17.0, 4.25, 96.5 }, Radius = 0.001 }, "orthographic arbitrary direction");
        }
        private static void InputRejected(string label, Action<Request> change, string revision = null, bool preview = true)
        {
            var context = Scenario.Reset(); var request = new Request(); change(request);
            bool rejected = false;
            try { var result = Call(context, request, revision, preview); rejected = Is(result, "success", false); }
            catch (ArgumentException) { rejected = true; }
            Check(rejected, label + " rejected");
            Check(Scenario.Calls.Count == 0 && Scenario.Writes.Count == 0, label + " rejected before native access");
        }
        private static void InvalidInputs()
        {
            InputRejected("null centre", value => value.Centre = null);
            InputRejected("short centre", value => value.Centre = new[] { 0.0, 1.0 });
            InputRejected("long centre", value => value.Centre = new[] { 0.0, 1.0, 2.0, 3.0 });
            InputRejected("NaN centre", value => value.Centre[0] = double.NaN);
            InputRejected("infinite centre", value => value.Centre[1] = double.PositiveInfinity);
            InputRejected("finite centre float overflow", value => value.Centre[2] = double.MaxValue);
            InputRejected("null direction", value => value.Direction = null);
            InputRejected("short direction", value => value.Direction = new[] { 0.0, 1.0 });
            InputRejected("zero norm", value => value.Direction = new double[3]);
            InputRejected("NaN direction", value => value.Direction[0] = double.NaN);
            InputRejected("infinite direction", value => value.Direction[1] = double.NegativeInfinity);
            foreach (double number in new[] { 0.0, -1.0, double.NaN, double.PositiveInfinity })
            {
                double radius = number;
                InputRejected("invalid radius " + radius.ToString("R"), value => value.Radius = radius);
            }
            foreach (double number in new[] { double.NaN, double.NegativeInfinity })
            {
                double twist = number;
                InputRejected("invalid twist " + twist.ToString("R"), value => value.Twist = twist);
            }
            InputRejected("missing execute revision", value => { }, null, false);
            InputRejected("short revision", value => { }, "abc");
            InputRejected("nonhex revision", value => { }, new string('z', 64));
        }
        private static void StaleRevision()
        {
            foreach (string change in new[] { "camera_centre", "camera_direction", "camera_radius", "camera_twist", "camera_perspective",
                "shade", "width", "height", "member_visual", "primitive_visual", "member_missing", "primitive_missing",
                "ce", "selection", "selection_order", "project", "mdb", "view_id", "drawlist_id",
                "request_centre", "request_direction", "request_radius", "request_twist", "request_perspective", "context_instance" })
            {
                var context = Scenario.Reset(); Scenario.Selected = new[] { Scenario.E(5), Scenario.E(2) };
                var request = new Request(); string revision = Preview(context, request);
                if (change == "camera_centre") Scenario.View.Centre[0]++;
                else if (change == "camera_direction") Scenario.View.Direction = new[] { 0.0, 0.0, 1.0 };
                else if (change == "camera_radius") Scenario.View.Radius++;
                else if (change == "camera_twist") Scenario.View.Twist++;
                else if (change == "camera_perspective") Scenario.View.Perspective = !Scenario.View.Perspective;
                else if (change == "shade") Scenario.View.Shaded = !Scenario.View.Shaded;
                else if (change == "width") Scenario.View.Width++;
                else if (change == "height") Scenario.View.Height++;
                else if (change == "member_visual") Scenario.DrawList.MemberValues[0].ColourValue++;
                else if (change == "primitive_visual") Scenario.DrawList.MemberValues[0].PrimitiveValues[0].EdgesValue = true;
                else if (change == "member_missing") Scenario.DrawList.MemberValues = Scenario.DrawList.MemberValues.Take(1).ToArray();
                else if (change == "primitive_missing") Scenario.DrawList.MemberValues[0].PrimitiveValues = new DrawListPrimitive[0];
                else if (change == "ce") Scenario.Ce = "=1/7";
                else if (change == "selection") Scenario.Selected = new[] { Scenario.E(3) };
                else if (change == "selection_order") Scenario.Selected = Scenario.Selected.Reverse().ToArray();
                else if (change == "project") Scenario.Project.NameValue = "OTHER";
                else if (change == "mdb") Scenario.Mdb.NameValue = "OTHER";
                else if (change == "view_id") Scenario.View.Id++;
                else if (change == "drawlist_id") { Scenario.DrawList.Id++; Scenario.View.AttachedId++; }
                else if (change == "request_centre") request.Centre[0]++;
                else if (change == "request_direction") request.Direction = request.Direction.Select(number => number * 2).ToArray();
                else if (change == "request_radius") request.Radius++;
                else if (change == "request_twist") request.Twist++;
                else if (change == "request_perspective") request.Perspective = !request.Perspective;
                else if (change == "context_instance") { context = new E3d21Context(); Scenario.Context = context; }
                Scenario.TraceReset();
                var result = Call(context, request, revision, false);
                Check(Is(result, "success", false) && Is(result, "revision_conflict", true) && Is(result, "write_attempted", false),
                    "stale " + change + " rejected with conflict");
                Check(result["actual_camera"] == null && Is(result, "readback_verified", false), "stale " + change + " has no post-write actual echo");
                Check(Scenario.Writes.Count == 0 && Scenario.Maps == 0 && Scenario.Persists == 0, "stale " + change + " no effects");
            }
        }
        private static void ExtremeFiniteInputs()
        {
            SuccessfulRequest(new Request { Direction = new[] { double.MaxValue, double.MaxValue, 0.0 },
                Centre = new[] { (double)float.MaxValue, -(double)float.MaxValue, 0.0 } }, "finite maximum centre and robust huge direction");
            SuccessfulRequest(new Request { Direction = new[] { double.Epsilon, 0.0, -double.Epsilon }, Radius = 1e-300,
                Twist = 720.125 }, "smallest nonzero direction and tiny positive double radius");
            SuccessfulRequest(new Request { Direction = new[] { 1e-300, 2e-300, 0.0 }, Radius = double.MaxValue,
                Twist = double.MaxValue }, "tiny direction and finite double radius/twist maxima");
        }
        private static void OrdinaryFailures()
        {
            foreach (string setter in ExpectedWrites)
            foreach (bool after in new[] { false, true })
            {
                var context = Scenario.Reset(); var request = new Request(); string revision = Preview(context, request);
                Scenario.TraceReset();
                if (after) Scenario.ThrowAfterWrite = setter; else Scenario.ThrowBeforeWrite = setter;
                var result = Call(context, request, revision, false);
                int attempted = Array.IndexOf(ExpectedWrites, setter) + 1;
                string label = setter + " ordinary failure " + (after ? "after" : "before") + " mutation";
                Check(Is(result, "success", false) && Is(result, "write_attempted", true) &&
                    Is(result, "camera_write_completed", false), label + " failure distinguished from completed camera write");
                Check(Scenario.Writes.SequenceEqual(ExpectedWrites.Take(attempted)), label + " never reaches a later setter or retries");
                Check(Is(result, "residual_complete", true) && result["actual_camera"] != null, label + " complete residual is retained");
                var actual = D(result["actual_camera"]);
                Check(((double[])actual["centre"]).SequenceEqual(Scenario.View.Centre) &&
                    (double)actual["radius"] == Scenario.View.Radius && (double)actual["extra_twist_degrees"] == Scenario.View.Twist &&
                    (bool)actual["perspective"] == Scenario.View.Perspective, label + " actual camera is observed residual rather than desired echo");
                Check(Is(result, "rollback_attempted", false) && Is(result, "retry_safe", false), label + " no automatic rollback or replay");
                Check(Scenario.Calls.Skip(Scenario.Calls.IndexOf(setter) + 1).All(call => !call.StartsWith("write.", StringComparison.Ordinal)),
                    label + " only readback follows failed native call");
                NoOtherEffects(label);
            }
            {
                var context = Scenario.Reset(); var request = new Request(); string revision = Preview(context, request);
                Scenario.TraceReset(); Scenario.RejectCentre = true;
                var result = Call(context, request, revision, false);
                Check(Is(result, "success", false) && Scenario.Writes.SequenceEqual(new[] { "write.centre_direction" }) &&
                    Scenario.Maps == 0 && Is(result, "residual_complete", true), "false-return centre setter stops and observes residual");
            }
        }
        private static void ReadbackDrift()
        {
            foreach (string drift in new[] { "centre", "direction", "radius", "twist", "perspective", "shade", "width", "height",
                "member", "primitive", "selection", "selection_order", "ce", "project", "mdb", "view", "drawlist" })
            {
                var context = Scenario.Reset(); Scenario.Selected = new[] { Scenario.E(2), Scenario.E(5) };
                var request = new Request(); string revision = Preview(context, request);
                Scenario.TraceReset();
                Scenario.AfterWrite = key =>
                {
                    if (key != "write.map") return;
                    if (drift == "centre") Scenario.View.Centre[0] += 400;
                    else if (drift == "direction") Scenario.View.Direction = new[] { 0.0, 1.0, 0.0 };
                    else if (drift == "radius") Scenario.View.Radius += 700;
                    else if (drift == "twist") Scenario.View.Twist += 27;
                    else if (drift == "perspective") Scenario.View.Perspective = !Scenario.View.Perspective;
                    else if (drift == "shade") Scenario.View.Shaded = !Scenario.View.Shaded;
                    else if (drift == "width") Scenario.View.Width++;
                    else if (drift == "height") Scenario.View.Height++;
                    else if (drift == "member") Scenario.DrawList.MemberValues[0].ColourValue++;
                    else if (drift == "primitive") Scenario.DrawList.MemberValues[0].PrimitiveValues[0].EdgesValue = true;
                    else if (drift == "selection") Scenario.Selected = new[] { Scenario.E(3) };
                    else if (drift == "selection_order") Scenario.Selected = Scenario.Selected.Reverse().ToArray();
                    else if (drift == "ce") Scenario.Ce = "=1/7";
                    else if (drift == "project") Scenario.Project.NameValue = "OTHER";
                    else if (drift == "mdb") Scenario.Mdb.NameValue = "OTHER";
                    else if (drift == "view") Scenario.View = new View3D();
                    else if (drift == "drawlist") Scenario.DrawList.Id++;
                };
                var result = Call(context, request, revision, false);
                Check(Is(result, "success", false) && Is(result, "readback_verified", false), drift + " readback mismatch never reports success");
                Check(Scenario.Writes.SequenceEqual(ExpectedWrites) && Scenario.Maps == 1, drift + " one request only");
                Check(Is(result, "rollback_attempted", false) && Is(result, "retry_safe", false), drift + " no rollback over navigation");
                if (new[] { "centre", "direction", "radius", "twist", "perspective" }.Contains(drift))
                    Check(Is(result, "camera_matches_request", false) && Is(result, "residual_complete", true), drift + " known different camera retained");
                if (drift == "centre")
                    Check(((double[])D(result["actual_camera"])["centre"])[0] == request.Centre[0] + 400, "post-map navigation reported exactly");
            }
        }
        private static void RevalidationAndGuardDrift()
        {
            foreach (string drift in new[] { "camera", "member", "selection", "ce", "view", "project" })
            {
                var context = Scenario.Reset(); var request = new Request(); string revision = Preview(context, request);
                Scenario.TraceReset(); int captures = 0;
                Scenario.OnNative = key =>
                {
                    if (key != "camera.height" || ++captures != 1) return;
                    if (drift == "camera") Scenario.View.Radius++;
                    else if (drift == "member") Scenario.DrawList.MemberValues[0].ColourValue++;
                    else if (drift == "selection") Scenario.Selected = new[] { Scenario.E(3) };
                    else if (drift == "ce") Scenario.Ce = "=1/7";
                    else if (drift == "view") Scenario.View = new View3D();
                    else if (drift == "project") Scenario.Project.NameValue = "OTHER";
                };
                var result = Call(context, request, revision, false);
                Check(Is(result, "success", false) && Is(result, "write_attempted", false) &&
                    Scenario.Writes.Count == 0, drift + " change during complete revalidation prevents first setter");
            }
            foreach (string drift in new[] { "ce", "view", "project", "drawlist" })
            {
                var context = Scenario.Reset(); var request = new Request(); string revision = Preview(context, request);
                Scenario.TraceReset();
                Scenario.AfterWrite = key =>
                {
                    if (key != "write.centre_direction") return;
                    if (drift == "ce") Scenario.Ce = "=1/7";
                    else if (drift == "view") Scenario.View = new View3D();
                    else if (drift == "project") Scenario.Project.NameValue = "OTHER";
                    else Scenario.DrawList.Id++;
                };
                var result = Call(context, request, revision, false);
                Check(Is(result, "success", false) && Scenario.Writes.SequenceEqual(new[] { "write.centre_direction" }) &&
                    Scenario.Maps == 0, drift + " context guard blocks subsequent camera setters");
            }
        }
        private static void RejectedObservations()
        {
            foreach (string invalid in new[] { "view_missing", "view_invalid", "manager_missing", "selection_missing", "selection_duplicate",
                "member_missing", "primitive_missing", "camera_nan", "camera_zero_direction", "camera_width", "busy" })
            {
                var context = Scenario.Reset(); var request = new Request();
                if (invalid == "view_missing") Scenario.Overrides["current.view"] = null;
                else if (invalid == "view_invalid") Scenario.Overrides["view.valid"] = false;
                else if (invalid == "manager_missing") Scenario.Overrides["manager.instance"] = null;
                else if (invalid == "selection_missing") Scenario.Overrides["drawlist.selection"] = null;
                else if (invalid == "selection_duplicate") Scenario.Selected = new[] { Scenario.E(2), Scenario.E(2) };
                else if (invalid == "member_missing") Scenario.Overrides["drawlist.members"] = null;
                else if (invalid == "primitive_missing") Scenario.Overrides["member.1.primitives"] = null;
                else if (invalid == "camera_nan") Scenario.View.Radius = double.NaN;
                else if (invalid == "camera_zero_direction") Scenario.View.Direction = new double[3];
                else if (invalid == "camera_width") Scenario.View.Width = 0;
                else if (invalid == "busy") Scenario.Busy = true;
                var result = Call(context, request);
                Check(Is(result, "success", false) && Is(result, "write_attempted", false) && Scenario.Writes.Count == 0,
                    invalid + " observation rejected without effects");
            }
        }
        private static void ResidualReadFailuresAndFinalGetter()
        {
            foreach (string mode in new[] { "ordinary_read", "invalid_radius", "context_final_getter", "attachment_final_getter" })
            {
                var context = Scenario.Reset(); var request = new Request(); string revision = Preview(context, request);
                Scenario.TraceReset();
                if (mode == "ordinary_read" || mode == "invalid_radius")
                {
                    Scenario.AfterWrite = key =>
                    {
                        if (key != "write.map") return;
                        if (mode == "ordinary_read") Scenario.Overrides["camera.radius"] = new InvalidOperationException("camera residual getter unavailable");
                        else Scenario.View.Radius = double.NaN;
                    };
                }
                else
                {
                    bool changed = false;
                    Scenario.OnNative = key =>
                    {
                        if (key != "camera.height" || Scenario.Maps == 0 || changed) return;
                        changed = true;
                        if (mode == "context_final_getter") Scenario.View = new View3D();
                        else Scenario.View.AttachedId++;
                    };
                }
                var result = Call(context, request, revision, false);
                Check(Is(result, "success", false) && Is(result, "readback_verified", false) && Is(result, "rollback_attempted", false),
                    mode + " last getter or residual failure rejects success without restore");
                Check(Scenario.Writes.SequenceEqual(ExpectedWrites) && Scenario.Maps == 1, mode + " no additional setter/map");
                if (mode != "context_final_getter")
                {
                    Check(Is(result, "residual_complete", false) && Is(result, "outcome_uncertain", true) &&
                        result["actual_camera"] == null && result["after"] == null &&
                        result["camera_matches_request"] == null && result["write_applied"] == null,
                        mode + " unknown actual state cleared instead of echoing plan or old readback");
                }
                else
                    Check(Is(result, "context_unchanged", false) && Is(result, "outcome_uncertain", true),
                        mode + " replacement view reported as different context");
            }
            // A native helper may mutate its supplied arrays. The request and
            // requested_camera are managed plans and must remain independent.
            {
                var context = Scenario.Reset(); var request = new Request(); string revision = Preview(context, request);
                Scenario.TraceReset(); Scenario.FloatCamera = true;
                var result = Call(context, request, revision, false);
                Check(((double[])D(result["request"])["centre"]).SequenceEqual(request.Centre) &&
                    ((double[])D(result["request"])["direction"]).SequenceEqual(request.Direction), "raw request preserved across float observations");
                Check(((double[])D(result["requested_camera"])["direction"]).Zip(Unit(request.Direction), Close).All(value => value),
                    "requested normalized plan remains independent of raw request");
            }
        }
        private static void RequestCloningAndNoopIdentity()
        {
            {
                var context = Scenario.Reset(); var request = new Request();
                double[] originalCentre = request.Centre.ToArray(), originalDirection = request.Direction.ToArray();
                bool mutated = false;
                Scenario.OnNative = key =>
                {
                    if (mutated) return;
                    mutated = true; request.Centre[0] = -900; request.Direction[0] = -30;
                };
                var result = Call(context, request);
                Check(Is(result, "success", true) && ((double[])D(result["request"])["centre"]).SequenceEqual(originalCentre) &&
                    ((double[])D(result["request"])["direction"]).SequenceEqual(originalDirection),
                    "request vectors are cloned before native callbacks can mutate caller arrays");
            }
            {
                var context = Scenario.Reset(); var request = new Request();
                Scenario.View.Direction = request.Direction.ToArray();
                Scenario.View.Centre = request.Centre.ToArray(); Scenario.View.Radius = request.Radius;
                Scenario.View.Twist = request.Twist; Scenario.View.Perspective = request.Perspective;
                string revision = Preview(context, request); Scenario.TraceReset();
                var result = Call(context, request, revision.ToUpperInvariant(), false);
                Check(Is(result, "success", true) && Is(result, "no_change", true) && Scenario.Writes.Count == 0,
                    "uppercase revision and proportionally equal native direction remain a valid no-op");
            }
        }
        private static void NativeFaultPath(bool ordinaryFailure)
        {
            var context = Scenario.Reset(); var request = new Request(); string revision = Preview(context, request);
            Scenario.TraceReset();
            if (ordinaryFailure) Scenario.ThrowAfterWrite = "write.radius";
            var success = Call(context, request, revision, false);
            Check(Is(success, "success", !ordinaryFailure), "fault matrix expected baseline outcome");
            var boundaries = Scenario.Calls.ToArray();
            Check((ordinaryFailure ? ExpectedWrites.Take(3) : ExpectedWrites).All(label => boundaries.Contains(label)),
                "fault matrix contains expected writes");
            if (ordinaryFailure) Check(Is(success, "residual_complete", true), "fault matrix includes ordinary failure residual reads");
            int matrixFaults = 0;
            foreach (bool throws in new[] { false, true })
            for (int position = 1; position <= boundaries.Length; position++)
            {
                context = Scenario.Reset(); request = new Request(); revision = Preview(context, request);
                Scenario.TraceReset();
                if (ordinaryFailure) Scenario.ThrowAfterWrite = "write.radius";
                int target = position;
                Scenario.OnNative = label =>
                {
                    if (Scenario.Calls.Count != target) return;
                    context.Fault();
                    if (throws) throw new InvalidOperationException("native callback also threw");
                };
                var result = Call(context, request, revision, false);
                string name = "fault " + position + " " + boundaries[position - 1] + " throws=" + throws;
                Check(Is(result, "success", false) && context.Faulted, name + " latched failure");
                Check(Scenario.Calls.Count == position && Scenario.NativeAfterFault == 0, name + " stops all native access");
                Check(Is(result, "rollback_attempted", false) && Is(result, "retry_safe", false) && Scenario.Persists == 0, name + " no rollback/save/replay");
                int callCount = Scenario.Calls.Count, writeCount = Scenario.Writes.Count;
                var blocked = Call(context, request, revision, false);
                Check(Is(blocked, "success", false) && Scenario.Calls.Count == callCount && Scenario.Writes.Count == writeCount,
                    name + " future operation blocked before SDK");
                faults++; matrixFaults++;
            }
            Console.WriteLine("Camera native fault matrix " + (ordinaryFailure ? "ordinary residual" : "successful write") + ": " +
                boundaries.Length + " boundaries x returning/throwing callbacks = " + matrixFaults + " cases");
        }
        private static void NativeFaults() { NativeFaultPath(false); NativeFaultPath(true); }
        private static int Main()
        {
            Run("arbitrary numeric camera, preview, fresh revision and no-op", BasicSuccess);
            Run("invalid numeric inputs and revision before SDK", InvalidInputs);
            Run("extreme finite direction and native double inputs", ExtremeFiniteInputs);
            Run("stale camera, display, CE, selection, context and request", StaleRevision);
            Run("ordinary setter failures preserve exact residual", OrdinaryFailures);
            Run("post-map camera and invariant mismatches", ReadbackDrift);
            Run("revalidation and between-setter context guards", RevalidationAndGuardDrift);
            Run("invalid or incomplete native observations", RejectedObservations);
            Run("residual read failures and final getter context drift", ResidualReadFailuresAndFinalGetter);
            Run("request cloning and exact no-op identity", RequestCloningAndNoopIdentity);
            Run("native fault stopping", NativeFaults);
            Console.WriteLine("Camera editing production checks passed: " + checks + "; scenarios: " + scenarios + "; native faults: " + faults);
            return 0;
        }
    }
}
