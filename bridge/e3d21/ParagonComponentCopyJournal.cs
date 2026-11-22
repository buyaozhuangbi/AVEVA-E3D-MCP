using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace E3d21AiBridge;

// Pure managed, operation-scoped state owned by one adapter session. It does
// not dispatch a callback, read the SDK, execute PML, or establish durability.
// TryStart records internal callback ownership, not observed native execution.
internal sealed class ParagonComponentCopyJournal
{
    internal const string Operation = "paragon_component_copy";
    internal const int MaximumRecords = 64;
    internal const int MaximumReceiptUtf8Bytes = 262144;
    internal const int MaximumRememberedIds = 65536;
    internal const int MaximumJsonDepth = 64;
    private static readonly Regex NumericReference = new Regex(@"\A=([1-9][0-9]*)/(0|[1-9][0-9]*)\z", RegexOptions.CultureInvariant);

    internal sealed class CapacityException : InvalidOperationException
    {
        internal CapacityException(string message) : base(message) { }
    }

    // These capabilities are never in Get/Snapshot. A fabricated instance with
    // the same journal/id is invalid: entries also require exact object identity.
    internal sealed class AdmissionToken
    {
        internal readonly ParagonComponentCopyJournal Owner;
        internal readonly Guid Id;
        internal AdmissionToken(ParagonComponentCopyJournal owner, Guid id) { Owner = owner; Id = id; }
    }

    internal sealed class CallbackToken
    {
        internal readonly ParagonComponentCopyJournal Owner;
        internal readonly Guid Id;
        internal CallbackToken(ParagonComponentCopyJournal owner, Guid id) { Owner = owner; Id = id; }
    }

    internal sealed class AdmissionResult
    {
        internal readonly bool IsNew;
        internal readonly AdmissionToken Token;
        internal readonly Dictionary<string, object> Snapshot;
        internal AdmissionResult(bool isNew, AdmissionToken token, Dictionary<string, object> snapshot)
        { IsNew = isNew; Token = token; Snapshot = snapshot; }
    }

    // Independent of the optional full receipt. Missing or malformed evidence
    // remains unknown; neither running nor an error code implies native facts.
    internal sealed class CriticalSummary
    {
        private readonly bool? creationAttempted, nativeRunAttempted, outcomeUncertain, nativeFaultLatched;
        private readonly string knownNewRefno, errorCode;

        internal CriticalSummary(bool? creationAttempted = null, bool? nativeRunAttempted = null,
            string knownNewRefno = null, bool? outcomeUncertain = null,
            bool? nativeFaultLatched = null, string errorCode = null)
        {
            this.creationAttempted = creationAttempted;
            this.nativeRunAttempted = nativeRunAttempted;
            this.knownNewRefno = CanonicalReference(knownNewRefno);
            this.outcomeUncertain = outcomeUncertain;
            this.nativeFaultLatched = nativeFaultLatched;
            this.errorCode = !string.IsNullOrWhiteSpace(errorCode) && errorCode.Length <= 128 && ValidUtf16(errorCode) ? errorCode : null;
        }

        internal static CriticalSummary From(JObject facts)
        {
            if (ReferenceEquals(facts, null) || facts.GetType() != typeof(JObject)) return new CriticalSummary();
            return new CriticalSummary(Boolean(facts["creation_attempted"]), Boolean(facts["native_run_attempted"]),
                Text(facts["known_new_refno"]), Boolean(facts["outcome_uncertain"]),
                Boolean(facts["native_fault_latched"]), Text(facts["error_code"]));
        }

        internal Dictionary<string, object> Snapshot() => new Dictionary<string, object>
        {
            ["creation_attempted"] = creationAttempted, ["native_run_attempted"] = nativeRunAttempted,
            ["known_new_refno"] = knownNewRefno, ["outcome_uncertain"] = outcomeUncertain,
            ["native_fault_latched"] = nativeFaultLatched, ["error_code"] = errorCode, ["retry_safe"] = false
        };

