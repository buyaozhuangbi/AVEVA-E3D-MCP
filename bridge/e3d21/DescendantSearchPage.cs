using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

namespace E3d21AiBridge;

// A live preorder walk. Continuation contains only the next candidate's path,
// never SDK objects or a materialized subtree. The root is outside the results.
internal sealed class DescendantSearchPage<T> where T : class
{
    public readonly List<object> Items = new List<object>();
    public readonly List<string> ReadErrors = new List<string>();
    public List<T> NextPath;
    public string[] NextReferences;
    public int VisitedCount;
    public bool DepthTruncated;
    public bool? HasMore;
    public string StopReason;

    public static DescendantSearchPage<T> Read(IList<T> startPath, bool continuation,
        int maxDepth, int limit, int scanLimit, bool depthTruncated,
        Func<T, T> firstChild, Func<T, T> nextSibling, Func<T, T> owner,
        Func<T, string> reference, Func<T, int, object> match)
    {
        if (startPath == null || startPath.Count < (continuation ? 2 : 1)
            || startPath.Count > maxDepth + 1 || (!continuation && startPath.Count != 1))
            throw new ArgumentException("Invalid descendant traversal path.");
        if (maxDepth < 1 || maxDepth > 64 || limit < 1 || limit > 200 || scanLimit < 1 || scanLimit > 1000)
            throw new ArgumentOutOfRangeException("Invalid descendant search bounds.");
        var page = new DescendantSearchPage<T> { DepthTruncated = depthTruncated };
        var path = new List<T>(startPath);
        var pathReferences = new List<string>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        try
        {
            // The native layer validates a resumed path against its signed refs.
            // Keep generic ancestry checks here as an independent scope guard.
            for (int index = 0; index < path.Count; index++)
            {
                string key = RequireReference(path[index], reference);
                pathReferences.Add(key);
                if (!seen.Add(key)) throw new InvalidOperationException("Repeated element in descendant ancestor path.");
                if (index > 0) RequireChild(path[index], pathReferences[index - 1], owner, reference);
            }
            // Ancestors may be re-encountered only as navigation frames, never candidates.
            if (continuation) seen.Remove(pathReferences[pathReferences.Count - 1]);
            bool candidate = continuation || page.Advance(path, pathReferences, maxDepth, firstChild, nextSibling, owner, reference);
            while (candidate)
            {
                T element = path[path.Count - 1];
                string key = pathReferences[pathReferences.Count - 1];
                if (!seen.Add(key)) throw new InvalidOperationException("Repeated descendant reference during this page.");
                RequireChild(element, pathReferences[pathReferences.Count - 2], owner, reference);
                page.VisitedCount++;
                object item = match(element, path.Count - 1);
                if (item != null) page.Items.Add(item);
                candidate = page.Advance(path, pathReferences, maxDepth, firstChild, nextSibling, owner, reference);
                if (!candidate) break;
                if (seen.Contains(pathReferences[pathReferences.Count - 1]))
                    throw new InvalidOperationException("Repeated descendant reference during page lookahead.");
                if (page.Items.Count >= limit || page.VisitedCount >= scanLimit)
                {
                    page.HasMore = true;
                    page.NextPath = path;
                    page.NextReferences = pathReferences.ToArray();
                    page.StopReason = page.Items.Count >= limit ? "result_limit" : "scan_limit";
                    return page;
                }
            }
            page.HasMore = false;
            page.StopReason = "exhausted";
        }
        catch (Exception ex)
        {
            page.ReadErrors.Add(ex.Message);
            page.HasMore = null;
            page.NextPath = null;
            page.NextReferences = null;
            page.StopReason = "read_error";
        }
        return page;
    }

    private bool Advance(List<T> path, List<string> pathReferences, int maxDepth, Func<T, T> firstChild,
        Func<T, T> nextSibling, Func<T, T> owner, Func<T, string> reference)
    {
        T current = path[path.Count - 1];
        T child = firstChild(current);
        if (child != null)
        {
            string childReference = RequireReference(child, reference);
            RequireChild(child, pathReferences[pathReferences.Count - 1], owner, reference);
            RequireOutsidePath(childReference, pathReferences);
            if (path.Count - 1 < maxDepth)
            {
                path.Add(child);
                pathReferences.Add(childReference);
                return true;
            }
            DepthTruncated = true;
        }
        while (path.Count > 1)
        {
            T sibling = nextSibling(path[path.Count - 1]);
            if (sibling != null)
            {
                string siblingReference = RequireReference(sibling, reference);
                RequireChild(sibling, pathReferences[pathReferences.Count - 2], owner, reference);
                RequireOutsidePath(siblingReference, pathReferences);
                path[path.Count - 1] = sibling;
                pathReferences[pathReferences.Count - 1] = siblingReference;
                return true;
            }
            path.RemoveAt(path.Count - 1);
            pathReferences.RemoveAt(pathReferences.Count - 1);
        }
        return false;
    }

