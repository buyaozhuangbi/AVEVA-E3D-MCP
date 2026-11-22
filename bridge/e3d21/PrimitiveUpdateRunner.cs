using System;
using System.Collections.Generic;

namespace E3d21AiBridge;

// The caller captures exact native values and verifies native state. This
// runner only orders reversible setters; it creates/deletes no database items.
internal static class PrimitiveUpdateRunner
{
    internal static PrimitiveUpdateRunResult Run(IList<PrimitiveUpdateStep> steps,
        Action verifyFinal, Func<bool> verifyRollback)
    {
        if (steps == null) throw new ArgumentNullException("steps");
        if (verifyFinal == null) throw new ArgumentNullException("verifyFinal");
        if (verifyRollback == null) throw new ArgumentNullException("verifyRollback");
        var plan = new PrimitiveUpdateStep[steps.Count];
        var names = new HashSet<string>(StringComparer.Ordinal);
        for (int index = 0; index < plan.Length; index++)
        {
            PrimitiveUpdateStep step = steps[index];
            if (step == null || string.IsNullOrWhiteSpace(step.Name) || step.Apply == null || step.Rollback == null)
                throw new ArgumentException("Every step requires a name and both setters before editing.", "steps");
            if (!names.Add(step.Name))
                throw new ArgumentException("Step names must be unique before editing.", "steps");
            // Copy values as well as the list: callbacks must not change a later
            // setter or replace the exact restoration captured before writes.
            plan[index] = new PrimitiveUpdateStep { Name = step.Name, Apply = step.Apply, Rollback = step.Rollback };
        }

        var result = new PrimitiveUpdateRunResult();
        var attempted = new List<string>();
        var completed = new List<string>();
        try
        {
            foreach (PrimitiveUpdateStep step in plan)
            {
                // A native setter may apply its value and then throw.
                attempted.Add(step.Name);
                step.Apply();
                completed.Add(step.Name);
            }
            verifyFinal();
            result.success = true;
        }
        catch (Exception error)
        {
            result.error = error;
            if (attempted.Count > 0)
            {
                result.rollback_attempted = true;
                var rollbackErrors = new List<string>();
                for (int index = attempted.Count - 1; index >= 0; index--)
                {
                    PrimitiveUpdateStep step = plan[index];
                    try { step.Rollback(); }
                    catch (Exception rollbackError)
                    {
                        rollbackErrors.Add(step.Name + ": " + rollbackError.Message);
                    }
                }
                try { result.rollback_verified_original = verifyRollback(); }
                catch (Exception verificationError)
                {
                    result.rollback_verified_original = false;
                    rollbackErrors.Add("Verify original state: " + verificationError.Message);
                }
                result.rollback_errors = rollbackErrors.ToArray();
                result.rollback_succeeded = rollbackErrors.Count == 0 && result.rollback_verified_original == true;
            }
        }
        result.attempted_steps = attempted.ToArray();
        result.completed_steps = completed.ToArray();
        return result;
    }
}

internal sealed class PrimitiveUpdateStep
{
    internal string Name;
    internal Action Apply;
    internal Action Rollback;
}

internal sealed class PrimitiveUpdateRunResult
{
    internal bool success;
    internal Exception error;
    // Completion means the callback returned; native readback proves state.
    internal string[] attempted_steps = new string[0];
    internal string[] completed_steps = new string[0];
    internal bool rollback_attempted;
    internal bool? rollback_succeeded;
    internal bool? rollback_verified_original;
    internal string[] rollback_errors = new string[0];
}
