using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Aveva.Core.Database;
using E3d21AiBridge;

// Managed fakes expose only the read surface consumed by the production reader.
// No AVEVA assembly, native runtime, PML engine, Admin initializer or file API.
namespace Aveva.Core.Database
{
    public enum DbType { Design = 1, Catalogue = 2, System = 10, Global = 11 }
    public sealed class Project
    {
        public static bool operator ==(Project left, Project right) => Scenario.ForbiddenOperator();
        public static bool operator !=(Project left, Project right) => Scenario.ForbiddenOperator();
        public override bool Equals(object other) => ReferenceEquals(this, other);
        public override int GetHashCode() => System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(this);
        internal static Project Current;
        internal string Value = "PROJECT";
        public static Project CurrentProject => Scenario.Read("project.current", Current);
        public string Name => Scenario.Read("project.name", Value);
    }
    public sealed class MDB
    {
        public static bool operator ==(MDB left, MDB right) => Scenario.ForbiddenOperator();
        public static bool operator !=(MDB left, MDB right) => Scenario.ForbiddenOperator();
        public override bool Equals(object other) => ReferenceEquals(this, other);
        public override int GetHashCode() => System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(this);
        internal static MDB Current;
        internal string Value = "MDB";
        internal Db[] Databases;
        public static MDB CurrentMDB => Scenario.Read("mdb.current", Current);
        public string Name => Scenario.Read("mdb.name", Value);
        public Db[] GetDBArray() => Scenario.Read("mdb.array", Databases);
    }
    public sealed class Db
    {
        public static bool operator ==(Db left, Db right) => Scenario.ForbiddenOperator();
        public static bool operator !=(Db left, Db right) => Scenario.ForbiddenOperator();
        public override bool Equals(object other) => ReferenceEquals(this, other);
        public override int GetHashCode() => System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(this);
        internal readonly string Path;
        internal string Value;
        internal int NumberValue, ExtractValue;
        internal bool ExtractFlag;
        internal Db(string path, string name, int number, int extract, bool flag)
        { Path = path; Value = name; NumberValue = number; ExtractValue = extract; ExtractFlag = flag; }
        public string Name => Scenario.Read(Path + ".name", Value);
        public int Number => Scenario.Read(Path + ".number", NumberValue);
        public int ExtractNumber => Scenario.Read(Path + ".extract", ExtractValue);
        public bool IsExtract => Scenario.Read(Path + ".is_extract", ExtractFlag);
    }
    public sealed class DbElementType
    {
        public static bool operator ==(DbElementType left, DbElementType right) => Scenario.ForbiddenOperator();
        public static bool operator !=(DbElementType left, DbElementType right) => Scenario.ForbiddenOperator();
        public override bool Equals(object other) => ReferenceEquals(this, other);
        public override int GetHashCode() => System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(this);
        internal readonly string Value;
        internal DbElementType(string value) { Value = value; }
        public bool IsValid => Scenario.Read("type." + Value + ".valid", true);
        public string ShortName => Scenario.Read("type." + Value + ".name", Value);
    }
    public static class DbElementTypeInstance
    {
        public static readonly DbElementType TEAM = new DbElementType("TEAM"), DB = new DbElementType("DB");
    }
    public sealed class DbAttribute
    {
        public static bool operator ==(DbAttribute left, DbAttribute right) => Scenario.ForbiddenOperator();
        public static bool operator !=(DbAttribute left, DbAttribute right) => Scenario.ForbiddenOperator();
        public override bool Equals(object other) => ReferenceEquals(this, other);
        public override int GetHashCode() => System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(this);
        internal readonly string Value;
        internal DbAttribute(string value) { Value = value; }
    }
    public static class DbAttributeInstance
    {
        public static readonly DbAttribute NAMN = new DbAttribute("NAMN"), NAMEDB = new DbAttribute("NAMEDB"),
            DBNO = new DbAttribute("DBNO"), FINO = new DbAttribute("FINO"), EXTNO = new DbAttribute("EXTNO"),
            EXTOWN = new DbAttribute("EXTOWN");
    }
    public sealed class DbElement
    {
        public static bool operator ==(DbElement left, DbElement right) => Scenario.ForbiddenOperator();
        public static bool operator !=(DbElement left, DbElement right) => Scenario.ForbiddenOperator();
        public override bool Equals(object other) => ReferenceEquals(this, other);
        public override int GetHashCode() => System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(this);
        internal readonly string Path;
        internal int[] Reference;
        internal string TypeValue, Name;
        internal DbType Storage = DbType.System;
        internal int Dbno, Fino, Extno;
        internal DbElement OwnerValue, ExtractOwner;
        internal bool Null, Valid = true, Deleted;
        internal DbElement(string path, int number, string type, string name = null)
        { Path = path; Reference = new[] { 5, number }; TypeValue = type; Name = name; }
        public bool IsNull => Scenario.Read(Path + ".null", Null);
        public bool IsValid => Scenario.Read(Path + ".valid", Valid);
        public bool IsDeleted => Scenario.Read(Path + ".deleted", Deleted);
        public int[] Ref => Scenario.Read(Path + ".ref", Reference);
        public DbType DbType => Scenario.Read(Path + ".storage", Storage);
        public DbElementType GetElementType() => Scenario.Read(Path + ".type", new DbElementType(TypeValue));
        public DbElement Owner => Scenario.Read(Path + ".owner", OwnerValue);
        public bool GetValidString(DbAttribute attribute, ref string value)
        {
            value = Scenario.Read(Path + "." + attribute.Value, Name);
            return Scenario.Available(Path + "." + attribute.Value);
        }
        public bool GetValidInteger(DbAttribute attribute, ref int value)
        {
            int fallback = ReferenceEquals(attribute, DbAttributeInstance.DBNO) ? Dbno : ReferenceEquals(attribute, DbAttributeInstance.FINO) ? Fino : Extno;
            value = Scenario.Read(Path + "." + attribute.Value, fallback);
            return Scenario.Available(Path + "." + attribute.Value);
        }
        public bool GetValidRef(DbAttribute attribute, ref DbElement value)
        {
            if (!ReferenceEquals(attribute, DbAttributeInstance.EXTOWN)) throw new Exception("Unexpected reference read.");
            value = Scenario.Read(Path + ".EXTOWN", ExtractOwner);
            return Scenario.Available(Path + ".EXTOWN");
        }
    }
    public sealed class DbSystem
    {
        public static bool operator ==(DbSystem left, DbSystem right) => Scenario.ForbiddenOperator();
        public static bool operator !=(DbSystem left, DbSystem right) => Scenario.ForbiddenOperator();
        public override bool Equals(object other) => ReferenceEquals(this, other);
        public override int GetHashCode() => System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(this);
        private static readonly DbSystem instance = new DbSystem();
        public static DbSystem Instance => Scenario.Read("system.instance", instance);
        internal static DbElement[] Teams, Records;
        public DbElement[] GetAll(DbElementType type)
        {
            if (ReferenceEquals(type, DbElementTypeInstance.TEAM)) return Scenario.Read("teams.array", Teams);
            if (ReferenceEquals(type, DbElementTypeInstance.DB)) return Scenario.Read("records.array", Records);
            throw new Exception("Only explicit TEAM/DB reads are permitted.");
        }
    }
    public static class CurrentElement
    {
        public static DbElement Element => Scenario.Read("ce.element", Scenario.Ce);
    }
}

