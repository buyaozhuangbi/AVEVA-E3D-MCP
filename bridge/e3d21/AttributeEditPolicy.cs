using System;
using System.Globalization;

namespace E3d21AiBridge;

internal static class AttributeEditPolicy
{
    public static string InputKind(string attribute)
    {
        switch (attribute)
        {
            case "NAME": case "DESC": case "DUTY":
                return "string";
            case "DIAM": case "HEIG": case "XLEN": case "YLEN": case "ZLEN": case "DTOP": case "DBOT":
                return "finite_number";
            default:
                return null;
        }
    }

    public static object Parse(string attribute, string value)
    {
        string kind = InputKind(attribute);
        if (kind == null)
            throw new ArgumentException("Attribute is not enabled for direct MCP editing: " + attribute);
        if (kind == "string")
            return value ?? "";
        double numeric;
        if (!double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out numeric)
            || double.IsNaN(numeric) || double.IsInfinity(numeric))
            throw new ArgumentException("Numeric attribute requires a finite number in invariant notation: " + attribute);
        return numeric;
    }

    public static bool SupportsSdkType(string attribute, string sdkType, bool isArray)
    {
        string kind = InputKind(attribute);
        // E3D describes text storage (e.g. NAME) as STRING + IsArray=true.
        // STRINGARRAY is a distinct SDK type and must not use a scalar setter.
        return (kind == "string" && sdkType == "STRING")
            || (kind == "finite_number" && sdkType == "DOUBLE" && !isArray);
    }
}
