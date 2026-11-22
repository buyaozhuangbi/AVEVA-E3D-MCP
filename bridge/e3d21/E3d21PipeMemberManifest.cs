using System;
using System.Collections.Generic;
using System.Globalization;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context : IE3d21PipeMemberManifestContext
{
    public object ReadPipeMemberManifest(string refno, int maxDepth, int limit, IList<string> attributes)
    {
        var request = PipeMemberManifestRequest.Validate(refno, maxDepth, limit, attributes);
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy.");
        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy.");
            Project project = NativeRead(() => Project.CurrentProject);
            MDB mdb = NativeRead(() => MDB.CurrentMDB);
            var before = PipeManifestContext(project, mdb);
            DbElement root;
            if (request.Refno[0] == '=')
            {
                string[] parts = request.Refno.Substring(1).Split('/');
                var pair = new[] { int.Parse(parts[0], CultureInfo.InvariantCulture), int.Parse(parts[1], CultureInfo.InvariantCulture) };
                root = NativeRead(() => DbElement.GetElement(pair));
            }
            else
                root = NativeRead(() => mdb.FindElement(request.Refno));
            var identity = PipeManifestIdentity(root);
            if (identity.Type != "PIPE" && identity.Type != "BRAN")
                throw new ArgumentException("The manifest root must be a Design PIPE or BRAN.");
            string rootName = PipeManifestName(root);
            if (request.Refno[0] == '=' ? request.Refno != identity.Refno :
                !string.Equals(request.Refno.Substring(1), rootName.StartsWith("/", StringComparison.Ordinal)
                    ? rootName.Substring(1) : rootName, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("The manifest root does not match the explicit target.");

            var traversal = new PipeMemberTraversal<DbElement>(root, identity, request,
                (parent, ordinal) =>
                {
                    DbElement child = NativeRead(() => parent.Member(ordinal));
                    return child == null || NativeRead(() => child.IsNull) ? null : child;
                }, member => PipeManifestIdentity(member), member => NativeRead(() => member.Owner), ThrowIfNativeFaulted);

            // Resolve SDK attribute definitions once. Read values on the exact member handles;
            // looking the rows up by DBREF would substitute the flange for its implicit tube.
            var definitions = new List<EquipmentManifestAttribute>();
            if (traversal.Rows.Count != 0)
                foreach (string name in request.Attributes)
                    definitions.Add(new EquipmentManifestAttribute(name));
            var rows = new List<Dictionary<string, object>>(traversal.Rows.Count);
            bool rowsComplete = true;
            bool allReturnedValuesAvailable = true;
            foreach (var entry in traversal.Rows)
            {
                var row = new Dictionary<string, object>
                {
                    ["id"] = entry.Id, ["refno"] = entry.Refno, ["type"] = entry.Type,
                    ["identity_key"] = entry.IdentityKey, ["parent_index"] = entry.ParentIndex,
                    ["depth"] = entry.Depth, ["sibling_index"] = entry.SiblingIndex,
                    ["name"] = null, ["name_error"] = null
                };
                try { row["name"] = PipeManifestName(entry.Member); }
                catch (Exception error)
                {
                    ThrowIfNativeFaulted();
                    row["name_error"] = PipeMemberManifestRequest.ErrorText(error.Message);
                }
                bool fieldsRead = PipeManifestAttributes(entry.Member, definitions, row, out bool valuesAvailable);
                bool rowComplete = fieldsRead && row["name_error"] == null;
                row["complete"] = rowComplete;
                rowsComplete &= rowComplete;
                allReturnedValuesAvailable &= valuesAvailable;
                rows.Add(row);
            }

            var errors = new List<Dictionary<string, object>>(traversal.ReadErrors);
            Dictionary<string, object> after = null;
            bool? sameProject = null, sameMdb = null;
            bool? agrees = null;
            try
            {
                Project afterProject = NativeRead(() => Project.CurrentProject);
                MDB afterMdb = NativeRead(() => MDB.CurrentMDB);
                after = PipeManifestContext(afterProject, afterMdb);
                sameProject = ReferenceEquals(project, afterProject);
                sameMdb = ReferenceEquals(mdb, afterMdb);
                agrees = sameProject == true && sameMdb == true &&
                    Equals(before["project"], after["project"]) && Equals(before["mdb"], after["mdb"]) &&
                    Equals(before["current_element"], after["current_element"]) &&
                    Equals(before["current_element_type"], after["current_element_type"]);
                if (agrees == false)
                    errors.Add(PipeManifestBoundaryError("Project, MDB or typed current element changed during the read."));
            }
            catch (Exception error)
            {
                ThrowIfNativeFaulted();
                errors.Add(PipeManifestBoundaryError(error.Message));
            }
            bool complete = traversal.Complete && rowsComplete && agrees == true;
            return new Dictionary<string, object>
            {
                ["operation"] = "pipe_member_manifest", ["available"] = true, ["success"] = complete,
                ["complete"] = complete, ["traversal_complete"] = traversal.Complete,
                ["all_returned_values_available"] = allReturnedValuesAvailable,
                ["read_only"] = true, ["atomic_snapshot"] = false, ["single_dispatch"] = true,
                ["requested_refno"] = request.Refno,
                ["root"] = new { id = 0, refno = identity.Refno, type = identity.Type,
                    name = rootName, identity_key = identity.Refno + "|" + identity.Type, depth = 0 },
                ["rows"] = rows, ["returned_count"] = rows.Count, ["order"] = "database_preorder",
                ["max_depth"] = request.MaxDepth, ["limit"] = request.Limit, ["attributes"] = request.Attributes,
                ["truncated"] = traversal.TruncationReasons.Count != 0,
                ["truncation_reasons"] = traversal.TruncationReasons, ["read_errors"] = errors,
                ["context"] = new { before, after, same_project_instance = sameProject, same_mdb_instance = sameMdb,
                    boundary_agreement = agrees }
            };
        });
    }

    private static (string Refno, string Type) PipeManifestIdentity(DbElement element, bool designOnly = true)
    {
        if (element == null || NativeRead(() => element.IsNull) ||
            !NativeRead(() => element.IsValid) || NativeRead(() => element.IsDeleted))
            throw new InvalidOperationException("The SDK element is unavailable or deleted.");
        if (designOnly && NativeRead(() => element.DbType) != DbType.Design)
            throw new InvalidOperationException("The manifest requires Design database elements.");
        string reference = ModelReference(NativeRead(() => element.Ref));
        DbElementType sdkType = NativeRead(() => element.GetElementType());
        string type = sdkType == null ? null : NativeRead(() => sdkType.ShortName)?.ToUpperInvariant();
        if (type == null || type.Length < 1 || type.Length > 64 || type[0] < 'A' || type[0] > 'Z')
            throw new InvalidOperationException("No valid SDK type short name was available.");
        foreach (char c in type)
            if (!(c >= 'A' && c <= 'Z') && !(c >= '0' && c <= '9') && c != '_')
                throw new InvalidOperationException("No valid SDK type short name was available.");
        return (reference, type);
    }

    private static string PipeManifestName(DbElement element)
    {
        string name = EquipmentManifestText(NativeRead(() => element.GetAsString(DbAttributeInstance.NAME)));
        if (string.IsNullOrWhiteSpace(name)) throw new InvalidOperationException("No SDK NAME text was available.");
        return name;
    }

    private static Dictionary<string, object> PipeManifestContext(Project project, MDB mdb)
    {
        string projectName = EquipmentManifestContextName(project == null ? null : NativeRead(() => project.Name), "project");
        string mdbName = EquipmentManifestContextName(mdb == null ? null : NativeRead(() => mdb.Name), "MDB");
        var ce = PipeManifestIdentity(NativeRead(() => Aveva.Core.Database.CurrentElement.Element), designOnly: false);
        return new Dictionary<string, object>
        {
            ["project"] = projectName, ["mdb"] = mdbName, ["current_element"] = ce.Refno, ["current_element_type"] = ce.Type
        };
    }

    private static Dictionary<string, object> PipeManifestBoundaryError(string message)
    {
        return new Dictionary<string, object> { ["stage"] = "context_after", ["message"] = PipeMemberManifestRequest.ErrorText(message) };
    }

    private static bool PipeManifestAttributes(DbElement element, IList<EquipmentManifestAttribute> definitions,
        Dictionary<string, object> row, out bool valuesAvailable)
    {
        var values = new Dictionary<string, string>();
        var typed = new Dictionary<string, object>();
        var errors = new Dictionary<string, string>();
        var typedErrors = new Dictionary<string, string>();
        var unavailable = new List<string>();
        row["values"] = values;
        row["typed_values"] = typed;
        row["errors"] = errors;
        row["typed_read_errors"] = typedErrors;
        row["unavailable_attributes"] = unavailable;
        foreach (var field in definitions)
        {
            string name = field.Name;
            if (field.Error != null) { errors[name] = field.Error; continue; }
            try
            {
                if (!NativeRead(() => element.IsAttributeValid(field.Attribute)))
                {
                    unavailable.Add(name);
                    continue;
                }
                values[name] = EquipmentManifestText(NativeRead(() => element.GetAsString(field.Attribute)));
            }
            catch (Exception error)
            {
                ThrowIfNativeFaulted();
                errors[name] = PipeMemberManifestRequest.ErrorText(error.Message);
                continue;
            }
            if (field.TypedError != null) { typedErrors[name] = field.TypedError; continue; }
            try
            {
                switch (field.ScalarType)
                {
                    case DbAttributeType.DOUBLE:
                        double number = NativeRead(() => element.GetDouble(field.Attribute));
                        if (double.IsNaN(number) || double.IsInfinity(number))
                            throw new InvalidOperationException("The SDK returned a non-finite scalar.");
                        typed[name] = number;
                        break;
                    case DbAttributeType.INTEGER: typed[name] = NativeRead(() => element.GetInteger(field.Attribute)); break;
                    case DbAttributeType.BOOL: typed[name] = NativeRead(() => element.GetBool(field.Attribute)); break;
                    case DbAttributeType.STRING:
                        typed[name] = EquipmentManifestText(NativeRead(() => element.GetString(field.Attribute)));
                        break;
                }
            }
            catch (Exception error)
            {
                ThrowIfNativeFaulted();
                typedErrors[name] = PipeMemberManifestRequest.ErrorText(error.Message);
            }
        }
        bool complete = errors.Count == 0 && typedErrors.Count == 0;
        valuesAvailable = complete && unavailable.Count == 0;
        return complete;
    }
}