internal static class Scenario
{
    internal static readonly List<string> Reads = new List<string>();
    internal static readonly Dictionary<string, object> Overrides = new Dictionary<string, object>();
    internal static E3d21Context Context;
    internal static Action<string> OnRead;
    internal static DbElement Ce, Team, OtherTeam, Dbli, Master, Working, OtherWorking;
    internal static int Dispatches, BusyChecks, BusyAt, FaultAt, ThrowAt, ReadsAfterFault;
    internal static bool InUi;
    internal static bool ForbiddenOperator() => throw new Exception("Forbidden implicit SDK equality operator.");
    internal static bool Available(string path) => !Overrides.TryGetValue(path + ".available", out object value) || (bool)value;
    internal static T Read<T>(string path, T fallback)
    {
        if (!InUi) throw new Exception("SDK read escaped the UI dispatch: " + path);
        if (Context.HasFault) { ReadsAfterFault++; throw new Exception("SDK read after fault: " + path); }
        Reads.Add(path);
        OnRead?.Invoke(path);
        if (Reads.Count == FaultAt) Context.Fault();
        if (Reads.Count == ThrowAt) throw new InvalidOperationException("Injected managed read failure.");
        if (!Overrides.TryGetValue(path, out object value)) return fallback;
        if (value is Exception error) throw error;
        return (T)value;
    }
    internal static E3d21Context Reset()
    {
        Reads.Clear(); Overrides.Clear(); OnRead = null; Dispatches = BusyChecks = BusyAt = FaultAt = ThrowAt = ReadsAfterFault = 0; InUi = false;
        Project.Current = new Project(); MDB.Current = new MDB();
        Ce = new DbElement("ce", 1, "EQUI") { Storage = DbType.Design };
        Team = new DbElement("team", 10, "TEAM", "/*PIPING");
        OtherTeam = new DbElement("other_team", 11, "TEAM", "/*CATALOGUE");
        Dbli = new DbElement("dbli", 20, "DBLI") { OwnerValue = Team };
        Master = new DbElement("master", 30, "DB", "/*PIPING/BASE") { Dbno = 77, Fino = 900, OwnerValue = Dbli };
        Working = new DbElement("working", 31, "DB", "PIPING/WORK")
        { Dbno = 77, Fino = 901, Extno = 1, ExtractOwner = Master, OwnerValue = Dbli };
        OtherWorking = new DbElement("other_working", 32, "DB", "/PIPING/OTHER")
        { Dbno = 77, Fino = 902, Extno = 2, ExtractOwner = Master, OwnerValue = Dbli };
        DbSystem.Teams = new[] { Team, OtherTeam };
        DbSystem.Records = new[] { Master, Working, OtherWorking };
        MDB.Current.Databases = new[] {
            new Db("mdb.other", "PIPING/OTHER", 77, 2, true),
            new Db("mdb.master", "PIPING/BASE", 77, 0, false),
            new Db("mdb.working", "PIPING/WORK", 77, 1, true)
        };
        return Context = new E3d21Context();
    }
}

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;
        private readonly Action<Action> invokeOnUi;
        internal E3d21Context()
        {
            invokeOnUi = action => { Scenario.Dispatches++; Scenario.InUi = true; try { action(); } finally { Scenario.InUi = false; } };
        }
        internal bool HasFault => nativeFaultState.HasFault;
        internal void Fault() => nativeFaultState.Observe("Unhandled Database error 538 from FakeDb", "");
        private bool IsCommandActive()
        {
            if (nativeFaultState.HasFault) throw new NativeDatabaseFaultException();
            return ++Scenario.BusyChecks == Scenario.BusyAt;
        }
        // The production context has this same-named member. An unqualified
        // CurrentElement.Element must therefore fail compilation here too.
        private static DbElement CurrentElement() => throw new Exception("Unexpected legacy CE helper.");
        // PRODUCTION_HELPERS
    }
}

