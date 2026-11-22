// The executable links the POC and the real managed native guard to recording fakes.
using System;
using System.Collections.Generic;
using System.Linq;
using Aveva.Core.Database;
using E3d21AiBridge;

namespace Aveva.Core.Database
{
    public enum DbType { Design = 1, Catalogue = 2, Property = 4, Draft = 7 }
    public enum DbAttributeType { STRING, ELEMENT }

    public sealed class Project
    {
        public static Project CurrentProject => TraceFixture.Read("project", TraceFixture.Project);
        public string Name => TraceFixture.Read("project.name", "fixture-project");
    }

    public sealed class MDB
    {
        public static MDB CurrentMDB => TraceFixture.Read("mdb", TraceFixture.Mdb);
        public string Name => TraceFixture.Read("mdb.name", "fixture-mdb");
    }

    public static class CurrentElement
    {
        public static DbElement Element => TraceFixture.Read("ce", TraceFixture.Ce);
    }

    public sealed class DbAttribute
    {
        internal readonly string Key;
        internal DbAttribute(string key) { Key = key; }
        public bool IsValid => TraceFixture.Read(Key + ".valid", true);
        public DbAttributeType Type => TraceFixture.Read(Key + ".type", DbAttributeType.ELEMENT);
    }

    public static class DbAttributeInstance
    {
        public static readonly DbAttribute SPRE = new DbAttribute("SPRE"), CATR = new DbAttribute("CATR"),
            PRTREF = new DbAttribute("PRTREF"), CMPR = new DbAttribute("CMPR"),
            MATR = new DbAttribute("MATR"), FLUR = new DbAttribute("FLUR");
    }

    public sealed class DbElementType
    {
        private readonly DbElement element;
        internal DbElementType(DbElement element) { this.element = element; }
        public bool IsValid => TraceFixture.Read(element.Key + ".type.valid", true);
        public string ShortName => TraceFixture.Read(element.Key + ".type.short", element.Noun);
    }

    public sealed class DbElement
    {
        internal readonly string Key;
        internal readonly string Noun;
        private readonly int[] pair;
        private readonly DbType db;
        internal DbElement DirectOwner;
        internal readonly Dictionary<string, DbElement> Attributes = new Dictionary<string, DbElement>();
        internal DbElement(int dbno, int no, DbType db, string noun)
        { this.db = db; pair = new[] { dbno, no }; Key = $"={dbno}/{no}"; Noun = noun; }
        internal static readonly DbElement Null = new DbElement(0, 0, 0, null);

        public static DbElement GetElement(int[] pair)
        {
            string key = $"={pair[0]}/{pair[1]}";
            return TraceFixture.Read("lookup." + key, TraceFixture.Elements.TryGetValue(key, out var target) ? target : Null);
        }
        public DbElement GetElement(DbAttribute attribute) => TraceFixture.Read(Key + ".get." + attribute.Key, Attributes[attribute.Key]);
        public bool IsAttributeValid(DbAttribute attribute) => TraceFixture.Read(Key + ".supports." + attribute.Key, Attributes.ContainsKey(attribute.Key));
        public int[] Ref => TraceFixture.Read(Key + ".ref", pair);
        public bool IsNull => TraceFixture.Read(Key + ".null", ReferenceEquals(this, Null));
        public bool IsValid => TraceFixture.Read(Key + ".valid", true);
        public bool IsDeleted => TraceFixture.Read(Key + ".deleted", false);
        public DbElementType ElementType => TraceFixture.Read(Key + ".type", new DbElementType(this));
        public DbType DbType => TraceFixture.Read(Key + ".db", db);
        public DbElement Owner => TraceFixture.Read(Key + ".owner", DirectOwner ?? Null);
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
            TraceFixture.Dispatches++;
            TraceFixture.UiDepth++;
            try { action(); }
            finally { TraceFixture.UiDepth--; }
        };
        private bool IsCommandActive() => TraceFixture.Busy || TraceFixture.BusyInUi && TraceFixture.UiDepth != 0;
        internal void Fault() { nativeFaultState.Observe("Unhandled Database error 538 from Fixture", ""); }

        // PRODUCTION_NATIVE_HELPERS
    }
}

internal static class TraceFixture
{
    internal static readonly Dictionary<string, DbElement> Elements = new Dictionary<string, DbElement>();
    internal static readonly List<string> Calls = new List<string>();
    internal static readonly Dictionary<string, object> Overrides = new Dictionary<string, object>();
    internal static Project Project;
    internal static MDB Mdb;
    internal static DbElement Ce, Design, Spco, Scom, Cate, Cata, Part, Spec, Sele, Props, Material, Fluid;
    internal static int UiDepth, Dispatches;
    internal static bool Busy, BusyInUi;
    internal static Action<string> OnRead;

