using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using Aveva.Core.Database;
using E3d21AiBridge;

namespace Aveva.Core.Database
{
    public enum DbType { Design, Catalogue }
    public enum FakeAttributeType { STRING, DOUBLE, STRINGARRAY }

    public sealed class DbAttribute
    {
        internal static readonly Dictionary<string, DbAttribute> Attributes = new Dictionary<string, DbAttribute>(StringComparer.OrdinalIgnoreCase);
        public string ShortName { get; internal set; }
        public bool IsValid { get; internal set; } = true;
        public bool IsArray { get; internal set; }
        public FakeAttributeType Type { get; internal set; }
        public static DbAttribute GetDbAttribute(string name)
        {
            DbAttribute value;
            return Attributes.TryGetValue(name, out value) ? value : null;
        }
    }

    public static class DbAttributeInstance
    {
        public static readonly DbAttribute NAME = new DbAttribute { ShortName = "NAME", Type = FakeAttributeType.STRING, IsArray = true };
    }

    public sealed class DbElementType
    {
        public string ShortName { get; set; } = "EQUI";
        public bool IsValid { get; set; } = true;
    }

    public sealed class DbElement
    {
        internal int Id = 1;
        internal string Name = "/PUMP";
        internal readonly Dictionary<string, string> Values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        { ["NAME"] = "/PUMP", ["DESC"] = "old description", ["DIAM"] = "5" };
        internal DbElement OwnerValue = null;
        internal DbType DatabaseType = DbType.Design;
        internal DbElementType TypeValue = new DbElementType();
        public bool IsValid { get { return true; } }
        public bool IsNull { get { return false; } }
        public int[] Ref { get { return new[] { 1, Id }; } }
        public DbElement Owner { get { return OwnerValue; } }
        public DbType DbType { get { return DatabaseType; } }
        public DbElementType GetActualType() { return TypeValue; }
        public bool IsAttributeValid(DbAttribute attribute) { return attribute.ShortName != "UNSUPPORTED"; }
        public bool IsAttributeSetable(DbAttribute attribute) { return true; }
        public string GetAsString(DbAttribute attribute)
        {
            if (Scenario.ReadbackFailureArmed && Scenario.WriteCount > 0 && attribute.ShortName == Scenario.ReadbackFailureAttribute)
            {
                Scenario.ReadbackFailureArmed = false;
                throw new InvalidOperationException("simulated readback failure");
            }
            string formatted;
            if (Scenario.FormattedOverrides.TryGetValue(attribute.ShortName, out formatted)) return formatted;
            string value;
            value = Values.TryGetValue(attribute.ShortName, out value) ? value : "";
            return value.Length == 0 && Scenario.EmptyTextFormatsAsUnset &&
                (attribute.ShortName == "DESC" || attribute.ShortName == "DUTY") ? "unset" : value;
        }
        public string GetString(DbAttribute attribute)
        { string value; return Values.TryGetValue(attribute.ShortName, out value) ? value : ""; }
        public double GetDouble(DbAttribute attribute)
        { return double.Parse(Values[attribute.ShortName], NumberStyles.Float, CultureInfo.InvariantCulture); }
        public void SetAttribute(DbAttribute attribute, string value)
        {
            Scenario.BeforeSetter(attribute.ShortName);
            Values[attribute.ShortName] = value;
            Scenario.AfterSetter(attribute.ShortName);
        }
        public void SetAttribute(DbAttribute attribute, double value)
        {
            Scenario.BeforeSetter(attribute.ShortName);
            Values[attribute.ShortName] = value.ToString("R", CultureInfo.InvariantCulture);
            Scenario.AfterSetter(attribute.ShortName);
        }
    }

    public sealed class MDB
    {
        public static MDB CurrentMDB;
        public string Name { get; set; } = "ALL";
    }

    public sealed class Project
    {
        public static Project CurrentProject;
        public string Name { get; set; } = "APS";
    }
}

internal sealed class NativeDatabaseFaultState
{
    internal bool HasFault = false;
    internal object Snapshot() { return new { has_fault = HasFault }; }
}

