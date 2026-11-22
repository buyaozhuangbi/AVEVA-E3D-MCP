using System;
using System.Collections.Generic;

namespace E3d21AiBridge;

// Pure, SDK-independent validation. Coordinates and issue indexes retain the
// caller's order; indexes are zero based and edge i joins i to (i + 1) % n.
// No closing vertex is removed, vertices reordered, or coordinates rounded.
internal static class PanelPolygon
{
    internal const int CreateVertexLimit = 256;
    internal const int InspectVertexLimit = 2048;
    internal const double CoordinateTolerance = 0.00001; // millimetres
    private const double RoundoffTolerance = 32 * 2.2204460492503131e-16;
    private const int IssueLimit = 128;

    internal static PanelPolygonValidation Validate(double[][] vertices,
        bool requireCounterClockwise = true, int maxVertices = CreateVertexLimit)
    {
        if (maxVertices < 3 || maxVertices > InspectVertexLimit)
            throw new ArgumentOutOfRangeException("maxVertices");
        var issues = new IssueList();
        int count = vertices == null ? 0 : vertices.Length;
        if (count < 3 || count > maxVertices)
        {
            issues.Add("vertex_count", "Supply between 3 and " + maxVertices + " vertices; do not repeat the closing vertex.");
            return Result(issues);
        }

        var points = new Point[count];
        for (int i = 0; i < count; i++)
        {
            double[] vertex = vertices[i];
            if (vertex == null || vertex.Length != 2)
                issues.Add("vertex_format", "Each vertex must contain exactly two coordinates in millimetres.", i);
            else if (!Finite(vertex[0]) || !Finite(vertex[1]))
                issues.Add("nonfinite_coordinate", "Vertex coordinates must be finite numbers.", i);
            else
                points[i] = new Point(vertex[0], vertex[1]);
        }
        if (issues.Count != 0) return Result(issues);

        double minX = points[0].X, maxX = minX, minY = points[0].Y, maxY = minY;
        foreach (Point point in points)
        {
            minX = Math.Min(minX, point.X); maxX = Math.Max(maxX, point.X);
            minY = Math.Min(minY, point.Y); maxY = Math.Max(maxY, point.Y);
        }
        var extent = new PanelPolygonExtent(new[] { minX, minY }, new[] { maxX, maxY });
        double? area = null, perimeter = null;
        string winding = "unknown";
        try
        {
            // Roundoff depends on differences and products, never the absolute
            // coordinate offset. Relative-to-first-vertex area avoids catastrophic
            // cancellation when a small panel has a large world translation.
            Number(maxX - minX); Number(maxY - minY);
            var lengthSum = new CompensatedSum();
            for (int i = 0; i < count; i++)
            {
                double length = Distance(points[i], points[(i + 1) % count]);
                lengthSum.Add(length);
                if (length <= CoordinateTolerance)
                    issues.Add("zero_length_edge", "An edge is zero length within the coordinate tolerance.", i, (i + 1) % count, i);
            }
            perimeter = lengthSum.Value;

            var twiceArea = new CompensatedSum();
            var areaRoundoff = new CompensatedSum();
            for (int i = 1; i < count - 1; i++)
            {
                Point a = Delta(points[0], points[i]);
                Point b = Delta(points[0], points[i + 1]);
                double left = Number(a.X * b.Y), right = Number(a.Y * b.X);
                twiceArea.Add(Number(left - right));
                areaRoundoff.Add(RoundoffTolerance * 0.5 * Math.Abs(left));
                areaRoundoff.Add(RoundoffTolerance * 0.5 * Math.Abs(right));
            }
            area = Number(twiceArea.Value * 0.5);
            double areaTolerance = Number(CoordinateTolerance * (perimeter.Value * 0.5) + areaRoundoff.Value);
            if (Math.Abs(area.Value) <= areaTolerance)
            {
                winding = "degenerate";
                issues.Add("degenerate_area", "The signed area is zero or too small relative to the boundary tolerance.");
            }
            else
            {
                winding = area.Value > 0 ? "counterclockwise" : "clockwise";
                if (requireCounterClockwise && area.Value < 0)
                    issues.Add("clockwise_winding", "Creation requires counterclockwise vertices as viewed from local +Z; reverse the submitted order explicitly.");
            }

            for (int i = 0; i < count; i++)
                for (int j = i + 1; j < count; j++)
                    if (Distance(points[i], points[j]) <= CoordinateTolerance)
                        issues.Add("duplicate_vertex", "Vertices repeat the same coordinate within tolerance; a closing duplicate is also invalid.", i, j);

            for (int i = 0; i < count; i++)
            {
                int next = (i + 1) % count, after = (i + 2) % count;
                if (Orientation(points[i], points[next], points[after]) == 0 &&
                    SameDirection(Delta(points[next], points[i]), Delta(points[next], points[after])))
                    issues.Add("adjacent_edge_overlap", "Adjacent edges backtrack over the same boundary.", null, null, i, next);

                for (int j = i + 1; j < count; j++)
                {
                    int jNext = (j + 1) % count;
                    if (j == next || jNext == i) continue;
                    CheckNonAdjacent(points[i], points[next], points[j], points[jNext], i, j, issues);
                }
            }
        }
        catch (NumericRangeException)
        {
            // Fail closed when a coordinate difference, product, metric or sum
            // cannot be represented by a finite double. Never emit NaN/Infinity.
            issues.Add("numeric_range", "Coordinate differences or polygon measurements exceed the supported finite numeric range.");
        }
        return Result(issues, area, perimeter, extent, winding);
    }

