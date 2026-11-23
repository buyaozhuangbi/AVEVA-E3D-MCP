using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Aveva.Core.Database;
using Newtonsoft.Json.Linq;

namespace E3d21AiBridge;

public sealed partial class E3d21Context : IE3d21ParagonComponentParametersContext
{
    private const string ParagonParametersOperation = "paragon_component_parameters_set";
    private readonly object paragonParametersJournalLock = new object();
    private string paragonParametersSession;
    private ParagonComponentCopyJournal paragonParametersJournal;
    private sealed class ParagonParametersCapture
    {
        internal Project Project;
        internal MDB Mdb;
        internal DbElement Target;
        internal DbAttribute Attribute;
        internal Dictionary<string, object> Evidence;
        internal List<string> Bits;
    }
    private ParagonComponentCopyJournal ParagonParametersJournalFor(string session)
    {
        lock (paragonParametersJournalLock)
        {
            if (paragonParametersSession != null && paragonParametersSession != session)
                throw new InvalidOperationException("A parameter context cannot be reused by another server session.");
            if (paragonParametersJournal == null)
            {
                paragonParametersSession = session;
                paragonParametersJournal = new ParagonComponentCopyJournal(operation: ParagonComponentCopyJournal.ParametersOperation);
            }
            return paragonParametersJournal;
        }
    }
    public object GetParagonComponentParametersStatus(string requestId, string expectedSessionId, string sessionId)
    {
        ParagonComponentParametersRequest.ValidateStatus(requestId, expectedSessionId);
        if (!ParagonCopyContextText(sessionId) || expectedSessionId != sessionId)
            throw new ArgumentException("The expected bridge session does not match the current server session.");
        // Pure managed history remains queryable while the UI is blocked or faulted.
        return ParagonParametersJournalFor(sessionId).Get(requestId);
    }

