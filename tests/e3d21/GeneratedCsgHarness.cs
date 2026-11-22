using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Aveva.Core.Database;
using Aveva.Core.Maths.Geometry;
using Aveva.Core3D.CSG;
using E3d21AiBridge;

// Runs the production partial against instrumented managed SDK substitutes.
// It establishes orchestration/receipt guarantees, not native CSG correctness.
internal static class GeneratedCsgHarness
{
    private static int checks;
    private static void Check(bool condition, string message) { checks++; if (!condition) throw new Exception(message); }
    private static Dictionary<string, object> Row(object value) { return (Dictionary<string, object>)value; }
    private static List<object> List(Dictionary<string, object> value, string key) { return (List<object>)value[key]; }
    private static Dictionary<string, object> Run(int trees = 8, int items = 128) { return Row(Scenario.Context.GetGeneratedCsg("=1/2", trees, items)); }
    private static Dictionary<string, object> FirstItem(Dictionary<string, object> report) { return Row(List(Row(List(report, "trees")[0]), "items")[0]); }
    private static bool Error(Dictionary<string, object> report, string code) { return List(report, "errors").Cast<Dictionary<string, object>>().Any(value => Equals(value["code"], code)); }
    private static void Throws<T>(Action action, string message) where T : Exception
    {
        try { action(); } catch (T) { Check(true, message); return; }
        throw new Exception("Expected " + typeof(T).Name + ": " + message);
    }

    private static void InputsAndAdmission()
    {
        Scenario.Reset();
        foreach (Action request in new Action[]
        {
            () => Scenario.Context.GetGeneratedCsg(null, 1, 1), () => Scenario.Context.GetGeneratedCsg("CE", 1, 1),
            () => Scenario.Context.GetGeneratedCsg("=1/2\nQ NAME", 1, 1), () => Run(0), () => Run(17), () => Run(items: 0), () => Run(items: 257)
        }) Throws<ArgumentException>(request, "Input validation is pure and bounded.");
        Check(Scenario.Trace.Count == 0 && Scenario.Dispatches == 0, "Invalid inputs never dispatch or access SDK state.");
        for (int busyAt = 1; busyAt <= 2; busyAt++)
        {
            Scenario.Reset(); Scenario.BusyAt = busyAt;
            if (busyAt == 1) Throws<InvalidOperationException>(() => Run(), "Busy preflight rejects before dispatch.");
            else Check(Equals(Run()["success"], false), "Busy UI recheck does not generate CSG.");
            Check(Scenario.Trace.Count == 0 && Scenario.GenerationCalls == 0, "Both busy gates prevent native reads.");
        }
        foreach (string type in new[] { "CYLI", "BOX", "CONE", "DISH", "NOZZ", "PANE" })
        {
            Scenario.Reset(); Scenario.Element.TypeValue.ShortValue = type;
            Check(Equals(Run()["success"], true) && Scenario.GenerationCalls == 1, "The six explicit primitive classes are admitted: " + type);
        }
        foreach (string type in new[] { "EQUI", "SUBE", "VALV", "NCYL", "WORLD" })
        {
            Scenario.Reset(); Scenario.Element.TypeValue.ShortValue = type; var report = Run();
            Check(Error(report, "inspection_failed") && Scenario.GenerationCalls == 0 && !Scenario.Trace.Any(path => path.StartsWith("builder.")), "Aggregate/other roots never access the CSG builder: " + type);
        }
        Scenario.Reset(); Scenario.Element.DbTypeValue = DbType.Catalogue;
        Check(Equals(Run()["success"], false) && Scenario.GenerationCalls == 0, "Non-Design elements are rejected before generation.");
        Scenario.Reset(); CSGTreeBuilder.Value = null;
        Check(Equals(Run()["available"], false) && Scenario.OptionCalls == 0, "An absent public builder is reported without initialization.");
    }

    private static void ShapesAndOwnership()
    {
        Scenario.Reset();
        var input = Scenario.Item;
        input.TypeValue = CSGType.CSG_NEG_BOX;
        Scenario.Tree.ItemsValue = new CSGItem[] { input, new FakeItem("unknown") { TypeValue = (CSGType)9876, RDataValue = null, IDataValue = new int[0], TransformValue = null, LimitsValue = null } };
        var report = Run(); var options = Row(report["options"]); var context = Row(report["context"]); var counts = Row(report["counts"]); var item = FirstItem(report);
        Check(Equals(report["success"], true) && Equals(report["complete"], true) && List(report, "errors").Count == 0, "A healthy descriptor read completes.");
        Check(Equals(context["ce_unchanged"], true) && Equals(context["identity_unchanged"], true) && Equals(report["source_unchanged"], true), "Context and source identity are checked after extraction.");
        Check(Equals(Row(report["source"])["refno"], "=1/2") && Equals(Row(report["source"])["type"], "CYLI"), "Requested source identity is preserved.");
        Check(Equals(Row(item["type"])["name"], "CSG_NEG_BOX") && Equals(Row(item["type"])["value"], 51), "Negative type identity remains explicit.");
        Check(!ReferenceEquals(item["rdata"], input.RDataValue) && ((double[])item["rdata"]).SequenceEqual(input.RDataValue), "Real data are copied without interpretation.");
        Check(!ReferenceEquals(item["idata"], input.IDataValue) && ((int[])item["idata"]).SequenceEqual(input.IDataValue), "Integer data are copied without interpretation.");
        Check(((double[])item["transform_as_array12"]).Length == 12 && Equals(Row(item["local_limits"])["frame"], "untransformed"), "Transforms and untransformed limits retain explicit conventions.");
        var unknown = Row(List(Row(List(report, "trees")[0]), "items")[1]);
        Check(Equals(Row(unknown["type"])["known"], false) && Equals(Row(unknown["type"])["name"], "9876"), "Unknown enum values remain opaque descriptors.");
        Check(unknown["rdata"] == null && ((int[])unknown["idata"]).Length == 0 && unknown["transform_as_array12"] == null && unknown["local_limits"] == null && Equals(unknown["complete"], true), "Null and empty SDK values remain distinct without invented geometry.");
        foreach (string flag in new[] { "native_tessellation", "cuts_applied", "post_boolean_mesh_verified", "raw_data_layout_verified", "transform_convention_verified", "native_work_bounded", "native_cancellation_supported" })
            Check(Equals(report[flag], false), "No unsupported claim is made: " + flag);
        Check(Equals(counts["returned_item_count"], 2) && Equals(counts["observed_item_count"], 2L) && Equals(counts["returned_tree_count"], 1), "Returned and observed descriptor counts are exact.");
        Check(Equals(options["restored"], true) && Scenario.Options.Count == 2 && Scenario.Options.All(value => value.DisposeCount == 1), "Both owned option wrappers are disposed once on healthy completion.");
        var before = Row(options["before"]); var after = Row(options["after"]); var requested = Row(options["requested"]);
        Check(before.Count == 13 && before.All(pair => Equals(pair.Value, after[pair.Key])), "All thirteen public options are snapshotted and compared.");
        Check(Equals(requested["holes"], true) && Equals(requested["tube"], true) && Equals(requested["center_line"], false) && Equals(requested["level"], before["level"]) && Equals(requested["hard_obstructions"], before["hard_obstructions"]), "Requested geometry switches are explicit while native level and obstruction selection are preserved.");
        Check(input.TransformValue.DisposeCount == 0 && input.LimitsValue.DisposeCount == 0 && Equals(Row(report["resources"])["borrowed_dispose_attempted_count"], 0), "Cached transforms and limits are borrowed and never disposed.");
        Scenario.Reset(); Scenario.ReuseOptions = true; report = Run();
        Check(Equals(report["success"], true) && Equals(Row(report["resources"])["owned_options_registered_count"], 1) && Scenario.Options[0].DisposeCount == 1, "Repeated owned references are deduplicated by reference identity.");
        Scenario.Reset(); Scenario.Tree.ElementValue = new DbElement("child") { Reference = new[] { 1, 9 }, TypeValue = new DbElementType { ShortValue = "NBOX" }, NameValue = "/CHILD" };
        report = Run();
        Check(Equals(report["success"], true) && Equals(Row(List(report, "trees")[0])["source_matches_requested"], false) && Equals(Row(Row(List(report, "trees")[0])["source"])["refno"], "=1/9"), "Every tree retains its own observed source without silently replacing it with the request identity.");
    }

