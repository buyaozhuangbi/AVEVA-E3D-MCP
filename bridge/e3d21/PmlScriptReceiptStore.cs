using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace E3d21AiBridge;

// One store belongs to one adapter session. It never reads the SDK or executes
// a script. Pending means accepted, not observed running. Receipt records are
// bounded/expiring; accepted UUID tombstones remain for the session so eviction
// cannot make an old request executable again. The tombstone cap fails closed.
internal sealed class PmlScriptReceiptStore
{
    internal sealed class DuplicateRequestException : InvalidOperationException
    {
        internal DuplicateRequestException() : base("This request_id was already accepted in this adapter session; it will not be executed again.") { }
    }

    internal sealed class CapacityException : InvalidOperationException
    {
        internal CapacityException(string message) : base(message) { }
    }

    private sealed class Entry
    {
        internal Guid Id;
        internal string SourceSha256;
        internal int ByteCount;
        internal DateTimeOffset AcceptedUtc;
        internal DateTimeOffset? CompletedUtc;
        internal long Sequence;
        internal bool CompletionClaimed;
        internal string ReceiptJson;
        internal string OmittedReason;
        internal int? HttpStatus;
        internal CompletionSummary Summary;
    }

    private sealed class CompletionSummary
    {
        internal object NativeSuccess, ExecutionAttempted, ExecutionCompleted, OutcomeUncertain;
        internal Dictionary<string, object> Snapshot()
        {
            return new Dictionary<string, object>
            {
                ["native_success"] = NativeSuccess, ["execution_attempted"] = ExecutionAttempted,
                ["execution_completed"] = ExecutionCompleted, ["outcome_uncertain"] = OutcomeUncertain
            };
        }
    }

    private readonly object sync = new object();
    private readonly Dictionary<Guid, Entry> entries = new Dictionary<Guid, Entry>();
    private readonly HashSet<Guid> seenIds = new HashSet<Guid>();
    private readonly Func<DateTimeOffset> clock;
    private readonly int capacity, maxReceiptUtf8Bytes, maxRememberedIds;
    private readonly TimeSpan retention;
    private long sequence;

    internal PmlScriptReceiptStore(Func<DateTimeOffset> clock = null, int capacity = 64,
        TimeSpan? retention = null, int maxReceiptUtf8Bytes = 262144, int maxRememberedIds = 65536)
    {
        if (capacity < 1 || capacity > 64) throw new ArgumentOutOfRangeException("capacity");
        if (maxReceiptUtf8Bytes < 1 || maxReceiptUtf8Bytes > 262144) throw new ArgumentOutOfRangeException("maxReceiptUtf8Bytes");
        if (maxRememberedIds < 1 || maxRememberedIds > 65536) throw new ArgumentOutOfRangeException("maxRememberedIds");
        TimeSpan duration = retention ?? TimeSpan.FromMinutes(30);
        if (duration <= TimeSpan.Zero || duration > TimeSpan.FromMinutes(30)) throw new ArgumentOutOfRangeException("retention");
        this.clock = clock ?? (() => DateTimeOffset.UtcNow);
        this.capacity = capacity;
        this.retention = duration;
        this.maxReceiptUtf8Bytes = maxReceiptUtf8Bytes;
        this.maxRememberedIds = maxRememberedIds;
    }

    internal Dictionary<string, object> Begin(string requestId, string sourceSha256, int byteCount)
    {
        Guid id = RequireId(requestId);
        if (sourceSha256 == null || sourceSha256.Length != 64 ||
            sourceSha256.Any(value => !(value >= '0' && value <= '9' || value >= 'a' && value <= 'f')))
            throw new ArgumentException("source_sha256 must be a lowercase SHA-256 hexadecimal digest.", "sourceSha256");
        if (byteCount < 1 || byteCount > 262144) throw new ArgumentOutOfRangeException("byteCount");
        lock (sync)
        {
            if (seenIds.Contains(id)) throw new DuplicateRequestException();
            if (seenIds.Count >= maxRememberedIds)
                throw new CapacityException("The session request-ID reservation limit has been reached; no new request was accepted and no old ID was released.");
            DateTimeOffset now = clock().ToUniversalTime();
            PurgeExpired(now);
            if (entries.Count >= capacity)
            {
                Entry oldest = entries.Values.Where(value => value.CompletedUtc.HasValue)
                    .OrderBy(value => value.CompletedUtc.Value).ThenBy(value => value.Sequence).FirstOrDefault();
                if (oldest == null)
                    throw new CapacityException("All retained script receipt slots are pending; no pending request was evicted and no new request was accepted.");
                entries.Remove(oldest.Id);
            }
            var entry = new Entry
            {
                Id = id, SourceSha256 = sourceSha256, ByteCount = byteCount,
                AcceptedUtc = now, Sequence = ++sequence, Summary = new CompletionSummary()
            };
            entries.Add(id, entry);
            seenIds.Add(id);
            return Snapshot(entry);
        }
    }

