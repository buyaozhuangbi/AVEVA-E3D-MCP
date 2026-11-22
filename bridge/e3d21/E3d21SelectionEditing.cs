using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core3D.Graphics;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private readonly string selectionEditingRevisionSession = Guid.NewGuid().ToString("N");

    private sealed class SelectionEditingState
    {
        internal View3D View;
        internal DrawList DrawList;
        internal int ViewId, DrawListId;
        internal string Project, Mdb, Ce, DisplayFingerprint;
        internal string[] References;
        internal PrimitiveVisualMap Display;
    }

    public object SetGraphicalSelection(IList<string> refnos, string expectedRevision, bool dryRun)
    {
        if (refnos == null || refnos.Count > 32)
            throw new ArgumentException("refnos must contain between 0 and 32 explicit =database/element references.", "refnos");
        string[] requested = refnos.Select(SelectionEditingReference).ToArray();
        if (requested.Distinct(StringComparer.Ordinal).Count() != requested.Length)
            throw new ArgumentException("refnos must not contain duplicate references.", "refnos");
        string expected = expectedRevision == null ? null : expectedRevision.ToLowerInvariant();
        if (expected != null && (expected.Length != 64 || expected.Any(c => !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f'))))
            throw new ArgumentException("expected_revision must contain exactly 64 hexadecimal characters.", "expectedRevision");
        if (!dryRun && expected == null)
            throw new ArgumentException("Execution requires expected_revision from a current graphical selection preview.", "expectedRevision");

        var errors = new List<object>();
        var result = new Dictionary<string, object>
        {
            ["success"] = false, ["dry_run"] = dryRun, ["operation"] = requested.Length == 0 ? "clear" : "replace",
            ["requested_refnos"] = requested, ["desired_refnos"] = requested, ["input_count"] = refnos.Count,
            ["add_graphics"] = false,
            ["revision"] = null, ["revision_conflict"] = false, ["no_change"] = false,
            ["write_attempted"] = false, ["native_write_returned"] = false,
            ["clear_attempted"] = false, ["clear_completed"] = false,
            ["add_attempted"] = false, ["add_completed"] = false,
            ["notification_attempted"] = false, ["notification_completed"] = false,
            ["update_attempted"] = false, ["update_completed"] = false,
            ["before"] = null, ["immediate_readback"] = null, ["readback"] = null,
            ["readback_verified"] = false, ["verification_state"] = "rejected",
            ["selection_matches"] = null, ["selection_order_matches"] = null,
            ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
            ["context_unchanged"] = null, ["display_unchanged"] = null,
            ["outcome_uncertain"] = false, ["retry_safe"] = false, ["rollback_attempted"] = false,
            ["native_database_fault"] = null, ["errors"] = errors,
            ["scope"] = "Replace the native interaction graphical selection with exact references already observed in the current Drawlist. View/Drawlist IDs are context, not proof of view-private selection ownership.",
            ["consistency"] = "Preview and revalidation in one UI dispatch; actual execution has one independent subsequent UI readback. No native transaction, completion barrier or automatic rollback.",
            ["revision_scope"] = "Bridge context instance, project/MDB, view/Drawlist IDs, CE, complete ordered native selection, desired refs, and complete observed member/primitive display maps. This is an observation token, not a native revision.",
            ["display_scope"] = "Complete bounded member/primitive presence and five public visual values; hidden masks, camera and pixel highlights are not verified."
        };
        bool acquired = false;
        string phase = "preflight";
        SelectionEditingState before = null;
        try
        {
            acquired = TryBeginCommand("set-graphical-selection");
            if (!acquired)
                throw new InvalidOperationException("E3D is busy; no graphical selection action was dispatched.");
            bool verifyLater = OnUi(delegate
            {
                phase = "preview";
                before = SelectionEditingReadState();
                DbElement[] desired = SelectionEditingResolve(requested, before);
                string revision = SelectionEditingRevision(before, requested);
                result["before"] = SelectionEditingObservation(before);
                result["ce_before"] = before.Ce;
                result["revision"] = revision;
                result["no_change"] = SelectionEditingSameSet(before.References, requested);
                if (expected != null && expected != revision)
                {
                    result["revision_conflict"] = true;
                    throw new InvalidOperationException("expected_revision no longer matches the request, selection, display or context. No write was attempted.");
                }
                if (dryRun)
                {
                    SelectionEditingRequireContext(before, result);
                    result["success"] = true;
                    result["verification_state"] = "preview";
                    return false;
                }

                // A second complete observation in the same callback binds the
                // actual writes to the preview, without retaining SDK objects
                // across client requests or using a nested public reader.
                phase = "revalidation";
                desired = SelectionEditingResolve(requested, before);
                SelectionEditingState immediate = SelectionEditingReadState();
                if (!ReferenceEquals(before.View, immediate.View) || SelectionEditingRevision(immediate, requested) != revision)
                {
                    result["revision_conflict"] = true;
                    throw new InvalidOperationException("Selection, display or context changed during preflight. No write was attempted.");
                }
                before = immediate;
                if (!Equals(result["no_change"], true))
                {
                    phase = "clear";
                    SelectionEditingRequireContext(before, result);
                    result["write_attempted"] = true;
                    result["clear_attempted"] = true;
                    NativeWrite(() => before.DrawList.ClearGraphicalSelection());
                    result["clear_completed"] = true;
                    if (desired.Length != 0)
                    {
                        phase = "add";
                        SelectionEditingRequireContext(before, result);
                        result["add_attempted"] = true;
                        NativeWrite(() => before.DrawList.AddToSelection(desired, false));
                        result["add_completed"] = true;
                    }
                    result["native_write_returned"] = true;
                    phase = "notification";
                    SelectionEditingRequireContext(before, result);
                    result["notification_attempted"] = true;
                    NativeWrite(() => before.DrawList.GraphicalSelectionHasChanged());
                    result["notification_completed"] = true;
                    phase = "update";
                    SelectionEditingRequireContext(before, result);
                    result["update_attempted"] = true;
                    NativeWrite(() => Aveva.Core.Utilities.CommandLine.Command.Update());
                    result["update_completed"] = true;
                }
                phase = "immediate_readback";
                SelectionEditingState after = SelectionEditingReadState();
                result["immediate_readback"] = SelectionEditingObservation(after);
                SelectionEditingVerify(before, after, requested, result);
                if (!Equals(result["context_unchanged"], true) || !Equals(result["ce_unchanged"], true) ||
                    !Equals(result["display_unchanged"], true))
                {
                    result["verification_state"] = "not_confirmed";
                    result["outcome_uncertain"] = Equals(result["write_attempted"], true);
                    errors.Add(new { phase, error = "Context, CE or complete display state changed; no further SDK action was attempted." });
                    return false;
                }
                // Selection propagation may still be pending. Only this one
                // subsequent read is allowed; the mutation is never replayed.
                return true;
            });

            if (verifyLater)
            {
                phase = "readback";
                OnUi(delegate
                {
                    SelectionEditingRequireContext(before, result);
                    SelectionEditingState after = SelectionEditingReadState();
                    result["readback"] = SelectionEditingObservation(after);
                    bool verified = SelectionEditingVerify(before, after, requested, result);
                    result["readback_verified"] = verified;
                    result["success"] = verified;
                    result["verification_state"] = verified ? "verified" : "not_confirmed";
                    result["outcome_uncertain"] = !verified && Equals(result["write_attempted"], true);
                    if (!verified) errors.Add(new { phase, error = "Independent complete readback did not confirm the exact requested selection and unchanged context/CE/display." });
                    return true;
                });
            }
        }
        catch (Exception error)
        {
            // This catch performs managed bookkeeping only. In particular a
            // failed native write/notification/update never triggers SDK reads,
            // cleanup, restore, retries or an attempted post-fault CE read.
            bool attempted = Equals(result["write_attempted"], true);
            result["success"] = false;
            result["readback_verified"] = false;
            result["outcome_uncertain"] = attempted;
            result["verification_state"] = attempted ? "unknown" : "rejected";
            // Preserve a known guard failure, but prior successful guards are
            // not observations of the state after this failed native call.
            if (!Equals(result["ce_unchanged"], false)) { result["ce_after"] = null; result["ce_unchanged"] = null; }
            if (!Equals(result["context_unchanged"], false)) result["context_unchanged"] = null;
            if (!Equals(result["display_unchanged"], false)) result["display_unchanged"] = null;
            if (nativeFaultState != null && nativeFaultState.HasFault)
            {
                result["native_database_fault"] = nativeFaultState.Snapshot();
                result["verification_state"] = "unknown";
                result["ce_after"] = null;
                result["ce_unchanged"] = null;
                result["context_unchanged"] = null;
                result["display_unchanged"] = null;
            }
            errors.Add(new { phase, error = error.Message });
        }
        finally { if (acquired) EndCommand(); }
        return result;
    }

    private static string SelectionEditingReference(string value)
    {
        if (value == null || !value.StartsWith("=", StringComparison.Ordinal))
            throw new ArgumentException("refnos must contain canonical =database/element references; names and implicit CE are unsupported.", "refnos");
        string[] parts = value.Substring(1).Split('/');
        int database, element;
        if (parts.Length != 2 || !int.TryParse(parts[0], NumberStyles.None, CultureInfo.InvariantCulture, out database) || database <= 0 ||
            !int.TryParse(parts[1], NumberStyles.None, CultureInfo.InvariantCulture, out element) || element < 0 ||
            value != "=" + database.ToString(CultureInfo.InvariantCulture) + "/" + element.ToString(CultureInfo.InvariantCulture))
            throw new ArgumentException("refnos must contain canonical =database/element references.", "refnos");
        return value;
    }

    private static string SelectionEditingElementReference(DbElement element)
    {
        if (element == null || NativeRead(() => element.IsNull) || !NativeRead(() => element.IsValid) || NativeRead(() => element.IsDeleted))
            throw new InvalidOperationException("The selection contains an unavailable, invalid or deleted element.");
        return ModelReference(NativeRead(() => element.Ref));
    }

    private static SelectionEditingState SelectionEditingReadContext()
    {
        View3D view = NativeRead(() => CurrentView3D()) as View3D;
        if (view == null || !NativeRead(() => view.IsValid()))
            throw new InvalidOperationException("No valid current E3D 3D view is available.");
        DrawListManager manager = NativeRead(() => DrawListManager.Instance);
        if (manager == null) throw new InvalidOperationException("No Drawlist manager is available.");
        int viewId = NativeRead(() => view.ViewId);
        DrawList drawList = NativeRead(() => manager.getViewDrawList(viewId));
        DrawList current = NativeRead(() => manager.CurrentDrawList);
        if (drawList == null || current == null) throw new InvalidOperationException("No current view Drawlist is available.");
        int drawListId = NativeRead(() => drawList.ID);
        if (NativeRead(() => current.ID) != drawListId)
            throw new InvalidOperationException("The current Drawlist differs from the current view's Drawlist.");
        Project project = NativeRead(() => Project.CurrentProject);
        MDB mdb = NativeRead(() => MDB.CurrentMDB);
        if (project == null || mdb == null) throw new InvalidOperationException("No project/MDB is available.");
        string projectName = NativeRead(() => project.Name), mdbName = NativeRead(() => mdb.Name);
        if (string.IsNullOrWhiteSpace(projectName) || string.IsNullOrWhiteSpace(mdbName))
            throw new InvalidOperationException("Project/MDB identity is unavailable.");
        return new SelectionEditingState
        {
            View = view, ViewId = viewId, DrawList = drawList, DrawListId = drawListId,
            Project = projectName, Mdb = mdbName, Ce = ModelCurrentReference()
        };
    }

    private static SelectionEditingState SelectionEditingReadState()
    {
        SelectionEditingState state = SelectionEditingReadContext();
        DbElement[] selection = NativeRead(() => state.DrawList.GraphicalSelection());
        if (selection == null) throw new InvalidOperationException("Native selection is unavailable; it cannot be assumed empty.");
        if (selection.Length > 200) throw new InvalidOperationException("A complete original selection of at most 200 elements is required.");
        state.References = selection.Select(SelectionEditingElementReference).ToArray();
        if (state.References.Distinct(StringComparer.Ordinal).Count() != state.References.Length)
            throw new InvalidOperationException("Native selection contains duplicate references; exact set verification is unavailable.");
        state.Display = PrimitiveVisualReadMap(state.DrawList, true, false);
        PrimitiveVisualRequireComplete(state.Display);
        state.DisplayFingerprint = PrimitiveHash(writer => PrimitiveVisualWriteMap(writer, state.Display));
        SelectionEditingState after = SelectionEditingReadContext();
        if (!SelectionEditingSameContext(state, after) || state.Ce != after.Ce)
            throw new InvalidOperationException("View, Drawlist, project/MDB or CE changed during the complete selection/display observation.");
        return state;
    }

    private static DbElement[] SelectionEditingResolve(string[] requested, SelectionEditingState state)
    {
        var resolved = new List<DbElement>();
        HashSet<string> primitiveReferences = null;
        using (var primitives = state.Display.Entries.Values.GetEnumerator())
        {
            foreach (string reference in requested)
            {
                string[] parts = reference.Substring(1).Split('/');
                int[] pair = { int.Parse(parts[0], CultureInfo.InvariantCulture), int.Parse(parts[1], CultureInfo.InvariantCulture) };
                DbElement element = NativeRead(() => DbElement.GetElement(pair));
                if (SelectionEditingElementReference(element) != reference)
                    throw new InvalidOperationException("Direct lookup returned a different element: " + reference);
                if (!state.Display.Members.ContainsKey(reference))
                {
                    // Index only the traversed part of this managed observation.
                    // Resume at the next entry for later targets; native lookup
                    // and identity checks still run for every requested ref.
                    if (primitiveReferences == null)
                        primitiveReferences = new HashSet<string>(StringComparer.Ordinal);
                    bool observed = primitiveReferences.Contains(reference);
                    while (!observed && primitives.MoveNext())
                    {
                        string primitiveReference = primitives.Current.refno;
                        primitiveReferences.Add(primitiveReference);
                        observed = primitiveReference == reference;
                    }
                    if (!observed)
                        throw new InvalidOperationException("Requested reference has no exact observed Drawlist member or primitive entry: " + reference);
                }
                resolved.Add(element);
            }
        }
        return resolved.ToArray();
    }

    private string SelectionEditingRevision(SelectionEditingState state, string[] desired)
    {
        return PrimitiveHash(writer =>
        {
            writer.Write("graphical-selection-replace-v1"); writer.Write(selectionEditingRevisionSession);
            writer.Write(state.Project); writer.Write(state.Mdb); writer.Write(state.ViewId); writer.Write(state.DrawListId); writer.Write(state.Ce);
            writer.Write(state.References.Length); foreach (string reference in state.References) writer.Write(reference);
            writer.Write(desired.Length); foreach (string reference in desired) writer.Write(reference);
            PrimitiveVisualWriteMap(writer, state.Display);
        });
    }

    private static bool SelectionEditingSameContext(SelectionEditingState before, SelectionEditingState after)
    {
        return ReferenceEquals(before.View, after.View) && before.ViewId == after.ViewId && before.DrawListId == after.DrawListId &&
            before.Project == after.Project && before.Mdb == after.Mdb;
    }

    private static void SelectionEditingRequireContext(SelectionEditingState before, Dictionary<string, object> result)
    {
        SelectionEditingState after = SelectionEditingReadContext();
        result["ce_after"] = after.Ce;
        result["ce_unchanged"] = before.Ce == after.Ce;
        result["context_unchanged"] = SelectionEditingSameContext(before, after);
        if (!Equals(result["ce_unchanged"], true) || !Equals(result["context_unchanged"], true))
            throw new InvalidOperationException("Context or CE changed; no further graphical selection action is permitted.");
    }

    private static bool SelectionEditingSameSet(string[] first, string[] second)
    {
        return first.Length == second.Length && new HashSet<string>(first, StringComparer.Ordinal).SetEquals(second);
    }

    private static bool SelectionEditingVerify(SelectionEditingState before, SelectionEditingState after,
        string[] requested, Dictionary<string, object> result)
    {
        var members = CompareDrawListVisualSnapshots(before.Display.Members, after.Display.Members);
        var primitives = CompareDrawListPrimitiveSnapshots(before.Display.Entries, after.Display.Entries);
        result["member_verification_errors"] = members;
        result["primitive_verification_errors"] = primitives;
        result["ce_after"] = after.Ce;
        result["ce_unchanged"] = before.Ce == after.Ce;
        result["context_unchanged"] = SelectionEditingSameContext(before, after);
        result["display_unchanged"] = members.Count == 0 && primitives.Count == 0;
        result["selection_matches"] = SelectionEditingSameSet(after.References, requested);
        result["selection_order_matches"] = after.References.SequenceEqual(requested, StringComparer.Ordinal);
        return Equals(result["ce_unchanged"], true) && Equals(result["context_unchanged"], true) &&
            Equals(result["display_unchanged"], true) && Equals(result["selection_matches"], true);
    }

    private static object SelectionEditingObservation(SelectionEditingState state)
    {
        return new Dictionary<string, object>
        {
            ["complete"] = true, ["refnos"] = state.References, ["selection_count"] = state.References.Length,
            ["project"] = state.Project, ["mdb"] = state.Mdb, ["view_id"] = state.ViewId, ["drawlist_id"] = state.DrawListId,
            ["ce"] = state.Ce, ["display_fingerprint"] = state.DisplayFingerprint,
            ["member_count"] = state.Display.Members.Count, ["primitive_entry_count"] = state.Display.Entries.Count
        };
    }
}