internal sealed class NativeDatabaseFaultException : InvalidOperationException
{
    internal NativeDatabaseFaultException() : base("simulated latched native database fault") { }
}

internal static class ModelEditInput
{
    internal static string ExplicitReference(string value, string parameter)
    {
        string normalized = (value ?? "").Trim();
        if (normalized != "=1/1") throw new ArgumentException(parameter + " must be an explicit element reference.");
        return normalized;
    }
    internal static bool TextMatches(string expected, string actual, bool databaseName)
    {
        if (expected == null || actual == null) return expected == actual;
        return databaseName
            ? string.Equals(expected.TrimStart('/'), actual.TrimStart('/'), StringComparison.OrdinalIgnoreCase)
            : string.Equals(expected, actual, StringComparison.Ordinal);
    }
}

internal static class Scenario
{
    internal static DbElement Element;
    internal static DbElement Current;
    internal static Project Project;
        internal static MDB MdbValue;
    internal static string FailWriteAttribute;
    internal static string ReadbackFailureAttribute;
    internal static bool ReadbackFailureArmed;
    internal static Action<string> AfterWrite;
    internal static bool ResidualThenThrow;
    internal static int WriteCount;
    internal static readonly List<string> Writes = new List<string>();
    internal static readonly Dictionary<string, string> FormattedOverrides = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
    internal static bool EmptyTextFormatsAsUnset;
    internal static NativeDatabaseFaultState FaultState;
    internal static int NativeBoundaryCount, NativeAfterFault, FaultAtBoundary;
    internal static bool FaultBoundaryThrows;
    internal static readonly List<string> NativeBoundaries = new List<string>();

    internal static void Reset()
    {
        DbAttribute.Attributes.Clear();
        DbAttribute.Attributes["NAME"] = DbAttributeInstance.NAME;
        DbAttribute.Attributes["DESC"] = new DbAttribute { ShortName = "DESC", Type = FakeAttributeType.STRING };
        DbAttribute.Attributes["DIAM"] = new DbAttribute { ShortName = "DIAM", Type = FakeAttributeType.DOUBLE };
        DbAttribute.Attributes["DUTY"] = new DbAttribute { ShortName = "DUTY", Type = FakeAttributeType.STRING };
        Element = new DbElement();
        Current = new DbElement { Id = 8, Name = "/CE" };
        Project = new Project(); MdbValue = new Aveva.Core.Database.MDB();
        Aveva.Core.Database.Project.CurrentProject = Project;
        Aveva.Core.Database.MDB.CurrentMDB = MdbValue;
        FailWriteAttribute = null; ReadbackFailureAttribute = null; ReadbackFailureArmed = false;
        AfterWrite = null; ResidualThenThrow = false;
        WriteCount = 0; Writes.Clear();
        FormattedOverrides.Clear(); EmptyTextFormatsAsUnset = false;
        FaultState = null; NativeBoundaryCount = NativeAfterFault = FaultAtBoundary = 0;
        FaultBoundaryThrows = false; NativeBoundaries.Clear();
    }

    internal static void Boundary(string kind)
    {
        if (FaultState != null && FaultState.HasFault) NativeAfterFault++;
        NativeBoundaryCount++;
        NativeBoundaries.Add(kind);
        if (FaultAtBoundary > 0 && NativeBoundaryCount == FaultAtBoundary)
        {
            FaultState.HasFault = true;
            if (FaultBoundaryThrows) throw new InvalidOperationException("simulated native callback fault");
        }
    }

    internal static void ResetTrace()
    {
        NativeBoundaryCount = NativeAfterFault = 0;
        NativeBoundaries.Clear();
    }

    internal static void BeforeSetter(string name)
    {
        WriteCount++;
        Writes.Add(name);
        if (name == FailWriteAttribute)
        {
            if (ResidualThenThrow) Element.Values[name] = "unexpected residual";
            throw new InvalidOperationException("simulated setter failure");
        }
    }

