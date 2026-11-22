using System;
using System.Collections.Generic;
using System.Globalization;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private const int ProjectCatalogMaximum = 10000;
    private const int ProjectCatalogMatchLimit = 64;

    public object GetProjectCatalogProbe(string team, string name, int? databaseNumber,
        string expectedContext, string sessionId)
    {
        if (string.IsNullOrWhiteSpace(sessionId)) throw new ArgumentException("Bridge session identity is unavailable.");
        DatabaseStateRequest.ValidateProjectCatalogProbe(expectedContext, ref team, ref name, databaseNumber);
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
            var errors = new List<object>();
            var databases = new List<object>();
            var teams = new List<string>();
            var worldDatabaseMatches = new List<object>();
            var worldTeamMatches = new List<string>();
            var worldErrors = new List<object>();
            var result = new Dictionary<string, object>
            {
                ["success"] = false, ["available"] = false, ["source"] = "E3D21 SDK DbSystem.GetAll(DB/TEAM)",
                ["single_dispatch"] = true, ["atomic_snapshot"] = false, ["session_id"] = sessionId,
                ["project"] = null, ["mdb"] = null, ["context_id"] = expectedContext, ["expected_context"] = expectedContext,
                ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
                ["identity_unchanged"] = null, ["candidate"] = new Dictionary<string, object>
                { ["team"] = team, ["name"] = name, ["full_database_name"] = team + "/" + name,
                  ["database_number"] = databaseNumber },
                ["database_matches"] = databases, ["database_match_count"] = null,
                ["database_matches_truncated"] = false, ["team_matches"] = teams,
                ["team_match_count"] = null, ["team_matches_truncated"] = false,
                ["counts"] = new Dictionary<string, object>
                { ["mdb_database_count"] = null, ["mdb_database_identity_count"] = null,
                  ["mdb_database_positive_control_count"] = null, ["mdb_team_positive_control_count"] = null,
                  ["catalog_database_count"] = null, ["catalog_database_inspected_count"] = null,
                  ["catalog_team_count"] = null, ["catalog_team_inspected_count"] = null,
                  ["catalog_database_outside_mdb_count"] = null },
                ["world_level"] = new Dictionary<string, object>
                {
                    ["success"] = false, ["available"] = false, ["status"] = "unavailable",
                    ["source"] = "E3D21 SDK DbSystem.GetAllWorldLevel(DB/TEAM)",
                    ["visibility"] = "world_level",
                    ["visibility_scope"] = "Global-equivalent world-level DB/TEAM visibility only; this is not Admin permission, Area availability, project-wide catalog coverage or create authorization.",
                    ["admin_permission_verified"] = false, ["area_availability_verified"] = false,
                    ["project_coverage_verified"] = false,
                    ["single_dispatch"] = true, ["atomic_snapshot"] = false,
                    ["session_id"] = sessionId, ["project"] = null, ["mdb"] = null,
                    ["context_id"] = expectedContext, ["expected_context"] = expectedContext,
                    ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
                    ["identity_unchanged"] = null,
                    ["candidate"] = new Dictionary<string, object>
                    { ["team"] = team, ["name"] = name, ["full_database_name"] = team + "/" + name,
                      ["database_number"] = databaseNumber },
                    ["database_matches"] = worldDatabaseMatches, ["database_match_count"] = null,
                    ["database_matches_truncated"] = false, ["team_matches"] = worldTeamMatches,
                    ["team_match_count"] = null, ["team_matches_truncated"] = false,
                    ["counts"] = new Dictionary<string, object>
                    { ["world_database_count"] = null, ["world_database_inspected_count"] = null,
                      ["world_team_count"] = null, ["world_team_inspected_count"] = null },
                    ["read_errors"] = worldErrors
                },
                ["read_errors"] = errors,
                ["scope"] = "Positive DB/TEAM matches from bounded DbSystem.GetAll arrays; no full project coverage, Admin permission or create authorization."
            };

            var world = (Dictionary<string, object>)result["world_level"];
            var worldCounts = (Dictionary<string, object>)world["counts"];

            string ceBefore = ModelCurrentReference();
            result["ce_before"] = ceBefore;
            Project project = NativeRead(() => Project.CurrentProject);
            MDB mdb = NativeRead(() => MDB.CurrentMDB);
            if (project == null || mdb == null) throw new InvalidOperationException("No current project/MDB is open in E3D.");
            string projectName = NativeRead(() => project.Name);
            string mdbName = NativeRead(() => mdb.Name);
            result["project"] = projectName;
            result["mdb"] = mdbName;
            string context = DatabaseStateContext(sessionId, projectName, mdbName);
            result["context_id"] = context;
            world["project"] = projectName;
            world["mdb"] = mdbName;
            world["context_id"] = context;
            world["ce_before"] = ceBefore;
            if (!StringComparer.Ordinal.Equals(context, expectedContext))
                throw new InvalidOperationException("The E3D session, project or MDB changed before the catalog probe.");

            Db[] current = NativeRead(() => mdb.GetDBArray());
            DbElement[] catalog = NativeRead(() => DbSystem.Instance.GetAll(DbElementTypeInstance.DB));
            DbElement[] teamElements = NativeRead(() => DbSystem.Instance.GetAll(DbElementTypeInstance.TEAM));
            DbElement[] worldCatalog = null;
            DbElement[] worldTeamElements = null;
            bool worldArrayReadFailed = false;
            try
            {
                worldCatalog = NativeRead(() => DbSystem.Instance.GetAllWorldLevel(DbElementTypeInstance.DB));
                worldTeamElements = NativeRead(() => DbSystem.Instance.GetAllWorldLevel(DbElementTypeInstance.TEAM));
            }
            catch (Exception error)
            {
                ThrowIfNativeFaulted();
                worldArrayReadFailed = true;
                ProjectCatalogWorldError(world, worldErrors, "world_level_array", error.Message);
            }
            if (current == null || catalog == null || teamElements == null)
                throw new InvalidOperationException("SDK returned a null catalog array.");
            if (current.Length > ProjectCatalogMaximum || catalog.Length > ProjectCatalogMaximum || teamElements.Length > ProjectCatalogMaximum)
                throw new InvalidOperationException("A project catalog array exceeds the 10000-entry inspection limit.");
            if (worldArrayReadFailed)
            {
                world["status"] = "unavailable";
            }
            else if (worldCatalog == null || worldTeamElements == null)
            {
                world["status"] = "unavailable";
                ProjectCatalogWorldError(world, worldErrors, "world_level_array", "The SDK returned a null world-level catalog array.");
            }
            else if (worldCatalog.Length > ProjectCatalogMaximum || worldTeamElements.Length > ProjectCatalogMaximum)
            {
                world["status"] = "unavailable";
                ProjectCatalogWorldError(world, worldErrors, "world_level_limit", "A world-level catalog array exceeds the 10000-entry inspection limit.");
            }
            else
            {
                worldCounts["world_database_count"] = worldCatalog.Length;
                worldCounts["world_database_inspected_count"] = 0;
                worldCounts["world_team_count"] = worldTeamElements.Length;
                worldCounts["world_team_inspected_count"] = 0;
            }

            Dictionary<string, int> currentIdentity = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            HashSet<string> currentTeams = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            int currentReadable = 0;
            bool currentComplete = true;
            foreach (Db db in current)
            {
                if (db == null) { currentComplete = false; continue; }
                string dbName = NativeRead(() => db.Name);
                int dbNumber = NativeRead(() => db.Number);
                if (!String.IsNullOrWhiteSpace(dbName) && dbNumber > 0)
                {
                    currentReadable++;
                    string canonical = ProjectCatalogName(dbName);
                    currentIdentity[canonical + "\n" + dbNumber.ToString(CultureInfo.InvariantCulture)] = 1;
                    int slash = canonical.IndexOf('/');
                    if (slash > 0) currentTeams.Add(canonical.Substring(0, slash));
                }
                else currentComplete = false;
            }
            var counts = (Dictionary<string, object>)result["counts"];
            counts["mdb_database_count"] = current.Length;
            counts["mdb_database_identity_count"] = currentReadable;
            counts["catalog_database_count"] = catalog.Length;
            counts["catalog_database_inspected_count"] = 0;
            counts["catalog_team_count"] = teamElements.Length;
            counts["catalog_team_inspected_count"] = 0;

            DbAttribute nameAttribute = NativeRead(() => DbAttribute.GetDbAttribute("NAMEDB"));
            DbAttribute numberAttribute = NativeRead(() => DbAttribute.GetDbAttribute("DBNO"));
            DbAttribute teamNameAttribute = NativeRead(() => DbAttribute.GetDbAttribute("NAMN"));
            int nameReadable = 0, numberReadable = 0, outside = 0, databaseMatchCount = 0, currentDatabasePositiveControls = 0;
            bool databaseCatalogComplete = true;
            foreach (DbElement element in catalog)
            {
                ThrowIfNativeFaulted();
                if (element == null || NativeRead(() => element.IsNull))
                {
                    databaseCatalogComplete = false;
                    ProjectCatalogError(result, errors, "catalog_database", "The SDK returned a null DB catalog element.");
                    continue;
                }
                string dbName = null; int dbNumber = 0;
                bool hasName = ProjectCatalogGetString(result, errors, element, nameAttribute, ref dbName,
                    "catalog_database_name", "NAMEDB");
                bool hasNumber = ProjectCatalogGetInteger(result, errors, element, numberAttribute, ref dbNumber,
                    "catalog_database_number", "DBNO");
                if (hasName) nameReadable++;
                else
                {
                    dbName = null; databaseCatalogComplete = false;
                }
                if (!hasNumber || dbNumber < 1)
                {
                    dbNumber = 0; databaseCatalogComplete = false;
                }
                else numberReadable++;
                counts["catalog_database_inspected_count"] = (int)counts["catalog_database_inspected_count"] + 1;
                string canonicalDatabaseName = dbName == null ? null : ProjectCatalogName(dbName);
                bool? nameMatch = canonicalDatabaseName == null ? (bool?)null : StringComparer.Ordinal.Equals(canonicalDatabaseName, team + "/" + name);
                bool? numberMatch = databaseNumber.HasValue && dbNumber > 0 ? (bool?)(dbNumber == databaseNumber.Value) : null;
                bool currentMatch = canonicalDatabaseName != null && dbNumber > 0
                    && currentIdentity.ContainsKey(canonicalDatabaseName + "\n" + dbNumber.ToString(CultureInfo.InvariantCulture));
                if (currentMatch) currentDatabasePositiveControls++;
                else if (currentComplete && canonicalDatabaseName != null && dbNumber > 0) outside++;
                if (nameMatch != true && numberMatch != true) continue;
                databaseMatchCount++;
                if (databases.Count < ProjectCatalogMatchLimit)
                    databases.Add(new Dictionary<string, object>
                    { ["name"] = canonicalDatabaseName, ["database_number"] = dbNumber == 0 ? (object)null : dbNumber,
                      ["name_match"] = nameMatch, ["database_number_match"] = numberMatch,
                      ["current_mdb_match"] = currentMatch ? "name_and_database_number" :
                        canonicalDatabaseName == null || dbNumber == 0 || !currentComplete ? "indeterminate" : "no_name_or_number_match" });
            }
            counts["catalog_database_readable_name_count"] = nameReadable;
            counts["catalog_database_readable_number_count"] = numberReadable;
            counts["catalog_database_outside_mdb_count"] = currentComplete && databaseCatalogComplete ? (object)outside : null;
            result["database_match_count"] = databaseMatchCount;
            result["database_matches_truncated"] = databaseMatchCount > ProjectCatalogMatchLimit;

            int teamReadable = 0, teamMatchCount = 0, currentTeamPositiveControls = 0;
            bool teamCatalogComplete = true;
            foreach (DbElement element in teamElements)
            {
                ThrowIfNativeFaulted();
                counts["catalog_team_inspected_count"] = (int)counts["catalog_team_inspected_count"] + 1;
                if (element == null || NativeRead(() => element.IsNull))
                {
                    teamCatalogComplete = false;
                    ProjectCatalogError(result, errors, "catalog_team", "The SDK returned a null TEAM catalog element.");
                    continue;
                }
                string teamName = null;
                bool valid = ProjectCatalogGetString(result, errors, element, teamNameAttribute, ref teamName,
                    "catalog_team_name", "NAMN");
                if (!valid)
                {
                    teamCatalogComplete = false;
                    continue;
                }
                teamReadable++;
                teamName = ProjectCatalogTeam(teamName);
                if (currentTeams.Contains(teamName)) currentTeamPositiveControls++;
                if (StringComparer.OrdinalIgnoreCase.Equals(teamName, team))
                {
                    teamMatchCount++;
                    if (teams.Count < ProjectCatalogMatchLimit) teams.Add(team);
                }
            }
            counts["catalog_team_readable_name_count"] = teamReadable;
            result["team_match_count"] = teamMatchCount;
            result["team_matches_truncated"] = teamMatchCount > ProjectCatalogMatchLimit;
            counts["mdb_database_positive_control_count"] = currentDatabasePositiveControls;
            counts["mdb_team_positive_control_count"] = currentTeamPositiveControls;

            if (!worldArrayReadFailed && worldCatalog != null && worldTeamElements != null &&
                worldCatalog.Length <= ProjectCatalogMaximum && worldTeamElements.Length <= ProjectCatalogMaximum)
            {
                ProjectCatalogScanWorldLevel(result, world, worldCatalog, worldTeamElements, team, name,
                    databaseNumber, currentIdentity, currentComplete, nameAttribute, numberAttribute, teamNameAttribute);
            }
            string ceAfter = ModelCurrentReference();
            result["ce_after"] = ceAfter;
            result["ce_unchanged"] = ceBefore == ceAfter;
            result["identity_unchanged"] = ReferenceEquals(NativeRead(() => Project.CurrentProject), project)
                && ReferenceEquals(NativeRead(() => MDB.CurrentMDB), mdb)
                && StringComparer.Ordinal.Equals(NativeRead(() => project.Name), projectName)
                && StringComparer.Ordinal.Equals(NativeRead(() => mdb.Name), mdbName);
            if (!(bool)result["ce_unchanged"] || !(bool)result["identity_unchanged"])
                throw new InvalidOperationException("Project, MDB or current element changed during the catalog probe.");
            world["ce_after"] = ceAfter;
            world["ce_unchanged"] = result["ce_unchanged"];
            world["identity_unchanged"] = result["identity_unchanged"];
            world["available"] = !worldArrayReadFailed && worldCatalog != null && worldTeamElements != null &&
                worldCatalog.Length <= ProjectCatalogMaximum && worldTeamElements.Length <= ProjectCatalogMaximum;
            world["success"] = world["available"] is bool && (bool)world["available"] &&
                worldErrors.Count == 0 && world["ce_unchanged"] is bool && (bool)world["ce_unchanged"] &&
                world["identity_unchanged"] is bool && (bool)world["identity_unchanged"];
            world["status"] = (bool)world["success"] ? "observed" :
                (bool)world["available"] ? "partial" : "unavailable";
            result["success"] = currentComplete && currentReadable > 0 && databaseCatalogComplete
                && nameReadable > 0 && numberReadable > 0 && teamCatalogComplete && teamReadable > 0
                && currentDatabasePositiveControls > 0 && currentTeamPositiveControls > 0 && errors.Count == 0;
            result["available"] = true;
            return result;
        });
    }

    private static void ProjectCatalogScanWorldLevel(Dictionary<string, object> result,
        Dictionary<string, object> world, DbElement[] databases, DbElement[] teamElements,
        string team, string name, int? databaseNumber, Dictionary<string, int> currentIdentity,
        bool currentComplete, DbAttribute nameAttribute, DbAttribute numberAttribute,
        DbAttribute teamNameAttribute)
    {
        var errors = (List<object>)world["read_errors"];
        var databaseMatches = (List<object>)world["database_matches"];
        var teamMatches = (List<string>)world["team_matches"];
        var counts = (Dictionary<string, object>)world["counts"];
        int databaseMatchCount = 0;
        int databaseInspected = 0;
        int teamMatchCount = 0;
        int teamInspected = 0;
        bool databaseComplete = true;
        bool teamComplete = true;
        foreach (DbElement element in databases)
        {
            ThrowIfNativeFaulted();
            if (!ProjectCatalogWorldElementUsable(world, errors, element, "world_level_database"))
            {
                databaseComplete = false;
                continue;
            }
            string dbName = null;
            int dbNumber = 0;
            bool hasName = ProjectCatalogGetString(world, errors, element, nameAttribute, ref dbName,
                "world_level_database_name", "NAMEDB");
            bool hasNumber = ProjectCatalogGetInteger(world, errors, element, numberAttribute, ref dbNumber,
                "world_level_database_number", "DBNO");
            if (!hasName) { dbName = null; databaseComplete = false; }
            if (!hasNumber || dbNumber < 1) { dbNumber = 0; databaseComplete = false; }
            databaseInspected++;
            string canonical = dbName == null ? null : ProjectCatalogName(dbName);
            bool? nameMatch = canonical == null ? (bool?)null :
                StringComparer.Ordinal.Equals(canonical, team + "/" + name);
            bool? numberMatch = databaseNumber.HasValue && dbNumber > 0 ?
                (bool?)(dbNumber == databaseNumber.Value) : null;
            if (nameMatch != true && numberMatch != true) continue;
            databaseMatchCount++;
            if (databaseMatches.Count < ProjectCatalogMatchLimit)
            {
                bool currentMatch = canonical != null && dbNumber > 0 &&
                    currentIdentity.ContainsKey(canonical + "\n" + dbNumber.ToString(CultureInfo.InvariantCulture));
                databaseMatches.Add(new Dictionary<string, object>
                {
                    ["name"] = canonical,
                    ["database_number"] = dbNumber == 0 ? (object)null : dbNumber,
                    ["name_match"] = nameMatch,
                    ["database_number_match"] = numberMatch,
                    ["current_mdb_match"] = currentMatch ? "name_and_database_number" :
                        canonical == null || dbNumber == 0 || !currentComplete ? "indeterminate" : "no_name_or_number_match"
                });
            }
        }
        foreach (DbElement element in teamElements)
        {
            ThrowIfNativeFaulted();
            if (!ProjectCatalogWorldElementUsable(world, errors, element, "world_level_team"))
            {
                teamComplete = false;
                continue;
            }
            string teamName = null;
            if (!ProjectCatalogGetString(world, errors, element, teamNameAttribute, ref teamName,
                "world_level_team_name", "NAMN"))
            {
                teamComplete = false;
                continue;
            }
            teamInspected++;
            teamName = ProjectCatalogTeam(teamName);
            if (StringComparer.OrdinalIgnoreCase.Equals(teamName, team))
            {
                teamMatchCount++;
                if (teamMatches.Count < ProjectCatalogMatchLimit) teamMatches.Add(team);
            }
        }
        counts["world_database_inspected_count"] = databaseInspected;
        counts["world_team_inspected_count"] = teamInspected;
        world["database_match_count"] = databaseMatchCount;
        world["database_matches_truncated"] = databaseMatchCount > ProjectCatalogMatchLimit;
        world["team_match_count"] = teamMatchCount;
        world["team_matches_truncated"] = teamMatchCount > ProjectCatalogMatchLimit;
        if (databaseComplete && teamComplete && errors.Count == 0)
            world["status"] = "observed";
    }

    private static string ProjectCatalogName(string value)
    {
        return value == null ? null : value.TrimStart('/').ToUpperInvariant();
    }

    private static bool ProjectCatalogWorldElementUsable(Dictionary<string, object> world,
        List<object> errors, DbElement element, string stage)
    {
        try
        {
            if (element == null || NativeRead(() => element.IsNull))
                throw new InvalidOperationException("The SDK returned a null world-level catalog element.");
            return true;
        }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            ProjectCatalogWorldError(world, errors, stage, error.Message);
            return false;
        }
    }

    private static string ProjectCatalogTeam(string value)
    {
        return value == null ? null : value.TrimStart('*', '/').ToUpperInvariant();
    }

    private static void ProjectCatalogError(Dictionary<string, object> result, List<object> errors,
        string stage, string message)
    {
        if (errors.Count < 128)
            errors.Add(new Dictionary<string, object> { ["stage"] = stage, ["refno"] = null, ["error"] = message });
        else
            result["read_errors_truncated"] = true;
    }

    private static void ProjectCatalogWorldError(Dictionary<string, object> world, List<object> errors,
        string stage, string message)
    {
        if (errors.Count < 128)
            errors.Add(new Dictionary<string, object> { ["stage"] = stage, ["refno"] = null, ["error"] = message });
        else
            world["read_errors_truncated"] = true;
    }

    private static bool ProjectCatalogGetString(Dictionary<string, object> result, List<object> errors,
        DbElement element, DbAttribute attribute, ref string value, string stage, string field)
    {
        try
        {
            string observed = value;
            if (!NativeRead(() => element.GetValidString(attribute, ref observed)) || String.IsNullOrWhiteSpace(observed))
                throw new InvalidOperationException(field + " was unavailable on a catalog element.");
            value = observed.TrimStart('/');
            return true;
        }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            value = null;
            ProjectCatalogError(result, errors, stage, error.Message);
            return false;
        }
    }

    private static bool ProjectCatalogGetInteger(Dictionary<string, object> result, List<object> errors,
        DbElement element, DbAttribute attribute, ref int value, string stage, string field)
    {
        try
        {
            int observed = value;
            if (!NativeRead(() => element.GetValidInteger(attribute, ref observed)) || observed < 1)
                throw new InvalidOperationException(field + " was unavailable on a DB catalog element.");
            value = observed;
            return true;
        }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            value = 0;
            ProjectCatalogError(result, errors, stage, error.Message);
            return false;
        }
    }
}
