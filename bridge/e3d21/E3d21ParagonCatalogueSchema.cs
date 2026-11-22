using System;
using System.Collections.Generic;
using System.Globalization;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context : IE3d21ParagonCatalogueSchemaContext
{
    public object ReadParagonCatalogueSchema(string refno, IList<string> attributes, bool includeConstraints, string sessionId)
    {
        var request = ParagonCatalogueSchemaRequest.Validate(refno, attributes, includeConstraints);
        ParagonManifestContextText(sessionId, "bridge session");
        string build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy.");
        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy.");
            var before = ParagonManifestBoundary(sessionId, build, out Project project, out MDB mdb);
            if (mdb == null && request.Refno[0] != '=')
                throw new InvalidOperationException("An MDB is required to resolve a named catalogue target.");
            DbElement element;
            if (request.Refno[0] == '=')
            {
                string[] parts = request.Refno.Substring(1).Split('/');
                int[] pair = { int.Parse(parts[0], CultureInfo.InvariantCulture), int.Parse(parts[1], CultureInfo.InvariantCulture) };
                element = NativeRead(() => DbElement.GetElement(pair));
            }
            else element = NativeRead(() => mdb.FindElement(DbType.Catalogue, request.Refno));
            var identity = ParagonManifestIdentity(element, true);
            if (request.Refno[0] == '=' && request.Refno != identity.Refno)
                throw new InvalidOperationException("The SDK resolved a different database reference.");
            var target = identity.ToResult();
            target["name"] = null;
            target["name_error"] = null;
            try { target["name"] = ParagonManifestName(element); }
            catch (Exception error) { ThrowIfNativeFaulted(); target["name_error"] = ParagonCatalogueManifestRequest.ErrorText(error.Message); }
            if (request.Refno[0] == '/')
            {
                string name = target["name"] as string;
                if (name == null || !string.Equals(request.Refno.Substring(1), name.StartsWith("/", StringComparison.Ordinal)
                    ? name.Substring(1) : name, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("The SDK name did not verify the explicit catalogue target.");
            }

            var errors = new Dictionary<string, string>();
            var children = new List<Dictionary<string, object>>();
            int? totalChildren = null;
            bool? childrenTruncated = null;
            bool childrenComplete = true;
            try
            {
                // Both APIs return native-materialized arrays. Output budgets
                // bound per-item reads, not the SDK's initial array allocation.
                DbElementType type = NativeRead(() => element.GetElementType());
                if (type == null || !NativeRead(() => type.IsValid)) throw new InvalidOperationException("No valid native parent type was available.");
                DbElementType[] allowed = NativeRead(() => type.MemberTypes());
                if (allowed == null) throw new InvalidOperationException("The SDK returned no MemberTypes array.");
                totalChildren = allowed.Length;
                int count = Math.Min(allowed.Length, ParagonCatalogueSchemaRequest.MaximumChildTypes);
                childrenTruncated = count < allowed.Length;
                for (int index = 0; index < count; index++)
                {
                    var child = ParagonSchemaChild(element, allowed[index], index);
                    childrenComplete &= Equals(child["complete"], true);
                    children.Add(child);
                }
                childrenComplete &= childrenTruncated == false;
            }
            catch (Exception error)
            {
                ThrowIfNativeFaulted();
                errors["member_types"] = ParagonCatalogueManifestRequest.ErrorText(error.Message);
                childrenComplete = false;
            }
            var fields = new List<Dictionary<string, object>>();
            bool attributesComplete = true;
            bool? constraintsComplete = request.IncludeConstraints ? (bool?)true : null;
            foreach (string name in request.Attributes)
            {
                var field = ParagonSchemaAttribute(element, name, request.IncludeConstraints);
                attributesComplete &= Equals(field["metadata_complete"], true);
                if (request.IncludeConstraints)
                    constraintsComplete &= Equals(((Dictionary<string, object>)field["constraints"])["status"], "complete");
                fields.Add(field);
            }
            var after = ParagonManifestBoundary(sessionId, build, out Project afterProject, out MDB afterMdb);
            bool? sameProject = project == null || afterProject == null ? (bool?)null : ReferenceEquals(project, afterProject);
            bool? sameMdb = mdb == null || afterMdb == null ? (bool?)null : ReferenceEquals(mdb, afterMdb);
            bool? agreement = ParagonManifestAgreement(before, after, sameProject, sameMdb);
            bool complete = target["name_error"] == null && childrenComplete && attributesComplete
                && (!request.IncludeConstraints || constraintsComplete == true) && agreement == true;
            return new Dictionary<string, object>
            {
                ["operation"] = "paragon_catalogue_schema", ["available"] = true, ["success"] = complete, ["complete"] = complete,
                ["read_only"] = true, ["single_dispatch"] = true, ["atomic_snapshot"] = false,
                ["requested_refno"] = request.Refno, ["requested_attributes"] = request.Attributes,
                ["include_constraints"] = request.IncludeConstraints, ["target"] = target,
                ["child_types"] = children,
                ["child_types_observation"] = new { limit = ParagonCatalogueSchemaRequest.MaximumChildTypes,
                    returned_count = children.Count, total_count = totalChildren, truncated = childrenTruncated,
                    complete = childrenComplete, order = "sdk_member_types", native_arrays_materialized_before_output_limits = true },
                ["attributes"] = fields, ["attributes_complete"] = attributesComplete, ["constraints_complete"] = constraintsComplete,
                ["read_errors"] = errors,
                ["context"] = new { before, after, same_project_instance = sameProject, same_mdb_instance = sameMdb, boundary_agreement = agreement },
                ["output_limits"] = new { child_types = ParagonCatalogueSchemaRequest.MaximumChildTypes,
                    database_types_per_child = ParagonCatalogueSchemaRequest.MaximumDatabaseTypes, attributes = ParagonCatalogueSchemaRequest.MaximumAttributes,
                    values_or_boundaries_per_attribute = ParagonCatalogueSchemaRequest.MaximumConstraintItems,
                    constraint_text_utf16_characters = ParagonCatalogueSchemaRequest.MaximumConstraintTextLength,
                    native_arrays_materialized_before_output_limits = true },
                ["scope"] = new { kind = "catalogue_parent_schema", attribute_target = "current_target",
                    member_type_basis = "native_base_type", future_child_attributes_observed = false,
                    whole_project_coverage = false, current_module = (string)null, permissions_verified = (bool?)null,
                    proposed_values_validated = false, engineering_validated = (bool?)null,
                    range_unit_basis = "unknown", range_boundary_encoding = "unknown", optionality_is_requiredness = false }
            };
        });
    }

    private static void ParagonSchemaObserve(Dictionary<string, object> result, Dictionary<string, string> errors,
        string field, Func<object> read)
    {
        try { result[field] = read(); }
        catch (Exception error) { ThrowIfNativeFaulted(); errors[field] = ParagonCatalogueManifestRequest.ErrorText(error.Message); }
    }

    private static string ParagonSchemaText(string value, int maximum = 256)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Length > maximum)
            throw new InvalidOperationException("The SDK returned missing or over-budget schema text.");
        return value;
    }

    private static Dictionary<string, object> ParagonSchemaChild(DbElement parent, DbElementType child, int index)
    {
        var errors = new Dictionary<string, string>();
        var result = new Dictionary<string, object>
        {
            ["sdk_index"] = index, ["valid"] = null, ["name"] = null, ["short_name"] = null,
            ["database_type_codes"] = null, ["database_types_total_count"] = null,
            ["database_types_truncated"] = null, ["catalogue_supported"] = null,
            ["sdk_creatable_now"] = null, ["errors"] = errors, ["complete"] = false
        };
        if (child == null) { result["valid"] = false; errors["type"] = "The SDK returned a null member type."; return result; }
        ParagonSchemaObserve(result, errors, "valid", () => NativeRead(() => child.IsValid));
        if (!Equals(result["valid"], true))
        {
            if (!errors.ContainsKey("valid")) errors["type"] = "The SDK returned an invalid member type.";
            return result;
        }
        ParagonSchemaObserve(result, errors, "name", () => ParagonSchemaText(NativeRead(() => child.Name)));
        ParagonSchemaObserve(result, errors, "short_name", () => ParagonSchemaText(NativeRead(() => child.ShortName), 64));
        try
        {
            // Installed SDK signature is int[] DatabaseTypes(), not DbType[].
            int[] native = NativeRead(() => child.DatabaseTypes());
            if (native == null) throw new InvalidOperationException("The SDK returned no DatabaseTypes array.");
            int count = Math.Min(native.Length, ParagonCatalogueSchemaRequest.MaximumDatabaseTypes);
            var codes = new List<int>(count);
            bool supportsCatalogue = false;
            for (int ordinal = 0; ordinal < count; ordinal++)
            {
                codes.Add(native[ordinal]);
                supportsCatalogue |= native[ordinal] == (int)DbType.Catalogue;
            }
            bool truncated = count < native.Length;
            result["database_type_codes"] = codes;
            result["database_types_total_count"] = native.Length;
            result["database_types_truncated"] = truncated;
            result["catalogue_supported"] = supportsCatalogue ? true : truncated ? (bool?)null : false;
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["database_type_codes"] = ParagonCatalogueManifestRequest.ErrorText(error.Message); }
        ParagonSchemaObserve(result, errors, "sdk_creatable_now", () => NativeRead(() => parent.IsCreatable(child)));
        result["complete"] = errors.Count == 0 && Equals(result["database_types_truncated"], false);
        return result;
    }

    private static Dictionary<string, object> ParagonSchemaAttribute(DbElement target, string requestedName, bool includeConstraints)
    {
        var errors = new Dictionary<string, string>();
        var result = new Dictionary<string, object>
        {
            ["requested_name"] = requestedName, ["known"] = null, ["supported"] = null,
            ["name"] = null, ["short_name"] = null, ["data_type"] = null, ["is_array"] = null,
            ["is_pseudo"] = null, ["is_uda"] = null, ["unit_kind"] = null, ["maximum_size"] = null,
            ["errors"] = errors, ["metadata_complete"] = false, ["complete"] = false,
            ["constraints"] = includeConstraints ? new Dictionary<string, object> { ["status"] = "not_attempted", ["reason"] = "attribute_not_verified" } : null
        };
        DbAttribute attribute;
        try
        {
            attribute = NativeRead(() => DbAttribute.GetDbAttribute(requestedName));
            bool valid = attribute != null && NativeRead(() => attribute.IsValid);
            result["known"] = valid;
            if (!valid)
            {
                if (includeConstraints) ((Dictionary<string, object>)result["constraints"])["reason"] = "unknown_attribute";
                return result;
            }
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["known"] = ParagonCatalogueManifestRequest.ErrorText(error.Message); return result; }
        ParagonSchemaObserve(result, errors, "supported", () => NativeRead(() => target.IsAttributeValid(attribute)));
        ParagonSchemaObserve(result, errors, "name", () => ParagonSchemaText(NativeRead(() => attribute.Name)));
        ParagonSchemaObserve(result, errors, "short_name", () => ParagonSchemaText(NativeRead(() => attribute.ShortName), 64));
        ParagonSchemaObserve(result, errors, "data_type", () => ParagonSchemaText(NativeRead(() => attribute.Type).ToString(), 64));
        ParagonSchemaObserve(result, errors, "is_array", () => NativeRead(() => attribute.IsArray));
        ParagonSchemaObserve(result, errors, "is_pseudo", () => NativeRead(() => attribute.IsPseudo));
        ParagonSchemaObserve(result, errors, "is_uda", () => NativeRead(() => attribute.IsUDA));
        ParagonSchemaObserve(result, errors, "unit_kind", () => ParagonSchemaText(NativeRead(() => attribute.Units).ToString(), 64));
        ParagonSchemaObserve(result, errors, "maximum_size", () => NativeRead(() => attribute.MaximumSize));
        bool metadataComplete = errors.Count == 0;
        result["metadata_complete"] = metadataComplete;
        bool constraintsComplete = true;
        if (includeConstraints)
        {
            if (Equals(result["supported"], true)) result["constraints"] = ParagonSchemaConstraints(target, attribute);
            else ((Dictionary<string, object>)result["constraints"])["reason"] = Equals(result["supported"], false)
                ? "attribute_not_supported" : "attribute_support_unknown";
            constraintsComplete = Equals(((Dictionary<string, object>)result["constraints"])["status"], "complete");
        }
        result["complete"] = metadataComplete && constraintsComplete;
        return result;
    }

    private static Dictionary<string, object> ParagonSchemaConstraints(DbElement target, DbAttribute attribute)
    {
        var values = new Dictionary<string, object>
        {
            ["status"] = "read_error", ["defined"] = null, ["values"] = new List<object>(), ["total_count"] = null,
            ["returned_count"] = 0, ["truncated"] = null, ["error"] = null
        };
        var ranges = new Dictionary<string, object>
        {
            ["status"] = "read_error", ["defined"] = null, ["raw_boundaries"] = new List<object>(), ["total_count"] = null,
            ["returned_count"] = 0, ["truncated"] = null, ["error"] = null,
            ["non_finite_indices"] = new List<int>(), ["unit_basis"] = "unknown", ["boundary_encoding"] = "unknown"
        };
        var optional = new Dictionary<string, object> { ["status"] = "read_error", ["value"] = null, ["error"] = null };
        try
        {
            var query = NativeRead(() =>
            {
                KeyValuePair<string, string>[] items = null;
                bool defined = target.GetAttributeAllowedValuesAndDescriptions(attribute, ref items);
                return Tuple.Create(defined, items);
            });
            if (!query.Item1)
            {
                values["status"] = "not_defined"; values["defined"] = false; values["total_count"] = 0; values["truncated"] = false;
            }
            else
            {
                if (query.Item2 == null) throw new InvalidOperationException("The SDK reported allowed values without an array.");
                int count = Math.Min(query.Item2.Length, ParagonCatalogueSchemaRequest.MaximumConstraintItems);
                bool truncated = count < query.Item2.Length;
                var bounded = new List<object>(count);
                for (int index = 0; index < count; index++)
                {
                    string value = ParagonSchemaConstraintText(query.Item2[index].Key, out bool valueTruncated);
                    string description = ParagonSchemaConstraintText(query.Item2[index].Value, out bool descriptionTruncated);
                    truncated |= valueTruncated || descriptionTruncated;
                    bounded.Add(new { value, description, value_truncated = valueTruncated, description_truncated = descriptionTruncated });
                }
                values["status"] = "defined"; values["defined"] = true; values["values"] = bounded;
                values["total_count"] = query.Item2.Length; values["returned_count"] = count; values["truncated"] = truncated;
            }
        }
        catch (Exception error) { ThrowIfNativeFaulted(); values["error"] = ParagonCatalogueManifestRequest.ErrorText(error.Message); }
        try
        {
            var query = NativeRead(() =>
            {
                double[] items = null;
                bool defined = target.GetAttributeAllowedRanges(attribute, ref items);
                return Tuple.Create(defined, items);
            });
            if (!query.Item1)
            {
                ranges["status"] = "not_defined"; ranges["defined"] = false; ranges["total_count"] = 0; ranges["truncated"] = false;
            }
            else
            {
                if (query.Item2 == null) throw new InvalidOperationException("The SDK reported allowed ranges without an array.");
                int count = Math.Min(query.Item2.Length, ParagonCatalogueSchemaRequest.MaximumConstraintItems);
                var bounded = new List<object>(count);
                var nonFinite = new List<int>();
                for (int index = 0; index < count; index++)
                {
                    double value = query.Item2[index];
                    if (double.IsNaN(value) || double.IsInfinity(value))
                    {
                        bounded.Add(value.ToString("R", CultureInfo.InvariantCulture));
                        nonFinite.Add(index);
                    }
                    else bounded.Add(value);
                }
                ranges["status"] = "defined"; ranges["defined"] = true; ranges["raw_boundaries"] = bounded;
                ranges["total_count"] = query.Item2.Length; ranges["returned_count"] = count;
                ranges["truncated"] = count < query.Item2.Length; ranges["non_finite_indices"] = nonFinite;
            }
        }
        catch (Exception error) { ThrowIfNativeFaulted(); ranges["error"] = ParagonCatalogueManifestRequest.ErrorText(error.Message); }
        try { optional["value"] = NativeRead(() => target.IsAttributeAllowedOptional(attribute)); optional["status"] = "complete"; }
        catch (Exception error) { ThrowIfNativeFaulted(); optional["error"] = ParagonCatalogueManifestRequest.ErrorText(error.Message); }
        bool errorObserved = values["error"] != null || ranges["error"] != null || optional["error"] != null;
        bool truncatedObserved = Equals(values["truncated"], true) || Equals(ranges["truncated"], true);
        return new Dictionary<string, object>
        {
            ["status"] = errorObserved ? "partial" : truncatedObserved ? "truncated" : "complete",
            ["allowed_values"] = values, ["allowed_ranges"] = ranges, ["values_or_ranges_optional"] = optional
        };
    }

    private static string ParagonSchemaConstraintText(string value, out bool truncated)
    {
        truncated = value != null && value.Length > ParagonCatalogueSchemaRequest.MaximumConstraintTextLength;
        if (!truncated) return value;
        int length = ParagonCatalogueSchemaRequest.MaximumConstraintTextLength;
        if (char.IsHighSurrogate(value[length - 1]) && char.IsLowSurrogate(value[length])) length--;
        return value.Substring(0, length);
    }
}
