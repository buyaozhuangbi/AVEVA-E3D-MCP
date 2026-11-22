using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;

// Bitmap and SDK calls are recording substitutes. Prepared PNG bytes are real
// PNG files, but rendering and compression are intentionally outside this test.
internal sealed class Bitmap : IDisposable
{
    internal int Width { get { return Fixture.Width; } }
    internal int Height { get { return Fixture.Height; } }
    internal void Save(MemoryStream stream, object format)
    {
        Fixture.Calls.Add("bitmap.save");
        if (Fixture.SaveThrows) throw new InvalidOperationException("injected PNG save failure");
        Fixture.Fill(stream, Fixture.Payload, Fixture.Shape);
    }
    public void Dispose() { Fixture.Calls.Add("bitmap.dispose"); }
}
internal static class ImageFormat { internal static readonly object Png = new object(); }
internal sealed class View3D
{
    internal bool IsValid() { Fixture.Calls.Add("view.valid"); return true; }
    internal Bitmap GetBitmap(int width, int height)
    { Fixture.Calls.Add("view.bitmap:" + width + "x" + height); return new Bitmap(); }
}
internal sealed class DrawListManager
{
    internal static readonly DrawListManager Instance = new DrawListManager();
    internal void UpdateGraphics() { Fixture.Calls.Add("graphics.update"); }
}
internal static class Fixture
{
    internal static byte[] Payload;
    internal static int Width = 64, Height = 64;
    internal static string Shape = "plain";
    internal static bool SaveThrows;
    internal static readonly View3D View = new View3D();
    internal static readonly List<string> Calls = new List<string>();
    internal static void Fill(MemoryStream stream, byte[] bytes, string shape)
    {
        if (shape == "capacity") stream.Capacity = checked(bytes.Length + 8192);
        stream.Write(bytes, 0, bytes.Length);
        if (shape == "tail" || shape == "tail_position")
        {
            byte[] poison = Enumerable.Repeat((byte)0xDA, 8192).ToArray();
            stream.Write(poison, 0, poison.Length);
            stream.SetLength(bytes.Length);
        }
        if (shape == "position" || shape == "tail_position") stream.Position = bytes.Length == 0 ? 0 : bytes.Length / 2 + 1;
        if (shape == "beyond") stream.Position = bytes.Length + 100;
    }
}
internal static class Production
{
    private static bool IsCommandActive() { return false; }
    private static T OnUi<T>(Func<T> action) { return action(); }
    private static T NativeRead<T>(Func<T> action) { return action(); }
    private static void NativeWrite(Action action) { action(); }
    private static void ThrowIfNativeFaulted() { }
    private static View3D CameraRequireView() { Fixture.Calls.Add("view.require"); return Fixture.View; }
    private static void InvokeViewMethod(View3D view, string name) { Fixture.Calls.Add("view.invoke:" + name); }
    private static Dictionary<string, object> CaptureReadState(View3D view, string phase, Dictionary<string, string> errors)
    { Fixture.Calls.Add("state:" + phase); return new Dictionary<string, object> { ["view_id"] = 1, ["drawlist_id"] = 2 }; }
    private static bool? CaptureViewIsCurrent(View3D view, string phase, Dictionary<string, string> errors)
    { Fixture.Calls.Add("current:" + phase); return true; }
    private static object CaptureObservation(Dictionary<string, object> a, Dictionary<string, object> b,
        Dictionary<string, object> c, Dictionary<string, string> errors, bool? sameA, bool? sameB, bool? sameC,
        string started, string finished)
    {
        // Wall-clock timestamps are not relevant to buffer byte equivalence.
        return new { before = a, bitmap = b, after = c, errors = errors, sameA = sameA, sameB = sameB, sameC = sameC,
            has_started = started != null, has_finished = finished != null };
    }
    internal static string Hash(Action<BinaryWriter> write) { return PrimitiveHash(write); }
    // PRODUCTION_METHODS
}
internal static class BufferCopyHarness
{
    private static int checks;
    private static long sink;
    private static readonly Func<long> allocated = AllocationCounter();
    private static Func<long> AllocationCounter()
    {
        MethodInfo method = typeof(GC).GetMethod("GetAllocatedBytesForCurrentThread", BindingFlags.Public | BindingFlags.Static);
        return method == null ? null : (Func<long>)Delegate.CreateDelegate(typeof(Func<long>), method);
    }
    private static string Digest(byte[] bytes)
    { using (SHA256 sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(); }
    private static void Check(bool condition, string message) { checks++; if (!condition) throw new Exception(message); }
    private static Dictionary<string, object> Capture(byte[] bytes, string shape, int width = 64, int height = 64)
    {
        Fixture.Payload = bytes; Fixture.Shape = shape; Fixture.Width = width; Fixture.Height = height; Fixture.Calls.Clear();
        return (Dictionary<string, object>)Production.CaptureView(width, height);
    }
    private static object Contracts(string fixtures)
    {
        var cases = new List<object>();
        var random = new Random(417231);
        var lengths = new List<int> { 0, 1, 2, 3, 255, 256, 257, 1023, 1024, 1025, 4095, 4096, 4097, 65535, 65536, 65537, 1447035 };
        for (int index = 0; index < 12; index++) lengths.Add(random.Next(2, 100000));
        foreach (int length in lengths)
        {
            byte[] payload = new byte[length]; random.NextBytes(payload);
            string expectedHash = Digest(payload), expectedBase64 = Convert.ToBase64String(payload);
            foreach (string shape in new[] { "plain", "capacity", "position", "beyond", "tail", "tail_position" })
            {
                string hash = Production.Hash(writer => Fixture.Fill((MemoryStream)writer.BaseStream, payload, shape));
                Check(hash == expectedHash, "actual production hash exact bytes: " + length + " " + shape);
                var result = Capture(payload, shape);
                string base64 = (string)result["image_base64"];
                Check(base64 == expectedBase64 && Convert.FromBase64String(base64).SequenceEqual(payload),
                    "actual CaptureView exact Base64 and decoded bytes: " + length + " " + shape);
                Check(Fixture.Calls.Count(call => call == "bitmap.save") == 1 && Fixture.Calls.Last() == "bitmap.dispose", "one save and disposal");
                cases.Add(new { kind = "byte-boundary", length = length, shape = shape, hash = hash,
                    base64_sha256 = Digest(Encoding.ASCII.GetBytes(base64)), base64_length = base64.Length, trace = Fixture.Calls.ToArray() });
            }
        }
        foreach (var size in new[] { new[] { 64, 64 }, new[] { 1400, 1050 }, new[] { 2048, 2048 } })
        {
            byte[] payload = File.ReadAllBytes(Path.Combine(fixtures, "png-" + size[0] + ".png"));
            Check(payload.Take(8).SequenceEqual(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 }), "real fixture PNG signature");
            foreach (string shape in new[] { "plain", "capacity", "tail_position" })
            {
                var result = Capture(payload, shape, size[0], size[1]);
                string base64 = (string)result["image_base64"];
                Check(base64 == Convert.ToBase64String(payload) && Convert.FromBase64String(base64).SequenceEqual(payload), "real PNG exact content");
                Check((int)result["width"] == size[0] && (int)result["height"] == size[1] && (string)result["mime_type"] == "image/png", "PNG metadata unchanged");
                cases.Add(new { kind = "png", width = size[0], height = size[1], length = payload.Length, shape = shape,
                    hash = Digest(payload), base64_sha256 = Digest(Encoding.ASCII.GetBytes(base64)), base64_length = base64.Length,
                    trace = Fixture.Calls.ToArray() });
            }
        }
        Fixture.SaveThrows = true;
        try { Capture(new byte[1], "plain"); throw new Exception("expected save failure"); }
        catch (InvalidOperationException error)
        {
            Check(error.Message == "The E3D view bitmap could not be encoded as PNG: injected PNG save failure" &&
                error.InnerException.Message == "injected PNG save failure" && Fixture.Calls.Last() == "bitmap.dispose", "PNG save exception and disposal retained");
        }
        finally { Fixture.SaveThrows = false; }
        Fixture.Calls.Clear();
        try { Production.CaptureView(4096, 64); throw new Exception("expected rejected width"); }
        catch (ArgumentOutOfRangeException) { Check(Fixture.Calls.Count == 0, "4096 is only a byte boundary, not an accepted image width"); }
        bool hashThrows = false;
        try { Production.Hash(writer => { writer.Write(123); throw new InvalidOperationException("writer failed"); }); }
        catch (InvalidOperationException error) { hashThrows = error.Message == "writer failed"; }
        Check(hashThrows, "writer exception unchanged");
        return new { passed = true, checks = checks, cases = cases, full_base64_equality_to_oracle = true,
            runtime = Environment.Version.ToString(), is_64_bit = Environment.Is64BitProcess, intptr_size = IntPtr.Size,
            scope = "actual PrimitiveHash and CaptureView method bodies; fake SDK/Bitmap.Save writes prepared bytes; real PNG fixtures; no rendering or compression measured" };
    }
    private static Dictionary<string, Action> Benchmarks(string fixtures)
    {
        var result = new Dictionary<string, Action>();
        foreach (int size in new[] { 64, 4096, 1447035 })
        {
            byte[] payload = new byte[size]; new Random(size).NextBytes(payload);
            result.Add("hash-" + size, () => { string value = Production.Hash(writer => writer.Write(payload)); sink += value.Length; });
        }
        foreach (var size in new[] { new[] { 64, 64 }, new[] { 1400, 1050 }, new[] { 2048, 2048 } })
        {
            byte[] payload = File.ReadAllBytes(Path.Combine(fixtures, "png-" + size[0] + ".png"));
            int width = size[0], height = size[1];
            result.Add("png-" + width, () => { var value = Capture(payload, "plain", width, height); sink += ((string)value["image_base64"]).Length; });
        }
        return result;
    }
    private static object Measure(Action action, int iterations)
    {
        GC.Collect(); GC.WaitForPendingFinalizers(); GC.Collect();
        int[] collections = { GC.CollectionCount(0), GC.CollectionCount(1), GC.CollectionCount(2) };
        long startAllocation = allocated == null ? 0 : allocated();
        TimeSpan startCpu = Process.GetCurrentProcess().TotalProcessorTime;
        var timer = Stopwatch.StartNew();
        for (int iteration = 0; iteration < iterations; iteration++) action();
        timer.Stop();
        double cpu = (Process.GetCurrentProcess().TotalProcessorTime - startCpu).TotalMilliseconds;
        long allocation = allocated == null ? 0 : allocated() - startAllocation;
        return new { iterations = iterations, wall_ms = timer.Elapsed.TotalMilliseconds, cpu_ms = cpu,
            wall_per_call_ms = timer.Elapsed.TotalMilliseconds / iterations, cpu_per_call_ms = cpu / iterations,
            allocation_bytes = allocated == null ? (long?)null : allocation,
            allocated_bytes_per_call = allocated == null ? (double?)null : (double)allocation / iterations,
            gen0 = GC.CollectionCount(0) - collections[0], gen1 = GC.CollectionCount(1) - collections[1], gen2 = GC.CollectionCount(2) - collections[2] };
    }
    private static object Bench(string fixtures, string iterationPath)
    {
        var serializer = new JavaScriptSerializer();
        var counts = serializer.Deserialize<Dictionary<string, int>>(File.ReadAllText(iterationPath));
        var rows = new Dictionary<string, object>();
        foreach (var pair in Benchmarks(fixtures))
        {
            for (int warmup = 0; warmup < 5; warmup++) pair.Value();
            rows.Add(pair.Key, Measure(pair.Value, counts[pair.Key]));
        }
        return new { runtime = Environment.Version.ToString(), is_64_bit = Environment.Is64BitProcess, intptr_size = IntPtr.Size,
            allocation_counter = allocated == null ? "unavailable" : "GC.GetAllocatedBytesForCurrentThread",
            rows = rows, sink = sink };
    }
    private static int Main(string[] args)
    {
        try
        {
            object result = args[0] == "contracts" ? Contracts(args[1]) : Bench(args[1], args[3]);
            File.WriteAllText(args[2], new JavaScriptSerializer { MaxJsonLength = int.MaxValue }.Serialize(result));
            Console.WriteLine("Buffer copy " + args[0] + " checks passed");
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