        private static bool? Boolean(JToken token) => !ReferenceEquals(token, null) && token.GetType() == typeof(JValue)
            && token.Type == JTokenType.Boolean && ((JValue)token).Value is bool ? (bool?)((JValue)token).Value : null;
        private static string Text(JToken token) => !ReferenceEquals(token, null) && token.GetType() == typeof(JValue)
            && token.Type == JTokenType.String ? ((JValue)token).Value as string : null;
    }

    private enum EntryState { Accepted, Running, Completed }
    private sealed class Entry
    {
        internal Guid Id;
        internal string Fingerprint;
        internal AdmissionToken Admission;
        internal CallbackToken Callback;
        internal EntryState State;
        internal DateTimeOffset AcceptedUtc;
        internal DateTimeOffset? StartedUtc, CompletedUtc;
        internal long Sequence;
        internal bool CompletionClaimed;
        internal string ReceiptJson, OmittedReason;
        internal int? HttpStatus;
        internal CriticalSummary Summary = new CriticalSummary();
    }

    private readonly object sync = new object();
    private readonly Dictionary<Guid, Entry> entries = new Dictionary<Guid, Entry>();
    private readonly HashSet<Guid> seenIds = new HashSet<Guid>();
    private readonly Func<DateTimeOffset> clock;
    private readonly int capacity, maxReceiptUtf8Bytes, maxRememberedIds;
    private readonly TimeSpan retention;
    private long sequence;

    internal ParagonComponentCopyJournal(Func<DateTimeOffset> clock = null, int capacity = MaximumRecords,
        TimeSpan? retention = null, int maxReceiptUtf8Bytes = MaximumReceiptUtf8Bytes,
        int maxRememberedIds = MaximumRememberedIds)
    {
        if (capacity < 1 || capacity > MaximumRecords) throw new ArgumentOutOfRangeException("capacity");
        if (maxReceiptUtf8Bytes < 1 || maxReceiptUtf8Bytes > MaximumReceiptUtf8Bytes) throw new ArgumentOutOfRangeException("maxReceiptUtf8Bytes");
        if (maxRememberedIds < 1 || maxRememberedIds > MaximumRememberedIds) throw new ArgumentOutOfRangeException("maxRememberedIds");
        TimeSpan duration = retention ?? TimeSpan.FromMinutes(30);
        if (duration <= TimeSpan.Zero || duration > TimeSpan.FromMinutes(30)) throw new ArgumentOutOfRangeException("retention");
        this.clock = clock ?? (() => DateTimeOffset.UtcNow);
        this.capacity = capacity; this.retention = duration;
        this.maxReceiptUtf8Bytes = maxReceiptUtf8Bytes; this.maxRememberedIds = maxRememberedIds;
    }

    internal AdmissionResult Begin(string requestId, string requestFingerprint)
    {
        Guid id = RequireId(requestId);
        if (requestFingerprint == null || requestFingerprint.Length != 64 || requestFingerprint.Any(
            value => !(value >= '0' && value <= '9' || value >= 'a' && value <= 'f')))
            throw new ArgumentException("request_fingerprint must be a server-computed lowercase SHA-256 hexadecimal digest.", "requestFingerprint");
        lock (sync)
        {
            DateTimeOffset now = clock().ToUniversalTime();
            PurgeExpired(now);
            if (seenIds.Contains(id))
                return new AdmissionResult(false, null, entries.TryGetValue(id, out Entry old) ? Snapshot(old) : Unknown(id));
            if (seenIds.Count >= maxRememberedIds)
                throw new CapacityException("The session request-ID limit is full; no new ID was reserved and no old ID was released.");
            if (entries.Count >= capacity)
            {
                Entry oldest = entries.Values.Where(value => value.State == EntryState.Completed)
                    .OrderBy(value => value.CompletedUtc.Value).ThenBy(value => value.Sequence).FirstOrDefault();
                if (ReferenceEquals(oldest, null))
                    throw new CapacityException("All receipt slots are accepted or running; no active record was evicted and no new ID was reserved.");
                entries.Remove(oldest.Id);
            }
            var entry = new Entry { Id = id, Fingerprint = requestFingerprint, State = EntryState.Accepted,
                AcceptedUtc = now, Sequence = ++sequence, Admission = new AdmissionToken(this, id) };
            entries.Add(id, entry); seenIds.Add(id);
            return new AdmissionResult(true, entry.Admission, Snapshot(entry));
        }
    }

