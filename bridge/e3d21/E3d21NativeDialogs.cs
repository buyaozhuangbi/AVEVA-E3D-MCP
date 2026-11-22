using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;

namespace E3d21AiBridge;

// Native window diagnostics and narrowly scoped error dismissal. Inspect is
// read-only; DismissKnownErrors only posts to verified OK/Cancel buttons. No
// E3D/SDK call, activation, WM_CLOSE, input field or arbitrary button is used.
internal static class E3d21NativeDialogs
{
    private const int TotalBudgetMilliseconds = 250;
    private const int TextBudgetMilliseconds = 25;
    private const int WindowLimit = 512;
    private const int DialogLimit = 32;
    private const int ControlLimit = 512;
    private const int TextCapacity = 1024;
    private const uint WmGetText = 0x000D;
    private const uint SmtoBlock = 0x0001;
    private const uint SmtoAbortIfHung = 0x0002;
    private const uint SmtoErrorOnExit = 0x0020;
    private const uint BmClick = 0x00F5;
    private static readonly Regex DatabaseErrorPattern = new Regex(
        @"(?:^|\r?\n)\s*(?:Unhandled\s+)?Database\s+error\s+(\d+)\b", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    private sealed class Scan
    {
        internal readonly Stopwatch Timer = Stopwatch.StartNew();
        internal readonly List<Dictionary<string, object>> Dialogs = new List<Dictionary<string, object>>();
        internal readonly List<string> Errors = new List<string>();
        internal uint ProcessId;
        internal int Windows, Controls, Failures;
        internal bool Complete = true, DatabaseErrorPresent;

        internal bool WithinBudget()
        {
            if (Timer.ElapsedMilliseconds < TotalBudgetMilliseconds) return true;
            Fail("The total native-dialog scan budget was exhausted.");
            return false;
        }

        internal void Fail(string reason)
        {
            Complete = false;
            Failures++;
            if (Errors.Count < 32 && !Errors.Contains(reason)) Errors.Add(reason);
        }
    }

    internal static Dictionary<string, object> Inspect()
    {
        var scan = new Scan { ProcessId = unchecked((uint)Process.GetCurrentProcess().Id) };
        try
        {
            EnumWindowsProc visitor = delegate(IntPtr window, IntPtr parameter)
            {
                try
                {
                    if (!scan.WithinBudget()) return false;
                    uint process;
                    uint thread = GetWindowThreadProcessId(window, out process);
                    if (process != scan.ProcessId || thread == 0 || !IsWindowVisible(window)) return true;
                    if (++scan.Windows > WindowLimit) { scan.Fail("The current-process top-level window scan limit was exceeded."); return false; }
                    string className = ClassName(window);
                    if (className == null) { scan.Fail("A current-process window class could not be read."); return true; }
                    if (className != "#32770") return true;
                    if (scan.Dialogs.Count >= DialogLimit) { scan.Fail("The standard-dialog scan limit was exceeded."); return false; }
                    InspectDialog(scan, window, thread);
                    return scan.WithinBudget();
                }
                catch { scan.Fail("A native window changed or could not be inspected."); return true; }
            };
            bool enumerated = EnumWindows(visitor, IntPtr.Zero);
            GC.KeepAlive(visitor);
            if (!enumerated && scan.Complete) scan.Fail("Top-level window enumeration did not complete.");
        }
        catch { scan.Fail("Native window enumeration is unavailable."); }
        scan.WithinBudget();
        bool any = scan.Dialogs.Count != 0;
        return new Dictionary<string, object>
        {
            ["complete"] = scan.Complete,
            ["status"] = scan.Complete ? "complete" : "unknown",
            ["standard_dialog_present"] = any ? (object)true : scan.Complete ? (object)false : null,
            ["database_error_present"] = scan.DatabaseErrorPresent ? (object)true : scan.Complete ? (object)false : null,
            ["dialogs"] = scan.Dialogs, ["dialog_count"] = scan.Dialogs.Count,
            ["errors"] = scan.Errors, ["elapsed_ms"] = scan.Timer.ElapsedMilliseconds,
            ["budget_ms"] = TotalBudgetMilliseconds,
            ["scope"] = "Visible #32770 windows in the current E3D process; dialog titles and visible Static/Button child text only. Standard-dialog presence does not itself prove modality. Edit, password and other control text is not read.",
            ["read_only"] = true
        };
    }

    private static void InspectDialog(Scan scan, IntPtr window, uint thread)
    {
        var controls = new List<Dictionary<string, object>>();
        var codes = new List<int>();
        int failuresBefore = scan.Failures;
        string title;
        bool titleRead = ReadBoundedText(scan, window, thread, out title);
        var dialog = new Dictionary<string, object>
        {
            ["handle"] = HandleText(window), ["class"] = "#32770", ["thread_id"] = thread,
            ["title"] = titleRead ? title : null,
            ["visible"] = true, ["controls"] = controls, ["database_error_codes"] = codes,
            ["complete"] = false
        };
        scan.Dialogs.Add(dialog);
        EnumWindowsProc visitor = delegate(IntPtr control, IntPtr parameter)
        {
            try
            {
                if (!scan.WithinBudget()) return false;
                uint process;
                uint ownerThread = GetWindowThreadProcessId(control, out process);
                if (process != scan.ProcessId || ownerThread == 0 || !IsWindowVisible(control)) return true;
                if (++scan.Controls > ControlLimit) { scan.Fail("The current-process child-control scan limit was exceeded."); return false; }
                string className = ClassName(control);
                if (className == null) { scan.Fail("A dialog child class could not be read."); return true; }
                if (className != "Static" && className != "Button") return true;
                string text;
                bool read = ReadBoundedText(scan, control, ownerThread, out text);
                var item = new Dictionary<string, object>
                {
                    ["handle"] = HandleText(control), ["class"] = className, ["control_id"] = GetDlgCtrlID(control),
                    ["text"] = read ? text : null, ["read_complete"] = read
                };
                controls.Add(item);
                if (read && className == "Static")
                {
                    foreach (Match match in DatabaseErrorPattern.Matches(text))
                    {
                        int code;
                        if (int.TryParse(match.Groups[1].Value, NumberStyles.None, CultureInfo.InvariantCulture, out code))
                        {
                            if (!codes.Contains(code)) codes.Add(code);
                            scan.DatabaseErrorPresent = true;
                        }
                    }
                }
                return scan.WithinBudget();
            }
            catch { scan.Fail("A dialog child changed or could not be inspected."); return true; }
        };
        EnumChildWindows(window, visitor, IntPtr.Zero);
        GC.KeepAlive(visitor);
        // Win32 documents EnumChildWindows' return value as unused. Callback
        // stop reasons, limits, errors and window liveness carry completeness.
        if (!IsWindow(window) || !IsWindowVisible(window)) scan.Fail("A standard dialog disappeared during inspection.");
        dialog["complete"] = scan.Failures == failuresBefore && scan.WithinBudget();
    }

    internal static Dictionary<string, object> DismissKnownErrors()
    {
        Dictionary<string, object> observation = Inspect();
        var actions = new List<Dictionary<string, object>>();
        var result = new Dictionary<string, object>
        {
            ["requested"] = false, ["dismissal_confirmed"] = false,
            ["observation"] = observation, ["actions"] = actions,
            ["status"] = "not_attempted", ["outcome_unknown"] = !Equals(observation["complete"], true),
            ["policy"] = "Only a current-process visible AVEVA/E3D standard dialog with exact Database error N Static text and verified IDOK/IDCANCEL caption may receive BM_CLICK. Posting requests dismissal; only a later complete observation can confirm disappearance."
        };
        if (!Equals(observation["complete"], true)) return result;
        var scan = new Scan { ProcessId = unchecked((uint)Process.GetCurrentProcess().Id) };
        foreach (Dictionary<string, object> dialog in (List<Dictionary<string, object>>)observation["dialogs"])
        {
            if (!scan.WithinBudget()) break;
            if (!Equals(dialog["complete"], true) || !KnownTitle(dialog["title"] as string)) continue;
            var controls = (List<Dictionary<string, object>>)dialog["controls"];
            Dictionary<string, object> message = controls.Find(item => Equals(item["class"], "Static") && ExactDatabaseError(item["text"] as string));
            if (message == null) continue;
            Dictionary<string, object> button = controls.Find(item => KnownButton(item, 1)) ?? controls.Find(item => KnownButton(item, 2));
            if (button == null) continue;
            IntPtr window = ParseHandle((string)dialog["handle"]);
            IntPtr messageWindow = ParseHandle((string)message["handle"]);
            IntPtr buttonWindow = ParseHandle((string)button["handle"]);
            uint ownerThread;
            if (!CurrentWindow(window, scan.ProcessId, "#32770", out ownerThread) ||
                !CurrentChild(messageWindow, window, scan.ProcessId, "Static") ||
                !CurrentChild(buttonWindow, window, scan.ProcessId, "Button") || !IsWindowEnabled(buttonWindow) ||
                GetDlgCtrlID(buttonWindow) != (int)button["control_id"])
            { scan.Fail("A known error dialog or its verified controls changed before dismissal."); continue; }
            string freshTitle, freshMessage, freshButton;
            if (!ReadBoundedText(scan, window, ownerThread, out freshTitle) ||
                !ReadBoundedText(scan, messageWindow, ownerThread, out freshMessage) ||
                !ReadBoundedText(scan, buttonWindow, ownerThread, out freshButton)) continue;
            if (freshTitle != (string)dialog["title"] || freshMessage != (string)message["text"] || freshButton != (string)button["text"] ||
                !KnownTitle(freshTitle) || !ExactDatabaseError(freshMessage) || !scan.WithinBudget())
            { scan.Fail("A known error dialog's text changed before dismissal."); continue; }
            bool posted = PostMessage(buttonWindow, BmClick, IntPtr.Zero, IntPtr.Zero);
            actions.Add(new Dictionary<string, object>
            {
                ["dialog_handle"] = dialog["handle"], ["button_control_id"] = button["control_id"],
                ["button_text"] = freshButton, ["message_posted"] = posted,
                ["dismissal_confirmed"] = false
            });
            if (posted) result["requested"] = true;
            else scan.Fail("Posting the verified OK/Cancel button request failed.");
        }
        result["status"] = Equals(result["requested"], true) ? "dismissal_requested_unconfirmed" : scan.Complete ? "no_known_error_dialog" : "unknown";
        result["outcome_unknown"] = !scan.Complete || Equals(result["requested"], true);
        result["errors"] = scan.Errors;
        result["elapsed_ms"] = (long)observation["elapsed_ms"] + scan.Timer.ElapsedMilliseconds;
        return result;
    }

    private static bool KnownTitle(string title)
    {
        return title != null && (title.IndexOf("AVEVA", StringComparison.OrdinalIgnoreCase) >= 0 ||
            title.IndexOf("E3D", StringComparison.OrdinalIgnoreCase) >= 0 || title.IndexOf("Everything3D", StringComparison.OrdinalIgnoreCase) >= 0);
    }

    private static bool ExactDatabaseError(string text)
    {
        if (text == null) return false;
        string candidate = text.Trim();
        Match match = DatabaseErrorPattern.Match(candidate);
        return match.Success && match.Index == 0 && match.Length == candidate.Length;
    }

    private static bool KnownButton(Dictionary<string, object> button, int identifier)
    {
        if (!Equals(button["class"], "Button") || !Equals(button["read_complete"], true) || !Equals(button["control_id"], identifier)) return false;
        string caption = ((string)button["text"] ?? "").Replace("&", "").Trim();
        return identifier == 1
            ? string.Equals(caption, "OK", StringComparison.OrdinalIgnoreCase) || caption == "确定" || caption == "確認"
            : string.Equals(caption, "Cancel", StringComparison.OrdinalIgnoreCase) || caption == "取消";
    }

    private static bool CurrentWindow(IntPtr window, uint processId, string className, out uint thread)
    {
        uint actualProcess;
        thread = GetWindowThreadProcessId(window, out actualProcess);
        return thread != 0 && actualProcess == processId && IsWindow(window) && IsWindowVisible(window) && ClassName(window) == className;
    }

    private static bool CurrentChild(IntPtr child, IntPtr dialog, uint processId, string className)
    {
        uint thread;
        return IsChild(dialog, child) && CurrentWindow(child, processId, className, out thread);
    }

    private static IntPtr ParseHandle(string value)
    {
        ulong parsed;
        if (value == null || !value.StartsWith("0x", StringComparison.Ordinal) ||
            !ulong.TryParse(value.Substring(2), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out parsed)) return IntPtr.Zero;
        return IntPtr.Size == 4 ? new IntPtr(unchecked((int)parsed)) : new IntPtr(unchecked((long)parsed));
    }

    private static bool ReadBoundedText(Scan scan, IntPtr window, uint ownerThread, out string text)
    {
        text = null;
        if (!scan.WithinBudget()) return false;
        uint currentProcess;
        uint currentOwner = GetWindowThreadProcessId(window, out currentProcess);
        if (currentOwner == 0 || currentOwner != ownerThread || currentProcess != scan.ProcessId)
        {
            scan.Fail("A dialog/control owner changed before its bounded text read.");
            return false;
        }
        // SendMessageTimeout cannot enforce its timeout for a same-thread
        // window procedure. Skip that read instead of pretending it is bounded.
        if (ownerThread == GetCurrentThreadId())
        {
            scan.Fail("A dialog text read would execute on the scanning thread; bounded text observation is unavailable.");
            return false;
        }
        int remaining = TotalBudgetMilliseconds - (int)scan.Timer.ElapsedMilliseconds;
        if (remaining <= 0) { scan.WithinBudget(); return false; }
        var buffer = new StringBuilder(TextCapacity);
        UIntPtr copied;
        IntPtr success = SendMessageTimeout(window, WmGetText, new UIntPtr(TextCapacity), buffer,
            SmtoBlock | SmtoAbortIfHung | SmtoErrorOnExit, unchecked((uint)Math.Min(TextBudgetMilliseconds, remaining)), out copied);
        if (success == IntPtr.Zero)
        {
            scan.Fail("An allowed dialog/control text read timed out or failed.");
            return false;
        }
        if (copied.ToUInt64() >= TextCapacity - 1)
        {
            scan.Fail("An allowed dialog/control text value exceeded the bounded text capacity.");
            return false;
        }
        text = buffer.ToString();
        return scan.WithinBudget();
    }

    private static string ClassName(IntPtr handle)
    {
        var text = new StringBuilder(128);
        int length = GetClassName(handle, text, text.Capacity);
        return length > 0 && length < text.Capacity - 1 ? text.ToString() : null;
    }

    private static string HandleText(IntPtr handle)
    {
        return "0x" + unchecked((ulong)handle.ToInt64()).ToString("x", CultureInfo.InvariantCulture);
    }

    private delegate bool EnumWindowsProc(IntPtr window, IntPtr parameter);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool EnumWindows(EnumWindowsProc visitor, IntPtr parameter);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool EnumChildWindows(IntPtr parent, EnumWindowsProc visitor, IntPtr parameter);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool IsWindowVisible(IntPtr window);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool IsWindow(IntPtr window);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool IsWindowEnabled(IntPtr window);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool IsChild(IntPtr parent, IntPtr child);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetClassName(IntPtr window, StringBuilder text, int capacity);

    [DllImport("user32.dll")]
    private static extern int GetDlgCtrlID(IntPtr window);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr SendMessageTimeout(IntPtr window, uint message, UIntPtr wParam,
        StringBuilder text, uint flags, uint timeout, out UIntPtr result);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool PostMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);

    [DllImport("kernel32.dll")]
    private static extern uint GetCurrentThreadId();
}
