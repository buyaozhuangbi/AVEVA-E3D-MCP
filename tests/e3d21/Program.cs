using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Globalization;
using System.Threading;
using E3d21AiBridge;

internal static class Program
{
    private sealed class TestMember
    {
        public string Ref;
        public string Type;
    }

    private static readonly Dictionary<int, int[]> Tree = new Dictionary<int, int[]>
    {
        [1] = new[] { 2, 5 }, [2] = new[] { 3, 4 }, [5] = new[] { 6, 7 }
    };

    private static IEnumerable<int> Children(int value)
    {
        int[] children;
        return Tree.TryGetValue(value, out children) ? children : new int[0];
    }

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    private static int Count(BoundedHierarchy<int>.Node node)
    {
        return 1 + node.Children.Sum(Count);
    }

    private static IEnumerable<int> BrokenChildren(int value)
    {
        if (value != 1) yield break;
        yield return 2;
        throw new InvalidOperationException("Database member read failed");
    }

    private static int largeHierarchyChildReads;

    private static IEnumerable<int> LargeHierarchyChildren(int value)
    {
        if (value != 1) yield break;
        for (int child = 2; child <= 1001; child++)
        {
            largeHierarchyChildReads++;
            yield return child;
        }
    }

    private static IEnumerable<int> LateHierarchyErrorChildren(int value)
    {
        if (value == 1)
        {
            for (int child = 2; child <= 1001; child++)
                yield return child;
        }
        else if (value == 1001 || value == 1002)
            yield return value + 1;
        else if (value == 1003)
            throw new InvalidOperationException("Late nested member read failed");
    }

    private static void CheckLargeHierarchies()
    {
        largeHierarchyChildReads = 0;
        var exact = BoundedHierarchy<int>.Read(1, 8, 1001, LargeHierarchyChildren);
        Check(exact.Complete && exact.Elements.Count == 1001 && Count(exact.Root) == 1001 &&
            exact.Elements.SequenceEqual(Enumerable.Range(1, 1001)),
            "A 1001-element hierarchy must be complete at its exact global budget");
        Check(largeHierarchyChildReads == 1000, "An exact large hierarchy must read each child once");

        largeHierarchyChildReads = 0;
        var limited = BoundedHierarchy<int>.Read(1, 8, 1000, LargeHierarchyChildren);
        Check(!limited.Complete && limited.Elements.Count == 1000 && Count(limited.Root) == 1000 &&
            limited.TruncationReasons.Contains("limit") && limited.ReadErrors.Count == 0 &&
            limited.Elements.SequenceEqual(exact.Elements.Take(1000)),
            "Large hierarchy truncation must preserve preorder and the global node budget");
        Check(largeHierarchyChildReads == 1000, "A bounded large hierarchy must stop after its omitted-child lookahead");

        var lateError = BoundedHierarchy<int>.Read(1, 8, 2000, LateHierarchyErrorChildren);
        Check(!lateError.Complete && lateError.Elements.Count == 1003 && Count(lateError.Root) == 1003 &&
            lateError.ReadErrors.Count == 1 && lateError.ReadErrors[0].Contains("Late nested") &&
            lateError.TruncationReasons.Count == 0,
            "A nested read error after the first 1000 elements must retain partial coverage without certifying completeness");
        Console.WriteLine("Large hierarchy regression passed: 1001-node exact budget, truncation, bounded lookahead and late nested errors.");
    }

    private static int siblingReads;

    private static IEnumerable<int> ManyMembers()
    {
        for (int index = 1; index <= 100000; index++)
        {
            siblingReads++;
            yield return index;
        }
    }

