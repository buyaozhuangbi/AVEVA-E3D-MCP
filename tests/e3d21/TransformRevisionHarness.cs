using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Aveva.Core.Database;
using Aveva.Core.Geometry;
using Aveva.Core.Utilities.Messaging;
using E3d21AiBridge;

internal static class TransformRevisionHarness
{
    private static int checks;
    internal static void Check(bool ok, string message)
    { checks++; if (!ok) throw new Exception(message); }
    private static Dictionary<string, object> Preview(double x = 10, string wrt = "OWNER", string expected = null,
        bool includeOrientation = false)
    {
        return (Dictionary<string, object>)Scenario.Context.SetTransformRevisioned("=1/2", new[] { x, 20.0, 30.0 },
            includeOrientation ? new[] { 1.0, 0, 0 } : null,
            includeOrientation ? new[] { 0.0, 1, 0 } : null, wrt, expected, true);
    }
    private static Dictionary<string, object> Execute(string revision, double x = 10, string wrt = "OWNER",
        bool includeOrientation = false)
    {
        return (Dictionary<string, object>)Scenario.Context.SetTransformRevisioned("=1/2", new[] { x, 20.0, 30.0 },
            includeOrientation ? new[] { 1.0, 0, 0 } : null,
            includeOrientation ? new[] { 0.0, 1, 0 } : null, wrt, revision, false);
    }
    public static int Main()
    {
        Scenario.Reset();
        var preview = Preview();
        string token = preview["revision"] as string;
        Check(Equals(preview["success"], true) && token != null && token.Length == 64 && token.All(c => c >= '0' && c <= '9' || c >= 'a' && c <= 'f'),
            "Dry-run returns a lowercase SHA-256 revision.");
        Check(Equals(preview["revision_conflict"], false) && Equals(preview["write_attempted"], false)
            && Scenario.PosWrites == 0 && Scenario.OriWrites == 0 && Scenario.Updates == 0,
            "Preview is successful and performs no setter or notification.");
        Scenario.Reset();
        var previewWithToken = Preview(expected: new string('a', 64));
        Check(Equals(previewWithToken["success"], true) && Scenario.PosWrites == 0,
            "Revisioned preview accepts an optional well-formed expected_revision without writing.");
        Check(RejectArguments(null, false), "Revisioned execution rejects a missing revision before reads.");
        Check(RejectArguments("bad", false), "Revisioned execution rejects malformed revision before reads.");

        Scenario.Reset(); token = (string)Preview()["revision"];
        var result = Execute(token);
        Check(Equals(result["success"], true) && Equals(result["revision_conflict"], false)
            && Equals(result["write_attempted"], true) && Equals(result["write_applied"], true),
            "A current revision authorizes the exact previewed transform.");
        Check(Scenario.PosWrites == 1 && Scenario.OriWrites == 0 && Scenario.Updates == 1
            && Equals(((Dictionary<string, object>)result["notification_update"])["readback_verified"], true),
            "Correct revision executes one write and one verified notification.");

        Scenario.Reset(); token = (string)Preview()["revision"];
        result = Execute(token, 11);
        CheckStale(result, "requested transform drift");
        foreach (string drift in new[] { "project", "mdb_name", "ce", "target_pose", "frame_ref", "frame_pose", "mdb_membership" })
        {
            Scenario.Reset(); token = (string)Preview(wrt: drift == "frame_ref" || drift == "frame_pose" ? "/FRAME" : "OWNER")["revision"];
            Scenario.ApplyDrift(drift);
            result = Execute(token, wrt: drift == "frame_ref" || drift == "frame_pose" ? "/FRAME" : "OWNER");
            CheckStale(result, drift + " changed after preview");
        }

        Scenario.Reset(); Scenario.Mdb.Databases = new[] { Scenario.MainDatabase, Scenario.Database(101, 1, "AMBIGUOUS", 9) };
        preview = Preview();
        Check(!Equals(preview["success"], true) && preview["revision"] == null && Scenario.PosWrites == 0,
            "Same-number multiple extracts prevent a transform revision.");

        // The target can belong to a later Design database in the MDB.  Its
        // database WORLD must be the revision anchor; an unavailable first
        // Design WORLD must not block a valid target-scoped preview.
        Scenario.Reset();
        var targetDatabase = Scenario.Database(202, 0, "TARGET", 2);
        Scenario.MainDatabase.World = null;
        Scenario.Mdb.Databases = new[] { Scenario.MainDatabase, targetDatabase };
        Scenario.World = targetDatabase.World;
        Scenario.Owner = new DbElement("target-owner", new[] { 2, 1 }, "EQUI", targetDatabase) { OwnerValue = Scenario.World };
        Scenario.Target = new DbElement("target", new[] { 2, 2 }, "EQUI", targetDatabase) { OwnerValue = Scenario.Owner };
        Scenario.RequestedFrame = new DbElement("target-frame", new[] { 2, 3 }, "EQUI", targetDatabase) { OwnerValue = Scenario.World };
        Scenario.Current = Scenario.Target;
        preview = Preview();
        Check(Equals(preview["success"], true) && preview["revision"] is string && Scenario.PosWrites == 0,
            "A transform revision uses the target Design database WORLD rather than the first MDB Design WORLD.");

        Scenario.Reset(); Scenario.FaultAt = "db.main.number";
        try { Preview(); throw new Exception("Expected native fault while reading revision state."); }
        catch (NativeDatabaseFaultException) { }
        Check(Scenario.NativeAfterFault == 0 && Scenario.Trace.Last() == "db.main.number"
            && Scenario.PosWrites == 0 && Scenario.OriWrites == 0 && Scenario.Updates == 0,
            "A fault during state capture prevents every subsequent native read and write.");

        // Faults can be reported by the SDK while its getter still returns a
        // value.  Every individual revision getter must stop before calling
        // the next one, including getters grouped in a conditional or array.
        foreach (string fault in new[] { "target.valid.NAME", "target.string.NAME", "pos.x", "pos.y",
            "ori.x", "ori.y", "dir.east", "dir.north", "type.WORL.shortname", "world.MAIN.db",
            "type.EQUI.shortname" })
        {
            foreach (bool dryRun in new[] { true, false })
            {
                Scenario.Reset();
                token = dryRun ? null : (string)Preview(includeOrientation: true)["revision"];
                Scenario.RevisionStarted = false;
                Scenario.RevisionFaultAt = fault;
                bool faulted = false;
                try
                {
                    if (dryRun) Preview(includeOrientation: true);
                    else Execute(token, includeOrientation: true);
                }
                catch (NativeDatabaseFaultException) { faulted = true; }
                Check(faulted && Scenario.NativeAfterFault == 0 && Scenario.Trace.Last() == fault,
                    "Revision getter fault stops all native continuation: " + fault + " dryRun=" + dryRun);
                Check(Scenario.PosWrites == 0 && Scenario.OriWrites == 0 && Scenario.Updates == 0,
                    "Revision getter fault prevents setters and notification: " + fault + " dryRun=" + dryRun);
            }
        }

        // This helper is shared with create preflight. Exercise its actual
        // owner-chain walk, rather than the transform frame-policy stubs.
        foreach (string fault in new[] { "target.dbtype", "target.ref", "target.element_type",
            "target.actual_type", "type.EQUI.shortname", "target.valid.POS",
            "target.valid.ORI", "target.get.pos", "pos.x", "pos.y", "target.get.ori", "ori.x", "ori.y",
            "target.rules", "attribute.POS.shortname", "target.owner", "world.MAIN.db", "type.WORL.shortname" })
        {
            Scenario.Reset(); Scenario.FaultAt = fault;
            bool faulted = false;
            try { Scenario.Context.ProbeCreateAncestorRevision(); }
            catch (NativeDatabaseFaultException) { faulted = true; }
            Check(faulted && Scenario.NativeAfterFault == 0 && Scenario.Trace.Last() == fault,
                "Create ancestor getter fault stops all native continuation: " + fault);
            Check(Scenario.PosWrites == 0 && Scenario.OriWrites == 0 && Scenario.Updates == 0,
                "Create ancestor getter fault never writes: " + fault);
        }

        Scenario.Reset();
        Scenario.Owner.TypeName = "SITE"; Scenario.Owner.IsWorldValue = true;
        Check(!Scenario.World.GetElementType().IsWorld && Scenario.Owner.GetElementType().IsWorld,
            "The fixture matches E3D21 SITE=true and WORL=false IsWorld metadata.");
        token = (string)Preview()["revision"];
        Check(Equals(Execute(token)["success"], true) && Scenario.PosWrites == 1,
            "Guarded transforms accept a physical WORL with IsWorld=false.");
        Scenario.Reset(); Scenario.Owner.TypeName = "SITE"; Scenario.Owner.IsWorldValue = true;
        string ancestorRevision = Scenario.Context.ProbeCreateAncestorRevision();
        Check(ancestorRevision.Length == 64 && Scenario.Trace.Contains("owner.owner"),
            "Create identity traversal passes SITE=true and reaches the exact native WORL.");
        Scenario.Trace.Clear(); Scenario.Owner.PositionValid = Scenario.Owner.OrientationValid = false;
        Check(Scenario.Context.ProbeNativeStorageFrame(Scenario.Owner) == "=1/0"
            && Scenario.Trace.Contains("owner.owner"),
            "Native storage-frame search passes a non-positioning SITE and accepts WORL=false as its root.");
        Scenario.Trace.Clear(); Scenario.Context.ProbeStableFrame(Scenario.Target, Scenario.Owner);
        Check(Scenario.Trace.Contains("owner.owner") && Scenario.Trace.Contains("world.MAIN.db"),
            "Stable-frame verification passes SITE and verifies the physical database WORL.");
        bool rejectedFrame = false;
        try { Scenario.Context.ProbeStableFrame(Scenario.World, Scenario.Owner); }
        catch (ArgumentException) { rejectedFrame = true; }
        Check(rejectedFrame, "SITE=true cannot hide the edited ancestor in the stable-frame owner walk.");
        foreach (int element in new[] { 0, 9 })
        {
            Scenario.Reset();
            DbElement impostor = new DbElement("impostor", new[] { element == 0 ? 8 : 1, element },
                "WORL", Scenario.MainDatabase);
            rejectedFrame = false;
            try { Scenario.Context.ProbeNativeStorageFrame(impostor); }
            catch (InvalidOperationException) { rejectedFrame = true; }
            Check(rejectedFrame, "A WORL candidate must have element zero and match its exact database World: " + element);
        }

        Scenario.Reset(); token = (string)Preview()["revision"];
        Scenario.UpdateThrows = true; result = Execute(token);
        var notification = (Dictionary<string, object>)result["notification_update"];
        Check(Equals(result["success"], false) && Equals(result["write_applied"], true)
            && Equals(result["rollback_attempted"], false),
            "Notification failure preserves the original successful write outcome.");
        Check(Scenario.PosWrites == 1 && Scenario.Updates == 1 && Equals(notification["attempted"], true)
            && Equals(notification["completed"], false) && notification["error"] != null,
            "Notification failure is reported separately and never replays the setter.");

        Console.WriteLine("Transform revision production checks passed: " + checks);
        return 0;
    }

