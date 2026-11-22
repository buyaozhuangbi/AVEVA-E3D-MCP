// Isolated contract fixture: recording fakes only; no AVEVA assembly or live DB.
using System;
using System.Collections.Generic;
using System.Linq;

internal sealed class CandidateScenario
{
    internal readonly Dictionary<string, string[]> Members = new Dictionary<string, string[]>(StringComparer.Ordinal);
    internal readonly Dictionary<string, bool?> Creatable = new Dictionary<string, bool?>(StringComparer.Ordinal);
    internal readonly HashSet<string> MemberErrors = new HashSet<string>(StringComparer.Ordinal);
    internal readonly HashSet<string> CreatableErrors = new HashSet<string>(StringComparer.Ordinal);
    internal readonly List<string> Calls = new List<string>();
    internal int MutationCalls;

    internal string[] ReadMembers(string parent)
    {
        Calls.Add("MemberTypes:" + parent);
        if (MemberErrors.Contains(parent)) throw new InvalidOperationException("fixture MemberTypes failure");
        return Members.TryGetValue(parent, out var values) ? values : Array.Empty<string>();
    }

    internal bool? ReadCreatable(string parent, string child)
    {
        Calls.Add("IsCreatable:" + parent + ":" + child);
        if (CreatableErrors.Contains(parent + ":" + child)) throw new InvalidOperationException("fixture IsCreatable failure");
        return Creatable.TryGetValue(parent + ":" + child, out var value) ? value : null;
    }
}

internal sealed class CandidateObservation
{
    internal string Parent;
    internal string Child;
    internal bool? MemberTypeContains;
    internal bool? IsCreatable;
    internal string MemberTypesError;
    internal string IsCreatableError;
    internal string PmlOwnerHint;
    internal string FormRoute;
    internal readonly string Comparison = "independent_observations_no_equivalence_contract";
    internal readonly object CurrentModule = null;
    internal readonly object Attributes = null;
    internal readonly object PermissionsVerified = null;
    internal readonly object ExecutionPermitted = null;
}

internal static class ParagonHierarchyCandidateHarness
{
    private static readonly string[] Types = { "CATA", "SECT", "CATE" };
    private static readonly Dictionary<string, string> PmlOwnerHint = new Dictionary<string, string>(StringComparer.Ordinal)
    {
        ["SECT"] = "CATA",
        ["CATE"] = "SECT"
    };

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    private static CandidateObservation Observe(CandidateScenario scenario, string parent, string child)
    {
        var result = new CandidateObservation { Parent = parent, Child = child };
        try { result.MemberTypeContains = scenario.ReadMembers(parent).Contains(child, StringComparer.Ordinal); }
        catch (Exception error) { result.MemberTypesError = error.GetType().Name; }
        try { result.IsCreatable = scenario.ReadCreatable(parent, child); }
        catch (Exception error) { result.IsCreatableError = error.GetType().Name; }
        result.PmlOwnerHint = PmlOwnerHint.TryGetValue(child, out var owner) ? owner : null;
        result.FormRoute = child == "CATE" ? "catModelViews.create" : "general_NEW";
        return result;
    }

    private static void MatrixRetainsIndependentNativeObservations()
    {
        var scenario = new CandidateScenario();
        scenario.Members["CATA"] = new[] { "SECT" };
        scenario.Members["SECT"] = new[] { "CATE" };
        scenario.Members["CATE"] = Array.Empty<string>();
        foreach (var parent in Types)
            foreach (var child in Types)
                scenario.Creatable[parent + ":" + child] = parent == "CATA" && child == "SECT";

        // A mismatch is retained as data; it does not become an action decision.
        scenario.Creatable["SECT:CATE"] = false;
        scenario.Creatable["CATE:CATA"] = true;
        var rows = (from parent in Types from child in Types select Observe(scenario, parent, child)).ToArray();
        Check(rows.Length == 9, "The candidate matrix must contain all nine parent/child pairs.");
        Check(rows.Single(row => row.Parent == "CATA" && row.Child == "SECT").MemberTypeContains == true &&
              rows.Single(row => row.Parent == "CATA" && row.Child == "SECT").IsCreatable == true,
            "The CATA→SECT edge should preserve both fixture observations.");
        Check(rows.Single(row => row.Parent == "SECT" && row.Child == "CATE").MemberTypeContains == true &&
              rows.Single(row => row.Parent == "SECT" && row.Child == "CATE").IsCreatable == false,
            "A MemberTypes/IsCreatable mismatch must remain visible without collapsing either value.");
        Check(rows.Single(row => row.Parent == "CATE" && row.Child == "CATA").MemberTypeContains == false &&
              rows.Single(row => row.Parent == "CATE" && row.Child == "CATA").IsCreatable == true,
            "An unlisted candidate with a true IsCreatable observation must remain visible.");
        Check(rows.Single(row => row.Parent == "SECT" && row.Child == "CATE").FormRoute == "catModelViews.create" &&
              rows.Single(row => row.Parent == "CATA" && row.Child == "SECT").FormRoute == "general_NEW",
            "CATE must retain its custom form path, distinct from general NEW.");
        Check(rows.All(row => row.CurrentModule == null && row.Attributes == null && row.PermissionsVerified == null &&
                              row.ExecutionPermitted == null && row.Comparison == "independent_observations_no_equivalence_contract"),
            "Module, attribute, permission, and execution state must stay unknown.");
        Check(scenario.MutationCalls == 0 && scenario.Calls.Count == 18,
            "The 3×3 matrix must use one MemberTypes read and one IsCreatable read per pair, with no mutations.");
    }

    private static void ErrorsRemainUnknownAndDoNotAuthorize()
    {
        var scenario = new CandidateScenario();
        scenario.Members["CATA"] = new[] { "SECT" };
        scenario.MemberErrors.Add("SECT");
        scenario.CreatableErrors.Add("SECT:CATE");
        var errorRow = Observe(scenario, "SECT", "CATE");
        Check(errorRow.MemberTypeContains == null && errorRow.MemberTypesError == "InvalidOperationException" &&
              errorRow.IsCreatable == null && errorRow.IsCreatableError == "InvalidOperationException" &&
              errorRow.PermissionsVerified == null && errorRow.ExecutionPermitted == null,
            "Read errors must remain explicit unknowns, never permission decisions.");
        Check(scenario.MutationCalls == 0, "The read-only fake scenario reached a mutation API.");
    }

    private static int Main()
    {
        MatrixRetainsIndependentNativeObservations();
        ErrorsRemainUnknownAndDoNotAuthorize();
        Console.WriteLine("PARAGON hierarchy candidate offline contract passed (9 pairs; no mutation calls).");
        return 0;
    }
}
