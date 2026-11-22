using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core.Geometry;
using Aveva.Core.Utilities.Messaging;
using E3d21AiBridge;

internal static class TransformNotificationHarness
{
    private static int checks;
    internal static void Check(bool ok, string message)
    { checks++; if (!ok) throw new Exception(message); }
    private static Dictionary<string, object> Run(bool dry = false, bool both = true)
    {
        return (Dictionary<string, object>)Scenario.Context.SetTransform("=1/2", new[] { 10.0, 20, 30 },
            both ? new[] { 0.0, 1, 0 } : null, both ? new[] { -1.0, 0, 0 } : null, "OWNER", dry);
    }
    private static Dictionary<string, object> Notification(Dictionary<string, object> result)
    { return (Dictionary<string, object>)result["notification_update"]; }
    private static void Fault(Action action)
    {
        try { action(); throw new Exception("Expected native fault."); }
        catch (NativeDatabaseFaultException) { Check(Scenario.NativeAfterFault == 0, "No SDK call after fault latch."); }
        Check(Scenario.Context.Ends == 1, "Command gate releases after fault.");
    }
    public static int Main()
    {
        Scenario.Reset();
        var result = Run(); var notification = Notification(result);
        Check(Equals(result["success"], true) && Equals(result["write_applied"], true), "Successful transform remains successful.");
        Check(Scenario.PosWrites == 1 && Scenario.OriWrites == 1 && Scenario.Updates == 1, "Two fields trigger one notification after the batch.");
        Check(Scenario.Trace.IndexOf("update") > Scenario.Trace.IndexOf("set.ori.1"), "Notification follows both setters.");
        Check(Equals(notification["completed"], true) && Equals(notification["readback_verified"], true), "Notification completion and fresh readback recorded.");
        Check((string)notification["verification_target"] == "submitted" && Equals(result["ce_unchanged"], true), "Success is checked against submitted transform and CE.");
        Check(Equals(notification["frame_identity_unchanged"], true) && Scenario.QualifiedReadsAfterUpdate == 2, "Post-notification reads resolve matching frame identities and use a fresh qualifier.");
        Check(((string)notification["scope"]).Contains("spatial-map") && ((string)notification["scope"]).Contains("side effects"), "Receipt retains exact effect limitations.");

        Scenario.Reset(); result = Run(false, false);
        Check(Scenario.Updates == 1 && Scenario.OriWrites == 0 && Equals(result["success"], true), "Position-only writes notify once.");
        Scenario.Reset(); result = (Dictionary<string, object>)Scenario.Context.SetTransform("=1/2", null, new[] { 0.0, 1, 0 }, new[] { -1.0, 0, 0 }, "OWNER", false);
        Check(Scenario.Updates == 1 && Scenario.PosWrites == 0 && Equals(result["success"], true), "Orientation-only writes notify once.");

        Scenario.Reset(); result = Run(true);
        Check(Scenario.Updates == 0 && Scenario.PosWrites == 0 && Scenario.OriWrites == 0, "Dry run never writes or notifies.");
        Check(Equals(Notification(result)["attempted"], false) && Equals(result["success"], true), "Dry run reports unattempted notification.");
        Scenario.Reset(); Scenario.PreflightFailure = true; result = Run();
        Check(Scenario.Updates == 0 && Scenario.PosWrites == 0 && Equals(result["success"], false), "Preflight failure never notifies.");
        Scenario.Reset(); Scenario.Busy = true; result = Run();
        Check(Scenario.Updates == 0 && Scenario.Trace.Count == 0 && Equals(result["busy"], true), "Busy request never dispatches.");

        Scenario.Reset(); Scenario.FailOrientationFirst = true; result = Run(); notification = Notification(result);
        Check(Scenario.PosWrites == 2 && Scenario.OriWrites == 2 && Scenario.Updates == 1, "Setter failure restores both attempted fields and notifies once.");
        Check(Scenario.Trace.IndexOf("update") > Scenario.Trace.IndexOf("set.pos.2"), "Notification runs after rollback finishes.");
        Check(Equals(result["rollback_succeeded"], true) && Equals(result["write_applied"], false) && Equals(result["success"], false), "Original failure and rollback result retained.");
        Check((string)notification["verification_target"] == "original_after_rollback" && Equals(notification["readback_verified"], true), "Rollback verification uses captured original, never submitted values.");
        Check(notification["error"] == null, "Successful rollback notification is not misreported as a target mismatch.");

        Scenario.Reset(); Scenario.FailPositionFirst = true; result = Run();
        Check(Scenario.PosWrites == 2 && Scenario.OriWrites == 0 && Scenario.Updates == 1, "First setter failure only restores attempted POS, then notifies.");
        Check(Equals(Notification(result)["readback_verified"], true), "Unattempted orientation remains at captured original during verification.");

        Scenario.Reset(); Scenario.UpdateThrows = true; result = Run(); notification = Notification(result);
        Check(Scenario.PosWrites == 1 && Scenario.OriWrites == 1 && Scenario.Updates == 1, "Notification failure cannot trigger a second write/rollback.");
        Check(Equals(result["write_applied"], true) && Equals(result["rollback_attempted"], false) && Equals(result["success"], false), "Notification failure retains applied write outcome.");
        Check(Equals(notification["attempted"], true) && Equals(notification["completed"], false) && notification["error"] != null, "Notification failure has separate receipt.");
        Scenario.Reset(); Scenario.FailOrientationFirst = Scenario.UpdateThrows = true; result = Run();
        Check(Scenario.PosWrites == 2 && Scenario.OriWrites == 2 && Scenario.Updates == 1 && Equals(result["rollback_succeeded"], true), "Failed notification after rollback never retries rollback.");
        Check(Equals(result["write_applied"], false) && ((string)result["error"]).Contains("Setting ORI"), "Original rollback/error data survives notification exception.");

        Scenario.Reset(); Scenario.Callback = () => Scenario.Target.PositionValue = Position.Raw(99, 98, 97); result = Run(); notification = Notification(result);
        Check(Equals(notification["completed"], true) && Equals(notification["readback_verified"], false) && Equals(result["success"], false), "Callback POS changes detected after successful Update.");
        Check(Scenario.PosWrites == 1 && Scenario.OriWrites == 1 && Equals(result["write_applied"], true), "Callback mismatch does not undo prior applied write.");
        Check((double)((Dictionary<string, object>)result["position"])["x"] == 99, "Fresh callback-modified transform is reported.");
        Scenario.Reset(); Scenario.Callback = () => Scenario.Target.OrientationValue = Orientation.Identity(); result = Run();
        Check(Equals(Notification(result)["readback_verified"], false) && Scenario.OriWrites == 1, "Callback ORI changes detected without replay.");
        Scenario.Reset(); Scenario.FailOrientationFirst = true; Scenario.Callback = () => Scenario.Target.PositionValue = Position.Raw(99, 0, 0); result = Run();
        Check(Equals(result["rollback_succeeded"], true) && Equals(result["write_applied"], false), "Pre-notification rollback outcome remains historical evidence.");
        Check(Equals(Notification(result)["readback_verified"], false) && Scenario.PosWrites == 2, "Post-rollback callback mismatch separately reported without another rollback.");
        Scenario.Reset(); Scenario.Callback = () => Scenario.Current = Scenario.Owner; result = Run();
        Check(Equals(result["success"], false) && Equals(result["ce_unchanged"], false) && Scenario.PosWrites == 1, "Callback CE change is observed without restoring CE or replaying writes.");
        foreach (string changedFrame in new[] { "owner", "requested", "storage" })
        {
            Scenario.Reset();
            Scenario.Callback = () =>
            {
                if (changedFrame == "owner") Scenario.Owner = new DbElement(40);
                if (changedFrame == "requested") Scenario.RequestedFrame = new DbElement(41);
                if (changedFrame == "storage") Scenario.StorageFrame = new DbElement(42);
            };
            result = Run(); notification = Notification(result);
            Check(Equals(result["success"], false) && Equals(notification["readback_verified"], false) && Equals(notification["frame_identity_unchanged"], false), "Changed " + changedFrame + " identity prevents false coordinate verification.");
            Check(Scenario.QualifiedReadsAfterUpdate == 0 && Scenario.PosWrites == 1 && Scenario.OriWrites == 1 && Equals(result["write_applied"], true), "Frame mismatch stops before stale-frame coordinate getters and never rewrites historical outcome.");
        }
        Scenario.Reset(); Scenario.FinalCeThrows = true; result = Run(); notification = Notification(result);
        Check(Equals(result["success"], false) && result["ce_unchanged"] == null && Equals(notification["readback_verified"], false)
            && notification["ce_unchanged"] == null && result.ContainsKey("ce_read_error"), "A failure only in the final CE getter invalidates success and latest notification verification.");
        Check(Scenario.CeReadsAfterUpdate == 2 && Scenario.PosWrites == 1 && Scenario.OriWrites == 1 && Equals(result["write_applied"], true), "Final CE verification failure never invokes restore or replay.");

        Scenario.Reset(); Scenario.ReadThrowsAfterUpdate = "get.pos"; result = Run();
        Check(Equals(result["success"], false) && Equals(Notification(result)["completed"], true) && Scenario.PosWrites == 1, "Post-update getter failure fails verification without rollback.");
        Scenario.Reset(); Scenario.FailOrientationFirst = Scenario.FailRollbackPosition = true; result = Run();
        Check(Equals(result["rollback_succeeded"], false) && Scenario.Updates == 1, "Even an incomplete rollback has a single notification after attempted writes.");
        Check(Equals(Notification(result)["readback_verified"], false) && Scenario.PosWrites == 2, "Incomplete rollback is compared to original and not retried.");

        foreach (string label in new[] { "set.pos.1", "set.ori.1", "update" })
        {
            Scenario.Reset(); Scenario.FaultAt = label; Fault(() => Run());
            Check(Scenario.Updates == (label == "update" ? 1 : 0), "No notification after a setter native fault.");
            Check(Scenario.PosWrites == 1 && Scenario.OriWrites <= 1, "Fault never triggers rollback.");
        }
        Scenario.Reset(); Scenario.FailOrientationFirst = true; Scenario.FaultAt = "set.ori.2"; Fault(() => Run());
        Check(Scenario.PosWrites == 1 && Scenario.Updates == 0, "Rollback native fault blocks remaining rollback and notification.");
        foreach (string label in new[] { "owner", "frame.resolve", "storage.resolve", "ref", "qualifier.set", "get.pos", "pos.x", "get.ori", "ori.x", "dir.east", "ce" })
        {
            Scenario.Reset(); Scenario.FaultAfterUpdateAt = label; Fault(() => Run());
            Check(Scenario.Updates == 1 && Scenario.PosWrites == 1 && Scenario.OriWrites == 1, "Post-notification native fault stops subsequent SDK access.");
        }
        Scenario.Reset(); Scenario.Callback = () => { Scenario.Context.Fault(); throw new Exception("secondary callback failure"); }; Fault(() => Run());
        Check(Scenario.Updates == 1 && Scenario.PosWrites == 1, "Latched native fault supersedes callback managed error.");
        Console.WriteLine("Transform notification production checks passed: " + checks);
        return 0;
    }
}

