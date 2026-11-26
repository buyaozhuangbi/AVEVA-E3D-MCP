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
using E3d21AiBridge;

// The production reader, hashes, guards and CreateElement body are injected.
// SDK allocation/setters and coordinate conversion are managed substitutes.
internal static class ModelCreateRevisionHarness
{
    private static int checks;
    private static void Check(bool value, string message) { checks++; if (!value) throw new Exception(message); }
    private static Dictionary<string, object> Run(bool preview, string revision = null,
        string name = "", IDictionary<string, string> attributes = null, double[] position = null)
    {
        return (Dictionary<string, object>)CreateScenario.Context.CreateElement("=1/2", "BOX", name,
            attributes ?? new Dictionary<string, string>(), position, null, null, "/FRAME", revision, preview);
    }
    private static string Preview()
    {
        var result = Run(true);
        Check(Equals(result["success"], true), "A native-substitute preview must succeed: " + (result.ContainsKey("error") ? result["error"] : ""));
        Check(CreateScenario.Creates == 0 && CreateScenario.Setters == 0 && CreateScenario.Cleanups == 0, "Preview never allocates or edits.");
        string revision = (string)result["revision"];
        Check(revision.Length == 64 && revision.All(c => "0123456789abcdef".Contains(c)), "A preview returns a 64-character revision.");
        return revision;
    }
    private static void Conflict(string revision, string label)
    {
        var result = Run(false, revision);
        Check(Equals(result["success"], false) && Equals(result["revision_conflict"], true), label + ": stale revision rejected.");
        Check(Equals(result["creation_attempted"], false) && Equals(result["outcome_uncertain"], false)
            && Equals(result["retry_safe"], true), label + ": failure is known to precede allocation.");
        Check(CreateScenario.Creates == 0 && CreateScenario.Setters == 0 && CreateScenario.Cleanups == 0, label + ": no create, setter or cleanup.");
    }
    private static void RejectedBeforeCreate(Dictionary<string, object> result, string label)
    {
        Check(Equals(result["success"], false), label + ": request must be rejected.");
        Check(Equals(result["creation_attempted"], false) && Equals(result["outcome_uncertain"], false)
            && Equals(result["retry_safe"], true), label + ": native allocation was never dispatched.");
        Check(CreateScenario.Creates == 0 && CreateScenario.Setters == 0 && CreateScenario.Cleanups == 0,
            label + ": no create, setter, or compensating delete.");
    }
    private static int Main()
    {
        try
        {
            CreateScenario.Reset(); string revision = Preview();
            var result = Run(false, revision.ToUpperInvariant());
            Check(Equals(result["success"], true) && Equals(result["readback_verified"], true), "A matching preview permits the real CreateElement success path.");
            Check(CreateScenario.Creates == 1 && CreateScenario.Parent.Children.Count == 3 && CreateScenario.Cleanups == 0, "Exactly one native-substitute child is appended.");
            Check(Equals(result["parent_membership_verified"], true) && Equals(result["ce_unchanged"], true), "Post-create membership and CE readback remain verified.");

            foreach (string invalid in new[] { (string)null, "", "a", new string('g', 64), new string('a', 65) })
            {
                CreateScenario.Reset(); bool rejected = false;
                try { Run(false, invalid); } catch (ArgumentException) { rejected = true; }
                Check(rejected && CreateScenario.Dispatches == 0 && CreateScenario.Creates == 0, "Missing/malformed execution tokens fail before dispatch.");
            }
            foreach (string change in new[] { "append", "remove", "reorder", "rename", "type", "actual-type" })
            {
                CreateScenario.Reset(); revision = Preview();
                if (change == "append") CreateScenario.Parent.Children.Add(CreateScenario.Element(8, "CYLI", CreateScenario.Parent));
                if (change == "remove") CreateScenario.Parent.Children.RemoveAt(0);
                if (change == "reorder") CreateScenario.Parent.Children.Reverse();
                if (change == "rename") CreateScenario.Parent.Children[0].Name = "/RENAMED";
                if (change == "type") CreateScenario.Parent.Children[0].Type = DbElementType.Find("CYLI");
                if (change == "actual-type") CreateScenario.Parent.Children[0].ActualType = DbElementType.Find(":CUSTOM");
                Conflict(revision, "Sibling " + change);
            }
            foreach (string change in new[] { "parent-native-pos", "parent-world-pos", "ancestor-pos", "frame-native-pos",
                "frame-world-pos", "frame-ori", "frame-owner", "frame-rule", "frame-identity", "creatable", "project", "mdb", "session" })
            {
                CreateScenario.Reset(); revision = Preview();
                if (change == "parent-native-pos") CreateScenario.Parent.NativePosition.X = 7;
                if (change == "parent-world-pos") CreateScenario.Parent.WorldPosition.Y = 7;
                if (change == "ancestor-pos") CreateScenario.Ancestor.NativePosition.Z = 7;
                if (change == "frame-native-pos") CreateScenario.Frame.NativePosition.X = 7;
                if (change == "frame-world-pos") CreateScenario.Frame.WorldPosition.Y = 7;
                if (change == "frame-ori") CreateScenario.Frame.WorldOrientation = Orientation.Rotated();
                if (change == "frame-owner") CreateScenario.Frame.Owner = CreateScenario.Parent;
                if (change == "frame-rule") CreateScenario.Frame.Rules = new[] { DbAttributeInstance.POS };
                if (change == "frame-identity") CreateScenario.Frame.Ref = new[] { 1, 90 };
                if (change == "creatable") CreateScenario.Parent.Creatable = false;
                if (change == "project") Project.CurrentProject.Name = "OTHER";
                if (change == "mdb") CreateScenario.Mdb.Name = "OTHER";
                if (change == "session") CreateScenario.Context = new E3d21Context();
                Conflict(revision, change);
            }
            foreach (string change in new[] { "sibling", "frame" })
            {
                CreateScenario.Reset(); revision = Preview(); CreateScenario.TypeReads = 0;
                CreateScenario.BeforeTypeRead = () =>
                {
                    if (CreateScenario.TypeReads != 2) return;
                    if (change == "sibling") CreateScenario.Parent.Children[0].Name = "/DURING-PREFLIGHT";
                    else CreateScenario.Frame.NativePosition.X = 19;
                };
                Conflict(revision, "Immediate pre-allocation " + change + " drift");
                Check(CreateScenario.TypeReads == 2, "Both production state reads ran before rejecting immediate drift.");
            }
            foreach (string change in new[] { "name", "attributes", "position" })
            {
                CreateScenario.Reset(); revision = Preview();
                result = Run(false, revision, name: change == "name" ? "/NEW" : "",
                    attributes: change == "attributes" ? new Dictionary<string, string> { ["XLEN"] = "12" } : null,
                    position: change == "position" ? new[] { 1d, 2, 3 } : null);
                Check(Equals(result["revision_conflict"], true) && CreateScenario.Creates == 0, "Changed request " + change + " cannot reuse a token.");
            }
            CreateScenario.Reset();
            var attributes1 = new Dictionary<string, string> { ["XLEN"] = "12", ["YLEN"] = "13" };
            var attributes2 = new Dictionary<string, string> { ["YLEN"] = "13", ["XLEN"] = "12" };
            var first = Run(true, attributes: attributes1); var second = Run(true, attributes: attributes2);
            Check(Equals(first["revision"], second["revision"]), "Canonical attribute ordering is stable across equivalent requests.");

            // A database's native WORLD need not be the MDB's first Design
            // WORLD. Both identities must be read from SDK databases, not
            // inferred from raw reference components or formatted aliases.
            CreateScenario.Reset(); CreateScenario.UseMultipleDatabases();
            Check(CreateScenario.Parent.Db.Number != CreateScenario.Parent.Ref[0],
                "The fixture keeps SDK database numbers independent of raw REF components.");
            Check(CreateScenario.Ancestor.Owner != CreateScenario.World,
                "The target owner chain ends at another valid member database WORLD.");
            revision = Preview();
            result = Run(false, revision, position: null);
            Check(Equals(result["success"], true) && Equals(result["readback_verified"], true),
                "Different WORLDs from two Design databases in the same MDB support creation.");
            Check(CreateScenario.Creates == 1 && CreateScenario.Cleanups == 0,
                "A valid multi-database workflow performs one creation without cleanup.");

            CreateScenario.Reset(); CreateScenario.UseMultipleDatabases();
            CreateScenario.Ancestor.Owner = CreateScenario.World;
            revision = Preview();
            result = Run(false, revision);
            Check(Equals(result["success"], true),
                "The SDK-selected, verified MDB WORLD can terminate a virtual cross-database owner chain.");

            CreateScenario.Reset(); CreateScenario.UseMultipleDatabases();
            CreateScenario.SecondDatabase.Number = CreateScenario.MainDatabase.Number;
            CreateScenario.SecondDatabase.ExtractNumber = CreateScenario.MainDatabase.ExtractNumber + 1;
            RejectedBeforeCreate(Run(true), "Referenced DB number has ambiguous extracts");
            RejectedBeforeCreate(Run(false, new string('a', 64)), "Ambiguous extract identity execution");

            CreateScenario.Reset(); CreateScenario.UseMultipleDatabases(includeUnrelated: true);
            CreateScenario.Mdb.Databases = CreateScenario.Mdb.Databases.Concat(new[] {
                CreateScenario.Database(303, 1, "UNRELATED-EXTRACT", 4) }).ToArray();
            revision = Preview();
            Check(Equals(Run(false, revision)["success"], true),
                "Unreferenced database extracts do not make a distinct target/frame database ambiguous.");

            CreateScenario.Reset(); CreateScenario.UseMultipleDatabases();
            Db equivalentWrapper = new Db { Number = CreateScenario.MainDatabase.Number,
                ExtractNumber = CreateScenario.MainDatabase.ExtractNumber, Name = CreateScenario.MainDatabase.Name,
                Type = DbType.Design, ReferenceNamespace = 1, World = CreateScenario.MainDatabase.World };
            CreateScenario.Parent.Db = equivalentWrapper; CreateScenario.Ancestor.Db = equivalentWrapper;
            revision = Preview();
            result = Run(false, revision);
            Check(Equals(result["success"], true),
                "Equivalent SDK database wrappers match by complete identity, not CLR object identity.");

            foreach (string invalid in new[] { "parent-outside-mdb", "frame-outside-mdb", "non-world-owner-other-db",
                "world-outside-mdb", "same-number-other-extract", "missing-parent-db", "missing-member-db",
                "null-mdb-array", "empty-mdb-array", "duplicate-db-identity", "db-read-error", "mdb-read-error",
                "missing-physical-world", "contradictory-physical-world", "nonzero-world-ref-element" })
            {
                CreateScenario.Reset(); CreateScenario.UseMultipleDatabases();
                Db outsider = CreateScenario.Database(909, 7, "OUTSIDE", 9);
                if (invalid == "parent-outside-mdb") CreateScenario.Parent.Db = outsider;
                if (invalid == "frame-outside-mdb") CreateScenario.Frame.Db = outsider;
                if (invalid == "non-world-owner-other-db") CreateScenario.Ancestor.Db = CreateScenario.SecondDatabase;
                if (invalid == "world-outside-mdb") CreateScenario.Ancestor.Owner = outsider.World;
                if (invalid == "same-number-other-extract")
                {
                    outsider.Number = CreateScenario.MainDatabase.Number;
                    outsider.ExtractNumber = CreateScenario.MainDatabase.ExtractNumber + 1;
                    CreateScenario.Parent.Db = outsider;
                }
                if (invalid == "missing-parent-db") CreateScenario.Parent.Db = null;
                if (invalid == "missing-member-db") CreateScenario.Mdb.Databases[0] = null;
                if (invalid == "null-mdb-array") CreateScenario.Mdb.Databases = null;
                if (invalid == "empty-mdb-array") CreateScenario.Mdb.Databases = new Db[0];
                if (invalid == "duplicate-db-identity")
                    CreateScenario.Mdb.Databases = new[] { CreateScenario.SecondDatabase,
                        CreateScenario.MainDatabase, CreateScenario.MainDatabase };
                if (invalid == "db-read-error") CreateScenario.MainDatabase.ThrowOnNumberRead = true;
                if (invalid == "mdb-read-error") CreateScenario.Mdb.ThrowOnRead = true;
                if (invalid == "missing-physical-world") CreateScenario.MainDatabase.World = null;
                if (invalid == "contradictory-physical-world")
                {
                    Db clone = CreateScenario.Database(CreateScenario.MainDatabase.Number,
                        CreateScenario.MainDatabase.ExtractNumber, CreateScenario.MainDatabase.Name, 8);
                    CreateScenario.Parent.Db = clone;
                    CreateScenario.Ancestor.Db = clone;
                }
                if (invalid == "nonzero-world-ref-element") CreateScenario.MainDatabase.World.Ref[1] = 999;
                RejectedBeforeCreate(Run(true), invalid + " preview");
                RejectedBeforeCreate(Run(false, new string('a', 64)), invalid + " execution");
            }

            foreach (string change in new[] { "append", "remove", "replace", "reorder", "name", "number", "extract", "type" })
            {
                CreateScenario.Reset(); CreateScenario.UseMultipleDatabases(includeUnrelated: true);
                revision = Preview();
                Db unrelated = CreateScenario.Mdb.Databases[2];
                if (change == "append") CreateScenario.Mdb.Databases = CreateScenario.Mdb.Databases
                    .Concat(new[] { CreateScenario.Database(404, 0, "APPENDED", 4) }).ToArray();
                if (change == "remove") CreateScenario.Mdb.Databases = CreateScenario.Mdb.Databases.Take(2).ToArray();
                if (change == "replace") CreateScenario.Mdb.Databases[2] = CreateScenario.Database(404, 0, "REPLACED", 4);
                if (change == "reorder") Array.Reverse(CreateScenario.Mdb.Databases);
                if (change == "name") unrelated.Name = "RENAMED";
                if (change == "number") unrelated.Number = 404;
                if (change == "extract") unrelated.ExtractNumber = 2;
                if (change == "type") unrelated.Type = DbType.Catalogue;
                Conflict(revision, "MDB membership " + change);
            }

            CreateScenario.Reset(); CreateScenario.UseMultipleDatabases(includeUnrelated: true);
            revision = Preview(); CreateScenario.TypeReads = 0;
            CreateScenario.BeforeTypeRead = () =>
            {
                if (CreateScenario.TypeReads == 2)
                    CreateScenario.Mdb.Databases[2].Name = "CHANGED-BEFORE-ALLOCATION";
            };
            Conflict(revision, "Immediate pre-allocation MDB identity change");
            Check(CreateScenario.TypeReads == 2, "Both state captures re-read the MDB before native creation.");

            // Match the local E3D21 observation: SITE reports IsWorld=true,
            // while the physical WORL returned by Db.World reports false.
            CreateScenario.Reset();
            CreateScenario.Ancestor.Type = DbElementType.Find("SITE");
            Check(CreateScenario.Ancestor.Type.IsWorld && !CreateScenario.World.Type.IsWorld,
                "The fixture matches native SITE=true and WORL=false IsWorld semantics.");
            revision = Preview();
            Check(Equals(Run(false, revision)["success"], true) && CreateScenario.Creates == 1,
                "Creation walks through SITE and terminates at the exact database WORL despite IsWorld metadata.");
            CreateScenario.Reset();
            CreateScenario.World.Type = DbElementType.Find("SITE");
            RejectedBeforeCreate(Run(true), "SITE at element zero is not a physical WORLD");
            Console.WriteLine("Model create revision production checks passed: " + checks);
            return 0;
        }
        catch (Exception failure) { Console.Error.WriteLine(failure); return 1; }
    }
}

