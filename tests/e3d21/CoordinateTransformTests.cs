using System;

namespace E3d21AiBridge.Tests;

internal static class CoordinateTransformTests
{
    private static int assertions;

    private static void Check(bool condition, string message)
    {
        assertions++;
        if (!condition) throw new InvalidOperationException(message);
    }

    private static bool Near(double actual, double expected) { return Math.Abs(actual - expected) < 1e-9; }

    private static void Throws(Action action, string message)
    {
        bool thrown = false;
        try { action(); } catch (ArgumentException) { thrown = true; }
        Check(thrown, message);
    }

    public static void Main()
    {
        var world = CoordinateTransformFrame.World();
        double[] identity = world.PointFromWorld(world.PointToWorld(new[] { 3.0, -4.0, 5.5 }));
        Check(identity[0] == 3 && identity[1] == -4 && identity[2] == 5.5, "WORLD round trip preserves point coordinates.");

        var rotated = new CoordinateTransformFrame(new[] { 10.0, 20, 30 }, new[] { 0.0, 1, 0 },
            new[] { -1.0, 0, 0 }, new[] { 0.0, 0, 1 });
        double[] worldPoint = rotated.PointToWorld(new[] { 1.0, 2, 3 });
        Check(Near(worldPoint[0], 8) && Near(worldPoint[1], 21) && Near(worldPoint[2], 33),
            "Point transform applies basis and translation in local-to-WORLD order.");
        double[] localPoint = rotated.PointFromWorld(worldPoint);
        Check(Near(localPoint[0], 1) && Near(localPoint[1], 2) && Near(localPoint[2], 3),
            "Point transform inverse restores local coordinates.");

        double[] worldVector = rotated.VectorToWorld(new[] { 1.0, 2, 3 });
        Check(Near(worldVector[0], -2) && Near(worldVector[1], 1) && Near(worldVector[2], 3),
            "Vector conversion applies rotation without the frame origin.");
        double[] localVector = rotated.VectorFromWorld(worldVector);
        Check(Near(localVector[0], 1) && Near(localVector[1], 2) && Near(localVector[2], 3),
            "Vector inverse restores local coordinates.");

        Throws(() => new CoordinateTransformFrame(new[] { 0.0, 0, 0 }, new[] { 2.0, 0, 0 },
            new[] { 0.0, 1, 0 }, new[] { 0.0, 0, 1 }), "Scaled SDK axes are rejected rather than normalized.");
        Throws(() => new CoordinateTransformFrame(new[] { 0.0, 0, 0 }, new[] { 1.0, 0, 0 },
            new[] { 0.0, 1, 0 }, new[] { 0.0, 0, -1 }), "Left-handed SDK axes are rejected.");
        Throws(() => new CoordinateTransformFrame(new[] { 0.0, 0, 0 }, new[] { 1.0, 0, 0 },
            new[] { 0.1, 1, 0 }, new[] { 0.0, 0, 1 }), "Non-orthogonal SDK axes are rejected.");
        Throws(() => world.PointToWorld(new[] { double.NaN, 0.0, 0 }), "Non-finite input coordinates are rejected.");
        var diagonal = new CoordinateTransformFrame(new[] { 0.0, 0, 0 },
            new[] { Math.Sqrt(0.5), Math.Sqrt(0.5), 0.0 }, new[] { -Math.Sqrt(0.5), Math.Sqrt(0.5), 0.0 },
            new[] { 0.0, 0, 1 });
        Throws(() => diagonal.PointToWorld(new[] { double.MaxValue, double.MaxValue, 0.0 }),
            "Non-finite conversion results are rejected.");
        Console.WriteLine("Coordinate transform math passed: " + assertions + " assertions.");
    }
}
