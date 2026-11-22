using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Globalization;
using System.Linq;
using E3d21AiBridge;

internal sealed class DbAttribute
{
    public readonly string Name;
    public bool IsValid = true;
    public DbAttribute(string name) { Name = name; }
    public static DbAttribute GetDbAttribute(string name)
    {
        ValidationContext.RequireDispatch();
        ValidationContext.Calls.Add("attribute." + name);
        return name == "UNKNOWN" ? null : new DbAttribute(name);
    }
}

internal static class DbAttributeInstance
{
    public static readonly DbAttribute XLEN = new DbAttribute("XLEN");
    public static readonly DbAttribute YLEN = new DbAttribute("YLEN");
    public static readonly DbAttribute ZLEN = new DbAttribute("ZLEN");
    public static readonly DbAttribute DIAM = new DbAttribute("DIAM");
    public static readonly DbAttribute HEIG = new DbAttribute("HEIG");
}

internal enum DbDimension { DIST, ANGLE }

internal sealed class DbDoubleDimension
{
    public bool Set = true;
    public DbDimension StandardDimension = DbDimension.DIST;
}

internal sealed class DbDoubleUnits
{
    public bool Set = true;
    public string Name;
    public double Scale;
    public DbDoubleDimension Dimension;
    public static DbDoubleUnits GetUnits(string name, DbDoubleDimension dimension)
    {
        ValidationContext.RequireDispatch();
        ValidationContext.Calls.Add("units." + name);
        if (ValidationContext.UnitReadFails) throw new InvalidOperationException("Unit lookup failed");
        return new DbDoubleUnits { Name = name, Scale = name == "mm" ? 1 : 0, Dimension = dimension };
    }
}

internal sealed class DbDouble
{
    public double Value;
    public DbDoubleUnits Units;
    public DbDoubleDimension Dimension;
    public DbDouble ConvertUnits(DbDoubleUnits target)
    {
        ValidationContext.RequireDispatch();
        ValidationContext.Calls.Add("convert." + target.Name);
        if (ValidationContext.ConversionFails) throw new InvalidOperationException("Unit conversion failed");
        if (Units == null || target == null || target.Scale == 0) return null;
        return new DbDouble { Value = Value * Units.Scale / target.Scale, Units = target, Dimension = Dimension };
    }
}

internal sealed class FakeType
{
    public string ShortName;
    public string Name;
}

internal sealed class DbElement
{
    public string Ref;
    public string Type;
    public DbElement Owner;
    public bool IsNull;
    public bool IsValid = true;
    public bool TypeReadFails;
    public int MemberReadFailure;
    public string FailedAttribute;
    public string FaultAttribute;
    public bool DimensionUnavailable;
    public bool WrongDimension;
    public readonly Dictionary<string, string> Values = new Dictionary<string, string>();
    public readonly List<DbElement> Children = new List<DbElement>();

    public FakeType GetElementType()
    {
        ValidationContext.RequireDispatch();
        if (TypeReadFails) throw new InvalidOperationException("Type read failed");
        return new FakeType { ShortName = Type, Name = Type };
    }

    public string GetAsString(DbAttribute attribute)
    {
        ValidationContext.RequireDispatch();
        if (FailedAttribute == attribute.Name) throw new InvalidOperationException("Dimension read failed");
        if (FaultAttribute == attribute.Name) ValidationContext.NativeFault = true;
        string value;
        return Values.TryGetValue(attribute.Name, out value) ? value : "";
    }

    public bool IsAttributeValid(DbAttribute attribute)
    {
        ValidationContext.RequireDispatch();
        ValidationContext.Calls.Add("valid." + attribute.Name);
        return Values.ContainsKey(attribute.Name);
    }

