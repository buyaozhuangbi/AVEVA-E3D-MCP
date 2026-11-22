// Recording managed SDK fakes only. No AVEVA assembly or E3D process is loaded.
using System;
using System.Collections.Generic;
using System.Linq;
using Aveva.Core.Database;
using E3d21AiBridge;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Aveva.Core.Database
{
    public enum DbType { Design = 1, Catalogue = 2, Catalog = 2, Property = 4, Draft = 7, Dictionary = 8 }
    public enum DbAttributeType { INTEGER = 1, DOUBLE, BOOL, STRING, ELEMENT, WORD, DIRECTION, POSITION }
    public enum DbAttributeUnit { NONE, DIST, BORE, PRES, TEMP }
    public sealed class Project
    {
        public static bool operator ==(Project a, Project b) { SpecScenario.Forbidden("Project equality operator"); return false; }
        public static bool operator !=(Project a, Project b) { SpecScenario.Forbidden("Project inequality operator"); return false; }
        public override bool Equals(object other) => ReferenceEquals(this, other);
        public override int GetHashCode() => base.GetHashCode();
        public static Project CurrentProject => SpecScenario.Read("project.current", SpecScenario.Project);
        public string Name => SpecScenario.Read("project.name", "OFFLINE-PROJECT");
    }
    public sealed class MDB
    {
        public static bool operator ==(MDB a, MDB b) { SpecScenario.Forbidden("MDB equality operator"); return false; }
        public static bool operator !=(MDB a, MDB b) { SpecScenario.Forbidden("MDB inequality operator"); return false; }
        public override bool Equals(object other) => ReferenceEquals(this, other);
        public override int GetHashCode() => base.GetHashCode();
        public static MDB CurrentMDB => SpecScenario.Read("mdb.current", SpecScenario.Mdb);
        public string Name => SpecScenario.Read("mdb.name", "OFFLINE-MDB");
        public DbElement FindElement(DbType type, string name)
        {
            DbElement value;
            return SpecScenario.Read("find." + (int)type + "." + name,
                SpecScenario.Names.TryGetValue((int)type + "|" + name, out value) ? value : null);
        }
        public DbElement FindElement(string name) { SpecScenario.Forbidden("untyped name lookup"); return null; }
        public Db[] GetDBArray() { SpecScenario.Forbidden("database array scan"); return null; }
    }
    public static class CurrentElement
    {
        public static DbElement Element
        {
            get => SpecScenario.Read("ce", SpecScenario.Ce);
            set => SpecScenario.Forbidden("set CE");
        }
    }
    public sealed class Db
    {
        public static bool operator ==(Db a, Db b) { SpecScenario.Forbidden("Db equality operator"); return false; }
        public static bool operator !=(Db a, Db b) { SpecScenario.Forbidden("Db inequality operator"); return false; }
        public override bool Equals(object other) => ReferenceEquals(this, other);
        public override int GetHashCode() => base.GetHashCode();
        internal int DatabaseNumber;
        internal DbType DatabaseType;
        private string Path => "db." + DatabaseNumber;
        public int Number => SpecScenario.Read(Path + ".number", DatabaseNumber);
        public string Name => SpecScenario.Read(Path + ".name", "SUPPORT/DB-" + DatabaseNumber);
        public DbType Type => SpecScenario.Read(Path + ".type", DatabaseType);
    }
    public sealed class DbElementType
    {
        public static bool operator ==(DbElementType a, DbElementType b) { SpecScenario.Forbidden("DbElementType equality operator"); return false; }
        public static bool operator !=(DbElementType a, DbElementType b) { SpecScenario.Forbidden("DbElementType inequality operator"); return false; }
        public override bool Equals(object other) => ReferenceEquals(this, other);
        public override int GetHashCode() => base.GetHashCode();
        internal string Kind;
        public static DbElementType GetElementType(string name) => SpecScenario.Read("type.resolve." + name, new DbElementType { Kind = name });
        public bool IsValid => SpecScenario.Read("type." + Kind + ".valid", true);
        public string ShortName => SpecScenario.Read("type." + Kind + ".short", Kind);
    }
    public sealed class DbAttribute
    {
        public static bool operator ==(DbAttribute a, DbAttribute b) { SpecScenario.Forbidden("DbAttribute equality operator"); return false; }
        public static bool operator !=(DbAttribute a, DbAttribute b) { SpecScenario.Forbidden("DbAttribute inequality operator"); return false; }
        public override bool Equals(object other) => ReferenceEquals(this, other);
        public override int GetHashCode() => base.GetHashCode();
        internal string Key;
        internal DbAttributeType DataType;
        internal DbAttributeUnit Category;
        internal bool Array;
        public static DbAttribute GetDbAttribute(string name)
        {
            DbAttribute value;
            return SpecScenario.Read("attribute." + name + ".resolve", SpecScenario.Attributes.TryGetValue(name, out value) ? value : null);
        }
        public bool IsValid => SpecScenario.Read("attribute." + Key + ".valid", true);
        public DbAttributeType Type => SpecScenario.Read("attribute." + Key + ".type", DataType);
        public bool IsArray => SpecScenario.Read("attribute." + Key + ".array", Array);
        public DbAttributeUnit Units => SpecScenario.Read("attribute." + Key + ".units", Category);
    }
    public static class DbAttributeInstance
    {
        public static readonly DbAttribute NAME = new DbAttribute { Key = "NAME", DataType = DbAttributeType.STRING };
    }
    public sealed class DbElement
    {
        public static bool operator ==(DbElement a, DbElement b) { SpecScenario.Forbidden("DbElement equality operator"); return false; }
        public static bool operator !=(DbElement a, DbElement b) { SpecScenario.Forbidden("DbElement inequality operator"); return false; }
        public override bool Equals(object other) => ReferenceEquals(this, other);
        public override int GetHashCode() => base.GetHashCode();
        internal int DatabaseNumber, Id;
        internal string Kind, NativeName;
        internal DbType DatabaseType;
        internal DbElement NativeOwner;
        internal readonly List<DbElement> Children = new List<DbElement>();
        internal readonly Dictionary<string, object> Values = new Dictionary<string, object>();
        internal string Key => "=" + DatabaseNumber + "/" + Id;
        internal string Path => Key + "|" + Kind;
        public static DbElement GetElement(int[] pair)
        {
            DbElement value;
            string key = "=" + pair[0] + "/" + pair[1];
            return SpecScenario.Read("get." + key, SpecScenario.Elements.TryGetValue(key, out value) ? value : null);
        }
        public static DbElement GetElement(int[] pair, DbElementType type)
        {
            SpecScenario.Forbidden("unverified typed numeric alias resolution"); return null;
        }
        public bool IsValid => SpecScenario.Read(Path + ".valid", true);
        public bool IsNull => SpecScenario.Read(Path + ".null", false);
        public bool IsDeleted => SpecScenario.Read(Path + ".deleted", false);
        public int[] Ref => SpecScenario.Read(Path + ".ref", new[] { DatabaseNumber, Id });
        public DbType DbType => SpecScenario.Read(Path + ".database_type", DatabaseType);
        public DbElementType GetElementType() => SpecScenario.Read(Path + ".type", new DbElementType { Kind = Kind });
        public Db Db => SpecScenario.Read(Path + ".db", new Db { DatabaseNumber = DatabaseNumber, DatabaseType = DatabaseType });
        public DbElement Owner => SpecScenario.Read(Path + ".owner", NativeOwner);
        public DbElement Member(int ordinal) => SpecScenario.Read(Path + ".member." + ordinal,
            ordinal <= Children.Count ? Children[ordinal - 1] : null);
        public DbElement[] Members() { SpecScenario.Forbidden("materialize members"); return null; }
        public bool IsAttributeValid(DbAttribute attribute) => SpecScenario.Read(Path + ".supports." + attribute.Key, Values.ContainsKey(attribute.Key));
        public string GetAsString(DbAttribute attribute)
        {
            string text;
            if (attribute.Key == "NAME") text = NativeName;
            else if (!Values.ContainsKey(attribute.Key)) throw new InvalidOperationException("fake missing formatted value");
            else if (Values[attribute.Key] == null) text = "Unset";
            else if (Values[attribute.Key] is DbElement reference) text = reference.NativeName;
            else text = Convert.ToString(Values[attribute.Key], System.Globalization.CultureInfo.InvariantCulture);
            return SpecScenario.Read(Path + ".formatted." + attribute.Key, text);
        }
        public DbElement GetElement(DbAttribute attribute) => SpecScenario.Read(Path + ".reference." + attribute.Key, (DbElement)Values[attribute.Key]);
        public double GetDouble(DbAttribute attribute) => SpecScenario.Read(Path + ".double." + attribute.Key, (double)Values[attribute.Key]);
        public int GetInteger(DbAttribute attribute) => SpecScenario.Read(Path + ".integer." + attribute.Key, (int)Values[attribute.Key]);
        public bool GetBool(DbAttribute attribute) => SpecScenario.Read(Path + ".bool." + attribute.Key, (bool)Values[attribute.Key]);
        public string GetString(DbAttribute attribute) => SpecScenario.Read(Path + ".string." + attribute.Key, (string)Values[attribute.Key]);
    }
}

