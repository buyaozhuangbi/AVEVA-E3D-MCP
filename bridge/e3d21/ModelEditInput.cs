using System;
using System.Globalization;
using System.Linq;

namespace E3d21AiBridge;

// Input-only checks deliberately have no SDK dependency and never normalise axes.
internal sealed class ModelEditInput
{
    internal const double AxisTolerance = 0.000001;
    internal const double CoordinateTolerance = 0.00001;
    internal const double RelativeTolerance = 0.0000000001;
    internal const double OrientationReadbackTolerance = AxisTolerance * 4;
    internal readonly double[] Position;
    internal readonly double[] XAxis;
    internal readonly double[] YAxis;
    internal readonly string Wrt;

    internal ModelEditInput(double[] position, double[] xAxis, double[] yAxis, string wrt, bool required)
    {
        Position = Vector(position, "position");
        XAxis = Vector(xAxis, "x_axis");
        YAxis = Vector(yAxis, "y_axis");
        if ((XAxis == null) != (YAxis == null))
            throw new ArgumentException("orientation requires both x_axis and y_axis.");
        if (required && Position == null && XAxis == null)
            throw new ArgumentException("Supply position, orientation, or both.");
        if (XAxis != null)
        {
            if (Math.Abs(Math.Sqrt(Dot(XAxis, XAxis)) - 1.0) > AxisTolerance ||
                Math.Abs(Math.Sqrt(Dot(YAxis, YAxis)) - 1.0) > AxisTolerance)
                throw new ArgumentException("x_axis and y_axis must be unit vectors; axes are not normalised automatically.");
            if (Math.Abs(Dot(XAxis, YAxis)) > AxisTolerance)
                throw new ArgumentException("x_axis and y_axis must be orthogonal.");
        }
        Wrt = string.IsNullOrWhiteSpace(wrt) ? "OWNER" : wrt.Trim();
        if (Wrt.Equals("OWNER", StringComparison.OrdinalIgnoreCase) ||
            Wrt.Equals("WORLD", StringComparison.OrdinalIgnoreCase))
            Wrt = Wrt.ToUpperInvariant();
        else
            Wrt = ExplicitReference(Wrt, "wrt");
    }

    internal static string ExplicitReference(string value, string parameter)
    {
        if (string.IsNullOrWhiteSpace(value))
            throw new ArgumentException(parameter + " must explicitly identify /NAME or =database/element; current element is not a default.", parameter);
        string text = value.Trim();
        if (text.Any(char.IsWhiteSpace) || text.Any(char.IsControl))
            throw new ArgumentException(parameter + " must be /NAME or =database/element.", parameter);
        if (text.StartsWith("/", StringComparison.Ordinal) && text.Length > 1)
            return text;
        if (text.StartsWith("=", StringComparison.Ordinal))
        {
            string[] pair = text.Substring(1).Split('/');
            int database, element;
            if (pair.Length == 2 &&
                int.TryParse(pair[0], NumberStyles.None, CultureInfo.InvariantCulture, out database) && database > 0 &&
                int.TryParse(pair[1], NumberStyles.None, CultureInfo.InvariantCulture, out element) && element >= 0)
                return text;
        }
        throw new ArgumentException(parameter + " must be /NAME or =database/element.", parameter);
    }

    internal static string TypeName(string value)
    {
        string text = (value ?? "").Trim();
        if (text.Length == 0 || text.Length > 64 ||
            text.Any(c => !(char.IsLetterOrDigit(c) || c == ':' || c == '_')))
            throw new ArgumentException("type must be a Design element type name.", "typeName");
        return text;
    }

    internal static bool IsReferencePair(int[] reference)
    {
        return reference != null && reference.Length == 2 && reference[0] > 0 && reference[1] >= 0;
    }

    internal static string ElementName(string value)
    {
        if (value == null || value.Length == 0) return null;
        // E3D21 rejected NAME over 50 characters in the 2026-11-16 live probe.
        // Conservatively count the complete submitted string, including '/'.
        // This is an input guard, not a claim that all remaining names are SDK-valid.
        if (value.Length > 50)
            throw new ArgumentException("E3D21 name input is limited to 50 characters, conservatively including any leading slash; nothing was created.", "name");
        if (value.Any(char.IsWhiteSpace) || value.Any(char.IsControl))
            throw new ArgumentException("name must be a single database name without whitespace or control characters.", "name");
        return value;
    }