internal static class TeamDatabaseIdentityManifestHarness
{
    private static int checks;
    private static void Check(bool value, string label) { checks++; if (!value) throw new Exception("Check failed: " + label); }
    private static Dictionary<string, object> Dict(object value) => (Dictionary<string, object>)value;
    private static List<Dictionary<string, object>> Rows(Dictionary<string, object> result, string key) => (List<Dictionary<string, object>>)result[key];
    private static Dictionary<string, object> Database(Dictionary<string, object> result, int index = 0) => Rows(result, "databases")[index];
    private static Dictionary<string, object> Relation(Dictionary<string, object> result, int index = 0) => Dict(Database(result, index)["team_relation"]);
    private static Dictionary<string, object> Match(Dictionary<string, object> result, int index = 0) => Dict(Database(result, index)["current_mdb"]);
    private static bool Candidates(Dictionary<string, object> result, int index, params int[] indexes) =>
        ((List<int>)Match(result, index)["candidate_indexes"]).SequenceEqual(indexes);
    private static string Expected()
    {
        using (var hash = SHA256.Create()) return string.Concat(hash.ComputeHash(Encoding.UTF8.GetBytes("SESSION\nPROJECT\nMDB")).Select(b => b.ToString("x2")));
    }
    private static Dictionary<string, object> Read(int limit = 64, string expected = null, string session = "SESSION")
    {
        string requestedContext = expected ?? Expected();
        var result = Dict(((IE3d21TeamDatabaseIdentityManifestContext)Scenario.Context)
            .ReadTeamDatabaseIdentityManifest(session, requestedContext, limit));
        Check(Rows(result, "databases").All(row => Equals(Dict(row["current_mdb"])["status"], "indeterminate") &&
            Dict(row["current_mdb"])["matched_index"] == null && Equals(Dict(row["current_mdb"])["semantic_equivalence_verified"], false)),
            "raw MDB candidates never certify System record equivalence");
        Check(Rows(result, "current_mdb").All(row => row["identity_coherent"] == null), "MDB number semantics remain unknown");
        Check(Equals(Dict(result["scope"])["extract_number_semantics_verified"], false) &&
            Equals(Dict(result["scope"])["mdb_record_join_verified"], false), "unverified numbering and join scope explicit");
        Console.WriteLine("MANIFEST_RECEIPT=" + Serialize(new Dictionary<string, object>
        {
            ["arguments"] = new Dictionary<string, object> { ["expected_context"] = requestedContext, ["limit"] = limit },
            ["reply"] = new Dictionary<string, object>
            {
                ["ok"] = true, ["version"] = "E3D21", ["session_id"] = "SESSION",
                ["bridge_build"] = typeof(E3d21Context).Module.ModuleVersionId.ToString("D"), ["result"] = result
            }
        }));
        return result;
    }
    private static bool HasError(Dictionary<string, object> result, string path) => ((List<object>)result["read_errors"])
        .Select(Dict).Any(error => Equals(error["path"], path));
    private static void Throws<T>(Action action, string label) where T : Exception
    {
        try { action(); } catch (T) { Check(true, label); return; }
        throw new Exception("Expected " + typeof(T).Name + ": " + label);
    }
    private static bool ValidSurrogates(string value)
    {
        for (int position = 0; position < value.Length; position++)
        {
            if (char.IsHighSurrogate(value[position]))
            {
                if (++position >= value.Length || !char.IsLowSurrogate(value[position])) return false;
            }
            else if (char.IsLowSurrogate(value[position])) return false;
        }
        return true;
    }

