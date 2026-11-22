using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core3D.Graphics;
using Newtonsoft.Json;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private readonly string primitiveVisualRevisionSession = Guid.NewGuid().ToString("N");

    // These are the five values returned by the public native getters. The
    // collection is not evidence of explicit override or inheritance masks.
    private sealed class DrawListPrimitiveVisualEntry
    {
        [JsonProperty(Required = Required.Always)] public string owner_refno;
        [JsonProperty(Required = Required.Always)] public string refno;
        [JsonProperty(Required = Required.Always)] public int colour;
        [JsonProperty(Required = Required.Always)] public int transparency;
        [JsonProperty(Required = Required.Always)] public bool edges;
        [JsonProperty(Required = Required.Always)] public bool visibility;
        [JsonProperty(Required = Required.Always)] public bool shallow;
    }

    private sealed class PrimitiveVisualMap
    {
        internal readonly Dictionary<string, DrawListVisualEntry> Members = new Dictionary<string, DrawListVisualEntry>(StringComparer.Ordinal);
        internal readonly Dictionary<string, DrawListPrimitiveVisualEntry> Entries = new Dictionary<string, DrawListPrimitiveVisualEntry>(StringComparer.Ordinal);
        internal readonly Dictionary<string, DrawListPrimitive> Wrappers = new Dictionary<string, DrawListPrimitive>(StringComparer.Ordinal);
        internal readonly HashSet<string> Owners = new HashSet<string>(StringComparer.Ordinal);
        internal readonly List<string> Errors = new List<string>();
    }

    private sealed class PrimitiveVisualState
    {
        internal DrawList DrawList;
        internal int DrawListId;
        internal string Project, Mdb, Fingerprint;
        internal PrimitiveVisualMap Map;
    }

    private sealed class PrimitiveVisualTarget
    {
        internal string Reference, Type, OwnerReference, ResolvedMemberReference;
        internal string[] Keys;
    }

    private sealed class UnsupportedTransparencyStateException : InvalidOperationException
    {
        internal UnsupportedTransparencyStateException(string message) : base(message) { }
    }

    public object GetDisplaySnapshot()
    {
        const string command = "display-snapshot";
        if (!TryBeginCommand(command)) return BusyResult(command);
        try
        {
            return OnUi(delegate
            {
                var errors = new List<string>();
                var result = new Dictionary<string, object>
                {
                    ["success"] = false, ["complete"] = false,
                    ["members"] = new DrawListVisualEntry[0],
                    ["primitive_entries"] = new DrawListPrimitiveVisualEntry[0],
                    ["member_count"] = 0, ["primitive_entry_count"] = 0,
                    ["drawlist_id"] = null, ["project"] = null, ["mdb"] = null,
                    ["observed_fingerprint"] = null, ["read_errors"] = errors,
                    ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
                    ["visual_state_semantics"] = "Complete listed members and native primitive entries with five observed visual values; hidden override/inheritance masks are not exposed.",
                    ["fingerprint_scope"] = "Observation-only project/MDB, Drawlist ID, exact member map and (owner_refno, refno) primitive map; this fingerprint is not a write revision.",
                    ["pixel_visibility_verified"] = false
                };
                try
                {
                    result["ce_before"] = ModelCurrentReference();
                    PrimitiveVisualState state = PrimitiveVisualReadState();
                    result["drawlist_id"] = state.DrawListId;
                    result["project"] = state.Project;
                    result["mdb"] = state.Mdb;
                    result["members"] = state.Map.Members.OrderBy(item => item.Key, StringComparer.Ordinal).Select(item => item.Value).ToArray();
                    result["primitive_entries"] = state.Map.Entries.OrderBy(item => item.Key, StringComparer.Ordinal).Select(item => item.Value).ToArray();
                    result["member_count"] = state.Map.Members.Count;
                    result["primitive_entry_count"] = state.Map.Entries.Count;
                    errors.AddRange(state.Map.Errors);
                    result["observed_fingerprint"] = state.Fingerprint;
                }
                catch (Exception error) { ThrowIfNativeFaulted(); errors.Add("display_snapshot: " + error.Message); }
                ModelCaptureCeAfter(result);
                if (!Equals(result["ce_unchanged"], true)) errors.Add("Current element could not be verified unchanged during the display snapshot.");
                bool complete = errors.Count == 0;
                result["success"] = complete;
                result["complete"] = complete;
                if (!complete) result["observed_fingerprint"] = null;
                return result;
            });
        }
        finally { EndCommand(); }
    }

    public object SetPrimitiveVisual(string refno, string expectedRevision, string colour, int? transparency,
        bool? edges, bool? visibility, bool? shallow, bool dryRun)
    {
        string requestedTarget = ModelEditInput.ExplicitReference(refno, "refno");
        if (transparency.HasValue && (transparency.Value < 0 || transparency.Value > 100))
            throw new ArgumentOutOfRangeException("transparency", "transparency must be in 0..100.");
        string expectedRevisionValue = expectedRevision == null ? null : expectedRevision.ToLowerInvariant();
        if (expectedRevisionValue != null && (expectedRevisionValue.Length != 64 ||
            expectedRevisionValue.Any(value => !(value >= '0' && value <= '9' || value >= 'a' && value <= 'f'))))
            throw new ArgumentException("expected_revision must contain exactly 64 hexadecimal characters.", "expectedRevision");
        if (!dryRun && expectedRevisionValue == null)
            throw new ArgumentException("Execution requires expected_revision from a current primitive visual preview.", "expectedRevision");
        string command = "primitive-visual " + requestedTarget;
        // The same guard also serializes previews and full-map reads against
        // bridge commands. Acquiring this in-memory guard makes no SDK write.
        if (!TryBeginCommand(command)) return BusyResult(command);
        try
        {
            return OnUi(delegate
            {
                var attempts = new List<string>();
                var result = new Dictionary<string, object>
                {
                    ["success"] = false, ["dry_run"] = dryRun, ["refno"] = requestedTarget,
                    ["revision"] = null, ["revision_conflict"] = false,
                    ["write_attempted"] = false, ["write_applied"] = false,
                    ["readback_verified"] = false, ["all_members_verified"] = false,
                    ["primitive_readback_verified"] = false, ["membership_verified"] = false,
                    ["graphics_updated"] = false, ["setter_attempts"] = attempts,
                    ["add_attempted"] = false, ["refresh_attempted"] = false,
                    ["unsupported_transparency_state"] = false,
                    ["transparency_reset_members"] = new List<string>(),
                    ["partial_write"] = false, ["outcome_uncertain"] = false,
                    ["rollback_attempted"] = false, ["rollback_succeeded"] = null,
                    ["rollback_verified_original"] = null,
                    ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
                    ["visual_scope"] = "Existing native primitive entries for one exact non-TOPF PRMF database reference, including every observed owner alias.",
                    ["visual_state_semantics"] = "Five native observed values and complete entry presence; hidden override/inheritance masks are not exposed.",
                    ["pixel_visibility_verified"] = false,
                    ["revision_scope"] = "Bridge instance, project/MDB, Drawlist ID, requested and resolved target identity/type/owner, resolved member, exact requested fields and complete member/primitive observed maps.",
                    ["persistence"] = DrawListPersistenceResult(dryRun ? "preview" : "preflight_rejected")
                };
                PrimitiveVisualState before = null;
                try
                {
                    result["ce_before"] = ModelCurrentReference();
                    int? colourIndex = ResolveColourIndex(colour);
                    if (!colourIndex.HasValue && !transparency.HasValue && !edges.HasValue && !visibility.HasValue && !shallow.HasValue)
                        throw new ArgumentException("Supply at least one primitive visual field.");
                    before = PrimitiveVisualReadState();
                    PrimitiveVisualRequireComplete(before.Map);
                    PrimitiveVisualTarget target = PrimitiveVisualReadTarget(before, requestedTarget);
                    string revision = PrimitiveVisualRevision(before, target, requestedTarget, colour, colourIndex, transparency, edges, visibility, shallow);
                    result["revision"] = revision;
                    result["refno"] = target.Reference;
                    result["type"] = target.Type;
                    result["top_level_element"] = false;
                    result["primitive_element"] = true;
                    result["resolved_member_refno"] = target.ResolvedMemberReference;
                    result["primitive_match_count"] = target.Keys.Length;
                    result["target_owner_refnos"] = target.Keys.Select(key => before.Map.Entries[key].owner_refno).ToArray();
                    result["drawlist_id"] = before.DrawListId;
                    result["observed_fingerprint_before"] = before.Fingerprint;
                    result["member_count"] = before.Map.Members.Count;
                    result["primitive_entry_count"] = before.Map.Entries.Count;
                    result["colour_index"] = colourIndex;
                    result["request"] = new Dictionary<string, object>
                    {
                        ["refno"] = requestedTarget, ["colour"] = colour, ["transparency"] = transparency,
                        ["edges"] = edges, ["visibility"] = visibility, ["shallow"] = shallow
                    };
                    if (expectedRevisionValue != null && expectedRevisionValue != revision)
                    {
                        result["revision_conflict"] = true;
                        throw new InvalidOperationException("expected_revision does not match this request, target or complete display state. Preview again; no write was attempted.");
                    }
                    PrimitiveVisualRequireCeUnchanged(result);
                    var previewEntries = before.Map.Entries.ToDictionary(item => item.Key, item => PrimitiveVisualClone(item.Value), StringComparer.Ordinal);
                    foreach (string key in target.Keys)
                        if (transparency.HasValue) previewEntries[key].transparency = transparency.Value;
                    PrimitiveVisualPreflightTransparency(before.DrawList, before.Map, previewEntries, before.Map.Members);
                    if (dryRun)
                    {
                        result["success"] = true;
                        return result;
                    }

                    // Repeat the entire read immediately before writing. Any
                    // changed request/context/member/primitive state is rejected.
                    PrimitiveVisualState immediate = PrimitiveVisualReadState();
                    PrimitiveVisualRequireComplete(immediate.Map);
                    PrimitiveVisualTarget immediateTarget = PrimitiveVisualReadTarget(immediate, requestedTarget);
                    if (PrimitiveVisualRevision(immediate, immediateTarget, requestedTarget, colour, colourIndex, transparency, edges, visibility, shallow) != revision)
                    {
                        result["revision_conflict"] = true;
                        throw new InvalidOperationException("Display state changed during preflight; no write was attempted.");
                    }
                    before = immediate;
                    target = immediateTarget;
                    PrimitiveVisualRequireCeUnchanged(result);
                    var wantedEntries = before.Map.Entries.ToDictionary(item => item.Key, item => PrimitiveVisualClone(item.Value), StringComparer.Ordinal);
                    foreach (string key in target.Keys)
                    {
                        ThrowIfNativeFaulted();
                        DrawListPrimitiveVisualEntry wanted = wantedEntries[key];
                        if (colourIndex.HasValue) wanted.colour = colourIndex.Value;
                        if (transparency.HasValue) wanted.transparency = transparency.Value;
                        if (edges.HasValue) wanted.edges = edges.Value;
                        if (visibility.HasValue) wanted.visibility = visibility.Value;
                        if (shallow.HasValue) wanted.shallow = shallow.Value;
                    }
                    ApplyDrawListPrimitiveSnapshot(before.DrawList, wantedEntries, true, before.Map.Members, field =>
                        {
                            result["write_attempted"] = true;
                            result["persistence"] = DrawListPersistenceResult("display_failed");
                            attempts.Add(field);
                        }, (List<string>)result["transparency_reset_members"]);
                    if (Equals(result["write_attempted"], true))
                    {
                        result["refresh_attempted"] = true;
                        PrimitiveVisualRefresh();
                        result["graphics_updated"] = true;
                    }
                    PrimitiveVisualRequireContext(before);
                    PrimitiveVisualState after = PrimitiveVisualReadState();
                    PrimitiveVisualRequireComplete(after.Map);
                    Dictionary<string, string> memberDifferences = CompareDrawListVisualSnapshots(before.Map.Members, after.Map.Members);
                    Dictionary<string, string> primitiveDifferences = CompareDrawListPrimitiveSnapshots(wantedEntries, after.Map.Entries);
                    result["verification_errors"] = memberDifferences;
                    result["primitive_verification_errors"] = primitiveDifferences;
                    result["observed_fingerprint_after"] = after.Fingerprint;
                    if (memberDifferences.Count != 0 || primitiveDifferences.Count != 0)
                        throw new InvalidOperationException("Complete display readback differs from the requested primitive values or changed another member/primitive entry.");
                    PrimitiveVisualRequireCeUnchanged(result);
                    result["readback_verified"] = true;
                    result["all_members_verified"] = true;
                    result["primitive_readback_verified"] = true;
                    result["membership_verified"] = true;
                    result["write_applied"] = Equals(result["write_attempted"], true);
                    result["target_entries"] = target.Keys.Select(key => after.Map.Entries[key]).ToArray();
                    result["success"] = true;
                }
                catch (Exception error)
                {
                    ThrowIfNativeFaulted();
                    result["error"] = error.Message;
                    result["unsupported_transparency_state"] = error is UnsupportedTransparencyStateException;
                    if (before != null && Equals(result["write_attempted"], true))
                        PrimitiveVisualRollback(before, result);
                    else ModelCaptureCeAfter(result);
                }
                // Persistence is separate from the successful visual operation.
                // Failure must never roll back or replay verified native writes.
                if (Equals(result["success"], true))
                    result["persistence"] = SaveDrawListState(before.DrawList);
                return result;
            });
        }
        finally { EndCommand(); }
    }

    private static PrimitiveVisualMap PrimitiveVisualReadMap(DrawList drawList, bool memberValues = true, bool consistentAliases = true)
    {
        ThrowIfNativeFaulted();
        var map = new PrimitiveVisualMap();
        if (drawList == null) { map.Errors.Add("No Drawlist is available."); return map; }
        DrawListMember[] members;
        try
        {
            members = NativeRead(() => drawList.Members());
            if (members == null) throw new InvalidOperationException("SDK returned no member collection.");
        }
        catch (Exception error) { ThrowIfNativeFaulted(); map.Errors.Add("members: " + error.Message); return map; }
        if (members.Length > DisplayStateMemberLimit)
            map.Errors.Add("The native member collection exceeds the 10000-member limit.");
        int primitiveCount = 0;
        var aliases = new Dictionary<string, DrawListPrimitiveVisualEntry>(StringComparer.Ordinal);
        for (int memberIndex = 0; memberIndex < Math.Min(members.Length, DisplayStateMemberLimit); memberIndex++)
        {
            ThrowIfNativeFaulted();
            string label = "member[" + memberIndex + "]";
            try
            {
                DrawListMember member = members[memberIndex];
                DbElement memberElement = member == null ? null : NativeRead(() => member.DbElement);
                if (!IsUsable(memberElement))
                    throw new InvalidOperationException("SDK returned an unreadable exact member.");
                string owner = ModelReference(NativeRead(() => memberElement.Ref));
                if (!map.Owners.Add(owner)) throw new InvalidOperationException("Duplicate exact member reference: " + owner);
                if (memberValues)
                {
                    try { map.Members.Add(owner, CaptureDrawListVisual(member)); }
                    catch (Exception error) { ThrowIfNativeFaulted(); map.Errors.Add(label + ".visual: " + error.Message); }
                }
                DrawListPrimitive[] primitives = NativeRead(() => member.Primitives);
                if (primitives == null) throw new InvalidOperationException("SDK returned no primitive collection.");
                int remaining = DisplayStatePrimitiveLimit - primitiveCount;
                if (primitives.Length > remaining)
                    map.Errors.Add("The complete native primitive collection exceeds the global 20000-entry limit, including owner aliases.");
                int readable = Math.Min(primitives.Length, remaining);
                primitiveCount += readable;
                for (int index = 0; index < readable; index++)
                {
                    ThrowIfNativeFaulted();
                    try
                    {
                        DrawListPrimitive primitive = primitives[index];
                        if (primitive == null || !IsUsable(NativeRead(() => primitive.DbElement)))
                            throw new InvalidOperationException("SDK returned an unreadable primitive entry.");
                        DbElement element = NativeRead(() => primitive.DbElement);
                        string reference = ModelReference(NativeRead(() => element.Ref));
                        DisplayStateRequireDatabaseSubtree(element, owner);
                        var entry = new DrawListPrimitiveVisualEntry
                        {
                            owner_refno = owner, refno = reference, colour = NativeRead(() => primitive.Colour),
                            transparency = NativeRead(() => primitive.Transparency), edges = NativeRead(() => primitive.Edges),
                            visibility = NativeRead(() => primitive.Visibility), shallow = NativeRead(() => primitive.ShallowRendering)
                        };
                        if (entry.transparency < 0 || entry.transparency > 100)
                            throw new InvalidOperationException("Native primitive transparency is outside 0..100.");
                        string key = PrimitiveVisualKey(entry);
                        if (map.Entries.ContainsKey(key)) throw new InvalidOperationException("Duplicate primitive entry under the same owner: " + key);
                        map.Entries.Add(key, entry);
                        map.Wrappers.Add(key, primitive);
                        DrawListPrimitiveVisualEntry previous;
                        if (aliases.TryGetValue(reference, out previous))
                        {
                            if (consistentAliases && !PrimitiveVisualValuesEqual(previous, entry))
                                map.Errors.Add("Native owner aliases have different observed values for " + reference + ".");
                        }
                        else aliases.Add(reference, entry);
                    }
                    catch (Exception error) { ThrowIfNativeFaulted(); map.Errors.Add(label + ".primitive[" + index + "]: " + error.Message); }
                }
            }
            catch (Exception error) { ThrowIfNativeFaulted(); map.Errors.Add(label + ": " + error.Message); }
        }
        return map;
    }

    private static PrimitiveVisualState PrimitiveVisualReadState(bool consistentAliases = true)
    {
        ThrowIfNativeFaulted();
        Project project = NativeRead(() => Project.CurrentProject);
        MDB mdb = CurrentMdb();
        if (project == null || mdb == null || string.IsNullOrWhiteSpace(NativeRead(() => project.Name)) || string.IsNullOrWhiteSpace(NativeRead(() => mdb.Name)))
            throw new InvalidOperationException("Project/MDB identity is unavailable.");
        DrawListManager manager = NativeRead(() => DrawListManager.Instance);
        if (manager == null || NativeRead(() => manager.CurrentDrawList) == null)
            throw new InvalidOperationException("No current Drawlist is available.");
        DrawList drawList = NativeRead(() => manager.CurrentDrawList);
        var state = new PrimitiveVisualState
        {
            DrawList = drawList, DrawListId = NativeRead(() => drawList.ID), Project = NativeRead(() => project.Name), Mdb = NativeRead(() => mdb.Name),
            Map = PrimitiveVisualReadMap(drawList, true, consistentAliases)
        };
        try { PrimitiveVisualRequireContext(state); }
        catch (Exception error) { ThrowIfNativeFaulted(); state.Map.Errors.Add("context: " + error.Message); }
        if (state.Map.Errors.Count == 0)
            state.Fingerprint = PrimitiveHash(writer =>
            {
                writer.Write("display-observation-v1"); writer.Write(state.Project); writer.Write(state.Mdb); writer.Write(state.DrawListId);
                PrimitiveVisualWriteMap(writer, state.Map);
            });
        return state;
    }

    private static void PrimitiveVisualRequireContext(PrimitiveVisualState state)
    {
        ThrowIfNativeFaulted();
        Project project = NativeRead(() => Project.CurrentProject);
        MDB mdb = CurrentMdb();
        DrawListManager manager = NativeRead(() => DrawListManager.Instance);
        if (project == null || mdb == null || NativeRead(() => project.Name) != state.Project || NativeRead(() => mdb.Name) != state.Mdb ||
            manager == null || NativeRead(() => manager.CurrentDrawList) == null || NativeRead(() => manager.CurrentDrawList.ID) != state.DrawListId || NativeRead(() => state.DrawList.ID) != state.DrawListId)
            throw new InvalidOperationException("Project/MDB or the current Drawlist changed during the operation.");
    }

    private static PrimitiveVisualTarget PrimitiveVisualReadTarget(PrimitiveVisualState state, string requested)
    {
        ThrowIfNativeFaulted();
        DbElement element = ModelRequireElement(requested);
        if (element.DbType != DbType.Design) throw new InvalidOperationException("A usable Design primitive is required.");
        string reference = ModelReference(NativeRead(() => element.Ref));
        DbElementType actualType = element.GetActualType();
        if (actualType == null || !actualType.IsValid) throw new InvalidOperationException("Native actual type metadata is unavailable.");
        string type = element.GetElementType().ShortName;
        if (DisplayStateTopLevelElement(element) || !actualType.GetBool(DbNounField.PRMF) || type == "PLOO" || type == "PAVE")
            throw new InvalidOperationException("Only native non-TOPF PRMF elements with existing primitive entries are supported; no ancestor member or entry creation is substituted.");
        if (state.Map.Members.ContainsKey(reference))
            throw new InvalidOperationException("The requested database reference is also an exact Drawlist member and is outside primitive-only scope.");
        string[] keys = state.Map.Entries.Where(item => item.Value.refno == reference).Select(item => item.Key).OrderBy(key => key, StringComparer.Ordinal).ToArray();
        if (keys.Length == 0) throw new InvalidOperationException("No existing native primitive entry was observed for the requested reference; no write or Add is permitted.");
        DrawListMember resolved = state.DrawList.GetDrawListMember(element);
        if (resolved == null || !IsUsable(resolved.DbElement))
            throw new InvalidOperationException("No usable native display member resolves for the requested primitive.");
        string resolvedReference = ModelReference(resolved.DbElement.Ref);
        if (!state.Map.Members.ContainsKey(resolvedReference) || !DisplayStateTopLevelElement(resolved.DbElement) ||
            !state.Map.Entries.ContainsKey(resolvedReference + "|" + reference))
            throw new InvalidOperationException("The resolved TOPF member must be listed and contain an existing entry for the exact primitive reference.");
        DisplayStateRequireDatabaseSubtree(element, resolvedReference);
        if (!IsUsable(element.Owner)) throw new InvalidOperationException("The primitive database owner is unavailable.");
        return new PrimitiveVisualTarget
        {
            Reference = reference, Type = actualType.Name, OwnerReference = ModelReference(element.Owner.Ref),
            ResolvedMemberReference = resolvedReference, Keys = keys
        };
    }

    private string PrimitiveVisualRevision(PrimitiveVisualState state, PrimitiveVisualTarget target,
        string requested, string colour, int? colourIndex, int? transparency, bool? edges, bool? visibility, bool? shallow)
    {
        return PrimitiveHash(writer =>
        {
            writer.Write("primitive-visual-request-v1"); writer.Write(primitiveVisualRevisionSession);
            writer.Write(state.Project); writer.Write(state.Mdb); writer.Write(state.DrawListId);
            writer.Write(requested); writer.Write(target.Reference); writer.Write(target.Type);
            writer.Write(target.OwnerReference); writer.Write(target.ResolvedMemberReference);
            writer.Write(colour != null); if (colour != null) writer.Write(colour);
            writer.Write(colourIndex.HasValue); if (colourIndex.HasValue) writer.Write(colourIndex.Value);
            writer.Write(transparency.HasValue); if (transparency.HasValue) writer.Write(transparency.Value);
            writer.Write(edges.HasValue); if (edges.HasValue) writer.Write(edges.Value);
            writer.Write(visibility.HasValue); if (visibility.HasValue) writer.Write(visibility.Value);
            writer.Write(shallow.HasValue); if (shallow.HasValue) writer.Write(shallow.Value);
            PrimitiveVisualWriteMap(writer, state.Map);
        });
    }

    private static void PrimitiveVisualWriteMap(BinaryWriter writer, PrimitiveVisualMap map)
    {
        writer.Write(map.Members.Count);
        foreach (DrawListVisualEntry member in map.Members.OrderBy(item => item.Key, StringComparer.Ordinal).Select(item => item.Value))
        {
            ThrowIfNativeFaulted();
            writer.Write(member.refno); writer.Write(member.colour); writer.Write(member.transparency);
            writer.Write(member.edges); writer.Write(member.visibility); writer.Write(member.shallow);
        }
        writer.Write(map.Entries.Count);
        foreach (DrawListPrimitiveVisualEntry entry in map.Entries.OrderBy(item => item.Key, StringComparer.Ordinal).Select(item => item.Value))
        {
            ThrowIfNativeFaulted();
            writer.Write(entry.owner_refno); writer.Write(entry.refno); writer.Write(entry.colour); writer.Write(entry.transparency);
            writer.Write(entry.edges); writer.Write(entry.visibility); writer.Write(entry.shallow);
        }
    }

    private static void PrimitiveVisualRequireComplete(PrimitiveVisualMap map)
    {
        if (map.Errors.Count != 0)
            throw new InvalidOperationException("A complete bounded native display observation is required: " + string.Join("; ", map.Errors));
    }

    private static void PrimitiveVisualRequireCeUnchanged(Dictionary<string, object> result)
    {
        ThrowIfNativeFaulted();
        result["ce_after"] = ModelCurrentReference();
        ModelCeUnchanged(result);
        if (!Equals(result["ce_unchanged"], true))
            throw new InvalidOperationException("Current element could not be verified unchanged.");
    }

    private static void PrimitiveVisualRefresh()
    {
        ThrowIfNativeFaulted();
        DrawListManager manager = NativeRead(() => DrawListManager.Instance);
        if (manager == null) throw new InvalidOperationException("The Drawlist manager is unavailable for graphics refresh.");
        NativeWrite(() => manager.UpdateGraphics());
    }

    private static string PrimitiveVisualKey(DrawListPrimitiveVisualEntry entry)
    {
        return entry.owner_refno + "|" + entry.refno;
    }

    private static DrawListPrimitiveVisualEntry PrimitiveVisualClone(DrawListPrimitiveVisualEntry entry)
    {
        return new DrawListPrimitiveVisualEntry
        {
            owner_refno = entry.owner_refno, refno = entry.refno, colour = entry.colour, transparency = entry.transparency,
            edges = entry.edges, visibility = entry.visibility, shallow = entry.shallow
        };
    }

    private static bool PrimitiveVisualValuesEqual(DrawListPrimitiveVisualEntry left, DrawListPrimitiveVisualEntry right)
    {
        return left.colour == right.colour && left.transparency == right.transparency && left.edges == right.edges &&
            left.visibility == right.visibility && left.shallow == right.shallow;
    }

    private static Dictionary<string, DrawListPrimitiveVisualEntry> CaptureDrawListPrimitiveSnapshot(DrawList drawList)
    {
        ThrowIfNativeFaulted();
        PrimitiveVisualMap map = PrimitiveVisualReadMap(drawList, false);
        PrimitiveVisualRequireComplete(map);
        return map.Entries;
    }

    private static Dictionary<string, string> CompareDrawListPrimitiveSnapshots(
        IDictionary<string, DrawListPrimitiveVisualEntry> expected, IDictionary<string, DrawListPrimitiveVisualEntry> actual)
    {
        var differences = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (KeyValuePair<string, DrawListPrimitiveVisualEntry> saved in expected)
        {
            ThrowIfNativeFaulted();
            DrawListPrimitiveVisualEntry current;
            if (!actual.TryGetValue(saved.Key, out current)) { differences[saved.Key] = "Native primitive entry is missing."; continue; }
            DrawListPrimitiveVisualEntry wanted = saved.Value;
            var fields = new List<string>();
            if (wanted.owner_refno != current.owner_refno || wanted.refno != current.refno) fields.Add("identity");
            if (wanted.colour != current.colour) fields.Add("colour");
            if (wanted.transparency != current.transparency) fields.Add("transparency");
            if (wanted.edges != current.edges) fields.Add("edges");
            if (wanted.visibility != current.visibility) fields.Add("visibility");
            if (wanted.shallow != current.shallow) fields.Add("shallow");
            if (fields.Count != 0) differences[saved.Key] = "Observed fields differ: " + string.Join(", ", fields);
        }
        foreach (string key in actual.Keys)
            if (!expected.ContainsKey(key)) differences[key] = "Unexpected native primitive entry is present.";
        return differences;
    }

    private static void ApplyDrawListPrimitiveSnapshot(DrawList drawList,
        IDictionary<string, DrawListPrimitiveVisualEntry> expected, bool requireExactMembership = true,
        IDictionary<string, DrawListVisualEntry> expectedMembers = null, Action<string> attempted = null,
        IList<string> resetMembers = null)
    {
        ThrowIfNativeFaulted();
        // A failed write can leave aliases disagreeing. Read all actual values
        // without accepting that state as a complete successful observation;
        // the compensation can reunify them through one existing DBref wrapper.
        PrimitiveVisualMap actual = PrimitiveVisualReadMap(drawList, true, false);
        PrimitiveVisualRequireComplete(actual);
        if (expected.Keys.Any(key => !actual.Entries.ContainsKey(key)))
            throw new InvalidOperationException("An expected native primitive entry is missing; no replacement entry will be created.");
        if (requireExactMembership && (expected.Count != actual.Entries.Count || actual.Entries.Keys.Any(key => !expected.ContainsKey(key))))
            throw new InvalidOperationException("Native primitive membership differs from the required exact snapshot; no entries will be removed or created.");
        // Compensation retains extra entries with their current values. A bulk
        // zero reset must never erase extras and then ignore them in readback.
        var completeExpected = actual.Entries.ToDictionary(item => item.Key, item => PrimitiveVisualClone(item.Value), StringComparer.Ordinal);
        foreach (var entry in expected) completeExpected[entry.Key] = entry.Value;
        var completeMembers = actual.Members.ToDictionary(item => item.Key, item => CloneDrawListVisual(item.Value), StringComparer.Ordinal);
        if (expectedMembers != null)
            foreach (var member in expectedMembers)
            {
                if (!completeMembers.ContainsKey(member.Key)) throw new InvalidOperationException("Expected member missing during primitive compensation: " + member.Key);
                completeMembers[member.Key] = member.Value;
            }
        Dictionary<string, DrawListPrimitiveVisualEntry> targets = PrimitiveVisualValidateExpected(completeExpected);
        string[] zeroMembers = PrimitiveVisualPreflightTransparency(drawList, actual, completeExpected, completeMembers);
        // Membership stays fixed for this captured map. Index only alias keys
        // before any native write; visual values must still be read dynamically.
        var aliasGroups = new Dictionary<string, List<string>>(StringComparer.Ordinal);
        foreach (KeyValuePair<string, DrawListPrimitiveVisualEntry> entry in actual.Entries)
        {
            List<string> keys;
            if (!aliasGroups.TryGetValue(entry.Value.refno, out keys))
            {
                keys = new List<string>();
                aliasGroups.Add(entry.Value.refno, keys);
            }
            keys.Add(entry.Key);
        }
        var aliasKeys = aliasGroups.ToDictionary(group => group.Key,
            group => group.Value.OrderBy(key => key, StringComparer.Ordinal).ToArray(), StringComparer.Ordinal);
        foreach (string owner in zeroMembers)
        {
            DrawListVisualEntry wanted = completeMembers[owner];
            DrawListMember member = RequireExactDrawListMember(drawList, ModelRequireElement(owner));
            if (attempted != null) attempted("member_visual_properties:" + owner);
            if (resetMembers != null) resetMembers.Add(owner);
            // E3D 2.1's individual primitive setter clamps 0 to 1. This public
            // aggregate member method calls the native aggregate primitive
            // setter, which preserves zero. It also propagates three other
            // fields, so every existing primitive is compensated below.
            NativeWrite(() => member.SetVisualProperties(wanted.visibility, wanted.colour, 0, wanted.edges));
        }
        // Complete preflight precedes every setter, including all owner-chain
        // proofs and expected alias consistency. Native observed zero values
        // are passed through without guessing their inheritance semantics.
        foreach (KeyValuePair<string, DrawListPrimitiveVisualEntry> target in targets.OrderBy(item => item.Key, StringComparer.Ordinal))
        {
            ThrowIfNativeFaulted();
            string[] keys;
            if (!aliasKeys.TryGetValue(target.Key, out keys) || keys.Length == 0)
                throw new InvalidOperationException("No existing primitive wrapper is available for " + target.Key);
            // Earlier setters can affect a later reference, and a setter can
            // materialize an override that changes other fields on this same
            // reference. Re-read every alias after each actual compensation.
            PrimitiveVisualApplyExisting(actual.Wrappers[keys[0]],
                () => PrimitiveVisualReadExistingAliases(actual, keys), target.Value, attempted);
        }
        Dictionary<string, DrawListPrimitiveVisualEntry> after = CaptureDrawListPrimitiveSnapshot(drawList);
        Dictionary<string, string> differences = CompareDrawListPrimitiveSnapshots(completeExpected, after);
        Dictionary<string, string> memberDifferences = CompareDrawListVisualSnapshots(completeMembers, CaptureDrawListVisualSnapshot(drawList));
        if (memberDifferences.Count != 0)
            throw new InvalidOperationException("Primitive compensation changed member values: " + string.Join("; ", memberDifferences.Select(item => item.Key + ": " + item.Value)));
        if (differences.Count != 0)
            throw new InvalidOperationException("Primitive observed-value restoration failed full readback: " + string.Join("; ", differences.Select(item => item.Key + ": " + item.Value)));
    }

    private static string[] PrimitiveVisualPreflightTransparency(DrawList drawList, PrimitiveVisualMap actual,
        IDictionary<string, DrawListPrimitiveVisualEntry> expected, IDictionary<string, DrawListVisualEntry> expectedMembers)
    {
        ThrowIfNativeFaulted();
        PrimitiveVisualRequireComplete(actual);
        PrimitiveVisualValidateExpected(expected);
        foreach (var member in expectedMembers)
            if (member.Value.transparency == 100 && (!actual.Members.ContainsKey(member.Key) || actual.Members[member.Key].transparency != 100))
                throw new UnsupportedTransparencyStateException("E3D 2.1's individual member transparency setter clamps 100 to 99; exact member transparency 100 is unsupported by this restoration path.");
        var reset = new HashSet<string>(StringComparer.Ordinal);
        foreach (var group in expected.Values.GroupBy(entry => entry.refno))
        {
            DrawListPrimitiveVisualEntry wanted = group.First();
            if (wanted.transparency != 0) continue;
            bool missing = group.Any(entry => !actual.Entries.ContainsKey(PrimitiveVisualKey(entry)));
            bool zeroWrite = group.Any(entry => actual.Entries.ContainsKey(PrimitiveVisualKey(entry)) && actual.Entries[PrimitiveVisualKey(entry)].transparency != 0);
            bool propagatedWrite = group.Any(entry => expectedMembers.ContainsKey(entry.owner_refno) && actual.Members.ContainsKey(entry.owner_refno) &&
                expectedMembers[entry.owner_refno].transparency != actual.Members[entry.owner_refno].transparency);
            // Existing unusual observations are valid for reads and no-ops.
            if (!zeroWrite && !propagatedWrite && !missing) continue;
            DbElement element = ModelRequireElement(wanted.refno);
            string owner = null;
            if (missing)
            {
                DbElement current = element;
                var seen = new HashSet<string>(StringComparer.Ordinal);
                for (int depth = 0; depth < 256 && IsUsable(current); depth++)
                {
                    string reference = ElementRef(current);
                    if (!seen.Add(reference)) break;
                    if (expectedMembers.ContainsKey(reference)) { owner = reference; break; }
                    if (NativeRead(() => current.GetElementType()).IsWorld) break;
                    current = NativeRead(() => current.Owner);
                }
            }
            else
            {
                DrawListMember member = NativeRead(() => drawList.GetDrawListMember(element));
                if (member != null && IsUsable(NativeRead(() => member.DbElement))) owner = ElementRef(NativeRead(() => member.DbElement));
            }
            if (owner == null) throw new InvalidOperationException("No resolved member for zero transparency restoration: " + wanted.refno);
            DisplayStateRequireDatabaseSubtree(element, owner);
            if (!expectedMembers.ContainsKey(owner) || !expected.ContainsKey(owner + "|" + wanted.refno))
                throw new InvalidOperationException("Resolved member/primitive alias is absent from complete restoration map: " + owner + "|" + wanted.refno);
            if (expectedMembers[owner].transparency != 0)
                throw new UnsupportedTransparencyStateException("E3D 2.1 cannot restore primitive transparency 0 under resolved member " + owner +
                    " with requested transparency " + expectedMembers[owner].transparency.ToString(CultureInfo.InvariantCulture) +
                    " while preserving the complete display state. The primitive setter clamps 0 to 1; no write was attempted by this preflight.");
            if (zeroWrite) reset.Add(owner);
        }
        return reset.OrderBy(reference => reference, StringComparer.Ordinal).ToArray();
    }

    private static bool PrimitiveVisualInSubtree(DbElement element, string ancestor)
    {
        ThrowIfNativeFaulted();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        DbElement current = element;
        for (int depth = 0; depth < 256 && IsUsable(current); depth++)
        {
            string reference = ElementRef(current);
            if (!seen.Add(reference)) throw new InvalidOperationException("Database owner cycle during transparency preflight.");
            if (reference == ancestor) return true;
            if (NativeRead(() => current.GetElementType()).IsWorld) return false;
            current = NativeRead(() => current.Owner);
        }
        throw new InvalidOperationException("Incomplete database owner chain during transparency preflight.");
    }

    private static Dictionary<string, DrawListPrimitiveVisualEntry> PrimitiveVisualValidateExpected(IDictionary<string, DrawListPrimitiveVisualEntry> expected)
    {
        if (expected == null || expected.Count > DisplayStatePrimitiveLimit)
            throw new InvalidOperationException("A primitive snapshot within the global 20000-entry limit is required.");
        var targets = new Dictionary<string, DrawListPrimitiveVisualEntry>(StringComparer.Ordinal);
        foreach (KeyValuePair<string, DrawListPrimitiveVisualEntry> saved in expected)
        {
            ThrowIfNativeFaulted();
            DrawListPrimitiveVisualEntry entry = saved.Value;
            if (entry == null || !PrimitiveVisualCanonicalReference(entry.owner_refno) || !PrimitiveVisualCanonicalReference(entry.refno) ||
                saved.Key != PrimitiveVisualKey(entry) || entry.transparency < 0 || entry.transparency > 100)
                throw new InvalidOperationException("A primitive snapshot contains an invalid identity, key or observed value.");
            DrawListPrimitiveVisualEntry alias;
            if (targets.TryGetValue(entry.refno, out alias))
            {
                if (!PrimitiveVisualValuesEqual(alias, entry))
                    throw new InvalidOperationException("Expected owner aliases disagree for primitive " + entry.refno);
            }
            else targets.Add(entry.refno, entry);
        }
        return targets;
    }

    private static bool PrimitiveVisualCanonicalReference(string value)
    {
        if (string.IsNullOrEmpty(value) || !value.StartsWith("=", StringComparison.Ordinal)) return false;
        string[] pair = value.Substring(1).Split('/');
        int database, element;
        return pair.Length == 2 && int.TryParse(pair[0], NumberStyles.None, CultureInfo.InvariantCulture, out database) && database > 0 &&
            int.TryParse(pair[1], NumberStyles.None, CultureInfo.InvariantCulture, out element) && element >= 0 &&
            value == ModelReference(new[] { database, element });
    }

    private static void PrimitiveVisualApplyExisting(DrawListPrimitive primitive,
        Func<IList<DrawListPrimitiveVisualEntry>> readAliases, DrawListPrimitiveVisualEntry wanted, Action<string> attempted)
    {
        ThrowIfNativeFaulted();
        // Public wrappers can exist before their hidden native override does.
        // Its first setter may materialize other fields with different values.
        // Only explicit mismatches are written; an already matching reference
        // uses one read and no setter. Each write must finish its fault barrier
        // before any further read, planning, or compensation.
        Func<IList<DrawListPrimitiveVisualEntry>> observe = () =>
        {
            IList<DrawListPrimitiveVisualEntry> observed = NativeRead(readAliases);
            if (observed == null || observed.Count == 0 || observed.Any(entry => entry == null))
                throw new InvalidOperationException("Existing primitive aliases could not be read completely during compensation.");
            if (wanted.transparency == 0 && observed.Any(entry => entry.transparency != 0))
                throw new InvalidOperationException("Zero transparency requires the verified aggregate member reset; the primitive setter would clamp it to 1.");
            return observed;
        };
        IList<DrawListPrimitiveVisualEntry> actualAliases = observe();
        // A second pass covers fields checked before a later setter materialized
        // an override. This is bounded compensation within the original request,
        // at most ten setters, never a replay of the request or an unbounded retry.
        for (int pass = 0; pass < 2; pass++)
        {
            if (actualAliases.All(entry => PrimitiveVisualValuesEqual(entry, wanted))) return;
            if (actualAliases.Any(entry => entry.colour != wanted.colour))
            {
                if (attempted != null) attempted("colour"); NativeWrite(() => primitive.Colour = wanted.colour);
                actualAliases = observe();
            }
            if (actualAliases.Any(entry => entry.transparency != wanted.transparency))
            {
                if (attempted != null) attempted("transparency"); NativeWrite(() => primitive.Transparency = wanted.transparency);
                actualAliases = observe();
            }
            if (actualAliases.Any(entry => entry.edges != wanted.edges))
            {
                if (attempted != null) attempted("edges"); NativeWrite(() => primitive.Edges = wanted.edges);
                actualAliases = observe();
            }
            if (actualAliases.Any(entry => entry.visibility != wanted.visibility))
            {
                if (attempted != null) attempted("visibility"); NativeWrite(() => primitive.Visibility = wanted.visibility);
                actualAliases = observe();
            }
            if (actualAliases.Any(entry => entry.shallow != wanted.shallow))
            {
                if (attempted != null) attempted("shallow"); NativeWrite(() => primitive.ShallowRendering = wanted.shallow);
                actualAliases = observe();
            }
        }
        if (!actualAliases.All(entry => PrimitiveVisualValuesEqual(entry, wanted)))
            throw new InvalidOperationException("Primitive observed values did not converge after two compensation passes: " + wanted.refno);
    }

    private static DrawListPrimitiveVisualEntry[] PrimitiveVisualReadExistingAliases(PrimitiveVisualMap map, IEnumerable<string> keys)
    {
        ThrowIfNativeFaulted();
        return keys.Select(key =>
        {
            DrawListPrimitive primitive = map.Wrappers[key];
            DrawListPrimitiveVisualEntry captured = map.Entries[key];
            if (!IsUsable(NativeRead(() => primitive.DbElement)) || ModelReference(NativeRead(() => NativeRead(() => primitive.DbElement).Ref)) != captured.refno)
                throw new InvalidOperationException("An existing primitive wrapper changed identity during compensation.");
            DisplayStateRequireDatabaseSubtree(NativeRead(() => primitive.DbElement), captured.owner_refno);
            int transparency = NativeRead(() => primitive.Transparency);
            if (transparency < 0 || transparency > 100)
                throw new InvalidOperationException("Native primitive transparency is outside 0..100 during compensation.");
            return new DrawListPrimitiveVisualEntry
            {
                owner_refno = captured.owner_refno, refno = captured.refno,
                colour = NativeRead(() => primitive.Colour), transparency = transparency, edges = NativeRead(() => primitive.Edges),
                visibility = NativeRead(() => primitive.Visibility), shallow = NativeRead(() => primitive.ShallowRendering)
            };
        }).ToArray();
    }

    private static void PrimitiveVisualRollback(PrimitiveVisualState before, Dictionary<string, object> result)
    {
        ThrowIfNativeFaulted();
        result["rollback_attempted"] = true;
        var errors = new Dictionary<string, string>(StringComparer.Ordinal);
        try
        {
            PrimitiveVisualRequireContext(before);
            ApplyDrawListPrimitiveSnapshot(before.DrawList, before.Map.Entries, false, before.Map.Members);
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["primitive_compensation"] = error.Message; }
        try
        {
            PrimitiveVisualRequireContext(before);
            PrimitiveVisualRefresh();
            result["rollback_graphics_updated"] = true;
        }
        catch (Exception error) { ThrowIfNativeFaulted(); result["rollback_graphics_updated"] = false; errors["graphics"] = error.Message; }
        bool readable = false, restored = false;
        try
        {
            PrimitiveVisualRequireContext(before);
            // Different values on fully readable aliases are a known residual,
            // not an unreadable outcome. Successful reads/previews still reject
            // that state through the strict default capture path.
            PrimitiveVisualState after = PrimitiveVisualReadState(false);
            PrimitiveVisualRequireComplete(after.Map);
            Dictionary<string, string> memberDifferences = CompareDrawListVisualSnapshots(before.Map.Members, after.Map.Members);
            Dictionary<string, string> primitiveDifferences = CompareDrawListPrimitiveSnapshots(before.Map.Entries, after.Map.Entries);
            result["actual_residual"] = memberDifferences;
            result["actual_primitive_residual"] = primitiveDifferences;
            result["observed_fingerprint_after"] = after.Fingerprint;
            readable = true;
            restored = memberDifferences.Count == 0 && primitiveDifferences.Count == 0;
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["verification"] = error.Message; }
        ModelCaptureCeAfter(result);
        bool ceVerified = Equals(result["ce_unchanged"], true);
        if (!ceVerified) errors["current_element"] = "Current element could not be verified unchanged; it was not reassigned.";
        result["rollback_errors"] = errors;
        result["rollback_verified_original"] = readable ? (object)restored : null;
        result["rollback_succeeded"] = readable && restored && ceVerified && errors.Count == 0;
        result["partial_write"] = readable ? (object)!restored : null;
        result["outcome_uncertain"] = !readable || result["ce_unchanged"] == null;
        result["write_applied"] = readable && restored ? (object)false : null;
    }
}
