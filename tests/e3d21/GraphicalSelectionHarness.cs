using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core3D.Graphics;

namespace Aveva.Core.Database
{
    public sealed class DbAttributeInstance
    {
        public static readonly DbAttributeInstance NAME = new DbAttributeInstance();
    }
    public sealed class DbElementType
    {
        private readonly int id;
        private readonly string value;
        public DbElementType(int id, string value) { this.id = id; this.value = value; }
        public string ShortName { get { return Scenario.Read("element." + id + ".type.short_name", value); } }
    }
    public sealed class DbElement
    {
        private readonly int id;
        private readonly string type, name;
        public DbElement(int id, string type = "BOX", string name = null)
        { this.id = id; this.type = type; this.name = name ?? "/ELEMENT-" + id; }
        private T Read<T>(string field, T fallback) { return Scenario.Read("element." + id + "." + field, fallback); }
        public bool IsNull { get { return Read("is_null", false); } }
        public bool IsValid { get { return Read("is_valid", true); } }
        public bool IsDeleted { get { return Read("is_deleted", false); } }
        public int[] Ref { get { return Read("ref", new[] { 1, id }); } }
        public DbElementType GetElementType() { return Read("type", new DbElementType(id, type)); }
        public string GetAsString(DbAttributeInstance attribute) { return Read("name", name); }
    }
}

namespace Aveva.Core3D.Graphics
{
    public sealed class View3D
    {
        private readonly int id;
        public View3D(int id = 7) { this.id = id; }
        public int ViewId { get { return Scenario.Read("view.id", id); } }
    }
    public sealed class DrawList
    {
        private readonly int id;
        public DrawList(int id = 11) { this.id = id; }
        public int ID { get { return Scenario.Read("drawlist.id", id); } }
        public DbElement[] GraphicalSelection() { return Scenario.Read("drawlist.selection", Scenario.Selected); }
    }
    public sealed class DrawListManager
    {
        public static DrawListManager Instance { get { return Scenario.Read("manager.instance", Scenario.Manager); } }
        public DrawList CurrentDrawList { get { return Scenario.Read("manager.current_drawlist", Scenario.CurrentDrawList); } }
    }
}

internal static class Scenario
{
    internal static readonly List<string> Reads = new List<string>();
    internal static readonly Dictionary<string, object> Overrides = new Dictionary<string, object>();
    internal static Action<string> OnRead;
    internal static DbElement[] Selected;
    internal static View3D View;
    internal static DrawList CurrentDrawList;
    internal static DrawListManager Manager;
    internal static string Ce;
    internal static int Dispatches, BusyChecks, BusyAt, FaultAtRead;
    internal static T Read<T>(string path, T fallback)
    {
        Reads.Add(path);
        if (OnRead != null) OnRead(path);
        object value;
        if (!Overrides.TryGetValue(path, out value)) return fallback;
        if (value is Exception) throw (Exception)value;
        return (T)value;
    }
    internal static void Reset(params DbElement[] selected)
    {
        Reads.Clear(); Overrides.Clear(); OnRead = null;
        Selected = selected; View = new View3D(); CurrentDrawList = new DrawList(); Manager = new DrawListManager();
        Ce = "=1/99"; Dispatches = BusyChecks = BusyAt = FaultAtRead = 0;
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
        internal void Fault() { nativeFaultState.Observe("Unhandled Database error 538 from FakeSelection", ""); }
        private bool IsCommandActive() { return ++Scenario.BusyChecks == Scenario.BusyAt; }
        private static string ModelCurrentReference() { return NativeRead(() => Scenario.Read("ce", Scenario.Ce)); }
        private static object CurrentView3D() { return Scenario.Read("current_view", Scenario.View); }
        private static View3D CameraRequireView()
        {
            View3D view = NativeRead(() => CurrentView3D()) as View3D;
            if (view == null) throw new InvalidOperationException("No active View3D.");
            return view;
        }
        private static string ModelReference(int[] reference)
        {
            if (reference == null || reference.Length != 2 || reference[0] <= 0 || reference[1] < 0)
                throw new InvalidOperationException("Invalid database reference.");
            return "=" + reference[0] + "/" + reference[1];
        }
        // PRODUCTION_METHODS
    }

