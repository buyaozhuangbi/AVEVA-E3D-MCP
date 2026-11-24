using System;
using System.Collections.Generic;
using System.Linq;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
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
        public override int GetHashCode() { Scenario.Forbidden("SDK identity hash"); return 1; }
    }
    public sealed class MDB
    {
        public static MDB CurrentMDB => Scenario.Read("mdb.current", Scenario.Mdb);
        public string Name => Scenario.Read("mdb.name", "MDB");
        public DbElement FindElement(DbType db, string name)
        {
            if (db != DbType.Catalogue) Scenario.Forbidden("Design name resolver");
            return Scenario.Read("name.lookup", Scenario.Named ?? DbElement.Null);
        }
        public static bool operator ==(MDB left, MDB right) { Scenario.Forbidden("MDB SDK =="); return false; }
        public static bool operator !=(MDB left, MDB right) { Scenario.Forbidden("MDB SDK !="); return false; }
        public override bool Equals(object value) => ReferenceEquals(this, value);
        public override int GetHashCode() { Scenario.Forbidden("SDK identity hash"); return 1; }
    }
    public static class CurrentElement { public static DbElement Element => Scenario.Read("ce", Scenario.Ce); }
    public sealed class DbElementType
    {
        internal readonly string Key;
        internal DbElementType(string key) { Key = key; }
        public bool IsValid => Scenario.Read("type." + Key + ".valid", true);
        public string Name => Scenario.Read("type." + Key + ".name", Key);
        public string ShortName => Scenario.Read("type." + Key + ".short", Key);
        public static DbElementType GetElementType(string key) => Scenario.Read("type.resolve." + key, Scenario.Types[key]);
        public bool IsAttributeValid(DbAttribute attribute) => Scenario.Read("type." + Key + ".supports." + attribute.Key, true);
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
        public DbAttributeType Type => Scenario.Read("attribute." + Key + ".type", Key == "PARAM" ? DbAttributeType.DOUBLE : Key == "NAME" ? DbAttributeType.STRING : DbAttributeType.ELEMENT);
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
        internal string DisplayName;
        internal DbElement(int id, string kind) { Id = id; Kind = kind; }
        internal string Key => "element." + Id;
        internal static readonly DbElement Null = new DbElement(0, "NULL");
        public static DbElement GetElement(int[] pair) => Scenario.Read("element.resolve." + pair[1], Scenario.Elements.TryGetValue(pair[1], out var target) ? target : Null);
        public DbElement Owner => Scenario.Read(Key + ".owner", Id == 1 || Id == 3 ? Scenario.Parent : Id == 40 ? Scenario.Point : Id == 50 ? Scenario.Geometry : Id == 4 || Id == 5 ? Scenario.Foreign : Null);
        public bool IsCreatable(DbElementType type) => Scenario.Read(Key + ".creatable", true);
        public bool IsNull => Scenario.Read(Key + ".null", Id == 0);
        public bool IsValid => Scenario.Read(Key + ".valid", true);
        public bool IsDeleted => Scenario.Read(Key + ".deleted", false);
        public int[] Ref => Scenario.Read(Key + ".ref", new[] { Id == 0 ? 0 : Id == 9 ? 1 : 2, Id });
        public DbType DbType => Scenario.Read(Key + ".db_type", Id == 9 ? DbType.Design : DbType.Catalogue);
        public DbElementType GetElementType() => Scenario.Read(Key + ".base_type", Scenario.Types[Kind]);
        public DbElementType GetActualType() => Scenario.Read(Key + ".actual_type", Scenario.Types[Kind]);
        public string GetAsString(DbAttribute attribute)
        {
            if (attribute.Key != "NAME") Scenario.Forbidden("formatted arbitrary attribute read");
            return Scenario.Read(Key + ".name", DisplayName ?? (Id == 1 ? "/SOURCE" : Id == 2 ? "/PARENT" : Id == 3 ? "/UNNAMED" : "/ELEMENT" + Id));
        }
        public DbElement Member(int ordinal)
        {
            var members = Id == 2 ? Scenario.Members : Id == 4 ? Scenario.PointMembers : Id == 5 ? Scenario.GeometryMembers : null;
            DbElement value = members != null && ordinal <= members.Count ? members[ordinal - 1] : Null;
            return Scenario.Read(Key + ".member." + ordinal, value);
        }
        public DbElement CreateLast(DbElementType type)
        {
            if (Id != 2 || type.Key != "SCOM") Scenario.Forbidden("create outside explicit Catalogue CATE/SCOM");
            Scenario.Creates++; var value = new DbElement(3, "SCOM"); Scenario.Created = value;
            Scenario.Elements[3] = value; Scenario.Members.Add(value);
            Scenario.OnCreate?.Invoke();
            return Scenario.Read(Key + ".create_last", value);
        }
        public void SetAttribute(DbAttribute attribute, string value)
        {
            if (Id != 3 || attribute.Key != "NAME") Scenario.Forbidden("string setter outside verified fresh SCOM NAME");
            Scenario.Writes++; Scenario.SetterNames.Add(attribute.Key); DisplayName = value;
            Scenario.OnSetter?.Invoke(attribute.Key);
            Scenario.Read(Key + ".set.NAME", true);
        }
        public void SetAttribute(DbAttribute attribute, DbElement value)
        {
            if (Id != 3 || (attribute.Key != "PTREF" && attribute.Key != "GMREF")) Scenario.Forbidden("reference setter outside verified fresh SCOM fixed refs");
            Scenario.Writes++; Scenario.SetterNames.Add(attribute.Key); Scenario.ReferenceValues[attribute.Key] = value;
            Scenario.OnSetter?.Invoke(attribute.Key);
            Scenario.Read(Key + ".set." + attribute.Key, true);
        }
        public bool IsAttributeValid(DbAttribute attribute) => Scenario.Read(Key + ".supports." + attribute.Key, true);
        public double[] GetDoubleArray(DbAttribute attribute)
        {
            if (attribute.Key != "PARAM") Scenario.Forbidden("unexpected double array");
            return Scenario.Read(Key + ".double_array." + attribute.Key, Scenario.ParameterValues);
        }
        public bool IsAttributeSetable(DbAttribute attribute) => Scenario.Read(Key + ".setable." + attribute.Key, true);
        public void SetAttribute(DbAttribute attribute, double[] values)
        {
            if (Id != 3 || attribute.Key != "PARAM") Scenario.Forbidden("parameter setter outside verified fresh SCOM PARAM");
            Scenario.Writes++; Scenario.SetterNames.Add(attribute.Key); Scenario.ParameterValues = (double[])values.Clone();
            Scenario.OnSetter?.Invoke(attribute.Key);
            Scenario.Read(Key + ".set.PARAM", true);
        }
        public DbElement GetElement(DbAttribute attribute) => Scenario.Read(Key + ".scalar." + attribute.Key, Scenario.ReferenceValues.TryGetValue(attribute.Key, out var target) ? target : Null);
        public DbElement[] GetElementArray(DbAttribute attribute) => Scenario.Read(Key + ".array." + attribute.Key, new DbElement[0]);
        public static bool operator ==(DbElement left, DbElement right) { Scenario.Forbidden("element SDK =="); return false; }
        public static bool operator !=(DbElement left, DbElement right) { Scenario.Forbidden("element SDK !="); return false; }
        public override bool Equals(object value) => ReferenceEquals(this, value);
        public override int GetHashCode() { Scenario.Forbidden("element identity hash"); return Id; }
        // No broad traversal, claim, delete, savework, expression, formatted
        // PARAM or geometry APIs are exposed to the production implementation.
    }
}

