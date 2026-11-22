using System;
using System.Collections.Generic;
using System.Linq;
using E3d21AiBridge;

namespace Aveva.Core.Utilities.ModuleInfo
{
    public sealed class Module
    {
        public string Name { get { return Scenario.Read("module.name", "PARAGON"); } }
        public int Number { get { return Scenario.Read("module.number", 41); } }
        public string Description { get { return Scenario.Read("module.description", "Catalogue"); } }
    }

    public sealed class ModuleManager
    {
        public Module CurrentModule { get { return Scenario.Read("module.current", Scenario.Module); } }
    }
}

namespace Aveva.Core.Database
{
    public sealed class Project
    {
        public static Project CurrentProject { get { return Scenario.Read("project.current", Scenario.Project); } }
        public bool IsOpen() { return Scenario.Read("project.open", true); }
        public string Name { get { return Scenario.Read("project.name", "PLANT"); } }
        public string UserName { get { return Scenario.Read("project.user", "SYSTEM"); } }
        public string Number { get { return Scenario.Read("project.number", "27"); } }
        public string Aduuid { get { return Scenario.Read("project.aduuid", "PROJECT-UUID-1"); } }
    }

    public enum DbType { Design = 1, Catalogue = 2 }

    public sealed class MDB
    {
        public static MDB CurrentMDB { get { return Scenario.Read("mdb.current", Scenario.Mdb); } }
        public string Name { get { return Scenario.Read("mdb.name", "ADMIN"); } }
        public DbElement MdbElement { get { return Scenario.Read("mdb.element", Scenario.MdbElement); } }
    }

    public static class DbAttributeInstance
    {
        public static readonly object REF = new object();
    }

    public sealed class DbElementType
    {
        private readonly string prefix;
        public DbElementType(string prefix = "ce") { this.prefix = prefix; }
        public string ShortName { get { return Scenario.Read(prefix + ".type.short_name", prefix == "ce" ? "WORL" : "MDB"); } }
    }

    public sealed class DbElement
    {
        private readonly string prefix;
        public DbElement(string prefix = "ce") { this.prefix = prefix; }
        public bool IsNull { get { return Scenario.Read(prefix + ".is_null", false); } }
        public bool IsValid { get { return Scenario.Read(prefix + ".is_valid", true); } }
        public bool IsDeleted { get { return Scenario.Read(prefix + ".is_deleted", false); } }
        public DbType DbType { get { return Scenario.Read(prefix + ".db_type", DbType.Catalogue); } }
        public DbElementType GetElementType() { return Scenario.Read(prefix + ".get_type", new DbElementType(prefix)); }
        public string GetAsString(object attribute)
        {
            if (!ReferenceEquals(attribute, DbAttributeInstance.REF)) Scenario.Forbidden("read non-REF attribute");
            return Scenario.Read(prefix + ".refno", prefix == "ce" ? "=1/2" : "=9/4");
        }
    }

    public static class CurrentElement
    {
        public static DbElement Element { get { return Scenario.Read("ce.current", Scenario.Ce); } }
    }
}

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        private readonly Action<Action> invokeOnUi;
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;
        [ThreadStatic] private static bool generatedCsgRecoveryPermitted;

        internal E3d21Context(Action<Action> invokeOnUi)
        {
            this.invokeOnUi = invokeOnUi;
            Scenario.FaultState = nativeFaultState;
        }

        private bool IsCommandActive()
        {
            if (nativeFaultState.HasFault) throw new NativeDatabaseFaultException("Native fault latched.");
            return Scenario.Busy;
        }

        private static void ThrowIfGeneratedCsgQuarantined() { }

        // PRODUCTION_NATIVE_HELPERS
    }

}

internal static class Scenario
{
    internal static Aveva.Core.Utilities.ModuleInfo.Module Module = new Aveva.Core.Utilities.ModuleInfo.Module();
    internal static Aveva.Core.Database.Project Project = new Aveva.Core.Database.Project();
    internal static Aveva.Core.Database.MDB Mdb = new Aveva.Core.Database.MDB();
    internal static Aveva.Core.Database.DbElement Ce = new Aveva.Core.Database.DbElement();
    internal static Aveva.Core.Database.DbElement MdbElement = new Aveva.Core.Database.DbElement("mdb.element");
    internal static readonly List<string> Reads = new List<string>();
    internal static readonly Dictionary<string, int> ReadCounts = new Dictionary<string, int>(StringComparer.Ordinal);
    internal static readonly Dictionary<string, Func<int, object>> Overrides = new Dictionary<string, Func<int, object>>(StringComparer.Ordinal);
    internal static int Dispatches, UiDepth, ForbiddenCalls;
    internal static bool Busy, NativeFault;
    internal static string NativeFaultAt;
    internal static NativeDatabaseFaultState FaultState;
    internal static object Result;
    internal static Exception Failure;

