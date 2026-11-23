using System;
using System.Collections.Generic;
using System.Linq;
using Aveva.Core.Database;
using E3d21AiBridge;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Aveva.Core.Database
{
    public enum DbType { Design = 1, Catalogue = 2 }
    public enum DbAttributeType { DOUBLE, ELEMENT, STRING, WORD }
    public enum DbAttributeUnit { NONE, DIST }
    public sealed class Project
    {
        public static Project CurrentProject => Scenario.Read("project.current", Scenario.Project);
        public string Name => Scenario.Read("project.name", "PROJECT");
        public static bool operator ==(Project left, Project right) { Scenario.Forbidden("Project SDK =="); return false; }
        public static bool operator !=(Project left, Project right) { Scenario.Forbidden("Project SDK !="); return false; }
        public override bool Equals(object value) => ReferenceEquals(this, value);
        public override int GetHashCode() => 1;
    }
    public sealed class MDB
    {
        public static MDB CurrentMDB => Scenario.Read("mdb.current", Scenario.Mdb);
        public string Name => Scenario.Read("mdb.name", "MDB");
        public static bool operator ==(MDB left, MDB right) { Scenario.Forbidden("MDB SDK =="); return false; }
        public static bool operator !=(MDB left, MDB right) { Scenario.Forbidden("MDB SDK !="); return false; }
        public override bool Equals(object value) => ReferenceEquals(this, value);
        public override int GetHashCode() => 1;
    }
    public static class CurrentElement { public static DbElement Element => Scenario.Read("ce", Scenario.Ce); }
    public sealed class DbElementType
    {
        internal readonly string Key;
        internal DbElementType(string key) { Key = key; }
        public bool IsValid => Scenario.Read("type." + Key + ".valid", true);
        public string Name => Scenario.Read("type." + Key + ".name", Key);
        public string ShortName => Scenario.Read("type." + Key + ".short", Key);
        public static bool operator ==(DbElementType left, DbElementType right) { Scenario.Forbidden("type SDK =="); return false; }
        public static bool operator !=(DbElementType left, DbElementType right) { Scenario.Forbidden("type SDK !="); return false; }
        public override bool Equals(object value) => ReferenceEquals(this, value);
        public override int GetHashCode() => Key.GetHashCode();
    }
    public sealed class DbAttribute
    {
        internal readonly string Key;
        internal DbAttribute(string key) { Key = key; }
        public static DbAttribute GetDbAttribute(string key) => Scenario.Read("attribute.resolve." + key, Scenario.Attributes[key]);
        public bool IsValid => Scenario.Read("attribute." + Key + ".valid", true);
        public DbAttributeType Type => Scenario.Read("attribute." + Key + ".type", Key == "PARAM" ? DbAttributeType.DOUBLE : DbAttributeType.ELEMENT);
        public bool IsArray => Scenario.Read("attribute." + Key + ".array", Key == "PARAM" || Key == "BLRFA");
        public DbAttributeUnit Units => Scenario.Read("attribute." + Key + ".units", DbAttributeUnit.NONE);
        public static bool operator ==(DbAttribute left, DbAttribute right) { Scenario.Forbidden("attribute SDK =="); return false; }
        public static bool operator !=(DbAttribute left, DbAttribute right) { Scenario.Forbidden("attribute SDK !="); return false; }
        public override bool Equals(object value) => ReferenceEquals(this, value);
        public override int GetHashCode() => Key.GetHashCode();
    }
    public static class DbAttributeInstance { public static readonly DbAttribute NAME = new DbAttribute("NAME"); }
    public sealed class DbElement
    {
        internal readonly int Id;
        internal readonly string Kind;
        internal DbElement(int id, string kind) { Id = id; Kind = kind; }
        internal string Key => "element." + Id;
        public static DbElement GetElement(int[] pair) => Scenario.Read("element.resolve", Scenario.Root);
        public bool IsNull => Scenario.Read(Key + ".null", Id == 0);
        public bool IsValid => Scenario.Read(Key + ".valid", true);
        public bool IsDeleted => Scenario.Read(Key + ".deleted", false);
        public int[] Ref => Scenario.Read(Key + ".ref", Id == 0 ? new[] { 0, 0 } : new[] { Id == 9 ? 1 : 2, Id });
        public DbType DbType => Scenario.Read(Key + ".db_type", Id == 9 ? DbType.Design : DbType.Catalogue);
        public DbElementType GetElementType() => Scenario.Read(Key + ".base_type", Scenario.Types[Kind]);
        public DbElementType GetActualType() => Scenario.Read(Key + ".actual_type", Scenario.Types[Kind]);
        public string GetAsString(DbAttribute attribute)
        {
            if (attribute.Key != "NAME") Scenario.Forbidden("formatted arbitrary attribute read");
            return Scenario.Read(Key + ".name", "/ELEMENT" + Id);
        }
        public DbElement Member(int ordinal)
        {
            if (ordinal != 1) Scenario.Forbidden("member traversal exceeded first member");
            return Scenario.Read(Key + ".member1", (DbElement)null);
        }
        public bool IsAttributeValid(DbAttribute attribute) => Scenario.Read(Key + ".supports." + attribute.Key, true);
        public double[] GetDoubleArray(DbAttribute attribute)
        {
            if (attribute.Key != "PARAM") Scenario.Forbidden("unexpected double array");
            return Scenario.Read(Key + ".double_array." + attribute.Key, new[] { 1.0, 2.5 });
        }
        public DbElement GetElement(DbAttribute attribute) => Scenario.Read(Key + ".scalar." + attribute.Key, Scenario.Reference);
        public DbElement[] GetElementArray(DbAttribute attribute) => Scenario.Read(Key + ".array." + attribute.Key, new[] { Scenario.Reference });
        public static bool operator ==(DbElement left, DbElement right) { Scenario.Forbidden("element SDK =="); return false; }
        public static bool operator !=(DbElement left, DbElement right) { Scenario.Forbidden("element SDK !="); return false; }
        public override bool Equals(object value) => ReferenceEquals(this, value);
        public override int GetHashCode() => Id;
        // Deliberately no GetMembers, traversal, write, claim, command, search,
        // Create, scalar numeric, formatted PARAM or geometry APIs are exposed.
    }
}

