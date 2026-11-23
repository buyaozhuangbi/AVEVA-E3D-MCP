using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Aveva.Core.Database;
using Aveva.Core.Utilities.ModuleInfo;
using Aveva.Core.Utilities.Messaging;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using PmlCommand = Aveva.Core.Utilities.CommandLine.Command;

namespace E3d21AiBridge;

public sealed partial class E3d21Context : IE3d21ParagonComponentCopyContext
{
    private const int ParagonCopyMaximumMembers = 100;
    private readonly object paragonCopyJournalLock = new object();
    private string paragonCopySession;
    private ParagonComponentCopyJournal paragonCopyJournal;
    // CWT uses CLR identity, not SDK equality or GetHashCode. Values never hold
    // their weak keys. Only opaque managed tags leave the active UI scope.
    private sealed class ParagonCopyInstanceTag { internal readonly string Value = Guid.NewGuid().ToString("D"); }
    private readonly ConditionalWeakTable<object, ParagonCopyInstanceTag> paragonCopyInstances =
        new ConditionalWeakTable<object, ParagonCopyInstanceTag>();

    private sealed class ParagonCopyConflict : InvalidOperationException
    {
        internal readonly string Code;
        internal ParagonCopyConflict(string code, string message) : base(message) { Code = code; }
    }

    private sealed class ParagonCopyCapture
    {
        internal Project Project;
        internal MDB Mdb;
        internal DbElement Source, Parent;
        internal Dictionary<string, object> Evidence;
        internal List<Dictionary<string, object>> Members;
        internal Dictionary<string, object> Template;
    }

    public object GetParagonComponentCopyStatus(string requestId, string expectedSessionId, string sessionId)
    {
        // Status deliberately has no busy gate, dispatch, SDK read or fault
        // probe. A running/modal callback cannot prevent this managed query.
        if (string.IsNullOrWhiteSpace(sessionId) || sessionId.Length > 256 || expectedSessionId != sessionId)
            throw new ArgumentException("The expected bridge session does not match the current server session.");
        return ParagonCopyJournalFor(sessionId).Get(requestId);
    }

    private ParagonComponentCopyJournal ParagonCopyJournalFor(string sessionId)
    {
        lock (paragonCopyJournalLock)
        {
            if (paragonCopySession != null && paragonCopySession != sessionId)
                throw new InvalidOperationException("A component-copy context cannot be reused by a different bridge session.");
            if (paragonCopyJournal == null)
            {
                paragonCopySession = sessionId;
                paragonCopyJournal = new ParagonComponentCopyJournal();
            }
            return paragonCopyJournal;
        }
    }