    // Only the admitted owner gives this handle to its actual callback. This
    // managed store cannot verify UI ownership or whether native code ran.
    internal bool TryStart(AdmissionToken admission, out CallbackToken callback)
    {
        callback = null;
        lock (sync)
        {
            if (!TryAdmission(admission, out Entry entry) || entry.State != EntryState.Accepted || entry.CompletionClaimed) return false;
            DateTimeOffset started = clock().ToUniversalTime();
            entry.Callback = new CallbackToken(this, entry.Id);
            entry.StartedUtc = started; entry.State = EntryState.Running;
            callback = entry.Callback;
            return true;
        }
    }

    internal bool CompleteBeforeStart(AdmissionToken admission, JObject receiptEnvelope, CriticalSummary facts, int httpStatus)
    {
        RequireStatus(httpStatus);
        Entry entry;
        lock (sync)
        {
            if (!TryAdmission(admission, out entry) || entry.State != EntryState.Accepted || entry.CompletionClaimed) return false;
            entry.CompletionClaimed = true;
        }
        return CompleteClaimed(entry, receiptEnvelope, facts, httpStatus);
    }

    internal bool CompleteStarted(CallbackToken callback, JObject receiptEnvelope, CriticalSummary facts, int httpStatus)
    {
        RequireStatus(httpStatus);
        Entry entry;
        lock (sync)
        {
            if (ReferenceEquals(callback, null) || !ReferenceEquals(callback.Owner, this)
                || !entries.TryGetValue(callback.Id, out entry) || !ReferenceEquals(entry.Callback, callback)
                || entry.State != EntryState.Running || entry.CompletionClaimed) return false;
            entry.CompletionClaimed = true;
        }
        return CompleteClaimed(entry, receiptEnvelope, facts, httpStatus);
    }

    private bool CompleteClaimed(Entry entry, JObject receiptEnvelope, CriticalSummary facts, int httpStatus)
    {
        // Accepted/running entries are not evictable while this claim is being
        // serialized. No caller data/SDK proxy is reflected or serialized.
        string json = null, omitted = null;
        CriticalSummary summary = facts ?? new CriticalSummary();
        if (ReferenceEquals(receiptEnvelope, null)) omitted = "receipt_missing";
        else
        {
            try { json = SerializeBounded(receiptEnvelope); }
            catch (ReceiptTooLargeException) { omitted = "receipt_too_large"; }
            catch (PureJsonRequiredException) { omitted = "receipt_not_pure_json"; }
            catch (Exception) { omitted = "serialization_failed"; }
        }
        lock (sync)
        {
            entry.ReceiptJson = json; entry.OmittedReason = omitted; entry.HttpStatus = httpStatus;
            entry.Summary = summary; entry.CompletedUtc = clock().ToUniversalTime(); entry.State = EntryState.Completed;
        }
        return true;
    }

    internal Dictionary<string, object> Get(string requestId)
    {
        Guid id = RequireId(requestId);
        lock (sync)
        {
            PurgeExpired(clock().ToUniversalTime());
            return entries.TryGetValue(id, out Entry entry) ? Snapshot(entry) : Unknown(id);
        }
    }

    private bool TryAdmission(AdmissionToken admission, out Entry entry)
    {
        entry = null;
        return !ReferenceEquals(admission, null) && ReferenceEquals(admission.Owner, this)
            && entries.TryGetValue(admission.Id, out entry) && ReferenceEquals(entry.Admission, admission);
    }