    public ParagonComponentParametersReply SetParagonComponentParameters(string refno, IList<double> parameters,
        bool dryRun, string requestId, string expectedSessionId,
        string expectedBridgeBuild, string expectedRevision, string sessionId)
    {
        var request = ParagonComponentParametersRequest.Validate(refno, parameters, dryRun,
            requestId, expectedSessionId, expectedBridgeBuild, expectedRevision);
        if (string.IsNullOrWhiteSpace(sessionId) || sessionId.Length > 256)
            throw new ArgumentException("A server-owned bridge session identity is required.", nameof(sessionId));
        string build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
        var result = ParagonParametersResult(request, sessionId, build);
        var progress = (Dictionary<string, object>)result["progress"];
        var errors = (List<Dictionary<string, object>>)result["errors"];
        var journal = ParagonParametersJournalFor(sessionId);
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
                    refno = request.Refno, parameter_bits = request.ParameterBits,
                    session_id = sessionId, bridge_build = build,
                    expected_revision = request.ExpectedRevision
                }));
                var accepted = journal.Begin(request.RequestId, fingerprint);
                if (!accepted.IsNew)
                {
                    result["status"] = "duplicate"; result["journal"] = accepted.Snapshot;
                    ParagonCopyError(errors, "admission", "duplicate_request_id", "The request ID was already reserved; this submission did not execute.");
                    return new ParagonComponentParametersReply(409, false, result, "The request ID was already reserved.");
                }
                admission = accepted.Token;
            }
            if (!TryBeginCommand("PARAGON component parameters"))
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
                            throw new InvalidOperationException("The admitted component-parameters callback lost ownership before it started.");
                        ThrowIfNativeFaulted();
                        var before = ParagonParametersEvidence(); result["before"] = before;
                        var captured = CaptureParagonParameters(request, sessionId, build, before, progress, true);
                        string revision = ParagonParametersRevision(request, sessionId, build, captured);
                        result["scoped_revision"] = revision;
                        if (request.DryRun)
                        {
                            result["status"] = "prepared"; result["success"] = true; result["complete"] = true;
                            return true;
                        }
                        if (revision != request.ExpectedRevision)
                            throw new ParagonCopyConflict("revision_conflict", "The scoped native observations changed since preparation; no mutating stage was entered.");
                        bool setterFailed = false;
                        if (!Equals(progress["no_change"], true))
                        {
                            try
                            {
                                NativeWrite(delegate
                                {
                                    progress["parameter_write_attempted"] = true;
                                    captured.Target.SetAttribute(captured.Attribute, request.Parameters);
                                    progress["parameter_write_returned"] = true;
                                });
                            }
                            catch (NativeDatabaseFaultException) { throw; }
                            catch (Exception error)
                            {
                                ThrowIfNativeFaulted(); setterFailed = true;
                                ParagonCopyError(errors, "setter", "parameter_setter_failed", error.Message);
                            }
                        }
                        var after = ParagonParametersEvidence(); result["after"] = after;
                        ParagonParametersReadback(request, captured, sessionId, build, after, progress, errors);
                        bool success = !setterFailed && Equals(progress["readback_complete"], true)
                            && Equals(progress["parameters_equal_request"], true) && Equals(progress["context_unchanged"], true)
                            && (Equals(progress["no_change"], true) || Equals(progress["parameter_write_returned"], true)) && errors.Count == 0;
                        result["success"] = success; result["complete"] = success;
                        result["status"] = success ? (Equals(progress["no_change"], true) ? "unchanged" : "updated") : "failed";
                        progress["outcome_uncertain"] = !success && Equals(progress["parameter_write_attempted"], true);
                        if (!success) throw new InvalidOperationException("The parameter setter or its independent numeric readback did not complete successfully.");
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
                throw new InvalidOperationException("The dispatcher returned before the parameter callback began; that callback was cancelled without execution.");
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
            failure = ParagonComponentParametersRequest.ErrorText(error.Message);
            result["status"] = "failed"; httpStatus = 500; ok = false;
            bool fault = error is NativeDatabaseFaultException || nativeFaultState != null && nativeFaultState.HasFault;
            progress["native_fault_latched"] = fault;
            progress["outcome_uncertain"] = Equals(progress["parameter_write_attempted"], true);
            ParagonCopyError(errors, "execution", fault ? "native_database_fault" : "parameter_update_failed", failure);
        }
        finally
        {
            // All facts below are managed. Do not probe CE, diagnostics or SDK
            // handles after either a native fault or a dispatcher failure.
            result["this_submission_executed"] = Equals(progress["parameter_write_attempted"], true);
            if (!Equals(result["status"], "prepared") && !(Equals(result["status"], "updated") || Equals(result["status"], "unchanged")))
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
        return new ParagonComponentParametersReply(httpStatus, ok, result, failure, latched);
    }

    private static Dictionary<string, object> ParagonParametersResult(ParagonComponentParametersRequest request, string session, string build) =>
        new Dictionary<string, object>
        {
            ["operation"] = ParagonParametersOperation, ["dry_run"] = request.DryRun,
            ["request"] = new { refno = request.Refno, parameters = request.Parameters, parameter_bits = request.ParameterBits },
            ["session_id"] = session, ["bridge_build"] = build, ["request_id"] = request.RequestId,
            ["status"] = "blocked", ["success"] = false, ["complete"] = false, ["scoped_revision"] = null,
            ["before"] = null, ["after"] = null, ["errors"] = new List<Dictionary<string, object>>(), ["journal"] = null,
            ["this_submission_executed"] = false, ["request_replayed"] = false,
            ["progress"] = new Dictionary<string, object>
            {
                ["known_target_refno"] = null, ["known_target_refno_verified"] = null,
                ["parameter_write_attempted"] = false, ["parameter_write_returned"] = false,
                ["parameters_equal_request"] = null, ["readback_complete"] = false, ["context_unchanged"] = null,
                ["no_change"] = null, ["outcome_uncertain"] = false, ["native_fault_latched"] = false, ["retry_safe"] = false
            },
            ["scope"] = new
            {
                kind = "fixed_catalogue_scom_numeric_parameters_update", backend = "native_sdk_double_array_setter",
                parameter_comparison = "complete_finite_binary64_bits", whole_array_replacement = true,
                array_length_change_allowed = false, units_converted = false, units_verified = false,
                geometry_observed = false, geometry_equivalence_verified = false, word_parameters_observed = false,
                hidden_attributes_observed = false, all_references_covered = false, whole_clone_fingerprint = false,
                target_creation_verified = false, engineering_validated = (bool?)null, write_permission_verified = (bool?)null,
                savework_performed = false, automatic_ce_restore = false, atomic_snapshot = false,
                scoped_revision_is_lock = false, whole_project_coverage = false
            }
        };

    private static Dictionary<string, object> ParagonParametersEvidence() => new Dictionary<string, object>
    {
        ["module"] = null, ["context"] = null, ["target_binding"] = null, ["template"] = null, ["owner"] = null,
        ["setable"] = null, ["parameter_bits"] = null
    };

    private ParagonParametersCapture CaptureParagonParameters(ParagonComponentParametersRequest request,
        string session, string build, Dictionary<string, object> evidence, Dictionary<string, object> progress, bool preflight)
    {
        var module = ParagonCopyModule(); evidence["module"] = module; ParagonCopyRequireModule(module);
        var context = ParagonCopyContext(session, build, out Project project, out MDB mdb); evidence["context"] = context;
        ParagonCopyRequire(!ReferenceEquals(project, null) && !ReferenceEquals(mdb, null)
            && ParagonCopyContextText(context["project"]) && ParagonCopyContextText(context["mdb"]) && context["current_element"] != null,
            "context_unknown", "A current project/MDB/typed CE is required for the explicit parameter target.");
        // Persist the first required numeric binding before the larger
        // template reader can fail in an optional NAME/reference getter.
        // These managed facts remain usable after a nativefault; the SDK
        // handle itself never enters the receipt or journal.
        var first = ComponentTemplateCoreIdentity(ParagonCopyResolve(request.Refno)); evidence["target_binding"] = first;
        ParagonCopyRequire(Equals(first["refno"], request.Refno) && Equals(first["type"], "SCOM")
            && Equals(first["db_type_code"], 2), "target_binding_mismatch",
            "The explicit numeric target must bind to the same Catalogue SCOM base identity.");
        progress["known_target_refno"] = request.Refno; progress["known_target_refno_verified"] = true;
        var template = ReadParagonComponentTemplateCore(ParagonComponentTemplateRequest.Validate(request.Refno), session, build,
            out ParagonComponentTemplateCapture captured); evidence["template"] = template;
        var target = template["target"] as Dictionary<string, object>;
        if (target != null && Equals(target["refno"], request.Refno) && Equals(target["type"], "SCOM") && Equals(target["db_type_code"], 2))
        {
            var binding = target["request_binding"] as Dictionary<string, object>;
            if (binding != null && Equals(binding["verified"], true) && Equals(binding["requested_refno"], request.Refno)
                && Equals(binding["resolved_refno"], request.Refno))
            { progress["known_target_refno"] = request.Refno; progress["known_target_refno_verified"] = true; }
        }
        // Compute bits directly from native managed doubles before any JSON
        // conversion; net48 JSON loses the sign of negative zero.
        var bits = ParagonCopyNumbers(template); evidence["parameter_bits"] = bits?.ToArray();
        ParagonCopyRequire(captured != null && target != null && Equals(target["refno"], request.Refno)
            && ParagonCopyType(target, "SCOM") && !((Dictionary<string, string>)target["errors"]).ContainsKey("request_binding")
            && Equals(((Dictionary<string, object>)template["leaf"])["is_leaf"], true),
            "target_not_scom_leaf", "The target must resolve to the explicit actual/base Catalogue SCOM leaf.");
        foreach (string key in new[] { "refno", "type", "db_type", "db_type_code", "identity_key" })
            ParagonCopyRequire(Equals(first[key], target[key]), "target_binding_changed",
                "The complete template identity must agree with the first independent numeric binding.");
        ParagonCopyRequire(ReferenceEquals(project, captured.OriginalProject) && ReferenceEquals(mdb, captured.OriginalMdb)
            && ParagonCopyTemplateContextMatches(template, context), "template_context_changed",
            "The raw template and the independently observed native context must agree.");
        ParagonCopyRequire(bits != null, "parameter_values_unknown", "The complete finite native DOUBLE PARAM array must be readable.");
        if (preflight) ParagonCopyRequire(bits.Count == request.Parameters.Length && bits.Count >= 1 && bits.Count <= 128,
            "parameter_length_conflict", "The complete requested PARAM vector must preserve the existing length from 1 through 128.");
        DbElement owner = NativeRead(() => captured.Source.Owner);
        var identity = ComponentTemplateIdentity(owner); evidence["owner"] = identity;
        ParagonCopyRequire(ParagonCopyType(identity, "CATE"), "owner_not_cate", "The target must have an actual/base Catalogue CATE owner.");
        DbAttribute attribute = null;
        if (preflight)
        {
            attribute = NativeRead(() => DbAttribute.GetDbAttribute("PARAM"));
            ParagonCopyRequire(!ReferenceEquals(attribute, null) && NativeRead(() => attribute.IsValid)
                && NativeRead(() => attribute.Type) == DbAttributeType.DOUBLE && NativeRead(() => attribute.IsArray)
                && NativeRead(() => captured.Source.IsAttributeValid(attribute)),
                "parameter_attribute_unknown", "PARAM must be a supported native DOUBLE array before a setter is entered.");
            bool setable = NativeRead(() => captured.Source.IsAttributeSetable(attribute)); evidence["setable"] = setable;
            ParagonCopyRequire(setable, "parameter_not_setable", "The SDK does not currently report PARAM as setable on the explicit target.");
            // This is an observed array comparison, independent of whether
            // the terminal context/module checks subsequently reject capture.
            // Later readback must not replace this pre-write fact.
            progress["no_change"] = bits.SequenceEqual(request.ParameterBits);
        }
        // Post-set IsAttributeSetable is deliberately unobserved/null. It is
        // not a postcondition of a write that has already returned.
        var finalContext = ParagonCopyContext(session, build, out Project finalProject, out MDB finalMdb);
        ParagonCopyRequire(ReferenceEquals(project, finalProject) && ReferenceEquals(mdb, finalMdb)
            && JToken.DeepEquals(JObject.FromObject(context), JObject.FromObject(finalContext)),
            "parameter_context_changed", "The native project/MDB/typed CE context changed during the parameter observation.");
        ParagonCopyRequireModule(ParagonCopyModule());
        return new ParagonParametersCapture { Target = captured.Source, Attribute = attribute, Project = project, Mdb = mdb,
            Evidence = evidence, Bits = bits };
    }

    private static object ParagonParametersIdentitySlice(Dictionary<string, object> identity)
    {
        var actual = (Dictionary<string, object>)identity["actual_type"];
        var basis = (Dictionary<string, object>)identity["base_type"];
        return new { refno = identity["refno"], type = identity["type"], db_type = identity["db_type"],
            db_type_code = identity["db_type_code"], identity_key = identity["identity_key"],
            actual_type = actual["short_name"], base_type = basis["short_name"] };
    }

    private static string ParagonParametersRevision(ParagonComponentParametersRequest request,
        string session, string build, ParagonParametersCapture captured) => ParagonCopyHash(JObject.FromObject(new
        {
            operation = ParagonParametersOperation, refno = request.Refno, parameter_bits = request.ParameterBits,
            session_id = session, bridge_build = build, module = captured.Evidence["module"], context = captured.Evidence["context"],
            target = ParagonParametersIdentitySlice((Dictionary<string, object>)((Dictionary<string, object>)captured.Evidence["template"])["target"]),
            owner = ParagonParametersIdentitySlice((Dictionary<string, object>)captured.Evidence["owner"]),
            old_parameter_bits = captured.Bits, setable = captured.Evidence["setable"]
        }));

    private void ParagonParametersReadback(ParagonComponentParametersRequest request, ParagonParametersCapture before,
        string session, string build, Dictionary<string, object> evidence, Dictionary<string, object> progress,
        List<Dictionary<string, object>> errors)
    {
        try
        {
            var after = CaptureParagonParameters(request, session, build, evidence, progress, false);
            bool same = ReferenceEquals(before.Project, after.Project) && ReferenceEquals(before.Mdb, after.Mdb)
                && JToken.DeepEquals(JObject.FromObject(before.Evidence["context"]), JObject.FromObject(after.Evidence["context"]))
                && JToken.DeepEquals(JToken.FromObject(ParagonParametersIdentitySlice((Dictionary<string, object>)before.Evidence["owner"])),
                    JToken.FromObject(ParagonParametersIdentitySlice((Dictionary<string, object>)after.Evidence["owner"])));
            progress["context_unchanged"] = same;
            progress["parameters_equal_request"] = after.Bits.SequenceEqual(request.ParameterBits);
            progress["readback_complete"] = same;
            if (!same) ParagonCopyError(errors, "readback", "parameter_context_changed", "The required native context/owner changed across the parameter operation.");
        }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            var bits = evidence["parameter_bits"] as string[];
            if (bits != null) progress["parameters_equal_request"] = bits.SequenceEqual(request.ParameterBits);
            ParagonCopyError(errors, "readback", "parameter_readback_failed", error.Message);
        }
    }
}
