using System;
using System.Collections.Generic;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core3D.Clasher;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    public object GetSpatialMapStatus(int databaseNumber, int extractNumber, string expectedContext, string sessionId)
    {
        if (databaseNumber <= 0) throw new ArgumentOutOfRangeException("databaseNumber", "database_number must be positive.");
        if (extractNumber < 0) throw new ArgumentOutOfRangeException("extractNumber", "extract_number must be nonnegative.");
        DatabaseStateRequest.Validate(0, 1, expectedContext);
        if (string.IsNullOrWhiteSpace(sessionId)) throw new ArgumentException("Bridge session identity is unavailable.");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
            var errors = new List<object>();
            var result = new Dictionary<string, object>
            {
                ["success"] = false, ["available"] = false, ["complete"] = false,
                ["source"] = "E3D21 SDK SpatialMap.CheckSpatialMap", ["consistency"] = "live",
                ["requested"] = new { database_number = databaseNumber, extract_number = extractNumber },
                ["project"] = null, ["mdb"] = null, ["session_id"] = sessionId, ["context_id"] = null,
                ["database"] = null, ["reason"] = null, ["database_count"] = null,
                ["enumeration_complete"] = false, ["number_match_count"] = 0, ["exact_match_count"] = 0,
                ["matching_extract_numbers"] = new int[0],
                ["check_attempted"] = false, ["check_completed"] = false,
                ["status"] = null, ["status_value"] = null, ["status_known"] = null, ["is_up_to_date"] = null,
                ["map_validity_verified"] = false,
                ["database_resolution"] = "The requested (Number, ExtractNumber) must match exactly one current-MDB entry. SDK CheckSpatialMap resolves Db by Number only; multiple extracts for that number and Db.IsExtract=true are unsupported.",
                ["availability_scope"] = "A unique requested database was located in a completely inspected MDB identity array; availability does not imply that its spatial map was queried or is valid.",
                ["enumeration_scope"] = "SDK GetDBArray materializes the full MDB array. At most 10000 entries are accepted; every database number and every matching number's extract are inspected before native CheckSpatialMap.",
                ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
                ["identity_unchanged_before_check"] = null, ["identity_unchanged"] = null,
                ["messages"] = new List<object>(), ["read_errors"] = errors,
                ["message_scope"] = "Messages observed during this callback; exclusive attribution and complete ring-buffer coverage are not proven.",
                ["global_diagnostic_state_unchanged"] = null,
                ["limitations"] = new[]
                {
                    "Read success and completeness describe status observation, not map validity. UNKNOWN, NOWARNINGS and every other non-UPTODATE value remain distinct from UPTODATE.",
                    "Native spatial-map checking is suspended: the 2026-11-16 E3D21 native stack overflow remains unresolved. Database identity metadata remains available; no SpatialMap service is acquired and no map is checked or built."
                }
            };
            HashSet<Dictionary<string, object>> oldMessages;
            lock (messageLock) oldMessages = new HashSet<Dictionary<string, object>>(outputMessages);
            MDB mdb = null;
            Project project = null;
            try
            {
                DatabaseStateRead(result, "ce_before", "ce_before", errors, () => ModelCurrentReference());
                try
                {
                    mdb = NativeRead(() => MDB.CurrentMDB);
                    project = NativeRead(() => Project.CurrentProject);
                    if (mdb == null || project == null)
                        SpatialMapStatusReject(result, errors, "context", "no_project_mdb", "No current project/MDB is open.");
                    else
                    {
                        DatabaseStateRead(result, "project", "project", errors, () => project.Name);
                        DatabaseStateRead(result, "mdb", "mdb", errors, () => mdb.Name);
                        if (string.IsNullOrWhiteSpace(result["project"] as string) || string.IsNullOrWhiteSpace(result["mdb"] as string))
                            SpatialMapStatusReject(result, errors, "context_id", "identity_unavailable", "Project/MDB names must be readable and nonempty.");
                        else
                        {
                            string context = DatabaseStateContext(sessionId, (string)result["project"], (string)result["mdb"]);
                            result["context_id"] = context;
                            if (expectedContext.Length > 0 && expectedContext != context)
                                SpatialMapStatusReject(result, errors, "expected_context", "context_mismatch", "The E3D session, project or MDB changed; obtain the current database-state context before querying this database.");
                            else
                            {
                                Db database = SpatialMapStatusResolveDatabase(mdb, databaseNumber, extractNumber, result, errors);
                                if (database != null && errors.Count == 0)
                                {
                                    bool unchanged = SpatialMapStatusContextMatches(mdb, project, result);
                                    result["identity_unchanged_before_check"] = unchanged;
                                    if (!unchanged)
                                        SpatialMapStatusReject(result, errors, "identity_unchanged_before_check", "context_changed", "Project/MDB identity changed while resolving the database; native CheckSpatialMap was not called.");
                                    else
                                    {
                                        string suspensionReason, suspensionMessage;
                                        if (!NativeSpatialMapCheckAllowed(out suspensionReason, out suspensionMessage))
                                            SpatialMapStatusReject(result, errors, "spatial_map_policy", suspensionReason, suspensionMessage);
                                        else
                                        {
                                            SpatialMap map = NativeRead(() => SpatialMap.Instance);
                                            if (map == null)
                                                SpatialMapStatusReject(result, errors, "spatial_map", "service_unavailable", "The native spatial-map service is unavailable.");
                                            else
                                            {
                                                ThrowIfNativeFaulted();
                                                result["check_attempted"] = true;
                                                SpatialMapStatus status = NativeRead(() => map.CheckSpatialMap(database));
                                                result["check_completed"] = true;
                                                result["status"] = status.ToString();
                                                result["status_value"] = (int)status;
                                                result["status_known"] = Enum.IsDefined(typeof(SpatialMapStatus), status);
                                                result["is_up_to_date"] = status == SpatialMapStatus.UPTODATE;
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                catch (Exception error)
                {
                    ThrowIfNativeFaulted();
                    SpatialMapStatusReject(result, errors, Equals(result["check_attempted"], true) ? "check" : "spatial_map_status", "read_failed", error.Message);
                }
                DatabaseStateRead(result, "ce_after", "ce_after", errors, () => ModelCurrentReference());
                ModelCeUnchanged(result);
                if (!Equals(result["ce_unchanged"], true))
                    SpatialMapStatusReject(result, errors, "ce_unchanged", "consistency_unverified", "Current element could not be verified unchanged.");
                if (mdb != null && project != null)
                {
                    DatabaseStateRead(result, "identity_unchanged", "identity_unchanged", errors, () => SpatialMapStatusContextMatches(mdb, project, result));
                    if (!Equals(result["identity_unchanged"], true))
                        SpatialMapStatusReject(result, errors, "identity_unchanged", "consistency_unverified", "Project/MDB identity could not be verified unchanged.");
                }
                bool successful = errors.Count == 0 && Equals(result["available"], true) && Equals(result["check_completed"], true);
                result["success"] = successful;
                result["complete"] = successful;
                return result;
            }
            finally
            {
                lock (messageLock)
                    result["messages"] = outputMessages.Where(value => !oldMessages.Contains(value)).Cast<object>().ToList();
            }
        });
    }

    // Fixed shared isolation policy for both status queries and clash preflight.
    // No request, environment variable or configuration switch can bypass this.
    private static bool NativeSpatialMapCheckAllowed(out string reason, out string message)
    {
        reason = "native_spatial_map_check_suspended";
        message = "Native CheckSpatialMap is suspended: the 2026-11-16 E3D21 native stack overflow remains unresolved. Only database/input metadata inspection is available; no SpatialMap.Instance, CheckSpatialMap or Clasher factories are called. This policy has no runtime bypass.";
        return false;
    }

    private static Db SpatialMapStatusResolveDatabase(MDB mdb, int number, int extract,
        Dictionary<string, object> result, List<object> errors)
    {
        Db[] values = NativeRead(() => mdb.GetDBArray());
        if (values == null)
        {
            SpatialMapStatusReject(result, errors, "databases", "database_array_unavailable", "SDK returned no current-MDB database array.");
            return null;
        }
        result["database_count"] = values.Length;
        if (values.Length > 10000)
        {
            SpatialMapStatusReject(result, errors, "databases", "database_count_limit", "The SDK array exceeds the 10000-entry limit; no entries were inspected and native CheckSpatialMap was not called.");
            return null;
        }
        int priorErrors = errors.Count;
        var matchingExtracts = new HashSet<int>();
        var exact = new List<Db>();
        int numberMatches = 0;
        for (int index = 0; index < values.Length; index++)
        {
            ThrowIfNativeFaulted();
            string path = "databases[" + index + "]";
            Db candidate = values[index];
            if (candidate == null)
            {
                DatabaseStateError(errors, path, "null_database", "A null entry prevents complete database identity resolution.");
                continue;
            }
            try
            {
                int candidateNumber = NativeRead(() => candidate.Number);
                if (candidateNumber != number) continue;
                numberMatches++;
                int candidateExtract = NativeRead(() => candidate.ExtractNumber);
                matchingExtracts.Add(candidateExtract);
                if (candidateExtract == extract) exact.Add(candidate);
            }
            catch (Exception error)
            {
                ThrowIfNativeFaulted();
                DatabaseStateError(errors, path, "identity_read_failed", error.Message);
            }
        }
        result["number_match_count"] = numberMatches;
        result["exact_match_count"] = exact.Count;
        result["matching_extract_numbers"] = matchingExtracts.OrderBy(value => value).ToArray();
        bool enumerationComplete = errors.Count == priorErrors;
        result["enumeration_complete"] = enumerationComplete;
        Db database = exact.Count == 1 ? exact[0] : null;
        Dictionary<string, object> metadata = null;
        if (database != null)
        {
            metadata = new Dictionary<string, object> { ["number"] = number, ["extract_number"] = extract };
            result["database"] = metadata;
            DatabaseStateRead(metadata, "name", "database.name", errors, () => database.Name);
            DatabaseStateRead(metadata, "type", "database.type", errors, () => database.Type.ToString());
            DatabaseStateRead(metadata, "is_extract", "database.is_extract", errors, () => database.IsExtract);
            DatabaseStateRead(metadata, "is_primary", "database.is_primary", errors, () => database.IsPrimary);
            DatabaseStateRead(metadata, "is_read_only", "database.is_read_only", errors, () => database.IsReadOnly);
        }
        if (!enumerationComplete)
        {
            SpatialMapStatusReject(result, errors, "databases", "database_resolution_incomplete", "At least one MDB entry could not be excluded as a possible database-number match; native CheckSpatialMap was not called.");
            return null;
        }
        if (exact.Count != 1)
        {
            SpatialMapStatusReject(result, errors, "database", exact.Count == 0 ? "database_not_found" : "ambiguous_database_identity",
                "The requested (database_number, extract_number) must identify exactly one current-MDB entry.");
            return null;
        }
        result["available"] = true;
        if (matchingExtracts.Count != 1)
        {
            SpatialMapStatusReject(result, errors, "database", "ambiguous_spatial_map_database_number", "Multiple extracts share this database number in the current MDB; the SDK's number-only map lookup cannot be scoped to the requested extract.");
            return null;
        }
        if (metadata["type"] != null && !Equals(metadata["type"], DbType.Design.ToString()))
        {
            SpatialMapStatusReject(result, errors, "database.type", "unsupported_database_type", "Spatial map status requires a Design database.");
            return null;
        }
        if (Equals(metadata["is_extract"], true))
        {
            SpatialMapStatusReject(result, errors, "database.is_extract", "unsupported_spatial_map_extract_scope", "The SDK resolves map databases by Number only; Db.IsExtract=true cannot be checked with verified extract targeting.");
            return null;
        }
        if (errors.Count > priorErrors)
        {
            SpatialMapStatusReject(result, errors, "database", "database_metadata_incomplete", "Requested database metadata could not be read completely; native CheckSpatialMap was not called.");
            return null;
        }
        return errors.Count == 0 ? database : null;
    }

    private static bool SpatialMapStatusContextMatches(MDB mdb, Project project, Dictionary<string, object> result)
    {
        MDB currentMdb = NativeRead(() => MDB.CurrentMDB);
        Project currentProject = NativeRead(() => Project.CurrentProject);
        return ReferenceEquals(currentMdb, mdb) && ReferenceEquals(currentProject, project) &&
            result["project"] != null && result["mdb"] != null &&
            string.Equals(NativeRead(() => project.Name), (string)result["project"], StringComparison.Ordinal) &&
            string.Equals(NativeRead(() => mdb.Name), (string)result["mdb"], StringComparison.Ordinal);
    }

    private static void SpatialMapStatusReject(Dictionary<string, object> result, List<object> errors,
        string path, string code, string message)
    {
        if (result["reason"] == null) result["reason"] = code;
        DatabaseStateError(errors, path, code, message);
    }
}
