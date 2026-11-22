using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using Aveva.ApplicationFramework;
using Aveva.ApplicationFramework.Presentation;

namespace E3d21AiBridge;

public sealed class E3d21AiAddin : IAddin
{
    private Form uiDispatcher;
    private IntPtr uiWindow;
    private uint uiThreadId;
    private uint processId;
    private int dispatcherReady;
    private int nativeOperationActive;
    private int lastCallbackThreadId;
    private int dispatchCount;
    private E3d21HttpAdapter adapter;

    private sealed class UiInvocation
    {
        // Pending=0, running=1, completed=2, cancelled before start=3.
        internal int State;
        internal Exception Failure;
    }

    public string Name
    {
        get { return "E3d21AiAddin"; }
    }

    public string Description
    {
        get { return "MCP bridge for AVEVA Everything3D 2.1"; }
    }

    public void Start(ServiceManager serviceManager)
    {
        if (adapter != null)
            return;

        BindExistingUiDispatcher();
        int port = ReadPort();
        string logFile = Environment.GetEnvironmentVariable("E3D21_LOG_FILE");
        E3d21Context context = null;
        // The context subscribes to SDK output events in its constructor. That
        // initialization must obey the same verified UI dispatch as requests.
        InvokeOnUi(delegate { context = new E3d21Context(InvokeOnUi, logFile, PostOnUi, ExecutionState); });
        adapter = new E3d21HttpAdapter(context, port);
        adapter.Start();
        Trace.WriteLine("E3D21 MCP adapter started on 127.0.0.1:" + port);
    }

    public void Stop()
    {
        Volatile.Write(ref dispatcherReady, 0);
        E3d21HttpAdapter current = adapter;
        adapter = null;
        if (current == null)
            return;
        current.Dispose();
        Trace.WriteLine("E3D21 MCP adapter stopped");
    }

    private void InvokeOnUi(Action action)
    {
        if (action == null)
            throw new ArgumentNullException("action");
        RunDispatchedAction(action, delegate(Action callback)
        {
            Form dispatcher = RequireExistingUiDispatcher();
            // InvokeRequired alone can be false when a control has no handle.
            // Inline execution requires the actual cached HWND owner thread.
            if (GetCurrentThreadId() == uiThreadId) callback();
            else dispatcher.Invoke(callback);
        }, VerifyUiCallback);
    }

    private void PostOnUi(Action action)
    {
        if (action == null)
            throw new ArgumentNullException("action");
        Form dispatcher = RequireExistingUiDispatcher();
        // Emergency cancellation is deliberately separate from the SDK gate,
        // and always posted, even when requested from the owning UI thread.
        dispatcher.BeginInvoke(new Action(delegate
        {
            try
            {
                VerifyUiCallback();
                action();
            }
            catch (Exception ex)
            {
                Trace.WriteLine("E3D21 MCP UI cancellation failed: " + ex);
            }
        }));
    }

    private void BindExistingUiDispatcher()
    {
        Volatile.Write(ref dispatcherReady, 0);
        IWindowManager manager = DependencyResolver.Exists<IWindowManager>()
            ? DependencyResolver.GetImplementationOf<IWindowManager>() : null;
        Form mainForm = manager == null ? null : manager.MainForm;
        if (mainForm == null || mainForm.IsDisposed || mainForm.Disposing || !mainForm.IsHandleCreated)
            throw new InvalidOperationException("E3D's existing main window handle is unavailable; SDK work will not run on an unverified thread.");
        // Never call CreateControl/CreateHandle, or read Handle when absent.
        IntPtr handle = mainForm.Handle;
        uint ownerProcess;
        uint ownerThread = GetWindowThreadProcessId(handle, out ownerProcess);
        uint ownProcess = unchecked((uint)Process.GetCurrentProcess().Id);
        if (handle == IntPtr.Zero || !IsWindow(handle) || ownerThread == 0 || ownerProcess != ownProcess)
            throw new InvalidOperationException("The E3D main window's owning process/thread could not be verified.");
        uiDispatcher = mainForm;
        uiWindow = handle;
        uiThreadId = ownerThread;
        processId = ownProcess;
        Volatile.Write(ref dispatcherReady, 1);
    }

