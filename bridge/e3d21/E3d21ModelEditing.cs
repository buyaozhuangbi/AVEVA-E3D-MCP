using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core.Geometry;
using Aveva.Core.Utilities.Messaging;

namespace E3d21AiBridge;

public sealed partial class E3d21Context : IRevisionedTransformContext
{
    private const int ModelMembershipVerificationLimit = 10000;
    private readonly string createRevisionSession = Guid.NewGuid().ToString("N");

    private sealed class CreateState
    {
        internal DbElement Parent, Frame, StorageFrame;
        internal DbElement World;
        internal DbElementType Type;
        internal string ParentReference, ParentRevision, Revision;
        internal string[] ParentMembers;
        internal CreateMemberIdentity[] ParentMemberIdentities;
        internal List<ModelInitialAttribute> InitialAttributes;
        internal ModelEditInput NativeInput;
        internal Position NativePosition;
        internal Orientation NativeOrientation;
        internal bool CreatableNow;
        internal CreateMdbIdentity MdbIdentity;
    }

    private sealed class CreateDatabaseIdentity
    {
        internal Db Database;
        internal int Number, ExtractNumber;
        internal string Name, Type;
    }

    private sealed class CreateMdbIdentity
    {
        internal readonly List<CreateDatabaseIdentity> Databases = new List<CreateDatabaseIdentity>();
        internal readonly Dictionary<string, CreateDatabaseIdentity> ByKey =
            new Dictionary<string, CreateDatabaseIdentity>(StringComparer.Ordinal);
    }

    private CreateState ModelCreateReadState(string parentTarget, string requestedType, string requestedName,
        IDictionary<string, string> submitted, ModelEditInput input)
    {
        ThrowIfNativeFaulted();
        var state = new CreateState { Parent = ModelRequireElement(parentTarget) };
        if (state.Parent.DbType != DbType.Design)
            throw new InvalidOperationException("parent must be in a Design database.");
        state.ParentReference = ModelReference(state.Parent.Ref);
        state.Type = DbElementType.GetElementType(requestedType);
        if (state.Type == null || !state.Type.IsValid || !state.Type.DatabaseTypes().Contains((int)DbType.Design))
            throw new InvalidOperationException("Unknown or non-Design element type: " + requestedType);
        state.ParentMemberIdentities = ModelCreateMemberIdentities(state.Parent);
        state.ParentMembers = state.ParentMemberIdentities.Select(item => item.Reference).ToArray();
        state.CreatableNow = state.Parent.IsCreatable(state.Type);
        state.World = ModelFrame("WORLD", state.Parent);
        state.Frame = ModelFrame(input.Wrt, state.Parent);
        state.InitialAttributes = ModelInitialAttributes(state.Type, submitted);
        if (requestedName != null) ModelRequireTypeAttribute(state.Type, DbAttributeInstance.NAME);
        if (input.Position != null) ModelRequireTypeAttribute(state.Type, DbAttributeInstance.POS);
        if (input.XAxis != null) ModelRequireTypeAttribute(state.Type, DbAttributeInstance.ORI);
        state.NativeInput = input;
        if (input.Position != null || input.XAxis != null)
        {
            state.StorageFrame = ModelStorageFrame(state.Parent);
            state.NativeInput = ModelStorageTransform(state.StorageFrame, state.Frame).ToNative(input);
        }
        state.NativePosition = ModelPosition(state.NativeInput.Position);
        state.NativeOrientation = ModelOrientation(state.NativeInput);

        MDB mdb = CurrentMdb();
        Project project = Project.CurrentProject;
        if (mdb == null || project == null)
            throw new InvalidOperationException("Project/MDB context is unavailable.");
        state.MdbIdentity = ModelCreateReadMdbIdentity(mdb);
        CreateDatabaseIdentity firstDesign = state.MdbIdentity.Databases.FirstOrDefault(item =>
            item.Type == DbType.Design.ToString());
        if (firstDesign == null)
            throw new InvalidOperationException("The current MDB has no verified Design database.");
        string worldReference = ModelReference(state.World.Ref);
        if (worldReference != ModelCreateDatabaseWorld(firstDesign, state.MdbIdentity))
            throw new InvalidOperationException("The selected Design WORLD does not match the first Design database in the current MDB.");
        ModelCreateRequireDatabase(state.World, state.MdbIdentity, firstDesign);
        state.ParentRevision = PrimitiveHash(writer =>
        {
            writer.Write("native-element-create-parent-v2");
            writer.Write(createRevisionSession); writer.Write(project.Name); writer.Write(mdb.Name);
            writer.Write(state.MdbIdentity.Databases.Count);
            foreach (CreateDatabaseIdentity database in state.MdbIdentity.Databases)
                ModelCreateWriteDatabaseIdentity(writer, database);
            writer.Write(state.ParentReference);
            writer.Write(state.Parent.GetElementType().ShortName);
            writer.Write(state.Parent.GetActualType().ShortName);
            ModelCreateWriteNullableString(writer,
                state.Parent.IsAttributeValid(DbAttributeInstance.NAME)
                    ? state.Parent.GetString(DbAttributeInstance.NAME) : null);
            writer.Write(state.ParentMemberIdentities.Length);
            foreach (CreateMemberIdentity member in state.ParentMemberIdentities)
            {
                writer.Write(member.Reference); writer.Write(member.Type); writer.Write(member.ActualType);
                ModelCreateWriteNullableString(writer, member.Name);
            }
            writer.Write(state.CreatableNow);
            ModelCreateWriteAncestorChain(writer, state.Parent, state.World, worldReference, state.MdbIdentity);
            ModelCreateWriteAncestorChain(writer, state.Frame, state.World, worldReference, state.MdbIdentity);
            writer.Write(ModelReference(state.Frame.Ref));
            writer.Write(state.StorageFrame != null);
            if (state.StorageFrame != null)
            {
                writer.Write(ModelReference(state.StorageFrame.Ref));
                ModelCreateWriteAncestorChain(writer, state.StorageFrame, state.World, worldReference, state.MdbIdentity);
            }
        });
        var requestAttributes = state.InitialAttributes.ToDictionary(item => item.Canonical, item => item.Value,
            StringComparer.Ordinal);
        state.Revision = ModelCreateRequestRevision(state.ParentRevision, requestedType,
            state.Type.Name, state.Type.ShortName, requestedName, requestAttributes, input, state.NativeInput);
        return state;
    }

    private static string ModelCreateRequestRevision(string parentRevision, string requestedType,
        string resolvedTypeName, string resolvedTypeShortName, string requestedName,
        IDictionary<string, object> attributes, ModelEditInput input, ModelEditInput nativeInput)
    {
        return PrimitiveHash(writer =>
        {
            writer.Write("native-element-create-request-v1");
            writer.Write(parentRevision); writer.Write(requestedType.ToUpperInvariant());
            writer.Write(resolvedTypeName); writer.Write(resolvedTypeShortName);
            ModelCreateWriteNullableString(writer, requestedName);
            writer.Write(input.Wrt);
            ModelCreateWriteVector(writer, input.Position);
            ModelCreateWriteVector(writer, input.XAxis);
            ModelCreateWriteVector(writer, input.YAxis);
            ModelCreateWriteVector(writer, nativeInput.Position);
            ModelCreateWriteVector(writer, nativeInput.XAxis);
            ModelCreateWriteVector(writer, nativeInput.YAxis);
            foreach (KeyValuePair<string, object> item in attributes.OrderBy(value => value.Key, StringComparer.Ordinal))
            {
                writer.Write(item.Key);
                if (item.Value is string)
                {
                    writer.Write("string"); writer.Write((string)item.Value);
                }
                else
                {
                    writer.Write("double"); writer.Write((double)item.Value);
                }
            }
            writer.Write("attributes-end");
        });
    }

    private static bool ModelCreateRevisionConflict(string expected, string previewRevision, string immediateRevision)
    {
        return expected == null || !string.Equals(expected, previewRevision, StringComparison.Ordinal) ||
            !string.Equals(expected, immediateRevision, StringComparison.Ordinal);
    }

    private static CreateMemberIdentity[] ModelCreateMemberIdentities(DbElement parent)
    {
        MemberPage<DbElement> page = MemberPage<DbElement>.Read(DirectSiblings(parent, null),
            ModelMembershipVerificationLimit,
            element => ModelReference(element.Ref) + "|" + element.GetElementType().ShortName,
            element => ModelReference(element.Ref), element => element.GetElementType().ShortName, 0);
        ThrowIfNativeFaulted();
        if (page.HasMore != false || page.ReadErrors.Count > 0)
            throw new InvalidOperationException("Cannot fingerprint the complete parent membership within " +
                ModelMembershipVerificationLimit + " direct members. " + string.Join(" ", page.ReadErrors));
        return page.Items.Select(element => new CreateMemberIdentity
        {
            Reference = ModelReference(element.Ref),
            Type = element.GetElementType().ShortName,
            ActualType = element.GetActualType().ShortName,
            Name = element.IsAttributeValid(DbAttributeInstance.NAME)
                ? element.GetString(DbAttributeInstance.NAME) : null
        }).ToArray();
    }

    private static string ModelCreateDatabaseKey(CreateDatabaseIdentity database)
    {
        return database.Number.ToString(CultureInfo.InvariantCulture) + ":" +
            database.ExtractNumber.ToString(CultureInfo.InvariantCulture);
    }

    private static CreateDatabaseIdentity ModelCreateReadDatabaseIdentity(Db database)
    {
        ThrowIfNativeFaulted();
        if (database == null) throw new InvalidOperationException("A create database identity is unavailable.");
        var identity = new CreateDatabaseIdentity
        {
            Database = database, Number = NativeRead(() => database.Number),
            ExtractNumber = NativeRead(() => database.ExtractNumber),
            Name = NativeRead(() => database.Name), Type = NativeRead(() => database.Type).ToString()
        };
        if (identity.Number < 1 || identity.ExtractNumber < 0 || string.IsNullOrWhiteSpace(identity.Name) ||
            !Enum.IsDefined(typeof(DbType), NativeRead(() => database.Type)))
            throw new InvalidOperationException("A create database identity is incomplete or invalid.");
        return identity;
    }

