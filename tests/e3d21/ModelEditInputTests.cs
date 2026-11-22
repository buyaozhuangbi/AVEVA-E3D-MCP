#if MODEL_EDIT_INPUT_TESTS
using System;
using System.Linq;
using E3d21AiBridge;

internal static class ModelEditInputTests
{
    private static int checks;

    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) throw new Exception(message);
    }

    private static void Reject(Action action, string message)
    {
        bool rejected = false;
        try { action(); }
        catch (ArgumentException) { rejected = true; }
        Check(rejected, message);
    }

    private static void Main()
    {
        foreach (string invalid in new[] { null, "", " ", "CE", "OWNER", "WORLD", "PUMP", "/", "=1", "=1/2/3", "=a/2", "=0/2", "=1/-2", "=1/2 NEW BOX", "/PUMP\nDELETE" })
            Reject(() => ModelEditInput.ExplicitReference(invalid, "target"), "An edit target must never fall back to CE: " + invalid);
        Check(ModelEditInput.ExplicitReference(" /PUMP ", "target") == "/PUMP", "Explicit names may be trimmed.");
        Check(ModelEditInput.ExplicitReference("=123/456", "target") == "=123/456", "A numeric DBREF must be preserved.");
        Check(ModelEditInput.ExplicitReference("=123/0", "target") == "=123/0", "A Design WORLD DBREF uses element index zero.");
        foreach (string worldInvalid in new[] { "=123/-1", "=123/+1", "=123/0.0", "=123/" })
            Reject(() => ModelEditInput.ExplicitReference(worldInvalid, "target"), "Only nonnegative integer WORLD indices are valid: " + worldInvalid);
        Check(ModelEditInput.IsReferencePair(new[] { 2000000002, 0 }), "SDK WORLD reference pairs must accept element index zero.");
        Check(ModelEditInput.IsReferencePair(new[] { 1, 1 }), "Regular SDK element reference pairs remain valid.");
        foreach (int[] invalidPair in new[] { null, new int[0], new[] { 1 }, new[] { 1, 2, 3 }, new[] { 0, 0 }, new[] { -1, 0 }, new[] { 1, -1 } })
            Check(!ModelEditInput.IsReferencePair(invalidPair), "SDK reference pairs require positive database and nonnegative element indices.");
        Check(new ModelEditInput(null, null, null, null, false).Wrt == "OWNER", "Creation defaults to the explicit parent's frame.");
        Reject(() => new ModelEditInput(null, null, null, null, true), "An empty transform must be rejected.");
        foreach (string invalid in new[] { "CE", "PUMP", "=1", "=1/2 SAVEWORK" })
            Reject(() => new ModelEditInput(new[] { 0.0, 0.0, 0.0 }, null, null, invalid, true), "Implicit/malformed WRT must be rejected.");
        foreach (string frame in new[] { "owner", "world", "/PUMP", "=1/2" })
            Check(new ModelEditInput(new[] { 0.0, 0.0, 0.0 }, null, null, frame, true).Wrt == (frame[0] == '/' || frame[0] == '=' ? frame : frame.ToUpperInvariant()), "Allowed frame must be preserved.");
        foreach (double[] invalid in new[] { new double[0], new[] { 1.0, 2.0 }, new[] { double.NaN, 0, 0 }, new[] { double.PositiveInfinity, 0, 0 } })
            Reject(() => new ModelEditInput(invalid, null, null, null, true), "Invalid position must be rejected before SDK access.");
        Reject(() => new ModelEditInput(null, new[] { 1.0, 0, 0 }, null, null, true), "A lone orientation axis must be rejected.");
        Reject(() => new ModelEditInput(null, new[] { 2.0, 0, 0 }, new[] { 0.0, 1, 0 }, null, true), "Non-unit axes must not be silently normalised.");
        Reject(() => new ModelEditInput(null, new[] { 0.0, 0, 0 }, new[] { 0.0, 1, 0 }, null, true), "A zero axis must be rejected.");
        Reject(() => new ModelEditInput(null, new[] { 1.0, 0, 0 }, new[] { 1.0, 0, 0 }, null, true), "Collinear axes must be rejected.");
        Reject(() => new ModelEditInput(null, new[] { 1.0, 0, 0 }, new[] { 0.6, 0.8, 0 }, null, true), "Unit but nonorthogonal axes must be rejected.");
        Reject(() => new ModelEditInput(null, new[] { double.MaxValue, 0, 0 }, new[] { 0.0, 1, 0 }, null, true), "Finite inputs whose squared length overflows must be rejected.");
        double[] position = { 1, 2, 3 };
        double[] x = { Math.Sqrt(0.5), Math.Sqrt(0.5), 0 };
        double[] y = { -Math.Sqrt(0.5), Math.Sqrt(0.5), 0 };
        var input = new ModelEditInput(position, x, y, "WORLD", true);
        Check(input.XAxis.SequenceEqual(x) && input.YAxis.SequenceEqual(y), "Valid axes must be retained without normalisation.");
        Check(new ModelEditInput(null, new[] { 1.00000075, 0, 0 }, new[] { 0.0, 1, 0 }, null, true).XAxis[0] == 1.00000075,
            "Unit tolerance applies to the axis length, consistently with the protocol validator.");
        Reject(() => new ModelEditInput(null, new[] { 1.0000015, 0, 0 }, new[] { 0.0, 1, 0 }, null, true),
            "Axis length outside the shared tolerance must be rejected.");
        position[0] = x[0] = y[0] = 99;
        Check(input.Position[0] == 1 && input.XAxis[0] != 99 && input.YAxis[0] != 99, "Validated arrays must be copied before UI dispatch.");
        foreach (string forbidden in new[] { "NAME", "POS", "ORI", "OWNER", "REF", "UNRESTRICTED" })
            Reject(() => ModelEditInput.AttributeValue(forbidden, "1"), "Initial attributes must respect the allowlist: " + forbidden);
        Reject(() => ModelEditInput.AttributeValue("DESC", null), "Null dictionary values must not be silently converted to strings.");
        Reject(() => ModelEditInput.AttributeValue("XLEN", "NaN"), "Non-finite initial dimensions must be rejected.");
        Reject(() => ModelEditInput.AttributeValue("XLEN", "1,5"), "Initial dimensions must use invariant numeric notation.");
        Check((double)ModelEditInput.AttributeValue("XLEN", "125.5") == 125.5, "Allowed dimensions must retain their numeric values.");
        Check((string)ModelEditInput.AttributeValue("DESC", "泵体") == "泵体", "Allowed text must be preserved.");
        Reject(() => ModelEditInput.ElementName("/PUMP\nDELETE"), "Names must reject line breaks.");
        Check(ModelEditInput.ElementName(null) == null && ModelEditInput.ElementName("") == null, "Optional omitted names remain omitted.");
        foreach (string validName in new[] { new string('A', 49), new string('A', 50), "/" + new string('A', 49) })
            Check(ModelEditInput.ElementName(validName) == validName, "Name boundary accepts at most 50 submitted characters without truncation.");
        foreach (string longName in new[] { new string('A', 51), "/" + new string('A', 50), "/CODEX-TNOTIFY-0B70551320194F1BA12734C33105C37F-EQUI" })
            Reject(() => ModelEditInput.ElementName(longName), "Overlong names, including the live 52-character failure, reject before SDK creation.");
        Reject(() => ModelEditInput.TypeName("BOX DELETE ALL"), "Type must be a noun, not command text.");
        Check(ModelEditInput.TypeName(":CUSTOM_TYPE") == ":CUSTOM_TYPE", "UDET names may reach SDK type validation.");
        var quarterTurn = new ModelEditInput(null, new[] { 0.0, 1, 0 }, new[] { -1.0, 0, 0 }, "OWNER", true);
        Check(quarterTurn.OrientationMatches(new[] { 0.0, 1, 0 }, new[] { -1.0, 0, 0 }, new[] { 0.0, 0, 1 }),
            "A positive quarter-turn about Z must preserve the submitted local X and Y axes.");
        Check(!quarterTurn.OrientationMatches(new[] { 0.0, -1, 0 }, new[] { 1.0, 0, 0 }, new[] { 0.0, 0, 1 }),
            "The observed E3D two-Direction inverse-rotation regression must fail readback verification.");
        Check(!quarterTurn.OrientationMatches(new[] { 0.0, 1, 0 }, new[] { -1.0, 0, 0 }, new[] { 0.0, 0, -1 }),
            "Orientation readback must check the right-handed third axis.");
        Check(!quarterTurn.OrientationMatches(new[] { double.NaN, 1, 0 }, new[] { -1.0, 0, 0 }, new[] { 0.0, 0, 1 }),
            "Non-finite orientation readback must fail.");
        var generalRotation = new ModelEditInput(null, new[] { 0.0, 0, 1 }, new[] { 1.0, 0, 0 }, "WORLD", true);
        Check(generalRotation.OrientationMatches(new[] { 0.0, 0, 1 }, new[] { 1.0, 0, 0 }, new[] { 0.0, 1, 0 }),
            "Readback verification must also handle an orientation that changes the local Z axis.");
        Check(ModelEditInput.PositionMatches(new[] { 80.0, 210, 330 }, new[] { 80.0000001, 210.0, 330 }),
            "Position readback may tolerate floating-point roundoff.");
        var storageFrame = new ModelEditFrame(new[] { 100.0, 200, 300 },
            new[] { 0.0, 1, 0 }, new[] { -1.0, 0, 0 }, new[] { 0.0, 0, 1 });
        var requestedTransform = new ModelEditInput(new[] { 100.0, 220, 350 },
            new[] { 1.0, 0, 0 }, new[] { 0.0, 1, 0 }, "WORLD", true);
        ModelEditInput nativeTransform = storageFrame.ToNative(requestedTransform);
        Check(ModelEditInput.PositionMatches(new[] { 20.0, 0, 50 }, nativeTransform.Position),
            "The rotated/translated SUBE regression must store [20,0,50] for requested [100,220,350].");
        Check(nativeTransform.OrientationMatches(new[] { 0.0, -1, 0 }, new[] { 1.0, 0, 0 }, new[] { 0.0, 0, 1 }),
            "Requested world identity must be stored as the inverse SUBE rotation.");
        Check(ModelEditInput.PositionMatches(new[] { 10.0, 20, 30 }, storageFrame.PositionToNative(new[] { 80.0, 210, 330 })),
            "Original requested-frame values must convert back to their original native coordinates, avoiding double transforms on rollback.");
        ModelEditInput nativeVertex = storageFrame.ToNative(new ModelEditInput(new[] { 80.0, 210, 330 }, null, null, "WORLD", true));
        Check(ModelEditInput.PositionMatches(new[] { 10.0, 20, 30 }, nativeVertex.Position) && nativeVertex.XAxis == null,
            "Position-only PAVE conversion must use the enclosing PANE frame without inventing a vertex orientation.");
        Check(ModelEditInput.PositionMatches(new[] { 100.0, 220, 350 }, ModelEditFrame.Identity().ToNative(requestedTransform).Position),
            "A request in its actual storage frame must preserve the coordinates exactly.");
        var nestedFrame = new ModelEditFrame(new[] { 80.0, 210, 330 },
            new[] { 0.0, 0, 1 }, new[] { 0.0, 1, 0 }, new[] { -1.0, 0, 0 });
        ModelEditInput nestedNative = nestedFrame.ToNative(requestedTransform);
        Check(ModelEditInput.PositionMatches(new[] { 20.0, 10, -20 }, nestedNative.Position),
            "SDK-composed nested frames must convert all three axes and their translated origin.");
        Check(nestedNative.OrientationMatches(new[] { 0.0, 0, -1 }, new[] { 0.0, 1, 0 }, new[] { 1.0, 0, 0 }),
            "Non-planar native frame conversion must rotate orientation axes without applying translation.");
        var angledFrame = new ModelEditFrame(new[] { -7.0, 4, 12 },
            new[] { Math.Sqrt(0.5), Math.Sqrt(0.5), 0 },
            new[] { -Math.Sqrt(0.5), Math.Sqrt(0.5), 0 }, new[] { 0.0, 0, 1 });
        Check(ModelEditInput.PositionMatches(new[] { Math.Sqrt(2.0), 0, 3 }, angledFrame.PositionToNative(new[] { -6.0, 5, 15 })),
            "Frame conversion must support non-cardinal rotations without rounding or normalising the request.");
        double[] frameOrigin = { 1, 2, 3 }, frameX = { 1, 0, 0 }, frameY = { 0, 1, 0 }, frameZ = { 0, 0, 1 };
        var copiedFrame = new ModelEditFrame(frameOrigin, frameX, frameY, frameZ);
        frameOrigin[0] = frameX[0] = frameY[1] = frameZ[2] = 99;
        Check(ModelEditInput.PositionMatches(new[] { 3.0, 3, 3 }, copiedFrame.PositionToNative(new[] { 4.0, 5, 6 })),
            "Frame snapshots must retain independent copies of every SDK vector.");
        Reject(() => new ModelEditFrame(null, new[] { 1.0, 0, 0 }, new[] { 0.0, 1, 0 }, new[] { 0.0, 0, 1 }),
            "Missing SDK origins must not become zero translations.");
        Reject(() => new ModelEditFrame(new[] { 0.0, 0, 0 }, new[] { 1.0, 0, 0 }, new[] { 0.0, 1, 0 }, new[] { 0.0, 0, -1 }),
            "An incomplete or left-handed SDK frame must not be used for mutation.");
        Reject(() => new ModelEditFrame(new[] { 0.0, 0, 0 }, new[] { 1.0, 0, 0 }, new[] { 0.0, 1, 0 }, new[] { double.NaN, 0, 1 }),
            "A non-finite SDK frame must fail before mutation.");
        Check(!ModelEditInput.PositionMatches(new[] { 80.0, 210, 330 }, new[] { 120.0, 190, 330 }),
            "The inverse-rotation coordinate error must not pass readback verification.");
        Check(!ModelEditInput.PositionMatches(new[] { 1.0, 2, 3 }, new[] { double.PositiveInfinity, 2, 3 }),
            "Non-finite position readback must fail.");
        Check(ModelEditInput.NumberMatches(123.4, 123.4000001) && !ModelEditInput.NumberMatches(123.4, 120.0),
            "Dimension readback must compare the submitted numeric value.");
        Check(ModelEditInput.TextMatches("/Pump-A", "PUMP-A", true), "Database names may normalise slash and case.");
        Check(!ModelEditInput.TextMatches("/Pump-A", "/Pump-B", true), "Name readback must reject a different database name.");
        Check(ModelEditInput.TextMatches("泵体", "泵体", false) && !ModelEditInput.TextMatches("Duty A", "duty a", false),
            "Text attributes require exact readback, preserving case.");
        Console.WriteLine("Model edit input checks passed: " + checks);
    }
}
#endif
