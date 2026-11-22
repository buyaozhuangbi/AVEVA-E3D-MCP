using System;
using System.Collections.Generic;
using System.Linq;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context : IE3d21TeamDatabaseIdentityManifestContext
{
    // The caller must already own a supported initialized SDK/UI context.
    public object ReadTeamDatabaseIdentityManifest(string sessionId, string expectedContext, int limit = 64)
    {
        if (string.IsNullOrWhiteSpace(sessionId) || sessionId.Length > 256 || sessionId.Any(char.IsControl))
            throw new ArgumentException("A bounded bridge session identity is required.");
        TeamDatabaseIdentityManifestRequest request = TeamDatabaseIdentityManifestRequest.Validate(expectedContext, limit);
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy.");
        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy.");
            var errors = new List<object>();
            var teams = new List<TeamDbTeam>();
            var records = new List<TeamDbRecord>();
            var current = new List<TeamDbMdb>();
            var before = TeamDbBoundary(sessionId, errors, out Project project, out MDB mdb, "before");
            if (ReferenceEquals(project, null) || ReferenceEquals(mdb, null) || errors.Count != 0 || !Equals(before["context_id"], request.ExpectedContext))
                throw new InvalidOperationException("A readable matching project/MDB/CE context is required before the identity scan.");

            var scans = new Dictionary<string, object>();
            DbSystem system = TeamDbAttempt(errors, "system", () => NativeRead(() => DbSystem.Instance));
            DbElement[] teamArray = ReferenceEquals(system, null) ? null : TeamDbAttempt(errors, "teams.array", () => NativeRead(() => system.GetAll(DbElementTypeInstance.TEAM)));
            DbElement[] recordArray = ReferenceEquals(system, null) ? null : TeamDbAttempt(errors, "databases.array", () => NativeRead(() => system.GetAll(DbElementTypeInstance.DB)));
            Db[] mdbArray = TeamDbAttempt(errors, "current_mdb.array", () => NativeRead(() => mdb.GetDBArray()));
            bool teamArrayComplete = TeamDbScan(teamArray, limit, "teams", errors, scans, (element, index) =>
            {
                var row = new TeamDbTeam { Index = index }; teams.Add(row);
                row.Identity = TeamDbAttempt(row.Errors, "teams[" + index + "].identity", () => TeamDbRecordIdentity(element, "TEAM"));
                if (row.Identity != null)
                {
                    row.RawName = TeamDbString(element, DbAttributeInstance.NAMN, row.Errors, "teams[" + index + "].NAMN");
                    row.Name = TeamDbAttempt(row.Errors, "teams[" + index + "].canonical_name", () => TeamDatabaseIdentityManifest.Name(row.RawName, false));
                }
                errors.AddRange(row.Errors);
            });
            bool recordArrayComplete = TeamDbScan(recordArray, limit, "databases", errors, scans, (element, index) =>
            {
                TeamDbRecord row = TeamDbReadRecord(element, index); records.Add(row); errors.AddRange(row.Errors);
            });
            bool mdbArrayComplete = TeamDbScan(mdbArray, limit, "current_mdb", errors, scans, (db, index) =>
            {
                var row = new TeamDbMdb { Index = index }; current.Add(row);
                string path = "current_mdb[" + index + "]";
                if (ReferenceEquals(db, null)) TeamDatabaseIdentityManifest.Error(row.Errors, path, "null_database", "The SDK returned a null database handle.");
                else
                {
                    row.RawName = TeamDbAttempt(row.Errors, path + ".Name", () => NativeRead(() => db.Name));
                    row.Name = TeamDbAttempt(row.Errors, path + ".canonical_name", () => TeamDatabaseIdentityManifest.Name(row.RawName, true));
                    row.Number = TeamDbAttempt<int?>(row.Errors, path + ".Number", () => TeamDbNumber(NativeRead(() => db.Number), 1));
                    row.ExtractNumber = TeamDbAttempt<int?>(row.Errors, path + ".ExtractNumber", () => TeamDbNumber(NativeRead(() => db.ExtractNumber), 0));
                    row.IsExtract = TeamDbAttempt<bool?>(row.Errors, path + ".IsExtract", () => NativeRead(() => db.IsExtract));
                }
                errors.AddRange(row.Errors);
            });
            // A failed row may hide a duplicate. Observed candidates are retained,
            // but uniqueness cannot be confirmed against such a source.
            bool teamsComplete = teamArrayComplete && teams.All(row => row.Identity != null && row.Name != null);
            bool recordIdentitiesComplete = recordArrayComplete && records.All(row => row.Identity != null);
            var after = TeamDbBoundary(sessionId, errors, out Project afterProject, out MDB afterMdb, "after");
            bool stable = errors.All(error => !((string)((Dictionary<string, object>)error)["path"]).StartsWith("after.", StringComparison.Ordinal)) &&
                ReferenceEquals(project, afterProject) && ReferenceEquals(mdb, afterMdb) &&
                Equals(before["project"], after["project"]) && Equals(before["mdb"], after["mdb"]) &&
                Equals(before["context_id"], after["context_id"]) && Equals(before["ce_identity_key"], after["ce_identity_key"]);
            if (!stable) TeamDatabaseIdentityManifest.Error(errors, "context", "context_changed_or_unreadable", "Project, MDB or typed current element could not be verified unchanged.");
            var findings = new List<object>();
            var rows = TeamDatabaseIdentityManifest.Resolve(teams, records, current, teamsComplete, recordIdentitiesComplete, stable, findings);
            bool complete = stable && teamArrayComplete && recordArrayComplete && mdbArrayComplete && errors.Count == 0;
            bool relationsConfirmed = rows.All(row => Equals(((Dictionary<string, object>)row["team_relation"])["status"], "confirmed"));
            return new Dictionary<string, object>
            {
                ["operation"] = "internal_team_database_identity_manifest", ["schema_version"] = 1,
                ["read_only"] = true, ["single_dispatch"] = true, ["atomic_snapshot"] = false,
                ["source"] = "SDK DbSystem.GetAll(TEAM/DB) and current MDB.GetDBArray",
                ["limit_per_source"] = limit, ["owner_hop_limit"] = 2, ["scans"] = scans,
                ["teams"] = teams.Select(row => row.Result()).ToList(), ["databases"] = rows,
                ["current_mdb"] = current.Select(row => row.Result()).ToList(),
                ["context"] = new Dictionary<string, object>
                {
                    ["before"] = before, ["after"] = after, ["boundary_agreement"] = stable,
                    ["session_id"] = sessionId, ["bridge_build"] = typeof(E3d21Context).Module.ModuleVersionId.ToString("D")
                },
                ["complete"] = complete, ["identity_consistent"] = findings.Count == 0,
                ["all_team_relations_confirmed"] = relationsConfirmed,
                ["success"] = complete && findings.Count == 0 && relationsConfirmed,
                ["read_errors"] = errors, ["identity_findings"] = findings,
                ["scope"] = new Dictionary<string, object>
                {
                    ["kind"] = "bounded_sdk_visible_arrays", ["project_coverage_verified"] = false,
                    ["world_level_equivalence_inspected"] = false, ["admin_permission_verified"] = false,
                    ["file_existence_verified"] = false, ["database_creation_verified"] = false,
                    ["extract_number_semantics_verified"] = false, ["mdb_record_join_verified"] = false
                }
            };
        });
    }

    private static T TeamDbAttempt<T>(List<object> errors, string path, Func<T> read)
    {
        try { return read(); }
        catch (NativeDatabaseFaultException) { throw; }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            TeamDatabaseIdentityManifest.Error(errors, path, "read_unavailable", error.Message);
            return default(T);
        }
    }

    private static bool TeamDbScan<T>(T[] array, int limit, string name, List<object> errors,
        Dictionary<string, object> scans, Action<T, int> read)
    {
        bool usable = array != null && array.Length <= TeamDatabaseIdentityManifest.MaximumMaterializedArray;
        int count = usable ? Math.Min(limit, array.Length) : 0;
        scans[name] = new Dictionary<string, object>
        {
            ["available"] = usable, ["materialized_count"] = array?.Length, ["returned_count"] = count,
            ["prefix_complete"] = usable && count == array.Length,
            ["has_more"] = usable ? (object)(count < array.Length) : null,
            ["materializes_before_limit"] = true
        };
        if (!usable)
        {
            TeamDatabaseIdentityManifest.Error(errors, name + ".array", "array_unavailable", "The SDK array was null or exceeded the 10000-entry materialized-array limit.");
            return false;
        }
        for (int index = 0; index < count; index++) read(array[index], index);
        return count == array.Length;
    }

    private static Dictionary<string, object> TeamDbBoundary(string session, List<object> errors,
        out Project project, out MDB mdb, string stage)
    {
        project = TeamDbAttempt(errors, stage + ".project", () => NativeRead(() => Project.CurrentProject));
        mdb = TeamDbAttempt(errors, stage + ".mdb", () => NativeRead(() => MDB.CurrentMDB));
        Project observedProject = project; MDB observedMdb = mdb;
        string projectName = ReferenceEquals(observedProject, null) ? null : TeamDbAttempt(errors, stage + ".project.name", () => NativeRead(() => observedProject.Name));
        string mdbName = ReferenceEquals(observedMdb, null) ? null : TeamDbAttempt(errors, stage + ".mdb.name", () => NativeRead(() => observedMdb.Name));
        if (string.IsNullOrWhiteSpace(projectName) || string.IsNullOrWhiteSpace(mdbName))
            TeamDatabaseIdentityManifest.Error(errors, stage + ".context", "context_unavailable", "Nonempty project and MDB names are required.");
        TeamDbIdentity ce = TeamDbAttempt(errors, stage + ".ce", () => TeamDbElementIdentity(NativeRead(() => Aveva.Core.Database.CurrentElement.Element)));
        return new Dictionary<string, object>
        {
            ["project"] = projectName, ["mdb"] = mdbName,
            ["context_id"] = projectName == null || mdbName == null ? null : DatabaseStateContext(session, projectName, mdbName),
            ["ce"] = ce?.Result(), ["ce_identity_key"] = ce?.Key
        };
    }

    private static TeamDbIdentity TeamDbElementIdentity(DbElement element)
    {
        if (ReferenceEquals(element, null) || NativeRead(() => element.IsNull) || !NativeRead(() => element.IsValid) || NativeRead(() => element.IsDeleted))
            throw new InvalidOperationException("The native element was null, invalid or deleted.");
        int[] reference = NativeRead(() => element.Ref);
        DbType storage = NativeRead(() => element.DbType);
        DbElementType type = NativeRead(() => element.GetElementType());
        if (ReferenceEquals(type, null) || !NativeRead(() => type.IsValid)) throw new InvalidOperationException("The native element type was unavailable.");
        return new TeamDbIdentity(reference, NativeRead(() => type.ShortName)?.ToUpperInvariant(), (int)storage, storage.ToString());
    }

    private static TeamDbIdentity TeamDbRecordIdentity(DbElement element, string expected)
    {
        TeamDbIdentity identity = TeamDbElementIdentity(element);
        if (identity.Type != expected || identity.DatabaseTypeCode != (int)DbType.System && identity.DatabaseTypeCode != (int)DbType.Global)
            throw new InvalidOperationException("Expected a native " + expected + " record in System/Global storage, observed " + identity.Key + ".");
        return identity;
    }

    private static string TeamDbString(DbElement element, DbAttribute attribute, List<object> errors, string path) =>
        TeamDbAttempt(errors, path, () =>
        {
            string text = null;
            if (!NativeRead(() => element.GetValidString(attribute, ref text)))
                throw new InvalidOperationException("The native string attribute was unavailable.");
            if (text == null || text.Length > TeamDatabaseIdentityManifest.MaximumTextLength)
                throw new InvalidOperationException("The native string attribute was null or overlong.");
            return text;
        });

    private static int TeamDbNumber(int number, int minimum)
    {
        if (number < minimum) throw new InvalidOperationException("The native integer identity was outside its valid range.");
        return number;
    }

    private static int? TeamDbInteger(DbElement element, DbAttribute attribute, int minimum, List<object> errors, string path) =>
        TeamDbAttempt<int?>(errors, path, () =>
        {
            int number = 0;
            if (!NativeRead(() => element.GetValidInteger(attribute, ref number)))
                throw new InvalidOperationException("The native integer attribute was unavailable.");
            return TeamDbNumber(number, minimum);
        });

    private static TeamDbRecord TeamDbReadRecord(DbElement element, int index)
    {
        string path = "databases[" + index + "]";
        var row = new TeamDbRecord { Index = index };
        row.Identity = TeamDbAttempt(row.Errors, path + ".identity", () => TeamDbRecordIdentity(element, "DB"));
        if (row.Identity == null) return row;
        row.RawName = TeamDbString(element, DbAttributeInstance.NAMEDB, row.Errors, path + ".NAMEDB");
        row.Name = TeamDbAttempt(row.Errors, path + ".canonical_name", () => TeamDatabaseIdentityManifest.Name(row.RawName, true));
        row.Number = TeamDbInteger(element, DbAttributeInstance.DBNO, 1, row.Errors, path + ".DBNO");
        row.FileNumber = TeamDbInteger(element, DbAttributeInstance.FINO, 0, row.Errors, path + ".FINO");
        row.ExtractNumber = TeamDbInteger(element, DbAttributeInstance.EXTNO, 0, row.Errors, path + ".EXTNO");
        TeamDbAttempt<object>(row.Errors, path + ".EXTOWN", () =>
        {
            DbElement owner = null;
            if (!NativeRead(() => element.GetValidRef(DbAttributeInstance.EXTOWN, ref owner)))
                throw new InvalidOperationException("EXTOWN was unavailable; a master database cannot be inferred.");
            if (!ReferenceEquals(owner, null) && !NativeRead(() => owner.IsNull)) row.ExtractOwner = TeamDbRecordIdentity(owner, "DB");
            row.ExtractOwnerRead = true;
            return null;
        });
        TeamDbAttempt<object>(row.Errors, path + ".owner_path", () =>
        {
            DbElement cursor = element;
            var seen = new HashSet<string>(StringComparer.Ordinal) { row.Identity.Key };
            foreach (string expected in new[] { "DBLI", "TEAM" })
            {
                cursor = NativeRead(() => cursor.Owner);
                TeamDbIdentity identity = TeamDbElementIdentity(cursor);
                row.OwnerPath.Add(identity);
                if (!seen.Add(identity.Key)) throw new InvalidOperationException("A typed OWNER cycle was observed.");
                if (!row.Identity.SameDatabase(identity) || identity.Type != expected)
                    throw new InvalidOperationException("Only a same-database typed DB -> DBLI -> TEAM chain is supported.");
            }
            row.OwnerChainSupported = true;
            return null;
        });
        return row;
    }
}
