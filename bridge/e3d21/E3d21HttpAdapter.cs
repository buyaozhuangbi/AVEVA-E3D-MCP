using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace E3d21AiBridge;

public sealed class E3d21HttpAdapter : IDisposable
{
    private readonly HttpListener listener = new HttpListener();
    private readonly IE3d21Context context;
    private readonly CancellationTokenSource stop = new CancellationTokenSource();
    private Task loop;
    private readonly string sessionId = Guid.NewGuid().ToString("D");
    private readonly PmlScriptReceiptStore scriptReceipts = new PmlScriptReceiptStore();
    private readonly MessageCursorCodec messageCursors = new MessageCursorCodec();
    private const int MaximumJsonRequestBodyBytes = 1024 * 1024;

    public E3d21HttpAdapter(IE3d21Context context, int port = 8766)
    {
        this.context = context ?? throw new ArgumentNullException("context");
        if (port < 1 || port > 65535)
            throw new ArgumentOutOfRangeException("port");
        listener.Prefixes.Add("http://127.0.0.1:" + port + "/");
    }

    public void Start()
    {
        if (loop != null)
            throw new InvalidOperationException("The E3D 2.1 adapter is already running.");
        listener.Start();
        loop = Task.Run(ListenAsync);
    }

    public void Stop()
    {
        stop.Cancel();
        listener.Stop();
        if (loop != null)
            loop.GetAwaiter().GetResult();
        loop = null;
    }

    public void Dispose()
    {
        Stop();
        stop.Dispose();
        listener.Close();
    }

    private async Task ListenAsync()
    {
        while (!stop.IsCancellationRequested)
        {
            HttpListenerContext request;
            try
            {
                request = await listener.GetContextAsync().ConfigureAwait(false);
            }
            catch (HttpListenerException) when (stop.IsCancellationRequested)
            {
                break;
            }
            catch (ObjectDisposedException) when (stop.IsCancellationRequested)
            {
                break;
            }
            // Keep diagnostics and health requests available while a model command is waiting.
            _ = Task.Run(() => HandleAsync(request));
        }
    }

