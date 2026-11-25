using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;

namespace E3d21AiBridge;

public interface IE3d21TeamDatabaseIdentityManifestContext
{
    object ReadTeamDatabaseIdentityManifest(string sessionId, string expectedContext, int limit = 64);
}

// Pure managed input contract. The caller's context digest is never normalized,
// inferred from the reply, or replaced with a newly observed context.
internal sealed class TeamDatabaseIdentityManifestRequest
{
    internal const int DefaultLimit = 64;
    internal const int MaximumLimit = 250;
    internal string ExpectedContext;
    internal int Limit;

    internal static TeamDatabaseIdentityManifestRequest Validate(string expectedContext, int limit = DefaultLimit)
    {
        if (expectedContext == null || !Regex.IsMatch(expectedContext, @"\A[a-f0-9]{64}\z", RegexOptions.CultureInvariant))
            throw new ArgumentException("expected_context must be the 64-character lowercase hexadecimal context_id from a previous database-state response.");
        if (limit < 1 || limit > MaximumLimit) throw new ArgumentOutOfRangeException("limit");
        return new TeamDatabaseIdentityManifestRequest { ExpectedContext = expectedContext, Limit = limit };
    }

    internal static string ErrorText(string value)
    {
        if (string.IsNullOrWhiteSpace(value)) return "The read failed without a message.";
        if (value.Length <= 4096) return value;
        int length = 4093;
        if (char.IsHighSurrogate(value[length - 1])) length--;
        return value.Substring(0, length) + "...";
    }
}

// This file has no SDK, HTTP, PML or mutation path.
internal sealed class TeamDbIdentity
{
    internal readonly string Refno, Type, DatabaseType;
    internal readonly int DatabaseTypeCode, ReferenceDatabase;
    internal string Key => DatabaseTypeCode.ToString(CultureInfo.InvariantCulture) + "|" + Refno + "|" + Type;

    internal TeamDbIdentity(int[] reference, string type, int databaseTypeCode, string databaseType)
    {
        if (reference == null || reference.Length != 2 || reference[0] < 1 || reference[1] < 0)
            throw new InvalidOperationException("No valid native record reference was available.");
        if (string.IsNullOrEmpty(type) || type.Length > 64 || type[0] < 'A' || type[0] > 'Z' ||
            type.Any(c => !(c >= 'A' && c <= 'Z') && !(c >= '0' && c <= '9') && c != '_'))
            throw new InvalidOperationException("No valid native base type was available.");
        Refno = "=" + reference[0].ToString(CultureInfo.InvariantCulture) + "/" + reference[1].ToString(CultureInfo.InvariantCulture);
        ReferenceDatabase = reference[0]; Type = type; DatabaseTypeCode = databaseTypeCode; DatabaseType = databaseType;
    }

    internal bool SameDatabase(TeamDbIdentity other) => other != null &&
        DatabaseTypeCode == other.DatabaseTypeCode && ReferenceDatabase == other.ReferenceDatabase;

    internal Dictionary<string, object> Result() => new Dictionary<string, object>
    {
        ["refno"] = Refno, ["type"] = Type, ["db_type"] = DatabaseType,
        ["db_type_code"] = DatabaseTypeCode, ["identity_key"] = Key
    };
}

internal sealed class TeamDbTeam
{
    internal int Index;
    internal TeamDbIdentity Identity;
    internal string RawName, Name;
    internal readonly List<object> Errors = new List<object>();
    internal Dictionary<string, object> Result() => new Dictionary<string, object>
    {
        ["index"] = Index, ["identity"] = Identity?.Result(), ["namn"] = RawName,
        ["canonical_name"] = Name, ["read_errors"] = Errors
    };
}

internal sealed class TeamDbRecord
{
    internal int Index;
    internal TeamDbIdentity Identity, ExtractOwner;
    internal string RawName, Name;
    internal int? Number, FileNumber, ExtractNumber;
    internal bool ExtractOwnerRead, OwnerChainSupported;
    internal readonly List<TeamDbIdentity> OwnerPath = new List<TeamDbIdentity>();
    internal readonly List<object> Errors = new List<object>();