    public ParagonComponentCopyReply CopyParagonComponent(string sourceRefno, string parentRefno,
        string name, string recipe, bool dryRun, string requestId, string expectedSessionId,
        string expectedBridgeBuild, string expectedRevision, string sessionId)
    {
        var request = ParagonComponentCopyRequest.Validate(sourceRefno, parentRefno, name, recipe, dryRun,
            requestId, expectedSessionId, expectedBridgeBuild, expectedRevision);
        if (string.IsNullOrWhiteSpace(sessionId) || sessionId.Length > 256)
            throw new ArgumentException("A server-owned bridge session identity is required.", nameof(sessionId));
        string build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
        var result = ParagonCopyResult(request, sessionId, build);
        var progress = (Dictionary<string, object>)result["progress"];
        var errors = (List<Dictionary<string, object>>)result["errors"];
        var journal = ParagonCopyJournalFor(sessionId);
        ParagonComponentCopyJournal.AdmissionToken admission = null;
        ParagonComponentCopyJournal.CallbackToken callback = null;
        bool began = false;
        int httpStatus = 200;
        bool ok = true;
        string failure = null;
        int invocationState = 0; // 0 queued, 1 owns callback, 3 cancelled before start.
        var completed = new TaskCompletionSource<bool>();
        try
        {
            if (!request.DryRun)
            {
                if (request.ExpectedSessionId != sessionId || request.ExpectedBridgeBuild != build)
                    throw new ParagonCopyConflict("bridge_identity_conflict", "The expected bridge session/build changed; no request was admitted.");
                string fingerprint = ParagonCopyHash(JObject.FromObject(new
                {
                    source_refno = request.SourceRefno, parent_refno = request.ParentRefno, name = request.Name,
                    recipe = request.Recipe, session_id = sessionId, bridge_build = build,
                    expected_revision = request.ExpectedRevision
                }));
                var accepted = journal.Begin(request.RequestId, fingerprint);
                if (!accepted.IsNew)
                {
                    result["status"] = "duplicate"; result["journal"] = accepted.Snapshot;
                    ParagonCopyError(errors, "admission", "duplicate_request_id", "The request ID was already reserved; this submission did not execute.");
                    return new ParagonComponentCopyReply(409, false, result, "The request ID was already reserved.");
                }
                admission = accepted.Token;
            }
            if (!TryBeginCommand("PARAGON component copy"))
                throw new ParagonCopyConflict("busy", "Another E3D operation owns the command gate.");
            began = true;
            try
            {
                OnUi(delegate
                {
                    if (Interlocked.CompareExchange(ref invocationState, 1, 0) != 0) return false;
                    Exception callbackFailure = null;
                    try
                    {
                        if (!request.DryRun && !journal.TryStart(admission, out callback))
                            throw new InvalidOperationException("The admitted component-copy callback lost ownership before it started.");
                        ThrowIfNativeFaulted();
                        var before = ParagonCopyBefore(); result["before"] = before;
                        var captured = CaptureParagonCopy(request, sessionId, build, before);
                        string revision = ParagonCopyHash(JObject.FromObject(new
                        {
                            recipe = request.Recipe, source_refno = request.SourceRefno,
                            parent_refno = request.ParentRefno, name = request.Name,
                            session_id = sessionId, bridge_build = build, before
                        }));
                        result["scoped_revision"] = revision;
                        if (request.DryRun)
                        {
                            result["status"] = "prepared"; result["success"] = true; result["complete"] = true;
                            return true;
                        }
                        if (revision != request.ExpectedRevision)
                            throw new ParagonCopyConflict("revision_conflict", "The scoped native observations changed since preparation; no mutating stage was entered.");
                        RunParagonCopy(request, progress, errors);
                        if (Equals(progress["native_run_attempted"], true))
                        {
                            var after = ParagonCopyAfter(); result["after"] = after;
                            ReadBackParagonCopy(request, captured, sessionId, build, after, progress, errors);
                        }
                        bool success = Equals(progress["native_run_returned"], true) && Equals(progress["native_run_success"], true)
                            && Equals(progress["known_new_refno_verified"], true) && Equals(progress["readback_complete"], true)
                            && Equals(progress["source_unchanged_scoped"], true) && Equals(progress["copied_parameters_equal"], true)
                            && Equals(progress["copied_references_equal"], true) && errors.Count == 0;
                        result["success"] = success; result["complete"] = success;
                        result["status"] = success ? "executed" : "failed";
                        progress["outcome_uncertain"] = !success && Equals(progress["creation_attempted"], true);
                        if (!success) throw new InvalidOperationException("The native copy or its required scoped readback did not complete successfully.");
                        return true;
                    }
                    catch (Exception error) { callbackFailure = error; throw; }
                    finally
                    {
                        if (callbackFailure == null) completed.TrySetResult(true);
                        else completed.TrySetException(callbackFailure);
                    }
                });
            }
            finally
            {
                // A dispatcher may throw/return after queueing or starting the
                // callback. Only a not-started callback may be cancelled.
                if (Interlocked.CompareExchange(ref invocationState, 3, 0) == 1)
                    completed.Task.GetAwaiter().GetResult();
            }
            if (Volatile.Read(ref invocationState) == 3)
                throw new InvalidOperationException("The dispatcher returned before the copy callback began; that callback was cancelled without execution.");
        }
        catch (ParagonCopyConflict error)
        {
            failure = error.Message;
            result["status"] = request.DryRun ? "blocked" : "rejected";
            httpStatus = request.DryRun ? 200 : 409; ok = request.DryRun;
            ParagonCopyError(errors, "preflight", error.Code, error.Message);
        }
        catch (Exception error)
        {
            failure = ParagonComponentTemplateRequest.ErrorText(error.Message);
            result["status"] = "failed"; httpStatus = 500; ok = false;
            bool fault = error is NativeDatabaseFaultException || nativeFaultState != null && nativeFaultState.HasFault;
            progress["native_fault_latched"] = fault;
            progress["outcome_uncertain"] = Equals(progress["creation_attempted"], true);
            ParagonCopyError(errors, "execution", fault ? "native_database_fault" : "copy_failed", failure);
        }
        finally
        {
            // All facts below are managed. Do not probe CE, diagnostics or SDK
            // handles after either a native fault or a dispatcher failure.
            result["this_submission_executed"] = Equals(progress["native_run_attempted"], true);
            if (!Equals(result["status"], "prepared") && !Equals(result["status"], "executed"))
            { result["success"] = false; result["complete"] = false; }
            if (began) EndCommand();
        }
        bool latched = Equals(progress["native_fault_latched"], true);
        if (admission != null)
        {
            var envelope = JObject.FromObject(new { ok, version = "E3D21", session_id = sessionId, bridge_build = build,
                result, error = failure, error_code = latched ? "native_database_fault" : null,
                native_fault_latched = latched, retry_safe = false });
            var facts = JObject.FromObject(progress);
            facts["error_code"] = errors.Count == 0 ? null : errors[errors.Count - 1]["code"] as string;
            var summary = ParagonComponentCopyJournal.CriticalSummary.From(facts);
            if (callback != null) journal.CompleteStarted(callback, envelope, summary, httpStatus);
            else journal.CompleteBeforeStart(admission, envelope, summary, httpStatus);
            result["journal"] = journal.Get(request.RequestId);
        }
        return new ParagonComponentCopyReply(httpStatus, ok, result, failure, latched);
    }

