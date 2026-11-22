using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using E3d21AiBridge;

namespace Aveva.Core.Database
{
    public enum DbType { Design }
    public class Project { public static Project CurrentProject = new Project(); public string Name = "APS"; }
    public class MDB { public static MDB CurrentMDB = new MDB(); public string Name = "ALL"; }
    public class DbElement
    {
        public static int MemberCalls, FailMemberOrdinal;
        private DbElement owner;
        private string refno, type, name;
        public bool IsNull, RejectIdentityReads;
        public int OwnerReads, IdentityReads;
        public DbType DbType = DbType.Design;
        public DbElement Owner
        {
            get { ++OwnerReads; RejectIfSentinelIdentityWasRead(); return owner; }
            set { owner = value; }
        }
        public string Refno
        {
            get { ++IdentityReads; RejectIfSentinelIdentityWasRead(); return refno; }
            set { refno = value; }
        }
        public string Type
        {
            get { ++IdentityReads; RejectIfSentinelIdentityWasRead(); return type; }
            set { type = value; }
        }
        public string Name
        {
            get { ++IdentityReads; RejectIfSentinelIdentityWasRead(); return name; }
            set { name = value; }
        }
        public List<DbElement> Members = new List<DbElement>();
        private void RejectIfSentinelIdentityWasRead()
        {
            if (RejectIdentityReads) throw new InvalidOperationException("lookahead sentinel identity must not be read.");
        }
        public DbElement Member(int ordinal)
        {
            ++MemberCalls;
            if (ordinal == FailMemberOrdinal) throw new InvalidOperationException(new string('m', 10000));
            return ordinal <= Members.Count ? Members[ordinal - 1] : null;
        }
    }
}

