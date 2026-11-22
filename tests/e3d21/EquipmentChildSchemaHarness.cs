using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;
using E3d21AiBridge;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Aveva.Core.Database
{
    public enum DbType { Design = 1, Catalogue = 2 }
    public enum DbAttributeType { STRING, DOUBLE }
    public enum DbUnits { NONE, DISTANCE }
    public sealed class Project
    {
        public static Project CurrentProject => Scenario.Read("project.current", Scenario.Project);
        public string Name => Scenario.Read("project.name", "PROJECT");
    }
    public sealed class MDB
    {
        public static MDB CurrentMDB => Scenario.Read("mdb.current", Scenario.Mdb);
        public string Name => Scenario.Read("mdb.name", "MDB");
        public DbElement FindElement(string name)
        {
            Scenario.Names.TryGetValue(name, out DbElement value);
            return Scenario.Read("find." + name, value);
        }
    }
    public static class CurrentElement
    {
        public static DbElement Element
        {
            get => Scenario.Read("ce", Scenario.Ce);
            set => Scenario.Forbidden("set CE");
        }
    }
    public sealed class DbElementType
    {
        internal string Key;
        internal DbElementType(string key) { Key = key; }
        public static DbElementType GetElementType(string name)
        {
            Scenario.Types.TryGetValue(name, out DbElementType value);
            return Scenario.Read("type.resolve." + name, value);
        }
        public bool IsValid => Scenario.Read("type." + Key + ".valid", true);
        public string Name => Scenario.Read("type." + Key + ".name", Key);
        public string ShortName => Scenario.Read("type." + Key + ".short", Key);
        public DbAttribute[] SystemAttributes() => Scenario.Read("type." + Key + ".system_attributes", Scenario.TypeAttributes);
        public override bool Equals(object other) => Scenario.Read("type." + Key + ".equals", other is DbElementType type && type.Key == Key);
        public override int GetHashCode() => Key.GetHashCode();
        public static bool operator ==(DbElementType left, DbElementType right) { Scenario.Forbidden("SDK type equality operator used for a null check"); return false; }
        public static bool operator !=(DbElementType left, DbElementType right) { Scenario.Forbidden("SDK type inequality operator used for a null check"); return false; }
    }
    public sealed class DbAttribute
    {
        internal string Key;
        internal DbAttribute(string key) { Key = key; }
        public static DbAttribute GetDbAttribute(string name)
        {
            Scenario.Attributes.TryGetValue(name, out DbAttribute value);
            return Scenario.Read("attribute.resolve." + name, value);
        }
        public bool IsValid => Scenario.Read("attribute." + Key + ".valid", true);
        public string Name => Scenario.Read("attribute." + Key + ".name", Key);
        public string ShortName => Scenario.Read("attribute." + Key + ".short", Key);
        public DbAttributeType Type => Scenario.Read("attribute." + Key + ".type", DbAttributeType.DOUBLE);
        public bool IsArray => Scenario.Read("attribute." + Key + ".array", false);
        public bool IsPseudo => Scenario.Read("attribute." + Key + ".pseudo", false);
        public bool IsUDA => Scenario.Read("attribute." + Key + ".uda", Key.StartsWith(":"));
        public DbUnits Units => Scenario.Read("attribute." + Key + ".units", DbUnits.DISTANCE);
        public int MaximumSize => Scenario.Read("attribute." + Key + ".size", 1);
        public override bool Equals(object other) => ReferenceEquals(this, other);
        public override int GetHashCode() => Key.GetHashCode();
        public static bool operator ==(DbAttribute left, DbAttribute right) { Scenario.Forbidden("SDK attribute equality operator used for a null check"); return false; }
        public static bool operator !=(DbAttribute left, DbAttribute right) { Scenario.Forbidden("SDK attribute inequality operator used for a null check"); return false; }
    }
    public static class DbAttributeInstance { public static readonly DbAttribute NAME = new DbAttribute("NAME"); }
    public sealed class DbElement
    {
        internal int Id;
        internal string Kind, Actual;
        internal DbElement(int id, string kind, string actual = null) { Id = id; Kind = kind; Actual = actual ?? kind; }
        internal string Key => "element." + Id;
        internal string Reference => "=1/" + Id;
        public static DbElement GetElement(int[] pair)
        {
            string key = "=" + pair[0] + "/" + pair[1];
            Scenario.Elements.TryGetValue(key, out DbElement value);
            return Scenario.Read("get." + key, value);
        }
        public bool IsValid => Scenario.Read(Key + ".valid", true);
        public bool IsNull => Scenario.Read(Key + ".null", false);
        public bool IsDeleted => Scenario.Read(Key + ".deleted", false);
        public DbType DbType => Scenario.Read(Key + ".database", DbType.Design);
        public int[] Ref => Scenario.Read(Key + ".ref", new[] { 1, Id });
        public string GetAsString(DbAttribute attribute) => Scenario.Read(Key + ".name", "/E" + Id);
        public DbElementType ElementType => Scenario.Read(Key + ".base_type", Scenario.Types[Kind]);
        public DbElementType GetActualType() => Scenario.Read(Key + ".actual_type", Scenario.Types[Actual]);
        public bool IsCreatable(DbElementType type) => Scenario.Read(Key + ".creatable." + type.Key, true);
        public bool IsAttributeValid(DbAttribute attribute) => Scenario.Read(Key + ".supported." + attribute.Key, true);
        public bool IsAttributeSetable(DbAttribute attribute) => Scenario.Read(Key + ".settable." + attribute.Key, false);
        public override bool Equals(object other) => ReferenceEquals(this, other);
        public override int GetHashCode() => Id;
        public static bool operator ==(DbElement left, DbElement right) { Scenario.Forbidden("SDK element equality operator used for a null check"); return false; }
        public static bool operator !=(DbElement left, DbElement right) { Scenario.Forbidden("SDK element inequality operator used for a null check"); return false; }
        // No member, search, attribute-value, creation, claim, or setter APIs are
        // available. A production call to one fails compilation of this harness.
    }
}