    private static Dictionary<string, object> ParagonCopyResult(ParagonComponentCopyRequest request, string session, string build) =>
        new Dictionary<string, object>
        {
            ["operation"] = "paragon_component_copy", ["recipe"] = request.Recipe, ["dry_run"] = request.DryRun,
            ["request"] = new { source_refno = request.SourceRefno, parent_refno = request.ParentRefno, name = request.Name, recipe = request.Recipe },
            ["session_id"] = session, ["bridge_build"] = build, ["request_id"] = request.RequestId,
            ["status"] = "blocked", ["success"] = false, ["complete"] = false, ["scoped_revision"] = null,
            ["before"] = null, ["after"] = null, ["journal"] = null,
            ["this_submission_executed"] = false, ["request_replayed"] = false,
            ["errors"] = new List<Dictionary<string, object>>(),
            ["progress"] = new Dictionary<string, object>
            {
                ["creation_attempted"] = false, ["native_run_attempted"] = false, ["native_run_returned"] = false,
                ["native_run_success"] = null, ["known_new_refno"] = null, ["known_new_refno_verified"] = null,
                ["readback_complete"] = false, ["source_unchanged_scoped"] = null,
                ["copied_parameters_equal"] = null, ["copied_references_equal"] = null,
                ["outcome_uncertain"] = false, ["native_fault_latched"] = false, ["retry_safe"] = false
            },
            ["scope"] = new
            {
                kind = "same_category_scom_template_copy", backend = "server_generated_fixed_pml",
                parameter_comparison = "complete_finite_numeric_PARAM_only", reference_comparison = "observed_supported_fixed_seven_only",
                all_references_covered = false, whole_clone_fingerprint = false, geometry_observed = false,
                geometry_equivalence_verified = false, word_parameters_observed = false, hidden_attributes_observed = false,
                engineering_validated = (bool?)null, write_permission_verified = (bool?)null, savework_performed = false,
                automatic_ce_restore = false, atomic_snapshot = false, scoped_revision_is_lock = false, whole_project_coverage = false
            }
        };

    private static Dictionary<string, object> ParagonCopyBefore() => new Dictionary<string, object>
    {
        ["module"] = null, ["context"] = null, ["source_template"] = null, ["parent"] = null,
        ["source_owner"] = null, ["members"] = new List<Dictionary<string, object>>(), ["members_complete"] = false,
        ["name_lookup"] = new Dictionary<string, object> { ["status"] = "unknown", ["target"] = null }, ["creatable"] = null
    };

    private static Dictionary<string, object> ParagonCopyAfter() => new Dictionary<string, object>
    {
        ["module"] = null, ["context"] = null, ["source_template"] = null, ["new_template"] = null,
        ["parent"] = null, ["new_owner"] = null, ["members"] = new List<Dictionary<string, object>>(), ["members_complete"] = false,
        ["verification"] = new Dictionary<string, object>
        { ["new_identity"] = null, ["parent_append"] = null, ["source_unchanged_scoped"] = null,
            ["copied_parameters_equal"] = null, ["copied_references_equal"] = null }
    };

    private static void ParagonCopyError(List<Dictionary<string, object>> errors, string stage, string code, string message) =>
        errors.Add(new Dictionary<string, object> { ["stage"] = stage, ["code"] = code, ["message"] = ParagonComponentTemplateRequest.ErrorText(message) });

    private static void ParagonCopyRequire(bool condition, string code, string message)
    { if (!condition) throw new ParagonCopyConflict(code, message); }

    private Dictionary<string, object> ParagonCopyContext(string session, string build, out Project project, out MDB mdb)
    {
        var raw = ComponentTemplateBoundary(session, build, out project, out mdb);
        string binding = ReferenceEquals(project, null) || ReferenceEquals(mdb, null) ? null : ParagonCopyHash(new JValue(
            paragonCopyInstances.GetValue(project, _ => new ParagonCopyInstanceTag()).Value + "/" +
            paragonCopyInstances.GetValue(mdb, _ => new ParagonCopyInstanceTag()).Value));
        return new Dictionary<string, object> { ["session_id"] = session, ["bridge_build"] = build,
            ["project"] = ParagonCopyContextText(raw["project"]) ? raw["project"] : null,
            ["mdb"] = ParagonCopyContextText(raw["mdb"]) ? raw["mdb"] : null, ["current_element"] = raw["current_element"],
            ["instance_binding"] = binding };
    }

    private static bool ParagonCopyContextText(object value)
    {
        string text = value as string;
        if (string.IsNullOrWhiteSpace(text) || text.Length > 256) return false;
        for (int index = 0; index < text.Length; index++)
        {
            var category = CharUnicodeInfo.GetUnicodeCategory(text, index);
            if (category == UnicodeCategory.Control || category == UnicodeCategory.Format || category == UnicodeCategory.PrivateUse
                || category == UnicodeCategory.OtherNotAssigned || category == UnicodeCategory.LineSeparator
                || category == UnicodeCategory.ParagraphSeparator || category == UnicodeCategory.SpaceSeparator && text[index] != ' ') return false;
            if (char.IsHighSurrogate(text[index])) { if (++index == text.Length || !char.IsLowSurrogate(text[index])) return false; }
            else if (char.IsLowSurrogate(text[index])) return false;
        }
        return true;
    }

