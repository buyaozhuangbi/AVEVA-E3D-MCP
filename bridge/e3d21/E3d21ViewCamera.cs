using System;
using System.Collections.Generic;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using Aveva.Core.Database;
using Aveva.Core.Geometry;
using Aveva.Core3D.Graphics;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    public object GetViewState()
    {
        if (IsCommandActive())
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        return OnUi(delegate
        {
            if (IsCommandActive())
                throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
            View3D view = CameraRequireView();
            CameraDrawListSnapshot drawList = CameraReadDrawList(view);
            return CameraReadState(view, drawList);
        });
    }

    public object SetView(string preset, string fit, string targetRefno, double padding, bool dryRun)
    {
        string requestedPreset = (preset ?? "keep").Trim().ToLowerInvariant();
        string requestedFit = (fit ?? "none").Trim().ToLowerInvariant();
        string target = (targetRefno ?? "").Trim();
        double[] presetDirection = CameraPresetDirection(requestedPreset);
        if (requestedFit != "none" && requestedFit != "drawlist" && requestedFit != "target")
            throw new ArgumentException("fit must be none, drawlist or target.", "fit");
        if (requestedPreset == "keep" && requestedFit == "none")
            throw new ArgumentException("keep with fit=none does not request a camera change.");
        if (requestedFit == "target" ? target.Length == 0 : target.Length != 0)
            throw new ArgumentException("target_refno is required only when fit=target.", "targetRefno");
        if (!CameraFinite(padding) || padding < 1.0 || padding > 4.0)
            throw new ArgumentOutOfRangeException("padding", "padding must be finite and between 1 and 4.");
        bool ownsCommand = false;
        if (dryRun)
        {
            if (IsCommandActive())
                throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        }
        else
        {
            ownsCommand = TryBeginCommand("view-set " + requestedPreset + " " + requestedFit);
            if (!ownsCommand)
                throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        }

        try
        {
            return OnUi(delegate
            {
                if (dryRun && IsCommandActive())
                    throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
                View3D view = CameraRequireView();
                CameraDrawListSnapshot membersBefore = CameraReadDrawList(view);
                Dictionary<string, object> before = CameraReadState(view, membersBefore);
                Dictionary<string, object> result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
                {
                    ["success"] = false,
                    ["dry_run"] = dryRun,
                    ["write_attempted"] = false,
                    ["write_applied"] = false,
                    ["rollback_attempted"] = false,
                    ["rollback_succeeded"] = null,
                    ["preset"] = requestedPreset,
                    ["fit"] = requestedFit,
                    ["target_refno"] = target,
                    ["padding"] = padding,
                    ["before"] = before,
                    ["after"] = null,
                    ["ce_unchanged"] = null,
                    ["membership_unchanged"] = null,
                    ["visuals_unchanged"] = null,
                    ["view_unchanged"] = null,
                    ["source"] = "E3D SDK View3D"
                };
                CameraInitializeOperationReadback(result);
                Dictionary<string, object> plan = null;
                try
                {
                    double[] centre = (double[])before["centre"];
                    double[] direction = presetDirection ?? (double[])before["direction"];
                    double radius = (double)before["radius"];
                    double[] limits = null;
                    string limitsSource = "unchanged";
                    if (requestedFit == "drawlist")
                    {
                        if (membersBefore.Members.Count == 0)
                            throw new InvalidOperationException("The current view Drawlist is empty; no geometry can be fitted.");
                        limits = CameraRequireLimits(NativeRead(() => view.GetDrawlistLimits()));
                        limitsSource = "E3D SDK View3D.GetDrawlistLimits";
                    }
                    else if (requestedFit == "target")
                    {
                        DbElement element = ResolveElement(target);
                        if (!IsUsable(element))
                            throw new InvalidOperationException("Element was not found: " + target);
                        result["target_refno"] = ElementRef(element);
                        CameraDescribeTarget(result, element, membersBefore);
                        Spatial spatial = Spatial.Instance;
                        if (spatial == null)
                            throw new InvalidOperationException("The E3D spatial query service is unavailable.");
                        LimitsBox bounds;
                        if (!spatial.LimitsBox(element, out bounds) || bounds == null || bounds.Minimum == null || bounds.Maximum == null)
                            throw new InvalidOperationException("The E3D SDK did not return generated geometry bounds for the target.");
                        limits = CameraRequireLimits(new[]
                        {
                            bounds.Minimum.X, bounds.Maximum.X,
                            bounds.Minimum.Y, bounds.Maximum.Y,
                            bounds.Minimum.Z, bounds.Maximum.Z
                        });
                        result["target_geometry_available"] = true;
                        limitsSource = "E3D SDK Spatial.LimitsBox";
                        result["target_coordinate_frame"] = "SDK native (unverified)";
                    }
                    if (limits != null)
                    {
                        centre = new[]
                        {
                            limits[0] / 2.0 + limits[1] / 2.0,
                            limits[2] / 2.0 + limits[3] / 2.0,
                            limits[4] / 2.0 + limits[5] / 2.0
                        };
                        // SDK radius fits a world distance into half the smaller canvas dimension.
                        // A bounding sphere contains the box at every preset and view aspect ratio.
                        double dx = limits[1] - limits[0];
                        double dy = limits[3] - limits[2];
                        double dz = limits[5] - limits[4];
                        radius = Math.Sqrt(dx * dx + dy * dy + dz * dz) * 0.5 * padding;
                        if (!CameraFinite(radius) || radius <= 0.0)
                            throw new InvalidOperationException("The fit bounds do not provide a finite positive camera radius.");
                    }
                    bool perspective = requestedPreset == "keep" && (bool)before["perspective"];
                    double twist = requestedPreset == "keep" ? (double)before["extra_twist_degrees"] : 0.0;
                    plan = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
                    {
                        ["centre"] = centre,
                        ["direction"] = direction,
                        ["radius"] = radius,
                        ["perspective"] = perspective,
                        ["extra_twist_degrees"] = twist,
                        ["shaded"] = before["shaded"],
                        ["fit_limits"] = limits,
                        ["fit_source"] = limitsSource,
                        ["coordinate_frame"] = "SDK native",
                        ["direction_convention"] = "native View3D looking direction (x, y, z)"
                    };
                    result["plan"] = plan;
                    result["requested_camera"] = plan;
                    if (!dryRun)
                    {
                        result["write_attempted"] = true;
                        result["write_applied"] = null;
                        bool accepted = requestedPreset == "keep"
                            ? NativeRead(() => view.SetThruPoint(centre))
                            : NativeRead(() => view.SetCentreAndDirNoMapUpdate(centre, direction));
                        if (!accepted)
                            throw new InvalidOperationException("E3D rejected the requested camera centre or direction.");
                        result["write_applied"] = true;
                        if (requestedFit != "none") NativeWrite(() => view.SetRadius(radius));
                        if (requestedPreset != "keep")
                        {
                            NativeWrite(() => view.SetPerspectiveModeNoMapUpdate(false));
                            NativeWrite(() => view.SetExtraTwistDegrees(0.0));
                        }
                        NativeWrite(() => view.MapView());
                        result["camera_write_completed"] = true;
                    }
                }
                catch (Exception ex)
                {
                    ThrowIfNativeFaulted();
                    result["error"] = ex.Message;
                }
                // Observe once after the last attempted setter. Never replay or restore a
                // camera after an inconsistency: that could overwrite later user navigation.
                CameraCompleteOperation(result, view, before, membersBefore, plan, dryRun, true);
                return result;
            });
        }
        finally { if (ownsCommand) EndCommand(); }
    }

    private static void CameraInitializeOperationReadback(Dictionary<string, object> result)
    {
        result["requested_camera"] = null;
        result["actual_camera"] = null;
        result["camera_readback_succeeded"] = false;
        result["camera_readback_error"] = null;
        result["camera_matches_request"] = null;
        result["camera_matches_before"] = null;
        result["camera_write_completed"] = false;
        result["camera_observation_scope"] = "single_ui_callback";
        result["camera_observation_note"] = "This readback does not establish stability across later UI callbacks or prevent subsequent user navigation.";
    }

    private static void CameraCompleteOperation(Dictionary<string, object> result, View3D view,
        Dictionary<string, object> before, CameraDrawListSnapshot membersBefore,
        Dictionary<string, object> requested, bool dryRun, bool requireUnchangedDrawList)
    {
        try
        {
            CameraDrawListSnapshot membersAfter = CameraReadDrawList(view);
            Dictionary<string, object> after = CameraReadState(view, membersAfter);
            // Native getters can invoke callbacks. Recheck identity after the final getter
            // so values from a no-longer-current view are not labelled a current readback.
            if (!ReferenceEquals(CurrentView3D(), view) || CurrentDrawList() == null ||
                NativeRead(() => view.GetAttachedDrawList()) != membersAfter.Id ||
                CurrentDrawList().ID != membersAfter.Id)
                throw new InvalidOperationException("The active view or attached Drawlist changed during native camera readback.");
            result["after"] = after;
            result["actual_camera"] = after;
            result["camera_readback_succeeded"] = true;
            result["camera_matches_before"] = CameraMatches(after, before);
            if (!dryRun && requested != null)
                result["camera_matches_request"] = CameraMatches(after, requested);
            CameraCheckInvariants(result, before, after, membersBefore, membersAfter);
        }
        catch (Exception readError)
        {
            ThrowIfNativeFaulted();
            result["after"] = null;
            result["actual_camera"] = null;
            result["camera_readback_succeeded"] = false;
            result["camera_readback_error"] = readError.Message;
            result["after_error"] = readError.Message;
            result["camera_matches_before"] = null;
            result["camera_matches_request"] = null;
            result["ce_unchanged"] = null;
            result["membership_unchanged"] = null;
            result["visuals_unchanged"] = null;
            result["view_unchanged"] = null;
        }

        if (!result.ContainsKey("error"))
        {
            if (!(bool)result["camera_readback_succeeded"])
                result["error"] = "The native camera readback could not be verified.";
            else if (!Equals(result[dryRun ? "camera_matches_before" : "camera_matches_request"], true))
                result["error"] = dryRun
                    ? "Observed camera inconsistency while preparing the view preview."
                    : "Observed camera inconsistency: the native readback did not match the requested view.";
            else if (!Equals(result["ce_unchanged"], true) || !Equals(result["view_unchanged"], true) ||
                (requireUnchangedDrawList && (!Equals(result["membership_unchanged"], true) || !Equals(result["visuals_unchanged"], true))))
                result["error"] = "Observed current element, view or Drawlist inconsistency during the camera operation.";
            else
                result["success"] = true;
        }
        if ((bool)result["write_attempted"] && !Equals(result["success"], true))
        {
            result["retry_safe"] = false;
            result["outcome_uncertain"] = !(bool)result["camera_readback_succeeded"] || !(bool)result["camera_write_completed"];
            result["rollback_reason"] = "Camera writes are not replayed or restored after failure because that could overwrite later user navigation.";
        }
    }

    private static double[] CameraPresetDirection(string preset)
    {
        switch (preset)
        {
            case "keep": return null;
            case "x_positive": return new[] { 1.0, 0.0, 0.0 };
            case "x_negative": return new[] { -1.0, 0.0, 0.0 };
            case "y_positive": return new[] { 0.0, 1.0, 0.0 };
            case "y_negative": return new[] { 0.0, -1.0, 0.0 };
            case "z_positive": return new[] { 0.0, 0.0, 1.0 };
            case "z_negative": return new[] { 0.0, 0.0, -1.0 };
            case "isometric":
                double unit = 1.0 / Math.Sqrt(3.0);
                return new[] { unit, unit, -unit };
            default: throw new ArgumentException("Unknown camera preset: " + preset, "preset");
        }
    }

    private static View3D CameraRequireView()
    {
        ThrowIfNativeFaulted();
        View3D view = CurrentView3D() as View3D;
        if (view == null || !view.IsValid())
            throw new InvalidOperationException("No valid current E3D 3D view is available.");
        return view;
    }

    private static Dictionary<string, object> CameraReadState(View3D view, CameraDrawListSnapshot drawList)
    {
        ThrowIfNativeFaulted();
        double[] centre = CameraRequireVector(NativeRead(() => view.GetThruPoint()), "centre");
        double[] direction = CameraRequireVector(NativeRead(() => view.GetDirection()), "direction");
        double radius = NativeRead(() => view.GetRadius());
        double bearing = NativeRead(() => view.GetBearing());
        double elevation = NativeRead(() => view.GetElevation());
        double twist = NativeRead(() => view.GetExtraTwistDegrees());
        if (!CameraFinite(radius) || radius <= 0 || !CameraFinite(bearing) || !CameraFinite(elevation) || !CameraFinite(twist))
            throw new InvalidOperationException("E3D returned an invalid native camera scalar.");
        CameraNormalized(direction);
        Dictionary<string, string> errors = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        Dictionary<string, object> state = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
        {
            ["success"] = true,
            ["available"] = true,
            ["partial"] = false,
            ["view_id"] = NativeRead(() => view.ViewId),
            ["drawlist_id"] = drawList.Id,
            ["centre"] = centre,
            ["direction"] = direction,
            ["radius"] = radius,
            ["bearing_degrees"] = bearing,
            ["elevation_degrees"] = elevation,
            ["extra_twist_degrees"] = twist,
            ["perspective"] = NativeRead(() => view.PerspectiveMode),
            ["shaded"] = NativeRead(() => view.ShadeMode),
            ["width"] = NativeRead(() => view.GetWindowWidth()),
            ["height"] = NativeRead(() => view.GetWindowHeight()),
            ["current_element_refno"] = ElementRef(CurrentElement()),
            ["drawlist_member_count"] = drawList.Members.Count,
            ["drawlist_membership_fingerprint"] = drawList.MembershipFingerprint,
            ["drawlist_visuals_fingerprint"] = drawList.VisualsFingerprint,
            ["coordinate_frame"] = "SDK native",
            ["direction_convention"] = "native View3D looking direction (x, y, z)",
            ["source"] = "E3D SDK View3D getters",
            ["errors"] = errors
        };
        try { state["drawlist_limits"] = CameraRequireLimits(NativeRead(() => view.GetDrawlistLimits())); }
        catch (Exception ex) { ThrowIfNativeFaulted(); errors["drawlist_limits"] = ex.Message; }
        try { state["view_limits"] = CameraRequireLimits(NativeRead(() => view.GetLimits(LimitsType.VIEW))); }
        catch (Exception ex) { ThrowIfNativeFaulted(); errors["view_limits"] = ex.Message; }
        state["partial"] = errors.Count != 0;
        return state;
    }

    private sealed class CameraMember
    {
        public string Refno;
        public bool Visible;
    }

    private sealed class CameraDrawListSnapshot
    {
        public int Id;
        public readonly Dictionary<string, CameraMember> Members = new Dictionary<string, CameraMember>(StringComparer.Ordinal);
        public string MembershipFingerprint;
        public string VisualsFingerprint;
    }

    private static CameraDrawListSnapshot CameraReadDrawList(View3D view)
    {
        ThrowIfNativeFaulted();
        if (!ReferenceEquals(CurrentView3D(), view))
            throw new InvalidOperationException("The active 3D view changed during the camera operation.");
        DrawList drawList = CurrentDrawList();
        if (drawList == null || view.GetAttachedDrawList() != drawList.ID)
            throw new InvalidOperationException("The current Drawlist is not the Drawlist attached to the current 3D view.");
        CameraDrawListSnapshot snapshot = new CameraDrawListSnapshot { Id = drawList.ID };
        List<string> memberRecords = new List<string>();
        List<string> visualRecords = new List<string>();
        foreach (DrawListMember member in NativeRead(() => drawList.Members()))
        {
            ThrowIfNativeFaulted();
            if (member == null || !IsUsable(member.DbElement))
                throw new InvalidOperationException("The current Drawlist contains an unreadable member.");
            string refno = ElementRef(member.DbElement);
            bool visible = false;
            int colour = 0;
            int transparency = 0;
            bool edges = false;
            ThrowIfNativeFaulted();
            member.GetVisualProperties(ref visible, ref colour, ref transparency, ref edges);
            ThrowIfNativeFaulted();
            bool shallow = NativeRead(() => member.ShallowRendering);
            if (snapshot.Members.ContainsKey(refno))
                throw new InvalidOperationException("The current Drawlist contains duplicate member references.");
            snapshot.Members.Add(refno, new CameraMember { Refno = refno, Visible = visible });
            memberRecords.Add(refno);
            visualRecords.Add(refno + "|" + visible + "|" + colour.ToString(CultureInfo.InvariantCulture) + "|" +
                transparency.ToString(CultureInfo.InvariantCulture) + "|" + edges + "|" + shallow);
        }
        memberRecords.Sort(StringComparer.Ordinal);
        visualRecords.Sort(StringComparer.Ordinal);
        snapshot.MembershipFingerprint = CameraFingerprint(memberRecords);
        snapshot.VisualsFingerprint = CameraFingerprint(visualRecords);
        return snapshot;
    }

    private static string CameraFingerprint(List<string> records)
    {
        using (SHA256 hash = SHA256.Create())
        {
            byte[] bytes = hash.ComputeHash(Encoding.UTF8.GetBytes(string.Join("\n", records)));
            return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();
        }
    }

    private static void CameraDescribeTarget(Dictionary<string, object> result, DbElement target, CameraDrawListSnapshot drawList)
    {
        ThrowIfNativeFaulted();
        result["target_geometry_available"] = false;
        result["target_in_drawlist"] = false;
        result["covering_drawlist_refno"] = null;
        result["target_display_enabled"] = null;
        result["target_pixel_visible"] = null;
        result["target_visibility_note"] = "Drawlist coverage does not establish pixel visibility; clipping and occlusion are not evaluated.";
        string targetRef = ElementRef(target);
        CameraMember direct;
        if (drawList.Members.TryGetValue(targetRef, out direct))
        {
            result["target_in_drawlist"] = true;
            result["covering_drawlist_refno"] = direct.Refno;
            result["target_display_enabled"] = direct.Visible;
            return;
        }
        HashSet<string> visited = new HashSet<string>(StringComparer.Ordinal);
        DbElement candidate = target;
        for (int depth = 0; depth < 256; depth++)
        {
            ThrowIfNativeFaulted();
            if (candidate.GetElementType().IsWorld) break;
            candidate = NativeRead(() => candidate.Owner);
            if (!IsUsable(candidate)) break;
            string candidateRef = ElementRef(candidate);
            if (!visited.Add(candidateRef)) break;
            CameraMember ancestor;
            // SDK MAINSHALLOW means on-top/transient rendering, not a hierarchy-depth limit.
            // A DrawListMember includes its descendant primitives in either rendering mode.
            if (!drawList.Members.TryGetValue(candidateRef, out ancestor)) continue;
            result["target_in_drawlist"] = true;
            result["covering_drawlist_refno"] = ancestor.Refno;
            // Ancestor coverage alone cannot resolve all descendant display overrides.
            result["target_display_enabled"] = null;
            return;
        }
    }

    private static double[] CameraRequireVector(double[] vector, string name)
    {
        if (vector == null || vector.Length != 3)
            throw new InvalidOperationException("E3D returned an invalid native camera " + name + ".");
        foreach (double value in vector)
            if (!CameraFinite(value)) throw new InvalidOperationException("E3D returned a non-finite native camera " + name + ".");
        return (double[])vector.Clone();
    }

    private static double[] CameraRequireLimits(double[] limits)
    {
        if (limits == null || limits.Length != 6)
            throw new InvalidOperationException("E3D did not return six native geometry limits.");
        foreach (double value in limits)
            if (!CameraFinite(value)) throw new InvalidOperationException("E3D returned non-finite native geometry limits.");
        for (int axis = 0; axis < 3; axis++)
            if (limits[2 * axis + 1] < limits[2 * axis])
                throw new InvalidOperationException("E3D returned inverted native geometry limits.");
        return (double[])limits.Clone();
    }

    private static double[] CameraNormalized(double[] direction)
    {
        double norm = Math.Sqrt(direction[0] * direction[0] + direction[1] * direction[1] + direction[2] * direction[2]);
        if (!CameraFinite(norm) || norm <= 0)
            throw new InvalidOperationException("E3D returned an invalid native camera direction.");
        return new[] { direction[0] / norm, direction[1] / norm, direction[2] / norm };
    }

    private static bool CameraFinite(double value) { return !double.IsNaN(value) && !double.IsInfinity(value); }

    private static bool CameraNumberMatches(double actual, double expected)
    {
        return Math.Abs(actual - expected) <= Math.Max(1e-5, Math.Abs(expected) * 1e-6);
    }

    private static bool CameraVectorMatches(double[] actual, double[] expected)
    {
        for (int index = 0; index < 3; index++)
            if (!CameraNumberMatches(actual[index], expected[index])) return false;
        return true;
    }

    private static bool CameraMatches(Dictionary<string, object> actual, Dictionary<string, object> expected)
    {
        return CameraVectorMatches((double[])actual["centre"], (double[])expected["centre"]) &&
            CameraVectorMatches(CameraNormalized((double[])actual["direction"]), CameraNormalized((double[])expected["direction"])) &&
            CameraNumberMatches((double)actual["radius"], (double)expected["radius"]) &&
            (bool)actual["perspective"] == (bool)expected["perspective"] &&
            CameraNumberMatches((double)actual["extra_twist_degrees"], (double)expected["extra_twist_degrees"]) &&
            (!expected.ContainsKey("shaded") || Equals(actual["shaded"], expected["shaded"]));
    }

    private static void CameraCheckInvariants(Dictionary<string, object> result, Dictionary<string, object> before,
        Dictionary<string, object> after, CameraDrawListSnapshot membersBefore, CameraDrawListSnapshot membersAfter)
    {
        result["ce_unchanged"] = Equals(before["current_element_refno"], after["current_element_refno"]);
        result["membership_unchanged"] = membersBefore.Id == membersAfter.Id &&
            membersBefore.MembershipFingerprint == membersAfter.MembershipFingerprint;
        result["visuals_unchanged"] = membersBefore.Id == membersAfter.Id &&
            membersBefore.VisualsFingerprint == membersAfter.VisualsFingerprint;
        result["view_unchanged"] = Equals(before["view_id"], after["view_id"]) &&
            Equals(before["drawlist_id"], after["drawlist_id"]);
    }
}