    private static CreateMdbIdentity ModelCreateReadMdbIdentity(MDB mdb)
    {
        Db[] databases = NativeRead(() => mdb.GetDBArray());
        if (databases == null || databases.Length < 1 || databases.Length > ModelMembershipVerificationLimit)
            throw new InvalidOperationException("Cannot verify the complete current MDB database membership within 10000 entries.");
        var identity = new CreateMdbIdentity();
        foreach (Db database in databases)
        {
            CreateDatabaseIdentity item = ModelCreateReadDatabaseIdentity(database);
            string key = ModelCreateDatabaseKey(item);
            if (identity.ByKey.ContainsKey(key))
                throw new InvalidOperationException("The current MDB has duplicate database/extract identities.");
            identity.Databases.Add(item); identity.ByKey.Add(key, item);
        }
        return identity;
    }

    private static void ModelCreateWriteDatabaseIdentity(BinaryWriter writer, CreateDatabaseIdentity database)
    {
        writer.Write(database.Number); writer.Write(database.ExtractNumber);
        writer.Write(database.Name); writer.Write(database.Type);
    }

    private static CreateDatabaseIdentity ModelCreateRequireDatabase(DbElement element,
        CreateMdbIdentity mdb, CreateDatabaseIdentity expected)
    {
        CreateDatabaseIdentity observed = ModelCreateReadDatabaseIdentity(NativeRead(() => element.Db));
        CreateDatabaseIdentity member;
        if (!mdb.ByKey.TryGetValue(ModelCreateDatabaseKey(observed), out member) ||
            observed.Name != member.Name || observed.Type != member.Type || observed.Type != DbType.Design.ToString())
            throw new InvalidOperationException("A create parent/frame database is not a verified Design member of the current MDB.");
        // E3D21 resolves DbElement.Db through CurrentMDB.GetDB(number). Its extract
        // is not independent evidence when that number occurs more than once.
        if (mdb.Databases.Count(item => item.Number == observed.Number) != 1)
            throw new InvalidOperationException("A referenced create database number has ambiguous extracts in the current MDB.");
        if (expected != null && ModelCreateDatabaseKey(member) != ModelCreateDatabaseKey(expected))
            throw new InvalidOperationException("A non-WORLD create ancestor crosses database/extract identity.");
        string observedWorld = ModelCreateWorldReference(NativeRead(() => observed.Database.World));
        string memberWorld = ModelCreateWorldReference(NativeRead(() => member.Database.World));
        if (observedWorld != memberWorld)
            throw new InvalidOperationException("A create database wrapper disagrees with the current MDB database WORLD.");
        return member;
    }

    private static string ModelCreateWorldReference(DbElement world)
    {
        if (!IsUsable(world) || NativeRead(() => world.DbType) != DbType.Design)
            throw new InvalidOperationException("A create database WORLD is unavailable or outside Design.");
        int[] reference = NativeRead(() => world.Ref);
        DbElementType type = NativeRead(() => world.GetElementType());
        if (!ModelEditInput.IsReferencePair(reference) || reference[1] != 0 || type == null)
            throw new InvalidOperationException("A create database WORLD lacks a valid raw WORLD reference and type.");
        string shortName = NativeRead(() => type.ShortName);
        if (!(shortName == "WORL" || shortName == "WORLD"))
            throw new InvalidOperationException("A create database WORLD lacks a valid raw WORLD reference and type.");
        // Local E3D21 observations returned IsWorld=true for SITE and false
        // for physical WORL; the flag's exact native semantics remain unknown.
        // Match the explicit type and exact owning database World reference.
        Db database = NativeRead(() => world.Db);
        if (database == null)
            throw new InvalidOperationException("A create database WORLD has no verified owning database.");
        DbElement databaseWorld = NativeRead(() => database.World);
        if (!IsUsable(databaseWorld) || NativeRead(() => databaseWorld.DbType) != DbType.Design)
            throw new InvalidOperationException("A create database WORLD is unavailable from its owning database.");
        int[] databaseReference = NativeRead(() => databaseWorld.Ref);
        if (!ModelEditInput.IsReferencePair(databaseReference) || databaseReference[1] != 0 ||
            !reference.SequenceEqual(databaseReference))
            throw new InvalidOperationException("A create WORLD reference does not match its exact owning database WORLD.");
        return ModelReference(reference);
    }

    private static string ModelCreateDatabaseWorld(CreateDatabaseIdentity database, CreateMdbIdentity mdb)
    {
        DbElement world = NativeRead(() => database.Database.World);
        string reference = ModelCreateWorldReference(world);
        ModelCreateRequireDatabase(world, mdb, database);
        return reference;
    }

    private static void ModelCreateWriteAncestorChain(BinaryWriter writer, DbElement start, DbElement world,
        string worldReference, CreateMdbIdentity mdb)
    {
        var seen = new HashSet<string>(StringComparer.Ordinal);
        DbElement current = start;
        CreateDatabaseIdentity startDatabase = ModelCreateRequireDatabase(start, mdb, null);
        CreateDatabaseIdentity worldDatabase = ModelCreateRequireDatabase(world, mdb, null);
        string physicalWorld = ModelCreateDatabaseWorld(startDatabase, mdb);
        ModelCreateWriteDatabaseIdentity(writer, startDatabase);
        writer.Write(physicalWorld); writer.Write(worldReference);
        var worldQualifier = new DbQualifier();
        NativeWrite(() => worldQualifier.wrtQualifier = world);
        for (int depth = 0; depth < 1024; depth++)
        {
            ThrowIfNativeFaulted();
            if (!IsUsable(current) || NativeRead(() => current.DbType) != DbType.Design)
                throw new InvalidOperationException("A create parent/frame ancestor is invalid or outside Design.");
            string reference = ModelReference(NativeRead(() => current.Ref));
            if (!seen.Add(reference))
                throw new InvalidOperationException("A cycle was found in the create parent/frame ancestor chain.");
            DbElementType baseType = NativeRead(() => current.GetElementType());
            DbElementType actualType = NativeRead(() => current.GetActualType());
            writer.Write(reference);
            string baseShortName = NativeRead(() => baseType.ShortName);
            writer.Write(baseShortName);
            writer.Write(NativeRead(() => actualType.ShortName));
            if (baseShortName == "WORL" || baseShortName == "WORLD")
            {
                string endpoint = ModelCreateWorldReference(current);
                CreateDatabaseIdentity endpointDatabase = ModelCreateRequireDatabase(current, mdb, null);
                bool selectedWorld = endpoint == worldReference &&
                    ModelCreateDatabaseKey(endpointDatabase) == ModelCreateDatabaseKey(worldDatabase);
                bool databaseWorld = endpoint == physicalWorld &&
                    ModelCreateDatabaseKey(endpointDatabase) == ModelCreateDatabaseKey(startDatabase);
                if (!selectedWorld && !databaseWorld)
                    throw new InvalidOperationException("The create parent/frame ancestry reached a WORLD outside its verified MDB database.");
                ModelCreateWriteDatabaseIdentity(writer, endpointDatabase);
                writer.Write("verified-design-world");
                return;
            }
            ModelCreateRequireDatabase(current, mdb, startDatabase);
            bool hasPosition = NativeRead(() => current.IsAttributeValid(DbAttributeInstance.POS));
            bool hasOrientation = NativeRead(() => current.IsAttributeValid(DbAttributeInstance.ORI));
            writer.Write(hasPosition); writer.Write(hasOrientation);
            if (hasPosition)
            {
                Position native = NativeRead(() => current.GetPosition(DbAttributeInstance.POS));
                Position inWorld = NativeRead(() => current.GetPosition(DbAttributeInstance.POS, worldQualifier));
                if (native == null || inWorld == null)
                    throw new InvalidOperationException("A parent/frame ancestor position could not be read in native and WORLD frames.");
                ModelCreateWriteVector(writer, FiniteVector(NativeRead(() => native.X),
                    NativeRead(() => native.Y), NativeRead(() => native.Z)));
                ModelCreateWriteVector(writer, FiniteVector(NativeRead(() => inWorld.X),
                    NativeRead(() => inWorld.Y), NativeRead(() => inWorld.Z)));
            }
            if (hasOrientation)
            {
                Orientation native = NativeRead(() => current.GetOrientation(DbAttributeInstance.ORI));
                Orientation inWorld = NativeRead(() => current.GetOrientation(DbAttributeInstance.ORI, worldQualifier));
                if (native == null || inWorld == null)
                    throw new InvalidOperationException("A parent/frame ancestor orientation could not be read in native and WORLD frames.");
                ModelCreateWriteOrientation(writer, native);
                ModelCreateWriteOrientation(writer, inWorld);
            }
            DbAttribute[] rules = NativeRead(() => current.GetAttributesWithRules());
            if (rules == null)
                throw new InvalidOperationException("A create parent/frame ancestor rule list is unreadable.");
            string[] names = rules.Select(attribute => NativeRead(() => attribute.ShortName))
                .OrderBy(value => value, StringComparer.Ordinal).ToArray();
            writer.Write(names.Length);
            foreach (string name in names) writer.Write(name);
            current = NativeRead(() => current.Owner);
        }
        throw new InvalidOperationException("Create parent/frame ancestry exceeds 1024 elements before reaching WORLD.");
    }

    private static void ModelCreateWriteVector(BinaryWriter writer, double[] vector)
    {
        writer.Write(vector != null);
        if (vector != null)
        {
            foreach (double value in vector) writer.Write(ModelFinite(value));
        }
    }

    private static void ModelCreateWriteOrientation(BinaryWriter writer, Orientation value)
    {
        writer.Write(value != null);
        if (value != null)
            foreach (Direction direction in new[]
            {
                NativeRead(() => value.XDir()), NativeRead(() => value.YDir()), NativeRead(() => value.ZDir())
            })
                ModelCreateWriteVector(writer, DirectionVector(direction));
    }

    private static void ModelCreateWriteNullableString(BinaryWriter writer, string value)
    {
        writer.Write(value != null);
        if (value != null) writer.Write(value);
    }