    private static Dictionary<string, object> ParagonCopyModule()
    {
        var manager = NativeRead(() => new ModuleManager());
        var module = NativeRead(() => manager.CurrentModule);
        if (ReferenceEquals(module, null)) throw new InvalidOperationException("No native module identity was available.");
        string name = NativeRead(() => module.Name);
        int number = NativeRead(() => module.Number);
        return new Dictionary<string, object> { ["name"] = name, ["number"] = number };
    }

    private static void ParagonCopyRequireModule(Dictionary<string, object> module) => ParagonCopyRequire(
        Equals(module["name"], "PARAGON") && Equals(module["number"], 81), "not_paragon_81", "The native module must be exactly PARAGON/81.");

    private static bool ParagonCopyTemplateContextMatches(Dictionary<string, object> template, Dictionary<string, object> context)
    {
        var boundaries = JObject.FromObject(template["context"]);
        if (boundaries.Value<bool?>("boundary_agreement") != true) return false;
        var expected = JObject.FromObject(context);
        foreach (string side in new[] { "before", "after" })
        {
            var observed = boundaries[side] as JObject;
            if (observed == null || !(observed["errors"] is JObject errors) || errors.HasValues) return false;
            foreach (string key in new[] { "session_id", "bridge_build", "project", "mdb", "current_element" })
                if (!JToken.DeepEquals(observed[key], expected[key])) return false;
        }
        return true;
    }

    private static DbElement ParagonCopyResolve(string reference) => NativeRead(() =>
        DbElement.GetElement(ParagonComponentTemplateRequest.Validate(reference).Pair));

    private static bool ParagonCopyType(Dictionary<string, object> identity, string type)
    {
        var actual = identity["actual_type"] as Dictionary<string, object>;
        var basis = identity["base_type"] as Dictionary<string, object>;
        return Equals(identity["db_type_code"], 2) && Equals(identity["type"], type)
            && actual != null && basis != null && Equals(actual["short_name"], type) && Equals(basis["short_name"], type);
    }

    private static List<Dictionary<string, object>> ParagonCopyMembers(DbElement parent, int limit,
        List<Dictionary<string, object>> rows, out DbElement last)
    {
        last = null;
        var seen = new HashSet<string>(StringComparer.Ordinal);
        for (int ordinal = 1; ordinal <= limit + 1; ordinal++)
        {
            DbElement member = NativeRead(() => parent.Member(ordinal));
            if (ReferenceEquals(member, null) || NativeRead(() => member.IsNull)) return rows;
            ParagonCopyRequire(ordinal <= limit, "parent_member_limit", "The direct-member list exceeds its bounded inspection limit.");
            var row = ComponentTemplateCoreIdentity(member);
            rows.Add(row);
            row["name"] = null; row["name_error"] = null;
            try { row["name"] = ComponentTemplateText(NativeRead(() => member.GetAsString(DbAttributeInstance.NAME)), 1024); }
            catch (NativeDatabaseFaultException) { throw; }
            catch (Exception error) { ThrowIfNativeFaulted(); row["name_error"] = ComponentTemplateError(error); throw; }
            ParagonCopyRequire(Equals(row["db_type_code"], 2) && seen.Add((string)row["refno"]), "invalid_parent_member", "The ordered parent list must contain distinct Catalogue identities.");
            last = member;
        }
        throw new InvalidOperationException("The parent member list did not terminate.");
    }