internal static class Scenario
{
    internal static readonly List<string> Reads = new List<string>();
    internal static readonly Dictionary<string, object> Overrides = new Dictionary<string, object>();
    internal static readonly Dictionary<string, int> Counts = new Dictionary<string, int>();
    internal static readonly Dictionary<string, DbElementType> Types = new Dictionary<string, DbElementType>();
    internal static readonly Dictionary<string, DbAttribute> Attributes = new Dictionary<string, DbAttribute>();
    internal static Project Project;
    internal static MDB Mdb;
    internal static DbElement Root, Ce, Reference, Unset;
    internal static Action<string> OnRead;
    internal static bool Busy, BusyOnUi;
    internal static int UiDepth, Dispatches, ForbiddenCalls;
    internal static T Read<T>(string key, T fallback)
    {
        if (UiDepth != 1) Forbidden("SDK call outside exactly one UI callback: " + key);
        Reads.Add(key); Counts[key] = Counts.TryGetValue(key, out int count) ? count + 1 : 1;
        OnRead?.Invoke(key);
        if (!Overrides.TryGetValue(key, out object value)) return fallback;
        if (value is Exception error) throw error;
        return (T)value;
    }
    internal static void Forbidden(string message) { ForbiddenCalls++; throw new InvalidOperationException(message); }
    internal static void Reset()
    {
        Reads.Clear(); Overrides.Clear(); Counts.Clear(); Types.Clear(); Attributes.Clear();
        foreach (string type in new[] { "SCOM", "CATE", "GMSE", "EQUI", ":CUSTOM_SCOM" }) Types[type] = new DbElementType(type);
        foreach (string name in new[] { "PARAM", "PTREF", "GMREF", "DTREF", "BLRFA", "NGMREF", "PSTREF", "GSTREF" }) Attributes[name] = new DbAttribute(name);
        Project = new Project(); Mdb = new MDB(); Root = new DbElement(1, "SCOM");
        Ce = new DbElement(9, "EQUI"); Reference = new DbElement(2, "GMSE"); Unset = new DbElement(0, "GMSE");
        OnRead = null; Busy = BusyOnUi = false; UiDepth = Dispatches = ForbiddenCalls = 0;
    }
}

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;
        private readonly object commandLock = new object();
        private bool commandActive;
        private string activeCommand = "";
        private readonly Action<Action> invokeOnUi;
        public E3d21Context()
        {
            invokeOnUi = action =>
            {
                if (Scenario.BusyOnUi) commandActive = true;
                Scenario.Dispatches++; Scenario.UiDepth++;
                try { action(); } finally { Scenario.UiDepth--; }
            };
        }
        private bool NativeDispatchBusy() => Scenario.Busy;
        internal void Fault() => nativeFaultState.Observe("Unhandled Database error 538 from ComponentTemplateFake", "");
        private object CurrentElement() => null;
        internal object ReadTemplateFromOwnedScope(string refno, string sessionId)
        {
            var request = ParagonComponentTemplateRequest.Validate(refno);
            string build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
            if (!TryBeginCommand("component-template-core-owner")) throw new InvalidOperationException("Owner gate was not acquired.");
            try
            {
                return OnUi(delegate
                {
                    Project initialProject = Scenario.Project; MDB initialMdb = Scenario.Mdb;
                    var receipt = ReadParagonComponentTemplateCore(request, sessionId, build, out ParagonComponentTemplateCapture capture);
                    var target = receipt["target"] as Dictionary<string, object>;
                    return new Dictionary<string, object>
                    {
                        ["receipt"] = receipt, ["gate_still_owned"] = commandActive,
                        ["native_scope_owned"] = ReferenceEquals(currentNativeFaultState, nativeFaultState),
                        ["capture_present"] = !ReferenceEquals(capture, null),
                        ["capture_source_bound"] = ReferenceEquals(capture, null) ? (bool?)null : ReferenceEquals(capture.Source, Scenario.Root),
                        ["capture_project_original"] = ReferenceEquals(capture, null) ? (bool?)null : ReferenceEquals(capture.OriginalProject, initialProject),
                        ["capture_mdb_original"] = ReferenceEquals(capture, null) ? (bool?)null : ReferenceEquals(capture.OriginalMdb, initialMdb),
                        ["capture_binding"] = ReferenceEquals(capture, null) ? null : capture.VerifiedFirstBinding,
                        ["capture_binding_detached"] = ReferenceEquals(capture, null) ? (bool?)null : !ReferenceEquals(capture.VerifiedFirstBinding, target?["request_binding"]),
                        ["capture_serialized"] = ReferenceEquals(capture, null) ? null : JsonConvert.SerializeObject(capture)
                    };
                });
            }
            finally { EndCommand(); }
        }
        internal object ReadPublicFromOwnedScope(string refno, string sessionId)
        {
            if (!TryBeginCommand("component-template-public-owner")) throw new InvalidOperationException("Owner gate was not acquired.");
            try { return OnUi(() => ReadParagonComponentTemplate(refno, sessionId)); }
            finally { EndCommand(); }
        }
        internal object ReadCoreWithoutScope() => ReadParagonComponentTemplateCore(
            ParagonComponentTemplateRequest.Validate("=2/1"), "offline-session", "offline-build", out _);
        internal object ReadOtherContextCoreFromScope(E3d21Context other) => OnUi(() => other.ReadCoreWithoutScope());
        internal object ReadCoreAfterFaultInsideScope() => OnUi(delegate { Fault(); return ReadCoreWithoutScope(); });
        internal object ReadNullRequestCoreFromOwnedScope()
        {
            if (!TryBeginCommand("component-template-exception-owner")) throw new InvalidOperationException("Owner gate was not acquired.");
            try { return OnUi(() => ReadParagonComponentTemplateCore(null, "offline-session", "offline-build", out _)); }
            finally { EndCommand(); }
        }
        internal bool CommandGateOwned => commandActive;
        // PRODUCTION_NATIVE_HELPERS
    }
}

