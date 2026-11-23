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
        internal static readonly DbElement Null = new DbElement(0, "NULL");
        public static DbElement GetElement(int[] pair) => Scenario.Read("element.resolve." + pair[1], Scenario.Elements.TryGetValue(pair[1], out var target) ? target : Null);
        public DbElement Owner => Scenario.Read(Key + ".owner", Id == 1 || Id == 3 ? Scenario.Parent : Null);
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
            return Scenario.Read(Key + ".name", Id == 1 ? "/SOURCE" : Id == 2 ? "/PARENT" : Id == 3 ? "/NEW" : "/ELEMENT" + Id);
        }
        public DbElement Member(int ordinal)
        {
            if (Id != 2 && ordinal != 1) Scenario.Forbidden("non-parent deep member read");
            DbElement value = Id == 2 && ordinal <= Scenario.Members.Count ? Scenario.Members[ordinal - 1] : Null;
            return Scenario.Read(Key + ".member." + ordinal, value);
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
        public override int GetHashCode() { Scenario.Forbidden("element identity hash"); return Id; }
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
    internal static DbElement Root, Parent, Ce, Reference, Named;
    internal static readonly Dictionary<int, DbElement> Elements = new Dictionary<int, DbElement>();
    internal static readonly List<DbElement> Members = new List<DbElement>();
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
        foreach (string type in new[] { "SCOM", "CATE", "GMSE", "EQUI", ":CUSTOM_SCOM", "NULL" }) Types[type] = new DbElementType(type);
        foreach (string name in new[] { "PARAM", "PTREF", "GMREF", "DTREF", "BLRFA", "NGMREF", "PSTREF", "GSTREF" }) Attributes[name] = new DbAttribute(name);
        Project = new Project(); Mdb = new MDB(); Root = new DbElement(1, "SCOM");
        Ce = new DbElement(9, "EQUI"); Reference = new DbElement(4, "GMSE"); Parent = new DbElement(2, "CATE");
        Elements.Clear(); Elements[1] = Root; Elements[2] = Parent; Elements[4] = Reference;
        Members.Clear(); Members.Add(Root); Named = null; Runs = 0; RunSuccess = true;
        OnRun = ExecuteMutation; Dispatcher = action => action(); MacroSource = MacroPath = null;
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

internal static class ParagonComponentCopyChecks
{
    private const string Session = "native-copy-session";
    private static readonly string Build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
    private static int assertions, receipts;
    private static void Check(bool value, string message) { assertions++; if (!value) throw new Exception(message); }
    private static void Throws<T>(Action operation) where T : Exception
    { try { operation(); } catch (T) { assertions++; return; } throw new Exception("Expected " + typeof(T).Name); }
    private static string RequestId(int number) => "00000000-0000-0000-0000-" + number.ToString("x12");
    private static JObject Json(object value) => JObject.FromObject(value);
    private static JObject Progress(ParagonComponentCopyReply reply) => Json(reply.Result["progress"]);
    private static ParagonComponentCopyReply Prepare(E3d21Context context, bool emit = true)
    {
        var reply = context.CopyParagonComponent("=2/1", "=2/2", "/NEW", "same_category_scom_copy_v1", true, null, null, null, null, Session);
        if (emit) Emit(reply, true, null, null);
        return reply;
    }
    private static ParagonComponentCopyReply Execute(E3d21Context context, string revision, int id = 1, bool emit = true)
    {
        var reply = context.CopyParagonComponent("=2/1", "=2/2", "/NEW", "same_category_scom_copy_v1", false, RequestId(id), Session, Build, revision, Session);
        if (emit) Emit(reply, false, RequestId(id), revision);
        return reply;
    }
    private static void Emit(ParagonComponentCopyReply reply, bool preview, string requestId, string revision)
    {
        Check(Scenario.ForbiddenCalls == 0, "no overloaded SDK equality, SDK hash, or unguarded read");
        Check(!Scenario.Reads.Any(value => value.Contains("SET") || value.Contains("SAVEWORK") || value.Contains("DELETE")), "no fallback, save, delete or restore");
        Check(Progress(reply).Count == 13, "exact progress fields");
        Check(!(bool)Progress(reply)["retry_safe"] && !(bool)Json(reply.Result)["request_replayed"], "never replay");
        receipts++;
        Console.WriteLine("Component copy receipt: " + JsonConvert.SerializeObject(new
        {
            http_status = reply.HttpStatus,
            payload = preview ? new JObject { ["source_refno"] = "=2/1", ["parent_refno"] = "=2/2", ["name"] = "/NEW", ["recipe"] = "same_category_scom_copy_v1", ["dry_run"] = true }
                : new JObject { ["source_refno"] = "=2/1", ["parent_refno"] = "=2/2", ["name"] = "/NEW", ["recipe"] = "same_category_scom_copy_v1", ["dry_run"] = false,
                    ["request_id"] = requestId, ["expected_session_id"] = Session, ["expected_bridge_build"] = Build, ["expected_revision"] = revision },
            reply = new { ok = reply.Ok, version = "E3D21", session_id = Session, bridge_build = Build, result = reply.Result,
                error = reply.Error, error_code = reply.NativeFaultLatched ? "native_database_fault" : null, native_fault_latched = reply.NativeFaultLatched, retry_safe = false }
        }));
    }
    private static E3d21Context Reset() { Scenario.Reset(); return new E3d21Context(); }
    private static string Ready(E3d21Context context)
    {
        var preview = Prepare(context, false);
        Check(preview.Ok && preview.HttpStatus == 200 && Equals(preview.Result["status"], "prepared"), "normal preview must prepare: " + JsonConvert.SerializeObject(preview));
        Check(Scenario.Runs == 0 && !context.GateOwned, "preview does not run or retain gate");
        return (string)preview.Result["scoped_revision"];
    }
    private static int Normal()
    {
        var context = Reset();
        var prepare = Prepare(context);
        Check(prepare.Ok && Equals(prepare.Result["status"], "prepared") && (string)prepare.Result["scoped_revision"] != null, "normal prepared receipt");
        var second = Prepare(context);
        Check(Equals(prepare.Result["scoped_revision"], second.Result["scoped_revision"]), "same instance and native facts have stable scoped revision");
        Scenario.ClearReads();
        var result = Execute(context, (string)prepare.Result["scoped_revision"]);
        Check(result.Ok && result.HttpStatus == 200 && Equals(result.Result["status"], "executed"), "normal copy failed: " + JsonConvert.SerializeObject(result));
        var progress = Progress(result);
        Check((bool)progress["creation_attempted"] && (bool)progress["native_run_attempted"] && (bool)progress["native_run_returned"] && (bool)progress["native_run_success"], "native stages are separate facts");
        Check((string)progress["known_new_refno"] == "=2/3" && (bool)progress["known_new_refno_verified"] && (bool)progress["readback_complete"], "new REF is verified through real fake readback");
        Check((bool)progress["source_unchanged_scoped"] && (bool)progress["copied_parameters_equal"] && (bool)progress["copied_references_equal"], "scoped comparisons complete");
        Check(Scenario.MacroSource == "=2/2\nNEW SCOM /NEW AFTER LAST MEMBER COPY =2/1\n", "only fixed two-line recipe can run");
        Check(Scenario.Runs == 1 && !context.GateOwned && Scenario.UiDepth == 0, "one native Run and gate cleanup");
        int count = Scenario.Reads.Count;
        var duplicate = Execute(context, (string)prepare.Result["scoped_revision"]);
        Check(duplicate.HttpStatus == 409 && Equals(duplicate.Result["status"], "duplicate") && Scenario.Reads.Count == count && Scenario.Runs == 1, "duplicate never reaches native");
        var state = Json(context.GetParagonComponentCopyStatus(RequestId(1), Session, Session));
        Check((string)state["status"] == "completed" && (string)state["critical_summary"]["known_new_refno"] == "=2/3", "managed status preserves final facts");
        Check(Scenario.Reads.Count == count, "managed status zero SDK");
        return count;
    }
    private static void Preflight()
    {
        foreach (string key in new[] { "module.name", "module.number", "element.1.actual_type", "element.1.member.1", "element.1.owner", "element.2.creatable", "name.lookup" })
        {
            var context = Reset();
            if (key == "module.name") Scenario.Overrides[key] = "DESIGN";
            if (key == "module.number") Scenario.Overrides[key] = 1;
            if (key == "element.1.actual_type") Scenario.Overrides[key] = Scenario.Types[":CUSTOM_SCOM"];
            if (key == "element.1.member.1") Scenario.Overrides[key] = Scenario.Reference;
            if (key == "element.1.owner") Scenario.Overrides[key] = Scenario.Reference;
            if (key == "element.2.creatable") Scenario.Overrides[key] = false;
            if (key == "name.lookup") Scenario.Overrides[key] = Scenario.Root;
            var reply = Prepare(context);
            Check(!Equals(reply.Result["status"], "prepared") && Scenario.Runs == 0 && !context.GateOwned, "wrong required observation never prepares or runs: " + key);
        }
        foreach (string kind in new[] { "empty_refs", "unknown_param", "nonfinite_param", "param_cutoff", "param_inspection", "name_error", "name_collision", "parent_cutoff" })
        {
            var context = Reset();
            if (kind == "empty_refs") foreach (string attr in new[] { "PTREF", "GMREF", "DTREF", "BLRFA", "NGMREF", "PSTREF", "GSTREF" }) Scenario.Overrides["element.1.supports." + attr] = false;
            if (kind == "unknown_param") Scenario.Overrides["element.1.double_array.PARAM"] = new InvalidOperationException("value getter failed");
            if (kind == "nonfinite_param") Scenario.Overrides["element.1.double_array.PARAM"] = new[] { double.NaN };
            if (kind == "param_cutoff") Scenario.Overrides["element.1.double_array.PARAM"] = new double[129];
            if (kind == "param_inspection") Scenario.Overrides["element.1.double_array.PARAM"] = new double[4097];
            if (kind == "name_error") Scenario.Overrides["element.1.name"] = new InvalidOperationException("name failed");
            if (kind == "name_collision") Scenario.Overrides["element.1.name"] = "/NEW";
            if (kind == "parent_cutoff") for (int no = 10; no < 111; no++) Scenario.Members.Add(new DbElement(no, "SCOM"));
            var reply = Prepare(context);
            Check(!Equals(reply.Result["status"], "prepared") && Scenario.Runs == 0, "unknown or over-budget required slice never prepares: " + kind);
        }
        foreach (string variation in new[] { "unit_kind", "optional_unsupported", "reference_name", "empty_numeric" })
        {
            var context = Reset();
            if (variation == "unit_kind") Scenario.Overrides["attribute.PARAM.units"] = new InvalidOperationException("unit kind failed");
            if (variation == "optional_unsupported") { Scenario.Overrides["element.1.supports.GSTREF"] = false; Scenario.Overrides["element.3.supports.GSTREF"] = false; }
            if (variation == "reference_name") Scenario.Overrides["element.4.name"] = new InvalidOperationException("reference name failed");
            if (variation == "empty_numeric") { Scenario.Overrides["element.1.double_array.PARAM"] = new double[0]; Scenario.Overrides["element.3.double_array.PARAM"] = new double[0]; }
            string revision = Ready(context);
            var reply = Execute(context, revision);
            Check(reply.Ok && Equals(reply.Result["status"], "executed"), "independent required slice should execute: " + variation);
        }
    }
    private static void Conflicts()
    {
        foreach (string change in new[] { "project", "mdb", "param", "name", "members", "revision", "build", "session" })
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads();
            if (change == "project") Scenario.Project = new Project();
            if (change == "mdb") Scenario.Mdb = new MDB();
            if (change == "param") Scenario.Overrides["element.1.double_array.PARAM"] = new[] { 2.0 };
            if (change == "name") Scenario.Overrides["element.1.name"] = "/RENAMED";
            if (change == "members") Scenario.Members.Add(new DbElement(10, "SCOM"));
            if (change == "revision") revision = new string('f', 64);
            ParagonComponentCopyReply reply;
            if (change == "build" || change == "session")
                reply = context.CopyParagonComponent("=2/1", "=2/2", "/NEW", "same_category_scom_copy_v1", false, RequestId(1), change == "session" ? "different" : Session, change == "build" ? Guid.NewGuid().ToString("D") : Build, revision, Session);
            else reply = Execute(context, revision);
            Check(reply.HttpStatus == 409 && !reply.Ok && Scenario.Runs == 0 && !(bool)Progress(reply)["creation_attempted"], "changed binding rejects before mutation: " + change);
            if (change == "build" || change == "session") Check(Scenario.Reads.Count == 0 && Scenario.Dispatches == 1, "session/build conflict has zero SDK or execute dispatch");
        }
    }
    private static void PartialReadbacks()
    {
        foreach (string variation in new[] { "run_false", "run_throw", "new_name", "new_actual", "new_owner", "new_leaf", "extra_append", "no_append", "source_param", "new_param", "new_reference", "after_context", "new_template_failure", "after_known_ref_fault" })
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads();
            Scenario.OnRun = delegate
            {
                Scenario.ExecuteMutation();
                if (variation == "run_false") Scenario.RunSuccess = false;
                if (variation == "run_throw") Scenario.Overrides["command.run"] = new InvalidOperationException("run threw after partial mutation");
                if (variation == "new_name") Scenario.Overrides["element.3.name"] = "/OTHER";
                if (variation == "new_actual") Scenario.Overrides["element.3.actual_type"] = Scenario.Types[":CUSTOM_SCOM"];
                if (variation == "new_owner") Scenario.Overrides["element.3.owner"] = Scenario.Reference;
                if (variation == "new_leaf") Scenario.Overrides["element.3.member.1"] = Scenario.Reference;
                if (variation == "extra_append") Scenario.Members.Add(new DbElement(10, "SCOM"));
                if (variation == "no_append") Scenario.Members.RemoveAt(Scenario.Members.Count - 1);
                if (variation == "source_param") Scenario.Overrides["element.1.double_array.PARAM"] = new[] { 999.0 };
                if (variation == "new_param") Scenario.Overrides["element.3.double_array.PARAM"] = new[] { 999.0 };
                if (variation == "new_reference") Scenario.Overrides["element.3.scalar.PTREF"] = Scenario.Root;
                if (variation == "after_context") Scenario.Project = new Project();
                if (variation == "new_template_failure") Scenario.Overrides["element.3.double_array.PARAM"] = new InvalidOperationException("new param failed");
            };
            int lastCalls = 0;
            if (variation == "after_known_ref_fault") Scenario.OnRead = key => { if (Scenario.Runs > 0 && key == "element.resolve.1") { lastCalls = Scenario.Reads.Count; context.Fault(); } };
            var reply = Execute(context, revision);
            Check(!reply.Ok && reply.HttpStatus == 500 && Equals(reply.Result["status"], "failed"), "partial copy must fail with preserved evidence: " + variation);
            Check((bool)Progress(reply)["creation_attempted"] && (bool)Progress(reply)["outcome_uncertain"] && Scenario.Runs == 1, "partial mutating stage is not declared unused or safe");
            if (new[] { "run_false", "run_throw", "source_param", "new_param", "new_reference", "new_template_failure", "after_known_ref_fault" }.Contains(variation))
                Check((string)Progress(reply)["known_new_refno"] == "=2/3" && (bool)Progress(reply)["known_new_refno_verified"], "independently verified new REF survives later failures");
            if (variation == "after_known_ref_fault") Check(reply.NativeFaultLatched && Scenario.Reads.Count == lastCalls, "fault after known REF stops all later reads");
        }
    }
    private static void FaultMatrix(int calls)
    {
        int cases = 0;
        for (int mode = 0; mode < 2; mode++)
        for (int injection = 1; injection <= calls; injection++)
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads();
            Scenario.OnRead = key =>
            {
                if (Scenario.Reads.Count != injection) return;
                context.Fault();
                if (mode == 1) Scenario.Overrides[key] = new InvalidOperationException("ordinary exception accompanied by native fault");
            };
            var reply = Execute(context, revision, 1, false);
            Check(reply.NativeFaultLatched && reply.HttpStatus == 500 && !reply.Ok, "every injected fault is dedicated failure");
            Check(Scenario.Reads.Count == injection, "no SDK read follows a native fault");
            if (Scenario.Reads.Last() == "command.run")
            {
                Check((bool)Progress(reply)["native_run_attempted"] && (bool)Progress(reply)["creation_attempted"], "run flags are captured before its native invocation");
                Check((bool)Progress(reply)["native_run_returned"] == (mode == 0), "normal Run return is retained even when the NativeWrite finally guard throws a fault");
                Check(mode == 0 ? Progress(reply)["native_run_success"].Value<bool>() : Progress(reply)["native_run_success"].Type == JTokenType.Null, "Run bool is never guessed when the SDK threw");
            }
            Check(!context.GateOwned && Scenario.UiDepth == 0 && Scenario.ForbiddenCalls == 0, "fault settles owned callback and gate exactly once");
            int runs = Scenario.Runs;
            var duplicate = Execute(context, revision, 1, false);
            Check(duplicate.HttpStatus == 409 && Scenario.Reads.Count == injection && Scenario.Runs == runs, "duplicate after fault does not probe SDK");
            var next = Execute(context, revision, 2, false);
            Check(next.NativeFaultLatched && Scenario.Reads.Count == injection && Scenario.Runs == runs, "latched fault rejects new request before dispatch");
            var status = Json(context.GetParagonComponentCopyStatus(RequestId(1), Session, Session));
            Check((string)status["status"] == "completed" && (bool)status["critical_summary"]["native_fault_latched"] && Scenario.Reads.Count == injection, "fault critical facts remain queryable without SDK");
            cases++;
        }
        Console.WriteLine($"Component copy native fault matrix: {calls} read/write positions / {cases} return-or-throw cases");
    }

    private static void DiagnosticFaultsAndBudgets()
    {
        foreach (string key in new[] { "command.error", "command.error.text", "command.error.module", "command.error.number" })
        for (int mode = 0; mode < 2; mode++)
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads();
            Scenario.Overrides["command.error"] = new Aveva.Core.Utilities.Messaging.PdmsMessage();
            int faultPosition = 0;
            Scenario.OnRead = current =>
            {
                if (current != key) return;
                faultPosition = Scenario.Reads.Count; context.Fault();
                if (mode == 1) Scenario.Overrides[current] = new InvalidOperationException("ordinary error accompanied by native fault");
            };
            var reply = Execute(context, revision);
            Check(reply.NativeFaultLatched && Scenario.Reads.Count == faultPosition && faultPosition > 0, "native diagnostic fault must stop subsequent getters and all model readback");
            Check((bool)Progress(reply)["native_run_returned"] && (bool)Progress(reply)["native_run_success"], "diagnostic fault retains the actual successful native Run return");
            Check(Progress(reply)["known_new_refno"].Type == JTokenType.Null && reply.Result["after"] == null, "fault before readback cannot invent a new REF");
        }
        {
            var context = Reset();
            for (int no = 10; no < 109; no++) Scenario.Members.Add(new DbElement(no, "SCOM"));
            string revision = Ready(context); Scenario.ClearReads();
            var reply = Execute(context, revision);
            Check(reply.Ok && Json(reply.Result["before"])["members"].Count() == 100 && Json(reply.Result["after"])["members"].Count() == 101,
                "exact 100 pre-members allow one verified 101st append");
            Check(Scenario.Reads.Contains("element.2.member.102"), "post-list completeness requires the 102nd termination probe");
        }
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads();
            Scenario.OnRun = delegate { Scenario.ExecuteMutation(); Scenario.Overrides["element.1.double_array.PARAM"] = new[] { -0.0, 2.5 }; Scenario.Overrides["element.3.double_array.PARAM"] = new[] { +0.0, 2.5 }; };
            var reply = Execute(context, revision);
            Check(!reply.Ok && Progress(reply)["copied_parameters_equal"].Value<bool>() == false, "numeric comparison preserves DOUBLE bit patterns and order");
        }
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads();
            Scenario.OnRun = delegate { Scenario.ExecuteMutation(); Scenario.Overrides["attribute.PARAM.units"] = new InvalidOperationException("units became unavailable"); };
            var reply = Execute(context, revision);
            Check(reply.Ok && (bool)Progress(reply)["copied_parameters_equal"], "unit_kind-only post getter error does not erase complete numeric PARAM values");
        }
        foreach (string target in new[] { "project.name", "mdb.name" })
        {
            var context = Reset(); Scenario.Overrides[target] = "bad\ue000";
            var reply = Prepare(context);
            Check(Equals(reply.Result["status"], "blocked") && Scenario.Runs == 0, "private-use project/MDB text is unknown and blocks preparation");
        }
        {
            var context = Reset();
            Scenario.OnRead = key =>
            {
                if (key == "ce" && Scenario.Counts[key] == 2) Scenario.Overrides[key] = Scenario.Reference;
                if (key == "ce" && Scenario.Counts[key] == 4) Scenario.Overrides[key] = Scenario.Ce;
            };
            var reply = Prepare(context);
            Check(Equals(reply.Result["status"], "blocked") && Scenario.Runs == 0, "a self-consistent template CE cannot substitute for the independently captured outer CE");
        }
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads(); int postCeReads = 0;
            Scenario.OnRead = key =>
            {
                if (Scenario.Runs == 0 || key != "ce") return;
                postCeReads++;
                if (postCeReads == 2) Scenario.Overrides[key] = Scenario.Reference;
                if (postCeReads == 4) Scenario.Overrides[key] = Scenario.Ce;
            };
            var reply = Execute(context, revision);
            Check(!reply.Ok && (string)Progress(reply)["known_new_refno"] == "=2/3", "post-copy template CE drift blocks success while preserving previously verified new REF");
        }
    }

    private static void SetUnsetReferences()
    {
        foreach (int id in new[] { 1, 3 })
        {
            foreach (string attr in new[] { "PTREF", "GMREF", "DTREF", "NGMREF", "PSTREF", "GSTREF" })
                Scenario.Overrides["element." + id + ".scalar." + attr] = DbElement.Null;
            Scenario.Overrides["element." + id + ".array.BLRFA"] = new[] { DbElement.Null };
        }
    }

    private static void KnownUnsetReferences()
    {
        int baseline = 0;
        foreach (string variation in new[] { "scalar", "array", "all_unset", "one_supported_unset", "empty_array", "array_order" })
        {
            var context = Reset();
            if (variation == "all_unset" || variation == "one_supported_unset") SetUnsetReferences();
            if (variation == "one_supported_unset")
                foreach (int id in new[] { 1, 3 })
                foreach (string attr in new[] { "GMREF", "DTREF", "BLRFA", "NGMREF", "PSTREF", "GSTREF" }) Scenario.Overrides["element." + id + ".supports." + attr] = false;
            foreach (int id in new[] { 1, 3 })
            {
                if (variation == "scalar") Scenario.Overrides["element." + id + ".scalar.PTREF"] = DbElement.Null;
                if (variation == "array" || variation == "array_order") Scenario.Overrides["element." + id + ".array.BLRFA"] = new[] { DbElement.Null, Scenario.Reference };
                if (variation == "empty_array") Scenario.Overrides["element." + id + ".array.BLRFA"] = new DbElement[0];
            }
            var preview = Prepare(context); string revision = (string)preview.Result["scoped_revision"];
            Check(preview.Ok && Equals(preview.Result["status"], "prepared"), "explicitly observed unset values are real supported coverage: " + variation);
            Scenario.ClearReads();
            if (variation == "array_order") Scenario.OnRun = delegate
            { Scenario.ExecuteMutation(); Scenario.Overrides["element.3.array.BLRFA"] = new[] { Scenario.Reference, DbElement.Null }; };
            var reply = Execute(context, revision);
            if (variation == "array_order") Check(!reply.Ok && Progress(reply)["copied_references_equal"].Value<bool>() == false, "unset tokens retain array positions");
            else Check(reply.Ok && Progress(reply)["copied_references_equal"].Value<bool>(), "supported explicit null/empty values compare independently of excluded attributes");
            if (variation == "all_unset") baseline = Scenario.Reads.Count;
        }
        foreach (string variation in new[] { "clr_null", "array_clr_null", "malformed_pair", "null_getter", "ref_getter" })
        {
            var context = Reset(); SetUnsetReferences();
            if (variation == "clr_null") Scenario.Overrides["element.1.scalar.PTREF"] = null;
            if (variation == "array_clr_null") Scenario.Overrides["element.1.array.BLRFA"] = new DbElement[] { null };
            if (variation == "malformed_pair") Scenario.Overrides["element.0.ref"] = new[] { 0, 1 };
            if (variation == "null_getter") Scenario.Overrides["element.0.null"] = new InvalidOperationException("native null getter failed");
            if (variation == "ref_getter") Scenario.Overrides["element.0.ref"] = new InvalidOperationException("native null REF getter failed");
            var reply = Prepare(context);
            Check(Equals(reply.Result["status"], "blocked") && Scenario.Runs == 0, "unread CLR/null/getter failures never become equal unset values: " + variation);
        }
        int cases = 0;
        for (int mode = 0; mode < 2; mode++)
        for (int injection = 1; injection <= baseline; injection++)
        {
            var context = Reset(); SetUnsetReferences(); string revision = Ready(context); Scenario.ClearReads();
            Scenario.OnRead = key =>
            {
                if (Scenario.Reads.Count != injection) return;
                context.Fault(); if (mode == 1) Scenario.Overrides[key] = new InvalidOperationException("ordinary exception accompanied by native fault");
            };
            var reply = Execute(context, revision, 1, false);
            Check(reply.NativeFaultLatched && Scenario.Reads.Count == injection && !context.GateOwned, "verified-null native getter fault stops all later SDK access");
            cases++;
        }
        Console.WriteLine($"Component copy known-unset native fault matrix: {baseline} read/write positions / {cases} return-or-throw cases");
    }
    private static void DispatchOwnership()
    {
        var context = Reset(); string revision = Ready(context); Scenario.ClearReads();
        Action late = null;
        Scenario.Dispatcher = action => { late = action; };
        var cancelled = Execute(context, revision);
        Check(cancelled.HttpStatus == 500 && Scenario.Runs == 0 && !context.GateOwned, "not-started callback cancelled and journal completed");
        late();
        Check(Scenario.Runs == 0 && Scenario.Reads.Count == 0, "late cancelled callback never enters SDK");

        context = Reset(); revision = Ready(context); Scenario.ClearReads();
        var entered = new ManualResetEventSlim(false); var release = new ManualResetEventSlim(false);
        Task queued = null;
        Scenario.OnRead = key => { if (key == "command.run") { entered.Set(); release.Wait(15000); } };
        Scenario.Dispatcher = action => { queued = Task.Run(action); if (!entered.Wait(15000)) throw new Exception("callback did not start"); };
        Task<ParagonComponentCopyReply> request = Task.Run(() => Execute(context, revision));
        Check(entered.Wait(15000) && !request.Wait(100) && context.GateOwned, "dispatcher early return cannot release a running callback gate");
        int count = Scenario.Reads.Count;
        var status = Json(context.GetParagonComponentCopyStatus(RequestId(1), Session, Session));
        Check((string)status["status"] == "running" && Scenario.Reads.Count == count && !request.IsCompleted, "managed status works while native stage is blocked");
        var duplicate = Execute(context, revision);
        Check(duplicate.HttpStatus == 409 && Equals(duplicate.Result["status"], "duplicate") && Scenario.Reads.Count == count && !request.IsCompleted,
            "duplicate while callback is running cannot enter SDK or cancel the owner");
        release.Set(); Check(request.Wait(15000), "running callback eventually settles"); queued.Wait();
        Check(request.Result.Ok && Scenario.Runs == 1 && !context.GateOwned, "callback ownership waits for native return and readback");
    }
    private static int Main()
    {
        int calls = Normal(); Preflight(); Conflicts(); PartialReadbacks(); DispatchOwnership(); DiagnosticFaultsAndBudgets(); KnownUnsetReferences(); FaultMatrix(calls);
        Console.WriteLine($"Component copy production checks passed: {assertions}; {receipts} receipts; no AVEVA assembly loaded.");
        return 0;
    }
}
