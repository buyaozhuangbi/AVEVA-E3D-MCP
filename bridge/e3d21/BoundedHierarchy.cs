using System;
using System.Collections.Generic;

namespace E3d21AiBridge;

// Pure traversal shared by snapshots and validation. The node budget applies
// to the whole tree, including the root, rather than to each sibling list.
internal sealed class BoundedHierarchy<T>
{
    internal sealed class Node
    {
        public T Value;
        public readonly List<Node> Children = new List<Node>();
    }

    public Node Root { get; private set; }
    public readonly List<T> Elements = new List<T>();
    public readonly List<string> TruncationReasons = new List<string>();
    public readonly List<string> ReadErrors = new List<string>();
    public bool Complete { get { return TruncationReasons.Count == 0 && ReadErrors.Count == 0; } }

    public static BoundedHierarchy<T> Read(T root, int depth, int limit, Func<T, IEnumerable<T>> children)
    {
        if (depth < 0) throw new ArgumentOutOfRangeException("depth");
        if (limit < 1) throw new ArgumentOutOfRangeException("limit");
        if (children == null) throw new ArgumentNullException("children");
        BoundedHierarchy<T> result = new BoundedHierarchy<T>();
        result.Root = result.Visit(root, depth, limit, children);
        return result;
    }

    private Node Visit(T value, int depth, int limit, Func<T, IEnumerable<T>> children)
    {
        Node node = new Node { Value = value };
        Elements.Add(value);
        try
        {
            foreach (T child in children(value))
            {
                // Test only when an omitted child exists: a leaf at depth 0
                // or a complete tree exactly at the limit is still complete.
                if (depth == 0 || Elements.Count >= limit)
                {
                    string reason = depth == 0 ? "depth" : "limit";
                    if (!TruncationReasons.Contains(reason))
                        TruncationReasons.Add(reason);
                    break;
                }
                node.Children.Add(Visit(child, depth - 1, limit, children));
            }
        }
        catch (Exception ex)
        {
            // Retain partial results, but never certify an unreadable tree.
            ReadErrors.Add(ex.Message);
        }
        return node;
    }
}