    internal static void AfterSetter(string name)
    {
        if (name == ReadbackFailureAttribute) ReadbackFailureArmed = true;
        if (AfterWrite != null) AfterWrite(name);
    }
}

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        private bool TryBeginCommand(string command)
        {
            if (nativeFaultState.HasFault) throw new NativeDatabaseFaultException();
            return true;
        }
        private void EndCommand() { }
        private T OnUi<T>(Func<T> action, bool allowNativeFault = false)
        {
            Scenario.FaultState = nativeFaultState;
            ThrowIfNativeFaulted();
            return action();
        }
        private static bool IsUsable(DbElement element) { return element != null && element.IsValid && !element.IsNull; }
        private static DbElement CurrentElement() { return Scenario.Current; }
        private static DbElement ResolveElement(string refno) { return refno == "=1/1" ? Scenario.Element : null; }
        private static MDB CurrentMdb() { return MDB.CurrentMDB; }
        private static string ElementRef(DbElement element) { return "=1/" + element.Ref[1].ToString(CultureInfo.InvariantCulture); }
        private static T NativeRead<T>(Func<T> read)
        {
            ThrowIfNativeFaulted();
            try { Scenario.Boundary("read"); return read(); }
            finally { ThrowIfNativeFaulted(); }
        }
        private static void NativeWrite(Action write)
        {
            ThrowIfNativeFaulted();
            try { Scenario.Boundary("write"); write(); }
            finally { ThrowIfNativeFaulted(); }
        }
        private static void ThrowIfNativeFaulted()
        {
            if (Scenario.FaultState != null && Scenario.FaultState.HasFault) throw new NativeDatabaseFaultException();
            if (Scenario.Element == null) throw new InvalidOperationException("test context unavailable");
        }
        private static Dictionary<string, object> BusyResult(string command)
        { return new Dictionary<string, object> { ["success"] = false, ["busy"] = true, ["command"] = command }; }
        private static string PrimitiveHash(Action<BinaryWriter> write)
        {
            using (var stream = new MemoryStream())
            using (var writer = new BinaryWriter(stream, System.Text.Encoding.UTF8, true))
            {
                write(writer); writer.Flush();
                using (var sha = SHA256.Create())
                    return string.Concat(sha.ComputeHash(stream.ToArray()).Select(value => value.ToString("x2", CultureInfo.InvariantCulture)));
            }
        }
    }
}

