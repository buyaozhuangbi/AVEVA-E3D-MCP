using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using E3d21AiBridge;

// Compiled only by test_snapshot_formatted_attributes.py with actual production
// methods. Fakes model the decompiled formatter return/throw contract, not E3D.
internal static class SnapshotFormattedAttributesHarness
{
    [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
    [ThreadStatic] private static long permittedNativeFaultGeneration;
    private static int checks;
    private static int faultCases;
    internal static readonly List<string> Calls = new List<string>();
    internal static int NativeCalls;
    internal static int TryCalls;
    internal static int ThrowingCalls;
    internal static int FaultAt;
    internal static bool ThrowOnFault;
    internal static bool Record = true;

    internal static void Call(string operation)
    {
        NativeCalls++;
        if (Record) Calls.Add(operation);
        if (FaultAt == NativeCalls)
        {
            currentNativeFaultState.Observe("Unhandled Database error 538 from db_snapshot_test", "test");
            if (ThrowOnFault) throw new InvalidOperationException("SDK failure accompanying native fault");
        }
    }

    private static void Reset(int faultAt = 0, bool throwOnFault = false)
    {
        currentNativeFaultState = new NativeDatabaseFaultState();
        permittedNativeFaultGeneration = 0;
        NativeCalls = TryCalls = ThrowingCalls = 0;
        FaultAt = faultAt;
        ThrowOnFault = throwOnFault;
        Calls.Clear();
    }

    private static void Check(bool condition, string reason)
    {
        checks++;
        if (!condition) throw new Exception(reason);
    }

    private static void Equal(object expected, object actual, string path)
    {
        var expectedMap = expected as IDictionary<string, object>;
        if (expectedMap != null)
        {
            var actualMap = actual as IDictionary<string, object>;
            Check(actualMap != null, path + " dictionary type");
            Check(expectedMap.Keys.SequenceEqual(actualMap.Keys), path + " keys and insertion order");
            foreach (var key in expectedMap.Keys) Equal(expectedMap[key], actualMap[key], path + "." + key);
            return;
        }
        if (expected is IEnumerable && !(expected is string))
        {
            Check(actual is IEnumerable, path + " sequence type");
            var a = ((IEnumerable)expected).Cast<object>().ToArray();
            var b = ((IEnumerable)actual).Cast<object>().ToArray();
            Check(a.Length == b.Length, path + " sequence length");
            for (int i = 0; i < a.Length; i++) Equal(a[i], b[i], path + "[" + i + "]");
            return;
        }
        Check(object.Equals(expected, actual), path + " scalar value");
    }

    // Fixed pre-change oracle: deliberately retains the original throwing reads.
    private static Dictionary<string, object> OldElementInfo(DbElement element)
    {
        return new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
        {
            ["refno"] = ElementRef(element),
            ["type"] = ElementType(element),
            ["name"] = ElementName(element),
            ["desc"] = SafeString(delegate { return element.GetAsString(DbAttributeInstance.DESC); }),
            ["pos"] = SafeString(delegate { return element.GetAsString(DbAttributeInstance.POS); }),
            ["ori"] = SafeString(delegate { return element.GetAsString(DbAttributeInstance.ORI); }),
            ["diam"] = SafeString(delegate { return element.GetAsString(DbAttributeInstance.DIAM); }),
            ["heig"] = SafeString(delegate { return element.GetAsString(DbAttributeInstance.HEIG); }),
            ["xlen"] = SafeString(delegate { return element.GetAsString(DbAttributeInstance.XLEN); }),
            ["ylen"] = SafeString(delegate { return element.GetAsString(DbAttributeInstance.YLEN); }),
            ["zlen"] = SafeString(delegate { return element.GetAsString(DbAttributeInstance.ZLEN); }),
            ["dtop"] = SafeString(delegate { return element.GetAsString(DbAttributeInstance.DTOP); }),
            ["dbot"] = SafeString(delegate { return element.GetAsString(DbAttributeInstance.DBOT); }),
            ["db_no"] = SafeInt(delegate { return element.DbNo(); })
        };
    }

    private static Dictionary<string, object> OldNode(BoundedHierarchy<DbElement>.Node node)
    {
        var result = OldElementInfo(node.Value);
        result["members"] = node.Children.Select(child => (object)OldNode(child)).ToList();
        return result;
    }

    private static Dictionary<string, object> Snapshot(DbElement root, int depth, int limit, bool old)
    {
        var hierarchy = ReadHierarchy(root, depth, limit);
        if (!old) return BuildSnapshot(hierarchy, depth, limit);
        var result = OldNode(hierarchy.Root);
        result["snapshot_info"] = HierarchyInfo(hierarchy, depth, limit);
        return result;
    }

    private static DbElement Fixture(int nodes, int missing = 0)
    {
        var root = new DbElement("root", missing);
        for (int i = 1; i < nodes; i++) root.Children.Add(new DbElement("child" + i, missing));
        return root;
    }

    private static void CompareSnapshot(DbElement root, int depth, int limit)
    {
        Reset();
        var old = Snapshot(root, depth, limit, true);
        var calls = Calls.ToArray();
        Reset();
        var current = Snapshot(root, depth, limit, false);
        Equal(old, current, "snapshot");
        Check(calls.SequenceEqual(Calls), "identical native reads and order");
        Check(TryCalls == ((int)((Dictionary<string, object>)current["snapshot_info"])["element_count"]) * 10,
            "all ten attributes still attempted for every included node");
    }

    private static void TestValuesAndHierarchy()
    {
        for (int mode = 0; mode <= 4; mode++)
        {
            var root = Fixture(3);
            foreach (var node in new[] { root }.Concat(root.Children)) node.Mode = mode;
            CompareSnapshot(root, 8, 500);
        }
        var tree = Fixture(4, 7);
        tree.Children[0].Children.Add(new DbElement("grandchild", 4));
        CompareSnapshot(tree, 0, 500);
        CompareSnapshot(tree, 1, 500);
        CompareSnapshot(tree, 8, 1);
        CompareSnapshot(tree, 8, 3);
        CompareSnapshot(tree, 8, 5); // exactly full budget remains complete
        CompareSnapshot(Fixture(1), 0, 1); // real leaf remains complete
        tree.FailMembers = true;
        CompareSnapshot(tree, 8, 500); // partial hierarchy and read error preserved
        tree.FailMembers = false;
        tree.Children[1].Valid = false;
        CompareSnapshot(tree, 8, 500); // unusable child preserves read-error behavior
        tree.Children[1].Valid = true;
        tree.Children[0].Type.Short = "";
        tree.Values["FLNN"] = "Unknown pseudo-name";
        tree.Values["NAME"] = "";
        tree.Values["NAMN"] = "/fallback";
        CompareSnapshot(tree, 8, 500);
        Reset();
        Equal(OldElementInfo(null), ElementInfo(null), "null element");
        var fresh = Fixture(1);
        Reset();
        var before = Snapshot(fresh, 8, 500, false);
        fresh.Values["POS"] = "E 9 ft N 3 1/2 in U -0,75 mm";
        fresh.Values["DESC"] = "修改后的描述\r\n  preserve whitespace  ";
        fresh.Children.Add(new DbElement("new child", 10));
        Reset();
        var after = Snapshot(fresh, 8, 500, false);
        Check(!Equals(before["pos"], after["pos"]), "position is read fresh");
        Check(Equals(after["pos"], fresh.Values["POS"]), "formatted units are unchanged");
        Check(Equals(after["desc"], fresh.Values["DESC"]), "unicode and whitespace are unchanged");
        Check(((IList<object>)after["members"]).Count == 1, "hierarchy is read fresh");
        Check(Equals(after["DESC"], after["desc"]), "dictionary comparer retained");
    }

    private static void ExpectFault(Action action, string message)
    {
        try { action(); }
        catch (NativeDatabaseFaultException) { Check(true, message); return; }
        throw new Exception(message);
    }

    private static void TestFaults()
    {
        for (int mode = 0; mode <= 4; mode++)
        {
            var root = Fixture(3);
            foreach (var node in new[] { root }.Concat(root.Children)) node.Mode = mode;
            Reset();
            Snapshot(root, 8, 500, false);
            int count = NativeCalls;
            for (int at = 1; at <= count; at++)
            {
                foreach (bool throws in new[] { false, true })
                {
                    Reset(at, throws);
                    ExpectFault(() => Snapshot(root, 8, 500, true), "old snapshot must reject fault");
                    var oldCalls = Calls.ToArray();
                    Check(NativeCalls == at, "old snapshot stops immediately");
                    Reset(at, throws);
                    ExpectFault(() => Snapshot(root, 8, 500, false), "production snapshot must reject fault");
                    Check(NativeCalls == at, "production snapshot stops immediately");
                    Check(oldCalls.SequenceEqual(Calls), "same native fault boundary");
                    faultCases++;
                }
            }
        }
        Reset();
        currentNativeFaultState.Observe("Unhandled Database error 538 from db_snapshot_test", "test");
        ExpectFault(() => SafeFormattedAttribute(new DbElement("blocked", 0), DbAttributeInstance.DESC),
            "preexisting fault stops before getter");
        Check(NativeCalls == 0, "no native call with preexisting fault");
    }

    private static double Measure(DbElement root, bool old, int iterations)
    {
        Reset();
        var watch = Stopwatch.StartNew();
        for (int i = 0; i < iterations; i++) Snapshot(root, 8, 500, old);
        watch.Stop();
        return watch.Elapsed.TotalMilliseconds / iterations;
    }

    private static void Benchmark()
    {
        Record = false;
        foreach (int missing in new[] { 0, 8, 10 })
        {
            var root = Fixture(500, missing);
            Measure(root, true, 2);
            Measure(root, false, 2);
            var before = new List<double>();
            var after = new List<double>();
            for (int sample = 0; sample < 9; sample++)
            {
                // Alternate order to reduce warmup/order bias.
                if (sample % 2 == 0) { before.Add(Measure(root, true, 4)); after.Add(Measure(root, false, 4)); }
                else { after.Add(Measure(root, false, 4)); before.Add(Measure(root, true, 4)); }
            }
            before.Sort(); after.Sort();
            Console.WriteLine(string.Format(CultureInfo.InvariantCulture,
                "OFFLINE_BENCHMARK {{\"nodes\":500,\"missing_per_node\":{0},\"samples\":9,\"iterations_per_sample\":4,\"old_median_ms\":{1:F4},\"production_median_ms\":{2:F4},\"speedup\":{3:F3}",
                missing, before[4], after[4], before[4] / after[4]) + "}");
        }
        Record = true;
    }

    private static int Main()
    {
        try
        {
            TestValuesAndHierarchy();
            TestFaults();
            Benchmark();
            Console.WriteLine("Snapshot formatted attribute checks passed: " + checks + "; injected native fault cases: " + faultCases);
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }

    // PRODUCTION_METHODS
}

internal sealed class DbAttribute
{
    internal readonly string Name;
    internal DbAttribute(string name) { Name = name; }
}

internal static class DbAttributeInstance
{
    public static DbAttribute REF = new DbAttribute("REF"), FLNN = new DbAttribute("FLNN"), NAME = new DbAttribute("NAME"), NAMN = new DbAttribute("NAMN"),
        DESC = new DbAttribute("DESC"), POS = new DbAttribute("POS"), ORI = new DbAttribute("ORI"), DIAM = new DbAttribute("DIAM"),
        HEIG = new DbAttribute("HEIG"), XLEN = new DbAttribute("XLEN"), YLEN = new DbAttribute("YLEN"), ZLEN = new DbAttribute("ZLEN"),
        DTOP = new DbAttribute("DTOP"), DBOT = new DbAttribute("DBOT");
}

internal sealed class DbElementType
{
    internal string Short = "BOX";
    public string ShortName { get { SnapshotFormattedAttributesHarness.Call("type.ShortName"); return Short; } }
    public string Name { get { SnapshotFormattedAttributesHarness.Call("type.Name"); return "BOX"; } }
}

internal sealed class DbElement
{
    internal readonly string Id;
    internal readonly List<DbElement> Children = new List<DbElement>();
    internal readonly Dictionary<string, string> Values = new Dictionary<string, string>();
    internal readonly DbElementType Type = new DbElementType();
    internal int Mode;
    internal bool FailMembers;
    internal bool Valid = true;
    internal DbElement(string id, int missing)
    {
        Id = id;
        Values["REF"] = "=1/" + id;
        Values["FLNN"] = "/" + id;
        string[] fields = { "DESC", "POS", "ORI", "DIAM", "HEIG", "XLEN", "YLEN", "ZLEN", "DTOP", "DBOT" };
        for (int i = missing; i < fields.Length; i++) Values[fields[i]] = "  " + fields[i] + " ° 12,50 mm\r\n";
    }
    public bool IsValid { get { SnapshotFormattedAttributesHarness.Call(Id + ".IsValid"); return Valid; } }
    public bool IsNull { get { SnapshotFormattedAttributesHarness.Call(Id + ".IsNull"); return false; } }
    public DbElementType GetElementType() { SnapshotFormattedAttributesHarness.Call(Id + ".GetElementType"); return Type; }
    public int DbNo() { SnapshotFormattedAttributesHarness.Call(Id + ".DbNo"); return 42; }
    public IEnumerable<DbElement> Members()
    {
        SnapshotFormattedAttributesHarness.Call(Id + ".Members");
        for (int i = 0; i < Children.Count; i++)
        {
            if (FailMembers && i == 1) throw new InvalidOperationException("unreadable child");
            yield return Children[i];
        }
    }
    private bool Formatted(DbAttribute attribute, ref string value)
    {
        SnapshotFormattedAttributesHarness.Call(Id + ".attribute." + attribute.Name);
        bool identity = attribute.Name == "REF" || attribute.Name == "FLNN" || attribute.Name == "NAME" || attribute.Name == "NAMN";
        if (!identity)
        {
            if (Mode == 1) { value = null; return true; }
            if (Mode == 2) return false;
            if (Mode == 3) { value = "must never leak this failed value"; return false; }
            if (Mode == 4) throw new InvalidOperationException("ordinary SDK exception");
        }
        return Values.TryGetValue(attribute.Name, out value);
    }
    public string GetAsString(DbAttribute attribute)
    {
        SnapshotFormattedAttributesHarness.ThrowingCalls++;
        string value = null;
        if (!Formatted(attribute, ref value)) throw new InvalidOperationException("attribute unavailable");
        return value;
    }
    public bool GetValidAsString(DbAttribute attribute, ref string value)
    {
        SnapshotFormattedAttributesHarness.TryCalls++;
        return Formatted(attribute, ref value);
    }
}