    private static DbElement AddChild(DbElement parent, int number, string type = "NBOX")
    {
        var child = new DbElement("member" + number) { Reference = new[] { 1, number }, TypeValue = new DbElementType { ShortValue = type }, NameValue = "/MEMBER" + number, OwnerValue = parent };
        parent.Children.Add(child);
        return child;
    }

    private static void BoundedInputMembership()
    {
        Scenario.Reset();
        for (int index = 0; index < 32; index++) AddChild(Scenario.Element, 100 + index);
        var report = Run();
        Check(Equals(report["success"], true) && List(report, "source_subtree_before").Count == 32 && Equals(report["source_subtree_unchanged"], true), "Exactly 32 flat leaf negative members are admitted and observed again after extraction.");
        Scenario.Reset();
        for (int index = 0; index < 33; index++) AddChild(Scenario.Element, 100 + index);
        report = Run();
        Check(Scenario.GenerationCalls == 0 && Scenario.Trace.Contains("source.member33") && !Scenario.Trace.Contains("member132.type"), "The 33rd member rejects generation before reading its remaining properties.");
        foreach (string unsupported in new[] { "CYLI", "EQUI", "NSCY", "PLOO" })
        {
            Scenario.Reset(); AddChild(Scenario.Element, 100, unsupported); report = Run();
            Check(Scenario.GenerationCalls == 0 && Equals(report["success"], false), "Unsupported primitive children fail closed: " + unsupported);
        }
        Scenario.Reset(); var negative = AddChild(Scenario.Element, 100); AddChild(negative, 101);
        Check(Equals(Run()["success"], false) && Scenario.GenerationCalls == 0, "Nested negatives are refused before CSG generation.");
        Scenario.Reset(); var wrongOwner = AddChild(Scenario.Element, 100); wrongOwner.OwnerValue = new DbElement("foreign") { Reference = new[] { 1, 999 } };
        Check(Equals(Run()["success"], false) && Scenario.GenerationCalls == 0, "A member owner mismatch refuses generation.");
        Scenario.Reset(); AddChild(Scenario.Element, 100); AddChild(Scenario.Element, 100);
        Check(Equals(Run()["success"], false) && Scenario.GenerationCalls == 0, "Repeated/cyclic member identity refuses generation.");
        Scenario.Reset(); Scenario.Element.TypeValue.ShortValue = "PANE";
        var loop = AddChild(Scenario.Element, 100, "PLOO");
        for (int index = 0; index < 4; index++) AddChild(loop, 200 + index, "PAVE");
        report = Run();
        Check(Equals(report["success"], true) && List(report, "source_subtree_before").Count == 5, "A bounded PANE/PLOO/PAVE leaf contour is admitted.");
        AddChild(loop.Children[0], 300, "PAVE");
        Check(Equals(Run()["success"], false) && Scenario.GenerationCalls == 1, "A vertex descendant violates the depth and leaf policy without another generation.");
        Scenario.Reset(); Scenario.Element.TypeValue.ShortValue = "PANE";
        var first = AddChild(Scenario.Element, 100, "PLOO"); var second = AddChild(Scenario.Element, 101, "PLOO");
        for (int index = 0; index < 31; index++) AddChild(first, 200 + index, "PAVE");
        for (int index = 0; index < 30; index++) AddChild(second, 300 + index, "PAVE");
        report = Run();
        Check(Equals(report["success"], true) && List(report, "source_subtree_before").Count == 63, "A 64-node bounded Design input includes its root in the admission count.");
        AddChild(second, 330, "PAVE"); report = Run();
        Check(Equals(report["success"], false) && Scenario.GenerationCalls == 1, "A 65th Design node rejects generation without enumerating an unbounded hierarchy.");
        Scenario.Reset(); Scenario.OnTouch = path => { if (path == "item.limits.maxz") AddChild(Scenario.Element, 100); }; report = Run();
        Check(Equals(report["success"], false) && Equals(report["source_subtree_unchanged"], false), "Changed input membership prevents a complete observation.");
    }