    public DbDouble GetDbDouble(DbAttribute attribute)
    {
        ValidationContext.RequireDispatch();
        ValidationContext.Calls.Add("double." + attribute.Name);
        if (FailedAttribute == attribute.Name) throw new InvalidOperationException("Dimension read failed");
        if (FaultAttribute == attribute.Name) ValidationContext.NativeFault = true;
        if (DimensionUnavailable) return null;
        string raw;
        if (!Values.TryGetValue(attribute.Name, out raw)) throw new InvalidOperationException("Dimension unset");
        raw = raw.Trim();
        double scale = 1;
        if (raw.EndsWith("mm", StringComparison.OrdinalIgnoreCase)) raw = raw.Substring(0, raw.Length - 2).TrimEnd();
        else if (raw.EndsWith("in", StringComparison.OrdinalIgnoreCase))
        {
            raw = raw.Substring(0, raw.Length - 2).TrimEnd();
            scale = 25.4;
        }
        double value;
        if (!double.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out value))
            throw new FormatException("Malformed dimension");
        var dimension = new DbDoubleDimension { StandardDimension = WrongDimension ? DbDimension.ANGLE : DbDimension.DIST };
        var units = new DbDoubleUnits { Name = scale == 1 ? "mm" : "in", Scale = scale, Dimension = dimension };
        return new DbDouble { Value = value, Units = units, Dimension = dimension };
    }

    public DbElement Member(int index)
    {
        ValidationContext.RequireDispatch();
        ValidationContext.MemberReads++;
        if (MemberReadFailure == index) throw new InvalidOperationException("Member read failed");
        return index <= Children.Count ? Children[index - 1] : null;
    }

    public void Add(DbElement child) { child.Owner = this; Children.Add(child); }
}

internal sealed class ValidationContext
{
    public static bool NativeFault;
    public static bool UnitReadFails;
    public static bool ConversionFails;
    public static readonly List<string> Calls = new List<string>();
    public static int MemberReads;
    private static bool inDispatch;
    public DbElement Root;
    public int DispatchCount;
    public int ElementInfoReads;
    public int SnapshotCalls;

    public static void RequireDispatch()
    {
        if (!inDispatch) throw new InvalidOperationException("SDK call outside the UI callback");
    }

    public static void ResetTrace()
    {
        Calls.Clear();
        NativeFault = false;
    }

    private static void ThrowIfNativeFaulted()
    {
        if (NativeFault) throw new InvalidOperationException("Native database fault");
    }

    private static bool IsCommandActive() { return false; }
    private DbElement ResolveElement(string reference) { RequireDispatch(); return Root; }
    private static bool IsUsable(DbElement element) { return element != null && element.IsValid && !element.IsNull; }
    private static string ElementRef(DbElement element) { return element == null ? "" : element.Ref; }

    private T OnUi<T>(Func<T> operation)
    {
        if (inDispatch) throw new InvalidOperationException("Nested dispatch");
        DispatchCount++;
        inDispatch = true;
        try { ThrowIfNativeFaulted(); return operation(); }
        finally { inDispatch = false; }
    }

    private Dictionary<string, object> ElementInfo(DbElement element)
    {
        RequireDispatch();
        ElementInfoReads++;
        return new Dictionary<string, object> { ["refno"] = element.Ref };
    }

    private Dictionary<string, object> BuildSnapshot(BoundedHierarchy<DbElement> hierarchy, int depth, int limit)
    {
        RequireDispatch();
        SnapshotCalls++;
        foreach (DbElement element in hierarchy.Elements) ElementInfo(element);
        return new Dictionary<string, object> { ["snapshot_info"] = HierarchyInfo(hierarchy, depth, limit) };
    }

    // PRODUCTION_METHODS
}

internal sealed class FakeRequest
{
    public readonly FakeRequest Request;
    public readonly NameValueCollection QueryString;
    public FakeRequest(NameValueCollection query) { Request = this; QueryString = query; }
}

internal static class ValidationRoute
{
    public static Dictionary<string, object> Invoke(ValidationContext context, NameValueCollection parameters)
    {
        var request = new FakeRequest(parameters);
        object response;
        // PRODUCTION_VALIDATE_ROUTE
        return (Dictionary<string, object>)response.GetType().GetProperty("validation").GetValue(response, null);
    }
}

internal static class Program
{
    private static int checks;
    private static void Check(bool value, string message)
    {
        checks++;
        if (!value)
        {
            Console.Error.WriteLine("assertion failed: " + message);
            throw new InvalidOperationException(message);
        }
    }

