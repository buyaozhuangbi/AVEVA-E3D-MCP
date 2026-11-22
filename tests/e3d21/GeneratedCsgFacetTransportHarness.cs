using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using Aveva.Core.Maths.Geometry;
using Aveva.Core3D.CSG;

// Serialize actual production-reader receipts from managed SDK substitutes.
// This is transport/fault evidence, never a native geometry observation.
internal static class GeneratedCsgFacetTransportHarness
{
    private static readonly Dictionary<string, object> receipts = new Dictionary<string, object>();
    private static int faultChecks;
    private static Dictionary<string, object> Row(object value) { return (Dictionary<string, object>)value; }
    private static List<object> List(Dictionary<string, object> row, string key) { return (List<object>)row[key]; }
    private static void Check(bool condition, string message)
    {
        faultChecks++;
        if (!condition) throw new Exception(message);
    }
    private static Dictionary<string, object> Run()
    {
        return Row(Scenario.Context.GetGeneratedCsg("=1/2", 8, 64));
    }
    private static void Keep(string name, Dictionary<string, object> report)
    {
        receipts.Add(name, new Dictionary<string, object>
        {
            ["ok"] = true, ["version"] = "E3D21", ["result"] = report
        });
    }
    private static FakeLoop Loop(string name, double[][] vertices)
    {
        return new FakeLoop(name)
        {
            VerticesValue = vertices.Select((xyz, i) => new D3Point(name + ".vertex" + i, xyz[0], xyz[1], xyz[2])).ToArray(),
            NormalsValue = vertices.Select((xyz, i) => new D3Vector(name + ".normal" + i, 0, 0, 1)).ToArray()
        };
    }
    private static void Setup(CSGType type = CSGType.CSG_FACET_GROUP)
    {
        Scenario.Reset();
        Scenario.Item.TypeValue = type;
        Scenario.Item.FacetsValue = new CSGFacet[]
        {
            new FakeFacet("facet0") { LoopsValue = new CSGLoop[]
            {
                Loop("outer", new[] { new[] { 1.25, -2, 3 }, new[] { 6.25, -2, 3 }, new[] { 6.25, 7, 3 }, new[] { 1.25, 7, 3 } }),
                Loop("inner", new[] { new[] { 2d, 0, 3 }, new[] { 2d, 1, 3 }, new[] { 3d, 0, 3 } })
            } }
        };
    }
    private static CSGFacet[] EmptyFacets(int count)
    {
        return Enumerable.Range(0, count).Select(i => (CSGFacet)new FakeFacet("facet" + i)).ToArray();
    }
    private static CSGFacet FacetWithEmptyLoops(string name, int count)
    {
        return new FakeFacet(name)
        {
            LoopsValue = Enumerable.Range(0, count).Select(i => (CSGLoop)new FakeLoop(name + ".loop" + i)).ToArray()
        };
    }
    private static FakeLoop VertexOnlyLoop(string name, int count)
    {
        // Unequal/empty normal arrays remain observations, not mesh admission.
        return new FakeLoop(name)
        {
            VerticesValue = Enumerable.Repeat(new D3Point(name + ".vertex", 1.25, -2, 3), count).ToArray()
        };
    }
    private static void BoundaryReceipts()
    {
        Setup(); Keep("positive-inner-loops", Run());
        Setup(CSGType.CSG_NEG_FACET_GROUP); Keep("negative-inner-loops", Run());

        foreach (int count in new[] { 1024, 1025 })
        {
            Setup(); Scenario.Item.FacetsValue = EmptyFacets(count);
            Keep("facets-" + count, Run());
        }
        foreach (bool exceed in new[] { false, true })
        {
            Setup(); Scenario.Item.FacetsValue = EmptyFacets(1024);
            var second = new FakeItem("second") { TypeValue = CSGType.CSG_NEG_FACET_GROUP, FacetsValue = EmptyFacets(1024) };
            var items = new List<CSGItem> { Scenario.Item, second };
            if (exceed) items.Add(new FakeItem("third") { TypeValue = CSGType.CSG_FACET_GROUP, FacetsValue = EmptyFacets(1) });
            Scenario.Tree.ItemsValue = items.ToArray();
            Keep(exceed ? "total-facets-over" : "total-facets-at", Run());
        }
        foreach (int count in new[] { 128, 129 })
        {
            Setup(); Scenario.Item.FacetsValue = new[] { FacetWithEmptyLoops("facet0", count) };
            Keep("loops-" + count, Run());
        }
        foreach (bool exceed in new[] { false, true })
        {
            Setup();
            Scenario.Item.FacetsValue = Enumerable.Range(0, 16).Select(i => FacetWithEmptyLoops("facet" + i, 128)).ToArray();
            var items = new List<CSGItem>
            {
                Scenario.Item,
                new FakeItem("second") { TypeValue = CSGType.CSG_NEG_FACET_GROUP,
                    FacetsValue = Enumerable.Range(0, 16).Select(i => FacetWithEmptyLoops("second-facet" + i, 128)).ToArray() }
            };
            if (exceed) items.Add(new FakeItem("third") { TypeValue = CSGType.CSG_FACET_GROUP,
                FacetsValue = new[] { FacetWithEmptyLoops("extra", 1) } });
            Scenario.Tree.ItemsValue = items.ToArray();
            Keep(exceed ? "total-loops-over" : "total-loops-at", Run());
        }
        foreach (int count in new[] { 1024, 1025 })
        {
            Setup(); Scenario.Item.FacetsValue = new CSGFacet[]
            {
                new FakeFacet("facet0") { LoopsValue = new CSGLoop[] { VertexOnlyLoop("loop0", count) } }
            };
            Keep("entries-" + count, Run());
        }
        foreach (int finalEntries in new[] { 341, 342 })
        {
            Setup();
            Scenario.Item.FacetsValue = new CSGFacet[] { new FakeFacet("facet0")
            {
                LoopsValue = Enumerable.Range(0, 5).Select(i => (CSGLoop)VertexOnlyLoop("loop" + i, 1024)).ToArray()
            } };
            Scenario.Tree.ItemsValue = new CSGItem[]
            {
                Scenario.Item,
                new FakeItem("second") { TypeValue = CSGType.CSG_NEG_FACET_GROUP, FacetsValue = new CSGFacet[]
                {
                    new FakeFacet("second-facet") { LoopsValue = new CSGLoop[] { VertexOnlyLoop("last", finalEntries) } }
                } }
            };
            Keep(finalEntries == 341 ? "coordinates-at" : "coordinates-over", Run());
        }
        Setup();
        Scenario.Tree.ItemsValue = new CSGItem[]
        {
            Scenario.Item,
            new FakeItem("second") { TypeValue = CSGType.CSG_NEG_FACET_GROUP, FacetsValue = new CSGFacet[]
            {
                new FakeFacet("second-facet") { LoopsValue = new CSGLoop[] { new FakeLoop("broken") { VerticesValue = null } } }
            } }
        };
        Keep("failed-second-group", Run());
    }
    private static int TypedFaultReceipts()
    {
        Setup(); Run();
        string[] paths = Scenario.Trace.Where(path => path == "item.facets" || path == "facet0.loops" ||
            path.StartsWith("outer.", StringComparison.Ordinal) || path.StartsWith("inner.", StringComparison.Ordinal)).ToArray();
        Check(paths.Length == 48 && paths.Distinct().Count() == paths.Length, "Cover every typed getter, including each coordinate component.");
        int index = 0;
        foreach (string target in paths)
        {
            foreach (bool throws in new[] { false, true })
            {
                Setup();
                Scenario.OnTouch = path =>
                {
                    if (path != target) return;
                    Scenario.Context.Fault();
                    if (throws) throw new ArgumentException("Typed getter callback then throw");
                };
                var report = Run();
                var tree = Row(List(report, "trees")[0]);
                var item = Row(List(tree, "items")[0]);
                var counts = Row(Row(report["counts"])["facet_groups"]);
                Check(Scenario.Trace.Last() == target && Scenario.Trace.Count(path => path == target) == 1,
                    "No native entry or retry follows the typed fault: " + target);
                Check(Equals(report["success"], false) && Equals(report["complete"], false) &&
                    Equals(item["complete"], false) && item["facet_group"] == null && report["native_database_fault"] != null,
                    "The entire interrupted group is withheld and the native fault remains visible: " + target);
                Check(counts.Values.All(value => Equals(value, 0)), "An interrupted group never commits facet budget counts.");
                Check(Scenario.Options.All(option => option.DisposeCount == 0) &&
                    Equals(Row(report["resources"])["borrowed_dispose_attempted_count"], 0),
                    "Neither borrowed wrappers nor owned options are disposed after a native fault.");
                Keep("fault-" + index++, report);
            }
        }
        return paths.Length;
    }
    private static int Main()
    {
        try
        {
            BoundaryReceipts();
            int paths = TypedFaultReceipts();
            Console.WriteLine("Generated CSG facet transport receipts: " + JsonSerializer.Serialize(new
            {
                evidence = "production_reader_managed_sdk_substitutes_only", typed_fault_paths = paths,
                fault_checks = faultChecks, receipts
            }));
            return 0;
        }
        catch (Exception failure) { Console.Error.WriteLine(failure); return 1; }
    }
}
