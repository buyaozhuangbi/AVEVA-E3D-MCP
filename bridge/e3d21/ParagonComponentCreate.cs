using System;
using System.Collections.Generic;

namespace E3d21AiBridge;

public interface IE3d21ParagonComponentCreateContext
{
    ParagonComponentCreateReply CreateParagonComponent(string parentRefno, string pointRefno, string geometryRefno,
        string name, IList<double> parameters, bool dryRun, string requestId, string expectedSessionId,
        string expectedBridgeBuild, string expectedRevision, string sessionId);

    object GetParagonComponentCreateStatus(string requestId, string expectedSessionId, string sessionId);
}

public sealed class ParagonComponentCreateReply
{
    public int HttpStatus { get; }
    public bool Ok { get; }
    public Dictionary<string, object> Result { get; }
    public string Error { get; }
    public bool NativeFaultLatched { get; }

    public ParagonComponentCreateReply(int httpStatus, bool ok, Dictionary<string, object> result,
        string error = null, bool nativeFaultLatched = false)
    {
        if (httpStatus < 100 || httpStatus > 599) throw new ArgumentOutOfRangeException(nameof(httpStatus));
        HttpStatus = httpStatus;
        Ok = ok;
        Result = result;
        Error = error;
        NativeFaultLatched = nativeFaultLatched;
    }
}

internal sealed class ParagonComponentCreateRequest
{
    internal string ParentRefno { get; }
    internal string PointRefno { get; }
    internal string GeometryRefno { get; }
    internal string Name { get; }
    internal double[] Parameters => numeric.Parameters;
    internal string[] ParameterBits => numeric.ParameterBits;
    internal bool DryRun => numeric.DryRun;
    internal string RequestId => numeric.RequestId;
    internal string ExpectedSessionId => numeric.ExpectedSessionId;
    internal string ExpectedBridgeBuild => numeric.ExpectedBridgeBuild;
    internal string ExpectedRevision => numeric.ExpectedRevision;

    private readonly ParagonComponentParametersRequest numeric;

    private ParagonComponentCreateRequest(string parentRefno, string pointRefno, string geometryRefno,
        string name, ParagonComponentParametersRequest numeric)
    {
        ParentRefno = parentRefno;
        PointRefno = pointRefno;
        GeometryRefno = geometryRefno;
        Name = name;
        this.numeric = numeric;
    }

    internal static ParagonComponentCreateRequest Validate(string parentRefno, string pointRefno, string geometryRefno,
        string name, IList<double> parameters, bool dryRun, string requestId, string expectedSessionId,
        string expectedBridgeBuild, string expectedRevision)
    {
        RequireRoles(parentRefno, pointRefno, geometryRefno, name);
        var numeric = ParagonComponentParametersRequest.Validate(parentRefno, parameters, dryRun, requestId,
            expectedSessionId, expectedBridgeBuild, expectedRevision);
        return new ParagonComponentCreateRequest(parentRefno, pointRefno, geometryRefno, name, numeric);
    }

    internal static ParagonComponentCreateRequest ValidateWire(string parentRefno, string pointRefno, string geometryRefno,
        string name, IList<double> parameters, IList<string> parameterBits, bool dryRun, string requestId,
        string expectedSessionId, string expectedBridgeBuild, string expectedRevision)
    {
        RequireRoles(parentRefno, pointRefno, geometryRefno, name);
        var numeric = ParagonComponentParametersRequest.ValidateWire(parentRefno, parameters, parameterBits, dryRun,
            requestId, expectedSessionId, expectedBridgeBuild, expectedRevision);
        return new ParagonComponentCreateRequest(parentRefno, pointRefno, geometryRefno, name, numeric);
    }

    internal static void ValidateStatus(string requestId, string expectedSessionId) =>
        ParagonComponentCopyRequest.ValidateStatus(requestId, expectedSessionId);

    internal static string ErrorText(string value) => ParagonComponentCopyRequest.ErrorText(value);

    private static void RequireRoles(string parentRefno, string pointRefno, string geometryRefno, string name)
    {
        ParagonComponentCopyRequest.RequireReference(parentRefno, "parent_refno");
        ParagonComponentCopyRequest.RequireReference(pointRefno, "point_refno");
        ParagonComponentCopyRequest.RequireReference(geometryRefno, "geometry_refno");
        if (parentRefno == pointRefno || parentRefno == geometryRefno || pointRefno == geometryRefno)
            throw new ArgumentException("The parent, point set, and geometry set references must be distinct.");
        ParagonComponentCopyRequest.RequireName(name);
    }
}