    private static void RequireOutsidePath(string key, IList<string> pathReferences)
    {
        if (pathReferences.Contains(key))
            throw new InvalidOperationException("Descendant navigation repeated an ancestor or the same element.");
    }

    private static void RequireChild(T child, string parentReference, Func<T, T> owner, Func<T, string> reference)
    {
        if (RequireReference(owner(child), reference) != parentReference)
            throw new InvalidOperationException("Descendant navigation left the expected owner scope.");
    }

    private static string RequireReference(T element, Func<T, string> reference)
    {
        string value = element == null ? null : reference(element);
        if (string.IsNullOrEmpty(value)) throw new InvalidOperationException("Unreadable descendant reference.");
        return value;
    }
}

internal sealed class DescendantSearchCursor
{
    public const int MaximumLength = 16384;
    public string Session, Project, Mdb, Root, ElementType, NameContains;
    public int MaxDepth;
    public bool DepthTruncated;
    public string[] Path;

    public static byte[] CreateKey()
    {
        byte[] key = new byte[32];
        using (RandomNumberGenerator random = RandomNumberGenerator.Create()) random.GetBytes(key);
        return key;
    }

    public string Encode(byte[] key)
    {
        Validate();
        byte[] payload;
        using (var stream = new MemoryStream())
        {
            using (var writer = new BinaryWriter(stream, new UTF8Encoding(false, true), true))
            {
                writer.Write(1);
                foreach (string field in new[] { Session, Project, Mdb, Root, ElementType, NameContains }) writer.Write(field);
                writer.Write(MaxDepth);
                writer.Write(DepthTruncated);
                writer.Write(Path.Length);
                foreach (string part in Path) writer.Write(part);
            }
            payload = stream.ToArray();
        }
        byte[] signature;
        using (var hmac = new HMACSHA256(key)) signature = hmac.ComputeHash(payload);
        string token = Convert.ToBase64String(payload) + "." + Convert.ToBase64String(signature);
        if (token.Length > MaximumLength) throw new InvalidOperationException("Descendant cursor exceeds its size bound.");
        return token;
    }

    public static DescendantSearchCursor Decode(string value, byte[] key)
    {
        try
        {
            if (string.IsNullOrEmpty(value) || value.Length > MaximumLength) throw new FormatException();
            string[] pieces = value.Split('.');
            if (pieces.Length != 2) throw new FormatException();
            byte[] payload = Convert.FromBase64String(pieces[0]);
            byte[] signature = Convert.FromBase64String(pieces[1]);
            byte[] expected;
            using (var hmac = new HMACSHA256(key)) expected = hmac.ComputeHash(payload);
            if (signature.Length != expected.Length) throw new FormatException();
            int difference = 0;
            for (int index = 0; index < signature.Length; index++) difference |= signature[index] ^ expected[index];
            if (difference != 0) throw new FormatException();
            DescendantSearchCursor result;
            using (var stream = new MemoryStream(payload, false))
            using (var reader = new BinaryReader(stream, new UTF8Encoding(false, true)))
            {
                if (reader.ReadInt32() != 1) throw new FormatException();
                result = new DescendantSearchCursor
                {
                    Session = reader.ReadString(), Project = reader.ReadString(), Mdb = reader.ReadString(),
                    Root = reader.ReadString(), ElementType = reader.ReadString(), NameContains = reader.ReadString(),
                    MaxDepth = reader.ReadInt32(), DepthTruncated = reader.ReadBoolean()
                };
                int count = reader.ReadInt32();
                if (count < 2 || count > 65) throw new FormatException();
                result.Path = new string[count];
                for (int index = 0; index < count; index++) result.Path[index] = reader.ReadString();
                if (stream.Position != stream.Length) throw new FormatException();
            }
            result.Validate();
            return result;
        }
        catch (Exception ex) when (ex is FormatException || ex is ArgumentException || ex is IOException)
        {
            throw new ArgumentException("Invalid or expired descendant cursor. Start a new search.");
        }
    }

    private void Validate()
    {
        if (string.IsNullOrWhiteSpace(Session) || Session.Length > 256
            || string.IsNullOrWhiteSpace(Project) || Project.Length > 256
            || string.IsNullOrWhiteSpace(Mdb) || Mdb.Length > 256
            || ElementType == null || ElementType.Length > 64 || NameContains == null || NameContains.Length > 256
            || MaxDepth < 1 || MaxDepth > 64 || Path == null || Path.Length < 2 || Path.Length > MaxDepth + 1
            || !IsReference(Root) || Path[0] != Root || Path.Any(part => !IsReference(part))
            || Path.Distinct(StringComparer.Ordinal).Count() != Path.Length)
            throw new ArgumentException("Invalid descendant cursor fields.");
    }

    public static bool IsReference(string text)
    {
        if (string.IsNullOrEmpty(text) || text.Length > 32 || text[0] != '=') return false;
        string[] pair = text.Substring(1).Split('/');
        int database, element;
        return pair.Length == 2
            && int.TryParse(pair[0], System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture, out database)
            && int.TryParse(pair[1], System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture, out element)
            && database > 0 && element >= 0;
    }
}
