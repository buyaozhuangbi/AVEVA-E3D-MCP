using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;
using Aveva.Core.Database;

namespace E3d21AiBridge;

public sealed partial class E3d21Context : IE3d21ParagonReferenceTraceContext
{
    private static readonly Regex ParagonTraceType = new Regex(@"\A[A-Z][A-Z0-9_]{0,63}\z");

    public object ReadParagonReferenceTrace(string refno, int traceLimit, string sessionId)
    {
        var request = ParagonReferenceTraceRequest.Validate(refno, traceLimit);
        if (!ParagonTraceIdentityText(sessionId))
            throw new ArgumentException("A printable server-owned session identity of at most 256 UTF16 units is required.", nameof(sessionId));
        string build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
        var trace = new ParagonTrace(request.TraceLimit);
        // This managed latch check must precede even the busy outcome.
        if (nativeFaultState != null && nativeFaultState.HasFault) throw new NativeDatabaseFaultException();
        if (IsCommandActive()) return trace.Result("busy");
        return OnUi(() =>
        {
            if (IsCommandActive()) return trace.Result("busy");
            var before = ParagonTraceBoundary.Read(sessionId, build);
            if (before == null) return trace.Result("context_unverified");
            trace.Start(request.Refno, request.Pair);
            var after = ParagonTraceBoundary.Read(sessionId, build);
            bool? sameProject = after == null ? (bool?)null : ReferenceEquals(before.ProjectHandle, after.ProjectHandle);
            bool? sameMdb = after == null ? (bool?)null : ReferenceEquals(before.MdbHandle, after.MdbHandle);
            bool? agreement = after == null ? (bool?)null : before.AgreesWith(after);
            var context = new Dictionary<string, object>
            {
                ["before"] = before.Sample, ["after"] = after?.Sample,
                ["same_project_instance"] = sameProject, ["same_mdb_instance"] = sameMdb,
                ["boundary_agreement"] = agreement
            };
            // Attempts stay counted, but no graph identities cross a lost boundary.
            return trace.Result(agreement == true ? null : "context_unverified", context);
        });
    }

