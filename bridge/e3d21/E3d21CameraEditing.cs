using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Aveva.Core3D.Graphics;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private readonly string cameraEditingRevisionSession = Guid.NewGuid().ToString("N");

    private sealed class CameraEditingState
    {
        internal SelectionEditingState Scene;
        internal Dictionary<string, object> Camera;
    }

    public object SetCamera(double[] centre, double[] direction, double radius, bool perspective,
        double extraTwistDegrees, string expectedRevision, bool dryRun)
    {
        double[] requestedCentre = CameraEditingInputVector(centre, "centre", true);
        double[] requestedDirection = CameraEditingInputVector(direction, "direction", false);
        double[] normalizedDirection = CameraEditingNormalized(requestedDirection);
        if (!CameraFinite(radius) || radius <= 0)
            throw new ArgumentOutOfRangeException("radius", "radius must be finite and greater than zero.");
        if (!CameraFinite(extraTwistDegrees))
            throw new ArgumentOutOfRangeException("extraTwistDegrees", "extra_twist_degrees must be finite.");
        string expected = expectedRevision == null ? null : expectedRevision.ToLowerInvariant();
        if (expected != null && (expected.Length != 64 || expected.Any(c => !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f'))))
            throw new ArgumentException("expected_revision must contain exactly 64 hexadecimal characters.", "expectedRevision");
        if (!dryRun && expected == null)
            throw new ArgumentException("Execution requires expected_revision from a current camera preview with the same five input fields.", "expectedRevision");
        var request = new Dictionary<string, object>
        {
            ["centre"] = requestedCentre, ["direction"] = requestedDirection, ["radius"] = radius,
            ["perspective"] = perspective, ["extra_twist_degrees"] = extraTwistDegrees
        };
        var errors = new List<object>();
        var setterAttempts = new List<string>();
        var result = new Dictionary<string, object>
        {
            ["success"] = false, ["dry_run"] = dryRun, ["request"] = request,
            ["revision"] = null, ["revision_conflict"] = false, ["no_change"] = null,
            ["write_attempted"] = false, ["write_applied"] = false, ["setter_attempts"] = setterAttempts,
            ["map_attempted"] = false, ["map_completed"] = false, ["camera_write_completed"] = false,
            ["readback_verified"] = false, ["camera_readback_succeeded"] = false,
            ["camera_matches_request"] = null, ["camera_matches_before"] = null,
            ["context_unchanged"] = null, ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
            ["selection_unchanged"] = null, ["members_unchanged"] = null, ["primitives_unchanged"] = null,
            ["shaded_unchanged"] = null, ["dimensions_unchanged"] = null,
            ["before"] = null, ["after"] = null, ["requested_camera"] = null, ["actual_camera"] = null,
            ["residual_complete"] = null, ["partial_write"] = false, ["outcome_uncertain"] = false,
            ["retry_safe"] = false, ["rollback_attempted"] = false, ["native_database_fault"] = null, ["errors"] = errors,
            ["coordinate_frame"] = "SDK native", ["direction_convention"] = "native View3D looking direction (x, y, z)",
            ["scope"] = "Set only centre, normalized looking direction, radius, perspective and extra twist in the SDK native camera frame. Shading, dimensions, selection, CE and complete observed display must remain unchanged. This is not a complete optics or bit-exact camera restoration.",
            ["revision_scope"] = "Bridge context instance, original five input fields, normalized requested direction, project/MDB/view/Drawlist/CE, complete member/primitive maps, ordered selection of at most 200 elements and current camera including shading and viewport dimensions.",
            ["camera_observation_scope"] = "single_ui_callback",
            ["camera_observation_note"] = "Readback uses the existing camera tolerance and does not prevent later user navigation. Hidden camera optics and clipping are outside this interface.",
            ["failure_policy"] = "No automatic rollback, retry, Add, fit, CE change or display persistence. Ordinary write failures retain a readable residual observation when possible; native faults stop all further SDK access."
        };
        bool acquired = false;
        string phase = "preflight";
        CameraEditingState before = null;
        Dictionary<string, object> plan = null;
        try
        {
            acquired = TryBeginCommand("camera-set");
            if (!acquired) throw new InvalidOperationException("E3D is busy; no camera write was dispatched.");
            OnUi(delegate
            {
                try
                {
                    before = CameraEditingReadState();
                    plan = CameraEditingPlan(before, requestedCentre, normalizedDirection, radius, perspective, extraTwistDegrees);
                    string revision = CameraEditingRevision(before, request, plan);
                    result["before"] = CameraEditingObservation(before);
                    result["ce_before"] = before.Scene.Ce;
                    result["requested_camera"] = plan;
                    result["revision"] = revision;
                    result["no_change"] = CameraMatches(before.Camera, plan);
                    if (expected != null && expected != revision)
                    {
                        result["revision_conflict"] = true;
                        throw new InvalidOperationException("expected_revision does not match this camera request and complete observed context. Preview again; no write was attempted.");
                    }
                    if (dryRun)
                    {
                        result["success"] = true;
                        result["ce_after"] = before.Scene.Ce;
                        result["ce_unchanged"] = true;
                        return true;
                    }
                    phase = "revalidation";
                    CameraEditingState immediate = CameraEditingReadState();
                    if (!SelectionEditingSameContext(before.Scene, immediate.Scene) ||
                        CameraEditingRevision(immediate, request, plan) != revision)
                    {
                        result["revision_conflict"] = true;
                        throw new InvalidOperationException("Camera, viewport, display, selection or context changed during preflight. No write was attempted.");
                    }
                    before = immediate;
                    if (!Equals(result["no_change"], true))
                    {
                        // The no-map setters defer the single explicit MapView until
                        // all five requested fields have been supplied. Apply radius
                        // and extra twist after orientation/mode changes, then verify.
                        phase = "centre_direction";
                        CameraEditingRequireContext(before);
                        double[] nativeCentre = (double[])requestedCentre.Clone();
                        double[] nativeDirection = (double[])normalizedDirection.Clone();
                        result["write_attempted"] = true;
                        result["write_applied"] = null;
                        setterAttempts.Add(phase);
                        if (!NativeRead(() => before.Scene.View.SetCentreAndDirNoMapUpdate(nativeCentre, nativeDirection)))
                            throw new InvalidOperationException("The native camera centre/direction setter rejected the request.");
                        phase = "perspective";
                        CameraEditingRequireContext(before);
                        setterAttempts.Add(phase);
                        NativeWrite(() => before.Scene.View.SetPerspectiveModeNoMapUpdate(perspective));
                        phase = "radius";
                        CameraEditingRequireContext(before);
                        setterAttempts.Add(phase);
                        NativeWrite(() => before.Scene.View.SetRadius(radius));
                        phase = "extra_twist_degrees";
                        CameraEditingRequireContext(before);
                        setterAttempts.Add(phase);
                        NativeWrite(() => before.Scene.View.SetExtraTwistDegrees(extraTwistDegrees));
                        phase = "map";
                        CameraEditingRequireContext(before);
                        result["map_attempted"] = true;
                        NativeWrite(() => before.Scene.View.MapView());
                        result["map_completed"] = true;
                        result["camera_write_completed"] = true;
                    }
                    phase = "readback";
                    CameraEditingState after = CameraEditingReadState();
                    bool verified = CameraEditingVerify(before, after, plan, result);
                    result["readback_verified"] = verified;
                    result["success"] = verified;
                    if (!verified)
                        errors.Add(new { phase, error = "Complete readback did not confirm the requested five camera fields with unchanged context, CE, display, selection, shading and viewport dimensions." });
                }
                catch (Exception error)
                {
                    ThrowIfNativeFaulted();
                    errors.Add(new { phase, error = error.Message });
                    if (before != null && Equals(result["write_attempted"], true))
                    {
                        try
                        {
                            CameraEditingState residual = CameraEditingReadState();
                            CameraEditingVerify(before, residual, plan, result);
                        }
                        catch (Exception residualError)
                        {
                            ThrowIfNativeFaulted();
                            CameraEditingUnknown(result);
                            errors.Add(new { phase = "residual", error = residualError.Message });
                        }
                    }
                }
                return true;
            });
        }
        catch (Exception error)
        {
            // Managed bookkeeping only: in particular, a native fault must not
            // lead to a residual getter, MapView, rollback or a CE observation.
            CameraEditingUnknown(result);
            if (nativeFaultState != null && nativeFaultState.HasFault)
                result["native_database_fault"] = nativeFaultState.Snapshot();
            errors.Add(new { phase, error = error.Message });
        }
        finally { if (acquired) EndCommand(); }
        return result;
    }

    private static double[] CameraEditingInputVector(double[] values, string name, bool centre)
    {
        if (values == null || values.Length != 3 || values.Any(value => !CameraFinite(value)))
            throw new ArgumentException(name + " must contain exactly three finite numbers.", name);
        if (centre && values.Any(value => Math.Abs(value) > float.MaxValue))
            throw new ArgumentOutOfRangeException(name, "centre must remain within the finite native float readback range.");
        return (double[])values.Clone();
    }

    private static double[] CameraEditingNormalized(double[] direction)
    {
        double scale = direction.Max(value => Math.Abs(value));
        if (!CameraFinite(scale) || scale <= 0)
            throw new ArgumentException("direction must be a nonzero finite vector.", "direction");
        double x = direction[0] / scale, y = direction[1] / scale, z = direction[2] / scale;
        double norm = Math.Sqrt(x * x + y * y + z * z);
        return new[] { x / norm, y / norm, z / norm };
    }

    private static Dictionary<string, object> CameraEditingReadCamera(SelectionEditingState scene)
    {
        View3D view = scene.View;
        var camera = new Dictionary<string, object>
        {
            ["success"] = true, ["available"] = true, ["partial"] = false,
            ["view_id"] = scene.ViewId, ["drawlist_id"] = scene.DrawListId,
            ["centre"] = CameraRequireVector(NativeRead(() => view.GetThruPoint()), "centre"),
            ["direction"] = CameraRequireVector(NativeRead(() => view.GetDirection()), "direction"),
            ["radius"] = NativeRead(() => view.GetRadius()),
            ["extra_twist_degrees"] = NativeRead(() => view.GetExtraTwistDegrees()),
            ["perspective"] = NativeRead(() => view.PerspectiveMode), ["shaded"] = NativeRead(() => view.ShadeMode),
            ["width"] = NativeRead(() => view.GetWindowWidth()), ["height"] = NativeRead(() => view.GetWindowHeight()),
            ["current_element_refno"] = scene.Ce,
            ["coordinate_frame"] = "SDK native", ["direction_convention"] = "native View3D looking direction (x, y, z)"
        };
        CameraEditingNormalized((double[])camera["direction"]);
        // SDK getters return a float direction; its squared norm remains safe
        // for the existing CameraMatches helper, unlike arbitrary input doubles.
        if (!CameraFinite((double)camera["radius"]) || (double)camera["radius"] <= 0 ||
            !CameraFinite((double)camera["extra_twist_degrees"]) || (int)camera["width"] <= 0 || (int)camera["height"] <= 0)
            throw new InvalidOperationException("Complete finite camera values and positive viewport dimensions are required.");
        return camera;
    }

    private static CameraEditingState CameraEditingReadState()
    {
        SelectionEditingState scene = SelectionEditingReadState();
        Dictionary<string, object> camera = CameraEditingReadCamera(scene);
        if (NativeRead(() => scene.View.GetAttachedDrawList()) != scene.DrawListId)
            throw new InvalidOperationException("The camera view's attached Drawlist differs from the complete display observation.");
        SelectionEditingState after = SelectionEditingReadContext();
        if (!SelectionEditingSameContext(scene, after) || scene.Ce != after.Ce)
            throw new InvalidOperationException("View, Drawlist, project/MDB or CE changed during the camera observation.");
        return new CameraEditingState { Scene = scene, Camera = camera };
    }

    private static void CameraEditingRequireContext(CameraEditingState before)
    {
        SelectionEditingState now = SelectionEditingReadContext();
        if (!SelectionEditingSameContext(before.Scene, now) || before.Scene.Ce != now.Ce ||
            NativeRead(() => now.View.GetAttachedDrawList()) != before.Scene.DrawListId ||
            NativeRead(() => now.View.GetWindowWidth()) != (int)before.Camera["width"] ||
            NativeRead(() => now.View.GetWindowHeight()) != (int)before.Camera["height"])
            throw new InvalidOperationException("Camera context or viewport dimensions changed; no further camera action was attempted.");
    }

    private static Dictionary<string, object> CameraEditingPlan(CameraEditingState state, double[] centre,
        double[] direction, double radius, bool perspective, double twist)
    {
        return new Dictionary<string, object>
        {
            ["success"] = true, ["available"] = true, ["view_id"] = state.Scene.ViewId, ["drawlist_id"] = state.Scene.DrawListId,
            ["centre"] = (double[])centre.Clone(), ["direction"] = (double[])direction.Clone(), ["radius"] = radius,
            ["perspective"] = perspective, ["extra_twist_degrees"] = twist, ["shaded"] = state.Camera["shaded"],
            ["width"] = state.Camera["width"], ["height"] = state.Camera["height"],
            ["coordinate_frame"] = "SDK native", ["direction_convention"] = "native View3D looking direction (x, y, z)"
        };
    }

    private static void CameraEditingWriteFields(BinaryWriter writer, Dictionary<string, object> values)
    {
        foreach (double value in (double[])values["centre"]) writer.Write(value);
        foreach (double value in (double[])values["direction"]) writer.Write(value);
        writer.Write((double)values["radius"]); writer.Write((bool)values["perspective"]);
        writer.Write((double)values["extra_twist_degrees"]);
    }

    private string CameraEditingRevision(CameraEditingState state, Dictionary<string, object> request, Dictionary<string, object> plan)
    {
        return PrimitiveHash(writer =>
        {
            writer.Write("native-five-field-camera-v1"); writer.Write(cameraEditingRevisionSession);
            CameraEditingWriteFields(writer, request); CameraEditingWriteFields(writer, plan);
            writer.Write(state.Scene.Project); writer.Write(state.Scene.Mdb); writer.Write(state.Scene.ViewId);
            writer.Write(state.Scene.DrawListId); writer.Write(state.Scene.Ce);
            writer.Write(state.Scene.References.Length); foreach (string reference in state.Scene.References) writer.Write(reference);
            CameraEditingWriteFields(writer, state.Camera); writer.Write((bool)state.Camera["shaded"]);
            writer.Write((int)state.Camera["width"]); writer.Write((int)state.Camera["height"]);
            PrimitiveVisualWriteMap(writer, state.Scene.Display);
        });
    }

    private static object CameraEditingObservation(CameraEditingState state)
    {
        return new
        {
            complete = true, project = state.Scene.Project, mdb = state.Scene.Mdb, view_id = state.Scene.ViewId,
            drawlist_id = state.Scene.DrawListId, current_element = state.Scene.Ce, selection_refnos = state.Scene.References,
            camera = state.Camera,
            members = state.Scene.Display.Members.OrderBy(item => item.Key, StringComparer.Ordinal).Select(item => item.Value).ToArray(),
            primitive_entries = state.Scene.Display.Entries.OrderBy(item => item.Key, StringComparer.Ordinal).Select(item => item.Value).ToArray()
        };
    }

    private static bool CameraEditingVerify(CameraEditingState before, CameraEditingState after,
        Dictionary<string, object> plan, Dictionary<string, object> result)
    {
        bool context = SelectionEditingSameContext(before.Scene, after.Scene);
        bool ce = before.Scene.Ce == after.Scene.Ce;
        bool selection = before.Scene.References.SequenceEqual(after.Scene.References, StringComparer.Ordinal);
        var members = CompareDrawListVisualSnapshots(before.Scene.Display.Members, after.Scene.Display.Members);
        var primitives = CompareDrawListPrimitiveSnapshots(before.Scene.Display.Entries, after.Scene.Display.Entries);
        bool requested = CameraMatches(after.Camera, plan);
        bool original = CameraMatches(after.Camera, before.Camera);
        bool shaded = Equals(before.Camera["shaded"], after.Camera["shaded"]);
        bool dimensions = Equals(before.Camera["width"], after.Camera["width"]) && Equals(before.Camera["height"], after.Camera["height"]);
        result["after"] = CameraEditingObservation(after); result["actual_camera"] = after.Camera;
        result["residual_complete"] = true; result["camera_readback_succeeded"] = true;
        result["camera_matches_request"] = requested; result["camera_matches_before"] = original;
        result["context_unchanged"] = context; result["ce_after"] = after.Scene.Ce; result["ce_unchanged"] = ce;
        result["selection_unchanged"] = selection; result["members_unchanged"] = members.Count == 0;
        result["primitives_unchanged"] = primitives.Count == 0; result["shaded_unchanged"] = shaded;
        result["dimensions_unchanged"] = dimensions;
        result["member_verification_errors"] = members; result["primitive_verification_errors"] = primitives;
        bool verified = context && ce && selection && members.Count == 0 && primitives.Count == 0 && shaded && dimensions && requested;
        bool originalState = original && ce && selection && members.Count == 0 && primitives.Count == 0 && shaded && dimensions;
        bool attempted = Equals(result["write_attempted"], true);
        result["write_applied"] = attempted ? (context ? (object)!original : null) : false;
        result["partial_write"] = attempted ? (context ? (object)(!verified && !originalState) : null) : false;
        result["outcome_uncertain"] = attempted && !context;
        return verified;
    }

    private static void CameraEditingUnknown(Dictionary<string, object> result)
    {
        bool attempted = Equals(result["write_attempted"], true);
        result["success"] = false; result["readback_verified"] = false;
        result["residual_complete"] = false; result["camera_readback_succeeded"] = false;
        result["actual_camera"] = null; result["after"] = null;
        result["camera_matches_request"] = null; result["camera_matches_before"] = null;
        result["context_unchanged"] = null; result["ce_after"] = null; result["ce_unchanged"] = null;
        result["selection_unchanged"] = null; result["members_unchanged"] = null; result["primitives_unchanged"] = null;
        result["shaded_unchanged"] = null; result["dimensions_unchanged"] = null;
        result["write_applied"] = attempted ? (object)null : false;
        result["partial_write"] = attempted ? (object)null : false;
        result["outcome_uncertain"] = attempted;
    }
}