    internal static object AttributeValue(string canonical, string value)
    {
        if (canonical == "NAME" || canonical == "POS" || canonical == "ORI")
            throw new ArgumentException("Use the dedicated name/position/orientation parameters instead of attributes[" + canonical + "].");
        if (value == null)
            throw new ArgumentException("Attribute values must be strings, not null: " + canonical);
        return AttributeEditPolicy.Parse(canonical, value);
    }

    internal static bool NumberMatches(double expected, double actual)
    {
        return IsFinite(expected) && IsFinite(actual) && Math.Abs(expected - actual) <=
            CoordinateTolerance + RelativeTolerance * Math.Max(Math.Abs(expected), Math.Abs(actual));
    }

    internal static bool PositionMatches(double[] expected, double[] actual)
    {
        return expected != null && actual != null && expected.Length == 3 && actual.Length == 3 &&
            expected.Zip(actual, NumberMatches).All(matches => matches);
    }

    internal bool OrientationMatches(double[] actualX, double[] actualY, double[] actualZ)
    {
        if (XAxis == null) return false;
        double[] expectedZ =
        {
            XAxis[1] * YAxis[2] - XAxis[2] * YAxis[1],
            XAxis[2] * YAxis[0] - XAxis[0] * YAxis[2],
            XAxis[0] * YAxis[1] - XAxis[1] * YAxis[0]
        };
        return AxisMatches(XAxis, actualX) && AxisMatches(YAxis, actualY) && AxisMatches(expectedZ, actualZ);
    }

    internal static bool TextMatches(string expected, string actual, bool databaseName)
    {
        if (expected == null || actual == null) return expected == actual;
        if (databaseName)
            return string.Equals(expected.TrimStart('/'), actual.TrimStart('/'), StringComparison.OrdinalIgnoreCase);
        return string.Equals(expected, actual, StringComparison.Ordinal);
    }

    private static bool AxisMatches(double[] expected, double[] actual)
    {
        return actual != null && actual.Length == 3 &&
            expected.Zip(actual, (a, b) => IsFinite(b) && Math.Abs(a - b) <= OrientationReadbackTolerance).All(matches => matches);
    }

    private static bool IsFinite(double value)
    {
        return !double.IsNaN(value) && !double.IsInfinity(value);
    }

    private static double[] Vector(double[] input, string name)
    {
        if (input == null) return null;
        if (input.Length != 3 || input.Any(value => double.IsNaN(value) || double.IsInfinity(value)))
            throw new ArgumentException(name + " must contain exactly three finite numbers.", name);
        return (double[])input.Clone();
    }

    private static double Dot(double[] a, double[] b)
    {
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    }
}

// The SDK's POS/ORI setters store native coordinates even when given a WRT
// qualifier. A frame's columns are its local axes expressed in the requested
// reference; inverse rotation therefore uses dot products with those columns.
internal sealed class ModelEditFrame
{
    private readonly double[] origin;
    private readonly double[] x;
    private readonly double[] y;
    private readonly double[] z;

    internal ModelEditFrame(double[] origin, double[] xAxis, double[] yAxis, double[] zAxis)
    {
        var validated = new ModelEditInput(origin, xAxis, yAxis, "OWNER", true);
        if (validated.Position == null || !validated.OrientationMatches(xAxis, yAxis, zAxis))
            throw new ArgumentException("The SDK did not return a complete right-handed reference frame.");
        this.origin = validated.Position;
        x = validated.XAxis;
        y = validated.YAxis;
        z = (double[])zAxis.Clone();
    }

    internal static ModelEditFrame Identity()
    {
        return new ModelEditFrame(new[] { 0.0, 0, 0 }, new[] { 1.0, 0, 0 },
            new[] { 0.0, 1, 0 }, new[] { 0.0, 0, 1 });
    }

    internal double[] PositionToNative(double[] requested)
    {
        if (requested == null) return null;
        return DirectionToNative(new[] { requested[0] - origin[0], requested[1] - origin[1], requested[2] - origin[2] });
    }

    internal double[] DirectionToNative(double[] requested)
    {
        if (requested == null) return null;
        return new[] { Dot(requested, x), Dot(requested, y), Dot(requested, z) };
    }

    internal ModelEditInput ToNative(ModelEditInput requested)
    {
        return new ModelEditInput(PositionToNative(requested.Position),
            DirectionToNative(requested.XAxis), DirectionToNative(requested.YAxis), "OWNER", false);
    }

    private static double Dot(double[] a, double[] b)
    {
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    }
}
