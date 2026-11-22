using System;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using E3dAiHttpAdapter;

internal sealed class ChunkTrackingStream : MemoryStream
{
    internal long BytesRead { get; private set; }
    internal ChunkTrackingStream(byte[] bytes) : base(bytes) { }
    public override Task<int> ReadAsync(byte[] buffer, int offset, int count, System.Threading.CancellationToken cancellationToken)
    {
        int read = base.Read(buffer, offset, count);
        BytesRead += read;
        return Task.FromResult(read);
    }
}

internal static class LegacyJsonRequestBodyHarness
{
    private static int checks;
    private static int failures;

    private static void Check(bool value, string label)
    {
        checks++;
        if (value) return;
        failures++;
        Console.Error.WriteLine("FAIL: " + label);
    }

    private static byte[] Utf8(string value) { return Encoding.UTF8.GetBytes(value); }

    private static async Task Main()
    {
        byte[] valid = Utf8("{\"outer\":{\"v\":1},\"items\":[{\"v\":2}]}");
        byte[] read = await E3dHttpAdapter.ReadBoundedBodyAsync(new MemoryStream(valid));
        E3dHttpAdapter.ValidateUniqueJsonProperties(read);
        Check(read.Length == valid.Length, "ordinary nested JSON body is preserved");
        string utf16 = E3dHttpAdapter.DecodeJsonBody(Encoding.Unicode.GetBytes("{\"expression\":\"查询\"}"), Encoding.Unicode);
        Check(utf16 == "{\"expression\":\"查询\"}", "request ContentEncoding remains supported");
        string utf8Bom = E3dHttpAdapter.DecodeJsonBody(new byte[] { 0xEF, 0xBB, 0xBF }.Concat(valid).ToArray(), Encoding.UTF8);
        Check(utf8Bom == Encoding.UTF8.GetString(valid), "StreamReader BOM detection remains supported");

        foreach (string duplicate in new[] {
            "{\"value\":1,\"value\":2}",
            "{\"outer\":{\"value\":1,\"value\":2}}",
            "{\"va\\u006cue\":1,\"value\":2}"
        })
        {
            bool rejected = false;
            try { E3dHttpAdapter.ValidateUniqueJsonProperties(Utf8(duplicate)); }
            catch (InvalidDataException) { rejected = true; }
            Check(rejected, "duplicate decoded property is rejected: " + duplicate);
        }

        var exactStream = new ChunkTrackingStream(new byte[1024 * 1024]);
        byte[] exact = await E3dHttpAdapter.ReadBoundedBodyAsync(exactStream);
        Check(exact.Length == 1024 * 1024, "exact one MiB request body is accepted");

        var oversizedStream = new ChunkTrackingStream(new byte[2 * 1024 * 1024]);
        bool sizeRejected = false;
        try { await E3dHttpAdapter.ReadBoundedBodyAsync(oversizedStream); }
        catch (InvalidDataException) { sizeRejected = true; }
        Check(sizeRejected, "body above one MiB is rejected");
        Check(oversizedStream.BytesRead == 1024 * 1024 + 1,
            "oversized request stops after one byte over the cap");

        Console.WriteLine("Legacy JSON request-body checks: " + checks + "; failures: " + failures);
        if (failures != 0) Environment.ExitCode = 1;
    }
}
