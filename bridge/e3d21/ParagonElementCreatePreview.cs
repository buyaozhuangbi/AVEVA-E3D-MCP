using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;
using Aveva.Core.Database;

namespace E3d21AiBridge;

/// <summary>
/// Read-only SDK observations for one proposed PARAGON element type beneath
/// an explicitly resolved Catalogue parent. The caller owns target resolution
/// and UI-thread dispatch. This class contains no PML or mutation path.
/// </summary>
public static class ParagonElementCreatePreview
{
    private static readonly string[] CandidateTypes = { "CATA", "SECT", "CATE" };
    private static readonly Regex NumericTarget = new Regex(
        @"^=[1-9][0-9]{0,9}/(?:0|[1-9][0-9]{0,9})$", RegexOptions.CultureInvariant);

    public static object Read(string requestedRefno, string requestedType, DbElement target)
    {
        if (!IsExplicitTarget(requestedRefno))
            throw new ArgumentException("An explicit /NAME or =database/element target is required.", "requestedRefno");
        string candidateName = (requestedType ?? "").Trim().ToUpperInvariant();
        if (!CandidateTypes.Contains(candidateName, StringComparer.Ordinal))
            throw new ArgumentException("element_type must be CATA, SECT or CATE.", "requestedType");

        var targetErrors = new Dictionary<string, string>(StringComparer.Ordinal);
        var targetRow = new Dictionary<string, object>(StringComparer.Ordinal)
        {
            ["requested_refno"] = requestedRefno,
            ["resolved_refno"] = null,
            ["resolved_name"] = null,
            ["element_type"] = null,
            ["database_type"] = null,
            ["database_type_code"] = null,
            ["target_matches_request"] = null,
            ["catalogue_target"] = null,
            ["errors"] = targetErrors,
            ["complete"] = false
        };

        int[] reference = null;
        DbElementType parentType = null;
        bool catalogueTarget = false;
        if (target == null)
        {
            targetErrors["resolve"] = "The explicit target could not be resolved.";
        }
        else
        {
            try
            {
                reference = target.Ref;
                if (reference == null || reference.Length != 2 || reference[0] <= 0 || reference[1] < 0)
                    throw new InvalidOperationException("The SDK returned an invalid element reference.");
                targetRow["resolved_refno"] = "=" + reference[0].ToString(System.Globalization.CultureInfo.InvariantCulture)
                    + "/" + reference[1].ToString(System.Globalization.CultureInfo.InvariantCulture);
            }
            catch (Exception error) { targetErrors["refno"] = SafeError(error); }

            string targetName = null;
            try
            {
                targetName = target.GetAsString(DbAttributeInstance.NAME);
                if (string.IsNullOrWhiteSpace(targetName))
                    throw new InvalidOperationException("The SDK returned an empty target NAME.");
                targetRow["resolved_name"] = targetName;
            }
            catch (Exception error) { targetErrors["name"] = SafeError(error); }

            if (!targetErrors.ContainsKey("refno") && !targetErrors.ContainsKey("name"))
            {
                string resolvedRefno = targetRow["resolved_refno"] as string;
                bool matches = requestedRefno.StartsWith("=", StringComparison.Ordinal)
                    ? string.Equals(requestedRefno, resolvedRefno, StringComparison.Ordinal)
                    : string.Equals(requestedRefno.Substring(1), targetName.StartsWith("/", StringComparison.Ordinal)
                        ? targetName.Substring(1) : targetName, StringComparison.OrdinalIgnoreCase);
                targetRow["target_matches_request"] = matches;
            }

            try
            {
                DbType type = target.DbType;
                int code = (int)type;
                targetRow["database_type"] = type == DbType.Catalogue ? "Catalogue" : type.ToString();
                targetRow["database_type_code"] = code;
                catalogueTarget = type == DbType.Catalogue;
                targetRow["catalogue_target"] = catalogueTarget;
            }
            catch (Exception error) { targetErrors["database_type"] = SafeError(error); }

            try
            {
                parentType = target.GetElementType();
                if (parentType == null || !parentType.IsValid)
                    throw new InvalidOperationException("The SDK returned no valid target element type.");
                string name = parentType.ShortName;
                if (string.IsNullOrWhiteSpace(name))
                    throw new InvalidOperationException("The SDK returned an empty target element type name.");
                targetRow["element_type"] = name;
            }
            catch (Exception error) { targetErrors["element_type"] = SafeError(error); }
        }
        bool targetMatches = Equals(targetRow["target_matches_request"], true);
        targetRow["complete"] = targetErrors.Count == 0 && targetMatches;

