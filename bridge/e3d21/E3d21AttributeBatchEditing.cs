using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private readonly string attributeBatchRevisionSession = Guid.NewGuid().ToString("N");

    private sealed class AttributeBatchItem
    {
        internal string Name;
        internal string InputValue;
        internal string OldValue;
        internal string OldTypedValue;
        internal double? OldNumericValue;
        internal object ParsedValue;
        internal DbAttribute Attribute;
    }

    private sealed class AttributeBatchState
    {
        internal DbElement Element;
        internal string RequestedRefno, Refno, Type, Name, Owner, Project, Mdb, Ce;
        internal List<AttributeBatchItem> Items;
    }

    public object SetAttributes(string refno, IDictionary<string, string> values, string expectedRevision, bool dryRun)
    {
        string requested = ModelEditInput.ExplicitReference(refno, "refno");
        List<KeyValuePair<string, string>> inputs = ValidateAttributeBatchInputs(values);
        string expected = expectedRevision == null ? null : expectedRevision.ToLowerInvariant();
        if (expected != null && (expected.Length != 64 || expected.Any(c => !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f'))))
            throw new ArgumentException("expected_revision must contain exactly 64 hexadecimal characters.", "expectedRevision");
        if (!dryRun && expected == null)
            throw new ArgumentException("Execution requires expected_revision from a current batch attribute preview.", "expectedRevision");

        var fields = new List<Dictionary<string, object>>();
        var errors = new List<object>();
        var result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
        {
            ["success"] = false, ["dry_run"] = dryRun, ["requested_refno"] = requested, ["refno"] = null,
            ["revision"] = null, ["revision_conflict"] = false, ["preflight_complete"] = false,
            ["write_attempted"] = false, ["write_applied"] = false, ["partial_write"] = false,
            ["outcome_uncertain"] = false, ["readback_verified"] = false,
            ["no_change"] = false,
            ["context_unchanged"] = null, ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
            ["attributes"] = fields, ["errors"] = errors,
            ["scope"] = "One explicitly referenced element and 1-20 whitelisted scalar attributes, preflighted before ordered direct SDK setters and individually read back. This is not an atomic database transaction.",
            ["revision_scope"] = "Bridge context instance, literal and resolved reference, type/name/owner, project/MDB/current element, canonical attribute names, typed requested values and complete native old values.",
            ["failure_policy"] = "No automatic retry or rollback. A later field failure may leave earlier attributes applied; inspect per-field status and current values before deciding what to do next."
        };

        bool acquired = false;
        string phase = "preflight";
        AttributeBatchState before = null;
        try
        {
            acquired = TryBeginCommand("database-set-attributes " + requested);
            if (!acquired) return BusyResult("database-set-attributes " + requested);
            OnUi(delegate
            {
                before = AttributeBatchReadState(requested, inputs);
                string revision = AttributeBatchRevision(before);
                result["refno"] = before.Refno;
                result["element"] = new { refno = before.Refno, type = before.Type, name = before.Name, owner_refno = before.Owner };
                result["revision"] = revision;
                result["ce_before"] = before.Ce;
                result["preflight_complete"] = true;
                foreach (AttributeBatchItem item in before.Items)
                    fields.Add(AttributeBatchField(item, "planned", false, false, null, null));
                result["no_change"] = before.Items.All(AttributeBatchAlreadyMatches);

                if (expected != null && expected != revision)
                {
                    result["revision_conflict"] = true;
                    errors.Add(new { phase = "preflight", error = "expected_revision no longer matches the requested values and complete observed context. No attributes were written." });
                    return true;
                }
                if (dryRun)
                {
                    result["success"] = true;
                    result["ce_after"] = before.Ce;
                    result["ce_unchanged"] = true;
                    result["context_unchanged"] = true;
                    result["readback_verified"] = false;
                    return true;
                }

                phase = "revalidation";
                AttributeBatchState immediate = AttributeBatchReadState(requested, inputs);
                if (AttributeBatchRevision(immediate) != revision)
                {
                    result["revision_conflict"] = true;
                    errors.Add(new { phase, error = "Target, requested values, old values or project/MDB/current element changed during preflight. No attributes were written." });
                    return true;
                }
                before = immediate;

                bool anyApplied = false;
                bool anyUncertain = false;
                bool allVerified = true;
                for (int index = 0; index < before.Items.Count; index++)
                {
                    AttributeBatchItem item = before.Items[index];
                    Dictionary<string, object> field = fields[index];
                    phase = "revalidation:" + item.Name;
                    string observedName;
                    string drift;
                    if (!AttributeBatchStillCurrent(before, index, fields, out observedName, out drift))
                    {
                        result["revision_conflict"] = true;
                        result["context_unchanged"] = false;
                        errors.Add(new { phase, error = drift });
                        allVerified = false;
                        anyUncertain = true;
                        break;
                    }
                    if (AttributeBatchAlreadyMatches(item))
                    {
                        field["status"] = "no_change";
                        field["actual_value"] = item.OldValue;
                        if (item.OldTypedValue != null) field["actual_typed_value"] = item.OldTypedValue;
                        if (item.OldNumericValue.HasValue) field["actual_numeric_value"] = item.OldNumericValue.Value;
                        field["write_applied"] = false;
                        field["readback_verified"] = true;
                        continue;
                    }
                    phase = "write:" + item.Name;
                    result["write_attempted"] = true;
                    field["status"] = "write_attempted";
                    field["write_attempted"] = true;
                    field["write_applied"] = null;
                    try
                    {
                        if (item.ParsedValue is string)
                            NativeWrite(() => before.Element.SetAttribute(item.Attribute, (string)item.ParsedValue));
                        else
                            NativeWrite(() => before.Element.SetAttribute(item.Attribute, (double)item.ParsedValue));
                        field["write_applied"] = true;
                        anyApplied = true;
                        string readback = NativeRead(() => before.Element.GetAsString(item.Attribute));
                        field["actual_value"] = readback;
                        bool matches;
                        if (item.ParsedValue is string)
                        {
                            string typedReadback = NativeRead(() => before.Element.GetString(item.Attribute));
                            field["actual_typed_value"] = typedReadback;
                            matches = AttributeBatchValueMatches(item.Name, item.ParsedValue, typedReadback);
                        }
                        else
                        {
                            double actualNumber = NativeRead(() => before.Element.GetDouble(item.Attribute));
                            field["actual_numeric_value"] = actualNumber;
                            matches = AttributeBatchNumberMatches((double)item.ParsedValue, actualNumber);
                        }
                        field["readback_verified"] = matches;
                        field["status"] = matches ? "verified" : "readback_mismatch";
                        if (!matches)
                        {
                            field["error"] = "The native readback does not match the requested value.";
                            errors.Add(new { phase, error = "Attribute readback did not match the requested value." });
                            allVerified = false;
                            break;
                        }
                    }
                    catch (Exception error)
                    {
                        ThrowIfNativeFaulted();
                        field["error"] = error.Message;
                        allVerified = false;
                        string actual = null;
                        try
                        {
                            actual = NativeRead(() => before.Element.GetAsString(item.Attribute));
                            field["actual_value"] = actual;
                            bool matches;
                            if (item.ParsedValue is string)
                            {
                                string typedActual = NativeRead(() => before.Element.GetString(item.Attribute));
                                field["actual_typed_value"] = typedActual;
                                matches = AttributeBatchValueMatches(item.Name, item.ParsedValue, typedActual);
                            }
                            else
                            {
                                double actualNumber = NativeRead(() => before.Element.GetDouble(item.Attribute));
                                field["actual_numeric_value"] = actualNumber;
                                matches = AttributeBatchNumberMatches((double)item.ParsedValue, actualNumber);
                            }
                            if (matches)
                            {
                                field["write_applied"] = true;
                                field["status"] = "applied_unverified";
                                anyApplied = true;
                            }
                            else if (item.ParsedValue is string
                                ? AttributeBatchObservedMatchesOld(item, (string)field["actual_typed_value"])
                                : AttributeBatchNumberMatches(item.OldNumericValue.Value, (double)field["actual_numeric_value"]))
                            {
                                field["write_applied"] = false;
                                field["status"] = "not_applied";
                            }
                            else
                            {
                                field["write_applied"] = null;
                                field["status"] = "residual_observed";
                                field["outcome_uncertain"] = true;
                                anyUncertain = true;
                            }
                        }
                        catch (Exception readError)
                        {
                            ThrowIfNativeFaulted();
                            field["write_applied"] = null;
                            field["status"] = "outcome_unknown";
                            field["residual_read_error"] = readError.Message;
                            anyUncertain = true;
                        }
                        errors.Add(new { phase, error = error.Message });
                        break;
                    }
                }
                foreach (Dictionary<string, object> field in fields)
                    if (Equals(field["status"], "planned")) field["status"] = "not_attempted";

                phase = "context_readback";
                try
                {
                    AttributeBatchState after = AttributeBatchReadResolvedIdentity(before);
                    bool same = AttributeBatchSameIdentity(before, after, fields);
                    result["context_unchanged"] = same;
                    result["ce_after"] = after.Ce;
                    result["ce_unchanged"] = string.Equals(before.Ce, after.Ce, StringComparison.Ordinal);
                    if (!same)
                    {
                        errors.Add(new { phase, error = "Project/MDB, CE or target identity changed during the batch operation." });
                        allVerified = false;
                        anyUncertain = true;
                    }
                }
                catch (Exception error)
                {
                    ThrowIfNativeFaulted();
                    result["context_unchanged"] = null;
                    result["ce_after"] = null;
                    result["ce_unchanged"] = null;
                    errors.Add(new { phase, error = error.Message });
                    allVerified = false;
                    anyUncertain = true;
                }

                result["write_applied"] = anyApplied ? (object)true : anyUncertain ? null : false;
                result["partial_write"] = anyApplied && !allVerified;
                result["outcome_uncertain"] = anyUncertain;
                result["readback_verified"] = allVerified && fields.All(field => Equals(field["readback_verified"], true));
                result["success"] = allVerified && Equals(result["readback_verified"], true);
                return true;
            });
        }
        catch (Exception error)
        {
            bool writeAttempted = Equals(result["write_attempted"], true);
            bool anyApplied = fields.Any(field => Equals(field["write_applied"], true));
            foreach (Dictionary<string, object> field in fields)
            {
                if (Equals(field["status"], "write_attempted"))
                {
                    field["status"] = "outcome_unknown";
                    field["write_applied"] = null;
                    field["outcome_uncertain"] = true;
                }
                else if (Equals(field["status"], "planned"))
                    field["status"] = "not_attempted";
            }
            result["success"] = false;
            result["outcome_uncertain"] = writeAttempted;
            result["write_applied"] = anyApplied ? (object)true : writeAttempted ? null : false;
            result["partial_write"] = writeAttempted && anyApplied;
            errors.Add(new { phase, error = error.Message });
            if (nativeFaultState != null && nativeFaultState.HasFault)
                result["native_database_fault"] = nativeFaultState.Snapshot();
        }
        finally
        {
            if (acquired) EndCommand();
        }
        return result;
    }

    private static List<KeyValuePair<string, string>> ValidateAttributeBatchInputs(IDictionary<string, string> values)
    {
        if (values == null || values.Count < 1 || values.Count > 20)
            throw new ArgumentException("values must contain between 1 and 20 string attributes.", "values");
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var result = new List<KeyValuePair<string, string>>();
        foreach (KeyValuePair<string, string> pair in values)
        {
            if (string.IsNullOrWhiteSpace(pair.Key) || pair.Key.Any(char.IsControl))
                throw new ArgumentException("Attribute names must be non-empty and contain no control characters.", "values");
            if (pair.Value == null)
                throw new ArgumentException("Attribute values must be strings, not null: " + pair.Key, "values");
            string name = pair.Key.Trim().ToUpperInvariant();
            if (!seen.Add(name))
                throw new ArgumentException("Duplicate attribute after normalization: " + name, "values");
            if (AttributeEditPolicy.InputKind(name) == null)
                throw new ArgumentException("Attribute is not enabled for direct MCP editing: " + name, "values");
            result.Add(new KeyValuePair<string, string>(name, pair.Value));
        }
        return result;
    }

    private AttributeBatchState AttributeBatchReadState(string requested, IList<KeyValuePair<string, string>> inputs)
    {
        AttributeBatchState state = AttributeBatchReadIdentity(requested);
        var items = new List<AttributeBatchItem>();
        foreach (KeyValuePair<string, string> pair in inputs)
        {
            ThrowIfNativeFaulted();
            DbAttribute attribute = NativeRead(() => DbAttribute.GetDbAttribute(pair.Key));
            if (attribute == null || !NativeRead(() => attribute.IsValid))
                throw new InvalidOperationException("E3D attribute was not found: " + pair.Key);
            string canonical = NativeRead(() => attribute.ShortName).Trim().ToUpperInvariant();
            if (!string.Equals(canonical, pair.Key, StringComparison.Ordinal))
                throw new InvalidOperationException("Requested attribute did not resolve to its normalized native name: " + pair.Key);
            if (!NativeRead(() => state.Element.IsAttributeValid(attribute)))
                throw new InvalidOperationException("Attribute " + canonical + " is not supported on " + state.Type + ".");
            if (!NativeRead(() => state.Element.IsAttributeSetable(attribute)))
                throw new InvalidOperationException("E3D reports that attribute " + canonical + " cannot be set on this element now.");
            object parsed = AttributeEditPolicy.Parse(canonical, pair.Value);
            string sdkType = NativeRead(() => attribute.Type.ToString());
            bool isArray = NativeRead(() => attribute.IsArray);
            if (!AttributeEditPolicy.SupportsSdkType(canonical, sdkType, isArray))
                throw new InvalidOperationException("Attribute SDK type is not supported by direct MCP editing: " + sdkType);
            string oldValue = NativeRead(() => state.Element.GetAsString(attribute));
            if (oldValue == null) throw new InvalidOperationException("A complete native old value is required for attribute " + canonical + ".");
            string oldTypedValue = null;
            if (parsed is string)
            {
                oldTypedValue = NativeRead(() => state.Element.GetString(attribute));
                if (oldTypedValue == null)
                    throw new InvalidOperationException("A complete typed native old value is required for attribute " + canonical + ".");
            }
            double? oldNumeric = null;
            if (parsed is double)
            {
                double numeric = NativeRead(() => state.Element.GetDouble(attribute));
                if (double.IsNaN(numeric) || double.IsInfinity(numeric))
                    throw new InvalidOperationException("A finite typed native old value is required for attribute " + canonical + ".");
                oldNumeric = numeric;
            }
            items.Add(new AttributeBatchItem { Name = canonical, InputValue = pair.Value, OldValue = oldValue, OldTypedValue = oldTypedValue,
                OldNumericValue = oldNumeric, ParsedValue = parsed, Attribute = attribute });
        }
        state.Items = items;
        return state;
    }

    private AttributeBatchState AttributeBatchReadIdentity(string requested)
    {
        ThrowIfNativeFaulted();
        DbElement current = CurrentElement();
        if (!IsUsable(current)) throw new InvalidOperationException("A current E3D element is required to bind the edit context.");
        DbElement element = ResolveElement(requested);
        if (!IsUsable(element)) throw new InvalidOperationException("Element was not found: " + requested);
        if (NativeRead(() => element.DbType) != DbType.Design)
            throw new InvalidOperationException("Batch attribute editing requires an element from a Design database.");
        return AttributeBatchReadIdentity(requested, element);
    }

    private AttributeBatchState AttributeBatchReadResolvedIdentity(AttributeBatchState prior)
    {
        if (prior == null || !IsUsable(prior.Element))
            throw new InvalidOperationException("The edited element is no longer available for identity readback.");
        return AttributeBatchReadIdentity(prior.RequestedRefno, prior.Element);
    }

    private AttributeBatchState AttributeBatchReadIdentity(string requested, DbElement element)
    {
        ThrowIfNativeFaulted();
        MDB mdb = CurrentMdb();
        Project project = NativeRead(() => Project.CurrentProject);
        if (mdb == null || project == null) throw new InvalidOperationException("A current E3D project and MDB are required.");
        DbElement current = CurrentElement();
        if (!IsUsable(current)) throw new InvalidOperationException("A current E3D element is required to bind the edit context.");
        DbElementType actual = NativeRead(() => element.GetActualType());
        if (actual == null || !NativeRead(() => actual.IsValid)) throw new InvalidOperationException("Native target type metadata is unavailable.");
        DbElement owner = NativeRead(() => element.Owner);
        string ownerRef = IsUsable(owner) ? ElementRef(owner) : "";
        string name = NativeRead(() => element.GetString(DbAttributeInstance.NAME));
        string projectName = NativeRead(() => project.Name);
        string mdbName = NativeRead(() => mdb.Name);
        string ce = ElementRef(current);
        string type = NativeRead(() => actual.ShortName).Trim().ToUpperInvariant();
        if (string.IsNullOrWhiteSpace(type) || name == null || string.IsNullOrWhiteSpace(projectName) || string.IsNullOrWhiteSpace(mdbName))
            throw new InvalidOperationException("Complete project, MDB and target identity observations are required.");
        if (requested.StartsWith("=", StringComparison.Ordinal))
        {
            string[] pair = requested.Substring(1).Split('/');
            string canonical = "=" + int.Parse(pair[0], CultureInfo.InvariantCulture).ToString(CultureInfo.InvariantCulture) + "/" +
                int.Parse(pair[1], CultureInfo.InvariantCulture).ToString(CultureInfo.InvariantCulture);
            if (!string.Equals(ElementRef(element), canonical, StringComparison.Ordinal))
                throw new InvalidOperationException("Native numeric lookup returned a different target reference.");
        }
        return new AttributeBatchState
        {
            RequestedRefno = requested, Refno = ElementRef(element), Type = type, Name = name,
            Owner = ownerRef, Project = projectName, Mdb = mdbName, Ce = ce, Element = element
        };
    }

    private string AttributeBatchRevision(AttributeBatchState state)
    {
        return PrimitiveHash(writer =>
        {
            writer.Write("direct-attribute-batch-v1"); writer.Write(attributeBatchRevisionSession);
            writer.Write(state.RequestedRefno); writer.Write(state.Refno); writer.Write(state.Type);
            writer.Write(state.Name); writer.Write(state.Owner); writer.Write(state.Project); writer.Write(state.Mdb); writer.Write(state.Ce);
            writer.Write(state.Items.Count);
            foreach (AttributeBatchItem item in state.Items)
            {
                writer.Write(item.Name); writer.Write(item.OldValue);
                writer.Write(item.OldTypedValue != null);
                if (item.OldTypedValue != null) writer.Write(item.OldTypedValue);
                writer.Write(item.OldNumericValue.HasValue);
                if (item.OldNumericValue.HasValue) writer.Write(item.OldNumericValue.Value);
                if (item.ParsedValue is string)
                {
                    writer.Write("string"); writer.Write((string)item.ParsedValue);
                }
                else
                {
                    writer.Write("double"); writer.Write((double)item.ParsedValue);
                }
            }
        });
    }

    private static bool AttributeBatchSameIdentity(AttributeBatchState before, AttributeBatchState after,
        IList<Dictionary<string, object>> fields)
    {
        AttributeBatchItem name = before.Items == null ? null : before.Items.FirstOrDefault(item => item.Name == "NAME");
        bool nameApplied = AttributeBatchNameWasApplied(before, name, fields);
        bool nameMatches = name == null || !nameApplied
            ? string.Equals(before.Name, after.Name, StringComparison.Ordinal)
            : ModelEditInput.TextMatches((string)name.ParsedValue, after.Name, true);
        return before.RequestedRefno == after.RequestedRefno && before.Refno == after.Refno &&
            before.Type == after.Type && nameMatches && before.Owner == after.Owner && before.Project == after.Project &&
            before.Mdb == after.Mdb && before.Ce == after.Ce;
    }

    private static bool AttributeBatchNameWasApplied(AttributeBatchState state, AttributeBatchItem name,
        IList<Dictionary<string, object>> fields)
    {
        if (name == null || fields == null) return false;
        int index = state.Items.IndexOf(name);
        if (index < 0 || index >= fields.Count) return false;
        Dictionary<string, object> field = fields[index];
        string status = field.ContainsKey("status") ? field["status"] as string : null;
        if (status == "verified" || status == "no_change") return true;
        return status == "applied_unverified" && field.ContainsKey("actual_typed_value") &&
            ModelEditInput.TextMatches((string)name.ParsedValue, field["actual_typed_value"] as string, true);
    }

    private bool AttributeBatchStillCurrent(AttributeBatchState state, int itemIndex,
        IList<Dictionary<string, object>> fields, out string observedName, out string error)
    {
        observedName = null;
        error = null;
        AttributeBatchState current;
        try { current = AttributeBatchReadResolvedIdentity(state); }
        catch (Exception exception)
        {
            ThrowIfNativeFaulted();
            error = "Target or project context could not be revalidated before the next setter: " + exception.Message;
            return false;
        }
        observedName = current.Name;
        AttributeBatchItem nameItem = state.Items.FirstOrDefault(item => item.Name == "NAME");
        int nameIndex = nameItem == null ? -1 : state.Items.IndexOf(nameItem);
        bool nameApplied = nameIndex >= 0 && nameIndex < itemIndex && AttributeBatchNameWasApplied(state, nameItem, fields);
        string expectedName = nameApplied ? (string)nameItem.ParsedValue : state.Name;
        bool identityMatches = state.RequestedRefno == current.RequestedRefno && state.Refno == current.Refno &&
            state.Type == current.Type && state.Owner == current.Owner && state.Project == current.Project &&
            state.Mdb == current.Mdb && state.Ce == current.Ce &&
            ModelEditInput.TextMatches(expectedName, current.Name, true);
        if (!identityMatches)
        {
            error = "Project/MDB, CE or target identity changed before the next setter. No further attributes were written.";
            return false;
        }

        AttributeBatchItem item = state.Items[itemIndex];
        try
        {
            bool unchanged;
            if (item.OldNumericValue.HasValue)
            {
                double numeric = NativeRead(() => state.Element.GetDouble(item.Attribute));
                unchanged = AttributeBatchNumberMatches(item.OldNumericValue.Value, numeric);
            }
            else
            {
                string typedValue = NativeRead(() => state.Element.GetString(item.Attribute));
                unchanged = AttributeBatchObservedMatchesOld(item, typedValue);
            }
            if (!unchanged)
            {
                error = "Attribute " + item.Name + " changed after preflight and before its setter. No further writes were attempted.";
                return false;
            }
        }
        catch (Exception exception)
        {
            ThrowIfNativeFaulted();
            error = "Attribute " + item.Name + " could not be revalidated before its setter: " + exception.Message;
            return false;
        }
        return true;
    }

    private static bool AttributeBatchValueMatches(string name, object expected, string actual)
    {
        if (actual == null) return false;
        if (expected is string)
            return name == "NAME" ? ModelEditInput.TextMatches((string)expected, actual, true) :
                string.Equals((string)expected, actual, StringComparison.Ordinal);
        double parsed;
        return double.TryParse(actual, NumberStyles.Float, CultureInfo.InvariantCulture, out parsed) &&
            AttributeBatchNumberMatches((double)expected, parsed);
    }

    private static bool AttributeBatchAlreadyMatches(AttributeBatchItem item)
    {
        if (item.ParsedValue is string)
            return item.Name == "NAME" ? ModelEditInput.TextMatches((string)item.ParsedValue, item.OldTypedValue, true) :
                string.Equals((string)item.ParsedValue, item.OldTypedValue, StringComparison.Ordinal);
        return item.OldNumericValue.HasValue && AttributeBatchNumberMatches((double)item.ParsedValue, item.OldNumericValue.Value);
    }

    private static bool AttributeBatchObservedMatchesOld(AttributeBatchItem item, string actual)
    {
        if (item.OldNumericValue.HasValue)
            throw new InvalidOperationException("Numeric old-value comparison requires a typed double observation.");
        return item.Name == "NAME" ? ModelEditInput.TextMatches(item.OldTypedValue, actual, true) :
            string.Equals(item.OldTypedValue, actual, StringComparison.Ordinal);
    }

    private static bool AttributeBatchNumberMatches(double expected, double actual)
    {
        return !double.IsNaN(expected) && !double.IsInfinity(expected) && !double.IsNaN(actual) && !double.IsInfinity(actual) &&
            Math.Abs(actual - expected) <= 0.00000001 + 0.0000000001 * Math.Max(Math.Abs(actual), Math.Abs(expected));
    }

    private static Dictionary<string, object> AttributeBatchField(AttributeBatchItem item, string status,
        bool writeAttempted, bool? writeApplied, string actualValue, string error)
    {
        var field = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
        {
            ["attribute"] = item.Name, ["input_value"] = item.InputValue, ["parsed_value"] = item.ParsedValue,
            ["old_value"] = item.OldValue, ["old_typed_value"] = item.OldTypedValue,
            ["old_numeric_value"] = item.OldNumericValue,
            ["status"] = status, ["write_attempted"] = writeAttempted,
            ["write_applied"] = writeApplied, ["readback_verified"] = false
        };
        if (actualValue != null) field["actual_value"] = actualValue;
        if (error != null) field["error"] = error;
        return field;
    }
}
