// Recording managed SDK fakes only. No E3D process or database DLL is loaded.
using System;
using System.Collections.Generic;
using System.Linq;
using Aveva.Core.Database;
using E3d21AiBridge;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Aveva.Core.Database
{
    public enum DbType { Design = 1, Catalog = 2, Catalogue = 2, Draft = 7 }
    public enum DbAttributeType { STRING, DOUBLE, INTEGER, BOOL, ELEMENT }
    public enum DbAttributeUnits { NONE, DISTANCE }

    public sealed class Project
    {
        public static Project CurrentProject => SchemaScenario.Read("project.current", SchemaScenario.Project);
        public string Name => SchemaScenario.Read("project.name", "PROJECT");
    }

    public sealed class MDB
    {
        public static MDB CurrentMDB => SchemaScenario.Read("mdb.current", SchemaScenario.Mdb);
        public string Name => SchemaScenario.Read("mdb.name", "CATALOGUE-MDB");
        public DbElement FindElement(DbType type, string name)
        {
            if (type != DbType.Catalogue) SchemaScenario.Forbidden("non-Catalogue name lookup");
            DbElement value;
            return SchemaScenario.Read("mdb.catalogue.find." + name, SchemaScenario.Names.TryGetValue(name, out value) ? value : null);
        }
    }

    public static class CurrentElement
    {
        public static DbElement Element
        {
            get => SchemaScenario.Read("ce", SchemaScenario.Ce);
            set => SchemaScenario.Forbidden("set CE");
        }
    }

    public sealed class DbElementType
    {
        internal readonly string Key;
        public DbElementType(string key) { Key = key; }
        public bool IsValid => SchemaScenario.Read("type." + Key + ".valid", true);
        public string Name => SchemaScenario.Read("type." + Key + ".name", Key);
        public string ShortName => SchemaScenario.Read("type." + Key + ".short", Key);
        public DbElementType[] MemberTypes() => SchemaScenario.Read("type." + Key + ".members", SchemaScenario.Children);
        public int[] DatabaseTypes() => SchemaScenario.Read("type." + Key + ".databases", new[] { (int)DbType.Catalogue });
    }

    public sealed class DbAttribute
    {
        internal readonly string Key;
        internal DbAttribute(string key) { Key = key; }
        public static DbAttribute GetDbAttribute(string name)
        {
            DbAttribute attribute;
            return SchemaScenario.Read("attribute." + name + ".resolve", SchemaScenario.Attributes.TryGetValue(name, out attribute) ? attribute : null);
        }
        public bool IsValid => SchemaScenario.Read("attribute." + Key + ".valid", true);
        public string Name => SchemaScenario.Read("attribute." + Key + ".name", Key);
        public string ShortName => SchemaScenario.Read("attribute." + Key + ".short", Key);
        public DbAttributeType Type => SchemaScenario.Read("attribute." + Key + ".type", Key == "DESC" ? DbAttributeType.STRING : DbAttributeType.DOUBLE);
        public bool IsArray => SchemaScenario.Read("attribute." + Key + ".array", false);
        public bool IsPseudo => SchemaScenario.Read("attribute." + Key + ".pseudo", false);
        public bool IsUDA => SchemaScenario.Read("attribute." + Key + ".uda", Key.StartsWith(":"));
        public DbAttributeUnits Units => SchemaScenario.Read("attribute." + Key + ".units", DbAttributeUnits.DISTANCE);
        public int MaximumSize => SchemaScenario.Read("attribute." + Key + ".maximum", 1);
    }

    public static class DbAttributeInstance { public static readonly DbAttribute NAME = new DbAttribute("NAME"); }

    public sealed class DbElement
    {
        internal readonly int Id;
        internal readonly string Kind, NativeName;
        internal readonly DbType DatabaseType;
        internal DbElement(int id, string kind, string name, DbType databaseType = DbType.Catalogue)
        { Id = id; Kind = kind; NativeName = name; DatabaseType = databaseType; }
        private string Path => "element." + Id;
        public static DbElement GetElement(int[] pair)
        {
            string reference = "=" + pair[0] + "/" + pair[1];
            DbElement value;
            return SchemaScenario.Read("element.get." + reference, SchemaScenario.Elements.TryGetValue(reference, out value) ? value : null);
        }
        public bool IsValid => SchemaScenario.Read(Path + ".valid", true);
        public bool IsNull => SchemaScenario.Read(Path + ".null", false);
        public bool IsDeleted => SchemaScenario.Read(Path + ".deleted", false);
        public DbType DbType => SchemaScenario.Read(Path + ".database", DatabaseType);
        public int[] Ref => SchemaScenario.Read(Path + ".ref", new[] { 1, Id });
        public DbElementType GetElementType() => SchemaScenario.Read(Path + ".base_type", new DbElementType(Kind));
        public string GetAsString(DbAttribute attribute)
        {
            if (attribute != DbAttributeInstance.NAME) SchemaScenario.Forbidden("read attribute values");
            return SchemaScenario.Read(Path + ".name", NativeName);
        }
        public bool IsCreatable(DbElementType type) => SchemaScenario.Read(Path + ".creatable." + type.Key, true);
        public bool IsAttributeValid(DbAttribute attribute) => SchemaScenario.Read(Path + ".supports." + attribute.Key, true);
        public bool IsAttributeSetable(DbAttribute attribute) { SchemaScenario.Forbidden("query edit policy"); return false; }
        public DbAttribute[] GetAttributes() { SchemaScenario.Forbidden("enumerate non-explicit attributes"); return null; }
        public bool GetAttributeAllowedValuesAndDescriptions(DbAttribute attribute, ref KeyValuePair<string, string>[] values)
        {
            var plan = SchemaScenario.Plan(attribute.Key);
            var query = SchemaScenario.Read(Path + ".constraint." + attribute.Key + ".values", Tuple.Create(plan.ValuesDefined, plan.Values));
            values = query.Item2;
            return query.Item1;
        }
        public bool GetAttributeAllowedRanges(DbAttribute attribute, ref double[] ranges)
        {
            var plan = SchemaScenario.Plan(attribute.Key);
            var query = SchemaScenario.Read(Path + ".constraint." + attribute.Key + ".ranges", Tuple.Create(plan.RangesDefined, plan.Ranges));
            ranges = query.Item2;
            return query.Item1;
        }
        public bool IsAttributeAllowedOptional(DbAttribute attribute) => SchemaScenario.Read(
            Path + ".constraint." + attribute.Key + ".optional", SchemaScenario.Plan(attribute.Key).Optional);
        public void Claim() => SchemaScenario.Forbidden("claim model");
    }
}