    internal bool Complete(string requestId, object receiptEnvelope, int httpStatus)
    {
        Guid id = RequireId(requestId);
        if (httpStatus < 100 || httpStatus > 599) throw new ArgumentOutOfRangeException("httpStatus");
        Entry entry;
        lock (sync)
        {
            if (!entries.TryGetValue(id, out entry) || entry.CompletionClaimed) return false;
            entry.CompletionClaimed = true;
        }
        // Serialize outside the store lock: a concurrent Get continues to return
        // pending, and duplicate completion cannot overwrite this first claim.
        string json = null, omitted = null;
        CompletionSummary summary = Summarize(receiptEnvelope);
        try { json = SerializeBounded(receiptEnvelope); }
        catch (Exception error) { omitted = HasSizeFailure(error) ? "receipt_too_large" : "serialization_failed"; }
        lock (sync)
        {
            entry.ReceiptJson = json;
            entry.OmittedReason = omitted;
            entry.HttpStatus = httpStatus;
            entry.Summary = summary;
            entry.CompletedUtc = clock().ToUniversalTime();
        }
        return true;
    }

    internal Dictionary<string, object> Get(string requestId)
    {
        Guid id = RequireId(requestId);
        lock (sync)
        {
            PurgeExpired(clock().ToUniversalTime());
            Entry entry;
            if (entries.TryGetValue(id, out entry)) return Snapshot(entry);
            return new Dictionary<string, object>
            {
                ["available"] = false, ["status"] = "unknown_or_expired", ["request_id"] = id.ToString("D"),
                ["request_id_reserved"] = seenIds.Contains(id), ["accepted_utc"] = null, ["completed_utc"] = null,
                ["source_sha256"] = null, ["byte_count"] = null, ["receipt_available"] = false,
                ["receipt_omitted_reason"] = "unknown_or_expired", ["receipt"] = null, ["http_status"] = null,
                ["summary"] = new CompletionSummary().Snapshot(), ["store_policy"] = Policy(),
                ["scope"] = "Missing or expired receipt does not prove that the request was never executed. Querying never reserves or replays an ID."
            };
        }
    }

    private Dictionary<string, object> Snapshot(Entry entry)
    {
        bool complete = entry.CompletedUtc.HasValue;
        // Parse a fresh token tree for every caller. Neither changes to the
        // original envelope nor changes to this snapshot can mutate storage.
        object receipt = null;
        if (entry.ReceiptJson != null)
            using (var reader = new JsonTextReader(new StringReader(entry.ReceiptJson)))
            {
                reader.DateParseHandling = DateParseHandling.None;
                receipt = JToken.ReadFrom(reader);
            }
        return new Dictionary<string, object>
        {
            ["available"] = true, ["status"] = complete ? "completed" : "pending",
            ["request_id"] = entry.Id.ToString("D"), ["request_id_reserved"] = true,
            ["accepted_utc"] = entry.AcceptedUtc.ToString("O", CultureInfo.InvariantCulture),
            ["completed_utc"] = complete ? entry.CompletedUtc.Value.ToString("O", CultureInfo.InvariantCulture) : null,
            ["source_sha256"] = entry.SourceSha256, ["byte_count"] = entry.ByteCount,
            ["receipt_available"] = entry.ReceiptJson != null,
            ["receipt_omitted_reason"] = complete ? entry.OmittedReason : "pending",
            ["receipt"] = receipt, ["http_status"] = entry.HttpStatus,
            ["summary"] = entry.Summary.Snapshot(), ["store_policy"] = Policy(),
            ["scope"] = "Pending means accepted, not observed executing. Completed means a final HTTP receipt was recorded, not necessarily successful native execution."
        };
    }

