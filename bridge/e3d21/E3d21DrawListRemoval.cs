using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core3D.Graphics;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private readonly string drawListRemovalRevisionSession = Guid.NewGuid().ToString("N");

    private sealed class DrawListRemovalState
    {
        internal SelectionEditingState Scene;
        internal DbElement Element;
        internal string Reference, Type, Name, Owner;
        internal bool TopLevel, ExactMember;
        internal Dictionary<string, object> Camera;
    }

    public object RemoveDrawListMember(string refno, string expectedRevision, bool dryRun)
    {
        string requested = ModelEditInput.ExplicitReference(refno, "refno");
        string expected = expectedRevision == null ? null : expectedRevision.ToLowerInvariant();
        if (expected != null && (expected.Length != 64 || expected.Any(c => !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f'))))
            throw new ArgumentException("expected_revision must contain exactly 64 hexadecimal characters.", "expectedRevision");
        if (!dryRun && expected == null)
            throw new ArgumentException("Execution requires expected_revision from a current Drawlist removal preview.", "expectedRevision");
        var errors = new List<object>();
        var result = new Dictionary<string, object>
        {
            ["success"] = false, ["dry_run"] = dryRun, ["requested_refno"] = requested, ["refno"] = null,
            ["revision"] = null, ["revision_conflict"] = false, ["top_level_element"] = null,
            ["exact_member_before"] = null, ["exact_member_after"] = null, ["no_change"] = false,
            ["write_attempted"] = false, ["remove_attempted"] = false, ["native_remove_returned"] = false,
            ["refresh_attempted"] = false, ["graphics_updated"] = false, ["write_applied"] = false,
            ["removed"] = false, ["readback_verified"] = false, ["membership_verified"] = false,
            ["remaining_members_verified"] = false, ["remaining_primitives_verified"] = false,
            ["target_identity_verified"] = false, ["selection_unchanged"] = null, ["camera_unchanged"] = null,
            ["context_unchanged"] = null, ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
            ["outcome_uncertain"] = false, ["partial_write"] = false, ["retry_safe"] = false,
            ["rollback_attempted"] = false, ["residual_complete"] = null, ["errors"] = errors,
            ["before"] = null, ["after"] = null, ["native_database_fault"] = null,
            ["persistence"] = DrawListPersistenceResult(dryRun ? "preview" : "preflight_rejected"),
            ["scope"] = "Remove one exact native TOPF Drawlist member using DrawList.Remove(DbElement). No database deletion, primitive ancestor substitution, camera movement or selection edit is requested.",
            ["restrictions"] = "Overlapping displayed member ancestry, cross-owner target-subtree entries and selected ancestors/descendants are rejected before removal. Selection observations are bounded to 200 entries; owner-chain scope proofs share 40000 reads and depth 1024.",
            ["revision_scope"] = "Bridge context instance, literal requested reference, resolved target reference/type/name/owner/TOPF, project/MDB/view/Drawlist/CE, complete observed member/primitive maps, ordered native selection and native camera values.",
            ["database_verification_scope"] = "Target identity, type, name and direct database owner remain readable and unchanged; this is not a full database or geometry comparison.",
            ["pixel_visibility_verified"] = false,
            ["failure_policy"] = "No automatic retry, RemoveOnly, Add or rollback. Ordinary failures retain complete residual observations when readable; native faults stop further SDK access. A missing exact member does not prove invisible pixels."
        };
        bool acquired = false;
        string phase = "preflight";
        DrawListRemovalState before = null;
        DrawListRemovalState verifiedAfter = null;
        try
        {
            acquired = TryBeginCommand("remove-drawlist-member " + requested);
            if (!acquired) throw new InvalidOperationException("E3D is busy; no Drawlist removal was dispatched.");
            OnUi(delegate
            {
                try
                {
                    before = DrawListRemovalReadState(requested, true);
                    string revision = DrawListRemovalRevision(before, requested);
                    result["refno"] = before.Reference;
                    result["top_level_element"] = before.TopLevel;
                    result["exact_member_before"] = before.ExactMember;
                    result["no_change"] = !before.ExactMember;
                    result["ce_before"] = before.Scene.Ce;
                    result["revision"] = revision;
                    result["before"] = DrawListRemovalObservation(before);
                    if (expected != null && expected != revision)
                    {
                        result["revision_conflict"] = true;
                        throw new InvalidOperationException("expected_revision no longer matches this exact removal request or its complete observed context. No removal was attempted.");
                    }
                    if (dryRun)
                    {
                        result["ce_after"] = before.Scene.Ce; result["ce_unchanged"] = true;
                        result["context_unchanged"] = true; result["success"] = true;
                        return true;
                    }
                    phase = "revalidation";
                    DrawListRemovalState immediate = DrawListRemovalReadState(requested, true);
                    if (!SelectionEditingSameContext(before.Scene, immediate.Scene) || DrawListRemovalRevision(immediate, requested) != revision)
                    {
                        result["revision_conflict"] = true;
                        throw new InvalidOperationException("Display, target, camera, selection or context changed during preflight. No removal was attempted.");
                    }
                    before = immediate;
                    if (before.ExactMember)
                    {
                        phase = "remove";
                        result["write_attempted"] = true; result["remove_attempted"] = true;
                        result["persistence"] = DrawListPersistenceResult("display_failed");
                        NativeWrite(() => before.Scene.DrawList.Remove(before.Element));
                        result["native_remove_returned"] = true;
                        phase = "refresh";
                        result["refresh_attempted"] = true;
                        PrimitiveVisualRefresh();
                        result["graphics_updated"] = true;
                    }
                    phase = "readback";
                    DrawListRemovalState after = DrawListRemovalReadState(requested, false);
                    bool verified = DrawListRemovalVerify(before, after, result, true);
                    if (!verified) throw new InvalidOperationException("Complete readback did not confirm exact member removal with unchanged remaining display, target identity, camera, selection and CE.");
                    result["readback_verified"] = true;
                    result["write_applied"] = before.ExactMember;
                    result["removed"] = before.ExactMember;
                    result["success"] = true;
                    verifiedAfter = after;
                }
                catch (Exception error)
                {
                    ThrowIfNativeFaulted();
                    result["success"] = false;
                    errors.Add(new { phase, error = error.Message });
                    if (before != null && Equals(result["remove_attempted"], true))
                    {
                        // Observe an ordinary failure, never replay Remove or
                        // regenerate the deleted display member with Add.
                        try
                        {
                            DrawListRemovalState residual = DrawListRemovalReadState(requested, false);
                            DrawListRemovalVerify(before, residual, result, false);
                        }
                        catch (Exception residualError)
                        {
                            ThrowIfNativeFaulted();
                            result["residual_complete"] = false; result["outcome_uncertain"] = true;
                            result["partial_write"] = null; result["write_applied"] = null; result["removed"] = null;
                            errors.Add(new { phase = "residual", error = residualError.Message });
                        }
                    }
                }
                // Persistence is separate from a verified display operation.
                // A save error must not replay or undo removal. Persist this
                // verified observation, including an explicitly empty scene.
                if (Equals(result["success"], true) && Equals(result["remove_attempted"], true))
                {
                    phase = "persistence";
                    var document = new DrawListVisualDocument
                    {
                        schema_version = 2, project = verifiedAfter.Scene.Project, mdb = verifiedAfter.Scene.Mdb,
                        members = verifiedAfter.Scene.Display.Members.OrderBy(row => row.Key, StringComparer.Ordinal).Select(row => row.Value).ToList(),
                        primitive_entries = verifiedAfter.Scene.Display.Entries.OrderBy(row => row.Key, StringComparer.Ordinal).Select(row => row.Value).ToList()
                    };
                    result["persistence"] = PersistDrawListVisualDocument(DrawListPersistenceResult("snapshot_failed"), document, true);
                }
                else if (Equals(result["success"], true) && !dryRun)
                    result["persistence"] = DrawListPersistenceResult("no_change");
                return true;
            });
        }
        catch (Exception error)
        {
            // Managed-only bookkeeping. This includes failures raised by a
            // native fault barrier; do not attempt CE, residual or refresh reads.
            result["success"] = false; result["readback_verified"] = false;
            bool attempted = Equals(result["remove_attempted"], true);
            result["outcome_uncertain"] = attempted;
            if (attempted)
            {
                result["partial_write"] = null; result["write_applied"] = null; result["removed"] = null;
                result["residual_complete"] = false;
            }
            result["ce_after"] = null; result["ce_unchanged"] = null; result["context_unchanged"] = null;
            result["selection_unchanged"] = null; result["camera_unchanged"] = null;
            result["target_identity_verified"] = false;
            if (nativeFaultState != null && nativeFaultState.HasFault) result["native_database_fault"] = nativeFaultState.Snapshot();
            errors.Add(new { phase, error = error.Message });
        }
        finally { if (acquired) EndCommand(); }
        return result;
    }

    private static DrawListRemovalState DrawListRemovalReadState(string requested, bool preflight)
    {
        ThrowIfNativeFaulted();
        SelectionEditingState scene = SelectionEditingReadState();
        if (preflight) PrimitiveVisualValidateExpected(scene.Display.Entries);
        DbElement element = ModelRequireElement(requested);
        if (NativeRead(() => element.DbType) != DbType.Design) throw new InvalidOperationException("A Design database element is required.");
        DbElementType actual = NativeRead(() => element.GetActualType());
        if (actual == null || !NativeRead(() => actual.IsValid)) throw new InvalidOperationException("Native target type metadata is unavailable.");
        var state = new DrawListRemovalState
        {
            Scene = scene, Element = element, Reference = SelectionEditingElementReference(element),
            Type = NativeRead(() => actual.Name), Name = NativeRead(() => element.GetAsString(DbAttributeInstance.NAME)),
            Owner = SelectionEditingElementReference(NativeRead(() => element.Owner)),
            TopLevel = NativeRead(() => actual.GetBool(DbNounField.TOPF)), Camera = DrawListRemovalCamera(scene.View)
        };
        if (string.IsNullOrWhiteSpace(state.Type) || state.Name == null) throw new InvalidOperationException("Complete target type/name observations are required.");
        if (requested.StartsWith("=", StringComparison.Ordinal))
        {
            string[] pair = requested.Substring(1).Split('/');
            string canonical = "=" + int.Parse(pair[0], CultureInfo.InvariantCulture).ToString(CultureInfo.InvariantCulture) + "/" +
                int.Parse(pair[1], CultureInfo.InvariantCulture).ToString(CultureInfo.InvariantCulture);
            if (state.Reference != canonical) throw new InvalidOperationException("Native numeric lookup returned a different target reference; no ancestor or alias is substituted.");
        }
        state.ExactMember = scene.Display.Members.ContainsKey(state.Reference);
        if (preflight)
        {
            if (!state.TopLevel) throw new InvalidOperationException("Only an explicit native TOPF element can be removed. A primitive's resolved ancestor is never substituted.");
            if (state.ExactMember)
            {
                DrawListMember resolved = NativeRead(() => scene.DrawList.GetDrawListMember(element));
                if (resolved == null || SelectionEditingElementReference(NativeRead(() => resolved.DbElement)) != state.Reference)
                    throw new InvalidOperationException("Native member lookup did not resolve the exact requested TOPF member.");
                DrawListRemovalRequireIsolated(state);
            }
        }
        SelectionEditingState end = SelectionEditingReadContext();
        if (!SelectionEditingSameContext(scene, end) || scene.Ce != end.Ce)
            throw new InvalidOperationException("View, Drawlist, project/MDB or CE changed during removal observation.");
        return state;
    }

    private static Dictionary<string, object> DrawListRemovalCamera(View3D view)
    {
        var camera = new Dictionary<string, object>
        {
            ["centre"] = CameraRequireVector(NativeRead(() => view.GetThruPoint()), "centre"),
            ["direction"] = CameraRequireVector(NativeRead(() => view.GetDirection()), "direction"),
            ["radius"] = NativeRead(() => view.GetRadius()),
            ["extra_twist_degrees"] = NativeRead(() => view.GetExtraTwistDegrees()),
            ["perspective"] = NativeRead(() => view.PerspectiveMode), ["shaded"] = NativeRead(() => view.ShadeMode)
        };
        CameraNormalized((double[])camera["direction"]);
        if (!CameraFinite((double)camera["radius"]) || (double)camera["radius"] <= 0 || !CameraFinite((double)camera["extra_twist_degrees"]))
            throw new InvalidOperationException("Complete finite native camera observations are required.");
        return camera;
    }

    private static HashSet<string> DrawListRemovalAncestors(DbElement element, ref int remaining)
    {
        var chain = new HashSet<string>(StringComparer.Ordinal);
        DbElement current = element;
        for (int depth = 0; depth < 1024; depth++)
        {
            ThrowIfNativeFaulted();
            if (--remaining < 0) throw new InvalidOperationException("Removal scope proof exceeds the shared 40000-node owner-chain budget.");
            string reference = SelectionEditingElementReference(current);
            if (!chain.Add(reference)) throw new InvalidOperationException("Database owner cycle during removal scope proof.");
            DbElementType type = NativeRead(() => current.GetElementType());
            if (type == null) throw new InvalidOperationException("Owner-chain type is unavailable.");
            if (NativeRead(() => type.IsWorld)) return chain;
            current = NativeRead(() => current.Owner);
        }
        throw new InvalidOperationException("Removal scope proof exceeds the 1024-depth owner-chain limit.");
    }

    private static void DrawListRemovalRequireIsolated(DrawListRemovalState state)
    {
        int remaining = 40000;
        HashSet<string> ancestors = DrawListRemovalAncestors(state.Element, ref remaining);
        foreach (string member in state.Scene.Display.Members.Keys)
        {
            if (member == state.Reference) continue;
            if (ancestors.Contains(member) || DrawListRemovalAncestors(ModelRequireElement(member), ref remaining).Contains(state.Reference))
                throw new InvalidOperationException("Another displayed member overlaps the requested target's database ancestry; exact isolated removal is required.");
        }
        var removedReferences = new HashSet<string>(state.Scene.Display.Entries.Values.Where(entry => entry.owner_refno == state.Reference).Select(entry => entry.refno), StringComparer.Ordinal);
        foreach (DrawListPrimitiveVisualEntry entry in state.Scene.Display.Entries.Values)
            if (entry.owner_refno != state.Reference && (entry.refno == state.Reference || removedReferences.Contains(entry.refno)))
                throw new InvalidOperationException("A target-subtree primitive is also observed under another owner; isolated removal is ambiguous.");
        foreach (string selected in state.Scene.References)
            if (ancestors.Contains(selected) || DrawListRemovalAncestors(ModelRequireElement(selected), ref remaining).Contains(state.Reference))
                throw new InvalidOperationException("The target or an ancestor/descendant is graphically selected. Explicitly review and clear or replace that selection with e3d_set_graphical_selection before previewing removal again.");
    }

    private string DrawListRemovalRevision(DrawListRemovalState state, string requested)
    {
        return PrimitiveHash(writer =>
        {
            writer.Write("drawlist-exact-removal-v1"); writer.Write(drawListRemovalRevisionSession); writer.Write(requested);
            writer.Write(state.Reference); writer.Write(state.Type); writer.Write(state.Name); writer.Write(state.Owner);
            writer.Write(state.TopLevel); writer.Write(state.ExactMember);
            writer.Write(state.Scene.Project); writer.Write(state.Scene.Mdb); writer.Write(state.Scene.ViewId); writer.Write(state.Scene.DrawListId); writer.Write(state.Scene.Ce);
            writer.Write(state.Scene.References.Length); foreach (string selected in state.Scene.References) writer.Write(selected);
            foreach (double value in (double[])state.Camera["centre"]) writer.Write(value);
            foreach (double value in (double[])state.Camera["direction"]) writer.Write(value);
            writer.Write((double)state.Camera["radius"]); writer.Write((double)state.Camera["extra_twist_degrees"]);
            writer.Write((bool)state.Camera["perspective"]); writer.Write((bool)state.Camera["shaded"]);
            PrimitiveVisualWriteMap(writer, state.Scene.Display);
        });
    }

    private static object DrawListRemovalObservation(DrawListRemovalState state)
    {
        return new
        {
            complete = true, project = state.Scene.Project, mdb = state.Scene.Mdb, view_id = state.Scene.ViewId,
            drawlist_id = state.Scene.DrawListId, current_element = state.Scene.Ce,
            target = new { refno = state.Reference, type = state.Type, name = state.Name, owner_refno = state.Owner, top_level_element = state.TopLevel },
            exact_member = state.ExactMember, selection_refnos = state.Scene.References, camera = state.Camera,
            members = state.Scene.Display.Members.OrderBy(row => row.Key, StringComparer.Ordinal).Select(row => row.Value).ToArray(),
            primitive_entries = state.Scene.Display.Entries.OrderBy(row => row.Key, StringComparer.Ordinal).Select(row => row.Value).ToArray()
        };
    }

    private static bool DrawListRemovalVerify(DrawListRemovalState before, DrawListRemovalState after, Dictionary<string, object> result, bool expectedRemoval)
    {
        var wantedMembers = before.Scene.Display.Members.Where(row => row.Key != before.Reference).ToDictionary(row => row.Key, row => row.Value, StringComparer.Ordinal);
        var wantedPrimitives = before.Scene.Display.Entries.Where(row => row.Value.owner_refno != before.Reference).ToDictionary(row => row.Key, row => row.Value, StringComparer.Ordinal);
        var memberDifferences = CompareDrawListVisualSnapshots(wantedMembers, after.Scene.Display.Members);
        var primitiveDifferences = CompareDrawListPrimitiveSnapshots(wantedPrimitives, after.Scene.Display.Entries);
        result["after"] = DrawListRemovalObservation(after); result["residual_complete"] = true;
        result["member_verification_errors"] = memberDifferences; result["primitive_verification_errors"] = primitiveDifferences;
        result["exact_member_after"] = after.ExactMember;
        bool identity = before.Reference == after.Reference && before.Type == after.Type && before.Name == after.Name && before.Owner == after.Owner && before.TopLevel == after.TopLevel;
        bool context = SelectionEditingSameContext(before.Scene, after.Scene);
        bool ce = before.Scene.Ce == after.Scene.Ce;
        bool selection = before.Scene.References.SequenceEqual(after.Scene.References, StringComparer.Ordinal);
        bool camera = CameraMatches(after.Camera, before.Camera);
        result["target_identity_verified"] = identity; result["context_unchanged"] = context;
        result["ce_after"] = after.Scene.Ce; result["ce_unchanged"] = ce;
        result["selection_unchanged"] = selection; result["camera_unchanged"] = camera;
        result["remaining_members_verified"] = memberDifferences.Count == 0;
        result["remaining_primitives_verified"] = primitiveDifferences.Count == 0;
        result["membership_verified"] = !after.ExactMember && memberDifferences.Count == 0 && primitiveDifferences.Count == 0;
        bool verified = !after.ExactMember && memberDifferences.Count == 0 && primitiveDifferences.Count == 0 && identity && context && ce && selection && camera;
        bool originalDisplay = CompareDrawListVisualSnapshots(before.Scene.Display.Members, after.Scene.Display.Members).Count == 0 &&
            CompareDrawListPrimitiveSnapshots(before.Scene.Display.Entries, after.Scene.Display.Entries).Count == 0;
        result["outcome_uncertain"] = !context || !identity;
        if (!expectedRemoval)
        {
            result["removed"] = context && identity ? (object)(before.ExactMember && !after.ExactMember) : null;
            result["write_applied"] = context && identity ? (object)!originalDisplay : null;
            result["partial_write"] = context && identity ? (object)!originalDisplay : null;
            result["original_observed_state_restored"] = originalDisplay && identity && context && ce && selection && camera;
        }
        return verified;
    }
}
