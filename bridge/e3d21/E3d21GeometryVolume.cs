using System;
using System.Collections.Generic;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    public object GetGeometryVolume(string refno, string scope)
    {
        string target = ModelEditInput.ExplicitReference(refno, "refno");
        string requestedScope = (scope ?? "element").Trim().ToLowerInvariant();
        if (requestedScope != "element" && requestedScope != "members")
            throw new ArgumentException("scope must be element or members.", "scope");
        if (IsCommandActive())
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        return OnUi(delegate
        {
            if (IsCommandActive())
                throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
            DbElement element = ModelRequireElement(target);
            return ReadGeometryVolumeOnUi(element, requestedScope);
        });
    }

    private static Dictionary<string, object> ReadGeometryVolumeOnUi(DbElement element, string requestedScope)
    {
        string nativeQualifier = requestedScope == "element" ? "ONLY" : "ALL";
        if (NativeRead(() => element.DbType) != DbType.Design)
            throw new InvalidOperationException("refno must identify an element in a Design database.");
        string ceBefore = ModelCurrentReference();
        DbElementType baseType = NativeRead(() => element.GetElementType());
        if (ReferenceEquals(baseType, null))
            throw new InvalidOperationException("The SDK returned no base element type for volume interpretation.");
        DbElementType negativeCylinder = NativeRead(() => DbElementTypeInstance.NCYLINDER);
        DbElementType negativeBox = NativeRead(() => DbElementTypeInstance.NBOX);
        DbElementType negativeCone = NativeRead(() => DbElementTypeInstance.NCONE);
        bool knownNegative = NativeRead(() => baseType == negativeCylinder) ||
            NativeRead(() => baseType == negativeBox) || NativeRead(() => baseType == negativeCone);
        // This exception is deliberately limited to these exact SDK base types.
        // Other types retain the existing pair check; that is not a claim that
        // every other negative family has positive-material volume semantics.
        Dictionary<string, object> gross = GeometryReadVolume(element, DbAttributeInstance.GVOL, nativeQualifier);
        Dictionary<string, object> net = GeometryReadVolume(element, DbAttributeInstance.NVOL, nativeQualifier);
        var errors = new Dictionary<string, string>();
        if (!Equals(gross["available"], true)) errors["GVOL"] = (string)gross["error"];
        if (!Equals(net["available"], true)) errors["NVOL"] = (string)net["error"];
        bool? coherent = null;
        double? removed = null;
        double? pairTolerance = null;
        bool valuesAvailable = Equals(gross["available"], true) && Equals(net["available"], true);
        if (valuesAvailable && !knownNegative)
        {
            double grossValue = (double)gross["value_mm3"];
            double netValue = (double)net["value_mm3"];
            double tolerance = Math.Max(0.000001, Math.Max(grossValue, netValue) * 0.000000001);
            pairTolerance = tolerance;
            coherent = netValue <= grossValue + tolerance;
            if (coherent == true)
                removed = Math.Max(0, grossValue - netValue);
            else
                errors["volume_pair"] = "Native NVOL exceeds GVOL beyond the numerical tolerance; removed volume is unavailable.";
        }
        var result = new Dictionary<string, object>
        {
            ["success"] = false,
            ["read_complete"] = false,
            ["element"] = MeasurementElementIdentity(element),
            ["scope"] = requestedScope,
            ["native_qualifier"] = nativeQualifier,
            ["unit"] = "mm3",
            ["gross"] = gross,
            ["net"] = net,
            ["volume_pair_applicable"] = !knownNegative,
            ["volume_pair_reason"] = knownNegative ? "negative_primitive_not_material_pair" : "existing_gross_net_pair_check",
            ["volume_pair_coherent"] = coherent,
            ["volume_pair_tolerance_mm3"] = pairTolerance,
            ["removed_volume_mm3"] = removed,
            ["read_errors"] = errors,
            ["ce_before"] = ceBefore,
            ["ce_after"] = null,
            ["ce_unchanged"] = null,
            ["source"] = "Public SDK GetDbDouble(GVOL/NVOL, DbQualifier) with explicit volume-unit conversion.",
            ["measurement_scope"] = knownNegative
                ? "Native GVOL/NVOL for the explicitly requested NCYLINDER, NBOX or NCONE and qualifier. The positive-material gross/net relationship is not applicable. These values do not establish material removed from the owner or another element; removed volume remains unavailable."
                : "E3D native mass-volume geometry at the current representation settings. GVOL ignores negatives; NVOL considers negatives. ONLY measures the element; ALL includes its members. A gross/net difference measures the total native removal and does not identify an individual hole. The negative-type exception is limited to NCYLINDER, NBOX and NCONE; other types retain the existing pair check."
        };
        ModelCaptureCeAfter(result);
        if (!Equals(result["ce_unchanged"], true))
            errors["current_element"] = "Current element could not be verified unchanged during the read.";
        result["read_complete"] = valuesAvailable && Equals(result["ce_unchanged"], true);
        result["success"] = Equals(result["read_complete"], true) && (knownNegative || coherent == true);
        return result;
    }

    private static Dictionary<string, object> GeometryReadVolume(DbElement element, DbAttribute attribute, string nativeQualifier)
    {
        ThrowIfNativeFaulted();
        var result = new Dictionary<string, object>
        {
            ["available"] = false,
            ["attribute"] = NativeRead(() => attribute.ShortName),
            ["raw_text"] = null,
            ["native_value"] = null,
            ["native_units"] = null,
            ["database_value"] = null,
            ["database_units"] = null,
            ["dimension"] = null,
            ["value_mm3"] = null,
            ["unit"] = null,
            ["converted_units"] = null,
            ["error"] = null
        };
        var qualifier = NativeRead(() => new DbQualifier());
        NativeWrite(() => qualifier.Add(nativeQualifier));
        // Pseudo attributes can be readable when IsAttributeValid reports false.
        // Keep formatted text independent of the dimensioned numerical read.
        try { result["raw_text"] = NativeRead(() => element.GetAsString(attribute, qualifier)); }
        catch (Exception ex) { ThrowIfNativeFaulted(); result["raw_text_error"] = ex.Message; }
        try
        {
            DbDouble native = NativeRead(() => element.GetDbDouble(attribute, qualifier));
            if (ReferenceEquals(native, null))
                throw new InvalidOperationException("The SDK returned no dimensioned volume value.");
            DbDoubleDimension dimension = NativeRead(() => native.Dimension);
            if (ReferenceEquals(dimension, null) || !NativeRead(() => dimension.Set))
                throw new InvalidOperationException("The SDK returned no volume dimension.");
            result["dimension"] = new Dictionary<string, object>
            {
                ["name"] = NativeRead(() => dimension.Name),
                ["standard_dimension"] = NativeRead(() => dimension.StandardDimension).ToString()
            };
            result["native_value"] = GeometryFiniteVolumeNumber(NativeRead(() => native.Value));
            result["native_units"] = GeometryVolumeUnits(NativeRead(() => native.Units));
            result["database_value"] = GeometryFiniteVolumeNumber(NativeRead(() => native.DatabaseValue));
            result["database_units"] = GeometryVolumeUnits(NativeRead(() => dimension.DatabaseUnits));
            if (NativeRead(() => dimension.StandardDimension) != DbDimension.VOLUME)
                throw new InvalidOperationException("The SDK returned a dimension other than VOLUME; no mm3 value was inferred.");
            DbDoubleUnits outputUnits = GeometryCubicMillimetreUnits(dimension);
            DbDouble converted = NativeRead(() => native.ConvertUnits(outputUnits));
            if (ReferenceEquals(converted, null) || NativeRead(() => converted.Dimension) != dimension ||
                NativeRead(() => converted.Units) != outputUnits)
                throw new InvalidOperationException("The SDK could not verify conversion to the requested cubic millimetre units.");
            double value = GeometryFiniteVolumeNumber(NativeRead(() => converted.Value));
            if (value < 0)
                throw new InvalidOperationException("The SDK returned a negative volume; no material-removal result was inferred.");
            result["value_mm3"] = value;
            result["unit"] = "mm3";
            result["converted_units"] = GeometryVolumeUnits(NativeRead(() => converted.Units));
            result["available"] = true;
        }
        catch (Exception ex) { ThrowIfNativeFaulted(); result["error"] = ex.Message; }
        return result;
    }

    private static DbDoubleUnits GeometryCubicMillimetreUnits(DbDoubleDimension dimension)
    {
        ThrowIfNativeFaulted();
        // Explicit native unit parsing; no session unit or format changes.
        DbDoubleUnits units = NativeRead(() => DbDoubleUnits.GetUnits("mm3", dimension));
        if (ReferenceEquals(units, null) || !NativeRead(() => units.Set))
            throw new InvalidOperationException("The SDK could not resolve explicit cubic millimetre units; native values and units are retained.");
        DbDoubleDimension unitDimension = NativeRead(() => units.Dimension);
        if (unitDimension != dimension || NativeRead(() => unitDimension.StandardDimension) != DbDimension.VOLUME)
            throw new InvalidOperationException("The SDK could not resolve explicit cubic millimetre units; native values and units are retained.");
        return units;
    }

    private static Dictionary<string, object> GeometryVolumeUnits(DbDoubleUnits units)
    {
        ThrowIfNativeFaulted();
        if (ReferenceEquals(units, null) || !NativeRead(() => units.Set))
            throw new InvalidOperationException("The SDK returned unset units.");
        return new Dictionary<string, object>
        {
            ["name"] = NativeRead(() => units.Name),
            ["short_name"] = NativeRead(() => units.ShortName),
            ["description"] = NativeRead(() => units.Description),
            ["factor"] = GeometryFiniteVolumeNumber(NativeRead(() => units.Factor)),
            ["standard_dimension"] = NativeRead(() => NativeRead(() => units.Dimension).StandardDimension).ToString()
        };
    }

    private static double GeometryFiniteVolumeNumber(double value)
    {
        if (double.IsNaN(value) || double.IsInfinity(value))
            throw new InvalidOperationException("The SDK returned a non-finite volume number.");
        return value;
    }
}
