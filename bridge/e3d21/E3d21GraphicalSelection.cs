using System;
using System.Collections.Generic;
using Aveva.Core.Database;
using Aveva.Core3D.Graphics;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    public object GetGraphicalSelection(int limit)
    {
        if (limit < 1 || limit > 200) throw new ArgumentOutOfRangeException("limit");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        var elements = new List<object>();
        var errors = new List<object>();
        var result = new Dictionary<string, object>
        {
            ["success"] = false, ["available"] = false, ["complete"] = false,
            ["source"] = "E3D SDK DrawList.GraphicalSelection",
            ["scope"] = "Native interaction graphical selection in SDK order. The SDK implementation uses the interaction selection service; view and Drawlist IDs are observation context, not proof that each selected object belongs to that view. This is not Design Explorer selection, all highlighted objects, or the last mouse pick.",
            ["enumeration_scope"] = "The SDK materializes the selection array before limit is applied. limit bounds element-detail reads and returned entries, not native array construction. No continuation cursor or frozen selection snapshot is provided.",
            ["consistency"] = "single_ui_dispatch", ["read_only"] = true,
            ["limit"] = limit, ["selection_count"] = null, ["returned_count"] = 0,
            ["resolved_count"] = 0, ["truncated"] = null, ["has_more"] = null,
            ["view_id"] = null, ["drawlist_id"] = null, ["context_unchanged"] = null,
            ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
            ["elements"] = elements, ["read_errors"] = errors
        };
        try
        {
            return OnUi(delegate
            {
                if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
                try
                {
                    result["ce_before"] = ModelCurrentReference();
                    View3D view = CameraRequireView();
                    DrawListManager manager = NativeRead(() => DrawListManager.Instance);
                    if (manager == null) throw new InvalidOperationException("No Drawlist manager is available.");
                    DrawList drawList = NativeRead(() => manager.CurrentDrawList);
                    if (drawList == null) throw new InvalidOperationException("No current Drawlist is available.");
                    int viewId = NativeRead(() => view.ViewId);
                    int drawListId = NativeRead(() => drawList.ID);
                    result["view_id"] = viewId;
                    result["drawlist_id"] = drawListId;
                    DbElement[] selected = NativeRead(() => drawList.GraphicalSelection());
                    if (selected == null) throw new InvalidOperationException("The SDK returned no selection collection; an empty selection cannot be inferred.");
                    result["available"] = true;
                    result["selection_count"] = selected.Length;
                    result["truncated"] = selected.Length > limit;
                    result["has_more"] = selected.Length > limit;
                    int resolved = 0;
                    for (int index = 0; index < Math.Min(selected.Length, limit); index++)
                    {
                        ThrowIfNativeFaulted();
                        var rowErrors = new List<object>();
                        var row = new Dictionary<string, object>
                        {
                            ["selection_index"] = index + 1, ["success"] = false,
                            ["refno"] = null, ["type"] = null, ["name"] = null,
                            ["read_errors"] = rowErrors
                        };
                        elements.Add(row);
                        result["returned_count"] = elements.Count;
                        DbElement element = selected[index];
                        try
                        {
                            if (element == null || NativeRead(() => element.IsNull) ||
                                !NativeRead(() => element.IsValid) || NativeRead(() => element.IsDeleted))
                                throw new InvalidOperationException("The SDK selection contains an unreadable database element.");
                            GraphicalSelectionRead(row, "refno", rowErrors,
                                () => ModelReference(NativeRead(() => element.Ref)));
                            GraphicalSelectionRead(row, "type", rowErrors, delegate
                            {
                                var type = NativeRead(() => element.GetElementType());
                                if (type == null) throw new InvalidOperationException("The SDK returned no element type.");
                                string name = NativeRead(() => type.ShortName);
                                if (string.IsNullOrWhiteSpace(name)) throw new InvalidOperationException("The SDK returned an empty element type.");
                                return name.ToUpperInvariant();
                            });
                            GraphicalSelectionRead(row, "name", rowErrors,
                                () => NativeRead(() => element.GetAsString(DbAttributeInstance.NAME)));
                            row["success"] = rowErrors.Count == 0;
                            if (Equals(row["success"], true)) resolved++;
                        }
                        catch (Exception ex)
                        {
                            ThrowIfNativeFaulted();
                            GraphicalSelectionError(rowErrors, "element", ex.Message);
                        }
                        if (rowErrors.Count > 0)
                            errors.Add(new Dictionary<string, object>
                            {
                                ["path"] = "elements[" + index + "]", ["errors"] = rowErrors
                            });
                        result["resolved_count"] = resolved;
                    }
                    result["ce_after"] = ModelCurrentReference();
                    result["ce_unchanged"] = Equals(result["ce_before"], result["ce_after"]);
                    View3D afterView = NativeRead(() => CurrentView3D()) as View3D;
                    DrawList afterDrawList = NativeRead(() => manager.CurrentDrawList);
                    result["context_unchanged"] = ReferenceEquals(afterView, view) &&
                        afterView != null && NativeRead(() => afterView.ViewId) == viewId &&
                        afterDrawList != null && NativeRead(() => afterDrawList.ID) == drawListId;
                    if (!Equals(result["ce_unchanged"], true))
                        GraphicalSelectionError(errors, "ce_after", "The current element changed during selection inspection.");
                    if (!Equals(result["context_unchanged"], true))
                        GraphicalSelectionError(errors, "context", "The current view or Drawlist changed during selection inspection.");
                    result["success"] = errors.Count == 0;
                    result["complete"] = Equals(result["success"], true) && Equals(result["truncated"], false);
                }
                catch (Exception ex)
                {
                    ThrowIfNativeFaulted();
                    GraphicalSelectionError(errors, "selection", ex.Message);
                }
                return result;
            });
        }
        catch (NativeDatabaseFaultException)
        {
            // The managed report may already contain useful rows. Never use the
            // SDK to finish identity/CE observations after the fault barrier.
            result["success"] = false;
            result["complete"] = false;
            result["ce_after"] = null;
            result["ce_unchanged"] = null;
            result["context_unchanged"] = null;
            result["native_database_fault"] = nativeFaultState == null ? null : nativeFaultState.Snapshot();
            GraphicalSelectionError(errors, "selection", "A native database fault stopped selection inspection. Partial observations are retained; later SDK reads were not attempted.");
            return result;
        }
    }

    private static void GraphicalSelectionRead(Dictionary<string, object> row, string field,
        List<object> errors, Func<object> read)
    {
        try
        {
            object value = read();
            if (value == null) throw new InvalidOperationException("The SDK returned no " + field + " value.");
            row[field] = value;
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            GraphicalSelectionError(errors, field, ex.Message);
        }
    }

    private static void GraphicalSelectionError(List<object> errors, string path, string message)
    {
        errors.Add(new Dictionary<string, object> { ["path"] = path, ["error"] = message });
    }
}
