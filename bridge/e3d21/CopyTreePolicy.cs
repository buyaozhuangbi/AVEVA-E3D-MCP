using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace E3d21AiBridge;

// SDK-free identity checks used before any copied-node setter or recursive delete.
internal sealed class CopyTreeIdentity
{
    internal string Reference, Owner, Type, Path, Name;
}

// An immutable observation of the accepted result of one returned native copy.
// It does not prove the creation provenance of nodes merely absent from a baseline.
internal sealed class CopyTreeCapture
{
    private readonly CopyTreeIdentity[] nodes;

    internal CopyTreeCapture(IList<CopyTreeIdentity> accepted)
    {
        nodes = accepted.Select(node => new CopyTreeIdentity
        {
            Reference = node.Reference, Owner = node.Owner, Type = node.Type, Path = node.Path
        }).ToArray();
    }

    internal string[] References { get { return nodes.Select(node => node.Reference).ToArray(); } }

    internal void RequireUnchanged(IList<CopyTreeIdentity> current)
    {
        if (current == null || current.Count != nodes.Length)
            throw new InvalidOperationException("The current copy subtree does not match the captured native copy result; no recursive cleanup is permitted.");
        for (int index = 0; index < nodes.Length; index++)
        {
            CopyTreeIdentity before = nodes[index], after = current[index];
            if (after == null || before.Reference != after.Reference || before.Owner != after.Owner ||
                before.Type != after.Type || before.Path != after.Path)
                throw new InvalidOperationException("A captured copy node was added, removed, replaced, reordered or reparented; no recursive cleanup is permitted.");
        }
    }
}

internal static class CopyTreePolicy
{
    private const int MaximumNameLength = 50;

