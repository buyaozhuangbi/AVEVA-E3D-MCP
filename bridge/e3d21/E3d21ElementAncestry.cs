using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context : IElementAncestryContext, IElementAncestryBatchContext
{
    public object ElementAncestryBatch(string[] refnos, int maxDepth)
    {
        if (refnos == null || refnos.Length < 1 || refnos.Length > 16)
            throw new ArgumentException("refnos must contain from 1 through 16 explicit references.", "refnos");
        if (maxDepth < 1 || maxDepth > 64)
            throw new ArgumentOutOfRangeException("maxDepth", "max_depth must be from 1 through 64.");
        var requested = new List<int[]>();
        var unique = new HashSet<string>(StringComparer.Ordinal);
        foreach (string refno in refnos)
        {
            int[] pair = AncestryReference(refno);
            string canonical = ModelReference(pair);
            if (!unique.Add(canonical))
                throw new ArgumentException("refnos must contain unique canonical references.", "refnos");
            requested.Add(pair);
        }
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

        var errors = new List<object>();
        var databases = new List<Dictionary<string, object>>();
        var context = new Dictionary<string, object>
        {
            ["project_before"] = null, ["project_after"] = null,
            ["mdb_before"] = null, ["mdb_after"] = null,
            ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
            ["identity_unchanged"] = null
        };
        var results = new List<Dictionary<string, object>>();
        for (int index = 0; index < refnos.Length; index++)
            results.Add(AncestryBatchItem(index, refnos[index]));
        var result = new Dictionary<string, object>
        {
            ["success"] = false, ["complete"] = false, ["read_only"] = true,
            ["refnos"] = new List<string>(refnos), ["max_depth"] = maxDepth,
            ["results"] = results, ["mdb_databases"] = databases, ["mdb_database_count"] = null,
            ["mdb_scan_complete"] = false, ["mdb_membership_complete"] = false,
            ["world_frame"] = null, ["context"] = context, ["read_errors"] = errors,
            ["native_database_fault"] = null,
            ["consistency"] = "single_ui_dispatch_not_a_database_lock",
            ["database_identity_source"] = "DbElement.Db resolves through the current MDB by database Number; ExtractNumber is not independent evidence when that Number has multiple extracts.",
            ["scope"] = "Explicit numeric targets, bounded OWNER chains, one SDK-selected Design WORLD and one ordered current-MDB database identity scan. No PML, claims, writes or display changes."
        };
        try
        {
            OnUi(delegate
            {
                if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
                Project project = (Project)AncestryRead(() => Project.CurrentProject, errors, "context.project");
                MDB mdb = (MDB)AncestryRead(() => MDB.CurrentMDB, errors, "context.mdb");
                context["ce_before"] = AncestryRead(() => ModelCurrentReference(), errors, "context.ce_before");
                bool membershipComplete = false;
                if (project == null || mdb == null)
                    AncestryError(errors, "context", "unavailable", "An open project and MDB are required.");
                else
                {
                    context["project_before"] = AncestryRead(() => project.Name, errors, "context.project_before");
                    context["mdb_before"] = AncestryRead(() => mdb.Name, errors, "context.mdb_before");
                    membershipComplete = AncestryReadDatabases(mdb, result, databases, errors);
                    result["mdb_scan_complete"] = membershipComplete;
                    DbElement world = (DbElement)AncestryRead(() => mdb.GetFirstWorld(DbType.Design), errors, "world_frame");
                    if (world != null)
                    {
                        Dictionary<string, object> frame = AncestryElement(world, databases, membershipComplete, errors, "world_frame");
                        result["world_frame"] = frame;
                        if (!AncestryVerifiedDesignWorld(frame))
                            AncestryError(errors, "world_frame", "invalid_world", "The SDK-selected frame is not the verified WORLD of a unique Design database in this MDB.");
                    }
                    else AncestryError(errors, "world_frame", "unavailable", "The SDK returned no Design WORLD.");
                }
                for (int index = 0; index < requested.Count; index++)
                {
                    Dictionary<string, object> item = results[index];
                    if (errors.Any(error => error is Dictionary<string, object> row &&
                        ((string)row["path"]).StartsWith("context", StringComparison.Ordinal)))
                    { item["status"] = "incomplete"; continue; }
                    AncestryWalk(item, requested[index], maxDepth, databases, membershipComplete);
                    item["complete"] = Equals(item["complete"], true) && errors.Count == 0;
                    item["success"] = item["complete"];
                    if (!Equals(item["complete"], true)) item["status"] = "incomplete";
                }
                context["ce_after"] = AncestryRead(() => ModelCurrentReference(), errors, "context.ce_after");
                context["ce_unchanged"] = context["ce_before"] != null && context["ce_after"] != null ? (object)Equals(context["ce_before"], context["ce_after"]) : null;
                Project projectAfter = (Project)AncestryRead(() => Project.CurrentProject, errors, "context.project_after_object");
                MDB mdbAfter = (MDB)AncestryRead(() => MDB.CurrentMDB, errors, "context.mdb_after_object");
                if (projectAfter != null) context["project_after"] = AncestryRead(() => projectAfter.Name, errors, "context.project_after");
                if (mdbAfter != null) context["mdb_after"] = AncestryRead(() => mdbAfter.Name, errors, "context.mdb_after");
                context["identity_unchanged"] = project != null && mdb != null && projectAfter != null && mdbAfter != null &&
                    context["project_before"] != null && context["mdb_before"] != null && context["project_after"] != null && context["mdb_after"] != null
                    ? (object)(ReferenceEquals(project, projectAfter) && ReferenceEquals(mdb, mdbAfter) && Equals(context["project_before"], context["project_after"]) && Equals(context["mdb_before"], context["mdb_after"])) : null;
                if (!Equals(context["ce_unchanged"], true) || !Equals(context["identity_unchanged"], true))
                    AncestryError(errors, "context", "consistency_unverified", "Project/MDB and current element could not be verified unchanged.");
                result["mdb_membership_complete"] = Equals(result["mdb_scan_complete"], true) && Equals(context["identity_unchanged"], true);
                if (errors.Count > 0 || !Equals(result["mdb_membership_complete"], true))
                    foreach (Dictionary<string, object> item in results)
                    {
                        item["success"] = false;
                        item["complete"] = false;
                        if (Equals(item["status"], "complete")) item["status"] = "incomplete";
                    }
                if (!Equals(result["mdb_membership_complete"], true))
                {
                    foreach (Dictionary<string, object> item in results)
                        foreach (Dictionary<string, object> row in (List<Dictionary<string, object>>)item["ancestors"])
                            ((Dictionary<string, object>)row["database"])["in_current_mdb"] = null;
                    var worldFrame = result["world_frame"] as Dictionary<string, object>;
                    if (worldFrame != null)
                        ((Dictionary<string, object>)worldFrame["database"])["in_current_mdb"] = null;
                }
                result["complete"] = Equals(result["mdb_membership_complete"], true) && errors.Count == 0 && results.All(item => Equals(item["complete"], true));
                result["success"] = result["complete"];
                return result;
            });
        }
        catch (NativeDatabaseFaultException error)
        {
            AncestryError(errors, "ancestry", "native_database_fault", error.Message);
            result["native_database_fault"] = nativeFaultState == null ? null : nativeFaultState.Snapshot();
            foreach (string field in new[] { "ce_after", "ce_unchanged", "project_after", "mdb_after", "identity_unchanged" }) context[field] = null;
            result["success"] = false; result["complete"] = false; result["mdb_membership_complete"] = false;
            foreach (Dictionary<string, object> item in results)
            {
                foreach (Dictionary<string, object> row in (List<Dictionary<string, object>>)item["ancestors"])
                    ((Dictionary<string, object>)row["database"])["in_current_mdb"] = null;
                if (Equals(item["status"], "not_started")) continue;
                // Earlier target reads remain in the receipt, but a fatal
                // shared-context failure invalidates completion claims.
                item["status"] = "incomplete";
                item["success"] = false; item["complete"] = false;
                AncestryError((List<object>)item["read_errors"], "shared_context", "native_database_fault",
                    "A native database fault prevented final context verification.");
            }
            var worldFrame = result["world_frame"] as Dictionary<string, object>;
            if (worldFrame != null) ((Dictionary<string, object>)worldFrame["database"])["in_current_mdb"] = null;
        }
        return result;
    }

    private static Dictionary<string, object> AncestryBatchItem(int index, string refno)
    {
        return new Dictionary<string, object>
        {
            ["index"] = index, ["requested_refno"] = refno, ["status"] = "not_started",
            ["success"] = false, ["complete"] = false, ["target"] = null,
            ["ancestors"] = new List<Dictionary<string, object>>(), ["reached_world"] = false,
            ["truncated"] = false, ["read_errors"] = new List<object>()
        };
    }

    private static void AncestryWalk(Dictionary<string, object> item, int[] requested, int maxDepth,
        List<Dictionary<string, object>> databases, bool membershipComplete)
    {
        item["status"] = "incomplete";
        var itemErrors = (List<object>)item["read_errors"];
        DbElement current = (DbElement)AncestryRead(() => DbElement.GetElement(requested), itemErrors, "target");
        var ancestors = (List<Dictionary<string, object>>)item["ancestors"];
        var seen = new HashSet<string>(StringComparer.Ordinal);
        for (int depth = 0; depth < maxDepth; depth++)
        {
            string path = "ancestors[" + depth.ToString(CultureInfo.InvariantCulture) + "]";
            Dictionary<string, object> row = AncestryElement(current, databases, membershipComplete, itemErrors, path);
            row["depth"] = depth; ancestors.Add(row);
            if (depth == 0)
            {
                item["target"] = row;
                int[] resolved = row["raw_ref"] as int[];
                if (resolved == null || resolved.Length != 2 || resolved[0] != requested[0] || resolved[1] != requested[1])
                    AncestryError(itemErrors, "target.raw_ref", "reference_mismatch", "The SDK resolved a different raw reference than the explicit request.");
            }
            if (!Equals(row["available"], true)) break;
            string raw = row["raw_refno"] as string;
            if (raw == null) break;
            if (!seen.Add(AncestryCycleKey(row)))
            { AncestryError(itemErrors, path, "cycle", "A repeated raw reference was found in the OWNER chain."); break; }
            string typeName = row["type"] as string;
            if (typeName == "WORL" || typeName == "WORLD")
            {
                if (AncestryVerifiedDesignWorld(row)) item["reached_world"] = true;
                else AncestryError(itemErrors, path, "invalid_world", "The WORLD endpoint does not match a verified current-MDB Design database WORLD.");
                break;
            }
            if (depth + 1 == maxDepth) { item["truncated"] = true; break; }
            DbElement child = current;
            current = (DbElement)AncestryRead(() => child.Owner, itemErrors, path + ".owner");
        }
        item["complete"] = Equals(item["reached_world"], true) && !Equals(item["truncated"], true) &&
            Equals(membershipComplete, true) && itemErrors.Count == 0;
        item["success"] = item["complete"];
        item["status"] = Equals(item["complete"], true) ? "complete" : "incomplete";
    }

    public object ElementAncestry(string refno, int maxDepth)
    {
        int[] requested = AncestryReference(refno);
        if (maxDepth < 1 || maxDepth > 64)
            throw new ArgumentOutOfRangeException("maxDepth", "max_depth must be from 1 through 64.");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        var errors = new List<object>();
        var ancestors = new List<Dictionary<string, object>>();
        var databases = new List<Dictionary<string, object>>();
        var context = new Dictionary<string, object>
        {
            ["project_before"] = null, ["project_after"] = null,
            ["mdb_before"] = null, ["mdb_after"] = null,
            ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
            ["identity_unchanged"] = null
        };
        var result = new Dictionary<string, object>
        {
            ["success"] = false, ["complete"] = false, ["read_only"] = true,
            ["requested_refno"] = refno, ["max_depth"] = maxDepth,
            ["target"] = null, ["ancestors"] = ancestors, ["world_frame"] = null,
            ["mdb_databases"] = databases, ["mdb_database_count"] = null,
            ["mdb_scan_complete"] = false, ["mdb_membership_complete"] = false,
            ["reached_world"] = false, ["truncated"] = false,
            ["read_errors"] = errors, ["context"] = context, ["native_database_fault"] = null,
            ["consistency"] = "single_ui_dispatch_not_a_database_lock",
            ["database_identity_source"] = "DbElement.Db resolves through the current MDB by database Number; ExtractNumber is not independent evidence when that Number has multiple extracts.",
            ["scope"] = "Explicit numeric target, its bounded OWNER chain, the SDK-selected first Design WORLD and at most 10000 ordered current-MDB database identities. No PML, claims, writes or display changes. Raw SDK references and formatted REF are separate observations."
        };
        try
        {
            OnUi(delegate
            {
                if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
                Project project = (Project)AncestryRead(() => Project.CurrentProject, errors, "context.project");
                MDB mdb = (MDB)AncestryRead(() => MDB.CurrentMDB, errors, "context.mdb");
                context["ce_before"] = AncestryRead(() => ModelCurrentReference(), errors, "context.ce_before");
                if (project == null || mdb == null)
                    AncestryError(errors, "context", "unavailable", "An open project and MDB are required.");
                else
                {
                    context["project_before"] = AncestryRead(() => project.Name, errors, "context.project_before");
                    context["mdb_before"] = AncestryRead(() => mdb.Name, errors, "context.mdb_before");
                    bool membershipComplete = AncestryReadDatabases(mdb, result, databases, errors);
                    result["mdb_scan_complete"] = membershipComplete;
                    DbElement world = (DbElement)AncestryRead(() => mdb.GetFirstWorld(DbType.Design), errors, "world_frame");
                    if (world != null)
                    {
                        Dictionary<string, object> frame = AncestryElement(world, databases, membershipComplete, errors, "world_frame");
                        result["world_frame"] = frame;
                        if (!AncestryVerifiedDesignWorld(frame))
                            AncestryError(errors, "world_frame", "invalid_world", "The SDK-selected frame is not the verified WORLD of a unique Design database in this MDB.");
                    }
                    else AncestryError(errors, "world_frame", "unavailable", "The SDK returned no Design WORLD.");

                    var item = AncestryBatchItem(0, refno);
                    item["read_errors"] = errors;
                    item["ancestors"] = ancestors;
                    AncestryWalk(item, requested, maxDepth, databases, membershipComplete);
                    result["target"] = item["target"];
                    result["reached_world"] = item["reached_world"];
                    result["truncated"] = item["truncated"];
                }
                context["ce_after"] = AncestryRead(() => ModelCurrentReference(), errors, "context.ce_after");
                context["ce_unchanged"] = context["ce_before"] != null && context["ce_after"] != null
                    ? (object)Equals(context["ce_before"], context["ce_after"]) : null;
                Project projectAfter = (Project)AncestryRead(() => Project.CurrentProject, errors, "context.project_after_object");
                MDB mdbAfter = (MDB)AncestryRead(() => MDB.CurrentMDB, errors, "context.mdb_after_object");
                if (projectAfter != null)
                    context["project_after"] = AncestryRead(() => projectAfter.Name, errors, "context.project_after");
                if (mdbAfter != null)
                    context["mdb_after"] = AncestryRead(() => mdbAfter.Name, errors, "context.mdb_after");
                context["identity_unchanged"] = project != null && mdb != null && projectAfter != null && mdbAfter != null &&
                    context["project_before"] != null && context["mdb_before"] != null &&
                    context["project_after"] != null && context["mdb_after"] != null
                    ? (object)(ReferenceEquals(project, projectAfter) && ReferenceEquals(mdb, mdbAfter) &&
                        Equals(context["project_before"], context["project_after"]) && Equals(context["mdb_before"], context["mdb_after"])) : null;
                if (!Equals(context["ce_unchanged"], true) || !Equals(context["identity_unchanged"], true))
                    AncestryError(errors, "context", "consistency_unverified", "Project/MDB and current element could not be verified unchanged.");
                result["mdb_membership_complete"] = Equals(result["mdb_scan_complete"], true) && Equals(context["identity_unchanged"], true);
                result["complete"] = Equals(result["reached_world"], true) && !Equals(result["truncated"], true) &&
                    Equals(result["mdb_membership_complete"], true) && errors.Count == 0;
                result["success"] = result["complete"];
                return result;
            });
        }
        catch (NativeDatabaseFaultException error)
        {
            // After a latched native fault only managed evidence is touched.
            AncestryError(errors, "ancestry", "native_database_fault", error.Message);
            result["native_database_fault"] = nativeFaultState == null ? null : nativeFaultState.Snapshot();
            foreach (string field in new[] { "ce_after", "ce_unchanged", "project_after", "mdb_after", "identity_unchanged" })
                context[field] = null;
            result["success"] = false; result["complete"] = false; result["mdb_membership_complete"] = false;
        }
        if (!Equals(result["mdb_membership_complete"], true))
        {
            foreach (Dictionary<string, object> row in ancestors)
                ((Dictionary<string, object>)row["database"])["in_current_mdb"] = null;
            var world = result["world_frame"] as Dictionary<string, object>;
            if (world != null) ((Dictionary<string, object>)world["database"])["in_current_mdb"] = null;
        }
        return result;
    }

    private static int[] AncestryReference(string refno)
    {
        if (refno != null && refno.Length >= 4 && refno.Length <= 23 && refno[0] == '=')
        {
            string[] parts = refno.Substring(1).Split('/');
            int database, element;
            if (parts.Length == 2 && parts.All(part => part.Length > 0 && part.All(c => c >= '0' && c <= '9') &&
                    (part.Length == 1 || part[0] != '0')) &&
                int.TryParse(parts[0], NumberStyles.None, CultureInfo.InvariantCulture, out database) && database > 0 &&
                int.TryParse(parts[1], NumberStyles.None, CultureInfo.InvariantCulture, out element) && element >= 0)
                return new[] { database, element };
        }
        throw new ArgumentException("refno must be an explicit canonical numeric Int32 reference such as =123/0.", "refno");
    }

    private static object AncestryRead(Func<object> read, List<object> errors, string path)
    {
        try { return NativeRead(read); }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            AncestryError(errors, path, "read_failed", error.Message);
            return null;
        }
    }

    private static void AncestryError(List<object> errors, string path, string code, string message)
    {
        string text = message ?? "Unavailable native observation.";
        if (text.Length > 1024) text = text.Substring(0, char.IsHighSurrogate(text[1023]) ? 1023 : 1024);
        errors.Add(new Dictionary<string, object> { ["path"] = path, ["code"] = code, ["message"] = text });
    }

    private static string AncestryCycleKey(Dictionary<string, object> row)
    {
        var database = row["database"] as Dictionary<string, object>;
        string reference = row["raw_refno"] as string ?? "<unavailable>";
        if (database == null || !(database["number"] is int) || !(database["extract_number"] is int)) return reference;
        return ((int)database["number"]).ToString(CultureInfo.InvariantCulture) + ":" +
            ((int)database["extract_number"]).ToString(CultureInfo.InvariantCulture) + ":" + reference;
    }

    private static bool AncestryVerifiedDesignWorld(Dictionary<string, object> row)
    {
        int[] pair = row["raw_ref"] as int[];
        string type = row["type"] as string;
        var database = row["database"] as Dictionary<string, object>;
        object databaseWorld;
        int[] ownWorld = database != null && database.TryGetValue("world_raw_ref", out databaseWorld)
            ? databaseWorld as int[] : null;
        return Equals(row["available"], true) && ModelEditInput.IsReferencePair(pair) && pair[1] == 0 &&
            (type == "WORL" || type == "WORLD") && database != null &&
            Equals(database["type"], DbType.Design.ToString()) && Equals(database["in_current_mdb"], true) &&
            ModelEditInput.IsReferencePair(ownWorld) && ownWorld[0] == pair[0] && ownWorld[1] == pair[1];
    }

    private static Dictionary<string, object> AncestryDatabase(Db database, List<object> errors, string path)
    {
        var row = new Dictionary<string, object>
        {
            ["name"] = null, ["number"] = null, ["extract_number"] = null, ["type"] = null,
            ["in_current_mdb"] = null, ["matching_number_count"] = null,
            ["identity_source"] = "DbElement.Db / current MDB database Number lookup"
        };
        if (database == null) { AncestryError(errors, path, "unavailable", "The SDK database identity is unavailable."); return row; }
        row["name"] = AncestryRead(() => database.Name, errors, path + ".name");
        row["number"] = AncestryRead(() => database.Number, errors, path + ".number");
        row["extract_number"] = AncestryRead(() => database.ExtractNumber, errors, path + ".extract_number");
        row["type"] = AncestryRead(() => database.Type.ToString(), errors, path + ".type");
        if (!AncestryDatabaseComplete(row))
            AncestryError(errors, path, "incomplete_identity", "A nonempty name/type, positive Number and nonnegative ExtractNumber are required.");
        return row;
    }

    private static bool AncestryDatabaseComplete(Dictionary<string, object> row)
    {
        return row["number"] is int && (int)row["number"] > 0 &&
            row["extract_number"] is int && (int)row["extract_number"] >= 0 &&
            !string.IsNullOrWhiteSpace(row["name"] as string) && !string.IsNullOrWhiteSpace(row["type"] as string);
    }

    private static bool AncestryReadDatabases(MDB mdb, Dictionary<string, object> result,
        List<Dictionary<string, object>> databases, List<object> errors)
    {
        int before = errors.Count;
        Db[] values = (Db[])AncestryRead(() => mdb.GetDBArray(), errors, "mdb_databases");
        if (values == null) { AncestryError(errors, "mdb_databases", "unavailable", "The complete SDK MDB array is unavailable."); return false; }
        result["mdb_database_count"] = values.Length;
        if (values.Length > 10000)
        {
            AncestryError(errors, "mdb_databases", "limit", "The MDB array exceeds the 10000 database read limit; no identities were read.");
            return false;
        }
        var keys = new HashSet<string>(StringComparer.Ordinal);
        for (int index = 0; index < values.Length; index++)
        {
            var row = AncestryDatabase(values[index], errors, "mdb_databases[" + index.ToString(CultureInfo.InvariantCulture) + "]");
            row["index"] = index;
            row["identity_source"] = "MDB.GetDBArray";
            row["in_current_mdb"] = true;
            databases.Add(row);
            if (AncestryDatabaseComplete(row) && !keys.Add(row["number"] + ":" + row["extract_number"]))
                AncestryError(errors, "mdb_databases", "duplicate_identity", "Duplicate database Number/ExtractNumber pairs prevent complete membership verification.");
        }
        return errors.Count == before;
    }

    private static Dictionary<string, object> AncestryElement(DbElement element,
        List<Dictionary<string, object>> databases, bool membershipComplete, List<object> errors, string path)
    {
        var database = new Dictionary<string, object>
        {
            ["name"] = null, ["number"] = null, ["extract_number"] = null, ["type"] = null,
            ["in_current_mdb"] = null, ["matching_number_count"] = null,
            ["identity_source"] = "DbElement.Db / current MDB database Number lookup"
        };
        var row = new Dictionary<string, object>
        {
            ["available"] = false, ["raw_ref"] = null, ["raw_refno"] = null,
            ["formatted_refno"] = null, ["type"] = null, ["is_world"] = null, ["database"] = database
        };
        // Keep one SDK getter inside each fault barrier: IsValid may latch a
        // native database fault while returning true, in which case IsNull
        // must never be called.
        if (element == null || !Equals(AncestryRead(() => element.IsValid, errors, path + ".available"), true) ||
            !Equals(AncestryRead(() => element.IsNull, errors, path + ".available"), false))
        {
            AncestryError(errors, path, "unavailable", "The native element did not resolve to a valid element.");
            return row;
        }
        row["available"] = true;
        int[] pair = (int[])AncestryRead(() => element.Ref, errors, path + ".raw_ref");
        row["raw_ref"] = pair == null ? null : (int[])pair.Clone();
        if (ModelEditInput.IsReferencePair(pair)) row["raw_refno"] = ModelReference(pair);
        else AncestryError(errors, path + ".raw_ref", "invalid_reference", "The SDK did not return a valid numeric reference pair.");
        row["formatted_refno"] = AncestryRead(() => element.GetAsString(DbAttributeInstance.REF), errors, path + ".formatted_refno");
        DbElementType type = (DbElementType)AncestryRead(() => element.GetElementType(), errors, path + ".type");
        if (type != null)
        {
            row["type"] = AncestryRead(() => type.ShortName, errors, path + ".type");
            row["is_world"] = AncestryRead(() => type.IsWorld, errors, path + ".is_world");
        }
        else AncestryError(errors, path + ".type", "unavailable", "The SDK element type is unavailable.");
        Db db = (Db)AncestryRead(() => element.Db, errors, path + ".database");
        database = AncestryDatabase(db, errors, path + ".database");
        row["database"] = database;
        database["world_raw_ref"] = null; database["world_raw_refno"] = null; database["world_formatted_refno"] = null;
        if (membershipComplete && AncestryDatabaseComplete(database))
        {
            var matches = databases.Where(item => Equals(item["number"], database["number"])).ToList();
            database["matching_number_count"] = matches.Count;
            if (matches.Count > 1)
                AncestryError(errors, path + ".database", "ambiguous_number", "DbElement.Db cannot independently identify an extract when its Number occurs more than once in the current MDB.");
            else
            {
                database["in_current_mdb"] = matches.Count == 1 && new[] { "name", "extract_number", "type" }
                    .All(field => Equals(matches[0][field], database[field]));
                if (!Equals(database["in_current_mdb"], true))
                    AncestryError(errors, path + ".database", "not_in_mdb", "The element database identity is not a unique exact member of the current MDB.");
            }
        }
        if (db != null)
        {
            DbElement world = (DbElement)AncestryRead(() => db.World, errors, path + ".database.world");
            if (world != null)
            {
                int[] raw = (int[])AncestryRead(() => world.Ref, errors, path + ".database.world.raw_ref");
                database["world_raw_ref"] = raw == null ? null : (int[])raw.Clone();
                if (ModelEditInput.IsReferencePair(raw)) database["world_raw_refno"] = ModelReference(raw);
                else AncestryError(errors, path + ".database.world", "invalid_reference", "The database WORLD has no valid raw reference.");
                database["world_formatted_refno"] = AncestryRead(() => world.GetAsString(DbAttributeInstance.REF), errors, path + ".database.world.formatted_refno");
            }
            else AncestryError(errors, path + ".database.world", "unavailable", "The SDK database WORLD is unavailable.");
        }
        return row;
    }
}