internal static class SpecScenario
{
    internal static readonly Dictionary<string, DbElement> Elements = new Dictionary<string, DbElement>();
    internal static readonly Dictionary<string, DbElement> TypedElements = new Dictionary<string, DbElement>();
    internal static readonly Dictionary<string, DbElement> Names = new Dictionary<string, DbElement>();
    internal static readonly Dictionary<string, DbAttribute> Attributes = new Dictionary<string, DbAttribute>();
    internal static readonly Dictionary<string, object> Overrides = new Dictionary<string, object>();
    internal static readonly List<string> Reads = new List<string>();
    internal static Action<string> OnRead;
    internal static Project Project;
    internal static MDB Mdb;
    internal static DbElement Root, Ce, Spec, World;
    internal static int Dispatches, UiDepth, ForbiddenCalls;
    internal static bool Busy, BusyOnUi;
    internal static T Read<T>(string key, T fallback)
    {
        if (UiDepth != 1) Forbidden("SDK read outside a single UI dispatch: " + key);
        Reads.Add(key);
        OnRead?.Invoke(key);
        if (!Overrides.TryGetValue(key, out object value)) return fallback;
        if (value is Exception error) throw error;
        return (T)value;
    }
    internal static void Forbidden(string message) { ForbiddenCalls++; throw new InvalidOperationException("Forbidden: " + message); }
    internal static DbElement Add(int database, int id, string kind, DbType type, string name = null)
    {
        var value = new DbElement { DatabaseNumber = database, Id = id, Kind = kind, DatabaseType = type,
            NativeName = name ?? "/" + kind + "-" + id };
        if (!Elements.ContainsKey(value.Key)) Elements[value.Key] = value;
        TypedElements[value.Path] = value;
        Names[(int)type + "|" + value.NativeName] = value;
        return value;
    }
    internal static DbElement Child(DbElement parent, DbElement child)
    { parent.Children.Add(child); child.NativeOwner = parent; return child; }
    internal static void Attribute(string key, DbAttributeType type, DbAttributeUnit units = DbAttributeUnit.NONE, bool array = false)
    { Attributes[key] = new DbAttribute { Key = key, DataType = type, Category = units, Array = array }; }
    internal static void Reset()
    {
        Elements.Clear(); TypedElements.Clear(); Names.Clear(); Attributes.Clear(); Overrides.Clear(); Reads.Clear();
        OnRead = null; Dispatches = UiDepth = ForbiddenCalls = 0; Busy = BusyOnUi = false;
        Project = new Project(); Mdb = new MDB();
        World = Add(1, 0, "WORL", DbType.Design);
        Ce = Add(1, 900, "EQUI", DbType.Design, "/UNCHANGED-CE");
        Root = Add(1, 10, "BRAN", DbType.Design, "/EXPLICIT-BRANCH"); Root.NativeOwner = World;
        Spec = Add(2, 10, "SPEC", DbType.Catalogue); Spec.NativeOwner = Add(2, 0, "WORL", DbType.Catalogue);
        foreach (string key in new[] { "PSPEC", "TSPEC", "ISPEC", "SPREF", "CATR", "MATR", "GMRE", "PTRE", "DETR" })
            Attribute(key, DbAttributeType.ELEMENT);
        foreach (string key in new[] { "PSPEC", "TSPEC", "ISPEC" }) Root.Values[key] = Spec;
        Attribute("BORE", DbAttributeType.DOUBLE, DbAttributeUnit.BORE); Root.Values["BORE"] = 100d;
        Attribute("COUNT", DbAttributeType.INTEGER); Root.Values["COUNT"] = 0;
        Attribute("FLAG", DbAttributeType.BOOL); Root.Values["FLAG"] = false;
        Attribute("DESC", DbAttributeType.STRING); Root.Values["DESC"] = "";
        Attribute("PARA", DbAttributeType.DOUBLE, DbAttributeUnit.DIST, true); Root.Values["PARA"] = 10d;
        Attribute("POS", DbAttributeType.POSITION, DbAttributeUnit.DIST); Root.Values["POS"] = "native position";
    }
}

