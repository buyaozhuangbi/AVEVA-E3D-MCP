using System;
using System.Linq;

namespace E3d21AiBridge;

// A rigid E3D frame whose basis vectors are expressed in WORLD. Inputs are
// validated exactly as observed; axes are never normalized or repaired.
internal sealed class CoordinateTransformFrame
{
    private const double BasisTolerance = 0.00001;
    private readonly double[] origin;
    private readonly double[] x;
    private readonly double[] y;
    private readonly double[] z;

    internal CoordinateTransformFrame(double[] position, double[] xAxis, double[] yAxis, double[] zAxis)
    {
        origin = Vector(position, "origin");
        x = Vector(xAxis, "x_axis");
        y = Vector(yAxis, "y_axis");
        z = Vector(zAxis, "z_axis");
        if (Math.Abs(Length(x) - 1) > BasisTolerance || Math.Abs(Length(y) - 1) > BasisTolerance ||
            Math.Abs(Length(z) - 1) > BasisTolerance || Math.Abs(Dot(x, y)) > BasisTolerance ||
            Math.Abs(Dot(x, z)) > BasisTolerance || Math.Abs(Dot(y, z)) > BasisTolerance ||
            Dot(Cross(x, y), z) < 1 - BasisTolerance)
            throw new ArgumentException("The SDK frame axes are not a verified right-handed orthonormal basis.");
    }

    internal static CoordinateTransformFrame World()
    {
        return new CoordinateTransformFrame(new[] { 0.0, 0, 0 }, new[] { 1.0, 0, 0 },
            new[] { 0.0, 1, 0 }, new[] { 0.0, 0, 1 });
    }

    internal double[] PointToWorld(double[] point)
    {
        Vector(point, "point");
        return Checked(new[] { origin[0] + x[0] * point[0] + y[0] * point[1] + z[0] * point[2],
            origin[1] + x[1] * point[0] + y[1] * point[1] + z[1] * point[2],
            origin[2] + x[2] * point[0] + y[2] * point[1] + z[2] * point[2] });
    }

    internal double[] VectorToWorld(double[] vector)
    {
        Vector(vector, "vector");
        return Checked(new[] { x[0] * vector[0] + y[0] * vector[1] + z[0] * vector[2],
            x[1] * vector[0] + y[1] * vector[1] + z[1] * vector[2],
            x[2] * vector[0] + y[2] * vector[1] + z[2] * vector[2] });
    }

    internal double[] PointFromWorld(double[] point)
    {
        Vector(point, "world point");
        var delta = new[] { point[0] - origin[0], point[1] - origin[1], point[2] - origin[2] };
        return Checked(new[] { Dot(delta, x), Dot(delta, y), Dot(delta, z) });
    }

    internal double[] VectorFromWorld(double[] vector)
    {
        Vector(vector, "world vector");
        return Checked(new[] { Dot(vector, x), Dot(vector, y), Dot(vector, z) });
    }

    private static double[] Vector(double[] value, string name)
    {
        if (value == null || value.Length != 3 || value.Any(item => double.IsNaN(item) || double.IsInfinity(item)))
            throw new ArgumentException(name + " must contain exactly three finite numbers.");
        return value;
    }

    private static double[] Checked(double[] value)
    {
        if (value.Any(item => double.IsNaN(item) || double.IsInfinity(item)))
            throw new ArgumentException("Coordinate conversion produced a non-finite result.");
        return value;
    }

    private static double Length(double[] value) { return Math.Sqrt(Dot(value, value)); }
    private static double Dot(double[] a, double[] b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
    private static double[] Cross(double[] a, double[] b)
    {
        return new[] { a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0] };
    }
}
