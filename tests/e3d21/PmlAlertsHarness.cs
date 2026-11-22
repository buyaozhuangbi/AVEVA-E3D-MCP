using System;
using System.Collections;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using E3d21AiBridge;

namespace System.Windows.Forms
{
    public enum DialogResult { None, OK, Cancel }
    public interface IButtonControl { DialogResult DialogResult { get; set; } void PerformClick(); }
    public class Control
    {
        public string Name { get; set; }
        public virtual string Text { get; set; }
        public bool Visible = true, Enabled = true;
        public readonly List<Control> Controls = new List<Control>();
    }
    public class RichTextBox : Control
    {
        private string text = "";
        public bool ReadOnly = true, ThrowOnText;
        public int TextReads;
        public int TextLength => text.Length;
        public override string Text { get { TextReads++; if (ThrowOnText) throw new Exception("private secret text"); return text; } set { text = value; } }
    }
    public class Form : Control
    {
        private static int counter;
        public bool IsDisposed, Disposing, IsHandleCreated = true, Modal = true;
        public IntPtr Handle = new IntPtr(Interlocked.Increment(ref counter));
        public IButtonControl AcceptButton;
    }
    public static class Application { public static readonly List<Form> OpenForms = new List<Form>(); }
}
namespace Aveva.Core.Presentation.Druid
{
    public class UI_MessageBox : System.Windows.Forms.Form { }
}
namespace Infragistics.Win.Misc
{
    public class UltraButton : System.Windows.Forms.Control, System.Windows.Forms.IButtonControl
    {
        public System.Windows.Forms.DialogResult DialogResult { get; set; }
        public int Clicks;
        public Action Click;
        public void PerformClick() { Clicks++; Click?.Invoke(); }
    }
}

internal static class Harness
{
    private static int assertions;
    private const string Syntax = "(47,15)   CP: Syntax error\n In line 4 of Command/Form Input\n PASSWORD privateSecret123";

    private sealed class Fixture
    {
        internal readonly Aveva.Core.Presentation.Druid.UI_MessageBox Form = new Aveva.Core.Presentation.Druid.UI_MessageBox();
        internal readonly System.Windows.Forms.RichTextBox Message = new System.Windows.Forms.RichTextBox { Name = "txtMessage", Text = Syntax };
        internal readonly Infragistics.Win.Misc.UltraButton Ok = new Infragistics.Win.Misc.UltraButton { Text = "OK", DialogResult = System.Windows.Forms.DialogResult.OK };
        internal Fixture()
        {
            Form.Controls.Add(Message); Form.Controls.Add(Ok); Form.AcceptButton = Ok;
            Ok.Click = () => Form.Visible = false;
            System.Windows.Forms.Application.OpenForms.Add(Form);
        }
    }

    private static void Check(bool condition, string message) { assertions++; if (!condition) throw new Exception(message); }
    private static Dictionary<string, object> Dict(object value) => (Dictionary<string, object>)value;
    private static List<object> List(object value) => (List<object>)value;
    private static string Values(object value)
    {
        if (value is IDictionary dictionary) return string.Join(" ", dictionary.Values.Cast<object>().Select(Values));
        if (value is IEnumerable sequence && !(value is string)) return string.Join(" ", sequence.Cast<object>().Select(Values));
        return value == null ? "null" : value.ToString();
    }
    private static void Reset() { System.Windows.Forms.Application.OpenForms.Clear(); }

