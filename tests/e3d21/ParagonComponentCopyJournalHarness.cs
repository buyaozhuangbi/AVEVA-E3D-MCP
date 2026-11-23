using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using E3d21AiBridge;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Journal = E3d21AiBridge.ParagonComponentCopyJournal;

internal static class ParagonComponentCopyJournalHarness
{
    private static int assertions, hostileWrites;
    private static string Id(int value) => "00000000-0000-0000-0000-" + value.ToString("x12", CultureInfo.InvariantCulture);
    private static string Digest(char value = 'a') => new string(value, 64);
    private static JObject Json(object value) => JObject.FromObject(value);
    private static JObject Status(Journal journal, int id) => Json(journal.Get(Id(id)));
    private static bool Null(JToken value) => value == null || value.Type == JTokenType.Null;
    private static void Check(bool value, string message)
    { Interlocked.Increment(ref assertions); if (!value) throw new Exception(message); }
    private static void Throws<T>(Action action, string message) where T : Exception
    { bool rejected = false; try { action(); } catch (T) { rejected = true; } Check(rejected, message); }
    private static Journal.CriticalSummary Facts(bool? attempted = true, string reference = "=2/42", bool? fault = true) =>
        new Journal.CriticalSummary(attempted, attempted, reference, true, fault, "native_database_fault");
    private static JObject Receipt(int marker = 1) => new JObject { ["ok"] = false, ["result"] = new JObject
        { ["marker"] = marker, ["native_run_attempted"] = false, ["known_new_refno"] = "=999/999", ["time"] = "2026-11-22T01:02:03Z" } };
    private static void UnknownFacts(JToken facts, string message)
    {
        foreach (string name in new[] { "creation_attempted", "native_run_attempted", "native_run_returned", "native_run_success",
            "known_new_refno", "known_new_refno_verified", "readback_complete", "source_unchanged_scoped", "outcome_uncertain", "native_fault_latched", "error_code" })
            Check(Null(facts[name]), message + ": " + name);
        Check((bool)facts["retry_safe"] == false, message + ": retry_safe");
    }

    private sealed class HostileReceipt : JObject
    {
        public override void WriteTo(JsonWriter writer, params JsonConverter[] converters)
        { Interlocked.Increment(ref hostileWrites); throw new Exception("A caller-defined serializer must never execute."); }
    }

    private static void Validation()
    {
        foreach (int value in new[] { 0, -1, 65 }) Throws<ArgumentOutOfRangeException>(() => new Journal(capacity: value), "Invalid record budget accepted.");
        foreach (int value in new[] { 0, -1, 262145 }) Throws<ArgumentOutOfRangeException>(() => new Journal(maxReceiptUtf8Bytes: value), "Invalid receipt byte budget accepted.");
        foreach (int value in new[] { 0, -1, 65537 }) Throws<ArgumentOutOfRangeException>(() => new Journal(maxRememberedIds: value), "Invalid tombstone budget accepted.");
        foreach (TimeSpan value in new[] { TimeSpan.Zero, TimeSpan.FromTicks(-1), TimeSpan.FromMinutes(30) + TimeSpan.FromTicks(1) })
            Throws<ArgumentOutOfRangeException>(() => new Journal(retention: value), "Invalid retention accepted.");
        var journal = new Journal();
        foreach (string id in new[] { null, "", " " + Id(1), Id(1) + " ", Id(1) + "\n", "{00000000-0000-0000-0000-000000000001}", "00000000000000000000000000000001", "00000000-0000-0000-0000-00000000000A" })
        { Throws<ArgumentException>(() => journal.Begin(id, Digest()), "Invalid UUID admitted."); Throws<ArgumentException>(() => journal.Get(id), "Invalid UUID query accepted."); }
        foreach (string fingerprint in new[] { null, "", new string('a', 63), new string('a', 65), Digest('A'), Digest('g'), Digest() + "\n" })
            Throws<ArgumentException>(() => journal.Begin(Id(1), fingerprint), "Invalid fingerprint admitted.");
        JObject unknown = Status(journal, 1);
        Check((string)unknown["status"] == "unknown_or_expired" && !(bool)unknown["request_id_reserved"] && !(bool)unknown["retry_safe"], "Get reserved or implied safe replay for an unknown ID.");
        var admission = journal.Begin(Id(1), Digest());
        Check(admission.IsNew && admission.Token != null && (string)admission.Snapshot["status"] == "accepted", "New admission failed after invalid requests.");
        JObject policy = Json(admission.Snapshot)["store_policy"] as JObject;
        Check((int)policy["receipt_record_limit"] == 64 && (int)policy["max_receipt_utf8_bytes"] == 262144
            && (int)policy["session_request_id_limit"] == 65536 && (double)policy["completed_retention_seconds"] == 1800,
            "Default logical policy is not exactly bounded.");
        Check(!(bool)policy["durable"] && !(bool)policy["cross_session_exactly_once"] && !(bool)policy["accepted_or_running_evicted"], "Policy overstated durability or allowed active eviction.");
        foreach (int status in new[] { 0, 99, 600, 1000 })
            Throws<ArgumentOutOfRangeException>(() => journal.CompleteBeforeStart(admission.Token, Receipt(), Facts(), status), "Invalid HTTP status claimed completion.");
        Check(journal.TryStart(admission.Token, out Journal.CallbackToken callback), "Invalid completion status consumed admission.");
        foreach (int status in new[] { 99, 600 })
            Throws<ArgumentOutOfRangeException>(() => journal.CompleteStarted(callback, Receipt(), Facts(), status), "Invalid started status claimed completion.");
        Check(journal.CompleteStarted(callback, Receipt(), Facts(), 599), "Valid status boundary was rejected.");
        Console.WriteLine("Copy journal section passed: validation and exact budgets");
    }

