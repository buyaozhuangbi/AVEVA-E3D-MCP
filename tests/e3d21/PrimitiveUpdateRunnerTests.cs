#if PRIMITIVE_UPDATE_RUNNER_TESTS
using System;
using System.Collections.Generic;
using System.Linq;
using E3d21AiBridge;

internal static class PrimitiveUpdateRunnerTests
{
    private static int checks;
    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) throw new Exception(message);
    }

    private sealed class FakePrimitive
    {
        internal readonly List<string> Events = new List<string>();
        internal readonly Dictionary<string, int> Values = new Dictionary<string, int>
        { ["height"] = 10, ["diameter"] = 20, ["position"] = 30 };
        internal readonly string[] Children = { "negative-1", "negative-2" };
        internal PrimitiveUpdateStep Step(string name, int desired, bool throwAfterApply = false,
            bool throwAfterRollback = false, bool skipRollback = false)
        {
            int original = Values[name];
            return new PrimitiveUpdateStep
            {
                Name = name,
                Apply = () =>
                {
                    Events.Add("apply:" + name);
                    Values[name] = desired;
                    if (throwAfterApply) throw new InvalidOperationException("apply failed after mutation");
                },
                Rollback = () =>
                {
                    Events.Add("restore:" + name);
                    if (!skipRollback) Values[name] = original;
                    if (throwAfterRollback) throw new InvalidOperationException("restore failed after mutation");
                }
            };
        }
        internal void Final() { Events.Add("verify-final"); }
        internal bool Original()
        {
            Events.Add("verify-original");
            return Values["height"] == 10 && Values["diameter"] == 20 && Values["position"] == 30 &&
                Children.SequenceEqual(new[] { "negative-1", "negative-2" });
        }
    }

    private static void Main()
    {
        var primitive = new FakePrimitive();
        PrimitiveUpdateRunResult result = PrimitiveUpdateRunner.Run(new[]
        { primitive.Step("height", 110), primitive.Step("diameter", 120) }, primitive.Final, primitive.Original);
        Check(result.success && result.error == null && !result.rollback_attempted && result.rollback_succeeded == null && result.rollback_verified_original == null,
            "Successful verified writes must not invent a rollback status.");
        Check(primitive.Values["height"] == 110 && primitive.Values["diameter"] == 120 && primitive.Values["position"] == 30 && primitive.Children.Length == 2,
            "Successful dimension writes preserve unedited state and negative identities.");
        Check(primitive.Events.SequenceEqual(new[] { "apply:height", "apply:diameter", "verify-final" }) &&
            result.attempted_steps.SequenceEqual(new[] { "height", "diameter" }) && result.completed_steps.SequenceEqual(result.attempted_steps),
            "Success requires ordered writes followed by final verification.");

        primitive = new FakePrimitive();
        result = PrimitiveUpdateRunner.Run(new[] { primitive.Step("height", 110, true), primitive.Step("diameter", 120) }, primitive.Final, primitive.Original);
        Check(!result.success && result.rollback_attempted && result.rollback_succeeded == true && result.rollback_verified_original == true,
            "A setter which mutates then throws must be included in rollback.");
        Check(result.attempted_steps.SequenceEqual(new[] { "height" }) && result.completed_steps.Length == 0 && result.error.Message == "apply failed after mutation",
            "An attempted setter is distinct from a completed callback and preserves its error.");
        Check(primitive.Events.SequenceEqual(new[] { "apply:height", "restore:height", "verify-original" }) && primitive.Values["height"] == 10 && primitive.Values["diameter"] == 20,
            "An early failure stops later setters and restores exactly the attempted prefix.");

        primitive = new FakePrimitive();
        result = PrimitiveUpdateRunner.Run(new[] { primitive.Step("height", 110), primitive.Step("diameter", 120, true), primitive.Step("position", 130) }, primitive.Final, primitive.Original);
        Check(result.attempted_steps.SequenceEqual(new[] { "height", "diameter" }) && result.completed_steps.SequenceEqual(new[] { "height" }),
            "Later setter failure records the completed prefix independently.");
        Check(primitive.Events.SequenceEqual(new[] { "apply:height", "apply:diameter", "restore:diameter", "restore:height", "verify-original" }) && result.rollback_succeeded == true,
            "Every attempted setter must be restored in reverse order before original-state verification.");

        primitive = new FakePrimitive();
        var finalError = new InvalidOperationException("desired-state readback failed");
        result = PrimitiveUpdateRunner.Run(new[] { primitive.Step("height", 110), primitive.Step("position", 130) },
            () => { primitive.Final(); throw finalError; }, primitive.Original);
        Check(!result.success && ReferenceEquals(result.error, finalError) && result.completed_steps.Length == 2 && result.rollback_succeeded == true,
            "A final verification failure rolls back all completed writes and retains the original failure.");
        Check(primitive.Events.SequenceEqual(new[] { "apply:height", "apply:position", "verify-final", "restore:position", "restore:height", "verify-original" }),
            "Final verification must precede restoration, followed by a fresh original-state proof.");

        primitive = new FakePrimitive();
        result = PrimitiveUpdateRunner.Run(new[] { primitive.Step("height", 110), primitive.Step("diameter", 120, true, true) }, primitive.Final, primitive.Original);
        Check(result.rollback_succeeded == false && result.rollback_verified_original == true && result.rollback_errors.Length == 1 && result.rollback_errors[0].StartsWith("diameter:"),
            "A rollback setter which restores then throws remains a reported failure even if native state is later verified original.");
        Check(primitive.Events.Skip(primitive.Events.Count - 3).SequenceEqual(new[] { "restore:diameter", "restore:height", "verify-original" }),
            "Rollback must continue restoring earlier fields after a restoration setter throws.");
        Check(result.error.Message == "apply failed after mutation",
            "A rollback error must not replace the original operation error.");

        primitive = new FakePrimitive();
        result = PrimitiveUpdateRunner.Run(new[] { primitive.Step("height", 110, false, true), primitive.Step("diameter", 120, true, true) }, primitive.Final, primitive.Original);
        Check(result.rollback_errors.Length == 2 && result.rollback_errors[0].StartsWith("diameter:") && result.rollback_errors[1].StartsWith("height:") && result.rollback_verified_original == true,
            "Every rollback failure is retained in restoration order and verification still runs.");

        primitive = new FakePrimitive();
        result = PrimitiveUpdateRunner.Run(new[] { primitive.Step("height", 110, true, false, true) }, primitive.Final, primitive.Original);
        Check(result.rollback_succeeded == false && result.rollback_verified_original == false && result.rollback_errors.Length == 0 && primitive.Values["height"] == 110,
            "A rollback callback returning normally cannot imply recovery when fresh original-state proof fails.");

        primitive = new FakePrimitive();
        result = PrimitiveUpdateRunner.Run(new[] { primitive.Step("height", 110, true) }, primitive.Final,
            () => { primitive.Events.Add("verify-original-threw"); throw new InvalidOperationException("native read unavailable"); });
        Check(result.rollback_succeeded == false && result.rollback_verified_original == false && result.rollback_errors.Length == 1 && result.rollback_errors[0].Contains("native read unavailable"),
            "Unreadable original state fails recovery and retains its diagnostic.");

        primitive = new FakePrimitive();
        var steps = new List<PrimitiveUpdateStep>();
        PrimitiveUpdateStep diameter = primitive.Step("diameter", 120);
        PrimitiveUpdateStep height = primitive.Step("height", 110);
        Action applyHeight = height.Apply;
        height.Apply = () =>
        {
            applyHeight();
            steps.Clear();
            diameter.Name = "unreviewed";
            diameter.Apply = () => { throw new Exception("replacement must never execute"); };
            diameter.Rollback = () => { throw new Exception("replacement rollback must never execute"); };
        };
        steps.Add(height); steps.Add(diameter);
        result = PrimitiveUpdateRunner.Run(steps, () => { throw finalError; }, primitive.Original);
        Check(result.rollback_succeeded == true && result.attempted_steps.SequenceEqual(new[] { "height", "diameter" }) && primitive.Values["diameter"] == 20,
            "List or mutable-step changes during a callback cannot replace a reviewed setter or captured restoration.");

        primitive = new FakePrimitive();
        result = PrimitiveUpdateRunner.Run(new PrimitiveUpdateStep[0], primitive.Final, primitive.Original);
        Check(result.success && result.attempted_steps.Length == 0 && primitive.Events.SequenceEqual(new[] { "verify-final" }),
            "An empty edit plan still verifies final state without claiming writes.");
        primitive = new FakePrimitive();
        result = PrimitiveUpdateRunner.Run(new PrimitiveUpdateStep[0], () => { throw finalError; }, primitive.Original);
        Check(!result.success && !result.rollback_attempted && result.rollback_succeeded == null && result.rollback_verified_original == null && primitive.Events.Count == 0,
            "A no-write verification failure must not invoke restoration or invent a recovery claim.");

        CheckRejected(new PrimitiveUpdateStep[] { null }, false, false, "Null steps must fail before effects.");
        CheckRejected(new[] { new PrimitiveUpdateStep { Name = "height", Apply = () => { } } }, false, false, "Missing rollback must fail before effects.");
        CheckRejected(new[] { new PrimitiveUpdateStep { Name = "height", Rollback = () => { } } }, false, false, "Missing apply must fail before effects.");
        CheckRejected(new[] { new PrimitiveUpdateStep { Name = " ", Apply = () => { }, Rollback = () => { } } }, false, false, "Blank names must fail before effects.");
        CheckRejected(null, false, false, "Missing plan must fail before effects.");
        CheckRejected(new PrimitiveUpdateStep[0], true, false, "Missing final verifier must fail before effects.");
        CheckRejected(new PrimitiveUpdateStep[0], false, true, "Missing original verifier must fail before effects.");
        primitive = new FakePrimitive();
        bool duplicateRejected = false;
        try { PrimitiveUpdateRunner.Run(new[] { primitive.Step("height", 110), primitive.Step("height", 120) }, primitive.Final, primitive.Original); }
        catch (ArgumentException) { duplicateRejected = true; }
        Check(duplicateRejected && primitive.Events.Count == 0 && primitive.Values["height"] == 10,
            "Duplicate step names must fail preflight before any setter.");
        Console.WriteLine("Primitive update runner checks passed: " + checks);
    }

    private static void CheckRejected(IList<PrimitiveUpdateStep> steps, bool missingFinal, bool missingOriginal, string message)
    {
        bool rejected = false;
        bool called = false;
        Action final = () => { called = true; };
        Func<bool> original = () => { called = true; return true; };
        try { PrimitiveUpdateRunner.Run(steps, missingFinal ? null : final, missingOriginal ? null : original); }
        catch (ArgumentException) { rejected = true; }
        Check(rejected && !called, message);
    }
}
#endif
