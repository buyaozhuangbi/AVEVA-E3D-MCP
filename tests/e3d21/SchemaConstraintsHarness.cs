using System;
using System.Collections.Generic;
using System.Linq;
using Aveva.Core.Database;

namespace Aveva.Core.Database
{
    internal enum DbAttributeType { STRING, DOUBLE, INTEGER, BOOL }
    internal enum DbAttributeUnits { UNKNOWN, DISTANCE, ANGLE }

    internal sealed class DbAttribute
    {
        public string Name { get; private set; }
        public string ShortName { get { return Name; } }
        public bool IsValid { get { return true; } }
        public DbAttributeType Type { get { return DbAttributeType.DOUBLE; } }
        public bool IsArray { get { return false; } }
        public bool IsPseudo { get { return false; } }
        public bool IsUDA { get { return false; } }
        public DbAttributeUnits Units { get { return DbAttributeUnits.DISTANCE; } }
        public int MaximumSize { get { return 0; } }

        private DbAttribute(string name) { Name = name; }

        public static DbAttribute GetDbAttribute(string name)
        {
            SchemaConstraintsScenario.RequireDispatch();
            SchemaConstraintsScenario.AttributeLookups++;
            if (name == "UNKNOWN") return null;
            return new DbAttribute(name);
        }
    }

    internal sealed class Project
    {
        public static Project CurrentProject { get { SchemaConstraintsScenario.RequireDispatch(); return SchemaConstraintsScenario.Project; } }
        public string Name { get { SchemaConstraintsScenario.RequireDispatch(); return "PROJECT"; } }
    }

    internal sealed class MDB
    {
        public static MDB CurrentMDB { get { SchemaConstraintsScenario.RequireDispatch(); return SchemaConstraintsScenario.Mdb; } }
        public string Name { get { SchemaConstraintsScenario.RequireDispatch(); return "DESIGN"; } }
    }

    internal static class CurrentElement
    {
        public static DbElement Element { get { SchemaConstraintsScenario.RequireDispatch(); return SchemaConstraintsScenario.CurrentElement; } }
    }

    internal sealed class DbElementType
    {
        public string Name { get; private set; }
        public string ShortName { get { return Name; } }
        public DbElementType(string name) { Name = name; }
        public IEnumerable<DbElementType> MemberTypes()
        {
            SchemaConstraintsScenario.RequireDispatch();
            return new DbElementType[0];
        }
        public IEnumerable<DbElementType> OwnerTypes()
        {
            SchemaConstraintsScenario.RequireDispatch();
            return new DbElementType[0];
        }
    }

    internal sealed class DbElement
    {
        public int[] Ref { get { SchemaConstraintsScenario.RequireDispatch(); return new[] { 1, 1 }; } }
        public string TypeName { get; private set; }
        public bool IsNull { get { SchemaConstraintsScenario.RequireDispatch(); return false; } }
        public bool IsValid { get { SchemaConstraintsScenario.RequireDispatch(); return true; } }

        public DbElement(string typeName = "EQUI") { TypeName = typeName; }

