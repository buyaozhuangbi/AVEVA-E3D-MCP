using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core.Geometry;

namespace Aveva.Core.Database
{
    public enum DbType { Design }
    public enum DbDimension { VOLUME }
    public sealed class Project
    {
        internal static Project Value;
        public static Project CurrentProject { get { return Scenario.Read("project.current", Value); } }
        public string Name { get { return Scenario.Read("project.name", "PROJECT"); } }
    }
    public sealed class MDB
    {
        internal static MDB Value;
        public static MDB CurrentMDB { get { return Scenario.Read("mdb.current", Value); } }
        public string Name { get { return Scenario.Read("mdb.name", "MDB"); } }
        public DbElement GetFirstWorld(DbType type) { return Scenario.Read("mdb.world", Scenario.World); }
    }
    public static class CurrentElement
    {
        public static DbElement Element { get { return Scenario.Read("ce", Scenario.Ce); } }
    }
    public sealed class DbAttribute
    {
        internal string Key;
        internal DbAttribute(string key) { Key = key; }
        public string ShortName { get { return Scenario.Read("attribute." + Key, Key); } }
    }
    public static class DbAttributeInstance
    {
        public static readonly DbAttribute NAME = new DbAttribute("NAME"), REF = new DbAttribute("REF"),
            POS = new DbAttribute("POS"), ORI = new DbAttribute("ORI"), GVOL = new DbAttribute("GVOL"),
            NVOL = new DbAttribute("NVOL"), CABBOX = new DbAttribute("CABBOX"), WBOX = new DbAttribute("WBOX"), CSGBOX = new DbAttribute("CSGBOX");
    }
    public sealed class DbElementType
    {
        private readonly string path;
        internal DbElementType(string path) { this.path = path; }
        public string Name { get { return Scenario.Read(path + ".type.name", "BOX"); } }
        public string ShortName { get { return Scenario.Read(path + ".type.short_name", "BOX"); } }
        public static bool operator ==(DbElementType first, DbElementType second)
        {
            return Scenario.Read("type.equals." + (ReferenceEquals(second, null) ? "null" : second.path), ReferenceEquals(first, second));
        }
        public static bool operator !=(DbElementType first, DbElementType second) { return !(first == second); }
        public override bool Equals(object value) { return ReferenceEquals(this, value); }
        public override int GetHashCode() { return base.GetHashCode(); }
    }
    public static class DbElementTypeInstance
    {
        internal static readonly DbElementType CylinderValue = new DbElementType("NCYLINDER"),
            BoxValue = new DbElementType("NBOX"), ConeValue = new DbElementType("NCONE");
        public static DbElementType NCYLINDER { get { return Scenario.Read("type.constant.NCYLINDER", CylinderValue); } }
        public static DbElementType NBOX { get { return Scenario.Read("type.constant.NBOX", BoxValue); } }
        public static DbElementType NCONE { get { return Scenario.Read("type.constant.NCONE", ConeValue); } }
    }
    public sealed class DbQualifier
    {
        internal DbElement Frame;
        internal string Scope;
        public DbQualifier() { Scenario.Read("qualifier.new", true); }
        public DbElement wrtQualifier { set { Scenario.Read("qualifier.wrt", true); Frame = value; } }
        public void Add(string value) { Scenario.Read("qualifier." + value, true); Scope = value; }
    }
    public sealed class DbElement
    {
        internal readonly int Id;
        internal string Path { get { return "e" + Id; } }
        internal DbElement(int id) { Id = id; }
        public bool IsValid { get { return Scenario.Read(Path + ".valid", true); } }
        public bool IsNull { get { return Scenario.Read(Path + ".null", false); } }
        public int[] Ref { get { return Scenario.Read(Path + ".ref", new[] { 1, Id }); } }
        public DbType DbType { get { return Scenario.Read(Path + ".db_type", DbType.Design); } }
        public DbElement Owner { get { return Scenario.Read(Path + ".owner", Scenario.Owner); } }
        public DbElementType GetElementType() { return Scenario.Read(Path + ".type", new DbElementType(Path)); }
        public bool IsAttributeValid(DbAttribute attribute) { return Scenario.Read(Path + ".supports." + attribute.Key, true); }
        public string GetString(DbAttribute attribute) { return Scenario.Read(Path + ".string." + attribute.Key, "/E" + Id); }
        public string GetAsString(DbAttribute attribute)
        {
            return Scenario.Read(Path + ".text." + attribute.Key, attribute == DbAttributeInstance.REF ? "=1/" + Id : "/E" + Id);
        }
        public string GetAsString(DbAttribute attribute, DbQualifier qualifier)
        {
            Scenario.Qualifiers.Add(Path + "." + attribute.Key + ":" + qualifier.Scope);
            return Scenario.Read(Path + ".text." + attribute.Key, attribute.Key == "GVOL" ? "100 mm3" : "80 mm3");
        }
        public Position GetPosition(DbAttribute attribute, DbQualifier qualifier)
        {
            Scenario.Frames.Add(qualifier.Frame);
            return Scenario.Read(Path + ".position", new Position(Path + ".position", 1, 2, 3));
        }
        public Orientation GetOrientation(DbAttribute attribute, DbQualifier qualifier)
        {
            Scenario.Frames.Add(qualifier.Frame);
            return Scenario.Read(Path + ".orientation", new Orientation(Path + ".orientation"));
        }
        public DbDouble GetDbDouble(DbAttribute attribute, DbQualifier qualifier)
        {
            Scenario.Qualifiers.Add(Path + "." + attribute.Key + ":" + qualifier.Scope);
            return Scenario.Read(Path + "." + attribute.Key, new DbDouble(Path + "." + attribute.Key, attribute.Key == "GVOL" ? 100 : 80, Scenario.NativeUnits));
        }
    }
    public sealed class DbDoubleDimension
    {
        public bool Set { get { return Scenario.Read("dimension.set", true); } }
        public string Name { get { return Scenario.Read("dimension.name", "Volume"); } }
        public DbDimension StandardDimension { get { return Scenario.Read("dimension.standard", DbDimension.VOLUME); } }
        public DbDoubleUnits DatabaseUnits { get { return Scenario.Read("dimension.database_units", Scenario.NativeUnits); } }
    }
    public sealed class DbDoubleUnits
    {
        private readonly string path;
        internal DbDoubleUnits(string path) { this.path = path; }
        public bool Set { get { return Scenario.Read(path + ".set", true); } }
        public string Name { get { return Scenario.Read(path + ".name", "cubic millimetre"); } }
        public string ShortName { get { return Scenario.Read(path + ".short_name", "mm3"); } }
        public string Description { get { return Scenario.Read(path + ".description", "cubic millimetre"); } }
        public double Factor { get { return Scenario.Read(path + ".factor", 1.0); } }
        public DbDoubleDimension Dimension { get { return Scenario.Read(path + ".dimension", Scenario.Dimension); } }
        public static DbDoubleUnits GetUnits(string name, DbDoubleDimension dimension) { return Scenario.Read("units.parse." + name, Scenario.OutputUnits); }
    }
    public sealed class DbDouble
    {
        private readonly string path;
        private readonly double value;
        private readonly DbDoubleUnits units;
        internal DbDouble(string path, double value, DbDoubleUnits units) { this.path = path; this.value = value; this.units = units; }
        public DbDoubleDimension Dimension { get { return Scenario.Read(path + ".dimension", Scenario.Dimension); } }
        public double Value { get { return Scenario.Read(path + ".value", value); } }
        public double DatabaseValue { get { return Scenario.Read(path + ".database_value", value); } }
        public DbDoubleUnits Units { get { return Scenario.Read(path + ".units", units); } }
        public DbDouble ConvertUnits(DbDoubleUnits outputUnits) { return Scenario.Read(path + ".convert", new DbDouble(path + ".converted", value, outputUnits)); }
    }
}

