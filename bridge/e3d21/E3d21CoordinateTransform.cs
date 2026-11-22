using System;
using System.Collections.Generic;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    public object TransformCoordinates(string fromWrt, string toWrt, IList<double[]> points, IList<double[]> vectors)
    {
        string sourceName = CoordinateFrameName(fromWrt, "from_wrt");
        string targetName = CoordinateFrameName(toWrt, "to_wrt");
        if (points == null || vectors == null || points.Count > 128 || vectors.Count > 128 || points.Count + vectors.Count == 0)
            throw new ArgumentException("Provide 1 to 256 total points and vectors, with at most 128 of either.");
        foreach (double[] point in points) CoordinateTransformFrame.World().PointToWorld(point);
        foreach (double[] vector in vectors) CoordinateTransformFrame.World().VectorToWorld(vector);
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

        var result = new Dictionary<string, object>
        {
            ["success"] = false, ["available"] = false, ["complete"] = false,
            ["source"] = "E3D SDK WRT frame reads and validated rigid transform",
            ["single_dispatch"] = true,
            ["consistency"] = "single_ui_dispatch",
            ["consistency_scope"] = "Frames and coordinates were read/converted in one UI callback; this is not a database lock.",
            ["from_wrt"] = sourceName, ["to_wrt"] = targetName,
            ["points"] = new List<object>(), ["vectors"] = new List<object>(),
            ["read_errors"] = new List<object>(),
            ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
            ["project_before"] = null, ["project_after"] = null, ["mdb_before"] = null, ["mdb_after"] = null,
            ["identity_unchanged"] = null
        };
        OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
            string ceBefore = NativeRead(() => ModelCurrentReference());
            Project projectBefore = NativeRead(() => Project.CurrentProject);
            MDB mdbBefore = NativeRead(() => MDB.CurrentMDB);
            if (projectBefore == null || mdbBefore == null) throw new InvalidOperationException("An open E3D project and MDB are required.");
            result["ce_before"] = ceBefore;
            result["project_before"] = NativeRead(() => projectBefore.Name);
            result["mdb_before"] = NativeRead(() => mdbBefore.Name);
            DbElement sourceElement = ResolveCoordinateFrame(sourceName, mdbBefore);
            DbElement targetElement = ResolveCoordinateFrame(targetName, mdbBefore);
            if (IsUsable(sourceElement)) result["from_frame"] = CoordinateFrameIdentity(sourceElement, sourceName);
            if (IsUsable(targetElement)) result["to_frame"] = CoordinateFrameIdentity(targetElement, targetName);
            var errors = (List<object>)result["read_errors"];
            CoordinateTransformFrame source = ReadCoordinateFrame(sourceElement, sourceName, "from_frame", errors);
            CoordinateTransformFrame target = ReadCoordinateFrame(targetElement, targetName, "to_frame", errors);

            var convertedPoints = (List<object>)result["points"];
            for (int i = 0; i < points.Count; i++)
            {
                double[] output = null;
                bool converted = source != null && target != null;
                if (converted)
                {
                    try { output = target.PointFromWorld(source.PointToWorld(points[i])); }
                    catch (ArgumentException ex)
                    {
                        converted = false;
                        errors.Add(new Dictionary<string, object> { ["path"] = "points[" + i + "]",
                            ["code"] = "conversion_failed", ["message"] = ex.Message });
                    }
                }
                convertedPoints.Add(new Dictionary<string, object> { ["index"] = i, ["input"] = points[i],
                    ["output"] = output, ["success"] = converted, ["unit"] = "mm" });
            }
            var convertedVectors = (List<object>)result["vectors"];
            for (int i = 0; i < vectors.Count; i++)
            {
                double[] output = null;
                bool converted = source != null && target != null;
                if (converted)
                {
                    try { output = target.VectorFromWorld(source.VectorToWorld(vectors[i])); }
                    catch (ArgumentException ex)
                    {
                        converted = false;
                        errors.Add(new Dictionary<string, object> { ["path"] = "vectors[" + i + "]",
                            ["code"] = "conversion_failed", ["message"] = ex.Message });
                    }
                }
                convertedVectors.Add(new Dictionary<string, object> { ["index"] = i, ["input"] = vectors[i],
                    ["output"] = output, ["success"] = converted });
            }
            string ceAfter = NativeRead(() => ModelCurrentReference());
            Project projectAfter = NativeRead(() => Project.CurrentProject);
            MDB mdbAfter = NativeRead(() => MDB.CurrentMDB);
            result["ce_after"] = ceAfter;
            result["project_after"] = projectAfter == null ? null : NativeRead(() => projectAfter.Name);
            result["mdb_after"] = mdbAfter == null ? null : NativeRead(() => mdbAfter.Name);
            result["ce_unchanged"] = string.Equals(ceBefore, ceAfter, StringComparison.Ordinal);
            bool identityUnchanged = ReferenceEquals(projectBefore, projectAfter) && ReferenceEquals(mdbBefore, mdbAfter) &&
                Equals(result["project_before"], result["project_after"]) && Equals(result["mdb_before"], result["mdb_after"]);
            result["identity_unchanged"] = identityUnchanged;
            result["available"] = source != null && target != null;
            result["success"] = errors.Count == 0 && Equals(result["ce_unchanged"], true) && identityUnchanged;
            result["complete"] = Equals(result["success"], true);
            if (!Equals(result["ce_unchanged"], true) || !identityUnchanged)
                ((List<object>)result["read_errors"]).Add(new Dictionary<string, object> {
                    ["path"] = "context", ["code"] = "context_changed", ["message"] = "CE, project or MDB identity changed during coordinate conversion." });
            return result;
        });
        return result;
    }

    private static string CoordinateFrameName(string value, string field)
    {
        string name = (value ?? "").Trim();
        if (name.Equals("WORLD", StringComparison.OrdinalIgnoreCase)) return "WORLD";
        return ModelEditInput.ExplicitReference(name, field);
    }

    private static DbElement ResolveCoordinateFrame(string name, MDB mdb)
    {
        DbElement element = name == "WORLD" ? NativeRead(() => mdb.GetFirstWorld(DbType.Design)) : ResolveElement(name);
        return IsUsable(element) ? element : null;
    }

    private static CoordinateTransformFrame ReadCoordinateFrame(DbElement frame, string name, string path, List<object> errors)
    {
        if (!IsUsable(frame))
        {
            errors.Add(new Dictionary<string, object> { ["path"] = path, ["code"] = "frame_not_found",
                ["message"] = "Reference frame was not found: " + name });
            return null;
        }
        if (name == "WORLD") return CoordinateTransformFrame.World();
        try
        {
            Dictionary<string, object> spatial = ReadSpatialInfoOnUi(frame, "WORLD");
            var position = spatial["position"] as Dictionary<string, object>;
            var orientation = spatial["orientation"] as Dictionary<string, object>;
            if (!Equals(spatial["position_complete"], true) || !Equals(spatial["orientation_complete"], true) || position == null || orientation == null)
            {
                errors.Add(new Dictionary<string, object> { ["path"] = path, ["code"] = "frame_pose_unavailable",
                    ["message"] = "Reference frame position and orientation must both be available: " + name });
                return null;
            }
            return new CoordinateTransformFrame(new[] { Convert.ToDouble(position["x"]), Convert.ToDouble(position["y"]), Convert.ToDouble(position["z"]) },
                orientation["x_axis"] as double[], orientation["y_axis"] as double[], orientation["z_axis"] as double[]);
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            errors.Add(new Dictionary<string, object> { ["path"] = path, ["code"] = "frame_pose_invalid",
                ["message"] = "Reference frame pose could not be validated: " + ex.Message });
            return null;
        }
    }

    private static object CoordinateFrameIdentity(DbElement element, string requested)
    {
        return new Dictionary<string, object> { ["requested"] = requested, ["refno"] = ElementRef(element),
            ["type"] = ElementType(element), ["name"] = SafeString(() => element.GetAsString(DbAttributeInstance.NAME)) };
    }
}