namespace E3d21AiBridge
{
    // The production snapshot and pure walker compile unchanged against these
    // recording native readers. A nested dispatch, write, or unbound SDK read
    // has no permissive implementation in this harness.
    public sealed partial class E3d21Context
    {
        public static int Dispatches, TraceReads, PairReads, BranchReads;
        public static bool Active, Fault, ChangeMdb, LoseCe, LostCe, FaultOnPair, LongErrors, ContextErrorAfterPair;
        private static bool inUi;
        public static readonly Dictionary<string, Dictionary<string, object>> Endpoints = new Dictionary<string, Dictionary<string, object>>();
        public static Aveva.Core.Database.DbElement Branch;
        public static void Reset()
        {
            Dispatches = TraceReads = PairReads = BranchReads = 0;
            Aveva.Core.Database.DbElement.MemberCalls = Aveva.Core.Database.DbElement.FailMemberOrdinal = 0;
            Active = Fault = ChangeMdb = inUi = LoseCe = LostCe = FaultOnPair = LongErrors = ContextErrorAfterPair = false;
            Endpoints.Clear();
            Aveva.Core.Database.Project.CurrentProject = new Aveva.Core.Database.Project();
            Aveva.Core.Database.MDB.CurrentMDB = new Aveva.Core.Database.MDB();
            Branch = new Aveva.Core.Database.DbElement { Refno = "=1/1", Type = "BRAN", Name = "/B" };
            Branch.Members.Add(new Aveva.Core.Database.DbElement { Refno = "=1/3", Type = "ELBO", Owner = Branch });
            Branch.Members.Add(new Aveva.Core.Database.DbElement { Refno = "=1/3", Type = "TUBI", Owner = Branch });
        }
        private bool IsCommandActive() => Active;
        private T OnUi<T>(Func<T> operation)
        {
            if (inUi) throw new Exception("Nested SDK dispatch");
            ++Dispatches; inUi = true;
            try { ThrowIfNativeFaulted(); return operation(); }
            finally { inUi = false; }
        }
        private static void ThrowIfNativeFaulted() { if (Fault) throw new InvalidOperationException("fatal native fault"); }
        private static T NativeRead<T>(Func<T> read)
        {
            if (!inUi) throw new Exception("SDK read outside UI scope");
            ThrowIfNativeFaulted(); T value = read(); ThrowIfNativeFaulted(); return value;
        }
        private static Dictionary<string, object> ReadEndpointContext(Aveva.Core.Database.Project project, Aveva.Core.Database.MDB mdb)
        {
            if (ContextErrorAfterPair && PairReads > 0) throw new InvalidOperationException(new string('c', 10000));
            return new Dictionary<string, object> { ["project"] = project.Name, ["mdb"] = mdb.Name, ["current_element"] = LostCe ? "" : "=1/90", ["current_element_type"] = "EQUI" };
        }
        private static bool IsUsable(Aveva.Core.Database.DbElement element) => element != null && !element.IsNull;
        private static string ElementRef(Aveva.Core.Database.DbElement element) => element.Refno;
        private static string ElementType(Aveva.Core.Database.DbElement element) => element.Type;
        private static Dictionary<string, object> ElementIdentity(Aveva.Core.Database.DbElement element) => new Dictionary<string, object> {
            ["refno"] = element.Refno, ["type"] = element.Type, ["name"] = element.Name };
        private static Aveva.Core.Database.DbElement ResolveElementByType(string reference, string type)
        {
            ++BranchReads;
            if (reference != "=1/1" && reference != "/B") throw new ArgumentException(LongErrors ? new string('b', 10000) : "Missing branch");
            return Branch;
        }
        private static Dictionary<string, object> ReadAttributesOnUiThread(Aveva.Core.Database.DbElement element, IList<string> names)
        {
            var result = new Dictionary<string, object> { ["success"] = !LongErrors, ["refno"] = element.Refno, ["type"] = element.Type,
                ["values"] = names.ToDictionary(name => name, name => "native:" + name), ["typed_values"] = new Dictionary<string, object>(),
                ["errors"] = new Dictionary<string, string>(), ["typed_read_errors"] = new Dictionary<string, string>() };
            if (LongErrors)
            {
                ((Dictionary<string, string>)result["values"])["HPOS"] = new string('v', 10000);
                ((Dictionary<string, string>)result["errors"])["HPOS"] = new string('a', 10000);
                ((Dictionary<string, string>)result["typed_read_errors"])["HBOR"] = new string('t', 10000);
            }
            return result;
        }
        private static Dictionary<string, object> ConnectionReadTraceEndpoint(string reference, int point, string type)
        {
            ThrowIfNativeFaulted(); ++TraceReads;
            return Endpoints[reference + "|" + type + "|" + point];
        }
        private static Dictionary<string, object> ConnectionReadPairEndpoint(string reference, int point, string type)
        {
            ThrowIfNativeFaulted(); ++PairReads;
            if (FaultOnPair) { Fault = true; ThrowIfNativeFaulted(); }
            if (ChangeMdb && PairReads == 2) Aveva.Core.Database.MDB.CurrentMDB = new Aveva.Core.Database.MDB();
            if (LoseCe && PairReads == 2) LostCe = true;
            var result = Endpoint(reference, type, point);
            result["requested_expected_type"] = type;
            if (LongErrors)
            {
                result["complete"] = false;
                ((Dictionary<string, string>)result["errors"])["position"] = new string('p', 10000);
                result["catalogue_evidence"] = new Dictionary<string, object> {
                    ["SPREF"] = new Dictionary<string, object> { ["error"] = new string('s', 10000) } };
            }
            return result;
        }
        private static Dictionary<string, object> ConnectionPairElementIdentity(Dictionary<string, object> endpoint) => (Dictionary<string, object>)endpoint["element"];
        private static Dictionary<string, object> ConnectionComparePairPositions(Dictionary<string, object> a, Dictionary<string, object> b, double tolerance) =>
            new Dictionary<string, object> { ["status"] = "within_tolerance", ["distance_mm"] = 0.0 };
        private static Dictionary<string, object> ConnectionComparePairDirections(Dictionary<string, object> a, Dictionary<string, object> b) =>
            new Dictionary<string, object> { ["relationship"] = "opposed" };
        private static Dictionary<string, object> ConnectionComparePairReferences(Dictionary<string, object> a, Dictionary<string, object> ai, int ap,
            Dictionary<string, object> b, Dictionary<string, object> bi, int bp) => new Dictionary<string, object> { ["status"] = "not_referenced" };
        public static Dictionary<string, object> Endpoint(string reference, string type, int point, string target = null, string targetType = null, int targetPoint = 1)
        {
            string actual = reference == "/ALIAS" ? "=1/2" : reference;
            var connection = new Dictionary<string, object> { ["available"] = true, ["resolution_complete"] = true,
                ["point_number"] = target == null ? (object)null : targetPoint,
                ["reference"] = new Dictionary<string, object> { ["status"] = target == null ? "unset" : "resolved", ["refno"] = target, ["type"] = targetType } };
            return new Dictionary<string, object> { ["requested_refno"] = reference, ["requested_type"] = type, ["point_number"] = point,
                ["element"] = new Dictionary<string, object> { ["refno"] = actual, ["type"] = type, ["status"] = "exists", ["name"] = reference },
                ["status"] = "exists", ["complete"] = true, ["errors"] = new Dictionary<string, string>(),
                ["exists"] = new Dictionary<string, object> { ["available"] = true, ["value"] = true },
                ["point"] = new Dictionary<string, object> { ["number"] = point, ["complete"] = true, ["connection_reference"] = connection } };
        }
    }
}