    private static DbElement Element(string reference, string type)
    {
        var element = new DbElement { Ref = reference, Type = type };
        foreach (string name in new[] { "XLEN", "YLEN", "ZLEN", "DIAM", "HEIG", "DTOP", "DBOT", "RADI" })
            element.Values[name] = "10mm";
        return element;
    }

    private static Dictionary<string, object> Validate(ValidationContext context, int limit = 2000, bool includeSnapshot = false,
        params string[] requiredTypes)
    {
        ValidationContext.ResetTrace();
        return (Dictionary<string, object>)context.ValidateModel(context.Root.Ref, 8, limit, requiredTypes, includeSnapshot);
    }

    private static void CheckSupportedShape(string type, string[] attributes)
    {
        var result = Validate(new ValidationContext { Root = Element("=3/1", type) });
        string[] readAttributes = ValidationContext.Calls
            .Where(call => call.StartsWith("attribute.", StringComparison.Ordinal))
            .Select(call => call.Substring("attribute.".Length)).ToArray();
        Check((bool)result["valid"] && (bool)result["checks_complete"] && readAttributes.SequenceEqual(attributes),
            type + " must use its published dimension attributes");
    }

    private static bool HasIssue(Dictionary<string, object> result, string fragment)
    {
        return ((List<string>)result["issues"]).Any(issue => issue.Contains(fragment));
    }

    private static void CheckValidationRoute()
    {
        var context = new ValidationContext { Root = Element("=2/1", "EQUI") };
        var defaults = ValidationRoute.Invoke(context, new NameValueCollection());
        var info = (Dictionary<string, object>)defaults["snapshot_info"];
        Check((int)info["depth"] == 4 && (int)info["limit"] == 200 && (bool)defaults["snapshot_included"] &&
            context.DispatchCount == 1, "The HTTP validation route must preserve default depth, limit and snapshot");
        var explicitParameters = new NameValueCollection
        {
            { "refno", "=2/1" }, { "depth", "8" }, { "limit", "2000" },
            { "include_snapshot", "false" }, { "required_types", "EQUI;BOX" }
        };
        var compact = ValidationRoute.Invoke(context, explicitParameters);
        Check(!(bool)compact["snapshot_included"] && compact["snapshot"] == null &&
            (int)((Dictionary<string, object>)compact["snapshot_info"])["limit"] == 2000 &&
            ((List<string>)compact["issues"]).SequenceEqual(new[] { "Missing required element type: BOX" }),
            "The HTTP validation route must forward 2000, false and the required-type list");

        var invalidQueries = new[]
        {
            new NameValueCollection { { "unknown", "1" } },
            new NameValueCollection { { "limit", "200" }, { "limit", "200" } },
            new NameValueCollection { { "include_snapshot", "false" }, { "include_snapshot", "true" } },
            new NameValueCollection { { "refno", "=2/1" }, { "refno", "=2/1" } },
            new NameValueCollection { { "required_types", "EQUI" }, { "required_types", "BOX" } },
            new NameValueCollection { { "depth", "4" }, { "depth", "4" } },
            new NameValueCollection { { "depth", "9" } },
            new NameValueCollection { { "depth", "-1" } },
            new NameValueCollection { { "depth", "1.5" } },
            new NameValueCollection { { "limit", "2001" } },
            new NameValueCollection { { "limit", "0" } },
            new NameValueCollection { { "limit", "1.0" } },
            new NameValueCollection { { "limit", "" } },
            new NameValueCollection { { "limit", "2147483648" } },
            new NameValueCollection { { "limit", " 200" } },
            new NameValueCollection { { "include_snapshot", "False" } },
            new NameValueCollection { { "include_snapshot", "0" } },
            new NameValueCollection { { "include_snapshot", "" } },
        };
        foreach (NameValueCollection query in invalidQueries)
        {
            var untouched = new ValidationContext { Root = Element("=2/1", "EQUI") };
            bool rejected = false;
            try { ValidationRoute.Invoke(untouched, query); }
            catch (ArgumentException) { rejected = true; }
            Check(rejected && untouched.DispatchCount == 0, "Malformed validation queries must fail before SDK dispatch");
        }
    }