    private ParagonCopyCapture CaptureParagonCopy(ParagonComponentCopyRequest request, string session,
        string build, Dictionary<string, object> evidence)
    {
        var observedModule = ParagonCopyModule(); evidence["module"] = observedModule; ParagonCopyRequireModule(observedModule);
        var context = ParagonCopyContext(session, build, out Project project, out MDB mdb); evidence["context"] = context;
        ParagonCopyRequire(ParagonCopyContextText(context["project"]) && ParagonCopyContextText(context["mdb"]) && context["current_element"] != null
            && !ReferenceEquals(project, null) && !ReferenceEquals(mdb, null), "context_unknown", "The native project/MDB/typed CE boundary must be known.");
        var template = ReadParagonComponentTemplateCore(ParagonComponentTemplateRequest.Validate(request.SourceRefno), session, build,
            out ParagonComponentTemplateCapture sourceCapture);
        evidence["source_template"] = template;
        var target = template["target"] as Dictionary<string, object>;
        var leaf = (Dictionary<string, object>)template["leaf"];
        ParagonCopyRequire(sourceCapture != null && target != null && ParagonCopyType(target, "SCOM")
            && Equals(target["refno"], request.SourceRefno) && target["name"] is string && target["name_error"] == null
            && Equals(leaf["is_leaf"], true) && ParagonCopyNumbers(template) != null,
            "source_template_unknown", "The source must be a bound actual/base SCOM leaf with complete finite numeric PARAM coverage.");
        ParagonCopyRequire(ParagonCopyTemplateContextMatches(template, context)
            && ReferenceEquals(project, sourceCapture.OriginalProject) && ReferenceEquals(mdb, sourceCapture.OriginalMdb),
            "source_context_changed", "The source observation did not preserve the current native project/MDB boundary.");
        var referenceValues = (Dictionary<string, object>)template["references"];
        var referenceSlices = ParagonComponentTemplateRequest.ReferenceAttributes.Select(attribute =>
            ParagonCopyReferenceValue((Dictionary<string, object>)referenceValues[attribute])).ToList();
        ParagonCopyRequire(referenceSlices.Any(value => value != null && value.Type == JTokenType.Object),
            "no_supported_reference_coverage", "At least one supported reference-value slice must be observed before copying.");
        ParagonCopyRequire(referenceSlices.All(value => value != null), "source_reference_unknown",
            "Every supported fixed reference-value slice must be known and complete before copying.");
        DbElement source = sourceCapture.Source;
        DbElement parent = ParagonCopyResolve(request.ParentRefno);
        var parentIdentity = ComponentTemplateIdentity(parent); evidence["parent"] = parentIdentity;
        ParagonCopyRequire(Equals(parentIdentity["refno"], request.ParentRefno) && ParagonCopyType(parentIdentity, "CATE"),
            "parent_not_cate", "The explicit parent must resolve to the same actual/base Catalogue CATE.");
        DbElement owner = NativeRead(() => source.Owner);
        var ownerIdentity = ComponentTemplateIdentity(owner); evidence["source_owner"] = ownerIdentity;
        ParagonCopyRequire(Equals(ownerIdentity["refno"], request.ParentRefno) && ParagonCopyType(ownerIdentity, "CATE"),
            "source_owner_mismatch", "The source's native typed owner must be the explicit CATE parent.");
        DbElementType sourceType = NativeRead(() => source.GetActualType());
        ParagonCopyRequire(!ReferenceEquals(sourceType, null) && NativeRead(() => sourceType.IsValid)
            && NativeRead(() => sourceType.ShortName) == "SCOM", "source_actual_type_changed",
            "The native actual type changed before the creatability observation.");
        bool creatable = NativeRead(() => parent.IsCreatable(sourceType)); evidence["creatable"] = creatable;
        ParagonCopyRequire(creatable, "not_creatable", "The SDK does not currently report SCOM creatability under the explicit parent.");
        var members = (List<Dictionary<string, object>>)evidence["members"];
        ParagonCopyMembers(parent, ParagonCopyMaximumMembers, members, out _); evidence["members_complete"] = true;
        ParagonCopyRequire(members.Count(row => Equals(row["refno"], request.SourceRefno) && Equals(row["type"], "SCOM")) == 1,
            "source_not_listed", "The source must appear exactly once in the complete direct-member list.");
        var listedSource = members.Single(row => Equals(row["refno"], request.SourceRefno));
        ParagonCopyRequire(Equals(listedSource["name"], target["name"]), "source_name_changed",
            "The source NAME changed between its template observation and the parent list.");
        ParagonCopyRequire(!members.Any(row => string.Equals(row["name"] as string, request.Name, StringComparison.OrdinalIgnoreCase)),
            "name_present_in_parent", "The new name is already present under the parent.");
        DbElement named = NativeRead(() => mdb.FindElement(DbType.Catalogue, request.Name));
        if (ReferenceEquals(named, null)) throw new InvalidOperationException("The Catalogue name lookup returned an unread null proxy.");
        bool isNull = NativeRead(() => named.IsNull);
        int[] pair = NativeRead(() => named.Ref);
        bool absent = isNull && pair != null && pair.Length == 2 && pair[0] == 0 && pair[1] == 0;
        var lookup = (Dictionary<string, object>)evidence["name_lookup"];
        if (!absent && !isNull) { lookup["target"] = ComponentTemplateIdentity(named); lookup["status"] = "present"; }
        else if (absent) lookup["status"] = "absent";
        ParagonCopyRequire(absent, "name_not_verified_absent", "The requested Catalogue name was not verified absent in the current MDB.");
        var afterContext = ParagonCopyContext(session, build, out Project afterProject, out MDB afterMdb);
        ParagonCopyRequire(ReferenceEquals(project, afterProject) && ReferenceEquals(mdb, afterMdb)
            && JToken.DeepEquals(JObject.FromObject(context), JObject.FromObject(afterContext)),
            "preflight_context_changed", "The module/project/MDB/typed CE observations changed during capture.");
        ParagonCopyRequireModule(ParagonCopyModule());
        return new ParagonCopyCapture { Project = project, Mdb = mdb, Source = source, Parent = parent,
            Evidence = evidence, Members = members, Template = template };
    }

    private static List<string> ParagonCopyNumbers(Dictionary<string, object> template)
    {
        var value = template["parameters"] as Dictionary<string, object>;
        if (value == null || !Equals(value["supported"], true) || !Equals(value["sdk_type"], "DOUBLE")
            || !Equals(value["is_array"], true) || !Equals(value["shape"], "double_array")
            || !Equals(value["truncated"], false) || !Equals(value["inspection_limited"], false)
            || !Equals(value["native_array_materialized"], true)
            || ((Dictionary<string, string>)value["errors"]).Keys.Any(key => key != "unit_kind")) return null;
        var rows = (List<Dictionary<string, object>>)value["values"];
        if (!(value["total_count"] is int count) || count != rows.Count || !Equals(value["returned_count"], count)) return null;
        var numbers = new List<string>();
        for (int index = 0; index < rows.Count; index++)
        {
            var row = rows[index];
            if (!Equals(row["index"], index) || !Equals(row["status"], "observed") || row["error"] != null || !(row["value"] is double number)
                || double.IsNaN(number) || double.IsInfinity(number)) return null;
            numbers.Add(BitConverter.DoubleToInt64Bits(number).ToString("x16", CultureInfo.InvariantCulture));
        }
        return numbers;
    }