    private static bool RejectArguments(string expected, bool dryRun)
    {
        int reads = Scenario.Trace.Count;
        try { Scenario.Context.SetTransformRevisioned("=1/2", new[] { 10.0, 20, 30 }, null, null, "OWNER", expected, dryRun); }
        catch (ArgumentException) { return Scenario.Trace.Count == reads && Scenario.PosWrites == 0; }
        return false;
    }

    private static void CheckStale(Dictionary<string, object> result, string label)
    {
        Check(Equals(result["success"], false) && Equals(result["revision_conflict"], true)
            && Equals(result["write_attempted"], false) && Equals(result["retry_safe"], true),
            "Stale state rejects before writing: " + label);
        Check(Scenario.PosWrites == 0 && Scenario.OriWrites == 0 && Scenario.Updates == 0
            && Equals(result["rollback_attempted"], false), "Stale state has no setter/rollback/notification: " + label);
    }
}

internal static class Scenario
{
    internal static E3d21Context Context;
    internal static DbElement Target, Owner, Current, RequestedFrame, StorageFrame, World, ActiveFrame;
    internal static Db MainDatabase;
    internal static MDB Mdb;
    internal static Project Project;
    internal static int PosWrites, OriWrites, Updates, NativeAfterFault, QualifiedReadsAfterUpdate, CeReadsAfterUpdate;
    internal static bool Busy, PreflightFailure, FailPositionFirst, FailOrientationFirst, FailRollbackPosition, UpdateThrows, FinalCeThrows;
    internal static string FaultAt, FaultAfterUpdateAt, ReadThrowsAfterUpdate;
    internal static string RevisionFaultAt;
    internal static bool RevisionStarted;
    internal static Action Callback;
    internal static readonly List<string> Trace = new List<string>();
    internal static T Read<T>(string label, T value)
    {
        if (Context.HasFault) { NativeAfterFault++; throw new Exception("SDK called after native fault: " + label); }
        Trace.Add(label);
        if (Updates > 0 && label == "ce" && ++CeReadsAfterUpdate == 2 && FinalCeThrows)
            throw new InvalidOperationException("Final CE getter unavailable.");
        if (FaultAt == label || (RevisionStarted && RevisionFaultAt == label) ||
            (Updates > 0 && FaultAfterUpdateAt == label)) Context.Fault();
        if (Updates > 0 && ReadThrowsAfterUpdate == label) throw new InvalidOperationException("Injected read failure: " + label);
        return value;
    }
    internal static void ApplyDrift(string drift)
    {
        switch (drift)
        {
            case "project": Project.Name = "OTHER-PROJECT"; break;
            case "mdb_name": Mdb.Name = "OTHER-MDB"; break;
            case "ce": Current = new DbElement("ce", new[] { 9, 90 }, "EQUI", MainDatabase); break;
            case "target_pose": Target.PositionValue = Position.Raw(88, 2, 3); break;
            case "frame_ref": RequestedFrame.Reference = new[] { 8, 80 }; break;
            case "frame_pose": (StorageFrame ?? Owner).FramePosition = Position.Raw(5, 0, 0); break;
            case "mdb_membership": Mdb.Databases = new[] { MainDatabase, Database(202, 0, "OTHER", 2) }; break;
            default: throw new ArgumentException("Unknown drift: " + drift);
        }
    }
    internal static Db Database(int number, int extract, string name, int namespaceValue)
    {
        var value = new Db("db." + name.ToLowerInvariant())
        { NumberValue = number, ExtractNumberValue = extract, NameValue = name, TypeValue = DbType.Design };
        value.World = new DbElement("world." + name, new[] { namespaceValue, 0 }, "WORL", value) { IsWorldValue = false };
        return value;
    }
    internal static void Reset()
    {
        Context = new E3d21Context();
        PosWrites = OriWrites = Updates = NativeAfterFault = QualifiedReadsAfterUpdate = CeReadsAfterUpdate = 0;
        Busy = PreflightFailure = FailPositionFirst = FailOrientationFirst = FailRollbackPosition = UpdateThrows = FinalCeThrows = false;
        RequestedFrame = StorageFrame = ActiveFrame = null; MainDatabase = Database(101, 0, "MAIN", 1);
        Mdb = new MDB { Name = "MDB", Databases = new[] { MainDatabase } }; MDB.CurrentMDB = Mdb;
        Project = new Project { Name = "PROJECT" }; Aveva.Core.Database.Project.CurrentProject = Project;
        World = MainDatabase.World; Owner = new DbElement("owner", new[] { 1, 1 }, "EQUI", MainDatabase) { OwnerValue = World };
        Target = new DbElement("target", new[] { 1, 2 }, "EQUI", MainDatabase) { OwnerValue = Owner };
        RequestedFrame = new DbElement("frame", new[] { 1, 3 }, "EQUI", MainDatabase) { OwnerValue = World };
        Current = Target;
        FaultAt = FaultAfterUpdateAt = ReadThrowsAfterUpdate = RevisionFaultAt = null;
        RevisionStarted = false; Callback = null; Trace.Clear();
    }
}

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        private const int ModelMembershipVerificationLimit = 10000;
        private static readonly string createRevisionSession = Guid.NewGuid().ToString("N");
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
        private static void ThrowIfNativeFaulted()
        { if (currentNativeFaultState != null && currentNativeFaultState.HasFault) throw new NativeDatabaseFaultException(); }
        private static T NativeRead<T>(Func<T> read)
        { ThrowIfNativeFaulted(); try { return read(); } finally { ThrowIfNativeFaulted(); } }
        private static void NativeWrite(Action write)
        { ThrowIfNativeFaulted(); try { write(); } finally { ThrowIfNativeFaulted(); } }
        private T OnUi<T>(Func<T> action)
        {
            NativeDatabaseFaultState prior = currentNativeFaultState;
            currentNativeFaultState = nativeFaultState;
            try { T result = action(); ThrowIfNativeFaulted(); return result; }
            finally { currentNativeFaultState = prior; }
        }
        private static bool IsUsable(DbElement element)
        { return element != null && NativeRead(() => element.IsValid) && !NativeRead(() => element.IsNull); }
        private static MDB CurrentMdb()
        {
            Scenario.RevisionStarted = true;
            return NativeRead(() => MDB.CurrentMDB);
        }
        private static DbElement ModelRequireElement(string target)
        {
            if (Scenario.PreflightFailure) throw new InvalidOperationException("preflight rejected");
            return NativeRead(() => Scenario.Read("resolve." + target, Scenario.Target));
        }
        private static DbElement ModelFrame(string wrt, DbElement owner)
        {
            DbElement frame = wrt == "OWNER" ? owner : wrt == "WORLD" ? Scenario.World :
                NativeRead(() => Scenario.Read("frame.resolve", Scenario.RequestedFrame ?? owner));
            Scenario.ActiveFrame = frame; return frame;
        }
        private static void ModelRequireStableFrame(DbElement element, DbElement frame) { }
        private static DbElement ModelStorageFrame(DbElement owner) { return NativeRead(() => Scenario.Read("storage.resolve", Scenario.StorageFrame ?? owner)); }
        private static void ModelRequireStorageFrame(DbElement element, DbElement frame, ModelEditInput input) { }
        private static ModelEditFrame ModelStorageTransform(DbElement storage, DbElement frame) { return ModelEditFrame.Identity(); }
        private static Dictionary<string, object> ModelIdentity(DbElement element) { return new Dictionary<string, object> { ["refno"] = ModelReference(element.Ref) }; }
        private static Dictionary<string, object> ModelFrameIdentity(DbElement frame, string requested) { return ModelIdentity(frame); }
        internal string ProbeCreateAncestorRevision()
        {
            return OnUi(() =>
            {
                CreateMdbIdentity mdb = ModelCreateReadMdbIdentity(Scenario.Mdb);
                string world = ModelCreateWorldReference(Scenario.World);
                return PrimitiveHash(writer => ModelCreateWriteAncestorChain(writer, Scenario.Target,
                    Scenario.World, world, mdb));
            });
        }
        internal string ProbeNativeStorageFrame(DbElement owner)
        { return OnUi(() => ModelReference(ProbeStorageFrameCore(owner).Ref)); }
        internal void ProbeStableFrame(DbElement target, DbElement frame)
        { OnUi(() => { ProbeStableFrameCore(target, frame); return true; }); }
        // PRODUCTION_METHODS
    }
}

