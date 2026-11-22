using System;
using System.Collections.Generic;
using System.Linq;
using E3d21AiBridge;

internal static class CopyTreePolicyTests
{
    private static int checks;
    private static void Check(bool value, string message)
    {
        checks++;
        if (!value) throw new Exception(message);
    }
    private static void Reject(Action action, string message)
    {
        bool threw = false;
        try { action(); } catch (InvalidOperationException) { threw = true; }
        Check(threw, message);
    }
    private static ArgumentException RejectName(Action action, string message)
    {
        ArgumentException rejected = null;
        try { action(); } catch (ArgumentException error) { rejected = error; }
        Check(rejected != null && rejected.ParamName == "name", message);
        return rejected;
    }
    private static CopyTreeIdentity Node(string reference, string owner, string type, string path, string name = "")
    {
        return new CopyTreeIdentity { Reference = reference, Owner = owner, Type = type, Path = path, Name = name };
    }
    private static List<CopyTreeIdentity> Source()
    {
        return new List<CopyTreeIdentity>
        {
            Node("=1/10", "=1/1", "SUBE", "", "old-assembly"),
            Node("=1/11", "=1/10", "CYLI", "0", "separately-named-part"),
            Node("=1/12", "=1/11", "NCYL", "0/0", "bore"),
            Node("=1/13", "=1/10", "BOX", "1")
        };
    }
    private static List<CopyTreeIdentity> Copied()
    {
        return new List<CopyTreeIdentity>
        {
            Node("=1/20", "=1/1", "SUBE", ""), Node("=1/21", "=1/20", "CYLI", "0"),
            Node("=1/22", "=1/21", "NCYL", "0/0"), Node("=1/23", "=1/20", "BOX", "1")
        };
    }
    private static void CheckNameBoundaries()
    {
        string root50 = "/" + new string('R', 49), root51 = root50 + "R";
        string root44 = "/" + new string('R', 43), root45 = root44 + "R";
        Check(CopyTreePolicy.RequestedName(root50) == root50, "A 50-character explicit root name includes its slash and is not shortened.");
        RejectName(() => CopyTreePolicy.RequestedName(root51), "A 51-character root name must fail before any database work.");
        Check(CopyTreePolicy.RequestedName("//part") == "/part", "Existing leading-slash normalization remains supported.");
        RejectName(() => CopyTreePolicy.RequestedName("/" + root50), "The full input length is checked before leading-slash normalization.");

        var rootOnly = Source().Take(1).ToList();
        Check(CopyTreePolicy.Names(rootOnly, root50).SequenceEqual(new[] { root50 }), "Root-only copy can use all 50 characters.");
        RejectName(() => CopyTreePolicy.Names(rootOnly, root51), "The generated-name validator also checks the root name.");
        var namedChildren = Source();
        string[] originalNames = namedChildren.Select(node => node.Name).ToArray();
        string[] accepted = CopyTreePolicy.Names(namedChildren, root44);
        Check(accepted.SequenceEqual(new[] { root44, root44 + "-C0001", root44 + "-C0002", null })
            && accepted[1].Length == 50 && accepted[2].Length == 50, "A root of 44 characters leaves six characters for each named descendant.");
        ArgumentException childError = RejectName(() => CopyTreePolicy.Names(namedChildren, root45), "A 45-character root with a named descendant must fail instead of creating an overlong name.");
        Check(childError.Message.Contains("index 1") && childError.Message.Contains("51 characters"), "Rejection identifies the offending planned descendant and total length.");
        Check(namedChildren.Select(node => node.Name).SequenceEqual(originalNames), "Rejected copy naming never mutates source names or truncates them.");

        var maximumTree = Enumerable.Range(0, 2048).Select(index => Node("=1/" + (100 + index),
            index == 0 ? "=1/1" : "=1/100", index == 0 ? "SUBE" : "BOX", index == 0 ? "" : (index - 1).ToString())).ToList();
        string[] anonymous = CopyTreePolicy.Names(maximumTree, root50);
        Check(anonymous.Length == 2048 && anonymous[0] == root50 && anonymous.Skip(1).All(value => value == null),
            "A full-size tree with only anonymous descendants still permits a 50-character root.");
        maximumTree[2047].Name = "last-named-node";
        string[] lastNamed = CopyTreePolicy.Names(maximumTree, root44);
        Check(lastNamed[2047] == root44 + "-C2047" && lastNamed[2047].Length == 50,
            "The maximum admitted DFS index retains the exact six-character -C2047 suffix.");
        Check(lastNamed.Skip(1).Take(2046).All(value => value == null), "Late named descendants do not rename earlier anonymous descendants.");
        ArgumentException lastError = RejectName(() => CopyTreePolicy.Names(maximumTree, root45), "The complete generated mapping must be validated through its final node.");
        Check(lastError.Message.Contains("index 2047") && lastError.Message.Contains("51 characters"), "A late overlong descendant is diagnosed at its actual DFS index.");
    }
    private static void Main()
    {
        var source = Source();
        var protectedReferences = new HashSet<string>(source.Select(node => node.Reference).Concat(new[] { "=1/1", "=1/99" }), StringComparer.Ordinal);
        string name = CopyTreePolicy.RequestedName("/new-part");
        Check(name == "/new-part", "Explicit names must be canonical database names.");
        Check(CopyTreePolicy.SourceName("=1/12", "=1/12") == "", "Only the source's exact numeric reference means unnamed.");
        Check(CopyTreePolicy.SourceName("/part", "=1/12") == "part", "Named source nodes preserve their actual name.");
        Reject(() => CopyTreePolicy.SourceName("=1/99", "=1/12"), "A foreign numeric reference is not an unnamed node.");
        Reject(() => CopyTreePolicy.SourceName(null, "=1/12"), "Unreadable NAME cannot be treated as unnamed.");
        bool badName = false;
        try { CopyTreePolicy.RequestedName("no-slash"); } catch (ArgumentException) { badName = true; }
        Check(badName, "Execution names must begin with slash consistently across native and MCP inputs.");
        Check(CopyTreePolicy.IsSdkWorldEndpoint("=2000000001/0", "=2000000001/0"), "The SDK's exact Design WORLD endpoint must stop owner traversal.");
        Check(!CopyTreePolicy.IsSdkWorldEndpoint("=2000000001/1", "=2000000001/1"), "A matching nonzero reference must retain ordinary element validity checks.");
        Check(!CopyTreePolicy.IsSdkWorldEndpoint("=2000000005/0", "=2000000001/0"), "An unrelated database WORLD wrapper cannot bypass ordinary checks.");
        Check(!CopyTreePolicy.IsSdkWorldEndpoint("=0/0", "=0/0"), "A null/system reference is not a valid SDK Design WORLD.");
        Check(!CopyTreePolicy.IsSdkWorldEndpoint(null, "=2000000001/0"), "An absent frame is not a WORLD endpoint.");
        string[] names = CopyTreePolicy.Names(source, name);
        Check(names.SequenceEqual(new[] { "/new-part", "/new-part-C0001", "/new-part-C0002", null }),
            "Named descendants use deterministic DFS names regardless of source prefix; unnamed descendants remain unnamed.");
        CheckNameBoundaries();
        var copy = Copied();
        CopyTreePolicy.RequireNewRoot(copy[0], "=1/1", "SUBE", protectedReferences);
        CopyTreePolicy.RequireMatchingTree(source, copy, "=1/1", protectedReferences);
        checks++;
        CopyTreePolicy.RequireAppend(new[] { "=1/10", "=1/99" }, new[] { "=1/10", "=1/99", "=1/20" }, "=1/20");
        checks++;
        Reject(() => CopyTreePolicy.RequireNewRoot(source[0], "=1/1", "SUBE", protectedReferences), "Existing source root can never become setter/cleanup eligible.");
        Reject(() => CopyTreePolicy.RequireNewRoot(Node("=1/20", "=1/2", "SUBE", ""), "=1/1", "SUBE", protectedReferences), "Unexpected root owner must be rejected.");
        Reject(() => CopyTreePolicy.RequireNewRoot(Node("=1/20", "=1/1", "EQUI", ""), "=1/1", "SUBE", protectedReferences), "Unexpected root type must be rejected.");
        copy = Copied(); copy[2].Reference = "=1/12";
        Reject(() => CopyTreePolicy.RequireMatchingTree(source, copy, "=1/1", protectedReferences), "A source negative attached below a new root must prohibit recursive delete.");
        copy = Copied(); copy[2].Reference = "=1/99";
        Reject(() => CopyTreePolicy.RequireMatchingTree(source, copy, "=1/1", protectedReferences), "A protected preexisting destination descendant must prohibit delete.");
        copy = Copied(); copy[2].Reference = "=1/21";
        Reject(() => CopyTreePolicy.RequireMatchingTree(source, copy, "=1/1", protectedReferences), "Repeated new references must be rejected.");
        copy = Copied(); copy[2].Owner = "=1/20";
        Reject(() => CopyTreePolicy.RequireMatchingTree(source, copy, "=1/1", protectedReferences), "Owner/path mismatch must be rejected.");
        copy = Copied(); copy[2].Path = "0";
        Reject(() => CopyTreePolicy.RequireMatchingTree(source, copy, "=1/1", protectedReferences), "Duplicate structural paths must be rejected.");
        copy = Copied(); copy[1].Type = "BOX";
        Reject(() => CopyTreePolicy.RequireMatchingTree(source, copy, "=1/1", protectedReferences), "A matching node count does not prove matching geometry types.");
        copy = Copied(); copy.RemoveAt(2);
        Reject(() => CopyTreePolicy.RequireMatchingTree(source, copy, "=1/1", protectedReferences), "Missing negative copies must be rejected.");
        copy = Copied(); copy.Add(Node("=1/24", "=1/20", "BOX", "2"));
        Reject(() => CopyTreePolicy.RequireMatchingTree(source, copy, "=1/1", protectedReferences), "Unexpected copied nodes must be rejected.");
        Reject(() => CopyTreePolicy.RequireAppend(new[] { "=1/10", "=1/99" }, new[] { "=1/99", "=1/10", "=1/20" }, "=1/20"), "Reordered existing destination membership is not a verified append.");
        Reject(() => CopyTreePolicy.RequireAppend(new[] { "=1/10", "=1/99" }, new[] { "=1/10", "=1/20" }, "=1/20"), "Lost existing destination membership is not a verified append.");
        // Structural validity/protected exclusion alone cannot authorize partial-copy cleanup.
        var partial = Copied().Take(2).ToList();
        CopyTreePolicy.RequireNewTree(partial, "=1/20", "=1/1", protectedReferences);
        checks++;
        Reject(() => CopyTreePolicy.RequireEmptyRoot(partial, "=1/20", "=1/1", "SUBE", protectedReferences),
            "An uncaptured nonempty subtree cannot be deleted even when all nodes avoid the protected baseline.");
        partial[1].Owner = "=1/10";
        Reject(() => CopyTreePolicy.RequireNewTree(partial, "=1/20", "=1/1", protectedReferences), "Partial-copy cleanup cannot cross into source ownership.");
        CopyTreePolicy.RequireEmptyRoot(Copied().Take(1).ToList(), "=1/20", "=1/1", "SUBE", protectedReferences);
        checks++;
        string[] beforeMembers = { "=1/10", "=1/99" };
        string[] appendedMembers = { "=1/10", "=1/99", "=1/20" };
        copy = Copied();
        CopyTreeCapture captured = CopyTreePolicy.CaptureReturnedTree(source, copy, "=1/20", "=1/1",
            protectedReferences, beforeMembers, appendedMembers);
        captured.RequireUnchanged(Copied());
        checks++;
        copy[1].Reference = "=1/777";
        string[] exposed = captured.References; exposed[0] = "=1/999";
        Check(captured.References.SequenceEqual(new[] { "=1/20", "=1/21", "=1/22", "=1/23" }),
            "Captured identities and exported reference arrays cannot alias mutable input snapshots.");
        foreach (string mutation in new[] { "add", "remove", "replace", "owner", "type", "path", "order" })
        {
            var changed = Copied();
            if (mutation == "add") changed.Add(Node("=1/500", "=1/20", "BOX", "2"));
            if (mutation == "remove") changed.RemoveAt(2);
            if (mutation == "replace") changed[2].Reference = "=1/500";
            if (mutation == "owner") changed[2].Owner = "=1/23";
            if (mutation == "type") changed[2].Type = "NBOX";
            if (mutation == "path") changed[2].Path = "1/0";
            if (mutation == "order") { var node = changed[1]; changed[1] = changed[3]; changed[3] = node; }
            Reject(() => captured.RequireUnchanged(changed), "Captured cleanup must reject " + mutation + " after acceptance.");
        }
        var renamed = Copied(); renamed[1].Name = "new-name"; captured.RequireUnchanged(renamed); checks++;
        Reject(() => CopyTreePolicy.CaptureReturnedTree(source, Copied().Take(2).ToList(), "=1/20", "=1/1",
            protectedReferences, beforeMembers, appendedMembers), "A partial native result cannot become an accepted cleanup tree.");
        Reject(() => CopyTreePolicy.CaptureReturnedTree(source, Copied(), "=1/20", "=1/1",
            protectedReferences, beforeMembers, appendedMembers.Concat(new[] { "=1/700" }).ToArray()),
            "Concurrent destination membership additions prevent capture.");
        Console.WriteLine("Copy tree policy checks passed: " + checks);
    }
}