namespace Aveva.Core.Geometry
{
    public sealed class Position
    {
        private readonly string path;
        private readonly double x, y, z;
        internal Position(string path, double x, double y, double z) { this.path = path; this.x = x; this.y = y; this.z = z; }
        public double X { get { return Scenario.Read(path + ".x", x); } }
        public double Y { get { return Scenario.Read(path + ".y", y); } }
        public double Z { get { return Scenario.Read(path + ".z", z); } }
    }
    public sealed class Direction
    {
        private readonly string path;
        private readonly double east, north, up;
        internal Direction(string path, double east, double north, double up) { this.path = path; this.east = east; this.north = north; this.up = up; }
        public double East { get { return Scenario.Read(path + ".east", east); } }
        public double North { get { return Scenario.Read(path + ".north", north); } }
        public double Up { get { return Scenario.Read(path + ".up", up); } }
    }
    public sealed class Orientation
    {
        private readonly string path;
        internal Orientation(string path) { this.path = path; }
        public Direction XDir() { return Scenario.Read(path + ".x_axis", new Direction(path + ".x_axis", 1, 0, 0)); }
        public Direction YDir() { return Scenario.Read(path + ".y_axis", new Direction(path + ".y_axis", 0, 1, 0)); }
        public Direction ZDir() { return Scenario.Read(path + ".z_axis", new Direction(path + ".z_axis", 0, 0, 1)); }
    }
    public sealed class LimitsBox
    {
        private readonly string path;
        internal LimitsBox(string path) { this.path = path; }
        public Position Minimum { get { return Scenario.Read(path + ".minimum", new Position(path + ".minimum", 0, 0, 0)); } }
        public Position Maximum { get { return Scenario.Read(path + ".maximum", new Position(path + ".maximum", 10, 20, 30)); } }
    }
    public sealed class Spatial
    {
        public static Spatial Instance { get { return Scenario.Read("spatial.instance", new Spatial()); } }
        public bool ElementInElementExact(DbElement first, DbElement second, bool fully)
        {
            string call = "exact." + first.Id + "." + second.Id + "." + fully;
            Scenario.RelationCalls.Add(call);
            return Scenario.Read(call, false);
        }
        public bool ElementInElementBox(DbElement first, DbElement second)
        {
            string call = "box." + first.Id + "." + second.Id;
            Scenario.RelationCalls.Add(call);
            return Scenario.Read(call, false);
        }
        public bool LimitsBox(DbElement element, out LimitsBox bounds)
        {
            bounds = new LimitsBox(element.Path + ".bounds");
            return Scenario.Read(element.Path + ".bounds", true);
        }
    }
}