    internal sealed class RouteRequest
    {
        public RouteInnerRequest Request;
        internal RouteRequest(NameValueCollection query) { Request = new RouteInnerRequest { QueryString = query }; }
    }
    internal sealed class RouteInnerRequest { public NameValueCollection QueryString; }
    internal sealed class RouteContext
    {
        internal int Calls, LastLimit;
        public object GetGraphicalSelection(int limit) { Calls++; LastLimit = limit; return new { limit = limit }; }
    }
    internal static class ProductionRoute
    {
        internal static object Invoke(RouteRequest request, RouteContext context)
        {
            object response = null;
            string sessionId = "SESSION";
            // PRODUCTION_ROUTE
            return response;
        }
    }

    internal static class GraphicalSelectionHarness
    {
        private static int checks;
        private static void Check(bool value, string label)
        { checks++; if (!value) throw new Exception("Check failed: " + label); }
        private static Dictionary<string, object> Dict(object value) { return (Dictionary<string, object>)value; }
        private static List<object> List(Dictionary<string, object> value, string field) { return (List<object>)value[field]; }
        private static Dictionary<string, object> Read(E3d21Context context, int limit = 50)
        { return Dict(context.GetGraphicalSelection(limit)); }
        private static bool HasError(Dictionary<string, object> value, string path)
        { return List(value, "read_errors").Select(Dict).Any(error => Equals(error["path"], path)); }
        private static void Throws<T>(Action action, string label) where T : Exception
        {
            try { action(); }
            catch (T) { Check(true, label); return; }
            throw new Exception("Expected " + typeof(T).Name + ": " + label);
        }

        private static void NormalOrderAndLimit()
        {
            Scenario.Reset(new DbElement(2, "box", "/FIRST"), new DbElement(3, "cyli", "/SECOND"));
            var result = Read(new E3d21Context());
            var rows = List(result, "elements").Select(Dict).ToList();
            Check(Scenario.Dispatches == 1 && Scenario.Reads.Count(path => path == "drawlist.selection") == 1,
                "one UI dispatch and one native selection array read");
            Check(Equals(result["success"], true) && Equals(result["complete"], true) && Equals(result["available"], true), "complete selection");
            Check(Equals(result["selection_count"], 2) && Equals(result["returned_count"], 2) && Equals(result["resolved_count"], 2), "native and resolved counts");
            Check(rows.Select(row => (string)row["refno"]).SequenceEqual(new[] { "=1/2", "=1/3" }) &&
                rows.Select(row => (string)row["type"]).SequenceEqual(new[] { "BOX", "CYLI" }), "selection order and canonical type");
            Check(Equals(rows[0]["selection_index"], 1) && Equals(rows[1]["selection_index"], 2) && Equals(rows[1]["name"], "/SECOND"), "ordered native identity");
            Check(Equals(result["context_unchanged"], true) && Equals(result["ce_unchanged"], true), "context and CE observations");

            Scenario.Reset(new DbElement(2), new DbElement(3));
            result = Read(new E3d21Context(), 1);
            Check(Equals(result["success"], true) && Equals(result["complete"], false), "truncated read is successful but incomplete");
            Check(Equals(result["selection_count"], 2) && Equals(result["returned_count"], 1) && Equals(result["truncated"], true) && Equals(result["has_more"], true), "truncated never means empty");
            Check(!Scenario.Reads.Any(path => path.StartsWith("element.3.", StringComparison.Ordinal)), "limit prevents later detail reads");
        }

