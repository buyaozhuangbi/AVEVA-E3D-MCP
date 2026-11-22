using System;
using System.Collections.Generic;

namespace E3d21AiBridge;

public interface IE3d21EquipmentManifestContext
{
    object ReadEquipmentManifest(string refno, int maxDepth, int limit, IList<string> attributes, string sessionId);
}

internal sealed class EquipmentManifestRequest
{
    internal const int DefaultMaxDepth = 8;
    internal const int DefaultLimit = 64;
    internal const int MaximumAttributes = 10;
    internal const int MaximumTextLength = 4096;

    internal string Refno;
    internal int MaxDepth;
    internal int Limit;
    internal List<string> Attributes;

    internal static EquipmentManifestRequest Validate(string refno, int maxDepth, int limit, IList<string> attributes)
    {
        if (refno == null || refno.Length > 256)
            throw new ArgumentException("refno must explicitly identify /NAME or =database/element, up to 256 characters.");
        string target = ModelEditInput.ExplicitReference(refno, "refno");
        if (target.IndexOf('*') >= 0)
            throw new ArgumentException("refno must be a literal equipment name or database reference.");
        if (maxDepth < 1 || maxDepth > 32)
            throw new ArgumentException("max_depth must be from 1 through 32.");
        if (limit < 1 || limit > 200)
            throw new ArgumentException("limit must be from 1 through 200.");
        if (attributes != null && attributes.Count > MaximumAttributes)
            throw new ArgumentException("attributes must contain at most 10 names.");

        List<string> names = new List<string>();
        HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
        if (attributes != null)
        {
            foreach (string attribute in attributes)
            {
                string name = (attribute ?? "").Trim();
                if (name.Length == 0 || name.Length > 64 || !IsAttributeName(name))
                    throw new ArgumentException("Use attribute names only, for example POS, HEIG, or :CUSTOM.");
                name = name.ToUpperInvariant();
                if (!seen.Add(name))
                    throw new ArgumentException("attributes must not contain duplicate names.");
                names.Add(name);
            }
        }
        if (limit * names.Count > 500)
            throw new ArgumentException("limit times the number of attributes must not exceed 500.");
        return new EquipmentManifestRequest { Refno = target, MaxDepth = maxDepth, Limit = limit, Attributes = names };
    }

    private static bool IsAttributeName(string value)
    {
        foreach (char c in value)
            if (!(c >= 'A' && c <= 'Z') && !(c >= 'a' && c <= 'z') && !(c >= '0' && c <= '9') && c != ':' && c != '_')
                return false;
        return true;
    }

    internal static string ErrorText(string value)
    {
        if (string.IsNullOrWhiteSpace(value)) return "The equipment manifest read failed without an SDK message.";
        if (value.Length <= MaximumTextLength) return value;
        const string suffix = " [message truncated]";
        int length = MaximumTextLength - suffix.Length;
        if (char.IsHighSurrogate(value[length - 1])) length--;
        return value.Substring(0, length) + suffix;
    }
}

internal sealed class EquipmentManifestTraversal<T> where T : class
{
    internal sealed class Row
    {
        internal T Value;
        internal string Reference;
        internal int Index;
        internal int? ParentIndex;
        internal int Depth;
        internal int? SiblingIndex;
    }

    internal sealed class ReadError
    {
        internal string ParentReference;
        internal int SiblingIndex;
        internal string Message;
    }

    internal readonly List<Row> Rows = new List<Row>();
    internal readonly List<ReadError> ReadErrors = new List<ReadError>();
    internal readonly List<string> TruncationReasons = new List<string>();
    private readonly HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
    private readonly int maxDepth;
    private readonly int limit;
    private readonly Func<T, int, T> memberAt;
    private readonly Func<T, string> reference;
    private readonly Func<T, string> ownerReference;
    private readonly Action<Exception> onError;

    private EquipmentManifestTraversal(int maxDepth, int limit, Func<T, int, T> memberAt,
        Func<T, string> reference, Func<T, string> ownerReference, Action<Exception> onError)
    {
        this.maxDepth = maxDepth;
        this.limit = limit;
        this.memberAt = memberAt;
        this.reference = reference;
        this.ownerReference = ownerReference;
        this.onError = onError;
    }

    internal static EquipmentManifestTraversal<T> Read(T root, string rootRefno, int maxDepth, int limit,
        Func<T, int, T> memberAt, Func<T, string> reference, Func<T, string> ownerReference, Action<Exception> onError)
    {
        if (root == null) throw new ArgumentNullException(nameof(root));
        if (string.IsNullOrEmpty(rootRefno)) throw new ArgumentException("The root reference is required.", nameof(rootRefno));
        if (maxDepth < 1 || maxDepth > 32) throw new ArgumentOutOfRangeException(nameof(maxDepth));
        if (limit < 1 || limit > 200) throw new ArgumentOutOfRangeException(nameof(limit));
        if (memberAt == null) throw new ArgumentNullException(nameof(memberAt));
        if (reference == null) throw new ArgumentNullException(nameof(reference));
        if (ownerReference == null) throw new ArgumentNullException(nameof(ownerReference));

        var result = new EquipmentManifestTraversal<T>(maxDepth, limit, memberAt, reference, ownerReference, onError);
        result.seen.Add(rootRefno);
        result.Visit(root, rootRefno, null, 0, null);
        return result;
    }

    private bool Visit(T node, string refno, int? parentIndex, int depth, int? siblingIndex)
    {
        int rowIndex = Rows.Count;
        Rows.Add(new Row { Value = node, Reference = refno, Index = rowIndex, ParentIndex = parentIndex, Depth = depth, SiblingIndex = siblingIndex });
        for (int ordinal = 1; ; ordinal++)
        {
            T child;
            string childRefno;
            try
            {
                child = memberAt(node, ordinal);
                if (child == null) return true;
                childRefno = reference(child);
                if (string.IsNullOrEmpty(childRefno) || ownerReference(child) != refno)
                    throw new InvalidOperationException("The direct member is unreadable or has an unexpected owner.");
                if (!seen.Add(childRefno))
                    throw new InvalidOperationException("The hierarchy contains a repeated element reference.");
            }
            catch (Exception ex)
            {
                onError?.Invoke(ex);
                ReadErrors.Add(new ReadError { ParentReference = refno, SiblingIndex = ordinal, Message = ex.Message });
                return false;
            }

            // One lookahead distinguishes an exact fit from an omitted node.
            // Stop the whole traversal to keep the returned rows a preorder prefix.
            if (depth == maxDepth || Rows.Count == limit)
            {
                if (depth == maxDepth) TruncationReasons.Add("depth");
                if (Rows.Count == limit) TruncationReasons.Add("limit");
                return false;
            }
            if (!Visit(child, childRefno, rowIndex, depth + 1, ordinal)) return false;
        }
    }
}