        public DbElementType GetElementType()
        {
            SchemaConstraintsScenario.RequireDispatch();
            return new DbElementType(TypeName);
        }
        public string GetAsString(DbAttribute attribute)
        {
            SchemaConstraintsScenario.RequireDispatch();
            return TypeName + ":" + attribute.Name;
        }
        public double GetDouble(DbAttribute attribute)
        {
            SchemaConstraintsScenario.RequireDispatch();
            return 1.0;
        }
        public int GetInteger(DbAttribute attribute)
        {
            SchemaConstraintsScenario.RequireDispatch();
            return 1;
        }
        public string GetString(DbAttribute attribute)
        {
            SchemaConstraintsScenario.RequireDispatch();
            return TypeName + ":" + attribute.Name;
        }
        public bool GetBool(DbAttribute attribute)
        {
            SchemaConstraintsScenario.RequireDispatch();
            return true;
        }
        public bool IsCreatable(DbElementType type)
        {
            SchemaConstraintsScenario.RequireDispatch();
            return false;
        }
        public IEnumerable<DbAttribute> GetAttributes()
        {
            SchemaConstraintsScenario.RequireDispatch();
            SchemaConstraintsScenario.AttributeEnumerationCalls++;
            return new[] { DbAttribute.GetDbAttribute("HEIG") };
        }
        public bool IsAttributeValid(DbAttribute attribute)
        {
            SchemaConstraintsScenario.RequireDispatch();
            return attribute.Name != "UNSUP";
        }
        public bool IsAttributeSetable(DbAttribute attribute)
        {
            SchemaConstraintsScenario.RequireDispatch();
            return true;
        }
        public bool GetAttributeAllowedValuesAndDescriptions(DbAttribute attribute,
            ref KeyValuePair<string, string>[] values)
        {
            SchemaConstraintsScenario.RequireDispatch();
            SchemaConstraintsScenario.ConstraintCalls.Add(attribute.Name + ".values");
            SchemaConstraintsScenario.Plan(attribute.Name).ReadValues(ref values);
            return SchemaConstraintsScenario.Plan(attribute.Name).ValuesDefined;
        }
        public bool GetAttributeAllowedRanges(DbAttribute attribute, ref double[] ranges)
        {
            SchemaConstraintsScenario.RequireDispatch();
            SchemaConstraintsScenario.ConstraintCalls.Add(attribute.Name + ".ranges");
            SchemaConstraintsScenario.Plan(attribute.Name).ReadRanges(ref ranges);
            return SchemaConstraintsScenario.Plan(attribute.Name).RangesDefined;
        }
        public bool IsAttributeAllowedOptional(DbAttribute attribute)
        {
            SchemaConstraintsScenario.RequireDispatch();
            SchemaConstraintsScenario.ConstraintCalls.Add(attribute.Name + ".optional");
            return SchemaConstraintsScenario.Plan(attribute.Name).ReadOptional();
        }
    }
}

internal sealed class ConstraintPlan
{
    public bool ValuesDefined;
    public KeyValuePair<string, string>[] Values;
    public bool RangesDefined;
    public double[] Ranges;
    public bool Optional;
    public string ValuesError;
    public string RangesError;
    public string OptionalError;
    public bool FaultOnValues;

    public void ReadValues(ref KeyValuePair<string, string>[] output)
    {
        if (FaultOnValues) SchemaConstraintsScenario.LatchNativeFault();
        if (ValuesError != null) throw new InvalidOperationException(ValuesError);
        output = Values;
    }
    public void ReadRanges(ref double[] output)
    {
        if (RangesError != null) throw new InvalidOperationException(RangesError);
        output = Ranges;
    }
    public bool ReadOptional()
    {
        if (OptionalError != null) throw new InvalidOperationException(OptionalError);
        return Optional;
    }
}

internal static class SchemaConstraintsScenario
{
    private static bool inDispatch;
    private static readonly Dictionary<string, ConstraintPlan> plans = new Dictionary<string, ConstraintPlan>();
    public static int Dispatches;
    public static int ResolveCalls;
    public static int AttributeLookups;
    public static int AttributeEnumerationCalls;
    public static int AliasResolutionCalls;
    public static string ResolvedType = "EQUI";
    public static bool AliasResolverFails;
    public static readonly List<string> ConstraintCalls = new List<string>();
    public static E3d21AiBridge.NativeDatabaseFaultState FaultState;
    public static Project Project;
    public static MDB Mdb;
    public static DbElement CurrentElement;

