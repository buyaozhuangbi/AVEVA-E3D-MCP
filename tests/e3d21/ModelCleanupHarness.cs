using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core.Utilities.Messaging;
using E3d21AiBridge;

internal static class ModelCleanupHarness
{
    private static int checks;
    private static void Check(bool ok, string message) { checks++; if (!ok) throw new Exception(message); }
    private static Dictionary<string, object> Run()
    {
        var result = new Dictionary<string, object>();
        Scenario.Context.Cleanup(result, Scenario.Created, Scenario.CapturedReference, Scenario.CapturedParent, Scenario.Original, Scenario.RequireEmpty);
        return result;
    }
    private static Dictionary<string, object> RunDelete()
    {
        return (Dictionary<string, object>)Scenario.Context.Delete(Scenario.Target.Key, false);
    }
    private static void Rejected(string label)
    {
        var result = Run();
        Check(Scenario.Deletes == 0 && Equals(result["cleanup_attempted"], false), label + ": unproved target never deleted.");
        Check(!Equals(result["cleanup_succeeded"], true) && Equals(result["creation_result_unknown"], true)
            && result["residual_refno"] != null && result.ContainsKey("cleanup_errors"), label + ": uncertainty and residual identity retained.");
    }
    public static int Main()
    {
        try { return Execute(); }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
    private static int Execute()
    {
        Scenario.Reset(); var result = Run();
        Check(Scenario.Parent.Reference[0] != Scenario.Target.Reference[0], "Regression fixture uses legitimately different DBREF high words.");
        Check(Scenario.Deletes == 1 && Equals(result["cleanup_attempted"], true) && Equals(result["cleanup_parent_append_verified"], true), "Exact new child under captured owner is removed despite different high word.");
        Check(Equals(result["cleanup_succeeded"], true) && Equals(result["cleanup_verified_absent"], true)
            && Equals(result["creation_result_unknown"], false) && result["residual_refno"] == null, "Deletion is verified by deleted state and complete original parent membership.");
        Check(Scenario.Parent.Children.Select(value => value.Key).SequenceEqual(Scenario.Original), "Only the captured appended child is removed.");
        Scenario.Reset(); Scenario.RequireEmpty = true; Run();
        string[] baselineCalls = Scenario.Calls.Distinct().ToArray();

        Scenario.Reset(); Scenario.Target.Parent = Scenario.OtherParent; Rejected("Wrong owner");
        Scenario.Reset(); Scenario.Original = Scenario.Original.Concat(new[] { Scenario.Target.Key }).ToArray(); Rejected("Previously existing reference");
        Scenario.Reset(); Scenario.Created = Scenario.Parent.Children[0]; Rejected("Returned wrapper reference differs");
        Scenario.Reset(); Scenario.LookupOverride = Scenario.Parent.Children[0]; Rejected("Exact-reference lookup returns a different reference");
        Scenario.Reset(); Scenario.ParentLookupOverride = Scenario.OtherParent; Rejected("Captured parent resolves differently");
        foreach (string change in new[] { "extra", "reordered", "missing", "inserted", "duplicate" })
        {
            Scenario.Reset();
            if (change == "extra") Scenario.Parent.Children.Add(Scenario.Child(2000000003, 5));
            if (change == "reordered") { var value = Scenario.Parent.Children[0]; Scenario.Parent.Children[0] = Scenario.Parent.Children[1]; Scenario.Parent.Children[1] = value; }
            if (change == "missing") Scenario.Parent.Children.RemoveAt(0);
            if (change == "inserted") { Scenario.Parent.Children.Remove(Scenario.Target); Scenario.Parent.Children.Insert(0, Scenario.Target); }
            if (change == "duplicate") Scenario.Parent.Children.Add(Scenario.Target);
            Rejected("Changed full parent sequence: " + change);
        }
        Scenario.Reset(); Scenario.ThrowAt = "first.parent"; Rejected("Incomplete membership read");
        Scenario.Reset(); Scenario.RequireEmpty = true; Scenario.Target.Children.Add(new DbElement("nested", 2000000003, 2) { Parent = Scenario.Target });
        Rejected("CreateElement target acquired an unknown child");
        Scenario.Reset(); Scenario.RequireEmpty = true; result = Run();
        Check(Scenario.Deletes == 1 && Equals(result["cleanup_created_empty_verified"], true), "CreateElement empty-only cleanup verifies no child members before delete.");
        Scenario.Reset(); Scenario.Target.Children.Add(new DbElement("nested", 2000000003, 2) { Parent = Scenario.Target });
        result = Run(); Check(Scenario.Deletes == 1 && Equals(result["cleanup_succeeded"], true), "Default helper mode preserves legacy nonempty-subtree behavior; callers must establish provenance separately.");
        Scenario.Reset(); Scenario.CapturedReference = null;
        result = Run(); Check(Scenario.Deletes == 0 && Equals(result["cleanup_succeeded"], null) && Equals(result["creation_result_unknown"], true), "Missing capture cannot trigger deletion.");

        Scenario.Reset(); Scenario.Target.Valid = false; Scenario.Target.Deleted = true; Scenario.Parent.Children.Remove(Scenario.Target);
        result = Run(); Check(Scenario.Deletes == 0 && Equals(result["cleanup_succeeded"], true), "Already deleted exact element is verified without another delete.");
        Scenario.Reset(); Scenario.DeleteReturnsFalse = true; result = Run();
        Check(Scenario.Deletes == 1 && Equals(result["cleanup_succeeded"], false) && Equals(result["creation_result_unknown"], true), "False native deletion result cannot manufacture cleanup success.");
        Scenario.Reset(); Scenario.ThrowAfterDelete = true; result = Run();
        Check(Scenario.Deletes == 1 && Equals(result["cleanup_succeeded"], true) && result.ContainsKey("cleanup_errors"), "Thrown delete preserves diagnostics while independent deleted/membership evidence reconciles actual absence.");
        Scenario.Reset(); Scenario.AfterDeleteLookupOverride = new DbElement("wrong-deleted", 2000000003, 99) { Valid = false, Deleted = true };
        result = Run(); Check(Scenario.Deletes == 1 && !Equals(result["cleanup_succeeded"], true) && Equals(result["creation_result_unknown"], true), "A different deleted wrapper after delete cannot certify exact target absence.");
        Scenario.Reset(); Scenario.AfterDeleteParentOverride = Scenario.OtherParent;
        result = Run(); Check(Scenario.Deletes == 1 && !Equals(result["cleanup_succeeded"], true) && Equals(result["creation_result_unknown"], true), "A changed parent resolution after delete cannot certify original membership restoration.");

        Scenario.Reset(); Scenario.Parent.Children.Clear();
        for (int index = 0; index < 10000; index++) Scenario.Parent.Children.Add(Scenario.Child(2000000001, index + 100));
        Scenario.Original = Scenario.Parent.Children.Select(value => value.Key).ToArray(); Scenario.Parent.Children.Add(Scenario.Target);
        result = Run(); Check(Scenario.Deletes == 1 && Equals(result["cleanup_succeeded"], true), "The captured 10000-member bound admits the one appended child for pre-delete proof.");
        Scenario.Reset(); for (int index = 0; index < 10000; index++) Scenario.Parent.Children.Insert(0, Scenario.Child(2000000001, index + 100));
        Rejected("Over-budget membership is not treated as complete");

        foreach (string at in baselineCalls)
        {
            Scenario.Reset(); Scenario.RequireEmpty = true; Scenario.FaultAt = at;
            bool faulted = false;
            try { Run(); } catch (NativeDatabaseFaultException) { faulted = true; }
            Check(faulted && Scenario.NativeAfterFault == 0, "Native fault halts every subsequent SDK call at " + at);
            if (at != "delete.target" && !at.Contains("deleted"))
                Check(Scenario.Deletes <= 1, "Native failure never retries deletion.");
        }
        Scenario.Reset(); Scenario.Context.Fault();
        try { Run(); throw new Exception("Expected latched fault rejection."); } catch (NativeDatabaseFaultException) { }
        Check(Scenario.Calls.Count == 0 && Scenario.Deletes == 0, "Preexisting native fault prevents all cleanup SDK dispatch.");

        Scenario.Reset(); result = RunDelete();
        Check(Scenario.Deletes == 1 && Equals(result["success"], true)
            && Equals(result["verification_status"], "confirmed_deleted")
            && Equals(result["native_delete_returned"], true) && Equals(result["deletion_verified"], true),
            "Ordinary delete requires exact deleted DBREF and complete owner membership readback.");
        Check(Scenario.Parent.Children.Select(value => value.Key).SequenceEqual(Scenario.Original),
            "Ordinary delete removes only the exact requested owner member.");

        Scenario.Reset(); Scenario.DeleteReturnsFalse = true; result = RunDelete();
        Check(Scenario.Deletes == 1 && Equals(result["success"], false)
            && Equals(result["verification_status"], "still_present") && Equals(result["native_delete_returned"], false)
            && Equals(result["outcome_uncertain"], true) && Equals(result["retry_safe"], false),
            "A surviving root after a false hierarchy delete does not prove no partial effect.");

        Scenario.Reset(); Scenario.DeleteReturnsFalse = true; Scenario.DeleteMutatesDescendantThenFails = true;
        result = RunDelete();
        Check(Scenario.Deletes == 1 && Scenario.Target.Children.Count == 1
            && Equals(result["verification_status"], "still_present")
            && Equals(result["outcome_uncertain"], true) && Equals(result["retry_safe"], false),
            "A surviving root with a changed child hierarchy remains an uncertain, non-retryable outcome.");

        Scenario.Reset(); Scenario.DeleteReturnsFalse = true; Scenario.DeleteAppliesThenFalse = true; result = RunDelete();
        Check(Scenario.Deletes == 1 && Equals(result["success"], true)
            && Equals(result["verification_status"], "confirmed_deleted") && Equals(result["native_delete_returned"], false),
            "Positive exact native readback can confirm the deletion effect even when Delete returns false.");

        Scenario.Reset(); Scenario.ThrowAfterDelete = true; result = RunDelete();
        Check(Scenario.Deletes == 1 && Equals(result["success"], true)
            && Equals(result["verification_status"], "confirmed_deleted") && result.ContainsKey("delete_exception"),
            "Independent readback reconciles an exception after deletion took effect without replay.");

        Scenario.Reset(); Scenario.FaultAtAfterDelete = "resolve.target";
        try { RunDelete(); throw new Exception("Expected native-fault barrier during delete readback."); }
        catch (NativeDatabaseFaultException) { }
        Check(Scenario.Deletes == 1 && Scenario.NativeAfterFault == 0,
            "A native database fault after delete stops every subsequent SDK read.");

        Scenario.Reset(); Scenario.ThrowAtAfterDelete = "first.parent"; result = RunDelete();
        Check(Scenario.Deletes == 1 && Equals(result["success"], false)
            && Equals(result["verification_status"], "unknown") && Equals(result["outcome_uncertain"], true)
            && Equals(result["retry_safe"], false), "Incomplete owner readback is unknown and cannot be replayed.");

        Scenario.Reset(); Scenario.AfterDeleteLookupOverride = new DbElement("wrong-deleted", 2000000003, 99) { Valid = false, Deleted = true };
        result = RunDelete();
        Check(Scenario.Deletes == 1 && Equals(result["success"], false)
            && Equals(result["verification_status"], "unknown"), "A different numeric reference cannot certify deletion.");

        Scenario.Reset(); Scenario.Target.Parent = Scenario.OtherParent; result = RunDelete();
        Check(Scenario.Deletes == 0 && Equals(result["success"], false)
            && Equals(result["verification_status"], "not_attempted") && Equals(result["retry_safe"], true),
            "Owner membership preflight prevents unverified deletion before a native write.");

        Scenario.Reset(); Scenario.LookupOverride = new DbElement("missing-target", 2000000001, 999) { Valid = false };
        result = RunDelete();
        Check(Scenario.Deletes == 0 && Equals(result["success"], false)
            && Equals(result["delete_attempted"], false) && Equals(result["verification_status"], "not_attempted")
            && Equals(result["outcome_uncertain"], false) && Equals(result["retry_safe"], true),
            "A missing target is an explicit preflight rejection, not an unknown destructive outcome.");
        Console.WriteLine("Model cleanup production checks passed: " + checks);
        return 0;
    }
}

internal static class Scenario
{
    internal static E3d21Context Context;
    internal static DbElement Parent, OtherParent, Target, Created, LookupOverride, ParentLookupOverride, AfterDeleteLookupOverride, AfterDeleteParentOverride;
    internal static string CapturedParent, FaultAt, ThrowAt;
    internal static string FaultAtAfterDelete, ThrowAtAfterDelete;
    internal static int[] CapturedReference;
    internal static string[] Original;
    internal static bool DeleteReturnsFalse, DeleteAppliesThenFalse, DeleteMutatesDescendantThenFails, ThrowAfterDelete, RequireEmpty;
    internal static int Deletes, NativeAfterFault;
    internal static readonly List<string> Calls = new List<string>();
    internal static T Touch<T>(string label, T value)
    {
        if (Context.HasFault) { NativeAfterFault++; throw new InvalidOperationException("Native call after fault: " + label); }
        Calls.Add(label);
        if (FaultAt == label || Deletes > 0 && FaultAtAfterDelete == label) Context.Fault();
        if (ThrowAt == label || Deletes > 0 && ThrowAtAfterDelete == label)
            throw new InvalidOperationException("Injected read failure: " + label);
        return value;
    }
    internal static DbElement Child(int high, int low) { return new DbElement("child" + low, high, low) { Parent = Parent }; }
    internal static void Reset()
    {
        Calls.Clear(); Deletes = NativeAfterFault = 0;
        FaultAt = ThrowAt = FaultAtAfterDelete = ThrowAtAfterDelete = null;
        DeleteReturnsFalse = DeleteAppliesThenFalse = DeleteMutatesDescendantThenFails = ThrowAfterDelete = RequireEmpty = false; LookupOverride = ParentLookupOverride = null;
        AfterDeleteLookupOverride = AfterDeleteParentOverride = null;
        Context = new E3d21Context(); Parent = new DbElement("parent", 2000000001, 490); OtherParent = new DbElement("other", 2000000001, 491);
        Parent.Children.Add(Child(2000000001, 10)); Parent.Children.Add(Child(2000000001, 20));
        Original = Parent.Children.Select(value => value.Key).ToArray(); CapturedParent = Parent.Key;
        Target = Created = new DbElement("target", 2000000003, 1) { Parent = Parent };
        Parent.Children.Add(Target); CapturedReference = (int[])Target.Reference.Clone();
    }
}

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        private const int ModelMembershipVerificationLimit = 10000;
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;
        private readonly Action<Action> invokeOnUi = action => action();
        internal bool HasFault { get { return nativeFaultState.HasFault; } }
        internal void Fault() { nativeFaultState.Observe("Unhandled Database error 538 from CleanupFake", ""); }
        internal void Cleanup(Dictionary<string, object> result, DbElement created, int[] reference, string parent, string[] members, bool requireEmpty)
        { OnUi(() => { ModelCleanupCreated(result, created, reference, parent, members, requireEmpty); return true; }); }
        internal object Delete(string reference, bool dryRun) { return DeleteElement(reference, dryRun); }
        private bool TryBeginCommand(string command) { return true; }
        private void EndCommand() { }
        private Dictionary<string, object> BusyResult(string command) { return new Dictionary<string, object> { ["success"] = false, ["busy"] = true, ["command"] = command }; }
        private static Dictionary<string, object> ElementInfo(DbElement element)
        { return new Dictionary<string, object> { ["refno"] = ModelReference(element.Ref), ["type"] = "EQUI" }; }
        private static bool SafeBool(Func<bool> read) { return read(); }
        private static string MessageText(PdmsMessage message) { return message == null ? "" : message.MessageText(); }
        private static DbElement ResolveElement(string reference)
        {
            return NativeRead(() => Scenario.Touch("resolve." + (reference == Scenario.Target.Key ? "target" : "parent"),
                reference == Scenario.Target.Key
                    ? (Scenario.Deletes > 0 ? Scenario.AfterDeleteLookupOverride : null) ?? Scenario.LookupOverride ?? Scenario.Target
                    : (Scenario.Deletes > 0 ? Scenario.AfterDeleteParentOverride : null) ?? Scenario.ParentLookupOverride ?? Scenario.Parent));
        }
        private static string ElementRef(DbElement element) { return ModelReference(NativeRead(() => element.Ref)); }
        // PRODUCTION_METHODS
    }
}