    private static void CheckMemberPages()
    {
        var first = MemberPage<int>.Read(ManyMembers(), 5, value => value.ToString());
        Check(first.Items.SequenceEqual(new[] { 1, 2, 3, 4, 5 }) && first.HasMore == true && siblingReads == 6,
            "Member pages must inspect only the requested page plus one sibling");
        var exact = MemberPage<int>.Read(new[] { 1, 2 }, 2, value => value.ToString());
        Check(exact.HasMore == false && exact.Items.Count == 2, "Exact-sized final page is exhausted");
        var empty = MemberPage<int>.Read(new int[0], 1, value => value.ToString());
        Check(empty.HasMore == false && empty.Items.Count == 0, "Empty leaf has no next page");
        var broken = MemberPage<int>.Read(BrokenChildren(1), 1, value => value.ToString());
        Check(broken.HasMore == null && broken.Items.Count == 1 && broken.ReadErrors.Count == 1,
            "A failed lookahead must retain partial items without certifying exhaustion");
        var cycle = MemberPage<int>.Read(new[] { 1, 2, 1 }, 2, value => value.ToString());
        Check(cycle.HasMore == null && cycle.ReadErrors.Count == 1, "Repeated sibling references must be reported");
        var aliases = new[]
        {
            new TestMember { Ref = "=1/2", Type = "BRAN" },
            new TestMember { Ref = "=1/2", Type = "TUBI" },
            new TestMember { Ref = "=1/3", Type = "ELBO" },
        };
        Func<TestMember, string> memberKey = member => member.Ref + "\0" + member.Type;
        var aliasPage = MemberPage<TestMember>.Read(aliases, 2, memberKey, member => member.Ref, member => member.Type, 0);
        Check(aliasPage.Items.Count == 2 && aliasPage.Items[0].Type == "BRAN" && aliasPage.Items[1].Type == "TUBI" &&
            aliasPage.LastRef == "=1/2" && aliasPage.LastType == "TUBI" && aliasPage.LastOrdinal == 2 &&
            aliasPage.HasMore == true && aliasPage.ReadErrors.Count == 0,
            "Different element types may share a refno while remaining separate siblings");
        var exactAliasDuplicate = MemberPage<TestMember>.Read(new[] { aliases[1], aliases[1] }, 2,
            memberKey, member => member.Ref, member => member.Type, 1);
        Check(exactAliasDuplicate.Items.Count == 1 && exactAliasDuplicate.HasMore == null && exactAliasDuplicate.ReadErrors.Count == 1,
            "Repeated identical typed sibling identities must remain a read error");
        var cursor = new MemberPageCursor { Session = "session", Project = "项目", Mdb = "ALL", Parent = "=1/2", After = "=1/3" };
        cursor.ParentType = "BRAN";
        cursor.AfterType = "TUBI";
        cursor.AfterIdentity = Convert.ToBase64String(Encoding.UTF8.GetBytes(cursor.After + "\0" + cursor.AfterType));
        cursor.AfterOrdinal = 2;
        var decoded = MemberPageCursor.Decode(cursor.Encode());
        Check(decoded.Project == cursor.Project && decoded.Parent == cursor.Parent && decoded.After == cursor.After,
            "Cursor must preserve the session, project, parent and last member");
        Check(decoded.ParentType == cursor.ParentType && decoded.AfterType == cursor.AfterType &&
            decoded.AfterIdentity == cursor.AfterIdentity && decoded.AfterOrdinal == cursor.AfterOrdinal,
            "Cursor must preserve alias types, identity and the exact sibling index");
        string[] signed = cursor.Encode().Split('.');
        string[] payload = Encoding.UTF8.GetString(Convert.FromBase64String(signed[0])).Split('\n');
        payload[6] = "=1/99";
        payload[7] = "TUBI";
        payload[8] = Convert.ToBase64String(Encoding.UTF8.GetBytes("=1/99\0TUBI"));
        payload[9] = "4";
        string coherentForgery = Convert.ToBase64String(Encoding.UTF8.GetBytes(string.Join("\n", payload))) + "." + signed[1];
        bool forgedRejected = false;
        try { MemberPageCursor.Decode(coherentForgery); } catch (ArgumentException) { forgedRejected = true; }
        Check(forgedRejected, "A coherently rewritten later anchor must fail cursor authentication");
        foreach (string invalid in new[] {
            "!", new string('a', 2049),
            Convert.ToBase64String(Encoding.UTF8.GetBytes("1\ns\np\nm\n=1/2\n=1/3")),
            Convert.ToBase64String(Encoding.UTF8.GetBytes("2\ns\np\nm\n=1/2\nBRAN\n=1/3\nTUBI\n!\n2")),
            signed[0] + "." + Convert.ToBase64String(new byte[32])
        })
        {
            bool rejected = false;
            try { MemberPageCursor.Decode(invalid); } catch (ArgumentException) { rejected = true; }
            Check(rejected, "Invalid or unsupported member cursors must be rejected");
        }
        Console.WriteLine("Member page regression passed: bounded reads, lookahead errors, cycles, exact pages and cursor validation.");
    }

