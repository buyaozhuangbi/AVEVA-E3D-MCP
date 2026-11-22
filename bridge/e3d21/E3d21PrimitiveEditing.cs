using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Aveva.Core.Database;
using Aveva.Core.Geometry;
using Aveva.Core.Utilities.Messaging;

namespace E3d21AiBridge;

public sealed partial class E3d21Context
{
    private const int PrimitiveMemberLimit = 10000;
    private const int PrimitiveNegativeLimit = 128;
    private const int PrimitiveDepthLimit = 8;
    private readonly string primitiveRevisionSession = Guid.NewGuid().ToString("N");

    private sealed class PrimitiveNode
    {
        internal DbElement Element;
        internal string Reference, Type, Shape;
        internal bool Negative;
        internal readonly Dictionary<string, DbAttribute> Attributes = new Dictionary<string, DbAttribute>(StringComparer.Ordinal);
        internal readonly Dictionary<string, DbDouble> NativeDimensions = new Dictionary<string, DbDouble>(StringComparer.Ordinal);
        internal readonly Dictionary<string, double> Dimensions = new Dictionary<string, double>(StringComparer.Ordinal);
        internal Position NativePosition, WorldPosition;
        internal Orientation NativeOrientation, WorldOrientation;
        internal Position LocalToOwnerPosition;
        internal Orientation LocalToOwnerOrientation;
        internal string StorageFrameReference;
        internal readonly List<PrimitiveNode> Children = new List<PrimitiveNode>();
        internal readonly List<string> MemberReferences = new List<string>();
        internal readonly List<string> AdmissionReasons = new List<string>();
        internal readonly Dictionary<string, string> ReadErrors = new Dictionary<string, string>(StringComparer.Ordinal);
        internal readonly List<string> GuardSignature = new List<string>();
        internal bool Complete = true;
        internal Dictionary<string, object> ToDictionary(bool recurse = true)
        {
            var transform = new Dictionary<string, object>
            {
                ["position"] = NativePosition == null ? null : PrimitivePositionVector(NativePosition),
                ["orientation"] = NativeOrientation == null ? null : PrimitiveOrientationValue(NativeOrientation)
            };
            var world = new Dictionary<string, object>
            {
                ["position"] = WorldPosition == null ? null : PrimitivePositionVector(WorldPosition),
                ["orientation"] = WorldOrientation == null ? null : PrimitiveOrientationValue(WorldOrientation)
            };
            return new Dictionary<string, object>
            {
                ["element"] = new Dictionary<string, object> { ["refno"] = Reference, ["type"] = Type, ["name"] = PrimitiveSafeName(Element) },
                ["shape"] = Shape,
                ["negative"] = Negative,
                ["is_negative"] = Negative,
                ["unit"] = "mm",
                ["dimensions"] = new Dictionary<string, double>(Dimensions),
                ["frame"] = new Dictionary<string, object>
                {
                    ["storage_frame_refno"] = StorageFrameReference,
                    ["native"] = transform,
                    ["world"] = world,
                    ["relative_to_owner"] = LocalToOwnerPosition == null || LocalToOwnerOrientation == null ? null :
                        new Dictionary<string, object>
                        {
                            ["position"] = PrimitivePositionVector(LocalToOwnerPosition),
                            ["orientation"] = PrimitiveOrientationValue(LocalToOwnerOrientation)
                        }
                },
                ["negative_children"] = recurse ? Children.Select(child => child.ToDictionary(true)).Cast<object>().ToList() : new List<object>(),
                ["member_refnos"] = MemberReferences.ToArray(),
                ["complete"] = Complete,
                ["read_errors"] = new Dictionary<string, string>(ReadErrors),
                ["edit_reasons"] = AdmissionReasons.ToArray()
            };
        }
    }

    private sealed class PrimitiveEditState
    {
        internal DbElement Target, Owner;
        internal PrimitiveNode Geometry, OwnerGeometry;
        internal string TargetReference, OwnerReference, Revision, ProjectName, MdbName;
        internal string[] OwnerMembers;
        internal readonly List<string> Reasons = new List<string>();
        internal readonly Dictionary<string, string> Errors = new Dictionary<string, string>(StringComparer.Ordinal);
        internal readonly List<string> AncestorSignature = new List<string>();
        internal bool Complete;
        internal bool Eligible { get { return Complete && Reasons.Count == 0 && Revision != null; } }
    }

