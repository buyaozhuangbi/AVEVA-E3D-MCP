using System;
using System.Collections.Generic;
using System.Globalization;
using Aveva.Core.Database;
using Aveva.Core.Utilities.ModuleInfo;

namespace E3d21AiBridge;

public sealed partial class E3d21Context : IE3d21ModuleContext
{
    private const int ModuleContextTextLimit = 256;
    private const int ModuleContextErrorLimit = 24;
    private const int ModuleContextErrorTextLimit = 320;

    public object ReadModuleContext(string sessionId)
    {
        if (!Guid.TryParseExact(sessionId, "D", out _))
            throw new ArgumentException("A canonical bridge session ID is required.", nameof(sessionId));

        string build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D");
        if (IsCommandActive()) throw new InvalidOperationException("E3D is busy.");

        return OnUi(delegate
        {
            if (IsCommandActive()) throw new InvalidOperationException("E3D is busy.");
            var errors = new List<Dictionary<string, object>>();
            var before = ObserveModuleContextBoundary(sessionId, build, "before", errors);
            // A new native fault aborts here; never start the after observation.
            ThrowIfNativeFaulted();
            var after = ObserveModuleContextBoundary(sessionId, build, "after", errors);
            ThrowIfNativeFaulted();

            bool? unchanged = ModuleContextAgreement(before, after);
            bool successful = unchanged == true && errors.Count == 0;
            bool complete = successful;
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["operation"] = "module_context",
                ["available"] = true,
                ["success"] = successful,
                ["complete"] = complete,
                ["read_only"] = true,
                ["single_dispatch"] = true,
                ["atomic_snapshot"] = false,
                ["module"] = after.Module,
                ["context"] = new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["before"] = before.ToResult(),
                    ["after"] = after.ToResult(),
                    ["unchanged"] = unchanged
                },
                ["scope"] = new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["kind"] = "active_module_identity",
                    ["authorization_status"] = "unknown",
                    ["admin_database_creation_permission_verified"] = null,
                    ["paragon_write_permission_verified"] = null,
                    ["database_lock_status"] = "not_assessed"
                },
                ["read_errors"] = errors
            };
        });
    }

    private static ModuleContextBoundary ObserveModuleContextBoundary(
        string sessionId, string build, string boundary, List<Dictionary<string, object>> errors)
    {
        var snapshot = new ModuleContextBoundary
        {
            SessionId = sessionId,
            BridgeBuild = build,
            Module = ObserveModule(boundary, errors),
            Project = ObserveProject(boundary, errors),
            Mdb = ObserveMdb(boundary, errors),
            CurrentElement = ObserveCurrentElement(boundary, errors)
        };
        return snapshot;
    }

    private static Dictionary<string, object> ObserveModule(string boundary, List<Dictionary<string, object>> errors)
    {
        Module module = ReadModuleContextValue(
            () => new ModuleManager().CurrentModule, boundary + ".module", errors, (Module)null);
        if (module == null)
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["status"] = errors.Exists(item => item["stage"].Equals(boundary + ".module")) ? "unknown" : "absent",
                ["name"] = null,
                ["number"] = null,
                ["description"] = null
            };
        }

        string name = ReadModuleContextText(() => module.Name, boundary + ".module.name", errors, true);
        int? number = ReadModuleContextNumber(() => module.Number, boundary + ".module.number", errors);
        string description = ReadModuleContextText(() => module.Description, boundary + ".module.description", errors, false);
        return new Dictionary<string, object>(StringComparer.Ordinal)
        {
            ["status"] = name != null && number.HasValue ? "present" : "unknown",
            ["name"] = name, ["number"] = number, ["description"] = description
        };
    }

    private static Dictionary<string, object> ObserveProject(string boundary, List<Dictionary<string, object>> errors)
    {
        Project project = ReadModuleContextValue(() => Project.CurrentProject, boundary + ".project", errors, (Project)null);
        if (project == null)
        {
            AddModuleContextError(errors, boundary + ".project", "The project singleton was unavailable; project absence was not verified.");
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["status"] = "unknown",
                ["name"] = null, ["user"] = null, ["project_number"] = null, ["aduuid"] = null
            };
        }

        bool? isOpen = ReadModuleContextBoolean(project.IsOpen, boundary + ".project.is_open", errors);
        if (isOpen == false)
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["status"] = "not_open", ["name"] = null, ["user"] = null, ["project_number"] = null, ["aduuid"] = null
            };
        }
        string name = ReadModuleContextText(() => project.Name, boundary + ".project.name", errors, true);
        string user = ReadModuleContextText(() => project.UserName, boundary + ".project.user", errors, false);
        string number = ReadModuleContextText(() => project.Number, boundary + ".project.number", errors, true);
        string aduuid = ReadModuleContextText(() => project.Aduuid, boundary + ".project.aduuid", errors, true);
        string status = isOpen == true && name != null && number != null && aduuid != null ? "present" : "unknown";
        return new Dictionary<string, object>(StringComparer.Ordinal)
        {
            ["status"] = status, ["name"] = name, ["user"] = user, ["project_number"] = number, ["aduuid"] = aduuid
        };
    }

    private static Dictionary<string, object> ObserveMdb(string boundary, List<Dictionary<string, object>> errors)
    {
        MDB mdb = ReadModuleContextValue(() => MDB.CurrentMDB, boundary + ".mdb", errors, (MDB)null);
        if (mdb == null)
        {
            AddModuleContextError(errors, boundary + ".mdb", "The MDB singleton was unavailable; no current MDB absence was verified.");
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["status"] = "unknown", ["name"] = null, ["element_is_null"] = null, ["element"] = null
            };
        }
        DbElement element = ReadModuleContextValue(() => mdb.MdbElement, boundary + ".mdb.element", errors, (DbElement)null);
        if (element == null)
        {
            AddModuleContextError(errors, boundary + ".mdb.element", "The MDB element wrapper was unavailable; no current MDB absence was verified.");
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["status"] = "unknown", ["name"] = null, ["element_is_null"] = null, ["element"] = null
            };
        }
        bool? isNull = ReadModuleContextBoolean(() => element.IsNull, boundary + ".mdb.element.is_null", errors);
        if (isNull == true)
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["status"] = "absent", ["name"] = null, ["element_is_null"] = true, ["element"] = null
            };
        if (isNull != false)
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["status"] = "unknown", ["name"] = null, ["element_is_null"] = null, ["element"] = null
            };

        Dictionary<string, object> identity = ReadModuleContextElementIdentity(element, boundary + ".mdb.element", errors);
        string name = ReadModuleContextText(() => mdb.Name, boundary + ".mdb.name", errors, true);
        bool identityComplete = identity["refno"] != null && identity["type"] != null &&
            identity["db_type"] != null && identity["db_type_name"] != null && identity["db_type_code"] != null;
        return new Dictionary<string, object>(StringComparer.Ordinal)
        {
            // MDB.Name is useful context, but the typed MdbElement is the identity.
            ["status"] = identityComplete ? "present" : "unknown",
            ["name"] = name, ["element_is_null"] = false, ["element"] = identity
        };
    }

    private static Dictionary<string, object> ObserveCurrentElement(string boundary, List<Dictionary<string, object>> errors)
    {
        DbElement element = ReadModuleContextValue(
            () => Aveva.Core.Database.CurrentElement.Element, boundary + ".current_element", errors, (DbElement)null);
        if (element == null)
        {
            AddModuleContextError(errors, boundary + ".current_element", "The current-element wrapper was unavailable; no absence was verified.");
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["status"] = "unknown",
                ["is_null"] = null, ["refno"] = null, ["type"] = null
            };
        }

        bool? isNull = ReadModuleContextBoolean(() => element.IsNull, boundary + ".current_element.is_null", errors);
        if (isNull == true)
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["status"] = "absent", ["is_null"] = true, ["refno"] = null, ["type"] = null
            };
        }
        if (isNull != false)
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["status"] = "unknown", ["is_null"] = null, ["refno"] = null, ["type"] = null
            };
        }

        bool? isValid = ReadModuleContextBoolean(() => element.IsValid, boundary + ".current_element.is_valid", errors);
        bool? isDeleted = ReadModuleContextBoolean(() => element.IsDeleted, boundary + ".current_element.is_deleted", errors);
        if (isValid != true || isDeleted != false)
        {
            if (isValid == false || isDeleted == true)
                AddModuleContextError(errors, boundary + ".current_element", "The current element is invalid or deleted.");
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["status"] = "unknown", ["is_null"] = false, ["refno"] = null, ["type"] = null
            };
        }

        string reference = ReadModuleContextText(
            () => element.GetAsString(DbAttributeInstance.REF), boundary + ".current_element.refno", errors, true);
        DbElementType type = ReadModuleContextValue(
            () => element.GetElementType(), boundary + ".current_element.type", errors, (DbElementType)null);
        string shortName = type == null ? null : ReadModuleContextText(
            () => type.ShortName, boundary + ".current_element.type", errors, true)?.ToUpperInvariant();
        if (reference != null && !ModuleContextValidReference(reference))
        {
            AddModuleContextError(errors, boundary + ".current_element.refno", "The SDK returned a malformed current element reference.");
            reference = null;
        }
        if (shortName != null && !ModuleContextValidType(shortName))
        {
            AddModuleContextError(errors, boundary + ".current_element.type", "The SDK returned an invalid current element type name.");
            shortName = null;
        }
        return new Dictionary<string, object>(StringComparer.Ordinal)
        {
            ["status"] = reference != null && shortName != null ? "present" : "unknown",
            ["is_null"] = false, ["refno"] = reference, ["type"] = shortName
        };
    }

    private static Dictionary<string, object> ReadModuleContextElementIdentity(
        DbElement element, string stage, List<Dictionary<string, object>> errors)
    {
        bool? isValid = ReadModuleContextBoolean(() => element.IsValid, stage + ".is_valid", errors);
        bool? isDeleted = ReadModuleContextBoolean(() => element.IsDeleted, stage + ".is_deleted", errors);
        if (isValid != true || isDeleted != false)
        {
            if (isValid == false || isDeleted == true)
                AddModuleContextError(errors, stage, "The element is invalid or deleted.");
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["refno"] = null, ["type"] = null, ["db_type"] = null,
                ["db_type_name"] = null, ["db_type_code"] = null
            };
        }

        string reference = ReadModuleContextText(
            () => element.GetAsString(DbAttributeInstance.REF), stage + ".refno", errors, true);
        DbElementType elementType = ReadModuleContextValue(
            () => element.GetElementType(), stage + ".type", errors, (DbElementType)null);
        string type = elementType == null ? null : ReadModuleContextText(
            () => elementType.ShortName, stage + ".type", errors, true)?.ToUpperInvariant();
        DbType? databaseType = ReadModuleContextValue<DbType?>(() => element.DbType, stage + ".db_type", errors, null);
        if (reference != null && !ModuleContextValidReference(reference))
        {
            AddModuleContextError(errors, stage + ".refno", "The SDK returned a malformed element reference.");
            reference = null;
        }
        if (type != null && !ModuleContextValidType(type))
        {
            AddModuleContextError(errors, stage + ".type", "The SDK returned an invalid element type name.");
            type = null;
        }
        return new Dictionary<string, object>(StringComparer.Ordinal)
        {
            ["refno"] = reference, ["type"] = type,
            ["db_type"] = databaseType.HasValue ? databaseType.Value.ToString() : null,
            ["db_type_name"] = databaseType.HasValue ? databaseType.Value.ToString() : null,
            ["db_type_code"] = databaseType.HasValue ? (int?)databaseType.Value : null
        };
    }

    private static T ReadModuleContextValue<T>(Func<T> read, string stage,
        List<Dictionary<string, object>> errors, T fallback)
    {
        try { return NativeRead(read); }
        catch (Exception error)
        {
            // NativeRead checks the latch after every native getter. If a new
            // fault occurred, propagate immediately instead of collecting it.
            ThrowIfNativeFaulted();
            AddModuleContextError(errors, stage, error.Message);
            return fallback;
        }
    }

    private static string ReadModuleContextText(Func<string> read, string stage,
        List<Dictionary<string, object>> errors, bool requireNonempty)
    {
        int errorCount = errors.Count;
        string value = ReadModuleContextValue(read, stage, errors, (string)null);
        if (value == null || value.Length > ModuleContextTextLimit ||
            (requireNonempty && string.IsNullOrWhiteSpace(value)) || ContainsControl(value))
        {
            if (errors.Count == errorCount)
                AddModuleContextError(errors, stage, "Text is missing or exceeds the bounded identity field limits.");
            return null;
        }
        return value;
    }

    private static int? ReadModuleContextNumber(Func<int> read, string stage, List<Dictionary<string, object>> errors)
    {
        try { return NativeRead(read); }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            AddModuleContextError(errors, stage, error.Message);
            return null;
        }
    }

    private static bool? ReadModuleContextBoolean(Func<bool> read, string stage, List<Dictionary<string, object>> errors)
    {
        try { return NativeRead(read); }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            AddModuleContextError(errors, stage, error.Message);
            return null;
        }
    }

    private static bool? ModuleContextAgreement(ModuleContextBoundary before, ModuleContextBoundary after)
    {
        if (!string.Equals(before.SessionId, after.SessionId, StringComparison.Ordinal) ||
            !string.Equals(before.BridgeBuild, after.BridgeBuild, StringComparison.Ordinal))
            return false;

        bool? module = ModuleContextIdentityAgreement(before.Module, after.Module, "name", "number", "description");
        bool? project = ModuleContextIdentityAgreement(before.Project, after.Project, "name", "user", "project_number", "aduuid");
        bool? mdb = ModuleContextIdentityAgreement(before.Mdb, after.Mdb, "element");
        bool? currentElement = ModuleContextIdentityAgreement(before.CurrentElement, after.CurrentElement, "is_null", "refno", "type");
        if (module == false || project == false || mdb == false || currentElement == false) return false;
        if (module == null || project == null || mdb == null || currentElement == null) return null;
        return true;
    }

    private static bool? ModuleContextIdentityAgreement(
        Dictionary<string, object> before, Dictionary<string, object> after, params string[] identityFields)
    {
        string beforeStatus = before["status"] as string;
        string afterStatus = after["status"] as string;
        if (beforeStatus == null || afterStatus == null || beforeStatus == "unknown" || afterStatus == "unknown") return null;
        if (!string.Equals(beforeStatus, afterStatus, StringComparison.Ordinal)) return false;
        if (beforeStatus == "absent" || beforeStatus == "not_open") return true;
        foreach (string field in identityFields)
        {
            object first = before[field];
            object last = after[field];
            if (first == null || last == null) return null;
            if (first is Dictionary<string, object> firstIdentity && last is Dictionary<string, object> lastIdentity)
            {
                if (firstIdentity.Count != lastIdentity.Count) return false;
                foreach (string identityField in firstIdentity.Keys)
                {
                    if (!firstIdentity.TryGetValue(identityField, out object firstValue) ||
                        !lastIdentity.TryGetValue(identityField, out object lastValue)) return false;
                    if (firstValue == null || lastValue == null) return null;
                    if (!Equals(firstValue, lastValue)) return false;
                }
            }
            else if (!Equals(first, last)) return false;
        }
        return true;
    }

    private static bool ContainsControl(string value)
    {
        foreach (char character in value)
            if (char.IsControl(character)) return true;
        return false;
    }

    private static bool ModuleContextValidReference(string reference)
    {
        if (reference.Length < 4 || reference[0] != '=') return false;
        int separator = reference.IndexOf('/');
        if (separator < 2 || separator == reference.Length - 1 || reference.IndexOf('/', separator + 1) >= 0) return false;
        for (int i = 1; i < reference.Length; i++)
            if (i != separator && (reference[i] < '0' || reference[i] > '9')) return false;
        return true;
    }

    private static bool ModuleContextValidType(string type)
    {
        if (string.IsNullOrEmpty(type) || type.Length > 64 || type[0] < 'A' || type[0] > 'Z') return false;
        foreach (char character in type)
            if (!(character >= 'A' && character <= 'Z') && !(character >= '0' && character <= '9') && character != '_')
                return false;
        return true;
    }

    private static void AddModuleContextError(List<Dictionary<string, object>> errors, string stage, string message)
    {
        if (errors.Count >= ModuleContextErrorLimit) return;
        string text = message ?? "The native identity read failed.";
        var bounded = new System.Text.StringBuilder(Math.Min(text.Length, ModuleContextErrorTextLimit));
        foreach (char character in text)
        {
            if (bounded.Length >= ModuleContextErrorTextLimit) break;
            bounded.Append(char.IsControl(character) ? ' ' : character);
        }
        errors.Add(new Dictionary<string, object>(StringComparer.Ordinal)
        {
            ["stage"] = stage,
            ["message"] = bounded.ToString()
        });
    }

    private sealed class ModuleContextBoundary
    {
        internal string SessionId;
        internal string BridgeBuild;
        internal Dictionary<string, object> Module;
        internal Dictionary<string, object> Project;
        internal Dictionary<string, object> Mdb;
        internal Dictionary<string, object> CurrentElement;

        internal Dictionary<string, object> ToResult()
        {
        return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["session_id"] = SessionId,
                ["bridge_build"] = BridgeBuild,
                ["module"] = Module,
                ["project"] = Project,
                ["mdb"] = Mdb,
                ["current_element"] = CurrentElement
            };
        }
    }
}