    public static void Reset()
    {
        inDispatch = false;
        plans.Clear();
        Dispatches = ResolveCalls = AttributeLookups = AttributeEnumerationCalls = AliasResolutionCalls = 0;
        ResolvedType = "EQUI";
        AliasResolverFails = false;
        ConstraintCalls.Clear();
        FaultState = new E3d21AiBridge.NativeDatabaseFaultState();
        Project = new Project();
        Mdb = new MDB();
        CurrentElement = new DbElement("EQUI");
    }
    public static void RequireDispatch()
    {
        if (!inDispatch) throw new InvalidOperationException("Fake SDK called outside UI dispatch.");
    }
    public static void RequireDispatchStart()
    {
        if (inDispatch) throw new InvalidOperationException("Nested fake UI dispatch.");
        inDispatch = true;
    }
    public static void EndDispatch() { inDispatch = false; }
    public static ConstraintPlan Plan(string name)
    {
        ConstraintPlan plan;
        if (!plans.TryGetValue(name, out plan)) plans[name] = plan = new ConstraintPlan();
        return plan;
    }
    public static void LatchNativeFault()
    {
        FaultState.Observe("Unhandled Database error 77 from schema_test", "test");
    }
    public static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception("Assertion failed: " + message);
        assertions++;
    }
    private static int assertions;
    private static Dictionary<string, object> Dict(object value) { return (Dictionary<string, object>)value; }
    private static IList<object> Rows(object value) { return (IList<object>)value; }
    private static bool Flag(Dictionary<string, object> item, string key) { return Equals(item[key], true); }
    private static string Text(Dictionary<string, object> item, string key) { return item[key] as string; }
    private static void ExpectArgument(Action action, string label)
    {
        bool rejected = false;
        try { action(); } catch (ArgumentException) { rejected = true; }
        Check(rejected, label);
    }
    private static void ExpectInvalidOperation(Action action, string label)
    {
        bool rejected = false;
        try { action(); } catch (InvalidOperationException) { rejected = true; }
        Check(rejected, label);
    }
    private static Dictionary<string, object> Schema(E3d21AiBridge.E3d21Context context,
        IList<string> names, bool constraints = true, string refno = "=1/1", string expectedType = "")
    {
        return Dict(context.GetElementSchema(refno, names, 0, 50, constraints, expectedType));
    }
    private static Dictionary<string, object> Row(Dictionary<string, object> schema, int index = 0)
    { return Dict(Rows(schema["attributes"])[index]); }
    private static Dictionary<string, object> Constraints(Dictionary<string, object> row)
    { return Dict(row["constraints"]); }

    public static void Run()
    {
        Reset();
        var context = new E3d21AiBridge.E3d21Context();
        Dictionary<string, object> ordinary = Schema(context, new[] { "HEIG" }, false);
        Dictionary<string, object> boundary = Dict(ordinary["context"]);
        Dictionary<string, object> boundaryBefore = Dict(boundary["before"]);
        Dictionary<string, object> boundaryAfter = Dict(boundary["after"]);
        Check(Text(boundaryBefore, "project") == "PROJECT" && Text(boundaryBefore, "mdb") == "DESIGN" &&
            Text(boundaryBefore, "current_element") == "=1/1" && Text(boundaryBefore, "current_element_type") == "EQUI" &&
            Text(boundaryAfter, "current_element") == "=1/1", "schema returns typed context before and after the read");
        Check(Flag(boundary, "same_project_instance") && Flag(boundary, "same_mdb_instance") &&
            Flag(boundary, "identity_unchanged") && Flag(boundary, "ce_unchanged") &&
            Flag(boundary, "boundary_agreement") && Flag(ordinary, "identity_unchanged") &&
            Flag(ordinary, "ce_unchanged"), "schema context identity and CE flags are verified");
        Check(!Row(ordinary).ContainsKey("constraints"), "constraints are absent unless requested");
        Check(!ordinary.ContainsKey("constraints_requested"), "ordinary response has no constraint metadata");
        Check(ConstraintCalls.Count == 0, "includeConstraints=false performs no constraint SDK reads");

        Reset(); context = new E3d21AiBridge.E3d21Context();
        ResolvedType = "BRAN";
        Dictionary<string, object> typedSchema = Schema(context, new[] { "HEIG" }, false, "=1/1", " tubi ");
        Check(Text(Dict(typedSchema["element"]), "type") == "TUBI" &&
            Text(Dict(typedSchema["element_type"]), "name") == "TUBI" && AliasResolutionCalls == 1,
            "Schema reads must resolve and report the typed TUBI alias");

        Reset(); context = new E3d21AiBridge.E3d21Context();
        ResolvedType = "BRAN";
        ExpectInvalidOperation(() => Schema(context, new[] { "HEIG" }, false, "=1/1", "PIPE"),
            "A non-TUBI type mismatch must be rejected");
        Check(AliasResolutionCalls == 0, "Non-TUBI mismatches must not attempt generic alias resolution");

        Reset(); context = new E3d21AiBridge.E3d21Context();
        ResolvedType = "BRAN";
        AliasResolverFails = true;
        ExpectInvalidOperation(() => Schema(context, new[] { "HEIG" }, false, "=1/1", "TUBI"),
            "A failed TUBI alias lookup must fail the schema read");
        Check(AliasResolutionCalls == 1, "A failed TUBI lookup must not silently return its anchor");

        Reset(); context = new E3d21AiBridge.E3d21Context();
        ResolvedType = "BRAN";
        Dictionary<string, object> typedAttributes = Dict(context.ReadAttributes("=1/1", new[] { "POS" }, " tubi "));
        Check(Text(typedAttributes, "type") == "TUBI" &&
            ((IDictionary<string, string>)typedAttributes["values"])["POS"] == "TUBI:POS",
            "Attribute reads must use the resolved TUBI object, not its BRAN anchor");

        Reset(); context = new E3d21AiBridge.E3d21Context();
        ExpectArgument(() => Schema(context, new[] { "HEIG" }, false, "=1/1", "BAD TYPE"),
            "Invalid expected types must be rejected before E3D dispatch");
        Check(Dispatches == 0 && ResolveCalls == 0, "Invalid expected types must not enter the E3D UI dispatcher");

        Reset(); context = new E3d21AiBridge.E3d21Context();
        ExpectArgument(() => context.GetElementSchema("=1/1", new string[0], 0, 50, true),
            "constraint mode requires at least one explicit attribute");
        ExpectArgument(() => context.GetElementSchema("=1/1", Enumerable.Range(0, 11).Select(i => "A" + i).ToList(), 0, 50, true),
            "constraint mode limits attributes to ten");
        ExpectArgument(() => context.GetElementSchema("CE", new[] { "HEIG" }, 0, 50, true),
            "constraint mode requires an explicit refno");
        ExpectArgument(() => context.GetElementSchema("", new[] { "HEIG" }, 0, 50, true),
            "constraint mode rejects an empty refno");
        Check(Dispatches == 0 && AttributeLookups == 0 && ConstraintCalls.Count == 0,
            "all constraint request gates reject before dispatch or SDK access");

        Reset(); context = new E3d21AiBridge.E3d21Context();
        Dictionary<string, object> skipped = Schema(context, new[] { "UNKNOWN", "UNSUP" });
        Check(Text(Constraints(Row(skipped)), "status") == "not_attempted"
            && Text(Constraints(Row(skipped)), "reason") == "unknown_attribute",
            "unknown attribute constraints are not attempted");
        Check(Text(Constraints(Row(skipped, 1)), "status") == "not_attempted"
            && Text(Constraints(Row(skipped, 1)), "reason") == "attribute_not_supported",
            "unsupported attribute constraints are not attempted");
        Check(ConstraintCalls.Count == 0, "unknown and unsupported attributes perform no constraint SDK reads");
        Check(!Flag(skipped, "constraints_complete"), "skipped attributes make constraints_complete false");

        Reset(); context = new E3d21AiBridge.E3d21Context();
        Dictionary<string, object> ten = Schema(context, Enumerable.Range(0, 10).Select(i => "A" + i).ToList());
        Check(Rows(ten["attributes"]).Count == 10 && ConstraintCalls.Count == 30,
            "ten explicit supported attributes each read values, ranges, and optionality");
        Check(Flag(ten, "constraints_requested") && Flag(ten, "constraints_complete"),
            "complete page reports requested and complete constraint status");

        Reset(); context = new E3d21AiBridge.E3d21Context();
        ConstraintPlan plan = Plan("HEIG");
        Dictionary<string, object> falseDefined = Schema(context, new[] { "HEIG" });
        Dictionary<string, object> allowedValues = Dict(Constraints(Row(falseDefined))["allowed_values"]);
        Check(Text(allowedValues, "status") == "not_defined" && Equals(allowedValues["defined"], false)
            && Equals(allowedValues["total_count"], 0), "SDK false means values not_defined, not an empty defined set");
        Check(Text(Dict(Constraints(Row(falseDefined))["allowed_ranges"]), "status") == "not_defined",
            "SDK false means ranges not_defined");
        Check(Flag(falseDefined, "constraints_complete"), "not-defined constraints are a complete read");

        Reset(); context = new E3d21AiBridge.E3d21Context();
        plan = Plan("HEIG"); plan.ValuesDefined = true; plan.Values = null;
        plan.RangesDefined = true; plan.Ranges = null;
        Dictionary<string, object> nullValues = Schema(context, new[] { "HEIG" });
        allowedValues = Dict(Constraints(Row(nullValues))["allowed_values"]);
        Dictionary<string, object> nullRanges = Dict(Constraints(Row(nullValues))["allowed_ranges"]);
        Check(Text(allowedValues, "status") == "read_error" && allowedValues["defined"] == null,
            "SDK true with null values array is a read error");
        Check(Text(nullRanges, "status") == "read_error" && nullRanges["defined"] == null,
            "SDK true with null ranges array is a read error");
        Check(Text(Constraints(Row(nullValues)), "status") == "partial" && !Flag(nullValues, "constraints_complete"),
            "null values array marks the page partial and incomplete");

        Reset(); context = new E3d21AiBridge.E3d21Context();
        plan = Plan("HEIG"); plan.ValuesError = "values unavailable";
        plan.RangesDefined = true; plan.Ranges = new[] { 1.0, 2.0 }; plan.Optional = true;
        Dictionary<string, object> partial = Schema(context, new[] { "HEIG" });
        Dictionary<string, object> partialConstraints = Constraints(Row(partial));
        Check(Text(partialConstraints, "status") == "partial" && Text(Dict(partialConstraints["allowed_ranges"]), "status") == "defined",
            "a values error preserves successful range and optional reads");
        Check(Flag(Dict(partialConstraints["values_or_ranges_optional"]), "value")
            && ((IDictionary<string, string>)partial["read_errors"]).ContainsKey("attribute:HEIG.allowed_values"),
            "partial reads retain optionality and report the failing component");
        Check(!Flag(partial, "constraints_complete"), "partial component read marks constraints incomplete");

        Reset(); context = new E3d21AiBridge.E3d21Context();
        plan = Plan("HEIG"); plan.ValuesDefined = true;
        plan.Values = Enumerable.Range(0, 101).Select(i => new KeyValuePair<string, string>("v" + i, "d" + i)).ToArray();
        Dictionary<string, object> capped = Schema(context, new[] { "HEIG" });
        allowedValues = Dict(Constraints(Row(capped))["allowed_values"]);
        Check(Equals(allowedValues["total_count"], 101) && Equals(allowedValues["returned_count"], 100)
            && Rows(allowedValues["values"]).Count == 100 && Flag(allowedValues, "truncated"),
            "allowed values retain total count and cap output at 100");
        Check(Text(Constraints(Row(capped)), "status") == "truncated" && !Flag(capped, "constraints_complete"),
            "item truncation is reported at both attribute and page levels");

        Reset(); context = new E3d21AiBridge.E3d21Context();
        plan = Plan("HEIG"); plan.ValuesDefined = true;
        plan.Values = new[] {
            new KeyValuePair<string, string>(new string('a', 1023) + "\U0001F600" + "z", new string('d', 1024)),
            new KeyValuePair<string, string>("ordinary", new string('x', 1025)) };
        Dictionary<string, object> bounded = Schema(context, new[] { "HEIG" });
        allowedValues = Dict(Constraints(Row(bounded))["allowed_values"]);
        Dictionary<string, object> firstValue = Dict(Rows(allowedValues["values"])[0]);
        Dictionary<string, object> secondValue = Dict(Rows(allowedValues["values"])[1]);
        string boundedKey = Text(firstValue, "value");
        Check(boundedKey.Length == 1023 && !char.IsHighSurrogate(boundedKey[boundedKey.Length - 1])
            && Flag(firstValue, "value_truncated"), "1024-character cut backs off before a split surrogate pair");
        Check(Text(firstValue, "description").Length == 1024 && !Flag(firstValue, "description_truncated"),
            "text of exactly 1024 UTF-16 characters is preserved");
        Check(Text(secondValue, "description").Length == 1024 && Flag(secondValue, "description_truncated"),
            "text beyond 1024 UTF-16 characters is truncated");

        Reset(); context = new E3d21AiBridge.E3d21Context();
        plan = Plan("HEIG"); plan.RangesDefined = true;
        plan.Ranges = Enumerable.Range(0, 102).Select(i => (double)i).ToArray();
        plan.Ranges[2] = double.NaN; plan.Ranges[5] = double.PositiveInfinity;
        plan.Ranges[99] = double.NegativeInfinity; plan.Ranges[101] = double.NaN;
        Dictionary<string, object> rangesPage = Schema(context, new[] { "HEIG" });
        Dictionary<string, object> rangeResult = Dict(Constraints(Row(rangesPage))["allowed_ranges"]);
        IList<object> boundaries = Rows(rangeResult["raw_boundaries"]);
        IList<int> nonFinite = (IList<int>)rangeResult["non_finite_indices"];
        Check(Equals(rangeResult["total_count"], 102) && Equals(rangeResult["returned_count"], 100)
            && Flag(rangeResult, "truncated"), "range output preserves total and caps at 100 boundaries");
        Check(boundaries[2] as string == "NaN" && boundaries[5] as string == "Infinity"
            && boundaries[99] as string == "-Infinity" && nonFinite.Count == 3
            && nonFinite[0] == 2 && nonFinite[1] == 5 && nonFinite[2] == 99,
            "non-finite range boundaries become strings with page-relative indices");
        Check(Flag(rangeResult, "defined") && Text(rangeResult, "unit_basis") == "unknown",
            "raw range encoding remains defined while its unit basis is explicit as unknown");

        Reset(); context = new E3d21AiBridge.E3d21Context();
        plan = Plan("HEIG"); plan.FaultOnValues = true;
        bool nativeFaultStopped = false;
        try { Schema(context, new[] { "HEIG" }); }
        catch (E3d21AiBridge.NativeDatabaseFaultException) { nativeFaultStopped = true; }
        Check(nativeFaultStopped && ConstraintCalls.Count == 1 && ConstraintCalls[0] == "HEIG.values",
            "NativeRead latch stops before ranges or optionality can read the SDK");

        Console.WriteLine("Schema constraints production checks passed: " + assertions + " assertions.");
    }
}

