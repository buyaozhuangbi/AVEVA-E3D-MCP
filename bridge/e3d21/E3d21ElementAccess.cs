using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private sealed class ElementAccessReadState
    {
        internal readonly List<object> Errors = new List<object>();
        internal Dictionary<string, object> Row;
        internal Dictionary<string, object> Field;
        internal string Path = "batch";
    }

    public object GetElementAccess(IList<string> refnos)
    {
        if (refnos == null || refnos.Count < 1 || refnos.Count > 32)
            throw new ArgumentException("refnos must contain between 1 and 32 explicit references.");
        List<string> requested = refnos.Select(value => ModelEditInput.ExplicitReference(value, "refnos")).ToList();
        if (requested.Distinct(StringComparer.OrdinalIgnoreCase).Count() != requested.Count)
            throw new ArgumentException("refnos must not contain duplicate references.");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

        var state = new ElementAccessReadState();
        var rows = requested.Select(ElementAccessRow).ToList();
        var report = new Dictionary<string, object>
        {
            ["success"] = false, ["scan_complete"] = false, ["all_values_available"] = false,
            ["read_only"] = true, ["single_dispatch"] = true,
            ["consistency"] = "single_ui_dispatch",
            ["consistency_scope"] = "Current-session observations in one UI callback; no database lock or immutable snapshot.",
            ["permission_scope"] = "Generic SDK update permission and observed flags only; not a guarantee that any specific attribute or future operation can be written.",
            ["claims_freshness"] = "current_session_unverified", ["claims_refresh_attempted"] = false,
            ["claim_scope"] = "Requested elements only; PRMOWN is returned as a reference without reading its owner's claims.",
            ["requested_refnos"] = requested, ["row_count"] = rows.Count, ["rows"] = rows, ["read_errors"] = state.Errors,
            ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
            ["project_before"] = null, ["project_after"] = null, ["mdb_before"] = null, ["mdb_after"] = null,
            ["identity_unchanged"] = null, ["native_database_fault"] = null
        };
        try
        {
            OnUi(delegate
            {
                if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
                ElementAccessReadTop(report, "ce_before", state, () => ModelCurrentReference());
                Project projectBefore = null, projectAfter = null;
                MDB mdbBefore = null, mdbAfter = null;
                ElementAccessReadTop(report, "project_before", state, () =>
                {
                    projectBefore = NativeRead(() => Project.CurrentProject);
                    if (projectBefore == null) throw new InvalidOperationException("No project is open.");
                    return ElementAccessContextName(NativeRead(() => projectBefore.Name));
                });
                ElementAccessReadTop(report, "mdb_before", state, () =>
                {
                    mdbBefore = NativeRead(() => MDB.CurrentMDB);
                    if (mdbBefore == null) throw new InvalidOperationException("No MDB is open.");
                    return ElementAccessContextName(NativeRead(() => mdbBefore.Name));
                });
                var resolved = new Dictionary<string, int>(StringComparer.Ordinal);
                for (int index = 0; index < rows.Count; index++)
                {
                    state.Row = rows[index]; state.Field = null; state.Path = "rows[" + index + "]";
                    Dictionary<string, object> row = state.Row;
                    string rowPath = state.Path;
                    row["status"] = "reading";
                    DbElement element;
                    try
                    {
                        element = ElementAccessResolve((string)row["requested_refno"]);
                        if (element == null || NativeRead(() => element.IsNull) || !NativeRead(() => element.IsValid))
                        {
                            ElementAccessUnresolved(row, state, "not_found", "The explicit reference did not resolve to a valid element.");
                            continue;
                        }
                        int[] pair = NativeRead(() => element.Ref);
                        if (!ModelEditInput.IsReferencePair(pair))
                            throw new InvalidOperationException("The resolved element has no valid numeric reference.");
                        string reference = ModelReference(pair);
                        row["refno"] = reference; row["resolved"] = true;
                        foreach (var field in ElementAccessFields(row)) field["source_refno"] = reference;
                        int firstIndex;
                        if (resolved.TryGetValue(reference, out firstIndex)) row["duplicate_of_index"] = firstIndex;
                        else resolved.Add(reference, index);
                    }
                    catch (Exception error)
                    {
                        ThrowIfNativeFaulted();
                        ElementAccessUnresolved(row, state, "read_error", ElementAccessMessage(error.Message));
                        continue;
                    }
                    var fields = (Dictionary<string, object>)row["fields"];
                    var database = (Dictionary<string, object>)row["database"];
                    var claim = (Dictionary<string, object>)row["target_claim"];
                    ElementAccessRead(fields, "type", rowPath + ".fields", state, field =>
                    {
                        DbElementType type = NativeRead(() => element.GetElementType());
                        if (type == null) { ElementAccessUnavailable(field, "missing_type"); return; }
                        ElementAccessText(field, NativeRead(() => type.Name));
                    });
                    ElementAccessReadAttribute(element, fields, "name", "NAME", DbAttributeType.STRING, rowPath + ".fields", state);
                    ElementAccessRead(fields, "update_allowed", rowPath + ".fields", state,
                        field => ElementAccessValue(field, NativeRead(() => element.IsAttributeSetable(null))));
                    var update = (Dictionary<string, object>)fields["update_allowed"];
                    var readOnly = (Dictionary<string, object>)fields["read_only"];
                    if (Equals(update["status"], "ok")) ElementAccessValue(readOnly, !(bool)update["value"]);
                    else ElementAccessUnavailable(readOnly, "update_permission_unavailable");
                    foreach (var pair in new[] { new[] { "locked", "LOCK" }, new[] { "database_writable", "DBWRIT" },
                        new[] { "claim_allowed", "OKCLA" }, new[] { "dac_claim_allowed", "DACCLA" } })
                        ElementAccessReadAttribute(element, fields, pair[0], pair[1], DbAttributeType.BOOL, rowPath + ".fields", state);
                    ElementAccessReadAttribute(element, fields, "primary_owner_refno", "PRMOWN", DbAttributeType.ELEMENT, rowPath + ".fields", state);

                    state.Field = null; state.Path = rowPath + ".database";
                    Db db = null;
                    try
                    {
                        db = NativeRead(() => element.Db);
                        if (db == null)
                            foreach (Dictionary<string, object> field in database.Values) ElementAccessUnavailable(field, "missing_database");
                    }
                    catch (Exception error)
                    {
                        ThrowIfNativeFaulted();
                        foreach (Dictionary<string, object> field in database.Values)
                        { field["status"] = "read_error"; field["reason"] = "database_read_failed"; }
                        ElementAccessError(state, "database_read_failed", error.Message);
                    }
                    if (db != null)
                    {
                        ElementAccessRead(database, "number", rowPath + ".database", state, field => ElementAccessValue(field, NativeRead(() => db.Number)));
                        ElementAccessRead(database, "extract_number", rowPath + ".database", state, field => ElementAccessValue(field, NativeRead(() => db.ExtractNumber)));
                        ElementAccessRead(database, "name", rowPath + ".database", state, field => ElementAccessText(field, NativeRead(() => db.Name)));
                        ElementAccessRead(database, "type", rowPath + ".database", state, field => ElementAccessValue(field, NativeRead(() => db.Type).ToString()));
                        ElementAccessRead(database, "is_read_only", rowPath + ".database", state, field => ElementAccessValue(field, NativeRead(() => db.IsReadOnly)));
                        ElementAccessRead(database, "is_primary", rowPath + ".database", state, field => ElementAccessValue(field, NativeRead(() => db.IsPrimary)));
                        ElementAccessRead(database, "is_extract", rowPath + ".database", state, field => ElementAccessValue(field, NativeRead(() => db.IsExtract)));
                    }
                    foreach (var pair in new[] { new[] { "claimed", "LCLM" }, new[] { "hierarchy_claimed", "LCLMH" } })
                        ElementAccessReadAttribute(element, claim, pair[0], pair[1], DbAttributeType.BOOL, rowPath + ".target_claim", state);
                    ElementAccessReadAttribute(element, claim, "claimant_user", "USERC", DbAttributeType.STRING, rowPath + ".target_claim", state);
                    ElementAccessReadAttribute(element, claim, "claimant_number", "CLMNUM", DbAttributeType.INTEGER, rowPath + ".target_claim", state);
                    ElementAccessReadAttribute(element, claim, "claimant_extract", "EXTRC", DbAttributeType.STRING, rowPath + ".target_claim", state);
                    row["scan_complete"] = true;
                    row["all_values_available"] = ElementAccessFields(row).All(field => Equals(field["status"], "ok"));
                    row["status"] = ((List<object>)row["errors"]).Count == 0 ? "returned" : "partial";
                    state.Field = null;
                }
                state.Row = null; state.Field = null;
                ElementAccessReadTop(report, "ce_after", state, () => ModelCurrentReference());
                ModelCeUnchanged(report);
                state.Path = "ce_unchanged";
                if (!Equals(report["ce_unchanged"], true)) ElementAccessError(state, "consistency_unverified", "Current element could not be verified unchanged.");
                ElementAccessReadTop(report, "project_after", state, () =>
                {
                    projectAfter = NativeRead(() => Project.CurrentProject);
                    if (projectAfter == null) throw new InvalidOperationException("No project is open.");
                    return ElementAccessContextName(NativeRead(() => projectAfter.Name));
                });
                ElementAccessReadTop(report, "mdb_after", state, () =>
                {
                    mdbAfter = NativeRead(() => MDB.CurrentMDB);
                    if (mdbAfter == null) throw new InvalidOperationException("No MDB is open.");
                    return ElementAccessContextName(NativeRead(() => mdbAfter.Name));
                });
                report["identity_unchanged"] = projectBefore != null && mdbBefore != null &&
                    ReferenceEquals(projectBefore, projectAfter) && ReferenceEquals(mdbBefore, mdbAfter) &&
                    report["project_before"] != null && report["mdb_before"] != null &&
                    Equals(report["project_before"], report["project_after"]) && Equals(report["mdb_before"], report["mdb_after"]);
                state.Path = "identity_unchanged";
                if (!Equals(report["identity_unchanged"], true)) ElementAccessError(state, "consistency_unverified", "Project/MDB identity could not be verified unchanged.");
                report["scan_complete"] = rows.All(row => Equals(row["scan_complete"], true));
                report["all_values_available"] = rows.All(row => Equals(row["all_values_available"], true));
                report["success"] = Equals(report["scan_complete"], true) && state.Errors.Count == 0 && rows.All(row => Equals(row["resolved"], true));
                return report;
            });
        }
        catch (NativeDatabaseFaultException error)
        {
            // Managed-only after a fault, including an OnUi post-callback fault.
            ElementAccessError(state, "native_database_fault", error.Message);
            if (state.Field != null) { state.Field["value"] = null; state.Field["status"] = "read_error"; state.Field["reason"] = "native_database_fault"; }
            if (state.Row != null) { state.Row["status"] = "partial"; state.Row["scan_complete"] = false; state.Row["all_values_available"] = false; }
            report["success"] = false; report["scan_complete"] = false; report["all_values_available"] = false;
            foreach (string key in new[] { "ce_after", "ce_unchanged", "project_after", "mdb_after", "identity_unchanged" }) report[key] = null;
            report["native_database_fault"] = nativeFaultState == null ? null : nativeFaultState.Snapshot();
        }
        return report;
    }

    private static DbElement ElementAccessResolve(string reference)
    {
        // ExplicitReference has already validated every input before dispatch.
        // Direct lookups preserve SDK failures for the row's read_error handler;
        // this read must never fall back to CE or a hierarchy traversal.
        if (reference.StartsWith("=", StringComparison.Ordinal))
        {
            string[] parts = reference.Substring(1).Split('/');
            int[] pair = {
                int.Parse(parts[0], NumberStyles.None, CultureInfo.InvariantCulture),
                int.Parse(parts[1], NumberStyles.None, CultureInfo.InvariantCulture)
            };
            return NativeRead(() => DbElement.GetElement(pair));
        }
        MDB mdb = NativeRead(() => MDB.CurrentMDB);
        if (mdb == null) throw new InvalidOperationException("No MDB is open.");
        return NativeRead(() => mdb.FindElement(reference));
    }

    private static Dictionary<string, object> ElementAccessRow(string requested)
    {
        var fields = new Dictionary<string, object>();
        foreach (var pair in new[] { new[] { "type", "DbElement.GetElementType().Name" }, new[] { "name", "NAME" },
            new[] { "update_allowed", "DbElement.IsAttributeSetable(null)" }, new[] { "read_only", "not update_allowed" },
            new[] { "locked", "LOCK" }, new[] { "database_writable", "DBWRIT" }, new[] { "claim_allowed", "OKCLA" },
            new[] { "dac_claim_allowed", "DACCLA" }, new[] { "primary_owner_refno", "PRMOWN" } }) fields[pair[0]] = ElementAccessField(pair[1]);
        var database = new Dictionary<string, object>();
        foreach (var pair in new[] { new[] { "number", "Number" }, new[] { "extract_number", "ExtractNumber" }, new[] { "name", "Name" },
            new[] { "type", "Type" }, new[] { "is_read_only", "IsReadOnly" }, new[] { "is_primary", "IsPrimary" }, new[] { "is_extract", "IsExtract" } })
            database[pair[0]] = ElementAccessField("DbElement.Db." + pair[1]);
        var claim = new Dictionary<string, object>();
        foreach (var pair in new[] { new[] { "claimed", "LCLM" }, new[] { "hierarchy_claimed", "LCLMH" }, new[] { "claimant_user", "USERC" },
            new[] { "claimant_number", "CLMNUM" }, new[] { "claimant_extract", "EXTRC" } }) claim[pair[0]] = ElementAccessField(pair[1]);
        return new Dictionary<string, object>
        {
            ["requested_refno"] = requested, ["refno"] = null, ["resolved"] = false, ["duplicate_of_index"] = null,
            ["status"] = "not_attempted", ["scan_complete"] = false, ["all_values_available"] = false,
            ["fields"] = fields, ["database"] = database, ["target_claim"] = claim, ["errors"] = new List<object>()
        };
    }

    private static Dictionary<string, object> ElementAccessField(string source)
    { return new Dictionary<string, object> { ["value"] = null, ["status"] = "not_attempted", ["source"] = source, ["source_refno"] = null }; }

    private static IEnumerable<Dictionary<string, object>> ElementAccessFields(Dictionary<string, object> row)
    { return new[] { "fields", "database", "target_claim" }.SelectMany(key => ((Dictionary<string, object>)row[key]).Values.Cast<Dictionary<string, object>>()); }

    private static void ElementAccessValue(Dictionary<string, object> field, object value)
    { field["value"] = value; field["status"] = "ok"; }

    private static void ElementAccessUnavailable(Dictionary<string, object> field, string reason)
    { field["value"] = null; field["status"] = "not_available"; field["reason"] = reason; }

    private static void ElementAccessText(Dictionary<string, object> field, string value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Equals("unset", StringComparison.OrdinalIgnoreCase))
        { field["value"] = null; field["status"] = "unset"; field["reason"] = "empty_or_unset"; }
        else ElementAccessValue(field, value);
    }

    private static void ElementAccessRead(Dictionary<string, object> fields, string key, string path, ElementAccessReadState state,
        Action<Dictionary<string, object>> read)
    {
        state.Path = path + "." + key;
        state.Field = (Dictionary<string, object>)fields[key];
        try { ThrowIfNativeFaulted(); read(state.Field); ThrowIfNativeFaulted(); }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            state.Field["value"] = null; state.Field["status"] = "read_error";
            ElementAccessError(state, "read_failed", error.Message);
        }
        state.Field = null;
    }

    private static void ElementAccessReadAttribute(DbElement element, Dictionary<string, object> fields, string key, string attributeName,
        DbAttributeType expectedType, string path, ElementAccessReadState state)
    {
        ElementAccessRead(fields, key, path, state, field =>
        {
            DbAttribute attribute = NativeRead(() => DbAttribute.GetDbAttribute(attributeName));
            if (attribute == null || !NativeRead(() => attribute.IsValid)) { ElementAccessUnavailable(field, "attribute_unavailable"); return; }
            DbAttributeType actualType = NativeRead(() => attribute.Type);
            field["sdk_type"] = actualType.ToString();
            // In E3D 2.1 IsArray is implemented as native attribute size > 1,
            // which also describes the capacity of a STRING. STRINGARRAY is a
            // distinct type; a scalar STRING uses GetValidString regardless of
            // its character capacity, matching the existing attribute reader.
            if (actualType != expectedType || (expectedType != DbAttributeType.STRING && NativeRead(() => attribute.IsArray)))
            { ElementAccessUnavailable(field, "unsupported_attribute_type"); return; }
            if (!NativeRead(() => element.IsAttributeValid(attribute))) { ElementAccessUnavailable(field, "attribute_value_invalid"); return; }
            bool available;
            object value;
            if (expectedType == DbAttributeType.BOOL)
            {
                bool observed = false;
                available = NativeRead(() => element.GetValidBool(attribute, ref observed)); value = observed;
            }
            else if (expectedType == DbAttributeType.INTEGER)
            {
                int observed = 0;
                available = NativeRead(() => element.GetValidInteger(attribute, ref observed)); value = observed;
            }
            else if (expectedType == DbAttributeType.STRING)
            {
                string observed = null;
                available = NativeRead(() => element.GetValidString(attribute, ref observed)); value = observed;
            }
            else
            {
                DbElement observed = null;
                available = NativeRead(() => element.GetValidRef(attribute, ref observed));
                if (!available) { ElementAccessUnavailable(field, "attribute_read_unavailable"); return; }
                if (observed == null || NativeRead(() => observed.IsNull))
                { field["status"] = "unset"; field["reason"] = "null_reference"; return; }
                if (!NativeRead(() => observed.IsValid))
                { field["status"] = "unresolved_reference"; field["reason"] = "invalid_reference"; return; }
                int[] reference = NativeRead(() => observed.Ref);
                if (!ModelEditInput.IsReferencePair(reference))
                { field["status"] = "unresolved_reference"; field["reason"] = "invalid_reference_pair"; return; }
                value = ModelReference(reference);
            }
            if (!available) { ElementAccessUnavailable(field, "attribute_read_unavailable"); return; }
            if (expectedType == DbAttributeType.STRING) ElementAccessText(field, (string)value);
            else ElementAccessValue(field, value);
        });
    }

    private static void ElementAccessUnresolved(Dictionary<string, object> row, ElementAccessReadState state, string status, string message)
    {
        row["status"] = status; row["scan_complete"] = true;
        foreach (var field in ElementAccessFields(row)) ElementAccessUnavailable(field, "element_not_resolved");
        ElementAccessError(state, "element_" + status, message);
    }

    private static void ElementAccessReadTop(Dictionary<string, object> report, string key, ElementAccessReadState state, Func<object> read)
    {
        state.Path = key; state.Field = null;
        try { report[key] = NativeRead(read); }
        catch (Exception error) { ThrowIfNativeFaulted(); ElementAccessError(state, "read_failed", error.Message); }
    }

    private static string ElementAccessContextName(string value)
    { if (string.IsNullOrWhiteSpace(value)) throw new InvalidOperationException("Project/MDB name is unavailable."); return value; }

    private static string ElementAccessMessage(string message)
    { return string.IsNullOrEmpty(message) ? "SDK read failed." : message.Length <= 512 ? message : message.Substring(0, 512); }

    private static void ElementAccessError(ElementAccessReadState state, string code, string message)
    {
        var error = new Dictionary<string, object> { ["path"] = state.Path, ["code"] = code, ["message"] = ElementAccessMessage(message) };
        state.Errors.Add(error);
        if (state.Row != null) ((List<object>)state.Row["errors"]).Add(error);
    }
}