internal static class AttributeBatchHarness
{
    private static int checks;

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Check failed: " + message);
        checks++;
    }

    private static Dictionary<string, object> Dict(object value)
    { return (Dictionary<string, object>)value; }

    private static string Preview(E3d21Context context, IDictionary<string, string> values)
    {
        var result = Dict(context.SetAttributes("=1/1", values, null, true));
        Check((bool)result["success"], "preview succeeds");
        Check((string)result["revision"] != null && ((string)result["revision"]).Length == 64, "preview returns revision");
        Check(Scenario.Writes.Count == 0, "preview performs no setters");
        return (string)result["revision"];
    }

    private static void Run()
    {
        Scenario.Reset();
        var context = new E3d21Context();
        var values = new Dictionary<string, string> { ["DESC"] = "Pump casing", ["DIAM"] = "12.5" };
        string revision = Preview(context, values);
        var success = Dict(context.SetAttributes("=1/1", values, revision, false));
        Check((bool)success["success"] && (bool)success["readback_verified"], "multi-field execution verifies readback");
        Check(Scenario.Writes.SequenceEqual(new[] { "DESC", "DIAM" }), "attributes execute in submitted order");
        var successfulFields = (List<Dictionary<string, object>>)success["attributes"];
        Check((string)successfulFields[0]["attribute"] == "DESC" && (string)successfulFields[1]["attribute"] == "DIAM",
            "per-field response preserves submitted order");
        Check(Scenario.Element.Values["DESC"] == "Pump casing" && Scenario.Element.Values["DIAM"] == "12.5", "both values persisted");
        Check(!(bool)success["partial_write"] && !(bool)success["outcome_uncertain"], "complete success is not partial or uncertain");

        Scenario.Reset(); context = new E3d21Context();
        var unchangedValues = new Dictionary<string, string> { ["NAME"] = "pump", ["DESC"] = "old description", ["DIAM"] = "5.0" };
        revision = Preview(context, unchangedValues);
        Scenario.FailWriteAttribute = "DIAM";
        var unchanged = Dict(context.SetAttributes("=1/1", unchangedValues, revision, false));
        Check((bool)unchanged["success"] && (bool)unchanged["no_change"] && (bool)unchanged["readback_verified"],
            "normalized NAME and already-matching values are verified as no-change");
        Check(Scenario.Writes.Count == 0 && Equals(unchanged["write_applied"], false),
            "no-change fields do not call setters or claim applied writes");
        var unchangedFields = (List<Dictionary<string, object>>)unchanged["attributes"];
        Check(unchangedFields.All(field => (string)field["status"] == "no_change" && Equals(field["write_applied"], false)),
            "no-change field receipts remain precise");

        Scenario.Reset(); context = new E3d21Context();
        Scenario.Element.Values["DESC"] = "";
        Scenario.EmptyTextFormatsAsUnset = true;
        revision = Preview(context, new Dictionary<string, string> { ["DESC"] = "" });
        var emptyDesc = Dict(context.SetAttributes("=1/1", new Dictionary<string, string> { ["DESC"] = "" }, revision, false));
        var emptyDescField = ((List<Dictionary<string, object>>)emptyDesc["attributes"])[0];
        Check((bool)emptyDesc["success"] && (bool)emptyDesc["no_change"] && Scenario.Writes.Count == 0,
            "empty typed DESC is a no-change despite formatted unset");
        Check((string)emptyDescField["old_value"] == "unset" && (string)emptyDescField["old_typed_value"] == "",
            "empty DESC retains separate formatted and typed values");

        Scenario.Reset(); context = new E3d21Context();
        revision = Preview(context, values);
        Scenario.Element.Values["DESC"] = "intervening edit";
        var stale = Dict(context.SetAttributes("=1/1", values, revision, false));
        Check(!(bool)stale["success"] && (bool)stale["revision_conflict"], "changed old value invalidates revision");
        Check(Scenario.Writes.Count == 0, "stale revision causes zero writes");

        foreach (string drift in new[] { "ce", "project", "mdb", "type", "requested value" })
        {
            Scenario.Reset(); context = new E3d21Context();
            revision = Preview(context, values);
            if (drift == "ce") Scenario.Current.Id++;
            if (drift == "project") Scenario.Project.Name = "OTHER";
            if (drift == "mdb") Scenario.MdbValue.Name = "OTHER";
            if (drift == "type") Scenario.Element.TypeValue.ShortName = "PIPE";
            IDictionary<string, string> changedValues = drift == "requested value"
                ? (IDictionary<string, string>)new Dictionary<string, string> { ["DESC"] = "Different", ["DIAM"] = "12.5" }
                : values;
            var conflict = Dict(context.SetAttributes("=1/1", changedValues, revision, false));
            Check(!(bool)conflict["success"] && (bool)conflict["revision_conflict"], "preview drift rejected: " + drift);
            Check(Scenario.Writes.Count == 0, "preview drift performs zero writes: " + drift);
        }

        Scenario.Reset(); context = new E3d21Context();
        revision = Preview(context, values);
        Scenario.FailWriteAttribute = "DIAM";
        var partial = Dict(context.SetAttributes("=1/1", values, revision, false));
        Check(!(bool)partial["success"] && (bool)partial["partial_write"], "later setter failure reports partial state");
        Check(Scenario.Element.Values["DESC"] == "Pump casing" && Scenario.Element.Values["DIAM"] == "5", "no rollback hides earlier applied value");
        var partialFields = (List<Dictionary<string, object>>)partial["attributes"];
        Check((bool)partialFields[0]["write_applied"] && Equals(partialFields[1]["write_applied"], false), "per-field applied state retained");
        Check(Scenario.Writes.Count == 2, "failed write is not replayed");

        Scenario.Reset(); context = new E3d21Context();
        Scenario.FormattedOverrides["DIAM"] = "5 mm";
        revision = Preview(context, values);
        Scenario.FailWriteAttribute = "DIAM";
        var unitFormattedFailure = Dict(context.SetAttributes("=1/1", values, revision, false));
        var unitFormattedField = ((List<Dictionary<string, object>>)unitFormattedFailure["attributes"])[1];
        Check((string)unitFormattedField["status"] == "not_applied" && Equals(unitFormattedField["write_applied"], false),
            "numeric failure recognizes typed old value without parsing units from formatted text");
        Check((string)unitFormattedField["actual_value"] == "5 mm" && (double)unitFormattedField["actual_numeric_value"] == 5,
            "numeric residual receipt retains formatted and typed observations");

        Scenario.Reset(); context = new E3d21Context();
        var nameAfterFailure = new Dictionary<string, string> { ["DESC"] = "new", ["NAME"] = "/RENAMED" };
        revision = Preview(context, nameAfterFailure);
        Scenario.FailWriteAttribute = "DESC";
        var nameNotAttempted = Dict(context.SetAttributes("=1/1", nameAfterFailure, revision, false));
        var nameNotAttemptedFields = (List<Dictionary<string, object>>)nameNotAttempted["attributes"];
        Check((string)nameNotAttemptedFields[1]["status"] == "not_attempted" &&
            Equals(nameNotAttempted["context_unchanged"], true),
            "unattempted NAME keeps old identity after an earlier setter failure");

        Scenario.Reset(); context = new E3d21Context();
        var nameOnly = new Dictionary<string, string> { ["NAME"] = "/RENAMED" };
        revision = Preview(context, nameOnly);
        Scenario.ReadbackFailureAttribute = "NAME";
        var appliedUnverifiedName = Dict(context.SetAttributes("=1/1", nameOnly, revision, false));
        var appliedUnverifiedNameField = ((List<Dictionary<string, object>>)appliedUnverifiedName["attributes"])[0];
        Check((string)appliedUnverifiedNameField["status"] == "applied_unverified" &&
            Equals(appliedUnverifiedName["context_unchanged"], true),
            "typed confirmed NAME application remains expected identity after formatted read failure");

        Scenario.Reset(); context = new E3d21Context();
        revision = Preview(context, values);
        Scenario.AfterWrite = name => { if (name == "DESC") Scenario.Current.Id++; };
        var midContext = Dict(context.SetAttributes("=1/1", values, revision, false));
        Check(!(bool)midContext["success"] && (bool)midContext["revision_conflict"], "setter context drift stops the batch");
        Check(Scenario.Writes.SequenceEqual(new[] { "DESC" }) && Scenario.Element.Values["DIAM"] == "5",
            "context drift causes zero later setters");
        var stoppedFields = (List<Dictionary<string, object>>)midContext["attributes"];
        Check((string)stoppedFields[1]["status"] == "not_attempted" && Equals(stoppedFields[1]["write_attempted"], false),
            "unattempted field retained after context drift");

        Scenario.Reset(); context = new E3d21Context();
        var textValues = new Dictionary<string, string> { ["DESC"] = "new description", ["DUTY"] = "new duty" };
        revision = Preview(context, textValues);
        Scenario.FailWriteAttribute = "DUTY"; Scenario.ResidualThenThrow = true;
        var residual = Dict(context.SetAttributes("=1/1", textValues, revision, false));
        var residualFields = (List<Dictionary<string, object>>)residual["attributes"];
        Check((string)residualFields[1]["status"] == "residual_observed" && residualFields[1]["write_applied"] == null &&
            (bool)residual["outcome_uncertain"], "unattributed residual remains unknown rather than claimed applied");

        Scenario.Reset(); context = new E3d21Context();
        revision = Preview(context, values);
        Scenario.ReadbackFailureAttribute = "DIAM";
        var unreadable = Dict(context.SetAttributes("=1/1", values, revision, false));
        Check(!(bool)unreadable["success"] && (bool)unreadable["partial_write"], "readback failure reports partial completion");
        Check(Scenario.Element.Values["DIAM"] == "12.5" && Scenario.Writes.Count == 2, "readback failure does not replay setter");

        Scenario.Reset(); context = new E3d21Context();
        var duplicate = new Dictionary<string, string> { ["DIAM"] = "1", [" diam "] = "2" };
        bool rejected = false;
        try { context.SetAttributes("=1/1", duplicate, null, true); } catch (ArgumentException) { rejected = true; }
        Check(rejected && Scenario.Writes.Count == 0, "canonical duplicate is rejected before dispatch");

        Scenario.Reset(); context = new E3d21Context();
        DbAttribute.Attributes["DIAM"] = new DbAttribute { ShortName = "DIAM", Type = FakeAttributeType.STRING };
        var wrongSdkType = Dict(context.SetAttributes("=1/1", new Dictionary<string, string> { ["DIAM"] = "12.5" }, null, true));
        Check(!(bool)wrongSdkType["success"] && Scenario.Writes.Count == 0, "unsupported SDK type is rejected before any write");

        Scenario.Reset(); context = new E3d21Context();
        Scenario.Element.DatabaseType = DbType.Catalogue;
        var catalogue = Dict(context.SetAttributes("=1/1", new Dictionary<string, string> { ["DESC"] = "No" }, null, true));
        Check(!(bool)catalogue["success"] && Scenario.Writes.Count == 0, "catalogue target is rejected before any write");

        Scenario.Reset(); context = new E3d21Context();
        var badNumeric = Dict(context.SetAttributes("=1/1", new Dictionary<string, string> { ["DIAM"] = "NaN" }, null, true));
        Check(!(bool)badNumeric["success"] && Scenario.Writes.Count == 0, "non-finite numeric input is rejected before writes");

        NativeFaultMatrix();
    }

    private static void NativeFaultMatrix()
    {
        var values = new Dictionary<string, string> { ["DESC"] = "Pump casing", ["DIAM"] = "12.5", ["DUTY"] = "Service" };
        Scenario.Reset();
        var context = new E3d21Context();
        string revision = Preview(context, values);
        Scenario.ResetTrace();
        var baseline = Dict(context.SetAttributes("=1/1", values, revision, false));
        Check((bool)baseline["success"], "native fault matrix baseline succeeds");
        string[] boundaries = Scenario.NativeBoundaries.ToArray();
        Check(boundaries.Contains("read") && boundaries.Contains("write"), "native matrix includes both read and write barriers");
        for (int at = 1; at <= boundaries.Length; at++)
        {
            foreach (bool throws in new[] { false, true })
            {
                Scenario.Reset(); context = new E3d21Context();
                revision = Preview(context, values);
                Scenario.ResetTrace();
                Scenario.FaultAtBoundary = at;
                Scenario.FaultBoundaryThrows = throws;
                var fault = Dict(context.SetAttributes("=1/1", values, revision, false));
                string label = "native boundary " + at + " " + boundaries[at - 1] + " throws=" + throws;
                Check(!(bool)fault["success"] && fault.ContainsKey("native_database_fault"), label + " produces a failed fault receipt");
                Check(Scenario.NativeBoundaryCount == at && Scenario.NativeAfterFault == 0,
                    label + " stops all subsequent native read/write delegates");
                int maximumWrites = boundaries.Take(at).Count(kind => kind == "write");
                Check(Scenario.Writes.Count <= maximumWrites && Scenario.Writes.Distinct().Count() == Scenario.Writes.Count,
                    label + " causes no later or replayed setter");
                var fields = (List<Dictionary<string, object>>)fault["attributes"];
                Check(fields.All(field => !Equals(field["status"], "planned")),
                    label + " marks every non-dispatched field not_attempted");
                if (Equals(fault["write_attempted"], true))
                    Check((bool)fault["outcome_uncertain"], label + " preserves uncertain post-write outcome");
                int nativeCount = Scenario.NativeBoundaryCount;
                int writes = Scenario.Writes.Count;
                var future = Dict(context.SetAttributes("=1/1", values, null, true));
                Check(!(bool)future["success"] && Scenario.NativeBoundaryCount == nativeCount && Scenario.Writes.Count == writes,
                    label + " latched fault blocks later previews before native work");
            }
        }
        Console.WriteLine("Attribute batch native fault boundaries checked: " + boundaries.Length + "; fault injections: " + (boundaries.Length * 2));
    }

    private static int Main()
    {
        try
        {
            Run();
            Console.WriteLine("Attribute batch production contract checks passed: " + checks);
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error);
            return 1;
        }
    }
}