    private static JToken ParagonCopyReferenceValue(Dictionary<string, object> value)
    {
        if (Equals(value["supported"], false)) return new JValue("unsupported_excluded");
        if (!Equals(value["supported"], true) || !Equals(value["sdk_type"], "ELEMENT") || !(value["is_array"] is bool array)
            || !Equals(value["truncated"], false) || !Equals(value["inspection_limited"], false)
            || ((Dictionary<string, string>)value["errors"]).Keys.Any(key => key != "unit_kind" && key != "items")) return null;
        var rows = (List<Dictionary<string, object>>)value["values"];
        if (!(value["total_count"] is int count) || count != rows.Count || !Equals(value["returned_count"], count)) return null;
        if (array && !Equals(value["native_array_materialized"], true)) return null;
        var identities = new JArray();
        for (int index = 0; index < rows.Count; index++)
        {
            var row = rows[index];
            var identity = row["identity"] as Dictionary<string, object>;
            if (Equals(row["index"], index) && Equals(row["status"], "unset")
                && Equals(row["reference_null"], true) && identity == null && row["error"] == null)
            { identities.Add(JValue.CreateNull()); continue; }
            if (!Equals(row["index"], index) || !Equals(row["reference_null"], false) || identity == null) return null;
            var actual = identity["actual_type"] as Dictionary<string, object>;
            var basis = identity["base_type"] as Dictionary<string, object>;
            if (!(identity["refno"] is string) || !(identity["type"] is string) || !(identity["db_type_code"] is int)
                || actual == null || basis == null || !(actual["short_name"] is string) || !(basis["short_name"] is string)
                || !Equals(basis["short_name"], identity["type"])
                || ((Dictionary<string, string>)identity["errors"]).ContainsKey("request_binding")) return null;
            identities.Add(JObject.FromObject(new { refno = identity["refno"], type = identity["type"],
                db_type = identity["db_type"], db_type_code = identity["db_type_code"], identity_key = identity["identity_key"],
                actual_type = actual["short_name"], base_type = basis["short_name"] }));
        }
        return new JObject { ["shape"] = (string)value["shape"], ["values"] = identities };
    }

    private static bool? ParagonCopyReferencesEqual(Dictionary<string, object> source, Dictionary<string, object> target)
    {
        bool unknown = false, compared = false;
        var first = (Dictionary<string, object>)source["references"];
        var last = (Dictionary<string, object>)target["references"];
        foreach (string name in ParagonComponentTemplateRequest.ReferenceAttributes)
        {
            var a = ParagonCopyReferenceValue((Dictionary<string, object>)first[name]);
            var b = ParagonCopyReferenceValue((Dictionary<string, object>)last[name]);
            if (a == null || b == null) { unknown = true; continue; }
            if (!JToken.DeepEquals(a, b)) return false;
            if (a.Type == JTokenType.Object) compared = true;
        }
        return unknown || !compared ? (bool?)null : true;
    }

    private static bool? ParagonCopyParametersEqual(Dictionary<string, object> source, Dictionary<string, object> target)
    {
        var a = ParagonCopyNumbers(source); var b = ParagonCopyNumbers(target);
        return a == null || b == null ? (bool?)null : a.SequenceEqual(b);
    }

    private static bool? ParagonCopySourceEqual(Dictionary<string, object> source, Dictionary<string, object> target)
    {
        var a = source["target"] as Dictionary<string, object>; var b = target["target"] as Dictionary<string, object>;
        if (a == null || b == null) return null;
        foreach (string key in new[] { "refno", "type", "db_type_code", "name" })
        { if (a[key] == null || b[key] == null) return null; if (!Equals(a[key], b[key])) return false; }
        if (!ParagonCopyType(a, "SCOM") || !ParagonCopyType(b, "SCOM")) return false;
        var leaf = (Dictionary<string, object>)target["leaf"];
        if (!Equals(leaf["is_leaf"], true)) return leaf["is_leaf"] == null ? (bool?)null : false;
        bool? numeric = ParagonCopyParametersEqual(source, target), references = ParagonCopyReferencesEqual(source, target);
        return numeric == false || references == false ? false : numeric == true && references == true ? true : (bool?)null;
    }

    private static string ParagonCopyHash(JToken value)
    {
        var text = new StringBuilder(); ParagonCopyCanonical(value, text);
        using (var hash = SHA256.Create()) return BitConverter.ToString(hash.ComputeHash(new UTF8Encoding(false, true).GetBytes(text.ToString()))).Replace("-", "").ToLowerInvariant();
    }