    private static void ModelCreatePopulateResult(Dictionary<string, object> result, CreateState state,
        string requestedName, IDictionary<string, string> submitted, ModelEditInput input)
    {
        result["parent"] = ModelIdentity(state.Parent);
        result["type"] = state.Type.Name;
        result["frame"] = ModelFrameIdentity(state.Frame, input.Wrt);
        result["submitted"] = new Dictionary<string, object>
        {
            ["name"] = requestedName, ["attributes"] = submitted,
            ["position"] = input.Position,
            ["orientation"] = ModelSubmittedOrientation(input)
        };
        result["creatable_now"] = state.CreatableNow;
        result["existing_parent_member_count"] = state.ParentMembers.Length;
        result["parent_revision"] = state.ParentRevision;
        result["revision"] = state.Revision;
    }

    private sealed class CreateMemberIdentity
    {
        internal string Reference, Type, ActualType, Name;
    }

    public object CreateElement(string parentRef, string typeName, string name,
        IDictionary<string, string> attributes, double[] position, double[] xAxis, double[] yAxis,
        string wrt, string expectedRevision, bool dryRun)
    {
        string parentTarget = ModelEditInput.ExplicitReference(parentRef, "parentRef");
        string requestedType = ModelEditInput.TypeName(typeName);
        string requestedName = ModelEditInput.ElementName(name);
        ModelEditInput input = new ModelEditInput(position, xAxis, yAxis, wrt, false);
        string expected = expectedRevision == null ? null : expectedRevision.Trim().ToLowerInvariant();
        if (expected != null && (expected.Length != 64 || expected.Any(c => !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f'))))
            throw new ArgumentException("expected_revision must contain exactly 64 hexadecimal characters.", "expectedRevision");
        if (!dryRun && expected == null)
            throw new ArgumentException("Execution requires expected_revision from a current create preview.", "expectedRevision");
        if (attributes != null && attributes.Count > 20)
            throw new ArgumentException("attributes may contain at most 20 entries.", "attributes");
        var submitted = attributes == null ? new Dictionary<string, string>() : new Dictionary<string, string>(attributes);
        string command = "database-create " + parentTarget + " " + requestedType;
        if (dryRun ? IsCommandActive() : !TryBeginCommand(command)) return BusyResult(command);
        try
        {
            return OnUi(delegate
            {
                var result = ModelEditResult(dryRun);
                DbElement created = null;
                int[] createdReference = null;
                string cleanupParentReference = null;
                string[] previousReferences = null;
                CreateState state = null;
                bool createAttempted = false;
                result["revision_conflict"] = false;
                result["creation_attempted"] = false;
                result["creation_result_unknown"] = false;
                result["outcome_uncertain"] = false;
                result["retry_safe"] = true;
                result["cleanup_attempted"] = false;
                result["cleanup_succeeded"] = null;
                result["residual_refno"] = null;
                result["validation_scope"] = "Input, Design type, legal parent/child relationship, current parent membership, ancestor/requested/storage frames and type attribute support. New-element settable state and further name/value/database constraints are checked only during execution.";
                result["revision_scope"] = "E3D bridge session/project/MDB, explicit parent identity/type and ordered direct child identities/types/names, parent IsCreatable state, parent and requested-frame ancestor identities/owner links/native and WORLD transforms/rule names, storage frame, requested type/name/attributes/WRT/transform and converted native transform. This state comparison is not a database lock.";
                try
                {
                    result["ce_before"] = ModelCurrentReference();
                    state = ModelCreateReadState(parentTarget, requestedType, requestedName, submitted, input);
                    ModelCreatePopulateResult(result, state, requestedName, submitted, input);
                    if (expected != null && ModelCreateRevisionConflict(expected, state.Revision, state.Revision))
                    {
                        result["revision_conflict"] = true;
                        throw new InvalidOperationException("expected_revision does not match the current parent, frame or create request. Preview again before creating.");
                    }
                    if (!state.CreatableNow)
                        throw new InvalidOperationException("E3D does not allow creating " + state.Type.Name + " under this parent now.");
                    result["submitted"] = new Dictionary<string, object>
                    {
                        ["name"] = requestedName, ["attributes"] = submitted,
                        ["position"] = input.Position,
                        ["orientation"] = ModelSubmittedOrientation(input)
                    };
                    if (dryRun)
                    {
                        result["ce_after"] = ModelCurrentReference();
                        ModelCeUnchanged(result);
                        result["success"] = true;
                        return result;
                    }

                    CreateState immediate = ModelCreateReadState(parentTarget, requestedType, requestedName, submitted, input);
                    if (ModelCreateRevisionConflict(expected, state.Revision, immediate.Revision))
                    {
                        result["revision_conflict"] = true;
                        throw new InvalidOperationException("Create state changed during preflight; no creation was attempted.");
                    }
                    if (!immediate.CreatableNow)
                        throw new InvalidOperationException("E3D no longer allows creating " + immediate.Type.Name + " under this parent.");
                    state = immediate;
                    DbElement parent = state.Parent;
                    DbElementType type = state.Type;
                    DbElement frame = state.Frame;
                    DbElement storageFrame = state.StorageFrame;
                    List<ModelInitialAttribute> initial = state.InitialAttributes;
                    ModelEditInput nativeInput = state.NativeInput;
                    Position newPosition = state.NativePosition;
                    Orientation newOrientation = state.NativeOrientation;
                    cleanupParentReference = state.ParentReference;
                    previousReferences = state.ParentMembers;
                    result["parent"] = ModelIdentity(parent);
                    result["type"] = type.Name;
                    result["frame"] = ModelFrameIdentity(frame, input.Wrt);
                    result["parent_revision"] = state.ParentRevision;

                    createAttempted = true;
                    result["creation_attempted"] = true;
                    result["retry_safe"] = false;
                    DbElement returned = NativeRead(() => parent.CreateLast(type));
                    if (returned == null || returned.IsNull)
                    {
                        throw new InvalidOperationException("CreateLast did not return an identifiable element.");
                    }
                    int[] returnedReference = returned.Ref;
                    string returnedRefno = ModelReference(returnedReference);
                    result["refno"] = returnedRefno;
                    // Only a new reference of the requested type under the captured
                    // explicit parent may be written or made eligible for cleanup.
                    if (previousReferences.Contains(returnedRefno, StringComparer.Ordinal) ||
                        !IsUsable(returned) || returned.GetElementType() != type ||
                        ModelReference(returned.Owner.Ref) != cleanupParentReference)
                        throw new InvalidOperationException("CreateLast did not return a verifiable new element of the requested type under its explicit parent; no returned element was modified or made eligible for cleanup.");
                    createdReference = (int[])returnedReference.Clone();
                    created = returned;
                    if (requestedName != null)
                    {
                        ModelRequireSettable(created, DbAttributeInstance.NAME);
                        PdmsMessage message;
                        ThrowIfNativeFaulted();
                        bool ok = created.SetAttribute(DbAttributeInstance.NAME, new DbQualifier(), requestedName, out message);
                        ThrowIfNativeFaulted();
                        ModelRequireSetter(ok, message, "NAME");
                    }
                    foreach (ModelInitialAttribute item in initial)
                    {
                        ThrowIfNativeFaulted();
                        ModelRequireSettable(created, item.Attribute);
                        PdmsMessage message;
                        ThrowIfNativeFaulted();
                        bool ok = item.Value is string
                            ? created.SetAttribute(item.Attribute, new DbQualifier(), (string)item.Value, out message)
                            : created.SetAttribute(item.Attribute, new DbQualifier(), (double)item.Value, out message);
                        ThrowIfNativeFaulted();
                        ModelRequireSetter(ok, message, item.Canonical);
                    }
                    if (storageFrame != null) ModelRequireStorageFrame(created, storageFrame, input);
                    if (newPosition != null) ModelWritePosition(created, newPosition);
                    if (newOrientation != null) ModelWriteOrientation(created, newOrientation);
                    result["element"] = ModelIdentity(created);
                    if (requestedName != null && !ModelEditInput.TextMatches(requestedName, created.GetString(DbAttributeInstance.NAME), true))
                        throw new InvalidOperationException("NAME readback did not match the submitted name.");
                    var values = new Dictionary<string, object>();
                    result["attributes"] = values;
                    foreach (ModelInitialAttribute item in initial)
                    {
                        ThrowIfNativeFaulted();
                        object actual = item.Value is string
                            ? (object)created.GetString(item.Attribute) : ModelFinite(created.GetDouble(item.Attribute));
                        values[item.Canonical] = actual;
                        bool matches = item.Value is string
                            ? ModelEditInput.TextMatches((string)item.Value, (string)actual, false)
                            : ModelEditInput.NumberMatches((double)item.Value, (double)actual);
                        if (!matches) throw new InvalidOperationException(item.Canonical + " readback did not match the submitted value.");
                    }
                    var qualifier = new DbQualifier { wrtQualifier = frame };
                    ModelAddTransform(result, created, qualifier, input);
                    if (!ModelMemberReferences(parent, ModelMembershipVerificationLimit + 1).SequenceEqual(previousReferences.Concat(new[] { returnedRefno }), StringComparer.Ordinal))
                        throw new InvalidOperationException("Parent membership did not preserve its existing sequence and append only the new element.");
                    result["parent_membership_verified"] = true;
                    result["ce_after"] = ModelCurrentReference();
                    ModelCeUnchanged(result);
                    result["success"] = true;
                }
                catch (Exception ex)
                {
                    ThrowIfNativeFaulted();
                    result["error"] = ex.Message;
                    result["readback_verified"] = dryRun ? (object)null : false;
                    if (created != null)
                        ModelCleanupCreated(result, created, createdReference, cleanupParentReference, previousReferences, true);
                    else if (createAttempted)
                    {
                        result["creation_result_unknown"] = true;
                        result["error"] = ex.Message + " No newly created element could be safely identified for cleanup; creation outcome is unknown. Inspect the parent before retrying.";
                    }
                    result["outcome_uncertain"] = Equals(result["creation_result_unknown"], true);
                    result["retry_safe"] = !createAttempted || Equals(result["cleanup_succeeded"], true);
                }
                ModelCaptureCeAfter(result);
                return result;
            });
        }
        finally { if (!dryRun) EndCommand(); }
    }

    public object SetTransform(string refno, double[] position, double[] xAxis, double[] yAxis, string wrt, bool dryRun)
    {
        return SetTransformCore(refno, position, xAxis, yAxis, wrt, null, dryRun, false);
    }

    public object SetTransformRevisioned(string refno, double[] position, double[] xAxis, double[] yAxis,
        string wrt, string expectedRevision, bool dryRun)
    {
        return SetTransformCore(refno, position, xAxis, yAxis, wrt, expectedRevision, dryRun, true);
    }

    private object SetTransformCore(string refno, double[] position, double[] xAxis, double[] yAxis,
        string wrt, string expectedRevision, bool dryRun, bool revisioned)
    {
        string target = ModelEditInput.ExplicitReference(refno, "refno");
        ModelEditInput input = new ModelEditInput(position, xAxis, yAxis, wrt, true);
        string expected = expectedRevision == null ? null : expectedRevision.ToLowerInvariant();
        if (revisioned && ((expected != null && (expected.Length != 64 ||
            expected.Any(c => !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f')))) ||
            (!dryRun && expected == null)))
            throw new ArgumentException("Execution requires a canonical 64-character hexadecimal expected_revision.", "expectedRevision");
        string command = "database-set-transform " + target;
        if (dryRun ? IsCommandActive() : !TryBeginCommand(command)) return BusyResult(command);
        try
        {
            return OnUi(delegate
            {
                var result = ModelEditResult(dryRun);
                result["refno"] = target;
                result["write_attempted"] = false;
                result["write_applied"] = false;
                result["rollback_attempted"] = false;
                result["rollback_succeeded"] = null;
                if (revisioned)
                {
                    result["revision"] = null;
                    result["revision_conflict"] = false;
                    result["revision_scope"] = "E3D bridge session/project/MDB and ordered database/extract identities, current CE, exact target/type/name/unique Design database, OWNER/requested/storage frame identities, current target POS/ORI in native and requested frames, frame conversion, settable evidence and the exact requested transform. The comparison is not a database lock.";
                }
                var notification = new Dictionary<string, object>
                {
                    ["attempted"] = false, ["completed"] = false, ["error"] = null,
                    ["readback_verified"] = null, ["frame_identity_unchanged"] = null,
                    ["scope"] = "One Command.Update after attempted transform writes and any existing rollback. write_applied and rollback_succeeded describe the pre-notification outcome; notification_update contains the latest observation. Verifies submitted fields and owner/requested/storage frame identities only; callback changes to unsubmitted fields or frame transforms are not certified absent. Dispatches global database-change notifications and form/view callbacks; does not certify spatial-map validity or absence of UI/other callback side effects."
                };
                result["notification_update"] = notification;
                result["validation_scope"] = "Input, supported attributes and current SDK settable state; further database constraints are enforced by the setters. Multiple attribute writes are not a database transaction.";
                DbElement element = null;
                DbQualifier qualifier = null;
                Position oldPosition = null;
                Orientation oldOrientation = null;
                Position oldNativePosition = null;
                Orientation oldNativeOrientation = null;
                string originalOwnerRef = null, originalFrameRef = null, originalStorageRef = null;
                bool positionAttempted = false, orientationAttempted = false;
                try
                {
                    result["ce_before"] = ModelCurrentReference();
                    element = ModelRequireElement(target);
                    if (element.DbType != DbType.Design)
                        throw new InvalidOperationException("target must be in a Design database.");
                    DbElement owner = NativeRead(() => element.Owner);
                    DbElement frame = ModelFrame(input.Wrt, owner);
                    ModelRequireStableFrame(element, frame);
                    qualifier = new DbQualifier { wrtQualifier = frame };
                    if (input.Position != null) ModelRequireSettable(element, DbAttributeInstance.POS);
                    if (input.XAxis != null) ModelRequireSettable(element, DbAttributeInstance.ORI);
                    DbElement storageFrame = ModelStorageFrame(owner);
                    ModelRequireStorageFrame(element, storageFrame, input);
                    originalOwnerRef = ModelReference(NativeRead(() => owner.Ref));
                    originalFrameRef = ModelReference(NativeRead(() => frame.Ref));
                    originalStorageRef = ModelReference(NativeRead(() => storageFrame.Ref));
                    notification["frame_before"] = new Dictionary<string, object>
                    {
                        ["owner_refno"] = originalOwnerRef, ["requested_refno"] = originalFrameRef,
                        ["storage_refno"] = originalStorageRef
                    };
                    ModelEditInput nativeInput = ModelStorageTransform(storageFrame, frame).ToNative(input);
                    Position newPosition = ModelPosition(nativeInput.Position);
                    Orientation newOrientation = ModelOrientation(nativeInput);
                    // Retain native values for exact restoration and requested-frame values for reporting/verification.
                    if (input.Position != null)
                    {
                        oldNativePosition = element.GetPosition(DbAttributeInstance.POS);
                        oldPosition = element.GetPosition(DbAttributeInstance.POS, qualifier);
                        if (oldNativePosition == null || oldPosition == null) throw new InvalidOperationException("SDK did not return the original POS; nothing was written.");
                    }
                    if (input.XAxis != null)
                    {
                        oldNativeOrientation = element.GetOrientation(DbAttributeInstance.ORI);
                        oldOrientation = element.GetOrientation(DbAttributeInstance.ORI, qualifier);
                        if (oldNativeOrientation == null || oldOrientation == null) throw new InvalidOperationException("SDK did not return the original ORI; nothing was written.");
                    }
                    result["element"] = ModelIdentity(element);
                    result["refno"] = ModelReference(element.Ref);
                    result["frame"] = ModelFrameIdentity(frame, input.Wrt);
                    var original = new Dictionary<string, object>();
                    if (oldPosition != null) original["position"] = ModelPositionValue(oldPosition);
                    if (oldOrientation != null) original["orientation"] = ModelOrientationValue(oldOrientation);
                    result["original"] = original;
                    result["submitted"] = new Dictionary<string, object>
                    {
                        ["position"] = input.Position, ["orientation"] = ModelSubmittedOrientation(input)
                    };
                    if (revisioned)
                    {
                        string currentRevision = ModelTransformRevision(target, input);
                        result["revision"] = currentRevision;
                        if (!dryRun && !string.Equals(expected, currentRevision, StringComparison.Ordinal))
                        {
                            result["revision_conflict"] = true;
                            result["retry_safe"] = true;
                            throw new InvalidOperationException("expected_revision does not match the current target, frame or transform request. Preview again before writing.");
                        }
                    }
                    if (!dryRun)
                    {
                        result["write_attempted"] = true;
                        result["write_applied"] = null;
                        if (newPosition != null)
                        {
                            positionAttempted = true;
                            ModelWritePosition(element, newPosition);
                        }
                        if (newOrientation != null)
                        {
                            orientationAttempted = true;
                            ModelWriteOrientation(element, newOrientation);
                        }
                        ModelAddTransform(result, element, qualifier, input);
                        result["write_applied"] = true;
                    }
                    result["ce_after"] = ModelCurrentReference();
                    ModelCeUnchanged(result);
                    result["success"] = true;
                }
                catch (Exception ex)
                {
                    ThrowIfNativeFaulted();
                    result["error"] = ex.Message;
                    if (positionAttempted || orientationAttempted)
                        ModelRestoreTransform(result, element, qualifier, oldPosition, oldOrientation,
                            oldNativePosition, oldNativeOrientation, positionAttempted, orientationAttempted);
                }
                // Notification is deliberately outside the write/rollback catch. A callback
                // failure must never replay a setter or trigger a second rollback.
                if (positionAttempted || orientationAttempted)
                {
                    ThrowIfNativeFaulted();
                    result["readback_verified"] = false;
                    try
                    {
                        notification["attempted"] = true;
                        NativeWrite(() => Aveva.Core.Utilities.CommandLine.Command.Update());
                        notification["completed"] = true;

                        DbElement currentOwner = NativeRead(() => element.Owner);
                        DbElement currentFrame = NativeRead(() => ModelFrame(input.Wrt, currentOwner));
                        DbElement currentStorage = NativeRead(() => ModelStorageFrame(currentOwner));
                        string currentOwnerRef = ModelReference(NativeRead(() => currentOwner.Ref));
                        string currentFrameRef = ModelReference(NativeRead(() => currentFrame.Ref));
                        string currentStorageRef = ModelReference(NativeRead(() => currentStorage.Ref));
                        notification["frame_after"] = new Dictionary<string, object>
                        {
                            ["owner_refno"] = currentOwnerRef, ["requested_refno"] = currentFrameRef,
                            ["storage_refno"] = currentStorageRef
                        };
                        bool sameFrames = currentOwnerRef == originalOwnerRef && currentFrameRef == originalFrameRef &&
                            currentStorageRef == originalStorageRef;
                        notification["frame_identity_unchanged"] = sameFrames;
                        if (!sameFrames)
                            throw new InvalidOperationException("Owner, requested frame or native storage frame identity changed during notification. No further write or rollback was attempted.");
                        qualifier = new DbQualifier();
                        NativeWrite(() => qualifier.wrtQualifier = currentFrame);

                        // Callbacks can alter the edited element or CE. Use immutable pre-write
                        // values for rollback verification, not the submitted target values.
                        bool rolledBack = Equals(result["rollback_attempted"], true);
                        notification["verification_target"] = rolledBack ? "original_after_rollback" : "submitted";
                        ModelEditInput expected = input;
                        if (rolledBack)
                        {
                            var original = (Dictionary<string, object>)result["original"];
                            var originalPosition = original.ContainsKey("position")
                                ? (Dictionary<string, object>)original["position"] : null;
                            var originalOrientation = original.ContainsKey("orientation")
                                ? (Dictionary<string, object>)original["orientation"] : null;
                            expected = new ModelEditInput(originalPosition == null ? null : new[]
                            {
                                (double)originalPosition["x"], (double)originalPosition["y"], (double)originalPosition["z"]
                            }, originalOrientation == null ? null : (double[])originalOrientation["x_axis"],
                                originalOrientation == null ? null : (double[])originalOrientation["y_axis"], input.Wrt, true);
                        }
                        var actual = new Dictionary<string, object>();
                        notification["transform"] = actual;
                        bool matches = true;
                        if (expected.Position != null)
                        {
                            Position value = NativeRead(() => element.GetPosition(DbAttributeInstance.POS, qualifier));
                            if (value == null) throw new InvalidOperationException("SDK did not return POS after notification.");
                            double[] vector = FiniteVector(NativeRead(() => value.X), NativeRead(() => value.Y), NativeRead(() => value.Z));
                            actual["position"] = new Dictionary<string, object>
                            {
                                ["unit"] = "mm", ["x"] = vector[0], ["y"] = vector[1], ["z"] = vector[2]
                            };
                            matches &= ModelEditInput.PositionMatches(expected.Position, vector);
                            if (!rolledBack) result["position"] = actual["position"];
                        }
                        if (expected.XAxis != null)
                        {
                            Orientation value = NativeRead(() => element.GetOrientation(DbAttributeInstance.ORI, qualifier));
                            if (value == null) throw new InvalidOperationException("SDK did not return ORI after notification.");
                            double[] actualX = DirectionVector(NativeRead(() => value.XDir()));
                            double[] actualY = DirectionVector(NativeRead(() => value.YDir()));
                            double[] actualZ = DirectionVector(NativeRead(() => value.ZDir()));
                            actual["orientation"] = new Dictionary<string, object>
                            {
                                ["x_axis"] = actualX, ["y_axis"] = actualY, ["z_axis"] = actualZ
                            };
                            matches &= expected.OrientationMatches(actualX, actualY, actualZ);
                            if (!rolledBack) result["orientation"] = actual["orientation"];
                        }
                        notification["readback_verified"] = matches;
                        result["readback_verified"] = !rolledBack && matches;
                        result["ce_after"] = ModelCurrentReference();
                        ModelCeUnchanged(result);
                        notification["ce_after"] = result["ce_after"];
                        notification["ce_unchanged"] = result["ce_unchanged"];
                        if (!matches)
                            throw new InvalidOperationException("Transform after notification did not match " + notification["verification_target"] + ". No further write or rollback was attempted.");
                        if (!Equals(result["ce_unchanged"], true))
                            throw new InvalidOperationException("Current element changed during notification. No further write or rollback was attempted.");
                    }
                    catch (Exception ex)
                    {
                        ThrowIfNativeFaulted();
                        notification["error"] = ex.Message;
                        notification["readback_verified"] = false;
                        result["success"] = false;
                        result["readback_verified"] = false;
                        string previousError = result.ContainsKey("error") ? (string)result["error"] : null;
                        result["error"] = (string.IsNullOrEmpty(previousError) ? "" : previousError + " ") +
                            "Transform notification: " + ex.Message;
                    }
                }
                ModelCaptureCeAfter(result);
                if (positionAttempted || orientationAttempted)
                {
                    notification["ce_after"] = result["ce_after"];
                    notification["ce_unchanged"] = result["ce_unchanged"];
                    if (!Equals(result["ce_unchanged"], true))
                    {
                        result["success"] = false;
                        result["readback_verified"] = false;
                        notification["readback_verified"] = false;
                        string ceError = "Final current-element identity could not be verified unchanged after notification. No further write or rollback was attempted.";
                        notification["error"] = notification["error"] == null ? ceError : notification["error"] + " " + ceError;
                        string prior = result.ContainsKey("error") ? (string)result["error"] : null;
                        result["error"] = (string.IsNullOrEmpty(prior) ? "" : prior + " ") + ceError;
                    }
                }
                return result;
            });
        }
        finally { if (!dryRun) EndCommand(); }
    }

    private string ModelTransformRevision(string target, ModelEditInput input)
    {
        ThrowIfNativeFaulted();
        Project project = NativeRead(() => Project.CurrentProject);
        MDB mdb = CurrentMdb();
        if (project == null || mdb == null)
            throw new InvalidOperationException("Project/MDB context is unavailable for a guarded transform.");
        string projectName = NativeRead(() => project.Name);
        string mdbName = NativeRead(() => mdb.Name);
        if (string.IsNullOrWhiteSpace(projectName) || string.IsNullOrWhiteSpace(mdbName))
            throw new InvalidOperationException("Project/MDB identity is incomplete for a guarded transform.");
        string ce = ModelCurrentReference();
        CreateMdbIdentity mdbIdentity = ModelCreateReadMdbIdentity(mdb);
        DbElement element = ModelRequireElement(target);
        if (NativeRead(() => element.DbType) != DbType.Design)
            throw new InvalidOperationException("target must be in a Design database.");
        CreateDatabaseIdentity elementDatabase = ModelCreateRequireDatabase(element, mdbIdentity, null);
        // A transform revision is target-scoped.  The active MDB may contain
        // several Design databases, so bind the WORLD identity to the target's
        // verified database instead of whichever Design database happens to be
        // first in MDB order.
        string worldReference = ModelCreateDatabaseWorld(elementDatabase, mdbIdentity);
        DbElement owner = NativeRead(() => element.Owner);
        DbElement frame = ModelFrame(input.Wrt, owner);
        ModelRequireStableFrame(element, frame);
        DbElement storageFrame = ModelStorageFrame(owner);
        CreateDatabaseIdentity ownerDatabase = ModelCreateRequireDatabase(owner, mdbIdentity, null);
        CreateDatabaseIdentity frameDatabase = ModelCreateRequireDatabase(frame, mdbIdentity, null);
        CreateDatabaseIdentity storageDatabase = ModelCreateRequireDatabase(storageFrame, mdbIdentity, null);
        ModelRequireStorageFrame(element, storageFrame, input);
        if (input.Position != null) ModelRequireSettable(element, DbAttributeInstance.POS);
        if (input.XAxis != null) ModelRequireSettable(element, DbAttributeInstance.ORI);

        string elementReference = ModelReference(NativeRead(() => element.Ref));
        string ownerReference = ModelReference(NativeRead(() => owner.Ref));
        string frameReference = ModelReference(NativeRead(() => frame.Ref));
        string storageReference = ModelReference(NativeRead(() => storageFrame.Ref));
        if (string.IsNullOrEmpty(elementReference) || string.IsNullOrEmpty(ownerReference) ||
            string.IsNullOrEmpty(frameReference) || string.IsNullOrEmpty(storageReference))
            throw new InvalidOperationException("A guarded transform identity reference is unavailable.");

        Position nativePosition = null, framedPosition = null;
        Orientation nativeOrientation = null, framedOrientation = null;
        var qualifier = new DbQualifier { wrtQualifier = frame };
        if (input.Position != null)
        {
            nativePosition = NativeRead(() => element.GetPosition(DbAttributeInstance.POS));
            framedPosition = NativeRead(() => element.GetPosition(DbAttributeInstance.POS, qualifier));
            if (nativePosition == null || framedPosition == null)
                throw new InvalidOperationException("The SDK did not return original native/requested-frame POS for a guarded transform.");
        }
        if (input.XAxis != null)
        {
            nativeOrientation = NativeRead(() => element.GetOrientation(DbAttributeInstance.ORI));
            framedOrientation = NativeRead(() => element.GetOrientation(DbAttributeInstance.ORI, qualifier));
            if (nativeOrientation == null || framedOrientation == null)
                throw new InvalidOperationException("The SDK did not return original native/requested-frame ORI for a guarded transform.");
        }
        ModelEditFrame storageTransform = ModelStorageTransform(storageFrame, frame);
        ModelEditInput nativeInput = storageTransform.ToNative(input);
        DbElementType elementType = NativeRead(() => element.GetElementType());
        DbElementType elementActualType = NativeRead(() => element.GetActualType());
        string type = NativeRead(() => elementType.ShortName);
        string actualType = NativeRead(() => elementActualType.ShortName);
        bool nameValid = NativeRead(() => element.IsAttributeValid(DbAttributeInstance.NAME));
        string name = nameValid ? NativeRead(() => element.GetString(DbAttributeInstance.NAME)) : null;
        bool positionValid = NativeRead(() => element.IsAttributeValid(DbAttributeInstance.POS));
        bool positionSettable = positionValid && NativeRead(() => element.IsAttributeSetable(DbAttributeInstance.POS));
        bool orientationValid = NativeRead(() => element.IsAttributeValid(DbAttributeInstance.ORI));
        bool orientationSettable = orientationValid && NativeRead(() => element.IsAttributeSetable(DbAttributeInstance.ORI));

        return PrimitiveHash(writer =>
        {
            writer.Write("native-element-transform-revision-v1");
            writer.Write(createRevisionSession); writer.Write(projectName); writer.Write(mdbName); writer.Write(ce ?? "");
            writer.Write(mdbIdentity.Databases.Count);
            foreach (CreateDatabaseIdentity database in mdbIdentity.Databases)
                ModelCreateWriteDatabaseIdentity(writer, database);
            writer.Write(elementReference); writer.Write(type ?? ""); writer.Write(actualType ?? "");
            ModelCreateWriteNullableString(writer, name);
            ModelCreateWriteDatabaseIdentity(writer, elementDatabase);
            writer.Write(ownerReference); ModelCreateWriteDatabaseIdentity(writer, ownerDatabase);
            writer.Write(frameReference); ModelCreateWriteDatabaseIdentity(writer, frameDatabase);
            writer.Write(storageReference); ModelCreateWriteDatabaseIdentity(writer, storageDatabase);
            writer.Write(worldReference); writer.Write(positionValid); writer.Write(positionSettable);
            writer.Write(orientationValid); writer.Write(orientationSettable);
            writer.Write(input.Wrt); ModelCreateWriteVector(writer, input.Position);
            ModelCreateWriteVector(writer, input.XAxis); ModelCreateWriteVector(writer, input.YAxis);
            ModelCreateWriteVector(writer, nativeInput.Position);
            ModelCreateWriteVector(writer, nativeInput.XAxis); ModelCreateWriteVector(writer, nativeInput.YAxis);
            ModelCreateWriteNullableString(writer, input.Position == null ? null : elementReference);
            if (input.Position != null)
            {
                ModelCreateWriteVector(writer, FiniteVector(NativeRead(() => nativePosition.X),
                    NativeRead(() => nativePosition.Y), NativeRead(() => nativePosition.Z)));
                ModelCreateWriteVector(writer, FiniteVector(NativeRead(() => framedPosition.X),
                    NativeRead(() => framedPosition.Y), NativeRead(() => framedPosition.Z)));
            }
            if (input.XAxis != null)
            {
                ModelCreateWriteOrientation(writer, nativeOrientation);
                ModelCreateWriteOrientation(writer, framedOrientation);
            }
            if (storageReference != frameReference)
            {
                Position origin = NativeRead(() => storageFrame.GetPosition(DbAttributeInstance.POS, qualifier));
                Orientation rotation = NativeRead(() => storageFrame.GetOrientation(DbAttributeInstance.ORI, qualifier));
                if (origin == null || rotation == null)
                    throw new InvalidOperationException("The SDK did not return the complete frame conversion used by a guarded transform.");
                ModelCreateWriteVector(writer, FiniteVector(NativeRead(() => origin.X),
                    NativeRead(() => origin.Y), NativeRead(() => origin.Z)));
                ModelCreateWriteOrientation(writer, rotation);
            }
        });
    }

    private sealed class ModelInitialAttribute
    {
        internal DbAttribute Attribute;
        internal string Canonical;
        internal object Value;
    }

    private static List<ModelInitialAttribute> ModelInitialAttributes(DbElementType type, IDictionary<string, string> submitted)
    {
        var result = new List<ModelInitialAttribute>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (KeyValuePair<string, string> item in submitted)
        {
            ThrowIfNativeFaulted();
            string key = (item.Key ?? "").Trim();
            if (key.Length == 0 || key.Length > 64 || key.Any(c => !(char.IsLetterOrDigit(c) || c == ':' || c == '_')))
                throw new ArgumentException("attributes must use attribute names only.");
            DbAttribute attribute = DbAttribute.GetDbAttribute(key);
            if (attribute == null || !attribute.IsValid)
                throw new ArgumentException("Unknown attribute: " + key);
            string canonical = attribute.ShortName.ToUpperInvariant();
            object parsed = ModelEditInput.AttributeValue(canonical, item.Value);
            if (!seen.Add(canonical)) throw new ArgumentException("Duplicate attribute aliases: " + canonical);
            if (!AttributeEditPolicy.SupportsSdkType(canonical, attribute.Type.ToString(), attribute.IsArray))
                throw new ArgumentException("Unsupported SDK attribute type: " + canonical);
            ModelRequireTypeAttribute(type, attribute);
            result.Add(new ModelInitialAttribute { Attribute = attribute, Canonical = canonical, Value = parsed });
        }
        return result;
    }

    private static Dictionary<string, object> ModelEditResult(bool dryRun)
    {
        return new Dictionary<string, object>
        {
            ["success"] = false, ["dry_run"] = dryRun,
            ["readback_verified"] = dryRun ? (object)null : false,
            ["ce_before"] = null, ["ce_after"] = null, ["ce_unchanged"] = null
        };
    }

    private static DbElement ModelRequireElement(string target)
    {
        ThrowIfNativeFaulted();
        DbElement element = ResolveElement(target);
        if (!IsUsable(element)) throw new InvalidOperationException("Element was not found: " + target);
        return element;
    }

    private static DbElement ModelFrame(string requested, DbElement owner)
    {
        ThrowIfNativeFaulted();
        DbElement frame;
        if (requested == "OWNER") frame = owner;
        else if (requested == "WORLD")
        {
            MDB mdb = NativeRead(() => CurrentMdb());
            if (mdb == null) throw new InvalidOperationException("No current MDB is open.");
            frame = NativeRead(() => mdb.GetFirstWorld(DbType.Design));
        }
        else frame = ModelRequireElement(requested);
        if (!IsUsable(frame) || NativeRead(() => frame.DbType) != DbType.Design)
            throw new InvalidOperationException("A usable Design reference frame was not found: " + requested);
        return frame;
    }

    private static void ModelRequireTypeAttribute(DbElementType type, DbAttribute attribute)
    {
        ThrowIfNativeFaulted();
        if (!type.IsAttributeValid(attribute))
            throw new InvalidOperationException("Attribute " + attribute.ShortName + " is not supported on " + type.Name + ".");
    }

    private static DbElement ModelStorageFrame(DbElement owner)
    {
        ThrowIfNativeFaulted();
        // A PAVE's owner PLOO has no coordinate system; its native POS is in
        // the enclosing PANE. Find the nearest complete positioning ancestor,
        // then confirm this contract against the element's native getters.
        var seen = new HashSet<string>(StringComparer.Ordinal);
        DbElement current = owner;
        for (int depth = 0; depth < 1024; depth++)
        {
            ThrowIfNativeFaulted();
            if (!IsUsable(current))
                throw new InvalidOperationException("A usable native positioning frame was not found; nothing was written.");
            if (!seen.Add(ModelReference(NativeRead(() => current.Ref))))
                throw new InvalidOperationException("A cycle was found in the native positioning frame hierarchy.");
            DbElementType type = NativeRead(() => current.GetElementType());
            string shortName = NativeRead(() => type.ShortName);
            if (shortName == "WORL" || shortName == "WORLD")
            {
                ModelCreateWorldReference(current);
                return current;
            }
            bool hasPosition = NativeRead(() => current.IsAttributeValid(DbAttributeInstance.POS));
            bool hasOrientation = NativeRead(() => current.IsAttributeValid(DbAttributeInstance.ORI));
            if (hasPosition && hasOrientation) return current;
            if (hasPosition || hasOrientation)
                throw new InvalidOperationException("The native positioning ancestor has an incomplete POS/ORI frame; conversion is unavailable and nothing was written.");
            current = NativeRead(() => current.Owner);
        }
        throw new InvalidOperationException("Native positioning frame hierarchy exceeds the verification limit.");
    }

    private static ModelEditFrame ModelStorageTransform(DbElement storageFrame, DbElement requestedFrame)
    {
        ThrowIfNativeFaulted();
        if (ModelReference(storageFrame.Ref) == ModelReference(requestedFrame.Ref))
            return ModelEditFrame.Identity();
        // Getters implement WRT conversion; E3D 2.1's native setters do not.
        // Read the entire frame from the SDK instead of filling missing POS/ORI
        // with zero/identity or composing guessed owner transformations.
        var qualifier = new DbQualifier { wrtQualifier = requestedFrame };
        Position origin = NativeRead(() => storageFrame.GetPosition(DbAttributeInstance.POS, qualifier));
        Orientation rotation = NativeRead(() => storageFrame.GetOrientation(DbAttributeInstance.ORI, qualifier));
        if (origin == null || rotation == null)
            throw new InvalidOperationException("SDK did not return the complete native positioning frame; nothing was written.");
        return new ModelEditFrame(FiniteVector(origin.X, origin.Y, origin.Z),
            DirectionVector(rotation.XDir()), DirectionVector(rotation.YDir()), DirectionVector(rotation.ZDir()));
    }

    private static void ModelRequireStorageFrame(DbElement element, DbElement storageFrame, ModelEditInput input)
    {
        ThrowIfNativeFaulted();
        var qualifier = new DbQualifier { wrtQualifier = storageFrame };
        if (input.Position != null)
        {
            Position native = NativeRead(() => element.GetPosition(DbAttributeInstance.POS));
            Position relative = NativeRead(() => element.GetPosition(DbAttributeInstance.POS, qualifier));
            if (native == null || relative == null ||
                !ModelEditInput.PositionMatches(FiniteVector(native.X, native.Y, native.Z), FiniteVector(relative.X, relative.Y, relative.Z)))
                throw new InvalidOperationException("The SDK native POS does not use the selected positioning ancestor; nothing was written.");
        }
        if (input.XAxis != null)
        {
            Orientation native = NativeRead(() => element.GetOrientation(DbAttributeInstance.ORI));
            Orientation relative = NativeRead(() => element.GetOrientation(DbAttributeInstance.ORI, qualifier));
            if (native == null || relative == null ||
                !ModelOrientationsEqual(native, relative))
                throw new InvalidOperationException("The SDK native ORI does not use the selected positioning ancestor; nothing was written.");
        }
    }

    private static bool ModelOrientationsEqual(Orientation left, Orientation right)
    {
        return ModelVectorsEqual(DirectionVector(left.XDir()), DirectionVector(right.XDir())) &&
            ModelVectorsEqual(DirectionVector(left.YDir()), DirectionVector(right.YDir())) &&
            ModelVectorsEqual(DirectionVector(left.ZDir()), DirectionVector(right.ZDir()));
    }

    private static void ModelRequireStableFrame(DbElement target, DbElement frame)
    {
        ThrowIfNativeFaulted();
        // A frame owned by the edited element moves with the edit, invalidating
        // the original-value and rollback WRT contract. Check before any setter.
        string targetRef = ModelReference(NativeRead(() => target.Ref));
        var seen = new HashSet<string>(StringComparer.Ordinal);
        DbElement current = frame;
        for (int depth = 0; depth < 1024; depth++)
        {
            ThrowIfNativeFaulted();
            if (current == null || NativeRead(() => current.IsNull)) return;
            if (!NativeRead(() => current.IsValid)) throw new InvalidOperationException("Cannot verify that the reference frame is independent of the edited element.");
            string currentRef = ModelReference(NativeRead(() => current.Ref));
            if (currentRef == targetRef)
                throw new ArgumentException("wrt cannot be the edited element or one of its descendants; use OWNER, WORLD, or an independent element.");
            if (!seen.Add(currentRef)) throw new InvalidOperationException("A cycle was found in the reference frame owner hierarchy.");
            // E3D represents the Design WORLD as REF = database/0. It is the
            // stable root; its Owner may be a null/system placeholder and must
            // not be traversed or treated as a malformed reference.
            DbElementType type = NativeRead(() => current.GetElementType());
            string shortName = NativeRead(() => type.ShortName);
            if (shortName == "WORL" || shortName == "WORLD")
            {
                ModelCreateWorldReference(current);
                return;
            }
            current = NativeRead(() => current.Owner);
        }
        throw new InvalidOperationException("Reference frame owner hierarchy exceeds the verification limit.");
    }

    private static void ModelRequireSettable(DbElement element, DbAttribute attribute)
    {
        ThrowIfNativeFaulted();
        if (!NativeRead(() => element.IsAttributeValid(attribute)) || !NativeRead(() => element.IsAttributeSetable(attribute)))
            throw new InvalidOperationException("E3D reports that " + attribute.ShortName + " is unsupported or cannot be set on this element now.");
    }

    private static void ModelRequireSetter(bool success, PdmsMessage message, string field)
    {
        ThrowIfNativeFaulted();
        // A returned diagnostic is not discarded even when the SDK bool is true.
        string text = message == null ? "" : NativeRead(() => message.MessageText());
        if (!success || message != null)
            throw new InvalidOperationException("Setting " + field + " failed (SDK returned " + success + "). " + text);
    }

    private static void ModelWritePosition(DbElement element, Position value)
    {
        ThrowIfNativeFaulted();
        ModelRequireSettable(element, DbAttributeInstance.POS);
        PdmsMessage message;
        ThrowIfNativeFaulted();
        bool success = element.SetAttribute(DbAttributeInstance.POS, new DbQualifier(), value, out message);
        ThrowIfNativeFaulted();
        ModelRequireSetter(success, message, "POS");
    }

    private static void ModelWriteOrientation(DbElement element, Orientation value)
    {
        ThrowIfNativeFaulted();
        ModelRequireSettable(element, DbAttributeInstance.ORI);
        PdmsMessage message;
        ThrowIfNativeFaulted();
        bool success = element.SetAttribute(DbAttributeInstance.ORI, new DbQualifier(), value, out message);
        ThrowIfNativeFaulted();
        ModelRequireSetter(success, message, "ORI");
    }

    private static Position ModelPosition(double[] vector)
    {
        if (vector == null) return null;
        Position value = Position.Create(vector[0], vector[1], vector[2]);
        if (value == null) throw new InvalidOperationException("SDK did not construct the submitted position/vector.");
        return value;
    }

    private static Orientation ModelOrientation(ModelEditInput input)
    {
        if (input.XAxis == null) return null;
        // E3D 2.1's two-Direction constructor fills the matrix in a different
        // order and yields the inverse rotation. Bind each local axis explicitly.
        Orientation value = Orientation.Create(
            Axis.X, Direction.Create(ModelPosition(input.XAxis)),
            Axis.Y, Direction.Create(ModelPosition(input.YAxis)));
        if (value == null) throw new InvalidOperationException("SDK did not construct the submitted orientation.");
        ModelRequireOrientationMatches(input, value, "Constructed ORI");
        return value;
    }

    private static object ModelSubmittedOrientation(ModelEditInput input)
    {
        return input.XAxis == null ? null : new Dictionary<string, object>
        {
            ["x_axis"] = input.XAxis, ["y_axis"] = input.YAxis
        };
    }

    private static string ModelReference(int[] reference)
    {
        if (!ModelEditInput.IsReferencePair(reference))
            throw new InvalidOperationException("SDK did not return a reference number pair.");
        return "=" + reference[0].ToString(CultureInfo.InvariantCulture) + "/" + reference[1].ToString(CultureInfo.InvariantCulture);
    }

    private static Dictionary<string, object> ModelIdentity(DbElement element)
    {
        ThrowIfNativeFaulted();
        return new Dictionary<string, object>
        {
            ["refno"] = ModelReference(element.Ref), ["type"] = element.GetElementType().Name,
            ["name"] = element.IsAttributeValid(DbAttributeInstance.NAME) ? element.GetString(DbAttributeInstance.NAME) : null
        };
    }

    private static Dictionary<string, object> ModelFrameIdentity(DbElement frame, string requested)
    {
        var result = ModelIdentity(frame);
        result["requested"] = requested;
        return result;
    }

    private static double ModelFinite(double value)
    {
        if (double.IsNaN(value) || double.IsInfinity(value))
            throw new InvalidOperationException("SDK returned a non-finite value.");
        return value;
    }

    private static Dictionary<string, object> ModelPositionValue(Position value)
    {
        return new Dictionary<string, object>
        {
            ["unit"] = "mm", ["x"] = ModelFinite(value.X), ["y"] = ModelFinite(value.Y), ["z"] = ModelFinite(value.Z)
        };
    }

    private static Dictionary<string, object> ModelOrientationValue(Orientation value)
    {
        return new Dictionary<string, object>
        {
            ["x_axis"] = DirectionVector(value.XDir()), ["y_axis"] = DirectionVector(value.YDir()),
            ["z_axis"] = DirectionVector(value.ZDir())
        };
    }

    private static void ModelAddTransform(Dictionary<string, object> result, DbElement element, DbQualifier qualifier, ModelEditInput input)
    {
        ThrowIfNativeFaulted();
        if (input.Position != null)
        {
            Position value = NativeRead(() => element.GetPosition(DbAttributeInstance.POS, qualifier));
            result["position"] = ModelPositionValue(value);
            if (!ModelEditInput.PositionMatches(input.Position, FiniteVector(value.X, value.Y, value.Z)))
                throw new InvalidOperationException("POS readback did not match the submitted position in the requested frame.");
        }
        if (input.XAxis != null)
        {
            Orientation value = NativeRead(() => element.GetOrientation(DbAttributeInstance.ORI, qualifier));
            result["orientation"] = ModelOrientationValue(value);
            ModelRequireOrientationMatches(input, value, "ORI readback");
        }
        result["readback_verified"] = true;
    }

    private static void ModelRequireOrientationMatches(ModelEditInput input, Orientation value, string stage)
    {
        if (!input.OrientationMatches(DirectionVector(value.XDir()), DirectionVector(value.YDir()), DirectionVector(value.ZDir())))
            throw new InvalidOperationException(stage + " did not match the submitted local axes in the requested frame.");
    }

    private static string ModelCurrentReference()
    {
        ThrowIfNativeFaulted();
        DbElement current = NativeRead(() => Aveva.Core.Database.CurrentElement.Element);
        return current == null || NativeRead(() => current.IsNull) ? "" : ModelReference(NativeRead(() => current.Ref));
    }

    private static void ModelCeUnchanged(Dictionary<string, object> result)
    {
        if (result["ce_before"] != null && result["ce_after"] != null)
            result["ce_unchanged"] = string.Equals((string)result["ce_before"], (string)result["ce_after"], StringComparison.Ordinal);
    }

    private static void ModelCaptureCeAfter(Dictionary<string, object> result)
    {
        ThrowIfNativeFaulted();
        try { result["ce_after"] = ModelCurrentReference(); ModelCeUnchanged(result); }
        catch (Exception ex) { ThrowIfNativeFaulted(); result["ce_after"] = null; result["ce_unchanged"] = null; result["ce_read_error"] = ex.Message; }
    }

    private static string[] ModelMemberReferences(DbElement parent, int limit = ModelMembershipVerificationLimit)
    {
        ThrowIfNativeFaulted();
        MemberPage<DbElement> page = MemberPage<DbElement>.Read(DirectSiblings(parent, null),
            limit, item => ModelReference(NativeRead(() => item.Ref)));
        ThrowIfNativeFaulted();
        if (page.HasMore != false || page.ReadErrors.Count > 0)
            throw new InvalidOperationException("Cannot verify complete parent membership within " +
                limit + " direct members. " + string.Join(" ", page.ReadErrors));
        return page.Items.Select(item => ModelReference(NativeRead(() => item.Ref))).ToArray();
    }

    private static void ModelVerifyDeletion(Dictionary<string, object> result, int[] targetReference,
        string targetRefno, string ownerRefno, string[] originalMembers, bool? nativeDeleteReturned,
        string nativeError, Func<string, DbElement> resolveElement)
    {
        result["native_delete_error"] = nativeError ?? "";
        result["native_delete_returned"] = nativeDeleteReturned;
        try
        {
            DbElement remaining = NativeRead(() => DbElement.GetElement(targetReference));
            if (remaining == null)
                throw new InvalidOperationException("The exact target DBREF lookup returned no wrapper.");
            bool isNull = NativeRead(() => remaining.IsNull);
            if (isNull)
                throw new InvalidOperationException("The exact target DBREF lookup returned a null reference.");
            string readbackRefno = ModelReference(NativeRead(() => remaining.Ref));
            if (!string.Equals(readbackRefno, targetRefno, StringComparison.Ordinal))
                throw new InvalidOperationException("The target readback resolved a different numeric DBREF.");
            bool validState = NativeRead(() => remaining.IsValid);
            bool deletedState = !validState && NativeRead(() => remaining.IsDeleted);

            DbElement owner = resolveElement(ownerRefno);
            if (!IsUsable(owner) || ModelReference(NativeRead(() => owner.Ref)) != ownerRefno)
                throw new InvalidOperationException("The exact original owner could not be reverified.");
            string[] currentMembers = ModelMemberReferences(owner);
            string[] expectedMembers = originalMembers
                .Where(value => !string.Equals(value, targetRefno, StringComparison.Ordinal)).ToArray();
            bool absentFromOwner = !currentMembers.Contains(targetRefno, StringComparer.Ordinal);
            bool ownerMembershipMatches = currentMembers.SequenceEqual(expectedMembers, StringComparer.Ordinal);
            bool verified = deletedState && absentFromOwner && ownerMembershipMatches;
            bool stillPresent = validState && currentMembers.Contains(targetRefno, StringComparer.Ordinal);

            result["readback_refno"] = readbackRefno;
            result["readback_reference_is_valid"] = validState;
            result["readback_deleted_state"] = deletedState;
            result["readback_absent_from_owner"] = absentFromOwner;
            result["readback_owner_membership_matches_expected"] = ownerMembershipMatches;
            result["deletion_verified"] = verified;
            result["verification_status"] = verified ? "confirmed_deleted" : stillPresent ? "still_present" : "unknown";
            // A surviving root does not prove a failed hierarchy deletion had
            // no partial effect on its descendants. Only full confirmation
            // that the exact target was removed makes the outcome certain.
            result["outcome_uncertain"] = !verified;
            result["success"] = verified;
            if (!verified)
            {
                result["verification_error"] = "Readback did not verify both the exact deleted reference and the complete original owner membership.";
                result["error"] = stillPresent
                    ? "The exact target remains valid and present in its original owner. Do not automatically replay deletion."
                    : "The delete result remains unknown because complete exact-reference and owner-membership proof was not obtained.";
            }
        }
        catch (Exception ex)
        {
            // A latched native database fault forbids all further SDK calls;
            // propagate it so the bridge also reports the current fault state.
            ThrowIfNativeFaulted();
            result["success"] = false;
            result["deletion_verified"] = false;
            result["verification_status"] = "unknown";
            result["outcome_uncertain"] = true;
            result["retry_safe"] = false;
            result["verification_error"] = ex.Message;
            result["error"] = nativeDeleteReturned == false
                ? "The SDK reported that deletion failed, and the target state could not be fully read back."
                : "The delete call may have changed the database, but readback did not complete.";
        }
    }

    private static void ModelCleanupCreated(Dictionary<string, object> result, DbElement created, int[] reference,
        string parentReference, string[] originalMembers, bool requiresEmptyCreated = false)
    {
        ThrowIfNativeFaulted();
        result["cleanup_attempted"] = false;
        result["cleanup_succeeded"] = null;
        result["cleanup_verified_absent"] = null;
        result["cleanup_parent_append_verified"] = false;
        result["residual_refno"] = reference == null ? null : ModelReference(reference);
        var errors = new List<string>();
        try
        {
            if (reference == null || originalMembers == null || string.IsNullOrEmpty(parentReference))
                throw new InvalidOperationException("The captured new reference, original parent, and original membership are required; no deletion was attempted.");
            string capturedRefno = ModelReference(reference);
            DbElement parent = ModelRequireElement(parentReference);
            // DBREF high words are opaque reference identity, not Db.Number.
            // A new child can legitimately have a different high word to its owner.
            if (ModelReference(NativeRead(() => parent.Ref)) != parentReference ||
                originalMembers.Contains(capturedRefno, StringComparer.Ordinal))
                throw new InvalidOperationException("The captured reference was not a new member of the original parent; no deletion was attempted.");
            DbElement exact = NativeRead(() => DbElement.GetElement(reference));
            bool alreadyDeleted = exact != null && !NativeRead(() => exact.IsNull) && !NativeRead(() => exact.IsValid) && NativeRead(() => exact.IsDeleted);
            if (!alreadyDeleted)
            {
                if (!IsUsable(exact) || ModelReference(NativeRead(() => exact.Ref)) != capturedRefno ||
                    created == null || ModelReference(NativeRead(() => created.Ref)) != capturedRefno)
                    throw new InvalidOperationException("The exact returned new element and original parent could not be reverified; no deletion was attempted.");
                DbElement owner = NativeRead(() => exact.Owner);
                if (!IsUsable(owner) || ModelReference(NativeRead(() => owner.Ref)) != parentReference)
                    throw new InvalidOperationException("The exact returned new element has a different or unreadable owner; no deletion was attempted.");
                bool appendedOnly = ModelMemberReferences(parent, ModelMembershipVerificationLimit + 1)
                    .SequenceEqual(originalMembers.Concat(new[] { capturedRefno }), StringComparer.Ordinal);
                result["cleanup_parent_append_verified"] = appendedOnly;
                if (!appendedOnly)
                    throw new InvalidOperationException("Parent membership must preserve the entire original sequence and append only the captured new reference; no deletion was attempted.");
                if (requiresEmptyCreated)
                {
                    bool empty = ModelMemberReferences(exact).Length == 0;
                    result["cleanup_created_empty_verified"] = empty;
                    if (!empty)
                        throw new InvalidOperationException("The newly created element acquired child members whose provenance was not captured; no deletion was attempted.");
                }
                result["cleanup_attempted"] = true;
                try
                {
                    PdmsMessage message;
                    ThrowIfNativeFaulted();
                    bool deleted = exact.Delete(out message);
                    ThrowIfNativeFaulted();
                    result["cleanup_delete_returned"] = deleted;
                    if (!deleted || message != null)
                        errors.Add("Delete returned " + deleted + ". " + (message == null ? "" : NativeRead(() => message.MessageText())));
                }
                catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("Delete: " + ex.Message); }
            }
        }
        catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("Verify cleanup target: " + ex.Message); }
        try
        {
            if (reference == null || originalMembers == null || string.IsNullOrEmpty(parentReference))
                throw new InvalidOperationException("Captured reference and original parent membership are unavailable.");
            string capturedRefno = ModelReference(reference);
            DbElement remaining = NativeRead(() => DbElement.GetElement(reference));
            if (remaining == null || ModelReference(NativeRead(() => remaining.Ref)) != capturedRefno)
                throw new InvalidOperationException("The cleanup readback did not resolve the exact captured reference.");
            // The SDK retains non-null reference wrappers after deletion. Its
            // IsDeleted getter requires an invalid element in a still-open DB;
            // invalid or null alone cannot distinguish deletion from a closed DB.
            bool validState = remaining != null && NativeRead(() => remaining.IsValid);
            bool deletedState = remaining != null && !NativeRead(() => remaining.IsNull) && !validState && NativeRead(() => remaining.IsDeleted);
            DbElement parent = ModelRequireElement(parentReference);
            if (ModelReference(NativeRead(() => parent.Ref)) != parentReference)
                throw new InvalidOperationException("The cleanup readback did not resolve the exact captured parent.");
            string[] currentMembers = ModelMemberReferences(parent);
            bool absentFromParent = !currentMembers.Contains(capturedRefno, StringComparer.Ordinal);
            bool originalMembershipRestored = currentMembers.SequenceEqual(originalMembers, StringComparer.Ordinal);
            bool absent = deletedState && absentFromParent;
            result["cleanup_reference_is_valid"] = validState;
            result["cleanup_deleted_state"] = deletedState;
            result["cleanup_absent_from_parent"] = absentFromParent;
            result["cleanup_parent_membership_restored"] = originalMembershipRestored;
            result["cleanup_verified_absent"] = absent;
            result["cleanup_succeeded"] = absent && originalMembershipRestored;
            if (absent) result["residual_refno"] = null;
            if (!absent) errors.Add("The exact created reference is not certified deleted and absent from its original parent.");
            if (!originalMembershipRestored) errors.Add("The original parent membership sequence was not restored.");
        }
        catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("Verify cleanup: " + ex.Message); }
        result["creation_result_unknown"] = !Equals(result["cleanup_succeeded"], true);
        if (errors.Count > 0) result["cleanup_errors"] = errors;
    }

    private static void ModelRestoreTransform(Dictionary<string, object> result, DbElement element, DbQualifier qualifier,
        Position originalPosition, Orientation originalOrientation, Position nativePosition, Orientation nativeOrientation,
        bool restorePosition, bool restoreOrientation)
    {
        ThrowIfNativeFaulted();
        result["rollback_attempted"] = true;
        result["rollback_succeeded"] = false;
        result["write_applied"] = null;
        result.Remove("position");
        result.Remove("orientation");
        var errors = new List<string>();
        // Restore only attempted fields, in reverse write order; never use global UNDO.
        if (restoreOrientation)
        {
            try { ModelWriteOrientation(element, nativeOrientation); }
            catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("Restore ORI: " + ex.Message); }
        }
        if (restorePosition)
        {
            try { ModelWritePosition(element, nativePosition); }
            catch (Exception ex) { ThrowIfNativeFaulted(); errors.Add("Restore POS: " + ex.Message); }
        }
        var residual = new Dictionary<string, object>();
        bool matches = true;
        bool residualComplete = true;
        if (restorePosition)
        {
            try
            {
                Position value = NativeRead(() => element.GetPosition(DbAttributeInstance.POS, qualifier));
                residual["position"] = ModelPositionValue(value);
                matches &= ModelVectorsEqual(FiniteVector(value.X, value.Y, value.Z),
                    FiniteVector(originalPosition.X, originalPosition.Y, originalPosition.Z));
            }
            catch (Exception ex) { ThrowIfNativeFaulted(); residualComplete = false; errors.Add("Read residual POS: " + ex.Message); }
        }
        if (restoreOrientation)
        {
            try
            {
                Orientation value = NativeRead(() => element.GetOrientation(DbAttributeInstance.ORI, qualifier));
                residual["orientation"] = ModelOrientationValue(value);
                matches &= ModelVectorsEqual(DirectionVector(value.XDir()), DirectionVector(originalOrientation.XDir())) &&
                    ModelVectorsEqual(DirectionVector(value.YDir()), DirectionVector(originalOrientation.YDir())) &&
                    ModelVectorsEqual(DirectionVector(value.ZDir()), DirectionVector(originalOrientation.ZDir()));
            }
            catch (Exception ex) { ThrowIfNativeFaulted(); residualComplete = false; errors.Add("Read residual ORI: " + ex.Message); }
        }
        result["residual_transform"] = residual;
        result["residual_transform_complete"] = residualComplete;
        result["residual_matches_original"] = residualComplete ? (object)matches : null;
        result["rollback_succeeded"] = residualComplete && matches && errors.Count == 0;
        if (residualComplete && matches) result["write_applied"] = false;
        if (errors.Count > 0) result["rollback_errors"] = errors;
    }

    private static bool ModelVectorsEqual(double[] left, double[] right)
    {
        return left.Zip(right, (a, b) => Math.Abs(a - b) <= ModelEditInput.AxisTolerance).All(equal => equal);
    }
}