    public static void Main()
    {
        CheckValidationRoute();
        var root = Element("=1/1", "EQUI");
        for (int index = 2; index <= 1001; index++) root.Add(Element("=1/" + index, "BOX"));
        var context = new ValidationContext { Root = root };
        ValidationContext.MemberReads = 0;
        var exact = Validate(context, 1001);
        Check((bool)exact["valid"] && (bool)exact["complete"] && (bool)exact["checks_complete"] &&
            (int)exact["element_count"] == 1001, "A 1001-element model must validate completely");
        Check(context.DispatchCount == 1 && context.ElementInfoReads == 1 && context.SnapshotCalls == 0 &&
            exact["snapshot"] == null && !(bool)exact["snapshot_included"], "Compact validation must use one dispatch and avoid child snapshot reads");
        Check((string)exact["consistency"] == "single_ui_dispatch" &&
            ((string)exact["consistency_scope"]).Contains("not a database lock"), "Consistency must explain the UI callback boundary");
        Check(ValidationContext.MemberReads == 2001, "Indexed traversal must read each child and each leaf terminator without materializing siblings");

        var limited = Validate(context, 1000, false, "NOZZ");
        Check(limited["valid"] == null && !(bool)limited["complete"] && (int)limited["element_count"] == 1000 &&
            ((List<string>)limited["unverified_required_types"]).SequenceEqual(new[] { "NOZZ" }) &&
            ((List<string>)limited["issues"]).Count == 0, "Truncated validation cannot certify an absent required type");

        var snapshotContext = new ValidationContext { Root = Element("=1/2", "BOX") };
        var snapshot = (Dictionary<string, object>)snapshotContext.ValidateModel("=1/2", 8, 2000, new string[0]);
        Check((bool)snapshot["snapshot_included"] && snapshot["snapshot"] != null && snapshotContext.SnapshotCalls == 1,
            "Omitting the new argument must preserve the legacy snapshot");

        CheckSupportedShape("BOX", new[] { "XLEN", "YLEN", "ZLEN" });
        CheckSupportedShape("NBOX", new[] { "XLEN", "YLEN", "ZLEN" });
        CheckSupportedShape("CYLI", new[] { "DIAM", "HEIG" });
        CheckSupportedShape("CYLINDER", new[] { "DIAM", "HEIG" });
        CheckSupportedShape("NCYL", new[] { "DIAM", "HEIG" });
        CheckSupportedShape("CONE", new[] { "DTOP", "DBOT", "HEIG" });
        CheckSupportedShape("NCON", new[] { "DTOP", "DBOT", "HEIG" });
        CheckSupportedShape("DISH", new[] { "DIAM", "HEIG", "RADI" });

        var rulesResult = Validate(new ValidationContext { Root = Element("=1/3", "EQUI") });
        var rules = (List<object>)rulesResult["dimension_check_rules"];
        var ruleTypes = rules.SelectMany(rule => (string[])((Dictionary<string, object>)rule)["types"]).ToList();
        var nozzleRule = (Dictionary<string, object>)rules.Single(rule =>
            ((string[])((Dictionary<string, object>)rule)["types"]).Contains("NOZZ"));
        Check(new[] { "BOX", "NBOX", "CYLI", "NCYL", "CYLINDER", "CONE", "NCON", "DISH", "NOZZ" }
                .All(type => ruleTypes.Contains(type)) && (string)nozzleRule["status"] == "partial" &&
            ((string[])nozzleRule["unsupported_attributes"]).Contains("DIAM"),
            "The validation response must expose the supported shape and partial nozzle rules");

        foreach (string field in new[] { "XLEN", "YLEN", "ZLEN" })
        {
            foreach (string invalid in new[] { "0mm", "-1mm" })
            {
                var box = Element("=1/4", "BOX");
                box.Values[field] = invalid;
                var result = Validate(new ValidationContext { Root = box });
                Check((bool)result["valid"] == false && HasIssue(result, "invalid " + field),
                    "BOX must reject nonpositive " + field);
            }
        }
        foreach (string field in new[] { "DIAM", "HEIG" })
        {
            foreach (string invalid in new[] { "0mm", "-1mm" })
            {
                var cylinder = Element("=1/5", "CYLI");
                cylinder.Values[field] = invalid;
                var result = Validate(new ValidationContext { Root = cylinder });
                Check((bool)result["valid"] == false && HasIssue(result, "invalid " + field),
                    "CYLI must reject nonpositive " + field);
            }
        }

        var coneZeroEnd = Element("=1/6", "CONE");
        coneZeroEnd.Values["DTOP"] = "0mm";
        Check((bool)Validate(new ValidationContext { Root = coneZeroEnd })["valid"],
            "A cone may have one zero end diameter");
        var coneBothZero = Element("=1/7", "NCON");
        coneBothZero.Values["DTOP"] = "0mm";
        coneBothZero.Values["DBOT"] = "0mm";
        var bothZeroResult = Validate(new ValidationContext { Root = coneBothZero });
        Check((bool)bothZeroResult["valid"] == false && HasIssue(bothZeroResult, "cannot both be zero"),
            "A cone with both end diameters zero must be invalid");
        foreach (string field in new[] { "DTOP", "DBOT" })
        {
            var cone = Element("=1/8", "CONE");
            cone.Values[field] = "-1mm";
            var result = Validate(new ValidationContext { Root = cone });
            Check((bool)result["valid"] == false && HasIssue(result, "invalid " + field),
                "A cone must reject negative " + field);
        }
        var coneZeroHeight = Element("=1/9", "CONE");
        coneZeroHeight.Values["HEIG"] = "0mm";
        Check((bool)Validate(new ValidationContext { Root = coneZeroHeight })["valid"] == false,
            "A cone must reject zero height");

        var dishZeroRadius = Element("=1/10", "DISH");
        dishZeroRadius.Values["RADI"] = "0mm";
        Check((bool)Validate(new ValidationContext { Root = dishZeroRadius })["valid"],
            "DISH must allow a zero radius under the published MCP policy");
        var dishNegativeRadius = Element("=1/11", "DISH");
        dishNegativeRadius.Values["RADI"] = "-1mm";
        var negativeRadius = Validate(new ValidationContext { Root = dishNegativeRadius });
        Check((bool)negativeRadius["valid"] == false && HasIssue(negativeRadius, "invalid RADI"),
            "DISH must reject a negative radius");
        foreach (string field in new[] { "DIAM", "HEIG" })
        {
            var dish = Element("=1/12", "DISH");
            dish.Values[field] = "0mm";
            Check((bool)Validate(new ValidationContext { Root = dish })["valid"] == false,
                "DISH must reject zero " + field);
        }

        var inchBox = Element("=1/13", "BOX");
        inchBox.Values["XLEN"] = "-1in";
        var inchResult = Validate(new ValidationContext { Root = inchBox });
        Check((bool)inchResult["valid"] == false && HasIssue(inchResult, "-25.4 mm") &&
            ValidationContext.Calls.Contains("units.mm") && ValidationContext.Calls.Contains("convert.mm"),
            "Dimension issues must report the explicit conversion to millimetres");

        var missingCylinder = Element("=1/14", "CYLI");
        missingCylinder.Values.Remove("DIAM");
        var missingContext = new ValidationContext { Root = missingCylinder };
        var missingResult = Validate(missingContext);
        var missingErrors = (List<object>)missingResult["check_read_errors"];
        Check((bool)missingResult["complete"] && !(bool)missingResult["checks_complete"] &&
            missingResult["valid"] == null && ((List<string>)missingResult["issues"]).Count == 0 &&
            missingErrors.Count == 1 && (string)((Dictionary<string, object>)missingErrors[0])["attribute"] == "DIAM",
            "A missing supported field must remain partial and must not be invented as zero");
        Check(ValidationContext.Calls.Contains("valid.DIAM") && !ValidationContext.Calls.Contains("double.DIAM"),
            "An unset attribute must stop before reading a DbDouble");

        var nullDimension = Element("=1/15", "CYLI");
        nullDimension.DimensionUnavailable = true;
        var nullResult = Validate(new ValidationContext { Root = nullDimension });
        Check(nullResult["valid"] == null && ((List<string>)nullResult["issues"]).Count == 0 &&
            ((List<object>)nullResult["check_read_errors"]).Count == 2,
            "Null dimension values must remain unreadable rather than becoming zero");

        var coneOneUnknown = Element("=1/16", "CONE");
        coneOneUnknown.Values["DTOP"] = "0mm";
        coneOneUnknown.Values.Remove("DBOT");
        var oneUnknownResult = Validate(new ValidationContext { Root = coneOneUnknown });
        Check(oneUnknownResult["valid"] == null && ((List<string>)oneUnknownResult["issues"]).Count == 0 &&
            ((List<object>)oneUnknownResult["check_read_errors"]).Count == 1,
            "One known zero cone endpoint and one unknown endpoint must not be rejected as both zero");
        var coneOneFailedRead = Element("=1/16", "CONE");
        coneOneFailedRead.Values["DTOP"] = "0mm";
        coneOneFailedRead.FailedAttribute = "DBOT";
        var failedEndpoint = Validate(new ValidationContext { Root = coneOneFailedRead });
        Check(failedEndpoint["valid"] == null && ((List<string>)failedEndpoint["issues"]).Count == 0 &&
            ((List<object>)failedEndpoint["check_read_errors"]).Count == 1,
            "A failed cone endpoint read beside one known zero must remain unverified, not invalid");
        var coneUnknown = Element("=1/17", "CONE");
        coneUnknown.Values.Remove("DTOP");
        coneUnknown.Values.Remove("DBOT");
        var unknownEndpoints = Validate(new ValidationContext { Root = coneUnknown });
        Check(unknownEndpoints["valid"] == null && ((List<string>)unknownEndpoints["issues"]).Count == 0 &&
            ((List<object>)unknownEndpoints["check_read_errors"]).Count == 2,
            "Two unknown cone endpoints must not be treated as zero diameters");

        foreach (string nozzleType in new[] { "NOZZ", "NOZZLE" })
        {
            var nozzle = Element("=1/18", nozzleType);
            nozzle.Values.Remove("DIAM");
            var nozzleContext = new ValidationContext { Root = nozzle };
            var nozzleResult = Validate(nozzleContext);
            var nozzleUnverified = (List<object>)nozzleResult["unverified_dimensions"];
            var nozzleDimension = (Dictionary<string, object>)nozzleUnverified.Single();
            Check((bool)nozzleResult["complete"] && !(bool)nozzleResult["checks_complete"] &&
                nozzleResult["valid"] == null && nozzleResult["checked_elements_valid"] == null &&
                ((List<object>)nozzleResult["check_read_errors"]).Count == 0 &&
                (string)nozzleDimension["attribute"] == "DIAM" &&
                (string)nozzleDimension["reason"] == "unsupported_dimension_attribute" &&
                ValidationContext.Calls.Contains("double.HEIG") && !ValidationContext.Calls.Contains("attribute.DIAM"),
                nozzleType + " must check height and report DIAM as unsupported without attempting its getter");
            foreach (string invalid in new[] { "0mm", "-1mm", "NaN", "Infinity", "-Infinity" })
            {
                nozzle.Values["HEIG"] = invalid;
                var invalidNozzleHeight = Validate(new ValidationContext { Root = nozzle });
                Check(object.Equals(invalidNozzleHeight["valid"], false) &&
                    ((List<string>)invalidNozzleHeight["issues"]).Count == 1 &&
                    ((List<object>)invalidNozzleHeight["check_read_errors"]).Count == 0,
                    nozzleType + " must reject nonpositive or nonfinite height even when DIAM remains unsupported");
            }
        }

        var failedCylinder = Element("=1/19", "CYLI");
        failedCylinder.FailedAttribute = "HEIG";
        var failedDimension = Validate(new ValidationContext { Root = failedCylinder });
        Check(failedDimension["valid"] == null && !(bool)failedDimension["checks_complete"] &&
            ((List<object>)failedDimension["check_read_errors"]).Count == 1 &&
            ValidationContext.Calls.Last() == "double.HEIG",
            "An ordinary getter exception must be recorded without issuing further fake reads");
        var malformedCylinder = Element("=1/20", "CYLI");
        malformedCylinder.Values["HEIG"] = "grossly malformed";
        var malformed = Validate(new ValidationContext { Root = malformedCylinder });
        Check(malformed["valid"] == null && ((List<object>)malformed["check_read_errors"]).Count == 1 &&
            ValidationContext.Calls.Last() == "double.HEIG",
            "Malformed SDK dimension text must remain a read error without later conversion calls");
        failedCylinder.Values["DIAM"] = "0mm";
        failedCylinder.FailedAttribute = "HEIG";
        var invalidWithReadError = Validate(new ValidationContext { Root = failedCylinder });
        Check((bool)invalidWithReadError["valid"] == false && (bool)invalidWithReadError["checked_elements_valid"] == false,
            "A definite issue must remain invalid even when a separate dimension is unreadable");
        var wrongDimension = Element("=1/21", "CYLI");
        wrongDimension.WrongDimension = true;
        var wrongDimensionResult = Validate(new ValidationContext { Root = wrongDimension });
        Check(wrongDimensionResult["valid"] == null && ((List<string>)wrongDimensionResult["issues"]).Count == 0 &&
            ((List<object>)wrongDimensionResult["check_read_errors"]).Count == 2 &&
            !ValidationContext.Calls.Contains("units.mm"),
            "Angular values returned for distance fields must remain unreadable and skip conversion");

        var unknown = Element("=1/5", "EQUI");
        unknown.TypeReadFails = true;
        var unknownResult = Validate(new ValidationContext { Root = unknown }, 2000, false, "BOX");
        Check(unknownResult["valid"] == null && !(bool)unknownResult["checks_complete"] &&
            ((List<string>)unknownResult["issues"]).Count == 0 &&
            ((List<string>)unknownResult["unverified_required_types"]).SequenceEqual(new[] { "BOX" }),
            "Unreadable native types must not imply that a required type is missing");
        unknown.TypeReadFails = false;
        unknown.Type = "";
        Check(Validate(new ValidationContext { Root = unknown })["valid"] == null, "An empty native type must be unverified");

        foreach (string nonfinite in new[] { "NaN", "Infinity", "-Infinity" })
        {
            var box = Element("=1/6", "BOX");
            box.Values["XLEN"] = nonfinite;
            var nonfiniteResult = Validate(new ValidationContext { Root = box });
            Check((bool)nonfiniteResult["valid"] == false && ((List<string>)nonfiniteResult["issues"]).Count > 0 &&
                ((List<object>)nonfiniteResult["check_read_errors"]).Count == 0,
                "Nonfinite dimensions must never validate successfully");
        }
        var missingType = Validate(new ValidationContext { Root = Element("=1/8", "EQUI") }, 2000, false, "BOX");
        Check((bool)missingType["valid"] == false && ((List<string>)missingType["issues"]).Count == 1,
            "A required type absent from a fully read hierarchy must remain a definite issue");

        root.MemberReadFailure = 1001;
        var failedLookahead = Validate(new ValidationContext { Root = root }, 1001);
        Check(failedLookahead["valid"] == null && !(bool)failedLookahead["complete"] &&
            ((List<string>)((Dictionary<string, object>)failedLookahead["snapshot_info"])["read_errors"]).Count == 1,
            "A failing final Member(index) read must not be mistaken for the end of the hierarchy");

        bool rangeRejected = false;
        var untouched = new ValidationContext { Root = Element("=1/9", "EQUI") };
        try { Validate(untouched, 2001); } catch (ArgumentOutOfRangeException) { rangeRejected = true; }
        Check(rangeRejected && untouched.DispatchCount == 0, "Out-of-range limits must fail before SDK dispatch");

        var faulted = Element("=1/10", "CYLI");
        faulted.FaultAttribute = "DIAM";
        bool nativeFaultEscaped = false;
        try { Validate(new ValidationContext { Root = faulted }); }
        catch (InvalidOperationException error) { nativeFaultEscaped = error.Message == "Native database fault"; }
        finally { ValidationContext.NativeFault = false; }
        Check(nativeFaultEscaped && ValidationContext.Calls.SequenceEqual(new[] { "attribute.DIAM", "valid.DIAM", "double.DIAM" }),
            "A native fault must escape partial-check handling without further SDK reads");
        Console.WriteLine("Model validation production checks passed: " + checks);
    }
}
