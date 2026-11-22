using System;
using System.Collections.Generic;
using System.Globalization;
using Aveva.Core.Database;
using Aveva.Core.Geometry;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    // Read only the PAVE records already returned by PanelReadGeometry. These
    // native derived points do not establish a sweep, surface offset or hole.
    private void PanelAddBoundaryPoints(DbElement pane, Dictionary<string, object> geometry, bool includeWorld)
    {
        ThrowIfNativeFaulted();
        var errors = new Dictionary<string, string>();
        bool sourceComplete = geometry.TryGetValue("complete", out object completeValue) && Equals(completeValue, true);
        geometry["boundary_points_requested"] = true;
        geometry["boundary_points_complete"] = false;
        geometry["boundary_points_errors"] = errors;
        geometry["boundary_points_vertex_count"] = 0;
        geometry["boundary_points_curved_vertex_count"] = 0;
        geometry["boundary_points_source_geometry_complete"] = sourceComplete;
        geometry["boundary_points_read_complete"] = false;
        geometry["boundary_points_scope"] = "Raw native PPOS P0/P4/P6/P8 and PDIR P0 for enumerated nonzero-FRAD PAVEs only. Read completeness covers this returned vertex set; whole-PANE coverage additionally requires source geometry completeness. No arc sweep, thickness offset, hole role or rendered boundary is inferred.";

        string paneFrameRef = null, paneFrameError = null;
        try { paneFrameRef = ModelReference(NativeRead(() => pane.Ref)); }
        catch (Exception ex) { ThrowIfNativeFaulted(); paneFrameError = ex.Message; }

        DbElement world = null;
        string worldFrameRef = null, worldFrameError = null;
        bool worldFrameAttempted = false;
        int vertexCount = 0, curvedCount = 0;
        bool limitReached = false;
        IList<object> loops = geometry.TryGetValue("loops", out object loopValue) ? loopValue as IList<object> : null;
        if (loops == null)
        {
            errors["loops"] = "The source panel read did not return a loop list.";
            loops = new List<object>();
        }
        for (int loopIndex = 0; loopIndex < loops.Count && !limitReached; loopIndex++)
        {
            ThrowIfNativeFaulted();
            var loop = loops[loopIndex] as Dictionary<string, object>;
            IList<object> vertices = loop != null && loop.TryGetValue("vertices", out object verticesValue)
                ? verticesValue as IList<object> : null;
            if (vertices == null)
            {
                errors["loop:" + loopIndex] = "The source loop did not return a vertex list.";
                continue;
            }
            for (int vertexIndex = 0; vertexIndex < vertices.Count; vertexIndex++)
            {
                ThrowIfNativeFaulted();
                if (vertexCount == PanelPolygon.InspectVertexLimit)
                {
                    errors["vertex_limit"] = "The returned PAVE set exceeds the existing panel vertex limit; remaining boundary points were not read.";
                    limitReached = true;
                    break;
                }
                vertexCount++;
                var vertex = vertices[vertexIndex] as Dictionary<string, object>;
                string key = "loop:" + loopIndex + ":vertex:" + vertexIndex;
                if (vertex == null)
                {
                    errors[key] = "The source vertex record is unavailable.";
                    continue;
                }
                string refno = vertex.TryGetValue("refno", out object refValue) ? refValue as string : null;
                if (!string.IsNullOrWhiteSpace(refno)) key = refno;
                object radiusValue = vertex.TryGetValue("fillet_radius_mm", out object radius) ? radius : null;
                if (!PanelBoundaryTryRadius(radiusValue, out double radiusMm))
                {
                    string error = "A finite native FRAD is required to decide whether derived boundary points are needed.";
                    vertex["boundary_points"] = new Dictionary<string, object>
                    {
                        ["required"] = null, ["reason"] = "fillet_radius_unavailable", ["complete"] = false,
                        ["errors"] = new Dictionary<string, string> { ["FRAD"] = error }
                    };
                    errors[key + ":FRAD"] = error;
                    continue;
                }
                if (radiusMm == 0)
                {
                    vertex["boundary_points"] = new Dictionary<string, object>
                    {
                        ["required"] = false, ["reason"] = "straight_vertex"
                    };
                    continue;
                }

                curvedCount++;
                DbElement pave = null;
                string paveError = null;
                try
                {
                    if (string.IsNullOrWhiteSpace(refno))
                        throw new InvalidOperationException("The source PAVE reference is unavailable.");
                    pave = ModelRequireElement(refno);
                    if (NativeRead(() => pave.DbType) != DbType.Design ||
                        !string.Equals(NativeRead(() => NativeRead(() => pave.GetElementType()).ShortName), "PAVE", StringComparison.OrdinalIgnoreCase))
                        throw new InvalidOperationException("The source reference no longer identifies a Design PAVE.");
                }
                catch (Exception ex) { ThrowIfNativeFaulted(); pave = null; paveError = ex.Message; }

                var boundary = new Dictionary<string, object> { ["required"] = true };
                vertex["boundary_points"] = boundary;
                var local = PanelBoundaryReadFrame(pave, paneFrameError == null ? pane : null, paneFrameRef,
                    paveError ?? paneFrameError);
                boundary["panel_local"] = local;
                PanelBoundaryCollectErrors(errors, key + ":panel_local", local);
                bool boundaryComplete = Equals(local["complete"], true);
                if (includeWorld)
                {
                    if (!worldFrameAttempted)
                    {
                        worldFrameAttempted = true;
                        try
                        {
                            world = ModelFrame("WORLD", NativeRead(() => pane.Owner));
                            worldFrameRef = ModelReference(NativeRead(() => world.Ref));
                            if (geometry.TryGetValue("world_position_frame", out object observedFrame) &&
                                observedFrame is string observed && !string.IsNullOrWhiteSpace(observed) && observed != worldFrameRef)
                                throw new InvalidOperationException("The WORLD frame differs from the source panel WORLD position read.");
                        }
                        catch (Exception ex) { ThrowIfNativeFaulted(); world = null; worldFrameError = ex.Message; }
                    }
                    var absolute = PanelBoundaryReadFrame(pave, world, worldFrameRef, paveError ?? worldFrameError);
                    boundary["WORLD"] = absolute;
                    PanelBoundaryCollectErrors(errors, key + ":WORLD", absolute);
                    boundaryComplete &= Equals(absolute["complete"], true);
                }
                boundary["complete"] = boundaryComplete;
            }
        }
        if (!geometry.TryGetValue("vertex_count", out object countValue) || !(countValue is int count) || count != vertexCount)
            errors["source_vertex_count"] = "The enumerated boundary vertex count does not match the source panel vertex count.";
        geometry["boundary_points_vertex_count"] = vertexCount;
        geometry["boundary_points_curved_vertex_count"] = curvedCount;
        geometry["boundary_points_read_complete"] = errors.Count == 0;
        geometry["boundary_points_complete"] = sourceComplete && errors.Count == 0;
    }

    private static bool PanelBoundaryTryRadius(object value, out double radius)
    {
        radius = 0;
        if (!(value is double) && !(value is float) && !(value is decimal) &&
            !(value is int) && !(value is long) && !(value is short) && !(value is byte)) return false;
        try { radius = Convert.ToDouble(value, CultureInfo.InvariantCulture); }
        catch (Exception) { return false; }
        return !double.IsNaN(radius) && !double.IsInfinity(radius);
    }

    private static Dictionary<string, object> PanelBoundaryReadFrame(DbElement pave, DbElement frame,
        string frameRef, string unavailableError)
    {
        ThrowIfNativeFaulted();
        var errors = new Dictionary<string, string>();
        var points = new List<object>();
        var result = new Dictionary<string, object>
        {
            ["frame_refno"] = frameRef, ["unit"] = "mm", ["points"] = points,
            ["complete"] = false, ["errors"] = errors,
            ["source"] = "Native PPOS/PDIR with numeric P-point qualifier and explicit WRT frame; no projected or substitute coordinates."
        };
        foreach (int number in new[] { 0, 4, 6, 8 })
        {
            ThrowIfNativeFaulted();
            var pointErrors = new Dictionary<string, string>();
            var point = new Dictionary<string, object>
            {
                ["number"] = number, ["exists"] = null, ["exists_checked"] = false,
                ["position_mm"] = null, ["position_complete"] = false,
                ["complete"] = false, ["errors"] = pointErrors
            };
            points.Add(point);
            if (number == 0)
            {
                point["direction"] = null;
                point["direction_complete"] = false;
                point["existence_source"] = "P0 existence is not queried: installed native PAVE export reads P0 POS/DIR directly; PPEXST(0) is not an established admission test.";
            }
            else point["existence_source"] = "Native PPEXST with numeric P-point qualifier; independent of PPOS availability.";

            if (pave == null || frame == null || unavailableError != null)
            {
                pointErrors["source"] = unavailableError ?? "The source PAVE or explicit coordinate frame is unavailable.";
            }
            else
            {
                if (number != 0)
                {
                    try
                    {
                        DbQualifier existsQualifier = PanelBoundaryQualifier(null, number);
                        point["exists_checked"] = true;
                        bool exists = NativeRead(() => pave.GetBool(DbAttributeInstance.PPEXST, existsQualifier));
                        point["exists"] = exists;
                        if (!exists) pointErrors["exists"] = "Native PPEXST reports that this required derived point does not exist.";
                    }
                    catch (Exception ex) { ThrowIfNativeFaulted(); pointErrors["exists"] = ex.Message; }
                }
                // Preserve PPOS as an independent observation, including when
                // PPEXST is unavailable or disagrees. Neither read replaces the other.
                try
                {
                    DbQualifier positionQualifier = PanelBoundaryQualifier(frame, number);
                    Position native = NativeRead(() => pave.GetPosition(DbAttributeInstance.PPOS, positionQualifier));
                    if (native == null) throw new InvalidOperationException("SDK returned no qualified derived P-point position.");
                    point["position_mm"] = FiniteVector(
                        NativeRead(() => native.X), NativeRead(() => native.Y), NativeRead(() => native.Z));
                    point["position_complete"] = true;
                }
                catch (Exception ex) { ThrowIfNativeFaulted(); pointErrors["position"] = ex.Message; }
                if (number == 0)
                {
                    try
                    {
                        DbQualifier directionQualifier = PanelBoundaryQualifier(frame, 0);
                        Direction native = NativeRead(() => pave.GetDirection(DbAttributeInstance.PDIR, directionQualifier));
                        if (native == null) throw new InvalidOperationException("SDK returned no qualified P0 direction.");
                        point["direction"] = FiniteVector(
                            NativeRead(() => native.East), NativeRead(() => native.North), NativeRead(() => native.Up));
                        point["direction_complete"] = true;
                    }
                    catch (Exception ex) { ThrowIfNativeFaulted(); pointErrors["direction"] = ex.Message; }
                }
            }
            point["complete"] = pointErrors.Count == 0 && Equals(point["position_complete"], true) &&
                (number == 0 ? Equals(point["direction_complete"], true) : Equals(point["exists"], true));
            foreach (KeyValuePair<string, string> error in pointErrors)
                errors["P" + number + "." + error.Key] = error.Value;
        }
        result["complete"] = errors.Count == 0;
        return result;
    }

    private static DbQualifier PanelBoundaryQualifier(DbElement frame, int number)
    {
        DbQualifier qualifier = NativeRead(() => new DbQualifier());
        if (frame != null) NativeWrite(() => qualifier.wrtQualifier = frame);
        NativeWrite(() => qualifier.Add((double)number));
        return qualifier;
    }

    private static void PanelBoundaryCollectErrors(Dictionary<string, string> target, string prefix,
        Dictionary<string, object> frame)
    {
        var errors = (Dictionary<string, string>)frame["errors"];
        foreach (KeyValuePair<string, string> error in errors) target[prefix + ":" + error.Key] = error.Value;
    }
}
