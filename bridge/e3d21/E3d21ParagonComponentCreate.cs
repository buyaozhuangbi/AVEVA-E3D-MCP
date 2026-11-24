using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Aveva.Core.Database;
using Newtonsoft.Json.Linq;

namespace E3d21AiBridge;

// One direct SDK creation, with no template COPY, PML, definition-tree writes,
// savework, automatic CE restoration, cleanup deletion, or setter replay.
public sealed partial class E3d21Context : IE3d21ParagonComponentCreateContext
{
    private const int ParagonCreateSetMemberLimit = 64;
    private readonly object paragonCreateJournalLock = new object();
    private string paragonCreateSession;
    private ParagonComponentCopyJournal paragonCreateJournal;

    private sealed class ParagonCreateCapture
    {
        internal Project Project;
        internal MDB Mdb;
        internal DbElement Parent, PointSet, GeometrySet;
        internal DbElementType Type;
        internal Dictionary<string, object> Evidence;
        internal List<Dictionary<string, object>> Members;
    }

    private ParagonComponentCopyJournal ParagonCreateJournalFor(string session)
    {
        lock (paragonCreateJournalLock)
        {
            if (paragonCreateSession != null && paragonCreateSession != session)
                throw new InvalidOperationException("A component-create context cannot be reused by a different bridge session.");
            if (paragonCreateJournal == null)
            {
                paragonCreateSession = session;
                paragonCreateJournal = new ParagonComponentCopyJournal(operation: ParagonComponentCopyJournal.CreateOperation);
            }
            return paragonCreateJournal;
        }
    }

    public object GetParagonComponentCreateStatus(string requestId, string expectedSessionId, string sessionId)
    {
        ParagonComponentCopyRequest.ValidateStatus(requestId, expectedSessionId);
        if (string.IsNullOrWhiteSpace(sessionId) || sessionId.Length > 256 || expectedSessionId != sessionId)
            throw new ArgumentException("The expected bridge session does not match the current server session.");
        // Managed only, including while a native callback is blocked or faulted.
        return ParagonCreateJournalFor(sessionId).Get(requestId);
    }