    private static void NormalAndExtracts()
    {
        Scenario.Reset(); var result = Read();
        Check(Equals(result["success"], true) && Equals(result["complete"], true), "complete normal observation");
        Check(Scenario.Dispatches == 1 && Scenario.BusyChecks == 2 && !Scenario.InUi, "one dispatch and busy guard");
        Check(Equals(result["atomic_snapshot"], false) && Equals(Dict(result["scope"])["project_coverage_verified"], false), "visibility does not assert atomicity or project coverage");
        Check(Equals(Dict(Relation(result)["confirmed_team"])["refno"], "=5/10"), "typed TEAM was not confused with direct DBLI owner");
        Check(Equals(Relation(result)["status"], "confirmed"), "name and typed ownership agree");
        Check(Candidates(result, 0, 1) && Candidates(result, 1, 2) && Candidates(result, 2, 0), "full raw tuple candidates preserved independent of array order");
        Check(Equals(Database(result, 0)["fino"], 900) && Equals(Database(result, 1)["extno"], 1), "FINO retained independently from EXTNO");
        var paths = (List<Dictionary<string, object>>)Database(result)["owner_path"];
        Check(paths.Count == 2 && Equals(paths[0]["type"], "DBLI") && Equals(paths[1]["type"], "TEAM"), "typed two-hop OWNER evidence");
        Check(Scenario.Reads.Count(path => path == "master.owner") == 1 && Scenario.Reads.Count(path => path == "dbli.owner") == 3, "each DB owner path read once without extra enumeration");
        Console.WriteLine("MANIFEST_NORMAL=" + Serialize(result));

        Scenario.Reset(); Scenario.Master.Fino = 1; Scenario.Working.Fino = 0; Scenario.OtherWorking.Fino = 77;
        result = Read(); Check(Candidates(result, 1, 2), "FINO values do not change raw tuple candidates");
        Scenario.Reset(); Scenario.Working.Extno = 2;
        result = Read(); Check(Candidates(result, 1), "same name and DBNO with different raw EXTNO is not a candidate");
        Scenario.Reset(); Scenario.Working.Name = "/*PIPING/BASE";
        result = Read(); Check(Candidates(result, 1), "same name/DBNO with different raw number is not a candidate");
        Scenario.Reset(); Scenario.Working.Name = "/*PIPING/BASE"; MDB.Current.Databases[2].Value = "PIPING/BASE";
        result = Read(); Check(Candidates(result, 0, 1) && Candidates(result, 1, 2), "same complete name and DBNO retains distinct raw numbers");
        Scenario.Reset(); Scenario.Overrides["working.EXTNO.available"] = false;
        result = Read(); Check(Database(result, 1)["extno"] == null && Equals(Match(result, 1)["status"], "indeterminate"), "missing EXTNO is unknown, never zero");
        Check(Equals(Match(result, 0)["status"], "indeterminate"), "unread extract identity may hide a duplicate");
        Scenario.Reset(); Scenario.Overrides["working.EXTOWN.available"] = false;
        result = Read(); Check(Equals(Database(result, 1)["extract_kind"], "unknown") && Equals(Match(result, 1)["status"], "indeterminate"), "missing EXTOWN is not master evidence");
        Scenario.Reset(); Scenario.Overrides["master.EXTOWN"] = new DbElement("null_owner", 0, "DB") { Null = true };
        result = Read(); Check(Equals(Database(result)["extract_owner_state"], "none"), "native null reference is explicit master candidate evidence");
        Scenario.Reset(); Scenario.Master.ExtractOwner = Scenario.Working;
        result = Read(); Check(Equals(Database(result)["extract_kind"], "extract_candidate") && Equals(result["identity_consistent"], true), "nonnull typed owner is candidate evidence independent of zero EXTNO");
        Scenario.Reset(); Scenario.Working.ExtractOwner = null;
        result = Read(); Check(Equals(Database(result, 1)["extract_kind"], "master_candidate") && Equals(result["identity_consistent"], true), "positive EXTNO with null owner does not imply extract or inconsistency");
        Scenario.Reset(); Scenario.Working.ExtractOwner = Scenario.Working;
        result = Read(); Check(Equals(Database(result, 1)["extract_kind"], "unknown") && Equals(result["identity_consistent"], true), "self owner preserves raw evidence without inventing numbering consistency");
        Scenario.Reset(); Scenario.Overrides["mdb.working.is_extract"] = false;
        result = Read(); Check(Equals(Rows(result, "current_mdb")[2]["extract_number"], 1) &&
            Equals(Rows(result, "current_mdb")[2]["is_extract"], false) && Equals(result["identity_consistent"], true) && Candidates(result, 1, 2),
            "primary ExtractNumber one with IsExtract false is a valid independent observation");
        Scenario.Reset(); Scenario.Working.Extno = 91; MDB.Current.Databases[2].ExtractValue = 91;
        result = Read(); Check(Equals(Database(result, 1)["extno"], 91) && Candidates(result, 1, 2), "unknown raw number semantics never cause remapping");
    }