    internal static T Read<T>(string key, T result)
    {
        if (UiDepth != 1) throw new InvalidOperationException("SDK read outside the one UI dispatch: " + key);
        Calls.Add(key);
        OnRead?.Invoke(key);
        if (!Overrides.TryGetValue(key, out var value)) return result;
        if (value is Exception failure) throw failure;
        return (T)value;
    }

    private static DbElement Add(int dbno, int refno, DbType db, string type, DbElement owner = null)
    {
        var element = new DbElement(dbno, refno, db, type) { DirectOwner = owner };
        Elements.Add(element.Key, element);
        return element;
    }

    internal static E3d21Context Reset()
    {
        Elements.Clear(); Calls.Clear(); Overrides.Clear();
        Project = new Project(); Mdb = new MDB(); OnRead = null; UiDepth = Dispatches = 0; Busy = BusyInUi = false;
        Ce = Add(1, 20, DbType.Design, "EQUI");
        Design = Add(1, 21, DbType.Design, "ELBO", Add(1, 22, DbType.Design, "BRAN"));
        var world = Add(2, 0, DbType.Catalogue, "WORL");
        Cata = Add(2, 1, DbType.Catalogue, "CATA", world);
        Cate = Add(2, 3, DbType.Catalogue, "CATE", Add(2, 2, DbType.Catalogue, "SECT", Cata));
        Scom = Add(2, 4, DbType.Catalogue, "SCOM", Cate);
        Part = Add(2, 5, DbType.Catalogue, "GPART", Add(2, 6, DbType.Catalogue, "PRTELE"));
        Spec = Add(2, 7, DbType.Catalogue, "SPEC", Add(2, 8, DbType.Catalogue, "SPWL", world));
        Sele = Add(2, 9, DbType.Catalogue, "SELE", Spec);
        Spco = Add(2, 10, DbType.Catalogue, "SPCO", Add(2, 11, DbType.Catalogue, "SELE", Sele));
        Props = Add(3, 1, DbType.Property, "CMPD", Add(3, 2, DbType.Property, "CMPT"));
        var matw = Add(3, 3, DbType.Property, "MATW");
        Material = Add(3, 4, DbType.Property, "SOLI", matw);
        Fluid = Add(3, 5, DbType.Property, "FLUI", matw);
        Design.Attributes["SPRE"] = Spco;
        Spco.Attributes["CATR"] = Scom; Spco.Attributes["PRTREF"] = Part; Spco.Attributes["CMPR"] = Props;
        Part.Attributes["CATR"] = Scom; Part.Attributes["CMPR"] = Props;
        Spec.Attributes["MATR"] = Material; Spec.Attributes["FLUR"] = Fluid;
        return new E3d21Context();
    }
}

internal static class ParagonReferenceTraceChecks
{
    private static void Check(bool condition, string message)
    { if (!condition) throw new Exception(message); }

    private static void Throws<T>(Action call) where T : Exception
    { try { call(); } catch (T) { return; } throw new Exception("Expected " + typeof(T).Name); }

    private static Dictionary<string, Dictionary<string, object>> Rows(Dictionary<string, object> result)
        => (Dictionary<string, Dictionary<string, object>>)result["trace"];
    private static Dictionary<string, object> Limit(Dictionary<string, object> result)
        => (Dictionary<string, object>)result["trace_limit"];
    private static void Row(Dictionary<string, object> result, string path, string status, string reference = null, string kind = null, string owner = null)
    {
        Check(Rows(result).TryGetValue(path, out var row), "missing path: " + path);
        Check(row.Count == 4 && Equals(row["ref"], reference) && Equals(row["type"], kind) &&
            Equals(row["owner"], owner) && Equals(row["status"], status), "incorrect identity/status for " + path);
    }