internal static class ParagonComponentCreateChecks
{
    private const string Session = "native-create-session";
    private static readonly string Build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
    private static double[] Desired = new[] { 0.0, 80.0, 125.5 };
    private static int assertions, receipts;
    private static string Id(int value) => "00000000-0000-0000-0000-" + value.ToString("x12");
    private static JObject Json(object value) => JObject.FromObject(value);
    private static JObject Progress(ParagonComponentCreateReply reply) => Json(reply.Result["progress"]);
    private static bool True(JObject value, string name) => value.Value<bool?>(name) == true;
    private static void Check(bool value, string message) { assertions++; if (!value) throw new Exception(message); }
    private static E3d21Context Reset()
    { Scenario.Reset(); Desired = new[] { 0.0, 80.0, 125.5 }; return new E3d21Context(); }
    private static ParagonComponentCreateReply Prepare(E3d21Context context, bool emit = true)
    {
        var reply = context.CreateParagonComponent("=2/2", "=2/4", "=2/5", "/NEW", Desired,
            true, null, null, null, null, Session);
        if (emit) Emit(reply, true, null, null);
        return reply;
    }
    private static ParagonComponentCreateReply Execute(E3d21Context context, string revision, int id = 1, bool emit = true)
    {
        var reply = context.CreateParagonComponent("=2/2", "=2/4", "=2/5", "/NEW", Desired,
            false, Id(id), Session, Build, revision, Session);
        if (emit) Emit(reply, false, Id(id), revision);
        return reply;
    }
    private static void Emit(ParagonComponentCreateReply reply, bool dryRun, string id, string revision)
    {
        var progress = Progress(reply);
        Check(Scenario.ForbiddenCalls == 0 && Scenario.Runs == 0 && !Scenario.Reads.Any(key => key.StartsWith("command.")),
            "direct creation never invokes PML, SDK equality/hash, or unrelated mutations");
        Check(progress.Count == 21 && !True(progress, "retry_safe") && !(bool)reply.Result["request_replayed"], "exact progress/no replay");
        Check(!Scenario.Reads.Any(key => key == "element.40.name" || key == "element.50.name"), "set member NAME is never read");
        var payload = new JObject { ["parent_refno"] = "=2/2", ["point_refno"] = "=2/4", ["geometry_refno"] = "=2/5",
            ["name"] = "/NEW", ["parameters"] = new JArray(Desired),
            ["parameter_bits"] = new JArray(Desired.Select(ParagonComponentParametersRequest.Bits)), ["dry_run"] = dryRun };
        if (!dryRun) { payload["request_id"] = id; payload["expected_session_id"] = Session; payload["expected_bridge_build"] = Build; payload["expected_revision"] = revision; }
        receipts++;
        Console.WriteLine("Component create receipt: " + Newtonsoft.Json.JsonConvert.SerializeObject(new
        {
            payload, http_status = reply.HttpStatus,
            reply = new { ok = reply.Ok, version = "E3D21", session_id = Session, bridge_build = Build,
                result = reply.Result, error = reply.Error, error_code = reply.NativeFaultLatched ? "native_database_fault" : null,
                native_fault_latched = reply.NativeFaultLatched, retry_safe = false }
        }));
    }
    private static string Ready(E3d21Context context)
    {
        var reply = Prepare(context, false);
        Check(reply.Ok && Equals(reply.Result["status"], "prepared"), "preflight must prepare: " + Newtonsoft.Json.JsonConvert.SerializeObject(reply));
        Check(Scenario.Creates == 0 && Scenario.Writes == 0 && !context.GateOwned, "preflight never mutates or retains its gate");
        return (string)reply.Result["scoped_revision"];
    }
    private static int Normal()
    {
        var context = Reset(); var prepared = Prepare(context);
        Check(prepared.Ok && Equals(prepared.Result["status"], "prepared") && prepared.Result["after"] == null, "prepare is scoped evidence only");
        Check(Json(prepared.Result["before"]).Count == 9 && Progress(prepared)["known_new_refno"].Type == JTokenType.Null, "prepare has no invented created identity");
        Scenario.ClearReads(); var reply = Execute(context, (string)prepared.Result["scoped_revision"]);
        Check(reply.Ok && Equals(reply.Result["status"], "created") && Scenario.Creates == 1 && Scenario.Writes == 4 && !context.GateOwned,
            "one CreateLast plus the four fixed SDK setters succeeds");
        Check(Scenario.SetterNames.SequenceEqual(new[] { "NAME", "PTREF", "GMREF", "PARAM" }), "setters are fixed and ordered");
        var p = Progress(reply);
        Check((string)p["known_new_refno"] == "=2/3" && True(p, "known_new_refno_verified") && True(p, "readback_complete")
            && True(p, "parent_append") && True(p, "context_unchanged") && True(p, "parameters_equal_request")
            && True(p, "references_equal_request") && True(p, "sets_unchanged_scoped"), "independent required readback facts all agree");
        Check(Json(reply.Result["after"]).Count == 12 && !True(p, "outcome_uncertain") && Scenario.Ce.Id == 9,
            "the receipt has exact after shape and no CE write");
        int calls = Scenario.Reads.Count;
        var duplicate = Execute(context, (string)prepared.Result["scoped_revision"]);
        Check(duplicate.HttpStatus == 409 && Equals(duplicate.Result["status"], "duplicate") && Scenario.Reads.Count == calls && Scenario.Creates == 1,
            "a duplicate ID cannot repeat the creation or setters");
        var status = Json(context.GetParagonComponentCreateStatus(Id(1), Session, Session));
        Check((string)status["operation"] == "paragon_component_create" && (string)status["status"] == "completed"
            && (bool)status["critical_summary"]["creation_returned"] && status["critical_summary"].Count() == 17 && Scenario.Reads.Count == calls,
            "the creation journal/status has exact managed facts and zero native probes");
        return calls;
    }
    private static void SetBoundaries()
    {
        foreach (string type in new[] { "PTSE", "GMSE" })
        foreach (string failure in new[] { "empty", "owner", "duplicate", "design", "actual", "limit" })
        {
            var context = Reset(); bool point = type == "PTSE";
            var rows = point ? Scenario.PointMembers : Scenario.GeometryMembers;
            int id = point ? 40 : 50, set = point ? 4 : 5;
            if (failure == "empty") rows.Clear();
            if (failure == "owner") Scenario.Overrides["element." + id + ".owner"] = Scenario.Foreign;
            if (failure == "duplicate") rows.Add(rows[0]);
            if (failure == "design") Scenario.Overrides["element." + id + ".db_type"] = DbType.Design;
            if (failure == "actual") Scenario.Overrides["element." + set + ".actual_type"] = Scenario.Types[":CUSTOM_" + type];
            if (failure == "limit")
            {
                rows.Clear();
                for (int i = 0; i < 65; i++)
                {
                    var item = new DbElement(400 + i, point ? "PTAX" : "SCYL"); rows.Add(item);
                    Scenario.Overrides["element." + (400 + i) + ".owner"] = point ? Scenario.Point : Scenario.Geometry;
                }
            }
            var reply = Prepare(context);
            Check(!Equals(reply.Result["status"], "prepared") && Scenario.Creates == 0 && Scenario.Writes == 0, "required set evidence blocks " + type + "/" + failure);
            if (failure == "limit") Check(!Scenario.Reads.Any(key => key.StartsWith("element.464.") && key != "element.464.null"), "the 65th set member identity/metadata is never inspected beyond the end sentinel check");
        }
        foreach (bool point in new[] { false, true })
        {
            var context = Reset(); var rows = point ? Scenario.PointMembers : Scenario.GeometryMembers; rows.Clear();
            for (int i = 0; i < 64; i++)
            {
                var item = new DbElement(400 + i, point ? "PTAX" : "SCYL"); rows.Add(item);
                Scenario.Overrides["element." + (400 + i) + ".owner"] = point ? Scenario.Point : Scenario.Geometry;
            }
            string revision = Ready(context); var reply = Execute(context, revision);
            Check(reply.Ok && Json(reply.Result["before"])[point ? "point_set" : "geometry_set"]["members"].Count() == 64,
                "64-member complete ordered sets are supported");
        }
        {
            var context = Reset();
            Scenario.Overrides["element.4.name"] = new InvalidOperationException("point name optional");
            Scenario.Overrides["element.5.name"] = new InvalidOperationException("geometry name optional");
            Scenario.Overrides["type.PTSE.name"] = new InvalidOperationException("optional display name");
            Scenario.Overrides["type.GMSE.name"] = new InvalidOperationException("optional display name");
            var before = Prepare(context); var reply = Execute(context, (string)before.Result["scoped_revision"]);
            Check(reply.Ok && !True(Json(reply.Result["after"])["new_template"] as JObject, "complete"),
                "optional set NAME/type display metadata does not erase exact typed reference/member slices");
        }
        {
            var context = Reset();
            Scenario.Overrides["element.4.member.2"] = null; Scenario.Overrides["element.5.member.2"] = null;
            Scenario.OnCreate = () => Scenario.Overrides["element.2.member.3"] = null;
            string revision = Ready(context); var reply = Execute(context, revision);
            Check(reply.Ok, "Member(int) CLR-null sentinels terminate both set and fresh parent member lists as documented");
        }
    }
    private static void ParentAndRequestBoundaries()
    {
        foreach (string failure in new[] { "parent_actual", "parent_design", "module", "creatable", "name_parent", "name_mdb", "attribute", "type_attribute", "context" })
        {
            var context = Reset();
            if (failure == "parent_actual") Scenario.Overrides["element.2.actual_type"] = Scenario.Types[":CUSTOM_SCOM"];
            if (failure == "parent_design") Scenario.Overrides["element.2.db_type"] = DbType.Design;
            if (failure == "module") Scenario.Overrides["module.number"] = 32;
            if (failure == "creatable") Scenario.Overrides["element.2.creatable"] = false;
            if (failure == "name_parent") Scenario.Overrides["element.1.name"] = "/NEW";
            if (failure == "name_mdb") Scenario.Named = Scenario.Root;
            if (failure == "attribute") Scenario.Overrides["attribute.PTREF.array"] = true;
            if (failure == "type_attribute") Scenario.Overrides["type.SCOM.supports.GMREF"] = false;
            if (failure == "context") Scenario.Overrides["ce"] = null;
            var reply = Prepare(context); Check(!Equals(reply.Result["status"], "prepared") && Scenario.Creates == 0, "unsafe parent/request evidence blocks " + failure);
        }
        {
            var context = Reset(); Scenario.Members.Clear();
            for (int i = 0; i < 100; i++) Scenario.Members.Add(new DbElement(100 + i, "SCOM"));
            string revision = Ready(context); var reply = Execute(context, revision);
            Check(reply.Ok && Scenario.Members.Count == 101, "100 existing direct CATE members plus the one fresh append are bounded independently");
        }
        {
            var context = Reset(); Scenario.Members.Clear();
            for (int i = 0; i < 101; i++) Scenario.Members.Add(new DbElement(100 + i, "SCOM"));
            var reply = Prepare(context); Check(!Equals(reply.Result["status"], "prepared") && Scenario.Creates == 0, "101 existing members blocks creation");
        }
        foreach (double[] values in new[] { new[] { BitConverter.Int64BitsToDouble(long.MinValue), -double.Epsilon, double.MaxValue }, Enumerable.Repeat(1.5, 128).ToArray() })
        {
            var context = Reset(); Desired = values; string revision = Ready(context); var reply = Execute(context, revision);
            Check(reply.Ok && True(Progress(reply), "parameters_equal_request"), "finite vectors of 1..128 retain exact binary64 bits");
            if (values.Length == 3) Check((string)Json(reply.Result["after"])["parameter_bits"][0] == "8000000000000000", "negative zero survives native getter and transport bits");
        }
        {
            var context = Reset(); Scenario.Overrides["attribute.NAME.type"] = DbAttributeType.WORD;
            Scenario.Overrides["attribute.resolve.NAME"] = new InvalidOperationException("NAME registry lookup is unnecessary");
            string revision = Ready(context); var reply = Execute(context, revision);
            Check(reply.Ok && !Scenario.Reads.Contains("attribute.NAME.type") && !Scenario.Reads.Contains("attribute.resolve.NAME"),
                "NAME uses its fixed SDK instance/string setter without an unverified global registry shape assumption");
        }
        foreach (double[] invalid in new[] { new double[0], new double[129], new[] { double.NaN }, new[] { double.PositiveInfinity } })
        {
            var context = Reset(); bool threw = false;
            try { context.CreateParagonComponent("=2/2", "=2/4", "=2/5", "/NEW", invalid, true, null, null, null, null, Session); }
            catch (ArgumentException) { threw = true; }
            Check(threw && Scenario.Reads.Count == 0 && Scenario.Creates == 0, "invalid numeric request is rejected before native dispatch");
        }
    }
    private static void RevisionAndMutationFailures()
    {
        foreach (string changed in new[] { "set_order", "point_owner", "actual", "instance", "typed_ce", "name", "parameter_bits" })
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads();
            if (changed == "set_order") { Scenario.PointMembers.Insert(0, new DbElement(41, "PTAX")); Scenario.Overrides["element.41.owner"] = Scenario.Point; }
            if (changed == "point_owner") Scenario.Overrides["element.40.owner"] = Scenario.Foreign;
            if (changed == "actual") Scenario.Overrides["element.4.actual_type"] = Scenario.Types[":CUSTOM_PTSE"];
            if (changed == "instance") Scenario.Project = new Project();
            if (changed == "typed_ce") Scenario.Overrides["element.9.db_type"] = DbType.Catalogue;
            if (changed == "name") Scenario.Overrides["element.1.name"] = "/OTHER";
            if (changed == "parameter_bits") Desired[0] = -1.0;
            var reply = Execute(context, revision);
            Check(reply.HttpStatus == 409 && !True(Progress(reply), "creation_attempted") && Scenario.Creates == 0, "execution revalidates its scoped revision: " + changed);
        }
        foreach (string failure in new[] { "existing", "wrong_owner", "actual", "ref", "prefix", "extra", "nonleaf", "null" })
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads();
            Scenario.OnCreate = delegate
            {
                if (failure == "existing") Scenario.Overrides["element.2.create_last"] = Scenario.Root;
                if (failure == "wrong_owner") Scenario.Overrides["element.3.owner"] = Scenario.Foreign;
                if (failure == "actual") Scenario.Overrides["element.3.actual_type"] = Scenario.Types[":CUSTOM_SCOM"];
                if (failure == "ref") Scenario.Overrides["element.3.ref"] = new[] { 0, 1 };
                if (failure == "prefix") Scenario.Members[0] = new DbElement(20, "SCOM");
                if (failure == "extra") Scenario.Members.Add(new DbElement(20, "SCOM"));
                if (failure == "nonleaf") Scenario.Overrides["element.3.member.1"] = Scenario.Root;
                if (failure == "null") Scenario.Overrides["element.2.create_last"] = null;
            };
            var reply = Execute(context, revision);
            Check(reply.HttpStatus == 500 && !reply.Ok && Scenario.Creates == 1 && Scenario.Writes == 0 && True(Progress(reply), "outcome_uncertain"),
                "an unverified CreateLast return never authorizes setters: " + failure);
        }
        foreach (string stage in new[] { "create_last", "NAME", "PTREF", "GMREF", "PARAM" })
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads();
            string key = stage == "create_last" ? "element.2.create_last" : "element.3.set." + stage;
            Scenario.Overrides[key] = new InvalidOperationException("ordinary stage error " + stage);
            var reply = Execute(context, revision); var p = Progress(reply);
            Check(reply.HttpStatus == 500 && !reply.NativeFaultLatched && True(p, "creation_attempted") && True(p, "outcome_uncertain"),
                "ordinary mutating errors produce uncertain failed receipts: " + stage);
            int expected = stage == "create_last" ? 0 : Array.IndexOf(new[] { "NAME", "PTREF", "GMREF", "PARAM" }, stage) + 1;
            Check(Scenario.Writes == expected && Scenario.SetterNames.Count == expected, "no setter after a failed stage is entered");
            if (stage != "create_last") Check((string)p["known_new_refno"] == "=2/3" && True(p, "known_new_refno_verified"), "the original verified REF survives ordinary setter failure");
        }
        foreach (string key in new[] { "NAME", "PTREF", "GMREF", "PARAM" })
        {
            var context = Reset(); string revision = Ready(context); Scenario.OnCreate = () => Scenario.Overrides["element.3.setable." + key] = false;
            var reply = Execute(context, revision);
            Check(reply.HttpStatus == 500 && !Scenario.SetterNames.Contains(key), "each fixed setter has its own native setable gate");
        }
        foreach (string field in new[] { "project", "mdb", "module", "typed_ce", "ce" })
        {
            var context = Reset(); string revision = Ready(context);
            Scenario.OnCreate = delegate
            {
                if (field == "project") Scenario.Project = new Project();
                if (field == "mdb") Scenario.Mdb = new MDB();
                if (field == "module") Scenario.Overrides["module.number"] = 32;
                if (field == "typed_ce") Scenario.Overrides["element.9.db_type"] = DbType.Catalogue;
                if (field == "ce") Scenario.Ce = Scenario.Created;
            };
            var reply = Execute(context, revision); var p = Progress(reply);
            Check(reply.HttpStatus == 500 && Scenario.Creates == 1 && Scenario.Writes == 0
                && (string)p["known_new_refno"] == "=2/3" && True(p, "known_new_refno_verified") && True(p, "outcome_uncertain"),
                "a synchronous CreateLast context/module change preserves its fresh REF and enters no setter: " + field);
        }
    }
    private static void ReadbackAndOptionalEvidence()
    {
        foreach (string failure in new[] { "numbers", "point", "geometry", "array", "name", "owner", "leaf", "set", "project", "mdb", "typed_ce" })
        {
            var context = Reset(); string revision = Ready(context);
            Scenario.OnSetter = attribute =>
            {
                if (attribute != "PARAM") return;
                if (failure == "numbers") Scenario.ParameterValues[0] = 9.0;
                if (failure == "point") Scenario.ReferenceValues["PTREF"] = Scenario.Geometry;
                if (failure == "geometry") Scenario.ReferenceValues["GMREF"] = Scenario.Point;
                if (failure == "array") Scenario.Overrides["attribute.PTREF.array"] = true;
                if (failure == "name") Scenario.Created.DisplayName = "/CHANGED";
                if (failure == "owner") Scenario.Overrides["element.3.owner"] = Scenario.Foreign;
                if (failure == "leaf") Scenario.Overrides["element.3.member.1"] = Scenario.Root;
                if (failure == "set") Scenario.Overrides["element.40.owner"] = Scenario.Foreign;
                if (failure == "project") Scenario.Project = new Project();
                if (failure == "mdb") Scenario.Mdb = new MDB();
                if (failure == "typed_ce") Scenario.Overrides["element.9.db_type"] = DbType.Catalogue;
            };
            var reply = Execute(context, revision);
            Check(reply.HttpStatus == 500 && True(Progress(reply), "parameters_returned") && True(Progress(reply), "outcome_uncertain")
                && (string)Progress(reply)["known_new_refno"] == "=2/3", "independent readback prevents unobserved success: " + failure);
        }
        foreach (string optional in new[] { "attribute.PARAM.units", "attribute.PTREF.units", "element.3.scalar.DTREF", "element.5.name", "type.GMSE.name" })
        {
            var context = Reset(); string revision = Ready(context);
            Scenario.OnSetter = key => { if (key == "PARAM") Scenario.Overrides[optional] = new InvalidOperationException("optional read failed"); };
            var reply = Execute(context, revision);
            Check(reply.Ok && Equals(reply.Result["status"], "created") && True(Progress(reply), "readback_complete"), "optional ordinary metadata does not erase required slices: " + optional);
        }
    }
    private static void DispatcherAndArrayOwnership()
    {
        var context = Reset(); string revision = Ready(context); Scenario.ClearReads(); Action queued = null;
        Scenario.Dispatcher = action => queued = action;
        var cancelled = Execute(context, revision);
        Check(cancelled.HttpStatus == 500 && Scenario.Creates == 0 && !context.GateOwned, "queued but unstarted callbacks are cancelled without mutation");
        queued(); Check(Scenario.Reads.Count == 0 && Scenario.Creates == 0, "cancelled late callback cannot touch the SDK");
        context = Reset(); revision = Ready(context); Scenario.ClearReads();
        var entered = new ManualResetEventSlim(false); var release = new ManualResetEventSlim(false); Task callback = null;
        Scenario.OnRead = key => { if (key == "element.3.set.PARAM") { entered.Set(); release.Wait(15000); } };
        Scenario.Dispatcher = action => { callback = Task.Run(action); if (!entered.Wait(15000)) throw new Exception("callback did not begin"); };
        Task<ParagonComponentCreateReply> run = Task.Run(() => Execute(context, revision, 1, false));
        Check(entered.Wait(15000) && !run.Wait(100) && context.GateOwned, "early dispatcher return cannot release the active callback gate");
        int calls = Scenario.Reads.Count;
        var status = Json(context.GetParagonComponentCreateStatus(Id(1), Session, Session));
        var duplicate = Execute(context, revision, 1, false);
        Check((string)status["status"] == "running" && duplicate.HttpStatus == 409 && Scenario.Reads.Count == calls && !run.IsCompleted,
            "managed status and duplicate admission remain available while a native setter is blocked");
        release.Set(); Check(run.Wait(15000), "the actual callback completes"); callback.Wait();
        Check(run.Result.Ok && !context.GateOwned && Scenario.Creates == 1, "only actual callback completion releases command ownership");
        context = Reset(); revision = Ready(context);
        Scenario.Dispatcher = action => { Desired[0] = 999.0; action(); };
        var immutable = context.CreateParagonComponent("=2/2", "=2/4", "=2/5", "/NEW", Desired,
            false, Id(1), Session, Build, revision, Session);
        Check(immutable.Ok && Scenario.ParameterValues[0] == 0.0, "the native callback owns a validated numeric array copy");
    }
    private static void FaultMatrix(int reads)
    {
        int cases = 0;
        for (int mode = 0; mode < 2; mode++)
        for (int index = 1; index <= reads; index++)
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads();
            Scenario.OnRead = key =>
            {
                if (Scenario.Reads.Count != index) return;
                context.Fault(); if (mode == 1) Scenario.Overrides[key] = new InvalidOperationException("ordinary error accompanied by nativefault");
            };
            var reply = Execute(context, revision, 1, false); var p = Progress(reply);
            Check(reply.NativeFaultLatched && reply.HttpStatus == 500 && Scenario.Reads.Count == index, "every fault stops all subsequent SDK calls at its exact position");
            Check(!context.GateOwned && Scenario.UiDepth == 0 && Scenario.ForbiddenCalls == 0, "faulted callbacks settle before command release");
            if (Scenario.Reads.Last() == "element.3.set.PARAM")
                Check(True(p, "parameters_attempted") && True(p, "parameters_returned") == (mode == 0)
                    && (string)p["known_new_refno"] == "=2/3", "returns before guard and the previously verified REF survive faults");
            int creates = Scenario.Creates, setters = Scenario.Writes;
            var duplicate = Execute(context, revision, 1, false); var next = Execute(context, revision, 2, false);
            Check(duplicate.HttpStatus == 409 && next.NativeFaultLatched && Scenario.Reads.Count == index
                && Scenario.Creates == creates && Scenario.Writes == setters, "neither a duplicate nor a fresh request ID bypasses a latched fault");
            var status = Json(context.GetParagonComponentCreateStatus(Id(1), Session, Session));
            Check((string)status["status"] == "completed" && (bool)status["critical_summary"]["native_fault_latched"] && Scenario.Reads.Count == index,
                "the faulted journal is purely managed and preserves the final latch"); cases++;
        }
        Console.WriteLine($"Component create native fault matrix: {reads} SDK read/write positions / {cases} return-or-throw cases");
    }
    private static int Main()
    {
        try { return MainCore(); }
        catch (Exception error)
        {
            Console.WriteLine("Component create harness failure: " + error.GetType().FullName + ": " + error.Message);
            return 1;
        }
    }
    private static int MainCore()
    {
        int reads = Normal(); SetBoundaries(); ParentAndRequestBoundaries(); RevisionAndMutationFailures();
        ReadbackAndOptionalEvidence(); DispatcherAndArrayOwnership(); FaultMatrix(reads);
        Console.WriteLine($"Component create production checks passed: {assertions}; {receipts} receipts; no SDK assemblies loaded.");
        return 0;
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
    internal static DbElement Root, Parent, Ce, Reference, Named, Point, Geometry, Foreign, Created;
    internal static List<DbElement> PointMembers = new List<DbElement>(), GeometryMembers = new List<DbElement>();
    internal static Dictionary<string,DbElement> ReferenceValues = new Dictionary<string,DbElement>();
    internal static readonly List<string> SetterNames = new List<string>();
    internal static Action OnCreate;
    internal static Action<string> OnSetter;
    internal static int Creates;
    internal static readonly Dictionary<int, DbElement> Elements = new Dictionary<int, DbElement>();
    internal static readonly List<DbElement> Members = new List<DbElement>();
    internal static double[] ParameterValues;
    internal static int Writes;
    internal static Action<double[]> OnSet;
    internal static Action OnRun;
    internal static Action<Action> Dispatcher;
    internal static bool RunSuccess;
    internal static int Runs;
    internal static string MacroSource, MacroPath;
    internal static void ClearReads() { Reads.Clear(); Counts.Clear(); }
    internal static void ExecuteMutation()
    {
        var created = new DbElement(3, "SCOM"); Elements[3] = created; Members.Add(created); Ce = created;
    }
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
        foreach (string type in new[] { "SCOM", "CATE", "PTSE", "GMSE", "PTAX", "SCYL", "EQUI", ":CUSTOM_SCOM", ":CUSTOM_PTSE", ":CUSTOM_GMSE", "NULL" }) Types[type] = new DbElementType(type);
        foreach (string name in new[] { "NAME", "PARAM", "PTREF", "GMREF", "DTREF", "BLRFA", "NGMREF", "PSTREF", "GSTREF" }) Attributes[name] = new DbAttribute(name);
        Project = new Project(); Mdb = new MDB(); Root = new DbElement(1, "SCOM");
        Ce = new DbElement(9, "EQUI"); Reference = new DbElement(4, "GMSE"); Parent = new DbElement(2, "CATE");
        Point = new DbElement(4, "PTSE"); Geometry = new DbElement(5, "GMSE"); Foreign = new DbElement(6, "CATE");
        Elements.Clear(); Elements[1] = Root; Elements[2] = Parent; Elements[4] = Point; Elements[5] = Geometry; Elements[6] = Foreign;
        PointMembers.Clear(); GeometryMembers.Clear();
        var pt = new DbElement(40, "PTAX"); var gm = new DbElement(50, "SCYL");
        Elements[40] = pt; Elements[50] = gm; PointMembers.Add(pt); GeometryMembers.Add(gm);
        Creates = 0; Created = null; SetterNames.Clear(); OnCreate = null; OnSetter = null; ReferenceValues.Clear();
        Members.Clear(); Members.Add(Root); Named = null; Runs = 0; RunSuccess = true;
        OnRun = ExecuteMutation; Dispatcher = action => action(); MacroSource = MacroPath = null;
        ParameterValues = new double[0]; Writes = 0; OnSet = null;
        OnRead = null; Busy = BusyOnUi = false; UiDepth = Dispatches = ForbiddenCalls = 0;
    }
}