    private static void CheckNonAdjacent(Point a, Point b, Point c, Point d, int first, int second, IssueList issues)
    {
        if (Separated(a.X, b.X, c.X, d.X) || Separated(a.Y, b.Y, c.Y, d.Y)) return;
        int abc = Orientation(a, b, c), abd = Orientation(a, b, d);
        int cda = Orientation(c, d, a), cdb = Orientation(c, d, b);
        if (abc * abd < 0 && cda * cdb < 0)
        {
            issues.Add("self_intersection", "Nonadjacent edges cross.", null, null, first, second);
            return;
        }
        if (abc == 0 && abd == 0 && cda == 0 && cdb == 0)
        {
            // Project on the longer axis to distinguish a shared endpoint from
            // an interval overlap. Both are forbidden for nonadjacent edges.
            bool useX = Math.Abs(b.X - a.X) >= Math.Abs(b.Y - a.Y);
            double a0 = useX ? a.X : a.Y, a1 = useX ? b.X : b.Y;
            double b0 = useX ? c.X : c.Y, b1 = useX ? d.X : d.Y;
            double overlap = Math.Min(Math.Max(a0, a1), Math.Max(b0, b1)) -
                Math.Max(Math.Min(a0, a1), Math.Min(b0, b1));
            if (overlap >= -CoordinateTolerance)
                issues.Add(overlap > CoordinateTolerance ? "collinear_edge_overlap" : "edge_touch",
                    "Nonadjacent edges overlap or touch within tolerance.", null, null, first, second);
            return;
        }
        if ((abc == 0 && Within(c, a, b)) || (abd == 0 && Within(d, a, b)) ||
            (cda == 0 && Within(a, c, d)) || (cdb == 0 && Within(b, c, d)))
            issues.Add("edge_touch", "A nonadjacent edge touches another edge within tolerance.", null, null, first, second);
    }

    private static bool Separated(double a, double b, double c, double d)
    {
        return Math.Min(c, d) - Math.Max(a, b) > CoordinateTolerance ||
            Math.Min(a, b) - Math.Max(c, d) > CoordinateTolerance;
    }

    private static bool Within(Point point, Point a, Point b)
    {
        return point.X - Math.Max(a.X, b.X) <= CoordinateTolerance &&
            Math.Min(a.X, b.X) - point.X <= CoordinateTolerance &&
            point.Y - Math.Max(a.Y, b.Y) <= CoordinateTolerance &&
            Math.Min(a.Y, b.Y) - point.Y <= CoordinateTolerance;
    }

    private static int Orientation(Point a, Point b, Point c)
    {
        Point ab = Delta(a, b), ac = Delta(a, c);
        double left = Number(ab.X * ac.Y), right = Number(ab.Y * ac.X);
        double determinant = Number(left - right);
        double tolerance = Number(CoordinateTolerance * Length(ab) +
            RoundoffTolerance * Math.Abs(left) + RoundoffTolerance * Math.Abs(right));
        return determinant > tolerance ? 1 : determinant < -tolerance ? -1 : 0;
    }

