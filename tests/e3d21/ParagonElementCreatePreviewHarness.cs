using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using E3d21AiBridge;

namespace Aveva.Core.Database
{
    public enum DbType { Design = 1, Catalogue = 2 }

    public sealed class DbElementType
    {
        public string Name { get; private set; }
        public string ShortName { get { return Name; } }
        public bool IsValid { get; set; } = true;
        public DbElementType[] Members { get; set; } = new DbElementType[0];
        public bool FailMemberTypes { get; set; }
        private static readonly Dictionary<string, DbElementType> Types = new Dictionary<string, DbElementType>(StringComparer.Ordinal);
        public DbElementType(string name) { Name = name; Types[name] = this; }
        public DbElementType[] MemberTypes()
        {
            if (FailMemberTypes) throw new InvalidOperationException("synthetic MemberTypes read failure");
            return Members;
        }
        public static DbElementType GetElementType(string name)
        {
            DbElementType value;
            if (!Types.TryGetValue(name, out value)) value = new DbElementType(name);
            return value;
        }
        public static bool operator ==(DbElementType left, DbElementType right) { return ReferenceEquals(left, right); }
        public static bool operator !=(DbElementType left, DbElementType right) { return !ReferenceEquals(left, right); }
        public override bool Equals(object other) { return ReferenceEquals(this, other); }
        public override int GetHashCode() { return Name.GetHashCode(); }
    }

    public sealed class DbElement
    {
        public int[] Ref { get; set; }
        public string Name { get; set; } = "/CAT/SECTION";
        public DbType DbType { get; set; }
        public DbElementType ElementType { get; set; }
        public bool FailIsCreatable { get; set; }
        public readonly Dictionary<string, bool> Creatable = new Dictionary<string, bool>(StringComparer.Ordinal);
        public int IsCreatableCalls { get; private set; }
        public DbElementType GetElementType() { return ElementType; }
        public string GetAsString(DbAttribute attribute) { return Name; }
        public bool IsCreatable(DbElementType type)
        {
            IsCreatableCalls++;
            if (FailIsCreatable) throw new InvalidOperationException("synthetic IsCreatable read failure");
            bool value;
            return Creatable.TryGetValue(type.Name, out value) && value;
        }
    }

    public sealed class DbAttribute { }
    public static class DbAttributeInstance { public static readonly DbAttribute NAME = new DbAttribute(); }
}

internal static class ParagonElementCreatePreviewHarness
{
    private static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    private static JObject Object(object value) { return JObject.FromObject(value); }

    public static int Main()
    {
        var sect = Aveva.Core.Database.DbElementType.GetElementType("SECT");
        Aveva.Core.Database.DbElementType.GetElementType("CATA");
        Aveva.Core.Database.DbElementType.GetElementType("CATE");
        var parent = new Aveva.Core.Database.DbElement
        {
            Ref = new[] { 1, 100 }, DbType = Aveva.Core.Database.DbType.Catalogue,
            ElementType = new Aveva.Core.Database.DbElementType("CATA")
        };
        parent.ElementType.Members = new[] { sect };
        parent.Creatable["CATA"] = false;
        parent.Creatable["SECT"] = true;
        parent.Creatable["CATE"] = false;

        JObject result = Object(ParagonElementCreatePreview.Read("=1/100", "SECT", parent));
        JObject candidate = (JObject)result["candidate"];
        Require((string)candidate["type"] == "SECT" && (bool)candidate["member_type"]
            && (bool)candidate["sdk_is_creatable_now"], "SECT observation");
        Require((bool)result["read_only"] && (bool)result["preview_only"] && !(bool)result["native_side_effects"], "preview contract");
        Require(result["scope"]["module"].Type == JTokenType.Null
            && result["scope"]["permissions_verified"].Type == JTokenType.Null
            && result["scope"]["revision"].Type == JTokenType.Null, "unknown scope fields");

        JObject cataPreview = Object(ParagonElementCreatePreview.Read("=1/100", "CATA", parent));
        Require((bool)cataPreview["candidate"]["member_type"] == false
            && (bool)cataPreview["candidate"]["sdk_is_creatable_now"] == false, "CATA observation");
        JObject catePreview = Object(ParagonElementCreatePreview.Read("=1/100", "CATE", parent));
        Require((bool)catePreview["candidate"]["member_type"] == false
            && (bool)catePreview["candidate"]["sdk_is_creatable_now"] == false, "CATE observation");

        parent.ElementType.FailMemberTypes = true;
        JObject memberFailure = Object(ParagonElementCreatePreview.Read("=1/100", "SECT", parent));
        JObject sectFailure = (JObject)memberFailure["candidate"];
        Require(sectFailure["member_type"].Type == JTokenType.Null, "failed MemberTypes stays unknown");
        Require(sectFailure["sdk_is_creatable_now"].Type == JTokenType.Boolean
            && (bool)sectFailure["sdk_is_creatable_now"] == true, "MemberTypes failure does not overwrite independent IsCreatable observation");
        Require(!(bool)memberFailure["complete"], "partial preview is incomplete");

        parent.ElementType.FailMemberTypes = false;
        parent.FailIsCreatable = true;
        JObject createReadFailure = Object(ParagonElementCreatePreview.Read("=1/100", "SECT", parent));
        Require(createReadFailure["candidate"]["sdk_is_creatable_now"].Type == JTokenType.Null,
            "failed IsCreatable stays unknown");
        Require(!(bool)createReadFailure["complete"], "IsCreatable failure remains partial");

        JObject unresolved = Object(ParagonElementCreatePreview.Read("=1/100", "SECT", null));
        Require(unresolved["candidate"]["member_type"].Type == JTokenType.Null
            && unresolved["candidate"]["sdk_is_creatable_now"].Type == JTokenType.Null,
            "unresolved target remains unknown");
        Require(parent.IsCreatableCalls == 5, "only read-only IsCreatable probes ran");

        bool rejected = false;
        try { ParagonElementCreatePreview.Read("CE", "SECT", parent); }
        catch (ArgumentException) { rejected = true; }
        Require(rejected, "implicit CE rejected");
        rejected = false;
        try { ParagonElementCreatePreview.Read("=1/100", "LIBY", parent); }
        catch (ArgumentException) { rejected = true; }
        Require(rejected, "unsupported candidate type rejected");
        rejected = false;
        try { ParagonElementCreatePreview.Read("=2147483648/1", "SECT", parent); }
        catch (ArgumentException) { rejected = true; }
        Require(rejected, "out-of-range explicit numeric target rejected");
        rejected = false;
        try { ParagonElementCreatePreview.Read("/CAT/SECTION WITH SPACE", "SECT", parent); }
        catch (ArgumentException) { rejected = true; }
        Require(rejected, "ambiguous whitespace target rejected");
        Console.WriteLine("PARAGON element-create preview harness passed");
        return 0;
    }
}
