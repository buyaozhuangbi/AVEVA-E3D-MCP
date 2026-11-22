using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core.Geometry;
using Aveva.Core.Utilities.Messaging;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private const int CopyNodeLimit = 2048;
    private const int CopyDepthLimit = 32;
    private const int CopyProtectionLimit = 10000;
    private readonly string copyRevisionSession = Guid.NewGuid().ToString("N");

    private sealed class CopyNode
    {
        internal DbElement Element;
        internal CopyTreeIdentity Identity;
        internal Position NativePosition, WorldPosition, RootPosition;
        internal Orientation NativeOrientation, WorldOrientation, RootOrientation;
        internal readonly SortedDictionary<string, double> Dimensions = new SortedDictionary<string, double>(StringComparer.Ordinal);
        internal readonly SortedDictionary<string, string> Values = new SortedDictionary<string, string>(StringComparer.Ordinal);
        internal readonly SortedDictionary<string, string> Attributes = new SortedDictionary<string, string>(StringComparer.Ordinal);
    }

    private sealed class CopyState
    {
        internal DbElement Source, Parent, Frame, Storage;
        internal List<CopyNode> Nodes;
        internal string SourceRevision, ParentRevision, Revision;
        internal string ParentReference;
        internal string[] ParentMembers, Names;
        internal HashSet<string> Protected;
        internal ModelEditInput Effective, Native;
        internal bool EmptyRootVerified;
        internal CopyTreeCapture AcceptedCopy;
    }

    public object CopyElement(string sourceRef, string parentRef, string name, double[] position,
        double[] xAxis, double[] yAxis, string wrt, string expectedRevision, bool dryRun)
    {
        string sourceTarget = ModelEditInput.ExplicitReference(sourceRef, "source_refno");
        string parentTarget = ModelEditInput.ExplicitReference(parentRef, "parent_refno");
        string requestedName = CopyTreePolicy.RequestedName(name);
        ModelEditInput input = new ModelEditInput(position, xAxis, yAxis, wrt, false);
        string expected = expectedRevision == null ? null : expectedRevision.ToLowerInvariant();
        if (expected != null && (expected.Length != 64 || expected.Any(c => !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f'))))
            throw new ArgumentException("expected_revision must contain exactly 64 hexadecimal characters.", "expectedRevision");
        if (!dryRun && expected == null)
            throw new ArgumentException("Execution requires expected_revision from a current copy preview.", "expectedRevision");
        string command = "database-copy " + sourceTarget + " " + parentTarget;
        if (dryRun ? IsCommandActive() : !TryBeginCommand(command)) return BusyResult(command);
        try
        {
            return OnUi(delegate
            {
                var result = ModelEditResult(dryRun);
                result["creation_attempted"] = false;
                result["creation_result_unknown"] = false;
                result["copy_attempted"] = false;
                result["native_copy_returned"] = false;
                result["native_copy_result_captured"] = false;
                result["captured_native_copy_refnos"] = null;
                result["empty_root_verified_before_copy"] = false;
                result["write_applied"] = false;
                result["outcome_uncertain"] = false;
                result["retry_safe"] = true;
                result["cleanup_attempted"] = false;
                result["cleanup_succeeded"] = null;
                result["residual_refno"] = null;
                result["source_unchanged"] = null;
                result["validation_scope"] = "Unlinked, rule-free native SUBE geometry trees containing CYLI/BOX/CONE with leaf NCYL/NBOX/NCON cuts, standard positive DISH leaves, and simple PANE/PLOO/PAVE panels. DISH includes DIAM/HEIG/RADI and cannot contain child members. All ordered identities, names, supported geometry, native/local/WORLD transforms and source persistent attribute reads are guarded. UDETs, equipment roots, NDIS, external references, unsupported members and arbitrary rules are rejected. Native creation/copy/setters are not a database transaction.";
                result["name_policy"] = "Explicit root name; every originally named descendant receives root-name-C plus its four-digit depth-first index; unnamed descendants remain unnamed.";
                result["copy_identity_scope"] = "The ordered identity/type/owner/path list is captured only after native CopyHierarchy returns, source verification and complete returned-tree/parent-append validation. It identifies the accepted native copy result; absence from the protected baseline does not independently prove node creation provenance. Before capture, only the verified still-empty new root is eligible for failure cleanup.";
                CopyState state = null;
                DbElement created = null;
                int[] createdReference = null;
                bool createAttempted = false;
                try
                {
                    result["ce_before"] = ModelCurrentReference();
                    state = CopyReadState(sourceTarget, parentTarget, requestedName, input);
                    CopyPopulatePreview(result, state, input, requestedName);
                    if (expected != null && expected != state.Revision)
                    {
                        result["revision_conflict"] = true;
                        throw new InvalidOperationException("expected_revision does not match the current source, destination, frames, names or copy request. Preview again before copying.");
                    }
                    if (dryRun)
                    {
                        result["ce_after"] = ModelCurrentReference();
                        ModelCeUnchanged(result);
                        if (!Equals(result["ce_unchanged"], true)) throw new InvalidOperationException("Current element changed during copy preview.");
                        result["success"] = true;
                        return result;
                    }

                    CopyState immediate = CopyReadState(sourceTarget, parentTarget, requestedName, input);
                    if (immediate.Revision != expected || immediate.Revision != state.Revision)
                    {
                        result["revision_conflict"] = true;
                        throw new InvalidOperationException("Copy state changed during preflight; no creation was attempted.");
                    }
                    state = immediate;
                    // Construct native values before any creation; WRT-qualified setters do not convert in E3D 2.1.
                    Position wantedPosition = ModelPosition(state.Native.Position);
                    Orientation wantedOrientation = ModelOrientation(state.Native);
                    createAttempted = true;
                    result["creation_attempted"] = true;
                    result["retry_safe"] = false;
                    DbElement returned = NativeRead(() => state.Parent.CreateLast(NativeRead(() => state.Source.GetActualType())));
                    if (!IsUsable(returned)) throw new InvalidOperationException("CreateLast did not return an identifiable valid copy root.");
                    CopyTreePolicy.RequireNewRoot(CopyIdentity(returned, ""), state.ParentReference,
                        state.Nodes[0].Identity.Type, state.Protected);
                    createdReference = (int[])returned.Ref.Clone();
                    created = returned;
                    result["refno"] = ModelReference(createdReference);
                    result["residual_refno"] = ModelReference(createdReference);
                    CopyTreePolicy.RequireAppend(state.ParentMembers, ModelMemberReferences(state.Parent), ModelReference(createdReference));
                    if (CopyChildren(created, CopyNodeLimit).Count != 0)
                        throw new InvalidOperationException("The newly created copy root unexpectedly already has members.");
                    state.EmptyRootVerified = true;
                    result["empty_root_verified_before_copy"] = true;
                    if (!created.IsCopyable(state.Source)) throw new InvalidOperationException("SDK IsCopyable rejected the new root and source types/state.");
                    result["copy_attempted"] = true;
                    ThrowIfNativeFaulted();
                    created.CopyHierarchy(state.Source, new DbCopyOption
                    {
                        Rename = false,
                        IncludeRules = false,
                        ReExecuteRules = false
                    });
                    ThrowIfNativeFaulted();
                    result["native_copy_returned"] = true;

                    // Prove source names and lookup identities stayed intact before any destination NAME setter.
                    CopyRequireSourceUnchanged(state);
                    result["source_unchanged"] = true;
                    List<CopyNode> copied = CopyReadTree(created);
                    CopyRequireMapping(state, copied);
                    // Lock the complete accepted native result before our NAME/POS/ORI
                    // setters. No partially returned/unknown subtree gets this permit.
                    CopyRequireSourceUnchanged(state);
                    state.AcceptedCopy = CopyTreePolicy.CaptureReturnedTree(
                        state.Nodes.Select(node => node.Identity).ToList(), copied.Select(node => node.Identity).ToList(),
                        ModelReference(createdReference), state.ParentReference, state.Protected,
                        state.ParentMembers, ModelMemberReferences(state.Parent));
                    result["native_copy_result_captured"] = true;
                    result["captured_native_copy_refnos"] = state.AcceptedCopy.References;
                    CopyRequireGeometry(state.Nodes, copied, false);
                    CopyRequireNamesAvailable(state.Names, copied);
                    for (int index = 0; index < copied.Count; index++)
                    {
                        ThrowIfNativeFaulted();
                        string wantedName = state.Names[index];
                        DbElement exact = CopyRequireExactNew(copied[index], state);
                        if (wantedName == null)
                        {
                            if (!string.IsNullOrEmpty(copied[index].Identity.Name))
                                throw new InvalidOperationException("Native copy named an originally unnamed descendant; the unnamed-name contract cannot be verified.");
                            continue;
                        }
                        ModelRequireSettable(exact, DbAttributeInstance.NAME);
                        PdmsMessage message;
                        ThrowIfNativeFaulted();
                        bool renamed = exact.SetAttribute(DbAttributeInstance.NAME, new DbQualifier(), wantedName, out message);
                        ThrowIfNativeFaulted();
                        ModelRequireSetter(renamed, message, "NAME");
                        if (!ModelEditInput.TextMatches(wantedName, exact.GetString(DbAttributeInstance.NAME), true))
                            throw new InvalidOperationException("Copied NAME readback did not match its planned unique name.");
                    }
                    ModelRequireStorageFrame(created, state.Storage, state.Native);
                    ModelWritePosition(created, wantedPosition);
                    ModelWriteOrientation(created, wantedOrientation);

                    List<CopyNode> final = CopyReadTree(created);
                    CopyRequireMapping(state, final);
                    state.AcceptedCopy.RequireUnchanged(final.Select(node => node.Identity).ToList());
                    CopyRequireGeometry(state.Nodes, final, true);
                    CopyRequireFinalNames(state, final);
                    ModelAddTransform(result, created, new DbQualifier { wrtQualifier = state.Frame }, state.Effective);
                    CopyTreePolicy.RequireAppend(state.ParentMembers, ModelMemberReferences(state.Parent), ModelReference(createdReference));
                    CopyRequireSourceUnchanged(state);
                    result["source_unchanged"] = true;
                    result["parent_membership_verified"] = true;
                    result["captured_copy_identity_verified"] = true;
                    // Retain the older receipt field used by existing pump scripts;
                    // its explicitly stated scope is identity acceptance, not provenance.
                    result["all_new_references_verified"] = true;
                    result["all_new_references_verified_scope"] = "Legacy alias for captured_copy_identity_verified. The unchanged accepted native copy identities were verified; this does not independently prove creation provenance.";
                    result["geometry_verified"] = true;
                    result["name_mapping"] = CopyMappingResult(state, final);
                    result["element"] = ModelIdentity(created);
                    result["node_count"] = final.Count;
                    result["ce_after"] = ModelCurrentReference();
                    ModelCeUnchanged(result);
                    if (!Equals(result["ce_unchanged"], true)) throw new InvalidOperationException("Current element changed during copy.");
                    result["write_applied"] = true;
                    result["residual_refno"] = null;
                    result["success"] = true;
                }
                catch (Exception ex)
                {
                    ThrowIfNativeFaulted();
                    result["error"] = ex.Message;
                    result["readback_verified"] = dryRun ? (object)null : false;
                    if (state != null)
                    {
                        try { CopyRequireSourceUnchanged(state); result["source_unchanged"] = true; }
                        catch (Exception sourceError) { ThrowIfNativeFaulted(); result["source_unchanged"] = false; result["source_verification_error"] = sourceError.Message; }
                    }
                    if (created != null)
                        CopyCleanup(result, state, created, createdReference);
                    else if (createAttempted)
                    {
                        result["creation_result_unknown"] = true;
                        result["error"] = ex.Message + " No verified new root was returned; inspect the explicit parent before retrying.";
                    }
                    if (state != null && createAttempted)
                    {
                        try { CopyRequireSourceUnchanged(state); result["source_unchanged"] = true; }
                        catch (Exception sourceError) { ThrowIfNativeFaulted(); result["source_unchanged"] = false; result["source_verification_error"] = sourceError.Message; }
                    }
                    bool restored = !createAttempted || Equals(result["cleanup_succeeded"], true);
                    bool sourceSafe = state == null || Equals(result["source_unchanged"], true);
                    result["write_applied"] = restored ? (object)false : null;
                    result["outcome_uncertain"] = !restored || !sourceSafe;
                    result["retry_safe"] = restored && sourceSafe;
                }
                ModelCaptureCeAfter(result);
                return result;
            });
        }
        finally { if (!dryRun) EndCommand(); }
    }

    private CopyState CopyReadState(string sourceReference, string parentReference, string name, ModelEditInput input)
    {
        ThrowIfNativeFaulted();
        var state = new CopyState { Source = ModelRequireElement(sourceReference), Parent = ModelRequireElement(parentReference) };
        if (state.Source.DbType != DbType.Design || state.Parent.DbType != DbType.Design)
            throw new InvalidOperationException("Source and destination parent must be usable Design elements.");
        state.Nodes = CopyReadTree(state.Source);
        if (!new[] { "SUBE", "CYLI", "BOX", "CONE", "DISH", "PANE" }.Contains(state.Nodes[0].Identity.Type, StringComparer.Ordinal))
            throw new InvalidOperationException("Copy root must be SUBE, CYLI, BOX, CONE, a standard positive DISH without children, or simple PANE.");
        string parentRef = ModelReference(state.Parent.Ref);
        state.ParentReference = parentRef;
        if (state.Nodes.Any(node => node.Identity.Reference == parentRef))
            throw new InvalidOperationException("Destination parent cannot be the source or a source descendant.");
        if (!state.Parent.IsCreatable(state.Source.GetActualType()))
            throw new InvalidOperationException("SDK does not allow the source root type under the destination parent now.");
        state.ParentMembers = ModelMemberReferences(state.Parent);
        state.Frame = ModelFrame(input.Wrt, state.Parent);
        state.Storage = ModelStorageFrame(state.Parent);
        var sourceQualifier = new DbQualifier { wrtQualifier = state.Frame };
        Position sourcePosition = state.Source.GetPosition(DbAttributeInstance.POS, sourceQualifier);
        Orientation sourceOrientation = state.Source.GetOrientation(DbAttributeInstance.ORI, sourceQualifier);
        if (sourcePosition == null || sourceOrientation == null) throw new InvalidOperationException("The source root frame is unavailable.");
        state.Effective = new ModelEditInput(input.Position ?? PrimitivePositionVector(sourcePosition),
            input.XAxis ?? DirectionVector(sourceOrientation.XDir()), input.YAxis ?? DirectionVector(sourceOrientation.YDir()), input.Wrt, true);
        state.Native = ModelStorageTransform(state.Storage, state.Frame).ToNative(state.Effective);
        state.Names = CopyTreePolicy.Names(state.Nodes.Select(node => node.Identity).ToList(), name);
        CopyRequireNamesAvailable(state.Names, null);
        CopyRequireNameResolutions(state.Nodes);
        state.SourceRevision = CopySourceFingerprint(state.Nodes);
        List<CopyTreeIdentity> protectedTree = CopyReadIdentities(state.Parent, CopyProtectionLimit);
        state.Protected = new HashSet<string>(protectedTree.Select(node => node.Reference)
            .Concat(state.Nodes.Select(node => node.Identity.Reference)), StringComparer.Ordinal);
        var signatures = new List<string>();
        CopyAppendAncestors(state.Source.Owner, signatures, state.Protected);
        CopyAppendAncestors(state.Parent, signatures, state.Protected);
        CopyAppendAncestors(state.Frame, signatures, state.Protected);
        state.ParentRevision = PrimitiveHash(writer =>
        {
            writer.Write(parentRef);
            foreach (string member in state.ParentMembers) writer.Write(member);
            writer.Write("members-end");
            foreach (CopyTreeIdentity node in protectedTree)
            {
                ThrowIfNativeFaulted();
                writer.Write(node.Reference); writer.Write(node.Owner); writer.Write(node.Type); writer.Write(node.Path); writer.Write(node.Name ?? "");
            }
            foreach (string signature in signatures) writer.Write(signature);
            writer.Write(ModelReference(state.Frame.Ref)); writer.Write(ModelReference(state.Storage.Ref));
        });
        MDB mdb = CurrentMdb();
        Project project = Project.CurrentProject;
        if (mdb == null || project == null) throw new InvalidOperationException("Project/MDB context is unavailable.");
        state.Revision = PrimitiveHash(writer =>
        {
            writer.Write("native-geometry-copy-v1"); writer.Write(copyRevisionSession); writer.Write(project.Name); writer.Write(mdb.Name);
            writer.Write(state.SourceRevision); writer.Write(state.ParentRevision); writer.Write(name); writer.Write(input.Wrt);
            CopyWriteVector(writer, input.Position); CopyWriteVector(writer, input.XAxis); CopyWriteVector(writer, input.YAxis);
            CopyWriteVector(writer, state.Native.Position); CopyWriteVector(writer, state.Native.XAxis); CopyWriteVector(writer, state.Native.YAxis);
            foreach (string proposed in state.Names) writer.Write(proposed ?? "");
        });
        return state;
    }

    private List<CopyNode> CopyReadTree(DbElement root)
    {
        ThrowIfNativeFaulted();
        var nodes = new List<CopyNode>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        CopyReadNode(root, root, "", null, 0, nodes, seen);
        return nodes;
    }

    private void CopyReadNode(DbElement element, DbElement root, string path, string expectedOwner, int depth,
        List<CopyNode> nodes, HashSet<string> seen)
    {
        ThrowIfNativeFaulted();
        if (depth > CopyDepthLimit || nodes.Count >= CopyNodeLimit)
            throw new InvalidOperationException("Copy geometry exceeds the 2048-node or depth-32 verification limit.");
        CopyTreeIdentity identity = CopyIdentity(element, path);
        if (!seen.Add(identity.Reference) || expectedOwner != null && identity.Owner != expectedOwner)
            throw new InvalidOperationException("Source/copy membership is repeated or has an unexpected owner.");
        string type = identity.Type;
        if (NativeRead(() => NativeRead(() => element.GetActualType()) != NativeRead(() => element.GetElementType())))
            throw new InvalidOperationException("User-defined element types are outside guarded geometry copy: " + identity.Reference);
        if (!new[] { "SUBE", "CYLI", "BOX", "CONE", "DISH", "NCYL", "NBOX", "NCON", "PANE", "PLOO", "PAVE" }.Contains(type, StringComparer.Ordinal))
            throw new InvalidOperationException("Unsupported geometry copy member type " + type + " at " + identity.Reference);
        var node = new CopyNode { Element = element, Identity = identity };
        CopyReadGuards(element, node.Attributes);
        var world = new DbQualifier { wrtQualifier = ModelFrame("WORLD", element.Owner) };
        var relative = new DbQualifier { wrtQualifier = root };
        if (element.IsAttributeValid(DbAttributeInstance.POS))
        {
            node.NativePosition = element.GetPosition(DbAttributeInstance.POS);
            node.WorldPosition = element.GetPosition(DbAttributeInstance.POS, world);
            node.RootPosition = element.GetPosition(DbAttributeInstance.POS, relative);
            if (node.NativePosition == null || node.WorldPosition == null || node.RootPosition == null)
                throw new InvalidOperationException("A complete native/WORLD/root-relative position is unavailable: " + identity.Reference);
            PrimitivePositionVector(node.NativePosition); PrimitivePositionVector(node.WorldPosition); PrimitivePositionVector(node.RootPosition);
        }
        if (element.IsAttributeValid(DbAttributeInstance.ORI))
        {
            node.NativeOrientation = element.GetOrientation(DbAttributeInstance.ORI);
            node.WorldOrientation = element.GetOrientation(DbAttributeInstance.ORI, world);
            node.RootOrientation = element.GetOrientation(DbAttributeInstance.ORI, relative);
            if (node.NativeOrientation == null || node.WorldOrientation == null || node.RootOrientation == null)
                throw new InvalidOperationException("A complete native/WORLD/root-relative orientation is unavailable: " + identity.Reference);
            PrimitiveOrientationToken(node.NativeOrientation); PrimitiveOrientationToken(node.WorldOrientation); PrimitiveOrientationToken(node.RootOrientation);
        }
        string shape = PrimitiveShape(type);
        if (shape != null)
        {
            foreach (KeyValuePair<string, string> field in PrimitiveShapeFields(shape))
                node.Dimensions[field.Key] = CopyReadDistance(element, NativeRead(() => DbAttribute.GetDbAttribute(field.Value)));
            PrimitiveValidateDimensions(shape, node.Dimensions);
            if (PrimitiveIsNegative(type))
            {
                DbAttribute napp = DbAttribute.GetDbAttribute("NAPP");
                if (napp == null || !napp.IsValid) throw new InvalidOperationException("SDK NAPP metadata is unavailable.");
                if (element.IsAttributeValid(napp))
                {
                    int application = element.GetInteger(napp);
                    if (application != -1 && application != 2) throw new InvalidOperationException("Only default/owner-only negative scopes can be copied.");
                    node.Values["NAPP"] = application.ToString(CultureInfo.InvariantCulture);
                }
            }
        }
        if (type == "PANE")
        {
            PanelEditState panel = PanelReadEditState(element);
            if (!panel.Eligible) throw new InvalidOperationException("Only simple unlinked panels can be copied: " + string.Join(" ", panel.Reasons));
            PanelPolygonValidation polygon = PanelPolygon.Validate(panel.Vertices.Select(vertex => new[] { vertex.LocalPosition[0], vertex.LocalPosition[1] }).ToArray(), true, PanelPolygon.CreateVertexLimit);
            if (!polygon.valid) throw new InvalidOperationException("The panel outline is not a supported simple counterclockwise polygon.");
        }
        if (type == "PLOO")
        {
            node.Dimensions["HEIG"] = CopyReadDistance(element, DbAttributeInstance.HEIG);
            node.Values["SJUS"] = element.GetAsString(DbAttributeInstance.SJUS).Trim();
            node.Values["LBLIND"] = element.GetBool(DbAttributeInstance.LBLIND).ToString();
        }
        if (type == "PAVE")
        {
            node.Dimensions["FRAD"] = CopyReadDistance(element, DbAttributeInstance.FRAD);
            node.Dimensions["BANG"] = ModelFinite(element.GetDouble(DbAttributeInstance.BANG));
            node.Values["CTYA"] = element.GetAsString(DbAttributeInstance.CTYA).Trim();
            node.Values["GTYP"] = element.GetAsString(DbAttributeInstance.GTYP).Trim();
            DbElement[] links = element.GetElementArray(DbAttributeInstance.LNFA);
            if (links == null || links.Any(link => link != null && !link.IsNull))
                throw new InvalidOperationException("Linked or unreadable PAVE LNFA references cannot be copied.");
        }
        nodes.Add(node);
        List<DbElement> children = CopyChildren(element, CopyNodeLimit);
        if (type == "DISH" && children.Count != 0)
            throw new InvalidOperationException("Only standard positive DISH primitives without child members can be copied.");
        if ((PrimitiveIsNegative(type) || type == "PAVE") && children.Count != 0)
            throw new InvalidOperationException("Negative primitives and PAVE vertices must be leaves for copy.");
        if (type == "PANE" && (children.Count != 1 || children[0].GetElementType().ShortName != "PLOO"))
            throw new InvalidOperationException("Only single-loop panels can be copied.");
        if (type == "PLOO" && (children.Count < 3 || children.Count > PanelPolygon.CreateVertexLimit))
            throw new InvalidOperationException("A copied panel loop must contain 3 to 256 vertices.");
        for (int index = 0; index < children.Count; index++)
        {
            ThrowIfNativeFaulted();
            string childType = children[index].GetElementType().ShortName;
            bool allowed = type == "SUBE" ? new[] { "SUBE", "CYLI", "BOX", "CONE", "DISH", "PANE" }.Contains(childType, StringComparer.Ordinal) :
                type == "PANE" ? childType == "PLOO" : type == "PLOO" ? childType == "PAVE" :
                shape != null && !PrimitiveIsNegative(type) && PrimitiveIsNegative(childType);
            if (!allowed) throw new InvalidOperationException("Unsupported copy owner/child geometry relationship: " + type + " / " + childType);
            string childPath = path.Length == 0 ? index.ToString(CultureInfo.InvariantCulture) : path + "/" + index.ToString(CultureInfo.InvariantCulture);
            CopyReadNode(children[index], root, childPath, identity.Reference, depth + 1, nodes, seen);
        }
    }

    private static double CopyReadDistance(DbElement element, DbAttribute attribute)
    {
        ThrowIfNativeFaulted();
        if (attribute == null || !NativeRead(() => attribute.IsValid) || !NativeRead(() => element.IsAttributeValid(attribute)))
            throw new InvalidOperationException("A required geometry attribute is unavailable.");
        DbDouble value = NativeRead(() => element.GetDbDouble(attribute));
        if (value == null || NativeRead(() => value.Dimension) == null || !NativeRead(() => value.Dimension).Set ||
            NativeRead(() => NativeRead(() => value.Dimension).StandardDimension) != DbDimension.DIST)
            throw new InvalidOperationException("Geometry distance did not return a dimensioned DIST value: " + NativeRead(() => attribute.ShortName));
        DbDoubleUnits units = NativeRead(() => DbDoubleUnits.GetUnits("mm", NativeRead(() => value.Dimension)));
        if (units == null || !units.Set || NativeRead(() => units.Dimension) != NativeRead(() => value.Dimension))
            throw new InvalidOperationException("Millimetre units are unavailable.");
        DbDouble converted = NativeRead(() => value.ConvertUnits(units));
        if (converted == null || NativeRead(() => converted.Dimension) != NativeRead(() => value.Dimension) ||
            NativeRead(() => converted.Units) != units)
            throw new InvalidOperationException("Geometry millimetre conversion could not be verified.");
        return ModelFinite(converted.Value);
    }

    private static CopyTreeIdentity CopyIdentity(DbElement element, string path)
    {
        if (!IsUsable(element) || NativeRead(() => element.DbType) != DbType.Design) throw new InvalidOperationException("A Design copy element is unreadable.");
        string name = NativeRead(() => element.IsAttributeValid(DbAttributeInstance.NAME)) ? NativeRead(() => element.GetString(DbAttributeInstance.NAME)) : "";
        string reference = ModelReference(NativeRead(() => element.Ref));
        // E3D returns the exact numeric REF as the NAME of an unnamed element.
        // Only the element's own canonical REF denotes unnamed state.
        name = CopyTreePolicy.SourceName(name, reference);
        return new CopyTreeIdentity
        {
            Reference = reference, Owner = ModelReference(NativeRead(() => NativeRead(() => element.Owner).Ref)),
            Type = NativeRead(() => NativeRead(() => element.GetActualType()).ShortName), Path = path, Name = name
        };
    }

    private static List<DbElement> CopyChildren(DbElement parent, int limit)
    {
        MemberPage<DbElement> page = MemberPage<DbElement>.Read(PrimitiveDirectMembers(parent), limit, element => ModelReference(NativeRead(() => element.Ref)));
        ThrowIfNativeFaulted();
        if (page.HasMore != false || page.ReadErrors.Count > 0)
            throw new InvalidOperationException("Copy membership could not be read completely: " + string.Join(" ", page.ReadErrors));
        return page.Items;
    }

    private static List<CopyTreeIdentity> CopyReadIdentities(DbElement root, int limit)
    {
        var result = new List<CopyTreeIdentity>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        Action<DbElement, string, string, int> visit = null;
        visit = (element, path, owner, depth) =>
        {
            if (result.Count >= limit || depth > 64) throw new InvalidOperationException("Protected/copy hierarchy exceeds its complete verification limit.");
            CopyTreeIdentity identity = CopyIdentity(element, path);
            if (!seen.Add(identity.Reference) || owner != null && identity.Owner != owner)
                throw new InvalidOperationException("Protected/copy hierarchy contains repeated or foreign membership.");
            result.Add(identity);
            List<DbElement> children = CopyChildren(element, limit);
            for (int index = 0; index < children.Count; index++)
                visit(children[index], path.Length == 0 ? index.ToString(CultureInfo.InvariantCulture) : path + "/" + index.ToString(CultureInfo.InvariantCulture), identity.Reference, depth + 1);
        };
        visit(root, "", null, 0);
        return result;
    }

    private static void CopyReadGuards(DbElement element, IDictionary<string, string> attributes)
    {
        ThrowIfNativeFaulted();
        var signatures = new List<string>();
        var reasons = new List<string>();
        PrimitiveGuardElement(element, signatures, reasons);
        if (reasons.Count != 0) throw new InvalidOperationException(string.Join(" ", reasons));
        DbAttribute[] rules = NativeRead(() => element.GetAttributesWithRules());
        if (rules == null || rules.Length != 0)
            throw new InvalidOperationException("Any attribute rules or unreadable rule state prevent guarded copy: " + ModelReference(NativeRead(() => element.Ref)));
        DbAttribute[] all = NativeRead(() => element.GetAttributes());
        if (all == null) throw new InvalidOperationException("Persistent attribute/reference metadata is unavailable.");
        foreach (DbAttribute attribute in all.OrderBy(item => NativeRead(() => item.ShortName), StringComparer.Ordinal))
        {
            ThrowIfNativeFaulted();
            if (attribute == null || !NativeRead(() => attribute.IsValid)) throw new InvalidOperationException("Persistent attribute metadata is incomplete.");
            if (NativeRead(() => attribute.Type) == DbAttributeType.ELEMENT)
            {
                DbElement[] references = NativeRead(() => attribute.IsArray) ? NativeRead(() => element.GetElementArray(attribute)) : new[] { NativeRead(() => element.GetElement(attribute)) };
                if (references == null || references.Any(reference => reference == null))
                    throw new InvalidOperationException("A persistent reference cannot be read reliably: " + NativeRead(() => attribute.ShortName));
                // OWNER is structural only when it agrees with the independently read direct owner.
                if (NativeRead(() => attribute.ShortName) == "OWNER" && !NativeRead(() => attribute.IsArray))
                {
                    if (NativeRead(() => references[0].IsNull) || ModelReference(NativeRead(() => references[0].Ref)) != ModelReference(NativeRead(() => NativeRead(() => element.Owner).Ref)))
                        throw new InvalidOperationException("Structural OWNER does not match direct membership.");
                }
                else if ((NativeRead(() => attribute.ShortName) == "REF" || NativeRead(() => attribute.ShortName) == "REFNO") && !NativeRead(() => attribute.IsArray))
                {
                    if (NativeRead(() => references[0].IsNull) || ModelReference(NativeRead(() => references[0].Ref)) != ModelReference(NativeRead(() => element.Ref)))
                        throw new InvalidOperationException("Structural REF does not match the captured element identity.");
                }
                else if (references.Any(reference => !NativeRead(() => reference.IsNull)))
                    throw new InvalidOperationException("Nonempty persistent reference " + NativeRead(() => attribute.ShortName) + " is outside guarded geometry copy.");
            }
            // E3D exposes stored geometry and display state through pseudo accessors.
            // Reference guards above therefore run for pseudo attributes as well.
            if (NativeRead(() => attribute.IsPseudo) && NativeRead(() => attribute.Type) != DbAttributeType.ELEMENT &&
                !new[] { "LEVE", "LOCK", "OBST" }.Contains(NativeRead(() => attribute.ShortName), StringComparer.Ordinal)) continue;
            if (NativeRead(() => attribute.ShortName) == "LOCK" && NativeRead(() => element.GetBool(attribute)))
                throw new InvalidOperationException("Locked geometry is outside guarded copy because its copied setters/cleanup cannot be guaranteed.");
            attributes[NativeRead(() => attribute.ShortName)] = CopyAttributeToken(element, attribute);
        }
        foreach (string signature in signatures) attributes["guard:" + signature] = "";
    }

    private static string CopyAttributeToken(DbElement element, DbAttribute attribute)
    {
        ThrowIfNativeFaulted();
        return PrimitiveHash(writer =>
        {
            writer.Write((int)NativeRead(() => attribute.Type)); writer.Write(NativeRead(() => attribute.IsArray));
            switch (NativeRead(() => attribute.Type))
            {
                case DbAttributeType.DOUBLE:
                    DbDouble[] numbers = NativeRead(() => attribute.IsArray) ? NativeRead(() => element.GetDbDoubleArray(attribute)) : new[] { NativeRead(() => element.GetDbDouble(attribute)) };
                    if (numbers == null || numbers.Length > 4096 || numbers.Any(value => value == null))
                        throw new InvalidOperationException("A numeric attribute cannot be captured completely: " + NativeRead(() => attribute.ShortName));
                    writer.Write(numbers.Length);
                    foreach (DbDouble value in numbers)
                    {
                        ThrowIfNativeFaulted();
                        writer.Write(ModelFinite(NativeRead(() => value.DatabaseValue)));
                        writer.Write(NativeRead(() => value.Dimension) == null ? "" : NativeRead(() => NativeRead(() => value.Dimension).Name));
                    }
                    break;
                case DbAttributeType.INTEGER:
                    int[] integers = NativeRead(() => attribute.IsArray) ? NativeRead(() => element.GetIntegerArray(attribute)) : new[] { NativeRead(() => element.GetInteger(attribute)) };
                    if (integers == null || integers.Length > 4096) throw new InvalidOperationException("An integer attribute cannot be captured completely.");
                    writer.Write(integers.Length); foreach (int value in integers) writer.Write(value);
                    break;
                case DbAttributeType.BOOL:
                    bool[] flags = NativeRead(() => attribute.IsArray) ? NativeRead(() => element.GetBoolArray(attribute)) : new[] { NativeRead(() => element.GetBool(attribute)) };
                    if (flags == null || flags.Length > 4096) throw new InvalidOperationException("A boolean attribute cannot be captured completely.");
                    writer.Write(flags.Length); foreach (bool value in flags) writer.Write(value);
                    break;
                case DbAttributeType.ELEMENT:
                    DbElement[] references = NativeRead(() => attribute.IsArray) ? NativeRead(() => element.GetElementArray(attribute)) : new[] { NativeRead(() => element.GetElement(attribute)) };
                    if (references == null || references.Length > 4096 || references.Any(value => value == null))
                        throw new InvalidOperationException("A reference attribute cannot be captured completely.");
                    writer.Write(references.Length); foreach (DbElement value in references) writer.Write(NativeRead(() => value.IsNull) ? "" : ModelReference(NativeRead(() => value.Ref)));
                    break;
                case DbAttributeType.STRING:
                    CopyWriteText(writer, NativeRead(() => element.GetString(attribute)));
                    break;
                case DbAttributeType.STRINGARRAY:
                case DbAttributeType.WORD:
                    string[] text = NativeRead(() => attribute.Type) == DbAttributeType.STRINGARRAY || NativeRead(() => attribute.IsArray) ?
                        NativeRead(() => element.GetAsStringArray(attribute)) : new[] { NativeRead(() => element.GetAsString(attribute)) };
                    if (text == null || text.Length > 4096) throw new InvalidOperationException("A text attribute cannot be captured completely.");
                    writer.Write(text.Length); foreach (string value in text) CopyWriteText(writer, value);
                    break;
                case DbAttributeType.POSITION:
                    Position position = NativeRead(() => element.GetPosition(attribute));
                    if (position == null) throw new InvalidOperationException("A persistent position is unreadable.");
                    PrimitiveWritePositionBytes(writer, position);
                    break;
                case DbAttributeType.DIRECTION:
                    Direction direction = NativeRead(() => element.GetDirection(attribute));
                    if (direction == null) throw new InvalidOperationException("A persistent direction is unreadable.");
                    foreach (double component in DirectionVector(direction)) writer.Write(component);
                    break;
                case DbAttributeType.ORIENTATION:
                    Orientation orientation = NativeRead(() => element.GetOrientation(attribute));
                    if (orientation == null) throw new InvalidOperationException("A persistent orientation is unreadable.");
                    PrimitiveWriteOrientationBytes(writer, orientation);
                    break;
                case DbAttributeType.DATETIME:
                    writer.Write(NativeRead(() => element.GetDateTime(attribute)).ToBinary());
                    break;
                default: throw new InvalidOperationException("Unsupported persistent attribute data type: " + NativeRead(() => attribute.ShortName));
            }
        });
    }

    private static void CopyWriteText(BinaryWriter writer, string value)
    {
        if (value == null || value.Length > 65536) throw new InvalidOperationException("A text value cannot be captured completely.");
        writer.Write(value);
    }

    private static void CopyAppendAncestors(DbElement element, IList<string> signatures, ISet<string> protectedReferences)
    {
        var seen = new HashSet<string>(StringComparer.Ordinal);
        DbElement current = element;
        DbElement world = ModelFrame("WORLD", element);
        string worldReference = ModelReference(world.Ref);
        for (int depth = 0; depth < 1024; depth++)
        {
            ThrowIfNativeFaulted();
            if (current == null) throw new InvalidOperationException("A copy frame ancestor is unavailable before reaching the Design WORLD.");
            if (!IsUsable(current) || current.DbType != DbType.Design)
                throw new InvalidOperationException("A copy frame ancestor is invalid or outside Design before reaching WORLD.");
            string reference = ModelReference(current.Ref);
            // MDB's first Design WORLD can be in a different DB from the source.
            // Recognize the SDK-selected, independently validated WORL /0 endpoint
            // directly, without depending only on IsWorld or traversing its Owner.
            if (CopyTreePolicy.IsSdkWorldEndpoint(reference, worldReference))
            {
                if (!IsUsable(world) || world.DbType != DbType.Design || current.GetElementType().ShortName != "WORL")
                    throw new InvalidOperationException("The current SDK Design WORLD endpoint could not be verified while guarding copy frames.");
                protectedReferences.Add(reference);
                signatures.Add(reference + "|SDK-DESIGN-WORLD");
                return;
            }
            if (!seen.Add(reference)) throw new InvalidOperationException("A cycle was found in a copy frame ancestor chain.");
            protectedReferences.Add(reference);
            string type = current.GetActualType().ShortName;
            if (type == "TMPL") throw new InvalidOperationException("Template ancestors cannot participate in guarded copy.");
            signatures.Add(reference + "|" + type);
            if (current.GetElementType().IsWorld) return;
            var guard = new List<string>(); var reasons = new List<string>();
            PrimitiveGuardElement(current, guard, reasons);
            if (reasons.Count != 0) throw new InvalidOperationException(string.Join(" ", reasons));
            foreach (string token in guard) signatures.Add(token);
            DbAttribute[] rules = current.GetAttributesWithRules();
            if (rules == null || rules.Length != 0) throw new InvalidOperationException("Rule-driven or unreadable ancestor state cannot participate in guarded copy.");
            if (current.IsAttributeValid(DbAttributeInstance.POS) && current.IsAttributeValid(DbAttributeInstance.ORI))
                PrimitiveAppendTransform(signatures, reference, current.GetPosition(DbAttributeInstance.POS), current.GetOrientation(DbAttributeInstance.ORI),
                    current.GetPosition(DbAttributeInstance.POS, new DbQualifier { wrtQualifier = world }),
                    current.GetOrientation(DbAttributeInstance.ORI, new DbQualifier { wrtQualifier = world }));
            else if (current.IsAttributeValid(DbAttributeInstance.POS) || current.IsAttributeValid(DbAttributeInstance.ORI))
                throw new InvalidOperationException("An incomplete ancestor positioning frame cannot participate in copy.");
            current = current.Owner;
        }
        throw new InvalidOperationException("The Design WORLD was not reached while guarding copy frames.");
    }

    private static string CopySourceFingerprint(IList<CopyNode> nodes)
    {
        return PrimitiveHash(writer =>
        {
            writer.Write(nodes.Count);
            foreach (CopyNode node in nodes)
            {
                ThrowIfNativeFaulted();
                writer.Write(node.Identity.Reference); writer.Write(node.Identity.Owner); writer.Write(node.Identity.Type);
                writer.Write(node.Identity.Path); writer.Write(node.Identity.Name);
                CopyWritePosition(writer, node.NativePosition); CopyWritePosition(writer, node.WorldPosition);
                CopyWriteOrientation(writer, node.NativeOrientation); CopyWriteOrientation(writer, node.WorldOrientation);
                foreach (var pair in node.Dimensions) { writer.Write(pair.Key); writer.Write(pair.Value); }
                writer.Write("dimensions-end");
                foreach (var pair in node.Values) { writer.Write(pair.Key); writer.Write(pair.Value); }
                writer.Write("values-end");
                foreach (var pair in node.Attributes) { writer.Write(pair.Key); writer.Write(pair.Value); }
                writer.Write("attributes-end");
            }
        });
    }

    private void CopyRequireSourceUnchanged(CopyState state)
    {
        ThrowIfNativeFaulted();
        List<CopyNode> after = CopyReadTree(ModelRequireElement(state.Nodes[0].Identity.Reference));
        if (CopySourceFingerprint(after) != state.SourceRevision)
            throw new InvalidOperationException("Source subtree names, identities, geometry, frame or persistent attributes changed during copy.");
        CopyRequireNameResolutions(after);
    }

    private static void CopyRequireNameResolutions(IEnumerable<CopyNode> nodes)
    {
        ThrowIfNativeFaulted();
        foreach (CopyNode node in nodes.Where(item => item.Identity.Name.Length != 0))
        {
            ThrowIfNativeFaulted();
            DbElement named = CopyFindName("/" + node.Identity.Name);
            if (!IsUsable(named) || ModelReference(named.Ref) != node.Identity.Reference)
                throw new InvalidOperationException("An original source name no longer resolves to its captured source reference.");
        }
    }

    private static void CopyRequireNamesAvailable(string[] names, IList<CopyNode> copied)
    {
        ThrowIfNativeFaulted();
        for (int index = 0; index < names.Length; index++)
        {
            ThrowIfNativeFaulted();
            if (names[index] == null) continue;
            DbElement existing = CopyFindName(names[index]);
            if (IsUsable(existing) && (copied == null || ModelReference(existing.Ref) != copied[index].Identity.Reference))
                throw new InvalidOperationException("A planned copy name already exists: " + names[index]);
        }
    }

    private static DbElement CopyFindName(string name)
    {
        ThrowIfNativeFaulted();
        MDB mdb = CurrentMdb();
        if (mdb == null) throw new InvalidOperationException("Current MDB is unavailable for copy-name validation.");
        // The public SDK name lookup returns a null-element for an absent name.
        // Do not swallow lookup failures or replace them with a bounded tree search.
        DbElement found = mdb.FindElement(name);
        if (found == null) throw new InvalidOperationException("SDK name lookup returned no reliable element state: " + name);
        if (found.IsNull) return null;
        if (!found.IsValid) throw new InvalidOperationException("SDK name lookup returned an invalid non-null element: " + name);
        return found;
    }

    private static void CopyRequireMapping(CopyState state, IList<CopyNode> copied)
    {
        CopyTreePolicy.RequireMatchingTree(state.Nodes.Select(node => node.Identity).ToList(), copied.Select(node => node.Identity).ToList(),
            ModelReference(state.Parent.Ref), state.Protected);
    }

    private static DbElement CopyRequireExactNew(CopyNode node, CopyState state)
    {
        ThrowIfNativeFaulted();
        DbElement exact = ModelRequireElement(node.Identity.Reference);
        CopyTreeIdentity actual = CopyIdentity(exact, node.Identity.Path);
        if (state.Protected.Contains(actual.Reference) || actual.Reference != node.Identity.Reference ||
            actual.Owner != node.Identity.Owner || actual.Type != node.Identity.Type)
            throw new InvalidOperationException("A copied node identity/owner changed before its setter.");
        return exact;
    }

    private static void CopyRequireGeometry(IList<CopyNode> source, IList<CopyNode> copied, bool positioned)
    {
        CopyNode copiedRoot = copied[0];
        for (int index = 0; index < source.Count; index++)
        {
            ThrowIfNativeFaulted();
            CopyNode before = source[index], after = copied[index];
            if (!before.Dimensions.Keys.SequenceEqual(after.Dimensions.Keys, StringComparer.Ordinal) ||
                before.Dimensions.Any(pair => !PrimitiveNumbersMatch(pair.Value, after.Dimensions[pair.Key])) ||
                !before.Values.SequenceEqual(after.Values))
                throw new InvalidOperationException("Copied supported geometry differs from its source at " + before.Identity.Path);
            // CopyHierarchy is also expected to retain descriptive attributes and UDAs.
            // Only fields whose identity/frame is explicitly verified elsewhere are excluded.
            var structural = new HashSet<string>(new[] { "NAME", "OWNER", "REF", "REFNO", "POS", "ORI" }, StringComparer.Ordinal);
            var sourceAttributes = before.Attributes.Where(pair => !structural.Contains(pair.Key) && !pair.Key.StartsWith("guard:", StringComparison.Ordinal)).ToArray();
            var copiedAttributes = after.Attributes.Where(pair => !structural.Contains(pair.Key) && !pair.Key.StartsWith("guard:", StringComparison.Ordinal)).ToArray();
            if (!sourceAttributes.SequenceEqual(copiedAttributes))
            {
                string field = sourceAttributes.FirstOrDefault(pair => !after.Attributes.ContainsKey(pair.Key) || after.Attributes[pair.Key] != pair.Value).Key ?? "attribute-set";
                throw new InvalidOperationException("A copied persistent attribute differs from its source: " + field + " at " + before.Identity.Path);
            }
            if (index == 0) continue;
            if (before.NativePosition != null && !PrimitivePositionMatches(before.NativePosition, after.NativePosition) ||
                before.NativeOrientation != null && (after.NativeOrientation == null || !ModelOrientationsEqual(before.NativeOrientation, after.NativeOrientation)))
                throw new InvalidOperationException("A copied descendant's native local geometry frame changed.");
            if (before.RootPosition != null && !PrimitivePositionMatches(before.RootPosition, after.RootPosition) ||
                before.RootOrientation != null && (after.RootOrientation == null || !ModelOrientationsEqual(before.RootOrientation, after.RootOrientation)))
                throw new InvalidOperationException("A copied descendant does not preserve its root-relative frame.");
            if (positioned && before.RootPosition != null && !PrimitiveVectorMatches(
                PrimitiveComposePosition(copiedRoot.WorldPosition, copiedRoot.WorldOrientation, before.RootPosition), PrimitivePositionVector(after.WorldPosition), 0.001))
                throw new InvalidOperationException("A copied descendant WORLD position does not follow the positioned root.");
            if (positioned && before.RootOrientation != null && !ModelOrientationsEqual(
                PrimitiveComposeOrientation(copiedRoot.WorldOrientation, before.RootOrientation), after.WorldOrientation))
                throw new InvalidOperationException("A copied descendant WORLD orientation does not follow the positioned root.");
        }
    }

    private static void CopyRequireFinalNames(CopyState state, IList<CopyNode> final)
    {
        for (int index = 0; index < final.Count; index++)
        {
            ThrowIfNativeFaulted();
            string expected = state.Names[index] ?? "";
            if (!ModelEditInput.TextMatches(expected, final[index].Identity.Name, true))
                throw new InvalidOperationException("The final copied name mapping does not match the preview.");
        }
        CopyRequireNameResolutions(final);
    }

    private static object[] CopyMappingResult(CopyState state, IList<CopyNode> copied)
    {
        return state.Nodes.Select((node, index) => (object)new Dictionary<string, object>
        {
            ["source_refno"] = node.Identity.Reference, ["copied_refno"] = copied == null ? null : copied[index].Identity.Reference,
            ["path"] = node.Identity.Path, ["type"] = node.Identity.Type,
            ["source_name"] = node.Identity.Name, ["copied_name"] = state.Names[index]
        }).ToArray();
    }

    private static void CopyPopulatePreview(Dictionary<string, object> result, CopyState state, ModelEditInput input, string requestedName)
    {
        result["source"] = ModelIdentity(state.Source); result["parent"] = ModelIdentity(state.Parent);
        result["source_refno"] = ModelReference(state.Source.Ref); result["parent_refno"] = ModelReference(state.Parent.Ref);
        result["revision"] = state.Revision; result["source_revision"] = state.SourceRevision; result["parent_revision"] = state.ParentRevision;
        result["revision_scope"] = "Bridge session/project/MDB, source ordered subtree identities/names/geometry/persistent attributes, destination ordered membership/protected descendants/ancestor frames, requested WRT and exact requested name/transform. Source content and destination membership are hashed separately.";
        result["node_count"] = state.Nodes.Count; result["name_mapping"] = CopyMappingResult(state, null);
        result["existing_parent_member_count"] = state.ParentMembers.Length;
        result["frame"] = ModelFrameIdentity(state.Frame, input.Wrt);
        result["submitted"] = new Dictionary<string, object>
        {
            ["name"] = requestedName, ["position"] = input.Position, ["orientation"] = ModelSubmittedOrientation(input)
        };
        result["effective_transform"] = new Dictionary<string, object>
        {
            ["position"] = state.Effective.Position, ["orientation"] = ModelSubmittedOrientation(state.Effective),
            ["omitted_components"] = "Preserve source WORLD position/orientation; OWNER means the explicit destination parent."
        };
    }

    private static void CopyCleanup(Dictionary<string, object> result, CopyState state, DbElement created, int[] reference)
    {
        ThrowIfNativeFaulted();
        try
        {
            if (state == null || reference == null || !state.EmptyRootVerified || string.IsNullOrEmpty(state.ParentReference))
                throw new InvalidOperationException("An exact new root was not verified empty before native copying; no deletion was attempted.");
            object sourceUnchanged;
            if (!result.TryGetValue("source_unchanged", out sourceUnchanged) || !Equals(sourceUnchanged, true))
                throw new InvalidOperationException("Source identity/state could not be reverified unchanged; no cleanup deletion was attempted.");
            DbElement exact = ModelRequireElement(ModelReference(reference));
            List<CopyTreeIdentity> current = CopyReadIdentities(exact, CopyProtectionLimit);
            CopyTreePolicy.RequireNewRoot(current[0], state.ParentReference, state.Nodes[0].Identity.Type, state.Protected);
            CopyTreePolicy.RequireNewTree(current, ModelReference(reference), state.ParentReference, state.Protected);
            if (state.AcceptedCopy == null)
                CopyTreePolicy.RequireEmptyRoot(current, ModelReference(reference), state.ParentReference, state.Nodes[0].Identity.Type, state.Protected);
            else
                state.AcceptedCopy.RequireUnchanged(current);
            if (ModelReference(NativeRead(() => state.Parent.Ref)) != state.ParentReference)
                throw new InvalidOperationException("The captured destination parent identity changed; no deletion was attempted.");
            CopyTreePolicy.RequireAppend(state.ParentMembers, ModelMemberReferences(state.Parent), ModelReference(reference));
            result["cleanup_verified_refnos"] = current.Select(node => node.Reference).ToArray();
            result["cleanup_verified_new_refnos"] = result["cleanup_verified_refnos"];
            result["cleanup_verification_scope"] = state.AcceptedCopy == null ? "exact_empty_created_root" : "captured_native_copy_result";
            ModelCleanupCreated(result, created, reference, state.ParentReference, state.ParentMembers, state.AcceptedCopy == null);
            bool allDeleted = true;
            foreach (CopyTreeIdentity node in current)
            {
                ThrowIfNativeFaulted();
                string[] pair = node.Reference.Substring(1).Split('/');
                DbElement remaining = NativeRead(() => DbElement.GetElement(new[] { int.Parse(pair[0], CultureInfo.InvariantCulture), int.Parse(pair[1], CultureInfo.InvariantCulture) }));
                if (remaining == null || ModelReference(NativeRead(() => remaining.Ref)) != node.Reference ||
                    NativeRead(() => remaining.IsNull) || NativeRead(() => remaining.IsValid) || !NativeRead(() => remaining.IsDeleted)) allDeleted = false;
            }
            result["cleanup_captured_nodes_deleted"] = allDeleted;
            result["cleanup_all_new_nodes_deleted"] = allDeleted;
            if (!allDeleted)
            {
                result["cleanup_succeeded"] = false;
                result["creation_result_unknown"] = true;
            }
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            result["cleanup_succeeded"] = false;
            result["creation_result_unknown"] = true;
            result["cleanup_error"] = "Copy cleanup could not verify the unchanged captured native result or the exact empty root; no additional deletion was attempted. " + ex.Message;
        }
    }

    private static void CopyWriteVector(BinaryWriter writer, double[] values)
    {
        writer.Write(values != null);
        if (values != null) foreach (double value in values) writer.Write(value);
    }
    private static void CopyWritePosition(BinaryWriter writer, Position value)
    {
        writer.Write(value != null); if (value != null) PrimitiveWritePositionBytes(writer, value);
    }
    private static void CopyWriteOrientation(BinaryWriter writer, Orientation value)
    {
        writer.Write(value != null); if (value != null) PrimitiveWriteOrientationBytes(writer, value);
    }
}