internal sealed class SchemaConstraintPlan
{
    internal bool ValuesDefined, RangesDefined;
    internal bool Optional = true;
    internal KeyValuePair<string, string>[] Values;
    internal double[] Ranges;
}

internal static class SchemaScenario
{
    internal static readonly List<string> Reads = new List<string>();
    internal static readonly Dictionary<string, object> Overrides = new Dictionary<string, object>();
    internal static readonly Dictionary<string, DbElement> Names = new Dictionary<string, DbElement>(StringComparer.OrdinalIgnoreCase);
    internal static readonly Dictionary<string, DbElement> Elements = new Dictionary<string, DbElement>();
    internal static readonly Dictionary<string, DbAttribute> Attributes = new Dictionary<string, DbAttribute>();
    internal static readonly Dictionary<string, SchemaConstraintPlan> Plans = new Dictionary<string, SchemaConstraintPlan>();
    internal static Action<string> OnRead;
    internal static Project Project;
    internal static MDB Mdb;
    internal static DbElement Target, Ce;
    internal static DbElementType[] Children;
    internal static int Dispatches, UiDepth, ForbiddenCalls;
    internal static bool Busy, BusyOnUi;
    internal static T Read<T>(string key, T value)
    {
        if (UiDepth != 1) Forbidden("SDK read outside one UI dispatch: " + key);
        Reads.Add(key);
        OnRead?.Invoke(key);
        if (!Overrides.TryGetValue(key, out object replacement)) return value;
        if (replacement is Exception error) throw error;
        return (T)replacement;
    }
    internal static void Forbidden(string message)
    {
        ForbiddenCalls++;
        throw new InvalidOperationException("Forbidden SDK call: " + message);
    }
    internal static SchemaConstraintPlan Plan(string name)
    {
        if (!Plans.TryGetValue(name, out SchemaConstraintPlan plan)) Plans[name] = plan = new SchemaConstraintPlan();
        return plan;
    }
    internal static void Reset()
    {
        Reads.Clear(); Overrides.Clear(); Names.Clear(); Elements.Clear(); Attributes.Clear(); Plans.Clear();
        OnRead = null; Dispatches = UiDepth = ForbiddenCalls = 0; Busy = BusyOnUi = false;
        Project = new Project(); Mdb = new MDB();
        Target = new DbElement(1, "CATA", "/CAT");
        Ce = new DbElement(90, "EQUI", "/UNCHANGED-CE", DbType.Design);
        Names["/CAT"] = Target; Elements["=1/1"] = Target;
        Children = new[] { new DbElementType("SECT"), new DbElementType("STSE"), new DbElementType("LIBY") };
        Overrides["type.LIBY.databases"] = new[] { (int)DbType.Draft };
        Overrides["element.1.creatable.LIBY"] = false;
        foreach (string name in new[] { "DESC", ":BORE", "UNSUPPORTED" }) Attributes[name] = new DbAttribute(name);
        Plan("DESC").ValuesDefined = true;
        Plan("DESC").Values = new[] { new KeyValuePair<string, string>("A", "native description") };
        Plan(":BORE").RangesDefined = true;
        Plan(":BORE").Ranges = new[] { 0d, 10d, double.NaN, double.PositiveInfinity, double.NegativeInfinity };
    }
}

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;
        private readonly Action<Action> invokeOnUi = action =>
        {
            SchemaScenario.Dispatches++; SchemaScenario.UiDepth++;
            try { action(); } finally { SchemaScenario.UiDepth--; }
        };
        internal void Fault() => nativeFaultState.Observe("Unhandled Database error 538 from SchemaFake", "");
        private bool IsCommandActive()
        {
            if (nativeFaultState.HasFault) throw new NativeDatabaseFaultException();
            return SchemaScenario.Busy || SchemaScenario.UiDepth != 0 && SchemaScenario.BusyOnUi;
        }
        // PRODUCTION_HELPERS
    }
}