    private static void ReadCases()
    {
        Reset(); var service = new E3d21PmlAlerts(action => action());
        var empty = service.Inspect();
        Check(Equals(empty["complete"], true) && Equals(empty["alert_present"], false), "known empty");
        var fixture = new Fixture();
        var read = service.Inspect(); var row = Dict(List(read["alerts"])[0]);
        Check(Equals(read["complete"], true) && Equals(read["alert_present"], true), "modal observed");
        Check(Equals(row["module_number"], 47) && Equals(row["error_number"], 15) && Equals(row["line_number"], 4), "safe numeric detail");
        Check(Equals(row["dismissible"], true) && Equals(row["raw_text_returned"], false), "precise eligibility");
        Check(!Values(read).Contains("privateSecret") && !Values(read).Contains("PASSWORD") && !Values(read).Contains("Command/Form"), "no PML leakage");
        Check(fixture.Ok.Clicks == 0, "inspection read only");
        var close = service.DismissKnownErrors();
        Check(fixture.Ok.Clicks == 1 && Equals(close["requested"], true) && Equals(close["dismissal_confirmed"], true), "exact OK clicked");
        Check(close["input_stack_cleared"] == null && !Values(close).Contains("privateSecret"), "no clear-input claim");
        Check(Equals(service.Inspect()["alert_present"], false), "closed dialog excluded");

        foreach (string text in new[] { "(47,16) CP: Something else", "(1,2) Continue?", "Confirm deletion?", "(47,15) CP: Syntax error; then delete" })
        {
            Reset(); fixture = new Fixture(); fixture.Message.Text = text;
            close = service.DismissKnownErrors();
            Check(fixture.Ok.Clicks == 0 && Equals(close["requested"], false), "unknown errors never auto-close");
            Check(Equals(Dict(close["observation"])["alert_present"], true), "unknown is present");
        }
        foreach (Action<Fixture> change in new Action<Fixture>[] {
            item => item.Message.ReadOnly = false, item => item.Message.Name = "password", item => item.Message.Visible = false,
            item => item.Form.AcceptButton = null, item => item.Ok.Text = "Relaunch", item => item.Ok.Enabled = false,
            item => item.Ok.DialogResult = System.Windows.Forms.DialogResult.Cancel,
            item => item.Form.Controls.Add(new System.Windows.Forms.RichTextBox { Name = "txtMessage", Text = Syntax }) })
        {
            Reset(); fixture = new Fixture(); change(fixture); close = service.DismissKnownErrors();
            Check(fixture.Ok.Clicks == 0 && Equals(close["requested"], false), "invalid controls blocked");
        }
        Reset(); fixture = new Fixture(); fixture.Message.Text = new string('x', 8193);
        read = service.Inspect(); Check(fixture.Message.TextReads == 0 && Equals(Dict(List(read["alerts"])[0])["message_read_complete"], false), "bounded text read");
        Reset(); fixture = new Fixture(); fixture.Message.ThrowOnText = true; read = service.Inspect();
        Check(Equals(read["complete"], false) && Equals(read["alert_present"], true), "failed message read retains observed modal presence");
        Check(!Values(read).Contains("private secret"), "exception text redacted");
        Reset(); fixture = new Fixture(); var second = new Fixture(); close = service.DismissKnownErrors();
        Check(fixture.Ok.Clicks == 0 && second.Ok.Clicks == 0 && Equals(close["status"], "multiple_alerts_not_dismissed"), "multiple alerts protected");
        Reset(); System.Windows.Forms.Application.OpenForms.Add(new System.Windows.Forms.Form());
        Check(Equals(service.Inspect()["alert_present"], false), "unrelated form ignored without input reads");
        foreach (Action<Fixture> change in new Action<Fixture>[] { item => item.Form.Modal = false,
            item => item.Form.Visible = false, item => item.Form.IsDisposed = true, item => item.Form.IsHandleCreated = false })
        {
            Reset(); fixture = new Fixture(); change(fixture);
            Check(Equals(service.Inspect()["alert_present"], false) && fixture.Message.TextReads == 0, "ineligible form excluded");
        }
    }

    private static Action Take(BlockingCollection<Action> queue)
    {
        Action action;
        if (!queue.TryTake(out action, 2000)) throw new Exception("missing UI callback");
        return action;
    }

    private static void DispatcherCases()
    {
        Reset(); var fixture = new Fixture(); var queue = new BlockingCollection<Action>();
        var service = new E3d21PmlAlerts(action => queue.Add(action));
        Dictionary<string, object> result = null;
        var thread = new Thread(() => result = service.Inspect()); thread.Start();
        Action expiredRead = Take(queue); Check(thread.Join(1500), "bounded HTTP observation wait"); expiredRead();
        Check(Equals(result["complete"], false) && result["alert_present"] == null && fixture.Message.TextReads == 0, "expired callback never inspects");

        Reset(); fixture = new Fixture(); result = null;
        thread = new Thread(() => result = service.DismissKnownErrors()); thread.Start();
        Take(queue)(); Action expiredClose = Take(queue);
        Check(thread.Join(1500), "bounded HTTP dismissal wait"); expiredClose();
        Check(fixture.Ok.Clicks == 0 && Equals(result["status"], "dismissal_callback_unconfirmed"), "expired close never acts later");
        Check(result["requested"] == null && Equals(result["outcome_unknown"], true), "unobserved callback does not deny possible click");

        foreach (bool replace in new[] { false, true })
        {
            Reset(); fixture = new Fixture(); result = null;
            thread = new Thread(() => result = service.DismissKnownErrors()); thread.Start(); Take(queue)();
            Action close = Take(queue); Fixture newer = null;
            if (replace) { fixture.Form.Visible = false; newer = new Fixture(); }
            else fixture.Message.Text = "(47,15) CP: Syntax error\n In line 50 of Later Input";
            close(); Check(thread.Join(1500), "changed-dialog result completed");
            Check(fixture.Ok.Clicks == 0 && (newer == null || newer.Ok.Clicks == 0) && Equals(result["requested"], false), "changed or replacement dialog preserved");
        }

        Reset(); fixture = new Fixture(); result = null;
        thread = new Thread(() => result = service.DismissKnownErrors()); thread.Start(); Take(queue)();
        Action nestedClose = Take(queue); var nested = new Fixture(); nestedClose();
        Check(thread.Join(1500) && fixture.Ok.Clicks == 0 && nested.Ok.Clicks == 0, "later nested alert prevents old-target dismissal");

        Reset(); fixture = new Fixture(); var wrongThread = new E3d21PmlAlerts(action => action()); result = null;
        thread = new Thread(() => result = wrongThread.Inspect()); thread.Start(); Check(thread.Join(1500), "wrong-thread rejection completed");
        Check(Equals(result["complete"], false) && fixture.Message.TextReads == 0, "wrong thread does not touch forms");
        var missing = new E3d21PmlAlerts(null); result = null;
        thread = new Thread(() => result = missing.Inspect()); thread.Start(); Check(thread.Join(1500), "missing dispatcher completed");
        Check(Equals(result["complete"], false), "missing dispatcher unavailable");
    }

    private static void Main()
    {
        ReadCases(); DispatcherCases();
        Console.WriteLine("PML alert production checks passed: " + assertions);
    }
}