    private static void CapabilitiesAndDedup()
    {
        var journal = new Journal(); var other = new Journal();
        var original = journal.Begin(Id(10), Digest());
        foreach (string fingerprint in new[] { Digest(), Digest('b') })
        {
            var duplicate = journal.Begin(Id(10), fingerprint);
            Check(!duplicate.IsNew && duplicate.Token == null && (string)duplicate.Snapshot["request_fingerprint"] == Digest(), "Duplicate acquired a handle or replaced original intent.");
            Check(!journal.TryStart(duplicate.Token, out Journal.CallbackToken missing) && missing == null, "Duplicate started original callback.");
            Check(!journal.CompleteBeforeStart(duplicate.Token, Receipt(99), Facts(), 500), "Duplicate canceled original admission.");
        }
        var foreign = other.Begin(Id(10), Digest('c'));
        foreach (var token in new[] { (Journal.AdmissionToken)null, foreign.Token, new Journal.AdmissionToken(journal, Guid.Parse(Id(10))), new Journal.AdmissionToken(other, Guid.Parse(Id(10))) })
        {
            Check(!journal.TryStart(token, out Journal.CallbackToken invalid) && invalid == null, "Forged or foreign admission started callback.");
            Check(!journal.CompleteBeforeStart(token, Receipt(98), Facts(), 500), "Forged or foreign admission canceled callback.");
        }
        original.Snapshot["request_fingerprint"] = "mutated";
        Check((string)Status(journal, 10)["request_fingerprint"] == Digest(), "Admission snapshot mutated intent storage.");
        Check(journal.TryStart(original.Token, out Journal.CallbackToken callback), "Duplicate attacks consumed original owner's admission.");
        Check(!journal.TryStart(original.Token, out Journal.CallbackToken late) && late == null, "Second callback start succeeded.");
        Check(!journal.CompleteBeforeStart(original.Token, Receipt(90), Facts(), 500), "Before-start completion terminated running callback.");
        Check(other.TryStart(foreign.Token, out Journal.CallbackToken foreignCallback), "Independent session could not admit its own UUID.");
        foreach (var token in new[] { (Journal.CallbackToken)null, foreignCallback, new Journal.CallbackToken(journal, Guid.Parse(Id(10))), new Journal.CallbackToken(other, Guid.Parse(Id(10))) })
            Check(!journal.CompleteStarted(token, Receipt(97), Facts(), 500), "Forged/foreign callback token completed original record.");
        Check(JsonConvert.SerializeObject(original.Token) == "{}" && JsonConvert.SerializeObject(callback) == "{}"
            && JsonConvert.SerializeObject(original) == "{}", "Capabilities leaked through ordinary JSON serialization.");
        string snapshot = Status(journal, 10).ToString(Formatting.None);
        Check(!snapshot.Contains("Token") && !snapshot.Contains("Admission") && !snapshot.Contains("Callback"), "Get exposed a capability.");
        Check(journal.CompleteStarted(callback, Receipt(10), Facts(), 409), "Original callback lost completion authority.");
        Check(!journal.CompleteStarted(callback, Receipt(11), Facts(false), 200), "Late completion overwrote first completion.");
        Check((int)Status(journal, 10)["receipt"]["result"]["marker"] == 10, "Late receipt replaced original record.");
        var canceled = journal.Begin(Id(11), Digest());
        Check(journal.CompleteBeforeStart(canceled.Token, Receipt(12), Facts(false, null, false), 409), "Before-start terminal could not claim accepted admission.");
        Check(!journal.TryStart(canceled.Token, out Journal.CallbackToken canceledCallback) && canceledCallback == null,
            "Late callback started after before-start completion.");
        Check(!journal.CompleteBeforeStart(canceled.Token, Receipt(13), Facts(), 500), "Before-start late completion replaced first terminal.");
        Console.WriteLine("Copy journal section passed: admission and callback capabilities, deduplication");
    }

