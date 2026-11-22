using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core.Geometry;
using Aveva.Core.Utilities.Messaging;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private const int ConnectionPointLimit = 64;

    public object GetConnectionPoints(string refno, string wrt, IList<int> pointNumbers, string expectedType = null)
    {
        string target = ModelEditInput.ExplicitReference(refno, "refno");
        string requestedType = String.IsNullOrWhiteSpace(expectedType)
            ? null : ModelEditInput.TypeName(expectedType).ToUpperInvariant();
        string requestedFrame = (wrt ?? "WORLD").Trim();
        if (requestedFrame.Equals("WORLD", StringComparison.OrdinalIgnoreCase) ||
            requestedFrame.Equals("OWNER", StringComparison.OrdinalIgnoreCase))
            requestedFrame = requestedFrame.ToUpperInvariant();
        else requestedFrame = ModelEditInput.ExplicitReference(requestedFrame, "wrt");
        int[] requested = pointNumbers == null ? new int[0] : pointNumbers.ToArray();
        if (requested.Length > ConnectionPointLimit || requested.Any(number => number < 1 || number > 10000) ||
            requested.Distinct().Count() != requested.Length)
            throw new ArgumentException("point_numbers must contain at most 64 unique integers from 1 through 10000.");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
            Project projectBefore = NativeRead(() => Project.CurrentProject);
            MDB mdbBefore = NativeRead(() => MDB.CurrentMDB);
            Dictionary<string, object> contextBefore = ReadEndpointContext(projectBefore, mdbBefore);
            DbElement element;
            if (requestedType == null) element = ModelRequireElement(target);
            else
            {
                element = ResolveElementByType(target, requestedType);
                if (!IsUsable(element)) throw new InvalidOperationException("Element was not found: " + target);
            }
            if (NativeRead(() => element.DbType) != DbType.Design)
                throw new ArgumentException("refno must identify an element in a Design database.");
            DbElement frame = ModelFrame(requestedFrame, NativeRead(() => element.Owner));
            var errors = new Dictionary<string, string>();
            var enumeration = new Dictionary<string, object>
            {
                ["source"] = requested.Length == 0 ? "PPLS" : "requested",
                ["available"] = false, ["complete"] = false,
                ["native_list_available"] = false, ["native_list_complete"] = false,
                ["native_point_numbers"] = null, ["native_list_text"] = null,
                ["state"] = "not_read", ["error"] = null,
                ["limit"] = ConnectionPointLimit
            };
            int[] nativePoints = null;
            int[] selected = requested;
            try
            {
                // PPLS is a pseudo attribute. Preserve the formatted unset marker
                // separately from an SDK-read empty array.
                string nativeListText = NativeRead(() => element.GetAsString(DbAttributeInstance.PPLS));
                enumeration["native_list_text"] = nativeListText;
                if (string.Equals((nativeListText ?? "").Trim(), "unset", StringComparison.OrdinalIgnoreCase))
                {
                    enumeration["state"] = "unset";
                    throw new InvalidOperationException("PPLS is unset; no connection-point enumeration is inferred.");
                }
                nativePoints = NativeRead(() => element.GetIntegerArray(DbAttributeInstance.PPLS));
                enumeration["native_point_numbers"] = nativePoints;
                if (nativePoints == null)
                {
                    enumeration["state"] = "read_failed_or_null";
                    throw new InvalidOperationException("PPLS returned no integer list; no connection-point enumeration is inferred.");
                }
                enumeration["state"] = nativePoints.Length == 0 ? "empty" : "populated";
                enumeration["native_list_available"] = true;
                if (nativePoints.Length > ConnectionPointLimit)
                    throw new InvalidOperationException("PPLS exceeds the 64-point limit; supply an explicit bounded point_numbers list. The native list is retained without silently truncating it.");
                if (nativePoints.Any(number => number < 1 || number > 10000) || nativePoints.Distinct().Count() != nativePoints.Length)
                    throw new InvalidOperationException("PPLS contains duplicate or unsupported point numbers; the native list is retained without reinterpretation.");
                enumeration["native_list_complete"] = true;
                if (requested.Length == 0) selected = nativePoints;
                enumeration["available"] = true;
                enumeration["complete"] = true;
            }
            catch (Exception ex)
            {
                ThrowIfNativeFaulted();
                if (Equals(enumeration["state"], "not_read")) enumeration["state"] = "read_failed";
                enumeration["error"] = ex.Message;
                errors["enumeration"] = ex.Message;
                if (requested.Length != 0) enumeration["available"] = true;
            }

            var points = new List<Dictionary<string, object>>();
            foreach (int number in selected)
            {
                ThrowIfNativeFaulted();
                Dictionary<string, object> point = ConnectionReadPoint(element, frame, number,
                    ConnectionPointListedInPpls(nativePoints, number));
                points.Add(point);
                foreach (var error in (Dictionary<string, string>)point["errors"])
                    errors["P" + number.ToString(CultureInfo.InvariantCulture) + "." + error.Key] = error.Value;
            }
            var references = new Dictionary<string, object>();
            foreach (DbAttribute attribute in new[] { DbAttributeInstance.SPRE, DbAttributeInstance.CATR, DbAttributeInstance.CREF })
            {
                ThrowIfNativeFaulted();
                string attributeName = NativeRead(() => attribute.ShortName);
                Dictionary<string, object> reference = ConnectionReadReference(element, attribute);
                references[attributeName] = reference;
                if (reference["error"] != null) errors[attributeName] = (string)reference["error"];
            }
            var cref = (Dictionary<string, object>)references["CREF"];
            if (Equals(cref["status"], "unset"))
                cref["end"] = ConnectionUnavailable("not_applicable", null);
            else
            {
                var end = ConnectionReadField("CEND", () => new Dictionary<string, object>
                {
                    ["value"] = NativeRead(() => element.GetAsString(DbAttributeInstance.CEND))
                });
                cref["end"] = end;
                if (end["error"] != null) errors["CEND"] = (string)end["error"];
            }
            var frameIdentity = ConnectionIdentity(frame);
            frameIdentity["requested"] = requestedFrame;
            var result = new Dictionary<string, object>
            {
                ["success"] = true, ["complete"] = false,
                ["element"] = ConnectionIdentity(element), ["frame"] = frameIdentity,
                ["requested_point_numbers"] = requested, ["point_numbers"] = selected,
                ["enumeration"] = enumeration, ["points"] = points, ["references"] = references,
                ["errors"] = errors,
                ["source"] = "Public SDK PPLS/PPEXST and qualified PPOS/PDIR/PPBO/PPCO/PCREF reads; 710633 references use a constrained SDK ILEAVE TUBE lookup.",
                ["pcref_encoding_source"] = "Installed AVEVA PMLLIB/spooler/functions/splpcref.pmlfnc: four values are DBREF database, DBREF element, tube flag and point; negative point values decode by absolute value; 710633 selects ILEAVE TUBE, resolved through DbElement.Parse with the anchor as explicit CE; other values use direct DBREF resolution.",
                ["measurement_scope"] = "success means a diagnostic report was produced; complete means read coverage, not engineering connection suitability. P-points can be geometric points without a usable piping connection. Bore is the native nominal P-point bore, not inferred DN or outside diameter. PCREF encoding and CREF are distinct native evidence."
            };
            Project projectAfter = NativeRead(() => Project.CurrentProject);
            MDB mdbAfter = NativeRead(() => MDB.CurrentMDB);
            Dictionary<string, object> contextAfter = ReadEndpointContext(projectAfter, mdbAfter);
            ApplyEndpointContext(result, projectBefore, mdbBefore, contextBefore,
                projectAfter, mdbAfter, contextAfter);
            if (!Equals(result["ce_unchanged"], true))
                errors["current_element"] = "Current element could not be verified unchanged during the read.";
            if (!Equals(result["identity_unchanged"], true))
                errors["project_mdb"] = "Project/MDB identity could not be verified unchanged during the read.";
            bool nonEnumerationErrors = errors.Keys.Any(key =>
                !string.Equals(key, "enumeration", StringComparison.Ordinal));
            result["complete"] = ConnectionCoverageComplete(requested,
                Equals(enumeration["complete"], true),
                points.All(point => Equals(point["complete"], true)), nonEnumerationErrors);
            return result;
        });
    }

    public object CheckConnectionPair(string firstRefno, int firstPointNumber,
        string secondRefno, int secondPointNumber, double toleranceMm,
        string firstExpectedType = null, string secondExpectedType = null)
    {
        string firstTarget = ModelEditInput.ExplicitReference(firstRefno, "first_refno");
        string secondTarget = ModelEditInput.ExplicitReference(secondRefno, "second_refno");
        string requestedFirstType = String.IsNullOrWhiteSpace(firstExpectedType)
            ? null : ModelEditInput.TypeName(firstExpectedType).ToUpperInvariant();
        string requestedSecondType = String.IsNullOrWhiteSpace(secondExpectedType)
            ? null : ModelEditInput.TypeName(secondExpectedType).ToUpperInvariant();
        if (firstPointNumber < 1 || firstPointNumber > 10000 || secondPointNumber < 1 || secondPointNumber > 10000)
            throw new ArgumentOutOfRangeException("point_number", "Point selectors must be integers from 1 through 10000.");
        if (Double.IsNaN(toleranceMm) || Double.IsInfinity(toleranceMm) || toleranceMm < 0 || toleranceMm > 1000000)
            throw new ArgumentOutOfRangeException("tolerance_mm", "tolerance_mm must be finite and between 0 and 1000000 mm.");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
            string ceBefore = ModelCurrentReference();
            Dictionary<string, object> first = ConnectionReadPairEndpoint(firstTarget, firstPointNumber, requestedFirstType);
            Dictionary<string, object> second = ConnectionReadPairEndpoint(secondTarget, secondPointNumber, requestedSecondType);
            Dictionary<string, object> firstIdentity = ConnectionPairElementIdentity(first);
            Dictionary<string, object> secondIdentity = ConnectionPairElementIdentity(second);
            Dictionary<string, object> position = ConnectionComparePairPositions(first, second, toleranceMm);
            Dictionary<string, object> direction = ConnectionComparePairDirections(first, second);
            Dictionary<string, object> correspondence = ConnectionComparePairReferences(
                first, firstIdentity, firstPointNumber, second, secondIdentity, secondPointNumber);
            string ceAfter = ModelCurrentReference();
            bool? ceUnchanged = string.IsNullOrWhiteSpace(ceBefore) || string.IsNullOrWhiteSpace(ceAfter)
                ? (bool?)null : StringComparer.Ordinal.Equals(ceBefore, ceAfter);
            bool complete = Equals(first["complete"], true) && Equals(second["complete"], true) &&
                Equals(ceUnchanged, true);
            return new Dictionary<string, object>
            {
                ["success"] = true, ["complete"] = complete,
                ["endpoints"] = new Dictionary<string, object> { ["first"] = first, ["second"] = second },
                ["position_comparison"] = position, ["direction_relationship"] = direction,
                ["pcref_correspondence"] = correspondence,
                ["tolerance"] = new Dictionary<string, object> { ["value"] = toleranceMm, ["unit"] = "mm" },
                ["ce_before"] = ceBefore, ["ce_after"] = ceAfter, ["ce_unchanged"] = ceUnchanged,
                ["source"] = "Single E3D21 native UI-thread dispatch; public SDK PPLS-independent PPEXST and qualified PPOS/PDIR/PPBO/PPCO/PCREF reads.",
                ["measurement_scope"] = "Position and native PDIR directions are compared as geometric evidence. PCREF resolution and target correspondence are reported separately. Bore, PPCO and catalogue/connection references are preserved as native evidence; no dimensional, pressure-class, catalogue or engineering compatibility is inferred. complete means requested fields were read, not that a connection is suitable."
            };
        });
    }

    private static Dictionary<string, object> ConnectionReadPairEndpoint(string refno, int pointNumber,
        string expectedType = null)
    {
        var endpoint = new Dictionary<string, object>
        {
            ["requested_refno"] = refno, ["point_number"] = pointNumber,
            ["status"] = "unknown", ["element"] = null, ["exists"] = ConnectionUnavailable("not_read", null),
            ["point"] = null, ["catalogue_evidence"] = null, ["complete"] = false, ["errors"] = new Dictionary<string, string>()
        };
        if (!String.IsNullOrWhiteSpace(expectedType)) endpoint["requested_expected_type"] = expectedType;
        var errors = (Dictionary<string, string>)endpoint["errors"];
        try
        {
            DbElement element = String.IsNullOrWhiteSpace(expectedType)
                ? ModelRequireElement(refno) : ResolveElementByType(refno, expectedType);
            if (!IsUsable(element)) throw new InvalidOperationException("Element was not found: " + refno);
            if (NativeRead(() => element.DbType) != DbType.Design)
                throw new InvalidOperationException("The reference does not identify an element in a Design database.");
            endpoint["element"] = ConnectionIdentity(element);
            DbElement world = ModelFrame("WORLD", NativeRead(() => element.Owner));
            Dictionary<string, object> point = ConnectionReadPoint(element, world, pointNumber, null);
            endpoint["point"] = point;
            endpoint["exists"] = point["exists"];
            if (Equals(((Dictionary<string, object>)point["exists"])["available"], true))
                endpoint["status"] = Equals(((Dictionary<string, object>)point["exists"])["value"], true) ? "exists" : "not_found";
            else errors["exists"] = ((Dictionary<string, object>)point["exists"])["error"] as string ?? "P-point existence is unknown.";

            var catalogue = new Dictionary<string, object>();
            foreach (DbAttribute attribute in new[] { DbAttributeInstance.SPRE, DbAttributeInstance.CATR, DbAttributeInstance.CREF })
            {
                string name = NativeRead(() => attribute.ShortName);
                Dictionary<string, object> reference = ConnectionReadReference(element, attribute);
                catalogue[name] = reference;
                if (reference["error"] != null) errors[name] = (string)reference["error"];
            }
            Dictionary<string, object> cref = (Dictionary<string, object>)catalogue["CREF"];
            if (Equals(cref["status"], "unset"))
                cref["end"] = ConnectionUnavailable("not_applicable", null);
            else
            {
                Dictionary<string, object> end = ConnectionReadField("CEND", () => new Dictionary<string, object>
                { ["value"] = NativeRead(() => element.GetAsString(DbAttributeInstance.CEND)) });
                cref["end"] = end;
                if (end["error"] != null) errors["CEND"] = (string)end["error"];
            }
            endpoint["catalogue_evidence"] = catalogue;
            if (Equals(endpoint["status"], "not_found"))
                errors["point"] = "PPEXST reports that this P-point does not exist.";
            bool referencesRead = catalogue.Values.Cast<Dictionary<string, object>>().All(value =>
                Equals(value["available"], true) && value["error"] == null) &&
                (Equals(cref["status"], "unset") || Equals(((Dictionary<string, object>)cref["end"])["available"], true));
            endpoint["complete"] = Equals(endpoint["status"], "exists") && Equals(point["complete"], true) &&
                referencesRead && errors.Count == 0;
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            endpoint["status"] = "unknown";
            endpoint["error"] = ex.Message;
            errors["endpoint"] = ex.Message;
        }
        return endpoint;
    }

    private static Dictionary<string, object> ConnectionPairElementIdentity(Dictionary<string, object> endpoint)
    {
        Dictionary<string, object> source = endpoint["element"] as Dictionary<string, object>;
        if (source == null) return null;
        var identity = new Dictionary<string, object>(source);
        identity["status"] = endpoint["status"];
        return identity;
    }

    private static Dictionary<string, object> ConnectionComparePairPositions(
        Dictionary<string, object> first, Dictionary<string, object> second, double toleranceMm)
    {
        var result = new Dictionary<string, object>
        {
            ["status"] = "unknown", ["available"] = false, ["tolerance_mm"] = toleranceMm,
            ["delta_mm"] = null, ["distance_mm"] = null, ["within_tolerance"] = null
        };
        double[] a = ConnectionPairVector(first, "position");
        double[] b = ConnectionPairVector(second, "position");
        if (a == null || b == null) return result;
        double[] delta = { b[0] - a[0], b[1] - a[1], b[2] - a[2] };
        if (delta.Any(value => Double.IsNaN(value) || Double.IsInfinity(value))) return result;
        double scale = delta.Max(value => Math.Abs(value));
        double distance = scale == 0 ? 0 : scale * Math.Sqrt(delta.Sum(value => (value / scale) * (value / scale)));
        if (Double.IsNaN(distance) || Double.IsInfinity(distance)) return result;
        bool within = distance <= toleranceMm;
        result["available"] = true;
        result["status"] = within ? "within_tolerance" : "outside_tolerance";
        result["delta_mm"] = delta;
        result["distance_mm"] = distance;
        result["within_tolerance"] = within;
        return result;
    }

    private static Dictionary<string, object> ConnectionComparePairDirections(
        Dictionary<string, object> first, Dictionary<string, object> second)
    {
        var result = new Dictionary<string, object>
        { ["status"] = "unknown", ["available"] = false, ["dot"] = null, ["angle_degrees"] = null,
          ["relationship"] = "unknown", ["classification"] = "descriptive",
          ["classification_rule"] = "opposed when dot <= -0.999; aligned when dot >= 0.999; otherwise oblique" };
        double[] a = ConnectionPairVector(first, "direction");
        double[] b = ConnectionPairVector(second, "direction");
        if (a == null || b == null) return result;
        double dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
        if (Double.IsNaN(dot) || Double.IsInfinity(dot)) return result;
        dot = Math.Max(-1.0, Math.Min(1.0, dot));
        result["available"] = true;
        result["status"] = "known";
        result["dot"] = dot;
        result["angle_degrees"] = Math.Acos(dot) * (180.0 / Math.PI);
        result["relationship"] = ConnectionPairDirectionRelationship(dot);
        return result;
    }

    private static string ConnectionPairDirectionRelationship(double dot)
    {
        if (Double.IsNaN(dot) || Double.IsInfinity(dot)) return "unknown";
        if (dot <= -0.999) return "opposed";
        if (dot >= 0.999) return "aligned";
        return "oblique";
    }

    private static double[] ConnectionPairVector(Dictionary<string, object> endpoint, string field)
    {
        Dictionary<string, object> point = endpoint["point"] as Dictionary<string, object>;
        Dictionary<string, object> value = point == null ? null : point[field] as Dictionary<string, object>;
        if (value == null || !Equals(value["available"], true)) return null;
        object x = value["x"], y = value["y"], z = value["z"];
        if (!(x is double) || !(y is double) || !(z is double)) return null;
        double[] vector = { (double)x, (double)y, (double)z };
        return vector.Any(component => Double.IsNaN(component) || Double.IsInfinity(component)) ? null : vector;
    }

    private static Dictionary<string, object> ConnectionComparePairReferences(
        Dictionary<string, object> first, Dictionary<string, object> firstIdentity, int firstPointNumber,
        Dictionary<string, object> second, Dictionary<string, object> secondIdentity, int secondPointNumber)
    {
        string firstToSecond = ConnectionPairReferenceStatus(first, secondIdentity, secondPointNumber);
        string secondToFirst = ConnectionPairReferenceStatus(second, firstIdentity, firstPointNumber);
        string status = "unknown";
        if (firstToSecond == "matches_target" && secondToFirst == "matches_target") status = "reciprocal_match";
        else if (firstToSecond == "matches_target" || secondToFirst == "matches_target") status = "one_way_match";
        else if (firstToSecond == "not_referenced" && secondToFirst == "not_referenced") status = "not_referenced";
        else if (firstToSecond == "different_element" || firstToSecond == "different_point" ||
            secondToFirst == "different_element" || secondToFirst == "different_point") status = "different_target";
        return new Dictionary<string, object>
        {
            ["status"] = status,
            ["first_to_second"] = new Dictionary<string, object> { ["status"] = firstToSecond,
                ["expected_refno"] = secondIdentity == null ? null : secondIdentity["refno"],
                ["expected_type"] = secondIdentity == null ? null : secondIdentity["type"],
                ["expected_point_number"] = secondPointNumber },
            ["second_to_first"] = new Dictionary<string, object> { ["status"] = secondToFirst,
                ["expected_refno"] = firstIdentity == null ? null : firstIdentity["refno"],
                ["expected_type"] = firstIdentity == null ? null : firstIdentity["type"],
                ["expected_point_number"] = firstPointNumber },
            ["scope"] = "PCREF target comparison only; it does not establish engineering compatibility."
        };
    }

    private static string ConnectionPairReferenceStatus(Dictionary<string, object> endpoint,
        Dictionary<string, object> expectedIdentity, int expectedPointNumber)
    {
        if (endpoint == null || !Equals(endpoint["status"], "exists") || expectedIdentity == null ||
            !Equals(expectedIdentity["status"], "exists")) return "unknown";
        string expectedRefno = expectedIdentity["refno"] as string;
        string expectedType = expectedIdentity["type"] as string;
        if (String.IsNullOrWhiteSpace(expectedRefno) || String.IsNullOrWhiteSpace(expectedType)) return "unknown";
        Dictionary<string, object> point = endpoint["point"] as Dictionary<string, object>;
        Dictionary<string, object> pcref = point == null ? null : point["connection_reference"] as Dictionary<string, object>;
        if (pcref == null || !Equals(pcref["available"], true)) return "unknown";
        Dictionary<string, object> reference = pcref["reference"] as Dictionary<string, object>;
        if (reference != null && Equals(reference["status"], "unset")) return "not_referenced";
        if (!Equals(pcref["resolution_complete"], true) || reference == null || !Equals(reference["status"], "resolved")) return "unknown";
        string actualRefno = reference["refno"] as string;
        string actualType = reference["type"] as string;
        bool implicitTube = pcref["encoded_type"] is int && (int)pcref["encoded_type"] == 710633;
        if (!StringComparer.Ordinal.Equals(actualRefno, expectedRefno))
            return implicitTube ? "alias_ambiguous" : "different_element";
        if (String.IsNullOrWhiteSpace(actualType) || !StringComparer.Ordinal.Equals(actualType, expectedType))
            return implicitTube ? "alias_ambiguous" : "unknown";
        object pointNumber = pcref["point_number"];
        if (!(pointNumber is long) && !(pointNumber is int)) return "unknown";
        return Convert.ToInt64(pointNumber, CultureInfo.InvariantCulture) == expectedPointNumber ? "matches_target" : "different_point";
    }

    private static bool? ConnectionPointListedInPpls(int[] nativePoints, int number)
    {
        return nativePoints == null ? (bool?)null : nativePoints.Contains(number);
    }

    private static bool ConnectionCoverageComplete(int[] requested, bool enumerationComplete,
        bool pointsComplete, bool nonEnumerationErrors)
    {
        return (requested.Length > 0 || enumerationComplete) && pointsComplete && !nonEnumerationErrors;
    }

    private static Dictionary<string, object> ConnectionReadPoint(DbElement element, DbElement frame, int number, bool? listed)
    {
        ThrowIfNativeFaulted();
        var qualifier = new DbQualifier();
        qualifier.Add((double)number);
        var spatialQualifier = new DbQualifier { wrtQualifier = frame };
        spatialQualifier.Add((double)number);
        var errors = new Dictionary<string, string>();
        var exists = ConnectionReadField("PPEXST", () => new Dictionary<string, object>
        {
            ["value"] = NativeRead(() => element.GetBool(DbAttributeInstance.PPEXST, qualifier))
        });
        if (!exists.ContainsKey("value")) exists["value"] = null;
        var point = new Dictionary<string, object>
        {
            ["number"] = number, ["listed_in_ppls"] = listed, ["exists"] = exists,
            ["complete"] = false, ["errors"] = errors
        };
        if (!Equals(exists["available"], true) || !Equals(exists["value"], true))
        {
            string reason = Equals(exists["available"], true) ? "point_not_found" : "existence_unknown";
            errors["exists"] = exists["error"] as string ?? "PPEXST reports that this P-point does not exist.";
            foreach (string field in new[] { "position", "direction", "bore", "connection_type", "connection_reference" })
                point[field] = ConnectionUnavailable(reason, null);
            return point;
        }
        point["position"] = ConnectionReadField("PPOS", delegate
        {
            Position value = NativeRead(() => element.GetPosition(DbAttributeInstance.PPOS, spatialQualifier));
            double[] xyz = FiniteVector(NativeRead(() => value.X), NativeRead(() => value.Y), NativeRead(() => value.Z));
            return new Dictionary<string, object> { ["unit"] = "mm", ["x"] = xyz[0], ["y"] = xyz[1], ["z"] = xyz[2] };
        });
        point["direction"] = ConnectionReadField("PDIR", delegate
        {
            Direction value = NativeRead(() => element.GetDirection(DbAttributeInstance.PDIR, spatialQualifier));
            double[] xyz = FiniteVector(NativeRead(() => value.East), NativeRead(() => value.North), NativeRead(() => value.Up));
            double magnitude = Math.Sqrt(xyz.Sum(component => component * component));
            if (Math.Abs(magnitude - 1.0) > 0.000001)
                throw new InvalidOperationException("PDIR is not a finite unit direction; no replacement direction was inferred.");
            return new Dictionary<string, object> { ["x"] = xyz[0], ["y"] = xyz[1], ["z"] = xyz[2] };
        });
        point["bore"] = ConnectionReadBore(element, qualifier);
        point["connection_type"] = ConnectionReadField("PPCO", () => new Dictionary<string, object>
        {
            ["value"] = NativeRead(() => element.GetString(DbAttributeInstance.PPCO, qualifier))
        });
        point["connection_reference"] = ConnectionReadPointReference(element, qualifier);
        foreach (string field in new[] { "position", "direction", "bore", "connection_type", "connection_reference" })
        {
            var value = (Dictionary<string, object>)point[field];
            if (value["error"] != null) errors[field] = (string)value["error"];
            if (value.ContainsKey("raw_text_error")) errors[field + ".raw_text"] = (string)value["raw_text_error"];
        }
        point["complete"] = errors.Count == 0;
        return point;
    }

    private static Dictionary<string, object> ConnectionReadBore(DbElement element, DbQualifier qualifier)
    {
        ThrowIfNativeFaulted();
        var result = ConnectionUnavailable("read_failed", null);
        result["attribute"] = "PPBO";
        foreach (string field in new[] { "raw_text", "native_value", "native_units", "database_value", "database_units", "dimension", "converted_dimension", "converted_units", "conversion_validation", "value_mm", "unit", "positive" })
            result[field] = null;
        try { result["raw_text"] = NativeRead(() => element.GetAsString(DbAttributeInstance.PPBO, qualifier)); }
        catch (Exception ex) { ThrowIfNativeFaulted(); result["raw_text_error"] = ex.Message; }
        try
        {
            DbDouble native = NativeRead(() => element.GetDbDouble(DbAttributeInstance.PPBO, qualifier));
            if (ReferenceEquals(native, null)) throw new InvalidOperationException("PPBO returned no dimensioned bore.");
            DbDoubleDimension dimension = NativeRead(() => native.Dimension);
            if (ReferenceEquals(dimension, null) || !NativeRead(() => dimension.Set))
                throw new InvalidOperationException("PPBO returned an unset dimension.");
            DbDimension standard = NativeRead(() => dimension.StandardDimension);
            result["dimension"] = new Dictionary<string, object> { ["name"] = NativeRead(() => dimension.Name), ["standard_dimension"] = standard.ToString() };
            result["native_value"] = ModelFinite(NativeRead(() => native.Value));
            result["native_units"] = ConnectionUnits(NativeRead(() => native.Units));
            result["database_value"] = ModelFinite(NativeRead(() => native.DatabaseValue));
            result["database_units"] = ConnectionUnits(NativeRead(() => dimension.DatabaseUnits));
            if (standard != DbDimension.BORE)
                throw new InvalidOperationException("PPBO returned a dimension other than BORE; no millimetre bore was inferred.");
            DbDoubleUnits units = NativeRead(() => DbDoubleUnits.GetUnits("mm", dimension));
            if (ReferenceEquals(units, null) || !NativeRead(() => units.Set) || NativeRead(() => units.Dimension != dimension))
                throw new InvalidOperationException("Explicit millimetre units could not be resolved for the native bore dimension.");
            Dictionary<string, object> requestedUnitInfo = ConnectionUnits(units);
            DbDouble converted = NativeRead(() => native.ConvertUnits(units));
            if (ReferenceEquals(converted, null))
                throw new InvalidOperationException("PPBO conversion returned no value in explicit millimetre units.");
            DbDoubleDimension convertedDimension = NativeRead(() => converted.Dimension);
            bool dimensionMatches = !ReferenceEquals(convertedDimension, null) &&
                NativeRead(() => convertedDimension.Set) && NativeRead(() => convertedDimension == dimension) &&
                NativeRead(() => convertedDimension.StandardDimension == standard);
            if (!ReferenceEquals(convertedDimension, null) && NativeRead(() => convertedDimension.Set))
                result["converted_dimension"] = new Dictionary<string, object>
                {
                    ["name"] = NativeRead(() => convertedDimension.Name),
                    ["standard_dimension"] = NativeRead(() => convertedDimension.StandardDimension).ToString()
                };
            if (!dimensionMatches)
                throw new InvalidOperationException("PPBO conversion returned a dimension other than the original BORE dimension.");
            DbDoubleUnits convertedUnits = NativeRead(() => converted.Units);
            Dictionary<string, object> convertedUnitInfo = ConnectionUnits(convertedUnits);
            bool unitMetadataMatches = ConnectionUnitMetadataMatches(convertedUnitInfo, requestedUnitInfo);
            result["converted_units"] = convertedUnitInfo;
            result["conversion_validation"] = new Dictionary<string, object>
            {
                ["dimension_matches"] = dimensionMatches,
                ["unit_name_short_name_and_factor_match"] = unitMetadataMatches,
                ["requested_units"] = requestedUnitInfo,
                ["converted_units"] = convertedUnitInfo
            };
            if (!unitMetadataMatches)
                throw new InvalidOperationException("PPBO conversion returned units whose name, short name or factor do not match the requested millimetre units.");
            double value = ModelFinite(NativeRead(() => converted.Value));
            result["value_mm"] = value;
            result["unit"] = "mm";
            result["positive"] = ConnectionBoreIsPositive(value);
            result["available"] = true;
            result["reason"] = ConnectionBoreIsPositive(value) ? null : "non_positive";
        }
        catch (Exception ex) { ThrowIfNativeFaulted(); result["error"] = ex.Message; }
        return result;
    }

    private static bool ConnectionUnitMetadataMatches(Dictionary<string, object> actual,
        Dictionary<string, object> expected)
    {
        return StringComparer.OrdinalIgnoreCase.Equals(actual["name"] as string, expected["name"] as string) &&
            StringComparer.OrdinalIgnoreCase.Equals(actual["short_name"] as string, expected["short_name"] as string) &&
            ConnectionUnitFactorsMatch((double)actual["factor"], (double)expected["factor"]);
    }

    private static bool ConnectionUnitFactorsMatch(double actual, double expected)
    {
        if (Double.IsNaN(actual) || Double.IsInfinity(actual) || Double.IsNaN(expected) || Double.IsInfinity(expected))
            return false;
        return Math.Abs(actual - expected) <= Math.Max(1.0, Math.Abs(expected)) * 1e-12;
    }

    private static bool ConnectionBoreIsPositive(double value)
    {
        return value > 0;
    }

    private static Dictionary<string, object> ConnectionUnits(DbDoubleUnits units)
    {
        ThrowIfNativeFaulted();
        if (ReferenceEquals(units, null) || !NativeRead(() => units.Set))
            throw new InvalidOperationException("The SDK returned unset bore units.");
        DbDoubleDimension dimension = NativeRead(() => units.Dimension);
        return new Dictionary<string, object>
        {
            ["name"] = NativeRead(() => units.Name), ["short_name"] = NativeRead(() => units.ShortName),
            ["description"] = NativeRead(() => units.Description), ["factor"] = ModelFinite(NativeRead(() => units.Factor)),
            ["standard_dimension"] = NativeRead(() => dimension.StandardDimension).ToString()
        };
    }

    private static Dictionary<string, object> ConnectionReadPointReference(DbElement element, DbQualifier qualifier)
    {
        ThrowIfNativeFaulted();
        var result = ConnectionUnavailable("read_failed", null);
        result["attribute"] = "PCREF";
        result["raw_integers"] = null;
        result["reference_pair"] = null;
        result["encoded_type"] = null;
        result["encoded_point_number"] = null;
        result["point_number"] = null;
        result["encoding_status"] = "unknown";
        result["resolution_complete"] = false;
        result["reference"] = null;
        try
        {
            int[] raw = NativeRead(() => element.GetIntegerArray(DbAttributeInstance.PCREF, qualifier));
            result["raw_integers"] = raw;
            if (raw == null || raw.Length != 4)
                throw new InvalidOperationException("PCREF did not return the documented four integers; no connection state was inferred.");
            int[] pair = { raw[0], raw[1] };
            result["reference_pair"] = pair;
            result["encoded_type"] = raw[2];
            result["encoded_point_number"] = raw[3];
            result["available"] = true;
            result["reason"] = null;
            if (pair[0] == 0 && pair[1] == 0)
            {
                result["reference"] = new Dictionary<string, object> { ["status"] = "unset", ["refno"] = null };
                result["encoding_status"] = "unset";
                result["resolution_complete"] = true;
            }
            else
            {
                if (!ModelEditInput.IsReferencePair(pair))
                    throw new InvalidOperationException("PCREF returned an unsupported reference pair; raw encoding is retained.");
                bool implicitTube = ConnectionPcrefUsesImplicitTube(raw[2]);
                result["encoding_status"] = ConnectionPcrefEncodingStatus(raw[2], raw[3], false);
                result["point_number"] = ConnectionDecodePcrefPointNumber(raw[3]);
                Dictionary<string, object> anchorReference = ConnectionResolveReferencePair(pair);
                if (implicitTube)
                {
                    result["anchor_reference"] = anchorReference;
                    Dictionary<string, object> reference = ConnectionResolveImplicitTube(pair, anchorReference);
                    bool tubeResolved = Equals(reference["status"], "resolved") &&
                        string.Equals(reference["type"] as string, "TUBI", StringComparison.OrdinalIgnoreCase);
                    result["reference"] = reference;
                    result["encoding_status"] = ConnectionPcrefEncodingStatus(raw[2], raw[3], tubeResolved);
                    if (reference["error"] != null) result["error"] = reference["error"];
                    result["resolution_complete"] = ConnectionPcrefResolutionComplete(raw[2],
                        Equals(anchorReference["status"], "resolved"), tubeResolved);
                }
                else
                {
                    result["reference"] = anchorReference;
                    result["resolution_complete"] = ConnectionPcrefResolutionComplete(raw[2],
                        Equals(anchorReference["status"], "resolved"), false);
                }
            }
        }
        catch (Exception ex) { ThrowIfNativeFaulted(); result["error"] = ex.Message; }
        return result;
    }

    private static long ConnectionDecodePcrefPointNumber(int encodedPointNumber)
    {
        return Math.Abs((long)encodedPointNumber);
    }

    private static bool ConnectionPcrefUsesImplicitTube(int encodedType)
    {
        return encodedType == 710633;
    }

    private static string ConnectionPcrefEncodingStatus(int encodedType, int encodedPointNumber, bool implicitTubeResolved)
    {
        if (ConnectionPcrefUsesImplicitTube(encodedType))
            return implicitTubeResolved ? "resolved_implicit_tube" : "unresolved_implicit_tube";
        return encodedPointNumber < 0 ? "nozzle_point_decoded" : "native_reference_pair";
    }

    private static bool ConnectionPcrefResolutionComplete(int encodedType, bool referenceResolved, bool implicitTubeResolved)
    {
        return ConnectionPcrefUsesImplicitTube(encodedType) ? implicitTubeResolved : referenceResolved;
    }

    private static string ConnectionImplicitTubeExpression(int[] pair)
    {
        if (pair == null || pair.Length != 2 || pair[0] <= 0 || pair[1] < 0)
            throw new ArgumentException("ILEAVE TUBE requires one valid database/element reference pair.", "pair");
        return "ILEAVE TUBE OF =" + pair[0].ToString(CultureInfo.InvariantCulture) + "/" +
            pair[1].ToString(CultureInfo.InvariantCulture);
    }

    private static Dictionary<string, object> ConnectionResolveImplicitTube(int[] pair, Dictionary<string, object> anchorReference)
    {
        string expression = ConnectionImplicitTubeExpression(pair);
        var result = new Dictionary<string, object>
        {
            ["status"] = "unresolved", ["refno"] = null, ["type"] = null, ["name"] = null,
            ["resolution_method"] = "DbElement.Parse with the PCREF anchor as explicit CE",
            ["expression"] = expression, ["error"] = null
        };
        if (anchorReference == null || !Equals(anchorReference["status"], "resolved"))
        {
            result["error"] = "PCREF anchor could not be resolved; ILEAVE TUBE was not evaluated.";
            return result;
        }

        try
        {
            DbElement anchor = NativeRead(() => DbElement.GetElement(pair));
            DbElement tube = ConnectionResolveImplicitTubeElement(pair, anchor);
            Dictionary<string, object> identity = ConnectionIdentity(tube);
            foreach (var entry in identity) result[entry.Key] = entry.Value;
            result["status"] = "resolved";
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            result["error"] = ex.Message;
        }
        return result;
    }

    private static DbElement ConnectionResolveImplicitTubeElement(int[] pair, DbElement anchor)
    {
        string expression = ConnectionImplicitTubeExpression(pair);
        if (anchor == null || NativeRead(() => anchor.IsNull) || !NativeRead(() => anchor.IsValid) || NativeRead(() => anchor.IsDeleted))
            throw new InvalidOperationException("PCREF anchor is not a valid database element.");
        if (!string.Equals(ModelReference(NativeRead(() => anchor.Ref)), ModelReference(pair), StringComparison.Ordinal))
            throw new InvalidOperationException("Resolved PCREF anchor does not match the encoded database/element pair.");

        DbElement tube = null;
        PdmsMessage parseError = null;
        bool parsed = NativeRead(() => DbElement.Parse(expression, anchor, out tube, out parseError));
        if (!parsed)
        {
            string diagnostic = MessageText(parseError);
            throw new InvalidOperationException(string.IsNullOrWhiteSpace(diagnostic)
                ? "E3D could not resolve the ILEAVE TUBE expression."
                : diagnostic);
        }
        if (tube == null || NativeRead(() => tube.IsNull) || !NativeRead(() => tube.IsValid) || NativeRead(() => tube.IsDeleted))
            throw new InvalidOperationException("ILEAVE TUBE returned no valid database element.");
        string tubeType = NativeRead(() => tube.GetElementType().ShortName);
        if (!string.Equals(tubeType, "TUBI", StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("ILEAVE TUBE returned an element whose type is not TUBI.");
        return tube;
    }

    private static Dictionary<string, object> ConnectionReadReference(DbElement element, DbAttribute attribute)
    {
        ThrowIfNativeFaulted();
        var result = ConnectionUnavailable("read_failed", null);
        result["raw_text"] = null;
        result["status"] = "unknown";
        result["refno"] = null;
        try
        {
            result["raw_text"] = NativeRead(() => element.GetAsString(attribute));
            DbElement reference = NativeRead(() => element.GetElement(attribute));
            if (reference == null || NativeRead(() => reference.IsNull))
            {
                string raw = ((string)result["raw_text"] ?? "").Trim();
                if (!raw.Equals("Nulref", StringComparison.OrdinalIgnoreCase) && raw != "=0/0")
                    throw new InvalidOperationException("SDK returned a null reference without an explicit native unset value.");
                result["status"] = "unset";
            }
            else
            {
                int[] pair = NativeRead(() => reference.Ref);
                result["refno"] = ModelReference(pair);
                if (NativeRead(() => reference.IsValid) && !NativeRead(() => reference.IsDeleted))
                {
                    var identity = ConnectionIdentity(reference);
                    foreach (var entry in identity) result[entry.Key] = entry.Value;
                    result["status"] = "resolved";
                }
                else result["status"] = "unresolved";
            }
            result["available"] = true;
            result["reason"] = null;
        }
        catch (Exception ex) { ThrowIfNativeFaulted(); result["status"] = "read_failed"; result["error"] = ex.Message; }
        return result;
    }

    private static Dictionary<string, object> ConnectionResolveReferencePair(int[] pair)
    {
        ThrowIfNativeFaulted();
        DbElement reference = NativeRead(() => DbElement.GetElement(pair));
        var result = new Dictionary<string, object> { ["status"] = "unresolved", ["refno"] = ModelReference(pair) };
        if (reference == null || NativeRead(() => reference.IsNull) || !NativeRead(() => reference.IsValid) || NativeRead(() => reference.IsDeleted))
            return result;
        foreach (var entry in ConnectionIdentity(reference)) result[entry.Key] = entry.Value;
        result["status"] = "resolved";
        return result;
    }

    private static Dictionary<string, object> ConnectionIdentity(DbElement element)
    {
        ThrowIfNativeFaulted();
        var type = NativeRead(() => element.GetElementType());
        return new Dictionary<string, object>
        {
            ["refno"] = ModelReference(NativeRead(() => element.Ref)),
            ["type"] = NativeRead(() => type.ShortName),
            ["name"] = NativeRead(() => element.GetAsString(DbAttributeInstance.NAME))
        };
    }

    private static Dictionary<string, object> ConnectionReadField(string attribute, Func<Dictionary<string, object>> read)
    {
        ThrowIfNativeFaulted();
        try
        {
            Dictionary<string, object> result = read();
            result["attribute"] = attribute;
            result["available"] = true;
            result["error"] = null;
            return result;
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            var result = ConnectionUnavailable("read_failed", ex.Message);
            result["attribute"] = attribute;
            return result;
        }
    }

    private static Dictionary<string, object> ConnectionUnavailable(string reason, string error)
    {
        return new Dictionary<string, object> { ["available"] = false, ["reason"] = reason, ["error"] = error };
    }
}
