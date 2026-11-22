using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Threading.Tasks;
using Aveva.Core.Database;
using E3d21AiBridge;
using Newtonsoft.Json.Linq;

// Production reader/selector/HTTP method bodies against managed SDK substitutes.
internal static class TypedConnectionChecks
{
    private static int checks;
    private static void Check(bool condition) { checks++; if (!condition) throw new Exception("Check " + checks); }
    private static Dictionary<string, object> Row(object value) { return (Dictionary<string, object>)value; }
    private static object Field(object value, string name) { return value.GetType().GetProperty(name).GetValue(value, null); }
    private static void Reject(Action action)
    {
        bool rejected = false;
        try { action(); } catch (ArgumentException) { rejected = true; } catch (InvalidOperationException) { rejected = true; }
        Check(rejected);
    }
    private static int Main()
    {
        try
        {
            var reader = new TypedConnectionReader();
            State.Reset("BRAN");
            var report = Row(reader.GetConnectionPoints("=1/2", "WORLD", new[] { 2, 1 }));
            Check((string)Row(report["element"])["type"] == "BRAN" && State.ParseCalls == 0);
            Check(State.PointTypes.SequenceEqual(new[] { "BRAN", "BRAN" }));
            Check(((int[])report["point_numbers"]).SequenceEqual(new[] { 2, 1 }));
            var context = Row(report["context"]);
            Check((string)Row(context["before"])["project"] == "PROJECT" &&
                  (string)Row(context["before"])["mdb"] == "MDB" &&
                  (string)Row(context["before"])["current_element"] == "=9/9" &&
                  (string)Row(context["before"])["current_element_type"] == "EQUI");
            Check(Equals(context["same_project_instance"], true) && Equals(context["same_mdb_instance"], true) &&
                  Equals(context["boundary_agreement"], true) && State.Dispatches == 1 &&
                  State.NativeCallsOutsideDispatch == 0);
            State.Reset("BRAN");
            State.OnPoint = () =>
            {
                Aveva.Core.Database.Project.Value = new Aveva.Core.Database.Project();
                Aveva.Core.Database.MDB.Value = new Aveva.Core.Database.MDB();
                State.CeElement = new DbElement { Type = "SITE", Ref = new[] { 9, 9 } };
                State.OnPoint = null;
            };
            report = Row(reader.GetConnectionPoints("=1/2", "WORLD", new[] { 1 }));
            context = Row(report["context"]);
            Check(Equals(context["same_project_instance"], false) && Equals(context["same_mdb_instance"], false) &&
                  Equals(context["ce_unchanged"], false) && Equals(context["boundary_agreement"], false) &&
                  (string)Row(context["after"])["current_element_type"] == "SITE" &&
                  Equals(report["complete"], false));
            foreach (string anchorType in new[] { "BRAN", "ELBO" })
            {
                State.Reset(anchorType);
                report = Row(reader.GetConnectionPoints("=1/2", "OWNER", null, " tubi "));
                Check((string)Row(report["element"])["type"] == "TUBI");
                Check((string)Row(report["frame"])["refno"] == "=1/3");
                Check(State.PointTypes.SequenceEqual(new[] { "TUBI", "TUBI" }));
                Check(State.ParseCalls == 1 && ReferenceEquals(State.ParseAnchor, State.Anchor));
                Check(State.ParseExpression == "ILEAVE TUBE OF =1/2");
                Check(Equals(report["ce_unchanged"], true) && State.Ce == "=9/9" && State.Dispatches == 1);
            }
            State.Reset("TUBI"); reader.GetConnectionPoints("=1/2", "WORLD", new[] { 1 }, "TUBI");
            Check(State.ParseCalls == 0 && State.PointTypes.SequenceEqual(new[] { "TUBI" }));
            State.Reset("ELBO");
            Reject(() => reader.GetConnectionPoints("=1/2", "WORLD", new[] { 1 }, "NOZZ"));
            Check(State.ParseCalls == 0 && State.PplsReads == 0 && State.PointTypes.Count == 0);
            foreach (string returnedType in new[] { "BRAN", "ELBO" })
            {
                State.Reset("BRAN"); State.Tube.Type = returnedType;
                Reject(() => reader.GetConnectionPoints("=1/2", "WORLD", new[] { 1 }, "TUBI"));
                Check(State.PplsReads == 0 && State.PointTypes.Count == 0);
            }
            State.Reset("BRAN"); State.ParseSucceeds = false;
            Reject(() => reader.GetConnectionPoints("=1/2", "WORLD", new[] { 1 }, "TUBI"));
            Check(State.PplsReads == 0);
            State.Reset("BRAN"); State.Tube.DbType = DbType.Catalogue;
            Reject(() => reader.GetConnectionPoints("=1/2", "WORLD", new[] { 1 }, "TUBI"));
            Check(State.PplsReads == 0);
            foreach (string value in new[] { "bad type", "TUBI; DELETE CE", new string('T', 65), "TU\nBI" })
            {
                State.Reset("BRAN");
                Reject(() => reader.GetConnectionPoints("=1/2", "WORLD", new[] { 1 }, value));
                Check(State.Dispatches == 0 && State.Resolves == 0);
            }
            var route = new RouteContext();
            object routeReply = Route.Invoke(new JObject { ["refno"] = "=1/2" }, route).GetAwaiter().GetResult();
            Check(route.Calls == 1 && route.Type == "" && route.Frame == "WORLD" && route.Points.Count == 0);
            Check((string)Field(routeReply, "session_id") == "session-test" &&
                  Guid.TryParse((string)Field(routeReply, "bridge_build"), out _));
            route = new RouteContext();
            Route.Invoke(new JObject { ["refno"] = "=1/2", ["expected_type"] = "TUBI", ["point_numbers"] = new JArray(2, 1) }, route).GetAwaiter().GetResult();
            Check(route.Calls == 1 && route.Type == "TUBI" && route.Points.SequenceEqual(new[] { 2, 1 }));
            foreach (JToken value in new JToken[] { JValue.CreateNull(), new JValue(1), new JValue(true), new JObject(), new JArray() })
            {
                var rejectedRoute = new RouteContext();
                Reject(() => Route.Invoke(new JObject { ["refno"] = "=1/2", ["expected_type"] = value }, rejectedRoute).GetAwaiter().GetResult());
                Check(rejectedRoute.Calls == 0);
            }
            var extraRoute = new RouteContext();
            Reject(() => Route.Invoke(new JObject { ["refno"] = "=1/2", ["unexpected"] = "TUBI" }, extraRoute).GetAwaiter().GetResult());
            Check(extraRoute.Calls == 0);
            Console.WriteLine("Typed connection point production checks passed: " + checks);
            return 0;
        }
        catch (Exception failure) { Console.Error.WriteLine(failure); return 1; }
    }
}