        private static void EmptyAndUnavailable()
        {
            Scenario.Reset();
            var empty = Read(new E3d21Context());
            Check(Equals(empty["success"], true) && Equals(empty["available"], true) && Equals(empty["complete"], true), "known empty is complete");
            Check(Equals(empty["selection_count"], 0) && Equals(empty["returned_count"], 0) && Equals(empty["truncated"], false) && List(empty, "elements").Count == 0, "known empty counts");
            Scenario.Reset(); Scenario.Selected = null;
            var unknown = Read(new E3d21Context());
            Check(Equals(unknown["success"], false) && Equals(unknown["available"], false) && Equals(unknown["complete"], false), "SDK null is unknown");
            Check(unknown["selection_count"] == null && unknown["truncated"] == null && unknown["has_more"] == null && HasError(unknown, "selection"), "unknown never gains empty count or truncation");
            foreach (string missing in new[] { "manager.instance", "manager.current_drawlist", "current_view" })
            {
                Scenario.Reset(); Scenario.Overrides[missing] = null;
                var unavailable = Read(new E3d21Context());
                Check(Equals(unavailable["available"], false) && unavailable["selection_count"] == null && HasError(unavailable, "selection"), "missing graphical context: " + missing);
            }
        }

        private static void BrokenRowsAndFields()
        {
            Scenario.Reset(new DbElement(2), null, new DbElement(4, "PANE"));
            var result = Read(new E3d21Context());
            var rows = List(result, "elements").Select(Dict).ToList();
            Check(Equals(result["success"], false) && Equals(result["complete"], false) && Equals(result["available"], true), "bad middle member is a partial failure");
            Check(rows.Count == 3 && Equals(rows[0]["refno"], "=1/2") && Equals(rows[2]["refno"], "=1/4") && Equals(rows[2]["success"], true), "later selection survives broken middle member");
            Check(rows[1]["refno"] == null && HasError(rows[1], "element") && Equals(result["resolved_count"], 2), "unreadable member remains explicit");
            foreach (string failed in new[] { "ref", "type", "name" })
            {
                Scenario.Reset(new DbElement(2), new DbElement(3), new DbElement(4));
                Scenario.Overrides["element.3." + failed] = new InvalidOperationException("Fake SDK " + failed + " read failure");
                result = Read(new E3d21Context());
                rows = List(result, "elements").Select(Dict).ToList();
                string field = failed == "ref" ? "refno" : failed;
                Check(Equals(result["success"], false) && Equals(result["returned_count"], 3) && Equals(result["resolved_count"], 2), "field error preserves every selection slot: " + failed);
                Check(HasError(rows[1], field) && rows[1][field] == null && Equals(rows[2]["success"], true), "field error and following row retained: " + failed);
                if (failed != "ref") Check(Equals(rows[1]["refno"], "=1/3"), "reference retained after other field failure");
                if (failed != "type") Check(Equals(rows[1]["type"], "BOX"), "type retained after other field failure");
                if (failed != "name") Check(Equals(rows[1]["name"], "/ELEMENT-3"), "name retained after other field failure");
            }
            foreach (object badType in new object[] { null, new DbElementType(3, " ") })
            {
                Scenario.Reset(new DbElement(2), new DbElement(3), new DbElement(4));
                Scenario.Overrides["element.3.type"] = badType;
                rows = List(Read(new E3d21Context()), "elements").Select(Dict).ToList();
                Check(HasError(rows[1], "type") && Equals(rows[1]["refno"], "=1/3") && Equals(rows[2]["success"], true), "null or empty type does not erase neighboring identities");
            }
        }

        private static void ContextChangesAndValidation()
        {
            foreach (string change in new[] { "ce", "view", "drawlist" })
            {
                Scenario.Reset(new DbElement(2));
                Scenario.OnRead = path =>
                {
                    if (path != "drawlist.selection") return;
                    if (change == "ce") Scenario.Ce = "=1/100";
                    if (change == "view") Scenario.View = new View3D(8);
                    if (change == "drawlist") Scenario.CurrentDrawList = new DrawList(12);
                };
                var result = Read(new E3d21Context());
                Check(Equals(result["success"], false) && Equals(result["complete"], false) && List(result, "elements").Count == 1, "changed context preserves selection and fails observation: " + change);
                Check(HasError(result, change == "ce" ? "ce_after" : "context"), "context change diagnostic: " + change);
            }
            foreach (int limit in new[] { -1, 0, 201, int.MaxValue })
            {
                Scenario.Reset(new DbElement(2));
                Throws<ArgumentOutOfRangeException>(() => Read(new E3d21Context(), limit), "invalid direct limit");
                Check(Scenario.Dispatches == 0 && Scenario.Reads.Count == 0, "invalid limit never dispatches");
            }
            Scenario.Reset(new DbElement(2)); Scenario.BusyAt = 1;
            Throws<InvalidOperationException>(() => Read(new E3d21Context()), "busy before dispatch");
            Check(Scenario.Dispatches == 0 && Scenario.Reads.Count == 0, "busy never dispatches");
            Scenario.Reset(new DbElement(2)); Scenario.BusyAt = 2;
            Throws<InvalidOperationException>(() => Read(new E3d21Context()), "became busy before SDK read");
            Check(Scenario.Dispatches == 1 && Scenario.Reads.Count == 0, "in-callback busy prevents SDK work");
        }