    private static void BudgetsAndFiniteData()
    {
        Scenario.Reset(); Scenario.Generated = new CSGTree[] { Scenario.Tree, new FakeTree("extra") { ElementValue = Scenario.Element, ItemsValue = new CSGItem[] { new FakeItem("extra") } } };
        var report = Run(trees: 1);
        Check(Equals(report["truncated"], true) && Equals(report["success"], false) && Equals(report["complete"], false) && List(report, "trees").Count == 1, "The tree budget returns a bounded incomplete receipt.");
        Check(Equals(Row(report["counts"])["total_tree_count"], 2) && !Scenario.Trace.Any(path => path.StartsWith("extra.")), "Uninspected trees have no SDK getters invoked.");
        Scenario.Reset(); Scenario.Tree.ItemsValue = new CSGItem[] { Scenario.Item, new FakeItem("extra") };
        report = Run(items: 1);
        Check(Equals(Row(report["counts"])["observed_item_count"], 2L) && Equals(Row(report["counts"])["returned_item_count"], 1) && !Scenario.Trace.Any(path => path.StartsWith("extra.")), "Item budgeting counts the inspected array without accessing omitted items.");
        Scenario.Reset(); Scenario.Item.RDataValue = new double[4097]; report = Run();
        Check(Error(report, "array_value_limit") && FirstItem(report)["rdata"] == null && Equals(FirstItem(report)["rdata_length"], 4097), "An oversized array is omitted instead of silently replaced with a prefix.");
        Check(!Scenario.Trace.Contains("item.idata") && Scenario.Options.All(value => value.DisposeCount == 1), "Budget rejection stops inspection while healthy owned options are released.");
        Scenario.Reset(); Scenario.Tree.ItemsValue = Enumerable.Range(0, 17).Select(index => (CSGItem)new FakeItem("many" + index) { RDataValue = new double[4096], IDataValue = new int[0], TransformValue = null, LimitsValue = null }).ToArray();
        report = Run();
        Check(Error(report, "total_array_value_limit") && Equals(Row(report["counts"])["copied_array_value_count"], 65536) && List(Row(List(report, "trees")[0]), "items").Count == 17, "The combined copied-array budget never exceeds 65536 values, including a marked incomplete terminal item.");
        foreach (double bad in new[] { double.NaN, double.PositiveInfinity, double.NegativeInfinity })
        {
            Scenario.Reset(); Scenario.Item.RDataValue = new[] { bad }; report = Run();
            Check(Equals(report["success"], false) && FirstItem(report)["rdata"] == null && !Scenario.Trace.Contains("item.idata") && !Equals(Row(report["generated_csg_quarantine"])["active"], true), "Nonfinite raw data are rejected without further item reads or inventing a native fault.");
        }
        Scenario.Reset(); Scenario.Item.TransformValue.Values = new double[11]; report = Run();
        Check(Equals(FirstItem(report)["complete"], false) && FirstItem(report)["transform_as_array12"] == null && !Scenario.Trace.Contains("item.limits"), "AsArray12 must return exactly twelve finite values.");
        Scenario.Reset(); Scenario.Item.TransformValue.Values[4] = double.NaN; report = Run();
        Check(FirstItem(report)["transform_as_array12"] == null && Error(report, "inspection_failed"), "A nonfinite transform is not serialized.");
        Scenario.Reset(); Scenario.Item.LimitsValue.Minimum[0] = 10; report = Run();
        Check(FirstItem(report)["local_limits"] == null && Equals(report["success"], false), "Inverted untransformed limits are rejected.");
    }

    private static void FacetGroupsAndTypedResources()
    {
        Scenario.Reset();
        Scenario.Item.TypeValue = CSGType.CSG_FACET_GROUP;
        var loop = new FakeLoop("loop0")
        {
            VerticesValue = new[]
            {
                new D3Point("loop0.vertex0", 1, 2, 3),
                new D3Point("loop0.vertex1", 4, 5, 6),
                new D3Point("loop0.vertex2", 7, 8, 9)
            },
            NormalsValue = new[]
            {
                new D3Vector("loop0.normal0", 0, 0, 1),
                new D3Vector("loop0.normal1", 0, 1, 0),
                new D3Vector("loop0.normal2", 1, 0, 0)
            }
        };
        Scenario.Item.FacetsValue = new CSGFacet[]
        {
            new FakeFacet("facet0") { LoopsValue = new CSGLoop[] { loop } }
        };
        var report = Run();
        var item = FirstItem(report);
        var facetGroup = Row(item["facet_group"]);
        var facets = List(facetGroup, "facets");
        var facet = Row(facets[0]);
        var loops = List(facet, "loops");
        var loopRow = Row(loops[0]);
        Check(Equals(report["success"], true) && Equals(report["complete"], true), "A typed facet group completes through the public SDK interfaces.");
        Check(Equals(facetGroup["source"], "CSGFacetGroup.Facets") && Equals(facetGroup["units"], "sdk_raw_unverified") &&
            Equals(facetGroup["coordinate_frame"], "sdk_facet_coordinates_unverified"), "Facet provenance remains raw and explicitly unverified.");
        Check(facets.Count == 1 && loops.Count == 1 && ((double[][])loopRow["vertices"]).Length == 3 &&
            ((double[][])loopRow["normals"]).Length == 3 && ((double[][])loopRow["vertices"])[1][2] == 6 &&
            ((double[][])loopRow["normals"])[2][0] == 1, "Facet, loop, vertex and normal arrays preserve typed coordinates without flattening or interpretation.");
        var counts = Row(report["counts"]); var facetCounts = Row(counts["facet_groups"]);
        Check(Equals(facetCounts["returned_group_count"], 1) && Equals(facetCounts["returned_facet_count"], 1) &&
            Equals(facetCounts["returned_loop_count"], 1) && Equals(facetCounts["copied_coordinate_value_count"], 18), "Facet counts include both point and normal coordinate components.");
        Check(Scenario.Options.All(value => value.DisposeCount == 1) &&
            Equals(Row(report["resources"])["borrowed_dispose_attempted_count"], 0), "Typed facet wrappers do not change owned option cleanup or borrowed disposal policy.");

        Scenario.Reset();
        Scenario.Item.TypeValue = CSGType.CSG_FACET_GROUP;
        Scenario.Item.FacetsValue = new CSGFacet[] { new FakeFacet("facet0") { LoopsValue = new CSGLoop[] { new FakeLoop("loop0") } } };
        Scenario.Failures["loop0.vertices"] = new InvalidOperationException("vertices getter failed");
        report = Run(); item = FirstItem(report);
        Check(Equals(report["success"], false) && Equals(report["complete"], false) &&
            Equals(item["facet_group"], null) && Error(report, "inspection_failed"), "A partial typed-array exception cannot produce a partial facet group receipt.");
        Check(Scenario.Trace.Contains("loop0.vertices") && Scenario.Options.All(value => value.DisposeCount == 1) &&
            Equals(Row(report["resources"])["borrowed_dispose_attempted_count"], 0), "Typed-array failure still releases owned options exactly once without disposing borrowed wrappers.");

        Scenario.Reset();
        Scenario.Item.TypeValue = CSGType.CSG_FACET_GROUP;
        Scenario.Item.FacetsValue = new CSGFacet[] { new FakeFacet("facet0") { LoopsValue = new CSGLoop[] { loop } } };
        Scenario.Failures["loop0.vertex1.y"] = new InvalidOperationException("coordinate getter failed mid-array");
        report = Run(); item = FirstItem(report);
        Check(Equals(report["success"], false) && Equals(item["facet_group"], null) &&
            Scenario.Trace.Contains("loop0.vertex1.y") && !Scenario.Trace.Contains("loop0.vertex1.z"), "A mid-coordinate exception stops extraction and withholds the entire facet group.");
        Check(Scenario.Options.All(value => value.DisposeCount == 1) &&
            Equals(Row(report["resources"])["borrowed_dispose_attempted_count"], 0), "Mid-coordinate failure leaves borrowed SDK wrappers undisposed and releases owned options.");

        Scenario.Reset();
        Scenario.Item.TypeValue = CSGType.CSG_FACET_GROUP;
        Scenario.Item.FacetsValue = null;
        report = Run(); item = FirstItem(report);
        Check(Equals(report["success"], false) && Equals(item["facet_group"], null) && Error(report, "inspection_failed"), "A null facet array fails closed without inventing geometry.");
        Check(Scenario.Options.All(value => value.DisposeCount == 1), "Null facet-array cleanup disposes each owned options wrapper once.");

        Scenario.Reset();
        Scenario.Item.TypeValue = CSGType.CSG_FACET_GROUP;
        Scenario.Item.FacetsValue = new CSGFacet[]
        {
            new FakeFacet("facet0") { LoopsValue = new CSGLoop[] { new FakeLoop("loop0") { VerticesValue = null } } }
        };
        report = Run(); item = FirstItem(report);
        Check(Equals(report["success"], false) && Equals(item["facet_group"], null) && Error(report, "inspection_failed"), "A null nested loop array fails closed without returning a partial group.");
        Check(Scenario.Options.All(value => value.DisposeCount == 1), "Null nested arrays preserve the owned-resource cleanup count.");

        Scenario.Reset();
        Scenario.Item.TypeValue = CSGType.CSG_FACET_GROUP;
        Scenario.Item.FacetsValue = Enumerable.Range(0, 1025)
            .Select(index => (CSGFacet)new FakeFacet("facet" + index) { LoopsValue = new CSGLoop[0] }).ToArray();
        report = Run(); item = FirstItem(report);
        Check(Equals(report["success"], false) && Equals(report["truncated"], true) &&
            List(report, "truncation_reasons").Contains("facet_count_limit") && Equals(item["facet_group"], null), "An oversized facet group is rejected before copying an unbounded prefix.");
        Check(Scenario.Options.All(value => value.DisposeCount == 1) &&
            Equals(Row(report["resources"])["borrowed_dispose_attempted_count"], 0), "Facet budget rejection retains the normal owned-resource cleanup count.");
    }

