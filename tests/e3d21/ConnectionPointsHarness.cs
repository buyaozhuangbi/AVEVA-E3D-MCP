using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        // PRODUCTION_METHODS

        public static void RunConnectionPointChecks(Action<bool, string> check)
        {
            check(ConnectionDecodePcrefPointNumber(-7) == 7L, "negative nozzle point uses absolute value");
            check(ConnectionDecodePcrefPointNumber(7) == 7L, "positive point is preserved");
            check(ConnectionDecodePcrefPointNumber(Int32.MinValue) == 2147483648L, "minimum integer does not overflow");
            check(ConnectionPcrefUsesImplicitTube(710633), "710633 identifies implicit tube");
            check(!ConnectionPcrefUsesImplicitTube(710632), "other type uses direct reference");
            check(ConnectionPcrefEncodingStatus(3, -7, false) == "nozzle_point_decoded", "negative encoding status");
            check(ConnectionPcrefEncodingStatus(710633, -7, false) == "unresolved_implicit_tube", "unresolved implicit tube status");
            check(ConnectionPcrefEncodingStatus(710633, -7, true) == "resolved_implicit_tube", "resolved implicit tube status");
            check(ConnectionPcrefResolutionComplete(3, true, false), "resolved direct negative point is complete");
            check(!ConnectionPcrefResolutionComplete(710633, true, false), "resolved anchor alone is not the final tube");
            check(ConnectionPcrefResolutionComplete(710633, true, true), "verified implicit TUBI is complete");
            check(!ConnectionPcrefResolutionComplete(3, false, false), "unresolved direct reference is incomplete");
            check(ConnectionImplicitTubeExpression(new[] { 2000000001, 1811 }) == "ILEAVE TUBE OF =2000000001/1811",
                "implicit tube expression uses the exact numeric anchor");
            bool invalidPairRejected = false;
            try { ConnectionImplicitTubeExpression(new[] { 0, 1811 }); } catch (ArgumentException) { invalidPairRejected = true; }
            check(invalidPairRejected, "invalid implicit tube reference pairs are rejected");

            var boreUnitsBoreContext = new Dictionary<string, object>
            {
                ["name"] = "mm", ["short_name"] = "mm", ["factor"] = 1.0, ["standard_dimension"] = "BORE"
            };
            var boreUnitsDistanceContext = new Dictionary<string, object>
            {
                ["name"] = "mm", ["short_name"] = "mm", ["factor"] = 1.0, ["standard_dimension"] = "DIST"
            };
            check(ConnectionUnitMetadataMatches(boreUnitsDistanceContext, boreUnitsBoreContext),
                "canonical mm unit survives BORE/DIST wrapper context difference");
            var inches = new Dictionary<string, object>
            {
                ["name"] = "in", ["short_name"] = "in", ["factor"] = 25.4, ["standard_dimension"] = "BORE"
            };
            check(!ConnectionUnitMetadataMatches(inches, boreUnitsBoreContext), "other BORE units are rejected");
            check(!ConnectionUnitFactorsMatch(1.0, 25.4), "different unit factors are rejected");
            check(ConnectionBoreIsPositive(80.0), "positive bore is positive");
            check(!ConnectionBoreIsPositive(0.0), "zero bore remains a readable non-positive value");
            check(!ConnectionBoreIsPositive(-1.0), "negative bore remains a readable non-positive value");

            check(ConnectionPointListedInPpls(null, 2) == null, "unknown PPLS membership stays null");
            check(ConnectionPointListedInPpls(new int[0], 2) == false, "known empty PPLS excludes the point");
            check(ConnectionPointListedInPpls(new[] { 2, 4 }, 2) == true, "listed point is true");
            check(ConnectionPointListedInPpls(new[] { 2, 4 }, 3) == false, "unlisted point is false");

            check(ConnectionCoverageComplete(new[] { 2 }, false, true, false),
                "explicit point coverage survives a separate PPLS failure");
            check(!ConnectionCoverageComplete(new int[0], false, true, false),
                "automatic enumeration requires a complete PPLS read");
            check(ConnectionCoverageComplete(new int[0], true, true, false), "complete enumeration coverage");
            check(!ConnectionCoverageComplete(new[] { 2 }, false, false, false), "point read failure blocks completion");
            check(!ConnectionCoverageComplete(new[] { 2 }, false, true, true), "other read errors block completion");

            check(ConnectionPairDirectionRelationship(-1.0) == "opposed", "negative dot gets descriptive opposed label");
            check(ConnectionPairDirectionRelationship(1.0) == "aligned", "positive dot gets descriptive aligned label");
            check(ConnectionPairDirectionRelationship(0.0) == "oblique", "intermediate dot gets descriptive oblique label");
            check(ConnectionPairDirectionRelationship(Double.NaN) == "unknown", "non-finite direction stays unknown");

            var firstPoint = new Dictionary<string, object>
            {
                ["position"] = new Dictionary<string, object>
                { ["available"] = true, ["x"] = 0.0, ["y"] = 0.0, ["z"] = 0.0 },
                ["direction"] = new Dictionary<string, object>
                { ["available"] = true, ["x"] = 1.0, ["y"] = 0.0, ["z"] = 0.0 }
            };
            var secondPoint = new Dictionary<string, object>
            {
                ["position"] = new Dictionary<string, object>
                { ["available"] = true, ["x"] = 3.0, ["y"] = 4.0, ["z"] = 12.0 },
                ["direction"] = new Dictionary<string, object>
                { ["available"] = true, ["x"] = 0.0, ["y"] = 1.0, ["z"] = 0.0 }
            };
            var firstRead = new Dictionary<string, object> { ["point"] = firstPoint };
            var secondRead = new Dictionary<string, object> { ["point"] = secondPoint };
            var within = ConnectionComparePairPositions(firstRead, secondRead, 13.0);
            check(Equals(within["status"], "within_tolerance") && Equals(within["distance_mm"], 13.0),
                "position distance is compared with caller tolerance inclusively");
            var outside = ConnectionComparePairPositions(firstRead, secondRead, 12.999);
            check(Equals(outside["status"], "outside_tolerance") && Equals(outside["within_tolerance"], false),
                "position outside caller tolerance is distinguished");
            var samePoint = ConnectionComparePairPositions(firstRead, firstRead, 0.0);
            check(Equals(samePoint["status"], "within_tolerance") && Equals(samePoint["distance_mm"], 0.0),
                "identical positions pass zero tolerance");
            var direction = ConnectionComparePairDirections(firstRead, secondRead);
            check(Equals(direction["dot"], 0.0) && Math.Abs((double)direction["angle_degrees"] - 90.0) < 1e-12,
                "raw direction dot and angle are reported");
            ((Dictionary<string, object>)secondPoint["position"])["available"] = false;
            var unknownPosition = ConnectionComparePairPositions(firstRead, secondRead, 5.0);
            check(Equals(unknownPosition["status"], "unknown") && unknownPosition["distance_mm"] == null,
                "missing position remains unknown instead of mismatch");
            ((Dictionary<string, object>)secondPoint["position"])["available"] = true;
            ((Dictionary<string, object>)secondPoint["direction"])["available"] = false;
            var unknownDirection = ConnectionComparePairDirections(firstRead, secondRead);
            check(Equals(unknownDirection["status"], "unknown") && unknownDirection["dot"] == null,
                "missing direction remains unknown");

            var pcref = new Dictionary<string, object>
            {
                ["available"] = true, ["resolution_complete"] = true, ["encoded_type"] = 3,
                ["point_number"] = 2L,
                ["reference"] = new Dictionary<string, object>
                { ["status"] = "resolved", ["refno"] = "=1/3", ["type"] = "NOZZ" }
            };
            var sourcePoint = new Dictionary<string, object> { ["connection_reference"] = pcref };
            var sourceEndpoint = new Dictionary<string, object> { ["status"] = "exists", ["point"] = sourcePoint };
            var targetIdentity = new Dictionary<string, object> { ["status"] = "exists", ["refno"] = "=1/3", ["type"] = "NOZZ" };
            check(ConnectionPairReferenceStatus(sourceEndpoint, targetIdentity, 2) == "matches_target",
                "PCREF exact ref/type/point and existing endpoint matches");
            check(ConnectionPairReferenceStatus(sourceEndpoint,
                new Dictionary<string, object> { ["status"] = "unknown", ["refno"] = "=1/3", ["type"] = "NOZZ" }, 2) == "unknown",
                "unverified selected endpoint cannot match");
            check(ConnectionPairReferenceStatus(sourceEndpoint,
                new Dictionary<string, object> { ["status"] = "exists", ["refno"] = "=1/4", ["type"] = "NOZZ" }, 2) == "different_element",
                "different resolved refno is distinguished");
            check(ConnectionPairReferenceStatus(sourceEndpoint,
                new Dictionary<string, object> { ["status"] = "exists", ["refno"] = "=1/3", ["type"] = "PIPE" }, 2) == "unknown",
                "same refno but different element type does not alias-match");
            check(ConnectionPairReferenceStatus(sourceEndpoint, targetIdentity, 3) == "different_point",
                "different decoded target point is distinguished");
            pcref["encoded_type"] = 710633;
            check(ConnectionPairReferenceStatus(sourceEndpoint,
                new Dictionary<string, object> { ["status"] = "exists", ["refno"] = "=1/3", ["type"] = "PIPE" }, 2) == "alias_ambiguous",
                "implicit tube type mismatch remains alias ambiguous");
            pcref["reference"] = new Dictionary<string, object> { ["status"] = "unset" };
            check(ConnectionPairReferenceStatus(sourceEndpoint, targetIdentity, 2) == "not_referenced",
                "explicitly unset PCREF is not inferred as a match");

            var firstIdentity = new Dictionary<string, object> { ["status"] = "exists", ["refno"] = "=1/2", ["type"] = "NOZZ" };
            var secondIdentity = new Dictionary<string, object> { ["status"] = "exists", ["refno"] = "=1/3", ["type"] = "NOZZ" };
            pcref["available"] = true;
            pcref["resolution_complete"] = true;
            pcref["encoded_type"] = 3;
            pcref["point_number"] = 2L;
            pcref["reference"] = new Dictionary<string, object>
            { ["status"] = "resolved", ["refno"] = "=1/3", ["type"] = "NOZZ" };
            var reversePcref = new Dictionary<string, object>
            {
                ["available"] = true, ["resolution_complete"] = true, ["encoded_type"] = 3, ["point_number"] = 1L,
                ["reference"] = new Dictionary<string, object> { ["status"] = "resolved", ["refno"] = "=1/2", ["type"] = "NOZZ" }
            };
            var secondEndpoint = new Dictionary<string, object>
            {
                ["status"] = "exists", ["point"] = new Dictionary<string, object>
                { ["connection_reference"] = reversePcref }
            };
            var reciprocal = ConnectionComparePairReferences(sourceEndpoint, firstIdentity, 1,
                secondEndpoint, secondIdentity, 2);
            check(Equals(reciprocal["status"], "reciprocal_match"), "reciprocal exact PCREF identities are reported");
            reversePcref["reference"] = new Dictionary<string, object> { ["status"] = "unset" };
            var oneWay = ConnectionComparePairReferences(sourceEndpoint, firstIdentity, 1,
                secondEndpoint, secondIdentity, 2);
            check(Equals(oneWay["status"], "one_way_match"), "one-way PCREF is not called reciprocal");
        }
    }

    internal static class Program
    {
        private static int checks;

        private static void Check(bool value, string label)
        {
            checks++;
            if (!value) throw new Exception("Check failed: " + label);
        }

        private static int Main()
        {
            E3d21Context.RunConnectionPointChecks(Check);
            Console.WriteLine("Connection point production checks passed: " + checks);
            return 0;
        }
    }
}
