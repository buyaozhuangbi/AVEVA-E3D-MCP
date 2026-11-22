using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    public object GetMeasurementsBatch(IList<string> refnos, IList<string> measurements, string wrt, string volumeScope)
    {
        if (refnos == null || refnos.Count < 1 || refnos.Count > 32)
            throw new ArgumentException("refnos must contain between 1 and 32 explicit references.");
        List<string> requested = refnos.Select(value => ModelEditInput.ExplicitReference(value, "refnos")).ToList();
        if (requested.Distinct(StringComparer.OrdinalIgnoreCase).Count() != requested.Count)
            throw new ArgumentException("refnos must not contain duplicate references.");
        if (measurements == null || measurements.Count < 1 || measurements.Count > 3 ||
            measurements.Any(value => value != "spatial" && value != "bounds" && value != "volume") ||
            measurements.Distinct(StringComparer.Ordinal).Count() != measurements.Count)
            throw new ArgumentException("measurements must contain 1 to 3 unique names: spatial, bounds, volume.");
        List<string> names = measurements.ToList();
        string frame = (wrt ?? "WORLD").Trim();
        if (frame.Equals("WORLD", StringComparison.OrdinalIgnoreCase) || frame.Equals("OWNER", StringComparison.OrdinalIgnoreCase))
            frame = frame.ToUpperInvariant();
        else frame = ModelEditInput.ExplicitReference(frame, "wrt");
        if (volumeScope != "element" && volumeScope != "members")
            throw new ArgumentException("volume_scope must be element or members.");
        if (IsCommandActive())
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

        var errors = new List<object>();
        var rows = new List<object>();
        foreach (string requestedRefno in requested)
        {
            var fields = new Dictionary<string, object>(StringComparer.Ordinal);
            foreach (string name in names)
                fields[name] = new Dictionary<string, object>
                {
                    ["status"] = "not_attempted", ["success"] = false, ["complete"] = false,
                    ["result"] = null, ["errors"] = new List<object>()
                };
            rows.Add(new Dictionary<string, object>
            {
                ["requested_refno"] = requestedRefno, ["refno"] = null, ["element"] = null,
                ["status"] = "not_attempted", ["success"] = false, ["complete"] = false,
                ["measurements"] = fields, ["errors"] = new List<object>()
            });
        }
        var report = new Dictionary<string, object>
        {
            ["success"] = false, ["complete"] = false, ["single_dispatch"] = true,
            ["consistency"] = "single_ui_dispatch",
            ["consistency_scope"] = "Requested measurements run in one UI callback; this is not a database lock or immutable snapshot.",
            ["requested_refnos"] = requested, ["measurements"] = names, ["wrt"] = frame,
            ["volume_scope"] = volumeScope, ["row_count"] = rows.Count, ["failed_row_count"] = rows.Count,
            ["rows"] = rows, ["read_errors"] = errors,
            ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
            ["ce_type_before"] = null, ["ce_type_after"] = null,
            ["project_before"] = null, ["project_after"] = null, ["mdb_before"] = null, ["mdb_after"] = null,
            ["identity_unchanged"] = null, ["native_database_fault"] = null
        };
        Dictionary<string, object> activeRow = null;
        Dictionary<string, object> activeField = null;
        string activePath = "batch";
        try
        {
            OnUi(delegate
            {
                if (IsCommandActive())
                    throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
                MeasurementReadTop(report, "ce_before", errors, () => ModelCurrentReference());
                Project projectBefore = null;
                MDB mdbBefore = null;
                Dictionary<string, object> endpointContextBefore = null;
                MeasurementReadTop(report, "project_before", errors, () =>
                {
                    projectBefore = NativeRead(() => Project.CurrentProject);
                    if (projectBefore == null) throw new InvalidOperationException("No project is open.");
                    return NativeRead(() => projectBefore.Name);
                });
                MeasurementReadTop(report, "mdb_before", errors, () =>
                {
                    mdbBefore = NativeRead(() => MDB.CurrentMDB);
                    if (mdbBefore == null) throw new InvalidOperationException("No MDB is open.");
                    return NativeRead(() => mdbBefore.Name);
                });
                if (projectBefore != null && mdbBefore != null)
                {
                    endpointContextBefore = ReadEndpointContext(projectBefore, mdbBefore);
                    report["ce_type_before"] = endpointContextBefore["current_element_type"];
                }
                for (int index = 0; index < rows.Count; index++)
                {
                    activeRow = (Dictionary<string, object>)rows[index];
                    activeField = null;
                    activePath = "rows[" + index + "]";
                    activeRow["status"] = "reading";
                    var rowErrors = (List<object>)activeRow["errors"];
                    DbElement element;
                    try
                    {
                        element = NativeRead(() => ModelRequireElement((string)activeRow["requested_refno"]));
                        var identity = MeasurementElementIdentity(element);
                        activeRow["element"] = identity;
                        activeRow["refno"] = identity["refno"];
                    }
                    catch (Exception ex)
                    {
                        ThrowIfNativeFaulted();
                        MeasurementError(errors, rowErrors, null, activePath + ".element", "element_read_failed", ex.Message);
                        activeRow["status"] = "failed";
                        continue;
                    }
                    var fields = (Dictionary<string, object>)activeRow["measurements"];
                    foreach (string name in names)
                    {
                        activeField = (Dictionary<string, object>)fields[name];
                        activeField["status"] = "reading";
                        activePath = "rows[" + index + "].measurements." + name;
                        var fieldErrors = (List<object>)activeField["errors"];
                        try
                        {
                            Dictionary<string, object> payload;
                            if (name == "spatial") payload = ReadSpatialInfoOnUi(element, frame);
                            else if (name == "bounds") payload = ReadGeometryBoundsOnUi(element);
                            else payload = ReadGeometryVolumeOnUi(element, volumeScope);
                            ThrowIfNativeFaulted();
                            activeField["result"] = payload;
                            MeasurementPayloadErrors(payload, name, activePath, errors, rowErrors, fieldErrors);
                            bool successful = Equals(payload["success"], true) && fieldErrors.Count == 0;
                            if (!successful && fieldErrors.Count == 0)
                                MeasurementError(errors, rowErrors, fieldErrors, activePath, "measurement_failed", "The native measurement did not succeed.");
                            activeField["success"] = successful;
                            activeField["complete"] = successful && MeasurementPayloadComplete(payload, name);
                            activeField["status"] = successful ? "returned" : "failed";
                        }
                        catch (Exception ex)
                        {
                            ThrowIfNativeFaulted();
                            MeasurementError(errors, rowErrors, fieldErrors, activePath, "measurement_read_failed", ex.Message);
                            activeField["status"] = "failed";
                        }
                    }
                    activeRow["success"] = rowErrors.Count == 0 && fields.Values.Cast<Dictionary<string, object>>().All(value => Equals(value["success"], true));
                    activeRow["complete"] = Equals(activeRow["success"], true) && fields.Values.Cast<Dictionary<string, object>>().All(value => Equals(value["complete"], true));
                    activeRow["status"] = Equals(activeRow["success"], true) ? "returned" : "failed";
                    activeField = null;
                }
                activeRow = null;
                activePath = "batch.after";
                MeasurementReadTop(report, "ce_after", errors, () => ModelCurrentReference());
                ModelCeUnchanged(report);
                Project projectAfter = null;
                MDB mdbAfter = null;
                MeasurementReadTop(report, "project_after", errors, () =>
                {
                    projectAfter = NativeRead(() => Project.CurrentProject);
                    if (projectAfter == null) throw new InvalidOperationException("No project is open.");
                    return NativeRead(() => projectAfter.Name);
                });
                MeasurementReadTop(report, "mdb_after", errors, () =>
                {
                    mdbAfter = NativeRead(() => MDB.CurrentMDB);
                    if (mdbAfter == null) throw new InvalidOperationException("No MDB is open.");
                    return NativeRead(() => mdbAfter.Name);
                });
                if (endpointContextBefore != null && projectAfter != null && mdbAfter != null)
                {
                    Dictionary<string, object> endpointContextAfter = ReadEndpointContext(projectAfter, mdbAfter);
                    report["ce_type_after"] = endpointContextAfter["current_element_type"];
                    bool? typedCeUnchanged = EndpointContextEqual(endpointContextBefore, endpointContextAfter);
                    bool? referenceCeUnchanged = report["ce_unchanged"] is bool referenceAgreement
                        ? referenceAgreement : (bool?)null;
                    report["ce_unchanged"] = referenceCeUnchanged == false || typedCeUnchanged == false ? false :
                        referenceCeUnchanged == true && typedCeUnchanged == true ? true : (bool?)null;
                    foreach (Dictionary<string, object> row in rows)
                    {
                        Dictionary<string, object> fields = (Dictionary<string, object>)row["measurements"];
                        object spatialValue;
                        Dictionary<string, object> spatial = fields.TryGetValue("spatial", out spatialValue)
                            ? spatialValue as Dictionary<string, object> : null;
                        if (spatial != null && spatial["result"] is Dictionary<string, object> spatialResult)
                            ApplyEndpointContext(spatialResult, projectBefore, mdbBefore, endpointContextBefore,
                                projectAfter, mdbAfter, endpointContextAfter);
                    }
                }
                else if (!Equals(report["ce_unchanged"], false))
                    report["ce_unchanged"] = null;
                if (!Equals(report["ce_unchanged"], true))
                    MeasurementError(errors, null, null, "ce_unchanged", "consistency_unverified", "Current element reference and type could not be verified unchanged.");
                report["identity_unchanged"] = projectBefore != null && mdbBefore != null &&
                    ReferenceEquals(projectBefore, projectAfter) && ReferenceEquals(mdbBefore, mdbAfter) &&
                    report["project_before"] != null && report["mdb_before"] != null &&
                    Equals(report["project_before"], report["project_after"]) && Equals(report["mdb_before"], report["mdb_after"]);
                if (!Equals(report["identity_unchanged"], true))
                    MeasurementError(errors, null, null, "identity_unchanged", "consistency_unverified", "Project/MDB identity could not be verified unchanged.");
                report["success"] = errors.Count == 0 && rows.Cast<Dictionary<string, object>>().All(row => Equals(row["success"], true));
                report["complete"] = Equals(report["success"], true) && rows.Cast<Dictionary<string, object>>().All(row => Equals(row["complete"], true));
                return report;
            });
        }
        catch (NativeDatabaseFaultException ex)
        {
            // This branch is managed-only. OnUi can throw after the callback
            // returned, so the report lives outside the dispatcher scope.
            var rowErrors = activeRow == null ? null : (List<object>)activeRow["errors"];
            var fieldErrors = activeField == null ? null : (List<object>)activeField["errors"];
            MeasurementError(errors, rowErrors, fieldErrors, activePath, "native_database_fault", ex.Message);
            if (activeRow != null) { activeRow["status"] = "failed"; activeRow["success"] = false; activeRow["complete"] = false; }
            if (activeField != null) { activeField["status"] = "failed"; activeField["success"] = false; activeField["complete"] = false; }
            report["success"] = false;
            report["complete"] = false;
            report["ce_after"] = null;
            report["ce_unchanged"] = null;
            report["ce_type_after"] = null;
            report["project_after"] = null;
            report["mdb_after"] = null;
            report["identity_unchanged"] = null;
            report["native_database_fault"] = nativeFaultState == null ? null : nativeFaultState.Snapshot();
        }
        report["failed_row_count"] = rows.Cast<Dictionary<string, object>>().Count(row => !Equals(row["success"], true));
        return report;
    }

    private static Dictionary<string, object> MeasurementElementIdentity(DbElement element)
    {
        int[] reference = NativeRead(() => element.Ref);
        var type = NativeRead(() => element.GetElementType());
        string typeName = NativeRead(() => type.Name);
        bool hasName = NativeRead(() => element.IsAttributeValid(DbAttributeInstance.NAME));
        return new Dictionary<string, object>
        {
            ["refno"] = ModelReference(reference), ["type"] = typeName,
            ["name"] = hasName ? NativeRead(() => element.GetString(DbAttributeInstance.NAME)) : null
        };
    }

    private static void MeasurementReadTop(Dictionary<string, object> report, string field, List<object> errors, Func<object> read)
    {
        try { report[field] = NativeRead(read); }
        catch (Exception ex) { ThrowIfNativeFaulted(); MeasurementError(errors, null, null, field, "read_failed", ex.Message); }
    }

    private static void MeasurementError(List<object> errors, List<object> rowErrors, List<object> fieldErrors,
        string path, string code, string message)
    {
        var error = new Dictionary<string, object> { ["path"] = path, ["code"] = code, ["message"] = message };
        errors.Add(error);
        if (rowErrors != null) rowErrors.Add(error);
        if (fieldErrors != null) fieldErrors.Add(error);
    }

    private static void MeasurementPayloadErrors(Dictionary<string, object> payload, string name, string path,
        List<object> errors, List<object> rowErrors, List<object> fieldErrors)
    {
        foreach (string key in new[] { "errors", "read_errors", "native_attribute_errors" })
        {
            object value;
            if (!payload.TryGetValue(key, out value) || !(value is IDictionary)) continue;
            foreach (DictionaryEntry item in (IDictionary)value)
                MeasurementError(errors, rowErrors, fieldErrors, path + "." + key + "." + item.Key,
                    "measurement_diagnostic", Convert.ToString(item.Value));
        }
        if (name != "volume") return;
        foreach (string key in new[] { "gross", "net" })
        {
            var value = (Dictionary<string, object>)payload[key];
            object error;
            if (value.TryGetValue("raw_text_error", out error))
                MeasurementError(errors, rowErrors, fieldErrors, path + "." + key + ".raw_text",
                    "read_failed", Convert.ToString(error));
        }
    }

    private static bool MeasurementPayloadComplete(Dictionary<string, object> payload, string name)
    {
        if (name == "spatial") return Equals(payload["complete"], true);
        if (name == "bounds") return Equals(payload["available"], true) && !Equals(payload["partial"], true);
        return Equals(payload["read_complete"], true) && Equals(payload["ce_unchanged"], true) &&
            Equals(((Dictionary<string, object>)payload["gross"])["available"], true) &&
            Equals(((Dictionary<string, object>)payload["net"])["available"], true);
    }
}