namespace Aveva.Core.Utilities.ModuleInfo
{
    public sealed class ModuleManager
    {
        public ModuleManager() { Scenario.Read("module.manager", true); }
        public Module CurrentModule => Scenario.Read("module.current", new Module());
    }
    public sealed class Module
    {
        public string Name => Scenario.Read("module.name", "PARAGON");
        public int Number => Scenario.Read("module.number", 81);
    }
}

namespace Aveva.Core.Utilities.Messaging
{
    public sealed class PdmsMessage
    {
        public string MessageText() => Scenario.Read("command.error.text", "native copy error");
        public int ModuleNumber => Scenario.Read("command.error.module", 81);
        public int MessageNumber => Scenario.Read("command.error.number", 1234);
        public static bool operator ==(PdmsMessage a, PdmsMessage b) { Scenario.Forbidden("message SDK =="); return false; }
        public static bool operator !=(PdmsMessage a, PdmsMessage b) { Scenario.Forbidden("message SDK !="); return false; }
        public override bool Equals(object value) => ReferenceEquals(this, value);
        public override int GetHashCode() => 1;
    }
}

namespace Aveva.Core.Utilities.CommandLine
{
    public sealed class Command
    {
        public static Command CreateCommand(string command)
        {
            Command result = Scenario.Read("command.factory", new Command());
            if (!command.StartsWith("$M \"", StringComparison.Ordinal) || !command.EndsWith("\"", StringComparison.Ordinal)) Scenario.Forbidden("non-macro backend");
            string path = command.Substring(4, command.Length - 5);
            Scenario.MacroPath = path;
            Scenario.MacroSource = File.ReadAllText(path);
            bool locked = false;
            try { using (File.Open(path, FileMode.Open, FileAccess.Write, FileShare.ReadWrite)) { } }
            catch (IOException) { locked = true; }
            if (!locked) Scenario.Forbidden("macro was not locked against writers before the native factory");
            return result;
        }
        public bool Run()
        {
            Scenario.Runs++;
            Scenario.OnRun?.Invoke();
            return Scenario.Read("command.run", Scenario.RunSuccess);
        }
        public string Result => Scenario.Read("command.result", "completed");
        public Aveva.Core.Utilities.Messaging.PdmsMessage Error => Scenario.Read("command.error", (Aveva.Core.Utilities.Messaging.PdmsMessage)null);
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
            invokeOnUi = action => Scenario.Dispatcher(delegate
            {
                Scenario.Dispatches++; Scenario.UiDepth++;
                try { action(); } finally { Scenario.UiDepth--; }
            });
        }
        private bool NativeDispatchBusy() => Scenario.Busy;
        internal void Fault() => nativeFaultState.Observe("Unhandled Database error 538 from ParagonCopyFake", "");
        internal bool GateOwned { get { lock (commandLock) return commandActive; } }
        private object CurrentElement() => null;
        // PRODUCTION_NATIVE_HELPERS
    }
}