internal static class CreateScenario
{
    internal static E3d21Context Context;
    internal static DbElement Parent, Frame, World, Ancestor;
    internal static MDB Mdb;
    internal static Db MainDatabase, SecondDatabase;
    internal static int Creates, Setters, Cleanups, Dispatches, TypeReads;
    internal static Action BeforeTypeRead;
    internal static DbElement Element(int number, string type, DbElement owner)
    {
        Db database = owner == null ? MainDatabase : owner.Db;
        return new DbElement { Ref = new[] { database == null ? 1 : database.ReferenceNamespace, number },
            Type = DbElementType.Find(type), Name = "/E" + number, Owner = owner, Db = database };
    }
    internal static Db Database(int number, int extract, string name, int referenceNamespace)
    {
        var database = new Db { Number = number, ExtractNumber = extract, Name = name, Type = DbType.Design,
            ReferenceNamespace = referenceNamespace };
        database.World = new DbElement { Ref = new[] { referenceNamespace, 0 }, Type = DbElementType.Find("WORL"),
            Name = "/*", Db = database };
        return database;
    }
    internal static void UseMultipleDatabases(bool includeUnrelated = false)
    {
        SecondDatabase = Database(202, 0, "SECOND", 2);
        World = SecondDatabase.World;
        Frame.Db = SecondDatabase; Frame.Owner = World; Frame.Ref = new[] { 2, 3 };
        Mdb.Databases = includeUnrelated
            ? new[] { SecondDatabase, MainDatabase, Database(303, 0, "UNRELATED", 3) }
            : new[] { SecondDatabase, MainDatabase };
    }
    internal static void Reset()
    {
        Creates = Setters = Cleanups = Dispatches = TypeReads = 0; BeforeTypeRead = null;
        MainDatabase = Database(101, 0, "MAIN", 1); SecondDatabase = null;
        World = MainDatabase.World; Ancestor = Element(1, "EQUI", World);
        Parent = Element(2, "SUBE", Ancestor); Frame = Element(3, "EQUI", World);
        Parent.Children.Add(Element(4, "BOX", Parent)); Parent.Children.Add(Element(5, "BOX", Parent));
        Mdb = new MDB { Name = "ALL", Databases = new[] { MainDatabase } };
        Project.CurrentProject = new Project { Name = "PROJECT" };
        Context = new E3d21Context();
    }
}

