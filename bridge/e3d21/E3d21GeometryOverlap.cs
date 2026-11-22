using System;
using System.Collections.Generic;
using System.Linq;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    public object CheckGeometryRelation(string testRefno, string againstRefno, bool includeBox)
    {
        string testTarget = ModelEditInput.ExplicitReference(testRefno, "test_refno");
        string againstTarget = ModelEditInput.ExplicitReference(againstRefno, "against_refno");
        if (IsCommandActive())
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

        return OnUi(delegate
        {
            if (IsCommandActive())
                throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

            string ceBefore = ModelCurrentReference();
            DbElement test = ModelRequireElement(testTarget);
            DbElement against = ModelRequireElement(againstTarget);
            if (NativeRead(() => test.DbType) != DbType.Design || NativeRead(() => against.DbType) != DbType.Design)
                throw new InvalidOperationException("Both references must identify elements in Design databases.");

            var exact = GeometryRelationExact(test, against);
            var box = GeometryRelationBox(test, against, includeBox);

            var result = new Dictionary<string, object>
            {
                ["success"] = true,
                ["complete"] = false,
                ["partial"] = false,
                ["test"] = ModelIdentity(test),
                ["against"] = ModelIdentity(against),
                ["exact"] = exact,
                ["box"] = box,
                ["ce_before"] = ceBefore,
                ["ce_after"] = null,
                ["ce_unchanged"] = null,
                ["source"] = "E3D SDK Spatial.ElementInElementExact(test, against, false)" +
                    (includeBox ? " and Spatial.ElementInElementBox(test, against)" : ""),
                ["measurement_scope"] = "Raw SDK boolean results for this ordered pair only. exact.b_fully is explicitly false; its boolean is not interpreted here. box.available=true means the native box-volume predicate ran. A false value is only that predicate's result and is not a clash/clear, clearance, or engineering conclusion."
            };

            ModelCaptureCeAfter(result);
            bool exactAvailable = Equals(exact["available"], true);
            bool boxAvailable = !includeBox || Equals(box["available"], true);
            result["complete"] = exactAvailable && boxAvailable && Equals(result["ce_unchanged"], true);
            result["partial"] = !Equals(result["complete"], true);
            return result;
        });
    }

    public object CheckGeometryRelationBatch(IList<string[]> pairs, bool includeBox)
    {
        if (pairs == null || pairs.Count < 1 || pairs.Count > 32)
            throw new ArgumentException("pairs must contain between 1 and 32 ordered explicit reference pairs.");

        var requested = new List<string[]>(pairs.Count);
        for (int index = 0; index < pairs.Count; index++)
        {
            string[] pair = pairs[index];
            if (pair == null || pair.Length != 2)
                throw new ArgumentException("Each pairs entry must contain exactly test_refno and against_refno.");
            requested.Add(new[]
            {
                ModelEditInput.ExplicitReference(pair[0], "pairs[" + index + "].test_refno"),
                ModelEditInput.ExplicitReference(pair[1], "pairs[" + index + "].against_refno")
            });
        }

        if (IsCommandActive())
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

        var rows = new List<Dictionary<string, object>>(requested.Count);
        int activeRowIndex = -1;
        string faultPath = "ce_before";
        string activePredicate = null;
        var errors = new List<object>();
        var report = new Dictionary<string, object>
        {
            ["success"] = false,
            ["complete"] = false,
            ["partial"] = true,
            ["single_dispatch"] = true,
            ["consistency"] = "single_ui_dispatch",
            ["consistency_scope"] = "All ordered pairs are evaluated in one UI callback; this is not a database lock or immutable snapshot.",
            ["requested_pair_count"] = requested.Count,
            ["include_box"] = includeBox,
            ["rows"] = rows,
            ["failed_pair_count"] = requested.Count,
            ["complete_pair_count"] = 0,
            ["errors"] = errors,
            ["ce_before"] = null,
            ["ce_after"] = null,
            ["ce_unchanged"] = null,
            ["native_database_fault"] = null,
            ["source"] = "E3D SDK Spatial.ElementInElementExact(test, against, false)" +
                (includeBox ? " and Spatial.ElementInElementBox(test, against)" : ""),
            ["measurement_scope"] = "Raw SDK booleans for each requested ordered pair only. exact.b_fully is fixed false. False is not a clash/clear, clearance, or engineering conclusion; omitted pairs are outside scope."
        };

        foreach (string[] pair in requested)
        {
            rows.Add(new Dictionary<string, object>
            {
                ["requested_test_refno"] = pair[0],
                ["requested_against_refno"] = pair[1],
                ["test"] = null,
                ["against"] = null,
                ["success"] = false,
                ["complete"] = false,
                ["partial"] = true,
                ["exact"] = GeometryRelationPending(true, true),
                ["box"] = GeometryRelationPending(false, includeBox),
                ["errors"] = new List<object>()
            });
        }

        try
        {
            OnUi(delegate
            {
                if (IsCommandActive())
                    throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

                report["ce_before"] = ModelCurrentReference();
                for (int index = 0; index < requested.Count; index++)
                {
                    activeRowIndex = index;
                    activePredicate = null;
                    faultPath = "rows[" + index + "]";
                    ThrowIfNativeFaulted();
                    Dictionary<string, object> row = rows[index];
                    List<object> rowErrors = (List<object>)row["errors"];
                    try
                    {
                        DbElement test = ModelRequireElement(requested[index][0]);
                        DbElement against = ModelRequireElement(requested[index][1]);
                        if (NativeRead(() => test.DbType) != DbType.Design ||
                            NativeRead(() => against.DbType) != DbType.Design)
                            throw new InvalidOperationException("Both references must identify elements in Design databases.");

                        row["test"] = ModelIdentity(test);
                        row["against"] = ModelIdentity(against);
                        activePredicate = "exact";
                        row["exact"] = GeometryRelationExact(test, against);
                        if (includeBox)
                        {
                            activePredicate = "box";
                            row["box"] = GeometryRelationBox(test, against, true);
                        }
                        activePredicate = null;
                        row["success"] = true;
                    }
                    catch (Exception ex)
                    {
                        ThrowIfNativeFaulted();
                        var error = new Dictionary<string, object>
                        {
                            ["path"] = faultPath,
                            ["code"] = "pair_read_failed",
                            ["message"] = ex.Message
                        };
                        rowErrors.Add(error);
                        errors.Add(error);
                    }
                }
                activeRowIndex = -1;
                faultPath = "ce_after";
                ModelCaptureCeAfter(report);
                faultPath = "dispatch.after";
                return true;
            });
        }
        catch (NativeDatabaseFaultException ex)
        {
            var error = new Dictionary<string, object>
            {
                ["path"] = activePredicate == null ? faultPath : faultPath + "." + activePredicate,
                ["code"] = "native_database_fault",
                ["message"] = ex.Message
            };
            errors.Add(error);
            if (activeRowIndex >= 0)
            {
                var row = rows[activeRowIndex];
                ((List<object>)row["errors"]).Add(error);
                if (activePredicate != null)
                {
                    var predicate = (Dictionary<string, object>)row[activePredicate];
                    predicate["status"] = "read_failed";
                    predicate["error"] = ex.Message;
                }
            }
            report["ce_after"] = null;
            report["ce_unchanged"] = null;
            report["native_database_fault"] = nativeFaultState == null ? null : nativeFaultState.Snapshot();
        }

        bool ceUnchanged = Equals(report["ce_unchanged"], true);
        foreach (var row in rows)
        {
            var exact = (Dictionary<string, object>)row["exact"];
            var box = (Dictionary<string, object>)row["box"];
            bool complete = ceUnchanged && Equals(row["success"], true) &&
                Equals(exact["available"], true) && (!includeBox || Equals(box["available"], true));
            row["complete"] = complete;
            row["partial"] = !complete;
        }
        int failed = rows.Count(row => !Equals(row["success"], true));
        int completeCount = rows.Count(row => Equals(row["complete"], true));
        report["failed_pair_count"] = failed;
        report["complete_pair_count"] = completeCount;
        report["success"] = failed == 0 && errors.Count == 0;
        report["complete"] = completeCount == rows.Count;
        report["partial"] = completeCount != rows.Count;
        return report;
    }

    private static Dictionary<string, object> GeometryRelationExact(DbElement test, DbElement against)
    {
        var result = GeometryRelationRead(() =>
        {
            Spatial spatial = NativeRead(() => Spatial.Instance);
            if (spatial == null) throw new InvalidOperationException("The E3D spatial query service is unavailable.");
            return NativeRead(() => spatial.ElementInElementExact(test, against, false));
        });
        result["predicate"] = "Spatial.ElementInElementExact(test, against, false)";
        result["b_fully"] = false;
        return result;
    }

    private static Dictionary<string, object> GeometryRelationBox(DbElement test, DbElement against, bool requested)
    {
        if (!requested) return GeometryRelationPending(false, false);
        var result = GeometryRelationRead(() =>
        {
            Spatial spatial = NativeRead(() => Spatial.Instance);
            if (spatial == null) throw new InvalidOperationException("The E3D spatial query service is unavailable.");
            return NativeRead(() => spatial.ElementInElementBox(test, against));
        });
        result["predicate"] = "Spatial.ElementInElementBox(test, against)";
        return result;
    }

    private static Dictionary<string, object> GeometryRelationPending(bool exact, bool requested)
    {
        var result = new Dictionary<string, object>
        {
            ["available"] = false, ["value"] = null, ["error"] = null,
            ["status"] = requested ? "not_attempted" : "not_requested",
            ["predicate"] = exact ? "Spatial.ElementInElementExact(test, against, false)" :
                requested ? "Spatial.ElementInElementBox(test, against)" : null
        };
        if (exact) result["b_fully"] = false;
        return result;
    }

    private static Dictionary<string, object> GeometryRelationRead(Func<bool> read)
    {
        try
        {
            return new Dictionary<string, object>
            {
                ["available"] = true,
                ["value"] = read(),
                ["error"] = null
            };
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            return new Dictionary<string, object>
            {
                ["available"] = false,
                ["value"] = null,
                ["error"] = ex.Message
            };
        }
    }
}