internal static class Scenario
{
    internal static E3d21Context Context;
    internal static DbElement Target, Owner, Current, RequestedFrame, StorageFrame;
    internal static int PosWrites, OriWrites, Updates, NativeAfterFault, QualifiedReadsAfterUpdate, CeReadsAfterUpdate;
    internal static bool Busy, PreflightFailure, FailPositionFirst, FailOrientationFirst, FailRollbackPosition, UpdateThrows, FinalCeThrows;
    internal static string FaultAt, FaultAfterUpdateAt, ReadThrowsAfterUpdate;
    internal static Action Callback;
    internal static readonly List<string> Trace = new List<string>();
    internal static T Read<T>(string label, T value)
    {
        if (Context.HasFault) { NativeAfterFault++; throw new Exception("SDK called after native fault: " + label); }
        Trace.Add(label);
        if (Updates > 0 && label == "ce" && ++CeReadsAfterUpdate == 2 && FinalCeThrows)
            throw new InvalidOperationException("Final CE getter unavailable.");
        if (FaultAt == label || (Updates > 0 && FaultAfterUpdateAt == label)) Context.Fault();
        if (Updates > 0 && ReadThrowsAfterUpdate == label) throw new InvalidOperationException("Injected read failure: " + label);
        return value;
    }
    internal static void Reset()
    {
        PosWrites = OriWrites = Updates = NativeAfterFault = QualifiedReadsAfterUpdate = CeReadsAfterUpdate = 0;
        Busy = PreflightFailure = FailPositionFirst = FailOrientationFirst = FailRollbackPosition = UpdateThrows = FinalCeThrows = false;
        RequestedFrame = StorageFrame = null;
        FaultAt = FaultAfterUpdateAt = ReadThrowsAfterUpdate = null; Callback = null; Trace.Clear();
        Context = new E3d21Context(); Owner = new DbElement(1); Target = new DbElement(2); Current = Target;
    }
}

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;
        private readonly Action<Action> invokeOnUi = action => action();
        internal int Ends;
        internal bool HasFault { get { return nativeFaultState.HasFault; } }
        internal void Fault() { nativeFaultState.Observe("Unhandled Database error 538 from TransformFake", ""); }
        private bool IsCommandActive() { return Scenario.Busy; }
        private bool TryBeginCommand(string command) { return !Scenario.Busy; }
        private void EndCommand() { Ends++; }
        private static object BusyResult(string command) { return new Dictionary<string, object> { ["success"] = false, ["busy"] = true }; }
        private static DbElement ModelRequireElement(string target)
        {
            if (Scenario.PreflightFailure) throw new InvalidOperationException("preflight rejected");
            return NativeRead(() => Scenario.Read("resolve", Scenario.Target));
        }
        private static DbElement ModelFrame(string wrt, DbElement owner) { return Scenario.Read("frame.resolve", Scenario.RequestedFrame ?? owner); }
        private static void ModelRequireStableFrame(DbElement element, DbElement frame) { }
        private static DbElement ModelStorageFrame(DbElement owner) { return Scenario.Read("storage.resolve", Scenario.StorageFrame ?? owner); }
        private static void ModelRequireStorageFrame(DbElement element, DbElement frame, ModelEditInput input) { }
        private static ModelEditFrame ModelStorageTransform(DbElement storage, DbElement frame) { return ModelEditFrame.Identity(); }
        private static Dictionary<string, object> ModelIdentity(DbElement element) { return new Dictionary<string, object> { ["refno"] = ModelReference(element.Ref) }; }
        private static Dictionary<string, object> ModelFrameIdentity(DbElement frame, string requested) { return ModelIdentity(frame); }
        // PRODUCTION_METHODS
    }
}

