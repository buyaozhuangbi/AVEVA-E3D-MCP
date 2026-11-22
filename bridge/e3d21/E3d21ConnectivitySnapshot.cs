using System;
using System.Collections.Generic;
using System.Linq;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context : IE3d21ConnectivitySnapshotContext
{
    private static readonly string[] ConnectivityBranchAttributes = {
        "HREF", "TREF", "HPOS", "TPOS", "HDIR", "TDIR", "HBOR", "TBOR", "HCON", "TCON",
        "LHEAD", "LTAIL", "PSPEC", "ISPEC", "TSPEC", "PRES", "TEMP", "SLOREF"
    };

    public object ReadConnectivitySnapshot(ConnectivitySnapshotRequest request, string sessionId, string bridgeBuild)
    {
        if (request == null || string.IsNullOrWhiteSpace(sessionId) || string.IsNullOrWhiteSpace(bridgeBuild))
            throw new ArgumentException("A validated request and native HTTP session/build identities are required.");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy.");
        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy.");
            Project project = NativeRead(() => Project.CurrentProject);
            MDB mdb = NativeRead(() => MDB.CurrentMDB);
            Dictionary<string, object> before = ConnectivityContext(project, mdb, sessionId, bridgeBuild);
            var branches = new List<Dictionary<string, object>>();
            var pairs = new List<Dictionary<string, object>>();
            Dictionary<string, object> trace = null;
            var errors = new List<Dictionary<string, object>>();
            bool expectedMatches = request.ExpectedContext.All(entry => Equals(before[entry.Key], entry.Value));
            if (!expectedMatches)
                errors.Add(new Dictionary<string, object> { ["code"] = "expected_context_mismatch", ["message"] = "Requested context does not match this native observation; no target was read." });
            else
            {
                for (int i = 0; i < request.Branches.Count; ++i)
                {
                    string reference = request.Branches[i];
                    branches.Add(ConnectivityItem("branch", i, new Dictionary<string, object> { ["refno"] = reference, ["expected_type"] = "BRAN" },
                        () => ConnectivityBranch(reference, request.MembersPerBranch), sessionId, bridgeBuild, before, project, mdb));
                }
                for (int i = 0; i < request.Pairs.Count; ++i)
                {
                    var pair = request.Pairs[i];
                    pairs.Add(ConnectivityItem("pair", i, pair.Json(), () => ConnectivityPairRead(pair), sessionId, bridgeBuild, before, project, mdb));
                }
                if (request.Starts.Count != 0)
                    trace = ConnectivityItem("trace", 0, new Dictionary<string, object> {
                        ["starts"] = request.Starts.Select(start => start.Json()).ToList(), ["max_hops"] = request.MaxHops, ["max_nodes"] = request.MaxNodes },
                        () => ConnectivityTraceSnapshot.Read(request, endpoint =>
                            ConnectionReadTraceEndpoint(endpoint.Refno, endpoint.PointNumber, endpoint.ExpectedType)), sessionId, bridgeBuild, before, project, mdb);
            }
            Dictionary<string, object> after = null;
            bool? sameProject = null, sameMdb = null, agrees = null;
            try
            {
                Project projectAfter = NativeRead(() => Project.CurrentProject);
                MDB mdbAfter = NativeRead(() => MDB.CurrentMDB);
                after = ConnectivityContext(projectAfter, mdbAfter, sessionId, bridgeBuild);
                sameProject = ReferenceEquals(project, projectAfter); sameMdb = ReferenceEquals(mdb, mdbAfter);
                agrees = sameProject == true && sameMdb == true && ConnectivityContextEqual(before, after);
            }
            catch (Exception error)
            {
                ThrowIfNativeFaulted();
                errors.Add(new Dictionary<string, object> { ["code"] = "context_after_unavailable", ["message"] = ConnectivitySnapshotErrors.Text(error.Message) });
            }
            if (agrees != true) errors.Add(new Dictionary<string, object> { ["code"] = "context_unverified" });
            var items = branches.Concat(pairs).Concat(trace == null ? new Dictionary<string, object>[0] : new[] { trace }).ToList();
            // A late batch mismatch invalidates item attribution, while preserving
            // each item's read coverage and its own boundary evidence.
            foreach (var item in items)
            {
                var itemContext = (Dictionary<string, object>)item["context"];
                var itemBefore = itemContext["before"] as Dictionary<string, object>;
                var itemAfter = itemContext["after"] as Dictionary<string, object>;
                bool observedConflict = agrees == false || Equals(itemContext["boundary_agreement"], false) ||
                    itemBefore != null && !ConnectivityContextEqual(before, itemBefore) ||
                    itemAfter != null && !ConnectivityContextEqual(before, itemAfter);
                bool? itemMatches = observedConflict ? (bool?)false :
                    agrees == true && itemBefore != null && itemAfter != null && Equals(itemContext["boundary_agreement"], true) ? (bool?)true : null;
                item["matches_batch_context"] = itemMatches;
                item["complete"] = Equals(item["complete"], true) && Equals(item["matches_batch_context"], true);
                if (!Equals(item["complete"], true)) errors.Add(new Dictionary<string, object> {
                    ["code"] = "item_incomplete", ["kind"] = item["kind"], ["index"] = item["index"] });
                var data = item["data"] as Dictionary<string, object>;
                bool contextVerified = Equals(item["matches_batch_context"], true) &&
                    Equals(((Dictionary<string, object>)item["context"])["boundary_agreement"], true);
                if (Equals(item["kind"], "branch") && data != null)
                    foreach (var member in (List<Dictionary<string, object>>)data["members"])
                    {
                        member["read_complete"] = Equals(member["complete"], true);
                        member["complete"] = Equals(member["read_complete"], true) && contextVerified;
                        member["context_binding_id"] = "branch:" + item["index"];
                    }
                if (Equals(item["kind"], "pair") && data != null)
                    foreach (var endpoint in ((Dictionary<string, object>)data["endpoints"]).Values.Cast<Dictionary<string, object>>())
                    {
                        endpoint["read_complete"] = Equals(endpoint["complete"], true);
                        endpoint["complete"] = Equals(endpoint["read_complete"], true) && contextVerified;
                        endpoint["context_binding_id"] = "pair:" + item["index"];
                    }
                if (Equals(item["kind"], "trace") && data != null)
                    foreach (var node in (List<Dictionary<string, object>>)data["nodes"])
                    {
                        node["context_binding_id"] = "trace:0";
                        node["complete"] = Equals(node["read_complete"], true) && contextVerified;
                    }
            }
            bool complete = expectedMatches && agrees == true && errors.Count == 0;
            var result = new Dictionary<string, object> {
                ["operation"] = "piping_connectivity_snapshot", ["success"] = complete, ["available"] = items.Any(item => item["data"] != null),
                ["complete"] = complete, ["read_only"] = true, ["single_dispatch"] = true, ["atomic_snapshot"] = false,
                ["branches"] = branches, ["pairs"] = pairs, ["trace"] = trace, ["read_errors"] = errors,
                ["context"] = new Dictionary<string, object> { ["before"] = before, ["after"] = after,
                    ["same_project_instance"] = sameProject, ["same_mdb_instance"] = sameMdb, ["boundary_agreement"] = agrees,
                    ["expected_context_matches"] = expectedMatches },
                ["limits"] = new Dictionary<string, object> { ["branch_count"] = request.Branches.Count, ["pair_count"] = request.Pairs.Count,
                    ["start_count"] = request.Starts.Count, ["member_limit"] = request.MembersPerBranch, ["member_budget"] = request.Branches.Count * request.MembersPerBranch,
                    ["member_probe_budget"] = request.Branches.Count * (request.MembersPerBranch + 1),
                    ["member_probe_count"] = branches.Where(item => item["data"] is Dictionary<string, object>)
                        .Sum(item => (int)((Dictionary<string, object>)item["data"])["member_probe_count"]),
                    ["member_budget_scope"] = "member_budget bounds returned direct members; member_probe_budget bounds Member(index) calls, including one extra lookahead per branch and empty/failed probes.",
                    ["max_hops"] = request.MaxHops, ["max_nodes"] = request.MaxNodes,
                    ["trace_node_budget_scope"] = "Endpoint inspections in the one trace, including alias resolutions; branch members and pair endpoints are separate bounded evidence." },
                ["scope"] = "Explicit BRAN state, explicitly selected P-point pairs, and directed native PCREF links only. No implicit traversal through another P-point of a component, no HREF/TREF endpoint inference, and no geometric edges. Matching boundaries and one UI callback are not a database lock. Read completeness, proximity, reciprocal PCREF and observed cycles do not establish engineering connectivity or compatibility."
            };
            ConnectivitySnapshotErrors.BoundFields(result);
            return result;
        });
    }

    private static Dictionary<string, object> ConnectivityContext(Project project, MDB mdb, string sessionId, string bridgeBuild)
    {
        string nativeBuild = typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
        if (!StringComparer.Ordinal.Equals(bridgeBuild, nativeBuild))
            throw new InvalidOperationException("The adapter bridge build does not match the current native module MVID.");
        var context = ReadEndpointContext(project, mdb);
        context["session_id"] = sessionId; context["bridge_build"] = nativeBuild;
        if (ConnectivitySnapshotRequest.ContextFields.Any(key => !context.ContainsKey(key) || !(context[key] is string text) || string.IsNullOrWhiteSpace(text)))
            throw new InvalidOperationException("The native typed context is incomplete.");
        return context;
    }

    private static bool ConnectivityContextEqual(Dictionary<string, object> first, Dictionary<string, object> second) =>
        first != null && second != null && ConnectivitySnapshotRequest.ContextFields.All(key =>
            first.ContainsKey(key) && second.ContainsKey(key) && Equals(first[key], second[key]));

    private Dictionary<string, object> ConnectivityItem(string kind, int index, object requested,
        Func<Dictionary<string, object>> read, string sessionId, string bridgeBuild, Dictionary<string, object> expected, Project expectedProject, MDB expectedMdb)
    {
        ThrowIfNativeFaulted();
        Project project = null;
        MDB mdb = null;
        Dictionary<string, object> before = null, after = null, data = null;
        bool? sameProject = null, sameMdb = null, agrees = null;
        var errors = new List<Dictionary<string, object>>();
        try
        {
            project = NativeRead(() => Project.CurrentProject);
            mdb = NativeRead(() => MDB.CurrentMDB);
            before = ConnectivityContext(project, mdb, sessionId, bridgeBuild);
            if (!ReferenceEquals(project, expectedProject) || !ReferenceEquals(mdb, expectedMdb) || !ConnectivityContextEqual(before, expected))
                throw new InvalidOperationException("Batch context changed; this item was not read.");
            data = read();
        }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            errors.Add(new Dictionary<string, object> { ["code"] = "read_failed", ["message"] = ConnectivitySnapshotErrors.Text(error.Message) });
        }
        try
        {
            Project projectAfter = NativeRead(() => Project.CurrentProject);
            MDB mdbAfter = NativeRead(() => MDB.CurrentMDB);
            after = ConnectivityContext(projectAfter, mdbAfter, sessionId, bridgeBuild);
            sameProject = ReferenceEquals(project, projectAfter); sameMdb = ReferenceEquals(mdb, mdbAfter);
            agrees = sameProject == true && sameMdb == true && ConnectivityContextEqual(before, after);
        }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            errors.Add(new Dictionary<string, object> { ["code"] = "context_after_unavailable", ["message"] = ConnectivitySnapshotErrors.Text(error.Message) });
        }
        bool readComplete = data != null && data.TryGetValue("read_complete", out object value) && Equals(value, true);
        return new Dictionary<string, object> { ["kind"] = kind, ["index"] = index, ["requested"] = requested,
            ["read_complete"] = readComplete, ["complete"] = readComplete && agrees == true && errors.Count == 0,
            ["data"] = data, ["read_errors"] = errors,
            ["context"] = new Dictionary<string, object> { ["before"] = before, ["after"] = after,
                ["same_project_instance"] = sameProject, ["same_mdb_instance"] = sameMdb, ["boundary_agreement"] = agrees } };
    }

    private Dictionary<string, object> ConnectivityBranch(string reference, int limit)
    {
        DbElement branch = ResolveElementByType(reference, "BRAN");
        if (!IsUsable(branch) || NativeRead(() => branch.DbType) != DbType.Design || ElementType(branch) != "BRAN")
            throw new InvalidOperationException("The explicit branch did not resolve a native Design BRAN.");
        var identity = ElementIdentity(branch);
        var attributes = ReadAttributesOnUiThread(branch, ConnectivityBranchAttributes.ToList());
        var members = new List<Dictionary<string, object>>();
        var seen = new HashSet<string>();
        var errors = new List<Dictionary<string, object>>();
        int probes = 0, lookaheadProbes = 0;
        bool? more = false;
        try
        {
            for (int ordinal = 1; ordinal <= limit + 1; ++ordinal)
            {
                ++probes;
                if (ordinal > limit) ++lookaheadProbes;
                DbElement member = NativeRead(() => branch.Member(ordinal));
                if (member == null || NativeRead(() => member.IsNull)) break;
                // The extra slot is a presence-only sentinel. Do not read its
                // owner or identity: it is outside the returned-row budget.
                if (ordinal > limit) { more = true; break; }
                if (!IsUsable(member) || ElementRef(NativeRead(() => member.Owner)) != (string)identity["refno"])
                    throw new InvalidOperationException("Member is unavailable or has an unexpected owner.");
                var row = ElementIdentity(member);
                string key = row["refno"] + "|" + row["type"];
                if (!seen.Add(key)) throw new InvalidOperationException("Repeated typed direct member.");
                row["sibling_index"] = ordinal; row["identity_key"] = key + "|" + ordinal;
                row["complete"] = true; members.Add(row);
            }
        }
        catch (Exception error)
        {
            ThrowIfNativeFaulted(); more = null;
            errors.Add(new Dictionary<string, object> { ["code"] = "member_read_failed", ["message"] = ConnectivitySnapshotErrors.Text(error.Message) });
        }
        bool complete = Equals(attributes["success"], true) && more == false && errors.Count == 0;
        return new Dictionary<string, object> { ["element"] = identity, ["attributes"] = attributes, ["members"] = members,
            ["member_limit"] = limit, ["returned_count"] = members.Count, ["has_more"] = more, ["truncated"] = more == true,
            ["member_probe_limit"] = limit + 1, ["member_probe_count"] = probes, ["lookahead_probe_count"] = lookaheadProbes,
            ["read_complete"] = complete, ["read_errors"] = errors };
    }

    private static Dictionary<string, object> ConnectivityPairRead(ConnectivityPair pair)
    {
        var first = ConnectionReadPairEndpoint(pair.First.Refno, pair.First.PointNumber, pair.First.ExpectedType);
        var second = ConnectionReadPairEndpoint(pair.Second.Refno, pair.Second.PointNumber, pair.Second.ExpectedType);
        return new Dictionary<string, object> {
            ["endpoints"] = new Dictionary<string, object> { ["first"] = first, ["second"] = second },
            ["position_comparison"] = ConnectionComparePairPositions(first, second, pair.ToleranceMm),
            ["direction_relationship"] = ConnectionComparePairDirections(first, second),
            ["pcref_correspondence"] = ConnectionComparePairReferences(first, ConnectionPairElementIdentity(first), pair.First.PointNumber,
                second, ConnectionPairElementIdentity(second), pair.Second.PointNumber),
            ["tolerance"] = new Dictionary<string, object> { ["value"] = pair.ToleranceMm, ["unit"] = "mm" },
            ["read_complete"] = Equals(first["complete"], true) && Equals(second["complete"], true)
        };
    }
}
