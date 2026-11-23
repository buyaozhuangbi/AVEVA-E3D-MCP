// Only managed recording fakes execute. The production reader and guards are linked unchanged.
using System;
using System.Collections.Generic;
using System.Linq;
using Aveva.Core.Database;
using E3d21AiBridge;
using Newtonsoft.Json;

namespace Aveva.Core.Database
{
    public enum DbType { Design = 1, Catalog = 2, Catalogue = 2, Property = 4, Draft = 7 }
    public enum DbAttributeType { STRING, ELEMENT }

    public sealed class Project
    {
        public static Project CurrentProject => TraceFixture.Read("project", TraceFixture.Project);
        public string Name => TraceFixture.Read("project.name", "fixture-project");
        public static bool operator ==(Project a, Project b) { TraceFixture.Forbidden("Project =="); return false; }
        public static bool operator !=(Project a, Project b) { TraceFixture.Forbidden("Project !="); return false; }
        public override bool Equals(object value) => ReferenceEquals(this, value);
        public override int GetHashCode() => base.GetHashCode();
    }

    public sealed class MDB
    {
        public static MDB CurrentMDB => TraceFixture.Read("mdb", TraceFixture.Mdb);
        public string Name => TraceFixture.Read("mdb.name", "fixture-mdb");
        public static bool operator ==(MDB a, MDB b) { TraceFixture.Forbidden("MDB =="); return false; }
        public static bool operator !=(MDB a, MDB b) { TraceFixture.Forbidden("MDB !="); return false; }
        public override bool Equals(object value) => ReferenceEquals(this, value);
        public override int GetHashCode() => base.GetHashCode();
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
        public static bool operator ==(DbAttribute a, DbAttribute b) { TraceFixture.Forbidden("DbAttribute =="); return false; }
        public static bool operator !=(DbAttribute a, DbAttribute b) { TraceFixture.Forbidden("DbAttribute !="); return false; }
        public override bool Equals(object value) => ReferenceEquals(this, value);
        public override int GetHashCode() => base.GetHashCode();
    }

    public static class DbAttributeInstance
    {
        private static readonly Dictionary<string, DbAttribute> Definitions = new Dictionary<string, DbAttribute>();
        private static DbAttribute Get(string key)
        {
            if (!Definitions.TryGetValue(key, out var value)) Definitions[key] = value = new DbAttribute(key);
            return TraceFixture.Read("attribute." + key, value);
        }
        public static DbAttribute SPRE => Get("SPRE");
        public static DbAttribute CATR => Get("CATR");
        public static DbAttribute PRTREF => Get("PRTREF");
        public static DbAttribute CMPR => Get("CMPR");
        public static DbAttribute MATR => Get("MATR");
        public static DbAttribute FLUR => Get("FLUR");
    }

    public sealed class DbElementType
    {
        private readonly DbElement element;
        internal DbElementType(DbElement element) { this.element = element; }
        public bool IsValid => TraceFixture.Read(element.Key + ".type.valid", true);
        public string ShortName => TraceFixture.Read(element.Key + ".type.short", element.Noun);
        public static bool operator ==(DbElementType a, DbElementType b) { TraceFixture.Forbidden("DbElementType =="); return false; }
        public static bool operator !=(DbElementType a, DbElementType b) { TraceFixture.Forbidden("DbElementType !="); return false; }
        public override bool Equals(object value) => ReferenceEquals(this, value);
        public override int GetHashCode() => base.GetHashCode();
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
        public static bool operator ==(DbElement a, DbElement b) { TraceFixture.Forbidden("DbElement =="); return false; }
        public static bool operator !=(DbElement a, DbElement b) { TraceFixture.Forbidden("DbElement !="); return false; }
        public override bool Equals(object value) => ReferenceEquals(this, value);
        public override int GetHashCode() => base.GetHashCode();
        public string Name { get { TraceFixture.Forbidden("Element.Name must not be invented in a fixed DBREF trace"); return null; } }
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
        private readonly Action<Action> invokeOnUi;
        public E3d21Context()
        {
            invokeOnUi = action =>
            {
                if (TraceFixture.BusyInUi) commandActive = true;
                TraceFixture.Dispatches++;
                TraceFixture.UiDepth++;
                try { action(); }
                finally { TraceFixture.UiDepth--; }
            };
        }
        private bool NativeDispatchBusy() => TraceFixture.Busy;
        private object CurrentElement() => null; // An unqualified CE reference must fail compilation.
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
    internal static int UiDepth, Dispatches, ForbiddenCalls;
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

