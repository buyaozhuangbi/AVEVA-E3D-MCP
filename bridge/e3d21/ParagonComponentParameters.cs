using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace E3d21AiBridge;

public interface IE3d21ParagonComponentParametersContext
{
    ParagonComponentParametersReply SetParagonComponentParameters(string refno, IList<double> parameters,
        bool dryRun, string requestId, string expectedSessionId, string expectedBridgeBuild,
        string expectedRevision, string sessionId);
    object GetParagonComponentParametersStatus(string requestId, string expectedSessionId, string sessionId);
}

public sealed class ParagonComponentParametersReply
{
    public int HttpStatus { get; }
    public bool Ok { get; }
    public Dictionary<string, object> Result { get; }
    public string Error { get; }
    public bool NativeFaultLatched { get; }

    public ParagonComponentParametersReply(int httpStatus, bool ok, Dictionary<string, object> result,
        string error = null, bool nativeFaultLatched = false)
    {
        if (httpStatus < 100 || httpStatus > 599) throw new ArgumentOutOfRangeException(nameof(httpStatus));
        HttpStatus = httpStatus; Ok = ok; Result = result;
        Error = error; NativeFaultLatched = nativeFaultLatched;
    }
}

// The HTTP wire carries explicit bits because net48 JSON parsing and rendering
// normalize negative zero. The SDK receives a private, validated DOUBLE array.
internal sealed class ParagonComponentParametersRequest
{
    internal const int MaximumParameters = 128;
    private static readonly Regex NumericReference = new Regex(
        @"\A=([1-9][0-9]{0,9})/(0|[1-9][0-9]{0,9})\z", RegexOptions.CultureInvariant);
    private static readonly Regex Revision = new Regex(@"\A[0-9a-f]{64}\z", RegexOptions.CultureInvariant);
    private static readonly Regex ParameterBitPattern = new Regex(@"\A[0-9a-f]{16}\z", RegexOptions.CultureInvariant);

    internal string Refno { get; private set; }
    internal double[] Parameters { get; private set; }
    internal string[] ParameterBits { get; private set; }
    internal bool DryRun { get; private set; }
    internal string RequestId { get; private set; }
    internal string ExpectedSessionId { get; private set; }
    internal string ExpectedBridgeBuild { get; private set; }
    internal string ExpectedRevision { get; private set; }

    internal static ParagonComponentParametersRequest Validate(string refno, IList<double> parameters,
        bool dryRun, string requestId, string expectedSessionId, string expectedBridgeBuild, string expectedRevision)
    {
        Match reference = NumericReference.Match(refno ?? "");
        int database, element;
        if (!reference.Success || !int.TryParse(reference.Groups[1].Value, NumberStyles.None, CultureInfo.InvariantCulture, out database)
            || !int.TryParse(reference.Groups[2].Value, NumberStyles.None, CultureInfo.InvariantCulture, out element))
            throw new ArgumentException("refno must be a canonical numeric DBREF with Int32 components.");
        if (parameters == null || parameters.Count < 1 || parameters.Count > MaximumParameters)
            throw new ArgumentException("parameters must contain 1 to 128 finite DOUBLE values.");
        var values = new double[parameters.Count];
        var bits = new string[parameters.Count];
        for (int index = 0; index < values.Length; index++)
        {
            double value = parameters[index];
            if (double.IsNaN(value) || double.IsInfinity(value))
                throw new ArgumentException("parameters must contain only finite DOUBLE values.");
            values[index] = value; bits[index] = Bits(value);
        }
        if (dryRun)
        {
            if (requestId != null || expectedSessionId != null || expectedBridgeBuild != null || expectedRevision != null)
                throw new ArgumentException("Preview does not accept execution identifiers or a revision.");
        }
        else
        {
            ValidateStatus(requestId, expectedSessionId);
            Guid build;
            if (expectedBridgeBuild == null || !Guid.TryParseExact(expectedBridgeBuild, "D", out build)
                || build.ToString("D") != expectedBridgeBuild)
                throw new ArgumentException("expected_bridge_build must be a canonical lowercase UUID.");
            if (expectedRevision == null || !Revision.IsMatch(expectedRevision))
                throw new ArgumentException("expected_revision must be a lowercase SHA-256 scoped revision.");
        }
        return new ParagonComponentParametersRequest { Refno = refno, Parameters = values, ParameterBits = bits,
            DryRun = dryRun, RequestId = requestId, ExpectedSessionId = expectedSessionId,
            ExpectedBridgeBuild = expectedBridgeBuild, ExpectedRevision = expectedRevision };
    }

    internal static ParagonComponentParametersRequest ValidateWire(string refno, IList<double> parameters,
        IList<string> parameterBits, bool dryRun, string requestId, string expectedSessionId,
        string expectedBridgeBuild, string expectedRevision)
    {
        var request = Validate(refno, parameters, dryRun, requestId, expectedSessionId, expectedBridgeBuild, expectedRevision);
        if (parameterBits == null || parameterBits.Count != request.Parameters.Length)
            throw new ArgumentException("parameter_bits must match the complete parameters array length.");
        for (int index = 0; index < request.Parameters.Length; index++)
        {
            string text = parameterBits[index];
            ulong raw;
            if (text == null || !ParameterBitPattern.IsMatch(text)
                || !ulong.TryParse(text, NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out raw))
                throw new ArgumentException("parameter_bits must contain canonical lowercase binary64 hex values.");
            double exact = BitConverter.Int64BitsToDouble(unchecked((long)raw));
            double numeric = request.Parameters[index];
            if (double.IsNaN(exact) || double.IsInfinity(exact)
                || !(numeric == 0.0 && exact == 0.0) && Bits(numeric) != text)
                throw new ArgumentException("parameter_bits must describe the corresponding finite DOUBLE value.");
            request.Parameters[index] = exact;
            request.ParameterBits[index] = text;
        }
        return request;
    }

    internal static string Bits(double value) =>
        BitConverter.DoubleToInt64Bits(value).ToString("x16", CultureInfo.InvariantCulture);

    internal static void ValidateStatus(string requestId, string expectedSessionId) =>
        ParagonComponentCopyRequest.ValidateStatus(requestId, expectedSessionId);

    internal static string ErrorText(string value) => ParagonComponentCopyRequest.ErrorText(value);
}
