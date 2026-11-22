using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private readonly byte[] descendantCursorKey = DescendantSearchCursor.CreateKey();

    public object FindDescendants(string rootRefno, string elementType, string nameContains,
        int? maxDepth, int limit, string cursor, string sessionId)
    {
        const int scanLimit = 1000;
        if (limit < 1 || limit > 200) throw new ArgumentOutOfRangeException("limit");
        if (maxDepth.HasValue && (maxDepth.Value < 1 || maxDepth.Value > 64)) throw new ArgumentOutOfRangeException("max_depth");
        if (nameContains != null && (nameContains.Length > 256 || nameContains.Any(char.IsControl)))
            throw new ArgumentException("name_contains must be literal text of at most 256 characters without control characters.");
        DescendantSearchCursor continuation = string.IsNullOrEmpty(cursor) ? null : DescendantSearchCursor.Decode(cursor, descendantCursorKey);
        if (continuation != null && continuation.Session != sessionId)
            throw new InvalidOperationException("Descendant cursor belongs to another E3D session. Start a new search.");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
            MDB mdb = CurrentMdb();
            Project project = Project.CurrentProject;
            if (mdb == null || project == null) throw new InvalidOperationException("No project/MDB is open in E3D.");
            if (continuation != null && (continuation.Project != project.Name || continuation.Mdb != mdb.Name))
                throw new InvalidOperationException("Descendant cursor belongs to another project or MDB. Start a new search.");
            string typeFilter = elementType == null && continuation != null ? continuation.ElementType : DescendantTypeFilter(elementType);
            string nameFilter = nameContains ?? (continuation == null ? "" : continuation.NameContains);
            int depth = maxDepth ?? (continuation == null ? 64 : continuation.MaxDepth);
            if (continuation != null && (typeFilter != continuation.ElementType || nameFilter != continuation.NameContains || depth != continuation.MaxDepth))
                throw new ArgumentException("Descendant cursor filters or max_depth do not match. Omit them to retain the cursor search or start a new search.");
            string requestedRoot = string.IsNullOrWhiteSpace(rootRefno) && continuation != null ? continuation.Root : rootRefno;
            requestedRoot = ModelEditInput.ExplicitReference(requestedRoot, "root_refno");
            DbElement root = DescendantResolve(requestedRoot, mdb);
            string rootRef = DescendantReference(root);
            if (root.DbType != DbType.Design) throw new ArgumentException("root_refno must identify an element in a Design database.");
            if (continuation != null && continuation.Root != rootRef)
                throw new ArgumentException("Descendant cursor does not match the requested root.");
            Dictionary<string, object> rootIdentity = DescendantIdentity(root);
            var path = new List<DbElement> { root };
            if (continuation != null)
            {
                for (int index = 1; index < continuation.Path.Length; index++)
                {
                    ThrowIfNativeFaulted();
                    DbElement node = DescendantResolve(continuation.Path[index], mdb);
                    if (DescendantReference(NativeRead(() => node.Owner)) != continuation.Path[index - 1])
                        throw new InvalidOperationException("Descendant cursor path was moved or reparented. Start a new search.");
                    path.Add(node);
                }
            }

            DescendantSearchPage<DbElement> page = DescendantSearchPage<DbElement>.Read(path,
                continuation != null, depth, limit, scanLimit, continuation != null && continuation.DepthTruncated,
                node => DescendantNavigate(NativeRead(() => node.FirstMember())), node => DescendantNavigate(NativeRead(() => node.Next())),
                node => NativeRead(() => node.Owner), DescendantReference,
                delegate(DbElement node, int nodeDepth)
                {
                    string type = DescendantBaseType(node);
                    if (typeFilter.Length > 0 && type != typeFilter) return null;
                    string name = NativeRead(() => node.GetAsString(DbAttributeInstance.NAME));
                    if (name == null) throw new InvalidOperationException("Descendant NAME read returned null.");
                    if (nameFilter.Length > 0 && name.IndexOf(nameFilter, StringComparison.OrdinalIgnoreCase) < 0) return null;
                    return new Dictionary<string, object>
                    {
                        ["refno"] = DescendantReference(node), ["type"] = type, ["name"] = name,
                        ["owner_refno"] = DescendantReference(NativeRead(() => node.Owner)), ["depth"] = nodeDepth
                    };
                });
            ThrowIfNativeFaulted();
            string nextCursor = null;
            if (page.HasMore == true)
                nextCursor = new DescendantSearchCursor
                {
                    Session = sessionId, Project = project.Name, Mdb = mdb.Name, Root = rootRef,
                    ElementType = typeFilter, NameContains = nameFilter, MaxDepth = depth,
                    DepthTruncated = page.DepthTruncated, Path = page.NextReferences
                }.Encode(descendantCursorKey);
            bool scopeComplete = page.HasMore == false && page.ReadErrors.Count == 0;
            return new Dictionary<string, object>
            {
                ["success"] = page.ReadErrors.Count == 0, ["root"] = rootIdentity,
                ["elements"] = page.Items, ["returned_count"] = page.Items.Count,
                ["visited_count"] = page.VisitedCount, ["limit"] = limit, ["scan_limit"] = scanLimit,
                ["filters"] = new Dictionary<string, object> { ["element_type"] = typeFilter, ["name_contains"] = nameFilter },
                ["max_depth"] = depth, ["includes_root"] = false, ["order"] = "database preorder",
                ["scope_complete"] = scopeComplete, ["complete"] = scopeComplete && !page.DepthTruncated,
                ["depth_truncated"] = page.DepthTruncated, ["has_more"] = page.HasMore,
                ["next_cursor"] = nextCursor, ["stop_reason"] = page.StopReason,
                ["consistency"] = "live", ["read_errors"] = page.ReadErrors
            };
        });
    }

    private static string DescendantTypeFilter(string value)
    {
        ThrowIfNativeFaulted();
        string text = (value ?? "").Trim();
        if (text.Length == 0) return "";
        if (text.Length > 64 || text.Any(c => !(char.IsLetterOrDigit(c) || c == '_')))
            throw new ArgumentException("element_type must be a single SDK base type name, for example NOZZ or NOZZLE.");
        DbElementType type = DbElementType.GetElementType(text.ToUpperInvariant());
        if (type == null || !type.IsValid || string.IsNullOrEmpty(type.ShortName))
            throw new ArgumentException("Unknown SDK element_type: " + text);
        return type.ShortName.ToUpperInvariant();
    }

    private static DbElement DescendantResolve(string reference, MDB mdb)
    {
        ThrowIfNativeFaulted();
        DbElement node;
        if (reference.StartsWith("=", StringComparison.Ordinal))
        {
            if (!DescendantSearchCursor.IsReference(reference)) throw new ArgumentException("Invalid descendant database reference.");
            string[] pair = reference.Substring(1).Split('/');
            node = NativeRead(() => DbElement.GetElement(new[] { int.Parse(pair[0], CultureInfo.InvariantCulture), int.Parse(pair[1], CultureInfo.InvariantCulture) }));
        }
        else node = NativeRead(() => mdb.FindElement(reference));
        if (node == null || NativeRead(() => node.IsNull) || !NativeRead(() => node.IsValid) || NativeRead(() => node.IsDeleted))
            throw new InvalidOperationException("Descendant search element was not found or was deleted: " + reference + ". Start a new search if this is a cursor path.");
        return node;
    }

    private static DbElement DescendantNavigate(DbElement node)
    {
        ThrowIfNativeFaulted();
        if (node == null || NativeRead(() => node.IsNull)) return null;
        DescendantReference(node);
        return node;
    }

    private static string DescendantReference(DbElement node)
    {
        ThrowIfNativeFaulted();
        if (node == null || NativeRead(() => node.IsNull) || !NativeRead(() => node.IsValid) || NativeRead(() => node.IsDeleted))
            throw new InvalidOperationException("Descendant traversal contains an unreadable element.");
        string reference = NativeRead(() => node.GetAsString(DbAttributeInstance.REF));
        if (!DescendantSearchCursor.IsReference(reference)) throw new InvalidOperationException("Descendant reference is unavailable or invalid.");
        return reference;
    }

    private static string DescendantBaseType(DbElement node)
    {
        ThrowIfNativeFaulted();
        // The SDK documents ElementType as returning the base type for UDETs.
        DbElementType type = NativeRead(() => node.ElementType);
        if (type == null || !type.IsValid || string.IsNullOrEmpty(type.ShortName))
            throw new InvalidOperationException("Descendant base type could not be read.");
        return type.ShortName.ToUpperInvariant();
    }

    private static Dictionary<string, object> DescendantIdentity(DbElement node)
    {
        ThrowIfNativeFaulted();
        string name = NativeRead(() => node.GetAsString(DbAttributeInstance.NAME));
        if (name == null) throw new InvalidOperationException("Descendant NAME read returned null.");
        return new Dictionary<string, object> { ["refno"] = DescendantReference(node), ["type"] = DescendantBaseType(node), ["name"] = name };
    }
}