    private static void QuarantineAndRestoration()
    {
        Scenario.Reset(); Scenario.GenerationFailure = new InvalidOperationException("native export driver failed");
        var report = Run(); int last = Scenario.Trace.Count;
        Check(Equals(Row(report["generated_csg_quarantine"])["active"], true) && Equals(Row(report["generated_csg_quarantine"])["reason"], "generation_failed"), "An ordinary GetGeometry exception persistently quarantines export state.");
        Check(Scenario.Trace.Last() == "builder.generate" && Scenario.OptionCalls == 1 && Scenario.Options[0].DisposeCount == 0 && Equals(Row(report["resources"])["skipped_count"], 1), "Generation failure permits no restore query, cleanup, retry or subsequent SDK call.");
        Throws<GeneratedCsgQuarantineException>(() => Run(), "A second generated read is rejected before dispatch.");
        Throws<GeneratedCsgQuarantineException>(() => Scenario.Context.Probe(), "The shared native barrier rejects other SDK reads after quarantine.");
        Check(Scenario.Trace.Count == last && Scenario.Dispatches == 1, "Quarantine persists across requests without additional native entry.");
        var health = Row(Scenario.Context.GetGeneratedCsgQuarantineState());
        Check(Equals(health["reset_available"], false) && Equals(health["retained_request_count"], 1) && Scenario.Trace.Count == last, "Managed health exposes the latch and retained graph without SDK reads.");
        Scenario.Context.RecoveryProbe();
        Check(Scenario.Trace.Last() == "recovery" && Equals(Row(Scenario.Context.GetGeneratedCsgQuarantineState())["active"], true), "The explicit recovery scope may cross but does not clear the quarantine.");
        Throws<GeneratedCsgQuarantineException>(() => Scenario.Context.Probe(), "The recovery permit is restored after its callback.");

        foreach (bool throws in new[] { false, true })
        {
            Scenario.Reset(); Scenario.OnTouch = path => { if (path == "builder.generate") { Scenario.Context.Fault(); if (throws) throw new ArgumentException("callback and throw"); } };
            report = Run();
            Check(Scenario.Trace.Last() == "builder.generate" && Equals(Row(report["generated_csg_quarantine"])["active"], true) && Row(report["native_database_fault"])["has_fault"].Equals(true), "A generation callback fault stops after the triggering entry whether native code returns or throws.");
            Check(Scenario.Options[0].DisposeCount == 0, "No option cleanup follows a generation callback fault.");
        }
        Scenario.Reset(); Scenario.RestoreOptions = false; report = Run();
        Check(Equals(Row(report["options"])["restored"], false) && Equals(Row(report["generated_csg_quarantine"])["reason"], "option_restoration_unverified") && !Scenario.Trace.Contains("tree.element") && Scenario.Options.All(value => value.DisposeCount == 0), "Observed global option drift is quarantined before any tree reads or cleanup.");
        foreach (string failedPath in new[] { "builder.options2.factory", "options2.holes.get" })
        {
            Scenario.Reset(); Scenario.Failures[failedPath] = new InvalidOperationException("cannot observe restored options"); report = Run();
            Check(Scenario.Trace.Last() == failedPath && Equals(Row(report["generated_csg_quarantine"])["reason"], "option_restoration_unverified") && Scenario.Options.All(value => value.DisposeCount == 0), "Failure to observe restored options is quarantined immediately: " + failedPath);
        }
        Scenario.Reset(); Scenario.Failures["options2.dispose"] = new InvalidOperationException("destructor failed"); report = Run();
        Check(Scenario.Trace.Last() == "options2.dispose" && Equals(Row(report["generated_csg_quarantine"])["reason"], "option_dispose_failed") && Scenario.Options[0].DisposeCount == 0 && Scenario.Options[1].DisposeAttempts == 1, "A failed destructor is attempted only once and quarantines before disposing anything else.");
    }