    internal static void Forbidden(string operation)
    { ForbiddenCalls++; throw new InvalidOperationException("Forbidden implicit SDK operation: " + operation); }

    private static DbElement Add(int dbno, int refno, DbType db, string type, DbElement owner = null)
    {
        var element = new DbElement(dbno, refno, db, type) { DirectOwner = owner };
        Elements.Add(element.Key, element);
        return element;
    }

    internal static E3d21Context Reset()
    {
        Elements.Clear(); Calls.Clear(); Overrides.Clear();
        Project = new Project(); Mdb = new MDB(); OnRead = null; UiDepth = Dispatches = ForbiddenCalls = 0; Busy = BusyInUi = false;
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
    private const string Session = "reference-trace-native-fixture";
    private static readonly string Build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
    private static int assertions, receipts;

    private static Dictionary<string, object> Trace(this E3d21Context context, string refno,
        int traceLimit = ParagonReferenceTraceRequest.DefaultTraceLimit)
    {
        var result = (Dictionary<string, object>)((IE3d21ParagonReferenceTraceContext)context).ReadParagonReferenceTrace(refno, traceLimit, Session);
        Check(TraceFixture.ForbiddenCalls == 0, "no overloaded SDK equality, NAME, or other implicit read");
        Check(result.Keys.OrderBy(key => key).SequenceEqual(new[] { "context", "db_type_codes", "status", "trace", "trace_limit" }), "exact production result fields");
        Check(DbCodes(result).Keys.All(path => Rows(result).ContainsKey(path)), "only published rows may have SDK type observations");
        var boundary = Context(result);
        Check(boundary.Count == 5, "exact context fields");
        foreach (string sampleKey in new[] { "before", "after" })
        {
            var sample = boundary[sampleKey] as Dictionary<string, object>;
            if (sample == null) continue;
            Check(sample.Count == 5 && Equals(sample["session_id"], Session) && Equals(sample["bridge_build"], Build), "server-owned session/MVID sample binding");
            var ce = sample["current_element"] as Dictionary<string, object>;
            Check(ce == null || ce.Count == 3 && ce["refno"] is string && ce["type"] is string && ce["db_type_code"] is int, "all-or-unknown typed CE");
        }
        receipts++;
        Console.WriteLine("Reference trace receipt: " + JsonConvert.SerializeObject(new
        {
            payload = new { refno, trace_limit = traceLimit },
            reply = new { ok = true, version = "E3D21", session_id = Session, bridge_build = Build, result }
        }));
        return result;
    }

    private static void Check(bool condition, string message)
    { assertions++; if (!condition) throw new Exception(message); }

    private static void Throws<T>(Action call) where T : Exception
    { try { call(); } catch (T) { return; } throw new Exception("Expected " + typeof(T).Name); }

    private static Dictionary<string, Dictionary<string, object>> Rows(Dictionary<string, object> result)
        => (Dictionary<string, Dictionary<string, object>>)result["trace"];
    private static Dictionary<string, object> Limit(Dictionary<string, object> result)
        => (Dictionary<string, object>)result["trace_limit"];
    private static Dictionary<string, object> Context(Dictionary<string, object> result)
        => (Dictionary<string, object>)result["context"];
    private static Dictionary<string, int> DbCodes(Dictionary<string, object> result)
        => (Dictionary<string, int>)result["db_type_codes"];
    private static void Row(Dictionary<string, object> result, string path, string status, string reference = null, string kind = null, string owner = null)
    {
        Check(Rows(result).TryGetValue(path, out var row), "missing path: " + path);
        Check(row.Count == 4 && Equals(row["ref"], reference) && Equals(row["type"], kind) &&
            Equals(row["owner"], owner) && Equals(row["status"], status), "incorrect identity/status for " + path);
    }

    private static int Full()
    {
        var context = TraceFixture.Reset();
        var result = context.Trace("=1/21");
        Check(result.Count == 5 && Equals(result["status"], "observed"), "result must contain the exact trace, budget, status, context and SDK code map");
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
        var result = context.Trace("=1/21");
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
        result = context.Trace("=2/7");
        Row(result, "root.MATREF", "attribute_unavailable");
        Row(result, "root.FLUREF", "out_of_scope", "=3/4", "SOLI");
        Check(!TraceFixture.Calls.Contains("=2/7.get.MATR"), "do not read a text attribute as a DBREF");

        context = TraceFixture.Reset();
        TraceFixture.Overrides["=2/3.owner"] = new InvalidOperationException();
        result = context.Trace("=2/3");
        Row(result, "root", "owner_read_error", "=2/3", "CATE");
        Row(result, "root.OWNER", "owner_read_error");
        Check(Equals(result["status"], "partial"), "owner error is not missing owner");

        context = TraceFixture.Reset();
        TraceFixture.Cate.DirectOwner = null;
        result = context.Trace("=2/3");
        Row(result, "root.OWNER", "owner_missing");
    }

    private static void ScopeAndBudget()
    {
        var context = TraceFixture.Reset();
        foreach (string invalid in new[] { null, "", "CE", "/A1A/EB50", "=0/0", "=1/01", "=01/1", "=1/2\n",
            "=1/-1", "=2147483648/1", "=1/2147483648", "ILEAVE TUBE OF =1/21" })
            Throws<ArgumentException>(() => context.Trace(invalid));
        Throws<ArgumentOutOfRangeException>(() => context.Trace("=1/21", 0));
        Throws<ArgumentOutOfRangeException>(() => context.Trace("=1/21", 33));
        Check(TraceFixture.Dispatches == 0 && TraceFixture.Calls.Count == 0, "reject non-literals and bad limits before native reads");

        var result = context.Trace("=1/21", 1);
        Check(Equals(result["status"], "partial") && Rows(result).Count == 1 && Equals(Limit(result)["reached"], true), "cap before following");
        Check(!TraceFixture.Calls.Contains("SPRE.valid"), "cap must precede even the attribute definition read");
        result = context.Trace("=2/1", 1);
        Check(Equals(result["status"], "observed") && Equals(Limit(result)["reached"], false), "an exact fit is not truncated");

        context = TraceFixture.Reset();
        TraceFixture.Overrides["lookup.=1/99"] = TraceFixture.Design;
        result = context.Trace("=1/99");
        Row(result, "root", "reference_mismatch", "=1/99");
        Check(Equals(result["status"], "unavailable") && !TraceFixture.Calls.Contains("=1/21.type"), "no substitute root");

        result = context.Trace("=1/98");
        Row(result, "root", "unresolved", "=1/98");

        context = TraceFixture.Reset();
        TraceFixture.Sele.DirectOwner = TraceFixture.Spco.DirectOwner;
        result = context.Trace("=2/10");
        Row(result, "root.OWNER.OWNER.OWNER", "cycle", "=2/11", "SELE");

        context = TraceFixture.Reset();
        TraceFixture.Design.Attributes["SPRE"] = TraceFixture.Scom;
        result = context.Trace("=1/21");
        Row(result, "root.SPREF", "out_of_scope", "=2/4", "SCOM");
        TraceFixture.Overrides["=1/21.type.short"] = "NOZZ";
        result = context.Trace("=1/21");
        Row(result, "root.SPREF", "resolved", "=2/4", "SCOM", "=2/3");
        Check(!Rows(result).Keys.Any(path => path.EndsWith(".MATREF")), "a direct SCOM ref does not invent a SPEC");
    }

    private static void BoundaryAndBarrier(int sdkCalls)
    {
        var context = TraceFixture.Reset();
        TraceFixture.Busy = true;
        Check(Equals(context.Trace("=1/21")["status"], "busy") && TraceFixture.Calls.Count == 0 && TraceFixture.Dispatches == 0, "outer busy guard does not dispatch or call SDK");

        context = TraceFixture.Reset();
        TraceFixture.BusyInUi = true;
        Check(Equals(context.Trace("=1/21")["status"], "busy") && TraceFixture.Calls.Count == 0, "busy guard on the UI");

        context = TraceFixture.Reset();
        TraceFixture.Project = null;
        var result = context.Trace("=1/21");
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
            result = context.Trace("=1/21");
            Check(Equals(result["status"], "context_unverified") && Rows(result).Count == 0 && (int)Limit(result)["used"] > 0,
                "discard all identities on boundary change: " + boundary);
        }

        for (int mode = 0; mode < 2; mode++)
        for (int injection = 1; injection <= sdkCalls; ++injection)
        {
            var faulted = TraceFixture.Reset();
            TraceFixture.OnRead = key =>
            {
                if (TraceFixture.Calls.Count != injection) return;
                faulted.Fault();
                if (mode == 1) TraceFixture.Overrides[key] = new InvalidOperationException("Ordinary SDK exception accompanied by a native fault");
            };
            Throws<NativeDatabaseFaultException>(() => faulted.Trace("=1/21"));
            Check(TraceFixture.Calls.Count == injection, "no further SDK read after a native fault");
            TraceFixture.Busy = true;
            Throws<NativeDatabaseFaultException>(() => faulted.Trace("=1/21"));
            Check(TraceFixture.Dispatches == 1 && TraceFixture.Calls.Count == injection, "preexisting fault must stop dispatch");
            Check(TraceFixture.ForbiddenCalls == 0, "native fault must never enter an implicit SDK equality read");
        }
        Console.WriteLine($"Reference trace native fault matrix: {sdkCalls} read positions / {sdkCalls * 2} return-or-throw cases");
    }

