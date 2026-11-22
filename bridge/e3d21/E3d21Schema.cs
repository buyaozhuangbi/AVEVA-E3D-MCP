using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private const int AttributeConstraintItemLimit = 100;
    private const int AttributeConstraintTextLimit = 1024;

    public object GetElementSchema(string refno, IList<string> requestedAttributes, int offset, int limit,
        bool includeConstraints = false, string expectedType = "")
    {
        if (offset < 0 || offset > 100000) throw new ArgumentOutOfRangeException("offset");
        if (limit < 1 || limit > 200) throw new ArgumentOutOfRangeException("limit");
        if (requestedAttributes == null || requestedAttributes.Count > 50)
            throw new ArgumentException("attributes must be an array of at most 50 names; an empty array lists SDK attributes.");
        List<string> names = requestedAttributes.Select(name => (name ?? "").Trim().ToUpperInvariant()).Distinct().ToList();
        if (names.Any(name => name.Length == 0 || name.Length > 64 ||
            name.Any(c => !(char.IsLetterOrDigit(c) || c == ':' || c == '_'))))
            throw new ArgumentException("Use attribute names only, for example POS, HEIG, or :CUSTOM.");
        if (includeConstraints && (names.Count == 0 || names.Count > 10))
            throw new ArgumentException("Constraint reads require 1 to 10 explicitly named attributes.");
        string requestedType = string.IsNullOrWhiteSpace(expectedType)
            ? "" : ModelEditInput.TypeName(expectedType).ToUpperInvariant();
        if (includeConstraints)
            refno = ModelEditInput.ExplicitReference(refno, "refno");
        if (IsCommandActive())
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

        return OnUi(delegate
        {
            Aveva.Core.Database.Project projectBefore = NativeRead(() => Aveva.Core.Database.Project.CurrentProject);
            Aveva.Core.Database.MDB mdbBefore = NativeRead(() => Aveva.Core.Database.MDB.CurrentMDB);
            Dictionary<string, object> contextBefore = ReadEndpointContext(projectBefore, mdbBefore);
            DbElement element = ResolveElementByType(refno, requestedType);
            if (!IsUsable(element))
                throw new InvalidOperationException("Element was not found: " + refno);
            DbElementType type = element.GetElementType();
            Dictionary<string, string> errors = new Dictionary<string, string>();
            List<object> children = new List<object>();
            List<object> owners = new List<object>();
            try
            {
                foreach (DbElementType child in type.MemberTypes().OrderBy(item => item.Name, StringComparer.OrdinalIgnoreCase))
                {
                    ThrowIfNativeFaulted();
                    Dictionary<string, object> item = TypeIdentity(child);
                    item["sdk_creatable_now"] = null;
                    try { item["sdk_creatable_now"] = element.IsCreatable(child); }
                    catch (Exception ex) { ThrowIfNativeFaulted(); errors["child:" + child.Name] = ex.Message; }
                    children.Add(item);
                }
            }
            catch (Exception ex) { ThrowIfNativeFaulted(); errors["child_types"] = ex.Message; }
            try
            {
                owners.AddRange(type.OwnerTypes().OrderBy(item => item.Name, StringComparer.OrdinalIgnoreCase)
                    .Select(item => (object)TypeIdentity(item)));
            }
            catch (Exception ex) { ThrowIfNativeFaulted(); errors["owner_types"] = ex.Message; }

            bool selected = names.Count > 0;
            bool enumerationComplete = true;
            if (!selected)
            {
                try
                {
                    names = element.GetAttributes().Select(attribute => attribute.Name)
                        .Distinct(StringComparer.OrdinalIgnoreCase).OrderBy(name => name, StringComparer.OrdinalIgnoreCase).ToList();
                }
                catch (Exception ex)
                {
                    ThrowIfNativeFaulted();
                    errors["attribute_list"] = ex.Message;
                    enumerationComplete = false;
                }
            }
            List<object> attributes = names.Skip(offset).Take(limit)
                .Select(name => (object)AttributeSchema(element, name, errors, includeConstraints)).ToList();
            bool hasMore = offset + attributes.Count < names.Count;
            var result = new Dictionary<string, object>
            {
                ["success"] = errors.Count == 0,
                ["element"] = ElementIdentity(element),
                ["element_type"] = TypeIdentity(type),
                ["child_types"] = children,
                ["owner_types"] = owners,
                ["attributes"] = attributes,
                ["attribute_selection"] = selected ? "requested" : "SDK GetAttributes",
                ["attribute_page"] = new Dictionary<string, object>
                {
                    ["offset"] = offset, ["limit"] = limit, ["returned_count"] = attributes.Count,
                    ["total_count"] = enumerationComplete ? (object)names.Count : null,
                    ["has_more"] = enumerationComplete ? (object)hasMore : null,
                    ["next_offset"] = enumerationComplete && hasMore ? (object)(offset + attributes.Count) : null
                },
                ["read_errors"] = errors
            };
            if (includeConstraints)
            {
                result["constraints_requested"] = true;
                result["constraints_complete"] = attributes.Cast<Dictionary<string, object>>().All(row =>
                    Equals(((Dictionary<string, object>)row["constraints"])["status"], "complete"));
                result["constraints_scope"] = "SDK allowed values/descriptions, raw range boundaries and values-or-ranges optionality on this page. Range units and boundary encoding are unverified; false means not defined, not unrestricted. Optionality is not attribute requiredness. These reads do not validate a proposed value or authorize an edit.";
                result["constraints_output_limits"] = new Dictionary<string, object>
                {
                    ["attributes"] = 10, ["values_or_boundaries_per_attribute"] = AttributeConstraintItemLimit,
                    ["text_utf16_characters"] = AttributeConstraintTextLimit,
                    ["native_arrays_materialized_before_output_limits"] = true
                };
            }

            Aveva.Core.Database.Project projectAfter = NativeRead(() => Aveva.Core.Database.Project.CurrentProject);
            Aveva.Core.Database.MDB mdbAfter = NativeRead(() => Aveva.Core.Database.MDB.CurrentMDB);
            Dictionary<string, object> contextAfter = ReadEndpointContext(projectAfter, mdbAfter);
            ApplyEndpointContext(result, projectBefore, mdbBefore, contextBefore,
                projectAfter, mdbAfter, contextAfter);
            return result;
        });
    }

    private static void ApplyEndpointContext(Dictionary<string, object> result,
        Aveva.Core.Database.Project projectBefore, Aveva.Core.Database.MDB mdbBefore, Dictionary<string, object> contextBefore,
        Aveva.Core.Database.Project projectAfter, Aveva.Core.Database.MDB mdbAfter, Dictionary<string, object> contextAfter)
    {
        bool? sameProject = projectBefore == null || projectAfter == null ? (bool?)null :
            ReferenceEquals(projectBefore, projectAfter);
        bool? sameMdb = mdbBefore == null || mdbAfter == null ? (bool?)null :
            ReferenceEquals(mdbBefore, mdbAfter);
        bool? identityUnchanged = sameProject == false || sameMdb == false ? false :
            sameProject != true || sameMdb != true || !EndpointContextText(contextBefore, "project") ||
            !EndpointContextText(contextAfter, "project") || !EndpointContextText(contextBefore, "mdb") ||
            !EndpointContextText(contextAfter, "mdb") ? (bool?)null :
            Equals(contextBefore["project"], contextAfter["project"]) &&
            Equals(contextBefore["mdb"], contextAfter["mdb"]);
        bool? ceUnchanged = EndpointContextEqual(contextBefore, contextAfter);
        bool? agreement = identityUnchanged == false || ceUnchanged == false ? false :
            identityUnchanged == true && ceUnchanged == true ? true : (bool?)null;
        result["context"] = new Dictionary<string, object>
        {
            ["before"] = contextBefore, ["after"] = contextAfter,
            ["same_project_instance"] = sameProject, ["same_mdb_instance"] = sameMdb,
            ["identity_unchanged"] = identityUnchanged, ["ce_unchanged"] = ceUnchanged,
            ["boundary_agreement"] = agreement
        };
        result["project"] = contextBefore["project"];
        result["project_after"] = contextAfter["project"];
        result["mdb"] = contextBefore["mdb"];
        result["mdb_after"] = contextAfter["mdb"];
        result["ce_before"] = contextBefore["current_element"];
        result["ce_after"] = contextAfter["current_element"];
        result["ce_unchanged"] = ceUnchanged;
        result["identity_unchanged"] = identityUnchanged;
    }

    private static Dictionary<string, object> ReadEndpointContext(Aveva.Core.Database.Project project, Aveva.Core.Database.MDB mdb)
    {
        DbElement current = NativeRead(() => Aveva.Core.Database.CurrentElement.Element);
        return new Dictionary<string, object>
        {
            ["project"] = project == null ? null : NativeRead(() => project.Name),
            ["mdb"] = mdb == null ? null : NativeRead(() => mdb.Name),
            ["current_element"] = EndpointReference(current),
            ["current_element_type"] = EndpointType(current)
        };
    }

    private static string EndpointReference(DbElement element)
    {
        if (element == null || NativeRead(() => element.IsNull)) return "";
        int[] reference = NativeRead(() => element.Ref);
        if (reference == null || reference.Length != 2 || reference[0] <= 0 || reference[1] < 0)
            throw new InvalidOperationException("The SDK did not return a valid current-element reference.");
        return "=" + reference[0].ToString(CultureInfo.InvariantCulture) + "/" +
            reference[1].ToString(CultureInfo.InvariantCulture);
    }

    private static string EndpointType(DbElement element)
    {
        if (element == null || NativeRead(() => element.IsNull)) return "";
        DbElementType type = NativeRead(() => element.GetElementType());
        if (type == null) return "";
        string shortName = NativeRead(() => type.ShortName);
        return string.IsNullOrWhiteSpace(shortName) ? NativeRead(() => type.Name) : shortName;
    }

    private static bool? EndpointContextEqual(Dictionary<string, object> before,
        Dictionary<string, object> after)
    {
        if (!EndpointContextText(before, "current_element") || !EndpointContextText(after, "current_element") ||
            !EndpointContextText(before, "current_element_type") || !EndpointContextText(after, "current_element_type"))
            return null;
        return Equals(before["current_element"], after["current_element"]) &&
            Equals(before["current_element_type"], after["current_element_type"]);
    }

    private static bool EndpointContextText(Dictionary<string, object> context, string field)
    {
        return context != null && context.ContainsKey(field) &&
            context[field] is string value && !string.IsNullOrWhiteSpace(value);
    }

    private static Dictionary<string, object> TypeIdentity(DbElementType type)
    {
        ThrowIfNativeFaulted();
        return new Dictionary<string, object> { ["name"] = type.Name, ["short_name"] = type.ShortName };
    }

    private static Dictionary<string, object> AttributeSchema(DbElement element, string name,
        Dictionary<string, string> errors, bool includeConstraints)
    {
        ThrowIfNativeFaulted();
        Dictionary<string, object> result = new Dictionary<string, object>
        {
            ["requested_name"] = name, ["known"] = null, ["supported"] = null,
            ["sdk_settable_now"] = null, ["direct_edit_supported"] = false
        };
        if (includeConstraints)
            result["constraints"] = new Dictionary<string, object>
            {
                ["status"] = "not_attempted", ["reason"] = "attribute_not_verified"
            };
        try
        {
            DbAttribute attribute = DbAttribute.GetDbAttribute(name);
            if (attribute == null || !attribute.IsValid)
            {
                result["known"] = false;
                result["supported"] = false;
                result["sdk_settable_now"] = false;
                if (includeConstraints)
                    result["constraints"] = new Dictionary<string, object>
                    {
                        ["status"] = "not_attempted", ["reason"] = "unknown_attribute"
                    };
                return result;
            }
            bool supported = element.IsAttributeValid(attribute);
            string input = AttributeEditPolicy.InputKind(attribute.ShortName.ToUpperInvariant());
            result["known"] = true;
            result["name"] = attribute.Name;
            result["short_name"] = attribute.ShortName;
            result["supported"] = supported;
            result["data_type"] = attribute.Type.ToString();
            result["is_array"] = attribute.IsArray;
            result["is_pseudo"] = attribute.IsPseudo;
            result["is_uda"] = attribute.IsUDA;
            // Units describes a category (e.g. distance), not a display unit.
            result["unit_kind"] = attribute.Units.ToString();
            result["maximum_size"] = attribute.MaximumSize;
            result["sdk_settable_now"] = supported && element.IsAttributeSetable(attribute);
            result["direct_edit_supported"] = AttributeEditPolicy.SupportsSdkType(
                attribute.ShortName.ToUpperInvariant(), attribute.Type.ToString(), attribute.IsArray);
            result["direct_edit_input"] = input;
            if (includeConstraints)
                result["constraints"] = supported
                    ? AttributeConstraintsSchema(element, attribute, errors, name)
                    : new Dictionary<string, object>
                    {
                        ["status"] = "not_attempted", ["reason"] = "attribute_not_supported"
                    };
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            result["read_error"] = ex.Message;
            errors["attribute:" + name] = ex.Message;
        }
        return result;
    }

    private static Dictionary<string, object> AttributeConstraintsSchema(DbElement element, DbAttribute attribute,
        Dictionary<string, string> errors, string name)
    {
        string valuesErrorKey = "attribute:" + name + ".allowed_values";
        string rangesErrorKey = "attribute:" + name + ".allowed_ranges";
        string optionalErrorKey = "attribute:" + name + ".allowed_optional";
        var valuesResult = new Dictionary<string, object>
        {
            ["status"] = "not_attempted", ["defined"] = null, ["values"] = new List<object>(),
            ["total_count"] = null, ["returned_count"] = 0, ["truncated"] = false
        };
        var rangesResult = new Dictionary<string, object>
        {
            ["status"] = "not_attempted", ["defined"] = null, ["raw_boundaries"] = new List<object>(),
            ["total_count"] = null, ["returned_count"] = 0, ["truncated"] = false,
            ["unit_basis"] = "unknown"
        };
        var optionalResult = new Dictionary<string, object>
        {
            ["status"] = "not_attempted", ["value"] = null
        };
        var result = new Dictionary<string, object>
        {
            ["status"] = "complete", ["unit_kind"] = NativeRead(() => attribute.Units).ToString(),
            ["values_or_ranges_optional"] = optionalResult,
            ["allowed_values"] = valuesResult, ["allowed_ranges"] = rangesResult
        };

        try
        {
            Tuple<bool, KeyValuePair<string, string>[]> query = NativeRead(() =>
            {
                KeyValuePair<string, string>[] values = null;
                bool defined = element.GetAttributeAllowedValuesAndDescriptions(attribute, ref values);
                return Tuple.Create(defined, values);
            });
            if (!query.Item1)
            {
                valuesResult["status"] = "not_defined";
                valuesResult["defined"] = false;
                valuesResult["total_count"] = 0;
            }
            else if (query.Item2 == null)
            {
                valuesResult["status"] = "read_error";
                valuesResult["defined"] = null;
                valuesResult["error"] = "The SDK reported allowed values but returned no array.";
                errors[valuesErrorKey] = (string)valuesResult["error"];
            }
            else
            {
                valuesResult["status"] = "defined";
                valuesResult["defined"] = true;
                valuesResult["total_count"] = query.Item2.Length;
                int count = Math.Min(query.Item2.Length, AttributeConstraintItemLimit);
                List<object> values = new List<object>(count);
                bool textTruncated = false;
                for (int index = 0; index < count; index++)
                {
                    string value = query.Item2[index].Key;
                    string description = query.Item2[index].Value;
                    bool valueTruncated;
                    bool descriptionTruncated;
                    string boundedValue = BoundedConstraintText(value, out valueTruncated);
                    string boundedDescription = BoundedConstraintText(description, out descriptionTruncated);
                    textTruncated |= valueTruncated || descriptionTruncated;
                    values.Add(new Dictionary<string, object>
                    {
                        ["value"] = boundedValue, ["description"] = boundedDescription,
                        ["value_truncated"] = valueTruncated, ["description_truncated"] = descriptionTruncated
                    });
                }
                valuesResult["values"] = values;
                valuesResult["returned_count"] = count;
                valuesResult["truncated"] = count < query.Item2.Length || textTruncated;
                if (textTruncated) valuesResult["text_truncated"] = true;
            }
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            valuesResult["status"] = "read_error";
            valuesResult["error"] = ex.Message;
            errors[valuesErrorKey] = ex.Message;
        }

        try
        {
            Tuple<bool, double[]> query = NativeRead(() =>
            {
                double[] ranges = null;
                bool defined = element.GetAttributeAllowedRanges(attribute, ref ranges);
                return Tuple.Create(defined, ranges);
            });
            if (!query.Item1)
            {
                rangesResult["status"] = "not_defined";
                rangesResult["defined"] = false;
                rangesResult["total_count"] = 0;
            }
            else if (query.Item2 == null)
            {
                rangesResult["status"] = "read_error";
                rangesResult["defined"] = null;
                rangesResult["error"] = "The SDK reported allowed ranges but returned no array.";
                errors[rangesErrorKey] = (string)rangesResult["error"];
            }
            else
            {
                rangesResult["status"] = "defined";
                rangesResult["defined"] = true;
                rangesResult["total_count"] = query.Item2.Length;
                int count = Math.Min(query.Item2.Length, AttributeConstraintItemLimit);
                List<object> ranges = new List<object>(count);
                List<int> nonFiniteIndices = new List<int>();
                for (int index = 0; index < count; index++)
                {
                    double boundary = query.Item2[index];
                    if (double.IsNaN(boundary) || double.IsInfinity(boundary))
                    {
                        ranges.Add(boundary.ToString("R", System.Globalization.CultureInfo.InvariantCulture));
                        nonFiniteIndices.Add(index);
                    }
                    else ranges.Add(boundary);
                }
                rangesResult["raw_boundaries"] = ranges;
                rangesResult["returned_count"] = count;
                rangesResult["truncated"] = count < query.Item2.Length;
                if (nonFiniteIndices.Count > 0)
                    rangesResult["non_finite_indices"] = nonFiniteIndices;
            }
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            rangesResult["status"] = "read_error";
            rangesResult["error"] = ex.Message;
            errors[rangesErrorKey] = ex.Message;
        }

        try
        {
            optionalResult["value"] = NativeRead(() => element.IsAttributeAllowedOptional(attribute));
            optionalResult["status"] = "complete";
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            optionalResult["status"] = "read_error";
            optionalResult["error"] = ex.Message;
            errors[optionalErrorKey] = ex.Message;
        }

        bool hasReadError = Equals(valuesResult["status"], "read_error") ||
            Equals(rangesResult["status"], "read_error") || Equals(optionalResult["status"], "read_error");
        bool truncated = Equals(valuesResult["truncated"], true) || Equals(rangesResult["truncated"], true);
        result["status"] = hasReadError ? "partial" : truncated ? "truncated" : "complete";
        return result;
    }

    private static string BoundedConstraintText(string value, out bool truncated)
    {
        truncated = value != null && value.Length > AttributeConstraintTextLimit;
        if (!truncated) return value;
        int length = AttributeConstraintTextLimit;
        if (char.IsHighSurrogate(value[length - 1]) && char.IsLowSurrogate(value[length]))
            length--;
        return value.Substring(0, length);
    }
}