internal static class ParagonCatalogueSchemaHarness
{
    private const string Session = "paragon-schema-offline-session";
    private static readonly string[] Fields = { "DESC", ":BORE" };
    private static bool Null(JToken value) => value == null || value.Type == JTokenType.Null;
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception(message + "\nSDK reads: " + string.Join(", ", SchemaScenario.Reads));
    }
    private static JObject Read(string refno = "=1/1", string[] attributes = null, bool include = false)
    {
        object result = new E3d21Context().ReadParagonCatalogueSchema(refno, attributes, include, Session);
        Check(SchemaScenario.Dispatches == 1 && SchemaScenario.ForbiddenCalls == 0, "Expected one read-only UI callback.");
        var reply = JObject.FromObject(new { ok = true, version = "E3D21", session_id = Session,
            bridge_build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D"), result });
        Console.WriteLine("Paragon schema receipt: " + JObject.FromObject(new {
            payload = new { refno, attributes = attributes ?? new string[0], include_constraints = include }, reply }).ToString(Formatting.None));
        return (JObject)reply["result"];
    }

    private static void Validation()
    {
        foreach (var request in new Action[] {
            () => ParagonCatalogueSchemaRequest.Validate(null, null, false),
            () => ParagonCatalogueSchemaRequest.Validate("CE", null, false),
            () => ParagonCatalogueSchemaRequest.Validate("/", null, false),
            () => ParagonCatalogueSchemaRequest.Validate("/CAT?", null, false),
            () => ParagonCatalogueSchemaRequest.Validate("=2147483648/1", null, false),
            () => ParagonCatalogueSchemaRequest.Validate("=0/0", null, false),
            () => ParagonCatalogueSchemaRequest.Validate("/CAT", new[] { "DESC", " desc " }, false),
            () => ParagonCatalogueSchemaRequest.Validate("/CAT", new[] { "POS WRT" }, false),
            () => ParagonCatalogueSchemaRequest.Validate("/CAT", new[] { "DÉSC" }, false),
            () => ParagonCatalogueSchemaRequest.Validate("/CAT", new string[0], true),
            () => ParagonCatalogueSchemaRequest.Validate("/CAT", new string[11], false),
            () => new E3d21Context().ReadParagonCatalogueSchema("/CAT", null, false, "") })
        {
            SchemaScenario.Reset(); bool rejected = false;
            try { request(); } catch (ArgumentException) { rejected = true; } catch (InvalidOperationException) { rejected = true; }
            Check(rejected && SchemaScenario.Reads.Count == 0 && SchemaScenario.Dispatches == 0, "Invalid input reached native code.");
        }
        var normalized = ParagonCatalogueSchemaRequest.Validate(" =01/001 ", new[] { " desc " }, true);
        Check(normalized.Refno == "=1/1" && normalized.Attributes[0] == "DESC", "Normalization did not match the public contract.");
        foreach (bool ui in new[] { false, true })
        {
            SchemaScenario.Reset(); SchemaScenario.Busy = !ui; SchemaScenario.BusyOnUi = ui; bool rejected = false;
            try { new E3d21Context().ReadParagonCatalogueSchema("/CAT", null, false, Session); }
            catch (InvalidOperationException) { rejected = true; }
            Check(rejected && SchemaScenario.Dispatches == (ui ? 1 : 0) && SchemaScenario.Reads.Count == 0, "Busy host reached database reads.");
        }
    }

    private static void TargetScopeAndMetadata()
    {
        SchemaScenario.Reset(); var result = Read(" /cat ", Fields, true);
        Check((bool)result["complete"] && (string)result["target"]["type"] == "CATA" && (int)result["target"]["db_type_code"] == 2,
            "The native Catalogue target must be verified.");
        Check(SchemaScenario.Reads.Contains("mdb.catalogue.find./cat") &&
            (string)result["child_types"][0]["short_name"] == "SECT" &&
            !(bool)result["child_types"][2]["catalogue_supported"] && !(bool)result["child_types"][2]["sdk_creatable_now"] &&
            (int)result["child_types"][2]["database_type_codes"][0] == 7,
            "MemberTypes and DatabaseTypes must preserve native order and the actual Draft LIBY evidence.");
        Check((string)result["scope"]["attribute_target"] == "current_target" &&
            !(bool)result["scope"]["future_child_attributes_observed"] &&
            (string)result["scope"]["member_type_basis"] == "native_base_type" && Null(result["scope"]["permissions_verified"]) &&
            !SchemaScenario.Reads.Any(key => key.StartsWith("element.90.constraint.") || key.StartsWith("element.90.supports.")),
            "Constraints and support queries must target only the explicit parent.");
        Check((string)result["attributes"][0]["constraints"]["allowed_values"]["values"][0]["description"] == "native description" &&
            (string)result["attributes"][1]["constraints"]["allowed_ranges"]["raw_boundaries"][2] == "NaN" &&
            (string)result["attributes"][1]["constraints"]["allowed_ranges"]["unit_basis"] == "unknown" &&
            !(bool)result["scope"]["optionality_is_requiredness"], "Raw SDK values/ranges and optionality were reinterpreted.");
        SchemaScenario.Reset(); result = Read();
        Check((bool)result["complete"] && result["attributes"].Count() == 0 && Null(result["constraints_complete"]) &&
            !SchemaScenario.Reads.Any(key => key.StartsWith("attribute.") || key.Contains(".constraint.")), "Empty attributes must not enumerate target attributes or constraints.");

        foreach (string error in new[] { "Design CATA", "Draft LIBY", "mismatched numeric target", "mismatched name" })
        {
            SchemaScenario.Reset();
            string reference = "=1/1";
            if (error == "Design CATA") SchemaScenario.Overrides["element.1.database"] = DbType.Design;
            else if (error == "Draft LIBY") SchemaScenario.Overrides["element.1.database"] = DbType.Draft;
            else if (error == "mismatched numeric target") SchemaScenario.Overrides["element.1.ref"] = new[] { 1, 2 };
            else { reference = "/CAT"; SchemaScenario.Overrides["element.1.name"] = "/OTHER"; }
            bool rejected = false;
            try { new E3d21Context().ReadParagonCatalogueSchema(reference, Fields, true, Session); }
            catch (ArgumentException) { rejected = true; } catch (InvalidOperationException) { rejected = true; }
            Check(rejected && !SchemaScenario.Reads.Any(key => key.EndsWith(".members") || key.Contains(".constraint.")), "An unverified target reached schema reads: " + error);
        }
    }

    private static void BudgetsAndPartial()
    {
        SchemaScenario.Reset();
        SchemaScenario.Children = Enumerable.Range(0, 201).Select(i => new DbElementType("CHILD" + i)).ToArray();
        var result = Read();
        Check(!(bool)result["complete"] && (int)result["child_types_observation"]["returned_count"] == 200 &&
            (int)result["child_types_observation"]["total_count"] == 201 &&
            (bool)result["child_types_observation"]["truncated"] && !SchemaScenario.Reads.Any(key => key.StartsWith("type.CHILD200.")),
            "A child type beyond the 200-item budget must not trigger SDK metadata reads.");
        SchemaScenario.Reset(); SchemaScenario.Children = new[] { new DbElementType("BIG") };
        SchemaScenario.Overrides["type.BIG.databases"] = Enumerable.Repeat(1, 33).ToArray();
        result = Read();
        Check(!(bool)result["complete"] && (bool)result["child_types"][0]["database_types_truncated"] &&
            Null(result["child_types"][0]["catalogue_supported"]), "Truncated DB type evidence cannot imply Catalogue incompatibility.");
        SchemaScenario.Reset();
        SchemaScenario.Plan("DESC").Values = Enumerable.Range(0, 101).Select(i => new KeyValuePair<string, string>("v" + i, new string('x', 1023) + "😀")).ToArray();
        SchemaScenario.Plan(":BORE").Ranges = Enumerable.Range(0, 101).Select(i => (double)i).ToArray();
        result = Read(attributes: Fields, include: true);
        Check(!(bool)result["complete"] && (bool)result["attributes_complete"] && !(bool)result["constraints_complete"] &&
            (int)result["attributes"][0]["constraints"]["allowed_values"]["returned_count"] == 100 &&
            ((string)result["attributes"][0]["constraints"]["allowed_values"]["values"][0]["description"]).Length == 1023 &&
            (int)result["attributes"][1]["constraints"]["allowed_ranges"]["returned_count"] == 100,
            "Constraint limits must preserve metadata, actual totals and a valid UTF-16 boundary.");

        foreach (string error in new[] { "type.CATA.members", "type.SECT.name", "type.STSE.databases", "element.1.creatable.STSE",
            "attribute.DESC.resolve", "attribute.DESC.units", "element.1.supports.DESC", "element.1.constraint.DESC.values",
            "element.1.constraint.DESC.ranges", "element.1.constraint.DESC.optional", "element.1.name" })
        {
            SchemaScenario.Reset(); SchemaScenario.Overrides[error] = new InvalidOperationException(new string('X', 5000));
            result = Read(attributes: Fields, include: true);
            Check(!(bool)result["complete"] && (bool)result["available"] && result["attributes"].Count() == 2 &&
                (string)result["attributes"][1]["requested_name"] == ":BORE", "An ordinary field failure discarded independent evidence: " + error);
        }
        foreach (string name in new[] { "UNKNOWN", "UNSUPPORTED" })
        {
            SchemaScenario.Reset(); SchemaScenario.Overrides["element.1.supports.UNSUPPORTED"] = false;
            result = Read(attributes: new[] { name }, include: true);
            Check(!(bool)result["complete"] && !SchemaScenario.Reads.Any(key => key.Contains(".constraint.")), "Unverified attributes reached constraint APIs.");
        }
        SchemaScenario.Reset(); SchemaScenario.Children = new DbElementType[] { null, new DbElementType("INVALID") };
        SchemaScenario.Overrides["type.INVALID.valid"] = false;
        result = Read();
        Check(!(bool)result["complete"] && result["child_types"].Count() == 2, "Invalid child entries must retain their native indices.");
    }

    private static void ContextAndUnknown()
    {
        foreach (string change in new[] { "before missing project", "before missing MDB", "before missing CE", "same-name project", "same-name MDB", "typed CE", "after MDB failure" })
        {
            SchemaScenario.Reset();
            if (change == "before missing project") SchemaScenario.Project = null;
            else if (change == "before missing MDB") SchemaScenario.Mdb = null;
            else if (change == "before missing CE") SchemaScenario.Ce = null;
            else SchemaScenario.OnRead = key =>
            {
                if (key == "mdb.current" && change == "after MDB failure" && SchemaScenario.Reads.Count(k => k == key) == 2)
                    SchemaScenario.Overrides[key] = new InvalidOperationException("MDB boundary unavailable");
                if (key != "type.CATA.members") return;
                if (change == "same-name project") SchemaScenario.Project = new Project();
                else if (change == "same-name MDB") SchemaScenario.Mdb = new MDB();
                else if (change == "typed CE") SchemaScenario.Ce = new DbElement(90, "BOX", "/CE-ALIAS", DbType.Design);
            };
            var result = Read();
            Check(!(bool)result["complete"] && (bool)result["child_types_observation"]["complete"] && result["child_types"].Count() == 3,
                "Boundary uncertainty must retain all independent schema evidence: " + change);
            if (change.StartsWith("before") || change == "after MDB failure") Check(Null(result["context"]["boundary_agreement"]), "Missing context must remain unknown.");
            else Check(!(bool)result["context"]["boundary_agreement"], "Same-name replacement or typed CE drift must be detected.");
        }
    }

    private static void FaultMatrix()
    {
        SchemaScenario.Reset(); Read(attributes: Fields, include: true); int calls = SchemaScenario.Reads.Count;
        for (int call = 1; call <= calls; call++)
            foreach (bool ordinary in new[] { false, true })
            {
                SchemaScenario.Reset(); var context = new E3d21Context();
                SchemaScenario.OnRead = key =>
                {
                    if (SchemaScenario.Reads.Count != call) return;
                    context.Fault();
                    if (ordinary) SchemaScenario.Overrides[key] = new InvalidOperationException("ordinary error accompanying native fault");
                };
                bool stopped = false;
                try { context.ReadParagonCatalogueSchema("=1/1", Fields, true, Session); }
                catch (NativeDatabaseFaultException) { stopped = true; }
                Check(stopped && SchemaScenario.Dispatches == 1 && SchemaScenario.Reads.Count == call && SchemaScenario.ForbiddenCalls == 0,
                    "Native fault must dominate ordinary errors and stop every subsequent SDK read at call " + call);
                try { context.ReadParagonCatalogueSchema("=1/1", Fields, true, Session); } catch (NativeDatabaseFaultException) { }
                Check(SchemaScenario.Dispatches == 1 && SchemaScenario.Reads.Count == call, "A latched fault must reject follow-up dispatch.");
            }
        Console.WriteLine("Paragon schema native fault matrix: " + calls * 2 + " cases.");
    }

    private static int Main()
    {
        Validation(); TargetScopeAndMetadata(); BudgetsAndPartial(); ContextAndUnknown(); FaultMatrix();
        Console.WriteLine("Paragon catalogue schema production checks passed.");
        return 0;
    }
}
