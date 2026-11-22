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

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;
        private const int ModelMembershipVerificationLimit = 10000;
        private static int checks, faultCases;
        private static void Check(bool value, string reason) { checks++; if (!value) throw new Exception(reason); }
        private static string Error(Dictionary<string, object> value) { return value.ContainsKey("error") ? Convert.ToString(value["error"]) : ""; }
        private static Dictionary<string, object> Map(object value) { return (Dictionary<string, object>)value; }
        private static E3d21Context Setup(string type = "DISH", double radius = 0)
        {
            Scenario.Reset(); currentNativeFaultState = new NativeDatabaseFaultState(); permittedNativeFaultGeneration = 0;
            Scenario.Latch = () => currentNativeFaultState.Observe("Unhandled Database error 538 from CopyDishFake", "test");
            Scenario.World = new DbElement(0, "WORL", null);
            Scenario.SourceOwner = new DbElement(1, "EQUI", Scenario.World);
            Scenario.Source = Make(2, type, Scenario.SourceOwner, radius);
            Scenario.Parent = new DbElement(3, "EQUI", Scenario.World);
            Scenario.Existing = Make(4, "BOX", Scenario.Parent);
            return new E3d21Context();
        }
        private static DbElement Make(int id, string type, DbElement owner, double radius = 0)
        {
            var e = new DbElement(id, type, owner);
            if (type == "DISH") { e.Values["DIAM"] = 100; e.Values["HEIG"] = 25; e.Values["RADI"] = radius; }
            if (type == "CYLI" || type == "NCYL") { e.Values["DIAM"] = 100; e.Values["HEIG"] = 25; }
            if (type == "BOX" || type == "NBOX") { e.Values["XLEN"] = 10; e.Values["YLEN"] = 20; e.Values["ZLEN"] = 30; }
            if (type == "CONE" || type == "NCON") { e.Values["DTOP"] = 0; e.Values["DBOT"] = 100; e.Values["HEIG"] = 25; }
            if (type == "PLOO") e.Values["HEIG"] = 5;
            if (type == "PAVE") { e.Values["FRAD"] = 0; e.Values["BANG"] = 0; }
            return e;
        }
        private Dictionary<string, object> Copy(bool preview = true, string revision = null)
        { return Map(CopyElement("=1/2", "=1/3", "/COPY", null, null, null, "OWNER", revision, preview)); }
        private static void PreviewSuccess(E3d21Context bridge, out string revision)
        {
            var preview = bridge.Copy();
            Check(Equals(preview["success"], true), "preview success: " + (preview.ContainsKey("error") ? preview["error"] : ""));
            Check(Scenario.Creates == 0 && Scenario.Copies == 0 && Scenario.Deletes == 0, "preview has no mutation");
            revision = (string)preview["revision"];
        }
        private static void TestDishRootAndSubtree()
        {
            foreach (double radius in new[] { 0.0, 40.0 })
            foreach (bool subtree in new[] { false, true })
            {
                var bridge = Setup(subtree ? "SUBE" : "DISH", radius);
                DbElement dish = subtree ? Make(5, "DISH", Scenario.Source, radius) : Scenario.Source;
                dish.StoredName = subtree ? "/HEAD" : "/SOURCE";
                dish.NativeScale = 25.4;
                var before = bridge.CopyReadTree(Scenario.Source);
                var node = before.First(x => x.Identity.Type == "DISH");
                Check(node.Dimensions.Keys.SequenceEqual(new[] { "DIAM", "HEIG", "RADI" }) && node.Dimensions["RADI"] == radius, "all DISH dimensions read as mm");
                string revision; PreviewSuccess(bridge, out revision);
                var result = bridge.Copy(false, revision);
                Check(Equals(result["success"], true) && Equals(result["geometry_verified"], true), "DISH copy verified: " + Error(result));
                Check(Scenario.Creates == 1 && Scenario.Copies == 1 && Scenario.Deletes == 0, "exactly one native create/copy");
                Check(Equals(result["source_unchanged"], true) && Equals(result["parent_membership_verified"], true), "source and original destination preserved");
                var copied = Scenario.CreatedRoot;
                var copiedDish = subtree ? copied.Children.Single() : copied;
                Check(copiedDish.Values["RADI"] == radius && copiedDish.Values["DIAM"] == 100 && copiedDish.Values["HEIG"] == 25, "RADI and other dimensions preserved");
                Check(copiedDish.NativeScale == 25.4, "native stored unit representation retained");
                Check(copied.StoredName == "/COPY" && (!subtree || copiedDish.StoredName == "/COPY-C0001"), "planned name mapping applied");
                Check(Scenario.Parent.Children.Select(x => x.Number).SequenceEqual(new[] { 4, copied.Number }), "parent ordered append verified");
                Check(!Scenario.Source.Deleted && !dish.Deleted && !Scenario.Existing.Deleted, "all original references retained");
                var mapping = (object[])result["name_mapping"];
                Check(mapping.Length == (subtree ? 2 : 1), "mapping covers full tree");
            }
        }
        private static void TestRevisionReadbackAndAdmission()
        {
            foreach (string field in new[] { "DIAM", "HEIG", "RADI" })
            {
                var bridge = Setup(radius: 40); string revision; PreviewSuccess(bridge, out revision);
                var before = bridge.CopyReadTree(Scenario.Source); string fingerprint = CopySourceFingerprint(before);
                Scenario.Source.Values[field] += 1;
                Check(CopySourceFingerprint(bridge.CopyReadTree(Scenario.Source)) != fingerprint, field + " enters source revision");
                var stale = bridge.Copy(false, revision);
                Check(Equals(stale["success"], false) && Equals(stale["revision_conflict"], true) && Scenario.Creates == 0, field + " stale revision rejects before creation");
                bridge = Setup(radius: 40); PreviewSuccess(bridge, out revision);
                Scenario.CorruptCopiedField = field;
                var mismatch = bridge.Copy(false, revision);
                Check(Equals(mismatch["success"], false) && Equals(mismatch["native_copy_result_captured"], true), field + " mismatch rejected after identity capture");
                Check(Equals(mismatch["cleanup_succeeded"], true) && Scenario.Deletes == 1 && Scenario.CreatedRoot.Deleted, field + " mismatch deletes only exact new copy");
                Check(Scenario.Parent.Children.Single() == Scenario.Existing && !Scenario.Source.Deleted, "mismatch cleanup protects existing references");
            }
            foreach (string type in new[] { "NDIS", "NDISH", "ADISH" })
            {
                var bridge = Setup(type); var rejected = bridge.Copy();
                Check(Equals(rejected["success"], false) && Scenario.Creates == 0, "unsupported type rejected: " + type);
            }
            foreach (string childType in new[] { "NCYL", "NBOX", "NCON", "NDIS", "BOX", "DISH" })
            {
                var bridge = Setup(); Make(5, childType, Scenario.Source);
                var rejected = bridge.Copy();
                Check(Equals(rejected["success"], false) && Scenario.Creates == 0, "DISH with any child rejected: " + childType);
            }
            var udet = Setup(); Scenario.Source.ActualType = new DbElementType("UDISH");
            Check(Equals(udet.Copy()["success"], false), "DISH UDET excluded");
            var ruled = Setup(); Scenario.Source.Rules.Add(new DbAttribute { Key = "RADI" });
            Check(Equals(ruled.Copy()["success"], false) && Scenario.Creates == 0, "RADI rules excluded");
            var unsupported = Setup("SUBE"); Make(5, "NDIS", Scenario.Source);
            Check(Equals(unsupported.Copy()["success"], false), "NDIS SUBE descendant excluded");
            var badOwner = Setup("BOX"); Make(5, "DISH", Scenario.Source);
            Check(Equals(badOwner.Copy()["success"], false), "DISH cannot become a primitive cut child");
            foreach (string field in new[] { "DIAM", "HEIG", "RADI" })
            {
                foreach (double invalid in new[] { -1.0, double.NaN, double.PositiveInfinity, double.NegativeInfinity })
                {
                    var bridge = Setup(); Scenario.Source.Values[field] = invalid;
                    Check(Equals(bridge.Copy()["success"], false) && Scenario.Creates == 0, "copy rejects invalid supported-domain " + field + "=" + invalid);
                }
                if (field != "RADI")
                {
                    var bridge = Setup(); Scenario.Source.Values[field] = 0;
                    Check(Equals(bridge.Copy()["success"], false) && Scenario.Creates == 0, "copy rejects zero " + field);
                }
            }
            var missing = Setup(); Scenario.Source.Values.Remove("RADI");
            Check(Equals(missing.Copy()["success"], false) && Scenario.Creates == 0, "copy rejects missing RADI instead of inventing zero");
        }
        private static void TestLegacyCopyRegression()
        {
            foreach (string shape in new[] { "CYLI", "BOX", "CONE" })
            {
                var bridge = Setup(shape);
                string cut = shape == "CYLI" ? "NCYL" : shape == "BOX" ? "NBOX" : "NCON";
                Make(5, cut, Scenario.Source);
                string revision; PreviewSuccess(bridge, out revision);
                var copied = bridge.Copy(false, revision);
                Check(Equals(copied["success"], true) && (int)copied["node_count"] == 2, "legacy positive/cut copy: " + shape + "/" + cut + " " + Error(copied));
                Check(Scenario.CreatedRoot.Children.Single().Type.Name == cut, "negative cut preserved " + cut);
            }
            foreach (bool subtree in new[] { false, true })
            {
                var bridge = Setup(subtree ? "SUBE" : "PANE");
                var pane = subtree ? Make(5, "PANE", Scenario.Source) : Scenario.Source;
                var loop = Make(6, "PLOO", pane);
                Make(7, "PAVE", loop); Make(8, "PAVE", loop); Make(9, "PAVE", loop); Make(10, "PAVE", loop);
                if (subtree) { Make(11, "BOX", Scenario.Source); Make(12, "DISH", Scenario.Source, 40); }
                string revision; PreviewSuccess(bridge, out revision);
                var result = bridge.Copy(false, revision);
                Check(Equals(result["success"], true) && Equals(result["geometry_verified"], true), "simple PANE/SUBE production copy path: " + Error(result));
                Check((int)result["node_count"] == (subtree ? 9 : 6), "full panel/SUBE mapping cardinality");
            }
        }
        private static void TestFailureCleanup()
        {
            var bridge = Setup(); string revision; PreviewSuccess(bridge, out revision);
            Scenario.ThrowCopyBeforePopulation = true;
            var failed = bridge.Copy(false, revision);
            Check(Equals(failed["success"], false) && Equals(failed["cleanup_succeeded"], true) && Scenario.Deletes == 1, "unreturned copy with exact empty new root is cleaned");
            Check(Equals(failed["cleanup_verification_scope"], "exact_empty_created_root"), "empty-root cleanup scope explicit");
            bridge = Setup("SUBE"); Make(5, "DISH", Scenario.Source, 40); PreviewSuccess(bridge, out revision);
            Scenario.ThrowCopyAfterPopulation = true;
            failed = bridge.Copy(false, revision);
            Check(Equals(failed["success"], false) && Equals(failed["cleanup_succeeded"], false) && Scenario.Deletes == 0,
                "uncaptured partial subtree cannot be deleted");
            Check(!Scenario.CreatedRoot.Deleted && !Scenario.Source.Deleted && Equals(failed["outcome_uncertain"], true), "uncaptured residual retained explicitly");
            bridge = Setup("SUBE"); Make(5, "DISH", Scenario.Source, 40); PreviewSuccess(bridge, out revision);
            Scenario.FailNameSetter = true;
            failed = bridge.Copy(false, revision);
            Check(Equals(failed["cleanup_succeeded"], true) && Scenario.Deletes == 1 && Scenario.CreatedRoot.Children.Single().Deleted,
                "post-capture name failure removes only complete exact accepted subtree");
            Check(Equals(failed["cleanup_captured_nodes_deleted"], true), "each captured node independently verified deleted");
            bridge = Setup("SUBE"); Make(5, "DISH", Scenario.Source, 40); PreviewSuccess(bridge, out revision);
            Scenario.AddForeignAfterName = true;
            failed = bridge.Copy(false, revision);
            Check(Equals(failed["success"], false) && Equals(failed["cleanup_succeeded"], false) && Scenario.Deletes == 0,
                "post-capture foreign child prevents any recursive deletion");
            Check(!Scenario.Existing.Deleted && !Scenario.Source.Deleted, "uncertain failure protects old geometry");
        }
        private static void TestRadiusFaults()
        {
            var bridge = Setup(radius: 40); Scenario.Source.Values["UDNUM"] = 7; bridge.CopyReadTree(Scenario.Source);
            var calls = Scenario.Calls.ToArray();
            foreach (int at in calls.Select((name, index) => new { name, index })
                .Where(x => x.name.Contains("RADI") || x.name.Contains("UDNUM") || x.name == "rules" || x.name == "attributes").Select(x => x.index + 1))
            foreach (bool throws in new[] { false, true })
            {
                bridge = Setup(radius: 40); Scenario.Source.Values["UDNUM"] = 7; Scenario.FaultAt = at; Scenario.ThrowWithFault = throws;
                bool faulted = false;
                try { bridge.CopyReadTree(Scenario.Source); } catch (NativeDatabaseFaultException) { faulted = true; }
                Check(faulted && Scenario.Calls.Count == at, "copy RADI fault stops at " + calls[at - 1] + ": " + string.Join(",", Scenario.Calls.Skip(at)));
                Check(Scenario.Creates == 0 && Scenario.Deletes == 0, "read fault cannot mutate"); faultCases++;
            }
            // A fault returned by the native copy or a subsequent NAME setter
            // forbids automatic cleanup or any later SDK continuation.
            bridge = Setup(); string revision; PreviewSuccess(bridge, out revision);
            Scenario.Calls.Clear(); bridge.Copy(false, revision);
            calls = Scenario.Calls.ToArray();
            foreach (int at in calls.Select((name, index) => new { name, index }).Where(x => x.name == "copy" || x.name == "set.NAME").Select(x => x.index + 1))
            foreach (bool throws in new[] { false, true })
            {
                bridge = Setup(); PreviewSuccess(bridge, out revision); Scenario.Calls.Clear();
                Scenario.FaultAt = at; Scenario.ThrowWithFault = throws;
                bool faulted = false;
                try { bridge.Copy(false, revision); } catch (NativeDatabaseFaultException) { faulted = true; }
                Check(faulted && Scenario.Calls.Count == at && Scenario.Deletes == 0, "copy/write fault forbids continuation and cleanup: " + calls[at - 1]);
                Check(Scenario.Creates == 1 && Scenario.Copies <= 1, "mutation is never replayed"); faultCases++;
            }
        }
        private static int Main()
        {
            try { TestDishRootAndSubtree(); TestRevisionReadbackAndAdmission(); TestLegacyCopyRegression(); TestFailureCleanup(); TestRadiusFaults();
                Console.WriteLine("DISH copy production checks passed: " + checks + "; native fault cases: " + faultCases); return 0; }
            catch (Exception error) { Console.Error.WriteLine(error); return 1; }
        }

        // Session/frame resolution is a managed fixture. Complete copy and cleanup
        // workflows, shape/revision/geometry policies are linked from production.
        private bool IsCommandActive() { return false; }
        private bool TryBeginCommand(string command) { return true; }
        private void EndCommand() { }
        private object BusyResult(string command) { throw new Exception("unexpected busy"); }
        private T OnUi<T>(Func<T> work) { ThrowIfNativeFaulted(); try { return work(); } finally { ThrowIfNativeFaulted(); } }
        private static DbElement ResolveElement(string reference) { return Scenario.Elements[reference]; }
        private static string ElementRef(DbElement element) { return ModelReference(NativeRead(() => element.Ref)); }
        private static MDB CurrentMdb() { return new MDB(); }
        private static DbElement ModelFrame(string name, DbElement owner) { return name == "OWNER" ? owner : Scenario.World; }
        private static DbElement ModelStorageFrame(DbElement owner) { return owner; }
        private sealed class StorageTransform { internal ModelEditInput ToNative(ModelEditInput input) { return input; } }
        private static StorageTransform ModelStorageTransform(DbElement a, DbElement b) { return new StorageTransform(); }
        private static void ModelRequireStorageFrame(DbElement element, DbElement frame, ModelEditInput input) { }
        private static Position ModelPosition(double[] p) { return new Position(p[0], p[1], p[2]); }
        private static Orientation ModelOrientation(ModelEditInput input) { return new Orientation(); }
        private static bool ModelOrientationsEqual(Orientation a, Orientation b) { return a != null && b != null; }
        private static void ModelWritePosition(DbElement element, Position value) { Scenario.Call("set.position"); element.Position = value; }
        private static void ModelWriteOrientation(DbElement element, Orientation value) { Scenario.Call("set.orientation"); }
        private static void ModelAddTransform(Dictionary<string, object> result, DbElement element, DbQualifier q, ModelEditInput input) { result["readback_verified"] = true; }
        private static string ModelCurrentReference() { return "=1/99"; }
        private static void ModelCeUnchanged(Dictionary<string, object> result) { result["ce_unchanged"] = Equals(result["ce_before"], result["ce_after"]); }
        private sealed class PanelVertex { internal double[] LocalPosition; }
        private sealed class PanelEditState { internal bool Eligible = true; internal string[] Reasons = new string[0]; internal List<PanelVertex> Vertices; }
        private static PanelEditState PanelReadEditState(DbElement element)
        { return new PanelEditState { Vertices = new[] { new[] { 0.0, 0.0 }, new[] { 10.0, 0.0 }, new[] { 10.0, 10.0 }, new[] { 0.0, 10.0 } }.Select(p => new PanelVertex { LocalPosition = p }).ToList() }; }
        // PRODUCTION_METHODS
    }
    internal static class Scenario
    {
        internal static DbElement World, SourceOwner, Source, Parent, Existing, CreatedRoot;
        internal static readonly Dictionary<string, DbElement> Elements = new Dictionary<string, DbElement>();
        internal static readonly List<string> Calls = new List<string>();
        internal static Action Latch;
        internal static int Creates, Copies, Deletes, NextId, FaultAt;
        internal static bool ThrowWithFault, ThrowCopyBeforePopulation, ThrowCopyAfterPopulation, FailNameSetter, AddForeignAfterName;
        internal static string CorruptCopiedField;
        internal static void Reset()
        { Elements.Clear(); Calls.Clear(); Creates = Copies = Deletes = FaultAt = 0; NextId = 100; CreatedRoot = null;
          ThrowWithFault = ThrowCopyBeforePopulation = ThrowCopyAfterPopulation = FailNameSetter = AddForeignAfterName = false; CorruptCopiedField = null; }
        internal static void Call(string name)
        { Calls.Add(name); if (Calls.Count == FaultAt) { Latch(); if (ThrowWithFault) throw new InvalidOperationException("native fault companion exception"); } }
    }
}