namespace E3d21AiBridge
{
    public sealed partial class E3d21Context
    {
        private const int ModelMembershipVerificationLimit = 10000;
        private readonly string createRevisionSession = Guid.NewGuid().ToString("N");
        // PRODUCTION_CREATE_METHODS
        private static T NativeRead<T>(Func<T> action) { return action(); }
        private static void NativeWrite(Action action) { action(); }
        private static void ThrowIfNativeFaulted() { }
        private static bool IsCommandActive() { return false; }
        private static bool TryBeginCommand(string command) { return true; }
        private static void EndCommand() { }
        private static object BusyResult(string command) { throw new Exception("Unexpected busy fixture."); }
        private static T OnUi<T>(Func<T> action) { CreateScenario.Dispatches++; return action(); }
        private static string ModelCurrentReference() { return "=1/2"; }
        private static MDB CurrentMdb() { return CreateScenario.Mdb; }
        private static DbElement ModelRequireElement(string target) { return CreateScenario.Parent; }
        private static bool IsUsable(DbElement element) { return element != null && !element.IsNull; }
        private static IEnumerable<DbElement> DirectSiblings(DbElement parent, object ignored) { return parent.Children; }
        private static DbElement ModelFrame(string requested, DbElement parent)
        {
            return requested == "WORLD" ? CreateScenario.Mdb.GetFirstWorld(DbType.Design) : requested == "OWNER" ? parent : CreateScenario.Frame;
        }
        private static DbElement ModelStorageFrame(DbElement parent) { return parent; }
        private sealed class ManagedFrameTransform { internal ModelEditInput ToNative(ModelEditInput input) { return input; } }
        private static ManagedFrameTransform ModelStorageTransform(DbElement from, DbElement to) { return new ManagedFrameTransform(); }
        private static void ModelRequireSettable(DbElement element, DbAttribute attribute) { }
        private static void ModelRequireSetter(bool ok, PdmsMessage message, string name) { if (!ok) throw new Exception("Setter failed."); }
        private static void ModelRequireStorageFrame(DbElement element, DbElement frame, ModelEditInput input) { }
        private static void ModelWritePosition(DbElement element, Position value) { CreateScenario.Setters++; element.NativePosition = element.WorldPosition = value; }
        private static void ModelWriteOrientation(DbElement element, Orientation value) { CreateScenario.Setters++; element.NativeOrientation = element.WorldOrientation = value; }
        private static void ModelCleanupCreated(Dictionary<string, object> result, DbElement created, int[] captured,
            string parent, string[] previous, bool empty)
        {
            CreateScenario.Cleanups++;
            throw new Exception("Guard/success cases must not enter cleanup: " + result["error"]);
        }
    }
}

