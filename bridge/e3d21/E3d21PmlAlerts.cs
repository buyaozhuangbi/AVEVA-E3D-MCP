using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace E3d21AiBridge;

// Construct on the verified E3D UI thread. These callbacks inspect only managed
// forms; they deliberately do not enter the normal native SDK dispatch gate.
internal sealed class E3d21PmlAlerts
{
    private const string AlertType = "Aveva.Core.Presentation.Druid.UI_MessageBox";
    private const string ButtonType = "Infragistics.Win.Misc.UltraButton";
    private const int BudgetMilliseconds = 250;
    private const int FormLimit = 128;
    private const int ControlLimit = 128;
    private const int MessageLimit = 8192;
    private readonly Action<Action> postOnUi;
    private readonly int ownerThreadId;
    private static readonly Regex CodePattern = new Regex(@"^\((\d{1,5}),\s*(\d{1,5})\)", RegexOptions.CultureInvariant);
    private static readonly Regex SyntaxPattern = new Regex(@"^\(47,\s*15\)\s+CP:\s*Syntax error$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
    private static readonly Regex LinePattern = new Regex(@"(?:^|\r?\n)\s*In line (\d{1,9}) of ", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    internal E3d21PmlAlerts(Action<Action> postOnUi)
    {
        this.postOnUi = postOnUi;
        ownerThreadId = Thread.CurrentThread.ManagedThreadId;
    }

    private sealed class Request
    {
        internal readonly Stopwatch Timer = Stopwatch.StartNew();
        internal int Expired;
        internal bool Active => Volatile.Read(ref Expired) == 0 && Timer.ElapsedMilliseconds < BudgetMilliseconds;
        internal void RequireActive()
        {
            if (!Active) throw new TimeoutException();
        }
    }

    private sealed class Candidate
    {
        internal Form Form;
        internal IntPtr Handle;
        internal RichTextBox Message;
        internal string Text;
        internal Control Button;
        internal bool Known;
        internal bool SyntaxMatched;
        internal Dictionary<string, object> Report;
    }

    private sealed class Observation
    {
        internal readonly List<Candidate> Candidates = new List<Candidate>();
        internal readonly List<object> Alerts = new List<object>();
        internal readonly List<string> Errors = new List<string>();
        internal bool Complete = true;
        internal int ObservedCount;

        internal Dictionary<string, object> Report(long elapsed)
        {
            return new Dictionary<string, object>
            {
                ["complete"] = Complete, ["status"] = Complete ? "complete" : "unknown",
                ["alert_present"] = ObservedCount > 0 ? (object)true : Complete ? (object)false : null,
                ["known_syntax_error_present"] = Candidates.Any(item => item.SyntaxMatched) ? (object)true : Complete ? (object)false : null,
                ["alerts"] = Alerts, ["alert_count"] = ObservedCount, ["errors"] = Errors,
                ["elapsed_ms"] = elapsed, ["budget_ms"] = BudgetMilliseconds, ["read_only"] = true,
                ["scope"] = "Visible modal DruidNet UI_MessageBox forms on the verified E3D UI thread. Only the known read-only txtMessage control and exact OK button are inspected. Returned diagnostics contain numeric error codes and line numbers, never raw message, PML, title or input text. This does not inspect or prove an empty PML input stack."
            };
        }
    }

    internal Dictionary<string, object> Inspect()
    {
        var request = new Request();
        Observation observed;
        if (!Invoke(request, ObserveOnUi, out observed))
        {
            observed = new Observation { Complete = false };
            observed.Errors.Add("PML alert observation did not complete on the verified UI thread within its budget.");
        }
        return observed.Report(request.Timer.ElapsedMilliseconds);
    }

    internal Dictionary<string, object> DismissKnownErrors()
    {
        var request = new Request();
        Observation observed;
        var actions = new List<object>();
        var result = new Dictionary<string, object>
        {
            ["requested"] = false, ["dismissal_confirmed"] = false, ["status"] = "unknown",
            ["outcome_unknown"] = true, ["actions"] = actions, ["read_only"] = false,
            ["input_stack_cleared"] = (object)null,
            ["pml_input_cleared"] = (object)null,
            ["policy"] = "Only an observed visible modal DruidNet UI_MessageBox containing the exact CP syntax error (47,15) may receive its verified OK click. The same form, handle, read-only message and button are revalidated. Queued requests expire; no later replacement alert is selected. Dismissal never establishes cancellation, rollback or an empty PML input stack."
        };
        if (!Invoke(request, ObserveOnUi, out observed))
        {
            observed = new Observation { Complete = false };
            observed.Errors.Add("PML alert observation did not complete; no dismissal was dispatched.");
        }
        result["observation"] = observed.Report(request.Timer.ElapsedMilliseconds);
        if (!observed.Complete) return result;
        Candidate[] known = observed.Candidates.Where(item => item.Known).ToArray();
        if (known.Length == 0)
        {
            result["status"] = "no_known_pml_error_alert";
            result["outcome_unknown"] = false;
            return result;
        }
        if (known.Length != 1 || observed.Alerts.Count != 1)
        {
            result["status"] = "multiple_alerts_not_dismissed";
            result["outcome_unknown"] = false;
            return result;
        }
        Candidate target = known[0];
        Dictionary<string, object> action;
        bool returned = Invoke(request, current => DismissOnUi(current, target), out action);
        if (!returned)
        {
            result["status"] = "dismissal_callback_unconfirmed";
            result["requested"] = null;
            return result;
        }
        actions.Add(action);
        result["requested"] = action["click_requested"];
        result["dismissal_confirmed"] = action["observed_dialog_dismissed"];
        result["status"] = Equals(action["observed_dialog_dismissed"], true)
            ? "observed_dialog_dismissed" : Equals(action["click_requested"], true)
                ? "dismissal_requested_unconfirmed" : "observed_alert_changed_not_dismissed";
        result["outcome_unknown"] = Equals(action["click_requested"], true) && !Equals(action["observed_dialog_dismissed"], true);
        result["elapsed_ms"] = request.Timer.ElapsedMilliseconds;
        return result;
    }

    private bool Invoke<T>(Request request, Func<Request, T> read, out T value)
    {
        value = default(T);
        if (!request.Active || postOnUi == null) return false;
        if (Thread.CurrentThread.ManagedThreadId == ownerThreadId)
        {
            try { value = read(request); return true; }
            catch { return false; }
        }
        var completion = new TaskCompletionSource<T>();
        try
        {
            postOnUi(delegate
            {
                try
                {
                    request.RequireActive();
                    if (Thread.CurrentThread.ManagedThreadId != ownerThreadId)
                        throw new InvalidOperationException("Unexpected UI callback thread.");
                    completion.TrySetResult(read(request));
                }
                catch { completion.TrySetCanceled(); }
            });
            int remaining = Math.Max(0, BudgetMilliseconds - (int)request.Timer.ElapsedMilliseconds);
            if (completion.Task.Wait(remaining) && completion.Task.Status == TaskStatus.RanToCompletion)
            {
                value = completion.Task.Result;
                return true;
            }
        }
        catch { }
        // The callback may remain queued. Its first action checks this token.
        Interlocked.Exchange(ref request.Expired, 1);
        return false;
    }

    private Observation ObserveOnUi(Request request)
    {
        request.RequireActive();
        var result = new Observation();
        try
        {
            int count = 0;
            foreach (Form form in Application.OpenForms)
            {
                request.RequireActive();
                if (++count > FormLimit) throw new InvalidOperationException();
                if (!IsAlert(form)) continue;
                result.ObservedCount++;
                Candidate candidate = ReadCandidate(request, form);
                result.Candidates.Add(candidate);
                result.Alerts.Add(candidate.Report);
            }
        }
        catch
        {
            result.Complete = false;
            result.Errors.Add("Managed PML alert enumeration was incomplete; no empty-alert conclusion is available.");
        }
        return result;
    }

    private static bool IsAlert(Form form)
    {
        return form != null && form.GetType().FullName == AlertType &&
            form.GetType().Assembly.GetName().Name == "DruidNet" &&
            !form.IsDisposed && !form.Disposing && form.IsHandleCreated && form.Visible && form.Modal;
    }

    private static Candidate ReadCandidate(Request request, Form form)
    {
        var candidate = new Candidate { Form = form, Handle = form.Handle };
        var messages = new List<RichTextBox>();
        var buttons = new List<Control>();
        var pending = new Queue<Control>();
        foreach (Control control in form.Controls) pending.Enqueue(control);
        int count = 0;
        while (pending.Count > 0)
        {
            request.RequireActive();
            if (++count > ControlLimit) throw new InvalidOperationException();
            Control control = pending.Dequeue();
            if (control is RichTextBox text && text.Name == "txtMessage" && text.ReadOnly && text.Visible)
                messages.Add(text);
            if (control.GetType().FullName == ButtonType && control is IButtonControl button &&
                button.DialogResult == DialogResult.OK && control.Text == "OK" && control.Visible && control.Enabled &&
                ReferenceEquals(form.AcceptButton, button)) buttons.Add(control);
            foreach (Control child in control.Controls) pending.Enqueue(child);
        }
        string firstLine = "";
        int? module = null, number = null, line = null;
        if (messages.Count == 1 && messages[0].TextLength <= MessageLimit)
        {
            candidate.Message = messages[0];
            candidate.Text = messages[0].Text;
            firstLine = candidate.Text.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? "";
            firstLine = firstLine.Trim();
            if (firstLine.StartsWith("Error:", StringComparison.OrdinalIgnoreCase)) firstLine = firstLine.Substring(6).Trim();
            Match code = CodePattern.Match(firstLine);
            if (code.Success)
            {
                module = int.Parse(code.Groups[1].Value, CultureInfo.InvariantCulture);
                number = int.Parse(code.Groups[2].Value, CultureInfo.InvariantCulture);
            }
            Match atLine = LinePattern.Match(candidate.Text);
            int parsedLine;
            if (atLine.Success && int.TryParse(atLine.Groups[1].Value, out parsedLine)) line = parsedLine;
        }
        candidate.Button = buttons.Count == 1 ? buttons[0] : null;
        candidate.SyntaxMatched = candidate.Message != null && SyntaxPattern.IsMatch(firstLine);
        candidate.Known = candidate.SyntaxMatched && candidate.Button != null;
        candidate.Report = new Dictionary<string, object>
        {
            ["dialog_handle"] = candidate.Handle.ToInt64().ToString("X", CultureInfo.InvariantCulture),
            ["type"] = AlertType, ["visible"] = true, ["modal"] = true,
            ["message_read_complete"] = candidate.Message != null, ["module_number"] = module,
            ["error_number"] = number, ["line_number"] = line,
            ["kind"] = candidate.SyntaxMatched ? "cp_syntax_error" : "unrecognized_pml_alert",
            ["dismissible"] = candidate.Known, ["raw_text_returned"] = false
        };
        return candidate;
    }

    private Dictionary<string, object> DismissOnUi(Request request, Candidate target)
    {
        var result = new Dictionary<string, object>
        {
            ["dialog_handle"] = target.Report["dialog_handle"], ["click_requested"] = false,
            ["observed_dialog_dismissed"] = false, ["input_stack_cleared"] = (object)null
        };
        request.RequireActive();
        int eligibleCount = 0;
        bool targetPresent = false;
        foreach (Form open in Application.OpenForms)
        {
            request.RequireActive();
            if (IsAlert(open))
            {
                eligibleCount++;
                targetPresent |= ReferenceEquals(open, target.Form);
            }
        }
        if (eligibleCount != 1 || !targetPresent) return result;
        if (!IsAlert(target.Form) || target.Form.Handle != target.Handle) return result;
        Candidate current = ReadCandidate(request, target.Form);
        if (!current.Known || !ReferenceEquals(current.Message, target.Message) ||
            !ReferenceEquals(current.Button, target.Button) || current.Text != target.Text) return result;
        request.RequireActive();
        // Use only the exact verified standard OK, never a save/relaunch button.
        result["click_requested"] = true;
        ((IButtonControl)target.Button).PerformClick();
        result["observed_dialog_dismissed"] = target.Form.IsDisposed || !target.Form.Visible;
        return result;
    }
}