namespace Aveva.Core.Database
{
    public enum DbType { Design, Catalog }
    public sealed class DbAttribute { internal string Name; public string ShortName { get { return Scenario.Read("attribute." + Name + ".shortname", Name); } } public DbAttribute(string name) { Name = name; } }
    public static class DbAttributeInstance { public static readonly DbAttribute POS = new DbAttribute("POS"), ORI = new DbAttribute("ORI"), NAME = new DbAttribute("NAME"); }
    public sealed class DbElementType
    {
        private readonly string name;
        private readonly bool world;
        public string ShortName { get { return Scenario.Read("type." + name + ".shortname", name); } }
        public string Name { get { return Scenario.Read("type." + name + ".name", name); } }
        public bool IsWorld { get { return Scenario.Read("type." + name + ".isworld", world); } }
        internal DbElementType(string name, bool world) { this.name = name; this.world = world; }
    }
    public sealed class DbQualifier
    {
        private DbElement frame;
        internal bool SetAfterUpdate;
        public DbElement wrtQualifier { get { return frame; } set { frame = Scenario.Read("qualifier.set", value); SetAfterUpdate = Scenario.Updates > 0; } }
    }
    public static class CurrentElement { public static DbElement Element { get { return Scenario.Read("ce", Scenario.Current); } } }
    public sealed class Db
    {
        private readonly string key;
        internal int NumberValue, ExtractNumberValue; internal string NameValue; internal DbType TypeValue; private DbElement world;
        internal Db(string key) { this.key = key; }
        public int Number { get { return Scenario.Read(key + ".number", NumberValue); } }
        public int ExtractNumber { get { return Scenario.Read(key + ".extract", ExtractNumberValue); } }
        public string Name { get { return Scenario.Read(key + ".name", NameValue); } }
        public DbType Type { get { return Scenario.Read(key + ".type", TypeValue); } }
        public DbElement World { get { return Scenario.Read(key + ".world", world); } set { world = value; } }
    }
    public sealed class MDB
    {
        public static MDB CurrentMDB;
        public string Name; public Db[] Databases;
        public Db[] GetDBArray() { return Scenario.Read("mdb.databases", Databases); }
    }
    public sealed class Project
    {
        public static Project CurrentProject; public string Name;
    }
    public sealed class DbElement
    {
        private readonly string key;
        internal int[] Reference; internal string TypeName, NameValue; internal Db Database;
        internal DbElement OwnerValue; internal bool IsWorldValue;
        internal bool PositionValid = true, OrientationValid = true;
        internal Position PositionValue = Position.Raw(1, 2, 3);
        internal Orientation OrientationValue = Orientation.Identity();
        internal Position FramePosition = Position.Raw(0, 0, 0); internal Orientation FrameOrientation = Orientation.Identity();
        public DbElement(string key, int[] reference, string type, Db database) { this.key = key; Reference = reference; TypeName = type; Database = database; NameValue = "/" + type; }
        public int[] Ref { get { return (int[])Scenario.Read(key + ".ref", Reference).Clone(); } }
        public bool IsNull { get { return Scenario.Read(key + ".isnull", false); } }
        public bool IsValid { get { return Scenario.Read(key + ".valid", true); } }
        public DbType DbType { get { return Scenario.Read(key + ".dbtype", Database == null ? DbType.Catalog : Database.Type); } }
        public Db Db { get { return Scenario.Read(key + ".db", Database); } }
        public DbElement Owner { get { return Scenario.Read(key + ".owner", OwnerValue); } }
        public DbElementType GetElementType() { return Scenario.Read(key + ".element_type", new DbElementType(TypeName, IsWorldValue)); }
        public DbElementType GetActualType() { return Scenario.Read(key + ".actual_type", new DbElementType(TypeName, IsWorldValue)); }
        public bool IsAttributeValid(DbAttribute a)
        { return Scenario.Read(key + ".valid." + a.Name, a.Name == "POS" ? PositionValid : a.Name == "ORI" ? OrientationValid : true); }
        public string GetString(DbAttribute a) { return Scenario.Read(key + ".string." + a.Name, NameValue); }
        private void VerifyQualifier(DbQualifier qualifier)
        {
            if (qualifier != null && Scenario.Updates > 0)
            {
                Scenario.QualifiedReadsAfterUpdate++;
                if (!qualifier.SetAfterUpdate || !ReferenceEquals(qualifier.wrtQualifier, Scenario.ActiveFrame ?? Scenario.Owner))
                    throw new InvalidOperationException("Post-notification getter used stale qualifier.");
            }
        }
        public Position GetPosition(DbAttribute a, DbQualifier qualifier = null)
        {
            VerifyQualifier(qualifier);
            Position value = qualifier != null && ReferenceEquals(this, Scenario.StorageFrame ?? Scenario.Owner)
                && !ReferenceEquals(qualifier.wrtQualifier, this) ? FramePosition : PositionValue;
            return Scenario.Read(key + ".get.pos", value.Clone());
        }
        public Orientation GetOrientation(DbAttribute a, DbQualifier qualifier = null)
        {
            VerifyQualifier(qualifier);
            Orientation value = qualifier != null && ReferenceEquals(this, Scenario.StorageFrame ?? Scenario.Owner)
                && !ReferenceEquals(qualifier.wrtQualifier, this) ? FrameOrientation : OrientationValue;
            return Scenario.Read(key + ".get.ori", value.Clone());
        }
        public bool IsAttributeSetable(DbAttribute a) { return Scenario.Read(key + ".settable." + a.Name, true); }
        public DbAttribute[] GetAttributesWithRules()
        { return Scenario.Read(key + ".rules", new[] { DbAttributeInstance.POS, DbAttributeInstance.ORI }); }
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