    private static void RequestAndErrors()
    {
        var request = ParagonReferenceTraceRequest.Validate("=2147483647/2147483647", 32);
        Check(request.Refno == "=2147483647/2147483647" && request.TraceLimit == 32 && request.Pair.SequenceEqual(new[] { int.MaxValue, int.MaxValue }), "canonical Int32 maximum");
        Check(ParagonReferenceTraceRequest.DefaultTraceLimit == 24 && ParagonReferenceTraceRequest.MaximumTraceLimit == 32, "frozen defaults");
        Check(ParagonReferenceTraceRequest.Validate("=1/0", 1).Pair.SequenceEqual(new[] { 1, 0 }), "element zero is legal input");
        var context = TraceFixture.Reset();
        foreach (string invalid in new[] { " =1/21", "=1/21 ", "=+1/21", "=1/+21", "=1/000", "=1/21\r", "=１/21", "=1/２１", "=1/1\0", "=1/1/2", new string('9', 10000) })
            Throws<ArgumentException>(() => context.Trace(invalid));
        foreach (string session in new[] { null, "", " ", "x\n", "x\u200b", "x\ud800", "x\ue000", "x\udb80\udc00", "x\udbc0\udc00", new string('x', 257), string.Concat(Enumerable.Repeat("\ud83d\ude00", 129)) })
            Throws<ArgumentException>(() => context.ReadParagonReferenceTrace("=1/21", 24, session));
        Check(TraceFixture.Calls.Count == 0 && TraceFixture.Dispatches == 0, "bad literals and session identities rejected before dispatch");

        foreach (int prefix in new[] { 4090, 4091, 4092, 4093, 4094, 4095, 4096 })
        {
            string input = new string('x', prefix) + "\ud83d\ude00" + new string('z', 20);
            string text = ParagonReferenceTraceRequest.ErrorText(input);
            Check(text.Length <= 4096 && text.EndsWith("..."), "error text is bounded");
            Check(!text.Any(character => char.IsSurrogate(character)) || text.Contains("\ud83d\ude00"), "truncation cannot split a surrogate pair");
            for (int index = 0; index < text.Length; index++)
            {
                if (char.IsHighSurrogate(text[index])) Check(index + 1 < text.Length && char.IsLowSurrogate(text[++index]), "retained high surrogate has low partner");
                else Check(!char.IsLowSurrogate(text[index]), "no unpaired low surrogate");
            }
        }
        string exact = new string('x', 4094) + "\ud83d\ude00";
        Check(ParagonReferenceTraceRequest.ErrorText(exact) == exact, "4096 UTF16 units retained exactly");
        Check(ParagonReferenceTraceRequest.ErrorText(null).Length > 0, "null error has bounded fallback");
    }