    private static int Full()
    {
        var context = TraceFixture.Reset();
        var result = context.ReadParagonReferenceTrace("=1/21");
        Check(result.Count == 3 && Equals(result["status"], "observed"), "result must contain only trace, status and trace_limit");
        Check(TraceFixture.Dispatches == 1 && TraceFixture.Calls.Count(k => k.StartsWith("lookup.")) == 1, "one dispatch and one numeric lookup");
        Row(result, "root", "resolved", "=1/21", "ELBO", "=1/22");
        Row(result, "root.SPREF", "resolved", "=2/10", "SPCO", "=2/11");
        Row(result, "root.SPREF.CATREF.OWNER", "resolved", "=2/3", "CATE", "=2/2");
        Row(result, "root.SPREF.CATREF.OWNER.OWNER", "resolved", "=2/2", "SECT", "=2/1");
        Row(result, "root.SPREF.CATREF.OWNER.OWNER.OWNER", "resolved", "=2/1", "CATA", "=2/0");
        Row(result, "root.SPREF.PRTREF", "resolved", "=2/5", "GPART", "=2/6");
        Row(result, "root.SPREF.PRTREF.CATREF", "resolved", "=2/4", "SCOM", "=2/3");
        Row(result, "root.SPREF.PRTREF.CMPREF", "resolved", "=3/1", "CMPD", "=3/2");
        Row(result, "root.SPREF.CMPREF", "resolved", "=3/1", "CMPD", "=3/2");
        string spec = "root.SPREF.OWNER.OWNER.OWNER";
        Row(result, spec, "resolved", "=2/7", "SPEC", "=2/8");
        Row(result, spec + ".MATREF", "resolved", "=3/4", "SOLI", "=3/3");
        Row(result, spec + ".FLUREF", "resolved", "=3/5", "FLUI", "=3/3");
        Check(Equals(Limit(result)["used"], Rows(result).Count) && Equals(Limit(result)["reached"], false), "default budget");
        Check(!TraceFixture.Calls.Any(k => k.StartsWith("=1/21.get.") && k != "=1/21.get.SPRE"), "Design must only read SPREF");
        return TraceFixture.Calls.Count;
    }

    private static void Partial()
    {
        var context = TraceFixture.Reset();
        TraceFixture.Spco.Attributes["CATR"] = DbElement.Null;
        TraceFixture.Spco.Attributes["PRTREF"] = null;
        TraceFixture.Spco.Attributes["CMPR"] = new DbElement(2, 30, DbType.Catalogue, "CMPD");
        TraceFixture.Overrides["=3/5.valid"] = false;
        TraceFixture.Overrides["=2/7.get.MATR"] = new InvalidOperationException("not for display");
        var result = context.ReadParagonReferenceTrace("=1/21");
        Check(Equals(result["status"], "partial"), "loss of evidence must make the result partial");
        Row(result, "root.SPREF.CATREF", "unset");
        Row(result, "root.SPREF.PRTREF", "unresolved");
        Row(result, "root.SPREF.CMPREF", "out_of_scope", "=2/30", "CMPD");
        Row(result, "root.SPREF.OWNER.OWNER.OWNER.MATREF", "read_error");
        Row(result, "root.SPREF.OWNER.OWNER.OWNER.FLUREF", "unresolved", "=3/5");
        Check(!TraceFixture.Calls.Contains("=2/30.owner") && !TraceFixture.Calls.Contains("=3/5.type"), "stop on invalid or out of scope");

        context = TraceFixture.Reset();
        TraceFixture.Overrides["MATR.type"] = DbAttributeType.STRING;
        TraceFixture.Spec.Attributes["FLUR"] = TraceFixture.Material;
        result = context.ReadParagonReferenceTrace("=2/7");
        Row(result, "root.MATREF", "attribute_unavailable");
        Row(result, "root.FLUREF", "out_of_scope", "=3/4", "SOLI");
        Check(!TraceFixture.Calls.Contains("=2/7.get.MATR"), "do not read a text attribute as a DBREF");

        context = TraceFixture.Reset();
        TraceFixture.Overrides["=2/3.owner"] = new InvalidOperationException();
        result = context.ReadParagonReferenceTrace("=2/3");
        Row(result, "root", "owner_read_error", "=2/3", "CATE");
        Row(result, "root.OWNER", "owner_read_error");
        Check(Equals(result["status"], "partial"), "owner error is not missing owner");

        context = TraceFixture.Reset();
        TraceFixture.Cate.DirectOwner = null;
        result = context.ReadParagonReferenceTrace("=2/3");
        Row(result, "root.OWNER", "owner_missing");
    }

