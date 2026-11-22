using System;
using System.Collections.Generic;

namespace E3d21AiBridge;

// SDK-independent phase boundary for a panel edit. The caller owns the exact
// native reference/setter journal and proves rollback from fresh native reads.
// A delete may take effect before its API throws, so entering a delete callback
// is the irreversible boundary even when no callback completed successfully.
internal static class PanelUpdateRunner
{
    internal static PanelUpdateRunResult Run(Action applyReversible, Action verifyBeforeDeletion,
        IList<Action> deleteSteps, Action verifyFinal, Func<bool> rollback)
    {
        // Resolve the entire plan before side effects. Snapshotting also prevents
        // a callback mutating the caller's list from adding an unreviewed delete.
        if (applyReversible == null) throw new ArgumentNullException("applyReversible");
        if (verifyBeforeDeletion == null) throw new ArgumentNullException("verifyBeforeDeletion");
        if (deleteSteps == null) throw new ArgumentNullException("deleteSteps");
        if (verifyFinal == null) throw new ArgumentNullException("verifyFinal");
        if (rollback == null) throw new ArgumentNullException("rollback");
        var plannedDeletes = new Action[deleteSteps.Count];
        for (int index = 0; index < plannedDeletes.Length; index++)
        {
            plannedDeletes[index] = deleteSteps[index];
            if (plannedDeletes[index] == null)
                throw new ArgumentException("Every deletion step must be defined before editing.", "deleteSteps");
        }

        var result = new PanelUpdateRunResult();
        try
        {
            applyReversible();
            verifyBeforeDeletion();
            foreach (Action delete in plannedDeletes)
            {
                result.deletion_stage_started = true;
                delete();
                result.deleted_steps_completed++;
            }
            verifyFinal();
            result.success = true;
        }
        catch (Exception error)
        {
            result.error = error;
            if (!result.deletion_stage_started)
            {
                result.rollback_attempted = true;
                try { result.rollback_succeeded = rollback(); }
                catch (Exception rollbackError)
                {
                    result.rollback_succeeded = false;
                    result.rollback_error = rollbackError;
                }
            }
        }
        return result;
    }
}

internal sealed class PanelUpdateRunResult
{
    internal bool success { get; set; }
    internal Exception error { get; set; }
    internal bool rollback_attempted { get; set; }
    internal bool? rollback_succeeded { get; set; }
    internal Exception rollback_error { get; set; }
    internal bool deletion_stage_started { get; set; }
    // This counts callbacks which returned normally, not all vertices actually
    // deleted. Only fresh native reads can establish the latter after a failure.
    internal int deleted_steps_completed { get; set; }
}