internal static class SchemaConstraintsProgram
{
    public static int Main()
    {
        try { SchemaConstraintsScenario.Run(); return 0; }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        [ThreadStatic] private static long permittedNativeFaultGeneration;

        private static bool IsCommandActive() { return false; }
        private static bool IsUsable(DbElement candidate)
        { return candidate != null && candidate.IsValid && !candidate.IsNull; }
        private static DbElement ResolveElement(string refno)
        {
            SchemaConstraintsScenario.RequireDispatch();
            SchemaConstraintsScenario.ResolveCalls++;
            return new DbElement(SchemaConstraintsScenario.ResolvedType);
        }
        private static Dictionary<string, object> ElementIdentity(DbElement candidate)
        { return new Dictionary<string, object> { ["refno"] = "=1/1", ["type"] = candidate.TypeName }; }
        private static string ElementRef(DbElement candidate) { return "=1/1"; }
        private static string ElementType(DbElement candidate) { return candidate.TypeName; }
        private static DbElement ConnectionResolveImplicitTubeElement(int[] pair, DbElement anchor)
        {
            SchemaConstraintsScenario.RequireDispatch();
            SchemaConstraintsScenario.AliasResolutionCalls++;
            if (pair == null || pair.Length != 2 || pair[0] != 1 || pair[1] != 1 || anchor.TypeName != "BRAN")
                throw new InvalidOperationException("Unexpected TUBI anchor.");
            if (SchemaConstraintsScenario.AliasResolverFails)
                throw new InvalidOperationException("TUBI alias resolution failed.");
            return new DbElement("TUBI");
        }
        private T OnUi<T>(Func<T> operation)
        {
            SchemaConstraintsScenario.Dispatches++;
            currentNativeFaultState = SchemaConstraintsScenario.FaultState;
            permittedNativeFaultGeneration = 0;
            try
            {
                SchemaConstraintsScenario.RequireDispatchStart();
                return operation();
            }
            finally
            {
                SchemaConstraintsScenario.EndDispatch();
                currentNativeFaultState = null;
                permittedNativeFaultGeneration = 0;
            }
        }

        // PRODUCTION_NATIVE_READ_METHODS
        // PRODUCTION_EXPECTED_TYPE_METHODS
    }
}
