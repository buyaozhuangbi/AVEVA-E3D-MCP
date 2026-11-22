using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;

namespace E3d21AiBridge;

// Pure input/encoding policy. It does not parse PML, touch files or invoke E3D.
internal sealed class PmlScriptInput
{
    internal const int MaximumUtf8Bytes = 262144;
    internal readonly byte[] MacroBytes;
    internal readonly string SourceSha256;
    internal readonly int ByteCount;
    internal readonly int LineCount;
    internal readonly int CodePage;
    internal readonly string EncodingName;
    internal readonly bool TerminalNewlineAdded;

    private PmlScriptInput(byte[] bytes, string hash, int count, int lines,
        Encoding encoding, bool newlineAdded)
    {
        MacroBytes = bytes;
        SourceSha256 = hash;
        ByteCount = count;
        LineCount = lines;
        CodePage = encoding.CodePage;
        EncodingName = encoding.WebName;
        TerminalNewlineAdded = newlineAdded;
    }

    internal static PmlScriptInput Prepare(string source, Encoding nativeEncoding)
    {
        if (string.IsNullOrWhiteSpace(source))
            throw new ArgumentException("source must not be empty or whitespace.", "source");
        if (source.IndexOf('\0') >= 0)
            throw new ArgumentException("source must not contain NUL.", "source");
        // Every valid UTF-16 code unit needs at least one UTF-8 byte on average.
        // This also bounds work before encoding a hostile oversized request.
        if (source.Length > MaximumUtf8Bytes)
            throw new ArgumentException("source exceeds the 262144-byte UTF-8 limit.", "source");
        byte[] utf8;
        try { utf8 = new UTF8Encoding(false, true).GetBytes(source); }
        catch (EncoderFallbackException)
        {
            throw new ArgumentException("source contains invalid Unicode (an unpaired surrogate).", "source");
        }
        if (utf8.Length > MaximumUtf8Bytes)
            throw new ArgumentException("source exceeds the 262144-byte UTF-8 limit.", "source");
        if (nativeEncoding == null) throw new ArgumentNullException("nativeEncoding");
        Encoding strict = Encoding.GetEncoding(nativeEncoding.CodePage,
            EncoderFallback.ExceptionFallback, DecoderFallback.ExceptionFallback);
        bool append = source[source.Length - 1] != '\r' && source[source.Length - 1] != '\n';
        byte[] macro;
        try { macro = strict.GetBytes(append ? source + "\r\n" : source); }
        catch (EncoderFallbackException)
        {
            throw new ArgumentException("source cannot be represented without substitution in E3D's local code page " +
                strict.CodePage + ". No macro was written.", "source");
        }
        int lines = 1;
        for (int index = 0; index < source.Length; index++)
        {
            if (source[index] == '\r')
            {
                lines++;
                if (index + 1 < source.Length && source[index + 1] == '\n') index++;
            }
            else if (source[index] == '\n') lines++;
        }
        string hash;
        using (SHA256 sha = SHA256.Create())
            hash = BitConverter.ToString(sha.ComputeHash(utf8)).Replace("-", "").ToLowerInvariant();
        return new PmlScriptInput(macro, hash, utf8.Length, lines, strict, append);
    }

    internal static string MacroCommand(string absolutePath, int codePage)
    {
        if (string.IsNullOrEmpty(absolutePath) || !Path.IsPathRooted(absolutePath))
            throw new ArgumentException("The generated macro path must be absolute.");
        foreach (char character in absolutePath)
            if (char.IsControl(character) || character == '"' || character == '$' || character == '%')
                throw new ArgumentException("The E3D temporary directory contains characters unsafe for a quoted PML macro path.");
        try
        {
            Encoding.GetEncoding(codePage, EncoderFallback.ExceptionFallback,
                DecoderFallback.ExceptionFallback).GetBytes(absolutePath);
        }
        catch (EncoderFallbackException)
        {
            throw new ArgumentException("The E3D temporary macro path is not representable in the local code page.");
        }
        return "$M \"" + absolutePath.Replace('\\', '/') + "\"";
    }
}