    private static void TypedBoundaries()
    {
        var context = TraceFixture.Reset();
        var result = context.Trace("=2/1");
        var ce = (Dictionary<string, object>)((Dictionary<string, object>)Context(result)["before"])["current_element"];
        Check(Equals(ce["refno"], "=1/20") && Equals(ce["type"], "EQUI") && Equals(ce["db_type_code"], 1), "native typed CE");
        Check(Equals(Context(result)["boundary_agreement"], true) && Equals(Context(result)["same_project_instance"], true) && Equals(Context(result)["same_mdb_instance"], true), "same original SDK instances");

        context = TraceFixture.Reset(); TraceFixture.Ce = DbElement.Null;
        result = context.Trace("=2/1");
        Check(Equals(result["status"], "observed") && ((Dictionary<string, object>)Context(result)["before"])["current_element"] == null, "verified native null CE is distinct from unread CE");
        Check(!TraceFixture.Calls.Contains("=0/0.type") && !TraceFixture.Calls.Contains("=0/0.db"), "native null CE has no invented type/code reads");

        foreach (string field in new[] { "=1/20.type.short", "=1/20.db", "project.name", "mdb.name" })
        {
            context = TraceFixture.Reset();
            int count = 0;
            TraceFixture.OnRead = key =>
            {
                if (key != field || ++count != 2) return;
                TraceFixture.Overrides[key] = field == "=1/20.db" ? (object)DbType.Catalogue : "CHANGED";
            };
            result = context.Trace("=1/21");
            Check(Equals(result["status"], "context_unverified") && Rows(result).Count == 0 && DbCodes(result).Count == 0, "suppress graph on typed or named boundary drift: " + field);
            Check((int)Limit(result)["used"] > 0 && Equals(Context(result)["same_project_instance"], true) && Equals(Context(result)["same_mdb_instance"], true) && Equals(Context(result)["boundary_agreement"], false), "typed CE/name drift does not disguise stable SDK instances");
        }

        foreach (string field in new[] { "project", "mdb", "project.name", "mdb.name", "ce", "=1/20.null", "=1/20.ref", "=1/20.valid", "=1/20.deleted", "=1/20.type", "=1/20.type.valid", "=1/20.type.short", "=1/20.db" })
        for (int failureSample = 1; failureSample <= 2; failureSample++)
        {
            context = TraceFixture.Reset();
            int count = 0;
            TraceFixture.OnRead = key =>
            {
                if (key == field && ++count == failureSample) TraceFixture.Overrides[key] = new InvalidOperationException("private boundary read failure");
            };
            result = context.Trace("=1/21");
            Check(Equals(result["status"], "context_unverified"), "ordinary failed boundary has no graph evidence");
            Check(Context(result)["after"] == null && Context(result)["same_project_instance"] == null && Context(result)["same_mdb_instance"] == null && Context(result)["boundary_agreement"] == null, "failed sample has unknown comparisons");
            Check((failureSample == 1) == (Context(result)["before"] == null) && (failureSample == 1) == Equals(Limit(result)["used"], 0), "before failure stops root and after sampling; after failure preserves attempted count");
            Check(!JsonConvert.SerializeObject(result).Contains("private boundary"), "raw ordinary exception text is not added to frozen trace shape");
        }

        foreach (object invalid in new object[] { null, "", " ", new string('x', 257), "x\n", "x\u00a0", "x\u200b", "x\ud800" })
        {
            context = TraceFixture.Reset(); TraceFixture.Overrides["project.name"] = invalid;
            result = context.Trace("=2/1");
            Check(Context(result)["before"] == null && Equals(Limit(result)["used"], 0), "invalid native name leaves boundary unknown");
        }
        foreach (string field in new[] { "project.name", "mdb.name" })
        foreach (string suffix in new[] { "\ue000", "\udb80\udc00", "\udbc0\udc00" })
        for (int sample = 1; sample <= 2; sample++)
        {
            context = TraceFixture.Reset();
            int nameReads = 0;
            TraceFixture.OnRead = key =>
            {
                if (key == field && ++nameReads == sample) TraceFixture.Overrides[key] = "fixture" + suffix;
            };
            result = context.Trace("=1/21", 1);
            Check(Equals(result["status"], "context_unverified") && Rows(result).Count == 0 && DbCodes(result).Count == 0, "private-use native names cannot publish graph evidence");
            Check((Context(result)["before"] == null) == (sample == 1) && Context(result)["after"] == null, "private-use boundary sample is entirely unknown");
            Check(Context(result)["same_project_instance"] == null && Context(result)["same_mdb_instance"] == null && Context(result)["boundary_agreement"] == null, "private-use sample cannot provide instance comparisons");
            Check(Equals(Limit(result)["used"], sample == 1 ? 0 : 1) && Equals(Limit(result)["reached"], sample == 2), "private-use after sample preserves attempted count and truncation");
            Check(nameReads == sample && TraceFixture.Calls.Count(key => key.StartsWith("lookup.")) == (sample == 1 ? 0 : 1), "private-use before sample stops root/after reads and failed after sample is not retried");
        }
        foreach (string field in new[] { "project.name", "mdb.name" })
        foreach (string name in new[] { "工程", "\ud83d\ude00", "工程 \ud83d\ude00" })
        {
            context = TraceFixture.Reset(); TraceFixture.Overrides[field] = name;
            result = context.Trace("=2/1");
            Check(Equals(result["status"], "observed"), "bounded printable CJK and emoji names remain observed");
        }
        context = TraceFixture.Reset(); TraceFixture.Ce = null;
        result = context.Trace("=2/1");
        Check(Context(result)["before"] == null, "null SDK object is an unread CE");

        context = TraceFixture.Reset();
        int ceTypeReads = 0;
        TraceFixture.OnRead = key =>
        {
            if (key == "=1/20.type.short" && ++ceTypeReads == 2) TraceFixture.Overrides[key] = "PIPE";
        };
        result = context.Trace("=1/21", 1);
        Check(Equals(result["status"], "context_unverified") && Equals(Limit(result)["used"], 1) && Equals(Limit(result)["reached"], true), "lost boundary preserves attempted budget and truncation instead of upgrading completeness");
        Check(Rows(result).Count == 0 && DbCodes(result).Count == 0, "lost truncated trace suppresses every graph identity and raw code");

        context = TraceFixture.Reset(); TraceFixture.Busy = true; context.Fault();
        Throws<NativeDatabaseFaultException>(() => context.Trace("=1/21"));
        Check(TraceFixture.Calls.Count == 0 && TraceFixture.Dispatches == 0, "preexisting fault wins over busy without any SDK work");
    }