internal static class ConnectivitySnapshotHarness
{
    private static int checks;
    private static void Check(bool condition, string label) { ++checks; if (!condition) throw new Exception(label); }
    private static ConnectivitySnapshotRequest Request(string json) => ConnectivitySnapshotRequest.Parse(JObject.Parse(json));
    private static string Build => typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
    private static Dictionary<string, object> Read(string json) => (Dictionary<string, object>)new E3d21Context().ReadConnectivitySnapshot(Request(json), "session", Build);
    private static List<Dictionary<string, object>> Rows(Dictionary<string, object> data, string key) => (List<Dictionary<string, object>>)data[key];
    private static Dictionary<string, object> Trace(Dictionary<string, object> result) => (Dictionary<string, object>)((Dictionary<string, object>)result["trace"])["data"];
    private const string Start = "{\"refno\":\"=1/2\",\"expected_type\":\"ELBO\",\"point_number\":1}";
    private const string Pair = "{\"first\":{\"refno\":\"=1/2\",\"expected_type\":\"ELBO\",\"point_number\":1},\"second\":{\"refno\":\"=1/3\",\"expected_type\":\"TUBI\",\"point_number\":2},\"tolerance_mm\":0}";

    public static void Main()
    {
        string[] invalid = { "{}", "{\"starts\":null}", "{\"branches\":[{\"refno\":\"/B\",\"expected_type\":\"PIPE\"}]}",
            "{\"starts\":[{\"refno\":\"=0/1\",\"expected_type\":\"ELBO\",\"point_number\":1}]}",
            "{\"starts\":[{\"refno\":\"/*\",\"expected_type\":\"ELBO\",\"point_number\":1}]}",
            "{\"starts\":[{\"refno\":\"=1/2\",\"point_number\":1}]}",
            "{\"starts\":[" + Start + "],\"max_hops\":true}", "{\"starts\":[" + Start + "],\"max_hops\":17}",
            "{\"starts\":[" + Start + "],\"max_nodes\":129}", "{\"starts\":[" + Start + "," + Start + "]}",
            "{\"starts\":[" + Start + "],\"expected_context\":{}}", "{\"starts\":[" + Start + "],\"write\":true}" };
        foreach (string json in invalid)
        {
            bool rejected = false; try { Request(json); } catch (ArgumentException) { rejected = true; }
            Check(rejected, "Bad request accepted: " + json);
        }
        E3d21Context.Reset();
        E3d21Context.Endpoints["=1/2|ELBO|1"] = E3d21Context.Endpoint("=1/2", "ELBO", 1, "=1/3", "TUBI", 2);
        E3d21Context.Endpoints["=1/3|TUBI|2"] = E3d21Context.Endpoint("=1/3", "TUBI", 2, "=1/2", "ELBO", 1);
        var batch = Read("{\"branches\":[{\"refno\":\"=1/1\",\"expected_type\":\"BRAN\"}],\"pairs\":[" + Pair + "],\"starts\":[" + Start + "]}");
        Check(Equals(batch["complete"], true) && E3d21Context.Dispatches == 1, "Batch did not use one dispatch");
        var trace = Trace(batch); var nodes = Rows(trace, "nodes"); var edges = Rows(trace, "edges");
        Check(nodes.Count == 2 && E3d21Context.TraceReads == 2, "Unexpected endpoint reads");
        Check(edges.All(edge => Equals(edge["relationship"], "reciprocal") && Equals(edge["cycle"], true)), "Reciprocal cycle was lost");
        var branch = (Dictionary<string, object>)Rows(batch, "branches")[0]["data"];
        Check(Rows(branch, "members").Select(row => row["identity_key"]).Distinct().Count() == 2, "Shared DBREF aliases merged");
        Check(Equals(branch["member_probe_count"], 3) && Equals(branch["lookahead_probe_count"], 0) &&
            Equals(branch["member_probe_limit"], 65) && Aveva.Core.Database.DbElement.MemberCalls == 3, "Short branch probe count lost the empty terminator");
        var limits = (Dictionary<string, object>)batch["limits"];
        Check(Equals(limits["member_budget"], 64) && Equals(limits["member_probe_budget"], 65) && Equals(limits["member_probe_count"], 3), "Returned-member and inspection budgets were conflated");
        Check(Equals(((Dictionary<string, object>)((Dictionary<string, object>)batch["context"])["before"])["bridge_build"], Build) &&
            Equals(((Dictionary<string, object>)((Dictionary<string, object>)Rows(batch, "pairs")[0]["context"])["after"])["bridge_build"], Build), "Boundary did not observe the current module MVID");
        Check(((Dictionary<string, object>)Rows(batch, "pairs")[0]["data"])["pcref_correspondence"] is Dictionary<string, object>, "Pair metadata missing");
        Check(!JObject.FromObject(batch).ToString().Contains("engineering_compatible"), "Engineering verdict invented");
        Console.WriteLine("SNAPSHOT_RECEIPT:" + JObject.FromObject(new { ok = true, version = "E3D21", session_id = "session", bridge_build = Build, result = batch }).ToString(Newtonsoft.Json.Formatting.None));

        E3d21Context.TraceReads = 0;
        trace = Trace(Read("{\"starts\":[" + Start + "],\"max_hops\":0}"));
        Check(E3d21Context.TraceReads == 1 && Equals(trace["truncated"], true), "Hop 0 read a target");
        Check(Rows(trace, "edges")[0]["to_index"] == null && Equals(Rows(trace, "edges")[0]["status"], "hop_limit"), "Hop frontier fabricated a visited node");
        E3d21Context.TraceReads = 0;
        trace = Trace(Read("{\"starts\":[" + Start + "],\"max_nodes\":1}"));
        Check(E3d21Context.TraceReads == 1 && Rows(trace, "edges")[0]["to_index"] == null && Equals(Rows(trace, "edges")[0]["status"], "node_limit"), "Node budget exceeded");

        E3d21Context.Endpoints["/ALIAS|ELBO|1"] = E3d21Context.Endpoint("/ALIAS", "ELBO", 1);
        E3d21Context.TraceReads = 0;
        trace = Trace(Read("{\"starts\":[" + Start + ",{\"refno\":\"/ALIAS\",\"expected_type\":\"ELBO\",\"point_number\":1}],\"max_nodes\":2}"));
        Check(E3d21Context.TraceReads == 2 && Rows(trace, "nodes").Count == 1 && Equals(trace["truncated"], true), "Alias inspections did not count against budget");
        Check(Rows(trace, "starts").Count == 2 && Equals(Rows(trace, "starts")[1]["node_index"], 0), "Alias seed mapping lost");
        E3d21Context.Endpoints["=1/2|TUBI|1"] = E3d21Context.Endpoint("=1/2", "TUBI", 1);
        trace = Trace(Read("{\"starts\":[" + Start + ",{\"refno\":\"=1/2\",\"expected_type\":\"TUBI\",\"point_number\":1}],\"max_hops\":0}"));
        Check(Rows(trace, "nodes").Count == 2, "TUBI and component seed identity merged");

        var partial = E3d21Context.Endpoint("=1/2", "ELBO", 1); partial["complete"] = false;
        E3d21Context.Endpoints["=1/2|ELBO|1"] = partial;
        trace = Trace(Read("{\"starts\":[" + Start + "]}"));
        Check(Equals(trace["scope_complete"], false) && trace["has_more"] == null && Rows(trace, "edges").Count == 0, "Unknown endpoint became an open end");
        batch = Read("{\"branches\":[{\"refno\":\"=1/1\",\"expected_type\":\"BRAN\"}],\"member_limit\":1}");
        Check(Equals(batch["complete"], false) && Equals(((Dictionary<string, object>)Rows(batch, "branches")[0]["data"])["has_more"], true), "Truncated members reported exhaustion");
        branch = (Dictionary<string, object>)Rows(batch, "branches")[0]["data"];
        Check(Equals(branch["member_probe_count"], 2) && Equals(branch["lookahead_probe_count"], 1) && Equals(branch["returned_count"], 1), "Present extra member was not counted as a lookahead");
        E3d21Context.Reset();
        var sentinel = new Aveva.Core.Database.DbElement { Owner = E3d21Context.Branch, RejectIdentityReads = true };
        E3d21Context.Branch.Members = new List<Aveva.Core.Database.DbElement> { E3d21Context.Branch.Members[0], sentinel };
        batch = Read("{\"branches\":[{\"refno\":\"=1/1\",\"expected_type\":\"BRAN\"}],\"member_limit\":1}");
        branch = (Dictionary<string, object>)Rows(batch, "branches")[0]["data"];
        Check(Equals(branch["has_more"], true) && Rows(branch, "read_errors").Count == 0 &&
            sentinel.OwnerReads == 0 && sentinel.IdentityReads == 0, "Lookahead sentinel was read as a returned member");
        E3d21Context.Reset();
        batch = Read("{\"branches\":[{\"refno\":\"=1/1\",\"expected_type\":\"BRAN\"}],\"member_limit\":2}");
        branch = (Dictionary<string, object>)Rows(batch, "branches")[0]["data"];
        Check(Equals(batch["complete"], true) && Equals(branch["member_probe_count"], 3) && Equals(branch["lookahead_probe_count"], 1), "Empty extra lookahead was not counted");
        E3d21Context.Reset(); E3d21Context.Branch.Members.Clear();
        batch = Read("{\"branches\":[{\"refno\":\"=1/1\",\"expected_type\":\"BRAN\"}]}");
        branch = (Dictionary<string, object>)Rows(batch, "branches")[0]["data"];
        Check(Equals(batch["complete"], true) && Equals(branch["member_probe_count"], 1) && Equals(branch["lookahead_probe_count"], 0), "Empty branch omitted its native probe");
        E3d21Context.Reset(); Aveva.Core.Database.DbElement.FailMemberOrdinal = 2;
        batch = Read("{\"branches\":[{\"refno\":\"=1/1\",\"expected_type\":\"BRAN\"}],\"member_limit\":1}");
        branch = (Dictionary<string, object>)Rows(batch, "branches")[0]["data"];
        Check(Equals(branch["member_probe_count"], 2) && Equals(branch["lookahead_probe_count"], 1) && branch["has_more"] == null &&
            ((string)Rows(branch, "read_errors")[0]["message"]).Length == 4096, "Failed lookahead probe or bounded member error was lost");
        E3d21Context.Reset(); E3d21Context.LongErrors = true;
        batch = Read("{\"branches\":[{\"refno\":\"=1/99\",\"expected_type\":\"BRAN\"},{\"refno\":\"=1/1\",\"expected_type\":\"BRAN\"}],\"pairs\":[" + Pair + "]}");
        Check(((string)Rows(Rows(batch, "branches")[0], "read_errors")[0]["message"]).Length == 4096, "Ordinary item exception was unbounded");
        branch = (Dictionary<string, object>)Rows(batch, "branches")[1]["data"];
        var attributes = (Dictionary<string, object>)branch["attributes"];
        Check(((Dictionary<string, string>)attributes["errors"])["HPOS"].Length == 4096 &&
            ((Dictionary<string, string>)attributes["typed_read_errors"])["HBOR"].Length == 4096 &&
            ((Dictionary<string, string>)attributes["values"])["HPOS"].Length == 10000, "Nested diagnostic bounds truncated attribute evidence or missed an error");
        var pairData = (Dictionary<string, object>)Rows(batch, "pairs")[0]["data"];
        var pairEndpoint = (Dictionary<string, object>)((Dictionary<string, object>)pairData["endpoints"])["first"];
        Check(((Dictionary<string, string>)pairEndpoint["errors"])["position"].Length == 4096 &&
            ((string)((Dictionary<string, object>)((Dictionary<string, object>)pairEndpoint["catalogue_evidence"])["SPREF"])["error"]).Length == 4096, "Nested pair errors were unbounded");
        Check(Equals(((Dictionary<string, object>)batch["limits"])["member_probe_count"], 3) &&
            Equals(((Dictionary<string, object>)batch["limits"])["member_probe_budget"], 130), "Failed branches changed the declared budget or invented member probes");
        E3d21Context.Reset();
        var traceEndpoint = E3d21Context.Endpoint("=1/2", "ELBO", 1); traceEndpoint["complete"] = false;
        ((Dictionary<string, string>)traceEndpoint["errors"])["connection_reference"] = new string('e', 10000);
        ((Dictionary<string, object>)((Dictionary<string, object>)traceEndpoint["point"])["connection_reference"])["error"] = new string('r', 10000);
        E3d21Context.Endpoints["=1/2|ELBO|1"] = traceEndpoint;
        batch = Read("{\"starts\":[" + Start + "]}");
        traceEndpoint = (Dictionary<string, object>)Rows(Trace(batch), "nodes")[0]["endpoint"];
        Check(((Dictionary<string, string>)traceEndpoint["errors"])["connection_reference"].Length == 4096 &&
            ((string)((Dictionary<string, object>)((Dictionary<string, object>)traceEndpoint["point"])["connection_reference"])["error"]).Length == 4096, "Nested trace errors were unbounded");
        E3d21Context.Reset(); E3d21Context.ContextErrorAfterPair = true;
        batch = Read("{\"pairs\":[" + Pair + "],\"starts\":[" + Start + "]}");
        Check(((string)Rows(batch, "read_errors")[0]["message"]).Length == 4096 &&
            ((string)Rows(Rows(batch, "pairs")[0], "read_errors")[0]["message"]).Length == 4096 &&
            ((string)Rows((Dictionary<string, object>)batch["trace"], "read_errors")[0]["message"]).Length == 4096, "Batch/item boundary errors were unbounded");

        foreach (string wrongBuild in new[] { "build", Guid.Empty.ToString("D"), Build + " " })
        {
            E3d21Context.Reset(); bool rejected = false;
            try { new E3d21Context().ReadConnectivitySnapshot(Request("{\"starts\":[" + Start + "]}"), "session", wrongBuild); }
            catch (InvalidOperationException) { rejected = true; }
            Check(rejected && E3d21Context.TraceReads == 0 && E3d21Context.PairReads == 0 && E3d21Context.BranchReads == 0, "Caller build was echoed instead of compared to the current MVID");
        }

        E3d21Context.Reset(); E3d21Context.ChangeMdb = true;
        batch = Read("{\"pairs\":[" + Pair + "],\"starts\":[" + Start + "]}");
        Check(Equals(batch["complete"], false) && E3d21Context.TraceReads == 0, "Reads continued in a changed MDB");
        Check(Equals(Rows(batch, "pairs")[0]["complete"], false), "Item retained completeness after MDB object change");
        E3d21Context.Reset(); E3d21Context.LoseCe = true;
        batch = Read("{\"pairs\":[" + Pair + "],\"starts\":[" + Start + "]}");
        Check(Equals(batch["complete"], false) && E3d21Context.TraceReads == 0 && batch["trace"] != null, "Unavailable item context lost the partial batch");
        E3d21Context.Reset();
        batch = Read("{\"branches\":[{\"refno\":\"=1/99\",\"expected_type\":\"BRAN\"}],\"pairs\":[" + Pair + "]}");
        Check(Equals(batch["complete"], false) && Rows(batch, "branches")[0]["data"] == null && Equals(Rows(batch, "pairs")[0]["complete"], true), "An ordinary item failure discarded later evidence");
        E3d21Context.Reset();
        string expected = "\"expected_context\":{\"session_id\":\"old-session\",\"bridge_build\":\"" + Build + "\",\"project\":\"APS\",\"mdb\":\"ALL\",\"current_element\":\"=1/90\",\"current_element_type\":\"EQUI\"}";
        batch = Read("{\"starts\":[" + Start + "]," + expected + "}");
        Check(Equals(batch["complete"], false) && E3d21Context.TraceReads == 0, "Stale expected context read targets");
        expected = expected.Replace("old-session", "session").Replace(Build, Guid.Empty.ToString("D"));
        batch = Read("{\"starts\":[" + Start + "]," + expected + "}");
        Check(Equals(batch["complete"], false) && E3d21Context.TraceReads == 0, "Stale expected build was not compared to the observed MVID");
        E3d21Context.Reset(); E3d21Context.Fault = true;
        bool failed = false; try { Read("{\"starts\":[" + Start + "]}"); } catch (InvalidOperationException) { failed = true; }
        Check(failed && E3d21Context.TraceReads == 0, "Native fault did not stop reads");
        E3d21Context.Reset(); E3d21Context.FaultOnPair = true;
        failed = false; try { Read("{\"pairs\":[" + Pair + "],\"starts\":[" + Start + "]}"); } catch (InvalidOperationException) { failed = true; }
        Check(failed && E3d21Context.PairReads == 1 && E3d21Context.TraceReads == 0, "A mid-read native fault was swallowed");
        Console.WriteLine("Connectivity snapshot native checks passed: " + checks);
    }
}
