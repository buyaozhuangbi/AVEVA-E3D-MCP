using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Globalization;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

namespace E3d21AiBridge;

// Optional managed capability; older IE3d21Context implementations still compile.
public interface IE3d21MessageWindowContext
{
    MessageWindowSnapshot ReadMessageWindow(long afterSequence, int limit, bool checkpoint);
}

internal sealed class MessageCursorException : Exception
{
    internal readonly string Code;
    internal readonly int HttpStatus;
    internal MessageCursorException(string code, string message, int httpStatus = 400) : base(message)
    {
        Code = code;
        HttpStatus = httpStatus;
    }
}

internal sealed class MessageWindowRequest
{
    internal string Cursor;
    internal int Limit;
    internal bool Checkpoint;

    internal static MessageWindowRequest Parse(NameValueCollection query)
    {
        if (query == null) throw Invalid("Message window query parameters are unavailable.");
        foreach (string key in query.AllKeys)
        {
            if (key != "cursor" && key != "limit" && key != "checkpoint")
                throw Invalid("Unknown message window parameter.");
            string[] values = query.GetValues(key);
            if (values == null || values.Length != 1)
                throw Invalid("Message window parameters must occur exactly once.");
        }
        int limit = 100;
        string suppliedLimit = query["limit"];
        if (suppliedLimit != null && (!int.TryParse(suppliedLimit, NumberStyles.None,
            CultureInfo.InvariantCulture, out limit) || limit < 1 || limit > 500))
            throw Invalid("limit must be an integer between 1 and 500.");
        bool checkpoint = false;
        string suppliedCheckpoint = query["checkpoint"];
        if (suppliedCheckpoint != null)
        {
            if (suppliedCheckpoint != "true" && suppliedCheckpoint != "false")
                throw Invalid("checkpoint must be true or false.");
            checkpoint = suppliedCheckpoint == "true";
        }
        string cursor = query["cursor"] ?? "";
        if (cursor.Length > 1024)
            throw new MessageCursorException("message_cursor_invalid", "The message cursor is invalid.");
        if (checkpoint && cursor.Length != 0)
            throw Invalid("checkpoint cannot be combined with a cursor.");
        return new MessageWindowRequest { Cursor = cursor, Limit = limit, Checkpoint = checkpoint };
    }

    private static MessageCursorException Invalid(string message)
    {
        return new MessageCursorException("message_cursor_request_invalid", message);
    }
}

// A per-adapter random key authenticates every opaque cursor. Both payload and
// signature use canonical base64url; a cursor is bound to one immutable session.
// Production owns one non-resetting ring for this adapter lifetime; replacing
// the context requires a new adapter/session rather than reusing old cursors.
internal sealed class MessageCursorCodec
{
    private readonly byte[] secret = new byte[32];
    internal MessageCursorCodec()
    {
        using (RandomNumberGenerator random = RandomNumberGenerator.Create()) random.GetBytes(secret);
    }

    internal string Encode(string sessionId, long afterSequence)
    {
        if (afterSequence < 0) throw new ArgumentOutOfRangeException("afterSequence");
        byte[] payload = Encoding.ASCII.GetBytes("1\n" + sessionId + "\n" + Decimal(afterSequence));
        byte[] signature;
        using (var hmac = new HMACSHA256(secret)) signature = hmac.ComputeHash(payload);
        return Base64Url(payload) + "." + Base64Url(signature);
    }

    internal long Decode(string cursor, string sessionId)
    {
        if (string.IsNullOrEmpty(cursor)) return 0;
        if (cursor.Length > 1024) throw Invalid();
        string[] pieces = cursor.Split('.');
        if (pieces.Length != 2) throw Invalid();
        byte[] payload = DecodeBase64Url(pieces[0]);
        byte[] suppliedSignature = DecodeBase64Url(pieces[1]);
        if (payload.Length > 128 || suppliedSignature.Length != 32 || payload.Any(value => value > 127)) throw Invalid();
        // Authenticate the opaque payload before interpreting its session field.
        // Otherwise an unsigned payload could probe whether a guessed session id
        // is current or stale by receiving different error classes.
        byte[] expectedSignature;
        using (var hmac = new HMACSHA256(secret)) expectedSignature = hmac.ComputeHash(payload);
        int difference = 0;
        for (int index = 0; index < expectedSignature.Length; index++)
            difference |= expectedSignature[index] ^ suppliedSignature[index];
        if (difference != 0) throw Invalid();
        string[] fields = Encoding.ASCII.GetString(payload).Split('\n');
        long position;
        Guid cursorSession;
        if (fields.Length != 3 || fields[0] != "1" || fields[1].Length != 36 ||
            !Guid.TryParseExact(fields[1], "D", out cursorSession) || cursorSession.ToString("D") != fields[1] ||
            !long.TryParse(fields[2], NumberStyles.None, CultureInfo.InvariantCulture, out position) ||
            position < 0 || Decimal(position) != fields[2]) throw Invalid();
        if (!string.Equals(fields[1], sessionId, StringComparison.Ordinal))
            throw new MessageCursorException("message_cursor_session_changed",
                "The message cursor belongs to another adapter session.", 409);
        return position;
    }