    private static void ImmutabilityAndCriticalFacts()
    {
        var journal = new Journal(); var admission = journal.Begin(Id(20), Digest());
        var rawFacts = new JObject { ["creation_attempted"] = true, ["native_run_attempted"] = false, ["known_new_refno"] = "=2/42",
            ["outcome_uncertain"] = true, ["native_fault_latched"] = true, ["error_code"] = "native_database_fault", ["retry_safe"] = true,
            ["native_run_returned"] = true, ["native_run_success"] = false, ["known_new_refno_verified"] = true,
            ["readback_complete"] = false, ["source_unchanged_scoped"] = true };
        var facts = Journal.CriticalSummary.From(rawFacts); rawFacts["known_new_refno"] = "=99/99";
        JObject receipt = Receipt(20); receipt["critical_summary"] = new JObject { ["creation_attempted"] = false, ["known_new_refno"] = "=88/88" };
        Check(journal.TryStart(admission.Token, out Journal.CallbackToken callback) && journal.CompleteStarted(callback, receipt, facts, 500), "Receipt completion failed.");
        receipt["result"]["marker"] = 999; receipt["extra"] = "mutation";
        JObject first = Status(journal, 20); JToken summary = first["critical_summary"];
        Check((int)first["receipt"]["result"]["marker"] == 20 && first["receipt"]["extra"] == null, "Original receipt mutation changed storage.");
        Check((string)summary["known_new_refno"] == "=2/42" && (bool)summary["creation_attempted"] && !(bool)summary["native_run_attempted"]
            && (bool)summary["native_fault_latched"] && (bool)summary["outcome_uncertain"] && !(bool)summary["retry_safe"], "Independent typed facts were lost or inferred from envelope.");
        Check((bool)summary["native_run_returned"] && !(bool)summary["native_run_success"]
            && (bool)summary["known_new_refno_verified"] && !(bool)summary["readback_complete"]
            && (bool)summary["source_unchanged_scoped"], "Copy Run return and scoped verification facts were not preserved independently.");
        Check(first["receipt"]["result"]["time"].Type == JTokenType.String, "JSON receipt date strings changed type.");
        first["receipt"]["result"]["marker"] = -1; first["critical_summary"]["known_new_refno"] = "=66/66"; first["store_policy"]["durable"] = true;
        JObject second = Status(journal, 20);
        Check((int)second["receipt"]["result"]["marker"] == 20 && (string)second["critical_summary"]["known_new_refno"] == "=2/42"
            && !(bool)second["store_policy"]["durable"], "Returned snapshot mutated receipt, critical facts, or policy.");
        foreach (JToken bad in new JToken[] { JValue.CreateNull(), new JValue(1), new JValue(0.0), new JValue("true"), new JArray(true), new JObject { ["value"] = true } })
        {
            var values = new JObject();
            foreach (string name in new[] { "creation_attempted", "native_run_attempted", "native_run_returned", "native_run_success",
                "known_new_refno_verified", "readback_complete", "source_unchanged_scoped", "outcome_uncertain", "native_fault_latched" }) values[name] = bad.DeepClone();
            var observed = Json(Journal.CriticalSummary.From(values).Snapshot());
            foreach (string name in new[] { "creation_attempted", "native_run_attempted", "native_run_returned", "native_run_success",
                "known_new_refno_verified", "readback_complete", "source_unchanged_scoped", "outcome_uncertain", "native_fault_latched" })
                Check(Null(observed[name]), "False boolean evidence promoted to a known fact.");
        }
        UnknownFacts(Json(Journal.CriticalSummary.From(null).Snapshot()), "Missing facts must remain unknown");
        UnknownFacts(Json(Journal.CriticalSummary.From(new HostileReceipt()).Snapshot()), "Derived facts must remain unknown");
        foreach (string reference in new[] { "=1/0", "=2/42", "=2147483647/2147483647" })
            Check((string)Json(new Journal.CriticalSummary(knownNewRefno: reference).Snapshot())["known_new_refno"] == reference, "Canonical verified reference was discarded.");
        foreach (string reference in new[] { null, "CE", "/NEW", "=0/1", "=01/1", "=1/01", "=1/-1", "=2147483648/1", "=1/2147483648", " =1/1", "=1/1\n" })
            Check(Null(Json(new Journal.CriticalSummary(knownNewRefno: reference).Snapshot())["known_new_refno"]), "Invalid known REF promoted to canonical identity.");
        foreach (JToken bad in new JToken[] { new JValue(42), new JValue(true), new JArray("=2/42"), new JObject() })
            Check(Null(Json(Journal.CriticalSummary.From(new JObject { ["known_new_refno"] = bad, ["error_code"] = bad.DeepClone() }).Snapshot())["known_new_refno"]), "Non-text reference became an identity.");
        foreach (string error in new[] { null, "", " ", new string('e', 129), "invalid\ud83d" })
            Check(Null(Json(new Journal.CriticalSummary(errorCode: error).Snapshot())["error_code"]), "Invalid error code was retained.");
        Check((string)Json(new Journal.CriticalSummary(errorCode: new string('e', 128)).Snapshot())["error_code"] == new string('e', 128), "Bounded error code was lost.");
        var unknownAdmission = journal.Begin(Id(21), Digest());
        Check(journal.CompleteBeforeStart(unknownAdmission.Token, null, null, 400), "Missing receipt completion failed.");
        JObject missing = Status(journal, 21);
        Check((string)missing["receipt_omitted_reason"] == "receipt_missing" && (string)missing["status"] == "completed", "Missing receipt is not an explicit terminal omission.");
        UnknownFacts(missing["critical_summary"], "Missing facts were manufactured");
        Console.WriteLine("Copy journal section passed: immutable receipts and strict critical facts");
    }

