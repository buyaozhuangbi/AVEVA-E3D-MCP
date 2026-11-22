using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using Newtonsoft.Json;

// Actual production validation/serialization/atomic-write/read methods are
// inserted by the Python runner. No E3D SDK assemblies or application loaded.
internal static class EmptyDrawListPersistenceHarness
{
    private const int DisplayStateMemberLimit = 10000, DisplayStatePrimitiveLimit = 20000;
    private static int checks;
    private static void ThrowIfNativeFaulted() { }
    private sealed class DrawListVisualEntry
    {
        public string refno;
        public int colour, transparency;
        public bool edges, visibility, shallow;
    }
    private sealed class DrawListPrimitiveVisualEntry
    {
        public string owner_refno, refno;
        public int colour, transparency;
        public bool edges, visibility, shallow;
    }
    private sealed class DrawListVisualDocument
    {
        public int schema_version;
        public string project, mdb;
        public List<DrawListVisualEntry> members = new List<DrawListVisualEntry>();
        public List<DrawListPrimitiveVisualEntry> primitive_entries;
    }
    private static void Check(bool condition, string name)
    { checks++; if (!condition) throw new Exception(name); }
    private static void Rejected(DrawListVisualDocument document, string name)
    {
        try { ValidateDrawListVisualDocument(document); }
        catch (InvalidOperationException) { Check(true, name); return; }
        throw new Exception("Expected document rejection: " + name);
    }
    private static DrawListVisualDocument Empty()
    { return new DrawListVisualDocument { schema_version = 2, project = "APS", mdb = "ALL", primitive_entries = new List<DrawListPrimitiveVisualEntry>() }; }
    private static DrawListVisualDocument Full()
    {
        var result = Empty();
        result.members.Add(new DrawListVisualEntry { refno = "=1/1", colour = 326, visibility = true });
        result.primitive_entries.Add(new DrawListPrimitiveVisualEntry { owner_refno = "=1/1", refno = "=1/2", visibility = true });
        return result;
    }
    private static Dictionary<string, object> Result(string root)
    {
        return new Dictionary<string, object> { ["path"] = Path.Combine(root, "view.json"),
            ["legacy_export"] = new Dictionary<string, object> { ["path"] = Path.Combine(root, "view"),
                ["attempted"] = false, ["succeeded"] = null, ["status"] = "not_attempted", ["error"] = null } };
    }
    private static int Main()
    {
        string root = Path.Combine(Path.GetTempPath(), "empty-drawlist-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            var empty = Empty(); ValidateDrawListVisualDocument(empty); Check(true, "schema-2 empty document valid");
            var invalid = Empty(); invalid.schema_version = 1; Rejected(invalid, "schema1 empty invalid");
            invalid = Empty(); invalid.schema_version = 3; Rejected(invalid, "unknown schema invalid");
            invalid = Empty(); invalid.project = " "; Rejected(invalid, "project required even when empty");
            invalid = Empty(); invalid.mdb = null; Rejected(invalid, "MDB required even when empty");
            invalid = Empty(); invalid.members = null; Rejected(invalid, "null member collection not empty observation");
            invalid = Empty(); invalid.primitive_entries = null; Rejected(invalid, "null primitive collection not empty observation");
            invalid = Empty(); invalid.primitive_entries.Add(new DrawListPrimitiveVisualEntry { owner_refno = "=1/1", refno = "=1/2" });
            Rejected(invalid, "orphan primitive cannot accompany zero members");
            invalid = Empty(); invalid.primitive_entries.Add(null); Rejected(invalid, "null primitive cannot accompany zero members");

            string json = Path.Combine(root, "view.json"), legacy = Path.Combine(root, "view");
            var initial = PersistDrawListVisualDocument(Result(root), Full());
            Check(Equals(initial["succeeded"], true), "baseline nonempty persisted");
            string priorJson = File.ReadAllText(json), priorLegacy = File.ReadAllText(legacy);
            var defaultEmpty = PersistDrawListVisualDocument(Result(root), Empty());
            Check(Equals(defaultEmpty["succeeded"], false) && Equals(defaultEmpty["status"], "empty_drawlist"), "default writer still refuses empty");
            Check(File.ReadAllText(json) == priorJson && File.ReadAllText(legacy) == priorLegacy, "default empty does not replace either prior file");
            Check(Equals(((Dictionary<string, object>)defaultEmpty["legacy_export"])["attempted"], false), "default refusal never writes legacy");

            var saved = PersistDrawListVisualDocument(Result(root), Empty(), true);
            Check(Equals(saved["succeeded"], true) && Equals(saved["status"], "saved") && Equals(saved["schema_version"], 2), "explicit empty save succeeds");
            Check(Equals(saved["primitive_entry_count"], 0), "explicit save reports zero primitives");
            var document = JsonConvert.DeserializeObject<DrawListVisualDocument>(File.ReadAllText(json));
            Check(document.schema_version == 2 && document.project == "APS" && document.mdb == "ALL" && document.members.Count == 0 && document.primitive_entries.Count == 0,
                "committed JSON is complete empty schema2 rather than stale prior members");
            Check(File.ReadAllText(legacy) == Environment.NewLine, "legacy export has no stale member reference");
            List<string> refs; DrawListVisualDocument read;
            ReadDrawListState(legacy, out refs, out read);
            Check(refs.Count == 0 && read != null && read.members.Count == 0 && read.primitive_entries.Count == 0, "production reader accepts empty authoritative JSON");
            File.WriteAllText(legacy, "=1/999" + Environment.NewLine);
            ReadDrawListState(legacy, out refs, out read);
            Check(refs.Count == 0, "empty authoritative JSON must not fall back to stale legacy references");
            string emptyJson = File.ReadAllText(json);
            using (var locked = File.Open(json, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
            {
                var failed = PersistDrawListVisualDocument(Result(root), Full(), true);
                Check(Equals(failed["succeeded"], false) && Equals(failed["status"], "write_failed"), "locked authoritative target reports failure");
                Check(Equals(((Dictionary<string, object>)failed["legacy_export"])["attempted"], false), "failed authoritative commit does not write legacy");
            }
            Check(File.ReadAllText(json) == emptyJson, "failed atomic commit retains old JSON");
            using (var locked = File.Open(legacy, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
            {
                var partial = PersistDrawListVisualDocument(Result(root), Empty(), true);
                var legacyResult = (Dictionary<string, object>)partial["legacy_export"];
                Check(Equals(partial["succeeded"], true) && Equals(legacyResult["succeeded"], false), "legacy failure does not undo authoritative empty success");
            }
            ReadDrawListState(legacy, out refs, out read);
            Check(refs.Count == 0 && read.members.Count == 0, "reader still sees empty JSON after legacy failure");
            invalid = Empty(); invalid.primitive_entries.Add(new DrawListPrimitiveVisualEntry { owner_refno = "=1/1", refno = "=1/2" });
            var bad = PersistDrawListVisualDocument(Result(root), invalid, true);
            Check(Equals(bad["succeeded"], false) && Equals(bad["status"], "snapshot_failed"), "opt-in cannot bypass orphan validation");
            Check(File.ReadAllText(json) == emptyJson, "invalid opt-in never changes authoritative JSON");
            invalid = Empty(); invalid.schema_version = 1;
            bad = PersistDrawListVisualDocument(Result(root), invalid, true);
            Check(Equals(bad["succeeded"], false), "opt-in does not permit legacy empty schema1");
            Check(Directory.GetFiles(root, "*.tmp").Length == 0, "atomic-write success/failure leaves no temporary files");
            Console.WriteLine("Empty Drawlist persistence checks passed: " + checks);
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
        finally { Directory.Delete(root, true); }
    }

    // PRODUCTION_METHODS
}