internal static class Scenario
{
    internal static readonly List<string> Reads = new List<string>();
    internal static readonly List<string> Qualifiers = new List<string>();
    internal static readonly List<DbElement> Frames = new List<DbElement>();
    internal static readonly List<string> RelationCalls = new List<string>();
    internal static readonly Dictionary<string, object> Overrides = new Dictionary<string, object>();
    internal static readonly Dictionary<string, DbElement> Elements = new Dictionary<string, DbElement>(StringComparer.OrdinalIgnoreCase);
    internal static Action<string> OnRead;
    internal static Action AfterOperation;
    internal static DbElement World, Owner, Ce;
    internal static DbDoubleDimension Dimension;
    internal static DbDoubleUnits NativeUnits, OutputUnits;
    internal static int Dispatches;
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
    internal static void Reset()
    {
        Reads.Clear(); Qualifiers.Clear(); Frames.Clear(); RelationCalls.Clear(); Overrides.Clear(); Elements.Clear(); OnRead = null; AfterOperation = null; Dispatches = 0; Busy = false;
        World = new DbElement(0); Owner = new DbElement(90); Ce = new DbElement(99);
        Project.Value = new Project(); MDB.Value = new MDB();
        Dimension = new DbDoubleDimension(); NativeUnits = new DbDoubleUnits("native_units"); OutputUnits = new DbDoubleUnits("output_units");
        for (int id = 1; id <= 32; id++) { var element = new DbElement(id); Elements["/E" + id] = element; Elements["=1/" + id] = element; }
        Elements["/FRAME"] = Owner;
    }
}

namespace E3d21AiBridge
{
    internal sealed class ModelEditInput
    {
        // PRODUCTION_INPUT_METHODS
    }
    public sealed partial class E3d21Context
    {
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;
        private readonly Action<Action> invokeOnUi;
        public E3d21Context() { invokeOnUi = action => { Scenario.Dispatches++; action(); }; }
        internal void Fault() { nativeFaultState.Observe("Unhandled Database error 538 from FakeDb", ""); }
        private bool IsCommandActive()
        {
            if (nativeFaultState.HasFault) throw new NativeDatabaseFaultException();
            return Scenario.Busy;
        }
        private static DbElement ResolveElement(string refno)
        {
            return NativeRead(() => { DbElement found; return Scenario.Read("resolve." + refno, Scenario.Elements.TryGetValue(refno, out found) ? found : null); });
        }
        // PRODUCTION_METHODS
    }
}