        bool nativeTargetUsable = target != null && catalogueTarget && targetMatches && targetErrors.Count == 0;
        var candidateErrors = new Dictionary<string, string>(StringComparer.Ordinal);
        bool? member = null;
        bool? creatable = null;
        DbElementType candidateType = null;
        if (!nativeTargetUsable)
        {
            candidateErrors["target"] = "SDK candidate reads were not queried because the explicit target was not verified as a Catalogue target.";
            candidateErrors["member_types"] = "MemberTypes was not queried without a verified Catalogue target.";
            candidateErrors["is_creatable"] = "IsCreatable was not queried without a verified Catalogue target.";
        }
        else
        {
            try
            {
                candidateType = DbElementType.GetElementType(candidateName);
                if (candidateType == null || !candidateType.IsValid)
                    throw new InvalidOperationException("The SDK returned no valid candidate element type.");
            }
            catch (Exception error) { candidateErrors["element_type"] = SafeError(error); }

            if (parentType == null)
                candidateErrors["member_types"] = "MemberTypes was not queried because the target element type could not be read.";
            else
            {
                try
                {
                    DbElementType[] allowedTypes = parentType.MemberTypes();
                    if (allowedTypes == null) throw new InvalidOperationException("The SDK returned a null MemberTypes array.");
                    if (candidateType != null) member = allowedTypes.Any(item => item == candidateType);
                }
                catch (Exception error) { candidateErrors["member_types"] = SafeError(error); }
            }

            if (candidateType != null && !candidateErrors.ContainsKey("element_type"))
            {
                try { creatable = target.IsCreatable(candidateType); }
                catch (Exception error) { candidateErrors["is_creatable"] = SafeError(error); }
            }
        }

        bool candidateComplete = member.HasValue && creatable.HasValue && candidateErrors.Count == 0;
        var candidate = new Dictionary<string, object>(StringComparer.Ordinal)
        {
            ["type"] = candidateName,
            ["member_type"] = member,
            ["sdk_is_creatable_now"] = creatable,
            ["errors"] = candidateErrors,
            ["complete"] = candidateComplete
        };
        bool complete = (bool)targetRow["complete"] && catalogueTarget && candidateComplete;
        var readErrors = new Dictionary<string, object>(StringComparer.Ordinal)
        {
            ["target"] = targetErrors,
            ["candidate"] = candidateErrors
        };
        var scope = new Dictionary<string, object>(StringComparer.Ordinal)
        {
            ["kind"] = "paragon_element_create_preview",
            ["module"] = null,
            ["permissions_verified"] = null,
            ["revision"] = null,
            ["whole_project_coverage"] = false,
            ["pml_executed"] = false,
            ["element_creation_attempted"] = false,
            ["element_created"] = false
        };
        return new Dictionary<string, object>(StringComparer.Ordinal)
        {
            ["operation"] = "paragon_element_create_preview",
            ["requested_refno"] = requestedRefno,
            ["requested_type"] = candidateName,
            ["target"] = targetRow,
            ["candidate"] = candidate,
            ["read_errors"] = readErrors,
            ["complete"] = complete,
            ["success"] = complete,
            ["read_only"] = true,
            ["preview_only"] = true,
            ["native_side_effects"] = false,
            ["execution_supported"] = false,
            ["scope"] = scope
        };
    }

    private static bool IsExplicitTarget(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Length > 256 || value != value.Trim()
            || value.IndexOfAny(new[] { '*', '?' }) >= 0 || value.Any(char.IsWhiteSpace) || value.Any(char.IsControl))
            return false;
        if (value[0] == '=')
        {
            if (!NumericTarget.IsMatch(value)) return false;
            string[] parts = value.Substring(1).Split('/');
            int database, element;
            return int.TryParse(parts[0], NumberStyles.None, CultureInfo.InvariantCulture, out database) && database > 0
                && int.TryParse(parts[1], NumberStyles.None, CultureInfo.InvariantCulture, out element) && element >= 0;
        }
        return value[0] == '/' && value.Length > 1;
    }

    private static string SafeError(Exception error)
    {
        string text = error == null ? "Unknown SDK read failure." : error.Message;
        if (string.IsNullOrWhiteSpace(text)) text = error == null ? "Unknown SDK read failure." : error.GetType().Name;
        return text.Length <= 256 ? text : text.Substring(0, 256);
    }
}