    private static bool SameDirection(Point a, Point b)
    {
        double scaleA = Math.Max(Math.Abs(a.X), Math.Abs(a.Y));
        double scaleB = Math.Max(Math.Abs(b.X), Math.Abs(b.Y));
        if (scaleA == 0 || scaleB == 0) return false;
        return (a.X / scaleA) * (b.X / scaleB) + (a.Y / scaleA) * (b.Y / scaleB) > RoundoffTolerance;
    }

    private static Point Delta(Point a, Point b) { return new Point(Number(b.X - a.X), Number(b.Y - a.Y)); }
    private static double Distance(Point a, Point b) { return Length(Delta(a, b)); }
    private static double Length(Point vector)
    {
        double high = Math.Max(Math.Abs(vector.X), Math.Abs(vector.Y));
        if (high == 0) return 0;
        double ratio = Math.Min(Math.Abs(vector.X), Math.Abs(vector.Y)) / high;
        return Number(high * Math.Sqrt(1 + ratio * ratio));
    }
    private static bool Finite(double value) { return !double.IsNaN(value) && !double.IsInfinity(value); }
    private static double Number(double value) { if (!Finite(value)) throw new NumericRangeException(); return value; }

    private static PanelPolygonValidation Result(IssueList issues, double? area = null,
        double? perimeter = null, PanelPolygonExtent extent = null, string winding = "unknown")
    {
        return new PanelPolygonValidation(issues.Count == 0, area, perimeter, extent, winding,
            issues.Items.ToArray(), issues.Count > IssueLimit);
    }

    private struct Point
    {
        internal readonly double X, Y;
        internal Point(double x, double y) { X = x; Y = y; }
    }
    private sealed class NumericRangeException : Exception { }
    private sealed class CompensatedSum
    {
        private double sum, correction;
        internal void Add(double value)
        {
            double next = Number(sum + value);
            correction = Number(correction + (Math.Abs(sum) >= Math.Abs(value) ? (sum - next) + value : (value - next) + sum));
            sum = next;
        }
        internal double Value { get { return Number(sum + correction); } }
    }
    private sealed class IssueList
    {
        internal readonly List<PanelPolygonIssue> Items = new List<PanelPolygonIssue>();
        internal int Count;
        internal void Add(string code, string message, int? vertex = null, int? otherVertex = null,
            int? edge = null, int? otherEdge = null)
        {
            Count++;
            if (Items.Count < IssueLimit)
                Items.Add(new PanelPolygonIssue(code, message, vertex, otherVertex, edge, otherEdge));
        }
    }
}

internal sealed class PanelPolygonValidation
{
    public bool valid { get; private set; }
    public double? signed_area_mm2 { get; private set; }
    public double? perimeter_mm { get; private set; }
    public PanelPolygonExtent extent { get; private set; }
    public string winding { get; private set; }
    public PanelPolygonIssue[] issues { get; private set; }
    public bool issues_truncated { get; private set; }
    internal PanelPolygonValidation(bool valid, double? area, double? perimeter, PanelPolygonExtent extent,
        string winding, PanelPolygonIssue[] issues, bool truncated)
    {
        this.valid = valid; signed_area_mm2 = area; perimeter_mm = perimeter; this.extent = extent;
        this.winding = winding; this.issues = issues; issues_truncated = truncated;
    }
}

internal sealed class PanelPolygonExtent
{
    public double[] min { get; private set; }
    public double[] max { get; private set; }
    internal PanelPolygonExtent(double[] min, double[] max) { this.min = min; this.max = max; }
}

internal sealed class PanelPolygonIssue
{
    public string code { get; private set; }
    public string message { get; private set; }
    public int? vertex_index { get; private set; }
    public int? other_vertex_index { get; private set; }
    public int? edge_index { get; private set; }
    public int? other_edge_index { get; private set; }
    internal PanelPolygonIssue(string code, string message, int? vertex, int? otherVertex, int? edge, int? otherEdge)
    {
        this.code = code; this.message = message; vertex_index = vertex; other_vertex_index = otherVertex;
        edge_index = edge; other_edge_index = otherEdge;
    }
}
