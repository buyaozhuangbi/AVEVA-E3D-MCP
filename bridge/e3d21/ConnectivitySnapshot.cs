using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;

namespace E3d21AiBridge;

// Kept separate from the HTTP router so its bounds and traversal can be tested
// without loading the AVEVA SDK. The router must use ReadUniqueBodyAsync.
public interface IE3d21ConnectivitySnapshotContext
{
    object ReadConnectivitySnapshot(ConnectivitySnapshotRequest request, string sessionId, string bridgeBuild);
}

internal static class ConnectivitySnapshotErrors
{
    internal static string Text(string value)
    {
        if (value == null || value.Length <= 4096) return value;
        int length = 4093;
        if (char.IsHighSurrogate(value[length - 1])) length--;
        return value.Substring(0, length) + "...";
    }

    // Existing on-UI readers also return nested error/typed-error maps. Bound
    // diagnostic text without truncating native attribute values or identities.
    internal static void BoundFields(object value, bool errorScope = false)
    {
        if (value is IDictionary map)
            foreach (object key in map.Keys.Cast<object>().ToArray())
            {
                string name = key as string ?? "";
                bool diagnostic = errorScope || name == "message" || name == "error" || name == "errors" ||
                    name == "read_errors" || name == "typed_read_errors" || name.EndsWith("_error", StringComparison.Ordinal);
                if (diagnostic && map[key] is string text) map[key] = Text(text);
                else BoundFields(map[key], diagnostic);
            }
        else if (value is IList list)
            for (int index = 0; index < list.Count; ++index)
                if (errorScope && list[index] is string text) list[index] = Text(text);
                else BoundFields(list[index], errorScope);
    }
}

public sealed class ConnectivityEndpoint
{
    public string Refno { get; internal set; }
    public string ExpectedType { get; internal set; }
    public int PointNumber { get; internal set; }
    internal string Key => Refno.ToUpperInvariant() + "|" + ExpectedType + "|" + PointNumber.ToString(CultureInfo.InvariantCulture);
    public Dictionary<string, object> Json() => new Dictionary<string, object>
    { ["refno"] = Refno, ["expected_type"] = ExpectedType, ["point_number"] = PointNumber };
}

public sealed class ConnectivityPair
{
    public ConnectivityEndpoint First { get; internal set; }
    public ConnectivityEndpoint Second { get; internal set; }
    public double ToleranceMm { get; internal set; }
    public Dictionary<string, object> Json() => new Dictionary<string, object>
    { ["first"] = First.Json(), ["second"] = Second.Json(), ["tolerance_mm"] = ToleranceMm };
}

public sealed class ConnectivitySnapshotRequest
{
    private ConnectivitySnapshotRequest() { }
    public const int BranchLimit = 8, PairLimit = 16, StartLimit = 16, MemberLimit = 64, NodeLimit = 128, HopLimit = 16;
    public IReadOnlyList<string> Branches { get; private set; }
    public IReadOnlyList<ConnectivityPair> Pairs { get; private set; }
    public IReadOnlyList<ConnectivityEndpoint> Starts { get; private set; }
    public int MembersPerBranch { get; private set; }
    public int MaxHops { get; private set; }
    public int MaxNodes { get; private set; }
    public IReadOnlyDictionary<string, string> ExpectedContext { get; private set; }
    public static readonly string[] ContextFields = { "session_id", "bridge_build", "project", "mdb", "current_element", "current_element_type" };