namespace Aveva.Core.Database
{
    internal enum DbType { Design = 1, Catalogue = 2 }
    internal enum AttributeType { STRING, DOUBLE }
    internal sealed class DbAttribute
    {
        internal string ShortName;
        internal bool IsValid = true, IsArray;
        internal AttributeType Type;
        internal static DbAttribute GetDbAttribute(string name)
        {
            return new DbAttribute { ShortName = name.ToUpperInvariant(), Type = name == "NAME" ? AttributeType.STRING : AttributeType.DOUBLE };
        }
    }
    internal static class DbAttributeInstance
    {
        internal static readonly DbAttribute NAME = DbAttribute.GetDbAttribute("NAME"), POS = DbAttribute.GetDbAttribute("POS"), ORI = DbAttribute.GetDbAttribute("ORI");
    }
    internal sealed class DbQualifier { internal DbElement wrtQualifier; }
    internal sealed class DbElementType
    {
        private static readonly Dictionary<string, DbElementType> types = new Dictionary<string, DbElementType>();
        internal string Name, ShortName;
        internal bool IsValid = true;
        internal bool IsWorld { get { return ShortName == "SITE"; } }
        internal int[] DatabaseTypes() { return new[] { (int)DbType.Design }; }
        internal bool IsAttributeValid(DbAttribute attribute) { return true; }
        internal static DbElementType Find(string name)
        {
            DbElementType type;
            if (!types.TryGetValue(name, out type)) types[name] = type = new DbElementType { Name = name, ShortName = name };
            return type;
        }
        internal static DbElementType GetElementType(string name)
        {
            CreateScenario.TypeReads++; if (CreateScenario.BeforeTypeRead != null) CreateScenario.BeforeTypeRead();
            return Find(name);
        }
    }
    internal sealed class DbElement
    {
        internal int[] Ref;
        internal DbElementType Type, ActualType;
        internal string Name;
        internal DbElement Owner;
        internal Db Db;
        internal bool Creatable = true;
        internal bool IsNull { get { return false; } }
        internal DbType DbType { get { return Db == null ? DbType.Design : Db.Type; } }
        internal readonly List<DbElement> Children = new List<DbElement>();
        internal Position NativePosition = Position.Create(0, 0, 0), WorldPosition = Position.Create(0, 0, 0);
        internal Orientation NativeOrientation = new Orientation(), WorldOrientation = new Orientation();
        internal DbAttribute[] Rules = new DbAttribute[0];
        internal DbElementType GetElementType() { return Type; }
        internal DbElementType GetActualType() { return ActualType ?? Type; }
        internal bool IsCreatable(DbElementType type) { return Creatable; }
        internal bool IsAttributeValid(DbAttribute attribute)
        { return attribute.ShortName == "NAME" || (Type.ShortName != "WORL" && Type.ShortName != "WORLD"); }
        internal string GetString(DbAttribute attribute) { return Name; }
        internal double GetDouble(DbAttribute attribute) { return 0; }
        internal DbAttribute[] GetAttributesWithRules() { return Rules; }
        internal Position GetPosition(DbAttribute attribute, DbQualifier qualifier = null) { return qualifier == null ? NativePosition : WorldPosition; }
        internal Orientation GetOrientation(DbAttribute attribute, DbQualifier qualifier = null) { return qualifier == null ? NativeOrientation : WorldOrientation; }
        internal DbElement CreateLast(DbElementType type)
        {
            CreateScenario.Creates++; var child = CreateScenario.Element(100, type.ShortName, this); Children.Add(child); return child;
        }
        internal bool SetAttribute(DbAttribute attribute, DbQualifier qualifier, string value, out PdmsMessage message)
        {
            CreateScenario.Setters++; Name = value; message = null; return true;
        }
        internal bool SetAttribute(DbAttribute attribute, DbQualifier qualifier, double value, out PdmsMessage message)
        {
            CreateScenario.Setters++; message = null; return true;
        }
    }
    internal sealed class Db
    {
        private int number;
        internal int Number { get { if (ThrowOnNumberRead) throw new InvalidOperationException("DB number unavailable"); return number; }
            set { number = value; } }
        internal int ExtractNumber, ReferenceNamespace;
        internal string Name;
        internal DbType Type;
        internal DbElement World;
        internal bool ThrowOnNumberRead;
    }
    internal sealed class MDB
    {
        internal string Name;
        internal Db[] Databases;
        internal bool ThrowOnRead;
        internal Db[] GetDBArray()
        {
            if (ThrowOnRead) throw new InvalidOperationException("MDB database list unavailable");
            return Databases;
        }
        internal DbElement GetFirstWorld(DbType type) { return Databases.First(database => database.Type == type).World; }
    }
    internal sealed class Project { internal static Project CurrentProject; internal string Name; }
}
namespace Aveva.Core.Utilities.Messaging { internal sealed class PdmsMessage { } }
namespace Aveva.Core.Geometry
{
    internal enum Axis { X, Y }
    internal sealed class Position
    {
        internal double X, Y, Z;
        internal static Position Create(double x, double y, double z) { return new Position { X = x, Y = y, Z = z }; }
    }
    internal sealed class Direction
    {
        internal double East, North, Up;
        internal static Direction Create(Position value) { return new Direction { East = value.X, North = value.Y, Up = value.Z }; }
    }
    internal sealed class Orientation
    {
        private Direction x = Direction.Create(Position.Create(1, 0, 0)), y = Direction.Create(Position.Create(0, 1, 0));
        internal Direction XDir() { return x; }
        internal Direction YDir() { return y; }
        internal Direction ZDir() { return Direction.Create(Position.Create(x.North * y.Up - x.Up * y.North, x.Up * y.East - x.East * y.Up, x.East * y.North - x.North * y.East)); }
        internal static Orientation Create(Axis first, Direction x, Axis second, Direction y) { return new Orientation { x = x, y = y }; }
        internal static Orientation Rotated() { return Create(Axis.X, Direction.Create(Position.Create(0, 1, 0)), Axis.Y, Direction.Create(Position.Create(-1, 0, 0))); }
    }
}