internal static class Scenario
{
    internal static readonly List<string> Reads = new List<string>();
    internal static readonly Dictionary<string, object> Overrides = new Dictionary<string, object>();
    internal static readonly Dictionary<string, DbElementType> Types = new Dictionary<string, DbElementType>();
    internal static readonly Dictionary<string, DbElement> Elements = new Dictionary<string, DbElement>();
    internal static readonly Dictionary<string, DbElement> Names = new Dictionary<string, DbElement>(StringComparer.OrdinalIgnoreCase);
    internal static readonly Dictionary<string, DbAttribute> Attributes = new Dictionary<string, DbAttribute>();
    internal static DbAttribute[] TypeAttributes;
    internal static Project Project;
    internal static MDB Mdb;
    internal static DbElement Ce, Parent, Target;
    internal static Action<string> OnRead;
    internal static int Dispatches, UiDepth, ForbiddenCalls;
    internal static bool Busy, BusyOnUi;
    internal static T Read<T>(string key, T fallback)
    {
        if (UiDepth != 1) Forbidden("SDK read outside exactly one UI callback: " + key);
        Reads.Add(key);
        OnRead?.Invoke(key);
        if (!Overrides.TryGetValue(key, out object replacement)) return fallback;
        if (replacement is Exception error) throw error;
        return (T)replacement;
    }
    internal static void Forbidden(string message)
    {
        ForbiddenCalls++;
        throw new InvalidOperationException(message);
    }
    internal static void Reset()
    {
        Reads.Clear(); Overrides.Clear(); Types.Clear(); Elements.Clear(); Names.Clear(); Attributes.Clear();
        OnRead = null; Dispatches = UiDepth = ForbiddenCalls = 0; Busy = BusyOnUi = false;
        Project = new Project(); Mdb = new MDB();
        foreach (string name in new[] { "EQUI", "CYLI", ":CUSTOM_CYLI", ":OTHER_CYLI" }) Types[name] = new DbElementType(name);
        Types["CYLINDER"] = Types["CYLI"];
        Parent = new DbElement(1, "EQUI"); Target = new DbElement(2, "CYLI"); Ce = new DbElement(9, "EQUI");
        foreach (DbElement element in new[] { Parent, Target, Ce }) { Elements[element.Reference] = element; Names["/E" + element.Id] = element; }
        foreach (string name in new[] { "DIAM", ":CUSTOM" }) Attributes[name] = new DbAttribute(name);
        TypeAttributes = Attributes.Values.ToArray();
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
            Scenario.Dispatches++; Scenario.UiDepth++;
            try { action(); } finally { Scenario.UiDepth--; }
        };
        private bool IsCommandActive()
        {
            if (nativeFaultState.HasFault) throw new NativeDatabaseFaultException();
            return Scenario.Busy || Scenario.UiDepth != 0 && Scenario.BusyOnUi;
        }
        internal void Fault() => nativeFaultState.Observe("Unhandled Database error 538 from ChildSchemaFake", "");
        // The real context has a CurrentElement() member. Keep that name here
        // so an unqualified database CurrentElement reference cannot compile.
        private object CurrentElement() => null;
        // PRODUCTION_NATIVE_HELPERS
    }
}

