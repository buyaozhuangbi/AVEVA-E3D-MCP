using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using E3d21AiBridge;

internal static class PmlScriptReceiptStoreHarness
{
    private static int checks;
    private static readonly string Hash = new string('a', 64);
    private static readonly DateTimeOffset Epoch = new DateTimeOffset(2026, 11, 16, 10, 0, 0, TimeSpan.Zero);

    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) throw new Exception(message);
    }

    private static void Reject<T>(Action action, string message) where T : Exception
    {
        bool rejected = false;
        try { action(); } catch (T) { rejected = true; }
        Check(rejected, message);
    }

    private static string Id() { return Guid.NewGuid().ToString("D"); }
    private static Dictionary<string, object> Row(object value) { return (Dictionary<string, object>)value; }
    private static Dictionary<string, object> Envelope(bool success = true, string output = "native output")
    {
        return new Dictionary<string, object>
        {
            ["ok"] = true, ["version"] = "E3D21", ["session_id"] = "original-session",
            ["result"] = new Dictionary<string, object>
            {
                ["success"] = success, ["execution_attempted"] = true,
                ["execution_completed"] = true, ["outcome_uncertain"] = false,
                ["result"] = output
            }
        };
    }

    private static void LifecycleAndDetachment()
    {
        DateTimeOffset now = Epoch;
        var store = new PmlScriptReceiptStore(() => now);
        string id = Id();
        var unknown = store.Get(id);
        Check(Equals(unknown["available"], false) && Equals(unknown["status"], "unknown_or_expired")
            && Equals(unknown["request_id_reserved"], false), "A read of an unknown ID neither reserves it nor claims execution absence.");
        var pending = store.Begin(id, Hash, 123);
        Check(Equals(pending["status"], "pending") && Equals(pending["available"], true) && Equals(pending["request_id_reserved"], true),
            "Begin atomically records accepted pending work.");
        Check(pending["completed_utc"] == null && pending["http_status"] == null && pending["receipt"] == null
            && Equals(pending["receipt_available"], false) && Row(pending["summary"]).Values.All(value => value == null),
            "Pending cannot fabricate running state, completion, success or an HTTP receipt.");
        Check(Equals(pending["source_sha256"], Hash) && Equals(pending["byte_count"], 123) && !pending.ContainsKey("source"),
            "Only source identity metadata is accepted; no submitted source field is retained.");
        Reject<PmlScriptReceiptStore.DuplicateRequestException>(() => store.Begin(id, Hash, 123), "Even an identical duplicate request is rejected.");
        Reject<PmlScriptReceiptStore.DuplicateRequestException>(() => store.Begin(id, new string('b', 64), 124), "Changed source cannot reuse an accepted ID.");
        now = Epoch.AddMinutes(2);
        var envelope = Envelope();
        Check(store.Complete(id, envelope, 200), "The first completion succeeds.");
        Row(envelope["result"])["result"] = "mutated after completion";
        var final = store.Get(id);
        Check(Equals(final["status"], "completed") && Equals(final["http_status"], 200) && Equals(final["receipt_available"], true),
            "Completed records expose HTTP status independently from native success.");
        Check((string)((JObject)final["receipt"])["result"]["result"] == "native output", "Completion detaches the original mutable envelope.");
        Check(Equals(final["accepted_utc"], Epoch.ToString("O")) && Equals(final["completed_utc"], now.ToString("O")), "Acceptance and completion timestamps are independent.");
        ((JObject)final["receipt"])["result"]["result"] = "mutated snapshot";
        Row(final["summary"])["native_success"] = false;
        Row(final["store_policy"])["receipt_record_limit"] = 9000;
        var another = store.Get(id);
        Check((string)((JObject)another["receipt"])["result"]["result"] == "native output"
            && Equals(Row(another["summary"])["native_success"], true)
            && Equals(Row(another["store_policy"])["receipt_record_limit"], 64), "Each returned receipt and metadata tree is independently detached.");
        Check(!store.Complete(id, Envelope(false), 500) && Equals(store.Get(id)["http_status"], 200), "A later completion cannot overwrite the first final receipt.");
        Check(!store.Complete(Id(), Envelope(), 200), "Completion cannot manufacture a record for an unaccepted ID.");
    }

    private static void CapacityRetentionAndReservations()
    {
        DateTimeOffset now = Epoch;
        var store = new PmlScriptReceiptStore(() => now, capacity: 2, retention: TimeSpan.FromMinutes(1), maxRememberedIds: 4);
        string first = Id(), second = Id(), third = Id(), fourth = Id(), fifth = Id();
        store.Begin(first, Hash, 1); store.Begin(second, Hash, 2);
        now = now.AddHours(5);
        Reject<PmlScriptReceiptStore.CapacityException>(() => store.Begin(third, Hash, 3), "Pending records never expire or get evicted to make space.");
        Check(Equals(store.Get(first)["status"], "pending") && Equals(store.Get(third)["request_id_reserved"], false), "Rejected capacity requests do not consume IDs.");
        store.Complete(first, Envelope(false), 200);
        now = now.AddSeconds(1);
        store.Begin(third, Hash, 3);
        Check(Equals(store.Get(first)["status"], "unknown_or_expired") && Equals(store.Get(first)["request_id_reserved"], true),
            "Only completed receipts are evicted; their IDs remain reserved.");
        Reject<PmlScriptReceiptStore.DuplicateRequestException>(() => store.Begin(first, Hash, 1), "Eviction cannot make an old request executable again.");
        store.Complete(second, Envelope(), 200);
        now = now.AddSeconds(59);
        Check(Equals(store.Get(second)["status"], "completed"), "Retention is measured from completion, not acceptance.");
        now = now.AddSeconds(1);
        Check(Equals(store.Get(second)["status"], "unknown_or_expired") && Equals(store.Get(second)["request_id_reserved"], true),
            "Completed receipts expire at the exact retention boundary, without releasing IDs.");
        store.Begin(fourth, Hash, 4); store.Complete(third, Envelope(), 200); store.Complete(fourth, Envelope(), 200);
        now = now.AddMinutes(2);
        store.Get(third); store.Get(fourth);
        Reject<PmlScriptReceiptStore.CapacityException>(() => store.Begin(fifth, Hash, 5), "The session ID reservation cap stays closed even after all receipts expire.");
        Check(Equals(store.Get(fifth)["request_id_reserved"], false), "ID-cap rejection does not reserve a new request.");
        Reject<PmlScriptReceiptStore.DuplicateRequestException>(() => store.Begin(first, Hash, 1), "Duplicate detection retains its distinct error after the ID cap is full.");
        var freshSession = new PmlScriptReceiptStore(() => now);
        Check(Equals(freshSession.Get(first)["request_id_reserved"], false), "Independent stores provide separate session state; the HTTP session guard must identify the intended one.");
    }

    private sealed class ThrowingReceipt
    {
        public object result { get { return new { success = false, execution_attempted = true, execution_completed = false, outcome_uncertain = true }; } }
        public string bad { get { throw new InvalidOperationException("Do not expose private serializer details"); } }
    }

    private static void BoundedAndFailedSerialization()
    {
        var exactEnvelope = Envelope(false, "中");
        int exactBytes = Encoding.UTF8.GetByteCount(JsonConvert.SerializeObject(exactEnvelope));
        var exact = new PmlScriptReceiptStore(() => Epoch, maxReceiptUtf8Bytes: exactBytes);
        string id = Id(); exact.Begin(id, Hash, 1); exact.Complete(id, exactEnvelope, 200);
        Check(Equals(exact.Get(id)["receipt_available"], true), "A receipt exactly at the UTF-8 byte limit is retained.");
        var small = new PmlScriptReceiptStore(() => Epoch, maxReceiptUtf8Bytes: exactBytes - 1);
        id = Id(); small.Begin(id, Hash, 1); small.Complete(id, exactEnvelope, 200);
        var result = small.Get(id);
        Check(Equals(result["status"], "completed") && Equals(result["receipt_available"], false)
            && Equals(result["receipt_omitted_reason"], "receipt_too_large") && result["receipt"] == null,
            "The limit counts UTF-8 bytes, and oversized completion does not remain pending.");
        Check(Equals(Row(result["summary"])["native_success"], false) && Equals(Row(result["summary"])["execution_attempted"], true)
            && Equals(Row(result["summary"])["execution_completed"], true) && Equals(Row(result["summary"])["outcome_uncertain"], false),
            "An omitted receipt retains only the actual typed native completion summary.");
        id = Id(); small.Begin(id, Hash, 1); small.Complete(id, new ThrowingReceipt(), 500);
        result = small.Get(id);
        Check(Equals(result["status"], "completed") && Equals(result["receipt_omitted_reason"], "serialization_failed")
            && Equals(result["http_status"], 500) && Equals(Row(result["summary"])["outcome_uncertain"], true),
            "A throwing serializer property still records a completed error receipt summary.");
        Check(!JsonConvert.SerializeObject(result).Contains("private serializer details"), "Serialization errors do not leak raw exception details.");
        var cycle = new Dictionary<string, object>(); cycle["self"] = cycle;
        id = Id(); small.Begin(id, Hash, 1); small.Complete(id, cycle, 500);
        Check(Equals(small.Get(id)["status"], "completed") && Equals(small.Get(id)["receipt_omitted_reason"], "serialization_failed"),
            "Reference loops cannot strand a receipt in pending.");
        id = Id(); small.Begin(id, Hash, 1); small.Complete(id,
            new { ok = false, execution_attempted = false, outcome_uncertain = false, error = "HTTP failure" }, 500);
        var summary = Row(small.Get(id)["summary"]);
        Check(summary["native_success"] == null && Equals(summary["execution_attempted"], false)
            && summary["execution_completed"] == null, "An HTTP 500 does not fabricate a native success/failure or completed execution flag.");
        id = Id(); small.Begin(id, Hash, 1); small.Complete(id,
            new { result = new { success = "true", execution_attempted = 1, execution_completed = false, outcome_uncertain = (bool?)null } }, 200);
        summary = Row(small.Get(id)["summary"]);
        Check(summary["native_success"] == null && summary["execution_attempted"] == null && Equals(summary["execution_completed"], false),
            "Summary flags preserve unknown instead of coercing strings/numbers to success.");
    }

    private sealed class BlockingReceipt
    {
        internal readonly ManualResetEventSlim Entered = new ManualResetEventSlim(), Release = new ManualResetEventSlim();
        public bool ok
        {
            get { Entered.Set(); if (!Release.Wait(5000)) throw new Exception("Test receipt was not released."); return true; }
        }
        public object result { get { return new { success = true, execution_attempted = true, execution_completed = true, outcome_uncertain = false }; } }
    }

    private static void Concurrency()
    {
        var store = new PmlScriptReceiptStore(() => Epoch);
        string id = Id();
        int accepted = 0, duplicates = 0;
        var threads = Enumerable.Range(0, 16).Select(index => new Thread(() =>
        {
            try { store.Begin(id, Hash, 123); Interlocked.Increment(ref accepted); }
            catch (PmlScriptReceiptStore.DuplicateRequestException) { Interlocked.Increment(ref duplicates); }
        })).ToArray();
        foreach (var thread in threads) thread.Start();
        foreach (var thread in threads) thread.Join();
        Check(accepted == 1 && duplicates == 15, "Concurrent Begin calls reserve one ID exactly once.");
        var receipt = new BlockingReceipt();
        bool completed = false;
        var completion = new Thread(() => completed = store.Complete(id, receipt, 200));
        completion.Start();
        try
        {
            Check(receipt.Entered.Wait(5000), "The first completion enters serialization.");
            Check(Equals(store.Get(id)["status"], "pending") && !store.Complete(id, Envelope(false), 500),
                "Reads do not block on serialization and duplicate Complete cannot race to overwrite the first claim.");
            Reject<PmlScriptReceiptStore.DuplicateRequestException>(() => store.Begin(id, Hash, 123), "A completing request remains reserved.");
        }
        finally { receipt.Release.Set(); completion.Join(5000); }
        Check(completed && Equals(store.Get(id)["status"], "completed") && Equals(store.Get(id)["http_status"], 200),
            "The first completion publishes atomically after serialization.");
    }

    private static void Validation()
    {
        var store = new PmlScriptReceiptStore(() => Epoch);
        foreach (string invalid in new[] { null, "", Guid.NewGuid().ToString("N"), " AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA ", "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA" })
            Reject<ArgumentException>(() => store.Begin(invalid, Hash, 1), "Only canonical lowercase hyphenated UUIDs are accepted.");
        foreach (string invalid in new[] { null, "", new string('a', 63), new string('A', 64), new string('z', 64) })
            Reject<ArgumentException>(() => store.Begin(Id(), invalid, 1), "Source digests must be exact lowercase SHA-256 hex.");
        Reject<ArgumentOutOfRangeException>(() => store.Begin(Id(), Hash, 0), "Byte count must describe nonempty source.");
        Reject<ArgumentOutOfRangeException>(() => store.Begin(Id(), Hash, 262145), "Source byte metadata remains bounded.");
        Reject<ArgumentOutOfRangeException>(() => new PmlScriptReceiptStore(capacity: 65), "Receipt retention cannot exceed 64 records.");
        Reject<ArgumentOutOfRangeException>(() => new PmlScriptReceiptStore(maxRememberedIds: 65537), "Session tombstones have a hard upper bound.");
        Reject<ArgumentOutOfRangeException>(() => new PmlScriptReceiptStore(retention: TimeSpan.FromMinutes(31)), "Completed retention cannot exceed 30 minutes.");
        Reject<ArgumentOutOfRangeException>(() => new PmlScriptReceiptStore(maxReceiptUtf8Bytes: 262145), "Receipt serialization cannot exceed the production byte cap.");
    }

    private static int Main()
    {
        try
        {
            LifecycleAndDetachment(); CapacityRetentionAndReservations(); BoundedAndFailedSerialization(); Concurrency(); Validation();
            Console.WriteLine("PML receipt store checks passed: " + checks);
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