    public static ConnectivitySnapshotRequest Parse(JObject body)
    {
        Fields(body, "branches", "pairs", "starts", "member_limit", "max_hops", "max_nodes", "expected_context");
        var branches = Array(body, "branches", BranchLimit).Select(token =>
        {
            var item = token as JObject;
            Fields(item, "refno", "expected_type");
            if (Type(item["expected_type"]) != "BRAN") throw new ArgumentException("branches require expected_type BRAN.");
            return Reference(item["refno"]);
        }).ToList();
        if (branches.Distinct(StringComparer.OrdinalIgnoreCase).Count() != branches.Count)
            throw new ArgumentException("Duplicate BRAN reference.");
        var pairs = Array(body, "pairs", PairLimit).Select(token =>
        {
            var item = token as JObject;
            Fields(item, "first", "second", "tolerance_mm");
            var first = Endpoint(item["first"]);
            var second = Endpoint(item["second"]);
            if (first.Key == second.Key) throw new ArgumentException("A pair requires two distinct typed P-points.");
            JToken tolerance = item["tolerance_mm"];
            if (tolerance == null || tolerance.Type != JTokenType.Float && tolerance.Type != JTokenType.Integer)
                throw new ArgumentException("tolerance_mm must be numeric.");
            double number = tolerance.Value<double>();
            if (double.IsNaN(number) || double.IsInfinity(number) || number < 0 || number > 1000000)
                throw new ArgumentException("tolerance_mm must be finite, from 0 through 1000000 mm.");
            return new ConnectivityPair { First = first, Second = second, ToleranceMm = number };
        }).ToList();
        if (pairs.Select(pair => pair.First.Key + ">" + pair.Second.Key).Distinct().Count() != pairs.Count)
            throw new ArgumentException("Duplicate ordered pair.");
        var starts = Array(body, "starts", StartLimit).Select(Endpoint).ToList();
        if (starts.Select(start => start.Key).Distinct().Count() != starts.Count)
            throw new ArgumentException("Duplicate typed trace start.");
        if (branches.Count + pairs.Count + starts.Count == 0) throw new ArgumentException("At least one explicit scope is required.");
        var expected = new Dictionary<string, string>();
        if (body["expected_context"] != null)
        {
            var item = body["expected_context"] as JObject;
            Fields(item, ContextFields);
            foreach (string key in ContextFields)
            {
                string text = item[key]?.Type == JTokenType.String ? item[key].Value<string>() : null;
                if (string.IsNullOrWhiteSpace(text) || text.Length > 512 || text.Any(char.IsControl))
                    throw new ArgumentException("expected_context requires all six identity fields.");
                expected[key] = text;
            }
            expected["current_element"] = Reference(item["current_element"]);
            if (expected["current_element"][0] != '=') throw new ArgumentException("Expected CE must be a numeric reference.");
            expected["current_element_type"] = Type(item["current_element_type"]);
        }
        return new ConnectivitySnapshotRequest {
            Branches = branches.AsReadOnly(), Pairs = pairs.AsReadOnly(), Starts = starts.AsReadOnly(),
            MembersPerBranch = Integer(body["member_limit"], 64, 1, MemberLimit),
            MaxHops = Integer(body["max_hops"], 8, 0, HopLimit),
            MaxNodes = Integer(body["max_nodes"], 64, Math.Max(1, starts.Count), NodeLimit),
            ExpectedContext = new System.Collections.ObjectModel.ReadOnlyDictionary<string, string>(expected)
        };
    }

    internal static void Fields(JObject body, params string[] allowed)
    {
        if (body == null || body.Properties().Any(p => !allowed.Contains(p.Name)))
            throw new ArgumentException("Unknown or malformed connectivity snapshot fields.");
    }

    private static JArray Array(JObject body, string key, int maximum)
    {
        if (body[key] == null) return new JArray();
        var value = body[key] as JArray;
        if (value == null || value.Count > maximum) throw new ArgumentException(key + " exceeds its bounded array contract.");
        return value;
    }

    internal static int Integer(JToken value, int fallback, int minimum, int maximum)
    {
        if (value == null) return fallback >= minimum ? fallback : minimum;
        int number;
        if (value.Type != JTokenType.Integer || !int.TryParse(value.ToString(), out number) || number < minimum || number > maximum)
            throw new ArgumentException("Integer is outside the connectivity snapshot budget.");
        return number;
    }

    internal static string Reference(JToken value)
    {
        string text = value?.Type == JTokenType.String ? value.Value<string>().Trim() : "";
        if (text.Length < 2 || text.Length > 512 || text.Any(c => char.IsWhiteSpace(c) || char.IsControl(c)) || text.IndexOfAny(new[] { '*', '?' }) >= 0)
            throw new ArgumentException("An explicit literal reference is required.");
        if (text[0] == '/') return text;
        Match match = Regex.Match(text, @"^=([0-9]{1,10})/([0-9]{1,10})$");
        int database, element;
        if (!match.Success || !int.TryParse(match.Groups[1].Value, out database) || database <= 0 ||
            !int.TryParse(match.Groups[2].Value, out element) || element < 0) throw new ArgumentException("Invalid numeric reference.");
        return "=" + database.ToString(CultureInfo.InvariantCulture) + "/" + element.ToString(CultureInfo.InvariantCulture);
    }