    internal static T Read<T>(string key, T fallback)
    {
        if (UiDepth != 1) Forbidden("SDK read outside the single UI callback: " + key);
        Reads.Add(key);
        int count;
        ReadCounts.TryGetValue(key, out count);
        count++;
        ReadCounts[key] = count;
        if (Overrides.TryGetValue(key, out Func<int, object> replacement))
        {
            object value = replacement(count);
            if (value is Exception error) throw error;
            return (T)value;
        }
        if (string.Equals(NativeFaultAt, key, StringComparison.Ordinal))
        {
            NativeFault = true;
            FaultState.Observe("Unhandled Database error 777 from MODULE_CONTEXT", null);
            throw new InvalidOperationException("Triggered native database fault.");
        }
        return fallback;
    }

    internal static void ThrowIfFaulted()
    {
        if (NativeFault) throw new E3d21AiBridge.NativeDatabaseFaultException("Native database fault is latched; stop immediately.");
    }

    internal static void Forbidden(string action)
    {
        ForbiddenCalls++;
        throw new InvalidOperationException("Forbidden native operation: " + action);
    }

    internal static void Reset()
    {
        Reads.Clear(); ReadCounts.Clear(); Overrides.Clear(); FaultState = null;
        Dispatches = UiDepth = ForbiddenCalls = 0;
        Busy = NativeFault = false; NativeFaultAt = null; Result = null; Failure = null;
        Module = new Aveva.Core.Utilities.ModuleInfo.Module();
        Project = new Aveva.Core.Database.Project(); Mdb = new Aveva.Core.Database.MDB();
        Ce = new Aveva.Core.Database.DbElement();
        MdbElement = new Aveva.Core.Database.DbElement("mdb.element");
    }
}

