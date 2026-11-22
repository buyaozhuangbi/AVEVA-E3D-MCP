using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Aveva.Core.Database;
using Aveva.Core.Geometry;
using Aveva.Core.Utilities.Messaging;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private readonly string panelRevisionSession = Guid.NewGuid().ToString("N");

    private sealed class PanelEditVertex
    {
        internal DbElement Element;
        internal string Reference;
        internal Position NativePosition;
        internal double[] LocalPosition;
        internal double Fillet;
        internal string Connection, GeometryType;
        internal double Beta;
    }

    private sealed class PanelEditState
    {
        internal DbElement Pane, Loop, Parent;
        internal string PaneReference, LoopReference, ParentReference;
        internal string[] ParentMembers;
        internal readonly List<PanelEditVertex> Vertices = new List<PanelEditVertex>();
        internal double Thickness;
        internal string Justification;
        internal string Frame, Revision;
        internal readonly List<string> Reasons = new List<string>();
        internal bool Eligible { get { return Reasons.Count == 0 && Revision != null; } }
    }

    private sealed class PanelUpdateJournal
    {
        internal readonly List<int> PositionAttempts = new List<int>();
        internal readonly List<PanelEditVertex> Added = new List<PanelEditVertex>();
        internal bool ThicknessAttempted, JustificationAttempted, UnknownAppend;
        internal bool HasWrites { get { return PositionAttempts.Count > 0 || Added.Count > 0 || ThicknessAttempted || JustificationAttempted || UnknownAppend; } }
    }

    public object UpdatePanel(string refno, string expectedRevision, double[][] vertices, double? thickness,
        string justification, bool allowVertexDeletion, bool dryRun)
    {
        string target = ModelEditInput.ExplicitReference(refno, "refno");
        string expected = expectedRevision == null ? null : expectedRevision.ToLowerInvariant();
        if (expected != null && (expected.Length != 64 || expected.Any(value => !(value >= '0' && value <= '9' || value >= 'a' && value <= 'f'))))
            throw new ArgumentException("expected_revision must contain exactly 64 hexadecimal characters.", "expectedRevision");
        if (!dryRun && expected == null)
            throw new ArgumentException("Execution requires expected_revision from a current e3d_panel_geometry result.", "expectedRevision");
        double[][] outline = vertices == null ? null : vertices.Select(item => item == null ? null : (double[])item.Clone()).ToArray();
        PanelPolygonValidation polygon = PanelPolygon.Validate(outline, true, PanelPolygon.CreateVertexLimit);
        if (thickness.HasValue && (double.IsNaN(thickness.Value) || double.IsInfinity(thickness.Value) || thickness.Value <= 0))
            throw new ArgumentException("thickness must be a finite positive number in mm.", "thickness");
        string nativeJustification = justification == null ? null : PanelJustificationToken(justification.Trim().ToLowerInvariant());
        string command = "database-update-panel " + target;
        if (dryRun ? IsCommandActive() : !TryBeginCommand(command)) return BusyResult(command);
        try
        {
            return OnUi(delegate
            {
                var result = ModelEditResult(dryRun);
                result["refno"] = target;
                result["write_attempted"] = false;
                result["write_applied"] = false;
                result["rollback_attempted"] = false;
                result["rollback_succeeded"] = null;
                result["deletion_stage_started"] = false;
                result["outcome_uncertain"] = false;
                result["retry_safe"] = true;
                result["partial"] = false;
                result["polygon"] = polygon;
                result["planned_delete_refnos"] = new string[0];
                result["deleted_vertex_refnos"] = new List<string>();
                result["appended_vertex_refnos"] = new List<string>();
                result["validation_scope"] = "One unlinked leaf-vertex PLOO on PANE local Z=0. PANE/PLOO and retained PAVE identities and PANE frame are preserved. Existing prefix vertices are mapped by index; growth appends PAVE, shrink removes only the explicit trailing vertices. Operations are not a database transaction. Before any deletion, failure attempts exact setter restoration and exact new-vertex cleanup; existing deleted references are never recreated.";
                PanelEditState original = null;
                var journal = new PanelUpdateJournal();
                var deleted = (List<string>)result["deleted_vertex_refnos"];
                var appended = (List<string>)result["appended_vertex_refnos"];
                var deletionAttempts = new List<string>();
                result["deletion_attempted_refnos"] = deletionAttempts;
                try
                {
                    result["ce_before"] = ModelCurrentReference();
                    if (!polygon.valid)
                        throw new ArgumentException("vertices must describe one simple nondegenerate counterclockwise outline; inspect polygon.issues.");
                    DbElement pane = ModelRequireElement(target);
                    original = PanelReadEditState(pane);
                    result["editable"] = original.Eligible;
                    result["edit_reasons"] = original.Reasons;
                    result["original_revision"] = original.Revision;
                    result["revision"] = original.Revision;
                    if (!original.Eligible)
                        throw new InvalidOperationException("The current panel is outside supported edit state; inspect edit_reasons.");
                    int retained = Math.Min(outline.Length, original.Vertices.Count);
                    string[] removedRefs = original.Vertices.Skip(outline.Length).Select(item => item.Reference).ToArray();
                    result["refno"] = original.PaneReference;
                    result["loop_refno"] = original.LoopReference;
                    result["preserved_vertex_refnos"] = original.Vertices.Take(retained).Select(item => item.Reference).ToArray();
                    result["planned_delete_refnos"] = removedRefs;
                    if (removedRefs.Contains((string)result["ce_before"], StringComparer.Ordinal))
                        throw new InvalidOperationException("A planned trailing PAVE is the current element. Select an element that will be retained before reducing this panel's vertex count.");
                    result["append_count"] = Math.Max(0, outline.Length - original.Vertices.Count);
                    result["deletion_requires_opt_in"] = removedRefs.Length > 0;
                    result["allow_vertex_deletion"] = allowVertexDeletion;
                    result["external_reference_check"] = "Incoming references outside this leaf hierarchy are not enumerated. Explicit vertex deletion can invalidate references to the reported trailing PAVE elements.";
                    result["submitted"] = new Dictionary<string, object>
                    {
                        ["vertices"] = outline, ["thickness_mm"] = thickness, ["justification"] = justification,
                        ["vertex_coordinate_frame"] = original.PaneReference + " local XY; Z=0 mm"
                    };
                    double wantedThickness = thickness ?? original.Thickness;
                    string wantedJustification = nativeJustification ?? original.Justification;
                    var wantedPoints = outline.Select(point => new[] { point[0], point[1], 0.0 }).ToArray();
                    Position[] wantedNative = wantedPoints.Select(ModelPosition).ToArray();
                    for (int index = 0; index < retained; index++)
                    {
                        ThrowIfNativeFaulted();
                        ModelRequireSettable(original.Vertices[index].Element, DbAttributeInstance.POS);
                        ModelRequireStorageFrame(original.Vertices[index].Element, pane,
                            new ModelEditInput(wantedPoints[index], null, null, "OWNER", true));
                    }
                    if (thickness.HasValue) ModelRequireSettable(original.Loop, DbAttributeInstance.HEIG);
                    if (nativeJustification != null) ModelRequireSettable(original.Loop, DbAttributeInstance.SJUS);
                    DbElementType vertexType = PanelRequireType("PAVE");
                    if (outline.Length > original.Vertices.Count && !original.Loop.IsCreatable(vertexType))
                        throw new InvalidOperationException("The current PLOO cannot append PAVE vertices now.");
                    foreach (PanelEditVertex vertex in original.Vertices.Skip(outline.Length))
                        if (!vertex.Element.IsDeleteable || !vertex.Element.IsHierarchyDeleteable)
                            throw new InvalidOperationException("A planned trailing PAVE is not deletable now: " + vertex.Reference);
                    if (expected != null && expected != original.Revision)
                    {
                        result["revision_conflict"] = true;
                        throw new InvalidOperationException("expected_revision does not match the current panel. Inspect it again before editing.");
                    }
                    if (dryRun)
                    {
                        result["geometry"] = PanelReadGeometry(pane);
                        result["ce_after"] = ModelCurrentReference();
                        ModelCeUnchanged(result);
                        if (!Equals(result["ce_unchanged"], true)) throw new InvalidOperationException("Current element changed during panel preview.");
                        result["success"] = true;
                        return result;
                    }
                    if (removedRefs.Length > 0 && !allowVertexDeletion)
                        throw new InvalidOperationException("Reducing the vertex count requires allow_vertex_deletion=true after reviewing planned_delete_refnos.");
                    // Re-read all admission fields and both coordinate frames after
                    // preflight, under the same UI callback, before any setter.
                    PanelEditState immediatelyBefore = PanelReadEditState(pane);
                    if (!immediatelyBefore.Eligible || immediatelyBefore.Revision != expected || immediatelyBefore.Revision != original.Revision)
                    {
                        result["revision_conflict"] = true;
                        throw new InvalidOperationException("The panel changed during preflight; no write was attempted.");
                    }
                    var deleteSteps = new List<Action>();
                    foreach (PanelEditVertex vertex in original.Vertices.Skip(outline.Length).Reverse())
                    {
                        ThrowIfNativeFaulted();
                        PanelEditVertex exact = vertex;
                        deleteSteps.Add(delegate
                        {
                            result["write_attempted"] = true;
                            result["retry_safe"] = false;
                            deletionAttempts.Add(exact.Reference);
                            PanelDeleteExistingVertex(exact, original, deleted);
                        });
                    }
                    PanelUpdateRunResult run = PanelUpdateRunner.Run(
                        delegate
                        {
                            for (int index = 0; index < retained; index++)
                            {
                                ThrowIfNativeFaulted();
                                if (PanelLocalPositionMatches(original.Vertices[index].LocalPosition, wantedPoints[index])) continue;
                                journal.PositionAttempts.Add(index);
                                result["write_attempted"] = true; result["retry_safe"] = false;
                                ModelWritePosition(original.Vertices[index].Element, wantedNative[index]);
                            }
                            if (thickness.HasValue && Math.Abs(wantedThickness - original.Thickness) > PanelPolygon.CoordinateTolerance)
                            {
                                journal.ThicknessAttempted = true;
                                result["write_attempted"] = true; result["retry_safe"] = false;
                                PanelWriteDouble(original.Loop, DbAttributeInstance.HEIG, wantedThickness);
                            }
                            if (nativeJustification != null && !PanelJustificationMatches(nativeJustification, original.Justification))
                            {
                                journal.JustificationAttempted = true;
                                result["write_attempted"] = true; result["retry_safe"] = false;
                                PanelWriteString(original.Loop, DbAttributeInstance.SJUS, nativeJustification);
                            }
                            for (int index = original.Vertices.Count; index < outline.Length; index++)
                            {
                                ThrowIfNativeFaulted();
                                result["write_attempted"] = true; result["retry_safe"] = false;
                                journal.UnknownAppend = true;
                                DbElement added = NativeRead(() => original.Loop.CreateLast(vertexType));
                                if (!IsUsable(added)) throw new InvalidOperationException("PAVE append did not return an identifiable usable element; inspect the loop before retrying.");
                                string addedRef = ModelReference(added.Ref);
                                if (original.Vertices.Any(item => item.Reference == addedRef) || journal.Added.Any(item => item.Reference == addedRef) ||
                                    added.GetElementType() != vertexType || ModelReference(added.Owner.Ref) != original.LoopReference)
                                    throw new InvalidOperationException("PAVE append returned an unexpected reference, type or owner; it was not made eligible for cleanup.");
                                var saved = new PanelEditVertex { Element = added, Reference = addedRef };
                                journal.Added.Add(saved); appended.Add(addedRef); journal.UnknownAppend = false;
                                ModelRequireStorageFrame(added, pane, new ModelEditInput(wantedPoints[index], null, null, "OWNER", true));
                                ModelWritePosition(added, wantedNative[index]);
                                PanelWriteDouble(added, DbAttributeInstance.FRAD, 0);
                            }
                        },
                        delegate
                        {
                            string[] reversibleRefs = original.Vertices.Select(item => item.Reference).Concat(appended).ToArray();
                            double[][] reversiblePoints = wantedPoints.Take(retained).Concat(original.Vertices.Skip(retained).Select(item => item.LocalPosition))
                                .Concat(wantedPoints.Skip(original.Vertices.Count)).ToArray();
                            PanelVerifyUpdatedState(original, reversibleRefs, reversiblePoints, wantedThickness, wantedJustification);
                        }, deleteSteps,
                        delegate
                        {
                            string[] finalRefs = original.Vertices.Take(retained).Select(item => item.Reference).Concat(appended).ToArray();
                            PanelVerifyUpdatedState(original, finalRefs, wantedPoints, wantedThickness, wantedJustification);
                            Dictionary<string, object> geometry = PanelReadGeometry(pane);
                            result["geometry"] = geometry;
                            if (!Equals(geometry["complete"], true) || !Equals(geometry["topology_valid"], true) || !Equals(geometry["editable"], true))
                                throw new InvalidOperationException("The updated panel's complete supported geometry could not be verified.");
                            result["revision"] = geometry["revision"];
                            result["vertex_refnos"] = finalRefs;
                            result["ce_after"] = ModelCurrentReference(); ModelCeUnchanged(result);
                            if (!Equals(result["ce_unchanged"], true)) throw new InvalidOperationException("Current element changed during panel update.");
                        },
                        delegate { return PanelRollbackUpdate(original, journal, result); });
                    result["deletion_stage_started"] = run.deletion_stage_started;
                    result["rollback_attempted"] = run.rollback_attempted;
                    result["rollback_succeeded"] = run.rollback_succeeded;
                    if (run.success)
                    {
                        result["success"] = true;
                        result["readback_verified"] = true;
                        result["write_applied"] = journal.HasWrites || deletionAttempts.Count > 0;
                        result["retry_safe"] = false;
                    }
                    else
                    {
                        result.Remove("vertex_refnos");
                        result["error"] = run.error.Message;
                        result["readback_verified"] = false;
                        result["partial"] = run.deletion_stage_started || journal.UnknownAppend || run.rollback_succeeded != true;
                        result["write_applied"] = run.rollback_succeeded == true ? (object)false : null;
                        result["outcome_uncertain"] = run.deletion_stage_started || journal.UnknownAppend || run.rollback_succeeded != true;
                        result["retry_safe"] = run.rollback_succeeded == true && !journal.UnknownAppend;
                        if (run.rollback_error != null) result["rollback_error"] = run.rollback_error.Message;
                        PanelCaptureUpdateResidual(original, deletionAttempts, result);
                    }
                }
                catch (Exception ex)
                {
                    ThrowIfNativeFaulted();
                    result["error"] = ex.Message;
                    result["readback_verified"] = dryRun ? (object)null : false;
                    if (original != null && (journal.HasWrites || deletionAttempts.Count > 0))
                    {
                        // Mutations are normally contained by the phase runner.
                        // Reaching here must never be reported as an unwritten preflight failure.
                        result["outcome_uncertain"] = true; result["retry_safe"] = false; result["partial"] = true;
                        PanelCaptureUpdateResidual(original, deletionAttempts, result);
                    }
                }
                ModelCaptureCeAfter(result);
                return result;
            });
        }
        finally { if (!dryRun) EndCommand(); }
    }

    private void PanelVerifyUpdatedState(PanelEditState original, string[] references, double[][] positions,
        double thickness, string justification)
    {
        ThrowIfNativeFaulted();
        PanelEditState actual = PanelReadEditState(original.Pane);
        if (!actual.Eligible) throw new InvalidOperationException("Panel state became unsupported during update: " + string.Join(" ", actual.Reasons));
        if (actual.PaneReference != original.PaneReference || actual.LoopReference != original.LoopReference || actual.Frame != original.Frame ||
            !actual.ParentMembers.SequenceEqual(original.ParentMembers, StringComparer.Ordinal) ||
            !actual.Vertices.Select(item => item.Reference).SequenceEqual(references, StringComparer.Ordinal))
            throw new InvalidOperationException("PANE frame/identity, PLOO identity, or expected membership changed during update.");
        if (positions.Length != actual.Vertices.Count || positions.Where((point, index) => !PanelLocalPositionMatches(point, actual.Vertices[index].LocalPosition)).Any())
            throw new InvalidOperationException("PAVE positions did not match the requested local geometry.");
        if (Math.Abs(actual.Thickness - thickness) > PanelPolygon.CoordinateTolerance ||
            !(string.Equals(actual.Justification, justification, StringComparison.OrdinalIgnoreCase) || PanelJustificationMatches(justification, actual.Justification)))
            throw new InvalidOperationException("Loop thickness or justification readback did not match.");
    }

    private static void PanelDeleteExistingVertex(PanelEditVertex vertex, PanelEditState original, List<string> deleted)
    {
        ThrowIfNativeFaulted();
        DbElement exact = ModelRequireElement(vertex.Reference);
        if (ModelReference(exact.Owner.Ref) != original.LoopReference || exact.GetElementType().ShortName != "PAVE")
            throw new InvalidOperationException("A trailing vertex no longer has its captured type and owner.");
        MemberPage<DbElement> children = PanelMembers(exact, 1);
        PanelRequireComplete(children, "trailing PAVE children");
        if (children.Items.Count != 0) throw new InvalidOperationException("A trailing PAVE acquired child elements; deletion was not attempted.");
        PdmsMessage message;
        ThrowIfNativeFaulted();
        bool returned = exact.Delete(out message);
        ThrowIfNativeFaulted();
        bool absent = PanelVertexAbsent(vertex.Reference, original.Loop);
        if (absent) deleted.Add(vertex.Reference);
        if (!returned || message != null || !absent)
            throw new InvalidOperationException("Trailing PAVE deletion returned " + returned + "; absence verified=" + absent + ". " + (message == null ? "" : message.MessageText()));
    }

    private static bool PanelVertexAbsent(string reference, DbElement loop)
    {
        ThrowIfNativeFaulted();
        // ResolveElement deliberately discards invalid wrappers, including
        // deleted tombstones. Use the captured numeric reference directly.
        DbElement exact = PanelReferenceElement(reference);
        bool deleted = exact != null && !NativeRead(() => exact.IsNull) && !NativeRead(() => exact.IsValid) && NativeRead(() => exact.IsDeleted);
        if (!IsUsable(loop)) throw new InvalidOperationException("The original PLOO is unreadable during absence verification.");
        MemberPage<DbElement> members = PanelMembers(loop, PanelPolygon.CreateVertexLimit);
        PanelRequireComplete(members, "PLOO membership after vertex deletion");
        return deleted && !members.Items.Any(item => ModelReference(item.Ref) == reference);
    }

    private static DbElement PanelReferenceElement(string reference)
    {
        ThrowIfNativeFaulted();
        string[] pair = reference.Substring(1).Split('/');
        return NativeRead(() => DbElement.GetElement(new[] { int.Parse(pair[0], CultureInfo.InvariantCulture), int.Parse(pair[1], CultureInfo.InvariantCulture) }));
    }

    private bool PanelRollbackUpdate(PanelEditState original, PanelUpdateJournal journal, Dictionary<string, object> result)
    {
        ThrowIfNativeFaulted();
        var errors = new List<string>();
        foreach (PanelEditVertex added in journal.Added.AsEnumerable().Reverse())
        {
            ThrowIfNativeFaulted();
            try
            {
                if (PanelVertexAbsent(added.Reference, original.Loop)) continue;
                DbElement exact = ModelRequireElement(added.Reference);
                if (ModelReference(exact.Owner.Ref) != original.LoopReference || original.Vertices.Any(item => item.Reference == added.Reference) || exact.GetElementType().ShortName != "PAVE")
                    throw new InvalidOperationException("The exact newly appended PAVE could not be reverified.");
                PdmsMessage message;
                ThrowIfNativeFaulted();
                bool returned = exact.Delete(out message);
                ThrowIfNativeFaulted();
                bool absent = PanelVertexAbsent(added.Reference, original.Loop);
                if (!returned || message != null) errors.Add("Delete added PAVE returned " + returned + ". " + (message == null ? "" : message.MessageText()));
                if (!absent) errors.Add("The appended PAVE remains or its absence is unverified: " + added.Reference);
            }
            catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("Cleanup appended PAVE " + added.Reference + ": " + ex.Message); }
        }
        if (journal.JustificationAttempted)
        {
            try
            {
                if (PanelUnsetWord(original.Justification)) NativeWrite(() => original.Loop.SetAttributeDefault(DbAttributeInstance.SJUS));
                else PanelWriteString(original.Loop, DbAttributeInstance.SJUS, original.Justification);
            }
            catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("Restore SJUS: " + ex.Message); }
        }
        if (journal.ThicknessAttempted)
        {
            try { PanelWriteDouble(original.Loop, DbAttributeInstance.HEIG, original.Thickness); }
            catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("Restore HEIG: " + ex.Message); }
        }
        foreach (int index in journal.PositionAttempts.AsEnumerable().Reverse())
        {
            ThrowIfNativeFaulted();
            try { ModelWritePosition(original.Vertices[index].Element, original.Vertices[index].NativePosition); }
            catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("Restore PAVE " + original.Vertices[index].Reference + ": " + ex.Message); }
        }
        PanelEditState after = PanelReadEditState(original.Pane);
        bool verified = after.Eligible && after.Revision == original.Revision &&
            after.ParentMembers.SequenceEqual(original.ParentMembers, StringComparer.Ordinal);
        result["rollback_verified_original"] = verified;
        result["unknown_append_outcome"] = journal.UnknownAppend;
        if (errors.Count > 0) result["rollback_errors"] = errors;
        return verified && !journal.UnknownAppend && errors.Count == 0;
    }

    private void PanelCaptureUpdateResidual(PanelEditState original, IList<string> deletionAttempts, Dictionary<string, object> result)
    {
        ThrowIfNativeFaulted();
        try
        {
            Dictionary<string, object> geometry = PanelReadGeometry(original.Pane);
            result["geometry"] = geometry;
            result["revision"] = geometry["revision"];
            result["residual_complete"] = Equals(geometry["complete"], true);
        }
        catch (Exception ex) { ThrowIfNativeFaulted(); result["residual_read_error"] = ex.Message; result["revision"] = null; result["geometry"] = null; result["residual_complete"] = false; }
        var states = new List<object>();
        foreach (string reference in deletionAttempts)
        {
            ThrowIfNativeFaulted();
            try
            {
                bool absent = PanelVertexAbsent(reference, original.Loop);
                DbElement exact = PanelReferenceElement(reference);
                if (absent)
                {
                    var deleted = (List<string>)result["deleted_vertex_refnos"];
                    if (!deleted.Contains(reference, StringComparer.Ordinal)) deleted.Add(reference);
                }
                states.Add(new Dictionary<string, object> { ["refno"] = reference, ["state"] = absent ? "deleted" : IsUsable(exact) ? "present" : "unknown" });
            }
            catch (Exception ex) { ThrowIfNativeFaulted(); states.Add(new Dictionary<string, object> { ["refno"] = reference, ["state"] = "unknown", ["error"] = ex.Message }); }
        }
        result["deletion_outcomes"] = states;
    }

    private PanelEditState PanelReadEditState(DbElement pane)
    {
        ThrowIfNativeFaulted();
        var state = new PanelEditState { Pane = pane };
        try
        {
            if (!IsUsable(pane) || pane.DbType != DbType.Design || pane.GetElementType().ShortName != "PANE")
                throw new InvalidOperationException("The target must be a usable Design PANE.");
            state.PaneReference = ModelReference(pane.Ref);
            state.Parent = pane.Owner;
            if (!IsUsable(state.Parent)) throw new InvalidOperationException("The PANE owner is unreadable.");
            state.ParentReference = ModelReference(state.Parent.Ref);
            state.ParentMembers = ModelMemberReferences(state.Parent);
            if (!state.ParentMembers.Contains(state.PaneReference, StringComparer.Ordinal))
                throw new InvalidOperationException("The PANE is not present in its reported owner membership.");
            PanelRequireEmptyReference(pane, DbAttributeInstance.SPRE);
            PanelRequireEmptyReference(pane, DbAttributeInstance.ORRF);
            PanelRequireNoGeometryRules(pane, "POS", "ORI");
            MemberPage<DbElement> members = PanelMembers(pane, 2);
            PanelRequireComplete(members, "PANE edit membership");
            if (members.Items.Count != 1 || members.Items[0].GetElementType().ShortName != "PLOO")
                throw new InvalidOperationException("Editing requires exactly one PLOO and no other PANE members.");
            state.Loop = members.Items[0];
            state.LoopReference = ModelReference(state.Loop.Ref);
            PanelRequireEmptyReference(state.Loop, DbAttributeInstance.SPRE);
            PanelRequireEmptyReference(state.Loop, DbAttributeInstance.ORRF);
            PanelRequireNoGeometryRules(state.Loop, "HEIG", "SJUS", "LBLIND");
            state.Thickness = ModelFinite(state.Loop.GetDouble(DbAttributeInstance.HEIG));
            if (state.Thickness <= 0) throw new InvalidOperationException("The existing loop thickness must be positive.");
            state.Justification = state.Loop.GetAsString(DbAttributeInstance.SJUS).Trim();
            if (!PanelSupportedNativeJustification(state.Justification))
                throw new InvalidOperationException("The existing native SJUS value is outside supported panel justification values.");
            if (state.Loop.GetBool(DbAttributeInstance.LBLIND))
                throw new InvalidOperationException("Blind loops are outside panel editing.");
            MemberPage<DbElement> vertices = PanelMembers(state.Loop, PanelPolygon.CreateVertexLimit);
            PanelRequireComplete(vertices, "PLOO edit membership");
            if (vertices.Items.Count < 3)
                throw new InvalidOperationException("An editable loop must contain 3 to 256 PAVE vertices.");
            var qualifier = new DbQualifier { wrtQualifier = pane };
            foreach (DbElement vertex in vertices.Items)
            {
                ThrowIfNativeFaulted();
                if (vertex.GetElementType().ShortName != "PAVE")
                    throw new InvalidOperationException("Only PAVE members are supported in an editable loop.");
                MemberPage<DbElement> children = PanelMembers(vertex, 1);
                PanelRequireComplete(children, "PAVE child membership");
                if (children.Items.Count != 0)
                    throw new InvalidOperationException("PAVE members with attached loads, constraints or other children cannot be edited by this tool.");
                PanelRequireEmptyReference(vertex, DbAttributeInstance.MVER);
                PanelRequireEmptyReference(vertex, DbAttributeInstance.ORRF);
                PanelRequireEmptyReference(vertex, DbAttributeInstance.SPRE);
                PanelRequireNoGeometryRules(vertex, "POS", "FRAD", "CTYA", "GTYP", "BANG");
                DbElement[] links = vertex.GetElementArray(DbAttributeInstance.LNFA);
                if (links == null || links.Any(link => link != null && !link.IsNull))
                    throw new InvalidOperationException("Linked vertices or unreadable LNFA arrays cannot be edited by this tool.");
                Position native = vertex.GetPosition(DbAttributeInstance.POS);
                Position local = vertex.GetPosition(DbAttributeInstance.POS, qualifier);
                if (native == null || local == null) throw new InvalidOperationException("A PAVE position could not be read.");
                double[] nativeVector = FiniteVector(native.X, native.Y, native.Z);
                double[] localVector = FiniteVector(local.X, local.Y, local.Z);
                if (!PanelLocalPositionMatches(nativeVector, localVector))
                    throw new InvalidOperationException("A PAVE native position does not use its PANE coordinate frame.");
                if (Math.Abs(localVector[2]) > PanelPolygon.CoordinateTolerance)
                    throw new InvalidOperationException("PAVE positions outside local Z = 0 cannot be edited by this tool.");
                var item = new PanelEditVertex
                {
                    Element = vertex, Reference = ModelReference(vertex.Ref),
                    NativePosition = ModelPosition(nativeVector), LocalPosition = localVector,
                    Fillet = ModelFinite(vertex.GetDouble(DbAttributeInstance.FRAD)),
                    Connection = vertex.GetAsString(DbAttributeInstance.CTYA).Trim(),
                    GeometryType = vertex.GetAsString(DbAttributeInstance.GTYP).Trim(),
                    Beta = ModelFinite(vertex.GetDouble(DbAttributeInstance.BANG))
                };
                if (item.Fillet != 0 || !PanelUnsetWord(item.Connection) || !PanelUnsetWord(item.GeometryType) || item.Beta != 0)
                    throw new InvalidOperationException("Fillets, connection types, geometry types or nonzero beta angles cannot be edited by this tool.");
                state.Vertices.Add(item);
            }
            state.Frame = PanelFrameFingerprint(pane, state.ParentReference);
            state.Revision = PanelEditFingerprint(state);
        }
        catch (Exception ex) { ThrowIfNativeFaulted(); state.Reasons.Add(ex.Message); state.Revision = null; }
        return state;
    }

    private void PanelAddRevision(DbElement pane, Dictionary<string, object> geometry)
    {
        if (!Equals(geometry["complete"], true))
        {
            geometry["editable"] = false;
            geometry["revision"] = null;
            geometry["edit_reasons"] = new[] { "A complete geometry read is required before issuing an edit revision." };
            return;
        }
        PanelEditState state = PanelReadEditState(pane);
        geometry["editable"] = state.Eligible;
        geometry["revision"] = state.Revision;
        geometry["edit_reasons"] = state.Reasons;
        geometry["revision_scope"] = "Bridge context, project/MDB, PANE identity/owner/native and WORLD frame, ordered loop/vertex identities, native geometry and supported unlinked leaf-vertex state. Names are excluded.";
    }

    private static void PanelRequireEmptyReference(DbElement element, DbAttribute attribute)
    {
        ThrowIfNativeFaulted();
        DbElement linked = element.GetElement(attribute);
        if (linked == null) throw new InvalidOperationException(attribute.ShortName + " could not be read reliably.");
        if (!linked.IsNull)
            throw new InvalidOperationException(attribute.ShortName + " must be unset for panel editing: " + ModelReference(element.Ref));
    }

    private static bool PanelSupportedNativeJustification(string value)
    {
        return PanelUnsetWord(value) || new[] { "TOP", "UTOP", "DTOP", "CENT", "UCEN", "DCEN", "BOTT", "BOTTOM", "UBOT", "DBOT" }
            .Contains(value.Trim().ToUpperInvariant(), StringComparer.Ordinal);
    }

    private static void PanelRequireNoGeometryRules(DbElement element, params string[] fields)
    {
        ThrowIfNativeFaulted();
        DbAttribute[] rules = element.GetAttributesWithRules();
        if (rules == null) throw new InvalidOperationException("Geometry attribute rules could not be read reliably.");
        if (rules.Any(attribute => fields.Contains(attribute.ShortName, StringComparer.OrdinalIgnoreCase)))
            throw new InvalidOperationException("Rule-driven geometry attributes cannot be edited by this tool: " + ModelReference(element.Ref));
    }

    private string PanelFrameFingerprint(DbElement pane, string owner)
    {
        ThrowIfNativeFaulted();
        DbElement world = ModelFrame("WORLD", pane.Owner);
        var qualifier = new DbQualifier { wrtQualifier = world };
        return PanelHash(delegate(BinaryWriter writer)
        {
            writer.Write(owner);
            PanelWritePositionBytes(writer, pane.GetPosition(DbAttributeInstance.POS));
            PanelWriteOrientationBytes(writer, pane.GetOrientation(DbAttributeInstance.ORI));
            writer.Write(ModelReference(world.Ref));
            PanelWritePositionBytes(writer, pane.GetPosition(DbAttributeInstance.POS, qualifier));
            PanelWriteOrientationBytes(writer, pane.GetOrientation(DbAttributeInstance.ORI, qualifier));
        });
    }

    private string PanelEditFingerprint(PanelEditState state)
    {
        MDB mdb = CurrentMdb();
        Project project = Project.CurrentProject;
        if (mdb == null || project == null) throw new InvalidOperationException("Project/MDB context is unavailable.");
        return PanelHash(delegate(BinaryWriter writer)
        {
            writer.Write("panel-edit-v1"); writer.Write(panelRevisionSession);
            writer.Write(project.Name); writer.Write(mdb.Name);
            writer.Write(state.PaneReference); writer.Write(state.ParentReference); writer.Write(state.Frame);
            writer.Write(state.LoopReference); writer.Write(state.Thickness); writer.Write(state.Justification);
            writer.Write(false); // LBLIND was read and must be false.
            writer.Write(state.Vertices.Count);
            foreach (PanelEditVertex vertex in state.Vertices)
            {
                ThrowIfNativeFaulted();
                writer.Write(vertex.Reference);
                PanelWritePositionBytes(writer, vertex.NativePosition);
                foreach (double value in vertex.LocalPosition) writer.Write(value);
                writer.Write(vertex.Fillet); writer.Write(vertex.Connection); writer.Write(vertex.GeometryType); writer.Write(vertex.Beta);
                writer.Write(0); // No child members, master/origin/template refs or linked-vertex refs were admitted.
            }
        });
    }

    private static string PanelHash(Action<BinaryWriter> write)
    {
        using (var stream = new MemoryStream())
        {
            using (var writer = new BinaryWriter(stream, Encoding.UTF8, true)) write(writer);
            using (SHA256 sha = SHA256.Create())
                return string.Concat(sha.ComputeHash(stream.ToArray()).Select(value => value.ToString("x2", CultureInfo.InvariantCulture)));
        }
    }

    private static void PanelWritePositionBytes(BinaryWriter writer, Position position)
    {
        if (position == null) throw new InvalidOperationException("A complete PANE frame could not be read.");
        foreach (double value in FiniteVector(position.X, position.Y, position.Z)) writer.Write(value);
    }

    private static void PanelWriteOrientationBytes(BinaryWriter writer, Orientation orientation)
    {
        if (orientation == null) throw new InvalidOperationException("A complete PANE orientation could not be read.");
        foreach (Direction direction in new[] { orientation.XDir(), orientation.YDir(), orientation.ZDir() })
            foreach (double value in DirectionVector(direction)) writer.Write(value);
    }
}
