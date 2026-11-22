using System;
using System.Collections.Generic;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core3D.Graphics;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private const int DisplayStateMemberLimit = 10000;
    private const int DisplayStatePrimitiveLimit = 20000;
    private const int DisplayStateOwnerDepthLimit = 1024;

    public object GetDisplayState(string refno)
    {
        string target = ModelEditInput.ExplicitReference(refno, "refno");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
            var errors = new List<string>();
            var matches = new List<object>();
            var result = new Dictionary<string, object>
            {
                ["success"] = false, ["available"] = false, ["complete"] = false,
                ["refno"] = target, ["element"] = null, ["drawlist_id"] = null,
                ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
                ["top_level_element"] = null,
                ["top_level_metadata_source"] = "DbElement.GetActualType().GetBool(DbNounField.TOPF)",
                ["exact_member"] = null, ["member_count"] = null, ["member_scan_complete"] = false,
                ["sdk_exists"] = null,
                ["resolved_member"] = null, ["resolved_member_matches_target"] = null,
                ["resolved_member_is_listed"] = null,
                ["resolved_member_relationship_verified"] = null,
                ["primitive_match_count"] = 0, ["primitive_matches"] = matches,
                ["primitive_count"] = null, ["primitive_scan_complete"] = false,
                ["primitive_match_count_exact"] = false,
                ["primitive_snapshot_hash"] = null,
                ["primitive_snapshot_hash_scope"] = "Observation-only hash of the complete SDK-resolved member primitive entries, sorted by numeric reference text, including owner/ref and five visual values; not a write revision.",
                ["primitive_scan_scope"] = "Existing primitives enumerated by SDK resolved DrawListMember.Primitives; no entries are created.",
                ["primitive_owner_refno_scope"] = "The SDK-resolved top-level member whose Primitives collection supplied the entry; not necessarily its immediate database owner.",
                ["pixel_visibility_verified"] = false, ["read_errors"] = errors
            };
            try
            {
                try { result["ce_before"] = ModelCurrentReference(); }
                catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("ce_before: " + ex.Message); }
                DbElement element = ModelRequireElement(target);
                string targetReference = ModelReference(element.Ref);
                result["refno"] = targetReference;
                try { result["element"] = ModelIdentity(element); }
                catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("element: " + ex.Message); }
                try { result["top_level_element"] = DisplayStateTopLevelElement(element); }
                catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("top_level_element: " + ex.Message); }

                // Access the public manager directly so getter failures remain observable.
                DrawListManager manager = DrawListManager.Instance;
                if (manager == null) throw new InvalidOperationException("No Drawlist manager is available.");
                DrawList drawList = manager.CurrentDrawList;
                if (drawList == null) throw new InvalidOperationException("No current Drawlist is available.");
                result["available"] = true;
                try { result["drawlist_id"] = drawList.ID; }
                catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("drawlist_id: " + ex.Message); }
                try { result["sdk_exists"] = drawList.Exists(element); }
                catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("sdk_exists: " + ex.Message); }

                var listed = new HashSet<string>(StringComparer.Ordinal);
                bool memberScanComplete = false;
                try
                {
                    DrawListMember[] members = drawList.Members();
                    if (members == null) throw new InvalidOperationException("SDK returned no member collection.");
                    result["member_count"] = members.Length;
                    if (members.Length > DisplayStateMemberLimit)
                        errors.Add("member_scan: SDK member count exceeds the 10000-member verification limit.");
                    bool valid = members.Length <= DisplayStateMemberLimit;
                    for (int index = 0; index < Math.Min(members.Length, DisplayStateMemberLimit); index++)
                    {
                        ThrowIfNativeFaulted();
                        try
                        {
                            DrawListMember member = members[index];
                            if (member == null) throw new InvalidOperationException("SDK returned a null member.");
                            DbElement memberElement = member.DbElement;
                            if (!IsUsable(memberElement)) throw new InvalidOperationException("Member database element is not usable.");
                            string reference = ModelReference(memberElement.Ref);
                            if (!listed.Add(reference)) throw new InvalidOperationException("Repeated exact member reference: " + reference);
                        }
                        catch (Exception ex)
                        {
                            ThrowIfNativeFaulted();
                            valid = false;
                            errors.Add("member[" + index + "]: " + ex.Message);
                        }
                    }
                    memberScanComplete = valid;
                    result["member_scan_complete"] = valid;
                    // Presence is observed even with a partial scan; absence requires completeness.
                    result["exact_member"] = listed.Contains(targetReference) ? (object)true : valid ? (object)false : null;
                }
                catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("member_scan: " + ex.Message); }

                try
                {
                    DrawListMember resolved = drawList.GetDrawListMember(element);
                    if (resolved == null)
                    {
                        result["resolved_member_matches_target"] = false;
                        result["resolved_member_is_listed"] = false;
                        result["primitive_count"] = 0;
                        result["primitive_scan_complete"] = true;
                        result["primitive_match_count_exact"] = true;
                        result["primitive_scan_scope"] = "No SDK-resolved DrawListMember; no primitive collection exists to inspect through this resolution.";
                        if (Equals(result["exact_member"], true))
                            errors.Add("resolved_member: SDK returned null for a target independently observed as an exact top-level member.");
                    }
                    else
                    {
                        DbElement owner = resolved.DbElement;
                        if (!IsUsable(owner)) throw new InvalidOperationException("SDK resolved member has an unusable database element.");
                        string ownerReference = ModelReference(owner.Ref);
                        result["resolved_member_matches_target"] = ownerReference == targetReference;
                        result["resolved_member_is_listed"] = listed.Contains(ownerReference) ? (object)true : memberScanComplete ? (object)false : null;
                        if (memberScanComplete && !listed.Contains(ownerReference))
                            errors.Add("resolved_member: SDK returned a member absent from the independently enumerated member list.");
                        var ownerState = new Dictionary<string, object>
                        {
                            ["refno"] = ownerReference, ["type"] = null, ["name"] = null,
                            ["colour"] = null, ["transparency"] = null, ["edges"] = null,
                            ["visibility"] = null, ["shallow"] = null
                        };
                        result["resolved_member"] = ownerState;
                        try
                        {
                            DisplayStateRequireDatabaseSubtree(element, ownerReference);
                            result["resolved_member_relationship_verified"] = true;
                        }
                        catch (Exception ex)
                        {
                            ThrowIfNativeFaulted();
                            result["resolved_member_relationship_verified"] = false;
                            errors.Add("resolved_member.relationship: " + ex.Message);
                        }
                        try
                        {
                            Dictionary<string, object> identity = ModelIdentity(owner);
                            ownerState["type"] = identity["type"]; ownerState["name"] = identity["name"];
                        }
                        catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("resolved_member.identity: " + ex.Message); }
                        DisplayStateReadVisual(ownerState, errors, "resolved_member", () => resolved.Colour,
                            () => resolved.Transparency, () => resolved.Edges, () => resolved.Visibility, () => resolved.ShallowRendering);
                        DisplayStateReadPrimitives(resolved, ownerReference, targetReference, result, matches, errors);
                    }
                }
                catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("resolved_member: " + ex.Message); }
            }
            catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("display_state: " + ex.Message); }
            try
            {
                result["ce_after"] = ModelCurrentReference();
                ModelCeUnchanged(result);
                if (!Equals(result["ce_unchanged"], true)) errors.Add("Current element could not be verified unchanged during the display-state read.");
            }
            catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("ce_after: " + ex.Message); }
            bool complete = Equals(result["available"], true) && Equals(result["member_scan_complete"], true) &&
                Equals(result["primitive_scan_complete"], true) && errors.Count == 0;
            result["complete"] = complete;
            result["success"] = complete;
            if (!complete) result["primitive_snapshot_hash"] = null;
            return result;
        });
    }

    private static void DisplayStateReadPrimitives(DrawListMember member, string ownerReference, string targetReference,
        Dictionary<string, object> result, List<object> matches, List<string> errors)
    {
        ThrowIfNativeFaulted();
        // The public getter enumerates native primitive pointers. Its wrapper factory
        // only reads getElement/getSglId; it does not use the mutating DB_Element ctor.
        DrawListPrimitive[] primitives = member.Primitives;
        if (primitives == null) throw new InvalidOperationException("SDK returned no primitive collection.");
        result["primitive_count"] = primitives.Length;
        bool complete = primitives.Length <= DisplayStatePrimitiveLimit;
        if (!complete) errors.Add("primitive_scan: SDK primitive count exceeds the 20000-entry verification limit.");
        var references = new HashSet<string>(StringComparer.Ordinal);
        var entries = new List<Dictionary<string, object>>();
        for (int index = 0; index < Math.Min(primitives.Length, DisplayStatePrimitiveLimit); index++)
        {
            ThrowIfNativeFaulted();
            try
            {
                DrawListPrimitive primitive = primitives[index];
                if (primitive == null) throw new InvalidOperationException("SDK returned a null primitive.");
                DbElement element = primitive.DbElement;
                if (!IsUsable(element)) throw new InvalidOperationException("Primitive database element is not usable.");
                string reference = ModelReference(element.Ref);
                if (!references.Add(reference))
                {
                    complete = false;
                    errors.Add("primitive[" + index + "]: Duplicate native primitive reference " + reference + ".");
                }
                // Read every entry's properties to surface incomplete enumeration,
                // even though this target-specific endpoint emits only exact matches.
                var visual = new Dictionary<string, object>
                {
                    ["refno"] = reference, ["owner_refno"] = ownerReference,
                    ["database_subtree_verified"] = false,
                    ["colour"] = null, ["transparency"] = null, ["edges"] = null,
                    ["visibility"] = null, ["shallow"] = null
                };
                int errorsBefore = errors.Count;
                try
                {
                    DisplayStateRequireDatabaseSubtree(element, ownerReference);
                    visual["database_subtree_verified"] = true;
                }
                catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("primitive[" + index + "].relationship: " + ex.Message); }
                DisplayStateReadVisual(visual, errors, "primitive[" + index + "]", () => primitive.Colour,
                    () => primitive.Transparency, () => primitive.Edges, () => primitive.Visibility, () => primitive.ShallowRendering);
                if (errors.Count != errorsBefore) complete = false;
                entries.Add(visual);
                if (reference == targetReference) matches.Add(visual);
            }
            catch (Exception ex)
            {
                ThrowIfNativeFaulted();
                complete = false;
                errors.Add("primitive[" + index + "]: " + ex.Message);
            }
        }
        result["primitive_match_count"] = matches.Count;
        result["primitive_scan_complete"] = complete;
        result["primitive_match_count_exact"] = complete;
        if (complete)
        {
            result["primitive_snapshot_hash"] = PrimitiveHash(writer =>
            {
                writer.Write("display-primitive-observation-v1");
                writer.Write(ownerReference);
                writer.Write(entries.Count);
                foreach (Dictionary<string, object> entry in entries.OrderBy(item => (string)item["refno"], StringComparer.Ordinal))
                {
                    ThrowIfNativeFaulted();
                    writer.Write((string)entry["owner_refno"]);
                    writer.Write((string)entry["refno"]);
                    writer.Write((int)entry["colour"]);
                    writer.Write((int)entry["transparency"]);
                    writer.Write((bool)entry["edges"]);
                    writer.Write((bool)entry["visibility"]);
                    writer.Write((bool)entry["shallow"]);
                }
            });
        }
    }

    private static bool DisplayStateTopLevelElement(DbElement element)
    {
        ThrowIfNativeFaulted();
        if (!IsUsable(element)) throw new InvalidOperationException("A usable element is required for native TOPF metadata.");
        DbElementType type = element.GetActualType();
        if (type == null || !type.IsValid) throw new InvalidOperationException("The SDK actual element type is unavailable for TOPF metadata.");
        return type.GetBool(DbNounField.TOPF);
    }

    private static void DisplayStateRequireDatabaseSubtree(DbElement element, string ancestorReference)
    {
        ThrowIfNativeFaulted();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        DbElement current = element;
        for (int depth = 0; depth < DisplayStateOwnerDepthLimit; depth++)
        {
            ThrowIfNativeFaulted();
            if (!IsUsable(current))
                throw new InvalidOperationException("An Owner-chain element is unreadable before reaching the resolved display member.");
            int[] numericReference = NativeRead(() => current.Ref);
            string reference = ModelReference(numericReference);
            if (!seen.Add(reference))
                throw new InvalidOperationException("A cycle was found in the database Owner chain at " + reference + ".");
            if (reference == ancestorReference) return;
            // WORLD has no database owner to traverse. A /0 endpoint can only
            // end a failed proof here; it never grants an unrelated ancestry.
            bool isWorld = numericReference[1] == 0;
            if (!isWorld)
            {
                var currentType = NativeRead(() => current.GetElementType());
                if (currentType == null) throw new InvalidOperationException("An Owner-chain element's type is unavailable.");
                isWorld = NativeRead(() => currentType.IsWorld);
            }
            if (isWorld)
                throw new InvalidOperationException("The database Owner chain reached WORLD without the resolved display member " + ancestorReference + ".");
            current = NativeRead(() => current.Owner);
        }
        throw new InvalidOperationException("The database Owner chain exceeds the 1024-element verification limit.");
    }

    private static void DisplayStateReadVisual(Dictionary<string, object> result, List<string> errors, string label,
        Func<int> colour, Func<int> transparency, Func<bool> edges, Func<bool> visibility, Func<bool> shallow)
    {
        ThrowIfNativeFaulted();
        DisplayStateReadField(result, errors, label, "colour", () => colour());
        DisplayStateReadField(result, errors, label, "transparency", () =>
        {
            int value = transparency();
            if (value < 0 || value > 100) throw new InvalidOperationException("SDK transparency is outside 0..100.");
            return value;
        });
        DisplayStateReadField(result, errors, label, "edges", () => edges());
        DisplayStateReadField(result, errors, label, "visibility", () => visibility());
        DisplayStateReadField(result, errors, label, "shallow", () => shallow());
    }

    private static void DisplayStateReadField(Dictionary<string, object> result, List<string> errors, string label,
        string field, Func<object> read)
    {
        ThrowIfNativeFaulted();
        try { result[field] = NativeRead(read); }
        catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add(label + "." + field + ": " + ex.Message); }
    }
}
