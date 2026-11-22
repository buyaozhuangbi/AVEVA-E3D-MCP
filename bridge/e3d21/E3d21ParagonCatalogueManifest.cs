using System;
using System.Collections.Generic;
using System.Globalization;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context : IE3d21ParagonCatalogueManifestContext
{
    public object ReadParagonCatalogueManifest(string refno, int maxDepth, int limit, IList<string> attributes, string sessionId)
    {
        var request = ParagonCatalogueManifestRequest.Validate(refno, maxDepth, limit, attributes);
        ParagonManifestContextText(sessionId, "bridge session");
        // The HTTP envelope uses the MVID from the same Addin assembly. No
        // status request, module switch, CE assignment or secondary UI dispatch.
        string build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy.");
        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy.");
            var before = ParagonManifestBoundary(sessionId, build, out Project project, out MDB mdb);
            if (project == null || mdb == null)
                throw new InvalidOperationException("A current project and MDB are required for catalogue inspection.");
            DbElement element;
            if (request.Refno[0] == '=')
            {
                string[] parts = request.Refno.Substring(1).Split('/');
                var pair = new[] { int.Parse(parts[0], CultureInfo.InvariantCulture), int.Parse(parts[1], CultureInfo.InvariantCulture) };
                element = NativeRead(() => DbElement.GetElement(pair));
            }
            else element = NativeRead(() => mdb.FindElement(DbType.Catalogue, request.Refno));
            var root = ParagonManifestNode(element);
            if (request.Refno[0] == '=' && root.Identity.Refno != request.Refno)
                throw new InvalidOperationException("The SDK resolved a different database reference.");
            // A numeric target can still retain a failed NAME read. A named
            // target must be independently checked before enumerating members.
            string checkedRootName = null;
            if (request.Refno[0] == '/')
            {
                checkedRootName = ParagonManifestName(element);
                if (!string.Equals(request.Refno.Substring(1), checkedRootName.StartsWith("/", StringComparison.Ordinal)
                    ? checkedRootName.Substring(1) : checkedRootName, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("The SDK resolved a different catalogue name.");
            }

            // EquipmentManifestTraversal is a pure, database-neutral bounded
            // preorder helper. Its callbacks here use catalogue identities and
            // typed native owners, never the Design-only equipment resolver.
            var traversal = EquipmentManifestTraversal<ParagonManifestEntry>.Read(root, root.Identity.Key,
                request.MaxDepth, request.Limit,
                (parent, ordinal) =>
                {
                    DbElement child = NativeRead(() => parent.Element.Member(ordinal));
                    return child == null || NativeRead(() => child.IsNull) ? null : ParagonManifestNode(child);
                }, entry => entry.Identity.Key,
                entry =>
                {
                    entry.Owner = ParagonManifestIdentity(NativeRead(() => entry.Element.Owner), true);
                    return entry.Owner.Key;
                }, error => ThrowIfNativeFaulted());

            var definitions = new List<ParagonManifestAttribute>();
            foreach (string name in request.Attributes) definitions.Add(new ParagonManifestAttribute(name));
            var rows = new List<Dictionary<string, object>>();
            bool rowsComplete = true, allValuesAvailable = true;
            foreach (var item in traversal.Rows)
            {
                var entry = item.Value;
                string ownerError = null;
                if (item.Index == 0)
                {
                    try
                    {
                        DbElement owner = NativeRead(() => entry.Element.Owner);
                        if (owner != null && !NativeRead(() => owner.IsNull)) entry.Owner = ParagonManifestIdentity(owner, false);
                    }
                    catch (Exception error) { ThrowIfNativeFaulted(); ownerError = ParagonCatalogueManifestRequest.ErrorText(error.Message); }
                }
                var row = entry.Identity.ToResult();
                row["id"] = item.Index;
                row["depth"] = item.Depth;
                row["parent_index"] = item.ParentIndex;
                row["sibling_index"] = item.SiblingIndex;
                row["owner"] = entry.Owner == null ? null : entry.Owner.ToResult();
                row["owner_error"] = ownerError;
                row["name"] = null;
                row["name_error"] = null;
                try { row["name"] = item.Index == 0 && checkedRootName != null ? checkedRootName : ParagonManifestName(entry.Element); }
                catch (Exception error) { ThrowIfNativeFaulted(); row["name_error"] = ParagonCatalogueManifestRequest.ErrorText(error.Message); }
                bool fieldsComplete = ParagonManifestAttributes(entry.Element, definitions, row, out bool valuesAvailable);
                bool complete = fieldsComplete && row["name_error"] == null && ownerError == null;
                row["complete"] = complete;
                rowsComplete &= complete;
                allValuesAvailable &= valuesAvailable;
                rows.Add(row);
            }

            var errors = new List<Dictionary<string, object>>();
            foreach (var error in traversal.ReadErrors)
                errors.Add(new Dictionary<string, object> { ["stage"] = "member", ["parent_identity_key"] = error.ParentReference,
                    ["sibling_index"] = error.SiblingIndex, ["message"] = ParagonCatalogueManifestRequest.ErrorText(error.Message) });
            var after = ParagonManifestBoundary(sessionId, build, out Project afterProject, out MDB afterMdb);
            bool? sameProject = project == null || afterProject == null ? (bool?)null : ReferenceEquals(project, afterProject);
            bool? sameMdb = mdb == null || afterMdb == null ? (bool?)null : ReferenceEquals(mdb, afterMdb);
            bool? agreement = ParagonManifestAgreement(before, after, sameProject, sameMdb);
            bool completeObservation = traversal.TruncationReasons.Count == 0 && traversal.ReadErrors.Count == 0
                && rowsComplete && agreement == true;
            return new Dictionary<string, object>
            {
                ["operation"] = "paragon_catalogue_manifest", ["available"] = true,
                ["success"] = completeObservation, ["complete"] = completeObservation,
                ["read_only"] = true, ["single_dispatch"] = true, ["atomic_snapshot"] = false,
                ["requested_refno"] = request.Refno, ["root_id"] = 0,
                ["rows"] = rows, ["returned_count"] = rows.Count, ["order"] = "database_preorder",
                ["max_depth"] = request.MaxDepth, ["limit"] = request.Limit, ["attributes"] = request.Attributes,
                ["traversal_complete"] = traversal.TruncationReasons.Count == 0 && traversal.ReadErrors.Count == 0,
                ["all_returned_values_available"] = allValuesAvailable,
                ["truncated"] = traversal.TruncationReasons.Count != 0, ["truncation_reasons"] = traversal.TruncationReasons,
                ["read_errors"] = errors,
                ["context"] = new { before, after, same_project_instance = sameProject, same_mdb_instance = sameMdb,
                    boundary_agreement = agreement },
                ["scope"] = new { kind = "explicit_catalogue_subtree", database_type = "Catalogue", database_type_code = 2,
                    whole_project_coverage = false, current_module = (string)null,
                    database_creation_verified = (bool?)null, engineering_validated = (bool?)null }
            };
        });
    }

    private sealed class ParagonManifestEntry
    {
        internal DbElement Element;
        internal ParagonManifestIdentityValue Identity, Owner;
    }

    private sealed class ParagonManifestIdentityValue
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

    private static ParagonManifestEntry ParagonManifestNode(DbElement element)
    {
        return new ParagonManifestEntry { Element = element, Identity = ParagonManifestIdentity(element, true) };
    }

    private static ParagonManifestIdentityValue ParagonManifestIdentity(DbElement element, bool requireCatalogue)
    {
        if (element == null || NativeRead(() => element.IsNull) || !NativeRead(() => element.IsValid) || NativeRead(() => element.IsDeleted))
            throw new InvalidOperationException("The SDK element is unavailable or deleted.");
        DbType databaseType = NativeRead(() => element.DbType);
        if (requireCatalogue && databaseType != DbType.Catalogue)
            throw new ArgumentException("The manifest root, members and member owners must belong to a Catalogue database.");
        string reference = ParagonCatalogueManifestRequest.Reference(NativeRead(() => element.Ref));
        // SDK GetElementType returns the native base type, including for UDETs.
        DbElementType sdkType = NativeRead(() => element.GetElementType());
        string type = sdkType == null || !NativeRead(() => sdkType.IsValid) ? null : NativeRead(() => sdkType.ShortName)?.ToUpperInvariant();
        if (string.IsNullOrEmpty(type) || type.Length > 64 || type[0] < 'A' || type[0] > 'Z')
            throw new InvalidOperationException("No valid native type short name was available.");
        foreach (char c in type)
            if (!(c >= 'A' && c <= 'Z') && !(c >= '0' && c <= '9') && c != '_')
                throw new InvalidOperationException("No valid native type short name was available.");
        return new ParagonManifestIdentityValue { Refno = reference, Type = type,
            DatabaseType = databaseType == DbType.Catalogue ? "Catalogue" : databaseType.ToString(), DatabaseTypeCode = (int)databaseType };
    }

    private static string ParagonManifestName(DbElement element)
    {
        string text = ParagonManifestText(NativeRead(() => element.GetAsString(DbAttributeInstance.NAME)));
        if (string.IsNullOrWhiteSpace(text)) throw new InvalidOperationException("No native NAME text was available.");
        return text;
    }

    private static string ParagonManifestText(string text)
    {
        if (text == null || text.Length > ParagonCatalogueManifestRequest.MaximumTextLength)
            throw new InvalidOperationException("The SDK returned null text or text beyond 4096 characters.");
        return text;
    }

    private static string ParagonManifestContextText(string text, string field)
    {
        if (string.IsNullOrWhiteSpace(text) || text.Length > 256)
            throw new InvalidOperationException("No valid " + field + " identity was available.");
        return text;
    }

    private static Dictionary<string, object> ParagonManifestBoundary(string session, string build, out Project project, out MDB mdb)
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
            projectName = ParagonManifestContextText(current == null ? null : NativeRead(() => current.Name), "project");
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["project"] = ParagonCatalogueManifestRequest.ErrorText(error.Message); }
        try
        {
            mdb = NativeRead(() => MDB.CurrentMDB);
            MDB current = mdb;
            mdbName = ParagonManifestContextText(current == null ? null : NativeRead(() => current.Name), "MDB");
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["mdb"] = ParagonCatalogueManifestRequest.ErrorText(error.Message); }
        try { ce = ParagonManifestIdentity(NativeRead(() => Aveva.Core.Database.CurrentElement.Element), false).ToResult(); }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["current_element"] = ParagonCatalogueManifestRequest.ErrorText(error.Message); }
        return new Dictionary<string, object> { ["session_id"] = session, ["bridge_build"] = build,
            ["project"] = projectName, ["mdb"] = mdbName, ["current_element"] = ce, ["errors"] = errors };
    }

    private static bool? ParagonManifestAgreement(Dictionary<string, object> before, Dictionary<string, object> after,
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
            || ((Dictionary<string, string>)before["errors"]).Count != 0 || ((Dictionary<string, string>)after["errors"]).Count != 0)
            return null;
        return true;
    }

    private sealed class ParagonManifestAttribute
    {
        internal readonly string Name;
        internal readonly DbAttribute Attribute;
        internal readonly string Error;
        internal readonly DbAttributeType? ScalarType;
        internal readonly string TypedError;
        internal ParagonManifestAttribute(string name)
        {
            Name = name;
            try
            {
                Attribute = NativeRead(() => DbAttribute.GetDbAttribute(name));
                if (Attribute == null || !NativeRead(() => Attribute.IsValid)) throw new InvalidOperationException("Unknown SDK attribute: " + name);
            }
            catch (Exception error) { ThrowIfNativeFaulted(); Error = ParagonCatalogueManifestRequest.ErrorText(error.Message); return; }
            try
            {
                DbAttributeType type = NativeRead(() => Attribute.Type);
                if (type == DbAttributeType.STRING || !NativeRead(() => Attribute.IsArray)
                    && (type == DbAttributeType.DOUBLE || type == DbAttributeType.INTEGER || type == DbAttributeType.BOOL)) ScalarType = type;
            }
            catch (Exception error) { ThrowIfNativeFaulted(); TypedError = ParagonCatalogueManifestRequest.ErrorText(error.Message); }
        }
    }

    private static bool ParagonManifestAttributes(DbElement element, IList<ParagonManifestAttribute> definitions,
        Dictionary<string, object> row, out bool allAvailable)
    {
        var values = new Dictionary<string, string>();
        var typed = new Dictionary<string, object>();
        var errors = new Dictionary<string, string>();
        var typedErrors = new Dictionary<string, string>();
        var unavailable = new List<string>();
        row["values"] = values; row["typed_values"] = typed; row["errors"] = errors;
        row["typed_read_errors"] = typedErrors; row["unavailable_attributes"] = unavailable;
        foreach (var field in definitions)
        {
            string name = field.Name;
            if (field.Error != null) { errors[name] = field.Error; continue; }
            try
            {
                if (!NativeRead(() => element.IsAttributeValid(field.Attribute))) { unavailable.Add(name); continue; }
                values[name] = ParagonManifestText(NativeRead(() => element.GetAsString(field.Attribute)));
            }
            catch (Exception error) { ThrowIfNativeFaulted(); errors[name] = ParagonCatalogueManifestRequest.ErrorText(error.Message); continue; }
            if (field.TypedError != null) { typedErrors[name] = field.TypedError; continue; }
            try
            {
                switch (field.ScalarType)
                {
                    case DbAttributeType.DOUBLE:
                        double number = NativeRead(() => element.GetDouble(field.Attribute));
                        if (double.IsNaN(number) || double.IsInfinity(number)) throw new InvalidOperationException("The SDK returned a non-finite scalar.");
                        typed[name] = number; break;
                    case DbAttributeType.INTEGER: typed[name] = NativeRead(() => element.GetInteger(field.Attribute)); break;
                    case DbAttributeType.BOOL: typed[name] = NativeRead(() => element.GetBool(field.Attribute)); break;
                    case DbAttributeType.STRING: typed[name] = ParagonManifestText(NativeRead(() => element.GetString(field.Attribute))); break;
                }
            }
            catch (Exception error) { ThrowIfNativeFaulted(); typedErrors[name] = ParagonCatalogueManifestRequest.ErrorText(error.Message); }
        }
        allAvailable = errors.Count == 0 && typedErrors.Count == 0 && unavailable.Count == 0;
        return allAvailable;
    }
}