namespace E3d21AiBridge
{
    // EquipmentManifestRequest's unrelated mutation-input dependency is never
    // called by the pure traversal helper used here.
    internal static class ModelEditInput
    {
        internal static string ExplicitReference(string text, string field)
        { SpecScenario.Forbidden("equipment request validation"); return null; }
    }
    public sealed partial class E3d21Context
    {
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;
        private readonly Action<Action> invokeOnUi = action =>
        {
            SpecScenario.Dispatches++; SpecScenario.UiDepth++;
            try { action(); } finally { SpecScenario.UiDepth--; }
        };
        internal void Fault() => nativeFaultState.Observe("Unhandled Database error 538 from PipingSpecFake", "");
        // Ensure a bare CurrentElement.Element would bind to this method and
        // fail compilation, matching the real partial production context.
        private static DbElement CurrentElement() { SpecScenario.Forbidden("legacy CE helper"); return null; }
        private bool IsCommandActive()
        {
            if (nativeFaultState.HasFault) throw new NativeDatabaseFaultException();
            return SpecScenario.Busy || SpecScenario.UiDepth != 0 && SpecScenario.BusyOnUi;
        }
        // PRODUCTION_HELPERS
    }
}

internal static class PipingSpecificationReadHarness
{
    private static int Checks;
    private const string Session = "11111111-1111-4111-8111-111111111111";
    private static readonly List<JObject> Receipts = new List<JObject>();
    private static readonly string[] Links = { "PSPEC", "TSPEC", "ISPEC" };
    private static void Check(bool value, string reason)
    {
        Checks++;
        if (!value) throw new Exception(reason + "\nReads: " + string.Join(", ", SpecScenario.Reads));
    }
    private static JObject Read(string[] attributes = null, int depth = 2, int limit = 32,
        string reference = null, string expected = null, string database = null, E3d21Context context = null)
    {
        string requestedRef = reference ?? SpecScenario.Root.Key;
        string expectedType = expected ?? SpecScenario.Root.Kind;
        string databaseType = database ?? (SpecScenario.Root.DatabaseType == DbType.Catalogue ? "Catalogue" : SpecScenario.Root.DatabaseType.ToString());
        string[] fields = attributes ?? Links;
        object value = (context ?? new E3d21Context()).ReadPipingSpecification(requestedRef, expectedType, databaseType,
            depth, limit, fields, Session);
        Check(SpecScenario.Dispatches == 1 && SpecScenario.ForbiddenCalls == 0, "one read-only dispatch");
        var result = JObject.FromObject(value);
        var request = PipingSpecificationReadRequest.Validate(requestedRef, expectedType, databaseType, depth, limit, fields, Session);
        Receipts.Add(JObject.FromObject(new {
            payload = new { refno = request.Scope.Refno, expected_type = request.ExpectedType, database_type = request.DatabaseType,
                max_depth = request.Scope.MaxDepth, limit = request.Scope.Limit, attributes = request.Scope.Attributes },
            // The native result is production code. This harness supplies the
            // HTTP wrapper independently from the same assembly's actual MVID.
            reply = new { ok = true, version = "E3D21", session_id = Session,
                bridge_build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D"), result }
        }));
        return result;
    }
    private static JToken Field(JObject result, string name, int row = 0) => result["rows"][row]["attributes"][name];
    private static bool Unknown(JToken value) => value != null && (string)value["status"] == "unknown" && value["typed_value"].Type == JTokenType.Null;
    private static void Reject(Action action, bool noDispatch = false)
    {
        bool rejected = false;
        try { action(); } catch (ArgumentException) { rejected = true; } catch (InvalidOperationException) { rejected = true; }
        Check(rejected, "invalid request/identity was accepted");
        if (noDispatch) Check(SpecScenario.Dispatches == 0 && SpecScenario.Reads.Count == 0, "invalid input reached SDK");
    }
    private static void InputsAndIdentity()
    {
        foreach (string reference in new[] { "", "CE", "/*", "/X?", "/EXPLICIT-BRANCH", "//EXPLICIT-BRANCH", "=0/1", "=1/-1",
            "=2147483648/1", "=1/2147483648", "=+1/1", "=1/+1", "=1/\n1", "=1 /1", "/A B" })
        {
            SpecScenario.Reset();
            Reject(() => Read(reference: reference), true);
        }
        foreach (Action action in new Action[] {
            () => Read(expected: ""), () => Read(expected: "BRAN;DELETE"), () => Read(database: "All"),
            () => Read(database: "Draft"), () => Read(database: ""), () => Read(limit: 0), () => Read(limit: 101),
            () => Read(depth: 0), () => Read(depth: 17), () => Read(attributes: new[] { "PSPEC", "pspec" }),
            () => Read(attributes: new[] { "NAME;DELETE" }),
            () => Read(attributes: Enumerable.Range(0, 11).Select(i => "A" + i).ToArray()),
            () => Read(attributes: Enumerable.Range(0, 6).Select(i => "A" + i).ToArray(), limit: 100) })
        { SpecScenario.Reset(); Reject(action, true); }
        SpecScenario.Reset(); Reject(() => Read(expected: "ELBO"));
        SpecScenario.Reset(); Reject(() => Read(database: "Catalogue"));
        SpecScenario.Reset(); SpecScenario.Overrides["get.=1/10"] = SpecScenario.Spec; Reject(() => Read());
        SpecScenario.Reset();
        JObject numeric = Read(reference: "  =0001/00010  ", expected: " bran ", database: " design ");
        Check((bool)numeric["complete"] && (string)numeric["requested_refno"] == "=1/10"
            && SpecScenario.Reads.Contains("get.=1/10") && !SpecScenario.Reads.Any(key => key.StartsWith("find.")),
            "explicit numeric root normalizes without name lookup");
        foreach (int number in new[] { 0, int.MaxValue })
        {
            SpecScenario.Reset();
            SpecScenario.Root = SpecScenario.Add(int.MaxValue, number, "BRAN", DbType.Design);
            SpecScenario.Root.NativeOwner = SpecScenario.World;
            JObject edge = Read(attributes: new string[0]);
            Check((bool)edge["complete"] && (string)edge["requested_refno"] == "=" + int.MaxValue + "/" + number,
                "Int32 numeric database and nonnegative element boundaries");
        }
        SpecScenario.Reset();
        var tube = SpecScenario.Child(SpecScenario.Root, SpecScenario.Add(1, 10, "TUBI", DbType.Design));
        var elbow = SpecScenario.Child(SpecScenario.Root, SpecScenario.Add(1, 11, "ELBO", DbType.Design));
        JObject typed = Read(attributes: new string[0]);
        Check((int)typed["returned_count"] == 3 && (string)typed["rows"][0]["identity_key"] == "=1/10|BRAN"
            && (string)typed["rows"][1]["identity_key"] == "=1/10|TUBI"
            && (int)typed["rows"][1]["sibling_index"] == 1, "shared DBREF typed membership preserved");
        SpecScenario.Reset(); tube = SpecScenario.Add(1, 10, "TUBI", DbType.Design); tube.NativeOwner = SpecScenario.Root;
        Reject(() => Read(attributes: new string[0], expected: "TUBI"));
        Check(!SpecScenario.Reads.Any(key => key.StartsWith("get.typed.")) && SpecScenario.ForbiddenCalls == 0,
            "a shared numeric anchor is not promoted to implicit TUBI");
    }
    private static void ReferencesAndUnknowns()
    {
        SpecScenario.Reset(); JObject baseline = Read();
        Check((bool)baseline["complete"] && (bool)baseline["read_only"] && !(bool)baseline["atomic_snapshot"], "baseline complete observation");
        foreach (string name in Links)
        {
            JToken value = Field(baseline, name);
            Check((string)value["typed_value"]["identity_key"] == "=2/10|SPEC"
                && (int)value["typed_value"]["database"]["number"] == 2
                && value["unit"].Type == JTokenType.Null && (string)value["unit_status"] == "unknown", "typed reference, database and unit evidence");
        }
        Check(!SpecScenario.Reads.Any(key => key.StartsWith("=2/10|SPEC.member.") || key.StartsWith("=2/10|SPEC.reference.")), "reference targets never expanded");
        Check(baseline["scope"]["engineering_compatible"].Type == JTokenType.Null
            && baseline["scope"]["material_selected"].Type == JTokenType.Null, "no engineering verdict or material choice");

        SpecScenario.Reset();
        var component = SpecScenario.Child(SpecScenario.Root, SpecScenario.Add(1, 11, "ELBO", DbType.Design));
        var spco = SpecScenario.Add(2, 20, "SPCO", DbType.Catalogue); spco.NativeOwner = SpecScenario.Spec;
        var scom = SpecScenario.Add(2, 30, "SCOM", DbType.Catalogue); scom.NativeOwner = SpecScenario.Spec;
        component.Values["SPREF"] = spco; spco.Values["CATR"] = scom;
        scom.Values["GMRE"] = SpecScenario.Add(2, 40, "GMSE", DbType.Catalogue);
        scom.Values["PTRE"] = SpecScenario.Add(2, 50, "PTSE", DbType.Catalogue);
        SpecScenario.Root = component;
        JObject member = Read(attributes: new[] { "SPREF" });
        Check((bool)member["complete"] && (string)Field(member, "SPREF")["typed_value"]["identity_key"] == "=2/20|SPCO"
            && !SpecScenario.Reads.Contains("=2/20|SPCO.reference.CATR"), "SPREF records SPCO without following CATR");
        SpecScenario.Reads.Clear(); SpecScenario.Dispatches = 0; SpecScenario.Root = spco;
        JObject catalogue = Read(attributes: new[] { "CATR" });
        Check((bool)catalogue["complete"] && (string)Field(catalogue, "CATR")["typed_value"]["identity_key"] == "=2/30|SCOM"
            && !SpecScenario.Reads.Contains("=2/30|SCOM.reference.GMRE"), "explicit SPCO read records SCOM without expanding it");
        SpecScenario.Reads.Clear(); SpecScenario.Dispatches = 0; SpecScenario.Root = scom;
        JObject geometry = Read(attributes: new[] { "GMRE", "PTRE" });
        Check((bool)geometry["complete"] && (string)Field(geometry, "GMRE")["typed_value"]["type"] == "GMSE"
            && (string)Field(geometry, "PTRE")["typed_value"]["type"] == "PTSE", "explicit SCOM read retains separate geometry/point set identities");

        SpecScenario.Reset(); SpecScenario.Root.Values["ISPEC"] = null;
        JObject result = Read();
        Check(!(bool)result["complete"] && Unknown(Field(result, "ISPEC")) && (bool)Field(result, "ISPEC")["reference_null"]
            && (string)Field(result, "ISPEC")["formatted_value"] == "Unset", "null reference remains unknown with formatted evidence");
        SpecScenario.Reset(); SpecScenario.Root.Values.Remove("TSPEC"); result = Read();
        Check(Unknown(Field(result, "TSPEC")) && (bool)Field(result, "TSPEC")["supported"] == false
            && !SpecScenario.Reads.Contains("=1/10|BRAN.reference.TSPEC"), "unsupported value remains unknown");
        SpecScenario.Reset(); result = Read(attributes: new[] { "NO_SUCH_ATTRIBUTE" });
        Check(Unknown(Field(result, "NO_SUCH_ATTRIBUTE")) && Field(result, "NO_SUCH_ATTRIBUTE")["supported"].Type == JTokenType.Null, "missing attribute definition remains unknown");
        SpecScenario.Reset(); SpecScenario.Overrides["=1/10|BRAN.formatted.PSPEC"] = new InvalidOperationException("formatter failed"); result = Read();
        Check(!(bool)result["complete"] && Field(result, "PSPEC")["formatted_value"].Type == JTokenType.Null
            && (string)Field(result, "PSPEC")["typed_status"] == "observed", "formatted failure preserves independent typed identity");
        SpecScenario.Reset(); SpecScenario.Overrides["=1/10|BRAN.reference.PSPEC"] = new InvalidOperationException("reference failed"); result = Read();
        Check(Unknown(Field(result, "PSPEC")) && (string)Field(result, "PSPEC")["formatted_status"] == "observed", "reference failure preserves text without parsing it");
        SpecScenario.Reset(); SpecScenario.Overrides["=2/10|SPEC.formatted.NAME"] = new InvalidOperationException("name failed"); result = Read();
        Check(!(bool)result["complete"] && (string)Field(result, "PSPEC")["typed_value"]["identity_key"] == "=2/10|SPEC"
            && Field(result, "PSPEC")["typed_value"]["name"].Type == JTokenType.Null, "unknown name retains typed DBREF");
        foreach (string suffix in new[] { "deleted", "valid", "ref" })
        {
            SpecScenario.Reset(); SpecScenario.Overrides["=2/10|SPEC." + suffix] = suffix == "ref" ? (object)new[] { 0, 1 } : suffix == "deleted";
            result = Read(); Check(Unknown(Field(result, "PSPEC")), "invalid native reference identity remains unknown: " + suffix);
        }
        foreach (string field in new[] { "name", "number", "type" })
        {
            SpecScenario.Reset(); SpecScenario.Overrides["db.2." + field] = new InvalidOperationException("database metadata failed"); result = Read();
            Check(!(bool)result["complete"] && (string)Field(result, "PSPEC")["typed_value"]["database"]["status"] == "unknown"
                && Field(result, "PSPEC")["typed_value"]["database"][field == "type" ? "type_code" : field].Type == JTokenType.Null,
                "database metadata failure remains unknown: " + field);
        }
        SpecScenario.Reset(); SpecScenario.Overrides["db.2.number"] = 777; result = Read();
        Check((int)Field(result, "PSPEC")["typed_value"]["database"]["number"] == 777
            && (string)Field(result, "PSPEC")["typed_value"]["refno"] == "=2/10", "database number is independent native evidence");
        SpecScenario.Reset(); SpecScenario.Overrides["db.2.type"] = DbType.Property; result = Read();
        Check(!(bool)result["complete"] && (int)Field(result, "PSPEC")["typed_value"]["database"]["type_code"] == 4
            && (int)Field(result, "PSPEC")["typed_value"]["db_type_code"] == 2, "disagreeing native database type readings are retained");
        SpecScenario.Reset(); SpecScenario.Overrides["db.2.type"] = (DbType)0;
        SpecScenario.Overrides["=2/10|SPEC.database_type"] = (DbType)0; result = Read();
        Check(!(bool)result["complete"] && (string)Field(result, "PSPEC")["typed_value"]["database"]["status"] == "unknown"
            && (string)Field(result, "PSPEC")["typed_value"]["identity_key"] == "=2/10|SPEC", "unknown native DB type preserves typed REF with unknown database status");

        foreach (var pair in new[] { new { Type = DbType.Catalogue, Code = 2, Kind = "SPCO" },
            new { Type = DbType.Property, Code = 4, Kind = "CMPD" }, new { Type = DbType.Dictionary, Code = 8, Kind = "UDET" } })
        {
            SpecScenario.Reset(); SpecScenario.Root = SpecScenario.Add(pair.Code, 100, pair.Kind, pair.Type);
            SpecScenario.Root.NativeOwner = SpecScenario.Add(pair.Code, 0, "WORL", pair.Type);
            var supportComponent = SpecScenario.Add(2, 110, "SCOM", DbType.Catalogue);
            SpecScenario.Root.Values["CATR"] = supportComponent;
            result = Read(attributes: new[] { "CATR" });
            Check((bool)result["complete"] && (int)result["rows"][0]["db_type_code"] == pair.Code
                && (string)Field(result, "CATR")["typed_value"]["type"] == "SCOM", "explicit supporting database target: " + pair.Type);
        }
    }
    private static void ScalarsAndBounds()
    {
        SpecScenario.Reset(); JObject result = Read(attributes: new[] { "BORE", "COUNT", "FLAG", "DESC" });
        Check((bool)result["complete"] && (double)Field(result, "BORE")["typed_value"] == 100d
            && (string)Field(result, "BORE")["unit_kind"] == "BORE" && Field(result, "BORE")["unit"].Type == JTokenType.Null
            && (int)Field(result, "COUNT")["typed_value"] == 0 && !(bool)Field(result, "FLAG")["typed_value"]
            && (string)Field(result, "DESC")["typed_value"] == "", "raw scalars keep zero/false/empty and unknown physical unit");
        foreach (double number in new[] { double.NaN, double.PositiveInfinity, double.NegativeInfinity })
        { SpecScenario.Reset(); SpecScenario.Root.Values["BORE"] = number; result = Read(attributes: new[] { "BORE" }); Check(Unknown(Field(result, "BORE")), "nonfinite scalar unknown"); }
        foreach (string name in new[] { "PARA", "POS" })
        {
            SpecScenario.Reset(); result = Read(attributes: new[] { name });
            Check(Unknown(Field(result, name)) && (string)Field(result, name)["formatted_status"] == "observed", "array/complex value not inferred");
        }
        foreach (string part in new[] { "type", "array", "units" })
        {
            SpecScenario.Reset(); SpecScenario.Overrides["attribute.BORE." + part] = new InvalidOperationException("metadata failed");
            result = Read(attributes: new[] { "BORE" }); Check(!(bool)result["complete"] && (string)Field(result, "BORE")["formatted_status"] == "observed", "attribute metadata partial evidence");
        }
        SpecScenario.Reset(); SpecScenario.Overrides["=1/10|BRAN.formatted.DESC"] = new string('x', 4097); result = Read(attributes: new[] { "DESC" });
        Check(!(bool)result["complete"] && Field(result, "DESC")["formatted_value"].Type == JTokenType.Null, "bounded native text");

        SpecScenario.Reset(); SpecScenario.Child(SpecScenario.Root, SpecScenario.Add(1, 11, "ELBO", DbType.Design));
        result = Read(attributes: new string[0], limit: 1);
        Check((int)result["returned_count"] == 1 && (bool)result["truncated"] && !(bool)result["complete"]
            && !SpecScenario.Reads.Contains("=1/11|ELBO.member.1"), "row limit uses one identity lookahead without descendant read");
        SpecScenario.Reset(); SpecScenario.Child(SpecScenario.Root, SpecScenario.Add(1, 11, "ELBO", DbType.Design));
        result = Read(attributes: new string[0], limit: 2); Check((bool)result["complete"], "exact row fit distinguished from truncation");
        SpecScenario.Reset(); var child = SpecScenario.Child(SpecScenario.Root, SpecScenario.Add(1, 11, "ELBO", DbType.Design));
        SpecScenario.Child(child, SpecScenario.Add(1, 12, "TUBI", DbType.Design));
        result = Read(attributes: new string[0], depth: 1);
        Check((int)result["returned_count"] == 2 && (bool)result["truncated"] && (string)result["truncation_reasons"][0] == "depth", "depth bound explicit");
        SpecScenario.Reset(); SpecScenario.Overrides["=1/10|BRAN.member.1"] = new InvalidOperationException("member unavailable"); result = Read();
        Check(!(bool)result["traversal_complete"] && !(bool)result["complete"] && result["read_errors"].Count() == 1, "member failure not empty tree");
        SpecScenario.Reset(); child = SpecScenario.Child(SpecScenario.Root, SpecScenario.Add(1, 11, "ELBO", DbType.Design)); child.NativeOwner = SpecScenario.World;
        result = Read(); Check(!(bool)result["complete"] && (int)result["returned_count"] == 1, "wrong typed owner fails traversal");
        SpecScenario.Reset(); child = SpecScenario.Child(SpecScenario.Root, SpecScenario.Add(1, 11, "ELBO", DbType.Design));
        child.NativeOwner = new DbElement { DatabaseNumber = 1, Id = 10, Kind = "BRAN", DatabaseType = DbType.Catalogue };
        result = Read(); Check(!(bool)result["complete"] && (int)result["returned_count"] == 1, "owner database type is checked independently of its typed DBREF");
        SpecScenario.Reset(); SpecScenario.Root.Children.Add(SpecScenario.Root); result = Read();
        Check(!(bool)result["complete"], "repeated/cyclic typed identity fails traversal");
        SpecScenario.Reset(); SpecScenario.Child(SpecScenario.Root, SpecScenario.Spec); result = Read();
        Check(!(bool)result["complete"] && !(bool)result["traversal_complete"], "foreign database member not silently accepted");
    }
    private static void BoundariesAndFaults()
    {
        SpecScenario.Reset(); int reads = 0;
        SpecScenario.OnRead = key => { if (key == "project.current" && ++reads == 2) SpecScenario.Overrides[key] = new Project(); };
        JObject result = Read(); Check(!(bool)result["complete"] && !(bool)result["context"]["boundary_agreement"], "project instance drift incomplete");
        SpecScenario.Reset(); reads = 0;
        SpecScenario.OnRead = key => { if (key == "ce" && ++reads == 2) SpecScenario.Overrides[key] = SpecScenario.Root; };
        result = Read(); Check(!(bool)result["complete"] && !(bool)result["context"]["boundary_agreement"], "typed CE drift incomplete");
        SpecScenario.Reset(); SpecScenario.Overrides["ce"] = new InvalidOperationException("CE unavailable"); result = Read();
        Check(!(bool)result["complete"] && result["context"]["boundary_agreement"].Type == JTokenType.Null, "unknown context stays unknown");
        SpecScenario.Reset(); reads = 0;
        SpecScenario.OnRead = key => { if (key == "mdb.current" && ++reads == 2) SpecScenario.Overrides[key] = new MDB(); };
        result = Read(); Check(!(bool)result["complete"] && !(bool)result["context"]["boundary_agreement"], "same-name MDB instance drift incomplete");
        foreach (string key in new[] { "project.name", "mdb.name", "=1/10|BRAN.formatted.NAME", "=1/10|BRAN.db", "=1/10|BRAN.owner" })
        {
            SpecScenario.Reset(); SpecScenario.Overrides[key] = new InvalidOperationException("ordinary independent getter failed");
            result = Read(); Check(!(bool)result["complete"] && (int)result["returned_count"] == 1
                && (string)Field(result, "PSPEC")["typed_status"] == "observed", "ordinary getter failure retains independent evidence: " + key);
        }
        SpecScenario.Reset(); reads = 0;
        SpecScenario.OnRead = key => { if (key == "project.current" && ++reads == 2) SpecScenario.Overrides[key] = new InvalidOperationException("after project unavailable"); };
        result = Read(); Check(!(bool)result["complete"] && result["context"]["same_project_instance"].Type == JTokenType.Null,
            "after context getter failure retains returned values");
        SpecScenario.Reset(); reads = 0;
        SpecScenario.OnRead = key => { if (key == "mdb.current" && ++reads == 2) SpecScenario.Overrides[key] = new InvalidOperationException("after MDB unavailable"); };
        result = Read(); Check(!(bool)result["complete"] && result["context"]["same_mdb_instance"].Type == JTokenType.Null,
            "after MDB getter failure retains returned values");
        SpecScenario.Reset(); SpecScenario.Busy = true; Reject(() => Read(), true);
        SpecScenario.Reset(); SpecScenario.BusyOnUi = true; Reject(() => Read()); Check(SpecScenario.Reads.Count == 0, "UI busy stops before SDK reads");

        int occurrences = 0;
        string[] attributes = { "PSPEC", "BORE", "DESC", "COUNT", "FLAG" };
        for (int scenario = 0; scenario < 3; scenario++)
        {
            PrepareFaultScenario(scenario); Read(attributes: attributes);
            string[] keys = SpecScenario.Reads.ToArray();
            occurrences += keys.Length;
            for (int mode = 0; mode < 2; mode++)
            for (int index = 0; index < keys.Length; index++)
            {
                int position = index;
                bool throwOrdinary = mode == 1;
                string point = keys[position];
                PrepareFaultScenario(scenario); var context = new E3d21Context(); bool injected = false, rejected = false;
                SpecScenario.OnRead = key => { if (SpecScenario.Reads.Count == position + 1) {
                    injected = true; context.Fault();
                    if (throwOrdinary) throw new InvalidOperationException("ordinary exception accompanying native fault");
                } };
                try { Read(attributes: attributes, context: context); }
                catch (NativeDatabaseFaultException) { rejected = true; }
                Check(injected && rejected && SpecScenario.Reads.Count == position + 1 && SpecScenario.Reads.Last() == point,
                    "native fault stops immediately at scenario " + scenario + " read " + position + " mode " + mode + " " + point);
                Check(SpecScenario.UiDepth == 0, "UI scope restored after fault");
                int count = SpecScenario.Reads.Count, dispatches = SpecScenario.Dispatches;
                rejected = false;
                try { Read(context: context); } catch (NativeDatabaseFaultException) { rejected = true; }
                Check(rejected && count == SpecScenario.Reads.Count && dispatches == SpecScenario.Dispatches
                    && SpecScenario.ForbiddenCalls == 0, "latched native fault prevents replay or implicit SDK calls");
            }
        }
        Console.WriteLine("Piping specification native fault matrix: " + (occurrences * 2)
            + " cases at " + occurrences + " read occurrences stopped before any subsequent SDK call and rejected replay.");
    }

    private static void PrepareFaultScenario(int scenario)
    {
        SpecScenario.Reset();
        if (scenario == 1)
        {
            var tube = SpecScenario.Child(SpecScenario.Root, SpecScenario.Add(1, 10, "TUBI", DbType.Design));
            var elbow = SpecScenario.Child(SpecScenario.Root, SpecScenario.Add(1, 11, "ELBO", DbType.Design));
            foreach (DbElement child in new[] { tube, elbow })
            {
                child.Values["PSPEC"] = SpecScenario.Spec; child.Values["BORE"] = 50d;
                child.Values["DESC"] = "child"; child.Values["COUNT"] = 1; child.Values["FLAG"] = true;
            }
        }
        if (scenario == 2)
        {
            SpecScenario.Overrides["attribute.BORE.type"] = new InvalidOperationException("metadata unavailable");
            SpecScenario.Overrides["db.2.name"] = new InvalidOperationException("reference database name unavailable");
            SpecScenario.Overrides["=1/10|BRAN.formatted.NAME"] = new InvalidOperationException("root name unavailable");
        }
    }
    private static int Main()
    {
        try
        {
            InputsAndIdentity(); ReferencesAndUnknowns(); ScalarsAndBounds(); BoundariesAndFaults();
            foreach (JObject receipt in Receipts) Console.WriteLine("Piping spec contract receipt: " + receipt.ToString(Formatting.None));
            Console.WriteLine("Piping specification production receipts: " + Receipts.Count);
            Console.WriteLine("Piping specification production checks passed: " + Checks);
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