    public static void Main()
    {
        CheckAttributeInputs();
        CheckMemberPages();
        CheckLargeHierarchies();
        var full = BoundedHierarchy<int>.Read(1, 8, 7, Children);
        Check(full.Complete && full.Elements.Count == 7 && Count(full.Root) == 7, "Exact budget must be complete");
        for (int limit = 1; limit < 7; limit++)
        {
            var result = BoundedHierarchy<int>.Read(1, 8, limit, Children);
            Check(!result.Complete && result.TruncationReasons.Contains("limit"), "Global limit must report truncation");
            Check(result.Elements.Count == limit && Count(result.Root) == limit, "Snapshot and validation counts must match");
            Check(result.Elements.SequenceEqual(full.Elements.Take(limit)), "Truncated traversal must preserve preorder");
        }
        var shallow = BoundedHierarchy<int>.Read(1, 1, 100, Children);
        Check(!shallow.Complete && shallow.Elements.SequenceEqual(new[] { 1, 2, 5 }), "Depth budget must retain siblings");
        Check(shallow.TruncationReasons.Contains("depth"), "Depth truncation must be explicit");
        var rootOnly = BoundedHierarchy<int>.Read(1, 0, 1, Children);
        Check(!rootOnly.Complete && rootOnly.Elements.Count == 1, "Root with children is incomplete at depth 0");
        var leaf = BoundedHierarchy<int>.Read(3, 0, 1, Children);
        Check(leaf.Complete, "A leaf is complete at depth 0 with limit 1");
        var broken = BoundedHierarchy<int>.Read(1, 8, 100, BrokenChildren);
        Check(!broken.Complete && broken.ReadErrors.Count == 1, "Enumeration errors cannot produce a complete result");
        Check(broken.Elements.SequenceEqual(new[] { 1, 2 }) && Count(broken.Root) == 2, "Preserve partial reads after errors");
        Console.WriteLine("Hierarchy regression passed: global budgets, depth, exact limits, leaf nodes, and partial read failures.");
    }

    private static void CheckAttributeInputs()
    {
        CultureInfo originalCulture = Thread.CurrentThread.CurrentCulture;
        try
        {
            Thread.CurrentThread.CurrentCulture = CultureInfo.GetCultureInfo("de-DE");
            Check((double)AttributeEditPolicy.Parse("HEIG", "1.25e2") == 125.0,
                "Numeric edits must use invariant notation regardless of the E3D process culture");
            Check((string)AttributeEditPolicy.Parse("DESC", "Pump \"A\" / 泵") == "Pump \"A\" / 泵",
                "Text edits must preserve content instead of treating it as PML");
            foreach (string invalid in new[] { "NaN", "Infinity", "-Infinity", "1e9999", "1,25", "10mm", "", "1; NEW BOX" })
            {
                bool rejected = false;
                try { AttributeEditPolicy.Parse("HEIG", invalid); } catch (ArgumentException) { rejected = true; }
                Check(rejected, "Non-finite or malformed numeric input must be rejected before writing: " + invalid);
            }
            Check(AttributeEditPolicy.InputKind("POS") == null, "Spatial attributes must not silently use scalar setters");
            Check(AttributeEditPolicy.SupportsSdkType("NAME", "STRING", true),
                "E3D NAME string storage must remain editable when IsArray is true");
            Check(AttributeEditPolicy.SupportsSdkType("DESC", "STRING", true), "Description text must use the text setter");
            Check(!AttributeEditPolicy.SupportsSdkType("DESC", "STRINGARRAY", true), "String arrays must not use a scalar setter");
            Check(!AttributeEditPolicy.SupportsSdkType("HEIG", "DOUBLE", true), "Numeric arrays must not use a scalar setter");
        }
        finally { Thread.CurrentThread.CurrentCulture = originalCulture; }
        Console.WriteLine("Attribute input regression passed: finite numbers, locale independence, text preservation and restricted edits.");
    }
}
