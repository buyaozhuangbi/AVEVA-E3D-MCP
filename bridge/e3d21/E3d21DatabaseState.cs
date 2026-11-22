using System;
using System.Collections.Generic;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    public object GetDatabaseIdentitySnapshot(string expectedContext, string sessionId)
    {
        if (string.IsNullOrWhiteSpace(sessionId)) throw new ArgumentException("Bridge session identity is unavailable.");
        if (string.IsNullOrWhiteSpace(expectedContext))
            throw new ArgumentException("expected_context is required for a database identity snapshot.");
        DatabaseStateRequest.Validate(0, 1, expectedContext);
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
            var errors = new List<object>();
            var databases = new List<object>();
            var result = new Dictionary<string, object>
            {
                ["success"] = false, ["available"] = false, ["complete"] = false,
                ["source"] = "E3D21 SDK MDB.GetDBArray identity scan",
                ["consistency"] = "single_ui_dispatch_not_a_database_lock", ["atomic_snapshot"] = false,
                ["project"] = null, ["mdb"] = null, ["session_id"] = sessionId,
                ["context_id"] = null, ["expected_context"] = expectedContext,
                ["total_count"] = null, ["returned_count"] = 0, ["identity_snapshot_sha256"] = null,
                ["databases"] = databases, ["ce_before"] = null, ["ce_after"] = null,
                ["ce_unchanged"] = null, ["identity_unchanged"] = null, ["read_errors"] = errors,
                ["scope"] = "Current MDB ordered database identity fields only: index, name, database number and extract number. No project-wide catalog, claims, sessions or write permission are inspected."
            };
            MDB mdb = null;
            Project project = null;
            DatabaseStateRead(result, "ce_before", "ce_before", errors, () => ModelCurrentReference());
            if (!(result["ce_before"] is string) || errors.Count > 0)
            {
                result["ce_after"] = null;
                result["ce_unchanged"] = null;
                result["success"] = false;
                result["complete"] = false;
                return result;
            }
            try
            {
                mdb = NativeRead(() => MDB.CurrentMDB);
                project = NativeRead(() => Project.CurrentProject);
                if (mdb == null || project == null)
                    DatabaseStateError(errors, "database_identity_snapshot", "no_project_mdb", "No current project/MDB is open.");
                else
                {
                    result["available"] = true;
                    DatabaseStateRead(result, "project", "project", errors, () => project.Name);
                    DatabaseStateRead(result, "mdb", "mdb", errors, () => mdb.Name);
                    string projectName = result["project"] as string;
                    string mdbName = result["mdb"] as string;
                    if (string.IsNullOrWhiteSpace(projectName) || string.IsNullOrWhiteSpace(mdbName))
                        DatabaseStateError(errors, "context_id", "identity_unavailable", "Project/MDB names must be readable and nonempty.");
                    else
                    {
                        string context = DatabaseStateContext(sessionId, projectName, mdbName);
                        result["context_id"] = context;
                        if (!StringComparer.Ordinal.Equals(context, expectedContext))
                            DatabaseStateError(errors, "expected_context", "context_mismatch", "The E3D session, project or MDB changed before the identity scan.");
                        else
                            DatabaseStateReadIdentities(mdb, result, databases, errors);
                    }
                }
            }
            catch (Exception ex)
            {
                ThrowIfNativeFaulted();
                DatabaseStateError(errors, "database_identity_snapshot", "read_failed", ex.Message);
            }
            DatabaseStateRead(result, "ce_after", "ce_after", errors, () => ModelCurrentReference());
            ModelCeUnchanged(result);
            if (!Equals(result["ce_unchanged"], true))
                DatabaseStateError(errors, "ce_unchanged", "consistency_unverified", "Current element could not be verified unchanged.");
            if (mdb != null && project != null)
            {
                DatabaseStateRead(result, "identity_unchanged", "identity_unchanged", errors, () =>
                    ReferenceEquals(MDB.CurrentMDB, mdb) && ReferenceEquals(Project.CurrentProject, project) &&
                    result["project"] != null && result["mdb"] != null &&
                    StringComparer.Ordinal.Equals(NativeRead(() => project.Name), (string)result["project"]) &&
                    StringComparer.Ordinal.Equals(NativeRead(() => mdb.Name), (string)result["mdb"]));
                if (!Equals(result["identity_unchanged"], true))
                    DatabaseStateError(errors, "identity_unchanged", "consistency_unverified", "Project/MDB identity could not be verified unchanged.");
            }
            bool successful = Equals(result["available"], true) && errors.Count == 0 &&
                Equals(result["ce_unchanged"], true) && Equals(result["identity_unchanged"], true) &&
                result["identity_snapshot_sha256"] is string;
            result["success"] = successful;
            result["complete"] = successful;
            return result;
        });
    }

    private static void DatabaseStateReadIdentities(MDB mdb, Dictionary<string, object> result,
        List<object> output, List<object> errors)
    {
        try
        {
            Db[] values = NativeRead(() => mdb.GetDBArray());
            if (values == null) throw new InvalidOperationException("SDK returned no database array.");
            result["total_count"] = values.Length;
            if (values.Length > 10000)
            {
                DatabaseStateError(errors, "databases", "database_count_limit", "The SDK database array exceeds the 10000-entry identity scan limit; no identities were read.");
                return;
            }
            var canonical = new StringBuilder();
            for (int index = 0; index < values.Length; index++)
            {
                ThrowIfNativeFaulted();
                string path = "databases[" + index.ToString(CultureInfo.InvariantCulture) + "]";
                Db database = values[index];
                var item = new Dictionary<string, object> { ["index"] = index, ["available"] = database != null };
                output.Add(item);
                if (database == null)
                {
                    DatabaseStateError(errors, path, "null_database", "SDK returned a null database entry.");
                    continue;
                }
                DatabaseStateRead(item, "name", path + ".name", errors, () => database.Name);
                DatabaseStateRead(item, "number", path + ".number", errors, () => database.Number);
                DatabaseStateRead(item, "extract_number", path + ".extract_number", errors, () => database.ExtractNumber);
                string name = item["name"] as string;
                object number = item["number"];
                object extract = item["extract_number"];
                if (String.IsNullOrWhiteSpace(name) || !(number is int) || (int)number < 1 ||
                    !(extract is int) || (int)extract < 0)
                {
                    DatabaseStateError(errors, path, "identity_incomplete", "Database name, positive number and nonnegative extract number are required.");
                    continue;
                }
                canonical.Append(index.ToString(CultureInfo.InvariantCulture)).Append('|')
                    .Append(Convert.ToBase64String(Encoding.UTF8.GetBytes(name))).Append('|')
                    .Append(((int)number).ToString(CultureInfo.InvariantCulture)).Append('|')
                    .Append(((int)extract).ToString(CultureInfo.InvariantCulture)).Append('\n');
            }
            result["returned_count"] = output.Count;
            if (errors.Count == 0)
            {
                using (SHA256 sha = SHA256.Create())
                {
                    byte[] bytes = sha.ComputeHash(Encoding.UTF8.GetBytes(canonical.ToString()));
                    var digest = new StringBuilder(64);
                    foreach (byte value in bytes) digest.Append(value.ToString("x2", CultureInfo.InvariantCulture));
                    result["identity_snapshot_sha256"] = digest.ToString();
                }
            }
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            DatabaseStateError(errors, "databases", "read_failed", ex.Message);
        }
    }

    public object GetDatabaseState(int offset, int limit, string expectedContext, string sessionId)
    {
        DatabaseStateRequest.Validate(offset, limit, expectedContext);
        if (string.IsNullOrWhiteSpace(sessionId)) throw new ArgumentException("Bridge session identity is unavailable.");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
            var errors = new List<object>();
            var databases = new List<object>();
            var result = new Dictionary<string, object>
            {
                ["success"] = false, ["available"] = false, ["complete"] = false, ["page_complete"] = false,
                ["source"] = "E3D21 SDK MDB/Db", ["consistency"] = "live",
                ["project"] = null, ["mdb"] = null, ["default_database_type"] = null,
                ["session_id"] = sessionId, ["context_id"] = null,
                ["default_write_mode"] = null, ["databases"] = databases,
                ["offset"] = offset, ["limit"] = limit, ["total_count"] = null,
                ["returned_count"] = 0, ["has_more"] = null, ["next_offset"] = null,
                ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
                ["identity_unchanged"] = null, ["read_errors"] = errors,
                ["permission_scope"] = "Native database flags; no guarantee that a specific element or attribute edit is permitted.",
                ["enumeration_scope"] = "Limits bound returned details. SDK GetDBArray materializes the full array; at most 10000 database entries are accepted. Database pages use current MDB order, not a stable cursor."
            };
            MDB mdb = null;
            Project project = null;
            DatabaseStateRead(result, "ce_before", "ce_before", errors, () => ModelCurrentReference());
            try
            {
                mdb = NativeRead(() => MDB.CurrentMDB);
                project = NativeRead(() => Project.CurrentProject);
                if (mdb == null || project == null)
                {
                    DatabaseStateError(errors, "database_state", "no_project_mdb", "No current project/MDB is open.");
                }
                else
                {
                    result["available"] = true;
                    DatabaseStateRead(result, "project", "project", errors, () => project.Name);
                    DatabaseStateRead(result, "mdb", "mdb", errors, () => mdb.Name);
                    if (string.IsNullOrWhiteSpace(result["project"] as string) || string.IsNullOrWhiteSpace(result["mdb"] as string))
                        DatabaseStateError(errors, "context_id", "identity_unavailable", "Project/MDB names must be readable and nonempty.");
                    else
                    {
                        string context = DatabaseStateContext(sessionId, (string)result["project"], (string)result["mdb"]);
                        result["context_id"] = context;
                        if (expectedContext.Length > 0 && expectedContext != context)
                            DatabaseStateError(errors, "expected_context", "context_mismatch", "The E3D session, project or MDB changed. Restart database pagination at offset 0.");
                        else
                        {
                            DatabaseStateRead(result, "default_database_type", "default_database_type", errors, () => mdb.DefaultType().ToString());
                            DatabaseStateRead(result, "default_write_mode", "default_write_mode", errors, () =>
                            {
                                DbType type = NativeRead(() => mdb.DefaultType());
                                return NativeRead(() => mdb.WriteMode(type)).ToString();
                            });
                            DatabaseStateReadDatabases(mdb, offset, limit, result, databases, errors);
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                ThrowIfNativeFaulted();
                DatabaseStateError(errors, "database_state", "read_failed", ex.Message);
            }
            DatabaseStateRead(result, "ce_after", "ce_after", errors, () => ModelCurrentReference());
            ModelCeUnchanged(result);
            if (!Equals(result["ce_unchanged"], true))
                DatabaseStateError(errors, "ce_unchanged", "consistency_unverified", "Current element could not be verified unchanged.");
            if (mdb != null && project != null)
            {
                DatabaseStateRead(result, "identity_unchanged", "identity_unchanged", errors, () =>
                    ReferenceEquals(MDB.CurrentMDB, mdb) && ReferenceEquals(Project.CurrentProject, project) &&
                    result["project"] != null && result["mdb"] != null &&
                    string.Equals(NativeRead(() => project.Name), (string)result["project"], StringComparison.Ordinal) &&
                    string.Equals(NativeRead(() => mdb.Name), (string)result["mdb"], StringComparison.Ordinal));
                if (!Equals(result["identity_unchanged"], true))
                    DatabaseStateError(errors, "identity_unchanged", "consistency_unverified", "Project/MDB identity could not be verified unchanged.");
            }
            bool successful = Equals(result["available"], true) && errors.Count == 0;
            result["success"] = successful;
            result["page_complete"] = successful;
            if (!successful) result["next_offset"] = null;
            bool complete = successful && offset == 0 && Equals(result["has_more"], false);
            result["complete"] = complete;
            return result;
        });
    }

    private static void DatabaseStateReadDatabases(MDB mdb, int offset, int limit,
        Dictionary<string, object> result, List<object> output, List<object> errors)
    {
        try
        {
            Db[] values = NativeRead(() => mdb.GetDBArray());
            if (values == null) throw new InvalidOperationException("SDK returned no database array.");
            result["total_count"] = values.Length;
            if (values.Length > 10000)
            {
                DatabaseStateError(errors, "databases", "database_count_limit", "The SDK database array exceeds the 10000-entry inspection limit; no database details were read.");
                return;
            }
            int end = Math.Min(values.Length, offset + limit);
            result["has_more"] = end < values.Length;
            result["next_offset"] = end < values.Length ? (object)end : null;
            for (int index = offset; index < end; index++)
            {
                ThrowIfNativeFaulted();
                string path = "databases[" + index + "]";
                Db database = values[index];
                var item = new Dictionary<string, object> { ["index"] = index, ["available"] = database != null };
                output.Add(item);
                if (database == null)
                {
                    DatabaseStateError(errors, path, "null_database", "SDK returned a null database entry.");
                    continue;
                }
                DatabaseStateRead(item, "name", path + ".name", errors, () => database.Name);
                DatabaseStateRead(item, "number", path + ".number", errors, () => database.Number);
                DatabaseStateRead(item, "extract_number", path + ".extract_number", errors, () => database.ExtractNumber);
                DatabaseStateRead(item, "type", path + ".type", errors, () => database.Type.ToString());
                DatabaseStateRead(item, "is_read_only", path + ".is_read_only", errors, () => database.IsReadOnly);
                DatabaseStateRead(item, "is_primary", path + ".is_primary", errors, () => database.IsPrimary);
                DatabaseStateRead(item, "is_extract", path + ".is_extract", errors, () => database.IsExtract);
                DatabaseStateRead(item, "has_changed_this_session", path + ".has_changed_this_session", errors, () => database.HasChangedThisSession);
                item["current_session"] = DatabaseStateSession(() => database.CurrentSession, path + ".current_session", errors);
                item["latest_session"] = DatabaseStateSession(() => database.LatestSessionOnDb, path + ".latest_session", errors);
            }
            result["returned_count"] = output.Count;
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            DatabaseStateError(errors, "databases", "read_failed", ex.Message);
        }
    }

    private static object DatabaseStateSession(Func<DbSession> getter, string path, List<object> errors)
    {
        var result = new Dictionary<string, object> { ["available"] = false, ["valid"] = null, ["number"] = null, ["extract_number"] = null };
        try
        {
            DbSession session = NativeRead(getter);
            if (session == null) throw new InvalidOperationException("SDK returned no session.");
            bool valid = NativeRead(() => session.IsValid);
            result["valid"] = valid;
            if (!valid)
            {
                DatabaseStateError(errors, path, "invalid_session", "SDK session is invalid; session fields were not read.");
                return result;
            }
            result["available"] = true;
            DatabaseStateRead(result, "number", path + ".number", errors, () => session.SessionNumber);
            DatabaseStateRead(result, "extract_number", path + ".extract_number", errors, () => session.Extract);
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            DatabaseStateError(errors, path, "read_failed", ex.Message);
        }
        return result;
    }

    private static void DatabaseStateRead(Dictionary<string, object> target, string key, string path,
        List<object> errors, Func<object> getter)
    {
        target[key] = null;
        try { target[key] = NativeRead(getter); }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            DatabaseStateError(errors, path, "read_failed", ex.Message);
        }
    }

    private static string DatabaseStateContext(string sessionId, string project, string mdb)
    {
        using (SHA256 sha = SHA256.Create())
        {
            byte[] bytes = Encoding.UTF8.GetBytes(sessionId + "\n" + project + "\n" + mdb);
            StringBuilder builder = new StringBuilder(64);
            foreach (byte value in sha.ComputeHash(bytes)) builder.Append(value.ToString("x2"));
            return builder.ToString();
        }
    }

    private static void DatabaseStateError(List<object> errors, string path, string code, string message)
    {
        errors.Add(new Dictionary<string, object> { ["path"] = path, ["code"] = code, ["message"] = message });
    }
}
