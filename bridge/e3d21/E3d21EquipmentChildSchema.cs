using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context : IE3d21EquipmentChildSchemaContext
{
    public object ReadEquipmentChildSchema(string parentRefno, string elementType, string readTargetRefno,
        IList<string> attributes, int offset, int limit, string sessionId)
    {
        var request = EquipmentChildSchemaRequest.Validate(parentRefno, elementType, readTargetRefno, attributes, offset, limit);
        if (string.IsNullOrWhiteSpace(sessionId) || sessionId.Length > 256) throw new ArgumentException("A bounded bridge session identity is required.", "sessionId");
        string build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
            var errors = new Dictionary<string, string>();
            var before = ChildSchemaBoundaryRead(sessionId, build);
            DbElement parent;
            Dictionary<string, object> parentIdentity;
            try
            {
                parent = ChildSchemaResolve(request.ParentRefno, before.Mdb, out Dictionary<string, object> parentBinding);
                parentIdentity = ChildSchemaIdentity(parent, parentBinding, out DbElementType parentActualType);
            }
            catch (Exception error)
            {
                ThrowIfNativeFaulted();
                throw new InvalidOperationException("equipment_child_schema parent_refno could not be read as an explicit existing Design parent: "
                    + EquipmentChildSchemaRequest.ErrorText(error.Message)
                    + " Parent creatability, type attributes and exemplar attributes have not been observed.", error);
            }
            var requestedIdentity = ChildSchemaEmptyType("DbElementType.GetElementType");
            var typeErrors = (Dictionary<string, string>)requestedIdentity["errors"];
            DbElementType requestedType = null;
            try
            {
                requestedType = NativeRead(() => DbElementType.GetElementType(request.ElementType));
                requestedIdentity = ChildSchemaType(requestedType, "DbElementType.GetElementType");
            }
            catch (Exception error) { ThrowIfNativeFaulted(); typeErrors["resolve"] = EquipmentChildSchemaRequest.ErrorText(error.Message); }
            bool typeKnown = Equals(requestedIdentity["complete"], true);
            var creatability = new Dictionary<string, object>
            {
                ["source"] = "DbElement.IsCreatable", ["scope"] = "current_parent_requested_type",
                ["parent_refno"] = parentIdentity["refno"], ["requested_element_type"] = request.ElementType,
                ["sdk_creatable_now"] = null, ["error"] = null, ["complete"] = false
            };
            if (typeKnown)
            {
                try
                {
                    creatability["sdk_creatable_now"] = NativeRead(() => parent.IsCreatable(requestedType));
                    creatability["complete"] = true;
                }
                catch (Exception error) { ThrowIfNativeFaulted(); creatability["error"] = EquipmentChildSchemaRequest.ErrorText(error.Message); }
            }
            var systemAttributes = ChildSchemaTypeAttributes(typeKnown ? requestedType : null, request);
            Dictionary<string, object> targetIdentity = null;
            bool? typeMatches = null;
            var instance = new Dictionary<string, object>
            {
                ["source"] = "explicit_existing_read_target", ["scope"] = "read_target_only",
                ["refno"] = null, ["status"] = "unknown", ["reason"] = "read_target_required",
                ["attributes"] = new List<object>(), ["complete"] = false
            };
            if (request.ReadTargetRefno != null)
            {
                instance["reason"] = "read_target_unavailable";
                try
                {
                    DbElement target = ChildSchemaResolve(request.ReadTargetRefno, before.Mdb, out Dictionary<string, object> targetBinding);
                    targetIdentity = ChildSchemaIdentity(target, targetBinding, out DbElementType actual);
                    instance["refno"] = targetIdentity["refno"];
                    var actualIdentity = (Dictionary<string, object>)targetIdentity["actual_type"];
                    instance["reason"] = typeKnown ? "actual_type_unknown" : "requested_type_unknown";
                    if (typeKnown && Equals(actualIdentity["complete"], true))
                    {
                        // Compare the actual SDK type, never only its base type or
                        // a manifest row's short-name string. A base CYLI exemplar
                        // does not witness :CUSTOM_CYLI, or vice versa.
                        typeMatches = NativeRead(() => requestedType.Equals(actual));
                        if (typeMatches == true)
                        {
                            var rows = new List<object>();
                            foreach (string name in request.Attributes) rows.Add(ChildSchemaInstanceAttribute(target, name));
                            bool complete = rows.Cast<Dictionary<string, object>>().All(row => Equals(row["complete"], true));
                            instance["attributes"] = rows;
                            instance["complete"] = complete;
                            instance["status"] = complete ? "observed" : "partial";
                            instance["reason"] = null;
                        }
                        else instance["reason"] = "actual_type_mismatch";
                    }
                }
                catch (Exception error)
                {
                    ThrowIfNativeFaulted();
                    errors["read_target"] = EquipmentChildSchemaRequest.ErrorText(error.Message);
                }
            }
            var after = ChildSchemaBoundaryRead(sessionId, build);
            bool? identityUnchanged = ChildSchemaBoundaryAgreement(before, after, false);
            bool? ceUnchanged = ChildSchemaBoundaryAgreement(before, after, true);
            bool completeRead = errors.Count == 0 && typeKnown && Equals(parentIdentity["complete"], true)
                && Equals(creatability["complete"], true) && Equals(systemAttributes["complete"], true)
                && Equals(instance["complete"], true) && targetIdentity != null && Equals(targetIdentity["complete"], true)
                && identityUnchanged == true && ceUnchanged == true;
            return new Dictionary<string, object>
            {
                ["operation"] = "equipment_child_schema", ["available"] = true,
                ["read_only"] = true, ["single_dispatch"] = true, ["atomic_snapshot"] = false,
                ["success"] = completeRead, ["complete"] = completeRead, ["session_id"] = sessionId, ["bridge_build"] = build,
                ["requested_parent_refno"] = request.ParentRefno, ["requested_element_type"] = request.ElementType,
                ["requested_read_target_refno"] = request.ReadTargetRefno, ["requested_attributes"] = request.Attributes,
                ["parent"] = parentIdentity, ["requested_type"] = requestedIdentity,
                ["parent_creatability"] = creatability, ["type_system_attributes"] = systemAttributes,
                ["read_target"] = targetIdentity, ["read_target_actual_type_matches"] = typeMatches,
                ["instance_attributes"] = instance, ["read_errors"] = errors,
                ["context"] = new Dictionary<string, object>
                {
                    ["before"] = before.Evidence, ["after"] = after.Evidence,
                    ["identity_unchanged"] = identityUnchanged, ["ce_unchanged"] = ceUnchanged
                },
                ["scope"] = new Dictionary<string, object>
                {
                    ["future_child_attributes_observed"] = false, ["actual_type_requires_existing_object"] = true,
                    ["instance_attribute_target"] = "explicit_existing_read_target_only",
                    ["type_system_attributes_are_instance_attributes"] = false,
                    ["sdk_creatable_now_is_execution_authorization"] = false,
                    ["permissions_verified"] = null, ["proposed_values_validated"] = false,
                    ["engineering_validated"] = null, ["constraints_observed"] = false,
                    ["whole_equipment_coverage"] = false
                }
            };
        });
    }

    private static DbElement ChildSchemaResolve(string reference, MDB mdb, out Dictionary<string, object> binding)
    {
        binding = null;
        DbElement element;
        if (reference[0] == '=')
        {
            string[] pair = reference.Substring(1).Split('/');
            int[] numbers = { int.Parse(pair[0], CultureInfo.InvariantCulture), int.Parse(pair[1], CultureInfo.InvariantCulture) };
            element = NativeRead(() => DbElement.GetElement(numbers));
        }
        else
        {
            if (ReferenceEquals(mdb, null)) throw new InvalidOperationException("The current MDB is unavailable; an explicit name cannot be resolved.");
            element = NativeRead(() => mdb.FindElement(reference));
        }
        string actualReference = ChildSchemaReference(element);
        if (NativeRead(() => element.DbType) != DbType.Design)
            throw new InvalidOperationException("The parent and explicit read target must belong to a Design database.");
        if (reference[0] == '=' && reference != actualReference)
            throw new InvalidOperationException("The resolved object does not match the explicit DBREF.");
        string observedName = null;
        if (reference[0] == '/')
        {
            observedName = ChildSchemaText(NativeRead(() => element.GetAsString(DbAttributeInstance.NAME)), 1024);
            if (!string.Equals(reference.TrimStart('/'), observedName.TrimStart('/'), StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("The resolved object does not match the explicit name.");
        }
        // Capture the real first observation only after the requested numeric
        // reference or bounded NAME has been verified against this Design object.
        binding = new Dictionary<string, object>
        {
            ["source"] = "explicit_request_resolution", ["requested_refno"] = reference,
            ["resolved_refno"] = actualReference, ["observed_name"] = observedName, ["verified"] = true
        };
        return element;
    }

    private static string ChildSchemaReference(DbElement element)
    {
        if (ReferenceEquals(element, null) || NativeRead(() => element.IsNull) || !NativeRead(() => element.IsValid)
            || NativeRead(() => element.IsDeleted)) throw new InvalidOperationException("The explicit object is unavailable or deleted.");
        int[] pair = NativeRead(() => element.Ref);
        if (pair == null || pair.Length != 2 || pair[0] <= 0 || pair[1] < 0)
            throw new InvalidOperationException("The SDK returned an invalid DBREF.");
        return "=" + pair[0].ToString(CultureInfo.InvariantCulture) + "/" + pair[1].ToString(CultureInfo.InvariantCulture);
    }

    private static Dictionary<string, object> ChildSchemaIdentity(DbElement element, Dictionary<string, object> binding,
        out DbElementType actualType)
    {
        var errors = new Dictionary<string, string>();
        var result = new Dictionary<string, object>
        {
            ["refno"] = ChildSchemaReference(element), ["name"] = null, ["request_binding"] = binding,
            ["base_type"] = ChildSchemaEmptyType("DbElement.ElementType"),
            ["actual_type"] = ChildSchemaEmptyType("DbElement.GetActualType"), ["errors"] = errors, ["complete"] = false
        };
        ChildSchemaObserve(result, errors, "name", () => ChildSchemaText(NativeRead(() => element.GetAsString(DbAttributeInstance.NAME)), 1024, true));
        ChildSchemaObserve(result, errors, "base_type", () => ChildSchemaType(NativeRead(() => element.ElementType), "DbElement.ElementType"));
        DbElementType observedActual = null;
        ChildSchemaObserve(result, errors, "actual_type", () => ChildSchemaType(observedActual = NativeRead(() => element.GetActualType()), "DbElement.GetActualType"));
        actualType = observedActual;
        string firstName = binding["observed_name"] as string;
        string currentName = result["name"] as string;
        if (!Equals(binding["resolved_refno"], result["refno"]) ||
            firstName != null && currentName != null && !string.Equals(firstName.TrimStart('/'), currentName.TrimStart('/'), StringComparison.OrdinalIgnoreCase))
            errors["request_binding"] = "The current object identity no longer agrees with the first verified explicit request resolution; DBREF or NAME changed during this read.";
        result["complete"] = errors.Count == 0 && Equals(((Dictionary<string, object>)result["base_type"])["complete"], true)
            && Equals(((Dictionary<string, object>)result["actual_type"])["complete"], true);
        return result;
    }

    private static Dictionary<string, object> ChildSchemaEmptyType(string source)
    {
        return new Dictionary<string, object>
        {
            ["source"] = source, ["valid"] = null, ["name"] = null, ["short_name"] = null,
            ["complete"] = false, ["errors"] = new Dictionary<string, string>()
        };
    }

    private static Dictionary<string, object> ChildSchemaType(DbElementType type, string source)
    {
        var result = ChildSchemaEmptyType(source);
        var errors = (Dictionary<string, string>)result["errors"];
        ChildSchemaObserve(result, errors, "valid", () => !ReferenceEquals(type, null) && NativeRead(() => type.IsValid));
        if (Equals(result["valid"], true))
        {
            ChildSchemaObserve(result, errors, "name", () => ChildSchemaText(NativeRead(() => type.Name), 256));
            ChildSchemaObserve(result, errors, "short_name", () => ChildSchemaText(NativeRead(() => type.ShortName), 64));
        }
        result["complete"] = Equals(result["valid"], true) && errors.Count == 0;
        return result;
    }

    private static Dictionary<string, object> ChildSchemaTypeAttributes(DbElementType type, EquipmentChildSchemaRequest request)
    {
        var errors = new Dictionary<string, string>();
        var rows = new List<object>();
        var result = new Dictionary<string, object>
        {
            ["source"] = "DbElementType.SystemAttributes", ["scope"] = "requested_type_only",
            ["offset"] = request.Offset, ["limit"] = request.Limit, ["total_count"] = null,
            ["returned_count"] = 0, ["has_more"] = null, ["next_offset"] = null,
            ["attributes"] = rows, ["errors"] = errors, ["complete"] = false,
            ["whole_type_coverage"] = false, ["native_arrays_materialized_before_output_limits"] = true,
            ["native_array_inspection_limit"] = EquipmentChildSchemaRequest.MaximumNativeArrayInspection,
            ["inspection_limited"] = null
        };
        if (ReferenceEquals(type, null)) { errors["type"] = "The requested SDK type is unknown."; return result; }
        try
        {
            DbAttribute[] native = NativeRead(() => type.SystemAttributes());
            if (native == null) throw new InvalidOperationException("SystemAttributes returned no array.");
            result["total_count"] = native.Length;
            bool inspectionLimited = native.Length > EquipmentChildSchemaRequest.MaximumNativeArrayInspection;
            result["inspection_limited"] = inspectionLimited;
            if (inspectionLimited)
            {
                // The native array is already materialized. Do not enumerate it,
                // inspect its entries, or pretend the requested schema is empty.
                errors["inspection_limit"] = "SystemAttributes materialized " + native.Length.ToString(CultureInfo.InvariantCulture)
                    + " entries, exceeding the inspection limit of " + EquipmentChildSchemaRequest.MaximumNativeArrayInspection.ToString(CultureInfo.InvariantCulture)
                    + ". Type attribute metadata and page continuation remain unknown; this cutoff does not bound SDK allocation.";
                return result;
            }
            int end = request.Offset + Math.Min(request.Limit, Math.Max(0, native.Length - request.Offset));
            for (int index = request.Offset; index < end; index++)
            {
                var row = ChildSchemaAttributeMetadata(native[index]);
                row["sdk_index"] = index;
                rows.Add(row);
            }
            bool hasMore = end < native.Length;
            result["returned_count"] = rows.Count;
            result["has_more"] = hasMore;
            result["next_offset"] = hasMore ? (object)end : null;
            bool complete = rows.Cast<Dictionary<string, object>>().All(row => Equals(row["complete"], true));
            result["complete"] = complete;
            result["whole_type_coverage"] = complete && request.Offset == 0 && !hasMore;
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["system_attributes"] = EquipmentChildSchemaRequest.ErrorText(error.Message); }
        return result;
    }

    private static Dictionary<string, object> ChildSchemaAttributeMetadata(DbAttribute attribute)
    {
        var errors = new Dictionary<string, string>();
        var result = new Dictionary<string, object>
        {
            ["known"] = null, ["name"] = null, ["short_name"] = null, ["data_type"] = null,
            ["is_array"] = null, ["is_pseudo"] = null, ["is_uda"] = null,
            ["unit_kind"] = null, ["maximum_size"] = null, ["errors"] = errors, ["complete"] = false
        };
        ChildSchemaObserve(result, errors, "known", () => !ReferenceEquals(attribute, null) && NativeRead(() => attribute.IsValid));
        if (!Equals(result["known"], true)) return result;
        ChildSchemaObserve(result, errors, "name", () => ChildSchemaText(NativeRead(() => attribute.Name), 256));
        ChildSchemaObserve(result, errors, "short_name", () => ChildSchemaText(NativeRead(() => attribute.ShortName), 64));
        ChildSchemaObserve(result, errors, "data_type", () => ChildSchemaText(NativeRead(() => attribute.Type).ToString(), 64));
        ChildSchemaObserve(result, errors, "is_array", () => NativeRead(() => attribute.IsArray));
        ChildSchemaObserve(result, errors, "is_pseudo", () => NativeRead(() => attribute.IsPseudo));
        ChildSchemaObserve(result, errors, "is_uda", () => NativeRead(() => attribute.IsUDA));
        // Units is an SDK category, never a concrete mm/in display unit.
        ChildSchemaObserve(result, errors, "unit_kind", () => ChildSchemaText(NativeRead(() => attribute.Units).ToString(), 64));
        ChildSchemaObserve(result, errors, "maximum_size", () => NativeRead(() => attribute.MaximumSize));
        result["complete"] = errors.Count == 0;
        return result;
    }

    private static Dictionary<string, object> ChildSchemaInstanceAttribute(DbElement target, string name)
    {
        DbAttribute attribute = null;
        string resolveError = null;
        try { attribute = NativeRead(() => DbAttribute.GetDbAttribute(name)); }
        catch (Exception error) { ThrowIfNativeFaulted(); resolveError = EquipmentChildSchemaRequest.ErrorText(error.Message); }
        var result = ChildSchemaAttributeMetadata(attribute);
        var errors = (Dictionary<string, string>)result["errors"];
        if (resolveError != null) { errors["resolve"] = resolveError; result["known"] = null; }
        result["requested_name"] = name;
        result["supported"] = null;
        result["sdk_settable_now"] = null;
        if (Equals(result["known"], true))
        {
            ChildSchemaObserve(result, errors, "supported", () => NativeRead(() => target.IsAttributeValid(attribute)));
            if (Equals(result["supported"], true))
                ChildSchemaObserve(result, errors, "sdk_settable_now", () => NativeRead(() => target.IsAttributeSetable(attribute)));
        }
        result["complete"] = Equals(result["known"], true) && errors.Count == 0;
        return result;
    }

    private static void ChildSchemaObserve(Dictionary<string, object> result, Dictionary<string, string> errors,
        string field, Func<object> read)
    {
        try { result[field] = read(); }
        catch (Exception error) { ThrowIfNativeFaulted(); errors[field] = EquipmentChildSchemaRequest.ErrorText(error.Message); }
    }

    private static string ChildSchemaText(string value, int limit, bool allowEmpty = false)
    {
        if (value == null || value.Length > limit || !allowEmpty && string.IsNullOrWhiteSpace(value))
            throw new InvalidOperationException("The SDK returned missing or oversized metadata text.");
        return value;
    }

    private sealed class EquipmentChildSchemaBoundary
    {
        internal Project Project;
        internal MDB Mdb;
        internal readonly Dictionary<string, object> Evidence = new Dictionary<string, object>
        {
            ["project"] = null, ["mdb"] = null, ["ce_refno"] = null,
            ["ce_base_type"] = null, ["ce_actual_type"] = null, ["errors"] = new Dictionary<string, string>()
        };
    }

    private static EquipmentChildSchemaBoundary ChildSchemaBoundaryRead(string sessionId, string build)
    {
        var boundary = new EquipmentChildSchemaBoundary();
        var result = boundary.Evidence;
        result["session_id"] = sessionId;
        result["bridge_build"] = build;
        var errors = (Dictionary<string, string>)result["errors"];
        ChildSchemaObserve(result, errors, "project", () =>
        {
            boundary.Project = NativeRead(() => Project.CurrentProject);
            return ChildSchemaText(ReferenceEquals(boundary.Project, null) ? null : NativeRead(() => boundary.Project.Name), 256);
        });
        ChildSchemaObserve(result, errors, "mdb", () =>
        {
            boundary.Mdb = NativeRead(() => MDB.CurrentMDB);
            return ChildSchemaText(ReferenceEquals(boundary.Mdb, null) ? null : NativeRead(() => boundary.Mdb.Name), 256);
        });
        DbElement ce = null;
        ChildSchemaObserve(result, errors, "ce_refno", () =>
        {
            ce = NativeRead(() => Aveva.Core.Database.CurrentElement.Element);
            return ChildSchemaReference(ce);
        });
        ChildSchemaObserve(result, errors, "ce_base_type", () =>
        {
            if (ReferenceEquals(ce, null)) throw new InvalidOperationException("CE type is unknown.");
            var identity = ChildSchemaType(NativeRead(() => ce.ElementType), "DbElement.ElementType");
            if (!Equals(identity["complete"], true)) throw new InvalidOperationException("CE type is unknown.");
            return identity["name"];
        });
        ChildSchemaObserve(result, errors, "ce_actual_type", () =>
        {
            if (ReferenceEquals(ce, null)) throw new InvalidOperationException("CE actual type is unknown.");
            var identity = ChildSchemaType(NativeRead(() => ce.GetActualType()), "DbElement.GetActualType");
            if (!Equals(identity["complete"], true)) throw new InvalidOperationException("CE actual type is unknown.");
            return identity["name"];
        });
        return boundary;
    }

    private static bool? ChildSchemaBoundaryAgreement(EquipmentChildSchemaBoundary before, EquipmentChildSchemaBoundary after, bool ce)
    {
        string[] fields = ce ? new[] { "ce_refno", "ce_base_type", "ce_actual_type" } : new[] { "session_id", "bridge_build", "project", "mdb" };
        bool unknown = false;
        foreach (string field in fields)
        {
            object left = before.Evidence[field], right = after.Evidence[field];
            if (left == null || right == null) unknown = true;
            else if (!Equals(left, right)) return false;
        }
        if (!ce)
        {
            if (!ReferenceEquals(before.Project, null) && !ReferenceEquals(after.Project, null) && !ReferenceEquals(before.Project, after.Project)) return false;
            if (!ReferenceEquals(before.Mdb, null) && !ReferenceEquals(after.Mdb, null) && !ReferenceEquals(before.Mdb, after.Mdb)) return false;
        }
        return unknown ? (bool?)null : true;
    }
}