    private static void OmissionAndPureJson()
    {
        int id = 30;
        foreach (bool unicode in new[] { false, true })
            foreach (bool over in new[] { false, true })
            {
                var journal = new Journal(maxReceiptUtf8Bytes: 32);
                var admission = journal.Begin(Id(id++), Digest());
                var receipt = new JObject { ["x"] = new string(unicode ? '中' : 'x', (unicode ? 8 : 24) + (over ? 1 : 0)) };
                Check(journal.CompleteBeforeStart(admission.Token, receipt, Facts(), 500), "Bounded receipt completion failed.");
                JObject status = Json(journal.Get(admission.Token.Id.ToString("D")));
                Check((bool)status["receipt_available"] == !over && (over ? (string)status["receipt_omitted_reason"] == "receipt_too_large" : Null(status["receipt_omitted_reason"])), "UTF8 logical boundary is not exact.");
                Check((string)status["critical_summary"]["known_new_refno"] == "=2/42" && (bool)status["critical_summary"]["native_fault_latched"], "Receipt size omission lost critical stage evidence.");
            }
        foreach (bool over in new[] { false, true })
        {
            var journal = new Journal(); var admission = journal.Begin(Id(id++), Digest());
            Check(journal.CompleteBeforeStart(admission.Token, new JObject { ["x"] = new string('x', 262136 + (over ? 1 : 0)) }, Facts(), 500), "Maximum receipt completion failed.");
            JObject status = Json(journal.Get(admission.Token.Id.ToString("D")));
            Check((bool)status["receipt_available"] == !over, "256 KiB boundary was not exact.");
            if (!over) Check(Encoding.UTF8.GetByteCount(status["receipt"].ToString(Formatting.None)) == 262144, "Retained maximum receipt differs from logical budget.");
        }
        JObject deep = new JObject(); JObject current = deep;
        for (int index = 0; index < 65; index++) { var next = new JObject(); current["next"] = next; current = next; }
        foreach (JObject invalid in new JObject[]
        {
            new HostileReceipt(), new JObject { ["nested"] = new HostileReceipt() }, new JObject { ["raw"] = new JRaw("not-json") },
            new JObject { ["date"] = new JValue(DateTime.UtcNow) }, new JObject { ["nan"] = double.NaN },
            new JObject { ["infinity"] = double.PositiveInfinity }, new JObject { ["undefined"] = JValue.CreateUndefined() },
            new JObject { ["bytes"] = new JValue(new byte[] { 1, 2 }) }, new JObject { ["text"] = "bad\ud83d" }, deep
        })
        {
            var journal = new Journal(); var admission = journal.Begin(Id(id++), Digest());
            Check(journal.CompleteBeforeStart(admission.Token, invalid, Facts(), 500), "Invalid pure-data receipt blocked terminal critical facts.");
            JObject status = Json(journal.Get(admission.Token.Id.ToString("D")));
            Check(!(bool)status["receipt_available"] && (string)status["receipt_omitted_reason"] == "receipt_not_pure_json"
                && (string)status["critical_summary"]["known_new_refno"] == "=2/42", "Invalid receipt was serialized or erased critical facts.");
        }
        Check(hostileWrites == 0, "Caller-defined serializer executed; possible SDK proxy access.");
        Console.WriteLine("Copy journal section passed: 256 KiB and UTF8 omissions, pure JSON serialization");
    }