    private static void ScopeAndBudget()
    {
        var context = TraceFixture.Reset();
        foreach (string invalid in new[] { null, "", "CE", "/A1A/EB50", "=0/0", "=1/01", "=01/1", "=1/2\n",
            "=1/-1", "=2147483648/1", "=1/2147483648", "ILEAVE TUBE OF =1/21" })
            Throws<ArgumentException>(() => context.ReadParagonReferenceTrace(invalid));
        Throws<ArgumentOutOfRangeException>(() => context.ReadParagonReferenceTrace("=1/21", 0));
        Throws<ArgumentOutOfRangeException>(() => context.ReadParagonReferenceTrace("=1/21", 33));
        Check(TraceFixture.Dispatches == 0 && TraceFixture.Calls.Count == 0, "reject non-literals and bad limits before native reads");

        var result = context.ReadParagonReferenceTrace("=1/21", 1);
        Check(Equals(result["status"], "partial") && Rows(result).Count == 1 && Equals(Limit(result)["reached"], true), "cap before following");
        Check(!TraceFixture.Calls.Contains("SPRE.valid"), "cap must precede even the attribute definition read");
        result = context.ReadParagonReferenceTrace("=2/1", 1);
        Check(Equals(result["status"], "observed") && Equals(Limit(result)["reached"], false), "an exact fit is not truncated");

        context = TraceFixture.Reset();
        TraceFixture.Overrides["lookup.=1/99"] = TraceFixture.Design;
        result = context.ReadParagonReferenceTrace("=1/99");
        Row(result, "root", "reference_mismatch", "=1/99");
        Check(Equals(result["status"], "unavailable") && !TraceFixture.Calls.Contains("=1/21.type"), "no substitute root");

        result = context.ReadParagonReferenceTrace("=1/98");
        Row(result, "root", "unresolved", "=1/98");

        context = TraceFixture.Reset();
        TraceFixture.Sele.DirectOwner = TraceFixture.Spco.DirectOwner;
        result = context.ReadParagonReferenceTrace("=2/10");
        Row(result, "root.OWNER.OWNER.OWNER", "cycle", "=2/11", "SELE");

        context = TraceFixture.Reset();
        TraceFixture.Design.Attributes["SPRE"] = TraceFixture.Scom;
        result = context.ReadParagonReferenceTrace("=1/21");
        Row(result, "root.SPREF", "out_of_scope", "=2/4", "SCOM");
        TraceFixture.Overrides["=1/21.type.short"] = "NOZZ";
        result = context.ReadParagonReferenceTrace("=1/21");
        Row(result, "root.SPREF", "resolved", "=2/4", "SCOM", "=2/3");
        Check(!Rows(result).Keys.Any(path => path.EndsWith(".MATREF")), "a direct SCOM ref does not invent a SPEC");
    }

    private static void BoundaryAndBarrier(int sdkCalls)
    {
        var context = TraceFixture.Reset();
        TraceFixture.BusyInUi = true;
        Check(Equals(context.ReadParagonReferenceTrace("=1/21")["status"], "busy") && TraceFixture.Calls.Count == 0, "busy guard on the UI");

        context = TraceFixture.Reset();
        TraceFixture.Project = null;
        var result = context.ReadParagonReferenceTrace("=1/21");
        Check(Equals(result["status"], "context_unverified") && Rows(result).Count == 0 && Equals(Limit(result)["used"], 0), "missing before boundary does not read root");

        foreach (string boundary in new[] { "project", "mdb", "ce" })
        {
            context = TraceFixture.Reset();
            int count = 0;
            TraceFixture.OnRead = key =>
            {
                if (key != boundary || ++count != 2) return;
                if (key == "ce") TraceFixture.Overrides[key] = TraceFixture.Design;
                if (key == "mdb") TraceFixture.Overrides[key] = new MDB();
                if (key == "project") TraceFixture.Overrides[key] = new Project();
            };
            result = context.ReadParagonReferenceTrace("=1/21");
            Check(Equals(result["status"], "context_unverified") && Rows(result).Count == 0 && (int)Limit(result)["used"] > 0,
                "discard all identities on boundary change: " + boundary);
        }

        for (int injection = 1; injection <= sdkCalls; ++injection)
        {
            var faulted = TraceFixture.Reset();
            TraceFixture.OnRead = _ => { if (TraceFixture.Calls.Count == injection) faulted.Fault(); };
            Throws<NativeDatabaseFaultException>(() => faulted.ReadParagonReferenceTrace("=1/21"));
            Check(TraceFixture.Calls.Count == injection, "no further SDK read after a native fault");
            Throws<NativeDatabaseFaultException>(() => faulted.ReadParagonReferenceTrace("=1/21"));
            Check(TraceFixture.Dispatches == 1 && TraceFixture.Calls.Count == injection, "preexisting fault must stop dispatch");
        }
    }

    private static int Main()
    {
        int sdkCalls = Full();
        Partial(); ScopeAndBudget(); BoundaryAndBarrier(sdkCalls);
        Console.WriteLine($"Reference trace checks passed ({sdkCalls} native fault injection points).");
        return 0;
    }
}