namespace Aveva.Core.Utilities.Messaging
{
    public sealed class PdmsMessage { public string MessageText() { E3d21AiBridge.Scenario.Call("message"); return "setter diagnostic"; } }
}
namespace Aveva.Core.Geometry
{
    public sealed class Position { public double X, Y, Z; public Position(double x = 0, double y = 0, double z = 0) { X = x; Y = y; Z = z; } }
    public sealed class Direction { public double East, North, Up; public Direction(double x, double y, double z) { East = x; North = y; Up = z; } }
    public sealed class Orientation
    { public Direction XDir() { return new Direction(1, 0, 0); } public Direction YDir() { return new Direction(0, 1, 0); } public Direction ZDir() { return new Direction(0, 0, 1); } }
}
namespace Aveva.Core.Database
{
    using E3d21AiBridge;
    public enum DbType { Design }
    public enum DbAttributeType { ELEMENT, STRINGARRAY, WORD, DOUBLE, INTEGER, BOOL, STRING, POSITION, DIRECTION, ORIENTATION, DATETIME }
    public enum DbDimension { DIST, OTHER }
    public sealed class DbAttribute
    {
        public string Key;
        public string ShortName { get { Scenario.Call("attribute.name." + Key); return Key; } }
        public bool IsValid { get { return true; } }
        public DbAttributeType Type { get { Scenario.Call("attribute.type." + Key); return Key == "NAME" ? DbAttributeType.STRING : new[] { "DIAM", "HEIG", "RADI", "XLEN", "YLEN", "ZLEN", "DTOP", "DBOT", "FRAD", "BANG", "UDNUM" }.Contains(Key) ? DbAttributeType.DOUBLE : DbAttributeType.ELEMENT; } }
        public bool IsArray { get { return false; } }
        public bool IsPseudo { get { return Key == "RADI" || Key == "HEIG"; } }
        public static DbAttribute GetDbAttribute(string name) { Scenario.Call("attribute." + name); return new DbAttribute { Key = name }; }
    }
    public static class DbAttributeInstance
    { public static readonly DbAttribute POS = new DbAttribute { Key = "POS" }, ORI = new DbAttribute { Key = "ORI" }, NAME = new DbAttribute { Key = "NAME" },
        HEIG = new DbAttribute { Key = "HEIG" }, SJUS = new DbAttribute { Key = "SJUS" }, LBLIND = new DbAttribute { Key = "LBLIND" },
        FRAD = new DbAttribute { Key = "FRAD" }, BANG = new DbAttribute { Key = "BANG" }, CTYA = new DbAttribute { Key = "CTYA" }, GTYP = new DbAttribute { Key = "GTYP" }, LNFA = new DbAttribute { Key = "LNFA" }; }
    public sealed class DbElementType
    {
        public string Name;
        public DbElementType(string name) { Name = name; }
        public string ShortName { get { Scenario.Call("type.short"); return Name; } }
        public bool IsWorld { get { Scenario.Call("type.world"); return Name == "WORL"; } }
    }
    public sealed class DbQualifier { public DbElement wrtQualifier; }
    public sealed class DbDoubleDimension
    {
        public bool Set = true; public string Label = "";
        public string Name { get { Scenario.Call("dimension.name." + Label); return "DIST"; } }
        public DbDimension StandardDimension { get { Scenario.Call("dimension.standard." + Label); return DbDimension.DIST; } }
    }
    public sealed class DbDoubleUnits
    {
        public string Name; public double Scale; public bool Set = true; public DbDoubleDimension DimensionValue;
        public DbDoubleDimension Dimension { get { Scenario.Call("units.dimension." + DimensionValue.Label); return DimensionValue; } }
        public static DbDoubleUnits GetUnits(string name, DbDoubleDimension dimension)
        { Scenario.Call("units.resolve." + dimension.Label); return new DbDoubleUnits { Name = name, Scale = name == "in" ? 25.4 : 1, DimensionValue = dimension }; }
    }
    public sealed class DbDouble
    {
        public double Value; public DbDoubleUnits UnitValue; public DbDoubleDimension DimensionValue; public string Label = "";
        public DbDoubleUnits Units { get { Scenario.Call("number.units." + Label); return UnitValue; } }
        public DbDoubleDimension Dimension { get { Scenario.Call("number.dimension." + Label); return DimensionValue; } }
        public double DatabaseValue { get { Scenario.Call("number.database." + Label); return Value * UnitValue.Scale; } }
        public DbDouble ConvertUnits(DbDoubleUnits units)
        { Scenario.Call("number.convert." + Label); return new DbDouble { Value = Value * UnitValue.Scale / units.Scale, UnitValue = units, DimensionValue = DimensionValue, Label = Label }; }
        public static DbDouble Create(DbDouble original)
        { return new DbDouble { Value = original.Value, UnitValue = original.UnitValue, DimensionValue = original.DimensionValue, Label = original.Label }; }
        public static DbDouble Create(double value, DbDoubleUnits units, DbDoubleDimension dimension)
        { Scenario.Call("number.create." + dimension.Label); return new DbDouble { Value = value, UnitValue = units, DimensionValue = dimension, Label = dimension.Label }; }
    }
    public sealed class DbElement
    {
        public int Number; public DbElement Parent; public DbElementType Type, ActualType;
        public bool Deleted, NullElement; public string StoredName; public Position Position = new Position();
        public readonly List<DbElement> Children = new List<DbElement>();
        public readonly Dictionary<string, double> Values = new Dictionary<string, double>();
        public readonly List<DbAttribute> Rules = new List<DbAttribute>();
        private readonly Dictionary<string, DbDoubleDimension> dimensions = new Dictionary<string, DbDoubleDimension>();
        public double NativeScale = 1;
        public DbElement(int number, string type, DbElement parent)
        { Number = number; Type = new DbElementType(type); Parent = parent; if (parent != null) parent.Children.Add(this); Scenario.Elements[Key] = this; }
        public string Key { get { return "=1/" + Number; } }
        public int[] Ref { get { Scenario.Call("ref"); return new[] { 1, Number }; } }
        public DbType DbType { get { Scenario.Call("dbtype"); return DbType.Design; } }
        public DbElement Owner { get { Scenario.Call("owner"); return Parent; } }
        public bool IsValid { get { Scenario.Call("valid"); return !Deleted && !NullElement; } }
        public bool IsNull { get { Scenario.Call("null"); return NullElement; } }
        public bool IsDeleted { get { Scenario.Call("deleted"); return Deleted; } }
        public DbElementType GetElementType() { Scenario.Call("element.type"); return Type; }
        public DbElementType GetActualType() { Scenario.Call("element.actualtype"); return ActualType ?? Type; }
        public DbElement FirstMember() { Scenario.Call("first"); return Children.FirstOrDefault(); }
        public DbElement Next() { Scenario.Call("next"); int index = Parent.Children.IndexOf(this) + 1; return index < Parent.Children.Count ? Parent.Children[index] : null; }
        public bool IsAttributeValid(DbAttribute attribute)
        { Scenario.Call("valid." + attribute.Key); return Values.ContainsKey(attribute.Key) || new[] { "NAME", "POS", "ORI" }.Contains(attribute.Key); }
        public bool IsAttributeSetable(DbAttribute attribute) { Scenario.Call("settable." + attribute.Key); return true; }
        public DbDouble GetDbDouble(DbAttribute attribute)
        {
            Scenario.Call("read." + attribute.Key);
            DbDoubleDimension dimension;
            if (!dimensions.TryGetValue(attribute.Key, out dimension))
                dimensions[attribute.Key] = dimension = new DbDoubleDimension { Label = attribute.Key };
            return new DbDouble { Value = Values[attribute.Key] / NativeScale,
                UnitValue = new DbDoubleUnits { Name = NativeScale == 1 ? "mm" : "in", Scale = NativeScale, DimensionValue = dimension }, DimensionValue = dimension, Label = attribute.Key };
        }
        public bool SetAttribute(DbAttribute attribute, DbQualifier qualifier, string value, out PdmsMessage message)
        {
            Scenario.Call("set." + attribute.Key); StoredName = value;
            message = null;
            if (Scenario.FailNameSetter) { Scenario.FailNameSetter = false; throw new InvalidOperationException("NAME failed after mutation"); }
            if (Scenario.AddForeignAfterName) { Scenario.AddForeignAfterName = false; new DbElement(999, "BOX", Scenario.CreatedRoot); throw new InvalidOperationException("foreign child arrived during callback"); }
            return true;
        }
        public Position GetPosition(DbAttribute attribute) { Scenario.Call("position"); return Position; }
        public Position GetPosition(DbAttribute attribute, DbQualifier qualifier) { return GetPosition(attribute); }
        public Orientation GetOrientation(DbAttribute attribute) { Scenario.Call("orientation"); return new Orientation(); }
        public Orientation GetOrientation(DbAttribute attribute, DbQualifier qualifier) { return GetOrientation(attribute); }
        public DbAttribute[] GetAttributesWithRules() { Scenario.Call("rules"); return Rules.ToArray(); }
        public string GetString(DbAttribute attribute) { Scenario.Call("string"); return StoredName ?? Key; }
        public int GetInteger(DbAttribute attribute) { Scenario.Call("integer"); return -1; }
        public DbElement GetElement(DbAttribute attribute) { throw new Exception("unsupported fixture guard should not be read"); }
        public string[] GetAsStringArray(DbAttribute attribute) { throw new Exception("unsupported fixture guard should not be read"); }
        public DbElement[] GetElementArray(DbAttribute attribute) { Scenario.Call("element.array"); return new DbElement[0]; }
        public DbAttribute[] GetAttributes() { Scenario.Call("attributes"); return Values.Keys.Select(key => new DbAttribute { Key = key }).ToArray(); }
        public DbDouble[] GetDbDoubleArray(DbAttribute attribute) { return new[] { GetDbDouble(attribute) }; }
        public int[] GetIntegerArray(DbAttribute attribute) { return new[] { GetInteger(attribute) }; }
        public bool GetBool(DbAttribute attribute) { Scenario.Call("bool"); return false; }
        public bool[] GetBoolArray(DbAttribute attribute) { return new[] { GetBool(attribute) }; }
        public string GetAsString(DbAttribute attribute) { Scenario.Call("formatted"); return attribute.Key == "SJUS" ? "POS" : "0"; }
        public double GetDouble(DbAttribute attribute) { Scenario.Call("double"); return Values[attribute.Key]; }
        public Direction GetDirection(DbAttribute attribute) { return new Direction(1, 0, 0); }
        public DateTime GetDateTime(DbAttribute attribute) { return new DateTime(2000, 1, 1); }
        public static DbElement GetElement(int[] reference) { Scenario.Call("resolve"); return Scenario.Elements["=" + reference[0] + "/" + reference[1]]; }
        public bool IsCreatable(DbElementType type) { Scenario.Call("creatable"); return true; }
        public DbElement CreateLast(DbElementType type)
        { Scenario.Call("create"); Scenario.Creates++; return Scenario.CreatedRoot = new DbElement(Scenario.NextId++, type.Name, this); }
        public bool IsCopyable(DbElement source) { Scenario.Call("copyable"); return true; }
        public void CopyHierarchy(DbElement source, DbCopyOption options)
        {
            Scenario.Call("copy"); Scenario.Copies++;
            if (options.Rename || options.IncludeRules || options.ReExecuteRules) throw new Exception("copy option contract changed");
            if (Scenario.ThrowCopyBeforePopulation) throw new InvalidOperationException("native copy failed before returning");
            CopyValues(source);
            foreach (var child in source.Children) CopyChild(child, this);
            if (Scenario.CorruptCopiedField != null)
            { var target = Values.ContainsKey(Scenario.CorruptCopiedField) ? this : Children.First(x => x.Values.ContainsKey(Scenario.CorruptCopiedField)); target.Values[Scenario.CorruptCopiedField] += 1; }
            if (Scenario.ThrowCopyAfterPopulation) throw new InvalidOperationException("native copy failed after unaccepted subtree population");
        }
        private void CopyValues(DbElement source)
        { foreach (var pair in source.Values) Values[pair.Key] = pair.Value; NativeScale = source.NativeScale; Position = source.Position; }
        private static void CopyChild(DbElement source, DbElement parent)
        { var created = new DbElement(Scenario.NextId++, source.Type.Name, parent); created.CopyValues(source); foreach (var child in source.Children) CopyChild(child, created); }
        private void DeleteTree() { Deleted = true; foreach (var child in Children) child.DeleteTree(); }
        public bool Delete(out PdmsMessage message)
        { Scenario.Call("delete"); Scenario.Deletes++; message = null; DeleteTree(); Parent.Children.Remove(this); return true; }
    }
    public sealed class DbCopyOption { public bool Rename, IncludeRules, ReExecuteRules; }
    public sealed class MDB
    { public string Name { get { Scenario.Call("mdb.name"); return "MDB"; } }
      public DbElement FindElement(string name) { Scenario.Call("find.name"); return Scenario.Elements.Values.FirstOrDefault(x => !x.Deleted && x.StoredName == name) ?? new DbElement(-1, "NULL", null) { NullElement = true }; } }
    public sealed class Project { public static Project CurrentProject { get { Scenario.Call("project"); return new Project(); } } public string Name { get { Scenario.Call("project.name"); return "PROJECT"; } } }
}