    private static void OwnershipAndTypes()
    {
        foreach (string type in new[] { "TEAM", "EQUI", "DB" })
        {
            Scenario.Reset(); Scenario.Dbli.TypeValue = type;
            var result = Read();
            Check(Equals(Relation(result)["status"], "unverified") && Relation(result)["confirmed_team"] == null, "direct owner type " + type + " not treated as Team");
            Check(!Scenario.Reads.Contains("dbli.owner"), "unsupported path stops before second owner read");
        }
        Scenario.Reset(); Scenario.Dbli.OwnerValue = Scenario.OtherTeam;
        var conflict = Read();
        Check(Equals(Relation(conflict)["status"], "conflict") && Relation(conflict)["confirmed_team"] == null, "name prefix cannot override actual Team owner");
        Check(Equals(Dict(Relation(conflict)["observed_team"])["refno"], "=5/11"), "conflicting native ancestor retained");
        Console.WriteLine("MANIFEST_CONFLICT=" + Serialize(conflict));
        Scenario.Reset(); Scenario.Dbli.OwnerValue = new DbElement("fake_team", 99, "EQUI", "/*PIPING");
        var result2 = Read(); Check(Relation(result2)["confirmed_team"] == null, "Team name on wrong native type is insufficient");
        Scenario.Reset(); DbSystem.Teams = new[] { new DbElement("unrelated_team", 90, "TEAM", "/*PIPING") };
        result2 = Read(); Check(Relation(result2)["confirmed_team"] == null && Relation(result2)["listed_team_index"] == null, "same name with another typed Team reference cannot satisfy ownership");
        Scenario.Reset(); Scenario.Dbli.OwnerValue = Scenario.Master;
        result2 = Read(); Check(Relation(result2)["confirmed_team"] == null && HasError(result2, "databases[0].owner_path"), "typed cycle bounded and retained");
        Check(Scenario.Reads.Count(path => path == "master.owner") <= 2, "cycle never followed recursively");
        Scenario.Reset(); Scenario.Master.OwnerValue = null;
        result2 = Read(); Check(Relation(result2)["confirmed_team"] == null, "null owner never inferred from name");
        Scenario.Reset(); Scenario.Dbli.Reference = new[] { 6, 20 };
        result2 = Read(); Check(Relation(result2)["confirmed_team"] == null, "cross-database owner path rejected");
        Scenario.Reset(); Scenario.Dbli.Storage = DbType.Global;
        result2 = Read(); Check(Relation(result2)["confirmed_team"] == null, "different storage kind owner path rejected");
        Scenario.Reset(); Scenario.Team.TypeValue = "DBLI";
        result2 = Read(); Check(Relation(result2)["confirmed_team"] == null && Rows(result2, "teams")[0]["identity"] == null, "SDK array does not certify element type");
        foreach (string type in new[] { "EQUI", "TEAM", "DBLI" })
        {
            Scenario.Reset(); Scenario.Master.TypeValue = type;
            result2 = Read(); Check(Database(result2)["identity"] == null && Match(result2)["matched_index"] == null, "non-DB record rejected " + type);
        }
        Scenario.Reset(); Scenario.Master.Storage = DbType.Design;
        result2 = Read(); Check(Database(result2)["identity"] == null, "DB token in Design storage rejected");
        Scenario.Reset(); Scenario.Master.Deleted = true;
        result2 = Read(); Check(Database(result2)["identity"] == null, "deleted DB record rejected");
        Scenario.Reset(); Scenario.Master.Reference = new[] { -1, 30 };
        result2 = Read(); Check(Database(result2)["identity"] == null, "invalid record ref rejected");
        Scenario.Reset(); Scenario.Team.Reference = new[] { 5, 20 };
        result2 = Read(); Check(Equals(Relation(result2)["status"], "confirmed"), "same numeric ref on DBLI/TEAM remains distinct typed identity");
        Scenario.Reset();
        foreach (var element in new[] { Scenario.Team, Scenario.OtherTeam, Scenario.Dbli, Scenario.Master, Scenario.Working, Scenario.OtherWorking }) element.Storage = DbType.Global;
        result2 = Read(); Check(Equals(result2["success"], true), "Global storage is typed and retained without claiming project coverage");
    }