    private static void Concurrency()
    {
        var journal = new Journal(); var admissions = new Journal.AdmissionResult[64];
        Parallel.For(0, admissions.Length, index => admissions[index] = journal.Begin(Id(100), Digest(index % 2 == 0 ? 'a' : 'b')));
        Check(admissions.Count(value => value.IsNew && value.Token != null) == 1 && admissions.Count(value => value.Token != null) == 1, "Concurrent duplicate admission minted multiple owners.");
        Journal.AdmissionResult owner = admissions.Single(value => value.IsNew);
        string originalFingerprint = (string)owner.Snapshot["request_fingerprint"];
        var callbacks = new Journal.CallbackToken[64]; int starts = 0;
        Parallel.For(0, callbacks.Length, index => { if (journal.TryStart(owner.Token, out callbacks[index])) Interlocked.Increment(ref starts); });
        Check(starts == 1 && callbacks.Count(value => value != null) == 1, "Concurrent TryStart minted multiple callback owners.");
        Journal.CallbackToken callback = callbacks.Single(value => value != null); int completions = 0;
        Parallel.For(0, 64, index => { if (journal.CompleteStarted(callback, Receipt(index), Facts(), 500)) Interlocked.Increment(ref completions); });
        JObject final = Status(journal, 100);
        Check(completions == 1 && (string)final["request_fingerprint"] == originalFingerprint && (string)final["status"] == "completed", "Concurrent completions overwrote intent or won more than once.");
        for (int round = 0; round < 100; round++)
        {
            var race = new Journal(); var admission = race.Begin(Id(200 + round), Digest());
            bool started = false, before = false; Journal.CallbackToken token = null;
            using (var go = new ManualResetEventSlim(false))
            {
                Task start = Task.Run(() => { go.Wait(); started = race.TryStart(admission.Token, out token); });
                Task cancel = Task.Run(() => { go.Wait(); before = race.CompleteBeforeStart(admission.Token, Receipt(1), Facts(false, null, false), 409); });
                go.Set(); Task.WaitAll(start, cancel);
            }
            Check(started != before && (started ? token != null : token == null), "Start-versus-before-start race did not elect exactly one path.");
            if (started)
            {
                Check((string)Status(race, 200 + round)["status"] == "running", "HTTP cancellation made a started callback terminal.");
                Check(race.CompleteStarted(token, Receipt(2), Facts(), 500), "Real callback lost ownership after cancellation race.");
            }
            Check(!race.TryStart(admission.Token, out Journal.CallbackToken late) && late == null, "Late callback replay started after race settled.");
        }
        var queried = new Journal(); var accepted = queried.Begin(Id(400), Digest()); queried.TryStart(accepted.Token, out Journal.CallbackToken running);
        int observed = 0;
        Task[] readers = Enumerable.Range(0, 8).Select(_ => Task.Run(() =>
        {
            for (int index = 0; index < 100; index++)
            {
                JObject status = Status(queried, 400); string state = (string)status["status"];
                Check(state == "running" || state == "completed", "Concurrent Get returned an impossible state.");
                if (state == "running") Check(Null(status["completed_utc"]) && Null(status["receipt"]), "Concurrent Get exposed partial commit.");
                else Check(!Null(status["completed_utc"]) && (string)status["critical_summary"]["known_new_refno"] == "=2/42", "Concurrent Get lost committed critical facts.");
                Interlocked.Increment(ref observed);
            }
        })).ToArray();
        Check(queried.CompleteStarted(running, Receipt(400), Facts(), 500), "Concurrent read completion failed."); Task.WaitAll(readers);
        Check(observed == 800, "Concurrent status reads did not finish.");
        Console.WriteLine("Copy journal section passed: concurrent dedup, first completion, start/cancel races, status reads");
    }

