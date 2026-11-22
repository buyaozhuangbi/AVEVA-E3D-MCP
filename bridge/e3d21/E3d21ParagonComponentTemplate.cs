using System;
using System.Collections.Generic;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context : IE3d21ParagonComponentTemplateContext
{
    public object ReadParagonComponentTemplate(string refno, string sessionId)
    {
        var request = ParagonComponentTemplateRequest.Validate(refno);
        if (string.IsNullOrWhiteSpace(sessionId) || sessionId.Length > 256)
            throw new ArgumentException("A server-owned session identity is required.", "sessionId");
        string build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy.");
        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy.");
            return ReadParagonComponentTemplateCore(request, sessionId, build, out _);
        });
    }

    // This capture is private, never part of a receipt, and has no public
    // serializable members. It can only be reused inside this context's
    // existing UI/native scope; SDK handles must never cross the HTTP boundary.
    private sealed class ParagonComponentTemplateCapture
    {
        internal readonly DbElement Source;
        internal readonly Project OriginalProject;
        internal readonly MDB OriginalMdb;
        internal readonly Dictionary<string, object> VerifiedFirstBinding;

        internal ParagonComponentTemplateCapture(DbElement source, Project project, MDB mdb,
            Dictionary<string, object> binding)
        {
            Source = source;
            OriginalProject = project;
            OriginalMdb = mdb;
            VerifiedFirstBinding = new Dictionary<string, object>(binding, StringComparer.Ordinal);
        }
    }

    // The caller owns the one OnUi scope and any command gate. Do not dispatch
    // or inspect that gate here: a controlled writer may already own it.
    private Dictionary<string, object> ReadParagonComponentTemplateCore(
        ParagonComponentTemplateRequest request, string sessionId, string build,
        out ParagonComponentTemplateCapture capture)
    {
        capture = null;
        if (!ReferenceEquals(currentNativeFaultState, nativeFaultState))
            throw new InvalidOperationException("The component template core requires this context's active E3D SDK scope.");
        ThrowIfNativeFaulted();
        var before = ComponentTemplateBoundary(sessionId, build, out Project project, out MDB mdb);
        var errors = new List<Dictionary<string, object>>();
        Dictionary<string, object> target = null;
        var leaf = ComponentTemplateEmptyLeaf();
        var parameters = ComponentTemplateEmptyAttribute("PARAM", "double_array");
        var references = new Dictionary<string, object>();
        foreach (string name in ParagonComponentTemplateRequest.ReferenceAttributes)
            references[name] = ComponentTemplateEmptyAttribute(name, "element_or_element_array");
        try
        {
            if (ReferenceEquals(project, null) || ReferenceEquals(mdb, null))
                throw new InvalidOperationException("A current project and MDB are required to resolve the explicit template.");
            DbElement element = NativeRead(() => DbElement.GetElement(request.Pair));
            // The first resolution must prove Catalogue + numeric DBREF +
            // base SCOM before any parameter/reference value is read.
            var first = ComponentTemplateCoreIdentity(element);
            if (!Equals(first["refno"], request.Refno) || !Equals(first["db_type_code"], 2) || !Equals(first["type"], "SCOM"))
                throw new InvalidOperationException("The explicit template must resolve to the same numeric Catalogue SCOM base type.");
            var binding = new Dictionary<string, object>
            {
                ["source"] = "explicit_request_resolution", ["requested_refno"] = request.Refno,
                ["resolved_refno"] = first["refno"], ["observed_name"] = null, ["verified"] = true
            };
            capture = new ParagonComponentTemplateCapture(element, project, mdb, binding);
            target = ComponentTemplateIdentity(element, binding);
            if (Equals(target["refno"], request.Refno) && Equals(target["db_type_code"], 2)
                && Equals(target["type"], "SCOM"))
            {
                leaf = ComponentTemplateLeaf(element);
                parameters = ComponentTemplateParameters(element);
                foreach (string name in ParagonComponentTemplateRequest.ReferenceAttributes)
                    references[name] = ComponentTemplateReferences(element, name);
            }
            else ((Dictionary<string, string>)target["errors"])["request_binding"] = "The bound target identity changed during the read; values were not read.";
        }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error) { ThrowIfNativeFaulted(); errors.Add(ComponentTemplateReadError("target", error)); }
        var after = ComponentTemplateBoundary(sessionId, build, out Project afterProject, out MDB afterMdb);
        bool? sameProject = ReferenceEquals(project, null) || ReferenceEquals(afterProject, null) ? (bool?)null : ReferenceEquals(project, afterProject);
        bool? sameMdb = ReferenceEquals(mdb, null) || ReferenceEquals(afterMdb, null) ? (bool?)null : ReferenceEquals(mdb, afterMdb);
        bool? agreement = ComponentTemplateAgreement(before, after, sameProject, sameMdb);
        bool complete = target != null && Equals(target["complete"], true) && Equals(leaf["complete"], true)
            && Equals(parameters["complete"], true) && errors.Count == 0 && agreement == true;
        foreach (string name in ParagonComponentTemplateRequest.ReferenceAttributes)
            complete &= Equals(((Dictionary<string, object>)references[name])["complete"], true);
        return new Dictionary<string, object>
        {
            ["operation"] = "paragon_component_template", ["available"] = true, ["success"] = complete, ["complete"] = complete,
            ["read_only"] = true, ["single_dispatch"] = true, ["atomic_snapshot"] = false,
            ["session_id"] = sessionId, ["bridge_build"] = build, ["requested_refno"] = request.Refno,
            ["expected_base_type"] = "SCOM", ["target"] = target, ["leaf"] = leaf,
            ["parameters"] = parameters, ["references"] = references, ["read_errors"] = errors,
            ["context"] = new { before, after, same_project_instance = sameProject, same_mdb_instance = sameMdb, boundary_agreement = agreement },
            ["output_limits"] = new { maximum_parameters = ParagonComponentTemplateRequest.MaximumParameters,
                maximum_reference_items = ParagonComponentTemplateRequest.MaximumReferenceItems,
                native_array_inspection_limit = ParagonComponentTemplateRequest.MaximumNativeArrayInspection,
                maximum_text_length = ParagonComponentTemplateRequest.MaximumTextLength,
                native_arrays_materialized_before_output_limits = true, native_array_allocation_bounded = false },
            ["scope"] = new { kind = "fixed_catalogue_scom_template_observations", database_type = "Catalogue", database_type_code = 2,
                actual_type_observed_separately = true, reference_hops = 1, reference_targets_expanded = false,
                word_parameters_observed = false, hidden_attributes_observed = false, all_attributes_covered = false,
                all_references_covered = false, whole_clone_fingerprint = false, geometry_observed = false,
                geometry_created = false, model_written = false, permissions_verified = (bool?)null,
                write_permission_verified = (bool?)null, engineering_validated = (bool?)null, whole_project_coverage = false }
        };
    }

    private static Dictionary<string, object> ComponentTemplateReadError(string stage, Exception error) =>
        new Dictionary<string, object> { ["stage"] = stage, ["message"] = ComponentTemplateError(error) };
    private static string ComponentTemplateError(Exception error) => ParagonComponentTemplateRequest.ErrorText(error.Message);
    private static string ComponentTemplateText(string text, int limit)
    {
        if (string.IsNullOrWhiteSpace(text) || text.Length > limit)
            throw new InvalidOperationException("The SDK returned null, blank, or over-budget identity text.");
        return text;
    }
    private static string ComponentTemplateShortName(string text)
    {
        text = ComponentTemplateText(text, 64);
        foreach (char character in text)
            if (!(character >= 'A' && character <= 'Z') && !(character >= 'a' && character <= 'z')
                && !(character >= '0' && character <= '9') && character != '_' && character != ':')
                throw new InvalidOperationException("The SDK returned an invalid type short name.");
        return text;
    }
    private static Dictionary<string, object> ComponentTemplateType(DbElementType type, string source)
    {
        var errors = new Dictionary<string, string>();
        var result = new Dictionary<string, object> { ["source"] = source, ["name"] = null,
            ["short_name"] = null, ["status"] = "unknown", ["errors"] = errors };
        try
        {
            if (ReferenceEquals(type, null) || !NativeRead(() => type.IsValid)) throw new InvalidOperationException("No valid native type was returned.");
            try { result["name"] = ComponentTemplateText(NativeRead(() => type.Name), 256); }
            catch (NativeDatabaseFaultException) { throw; }
            catch (Exception error) { ThrowIfNativeFaulted(); errors["name"] = ComponentTemplateError(error); }
            try { result["short_name"] = ComponentTemplateShortName(NativeRead(() => type.ShortName)); }
            catch (NativeDatabaseFaultException) { throw; }
            catch (Exception error) { ThrowIfNativeFaulted(); errors["short_name"] = ComponentTemplateError(error); }
        }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["type"] = ComponentTemplateError(error); }
        if (errors.Count == 0) result["status"] = "observed";
        return result;
    }
    private static Dictionary<string, object> ComponentTemplateCoreIdentity(DbElement element)
    {
        if (ReferenceEquals(element, null) || NativeRead(() => element.IsNull) || !NativeRead(() => element.IsValid)
            || NativeRead(() => element.IsDeleted)) throw new InvalidOperationException("The native element is null, invalid, or deleted.");
        string reference = ParagonComponentTemplateRequest.Reference(NativeRead(() => element.Ref));
        DbType databaseType = NativeRead(() => element.DbType);
        DbElementType type = NativeRead(() => element.GetElementType());
        if (ReferenceEquals(type, null) || !NativeRead(() => type.IsValid)) throw new InvalidOperationException("No valid native base type was returned.");
        string shortName = ComponentTemplateShortName(NativeRead(() => type.ShortName));
        return new Dictionary<string, object> { ["refno"] = reference, ["type"] = shortName,
            ["db_type"] = (int)databaseType == 2 ? "Catalogue" : databaseType.ToString(),
            ["db_type_code"] = (int)databaseType, ["identity_key"] = reference + "|" + shortName };
    }
    private static Dictionary<string, object> ComponentTemplateIdentity(DbElement element, Dictionary<string, object> binding = null)
    {
        var result = ComponentTemplateCoreIdentity(element);
        var errors = new Dictionary<string, string>();
        result["name"] = null; result["name_error"] = null; result["request_binding"] = binding;
        result["base_type"] = null; result["actual_type"] = null; result["errors"] = errors;
        try { result["name"] = ComponentTemplateText(NativeRead(() => element.GetAsString(DbAttributeInstance.NAME)), ParagonComponentTemplateRequest.MaximumTextLength); }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error) { ThrowIfNativeFaulted(); result["name_error"] = errors["name"] = ComponentTemplateError(error); }
        try { result["base_type"] = ComponentTemplateType(NativeRead(() => element.GetElementType()), "DbElement.GetElementType"); }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["base_type"] = ComponentTemplateError(error); }
        try { result["actual_type"] = ComponentTemplateType(NativeRead(() => element.GetActualType()), "DbElement.GetActualType"); }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["actual_type"] = ComponentTemplateError(error); }
        var baseType = result["base_type"] as Dictionary<string, object>;
        var actualType = result["actual_type"] as Dictionary<string, object>;
        if (baseType != null && baseType["short_name"] != null && !Equals(result["type"], baseType["short_name"]))
            errors["base_type"] = "Native base type observations disagree during this read.";
        if (binding != null && (!Equals(binding["resolved_refno"], result["refno"]) || !Equals(result["db_type_code"], 2) || !Equals(result["type"], "SCOM")))
            errors["request_binding"] = "The current identity disagrees with the first verified numeric Catalogue SCOM resolution.";
        result["complete"] = errors.Count == 0 && baseType != null && Equals(baseType["status"], "observed")
            && actualType != null && Equals(actualType["status"], "observed");
        return result;
    }
    private static Dictionary<string, object> ComponentTemplateEmptyLeaf() => new Dictionary<string, object>
    {
        ["source"] = "DbElement.Member(1)", ["status"] = "unknown", ["is_leaf"] = null,
        ["first_member"] = null, ["complete"] = false, ["errors"] = new Dictionary<string, string>()
    };
    private static Dictionary<string, object> ComponentTemplateLeaf(DbElement element)
    {
        var result = ComponentTemplateEmptyLeaf(); var errors = (Dictionary<string, string>)result["errors"];
        try
        {
            DbElement member = NativeRead(() => element.Member(1));
            bool empty = ReferenceEquals(member, null) || NativeRead(() => member.IsNull);
            result["is_leaf"] = empty;
            if (!empty) result["first_member"] = ComponentTemplateIdentity(member);
            result["status"] = "observed";
            result["complete"] = empty || Equals(((Dictionary<string, object>)result["first_member"])["complete"], true);
        }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["member"] = ComponentTemplateError(error); }
        return result;
    }
    private static Dictionary<string, object> ComponentTemplateEmptyAttribute(string name, string expectedShape) => new Dictionary<string, object>
    {
        ["requested_name"] = name, ["expected_shape"] = expectedShape, ["status"] = "unknown", ["supported"] = null,
        ["sdk_type"] = null, ["is_array"] = null, ["shape"] = null, ["reference_null"] = null,
        ["unit_kind"] = null, ["unit"] = null, ["unit_status"] = "unknown",
        ["total_count"] = null, ["returned_count"] = 0, ["truncated"] = null,
        ["inspection_limited"] = null, ["native_array_materialized"] = false,
        ["values"] = new List<Dictionary<string, object>>(), ["errors"] = new Dictionary<string, string>(), ["complete"] = false
    };
    private static DbAttribute ComponentTemplateAttribute(DbElement element, Dictionary<string, object> result)
    {
        var errors = (Dictionary<string, string>)result["errors"];
        DbAttribute attribute;
        try
        {
            attribute = NativeRead(() => DbAttribute.GetDbAttribute((string)result["requested_name"]));
            if (ReferenceEquals(attribute, null) || !NativeRead(() => attribute.IsValid)) throw new InvalidOperationException("No valid native attribute was returned.");
            result["supported"] = NativeRead(() => element.IsAttributeValid(attribute));
            if (!Equals(result["supported"], true)) { errors["support"] = "unsupported_attribute_value_unknown"; return null; }
        }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["attribute"] = ComponentTemplateError(error); return null; }
        try { result["sdk_type"] = NativeRead(() => attribute.Type).ToString(); }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["sdk_type"] = ComponentTemplateError(error); }
        try { result["is_array"] = NativeRead(() => attribute.IsArray); }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["is_array"] = ComponentTemplateError(error); }
        try { result["unit_kind"] = NativeRead(() => attribute.Units).ToString(); }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["unit_kind"] = ComponentTemplateError(error); }
        return attribute;
    }
    private static bool ComponentTemplateArrayBudget(Dictionary<string, object> result, int length, int outputLimit)
    {
        result["total_count"] = length; result["native_array_materialized"] = true;
        bool limited = length > ParagonComponentTemplateRequest.MaximumNativeArrayInspection;
        result["inspection_limited"] = limited;
        result["truncated"] = length > outputLimit;
        if (limited) ((Dictionary<string, string>)result["errors"])["inspection_limit"] = "The fully materialized native array exceeds the inspection limit; no array items were inspected.";
        return !limited;
    }
    private static Dictionary<string, object> ComponentTemplateParameters(DbElement element)
    {
        var result = ComponentTemplateEmptyAttribute("PARAM", "double_array");
        var errors = (Dictionary<string, string>)result["errors"];
        DbAttribute attribute = ComponentTemplateAttribute(element, result);
        if (ReferenceEquals(attribute, null)) return result;
        try
        {
            if (!Equals(result["sdk_type"], "DOUBLE") || !Equals(result["is_array"], true))
                throw new InvalidOperationException("PARAM native DOUBLE array shape was not observed; no value getter was called.");
            result["shape"] = "double_array";
            double[] native = NativeRead(() => element.GetDoubleArray(attribute));
            if (native == null) throw new InvalidOperationException("The native PARAM array is null; its value remains unknown.");
            if (!ComponentTemplateArrayBudget(result, native.Length, ParagonComponentTemplateRequest.MaximumParameters)) return result;
            var values = (List<Dictionary<string, object>>)result["values"];
            for (int index = 0; index < Math.Min(native.Length, ParagonComponentTemplateRequest.MaximumParameters); index++)
            {
                double value = native[index]; bool finite = !double.IsNaN(value) && !double.IsInfinity(value);
                values.Add(new Dictionary<string, object> { ["index"] = index, ["value"] = finite ? (object)value : null,
                    ["status"] = finite ? "observed" : "unknown", ["error"] = finite ? null : "non_finite_native_value" });
                if (!finite) errors["non_finite"] = "At least one returned PARAM item is non-finite; it remains unknown.";
            }
            result["returned_count"] = values.Count;
            if (Equals(result["truncated"], true)) errors["output_limit"] = "Only the bounded PARAM prefix was returned.";
            result["complete"] = errors.Count == 0;
            result["status"] = errors.Count == 0 ? "observed" : "partial";
        }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["value"] = ComponentTemplateError(error); }
        return result;
    }
    private static Dictionary<string, object> ComponentTemplateReferences(DbElement element, string name)
    {
        var result = ComponentTemplateEmptyAttribute(name, "element_or_element_array");
        var errors = (Dictionary<string, string>)result["errors"];
        DbAttribute attribute = ComponentTemplateAttribute(element, result);
        if (ReferenceEquals(attribute, null)) return result;
        try
        {
            if (!Equals(result["sdk_type"], "ELEMENT") || !(result["is_array"] is bool))
                throw new InvalidOperationException("Native ELEMENT scalar/array shape was not observed; no value getter was called.");
            bool array = (bool)result["is_array"];
            result["shape"] = array ? "element_array" : "element";
            DbElement[] native;
            if (array)
            {
                native = NativeRead(() => element.GetElementArray(attribute));
                if (native == null) { result["reference_null"] = true; throw new InvalidOperationException("The native reference array is null; its value remains unknown."); }
                if (!ComponentTemplateArrayBudget(result, native.Length, ParagonComponentTemplateRequest.MaximumReferenceItems)) return result;
            }
            else
            {
                native = new[] { NativeRead(() => element.GetElement(attribute)) };
                result["total_count"] = 1; result["truncated"] = false; result["inspection_limited"] = false;
            }
            var values = (List<Dictionary<string, object>>)result["values"];
            for (int index = 0; index < Math.Min(native.Length, ParagonComponentTemplateRequest.MaximumReferenceItems); index++)
            {
                var row = new Dictionary<string, object> { ["index"] = index, ["status"] = "unknown", ["identity"] = null, ["reference_null"] = null, ["error"] = null };
                values.Add(row);
                try
                {
                    DbElement referenced = native[index];
                    bool empty = ReferenceEquals(referenced, null) || NativeRead(() => referenced.IsNull);
                    row["reference_null"] = empty;
                    if (!array) result["reference_null"] = empty;
                    if (empty) throw new InvalidOperationException("native_null_reference_value_unknown");
                    var identity = ComponentTemplateIdentity(referenced);
                    row["identity"] = identity;
                    row["status"] = Equals(identity["complete"], true) ? "observed" : "partial";
                    if (!Equals(identity["complete"], true)) row["error"] = "Reference identity metadata remains incomplete.";
                }
                catch (NativeDatabaseFaultException) { throw; }
                catch (Exception error) { ThrowIfNativeFaulted(); row["error"] = ComponentTemplateError(error); }
                if (!Equals(row["status"], "observed")) errors["items"] = "At least one returned reference identity remains unknown or partial.";
            }
            result["returned_count"] = values.Count;
            if (Equals(result["truncated"], true)) errors["output_limit"] = "Only the bounded reference-array prefix was returned.";
            result["complete"] = errors.Count == 0;
            result["status"] = errors.Count == 0 ? "observed" : "partial";
        }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["value"] = ComponentTemplateError(error); }
        return result;
    }
    private static Dictionary<string, object> ComponentTemplateBoundary(string session, string build, out Project project, out MDB mdb)
    {
        project = null; mdb = null; string projectName = null, mdbName = null; Dictionary<string, object> ce = null;
        var errors = new Dictionary<string, string>();
        try { project = NativeRead(() => Project.CurrentProject); Project observed = project;
            projectName = ComponentTemplateText(ReferenceEquals(observed, null) ? null : NativeRead(() => observed.Name), 256); }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["project"] = ComponentTemplateError(error); }
        try { mdb = NativeRead(() => MDB.CurrentMDB); MDB observed = mdb;
            mdbName = ComponentTemplateText(ReferenceEquals(observed, null) ? null : NativeRead(() => observed.Name), 256); }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["mdb"] = ComponentTemplateError(error); }
        try { ce = ComponentTemplateCoreIdentity(NativeRead(() => Aveva.Core.Database.CurrentElement.Element)); }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["current_element"] = ComponentTemplateError(error); }
        return new Dictionary<string, object> { ["session_id"] = session, ["bridge_build"] = build,
            ["project"] = projectName, ["mdb"] = mdbName, ["current_element"] = ce, ["errors"] = errors };
    }
    private static bool? ComponentTemplateAgreement(Dictionary<string, object> before, Dictionary<string, object> after, bool? sameProject, bool? sameMdb)
    {
        if (sameProject == false || sameMdb == false) return false;
        foreach (string name in new[] { "session_id", "bridge_build", "project", "mdb" })
            if (before[name] != null && after[name] != null && !Equals(before[name], after[name])) return false;
        var first = before["current_element"] as Dictionary<string, object>; var last = after["current_element"] as Dictionary<string, object>;
        if (first != null && last != null)
            foreach (string name in new[] { "identity_key", "db_type", "db_type_code" }) if (!Equals(first[name], last[name])) return false;
        if (sameProject == null || sameMdb == null || first == null || last == null
            || ((Dictionary<string, string>)before["errors"]).Count != 0 || ((Dictionary<string, string>)after["errors"]).Count != 0) return null;
        return true;
    }
}