    public object GetPrimitiveGeometry(string refno)
    {
        string targetReference = ModelEditInput.ExplicitReference(refno, "refno");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
            PrimitiveEditState state = PrimitiveReadEditState(ModelRequireElement(targetReference));
            var result = PrimitiveStateResult(state);
            result["success"] = state.Complete;
            result["revision_scope"] = "Bridge session/project/MDB, target identity and type, direct owner identity and ordered members, target and supported negative subtree dimensions/native and WORLD frames/member order/supported link and rule states, plus guarded ancestor identity/frame/link/rule states. Names are excluded. Inapplicable NAPP/RATM/RATS/RREF attributes bind their SDK unsupported status rather than an invented value.";
            result["native_cut_scope"] = "Negative application scope is not exposed on some Design primitives by the public SDK. The revision binds supported Design link/rule states and retained negative ownership, and does not claim a single-target material cut.";
            return result;
        });
    }

    public object UpdatePrimitive(string refno, string expectedRevision, IDictionary<string, double> dimensions,
        double[] position, double[] xAxis, double[] yAxis, string wrt, bool dryRun)
    {
        string targetReference = ModelEditInput.ExplicitReference(refno, "refno");
        string expected = expectedRevision == null ? null : expectedRevision.Trim().ToLowerInvariant();
        if (expected != null && (expected.Length != 64 || expected.Any(c => !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f'))))
            throw new ArgumentException("expected_revision must contain exactly 64 hexadecimal characters.", "expectedRevision");
        if (!dryRun && expected == null)
            throw new ArgumentException("Execution requires expected_revision from a current e3d_primitive_geometry result.", "expectedRevision");
        if (dimensions != null && dimensions.Count > 3)
            throw new ArgumentException("dimensions may contain at most three shape dimensions.", "dimensions");
        var requestedDimensions = new Dictionary<string, double>(StringComparer.Ordinal);
        if (dimensions != null)
            foreach (KeyValuePair<string, double> item in dimensions)
            {
                ThrowIfNativeFaulted();
                string key = (item.Key ?? "").Trim().ToUpperInvariant();
                if (key.Length == 0 || requestedDimensions.ContainsKey(key))
                    throw new ArgumentException("Dimension names must be nonempty and unique after normalization.", "dimensions");
                if (!double.IsNaN(item.Value) && !double.IsInfinity(item.Value)) requestedDimensions.Add(key, item.Value);
                else throw new ArgumentException(key + " must be finite.", "dimensions");
            }
        ModelEditInput input = new ModelEditInput(position, xAxis, yAxis, wrt, false);
        bool hasTransform = input.Position != null || input.XAxis != null;
        if (requestedDimensions.Count == 0 && !hasTransform)
            throw new ArgumentException("Supply at least one shape dimension, position, or orientation change.");

        string command = "database-update-primitive " + targetReference;
        if (dryRun ? IsCommandActive() : !TryBeginCommand(command)) return BusyResult(command);
        try
        {
            return OnUi(delegate
            {
                var result = new Dictionary<string, object>
                {
                    ["success"] = false, ["dry_run"] = dryRun, ["refno"] = targetReference,
                    ["write_attempted"] = false, ["write_applied"] = false,
                    ["rollback_attempted"] = false, ["rollback_succeeded"] = null,
                    ["rollback_verified_original"] = null, ["rollback_errors"] = new string[0],
                    ["attempted_steps"] = new string[0], ["completed_steps"] = new string[0],
                    ["partial"] = false, ["outcome_uncertain"] = false, ["retry_safe"] = true,
                    ["readback_verified"] = dryRun ? (object)null : false,
                    ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null,
                    ["revision_conflict"] = false, ["error"] = null
                };
                PrimitiveEditState original = null;
                result["ce_before"] = ModelCurrentReference();
                try
                {
                    DbElement target = ModelRequireElement(targetReference);
                    original = PrimitiveReadEditState(target);
                    PrimitivePopulateUpdateResult(result, original);
                    if (!original.Eligible)
                        throw new InvalidOperationException("The primitive is outside the supported edit state; inspect edit_reasons and complete.");
                    if (expected != null && expected != original.Revision)
                    {
                        result["revision_conflict"] = true;
                        throw new InvalidOperationException("expected_revision is stale. Inspect the current primitive again before editing.");
                    }

                    PrimitiveNode targetNode = original.Geometry;
                    foreach (string key in requestedDimensions.Keys)
                        if (!targetNode.Attributes.ContainsKey(key))
                            throw new ArgumentException("Dimension " + key + " does not belong to primitive shape " + targetNode.Shape + ".");
                    var wantedDimensions = new Dictionary<string, double>(targetNode.Dimensions, StringComparer.Ordinal);
                    foreach (KeyValuePair<string, double> item in requestedDimensions) wantedDimensions[item.Key] = item.Value;
                    PrimitiveValidateDimensions(targetNode.Shape, wantedDimensions);

                    ModelEditInput nativeInput = null;
                    DbElement requestedFrame = null;
                    DbElement storageFrame = null;
                    if (hasTransform)
                    {
                        requestedFrame = ModelFrame(input.Wrt, original.Owner);
                        ModelRequireStableFrame(target, requestedFrame);
                        storageFrame = ModelStorageFrame(target.Owner);
                        ModelRequireStorageFrame(target, storageFrame, new ModelEditInput(input.Position, input.XAxis, input.YAxis, "OWNER", false));
                        nativeInput = ModelStorageTransform(storageFrame, requestedFrame).ToNative(input);
                        if (input.Position != null)
                            ModelRequireSettable(target, DbAttributeInstance.POS);
                        if (input.XAxis != null)
                            ModelRequireSettable(target, DbAttributeInstance.ORI);
                    }
                    var plan = PrimitiveBuildUpdateSteps(original, requestedDimensions, wantedDimensions, nativeInput, result);
                    result["submitted"] = new Dictionary<string, object>
                    {
                        ["dimensions_mm"] = new Dictionary<string, double>(requestedDimensions),
                        ["position_mm"] = input.Position,
                        ["orientation"] = input.XAxis == null ? null : new Dictionary<string, object>
                            { ["x_axis"] = input.XAxis, ["y_axis"] = input.YAxis },
                        ["wrt"] = hasTransform ? input.Wrt : null
                    };
                    result["planned_steps"] = plan.Select(item => item.Name).ToArray();
                    if (dryRun)
                    {
                        result["geometry"] = PrimitiveStateResult(original);
                        PrimitiveCaptureCeAfter(result);
                        if (!Equals(result["ce_unchanged"], true)) throw new InvalidOperationException("Current element changed during primitive preview.");
                        result["success"] = true;
                        return result;
                    }

                    PrimitiveEditState immediatelyBefore = PrimitiveReadEditState(target);
                    if (!immediatelyBefore.Eligible || immediatelyBefore.Revision != expected || immediatelyBefore.Revision != original.Revision)
                    {
                        result["revision_conflict"] = true;
                        throw new InvalidOperationException("Primitive, owner, supported cuts, frame, rules, links, or project context changed during preflight; no setter was attempted.");
                    }
                    foreach (PrimitiveUpdateStep step in plan)
                    {
                        ThrowIfNativeFaulted();
                        Action apply = step.Apply;
                        step.Apply = delegate
                        {
                            result["write_attempted"] = true;
                            result["retry_safe"] = false;
                            apply();
                        };
                    }
                    PrimitiveUpdateRunResult run = PrimitiveUpdateRunner.Run(plan,
                        delegate
                        {
                            PrimitiveEditState actual = PrimitiveReadEditState(target);
                            PrimitiveRequireUpdateReadback(original, actual, requestedDimensions, wantedDimensions,
                                input, requestedFrame, nativeInput);
                            result["geometry"] = PrimitiveStateResult(actual);
                            result["revision"] = actual.Revision;
                            result["readback_verified"] = true;
                        },
                        delegate
                        {
                            PrimitiveEditState actual = PrimitiveReadEditState(target);
                            result["residual_geometry"] = PrimitiveStateResult(actual);
                            result["revision"] = actual.Revision;
                            result["residual_complete"] = actual.Complete;
                            return actual.Eligible && actual.Revision == original.Revision;
                        });
                    result["attempted_steps"] = run.attempted_steps;
                    result["completed_steps"] = run.completed_steps;
                    result["rollback_attempted"] = run.rollback_attempted;
                    result["rollback_succeeded"] = run.rollback_succeeded;
                    result["rollback_verified_original"] = run.rollback_verified_original;
                    result["rollback_errors"] = run.rollback_errors;
                    if (run.error != null)
                    {
                        result["error"] = run.error.Message;
                        result["partial"] = run.attempted_steps.Length > 0 && !Equals(run.rollback_verified_original, true);
                        result["retry_safe"] = Equals(run.rollback_succeeded, true);
                        if (run.attempted_steps.Length > 0 && run.rollback_verified_original != true &&
                            !Equals(result.ContainsKey("residual_complete") ? result["residual_complete"] : null, true))
                        {
                            result["revision"] = null;
                            result["geometry"] = null;
                            result["residual_complete"] = false;
                            result["outcome_uncertain"] = true;
                        }
                    }
                    else
                    {
                        result["write_applied"] = run.attempted_steps.Length > 0;
                        result["success"] = true;
                        result["retry_safe"] = true;
                    }
                    if (run.error != null && run.attempted_steps.Length > 0 && run.rollback_verified_original != true)
                        result["write_applied"] = null;
                }
                catch (Exception ex)
                {
                    ThrowIfNativeFaulted();
                    result["error"] = ex.Message;
                    result["partial"] = Equals(result["write_attempted"], true) && !Equals(result["rollback_verified_original"], true);
                    if (result["rollback_succeeded"] == null && !Equals(result["write_attempted"], true))
                        result["rollback_succeeded"] = null;
                    if (Equals(result["write_attempted"], true) && !Equals(result["rollback_verified_original"], true))
                    {
                        result["outcome_uncertain"] = true;
                        result["revision"] = null;
                        result["geometry"] = null;
                    }
                }
                finally { PrimitiveCaptureCeAfter(result); }
                if (!Equals(result["ce_unchanged"], true))
                {
                    result["success"] = false;
                    result["error"] = result["error"] ?? "Current element could not be verified unchanged.";
                }
                return result;
            });
        }
        finally { if (!dryRun) EndCommand(); }
    }

    private PrimitiveEditState PrimitiveReadEditState(DbElement target)
    {
        ThrowIfNativeFaulted();
        var state = new PrimitiveEditState { Target = target };
        try
        {
            if (!IsUsable(target) || target.DbType != DbType.Design)
                throw new InvalidOperationException("The target must be a usable Design primitive.");
            state.TargetReference = ModelReference(target.Ref);
            state.Owner = target.Owner;
            if (!IsUsable(state.Owner)) throw new InvalidOperationException("The primitive owner is unreadable.");
            state.OwnerReference = ModelReference(state.Owner.Ref);
            state.OwnerMembers = PrimitiveMemberReferences(state.Owner);
            if (!state.OwnerMembers.Contains(state.TargetReference, StringComparer.Ordinal))
                throw new InvalidOperationException("The primitive is not present in its reported owner membership.");
            PrimitiveReadAncestors(state);
            int visited = 0;
            state.Geometry = PrimitiveReadNode(target, state, 0, ref visited, true);
            state.Complete = state.Geometry.Complete && state.Errors.Count == 0;
            if (state.Geometry.AdmissionReasons.Count > 0) state.Reasons.AddRange(state.Geometry.AdmissionReasons);
            if (state.Owner.GetElementType().ShortName == "TMPL")
                state.Reasons.Add("Template-owned primitives are outside guarded editing.");
            if (state.Geometry.Negative)
            {
                PrimitiveNode ownerNode = PrimitiveReadNode(state.Owner, state, 0, ref visited, false);
                state.OwnerGeometry = ownerNode;
                if (!ownerNode.Complete) state.Complete = false;
                if (!new[] { "CYLI", "BOX", "CONE" }.Contains(ownerNode.Shape, StringComparer.Ordinal))
                    state.Reasons.Add("Negative editing requires a supported positive primitive owner so owner cut scope is included in the revision.");
                if (ownerNode.AdmissionReasons.Count > 0) state.Reasons.AddRange(ownerNode.AdmissionReasons);
            }
            if (state.Geometry.Shape == null)
                state.Reasons.Add("Target primitive type is outside the supported CYLI/NCYL, BOX/NBOX, CONE/NCON and positive DISH shapes.");
            if (state.Target.GetAttributesWithRules() == null)
                throw new InvalidOperationException("Primitive geometry rule state could not be read.");
            if (state.Complete && state.Reasons.Count == 0)
                state.Revision = PrimitiveFingerprint(state);
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            state.Complete = false;
            state.Errors["read"] = ex.Message;
            state.Reasons.Add(ex.Message);
            state.Revision = null;
        }
        return state;
    }

    private PrimitiveNode PrimitiveReadNode(DbElement element, PrimitiveEditState edit, int depth, ref int visited, bool root)
    {
        ThrowIfNativeFaulted();
        if (depth > PrimitiveDepthLimit) throw new InvalidOperationException("Negative primitive membership exceeds depth " + PrimitiveDepthLimit + ".");
        if (++visited > PrimitiveNegativeLimit) throw new InvalidOperationException("Primitive geometry exceeds " + PrimitiveNegativeLimit + " nodes.");
        var node = new PrimitiveNode { Element = element, Reference = ModelReference(element.Ref), Type = element.GetElementType().ShortName };
        if (root) edit.Geometry = node;
        node.Negative = PrimitiveIsNegative(node.Type);
        node.Shape = PrimitiveShape(node.Type);
        if (node.Shape == "DISH")
        {
            DbElementType actualType = NativeRead(() => element.GetActualType());
            DbElementType baseType = NativeRead(() => element.GetElementType());
            if (!NativeRead(() => actualType == baseType))
                node.AdmissionReasons.Add("User-defined DISH types are outside guarded primitive editing.");
        }
        if (node.Shape == null)
        {
            node.Complete = false;
            node.AdmissionReasons.Add("Unsupported member primitive type " + node.Type + ".");
        }
        else
        {
            foreach (KeyValuePair<string, string> field in PrimitiveShapeFields(node.Shape))
            {
                ThrowIfNativeFaulted();
                DbAttribute attribute = NativeRead(() => DbAttribute.GetDbAttribute(field.Value));
                if (attribute == null || !NativeRead(() => attribute.IsValid) || !NativeRead(() => element.IsAttributeValid(attribute)))
                    throw new InvalidOperationException(field.Value + " is not readable on " + node.Reference + ".");
                DbDouble value = NativeRead(() => element.GetDbDouble(attribute));
                // Dimension/Units getters construct SDK metadata and can enter
                // native code. Split chained native getters at each boundary;
                // Set and Value themselves are managed fields in this SDK.
                if (value == null || NativeRead(() => value.Dimension) == null || !NativeRead(() => value.Dimension).Set ||
                    NativeRead(() => NativeRead(() => value.Dimension).StandardDimension) != DbDimension.DIST)
                    throw new InvalidOperationException(field.Value + " did not return a dimensioned DIST value on " + node.Reference + ".");
                DbDoubleUnits mm = NativeRead(() => DbDoubleUnits.GetUnits("mm", NativeRead(() => value.Dimension)));
                if (mm == null || !mm.Set || NativeRead(() => mm.Dimension) != NativeRead(() => value.Dimension) ||
                    NativeRead(() => NativeRead(() => mm.Dimension).StandardDimension) != DbDimension.DIST)
                    throw new InvalidOperationException("The SDK could not explicitly resolve millimetre units for " + field.Value + ".");
                DbDouble converted = NativeRead(() => value.ConvertUnits(mm));
                if (converted == null || NativeRead(() => converted.Dimension) != NativeRead(() => value.Dimension) ||
                    NativeRead(() => converted.Units) != mm)
                    throw new InvalidOperationException("The SDK could not verify millimetre conversion for " + field.Value + ".");
                double number = PrimitiveFinite(converted.Value, field.Value);
                node.Attributes[field.Key] = attribute;
                node.NativeDimensions[field.Key] = value;
                node.Dimensions[field.Key] = number;
            }
        }
        node.NativePosition = element.GetPosition(DbAttributeInstance.POS);
        node.NativeOrientation = element.GetOrientation(DbAttributeInstance.ORI);
        if (node.NativePosition == null || node.NativeOrientation == null)
            throw new InvalidOperationException("The primitive POS/ORI frame could not be read: " + node.Reference);
        DbElement storage = ModelStorageFrame(element.Owner);
        node.StorageFrameReference = ModelReference(storage.Ref);
        ModelRequireStorageFrame(element, storage, new ModelEditInput(PrimitivePositionVector(node.NativePosition), new[] { 1.0, 0, 0 }, new[] { 0.0, 1, 0 }, "OWNER", false));
        DbElement world = ModelFrame("WORLD", element.Owner);
        var worldQualifier = new DbQualifier { wrtQualifier = world };
        node.WorldPosition = element.GetPosition(DbAttributeInstance.POS, worldQualifier);
        node.WorldOrientation = element.GetOrientation(DbAttributeInstance.ORI, worldQualifier);
        if (node.WorldPosition == null || node.WorldOrientation == null)
            throw new InvalidOperationException("The primitive WORLD frame could not be read: " + node.Reference);
        PrimitiveReadGuardState(element, node);

        MemberPage<DbElement> members = MemberPage<DbElement>.Read(PrimitiveDirectMembers(element), PrimitiveNegativeLimit,
            item => ModelReference(item.Ref));
        if (members.HasMore != false || members.ReadErrors.Count > 0)
            throw new InvalidOperationException("Primitive child membership is incomplete: " + string.Join(" ", members.ReadErrors));
        node.MemberReferences.AddRange(members.Items.Select(item => ModelReference(item.Ref)));
        if (node.Shape == "DISH" && members.Items.Count > 0)
            node.AdmissionReasons.Add("DISH primitives with child members are outside guarded editing; only standalone positive DISH primitives are supported.");
        if (node.Negative && members.Items.Count > 0)
            node.AdmissionReasons.Add("Negative primitives with child members are outside guarded editing.");
        foreach (DbElement child in members.Items)
        {
            ThrowIfNativeFaulted();
            if (!PrimitiveIsNegative(child.GetElementType().ShortName))
            {
                node.Complete = false;
                node.AdmissionReasons.Add("A non-negative child is outside the supported cut subtree: " + ModelReference(child.Ref));
                node.Children.Add(PrimitiveReadUnsupportedChild(child));
                continue;
            }
            PrimitiveNode negative = PrimitiveReadNode(child, edit, depth + 1, ref visited, false);
            if (!negative.Negative || negative.Shape == null)
            {
                node.Complete = false;
                node.AdmissionReasons.Add("An unsupported negative child prevents an edit revision: " + negative.Reference);
            }
            if (ModelReference(child.Owner.Ref) != node.Reference)
                throw new InvalidOperationException("A reported negative member does not have the primitive as its direct owner.");
            ModelRequireStorageFrame(child, element, new ModelEditInput(null, negativeLocalXAxis, negativeLocalYAxis, "OWNER", false));
            var ownerQualifier = new DbQualifier { wrtQualifier = element };
            negative.LocalToOwnerPosition = child.GetPosition(DbAttributeInstance.POS, ownerQualifier);
            negative.LocalToOwnerOrientation = child.GetOrientation(DbAttributeInstance.ORI, ownerQualifier);
            if (negative.LocalToOwnerPosition == null || negative.LocalToOwnerOrientation == null)
                throw new InvalidOperationException("A negative child's owner-relative frame could not be read: " + negative.Reference);
            node.Children.Add(negative);
        }
        foreach (PrimitiveNode child in node.Children)
            node.AdmissionReasons.AddRange(child.AdmissionReasons);
        if (node.Shape != null)
        {
            try { PrimitiveValidateDimensions(node.Shape, node.Dimensions); }
            catch (Exception ex) { ThrowIfNativeFaulted(); node.AdmissionReasons.Add("Existing dimensions are outside supported shape limits: " + ex.Message); }
        }
        return node;
    }

    private static readonly double[] negativeLocalXAxis = { 1.0, 0, 0 };
    private static readonly double[] negativeLocalYAxis = { 0.0, 1, 0 };

    private PrimitiveNode PrimitiveReadUnsupportedChild(DbElement element)
    {
        ThrowIfNativeFaulted();
        return new PrimitiveNode
        {
            Element = element,
            Reference = ModelReference(element.Ref),
            Type = element.GetElementType().ShortName,
            Shape = PrimitiveShape(element.GetElementType().ShortName),
            Negative = PrimitiveIsNegative(element.GetElementType().ShortName),
            Complete = false
        };
    }

    private static IEnumerable<DbElement> PrimitiveDirectMembers(DbElement parent)
    {
        ThrowIfNativeFaulted();
        string expectedOwner = ModelReference(parent.Ref);
        DbElement member = NativeRead(() => parent.FirstMember());
        while (member != null && !NativeRead(() => member.IsNull))
        {
            ThrowIfNativeFaulted();
            if (!IsUsable(member) || ModelReference(member.Owner.Ref) != expectedOwner)
                throw new InvalidOperationException("Primitive member is unreadable or has an unexpected owner.");
            yield return member;
            member = NativeRead(() => member.Next());
        }
    }

    private static string[] PrimitiveMemberReferences(DbElement parent)
    {
        ThrowIfNativeFaulted();
        MemberPage<DbElement> page = MemberPage<DbElement>.Read(PrimitiveDirectMembers(parent), PrimitiveMemberLimit,
            item => ModelReference(item.Ref));
        ThrowIfNativeFaulted();
        if (page.HasMore != false || page.ReadErrors.Count > 0)
            throw new InvalidOperationException("Ordered owner membership could not be read completely: " + string.Join(" ", page.ReadErrors));
        return page.Items.Select(item => ModelReference(item.Ref)).ToArray();
    }

    private void PrimitiveReadAncestors(PrimitiveEditState state)
    {
        ThrowIfNativeFaulted();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        DbElement current = state.Owner;
        DbElement world = ModelFrame("WORLD", current);
        for (int depth = 0; depth < 1024 && IsUsable(current); depth++)
        {
            ThrowIfNativeFaulted();
            string reference = ModelReference(current.Ref);
            if (!seen.Add(reference)) throw new InvalidOperationException("Cycle found in primitive ancestor membership.");
            string type = current.GetElementType().ShortName;
            if (type == "TMPL") state.Reasons.Add("Template ancestors are outside guarded primitive editing: " + reference);
            PrimitiveGuardElement(current, state.AncestorSignature, state.Reasons);
            DbAttribute[] ancestorRules = current.GetAttributesWithRules();
            if (ancestorRules == null) throw new InvalidOperationException("Ancestor geometry rules could not be read: " + reference);
            state.AncestorSignature.Add(reference + "|geometry-rules|" + string.Join(",", ancestorRules
                .Where(attribute => attribute.ShortName == "POS" || attribute.ShortName == "ORI")
                .Select(attribute => attribute.ShortName).OrderBy(name => name, StringComparer.Ordinal)));
            if (ancestorRules.Any(attribute => attribute.ShortName == "POS" || attribute.ShortName == "ORI"))
                state.Reasons.Add("A primitive ancestor has rule-driven positioning: " + reference);
            Position nativePosition = null, worldPosition = null;
            Orientation nativeOrientation = null, worldOrientation = null;
            if (current.IsAttributeValid(DbAttributeInstance.POS) && current.IsAttributeValid(DbAttributeInstance.ORI))
            {
                nativePosition = current.GetPosition(DbAttributeInstance.POS);
                nativeOrientation = current.GetOrientation(DbAttributeInstance.ORI);
                var qualifier = new DbQualifier { wrtQualifier = world };
                worldPosition = current.GetPosition(DbAttributeInstance.POS, qualifier);
                worldOrientation = current.GetOrientation(DbAttributeInstance.ORI, qualifier);
                PrimitiveAppendTransform(state.AncestorSignature, reference, nativePosition, nativeOrientation, worldPosition, worldOrientation);
            }
            else state.AncestorSignature.Add(reference + "|no-frame");
            if (current.GetElementType().IsWorld) return;
            current = NativeRead(() => current.Owner);
        }
        throw new InvalidOperationException("The Design world was not reached while verifying primitive ancestors.");
    }

    private static void PrimitiveReadGuardState(DbElement element, PrimitiveNode node)
    {
        ThrowIfNativeFaulted();
        PrimitiveGuardElement(element, node.GuardSignature, node.AdmissionReasons);
        if (node.Negative)
        {
            DbAttribute application = DbAttribute.GetDbAttribute("NAPP");
            bool supported = application != null && application.IsValid && element.IsAttributeValid(application);
            if (supported)
            {
                int flag = element.GetInteger(application);
                node.GuardSignature.Add(node.Reference + "|NAPP|" + flag.ToString(CultureInfo.InvariantCulture));
                if (flag != -1 && flag != 2)
                    node.AdmissionReasons.Add("Negative application scope is outside default/owner-only scopes on " + node.Reference + ".");
            }
            else
            {
                // NAPP is a catalogue-only pseudo attribute on this Design
                // schema. Record absence without inventing a default scope.
                node.GuardSignature.Add(node.Reference + "|NAPP|Design-unsupported");
            }
        }
        DbAttribute[] rules = element.GetAttributesWithRules();
        if (rules == null) throw new InvalidOperationException("Dynamic rule state could not be read: " + node.Reference);
        HashSet<string> geometryFields = new HashSet<string>(node.Attributes.Keys.Concat(new[] { "POS", "ORI" }), StringComparer.OrdinalIgnoreCase);
        foreach (DbAttribute rule in rules)
            if (geometryFields.Contains(NativeRead(() => rule.ShortName)))
                node.AdmissionReasons.Add("Rule-driven primitive geometry is unsupported: " + NativeRead(() => rule.ShortName) + " on " + node.Reference);
    }

    private static void PrimitiveGuardElement(DbElement element, IList<string> signature, IList<string> reasons)
    {
        ThrowIfNativeFaulted();
        string reference = ModelReference(element.Ref);
        foreach (string name in new[] { "ORRF", "SPRE", "CREF", "ASREF", "SREF", "NGMR", "MVER" })
        {
            ThrowIfNativeFaulted();
            DbAttribute attribute = DbAttribute.GetDbAttribute(name);
            if (attribute == null || !attribute.IsValid) throw new InvalidOperationException("SDK metadata is missing guard attribute " + name + ".");
            if (!element.IsAttributeValid(attribute))
            {
                signature.Add(reference + "|" + name + "|unsupported");
                continue;
            }
            if (attribute.Type == DbAttributeType.ELEMENT && !attribute.IsArray)
            {
                DbElement value = element.GetElement(attribute);
                if (value == null) throw new InvalidOperationException(name + " could not be read reliably on " + reference + ".");
                string linked = value.IsNull ? "" : ModelReference(value.Ref);
                signature.Add(reference + "|" + name + "|" + linked);
                if (!value.IsNull) reasons.Add(name + " is linked on " + reference + "; linked geometry is outside guarded editing.");
            }
            else
                throw new InvalidOperationException("Guard attribute " + name + " has unsupported SDK shape on " + reference + ".");
        }

        DbAttribute[] dependencyAttributes = new[]
        {
            DbAttribute.GetDbAttribute("RATM"), DbAttribute.GetDbAttribute("RATS"), DbAttribute.GetDbAttribute("RREF")
        };
        foreach (DbAttribute attribute in dependencyAttributes)
        {
            ThrowIfNativeFaulted();
            if (attribute == null || !attribute.IsValid) throw new InvalidOperationException("SDK metadata for dynamic-rule dependencies is unavailable.");
            if (!element.IsAttributeValid(attribute))
            {
                signature.Add(reference + "|" + attribute.ShortName + "|Design-unsupported");
                continue;
            }
            if (attribute.Type == DbAttributeType.STRINGARRAY || attribute.Type == DbAttributeType.WORD && attribute.IsArray)
            {
                string[] values = element.GetAsStringArray(attribute);
                if (values == null) throw new InvalidOperationException(attribute.ShortName + " dependency state could not be read on " + reference + ".");
                string normalized = string.Join("\u001f", values.Select(value => (value ?? "").Trim()));
                signature.Add(reference + "|" + attribute.ShortName + "|" + normalized);
                if (values.Any(value => !string.IsNullOrWhiteSpace(value) && !PrimitiveUnset(value)))
                    reasons.Add("Dynamic-rule dependency " + attribute.ShortName + " is nonempty on " + reference + ".");
            }
            else if (attribute.Type == DbAttributeType.ELEMENT && attribute.IsArray)
            {
                DbElement[] values = element.GetElementArray(attribute);
                if (values == null) throw new InvalidOperationException(attribute.ShortName + " dependency state could not be read on " + reference + ".");
                string[] refs = values.Select(value => value == null || value.IsNull ? "" : ModelReference(value.Ref)).ToArray();
                signature.Add(reference + "|" + attribute.ShortName + "|" + string.Join("\u001f", refs));
                if (refs.Any(value => value.Length != 0)) reasons.Add("Dynamic-rule dependency " + attribute.ShortName + " is nonempty on " + reference + ".");
            }
            else throw new InvalidOperationException("Dynamic-rule dependency attribute has unsupported SDK shape: " + attribute.ShortName + ".");
        }
    }

    private static bool PrimitiveUnset(string value)
    {
        string text = (value ?? "").Trim();
        return text.Length == 0 || text.Equals("unset", StringComparison.OrdinalIgnoreCase) ||
            text.Equals("nulref", StringComparison.OrdinalIgnoreCase) || text.Equals("null", StringComparison.OrdinalIgnoreCase);
    }

    private static string PrimitiveSafeName(DbElement element)
    {
        ThrowIfNativeFaulted();
        try { return element != null && element.IsAttributeValid(DbAttributeInstance.NAME) ? element.GetString(DbAttributeInstance.NAME) : null; }
        catch { ThrowIfNativeFaulted(); return null; }
    }

    private static void PrimitiveAppendTransform(IList<string> signature, string reference,
        Position nativePosition, Orientation nativeOrientation, Position worldPosition, Orientation worldOrientation)
    {
        if (nativePosition == null || nativeOrientation == null || worldPosition == null || worldOrientation == null)
            throw new InvalidOperationException("A complete native/WORLD ancestor frame could not be read: " + reference);
        signature.Add(reference + "|" + string.Join(",", PrimitivePositionVector(nativePosition).Select(PrimitiveNumberToken)) +
            "|" + PrimitiveOrientationToken(nativeOrientation) + "|" + string.Join(",", PrimitivePositionVector(worldPosition).Select(PrimitiveNumberToken)) +
            "|" + PrimitiveOrientationToken(worldOrientation));
    }

    private string PrimitiveFingerprint(PrimitiveEditState state)
    {
        MDB mdb = CurrentMdb();
        Project project = Project.CurrentProject;
        if (mdb == null || project == null) throw new InvalidOperationException("Project/MDB context is unavailable.");
        state.ProjectName = project.Name;
        state.MdbName = mdb.Name;
        return PrimitiveHash(delegate(BinaryWriter writer)
        {
            writer.Write("primitive-edit-v1");
            writer.Write(primitiveRevisionSession);
            writer.Write(state.ProjectName); writer.Write(state.MdbName);
            writer.Write(state.TargetReference); writer.Write(state.Target.GetElementType().ShortName);
            writer.Write(state.OwnerReference);
            foreach (string reference in state.OwnerMembers) writer.Write(reference);
            writer.Write("owner-members-end");
            PrimitiveWriteNode(writer, state.Geometry);
            if (state.OwnerGeometry != null) PrimitiveWriteNode(writer, state.OwnerGeometry);
            foreach (string token in state.AncestorSignature) writer.Write(token);
        });
    }

    private static void PrimitiveWriteNode(BinaryWriter writer, PrimitiveNode node)
    {
        writer.Write(node.Reference); writer.Write(node.Type); writer.Write(node.Shape ?? ""); writer.Write(node.Negative);
        foreach (KeyValuePair<string, double> value in node.Dimensions.OrderBy(item => item.Key, StringComparer.Ordinal))
        {
            ThrowIfNativeFaulted();
            writer.Write(value.Key); writer.Write(value.Value);
            DbDouble native = node.NativeDimensions[value.Key];
            writer.Write(PrimitiveFinite(NativeRead(() => native.DatabaseValue), value.Key + " database value"));
            writer.Write(NativeRead(() => NativeRead(() => native.Dimension).Name));
            writer.Write(NativeRead(() => NativeRead(() => native.Dimension).StandardDimension).ToString());
        }
        PrimitiveWritePositionBytes(writer, node.NativePosition);
        PrimitiveWriteOrientationBytes(writer, node.NativeOrientation);
        PrimitiveWritePositionBytes(writer, node.WorldPosition);
        PrimitiveWriteOrientationBytes(writer, node.WorldOrientation);
        if (node.LocalToOwnerPosition != null) PrimitiveWritePositionBytes(writer, node.LocalToOwnerPosition);
        if (node.LocalToOwnerOrientation != null) PrimitiveWriteOrientationBytes(writer, node.LocalToOwnerOrientation);
        writer.Write(node.StorageFrameReference ?? "");
        foreach (string token in node.GuardSignature) writer.Write(token);
        writer.Write("guards-end");
        foreach (string reference in node.MemberReferences) writer.Write(reference);
        writer.Write("members-end");
        foreach (PrimitiveNode child in node.Children) PrimitiveWriteNode(writer, child);
        writer.Write("children-end");
    }

    private static string PrimitiveHash(Action<BinaryWriter> write)
    {
        using (var stream = new MemoryStream())
        {
            using (var writer = new BinaryWriter(stream, Encoding.UTF8, true)) write(writer);
            using (SHA256 sha = SHA256.Create())
                return string.Concat(sha.ComputeHash(stream.GetBuffer(), 0, checked((int)stream.Length)).Select(value => value.ToString("x2", CultureInfo.InvariantCulture)));
        }
    }

    private static void PrimitiveWritePositionBytes(BinaryWriter writer, Position value)
    {
        foreach (double component in PrimitivePositionVector(value)) writer.Write(component);
    }

    private static void PrimitiveWriteOrientationBytes(BinaryWriter writer, Orientation value)
    {
        foreach (Direction direction in new[] { value.XDir(), value.YDir(), value.ZDir() })
            foreach (double component in DirectionVector(direction)) writer.Write(component);
    }

    private static string PrimitiveShape(string type)
    {
        switch ((type ?? "").ToUpperInvariant())
        {
            case "CYLI": case "NCYL": return "CYLI";
            case "BOX": case "NBOX": return "BOX";
            case "CONE": case "NCON": return "CONE";
            case "DISH": return "DISH";
            default: return null;
        }
    }

    private static bool PrimitiveIsNegative(string type)
    {
        return new[] { "NCYL", "NBOX", "NCON" }.Contains((type ?? "").ToUpperInvariant(), StringComparer.Ordinal);
    }

    private static KeyValuePair<string, string>[] PrimitiveShapeFields(string shape)
    {
        switch (shape)
        {
            case "CYLI": return new[] { Pair("DIAM"), Pair("HEIG") };
            case "BOX": return new[] { Pair("XLEN"), Pair("YLEN"), Pair("ZLEN") };
            case "CONE": return new[] { Pair("DTOP"), Pair("DBOT"), Pair("HEIG") };
            case "DISH": return new[] { Pair("DIAM"), Pair("HEIG"), Pair("RADI") };
            default: throw new ArgumentException("Unknown primitive shape " + shape + ".", "shape");
        }
    }

    private static KeyValuePair<string, string> Pair(string value) { return new KeyValuePair<string, string>(value, value); }

    private static void PrimitiveValidateDimensions(string shape, IDictionary<string, double> values)
    {
        string[] allowed = PrimitiveShapeFields(shape).Select(item => item.Key).ToArray();
        if (values.Keys.Any(key => !allowed.Contains(key, StringComparer.Ordinal)))
            throw new ArgumentException("dimensions contains a field outside the " + shape + " shape.");
        foreach (string field in allowed)
            if (!values.ContainsKey(field)) throw new InvalidOperationException("Existing " + field + " is unavailable.");
        foreach (KeyValuePair<string, double> item in values)
            if (double.IsNaN(item.Value) || double.IsInfinity(item.Value)) throw new ArgumentException(item.Key + " must be finite.");
        if (shape == "CONE")
        {
            if (values["DTOP"] < 0 || values["DBOT"] < 0 || values["HEIG"] <= 0 || values["DTOP"] == 0 && values["DBOT"] == 0)
                throw new ArgumentException("CONE/NCON requires DTOP and DBOT >= 0, HEIG > 0, and at least one positive end diameter.");
        }
        else if (shape == "DISH")
        {
            // This is the bounded MCP support policy, not the complete native
            // geometric validity domain. The SDK still validates each setter.
            if (values["DIAM"] <= 0 || values["HEIG"] <= 0 || values["RADI"] < 0)
                throw new ArgumentException("DISH editing requires DIAM > 0, HEIG > 0 and RADI >= 0.");
        }
        else if (values.Values.Any(value => value <= 0))
            throw new ArgumentException(shape + " dimensions must be positive.");
    }

    private static double PrimitiveFinite(double value, string field)
    {
        if (double.IsNaN(value) || double.IsInfinity(value)) throw new InvalidOperationException("Non-finite SDK value for " + field + ".");
        return value;
    }

    private static Dictionary<string, object> PrimitiveStateResult(PrimitiveEditState state)
    {
        var result = new Dictionary<string, object>
        {
            ["complete"] = state.Complete,
            ["partial"] = !state.Complete,
            ["element"] = state.Geometry == null ? null : state.Geometry.ToDictionary()["element"],
            ["shape"] = state.Geometry == null ? null : state.Geometry.Shape,
            ["negative"] = state.Geometry == null ? (object)null : state.Geometry.Negative,
            ["is_negative"] = state.Geometry == null ? (object)null : state.Geometry.Negative,
            ["dimensions"] = state.Geometry == null ? new Dictionary<string, double>() : new Dictionary<string, double>(state.Geometry.Dimensions),
            ["frame"] = state.Geometry == null ? null : state.Geometry.ToDictionary()["frame"],
            ["negative_children"] = state.Geometry == null ? new List<object>() : state.Geometry.Children.Select(child => (object)child.ToDictionary()).ToList(),
            ["target_geometry"] = state.Geometry == null ? null : state.Geometry.ToDictionary(),
            ["negative_owner"] = state.OwnerGeometry == null ? null : state.OwnerGeometry.ToDictionary(),
            ["owner"] = state.Owner == null ? null : ModelIdentity(state.Owner),
            ["owner_member_refnos"] = state.OwnerMembers ?? new string[0],
            ["editable"] = state.Eligible,
            ["edit_reasons"] = state.Reasons.ToArray(),
            ["revision"] = state.Revision,
            ["unit"] = "mm",
            ["read_errors"] = new Dictionary<string, string>(state.Errors)
        };
        return result;
    }

    private static void PrimitivePopulateUpdateResult(Dictionary<string, object> result, PrimitiveEditState state)
    {
        result["editable"] = state.Eligible;
        result["complete"] = state.Complete;
        result["edit_reasons"] = state.Reasons.ToArray();
        result["original_revision"] = state.Revision;
        result["revision"] = state.Revision;
        result["before"] = PrimitiveStateResult(state);
    }

    private static string PrimitiveNumberToken(double value) { return value.ToString("R", CultureInfo.InvariantCulture); }

    private static double[] PrimitivePositionVector(Position value)
    {
        if (value == null) throw new InvalidOperationException("Position is unavailable.");
        return FiniteVector(value.X, value.Y, value.Z);
    }

    private static Dictionary<string, object> PrimitiveOrientationValue(Orientation value)
    {
        return new Dictionary<string, object>
        {
            ["x_axis"] = DirectionVector(value.XDir()), ["y_axis"] = DirectionVector(value.YDir()),
            ["z_axis"] = DirectionVector(value.ZDir())
        };
    }

    private static string PrimitiveOrientationToken(Orientation value)
    {
        return string.Join(";", new[] { value.XDir(), value.YDir(), value.ZDir() }
            .Select(direction => string.Join(",", DirectionVector(direction).Select(PrimitiveNumberToken))));
    }

    private List<PrimitiveUpdateStep> PrimitiveBuildUpdateSteps(PrimitiveEditState original,
        IDictionary<string, double> requestedDimensions, IDictionary<string, double> wantedDimensions,
        ModelEditInput nativeInput, Dictionary<string, object> result)
    {
        ThrowIfNativeFaulted();
        PrimitiveNode node = original.Geometry;
        var changes = new List<PrimitiveUpdateStep>();
        foreach (string field in PrimitiveShapeFields(node.Shape).Select(item => item.Key))
        {
            ThrowIfNativeFaulted();
            if (!requestedDimensions.ContainsKey(field)) continue;
            DbAttribute attribute = node.Attributes[field];
            ModelRequireSettable(node.Element, attribute);
            double beforeMm = node.Dimensions[field];
            double afterMm = wantedDimensions[field];
            if (PrimitiveNumbersMatch(beforeMm, afterMm)) continue;
            DbDouble before = node.NativeDimensions[field];
            DbDouble restore = DbDouble.Create(before);
            if (restore == null || NativeRead(() => restore.Dimension) != NativeRead(() => before.Dimension) ||
                NativeRead(() => restore.Units) != NativeRead(() => before.Units) ||
                !PrimitiveNumbersMatch(NativeRead(() => restore.DatabaseValue), NativeRead(() => before.DatabaseValue)))
                throw new InvalidOperationException("The exact database-unit restoration value could not be prepared for " + field + ".");
            DbDoubleUnits mm = NativeRead(() => DbDoubleUnits.GetUnits("mm", NativeRead(() => before.Dimension)));
            if (mm == null || !mm.Set || NativeRead(() => mm.Dimension) != NativeRead(() => before.Dimension) ||
                NativeRead(() => NativeRead(() => mm.Dimension).StandardDimension) != DbDimension.DIST)
                throw new InvalidOperationException("The SDK could not resolve explicit millimetre units for " + field + ".");
            DbDouble wanted = NativeRead(() => DbDouble.Create(afterMm, mm, NativeRead(() => before.Dimension)));
            if (
                wanted == null || NativeRead(() => wanted.Dimension) != NativeRead(() => before.Dimension) ||
                NativeRead(() => wanted.Units) != mm || !PrimitiveNumbersMatch(NativeRead(() => wanted.ConvertUnits(mm)).Value, afterMm))
                throw new InvalidOperationException("The SDK could not prepare a validated millimetre value for " + field + ".");
            var step = new PrimitiveUpdateStep
            {
                Name = field,
                Apply = delegate { PrimitiveWriteDimension(node.Element, attribute, wanted, field); },
                Rollback = delegate { PrimitiveWriteDimension(node.Element, attribute, restore, "restore " + field); }
            };
            changes.Add(step);
        }

        if (nativeInput != null && nativeInput.Position != null)
        {
            Position before = ModelPosition(PrimitivePositionVector(node.NativePosition));
            Position wanted = ModelPosition(nativeInput.Position);
            if (!ModelEditInput.PositionMatches(PrimitivePositionVector(wanted), PrimitivePositionVector(before)))
            {
                ModelRequireSettable(node.Element, DbAttributeInstance.POS);
                changes.Add(new PrimitiveUpdateStep
                {
                    Name = "POS",
                    Apply = delegate { ModelWritePosition(node.Element, wanted); },
                    Rollback = delegate { ModelWritePosition(node.Element, before); }
                });
            }
        }
        if (nativeInput != null && nativeInput.XAxis != null)
        {
            Orientation before = node.NativeOrientation;
            Orientation wanted = ModelOrientation(nativeInput);
            if (!ModelOrientationsEqual(wanted, before))
            {
                ModelRequireSettable(node.Element, DbAttributeInstance.ORI);
                changes.Add(new PrimitiveUpdateStep
                {
                    Name = "ORI",
                    Apply = delegate { ModelWriteOrientation(node.Element, wanted); },
                    Rollback = delegate { ModelWriteOrientation(node.Element, before); }
                });
            }
        }

        if (node.Shape == "CONE")
        {
            // Keep at least one end positive through tip-switching edits. Apply
            // increases before decreases; reverse rollback then restores safely.
            var endpointChanges = changes.Where(step => step.Name == "DTOP" || step.Name == "DBOT").ToArray();
            var increases = new List<PrimitiveUpdateStep>();
            var decreases = new List<PrimitiveUpdateStep>();
            foreach (PrimitiveUpdateStep step in endpointChanges)
                (wantedDimensions[step.Name] > node.Dimensions[step.Name] ? increases : decreases).Add(step);
            changes = increases.Concat(changes.Where(step => step.Name != "DTOP" && step.Name != "DBOT"))
                .Concat(decreases).ToList();
        }
        // A negative's direct parent owns its boolean cut. Its transform is
        // expressed relative to that parent and cannot target itself/descendants.
        if (node.Negative && nativeInput != null && (nativeInput.Position != null || nativeInput.XAxis != null))
        {
            DbElement requestedFrame = ModelFrame("OWNER", original.Owner);
            ModelRequireStableFrame(node.Element, requestedFrame);
        }
        result["preflight_settable"] = changes.Select(step => step.Name).ToArray();
        return changes;
    }

    private static void PrimitiveWriteDimension(DbElement element, DbAttribute attribute, DbDouble value, string field)
    {
        ThrowIfNativeFaulted();
        if (!NativeRead(() => element.IsAttributeValid(attribute)) || !NativeRead(() => element.IsAttributeSetable(attribute)))
            throw new InvalidOperationException("E3D no longer reports " + field + " as settable.");
        PdmsMessage message;
        ThrowIfNativeFaulted();
        bool success = element.SetAttribute(attribute, new DbQualifier(), value, out message);
        ThrowIfNativeFaulted();
        ModelRequireSetter(success, message, field);
    }

    private void PrimitiveRequireUpdateReadback(PrimitiveEditState original, PrimitiveEditState actual,
        IDictionary<string, double> requestedDimensions, IDictionary<string, double> wantedDimensions,
        ModelEditInput input, DbElement requestedFrame, ModelEditInput nativeInput)
    {
        if (!actual.Complete || !actual.Eligible)
            throw new InvalidOperationException("Updated primitive geometry or its guarded negative subtree could not be read completely.");
        PrimitiveNode expectedNode = original.Geometry;
        PrimitiveNode actualNode = actual.Geometry;
        if (expectedNode.Reference != actualNode.Reference || expectedNode.Shape != actualNode.Shape || expectedNode.Negative != actualNode.Negative)
            throw new InvalidOperationException("Primitive identity or shape changed during update.");
        if (!actual.AncestorSignature.SequenceEqual(original.AncestorSignature, StringComparer.Ordinal))
            throw new InvalidOperationException("Primitive ancestor frame, links, or rule state changed during update.");
        if (!expectedNode.GuardSignature.SequenceEqual(actualNode.GuardSignature, StringComparer.Ordinal) ||
            expectedNode.StorageFrameReference != actualNode.StorageFrameReference)
            throw new InvalidOperationException("Primitive links, rule dependencies, or storage frame changed during update.");
        if (input.Position == null && !PrimitivePositionMatches(expectedNode.NativePosition, actualNode.NativePosition))
            throw new InvalidOperationException("Unrequested primitive POS changed during a dimension/orientation update.");
        if (input.XAxis == null && !ModelOrientationsEqual(expectedNode.NativeOrientation, actualNode.NativeOrientation))
            throw new InvalidOperationException("Unrequested primitive ORI changed during a dimension/position update.");
        foreach (KeyValuePair<string, double> wanted in wantedDimensions)
            if (!actualNode.Dimensions.ContainsKey(wanted.Key) || !PrimitiveNumbersMatch(wanted.Value, actualNode.Dimensions[wanted.Key]))
                throw new InvalidOperationException("" + wanted.Key + " readback did not match the requested millimetre value.");
        if (!actualNode.MemberReferences.SequenceEqual(expectedNode.MemberReferences, StringComparer.Ordinal) ||
            actualNode.Children.Count != expectedNode.Children.Count)
            throw new InvalidOperationException("Negative child membership changed during primitive update.");
        for (int index = 0; index < expectedNode.Children.Count; index++)
        {
            ThrowIfNativeFaulted();
            PrimitiveNode beforeChild = expectedNode.Children[index];
            PrimitiveNode afterChild = actualNode.Children[index];
            if (beforeChild.Reference != afterChild.Reference || beforeChild.Shape != afterChild.Shape ||
                !beforeChild.Dimensions.Keys.OrderBy(key => key).SequenceEqual(afterChild.Dimensions.Keys.OrderBy(key => key)))
                throw new InvalidOperationException("A negative child identity or shape changed during primitive update.");
            if (!beforeChild.GuardSignature.SequenceEqual(afterChild.GuardSignature, StringComparer.Ordinal) ||
                beforeChild.StorageFrameReference != afterChild.StorageFrameReference)
                throw new InvalidOperationException("A retained negative child's links, rules, or storage frame changed: " + beforeChild.Reference + ".");
            foreach (KeyValuePair<string, double> dimension in beforeChild.Dimensions)
                if (!PrimitiveNumbersMatch(dimension.Value, afterChild.Dimensions[dimension.Key]))
                    throw new InvalidOperationException("A supported negative child dimension changed: " + beforeChild.Reference + " " + dimension.Key + ".");
            if (!PrimitivePositionMatches(beforeChild.NativePosition, afterChild.NativePosition) ||
                !ModelOrientationsEqual(beforeChild.NativeOrientation, afterChild.NativeOrientation) ||
                !PrimitivePositionMatches(beforeChild.LocalToOwnerPosition, afterChild.LocalToOwnerPosition) ||
                !ModelOrientationsEqual(beforeChild.LocalToOwnerOrientation, afterChild.LocalToOwnerOrientation))
                throw new InvalidOperationException("A negative child native/owner-relative transform changed: " + beforeChild.Reference + ".");
            double[] expectedWorldPosition = PrimitiveComposePosition(actualNode.WorldPosition, actualNode.WorldOrientation,
                beforeChild.LocalToOwnerPosition);
            Orientation expectedWorldOrientation = PrimitiveComposeOrientation(actualNode.WorldOrientation, beforeChild.LocalToOwnerOrientation);
            if (!PrimitiveVectorMatches(expectedWorldPosition, PrimitivePositionVector(afterChild.WorldPosition), 0.001) ||
                !ModelOrientationsEqual(expectedWorldOrientation, afterChild.WorldOrientation))
                throw new InvalidOperationException("A negative child WORLD transform does not follow the updated positive parent while retaining its local frame: " + beforeChild.Reference + ".");
        }
        if (original.OwnerGeometry != null)
            PrimitiveRequireOwnerSubtreeReadback(original.OwnerGeometry, actual.OwnerGeometry, original.TargetReference,
                requestedDimensions, wantedDimensions);
        if (input.Position != null)
        {
            Position readback = original.Target.GetPosition(DbAttributeInstance.POS, new DbQualifier { wrtQualifier = requestedFrame });
            if (readback == null || !ModelEditInput.PositionMatches(input.Position, PrimitivePositionVector(readback)))
                throw new InvalidOperationException("POS readback did not match the submitted position in the requested frame.");
        }
        if (input.XAxis != null)
        {
            Orientation readback = original.Target.GetOrientation(DbAttributeInstance.ORI, new DbQualifier { wrtQualifier = requestedFrame });
            if (readback == null || !input.OrientationMatches(DirectionVector(readback.XDir()), DirectionVector(readback.YDir()), DirectionVector(readback.ZDir())))
                throw new InvalidOperationException("ORI readback did not match the submitted axes in the requested frame.");
        }
        if (nativeInput != null && input.Position != null &&
            !ModelEditInput.PositionMatches(nativeInput.Position, PrimitivePositionVector(actualNode.NativePosition)))
            throw new InvalidOperationException("Native POS readback did not match the converted storage-frame input.");
        if (nativeInput != null && input.XAxis != null)
        {
            Orientation wanted = ModelOrientation(nativeInput);
            if (!ModelOrientationsEqual(wanted, actualNode.NativeOrientation))
                throw new InvalidOperationException("Native ORI readback did not match the converted storage-frame input.");
        }
        if (actual.OwnerReference != original.OwnerReference ||
            !actual.OwnerMembers.SequenceEqual(original.OwnerMembers, StringComparer.Ordinal))
            throw new InvalidOperationException("Primitive owner or ordered owner membership changed during update.");
    }

    private static void PrimitiveRequireOwnerSubtreeReadback(PrimitiveNode before, PrimitiveNode after, string editedReference,
        IDictionary<string, double> requestedDimensions, IDictionary<string, double> wantedDimensions)
    {
        if (before == null || after == null || before.Reference != after.Reference || before.Shape != after.Shape ||
            before.Negative != after.Negative || !before.MemberReferences.SequenceEqual(after.MemberReferences, StringComparer.Ordinal) ||
            before.Children.Count != after.Children.Count)
            throw new InvalidOperationException("Negative owner or sibling membership changed while editing its cut.");
        if (!before.GuardSignature.SequenceEqual(after.GuardSignature, StringComparer.Ordinal) ||
            before.StorageFrameReference != after.StorageFrameReference)
            throw new InvalidOperationException("Negative owner/sibling link, rule or storage-frame state changed during a child edit.");
        bool isEdited = before.Reference == editedReference;
        if (!isEdited)
        {
            foreach (KeyValuePair<string, double> dimension in before.Dimensions)
                if (!after.Dimensions.ContainsKey(dimension.Key) || !PrimitiveNumbersMatch(dimension.Value, after.Dimensions[dimension.Key]))
                    throw new InvalidOperationException("The negative owner's or sibling's " + dimension.Key + " changed during a child edit: " + before.Reference + ".");
            if (!PrimitivePositionMatches(before.NativePosition, after.NativePosition) ||
                !ModelOrientationsEqual(before.NativeOrientation, after.NativeOrientation))
                throw new InvalidOperationException("The negative owner or sibling native transform changed during a child edit: " + before.Reference + ".");
        }
        else
        {
            foreach (KeyValuePair<string, double> dimension in wantedDimensions)
                if (!after.Dimensions.ContainsKey(dimension.Key) || !PrimitiveNumbersMatch(dimension.Value, after.Dimensions[dimension.Key]))
                    throw new InvalidOperationException("Edited negative dimensions did not read back on its owner membership: " + dimension.Key + ".");
        }
        for (int index = 0; index < before.Children.Count; index++)
        {
            ThrowIfNativeFaulted();
            PrimitiveNode oldChild = before.Children[index];
            PrimitiveNode newChild = after.Children[index];
            PrimitiveRequireOwnerSubtreeReadback(oldChild, newChild, editedReference, requestedDimensions, wantedDimensions);
            Position expectedLocalPosition = oldChild.LocalToOwnerPosition;
            Orientation expectedLocalOrientation = oldChild.LocalToOwnerOrientation;
            if (oldChild.Reference != editedReference &&
                (!PrimitivePositionMatches(expectedLocalPosition, newChild.LocalToOwnerPosition) ||
                 !ModelOrientationsEqual(expectedLocalOrientation, newChild.LocalToOwnerOrientation)))
                throw new InvalidOperationException("An unchanged negative sibling's owner-relative frame changed: " + oldChild.Reference + ".");
            if (!PrimitiveVectorMatches(PrimitiveComposePosition(after.WorldPosition, after.WorldOrientation,
                    newChild.LocalToOwnerPosition), PrimitivePositionVector(newChild.WorldPosition), 0.001) ||
                !ModelOrientationsEqual(PrimitiveComposeOrientation(after.WorldOrientation, newChild.LocalToOwnerOrientation),
                    newChild.WorldOrientation))
                throw new InvalidOperationException("A negative sibling WORLD frame does not follow its reported owner: " + newChild.Reference + ".");
        }
    }

    private static double[] PrimitiveComposePosition(Position parentPosition, Orientation parentOrientation, Position localPosition)
    {
        double[] origin = PrimitivePositionVector(parentPosition);
        double[] local = PrimitivePositionVector(localPosition);
        double[][] axes = new[] { DirectionVector(parentOrientation.XDir()), DirectionVector(parentOrientation.YDir()), DirectionVector(parentOrientation.ZDir()) };
        return new[]
        {
            origin[0] + local[0] * axes[0][0] + local[1] * axes[1][0] + local[2] * axes[2][0],
            origin[1] + local[0] * axes[0][1] + local[1] * axes[1][1] + local[2] * axes[2][1],
            origin[2] + local[0] * axes[0][2] + local[1] * axes[1][2] + local[2] * axes[2][2]
        };
    }

    private static Orientation PrimitiveComposeOrientation(Orientation parent, Orientation local)
    {
        double[][] p = new[] { DirectionVector(parent.XDir()), DirectionVector(parent.YDir()), DirectionVector(parent.ZDir()) };
        double[][] l = new[] { DirectionVector(local.XDir()), DirectionVector(local.YDir()), DirectionVector(local.ZDir()) };
        double[] x = PrimitiveMultiplyAxes(p, l[0]);
        double[] y = PrimitiveMultiplyAxes(p, l[1]);
        return ModelOrientation(new ModelEditInput(null, x, y, "OWNER", false));
    }

    private static double[] PrimitiveMultiplyAxes(double[][] axes, double[] local)
    {
        return new[]
        {
            axes[0][0] * local[0] + axes[1][0] * local[1] + axes[2][0] * local[2],
            axes[0][1] * local[0] + axes[1][1] * local[1] + axes[2][1] * local[2],
            axes[0][2] * local[0] + axes[1][2] * local[1] + axes[2][2] * local[2]
        };
    }

    private static bool PrimitivePositionMatches(Position left, Position right)
    {
        return left != null && right != null && PrimitiveVectorMatches(PrimitivePositionVector(left), PrimitivePositionVector(right), 0.000001);
    }

    private static bool PrimitiveVectorMatches(double[] left, double[] right, double tolerance)
    {
        return left != null && right != null && left.Length == right.Length &&
            left.Zip(right, (a, b) => !double.IsNaN(a) && !double.IsInfinity(a) && Math.Abs(a - b) <= tolerance).All(item => item);
    }

    private static bool PrimitiveNumbersMatch(double left, double right)
    {
        return !double.IsNaN(left) && !double.IsInfinity(left) && !double.IsNaN(right) && !double.IsInfinity(right) &&
            Math.Abs(left - right) <= Math.Max(0.000001, Math.Max(Math.Abs(left), Math.Abs(right)) * 0.0000000001);
    }

    private void PrimitiveCaptureCeAfter(Dictionary<string, object> result)
    {
        ThrowIfNativeFaulted();
        try { result["ce_after"] = ModelCurrentReference(); ModelCeUnchanged(result); }
        catch (Exception ex) { ThrowIfNativeFaulted(); result["ce_after"] = null; result["ce_unchanged"] = null; result["ce_read_error"] = ex.Message; }
    }
}