internal static class Program
{
    private static int checks;
    private static readonly string[] All = { "spatial", "bounds", "volume" };
    private static Dictionary<string, object> D(object value) { return (Dictionary<string, object>)value; }
    private static List<object> Rows(Dictionary<string, object> report) { return ((IEnumerable)report["rows"]).Cast<object>().ToList(); }
    private static Dictionary<string, object> Field(Dictionary<string, object> report, int row, string field) { return D(D(D(Rows(report)[row])["measurements"])[field]); }
    private static void Check(bool condition, string message) { checks++; if (!condition) throw new Exception(message); }
    private static Dictionary<string, object> Batch(E3d21AiBridge.E3d21Context context, string[] refs = null, string[] names = null, string frame = "WORLD", string scope = "element")
    {
        return D(context.GetMeasurementsBatch(refs ?? new[] { "/E1", "/E2" }, names ?? All, frame, scope));
    }
    private static void Reject(Action action, string message)
    {
        int dispatched = Scenario.Dispatches;
        try { action(); throw new Exception("Accepted invalid " + message); }
        catch (ArgumentException) { Check(Scenario.Dispatches == dispatched, "Invalid " + message + " dispatched SDK work"); }
    }
    private static string Canonical(object value)
    {
        if (value == null) return "null";
        var dictionary = value as IDictionary;
        if (dictionary != null)
            return "{" + string.Join(",", dictionary.Keys.Cast<object>().Select(Convert.ToString).OrderBy(key => key).Select(key => key + ":" + Canonical(dictionary[key]))) + "}";
        var list = value as IEnumerable;
        if (!(value is string) && list != null) return "[" + string.Join(",", list.Cast<object>().Select(Canonical)) + "]";
        return Convert.ToString(value, CultureInfo.InvariantCulture);
    }
    private static void NoReadingStatus(Dictionary<string, object> report)
    {
        foreach (object rowValue in Rows(report))
        {
            var row = D(rowValue);
            Check(!Equals(row["status"], "reading"), "Internal row status escaped");
            foreach (object fieldValue in D(row["measurements"]).Values)
                Check(!Equals(D(fieldValue)["status"], "reading"), "Internal field status escaped");
        }
    }
    private static void InputChecks()
    {
        Scenario.Reset(); var context = new E3d21AiBridge.E3d21Context();
        Reject(() => context.GetMeasurementsBatch(null, All, "WORLD", "element"), "null refs");
        Reject(() => Batch(context, new string[0]), "empty refs");
        Reject(() => Batch(context, Enumerable.Range(1, 33).Select(i => "/E" + i).ToArray()), "33 refs");
        foreach (string value in new[] { "", "CE", "WORLD", "/", "=1/-1", "=x/2", "/A B", "/A\nB" })
            Reject(() => Batch(context, new[] { value }), "ref " + value);
        Reject(() => Batch(context, new[] { "/E1", " /e1 " }), "duplicate refs");
        Reject(() => context.GetMeasurementsBatch(new[] { "/E1" }, null, "WORLD", "element"), "null measurements");
        foreach (string[] names in new[] { new string[0], new[] { "SPATIAL" }, new[] { "spatial " }, new[] { "spatial", "spatial" }, new[] { "spatial", "bounds", "volume", "spatial" } })
            Reject(() => Batch(context, names: names), "measurements");
        foreach (string frame in new[] { "", "CE", "FRAME", "/A B", "=x/2" })
            Reject(() => Batch(context, frame: frame), "frame");
        foreach (string scope in new[] { null, "", "ALL", "ELEMENT", " members " })
            Reject(() => Batch(context, scope: scope), "scope");
        Check(Scenario.Reads.Count == 0, "Rejected inputs caused SDK reads");
    }
    private static void SuccessAndEquivalence()
    {
        Scenario.Reset(); var context = new E3d21AiBridge.E3d21Context();
        var report = Batch(context, new[] { " /E2 ", " =1/1 " }, new[] { "volume", "spatial", "bounds" }, " /FRAME ", "members");
        Check(Scenario.Dispatches == 1 && Equals(report["single_dispatch"], true), "Batch did not use exactly one dispatch");
        Check(Equals(report["success"], true) && Equals(report["complete"], true), "Valid batch failed");
        Check(Equals(D(Rows(report)[0])["requested_refno"], "/E2") && Equals(D(Rows(report)[1])["requested_refno"], "=1/1"), "Input order/trim was lost");
        Check(((List<string>)report["measurements"]).SequenceEqual(new[] { "volume", "spatial", "bounds" }), "Measurement order was lost");
        Check(Scenario.Qualifiers.All(value => value.EndsWith(":ALL")), "Members scope did not use ALL");
        Check(Scenario.Frames.All(value => ReferenceEquals(value, Scenario.Owner)), "Explicit WRT frame was not forwarded");
        Check(Equals(D(Field(report, 0, "bounds")["result"])["coordinate_frame"], "SDK native (unverified)"), "Bounds frame was overstated");
        Check(Equals(report["ce_unchanged"], true) && Equals(report["identity_unchanged"], true), "Read consistency was not verified");
        foreach (string name in All)
        {
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
            var batch = Batch(context, new[] { "/E1" }, new[] { name });
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
            object single = name == "spatial" ? context.GetSpatialInfo("/E1", "WORLD") : name == "bounds" ? context.GetGeometryBounds("/E1") : context.GetGeometryVolume("/E1", "element");
            Check(Canonical(Field(batch, 0, name)["result"]) == Canonical(single), "Single/batch payload differs for " + name);
        }
        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        report = Batch(context, Enumerable.Range(1, 32).Select(i => "/E" + i).ToArray(), new[] { "spatial" }, " owner ");
        Check(Rows(report).Count == 32 && Equals(report["success"], true) && Scenario.Dispatches == 1, "Maximum batch failed");
        Check(Equals(report["wrt"], "OWNER") && Scenario.Frames.All(value => ReferenceEquals(value, Scenario.Owner)), "OWNER was not canonicalized/forwarded");
    }
    private static void PartialAndConsistency()
    {
        Scenario.Reset(); var context = new E3d21AiBridge.E3d21Context();
        Scenario.Overrides["e1.supports.ORI"] = false;
        var report = Batch(context, new[] { "/E1" }, new[] { "spatial" });
        Check(Equals(report["success"], true) && Equals(report["complete"], false), "Unsupported PAVE ORI was misclassified");
        Check(Equals(Field(report, 0, "spatial")["status"], "returned") && ((List<object>)report["read_errors"]).Count == 0, "Unsupported ORI produced an error");
        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        report = Batch(context, new[] { "/MISSING", "/E2" });
        Check(Equals(report["success"], false) && Equals(D(Rows(report)[1])["success"], true), "Unresolved row discarded valid row");
        Check(Equals(Field(report, 0, "spatial")["status"], "not_attempted") && Equals(report["failed_row_count"], 1), "Unresolved row statuses were wrong");
        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        Scenario.Overrides["e1.position.x"] = new InvalidOperationException("position failed");
        report = Batch(context);
        Check(Equals(Field(report, 0, "spatial")["status"], "failed") && Equals(Field(report, 0, "bounds")["status"], "returned") && Equals(Field(report, 0, "volume")["status"], "returned"), "Ordinary field failure stopped later fields");
        Check(Equals(D(D(Field(report, 0, "spatial")["result"])["orientation"])["available"], true), "Ordinary POS failure lost ORI");
        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        Scenario.Overrides["e1.text.CABBOX"] = new InvalidOperationException("native diagnostic failed");
        report = Batch(context, names: new[] { "bounds" });
        Check(Equals(report["success"], false) && Equals(D(Field(report, 0, "bounds")["result"])["available"], true), "Bounds diagnostic failure lost successful limits or was hidden");
        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        Scenario.Overrides["e1.text.GVOL"] = new InvalidOperationException("formatted volume failed");
        report = Batch(context, names: new[] { "volume" });
        Check(Equals(report["success"], false) && Equals(D(D(Field(report, 0, "volume")["result"])["gross"])["available"], true), "Formatted volume diagnostics did not preserve numeric result");
        foreach (string changed in new[] { "ce", "project", "mdb" })
        {
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
            Scenario.OnRead = path => { if (path == "e1.position") { if (changed == "ce") Scenario.Ce = new DbElement(98); else if (changed == "project") Project.Value = new Project(); else MDB.Value = new MDB(); } };
            report = Batch(context, new[] { "/E1" }, new[] { "spatial" });
            Check(Equals(report["success"], false) && Equals(Field(report, 0, "spatial")["success"], true), "Consistency change did not fail batch while preserving result: " + changed);
        }
        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        Scenario.Overrides[Scenario.Ce.Path + ".type.short_name"] = "TUBI";
        report = Batch(context, new[] { "/E1" }, new[] { "spatial" });
        Check(Equals(report["success"], true) && Equals(report["complete"], true) &&
            Equals(report["ce_type_before"], "TUBI") && Equals(report["ce_type_after"], "TUBI"),
            "Stable typed CE did not retain a complete batch");
        foreach (string afterType in new[] { "FLAN", "" })
        {
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
            string cePath = Scenario.Ce.Path;
            Scenario.Overrides[cePath + ".type.short_name"] = "TUBI";
            Scenario.Overrides[cePath + ".type.name"] = "TUBI";
            Scenario.OnRead = path =>
            {
                if (path != "e1.position") return;
                Scenario.Overrides[cePath + ".type.short_name"] = afterType;
                Scenario.Overrides[cePath + ".type.name"] = afterType;
            };
            report = Batch(context, new[] { "/E1" }, new[] { "spatial" });
            var spatial = D(Field(report, 0, "spatial")["result"]);
            var boundary = D(spatial["context"]);
            Check(Equals(report["ce_before"], report["ce_after"]), "Typed CE case unexpectedly changed the DBREF");
            Check(Equals(boundary["ce_unchanged"], afterType == "" ? (object)null : false),
                "Spatial context lost the changed or unavailable CE type");
            Check(Equals(report["success"], false) && Equals(report["complete"], false) &&
                Equals(report["ce_unchanged"], boundary["ce_unchanged"]),
                "Batch completeness ignored the typed CE boundary for " + (afterType == "" ? "unknown type" : "changed type"));
            Check(Equals(report["ce_type_before"], "TUBI") && Equals(report["ce_type_after"], afterType),
                "Batch did not retain the before/after native CE types");
            Check(Equals(Field(report, 0, "spatial")["success"], true) &&
                Equals(D(spatial["position"])["available"], true) &&
                ((List<object>)report["read_errors"]).Any(error => Equals(D(error)["path"], "ce_unchanged")),
                "Typed CE drift discarded numeric evidence or omitted its consistency diagnostic");
        }
    }
    private static void FaultStopsEverySdkCall()
    {
        Scenario.Reset(); var context = new E3d21AiBridge.E3d21Context();
        Batch(context);
        int total = Scenario.Reads.Count;
        for (int faultAt = 1; faultAt <= total; faultAt++)
        {
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
            int trigger = faultAt;
            Scenario.OnRead = path => { if (Scenario.Reads.Count == trigger) context.Fault(); };
            var report = Batch(context);
            Check(Scenario.Reads.Count == trigger, "SDK continued after fault at #" + trigger + " (" + Scenario.Reads[trigger - 1] + "): " + string.Join(",", Scenario.Reads.Skip(trigger)));
            Check(Equals(report["success"], false) && report["native_database_fault"] != null, "Fault was not retained at #" + trigger);
            Check(report["ce_after"] == null && report["ce_unchanged"] == null && report["project_after"] == null && report["mdb_after"] == null && report["identity_unchanged"] == null, "Fault reported post-state at #" + trigger);
            NoReadingStatus(report);
        }
        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        Scenario.OnRead = path => { if (path == "e1.NVOL.value") context.Fault(); };
        var partial = Batch(context);
        Check(Equals(Field(partial, 0, "spatial")["status"], "returned") && Equals(Field(partial, 0, "bounds")["status"], "returned"), "Fault discarded completed measurements");
        Check(Equals(Field(partial, 0, "volume")["status"], "failed") && Equals(D(Rows(partial)[1])["status"], "not_attempted"), "Fault did not stop active/later measurements");
        Check(Scenario.Reads.Last() == "e1.NVOL.value", "Fault caused cleanup reads");
        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        Scenario.AfterOperation = () => context.Fault();
        var postCheck = Batch(context);
        Check(Scenario.Reads.Count == total && Equals(postCheck["success"], false) && postCheck["native_database_fault"] != null, "OnUi post-check fault lost report or performed more SDK reads");
        Check(Rows(postCheck).All(row => Equals(D(row)["success"], true)), "OnUi post-check fault discarded completed rows");
        Check(postCheck["ce_after"] == null && postCheck["identity_unchanged"] == null, "OnUi post-check fault claimed valid post-state");
        NoReadingStatus(postCheck);
        Console.WriteLine("Verified native fault stops at every one of " + total + " SDK call boundaries.");
    }
    private static void SetVolume(int id, DbElementType type, double gross, double net)
    {
        string path = "e" + id;
        if (type != null) Scenario.Overrides[path + ".type"] = type;
        Scenario.Overrides[path + ".GVOL"] = new DbDouble(path + ".GVOL", gross, Scenario.NativeUnits);
        Scenario.Overrides[path + ".NVOL"] = new DbDouble(path + ".NVOL", net, Scenario.NativeUnits);
    }
    private static void CheckNegativeVolume(Dictionary<string, object> result, double gross, double net)
    {
        Check(Equals(result["success"], true) && Equals(result["read_complete"], true), "Negative native read did not succeed");
        Check(Equals(result["volume_pair_applicable"], false) && result["volume_pair_coherent"] == null &&
              result["volume_pair_tolerance_mm3"] == null && result["removed_volume_mm3"] == null, "Negative volume inferred material removal");
        Check(Equals(result["volume_pair_reason"], "negative_primitive_not_material_pair"), "Negative non-applicability reason missing");
        Check((double)D(result["gross"])["value_mm3"] == gross && (double)D(result["net"])["value_mm3"] == net, "Native negative values were changed");
        Check(Equals(D(result["gross"])["available"], true) && Equals(D(result["net"])["available"], true) &&
              Equals(D(result["net"])["unit"], "mm3"), "Native negative availability/units lost");
        Check(((Dictionary<string, string>)result["read_errors"]).Count == 0, "Negative interpretation became a read error");
        Check(((string)result["measurement_scope"]).Contains("do not establish material removed from the owner"), "Owner-removal limitation missing");
    }
    private static void NegativeVolumeSemantics()
    {
        // Three NCYL results from the retained reactor calls 132, 142 and 151.
        // Exercise the actual production reader, not a handcrafted result map.
        double[] recordedNet = { 12844457.566329282, 6745454.132534735, 6745454.132534735 };
        Scenario.Reset(); var context = new E3d21AiBridge.E3d21Context();
        for (int i = 0; i < recordedNet.Length; i++) SetVolume(i + 1, DbElementTypeInstance.CylinderValue, 0, recordedNet[i]);
        var report = Batch(context, new[] { "/E1", "/E2", "/E3" }, new[] { "volume" });
        Check(Equals(report["success"], true) && Equals(report["complete"], true), "Negative batch did not accept native read completion");
        for (int i = 0; i < recordedNet.Length; i++)
        {
            CheckNegativeVolume(D(Field(report, i, "volume")["result"]), 0, recordedNet[i]);
            Check(Equals(Field(report, i, "volume")["complete"], true) && Equals(Field(report, i, "volume")["status"], "returned"), "Negative batch field remained incomplete");
            var single = D(context.GetGeometryVolume("/E" + (i + 1), "element"));
            CheckNegativeVolume(single, 0, recordedNet[i]);
            Check(Canonical(single) == Canonical(Field(report, i, "volume")["result"]), "Negative single/batch result differs");
        }
        foreach (DbElementType negative in new[] { DbElementTypeInstance.CylinderValue, DbElementTypeInstance.BoxValue, DbElementTypeInstance.ConeValue })
        {
            foreach (string scope in new[] { "element", "members" })
            {
                Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
                SetVolume(1, negative, 0, 0);
                CheckNegativeVolume(D(context.GetGeometryVolume("/E1", scope)), 0, 0);
                Check(Scenario.Qualifiers.All(value => value.EndsWith(scope == "element" ? ":ONLY" : ":ALL")), "Negative qualifier changed");
            }
        }
        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        SetVolume(1, null, 100, 120);
        var positive = D(context.GetGeometryVolume("/E1", "element"));
        Check(Equals(positive["read_complete"], true) && Equals(positive["success"], false) &&
              Equals(positive["volume_pair_applicable"], true) && Equals(positive["volume_pair_coherent"], false), "Positive incoherence was suppressed");
        Check(positive["removed_volume_mm3"] == null && ((Dictionary<string, string>)positive["read_errors"]).ContainsKey("volume_pair"), "Positive pair diagnostic lost");
        report = Batch(context, new[] { "/E1" }, new[] { "volume" });
        Check(Equals(report["success"], false) && Equals(report["complete"], false) && Equals(Field(report, 0, "volume")["success"], false), "Batch accepted positive incoherence");
        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        SetVolume(1, null, 100, 80);
        positive = D(context.GetGeometryVolume("/E1", "element"));
        Check(Equals(positive["success"], true) && Equals(positive["volume_pair_coherent"], true) && (double)positive["removed_volume_mm3"] == 20, "Positive removal changed");
        // A similarly named type that is not the SDK base identity is outside
        // the deliberately limited exception, including other negative types.
        foreach (string name in new[] { "NCYLINDER", "NBOX", "NCONE", "NCTORUS", "NOT_A_NEGATIVE" })
        {
            Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
            SetVolume(1, new DbElementType("impostor"), 0, 10);
            Scenario.Overrides["impostor.type.name"] = name;
            positive = D(context.GetGeometryVolume("/E1", "element"));
            Check(Equals(positive["success"], false) && Equals(positive["volume_pair_applicable"], true), "Name heuristics broadened negative exception: " + name);
        }
        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        SetVolume(1, DbElementTypeInstance.CylinderValue, 0, 10);
        Scenario.Overrides["e1.NVOL"] = new InvalidOperationException("volume unavailable");
        var partial = D(context.GetGeometryVolume("/E1", "element"));
        Check(Equals(partial["success"], false) && Equals(partial["read_complete"], false) &&
              Equals(partial["volume_pair_applicable"], false) && partial["removed_volume_mm3"] == null, "Negative read failure was hidden");
        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        Scenario.Overrides["e1.type"] = new InvalidOperationException("base type unavailable");
        bool typeThrew = false;
        try { context.GetGeometryVolume("/E1", "element"); }
        catch (InvalidOperationException ex) { typeThrew = ex.Message == "base type unavailable"; }
        Check(typeThrew && !Scenario.Reads.Contains("e1.GVOL"), "Failed type read guessed semantics or continued to volume");
        // In a batch, the first type read is row identity and the second is
        // classification inside the real volume reader. Fault exactly there.
        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); int typeReads = 0;
        Scenario.OnRead = path => { if (path == "e1.type" && ++typeReads == 2) context.Fault(); };
        report = Batch(context, names: new[] { "volume" });
        Check(typeReads == 2 && Scenario.Reads.Last() == "e1.type" && !Scenario.Reads.Contains("e1.GVOL"), "Type classification fault did not stop SDK reads");
        Check(Equals(report["success"], false) && report["native_database_fault"] != null &&
              Equals(D(Rows(report)[1])["status"], "not_attempted"), "Type classification native fault was swallowed");
        NoReadingStatus(report);
        Console.WriteLine("Standalone negative volume production checks passed.");
    }
    private static void GeometryRelationProductionChecks()
    {
        Scenario.Reset(); var context = new E3d21AiBridge.E3d21Context();
        Scenario.Overrides["exact.1.2.False"] = true;
        Scenario.Overrides["box.1.2"] = false;
        var result = D(context.CheckGeometryRelation(" /E1 ", " =1/2 ", true));
        var exact = D(result["exact"]); var box = D(result["box"]);
        Check(Scenario.Dispatches == 1 && Equals(result["complete"], true) && Equals(result["ce_unchanged"], true),
            "Geometry relation did not complete in one stable UI dispatch");
        Check(Equals(D(result["test"])["refno"], "=1/1") && Equals(D(result["against"])["refno"], "=1/2"),
            "Geometry relation reordered or changed the explicit pair");
        Check(Equals(exact["available"], true) && Equals(exact["value"], true) &&
              Equals(exact["b_fully"], false) && Equals(exact["predicate"], "Spatial.ElementInElementExact(test, against, false)"),
            "Exact SDK predicate or fixed bFully=false was not retained");
        Check(Equals(box["available"], true) && Equals(box["value"], false) &&
              Equals(box["predicate"], "Spatial.ElementInElementBox(test, against)"),
            "Optional box result was not retained as the raw native boolean");
        Check(Scenario.RelationCalls.SequenceEqual(new[] { "exact.1.2.False", "box.1.2" }),
            "Native relation method parameter order or invocation count changed");
        Check(((string)result["measurement_scope"]).Contains("not a clash/clear"),
            "Raw booleans were upgraded to a clash/clear verdict");

        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        Scenario.Overrides["exact.1.2.False"] = false;
        result = D(context.CheckGeometryRelation("/E1", "/E2", false));
        exact = D(result["exact"]); box = D(result["box"]);
        Check(Equals(result["complete"], true) && Equals(exact["available"], true) && Equals(exact["value"], false),
            "Native false result incorrectly failed the read or became unknown");
        Check(Equals(box["status"], "not_requested") && box["value"] == null && !Scenario.RelationCalls.Any(call => call.StartsWith("box.")),
            "Optional box predicate ran when it was not requested");

        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        Scenario.Overrides["exact.1.2.False"] = new InvalidOperationException("exact unavailable");
        result = D(context.CheckGeometryRelation("/E1", "/E2", true));
        exact = D(result["exact"]); box = D(result["box"]);
        Check(Equals(result["success"], true) && Equals(result["complete"], false) && Equals(result["partial"], true),
            "Ordinary predicate failure was not retained as an incomplete report");
        Check(Equals(exact["available"], false) && exact["value"] == null && Equals(exact["error"], "exact unavailable") &&
              Equals(box["available"], true), "Exact error suppressed independent box evidence");

        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        Scenario.OnRead = path => { if (path == "exact.1.2.False") Scenario.Ce = new DbElement(98); };
        result = D(context.CheckGeometryRelation("/E1", "/E2", false));
        Check(Equals(result["success"], true) && Equals(result["ce_unchanged"], false) &&
              Equals(result["complete"], false) && Equals(result["partial"], true), "CE drift was not preserved");

        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        bool invalidThrew = false;
        try { context.CheckGeometryRelation("CE", "/E2", false); }
        catch (ArgumentException) { invalidThrew = true; }
        Check(invalidThrew && Scenario.Dispatches == 0 && Scenario.RelationCalls.Count == 0,
            "Implicit current-element selection reached native geometry queries");

        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); Scenario.Busy = true;
        bool busyThrew = false;
        try { context.CheckGeometryRelation("/E1", "/E2", true); }
        catch (InvalidOperationException) { busyThrew = true; }
        Check(busyThrew && Scenario.Dispatches == 0 && Scenario.RelationCalls.Count == 0,
            "Busy E3D state allowed geometry relation dispatch");

        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        Scenario.OnRead = path => { if (path == "exact.1.2.False") context.Fault(); };
        bool nativeFaultThrew = false;
        try { context.CheckGeometryRelation("/E1", "/E2", true); }
        catch (InvalidOperationException) { nativeFaultThrew = true; }
        Check(nativeFaultThrew && Scenario.RelationCalls.SequenceEqual(new[] { "exact.1.2.False" }) &&
              Scenario.Reads.Last() == "exact.1.2.False" && !Scenario.RelationCalls.Any(call => call.StartsWith("box.")),
            "Native database fault was swallowed or later geometry calls continued");
        Console.WriteLine("Geometry relation production checks passed.");
    }
    private static void GeometryRelationBatchProductionChecks()
    {
        Scenario.Reset(); var context = new E3d21AiBridge.E3d21Context();
        Scenario.Overrides["exact.1.2.False"] = false;
        Scenario.Overrides["box.1.2"] = true;
        Scenario.Overrides["exact.2.3.False"] = true;
        Scenario.Overrides["box.2.3"] = false;
        var report = D(context.CheckGeometryRelationBatch(new[]
        {
            new[] { " /E1 ", "=1/2" }, new[] { "=1/2", "/E3" }
        }, true));
        var rows = Rows(report);
        Check(Scenario.Dispatches == 1 && Equals(report["single_dispatch"], true) &&
              Equals(report["complete"], true) && Equals(report["complete_pair_count"], 2),
            "Geometry relation batch did not run as one complete dispatch");
        Check(Equals(D(rows[0])["requested_test_refno"], "/E1") &&
              Equals(D(rows[0])["requested_against_refno"], "=1/2") &&
              Equals(D(rows[1])["requested_test_refno"], "=1/2"),
            "Geometry relation batch reordered or failed to normalize ordered pairs");
        Check(Equals(D(D(rows[0])["exact"])["value"], false) &&
              Equals(D(D(rows[1])["exact"])["value"], true) &&
              Equals(D(D(rows[0])["exact"])["b_fully"], false) &&
              Equals(D(D(rows[0])["box"])["value"], true) &&
              Equals(D(D(rows[1])["box"])["value"], false),
            "Geometry relation batch did not retain raw native predicate results");
        Check(Scenario.RelationCalls.SequenceEqual(new[]
            { "exact.1.2.False", "box.1.2", "exact.2.3.False", "box.2.3" }),
            "Geometry relation batch changed pair order or native predicate ordering");

        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        Scenario.Overrides["resolve./MISSING"] = null;
        report = D(context.CheckGeometryRelationBatch(new[]
        {
            new[] { "/E1", "/E2" }, new[] { "/MISSING", "/E3" }, new[] { "/E2", "/E3" }
        }, false));
        rows = Rows(report);
        Check(Equals(report["success"], false) && Equals(report["complete"], false) &&
              Equals(report["failed_pair_count"], 1) && Equals(D(rows[2])["complete"], true),
            "Ordinary pair-resolution failure suppressed or hid later results");
        Check(Scenario.RelationCalls.SequenceEqual(new[] { "exact.1.2.False", "exact.2.3.False" }) &&
              Equals(D(D(rows[1])["exact"])["value"], null),
            "Failed pair fabricated a result or later pairs did not continue");

        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        Scenario.OnRead = path => { if (path == "exact.2.3.False") context.Fault(); };
        report = D(context.CheckGeometryRelationBatch(new[]
        {
            new[] { "/E1", "/E2" }, new[] { "/E2", "/E3" }, new[] { "/E3", "/E4" }
        }, true));
        rows = Rows(report);
        Check(Equals(report["success"], false) && Equals(report["partial"], true) &&
              report["native_database_fault"] != null && Equals(D(rows[0])["success"], true) &&
              Equals(D(D(rows[0])["exact"])["available"], true) &&
              Equals(D(rows[0])["complete"], false) && Equals(D(rows[1])["complete"], false) &&
              report["ce_unchanged"] == null,
            "Native fault did not preserve completed and current partial pair evidence");
        Check(Scenario.RelationCalls.SequenceEqual(new[] { "exact.1.2.False", "box.1.2", "exact.2.3.False" }) &&
              !Scenario.RelationCalls.Contains("exact.3.4.False"),
            "Native database fault did not stop later native geometry calls");
        Check(Equals(D(D(rows[1])["exact"])["status"], "read_failed") &&
              Equals(D(D(rows[1])["box"])["status"], "not_attempted") &&
              Equals(D(D(rows[2])["exact"])["status"], "not_attempted"),
            "Native fault did not distinguish failed and unattempted predicates");

        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        Scenario.OnRead = path => { if (path == "box.1.2") context.Fault(); };
        report = D(context.CheckGeometryRelationBatch(new[] { new[] { "/E1", "/E2" } }, true));
        rows = Rows(report);
        Check(Equals(D(D(rows[0])["exact"])["value"], false) && D(D(rows[0])["exact"])["error"] == null &&
              Equals(D(D(rows[0])["box"])["status"], "read_failed"),
            "Box fault failed to retain the earlier exact value as valid raw evidence");

        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        Scenario.AfterOperation = () => context.Fault();
        report = D(context.CheckGeometryRelationBatch(new[] { new[] { "/E1", "/E2" } }, false));
        rows = Rows(report);
        Check(Equals(report["success"], false) && Equals(report["complete_pair_count"], 0) &&
              report["ce_unchanged"] == null && Equals(D(rows[0])["complete"], false) &&
              Equals(D(D(rows[0])["exact"])["value"], false) &&
              Equals(D(((List<object>)report["errors"])[0])["path"], "dispatch.after"),
            "Post-dispatch fault left stale completeness or discarded the raw result");

        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        Scenario.OnRead = path => { if (path == "exact.1.2.False") Scenario.Ce = new DbElement(98); };
        report = D(context.CheckGeometryRelationBatch(new[] { new[] { "/E1", "/E2" }, new[] { "/E2", "/E3" } }, false));
        Check(Equals(report["ce_unchanged"], false) && Equals(report["complete"], false) &&
              Rows(report).All(row => Equals(D(row)["complete"], false)),
            "CE drift was not applied to every batch-pair completeness result");

        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context();
        bool invalidThrew = false;
        try { context.CheckGeometryRelationBatch(new[] { new[] { "CE", "/E2" } }, false); }
        catch (ArgumentException) { invalidThrew = true; }
        bool sizeThrew = false;
        try { context.CheckGeometryRelationBatch(Enumerable.Range(0, 33).Select(value => new[] { "/E1", "/E2" }).ToArray(), false); }
        catch (ArgumentException) { sizeThrew = true; }
        Check(invalidThrew && sizeThrew && Scenario.Dispatches == 0 && Scenario.RelationCalls.Count == 0,
            "Invalid or oversized geometry relation batch reached E3D");

        Scenario.Reset(); context = new E3d21AiBridge.E3d21Context(); Scenario.Busy = true;
        bool busyThrew = false;
        try { context.CheckGeometryRelationBatch(new[] { new[] { "/E1", "/E2" } }, false); }
        catch (InvalidOperationException) { busyThrew = true; }
        Check(busyThrew && Scenario.Dispatches == 0 && Scenario.RelationCalls.Count == 0,
            "Busy E3D state allowed geometry relation batch dispatch");
        Console.WriteLine("Geometry relation batch production checks passed.");
    }
    private static int Main()
    {
        try
        {
            InputChecks(); SuccessAndEquivalence(); PartialAndConsistency(); NegativeVolumeSemantics(); FaultStopsEverySdkCall(); GeometryRelationProductionChecks(); GeometryRelationBatchProductionChecks();
            Console.WriteLine("Batch measurement production checks passed: " + checks); return 0;
        }
        catch (Exception ex) { Console.Error.WriteLine(ex); return 1; }
    }
}