    private Dictionary<string, object> Snapshot(Entry entry)
    {
        object receipt = null;
        if (entry.ReceiptJson != null)
            using (var reader = new JsonTextReader(new StringReader(entry.ReceiptJson)))
            { reader.DateParseHandling = DateParseHandling.None; reader.MaxDepth = MaximumJsonDepth; receipt = JToken.ReadFrom(reader); }
        bool completed = entry.State == EntryState.Completed;
        return new Dictionary<string, object>
        {
            ["operation"] = Operation, ["available"] = true,
            ["status"] = completed ? "completed" : entry.State == EntryState.Running ? "running" : "accepted",
            ["request_id"] = entry.Id.ToString("D"), ["request_fingerprint"] = entry.Fingerprint, ["request_id_reserved"] = true,
            ["accepted_utc"] = Time(entry.AcceptedUtc), ["started_utc"] = Time(entry.StartedUtc), ["completed_utc"] = Time(entry.CompletedUtc),
            ["completion_pending"] = entry.CompletionClaimed && !completed, ["http_status"] = entry.HttpStatus,
            ["receipt_available"] = entry.ReceiptJson != null, ["receipt_omitted_reason"] = completed ? entry.OmittedReason : "not_completed",
            ["receipt"] = receipt, ["critical_summary"] = entry.Summary.Snapshot(), ["retry_safe"] = false,
            ["store_policy"] = Policy(), ["scope"] = Scope
        };
    }

    private Dictionary<string, object> Unknown(Guid id) => new Dictionary<string, object>
    {
        ["operation"] = Operation, ["available"] = false, ["status"] = "unknown_or_expired",
        ["request_id"] = id.ToString("D"), ["request_fingerprint"] = null, ["request_id_reserved"] = seenIds.Contains(id),
        ["accepted_utc"] = null, ["started_utc"] = null, ["completed_utc"] = null, ["completion_pending"] = false,
        ["http_status"] = null, ["receipt_available"] = false, ["receipt_omitted_reason"] = "unknown_or_expired", ["receipt"] = null,
        ["critical_summary"] = new CriticalSummary().Snapshot(), ["retry_safe"] = false, ["store_policy"] = Policy(), ["scope"] = Scope
    };

    private const string Scope = "Session-local managed callback ownership only; accepted is not running, running does not prove native execution, and completed records a final receipt rather than native success. Unknown/expired does not prove no execution. No replay, durability, cross-session exactly-once, or SDK/UI ownership guarantee.";
    private Dictionary<string, object> Policy() => new Dictionary<string, object>
    {
        ["receipt_record_limit"] = capacity, ["max_receipt_utf8_bytes"] = maxReceiptUtf8Bytes,
        ["session_request_id_limit"] = maxRememberedIds, ["completed_retention_seconds"] = retention.TotalSeconds,
        ["maximum_json_depth"] = MaximumJsonDepth, ["receipt_budget_kind"] = "serialized_utf8_logical_bytes",
        ["accepted_or_running_evicted"] = false, ["request_ids_released_before_session_end"] = false,
        ["durable"] = false, ["cross_session_exactly_once"] = false
    };

    private void PurgeExpired(DateTimeOffset now)
    {
        foreach (Guid id in entries.Values.Where(value => value.State == EntryState.Completed
            && now - value.CompletedUtc.Value >= retention).Select(value => value.Id).ToArray()) entries.Remove(id);
    }

    private static string Time(DateTimeOffset? value) => value.HasValue ? value.Value.ToString("O", CultureInfo.InvariantCulture) : null;
    private static Guid RequireId(string value)
    {
        if (value == null || !Guid.TryParseExact(value, "D", out Guid id) || value != id.ToString("D"))
            throw new ArgumentException("request_id must be a canonical lowercase UUID with hyphens.", "requestId");
        return id;
    }
    private static void RequireStatus(int value)
    { if (value < 100 || value > 599) throw new ArgumentOutOfRangeException("httpStatus"); }
    private static string CanonicalReference(string value)
    {
        if (value == null || value.Length > 32) return null;
        Match match = NumericReference.Match(value);
        return match.Success && int.TryParse(match.Groups[1].Value, NumberStyles.None, CultureInfo.InvariantCulture, out _)
            && int.TryParse(match.Groups[2].Value, NumberStyles.None, CultureInfo.InvariantCulture, out _) ? value : null;
    }
    private static bool ValidUtf16(string value)
    {
        for (int index = 0; index < value.Length; index++)
            if (char.IsSurrogate(value[index]) && (!char.IsHighSurrogate(value[index]) || ++index >= value.Length || !char.IsLowSurrogate(value[index]))) return false;
        return true;
    }

