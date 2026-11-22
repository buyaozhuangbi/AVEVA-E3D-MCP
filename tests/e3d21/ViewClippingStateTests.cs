#if VIEW_CLIPPING_STATE_TESTS
using System;
using System.Collections.Generic;
using Aveva.Core3D.Graphics;
using E3d21AiBridge;

namespace Aveva.Core3D.Graphics
{
    internal sealed class View3D
    {
        internal View3DClipping Clipping { get; }
        internal string ViewId { get; }

        internal View3D(View3DClipping clipping, string viewId = "test-view")
        {
            Clipping = clipping;
            ViewId = viewId;
        }

        internal bool IsValid()
        {
            return true;
        }
    }

    internal sealed class View3DClipping
    {
        internal enum BoxFace
        {
            W = 1,
            S,
            D,
            E,
            N,
            U
        }

        private readonly bool capping;
        private readonly int capColour;
        private readonly double[] lengths;
        private readonly double[] position;
        private readonly double[] orientation;
        private readonly bool clipping;

        internal View3DClipping(bool clipping, bool capping, int capColour,
            double[] lengths, double[] position, double[] orientation)
        {
            this.clipping = clipping;
            this.capping = capping;
            this.capColour = capColour;
            this.lengths = lengths;
            this.position = position;
            this.orientation = orientation;
        }

        internal bool Clipping
        {
            get { return clipping; }
        }

        internal void GetClipBoxData(out bool capping, out int capColour,
            out double[] lengths, out double[] position, out double[] orientation)
        {
            capping = this.capping;
            capColour = this.capColour;
            lengths = this.lengths;
            position = this.position;
            orientation = this.orientation;
        }

        internal bool IsClipPlaneSetForBoxFace(BoxFace face, out bool active)
        {
            active = false;
            return false;
        }
    }
}

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        private readonly View3D testView;

        internal E3d21Context(View3D testView)
        {
            this.testView = testView;
        }

        private bool IsCommandActive()
        {
            return false;
        }

        private T OnUi<T>(Func<T> callback)
        {
            return callback();
        }

        private View3D CameraRequireView()
        {
            return testView;
        }

        private T NativeRead<T>(Func<T> read)
        {
            return read();
        }

        private void ThrowIfNativeFaulted()
        {
        }

        private View3D CurrentView3D()
        {
            return testView;
        }
    }
}

internal static class ViewClippingStateTests
{
    private static int checks;

    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) throw new Exception(message);
    }

    private static Dictionary<string, object> ReadBoxData(double[] lengths,
        double[] position, double[] orientation)
    {
        var clipping = new Aveva.Core3D.Graphics.View3DClipping(true, true, 7,
            lengths, position, orientation);
        var view = new Aveva.Core3D.Graphics.View3D(clipping);
        var result = (Dictionary<string, object>)new E3d21Context(view).GetViewClippingState();
        Check(Equals(result["view_unchanged"], true), "The fake current view must remain unchanged.");
        return (Dictionary<string, object>)result["box_data"];
    }

    private static void Main()
    {
        var valid = ReadBoxData(new[] { 1.0, 2.0, 3.0 },
            new[] { 4.0, 5.0, 6.0 }, new[] { 7.0, 8.0, 9.0 });
        Check(Equals(valid["available"], true), "A complete finite clip-box record is available.");
        Check(valid["lengths"] is double[] && valid["position"] is double[] && valid["orientation"] is double[],
            "A complete clip-box record retains all three vectors.");

        var nullVector = ReadBoxData(null, new[] { 4.0, 5.0, 6.0 }, new[] { 7.0, 8.0, 9.0 });
        Check(Equals(nullVector["available"], false), "A null clip-box vector is unavailable.");

        var wrongLength = ReadBoxData(new[] { 1.0, 2.0 }, new[] { 4.0, 5.0, 6.0 },
            new[] { 7.0, 8.0, 9.0 });
        Check(Equals(wrongLength["available"], false), "A short clip-box vector is unavailable.");

        var nonFinite = ReadBoxData(new[] { 1.0, 2.0, 3.0 }, new[] { 4.0, double.NaN, 6.0 },
            new[] { 7.0, 8.0, 9.0 });
        Check(Equals(nonFinite["available"], false), "A non-finite clip-box vector is unavailable.");

        Console.WriteLine("View clipping state checks passed: " + checks);
    }
}
#endif
