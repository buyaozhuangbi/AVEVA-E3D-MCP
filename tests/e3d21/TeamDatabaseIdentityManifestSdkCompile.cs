using System;

namespace E3d21AiBridge;

// Signature-only library target. These stubs make accidental execution fail;
// the project is only compiled against installed public Core Database metadata.
public sealed partial class E3d21Context
{
    private static T NativeRead<T>(Func<T> read) => throw new NotSupportedException("Compile-only SDK contract.");
    private static void ThrowIfNativeFaulted() => throw new NotSupportedException("Compile-only SDK contract.");
    private bool IsCommandActive() => throw new NotSupportedException("Compile-only SDK contract.");
    private T OnUi<T>(Func<T> action) => throw new NotSupportedException("Compile-only SDK contract.");
    private static string DatabaseStateContext(string session, string project, string mdb) =>
        throw new NotSupportedException("Compile-only SDK contract.");
}