    private static void ReadFailuresAndContext()
    {
        Scenario.Reset(); Scenario.Failures["item.rdata"] = new ArgumentException("ordinary getter failed"); var report = Run();
        Check(Equals(report["success"], false) && Equals(Row(report["generated_csg_quarantine"])["active"], false) && !Scenario.Trace.Contains("item.idata") && Scenario.Options.All(value => value.DisposeCount == 1), "An ordinary post-generation getter error stops inspection after verified restoration and performs healthy owned cleanup.");
        Check(List(FirstItem(report), "errors").Count == 1 && Equals(FirstItem(report)["complete"], false), "An incomplete item carries its own bounded error.");
        foreach (string changed in new[] { "ce", "project", "mdb", "source" })
        {
            Scenario.Reset(); Scenario.OnTouch = path =>
            {
                if (path != "item.limits.maxz") return;
                if (changed == "ce") Scenario.Ce = "=1/999";
                if (changed == "project") Project.Value = new Project { NameValue = "CHANGED" };
                if (changed == "mdb") MDB.Value = new MDB { NameValue = "CHANGED" };
                if (changed == "source") Scenario.Element.NameValue = "/CHANGED";
            };
            report = Run();
            Check(Equals(report["success"], false) && Error(report, "inspection_failed") && Equals(Row(report["generated_csg_quarantine"])["active"], false), "Changed observation context fails completion without retry: " + changed);
        }
    }

    private static void EffectiveOptionRestoration()
    {
        Scenario.Reset();
        Scenario.GlobalOptions["insulation"] = false;
        Scenario.GlobalOptions["obstruction"] = false;
        Scenario.GlobalOptions["insulation_translucency"] = 1351829404;
        Scenario.GlobalOptions["obstruction_translucency"] = 6605876;
        Scenario.AfterGenerate = () =>
        {
            Scenario.GlobalOptions["insulation_translucency"] = 1037075232;
            Scenario.GlobalOptions["obstruction_translucency"] = 9259688;
        };
        var report = Run(); var options = Row(report["options"]);
        Check(Equals(report["success"], true) && Equals(options["restored"], true) && Equals(options["effective_restored"], true), "The real disabled native transparency witness completes only under the explicit effective-state policy.");
        Check(Equals(options["comparison_policy"], "enabled_geometry_options_v1") && Equals(options["raw_restored"], false) && List(options, "raw_mismatched_fields").SequenceEqual(new object[] { "insulation_translucency", "obstruction_translucency" }), "Both raw drift values and their non-restoration remain visible.");
        Check(Equals(Row(options["before"])["insulation_translucency"], 1351829404) && Equals(Row(options["after"])["insulation_translucency"], 1037075232) && Equals(Row(options["before"])["obstruction_translucency"], 6605876) && Equals(Row(options["after"])["obstruction_translucency"], 9259688), "Native raw values are not replaced with normalized values.");
        Check(Row(options["effective_before"])["insulation_translucency"] == null && Row(options["effective_after"])["obstruction_translucency"] == null && List(options, "effective_mismatched_fields").Count == 0 && List(options, "inactive_fields").Count == 2, "Disabled transparency has an explicit null effective value and named exclusion.");
        Check(Equals(Row(options["requested"])["insulation_translucency"], 0) && Equals(Row(options["requested"])["obstruction_translucency"], 0), "The owned request option wrapper sets both disabled transparency parameters to zero.");
        Check(Scenario.Trace.Contains("options1.insulation_translucency.set") && Scenario.Trace.Contains("options1.obstruction_translucency.set") && Scenario.Options.All(value => value.DisposeCount == 1), "Zero parameters use public SDK setters and retain healthy disposal.");
        foreach (string geometry in new[] { "insulation", "obstruction" })
        {
            Scenario.Reset();
            string translucency = geometry + "_translucency";
            Scenario.GlobalOptions[geometry] = true;
            Scenario.AfterGenerate = () => Scenario.GlobalOptions[translucency] = (int)Scenario.GlobalOptions[translucency] + 1;
            report = Run(); options = Row(report["options"]);
            Check(Equals(report["success"], false) && Equals(options["effective_restored"], false) && Equals(Row(report["generated_csg_quarantine"])["active"], true) && List(options, "effective_mismatched_fields").Contains(translucency), "Enabled transparency drift still quarantines: " + geometry);
            Check(!List(options, "inactive_fields").Contains(translucency) && Scenario.Options.All(value => value.DisposeCount == 0), "An enabled transparency cannot be excluded or followed by native cleanup: " + geometry);
            foreach (bool beforeEnabled in new[] { false, true })
            {
                Scenario.Reset(); Scenario.GlobalOptions[geometry] = beforeEnabled;
                Scenario.AfterGenerate = () => Scenario.GlobalOptions[geometry] = !beforeEnabled;
                report = Run(); options = Row(report["options"]);
                Check(Equals(report["success"], false) && Equals(options["restored"], false) && List(options, "effective_mismatched_fields").Contains(geometry) && !List(options, "inactive_fields").Contains(translucency), "Geometry enablement must match in both directions: " + geometry + ", before=" + beforeEnabled);
            }
        }
        foreach (string key in new[] { "hard_obstructions", "soft_obstructions", "center_line", "tube", "holes", "solid_only", "pline", "wire_line" })
        {
            Scenario.Reset(); Scenario.GlobalOptions["insulation"] = false; Scenario.GlobalOptions["obstruction"] = false;
            Scenario.AfterGenerate = () => Scenario.GlobalOptions[key] = !(bool)Scenario.GlobalOptions[key];
            report = Run(); options = Row(report["options"]);
            Check(Equals(options["effective_restored"], false) && List(options, "effective_mismatched_fields").Contains(key) && Equals(Row(report["generated_csg_quarantine"])["active"], true), "Every other public geometry switch is compared strictly: " + key);
        }
        Scenario.Reset(); Scenario.AfterGenerate = () => Scenario.GlobalOptions["level"] = 7; report = Run();
        Check(Equals(Row(report["options"])["restored"], false) && Equals(Row(report["generated_csg_quarantine"])["active"], true), "The native detail level remains a strict restoration requirement.");
        Scenario.Reset(); Scenario.GlobalOptions["insulation"] = false;
        Scenario.Failures["options2.insulation_translucency.get"] = new InvalidOperationException("Disabled getter failed"); report = Run();
        Check(Equals(Row(report["generated_csg_quarantine"])["active"], true) && Scenario.Trace.Last() == "options2.insulation_translucency.get", "A disabled getter exception is never treated as a harmless raw-value difference.");
    }