    public ParagonComponentCreateReply CreateParagonComponent(string parentRefno, string pointRefno,
        string geometryRefno, string name, IList<double> parameters, bool dryRun, string requestId,
        string expectedSessionId, string expectedBridgeBuild, string expectedRevision, string sessionId)
    {
        var request = ParagonComponentCreateRequest.Validate(parentRefno, pointRefno, geometryRefno, name,
            parameters, dryRun, requestId, expectedSessionId, expectedBridgeBuild, expectedRevision);
        if (string.IsNullOrWhiteSpace(sessionId) || sessionId.Length > 256)
            throw new ArgumentException("A server-owned bridge session identity is required.", nameof(sessionId));
        string build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
        var result = ParagonCreateResult(request, sessionId, build);
        var progress = (Dictionary<string, object>)result["progress"];
        var errors = (List<Dictionary<string, object>>)result["errors"];
        var journal = ParagonCreateJournalFor(sessionId);
        ParagonComponentCopyJournal.AdmissionToken admission = null;
        ParagonComponentCopyJournal.CallbackToken callback = null;
        bool began = false, ok = true;
        int httpStatus = 200, invocationState = 0;
        string failure = null;
        var completed = new TaskCompletionSource<bool>();
        try
        {
            if (!request.DryRun)
            {
                if (request.ExpectedSessionId != sessionId || request.ExpectedBridgeBuild != build)
                    throw new ParagonCopyConflict("bridge_identity_conflict", "The expected bridge session/build changed; no request was admitted.");
                string fingerprint = ParagonCopyHash(JObject.FromObject(new
                {
                    operation = "paragon_component_create", parent_refno = request.ParentRefno,
                    point_refno = request.PointRefno, geometry_refno = request.GeometryRefno,
                    name = request.Name, parameter_bits = request.ParameterBits,
                    session_id = sessionId, bridge_build = build, expected_revision = request.ExpectedRevision
                }));
                var accepted = journal.Begin(request.RequestId, fingerprint);
                if (!accepted.IsNew)
                {
                    result["status"] = "duplicate"; result["journal"] = accepted.Snapshot;
                    ParagonCopyError(errors, "admission", "duplicate_request_id", "The request ID was already reserved; this submission did not execute.");
                    return new ParagonComponentCreateReply(409, false, result, "The request ID was already reserved.");
                }
                admission = accepted.Token;
            }
            if (!TryBeginCommand("PARAGON component create"))
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
                            throw new InvalidOperationException("The admitted component-create callback lost ownership before it started.");
                        ThrowIfNativeFaulted();
                        var before = ParagonCreateBefore(); result["before"] = before;
                        var captured = CaptureParagonCreate(request, sessionId, build, before);
                        string revision = ParagonCopyHash(JObject.FromObject(new
                        {
                            operation = "paragon_component_create", parent_refno = request.ParentRefno,
                            point_refno = request.PointRefno, geometry_refno = request.GeometryRefno,
                            name = request.Name, parameter_bits = request.ParameterBits,
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
                        var after = ParagonCreateAfter(); result["after"] = after;
                        try { RunParagonCreate(request, captured, sessionId, build, after, progress); }
                        catch (NativeDatabaseFaultException) { throw; }
                        catch (Exception error)
                        {
                            ThrowIfNativeFaulted();
                            ParagonCopyError(errors, "setter", "component_create_or_setter_failed", error.Message);
                        }
                        // Ordinary failures can have effects. Read back independently,
                        // never continue setters, delete an element, or infer success
                        // solely from CreateLast/SetAttribute returning.
                        ReadBackParagonCreate(request, captured, sessionId, build, after, progress, errors);
                        bool success = new[] { "creation_returned", "name_returned", "point_ref_returned",
                            "geometry_ref_returned", "parameters_returned", "known_new_refno_verified", "parent_append",
                            "readback_complete", "context_unchanged", "parameters_equal_request", "references_equal_request",
                            "sets_unchanged_scoped" }.All(key => Equals(progress[key], true)) && errors.Count == 0;
                        result["success"] = success; result["complete"] = success;
                        result["status"] = success ? "created" : "failed";
                        progress["outcome_uncertain"] = !success && Equals(progress["creation_attempted"], true);
                        if (!success) throw new InvalidOperationException("The direct SDK creation or its independent scoped readback did not complete successfully.");
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
                // A callback which began keeps the command gate until its actual
                // completion, even if the dispatcher returned/threw early.
                if (Interlocked.CompareExchange(ref invocationState, 3, 0) == 1)
                    completed.Task.GetAwaiter().GetResult();
            }
            if (Volatile.Read(ref invocationState) == 3)
                throw new InvalidOperationException("The dispatcher returned before the create callback began; that callback was cancelled without execution.");
        }
        catch (ParagonCopyConflict error)
        {
            failure = ParagonComponentCreateRequest.ErrorText(error.Message);
            bool attempted = Equals(progress["creation_attempted"], true);
            result["status"] = attempted ? "failed" : request.DryRun ? "blocked" : "rejected";
            httpStatus = attempted ? 500 : request.DryRun ? 200 : 409; ok = !attempted && request.DryRun;
            progress["outcome_uncertain"] = attempted;
            ParagonCopyError(errors, attempted ? "execution" : "preflight", error.Code, failure);
        }
        catch (Exception error)
        {
            failure = ParagonComponentCreateRequest.ErrorText(error.Message);
            result["status"] = "failed"; httpStatus = 500; ok = false;
            bool fault = error is NativeDatabaseFaultException || nativeFaultState != null && nativeFaultState.HasFault;
            progress["native_fault_latched"] = fault;
            progress["outcome_uncertain"] = Equals(progress["creation_attempted"], true);
            ParagonCopyError(errors, "execution", fault ? "native_database_fault" : "component_create_failed", failure);
        }
        finally
        {
            // All remaining facts are managed; no diagnostics/CE/SDK reads.
            result["this_submission_executed"] = Equals(progress["creation_attempted"], true);
            if (!Equals(result["status"], "prepared") && !Equals(result["status"], "created"))
            { result["success"] = false; result["complete"] = false; }
            if (began) EndCommand();
        }
        bool latched = Equals(progress["native_fault_latched"], true);
        if (admission != null)
        {
            var envelope = JObject.FromObject(new { ok, version = "E3D21", session_id = sessionId,
                bridge_build = build, result, error = failure,
                error_code = latched ? "native_database_fault" : null, native_fault_latched = latched, retry_safe = false });
            var facts = JObject.FromObject(progress);
            facts["error_code"] = errors.Count == 0 ? null : errors[errors.Count - 1]["code"] as string;
            var summary = ParagonComponentCopyJournal.CriticalSummary.From(facts);
            if (callback != null) journal.CompleteStarted(callback, envelope, summary, httpStatus);
            else journal.CompleteBeforeStart(admission, envelope, summary, httpStatus);
            result["journal"] = journal.Get(request.RequestId);
        }
        return new ParagonComponentCreateReply(httpStatus, ok, result, failure, latched);
    }

    private static Dictionary<string, object> ParagonCreateResult(ParagonComponentCreateRequest request, string session, string build) =>
        new Dictionary<string, object>
        {
            ["operation"] = "paragon_component_create", ["dry_run"] = request.DryRun,
            ["request"] = new { parent_refno = request.ParentRefno, point_refno = request.PointRefno,
                geometry_refno = request.GeometryRefno, name = request.Name, parameters = request.Parameters, parameter_bits = request.ParameterBits },
            ["session_id"] = session, ["bridge_build"] = build, ["request_id"] = request.RequestId,
            ["status"] = "blocked", ["success"] = false, ["complete"] = false, ["scoped_revision"] = null,
            ["before"] = null, ["after"] = null, ["journal"] = null,
            ["this_submission_executed"] = false, ["request_replayed"] = false,
            ["errors"] = new List<Dictionary<string, object>>(),
            ["progress"] = new Dictionary<string, object>
            {
                ["creation_attempted"] = false, ["creation_returned"] = false,
                ["name_attempted"] = false, ["name_returned"] = false,
                ["point_ref_attempted"] = false, ["point_ref_returned"] = false,
                ["geometry_ref_attempted"] = false, ["geometry_ref_returned"] = false,
                ["parameters_attempted"] = false, ["parameters_returned"] = false,
                ["known_new_refno"] = null, ["known_new_refno_verified"] = null, ["parent_append"] = null,
                ["readback_complete"] = false, ["context_unchanged"] = null, ["parameters_equal_request"] = null,
                ["references_equal_request"] = null, ["sets_unchanged_scoped"] = null,
                ["outcome_uncertain"] = false, ["native_fault_latched"] = false, ["retry_safe"] = false
            },
            ["scope"] = new { kind = "catalogue_scom_create", backend = "native_sdk", set_membership_only = true,
                shared_sets = true, geometry_evaluated = false, engineering_validated = (bool?)null,
                savework_performed = false, atomic_snapshot = false }
        };

    private static Dictionary<string, object> ParagonCreateSet() => new Dictionary<string, object>
    { ["target"] = null, ["members"] = new List<Dictionary<string, object>>(), ["members_complete"] = false };

    private static Dictionary<string, object> ParagonCreateBefore() => new Dictionary<string, object>
    {
        ["module"] = null, ["context"] = null, ["parent"] = null,
        ["point_set"] = ParagonCreateSet(), ["geometry_set"] = ParagonCreateSet(),
        ["members"] = new List<Dictionary<string, object>>(), ["members_complete"] = false,
        ["name_lookup"] = new Dictionary<string, object> { ["status"] = "unknown", ["target"] = null }, ["creatable"] = null
    };

    private static Dictionary<string, object> ParagonCreateAfter() => new Dictionary<string, object>
    {
        ["module"] = null, ["context"] = null, ["parent"] = null,
        ["point_set"] = ParagonCreateSet(), ["geometry_set"] = ParagonCreateSet(),
        ["members"] = new List<Dictionary<string, object>>(), ["members_complete"] = false,
        ["new_identity"] = null, ["new_owner"] = null, ["new_leaf"] = null,
        ["new_template"] = null, ["parameter_bits"] = null
    };

    private static DbElement ReadParagonCreateSet(string reference, string type, Dictionary<string, object> evidence)
    {
        DbElement target = ParagonCopyResolve(reference);
        var identity = ComponentTemplateIdentity(target); evidence["target"] = identity;
        ParagonCopyRequire(Equals(identity["refno"], reference) && ParagonCopyType(identity, type),
            "reference_set_type_mismatch", "The explicit reference set must resolve to the same actual/base Catalogue " + type + ".");
        var rows = (List<Dictionary<string, object>>)evidence["members"];
        var seen = new HashSet<string>(StringComparer.Ordinal);
        for (int ordinal = 1; ordinal <= ParagonCreateSetMemberLimit + 1; ordinal++)
        {
            DbElement member = NativeRead(() => target.Member(ordinal));
            // Installed Member(int) documents CLR null for end-of-members;
            // some SDK paths instead return the native IsNull sentinel.
            if (ReferenceEquals(member, null) || NativeRead(() => member.IsNull))
            {
                ParagonCopyRequire(rows.Count > 0, "reference_set_empty", "A referenced PTSE/GMSE must have a nonempty bounded direct-member list.");
                evidence["members_complete"] = true;
                return target;
            }
            ParagonCopyRequire(ordinal <= ParagonCreateSetMemberLimit, "reference_set_member_limit",
                "The reference-set direct-member list exceeds 64 items; no extra member identity was inspected.");
            var core = ComponentTemplateCoreIdentity(member);
            var row = new Dictionary<string, object> { ["identity"] = core, ["owner"] = null }; rows.Add(row);
            ParagonCopyRequire(Equals(core["db_type_code"], 2) && seen.Add((string)core["refno"]),
                "reference_set_member_identity", "Reference-set members must have distinct native Catalogue identities.");
            var owner = ComponentTemplateCoreIdentity(NativeRead(() => member.Owner)); row["owner"] = owner;
            ParagonCopyRequire(ParagonCreateCoreEqual(identity, owner), "reference_set_member_owner",
                "Every direct reference-set member must have the explicit set as its native typed owner.");
        }
        throw new InvalidOperationException("The set member list did not terminate.");
    }

    private static bool ParagonCreateCoreEqual(Dictionary<string, object> first, Dictionary<string, object> second) =>
        first != null && second != null && new[] { "refno", "type", "db_type", "db_type_code", "identity_key" }
            .All(key => first.ContainsKey(key) && second.ContainsKey(key) && Equals(first[key], second[key]));

    private static DbAttribute ParagonCreateAttribute(string name, DbAttributeType type, bool array)
    {
        DbAttribute attribute = NativeRead(() => DbAttribute.GetDbAttribute(name));
        ParagonCopyRequire(!ReferenceEquals(attribute, null) && NativeRead(() => attribute.IsValid)
            && NativeRead(() => attribute.Type) == type && NativeRead(() => attribute.IsArray) == array,
            "initial_attribute_shape_unknown", name + " must have the required native attribute type and shape.");
        return attribute;
    }

    private static DbAttribute ParagonCreateNameAttribute()
    {
        // The fixed SDK NAME instance and its string setter are established
        // APIs. Do not infer NAME's registry type across installations.
        DbAttribute attribute = NativeRead(() => DbAttributeInstance.NAME);
        ParagonCopyRequire(!ReferenceEquals(attribute, null) && NativeRead(() => attribute.IsValid),
            "name_attribute_unknown", "The native NAME attribute instance must be valid.");
        return attribute;
    }

    private ParagonCreateCapture CaptureParagonCreate(ParagonComponentCreateRequest request,
        string session, string build, Dictionary<string, object> evidence)
    {
        var module = ParagonCopyModule(); evidence["module"] = module; ParagonCopyRequireModule(module);
        var context = ParagonCopyContext(session, build, out Project project, out MDB mdb); evidence["context"] = context;
        ParagonCopyRequire(!ReferenceEquals(project, null) && !ReferenceEquals(mdb, null)
            && ParagonCopyContextText(context["project"]) && ParagonCopyContextText(context["mdb"]) && context["current_element"] != null,
            "context_unknown", "A current project/MDB and typed CE are required.");
        DbElement parent = ParagonCopyResolve(request.ParentRefno);
        var parentIdentity = ComponentTemplateIdentity(parent); evidence["parent"] = parentIdentity;
        ParagonCopyRequire(Equals(parentIdentity["refno"], request.ParentRefno) && ParagonCopyType(parentIdentity, "CATE"),
            "parent_not_cate", "The explicit parent must resolve to the same actual/base Catalogue CATE.");
        DbElement point = ReadParagonCreateSet(request.PointRefno, "PTSE", (Dictionary<string, object>)evidence["point_set"]);
        DbElement geometry = ReadParagonCreateSet(request.GeometryRefno, "GMSE", (Dictionary<string, object>)evidence["geometry_set"]);
        DbElementType type = NativeRead(() => DbElementType.GetElementType("SCOM"));
        ParagonCopyRequire(!ReferenceEquals(type, null) && NativeRead(() => type.IsValid)
            && NativeRead(() => type.ShortName) == "SCOM", "scom_type_unknown", "The native actual SCOM creation type is required.");
        bool creatable = NativeRead(() => parent.IsCreatable(type)); evidence["creatable"] = creatable;
        ParagonCopyRequire(creatable, "not_creatable", "The SDK does not currently report SCOM creatability under the explicit CATE.");
        var members = (List<Dictionary<string, object>>)evidence["members"];
        ParagonCopyMembers(parent, ParagonCopyMaximumMembers, members, out _); evidence["members_complete"] = true;
        ParagonCopyRequire(!members.Any(row => string.Equals(row["name"] as string, request.Name, StringComparison.OrdinalIgnoreCase)),
            "name_present_in_parent", "The requested name is already present under the parent.");
        DbElement named = NativeRead(() => mdb.FindElement(DbType.Catalogue, request.Name));
        if (ReferenceEquals(named, null)) throw new InvalidOperationException("The Catalogue name lookup returned an unread null proxy.");
        bool isNull = NativeRead(() => named.IsNull);
        int[] pair = NativeRead(() => named.Ref);
        bool absent = isNull && pair != null && pair.Length == 2 && pair[0] == 0 && pair[1] == 0;
        var lookup = (Dictionary<string, object>)evidence["name_lookup"];
        if (absent) lookup["status"] = "absent";
        else if (!isNull) { lookup["status"] = "present"; lookup["target"] = ComponentTemplateIdentity(named); }
        ParagonCopyRequire(absent, "name_not_verified_absent", "The requested Catalogue name was not verified absent in the current MDB.");
        var initialAttributes = new[]
        {
            ParagonCreateNameAttribute(),
            ParagonCreateAttribute("PTREF", DbAttributeType.ELEMENT, false),
            ParagonCreateAttribute("GMREF", DbAttributeType.ELEMENT, false),
            ParagonCreateAttribute("PARAM", DbAttributeType.DOUBLE, true)
        };
        foreach (DbAttribute attribute in initialAttributes)
            ParagonCopyRequire(NativeRead(() => type.IsAttributeValid(attribute)), "scom_initial_attribute_unsupported",
                "The native SCOM type must support every fixed initial attribute before creation is attempted.");
        var finalContext = ParagonCopyContext(session, build, out Project finalProject, out MDB finalMdb);
        ParagonCopyRequire(ReferenceEquals(project, finalProject) && ReferenceEquals(mdb, finalMdb)
            && JToken.DeepEquals(JObject.FromObject(context), JObject.FromObject(finalContext)),
            "preflight_context_changed", "The project/MDB/typed CE changed during the preflight observation.");
        ParagonCopyRequireModule(ParagonCopyModule());
        return new ParagonCreateCapture { Project = project, Mdb = mdb, Parent = parent, PointSet = point,
            GeometrySet = geometry, Type = type, Evidence = evidence, Members = members };
    }

    // No NAME read is required to prove the fresh append before naming the new
    // object. Only explicit native refs/types and owners authorize setters.
    private static bool ParagonCreateFreshAppend(DbElement parent, List<Dictionary<string, object>> before, string created)
    {
        var seen = new HashSet<string>(StringComparer.Ordinal);
        for (int ordinal = 1; ordinal <= before.Count + 2; ordinal++)
        {
            DbElement member = NativeRead(() => parent.Member(ordinal));
            bool end = ReferenceEquals(member, null) || NativeRead(() => member.IsNull);
            if (ordinal == before.Count + 2) return end;
            if (end) return false;
            var core = ComponentTemplateCoreIdentity(member);
            if (!Equals(core["db_type_code"], 2) || !seen.Add((string)core["refno"])) return false;
            if (ordinal <= before.Count) { if (!ParagonCreateCoreEqual(before[ordinal - 1], core)) return false; }
            else if (!Equals(core["refno"], created) || !Equals(core["type"], "SCOM")) return false;
        }
        return false;
    }

    private static void ParagonCreateSettable(DbElement created, DbAttribute attribute)
    {
        ParagonCopyRequire(NativeRead(() => created.IsAttributeValid(attribute)) && NativeRead(() => created.IsAttributeSetable(attribute)),
            "initial_attribute_not_setable", "The requested initial attribute is not valid and setable on the verified fresh SCOM.");
    }

    private void RunParagonCreate(ParagonComponentCreateRequest request, ParagonCreateCapture captured,
        string session, string build, Dictionary<string, object> after, Dictionary<string, object> progress)
    {
        DbElement returned = null;
        NativeWrite(delegate
        {
            progress["creation_attempted"] = true;
            returned = captured.Parent.CreateLast(captured.Type);
            progress["creation_returned"] = true;
        });
        if (ReferenceEquals(returned, null)) throw new InvalidOperationException("CreateLast returned an unread null proxy.");
        // Store the returned numeric REF as early as possible. A late getter,
        // setter or native fault cannot erase it or turn it into a retry hint.
        string reference = ParagonComponentTemplateRequest.Reference(NativeRead(() => returned.Ref));
        progress["known_new_refno"] = reference; progress["known_new_refno_verified"] = false;
        var identity = ComponentTemplateIdentity(returned); after["new_identity"] = identity;
        var owner = ComponentTemplateIdentity(NativeRead(() => returned.Owner)); after["new_owner"] = owner;
        var leaf = ComponentTemplateLeaf(returned); after["new_leaf"] = leaf;
        bool fresh = reference != request.ParentRefno && reference != request.PointRefno && reference != request.GeometryRefno
            && !captured.Members.Any(row => Equals(row["refno"], reference));
        ParagonCopyRequire(fresh && Equals(identity["refno"], reference) && ParagonCopyType(identity, "SCOM")
            && Equals(owner["refno"], request.ParentRefno) && ParagonCopyType(owner, "CATE")
            && Equals(leaf["is_leaf"], true) && ParagonCreateFreshAppend(captured.Parent, captured.Members, reference),
            "created_identity_unverified", "CreateLast did not return a verified fresh actual/base Catalogue SCOM leaf appended under the explicit parent; no setter was entered.");
        progress["known_new_refno_verified"] = true;
        // Do not knowingly begin a partial initialization when a later fixed
        // initial attribute is already unsupported or unsetable.
        DbAttribute nameAttribute = ParagonCreateNameAttribute();
        DbAttribute pointAttribute = ParagonCreateAttribute("PTREF", DbAttributeType.ELEMENT, false);
        DbAttribute geometryAttribute = ParagonCreateAttribute("GMREF", DbAttributeType.ELEMENT, false);
        DbAttribute parameterAttribute = ParagonCreateAttribute("PARAM", DbAttributeType.DOUBLE, true);
        foreach (DbAttribute initial in new[] { nameAttribute, pointAttribute, geometryAttribute, parameterAttribute })
            ParagonCreateSettable(returned, initial);
        // CreateLast can synchronously invoke native/application callbacks.
        // A verified new REF does not prove that the module or the native
        // project/MDB/typed-CE boundary survived until initialization.
        ParagonCopyRequireModule(ParagonCopyModule());
        var context = ParagonCopyContext(session, build, out Project project, out MDB mdb); after["context"] = context;
        ParagonCopyRequire(ReferenceEquals(project, captured.Project) && ReferenceEquals(mdb, captured.Mdb)
            && JToken.DeepEquals(JObject.FromObject(context), JObject.FromObject(captured.Evidence["context"])),
            "created_context_changed", "The module/project/MDB/typed CE changed during CreateLast; no initial setter was entered.");
        DbAttribute attribute = nameAttribute; ParagonCreateSettable(returned, attribute);
        NativeWrite(delegate { progress["name_attempted"] = true; returned.SetAttribute(attribute, request.Name); progress["name_returned"] = true; });
        attribute = pointAttribute; ParagonCreateSettable(returned, attribute);
        NativeWrite(delegate { progress["point_ref_attempted"] = true; returned.SetAttribute(attribute, captured.PointSet); progress["point_ref_returned"] = true; });
        attribute = geometryAttribute; ParagonCreateSettable(returned, attribute);
        NativeWrite(delegate { progress["geometry_ref_attempted"] = true; returned.SetAttribute(attribute, captured.GeometrySet); progress["geometry_ref_returned"] = true; });
        attribute = parameterAttribute; ParagonCreateSettable(returned, attribute);
        NativeWrite(delegate { progress["parameters_attempted"] = true; returned.SetAttribute(attribute, request.Parameters); progress["parameters_returned"] = true; });
    }

    private static JToken ParagonCreateSetSlice(Dictionary<string, object> evidence)
    {
        if (evidence == null || !Equals(evidence["members_complete"], true)) return null;
        var target = evidence["target"] as Dictionary<string, object>;
        if (target == null || !ParagonCopyType(target, (string)target["type"])) return null;
        var rows = (List<Dictionary<string, object>>)evidence["members"];
        if (rows.Count == 0 || rows.Count > ParagonCreateSetMemberLimit || rows.Any(row => row["owner"] == null)) return null;
        return JObject.FromObject(new { target = ParagonCreateIdentitySlice(target), members = rows });
    }

    private static object ParagonCreateIdentitySlice(Dictionary<string, object> identity) => new
    {
        refno = identity["refno"], type = identity["type"], db_type = identity["db_type"],
        db_type_code = identity["db_type_code"], identity_key = identity["identity_key"],
        actual_type = ((Dictionary<string, object>)identity["actual_type"])["short_name"],
        base_type = ((Dictionary<string, object>)identity["base_type"])["short_name"]
    };

    private static bool? ParagonCreateReferenceMatches(Dictionary<string, object> template, string attribute,
        Dictionary<string, object> expected)
    {
        var values = (Dictionary<string, object>)template["references"];
        var value = values[attribute] as Dictionary<string, object>;
        JToken slice = ParagonCopyReferenceValue(value);
        if (slice == null || slice.Type != JTokenType.Object) return null;
        if (!Equals(value["shape"], "element") || !Equals(value["is_array"], false)) return false;
        var rows = (List<Dictionary<string, object>>)value["values"];
        if (rows.Count != 1) return false;
        var observed = rows[0]["identity"] as Dictionary<string, object>;
        if (observed == null) return false;
        return JToken.DeepEquals(JToken.FromObject(ParagonCreateIdentitySlice(observed)), JToken.FromObject(ParagonCreateIdentitySlice(expected)));
    }

    private void ReadBackParagonCreate(ParagonComponentCreateRequest request, ParagonCreateCapture captured,
        string session, string build, Dictionary<string, object> after, Dictionary<string, object> progress,
        List<Dictionary<string, object>> errors)
    {
        bool moduleKnown = false, contextKnown = false, parentKnown = false, newKnown = false, templateKnown = false;
        DbElement afterParent = null;
        ParagonCopyReadbackStage("after.module", errors, delegate
        {
            var module = ParagonCopyModule(); after["module"] = module; ParagonCopyRequireModule(module); moduleKnown = true;
        });
        if (!moduleKnown) return;
        ParagonCopyReadbackStage("after.context", errors, delegate
        {
            var context = ParagonCopyContext(session, build, out Project project, out MDB mdb); after["context"] = context;
            bool same = ReferenceEquals(project, captured.Project) && ReferenceEquals(mdb, captured.Mdb)
                && JToken.DeepEquals(JObject.FromObject(context), JObject.FromObject(captured.Evidence["context"]));
            progress["context_unchanged"] = same;
            ParagonCopyRequire(same, "post_context_changed", "The native project/MDB/typed CE changed across creation."); contextKnown = true;
        });
        if (!contextKnown) return;
        ParagonCopyReadbackStage("after.parent", errors, delegate
        {
            afterParent = ParagonCopyResolve(request.ParentRefno);
            var parent = ComponentTemplateIdentity(afterParent); after["parent"] = parent;
            ParagonCopyRequire(Equals(parent["refno"], request.ParentRefno) && ParagonCopyType(parent, "CATE"),
                "post_parent_changed", "The parent no longer binds to the explicit actual/base CATE."); parentKnown = true;
        });
        ParagonCopyReadbackStage("after.point_set", errors, delegate
        { ReadParagonCreateSet(request.PointRefno, "PTSE", (Dictionary<string, object>)after["point_set"]); });
        ParagonCopyReadbackStage("after.geometry_set", errors, delegate
        { ReadParagonCreateSet(request.GeometryRefno, "GMSE", (Dictionary<string, object>)after["geometry_set"]); });
        var pointBefore = ParagonCreateSetSlice((Dictionary<string, object>)captured.Evidence["point_set"]);
        var geometryBefore = ParagonCreateSetSlice((Dictionary<string, object>)captured.Evidence["geometry_set"]);
        var pointAfter = ParagonCreateSetSlice((Dictionary<string, object>)after["point_set"]);
        var geometryAfter = ParagonCreateSetSlice((Dictionary<string, object>)after["geometry_set"]);
        if (pointBefore != null && geometryBefore != null && pointAfter != null && geometryAfter != null)
            progress["sets_unchanged_scoped"] = JToken.DeepEquals(pointBefore, pointAfter) && JToken.DeepEquals(geometryBefore, geometryAfter);
        ParagonCopyReadbackStage("after.members", errors, delegate
        {
            if (!parentKnown) return;
            var members = (List<Dictionary<string, object>>)after["members"];
            ParagonCopyMembers(afterParent, ParagonCopyMaximumMembers + 1, members, out _); after["members_complete"] = true;
            bool append = members.Count == captured.Members.Count + 1 && progress["known_new_refno"] is string;
            for (int index = 0; append && index < captured.Members.Count; index++)
                append &= ParagonCreateCoreEqual(captured.Members[index], members[index]) && Equals(captured.Members[index]["name"], members[index]["name"]);
            if (append)
            {
                var last = members[members.Count - 1];
                append = Equals(last["refno"], progress["known_new_refno"]) && Equals(last["type"], "SCOM")
                    && string.Equals(last["name"] as string, request.Name, StringComparison.OrdinalIgnoreCase);
            }
            progress["parent_append"] = append;
        });
        if (Equals(progress["known_new_refno_verified"], true))
        {
            string created = (string)progress["known_new_refno"];
            ParagonCopyReadbackStage("after.new_identity", errors, delegate
            {
                DbElement element = ParagonCopyResolve(created);
                var identity = ComponentTemplateIdentity(element); after["new_identity"] = identity;
                var owner = ComponentTemplateIdentity(NativeRead(() => element.Owner)); after["new_owner"] = owner;
                var leaf = ComponentTemplateLeaf(element); after["new_leaf"] = leaf;
                ParagonCopyRequire(Equals(identity["refno"], created) && ParagonCopyType(identity, "SCOM")
                    && string.Equals(identity["name"] as string, request.Name, StringComparison.OrdinalIgnoreCase)
                    && Equals(owner["refno"], request.ParentRefno) && ParagonCopyType(owner, "CATE") && Equals(leaf["is_leaf"], true),
                    "new_identity_readback_mismatch", "The new numeric REF/name/actual type/owner/leaf did not match the creation request."); newKnown = true;
            });
            ParagonCopyReadbackStage("after.new_template", errors, delegate
            {
                var template = ReadParagonComponentTemplateCore(ParagonComponentTemplateRequest.Validate(created), session, build,
                    out ParagonComponentTemplateCapture observed); after["new_template"] = template;
                var bits = ParagonCopyNumbers(template); after["parameter_bits"] = bits?.ToArray();
                if (bits != null) progress["parameters_equal_request"] = bits.SequenceEqual(request.ParameterBits);
                bool? point = ParagonCreateReferenceMatches(template, "PTREF", (Dictionary<string, object>)((Dictionary<string, object>)captured.Evidence["point_set"])["target"]);
                bool? geometry = ParagonCreateReferenceMatches(template, "GMREF", (Dictionary<string, object>)((Dictionary<string, object>)captured.Evidence["geometry_set"])["target"]);
                progress["references_equal_request"] = point == false || geometry == false ? false : point == true && geometry == true ? true : (bool?)null;
                var target = template["target"] as Dictionary<string, object>;
                ParagonCopyRequire(observed != null && ReferenceEquals(observed.OriginalProject, captured.Project)
                    && ReferenceEquals(observed.OriginalMdb, captured.Mdb) && ParagonCopyTemplateContextMatches(template, (Dictionary<string, object>)after["context"])
                    && target != null && Equals(target["refno"], created) && ParagonCopyType(target, "SCOM")
                    && string.Equals(target["name"] as string, request.Name, StringComparison.OrdinalIgnoreCase)
                    && Equals(((Dictionary<string, object>)template["leaf"])["is_leaf"], true),
                    "new_template_readback_unknown", "The independent template readback did not preserve the new identity/name/leaf/context."); templateKnown = true;
            });
        }
        ParagonCopyReadbackStage("after.final_context", errors, delegate
        {
            var context = ParagonCopyContext(session, build, out Project project, out MDB mdb);
            bool same = ReferenceEquals(project, captured.Project) && ReferenceEquals(mdb, captured.Mdb)
                && JToken.DeepEquals(JObject.FromObject(context), JObject.FromObject(captured.Evidence["context"]));
            if (!same) progress["context_unchanged"] = false;
            ParagonCopyRequire(same, "readback_context_changed", "The project/MDB/typed CE changed during the independent readback.");
            ParagonCopyRequireModule(ParagonCopyModule());
        });
        progress["readback_complete"] = errors.Count == 0 && parentKnown && newKnown && templateKnown
            && Equals(after["members_complete"], true) && Equals(progress["parent_append"], true)
            && Equals(progress["context_unchanged"], true) && Equals(progress["sets_unchanged_scoped"], true)
            && Equals(progress["parameters_equal_request"], true) && Equals(progress["references_equal_request"], true);
    }
}