namespace Aveva.Core.Database
{
    public sealed class DbElement
    {
        internal readonly string Label;
        internal readonly int[] Reference;
        internal readonly List<DbElement> Children = new List<DbElement>();
        internal bool Valid = true, Deleted;
        internal bool Deleteable = true, HierarchyDeleteable = true;
        internal DbElement Parent;
        internal DbElement(string label, int high, int low) { Label = label; Reference = new[] { high, low }; }
        internal string Key { get { return "=" + Reference[0] + "/" + Reference[1]; } }
        public int[] Ref { get { return Scenario.Touch("ref." + Label, (int[])Reference.Clone()); } }
        public bool IsNull { get { return Scenario.Touch("null." + Label, false); } }
        public bool IsValid { get { return Scenario.Touch("valid." + Label, Valid); } }
        public bool IsDeleteable { get { return Scenario.Touch("deletable." + Label, Deleteable); } }
        public bool IsHierarchyDeleteable { get { return Scenario.Touch("hierarchy-deleteable." + Label, HierarchyDeleteable); } }
        public bool IsDeleted { get { return Scenario.Touch("deleted." + Label, Deleted); } }
        public DbElement Owner { get { return Scenario.Touch("owner." + Label, Parent); } }
        public DbElement FirstMember() { return Scenario.Touch("first." + Label, Children.FirstOrDefault()); }
        public DbElement Next()
        {
            int index = Parent.Children.IndexOf(this);
            return Scenario.Touch("next." + Label, index + 1 < Parent.Children.Count ? Parent.Children[index + 1] : null);
        }
        public static DbElement GetElement(int[] reference)
        { return Scenario.Touch("resolve.target", (Scenario.Deletes > 0 ? Scenario.AfterDeleteLookupOverride : null) ?? Scenario.LookupOverride ?? Scenario.Target); }
        public bool Delete(out PdmsMessage message)
        {
            Scenario.Deletes++; message = null; Scenario.Touch("delete.target", true);
            if (Scenario.DeleteMutatesDescendantThenFails)
                Children.Add(new DbElement("partial-child", 2000000003, 777) { Parent = this });
            if (Scenario.DeleteReturnsFalse && !Scenario.DeleteAppliesThenFalse) return false;
            Valid = false; Deleted = true; Parent.Children.Remove(this);
            if (Scenario.ThrowAfterDelete) throw new InvalidOperationException("Delete returned via managed exception.");
            if (Scenario.DeleteReturnsFalse) return false;
            return true;
        }
    }
}
namespace Aveva.Core.Utilities.Messaging
{
    public sealed class PdmsMessage { public string MessageText() { return Scenario.Touch("message", "native diagnostic"); } }
}
