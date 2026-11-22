#if PANEL_POLYGON_TESTS
using System;
using System.Linq;
using E3d21AiBridge;

internal static class PanelPolygonTests
{
    private static int checks;
    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) throw new Exception(message);
    }
    private static double[][] Polygon(params double[] coordinates)
    {
        return Enumerable.Range(0, coordinates.Length / 2)
            .Select(i => new[] { coordinates[i * 2], coordinates[i * 2 + 1] }).ToArray();
    }
    private static bool Has(PanelPolygonValidation result, string code)
    {
        return result.issues.Any(issue => issue.code == code);
    }
    private static void Main()
    {
        double[][] rectangle = Polygon(0, 0, 100, 0, 100, 50, 0, 50);
        PanelPolygonValidation result = PanelPolygon.Validate(rectangle);
        Check(result.valid && result.winding == "counterclockwise", "A counterclockwise rectangle is valid for creation.");
        Check(result.signed_area_mm2 == 5000 && result.perimeter_mm == 300, "Rectangle measurements use millimetres.");
        Check(result.extent.min.SequenceEqual(new[] { 0.0, 0 }) && result.extent.max.SequenceEqual(new[] { 100.0, 50 }), "Extent retains the original XY frame.");
        Check(rectangle.SelectMany(p => p).SequenceEqual(new[] { 0.0, 0, 100, 0, 100, 50, 0, 50 }), "Validation does not mutate the submitted polygon.");
        double[][] reverse = rectangle.Reverse().ToArray();
        result = PanelPolygon.Validate(reverse);
        Check(!result.valid && Has(result, "clockwise_winding") && result.signed_area_mm2 == -5000, "Creation rejects clockwise winding without correcting it.");
        result = PanelPolygon.Validate(reverse, false, PanelPolygon.InspectVertexLimit);
        Check(result.valid && result.winding == "clockwise" && result.signed_area_mm2 == -5000, "Inspection reports clockwise winding without making it invalid.");

        result = PanelPolygon.Validate(Polygon(0, 0, 6, 0, 6, 2, 2, 2, 2, 6, 0, 6));
        Check(result.valid && result.signed_area_mm2 == 20 && result.perimeter_mm == 24, "A simple concave boundary is valid.");
        Check(PanelPolygon.Validate(Polygon(0, 0, 2, 0, 4, 0, 4, 3, 0, 3)).valid, "Monotonic collinear boundary vertices are preserved and accepted.");
        result = PanelPolygon.Validate(Polygon(0, 0, 4, 4, 0, 4, 4, 0));
        Check(!result.valid && Has(result, "degenerate_area") && Has(result, "self_intersection"), "A zero-area bowtie reports both area and crossing failures.");
        result = PanelPolygon.Validate(Polygon(0, 0, 5, 4, 0, 4, 4, 0));
        Check(!result.valid && Has(result, "self_intersection") && result.signed_area_mm2 != 0, "Crossings must be detected even when shoelace area is nonzero.");
        result = PanelPolygon.Validate(Polygon(0, 0, 4, 0, 4, 4, 0, 4, 0, 0));
        Check(!result.valid && Has(result, "duplicate_vertex") && Has(result, "zero_length_edge"), "A repeated closing vertex is rejected, never stripped.");
        Check(result.issues.Any(issue => issue.code == "duplicate_vertex" && issue.vertex_index == 0 && issue.other_vertex_index == 4), "Duplicate diagnostics identify the original indexes.");
        result = PanelPolygon.Validate(Polygon(0, 0, 4, 0, 4, 4, 2, 0, 0, 4));
        Check(!result.valid && Has(result, "edge_touch"), "A distinct vertex touching a nonadjacent edge invalidates the boundary.");
        result = PanelPolygon.Validate(Polygon(0, 0, 4, 0, 2, 0, 4, 4, 0, 4));
        Check(!result.valid && Has(result, "adjacent_edge_overlap"), "An adjacent collinear backtrack is invalid.");
        result = PanelPolygon.Validate(Polygon(0, 0, 8, 0, 8, 4, 2, 4, 2, 0, 6, 0, 6, 2, 0, 2));
        Check(!result.valid && Has(result, "collinear_edge_overlap"), "Nonadjacent collinear interval overlaps are invalid.");
        result = PanelPolygon.Validate(Polygon(0, 0, 4, 0, 4, 4, 0, 0, 0, 4));
        Check(!result.valid && Has(result, "duplicate_vertex"), "Equal-valued coordinates in different arrays are duplicates.");

        double offset = 1e12;
        double[][] translated = Polygon(offset, -offset, offset + 100, -offset,
            offset + 100, -offset + 0.125, offset, -offset + 0.125);
        result = PanelPolygon.Validate(translated);
        Check(result.valid && result.signed_area_mm2 == 12.5 && result.perimeter_mm == 200.25, "A large absolute translation cannot erase a narrow valid panel.");
        Check(result.extent.min[0] == offset && result.extent.max[1] == -offset + 0.125, "Translated extents keep the submitted coordinates.");
        result = PanelPolygon.Validate(Polygon(0, 0, 1e9, 0, 1e9, 0.001, 0, 0.001));
        Check(result.valid && result.signed_area_mm2 == 1e6, "Relative error tolerance must not erase a long narrow polygon.");
        result = PanelPolygon.Validate(Polygon(0, 0, 1, 0, 1, 0.000001, 0, 0.000001));
        Check(!result.valid && Has(result, "degenerate_area"), "Sub-tolerance thin boundaries are invalid.");
        Check(!PanelPolygon.Validate(Polygon(0, 0, 1e-8, 0, 0, 1e-8)).valid, "A tiny triangle is rejected.");
        result = PanelPolygon.Validate(Polygon(0, 0, 1e200, 0, 1e200, 1e200, 0, 1e200));
        Check(!result.valid && Has(result, "numeric_range") && result.signed_area_mm2 == null, "Area product overflow fails closed without emitting Infinity.");
        result = PanelPolygon.Validate(Polygon(-double.MaxValue, 0, double.MaxValue, 0, 0, 1));
        Check(!result.valid && Has(result, "numeric_range"), "Finite coordinates whose differences overflow must be rejected.");
        result = PanelPolygon.Validate(Polygon(0, 0, double.MaxValue, 0, double.MaxValue, 1, 0, 1));
        Check(!result.valid && Has(result, "numeric_range"), "A perimeter sum overflow must fail closed.");
        foreach (double invalid in new[] { double.NaN, double.PositiveInfinity, double.NegativeInfinity })
        {
            result = PanelPolygon.Validate(Polygon(0, 0, invalid, 0, 0, 1));
            Check(!result.valid && Has(result, "nonfinite_coordinate") && result.signed_area_mm2 == null, "Nonfinite input cannot enter geometric arithmetic.");
        }
        Check(Has(PanelPolygon.Validate(null), "vertex_count") && Has(PanelPolygon.Validate(new double[0][]), "vertex_count"), "Null and empty polygons are invalid.");
        Check(Has(PanelPolygon.Validate(Polygon(0, 0, 1, 1)), "vertex_count"), "Two vertices cannot define a panel boundary.");
        Check(Has(PanelPolygon.Validate(new[] { new[] { 0.0, 0 }, null, new[] { 0.0, 1 } }), "vertex_format"), "Null vertex rows are invalid.");
        Check(Has(PanelPolygon.Validate(new[] { new[] { 0.0, 0, 0 }, new[] { 1.0, 0 }, new[] { 0.0, 1 } }), "vertex_format"), "Three-dimensional input is not silently projected.");

        // Regression contours from pump-surfaces-repair-20261115.json plan
        // local_position, in stored PAVE order. A valid bounding box alone did
        // not establish these boundaries; check their actual ordered edges.
        double[][][] repairedPumpContours =
        {
            Polygon(300, -420, 500, -200, 500, 300, 300, 520, -300, 520, -500, 300, -500, -200, -300, -420, 0, -520),
            Polygon(0, -520, 300, -420, 500, -200, 500, 300, 300, 520, -300, 520, -500, 300, -500, -200, -300, -420),
            Polygon(200, -500, 450, -250, 450, 300, 150, 550, -250, 500, -450, 300, -450, -200, -200, -450),
            Polygon(200, -450, 450, -200, 450, 300, 250, 500, -150, 550, -450, 300, -450, -250, -200, -500),
            Polygon(450, -450, 450, 450, -450, 450, -450, -450)
        };
        double[] repairedPumpAreas = { 882000, 882000, 782500, 782500, 810000 };
        for (int i = 0; i < repairedPumpContours.Length; i++)
        {
            result = PanelPolygon.Validate(repairedPumpContours[i], false, PanelPolygon.InspectVertexLimit);
            Check(result.valid && result.winding == "counterclockwise" && result.signed_area_mm2 == repairedPumpAreas[i],
                "The repaired pump incident contour must be simple with its measured area: " + i);
        }

        Func<int, double[][]> ring = n => Enumerable.Range(0, n).Select(i => new[]
            { 100.0 * Math.Cos(2 * Math.PI * i / n), 100.0 * Math.Sin(2 * Math.PI * i / n) }).ToArray();
        Check(PanelPolygon.Validate(ring(256)).valid, "Creation permits its exact 256-vertex bound.");
        Check(Has(PanelPolygon.Validate(ring(257)), "vertex_count"), "Creation rejects 257 vertices before pairwise work.");
        Check(PanelPolygon.Validate(ring(2048), false, 2048).valid, "Read-only inspection permits the exact 2048-vertex bound.");
        Check(Has(PanelPolygon.Validate(ring(2049), false, 2048), "vertex_count"), "Inspection rejects its overall bound plus one.");
        result = PanelPolygon.Validate(Enumerable.Range(0, 256).Select(i => new[] { 0.0, 0 }).ToArray());
        Check(!result.valid && result.issues.Length == 128 && result.issues_truncated, "Repeated invalid geometry has bounded diagnostics.");
        Console.WriteLine("Panel polygon checks passed: " + checks);
    }
}
#endif
