using System;
using System.Collections.Generic;
using System.Linq;
using E3d21AiBridge;

internal static class DescendantSearchTests
{
    private sealed class Node
    {
        public int Id;
        public Node Parent, First, Next;
        public bool Broken;
        public string Reference { get { return "=1/" + Id; } }
    }

    private static int checks, firstReads, nextReads, evaluated;

    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) throw new Exception(message);
    }

    private static void Reject(Action operation, string message)
    {
        bool rejected = false;
        try { operation(); } catch (ArgumentException) { rejected = true; }
        Check(rejected, message);
    }

    private static Node Child(Node parent, int id)
    {
        var node = new Node { Id = id, Parent = parent };
        if (parent.First == null) parent.First = node;
        else
        {
            Node end = parent.First;
            while (end.Next != null) end = end.Next;
            end.Next = node;
        }
        return node;
    }

    private static DescendantSearchPage<Node> Read(IList<Node> path, bool continuation = false,
        int depth = 64, int limit = 200, int scanLimit = 1000, bool truncated = false,
        Func<Node, bool> filter = null, Func<Node, Node> next = null)
    {
        return DescendantSearchPage<Node>.Read(path, continuation, depth, limit, scanLimit, truncated,
            node => { firstReads++; return node.First; },
            next ?? (node => { nextReads++; return node.Next; }), node => node.Parent,
            node => { if (node.Broken) throw new InvalidOperationException("Unreadable native reference."); return node.Reference; },
            (node, level) => { evaluated++; return filter == null || filter(node) ? (object)node.Id : null; });
    }

    private static void PagesAndScope()
    {
        var root = new Node { Id = 1 };
        Node left = Child(root, 2);
        Child(left, 3);
        Child(left, 4);
        Node right = Child(root, 5);
        Child(right, 6);
        Child(right, 7);
        var first = Read(new[] { root }, limit: 2);
        Check(first.Items.Cast<int>().SequenceEqual(new[] { 2, 3 }) && first.HasMore == true, "First page must use database preorder, excluding root.");
        var second = Read(first.NextPath, true, limit: 2);
        var third = Read(second.NextPath, true, limit: 2);
        Check(second.Items.Cast<int>().SequenceEqual(new[] { 4, 5 }), "Resume must preserve preorder across owner ascents.");
        Check(third.Items.Cast<int>().SequenceEqual(new[] { 6, 7 }) && third.HasMore == false && third.StopReason == "exhausted", "Exactly full final page must report exhaustion.");
        var filtered = Read(new[] { root }, filter: node => node.Id % 2 == 0);
        Check(filtered.Items.Cast<int>().SequenceEqual(new[] { 2, 4, 6 }) && filtered.VisitedCount == 6, "Filters must not prune nonmatching ancestors.");
        var empty = Read(new[] { root }, filter: node => false);
        Check(empty.Items.Count == 0 && empty.HasMore == false && empty.VisitedCount == 6, "Empty completed search must be distinct from unsearched remainder.");
        var leaf = Read(new[] { left.First });
        Check(leaf.HasMore == false && leaf.VisitedCount == 0 && leaf.Items.Count == 0, "Root-only leaf search excludes its root.");
        var shallow = Read(new[] { root }, depth: 1, limit: 1);
        Check(shallow.DepthTruncated && shallow.HasMore == true && shallow.Items.Cast<int>().SequenceEqual(new[] { 2 }), "A real omitted child must mark depth truncation.");
        var shallowEnd = Read(shallow.NextPath, true, depth: 1, limit: 1, truncated: shallow.DepthTruncated);
        Check(shallowEnd.DepthTruncated && shallowEnd.HasMore == false, "Depth truncation must remain true on the final continuation.");
        var directLeaf = Read(new[] { left }, depth: 1);
        Check(!directLeaf.DepthTruncated && directLeaf.HasMore == false, "Leaf children exactly at depth boundary are complete.");
    }

    private static void BoundedWork()
    {
        var root = new Node { Id = 1 };
        Node last = null;
        for (int id = 2; id <= 100001; id++)
        {
            var node = new Node { Id = id, Parent = root };
            if (last == null) root.First = node; else last.Next = node;
            last = node;
        }
        firstReads = nextReads = evaluated = 0;
        var page = Read(new[] { root }, limit: 5);
        Check(page.VisitedCount == 5 && evaluated == 5 && page.HasMore == true, "Result budget must bound evaluated elements.");
        Check(firstReads == 6 && nextReads == 5, "Large sibling lists must use only a first child plus bounded successor navigation.");
        firstReads = nextReads = evaluated = 0;
        var resume = Read(page.NextPath, true, limit: 5);
        Check(resume.Items.Cast<int>().SequenceEqual(new[] { 7, 8, 9, 10, 11 }) && firstReads == 5 && nextReads == 5, "Continuation must not scan earlier siblings or restart at root.");
        firstReads = nextReads = evaluated = 0;
        var sparse = Read(new[] { root }, filter: node => false);
        Check(sparse.Items.Count == 0 && sparse.VisitedCount == 1000 && evaluated == 1000 && sparse.HasMore == true && sparse.StopReason == "scan_limit", "An empty matching page still needs a cursor after 1000 candidates.");
        Check(firstReads == 1001 && nextReads == 1000, "Scan bound must not materialize the remaining wide subtree.");
        var bounded = new Node { Id = 200000 };
        last = null;
        for (int id = 1; id <= 1014; id++)
        {
            var node = new Node { Id = id, Parent = bounded };
            if (last == null) bounded.First = node; else last.Next = node;
            last = node;
        }
        var all = new List<int>();
        var current = Read(new[] { bounded }, limit: 37);
        while (true)
        {
            all.AddRange(current.Items.Cast<int>());
            Check(current.VisitedCount <= 1000 && current.Items.Count <= 37, "Every page of a 1015-node model must respect both limits.");
            if (current.HasMore == false) break;
            current = Read(current.NextPath, true, limit: 37);
        }
        Check(all.SequenceEqual(Enumerable.Range(1, 1014)) && all.Distinct().Count() == 1014, "A 1015-node root must yield every descendant once.");
    }

    private static void FailuresAndCycles()
    {
        var root = new Node { Id = 1 };
        Node one = Child(root, 2), two = Child(root, 3);
        var failedLookahead = Read(new[] { root }, limit: 1,
            next: node => { throw new InvalidOperationException("Native Next failed."); });
        Check(failedLookahead.Items.Count == 1 && failedLookahead.HasMore == null && failedLookahead.NextPath == null
            && failedLookahead.ReadErrors.Count == 1 && failedLookahead.StopReason == "read_error", "Failed lookahead must preserve rows and never claim exhaustion or continuation.");
        var valid = Read(new[] { root }, limit: 1);
        two.Parent = new Node { Id = 100 };
        var moved = Read(valid.NextPath, true);
        Check(moved.HasMore == null && moved.Items.Count == 0 && moved.ReadErrors.Count == 1, "Moved cursor path must not escape its owner scope.");
        two.Parent = root;
        two.Broken = true;
        var unreadable = Read(new[] { root });
        Check(unreadable.Items.Count == 1 && unreadable.HasMore == null && unreadable.ReadErrors.Count == 1, "Unreadable descendants must preserve preceding rows without a false empty success.");
        two.Broken = false;
        two.Next = one;
        var cycle = Read(new[] { root });
        Check(cycle.HasMore == null && cycle.ReadErrors.Count == 1 && cycle.VisitedCount == 2, "Sibling cycles within a page must be bounded and reported.");
        var cycleAtBoundary = Read(new[] { root }, limit: 2);
        Check(cycleAtBoundary.HasMore == null && cycleAtBoundary.NextPath == null && cycleAtBoundary.Items.Count == 2,
            "A cycle discovered by page lookahead must fail before returning a misleading cursor.");
        two.Next = null;
        one.First = root;
        var escape = Read(new[] { root });
        Check(escape.HasMore == null && escape.ReadErrors.Count == 1, "An ancestor returned as child must fail the scope guard.");
        Reject(() => Read(new[] { root }, depth: 65), "Depth over 64 must be rejected.");
        Reject(() => Read(new[] { root }, limit: 201), "Result limit over 200 must be rejected.");
        Reject(() => Read(new[] { root }, scanLimit: 1001), "Scan limit over 1000 must be rejected.");
    }

    private static void SignedCursors()
    {
        byte[] key = DescendantSearchCursor.CreateKey();
        var original = new DescendantSearchCursor
        {
            Session = "session", Project = "项目", Mdb = "ALL", Root = "=1/1", ElementType = "NOZZ",
            NameContains = "泵 A", MaxDepth = 64, DepthTruncated = true,
            Path = Enumerable.Range(1, 65).Select(id => "=1/" + id).ToArray()
        };
        string token = original.Encode(key);
        var decoded = DescendantSearchCursor.Decode(token, key);
        Check(decoded.Session == original.Session && decoded.Project == original.Project && decoded.Mdb == original.Mdb
            && decoded.Root == original.Root && decoded.ElementType == original.ElementType && decoded.NameContains == original.NameContains
            && decoded.MaxDepth == 64 && decoded.DepthTruncated && decoded.Path.SequenceEqual(original.Path), "Signed cursor must retain exact scope, Unicode literal filter and full depth path.");
        Check(token.Length <= DescendantSearchCursor.MaximumLength, "Maximum-depth cursor must fit the advertised input bound.");
        Reject(() => DescendantSearchCursor.Decode(token, DescendantSearchCursor.CreateKey()), "Another bridge context cannot replay a cursor.");
        char replacement = token[4] == 'A' ? 'B' : 'A';
        string changed = token.Substring(0, 4) + replacement + token.Substring(5);
        Reject(() => DescendantSearchCursor.Decode(changed, key), "Payload tampering must be rejected by signature.");
        string[] pieces = token.Split('.');
        byte[] signature = Convert.FromBase64String(pieces[1]);
        signature[31] ^= 1;
        Reject(() => DescendantSearchCursor.Decode(pieces[0] + "." + Convert.ToBase64String(signature), key), "Signature tampering must be rejected.");
        foreach (string bad in new[] { "!", "a.b.c", new string('a', 16385), "YWJj.YWJj" })
            Reject(() => DescendantSearchCursor.Decode(bad, key), "Malformed or oversized cursor must be rejected.");
        original.Path[1] = "=2/999";
        original.Path[2] = "=2/999";
        Reject(() => original.Encode(key), "Repeated ancestor references cannot be encoded.");
        Check(!DescendantSearchCursor.IsReference("=1/-2") && !DescendantSearchCursor.IsReference("/NAME")
            && !DescendantSearchCursor.IsReference("=0/1") && DescendantSearchCursor.IsReference("=2000000001/2617"), "Cursor references must remain bounded numeric DBREF pairs.");
    }

    public static void Main()
    {
        PagesAndScope();
        BoundedWork();
        FailuresAndCycles();
        SignedCursors();
        Console.WriteLine("Descendant search regression passed: " + checks + " checks (paging, scope, budgets, partial failures and signed cursors).");
    }
}