namespace Aveva.Core.Database
{
    public enum DbType { Design, Catalog }
    public sealed class DbAttribute { internal string Name; public string ShortName { get { return Name; } } public DbAttribute(string name) { Name = name; } }
    public static class DbAttributeInstance { public static readonly DbAttribute POS = new DbAttribute("POS"), ORI = new DbAttribute("ORI"); }
    public sealed class DbQualifier
    {
        private DbElement frame;
        internal bool SetAfterUpdate;
        public DbElement wrtQualifier { get { return frame; } set { frame = Scenario.Read("qualifier.set", value); SetAfterUpdate = Scenario.Updates > 0; } }
    }
    public static class CurrentElement { public static DbElement Element { get { return Scenario.Read("ce", Scenario.Current); } } }
    public sealed class DbElement
    {
        private readonly int id;
        internal Position PositionValue = Position.Raw(1, 2, 3);
        internal Orientation OrientationValue = Orientation.Identity();
        public DbElement(int value) { id = value; }
        public int[] Ref { get { return Scenario.Read("ref", new[] { 1, id }); } }
        public bool IsNull { get { return Scenario.Read("isnull", false); } }
        public DbType DbType { get { return Scenario.Read("dbtype", DbType.Design); } }
        public DbElement Owner { get { return Scenario.Read("owner", Scenario.Owner); } }
        public bool IsAttributeValid(DbAttribute a) { return Scenario.Read("valid." + a.Name, true); }
        public bool IsAttributeSetable(DbAttribute a) { return Scenario.Read("settable." + a.Name, true); }
        private void VerifyQualifier(DbQualifier qualifier)
        {
            if (qualifier != null && Scenario.Updates > 0)
            {
                Scenario.QualifiedReadsAfterUpdate++;
                if (!qualifier.SetAfterUpdate || !ReferenceEquals(qualifier.wrtQualifier, Scenario.RequestedFrame ?? Scenario.Owner))
                    throw new InvalidOperationException("Post-notification getter used stale qualifier.");
            }
        }
        public Position GetPosition(DbAttribute a, DbQualifier qualifier = null) { VerifyQualifier(qualifier); return Scenario.Read("get.pos", PositionValue.Clone()); }
        public Orientation GetOrientation(DbAttribute a, DbQualifier qualifier = null) { VerifyQualifier(qualifier); return Scenario.Read("get.ori", OrientationValue.Clone()); }
        public bool SetAttribute(DbAttribute a, DbQualifier q, Position value, out PdmsMessage message)
        {
            int count = ++Scenario.PosWrites; message = null;
            if (!(Scenario.FailRollbackPosition && count == 2)) PositionValue = value.Clone();
            return Scenario.Read("set.pos." + count, !(Scenario.FailPositionFirst && count == 1) && !(Scenario.FailRollbackPosition && count == 2));
        }
        public bool SetAttribute(DbAttribute a, DbQualifier q, Orientation value, out PdmsMessage message)
        {
            int count = ++Scenario.OriWrites; message = null; OrientationValue = value.Clone();
            return Scenario.Read("set.ori." + count, !(Scenario.FailOrientationFirst && count == 1));
        }
    }
}