internal static class EquipmentChildSchemaHarness
{
    private static int assertions, receipts;
    private const string Session = "equipment-child-schema-offline-session";
    private static bool Null(JToken token) => token == null || token.Type == JTokenType.Null;
    private static void Check(bool condition, string message)
    {
        assertions++;
        if (!condition) throw new Exception(message + "\nReads: " + string.Join(", ", Scenario.Reads));
    }
    private static JObject Read(string kind = "CYLI", string target = "=1/2", string[] attributes = null,
        int offset = 0, int limit = 50, string parent = "=1/1")
    {
        attributes = attributes ?? new[] { "DIAM", ":CUSTOM" };
        object result = new E3d21Context().ReadEquipmentChildSchema(parent, kind, target, attributes, offset, limit, Session);
        Check(Scenario.Dispatches == 1 && Scenario.ForbiddenCalls == 0, "Read must dispatch once and remain read-only.");
        string build = typeof(EquipmentChildSchemaHarness).Module.ModuleVersionId.ToString("D");
        var reply = JObject.FromObject(new { ok = true, version = "E3D21", session_id = Session, bridge_build = build, result });
        var evidence = (JObject)reply["result"];
        foreach (JToken boundary in new[] { evidence, evidence["context"]["before"], evidence["context"]["after"] })
        {
            Check((string)boundary["session_id"] == Session && (string)boundary["bridge_build"] == build,
                "Result and both context boundaries must bind to the server session and envelope assembly MVID.");
        }
        Check(!(bool)evidence["scope"]["future_child_attributes_observed"]
            && !(bool)evidence["scope"]["type_system_attributes_are_instance_attributes"]
            && !(bool)evidence["scope"]["sdk_creatable_now_is_execution_authorization"]
            && Null(evidence["scope"]["permissions_verified"]) && !(bool)evidence["scope"]["proposed_values_validated"]
            && (string)evidence["parent_creatability"]["scope"] == "current_parent_requested_type"
            && (string)evidence["type_system_attributes"]["scope"] == "requested_type_only"
            && (string)evidence["instance_attributes"]["scope"] == "read_target_only",
            "Partial or complete reads must preserve all three evidence scopes without permission promotion.");
        foreach (string key in new[] { "parent", "read_target" })
        {
            if (Null(evidence[key])) continue;
            JToken identity = evidence[key], binding = identity["request_binding"];
            string requested = (string)evidence[key == "parent" ? "requested_parent_refno" : "requested_read_target_refno"];
            bool named = requested.StartsWith("/", StringComparison.Ordinal);
            Check(binding is JObject && ((JObject)binding).Count == 5
                && (string)binding["source"] == "explicit_request_resolution" && (string)binding["requested_refno"] == requested
                && (bool)binding["verified"] && (string)binding["resolved_refno"] == (string)identity["refno"]
                && (named ? !Null(binding["observed_name"]) && ((string)binding["observed_name"]).Length <= 1024
                    && string.Equals(((string)binding["observed_name"]).TrimStart('/'), requested.TrimStart('/'), StringComparison.OrdinalIgnoreCase)
                    : Null(binding["observed_name"])),
                "Only actual first verified request-resolution values may bind an existing parent/exemplar identity.");
            Check(Null(identity["base_type"]["request_binding"]) && Null(identity["actual_type"]["request_binding"]),
                "Object request bindings must not be attached to type identities.");
        }
        Check(Null(evidence["requested_type"]["request_binding"]), "A requested type is not an existing object request binding.");
        Console.WriteLine("Child schema receipt: " + JObject.FromObject(new
        {
            payload = new { parent_refno = parent, element_type = kind, read_target_refno = target, attributes, offset, limit }, reply
        }).ToString(Formatting.None));
        receipts++;
        return (JObject)reply["result"];
    }
    private static void Validation()
    {
        foreach (string reference in new[] { null, "", "CE", "/", "/E*", "/E?", "=0/1", "=2147483648/1", "=1/-1", "/E\n/E2", "/E\u0085X" })
        {
            Scenario.Reset(); bool rejected = false;
            try { EquipmentChildSchemaRequest.Validate(reference, "CYLI", null, new string[0], 0, 50); }
            catch (ArgumentException) { rejected = true; }
            Check(rejected && Scenario.Reads.Count == 0, "Invalid parent reached SDK.");
        }
        foreach (var invalid in new Action[] {
            () => EquipmentChildSchemaRequest.Validate("/E1", "CÝLI", null, new string[0], 0, 50),
            () => EquipmentChildSchemaRequest.Validate("/E1", "", null, new string[0], 0, 50),
            () => EquipmentChildSchemaRequest.Validate("/E1", "CYLI", "", new string[0], 0, 50),
            () => EquipmentChildSchemaRequest.Validate("/E1", "CYLI", "CE", new string[0], 0, 50),
            () => EquipmentChildSchemaRequest.Validate("/E1", "CYLI", null, null, 0, 50),
            () => EquipmentChildSchemaRequest.Validate("/E1", "CYLI", null, new[] { "DIAM", " diam " }, 0, 50),
            () => EquipmentChildSchemaRequest.Validate("/E1", "CYLI", null, new string[21], 0, 50),
            () => EquipmentChildSchemaRequest.Validate("/E1", "CYLI", null, new string[0], -1, 50),
            () => EquipmentChildSchemaRequest.Validate("/E1", "CYLI", null, new string[0], 100001, 50),
            () => EquipmentChildSchemaRequest.Validate("/E1", "CYLI", null, new string[0], 0, 201),
            () => new E3d21Context().ReadEquipmentChildSchema("/E1", "CYLI", null, new string[0], 0, 201, Session),
            () => new E3d21Context().ReadEquipmentChildSchema("/E1", "CYLI", null, new string[0], 100001, 50, Session),
            () => new E3d21Context().ReadEquipmentChildSchema("/E1", "CYLI", null, new string[21], 0, 50, Session),
            () => new E3d21Context().ReadEquipmentChildSchema("/E1", "CYLI", null, new string[0], 0, 50, ""),
            () => new E3d21Context().ReadEquipmentChildSchema("/E1", "CYLI", null, new string[0], 0, 50, null),
            () => new E3d21Context().ReadEquipmentChildSchema("/E1", "CYLI", null, new string[0], 0, 50, new string('x', 257)) })
        {
            Scenario.Reset(); bool rejected = false;
            try { invalid(); } catch (ArgumentException) { rejected = true; }
            Check(rejected && Scenario.Reads.Count == 0 && Scenario.Dispatches == 0, "Invalid request entered native dispatch.");
        }
        var normalized = EquipmentChildSchemaRequest.Validate(" =01/001 ", " :custom_cyli ", " /e2 ", new[] { " diam " }, 0, 50);
        Check(normalized.ParentRefno == "=1/1" && normalized.ElementType == ":CUSTOM_CYLI" && normalized.ReadTargetRefno == "/e2" && normalized.Attributes[0] == "DIAM", "Normalization drift.");
        foreach (bool ui in new[] { false, true })
        {
            Scenario.Reset(); Scenario.Busy = !ui; Scenario.BusyOnUi = ui; bool rejected = false;
            try { Read(); } catch (InvalidOperationException) { rejected = true; }
            Check(rejected && Scenario.Reads.Count == 0 && Scenario.Dispatches == (ui ? 1 : 0), "Busy host performed SDK reads.");
        }
    }
    private static void Provenance()
    {
        Scenario.Reset(); JObject result = Read(target: null);
        Check(!(bool)result["complete"] && (string)result["instance_attributes"]["reason"] == "read_target_required"
            && !result["instance_attributes"]["attributes"].Any(), "No exemplar must remain unknown.");
        Check((bool)result["type_system_attributes"]["whole_type_coverage"] && (bool)result["parent_creatability"]["sdk_creatable_now"]
            && !Scenario.Reads.Any(key => key.Contains(".supported.") || key.Contains(".settable.")), "Type attributes and parent creatability must not witness instance attributes.");
        Scenario.Reset(); result = Read(kind: "CYLINDER", parent: " /e1 ", target: " /e2 ");
        Check((bool)result["complete"] && (string)result["read_target"]["actual_type"]["name"] == "CYLI"
            && (bool)result["read_target_actual_type_matches"] && (string)result["instance_attributes"]["refno"] == "=1/2", "Alias resolution must use actual SDK type equality.");
        Check(Scenario.Reads.Count(key => key == "element.2.actual_type") == 1, "Actual target type must be captured once.");
        Scenario.Reset(); Scenario.Target.Actual = ":CUSTOM_CYLI"; result = Read(":CUSTOM_CYLI");
        Check((bool)result["complete"] && (string)result["read_target"]["base_type"]["name"] == "CYLI"
            && (string)result["read_target"]["actual_type"]["name"] == ":CUSTOM_CYLI"
            && Scenario.Reads.Contains("element.1.creatable.:CUSTOM_CYLI"), "UDET actual type must remain distinct from base type.");
        Check(!Scenario.Reads.Any(key => key.StartsWith("element.1.supported.") || key.StartsWith("element.9.supported.")), "Never read parent/CE support as child evidence.");
        foreach (var pair in new[] { new[] { "CYLI", ":CUSTOM_CYLI" }, new[] { ":CUSTOM_CYLI", "CYLI" }, new[] { ":CUSTOM_CYLI", ":OTHER_CYLI" } })
        {
            Scenario.Reset(); Scenario.Target.Actual = pair[1]; result = Read(pair[0]);
            Check(!(bool)result["read_target_actual_type_matches"] && (string)result["instance_attributes"]["reason"] == "actual_type_mismatch"
                && !result["instance_attributes"]["attributes"].Any() && !Scenario.Reads.Any(key => key.Contains(".supported.")), "Same base type must not pass an actual-UDET mismatch.");
        }
        Scenario.Reset(); result = Read(target: "=1/404");
        Check(Null(result["read_target"]) && (string)result["instance_attributes"]["reason"] == "read_target_unavailable", "Missing future name/reference is not an exemplar.");
        Scenario.Reset(); Scenario.Overrides["element.2.name"] = "/WRONG_FIRST_NAME"; result = Read(target: "/E2");
        Check(Null(result["read_target"]) && (string)result["instance_attributes"]["reason"] == "read_target_unavailable"
            && !Scenario.Reads.Any(key => key.StartsWith("element.2.supported.")),
            "A first NAME mismatch must never produce a verified exemplar binding or instance attributes.");
        Scenario.Reset(); result = Read(kind: ":UNKNOWN");
        Check(Null(result["parent_creatability"]["sdk_creatable_now"]) && (string)result["instance_attributes"]["reason"] == "requested_type_unknown", "Unknown requested type must not collapse to native base.");
        Scenario.Reset(); Scenario.Overrides["element.1.creatable.CYLI"] = false; result = Read();
        Check((bool)result["complete"] && !(bool)result["parent_creatability"]["sdk_creatable_now"]
            && Null(result["scope"]["permissions_verified"]), "Complete read/IsCreatable false does not authorize or invalidate schema evidence.");
        Scenario.Reset(); Scenario.Overrides["element.2.supported.DIAM"] = false; result = Read();
        Check((bool)result["complete"] && !(bool)result["instance_attributes"]["attributes"][0]["supported"]
            && Null(result["instance_attributes"]["attributes"][0]["sdk_settable_now"])
            && !Scenario.Reads.Contains("element.2.settable.DIAM"), "Unsupported attributes have no settable observation.");
    }
    private static void PartialAndPaging()
    {
        foreach (string key in new[] { "type.CYLI.system_attributes", "element.1.creatable.CYLI", "element.2.actual_type", "element.2.supported.DIAM", "element.2.settable.DIAM", "attribute.DIAM.units", "attribute.resolve.DIAM" })
        {
            Scenario.Reset(); Scenario.Overrides[key] = new InvalidOperationException("ordinary SDK error");
            JObject result = Read();
            Check(!(bool)result["complete"], "Ordinary failure must remain partial: " + key);
            if (key == "element.2.actual_type") Check((string)result["instance_attributes"]["reason"] == "actual_type_unknown", "Unreadable actual type cannot be replaced with its base type.");
            if (key == "type.CYLI.system_attributes") Check((string)result["instance_attributes"]["status"] == "observed", "Independent exemplar evidence must survive a type-list read failure.");
        }
        Scenario.Reset(); JObject unknown = Read(attributes: new[] { "MISSING" });
        Check(unknown["instance_attributes"]["attributes"][0]["known"].Value<bool>() == false
            && Null(unknown["instance_attributes"]["attributes"][0]["supported"]), "Unknown global attribute must remain unknown on instance.");
        foreach (int offset in new[] { 0, 200, 100000 })
        {
            Scenario.Reset(); Scenario.TypeAttributes = Enumerable.Range(0, 205).Select(i => new DbAttribute("A" + i)).ToArray();
            JObject result = Read(offset: offset, limit: 200);
            int expected = offset == 0 ? 200 : offset == 200 ? 5 : 0;
            Check((int)result["type_system_attributes"]["returned_count"] == expected
                && (int)result["type_system_attributes"]["total_count"] == 205
                && !(bool)result["type_system_attributes"]["whole_type_coverage"], "Type array output page/coverage must be explicit.");
        }
        Scenario.Reset(); int reads = 0; Scenario.OnRead = key => { if (key == "mdb.current" && ++reads == 2) Scenario.Overrides[key] = new MDB(); };
        JObject drift = Read(); Check(!(bool)drift["context"]["identity_unchanged"] && !(bool)drift["complete"], "Same text/different MDB object must count as identity drift.");
        Scenario.Reset(); reads = 0; Scenario.OnRead = key => { if (key == "ce" && ++reads == 2) Scenario.Overrides["element.9.base_type"] = Scenario.Types["CYLI"]; };
        drift = Read(); Check(!(bool)drift["context"]["ce_unchanged"] && !(bool)drift["complete"], "Same CE ref/different type must count as CE drift.");
        Scenario.Reset(); reads = 0; Scenario.OnRead = key => { if (key == "ce" && ++reads == 2) Scenario.Overrides["element.9.actual_type"] = Scenario.Types[":CUSTOM_CYLI"]; };
        drift = Read(); Check(!(bool)drift["context"]["ce_unchanged"] && !(bool)drift["complete"], "Same CE ref/base but different actual UDET must count as CE drift.");
        Scenario.Reset(); Scenario.Overrides["element.2.settable.DIAM"] = new InvalidOperationException(new string('x', 3000));
        drift = Read(); Check(((string)drift["instance_attributes"]["attributes"][0]["errors"]["sdk_settable_now"]).Length == 1024, "SDK error text must remain bounded.");
        Scenario.Reset(); Scenario.Overrides["project.name"] = new InvalidOperationException("unavailable");
        drift = Read(); Check(Null(drift["context"]["identity_unchanged"]) && !(bool)drift["complete"], "Unknown boundary must not become unchanged.");
        Scenario.Reset(); Scenario.Overrides["element.2.database"] = DbType.Catalogue;
        drift = Read(); Check((string)drift["instance_attributes"]["reason"] == "read_target_unavailable", "Catalogue object is not a Design exemplar.");
        foreach (string key in new[] { "element.1.name", "element.1.base_type", "element.1.actual_type", "element.2.name", "element.2.base_type" })
        {
            Scenario.Reset(); Scenario.Overrides[key] = new InvalidOperationException("ordinary identity getter error");
            JObject partial = Read();
            Check(!(bool)partial["complete"] && (bool)partial["type_system_attributes"]["complete"]
                && (string)partial["instance_attributes"]["status"] == "observed", "An ordinary optional identity getter failure must preserve independent partial evidence: " + key);
        }
        foreach (bool parentName in new[] { true, false })
        {
            Scenario.Reset(); int names = 0;
            string selected = parentName ? "element.1.name" : "element.2.name";
            Scenario.OnRead = key =>
            {
                // Resolve/bind the explicit name successfully first. Only the
                // later optional identity getter fails, so retain the DBREF.
                if (key == selected && ++names == 2) Scenario.Overrides[key] = new InvalidOperationException("NAME unavailable after explicit resolution");
            };
            JObject partial = Read(parent: parentName ? "/E1" : "=1/1", target: parentName ? "=1/2" : "/E2");
            JToken identity = partial[parentName ? "parent" : "read_target"];
            Check(Null(identity["name"]) && !Null(identity["errors"]["name"])
                && (string)identity["refno"] == (parentName ? "=1/1" : "=1/2")
                && (string)partial["instance_attributes"]["status"] == "observed" && !(bool)partial["complete"],
                "A post-resolution NAME getter failure must preserve bound existing-object evidence even when the request used a name.");
        }
        foreach (bool parentName in new[] { true, false })
        {
            Scenario.Reset(); int names = 0;
            string selected = parentName ? "element.1.name" : "element.2.name";
            Scenario.OnRead = key => { if (key == selected && ++names == 2) Scenario.Overrides[key] = "/RENAMED_AFTER_RESOLUTION"; };
            JObject partial = Read(parent: parentName ? "/E1" : "=1/1", target: parentName ? "=1/2" : "/E2");
            JToken identity = partial[parentName ? "parent" : "read_target"];
            Check((string)identity["request_binding"]["observed_name"] == (parentName ? "/E1" : "/E2")
                && (bool)identity["request_binding"]["verified"] && (string)identity["name"] == "/RENAMED_AFTER_RESOLUTION"
                && !Null(identity["errors"]["request_binding"]) && !(bool)identity["complete"] && !(bool)partial["complete"]
                && !(bool)partial["success"], "A different second NAME must retain both real observations and explicitly fail current binding/completeness, never become ready.");
        }
    }
    private static void InspectionBudget()
    {
        foreach (int offset in new[] { 0, EquipmentChildSchemaRequest.MaximumNativeArrayInspection - 1 })
        {
            Scenario.Reset();
            Scenario.TypeAttributes = Enumerable.Range(0, EquipmentChildSchemaRequest.MaximumNativeArrayInspection).Select(i => new DbAttribute("BOUND" + i)).ToArray();
            JObject result = Read(offset: offset, limit: 200);
            int expected = offset == 0 ? 200 : 1;
            Check((bool)result["type_system_attributes"]["complete"] && !(bool)result["type_system_attributes"]["inspection_limited"]
                && (int)result["type_system_attributes"]["returned_count"] == expected
                && Scenario.Reads.Count(key => key.StartsWith("attribute.BOUND") && key.EndsWith(".valid")) == expected,
                "At the accepted array-size boundary, only requested page entries may be inspected.");
        }
        foreach (string target in new[] { "=1/2", null })
        {
            Scenario.Reset();
            Scenario.TypeAttributes = Enumerable.Range(0, EquipmentChildSchemaRequest.MaximumNativeArrayInspection + 1).Select(i => new DbAttribute("SKIPPED" + i)).ToArray();
            JObject result = Read(target: target);
            JToken observation = result["type_system_attributes"];
            Check((int)observation["native_array_inspection_limit"] == EquipmentChildSchemaRequest.MaximumNativeArrayInspection
                && (bool)observation["inspection_limited"] && (bool)observation["native_arrays_materialized_before_output_limits"]
                && (int)observation["total_count"] == EquipmentChildSchemaRequest.MaximumNativeArrayInspection + 1
                && (int)observation["returned_count"] == 0 && !observation["attributes"].Any()
                && Null(observation["has_more"]) && Null(observation["next_offset"]) && !(bool)observation["complete"]
                && !(bool)observation["whole_type_coverage"] && !Null(observation["errors"]["inspection_limit"])
                && !Scenario.Reads.Any(key => key.StartsWith("attribute.SKIPPED")) && !(bool)result["complete"],
                "Oversized SDK materialization must report limit/unknown without inspecting any array entry or claiming an empty type schema.");
            Check((bool)result["parent_creatability"]["complete"]
                && (string)result["instance_attributes"]["status"] == (target == null ? "unknown" : "observed"),
                "Inspection limit must not erase independent parent or explicit exemplar evidence.");
        }
        Scenario.Reset(); Scenario.Overrides["type.CYLI.system_attributes"] = null;
        JObject missing = Read();
        Check(Null(missing["type_system_attributes"]["total_count"]) && Null(missing["type_system_attributes"]["inspection_limited"])
            && !Null(missing["type_system_attributes"]["errors"]["system_attributes"]), "An unreadable native array must not invent a count or limit observation.");
    }
    private static void RootFailure()
    {
        foreach (string failure in new[] { "missing", "deleted", "ref_getter", "different_name", "oversized_first_name" })
        {
            Scenario.Reset(); string parent = "=1/1";
            if (failure == "missing") Scenario.Elements.Remove(parent);
            if (failure == "deleted") Scenario.Overrides["element.1.deleted"] = true;
            if (failure == "ref_getter") Scenario.Overrides["element.1.ref"] = new InvalidOperationException("parent DBREF getter unavailable");
            if (failure == "different_name") { parent = "/E1"; Scenario.Overrides["element.1.name"] = "/OTHER"; }
            if (failure == "oversized_first_name") { parent = "/E1"; Scenario.Overrides["element.1.name"] = "/" + new string('x', 1024); }
            bool rejected = false;
            try { new E3d21Context().ReadEquipmentChildSchema(parent, "CYLI", "=1/2", new[] { "DIAM" }, 0, 50, Session); }
            catch (InvalidOperationException error)
            {
                rejected = error.Message.StartsWith("equipment_child_schema parent_refno")
                    && error.Message.Contains("have not been observed");
            }
            Check(rejected && Scenario.Dispatches == 1 && Scenario.ForbiddenCalls == 0
                && !Scenario.Reads.Any(key => key.StartsWith("type.resolve.") || key.Contains(".creatable.") || key.Contains(".system_attributes") || key.StartsWith("get.=1/2")),
                "An unreadable root must fail with explicit operation/scope evidence before reading type/exemplar schema, rather than fabricating a partial root.");
        }
    }
    private static int FaultMatrixCase(string kind, string target, string parent, Action configure)
    {
        Scenario.Reset(); configure(); Read(kind: kind, target: target, parent: parent); int length = Scenario.Reads.Count;
        for (int index = 1; index <= length; index++)
        {
            Scenario.Reset(); configure(); var context = new E3d21Context(); int count = 0;
            int selected = index;
            Scenario.OnRead = key => { if (++count == selected) context.Fault(); };
            bool rejected = false;
            try { context.ReadEquipmentChildSchema(parent, kind, target, new[] { "DIAM", ":CUSTOM" }, 0, 50, Session); }
            catch (NativeDatabaseFaultException) { rejected = true; }
            Check(rejected && Scenario.Reads.Count == index && Scenario.UiDepth == 0, "Native fault must stop at the exact failing read.");
            rejected = false;
            try { context.ReadEquipmentChildSchema(parent, kind, target, new string[0], 0, 50, Session); }
            catch (NativeDatabaseFaultException) { rejected = true; }
            Check(rejected && Scenario.Dispatches == 1 && Scenario.Reads.Count == index, "Latched fault must not replay native reads.");
        }
        return length;
    }
    private static void FaultMatrix()
    {
        int positions = FaultMatrixCase("CYLI", "=1/2", "=1/1", () => { });
        positions += FaultMatrixCase("CYLI", "/E2", "/E1", () => { });
        positions += FaultMatrixCase(":CUSTOM_CYLI", "=1/2", "=1/1", () => { Scenario.Target.Actual = ":CUSTOM_CYLI"; });
        Console.WriteLine("Child schema native fault matrix: " + positions + " read positions across DBREF, named, and actual UDET targets.");
    }
    public static int Main()
    {
        try
        {
            Validation(); Provenance(); PartialAndPaging(); InspectionBudget(); RootFailure(); FaultMatrix();
            Console.WriteLine("Equipment child schema production checks passed: " + assertions + " assertions, " + receipts + " receipts.");
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
