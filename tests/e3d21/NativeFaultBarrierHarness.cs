using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using E3d21AiBridge;

// Compiled only by test_native_fault_barriers.py after inserting the actual
// production methods. No AVEVA assemblies or native runtime are loaded.
internal sealed class NativeFaultBarrierHarness
{
    private readonly Action<Action> invokeOnUi;
    private readonly Func<Dictionary<string, object>> executionState;
    private readonly object commandLock = new object();
    private bool commandActive;
    private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
    [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
    [ThreadStatic] private static long permittedNativeFaultGeneration;
    private static int checks;
    private static int nativeCalls;
    private static Action onNativeCall;
    private static readonly List<string> nativeTrace = new List<string>();
    private static DbElement frameOwner, resolvedElement;

    private NativeFaultBarrierHarness(Action<Action> dispatch, Func<Dictionary<string, object>> executionState = null)
    {
        invokeOnUi = dispatch;
        this.executionState = executionState;
    }
    private static void Check(bool value, string message)
    {
        checks++;
        if (!value) throw new Exception(message);
    }
    private static void Fault(NativeDatabaseFaultState state)
    {
        state.Observe("Unhandled Database error 538 from db_test", "test");
    }
    private static void ExpectFault(Action action, string message)
    {
        try { action(); }
        catch (NativeDatabaseFaultException) { Check(true, message); return; }
        throw new Exception(message);
    }
    private static void ExpectRejection(Action action, string message)
    {
        try { action(); }
        catch (InvalidOperationException) { Check(true, message); return; }
        throw new Exception(message);
    }
    private static void Reset()
    {
        currentNativeFaultState = null;
        permittedNativeFaultGeneration = 0;
        nativeCalls = 0;
        onNativeCall = null;
        nativeTrace.Clear();
    }
    private static void NativeCall()
    {
        nativeCalls++;
        if (onNativeCall != null) onNativeCall();
    }
    private static void NativeCall(string entry)
    {
        nativeTrace.Add(entry);
        NativeCall();
    }

    private static int Main()
    {
        try
        {
            TestSafeReads();
            TestNativeCalls();
            TestModelFrames();
            TestDispatch();
            TestOwnDispatchBusyState();
            TestWrites();
            TestRunnerCompensation();
            Reset();
            Console.WriteLine("Native fault barrier checks passed: " + checks);
            return 0;
        }
        catch (Exception failure)
        {
            Console.Error.WriteLine(failure);
            return 1;
        }
    }

    private static void TestSafeReads()
    {
        Reset();
        Check(SafeString(() => null) == "" && SafeInt(() => 5) == 5 && SafeBool(() => true), "Safe helpers retain normal values.");
        Check(SafeString(() => { throw new Exception(); }) == ""
            && SafeInt(() => { throw new Exception(); }) == 0
            && !SafeBool(() => { throw new Exception(); }), "Ordinary failures keep the existing fallback contract.");
        foreach (bool throws in new[] { false, true })
        {
            foreach (string type in new[] { "string", "int", "bool" })
            {
                Reset();
                currentNativeFaultState = new NativeDatabaseFaultState();
                Action callback = () => { NativeCall(); Fault(currentNativeFaultState); if (throws) throw new ArgumentException("SDK failure after callback"); };
                ExpectFault(() =>
                {
                    if (type == "string") SafeString(() => { callback(); return "native"; });
                    else if (type == "int") SafeInt(() => { callback(); return 1; });
                    else SafeBool(() => { callback(); return true; });
                }, "A fault must escape Safe" + type + " whether the native call returns or throws.");
                Check(nativeCalls == 1, "Only the triggering native read may run.");
                ExpectFault(() => SafeString(() => { NativeCall(); return "later"; }), "An existing latch must prevent the next getter.");
                Check(nativeCalls == 1, "Blocked reads cannot invoke their callback.");
            }
        }
        Reset();
        currentNativeFaultState = new NativeDatabaseFaultState();
        var errors = new List<string>();
        ExpectFault(() => DisplayStateReadField(new Dictionary<string, object>(), errors, "native", "colour", () =>
        { Fault(currentNativeFaultState); return 1; }), "A display field cannot downgrade fatal output to a partial read.");
        Check(errors.Count == 0, "The fatal error must not become a normal per-field warning.");
    }

    private static void TestNativeCalls()
    {
        foreach (bool throws in new[] { false, true })
        {
            foreach (bool write in new[] { false, true })
            {
                Reset();
                currentNativeFaultState = new NativeDatabaseFaultState();
                Action callback = () => { NativeCall(); Fault(currentNativeFaultState); if (throws) throw new ArgumentException("native failure"); };
                ExpectFault(() => { if (write) NativeWrite(callback); else NativeRead(() => { callback(); return 1; }); },
                    "Native read/write must check the latch after return and exception.");
                ExpectFault(() => NativeWrite(NativeCall), "A latched call must block later writes.");
                Check(nativeCalls == 1, "The barrier cannot issue a second native call.");
            }
        }
    }

    private static void ResetFrameFixture()
    {
        Reset();
        currentNativeFaultState = new NativeDatabaseFaultState();
        frameOwner = new DbElement();
        resolvedElement = new DbElement();
        MDB.CurrentValue = new MDB { WorldValue = new DbElement() };
    }

    private static void TestModelFrames()
    {
        foreach (string requested in new[] { "WORLD", "OWNER", "=1/2" })
        {
            ResetFrameFixture();
            DbElement expected = requested == "WORLD" ? MDB.CurrentValue.WorldValue :
                requested == "OWNER" ? frameOwner : resolvedElement;
            string[] expectedTrace = requested == "WORLD" ?
                new[] { "mdb.current", "mdb.world", "frame.valid", "frame.null", "frame.dbtype" } :
                requested == "OWNER" ? new[] { "frame.valid", "frame.null", "frame.dbtype" } :
                new[] { "frame.resolve", "frame.valid", "frame.null", "frame.valid", "frame.null", "frame.dbtype" };
            Check(ReferenceEquals(ModelFrame(requested, frameOwner), expected) && nativeTrace.SequenceEqual(expectedTrace),
                "Frame selection retains its native WORLD/OWNER/explicit resolution contract: " + requested);

            foreach (bool throws in new[] { false, true })
            {
                for (int trigger = 1; trigger <= expectedTrace.Length; trigger++)
                {
                    ResetFrameFixture();
                    int faultCall = trigger;
                    onNativeCall = () =>
                    {
                        if (nativeCalls != faultCall) return;
                        Fault(currentNativeFaultState);
                        if (throws) throw new ArgumentException("Frame SDK callback then throw");
                    };
                    ExpectFault(() => ModelFrame(requested, frameOwner),
                        "A frame fault must escape after return or exception at " + requested + ":" + expectedTrace[trigger - 1]);
                    Check(nativeCalls == trigger && nativeTrace.SequenceEqual(expectedTrace.Take(trigger)),
                        "The triggering frame SDK entry is the last entry: " + requested + ":" + expectedTrace[trigger - 1]);
                    ExpectFault(() => ModelFrame(requested, frameOwner), "A latched frame read cannot start again.");
                    Check(nativeCalls == trigger, "A repeated frame read must issue zero additional SDK calls.");
                }
            }
            ResetFrameFixture();
            Fault(currentNativeFaultState);
            ExpectFault(() => ModelFrame(requested, frameOwner), "A prelatched frame lookup must fail before native work.");
            Check(nativeCalls == 0, "A prelatched frame lookup has zero SDK entries.");
        }

        ResetFrameFixture();
        MDB.CurrentValue = null;
        ExpectRejection(() => ModelFrame("WORLD", frameOwner), "An absent MDB retains the ordinary frame rejection.");
        Check(nativeCalls == 1, "An absent MDB cannot query WORLD.");
        ResetFrameFixture();
        onNativeCall = () => { if (nativeCalls == 1) throw new ArgumentException("Ordinary MDB getter failure"); };
        ExpectRejection(() => ModelFrame("WORLD", frameOwner), "An ordinary MDB getter failure retains the existing rejection.");
        Check(nativeCalls == 1 && !currentNativeFaultState.HasFault, "Ordinary MDB failure stops without creating a native fault.");
    }

    private static void TestDispatch()
    {
        Reset();
        var absent = new NativeFaultBarrierHarness(null);
        ExpectRejection(() => absent.OnUi(() => { NativeCall(); return 0; }), "Missing dispatcher must fail closed.");
        Check(nativeCalls == 0, "Missing dispatch must never run the operation.");
        var bridge = new NativeFaultBarrierHarness(action => action());
        permittedNativeFaultGeneration = 123;
        Check(bridge.OnUi(() => 42) == 42 && currentNativeFaultState == null && permittedNativeFaultGeneration == 123,
            "Successful dispatch must restore the previous thread scope.");
        ExpectRejection(() => bridge.OnUi<int>(() => { throw new InvalidOperationException("ordinary"); }), "Ordinary operation failures must propagate.");
        Check(currentNativeFaultState == null && permittedNativeFaultGeneration == 123, "Exception paths must restore the previous thread scope.");
        ExpectRejection(() => bridge.OnUi(() => bridge.OnUi(() => 1, true)), "Nested recovery cannot replace the active fault scope.");
        Fault(bridge.nativeFaultState);
        ExpectFault(() => bridge.OnUi(() => { NativeCall(); return 1; }), "Normal dispatch cannot enter after a fault.");
        Check(nativeCalls == 0, "Rejected normal dispatch cannot call the SDK.");
        long baseline = bridge.nativeFaultState.Generation;
        Check(bridge.OnUi(() => NativeRead(() => 7), true) == 7 && bridge.nativeFaultState.HasFault
            && bridge.nativeFaultState.Generation == baseline, "Recovery can cross an old fault without clearing it.");
        ExpectFault(() => bridge.OnUi(() => { Fault(bridge.nativeFaultState); return NativeRead(() => 7); }, true),
            "A new callback during recovery must stop the recovery operation.");
        Check(currentNativeFaultState == null && permittedNativeFaultGeneration == 123, "Faulted recovery must restore the scope.");

        // Simulate the UI dispatcher finding another live SDK scope even though
        // the HTTP caller's own thread had no scope when it requested dispatch.
        Reset();
        var occupied = new NativeDatabaseFaultState();
        var reentrant = new NativeFaultBarrierHarness(action =>
        {
            currentNativeFaultState = occupied;
            try { action(); }
            finally { currentNativeFaultState = null; }
        });
        ExpectRejection(() => reentrant.OnUi(() => { NativeCall(); return 1; }, true), "A reentrant dispatcher cannot overwrite an active UI scope.");
        Check(nativeCalls == 0, "Reentrant rejection must precede native work.");

        // ThreadStatic scope must be established on the actual execution
        // thread. Fault observations themselves can originate on another thread.
        Reset();
        var threaded = new NativeFaultBarrierHarness(action =>
        {
            Exception failure = null;
            var thread = new Thread(() => { try { action(); } catch (Exception ex) { failure = ex; } });
            thread.Start(); thread.Join();
            if (failure != null) throw failure;
        });
        int callerThread = Thread.CurrentThread.ManagedThreadId;
        ExpectFault(() => threaded.OnUi(() =>
        {
            Check(Thread.CurrentThread.ManagedThreadId != callerThread && ReferenceEquals(currentNativeFaultState, threaded.nativeFaultState),
                "The dispatcher must establish scope on the SDK thread.");
            var callbackThread = new Thread(() => Fault(threaded.nativeFaultState));
            callbackThread.Start(); callbackThread.Join();
            return NativeRead(() => { NativeCall(); return 1; });
        }), "A native callback from another thread must stop the scoped operation.");
        Check(nativeCalls == 0 && currentNativeFaultState == null, "Cross-thread callbacks must block subsequent work without leaking scope to the caller.");
    }

    private static void TestWrites()
    {
        foreach (Action write in new Action[]
        {
            () => ModelWritePosition(new DbElement(), new Position()),
            () => ModelWriteOrientation(new DbElement(), new Orientation()),
            () => PrimitiveWriteDimension(new DbElement(), new DbAttribute(), new DbDouble(), "DIAM")
        })
        {
            Reset();
            currentNativeFaultState = new NativeDatabaseFaultState();
            Fault(currentNativeFaultState);
            ExpectFault(write, "A prelatched write must stop before settable checks or setters.");
            Check(nativeCalls == 0, "A rejected write must perform zero SDK calls.");
            Reset();
            currentNativeFaultState = new NativeDatabaseFaultState();
            // IsAttributeValid, IsAttributeSetable, then SetAttribute.
            onNativeCall = () => { if (nativeCalls == 3) Fault(currentNativeFaultState); };
            ExpectFault(write, "A setter callback must stop before diagnostics or followup native work.");
            Check(nativeCalls == 3, "The triggering setter must be the last native call.");
        }
        var wanted = new DrawListPrimitiveVisualEntry { colour = 1, transparency = 2, edges = true, visibility = true, shallow = true };
        foreach (bool throws in new[] { false, true })
        {
            Reset();
            currentNativeFaultState = new NativeDatabaseFaultState();
            int observations = 0;
            onNativeCall = () => { Fault(currentNativeFaultState); if (throws) throw new ArgumentException("setter callback then throw"); };
            ExpectFault(() => PrimitiveVisualApplyExisting(new DrawListPrimitive(), () =>
                { observations++; return new[] { new DrawListPrimitiveVisualEntry() }; }, wanted, null),
                "A primitive setter fault must stop both further reads and writes.");
            Check(nativeCalls == 1 && observations == 1, "No observation or setter can execute after the setter fault.");

            Reset();
            currentNativeFaultState = new NativeDatabaseFaultState();
            onNativeCall = () => { Fault(currentNativeFaultState); if (throws) throw new ArgumentException("getter callback then throw"); };
            ExpectFault(() => PrimitiveVisualApplyExisting(new DrawListPrimitive(), () =>
                { NativeCall(); return new[] { new DrawListPrimitiveVisualEntry() }; }, wanted, null),
                "A primitive observation fault must stop planning and all setters.");
            Check(nativeCalls == 1, "The failed initial observation is the last native call.");

            Reset();
            currentNativeFaultState = new NativeDatabaseFaultState();
            observations = 0;
            onNativeCall = () => { if (nativeCalls == 2) { Fault(currentNativeFaultState); if (throws) throw new ArgumentException("post-setter getter callback then throw"); } };
            ExpectFault(() => PrimitiveVisualApplyExisting(new DrawListPrimitive(), () =>
                { observations++; if (observations > 1) NativeCall(); return new[] { new DrawListPrimitiveVisualEntry() }; }, wanted, null),
                "A fault during the refreshed observation must stop the compensation loop.");
            Check(nativeCalls == 2 && observations == 2, "No work follows the first setter's failed readback.");
        }
    }

    private static void TestOwnDispatchBusyState()
    {
        Reset();
        bool gateActive = true;
        int diagnosticReads = 0;
        var bridge = new NativeFaultBarrierHarness(action => action(), () =>
        {
            diagnosticReads++;
            return new Dictionary<string, object> { ["native_operation_active"] = gateActive };
        });
        Check(bridge.NativeDispatchBusy() && bridge.BridgeOperationBusy() && bridge.IsCommandActive(),
            "Outside the execution scope, an active native gate must report busy.");
        bridge.OnUi(() =>
        {
            int before = diagnosticReads;
            Check(!bridge.IsCommandActive(), "A read callback must not mistake its own active dispatch gate for another command.");
            Check(diagnosticReads == before, "The callback's own busy recheck must not consult the occupied native gate.");
            Check(bridge.NativeDispatchBusy() && bridge.BridgeOperationBusy(),
                "The raw gate and aggregate diagnostics must still expose the ongoing operation.");
            var other = new NativeFaultBarrierHarness(action => action(), () =>
                new Dictionary<string, object> { ["native_operation_active"] = true });
            Check(other.IsCommandActive(), "Another bridge instance cannot borrow the callback's own-scope exemption.");
            bool outsideBusy = false;
            var outside = new Thread(() => outsideBusy = bridge.IsCommandActive());
            outside.Start(); outside.Join();
            Check(outsideBusy, "Another thread must still see the active gate while the owning callback runs.");
            return 0;
        });
        Check(bridge.IsCommandActive(), "Leaving the callback scope must restore the external gate check.");

        gateActive = false;
        Check(!bridge.NativeDispatchBusy() && !bridge.BridgeOperationBusy() && !bridge.IsCommandActive(),
            "An inactive gate and no command must report idle outside the scope.");
        bridge.commandActive = true;
        Check(bridge.BridgeOperationBusy() && bridge.IsCommandActive(),
            "An active command must report busy even without an active native gate.");
        bridge.OnUi(() =>
        {
            Check(bridge.IsCommandActive(), "The own-scope exemption must not hide an active command.");
            gateActive = true;
            Check(bridge.IsCommandActive(), "An active command remains busy with both the command and native gates active.");
            return 0;
        });
        foreach (bool activeCommand in new[] { false, true })
        {
            bridge.commandActive = activeCommand;
            Fault(bridge.nativeFaultState);
            ExpectFault(() => bridge.IsCommandActive(), "A native fault must take precedence over external busy state.");
            ExpectFault(() => bridge.OnUi(() => bridge.IsCommandActive(), true),
                "Even recovery scope cannot let IsCommandActive hide a latched database fault.");
        }
        Check(currentNativeFaultState == null, "Busy-state checks must leave no thread-local execution scope behind.");
    }

    private static void TestRunnerCompensation()
    {
        Reset();
        var bridge = new NativeFaultBarrierHarness(action => action());
        int rollbackCalls = 0;
        ExpectFault(() => bridge.OnUi(() => PrimitiveUpdateRunner.Run(new[]
        {
            new PrimitiveUpdateStep
            {
                Name = "first",
                Apply = () => NativeWrite(() => { NativeCall(); Fault(bridge.nativeFaultState); }),
                Rollback = () => NativeWrite(() => { rollbackCalls++; NativeCall(); })
            },
            new PrimitiveUpdateStep { Name = "later", Apply = () => NativeWrite(NativeCall), Rollback = () => NativeWrite(NativeCall) }
        }, () => NativeRead(() => true), () => NativeRead(() => true))),
            "A runner that records callback errors must still escape the outer fault barrier.");
        Check(nativeCalls == 1 && rollbackCalls == 0, "A native fault must prohibit both later setters and compensation SDK calls.");
    }

    private sealed class DbAttribute { public string ShortName { get { NativeCall(); return "TEST"; } } }
    private static class DbAttributeInstance { internal static readonly DbAttribute POS = new DbAttribute(), ORI = new DbAttribute(); }
    private sealed class DbQualifier { }
    private sealed class Position { }
    private sealed class Orientation { }
    private sealed class DbDouble { }
    private sealed class PdmsMessage { public string MessageText() { NativeCall(); return "message"; } }
    private enum DbType { Design, Catalog }
    private sealed class MDB
    {
        internal static MDB CurrentValue;
        internal DbElement WorldValue;
        public static MDB CurrentMDB { get { NativeCall("mdb.current"); return CurrentValue; } }
        public DbElement GetFirstWorld(DbType type) { NativeCall("mdb.world"); return WorldValue; }
    }
    private static DbElement ResolveElement(string target)
    {
        // Numeric resolution is a single guarded SDK lookup in production.
        return NativeRead(() => { NativeCall("frame.resolve"); return resolvedElement; });
    }
    private sealed class DbElement
    {
        public bool IsValid { get { NativeCall("frame.valid"); return true; } }
        public bool IsNull { get { NativeCall("frame.null"); return false; } }
        public DbType DbType { get { NativeCall("frame.dbtype"); return DbType.Design; } }
        public bool IsAttributeValid(DbAttribute attribute) { NativeCall(); return true; }
        public bool IsAttributeSetable(DbAttribute attribute) { NativeCall(); return true; }
        public bool SetAttribute(DbAttribute attribute, DbQualifier qualifier, object value, out PdmsMessage message)
        { NativeCall(); message = new PdmsMessage(); return true; }
    }
    private sealed class DrawListPrimitiveVisualEntry
    {
        internal string refno;
        internal int colour, transparency;
        internal bool edges, visibility, shallow;
    }
    private sealed class DrawListPrimitive
    {
        public int Colour { set { NativeCall(); } }
        public int Transparency { set { NativeCall(); } }
        public bool Edges { set { NativeCall(); } }
        public bool Visibility { set { NativeCall(); } }
        public bool ShallowRendering { set { NativeCall(); } }
    }

    // PRODUCTION_METHODS
}
