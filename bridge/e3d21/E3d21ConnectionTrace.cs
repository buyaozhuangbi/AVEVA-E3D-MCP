using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private sealed class ConnectionTraceNode
    {
        internal int Index;
        internal int Hops;
        internal string RequestedRefno;
        internal string RequestedType;
        internal string Refno;
        internal string Type;
        internal int PointNumber;
        internal string Key;
        internal Dictionary<string, object> Endpoint;
        internal bool Expanded;
        internal string LinkStatus = "not_read";
        internal int? TargetIndex;
    }

    private sealed class ConnectionTraceEdge
    {
        internal int From;
        internal int? To;
        internal string Status;
        internal string TargetRefno;
        internal string TargetType;
        internal int? TargetPoint;
        internal string Relationship = "unknown";
        internal bool Cycle;
        internal string Error;
    }

    public object TraceConnectionNetwork(IList<string> refnos, IList<string> expectedTypes,
        IList<int> pointNumbers, int maxHops, int maxNodes)
    {
        if (refnos == null || expectedTypes == null || pointNumbers == null || refnos.Count != pointNumbers.Count ||
            refnos.Count != expectedTypes.Count || refnos.Count < 1 || refnos.Count > 16)
            throw new ArgumentException("starts must contain between 1 and 16 element/P-point pairs.");
        if (maxHops < 0 || maxHops > 16) throw new ArgumentOutOfRangeException("max_hops", "max_hops must be from 0 through 16.");
        if (maxNodes < refnos.Count || maxNodes > 128) throw new ArgumentOutOfRangeException("max_nodes", "max_nodes must be at least the start count and at most 128.");
        var starts = new List<Tuple<string, string, int>>();
        var startKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        for (int i = 0; i < refnos.Count; i++)
        {
            string refno = ModelEditInput.ExplicitReference(refnos[i], "starts[" + i + "].refno");
            string expectedType = String.IsNullOrWhiteSpace(expectedTypes[i]) ? null : ModelEditInput.TypeName(expectedTypes[i]);
            int point = pointNumbers[i];
            if (point < 1 || point > 10000) throw new ArgumentOutOfRangeException("point_number", "P-point numbers must be from 1 through 10000.");
            if (!startKeys.Add(refno + "\0" + (expectedType ?? "") + "\0" + point.ToString(CultureInfo.InvariantCulture)))
                throw new ArgumentException("starts must not contain duplicate element/P-point pairs.");
            starts.Add(Tuple.Create(refno, expectedType, point));
        }
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
            string ceBefore = ModelCurrentReference();
            Project projectBefore = NativeRead(() => Project.CurrentProject);
            MDB mdbBefore = NativeRead(() => MDB.CurrentMDB);
            if (projectBefore == null || mdbBefore == null) throw new InvalidOperationException("An open E3D project and MDB are required.");
            string projectNameBefore = NativeRead(() => projectBefore.Name);
            string mdbNameBefore = NativeRead(() => mdbBefore.Name);
            var nodes = new List<ConnectionTraceNode>();
            var edges = new List<ConnectionTraceEdge>();
            var errors = new List<object>();
            var byKey = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            var queue = new Queue<int>();
            bool truncated = false;

            Func<string, string, int, string> makeKey = (refno, type, point) =>
                (refno ?? "").Trim().ToUpperInvariant() + "\0" + (type ?? "").Trim().ToUpperInvariant() +
                "\0" + point.ToString(CultureInfo.InvariantCulture);
            Func<string, string, int, int, int> addNode = (refno, expectedType, point, hops) =>
            {
                var endpoint = ConnectionReadTraceEndpoint(refno, point, expectedType);
                Dictionary<string, object> identity = endpoint["element"] as Dictionary<string, object>;
                string actualRefno = identity == null ? refno : identity["refno"] as string ?? refno;
                string actualType = identity == null ? expectedType : identity["type"] as string ?? expectedType;
                string key = makeKey(actualRefno, actualType, point);
                int existing;
                if (byKey.TryGetValue(key, out existing)) return existing;
                int index = nodes.Count;
                var node = new ConnectionTraceNode { Index = index, Hops = hops, RequestedRefno = refno,
                    RequestedType = expectedType, Refno = actualRefno, Type = actualType,
                    PointNumber = point, Key = key, Endpoint = endpoint };
                nodes.Add(node);
                byKey[key] = index;
                queue.Enqueue(index);
                return index;
            };

            foreach (var start in starts) addNode(start.Item1, start.Item2, start.Item3, 0);
            while (queue.Count > 0)
            {
                int index = queue.Dequeue();
                ConnectionTraceNode node = nodes[index];
                node.Expanded = true;
                Dictionary<string, object> point = node.Endpoint["point"] as Dictionary<string, object>;
                Dictionary<string, object> reference = point == null ? null : point["connection_reference"] as Dictionary<string, object>;
                if (reference == null || !Equals(reference["available"], true))
                {
                    node.LinkStatus = "unresolved";
                    Dictionary<string, string> endpointErrors = node.Endpoint["errors"] as Dictionary<string, string>;
                    string code = Equals(node.Endpoint["status"], "not_found") ? "p_point_not_found" :
                        endpointErrors != null && endpointErrors.ContainsKey("exists") ? "ppexst_unavailable" : "pcref_unavailable";
                    string message = endpointErrors == null ? null :
                        String.Join("; ", endpointErrors.Values.Where(value => !String.IsNullOrWhiteSpace(value)).ToArray());
                    errors.Add(new Dictionary<string, object> { ["node_index"] = index, ["code"] = code,
                        ["message"] = String.IsNullOrWhiteSpace(message) ? "P-point existence or PCREF could not be read; connectivity is unknown." : message });
                    continue;
                }
                Dictionary<string, object> resolved = reference["reference"] as Dictionary<string, object>;
                if (resolved != null && Equals(resolved["status"], "unset"))
                {
                    node.LinkStatus = "open_end";
                    continue;
                }
                if (!Equals(reference["resolution_complete"], true) || resolved == null || !Equals(resolved["status"], "resolved"))
                {
                    node.LinkStatus = "unresolved";
                    errors.Add(new Dictionary<string, object> { ["node_index"] = index, ["code"] = "pcref_unresolved",
                        ["message"] = resolved == null ? "PCREF target is unavailable." : Convert.ToString(resolved["error"] ?? "PCREF target did not resolve.") });
                    continue;
                }
                string targetRefno = resolved["refno"] as string;
                string targetType = resolved["type"] as string;
                object rawPoint = reference["point_number"];
                long targetPointLong;
                if (String.IsNullOrWhiteSpace(targetRefno) || String.IsNullOrWhiteSpace(targetType) || rawPoint == null ||
                    !Int64.TryParse(Convert.ToString(rawPoint, CultureInfo.InvariantCulture), NumberStyles.Integer,
                        CultureInfo.InvariantCulture, out targetPointLong) || targetPointLong < 1 || targetPointLong > 10000)
                {
                    node.LinkStatus = "unresolved";
                    errors.Add(new Dictionary<string, object> { ["node_index"] = index, ["code"] = "pcref_target_incomplete",
                        ["message"] = "Resolved PCREF does not contain a usable target element and point number." });
                    continue;
                }
                int targetPoint = (int)targetPointLong;
                var edge = new ConnectionTraceEdge { From = index, Status = "resolved", TargetRefno = targetRefno,
                    TargetType = targetType, TargetPoint = targetPoint };
                node.LinkStatus = "resolved";
                string proposedKey = makeKey(targetRefno, targetType, targetPoint);
                int targetIndex;
                if (byKey.TryGetValue(proposedKey, out targetIndex))
                {
                    edge.To = targetIndex;
                    node.TargetIndex = targetIndex;
                    edge.Status = "target_already_visited";
                }
                else if (node.Hops >= maxHops)
                {
                    edge.Status = "hop_limit";
                    truncated = true;
                    edge.Error = "Target was not inspected because max_hops was reached.";
                }
                else if (nodes.Count >= maxNodes)
                {
                    edge.Status = "node_limit";
                    truncated = true;
                    edge.Error = "Target was not inspected because max_nodes was reached.";
                }
                else
                {
                    targetIndex = addNode(targetRefno, targetType, targetPoint, node.Hops + 1);
                    edge.To = targetIndex;
                    node.TargetIndex = targetIndex;
                }
                edges.Add(edge);
            }

            var adjacency = edges.Where(edge => edge.To.HasValue).GroupBy(edge => edge.From)
                .ToDictionary(group => group.Key, group => group.Select(edge => edge.To.Value).Distinct().ToList());
            foreach (ConnectionTraceEdge edge in edges)
            {
                if (!edge.To.HasValue) continue;
                ConnectionTraceNode target = nodes[edge.To.Value];
                Dictionary<string, object> targetPoint = target.Endpoint["point"] as Dictionary<string, object>;
                Dictionary<string, object> targetReference = targetPoint == null ? null :
                    targetPoint["connection_reference"] as Dictionary<string, object>;
                Dictionary<string, object> targetResolved = targetReference == null ? null :
                    targetReference["reference"] as Dictionary<string, object>;
                if (targetReference != null && Equals(targetReference["available"], true) &&
                    Equals(targetReference["resolution_complete"], true))
                {
                    if (targetResolved != null && Equals(targetResolved["status"], "unset"))
                        edge.Relationship = "one_way";
                    else if (targetResolved != null && Equals(targetResolved["status"], "resolved"))
                    {
                        string reverseRefno = targetResolved["refno"] as string;
                        string reverseType = targetResolved["type"] as string;
                        object reverseRawPoint = targetReference["point_number"];
                        long reversePoint;
                        bool pointsBack = StringComparer.OrdinalIgnoreCase.Equals(reverseRefno, nodes[edge.From].Refno) &&
                            StringComparer.OrdinalIgnoreCase.Equals(reverseType, nodes[edge.From].Type) &&
                            Int64.TryParse(Convert.ToString(reverseRawPoint, CultureInfo.InvariantCulture), NumberStyles.Integer,
                                CultureInfo.InvariantCulture, out reversePoint) && reversePoint == nodes[edge.From].PointNumber;
                        edge.Relationship = String.IsNullOrWhiteSpace(reverseType) ? "unknown" : pointsBack ? "reciprocal" : "one_way";
                    }
                }
                edge.Cycle = HasDirectedPath(edge.To.Value, edge.From, adjacency, new HashSet<int>());
            }
            string ceAfter = ModelCurrentReference();
            Project projectAfter = NativeRead(() => Project.CurrentProject);
            MDB mdbAfter = NativeRead(() => MDB.CurrentMDB);
            string projectNameAfter = projectAfter == null ? null : NativeRead(() => projectAfter.Name);
            string mdbNameAfter = mdbAfter == null ? null : NativeRead(() => mdbAfter.Name);
            bool ceUnchanged = StringComparer.Ordinal.Equals(ceBefore, ceAfter);
            bool identityUnchanged = ReferenceEquals(projectBefore, projectAfter) && ReferenceEquals(mdbBefore, mdbAfter) &&
                StringComparer.Ordinal.Equals(projectNameBefore, projectNameAfter) && StringComparer.Ordinal.Equals(mdbNameBefore, mdbNameAfter);
            if (!ceUnchanged || !identityUnchanged)
                errors.Add(new Dictionary<string, object> { ["code"] = "context_changed",
                    ["message"] = "CE, project or MDB identity changed during the connectivity trace." });
            bool allRead = nodes.All(node => Equals(node.Endpoint["status"], "exists") && node.Expanded);
            bool complete = allRead && !truncated && ceUnchanged && identityUnchanged && errors.Count == 0;
            return new Dictionary<string, object>
            {
                ["success"] = true, ["available"] = nodes.Count > 0, ["complete"] = complete,
                ["scope_complete"] = complete, ["truncated"] = truncated,
                ["has_more"] = truncated ? (bool?)true : (bool?)false,
                ["max_hops"] = maxHops, ["max_nodes"] = maxNodes,
                ["single_dispatch"] = true, ["consistency"] = "single_ui_dispatch",
                ["consistency_scope"] = "Visited endpoints and PCREF edges were read in one UI callback; this is not a database lock.",
                ["nodes"] = nodes.Select(node => new Dictionary<string, object>
                {
                    ["index"] = node.Index, ["hops"] = node.Hops, ["requested_refno"] = node.RequestedRefno,
                    ["requested_type"] = node.RequestedType, ["refno"] = node.Refno, ["type"] = node.Type,
                    ["point_number"] = node.PointNumber, ["link_status"] = node.LinkStatus,
                    ["expanded"] = node.Expanded, ["endpoint"] = node.Endpoint
                }).ToList(),
                ["edges"] = edges.Select(edge => new Dictionary<string, object>
                {
                    ["from_index"] = edge.From, ["to_index"] = edge.To, ["status"] = edge.Status,
                    ["target_refno"] = edge.TargetRefno, ["target_type"] = edge.TargetType,
                    ["target_point_number"] = edge.TargetPoint,
                    ["relationship"] = edge.Relationship, ["cycle"] = edge.Cycle, ["error"] = edge.Error
                }).ToList(),
                ["open_end_indices"] = nodes.Where(node => node.LinkStatus == "open_end").Select(node => node.Index).ToList(),
                ["unresolved_indices"] = nodes.Where(node => node.LinkStatus == "unresolved" || node.Endpoint["status"] as string != "exists").Select(node => node.Index).ToList(),
                ["read_errors"] = errors,
                ["ce_before"] = ceBefore, ["ce_after"] = ceAfter, ["ce_unchanged"] = ceUnchanged,
                ["project_before"] = projectNameBefore, ["project_after"] = projectNameAfter,
                ["mdb_before"] = mdbNameBefore, ["mdb_after"] = mdbNameAfter, ["identity_unchanged"] = identityUnchanged,
                ["source"] = "Native E3D21 P-point PPEXST and PCREF reads in one UI dispatch; element type disambiguates shared DBREF aliases.",
                ["measurement_scope"] = "Directed PCREF trace only. Reciprocal references and cycles are reported separately; geometric proximity, catalogue evidence and trace completeness do not establish engineering connectivity or compatibility."
            };
        });
    }

    private static Dictionary<string, object> ConnectionReadTraceEndpoint(string refno, int pointNumber, string expectedType)
    {
        var errors = new Dictionary<string, string>();
        var endpoint = new Dictionary<string, object>
        {
            ["requested_refno"] = refno, ["requested_type"] = expectedType, ["point_number"] = pointNumber,
            ["status"] = "unknown", ["element"] = null, ["exists"] = ConnectionUnavailable("not_read", null),
            ["point"] = null, ["complete"] = false, ["errors"] = errors
        };
        try
        {
            DbElement element = ResolveElementByType(refno, expectedType);
            if (!IsUsable(element)) throw new InvalidOperationException("Element was not found: " + refno);
            if (NativeRead(() => element.DbType) != DbType.Design)
                throw new InvalidOperationException("The reference does not identify an element in a Design database.");
            endpoint["element"] = ConnectionIdentity(element);
            var qualifier = new DbQualifier();
            qualifier.Add((double)pointNumber);
            Dictionary<string, object> exists = ConnectionReadField("PPEXST", () => new Dictionary<string, object>
            {
                ["value"] = NativeRead(() => element.GetBool(DbAttributeInstance.PPEXST, qualifier))
            });
            endpoint["exists"] = exists;
            if (!Equals(exists["available"], true) || !Equals(exists["value"], true))
            {
                string reason = Equals(exists["available"], true) ? "point_not_found" : "existence_unknown";
                errors["exists"] = exists["error"] as string ?? "P-point existence is unknown.";
                var unavailable = ConnectionUnavailable(reason, null);
                unavailable["attribute"] = "PCREF";
                unavailable["resolution_complete"] = false;
                unavailable["reference"] = null;
                unavailable["point_number"] = null;
                endpoint["status"] = Equals(exists["available"], true) ? "not_found" : "unknown";
                endpoint["point"] = new Dictionary<string, object>
                {
                    ["number"] = pointNumber, ["exists"] = exists, ["connection_reference"] = unavailable,
                    ["complete"] = false, ["errors"] = new Dictionary<string, string> { ["exists"] = errors["exists"] }
                };
                return endpoint;
            }

            Dictionary<string, object> connectionReference = ConnectionReadPointReference(element, qualifier);
            var pointErrors = new Dictionary<string, string>();
            if (connectionReference["error"] is string referenceError) pointErrors["connection_reference"] = referenceError;
            var point = new Dictionary<string, object>
            {
                ["number"] = pointNumber, ["exists"] = exists, ["connection_reference"] = connectionReference,
                ["complete"] = Equals(connectionReference["available"], true) &&
                    Equals(connectionReference["resolution_complete"], true),
                ["errors"] = pointErrors
            };
            endpoint["point"] = point;
            endpoint["status"] = "exists";
            endpoint["complete"] = Equals(point["complete"], true);
            foreach (var pair in pointErrors) errors[pair.Key] = pair.Value;
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

    private static bool HasDirectedPath(int current, int destination, Dictionary<int, List<int>> adjacency, HashSet<int> visited)
    {
        if (current == destination) return true;
        if (!visited.Add(current)) return false;
        List<int> next;
        if (!adjacency.TryGetValue(current, out next)) return false;
        foreach (int node in next)
            if (HasDirectedPath(node, destination, adjacency, visited)) return true;
        return false;
    }
}
