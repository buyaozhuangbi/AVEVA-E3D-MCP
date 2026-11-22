using System;
using System.Collections.Generic;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;

namespace E3d21AiBridge;

// Consumes at most limit + 1 direct siblings, without materializing Members().
internal sealed class MemberPage<T>
{
    public readonly List<T> Items = new List<T>();
    public readonly List<string> ReadErrors = new List<string>();
    public bool? HasMore;
    public string LastRef;
    public string LastType;
    public string LastIdentity;
    public int LastOrdinal;

    public static MemberPage<T> Read(IEnumerable<T> members, int limit, Func<T, string> reference)
    {
        return Read(members, limit, reference, reference, reference, 0);
    }

    public static MemberPage<T> Read(IEnumerable<T> members, int limit, Func<T, string> identity,
        Func<T, string> reference, Func<T, string> type, int startOrdinal)
    {
        if (limit < 1) throw new ArgumentOutOfRangeException("limit");
        if (startOrdinal < 0 || startOrdinal > Int32.MaxValue - limit - 1)
            throw new ArgumentOutOfRangeException("startOrdinal");
        MemberPage<T> page = new MemberPage<T>();
        HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
        int ordinal = startOrdinal;
        try
        {
            foreach (T member in members)
            {
                string key = reference(member);
                string uniqueIdentity = identity(member);
                string memberType = type(member);
                if (string.IsNullOrEmpty(key) || string.IsNullOrEmpty(memberType) ||
                    string.IsNullOrEmpty(uniqueIdentity) || !seen.Add(uniqueIdentity))
                    throw new InvalidOperationException("Unreadable or repeated sibling reference.");
                if (page.Items.Count == limit)
                {
                    page.HasMore = true;
                    return page;
                }
                page.Items.Add(member);
                page.LastRef = key;
                page.LastType = memberType;
                page.LastIdentity = Convert.ToBase64String(Encoding.UTF8.GetBytes(uniqueIdentity));
                page.LastOrdinal = ++ordinal;
            }
            page.HasMore = false;
        }
        catch (Exception ex)
        {
            // A failed lookahead cannot certify the end of the member list.
            page.HasMore = null;
            page.ReadErrors.Add(ex.Message);
        }
        return page;
    }
}

internal sealed class MemberPageCursor
{
    private static readonly byte[] SigningKey = CreateSigningKey();

    public string Session;
    public string Project;
    public string Mdb;
    public string Parent;
    public string ParentType;
    public string After;
    public string AfterType;
    public string AfterIdentity;
    public int AfterOrdinal;

    public string Encode()
    {
        byte[] payload = Encoding.UTF8.GetBytes(string.Join("\n", "3", Session, Project, Mdb,
            Parent, ParentType, After, AfterType, AfterIdentity,
            AfterOrdinal.ToString(CultureInfo.InvariantCulture)));
        byte[] signature;
        using (HMACSHA256 hmac = new HMACSHA256(SigningKey))
            signature = hmac.ComputeHash(payload);
        return Convert.ToBase64String(payload) + "." + Convert.ToBase64String(signature);
    }

    public static MemberPageCursor Decode(string value)
    {
        if (string.IsNullOrEmpty(value) || value.Length > 2048)
            throw new ArgumentException("Invalid member cursor. Start a new member listing.");
        string[] parts;
        try
        {
            string[] encoded = value.Split('.');
            if (encoded.Length != 2)
                throw new FormatException();
            byte[] payload = Convert.FromBase64String(encoded[0]);
            byte[] signature = Convert.FromBase64String(encoded[1]);
            if (signature.Length != 32 || !HasValidSignature(payload, signature))
                throw new ArgumentException("Invalid member cursor signature. Start a new member listing.");
            parts = new UTF8Encoding(false, true).GetString(payload).Split('\n');
        }
        catch (Exception ex) when (ex is FormatException || ex is DecoderFallbackException || ex is CryptographicException)
        {
            throw new ArgumentException("Invalid member cursor. Start a new member listing.");
        }
        int afterOrdinal;
        bool validIdentity;
        try
        {
            validIdentity = !string.IsNullOrEmpty(parts.Length > 8 ? parts[8] : null) &&
                new UTF8Encoding(false, true).GetString(Convert.FromBase64String(parts[8])).Length > 0;
        }
        catch (Exception ex) when (ex is FormatException || ex is DecoderFallbackException)
        {
            validIdentity = false;
        }
        if (parts.Length != 10 || parts[0] != "3" || Array.Exists(parts, string.IsNullOrWhiteSpace)
            || !parts[4].StartsWith("=", StringComparison.Ordinal) || !parts[6].StartsWith("=", StringComparison.Ordinal)
            || !Int32.TryParse(parts[9], NumberStyles.None, CultureInfo.InvariantCulture, out afterOrdinal) || afterOrdinal < 1
            || !validIdentity)
            throw new ArgumentException("Invalid member cursor. Start a new member listing.");
        return new MemberPageCursor
        {
            Session = parts[1], Project = parts[2], Mdb = parts[3], Parent = parts[4], ParentType = parts[5],
            After = parts[6], AfterType = parts[7], AfterIdentity = parts[8], AfterOrdinal = afterOrdinal
        };
    }

    private static byte[] CreateSigningKey()
    {
        byte[] key = new byte[32];
        using (RNGCryptoServiceProvider random = new RNGCryptoServiceProvider())
            random.GetBytes(key);
        return key;
    }

    private static bool HasValidSignature(byte[] payload, byte[] signature)
    {
        byte[] expected;
        using (HMACSHA256 hmac = new HMACSHA256(SigningKey))
            expected = hmac.ComputeHash(payload);
        int difference = expected.Length ^ signature.Length;
        for (int index = 0; index < Math.Min(expected.Length, signature.Length); index++)
            difference |= expected[index] ^ signature[index];
        return difference == 0;
    }
}
