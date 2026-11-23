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
            return Scenario.Read(Key + ".double_array." + attribute.Key, Scenario.ParameterValues);
        }
        public bool IsAttributeSetable(DbAttribute attribute) => Scenario.Read(Key + ".setable." + attribute.Key, true);
        public void SetAttribute(DbAttribute attribute, double[] values)
        {
            if (Id != 1 || attribute.Key != "PARAM") Scenario.Forbidden("setter on an unbound target or arbitrary attribute");
            Scenario.Writes++; Scenario.ParameterValues = (double[])values.Clone();
            Scenario.OnSet?.Invoke(values);
            Scenario.Read(Key + ".set.PARAM", true);
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
        foreach (string type in new[] { "SCOM", "CATE", "GMSE", "EQUI", ":CUSTOM_SCOM", "NULL" }) Types[type] = new DbElementType(type);
        foreach (string name in new[] { "PARAM", "PTREF", "GMREF", "DTREF", "BLRFA", "NGMREF", "PSTREF", "GSTREF" }) Attributes[name] = new DbAttribute(name);
        Project = new Project(); Mdb = new MDB(); Root = new DbElement(1, "SCOM");
        Ce = new DbElement(9, "EQUI"); Reference = new DbElement(4, "GMSE"); Parent = new DbElement(2, "CATE");
        Elements.Clear(); Elements[1] = Root; Elements[2] = Parent; Elements[4] = Reference;
        Members.Clear(); Members.Add(Root); Named = null; Runs = 0; RunSuccess = true;
        OnRun = ExecuteMutation; Dispatcher = action => action(); MacroSource = MacroPath = null;
        ParameterValues = new[] { 1.0, 2.5 }; Writes = 0; OnSet = null;
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

internal static class ParagonComponentParametersChecks
{
    private const string Session = "native-parameter-session";
    private static readonly string Build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
    private static int assertions, receipts;
    private static double[] Desired = new[] { 3.0, 4.5 };
    private static string Id(int value) => "00000000-0000-0000-0000-" + value.ToString("x12");
    private static JObject Json(object value) => JObject.FromObject(value);
    private static void Check(bool value, string message) { assertions++; if (!value) throw new Exception(message); }
    private static JObject Progress(ParagonComponentParametersReply value) => Json(value.Result["progress"]);
    private static E3d21Context Reset() { Scenario.Reset(); Desired = new[] { 3.0, 4.5 }; return new E3d21Context(); }
    private static ParagonComponentParametersReply Prepare(E3d21Context context, bool emit = true)
    {
        var result = context.SetParagonComponentParameters("=2/1", Desired, true, null, null, null, null, Session);
        if (emit) Emit(result, true, null, null);
        return result;
    }
    private static ParagonComponentParametersReply Execute(E3d21Context context, string revision, int id = 1, bool emit = true)
    {
        var result = context.SetParagonComponentParameters("=2/1", Desired, false, Id(id), Session, Build, revision, Session);
        if (emit) Emit(result, false, Id(id), revision);
        return result;
    }
    private static void Emit(ParagonComponentParametersReply result, bool dryRun, string id, string revision)
    {
        Check(Scenario.ForbiddenCalls == 0 && Scenario.Runs == 0 && !Scenario.Reads.Any(key => key.StartsWith("command.")), "parameter operation never invokes commands, SDK equality/hash or implicit model writes");
        Check(Progress(result).Count == 11 && !(bool)Progress(result)["retry_safe"] && !(bool)Json(result.Result)["request_replayed"], "exact progress and no retry");
        var payload = new JObject { ["refno"] = "=2/1", ["parameters"] = new JArray(Desired), ["parameter_bits"] = new JArray(Desired.Select(ParagonComponentParametersRequest.Bits)), ["dry_run"] = dryRun };
        if (!dryRun) { payload["request_id"] = id; payload["expected_session_id"] = Session; payload["expected_bridge_build"] = Build; payload["expected_revision"] = revision; }
        receipts++;
        Console.WriteLine("Component parameters receipt: " + JsonConvert.SerializeObject(new {
            payload, http_status = result.HttpStatus,
            reply = new { ok = result.Ok, version = "E3D21", session_id = Session, bridge_build = Build, result = result.Result,
                error = result.Error, error_code = result.NativeFaultLatched ? "native_database_fault" : null, native_fault_latched = result.NativeFaultLatched, retry_safe = false }
        }));
    }
    private static string Ready(E3d21Context context)
    {
        var result = Prepare(context, false);
        Check(result.Ok && Equals(result.Result["status"], "prepared"), "required observations must prepare: " + JsonConvert.SerializeObject(result));
        Check(Scenario.Writes == 0 && !context.GateOwned, "preview never writes or retains its gate");
        return (string)result.Result["scoped_revision"];
    }
    private static int Normal()
    {
        var context = Reset(); var before = Prepare(context);
        Check(before.Ok && (bool)Progress(before)["known_target_refno_verified"] && Progress(before)["context_unchanged"].Type == JTokenType.Null, "preview binds explicit target without claiming a readback");
        Scenario.ClearReads(); var result = Execute(context, (string)before.Result["scoped_revision"]);
        Check(result.Ok && Equals(result.Result["status"], "updated") && Scenario.Writes == 1 && !context.GateOwned, "one direct SDK update succeeds");
        Check((bool)Progress(result)["parameter_write_attempted"] && (bool)Progress(result)["parameter_write_returned"] && (bool)Progress(result)["parameters_equal_request"] && (bool)Progress(result)["readback_complete"], "setter returned and independent equality are separate facts");
        Check((string)Progress(result)["known_target_refno"] == "=2/1" && Json(result.Result["after"])["setable"].Type == JTokenType.Null, "verified REF survives and post setable is unobserved");
        Check(Scenario.Reads.Count(key => key == "element.1.setable.PARAM") == 1 && Scenario.Reads.Count(key => key == "element.1.double_array.PARAM") == 2, "pre-setable is read once and native arrays are independently read before/after");
        int calls = Scenario.Reads.Count;
        var duplicate = Execute(context, (string)before.Result["scoped_revision"]);
        Check(duplicate.HttpStatus == 409 && Equals(duplicate.Result["status"], "duplicate") && Scenario.Reads.Count == calls && Scenario.Writes == 1, "duplicate is zero SDK and cannot repeat setter");
        var status = Json(context.GetParagonComponentParametersStatus(Id(1), Session, Session));
        Check((string)status["operation"] == "paragon_component_parameters_set" && (string)status["status"] == "completed" && status["critical_summary"].Count() == 12, "new managed journal has exact parameter facts");
        Check(Scenario.Reads.Count == calls, "status performs no SDK reads");
        return calls;
    }
    private static void NoChangeAndBits()
    {
        {
            var context = Reset(); Desired = (double[])Scenario.ParameterValues.Clone(); string revision = Ready(context);
            var reply = Execute(context, revision);
            Check(reply.Ok && Equals(reply.Result["status"], "unchanged") && Scenario.Writes == 0 && !(bool)Progress(reply)["parameter_write_returned"] && (bool)Progress(reply)["readback_complete"], "no-change skips the setter but still independently reads back");
        }
        foreach (bool reverse in new[] { false, true })
        {
            var context = Reset(); double negative = BitConverter.Int64BitsToDouble(long.MinValue);
            Scenario.ParameterValues = new[] { reverse ? negative : 0.0, 2.5 };
            Desired = new[] { reverse ? 0.0 : negative, 2.5 };
            var preview = Prepare(context); var reply = Execute(context, (string)preview.Result["scoped_revision"]);
            Check(reply.Ok && Equals(reply.Result["status"], "updated") && Scenario.Writes == 1, "opposite zero signs are distinct intentions");
            string expected = reverse ? "0000000000000000" : "8000000000000000";
            Check((string)Json(reply.Result["request"])["parameter_bits"][0] == expected && (string)Json(reply.Result["after"])["parameter_bits"][0] == expected, "wire bits preserve negative zero despite JSON numeric normalization");
        }
        {
            var context = Reset(); Scenario.ParameterValues = new[] { double.Epsilon, -double.MaxValue }; Desired = new[] { -double.Epsilon, double.MaxValue };
            string revision = Ready(context); var reply = Execute(context, revision);
            Check(reply.Ok, "finite extremes and subnormal values are valid DOUBLE requests");
        }
        {
            var context = Reset(); Scenario.ParameterValues = new double[128]; Desired = Enumerable.Range(1, 128).Select(value => (double)value).ToArray();
            string revision = Ready(context); var reply = Execute(context, revision);
            Check(reply.Ok && Json(reply.Result["after"])["parameter_bits"].Count() == 128, "full 128-element vector remains bounded and exact");
        }
    }
    private static void RequiredAndOptional()
    {
        foreach (string failure in new[] { "module", "actual", "leaf", "owner", "setable", "metadata", "unknown", "nonfinite", "length", "limit", "inspection", "ce" })
        {
            var context = Reset();
            if (failure == "module") Scenario.Overrides["module.number"] = 1;
            if (failure == "actual") Scenario.Overrides["element.1.actual_type"] = Scenario.Types[":CUSTOM_SCOM"];
            if (failure == "leaf") Scenario.Overrides["element.1.member.1"] = Scenario.Reference;
            if (failure == "owner") Scenario.Overrides["element.1.owner"] = Scenario.Reference;
            if (failure == "setable") Scenario.Overrides["element.1.setable.PARAM"] = false;
            if (failure == "metadata") Scenario.Overrides["attribute.PARAM.type"] = DbAttributeType.WORD;
            if (failure == "unknown") Scenario.Overrides["element.1.double_array.PARAM"] = new InvalidOperationException("value getter failed");
            if (failure == "nonfinite") Scenario.ParameterValues = new[] { double.NaN, 2.5 };
            if (failure == "length") Scenario.ParameterValues = new[] { 1.0 };
            if (failure == "limit") Scenario.ParameterValues = new double[129];
            if (failure == "inspection") Scenario.ParameterValues = new double[4097];
            if (failure == "ce") Scenario.Overrides["ce"] = null;
            var reply = Prepare(context);
            Check(!Equals(reply.Result["status"], "prepared") && Scenario.Writes == 0, "missing required observation blocks setter: " + failure);
        }
        foreach (string optional in new[] { "name", "owner_name", "unit", "reference", "reference_name", "type_name", "unsupported" })
        {
            var context = Reset(); string revision = Ready(context);
            if (optional == "name") Scenario.Overrides["element.1.name"] = new InvalidOperationException("NAME failed");
            if (optional == "owner_name") Scenario.Overrides["element.2.name"] = new InvalidOperationException("owner NAME failed");
            if (optional == "unit") Scenario.Overrides["attribute.PARAM.units"] = new InvalidOperationException("unit kind failed");
            if (optional == "reference") Scenario.Overrides["element.1.scalar.PTREF"] = new InvalidOperationException("reference failed");
            if (optional == "reference_name") Scenario.Overrides["element.4.name"] = new InvalidOperationException("reference NAME failed");
            if (optional == "type_name") foreach (string type in new[] { "SCOM", "CATE", "GMSE" }) Scenario.Overrides["type." + type + ".name"] = new InvalidOperationException("type display name failed");
            if (optional == "unsupported") foreach (string key in new[] { "PTREF", "GMREF", "DTREF", "BLRFA", "NGMREF", "PSTREF", "GSTREF" }) Scenario.Overrides["element.1.supports." + key] = false;
            var preview = Prepare(context);
            Check(preview.Ok && Equals(preview.Result["scoped_revision"], revision), "optional partial does not alter required revision: " + optional);
            var reply = Execute(context, revision);
            Check(reply.Ok && Scenario.Writes == 1, "optional fields cannot block complete numeric update: " + optional);
        }
    }
    private static void RevalidationAndFailures()
    {
        foreach (string change in new[] { "project", "mdb", "values", "requested", "owner", "revision" })
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads();
            if (change == "project") Scenario.Project = new Project();
            if (change == "mdb") Scenario.Mdb = new MDB();
            if (change == "values") Scenario.ParameterValues = new[] { 2.0, 2.5 };
            if (change == "requested") Desired = new[] { 5.0, 6.0 };
            if (change == "owner") Scenario.Overrides["element.1.owner"] = new DbElement(10, "CATE");
            if (change == "revision") revision = new string('f', 64);
            var reply = Execute(context, revision);
            Check(reply.HttpStatus == 409 && !reply.Ok && Scenario.Writes == 0, "stale required state cannot enter setter: " + change);
        }
        foreach (string failure in new[] { "setter", "readback", "normalized", "length", "owner", "project", "ce", "actual" })
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads();
            Scenario.OnSet = values =>
            {
                if (failure == "setter") Scenario.Overrides["element.1.set.PARAM"] = new InvalidOperationException("setter threw after partial write");
                if (failure == "readback") Scenario.Overrides["element.1.double_array.PARAM"] = new InvalidOperationException("readback failed");
                if (failure == "normalized") Scenario.ParameterValues = new[] { 3.0, 4.50001 };
                if (failure == "length") Scenario.ParameterValues = new[] { 3.0 };
                if (failure == "owner") Scenario.Overrides["element.1.owner"] = Scenario.Reference;
                if (failure == "project") Scenario.Project = new Project();
                if (failure == "ce") Scenario.Ce = Scenario.Reference;
                if (failure == "actual") Scenario.Overrides["element.1.actual_type"] = Scenario.Types[":CUSTOM_SCOM"];
            };
            var reply = Execute(context, revision);
            Check(!reply.Ok && reply.HttpStatus == 500 && Scenario.Writes == 1 && (bool)Progress(reply)["outcome_uncertain"], "attempted failed write remains uncertain: " + failure);
            Check((string)Progress(reply)["known_target_refno"] == "=2/1" && (bool)Progress(reply)["known_target_refno_verified"], "bound REF survives all later failures");
            if (failure == "setter") Check(!(bool)Progress(reply)["parameter_write_returned"] && (bool)Progress(reply)["parameters_equal_request"], "readback equality cannot turn a throwing setter into success");
        }
    }
    private static void FaultMatrix(int reads)
    {
        int cases = 0;
        for (int mode = 0; mode < 2; mode++)
        for (int index = 1; index <= reads; index++)
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads();
            Scenario.OnRead = key => { if (Scenario.Reads.Count == index) { context.Fault(); if (mode == 1) Scenario.Overrides[key] = new InvalidOperationException("ordinary exception accompanied by fault"); } };
            var reply = Execute(context, revision, 1, false);
            Check(reply.NativeFaultLatched && reply.HttpStatus == 500 && Scenario.Reads.Count == index, "every fault immediately ends native SDK work");
            Check(!context.GateOwned && Scenario.UiDepth == 0 && Scenario.ForbiddenCalls == 0, "faulted callback owns completion until its scope settles");
            if (Scenario.Reads.Last() == "element.1.set.PARAM")
            {
                Check((bool)Progress(reply)["parameter_write_attempted"] && (bool)Progress(reply)["parameter_write_returned"] == (mode == 0), "setter return is retained inside NativeWrite before its finally fault guard");
                Check((string)Progress(reply)["known_target_refno"] == "=2/1", "setter fault cannot erase already bound REF");
            }
            int writes = Scenario.Writes;
            var duplicate = Execute(context, revision, 1, false); var next = Execute(context, revision, 2, false);
            Check(duplicate.HttpStatus == 409 && next.NativeFaultLatched && Scenario.Reads.Count == index && Scenario.Writes == writes, "neither duplicate nor a new ID can bypass a latched fault");
            var status = Json(context.GetParagonComponentParametersStatus(Id(1), Session, Session));
            Check((string)status["status"] == "completed" && (bool)status["critical_summary"]["native_fault_latched"] && Scenario.Reads.Count == index, "fault journal is readable with zero SDK diagnostics");
            cases++;
        }
        Console.WriteLine($"Component parameters native fault matrix: {reads} SDK read/write positions / {cases} return-or-throw cases");
    }
    private static void DispatchOwnership()
    {
        var context = Reset(); string revision = Ready(context); Scenario.ClearReads(); Action queued = null;
        Scenario.Dispatcher = action => { queued = action; };
        var cancelled = Execute(context, revision);
        Check(cancelled.HttpStatus == 500 && Scenario.Writes == 0 && !context.GateOwned, "not-started callback cancelled before completion");
        queued(); Check(Scenario.Reads.Count == 0 && Scenario.Writes == 0, "late cancelled UI callback cannot write");
        context = Reset(); revision = Ready(context); Scenario.ClearReads();
        var entered = new ManualResetEventSlim(false); var release = new ManualResetEventSlim(false); Task callback = null;
        Scenario.OnRead = key => { if (key == "element.1.set.PARAM") { entered.Set(); release.Wait(15000); } };
        Scenario.Dispatcher = action => { callback = Task.Run(action); if (!entered.Wait(15000)) throw new Exception("callback did not begin"); };
        Task<ParagonComponentParametersReply> run = Task.Run(() => Execute(context, revision));
        Check(entered.Wait(15000) && !run.Wait(100) && context.GateOwned, "early dispatcher return cannot release running setter ownership");
        int reads = Scenario.Reads.Count;
        var state = Json(context.GetParagonComponentParametersStatus(Id(1), Session, Session));
        var duplicate = Execute(context, revision);
        Check((string)state["status"] == "running" && duplicate.HttpStatus == 409 && Scenario.Reads.Count == reads && !run.IsCompleted, "status/duplicate remain purely managed while native setter blocks");
        release.Set(); Check(run.Wait(15000), "callback eventually completes"); callback.Wait();
        Check(run.Result.Ok && Scenario.Writes == 1 && !context.GateOwned, "real callback completion precedes gate release");
        context = Reset(); Desired = new[] { 3.0, 4.5 }; revision = Ready(context);
        Scenario.Dispatcher = action => { Desired[0] = 999.0; action(); };
        var reply = context.SetParagonComponentParameters("=2/1", Desired, false, Id(1), Session, Build, revision, Session);
        Check(reply.Ok && Scenario.ParameterValues[0] == 3.0, "validated array is owned and cannot be mutated while callback queues");
    }
    private static void MoreBoundariesAndOmission()
    {
        {
            var context = Reset();
            Scenario.OnRead = key =>
            {
                if (key == "ce" && Scenario.Counts[key] == 2) Scenario.Overrides[key] = Scenario.Reference;
                if (key == "ce" && Scenario.Counts[key] == 4) Scenario.Overrides[key] = Scenario.Ce;
            };
            var reply = Prepare(context);
            Check(Equals(reply.Result["status"], "blocked") && Scenario.Writes == 0, "template-internal stable CE cannot substitute for independent outer boundary");
        }
        foreach (string field in new[] { "project", "mdb", "typed_ce" })
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads();
            Scenario.OnSet = values =>
            {
                if (field == "project") Scenario.Project = new Project();
                if (field == "mdb") Scenario.Mdb = new MDB();
                if (field == "typed_ce") Scenario.Overrides["element.9.db_type"] = DbType.Catalogue;
            };
            var reply = Execute(context, revision);
            Check(!reply.Ok && (bool)Progress(reply)["parameter_write_returned"] && (bool)Progress(reply)["outcome_uncertain"], "same-name instance or typed CE change after setter does not become success");
            Check((string)Progress(reply)["known_target_refno"] == "=2/1", "context loss preserves target already bound before the write");
        }
        foreach (string field in new[] { "attribute.PARAM.units", "element.1.name", "element.1.scalar.PTREF" })
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads();
            Scenario.OnSet = values => Scenario.Overrides[field] = new InvalidOperationException("optional post-read getter failure");
            var reply = Execute(context, revision);
            Check(reply.Ok && Equals(reply.Result["status"], "updated"), "new optional post-read errors do not erase observed numeric bits");
        }
        {
            var context = Reset();
            foreach (string attr in new[] { "PTREF", "GMREF", "DTREF", "BLRFA", "NGMREF", "PSTREF", "GSTREF" })
            {
                Scenario.Overrides["attribute." + attr + ".array"] = true;
                Scenario.Overrides["element.1.array." + attr] = Enumerable.Repeat(Scenario.Reference, 16).ToArray();
            }
            Scenario.Overrides["element.4.name"] = "/" + new string('x', 1023);
            string revision = Ready(context); var reply = Execute(context, revision);
            var journal = Json(reply.Result["journal"]);
            Check(reply.Ok && journal["receipt"].Type == JTokenType.Null && (string)journal["receipt_omitted_reason"] == "receipt_too_large", "large optional evidence can omit the stored full receipt without losing terminal facts");
            Check((string)journal["critical_summary"]["known_target_refno"] == "=2/1" && (bool)journal["critical_summary"]["parameter_write_returned"] && (bool)journal["critical_summary"]["readback_complete"], "critical parameter facts survive receipt omission");
        }
    }
    private static void OrdinaryGetterMatrix()
    {
        foreach (string key in new[] { "module.name", "project.name", "mdb.name", "ce", "element.1.ref", "element.1.base_type", "element.1.actual_type", "element.1.member.1", "element.1.owner", "attribute.resolve.PARAM", "attribute.PARAM.type", "attribute.PARAM.array", "element.1.supports.PARAM", "element.1.setable.PARAM", "element.1.double_array.PARAM" })
        for (int phase = 0; phase < 2; phase++)
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads();
            if (phase == 0) Scenario.Overrides[key] = new InvalidOperationException("ordinary pre-setter native getter failure");
            else Scenario.OnSet = values => Scenario.Overrides[key] = new InvalidOperationException("ordinary post-setter native getter failure");
            var reply = Execute(context, revision);
            if (phase == 1 && key == "element.1.setable.PARAM")
                Check(reply.Ok, "post-setable is unobserved and cannot add a failure");
            else Check(!reply.Ok && Scenario.Writes == phase, "ordinary required getter failures never lead to a false success or additional setter");
            Check(!context.GateOwned && !reply.NativeFaultLatched, "ordinary SDK failures settle the callback without inventing nativefault");
        }
    }
    private static void FaultAfterFirstBinding()
    {
        foreach (string key in new[] { "element.1.scalar.PTREF", "element.4.name", "attribute.GMREF.units" })
        {
            var context = Reset(); string revision = Ready(context); Scenario.ClearReads(); int stopped = 0;
            Scenario.OnRead = current => { if (current == key) { stopped = Scenario.Reads.Count; context.Fault(); } };
            var result = Execute(context, revision);
            Check(result.NativeFaultLatched && stopped == Scenario.Reads.Count && Scenario.Writes == 0, "optional nativefault prevents setter and stops every later native read");
            Check((string)Progress(result)["known_target_refno"] == "=2/1" && (bool)Progress(result)["known_target_refno_verified"], "first binding survives even when full template core does not return");
        }
        foreach (string variation in new[] { "lookup_failure", "lookup_substitute", "base_drift", "db_drift" })
        {
            var context = Reset();
            Scenario.OnRead = key =>
            {
                if (key != "element.resolve.1" || Scenario.Counts[key] != 2) return;
                if (variation == "lookup_failure") Scenario.Overrides[key] = new InvalidOperationException("second template resolution failed");
                if (variation == "lookup_substitute") Scenario.Overrides[key] = Scenario.Parent;
                if (variation == "base_drift") Scenario.Overrides["element.1.base_type"] = Scenario.Types["GMSE"];
                if (variation == "db_drift") Scenario.Overrides["element.1.db_type"] = DbType.Design;
            };
            var reply = Prepare(context);
            Check(reply.Ok && Equals(reply.Result["status"], "blocked") && Scenario.Writes == 0, "later binding failure is a legitimate blocked preview: " + variation);
            Check((string)Json(reply.Result["before"])["target_binding"]["refno"] == "=2/1" && (bool)Progress(reply)["known_target_refno_verified"], "the original typed binding remains independently reviewable after later drift");
        }
    }
    private static void TerminalPreflightConflictsRetainArrayComparison()
    {
        foreach (string conflict in new[] { "module", "project_instance" })
        {
            var context = Reset();
            Scenario.OnRead = key =>
            {
                if (conflict == "module" && key == "module.number" && Scenario.Counts[key] == 2)
                    Scenario.Overrides[key] = 1;
                if (conflict == "project_instance" && key == "project.current" && Scenario.Counts[key] == 4)
                    Scenario.Overrides[key] = new Project();
            };
            var reply = Prepare(context);
            Check(reply.Ok && Equals(reply.Result["status"], "blocked") && Scenario.Writes == 0,
                "terminal preflight conflict preserves a legitimate blocked preview: " + conflict);
            var before = Json(reply.Result["before"]);
            Check(before.Count == 7 && before["owner"].Type == JTokenType.Object && before["setable"].Value<bool>()
                && before["parameter_bits"].Count() == Desired.Length, "required before slice survives the terminal conflict");
            Check(Progress(reply)["no_change"].Type == JTokenType.Boolean && !Progress(reply)["no_change"].Value<bool>(),
                "observed array comparison is retained even when terminal context/module capture is rejected");
            Check(Progress(reply)["context_unchanged"].Type == JTokenType.Null && reply.Result["scoped_revision"] == null,
                "retained no-change observation does not claim that terminal context validation passed");
        }
    }
    private static int Main()
    {
        int reads = Normal(); NoChangeAndBits(); RequiredAndOptional(); RevalidationAndFailures(); DispatchOwnership(); MoreBoundariesAndOmission(); OrdinaryGetterMatrix(); FaultAfterFirstBinding(); TerminalPreflightConflictsRetainArrayComparison(); FaultMatrix(reads);
        Console.WriteLine($"Component parameters production checks passed: {assertions}; {receipts} receipts; no SDK assemblies loaded.");
        return 0;
    }
}