    private async Task HandleAsync(HttpListenerContext request)
    {
        bool managedScriptStatusQuery = false;
        bool managedMessageWindowQuery = false;
        bool equipmentManifestQuery = false;
        bool equipmentChildSchemaQuery = false;
        bool pipeManifestQuery = false;
        bool paragonCatalogueManifestQuery = false;
        bool moduleContextQuery = false;
        bool paragonCatalogueSchemaQuery = false;
        bool connectivitySnapshotQuery = false;
        bool pipingSpecificationReadQuery = false;
        bool teamDatabaseIdentityManifestQuery = false;
        bool paragonComponentTemplateQuery = false;
        bool paragonReferenceTraceQuery = false;
        bool paragonComponentCopyQuery = false;
        bool paragonComponentCopyStatusQuery = false;
        bool paragonComponentCopyDispatched = false;
        bool paragonComponentCopyDryRun = true;
        bool paragonComponentParametersQuery = false;
        bool paragonComponentParametersStatusQuery = false;
        bool paragonComponentParametersDispatched = false;
        bool paragonComponentParametersDryRun = true;
        bool projectCatalogProbe = false;
        try
        {
            string path = request.Request.Url == null ? "" : request.Request.Url.AbsolutePath.TrimEnd('/');
            string method = request.Request.HttpMethod.ToUpperInvariant();
            managedScriptStatusQuery = method == "GET" && path == "/api/v1/pml-script/status";
            // Every method at this route must fail without native diagnostics.
            managedMessageWindowQuery = path == "/api/v1/messages/window";
            equipmentManifestQuery = path == "/api/v1/equipment/manifest";
            equipmentChildSchemaQuery = path == "/api/v1/equipment/child-schema";
            pipeManifestQuery = path == "/api/v1/piping/member-manifest";
            paragonCatalogueManifestQuery = path == "/api/v1/paragon/catalogue-manifest";
            moduleContextQuery = path == "/api/v1/module/context";
            paragonCatalogueSchemaQuery = path == "/api/v1/paragon/catalogue-schema";
            connectivitySnapshotQuery = path == "/api/v1/piping/connectivity-snapshot";
            pipingSpecificationReadQuery = path == "/api/v1/piping/specification-read";
            teamDatabaseIdentityManifestQuery = path == "/api/v1/database/team-identity-manifest";
            paragonComponentTemplateQuery = path == "/api/v1/paragon/component-template";
            paragonReferenceTraceQuery = path == "/api/v1/paragon/reference-trace";
            paragonComponentCopyQuery = path == "/api/v1/paragon/component-copy";
            paragonComponentCopyStatusQuery = path == "/api/v1/paragon/component-copy/status";
            paragonComponentParametersQuery = path == "/api/v1/paragon/component-parameters";
            paragonComponentParametersStatusQuery = path == "/api/v1/paragon/component-parameters/status";
            projectCatalogProbe = path == "/api/v1/database/project-catalog-probe";
            object response;
            if (method == "GET" && path == "/health")
            {
                object commandState = null;
                bool connected = false;
                string diagnosticError = null;
                try { commandState = SanitizeDiagnostics(context.CommandState()); }
                catch { diagnosticError = "Command-state diagnostics are unavailable."; }
                try { connected = context.Connected; }
                catch { diagnosticError = diagnosticError ?? "Connection observation is unavailable."; }
                response = new { ok = true, connected = connected, product = "AVEVA Everything3D", version = "E3D21",
                    session_id = sessionId, bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    state = commandState, diagnostic_error = diagnosticError,
                    api_features = new[] { "command_session_guard", "member_pagination", "element_schema", "typed_element_resolution", "attribute_constraints", "attribute_preflight", "attribute_batch_read", "attribute_batch_write", "create_element", "set_transform", "capture_view", "geometry_bounds", "geometry_relation", "geometry_relation_batch", "batch_exception_results", "view_state", "view_clipping_state", "set_view", "create_panel", "panel_geometry", "panel_world_positions", "panel_boundary_points", "update_panel", "geometry_volume", "primitive_geometry", "update_primitive", "find_descendants", "visual_persistence_diagnostics", "copy_element", "display_state", "visual_topf_preflight", "display_snapshot", "primitive_visual", "visual_persistence_v2", "native_ui_dispatch", "native_database_fault", "native_dialog_diagnostics", "database_state", "database_identity_snapshot", "camera_readback", "capture_observation", "connection_points", "connection_pair_check", "connection_trace", "bounded_model_validation", "graphical_selection", "set_graphical_selection", "measurements_batch", "coordinate_transform", "pml_nested_scope", "pml_alert_diagnostics", "pml_script_execution", "pml_script_status", "native_clash_check", "spatial_map_status", "transform_notification", "native_spatial_map_check_suspended", "drawlist_visual_preparation", "element_access", "drawlist_remove", "set_camera", "message_cursor_v1", "generated_csg" }
                        .Concat(new[] { "database_project_catalog_probe", "database_world_level_catalog_probe" })
                        .Concat(context is IE3d21EquipmentManifestContext ? new[] { "equipment_manifest" } : Array.Empty<string>())
                        .Concat(context is IE3d21EquipmentChildSchemaContext ? new[] { "equipment_child_schema" } : Array.Empty<string>())
                        .Concat(context is IE3d21ParagonCatalogueManifestContext ? new[] { "paragon_catalogue_manifest" } : Array.Empty<string>())
                        .Concat(context is IE3d21ModuleContext ? new[] { "module_context" } : Array.Empty<string>())
                        .Concat(context is IE3d21ParagonCatalogueSchemaContext ? new[] { "paragon_catalogue_schema" } : Array.Empty<string>())
                        .Concat(context is IE3d21ConnectivitySnapshotContext ? new[] { "piping_connectivity_snapshot" } : Array.Empty<string>())
                        .Concat(context is IE3d21PipingSpecificationReadContext ? new[] { "piping_specification_read" } : Array.Empty<string>())
                        .Concat(context is IE3d21TeamDatabaseIdentityManifestContext ? new[] { "team_database_identity_manifest" } : Array.Empty<string>())
                        .Concat(context is IE3d21ParagonComponentTemplateContext ? new[] { "paragon_component_template" } : Array.Empty<string>())
                        .Concat(context is IE3d21ParagonReferenceTraceContext ? new[] { "paragon_reference_trace" } : Array.Empty<string>())
                        .Concat(context is IE3d21ParagonComponentCopyContext ? new[] { "paragon_component_copy", "paragon_component_copy_status" } : Array.Empty<string>())
                        .Concat(context is IE3d21ParagonComponentParametersContext ? new[] { "paragon_component_parameters", "paragon_component_parameters_status" } : Array.Empty<string>())
                        .Concat(context is IE3d21PipeMemberManifestContext ? new[] { "pipe_member_manifest" } : Array.Empty<string>()).ToArray() };
            }
            else if (method == "GET" && path == "/api/v1/module/context")
            {
                var moduleContext = context as IE3d21ModuleContext;
                if (moduleContext == null)
                {
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "Module context is unavailable on this bridge." }, 404).ConfigureAwait(false);
                    return;
                }
                if (request.Request.QueryString.Count != 0 || request.Request.HasEntityBody)
                    throw new ArgumentException("Module context does not accept query parameters or a request body.");
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = moduleContext.ReadModuleContext(sessionId) };
            }
            else if (method == "POST" && path == "/api/v1/paragon/catalogue-schema")
            {
                var schemaContext = context as IE3d21ParagonCatalogueSchemaContext;
                if (schemaContext == null)
                {
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "PARAGON Catalogue schema is unavailable on this bridge." }, 404).ConfigureAwait(false);
                    return;
                }
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("PARAGON Catalogue schema accepts JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request, 8192).ConfigureAwait(false);
                if (body.Properties().Any(property => property.Name != "refno" && property.Name != "attributes" && property.Name != "include_constraints"))
                    throw new ArgumentException("Unknown PARAGON Catalogue schema parameter.");
                JArray attributes = body["attributes"] as JArray;
                if (body["attributes"] != null && (attributes == null || attributes.Count > ParagonCatalogueSchemaRequest.MaximumAttributes ||
                    attributes.Any(value => value.Type != JTokenType.String)))
                    throw new ArgumentException("attributes must be an array of at most 10 strings.");
                bool includeConstraints = ReadBoolean(body, "include_constraints", false);
                var schema = ParagonCatalogueSchemaRequest.Validate(ReadString(body, "refno", null),
                    attributes?.ToObject<List<string>>(), includeConstraints);
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = schemaContext.ReadParagonCatalogueSchema(schema.Refno, schema.Attributes, schema.IncludeConstraints, sessionId) };
            }
            else if (method == "POST" && path == "/api/v1/piping/connectivity-snapshot")
            {
                var snapshotContext = context as IE3d21ConnectivitySnapshotContext;
                if (snapshotContext == null)
                {
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "Piping connectivity snapshot is unavailable on this bridge." }, 404).ConfigureAwait(false);
                    return;
                }
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("Piping connectivity snapshots accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request, 65536).ConfigureAwait(false);
                ConnectivitySnapshotRequest snapshot = ConnectivitySnapshotRequest.Parse(body);
                string bridgeBuild = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D");
                response = new { ok = true, version = "E3D21", session_id = sessionId, bridge_build = bridgeBuild,
                    result = snapshotContext.ReadConnectivitySnapshot(snapshot, sessionId, bridgeBuild) };
            }
            else if (method == "POST" && path == "/api/v1/paragon/component-template")
            {
                var templateContext = context as IE3d21ParagonComponentTemplateContext;
                if (templateContext == null)
                {
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "PARAGON component templates are unavailable on this bridge." }, 404).ConfigureAwait(false);
                    return;
                }
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("PARAGON component templates accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request, 4096).ConfigureAwait(false);
                if (body.Properties().Any(property => property.Name != "refno"))
                    throw new ArgumentException("Unknown PARAGON component template parameter.");
                var template = ParagonComponentTemplateRequest.Validate(ReadString(body, "refno", null));
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = templateContext.ReadParagonComponentTemplate(template.Refno, sessionId) };
            }
            else if (method == "POST" && path == "/api/v1/paragon/component-parameters")
            {
                var parametersContext = context as IE3d21ParagonComponentParametersContext;
                if (parametersContext == null)
                {
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "PARAGON component parameter updates are unavailable on this bridge." }, 404).ConfigureAwait(false);
                    return;
                }
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("PARAGON component parameter updates accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request, 8192).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal)
                { "refno", "parameters", "parameter_bits", "dry_run", "request_id",
                    "expected_session_id", "expected_bridge_build", "expected_revision" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown PARAGON component parameter update argument.");
                paragonComponentParametersDryRun = ReadBoolean(body, "dry_run", true);
                var executionKeys = new[] { "request_id", "expected_session_id", "expected_bridge_build", "expected_revision" };
                if (paragonComponentParametersDryRun && executionKeys.Any(key => body.Property(key) != null))
                    throw new ArgumentException("Preview does not accept execution identifiers or a revision.");
                JArray bits = body["parameter_bits"] as JArray;
                if (bits == null || bits.Count < 1 || bits.Count > ParagonComponentParametersRequest.MaximumParameters
                    || bits.Any(value => value.Type != JTokenType.String))
                    throw new ArgumentException("parameter_bits must be an array of 1 to 128 canonical binary64 hex strings.");
                var update = ParagonComponentParametersRequest.ValidateWire(ReadString(body, "refno", null),
                    ReadParagonDoubleParameters(body), bits.Values<string>().ToList(), paragonComponentParametersDryRun,
                    ReadString(body, "request_id", null), ReadString(body, "expected_session_id", null),
                    ReadString(body, "expected_bridge_build", null), ReadString(body, "expected_revision", null));
                paragonComponentParametersDispatched = true;
                ParagonComponentParametersReply updated = parametersContext.SetParagonComponentParameters(update.Refno,
                    update.Parameters, update.DryRun, update.RequestId, update.ExpectedSessionId,
                    update.ExpectedBridgeBuild, update.ExpectedRevision, sessionId);
                if (updated == null || updated.Result == null)
                    throw new InvalidOperationException("The component parameter writer did not return a native receipt.");
                if (updated.Ok)
                    await WriteJsonAsync(request.Response, new { ok = true, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        result = updated.Result }, updated.HttpStatus).ConfigureAwait(false);
                else if (updated.NativeFaultLatched)
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error_code = "native_database_fault", native_fault_latched = true, retry_safe = false,
                        error = ParagonComponentParametersRequest.ErrorText(updated.Error), result = updated.Result }, updated.HttpStatus).ConfigureAwait(false);
                else
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = ParagonComponentParametersRequest.ErrorText(updated.Error), result = updated.Result }, updated.HttpStatus).ConfigureAwait(false);
                return;
            }
            else if (method == "GET" && path == "/api/v1/paragon/component-parameters/status")
            {
                var parametersContext = context as IE3d21ParagonComponentParametersContext;
                if (parametersContext == null)
                {
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "PARAGON component parameter status is unavailable on this bridge." }, 404).ConfigureAwait(false);
                    return;
                }
                if (request.Request.HasEntityBody || request.Request.ContentLength64 != 0
                    || !string.IsNullOrEmpty(request.Request.Headers["Transfer-Encoding"]))
                    throw new ArgumentException("Component parameter status does not accept a request body.");
                var query = request.Request.QueryString;
                foreach (string key in query.AllKeys)
                    if ((key != "request_id" && key != "expected_session_id") || query.GetValues(key).Length != 1)
                        throw new ArgumentException("Unknown or duplicate component parameter status argument.");
                string requestId = query["request_id"], expectedSessionId = query["expected_session_id"];
                ParagonComponentParametersRequest.ValidateStatus(requestId, expectedSessionId);
                if (expectedSessionId != sessionId)
                {
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        write_attempted = false, outcome_uncertain = false, retry_safe = false,
                        error = "The expected bridge session changed; this status query did not inspect another session." }, 409).ConfigureAwait(false);
                    return;
                }
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = parametersContext.GetParagonComponentParametersStatus(requestId, expectedSessionId, sessionId) };
            }
            else if (method == "POST" && path == "/api/v1/paragon/component-copy")
            {
                var copyContext = context as IE3d21ParagonComponentCopyContext;
                if (copyContext == null)
                {
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "PARAGON component generation is unavailable on this bridge." }, 404).ConfigureAwait(false);
                    return;
                }
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("PARAGON component generation accepts JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request, 8192).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal)
                { "source_refno", "parent_refno", "name", "recipe", "dry_run", "request_id",
                    "expected_session_id", "expected_bridge_build", "expected_revision" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown PARAGON component generation parameter.");
                paragonComponentCopyDryRun = ReadBoolean(body, "dry_run", true);
                var executionKeys = new[] { "request_id", "expected_session_id", "expected_bridge_build", "expected_revision" };
                if (paragonComponentCopyDryRun && executionKeys.Any(key => body.Property(key) != null))
                    throw new ArgumentException("Preview does not accept execution identifiers or a revision.");
                var copy = ParagonComponentCopyRequest.Validate(ReadString(body, "source_refno", null),
                    ReadString(body, "parent_refno", null), ReadString(body, "name", null),
                    ReadString(body, "recipe", ParagonComponentCopyRequest.RecipeName), paragonComponentCopyDryRun,
                    ReadString(body, "request_id", null), ReadString(body, "expected_session_id", null),
                    ReadString(body, "expected_bridge_build", null), ReadString(body, "expected_revision", null));
                paragonComponentCopyDispatched = true;
                ParagonComponentCopyReply copied = copyContext.CopyParagonComponent(copy.SourceRefno, copy.ParentRefno,
                    copy.Name, copy.Recipe, copy.DryRun, copy.RequestId, copy.ExpectedSessionId,
                    copy.ExpectedBridgeBuild, copy.ExpectedRevision, sessionId);
                if (copied == null || copied.Result == null)
                    throw new InvalidOperationException("The component generation reader did not return a native receipt.");
                if (copied.Ok)
                    await WriteJsonAsync(request.Response, new { ok = true, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        result = copied.Result }, copied.HttpStatus).ConfigureAwait(false);
                else if (copied.NativeFaultLatched)
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error_code = "native_database_fault", native_fault_latched = true, retry_safe = false,
                        error = ParagonComponentCopyRequest.ErrorText(copied.Error), result = copied.Result }, copied.HttpStatus).ConfigureAwait(false);
                else
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = ParagonComponentCopyRequest.ErrorText(copied.Error), result = copied.Result }, copied.HttpStatus).ConfigureAwait(false);
                return;
            }
            else if (method == "GET" && path == "/api/v1/paragon/component-copy/status")
            {
                var copyContext = context as IE3d21ParagonComponentCopyContext;
                if (copyContext == null)
                {
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "PARAGON component generation status is unavailable on this bridge." }, 404).ConfigureAwait(false);
                    return;
                }
                if (request.Request.HasEntityBody || request.Request.ContentLength64 != 0
                    || !string.IsNullOrEmpty(request.Request.Headers["Transfer-Encoding"]))
                    throw new ArgumentException("Component generation status does not accept a request body.");
                var query = request.Request.QueryString;
                foreach (string key in query.AllKeys)
                    if ((key != "request_id" && key != "expected_session_id") || query.GetValues(key).Length != 1)
                        throw new ArgumentException("Unknown or duplicate component generation status parameter.");
                string requestId = query["request_id"], expectedSessionId = query["expected_session_id"];
                ParagonComponentCopyRequest.ValidateStatus(requestId, expectedSessionId);
                if (expectedSessionId != sessionId)
                {
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        write_attempted = false, outcome_uncertain = false, retry_safe = false,
                        error = "The expected bridge session changed; this status query did not inspect another session." }, 409).ConfigureAwait(false);
                    return;
                }
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = copyContext.GetParagonComponentCopyStatus(requestId, expectedSessionId, sessionId) };
            }
            else if (method == "POST" && path == "/api/v1/paragon/reference-trace")
            {
                var traceContext = context as IE3d21ParagonReferenceTraceContext;
                if (traceContext == null)
                {
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "PARAGON reference traces are unavailable on this bridge." }, 404).ConfigureAwait(false);
                    return;
                }
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("PARAGON reference traces accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request, 4096).ConfigureAwait(false);
                if (body.Properties().Any(property => property.Name != "refno" && property.Name != "trace_limit"))
                    throw new ArgumentException("Unknown PARAGON reference trace parameter.");
                var trace = ParagonReferenceTraceRequest.Validate(ReadString(body, "refno", null),
                    ReadInteger(body, "trace_limit", ParagonReferenceTraceRequest.DefaultTraceLimit));
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = traceContext.ReadParagonReferenceTrace(trace.Refno, trace.TraceLimit, sessionId) };
            }
            else if (method == "POST" && path == "/api/v1/database/team-identity-manifest")
            {
                var identityContext = context as IE3d21TeamDatabaseIdentityManifestContext;
                if (identityContext == null)
                {
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "Team/database identity manifests are unavailable on this bridge." }, 404).ConfigureAwait(false);
                    return;
                }
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("Team/database identity manifests accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request, 4096).ConfigureAwait(false);
                if (body.Properties().Any(property => property.Name != "expected_context" && property.Name != "limit"))
                    throw new ArgumentException("Unknown Team/database identity manifest parameter.");
                var identity = TeamDatabaseIdentityManifestRequest.Validate(ReadString(body, "expected_context", null),
                    ReadInteger(body, "limit", TeamDatabaseIdentityManifestRequest.DefaultLimit));
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = identityContext.ReadTeamDatabaseIdentityManifest(sessionId, identity.ExpectedContext, identity.Limit) };
            }
            else if (method == "POST" && path == "/api/v1/piping/specification-read")
            {
                var specificationContext = context as IE3d21PipingSpecificationReadContext;
                if (specificationContext == null)
                {
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "Piping specification reads are unavailable on this bridge." }, 404).ConfigureAwait(false);
                    return;
                }
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("Piping specification reads accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request, 8192).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal)
                    { "refno", "expected_type", "database_type", "max_depth", "limit", "attributes" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown piping specification read parameter.");
                JArray attributes = body["attributes"] == null ? new JArray() : body["attributes"] as JArray;
                if (attributes == null || attributes.Count > ParagonCatalogueManifestRequest.MaximumAttributes ||
                    attributes.Any(value => value.Type != JTokenType.String))
                    throw new ArgumentException("attributes must be an array of at most 10 strings.");
                var specification = PipingSpecificationReadRequest.Validate(ReadString(body, "refno", null),
                    ReadString(body, "expected_type", null), ReadString(body, "database_type", null),
                    ReadInteger(body, "max_depth", 8), ReadInteger(body, "limit", 64),
                    attributes.ToObject<List<string>>(), sessionId);
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = specificationContext.ReadPipingSpecification(specification.Scope.Refno,
                        specification.ExpectedType, specification.DatabaseType, specification.Scope.MaxDepth,
                        specification.Scope.Limit, specification.Scope.Attributes, sessionId) };
            }
            else if (method == "POST" && path == "/api/v1/equipment/child-schema")
            {
                var childSchemaContext = context as IE3d21EquipmentChildSchemaContext;
                if (childSchemaContext == null)
                {
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "Equipment child schema is unavailable on this bridge." }, 404).ConfigureAwait(false);
                    return;
                }
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("Equipment child schema accepts JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request, 65536).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal)
                    { "parent_refno", "element_type", "read_target_refno", "attributes", "offset", "limit" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown equipment child schema parameter.");
                JArray attributes = body["attributes"] == null ? new JArray() : body["attributes"] as JArray;
                if (attributes == null || attributes.Count > EquipmentChildSchemaRequest.MaximumAttributes ||
                    attributes.Any(value => value.Type != JTokenType.String))
                    throw new ArgumentException("attributes must be an array of at most 20 strings.");
                JToken target = body["read_target_refno"];
                string readTargetRefno = target == null || target.Type == JTokenType.Null ? null :
                    ReadString(body, "read_target_refno", null);
                var schema = EquipmentChildSchemaRequest.Validate(ReadString(body, "parent_refno", null),
                    ReadString(body, "element_type", null), readTargetRefno, attributes.ToObject<List<string>>(),
                    ReadInteger(body, "offset", 0), ReadInteger(body, "limit", 50));
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = childSchemaContext.ReadEquipmentChildSchema(schema.ParentRefno, schema.ElementType,
                        schema.ReadTargetRefno, schema.Attributes, schema.Offset, schema.Limit, sessionId) };
            }
            else if (method == "GET" && path == "/api/v1/project")
            {
                response = new { ok = true, project = context.GetProject() };
            }
            else if (method == "GET" && path == "/api/v1/view/graphical-selection")
            {
                var query = request.Request.QueryString;
                foreach (string key in query.AllKeys)
                {
                    if (key != "limit") throw new ArgumentException("Unknown graphical selection parameter.");
                    if (query.GetValues(key).Length != 1) throw new ArgumentException("Duplicate graphical selection parameter.");
                }
                int limit = 50;
                if (query["limit"] != null && (!int.TryParse(query["limit"], System.Globalization.NumberStyles.None,
                    System.Globalization.CultureInfo.InvariantCulture, out limit) || limit < 1 || limit > 200))
                    throw new ArgumentException("limit must be an integer between 1 and 200.");
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    result = context.GetGraphicalSelection(limit) };
            }
            else if (method == "POST" && path == "/api/v1/view/graphical-selection")
            {
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("Graphical selection changes accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal) { "refnos", "dry_run", "expected_revision" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown graphical selection change parameter.");
                JArray refnos = body["refnos"] as JArray;
                if (refnos == null || refnos.Count > 32 || refnos.Any(value => value.Type != JTokenType.String))
                    throw new ArgumentException("refnos must be an array of between 0 and 32 explicit reference strings.");
                bool dryRun = ReadBoolean(body, "dry_run", true);
                response = new { ok = true, version = "E3D21", session_id = sessionId, dry_run = dryRun,
                    result = context.SetGraphicalSelection(refnos.ToObject<List<string>>(),
                        ReadString(body, "expected_revision", null), dryRun) };
            }
            else if (method == "POST" && path == "/api/v1/elements/access")
            {
                if (request.Request.QueryString.Count != 0) throw new ArgumentException("Element access accepts JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request).ConfigureAwait(false);
                if (body.Properties().Any(property => property.Name != "refnos"))
                    throw new ArgumentException("Unknown element access parameter.");
                JArray refnos = body["refnos"] as JArray;
                if (refnos == null || refnos.Any(value => value.Type != JTokenType.String))
                    throw new ArgumentException("refnos must be an array of explicit reference strings.");
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    result = context.GetElementAccess(refnos.ToObject<List<string>>()) };
            }
            else if (method == "POST" && path == "/api/v1/elements/measure")
            {
                if (request.Request.QueryString.Count != 0) throw new ArgumentException("Batch measurements accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal)
                    { "refnos", "measurements", "wrt", "volume_scope" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown batch measurement parameter.");
                JArray refnos = body["refnos"] as JArray;
                JArray measurements = body["measurements"] == null
                    ? new JArray("spatial", "bounds", "volume") : body["measurements"] as JArray;
                if (refnos == null || refnos.Any(value => value.Type != JTokenType.String))
                    throw new ArgumentException("refnos must be an array of strings.");
                if (measurements == null || measurements.Any(value => value.Type != JTokenType.String))
                    throw new ArgumentException("measurements must be an array of strings.");
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    result = context.GetMeasurementsBatch(refnos.ToObject<List<string>>(), measurements.ToObject<List<string>>(),
                        ReadString(body, "wrt", "WORLD"), ReadString(body, "volume_scope", "element")) };
            }
            else if (method == "POST" && path == "/api/v1/coordinates/transform")
            {
                if (request.Request.QueryString.Count != 0) throw new ArgumentException("Coordinate transforms accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal) { "from_wrt", "to_wrt", "points", "vectors" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)) || body["from_wrt"] == null || body["to_wrt"] == null)
                    throw new ArgumentException("Coordinate transforms require from_wrt and to_wrt and accept only points and vectors.");
                List<double[]> points = ReadCoordinateVectors(body["points"], "points");
                List<double[]> vectors = ReadCoordinateVectors(body["vectors"], "vectors");
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = context.TransformCoordinates(ReadString(body, "from_wrt", ""), ReadString(body, "to_wrt", ""), points, vectors) };
            }
            else if (method == "POST" && path == "/api/v1/clash/check")
            {
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("Native clash checks accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal)
                    { "first_refnos", "second_refnos", "clearance", "result_limit", "dry_run" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown native clash parameter.");
                JArray first = body["first_refnos"] as JArray;
                JArray second = body["second_refnos"] as JArray;
                if (first == null || first.Any(value => value.Type != JTokenType.String))
                    throw new ArgumentException("first_refnos must be an array of explicit reference strings.");
                if (second == null || second.Any(value => value.Type != JTokenType.String))
                    throw new ArgumentException("second_refnos must be an array of explicit reference strings.");
                bool dryRun = ReadBoolean(body, "dry_run", true);
                response = new { ok = true, version = "E3D21", session_id = sessionId, dry_run = dryRun,
                    result = context.CheckNativeClashes(first.ToObject<List<string>>(), second.ToObject<List<string>>(),
                        ReadFiniteNumber(body, "clearance", 0), ReadInteger(body, "result_limit", 50), dryRun) };
            }
            else if (method == "GET" && path == "/api/v1/database/state")
            {
                DatabaseStateRequest query = DatabaseStateRequest.Parse(request.Request.QueryString);
                response = new { ok = true, version = "E3D21", result = context.GetDatabaseState(
                    query.Offset, query.Limit, query.ExpectedContext, sessionId) };
            }
            else if (method == "GET" && path == "/api/v1/database/identity-snapshot")
            {
                var query = request.Request.QueryString;
                if (query.Count != 1 || query.AllKeys.Any(key => key != "expected_context") ||
                    query.GetValues("expected_context") == null || query.GetValues("expected_context").Length != 1)
                    throw new ArgumentException("Database identity snapshot requires exactly one expected_context parameter.");
                string expectedContext = query["expected_context"];
                DatabaseStateRequest.Validate(0, 1, expectedContext);
                if (string.IsNullOrEmpty(expectedContext))
                    throw new ArgumentException("expected_context is required for a database identity snapshot.");
                response = new { ok = true, version = "E3D21", result = context.GetDatabaseIdentitySnapshot(expectedContext, sessionId) };
            }
            else if (method == "POST" && path == "/api/v1/database/project-catalog-probe")
            {
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("Project database catalog probes accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request, 4096).ConfigureAwait(false);
                if (body.Properties().Any(property => property.Name != "team" && property.Name != "name" &&
                    property.Name != "database_number" && property.Name != "expected_context"))
                    throw new ArgumentException("Unknown project database catalog probe parameter.");
                string team = ReadString(body, "team", null);
                string name = ReadString(body, "name", null);
                string expectedContext = ReadString(body, "expected_context", null);
                int? databaseNumber = body["database_number"] == null ? (int?)null : ReadInteger(body, "database_number", 0);
                DatabaseStateRequest.ValidateProjectCatalogProbe(expectedContext, ref team, ref name, databaseNumber);
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    result = context.GetProjectCatalogProbe(team, name, databaseNumber, expectedContext, sessionId) };
            }
            else if (method == "GET" && path == "/api/v1/spatial-map/status")
            {
                var query = request.Request.QueryString;
                foreach (string key in query.AllKeys)
                {
                    if (key != "database_number" && key != "extract_number" && key != "expected_context")
                        throw new ArgumentException("Unknown spatial-map status parameter.");
                    if (query.GetValues(key).Length != 1)
                        throw new ArgumentException("Duplicate spatial-map status parameter.");
                }
                int databaseNumber, extractNumber;
                if (!int.TryParse(query["database_number"], System.Globalization.NumberStyles.None,
                    System.Globalization.CultureInfo.InvariantCulture, out databaseNumber) || databaseNumber < 1)
                    throw new ArgumentException("database_number is required and must be an integer between 1 and 2147483647.");
                if (!int.TryParse(query["extract_number"], System.Globalization.NumberStyles.None,
                    System.Globalization.CultureInfo.InvariantCulture, out extractNumber) || extractNumber < 0)
                    throw new ArgumentException("extract_number is required and must be an integer between 0 and 2147483647.");
                string expectedContext = query["expected_context"] ?? "";
                DatabaseStateRequest.Validate(0, 1, expectedContext);
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    result = context.GetSpatialMapStatus(databaseNumber, extractNumber, expectedContext, sessionId) };
            }
            else if (method == "POST" && path == "/api/v1/elements/find-descendants")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal)
                    { "root_refno", "element_type", "name_contains", "max_depth", "limit", "cursor" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown descendant search parameter.");
                int? maxDepth = body["max_depth"] == null ? (int?)null : ReadInteger(body, "max_depth", 64);
                response = new { ok = true, version = "E3D21", result = context.FindDescendants(
                    ReadString(body, "root_refno", ""), ReadString(body, "element_type", null),
                    ReadString(body, "name_contains", null), maxDepth, ReadInteger(body, "limit", 50),
                    ReadString(body, "cursor", ""), sessionId) };
            }
            else if (method == "GET" && path == "/api/v1/logs")
            {
                int tail = Clamp(ParseInt(request.Request.QueryString["tail"], 100), 1, 500);
                response = new { ok = true, version = "E3D21", lines = context.ReadLogs(tail) };
            }
            else if (method == "GET" && path == "/api/v1/messages")
            {
                int tail = Clamp(ParseInt(request.Request.QueryString["tail"], 100), 1, 500);
                response = new { ok = true, version = "E3D21", messages = context.ReadMessages(tail) };
            }
            else if (method == "GET" && path == "/api/v1/messages/window")
            {
                MessageWindowRequest query = MessageWindowRequest.Parse(request.Request.QueryString);
                long afterSequence = messageCursors.Decode(query.Cursor, sessionId);
                IE3d21MessageWindowContext managedContext = context as IE3d21MessageWindowContext;
                if (managedContext == null)
                    throw new MessageCursorException("message_cursor_unavailable", "Managed message windows are unavailable.", 501);
                MessageWindowSnapshot snapshot = managedContext.ReadMessageWindow(afterSequence, query.Limit, query.Checkpoint);
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = snapshot.ToResult(messageCursors.Encode(sessionId, snapshot.NextSequence)) };
            }
            else if (method == "GET" && path == "/api/v1/command-state")
            {
                response = new { ok = true, version = "E3D21", state = SanitizeDiagnostics(context.CommandState()) };
            }
            else if (method == "POST" && path == "/api/v1/command-cancel")
            {
                response = new { ok = true, version = "E3D21", result = SanitizeDiagnostics(context.CancelCommand()) };
            }
            else if (method == "GET" && path == "/api/v1/drawlist")
            {
                response = new { ok = true, version = "E3D21", drawlist = context.GetDrawListSnapshot() };
            }
            else if (method == "GET" && path == "/api/v1/view/capture")
            {
                response = new { ok = true, version = "E3D21", result = context.CaptureView(
                    ParseInt(request.Request.QueryString["width"], 1280), ParseInt(request.Request.QueryString["height"], 960)) };
            }
            else if (method == "GET" && path == "/api/v1/view/state")
            {
                response = new { ok = true, version = "E3D21", result = context.GetViewState() };
            }
            else if (method == "GET" && path == "/api/v1/view/clipping-state")
            {
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("View clipping state does not accept query parameters.");
                response = new { ok = true, version = "E3D21", result = context.GetViewClippingState() };
            }
            else if (method == "POST" && path == "/api/v1/view/set")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                HashSet<string> allowed = new HashSet<string>(StringComparer.Ordinal)
                    { "preset", "fit", "target_refno", "padding", "dry_run" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown view parameter; expected preset, fit, target_refno, padding or dry_run.");
                response = new { ok = true, version = "E3D21", result = context.SetView(
                    ReadString(body, "preset", "keep"), ReadString(body, "fit", "none"),
                    ReadString(body, "target_refno", ""), ReadFiniteNumber(body, "padding", 1.15),
                    ReadBoolean(body, "dry_run", true)) };
            }
            else if (method == "POST" && path == "/api/v1/view/camera/set")
            {
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("Camera changes accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal)
                    { "centre", "direction", "radius", "perspective", "extra_twist_degrees", "expected_revision", "dry_run" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown camera change parameter.");
                string[] required = { "centre", "direction", "radius", "perspective", "extra_twist_degrees" };
                if (required.Any(name => body[name] == null || body[name].Type == JTokenType.Null))
                    throw new ArgumentException("centre, direction, radius, perspective and extra_twist_degrees are required and cannot be null.");
                bool dryRun = ReadBoolean(body, "dry_run", true);
                response = new { ok = true, version = "E3D21", session_id = sessionId, dry_run = dryRun,
                    result = context.SetCamera(ReadVector(body, "centre"), ReadVector(body, "direction"),
                        ReadFiniteNumber(body, "radius", 0), ReadBoolean(body, "perspective", false),
                        ReadFiniteNumber(body, "extra_twist_degrees", 0), ReadString(body, "expected_revision", null), dryRun) };
            }
            else if (method == "POST" && path == "/api/v1/view/fit")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                bool shaded = (bool?)body["shaded"] ?? true;
                bool isometric = (bool?)body["isometric"] ?? true;
                bool autoAddCurrent = (bool?)body["auto_add_current"] ?? true;
                response = new { ok = true, version = "E3D21", result = context.FitView(shaded, isometric, autoAddCurrent) };
            }
            else if (method == "POST" && path == "/api/v1/view/restore")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                bool fitView = (bool?)body["fit"] ?? true;
                response = new { ok = true, version = "E3D21", result = context.RestoreDrawList(fitView) };
            }
            else if (method == "GET" && path == "/api/v1/view/element-state")
            {
                if (request.Request.QueryString.AllKeys.Any(key => key != "refno"))
                    throw new ArgumentException("Unknown display state parameter.");
                response = new { ok = true, version = "E3D21", result = context.GetDisplayState(
                    request.Request.QueryString["refno"] ?? "") };
            }
            else if (method == "POST" && path == "/api/v1/drawlist/visual")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                string refno = (string)body["refno"] ?? "";
                string colour = (string)body["colour"] ?? "";
                int? transparency = (int?)body["transparency"];
                bool? edges = (bool?)body["edges"];
                bool? visibility = (bool?)body["visibility"];
                bool? shallow = (bool?)body["shallow"];
                bool add = (bool?)body["add"] ?? true;
                bool dryRun = (bool?)body["dry_run"] ?? true;
                response = new { ok = true, version = "E3D21", result = context.SetDrawListVisual(refno, colour, transparency, edges, visibility, shallow, add, dryRun) };
            }
            else if (method == "POST" && path == "/api/v1/drawlist/remove")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal) { "refno", "expected_revision", "dry_run" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown Drawlist removal parameter.");
                response = new { ok = true, version = "E3D21", result = context.RemoveDrawListMember(
                    ReadString(body, "refno", ""), ReadString(body, "expected_revision", null), ReadBoolean(body, "dry_run", true)) };
            }
            else if (method == "GET" && path == "/api/v1/view/display-snapshot")
            {
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("Display snapshot does not accept query parameters.");
                response = new { ok = true, version = "E3D21", result = context.GetDisplaySnapshot() };
            }
            else if (method == "POST" && path == "/api/v1/view/primitive-visual")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal)
                    { "refno", "expected_revision", "colour", "transparency", "edges", "visibility", "shallow", "dry_run" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown primitive visual parameter.");
                int? transparency = body["transparency"] == null ? (int?)null : ReadInteger(body, "transparency", 0);
                response = new { ok = true, version = "E3D21", result = context.SetPrimitiveVisual(
                    ReadString(body, "refno", ""), ReadString(body, "expected_revision", null),
                    ReadString(body, "colour", ""), transparency,
                    ReadNullableBoolean(body, "edges"), ReadNullableBoolean(body, "visibility"),
                    ReadNullableBoolean(body, "shallow"), ReadBoolean(body, "dry_run", true)) };
            }
            else if (method == "GET" && path == "/api/v1/element/members")
            {
                string refno = request.Request.QueryString["refno"] ?? "";
                string expectedType = request.Request.QueryString["expected_type"] ?? "";
                string cursor = request.Request.QueryString["cursor"] ?? "";
                int limit = ParseInt(request.Request.QueryString["limit"], 50);
                response = new { ok = true, version = "E3D21", result = context.ListMembers(refno, expectedType, cursor, limit, sessionId) };
            }
            else if (method == "GET" && path == "/api/v1/element")
            {
                string refno = request.Request.QueryString["refno"] ?? "";
                string expectedType = request.Request.QueryString["expected_type"] ?? "";
                int depth = Clamp(ParseInt(request.Request.QueryString["depth"], 2), 0, 8);
                int limit = Clamp(ParseInt(request.Request.QueryString["limit"], 100), 1, 500);
                response = new { ok = true, version = "E3D21", element = context.GetElementSnapshot(refno, expectedType, depth, limit) };
            }
            else if (method == "GET" && path == "/api/v1/element/spatial")
            {
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = context.GetSpatialInfo(
                    request.Request.QueryString["refno"] ?? "", request.Request.QueryString["wrt"] ?? "WORLD") };
            }
            else if (method == "POST" && path == "/api/v1/element/connection-points")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                if (body.Properties().Any(property => property.Name != "refno" && property.Name != "wrt" && property.Name != "point_numbers" && property.Name != "expected_type"))
                    throw new ArgumentException("Unknown connection-points parameter.");
                JArray numbers = body["point_numbers"] == null ? new JArray() : body["point_numbers"] as JArray;
                if (numbers == null || numbers.Count > 64 || numbers.Any(value => value.Type != JTokenType.Integer))
                    throw new ArgumentException("point_numbers must be an array of at most 64 unique integers from 1 through 10000.");
                var points = new List<int>();
                foreach (JToken token in numbers)
                {
                    int number;
                    if (!int.TryParse(token.ToString(), out number) || number < 1 || number > 10000 || points.Contains(number))
                        throw new ArgumentException("point_numbers must be an array of at most 64 unique integers from 1 through 10000.");
                    points.Add(number);
                }
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = context.GetConnectionPoints(
                    ReadString(body, "refno", ""), ReadString(body, "wrt", "WORLD"), points, ReadString(body, "expected_type", "")) };
            }
            else if (method == "POST" && path == "/api/v1/element/connection-pair-check")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal)
                    { "first_refno", "first_expected_type", "first_point_number", "second_refno",
                      "second_expected_type", "second_point_number", "tolerance_mm" };
                var required = new[] { "first_refno", "first_point_number", "second_refno",
                    "second_point_number", "tolerance_mm" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)) ||
                    required.Any(name => body[name] == null))
                    throw new ArgumentException("Connection-pair check requires exactly first_refno, first_point_number, second_refno, second_point_number and tolerance_mm.");
                response = new { ok = true, version = "E3D21", result = context.CheckConnectionPair(
                    ReadString(body, "first_refno", ""), ReadInteger(body, "first_point_number", 0),
                    ReadString(body, "second_refno", ""), ReadInteger(body, "second_point_number", 0),
                    ReadFiniteNumber(body, "tolerance_mm", 0),
                    ReadString(body, "first_expected_type", ""), ReadString(body, "second_expected_type", "")) };
            }
            else if (method == "POST" && path == "/api/v1/element/connection-trace")
            {
                if (request.Request.QueryString.Count != 0) throw new ArgumentException("Connection traces accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request, 65536).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal) { "starts", "max_hops", "max_nodes" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown connection trace parameter.");
                JArray starts = body["starts"] as JArray;
                if (starts == null || starts.Count < 1 || starts.Count > 16)
                    throw new ArgumentException("starts must contain between 1 and 16 explicit element/P-point pairs.");
                var refnos = new List<string>();
                var numbers = new List<int>();
                var expectedTypes = new List<string>();
                foreach (JToken token in starts)
                {
                    JObject item = token as JObject;
                    if (item == null || (item.Properties().Count() != 2 && item.Properties().Count() != 3) ||
                        item["refno"] == null || item["point_number"] == null ||
                        item.Properties().Any(property => property.Name != "refno" && property.Name != "point_number" && property.Name != "expected_type") ||
                        item["refno"].Type != JTokenType.String || item["point_number"].Type != JTokenType.Integer)
                        throw new ArgumentException("Each start must contain refno and integer point_number, with optional expected_type.");
                    string expectedType = item["expected_type"] == null ? null :
                        item["expected_type"].Type == JTokenType.String ? item["expected_type"].Value<string>() : null;
                    if (item["expected_type"] != null && expectedType == null)
                        throw new ArgumentException("expected_type must be a string when supplied.");
                    int number = item["point_number"].Value<int>();
                    if (number < 1 || number > 10000) throw new ArgumentException("point_number must be from 1 through 10000.");
                    refnos.Add(item["refno"].Value<string>());
                    expectedTypes.Add(expectedType);
                    numbers.Add(number);
                }
                int maxHops = ReadInteger(body, "max_hops", 8);
                int maxNodes = ReadInteger(body, "max_nodes", 64);
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = context.TraceConnectionNetwork(refnos, expectedTypes, numbers, maxHops, maxNodes) };
            }
            else if (method == "GET" && path == "/api/v1/element/bounds")
            {
                response = new { ok = true, version = "E3D21", result = context.GetGeometryBounds(request.Request.QueryString["refno"] ?? "") };
            }
            else if (method == "POST" && path == "/api/v1/element/geometry-relation")
            {
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("Geometry relation checks accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal) { "test_refno", "against_refno", "include_box" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)) ||
                    body["test_refno"] == null || body["against_refno"] == null)
                    throw new ArgumentException("Geometry relation checks require test_refno and against_refno, with optional include_box.");
                response = new { ok = true, version = "E3D21", result = context.CheckGeometryRelation(
                    ReadString(body, "test_refno", ""), ReadString(body, "against_refno", ""),
                    ReadBoolean(body, "include_box", false)) };
            }
            else if (method == "POST" && path == "/api/v1/element/geometry-relation-batch")
            {
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("Geometry relation batch checks accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal) { "pairs", "include_box" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)) || body["pairs"] == null)
                    throw new ArgumentException("Geometry relation batches require pairs and accept optional include_box.");
                JArray pairs = body["pairs"] as JArray;
                if (pairs == null || pairs.Count < 1 || pairs.Count > 32)
                    throw new ArgumentException("pairs must contain between 1 and 32 [test_refno, against_refno] entries.");
                var parsed = new List<string[]>();
                foreach (JToken token in pairs)
                {
                    JArray pair = token as JArray;
                    if (pair == null || pair.Count != 2 || pair.Any(value => value.Type != JTokenType.String))
                        throw new ArgumentException("Each pairs entry must contain exactly two strings.");
                    parsed.Add(pair.ToObject<string[]>());
                }
                response = new { ok = true, version = "E3D21", result = context.CheckGeometryRelationBatch(
                    parsed, ReadBoolean(body, "include_box", false)) };
            }
            else if (method == "GET" && path == "/api/v1/element/volume")
            {
                string[] keys = request.Request.QueryString.AllKeys;
                if (keys.Any(key => key != "refno" && key != "scope"))
                    throw new ArgumentException("Unknown geometry volume parameter.");
                response = new { ok = true, version = "E3D21", result = context.GetGeometryVolume(
                    request.Request.QueryString["refno"] ?? "", request.Request.QueryString["scope"] ?? "element") };
            }
            else if (method == "GET" && path == "/api/v1/validate")
            {
                var query = request.Request.QueryString;
                var allowed = new HashSet<string>(StringComparer.Ordinal)
                    { "refno", "depth", "limit", "required_types", "include_snapshot" };
                foreach (string key in query.AllKeys)
                {
                    if (!allowed.Contains(key))
                        throw new ArgumentException("Unknown model validation parameter.");
                    string[] values = query.GetValues(key);
                    if (values == null || values.Length != 1)
                        throw new ArgumentException("Duplicate model validation parameter: " + key);
                }
                Func<string, int, int, int, int> readInteger = delegate(string name, int fallback, int minimum, int maximum)
                {
                    string raw = query[name];
                    if (raw == null) return fallback;
                    int value;
                    if (!int.TryParse(raw, System.Globalization.NumberStyles.None,
                        System.Globalization.CultureInfo.InvariantCulture, out value) || value < minimum || value > maximum)
                        throw new ArgumentException(name + " must be an integer between " + minimum + " and " + maximum + ".");
                    return value;
                };
                string refno = query["refno"] ?? "";
                int depth = readInteger("depth", 4, 0, 8);
                int limit = readInteger("limit", 200, 1, 2000);
                string include = query["include_snapshot"];
                if (include != null && include != "true" && include != "false")
                    throw new ArgumentException("include_snapshot must be true or false.");
                bool includeSnapshot = include == null || include == "true";
                string required = query["required_types"] ?? "";
                IList<string> requiredTypes = required.Split(new[] { ',', ';' }, StringSplitOptions.RemoveEmptyEntries).Select(value => value.Trim()).ToList();
                response = new { ok = true, version = "E3D21", validation = context.ValidateModel(refno, depth, limit, requiredTypes, includeSnapshot) };
            }
            else if (method == "POST" && path == "/api/v1/element/schema")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                if (body.Properties().Any(property => property.Name != "refno" && property.Name != "attributes" &&
                    property.Name != "offset" && property.Name != "limit" && property.Name != "include_constraints" &&
                    property.Name != "expected_type"))
                    throw new ArgumentException("Unknown element-schema parameter.");
                JArray attributes = body["attributes"] == null ? new JArray() : body["attributes"] as JArray;
                if (attributes == null || attributes.Any(value => value.Type != JTokenType.String))
                    throw new ArgumentException("attributes must be an array of strings");
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = context.GetElementSchema(
                    (string)body["refno"] ?? "", attributes.ToObject<List<string>>(),
                    (int?)body["offset"] ?? 0, (int?)body["limit"] ?? 50,
                    ReadBoolean(body, "include_constraints", false), ReadString(body, "expected_type", "")) };
            }
            else if (method == "POST" && path == "/api/v1/element/attributes")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                if (body.Properties().Any(property => property.Name != "refno" && property.Name != "attributes" &&
                    property.Name != "expected_type"))
                    throw new ArgumentException("Unknown attribute-read parameter.");
                JArray attributes = body["attributes"] as JArray;
                if (attributes == null || attributes.Any(value => value.Type != JTokenType.String))
                    throw new ArgumentException("attributes must be an array of strings");
                response = new { ok = true, version = "E3D21", result = context.ReadAttributes(
                    (string)body["refno"] ?? "", attributes.ToObject<List<string>>(),
                    ReadString(body, "expected_type", "")) };
            }
            else if (method == "POST" && path == "/api/v1/elements/attributes/batch")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                if (body.Properties().Any(property => property.Name != "refnos" && property.Name != "attributes"))
                    throw new ArgumentException("Unknown batch attribute-read parameter.");
                JArray refnos = body["refnos"] as JArray;
                JArray attributes = body["attributes"] as JArray;
                if (refnos == null || refnos.Any(value => value.Type != JTokenType.String))
                    throw new ArgumentException("refnos must be an array of strings.");
                if (attributes == null || attributes.Any(value => value.Type != JTokenType.String))
                    throw new ArgumentException("attributes must be an array of strings.");
                response = new { ok = true, version = "E3D21", result = context.ReadAttributesBatch(
                    refnos.ToObject<List<string>>(), attributes.ToObject<List<string>>()) };
            }
            else if (method == "POST" && path == "/api/v1/equipment/manifest")
            {
                var manifestContext = context as IE3d21EquipmentManifestContext;
                if (manifestContext == null)
                {
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        error = "The equipment manifest is unavailable on this bridge." }, 404).ConfigureAwait(false);
                    return;
                }
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("Equipment manifests accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request, 8192).ConfigureAwait(false);
                if (body.Properties().Any(property => property.Name != "refno" && property.Name != "max_depth" &&
                    property.Name != "limit" && property.Name != "attributes"))
                    throw new ArgumentException("Unknown equipment manifest parameter.");
                JArray attributes = body["attributes"] as JArray;
                if (body["attributes"] != null && (attributes == null || attributes.Count > EquipmentManifestRequest.MaximumAttributes ||
                    attributes.Any(value => value.Type != JTokenType.String)))
                    throw new ArgumentException("attributes must be an array of at most 10 strings.");
                EquipmentManifestRequest manifest = EquipmentManifestRequest.Validate(ReadString(body, "refno", null),
                    ReadInteger(body, "max_depth", EquipmentManifestRequest.DefaultMaxDepth),
                    ReadInteger(body, "limit", EquipmentManifestRequest.DefaultLimit), attributes?.ToObject<List<string>>());
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = manifestContext.ReadEquipmentManifest(manifest.Refno, manifest.MaxDepth, manifest.Limit, manifest.Attributes, sessionId) };
            }
            else if (method == "POST" && path == "/api/v1/piping/member-manifest")
            {
                var manifestContext = context as IE3d21PipeMemberManifestContext;
                if (manifestContext == null)
                {
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        error = "The pipe member manifest is unavailable on this bridge." }, 404).ConfigureAwait(false);
                    return;
                }
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("Pipe member manifests accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request, 8192).ConfigureAwait(false);
                if (body.Properties().Any(property => property.Name != "refno" && property.Name != "max_depth" &&
                    property.Name != "limit" && property.Name != "attributes"))
                    throw new ArgumentException("Unknown pipe member manifest parameter.");
                JArray attributes = body["attributes"] as JArray;
                if (body["attributes"] != null && (attributes == null || attributes.Count > PipeMemberManifestRequest.MaximumAttributes ||
                    attributes.Any(value => value.Type != JTokenType.String)))
                    throw new ArgumentException("attributes must be an array of at most 10 strings.");
                var manifest = PipeMemberManifestRequest.Validate(ReadString(body, "refno", null),
                    ReadInteger(body, "max_depth", PipeMemberManifestRequest.DefaultDepth),
                    ReadInteger(body, "limit", PipeMemberManifestRequest.DefaultLimit), attributes?.ToObject<List<string>>());
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = manifestContext.ReadPipeMemberManifest(manifest.Refno, manifest.MaxDepth, manifest.Limit, manifest.Attributes) };
            }
            else if (method == "POST" && path == "/api/v1/paragon/catalogue-manifest")
            {
                var manifestContext = context as IE3d21ParagonCatalogueManifestContext;
                if (manifestContext == null)
                {
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        error = "The PARAGON catalogue manifest is unavailable on this bridge." }, 404).ConfigureAwait(false);
                    return;
                }
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("PARAGON catalogue manifests accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request, 8192).ConfigureAwait(false);
                if (body.Properties().Any(property => property.Name != "refno" && property.Name != "max_depth" &&
                    property.Name != "limit" && property.Name != "attributes"))
                    throw new ArgumentException("Unknown PARAGON catalogue manifest parameter.");
                JArray attributes = body["attributes"] as JArray;
                if (body["attributes"] != null && (attributes == null || attributes.Count > ParagonCatalogueManifestRequest.MaximumAttributes ||
                    attributes.Any(value => value.Type != JTokenType.String)))
                    throw new ArgumentException("attributes must be an array of at most 10 strings.");
                var manifest = ParagonCatalogueManifestRequest.Validate(ReadString(body, "refno", null),
                    ReadInteger(body, "max_depth", ParagonCatalogueManifestRequest.DefaultMaxDepth),
                    ReadInteger(body, "limit", ParagonCatalogueManifestRequest.DefaultLimit), attributes?.ToObject<List<string>>());
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    result = manifestContext.ReadParagonCatalogueManifest(manifest.Refno, manifest.MaxDepth, manifest.Limit, manifest.Attributes, sessionId) };
            }
            else if (method == "POST" && path == "/api/v1/element/create")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                HashSet<string> allowed = new HashSet<string>(StringComparer.Ordinal)
                    { "parent_refno", "element_type", "name", "attributes", "position", "x_axis", "y_axis", "wrt", "expected_revision", "dry_run" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown element creation parameter.");
                JObject attrs = body["attributes"] == null ? new JObject() : body["attributes"] as JObject;
                if (attrs == null || attrs.Count > 20 || attrs.Properties().Any(p => p.Value.Type != JTokenType.String))
                    throw new ArgumentException("attributes must contain at most 20 string values");
                response = new { ok = true, version = "E3D21", result = context.CreateElement(
                    ReadString(body, "parent_refno", ""), ReadString(body, "element_type", ""), ReadString(body, "name", ""),
                    attrs.ToObject<Dictionary<string,string>>(), ReadVector(body, "position"), ReadVector(body, "x_axis"),
                    ReadVector(body, "y_axis"), ReadString(body, "wrt", "OWNER"),
                    ReadString(body, "expected_revision", null), ReadBoolean(body, "dry_run", true)) };
            }
            else if (method == "GET" && path == "/api/v1/panel/geometry")
            {
                var query = request.Request.QueryString;
                if (query.AllKeys.Any(key => key != "refno" && key != "include_world" && key != "include_boundary"))
                    throw new ArgumentException("Unknown panel geometry parameter.");
                foreach (string key in new[] { "refno", "include_world", "include_boundary" })
                    if (query.GetValues(key) != null && query.GetValues(key).Length != 1)
                        throw new ArgumentException("Duplicate panel geometry parameter: " + key + ".");
                bool includeWorld = false;
                string includeWorldValue = query["include_world"];
                if (includeWorldValue != null && !bool.TryParse(includeWorldValue, out includeWorld))
                    throw new ArgumentException("include_world must be true or false.");
                bool includeBoundary = false;
                string includeBoundaryValue = query["include_boundary"];
                if (includeBoundaryValue != null && !bool.TryParse(includeBoundaryValue, out includeBoundary))
                    throw new ArgumentException("include_boundary must be true or false.");
                response = new { ok = true, version = "E3D21", result = context.GetPanelGeometry(query["refno"] ?? "", includeWorld, includeBoundary) };
            }
            else if (method == "GET" && path == "/api/v1/element/primitive")
            {
                if (request.Request.QueryString.AllKeys.Any(key => key != "refno"))
                    throw new ArgumentException("Unknown primitive geometry parameter.");
                response = new { ok = true, version = "E3D21", result = context.GetPrimitiveGeometry(
                    request.Request.QueryString["refno"] ?? "") };
            }
            else if (method == "GET" && path == "/api/v1/element/generated-csg")
            {
                var query = request.Request.QueryString;
                foreach (string key in query.AllKeys)
                {
                    if (key != "refno" && key != "max_trees" && key != "max_items")
                        throw new ArgumentException("Unknown generated CSG parameter.");
                    if (query.GetValues(key).Length != 1)
                        throw new ArgumentException("Duplicate generated CSG parameter.");
                }
                int maxTrees = 8;
                int maxItems = 64;
                if (query["max_trees"] != null && (!int.TryParse(query["max_trees"], System.Globalization.NumberStyles.None,
                    System.Globalization.CultureInfo.InvariantCulture, out maxTrees) || maxTrees < 1 || maxTrees > 16))
                    throw new ArgumentException("max_trees must be an integer between 1 and 16.");
                if (query["max_items"] != null && (!int.TryParse(query["max_items"], System.Globalization.NumberStyles.None,
                    System.Globalization.CultureInfo.InvariantCulture, out maxItems) || maxItems < 1 || maxItems > 256))
                    throw new ArgumentException("max_items must be an integer between 1 and 256.");
                response = new { ok = true, version = "E3D21", session_id = sessionId,
                    result = context.GetGeneratedCsg(query["refno"] ?? "", maxTrees, maxItems) };
            }
            else if (method == "POST" && path == "/api/v1/element/primitive")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                HashSet<string> allowed = new HashSet<string>(StringComparer.Ordinal)
                    { "refno", "expected_revision", "dimensions", "position", "x_axis", "y_axis", "wrt", "dry_run" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown primitive update parameter.");
                Dictionary<string, double> dimensions = null;
                if (body["dimensions"] != null)
                {
                    JObject entries = body["dimensions"] as JObject;
                    if (entries == null || entries.Count < 1 || entries.Count > 3)
                        throw new ArgumentException("dimensions must contain 1 to 3 numerical fields in millimetres.");
                    dimensions = new Dictionary<string, double>(StringComparer.Ordinal);
                    foreach (JProperty property in entries.Properties())
                    {
                        string canonical = property.Name.Trim().ToUpperInvariant();
                        if (dimensions.ContainsKey(canonical))
                            throw new ArgumentException("Duplicate primitive dimension: " + canonical);
                        dimensions.Add(canonical, ReadFiniteNumber(entries, property.Name, 0));
                    }
                }
                response = new { ok = true, version = "E3D21", result = context.UpdatePrimitive(
                    ReadString(body, "refno", ""), ReadString(body, "expected_revision", null), dimensions,
                    ReadVector(body, "position"), ReadVector(body, "x_axis"), ReadVector(body, "y_axis"),
                    ReadString(body, "wrt", "OWNER"), ReadBoolean(body, "dry_run", true)) };
            }
            else if (method == "POST" && path == "/api/v1/element/copy")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                HashSet<string> allowed = new HashSet<string>(StringComparer.Ordinal)
                    { "source_refno", "parent_refno", "name", "position", "x_axis", "y_axis", "wrt", "expected_revision", "dry_run" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown element copy parameter.");
                response = new { ok = true, version = "E3D21", result = context.CopyElement(
                    ReadString(body, "source_refno", ""), ReadString(body, "parent_refno", ""),
                    ReadString(body, "name", ""), ReadVector(body, "position"),
                    ReadVector(body, "x_axis"), ReadVector(body, "y_axis"), ReadString(body, "wrt", "OWNER"),
                    ReadString(body, "expected_revision", null), ReadBoolean(body, "dry_run", true)) };
            }
            else if (method == "POST" && path == "/api/v1/panel/create")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                HashSet<string> allowed = new HashSet<string>(StringComparer.Ordinal)
                    { "parent_refno", "name", "vertices", "thickness", "position", "x_axis", "y_axis", "wrt", "justification", "dry_run" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown panel creation parameter.");
                if (body["thickness"] == null)
                    throw new ArgumentException("thickness is required.");
                response = new { ok = true, version = "E3D21", result = context.CreatePanel(
                    ReadString(body, "parent_refno", ""), ReadString(body, "name", ""), ReadPanelVertices(body),
                    ReadFiniteNumber(body, "thickness", 0), ReadVector(body, "position"),
                    ReadVector(body, "x_axis"), ReadVector(body, "y_axis"), ReadString(body, "wrt", "OWNER"),
                    ReadString(body, "justification", "positive"), ReadBoolean(body, "dry_run", true)) };
            }
            else if (method == "POST" && path == "/api/v1/panel/update")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                HashSet<string> allowed = new HashSet<string>(StringComparer.Ordinal)
                    { "refno", "expected_revision", "vertices", "thickness", "justification", "allow_vertex_deletion", "dry_run" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown panel update parameter.");
                double? thickness = body["thickness"] == null ? (double?)null : ReadFiniteNumber(body, "thickness", 0);
                response = new { ok = true, version = "E3D21", result = context.UpdatePanel(
                    ReadString(body, "refno", ""), ReadString(body, "expected_revision", null), ReadPanelVertices(body),
                    thickness, ReadString(body, "justification", null),
                    ReadBoolean(body, "allow_vertex_deletion", false), ReadBoolean(body, "dry_run", true)) };
            }
            else if (method == "POST" && path == "/api/v1/element/transform")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                response = new { ok = true, version = "E3D21", result = context.SetTransform(
                    ReadString(body, "refno", ""), ReadVector(body, "position"), ReadVector(body, "x_axis"),
                    ReadVector(body, "y_axis"), ReadString(body, "wrt", "OWNER"), ReadBoolean(body, "dry_run", true)) };
            }
            else if (method == "POST" && path == "/api/v1/element/delete")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                string refno = (string)body["refno"] ?? "";
                bool dryRun = (bool?)body["dry_run"] ?? true;
                response = new { ok = true, version = "E3D21", result = context.DeleteElement(refno, dryRun) };
            }
            else if (method == "POST" && path == "/api/v1/element/attribute")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                string refno = (string)body["refno"] ?? "";
                string attribute = (string)body["attribute"] ?? "";
                string value = (string)body["value"] ?? "";
                bool dryRun = (bool?)body["dry_run"] ?? true;
                response = new { ok = true, version = "E3D21", result = context.SetAttribute(refno, attribute, value, dryRun) };
            }
            else if (method == "POST" && path == "/api/v1/element/attributes/set")
            {
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("Batch attribute writes accept JSON parameters only.");
                JObject body = await ReadUniqueBodyAsync(request.Request, 65536).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal)
                    { "refno", "values", "expected_revision", "dry_run" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown batch attribute-set parameter.");
                JObject valuesObject = body["values"] as JObject;
                if (valuesObject == null || valuesObject.Count < 1 || valuesObject.Count > 20 ||
                    valuesObject.Properties().Any(property => property.Value == null || property.Value.Type != JTokenType.String))
                    throw new ArgumentException("values must be an object containing between 1 and 20 string attributes.");
                var values = new Dictionary<string, string>(StringComparer.Ordinal);
                foreach (JProperty property in valuesObject.Properties())
                {
                    if (values.ContainsKey(property.Name))
                        throw new ArgumentException("Duplicate attribute property: " + property.Name);
                    values.Add(property.Name, (string)property.Value);
                }
                response = new { ok = true, version = "E3D21", result = context.SetAttributes(
                    ReadString(body, "refno", ""), values, ReadString(body, "expected_revision", null),
                    ReadBoolean(body, "dry_run", true)) };
            }
            else if (method == "POST" && path == "/api/v1/query")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                string expression = (string)body["expression"] ?? "";
                int limit = Clamp((int?)body["limit"] ?? 50, 1, 500);
                IList<object> elements = context.Query(expression, limit);
                response = new { ok = true, version = "E3D21", count = elements.Count, elements = elements };
            }
            else if (method == "POST" && path == "/api/v1/command")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                string expectedSession = (string)body["expected_session_id"];
                if (expectedSession != null && expectedSession != sessionId)
                {
                    await WriteJsonAsync(request.Response, new { ok = false,
                        error = "E3D session changed; command was not executed.", session_id = sessionId,
                        state = SafeCommandStateDiagnostics() }, 409).ConfigureAwait(false);
                    return;
                }
                string command = (string)body["command"] ?? "";
                bool dryRun = (bool?)body["dry_run"] ?? true;
                response = new { ok = true, version = "E3D21", dry_run = dryRun, result = context.ExecutePml(command, dryRun) };
            }
            else if (method == "POST" && path == "/api/v1/pml-script")
            {
                if (request.Request.QueryString.Count != 0)
                    throw new ArgumentException("PML scripts accept JSON parameters only.");
                JObject body = await ReadPmlScriptBodyAsync(request.Request).ConfigureAwait(false);
                var allowed = new HashSet<string>(StringComparer.Ordinal)
                    { "source", "dry_run", "expected_session_id", "request_id" };
                if (body.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown PML script parameter.");
                string source = ReadString(body, "source", null);
                bool dryRun = ReadBoolean(body, "dry_run", true);
                string expectedSession = ReadString(body, "expected_session_id", null);
                string requestId = ReadPmlRequestId(ReadString(body, "request_id", null), true);
                if (expectedSession != null && expectedSession != sessionId)
                {
                    await WriteJsonAsync(request.Response, new { ok = false,
                        error = "E3D session changed; script was not executed.", session_id = sessionId,
                        execution_attempted = false, outcome_uncertain = false,
                        state = SafeCommandStateDiagnostics() }, 409).ConfigureAwait(false);
                    return;
                }
                if (dryRun)
                {
                    // Preview never reserves an execution identifier.
                    response = new { ok = true, version = "E3D21", session_id = sessionId,
                        dry_run = true, request_id = (string)null, result = context.ExecutePmlScript(source, true) };
                }
                else
                {
                    PmlScriptInput input = PmlScriptInput.Prepare(source, Encoding.Default);
                    requestId = requestId ?? Guid.NewGuid().ToString("D");
                    try { scriptReceipts.Begin(requestId, input.SourceSha256, input.ByteCount); }
                    catch (PmlScriptReceiptStore.DuplicateRequestException)
                    {
                        await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21",
                            session_id = sessionId, request_id = requestId, duplicate_request = true,
                            this_submission_executed = false, request_replayed = false,
                            error = "This script request_id was already accepted in this session. Query its status; do not resubmit the script." }, 409).ConfigureAwait(false);
                        return;
                    }
                    catch (PmlScriptReceiptStore.CapacityException)
                    {
                        await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21",
                            session_id = sessionId, request_id = requestId, execution_attempted = false,
                            outcome_uncertain = false,
                            error = "Script receipt capacity is full; this request was not accepted or executed." }, 503).ConfigureAwait(false);
                        return;
                    }
                    try
                    {
                        response = new { ok = true, version = "E3D21", session_id = sessionId,
                            request_id = requestId, dry_run = false, result = context.ExecutePmlScript(source, false) };
                        // Persist the final managed receipt before touching the
                        // HTTP response: a disconnected caller can still query it.
                        scriptReceipts.Complete(requestId, response, 200);
                    }
                    catch (Exception error)
                    {
                        var failure = new { ok = false, version = "E3D21", session_id = sessionId,
                            request_id = requestId, dry_run = false, execution_attempted = (bool?)null,
                            outcome_uncertain = true, retry_safe = false, error = error.Message,
                            state = SafeCommandStateDiagnostics() };
                        scriptReceipts.Complete(requestId, failure, 500);
                        await WriteJsonAsync(request.Response, failure, 500).ConfigureAwait(false);
                        return;
                    }
                }
            }
            else if (method == "GET" && path == "/api/v1/pml-script/status")
            {
                try
                {
                    var query = request.Request.QueryString;
                    foreach (string key in query.AllKeys)
                    {
                        if (key != "request_id" && key != "expected_session_id")
                            throw new ArgumentException("Unknown PML script status parameter.");
                        if (query.GetValues(key).Length != 1)
                            throw new ArgumentException("Duplicate PML script status parameter.");
                    }
                    string requestId = ReadPmlRequestId(query["request_id"], false);
                    string expectedSession = query["expected_session_id"];
                    if (string.IsNullOrWhiteSpace(expectedSession))
                        throw new ArgumentException("expected_session_id is required for script status queries.");
                    if (expectedSession != sessionId)
                    {
                        await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21",
                            session_id = sessionId, request_id = requestId, session_changed = true,
                            error = "E3D session changed; script receipts from the previous session are unavailable." }, 409).ConfigureAwait(false);
                        return;
                    }
                    // Managed history only: remains available during a blocked UI
                    // operation and does not invoke native diagnostics or the SDK.
                    response = new { ok = true, version = "E3D21", session_id = sessionId,
                        result = scriptReceipts.Get(requestId) };
                }
                catch (Exception error)
                {
                    // Even malformed status queries must not wait for the UI
                    // diagnostics path while the original script is blocked.
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21",
                        session_id = sessionId, error = error.Message },
                        error is ArgumentException ? 400 : 500).ConfigureAwait(false);
                    return;
                }
            }
            else if (method == "POST" && path == "/api/v1/command-batch")
            {
                JObject body = await ReadBodyAsync(request.Request).ConfigureAwait(false);
                JArray values = body["commands"] as JArray;
                if (values == null)
                    throw new ArgumentException("commands must be an array");
                List<string> commands = values.ToObject<List<string>>();
                bool dryRun = (bool?)body["dry_run"] ?? true;
                bool stopOnError = (bool?)body["stop_on_error"] ?? true;
                response = new { ok = true, version = "E3D21", dry_run = dryRun, stop_on_error = stopOnError, results = context.ExecutePmlBatch(commands, dryRun, stopOnError) };
            }
            else
            {
                if (managedMessageWindowQuery)
                    throw new MessageCursorException("message_cursor_request_invalid", "Message windows require GET.", 405);
                if (paragonComponentTemplateQuery)
                {
                    request.Response.AddHeader("Allow", "POST");
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "PARAGON component templates require POST." }, 405).ConfigureAwait(false);
                    return;
                }
                if (paragonComponentParametersQuery || paragonComponentParametersStatusQuery)
                {
                    request.Response.AddHeader("Allow", paragonComponentParametersStatusQuery ? "GET" : "POST");
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = paragonComponentParametersStatusQuery ? "Component parameter status requires GET." : "Component parameter updates require POST." }, 405).ConfigureAwait(false);
                    return;
                }
                if (paragonComponentCopyQuery || paragonComponentCopyStatusQuery)
                {
                    request.Response.AddHeader("Allow", paragonComponentCopyStatusQuery ? "GET" : "POST");
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = paragonComponentCopyStatusQuery ? "Component generation status requires GET." : "Component generation requires POST." }, 405).ConfigureAwait(false);
                    return;
                }
                if (paragonReferenceTraceQuery)
                {
                    request.Response.AddHeader("Allow", "POST");
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "PARAGON reference traces require POST." }, 405).ConfigureAwait(false);
                    return;
                }
                if (teamDatabaseIdentityManifestQuery)
                {
                    request.Response.AddHeader("Allow", "POST");
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "Team/database identity manifests require POST." }, 405).ConfigureAwait(false);
                    return;
                }
                if (pipingSpecificationReadQuery)
                {
                    request.Response.AddHeader("Allow", "POST");
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "Piping specification reads require POST." }, 405).ConfigureAwait(false);
                    return;
                }
                if (equipmentChildSchemaQuery)
                {
                    request.Response.AddHeader("Allow", "POST");
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "Equipment child schema requires POST." }, 405).ConfigureAwait(false);
                    return;
                }
                if (paragonCatalogueManifestQuery)
                {
                    request.Response.AddHeader("Allow", "POST");
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        error = "PARAGON catalogue manifests require POST." }, 405).ConfigureAwait(false);
                    return;
                }
                if (moduleContextQuery)
                {
                    request.Response.AddHeader("Allow", "GET");
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "Module context requires GET." }, 405).ConfigureAwait(false);
                    return;
                }
                if (paragonCatalogueSchemaQuery)
                {
                    request.Response.AddHeader("Allow", "POST");
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "PARAGON Catalogue schema requires POST." }, 405).ConfigureAwait(false);
                    return;
                }
                if (connectivitySnapshotQuery)
                {
                    request.Response.AddHeader("Allow", "POST");
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                        error = "Piping connectivity snapshot requires POST." }, 405).ConfigureAwait(false);
                    return;
                }
                if (pipeManifestQuery)
                {
                    request.Response.AddHeader("Allow", "POST");
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        error = "Pipe member manifests require POST." }, 405).ConfigureAwait(false);
                    return;
                }
                if (equipmentManifestQuery || projectCatalogProbe)
                {
                    request.Response.AddHeader("Allow", "POST");
                    await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                        error = projectCatalogProbe ? "Project database catalog probes require POST." : "Equipment manifests require POST." }, 405).ConfigureAwait(false);
                    return;
                }
                await WriteJsonAsync(request.Response, new { ok = false, error = "Not found", state = SafeCommandStateDiagnostics() }, 404).ConfigureAwait(false);
                return;
            }
            await WriteJsonAsync(request.Response, response, 200).ConfigureAwait(false);
        }
        catch (NativeDatabaseFaultException ex) when (paragonComponentParametersQuery || paragonComponentParametersStatusQuery)
        {
            bool unknown = paragonComponentParametersDispatched && !paragonComponentParametersDryRun;
            await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error_code = "native_database_fault", native_fault_latched = true,
                write_attempted = unknown ? (bool?)null : false, outcome_uncertain = unknown, retry_safe = false,
                error = ParagonComponentParametersRequest.ErrorText(ex.Message) }, 500).ConfigureAwait(false);
        }
        catch (Exception ex) when (paragonComponentParametersQuery || paragonComponentParametersStatusQuery)
        {
            bool unknown = paragonComponentParametersDispatched && !paragonComponentParametersDryRun;
            await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                write_attempted = unknown ? (bool?)null : false, outcome_uncertain = unknown, retry_safe = false,
                error = ex is JsonReaderException ? "Request body is not valid JSON." : ParagonComponentParametersRequest.ErrorText(ex.Message) },
                !paragonComponentParametersDispatched && (ex is ArgumentException || ex is JsonException) ? 400 : 500).ConfigureAwait(false);
        }
        catch (NativeDatabaseFaultException ex) when (paragonComponentCopyQuery || paragonComponentCopyStatusQuery)
        {
            bool unknown = paragonComponentCopyDispatched && !paragonComponentCopyDryRun;
            await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error_code = "native_database_fault", native_fault_latched = true,
                write_attempted = unknown ? (bool?)null : false, outcome_uncertain = unknown, retry_safe = false,
                error = ParagonComponentCopyRequest.ErrorText(ex.Message) }, 500).ConfigureAwait(false);
        }
        catch (Exception ex) when (paragonComponentCopyQuery || paragonComponentCopyStatusQuery)
        {
            bool unknown = paragonComponentCopyDispatched && !paragonComponentCopyDryRun;
            await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                write_attempted = unknown ? (bool?)null : false, outcome_uncertain = unknown, retry_safe = false,
                error = ex is JsonReaderException ? "Request body is not valid JSON." : ParagonComponentCopyRequest.ErrorText(ex.Message) },
                !paragonComponentCopyDispatched && (ex is ArgumentException || ex is JsonException) ? 400 : 500).ConfigureAwait(false);
        }
        catch (NativeDatabaseFaultException ex) when (paragonReferenceTraceQuery)
        {
            await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error_code = "native_database_fault", native_fault_latched = true, retry_safe = false,
                error = ParagonReferenceTraceRequest.ErrorText(ex.Message) }, 500).ConfigureAwait(false);
        }
        catch (Exception ex) when (paragonReferenceTraceQuery)
        {
            await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error = ex is JsonReaderException ? "Request body is not valid JSON." : ParagonReferenceTraceRequest.ErrorText(ex.Message) },
                ex is ArgumentException || ex is JsonException ? 400 : 500).ConfigureAwait(false);
        }
        catch (NativeDatabaseFaultException ex) when (paragonComponentTemplateQuery)
        {
            await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error_code = "native_database_fault", native_fault_latched = true, retry_safe = false,
                error = ParagonComponentTemplateRequest.ErrorText(ex.Message) }, 500).ConfigureAwait(false);
        }
        catch (Exception ex) when (paragonComponentTemplateQuery)
        {
            await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error = ex is JsonReaderException ? "Request body is not valid JSON." : ParagonComponentTemplateRequest.ErrorText(ex.Message) },
                ex is ArgumentException || ex is JsonException ? 400 : 500).ConfigureAwait(false);
        }
        catch (NativeDatabaseFaultException ex) when (teamDatabaseIdentityManifestQuery)
        {
            await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error_code = "native_database_fault", native_fault_latched = true, retry_safe = false,
                error = TeamDatabaseIdentityManifestRequest.ErrorText(ex.Message) }, 500).ConfigureAwait(false);
        }
        catch (Exception ex) when (teamDatabaseIdentityManifestQuery)
        {
            await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error = ex is JsonReaderException ? "Request body is not valid JSON." : TeamDatabaseIdentityManifestRequest.ErrorText(ex.Message) },
                ex is ArgumentException || ex is JsonException ? 400 : 500).ConfigureAwait(false);
        }
        catch (NativeDatabaseFaultException ex) when (pipingSpecificationReadQuery)
        {
            await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error_code = "native_database_fault", native_fault_latched = true, retry_safe = false,
                error = ParagonCatalogueManifestRequest.ErrorText(ex.Message) }, 500).ConfigureAwait(false);
        }
        catch (Exception ex) when (pipingSpecificationReadQuery)
        {
            await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error = ex is JsonReaderException ? "Request body is not valid JSON." : ParagonCatalogueManifestRequest.ErrorText(ex.Message) },
                ex is ArgumentException || ex is JsonException ? 400 : 500).ConfigureAwait(false);
        }
        catch (NativeDatabaseFaultException ex) when (equipmentChildSchemaQuery)
        {
            await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error_code = "native_database_fault", native_fault_latched = true, retry_safe = false,
                error = EquipmentChildSchemaRequest.ErrorText(ex.Message) }, 500).ConfigureAwait(false);
        }
        catch (Exception ex) when (equipmentChildSchemaQuery)
        {
            await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error = ex is JsonReaderException ? "Request body is not valid JSON." : EquipmentChildSchemaRequest.ErrorText(ex.Message) },
                ex is ArgumentException || ex is JsonException ? 400 : 500).ConfigureAwait(false);
        }
        catch (NativeDatabaseFaultException ex) when (connectivitySnapshotQuery)
        {
            await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error_code = "native_database_fault", native_fault_latched = true, retry_safe = false,
                error = ConnectivitySnapshotErrors.Text(ex.Message) }, 500).ConfigureAwait(false);
        }
        catch (Exception ex) when (connectivitySnapshotQuery)
        {
            await WriteJsonAsync(request.Response, new { ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error = ex is JsonReaderException ? "Request body is not valid JSON." : ConnectivitySnapshotErrors.Text(ex.Message) },
                ex is ArgumentException || ex is JsonException ? 400 : 500).ConfigureAwait(false);
        }
        catch (NativeDatabaseFaultException ex) when (moduleContextQuery || paragonCatalogueSchemaQuery)
        {
            await WriteJsonAsync(request.Response, new
            {
                ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error_code = "native_database_fault", native_fault_latched = true, retry_safe = false,
                error = ParagonCatalogueManifestRequest.ErrorText(ex.Message)
            }, 500).ConfigureAwait(false);
        }
        catch (Exception ex) when (moduleContextQuery || paragonCatalogueSchemaQuery)
        {
            await WriteJsonAsync(request.Response, new
            {
                ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error = ex is JsonReaderException ? "Request body is not valid JSON." : ParagonCatalogueManifestRequest.ErrorText(ex.Message)
            }, ex is ArgumentException || ex is JsonException ? 400 : 500).ConfigureAwait(false);
        }
        catch (NativeDatabaseFaultException ex) when (paragonCatalogueManifestQuery)
        {
            await WriteJsonAsync(request.Response, new
            {
                ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error_code = "native_database_fault", native_fault_latched = true, retry_safe = false,
                error = ParagonCatalogueManifestRequest.ErrorText(ex.Message)
            }, 500).ConfigureAwait(false);
        }
        catch (Exception ex) when (paragonCatalogueManifestQuery)
        {
            await WriteJsonAsync(request.Response, new
            {
                ok = false, version = "E3D21", session_id = sessionId,
                bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                error = ex is JsonReaderException ? "Request body is not valid JSON." : ParagonCatalogueManifestRequest.ErrorText(ex.Message)
            }, ex is ArgumentException || ex is JsonException ? 400 : 500).ConfigureAwait(false);
        }
        catch (Exception ex) when (pipeManifestQuery)
        {
            await WriteJsonAsync(request.Response, new
            {
                ok = false, version = "E3D21", session_id = sessionId,
                error = ex is JsonReaderException ? "Request body is not valid JSON." : PipeMemberManifestRequest.ErrorText(ex.Message)
            }, ex is ArgumentException || ex is JsonException ? 400 : 500).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            if (managedMessageWindowQuery)
            {
                MessageCursorException failure = ex as MessageCursorException;
                await WriteJsonAsync(request.Response, new
                {
                    ok = false, version = "E3D21", session_id = sessionId,
                    bridge_build = typeof(E3d21HttpAdapter).Module.ModuleVersionId.ToString("D"),
                    error_code = failure == null ? "message_cursor_read_failed" : failure.Code,
                    error = failure == null ? "The managed message window could not be read." : failure.Message,
                    managed_only = true, operation_correlated = false
                }, failure == null ? 500 : failure.HttpStatus).ConfigureAwait(false);
                return;
            }
            // Preserve machine-readable execution/fault diagnostics without
            // returning stack traces, request bodies, credentials, or headers.
            int status = ex is ArgumentException || ex is JsonException ? 400 : 500;
            await WriteJsonAsync(request.Response, new
            {
                ok = false, version = "E3D21", session_id = sessionId,
                error = ex is JsonReaderException ? "Request body is not valid JSON." :
                    equipmentManifestQuery ? EquipmentManifestRequest.ErrorText(ex.Message) : ex.Message,
                state = managedScriptStatusQuery || equipmentManifestQuery || projectCatalogProbe ? null : SafeCommandStateDiagnostics()
            }, status).ConfigureAwait(false);
        }
        finally
        {
            request.Response.Close();
        }
    }

    private object SafeCommandStateDiagnostics()
    {
        try { return SanitizeDiagnostics(context.CommandState()); }
        catch { return new { diagnostic_error = "Command-state diagnostics are unavailable." }; }
    }

    private static JToken SanitizeDiagnostics(object value)
    {
        if (value == null) return JValue.CreateNull();
        JToken snapshot = value is JToken ? ((JToken)value).DeepClone() : JToken.FromObject(value);
        SanitizeDiagnosticFields(snapshot);
        return snapshot;
    }

    private static void SanitizeDiagnosticFields(JToken token)
    {
        JObject fields = token as JObject;
        if (fields != null)
        {
            foreach (JProperty field in fields.Properties().ToArray())
            {
                string key = field.Name.Replace("_", "").Replace("-", "").ToLowerInvariant();
                if (key == "password" || key == "passwd" || key == "pwd" || key == "secret" ||
                    key == "credential" || key == "credentials" || key == "authorization" ||
                    key == "accesstoken" || key == "refreshtoken" || key == "apikey")
                    field.Value = "[redacted]";
                else if (key == "command" && field.Value.Type == JTokenType.String)
                {
                    // PML may contain login arguments. Health/fault state needs
                    // only to show whether an operation exists, never its text.
                    field.Value = string.IsNullOrEmpty((string)field.Value) ? "" : "[active command]";
                }
                else SanitizeDiagnosticFields(field.Value);
            }
        }
        else
        {
            JArray items = token as JArray;
            if (items != null) foreach (JToken item in items) SanitizeDiagnosticFields(item);
        }
    }

    private static async Task<JObject> ReadBodyAsync(HttpListenerRequest request)
    {
        // All ordinary JSON routes use the same bounded, duplicate-aware
        // reader.  Keep the larger PML-script transport on its dedicated
        // parser below because escaped source can legitimately require more
        // than this generic request budget.
        return await ReadUniqueBodyAsync(request).ConfigureAwait(false);
    }

    private static async Task<JObject> ReadUniqueBodyAsync(HttpListenerRequest request,
        int maximumBytes = MaximumJsonRequestBodyBytes)
    {
        using (var buffer = new MemoryStream())
        {
            var block = new byte[8192];
            int count;
            while ((count = await request.InputStream.ReadAsync(block, 0,
                Math.Min(block.Length, maximumBytes - (int)buffer.Length + 1)).ConfigureAwait(false)) != 0)
            {
                if (buffer.Length + count > maximumBytes)
                    throw new ArgumentException("JSON request body exceeds the " + maximumBytes.ToString() + " byte limit.");
                buffer.Write(block, 0, count);
            }
            buffer.Position = 0;
            using (StreamReader reader = new StreamReader(buffer, request.ContentEncoding ?? Encoding.UTF8))
            {
                string text = await reader.ReadToEndAsync().ConfigureAwait(false);
                var objectProperties = new Stack<HashSet<string>>();
                using (var json = new JsonTextReader(new StringReader(text)))
                    while (json.Read())
                    {
                        if (json.TokenType == JsonToken.StartObject)
                            objectProperties.Push(new HashSet<string>(StringComparer.Ordinal));
                        else if (json.TokenType == JsonToken.EndObject && objectProperties.Count > 0)
                            objectProperties.Pop();
                        else if (json.TokenType == JsonToken.PropertyName && objectProperties.Count > 0 &&
                                 !objectProperties.Peek().Add((string)json.Value))
                            throw new ArgumentException("Duplicate JSON parameter: " + json.Value);
                    }
                return JObject.Parse(text);
            }
        }
    }

    private static async Task<JObject> ReadPmlScriptBodyAsync(HttpListenerRequest request)
    {
        // An escaped 256 KiB source can need six bytes per character in JSON.
        // Bound the transport separately, before parsing or reaching the SDK.
        using (var buffer = new MemoryStream())
        {
            var block = new byte[8192];
            int count;
            while ((count = await request.InputStream.ReadAsync(block, 0,
                Math.Min(block.Length, PmlScriptJson.MaximumBodyBytes - (int)buffer.Length + 1)).ConfigureAwait(false)) != 0)
            {
                if (buffer.Length + count > PmlScriptJson.MaximumBodyBytes)
                    throw new ArgumentException("PML script JSON body exceeds the 2 MiB transport limit.");
                buffer.Write(block, 0, count);
            }
            return PmlScriptJson.Parse(buffer.ToArray());
        }
    }

    private static async Task WriteJsonAsync(HttpListenerResponse response, object value, int statusCode)
    {
        byte[] bytes = Encoding.UTF8.GetBytes(JsonConvert.SerializeObject(value));
        response.StatusCode = statusCode;
        response.ContentType = "application/json; charset=utf-8";
        response.ContentLength64 = bytes.Length;
        await response.OutputStream.WriteAsync(bytes, 0, bytes.Length).ConfigureAwait(false);
    }

    private static string ReadString(JObject body, string name, string fallback)
    {
        JToken value = body[name];
        if (value == null) return fallback;
        if (value.Type != JTokenType.String) throw new ArgumentException(name + " must be a string");
        return (string)value;
    }

    private static string ReadPmlRequestId(string value, bool allowMissing)
    {
        if (value == null && allowMissing) return null;
        Guid id;
        if (value == null || value.Length != 36 || !Guid.TryParseExact(value, "D", out id))
            throw new ArgumentException("request_id must be a UUID in 8-4-4-4-12 format.");
        return id.ToString("D");
    }

    private static bool ReadBoolean(JObject body, string name, bool fallback)
    {
        JToken value = body[name];
        if (value == null) return fallback;
        if (value.Type != JTokenType.Boolean) throw new ArgumentException(name + " must be a boolean");
        return (bool)value;
    }

    private static List<double> ReadParagonDoubleParameters(JObject body)
    {
        JArray array = body["parameters"] as JArray;
        if (array == null || array.Count < 1 || array.Count > ParagonComponentParametersRequest.MaximumParameters
            || array.Any(value => value.Type != JTokenType.Integer && value.Type != JTokenType.Float))
            throw new ArgumentException("parameters must be an array of 1 to 128 finite DOUBLE values.");
        try
        {
            var values = array.Select(value => value.Value<double>()).ToList();
            if (values.Any(value => double.IsNaN(value) || double.IsInfinity(value)))
                throw new ArgumentException("parameters must contain only finite DOUBLE values.");
            return values;
        }
        catch (Exception error) when (error is OverflowException || error is FormatException || error is InvalidCastException)
        {
            throw new ArgumentException("parameters must contain representable finite DOUBLE values.", error);
        }
    }

    private static int ReadInteger(JObject body, string name, int fallback)
    {
        JToken value = body[name];
        if (value == null) return fallback;
        if (value.Type != JTokenType.Integer) throw new ArgumentException(name + " must be an integer");
        long number;
        if (!long.TryParse(value.ToString(), out number) || number < int.MinValue || number > int.MaxValue)
            throw new ArgumentException(name + " is outside the supported integer range");
        return (int)number;
    }

    private static bool? ReadNullableBoolean(JObject body, string name)
    {
        JToken value = body[name];
        if (value == null || value.Type == JTokenType.Null) return null;
        if (value.Type != JTokenType.Boolean) throw new ArgumentException(name + " must be a boolean or null");
        return (bool)value;
    }

    private static double[] ReadVector(JObject body, string name)
    {
        JToken value = body[name];
        if (value == null || value.Type == JTokenType.Null) return null;
        JArray array = value as JArray;
        if (array == null || array.Count != 3 || array.Any(v => v.Type != JTokenType.Integer && v.Type != JTokenType.Float))
            throw new ArgumentException(name + " must contain three numbers");
        double[] vector = array.ToObject<double[]>();
        if (vector.Any(v => double.IsNaN(v) || double.IsInfinity(v)))
            throw new ArgumentException(name + " must contain finite numbers");
        return vector;
    }

    private static double ReadFiniteNumber(JObject body, string name, double fallback)
    {
        JToken value = body[name];
        if (value == null) return fallback;
        if (value.Type != JTokenType.Integer && value.Type != JTokenType.Float)
            throw new ArgumentException(name + " must be a number");
        double number = (double)value;
        if (double.IsNaN(number) || double.IsInfinity(number))
            throw new ArgumentException(name + " must be finite");
        return number;
    }

    private static List<double[]> ReadCoordinateVectors(JToken token, string name)
    {
        if (token == null) return new List<double[]>();
        JArray rows = token as JArray;
        if (rows == null || rows.Count > 128) throw new ArgumentException(name + " must be an array of at most 128 three-number arrays.");
        var result = new List<double[]>();
        foreach (JToken rowToken in rows)
        {
            JArray row = rowToken as JArray;
            if (row == null || row.Count != 3 || row.Any(value => value.Type != JTokenType.Integer && value.Type != JTokenType.Float))
                throw new ArgumentException(name + " entries must contain exactly three finite numbers.");
            double[] vector = row.ToObject<double[]>();
            if (vector.Any(value => double.IsNaN(value) || double.IsInfinity(value)))
                throw new ArgumentException(name + " entries must contain exactly three finite numbers.");
            result.Add(vector);
        }
        return result;
    }

    private static double[][] ReadPanelVertices(JObject body)
    {
        JArray array = body["vertices"] as JArray;
        if (array == null || array.Count < 3 || array.Count > 256)
            throw new ArgumentException("vertices must contain 3 to 256 local XY pairs.");
        var vertices = new List<double[]>();
        foreach (JToken token in array)
        {
            JArray pair = token as JArray;
            if (pair == null || pair.Count != 2 || pair.Any(value => value.Type != JTokenType.Float && value.Type != JTokenType.Integer))
                throw new ArgumentException("Each vertex must contain exactly two numbers.");
            double[] point = pair.ToObject<double[]>();
            if (point.Any(value => double.IsNaN(value) || double.IsInfinity(value)))
                throw new ArgumentException("Panel vertices must be finite.");
            vertices.Add(point);
        }
        return vertices.ToArray();
    }

    private static int ParseInt(string value, int fallback)
    {
        int parsed;
        return int.TryParse(value, out parsed) ? parsed : fallback;
    }

    private static int Clamp(int value, int minimum, int maximum)
    {
        return Math.Max(minimum, Math.Min(maximum, value));
    }
}