    private static void OrdinaryFailures(int sdkCalls)
    {
        for (int injection = 1; injection <= sdkCalls; ++injection)
        {
            var context = TraceFixture.Reset();
            TraceFixture.OnRead = key =>
            {
                if (TraceFixture.Calls.Count == injection) TraceFixture.Overrides[key] = new InvalidOperationException("private ordinary SDK failure");
            };
            var result = context.Trace("=1/21");
            Check(!Equals(result["status"], "observed"), "every attempted ordinary getter failure retains uncertainty");
            Check(!JsonConvert.SerializeObject(result).Contains("private ordinary"), "ordinary details do not change the frozen row contract");
        }
        Console.WriteLine($"Reference trace ordinary exception matrix: {sdkCalls} production receipts");
    }

    private static void FixedGraphAlternatives()
    {
        foreach (string noun in new[] { "SPRF", "SFIT", "JOIN" })
        {
            var context = TraceFixture.Reset();
            var stse = new DbElement(2, 51, DbType.Catalogue, "STSE") { DirectOwner = TraceFixture.Cata };
            var stca = new DbElement(2, 52, DbType.Catalogue, "STCA") { DirectOwner = stse };
            TraceFixture.Overrides["lookup.=2/53"] = new DbElement(2, 53, DbType.Catalogue, noun) { DirectOwner = stca };
            var result = context.Trace("=2/53");
            Row(result, "root", "resolved", "=2/53", noun, "=2/52");
            Row(result, "root.OWNER.OWNER.OWNER", "resolved", "=2/1", "CATA", "=2/0");
            Check(Equals(result["status"], "observed") && DbCodes(result).Values.All(code => code == 2), "fixed structural component owner chain");
        }
        foreach (string noun in new[] { "SOLI", "FLUI", "CMPD", "TUBD", "CABD" })
        {
            var context = TraceFixture.Reset();
            TraceFixture.Overrides["lookup.=3/53"] = new DbElement(3, 53, DbType.Property, noun);
            var result = context.Trace("=3/53");
            Check(Equals(result["status"], "observed") && Rows(result).Count == 1 && DbCodes(result)["root"] == 4, "property roots do not expand arbitrary references");
        }
        foreach (int raw in new[] { 7, int.MinValue, int.MaxValue })
        {
            var context = TraceFixture.Reset();
            TraceFixture.Overrides["lookup.=5/53"] = new DbElement(5, 53, (DbType)raw, "CATA");
            var result = context.Trace("=5/53");
            Row(result, "root", "out_of_scope", "=5/53", "CATA");
            Check(DbCodes(result)["root"] == raw, "raw SDK Int32 database type is never replaced with a semantic guess");
        }
        foreach (string noun in new[] { "NOZZ", "ELCONN" })
        {
            var context = TraceFixture.Reset(); TraceFixture.Design.Attributes["SPRE"] = TraceFixture.Scom;
            TraceFixture.Overrides["=1/21.type.short"] = noun;
            var result = context.Trace("=1/21");
            Check(Equals(result["status"], "observed") && Equals(Rows(result)["root.SPREF"]["type"], "SCOM"), "only fixed Design nouns allow direct SCOM");
        }
        {
            var context = TraceFixture.Reset();
            DbElement chain = TraceFixture.Spec;
            for (int no = 100; no < 140; no++) chain = new DbElement(2, no, DbType.Catalogue, "SELE") { DirectOwner = chain };
            TraceFixture.Spco.DirectOwner = chain;
            var result = context.Trace("=2/10", 32);
            Check(Equals(result["status"], "partial") && Rows(result).Count == 32 && Equals(Limit(result)["reached"], true), "maximum budget caps a long fixed owner chain");
            Check(!TraceFixture.Calls.Any(key => key.StartsWith("attribute.MATR")), "truncated owner chain does not reach unvisited SPEC attributes");
        }
        {
            var context = TraceFixture.Reset();
            var result = context.Trace("=2/0");
            Row(result, "root", "out_of_scope", "=2/0", "WORL");
            Check(Equals(result["status"], "unavailable"), "legal element-zero input does not bypass root scope");
        }
    }

    private static int Main()
    {
        int sdkCalls = Full();
        Partial(); ScopeAndBudget(); RequestAndErrors(); TypedBoundaries(); BoundaryAndBarrier(sdkCalls);
        OrdinaryFailures(sdkCalls); FixedGraphAlternatives();
        Console.WriteLine($"Reference trace production checks passed: {assertions}; {receipts} receipts; no SDK assembly loaded.");
        return 0;
    }
}