internal static class ParagonComponentTemplateHarness
{
    private const string Session = "paragon-component-template-offline-session";
    private static int assertions, receipts;
    private static bool Null(JToken token) => token == null || token.Type == JTokenType.Null;
    private static void Check(bool condition, string message)
    {
        assertions++;
        if (!condition) throw new Exception(message + "\nReads: " + string.Join(", ", Scenario.Reads));
    }
    private static JObject Read()
    {
        object result = new E3d21Context().ReadParagonComponentTemplate("=2/1", Session);
        Check(Scenario.Dispatches == 1 && Scenario.ForbiddenCalls == 0, "Read-only request must use exactly one UI callback.");
        string build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
        var reply = JObject.FromObject(new { ok = true, version = "E3D21", session_id = Session, bridge_build = build, result });
        var row = (JObject)reply["result"];
        foreach (JToken boundary in new[] { row, row["context"]["before"], row["context"]["after"] })
            Check((string)boundary["session_id"] == Session && (string)boundary["bridge_build"] == build, "Boundary/build binding drifted.");
        Check((bool)row["read_only"] && (bool)row["single_dispatch"] && !(bool)row["atomic_snapshot"]
            && !(bool)row["scope"]["whole_clone_fingerprint"] && !(bool)row["scope"]["word_parameters_observed"]
            && !(bool)row["scope"]["geometry_created"] && !(bool)row["scope"]["model_written"]
            && Null(row["scope"]["write_permission_verified"]), "Scope must never promote bounded read evidence to clone or write authority.");
        Console.WriteLine("Component template receipt: " + JObject.FromObject(new { payload = new { refno = "=2/1" }, reply }).ToString(Formatting.None));
        receipts++;
        return row;
    }
    private static void Validation()
    {
        foreach (string invalid in new[] { null, "", "CE", "/NAME", "=0/1", "=01/1", "=1/01", "=1/-1", "=2147483648/0", "=1/2147483648", " =2/1", "=2/1 ", "=2/1\n", "=2/*", "=2/1/1" })
        {
            Scenario.Reset(); bool rejected = false;
            try { new E3d21Context().ReadParagonComponentTemplate(invalid, Session); } catch (ArgumentException) { rejected = true; }
            Check(rejected && Scenario.Reads.Count == 0 && Scenario.Dispatches == 0, "Invalid explicit numeric target reached native dispatch.");
        }
        Check(ParagonComponentTemplateRequest.Validate("=2147483647/2147483647").Pair[1] == int.MaxValue, "Int32 boundary rejected.");
        Check(ParagonComponentTemplateRequest.Validate("=1/0").Pair[1] == 0, "Zero element number rejected.");
        foreach (string session in new[] { null, "", " ", new string('x', 257) })
        {
            Scenario.Reset(); bool rejected = false;
            try { new E3d21Context().ReadParagonComponentTemplate("=2/1", session); } catch (ArgumentException) { rejected = true; }
            Check(rejected && Scenario.Reads.Count == 0 && Scenario.Dispatches == 0, "Invalid server session reached native dispatch.");
        }
        foreach (bool inside in new[] { false, true })
        {
            Scenario.Reset(); Scenario.Busy = !inside; Scenario.BusyOnUi = inside; bool rejected = false;
            try { new E3d21Context().ReadParagonComponentTemplate("=2/1", Session); } catch (InvalidOperationException) { rejected = true; }
            Check(rejected && Scenario.Reads.Count == 0 && Scenario.Dispatches == (inside ? 1 : 0), "Busy request touched SDK.");
        }
        string cut = ParagonComponentTemplateRequest.ErrorText(new string('x', 4092) + "\ud83d\ude00" + new string('x', 10));
        Check(cut.Length <= 4096 && !char.IsHighSurrogate(cut[cut.Length - 4]), "Error clipping split a surrogate pair.");
    }
    private static void Cases()
    {
        Scenario.Reset(); JObject row = Read();
        Check((bool)row["complete"] && (bool)row["leaf"]["is_leaf"] && (string)row["target"]["actual_type"]["short_name"] == "SCOM", "Complete native baseline failed.");
        Check((string)row["references"]["BLRFA"]["shape"] == "element_array"
            && Scenario.Reads.Contains("element.1.array.BLRFA") && !Scenario.Reads.Contains("element.1.scalar.BLRFA"), "BLRFA was forced through scalar getter.");
        Check((string)row["target"]["request_binding"]["source"] == "explicit_request_resolution"
            && (bool)row["target"]["request_binding"]["verified"] && Null(row["target"]["request_binding"]["observed_name"]), "Numeric first binding is not positive and explicit.");
        Scenario.Reset(); Scenario.Overrides["element.1.actual_type"] = Scenario.Types[":CUSTOM_SCOM"]; row = Read();
        Check((string)row["target"]["type"] == "SCOM" && (string)row["target"]["actual_type"]["short_name"] == ":CUSTOM_SCOM", "Actual UDET was disguised as native base SCOM.");
        Scenario.Reset(); Scenario.Overrides["element.1.name"] = new InvalidOperationException("NAME failed"); row = Read();
        Check(!(bool)row["complete"] && (bool)row["target"]["request_binding"]["verified"] && Null(row["target"]["name"])
            && !Null(row["target"]["errors"]["name"]) && (int)row["parameters"]["returned_count"] == 2, "Ordinary NAME failure erased independent first binding/value evidence.");
        foreach (string key in new[] { "element.1.actual_type", "element.1.member1", "element.1.double_array.PARAM", "attribute.PARAM.type", "attribute.PARAM.array", "attribute.PARAM.units" })
        {
            Scenario.Reset(); Scenario.Overrides[key] = new InvalidOperationException("ordinary read failure"); row = Read();
            Check(!(bool)row["complete"] && row["target"] is JObject && (bool)row["target"]["request_binding"]["verified"], "Ordinary optional read failure lost target binding or became complete.");
        }
        Scenario.Reset(); Scenario.Overrides["element.1.member1"] = Scenario.Reference; row = Read();
        Check(!(bool)row["leaf"]["is_leaf"] && (string)row["leaf"]["first_member"]["refno"] == "=2/2" && !Scenario.Reads.Contains("element.2.member1"), "Leaf check traversed beyond Member(1).");
        foreach (object value in new object[] { new[] { 3, 1 }, null, new int[0] })
        {
            Scenario.Reset(); Scenario.Overrides["element.1.ref"] = value; row = Read();
            Check(Null(row["target"]) && !(bool)row["complete"] && !Scenario.Reads.Any(key => key.StartsWith("attribute.resolve.")), "Unbound/wrong numeric root read values.");
        }
        foreach (object value in new object[] { DbType.Design, (DbType)0 })
        {
            Scenario.Reset(); Scenario.Overrides["element.1.db_type"] = value; row = Read();
            Check(Null(row["target"]) && !Scenario.Reads.Any(key => key.StartsWith("attribute.resolve.")), "Non-Catalogue root read values.");
        }
        Scenario.Reset(); Scenario.Overrides["element.1.base_type"] = Scenario.Types["CATE"]; row = Read();
        Check(Null(row["target"]) && !Scenario.Reads.Any(key => key.StartsWith("attribute.resolve.")), "Non-SCOM root read values.");
        Scenario.Reset(); Scenario.OnRead = key => { if (key == "element.1.ref" && Scenario.Counts[key] == 2) Scenario.Overrides[key] = new[] { 2, 3 }; }; row = Read();
        Check(!(bool)row["complete"] && !Null(row["target"]["errors"]["request_binding"])
            && !Scenario.Reads.Any(key => key.StartsWith("attribute.resolve.")), "Second DBREF drift lost original binding or allowed value reads.");
        Scenario.Reset(); Scenario.Overrides["element.1.double_array.PARAM"] = Enumerable.Range(0, 129).Select(index => (double)index).ToArray(); row = Read();
        Check((int)row["parameters"]["total_count"] == 129 && (int)row["parameters"]["returned_count"] == 128 && (bool)row["parameters"]["truncated"] && !(bool)row["complete"], "PARAM output budget not preserved.");
        Scenario.Reset(); Scenario.Overrides["element.1.double_array.PARAM"] = new double[4097]; row = Read();
        Check((bool)row["parameters"]["inspection_limited"] && (int)row["parameters"]["returned_count"] == 0 && (bool)row["parameters"]["native_array_materialized"], "PARAM inspection budget misreported native allocation.");
        Scenario.Reset(); Scenario.Overrides["element.1.double_array.PARAM"] = new[] { double.NaN, double.PositiveInfinity, double.NegativeInfinity, 4.0 }; row = Read();
        Check(Null(row["parameters"]["values"][0]["value"]) && (double)row["parameters"]["values"][3]["value"] == 4.0 && !(bool)row["parameters"]["complete"], "Nonfinite PARAM was invented or discarded independent finite values.");
        Scenario.Reset(); Scenario.Overrides["element.1.double_array.PARAM"] = null; row = Read();
        Check(Null(row["parameters"]["total_count"]) && (string)row["parameters"]["status"] == "unknown", "Null PARAM became an empty observed array.");
        Scenario.Reset(); Scenario.Overrides["element.1.double_array.PARAM"] = new double[0]; row = Read();
        Check((int)row["parameters"]["total_count"] == 0 && (bool)row["parameters"]["complete"], "Empty native PARAM array was confused with null.");
        foreach (string key in new[] { "attribute.PARAM.type", "attribute.PARAM.array" })
        {
            Scenario.Reset(); Scenario.Overrides[key] = key.EndsWith("type") ? (object)DbAttributeType.WORD : false; row = Read();
            Check(!Scenario.Reads.Contains("element.1.double_array.PARAM") && !(bool)row["parameters"]["complete"], "Unknown/wrong PARAM shape invoked typed array getter.");
        }
        Scenario.Reset(); Scenario.Overrides["element.1.supports.PARAM"] = false; row = Read();
        Check(!(bool)row["parameters"]["supported"] && !Scenario.Reads.Contains("element.1.double_array.PARAM"), "Unsupported PARAM invoked value getter.");
        Scenario.Reset(); Scenario.Overrides["element.1.scalar.PTREF"] = null; row = Read();
        Check(Null(row["references"]["PTREF"]["reference_null"]) && (string)row["references"]["PTREF"]["values"][0]["status"] == "unknown"
            && (string)row["references"]["PTREF"]["values"][0]["error"] == "native_reference_handle_unknown", "CLR null reference became an observed native null value.");
        Scenario.Reset(); Scenario.Overrides["element.1.array.BLRFA"] = null; row = Read();
        Check(Null(row["references"]["BLRFA"]["total_count"]) && Null(row["references"]["BLRFA"]["reference_null"])
            && !(bool)row["references"]["BLRFA"]["complete"], "CLR null reference array became an observed empty array.");
        Scenario.Reset(); Scenario.Overrides["element.1.array.BLRFA"] = new DbElement[0]; row = Read();
        Check((int)row["references"]["BLRFA"]["total_count"] == 0 && (bool)row["references"]["BLRFA"]["complete"], "Empty array became unknown.");
        Scenario.Reset(); Scenario.Overrides["element.1.array.BLRFA"] = Enumerable.Repeat(Scenario.Reference, 17).ToArray(); row = Read();
        Check((int)row["references"]["BLRFA"]["total_count"] == 17 && (int)row["references"]["BLRFA"]["returned_count"] == 16 && (bool)row["references"]["BLRFA"]["truncated"], "Reference array output budget failed.");
        Scenario.Reset(); Scenario.Overrides["element.1.array.BLRFA"] = Enumerable.Repeat(Scenario.Reference, 4097).ToArray(); row = Read();
        Check((bool)row["references"]["BLRFA"]["inspection_limited"] && (int)row["references"]["BLRFA"]["returned_count"] == 0
            && Scenario.Reads.Count(key => key == "element.2.name") == 6, "Over-inspection array accessed item metadata.");
        Scenario.Reset(); Scenario.Overrides["attribute.BLRFA.array"] = false; row = Read();
        Check((string)row["references"]["BLRFA"]["shape"] == "element" && Scenario.Reads.Contains("element.1.scalar.BLRFA")
            && !Scenario.Reads.Contains("element.1.array.BLRFA"), "Reference metadata shape was ignored.");
        Scenario.Reset(); Scenario.Overrides["attribute.BLRFA.type"] = DbAttributeType.WORD; row = Read();
        Check(!Scenario.Reads.Contains("element.1.array.BLRFA") && !Scenario.Reads.Contains("element.1.scalar.BLRFA"), "WORD reference shape invoked ELEMENT getter.");
        Scenario.Reset(); Scenario.Overrides["element.2.name"] = new InvalidOperationException("reference name unknown"); row = Read();
        Check((string)row["references"]["PTREF"]["values"][0]["identity"]["refno"] == "=2/2"
            && (string)row["references"]["PTREF"]["values"][0]["status"] == "partial", "Reference NAME error erased typed DBREF evidence.");
        Scenario.Reset(); Scenario.OnRead = key => { if (key == "project.current" && Scenario.Counts[key] == 2) Scenario.Project = new Project();
            if (key == "project.current" && Scenario.Counts[key] == 2) Scenario.Overrides[key] = Scenario.Project; }; row = Read();
        Check(!(bool)row["context"]["same_project_instance"] && !(bool)row["context"]["boundary_agreement"] && !(bool)row["complete"], "Same-name project instance replacement went undetected.");
        Scenario.Reset(); Scenario.OnRead = key => { if (key == "mdb.current" && Scenario.Counts[key] == 2) Scenario.Overrides[key] = new MDB(); }; row = Read();
        Check(!(bool)row["context"]["same_mdb_instance"] && !(bool)row["context"]["boundary_agreement"], "Same-name MDB instance replacement went undetected.");
        Scenario.Reset(); Scenario.OnRead = key => { if (key == "element.9.base_type" && Scenario.Counts[key] == 2) Scenario.Overrides[key] = Scenario.Types["CATE"]; }; row = Read();
        Check(!(bool)row["context"]["boundary_agreement"], "Typed CE drift was hidden by equal DBREF.");
        Scenario.Reset(); Scenario.Overrides["project.name"] = new InvalidOperationException(new string('x', 10000)); row = Read();
        Check(Null(row["context"]["before"]["project"]) && ((string)row["context"]["before"]["errors"]["project"]).Length <= 4096
            && Null(row["context"]["boundary_agreement"]), "Context unknown/error budget was not preserved.");
        Scenario.Reset(); Scenario.Overrides["mdb.current"] = null; row = Read();
        Check(Null(row["target"]) && !Scenario.Reads.Contains("element.resolve") && Null(row["context"]["same_mdb_instance"]), "Missing MDB resolved native target.");
    }
    private static void ConfigureUnsetReferences(bool allAttributes)
    {
        Scenario.Overrides["element.1.scalar.PTREF"] = Scenario.Unset;
        Scenario.Overrides["element.1.array.BLRFA"] = new[] { Scenario.Unset, Scenario.Reference, Scenario.Unset };
        if (allAttributes)
        {
            foreach (string name in ParagonComponentTemplateRequest.ReferenceAttributes.Where(name => name != "BLRFA"))
                Scenario.Overrides["element.1.scalar." + name] = Scenario.Unset;
            Scenario.Overrides["element.1.array.BLRFA"] = new[] { Scenario.Unset, Scenario.Unset };
        }
    }
    private static void CheckUnset(JToken row, int index, string message)
    {
        Check((int)row["index"] == index && (string)row["status"] == "unset" && (bool?)row["reference_null"] == true
            && Null(row["identity"]) && Null(row["error"]), message);
    }
    private static void UnsetReferenceCases()
    {
        Scenario.Reset(); Scenario.Overrides["element.1.scalar.PTREF"] = Scenario.Unset;
        JObject row = Read(); JToken attribute = row["references"]["PTREF"];
        CheckUnset(attribute["values"][0], 0, "SDK null/[0,0] scalar did not retain the observed unset value.");
        Check((bool)attribute["reference_null"] && (bool)attribute["complete"] && (string)attribute["status"] == "observed"
            && (bool)row["complete"], "Observed unset scalar did not count as complete value evidence.");
        Check(Scenario.Reads.Where(key => key.StartsWith("element.0.")).SequenceEqual(new[] { "element.0.null", "element.0.ref" }),
            "Observed unset required anything beyond IsNull and the exact native null DBREF.");

        Scenario.Reset(); Scenario.Overrides["element.1.array.BLRFA"] = new[] { Scenario.Reference, Scenario.Unset, new DbElement(3, "GMSE") };
        row = Read(); attribute = row["references"]["BLRFA"];
        Check((int)attribute["total_count"] == 3 && (int)attribute["returned_count"] == 3 && (bool)attribute["complete"]
            && Null(attribute["reference_null"]) && (bool)attribute["native_array_materialized"], "Mixed native reference array lost shape, order or completeness.");
        Check((string)attribute["values"][0]["identity"]["refno"] == "=2/2"
            && (string)attribute["values"][2]["identity"]["refno"] == "=2/3", "Mixed array changed non-null target identities.");
        CheckUnset(attribute["values"][1], 1, "Mixed array null item was discarded or treated as a missing identity read.");

        Scenario.Reset(); ConfigureUnsetReferences(true); row = Read();
        foreach (string name in ParagonComponentTemplateRequest.ReferenceAttributes)
        {
            attribute = row["references"][name];
            Check((bool)attribute["complete"] && (string)attribute["status"] == "observed", "All-unset attribute did not retain complete value coverage: " + name);
            for (int index = 0; index < (int)attribute["returned_count"]; index++)
                CheckUnset(attribute["values"][index], index, "Fully null native array/scalar item was not observed unset.");
        }
        Check((bool)row["complete"] && (bool)row["success"], "Complete native unset values made the overall bounded observation partial.");

        Scenario.Reset(); Scenario.Overrides["element.1.array.BLRFA"] = new[] { Scenario.Reference, (DbElement)null, Scenario.Unset };
        row = Read(); attribute = row["references"]["BLRFA"];
        Check((string)attribute["values"][1]["status"] == "unknown" && Null(attribute["values"][1]["reference_null"])
            && (string)attribute["values"][1]["error"] == "native_reference_handle_unknown" && !(bool)attribute["complete"],
            "CLR null array handle was promoted to known unset or erased its sibling evidence.");
        CheckUnset(attribute["values"][2], 2, "Independent SDK null item was lost after a CLR null item.");
        Check((string)attribute["values"][0]["identity"]["refno"] == "=2/2", "CLR null array item erased a valid preceding reference.");

        Scenario.Reset(); Scenario.Overrides["element.1.array.BLRFA"] = new DbElement[] { null, null };
        row = Read(); attribute = row["references"]["BLRFA"];
        Check((int)attribute["total_count"] == 2 && (int)attribute["returned_count"] == 2 && !(bool)attribute["complete"]
            && attribute["values"].All(item => (string)item["status"] == "unknown" && Null(item["reference_null"])
                && (string)item["error"] == "native_reference_handle_unknown"), "Fully CLR null array items became an empty or complete observed array.");

        foreach (bool array in new[] { false, true })
            foreach (object pair in new object[] { null, new int[0], new[] { 0 }, new[] { 0, 0, 0 }, new[] { 0, 1 }, new[] { 1, 0 }, new[] { 2, 3 },
                new[] { -1, 0 }, new InvalidOperationException("unset native Ref getter failed") })
            {
                Scenario.Reset();
                Scenario.Overrides[array ? "element.1.array.BLRFA" : "element.1.scalar.PTREF"] = array ? (object)new[] { Scenario.Unset } : Scenario.Unset;
                Scenario.Overrides["element.0.ref"] = pair; row = Read(); attribute = row["references"][array ? "BLRFA" : "PTREF"];
                JToken value = attribute["values"][0];
                Check((string)value["status"] == "unknown" && (bool?)value["reference_null"] == true && Null(value["identity"])
                    && !Null(value["error"]) && ((string)value["error"]).Length > 0 && !(bool)attribute["complete"] && !(bool)row["complete"],
                    "Malformed/failed null sentinel DBREF was promoted to observed unset.");
                Check(Scenario.Reads.Where(key => key.StartsWith("element.0.")).SequenceEqual(new[] { "element.0.null", "element.0.ref" }),
                    "Unknown null sentinel touched identity metadata after the failed DBREF proof.");
            }
        foreach (bool array in new[] { false, true })
        {
            Scenario.Reset();
            Scenario.Overrides[array ? "element.1.array.BLRFA" : "element.1.scalar.PTREF"] = array ? (object)new[] { Scenario.Unset } : Scenario.Unset;
            Scenario.Overrides["element.0.null"] = new InvalidOperationException("native IsNull getter failed");
            row = Read(); attribute = row["references"][array ? "BLRFA" : "PTREF"];
            Check((string)attribute["values"][0]["status"] == "unknown" && Null(attribute["values"][0]["reference_null"])
                && (string)attribute["values"][0]["error"] == "native IsNull getter failed" && !Scenario.Reads.Contains("element.0.ref")
                && !(bool)attribute["complete"], "Failed IsNull getter invented nullness or performed a later Ref read.");
        }
    }
    private static void FaultMatrix()
    {
        int positions = 0, cases = 0;
        foreach (int path in new[] { 0, 1, 2, 3, 4 })
        {
            Action setup = () =>
            {
                Scenario.Reset();
                if (path == 1) { Scenario.Overrides["element.1.member1"] = Scenario.Reference;
                    Scenario.Overrides["element.1.array.BLRFA"] = new[] { Scenario.Reference, new DbElement(3, "GMSE") }; }
                if (path == 2) Scenario.Overrides["element.1.actual_type"] = Scenario.Types[":CUSTOM_SCOM"];
                if (path == 3) ConfigureUnsetReferences(false);
                if (path == 4) ConfigureUnsetReferences(true);
            };
            setup(); new E3d21Context().ReadParagonComponentTemplate("=2/1", Session);
            int total = Scenario.Reads.Count; positions += total;
            foreach (bool throws in new[] { false, true })
                for (int ordinal = 1; ordinal <= total; ordinal++)
                {
                    setup(); var context = new E3d21Context(); int hit = ordinal;
                    Scenario.OnRead = key => { if (Scenario.Reads.Count == hit) { context.Fault(); if (throws) throw new InvalidOperationException("ordinary failure plus native fault"); } };
                    bool stopped = false;
                    try { context.ReadParagonComponentTemplate("=2/1", Session); } catch (NativeDatabaseFaultException) { stopped = true; }
                    Check(stopped && Scenario.Reads.Count == ordinal && Scenario.ForbiddenCalls == 0, "Latched native fault allowed a later SDK read or became ordinary partial.");
                    bool replay = false; Scenario.OnRead = null;
                    try { context.ReadParagonComponentTemplate("=2/1", Session); } catch (NativeDatabaseFaultException) { replay = true; }
                    Check(replay && Scenario.Reads.Count == ordinal, "Latched native request replay touched SDK.");
                    cases++;
                }
        }
        Check(positions >= 741 && cases == positions * 2, "The public reader native-fault matrix lost production SDK read positions.");
        Console.WriteLine("Component template native fault matrix: " + positions + " read positions / " + cases + " return-or-throw cases; every fault stopped immediately and refused replay.");
    }
    private static void OwnedCore()
    {
        foreach (int path in new[] { 0, 1, 2, 3 })
        {
            Action setup = () =>
            {
                Scenario.Reset();
                if (path == 1) Scenario.Overrides["element.1.name"] = new InvalidOperationException("NAME remains unknown");
                if (path == 2) Scenario.Overrides["element.1.ref"] = new[] { 3, 1 };
                if (path == 3) Scenario.OnRead = key =>
                {
                    if (key == "project.current" && Scenario.Counts[key] == 2) Scenario.Overrides[key] = new Project();
                    if (key == "mdb.current" && Scenario.Counts[key] == 2) Scenario.Overrides[key] = new MDB();
                };
            };
            setup();
            var expected = JObject.FromObject(new E3d21Context().ReadParagonComponentTemplate("=2/1", Session));
            string[] expectedReads = Scenario.Reads.ToArray();
            setup(); var context = new E3d21Context();
            var owned = JObject.FromObject(context.ReadTemplateFromOwnedScope("=2/1", Session));
            Check(JToken.DeepEquals(expected, owned["receipt"]), "Owned core changed public receipt shape or partial semantics.");
            Check(Scenario.Reads.SequenceEqual(expectedReads) && Scenario.Dispatches == 1 && Scenario.ForbiddenCalls == 0,
                "Owned core added a dispatch, bypassed guarded SDK reads, or changed their order.");
            Check((bool)owned["gate_still_owned"] && (bool)owned["native_scope_owned"] && !context.CommandGateOwned,
                "Core consumed its caller's gate or failed to retain the caller's native scope.");
            Check((bool)owned["capture_present"] == (path != 2), "Capture exists without a verified first numeric Catalogue SCOM binding.");
            if (path != 2)
            {
                Check((bool)owned["capture_source_bound"] && (bool)owned["capture_project_original"] && (bool)owned["capture_mdb_original"],
                    "Capture lost the source handle or original project/MDB instances.");
                Check((bool)owned["capture_binding_detached"] && (bool)owned["capture_binding"]["verified"]
                    && (string)owned["capture_binding"]["resolved_refno"] == "=2/1" && (string)owned["capture_serialized"] == "{}",
                    "Capture binding aliases the receipt or exposes serializable SDK handles.");
            }
        }
        Scenario.Reset(); var publicContext = new E3d21Context(); bool busy = false;
        try { publicContext.ReadPublicFromOwnedScope("=2/1", Session); }
        catch (InvalidOperationException error) { busy = error.Message == "E3D is busy."; }
        Check(busy && Scenario.Reads.Count == 0 && Scenario.Dispatches == 1 && !publicContext.CommandGateOwned,
            "Public reader failed to reject its caller's occupied command gate before any SDK read.");
        Scenario.Reset(); bool noScope = false;
        try { new E3d21Context().ReadCoreWithoutScope(); }
        catch (InvalidOperationException error) { noScope = error.Message.Contains("this context's active E3D SDK scope"); }
        Check(noScope && Scenario.Reads.Count == 0 && Scenario.Dispatches == 0, "Core read outside an existing native scope.");
        Scenario.Reset(); bool wrongContext = false;
        try { new E3d21Context().ReadOtherContextCoreFromScope(new E3d21Context()); }
        catch (InvalidOperationException error) { wrongContext = error.Message.Contains("this context's active E3D SDK scope"); }
        Check(wrongContext && Scenario.Reads.Count == 0 && Scenario.Dispatches == 1,
            "Core borrowed another context's ThreadStatic fault gate or swallowed its ordinary scope error.");
        Scenario.Reset(); var faultContext = new E3d21Context(); bool prelatched = false; faultContext.Fault();
        try { faultContext.ReadTemplateFromOwnedScope("=2/1", Session); } catch (NativeDatabaseFaultException) { prelatched = true; }
        Check(prelatched && Scenario.Reads.Count == 0 && Scenario.Dispatches == 0 && !faultContext.CommandGateOwned,
            "Pre-latched native fault entered the owner gate or dispatched the core.");
        Scenario.Reset(); bool inScopeFault = false;
        try { new E3d21Context().ReadCoreAfterFaultInsideScope(); } catch (NativeDatabaseFaultException) { inScopeFault = true; }
        Check(inScopeFault && Scenario.Reads.Count == 0 && Scenario.Dispatches == 1, "Core swallowed a native fault latched in its existing UI scope.");
        Scenario.Reset(); var failureContext = new E3d21Context(); bool ordinaryFailure = false;
        try { failureContext.ReadNullRequestCoreFromOwnedScope(); } catch (NullReferenceException) { ordinaryFailure = true; }
        Check(ordinaryFailure && Scenario.Dispatches == 1 && !failureContext.CommandGateOwned,
            "Unexpected managed core failure was swallowed or the owner did not release its own gate.");
        Console.WriteLine("Component template owned core checks passed: one existing UI dispatch, occupied owner gate, private detached capture, same-context guard, and propagated failures.");
    }
    private static void OwnedCoreFaultMatrix()
    {
        int positions = 0, cases = 0;
        foreach (int path in new[] { 0, 1, 2, 3, 4 })
        {
            Action setup = () =>
            {
                Scenario.Reset();
                if (path == 1) { Scenario.Overrides["element.1.member1"] = Scenario.Reference;
                    Scenario.Overrides["element.1.array.BLRFA"] = new[] { Scenario.Reference, new DbElement(3, "GMSE") }; }
                if (path == 2) Scenario.Overrides["element.1.actual_type"] = Scenario.Types[":CUSTOM_SCOM"];
                if (path == 3) ConfigureUnsetReferences(false);
                if (path == 4) ConfigureUnsetReferences(true);
            };
            setup(); new E3d21Context().ReadTemplateFromOwnedScope("=2/1", Session);
            int total = Scenario.Reads.Count; positions += total;
            foreach (bool throws in new[] { false, true })
                for (int ordinal = 1; ordinal <= total; ordinal++)
                {
                    setup(); var context = new E3d21Context(); int hit = ordinal;
                    Scenario.OnRead = key => { if (Scenario.Reads.Count == hit) { context.Fault(); if (throws) throw new InvalidOperationException("ordinary failure plus native fault"); } };
                    bool stopped = false;
                    try { context.ReadTemplateFromOwnedScope("=2/1", Session); } catch (NativeDatabaseFaultException) { stopped = true; }
                    Check(stopped && Scenario.Reads.Count == ordinal && Scenario.ForbiddenCalls == 0 && Scenario.Dispatches == 1 && !context.CommandGateOwned,
                        "Owned core fault allowed a later SDK read, nested dispatch, swallowed fault, or leaked its caller's gate.");
                    bool replay = false; Scenario.OnRead = null;
                    try { context.ReadTemplateFromOwnedScope("=2/1", Session); } catch (NativeDatabaseFaultException) { replay = true; }
                    Check(replay && Scenario.Reads.Count == ordinal && Scenario.Dispatches == 1,
                        "Latched owned-core replay entered native dispatch or SDK.");
                    cases++;
                }
        }
        Check(positions >= 741 && cases == positions * 2, "The owned core native-fault matrix lost production SDK read positions.");
        Console.WriteLine("Component template owned core native fault matrix: " + positions + " read positions / " + cases + " return-or-throw cases; every fault stopped immediately and refused replay.");
    }
    private static int Main()
    {
        try { Validation(); Cases(); UnsetReferenceCases(); FaultMatrix(); OwnedCore(); OwnedCoreFaultMatrix(); Console.WriteLine("Component template production checks passed: " + assertions + " assertions / " + receipts + " receipts."); return 0; }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