    internal static string RequestedName(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || !value.StartsWith("/", StringComparison.Ordinal) || value.Length > MaximumNameLength ||
            value.Any(c => char.IsWhiteSpace(c) || char.IsControl(c)))
            throw new ArgumentException("name must be an explicit /database-name of at most 50 characters including the leading slash, without whitespace.", "name");
        string name = "/" + value.TrimStart('/');
        if (name.Length < 2 || name[1] == '=') throw new ArgumentException("name must be a database name.", "name");
        return name;
    }

    internal static string[] Names(IList<CopyTreeIdentity> source, string rootName)
    {
        if (source == null || source.Count == 0) throw new ArgumentException("The source hierarchy is empty.");
        string[] names = source.Select((node, index) => index == 0 ? rootName :
            string.IsNullOrEmpty(node.Name) ? null : rootName + "-C" + index.ToString("D4", CultureInfo.InvariantCulture)).ToArray();
        for (int index = 0; index < names.Length; index++)
            if (names[index] != null && names[index].Length > MaximumNameLength)
                throw new ArgumentException("Generated copy name at depth-first index " + index.ToString(CultureInfo.InvariantCulture) +
                    " contains " + names[index].Length.ToString(CultureInfo.InvariantCulture) +
                    " characters; E3D21 permits at most 50 including the leading slash. Named descendants require six suffix characters (-C0001); shorten the root name. Names are never truncated.", "name");
        return names;
    }

    internal static string SourceName(string value, string reference)
    {
        if (value == null) throw new InvalidOperationException("An element NAME could not be read reliably.");
        if (value == reference) return "";
        string name = value.TrimStart('/');
        if (name.StartsWith("=", StringComparison.Ordinal))
            throw new InvalidOperationException("An element NAME reports a foreign numeric reference instead of its own unnamed identity.");
        return name;
    }

    internal static bool IsSdkWorldEndpoint(string reference, string sdkWorldReference)
    {
        if (reference == null || reference != sdkWorldReference || !reference.StartsWith("=", StringComparison.Ordinal)) return false;
        string[] pair = reference.Substring(1).Split('/');
        int database, element;
        return pair.Length == 2 && int.TryParse(pair[0], NumberStyles.None, CultureInfo.InvariantCulture, out database) && database > 0 &&
            int.TryParse(pair[1], NumberStyles.None, CultureInfo.InvariantCulture, out element) && element == 0;
    }

    internal static void RequireNewRoot(CopyTreeIdentity root, string parent, string type, ISet<string> protectedReferences)
    {
        if (root == null || string.IsNullOrEmpty(root.Reference) || protectedReferences.Contains(root.Reference) ||
            root.Owner != parent || root.Type != type || root.Reference == parent)
            throw new InvalidOperationException("The returned copy root is not a verifiable new element of the requested type under the explicit parent.");
    }

    internal static void RequireNewTree(IList<CopyTreeIdentity> nodes, string rootReference, string parent,
        ISet<string> protectedReferences)
    {
        if (nodes == null || nodes.Count == 0 || nodes[0].Reference != rootReference || nodes[0].Owner != parent)
            throw new InvalidOperationException("The copied hierarchy root or owner changed.");
        var references = new HashSet<string>(StringComparer.Ordinal);
        var paths = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (CopyTreeIdentity node in nodes)
        {
            if (string.IsNullOrEmpty(node.Reference) || !references.Add(node.Reference) ||
                protectedReferences.Contains(node.Reference) || node.Reference == parent || paths.ContainsKey(node.Path))
                throw new InvalidOperationException("The copied hierarchy contains a repeated or protected existing reference.");
            if (node.Path.Length == 0)
            {
                if (node.Reference != rootReference) throw new InvalidOperationException("The copied hierarchy has multiple roots.");
            }
            else
            {
                int separator = node.Path.LastIndexOf('/');
                string ownerPath = separator < 0 ? "" : node.Path.Substring(0, separator);
                string expectedOwner;
                if (!paths.TryGetValue(ownerPath, out expectedOwner) || node.Owner != expectedOwner)
                    throw new InvalidOperationException("A copied node does not belong to its verified new parent.");
            }
            paths.Add(node.Path, node.Reference);
        }
    }

    internal static void RequireMatchingTree(IList<CopyTreeIdentity> source, IList<CopyTreeIdentity> copied,
        string parent, ISet<string> protectedReferences)
    {
        if (copied == null || copied.Count == 0) throw new InvalidOperationException("The copied hierarchy is empty.");
        RequireNewTree(copied, copied[0].Reference, parent, protectedReferences);
        if (source.Count != copied.Count) throw new InvalidOperationException("Copied hierarchy node count differs from the source.");
        for (int index = 0; index < source.Count; index++)
            if (source[index].Path != copied[index].Path || source[index].Type != copied[index].Type)
                throw new InvalidOperationException("Copied hierarchy type or ordered child structure differs from the source.");
    }

    internal static void RequireAppend(string[] before, string[] after, string added)
    {
        if (!after.SequenceEqual(before.Concat(new[] { added }), StringComparer.Ordinal))
            throw new InvalidOperationException("Destination membership did not preserve its existing sequence and append only the copy root.");
    }

    internal static CopyTreeCapture CaptureReturnedTree(IList<CopyTreeIdentity> source,
        IList<CopyTreeIdentity> copied, string rootReference, string parent, ISet<string> protectedReferences,
        string[] originalMembers, string[] currentMembers)
    {
        if (source == null || source.Count == 0 || copied == null || copied.Count == 0 || copied[0].Reference != rootReference)
            throw new InvalidOperationException("The complete returned copy root and source topology are required before capture.");
        RequireNewRoot(copied[0], parent, source[0].Type, protectedReferences);
        RequireMatchingTree(source, copied, parent, protectedReferences);
        RequireAppend(originalMembers, currentMembers, rootReference);
        return new CopyTreeCapture(copied);
    }

    internal static void RequireEmptyRoot(IList<CopyTreeIdentity> current, string rootReference,
        string parent, string rootType, ISet<string> protectedReferences)
    {
        if (current == null || current.Count != 1 || current[0].Reference != rootReference || current[0].Path != "")
            throw new InvalidOperationException("No native copy result was captured; only the exact still-empty created root may be cleaned up.");
        RequireNewRoot(current[0], parent, rootType, protectedReferences);
    }
}