    private sealed class ReceiptTooLargeException : IOException { }
    private sealed class PureJsonRequiredException : InvalidOperationException { }
    private string SerializeBounded(JObject value)
    {
        using (var bytes = new BoundedStream(maxReceiptUtf8Bytes))
        {
            using (var output = new StreamWriter(bytes, new UTF8Encoding(false, true), 1024, true))
            using (var writer = new JsonTextWriter(output))
            {
                writer.CloseOutput = false;
                WritePureJson(value, writer, 1);
                writer.Flush(); output.Flush();
            }
            return new UTF8Encoding(false, true).GetString(bytes.ToArray());
        }
    }

    // Avoid JToken.WriteTo/JsonSerializer virtual dispatch, converters, object
    // reflection, and arbitrary CLR values. Only exact JSON containers/values
    // and primitive CLR payloads can reach this writer, including under mutation.
    private void WritePureJson(JToken token, JsonTextWriter writer, int depth)
    {
        if (depth > MaximumJsonDepth || ReferenceEquals(token, null)) throw new PureJsonRequiredException();
        Type type = token.GetType();
        if (type == typeof(JObject))
        {
            writer.WriteStartObject();
            foreach (JProperty property in ((JObject)token).Properties())
            {
                if (property.GetType() != typeof(JProperty)) throw new PureJsonRequiredException();
                CheckText(property.Name); writer.WritePropertyName(property.Name); WritePureJson(property.Value, writer, depth + 1);
            }
            writer.WriteEndObject(); return;
        }
        if (type == typeof(JArray))
        {
            writer.WriteStartArray(); foreach (JToken item in (JArray)token) WritePureJson(item, writer, depth + 1);
            writer.WriteEndArray(); return;
        }
        if (type != typeof(JValue)) throw new PureJsonRequiredException();
        object value = ((JValue)token).Value;
        switch (token.Type)
        {
            case JTokenType.Null: writer.WriteNull(); return;
            case JTokenType.Boolean: if (value is bool boolean) { writer.WriteValue(boolean); return; } break;
            case JTokenType.String:
                if (value is string text) { CheckText(text); writer.WriteValue(text); return; } break;
            case JTokenType.Integer:
                if (value is long signed) { writer.WriteValue(signed); return; }
                if (value is ulong unsigned) { writer.WriteValue(unsigned); return; }
                if (value is int integer) { writer.WriteValue(integer); return; }
                if (value is uint uinteger) { writer.WriteValue(uinteger); return; }
                if (value is short small) { writer.WriteValue(small); return; }
                if (value is ushort usmall) { writer.WriteValue(usmall); return; }
                if (value is byte octet) { writer.WriteValue(octet); return; }
                if (value is sbyte signedOctet) { writer.WriteValue(signedOctet); return; } break;
            case JTokenType.Float:
                if (value is double number && !double.IsNaN(number) && !double.IsInfinity(number)) { writer.WriteValue(number); return; }
                if (value is float single && !float.IsNaN(single) && !float.IsInfinity(single)) { writer.WriteValue(single); return; }
                if (value is decimal precise) { writer.WriteValue(precise); return; } break;
        }
        throw new PureJsonRequiredException();
    }
    private void CheckText(string value)
    {
        if (value.Length > maxReceiptUtf8Bytes) throw new ReceiptTooLargeException();
        if (!ValidUtf16(value)) throw new PureJsonRequiredException();
    }
    private sealed class BoundedStream : MemoryStream
    {
        private readonly int maximum;
        internal BoundedStream(int maximum) { this.maximum = maximum; }
        public override void Write(byte[] buffer, int offset, int count)
        { if (Position + count > maximum) throw new ReceiptTooLargeException(); base.Write(buffer, offset, count); }
        public override void WriteByte(byte value)
        { if (Position + 1 > maximum) throw new ReceiptTooLargeException(); base.WriteByte(value); }
    }
}
