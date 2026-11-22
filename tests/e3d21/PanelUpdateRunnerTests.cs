#if PANEL_UPDATE_RUNNER_TESTS
using System;
using System.Collections.Generic;
using System.Linq;
using E3d21AiBridge;

internal static class PanelUpdateRunnerTests
{
    private static int checks;
    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) throw new Exception(message);
    }

    private sealed class FakePanel
    {
        internal readonly List<string> Events = new List<string>();
        internal readonly List<int> References = new List<int> { 10, 20, 30, 40, 50 };
        internal readonly List<int> Coordinates = new List<int> { 1, 2, 3, 4, 5 };
        internal void Apply()
        {
            Events.Add("apply");
            Coordinates[0] = 101;
            Coordinates[1] = 102;
            Coordinates[2] = 103;
        }
        internal bool Restore()
        {
            Events.Add("restore");
            Coordinates.Clear(); Coordinates.AddRange(new[] { 1, 2, 3, 4, 5 });
            return References.SequenceEqual(new[] { 10, 20, 30, 40, 50 });
        }
        internal void DeleteTail()
        {
            Events.Add("delete:" + References.Last());
            References.RemoveAt(References.Count - 1);
            Coordinates.RemoveAt(Coordinates.Count - 1);
        }
        internal void Before() { Events.Add("verify-before"); }
        internal void Final() { Events.Add("verify-final"); }
    }

    private static void Main()
    {
        var panel = new FakePanel();
        PanelUpdateRunResult result = PanelUpdateRunner.Run(panel.Apply, panel.Before,
            new Action[0], panel.Final, panel.Restore);
        Check(result.success && !result.rollback_attempted && result.rollback_succeeded == null,
            "A successful edit must leave no invented rollback status.");
        Check(panel.Coordinates[0] == 101 && panel.References.SequenceEqual(new[] { 10, 20, 30, 40, 50 }),
            "A successful same-count edit preserves every original reference.");
        Check(panel.Events.SequenceEqual(new[] { "apply", "verify-before", "verify-final" }),
            "Successful writes must be verified before reporting success.");

        panel = new FakePanel();
        result = PanelUpdateRunner.Run(panel.Apply, panel.Before,
            new Action[] { panel.DeleteTail, panel.DeleteTail }, panel.Final, panel.Restore);
        Check(result.success && result.deletion_stage_started && result.deleted_steps_completed == 2,
            "A successful shrink records that two exact deletion callbacks completed.");
        Check(panel.References.SequenceEqual(new[] { 10, 20, 30 }) && panel.Coordinates.SequenceEqual(new[] { 101, 102, 103 }),
            "A successful shrink preserves the kept prefix and removes only its planned tail.");
        Check(panel.Events.SequenceEqual(new[] { "apply", "verify-before", "delete:50", "delete:40", "verify-final" }),
            "No delete may precede reversible-state verification.");

        panel = new FakePanel();
        var writeError = new InvalidOperationException("second setter failed after applying");
        result = PanelUpdateRunner.Run(() => { panel.Apply(); throw writeError; }, panel.Before,
            new Action[] { panel.DeleteTail }, panel.Final, panel.Restore);
        Check(!result.success && ReferenceEquals(result.error, writeError) && result.rollback_attempted && result.rollback_succeeded == true,
            "A setter which mutates then fails must retain its original error and attempt restoration.");
        Check(panel.Coordinates.SequenceEqual(new[] { 1, 2, 3, 4, 5 }) && panel.Events.SequenceEqual(new[] { "apply", "restore" }),
            "Partial reversible writes must be restored without proceeding to verification or deletion.");

        panel = new FakePanel();
        var beforeError = new InvalidOperationException("readback mismatch");
        result = PanelUpdateRunner.Run(panel.Apply, () => { panel.Before(); throw beforeError; },
            new Action[] { panel.DeleteTail }, panel.Final, panel.Restore);
        Check(!result.deletion_stage_started && result.deleted_steps_completed == 0 && result.rollback_succeeded == true,
            "A pre-deletion mismatch must keep every original vertex eligible for restoration.");
        Check(panel.References.Count == 5 && panel.Coordinates[0] == 1 && !panel.Events.Any(e => e.StartsWith("delete:")),
            "An unverified reversible stage cannot delete a vertex.");

        panel = new FakePanel();
        result = PanelUpdateRunner.Run(panel.Apply, () => { throw beforeError; },
            new Action[0], panel.Final, () => { panel.Events.Add("restore-failed"); return false; });
        Check(!result.success && result.rollback_attempted && result.rollback_succeeded == false && result.rollback_error == null,
            "A rollback whose fresh native proof fails must remain failed even without an exception.");
        Check(panel.Coordinates[0] == 101, "The runner must not fabricate restored state after a failed rollback.");

        panel = new FakePanel();
        var restoreError = new InvalidOperationException("restore setter failed");
        result = PanelUpdateRunner.Run(panel.Apply, () => { throw beforeError; },
            new Action[0], panel.Final, () => { panel.Events.Add("restore-threw"); throw restoreError; });
        Check(!result.success && ReferenceEquals(result.error, beforeError) && ReferenceEquals(result.rollback_error, restoreError) && result.rollback_succeeded == false,
            "Rollback failure must preserve both operation and restoration errors.");

        panel = new FakePanel();
        var deleteError = new InvalidOperationException("delete applied before error");
        result = PanelUpdateRunner.Run(panel.Apply, panel.Before,
            new Action[] { () => { panel.DeleteTail(); throw deleteError; }, panel.DeleteTail }, panel.Final, panel.Restore);
        Check(!result.success && result.deletion_stage_started && result.deleted_steps_completed == 0 && !result.rollback_attempted && result.rollback_succeeded == null,
            "A delete can take effect before throwing; entering its callback must prohibit claimed rollback.");
        Check(panel.References.SequenceEqual(new[] { 10, 20, 30, 40 }) && panel.Coordinates[0] == 101,
            "The mutated residual must remain observable after a delete-and-throw failure.");
        Check(panel.Events.SequenceEqual(new[] { "apply", "verify-before", "delete:50" }),
            "Delete failure must stop every later delete and final-success verification.");

        panel = new FakePanel();
        result = PanelUpdateRunner.Run(panel.Apply, panel.Before,
            new Action[] { panel.DeleteTail, () => { throw deleteError; }, panel.DeleteTail }, panel.Final, panel.Restore);
        Check(!result.success && result.deleted_steps_completed == 1 && !result.rollback_attempted && panel.References.Count == 4,
            "A later deletion failure records its completed prefix and cannot recreate lost references.");

        panel = new FakePanel();
        var finalError = new InvalidOperationException("final snapshot failed");
        result = PanelUpdateRunner.Run(panel.Apply, panel.Before,
            new Action[] { panel.DeleteTail }, () => { panel.Final(); throw finalError; }, panel.Restore);
        Check(!result.success && ReferenceEquals(result.error, finalError) && !result.rollback_attempted && panel.References.Count == 4,
            "A final read failure after deletion must retain the residual and avoid invented recovery.");
        panel = new FakePanel();
        result = PanelUpdateRunner.Run(panel.Apply, panel.Before,
            new Action[0], () => { panel.Final(); throw finalError; }, panel.Restore);
        Check(!result.success && result.rollback_succeeded == true && panel.Coordinates[0] == 1,
            "The same final read failure remains reversible when no deletion callback was entered.");

        panel = new FakePanel();
        var planned = new List<Action> { panel.DeleteTail };
        result = PanelUpdateRunner.Run(() => { panel.Apply(); planned.Add(panel.DeleteTail); }, panel.Before,
            planned, panel.Final, panel.Restore);
        Check(result.success && result.deleted_steps_completed == 1 && panel.References.Count == 4,
            "Callback-side list mutation must not execute an unreviewed additional delete.");

        panel = new FakePanel();
        bool rejected = false;
        try { PanelUpdateRunner.Run(panel.Apply, panel.Before, new Action[] { panel.DeleteTail, null }, panel.Final, panel.Restore); }
        catch (ArgumentException) { rejected = true; }
        Check(rejected && panel.Events.Count == 0 && panel.References.Count == 5 && panel.Coordinates[0] == 1,
            "A malformed deletion plan must be rejected before any reversible mutation.");
        Console.WriteLine("Panel update runner checks passed: " + checks);
    }
}
#endif