namespace Aveva.Core.Geometry
{
    public enum Axis { X, Y, Z }
    public sealed class Position
    {
        internal double[] Vector;
        public double X { get { return Scenario.Read("pos.x", Vector[0]); } }
        public double Y { get { return Scenario.Read("pos.y", Vector[1]); } }
        public double Z { get { return Scenario.Read("pos.z", Vector[2]); } }
        internal static Position Raw(double x, double y, double z) { return new Position { Vector = new[] { x, y, z } }; }
        public static Position Create(double x, double y, double z) { return Scenario.Read("position.create", Raw(x, y, z)); }
        internal Position Clone() { return Raw(Vector[0], Vector[1], Vector[2]); }
    }
    public sealed class Direction
    {
        internal double[] Vector;
        public double East { get { return Scenario.Read("dir.east", Vector[0]); } }
        public double North { get { return Scenario.Read("dir.north", Vector[1]); } }
        public double Up { get { return Scenario.Read("dir.up", Vector[2]); } }
        public static Direction Create(Position p) { return Scenario.Read("direction.create", new Direction { Vector = (double[])p.Vector.Clone() }); }
    }
    public sealed class Orientation
    {
        internal double[] X, Y, Z;
        internal static Orientation Identity() { return new Orientation { X = new[] { 1.0, 0, 0 }, Y = new[] { 0.0, 1, 0 }, Z = new[] { 0.0, 0, 1 } }; }
        internal Orientation Clone() { return new Orientation { X = (double[])X.Clone(), Y = (double[])Y.Clone(), Z = (double[])Z.Clone() }; }
        public static Orientation Create(Axis a, Direction x, Axis b, Direction y)
        {
            return Scenario.Read("orientation.create", new Orientation
            {
                X = (double[])x.Vector.Clone(), Y = (double[])y.Vector.Clone(),
                Z = new[] { x.Vector[1]*y.Vector[2]-x.Vector[2]*y.Vector[1], x.Vector[2]*y.Vector[0]-x.Vector[0]*y.Vector[2], x.Vector[0]*y.Vector[1]-x.Vector[1]*y.Vector[0] }
            });
        }
        public Direction XDir() { return Scenario.Read("ori.x", new Direction { Vector = (double[])X.Clone() }); }
        public Direction YDir() { return Scenario.Read("ori.y", new Direction { Vector = (double[])Y.Clone() }); }
        public Direction ZDir() { return Scenario.Read("ori.z", new Direction { Vector = (double[])Z.Clone() }); }
    }
}

namespace Aveva.Core.Utilities.Messaging
{
    public sealed class PdmsMessage { public string MessageText() { return Scenario.Read("message", "diagnostic"); } }
}
namespace Aveva.Core.Utilities.CommandLine
{
    public static class Command
    {
        public static void Update()
        {
            Scenario.Updates++; Scenario.Read("update", true);
            if (Scenario.Callback != null) Scenario.Callback();
            if (Scenario.UpdateThrows) throw new InvalidOperationException("Injected Update failure.");
        }
    }
}