    private static void DuplicatesAndBounds()
    {
        Scenario.Reset(); DbSystem.Teams = new[] { Scenario.Team, Scenario.Team };
        var result = Read(); Check(Equals(Relation(result)["status"], "ambiguous") && Relation(result)["confirmed_team"] == null, "duplicate typed Team rejected");
        Scenario.Reset(); Scenario.OtherTeam.Name = Scenario.Team.Name;
        result = Read(); Check(Equals(Dict(Relation(result)["confirmed_team"])["refno"], "=5/10"), "Team relation uses typed identity, not name-only lookup");
        Scenario.Reset(); DbSystem.Records = new[] { Scenario.Master, Scenario.Master };
        result = Read(); Check(Equals(Relation(result)["status"], "ambiguous") && Equals(result["identity_consistent"], false), "duplicate native DB identity retained as a typed identity finding");
        Scenario.Reset(); Scenario.OtherWorking.Name = Scenario.Working.Name; Scenario.OtherWorking.Extno = Scenario.Working.Extno;
        result = Read(); Check(Candidates(result, 1, 2) && Candidates(result, 2, 2) && Equals(result["identity_consistent"], true), "same raw tuple on distinct DB identities proves no semantic ambiguity finding");
        Scenario.Reset(); MDB.Current.Databases = new[] { MDB.Current.Databases[1], MDB.Current.Databases[1] };
        result = Read(); Check(Candidates(result, 0, 0, 1) && Equals(result["identity_consistent"], true), "duplicate MDB raw candidates retained without choosing by order");
        Scenario.Reset(); result = Read(limit: 1);
        Check(Equals(result["complete"], false) && Rows(result, "databases").Count == 1 && Rows(result, "teams").Count == 1, "prefix bounded per source");
        Check(Equals(Match(result)["status"], "indeterminate") && Relation(result)["confirmed_team"] == null, "partial arrays cannot certify uniqueness");
        Check(!Scenario.Reads.Any(path => path.StartsWith("working.") || path.StartsWith("other_team.")), "unreturned source rows not inspected");
        Check(Equals(Dict(Dict(result["scans"])["databases"])["has_more"], true), "truncation explicit");
        Scenario.Reset(); DbSystem.Teams = new[] { Scenario.Team };
        DbSystem.Records = new[] { Scenario.Master, Scenario.Master };
        MDB.Current.Databases = new[] { MDB.Current.Databases[1], MDB.Current.Databases[0], MDB.Current.Databases[2] };
        result = Read(limit: 1);
        Check(Equals(Dict(Dict(result["scans"])["teams"])["prefix_complete"], true) &&
            Relation(result)["confirmed_team"] == null && Equals(Relation(result)["status"], "unverified"),
            "complete Team source does not certify uniqueness against a DB prefix");
        Check(Candidates(result, 0, 0), "an observed prefix candidate is retained without confirming the hidden duplicate or MDB join");
        Scenario.Reset(); Scenario.OtherWorking.Valid = false;
        result = Read(); Check(Relation(result)["confirmed_team"] == null && Equals(Relation(result)["status"], "unverified"),
            "an unreadable DB identity may hide a duplicate and blocks Team confirmation");
        Scenario.Reset(); DbSystem.Records = new DbElement[10001];
        result = Read(); Check(Rows(result, "databases").Count == 0 && Equals(result["complete"], false), "over-limit native array rejected before element reads");
        Scenario.Reset(); DbSystem.Teams = null;
        result = Read(); Check(Relation(result)["confirmed_team"] == null && HasError(result, "teams.array"), "null Team array not empty coverage");
        Scenario.Reset(); MDB.Current.Databases = null;
        result = Read(); Check(Equals(Match(result)["status"], "indeterminate"), "null MDB array not evidence of absence");
        Scenario.Reset(); MDB.Current.Databases[2] = null;
        result = Read(); Check(Equals(Match(result)["status"], "indeterminate"), "null MDB row may hide a duplicate");
        Scenario.Reset(); DbSystem.Teams = new DbElement[0];
        result = Read(); Check(Relation(result)["confirmed_team"] == null && Relation(result)["observed_team"] != null, "unlisted typed ancestor retained without confirming catalog relation");
        Scenario.Reset(); DbSystem.Teams = new DbElement[0]; DbSystem.Records = new DbElement[0]; MDB.Current.Databases = new Db[0];
        result = Read(); Check(Equals(result["success"], true) && Equals(Dict(result["scope"])["project_coverage_verified"], false), "empty readable arrays only certify their observation");
    }

