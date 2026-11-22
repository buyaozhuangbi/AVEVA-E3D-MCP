using System;
using System.Collections.Generic;
using System.Globalization;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context : IE3d21EquipmentManifestContext
{
    public object ReadEquipmentManifest(string refno, int maxDepth, int limit, IList<string> attributes, string sessionId)
    {
        EquipmentManifestRequest request = EquipmentManifestRequest.Validate(refno, maxDepth, limit, attributes);
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
            string ceBefore = ModelCurrentReference();
            Project project = NativeRead(() => Project.CurrentProject);
            MDB mdb = NativeRead(() => MDB.CurrentMDB);
            string projectName = EquipmentManifestContextName(project == null ? null : NativeRead(() => project.Name), "project");
            string mdbName = EquipmentManifestContextName(mdb == null ? null : NativeRead(() => mdb.Name), "MDB");

            DbElement root = EquipmentManifestResolve(request.Refno, mdb, out string expectedRootRefno);
            string rootRefno = EquipmentManifestReference(root);
            if (expectedRootRefno != null && rootRefno != expectedRootRefno)
                throw new InvalidOperationException("The resolved equipment reference does not match the requested database reference.");
            string rootName = EquipmentManifestName(root);
            if (request.Refno[0] == '/' && !string.Equals(request.Refno.Substring(1),
                rootName.StartsWith("/", StringComparison.Ordinal) ? rootName.Substring(1) : rootName, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("The resolved equipment name does not match the requested name.");
            string rootType = EquipmentManifestBaseType(root);
            if (rootType != "EQUI") throw new ArgumentException("The manifest root must be an EQUI element.");
            if (string.IsNullOrWhiteSpace(rootName)) throw new InvalidOperationException("The equipment root has no NAME text.");

            var traversal = EquipmentManifestTraversal<DbElement>.Read(root, rootRefno, request.MaxDepth, request.Limit,
                EquipmentManifestMember, EquipmentManifestReference,
                child => EquipmentManifestReference(NativeRead(() => child.Owner)), error => ThrowIfNativeFaulted());
            var readErrors = new List<Dictionary<string, object>>();
            foreach (var error in traversal.ReadErrors)
            {
                var entry = EquipmentManifestIssue("member", error.ParentReference, error.Message);
                entry["sibling_index"] = error.SiblingIndex;
                readErrors.Add(entry);
            }

            List<EquipmentManifestAttribute> requestedAttributes = new List<EquipmentManifestAttribute>();
            foreach (string name in request.Attributes)
                requestedAttributes.Add(new EquipmentManifestAttribute(name));
            var rows = new List<Dictionary<string, object>>(traversal.Rows.Count);
            bool allRowsRead = true;
            foreach (var node in traversal.Rows)
            {
                var row = new Dictionary<string, object>
                {
                    ["id"] = node.Index, ["refno"] = node.Reference, ["parent_index"] = node.ParentIndex,
                    ["depth"] = node.Depth, ["sibling_index"] = node.SiblingIndex
                };
                var identityErrors = new Dictionary<string, string>();
                row["identity_errors"] = identityErrors;
                row["type"] = node.Index == 0 ? rootType : EquipmentManifestReadIdentity(node.Reference, "type",
                    () => EquipmentManifestBaseType(node.Value), identityErrors, readErrors);
                row["name"] = node.Index == 0 ? rootName : EquipmentManifestReadIdentity(node.Reference, "name",
                    () => EquipmentManifestName(node.Value), identityErrors, readErrors);
                bool attributesRead = EquipmentManifestReadAttributes(node.Value, requestedAttributes, row);
                bool rowRead = attributesRead && identityErrors.Count == 0;
                row["success"] = rowRead;
                allRowsRead &= rowRead;
                rows.Add(row);
            }

            string ceAfter = EquipmentManifestObserve("ce_after", ModelCurrentReference, readErrors, rootRefno);
            Project projectAfterObject = null;
            string projectAfter = EquipmentManifestObserve("project_after", () =>
            {
                projectAfterObject = NativeRead(() => Project.CurrentProject);
                return EquipmentManifestContextName(projectAfterObject == null ? null : NativeRead(() => projectAfterObject.Name), "project");
            }, readErrors, rootRefno);
            MDB mdbAfterObject = null;
            string mdbAfter = EquipmentManifestObserve("mdb_after", () =>
            {
                mdbAfterObject = NativeRead(() => MDB.CurrentMDB);
                return EquipmentManifestContextName(mdbAfterObject == null ? null : NativeRead(() => mdbAfterObject.Name), "MDB");
            }, readErrors, rootRefno);
            bool? ceUnchanged = ceAfter == null ? (bool?)null : ceBefore == ceAfter;
            bool? identityUnchanged = projectAfter == null || mdbAfter == null ? (bool?)null :
                ReferenceEquals(projectAfterObject, project) && ReferenceEquals(mdbAfterObject, mdb) &&
                projectName == projectAfter && mdbName == mdbAfter;
            if (ceUnchanged == false)
                readErrors.Add(EquipmentManifestIssue("ce_unchanged", rootRefno, "The current element changed during the manifest read."));
            if (identityUnchanged == false)
                readErrors.Add(EquipmentManifestIssue("identity_unchanged", rootRefno, "The project or MDB changed during the manifest read."));

            bool truncated = traversal.TruncationReasons.Count > 0;
            bool complete = !truncated && allRowsRead && readErrors.Count == 0 && ceUnchanged == true && identityUnchanged == true;
            return new Dictionary<string, object>
            {
                ["success"] = complete, ["complete"] = complete,
                ["requested_refno"] = request.Refno, ["root_refno"] = rootRefno,
                ["rows"] = rows, ["returned_count"] = rows.Count, ["order"] = "preorder",
                ["max_depth"] = request.MaxDepth, ["limit"] = request.Limit, ["attributes"] = request.Attributes,
                ["truncated"] = truncated, ["truncation_reasons"] = traversal.TruncationReasons, ["read_errors"] = readErrors,
                ["ce_before"] = ceBefore, ["ce_after"] = ceAfter, ["ce_unchanged"] = ceUnchanged,
                ["project"] = projectName, ["project_after"] = projectAfter, ["mdb"] = mdbName,
                ["mdb_after"] = mdbAfter, ["identity_unchanged"] = identityUnchanged, ["session_id"] = sessionId,
                ["single_dispatch"] = true, ["atomic_snapshot"] = false
            };
        });
    }

    private static DbElement EquipmentManifestResolve(string target, MDB mdb, out string expectedRefno)
    {
        expectedRefno = null;
        if (target[0] != '=') return NativeRead(() => mdb.FindElement(target));
        string[] parts = target.Substring(1).Split('/');
        var pair = new[] { int.Parse(parts[0], CultureInfo.InvariantCulture), int.Parse(parts[1], CultureInfo.InvariantCulture) };
        expectedRefno = ModelReference(pair);
        return NativeRead(() => DbElement.GetElement(pair));
    }

    private static DbElement EquipmentManifestMember(DbElement parent, int ordinal)
    {
        DbElement child = NativeRead(() => parent.Member(ordinal));
        return child == null || NativeRead(() => child.IsNull) ? null : child;
    }

    private static string EquipmentManifestReference(DbElement element)
    {
        if (element == null || NativeRead(() => element.IsNull) ||
            !NativeRead(() => element.IsValid) || NativeRead(() => element.IsDeleted))
            throw new InvalidOperationException("The explicit root or hierarchy member is unavailable or deleted.");
        if (NativeRead(() => element.DbType) != DbType.Design)
            throw new InvalidOperationException("The manifest root and hierarchy members must belong to a Design database.");
        return ModelReference(NativeRead(() => element.Ref));
    }

    private static string EquipmentManifestBaseType(DbElement element)
    {
        // ElementType gives the native base type for a user-defined type.
        DbElementType type = NativeRead(() => element.ElementType);
        string shortName = type == null || !NativeRead(() => type.IsValid) ? null : NativeRead(() => type.ShortName);
        if (string.IsNullOrWhiteSpace(shortName) || shortName.Length > 64)
            throw new InvalidOperationException("The SDK returned no valid element type.");
        return shortName.ToUpperInvariant();
    }

    private static string EquipmentManifestName(DbElement element)
    {
        return EquipmentManifestText(NativeRead(() => element.GetAsString(DbAttributeInstance.NAME)));
    }

    private static string EquipmentManifestContextName(string value, string field)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Length > 256)
            throw new InvalidOperationException("No valid " + field + " name was available in E3D.");
        return value;
    }

    private static string EquipmentManifestObserve(string stage, Func<string> read,
        List<Dictionary<string, object>> errors, string refno)
    {
        try { return read(); }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            errors.Add(EquipmentManifestIssue(stage, refno, error.Message));
            return null;
        }
    }

    private static string EquipmentManifestReadIdentity(string refno, string field, Func<string> read,
        Dictionary<string, string> identityErrors, List<Dictionary<string, object>> readErrors)
    {
        try { return read(); }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            var issue = EquipmentManifestIssue("identity", refno, error.Message);
            issue["field"] = field;
            readErrors.Add(issue);
            identityErrors[field] = (string)issue["error"];
            return null;
        }
    }

    private sealed class EquipmentManifestAttribute
    {
        internal readonly string Name;
        internal readonly DbAttribute Attribute;
        internal readonly string Error;
        internal readonly DbAttributeType? ScalarType;
        internal readonly string TypedError;

        internal EquipmentManifestAttribute(string name)
        {
            Name = name;
            try
            {
                Attribute = NativeRead(() => DbAttribute.GetDbAttribute(name));
                if (Attribute == null || !NativeRead(() => Attribute.IsValid))
                    throw new InvalidOperationException("Unknown SDK attribute: " + name);
            }
            catch (Exception error)
            {
                ThrowIfNativeFaulted();
                Error = EquipmentManifestRequest.ErrorText(error.Message);
                return;
            }

            try
            {
                DbAttributeType type = NativeRead(() => Attribute.Type);
                if (type == DbAttributeType.STRING || (!NativeRead(() => Attribute.IsArray) &&
                    (type == DbAttributeType.DOUBLE || type == DbAttributeType.INTEGER || type == DbAttributeType.BOOL)))
                    ScalarType = type;
            }
            catch (Exception error)
            {
                ThrowIfNativeFaulted();
                TypedError = EquipmentManifestRequest.ErrorText(error.Message);
            }
        }
    }

    private static bool EquipmentManifestReadAttributes(DbElement element, IList<EquipmentManifestAttribute> attributes,
        Dictionary<string, object> row)
    {
        var values = new Dictionary<string, string>();
        var typedValues = new Dictionary<string, object>();
        var errors = new Dictionary<string, string>();
        var typedErrors = new Dictionary<string, string>();
        row["values"] = values;
        row["typed_values"] = typedValues;
        row["errors"] = errors;
        row["typed_read_errors"] = typedErrors;
        foreach (var attribute in attributes)
        {
            string name = attribute.Name;
            if (attribute.Error != null)
            {
                errors[name] = attribute.Error;
                continue;
            }
            try
            {
                if (!NativeRead(() => element.IsAttributeValid(attribute.Attribute)))
                    throw new InvalidOperationException("The attribute is unavailable on this element.");
            }
            catch (Exception error)
            {
                ThrowIfNativeFaulted();
                errors[name] = EquipmentManifestRequest.ErrorText(error.Message);
                continue;
            }
            try { values[name] = EquipmentManifestText(NativeRead(() => element.GetAsString(attribute.Attribute))); }
            catch (Exception error)
            {
                ThrowIfNativeFaulted();
                errors[name] = EquipmentManifestRequest.ErrorText(error.Message);
            }
            if (attribute.TypedError != null)
            {
                typedErrors[name] = attribute.TypedError;
                continue;
            }
            try
            {
                switch (attribute.ScalarType)
                {
                    case DbAttributeType.DOUBLE:
                        double number = NativeRead(() => element.GetDouble(attribute.Attribute));
                        if (double.IsNaN(number) || double.IsInfinity(number))
                            throw new InvalidOperationException("The SDK returned a non-finite numeric value.");
                        typedValues[name] = number;
                        break;
                    case DbAttributeType.INTEGER:
                        typedValues[name] = NativeRead(() => element.GetInteger(attribute.Attribute));
                        break;
                    case DbAttributeType.STRING:
                        typedValues[name] = EquipmentManifestText(NativeRead(() => element.GetString(attribute.Attribute)));
                        break;
                    case DbAttributeType.BOOL:
                        typedValues[name] = NativeRead(() => element.GetBool(attribute.Attribute));
                        break;
                }
            }
            catch (Exception error)
            {
                ThrowIfNativeFaulted();
                typedErrors[name] = EquipmentManifestRequest.ErrorText(error.Message);
            }
        }
        return errors.Count == 0 && typedErrors.Count == 0;
    }

    private static string EquipmentManifestText(string value)
    {
        if (value == null) throw new InvalidOperationException("The SDK returned no text value.");
        if (value.Length > EquipmentManifestRequest.MaximumTextLength)
            throw new InvalidOperationException("The SDK returned text beyond the manifest limit of 4096 characters.");
        return value;
    }

    private static Dictionary<string, object> EquipmentManifestIssue(string stage, string refno, string error)
    {
        return new Dictionary<string, object>
        {
            ["stage"] = stage, ["refno"] = refno, ["error"] = EquipmentManifestRequest.ErrorText(error)
        };
    }
}
