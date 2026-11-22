using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core.Utilities.Messaging;
using E3d21AiBridge;

// Runs extracted production copy and shared cleanup bodies against in-memory SDK fakes.
// Native CopyHierarchy is not available here; capture admission is tested with the
// production policy, independently of the native engine and its provenance contract.
internal static class CopyCleanupHarness
{
    private static int checks;
    private static void Check(bool value, string message)
    { checks++; if (!value) throw new Exception(message); }
    private static Dictionary<string, object> Run(bool capture, bool emptyVerified = true, bool sourceUnchanged = true)
    { return Scenario.Context.RunCleanup(capture, emptyVerified, sourceUnchanged); }
    private static void Reject(bool capture, string message, bool emptyVerified = true, bool sourceUnchanged = true)
    {
        var result = Run(capture, emptyVerified, sourceUnchanged);
        Check(Scenario.Deletes == 0, message + ": no root/subtree deletion.");
        Check(Equals(result["cleanup_succeeded"], false) && Equals(result["creation_result_unknown"], true)
            && result["residual_refno"] != null, message + ": preserve residual/uncertainty.");
        Check(Scenario.Existing.All(node => node.Valid && !node.Deleted), message + ": existing data remains untouched.");
        Check(Scenario.Target.Valid && Scenario.CopyNodes.All(node => node.Valid && !node.Deleted), message + ": retained copy nodes are not partially removed.");
    }
    public static int Main()
    {
        try { Execute(); Console.WriteLine("Copy cleanup production checks passed: " + checks); return 0; }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
    private static void Execute()
    {
        Scenario.Reset(); Scenario.CopyChildren();
        var result = Run(true);
        Check(Scenario.Deletes == 1 && Equals(result["cleanup_succeeded"], true)
            && Equals(result["cleanup_captured_nodes_deleted"], true), "Accepted unchanged native result can still be removed after later failure.");
        Check(Equals(result["cleanup_verification_scope"], "captured_native_copy_result"), "Accepted cleanup reports its actual evidence scope.");
        Check(Scenario.Target.Deleted && Scenario.CopyNodes.All(node => node.Deleted)
            && Scenario.Parent.Children.Select(node => node.Key).SequenceEqual(Scenario.Original), "Captured deletion restores only original parent sequence.");
        Check(Scenario.Existing.All(node => node.Valid && !node.Deleted), "Source and existing destination elements are protected.");
        string[] calls = Scenario.Calls.Distinct().ToArray();

        Scenario.Reset(); result = Run(false);
        Check(Scenario.Deletes == 1 && Equals(result["cleanup_succeeded"], true)
            && Equals(result["cleanup_created_empty_verified"], true)
            && Equals(result["cleanup_verification_scope"], "exact_empty_created_root"), "Uncaptured but verified still-empty root can be removed.");
        Scenario.Reset(); Scenario.CopyChildren(); Reject(false, "CopyHierarchy throws or returned topology was never accepted");
        Scenario.Reset(); Scenario.CopyChildren(); Scenario.Target.Children.RemoveAt(1); Reject(false, "Partial native result avoids protected refs but is not captured");
        Scenario.Reset(); Reject(false, "Initial empty root proof never completed", false);
        Scenario.Reset(); Scenario.CopyChildren(); Reject(true, "Source changed after copy acceptance", sourceUnchanged: false);

        foreach (string mutation in new[] { "add", "replace", "reparent", "reorder", "remove", "type", "protected" })
        {
            Scenario.Reset(); Scenario.CopyChildren();
            Scenario.AfterCapture = () =>
            {
                if (mutation == "add") Scenario.Target.Children.Add(Scenario.Add("foreign", 2000000003, 99, "BOX", Scenario.Target));
                if (mutation == "replace") Scenario.Target.Children[1] = Scenario.Add("replacement", 2000000003, 98, "BOX", Scenario.Target);
                if (mutation == "reparent") { var child = Scenario.Target.Children[0].Children[0]; Scenario.Target.Children[0].Children.Clear(); child.Parent = Scenario.Target.Children[1]; child.Parent.Children.Add(child); }
                if (mutation == "reorder") Scenario.Target.Children.Reverse();
                if (mutation == "remove") Scenario.Target.Children.RemoveAt(1);
                if (mutation == "type") Scenario.Target.Children[1].TypeName = "CONE";
                if (mutation == "protected") { var existing = Scenario.Existing[1]; existing.Parent = Scenario.Target; Scenario.Target.Children.Add(existing); }
            };
            Reject(true, "Post-capture subtree change " + mutation);
        }
        foreach (string mutation in new[] { "append", "remove", "reorder", "owner" })
        {
            Scenario.Reset(); Scenario.CopyChildren();
            Scenario.AfterCapture = () =>
            {
                if (mutation == "append") Scenario.Parent.Children.Add(Scenario.Add("sibling", 2000000003, 97, "BOX", Scenario.Parent));
                if (mutation == "remove") Scenario.Parent.Children.RemoveAt(0);
                if (mutation == "reorder") Scenario.Parent.Children.Reverse();
                if (mutation == "owner") Scenario.Target.Parent = Scenario.Other;
            };
            Reject(true, "Destination concurrency change " + mutation);
        }
        Scenario.Reset(); Scenario.CopyChildren(); Scenario.AfterCapture = () => Scenario.ThrowAt = "first.target";
        Reject(true, "Incomplete current subtree enumeration");
        Scenario.Reset(); Scenario.CopyChildren(); Scenario.AfterCapture = () => Scenario.CopyNodes[0].Name = "/renamed-by-our-setter";
        result = Run(true);
        Check(Scenario.Deletes == 1 && Equals(result["cleanup_succeeded"], true), "Expected NAME changes do not invalidate identity-only capture.");
        Scenario.Reset(); Scenario.CopyChildren(); Scenario.DeleteReturnsFalse = true;
        result = Run(true);
        Check(Scenario.Deletes == 1 && Equals(result["cleanup_succeeded"], false), "Delete false does not certify cleanup and is never retried.");
        Scenario.Reset(); Scenario.CopyChildren(); Scenario.ThrowAfterDelete = true;
        result = Run(true);
        Check(Scenario.Deletes == 1 && Equals(result["cleanup_succeeded"], true), "Independent deleted-state and full membership reads reconcile a thrown delete.");
        foreach (string fault in calls)
        {
            Scenario.Reset(); Scenario.CopyChildren(); Scenario.AfterCapture = () => Scenario.FaultAt = fault;
            bool faulted = false;
            try { Run(true); } catch (NativeDatabaseFaultException) { faulted = true; }
            Check(faulted && Scenario.NativeAfterFault == 0, "Fault stops subsequent SDK work at " + fault);
            Check(Scenario.Deletes <= 1, "Fault does not replay deletion at " + fault);
        }
    }
}

internal static class Scenario
{
    internal static E3d21Context Context;
    internal static DbElement Parent, Other, Target, Source;
    internal static string[] Original;
    internal static readonly List<DbElement> Existing = new List<DbElement>(), CopyNodes = new List<DbElement>();
    internal static readonly Dictionary<string, DbElement> Elements = new Dictionary<string, DbElement>();
    internal static readonly List<string> Calls = new List<string>();
    internal static string ThrowAt, FaultAt;
    internal static int Deletes, NativeAfterFault;
    internal static bool DeleteReturnsFalse, ThrowAfterDelete;
    internal static Action AfterCapture;
    internal static T Read<T>(string label, T value)
    {
        if (Context.HasFault) { NativeAfterFault++; throw new Exception("SDK call after native fault: " + label); }
        Calls.Add(label);
        if (FaultAt == label) Context.Fault();
        if (ThrowAt == label) throw new InvalidOperationException("Incomplete SDK read at " + label);
        return value;
    }
    internal static DbElement Add(string label, int high, int low, string type, DbElement parent)
    { var value = new DbElement(label, high, low, type) { Parent = parent }; Elements[value.Key] = value; return value; }
    internal static void Reset()
    {
        Calls.Clear(); Elements.Clear(); Existing.Clear(); CopyNodes.Clear(); AfterCapture = null;
        ThrowAt = FaultAt = null; Deletes = NativeAfterFault = 0; DeleteReturnsFalse = ThrowAfterDelete = false;
        Context = new E3d21Context(); Parent = Add("parent", 2000000001, 490, "EQUI", null); Other = Add("other", 2000000001, 491, "EQUI", null);
        Source = Add("source", 2000000001, 10, "SUBE", Other); Other.Children.Add(Source);
        Parent.Children.Add(Add("existing", 2000000001, 20, "BOX", Parent));
        Existing.Add(Source); Existing.Add(Parent.Children[0]);
        Original = Parent.Children.Select(node => node.Key).ToArray();
        Target = Add("target", 2000000003, 1, "SUBE", Parent); Parent.Children.Add(Target);
    }
    internal static void CopyChildren()
    {
        var positive = Add("copy-positive", 2000000003, 2, "CYLI", Target);
        var negative = Add("copy-negative", 2000000003, 3, "NCYL", positive);
        var box = Add("copy-box", 2000000003, 4, "BOX", Target);
        Target.Children.Add(positive); Target.Children.Add(box); positive.Children.Add(negative);
        CopyNodes.AddRange(new[] { positive, negative, box });
    }
}

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        private const int CopyProtectionLimit = 10000, ModelMembershipVerificationLimit = 10000;
        private sealed class CopyNode { internal CopyTreeIdentity Identity; }
        private sealed class CopyState
        {
            internal DbElement Parent;
            internal string ParentReference;
            internal string[] ParentMembers;
            internal List<CopyNode> Nodes;
            internal HashSet<string> Protected;
            internal bool EmptyRootVerified;
            internal CopyTreeCapture AcceptedCopy;
        }
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;
        private readonly Action<Action> invokeOnUi = action => action();
        internal bool HasFault { get { return nativeFaultState.HasFault; } }
        internal void Fault() { nativeFaultState.Observe("Unhandled Database error 538 from CopyCleanupFake", ""); }
        private static DbElement ResolveElement(string reference)
        { return NativeRead(() => Scenario.Read("resolve." + reference, Scenario.Elements[reference])); }
        private static string ElementRef(DbElement value) { return ModelReference(NativeRead(() => value.Ref)); }
        private static IEnumerable<DbElement> PrimitiveDirectMembers(DbElement value) { return DirectSiblings(value, null); }
        internal Dictionary<string, object> RunCleanup(bool capture, bool emptyVerified, bool sourceUnchanged)
        {
            return OnUi(() =>
            {
                var protectedRefs = new HashSet<string>(Scenario.Existing.Select(node => node.Key).Concat(new[] { Scenario.Parent.Key, Scenario.Other.Key }));
                var state = new CopyState
                {
                    Parent = Scenario.Parent, ParentReference = Scenario.Parent.Key, ParentMembers = (string[])Scenario.Original.Clone(),
                    Protected = protectedRefs, EmptyRootVerified = emptyVerified,
                    Nodes = new List<CopyNode> { new CopyNode { Identity = new CopyTreeIdentity { Type = "SUBE" } } }
                };
                if (capture)
                {
                    var returned = CopyReadIdentities(Scenario.Target, CopyProtectionLimit);
                    // Use an independent source topology; no copied ref enters protected baseline.
                    var source = new List<CopyTreeIdentity> {
                        new CopyTreeIdentity { Reference = "=5/10", Owner = "=5/1", Type = "SUBE", Path = "" },
                        new CopyTreeIdentity { Reference = "=5/11", Owner = "=5/10", Type = "CYLI", Path = "0" },
                        new CopyTreeIdentity { Reference = "=5/12", Owner = "=5/11", Type = "NCYL", Path = "0/0" },
                        new CopyTreeIdentity { Reference = "=5/13", Owner = "=5/10", Type = "BOX", Path = "1" }
                    };
                    state.AcceptedCopy = CopyTreePolicy.CaptureReturnedTree(source, returned, Scenario.Target.Key,
                        state.ParentReference, protectedRefs, state.ParentMembers, ModelMemberReferences(state.Parent));
                }
                Scenario.Calls.Clear();
                if (Scenario.AfterCapture != null) Scenario.AfterCapture();
                var result = new Dictionary<string, object> { ["source_unchanged"] = sourceUnchanged,
                    ["cleanup_attempted"] = false, ["cleanup_succeeded"] = null, ["residual_refno"] = Scenario.Target.Key };
                CopyCleanup(result, state, Scenario.Target, (int[])Scenario.Target.Reference.Clone());
                return result;
            });
        }
        // PRODUCTION_METHODS
    }
}