        private static void FaultStopsNativeContinuation()
        {
            foreach (bool throwOrdinaryAfterFault in new[] { false, true })
            {
                Scenario.Reset(new DbElement(2), new DbElement(3), new DbElement(4));
                var context = new E3d21Context();
                Scenario.OnRead = path =>
                {
                    if (path != "element.3.name") return;
                    context.Fault(); Scenario.FaultAtRead = Scenario.Reads.Count;
                    if (throwOrdinaryAfterFault) throw new InvalidOperationException("Ordinary exception after fault callback");
                };
                var result = Read(context);
                var rows = List(result, "elements").Select(Dict).ToList();
                Check(Scenario.Reads.Count == Scenario.FaultAtRead && Scenario.Reads.Last() == "element.3.name", "zero later SDK reads after fault callback");
                Check(Scenario.Dispatches == 1 && rows.Count == 2 && Equals(rows[0]["success"], true), "fault preserves confirmed prefix and active partial row");
                Check(Equals(rows[1]["refno"], "=1/3") && Equals(rows[1]["type"], "BOX") && rows[1]["name"] == null, "fault preserves fields read before latch");
                Check(Equals(result["success"], false) && Equals(result["complete"], false) && Equals(result["returned_count"], 2) && Equals(result["selection_count"], 3), "fault retains materialized native count without claiming completion");
                Check(result["ce_after"] == null && result["ce_unchanged"] == null && result["context_unchanged"] == null, "fault skips final CE/context queries");
                Check(Equals(Dict(result["native_database_fault"])["has_fault"], true) && HasError(result, "selection"), "fault snapshot and diagnostic retained");
            }
        }

        private static void HttpRouteValidation()
        {
            foreach (int limit in new[] { 1, 50, 200 })
            {
                var context = new RouteContext();
                var response = ProductionRoute.Invoke(new RouteRequest(new NameValueCollection { { "limit", limit.ToString() } }), context);
                Check(response != null && context.Calls == 1 && context.LastLimit == limit, "production route passes valid limit");
            }
            var defaultContext = new RouteContext();
            ProductionRoute.Invoke(new RouteRequest(new NameValueCollection()), defaultContext);
            Check(defaultContext.Calls == 1 && defaultContext.LastLimit == 50, "production route default");
            var invalid = new List<NameValueCollection>();
            foreach (string value in new[] { "", "0", "201", "-1", "+1", "1.0", " 1", "1 ", "true", "2147483648", "\n1" })
                invalid.Add(new NameValueCollection { { "limit", value } });
            invalid.Add(new NameValueCollection { { "limit", "1" }, { "limit", "2" } });
            invalid.Add(new NameValueCollection { { "unknown", "1" } });
            invalid.Add(new NameValueCollection { { "Limit", "1" } });
            invalid.Add(new NameValueCollection { { null, "limit" } });
            foreach (var query in invalid)
            {
                var context = new RouteContext();
                Throws<ArgumentException>(() => ProductionRoute.Invoke(new RouteRequest(query), context), "production route invalid query");
                Check(context.Calls == 0, "query rejected before context method");
            }
        }

        public static void Main()
        {
            NormalOrderAndLimit(); EmptyAndUnavailable(); BrokenRowsAndFields();
            ContextChangesAndValidation(); FaultStopsNativeContinuation(); HttpRouteValidation();
            Console.WriteLine("Graphical selection production checks passed: " + checks);
        }
    }
}