internal sealed class TypedConnectionReader
{
    private const int ConnectionPointLimit = 64;
    // PRODUCTION_READER
    // PRODUCTION_CONTEXT
    private static bool IsCommandActive() { return false; }
    private static T OnUi<T>(Func<T> read)
    {
        State.Dispatches++; State.InDispatch = true;
        try { return read(); } finally { State.InDispatch = false; }
    }
    private static T NativeRead<T>(Func<T> read)
    {
        if (!State.InDispatch) State.NativeCallsOutsideDispatch++;
        return read();
    }
    private static void ThrowIfNativeFaulted() { }
    private static bool IsUsable(DbElement value) { return value != null && !value.IsNull && value.IsValid && !value.IsDeleted; }
    private static DbElement ResolveElement(string reference) { State.Resolves++; return State.Anchor; }
    private static string ElementType(DbElement value) { return value.Type; }
    private static string ModelReference(int[] pair) { return "=" + pair[0] + "/" + pair[1]; }
    private static DbElement ModelFrame(string requested, DbElement owner) { return requested == "OWNER" ? owner : State.World; }
    private static string ModelCurrentReference() { return State.Ce; }
    private static string MessageText(PdmsMessage message) { return "parse failed"; }
    private static void ModelCaptureCeAfter(Dictionary<string, object> result)
    {
        result["ce_after"] = State.Ce;
        result["ce_unchanged"] = Equals(result["ce_before"], State.Ce);
    }
    private static Dictionary<string, object> ConnectionReadPoint(DbElement element, DbElement frame, int number, bool? listed)
    {
        if (State.OnPoint != null)
        {
            var callback = State.OnPoint;
            State.OnPoint = null;
            callback();
        }
        State.PointTypes.Add(element.Type);
        return new Dictionary<string, object> { ["number"] = number, ["complete"] = true, ["errors"] = new Dictionary<string, string>() };
    }
    private static Dictionary<string, object> ConnectionReadReference(DbElement element, DbAttribute attribute)
    {
        return new Dictionary<string, object> { ["status"] = "unset", ["error"] = null };
    }
    private static Dictionary<string, object> ConnectionReadField(string name, Func<Dictionary<string, object>> read) { return read(); }
}