    private Dictionary<string, object> Policy()
    {
        return new Dictionary<string, object>
        {
            ["receipt_record_limit"] = capacity, ["completed_retention_seconds"] = retention.TotalSeconds,
            ["max_receipt_utf8_bytes"] = maxReceiptUtf8Bytes, ["session_request_id_limit"] = maxRememberedIds,
            ["pending_evicted"] = false, ["request_ids_released_before_session_end"] = false
        };
    }

    private void PurgeExpired(DateTimeOffset now)
    {
        foreach (Guid id in entries.Values.Where(value => value.CompletedUtc.HasValue &&
            now - value.CompletedUtc.Value >= retention).Select(value => value.Id).ToArray())
            entries.Remove(id);
    }

    private static Guid RequireId(string value)
    {
        Guid id;
        if (value == null || !Guid.TryParseExact(value, "D", out id) || value != id.ToString("D"))
            throw new ArgumentException("request_id must be a canonical lowercase UUID with hyphens.", "requestId");
        return id;
    }

    private static CompletionSummary Summarize(object envelope)
    {
        object result = Field(envelope, "result");
        return new CompletionSummary
        {
            NativeSuccess = BooleanField(result, "success"),
            ExecutionAttempted = BooleanField(result, "execution_attempted") ?? BooleanField(envelope, "execution_attempted"),
            ExecutionCompleted = BooleanField(result, "execution_completed") ?? BooleanField(envelope, "execution_completed"),
            OutcomeUncertain = BooleanField(result, "outcome_uncertain") ?? BooleanField(envelope, "outcome_uncertain")
        };
    }

    private static object BooleanField(object value, string name)
    {
        object item = Field(value, name);
        if (item is bool) return item;
        var token = item as JValue;
        return token != null && token.Type == JTokenType.Boolean ? (object)(bool)token : null;
    }

    private static object Field(object value, string name)
    {
        try
        {
            var json = value as JObject;
            if (json != null) return json[name];
            var dictionary = value as IDictionary;
            if (dictionary != null) return dictionary.Contains(name) ? dictionary[name] : null;
            if (value == null) return null;
            PropertyInfo property = value.GetType().GetProperty(name, BindingFlags.Public | BindingFlags.Instance);
            return property == null || property.GetIndexParameters().Length != 0 ? null : property.GetValue(value, null);
        }
        catch { return null; }
    }

    private string SerializeBounded(object value)
    {
        if (value == null) throw new JsonSerializationException("A final receipt envelope is required.");
        using (var bytes = new BoundedStream(maxReceiptUtf8Bytes))
        {
            using (var output = new StreamWriter(bytes, new UTF8Encoding(false, true), 1024, true))
            using (var writer = new JsonTextWriter(output))
            {
                writer.CloseOutput = false;
                JsonSerializer.Create(new JsonSerializerSettings
                {
                    TypeNameHandling = TypeNameHandling.None,
                    ReferenceLoopHandling = ReferenceLoopHandling.Error,
                    Formatting = Formatting.None
                }).Serialize(writer, value);
                writer.Flush();
                output.Flush();
            }
            return new UTF8Encoding(false, true).GetString(bytes.ToArray());
        }
    }

    private static bool HasSizeFailure(Exception error)
    {
        for (Exception current = error; current != null; current = current.InnerException)
            if (current is ReceiptTooLargeException) return true;
        return false;
    }

    private sealed class ReceiptTooLargeException : IOException { }

    private sealed class BoundedStream : MemoryStream
    {
        private readonly int maximum;
        internal BoundedStream(int maximum) { this.maximum = maximum; }
        public override void Write(byte[] buffer, int offset, int count)
        {
            if (Position + count > maximum) throw new ReceiptTooLargeException();
            base.Write(buffer, offset, count);
        }
        public override void WriteByte(byte value)
        {
            if (Position + 1 > maximum) throw new ReceiptTooLargeException();
            base.WriteByte(value);
        }
    }
}