    private static void TimeoutCapacityAndExpiry()
    {
        DateTimeOffset now = new DateTimeOffset(2026, 11, 22, 0, 0, 0, TimeSpan.Zero);
        var journal = new Journal(() => now, capacity: 1);
        var admission = journal.Begin(Id(500), Digest()); Check(journal.TryStart(admission.Token, out Journal.CallbackToken callback), "Running setup failed.");
        now += TimeSpan.FromDays(365); JObject running = Status(journal, 500);
        Check((string)running["status"] == "running" && Null(running["completed_utc"]) && !(bool)running["retry_safe"], "HTTP wait/clock expiry terminated or made running work replay-safe.");
        UnknownFacts(running["critical_summary"], "Running state inferred native execution facts");
        Check(!journal.CompleteBeforeStart(admission.Token, Receipt(), Facts(false), 504), "HTTP timeout canceled running callback.");
        Throws<Journal.CapacityException>(() => journal.Begin(Id(501), Digest()), "Running record was evicted to admit another request.");
        Check(!(bool)Status(journal, 501)["request_id_reserved"], "Failed admission reserved a new ID.");
        Check(journal.CompleteStarted(callback, Receipt(), Facts(), 500), "Late real callback could not complete after HTTP timeout.");
        Check(journal.Begin(Id(501), Digest()).IsNew && (string)Status(journal, 500)["status"] == "unknown_or_expired"
            && (bool)Status(journal, 500)["request_id_reserved"], "Completed eviction released its tombstone or occupied slot.");
        var waiting = new Journal(() => now, capacity: 1); var waitingAdmission = waiting.Begin(Id(502), Digest()); now += TimeSpan.FromDays(365);
        Check((string)Status(waiting, 502)["status"] == "accepted", "Accepted work expired before callback ownership was decided.");
        Throws<Journal.CapacityException>(() => waiting.Begin(Id(503), Digest()), "Accepted record was evicted.");
        Check(waiting.CompleteBeforeStart(waitingAdmission.Token, null, null, 409), "Owner could not finish accepted work after long wait.");
        now = new DateTimeOffset(2026, 11, 22, 0, 0, 0, TimeSpan.Zero);
        var expiry = new Journal(() => now); var expiring = expiry.Begin(Id(510), Digest()); now += TimeSpan.FromMinutes(20);
        Check(expiry.CompleteBeforeStart(expiring.Token, Receipt(), Facts(), 409), "Expiry setup failed.");
        now -= TimeSpan.FromHours(1); Check((string)Status(expiry, 510)["status"] == "completed", "Clock rollback expired a receipt.");
        now = new DateTimeOffset(2026, 11, 22, 0, 49, 59, TimeSpan.Zero);
        Check((string)Status(expiry, 510)["status"] == "completed", "Retention was measured from admission instead of completion.");
        now += TimeSpan.FromSeconds(1);
        JObject expired = Status(expiry, 510);
        Check((string)expired["status"] == "unknown_or_expired" && (bool)expired["request_id_reserved"] && !(bool)expired["retry_safe"], "Exact expiry lost reservation or implied no execution.");
        var duplicate = expiry.Begin(Id(510), Digest('b'));
        Check(!duplicate.IsNew && duplicate.Token == null && (string)duplicate.Snapshot["status"] == "unknown_or_expired", "Expired request ID was executable again.");
        Check(!expiry.TryStart(expiring.Token, out Journal.CallbackToken stale) && stale == null
            && !expiry.CompleteBeforeStart(expiring.Token, Receipt(), Facts(), 200), "Expired capability regained authority.");
        var order = new Journal(() => now, capacity: 2); var a = order.Begin(Id(520), Digest()); var b = order.Begin(Id(521), Digest());
        order.CompleteBeforeStart(b.Token, Receipt(), Facts(), 200); now += TimeSpan.FromSeconds(1); order.CompleteBeforeStart(a.Token, Receipt(), Facts(), 200);
        order.Begin(Id(522), Digest());
        Check((bool)Status(order, 520)["available"] && !(bool)Status(order, 521)["available"], "Eviction did not choose earliest completed record.");
        var tied = new Journal(() => now, capacity: 2); var first = tied.Begin(Id(530), Digest()); var second = tied.Begin(Id(531), Digest());
        tied.CompleteBeforeStart(second.Token, null, null, 409); tied.CompleteBeforeStart(first.Token, null, null, 409); tied.Begin(Id(532), Digest());
        Check(!(bool)Status(tied, 530)["available"] && (bool)Status(tied, 531)["available"], "Completion-time tie did not use stable admission sequence.");
        var full = new Journal(() => now); var all = new List<Journal.AdmissionResult>();
        for (int index = 0; index < 64; index++) all.Add(full.Begin(Id(600 + index), Digest()));
        Throws<Journal.CapacityException>(() => full.Begin(Id(664), Digest()), "65th active record exceeded default capacity.");
        Check(all.All(value => (bool)Json(full.Get(value.Token.Id.ToString("D")))["available"]), "Full capacity admission evicted accepted work.");
        Console.WriteLine("Copy journal section passed: running timeout, active capacity, completion expiry and eviction");
    }