    internal static string Type(JToken value)
    {
        string text = value?.Type == JTokenType.String ? value.Value<string>().Trim().ToUpperInvariant() : "";
        if (!Regex.IsMatch(text, @"^[A-Z][A-Z0-9_:]{0,63}$")) throw new ArgumentException("An explicit native type is required.");
        return text;
    }

    private static ConnectivityEndpoint Endpoint(JToken token)
    {
        var item = token as JObject;
        Fields(item, "refno", "expected_type", "point_number");
        if (item["point_number"] == null) throw new ArgumentException("point_number is required.");
        return new ConnectivityEndpoint { Refno = Reference(item["refno"]), ExpectedType = Type(item["expected_type"]),
            PointNumber = Integer(item["point_number"], 0, 1, 10000) };
    }
}

// Pure directed PCREF walker. A node budget counts endpoint-reader invocations,
// including aliases resolving to an existing node, rather than just output rows.
internal static class ConnectivityTraceSnapshot
{
    private static object Get(IDictionary<string, object> value, string key) => value != null && value.TryGetValue(key, out object item) ? item : null;
    private static IDictionary<string, object> Map(object value) => value as IDictionary<string, object>;
    private static bool True(IDictionary<string, object> value, string key) => Equals(Get(value, key), true);
    private static string Text(IDictionary<string, object> value, string key) => Get(value, key) as string;
    private static string Key(string reference, string type, int point) => reference.ToUpperInvariant() + "|" + type + "|" + point;

