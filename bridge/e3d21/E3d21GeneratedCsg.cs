using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.CompilerServices;
using Aveva.Core.Database;
using Aveva.Core.Maths.Geometry;
using Aveva.Core3D.CSG;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private const int GeneratedCsgMaximumTrees = 16;
    private const int GeneratedCsgMaximumItems = 256;
    private const int GeneratedCsgMaximumArrayValues = 4096;
    private const int GeneratedCsgMaximumTotalArrayValues = 65536;
    private const int GeneratedCsgMaximumFacetsPerGroup = 1024;
    private const int GeneratedCsgMaximumLoopsPerFacet = 128;
    private const int GeneratedCsgMaximumEntriesPerLoopArray = 1024;
    private const int GeneratedCsgMaximumTotalFacets = 2048;
    private const int GeneratedCsgMaximumTotalLoops = 4096;
    private const int GeneratedCsgMaximumTotalFacetCoordinates = 16384;
    private const int GeneratedCsgMaximumInputNodes = 64;
    private const int GeneratedCsgMaximumDirectMembers = 32;
    private static readonly GeneratedCsgIsolation generatedCsgIsolation = new GeneratedCsgIsolation();
    // OnUi must scope this to its existing allowNativeFault recovery argument.
    [ThreadStatic] private static bool generatedCsgRecoveryPermitted;

    private static bool IsGeneratedCsgQuarantined()
    {
        return generatedCsgIsolation.Active;
    }

    private static void ThrowIfGeneratedCsgQuarantined()
    {
        if (!generatedCsgRecoveryPermitted && generatedCsgIsolation.Active)
            throw new GeneratedCsgQuarantineException();
    }

    // Managed-only: this remains available when all ordinary SDK calls are gated.
    public object GetGeneratedCsgQuarantineState()
    {
        return generatedCsgIsolation.Snapshot();
    }

    public object GetGeneratedCsg(string refno, int maxTrees, int maxItems)
    {
        string requested = ModelEditInput.ExplicitReference(refno, "refno");
        if (maxTrees < 1 || maxTrees > GeneratedCsgMaximumTrees)
            throw new ArgumentOutOfRangeException("maxTrees", "maxTrees must be between 1 and 16.");
        if (maxItems < 1 || maxItems > GeneratedCsgMaximumItems)
            throw new ArgumentOutOfRangeException("maxItems", "maxItems must be between 1 and 256.");
        ThrowIfGeneratedCsgQuarantined();
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

        var errors = new List<object>();
        var trees = new List<object>();
        var facetBudget = new GeneratedCsgFacetBudget();
        var counts = new Dictionary<string, object>
        {
            ["total_tree_count"] = null, ["observed_tree_count"] = 0, ["observed_item_count"] = 0L,
            ["returned_tree_count"] = 0, ["returned_item_count"] = 0, ["copied_array_value_count"] = 0,
            ["item_count_scope"] = "inspected_trees_only", ["facet_groups"] = null
        };
        var context = new Dictionary<string, object>
        {
            ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
            ["project_before"] = null, ["project_after"] = null, ["mdb_before"] = null, ["mdb_after"] = null,
            ["identity_unchanged"] = null
        };
        var options = new Dictionary<string, object>
        {
            ["before"] = null, ["requested"] = null, ["after"] = null, ["restored"] = null,
            ["comparison_policy"] = "enabled_geometry_options_v1",
            ["effective_before"] = null, ["effective_after"] = null, ["effective_restored"] = null,
            ["raw_restored"] = null, ["raw_mismatched_fields"] = new List<object>(),
            ["effective_mismatched_fields"] = new List<object>(), ["inactive_fields"] = new List<object>(),
            ["comparison_scope"] = "Compare all eleven geometry switches/level and translucency when its associated geometry is enabled. Only translucency disabled in both snapshots is excluded from effective restoration; raw observations and differences are retained, without claiming raw or hidden SDK state restoration."
        };
        var report = new Dictionary<string, object>
        {
            ["operation"] = "generated_csg", ["requested_refno"] = requested, ["refno"] = null,
            ["source"] = null, ["source_after"] = null, ["source_unchanged"] = null,
            ["source_subtree_before"] = null, ["source_subtree_after"] = null, ["source_subtree_unchanged"] = null,
            ["success"] = false, ["complete"] = false, ["available"] = false,
            ["generated_csg"] = true, ["generated_geometry_available"] = false,
            ["native_tessellation"] = false, ["cuts_applied"] = false, ["post_boolean_mesh_verified"] = false,
            ["raw_data_layout_verified"] = false, ["transform_convention_verified"] = false,
            ["facet_group_policy"] = "sdk_typed_v1",
            ["units"] = "sdk_raw_unverified", ["coordinate_frame"] = "sdk_item_transform_output_unverified",
            ["single_dispatch"] = true, ["consistency"] = "single_ui_dispatch",
            ["consistency_scope"] = "One UI callback; no database lock or immutable snapshot.",
            ["trees"] = trees, ["counts"] = counts, ["context"] = context, ["options"] = options,
            ["limits"] = new Dictionary<string, object>
            {
                ["max_trees"] = maxTrees, ["max_items"] = maxItems,
                ["max_array_values"] = GeneratedCsgMaximumArrayValues,
                ["max_total_array_values"] = GeneratedCsgMaximumTotalArrayValues,
                ["max_input_nodes"] = GeneratedCsgMaximumInputNodes,
                ["max_direct_members"] = GeneratedCsgMaximumDirectMembers, ["max_input_depth"] = 2,
                ["facet_groups"] = new Dictionary<string, object>
                {
                    ["max_facets_per_group"] = GeneratedCsgMaximumFacetsPerGroup,
                    ["max_loops_per_facet"] = GeneratedCsgMaximumLoopsPerFacet,
                    ["max_entries_per_loop_array"] = GeneratedCsgMaximumEntriesPerLoopArray,
                    ["max_total_facets"] = GeneratedCsgMaximumTotalFacets,
                    ["max_total_loops"] = GeneratedCsgMaximumTotalLoops,
                    ["max_total_coordinate_values"] = GeneratedCsgMaximumTotalFacetCoordinates
                }
            },
            ["truncated"] = false, ["truncation_reasons"] = new List<object>(), ["errors"] = errors,
            ["generation_attempted"] = false, ["generation_returned"] = false,
            ["native_work_bounded"] = false, ["native_cancellation_supported"] = false,
            ["resources"] = null, ["native_database_fault"] = null,
            ["generated_csg_quarantine"] = generatedCsgIsolation.Snapshot(),
            ["limitations"] = new[]
            {
                "Counts bound returned descriptors, not SDK generation, allocation or execution time; the SDK materializes complete arrays before the bridge can inspect their lengths.",
                "Raw positive, negative and unknown CSG descriptors are preserved without decoding Boolean operations or generating meshes.",
                "Facet groups are copied through the public SDK interfaces when implemented by the item. Coordinates, loop order and normals are preserved without unit, frame, topology or material-boundary verification.",
                "Transform.AsArray12 order, source/output frame and raw dimensional units are unverified. Limits are explicitly untransformed.",
                "Only factory-created option wrappers are owned and disposed. Cached item transforms/limits are borrowed and never recursively disposed.",
                "Design input membership is bounded to 64 nodes, 32 members per parent and depth two. Primitive children admit flat NBOX/NCYL/NCON leaves; PANE additionally admits PLOO/PAVE contours. Referenced nozzle catalogue complexity is not bounded by this Design membership check.",
                "The bridge cannot control SDK-internal cleanup or finalizers for constructors and arrays never returned to it. Returned graphs are retained if a fault prevents healthy completion."
            }
        };
        var resources = new GeneratedCsgResources();
        try
        {
            OnUi(delegate
            {
                string stage = "preflight";
                try
                {
                    ThrowIfGeneratedCsgQuarantined();
                    if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
                    Project projectBefore;
                    MDB mdbBefore;
                    GeneratedCsgReadContext(context, "before", out projectBefore, out mdbBefore);
                    DbElement element = NativeRead(() => ResolveElement(requested));
                    var identity = GeneratedCsgIdentity(element);
                    report["source"] = identity;
                    report["refno"] = identity["refno"];
                    if (NativeRead(() => element.DbType) != DbType.Design)
                        throw new InvalidOperationException("Generated CSG inputs must belong to a Design database.");
                    string type = (string)identity["type"];
                    if (type != "CYLI" && type != "BOX" && type != "CONE" && type != "DISH" && type != "NOZZ" && type != "PANE")
                        throw new InvalidOperationException("Generated CSG currently admits one CYLI, BOX, CONE, DISH, NOZZ or PANE element; aggregate roots are not admitted.");
                    stage = "source_subtree_before";
                    List<object> subtreeBefore = GeneratedCsgInputSubtree(element, identity);
                    report["source_subtree_before"] = subtreeBefore;

                    stage = "builder";
                    CSGTreeBuilder builder = NativeRead(() => CSGTreeBuilder.Instance);
                    if (builder == null) throw new InvalidOperationException("The public CSGTreeBuilder.Instance is unavailable; the bridge does not initialize it.");
                    report["available"] = true;
                    stage = "options_before";
                    CSGTreeBuilderOptions requestedOptions = GeneratedCsgAcquireOptions(builder, resources);
                    var before = GeneratedCsgOptions(requestedOptions);
                    options["before"] = before;
                    var effectiveBefore = GeneratedCsgEffectiveOptions(before);
                    options["effective_before"] = effectiveBefore;
                    GeneratedCsgConfigure(requestedOptions);
                    var requestedSnapshot = GeneratedCsgOptions(requestedOptions);
                    options["requested"] = requestedSnapshot;
                    var expectedSnapshot = new Dictionary<string, object>(before)
                    {
                        ["center_line"] = false, ["tube"] = true, ["insulation"] = false, ["obstruction"] = false,
                        ["holes"] = true, ["solid_only"] = false, ["pline"] = false, ["wire_line"] = false,
                        ["insulation_translucency"] = 0, ["obstruction_translucency"] = 0
                    };
                    if (!GeneratedCsgOptionsEqual(expectedSnapshot, requestedSnapshot))
                        throw new InvalidOperationException("The public option wrapper did not preserve the requested generation configuration.");

                    stage = "generation";
                    CSGTree[] generated;
                    ThrowIfNativeFaulted();
                    report["generation_attempted"] = true;
                    try
                    {
                        generated = builder.GetGeometry(element, requestedOptions);
                        // Hold the returned graph before a callback fault can discard it.
                        resources.Hold(generated);
                        report["generation_returned"] = true;
                        ThrowIfNativeFaulted();
                    }
                    catch (Exception failure)
                    {
                        generatedCsgIsolation.Quarantine("generation", "generation_failed", failure.Message, (string)identity["refno"]);
                        throw new GeneratedCsgQuarantineException("CSG generation did not complete safely; shared export state is uncertain.", failure);
                    }

                    stage = "options_after";
                    try
                    {
                        CSGTreeBuilderOptions restoredOptions = GeneratedCsgAcquireOptions(builder, resources);
                        var after = GeneratedCsgOptions(restoredOptions);
                        options["after"] = after;
                        var effectiveAfter = GeneratedCsgEffectiveOptions(after);
                        options["effective_after"] = effectiveAfter;
                        var rawMismatches = GeneratedCsgOptionMismatches(before, after);
                        var effectiveMismatches = GeneratedCsgOptionMismatches(effectiveBefore, effectiveAfter);
                        options["raw_restored"] = rawMismatches.Count == 0;
                        options["raw_mismatched_fields"] = rawMismatches;
                        options["effective_mismatched_fields"] = effectiveMismatches;
                        var inactive = (List<object>)options["inactive_fields"];
                        if (Equals(before["insulation"], false) && Equals(after["insulation"], false)) inactive.Add("insulation_translucency");
                        if (Equals(before["obstruction"], false) && Equals(after["obstruction"], false)) inactive.Add("obstruction_translucency");
                        bool restored = effectiveMismatches.Count == 0;
                        options["effective_restored"] = restored;
                        options["restored"] = restored;
                        if (!restored) throw new InvalidOperationException("Effective public CSG export options differ from the snapshot taken before generation.");
                    }
                    catch (Exception failure)
                    {
                        generatedCsgIsolation.Quarantine("options_after", "option_restoration_unverified", failure.Message, (string)identity["refno"]);
                        throw new GeneratedCsgQuarantineException("CSG export option restoration could not be verified.", failure);
                    }

                    stage = "trees";
                    if (generated == null) throw new InvalidOperationException("The SDK returned a null CSG tree array.");
                    counts["total_tree_count"] = generated.Length;
                    if (generated.Length > maxTrees) GeneratedCsgTruncate(report, "tree_limit");
                    var budget = new GeneratedCsgBudget();
                    int returnedItems = 0;
                    long observedItems = 0;
                    for (int treeIndex = 0; treeIndex < generated.Length && treeIndex < maxTrees; treeIndex++)
                    {
                        ThrowIfNativeFaulted();
                        if (returnedItems >= maxItems) { GeneratedCsgTruncate(report, "item_limit"); break; }
                        CSGTree tree = generated[treeIndex];
                        if (tree == null) throw new InvalidOperationException("The generated tree array contains a null tree.");
                        stage = "trees[" + treeIndex + "]";
                        DbElement source = NativeRead(() => tree.Element);
                        var sourceIdentity = GeneratedCsgIdentity(source);
                        var items = new List<object>();
                        var treeReport = new Dictionary<string, object>
                        {
                            ["index"] = treeIndex, ["source"] = sourceIdentity,
                            ["source_matches_requested"] = Equals(sourceIdentity["refno"], identity["refno"]),
                            ["item_count"] = null, ["items"] = items, ["complete"] = false
                        };
                        trees.Add(treeReport);
                        counts["returned_tree_count"] = trees.Count;
                        CSGItem[] generatedItems = NativeRead(() => tree.Items);
                        resources.Hold(generatedItems);
                        if (generatedItems == null) throw new InvalidOperationException("The SDK returned a null CSG item array.");
                        treeReport["item_count"] = generatedItems.Length;
                        observedItems += generatedItems.Length;
                        counts["observed_item_count"] = observedItems;
                        counts["observed_tree_count"] = treeIndex + 1;
                        int take = Math.Min(generatedItems.Length, maxItems - returnedItems);
                        if (take < generatedItems.Length) GeneratedCsgTruncate(report, "item_limit");
                        for (int itemIndex = 0; itemIndex < take; itemIndex++)
                        {
                            stage = "trees[" + treeIndex + "].items[" + itemIndex + "]";
                            var itemReport = GeneratedCsgItemReport(itemIndex);
                            items.Add(itemReport);
                            returnedItems++;
                            counts["returned_item_count"] = returnedItems;
                            try { GeneratedCsgReadItem(generatedItems[itemIndex], itemReport, budget, facetBudget, resources); }
                            catch (Exception failure)
                            {
                                GeneratedCsgError((List<object>)itemReport["errors"], stage, "item_read_failed", failure.Message);
                                throw;
                            }
                            finally { counts["copied_array_value_count"] = budget.Copied; }
                        }
                        treeReport["complete"] = take == generatedItems.Length;
                    }
                    if (generated.Length == 0 || returnedItems == 0)
                        throw new InvalidOperationException("The admitted primitive returned no generated CSG items.");
                    report["generated_geometry_available"] = true;

                    stage = "context_after";
                    Project projectAfter;
                    MDB mdbAfter;
                    GeneratedCsgReadContext(context, "after", out projectAfter, out mdbAfter);
                    context["ce_unchanged"] = Equals(context["ce_before"], context["ce_after"]);
                    context["identity_unchanged"] = ReferenceEquals(projectBefore, projectAfter) && ReferenceEquals(mdbBefore, mdbAfter) &&
                        Equals(context["project_before"], context["project_after"]) && Equals(context["mdb_before"], context["mdb_after"]);
                    var identityAfter = GeneratedCsgIdentity(element);
                    report["source_after"] = identityAfter;
                    report["source_unchanged"] = GeneratedCsgIdentityEqual(identity, identityAfter);
                    stage = "source_subtree_after";
                    List<object> subtreeAfter = GeneratedCsgInputSubtree(element, identityAfter);
                    report["source_subtree_after"] = subtreeAfter;
                    bool subtreeUnchanged = subtreeBefore.Count == subtreeAfter.Count;
                    for (int index = 0; subtreeUnchanged && index < subtreeBefore.Count; index++)
                        subtreeUnchanged = GeneratedCsgIdentityEqual((Dictionary<string, object>)subtreeBefore[index], (Dictionary<string, object>)subtreeAfter[index]) &&
                            Equals(((Dictionary<string, object>)subtreeBefore[index])["parent_refno"], ((Dictionary<string, object>)subtreeAfter[index])["parent_refno"]);
                    report["source_subtree_unchanged"] = subtreeUnchanged;
                    if (!Equals(context["ce_unchanged"], true) || !Equals(context["identity_unchanged"], true) || !Equals(report["source_unchanged"], true) || !subtreeUnchanged)
                        throw new InvalidOperationException("CE, project/MDB or source identity changed during the generated CSG observation.");
                }
                catch (GeneratedCsgBudgetException failure)
                {
                    GeneratedCsgTruncate(report, failure.Code);
                    GeneratedCsgError(errors, stage, failure.Code, failure.Message);
                }
                catch (Exception failure)
                {
                    GeneratedCsgError(errors, stage, generatedCsgIsolation.Active ? "generated_csg_quarantined" : "inspection_failed", failure.Message);
                    ThrowIfNativeFaulted();
                    ThrowIfGeneratedCsgQuarantined();
                }
                finally
                {
                    report["resources"] = resources.Cleanup(NativeWrite,
                        () => generatedCsgIsolation.Active || (nativeFaultState != null && nativeFaultState.HasFault),
                        failure => generatedCsgIsolation.Quarantine("cleanup", "option_dispose_failed", failure.Message, report["refno"] as string));
                    if (generatedCsgIsolation.Active || (nativeFaultState != null && nativeFaultState.HasFault))
                        generatedCsgIsolation.Retain(resources.Holdings);
                }
                report["success"] = errors.Count == 0 && !Equals(report["truncated"], true) && Equals(options["restored"], true) &&
                    Equals(context["ce_unchanged"], true) && Equals(context["identity_unchanged"], true) &&
                    Equals(report["source_unchanged"], true) && Equals(report["source_subtree_unchanged"], true) && Equals(report["generated_geometry_available"], true) &&
                    Equals(((Dictionary<string, object>)report["resources"])["complete"], true);
                report["complete"] = report["success"];
                return report;
            });
        }
        catch (Exception failure)
        {
            if (!(failure is NativeDatabaseFaultException) && !(failure is GeneratedCsgQuarantineException)) throw;
            report["success"] = false;
            report["complete"] = false;
            if (errors.Count == 0) GeneratedCsgError(errors, "dispatch", failure is NativeDatabaseFaultException ? "native_database_fault" : "generated_csg_quarantined", failure.Message);
            if (nativeFaultState != null && nativeFaultState.HasFault) report["native_database_fault"] = nativeFaultState.Snapshot();
        }
        counts["facet_groups"] = facetBudget.Snapshot();
        report["generated_csg_quarantine"] = generatedCsgIsolation.Snapshot();
        return report;
    }

    private static void GeneratedCsgReadContext(Dictionary<string, object> context, string suffix, out Project project, out MDB mdb)
    {
        context["ce_" + suffix] = NativeRead(() => ModelCurrentReference());
        project = NativeRead(() => Project.CurrentProject);
        mdb = NativeRead(() => MDB.CurrentMDB);
        if (project == null || mdb == null) throw new InvalidOperationException("An open project and MDB are required.");
        Project observedProject = project;
        MDB observedMdb = mdb;
        string projectName = NativeRead(() => observedProject.Name);
        string mdbName = NativeRead(() => observedMdb.Name);
        if (string.IsNullOrWhiteSpace(projectName) || string.IsNullOrWhiteSpace(mdbName) || projectName.Length > 4096 || mdbName.Length > 4096)
            throw new InvalidOperationException("Project/MDB names are required for context verification.");
        context["project_" + suffix] = projectName;
        context["mdb_" + suffix] = mdbName;
    }

    private static Dictionary<string, object> GeneratedCsgIdentity(DbElement element)
    {
        if (element == null || NativeRead(() => element.IsNull) || !NativeRead(() => element.IsValid))
            throw new InvalidOperationException("The generated CSG source is not a valid database element.");
        int[] reference = NativeRead(() => element.Ref);
        if (!ModelEditInput.IsReferencePair(reference)) throw new InvalidOperationException("The source has no valid numeric reference.");
        DbElementType type = NativeRead(() => element.GetElementType());
        if (type == null) throw new InvalidOperationException("The source element type is unavailable.");
        string typeName = NativeRead(() => type.ShortName);
        if (string.IsNullOrWhiteSpace(typeName)) typeName = NativeRead(() => type.Name);
        string name = NativeRead(() => element.GetAsString(DbAttributeInstance.NAME));
        if (string.IsNullOrWhiteSpace(typeName) || typeName.Length > 64 || name == null || name.Length > 4096)
            throw new InvalidOperationException("Source type/name identity is unavailable or exceeds the bounded string limit.");
        return new Dictionary<string, object> { ["refno"] = ModelReference(reference), ["name"] = name, ["type"] = typeName };
    }

    private static bool GeneratedCsgIdentityEqual(Dictionary<string, object> left, Dictionary<string, object> right)
    {
        return Equals(left["refno"], right["refno"]) && Equals(left["name"], right["name"]) && Equals(left["type"], right["type"]);
    }

    private static List<object> GeneratedCsgInputSubtree(DbElement element, Dictionary<string, object> identity)
    {
        var output = new List<object>();
        var seen = new HashSet<string>(StringComparer.Ordinal) { (string)identity["refno"] };
        GeneratedCsgInputChildren(element, identity, 0, output, seen);
        return output;
    }

    private static void GeneratedCsgInputChildren(DbElement parent, Dictionary<string, object> parentIdentity, int parentDepth,
        List<object> output, HashSet<string> seen)
    {
        string parentType = (string)parentIdentity["type"];
        for (int ordinal = 1; ordinal <= GeneratedCsgMaximumDirectMembers + 1; ordinal++)
        {
            int memberNumber = ordinal;
            DbElement child = NativeRead(() => parent.Member(memberNumber));
            if (child == null || NativeRead(() => child.IsNull)) return;
            if (ordinal > GeneratedCsgMaximumDirectMembers || seen.Count >= GeneratedCsgMaximumInputNodes)
                throw new InvalidOperationException("The Design input subtree exceeds the 32-member-per-parent or 64-node admission budget; generation was not admitted.");
            if (parentDepth >= 2)
                throw new InvalidOperationException("The Design input subtree exceeds the two-level admission depth.");
            var identity = GeneratedCsgIdentity(child);
            string childType = (string)identity["type"];
            bool negative = childType == "NBOX" || childType == "NCYL" || childType == "NCON";
            bool admitted = (parentDepth == 0 && negative) ||
                (parentType == "PANE" && childType == "PLOO") || (parentType == "PLOO" && childType == "PAVE");
            if (!admitted) throw new InvalidOperationException("The Design input contains an unsupported or non-leaf negative/contour member.");
            if (NativeRead(() => child.DbType) != DbType.Design)
                throw new InvalidOperationException("Every admitted input member must belong to a Design database.");
            DbElement owner = NativeRead(() => child.Owner);
            if (owner == null || NativeRead(() => owner.IsNull) || !NativeRead(() => owner.IsValid))
                throw new InvalidOperationException("An input member has no usable owner identity.");
            int[] ownerReference = NativeRead(() => owner.Ref);
            if (!ModelEditInput.IsReferencePair(ownerReference) || ModelReference(ownerReference) != (string)parentIdentity["refno"])
                throw new InvalidOperationException("An input member's observed owner differs from its enumerated parent.");
            if (!seen.Add((string)identity["refno"])) throw new InvalidOperationException("Input membership repeats an identity or contains a cycle.");
            identity["depth"] = parentDepth + 1;
            identity["parent_refno"] = parentIdentity["refno"];
            identity["sibling_index"] = ordinal;
            output.Add(identity);
            GeneratedCsgInputChildren(child, identity, parentDepth + 1, output, seen);
        }
    }

    private static CSGTreeBuilderOptions GeneratedCsgAcquireOptions(CSGTreeBuilder builder, GeneratedCsgResources resources)
    {
        ThrowIfNativeFaulted();
        CSGTreeBuilderOptions value = resources.Own(builder.CreateCSGTreeBuilderOptions());
        ThrowIfNativeFaulted();
        if (value == null || !(value is IDisposable))
            throw new InvalidOperationException("The public options factory must return an owned disposable option wrapper.");
        return value;
    }

    private static Dictionary<string, object> GeneratedCsgOptions(CSGTreeBuilderOptions options)
    {
        return new Dictionary<string, object>
        {
            ["hard_obstructions"] = NativeRead(() => options.HardObstructions),
            ["soft_obstructions"] = NativeRead(() => options.SoftObstructions),
            ["level"] = NativeRead(() => options.Level), ["center_line"] = NativeRead(() => options.CenterLine),
            ["tube"] = NativeRead(() => options.Tube), ["insulation"] = NativeRead(() => options.Insulation),
            ["insulation_translucency"] = NativeRead(() => options.InsulationTranslucency),
            ["obstruction"] = NativeRead(() => options.Obstruction),
            ["obstruction_translucency"] = NativeRead(() => options.ObstructionTranslucency),
            ["holes"] = NativeRead(() => options.Holes), ["solid_only"] = NativeRead(() => options.SolidOnly),
            ["pline"] = NativeRead(() => options.Pline), ["wire_line"] = NativeRead(() => options.WireLine)
        };
    }

    private static void GeneratedCsgConfigure(CSGTreeBuilderOptions options)
    {
        // Preserve native level/obstruction selection; request solid tube
        // descriptors including holes, without auxiliary wire geometry. Explicit
        // zero values prevent passing indeterminate disabled translucency data.
        NativeWrite(() => options.CenterLine = false);
        NativeWrite(() => options.Tube = true);
        NativeWrite(() => options.Insulation = false);
        NativeWrite(() => options.Obstruction = false);
        NativeWrite(() => options.InsulationTranslucency = 0);
        NativeWrite(() => options.ObstructionTranslucency = 0);
        NativeWrite(() => options.Holes = true);
        NativeWrite(() => options.SolidOnly = false);
        NativeWrite(() => options.Pline = false);
        NativeWrite(() => options.WireLine = false);
    }

    private static bool GeneratedCsgOptionsEqual(Dictionary<string, object> left, Dictionary<string, object> right)
    {
        if (left.Count != right.Count) return false;
        foreach (KeyValuePair<string, object> entry in left)
        {
            object value;
            if (!right.TryGetValue(entry.Key, out value) || !Equals(entry.Value, value)) return false;
        }
        return true;
    }

    private static Dictionary<string, object> GeneratedCsgEffectiveOptions(Dictionary<string, object> raw)
    {
        var effective = new Dictionary<string, object>(raw);
        // The installed SDK forwards native DES_Export translucency getters even
        // when the associated geometry is disabled. Raw values are observations;
        // only the corresponding enabled geometry gives them an effective role.
        if (Equals(raw["insulation"], false)) effective["insulation_translucency"] = null;
        if (Equals(raw["obstruction"], false)) effective["obstruction_translucency"] = null;
        return effective;
    }

    private static List<object> GeneratedCsgOptionMismatches(Dictionary<string, object> left, Dictionary<string, object> right)
    {
        var mismatches = new List<object>();
        foreach (KeyValuePair<string, object> entry in left)
        {
            object value;
            if (!right.TryGetValue(entry.Key, out value) || !Equals(entry.Value, value)) mismatches.Add(entry.Key);
        }
        return mismatches;
    }

    private static Dictionary<string, object> GeneratedCsgItemReport(int index)
    {
        return new Dictionary<string, object>
        {
            ["index"] = index, ["gml_item"] = null, ["type"] = null, ["rdata"] = null, ["idata"] = null,
            ["rdata_length"] = null, ["idata_length"] = null, ["transform_as_array12"] = null,
            ["local_limits"] = null, ["facet_group"] = null, ["translucency"] = null,
            ["complete"] = false, ["errors"] = new List<object>()
        };
    }

    private static void GeneratedCsgReadItem(CSGItem item, Dictionary<string, object> output, GeneratedCsgBudget budget,
        GeneratedCsgFacetBudget facetBudget, GeneratedCsgResources resources)
    {
        if (item == null) throw new InvalidOperationException("The SDK returned a null CSG item.");
        CSGType type = NativeRead(() => item.Type);
        output["type"] = new Dictionary<string, object>
        {
            ["name"] = type.ToString(), ["value"] = (int)type, ["known"] = Enum.IsDefined(typeof(CSGType), type)
        };
        output["gml_item"] = NativeRead(() => item.GMLItem);
        output["translucency"] = NativeRead(() => item.Translucency);
        double[] rdata = NativeRead(() => item.RData);
        output["rdata_length"] = rdata == null ? (object)null : rdata.Length;
        output["rdata"] = budget.Copy(rdata, -1);
        int[] idata = NativeRead(() => item.IData);
        output["idata_length"] = idata == null ? (object)null : idata.Length;
        output["idata"] = budget.Copy(idata);
        ThrowIfNativeFaulted();
        D3Transform transform = resources.Borrow(item.Transform);
        ThrowIfNativeFaulted();
        if (!ReferenceEquals(transform, null)) output["transform_as_array12"] = budget.Copy(NativeRead(() => transform.AsArray12()), 12);
        ThrowIfNativeFaulted();
        D3Limits limits = resources.Borrow(item.Limits);
        ThrowIfNativeFaulted();
        if (!ReferenceEquals(limits, null))
        {
            double[] minimum = budget.Copy(new[] { NativeRead(() => limits.MinX), NativeRead(() => limits.MinY), NativeRead(() => limits.MinZ) }, 3);
            double[] maximum = budget.Copy(new[] { NativeRead(() => limits.MaxX), NativeRead(() => limits.MaxY), NativeRead(() => limits.MaxZ) }, 3);
            for (int axis = 0; axis < 3; axis++)
                if (minimum[axis] > maximum[axis]) throw new InvalidOperationException("Untransformed local limits are inverted.");
            output["local_limits"] = new Dictionary<string, object> { ["min"] = minimum, ["max"] = maximum, ["frame"] = "untransformed" };
        }
        if (type == CSGType.CSG_FACET_GROUP || type == CSGType.CSG_NEG_FACET_GROUP)
        {
            CSGFacetGroup group = item as CSGFacetGroup;
            if (group == null) throw new InvalidOperationException("The facet-group item does not implement the SDK CSGFacetGroup interface.");
            output["facet_group"] = GeneratedCsgReadFacetGroup(group, facetBudget, resources);
        }
        output["complete"] = true;
    }

    private static Dictionary<string, object> GeneratedCsgReadFacetGroup(CSGFacetGroup group, GeneratedCsgFacetBudget budget,
        GeneratedCsgResources resources)
    {
        CSGFacet[] facets = GeneratedCsgReadFacetArray(() => group.Facets, resources, "Facets");
        if (facets.Length > GeneratedCsgMaximumFacetsPerGroup || facets.Length > GeneratedCsgMaximumTotalFacets - budget.Facets)
            throw new GeneratedCsgBudgetException("facet_count_limit", "SDK Facets exceed the per-group or request facet limit; the group is omitted.");
        int loopCount = 0, coordinateCount = 0;
        var facetRows = new List<object>(facets.Length);
        foreach (CSGFacet facet in facets)
        {
            if (facet == null) throw new InvalidOperationException("SDK Facets contains a null facet.");
            CSGLoop[] loops = GeneratedCsgReadFacetArray(() => facet.Loops, resources, "Loops");
            if (loops.Length > GeneratedCsgMaximumLoopsPerFacet || loops.Length > GeneratedCsgMaximumTotalLoops - budget.Loops - loopCount)
                throw new GeneratedCsgBudgetException("facet_loop_count_limit", "SDK Loops exceed the per-facet or request loop limit; the group is omitted.");
            loopCount += loops.Length;
            var loopRows = new List<object>(loops.Length);
            foreach (CSGLoop loop in loops)
            {
                if (loop == null) throw new InvalidOperationException("SDK Loops contains a null loop.");
                D3Point[] vertices = GeneratedCsgReadFacetArray(() => loop.Vertices, resources, "Vertices");
                int remaining = GeneratedCsgMaximumTotalFacetCoordinates - budget.Coordinates - coordinateCount;
                int vertexValues = GeneratedCsgFacetCoordinateCount(vertices.Length, remaining);
                D3Vector[] normals = GeneratedCsgReadFacetArray(() => loop.Normals, resources, "Normals");
                int normalValues = GeneratedCsgFacetCoordinateCount(normals.Length, remaining - vertexValues);
                loopRows.Add(new Dictionary<string, object>
                {
                    ["vertices"] = GeneratedCsgCopyTriples(vertices, point => new[] { NativeRead(() => point.X), NativeRead(() => point.Y), NativeRead(() => point.Z) }),
                    ["normals"] = GeneratedCsgCopyTriples(normals, normal => new[] { NativeRead(() => normal.X), NativeRead(() => normal.Y), NativeRead(() => normal.Z) })
                });
                coordinateCount += vertexValues + normalValues;
            }
            facetRows.Add(new Dictionary<string, object> { ["loops"] = loopRows });
        }
        budget.Groups++;
        budget.Facets += facets.Length;
        budget.Loops += loopCount;
        budget.Coordinates += coordinateCount;
        return new Dictionary<string, object>
        {
            ["source"] = "CSGFacetGroup.Facets",
            ["units"] = "sdk_raw_unverified",
            ["coordinate_frame"] = "sdk_facet_coordinates_unverified",
            ["facets"] = facetRows
        };
    }

    private static T[] GeneratedCsgReadFacetArray<T>(Func<T[]> getter, GeneratedCsgResources resources, string label)
    {
        T[] values = NativeRead(() =>
        {
            T[] returned = getter();
            // Borrow these cached arrays and their child wrappers. Keep the
            // returned graph before the fault barrier can throw.
            resources.Hold(returned);
            return returned;
        });
        if (values == null) throw new InvalidOperationException("The SDK returned a null " + label + " array.");
        return values;
    }

    private static int GeneratedCsgFacetCoordinateCount(int entries, int remaining)
    {
        if (entries > GeneratedCsgMaximumEntriesPerLoopArray)
            throw new GeneratedCsgBudgetException("facet_loop_entry_limit", "SDK Vertices or Normals exceed the per-loop array limit; the group is omitted.");
        int values = entries * 3;
        if (values > remaining)
            throw new GeneratedCsgBudgetException("facet_coordinate_value_limit", "SDK facet coordinates exceed the request limit; the group is omitted.");
        return values;
    }

    private static double[][] GeneratedCsgCopyTriples<T>(T[] array, Func<T, double[]> read) where T : class
    {
        var result = new double[array.Length][];
        for (int index = 0; index < array.Length; index++)
        {
            T value = array[index];
            if (ReferenceEquals(value, null)) throw new InvalidOperationException("An SDK facet coordinate wrapper is null.");
            double[] xyz = read(value);
            foreach (double component in xyz)
                if (double.IsNaN(component) || double.IsInfinity(component))
                    throw new InvalidOperationException("An SDK facet coordinate is nonfinite.");
            result[index] = xyz;
        }
        return result;
    }

    private sealed class GeneratedCsgFacetBudget
    {
        internal int Groups, Facets, Loops, Coordinates;
        internal Dictionary<string, object> Snapshot()
        {
            return new Dictionary<string, object>
            {
                ["returned_group_count"] = Groups, ["returned_facet_count"] = Facets,
                ["returned_loop_count"] = Loops, ["copied_coordinate_value_count"] = Coordinates
            };
        }
    }

    private static void GeneratedCsgError(List<object> errors, string stage, string code, string message)
    {
        string bounded = message ?? "";
        if (bounded.Length > 2048) bounded = bounded.Substring(0, 2048);
        errors.Add(new Dictionary<string, object> { ["stage"] = stage, ["code"] = code, ["message"] = bounded });
    }

    private static void GeneratedCsgTruncate(Dictionary<string, object> report, string reason)
    {
        report["truncated"] = true;
        var reasons = (List<object>)report["truncation_reasons"];
        if (!reasons.Contains(reason)) reasons.Add(reason);
    }

    private sealed class GeneratedCsgBudget
    {
        internal int Copied;
        private void Reserve(int length)
        {
            if (length > GeneratedCsgMaximumArrayValues)
                throw new GeneratedCsgBudgetException("array_value_limit", "An SDK data array exceeds the 4096-value output limit; no prefix is substituted.");
            if (length > GeneratedCsgMaximumTotalArrayValues - Copied)
                throw new GeneratedCsgBudgetException("total_array_value_limit", "The request exceeds the 65536-value copied-array budget.");
        }
        internal double[] Copy(double[] values, int exactLength)
        {
            if (values == null) return null;
            Reserve(values.Length);
            if (exactLength >= 0 && values.Length != exactLength) throw new InvalidOperationException("An SDK coordinate array has an unexpected length.");
            var copy = new double[values.Length];
            for (int index = 0; index < values.Length; index++)
            {
                double value = values[index];
                if (double.IsNaN(value) || double.IsInfinity(value)) throw new InvalidOperationException("An SDK data array contains a nonfinite number.");
                copy[index] = value;
            }
            Copied += copy.Length;
            return copy;
        }
        internal int[] Copy(int[] values)
        {
            if (values == null) return null;
            Reserve(values.Length);
            var copy = (int[])values.Clone();
            Copied += copy.Length;
            return copy;
        }
    }

    private sealed class GeneratedCsgBudgetException : InvalidOperationException
    {
        internal readonly string Code;
        internal GeneratedCsgBudgetException(string code, string message) : base(message) { Code = code; }
    }

    private sealed class GeneratedCsgResources
    {
        private sealed class Identity : IEqualityComparer<object>
        {
            public new bool Equals(object left, object right) { return ReferenceEquals(left, right); }
            public int GetHashCode(object value) { return RuntimeHelpers.GetHashCode(value); }
        }
        private readonly HashSet<object> seenOwned = new HashSet<object>(new Identity());
        private readonly HashSet<object> seenBorrowed = new HashSet<object>(new Identity());
        private readonly List<object> owned = new List<object>();
        internal readonly List<object> Holdings = new List<object>();
        internal T Own<T>(T value) where T : class
        {
            if (value != null && seenOwned.Add(value))
            {
                GC.SuppressFinalize(value);
                owned.Add(value);
                Holdings.Add(value);
            }
            return value;
        }
        internal T Borrow<T>(T value) where T : class
        {
            if (value != null && seenBorrowed.Add(value)) Holdings.Add(value);
            return value;
        }
        internal void Hold(object value) { if (value != null) Holdings.Add(value); }
        internal Dictionary<string, object> Cleanup(Action<Action> nativeWrite, Func<bool> faulted, Action<Exception> quarantine)
        {
            int attempted = 0, disposed = 0;
            var errors = new List<object>();
            for (int index = owned.Count - 1; index >= 0; index--)
            {
                if (faulted()) break;
                var disposable = owned[index] as IDisposable;
                if (disposable == null)
                {
                    errors.Add(new { code = "disposal_unavailable", index = index });
                    break;
                }
                try { attempted++; nativeWrite(() => disposable.Dispose()); disposed++; }
                catch (Exception failure)
                {
                    errors.Add(new { code = "dispose_failed", index = index, message = failure.Message });
                    quarantine(failure);
                    break;
                }
            }
            return new Dictionary<string, object>
            {
                ["owned_options_registered_count"] = owned.Count, ["finalization_suppressed_count"] = owned.Count,
                ["dispose_attempted_count"] = attempted, ["disposed_count"] = disposed,
                ["skipped_count"] = owned.Count - attempted, ["retained_owned_count"] = owned.Count - disposed,
                ["borrowed_wrapper_count"] = seenBorrowed.Count, ["borrowed_dispose_attempted_count"] = 0,
                ["complete"] = disposed == owned.Count, ["errors"] = errors,
                ["retention_reason"] = faulted() ? "fault_or_quarantine_no_further_sdk_calls" : null,
                ["scope"] = "Own only public-factory options, deduplicate by reference identity; borrowed item wrappers are never disposed."
            };
        }
    }

    private sealed class GeneratedCsgIsolation
    {
        private readonly object sync = new object();
        private bool active;
        private string stage, reason, message, sourceRefno, observedUtc;
        private readonly List<object> retained = new List<object>();
        internal bool Active { get { lock (sync) return active; } }
        internal void Quarantine(string observedStage, string observedReason, string observedMessage, string source)
        {
            lock (sync)
            {
                if (active) return;
                stage = observedStage; reason = observedReason; sourceRefno = source;
                message = observedMessage == null ? "" : observedMessage.Length <= 2048 ? observedMessage : observedMessage.Substring(0, 2048);
                observedUtc = DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture);
                active = true;
            }
        }
        internal void Retain(List<object> values)
        {
            lock (sync) retained.Add(values);
        }
        internal Dictionary<string, object> Snapshot()
        {
            lock (sync) return new Dictionary<string, object>
            {
                ["active"] = active, ["stage"] = stage, ["reason"] = reason, ["message"] = message,
                ["source_refno"] = sourceRefno, ["observed_utc"] = observedUtc, ["scope"] = "bridge_process",
                ["retry_safe"] = false, ["reset_available"] = false, ["retained_request_count"] = retained.Count,
                ["recovery_scope"] = "Only the existing exact SAVEWORK/DESIGN recovery dispatch may cross this latch; it does not clear it."
            };
        }
    }
}

internal sealed class GeneratedCsgQuarantineException : InvalidOperationException
{
    internal GeneratedCsgQuarantineException()
        : base("Generated CSG is quarantined because shared native export state is uncertain; do not retry the interrupted operation.") { }
    internal GeneratedCsgQuarantineException(string message, Exception inner) : base(message, inner) { }
}