    private Form RequireExistingUiDispatcher()
    {
        Form dispatcher = uiDispatcher;
        if (Volatile.Read(ref dispatcherReady) != 1 || dispatcher == null ||
            dispatcher.IsDisposed || dispatcher.Disposing || !dispatcher.IsHandleCreated || !UiWindowMatches())
        {
            Volatile.Write(ref dispatcherReady, 0);
            throw new InvalidOperationException("The verified E3D UI dispatcher is unavailable; no SDK action was dispatched.");
        }
        return dispatcher;
    }

    private void VerifyUiCallback()
    {
        uint actualThread = GetCurrentThreadId();
        Volatile.Write(ref lastCallbackThreadId, unchecked((int)actualThread));
        if (actualThread != uiThreadId)
        {
            Volatile.Write(ref dispatcherReady, 0);
            throw new InvalidOperationException("E3D SDK callback reached a thread other than the main window owner; the action was rejected.");
        }
        Form dispatcher = RequireExistingUiDispatcher();
        // This getter now runs on the HWND's verified owning thread. A handle
        // recreation invalidates the dispatcher instead of silently rebinding.
        if (dispatcher.Handle != uiWindow)
        {
            Volatile.Write(ref dispatcherReady, 0);
            throw new InvalidOperationException("The E3D main window handle changed; the SDK action was rejected.");
        }
        Interlocked.Increment(ref dispatchCount);
    }

    private bool UiWindowMatches()
    {
        IntPtr handle = uiWindow;
        if (handle == IntPtr.Zero || !IsWindow(handle)) return false;
        uint ownerProcess;
        uint ownerThread = GetWindowThreadProcessId(handle, out ownerProcess);
        return ownerThread != 0 && ownerThread == uiThreadId && ownerProcess == processId;
    }

    private Dictionary<string, object> ExecutionState()
    {
        // Health must remain callable while a native operation/modal is active.
        // Only managed fields and Win32 HWND identity are inspected here.
        bool ready = Volatile.Read(ref dispatcherReady) == 1 && UiWindowMatches();
        return new Dictionary<string, object>
        {
            ["native_operation_active"] = Volatile.Read(ref nativeOperationActive) == 1,
            ["dispatcher_ready"] = ready,
            ["ui_thread_id"] = uiThreadId,
            ["last_callback_thread_id"] = unchecked((uint)Volatile.Read(ref lastCallbackThreadId)),
            ["dispatch_count"] = Volatile.Read(ref dispatchCount)
        };
    }

    private void RunDispatchedAction(Action action, Action<Action> dispatch, Action verifyCallback)
    {
        if (action == null) throw new ArgumentNullException("action");
        if (dispatch == null) throw new ArgumentNullException("dispatch");
        if (verifyCallback == null) throw new ArgumentNullException("verifyCallback");
        if (Interlocked.CompareExchange(ref nativeOperationActive, 1, 0) != 0)
            throw new InvalidOperationException("Another E3D SDK operation is active or queued; concurrent and reentrant SDK actions are rejected.");
        var invocation = new UiInvocation();
        try
        {
            dispatch(delegate
            {
                // A failed dispatch may leave a callback queued. Cancelling its
                // token before it starts guarantees that it cannot write later.
                if (Interlocked.CompareExchange(ref invocation.State, 1, 0) != 0) return;
                try
                {
                    verifyCallback();
                    action();
                }
                catch (Exception error) { invocation.Failure = error; }
                finally
                {
                    Volatile.Write(ref invocation.State, 2);
                    Interlocked.Exchange(ref nativeOperationActive, 0);
                }
            });
            if (Volatile.Read(ref invocation.State) != 2)
                throw new InvalidOperationException("The UI dispatcher returned before its SDK callback completed.");
            if (invocation.Failure != null)
                throw new InvalidOperationException(invocation.Failure.Message, invocation.Failure);
        }
        finally
        {
            // Only a callback that has never begun can release ownership here.
            // A running callback retains the gate until its own finally exits,
            // even if the dispatcher unexpectedly throws or returns early.
            if (Interlocked.CompareExchange(ref invocation.State, 3, 0) == 0)
                Interlocked.Exchange(ref nativeOperationActive, 0);
        }
    }

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool IsWindow(IntPtr handle);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr handle, out uint processId);

    [DllImport("kernel32.dll")]
    private static extern uint GetCurrentThreadId();

    private static int ReadPort()
    {
        int port;
        return int.TryParse(Environment.GetEnvironmentVariable("E3D21_MCP_BRIDGE_PORT"), out port) && port > 0 && port < 65536
            ? port
            : 8766;
    }
}
