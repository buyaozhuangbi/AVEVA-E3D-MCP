using System;
using System.Collections.Generic;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core.Geometry;
using Aveva.Core.Utilities.Messaging;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private const int PanelLoopLimit = 32;
    private const int PanelMemberLimit = 10000;

    public object CreatePanel(string parentRef, string name, double[][] vertices, double thickness,
        double[] position, double[] xAxis, double[] yAxis, string wrt, string justification, bool dryRun)
    {
        string parentTarget = ModelEditInput.ExplicitReference(parentRef, "parentRef");
        string requestedName = ModelEditInput.ElementName(name);
        double[][] outline = vertices == null ? null : vertices.Select(item => item == null ? null : (double[])item.Clone()).ToArray();
        PanelPolygonValidation polygon = PanelPolygon.Validate(outline, true, PanelPolygon.CreateVertexLimit);
        if (double.IsNaN(thickness) || double.IsInfinity(thickness) || thickness <= 0)
            throw new ArgumentException("thickness must be a finite positive number in mm.", "thickness");
        string requestedJustification = (justification ?? "positive").Trim().ToLowerInvariant();
        string nativeJustification = PanelJustificationToken(requestedJustification);
        // A complete explicit transform prevents host defaults from deciding the new panel frame.
        ModelEditInput input = new ModelEditInput(position ?? new[] { 0.0, 0, 0 },
            xAxis ?? (yAxis == null ? new[] { 1.0, 0, 0 } : null),
            yAxis ?? (xAxis == null ? new[] { 0.0, 1, 0 } : null), wrt, true);
        string command = "database-create-panel " + parentTarget;
        if (dryRun ? IsCommandActive() : !TryBeginCommand(command)) return BusyResult(command);
        try
        {
            return OnUi(delegate
            {
                var result = ModelEditResult(dryRun);
                DbElement pane = null;
                int[] createdReference = null;
                string cleanupParentRef = null;
                string[] previousReferences = null;
                PanelCreationJournal journal = null;
                result["creation_attempted"] = false;
                result["creation_result_unknown"] = false;
                result["outcome_uncertain"] = false;
                result["retry_safe"] = true;
                result["cleanup_attempted"] = false;
                result["cleanup_succeeded"] = null;
                result["residual_refno"] = null;
                result["polygon"] = polygon;
                result["parent_membership_verified"] = false;
                result["creation_journal"] = new List<object>();
                result["creation_journal_complete"] = false;
                result["validation_scope"] = "Finite simple counterclockwise local XY outline, positive thickness, explicit Design parent, native member types and attribute support, and frame conversion. Each returned new PANE/PLOO/PAVE is registered only after exact identity, type, owner, empty initial members and complete append-only membership verification. Cleanup requires the complete current tree to match that ordered journal, including empty leaves. Unreturned or unverified creates and foreign members block cascading cleanup. This is bounded call-return evidence, not a database transaction or concurrency lock.";
                try
                {
                    result["ce_before"] = ModelCurrentReference();
                    if (!polygon.valid)
                        throw new ArgumentException("vertices must describe one simple nondegenerate counterclockwise outline; inspect polygon.issues.");
                    DbElement parent = ModelRequireElement(parentTarget);
                    if (NativeRead(() => parent.DbType) != DbType.Design)
                        throw new InvalidOperationException("parent must be in a Design database.");
                    cleanupParentRef = ModelReference(NativeRead(() => parent.Ref));
                    DbElementType paneType = PanelRequireType("PANE");
                    DbElementType loopType = PanelRequireType("PLOO");
                    DbElementType vertexType = PanelRequireType("PAVE");
                    if (!NativeRead(() => parent.IsCreatable(paneType)))
                        throw new InvalidOperationException("E3D does not allow creating PANE under this parent now.");
                    if (!NativeRead(() => paneType.MemberTypes().Any(type => type == loopType)) ||
                        !NativeRead(() => loopType.MemberTypes().Any(type => type == vertexType)))
                        throw new InvalidOperationException("The SDK does not support the required PANE/PLOO/PAVE relationship.");
                    ModelRequireTypeAttribute(paneType, DbAttributeInstance.POS);
                    ModelRequireTypeAttribute(paneType, DbAttributeInstance.ORI);
                    if (requestedName != null) ModelRequireTypeAttribute(paneType, DbAttributeInstance.NAME);
                    ModelRequireTypeAttribute(loopType, DbAttributeInstance.HEIG);
                    ModelRequireTypeAttribute(loopType, DbAttributeInstance.SJUS);
                    ModelRequireTypeAttribute(loopType, DbAttributeInstance.LBLIND);
                    ModelRequireTypeAttribute(vertexType, DbAttributeInstance.POS);
                    ModelRequireTypeAttribute(vertexType, DbAttributeInstance.FRAD);
                    MemberPage<DbElement> previousMembers = PanelMembers(parent, PanelMemberLimit);
                    PanelRequireComplete(previousMembers, "parent membership before creation");
                    previousReferences = previousMembers.Items.Select(item => ModelReference(NativeRead(() => item.Ref))).ToArray();
                    journal = new PanelCreationJournal(cleanupParentRef, previousReferences,
                        (List<object>)result["creation_journal"]);
                    DbElement frame = ModelFrame(input.Wrt, parent);
                    DbElement storageFrame = ModelStorageFrame(parent);
                    ModelEditInput nativeInput = ModelStorageTransform(storageFrame, frame).ToNative(input);
                    Position newPosition = ModelPosition(nativeInput.Position);
                    Orientation newOrientation = ModelOrientation(nativeInput);
                    Position[] newVertices = outline.Select(item => ModelPosition(new[] { item[0], item[1], 0.0 })).ToArray();
                    result["parent"] = ModelIdentity(parent);
                    result["frame"] = ModelFrameIdentity(frame, input.Wrt);
                    result["submitted"] = new Dictionary<string, object>
                    {
                        ["name"] = requestedName, ["vertices"] = outline, ["thickness_mm"] = thickness,
                        ["justification"] = requestedJustification, ["native_justification"] = nativeJustification,
                        ["position"] = input.Position, ["orientation"] = ModelSubmittedOrientation(input),
                        ["vertex_coordinate_frame"] = "new PANE local XY; Z = 0 mm"
                    };
                    result["existing_parent_member_count"] = previousReferences.Length;
                    if (dryRun)
                    {
                        result["ce_after"] = ModelCurrentReference();
                        ModelCeUnchanged(result);
                        if (!Equals(result["ce_unchanged"], true))
                            throw new InvalidOperationException("Current element changed during panel preview.");
                        result["success"] = true;
                        return result;
                    }

                    result["creation_stage"] = "PANE";
                    pane = PanelCreateTracked(parent, paneType, journal, result);
                    createdReference = (int[])journal.Nodes[0].Reference.Clone();
                    string returnedRefno = journal.Nodes[0].Refno;
                    result["refno"] = returnedRefno;
                    if (requestedName != null) PanelWriteString(pane, DbAttributeInstance.NAME, requestedName);
                    ModelRequireStorageFrame(pane, storageFrame, input);
                    ModelWritePosition(pane, newPosition);
                    ModelWriteOrientation(pane, newOrientation);
                    result["creation_stage"] = "PLOO";
                    DbElement loop = PanelCreateTracked(pane, loopType, journal, result);
                    result["loop_refno"] = ModelReference(NativeRead(() => loop.Ref));
                    PanelWriteDouble(loop, DbAttributeInstance.HEIG, thickness);
                    PanelWriteString(loop, DbAttributeInstance.SJUS, nativeJustification);
                    PanelWriteBool(loop, DbAttributeInstance.LBLIND, false);
                    var vertexReferences = new List<string>();
                    for (int index = 0; index < newVertices.Length; index++)
                    {
                        ThrowIfNativeFaulted();
                        result["creation_stage"] = "PAVE " + index;
                        DbElement vertex = PanelCreateTracked(loop, vertexType, journal, result);
                        vertexReferences.Add(ModelReference(NativeRead(() => vertex.Ref)));
                        var vertexInput = new ModelEditInput(new[] { outline[index][0], outline[index][1], 0.0 }, null, null, "OWNER", true);
                        // PLOO has no POS/ORI frame. Confirm each native PAVE POS belongs to PANE before writing it.
                        ModelRequireStorageFrame(vertex, pane, vertexInput);
                        ModelWritePosition(vertex, newVertices[index]);
                        PanelWriteDouble(vertex, DbAttributeInstance.FRAD, 0);
                        Position actual = NativeRead(() => vertex.GetPosition(DbAttributeInstance.POS, new DbQualifier { wrtQualifier = pane }));
                        if (actual == null || !PanelLocalPositionMatches(vertexInput.Position, FiniteVector(NativeRead(() => actual.X), NativeRead(() => actual.Y), NativeRead(() => actual.Z))))
                            throw new InvalidOperationException("PAVE POS readback did not match vertex " + index + " in PANE coordinates.");
                    }
                    result["creation_stage"] = "verification";
                    PanelRequireSequence(parent, previousReferences.Concat(new[] { returnedRefno }).ToArray(), "parent membership after creation");
                    result["parent_membership_verified"] = true;
                    PanelRequireSequence(pane, new[] { ModelReference(NativeRead(() => loop.Ref)) }, "new PANE membership");
                    PanelRequireSequence(loop, vertexReferences.ToArray(), "new PLOO membership");
                    if (requestedName != null && !ModelEditInput.TextMatches(requestedName, NativeRead(() => pane.GetString(DbAttributeInstance.NAME)), true))
                        throw new InvalidOperationException("NAME readback did not match the submitted name.");
                    double actualThickness = ModelFinite(NativeRead(() => loop.GetDouble(DbAttributeInstance.HEIG)));
                    string actualJustification = NativeRead(() => loop.GetAsString(DbAttributeInstance.SJUS));
                    if (!ModelEditInput.NumberMatches(thickness, actualThickness) ||
                        !PanelJustificationMatches(nativeJustification, actualJustification) || NativeRead(() => loop.GetBool(DbAttributeInstance.LBLIND)))
                        throw new InvalidOperationException("PLOO thickness, justification, or blind flag readback did not match.");
                    result["thickness_mm"] = actualThickness;
                    result["native_justification"] = actualJustification;
                    ModelAddTransform(result, pane, new DbQualifier { wrtQualifier = frame }, input);
                    Dictionary<string, object> geometry = PanelReadGeometry(pane);
                    result["geometry"] = geometry;
                    if (!(bool)geometry["complete"] || !Equals(geometry["topology_valid"], true))
                        throw new InvalidOperationException("New panel geometry readback could not verify the complete straight outline.");
                    if (!ModelEditInput.NumberMatches(thickness, (double)geometry["thickness_mm"]) ||
                        !PanelJustificationMatches(nativeJustification, (string)geometry["native_justification"]))
                        throw new InvalidOperationException("PANE thickness or justification did not match the submitted PLOO definition.");
                    result["element"] = ModelIdentity(pane);
                    result["vertex_refnos"] = vertexReferences;
                    result["creation_stage"] = "complete";
                    result["ce_after"] = ModelCurrentReference();
                    ModelCeUnchanged(result);
                    if (!Equals(result["ce_unchanged"], true))
                        throw new InvalidOperationException("Current element changed during panel creation.");
                    PanelVerifyCreationJournal(journal);
                    result["creation_journal_complete"] = true;
                    result["success"] = true;
                }
                catch (Exception ex)
                {
                    ThrowIfNativeFaulted();
                    result["error"] = ex.Message;
                    result["readback_verified"] = dryRun ? (object)null : false;
                    if (journal != null && journal.CreationAttempted)
                    {
                        PanelCleanupCreated(result, pane, createdReference, journal);
                    }
                    result["outcome_uncertain"] = Equals(result["creation_result_unknown"], true);
                    result["retry_safe"] = journal == null || !journal.CreationAttempted || Equals(result["cleanup_succeeded"], true);
                }
                ModelCaptureCeAfter(result);
                return result;
            });
        }
        finally { if (!dryRun) EndCommand(); }
    }

    public object GetPanelGeometry(string refno, bool includeWorld = false, bool includeBoundary = false)
    {
        string target = ModelEditInput.ExplicitReference(refno, "refno");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
            DbElement pane = ModelRequireElement(target);
            if (NativeRead(() => pane.DbType) != DbType.Design || !string.Equals(NativeRead(() => NativeRead(() => pane.GetElementType()).ShortName), "PANE", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("refno must identify a Design PANE.");
            Dictionary<string, object> geometry = PanelReadGeometry(pane);
            if (includeWorld) PanelAddWorldPositions(pane, geometry);
            if (includeBoundary) PanelAddBoundaryPoints(pane, geometry, includeWorld);
            return geometry;
        });
    }

    private void PanelAddWorldPositions(DbElement pane, Dictionary<string, object> geometry)
    {
        var worldErrors = new Dictionary<string, string>();
        geometry["world_positions_requested"] = true;
        geometry["world_position_unit"] = "mm";
        geometry["world_position_frame"] = null;
        geometry["world_position_source"] = "PAVE native POS qualified WRT current MDB Design WORLD";
        geometry["world_read_errors"] = worldErrors;
        // WORLD completeness describes only the PAVE set returned by the local
        // read. Local geometry completeness remains an independent result field.
        bool vertexSetComplete = Equals(geometry["complete"], true);
        int worldPositionCount = 0;
        int worldPositionVertexCount = 0;
        geometry["world_position_count"] = worldPositionCount;
        geometry["world_position_vertex_count"] = worldPositionVertexCount;
        DbElement world = null;
        try
        {
            world = ModelFrame("WORLD", NativeRead(() => pane.Owner));
            geometry["world_position_frame"] = ModelReference(NativeRead(() => world.Ref));
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            worldErrors["WORLD"] = ex.Message;
        }

        var loops = (List<object>)geometry["loops"];
        foreach (object loopValue in loops)
        {
            var loop = loopValue as Dictionary<string, object>;
            if (loop == null || !(loop.TryGetValue("vertices", out object verticesValue)) || !(verticesValue is List<object> vertices))
            {
                vertexSetComplete = false;
                continue;
            }
            foreach (object vertexValue in vertices)
            {
                var vertex = vertexValue as Dictionary<string, object>;
                if (vertex == null) { vertexSetComplete = false; continue; }
                vertex["position_world_mm"] = null;
                vertex["world_position_complete"] = false;
                worldPositionVertexCount++;
                geometry["world_position_vertex_count"] = worldPositionVertexCount;
                string refno = vertex.TryGetValue("refno", out object reference) ? Convert.ToString(reference) : "unknown";
                if (world == null)
                {
                    vertex["world_position_error"] = worldErrors.TryGetValue("WORLD", out string frameError) ? frameError : "Design WORLD is unavailable.";
                    continue;
                }
                try
                {
                    DbElement pave = ModelRequireElement(refno);
                    Position native = NativeRead(() => pave.GetPosition(DbAttributeInstance.POS,
                        new DbQualifier { wrtQualifier = world }));
                    if (native == null) throw new InvalidOperationException("SDK returned no WORLD-qualified PAVE POS.");
                    vertex["position_world_mm"] = FiniteVector(
                        NativeRead(() => native.X), NativeRead(() => native.Y), NativeRead(() => native.Z));
                    vertex["world_position_complete"] = true;
                    worldPositionCount++;
                    geometry["world_position_count"] = worldPositionCount;
                    vertex.Remove("world_position_error");
                }
                catch (Exception ex)
                {
                    ThrowIfNativeFaulted();
                    vertex["world_position_error"] = ex.Message;
                    worldErrors[refno] = ex.Message;
                }
            }
        }
        geometry["world_positions_complete"] = vertexSetComplete && worldErrors.Count == 0 &&
            worldPositionCount == worldPositionVertexCount &&
            (worldPositionVertexCount > 0 || Equals(geometry["complete"], true));
    }

    private Dictionary<string, object> PanelReadGeometry(DbElement pane)
    {
        ThrowIfNativeFaulted();
        var loops = new List<object>();
        var errors = new Dictionary<string, string>();
        var unsupported = new List<object>();
        var result = new Dictionary<string, object>
        {
            ["success"] = false, ["complete"] = false, ["partial"] = false,
            ["element"] = ModelIdentity(pane), ["unit"] = "mm",
            ["coordinate_frame"] = ModelReference(NativeRead(() => pane.Ref)),
            ["coordinate_source"] = "PAVE POS qualified WRT PANE",
            ["loops"] = loops, ["loop_count"] = 0, ["vertex_count"] = 0,
            ["topology_valid"] = null,
            ["validation_scope"] = "Individual straight coplanar PLOO polygons in PANE coordinates. Multiple-loop containment, hole relationships, fillets/arcs, fittings and negative geometry are not evaluated.",
            ["limits"] = new Dictionary<string, object>
            {
                ["loops"] = PanelLoopLimit, ["total_vertices"] = PanelPolygon.InspectVertexLimit,
                ["direct_members_per_container"] = PanelMemberLimit
            },
            ["unsupported_features"] = unsupported, ["read_errors"] = errors
        };
        // These PANE pseudo attributes can be readable even when IsAttributeValid reports false.
        try { result["thickness_mm"] = ModelFinite(NativeRead(() => pane.GetDouble(DbAttributeInstance.THICKN))); }
        catch (Exception ex) { ThrowIfNativeFaulted(); errors["PANE.THICKN"] = ex.Message; }
        try { result["native_justification"] = NativeRead(() => pane.GetAsString(DbAttributeInstance.SJUS)); }
        catch (Exception ex) { ThrowIfNativeFaulted(); errors["PANE.SJUS"] = ex.Message; }
        bool complete = true;
        bool? allValid = true;
        int totalVertices = 0;
        MemberPage<DbElement> paneMembers = PanelMembers(pane, PanelMemberLimit);
        PanelAddPageErrors(paneMembers, "PANE members", errors);
        if (paneMembers.HasMore != false) complete = false;
        foreach (DbElement member in paneMembers.Items)
        {
            ThrowIfNativeFaulted();
            if (!string.Equals(NativeRead(() => NativeRead(() => member.GetElementType()).ShortName), "PLOO", StringComparison.OrdinalIgnoreCase))
            {
                if (unsupported.Count < 32) unsupported.Add(new Dictionary<string, object>
                {
                    ["code"] = "additional_panel_member", ["element"] = ModelIdentity(member),
                    ["message"] = "This member's geometry is outside PLOO polygon validation."
                });
                continue;
            }
            if (loops.Count == PanelLoopLimit)
            {
                complete = false;
                errors["loop_limit"] = "Panel has more than " + PanelLoopLimit + " loops; remaining loops were not read.";
                break;
            }
            Dictionary<string, object> loopResult = PanelReadLoop(member, pane, loops.Count, ref totalVertices, errors);
            loops.Add(loopResult);
            if (!(bool)loopResult["complete"]) complete = false;
            object valid = loopResult["polygon_valid"];
            if (Equals(valid, false)) allValid = false;
            else if (valid == null && allValid == true) allValid = null;
            if (totalVertices == PanelPolygon.InspectVertexLimit && !(bool)loopResult["complete"])
                break;
        }
        if (loops.Count == 0 && complete) allValid = false;
        result["loop_count"] = loops.Count;
        result["vertex_count"] = totalVertices;
        result["complete"] = complete && errors.Count == 0;
        result["partial"] = !Equals(result["complete"], true);
        result["topology_valid"] = loops.Count > 1 || !complete || errors.Count > 0 || unsupported.Count > 0 ? null : (object)allValid;
        result["success"] = Equals(result["complete"], true);
        PanelAddRevision(pane, result);
        return result;
    }

    private static Dictionary<string, object> PanelReadLoop(DbElement loop, DbElement pane, int index,
        ref int totalVertices, Dictionary<string, string> allErrors)
    {
        ThrowIfNativeFaulted();
        string loopRef = ModelReference(NativeRead(() => loop.Ref));
        var errors = new Dictionary<string, string>();
        var features = new List<object>();
        var vertices = new List<object>();
        var xy = new List<double[]>();
        var z = new List<double>();
        var result = new Dictionary<string, object>
        {
            ["element"] = ModelIdentity(loop), ["loop_index"] = index,
            ["role"] = "not inferred", ["vertices"] = vertices, ["complete"] = true,
            ["polygon_valid"] = null, ["polygon"] = null,
            ["unsupported_features"] = features, ["read_errors"] = errors
        };
        try { result["thickness_mm"] = ModelFinite(NativeRead(() => loop.GetDouble(DbAttributeInstance.HEIG))); }
        catch (Exception ex) { ThrowIfNativeFaulted(); errors["HEIG"] = ex.Message; }
        try { result["native_justification"] = NativeRead(() => loop.GetAsString(DbAttributeInstance.SJUS)); }
        catch (Exception ex) { ThrowIfNativeFaulted(); errors["SJUS"] = ex.Message; }
        try
        {
            bool blind = NativeRead(() => loop.GetBool(DbAttributeInstance.LBLIND));
            result["blind"] = blind;
            if (blind) features.Add(new Dictionary<string, object> { ["code"] = "blind_loop", ["message"] = "LBLIND is true; blind-hole geometry is outside simple polygon validation." });
        }
        catch (Exception ex) { ThrowIfNativeFaulted(); errors["LBLIND"] = ex.Message; }
        MemberPage<DbElement> members = PanelMembers(loop, Math.Max(1, PanelPolygon.InspectVertexLimit - totalVertices));
        PanelAddPageErrors(members, "PLOO members", errors);
        bool complete = members.HasMore == false;
        foreach (DbElement vertex in members.Items)
        {
            ThrowIfNativeFaulted();
            if (!string.Equals(NativeRead(() => NativeRead(() => vertex.GetElementType()).ShortName), "PAVE", StringComparison.OrdinalIgnoreCase))
            {
                features.Add(new Dictionary<string, object> { ["code"] = "unexpected_loop_member", ["element"] = ModelIdentity(vertex) });
                continue;
            }
            if (totalVertices == PanelPolygon.InspectVertexLimit) { complete = false; break; }
            totalVertices++;
            var item = new Dictionary<string, object> { ["refno"] = ModelReference(NativeRead(() => vertex.Ref)), ["vertex_index"] = vertices.Count, ["position"] = null };
            vertices.Add(item);
            try
            {
                Position actual = NativeRead(() => vertex.GetPosition(DbAttributeInstance.POS, new DbQualifier { wrtQualifier = pane }));
                if (actual == null) throw new InvalidOperationException("SDK returned no PAVE POS.");
                double[] point = FiniteVector(NativeRead(() => actual.X), NativeRead(() => actual.Y), NativeRead(() => actual.Z));
                item["position"] = point;
                xy.Add(new[] { point[0], point[1] });
                z.Add(point[2]);
            }
            catch (Exception ex) { ThrowIfNativeFaulted(); errors["POS:" + item["refno"]] = ex.Message; }
            try
            {
                double radius = ModelFinite(NativeRead(() => vertex.GetDouble(DbAttributeInstance.FRAD)));
                item["fillet_radius_mm"] = radius;
                if (radius != 0) features.Add(new Dictionary<string, object> { ["code"] = "fillet_radius", ["vertex_index"] = item["vertex_index"], ["radius_mm"] = radius });
            }
            catch (Exception ex) { ThrowIfNativeFaulted(); errors["FRAD:" + item["refno"]] = ex.Message; }
            // Native diagnostics are retained without interpreting connection/beta attributes as arc definitions.
            foreach (DbAttribute attribute in new[] { DbAttributeInstance.CTYA, DbAttributeInstance.GTYP, DbAttributeInstance.BANG })
            {
                ThrowIfNativeFaulted();
                try
                {
                    string value = NativeRead(() => vertex.GetAsString(attribute));
                    item[NativeRead(() => attribute.ShortName)] = value;
                    if (attribute == DbAttributeInstance.GTYP && !PanelUnsetWord(value))
                        features.Add(new Dictionary<string, object> { ["code"] = "vertex_gtyp", ["vertex_index"] = item["vertex_index"], ["value"] = value });
                }
                catch (Exception ex) { ThrowIfNativeFaulted(); errors[NativeRead(() => attribute.ShortName) + ":" + item["refno"]] = ex.Message; }
            }
        }
        if (z.Count > 0)
        {
            double minZ = z.Min(), maxZ = z.Max();
            // A large absolute coordinate must not widen the planarity tolerance.
            bool parallel = maxZ - minZ <= PanelPolygon.CoordinateTolerance;
            bool coplanar = Math.Max(Math.Abs(minZ), Math.Abs(maxZ)) <= PanelPolygon.CoordinateTolerance;
            result["z_range_mm"] = new[] { minZ, maxZ };
            result["planar_parallel_xy"] = parallel;
            result["coplanar_in_local_xy"] = coplanar;
            if (!parallel) features.Add(new Dictionary<string, object> { ["code"] = "nonplanar_vertices", ["message"] = "PAVE local Z coordinates do not lie in one XY plane." });
            else if (!coplanar) features.Add(new Dictionary<string, object> { ["code"] = "offset_plane", ["message"] = "PAVE vertices lie on a plane offset from PANE local Z = 0; only the XY projection was checked." });
        }
        PanelPolygonValidation polygon = PanelPolygon.Validate(xy.ToArray(), false, PanelPolygon.InspectVertexLimit);
        if (complete && errors.Count == 0 && features.Count == 0)
        {
            result["polygon"] = polygon;
            result["polygon_valid"] = polygon.valid;
        }
        else result["xy_projection_diagnostic"] = polygon;
        result["vertex_count"] = vertices.Count;
        result["complete"] = complete && errors.Count == 0;
        foreach (var error in errors) allErrors[loopRef + ":" + error.Key] = error.Value;
        return result;
    }

    private static DbElementType PanelRequireType(string name)
    {
        DbElementType type = NativeRead(() => DbElementType.GetElementType(name));
        if (type == null || !NativeRead(() => type.IsValid) || !NativeRead(() => type.DatabaseTypes().Contains((int)DbType.Design)))
            throw new InvalidOperationException("Required Design type is unavailable: " + name);
        return type;
    }

    private sealed class PanelCreatedNode
    {
        internal int[] Reference;
        internal string Refno, Type, Owner;
        internal readonly List<string> Children = new List<string>();
    }

    private sealed class PanelCreationJournal
    {
        internal readonly string Parent;
        internal readonly string[] OriginalMembers;
        internal readonly List<PanelCreatedNode> Nodes = new List<PanelCreatedNode>();
        internal readonly List<object> Receipt;
        internal bool CreationAttempted, UnverifiedCreate;
        internal string UnverifiedReturnedRefno;
        internal PanelCreationJournal(string parent, string[] members, List<object> receipt)
        { Parent = parent; OriginalMembers = (string[])members.Clone(); Receipt = receipt; }
    }

    private static DbElement PanelCreateTracked(DbElement parent, DbElementType type,
        PanelCreationJournal journal, Dictionary<string, object> result)
    {
        ThrowIfNativeFaulted();
        if (journal == null || journal.UnverifiedCreate)
            throw new InvalidOperationException("An unresolved panel creation prevents any further CreateLast or property writes.");
        if (journal.Nodes.Count >= PanelPolygon.CreateVertexLimit + 2)
            throw new InvalidOperationException("Panel creation journal reached its bounded node limit.");
        // Validate the entire registered tree before each creation, not only the
        // immediate parent. Existing/foreign children must never become our nodes.
        PanelVerifyCreationJournal(journal);
        string parentRef = ModelReference(NativeRead(() => parent.Ref));
        PanelCreatedNode owner = journal.Nodes.SingleOrDefault(node => node.Refno == parentRef);
        if (owner == null && (journal.Nodes.Count != 0 || parentRef != journal.Parent))
            throw new InvalidOperationException("Creation parent is not the captured parent or a registered panel node.");
        if (!NativeRead(() => parent.IsCreatable(type)))
            throw new InvalidOperationException("The explicit panel parent cannot create the requested member type now.");
        string typeName = NativeRead(() => type.ShortName);
        string[] previous = owner == null ? journal.OriginalMembers : owner.Children.ToArray();
        // A call that throws or returns no usable reference may still have created
        // nodes. Keep the flag set until every identity/membership check finishes.
        journal.CreationAttempted = true;
        journal.UnverifiedCreate = true;
        journal.UnverifiedReturnedRefno = null;
        result["creation_attempted"] = true;
        result["retry_safe"] = false;
        DbElement returned = NativeRead(() => parent.CreateLast(type));
        if (!IsUsable(returned))
            throw new InvalidOperationException("CreateLast returned no usable identifiable panel node; cascading cleanup is not authorized.");
        int[] reference = NativeRead(() => returned.Ref);
        string refno = ModelReference(reference);
        journal.UnverifiedReturnedRefno = refno;
        result["unverified_created_refno"] = refno;
        if (refno == journal.Parent || journal.OriginalMembers.Contains(refno, StringComparer.Ordinal) ||
            journal.Nodes.Any(node => node.Refno == refno) || previous.Contains(refno, StringComparer.Ordinal))
            throw new InvalidOperationException("CreateLast returned an existing or already registered reference; no attributes were written to it.");
        DbElement exact = NativeRead(() => DbElement.GetElement(reference));
        if (!IsUsable(exact) || ModelReference(NativeRead(() => exact.Ref)) != refno ||
            NativeRead(() => returned.DbType) != DbType.Design || NativeRead(() => exact.DbType) != DbType.Design ||
            NativeRead(() => returned.GetElementType()) != type || NativeRead(() => exact.GetElementType()) != type)
            throw new InvalidOperationException("The returned node did not resolve as the exact requested new Design type.");
        foreach (DbElement candidate in new[] { returned, exact })
        {
            DbElement actualOwner = NativeRead(() => candidate.Owner);
            if (!IsUsable(actualOwner) || ModelReference(NativeRead(() => actualOwner.Ref)) != parentRef)
                throw new InvalidOperationException("The returned panel node's exact owner did not match its explicit creation parent.");
        }
        PanelRequireSequence(parent, previous.Concat(new[] { refno }).ToArray(), "tracked creation parent append");
        PanelRequireSequence(exact, new string[0], "new panel node must initially have no children");
        var recorded = new PanelCreatedNode
        {
            Reference = (int[])reference.Clone(), Refno = refno, Type = typeName, Owner = parentRef
        };
        journal.Nodes.Add(recorded);
        if (owner != null) owner.Children.Add(refno);
        journal.Receipt.Add(new Dictionary<string, object>
        {
            ["refno"] = refno, ["type"] = typeName, ["owner_refno"] = parentRef,
            ["children"] = recorded.Children, ["creation_ordinal"] = journal.Nodes.Count - 1
        });
        journal.UnverifiedCreate = false;
        journal.UnverifiedReturnedRefno = null;
        result["unverified_created_refno"] = null;
        // Reverify all branches after registration, before callers write attributes.
        PanelVerifyCreationJournal(journal);
        return exact;
    }

    private static void PanelVerifyCreationJournal(PanelCreationJournal journal)
    {
        ThrowIfNativeFaulted();
        if (journal == null || journal.UnverifiedCreate || journal.Nodes.Count > PanelPolygon.CreateVertexLimit + 2)
            throw new InvalidOperationException("Panel creation provenance is incomplete; no cascading cleanup or further creation is permitted.");
        DbElement parent = ModelRequireElement(journal.Parent);
        if (ModelReference(NativeRead(() => parent.Ref)) != journal.Parent)
            throw new InvalidOperationException("The captured panel parent no longer resolves exactly.");
        string[] expectedParent = journal.Nodes.Count == 0 ? journal.OriginalMembers :
            journal.OriginalMembers.Concat(new[] { journal.Nodes[0].Refno }).ToArray();
        PanelRequireSequence(parent, expectedParent, "captured panel parent sequence");
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (PanelCreatedNode node in journal.Nodes)
        {
            ThrowIfNativeFaulted();
            if (!seen.Add(node.Refno) || node.Refno != ModelReference(node.Reference))
                throw new InvalidOperationException("Panel journal has a duplicate or inconsistent identity.");
            DbElement exact = NativeRead(() => DbElement.GetElement(node.Reference));
            if (!IsUsable(exact) || ModelReference(NativeRead(() => exact.Ref)) != node.Refno ||
                NativeRead(() => exact.DbType) != DbType.Design)
                throw new InvalidOperationException("A registered panel node no longer resolves to the exact usable Design reference.");
            DbElementType actualType = NativeRead(() => exact.GetElementType());
            if (actualType == null || !string.Equals(NativeRead(() => actualType.ShortName), node.Type, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("A registered panel node changed type.");
            DbElement actualOwner = NativeRead(() => exact.Owner);
            if (!IsUsable(actualOwner) || ModelReference(NativeRead(() => actualOwner.Ref)) != node.Owner)
                throw new InvalidOperationException("A registered panel node changed owner.");
            if (node == journal.Nodes[0])
            {
                if (node.Owner != journal.Parent || !string.Equals(node.Type, "PANE", StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("The panel journal root is not the captured new PANE.");
            }
            else
            {
                PanelCreatedNode owner = journal.Nodes.SingleOrDefault(value => value.Refno == node.Owner);
                if (owner == null || !seen.Contains(owner.Refno) || !owner.Children.Contains(node.Refno))
                    throw new InvalidOperationException("Panel journal contains an unconnected or unordered node.");
            }
            // Reading every direct member list also proves that recorded leaves
            // remain empty; any unregistered descendant is rejected at its owner.
            PanelRequireSequence(exact, node.Children.ToArray(), "registered " + node.Type + " child sequence");
        }
    }

    private static MemberPage<DbElement> PanelMembers(DbElement parent, int limit)
    {
        ThrowIfNativeFaulted();
        MemberPage<DbElement> page = MemberPage<DbElement>.Read(DirectSiblings(parent, null), limit,
            item => ModelReference(NativeRead(() => item.Ref)));
        ThrowIfNativeFaulted();
        return page;
    }

    private static void PanelRequireComplete(MemberPage<DbElement> page, string context)
    {
        ThrowIfNativeFaulted();
        if (page.HasMore != false || page.ReadErrors.Count > 0)
            throw new InvalidOperationException("Cannot verify " + context + " within the bounded membership read. " + string.Join(" ", page.ReadErrors));
    }

    private static void PanelRequireSequence(DbElement parent, string[] expected, string context)
    {
        MemberPage<DbElement> page = PanelMembers(parent, Math.Max(1, expected.Length));
        PanelRequireComplete(page, context);
        if (!page.Items.Select(item => ModelReference(NativeRead(() => item.Ref))).SequenceEqual(expected, StringComparer.Ordinal))
            throw new InvalidOperationException("Unexpected " + context + ".");
    }

    private static void PanelAddPageErrors(MemberPage<DbElement> page, string context, Dictionary<string, string> errors)
    {
        if (page.HasMore == true) errors[context + ":limit"] = "Membership exceeds the configured read limit.";
        if (page.HasMore == null) errors[context + ":incomplete"] = "Membership read was incomplete.";
        for (int index = 0; index < page.ReadErrors.Count; index++) errors[context + ":" + index] = page.ReadErrors[index];
    }

    private static void PanelWriteString(DbElement element, DbAttribute attribute, string value)
    {
        ThrowIfNativeFaulted();
        ModelRequireSettable(element, attribute);
        PdmsMessage message;
        ThrowIfNativeFaulted();
        bool success = element.SetAttribute(attribute, new DbQualifier(), value, out message);
        ThrowIfNativeFaulted();
        ModelRequireSetter(success, message, NativeRead(() => attribute.ShortName));
    }

    private static void PanelWriteDouble(DbElement element, DbAttribute attribute, double value)
    {
        ThrowIfNativeFaulted();
        ModelRequireSettable(element, attribute);
        PdmsMessage message;
        ThrowIfNativeFaulted();
        bool success = element.SetAttribute(attribute, new DbQualifier(), value, out message);
        ThrowIfNativeFaulted();
        ModelRequireSetter(success, message, NativeRead(() => attribute.ShortName));
    }

    private static void PanelWriteBool(DbElement element, DbAttribute attribute, bool value)
    {
        ThrowIfNativeFaulted();
        ModelRequireSettable(element, attribute);
        PdmsMessage message;
        ThrowIfNativeFaulted();
        bool success = element.SetAttribute(attribute, new DbQualifier(), value, out message);
        ThrowIfNativeFaulted();
        ModelRequireSetter(success, message, NativeRead(() => attribute.ShortName));
    }

    private static string PanelJustificationToken(string value)
    {
        if (value == "positive") return "BOTT";
        if (value == "centre") return "CENT";
        if (value == "negative") return "TOP";
        throw new ArgumentException("justification must be positive, centre, or negative.", "justification");
    }

    private static bool PanelJustificationMatches(string expected, string actual)
    {
        string token = (actual ?? "").Trim().ToUpperInvariant();
        return token == expected || expected == "BOTT" && token == "BOTTOM";
    }

    private static bool PanelUnsetWord(string value)
    {
        return string.IsNullOrWhiteSpace(value) || string.Equals(value.Trim(), "unset", StringComparison.OrdinalIgnoreCase);
    }

    private static bool PanelLocalPositionMatches(double[] expected, double[] actual)
    {
        // Outline offsets remain local coordinates, so a large submitted offset
        // must not expand the allowed geometric error by relative tolerance.
        return expected != null && actual != null && expected.Length == 3 && actual.Length == 3 &&
            expected.Zip(actual, (left, right) => Math.Abs(left - right) <= PanelPolygon.CoordinateTolerance).All(equal => equal);
    }

    private static void PanelCleanupCreated(Dictionary<string, object> result, DbElement pane, int[] reference,
        PanelCreationJournal journal)
    {
        ThrowIfNativeFaulted();
        try
        {
            result["creation_journal_complete"] = false;
            result["cleanup_tree_verified"] = false;
            result["cleanup_registered_nodes_verified_deleted"] = false;
            result["residual_registered_refnos"] = journal.Nodes.Select(node => node.Refno).ToArray();
            result["unverified_created_refno"] = journal.UnverifiedReturnedRefno;
            result["unverified_creation_attempt"] = journal.UnverifiedCreate;
            PanelVerifyCreationJournal(journal);
            if (journal.Nodes.Count == 0)
                throw new InvalidOperationException("No exact registered panel root is available for cleanup.");
            // Registration can finish before the final verification in
            // PanelCreateTracked throws. The caller then has no assigned root,
            // but a freshly reverified complete journal still proves its identity.
            PanelCreatedNode root = journal.Nodes[0];
            if (reference != null && ModelReference(reference) != root.Refno ||
                pane != null && ModelReference(NativeRead(() => pane.Ref)) != root.Refno)
                throw new InvalidOperationException("The caller's panel root disagrees with the verified journal.");
            if (reference == null) reference = (int[])root.Reference.Clone();
            if (pane == null) pane = NativeRead(() => DbElement.GetElement(reference));
            if (!IsUsable(pane) || ModelReference(NativeRead(() => pane.Ref)) != root.Refno)
                throw new InvalidOperationException("The registered panel root could not be resolved exactly for cleanup.");
            result["cleanup_tree_verified"] = true;
            result["creation_journal_complete"] = true;
            ModelCleanupCreated(result, pane, reference, journal.Parent, journal.OriginalMembers);
            if (Equals(result["cleanup_succeeded"], true))
            {
                foreach (PanelCreatedNode node in journal.Nodes)
                {
                    DbElement remaining = NativeRead(() => DbElement.GetElement(node.Reference));
                    if (remaining == null || ModelReference(NativeRead(() => remaining.Ref)) != node.Refno ||
                        NativeRead(() => remaining.IsNull) || NativeRead(() => remaining.IsValid) || !NativeRead(() => remaining.IsDeleted))
                        throw new InvalidOperationException("A registered panel descendant was not certified deleted after cleanup.");
                }
                result["residual_registered_refnos"] = new string[0];
                result["cleanup_registered_nodes_verified_deleted"] = true;
            }
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            result["creation_result_unknown"] = true;
            result["cleanup_succeeded"] = false;
            result["residual_refno"] = journal.Nodes.Count > 0 ? journal.Nodes[0].Refno : journal.UnverifiedReturnedRefno;
            result["cleanup_errors"] = new[] { ex.Message };
        }
    }
}