    internal static Dictionary<string, object> Read(ConnectivitySnapshotRequest request, Func<ConnectivityEndpoint, Dictionary<string, object>> reader)
    {
        var nodes = new List<Dictionary<string, object>>();
        var edges = new List<Dictionary<string, object>>();
        var starts = new List<Dictionary<string, object>>();
        var errors = new List<Dictionary<string, object>>();
        var byKey = new Dictionary<string, int>();
        var queue = new Queue<int>();
        int reads = 0;
        bool truncated = false;
        Func<ConnectivityEndpoint, int, int> inspect = (target, hops) =>
        {
            if (byKey.TryGetValue(target.Key, out int found)) return found;
            if (reads >= request.MaxNodes) return -1;
            ++reads;
            Dictionary<string, object> endpoint = reader(target);
            var identity = Map(Get(endpoint, "element"));
            string reference = Text(identity, "refno"), type = Text(identity, "type");
            bool identityValid = reference != null && reference.StartsWith("=", StringComparison.Ordinal) && type == target.ExpectedType &&
                (target.Refno[0] != '=' || reference == target.Refno);
            string key = identityValid ? Key(reference, type, target.PointNumber) : target.Key;
            if (identityValid && byKey.TryGetValue(key, out found)) { byKey[target.Key] = found; return found; }
            int index = nodes.Count;
            var node = new Dictionary<string, object> { ["index"] = index, ["hops"] = hops,
                ["requested_refno"] = target.Refno, ["requested_type"] = target.ExpectedType,
                ["refno"] = identityValid ? reference : target.Refno, ["type"] = target.ExpectedType,
                ["point_number"] = target.PointNumber, ["identity_verified"] = identityValid,
                ["endpoint"] = endpoint, ["expanded"] = false, ["link_status"] = "not_read", ["read_complete"] = false };
            nodes.Add(node); byKey[key] = index; byKey[target.Key] = index; queue.Enqueue(index);
            return index;
        };
        for (int i = 0; i < request.Starts.Count; ++i)
            starts.Add(new Dictionary<string, object> { ["start_index"] = i, ["requested"] = request.Starts[i].Json(), ["node_index"] = inspect(request.Starts[i], 0) });
        while (queue.Count != 0)
        {
            var node = nodes[queue.Dequeue()];
            node["expanded"] = true;
            var endpoint = Map(node["endpoint"]);
            var point = Map(Get(endpoint, "point"));
            var link = Map(Get(point, "connection_reference"));
            var resolved = Map(Get(link, "reference"));
            if (!True(node, "identity_verified") || !True(endpoint, "complete") || Text(endpoint, "status") != "exists" || !True(link, "available"))
            {
                node["link_status"] = "unresolved";
                errors.Add(new Dictionary<string, object> { ["node_index"] = node["index"], ["code"] = "endpoint_incomplete" });
                continue;
            }
            if (Text(resolved, "status") == "unset") { node["link_status"] = "open_end"; node["read_complete"] = true; continue; }
            ConnectivityEndpoint target;
            try
            {
                if (!True(link, "resolution_complete") || Text(resolved, "status") != "resolved") throw new ArgumentException("PCREF is unresolved.");
                target = new ConnectivityEndpoint { Refno = ConnectivitySnapshotRequest.Reference(JToken.FromObject(Get(resolved, "refno") ?? "")),
                    ExpectedType = ConnectivitySnapshotRequest.Type(JToken.FromObject(Get(resolved, "type") ?? "")),
                    PointNumber = ConnectivitySnapshotRequest.Integer(JToken.FromObject(Get(link, "point_number") ?? ""), 0, 1, 10000) };
                if (!target.Refno.StartsWith("=", StringComparison.Ordinal)) throw new ArgumentException("PCREF must resolve a typed numeric reference.");
            }
            catch (ArgumentException)
            {
                node["link_status"] = "unresolved";
                errors.Add(new Dictionary<string, object> { ["node_index"] = node["index"], ["code"] = "pcref_unresolved" });
                continue;
            }
            int index = -1, hops = (int)node["hops"];
            string status;
            if (byKey.TryGetValue(target.Key, out index)) status = "target_already_visited";
            else if (hops >= request.MaxHops) { index = -1; status = "hop_limit"; truncated = true; }
            else if (reads >= request.MaxNodes) { index = -1; status = "node_limit"; truncated = true; }
            else { index = inspect(target, hops + 1); status = "resolved"; }
            node["link_status"] = "resolved"; node["read_complete"] = true;
            edges.Add(new Dictionary<string, object> { ["from_index"] = node["index"], ["to_index"] = index >= 0 ? (object)index : null,
                ["status"] = status, ["target_refno"] = target.Refno, ["target_type"] = target.ExpectedType,
                ["target_point_number"] = target.PointNumber, ["relationship"] = "unknown", ["cycle"] = false });
        }
        foreach (var edge in edges)
        {
            if (!(edge["to_index"] is int target)) continue;
            int source = (int)edge["from_index"];
            var targetNode = nodes[target];
            if (True(targetNode, "read_complete"))
            {
                var reverse = edges.FirstOrDefault(candidate => (int)candidate["from_index"] == target);
                edge["relationship"] = reverse != null && Equals(reverse["to_index"], source) ? "reciprocal" : "one_way";
            }
            var seen = new HashSet<int>(); var pending = new Stack<int>(); pending.Push(target);
            while (pending.Count != 0)
            {
                int current = pending.Pop();
                if (current == source) { edge["cycle"] = true; break; }
                if (!seen.Add(current)) continue;
                foreach (var next in edges.Where(candidate => Equals(candidate["from_index"], current) && candidate["to_index"] is int))
                    pending.Push((int)next["to_index"]);
            }
        }
        bool complete = errors.Count == 0 && !truncated;
        return new Dictionary<string, object> { ["starts"] = starts, ["nodes"] = nodes, ["edges"] = edges,
            ["node_read_count"] = reads, ["returned_count"] = nodes.Count, ["max_hops"] = request.MaxHops,
            ["max_nodes"] = request.MaxNodes, ["truncated"] = truncated, ["read_complete"] = complete,
            ["scope_complete"] = complete, ["has_more"] = truncated ? (object)true : errors.Count != 0 ? null : (object)false,
            ["open_end_indices"] = nodes.Where(node => Equals(node["link_status"], "open_end")).Select(node => node["index"]).ToList(),
            ["unresolved_indices"] = nodes.Where(node => Equals(node["link_status"], "unresolved")).Select(node => node["index"]).ToList(),
            ["read_errors"] = errors };
    }
}