    // EXTNO is an independent raw observation. Its numbering semantics are not
    // established here and must not substitute for the typed EXTOWN evidence.
    internal string ExtractKind => !ExtractOwnerRead ? "unknown" :
        ExtractOwner == null ? "master_candidate" :
        ExtractOwner.Type == "DB" && ExtractOwner.Key != Identity?.Key ? "extract_candidate" : "unknown";

    // FINO identifies a file. It is deliberately absent from the raw comparison
    // key; equal key values still do not prove a System-record/MDB association.
    internal string MatchKey => Name != null && Number.HasValue && ExtractNumber.HasValue
            ? TeamDatabaseIdentityManifest.MatchKey(Name, Number.Value, ExtractNumber.Value) : null;
}

internal sealed class TeamDbMdb
{
    internal int Index;
    internal string RawName, Name;
    internal int? Number, ExtractNumber;
    internal bool? IsExtract;
    internal readonly List<object> Errors = new List<object>();
    // Successful field reads do not verify how native ExtractNumber relates to
    // IsExtract, nor how either corresponds to a System DB record's EXTNO.
    internal bool? Coherent => null;
    internal string MatchKey => Name != null && Number.HasValue && ExtractNumber.HasValue
        ? TeamDatabaseIdentityManifest.MatchKey(Name, Number.Value, ExtractNumber.Value) : null;
    internal Dictionary<string, object> Result() => new Dictionary<string, object>
    {
        ["index"] = Index, ["name"] = RawName, ["canonical_name"] = Name, ["database_number"] = Number,
        ["extract_number"] = ExtractNumber, ["is_extract"] = IsExtract, ["identity_coherent"] = Coherent,
        ["read_errors"] = Errors
    };
}

internal static class TeamDatabaseIdentityManifest
{
    internal const int MaximumLimit = 250, MaximumMaterializedArray = 10000, MaximumTextLength = 256;

    // Accept the native TEAM marker (`*TEAM`) as well as the documented
    // leading /*, a single /, or an unprefixed name.  The marker is a
    // namespace prefix, not a wildcard; database names still reject every
    // wildcard character.  Never trim arbitrary slashes or infer a Team.
    internal static string Name(string raw, bool database)
    {
        if (string.IsNullOrEmpty(raw) || raw.Length > MaximumTextLength || raw.Any(c => char.IsControl(c) || char.IsWhiteSpace(c)))
            throw new InvalidOperationException("The native identity name was empty, overlong or malformed.");
        string name = raw.StartsWith("/*", StringComparison.Ordinal) ? raw.Substring(2) :
            raw.StartsWith("/", StringComparison.Ordinal) ? raw.Substring(1) : raw;
        if (!database && name.StartsWith("*", StringComparison.Ordinal)) name = name.Substring(1);
        string[] parts = name.Split('/');
        if (parts.Length != (database ? 2 : 1) || parts.Any(part => string.IsNullOrEmpty(part) ||
            part.IndexOfAny(new[] { '*', '?' }) >= 0 || string.Equals(part, "UNSET", StringComparison.OrdinalIgnoreCase)))
            throw new InvalidOperationException("A complete native Team or Team/DB name was required.");
        return name.ToUpperInvariant();
    }

    internal static string Text(string text)
    {
        if (text == null) return "The read failed without a message.";
        if (text.Length <= 512) return text;
        int length = char.IsHighSurrogate(text[508]) ? 508 : 509;
        return text.Substring(0, length) + "...";
    }

    internal static void Error(List<object> errors, string path, string code, string message) =>
        errors.Add(new Dictionary<string, object> { ["path"] = path, ["code"] = code, ["message"] = Text(message) });

    internal static string MatchKey(string name, int number, int extract) =>
        name + "|" + number.ToString(CultureInfo.InvariantCulture) + "|" + extract.ToString(CultureInfo.InvariantCulture);

