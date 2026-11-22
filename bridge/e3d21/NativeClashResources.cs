using System;
using System.Collections.Generic;
using System.Runtime.CompilerServices;

namespace E3d21AiBridge;

// Tracks only wrappers actually returned by the SDK. It cannot control native
// cleanup inside a failed SDK call or finalizers for wrappers never returned.
internal sealed class NativeClashResources
{
    private sealed class Identity : IEqualityComparer<object>
    {
        public new bool Equals(object left, object right) { return ReferenceEquals(left, right); }
        public int GetHashCode(object value) { return RuntimeHelpers.GetHashCode(value); }
    }
    private readonly HashSet<object> seen = new HashSet<object>(new Identity());
    private readonly List<object> owned = new List<object>();
    private bool cleaned;
    private Dictionary<string, object> receipt;

    internal T Register<T>(T value) where T : class
    {
        if (value != null && seen.Add(value))
        {
            // Suppress before any later native barrier can throw. A native
            // Dispose may itself throw before its own SuppressFinalize call.
            GC.SuppressFinalize(value);
            owned.Add(value);
        }
        return value;
    }

    internal T[] RegisterAll<T>(T[] values) where T : class
    {
        if (values != null) foreach (T value in values) Register(value);
        return values;
    }

    internal Dictionary<string, object> Cleanup(Action<Action> nativeWrite, Func<bool> faulted)
    {
        if (cleaned) return receipt;
        cleaned = true;
        var errors = new List<object>();
        receipt = new Dictionary<string, object>
        {
            ["registered_count"] = owned.Count, ["finalization_suppressed_count"] = owned.Count,
            ["dispose_attempted_count"] = 0, ["disposed_count"] = 0, ["dispose_failed_count"] = 0,
            ["skipped_count"] = 0, ["retained_count"] = owned.Count, ["complete"] = false,
            ["errors"] = errors, ["scope"] = "Only SDK wrappers returned to and registered by this request. SDK-internal cleanup and inaccessible partial constructions are not controlled."
        };
        int attempted = 0, disposed = 0, failed = 0, skipped = 0;
        for (int index = owned.Count - 1; index >= 0; index--)
        {
            if (faulted()) { skipped += index + 1; break; }
            var disposable = owned[index] as IDisposable;
            if (disposable == null)
            {
                skipped++;
                errors.Add(new { code = "disposal_unavailable", index = index });
                continue;
            }
            try
            {
                // Exactly one attempt, including when the destructor throws.
                // The caller supplies the normal pre/post native fault barrier.
                attempted++;
                nativeWrite(() => disposable.Dispose());
                disposed++;
            }
            catch (Exception error)
            {
                failed++;
                errors.Add(new { code = "dispose_failed", index = index, error = error.Message });
            }
        }
        receipt["dispose_attempted_count"] = attempted;
        receipt["disposed_count"] = disposed;
        receipt["dispose_failed_count"] = failed;
        receipt["skipped_count"] = skipped;
        receipt["retained_count"] = owned.Count - disposed;
        receipt["complete"] = skipped == 0 && failed == 0;
        receipt["retention_reason"] = faulted() ? "native_fault_no_further_dispose" :
            skipped != 0 || failed != 0 ? "cleanup_incomplete" : null;
        receipt["native_allocations_may_remain_until_process_exit"] = skipped != 0 || failed != 0;
        return receipt;
    }
}