    private static void NamesAndFailures()
    {
        foreach (string name in new[] { "PIPING/BASE", "/PIPING/BASE", "/*piping/base" })
        {
            Scenario.Reset(); Scenario.Master.Name = name;
            var result = Read(); Check(Candidates(result, 0, 1), "explicit prefix/case normalization for raw candidates " + name);
        }
        Scenario.Reset(); Scenario.Team.Name = "*PIPING";
        var markedTeam = Read();
        Check(Equals(Relation(markedTeam)["status"], "confirmed") &&
            Equals(Dict(Relation(markedTeam)["confirmed_team"])["refno"], "=5/10"),
            "native TEAM marker is a namespace prefix, not a wildcard");
        Scenario.Reset(); Scenario.Team.Name = "**PIPING";
        var doubleMarker = Read();
        Check(Equals(Relation(doubleMarker)["status"], "unverified"),
            "only one native TEAM marker is normalized");
        foreach (string name in new[] { "BASE", "//PIPING/BASE", "/*PIPING/BASE/EXTRA", "PIPING/BASE ", "PIPING/UNSET", "PIPING/*", "", "PIPING/BA\nSE", new string('A', 257) })
        {
            Scenario.Reset(); Scenario.Master.Name = name;
            var result = Read(); Check(Equals(Match(result)["status"], "indeterminate") && Relation(result)["confirmed_team"] == null, "malformed name never guessed");
        }
        foreach (string path in new[] { "master.NAMEDB.available", "master.DBNO.available", "master.FINO.available", "master.EXTNO.available", "master.EXTOWN.available", "team.NAMN.available" })
        {
            Scenario.Reset(); Scenario.Overrides[path] = false;
            var result = Read(); Check(Equals(result["complete"], false) && ((List<object>)result["read_errors"]).Count > 0, "attribute availability failure " + path);
        }
        Scenario.Reset(); Scenario.Overrides["master.DBNO"] = -1;
        var failed = Read(); Check(Database(failed)["dbno"] == null, "negative database number never defaulted");
        Scenario.Reset(); Scenario.Overrides["master.FINO"] = -1;
        failed = Read(); Check(Database(failed)["fino"] == null && Candidates(failed, 0, 1) &&
            Equals(Relation(failed)["status"], "confirmed"), "file read failure is independent of typed Team relation and raw candidates");
        Scenario.Reset(); Scenario.Overrides["master.EXTNO.available"] = false;
        failed = Read(); Check(Database(failed)["extno"] == null && Equals(Relation(failed)["status"], "confirmed"),
            "raw EXTNO read failure does not erase independently complete typed Team identity");
        Scenario.Reset(); Scenario.Overrides["master.FINO"] = 0;
        failed = Read(); Check(Equals(Database(failed)["fino"], 0) && Equals(Dict(failed["scope"])["file_existence_verified"], false), "zero FINO retained without file assertion");
        Scenario.Reset(); Scenario.Overrides["working.EXTOWN"] = Scenario.Team;
        failed = Read(); Check(Equals(Database(failed, 1)["extract_kind"], "unknown"), "non-DB extract-owner rejected");
        Scenario.Reset(); Scenario.Overrides["master.owner"] = new InvalidOperationException("managed owner read failed");
        failed = Read(); Check(Relation(failed)["confirmed_team"] == null && HasError(failed, "databases[0].owner_path"), "managed failure retains bounded partial evidence");
        Scenario.Reset(); Scenario.Overrides["teams.array"] = new InvalidOperationException(new string('X', 1000));
        failed = Read(); Check(((List<object>)failed["read_errors"]).Select(Dict).All(error => ((string)error["message"]).Length <= 512), "error text bounded");
    }