    private static void EveryNativeFaultBoundary()
    {
        Scenario.Reset(); Check(Equals(Run()["success"], true), "Baseline succeeds before fault injection.");
        int total = Scenario.Trace.Count;
        for (int trigger = 1; trigger <= total; trigger++)
        {
            foreach (bool throws in new[] { false, true })
            {
                Scenario.Reset(); int faultAt = trigger;
                Scenario.OnTouch = path =>
                {
                    if (Scenario.Trace.Count != faultAt) return;
                    Scenario.Context.Fault();
                    if (throws) throw new ArgumentException("SDK callback followed by throw");
                };
                var report = Run();
                Check(Scenario.Trace.Count == trigger && Equals(report["success"], false) && Equals(report["complete"], false), "No SDK entry follows fault boundary " + trigger + "/" + total + ", throws=" + throws);
                Check(Scenario.Context.HasFault && report["native_database_fault"] != null, "The real managed database-fault latch survives each injected callback.");
            }
        }
        Scenario.Reset(); Scenario.OnTouch = path => { if (path == "item.transform") Scenario.Context.Fault(); };
        var observed = Run();
        Check(Equals(Row(observed["resources"])["borrowed_wrapper_count"], 1) && Scenario.Item.TransformValue.DisposeCount == 0 && Scenario.Trace.Last() == "item.transform", "A returned borrowed wrapper is retained before its post-call fault barrier.");
        Scenario.Reset(); Scenario.OnTouch = path => { if (path == "builder.options1.factory") Scenario.Context.Fault(); };
        observed = Run();
        Check(Equals(Row(observed["resources"])["owned_options_registered_count"], 1) && Equals(Row(observed["resources"])["finalization_suppressed_count"], 1) && Scenario.Options[0].DisposeCount == 0, "Owned options are registered and finalization suppressed before their post-factory barrier.");
    }

    private static int Main()
    {
        try
        {
            InputsAndAdmission(); ShapesAndOwnership(); BoundedInputMembership(); BudgetsAndFiniteData(); FacetGroupsAndTypedResources(); QuarantineAndRestoration(); ReadFailuresAndContext(); EffectiveOptionRestoration(); EveryNativeFaultBoundary();
            Console.WriteLine("Generated CSG production harness checks passed: " + checks);
            return 0;
        }
        catch (Exception failure) { Console.Error.WriteLine(failure); return 1; }
    }
}

internal static class Scenario
{
    internal static E3d21Context Context;
    internal static DbElement Element;
    internal static FakeItem Item;
    internal static FakeTree Tree;
    internal static CSGTree[] Generated;
    internal static string Ce;
    internal static readonly List<string> Trace = new List<string>();
    internal static readonly Dictionary<string, Exception> Failures = new Dictionary<string, Exception>();
    internal static readonly List<FakeOptions> Options = new List<FakeOptions>();
    internal static Dictionary<string, object> GlobalOptions;
    internal static Action<string> OnTouch;
    internal static Action AfterGenerate;
    internal static Exception GenerationFailure;
    internal static bool RestoreOptions, ReuseOptions;
    internal static int Dispatches, BusyAt, BusyChecks, GenerationCalls, OptionCalls;
    internal static void Touch(string path)
    {
        Trace.Add(path);
        if (OnTouch != null) OnTouch(path);
        Exception failure;
        if (Failures.TryGetValue(path, out failure)) throw failure;
    }
    internal static T Read<T>(string path, T value) { Touch(path); return value; }
    internal static void Reset()
    {
        E3d21Context.ResetIsolationForHarness();
        Trace.Clear(); Failures.Clear(); Options.Clear(); OnTouch = null; AfterGenerate = null; GenerationFailure = null;
        RestoreOptions = true; ReuseOptions = false;
        Dispatches = 0; BusyAt = 0; BusyChecks = 0; GenerationCalls = 0; OptionCalls = 0;
        Context = new E3d21Context(); Ce = "=1/2";
        Element = new DbElement("source") { Reference = new[] { 1, 2 }, TypeValue = new DbElementType { ShortValue = "CYLI" }, NameValue = "/SOURCE" };
        Item = new FakeItem("item"); Tree = new FakeTree("tree") { ElementValue = Element, ItemsValue = new CSGItem[] { Item } }; Generated = new CSGTree[] { Tree };
        Project.Value = new Project { NameValue = "PROJECT" }; MDB.Value = new MDB { NameValue = "MDB" };
        GlobalOptions = new Dictionary<string, object>
        {
            ["hard_obstructions"] = true, ["soft_obstructions"] = false, ["level"] = 6, ["center_line"] = true,
            ["tube"] = false, ["insulation"] = true, ["insulation_translucency"] = 40, ["obstruction"] = true,
            ["obstruction_translucency"] = 50, ["holes"] = false, ["solid_only"] = true, ["pline"] = true, ["wire_line"] = true
        };
        CSGTreeBuilder.Value = new FakeBuilder();
    }
}

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        internal bool HasFault { get { return nativeFaultState.HasFault; } }
        internal void Fault() { nativeFaultState.Observe("Unhandled Database error 41 from CSG test", "test"); }
        internal void Probe() { NativeRead(() => Scenario.Read("probe", 1)); }
        internal void RecoveryProbe()
        {
            bool old = generatedCsgRecoveryPermitted;
            generatedCsgRecoveryPermitted = true;
            try { NativeRead(() => Scenario.Read("recovery", 1)); }
            finally { generatedCsgRecoveryPermitted = old; }
        }
        internal static void ResetIsolationForHarness()
        {
            // Test-only reset of our managed state; production exposes no reset.
            object isolation = typeof(E3d21Context).GetField("generatedCsgIsolation", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            foreach (FieldInfo field in isolation.GetType().GetFields(BindingFlags.Instance | BindingFlags.NonPublic))
            {
                if (field.FieldType == typeof(bool)) field.SetValue(isolation, false);
                else if (field.FieldType == typeof(string)) field.SetValue(isolation, null);
                else if (field.FieldType == typeof(List<object>)) ((List<object>)field.GetValue(isolation)).Clear();
            }
            currentNativeFaultState = null; generatedCsgRecoveryPermitted = false;
        }
        private bool IsCommandActive()
        {
            if (nativeFaultState.HasFault) throw new NativeDatabaseFaultException();
            Scenario.BusyChecks++;
            return Scenario.BusyAt == Scenario.BusyChecks;
        }
        private static void ThrowIfNativeFaulted()
        {
            ThrowIfGeneratedCsgQuarantined();
            if (currentNativeFaultState != null && currentNativeFaultState.HasFault) throw new NativeDatabaseFaultException();
        }
        private static T NativeRead<T>(Func<T> operation) { ThrowIfNativeFaulted(); try { return operation(); } finally { ThrowIfNativeFaulted(); } }
        private static void NativeWrite(Action operation) { ThrowIfNativeFaulted(); try { operation(); } finally { ThrowIfNativeFaulted(); } }
        private T OnUi<T>(Func<T> operation)
        {
            ThrowIfGeneratedCsgQuarantined();
            if (nativeFaultState.HasFault) throw new NativeDatabaseFaultException();
            Scenario.Dispatches++;
            currentNativeFaultState = nativeFaultState;
            try { ThrowIfNativeFaulted(); T value = operation(); ThrowIfNativeFaulted(); return value; }
            finally { currentNativeFaultState = null; }
        }
        private static string ModelCurrentReference() { return Scenario.Read("context.ce", Scenario.Ce); }
        private static DbElement ResolveElement(string reference) { return Scenario.Read("source.resolve", Scenario.Element); }
        private static string ModelReference(int[] pair) { return "=" + pair[0] + "/" + pair[1]; }
    }
}

