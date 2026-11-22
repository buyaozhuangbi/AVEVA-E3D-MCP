using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace E3d21AiBridge;

// Json.NET replaces isolated escaped surrogates with U+FFFD. Validate the
// original JSON strings first so execution and source hashes cannot silently
// use different text from what the caller submitted.
internal static class PmlScriptJson
{
    internal const int MaximumBodyBytes = 2097152;

    internal static JObject Parse(byte[] bytes)
    {
        if (bytes == null || bytes.Length > MaximumBodyBytes)
            throw new ArgumentException("PML script JSON body exceeds the 2 MiB transport limit.");
        string text;
        try { text = new UTF8Encoding(false, true).GetString(bytes); }
        catch (DecoderFallbackException)
        {
            throw new ArgumentException("PML script JSON body must use valid UTF-8.");
        }
        ValidateStringUnicode(text);
        var objectProperties = new Stack<HashSet<string>>();
        using (var json = new JsonTextReader(new StringReader(text)))
        {
            json.DateParseHandling = DateParseHandling.None;
            while (json.Read())
            {
                if (json.TokenType == JsonToken.Comment || json.TokenType == JsonToken.Undefined ||
                    json.TokenType == JsonToken.StartConstructor)
                    throw new ArgumentException("PML script body must be a JSON object.");
                if (json.TokenType == JsonToken.StartObject)
                    objectProperties.Push(new HashSet<string>(StringComparer.Ordinal));
                else if (json.TokenType == JsonToken.EndObject && objectProperties.Count > 0)
                    objectProperties.Pop();
                else if (json.TokenType == JsonToken.PropertyName && objectProperties.Count > 0 &&
                         !objectProperties.Peek().Add((string)json.Value))
                    throw new ArgumentException("Duplicate PML script JSON parameter.");
            }
        }
        using (var json = new JsonTextReader(new StringReader(text)))
        {
            json.DateParseHandling = DateParseHandling.None;
            return JObject.Load(json);
        }
    }

    private static void ValidateStringUnicode(string text)
    {
        for (int index = 0; index < text.Length; index++)
        {
            // JSON property/value delimiters outside a string must use the
            // standard double quote; apostrophes inside PML source strings
            // are ordinary content and must be preserved.
            if (text[index] == '\'')
                throw new ArgumentException("PML script JSON strings must use double quotes.");
            if (text[index] != '"') continue;
            var decoded = new StringBuilder();
            bool closed = false;
            while (++index < text.Length)
            {
                char value = text[index];
                if (value == '"') { closed = true; break; }
                if (value < 32) throw new ArgumentException("PML script JSON string contains an unescaped control character.");
                if (value == '\\')
                {
                    if (++index >= text.Length) break;
                    value = text[index];
                    if (value == 'u')
                    {
                        int scalar = 0;
                        for (int digit = 0; digit < 4; digit++)
                        {
                            if (++index >= text.Length) throw new ArgumentException("Invalid JSON Unicode escape.");
                            char hex = text[index];
                            int number = hex >= '0' && hex <= '9' ? hex - '0' :
                                hex >= 'a' && hex <= 'f' ? hex - 'a' + 10 :
                                hex >= 'A' && hex <= 'F' ? hex - 'A' + 10 : -1;
                            if (number < 0) throw new ArgumentException("Invalid JSON Unicode escape.");
                            scalar = (scalar << 4) | number;
                        }
                        value = (char)scalar;
                    }
                    else if (value == 'b') value = '\b';
                    else if (value == 'f') value = '\f';
                    else if (value == 'n') value = '\n';
                    else if (value == 'r') value = '\r';
                    else if (value == 't') value = '\t';
                    else if (value != '"' && value != '\\' && value != '/')
                        throw new ArgumentException("Invalid JSON string escape.");
                }
                decoded.Append(value);
            }
            if (!closed) throw new ArgumentException("Unterminated JSON string.");
            try { new UTF8Encoding(false, true).GetByteCount(decoded.ToString()); }
            catch (EncoderFallbackException)
            {
                throw new ArgumentException("PML script JSON contains invalid Unicode (an unpaired surrogate).");
            }
        }
    }
}