    private static void ContextAndValidation()
    {
        var defaults = TeamDatabaseIdentityManifestRequest.Validate(Expected());
        Check(defaults.ExpectedContext == Expected() && defaults.Limit == 64 &&
            TeamDatabaseIdentityManifestRequest.DefaultLimit == 64 && TeamDatabaseIdentityManifestRequest.MaximumLimit == 250,
            "strict managed request preserves required digest and default per-source bound");
        foreach (int limit in new[] { 1, 64, 200, 250 })
            Check(TeamDatabaseIdentityManifestRequest.Validate(Expected(), limit).Limit == limit, "inclusive managed request limit " + limit);
        foreach (string expected in new[] { null, "", "bad", new string('A', 64), " " + Expected(), Expected() + "\n" })
            Throws<ArgumentException>(() => TeamDatabaseIdentityManifestRequest.Validate(expected), "strict managed context input");
        foreach (int index in new[] { 4091, 4092, 4093, 4094, 4095, 4096 })
        {
            string text = new string('X', index) + "\ud83d\ude00" + new string('Y', 5000);
            string clipped = TeamDatabaseIdentityManifestRequest.ErrorText(text);
            Check(clipped.Length <= 4096 && ValidSurrogates(clipped), "HTTP error text bound and surrogate pairs");
        }
        Check(TeamDatabaseIdentityManifestRequest.ErrorText(null) == "The read failed without a message." &&
            TeamDatabaseIdentityManifestRequest.ErrorText("plain") == "plain", "HTTP error empty and short text");
        foreach (Action mutate in new Action[] {
            () => Project.Current.Value = "OTHER", () => MDB.Current.Value = "OTHER",
            () => Project.Current = new Project(), () => MDB.Current = new MDB { Databases = new Db[0] },
            () => Scenario.Ce.TypeValue = "BOX", () => Scenario.Ce.Reference = new[] { 5, 2 },
            () => Scenario.Ce.Storage = DbType.Catalogue })
        {
            Scenario.Reset(); Scenario.OnRead = path => { if (path == "mdb.working.is_extract") mutate(); };
            var result = Read();
            Check(Equals(Dict(result["context"])["boundary_agreement"], false) && Equals(result["success"], false), "boundary drift reported");
            Check(Equals(Match(result)["status"], "indeterminate") && Relation(result)["confirmed_team"] == null, "drift invalidates resolved associations");
        }
        Scenario.Reset(); Scenario.OnRead = path => { if (path == "mdb.working.is_extract") Scenario.Overrides["ce.element"] = new InvalidOperationException("CE unreadable"); };
        var unreadable = Read(); Check(Equals(Dict(unreadable["context"])["boundary_agreement"], false), "failed final CE read cannot certify context");
        Scenario.Reset(); Throws<InvalidOperationException>(() => Read(expected: new string('0', 64)), "stale context rejected");
        Check(!Scenario.Reads.Contains("teams.array"), "stale context rejected before catalog arrays");
        foreach (int limit in new[] { 0, -1, 251 })
        {
            Scenario.Reset(); Throws<ArgumentOutOfRangeException>(() => Read(limit: limit), "invalid bound");
            Check(Scenario.Dispatches == 0 && Scenario.Reads.Count == 0, "invalid bound before dispatch");
        }
        foreach (string expected in new[] { "", "bad", new string('A', 64), Expected() + "\n", " " + Expected() })
        {
            Scenario.Reset(); Throws<ArgumentException>(() => Read(expected: expected), "invalid expected context");
            Check(Scenario.Dispatches == 0, "invalid expected context before dispatch");
        }
        foreach (string session in new[] { "", "SESSION\n", new string('S', 257) })
        {
            Scenario.Reset(); Throws<ArgumentException>(() => Read(session: session), "invalid session");
            Check(Scenario.Dispatches == 0, "invalid session before dispatch");
        }
        Scenario.Reset(); Scenario.BusyAt = 1;
        Throws<InvalidOperationException>(() => Read(), "busy before dispatch"); Check(Scenario.Dispatches == 0, "outer busy has no dispatch");
        Scenario.Reset(); Scenario.BusyAt = 2;
        Throws<InvalidOperationException>(() => Read(), "busy inside dispatch"); Check(Scenario.Reads.Count == 0, "inner busy has no SDK read");
        Scenario.Reset(); Project.Current = null;
        Throws<InvalidOperationException>(() => Read(), "null project rejected"); Check(!Scenario.Reads.Contains("teams.array"), "no catalog without project");
        Scenario.Reset(); Scenario.Ce.Valid = false;
        Throws<InvalidOperationException>(() => Read(), "invalid initial CE rejected");
    }

    private static void FaultAndManagedFailureMatrix()
    {
        Scenario.Reset(); Read(); int total = Scenario.Reads.Count;
        foreach (bool ordinaryException in new[] { false, true })
        for (int index = 1; index <= total; index++)
        {
            Scenario.Reset(); Scenario.FaultAt = index; Scenario.ThrowAt = ordinaryException ? index : 0;
            Throws<NativeDatabaseFaultException>(() => Read(), "fault propagated at read " + index);
            Check(Scenario.Reads.Count == index && Scenario.ReadsAfterFault == 0 && !Scenario.InUi,
                "no reads after native fault " + index + "; observed=" + Scenario.Reads.Count + ", after_fault=" + Scenario.ReadsAfterFault);
            int dispatches = Scenario.Dispatches;
            Throws<NativeDatabaseFaultException>(() => Read(), "latched fault blocks subsequent scan");
            Check(Scenario.Dispatches == dispatches, "latched fault rejected before UI dispatch");
        }
        for (int index = 1; index <= total; index++)
        {
            Scenario.Reset(); Scenario.ThrowAt = index;
            try { var result = Read(); Check(Equals(result["success"], false), "managed failure cannot return success " + index); }
            catch (InvalidOperationException) { Check(!Scenario.Reads.Contains("teams.array"), "initial boundary failure stops array reads"); }
            Check(!Scenario.InUi && Scenario.ReadsAfterFault == 0, "managed failure dispatch cleanup");
        }
        Console.WriteLine("Team/DB native fault matrix: " + total + " read positions x 2 return/ordinary-exception modes; managed failure matrix: " + total + ".");
    }

    private static string Serialize(object result) => new System.Web.Script.Serialization.JavaScriptSerializer { MaxJsonLength = 1000000 }.Serialize(result);
    public static void Main()
    {
        try
        {
            Console.WriteLine("EXPECTED_BUILD=" + typeof(E3d21Context).Module.ModuleVersionId.ToString("D"));
            NormalAndExtracts(); OwnershipAndTypes(); DuplicatesAndBounds(); NamesAndFailures(); ContextAndValidation(); FaultAndManagedFailureMatrix();
            Console.WriteLine("Team/DB identity manifest production checks passed: " + checks + ".");
        }
        catch (Exception error)
        {
            Console.Error.WriteLine("Harness failure after check " + checks + ": " + error.GetType().FullName + ": " + error.Message);
            Console.Error.WriteLine(error.StackTrace);
            Environment.ExitCode = 1;
        }
    }
}