namespace Aveva.Core.Database
{
    public enum DbType { Design }
    public sealed class DbAttribute { }
    public static class DbAttributeInstance { public static readonly DbAttribute NAME = new DbAttribute(); }
    public sealed class DbElementType
    {
        internal string Value;
        public string ShortName { get { return Scenario.Read("type.name", Value); } }
    }
    public sealed class DbElement
    {
        internal readonly string Label;
        internal readonly int[] Reference;
        internal readonly List<DbElement> Children = new List<DbElement>();
        internal string TypeName, Name;
        internal DbElement Parent;
        internal bool Valid = true, Deleted;
        internal DbElement(string label, int high, int low, string type) { Label = label; Reference = new[] { high, low }; TypeName = type; }
        internal string Key { get { return "=" + Reference[0] + "/" + Reference[1]; } }
        public int[] Ref { get { return Scenario.Read("ref." + Label, (int[])Reference.Clone()); } }
        public bool IsNull { get { return Scenario.Read("null." + Label, false); } }
        public bool IsValid { get { return Scenario.Read("valid." + Label, Valid); } }
        public bool IsDeleted { get { return Scenario.Read("deleted." + Label, Deleted); } }
        public DbType DbType { get { return Scenario.Read("dbtype." + Label, DbType.Design); } }
        public DbElement Owner { get { return Scenario.Read("owner." + Label, Parent); } }
        public DbElementType GetActualType() { return Scenario.Read("type." + Label, new DbElementType { Value = TypeName }); }
        public bool IsAttributeValid(DbAttribute value) { return Scenario.Read("hasname." + Label, true); }
        public string GetString(DbAttribute value) { return Scenario.Read("name." + Label, Name ?? Key); }
        public DbElement FirstMember() { return Scenario.Read("first." + Label, Children.FirstOrDefault()); }
        public DbElement Next()
        {
            int index = Parent.Children.IndexOf(this);
            return Scenario.Read("next." + Label, index + 1 < Parent.Children.Count ? Parent.Children[index + 1] : null);
        }
        public static DbElement GetElement(int[] reference)
        { string key = "=" + reference[0] + "/" + reference[1]; return Scenario.Read("lookup." + key, Scenario.Elements[key]); }
        private void DeleteSubtree() { foreach (DbElement child in Children) child.DeleteSubtree(); Valid = false; Deleted = true; }
        public bool Delete(out PdmsMessage message)
        {
            Scenario.Deletes++; message = null; Scenario.Read("delete.target", true);
            if (Scenario.DeleteReturnsFalse) return false;
            DeleteSubtree(); Parent.Children.Remove(this);
            if (Scenario.ThrowAfterDelete) throw new InvalidOperationException("Managed return failure after native delete.");
            return true;
        }
    }
}
namespace Aveva.Core.Utilities.Messaging
{
    public sealed class PdmsMessage { public string MessageText() { return Scenario.Read("message", "native diagnostic"); } }
}