    internal static List<Dictionary<string, object>> Resolve(List<TeamDbTeam> teams, List<TeamDbRecord> records,
        List<TeamDbMdb> mdb, bool teamsComplete, bool recordIdentitiesComplete, bool contextStable,
        List<object> findings)
    {
        var teamKeys = teams.Where(t => t.Identity != null).GroupBy(t => t.Identity.Key).ToDictionary(g => g.Key, g => g.ToList());
        var recordKeys = records.Where(r => r.Identity != null).GroupBy(r => r.Identity.Key).ToDictionary(g => g.Key, g => g.Count());
        var mdbKeys = mdb.Where(r => r.MatchKey != null).GroupBy(r => r.MatchKey).ToDictionary(g => g.Key, g => g.ToList());
        foreach (var duplicate in teamKeys.Where(g => g.Value.Count > 1))
            Error(findings, "teams", "duplicate_typed_identity", duplicate.Key);
        var rows = new List<Dictionary<string, object>>();
        foreach (TeamDbRecord record in records)
        {
            bool duplicateRecord = record.Identity != null && recordKeys[record.Identity.Key] > 1;
            if (duplicateRecord) Error(findings, "databases[" + record.Index + "]", "duplicate_typed_identity", record.Identity.Key);
            TeamDbIdentity ancestor = record.OwnerChainSupported ? record.OwnerPath[1] : null;
            List<TeamDbTeam> listed = null;
            if (ancestor != null) teamKeys.TryGetValue(ancestor.Key, out listed);
            TeamDbTeam team = listed?.Count == 1 ? listed[0] : null;
            bool? nameAgreement = team?.Name == null || record.Name == null ? (bool?)null :
                string.Equals(record.Name.Split('/')[0], team.Name, StringComparison.Ordinal);
            string teamStatus = !contextStable ? "context_unverified" :
                duplicateRecord || listed?.Count > 1 ? "ambiguous" : nameAgreement == false ? "conflict" :
                teamsComplete && recordIdentitiesComplete && record.OwnerChainSupported && nameAgreement == true ? "confirmed" : "unverified";
            if (teamStatus == "conflict" || teamStatus == "ambiguous")
                Error(findings, "databases[" + record.Index + "].team_relation", teamStatus, "Typed ownership and catalog name evidence did not establish one consistent Team.");
            string key = record.MatchKey;
            List<TeamDbMdb> candidates = null;
            if (key != null) mdbKeys.TryGetValue(key, out candidates);
            rows.Add(new Dictionary<string, object>
            {
                ["index"] = record.Index, ["identity"] = record.Identity?.Result(),
                ["namedb"] = record.RawName, ["canonical_name"] = record.Name,
                ["dbno"] = record.Number, ["fino"] = record.FileNumber, ["extno"] = record.ExtractNumber,
                ["extract_owner_state"] = !record.ExtractOwnerRead ? "unknown" : record.ExtractOwner == null ? "none" : "observed",
                ["extown"] = record.ExtractOwner?.Result(), ["extract_kind"] = record.ExtractKind,
                ["owner_path"] = record.OwnerPath.Select(identity => identity.Result()).ToList(),
                ["team_relation"] = new Dictionary<string, object>
                {
                    ["status"] = teamStatus, ["source"] = "typed DB.Owner(DBLI).Owner(TEAM)",
                    ["owner_chain_supported"] = record.OwnerChainSupported, ["observed_team"] = ancestor?.Result(),
                    ["listed_team_index"] = team?.Index, ["name_agreement"] = nameAgreement,
                    ["confirmed_team"] = teamStatus == "confirmed" ? ancestor.Result() : null
                },
                ["current_mdb"] = new Dictionary<string, object>
                {
                    ["status"] = "indeterminate", ["match_fields"] = new[] { "canonical_name", "DBNO/Number", "raw_EXTNO/raw_ExtractNumber" },
                    ["candidate_indexes"] = candidates?.Select(candidate => candidate.Index).ToList() ?? new List<int>(),
                    ["matched_index"] = null, ["semantic_equivalence_verified"] = false
                },
                ["read_errors"] = record.Errors
            });
        }
        return rows;
    }
}
