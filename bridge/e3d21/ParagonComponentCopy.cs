using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace E3d21AiBridge;

public interface IE3d21ParagonComponentCopyContext
{
    ParagonComponentCopyReply CopyParagonComponent(string sourceRefno, string parentRefno, string name,
        string recipe, bool dryRun, string requestId, string expectedSessionId, string expectedBridgeBuild,
        string expectedRevision, string sessionId);
    object GetParagonComponentCopyStatus(string requestId, string expectedSessionId, string sessionId);
}

public sealed class ParagonComponentCopyReply
{
    public int HttpStatus { get; }
    public bool Ok { get; }
    public Dictionary<string, object> Result { get; }
    public string Error { get; }
    public bool NativeFaultLatched { get; }

    public ParagonComponentCopyReply(int httpStatus, bool ok, Dictionary<string, object> result,
        string error = null, bool nativeFaultLatched = false)
    {
        if (httpStatus < 100 || httpStatus > 599) throw new ArgumentOutOfRangeException(nameof(httpStatus));
        HttpStatus = httpStatus; Ok = ok; Result = result;
        Error = error; NativeFaultLatched = nativeFaultLatched;
    }
}

// Literal intent only: no SDK access, implicit CE, caller PML or backend selection.
internal sealed class ParagonComponentCopyRequest
{
    internal const string RecipeName = "same_category_scom_copy_v1";
    internal const int MaximumNameLength = 50;
    internal const int MaximumMembers = 100;
    private static readonly Regex NumericReference = new Regex(
        @"\A=([1-9][0-9]{0,9})/(0|[1-9][0-9]{0,9})\z", RegexOptions.CultureInvariant);
    private static readonly Regex LiteralName = new Regex(
        @"\A/[A-Za-z][A-Za-z0-9_-]*(?:/[A-Za-z][A-Za-z0-9_-]*)*\z", RegexOptions.CultureInvariant);
    private static readonly Regex Revision = new Regex(@"\A[0-9a-f]{64}\z", RegexOptions.CultureInvariant);

    internal string SourceRefno { get; private set; }
    internal string ParentRefno { get; private set; }
    internal string Name { get; private set; }
    internal string Recipe { get; private set; }
    internal bool DryRun { get; private set; }
    internal string RequestId { get; private set; }
    internal string ExpectedSessionId { get; private set; }
    internal string ExpectedBridgeBuild { get; private set; }
    internal string ExpectedRevision { get; private set; }

    internal static ParagonComponentCopyRequest Validate(string sourceRefno, string parentRefno, string name,
        string recipe, bool dryRun, string requestId, string expectedSessionId, string expectedBridgeBuild,
        string expectedRevision)
    {
        RequireReference(sourceRefno, "source_refno");
        RequireReference(parentRefno, "parent_refno");
        if (sourceRefno == parentRefno) throw new ArgumentException("source_refno and parent_refno must differ.");
        if (name == null || name.Length > MaximumNameLength || !LiteralName.IsMatch(name))
            throw new ArgumentException("name must be an explicit ASCII /NAME of at most 50 characters with letter-led path segments.");
        if (recipe != RecipeName) throw new ArgumentException("Only same_category_scom_copy_v1 is supported.");
        if (dryRun)
        {
            if (requestId != null || expectedSessionId != null || expectedBridgeBuild != null || expectedRevision != null)
                throw new ArgumentException("Preview does not accept execution identifiers or a revision.");
        }
        else
        {
            RequireUuid(requestId, "request_id");
            RequireSession(expectedSessionId);
            RequireUuid(expectedBridgeBuild, "expected_bridge_build");
            if (expectedRevision == null || !Revision.IsMatch(expectedRevision))
                throw new ArgumentException("expected_revision must be a lowercase SHA-256 scoped revision.");
        }
        return new ParagonComponentCopyRequest { SourceRefno = sourceRefno, ParentRefno = parentRefno,
            Name = name, Recipe = recipe, DryRun = dryRun, RequestId = requestId,
            ExpectedSessionId = expectedSessionId, ExpectedBridgeBuild = expectedBridgeBuild,
            ExpectedRevision = expectedRevision };
    }

    internal static void ValidateStatus(string requestId, string expectedSessionId)
    { RequireUuid(requestId, "request_id"); RequireSession(expectedSessionId); }

    private static void RequireReference(string value, string field)
    {
        Match match = NumericReference.Match(value ?? "");
        int database, element;
        if (!match.Success || !int.TryParse(match.Groups[1].Value, NumberStyles.None, CultureInfo.InvariantCulture, out database)
            || !int.TryParse(match.Groups[2].Value, NumberStyles.None, CultureInfo.InvariantCulture, out element))
            throw new ArgumentException(field + " must be a canonical numeric DBREF with Int32 components.");
    }

    private static void RequireUuid(string value, string field)
    {
        Guid id;
        if (value == null || !Guid.TryParseExact(value, "D", out id) || id.ToString("D") != value)
            throw new ArgumentException(field + " must be a canonical lowercase UUID with hyphens.");
    }

    private static void RequireSession(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Length > 256)
            throw new ArgumentException("expected_session_id must be a nonblank server session of at most 256 UTF16 units.");
        for (int index = 0; index < value.Length; index++)
        {
            char character = value[index];
            UnicodeCategory category = CharUnicodeInfo.GetUnicodeCategory(value, index);
            if (category == UnicodeCategory.Control || category == UnicodeCategory.Format
                || category == UnicodeCategory.LineSeparator || category == UnicodeCategory.ParagraphSeparator
                || category == UnicodeCategory.PrivateUse || category == UnicodeCategory.OtherNotAssigned
                || category == UnicodeCategory.SpaceSeparator && character != ' ')
                throw new ArgumentException("expected_session_id must be printable.");
            if (char.IsHighSurrogate(character))
            {
                if (index + 1 == value.Length || !char.IsLowSurrogate(value[++index]))
                    throw new ArgumentException("expected_session_id must be valid UTF16.");
            }
            else if (char.IsLowSurrogate(character)) throw new ArgumentException("expected_session_id must be valid UTF16.");
        }
    }

    internal static string ErrorText(string value)
    {
        if (string.IsNullOrWhiteSpace(value)) return "The component copy failed without a message.";
        if (value.Length <= 4096) return value;
        int length = 4093;
        if (char.IsHighSurrogate(value[length - 1])) length--;
        return value.Substring(0, length) + "...";
    }
}
