using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

internal sealed class ModelEditInput
{
    internal readonly double[] Position, XAxis, YAxis;
    internal readonly string Wrt;
    internal ModelEditInput(double[] position, double[] xAxis, double[] yAxis, string wrt)
    {
        Position = position; XAxis = xAxis; YAxis = yAxis; Wrt = wrt;
    }
}

internal sealed partial class E3d21Context
{
    // PRODUCTION_METHODS

    private static double ModelFinite(double value) { return value; }

    private static double[] Vector(double x, double y, double z) { return new[] { x, y, z }; }

    private static string Fingerprint(string parent, string requestedType, string typeName, string typeShort,
        string name, IDictionary<string, object> attributes, string wrt, double[] position, double[] xAxis,
        double[] yAxis, double[] nativePosition, double[] nativeXAxis, double[] nativeYAxis)
    {
        return ModelCreateRequestRevision(parent, requestedType, typeName, typeShort, name, attributes,
            new ModelEditInput(position, xAxis, yAxis, wrt),
            new ModelEditInput(nativePosition, nativeXAxis, nativeYAxis, "native"));
    }

    public static int Main()
    {
        var attributes = new Dictionary<string, object> { ["DESC"] = "body", ["HEIG"] = 10.0 };
        double[] position = Vector(1, 2, 3), xAxis = Vector(1, 0, 0), yAxis = Vector(0, 1, 0);
        double[] nativePosition = Vector(4, 5, 6), nativeXAxis = Vector(1, 0, 0), nativeYAxis = Vector(0, 1, 0);
        string baseline = Fingerprint("parent-state", "CYLI", "CYLI", "CYLI", "/BODY", attributes,
            "OWNER", position, xAxis, yAxis, nativePosition, nativeXAxis, nativeYAxis);
        Check(baseline.Length == 64, "Create preview revision is SHA256.");
        Check(!ModelCreateRevisionConflict(baseline, baseline, baseline), "Unchanged preview and immediate revisions permit creation.");
        Check(ModelCreateRevisionConflict("stale", baseline, baseline), "A stale requested revision is rejected.");
        Check(ModelCreateRevisionConflict(baseline, "changed", "changed"), "A changed preview state is rejected.");
        Check(ModelCreateRevisionConflict(baseline, baseline, "changed"), "A pre-create state change is rejected.");
        Check(ModelCreateRevisionConflict(null, baseline, baseline), "Execution without a revision is rejected.");

        var changed = new List<string>
        {
            Fingerprint("other-parent-state", "CYLI", "CYLI", "CYLI", "/BODY", attributes,
                "OWNER", position, xAxis, yAxis, nativePosition, nativeXAxis, nativeYAxis),
            Fingerprint("parent-state", "BOX", "BOX", "BOX", "/BODY", attributes,
                "OWNER", position, xAxis, yAxis, nativePosition, nativeXAxis, nativeYAxis),
            Fingerprint("parent-state", "CYLI", "CYLI", "CYLI", "/BODY2", attributes,
                "OWNER", position, xAxis, yAxis, nativePosition, nativeXAxis, nativeYAxis),
            Fingerprint("parent-state", "CYLI", "CYLI", "CYLI", "/BODY", attributes,
                "WORLD", position, xAxis, yAxis, nativePosition, nativeXAxis, nativeYAxis),
            Fingerprint("parent-state", "CYLI", "CYLI", "CYLI", "/BODY", attributes,
                "OWNER", Vector(1, 2, 4), xAxis, yAxis, nativePosition, nativeXAxis, nativeYAxis),
            Fingerprint("parent-state", "CYLI", "CYLI", "CYLI", "/BODY", attributes,
                "OWNER", position, Vector(0, 1, 0), yAxis, nativePosition, nativeXAxis, nativeYAxis),
            Fingerprint("parent-state", "CYLI", "CYLI", "CYLI", "/BODY", attributes,
                "OWNER", position, xAxis, yAxis, Vector(4, 5, 7), nativeXAxis, nativeYAxis),
            Fingerprint("parent-state", "CYLI", "CYLI", "CYLI", "/BODY", attributes,
                "OWNER", position, xAxis, yAxis, nativePosition, nativeXAxis, Vector(0, 0, 1)),
            Fingerprint("parent-state", "CYLI", "CYLI", "CYLI", "/BODY", new Dictionary<string, object>
                { ["DESC"] = "other", ["HEIG"] = 10.0 },
                "OWNER", position, xAxis, yAxis, nativePosition, nativeXAxis, nativeYAxis),
            Fingerprint("parent-state", "CYLI", "CYLI", "CYLI", "/BODY", new Dictionary<string, object>
                { ["DESC"] = "body", ["HEIG"] = 11.0 },
                "OWNER", position, xAxis, yAxis, nativePosition, nativeXAxis, nativeYAxis)
        };
        Check(changed.All(value => value != baseline), "Each parent/request/type/name/attribute/frame/transform change invalidates the revision.");
        Console.WriteLine("Create revision production checks passed: 15 assertions.");
        return 0;
    }

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }
}