    private static bool ParagonTraceIdentityText(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Length > 256) return false;
        for (int index = 0; index < value.Length; index++)
        {
            char character = value[index];
            UnicodeCategory category = CharUnicodeInfo.GetUnicodeCategory(value, index);
            if (category == UnicodeCategory.Control || category == UnicodeCategory.Format
                || category == UnicodeCategory.LineSeparator || category == UnicodeCategory.ParagraphSeparator
                || category == UnicodeCategory.SpaceSeparator && character != ' '
                || category == UnicodeCategory.PrivateUse || category == UnicodeCategory.OtherNotAssigned) return false;
            if (char.IsHighSurrogate(character))
            {
                if (index + 1 == value.Length || !char.IsLowSurrogate(value[index + 1])) return false;
                index++;
            }
            else if (char.IsLowSurrogate(character)) return false;
        }
        return true;
    }

    private static bool ParagonTraceNullPair(int[] pair) => pair != null && pair.Length == 2 && pair[0] == 0 && pair[1] == 0;

    private static string ParagonTraceFormat(int[] pair) => pair != null && pair.Length == 2 && pair[0] > 0 && pair[1] >= 0
        ? "=" + pair[0].ToString(CultureInfo.InvariantCulture) + "/" + pair[1].ToString(CultureInfo.InvariantCulture)
        : null;

    private sealed class ParagonTraceBoundary
    {
        internal Project ProjectHandle;
        internal MDB MdbHandle;
        internal Dictionary<string, object> Sample;

        internal static ParagonTraceBoundary Read(string sessionId, string build)
        {
            try
            {
                var project = NativeRead(() => Project.CurrentProject);
                var mdb = NativeRead(() => MDB.CurrentMDB);
                if (ReferenceEquals(project, null) || ReferenceEquals(mdb, null)) return null;
                var projectName = NativeRead(() => project.Name);
                var mdbName = NativeRead(() => mdb.Name);
                if (!ParagonTraceIdentityText(projectName) || !ParagonTraceIdentityText(mdbName)) return null;
                var ce = NativeRead(() => Aveva.Core.Database.CurrentElement.Element);
                // A failed or unverified CE read is different from a native null CE.
                if (ReferenceEquals(ce, null)) return null;
                bool isNull = NativeRead(() => ce.IsNull);
                var pair = NativeRead(() => ce.Ref);
                string reference = ParagonTraceFormat(pair);
                if (isNull ? !ParagonTraceNullPair(pair) : reference == null || !NativeRead(() => ce.IsValid) || NativeRead(() => ce.IsDeleted))
                    return null;
                Dictionary<string, object> identity = null;
                if (!isNull)
                {
                    var type = NativeRead(() => ce.ElementType); // SDK base noun, including UDET instances.
                    if (ReferenceEquals(type, null) || !NativeRead(() => type.IsValid)) return null;
                    string noun = NativeRead(() => type.ShortName);
                    if (noun == null || !ParagonTraceType.IsMatch(noun)) return null;
                    int code = (int)NativeRead(() => ce.DbType);
                    identity = new Dictionary<string, object> { ["refno"] = reference, ["type"] = noun, ["db_type_code"] = code };
                }
                return new ParagonTraceBoundary
                {
                    ProjectHandle = project, MdbHandle = mdb,
                    Sample = new Dictionary<string, object>
                    {
                        ["session_id"] = sessionId, ["bridge_build"] = build,
                        ["project"] = projectName, ["mdb"] = mdbName, ["current_element"] = identity
                    }
                };
            }
            catch (Exception)
            {
                ThrowIfNativeFaulted();
                return null;
            }
        }

        internal bool AgreesWith(ParagonTraceBoundary after)
        {
            if (after == null || !ReferenceEquals(ProjectHandle, after.ProjectHandle) || !ReferenceEquals(MdbHandle, after.MdbHandle)) return false;
            foreach (string key in new[] { "session_id", "bridge_build", "project", "mdb" })
                if (!Equals(Sample[key], after.Sample[key])) return false;
            var ce = Sample["current_element"] as Dictionary<string, object>;
            var other = after.Sample["current_element"] as Dictionary<string, object>;
            if (ce == null || other == null) return ce == null && other == null;
            return Equals(ce["refno"], other["refno"]) && Equals(ce["type"], other["type"])
                && Equals(ce["db_type_code"], other["db_type_code"]);
        }
    }

    private sealed class ParagonTrace
    {
        private const string Root = "root";
        private readonly int limit;
        private readonly Dictionary<string, Row> rows = new Dictionary<string, Row>(StringComparer.Ordinal);
        private readonly Dictionary<string, int> dbTypeCodes = new Dictionary<string, int>(StringComparer.Ordinal);
        private readonly HashSet<string> active = new HashSet<string>(StringComparer.Ordinal);
        private bool limitReached;

        internal ParagonTrace(int limit) { this.limit = limit; }

        private sealed class Row
        {
            internal string Ref, Type, Owner, Status = "unresolved";
            internal Dictionary<string, object> ToResult() => new Dictionary<string, object>
                { ["ref"] = Ref, ["type"] = Type, ["owner"] = Owner, ["status"] = Status };
        }

        internal void Start(string requested, int[] pair)
        {
            Visit(Root, _ => NativeRead(() => DbElement.GetElement(pair)), RootAllowed, requested);
        }

        internal Dictionary<string, object> Result(string unavailable = null, Dictionary<string, object> context = null)
        {
            bool rootObserved = rows.TryGetValue(Root, out Row root) && (root.Status == "resolved" || root.Status == "owner_read_error");
            string status = unavailable ?? (!rootObserved ? "unavailable" :
                limitReached || rows.Values.Any(row => row.Status != "resolved" && row.Status != "unset") ? "partial" : "observed");
            return new Dictionary<string, object>
            {
                ["status"] = status,
                ["trace_limit"] = new Dictionary<string, object> { ["limit"] = limit, ["used"] = rows.Count, ["reached"] = limitReached },
                ["trace"] = unavailable == null ? rows.ToDictionary(item => item.Key, item => item.Value.ToResult(), StringComparer.Ordinal)
                    : new Dictionary<string, Dictionary<string, object>>(StringComparer.Ordinal),
                ["context"] = context ?? new Dictionary<string, object>
                {
                    ["before"] = null, ["after"] = null, ["same_project_instance"] = null,
                    ["same_mdb_instance"] = null, ["boundary_agreement"] = null
                },
                ["db_type_codes"] = unavailable == null ? dbTypeCodes : new Dictionary<string, int>(StringComparer.Ordinal)
            };
        }

        private static bool RootAllowed(DbType db, string type) => db == DbType.Design ||
            db == DbType.Catalogue && (type == "SPEC" || type == "SPCO" || type == "GPART" || type == "CATA" ||
                type == "SECT" || type == "STSE" || type == "CATE" || type == "STCA" || IsCatalogueComponent(type)) ||
            db == DbType.Property && (type == "SOLI" || type == "FLUI" || IsPropertyComponent(type));

        private static bool IsCatalogueComponent(string type) => type == "SCOM" || type == "SPRF" || type == "SFIT" || type == "JOIN";
        private static bool IsPropertyComponent(string type) => type == "CMPD" || type == "TUBD" || type == "CABD";

        private void Visit(string path, Func<Row, DbElement> read, Func<DbType, string, bool> inScope, string requested = null)
        {
            if (rows.Count == limit) { limitReached = true; return; }
            var row = new Row { Ref = requested };
            rows.Add(path, row);

            DbElement target;
            DbType db;
            try
            {
                target = read(row);
                if (ReferenceEquals(target, null)) return;
                bool isNull = NativeRead(() => target.IsNull);
                var pair = NativeRead(() => target.Ref);
                if (isNull && ParagonTraceNullPair(pair))
                {
                    if (requested == null) row.Status = "unset";
                    return;
                }
                string reference = ParagonTraceFormat(pair);
                if (reference == null) { row.Status = "read_error"; return; }
                if (requested != null && reference != requested) { row.Status = "reference_mismatch"; return; }
                row.Ref = reference;
                if (isNull || !NativeRead(() => target.IsValid) || NativeRead(() => target.IsDeleted)) return;
                var type = NativeRead(() => target.ElementType); // native base noun for UDETs
                if (ReferenceEquals(type, null) || !NativeRead(() => type.IsValid)) throw new InvalidOperationException();
                string noun = NativeRead(() => type.ShortName);
                if (noun == null || !ParagonTraceType.IsMatch(noun)) throw new InvalidOperationException();
                row.Type = noun;
                if (active.Contains(reference)) { row.Status = "cycle"; return; }
                db = NativeRead(() => target.DbType);
                dbTypeCodes.Add(path, (int)db);
                if (!inScope(db, noun)) { row.Status = "out_of_scope"; return; }
                row.Status = "resolved";
            }
            catch (Exception)
            {
                ThrowIfNativeFaulted();
                row.Status = "read_error";
                return;
            }

            var owner = ReadOwner(target, row);
            active.Add(row.Ref);
            try { Follow(path, target, db, row, owner); }
            finally { active.Remove(row.Ref); }
        }

        private static DbElement ReadOwner(DbElement target, Row row)
        {
            try
            {
                var owner = NativeRead(() => target.Owner);
                if (ReferenceEquals(owner, null)) throw new InvalidOperationException();
                bool isNull = NativeRead(() => owner.IsNull);
                var pair = NativeRead(() => owner.Ref);
                if (isNull && ParagonTraceNullPair(pair)) return null;
                string reference = ParagonTraceFormat(pair);
                if (reference == null) throw new InvalidOperationException();
                row.Owner = reference;
                return owner;
            }
            catch (Exception)
            {
                ThrowIfNativeFaulted();
                row.Status = "owner_read_error";
                return null;
            }
        }

        private void Attribute(string path, DbElement source, string name, Func<DbAttribute> definition,
            DbType expectedDb, Func<string, bool> allowedNoun)
        {
            Visit(path + "." + name, row =>
            {
                var attr = NativeRead(definition);
                if (ReferenceEquals(attr, null) || !NativeRead(() => attr.IsValid) || NativeRead(() => attr.Type) != DbAttributeType.ELEMENT ||
                    !NativeRead(() => source.IsAttributeValid(attr)))
                {
                    row.Status = "attribute_unavailable";
                    return null;
                }
                return NativeRead(() => source.GetElement(attr));
            }, (db, kind) => db == expectedDb && allowedNoun(kind));
        }

        private void Owner(string path, DbElement targetOwner, Row source, Func<string, bool> allowedNoun)
        {
            Visit(path + ".OWNER", row =>
            {
                if (ReferenceEquals(targetOwner, null))
                    row.Status = source.Status == "owner_read_error" ? "owner_read_error" : "owner_missing";
                return targetOwner;
            }, (db, kind) => db == DbType.Catalogue && allowedNoun(kind), source.Owner);
        }

        private void Follow(string path, DbElement target, DbType db, Row row, DbElement owner)
        {
            if (db == DbType.Design)
            {
                Attribute(path, target, "SPREF", () => DbAttributeInstance.SPRE, DbType.Catalogue,
                    kind => kind == "SPCO" || kind == "SCOM" && (row.Type == "NOZZ" || row.Type == "ELCONN"));
                return;
            }
            if (db != DbType.Catalogue) return;
            switch (row.Type)
            {
                case "SPCO":
                    Attribute(path, target, "CATREF", () => DbAttributeInstance.CATR, DbType.Catalogue, kind => kind == "SCOM");
                    Attribute(path, target, "PRTREF", () => DbAttributeInstance.PRTREF, DbType.Catalogue, kind => kind == "GPART");
                    Attribute(path, target, "CMPREF", () => DbAttributeInstance.CMPR, DbType.Property, kind => kind == "CMPD" || kind == "TUBD");
                    goto case "SELE";
                case "SELE":
                    Owner(path, owner, row, kind => kind == "SELE" || kind == "SPEC");
                    break;
                case "GPART":
                    Attribute(path, target, "CATREF", () => DbAttributeInstance.CATR, DbType.Catalogue, IsCatalogueComponent);
                    Attribute(path, target, "CMPREF", () => DbAttributeInstance.CMPR, DbType.Property, IsPropertyComponent);
                    break;
                case "SPEC":
                    Attribute(path, target, "MATREF", () => DbAttributeInstance.MATR, DbType.Property, kind => kind == "SOLI");
                    Attribute(path, target, "FLUREF", () => DbAttributeInstance.FLUR, DbType.Property, kind => kind == "FLUI");
                    break;
                case "SCOM":
                    Owner(path, owner, row, kind => kind == "CATE" || kind == "SECT");
                    break;
                case "SPRF":
                case "SFIT":
                case "JOIN":
                    Owner(path, owner, row, kind => kind == "STCA" || kind == "STSE");
                    break;
                case "CATE":
                    Owner(path, owner, row, kind => kind == "SECT");
                    break;
                case "STCA":
                    Owner(path, owner, row, kind => kind == "STSE");
                    break;
                case "SECT":
                case "STSE":
                    Owner(path, owner, row, kind => kind == "CATA");
                    break;
            }
        }
    }
}
