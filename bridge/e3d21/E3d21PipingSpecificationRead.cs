using System;
using System.Collections.Generic;
using System.Globalization;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context : IE3d21PipingSpecificationReadContext
{
    /// <summary>
    /// One explicit numeric typed root, bounded ownership traversal, scalar attributes
    /// and one-hop reference identities. Reference targets are never expanded.
    /// </summary>
    public object ReadPipingSpecification(string refno, string expectedType, string databaseType,
        int maxDepth, int limit, IList<string> attributes, string sessionId)
    {
        var request = PipingSpecificationReadRequest.Validate(refno, expectedType, databaseType,
            maxDepth, limit, attributes, sessionId);
        var scope = request.Scope;
        string build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy.");
        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy.");
            var before = PipingSpecBoundary(sessionId, build, out Project project, out MDB mdb);
            if (ReferenceEquals(project, null) || ReferenceEquals(mdb, null))
                throw new InvalidOperationException("A current project and MDB are required.");

            string[] parts = scope.Refno.Substring(1).Split('/');
            var pair = new[] { int.Parse(parts[0], CultureInfo.InvariantCulture), int.Parse(parts[1], CultureInfo.InvariantCulture) };
            // Bind the first observed canonical DBREF, base type and native
            // database type to the explicit request. Shared BRAN/TUBI member
            // identities remain separate; the root is never promoted to an alias.
            DbElement element = NativeRead(() => DbElement.GetElement(pair));

            var root = PipingSpecNode(element, request.DatabaseTypeCode);
            if (root.Identity.Type != request.ExpectedType
                || root.Identity.Refno != scope.Refno)
                throw new InvalidOperationException("The SDK resolved a different typed target.");

            var traversal = EquipmentManifestTraversal<PipingSpecEntry>.Read(root, root.Identity.Key,
                scope.MaxDepth, scope.Limit, (parent, ordinal) =>
                {
                    DbElement child = NativeRead(() => parent.Element.Member(ordinal));
                    return ReferenceEquals(child, null) || NativeRead(() => child.IsNull) ? null : PipingSpecNode(child, request.DatabaseTypeCode);
                }, entry => entry.Identity.Key, entry =>
                {
                    entry.Owner = PipingSpecIdentity(NativeRead(() => entry.Element.Owner));
                    if (entry.Owner.DatabaseTypeCode != request.DatabaseTypeCode)
                        throw new InvalidOperationException("The native member owner belongs to a different database type.");
                    return entry.Owner.Key;
                }, error => ThrowIfNativeFaulted());

            var rows = new List<Dictionary<string, object>>();
            bool rowsComplete = true;
            foreach (var item in traversal.Rows)
            {
                var entry = item.Value;
                var row = PipingSpecObservation(entry.Element, entry.Identity);
                row["id"] = item.Index;
                row["parent_index"] = item.ParentIndex;
                row["sibling_index"] = item.SiblingIndex;
                row["depth"] = item.Depth;
                row["owner"] = entry.Owner == null ? null : entry.Owner.ToResult();
                row["owner_error"] = null;
                if (item.Index == 0)
                {
                    try
                    {
                        DbElement owner = NativeRead(() => element.Owner);
                        if (!ReferenceEquals(owner, null) && !NativeRead(() => owner.IsNull))
                            row["owner"] = PipingSpecIdentity(owner).ToResult();
                        else row["owner_error"] = "native_null_owner";
                    }
                    catch (Exception error) { ThrowIfNativeFaulted(); row["owner_error"] = PipingSpecError(error); }
                }
                var values = new Dictionary<string, object>();
                bool valuesComplete = true;
                foreach (string name in scope.Attributes)
                {
                    var value = PipingSpecAttribute(entry.Element, name);
                    values[name] = value;
                    valuesComplete &= Equals(value["status"], "observed");
                }
                row["attributes"] = values;
                row["complete"] = valuesComplete && row["name_error"] == null && row["owner_error"] == null
                    && Equals(((Dictionary<string, object>)row["database"])["status"], "observed");
                rowsComplete &= Equals(row["complete"], true);
                rows.Add(row);
            }

            var errors = new List<Dictionary<string, object>>();
            foreach (var error in traversal.ReadErrors)
                errors.Add(new Dictionary<string, object> { ["stage"] = "member", ["parent_identity_key"] = error.ParentReference,
                    ["sibling_index"] = error.SiblingIndex, ["message"] = ParagonCatalogueManifestRequest.ErrorText(error.Message) });
            var after = PipingSpecBoundary(sessionId, build, out Project afterProject, out MDB afterMdb);
            bool? sameProject = ReferenceEquals(project, null) || ReferenceEquals(afterProject, null) ? (bool?)null : ReferenceEquals(project, afterProject);
            bool? sameMdb = ReferenceEquals(mdb, null) || ReferenceEquals(afterMdb, null) ? (bool?)null : ReferenceEquals(mdb, afterMdb);
            bool? agreement = PipingSpecAgreement(before, after, sameProject, sameMdb);
            bool traversalComplete = traversal.TruncationReasons.Count == 0 && traversal.ReadErrors.Count == 0;
            bool complete = rowsComplete && traversalComplete && agreement == true;
            return new Dictionary<string, object>
            {
                ["operation"] = "piping_specification_read", ["available"] = true,
                ["success"] = complete, ["complete"] = complete,
                ["read_only"] = true, ["single_dispatch"] = true, ["atomic_snapshot"] = false,
                ["requested_refno"] = scope.Refno, ["expected_type"] = request.ExpectedType,
                ["database_type"] = request.DatabaseType, ["database_type_code"] = request.DatabaseTypeCode,
                ["attributes"] = scope.Attributes, ["max_depth"] = scope.MaxDepth, ["limit"] = scope.Limit,
                ["rows"] = rows, ["returned_count"] = rows.Count, ["order"] = "database_preorder",
                ["traversal_complete"] = traversalComplete, ["truncated"] = traversal.TruncationReasons.Count != 0,
                ["truncation_reasons"] = traversal.TruncationReasons, ["read_errors"] = errors,
                ["context"] = new { before, after, same_project_instance = sameProject, same_mdb_instance = sameMdb,
                    boundary_agreement = agreement },
                ["scope"] = new { kind = "explicit_typed_subtree_and_reference_identities",
                    reference_hops = 1, reference_targets_expanded = false, whole_project_coverage = false,
                    units_verified = false, engineering_compatible = (bool?)null, material_selected = (bool?)null,
                    database_access_authorized = (bool?)null },
                ["completeness_scope"] = "Requested raw/typed observations and bounded ownership traversal only; units and engineering suitability remain unverified."
            };
        });
    }

    private sealed class PipingSpecEntry
    {
        internal DbElement Element;
        internal PipingSpecIdentityValue Identity, Owner;
    }

    // Deliberately local: the older Catalogue helper uses SDK-overloaded null
    // operators. Pure null checks must never create an unguarded SDK call.
    private sealed class PipingSpecIdentityValue
    {
        internal string Refno, Type, DatabaseType;
        internal int DatabaseTypeCode;
        internal string Key => Refno + "|" + Type;
        internal Dictionary<string, object> ToResult() => new Dictionary<string, object>
        {
            ["refno"] = Refno, ["type"] = Type, ["db_type"] = DatabaseType,
            ["db_type_code"] = DatabaseTypeCode, ["identity_key"] = Key
        };
    }

    private static PipingSpecIdentityValue PipingSpecIdentity(DbElement element)
    {
        if (ReferenceEquals(element, null) || NativeRead(() => element.IsNull)
            || !NativeRead(() => element.IsValid) || NativeRead(() => element.IsDeleted))
            throw new InvalidOperationException("The SDK element is unavailable or deleted.");
        DbType databaseType = NativeRead(() => element.DbType);
        string reference = ParagonCatalogueManifestRequest.Reference(NativeRead(() => element.Ref));
        // This is the SDK base type, including for UDETs, never an inferred alias.
        DbElementType sdkType = NativeRead(() => element.GetElementType());
        string type = ReferenceEquals(sdkType, null) || !NativeRead(() => sdkType.IsValid)
            ? null : NativeRead(() => sdkType.ShortName)?.ToUpperInvariant();
        if (string.IsNullOrEmpty(type) || type.Length > 64 || type[0] < 'A' || type[0] > 'Z')
            throw new InvalidOperationException("No valid native type short name was available.");
        foreach (char c in type)
            if (!(c >= 'A' && c <= 'Z') && !(c >= '0' && c <= '9') && c != '_')
                throw new InvalidOperationException("No valid native type short name was available.");
        return new PipingSpecIdentityValue { Refno = reference, Type = type,
            DatabaseType = databaseType == DbType.Catalogue ? "Catalogue" : databaseType.ToString(),
            DatabaseTypeCode = (int)databaseType };
    }

    private static string PipingSpecText(string text)
    {
        if (text == null || text.Length > ParagonCatalogueManifestRequest.MaximumTextLength)
            throw new InvalidOperationException("The SDK returned null text or text beyond 4096 characters.");
        return text;
    }

    private static string PipingSpecName(DbElement element)
    {
        string text = PipingSpecText(NativeRead(() => element.GetAsString(DbAttributeInstance.NAME)));
        if (string.IsNullOrWhiteSpace(text)) throw new InvalidOperationException("No native NAME text was available.");
        return text;
    }

    private static string PipingSpecContextText(string text, string field)
    {
        if (string.IsNullOrWhiteSpace(text) || text.Length > 256)
            throw new InvalidOperationException("No valid " + field + " identity was available.");
        return text;
    }

    private static Dictionary<string, object> PipingSpecBoundary(string session, string build,
        out Project project, out MDB mdb)
    {
        project = null;
        mdb = null;
        string projectName = null, mdbName = null;
        Dictionary<string, object> ce = null;
        var errors = new Dictionary<string, string>();
        try
        {
            project = NativeRead(() => Project.CurrentProject);
            Project current = project;
            projectName = PipingSpecContextText(ReferenceEquals(current, null) ? null : NativeRead(() => current.Name), "project");
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["project"] = PipingSpecError(error); }
        try
        {
            mdb = NativeRead(() => MDB.CurrentMDB);
            MDB current = mdb;
            mdbName = PipingSpecContextText(ReferenceEquals(current, null) ? null : NativeRead(() => current.Name), "MDB");
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["mdb"] = PipingSpecError(error); }
        try { ce = PipingSpecIdentity(NativeRead(() => Aveva.Core.Database.CurrentElement.Element)).ToResult(); }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["current_element"] = PipingSpecError(error); }
        return new Dictionary<string, object> { ["session_id"] = session, ["bridge_build"] = build,
            ["project"] = projectName, ["mdb"] = mdbName, ["current_element"] = ce, ["errors"] = errors };
    }

    private static bool? PipingSpecAgreement(Dictionary<string, object> before, Dictionary<string, object> after,
        bool? sameProject, bool? sameMdb)
    {
        if (sameProject == false || sameMdb == false) return false;
        foreach (string name in new[] { "session_id", "bridge_build", "project", "mdb" })
            if (before[name] != null && after[name] != null && !Equals(before[name], after[name])) return false;
        var first = before["current_element"] as Dictionary<string, object>;
        var last = after["current_element"] as Dictionary<string, object>;
        if (first != null && last != null)
            foreach (string name in new[] { "identity_key", "db_type", "db_type_code" })
                if (!Equals(first[name], last[name])) return false;
        if (sameProject == null || sameMdb == null || first == null || last == null
            || ((Dictionary<string, string>)before["errors"]).Count != 0
            || ((Dictionary<string, string>)after["errors"]).Count != 0) return null;
        return true;
    }

    private static PipingSpecEntry PipingSpecNode(DbElement element, int databaseType)
    {
        var identity = PipingSpecIdentity(element);
        if (identity.DatabaseTypeCode != databaseType)
            throw new InvalidOperationException("The SDK target/member belongs to a different database type.");
        return new PipingSpecEntry { Element = element, Identity = identity };
    }

    private static string PipingSpecError(Exception error) => ParagonCatalogueManifestRequest.ErrorText(error.Message);

    private static Dictionary<string, object> PipingSpecObservation(DbElement element, PipingSpecIdentityValue identity)
    {
        var result = identity.ToResult();
        result["name"] = null;
        result["name_error"] = null;
        try { result["name"] = PipingSpecName(element); }
        catch (Exception error) { ThrowIfNativeFaulted(); result["name_error"] = PipingSpecError(error); }
        var database = new Dictionary<string, object> { ["status"] = "unknown", ["name"] = null,
            ["number"] = null, ["type"] = null, ["type_code"] = null, ["errors"] = new Dictionary<string, string>() };
        result["database"] = database;
        var errors = (Dictionary<string, string>)database["errors"];
        Db db = null;
        try
        {
            db = NativeRead(() => element.Db);
            if (ReferenceEquals(db, null)) throw new InvalidOperationException("No native database identity was returned.");
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["database"] = PipingSpecError(error); return result; }
        try { database["name"] = PipingSpecContextText(NativeRead(() => db.Name), "database"); }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["name"] = PipingSpecError(error); }
        try
        {
            int number = NativeRead(() => db.Number);
            // Preserve the independent native database number. Do not infer
            // database/extract identity from a formatted name or a REF prefix.
            if (number < 1) throw new InvalidOperationException("No positive native database number was returned.");
            database["number"] = number;
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["number"] = PipingSpecError(error); }
        try
        {
            DbType type = NativeRead(() => db.Type);
            database["type"] = type == DbType.Catalogue ? "Catalogue" : type.ToString();
            database["type_code"] = (int)type;
            if ((int)type < 1) throw new InvalidOperationException("Native database type is unknown or invalid.");
            if ((int)type != identity.DatabaseTypeCode) throw new InvalidOperationException("Native database types disagree.");
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["type"] = PipingSpecError(error); }
        database["status"] = errors.Count == 0 ? "observed" : "unknown";
        return result;
    }

    private static Dictionary<string, object> PipingSpecAttribute(DbElement element, string name)
    {
        var errors = new Dictionary<string, string>();
        var result = new Dictionary<string, object>
        {
            ["requested_name"] = name, ["status"] = "unknown", ["supported"] = null,
            ["sdk_type"] = null, ["is_array"] = null, ["unit_kind"] = null,
            ["unit"] = null, ["unit_status"] = "unknown", ["unit_reason"] = "no_explicit_unit_conversion",
            ["formatted_value"] = null, ["formatted_status"] = "unknown",
            ["typed_value"] = null, ["typed_status"] = "unknown", ["reference_null"] = null, ["errors"] = errors
        };
        DbAttribute attribute;
        try
        {
            attribute = NativeRead(() => DbAttribute.GetDbAttribute(name));
            if (ReferenceEquals(attribute, null) || !NativeRead(() => attribute.IsValid))
                throw new InvalidOperationException("Unknown native attribute.");
            bool supported = NativeRead(() => element.IsAttributeValid(attribute));
            result["supported"] = supported;
            if (!supported) { errors["value"] = "unsupported_attribute_value_unknown"; return result; }
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["attribute"] = PipingSpecError(error); return result; }
        try { result["sdk_type"] = NativeRead(() => attribute.Type).ToString(); }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["sdk_type"] = PipingSpecError(error); }
        try { result["is_array"] = NativeRead(() => attribute.IsArray); }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["is_array"] = PipingSpecError(error); }
        // Units is a category such as DIST/BORE, not a verified display unit.
        try { result["unit_kind"] = NativeRead(() => attribute.Units).ToString(); }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["unit_kind"] = PipingSpecError(error); }
        try
        {
            result["formatted_value"] = PipingSpecText(NativeRead(() => element.GetAsString(attribute)));
            result["formatted_status"] = "observed";
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["formatted"] = PipingSpecError(error); }
        try
        {
            if (!Equals(result["is_array"], false)) throw new InvalidOperationException("array_or_array_shape_unknown_not_expanded");
            switch (result["sdk_type"] as string)
            {
                case "ELEMENT":
                    DbElement target = NativeRead(() => element.GetElement(attribute));
                    if (ReferenceEquals(target, null) || NativeRead(() => target.IsNull))
                    {
                        result["reference_null"] = true;
                        throw new InvalidOperationException("native_null_reference_value_unknown");
                    }
                    result["reference_null"] = false;
                    var observation = PipingSpecObservation(target, PipingSpecIdentity(target));
                    result["typed_value"] = observation;
                    result["typed_status"] = "observed";
                    if (observation["name_error"] != null
                        || !Equals(((Dictionary<string, object>)observation["database"])["status"], "observed"))
                        errors["reference_metadata"] = "Reference identity observed; name or database metadata remains unknown.";
                    break;
                case "DOUBLE":
                    double number = NativeRead(() => element.GetDouble(attribute));
                    if (double.IsNaN(number) || double.IsInfinity(number)) throw new InvalidOperationException("Non-finite native scalar.");
                    result["typed_value"] = number; result["typed_status"] = "observed"; break;
                case "INTEGER": result["typed_value"] = NativeRead(() => element.GetInteger(attribute)); result["typed_status"] = "observed"; break;
                case "BOOL": result["typed_value"] = NativeRead(() => element.GetBool(attribute)); result["typed_status"] = "observed"; break;
                case "STRING": result["typed_value"] = PipingSpecText(NativeRead(() => element.GetString(attribute))); result["typed_status"] = "observed"; break;
                default: throw new InvalidOperationException("native_typed_value_kind_unknown_not_expanded");
            }
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["typed"] = PipingSpecError(error); }
        if (errors.Count == 0 && Equals(result["formatted_status"], "observed") && Equals(result["typed_status"], "observed"))
            result["status"] = "observed";
        return result;
    }
}