internal static class ModuleContextHarness
{
    private static int checks;

    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) throw new Exception(message);
    }

    private static Dictionary<string, object> Dict(object value) { return (Dictionary<string, object>)value; }

    private static Dictionary<string, object> Observe(string session = "12345678-1234-4234-8234-123456789abc")
    {
        var context = new E3d21AiBridge.E3d21Context(action =>
        {
            Scenario.Dispatches++;
            Scenario.UiDepth++;
            try { action(); }
            finally { Scenario.UiDepth--; }
        });
        return Dict(context.ReadModuleContext(session));
    }

    private static Dictionary<string, object> Child(Dictionary<string, object> value, string key)
    {
        return Dict(value[key]);
    }

    private static void CompleteModuleWithTypedCurrentElement()
    {
        Scenario.Reset();
        Dictionary<string, object> result = Observe();
        Dictionary<string, object> after = Child(Child(result, "context"), "after");
        if (!Equals(result["complete"], true))
        {
            Console.WriteLine("Native context boundary agreement: " + Child(result, "context")["unchanged"]);
            foreach (Dictionary<string, object> error in (List<Dictionary<string, object>>)result["read_errors"])
                Console.WriteLine("Native context read error: " + error["stage"] + " " + error["message"]);
        }
        Check(Equals(result["success"], true) && Equals(result["complete"], true), "complete boundary");
        Check(Equals(Child(result, "module")["name"], "PARAGON") && Equals(Child(result, "module")["number"], 41), "native module identity");
        Check(Equals(Child(after, "project")["user"], "SYSTEM"), "available project user observation");
        Check(Equals(Child(after, "project")["project_number"], "27") && Equals(Child(after, "project")["aduuid"], "PROJECT-UUID-1"), "stable project identity");
        Check(Equals(Child(after, "mdb")["name"], "ADMIN"), "current MDB name");
        Dictionary<string, object> mdbElement = Child(Child(after, "mdb"), "element");
        Check(Equals(mdbElement["refno"], "=9/4") && Equals(mdbElement["type"], "MDB") &&
            Equals(mdbElement["db_type"], "Catalogue") && Equals(mdbElement["db_type_name"], "Catalogue") &&
            Equals(mdbElement["db_type_code"], 2), "typed MDB element identity");
        Check(Equals(Child(after, "current_element")["refno"], "=1/2") && Equals(Child(after, "current_element")["type"], "WORL"), "typed CE identity");
        Check(Scenario.Dispatches == 1 && Scenario.UiDepth == 0, "one UI callback");
        Check(Scenario.Reads.All(key => !key.StartsWith("write.", StringComparison.Ordinal)) && Scenario.ForbiddenCalls == 0, "read-only SDK path");
        Check(Scenario.ReadCounts["module.current"] == 2 && Scenario.ReadCounts["project.current"] == 2 &&
            Scenario.ReadCounts["mdb.current"] == 2 && Scenario.ReadCounts["ce.current"] == 2 &&
            Scenario.ReadCounts["mdb.element"] == 2, "paired boundaries in one dispatch");
    }

    private static void VerifiedNoMdbAndNoCeIsComplete()
    {
        Scenario.Reset();
        Scenario.Overrides["mdb.element.is_null"] = _ => true;
        Scenario.Overrides["ce.is_null"] = _ => true;
        Dictionary<string, object> result = Observe();
        Dictionary<string, object> after = Child(Child(result, "context"), "after");
        Check(Equals(result["complete"], true), "verified absent MDB and CE remain informative");
        Check(Equals(Child(after, "mdb")["status"], "absent"), "null MDB element proves no current MDB");
        Check(Equals(Child(after, "current_element")["status"], "absent") &&
            Equals(Child(after, "current_element")["is_null"], true), "CE absence requires native IsNull=true");
    }

    private static void VerifiedAbsentModuleIsComplete()
    {
        Scenario.Reset();
        Scenario.Module = null;
        Dictionary<string, object> result = Observe();
        Check(Equals(result["available"], true) && Equals(result["success"], true) && Equals(result["complete"], true),
            "stable null CurrentModule is a complete verified-absent observation");
        Check(Equals(Child(result, "module")["status"], "absent") &&
            ((List<Dictionary<string, object>>)result["read_errors"]).Count == 0, "absent module is not a getter error");
    }

    private static void NotOpenProjectDoesNotReadProjectIdentity()
    {
        Scenario.Reset();
        Scenario.Overrides["project.open"] = _ => false;
        Dictionary<string, object> result = Observe();
        Dictionary<string, object> after = Child(Child(result, "context"), "after");
        Check(Equals(result["complete"], true), "closed project remains a complete module observation");
        Check(Equals(Child(after, "project")["status"], "not_open"), "closed project distinguished");
        Check(!Scenario.Reads.Contains("project.name") && !Scenario.Reads.Contains("project.user") &&
            !Scenario.Reads.Contains("project.number") && !Scenario.Reads.Contains("project.aduuid"), "closed project identity was not read");
    }

    private static void OrdinaryGetterErrorsAreRetained()
    {
        Scenario.Reset();
        Scenario.Overrides["mdb.name"] = _ => new InvalidOperationException("ordinary MDB name read failure");
        Scenario.Overrides["project.user"] = _ => new InvalidOperationException("ordinary user read failure");
        Dictionary<string, object> result = Observe();
        Dictionary<string, object> context = Child(result, "context");
        List<Dictionary<string, object>> errors = (List<Dictionary<string, object>>)result["read_errors"];
        Check(Equals(result["complete"], false), "getter failure is incomplete");
        Check(errors.Any(error => Equals(error["stage"], "before.mdb.name")) && errors.Any(error => Equals(error["stage"], "after.project.user")), "both ordinary failures retained");
        Check(Equals(context["unchanged"], null), "unknown identity does not become stable");
    }

    private static void ChangedNativeModuleIsDetected()
    {
        Scenario.Reset();
        Scenario.Overrides["module.name"] = count => count == 1 ? "PARAGON" : "ADMIN";
        Dictionary<string, object> result = Observe();
        Check(Equals(result["success"], false) && Equals(result["complete"], false), "module change invalidates observation");
        Check(Equals(Child(result, "context")["unchanged"], false), "native boundary drift is explicit");
    }

    private static void NullSingletonsAreUnknownAndIncomplete()
    {
        Scenario.Reset();
        Scenario.Project = null; Scenario.Mdb = null; Scenario.Ce = null;
        Dictionary<string, object> result = Observe();
        Dictionary<string, object> after = Child(Child(result, "context"), "after");
        Check(Equals(result["available"], true) && Equals(result["success"], false) && Equals(result["complete"], false),
            "uninitialized singleton wrappers cannot prove absence");
        foreach (string key in new[] { "project", "mdb", "current_element" })
            Check(Equals(Child(after, key)["status"], "unknown"), key + " null singleton is unknown");
        Check(((List<Dictionary<string, object>>)result["read_errors"]).Count >= 6,
            "null singleton reads retain both boundary errors");

        Scenario.Reset(); Scenario.Mdb = null;
        result = Observe(); after = Child(Child(result, "context"), "after");
        Check(Equals(Child(after, "mdb")["status"], "unknown") && !Equals(result["complete"], true),
            "null MDB wrapper is not a verified absent MDB");

        Scenario.Reset(); Scenario.MdbElement = null;
        result = Observe(); after = Child(Child(result, "context"), "after");
        Check(Equals(Child(after, "mdb")["status"], "unknown") && !Equals(result["complete"], true),
            "null MDB element wrapper is not a verified absent MDB");
    }

    private static void ProjectAndMdbTypedIdentityDriftIsDetected()
    {
        Scenario.Reset();
        Scenario.Overrides["project.aduuid"] = count => count == 1 ? "PROJECT-UUID-1" : "PROJECT-UUID-2";
        Dictionary<string, object> result = Observe();
        Check(Equals(Child(result, "context")["unchanged"], false) && Equals(result["complete"], false),
            "same-name project identity drift is detected");

        Scenario.Reset();
        Scenario.Overrides["mdb.element.refno"] = count => count == 1 ? "=9/4" : "=9/5";
        Scenario.Overrides["mdb.element.db_type"] = count => count == 1 ?
            Aveva.Core.Database.DbType.Catalogue : Aveva.Core.Database.DbType.Design;
        result = Observe();
        Check(Equals(Child(result, "context")["unchanged"], false) && Equals(result["complete"], false),
            "same-name MDB dbref and DbType drift is detected");
    }

    private static void CurrentElementPartialIdentityIsRetainedAsUnknown()
    {
        Scenario.Reset();
        Scenario.Overrides["ce.refno"] = _ => new InvalidOperationException("ordinary CE reference read failure");
        Dictionary<string, object> referenceFailure = Observe();
        Dictionary<string, object> referenceAfter = Child(Child(referenceFailure, "context"), "after");
        Dictionary<string, object> reference = Child(referenceAfter, "current_element");
        Check(Equals(reference["status"], "unknown") && reference["refno"] == null &&
            Equals(reference["type"], "WORL"), "failed CE refno retains the successful type");
        Check(((List<Dictionary<string, object>>)referenceFailure["read_errors"]).Any(error =>
            Equals(error["stage"], "before.current_element.refno")), "CE refno failure retains its error");

        Scenario.Reset();
        Scenario.Overrides["ce.type.short_name"] = _ => new InvalidOperationException("ordinary CE type read failure");
        Dictionary<string, object> result = Observe();
        Dictionary<string, object> after = Child(Child(result, "context"), "after");
        Dictionary<string, object> element = Child(after, "current_element");
        Check(Equals(element["status"], "unknown") && Equals(element["refno"], "=1/2") && element["type"] == null,
            "CE read failure preserves safe partial reference");
        Check(Equals(result["available"], true) && Equals(result["success"], false) && Equals(result["complete"], false),
            "partial CE identity remains available but incomplete");
        Check(((List<Dictionary<string, object>>)result["read_errors"]).Any(error =>
            Equals(error["stage"], "before.current_element.type")), "CE partial identity carries read error");
    }

    private static void NativeFaultStopsBeforeFurtherReads()
    {
        Scenario.Reset();
        Scenario.NativeFaultAt = "module.name";
        bool failed = false;
        try { Observe(); }
        catch (E3d21AiBridge.NativeDatabaseFaultException) { failed = true; }
        Check(failed && Scenario.Dispatches == 1, "fault propagated from one callback");
        Check(Scenario.ReadCounts.ContainsKey("module.current") && !Scenario.Reads.Contains("project.current") &&
            !Scenario.Reads.Contains("mdb.current") && !Scenario.Reads.Contains("ce.current"), "native fault stops all subsequent getters");
    }

    private static void InvalidSessionAndBusySessionDoNotDispatch()
    {
        Scenario.Reset();
        bool invalid = false;
        try { Observe("not-a-guid"); } catch (ArgumentException) { invalid = true; }
        Check(invalid && Scenario.Dispatches == 0, "invalid session rejected before native access");
        Scenario.Reset(); Scenario.Busy = true;
        bool busy = false;
        try { Observe(); } catch (InvalidOperationException) { busy = true; }
        Check(busy && Scenario.Dispatches == 0 && Scenario.Reads.Count == 0, "busy bridge rejects before UI dispatch");
    }

    public static void Main()
    {
        try
        {
            CompleteModuleWithTypedCurrentElement();
            VerifiedNoMdbAndNoCeIsComplete();
            VerifiedAbsentModuleIsComplete();
            NotOpenProjectDoesNotReadProjectIdentity();
            OrdinaryGetterErrorsAreRetained();
            ChangedNativeModuleIsDetected();
            NullSingletonsAreUnknownAndIncomplete();
            ProjectAndMdbTypedIdentityDriftIsDetected();
            CurrentElementPartialIdentityIsRetainedAsUnknown();
            NativeFaultStopsBeforeFurtherReads();
            InvalidSessionAndBusySessionDoNotDispatch();
            Console.WriteLine("Module-context production native checks passed: " + checks.ToString());
        }
        catch (Exception error)
        {
            Console.WriteLine("Module-context failure type: " + error.GetType().FullName);
            Console.WriteLine("Module-context failure message: " + error.Message);
            Console.WriteLine("Module-context failure stack: " + error.StackTrace);
            Environment.ExitCode = 1;
        }
    }
}
