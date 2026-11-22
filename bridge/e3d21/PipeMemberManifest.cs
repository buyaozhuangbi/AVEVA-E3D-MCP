using System;
using System.Collections.Generic;
using System.Globalization;

namespace E3d21AiBridge;

public interface IE3d21PipeMemberManifestContext
{
    object ReadPipeMemberManifest(string refno, int maxDepth, int limit, IList<string> attributes);
}

internal sealed class PipeMemberManifestRequest
{
    internal const int DefaultDepth = 8;
    internal const int MaximumDepth = 16;
    internal const int DefaultLimit = 64;
    internal const int MaximumLimit = 100;
    internal const int MaximumAttributes = 10;
    internal const int MaximumCells = 500;
    internal const int MaximumTextLength = 4096;
    private static readonly string[] DefaultAttributes = { "SPREF", "POS", "ORI", "ABOR", "LBOR" };

    internal string Refno;
    internal int MaxDepth;
    internal int Limit;
    internal List<string> Attributes;

    internal static PipeMemberManifestRequest Validate(string refno, int maxDepth, int limit, IList<string> attributes)
    {
        string target = ModelEditInput.ExplicitReference(refno, "refno");
        if (target.Length > 256 || target.IndexOfAny(new[] { '*', '?' }) >= 0)
            throw new ArgumentException("refno must be a literal PIPE or BRAN reference of at most 256 characters.");
        if (target[0] == '=')
        {
            string[] parts = target.Substring(1).Split('/');
            target = "=" + int.Parse(parts[0], CultureInfo.InvariantCulture).ToString(CultureInfo.InvariantCulture)
                + "/" + int.Parse(parts[1], CultureInfo.InvariantCulture).ToString(CultureInfo.InvariantCulture);
        }
        if (maxDepth < 1 || maxDepth > MaximumDepth)
            throw new ArgumentOutOfRangeException("max_depth");
        if (limit < 1 || limit > MaximumLimit)
            throw new ArgumentOutOfRangeException("limit");
        IList<string> fields = attributes ?? DefaultAttributes;
        if (fields.Count > MaximumAttributes || limit * fields.Count > MaximumCells)
            throw new ArgumentException("At most 10 attributes and 500 requested row/attribute cells are allowed.");
        var names = new List<string>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (string attribute in fields)
        {
            string name = (attribute ?? "").Trim();
            if (name.Length == 0 || name.Length > 64)
                throw new ArgumentException("Each attribute must be an explicit SDK name of 1 to 64 characters.");
            foreach (char c in name)
                if (!(c >= 'A' && c <= 'Z') && !(c >= 'a' && c <= 'z') && !(c >= '0' && c <= '9') && c != ':' && c != '_')
                    throw new ArgumentException("Only ASCII letters, digits, underscores and colons are allowed in attributes.");
            name = name.ToUpperInvariant();
            if (!seen.Add(name))
                throw new ArgumentException("attributes must be unique after normalization.");
            names.Add(name);
        }
        return new PipeMemberManifestRequest { Refno = target, MaxDepth = maxDepth, Limit = limit, Attributes = names };
    }

    internal static string ErrorText(string message)
    {
        if (string.IsNullOrWhiteSpace(message)) return "The SDK read failed without a message.";
        if (message.Length <= MaximumTextLength) return message;
        int length = MaximumTextLength - 3;
        if (char.IsHighSurrogate(message[length - 1])) length--;
        return message.Substring(0, length) + "...";
    }
}

internal sealed class PipeMemberTraversal<T> where T : class
{
    internal sealed class Row
    {
        internal T Member;
        internal string Refno, Type;
        internal int Id, ParentIndex, Depth, SiblingIndex;
        internal string IdentityKey => Refno + "|" + Type;
    }

    internal readonly List<Row> Rows = new List<Row>();
    internal readonly List<string> TruncationReasons = new List<string>();
    internal readonly List<Dictionary<string, object>> ReadErrors = new List<Dictionary<string, object>>();
    internal bool Complete => TruncationReasons.Count == 0 && ReadErrors.Count == 0;

    private readonly HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
    private readonly PipeMemberManifestRequest request;
    private readonly Func<T, int, T> memberAt;
    private readonly Func<T, (string Refno, string Type)> identity;
    private readonly Func<T, T> owner;
    private readonly Action checkFault;

    internal PipeMemberTraversal(T root, (string Refno, string Type) rootIdentity, PipeMemberManifestRequest request,
        Func<T, int, T> memberAt, Func<T, (string Refno, string Type)> identity, Func<T, T> owner, Action checkFault)
    {
        this.request = request;
        this.memberAt = memberAt;
        this.identity = identity;
        this.owner = owner;
        this.checkFault = checkFault;
        seen.Add(rootIdentity.Refno + "|" + rootIdentity.Type);
        Visit(root, rootIdentity, 0, 0);
    }

    private bool Visit(T parent, (string Refno, string Type) parentIdentity, int parentIndex, int depth)
    {
        for (int ordinal = 1; ; ordinal++)
        {
            Row row;
            try
            {
                T child = memberAt(parent, ordinal);
                if (child == null) return true;
                var childIdentity = identity(child);
                if (!identity(owner(child)).Equals(parentIdentity))
                    throw new InvalidOperationException("The member has an unexpected typed owner.");
                row = new Row { Member = child, Refno = childIdentity.Refno, Type = childIdentity.Type,
                    Id = Rows.Count + 1, ParentIndex = parentIndex, Depth = depth + 1, SiblingIndex = ordinal };
                if (!seen.Add(row.IdentityKey))
                    throw new InvalidOperationException("The hierarchy repeats a typed element reference.");
            }
            catch (Exception error)
            {
                checkFault();
                ReadErrors.Add(new Dictionary<string, object> { ["stage"] = "member", ["parent_index"] = parentIndex,
                    ["sibling_index"] = ordinal, ["message"] = PipeMemberManifestRequest.ErrorText(error.Message) });
                return false;
            }

            // Verify one omitted member to distinguish an exact fit from truncation.
            if (depth == request.MaxDepth) TruncationReasons.Add("depth");
            if (Rows.Count == request.Limit) TruncationReasons.Add("limit");
            if (TruncationReasons.Count != 0) return false;
            Rows.Add(row);
            if (!Visit(row.Member, (row.Refno, row.Type), row.Id, row.Depth)) return false;
        }
    }
}
