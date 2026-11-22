using System;
using System.Collections.Generic;
using Aveva.Core.Database;
using Aveva.Core.Geometry;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    public object GetGeometryBounds(string refno)
    {
        string requestedRefno = (refno ?? "").Trim();
        if (requestedRefno.Length == 0)
            throw new ArgumentException("refno must explicitly identify the element whose geometry bounds are required.", "refno");
        if (IsCommandActive())
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

        return OnUi(delegate
        {
            if (IsCommandActive())
                throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
            DbElement element = ResolveElement(requestedRefno);
            if (!IsUsable(element))
                throw new InvalidOperationException("Element was not found: " + requestedRefno);

            return ReadGeometryBoundsOnUi(element);
        });
    }

    private static Dictionary<string, object> ReadGeometryBoundsOnUi(DbElement element)
    {
        Dictionary<string, string> errors = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        Dictionary<string, string> nativeErrors = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        Dictionary<string, object> nativeValues = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
        Dictionary<string, object> result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
        {
            ["success"] = false,
            ["available"] = false,
            ["partial"] = false,
            ["refno"] = ElementRef(element),
            ["type"] = ElementType(element),
            ["min"] = null,
            ["max"] = null,
            ["unit"] = "mm",
            ["coordinate_frame"] = "SDK native (unverified)",
            ["source"] = "E3D SDK Spatial.LimitsBox",
            ["errors"] = errors,
            ["native_attributes"] = nativeValues,
            ["native_attribute_errors"] = nativeErrors
        };

        try
        {
            Spatial spatial = NativeRead(() => Spatial.Instance);
            if (spatial == null)
                throw new InvalidOperationException("The E3D spatial query service is unavailable.");
            LimitsBox bounds = null;
            bool returnedBounds = NativeRead(() => spatial.LimitsBox(element, out bounds));
            result["sdk_returned_bounds"] = returnedBounds;
            if (!returnedBounds || bounds == null)
                throw new InvalidOperationException("The E3D SDK did not return geometry bounds for this element.");

            Position minimum = NativeRead(() => bounds.Minimum);
            if (minimum == null)
                throw new InvalidOperationException("The E3D SDK returned geometry bounds without a minimum point.");
            double[] min = FiniteVector(NativeRead(() => minimum.X), NativeRead(() => minimum.Y), NativeRead(() => minimum.Z));
            result["min"] = min;
            Position maximum = NativeRead(() => bounds.Maximum);
            if (maximum == null)
                throw new InvalidOperationException("The E3D SDK returned geometry bounds without a maximum point.");
            double[] max = FiniteVector(NativeRead(() => maximum.X), NativeRead(() => maximum.Y), NativeRead(() => maximum.Z));
            result["max"] = max;
            for (int axis = 0; axis < 3; axis++)
                if (max[axis] < min[axis])
                    throw new InvalidOperationException("The E3D SDK returned inverted geometry bounds.");

            result["available"] = true;
            result["success"] = true;
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            errors["bounds"] = ex.Message;
            result["reason"] = "bounds_read_failed";
        }

        // Native formatted attributes are independent diagnostics. Their units,
        // layout and availability come from the SDK, not the min/max contract.
        DbAttribute[] nativeAttributes = { DbAttributeInstance.CABBOX, DbAttributeInstance.WBOX, DbAttributeInstance.CSGBOX };
        string[] nativeNames = { "CABBOX", "WBOX", "CSGBOX" };
        for (int index = 0; index < nativeNames.Length; index++)
        {
            string name = nativeNames[index];
            try
            {
                if (!NativeRead(() => element.IsAttributeValid(nativeAttributes[index])))
                    continue;
                nativeValues[name] = NativeRead(() => element.GetAsString(nativeAttributes[index]));
            }
            catch (Exception ex)
            {
                ThrowIfNativeFaulted();
                nativeErrors[name] = ex.Message;
            }
        }
        result["partial"] = (!(bool)result["available"] &&
            (result["min"] != null || result["max"] != null || nativeValues.Count > 0)) || nativeErrors.Count > 0;
        return result;
    }
}
