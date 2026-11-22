using System;
using System.Collections.Generic;
using Aveva.Core3D.Graphics;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    public object GetViewClippingState()
    {
        if (IsCommandActive())
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

        return OnUi(delegate
        {
            if (IsCommandActive())
                throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

            View3D view = CameraRequireView();
            View3DClipping clipping = NativeRead(() => view.Clipping);
            Dictionary<string, string> errors = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            Dictionary<string, object> result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
            {
                ["success"] = false,
                ["available"] = clipping != null,
                ["complete"] = false,
                ["status"] = clipping == null ? "unavailable" : "reading",
                ["source"] = "E3D SDK View3D.Clipping public getters",
                ["view_id"] = NativeRead(() => view.ViewId),
                ["clipping_enabled"] = null,
                ["capping"] = null,
                ["cap_colour"] = null,
                ["box_data"] = null,
                ["box_faces"] = new List<object>(),
                ["read_errors"] = errors,
                ["coordinate_frame"] = "SDK native (unverified)",
                ["units"] = "SDK native (unverified)",
                ["side_effects"] = "May initialize or warm the current view's managed clipping cache; no SDK setter, view refresh, model write, camera change or Drawlist change is requested."
            };

            if (clipping == null)
            {
                errors["clipping"] = "The current view did not provide a clipping reader.";
                return result;
            }

            try
            {
                result["clipping_enabled"] = NativeRead(() => clipping.Clipping);
            }
            catch (Exception ex)
            {
                ThrowIfNativeFaulted();
                errors["clipping_enabled"] = ex.Message;
            }

            bool capping = false;
            int capColour = 0;
            double[] lengths = null;
            double[] position = null;
            double[] orientation = null;
            bool boxDataRead = false;
            try
            {
                NativeRead(() =>
                {
                    clipping.GetClipBoxData(out capping, out capColour, out lengths, out position, out orientation);
                    return true;
                });
                boxDataRead = true;
            }
            catch (Exception ex)
            {
                ThrowIfNativeFaulted();
                errors["box_data"] = ex.Message;
            }

            if (boxDataRead)
            {
                result["capping"] = capping;
                result["cap_colour"] = capColour;
                double[] validLengths = ClippingVector(lengths, "box_data.lengths", errors);
                double[] validPosition = ClippingVector(position, "box_data.position", errors);
                double[] validOrientation = ClippingVector(orientation, "box_data.orientation", errors);
                result["box_data"] = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
                {
                    ["available"] = validLengths != null && validPosition != null && validOrientation != null,
                    ["lengths"] = validLengths,
                    ["position"] = validPosition,
                    ["orientation"] = validOrientation,
                    ["source"] = "View3DClipping.GetClipBoxData",
                    ["interpretation"] = "SDK stored clip-box data; it is not verified to describe planes defined individually."
                };
            }

            KeyValuePair<string, View3DClipping.BoxFace>[] faces =
            {
                new KeyValuePair<string, View3DClipping.BoxFace>("W", View3DClipping.BoxFace.W),
                new KeyValuePair<string, View3DClipping.BoxFace>("S", View3DClipping.BoxFace.S),
                new KeyValuePair<string, View3DClipping.BoxFace>("D", View3DClipping.BoxFace.D),
                new KeyValuePair<string, View3DClipping.BoxFace>("E", View3DClipping.BoxFace.E),
                new KeyValuePair<string, View3DClipping.BoxFace>("N", View3DClipping.BoxFace.N),
                new KeyValuePair<string, View3DClipping.BoxFace>("U", View3DClipping.BoxFace.U)
            };
            List<object> faceResults = (List<object>)result["box_faces"];
            foreach (KeyValuePair<string, View3DClipping.BoxFace> face in faces)
            {
                bool active = false;
                try
                {
                    bool defined = NativeRead(() => clipping.IsClipPlaneSetForBoxFace(face.Value, out active));
                    faceResults.Add(new Dictionary<string, object>
                    {
                        ["face"] = face.Key,
                        ["defined"] = defined,
                        ["active"] = active
                    });
                }
                catch (Exception ex)
                {
                    ThrowIfNativeFaulted();
                    errors["box_faces." + face.Key] = ex.Message;
                    faceResults.Add(new Dictionary<string, object>
                    {
                        ["face"] = face.Key,
                        ["defined"] = null,
                        ["active"] = null
                    });
                }
            }

            bool viewUnchanged = false;
            try
            {
                viewUnchanged = NativeRead(() => ReferenceEquals(CurrentView3D(), view) && view.IsValid());
            }
            catch (Exception ex)
            {
                ThrowIfNativeFaulted();
                errors["view_unchanged"] = ex.Message;
            }
            result["view_unchanged"] = viewUnchanged;
            if (!viewUnchanged)
                errors["view_unchanged"] = "The current E3D view changed or became unavailable during the read.";

            bool complete = errors.Count == 0 && viewUnchanged;
            result["success"] = complete;
            result["complete"] = complete;
            result["status"] = viewUnchanged ? (complete ? "complete" : "partial") : "context_changed";
            return result;
        });
    }

    private static double[] ClippingVector(double[] value, string field, IDictionary<string, string> errors)
    {
        if (value == null || value.Length != 3)
        {
            errors[field] = "E3D did not return exactly three values.";
            return null;
        }

        double[] result = new double[3];
        for (int index = 0; index < result.Length; index++)
        {
            if (double.IsNaN(value[index]) || double.IsInfinity(value[index]))
            {
                errors[field] = "E3D returned a non-finite value.";
                return null;
            }
            result[index] = value[index];
        }
        return result;
    }
}