    private static void Tombstones()
    {
        DateTimeOffset now = DateTimeOffset.UtcNow;
        var journal = new Journal(() => now, capacity: 1, maxReceiptUtf8Bytes: 32);
        for (int index = 1; index <= 65536; index++)
        {
            var admission = journal.Begin(Id(100000 + index), Digest());
            Check(admission.IsNew && journal.CompleteBeforeStart(admission.Token, null, null, 409), "Tombstone-cap admission/completion failed early.");
        }
        Throws<Journal.CapacityException>(() => journal.Begin(Id(200000), Digest()), "65537th remembered ID did not fail closed.");
        Check(!(bool)Status(journal, 200000)["request_id_reserved"], "Tombstone overflow reserved an unaccepted ID.");
        now += TimeSpan.FromDays(1);
        var duplicate = journal.Begin(Id(100001), Digest('b'));
        Check(!duplicate.IsNew && duplicate.Token == null && (bool)Json(duplicate.Snapshot)["request_id_reserved"], "Expired first tombstone was released at cap.");
        Throws<Journal.CapacityException>(() => journal.Begin(Id(200001), Digest()), "Expiry reopened session tombstone capacity.");
        var freshSession = new Journal();
        Check(freshSession.Begin(Id(100001), Digest('c')).IsNew, "Independent session was incorrectly given cross-session deduplication semantics.");
        Console.WriteLine("Copy journal section passed: full 65536-ID session tombstone boundary");
    }

    private static int Main()
    {
        try
        {
            Validation(); CapabilitiesAndDedup(); ImmutabilityAndCriticalFacts(); OmissionAndPureJson();
            Concurrency(); TimeoutCapacityAndExpiry(); Tombstones();
            Console.WriteLine("PARAGON component copy journal production checks passed: " + assertions + "; pure managed, no SDK/PML, hostile serializers invoked=" + hostileWrites + ".");
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