public interface IE3d21Context
{
    object FindDescendants(string rootRefno, string elementType, string nameContains, int? maxDepth, int limit, string cursor, string sessionId);
    bool Connected { get; }
    object GetProject();
    object GetDatabaseState(int offset, int limit, string expectedContext, string sessionId);
    object GetDatabaseIdentitySnapshot(string expectedContext, string sessionId);
    object GetProjectCatalogProbe(string team, string name, int? databaseNumber, string expectedContext, string sessionId);
    object GetSpatialMapStatus(int databaseNumber, int extractNumber, string expectedContext, string sessionId);
    IList<object> Query(string expression, int limit);
    IList<string> ReadLogs(int tail);
    IList<object> ReadMessages(int tail);
    object CommandState();
    object CancelCommand();
    object GetDrawListSnapshot();
    object GetDisplayState(string refno);
    object GetDisplaySnapshot();
    object RemoveDrawListMember(string refno, string expectedRevision, bool dryRun);
    object SetPrimitiveVisual(string refno, string expectedRevision, string colour, int? transparency,
        bool? edges, bool? visibility, bool? shallow, bool dryRun);
    object FitView(bool shaded, bool isometric, bool autoAddCurrent);
    object CaptureView(int width, int height);
    object GetViewState();
    object GetViewClippingState();
    object GetGraphicalSelection(int limit);
    object SetGraphicalSelection(IList<string> refnos, string expectedRevision, bool dryRun);
    object GetMeasurementsBatch(IList<string> refnos, IList<string> measurements, string wrt, string volumeScope);
    object TraceConnectionNetwork(IList<string> refnos, IList<string> expectedTypes, IList<int> pointNumbers, int maxHops, int maxNodes);
    object TransformCoordinates(string fromWrt, string toWrt, IList<double[]> points, IList<double[]> vectors);
    object GetElementAccess(IList<string> refnos);
    object SetView(string preset, string fit, string targetRefno, double padding, bool dryRun);
    object SetCamera(double[] centre, double[] direction, double radius, bool perspective,
        double extraTwistDegrees, string expectedRevision, bool dryRun);
    object RestoreDrawList(bool fitView);
    object SetDrawListVisual(string refno, string colour, int? transparency, bool? edges, bool? visibility, bool? shallow, bool add, bool dryRun);
    object GetElementSnapshot(string refno, string expectedType, int depth, int limit);
    object ListMembers(string refno, string expectedType, string cursor, int limit, string sessionId);
    object ReadAttributes(string refno, IList<string> attributes, string expectedType = "");
    object ReadAttributesBatch(IList<string> refnos, IList<string> attributes);
    object GetElementSchema(string refno, IList<string> attributes, int offset, int limit,
        bool includeConstraints = false, string expectedType = "");
    object GetSpatialInfo(string refno, string wrt);
    object GetConnectionPoints(string refno, string wrt, IList<int> pointNumbers, string expectedType = null);
    object CheckConnectionPair(string firstRefno, int firstPointNumber,
        string secondRefno, int secondPointNumber, double toleranceMm,
        string firstExpectedType = null, string secondExpectedType = null);
    object GetGeometryBounds(string refno);
    object CheckGeometryRelation(string testRefno, string againstRefno, bool includeBox);
    object CheckGeometryRelationBatch(IList<string[]> pairs, bool includeBox);
    object GetGeometryVolume(string refno, string scope);
    object GetPrimitiveGeometry(string refno);
    object GetGeneratedCsg(string refno, int maxTrees, int maxItems);
    object UpdatePrimitive(string refno, string expectedRevision, IDictionary<string, double> dimensions,
        double[] position, double[] xAxis, double[] yAxis, string wrt, bool dryRun);
    object GetPanelGeometry(string refno, bool includeWorld = false, bool includeBoundary = false);
    object CreatePanel(string parentRef, string name, double[][] vertices, double thickness,
        double[] position, double[] xAxis, double[] yAxis, string wrt, string justification, bool dryRun);
    object UpdatePanel(string refno, string expectedRevision, double[][] vertices, double? thickness,
        string justification, bool allowVertexDeletion, bool dryRun);
    object ValidateModel(string refno, int depth, int limit, IList<string> requiredTypes, bool includeSnapshot = true);
    object DeleteElement(string refno, bool dryRun);
    object SetAttribute(string refno, string attributeName, string value, bool dryRun);
    object SetAttributes(string refno, IDictionary<string, string> values, string expectedRevision, bool dryRun);
    object CopyElement(string sourceRef, string parentRef, string name, double[] position,
        double[] xAxis, double[] yAxis, string wrt, string expectedRevision, bool dryRun);
    object CreateElement(string parentRef, string typeName, string name, IDictionary<string,string> attributes,
        double[] position, double[] xAxis, double[] yAxis, string wrt, string expectedRevision, bool dryRun);
    object SetTransform(string refno, double[] position, double[] xAxis, double[] yAxis, string wrt, bool dryRun);
    object ExecutePml(string command, bool dryRun);
    object ExecutePmlScript(string source, bool dryRun);
    object CheckNativeClashes(IList<string> firstRefnos, IList<string> secondRefnos, double clearance, int resultLimit, bool dryRun);
    IList<object> ExecutePmlBatch(IList<string> commands, bool dryRun, bool stopOnError);
}
