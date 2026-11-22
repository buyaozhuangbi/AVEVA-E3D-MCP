using System.Net;
using System.Text;
using System.Text.Json;

namespace E3dAiHttpAdapter;

public sealed class E3dHttpAdapter : IDisposable
{
    private const int MaximumJsonBodyBytes = 1024 * 1024;
    private readonly HttpListener _listener = new();
    private readonly IE3dContext _context;
    private readonly CancellationTokenSource _stop = new();
    private readonly JsonSerializerOptions _json = new(JsonSerializerDefaults.Web);
    private Task? _loop;

    public E3dHttpAdapter(IE3dContext context, int port = 8765)
    {
        _context = context ?? throw new ArgumentNullException(nameof(context));
        if (port is < 1 or > 65535)
            throw new ArgumentOutOfRangeException(nameof(port));
        _listener.Prefixes.Add($"http://127.0.0.1:{port}/");
    }

    public void Start()
    {
        if (_loop is not null)
            throw new InvalidOperationException("The E3D HTTP adapter is already running.");
        _listener.Start();
        _loop = Task.Run(ListenAsync);
    }

    public async Task StopAsync()
    {
        _stop.Cancel();
        _listener.Stop();
        if (_loop is not null)
            await _loop.ConfigureAwait(false);
        _loop = null;
    }

    public void Dispose()
    {
        StopAsync().GetAwaiter().GetResult();
        _stop.Dispose();
        _listener.Close();
    }

    private async Task ListenAsync()
    {
        while (!_stop.IsCancellationRequested)
        {
            HttpListenerContext request;
            try
            {
                request = await _listener.GetContextAsync().ConfigureAwait(false);
            }
            catch (HttpListenerException) when (_stop.IsCancellationRequested)
            {
                break;
            }
            catch (ObjectDisposedException) when (_stop.IsCancellationRequested)
            {
                break;
            }

            _ = Task.Run(() => HandleAsync(request), _stop.Token);
        }
    }

    private async Task HandleAsync(HttpListenerContext request)
    {
        try
        {
            var path = request.Request.Url?.AbsolutePath.TrimEnd('/') ?? string.Empty;
            var method = request.Request.HttpMethod.ToUpperInvariant();
            object response;
            if (method == "GET" && path == "/health")
            {
                response = new { ok = true, connected = _context.Connected, product = _context.Product, version = _context.Version };
            }
            else if (method == "GET" && path == "/api/v1/project")
            {
                response = new { ok = true, project = _context.GetProject() };
            }
            else if (method == "GET" && path == "/api/v1/logs")
            {
                var tail = ParseInt(request.Request.QueryString["tail"], 100, 1, 500);
                response = new { ok = true, lines = _context.ReadLogs(tail) };
            }
            else if (method == "POST" && path == "/api/v1/query")
            {
                var body = await ReadJsonAsync<QueryRequest>(request.Request).ConfigureAwait(false);
                var limit = Math.Clamp(body.Limit, 1, 500);
                var elements = _context.Query(body.Expression, limit);
                response = new { ok = true, elements, count = elements.Count };
            }
            else if (method == "POST" && path == "/api/v1/command")
            {
                var body = await ReadJsonAsync<CommandRequest>(request.Request).ConfigureAwait(false);
                response = new { ok = true, dry_run = body.DryRun, result = _context.ExecutePml(body.Command, body.DryRun) };
            }
            else
            {
                await WriteJsonAsync(request.Response, new { ok = false, error = "Not found" }, 404).ConfigureAwait(false);
                return;
            }
            await WriteJsonAsync(request.Response, response).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            int status = ex is ArgumentException || ex is InvalidDataException || ex is System.Text.Json.JsonException
                ? 400 : 500;
            await WriteJsonAsync(request.Response, new { ok = false, error = ex.Message }, status).ConfigureAwait(false);
        }
        finally
        {
            request.Response.Close();
        }
    }

    private async Task<T> ReadJsonAsync<T>(HttpListenerRequest request)
    {
        var bytes = await ReadBoundedBodyAsync(request.InputStream).ConfigureAwait(false);
        if (bytes.Length == 0)
            throw new InvalidDataException("Request body is empty.");
        var text = DecodeJsonBody(bytes, request.ContentEncoding ?? Encoding.UTF8);
        if (text.Length == 0)
            throw new InvalidDataException("Request body is empty.");

        // System.Text.Json otherwise accepts duplicate properties (the last
        // value wins).  Validate decoded property names at every object level
        // before deserializing so all routes have deterministic semantics.
        ValidateUniqueJsonProperties(Encoding.UTF8.GetBytes(text));
        return JsonSerializer.Deserialize<T>(text, _json) ?? throw new InvalidDataException("Request body is empty.");
    }

    internal static async Task<byte[]> ReadBoundedBodyAsync(Stream inputStream)
    {
        using var buffer = new MemoryStream();
        var block = new byte[8192];
        int count;
        while ((count = await inputStream.ReadAsync(block, 0,
            Math.Min(block.Length, MaximumJsonBodyBytes - (int)buffer.Length + 1)).ConfigureAwait(false)) != 0)
        {
            if (buffer.Length + count > MaximumJsonBodyBytes)
                throw new InvalidDataException("JSON request body exceeds the 1 MiB limit.");
            buffer.Write(block, 0, count);
        }
        return buffer.ToArray();
    }

    internal static string DecodeJsonBody(byte[] bytes, Encoding encoding)
    {
        using var buffer = new MemoryStream(bytes, writable: false);
        using var reader = new StreamReader(buffer, encoding);
        return reader.ReadToEnd();
    }

    internal static void ValidateUniqueJsonProperties(byte[] bytes)
    {
        var reader = new Utf8JsonReader(bytes, isFinalBlock: true, state: default);
        var objects = new Stack<HashSet<string>>();
        while (reader.Read())
        {
            if (reader.TokenType == JsonTokenType.StartObject)
                objects.Push(new HashSet<string>(StringComparer.Ordinal));
            else if (reader.TokenType == JsonTokenType.EndObject && objects.Count > 0)
                objects.Pop();
            else if (reader.TokenType == JsonTokenType.PropertyName && objects.Count > 0 &&
                     !objects.Peek().Add(reader.GetString() ?? string.Empty))
                throw new InvalidDataException("Duplicate JSON parameter.");
        }
    }

    private async Task WriteJsonAsync(HttpListenerResponse response, object value, int statusCode = 200)
    {
        var bytes = JsonSerializer.SerializeToUtf8Bytes(value, _json);
        response.StatusCode = statusCode;
        response.ContentType = "application/json; charset=utf-8";
        response.ContentLength64 = bytes.Length;
        await response.OutputStream.WriteAsync(bytes).ConfigureAwait(false);
    }

    private static int ParseInt(string? value, int fallback, int minimum, int maximum)
    {
        return int.TryParse(value, out var parsed) ? Math.Clamp(parsed, minimum, maximum) : fallback;
    }

    private sealed record QueryRequest(string Expression, int Limit = 50);
    private sealed record CommandRequest(string Command, bool DryRun = true);
}

public interface IE3dContext
{
    bool Connected { get; }
    string Product { get; }
    string Version { get; }
    object GetProject();
    IReadOnlyList<object> Query(string expression, int limit);
    IReadOnlyList<string> ReadLogs(int tail);
    object ExecutePml(string command, bool dryRun);
}