namespace Aveva.Core.Database
{
    public enum DbType { Design, Catalogue }
    public sealed class DbAttributeInstance { public static readonly DbAttributeInstance NAME = new DbAttributeInstance(); }
    public sealed class DbElementType
    {
        internal string ShortValue;
        public string ShortName { get { return Scenario.Read("source.type.short", ShortValue); } }
        public string Name { get { return Scenario.Read("source.type.name", ShortValue); } }
    }
    public sealed class DbElement
    {
        private readonly string label;
        internal int[] Reference;
        internal DbElementType TypeValue;
        internal string NameValue;
        internal DbType DbTypeValue = DbType.Design;
        internal readonly List<DbElement> Children = new List<DbElement>();
        internal DbElement OwnerValue;
        internal DbElement(string label) { this.label = label; }
        public bool IsNull { get { return Scenario.Read(label + ".null", false); } }
        public bool IsValid { get { return Scenario.Read(label + ".valid", true); } }
        public int[] Ref { get { return Scenario.Read(label + ".ref", Reference); } }
        public DbType DbType { get { return Scenario.Read(label + ".dbtype", DbTypeValue); } }
        public DbElementType GetElementType() { return Scenario.Read(label + ".type", TypeValue); }
        public string GetAsString(DbAttributeInstance attribute) { return Scenario.Read(label + ".name", NameValue); }
        public DbElement Owner { get { return Scenario.Read(label + ".owner", OwnerValue); } }
        public DbElement Member(int index) { return Scenario.Read(label + ".member" + index, index <= Children.Count ? Children[index - 1] : null); }
    }
    public sealed class Project
    {
        internal static Project Value;
        internal string NameValue;
        public static Project CurrentProject { get { return Scenario.Read("context.project", Value); } }
        public string Name { get { return Scenario.Read("context.project.name", NameValue); } }
    }
    public sealed class MDB
    {
        internal static MDB Value;
        internal string NameValue;
        public static MDB CurrentMDB { get { return Scenario.Read("context.mdb", Value); } }
        public string Name { get { return Scenario.Read("context.mdb.name", NameValue); } }
    }
}

namespace Aveva.Core.Maths.Geometry
{
    public sealed class D3Point
    {
        private readonly string label;
        internal double[] Values;
        internal D3Point(string label, double x, double y, double z) { this.label = label; Values = new[] { x, y, z }; }
        public double X { get { return Scenario.Read(label + ".x", Values[0]); } }
        public double Y { get { return Scenario.Read(label + ".y", Values[1]); } }
        public double Z { get { return Scenario.Read(label + ".z", Values[2]); } }
    }

    public sealed class D3Vector
    {
        private readonly string label;
        internal double[] Values;
        internal D3Vector(string label, double x, double y, double z) { this.label = label; Values = new[] { x, y, z }; }
        public double X { get { return Scenario.Read(label + ".x", Values[0]); } }
        public double Y { get { return Scenario.Read(label + ".y", Values[1]); } }
        public double Z { get { return Scenario.Read(label + ".z", Values[2]); } }
    }

    public sealed class D3Transform : IDisposable
    {
        private readonly string label;
        internal int DisposeCount;
        internal double[] Values = new[] { 1d, 0, 0, 10, 0, 1, 0, 20, 0, 0, 1, 30 };
        internal D3Transform(string label) { this.label = label; }
        public double[] AsArray12() { return Scenario.Read(label + ".array12", Values); }
        public void Dispose() { DisposeCount++; Scenario.Touch(label + ".dispose"); }
    }
    public sealed class D3Limits : IDisposable
    {
        private readonly string label;
        internal int DisposeCount;
        internal double[] Minimum = new[] { -1d, -2, -3 }, Maximum = new[] { 1d, 2, 3 };
        internal D3Limits(string label) { this.label = label; }
        public double MinX { get { return Scenario.Read(label + ".minx", Minimum[0]); } }
        public double MinY { get { return Scenario.Read(label + ".miny", Minimum[1]); } }
        public double MinZ { get { return Scenario.Read(label + ".minz", Minimum[2]); } }
        public double MaxX { get { return Scenario.Read(label + ".maxx", Maximum[0]); } }
        public double MaxY { get { return Scenario.Read(label + ".maxy", Maximum[1]); } }
        public double MaxZ { get { return Scenario.Read(label + ".maxz", Maximum[2]); } }
        public void Dispose() { DisposeCount++; Scenario.Touch(label + ".dispose"); }
    }
}

namespace Aveva.Core3D.CSG
{
    public enum CSGType
    {
        CSG_BOX = 1, CSG_CYLI = 2, CSG_CONE = 3,
        CSG_FACET_GROUP = 19, CSG_NEG_BOX = 51, CSG_NEG_FACET_GROUP = 68
    }
    public interface CSGTree { DbElement Element { get; } CSGItem[] Items { get; } }
    public interface CSGItem
    {
        int GMLItem { get; } CSGType Type { get; } int Translucency { get; }
        double[] RData { get; } int[] IData { get; } D3Transform Transform { get; } D3Limits Limits { get; }
    }
    public interface CSGFacetGroup : CSGItem { CSGFacet[] Facets { get; } }
    public interface CSGFacet { CSGLoop[] Loops { get; } }
    public interface CSGLoop
    {
        D3Point[] Vertices { get; }
        D3Vector[] Normals { get; }
    }
    public abstract class CSGTreeBuilder
    {
        internal static CSGTreeBuilder Value;
        public static CSGTreeBuilder Instance { get { return Scenario.Read("builder.instance", Value); } }
        public abstract CSGTree[] GetGeometry(DbElement element, CSGTreeBuilderOptions options);
        public abstract CSGTreeBuilderOptions CreateCSGTreeBuilderOptions();
    }
    public abstract class CSGTreeBuilderOptions
    {
        public abstract bool HardObstructions { get; set; } public abstract bool SoftObstructions { get; set; }
        public abstract int Level { get; set; } public abstract bool CenterLine { get; set; }
        public abstract bool Tube { get; set; } public abstract bool Insulation { get; set; }
        public abstract int InsulationTranslucency { get; set; } public abstract bool Obstruction { get; set; }
        public abstract int ObstructionTranslucency { get; set; } public abstract bool Holes { get; set; }
        public abstract bool SolidOnly { get; set; } public abstract bool Pline { get; set; } public abstract bool WireLine { get; set; }
    }
}

