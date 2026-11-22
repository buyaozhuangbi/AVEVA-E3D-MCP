using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace E3d21AiBridge
{
    internal sealed class JsonBodyTrackingStream : MemoryStream
    {
        private readonly int chunkSize;
        internal int BytesRead;
        internal JsonBodyTrackingStream(byte[] data, int chunkSize) : base(data) { this.chunkSize = chunkSize; }
        public override Task<int> ReadAsync(byte[] buffer, int offset, int count, System.Threading.CancellationToken token)
        {
            int read = base.Read(buffer, offset, Math.Min(count, chunkSize));
            BytesRead += read;
            return Task.FromResult(read);
        }
    }

    internal sealed class JsonBodyRequest
    {
        internal Stream InputStream;
        internal Encoding ContentEncoding = Encoding.UTF8;
    }

    internal static class JsonBodyReaderHarness
    {
        private const int MaximumJsonRequestBodyBytes = 1024 * 1024;
        private static int checks;
        private static readonly List<string> failures = new List<string>();
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);

        private static void Check(bool value, string label)
        {
            checks++;
            if (!value) failures.Add(label);
        }

        private static async Task<JObject> Read(JsonBodyRequest request)
        {
            // PRODUCTION_READ_BODY
        }

        private static async Task<JObject> ReadUnique(JsonBodyRequest request, int maximumBytes = 1024 * 1024)
        {
            // PRODUCTION_READ_UNIQUE
        }

        private static byte[] Bytes(string value) { return Utf8.GetBytes(value); }

        private static Exception TryRead(JsonBodyRequest request, bool unique)
        {
            try
            {
                if (unique) ReadUnique(request).GetAwaiter().GetResult();
                else Read(request).GetAwaiter().GetResult();
                return null;
            }
            catch (Exception error) { return error; }
        }

        private static void RunChecks()
        {
            var valid = new JsonBodyRequest { InputStream = new MemoryStream(Bytes("{\"outer\":{\"value\":1},\"items\":[{\"value\":2}]}")) };
            Check(TryRead(valid, false) == null, "valid body accepted by generic reader");

            foreach (string duplicate in new[] {
                "{\"value\":1,\"value\":2}",
                "{\"outer\":{\"value\":1,\"value\":2}}",
                "{\"va\\u006cue\":1,\"value\":2}"
            })
            {
                var request = new JsonBodyRequest { InputStream = new MemoryStream(Bytes(duplicate)) };
                Exception error = TryRead(request, false);
                Check(error is ArgumentException || error is Newtonsoft.Json.JsonReaderException,
                    "duplicate keys rejected: " + duplicate);
            }

            string oversized = "{\"value\":\"" + new string('x', 2 * 1024 * 1024) + "\"}";
            var boundedStream = new JsonBodyTrackingStream(Bytes(oversized), 8192);
            Exception oversizedError = TryRead(new JsonBodyRequest { InputStream = boundedStream }, false);
            Check(oversizedError is ArgumentException, "oversized generic body rejected");
            Check(boundedStream.BytesRead < Bytes(oversized).Length && boundedStream.BytesRead == 1024 * 1024 + 1,
                "oversized stream stops near the configured budget");

            var unique = new JsonBodyRequest { InputStream = new MemoryStream(Bytes("{\"value\":1,\"other\":2}")) };
            Check(TryRead(unique, true) == null, "unique reader accepts normal body");
        }

        internal static int Main()
        {
            RunChecks();
            if (failures.Count != 0)
            {
                Console.Error.WriteLine("JSON body reader contract failed: " + failures.Count + " / " + checks);
                foreach (string failure in failures) Console.Error.WriteLine(failure);
                return 1;
            }
            Console.WriteLine("JSON body reader contract passed: " + checks);
            return 0;
        }
    }
}
