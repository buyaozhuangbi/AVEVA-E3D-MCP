using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using Aveva.Core3D.Graphics;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    public object CaptureView(int width, int height)
    {
        const int minimumDimension = 64;
        const int maximumDimension = 2048;
        const long maximumPixels = 4194304;
        if (width < minimumDimension || width > maximumDimension)
            throw new ArgumentOutOfRangeException("width", "View width must be between 64 and 2048 pixels.");
        if (height < minimumDimension || height > maximumDimension)
            throw new ArgumentOutOfRangeException("height", "View height must be between 64 and 2048 pixels.");
        if ((long)width * height > maximumPixels)
            throw new ArgumentException("The view image must not exceed 4194304 pixels.");
        if (IsCommandActive())
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

        return OnUi(delegate
        {
            if (IsCommandActive())
                throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

            View3D view = CameraRequireView();
            Dictionary<string, string> observationErrors = new Dictionary<string, string>(StringComparer.Ordinal);
            Dictionary<string, object> beforeRefresh = CaptureReadState(view, "before_refresh", observationErrors);
            bool? sameViewBefore = CaptureViewIsCurrent(view, "before_refresh_identity", observationErrors);

            // Flush pending database/Drawlist changes before reading the image.
            // No camera or membership setter is used, but pending graphics
            // notifications can change the observed state.
            DrawListManager manager = DrawListManager.Instance;
            if (manager != null) NativeWrite(() => manager.UpdateGraphics());
            bool? sameViewAtBitmap = CaptureViewIsCurrent(view, "before_bitmap_identity", observationErrors);
            if (sameViewAtBitmap == true) NativeWrite(() => InvokeViewMethod(view, "MapView"));
            Dictionary<string, object> beforeBitmap = CaptureReadState(view, "before_bitmap", observationErrors);
            sameViewAtBitmap = CaptureViewIsCurrent(view, "bitmap_identity", observationErrors);
            if (sameViewAtBitmap != true || !NativeRead(() => view.IsValid()))
            {
                observationErrors["bitmap"] = "The originally selected view is no longer current or valid; no bitmap was requested.";
                return new Dictionary<string, object>
                {
                    ["success"] = false,
                    ["error"] = observationErrors["bitmap"],
                    ["native_capture_observation"] = CaptureObservation(beforeRefresh, beforeBitmap, null,
                        observationErrors, sameViewBefore, sameViewAtBitmap, null, null, null)
                };
            }
            string bitmapStarted = DateTime.UtcNow.ToString("O");

            Bitmap captured = null;
            try
            {
                NativeRead(() => { captured = view.GetBitmap(width, height); return captured; });
            }
            catch (Exception ex)
            {
                // NativeRead can throw after GetBitmap returned, if the SDK
                // fault latch was set. Dispose the assigned bitmap in that case.
                if (captured != null) captured.Dispose();
                ThrowIfNativeFaulted();
                throw new InvalidOperationException("E3D could not capture the current 3D view: " + ex.Message, ex);
            }

            using (Bitmap bitmap = captured)
            {
                if (bitmap == null)
                    throw new InvalidOperationException("E3D could not create a bitmap of the current 3D view.");
                string bitmapFinished = DateTime.UtcNow.ToString("O");
                Dictionary<string, object> afterBitmap = CaptureReadState(view, "after_bitmap", observationErrors);
                bool? sameViewAfter = CaptureViewIsCurrent(view, "after_bitmap_identity", observationErrors);

                string imageBase64;
                try
                {
                    using (MemoryStream stream = new MemoryStream())
                    {
                        bitmap.Save(stream, ImageFormat.Png);
                        imageBase64 = Convert.ToBase64String(stream.ToArray());
                    }
                }
                catch (Exception ex)
                {
                    ThrowIfNativeFaulted();
                    throw new InvalidOperationException("The E3D view bitmap could not be encoded as PNG: " + ex.Message, ex);
                }

                Dictionary<string, object> result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
                {
                    ["success"] = true,
                    ["width"] = bitmap.Width,
                    ["height"] = bitmap.Height,
                    ["mime_type"] = "image/png",
                    ["image_base64"] = imageBase64,
                    ["source"] = "E3D View3D.GetBitmap",
                    ["view_id"] = beforeBitmap != null ? beforeBitmap["view_id"] : null,
                    ["drawlist_id"] = beforeBitmap != null ? beforeBitmap["drawlist_id"] : null,
                    ["native_capture_observation"] = CaptureObservation(beforeRefresh, beforeBitmap, afterBitmap,
                        observationErrors, sameViewBefore, sameViewAtBitmap, sameViewAfter, bitmapStarted, bitmapFinished)
                };
                return result;
            }
        });
    }

    private static Dictionary<string, object> CaptureObservation(Dictionary<string, object> beforeRefresh,
        Dictionary<string, object> beforeBitmap, Dictionary<string, object> afterBitmap,
        Dictionary<string, string> errors, bool? sameViewBefore, bool? sameViewAtBitmap, bool? sameViewAfter,
        string bitmapStarted, string bitmapFinished)
    {
        bool? viewObjectUnchanged = sameViewBefore == false || sameViewAtBitmap == false || sameViewAfter == false
            ? (bool?)false : sameViewBefore == true && sameViewAtBitmap == true && sameViewAfter == true ? (bool?)true : null;
        return new Dictionary<string, object>
        {
            ["schema"] = 1,
            ["before_refresh"] = beforeRefresh,
            ["before_bitmap"] = beforeBitmap,
            ["after_bitmap"] = afterBitmap,
            ["bitmap_started_utc"] = bitmapStarted,
            ["bitmap_finished_utc"] = bitmapFinished,
            ["view_object_unchanged"] = viewObjectUnchanged,
            ["complete"] = beforeRefresh != null && beforeBitmap != null && afterBitmap != null &&
                errors.Count == 0 && viewObjectUnchanged.HasValue,
            ["read_errors"] = errors,
            ["scope"] = "Actual SDK camera and top-level Drawlist samples around graphics refresh and GetBitmap in one UI callback. " +
                "No observation inside GetBitmap, no render-completion guarantee. Does not verify primitive overrides, geometry revisions, " +
                "clipping, selection highlights or pixel visibility; changes may include concurrent user navigation."
        };
    }

    private static Dictionary<string, object> CaptureReadState(View3D view, string phase, Dictionary<string, string> errors)
    {
        try
        {
            CameraDrawListSnapshot drawList = CameraReadDrawList(view);
            Dictionary<string, object> state = CameraReadState(view, drawList);
            if (!Equals(state["partial"], false))
                errors[phase + ".partial"] = "Native view state includes partial read errors; inspect the retained state.errors.";
            return state;
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            // Observation failures do not discard an otherwise valid bitmap.
            errors[phase] = ex.Message;
            return null;
        }
    }

    private static bool? CaptureViewIsCurrent(View3D view, string phase, Dictionary<string, string> errors)
    {
        try
        {
            object current = CurrentView3D();
            ThrowIfNativeFaulted();
            if (current == null)
            {
                errors[phase] = "Current view identity is unavailable.";
                return null;
            }
            return ReferenceEquals(current, view);
        }
        catch (Exception ex) { ThrowIfNativeFaulted(); errors[phase] = ex.Message; return null; }
    }
}