internal sealed class FakeTree : CSGTree
{
    private readonly string label;
    internal DbElement ElementValue;
    internal CSGItem[] ItemsValue;
    internal FakeTree(string label) { this.label = label; }
    public DbElement Element { get { return Scenario.Read(label + ".element", ElementValue); } }
    public CSGItem[] Items { get { return Scenario.Read(label + ".items", ItemsValue); } }
}

internal sealed class FakeItem : CSGFacetGroup
{
    private readonly string label;
    internal CSGType TypeValue = CSGType.CSG_CYLI;
    internal double[] RDataValue = new[] { 10d, 100d };
    internal int[] IDataValue = new[] { -1, 2, 3 };
    internal CSGFacet[] FacetsValue = new CSGFacet[0];
    internal D3Transform TransformValue;
    internal D3Limits LimitsValue;
    internal FakeItem(string label) { this.label = label; TransformValue = new D3Transform(label + ".transform"); LimitsValue = new D3Limits(label + ".limits"); }
    public int GMLItem { get { return Scenario.Read(label + ".gml", 71); } }
    public CSGType Type { get { return Scenario.Read(label + ".type", TypeValue); } }
    public int Translucency { get { return Scenario.Read(label + ".translucency", 0); } }
    public double[] RData { get { return Scenario.Read(label + ".rdata", RDataValue); } }
    public int[] IData { get { return Scenario.Read(label + ".idata", IDataValue); } }
    public D3Transform Transform { get { return Scenario.Read(label + ".transform", TransformValue); } }
    public D3Limits Limits { get { return Scenario.Read(label + ".limits", LimitsValue); } }
    public CSGFacet[] Facets { get { return Scenario.Read(label + ".facets", FacetsValue); } }
}

internal sealed class FakeFacet : CSGFacet
{
    private readonly string label;
    internal CSGLoop[] LoopsValue = new CSGLoop[0];
    internal FakeFacet(string label) { this.label = label; }
    public CSGLoop[] Loops { get { return Scenario.Read(label + ".loops", LoopsValue); } }
}

internal sealed class FakeLoop : CSGLoop
{
    private readonly string label;
    internal D3Point[] VerticesValue = new D3Point[0];
    internal D3Vector[] NormalsValue = new D3Vector[0];
    internal FakeLoop(string label) { this.label = label; }
    public D3Point[] Vertices { get { return Scenario.Read(label + ".vertices", VerticesValue); } }
    public D3Vector[] Normals { get { return Scenario.Read(label + ".normals", NormalsValue); } }
}

internal sealed class FakeBuilder : CSGTreeBuilder
{
    public override CSGTreeBuilderOptions CreateCSGTreeBuilderOptions()
    {
        int number = ++Scenario.OptionCalls;
        var value = Scenario.ReuseOptions && Scenario.Options.Count != 0 ? Scenario.Options[0] : new FakeOptions("options" + number);
        value.Values = new Dictionary<string, object>(Scenario.GlobalOptions);
        if (!Scenario.Options.Contains(value)) Scenario.Options.Add(value);
        Scenario.Touch("builder.options" + number + ".factory");
        return value;
    }
    public override CSGTree[] GetGeometry(DbElement element, CSGTreeBuilderOptions options)
    {
        Scenario.GenerationCalls++;
        Scenario.Touch("builder.generate");
        var before = Scenario.GlobalOptions;
        Scenario.GlobalOptions = new Dictionary<string, object>(((FakeOptions)options).Values);
        if (Scenario.GenerationFailure != null) throw Scenario.GenerationFailure;
        if (Scenario.RestoreOptions) Scenario.GlobalOptions = before;
        if (Scenario.AfterGenerate != null) Scenario.AfterGenerate();
        return Scenario.Generated;
    }
}

internal sealed class FakeOptions : CSGTreeBuilderOptions, IDisposable
{
    private readonly string label;
    internal Dictionary<string, object> Values;
    internal int DisposeCount, DisposeAttempts;
    internal FakeOptions(string label) { this.label = label; }
    private T Get<T>(string name) { return Scenario.Read(label + "." + name + ".get", (T)Values[name]); }
    private void Set<T>(string name, T value) { Scenario.Touch(label + "." + name + ".set"); Values[name] = value; }
    public override bool HardObstructions { get { return Get<bool>("hard_obstructions"); } set { Set("hard_obstructions", value); } }
    public override bool SoftObstructions { get { return Get<bool>("soft_obstructions"); } set { Set("soft_obstructions", value); } }
    public override int Level { get { return Get<int>("level"); } set { Set("level", value); } }
    public override bool CenterLine { get { return Get<bool>("center_line"); } set { Set("center_line", value); } }
    public override bool Tube { get { return Get<bool>("tube"); } set { Set("tube", value); } }
    public override bool Insulation { get { return Get<bool>("insulation"); } set { Set("insulation", value); } }
    public override int InsulationTranslucency { get { return Get<int>("insulation_translucency"); } set { Set("insulation_translucency", value); } }
    public override bool Obstruction { get { return Get<bool>("obstruction"); } set { Set("obstruction", value); } }
    public override int ObstructionTranslucency { get { return Get<int>("obstruction_translucency"); } set { Set("obstruction_translucency", value); } }
    public override bool Holes { get { return Get<bool>("holes"); } set { Set("holes", value); } }
    public override bool SolidOnly { get { return Get<bool>("solid_only"); } set { Set("solid_only", value); } }
    public override bool Pline { get { return Get<bool>("pline"); } set { Set("pline", value); } }
    public override bool WireLine { get { return Get<bool>("wire_line"); } set { Set("wire_line", value); } }
    public void Dispose() { DisposeAttempts++; Scenario.Touch(label + ".dispose"); DisposeCount++; }
}