internal enum DbType { Design, Catalogue }
internal sealed class PdmsMessage { }
internal sealed class DbAttribute { internal string ShortName; }
internal static class DbAttributeInstance
{
    internal static readonly DbAttribute NAME = new DbAttribute { ShortName = "NAME" }, PPLS = new DbAttribute { ShortName = "PPLS" },
        SPRE = new DbAttribute { ShortName = "SPRE" }, CATR = new DbAttribute { ShortName = "CATR" },
        CREF = new DbAttribute { ShortName = "CREF" }, CEND = new DbAttribute { ShortName = "CEND" };
}
internal sealed class DbElementType { internal string ShortName; internal string Name { get { return ShortName; } } }
internal sealed class DbElement
{
    internal string Type;
    internal int[] Ref = new[] { 1, 2 };
    internal DbType DbType = DbType.Design;
    internal DbElement Owner;
    internal bool IsNull { get { return false; } }
    internal bool IsValid { get { return true; } }
    internal bool IsDeleted { get { return false; } }
    internal DbElementType GetElementType() { return new DbElementType { ShortName = Type }; }
    internal string GetAsString(DbAttribute attribute) { if (attribute == DbAttributeInstance.PPLS) State.PplsReads++; return attribute == DbAttributeInstance.NAME ? "/" + Type : "1 2"; }
    internal int[] GetIntegerArray(DbAttribute attribute) { return new[] { 1, 2 }; }
    internal static bool Parse(string expression, DbElement anchor, out DbElement tube, out PdmsMessage error)
    {
        State.ParseCalls++; State.ParseExpression = expression; State.ParseAnchor = anchor;
        tube = State.Tube; error = null; return State.ParseSucceeds;
    }
}

namespace Aveva.Core.Database
{
    internal sealed class Project
    {
        internal static Project Value;
        internal string Name { get { return "PROJECT"; } }
        internal static Project CurrentProject { get { return Value; } }
    }
    internal sealed class MDB
    {
        internal static MDB Value;
        internal string Name { get { return "MDB"; } }
        internal static MDB CurrentMDB { get { return Value; } }
    }
    internal static class CurrentElement
    {
        internal static global::DbElement Element { get { return global::State.CeElement; } }
    }
}

internal static class State
{
    internal static DbElement Anchor, Tube, World, ParseAnchor, CeElement;
    internal static string Ce, ParseExpression;
    internal static bool ParseSucceeds;
    internal static int ParseCalls, PplsReads, Dispatches, Resolves, NativeCallsOutsideDispatch;
    internal static bool InDispatch;
    internal static Action OnPoint;
    internal static readonly List<string> PointTypes = new List<string>();
    internal static void Reset(string type)
    {
        World = new DbElement { Type = "WORLD", Ref = new[] { 1, 0 } };
        Anchor = new DbElement { Type = type, Owner = World };
        Tube = new DbElement { Type = "TUBI", Owner = new DbElement { Type = "BRAN", Ref = new[] { 1, 3 } } };
        CeElement = new DbElement { Type = "EQUI", Ref = new[] { 9, 9 } };
        Aveva.Core.Database.Project.Value = new Aveva.Core.Database.Project();
        Aveva.Core.Database.MDB.Value = new Aveva.Core.Database.MDB();
        Ce = "=9/9"; ParseExpression = null; ParseAnchor = null; ParseSucceeds = true;
        ParseCalls = PplsReads = Dispatches = Resolves = NativeCallsOutsideDispatch = 0;
        InDispatch = false; OnPoint = null; PointTypes.Clear();
    }
}
internal sealed class RouteContext
{
    internal int Calls;
    internal string Type, Frame;
    internal IList<int> Points;
    public object GetConnectionPoints(string reference, string frame, IList<int> points, string expectedType = null)
    {
        Calls++; Type = expectedType; Frame = frame; Points = points; return new { success = true };
    }
}
internal sealed class Carrier { internal JObject Request; }
internal static class E3d21HttpAdapter { }
internal static class Route
{
    internal static async Task<object> Invoke(JObject suppliedBody, RouteContext context)
    {
        var request = new Carrier { Request = suppliedBody };
        const string sessionId = "session-test";
        object response = null;
        // PRODUCTION_ROUTE
        return response;
    }
    private static Task<JObject> ReadBodyAsync(JObject body) { return Task.FromResult(body); }
    // PRODUCTION_READ_STRING
}
