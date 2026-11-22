using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Aveva.Core.Database;
using Aveva.Core.Geometry;
using Aveva.Core.Geometry.Implementation;
using Aveva.Core3D.Clasher;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private static readonly string[] NativeClashCategories = { "clashes", "touches", "clearances", "not_proven" };
    private const string NativeClashUnits = "SDK native (unverified)";
    private const int NativeClashInputNodeLimit = 256;
    private const int NativeClashInputDepthLimit = 8;

    public object CheckNativeClashes(IList<string> firstRefnos, IList<string> secondRefnos,
        double clearance, int resultLimit, bool dryRun)
    {
        string[] first = NativeClashReferences(firstRefnos, "first_refnos");
        string[] second = NativeClashReferences(secondRefnos, "second_refnos");
        if (first.Intersect(second, StringComparer.OrdinalIgnoreCase).Any())
            throw new ArgumentException("first_refnos and second_refnos must not overlap.");
        if (double.IsNaN(clearance) || double.IsInfinity(clearance) || clearance < 0 || clearance > 1000000)
            throw new ArgumentOutOfRangeException("clearance", "clearance must be finite and between 0 and 1000000 native units (unverified).");
        if (resultLimit < 1 || resultLimit > 200) throw new ArgumentOutOfRangeException("resultLimit");
        var errors = new List<object>();
        var groups = new Dictionary<string, object>();
        foreach (string category in NativeClashCategories)
            groups[category] = new Dictionary<string, object>
            {
                ["native_count"] = null, ["returned_count"] = 0, ["complete"] = false,
                ["truncated"] = null, ["records"] = new List<object>(), ["read_errors"] = new List<object>()
            };
        var acceptedFirst = new List<object>();
        var acceptedSecond = new List<object>();
        var inputDatabases = new List<object>();
        string mapSuspensionReason, mapSuspensionMessage;
        NativeSpatialMapCheckAllowed(out mapSuspensionReason, out mapSuspensionMessage);
        var report = new Dictionary<string, object>
        {
            ["success"] = false, ["dry_run"] = dryRun, ["complete"] = false,
            ["preflight_complete"] = false, ["native_check_attempted"] = false,
            ["preflight_scope"] = "Bounded input and database metadata inspection; native map admission is separately reported and is suspended.",
            ["native_check_returned"] = false, ["native_check_result"] = null,
            ["no_clear_verdict"] = true, ["classification"] = "native_records_only",
            ["no_native_records"] = null, ["scope_complete"] = null, ["truncated"] = false,
            ["not_proven_count"] = null, ["returned_count"] = 0, ["result_limit"] = resultLimit,
            ["requested"] = new { first_refnos = first, second_refnos = second, clearance = clearance, clearance_unit = NativeClashUnits },
            ["accepted_inputs"] = new { first = acceptedFirst, second = acceptedSecond },
            ["input_node_limit"] = NativeClashInputNodeLimit, ["input_depth_limit"] = NativeClashInputDepthLimit,
            ["negative_depth_limit"] = NativeClashInputDepthLimit, ["input_nodes_read"] = 0,
            ["positive_geometry_count"] = 0, ["negative_geometry_count"] = 0,
            ["input_databases"] = inputDatabases, ["spatial_map_preflight_complete"] = false,
            ["spatial_map_check_attempted"] = false, ["spatial_map_check_completed"] = false,
            ["spatial_map_check_reason"] = null, ["spatial_map_check_message"] = null,
            ["spatial_map_database_resolution"] = "SDK CheckSpatialMap resolves the database by Number only; exact extract targeting is unverified.",
            ["spatial_map_extract_targeting_verified"] = false,
            ["input_spatial_maps_up_to_date"] = null, ["input_spatial_maps_accepted"] = null,
            ["spatial_map_admission_policy"] = new Dictionary<string, object>
            {
                ["mode"] = "suspended", ["accepted_statuses"] = new string[0],
                ["reason"] = mapSuspensionReason, ["message"] = mapSuspensionMessage,
                ["validation_scope"] = "Preview inspects bounded input metadata only; native map checking and actual clash execution are unavailable under the fixed suspension policy.",
                ["map_validity_verified"] = false
            },
            ["native_check_eligible"] = false,
            ["native_check_ineligibility_reason"] = null, ["reason"] = null,
            ["input_geometry_support"] = "Candidate EQUI/SUBE roots with bounded EQUI/SUBE, BOX/CYLI/CONE and NCYL/NBOX/NCON descendants; every root requires positive geometry. Primitive roots support preview only. Native geometry classification remains pending live validation.",
            ["geometry_classification_verified"] = false,
            ["override_policy"] = new Dictionary<string, object>
            {
                ["enabled"] = false,
                ["scope"] = "Override is not configured while native checking is suspended; the following reason/effect describe prior native probes only.",
                ["reason"] = "A controlled local E3D 2.1 comparison returned one positive clash for the same isolated EQUI pair only with OVERRIDE ON; without it, unrelated current-MDB map warnings blocked the PML check with (69,85).",
                ["effect"] = "The native override may bypass current-MDB spatial-map warnings beyond the admitted input databases. The bridge preserves all observed native warnings and never builds or modifies maps.",
                ["validation_scope"] = "Limited local PML evidence; native SDK geometry classification remains pending its own positive verification. No fresh native Command.Error capture is claimed."
            },
            ["negative_cut_evaluation_verified"] = false, ["options"] = null, ["obstructions"] = null,
            ["results"] = groups, ["errors"] = errors, ["messages"] = new List<object>(),
            ["message_scope"] = "Messages observed during this callback; exclusive operation attribution and complete ring-buffer coverage are not proven.",
            ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
            ["project_before"] = null, ["project_after"] = null, ["mdb_before"] = null, ["mdb_after"] = null,
            ["identity_unchanged"] = null, ["global_diagnostic_state_unchanged"] = null,
            ["global_diagnostic_state_may_change"] = true,
            ["observed_change_classes"] = new[] { "obstruction_list", "touch_overlap" },
            ["observed_change_classes_scope"] = "Observed in prior native Check runs; not a claim that this request changed those settings.",
            ["outcome_uncertain"] = false, ["retry_safe"] = false, ["cleanup"] = null,
            ["limitations"] = new[]
            {
                "No empty-result, NotProven or truncated-result response proves geometric clearance, minimum distance or overlap volume.",
                "Result getters materialize complete native arrays before result_limit bounds detail reads; this limit does not bound native computation or allocation.",
                "Prior native Check runs changed the global obstruction list and touch overlap. This request does not restore or certify global diagnostic state and does not edit database geometry, CE, camera or Drawlist.",
                "Input-database CheckSpatialMap and actual clash execution are suspended because the 2026-11-16 E3D21 native stack overflow remains unresolved. Preview retains bounded input metadata only; the bridge never invokes BuildSpatialMap.",
                "Fault barriers stop subsequent bridge-initiated SDK calls. SDK-internal cleanup and finalizers for constructors or partial arrays never returned to the bridge remain uncontrolled.",
                "A single UI callback is not a database lock; native Check has no demonstrated safe interrupt contract."
            }
        };
        bool began = false;
        int invocationState = 0;
        var completed = new TaskCompletionSource<bool>();
        try
        {
            if (dryRun ? IsCommandActive() : !(began = TryBeginCommand("native-clash-check")))
            {
                report["busy"] = true;
                NativeClashError(errors, "request", "busy", "E3D is busy or awaiting an interaction.");
                return report;
            }
            try
            {
                OnUi(delegate
                {
                    if (Interlocked.CompareExchange(ref invocationState, 1, 0) != 0) return false;
                    Exception callbackFailure = null;
                    try { NativeClashOnUi(first, second, clearance, resultLimit, dryRun, report, errors, groups, acceptedFirst, acceptedSecond); return true; }
                    catch (Exception error) { callbackFailure = error; throw; }
                    finally
                    {
                        if (callbackFailure == null) completed.TrySetResult(true);
                        else completed.TrySetException(callbackFailure);
                    }
                });
            }
            finally
            {
                if (Interlocked.CompareExchange(ref invocationState, 3, 0) == 1)
                    completed.Task.GetAwaiter().GetResult();
            }
            if (Volatile.Read(ref invocationState) == 3)
                throw new InvalidOperationException("The dispatcher did not start the callback; late execution was cancelled.");
        }
        catch (Exception error)
        {
            NativeClashError(errors, "request", "operation_failed", error.Message);
            report["success"] = false;
            report["complete"] = false;
            report["outcome_uncertain"] = Equals(report["native_check_attempted"], true);
            if (nativeFaultState != null && nativeFaultState.HasFault)
            {
                report["recovery_required"] = true;
                report["native_database_fault"] = nativeFaultState.Snapshot();
            }
        }
        finally { if (began) EndCommand(); }
        return report;
    }

    private void NativeClashOnUi(string[] first, string[] second, double clearance, int limit, bool dryRun,
        Dictionary<string, object> report, List<object> errors, Dictionary<string, object> groups,
        List<object> acceptedFirst, List<object> acceptedSecond)
    {
        var resources = new NativeClashResources();
        HashSet<Dictionary<string, object>> oldMessages;
        lock (messageLock) oldMessages = new HashSet<Dictionary<string, object>>(outputMessages);
        try
        {
            ThrowIfNativeFaulted();
            report["ce_before"] = ModelCurrentReference();
            NativeClashContext(report, "before");
            var rootIdentities = new HashSet<string>(StringComparer.Ordinal);
            var nodes = new HashSet<string>(StringComparer.Ordinal);
            var membership = new Dictionary<string, string>(StringComparer.Ordinal);
            var databases = new Dictionary<Tuple<int, int>, Db>();
            DbElement[] firstElements = NativeClashInputs(first, "first", acceptedFirst, rootIdentities, nodes, membership, databases, report);
            DbElement[] secondElements = NativeClashInputs(second, "second", acceptedSecond, rootIdentities, nodes, membership, databases, report);
            bool mapsAccepted = NativeClashSpatialMaps(databases, report);
            bool mapScopeSupported = ((List<object>)report["input_databases"]).Cast<Dictionary<string, object>>()
                .All(value => Equals(value["is_extract"], false));
            bool aggregateRoots = acceptedFirst.Concat(acceptedSecond).Cast<Dictionary<string, object>>()
                .All(value => NativeClashAggregateType((string)value["type"]));
            report["input_geometry_native_granularity_supported"] = aggregateRoots;
            report["spatial_map_input_scope_supported"] = mapScopeSupported;
            bool eligible = mapsAccepted && mapScopeSupported && aggregateRoots;
            string ineligibility = report["spatial_map_check_reason"] as string ?? (!mapsAccepted ? "spatial_map_not_accepted" :
                !mapScopeSupported ? "unsupported_spatial_map_extract_scope" :
                !aggregateRoots ? "unsupported_native_check_granularity" : null);
            report["native_check_eligible"] = eligible;
            report["native_check_ineligibility_reason"] = ineligibility;
            report["preflight_complete"] = true;
            if (!dryRun && !eligible)
            {
                report["reason"] = ineligibility;
                NativeClashError(errors, "preflight", ineligibility, report["spatial_map_check_message"] as string ?? (!mapsAccepted ?
                    "Every input database must report UPTODATE or NOWARNINGS under the experimental admission policy; no spatial map was built." :
                    !mapScopeSupported ? "The SDK resolves spatial-map databases by Number only; inputs identified by Db.IsExtract as extract databases remain preview-only." :
                    "Native Check admits EQUI/SUBE roots only. Direct primitive inputs failed a local positive probe and remain preview-only."));
            }
            else if (!dryRun)
            {
                ClashOptions options = NativeClashAcquire(() => ClashOptions.Create(), resources);
                ObstructionList obstruction = NativeClashAcquire(() => ObstructionList.Create(), resources);
                ClashSet set = NativeClashAcquire(() => ClashSet.Create(), resources);
                report["options"] = NativeClashConfigure(options, clearance, report);
                NativeWrite(() => obstruction.AllObstructions = false);
                NativeWrite(() => obstruction.RemoveAllObstructions());
                NativeWrite(() => obstruction.RemoveAllExclusions());
                NativeWrite(() => obstruction.AddObstructions(secondElements));
                bool all = NativeRead(() => obstruction.AllObstructions);
                DbElement[] actual = NativeRead(() => obstruction.Obstructions);
                DbElement[] exclusions = NativeRead(() => obstruction.Exclusions);
                var obstructionEvidence = new Dictionary<string, object>
                {
                    ["all_obstructions"] = all, ["obstruction_count"] = actual == null ? (object)null : actual.Length,
                    ["exclusions_count"] = exclusions == null ? (object)null : exclusions.Length,
                    ["exact_input_membership"] = false, ["actual_identities"] = null
                };
                report["obstructions"] = obstructionEvidence;
                if (actual == null || exclusions == null) throw new InvalidOperationException("The native obstruction scope could not be read completely.");
                if (actual.Length > 8 || exclusions.Length > 0 || all)
                    throw new InvalidOperationException("The native obstruction scope expanded, retained exclusions or enabled AllObstructions.");
                string[] actualKeys = actual.Select(NativeClashIdentityKey).ToArray();
                string[] expectedKeys = secondElements.Select(NativeClashIdentityKey).ToArray();
                bool exact = actualKeys.Length == expectedKeys.Length && actualKeys.Distinct(StringComparer.Ordinal).Count() == actualKeys.Length &&
                    new HashSet<string>(expectedKeys, StringComparer.Ordinal).SetEquals(actualKeys);
                obstructionEvidence["exact_input_membership"] = exact;
                obstructionEvidence["actual_identities"] = actualKeys;
                if (!exact) throw new InvalidOperationException("Native obstruction identities do not exactly match second_refnos.");
                Clasher engine = NativeRead(() => Clasher.Instance);
                if (engine == null) throw new InvalidOperationException("The native Clasher service is unavailable.");
                ThrowIfNativeFaulted();
                report["native_check_attempted"] = true;
                bool returned = engine.Check(firstElements, options, obstruction, set);
                report["native_check_returned"] = true;
                report["native_check_result"] = returned;
                ThrowIfNativeFaulted();
                if (!returned) NativeClashError(errors, "check", "native_check_false", "The native Check returned false; records are retained when readable without inferring the meaning of that boolean.");
                Func<Clash[]>[] getters = { () => set.Clashes, () => set.Touches, () => set.Clearances, () => set.NotProven };
                int remaining = limit;
                bool scope = true;
                for (int index = 0; index < NativeClashCategories.Length; index++)
                    NativeClashReadCategory(NativeClashCategories[index], getters[index], resources, membership,
                        (Dictionary<string, object>)groups[NativeClashCategories[index]], errors, ref remaining, ref scope);
                report["scope_complete"] = scope;
                var categoryValues = groups.Values.Cast<Dictionary<string, object>>().ToArray();
                bool allCounts = categoryValues.All(value => value["native_count"] is int);
                report["no_native_records"] = allCounts ? (object)categoryValues.All(value => (int)value["native_count"] == 0) : null;
                report["not_proven_count"] = ((Dictionary<string, object>)groups["not_proven"])["native_count"];
                report["truncated"] = categoryValues.Any(value => Equals(value["truncated"], true));
                report["returned_count"] = categoryValues.Sum(value => (int)value["returned_count"]);
            }
        }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            NativeClashError(errors, "check", "inspection_failed", error.Message);
            report["outcome_uncertain"] = Equals(report["native_check_attempted"], true) && !Equals(report["native_check_returned"], true);
        }
        finally
        {
            report["cleanup"] = resources.Cleanup(NativeWrite, () => nativeFaultState != null && nativeFaultState.HasFault);
            var observed = groups.Values.Cast<Dictionary<string, object>>().ToArray();
            report["returned_count"] = observed.Sum(value => ((List<object>)value["records"]).Count);
            report["truncated"] = observed.Any(value => Equals(value["truncated"], true));
            report["not_proven_count"] = ((Dictionary<string, object>)groups["not_proven"])["native_count"];
            lock (messageLock)
                report["messages"] = outputMessages.Where(value => !oldMessages.Contains(value)).Cast<object>().ToList();
        }
        ThrowIfNativeFaulted();
        try
        {
            report["ce_after"] = ModelCurrentReference();
            report["ce_unchanged"] = Equals(report["ce_before"], report["ce_after"]);
            NativeClashContext(report, "after");
            report["identity_unchanged"] = Equals(report["project_before"], report["project_after"]) && Equals(report["mdb_before"], report["mdb_after"]);
            if (!Equals(report["ce_unchanged"], true) || !Equals(report["identity_unchanged"], true))
                NativeClashError(errors, "context", "context_changed", "CE or project/MDB identity changed during observation.");
        }
        catch (Exception error) { ThrowIfNativeFaulted(); NativeClashError(errors, "context", "context_read_failed", error.Message); }
        bool cleanupComplete = Equals(((Dictionary<string, object>)report["cleanup"])["complete"], true);
        report["success"] = errors.Count == 0 && cleanupComplete && Equals(report["preflight_complete"], true) &&
            (dryRun || Equals(report["native_check_result"], true));
        report["complete"] = !dryRun && Equals(report["success"], true) && Equals(report["scope_complete"], true) &&
            groups.Values.Cast<Dictionary<string, object>>().All(value => Equals(value["complete"], true));
    }

    private static string[] NativeClashReferences(IList<string> values, string parameter)
    {
        if (values == null || values.Count < 1 || values.Count > 8) throw new ArgumentException(parameter + " requires 1 to 8 explicit references.");
        string[] normalized = values.Select(value => ModelEditInput.ExplicitReference(value, parameter)).ToArray();
        if (normalized.Distinct(StringComparer.OrdinalIgnoreCase).Count() != normalized.Length)
            throw new ArgumentException(parameter + " must contain unique references.");
        return normalized;
    }

    private static void NativeClashContext(Dictionary<string, object> report, string suffix)
    {
        Project project = NativeRead(() => Project.CurrentProject);
        MDB mdb = NativeRead(() => MDB.CurrentMDB);
        if (project == null || mdb == null) throw new InvalidOperationException("A project and MDB must be open.");
        report["project_" + suffix] = NativeRead(() => project.Name);
        report["mdb_" + suffix] = NativeRead(() => mdb.Name);
        if (string.IsNullOrWhiteSpace((string)report["project_" + suffix]) || string.IsNullOrWhiteSpace((string)report["mdb_" + suffix]))
            throw new InvalidOperationException("Project/MDB names were not available for identity verification.");
    }

    private static Dictionary<string, object> NativeClashIdentity(DbElement element)
    {
        if (element == null || !NativeRead(() => IsUsable(element))) throw new InvalidOperationException("The native endpoint is not a usable database element.");
        string reference = ModelReference(NativeRead(() => element.Ref));
        DbElementType type = NativeRead(() => element.GetElementType());
        if (type == null) throw new InvalidOperationException("The native element has no readable type.");
        return new Dictionary<string, object>
        {
            ["refno"] = reference, ["type"] = NativeRead(() => type.ShortName),
            ["name"] = NativeRead(() => element.GetAsString(DbAttributeInstance.NAME))
        };
    }

    private static string NativeClashIdentityKey(DbElement element)
    {
        Dictionary<string, object> identity = NativeClashIdentity(element);
        return (string)identity["refno"] + "|" + (string)identity["type"];
    }

    private static DbElement[] NativeClashInputs(string[] references, string group, List<object> output,
        HashSet<string> roots, HashSet<string> nodes, Dictionary<string, string> membership,
        Dictionary<Tuple<int, int>, Db> databases, Dictionary<string, object> report)
    {
        var elements = new List<DbElement>();
        foreach (string requested in references)
        {
            if (nodes.Count >= NativeClashInputNodeLimit) throw new InvalidOperationException("The combined input tree exceeds the request-wide 256-node budget.");
            DbElement element = NativeRead(() => ModelRequireElement(requested));
            if (NativeRead(() => element.DbType) != DbType.Design)
                throw new InvalidOperationException("Native clash inputs must belong to a Design database.");
            var identity = NativeClashIdentity(element);
            string key = (string)identity["refno"] + "|" + (string)identity["type"];
            if (!roots.Add(key)) throw new InvalidOperationException("Resolved input aliases identify the same element or overlap across groups.");
            string type = (string)identity["type"];
            if (!NativeClashAggregateType(type) && !NativeClashPositiveType(type))
                throw new InvalidOperationException("Only EQUI/SUBE roots or preview-only BOX/CYLI/CONE roots are admitted.");
            var negatives = new List<object>();
            var descendants = new List<object>();
            identity["requested_refno"] = requested;
            identity["group"] = group;
            identity["depth"] = 0;
            identity["negative_children"] = negatives;
            identity["descendants"] = descendants;
            identity["negative_membership_complete"] = false;
            identity["subtree_membership_complete"] = false;
            identity["native_check_granularity_supported"] = NativeClashAggregateType(type);
            output.Add(identity);
            NativeClashRegisterInputNode(element, identity, group, nodes, membership, databases, report);
            NativeClashChildren(element, (string)identity["refno"], type, group, 1, descendants, negatives, nodes, membership, databases, report);
            int positiveCount = (NativeClashPositiveType(type) ? 1 : 0) + descendants.Cast<Dictionary<string, object>>()
                .Count(value => NativeClashPositiveType((string)value["type"]));
            identity["descendant_count"] = descendants.Count;
            identity["subtree_node_count"] = descendants.Count + 1;
            identity["positive_geometry_count"] = positiveCount;
            identity["negative_geometry_count"] = negatives.Count;
            identity["aggregate_count"] = (NativeClashAggregateType(type) ? 1 : 0) + descendants.Cast<Dictionary<string, object>>()
                .Count(value => NativeClashAggregateType((string)value["type"]));
            if (positiveCount == 0) throw new InvalidOperationException("Every input root must contain at least one admitted positive primitive.");
            identity["negative_membership_complete"] = true;
            identity["subtree_membership_complete"] = true;
            elements.Add(element);
        }
        return elements.ToArray();
    }

    private static bool NativeClashAggregateType(string type) { return type == "EQUI" || type == "SUBE"; }
    private static bool NativeClashPositiveType(string type) { return type == "BOX" || type == "CYLI" || type == "CONE"; }
    private static bool NativeClashNegativeType(string type) { return type == "NCYL" || type == "NBOX" || type == "NCON"; }

    private static void NativeClashRegisterInputNode(DbElement element, Dictionary<string, object> identity, string group,
        HashSet<string> nodes, Dictionary<string, string> membership, Dictionary<Tuple<int, int>, Db> databases, Dictionary<string, object> report)
    {
        string key = (string)identity["refno"] + "|" + (string)identity["type"];
        if (!nodes.Add(key)) throw new InvalidOperationException("Input subtrees overlap, repeat an identity or contain a cycle; all roots and descendants must be disjoint.");
        membership.Add(key, group);
        report["input_nodes_read"] = nodes.Count;
        string type = (string)identity["type"];
        if (NativeClashPositiveType(type)) report["positive_geometry_count"] = (int)report["positive_geometry_count"] + 1;
        if (NativeClashNegativeType(type)) report["negative_geometry_count"] = (int)report["negative_geometry_count"] + 1;
        Db database = NativeRead(() => element.Db);
        if (database == null) throw new InvalidOperationException("An admitted element has no readable input database.");
        int number = NativeRead(() => database.Number);
        int extract = NativeRead(() => database.ExtractNumber);
        identity["database_number"] = number;
        identity["database_extract_number"] = extract;
        var databaseKey = Tuple.Create(number, extract);
        if (!databases.ContainsKey(databaseKey)) databases.Add(databaseKey, database);
    }

    private static void NativeClashChildren(DbElement parent, string ownerReference, string parentType, string group, int depth,
        List<object> descendants, List<object> negatives, HashSet<string> nodes, Dictionary<string, string> membership,
        Dictionary<Tuple<int, int>, Db> databases, Dictionary<string, object> report)
    {
        DbElement member = NativeRead(() => parent.FirstMember());
        while (member != null && !NativeRead(() => member.IsNull))
        {
            if (depth > NativeClashInputDepthLimit || nodes.Count >= NativeClashInputNodeLimit)
                throw new InvalidOperationException("Input membership exceeds the request-wide 256-node or depth-8 budget.");
            if (NativeRead(() => member.DbType) != DbType.Design)
                throw new InvalidOperationException("All clash input members must belong to a Design database.");
            var identity = NativeClashIdentity(member);
            string type = (string)identity["type"];
            bool negative = NativeClashNegativeType(type);
            if (NativeClashAggregateType(parentType) ? !NativeClashAggregateType(type) && !NativeClashPositiveType(type) : !negative)
                throw new InvalidOperationException("An aggregate may contain only admitted aggregates/positive primitives; a primitive may contain only NCYL/NBOX/NCON negative children.");
            DbElement owner = NativeRead(() => member.Owner);
            if (owner == null || ModelReference(NativeRead(() => owner.Ref)) != ownerReference)
                throw new InvalidOperationException("Child ownership differs from the observed member hierarchy.");
            identity["owner_refno"] = ownerReference;
            identity["depth"] = depth;
            identity["group"] = group;
            if (negative) { identity["cut_evaluation_verified"] = false; negatives.Add(identity); }
            descendants.Add(identity);
            NativeClashRegisterInputNode(member, identity, group, nodes, membership, databases, report);
            NativeClashChildren(member, (string)identity["refno"], type, group, depth + 1, descendants, negatives, nodes, membership, databases, report);
            member = NativeRead(() => member.Next());
        }
    }

    private static bool NativeClashSpatialMaps(Dictionary<Tuple<int, int>, Db> databases, Dictionary<string, object> report)
    {
        var output = (List<object>)report["input_databases"];
        var entries = databases.OrderBy(value => value.Key.Item1).ThenBy(value => value.Key.Item2).ToArray();
        foreach (var entry in entries)
            output.Add(new Dictionary<string, object>
            {
                ["number"] = entry.Key.Item1, ["extract_number"] = entry.Key.Item2,
                ["is_extract"] = NativeRead(() => entry.Value.IsExtract),
                ["is_primary"] = NativeRead(() => entry.Value.IsPrimary),
                ["check_attempted"] = false, ["check_completed"] = false, ["checked"] = false, ["status"] = null,
                ["status_value"] = null, ["up_to_date"] = null, ["accepted"] = null
            });
        string suspensionReason, suspensionMessage;
        if (!NativeSpatialMapCheckAllowed(out suspensionReason, out suspensionMessage))
        {
            report["spatial_map_check_reason"] = suspensionReason;
            report["spatial_map_check_message"] = suspensionMessage;
            foreach (Dictionary<string, object> row in output)
                row["reason"] = suspensionReason;
            return false;
        }
        SpatialMap spatialMap = NativeRead(() => SpatialMap.Instance);
        if (spatialMap == null) throw new InvalidOperationException("The native spatial-map service is unavailable.");
        bool current = true;
        bool accepted = true;
        for (int index = 0; index < entries.Length; index++)
        {
            var row = (Dictionary<string, object>)output[index];
            Db database = entries[index].Value;
            ThrowIfNativeFaulted();
            row["check_attempted"] = true;
            report["spatial_map_check_attempted"] = true;
            SpatialMapStatus status = NativeRead(() => spatialMap.CheckSpatialMap(database));
            row["checked"] = true;
            row["check_completed"] = true;
            row["status"] = status.ToString();
            row["status_value"] = (int)status;
            row["up_to_date"] = status == SpatialMapStatus.UPTODATE;
            bool statusAccepted = status == SpatialMapStatus.UPTODATE || status == SpatialMapStatus.NOWARNINGS;
            row["accepted"] = statusAccepted;
            current &= status == SpatialMapStatus.UPTODATE;
            accepted &= statusAccepted;
        }
        report["spatial_map_preflight_complete"] = true;
        report["spatial_map_check_completed"] = true;
        report["input_spatial_maps_up_to_date"] = current;
        report["input_spatial_maps_accepted"] = accepted;
        return accepted;
    }

    private static T NativeClashAcquire<T>(Func<T> factory, NativeClashResources resources) where T : class
    {
        ThrowIfNativeFaulted();
        T value = factory();
        resources.Register(value);
        ThrowIfNativeFaulted();
        if (value == null || !(value is IDisposable)) throw new InvalidOperationException("A native Clasher factory did not return an owned disposable wrapper.");
        return value;
    }

    private static Dictionary<string, object> NativeClashConfigure(ClashOptions options, double clearance, Dictionary<string, object> report)
    {
        NativeWrite(() => options.TouchGap = 0);
        NativeWrite(() => options.Clearance = clearance);
        NativeWrite(() => options.TouchOverlap = 0);
        NativeWrite(() => options.Override = true);
        NativeWrite(() => options.Midpoint = true);
        NativeWrite(() => options.Segments = false);
        NativeWrite(() => options.BranchCheckType = BranchCheck.ACHECK);
        NativeWrite(() => options.IncludeConnections = false);
        NativeWrite(() => options.IgnoreConnectionsWithSpecifications = false);
        NativeWrite(() => options.IncludeTouches = true);
        NativeWrite(() => options.HullDesign = false);
        NativeWrite(() => options.HullProduction = false);
        NativeWrite(() => options.LMITestExact = false);
        NativeWrite(() => options.LMIDisplay = LMIDisplayType.CELLS);
        NativeWrite(() => options.LMIDisplayExpandCells = 0);
        var read = new Dictionary<string, object>
        {
            ["touch_gap"] = NativeRead(() => options.TouchGap), ["clearance"] = NativeRead(() => options.Clearance),
            ["touch_overlap"] = NativeRead(() => options.TouchOverlap), ["override"] = NativeRead(() => options.Override),
            ["midpoint"] = NativeRead(() => options.Midpoint), ["segments"] = NativeRead(() => options.Segments),
            ["branch_check_type"] = NativeRead(() => options.BranchCheckType).ToString(),
            ["include_connections"] = NativeRead(() => options.IncludeConnections),
            ["ignore_connections_with_specifications"] = NativeRead(() => options.IgnoreConnectionsWithSpecifications),
            ["include_touches"] = NativeRead(() => options.IncludeTouches), ["hull_design"] = NativeRead(() => options.HullDesign),
            ["hull_production"] = NativeRead(() => options.HullProduction), ["lmi_test_exact"] = NativeRead(() => options.LMITestExact),
            ["lmi_display"] = NativeRead(() => options.LMIDisplay).ToString(),
            ["lmi_display_expand_cells"] = NativeRead(() => options.LMIDisplayExpandCells), ["distance_unit"] = NativeClashUnits
        };
        report["options"] = read;
        read["readback_verified"] = false;
        DbElementType[] ignored = NativeRead(() => options.IgnoreClashes);
        read["ignore_clashes_count"] = ignored == null ? (object)null : ignored.Length;
        read["ignore_clashes_before"] = null;
        read["ignore_clashes_after"] = null;
        read["ignore_clashes_clear_attempted"] = false;
        read["ignore_clashes_cleared"] = false;
        if (ignored == null || ignored.Length > 64 || ignored.Any(value => value == null))
            throw new InvalidOperationException("Native options have unreadable, null or over-limit IgnoreClashes entries.");
        string[] beforeIgnored = ignored.Select(value => NativeRead(() => value.ShortName)).ToArray();
        read["ignore_clashes_before"] = beforeIgnored;
        read["ignore_clashes"] = beforeIgnored;
        if (ignored.Length > 0)
        {
            // Within removes the observed ignore entries from this request's
            // newly created options only. Never configure global PML state.
            read["ignore_clashes_clear_attempted"] = true;
            NativeWrite(() => options.Within(ignored));
        }
        DbElementType[] afterIgnored = NativeRead(() => options.IgnoreClashes);
        read["ignore_clashes_count"] = afterIgnored == null ? (object)null : afterIgnored.Length;
        if (afterIgnored == null || afterIgnored.Length > 64 || afterIgnored.Any(value => value == null))
            throw new InvalidOperationException("Native IgnoreClashes readback is null, unreadable or over its limit after Within.");
        string[] afterNames = afterIgnored.Select(value => NativeRead(() => value.ShortName)).ToArray();
        read["ignore_clashes_after"] = afterNames;
        read["ignore_clashes"] = afterNames;
        read["ignore_clashes_cleared"] = afterIgnored.Length == 0;
        if (afterIgnored.Length != 0)
            throw new InvalidOperationException("The request-local Within operation did not clear all observed IgnoreClashes entries.");
        var expected = new Dictionary<string, object>
        {
            ["touch_gap"] = 0.0, ["clearance"] = clearance, ["touch_overlap"] = 0.0, ["override"] = true,
            ["midpoint"] = true, ["segments"] = false, ["branch_check_type"] = "ACHECK", ["include_connections"] = false,
            ["ignore_connections_with_specifications"] = false, ["include_touches"] = true, ["hull_design"] = false,
            ["hull_production"] = false, ["lmi_test_exact"] = false, ["lmi_display"] = "CELLS", ["lmi_display_expand_cells"] = 0.0
        };
        if (expected.Any(pair => !Equals(read[pair.Key], pair.Value))) throw new InvalidOperationException("Native clash options did not preserve the explicitly requested settings.");
        read["readback_verified"] = true;
        return read;
    }

    private static void NativeClashReadCategory(string category, Func<Clash[]> getter, NativeClashResources resources,
        Dictionary<string, string> membership, Dictionary<string, object> group, List<object> errors, ref int remaining, ref bool scope)
    {
        var rows = (List<object>)group["records"];
        var groupErrors = (List<object>)group["read_errors"];
        try
        {
            ThrowIfNativeFaulted();
            Clash[] values = getter();
            resources.RegisterAll(values);
            ThrowIfNativeFaulted();
            if (values == null) throw new InvalidOperationException("The native result category returned a null array.");
            group["native_count"] = values.Length;
            int count = Math.Min(remaining, values.Length);
            group["truncated"] = count < values.Length;
            if (count < values.Length) scope = false;
            for (int index = 0; index < count; index++)
            {
                var row = new Dictionary<string, object>
                {
                    ["index"] = index, ["category"] = category, ["first"] = null, ["second"] = null,
                    ["native_type"] = null, ["instance_number"] = null, ["position"] = null,
                    ["scope_verified"] = false, ["complete"] = false, ["read_errors"] = new List<object>()
                };
                rows.Add(row);
                try
                {
                    Clash clash = values[index];
                    if (clash == null) throw new InvalidOperationException("The native category contains a null record.");
                    var first = NativeClashEndpoint(NativeRead(() => clash.First), membership);
                    row["first"] = first;
                    var second = NativeClashEndpoint(NativeRead(() => clash.Second), membership);
                    row["second"] = second;
                    bool belongs = Equals(first["group"], "first") && Equals(second["group"], "second") ||
                        Equals(first["group"], "second") && Equals(second["group"], "first");
                    row["scope_verified"] = belongs;
                    if (!belongs) scope = false;
                    row["native_type"] = NativeRead(() => clash.Type).ToString();
                    row["instance_number"] = NativeRead(() => clash.InstanceNumber);
                    // ClashPosition returns a fresh PositionImpl owning a
                    // native D3Point. ToD3Point is the audited managed-only
                    // accessor for that exact child, not a clone. Register the
                    // child before the post-getter barrier can throw; tracking
                    // Position itself would not suppress the child's finalizer.
                    ThrowIfNativeFaulted();
                    Position position = clash.ClashPosition;
                    object ownedPoint = null;
                    if (position != null)
                    {
                        PositionImpl implementation = position as PositionImpl;
                        if (implementation == null)
                        {
                            ThrowIfNativeFaulted();
                            throw new InvalidOperationException("The native clash position has an unsupported ownership implementation.");
                        }
                        ownedPoint = implementation.ToD3Point();
                        resources.Register(ownedPoint);
                    }
                    ThrowIfNativeFaulted();
                    if (position == null) throw new InvalidOperationException("The native clash position is unavailable.");
                    if (ownedPoint == null || !(ownedPoint is IDisposable))
                        throw new InvalidOperationException("The native clash position did not expose an owned disposable D3Point.");
                    double[] xyz = { NativeRead(() => position.X), NativeRead(() => position.Y), NativeRead(() => position.Z) };
                    if (xyz.Any(value => double.IsNaN(value) || double.IsInfinity(value))) throw new InvalidOperationException("The native clash position is not finite.");
                    row["position"] = new { xyz = xyz, coordinate_frame = NativeClashUnits, unit = NativeClashUnits };
                    row["complete"] = true;
                    if (!belongs) NativeClashError(errors, category + "[" + index + "]", "unexpected_endpoint", "The native endpoints do not form one verified first-group and second-group pair; the record was retained.");
                }
                catch (Exception error)
                {
                    ThrowIfNativeFaulted();
                    scope = false;
                    NativeClashError((List<object>)row["read_errors"], category + "[" + index + "]", "record_read_failed", error.Message);
                    NativeClashError(groupErrors, category + "[" + index + "]", "record_read_failed", error.Message);
                    NativeClashError(errors, category + "[" + index + "]", "record_read_failed", error.Message);
                }
            }
            remaining -= count;
            group["returned_count"] = rows.Count;
            group["complete"] = !Equals(group["truncated"], true) && groupErrors.Count == 0;
        }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            scope = false;
            NativeClashError(groupErrors, category, "category_read_failed", error.Message);
            NativeClashError(errors, category, "category_read_failed", error.Message);
        }
        finally { group["returned_count"] = rows.Count; }
    }

    private static Dictionary<string, object> NativeClashEndpoint(DbElement element, Dictionary<string, string> membership)
    {
        var identity = NativeClashIdentity(element);
        string group;
        identity["group"] = membership.TryGetValue((string)identity["refno"] + "|" + (string)identity["type"], out group) ? group : "outside";
        return identity;
    }

    private static void NativeClashError(List<object> errors, string path, string code, string message)
    {
        errors.Add(new Dictionary<string, object> { ["path"] = path, ["code"] = code, ["message"] = message });
    }
}