    private static void ParagonCopyCanonical(JToken value, StringBuilder output)
    {
        if (value is JObject map)
        {
            output.Append('{'); foreach (var property in map.Properties().OrderBy(item => item.Name, StringComparer.Ordinal))
            { output.Append(JsonConvert.SerializeObject(property.Name)).Append(':'); ParagonCopyCanonical(property.Value, output); output.Append(';'); }
            output.Append('}'); return;
        }
        if (value is JArray array) { output.Append('['); foreach (var item in array) { ParagonCopyCanonical(item, output); output.Append(';'); } output.Append(']'); return; }
        output.Append(value.Type).Append(':');
        if (value.Type == JTokenType.Float) output.Append(BitConverter.DoubleToInt64Bits(value.Value<double>()).ToString("x16", CultureInfo.InvariantCulture));
        else output.Append(value.ToString(Formatting.None));
    }

    private static void RunParagonCopy(ParagonComponentCopyRequest request, Dictionary<string, object> progress,
        List<Dictionary<string, object>> errors)
    {
        string source = request.ParentRefno + "\nNEW SCOM " + request.Name + " AFTER LAST MEMBER COPY " + request.SourceRefno + "\n";
        var input = PmlScriptInput.Prepare(source, Encoding.Default);
        string path = Path.GetFullPath(Path.Combine(Path.GetTempPath(), "e3d-paragon-copy-" + Guid.NewGuid().ToString("N") + ".pmlmac"));
        FileStream file = null; bool owned = false;
        try
        {
            string command = PmlScriptInput.MacroCommand(path, input.CodePage);
            file = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read); owned = true;
            file.Write(input.MacroBytes, 0, input.MacroBytes.Length); file.Flush(true); file.Dispose();
            file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            var operation = NativeRead(() => PmlCommand.CreateCommand(command));
            if (ReferenceEquals(operation, null)) throw new InvalidOperationException("The command factory returned no native operation.");
            try
            {
                NativeWrite(delegate
                {
                    progress["creation_attempted"] = true; progress["native_run_attempted"] = true;
                    bool success = operation.Run();
                    progress["native_run_returned"] = true; progress["native_run_success"] = success;
                });
            }
            catch (NativeDatabaseFaultException) { throw; }
            catch (Exception error) { ThrowIfNativeFaulted(); ParagonCopyError(errors, "run", "native_run_exception", error.Message); }
            try
            {
                string nativeResult = NativeRead(() => operation.Result);
                PdmsMessage error = NativeRead(() => operation.Error);
                if (!ReferenceEquals(error, null))
                {
                    string text = NativeRead(() => error.MessageText());
                    int module = NativeRead(() => error.ModuleNumber);
                    int number = NativeRead(() => error.MessageNumber);
                    ParagonCopyError(errors, "native_diagnostic", "pml_error", text + " (" + module + "/" + number + ")");
                }
            }
            catch (NativeDatabaseFaultException) { throw; }
            catch (Exception error) { ThrowIfNativeFaulted(); ParagonCopyError(errors, "native_diagnostic", "diagnostic_read_failed", error.Message); }
        }
        finally
        {
            // Managed macro cleanup only. Never releases the model command
            // gate, deletes a model element, restores CE or reads the SDK.
            try { file?.Dispose(); }
            catch (Exception error) { ParagonCopyError(errors, "macro_cleanup", "close_failed", error.Message); }
            if (owned && !Equals(progress["native_run_attempted"], true))
                try { File.Delete(path); } catch (Exception error) { ParagonCopyError(errors, "macro_cleanup", "delete_failed", error.Message); }
            else if (owned && Equals(progress["native_run_returned"], true) && Equals(progress["native_run_success"], true)
                && (currentNativeFaultState == null || !currentNativeFaultState.HasFault))
                try { File.Delete(path); } catch (Exception error) { ParagonCopyError(errors, "macro_cleanup", "delete_failed", error.Message); }
        }
    }

    private static void ParagonCopyReadbackStage(string stage, List<Dictionary<string, object>> errors, Action read)
    {
        try { read(); }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error) { ThrowIfNativeFaulted(); ParagonCopyError(errors, stage, "readback_failed", error.Message); }
    }

    private void ReadBackParagonCopy(ParagonComponentCopyRequest request, ParagonCopyCapture captured,
        string session, string build, Dictionary<string, object> after, Dictionary<string, object> progress,
        List<Dictionary<string, object>> errors)
    {
        var checks = (Dictionary<string, object>)after["verification"];
        bool moduleVerified = false, contextVerified = false;
        ParagonCopyReadbackStage("after.module", errors, delegate
        {
            var module = ParagonCopyModule(); after["module"] = module; ParagonCopyRequireModule(module); moduleVerified = true;
        });
        if (!moduleVerified) return;
        ParagonCopyReadbackStage("after.context", errors, delegate
        {
            after["context"] = ParagonCopyContext(session, build, out Project project, out MDB mdb);
            var context = (Dictionary<string, object>)after["context"];
            ParagonCopyRequire(ReferenceEquals(project, captured.Project) && ReferenceEquals(mdb, captured.Mdb)
                && ParagonCopyContextText(context["project"]) && ParagonCopyContextText(context["mdb"]) && context["current_element"] != null,
                "post_context_changed", "The native project/MDB instances changed across the mutating stage.");
            contextVerified = true;
        });
        if (!contextVerified) return;
        ParagonCopyReadbackStage("after.parent", errors, delegate
        {
            var parent = ComponentTemplateIdentity(captured.Parent); after["parent"] = parent;
            ParagonCopyRequire(Equals(parent["refno"], request.ParentRefno) && ParagonCopyType(parent, "CATE"), "post_parent_changed", "The parent identity no longer matches the explicit CATE.");
        });
        DbElement newElement = null;
        ParagonCopyReadbackStage("after.members", errors, delegate
        {
            var members = (List<Dictionary<string, object>>)after["members"];
            ParagonCopyMembers(captured.Parent, ParagonCopyMaximumMembers + 1, members, out DbElement last);
            after["members_complete"] = true;
            bool append = members.Count == captured.Members.Count + 1;
            for (int index = 0; append && index < captured.Members.Count; index++)
                foreach (string key in new[] { "refno", "type", "db_type_code", "name" })
                    append &= Equals(members[index][key], captured.Members[index][key]);
            checks["parent_append"] = append;
            if (!append) return;
            var row = members[members.Count - 1];
            if (!Equals(row["type"], "SCOM") || !string.Equals(row["name"] as string, request.Name, StringComparison.OrdinalIgnoreCase))
            { checks["new_identity"] = false; return; }
            var identity = ComponentTemplateIdentity(last);
            DbElement owner = NativeRead(() => last.Owner);
            var ownerIdentity = ComponentTemplateIdentity(owner); after["new_owner"] = ownerIdentity;
            var leaf = ComponentTemplateLeaf(last);
            bool verified = ParagonCopyType(identity, "SCOM") && Equals(identity["refno"], row["refno"])
                && !Equals(identity["refno"], request.SourceRefno) && !Equals(identity["refno"], request.ParentRefno)
                && string.Equals(identity["name"] as string, request.Name, StringComparison.OrdinalIgnoreCase)
                && ParagonCopyType(ownerIdentity, "CATE") && Equals(ownerIdentity["refno"], request.ParentRefno)
                && Equals(leaf["is_leaf"], true);
            checks["new_identity"] = verified;
            if (verified)
            {
                progress["known_new_refno"] = identity["refno"]; progress["known_new_refno_verified"] = true;
                newElement = last;
            }
        });
        ParagonCopyReadbackStage("after.source_template", errors, delegate
        {
            var template = ReadParagonComponentTemplateCore(ParagonComponentTemplateRequest.Validate(request.SourceRefno), session, build,
                out ParagonComponentTemplateCapture observed); after["source_template"] = template;
            ParagonCopyRequire(observed != null && ReferenceEquals(observed.OriginalProject, captured.Project)
                && ReferenceEquals(observed.OriginalMdb, captured.Mdb)
                && ParagonCopyTemplateContextMatches(template, (Dictionary<string, object>)after["context"]),
                "source_readback_context_unknown", "The source readback has an unknown or changed native context.");
        });
        if (!ReferenceEquals(newElement, null))
            ParagonCopyReadbackStage("after.new_template", errors, delegate
            {
                var template = ReadParagonComponentTemplateCore(ParagonComponentTemplateRequest.Validate((string)progress["known_new_refno"]), session, build,
                    out ParagonComponentTemplateCapture observed); after["new_template"] = template;
                var targetIdentity = template["target"] as Dictionary<string, object>;
                ParagonCopyRequire(observed != null && ReferenceEquals(observed.OriginalProject, captured.Project)
                    && ReferenceEquals(observed.OriginalMdb, captured.Mdb)
                    && ParagonCopyTemplateContextMatches(template, (Dictionary<string, object>)after["context"])
                    && targetIdentity != null && ParagonCopyType(targetIdentity, "SCOM")
                    && string.Equals(targetIdentity["name"] as string, request.Name, StringComparison.OrdinalIgnoreCase)
                    && Equals(((Dictionary<string, object>)template["leaf"])["is_leaf"], true),
                    "new_readback_unknown", "The new template identity/leaf/context did not remain verified during readback.");
            });
        var source = after["source_template"] as Dictionary<string, object>;
        var target = after["new_template"] as Dictionary<string, object>;
        if (source != null)
            progress["source_unchanged_scoped"] = checks["source_unchanged_scoped"] = ParagonCopySourceEqual(captured.Template, source);
        if (source != null && target != null)
        {
            progress["copied_parameters_equal"] = checks["copied_parameters_equal"] = ParagonCopyParametersEqual(source, target);
            progress["copied_references_equal"] = checks["copied_references_equal"] = ParagonCopyReferencesEqual(source, target);
        }
        ParagonCopyReadbackStage("after.final_context", errors, delegate
        {
            var context = ParagonCopyContext(session, build, out Project project, out MDB mdb);
            ParagonCopyRequire(ReferenceEquals(project, captured.Project) && ReferenceEquals(mdb, captured.Mdb)
                && after["context"] != null && JToken.DeepEquals(JObject.FromObject(context), JObject.FromObject(after["context"])),
                "readback_context_changed", "The post-copy reads did not preserve their native project/MDB/typed CE boundary.");
            ParagonCopyRequireModule(ParagonCopyModule());
        });
        progress["readback_complete"] = errors.Count == 0 && Equals(after["members_complete"], true)
            && Equals(checks["new_identity"], true) && Equals(checks["parent_append"], true)
            && source != null && target != null;
    }
}
