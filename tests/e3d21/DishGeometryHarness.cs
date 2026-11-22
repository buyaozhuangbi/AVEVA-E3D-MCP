using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core.Geometry;
using Aveva.Core.Utilities.Messaging;

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;
        private static int checks, faults;
        private static void Check(bool value, string message) { checks++; if (!value) throw new Exception(message); }
        private static Dictionary<string, object> Map(object value) { return (Dictionary<string, object>)value; }
        private static Dictionary<string, double> Dims(params double[] values)
        { return new Dictionary<string, double> { ["DIAM"] = values[0], ["HEIG"] = values[1], ["RADI"] = values[2] }; }
        private static void Reject(Action action, string reason)
        { try { action(); } catch (ArgumentException) { checks++; return; } catch (InvalidOperationException) { checks++; return; } throw new Exception(reason); }
        private static E3d21Context Setup(string type = "DISH", double radius = 0)
        {
            Scenario.Reset();
            currentNativeFaultState = new NativeDatabaseFaultState();
            permittedNativeFaultGeneration = 0;
            Scenario.Latch = () => currentNativeFaultState.Observe("Unhandled Database error 538 from dish_test", "test");
            Scenario.World = new DbElement(0, "WORL", null);
            Scenario.Owner = new DbElement(1, "EQUI", Scenario.World);
            Scenario.Target = new DbElement(2, type, Scenario.Owner);
            Scenario.Target.Values["DIAM"] = 100;
            Scenario.Target.Values["HEIG"] = 25;
            Scenario.Target.Values["RADI"] = radius;
            Scenario.Target.Values["XLEN"] = 10; Scenario.Target.Values["YLEN"] = 20; Scenario.Target.Values["ZLEN"] = 30;
            Scenario.Target.Values["DTOP"] = 0; Scenario.Target.Values["DBOT"] = 100;
            return new E3d21Context();
        }
        private Dictionary<string, object> Read() { return Map(GetPrimitiveGeometry("=1/2")); }
        private Dictionary<string, object> Update(IDictionary<string, double> changes, string revision = null, bool preview = true)
        { return Map(UpdatePrimitive("=1/2", revision, changes, null, null, null, "OWNER", preview)); }

        private static void TestPolicyAndShapeRegression()
        {
            Check(AttributeEditPolicy.InputKind("RADI") == null, "RADI generic direct editor remains disabled");
            Reject(() => AttributeEditPolicy.Parse("RADI", "0"), "RADI cannot use generic scalar setter");
            Reject(() => ModelEditInput.AttributeValue("RADI", "1"), "RADI cannot enter generic create attributes");
            Check(PrimitiveShape("DISH") == "DISH" && !PrimitiveIsNegative("DISH"), "positive DISH recognized");
            foreach (string unsupported in new[] { "NDIS", "NDISH", "ADISH", "UDISH" })
                Check(PrimitiveShape(unsupported) == null, "unsupported dish type remains excluded: " + unsupported);
            Check(PrimitiveShapeFields("DISH").Select(x => x.Key).SequenceEqual(new[] { "DIAM", "HEIG", "RADI" }), "DISH exact dimension order");
            foreach (var value in new[] { Dims(100, 25, 0), Dims(100, 25, 7), Dims(1, 1000, 10000) })
                PrimitiveValidateDimensions("DISH", value); // no invented joint geometry bound
            foreach (string field in new[] { "DIAM", "HEIG", "RADI" })
            {
                foreach (double value in new[] { -1.0, double.NaN, double.PositiveInfinity, double.NegativeInfinity })
                { var input = Dims(100, 25, 0); input[field] = value; Reject(() => PrimitiveValidateDimensions("DISH", input), "invalid " + field); }
                var missing = Dims(100, 25, 0); missing.Remove(field); Reject(() => PrimitiveValidateDimensions("DISH", missing), "missing " + field);
            }
            foreach (string field in new[] { "DIAM", "HEIG" })
            { var input = Dims(100, 25, 0); input[field] = 0; Reject(() => PrimitiveValidateDimensions("DISH", input), "zero " + field); }
            var foreign = Dims(100, 25, 0); foreign["DTOP"] = 0; Reject(() => PrimitiveValidateDimensions("DISH", foreign), "foreign field");
            foreach (string type in new[] { "CYLI", "NCYL", "BOX", "NBOX", "CONE", "NCON" })
            {
                string shape = type == "NCYL" ? "CYLI" : type == "NBOX" ? "BOX" : type == "NCON" ? "CONE" : type;
                Check(PrimitiveShape(type) == shape, "legacy shape mapping " + type);
                Check(PrimitiveIsNegative(type) == type.StartsWith("N"), "legacy negative classification " + type);
                string[] keys = shape == "CYLI" ? new[] { "DIAM", "HEIG" } : shape == "BOX" ? new[] { "XLEN", "YLEN", "ZLEN" } : new[] { "DTOP", "DBOT", "HEIG" };
                Check(PrimitiveShapeFields(shape).Select(x => x.Key).SequenceEqual(keys), "legacy fields " + type);
                var valid = keys.ToDictionary(key => key, key => key == "DTOP" ? 0.0 : 20.0);
                PrimitiveValidateDimensions(shape, valid);
                valid["RADI"] = 0; Reject(() => PrimitiveValidateDimensions(shape, valid), "RADI rejected on " + type);
                var legacy = Setup(type);
                if (PrimitiveIsNegative(type))
                {
                    Scenario.Owner.Type = new DbElementType(shape);
                    foreach (var dimension in Scenario.Target.Values) Scenario.Owner.Values[dimension.Key] = dimension.Value;
                }
                var inspected = legacy.Read();
                Check(Equals(inspected["complete"], true) && Equals(inspected["editable"], true), "legacy production inspection " + type);
                string change = shape == "BOX" ? "XLEN" : "HEIG";
                var update = legacy.Update(new Dictionary<string, double> { [change] = 40 }, (string)inspected["revision"], false);
                Check(Equals(update["success"], true) && Equals(update["readback_verified"], true) && Scenario.Target.Values[change] == 40,
                    "legacy production update roundtrip " + type);
            }
            var bridge = Setup();
            foreach (string type in new[] { "NDIS", "NDISH" })
            {
                Scenario.Target.Type = new DbElementType(type);
                Reject(() => bridge.CopyReadNode(Scenario.Target, Scenario.Target, "", "=1/1", 0,
                    new List<CopyNode>(), new HashSet<string>()), "copy admission rejects " + type);
            }
        }

        private static void TestReadRevisionRulesAndChildren()
        {
            var bridge = Setup();
            var read = bridge.Read();
            Check(Equals(read["success"], true) && Equals(read["complete"], true) && Equals(read["editable"], true), "leaf DISH readable/editable");
            Check(Equals(read["shape"], "DISH") && Equals(read["unit"], "mm") && Equals(read["negative"], false), "DISH result identity and units");
            var dimensions = (Dictionary<string, double>)read["dimensions"];
            Check(dimensions.Keys.SequenceEqual(new[] { "DIAM", "HEIG", "RADI" }) && dimensions["RADI"] == 0, "RADI zero roundtrip");
            string revision = (string)read["revision"];
            Scenario.Target.Values["RADI"] = 5;
            var changed = bridge.Read();
            Check((string)changed["revision"] != revision, "RADI participates in revision");
            var stale = bridge.Update(new Dictionary<string, double> { ["RADI"] = 2 }, revision, false);
            Check(Equals(stale["revision_conflict"], true) && Equals(stale["write_attempted"], false) && Scenario.Setters == 0, "stale RADI revision rejects before setter");
            Scenario.Target.Rules.Add(DbAttribute.GetDbAttribute("RADI"));
            var ruled = bridge.Read();
            Check(Equals(ruled["editable"], false) && ruled["revision"] == null, "RADI rules block revision/edit");
            Check(((string[])ruled["edit_reasons"]).Any(x => x.Contains("RADI")), "RADI rule reason reported");
            bridge = Setup();
            Scenario.Target.ActualType = new DbElementType("UDISH");
            read = bridge.Read();
            Check(Equals(read["editable"], false) && read["revision"] == null, "DISH UDET cannot edit");
            foreach (string childType in new[] { "NCYL", "NBOX", "NCON", "NDIS", "BOX" })
            {
                bridge = Setup();
                var child = new DbElement(3, childType, Scenario.Target);
                child.Values["DIAM"] = 10; child.Values["HEIG"] = 5; child.Values["XLEN"] = child.Values["YLEN"] = child.Values["ZLEN"] = 2;
                child.Values["DTOP"] = 0; child.Values["DBOT"] = 4;
                read = bridge.Read();
                Check(Equals(read["editable"], false) && read["revision"] == null, "any child denies DISH edit: " + childType);
                Check(((Dictionary<string, double>)read["dimensions"])["RADI"] == 0, "DISH dimensions retained with child " + childType);
                var preview = bridge.Update(new Dictionary<string, double> { ["RADI"] = 1 });
                Check(Equals(preview["success"], false) && Scenario.Setters == 0, "child gate before setters");
            }
            bridge = Setup("NDIS"); read = bridge.Read();
            Check(Equals(read["editable"], false) && read["revision"] == null, "negative dish remains unsupported");
            bridge = Setup("NCYL"); Scenario.Owner.Type = new DbElementType("DISH");
            Scenario.Owner.Values["DIAM"] = 100; Scenario.Owner.Values["HEIG"] = 25; Scenario.Owner.Values["RADI"] = 0;
            read = bridge.Read();
            Check(Equals(read["editable"], false) && read["revision"] == null, "DISH does not enable negative-owner editing");
        }

        private static void TestUpdateAndRestoration()
        {
            var bridge = Setup();
            string original = (string)bridge.Read()["revision"];
            foreach (double radius in new[] { 7.25, 0.0 })
            {
                var changes = new Dictionary<string, double> { ["RADI"] = radius };
                var preview = bridge.Update(changes);
                Check(Equals(preview["success"], true), "RADI preview succeeds");
                int before = Scenario.Setters;
                var result = bridge.Update(changes, (string)preview["revision"], false);
                Check(Equals(result["success"], true) && Equals(result["readback_verified"], true), "RADI execution verified");
                Check(Scenario.Setters == before + 1 && Scenario.Target.Values["RADI"] == radius, "one RADI setter and exact readback");
                Check(Scenario.Target.Values["DIAM"] == 100 && Scenario.Target.Values["HEIG"] == 25, "unrequested dimensions preserved");
            }
            Check((string)bridge.Read()["revision"] == original, "restored RADI restores native revision");
            bridge = Setup(radius: 12.7); // fixture native storage/display units use inches
            Scenario.Target.NativeScale = 25.4;
            string revision = (string)bridge.Read()["revision"];
            Scenario.FailSetterOnce = true;
            var failed = bridge.Update(new Dictionary<string, double> { ["RADI"] = 50.8 }, revision, false);
            Check(Equals(failed["success"], false) && Equals(failed["rollback_succeeded"], true) && Equals(failed["rollback_verified_original"], true), "setter mutation then error restores verified original");
            Check(Scenario.Target.Values["RADI"] == 12.7 && Scenario.LastSetterUnits == "in" && Scenario.LastSetterRaw == 0.5, "rollback retains exact native units/raw value");
            Check(Scenario.Setters == 2, "one apply and one rollback; no replay");
            foreach (int mode in new[] { 1, 2 })
            {
                bridge = Setup(radius: 3);
                revision = (string)bridge.Read()["revision"];
                Scenario.SetterDiagnosticOnce = mode;
                failed = bridge.Update(new Dictionary<string, double> { ["RADI"] = 9 }, revision, false);
                Check(Equals(failed["success"], false) && Equals(failed["rollback_verified_original"], true) && Scenario.Target.Values["RADI"] == 3,
                    "false return or true-with-diagnostic cannot pass and restores original: " + mode);
            }
            bridge = Setup(radius: 2);
            revision = (string)bridge.Read()["revision"];
            Scenario.BadReadbackOnce = true;
            failed = bridge.Update(new Dictionary<string, double> { ["RADI"] = 8 }, revision, false);
            Check(Equals(failed["success"], false) && Equals(failed["rollback_verified_original"], true) && Scenario.Target.Values["RADI"] == 2, "wrong RADI readback triggers exact restoration");
            bridge = Setup();
            Scenario.MissingRadius = true;
            var unavailable = bridge.Read();
            Check(Equals(unavailable["complete"], false) && unavailable["revision"] == null, "unreadable RADI is partial and cannot revise");
            Check(!((Dictionary<string, double>)unavailable["dimensions"]).ContainsKey("RADI"), "unreadable RADI never invents zero");
        }

        private static void TestFaultBoundaries()
        {
            var bridge = Setup(); bridge.Read();
            var readCalls = Scenario.Calls.ToArray();
            var radiusReadBoundaries = readCalls.Select((name, index) => new { name, index }).Where(x => x.name.Contains("RADI")).Select(x => x.index + 1).ToArray();
            foreach (int at in radiusReadBoundaries)
            {
                foreach (bool throws in new[] { false, true })
                {
                    bridge = Setup(); Scenario.FaultAt = at; Scenario.ThrowWithFault = throws;
                    ExpectFault(() => bridge.Read(), "read " + readCalls[at - 1]);
                    Check(Scenario.Calls.Count == at, "native continuation after fault at " + readCalls[at - 1] + ": " + string.Join(",", Scenario.Calls.Skip(at)));
                    faults++;
                }
            }
            bridge = Setup(); string revision = (string)bridge.Read()["revision"];
            Scenario.Calls.Clear(); bridge.Update(new Dictionary<string, double> { ["RADI"] = 5 }, revision, false);
            var updateCalls = Scenario.Calls.ToArray();
            var setterBoundaries = updateCalls.Select((name, index) => new { name, index }).Where(x => x.name.Contains("RADI")).Select(x => x.index + 1).ToArray();
            foreach (int at in setterBoundaries)
            {
                foreach (bool throws in new[] { false, true })
                {
                    bridge = Setup(); revision = (string)bridge.Read()["revision"]; Scenario.Calls.Clear();
                    Scenario.FaultAt = at; Scenario.ThrowWithFault = throws;
                    ExpectFault(() => bridge.Update(new Dictionary<string, double> { ["RADI"] = 5 }, revision, false), updateCalls[at - 1]);
                    Check(Scenario.Calls.Count == at && Scenario.Setters <= 1, "no native continuation after update fault at " + updateCalls[at - 1] + ": " + string.Join(",", Scenario.Calls.Skip(at)));
                    faults++;
                }
            }
            bridge = Setup();
            Scenario.Target.Rules.Add(new DbAttribute { Key = "RADI" });
            bridge.Read();
            int ruleAt = Scenario.Calls.FindIndex(name => name == "attribute.name.RADI") + 1;
            Check(ruleAt > 0, "RADI rule name uses SDK getter");
            foreach (bool throws in new[] { false, true })
            {
                bridge = Setup(); Scenario.Target.Rules.Add(new DbAttribute { Key = "RADI" });
                Scenario.FaultAt = ruleAt; Scenario.ThrowWithFault = throws;
                ExpectFault(() => bridge.Read(), "RADI rule-name getter");
                Check(Scenario.Calls.Count == ruleAt, "RADI rule-name fault stops immediately");
                faults++;
            }
            bridge = Setup(radius: 12.7); Scenario.Target.NativeScale = 25.4;
            revision = (string)bridge.Read()["revision"];
            Scenario.Calls.Clear(); Scenario.FailSetterOnce = true;
            bridge.Update(new Dictionary<string, double> { ["RADI"] = 50.8 }, revision, false);
            var rollbackCalls = Scenario.Calls.ToArray();
            int firstSetter = Array.IndexOf(rollbackCalls, "set.RADI");
            var rollbackBoundaries = rollbackCalls.Select((name, index) => new { name, index })
                .Where(x => x.index > firstSetter && x.name.Contains("RADI")).Select(x => x.index + 1).ToArray();
            Check(rollbackBoundaries.Length > 0, "rollback has observed native RADI boundaries");
            foreach (int at in rollbackBoundaries)
            {
                foreach (bool throws in new[] { false, true })
                {
                    bridge = Setup(radius: 12.7); Scenario.Target.NativeScale = 25.4;
                    revision = (string)bridge.Read()["revision"];
                    Scenario.Calls.Clear(); Scenario.FailSetterOnce = true; Scenario.FaultAt = at; Scenario.ThrowWithFault = throws;
                    ExpectFault(() => bridge.Update(new Dictionary<string, double> { ["RADI"] = 50.8 }, revision, false), rollbackCalls[at - 1]);
                    Check(Scenario.Calls.Count == at && Scenario.Setters <= 2, "rollback fault stops before later restore/readback calls at " + rollbackCalls[at - 1]);
                    faults++;
                }
            }
        }
        private static void ExpectFault(Action action, string name)
        { try { action(); } catch (NativeDatabaseFaultException) { checks++; return; } throw new Exception("Expected native fault: " + name); }
        private static int Main()
        {
            try { TestPolicyAndShapeRegression(); TestReadRevisionRulesAndChildren(); TestUpdateAndRestoration(); TestFaultBoundaries();
                Console.WriteLine("DISH production checks passed: " + checks + "; native fault cases: " + faults); return 0; }
            catch (Exception error) { Console.Error.WriteLine(error); return 1; }
        }

        // Peripheral session/frame fakes. Primitive implementation is unmodified.
        private bool IsCommandActive() { return false; }
        private bool TryBeginCommand(string name) { return true; }
        private void EndCommand() { }
        private object BusyResult(string name) { throw new Exception("unexpected busy"); }
        private T OnUi<T>(Func<T> work) { ThrowIfNativeFaulted(); try { return work(); } finally { ThrowIfNativeFaulted(); } }
        private static DbElement ModelRequireElement(string reference) { return Scenario.Target; }
        private static MDB CurrentMdb() { return new MDB(); }
        private static DbElement ModelFrame(string name, DbElement owner) { return name == "OWNER" ? owner : Scenario.World; }
        private static DbElement ModelStorageFrame(DbElement owner) { return owner; }
        private static void ModelRequireStorageFrame(DbElement element, DbElement frame, ModelEditInput input) { }
        private static void ModelRequireStableFrame(DbElement element, DbElement frame) { }
        private sealed class StorageTransform { internal ModelEditInput ToNative(ModelEditInput input) { return input; } }
        private static StorageTransform ModelStorageTransform(DbElement storage, DbElement requested) { return new StorageTransform(); }
        private static Position ModelPosition(double[] p) { return new Position(p[0], p[1], p[2]); }
        private static Orientation ModelOrientation(ModelEditInput input) { return new Orientation(); }
        private static bool ModelOrientationsEqual(Orientation a, Orientation b) { return a != null && b != null; }
        private static void ModelWritePosition(DbElement element, Position p) { throw new Exception("out of test scope"); }
        private static void ModelWriteOrientation(DbElement element, Orientation p) { throw new Exception("out of test scope"); }
        private static string ModelCurrentReference() { return "=1/99"; }
        private static void ModelCeUnchanged(Dictionary<string, object> result) { result["ce_unchanged"] = Equals(result["ce_before"], result["ce_after"]); }
        private static Dictionary<string, object> ModelIdentity(DbElement element) { return new Dictionary<string, object> { ["refno"] = ModelReference(element.Ref) }; }
        private const int CopyDepthLimit = 32, CopyNodeLimit = 2048;
        private sealed class CopyNode { }
        private sealed class CopyTreeIdentity { internal string Reference, Owner, Type; }
        private static CopyTreeIdentity CopyIdentity(DbElement e, string path) { return new CopyTreeIdentity { Reference = ModelReference(e.Ref), Owner = ModelReference(e.Owner.Ref), Type = e.Type.Name }; }
        // PRODUCTION_METHODS
    }

    internal static class Scenario
    {
        internal static DbElement Target, Owner, World;
        internal static readonly List<string> Calls = new List<string>();
        internal static Action Latch;
        internal static int FaultAt, Setters, SetterDiagnosticOnce;
        internal static bool ThrowWithFault, FailSetterOnce, BadReadbackOnce, MissingRadius;
        internal static string LastSetterUnits;
        internal static double LastSetterRaw;
        internal static void Reset()
        { Calls.Clear(); FaultAt = Setters = SetterDiagnosticOnce = 0; ThrowWithFault = FailSetterOnce = BadReadbackOnce = MissingRadius = false; LastSetterUnits = null; LastSetterRaw = 0; }
        internal static void Call(string name)
        {
            Calls.Add(name);
            if (Calls.Count == FaultAt) { Latch(); if (ThrowWithFault) throw new InvalidOperationException("native fault companion exception"); }
        }
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
    public enum DbAttributeType { ELEMENT, STRINGARRAY, WORD, DOUBLE }
    public enum DbDimension { DIST, OTHER }
    public sealed class DbAttribute
    {
        public string Key;
        public string ShortName { get { Scenario.Call("attribute.name." + Key); return Key; } }
        public bool IsValid { get { return true; } }
        public DbAttributeType Type { get { return DbAttributeType.ELEMENT; } }
        public bool IsArray { get { return false; } }
        public static DbAttribute GetDbAttribute(string name) { Scenario.Call("attribute." + name); return new DbAttribute { Key = name }; }
    }
    public static class DbAttributeInstance
    { public static readonly DbAttribute POS = new DbAttribute { Key = "POS" }, ORI = new DbAttribute { Key = "ORI" }, NAME = new DbAttribute { Key = "NAME" }; }
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
        public readonly List<DbElement> Children = new List<DbElement>();
        public readonly Dictionary<string, double> Values = new Dictionary<string, double>();
        public readonly List<DbAttribute> Rules = new List<DbAttribute>();
        private readonly Dictionary<string, DbDoubleDimension> dimensions = new Dictionary<string, DbDoubleDimension>();
        public double NativeScale = 1;
        public DbElement(int number, string type, DbElement parent) { Number = number; Type = new DbElementType(type); Parent = parent; if (parent != null) parent.Children.Add(this); }
        public int[] Ref { get { Scenario.Call("ref"); return new[] { 1, Number }; } }
        public DbType DbType { get { Scenario.Call("dbtype"); return DbType.Design; } }
        public DbElement Owner { get { Scenario.Call("owner"); return Parent; } }
        public bool IsValid { get { Scenario.Call("valid"); return true; } }
        public bool IsNull { get { Scenario.Call("null"); return false; } }
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
            if (attribute.Key == "RADI" && Scenario.MissingRadius) throw new InvalidOperationException("RADI unavailable");
            DbDoubleDimension dimension;
            if (!dimensions.TryGetValue(attribute.Key, out dimension))
                dimensions[attribute.Key] = dimension = new DbDoubleDimension { Label = attribute.Key };
            return new DbDouble { Value = Values[attribute.Key] / NativeScale,
                UnitValue = new DbDoubleUnits { Name = NativeScale == 1 ? "mm" : "in", Scale = NativeScale, DimensionValue = dimension }, DimensionValue = dimension, Label = attribute.Key };
        }
        public bool SetAttribute(DbAttribute attribute, DbQualifier qualifier, DbDouble value, out PdmsMessage message)
        {
            Scenario.Call("set." + attribute.Key); Scenario.Setters++;
            Values[attribute.Key] = value.Value * value.UnitValue.Scale;
            Scenario.LastSetterUnits = value.UnitValue.Name; Scenario.LastSetterRaw = value.Value;
            message = null;
            if (Scenario.FailSetterOnce) { Scenario.FailSetterOnce = false; throw new InvalidOperationException("setter changed then failed"); }
            if (Scenario.BadReadbackOnce) { Scenario.BadReadbackOnce = false; Values[attribute.Key] += 1; }
            if (Scenario.SetterDiagnosticOnce != 0)
            { int mode = Scenario.SetterDiagnosticOnce; Scenario.SetterDiagnosticOnce = 0; message = new PdmsMessage(); return mode == 2; }
            return true;
        }
        public Position GetPosition(DbAttribute attribute) { Scenario.Call("position"); return new Position(); }
        public Position GetPosition(DbAttribute attribute, DbQualifier qualifier) { return GetPosition(attribute); }
        public Orientation GetOrientation(DbAttribute attribute) { Scenario.Call("orientation"); return new Orientation(); }
        public Orientation GetOrientation(DbAttribute attribute, DbQualifier qualifier) { return GetOrientation(attribute); }
        public DbAttribute[] GetAttributesWithRules() { Scenario.Call("rules"); return Rules.ToArray(); }
        public string GetString(DbAttribute attribute) { Scenario.Call("string"); return "/E" + Number; }
        public int GetInteger(DbAttribute attribute) { Scenario.Call("integer"); return -1; }
        public DbElement GetElement(DbAttribute attribute) { throw new Exception("unsupported fixture guard should not be read"); }
        public string[] GetAsStringArray(DbAttribute attribute) { throw new Exception("unsupported fixture guard should not be read"); }
        public DbElement[] GetElementArray(DbAttribute attribute) { throw new Exception("unsupported fixture guard should not be read"); }
    }
    public sealed class MDB { public string Name { get { Scenario.Call("mdb.name"); return "MDB"; } } }
    public sealed class Project { public static Project CurrentProject { get { Scenario.Call("project"); return new Project(); } } public string Name { get { Scenario.Call("project.name"); return "PROJECT"; } } }
}