    internal static string Decimal(long value) { return value.ToString(CultureInfo.InvariantCulture); }
    private static string Base64Url(byte[] value)
    {
        return Convert.ToBase64String(value).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    }
    private static byte[] DecodeBase64Url(string value)
    {
        if (value.Length == 0 || value.Any(character => !(character >= 'A' && character <= 'Z') &&
            !(character >= 'a' && character <= 'z') && !(character >= '0' && character <= '9') &&
            character != '-' && character != '_')) throw Invalid();
        string padded = value.Replace('-', '+').Replace('_', '/');
        if (padded.Length % 4 == 1) throw Invalid();
        padded = padded.PadRight(padded.Length + (4 - padded.Length % 4) % 4, '=');
        byte[] decoded;
        try { decoded = Convert.FromBase64String(padded); }
        catch (FormatException) { throw Invalid(); }
        if (Base64Url(decoded) != value) throw Invalid();
        return decoded;
    }
    private static MessageCursorException Invalid()
    {
        return new MessageCursorException("message_cursor_invalid", "The message cursor is invalid.");
    }
}

public sealed class MessageWindowSnapshot
{
    internal long AfterSequence, LatestSequence, NextSequence, OverwrittenCount;
    internal long? FirstRetainedSequence;
    internal int RemainingCount, RequestedLimit;
    internal bool Checkpoint;
    internal readonly List<object> Messages = new List<object>();

    // Called under messageLock. Copy rows before changing their wire sequences;
    // legacy /messages and its numeric sequence values remain unchanged.
    internal static MessageWindowSnapshot Create(IEnumerable<Dictionary<string, object>> ring,
        long latestSequence, long afterSequence, int limit, bool checkpoint)
    {
        // The legacy producer uses an Int64 sequence. If it ever wraps, do not
        // fabricate a valid cursor or claim that its new negative interval is complete.
        if (latestSequence < 0) throw new InvalidOperationException("The message sequence is unavailable.");
        if (limit < 1 || limit > 500) throw new ArgumentOutOfRangeException("limit");
        if (afterSequence < 0) throw new ArgumentOutOfRangeException("afterSequence");
        if (afterSequence > latestSequence)
            throw new MessageCursorException("message_cursor_ahead",
                "The message cursor is ahead of the current message sequence.", 409);
        var result = new MessageWindowSnapshot
        {
            LatestSequence = latestSequence, AfterSequence = checkpoint ? latestSequence : afterSequence,
            NextSequence = checkpoint ? latestSequence : afterSequence, RequestedLimit = limit, Checkpoint = checkpoint
        };
        foreach (Dictionary<string, object> message in ring)
        {
            long sequence = Convert.ToInt64(message["sequence"], CultureInfo.InvariantCulture);
            if (result.FirstRetainedSequence == null) result.FirstRetainedSequence = sequence;
            if (checkpoint || sequence <= afterSequence || sequence > latestSequence) continue;
            if (result.Messages.Count < limit)
            {
                var copy = new Dictionary<string, object>(message);
                copy["sequence"] = MessageCursorCodec.Decimal(sequence);
                result.Messages.Add(copy);
                result.NextSequence = sequence;
            }
            else result.RemainingCount++;
        }
        if (!checkpoint && result.FirstRetainedSequence.HasValue && result.FirstRetainedSequence.Value > afterSequence)
            result.OverwrittenCount = result.FirstRetainedSequence.Value - afterSequence - 1;
        return result;
    }

    internal Dictionary<string, object> ToResult(string nextCursor)
    {
        bool hasMore = RemainingCount != 0;
        return new Dictionary<string, object>
        {
            ["schema_version"] = 1, ["success"] = true, ["available"] = true,
            ["status"] = Checkpoint ? "checkpoint" : OverwrittenCount != 0 ? "overwritten" : hasMore ? "more" : "complete",
            ["messages"] = Messages, ["requested_limit"] = RequestedLimit, ["checkpoint"] = Checkpoint,
            ["first_retained_sequence"] = FirstRetainedSequence.HasValue ? MessageCursorCodec.Decimal(FirstRetainedSequence.Value) : null,
            ["after_sequence"] = MessageCursorCodec.Decimal(AfterSequence),
            ["latest_sequence"] = MessageCursorCodec.Decimal(LatestSequence),
            ["next_sequence"] = MessageCursorCodec.Decimal(NextSequence),
            ["returned_count"] = Messages.Count, ["overwritten_count"] = MessageCursorCodec.Decimal(OverwrittenCount),
            ["remaining_count"] = RemainingCount, ["has_more"] = hasMore,
            ["complete_since_after_sequence"] = OverwrittenCount == 0 && !hasMore,
            ["next_cursor"] = nextCursor, ["operation_correlated"] = false, ["managed_only"] = true
        };
    }
}
