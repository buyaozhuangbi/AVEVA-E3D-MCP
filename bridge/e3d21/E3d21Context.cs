using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;
using Aveva.ApplicationFramework.Presentation;
using Aveva.CIE.Interaction.Engine;
using Aveva.CIE.Interaction.Engine.Events;
using Aveva.CIE.Interaction.UI;
using Aveva.Core.Database;
using Aveva.Core3D.Graphics;
using Aveva.Core.Utilities.CommandLine;
using Aveva.Core.Utilities.Messaging;
using Aveva.E3D.Standalone;
using Newtonsoft.Json;

using PmlCommand = Aveva.Core.Utilities.CommandLine.Command;

namespace E3d21AiBridge;

// The callback must synchronously dispatch the action to E3D's UI thread.
public sealed partial class E3d21Context : IE3d21Context, IE3d21MessageWindowContext
{
    private readonly Action<Action> invokeOnUi;
    private readonly Action<Action> postOnUi;
    private readonly E3d21PmlAlerts pmlAlerts;
    private readonly Func<Dictionary<string, object>> executionState;
    private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
    [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
    [ThreadStatic] private static long permittedNativeFaultGeneration;
    private readonly string logFile;
    private readonly object messageLock = new object();
    private readonly Queue<Dictionary<string, object>> outputMessages = new Queue<Dictionary<string, object>>();
    private long outputMessageSequence;
    private readonly PdmsOutputEvents.OutputListener outputListener;
    private readonly object commandLock = new object();
    private bool commandActive;
    private string activeCommand = "";
    private readonly object identityLock = new object();
    private Dictionary<string, object> lastProjectIdentity;

    public E3d21Context(Action<Action> invokeOnUi = null, string logFile = null, Action<Action> postOnUi = null,
        Func<Dictionary<string, object>> executionState = null)
    {
        this.invokeOnUi = invokeOnUi;
        this.postOnUi = postOnUi;
        pmlAlerts = new E3d21PmlAlerts(postOnUi);
        this.executionState = executionState;
        this.logFile = logFile;
        outputListener = CaptureOutput;
        PdmsOutputEvents.AddOutputListener(outputListener);
    }

    public bool Connected
    {
        get { return IsConnected(); }
    }

    public object GetProject()
    {
        if (BridgeOperationBusy())
            return new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
            {
                ["connected"] = IsConnected(),
                ["busy"] = true,
                ["command_state"] = CommandState()
            };
        if ((nativeFaultState != null && nativeFaultState.HasFault) || IsGeneratedCsgQuarantined())
        {
            Dictionary<string, object> identity;
            lock (identityLock)
                identity = lastProjectIdentity == null ? new Dictionary<string, object>() : new Dictionary<string, object>(lastProjectIdentity);
            identity["connected"] = IsConnected();
            identity["busy"] = false;
            identity["recovery_only"] = true;
            identity["current_element"] = null;
            identity["native_database_fault"] = nativeFaultState == null ? null : nativeFaultState.Snapshot();
            identity["generated_csg_quarantine"] = GetGeneratedCsgQuarantineState();
            identity["identity_source"] = "Cached identity from the last successful project read; no native database call was made.";
            return identity;
        }
        return OnUi(delegate
        {
            MDB mdb = CurrentMdb();
            Project project = null;
            try
            {
                project = Project.CurrentProject;
            }
            catch
            {
            }

            DbElement current = CurrentElement();
            var result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
            {
                ["name"] = project == null ? "" : SafeString(delegate { return project.Name; }),
                ["user"] = project == null ? "" : SafeString(delegate { return project.UserName; }),
                ["mdb"] = mdb == null ? "" : SafeString(delegate { return mdb.Name; }),
                ["current_element"] = ElementRef(current),
                ["current_element_info"] = ElementInfo(current),
                ["connected"] = mdb != null,
                ["recovery_only"] = false
            };
            lock (identityLock)
                lastProjectIdentity = new Dictionary<string, object> { ["name"] = result["name"], ["mdb"] = result["mdb"] };
            return result;
        });
    }

    public IList<object> Query(string expression, int limit)
    {
        if (string.IsNullOrWhiteSpace(expression))
            throw new ArgumentException("expression must not be empty", "expression");
        if (limit < 1 || limit > 500)
            throw new ArgumentOutOfRangeException("limit");
        if (IsCommandActive())
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

        return OnUi(delegate
        {
            MDB mdb = CurrentMdb();
            if (mdb == null)
                throw new InvalidOperationException("No current MDB is open in E3D 2.1.");

            string wanted = expression.Trim();
            List<DbElement> matches = new List<DbElement>();
            if (wanted.StartsWith("/", StringComparison.Ordinal) || wanted.StartsWith("=", StringComparison.Ordinal))
            {
                AddIfUsable(matches, ResolveElement(wanted), limit);
            }
            else
            {
                CollectMatches(mdb.GetFirstWorld(DbType.Design), wanted, limit, matches, true);
            }
            return matches.Select(ToElement).Cast<object>().ToList();
        });
    }

    public IList<string> ReadLogs(int tail)
    {
        if (tail < 1 || tail > 500)
            throw new ArgumentOutOfRangeException("tail");
        string path = logFile;
        if (string.IsNullOrWhiteSpace(path))
        {
            path = Environment.GetEnvironmentVariable("E3D21_LOG_FILE");
        }
        if (string.IsNullOrWhiteSpace(path))
        {
            path = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Aveva", "E3D-PmlCodeEditor-UIC", "PMLNetTrace.log");
        }
        if (!File.Exists(path))
            return new List<string>();
        Queue<string> recent = new Queue<string>(tail);
        try
        {
            using (FileStream stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (StreamReader reader = new StreamReader(stream))
            {
                string line;
                while ((line = reader.ReadLine()) != null)
                {
                    if (recent.Count == tail)
                        recent.Dequeue();
                    recent.Enqueue(line);
                }
            }
        }
        catch (IOException ex)
        {
            return new List<string> { "[log read failed] " + ex.Message };
        }
        return recent.ToList();
    }

    public object ExecutePml(string command, bool dryRun)
    {
        if (string.IsNullOrWhiteSpace(command))
            throw new ArgumentException("command must not be empty", "command");
        if (dryRun)
        {
            return new Dictionary<string, object>
            {
                ["dry_run"] = true,
                ["command"] = command,
                ["output"] = "PML command preview only; native syntax was not evaluated and E3D state was not changed."
            };
        }

        // CANCEL is an interaction event in E3D, not a normal PML command.
        // Running it through Command.RunInCurrentScope can leave the bridge
        // waiting forever, so route all cancellation aliases through the
        // non-blocking internal cancellation path.
        if (IsCancellationCommand(command))
        {
            Dictionary<string, object> cancellation = (Dictionary<string, object>)CancelCommand();
            cancellation["dry_run"] = false;
            cancellation["command"] = command;
            return cancellation;
        }

        if (!TryBeginCommand(command))
            return new Dictionary<string, object>
            {
                ["dry_run"] = false,
                ["success"] = false,
                ["busy"] = true,
                ["command"] = command,
                ["error"] = "Another E3D command is still running or is waiting on an E3D dialog."
            };

        try
        {
            return OnUi(delegate
            {
                long messageMark = MessageSequence();
                bool recoveryCommand = NativeDatabaseFaultState.IsExactRecoveryCommand(command);
                Dictionary<string, object> validation = recoveryCommand ? null : ValidateCommand(command, CurrentElement());
                if (validation != null)
                {
                    validation["dry_run"] = false;
                    validation["command"] = command;
                    return validation;
                }

                ThrowIfNativeFaulted();
                PmlCommand operation = PmlCommand.CreateCommand(command);
                ThrowIfNativeFaulted();
                // Ordinary requests own a nested PML scope. Local ! variables
                // must stay within this script; !! globals remain explicit
                // session state. Preserve the tested module-reentry path.
                bool success = recoveryCommand ? operation.RunInCurrentScope() : operation.Run();
                ThrowIfNativeFaulted();
                Dictionary<string, object> result = new Dictionary<string, object>
                {
                    ["dry_run"] = false,
                    ["success"] = success,
                    ["command"] = command,
                    ["execution_scope"] = recoveryCommand ? "current" : "nested",
                    ["result"] = recoveryCommand ? "" : SafeString(delegate { return operation.Result; }),
                    ["error"] = recoveryCommand ? "" : MessageText(operation.Error)
                };
                if (nativeFaultState != null && nativeFaultState.HasFault)
                {
                    result["recovery_only"] = true;
                    result["native_database_fault"] = nativeFaultState.Snapshot();
                }
                else AddDiagnostics(result, messageMark);
                return result;
            }, allowNativeFault: NativeDatabaseFaultState.IsExactRecoveryCommand(command));
        }
        finally
        {
            EndCommand();
        }
    }

    public IList<object> ExecutePmlBatch(IList<string> commands, bool dryRun, bool stopOnError)
    {
        if (commands == null || commands.Count == 0)
            throw new ArgumentException("commands must not be empty", "commands");

        if (dryRun)
        {
            return commands.Select(delegate(string command)
            {
                if (string.IsNullOrWhiteSpace(command))
                    return (object)new Dictionary<string, object>
                    {
                        ["dry_run"] = true,
                        ["success"] = false,
                        ["command"] = command ?? "",
                        ["error"] = "command must not be empty"
                    };
                return (object)new Dictionary<string, object>
                {
                    ["dry_run"] = true,
                    ["success"] = true,
                    ["command"] = command,
                    ["output"] = "PML command object created; E3D state was not changed."
                };
            }).ToList();
        }

        string batchLabel = "batch (" + commands.Count + " commands)";
        if (!TryBeginCommand(batchLabel))
            return new List<object>
            {
                new Dictionary<string, object>
                {
                    ["dry_run"] = false,
                    ["success"] = false,
                    ["busy"] = true,
                    ["command"] = batchLabel,
                    ["error"] = "Another E3D command is still running or is waiting on an E3D dialog."
                }
            };

        try
        {
            return OnUi(delegate
            {
                return CommandBatchRunner.Run(commands, stopOnError, delegate(string command)
                {
                    ThrowIfNativeFaulted();
                    long messageMark = MessageSequence();
                    Dictionary<string, object> validation = ValidateCommand(command, CurrentElement());
                    if (validation != null)
                    {
                        validation["dry_run"] = false;
                        validation["command"] = command;
                        return validation;
                    }

                    PmlCommand operation = PmlCommand.CreateCommand(command);
                    ThrowIfNativeFaulted();
                    // Each batch item is a complete script with its own local
                    // scope; scope isolation does not undo earlier effects.
                    bool recoveryCommand = NativeDatabaseFaultState.IsExactRecoveryCommand(command);
                    bool success = recoveryCommand ? operation.RunInCurrentScope() : operation.Run();
                    ThrowIfNativeFaulted();
                    Dictionary<string, object> result = new Dictionary<string, object>
                    {
                        ["dry_run"] = false,
                        ["success"] = success,
                        ["command"] = command,
                        ["execution_scope"] = recoveryCommand ? "current" : "nested",
                        ["result"] = SafeString(delegate { return operation.Result; }),
                        ["error"] = MessageText(operation.Error)
                    };
                    AddDiagnostics(result, messageMark);
                    return result;
                });
            });
        }
        finally
        {
            EndCommand();
        }
    }

    public IList<object> ReadMessages(int tail)
    {
        if (tail < 1 || tail > 500)
            throw new ArgumentOutOfRangeException("tail");
        lock (messageLock)
        {
            return outputMessages.Skip(Math.Max(0, outputMessages.Count - tail)).Cast<object>().ToList();
        }
    }

    public MessageWindowSnapshot ReadMessageWindow(long afterSequence, int limit, bool checkpoint)
    {
        lock (messageLock)
        {
            return MessageWindowSnapshot.Create(outputMessages, outputMessageSequence, afterSequence, limit, checkpoint);
        }
    }

    public object ListMembers(string refno, string expectedType, string cursor, int limit, string sessionId)
    {
        if (limit < 1 || limit > 200)
            throw new ArgumentOutOfRangeException("limit");
        string requestedType = string.IsNullOrWhiteSpace(expectedType)
            ? "" : ModelEditInput.TypeName(expectedType).ToUpperInvariant();
        MemberPageCursor continuation = string.IsNullOrEmpty(cursor) ? null : MemberPageCursor.Decode(cursor);
        if (continuation != null && continuation.Session != sessionId)
            throw new InvalidOperationException("Member cursor belongs to an earlier E3D session. Start a new member listing.");
        if (continuation != null && requestedType.Length > 0 && continuation.ParentType != requestedType)
            throw new InvalidOperationException("Member cursor is bound to another parent type. Start a new member listing.");
        if (IsCommandActive())
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        return OnUi(delegate
        {
            MDB mdb = CurrentMdb();
            Project project = Project.CurrentProject;
            if (mdb == null || project == null)
                throw new InvalidOperationException("No project/MDB is open in E3D.");
            if (continuation != null && (continuation.Project != project.Name || continuation.Mdb != mdb.Name))
                throw new InvalidOperationException("Member cursor belongs to another project or MDB. Start a new member listing.");
            string target = string.IsNullOrWhiteSpace(refno) && continuation != null ? continuation.Parent : refno;
            string parentType = continuation == null ? requestedType : continuation.ParentType;
            DbElement parent = ResolveElementByType(target, parentType);
            if (!IsUsable(parent))
                throw new InvalidOperationException("Parent element was not found: " + target);
            string parentRef = ElementRef(parent);
            string resolvedParentType = ElementType(parent);
            int afterOrdinal = 0;
            if (continuation != null)
            {
                if (continuation.Parent != parentRef || continuation.ParentType != resolvedParentType)
                    throw new InvalidOperationException("Member cursor does not match the requested parent.");
                afterOrdinal = continuation.AfterOrdinal;
                DbElement after = NativeRead(() => parent.Member(afterOrdinal));
                if (!IsUsable(after) || ElementRef(NativeRead(() => after.Owner)) != parentRef ||
                    ElementRef(after) != continuation.After || ElementType(after) != continuation.AfterType ||
                    MemberIdentityToken(after) != continuation.AfterIdentity)
                    throw new InvalidOperationException("Member cursor anchor was deleted or moved. Start a new member listing.");
            }
            MemberPage<DbElement> page = MemberPage<DbElement>.Read(
                DirectMembersAt(parent, afterOrdinal, limit + 1), limit, MemberIdentityKey,
                ElementRef, ElementType, afterOrdinal);
            string nextCursor = page.HasMore == true ? new MemberPageCursor
            {
                Session = sessionId, Project = project.Name, Mdb = mdb.Name,
                Parent = parentRef, ParentType = resolvedParentType,
                After = page.LastRef, AfterType = page.LastType,
                AfterIdentity = page.LastIdentity, AfterOrdinal = page.LastOrdinal
            }.Encode() : null;
            List<object> members = page.Items.Select((item, index) =>
            {
                Dictionary<string, object> identity = ElementIdentity(item);
                identity["sibling_index"] = afterOrdinal + index + 1;
                return (object)identity;
            }).ToList();
            return new Dictionary<string, object>
            {
                ["success"] = page.ReadErrors.Count == 0,
                ["parent"] = ElementIdentity(parent),
                ["members"] = members,
                ["returned_count"] = page.Items.Count,
                ["limit"] = limit,
                ["has_more"] = page.HasMore,
                ["next_cursor"] = nextCursor,
                ["consistency"] = "live",
                ["read_errors"] = page.ReadErrors
            };
        });
    }

    private static IEnumerable<DbElement> DirectMembersAt(DbElement parent, int afterOrdinal, int candidateLimit)
    {
        string parentRef = ElementRef(parent);
        for (int offset = 1; offset <= candidateLimit; offset++)
        {
            int memberNumber = checked(afterOrdinal + offset);
            DbElement current = NativeRead(() => parent.Member(memberNumber));
            if (current == null || NativeRead(() => current.IsNull))
                yield break;
            if (!IsUsable(current) || ElementRef(NativeRead(() => current.Owner)) != parentRef)
                throw new InvalidOperationException("Direct member is unreadable or has an unexpected owner.");
            yield return current;
        }
    }

    private static IEnumerable<DbElement> DirectSiblings(DbElement parent, DbElement after)
    {
        string parentRef = ElementRef(parent);
        DbElement current = NativeRead(() => after == null ? parent.FirstMember() : after.Next());
        while (current != null && !NativeRead(() => current.IsNull))
        {
            if (!NativeRead(() => current.IsValid) || ElementRef(NativeRead(() => current.Owner)) != parentRef)
                throw new InvalidOperationException("Direct member is unreadable or has an unexpected owner.");
            yield return current;
            current = NativeRead(() => current.Next());
        }
    }

    private static string MemberIdentityKey(DbElement element)
    {
        return ElementRef(element) + "\0" + ElementType(element);
    }

    private static string MemberIdentityToken(DbElement element)
    {
        return Convert.ToBase64String(Encoding.UTF8.GetBytes(MemberIdentityKey(element)));
    }

    private static Dictionary<string, object> ElementIdentity(DbElement element)
    {
        return new Dictionary<string, object>
        {
            ["refno"] = ElementRef(element), ["type"] = ElementType(element),
            ["name"] = SafeString(delegate { return element.GetAsString(DbAttributeInstance.NAME); })
        };
    }

    public object GetElementSnapshot(string refno, string expectedType, int depth, int limit)
    {
        if (depth < 0 || depth > 8)
            throw new ArgumentOutOfRangeException("depth");
        if (limit < 1 || limit > 500)
            throw new ArgumentOutOfRangeException("limit");
        string requestedType = string.IsNullOrWhiteSpace(expectedType)
            ? "" : ModelEditInput.TypeName(expectedType).ToUpperInvariant();
        if (IsCommandActive())
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        return OnUi(delegate
        {
            DbElement element = ResolveElementByType(refno, requestedType);
            if (!IsUsable(element))
                throw new InvalidOperationException("Element was not found: " + refno);
            BoundedHierarchy<DbElement> hierarchy = ReadHierarchy(element, depth, limit);
            return BuildSnapshot(hierarchy, depth, limit);
        });
    }

    public object ReadAttributes(string refno, IList<string> attributes, string expectedType = "")
    {
        List<string> names = NormalizeReadAttributes(attributes);
        string requestedType = string.IsNullOrWhiteSpace(expectedType)
            ? "" : ModelEditInput.TypeName(expectedType).ToUpperInvariant();
        if (IsCommandActive())
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        return OnUi(delegate
        {
            DbElement element = ResolveElementByType(refno, requestedType);
            if (!IsUsable(element))
                throw new InvalidOperationException("Element was not found: " + refno);
            return ReadAttributesOnUiThread(element, names);
        });
    }

    public object ReadAttributesBatch(IList<string> refnos, IList<string> attributes)
    {
        List<string> requestedRefnos = (refnos ?? new List<string>()).Select(value => (value ?? "").Trim()).ToList();
        if (requestedRefnos.Count < 1 || requestedRefnos.Count > 50 || requestedRefnos.Any(string.IsNullOrWhiteSpace))
            throw new ArgumentException("refnos must contain between 1 and 50 explicit element references.");
        if (requestedRefnos.Distinct(StringComparer.OrdinalIgnoreCase).Count() != requestedRefnos.Count)
            throw new ArgumentException("refnos must not contain duplicate references.");
        List<string> names = NormalizeReadAttributes(attributes);
        if ((long)requestedRefnos.Count * names.Count > 500)
            throw new ArgumentException("A batch may read at most 500 element/attribute values.");
        if (IsCommandActive())
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

        return OnUi(delegate
        {
            string ceBefore = ElementRef(CurrentElement());
            List<object> rows = new List<object>();
            foreach (string requestedRefno in requestedRefnos)
            {
                ThrowIfNativeFaulted();
                DbElement element;
                try
                {
                    element = ResolveElement(requestedRefno);
                    if (!IsUsable(element))
                        throw new InvalidOperationException("Element was not found: " + requestedRefno);
                }
                catch (Exception ex)
                {
                    ThrowIfNativeFaulted();
                    rows.Add(new Dictionary<string, object>
                    {
                        ["requested_refno"] = requestedRefno,
                        ["refno"] = null,
                        ["type"] = null,
                        ["name"] = null,
                        ["success"] = false,
                        ["values"] = new Dictionary<string, string>(),
                        ["typed_values"] = new Dictionary<string, object>(),
                        ["errors"] = new Dictionary<string, string> { ["$element"] = ex.Message },
                        ["typed_read_errors"] = new Dictionary<string, string>()
                    });
                    continue;
                }

                Dictionary<string, object> row = ReadAttributesOnUiThread(element, names);
                row["requested_refno"] = requestedRefno;
                row["name"] = SafeString(delegate { return element.GetAsString(DbAttributeInstance.NAME); });
                rows.Add(row);
            }

            string ceAfter = ElementRef(CurrentElement());
            bool ceUnchanged = string.Equals(ceBefore, ceAfter, StringComparison.Ordinal);
            int failedRows = rows.Count(row => !Equals(((Dictionary<string, object>)row)["success"], true));
            bool success = failedRows == 0;
            return new Dictionary<string, object>
            {
                ["success"] = success,
                ["complete"] = success && ceUnchanged,
                ["source"] = "E3D SDK attribute reads in one UI-thread dispatch",
                ["single_dispatch"] = true,
                ["ce_before"] = ceBefore,
                ["ce_after"] = ceAfter,
                ["ce_unchanged"] = ceUnchanged,
                ["requested_refnos"] = requestedRefnos,
                ["attributes"] = names,
                ["row_count"] = rows.Count,
                ["failed_row_count"] = failedRows,
                ["rows"] = rows
            };
        });
    }

    private static List<string> NormalizeReadAttributes(IList<string> attributes)
    {
        if (attributes == null || attributes.Count == 0 || attributes.Count > 50)
            throw new ArgumentException("attributes must contain between 1 and 50 attribute names");
        List<string> names = attributes.Select(name => (name ?? "").Trim().ToUpperInvariant())
            .Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        if (names.Any(name => name.Length == 0 || name.Length > 64 ||
            name.Any(c => !(char.IsLetterOrDigit(c) || c == ':' || c == '_'))))
            throw new ArgumentException("Use attribute names only, for example POS, HEIG, or :CUSTOM.");
        return names;
    }

    private static Dictionary<string, object> ReadAttributesOnUiThread(DbElement element, IList<string> names)
    {
        Dictionary<string, string> values = new Dictionary<string, string>();
        Dictionary<string, string> errors = new Dictionary<string, string>();
        Dictionary<string, object> typedValues = new Dictionary<string, object>();
        Dictionary<string, string> typedErrors = new Dictionary<string, string>();
        foreach (string name in names)
        {
            ThrowIfNativeFaulted();
            try
            {
                DbAttribute attribute = NativeRead(() => DbAttribute.GetDbAttribute(name));
                if (attribute == null)
                    throw new InvalidOperationException("Unknown attribute: " + name);
                values[name] = NativeRead(() => element.GetAsString(attribute));
                if (attribute.Type == DbAttributeType.STRING || !attribute.IsArray)
                {
                    try
                    {
                        switch (attribute.Type)
                        {
                            case DbAttributeType.DOUBLE:
                                double number = NativeRead(() => element.GetDouble(attribute));
                                if (double.IsNaN(number) || double.IsInfinity(number))
                                    throw new InvalidOperationException("SDK returned a non-finite scalar.");
                                typedValues[name] = number;
                                break;
                            case DbAttributeType.INTEGER: typedValues[name] = NativeRead(() => element.GetInteger(attribute)); break;
                            case DbAttributeType.STRING: typedValues[name] = NativeRead(() => element.GetString(attribute)); break;
                            case DbAttributeType.BOOL: typedValues[name] = NativeRead(() => element.GetBool(attribute)); break;
                        }
                    }
                    catch (Exception ex) { ThrowIfNativeFaulted(); typedErrors[name] = ex.Message; }
                }
            }
            catch (Exception ex)
            {
                ThrowIfNativeFaulted();
                errors[name] = ex.Message;
            }
        }
        return new Dictionary<string, object>
        {
            ["success"] = errors.Count == 0 && typedErrors.Count == 0,
            ["refno"] = ElementRef(element),
            ["type"] = ElementType(element),
            ["values"] = values,
            ["errors"] = errors,
            ["typed_values"] = typedValues,
            ["typed_read_errors"] = typedErrors
        };
    }

    public object GetSpatialInfo(string refno, string wrt)
    {
        string requestedFrame = (wrt ?? "WORLD").Trim();
        if (requestedFrame.Equals("WORLD", StringComparison.OrdinalIgnoreCase) ||
            requestedFrame.Equals("OWNER", StringComparison.OrdinalIgnoreCase))
            requestedFrame = requestedFrame.ToUpperInvariant();
        else if (!requestedFrame.StartsWith("/", StringComparison.Ordinal) &&
                 !requestedFrame.StartsWith("=", StringComparison.Ordinal))
            throw new ArgumentException("wrt must be WORLD, OWNER, /NAME or =database/element.");
        if (IsCommandActive())
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");

        return OnUi(delegate
        {
            Project projectBefore = NativeRead(() => Project.CurrentProject);
            MDB mdbBefore = NativeRead(() => MDB.CurrentMDB);
            Dictionary<string, object> contextBefore = ReadEndpointContext(projectBefore, mdbBefore);
            DbElement element = ResolveElement(refno);
            if (!IsUsable(element))
                throw new InvalidOperationException("Element was not found: " + refno);
            Dictionary<string, object> result = ReadSpatialInfoOnUi(element, requestedFrame);
            Project projectAfter = NativeRead(() => Project.CurrentProject);
            MDB mdbAfter = NativeRead(() => MDB.CurrentMDB);
            Dictionary<string, object> contextAfter = ReadEndpointContext(projectAfter, mdbAfter);
            ApplyEndpointContext(result, projectBefore, mdbBefore, contextBefore,
                projectAfter, mdbAfter, contextAfter);
            return result;
        });
    }

    private static Dictionary<string, object> ReadSpatialInfoOnUi(DbElement element, string requestedFrame)
    {
        DbElement frame;
        if (requestedFrame == "WORLD")
        {
            MDB mdb = NativeRead(() => CurrentMdb());
            frame = NativeRead(() => mdb.GetFirstWorld(DbType.Design));
        }
        else if (requestedFrame == "OWNER")
            frame = NativeRead(() => element.Owner);
        else
            frame = ResolveElement(requestedFrame);
        if (!IsUsable(frame))
            throw new InvalidOperationException("Reference frame was not found: " + requestedFrame);

        // The SDK evaluates WRT across the owning hierarchy. Do not infer
        // world coordinates from formatted POS text or add parent offsets.
        DbQualifier qualifier = NativeRead(() => new DbQualifier());
        NativeWrite(() => qualifier.wrtQualifier = frame);
        Dictionary<string, string> errors = new Dictionary<string, string>();
        Dictionary<string, object> position = ReadSpatialField(element, DbAttributeInstance.POS, delegate
        {
            Aveva.Core.Geometry.Position value = NativeRead(() => element.GetPosition(DbAttributeInstance.POS, qualifier));
            double[] xyz = FiniteVector(NativeRead(() => value.X), NativeRead(() => value.Y), NativeRead(() => value.Z));
            return new Dictionary<string, object>
            {
                ["unit"] = "mm", ["x"] = xyz[0], ["y"] = xyz[1], ["z"] = xyz[2]
            };
        }, "position", errors);
        Dictionary<string, object> orientation = ReadSpatialField(element, DbAttributeInstance.ORI, delegate
        {
            Aveva.Core.Geometry.Orientation value = NativeRead(() => element.GetOrientation(DbAttributeInstance.ORI, qualifier));
            return new Dictionary<string, object>
            {
                ["x_axis"] = DirectionVector(NativeRead(() => value.XDir())),
                ["y_axis"] = DirectionVector(NativeRead(() => value.YDir())),
                ["z_axis"] = DirectionVector(NativeRead(() => value.ZDir()))
            };
        }, "orientation", errors);
        return new Dictionary<string, object>
        {
            ["success"] = errors.Count == 0,
            ["complete"] = (bool)position["available"] && (bool)orientation["available"],
            // POS-only elements such as PAVE can be read successfully without ORI.
            ["position_complete"] = (bool)position["available"],
            ["orientation_complete"] = (bool)orientation["available"],
            ["source"] = "E3D SDK WRT qualifier",
            ["refno"] = ElementRef(element),
            ["type"] = ElementType(element),
            ["frame"] = new Dictionary<string, object>
            {
                ["requested"] = requestedFrame,
                ["refno"] = ElementRef(frame),
                ["type"] = ElementType(frame),
                ["name"] = SafeString(delegate { return frame.GetAsString(DbAttributeInstance.NAME); })
            },
            ["position"] = position,
            ["orientation"] = orientation,
            ["errors"] = errors
        };
    }

    private static Dictionary<string, object> ReadSpatialField(DbElement element, DbAttribute attribute,
        Func<Dictionary<string, object>> read, string field, Dictionary<string, string> errors)
    {
        try
        {
            if (!NativeRead(() => element.IsAttributeValid(attribute)))
                return new Dictionary<string, object> { ["available"] = false, ["reason"] = "attribute_not_supported" };
            Dictionary<string, object> result = NativeRead(read);
            result["available"] = true;
            return result;
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            errors[field] = ex.Message;
            return new Dictionary<string, object> { ["available"] = false, ["reason"] = "read_failed", ["error"] = ex.Message };
        }
    }

    private static double[] DirectionVector(Aveva.Core.Geometry.Direction direction)
    {
        return FiniteVector(NativeRead(() => direction.East), NativeRead(() => direction.North), NativeRead(() => direction.Up));
    }

    private static double[] FiniteVector(double x, double y, double z)
    {
        double[] values = { x, y, z };
        if (values.Any(value => double.IsNaN(value) || double.IsInfinity(value)))
            throw new InvalidOperationException("The SDK returned a non-finite coordinate.");
        return values;
    }

    public object ValidateModel(string refno, int depth, int limit, IList<string> requiredTypes, bool includeSnapshot = true)
    {
        if (depth < 0 || depth > 8)
            throw new ArgumentOutOfRangeException("depth");
        if (limit < 1 || limit > 2000)
            throw new ArgumentOutOfRangeException("limit");
        if (IsCommandActive())
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        return OnUi(delegate
        {
            DbElement root = ResolveElement(refno);
            if (!IsUsable(root))
                throw new InvalidOperationException("Element was not found: " + refno);

            // Read children incrementally up to the global node budget and
            // boundary lookaheads, without materializing a Members() array.
            BoundedHierarchy<DbElement> hierarchy = BoundedHierarchy<DbElement>.Read(
                root, depth, limit, element => DirectMembersAt(element, 0, limit));
            List<DbElement> elements = hierarchy.Elements;
            Dictionary<string, int> counts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            List<string> issues = new List<string>();
            List<object> checkReadErrors = new List<object>();
            List<object> unverifiedDimensions = new List<object>();
            bool typeReadsComplete = true;
            foreach (DbElement element in elements)
            {
                string elementRef = ElementRef(element);
                string type;
                try
                {
                    var nativeType = NativeRead(() => element.GetElementType());
                    if (nativeType == null)
                        throw new InvalidOperationException("The SDK returned no element type.");
                    type = NativeRead(() => nativeType.ShortName);
                    if (string.IsNullOrWhiteSpace(type))
                        type = NativeRead(() => nativeType.Name);
                    if (string.IsNullOrWhiteSpace(type))
                        throw new InvalidOperationException("The SDK returned an empty element type.");
                    type = type.Trim().ToUpperInvariant();
                }
                catch (Exception ex)
                {
                    ThrowIfNativeFaulted();
                    typeReadsComplete = false;
                    checkReadErrors.Add(new Dictionary<string, object>
                    {
                        ["refno"] = elementRef, ["attribute"] = "TYPE", ["error"] = ex.Message
                    });
                    continue;
                }
                if (!counts.ContainsKey(type))
                    counts[type] = 0;
                counts[type]++;

                string shape = ModelValidationShape(type);
                string[] dimensionAttributes = ModelValidationAttributes(type);
                Dictionary<string, double> dimensionValues = new Dictionary<string, double>(StringComparer.Ordinal);
                foreach (string attributeName in dimensionAttributes)
                {
                    try
                    {
                        dimensionValues[attributeName] = ValidationDistanceMillimetres(element, attributeName);
                    }
                    catch (Exception ex)
                    {
                        ThrowIfNativeFaulted();
                        checkReadErrors.Add(new Dictionary<string, object>
                        {
                            ["refno"] = elementRef, ["type"] = type,
                            ["attribute"] = attributeName, ["error"] = ex.Message
                        });
                    }
                }
                issues.AddRange(ModelValidationDimensionIssues(elementRef, shape, dimensionValues));
                if (type == "NOZZ" || type == "NOZZLE")
                    unverifiedDimensions.Add(new Dictionary<string, object>
                    {
                        ["refno"] = elementRef, ["type"] = type, ["attribute"] = "DIAM",
                        ["reason"] = "unsupported_dimension_attribute"
                    });
            }
            List<string> unverifiedTypes = new List<string>();
            foreach (string requiredType in requiredTypes ?? new List<string>())
            {
                string wanted = (requiredType ?? "").Trim();
                if (wanted.Length > 0 && (!counts.ContainsKey(wanted) || counts[wanted] == 0))
                {
                    if (hierarchy.Complete && typeReadsComplete)
                        issues.Add("Missing required element type: " + wanted);
                    else
                        unverifiedTypes.Add(wanted);
                }
            }

            bool checksComplete = checkReadErrors.Count == 0 && unverifiedDimensions.Count == 0;
            return new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
            {
                ["valid"] = issues.Count > 0 ? (object)false : hierarchy.Complete && checksComplete ? (object)true : null,
                ["complete"] = hierarchy.Complete,
                ["checks_complete"] = checksComplete,
                ["check_read_errors"] = checkReadErrors,
                ["unverified_dimensions"] = unverifiedDimensions,
                ["dimension_check_rules"] = ModelValidationDimensionRules(),
                ["checked_elements_valid"] = issues.Count > 0 ? (object)false : checksComplete ? (object)true : null,
                ["unverified_required_types"] = unverifiedTypes,
                ["scope"] = "Required element types and the listed basic dimension rules on visited elements. Negative primitive dimensions are checked, but dimension checks do not prove Boolean subtraction, solid topology, surface quality, rendering or engineering design validity. DISH bounds are the guarded MCP support policy, not the complete native validity domain. complete covers only hierarchy traversal; checks_complete is false when a supported read fails or a known dimension remains unsupported.",
                ["consistency"] = "single_ui_dispatch",
                ["consistency_scope"] = "Hierarchy traversal, supported checks and optional snapshot run in one E3D UI callback. This is not a database lock or an immutable database snapshot.",
                ["snapshot_info"] = HierarchyInfo(hierarchy, depth, limit),
                ["root"] = ElementInfo(root),
                ["element_count"] = elements.Count,
                ["type_counts"] = counts,
                ["issues"] = issues,
                ["snapshot_included"] = includeSnapshot,
                ["snapshot"] = includeSnapshot ? BuildSnapshot(hierarchy, depth, limit) : null
            };
        });
    }

    public object DeleteElement(string refno, bool dryRun)
    {
        if (!dryRun && !TryBeginCommand("database-delete " + refno))
            return BusyResult("database-delete " + refno);
        try
        {
            return OnUi(delegate
            {
                DbElement element = ResolveElement(refno);
            if (!IsUsable(element))
                return new Dictionary<string, object>
                {
                    ["success"] = false,
                    ["refno"] = refno ?? "",
                    ["delete_attempted"] = false,
                    ["verification_status"] = "not_attempted",
                    ["outcome_uncertain"] = false,
                    ["retry_safe"] = true,
                    ["error"] = "Element was not found: " + refno
                };
            Dictionary<string, object> result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
            {
                ["dry_run"] = dryRun,
                ["delete_attempted"] = false,
                ["refno"] = ElementRef(element),
                ["element"] = ElementInfo(element),
                ["deletable"] = SafeBool(delegate { return element.IsDeleteable; }),
                ["hierarchy_deletable"] = SafeBool(delegate { return element.IsHierarchyDeleteable; })
            };
            if (dryRun)
            {
                result["success"] = true;
                result["verification_status"] = "not_attempted";
                result["output"] = "Deletion was validated; E3D state was not changed.";
                return result;
            }
            if (!Convert.ToBoolean(result["deletable"], CultureInfo.InvariantCulture) || !Convert.ToBoolean(result["hierarchy_deletable"], CultureInfo.InvariantCulture))
            {
                result["success"] = false;
                result["verification_status"] = "not_attempted";
                result["error"] = "E3D does not allow deleting this element or its hierarchy.";
                return result;
            }

            // Capture the exact target DBREF and complete original owner
            // membership before the destructive call.  Delete()'s Boolean
            // return only reports the SDK call; it is not proof that the
            // target is deleted from the database.
            int[] targetReference = NativeRead(() => element.Ref);
            string targetRef = ModelReference(targetReference);
            result["refno"] = targetRef;
            DbElement owner = NativeRead(() => element.Owner);
            if (!IsUsable(owner))
            {
                result["success"] = false;
                result["verification_status"] = "not_attempted";
                result["outcome_uncertain"] = false;
                result["retry_safe"] = true;
                result["error"] = "The exact target owner could not be read; deletion was not attempted.";
                return result;
            }
            string ownerRef = ModelReference(NativeRead(() => owner.Ref));
            string[] originalMembers = ModelMemberReferences(owner);
            if (originalMembers.Count(value => string.Equals(value, targetRef, StringComparison.Ordinal)) != 1)
            {
                result["success"] = false;
                result["verification_status"] = "not_attempted";
                result["outcome_uncertain"] = false;
                result["retry_safe"] = true;
                result["error"] = "The exact target was not a unique direct member of its captured owner; deletion was not attempted.";
                return result;
            }
            result["owner_refno"] = ownerRef;
            result["original_member_count"] = originalMembers.Length;
            PdmsMessage error = null;
            result["delete_attempted"] = true;
            result["retry_safe"] = false;
            bool? nativeDeleteReturned = null;
            try
            {
                ThrowIfNativeFaulted();
                nativeDeleteReturned = element.Delete(out error);
                ThrowIfNativeFaulted();
            }
            catch (Exception deleteError)
            {
                ThrowIfNativeFaulted();
                result["delete_exception"] = deleteError.Message;
            }
            result["native_delete_returned"] = nativeDeleteReturned;
            ModelVerifyDeletion(result, targetReference, targetRef, ownerRef, originalMembers,
                nativeDeleteReturned, error == null ? "" : MessageText(error), ResolveElement);
                return result;
            });
        }
        finally
        {
            if (!dryRun)
                EndCommand();
        }
    }

    public object SetAttribute(string refno, string attributeName, string value, bool dryRun)
    {
        if (string.IsNullOrWhiteSpace(attributeName))
            throw new ArgumentException("attribute must not be empty", "attributeName");
        if (dryRun && IsCommandActive())
            return BusyResult("database-set-attribute-preview " + refno + " " + attributeName);
        if (!dryRun && !TryBeginCommand("database-set-attribute " + refno + " " + attributeName))
            return BusyResult("database-set-attribute " + refno + " " + attributeName);
        try
        {
            return OnUi(delegate
            {
                DbElement element = ResolveElement(refno);
            if (!IsUsable(element))
                return new Dictionary<string, object>
                {
                    ["success"] = false,
                    ["refno"] = refno ?? "",
                    ["error"] = "Element was not found: " + refno
                };
            Dictionary<string, object> result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
            {
                ["dry_run"] = dryRun,
                ["success"] = false,
                ["refno"] = ElementRef(element),
                ["attribute"] = attributeName.Trim().ToUpperInvariant(),
                ["new_value"] = value ?? "",
                ["changed"] = false,
                ["write_attempted"] = false,
                ["write_applied"] = false,
                ["validation_scope"] = "Attribute support, current SDK settable state and input type; further value and database constraints are enforced on execution."
            };
            try
            {
                DbAttribute dbAttribute = NativeRead(() => DbAttribute.GetDbAttribute(attributeName.Trim()));
                if (dbAttribute == null || !dbAttribute.IsValid)
                    throw new InvalidOperationException("E3D attribute was not found: " + attributeName);
                string canonical = dbAttribute.ShortName.ToUpperInvariant();
                result["attribute"] = canonical;
                if (!NativeRead(() => element.IsAttributeValid(dbAttribute)))
                    throw new InvalidOperationException("Attribute " + canonical + " is not supported on " + ElementType(element) + ".");
                if (!NativeRead(() => element.IsAttributeSetable(dbAttribute)))
                    throw new InvalidOperationException("E3D reports that attribute " + canonical + " cannot be set on this element now.");
                object parsed = AttributeEditPolicy.Parse(canonical, value);
                if (!AttributeEditPolicy.SupportsSdkType(canonical, dbAttribute.Type.ToString(), dbAttribute.IsArray))
                    throw new InvalidOperationException("Attribute SDK type is not supported by direct MCP editing: " + dbAttribute.Type);
                result["old_value"] = NativeRead(() => element.GetAsString(dbAttribute));
                result["parsed_value"] = parsed;
                if (!dryRun)
                {
                    result["write_attempted"] = true;
                    result["write_applied"] = null;
                    result["changed"] = null;
                    if (parsed is string) NativeWrite(() => element.SetAttribute(dbAttribute, (string)parsed));
                    else NativeWrite(() => element.SetAttribute(dbAttribute, (double)parsed));
                    result["write_applied"] = true;
                    result["value"] = NativeRead(() => element.GetAsString(dbAttribute));
                    result["changed"] = !string.Equals((string)result["value"], (string)result["old_value"], StringComparison.Ordinal);
                }
                result["success"] = true;
            }
            catch (Exception ex) { ThrowIfNativeFaulted(); result["error"] = ex.Message; }
            return result;
            });
        }
        finally
        {
            if (!dryRun)
                EndCommand();
        }
    }

    public object CommandState()
    {
        Dictionary<string, object> pmlDialogs = pmlAlerts.Inspect();
        Dictionary<string, object> execution = ExecutionDiagnostics();
        bool nativeActive = execution.ContainsKey("native_operation_active") && Equals(execution["native_operation_active"], true);
        Dictionary<string, object> dialogs = E3d21NativeDialogs.Inspect();
        if (nativeFaultState != null && Equals(dialogs["database_error_present"], true))
            foreach (Dictionary<string, object> dialog in (List<Dictionary<string, object>>)dialogs["dialogs"])
                foreach (int code in (List<int>)dialog["database_error_codes"])
                    nativeFaultState.ObserveDialog(code);
        bool faulted = nativeFaultState != null && nativeFaultState.HasFault;
        bool csgQuarantined = IsGeneratedCsgQuarantined();
        lock (commandLock)
        {
            return new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
            {
                ["busy"] = commandActive || nativeActive,
                ["command"] = activeCommand,
                ["execution"] = execution,
                ["native_database_fault"] = nativeFaultState == null ? null : nativeFaultState.Snapshot(),
                ["generated_csg_quarantine"] = GetGeneratedCsgQuarantineState(),
                ["recovery_required"] = faulted || csgQuarantined,
                ["native_dialogs"] = dialogs,
                ["pml_alerts"] = pmlDialogs,
                ["pml_input_cleared"] = null,
                ["readiness_scope"] = "Bridge execution and observed standard/Druid error windows only; idle state does not establish an empty PML input stack.",
                ["database_ready"] = !faulted && !csgQuarantined && !commandActive && !nativeActive &&
                    Equals(execution["dispatcher_ready"], true) && Equals(dialogs["complete"], true) &&
                    Equals(dialogs["standard_dialog_present"], false) &&
                    Equals(pmlDialogs["complete"], true) && Equals(pmlDialogs["alert_present"], false)
            };
        }
    }

    public object CancelCommand()
    {
        // This is intentionally callable while commandActive is true. The normal
        // command endpoint refuses concurrent work, but an interactive command
        // must be cancellable without sending a desktop keystroke.
        Dictionary<string, object> result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
        {
            ["requested"] = true,
            ["success"] = false,
            ["status"] = "pending",
            ["ui_cancel_queued"] = false,
            ["previous_state"] = CommandState()
        };
        result["input_abort"] = false;
        result["completion_confirmed"] = false;
        // This response describes dispatch only. Neither a queued callback nor
        // inputAbort/PostEvent returning proves that the command has settled.
        // Keep it unconfirmed even if the dispatcher happens to run inline.
        // Win32-only observation/posting stays on the HTTP thread: timeout
        // bounds cannot protect a SendMessageTimeout sent to one's own thread.
        result["native_dialog_dismissal"] = E3d21NativeDialogs.DismissKnownErrors();
        result["pml_alert_dismissal"] = pmlAlerts.DismissKnownErrors();
        result["pml_input_cleared"] = null;

        Action cancelOnUi = delegate
        {
            try
            {
                // A fault latch survives dismissal. Do not invoke database or
                // interaction cleanup while recovering a fatal database error.
                if (!IsGeneratedCsgQuarantined() && (nativeFaultState == null || !nativeFaultState.HasFault))
                {
                    DatalListing listing = DatalListing.Instance;
                    if (!IsGeneratedCsgQuarantined() && (nativeFaultState == null || !nativeFaultState.HasFault) && listing != null)
                        listing.inputAbort();
                    if (!IsGeneratedCsgQuarantined() && (nativeFaultState == null || !nativeFaultState.HasFault))
                    {
                        DocEngine engine = CurrentInteractionEngine();
                        if (!IsGeneratedCsgQuarantined() && (nativeFaultState == null || !nativeFaultState.HasFault) && engine != null)
                            engine.PostEvent(new StEvent(StEvent.EventType.CANCEL, null));
                    }
                }
            }
            catch
            {
                // Queuing never proves that cancellation or dismissal succeeded.
            }
        };

        try
        {
            if (postOnUi != null)
            {
                postOnUi(cancelOnUi);
                result["ui_cancel_queued"] = true;
            }
            else
            {
                throw new InvalidOperationException("No verified UI cancellation dispatcher is available.");
            }
        }
        catch (Exception ex)
        {
            result["ui_cancel_queued"] = false;
            result["status"] = "failed";
            result["error"] = ex.Message;
        }
        return result;
    }

    public object GetDrawListSnapshot()
    {
        if (IsCommandActive())
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        return OnUi(delegate
        {
            DrawList drawList = CurrentDrawList();
            if (drawList == null)
                return new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
                {
                    ["available"] = false,
                    ["members"] = new List<object>()
                };

            List<object> members = new List<object>();
            try
            {
                foreach (DrawListMember member in NativeRead(() => drawList.Members()))
                {
                    if (member == null)
                        continue;
                    DbElement element = NativeRead(() => member.DbElement);
                    members.Add(new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
                    {
                        ["refno"] = ElementRef(element),
                        ["type"] = ElementType(element),
                        ["name"] = ElementName(element),
                        ["colour"] = NativeRead(() => member.Colour),
                        ["transparency"] = NativeRead(() => member.Transparency),
                        ["edges"] = NativeRead(() => member.Edges),
                        ["visibility"] = NativeRead(() => member.Visibility),
                        ["shallow"] = NativeRead(() => member.ShallowRendering)
                    });
                }
            }
            catch
            {
                ThrowIfNativeFaulted();
                // A view can be destroyed while its drawlist is updating.
            }
            return new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
            {
                ["available"] = true,
                ["id"] = drawList.ID,
                ["member_count"] = members.Count,
                ["members"] = members
            };
        });
    }

    public object FitView(bool shaded, bool isometric, bool autoAddCurrent)
    {
        if (!TryBeginCommand("view-fit"))
            throw new InvalidOperationException("E3D is busy executing a command or waiting on a dialog.");
        try
        {
            return OnUi(delegate
            {
                View3D view = CameraRequireView();
                CameraDrawListSnapshot membersBefore = CameraReadDrawList(view);
                Dictionary<string, object> before = CameraReadState(view, membersBefore);
                DrawList drawList = CurrentDrawList();
                Dictionary<string, object> result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
                {
                    ["success"] = false,
                    ["shaded"] = shaded,
                    ["isometric"] = isometric,
                    ["restored_count"] = 0,
                    ["auto_added_current"] = false,
                    ["write_attempted"] = false,
                    ["write_applied"] = false,
                    ["drawlist_write_attempted"] = false,
                    ["rollback_attempted"] = false,
                    ["rollback_succeeded"] = null,
                    ["limits"] = null,
                    ["centre"] = null,
                    ["direction"] = null,
                    ["radius"] = null,
                    ["before"] = before,
                    ["after"] = null,
                    ["ce_unchanged"] = null,
                    ["membership_unchanged"] = null,
                    ["visuals_unchanged"] = null,
                    ["view_unchanged"] = null,
                    ["persistence"] = DrawListPersistenceResult("not_needed"),
                    ["source"] = "E3D SDK View3D"
                };
                CameraInitializeOperationReadback(result);
                Dictionary<string, object> requested = null;
                try
                {
                    if (autoAddCurrent && membersBefore.Members.Count == 0)
                    {
                        DbElement current = CurrentElement();
                        DbElement candidate = current;
                        for (int level = 0; IsUsable(candidate) && level < 8; level++)
                        {
                            string type = ElementType(candidate);
                            if (string.Equals(type, "EQUI", StringComparison.OrdinalIgnoreCase) ||
                                string.Equals(type, "EQUIPMENT", StringComparison.OrdinalIgnoreCase))
                                break;
                            candidate = NativeRead(() => candidate.Owner);
                        }
                        if (!IsUsable(candidate))
                            candidate = current;
                        if (IsUsable(candidate) && !NativeRead(() => drawList.Exists(candidate)))
                        {
                            result["drawlist_write_attempted"] = true;
                            NativeWrite(() => drawList.Add(candidate));
                            result["auto_added_current"] = true;
                        }
                    }

                    DrawListManager manager = DrawListManager.Instance;
                    if (manager != null)
                        NativeWrite(() => manager.UpdateGraphics());
                    // Revalidate the active identity after graphics materialization before
                    // applying this one camera request to the originally selected view.
                    CameraDrawListSnapshot fittingMembers = CameraReadDrawList(view);
                    if (fittingMembers.Id != membersBefore.Id)
                        throw new InvalidOperationException("The active Drawlist changed before the camera request could be applied.");
                    double[] limits = CameraRequireLimits(NativeRead(() => view.GetDrawlistLimits()));
                    double[] centre =
                    {
                        limits[0] / 2.0 + limits[1] / 2.0,
                        limits[2] / 2.0 + limits[3] / 2.0,
                        limits[4] / 2.0 + limits[5] / 2.0
                    };
                    double extent = Math.Max(limits[1] - limits[0],
                        Math.Max(limits[3] - limits[2], limits[5] - limits[4]));
                    if (extent < 1.0)
                        extent = 1000.0;
                    double radius = extent * 0.75;
                    if (!CameraFinite(radius) || radius <= 0.0)
                        throw new InvalidOperationException("The fit bounds do not provide a finite positive camera radius.");
                    double[] direction = isometric
                        ? new[] { 1.0, 1.0, -1.0 }
                        : CameraRequireVector(NativeRead(() => view.GetDirection()), "direction");
                    CameraNormalized(direction);
                    requested = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
                    {
                        ["centre"] = centre,
                        ["direction"] = direction,
                        ["radius"] = radius,
                        ["perspective"] = NativeRead(() => view.PerspectiveMode),
                        ["extra_twist_degrees"] = NativeRead(() => view.GetExtraTwistDegrees()),
                        ["shaded"] = shaded || NativeRead(() => view.ShadeMode),
                        ["coordinate_frame"] = "SDK native",
                        ["direction_convention"] = "native View3D looking direction (x, y, z)"
                    };
                    result["limits"] = limits;
                    result["requested_camera"] = requested;
                    result["write_attempted"] = true;
                    result["write_applied"] = null;
                    if (!NativeRead(() => view.SetCentreAndDirNoMapUpdate(centre, direction)))
                        throw new InvalidOperationException("E3D rejected the requested camera centre or direction.");
                    result["write_applied"] = true;
                    NativeWrite(() => view.SetRadius(radius));
                    if (shaded)
                        NativeWrite(() => view.SetShadeModeNoMapUpdate(true));
                    NativeWrite(() => view.MapView());
                    result["camera_write_completed"] = true;
                }
                catch (Exception ex)
                {
                    ThrowIfNativeFaulted();
                    result["error"] = ex.Message;
                }
                if ((bool)result["auto_added_current"])
                    result["persistence"] = SaveDrawListState(drawList);
                CameraCompleteOperation(result, view, before, membersBefore, requested, false, !(bool)result["auto_added_current"]);
                Dictionary<string, object> actual = result["actual_camera"] as Dictionary<string, object>;
                if (actual != null)
                {
                    // Legacy scalar fields now report SDK observations, never request echoes.
                    result["centre"] = actual["centre"];
                    result["direction"] = actual["direction"];
                    result["radius"] = actual["radius"];
                }
                if ((bool)result["drawlist_write_attempted"] && !Equals(result["success"], true))
                    result["retry_safe"] = false;
                return result;
            });
        }
        finally { EndCommand(); }
    }

    // E3D does not persist the active Drawlist between application sessions.
    // Reload saved members only when explicitly requested by the caller.
    public object RestoreDrawList(bool fitView)
    {
        ThrowIfNativeFaulted();
        if (IsCommandActive())
            return new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
            {
                ["ready"] = false,
                ["retry"] = true,
                ["reason"] = "E3D is busy executing a command or waiting on a dialog.",
                ["restored_count"] = 0,
                ["pending_count"] = 0,
                ["persistence"] = DrawListPersistenceResult("busy")
            };

        return OnUi(delegate
        {
            List<string> persistedRefs = new List<string>();
            DrawListVisualDocument visualState = null;
            try
            {
                ReadDrawListState(DrawListStatePath(), out persistedRefs, out visualState);
                MDB currentMdb = CurrentMdb();
                if (visualState != null && currentMdb != null)
                {
                    Project project = Project.CurrentProject;
                    ValidateDrawListVisualState(visualState, project == null ? null : project.Name, currentMdb.Name);
                }
            }
            catch (Exception ex)
            {
                ThrowIfNativeFaulted();
                return new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
                {
                    ["ready"] = false,
                    ["retry"] = false,
                    ["restored_count"] = 0,
                    ["pending_count"] = 0,
                    ["fit"] = false,
                    ["graphics_updated"] = false,
                    ["persisted_count"] = persistedRefs.Count,
                    ["restore_succeeded"] = false,
                    ["visual_state_available"] = visualState != null,
                    ["visual_restore_succeeded"] = false,
                    ["visual_restored_count"] = 0,
                    ["visual_apply_attempted_count"] = 0,
                    ["add_errors"] = new Dictionary<string, string>(),
                    ["visual_errors"] = new Dictionary<string, string> { ["state"] = ex.Message },
                    ["legacy_membership_only"] = false,
                    ["reason"] = "Persisted Drawlist state is unreadable or invalid; no Drawlist changes were made.",
                    ["persistence"] = DrawListPersistenceResult("restore_state_invalid", ex.Message)
                };
            }
            if (persistedRefs.Count == 0)
                return new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
                {
                    ["ready"] = true,
                    ["retry"] = false,
                    ["restored_count"] = 0,
                    ["pending_count"] = 0,
                    ["fit"] = false,
                    ["reason"] = "No persisted Drawlist elements.",
                    ["persistence"] = DrawListPersistenceResult("no_persisted_members")
                };

            if (CurrentMdb() == null)
                return new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
                {
                    ["ready"] = false,
                    ["retry"] = true,
                    ["restored_count"] = 0,
                    ["pending_count"] = persistedRefs.Count,
                    ["fit"] = false,
                    ["reason"] = "E3D has not opened an MDB yet.",
                    ["persistence"] = DrawListPersistenceResult("context_unavailable")
                };

            DrawList drawList = CurrentDrawList();
            if (drawList == null)
                return new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
                {
                    ["ready"] = false,
                    ["retry"] = true,
                    ["restored_count"] = 0,
                    ["pending_count"] = persistedRefs.Count,
                    ["fit"] = false,
                    ["reason"] = "E3D has not created a current Drawlist yet.",
                    ["persistence"] = DrawListPersistenceResult("drawlist_unavailable")
                };

            int pendingCount;
            Dictionary<string, object> restoreDetails;
            int restoredCount = RestoreDrawListState(drawList, persistedRefs, visualState, fitView, out pendingCount, out restoreDetails);

            bool restoreSucceeded = Equals(restoreDetails["restore_succeeded"], true);
            Dictionary<string, object> persistence = pendingCount == 0 && restoreSucceeded
                ? SaveDrawListState(drawList)
                : DrawListPersistenceResult("restore_incomplete");
            var restoreResult = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
            {
                ["ready"] = pendingCount == 0 && restoreSucceeded,
                ["retry"] = pendingCount > 0,
                ["restored_count"] = restoredCount,
                ["pending_count"] = pendingCount,
                ["fit"] = restoreDetails["fit"],
                ["graphics_updated"] = restoreDetails["graphics_updated"],
                ["persisted_count"] = persistedRefs.Count,
                ["persistence"] = persistence
            };
            foreach (KeyValuePair<string, object> detail in restoreDetails) restoreResult[detail.Key] = detail.Value;
            return restoreResult;
        });
    }

    public object SetDrawListVisual(string refno, string colour, int? transparency, bool? edges, bool? visibility, bool? shallow, bool add, bool dryRun)
    {
        ThrowIfNativeFaulted();
        if (string.IsNullOrWhiteSpace(refno))
            throw new ArgumentException("refno must not be empty", "refno");
        if (transparency.HasValue && (transparency.Value < 0 || transparency.Value > 100))
            throw new ArgumentOutOfRangeException("transparency");
        return OnUi(delegate
        {
            // Preview and execution share native-table validation before Add or
            // any visual write. Null means omitted, never an invalid colour.
            int? colourIndex = ResolveColourIndex(colour);
            DrawList drawList = CurrentDrawList();
            if (drawList == null)
                throw new InvalidOperationException("No current E3D Drawlist is available.");
            DbElement element = ResolveElement(refno);
            if (!IsUsable(element))
                throw new InvalidOperationException("Element was not found: " + refno);
            string targetRef = ElementRef(element);
            Dictionary<string, DrawListVisualEntry> before = CaptureDrawListVisualSnapshot(drawList);
            // TOPF describes the native display granularity, not whether pixels
            // are visible. Add does not turn an ordinary primitive into a TOPF.
            bool topLevelElement = DisplayStateTopLevelElement(element);
            bool exactMember = before.ContainsKey(targetRef);
            if (!exactMember && (!topLevelElement || !add))
                return new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
                {
                    ["success"] = false, ["dry_run"] = dryRun, ["refno"] = targetRef,
                    ["write_attempted"] = false, ["add_attempted"] = false, ["added"] = false,
                    ["exact_member"] = false, ["top_level_element"] = topLevelElement,
                    ["scope_mismatch"] = !topLevelElement,
                    ["error"] = !topLevelElement
                        ? "This element is not a native top-level display element. Add cannot provide independent member properties. Inspect e3d_display_state for any actual member and primitive entries; do not repeat add=true or silently modify its ancestor."
                        : "The top-level element is not in the current Drawlist. Use add=true only if adding it is intended.",
                    ["persistence"] = DrawListPersistenceResult("preflight_rejected")
                };
            Dictionary<string, DrawListPrimitiveVisualEntry> beforePrimitives = CaptureDrawListPrimitiveSnapshot(drawList);
            try
            {
                if (transparency == 100 && (!exactMember || before[targetRef].transparency != 100))
                    throw new UnsupportedTransparencyStateException("E3D 2.1's member transparency setter clamps 100 to 99; exact member transparency 100 is unsupported by this operation.");
                if (exactMember)
                {
                    var previewMembers = before.ToDictionary(item => item.Key, item => CloneDrawListVisual(item.Value), StringComparer.Ordinal);
                    if (transparency.HasValue) previewMembers[targetRef].transparency = transparency.Value;
                    PrimitiveVisualPreflightTransparency(drawList, PrimitiveVisualReadMap(drawList), beforePrimitives, previewMembers);
                }
                else if (transparency.GetValueOrDefault() != 0 && beforePrimitives.Values.Any(entry => entry.transparency == 0 &&
                    PrimitiveVisualInSubtree(ModelRequireElement(entry.refno), targetRef)))
                    throw new UnsupportedTransparencyStateException("Adding a member with nonzero transparency cannot guarantee preservation of existing zero-transparency primitives in its subtree.");
            }
            catch (UnsupportedTransparencyStateException error)
            {
                return new Dictionary<string, object>
                {
                    ["success"] = false, ["dry_run"] = dryRun, ["refno"] = targetRef,
                    ["unsupported_transparency_state"] = true, ["error"] = error.Message,
                    ["write_attempted"] = false, ["add_attempted"] = false, ["added"] = false,
                    ["refresh_attempted"] = false, ["graphics_updated"] = false, ["rollback_attempted"] = false,
                    ["persistence"] = DrawListPersistenceResult("preflight_rejected")
                };
            }
            if (dryRun)
                return new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
                {
                    ["success"] = true,
                    ["dry_run"] = true,
                    ["refno"] = targetRef,
                    ["write_attempted"] = false,
                    ["add_attempted"] = false,
                    ["top_level_element"] = topLevelElement,
                    ["exact_member"] = exactMember,
                    ["requires_add"] = !exactMember,
                    ["preserved_primitive_entry_count"] = beforePrimitives.Count,
                    ["colour"] = colour ?? "",
                    ["colour_index"] = colourIndex,
                    ["transparency"] = transparency,
                    ["edges"] = edges,
                    ["visibility"] = visibility,
                    ["shallow"] = shallow,
                    ["add"] = add,
                    ["persistence"] = DrawListPersistenceResult("preview")
                };
            List<DbElement> originalOrder = OrderDrawListElementsByOwner(drawList.Members().Select(member => member.DbElement));
            var result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
            {
                ["dry_run"] = false,
                ["success"] = false,
                ["visual_scope"] = "exact top-level DrawListMember",
                ["readback_verified"] = false,
                ["all_members_verified"] = false,
                ["primitive_readback_verified"] = false,
                ["membership_verified"] = false,
                ["graphics_updated"] = false,
                ["refresh_attempted"] = false,
                ["refresh_attempt_count"] = 0,
                ["preparation_refresh_attempted"] = false,
                ["preparation_refresh_completed"] = false,
                ["preparation_verified"] = false,
                ["materialized_primitive_keys"] = new string[0],
                ["materialized_primitive_count"] = 0,
                ["no_change"] = false,
                ["reconciliation_attempted"] = false,
                ["reconciled_member_count"] = 0,
                ["refno"] = targetRef,
                ["type"] = ElementType(element),
                ["added"] = false,
                ["add_attempted"] = false,
                ["top_level_element"] = topLevelElement,
                ["exact_member_before"] = exactMember,
                ["write_attempted"] = false,
                ["partial_write"] = false,
                ["rollback_attempted"] = false,
                ["rollback_succeeded"] = null,
                ["rollback_verified_original"] = null,
                ["unsupported_transparency_state"] = false,
                ["transparency_reset_members"] = new List<string>(),
                ["before_member_count"] = before.Count,
                ["persistence"] = DrawListPersistenceResult("display_failed")
            };
            try
            {
                // A known exact member with the same five observations needs
                // neither a setter nor graphics refresh/compensation. Refresh
                // itself can alter native entries, so do not run it for a no-op.
                if (exactMember && !DrawListVisualRequestChanges(before[targetRef], colourIndex, transparency, edges, visibility, shallow))
                {
                    result["no_change"] = true;
                    result["success"] = true;
                    result["readback_verified"] = true;
                    result["all_members_verified"] = true;
                    result["primitive_readback_verified"] = true;
                    result["membership_verified"] = true;
                    result["verified_member_count"] = before.Count;
                    result["verified_primitive_entry_count"] = beforePrimitives.Count;
                    result["verification_errors"] = new Dictionary<string, string>();
                    result["primitive_verification_errors"] = new Dictionary<string, string>();
                    DrawListVisualEntry unchanged = before[targetRef];
                    result["colour"] = unchanged.colour;
                    result["transparency"] = unchanged.transparency;
                    result["edges"] = unchanged.edges;
                    result["visibility"] = unchanged.visibility;
                    result["shallow"] = unchanged.shallow;
                    result["persistence"] = SaveDrawListState(drawList);
                    return result;
                }
                Dictionary<string, DrawListPrimitiveVisualEntry> preparedPrimitives = beforePrimitives;
                if (exactMember)
                {
                    // Newly created geometry can already have an exact member,
                    // while its primitive entries are still pending generation.
                    // Observe that generation before visual setters. Preserve
                    // the entry snapshot for truthful failure/rollback receipts.
                    result["preparation_refresh_attempted"] = true;
                    RefreshDrawListVisual(result);
                    result["preparation_refresh_completed"] = true;
                    PrimitiveVisualMap prepared = PrimitiveVisualReadMap(drawList);
                    PrimitiveVisualRequireComplete(prepared);
                    string[] materialized = ValidateDrawListVisualPreparationScope(targetRef,
                        before, beforePrimitives, prepared.Members, prepared.Entries,
                        reference => DisplayStateRequireDatabaseSubtree(ModelRequireElement(reference), targetRef));
                    preparedPrimitives = prepared.Entries;
                    result["materialized_primitive_keys"] = materialized;
                    result["materialized_primitive_count"] = materialized.Length;
                    result["preparation_verified"] = true;
                }
                if (add && !before.ContainsKey(targetRef))
                {
                    result["write_attempted"] = true;
                    result["add_attempted"] = true;
                    // An Add exception or failed enumeration leaves membership
                    // unknown until a complete readback, not known unchanged.
                    result["added"] = null;
                    NativeWrite(() => drawList.Add(element));
                    RefreshDrawListVisual(result);
                }
                DrawListMember member = RequireExactDrawListMember(drawList, element);
                Dictionary<string, DrawListVisualEntry> expected = CaptureDrawListVisualSnapshot(drawList);
                Dictionary<string, DrawListPrimitiveVisualEntry> expectedPrimitives = CaptureDrawListPrimitiveSnapshot(drawList);
                if (Equals(result["add_attempted"], true))
                {
                    ValidateDrawListVisualAddScope(targetRef, before, beforePrimitives, expected, expectedPrimitives,
                        reference => DisplayStateRequireDatabaseSubtree(ModelRequireElement(reference), targetRef));
                    result["added"] = true;
                }
                else if (CompareDrawListVisualSnapshots(before, expected).Count != 0 ||
                    CompareDrawListPrimitiveSnapshots(preparedPrimitives, expectedPrimitives).Count != 0)
                    throw new InvalidOperationException("Display state changed after preparation and before the member update; no further entries are accepted.");
                foreach (KeyValuePair<string, DrawListPrimitiveVisualEntry> saved in beforePrimitives)
                {
                    if (!expectedPrimitives.ContainsKey(saved.Key))
                        throw new InvalidOperationException("Add removed a previously observed primitive entry: " + saved.Key);
                    expectedPrimitives[saved.Key] = saved.Value;
                }
                List<DbElement> applicationOrder = Equals(result["added"], true)
                    ? OrderDrawListElementsByOwner(drawList.Members().Select(candidate => candidate.DbElement))
                    : originalOrder;
                // Add may establish extra memberships. Preserve every pre-existing
                // member's visual state, including any state that Add changed.
                foreach (KeyValuePair<string, DrawListVisualEntry> saved in before)
                    expected[saved.Key] = CloneDrawListVisual(saved.Value);
                DrawListVisualEntry wanted = expected[targetRef];
                if (colourIndex.HasValue) wanted.colour = colourIndex.Value;
                if (transparency.HasValue) wanted.transparency = transparency.Value;
                if (edges.HasValue) wanted.edges = edges.Value;
                if (visibility.HasValue) wanted.visibility = visibility.Value;
                if (shallow.HasValue) wanted.shallow = shallow.Value;
                PrimitiveVisualPreflightTransparency(drawList, PrimitiveVisualReadMap(drawList), expectedPrimitives, expected);

                // Public member setters may also propagate visual changes to
                // descendants. Reconcile other members once, in owner order.
                if (colourIndex.HasValue && NativeRead(() => member.Colour) != colourIndex.Value) { result["write_attempted"] = true; NativeWrite(() => member.Colour = colourIndex.Value); }
                if (transparency.HasValue && NativeRead(() => member.Transparency) != transparency.Value) { result["write_attempted"] = true; NativeWrite(() => member.Transparency = transparency.Value); }
                if (edges.HasValue && NativeRead(() => member.Edges) != edges.Value) { result["write_attempted"] = true; NativeWrite(() => member.Edges = edges.Value); }
                if (visibility.HasValue && NativeRead(() => member.Visibility) != visibility.Value) { result["write_attempted"] = true; NativeWrite(() => member.Visibility = visibility.Value); }
                if (shallow.HasValue && NativeRead(() => member.ShallowRendering) != shallow.Value) { result["write_attempted"] = true; NativeWrite(() => member.ShallowRendering = shallow.Value); }

                RefreshDrawListVisual(result);
                Dictionary<string, DrawListVisualEntry> after = CaptureDrawListVisualSnapshot(drawList);
                Dictionary<string, string> differences = CompareDrawListVisualSnapshots(expected, after);
                result["verification_errors"] = differences;
                if (differences.Count != 0 && expected.Count == after.Count && expected.Keys.All(after.ContainsKey) &&
                    !differences.ContainsKey(targetRef))
                {
                    result["reconciliation_attempted"] = true;
                    // Visit all non-target members, not only the initial diff:
                    // repairing an ancestor can change an initially correct child.
                    foreach (DbElement candidate in applicationOrder)
                    {
                        string candidateRef = ElementRef(candidate);
                        if (candidateRef == targetRef) continue;
                        if (ApplyExactDrawListVisual(drawList, candidate, expected[candidateRef]))
                            result["reconciled_member_count"] = (int)result["reconciled_member_count"] + 1;
                    }
                    RefreshDrawListVisual(result);
                    after = CaptureDrawListVisualSnapshot(drawList);
                    differences = CompareDrawListVisualSnapshots(expected, after);
                    result["verification_errors"] = differences;
                }
                if (differences.Count != 0)
                    throw new InvalidOperationException("Drawlist visual update changed unexpected member state or failed requested readback.");
                // Member setters may also affect existing graphical primitives.
                // Preserve their five observed native values; this does not claim
                // to inspect the SDK's hidden override/inheritance metadata.
                ApplyDrawListPrimitiveSnapshot(drawList, expectedPrimitives, true, expected,
                    field => result["write_attempted"] = true, (List<string>)result["transparency_reset_members"]);
                RefreshDrawListVisual(result);
                Dictionary<string, string> primitiveDifferences = CompareDrawListPrimitiveSnapshots(
                    expectedPrimitives, CaptureDrawListPrimitiveSnapshot(drawList));
                result["primitive_verification_errors"] = primitiveDifferences;
                after = CaptureDrawListVisualSnapshot(drawList);
                differences = CompareDrawListVisualSnapshots(expected, after);
                result["verification_errors"] = differences;
                if (differences.Count != 0 || primitiveDifferences.Count != 0)
                    throw new InvalidOperationException("Final member and primitive observed visuals did not match the complete requested state.");
                DrawListVisualEntry verified = after[targetRef];
                result["success"] = true;
                result["readback_verified"] = true;
                result["all_members_verified"] = true;
                result["primitive_readback_verified"] = true;
                result["verified_primitive_entry_count"] = expectedPrimitives.Count;
                result["membership_verified"] = true;
                result["verified_member_count"] = after.Count;
                result["colour"] = verified.colour;
                result["transparency"] = verified.transparency;
                result["edges"] = verified.edges;
                result["visibility"] = verified.visibility;
                result["shallow"] = verified.shallow;
            }
            catch (Exception error)
            {
                ThrowIfNativeFaulted();
                result["error"] = error.Message;
                result["unsupported_transparency_state"] = error is UnsupportedTransparencyStateException;
                // A rejected preparation has made no visual setter/Add calls.
                // Do not overwrite changes discovered by that readback merely
                // because a graphics refresh ran. Keep complete residuals.
                if (Equals(result["write_attempted"], true))
                    RollbackDrawListVisualSnapshot(drawList, before, beforePrimitives, originalOrder, result);
                CaptureDrawListVisualResidual(drawList, before, beforePrimitives, result);
            }
            // Saving is outside the display transaction: a persistence failure
            // must neither replay the update nor roll back verified visuals.
            if (Equals(result["success"], true))
                result["persistence"] = SaveDrawListState(drawList);
            return result;
        });
    }

    private bool IsConnected()
    {
        if ((nativeFaultState != null && nativeFaultState.HasFault) || IsGeneratedCsgQuarantined()) return false;
        MDB observed = CurrentMdb();
        // The connection getter can itself latch a native fault. Inspect only
        // managed state afterwards; SDK equality operators are not null tests.
        return (nativeFaultState == null || !nativeFaultState.HasFault) && !ReferenceEquals(observed, null);
    }

    private bool TryBeginCommand(string command)
    {
        if (nativeFaultState != null && nativeFaultState.HasFault && !NativeDatabaseFaultState.IsExactRecoveryCommand(command))
            throw new NativeDatabaseFaultException();
        lock (commandLock)
        {
            if (commandActive || NativeDispatchBusy())
                return false;
            commandActive = true;
            activeCommand = command ?? "";
            return true;
        }
    }

    private bool IsCommandActive()
    {
        if (nativeFaultState != null && nativeFaultState.HasFault)
            throw new NativeDatabaseFaultException();
        // Read endpoints recheck after UI dispatch. The gate belongs to this
        // very callback then; it must only reject other queued/running work.
        if (currentNativeFaultState != null && ReferenceEquals(currentNativeFaultState, nativeFaultState))
        {
            lock (commandLock) return commandActive;
        }
        return BridgeOperationBusy();
    }

    private bool BridgeOperationBusy()
    {
        lock (commandLock)
            return commandActive || NativeDispatchBusy();
    }

    private Dictionary<string, object> ExecutionDiagnostics()
    {
        if (executionState != null)
        {
            try { return executionState(); }
            catch { }
        }
        return new Dictionary<string, object>
        {
            ["native_operation_active"] = false,
            ["dispatcher_ready"] = false,
            ["diagnostics_unavailable"] = true
        };
    }

    private bool NativeDispatchBusy()
    {
        var state = ExecutionDiagnostics();
        return state.ContainsKey("native_operation_active") && Equals(state["native_operation_active"], true);
    }

    private static void ThrowIfNativeFaulted()
    {
        ThrowIfGeneratedCsgQuarantined();
        NativeDatabaseFaultState state = currentNativeFaultState;
        if (state != null && state.HasFault && state.Generation > permittedNativeFaultGeneration)
            throw new NativeDatabaseFaultException();
    }

    private static T NativeRead<T>(Func<T> operation)
    {
        ThrowIfNativeFaulted();
        try { return operation(); }
        finally { ThrowIfNativeFaulted(); }
    }

    private static void NativeWrite(Action operation)
    {
        ThrowIfNativeFaulted();
        try { operation(); }
        finally { ThrowIfNativeFaulted(); }
    }

    private static Dictionary<string, object> BusyResult(string command)
    {
        return new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
        {
            ["success"] = false,
            ["busy"] = true,
            ["command"] = command ?? "",
            ["error"] = "Another E3D command is still running or is waiting on a dialog."
        };
    }

    private void EndCommand()
    {
        lock (commandLock)
        {
            commandActive = false;
            activeCommand = "";
        }
    }

    private static MDB CurrentMdb()
    {
        ThrowIfGeneratedCsgQuarantined();
        try
        {
            return MDB.CurrentMDB;
        }
        catch
        {
            return null;
        }
    }

    private static DrawList CurrentDrawList()
    {
        ThrowIfNativeFaulted();
        try
        {
            DrawListManager manager = DrawListManager.Instance;
            return manager == null ? null : NativeRead(() => manager.CurrentDrawList);
        }
        catch
        {
            ThrowIfNativeFaulted();
            return null;
        }
    }

    private static object CurrentView3D()
    {
        ThrowIfNativeFaulted();
        try
        {
            Type managerType = typeof(View3DManager);
            PropertyInfo property = managerType.GetProperty("CurrentView", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
            if (property != null)
                return NativeRead(() => property.GetValue(null, null));

            FieldInfo field = managerType.GetField("Instance", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
            object manager = field == null ? null : field.GetValue(null);
            if (manager == null)
                return null;
            property = manager.GetType().GetProperty("CurrentView", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            return property == null ? null : NativeRead(() => property.GetValue(manager, null));
        }
        catch
        {
            return null;
        }
    }

    private static double[] InvokeDoubleArray(object target, string methodName)
    {
        if (target == null)
            return null;
        MethodInfo method = target.GetType().GetMethod(methodName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
        if (method == null)
            return null;
        return NativeRead(() => method.Invoke(target, null)) as double[];
    }

    private static void InvokeViewMethod(object target, string methodName, params object[] arguments)
    {
        if (target == null)
            throw new InvalidOperationException("E3D view is unavailable.");
        MethodInfo method = target.GetType().GetMethod(methodName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
        if (method == null)
            throw new InvalidOperationException("E3D view method was not found: " + methodName);
        NativeWrite(() => method.Invoke(target, arguments));
    }

    private static void ApplyViewFit(object view, double[] limits, bool shaded, bool isometric)
    {
        if (view == null || limits == null || limits.Length < 6)
            throw new InvalidOperationException("E3D did not return valid Drawlist limits.");
        double[] centre =
        {
            (limits[0] + limits[1]) / 2.0,
            (limits[2] + limits[3]) / 2.0,
            (limits[4] + limits[5]) / 2.0
        };
        double extent = Math.Max(Math.Abs(limits[1] - limits[0]),
            Math.Max(Math.Abs(limits[3] - limits[2]), Math.Abs(limits[5] - limits[4])));
        if (extent < 1.0)
            extent = 1000.0;

        double[] direction = isometric
            ? new[] { 1.0, 1.0, -1.0 }
            : InvokeDoubleArray(view, "GetDirection");
        if (direction == null || direction.Length < 3)
            direction = new[] { 1.0, 1.0, -1.0 };

        InvokeViewMethod(view, "SetCentreAndDir", centre, direction);
        InvokeViewMethod(view, "SetRadius", extent * 1.35);
        if (shaded)
            InvokeViewMethod(view, "SetShadeModeNoMapUpdate", true);
        InvokeViewMethod(view, "MapView");
    }

    private static bool IsCancellationCommand(string command)
    {
        string token = (command ?? "").Trim();
        int separator = token.IndexOfAny(new[] { ' ', '\t', '\r', '\n' });
        if (separator >= 0)
            token = token.Substring(0, separator);
        token = token.Trim().TrimStart('^').ToUpperInvariant();
        return token == "CANCEL" || token == "ABORT" || token == "ESC" || token == "ESCAPE";
    }

    private static DocEngine CurrentInteractionEngine()
    {
        try
        {
            FieldInfo subscribersField = typeof(View3DManager).GetField(
                "_subscribers", BindingFlags.Static | BindingFlags.NonPublic);
            IEnumerable subscribers = subscribersField == null ? null : subscribersField.GetValue(null) as IEnumerable;
            if (subscribers == null)
                return null;

            foreach (object subscriber in subscribers)
            {
                if (subscriber == null)
                    continue;
                object viewManager = PrivateField(subscriber, "view3dManager");
                object starCadObject = PrivateField(viewManager, "sc");
                StarCADUI starCad = starCadObject as StarCADUI;
                if (starCad == null || starCad.Documents == null)
                    continue;

                Document current = starCad.Documents.Current;
                if (current != null && current.Engine != null)
                    return current.Engine;

                foreach (Document document in starCad.Documents)
                {
                    if (document != null && document.IsActive && document.Engine != null)
                        return document.Engine;
                }
            }
        }
        catch
        {
        }
        return null;
    }

    private static object PrivateField(object target, string name)
    {
        if (target == null)
            return null;
        FieldInfo field = target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic);
        return field == null ? null : field.GetValue(target);
    }

    private static int? ResolveColourIndex(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
            return null;
        // E3D supplies the available indices and names. Do not invent a numeric
        // range or treat an unknown colour as an omitted optional argument.
        return ResolveDrawListColour(value, Colours.ColourTable());
    }

    private static int? ResolveDrawListColour(string value, Colour[] table)
    {
        if (string.IsNullOrWhiteSpace(value)) return null;
        if (table == null || table.Length == 0)
            throw new InvalidOperationException("The native E3D colour table is unavailable; colour could not be validated.");
        string wanted = value.Trim();
        int numeric;
        bool byIndex = int.TryParse(wanted, NumberStyles.Integer, CultureInfo.InvariantCulture, out numeric);
        if (!byIndex && wanted.StartsWith("colour_", StringComparison.OrdinalIgnoreCase))
        {
            if (!int.TryParse(wanted.Substring(7), NumberStyles.Integer, CultureInfo.InvariantCulture, out numeric))
                throw new ArgumentException("Invalid native E3D colour index: " + value, "colour");
            byIndex = true;
        }
        foreach (Colour candidate in table)
            if (candidate != null && (byIndex ? candidate.Index == numeric :
                string.Equals(candidate.Name, wanted, StringComparison.OrdinalIgnoreCase)))
                return candidate.Index;
        throw new ArgumentException("Unknown native E3D colour name or index: " + value, "colour");
    }

    private static DbElement CurrentElement()
    {
        try
        {
            return NativeRead(() => Aveva.Core.Database.CurrentElement.Element);
        }
        catch
        {
            ThrowIfNativeFaulted();
            return null;
        }
    }

    private static void CollectMatches(DbElement root, string wanted, int limit, List<DbElement> matches, bool matchType)
    {
        CollectMatches(root, wanted, limit, matches, matchType, 10000, 1500);
    }

    private static void CollectMatches(DbElement root, string wanted, int limit, List<DbElement> matches, bool matchType, int maxVisited, int maxMilliseconds)
    {
        if (!IsUsable(root) || matches.Count >= limit)
            return;

        Queue<Tuple<DbElement, int>> queue = new Queue<Tuple<DbElement, int>>();
        HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        List<DbElement> typeMatches = new List<DbElement>();
        List<DbElement> nameMatches = new List<DbElement>();
        Stopwatch timer = Stopwatch.StartNew();
        int visited = 0;
        queue.Enqueue(Tuple.Create(root, 0));
        string token = NormalizeRef(wanted);
        while (queue.Count > 0 && visited < maxVisited && timer.ElapsedMilliseconds < maxMilliseconds &&
            (matchType ? typeMatches.Count < limit : matches.Count < limit))
        {
            Tuple<DbElement, int> item = queue.Dequeue();
            visited++;
            DbElement element = item.Item1;
            if (!IsUsable(element) || item.Item2 > 64)
                continue;
            string key = ElementRef(element);
            if (!string.IsNullOrEmpty(key) && !seen.Add(key))
                continue;

            string type = ElementType(element);
            string name = ElementName(element);
            if (matchType)
            {
                if (TypeMatches(type, token))
                    AddIfUsable(typeMatches, element, limit);
                else if (string.Equals(name, token, StringComparison.OrdinalIgnoreCase) || name.IndexOf(token, StringComparison.OrdinalIgnoreCase) >= 0)
                    AddIfUsable(nameMatches, element, limit);
            }
            else if (string.Equals(NormalizeRef(key), token, StringComparison.OrdinalIgnoreCase))
            {
                AddIfUsable(matches, element, limit);
            }

            try
            {
                foreach (DbElement child in element.Members())
                    if (IsUsable(child))
                        queue.Enqueue(Tuple.Create(child, item.Item2 + 1));
            }
            catch
            {
            }
        }

        if (matchType)
        {
            List<DbElement> preferred = typeMatches.Count > 0 ? typeMatches : nameMatches;
            foreach (DbElement element in preferred)
            {
                if (matches.Count >= limit)
                    break;
                AddIfUsable(matches, element, limit);
            }
        }
    }

    private static bool TypeMatches(string type, string token)
    {
        if (string.Equals(type, token, StringComparison.OrdinalIgnoreCase))
            return true;
        switch (token.ToUpperInvariant())
        {
            case "EQUIPMENT":
                return string.Equals(type, "EQUI", StringComparison.OrdinalIgnoreCase);
            case "SUBEQUIPMENT":
                return string.Equals(type, "SUBE", StringComparison.OrdinalIgnoreCase);
            case "CYLINDER":
                return string.Equals(type, "CYLI", StringComparison.OrdinalIgnoreCase);
            case "NOZZLE":
                return string.Equals(type, "NOZZ", StringComparison.OrdinalIgnoreCase);
            case "BRANCH":
                return string.Equals(type, "BRAN", StringComparison.OrdinalIgnoreCase);
            default:
                return false;
        }
    }

    private static void AddIfUsable(List<DbElement> values, DbElement element, int limit)
    {
        if (!IsUsable(element) || values.Count >= limit)
            return;
        string key = ElementRef(element);
        if (values.Any(value => string.Equals(ElementRef(value), key, StringComparison.OrdinalIgnoreCase)))
            return;
        values.Add(element);
    }

    private static Dictionary<string, object> ToElement(DbElement element)
    {
        return ElementInfo(element);
    }

    private static Dictionary<string, object> ElementInfo(DbElement element)
    {
        return new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
        {
            ["refno"] = ElementRef(element),
            ["type"] = ElementType(element),
            ["name"] = ElementName(element),
            ["desc"] = SafeFormattedAttribute(element, DbAttributeInstance.DESC),
            ["pos"] = SafeFormattedAttribute(element, DbAttributeInstance.POS),
            ["ori"] = SafeFormattedAttribute(element, DbAttributeInstance.ORI),
            ["diam"] = SafeFormattedAttribute(element, DbAttributeInstance.DIAM),
            ["heig"] = SafeFormattedAttribute(element, DbAttributeInstance.HEIG),
            ["xlen"] = SafeFormattedAttribute(element, DbAttributeInstance.XLEN),
            ["ylen"] = SafeFormattedAttribute(element, DbAttributeInstance.YLEN),
            ["zlen"] = SafeFormattedAttribute(element, DbAttributeInstance.ZLEN),
            ["dtop"] = SafeFormattedAttribute(element, DbAttributeInstance.DTOP),
            ["dbot"] = SafeFormattedAttribute(element, DbAttributeInstance.DBOT),
            ["db_no"] = SafeInt(delegate { return element.DbNo(); })
        };
    }

    private static string DrawListStatePath()
    {
        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Aveva", "E3D-MCP", "E3D21.drawlist");
    }

    private sealed class DrawListVisualEntry
    {
        [JsonProperty(Required = Required.Always)]
        public string refno;
        [JsonProperty(Required = Required.Always)]
        public int colour;
        [JsonProperty(Required = Required.Always)]
        public int transparency;
        [JsonProperty(Required = Required.Always)]
        public bool edges;
        [JsonProperty(Required = Required.Always)]
        public bool visibility;
        [JsonProperty(Required = Required.Always)]
        public bool shallow;
    }

    private sealed class DrawListVisualDocument
    {
        [JsonProperty(Required = Required.Always)]
        public int schema_version = 1;
        [JsonProperty(Required = Required.Always)]
        public string project;
        [JsonProperty(Required = Required.Always)]
        public string mdb;
        [JsonProperty(Required = Required.Always)]
        public List<DrawListVisualEntry> members = new List<DrawListVisualEntry>();
        // Required only for schema 2. A schema-1 document remains explicitly
        // top-level-only and must not silently claim to include primitives.
        public List<DrawListPrimitiveVisualEntry> primitive_entries;
    }

    private static DrawListMember RequireExactDrawListMember(DrawList drawList, DbElement element)
    {
        ThrowIfNativeFaulted();
        if (drawList == null || !IsUsable(element))
            throw new InvalidOperationException("A usable Drawlist and database element are required.");
        string wanted = ElementRef(element);
        DrawListMember match = null;
        foreach (DrawListMember member in NativeRead(() => drawList.Members()))
        {
            if (member == null || !IsUsable(member.DbElement) || !string.Equals(ElementRef(member.DbElement), wanted, StringComparison.Ordinal))
                continue;
            if (match != null) throw new InvalidOperationException("The Drawlist contains duplicate exact member references: " + wanted);
            match = member;
        }
        if (match == null)
            throw new InvalidOperationException("The element is not an exact top-level Drawlist member: " + wanted + ". Inspect e3d_display_state to distinguish a top-level member from a primitive rendered under another member; repeating Add does not guarantee independent membership.");
        return match;
    }

    private static DrawListVisualEntry CaptureDrawListVisual(DrawListMember member)
    {
        ThrowIfNativeFaulted();
        if (member == null || !IsUsable(member.DbElement))
            throw new InvalidOperationException("Drawlist visual member could not be read.");
        var entry = new DrawListVisualEntry
        {
            refno = ElementRef(NativeRead(() => member.DbElement)), colour = NativeRead(() => member.Colour), transparency = NativeRead(() => member.Transparency),
            edges = NativeRead(() => member.Edges), visibility = NativeRead(() => member.Visibility), shallow = NativeRead(() => member.ShallowRendering)
        };
        if (string.IsNullOrWhiteSpace(entry.refno) || entry.transparency < 0 || entry.transparency > 100)
            throw new InvalidOperationException("A complete valid Drawlist visual state could not be captured.");
        return entry;
    }

    private static bool ApplyExactDrawListVisual(DrawList drawList, DbElement element, DrawListVisualEntry entry, Action attempted = null)
    {
        ThrowIfNativeFaulted();
        DrawListMember member = RequireExactDrawListMember(drawList, element);
        // Both DrawList.SetX array overloads go through native DrawList setters.
        // The public member properties instead call DES_DrawListMember.setX.
        // These can still affect descendants, so callers restore in owner order.
        bool changed = false;
        if (NativeRead(() => member.Colour) != entry.colour) { if (attempted != null) attempted(); changed = true; NativeWrite(() => member.Colour = entry.colour); }
        if (NativeRead(() => member.Transparency) != entry.transparency) { if (attempted != null) attempted(); changed = true; NativeWrite(() => member.Transparency = entry.transparency); }
        if (NativeRead(() => member.Edges) != entry.edges) { if (attempted != null) attempted(); changed = true; NativeWrite(() => member.Edges = entry.edges); }
        if (NativeRead(() => member.Visibility) != entry.visibility) { if (attempted != null) attempted(); changed = true; NativeWrite(() => member.Visibility = entry.visibility); }
        if (NativeRead(() => member.ShallowRendering) != entry.shallow) { if (attempted != null) attempted(); changed = true; NativeWrite(() => member.ShallowRendering = entry.shallow); }
        VerifyExactDrawListVisual(drawList, element, entry);
        return changed;
    }

    private static List<DbElement> OrderDrawListElementsByOwner(IEnumerable<DbElement> elements)
    {
        ThrowIfNativeFaulted();
        var ordered = new List<Tuple<int, string, DbElement>>();
        foreach (DbElement element in elements)
        {
            string reference = ElementRef(element);
            if (string.IsNullOrWhiteSpace(reference))
                throw new InvalidOperationException("Drawlist member identity is unreadable while ordering visual updates.");
            var ancestors = new HashSet<string>(StringComparer.Ordinal);
            DbElement current = element;
            int depth;
            for (depth = 0; depth < 256; depth++)
            {
                if (current == null || current.IsNull || !current.IsValid)
                    throw new InvalidOperationException("Drawlist member owner hierarchy is unreadable: " + reference);
                string currentRef = ElementRef(current);
                if (string.IsNullOrWhiteSpace(currentRef) || !ancestors.Add(currentRef))
                    throw new InvalidOperationException("Drawlist member owner hierarchy has an unreadable identity or cycle: " + reference);
                // Design WORLD is the stable root; its Owner may be a system
                // placeholder and must not be followed.
                if (NativeRead(() => current.GetElementType()).IsWorld) break;
                current = NativeRead(() => current.Owner);
            }
            if (depth == 256)
                throw new InvalidOperationException("Drawlist member owner hierarchy exceeds 256 levels: " + reference);
            ordered.Add(Tuple.Create(depth, reference, element));
        }
        return ordered.OrderBy(item => item.Item1).ThenBy(item => item.Item2, StringComparer.Ordinal)
            .Select(item => item.Item3).ToList();
    }

    private static DrawListVisualEntry CloneDrawListVisual(DrawListVisualEntry entry)
    {
        return new DrawListVisualEntry
        {
            refno = entry.refno, colour = entry.colour, transparency = entry.transparency,
            edges = entry.edges, visibility = entry.visibility, shallow = entry.shallow
        };
    }

    private static bool DrawListVisualRequestChanges(DrawListVisualEntry entry, int? colour, int? transparency,
        bool? edges, bool? visibility, bool? shallow)
    {
        if (entry == null) throw new ArgumentNullException("entry");
        return colour.HasValue && entry.colour != colour.Value ||
            transparency.HasValue && entry.transparency != transparency.Value ||
            edges.HasValue && entry.edges != edges.Value ||
            visibility.HasValue && entry.visibility != visibility.Value ||
            shallow.HasValue && entry.shallow != shallow.Value;
    }

    private static void AttemptDrawListVisualRefresh(IDictionary<string, object> result, Action refresh)
    {
        ThrowIfNativeFaulted();
        result["refresh_attempted"] = true;
        object count;
        result["refresh_attempt_count"] = result.TryGetValue("refresh_attempt_count", out count)
            ? Convert.ToInt32(count, CultureInfo.InvariantCulture) + 1 : 1;
        result["graphics_updated"] = false;
        NativeWrite(refresh);
        result["graphics_updated"] = true;
    }

    private static void RefreshDrawListVisual(IDictionary<string, object> result)
    {
        ThrowIfNativeFaulted();
        DrawListManager manager = NativeRead(() => DrawListManager.Instance);
        if (manager == null) throw new InvalidOperationException("The Drawlist manager is unavailable for graphics refresh.");
        AttemptDrawListVisualRefresh(result, () => manager.UpdateGraphics());
    }

    private static void ValidateDrawListVisualAddScope(string targetRef,
        IDictionary<string, DrawListVisualEntry> before, IDictionary<string, DrawListPrimitiveVisualEntry> beforePrimitives,
        IDictionary<string, DrawListVisualEntry> after, IDictionary<string, DrawListPrimitiveVisualEntry> afterPrimitives,
        Action<string> requireTargetSubtree)
    {
        if (!after.ContainsKey(targetRef)) throw new InvalidOperationException("Add did not establish the exact requested member.");
        foreach (string reference in before.Keys)
            if (!after.ContainsKey(reference)) throw new InvalidOperationException("Add removed an existing exact member: " + reference);
        foreach (string key in beforePrimitives.Keys)
            if (!afterPrimitives.ContainsKey(key)) throw new InvalidOperationException("Add removed an existing primitive entry: " + key);
        foreach (string reference in after.Keys)
            if (!before.ContainsKey(reference)) requireTargetSubtree(reference);
        foreach (KeyValuePair<string, DrawListPrimitiveVisualEntry> current in afterPrimitives)
        {
            DrawListPrimitiveVisualEntry entry = current.Value;
            if (current.Key != entry.owner_refno + "|" + entry.refno || !after.ContainsKey(entry.owner_refno))
                throw new InvalidOperationException("Added primitive identity/owner is inconsistent: " + current.Key);
            if (!beforePrimitives.ContainsKey(current.Key))
            {
                // A new alias may use a pre-existing ancestor owner, but its
                // database target must be inside this explicitly added subtree.
                requireTargetSubtree(entry.refno);
                if (!before.ContainsKey(entry.owner_refno)) requireTargetSubtree(entry.owner_refno);
            }
        }
    }

    private static string[] ValidateDrawListVisualPreparationScope(string targetRef,
        IDictionary<string, DrawListVisualEntry> before, IDictionary<string, DrawListPrimitiveVisualEntry> beforePrimitives,
        IDictionary<string, DrawListVisualEntry> after, IDictionary<string, DrawListPrimitiveVisualEntry> afterPrimitives,
        Action<string> requireTargetSubtree)
    {
        if (!before.ContainsKey(targetRef) || CompareDrawListVisualSnapshots(before, after).Count != 0)
            throw new InvalidOperationException("Graphics preparation changed exact member membership or observed values; no visual setters were attempted.");
        foreach (var original in beforePrimitives)
        {
            DrawListPrimitiveVisualEntry current;
            if (!afterPrimitives.TryGetValue(original.Key, out current) || !PrimitiveVisualValuesEqual(original.Value, current))
                throw new InvalidOperationException("Graphics preparation removed or changed an existing primitive: " + original.Key);
        }
        // The complete native map has already verified alias consistency and
        // each primitive's database ownership. Also bind keys and scope here;
        // a new entry elsewhere is not attributed to this requested operation.
        var added = new List<string>();
        foreach (var current in afterPrimitives)
        {
            DrawListPrimitiveVisualEntry entry = current.Value;
            if (entry == null || current.Key != entry.owner_refno + "|" + entry.refno || !after.ContainsKey(entry.owner_refno))
                throw new InvalidOperationException("Prepared primitive identity/owner is inconsistent: " + current.Key);
            if (beforePrimitives.ContainsKey(current.Key)) continue;
            requireTargetSubtree(entry.refno);
            added.Add(current.Key);
        }
        return added.OrderBy(key => key, StringComparer.Ordinal).ToArray();
    }

    private static void CaptureDrawListVisualResidual(DrawList drawList,
        IDictionary<string, DrawListVisualEntry> before, IDictionary<string, DrawListPrimitiveVisualEntry> beforePrimitives,
        IDictionary<string, object> result)
    {
        ThrowIfNativeFaulted();
        result["residual_complete"] = false;
        result["display_changed"] = null;
        try
        {
            // Preserve conflicting aliases as observed residuals. A failure
            // diagnostic must not discard both maps because one alias differs.
            PrimitiveVisualMap residual = PrimitiveVisualReadMap(drawList, true, false);
            result["residual_members"] = residual.Members.Values.ToArray();
            result["residual_primitive_entries"] = residual.Entries.Values.ToArray();
            result["residual_read_errors"] = residual.Errors.ToArray();
            result["residual_complete"] = residual.Errors.Count == 0;
            if (residual.Errors.Count == 0 && before != null && beforePrimitives != null)
            {
                var memberDifferences = CompareDrawListVisualSnapshots(before, residual.Members);
                var primitiveDifferences = CompareDrawListPrimitiveSnapshots(beforePrimitives, residual.Entries);
                result["residual_member_differences"] = memberDifferences;
                result["residual_primitive_differences"] = primitiveDifferences;
                result["display_changed"] = memberDifferences.Count != 0 || primitiveDifferences.Count != 0;
            }
            result["outcome_uncertain"] = residual.Errors.Count != 0;
        }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            result["residual_read_errors"] = new[] { error.Message };
            result["outcome_uncertain"] = true;
        }
    }

    private static Dictionary<string, DrawListVisualEntry> CaptureDrawListVisualSnapshot(DrawList drawList)
    {
        ThrowIfNativeFaulted();
        var snapshot = new Dictionary<string, DrawListVisualEntry>(StringComparer.Ordinal);
        DrawListMember[] members = NativeRead(() => drawList.Members());
        if (members == null || members.Length > DisplayStateMemberLimit)
            throw new InvalidOperationException("A complete Drawlist member collection within the 10000-entry limit is required.");
        foreach (DrawListMember member in members)
        {
            DrawListVisualEntry entry = CaptureDrawListVisual(member);
            if (snapshot.ContainsKey(entry.refno))
                throw new InvalidOperationException("The Drawlist contains duplicate exact member references: " + entry.refno);
            snapshot.Add(entry.refno, entry);
        }
        return snapshot;
    }

    private static Dictionary<string, string> CompareDrawListVisualSnapshots(
        IDictionary<string, DrawListVisualEntry> expected, IDictionary<string, DrawListVisualEntry> actual)
    {
        var differences = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (KeyValuePair<string, DrawListVisualEntry> saved in expected)
        {
            DrawListVisualEntry current;
            if (!actual.TryGetValue(saved.Key, out current))
            {
                differences[saved.Key] = "Exact member is missing.";
                continue;
            }
            DrawListVisualEntry wanted = saved.Value;
            var fields = new List<string>();
            if (wanted.colour != current.colour) fields.Add("colour");
            if (wanted.transparency != current.transparency) fields.Add("transparency");
            if (wanted.edges != current.edges) fields.Add("edges");
            if (wanted.visibility != current.visibility) fields.Add("visibility");
            if (wanted.shallow != current.shallow) fields.Add("shallow");
            if (fields.Count > 0) differences[saved.Key] = "Visual fields differ: " + string.Join(", ", fields);
        }
        foreach (string refno in actual.Keys)
            if (!expected.ContainsKey(refno)) differences[refno] = "Unexpected exact member is present.";
        return differences;
    }

    private static void RollbackDrawListVisualSnapshot(DrawList drawList,
        Dictionary<string, DrawListVisualEntry> before,
        Dictionary<string, DrawListPrimitiveVisualEntry> beforePrimitives,
        IList<DbElement> originalOrder, Dictionary<string, object> result)
    {
        ThrowIfNativeFaulted();
        result["rollback_attempted"] = true;
        var errors = new Dictionary<string, string>(StringComparer.Ordinal);
        Dictionary<string, DrawListPrimitiveVisualEntry> compensationPrimitives = null;
        Dictionary<string, DrawListVisualEntry> compensationMembers = null;
        // Only public member properties are used for compensation. Memberships
        // newly added by this operation are reported as residual, not removed.
        try
        {
            PrimitiveVisualMap current = PrimitiveVisualReadMap(drawList, true, false);
            PrimitiveVisualRequireComplete(current);
            compensationPrimitives = current.Entries.ToDictionary(item => item.Key, item => PrimitiveVisualClone(item.Value), StringComparer.Ordinal);
            compensationMembers = current.Members.ToDictionary(item => item.Key, item => CloneDrawListVisual(item.Value), StringComparer.Ordinal);
            if (before.Keys.Any(reference => !current.Members.ContainsKey(reference)))
                throw new InvalidOperationException("An original member is missing before visual compensation; no member restoration was attempted.");
            foreach (var primitive in beforePrimitives) compensationPrimitives[primitive.Key] = primitive.Value;
            foreach (var member in before) compensationMembers[member.Key] = member.Value;
            PrimitiveVisualPreflightTransparency(drawList, current, compensationPrimitives, compensationMembers);
            var members = new Dictionary<string, DrawListMember>(StringComparer.Ordinal);
            foreach (DrawListMember member in NativeRead(() => drawList.Members()))
                if (member != null && IsUsable(member.DbElement)) members.Add(ElementRef(member.DbElement), member);
            // Extra members were captured before the first compensation write.
            // Reapply their saved values too if an ancestor setter propagates.
            foreach (DbElement original in OrderDrawListElementsByOwner(members.Values.Select(member => member.DbElement)))
            {
                string originalRef = ElementRef(original);
                DrawListMember member;
                if (!members.TryGetValue(originalRef, out member))
                {
                    errors[originalRef] = "Original exact member is missing during rollback.";
                    continue;
                }
                try { ApplyExactDrawListVisual(drawList, member.DbElement, compensationMembers[originalRef]); }
                catch (Exception error) { ThrowIfNativeFaulted(); errors[originalRef] = error.Message; }
            }
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["rollback"] = error.Message; }
        try
        {
            if (compensationPrimitives != null && !errors.ContainsKey("rollback"))
                ApplyDrawListPrimitiveSnapshot(drawList, compensationPrimitives, false, compensationMembers);
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["rollback_primitives"] = error.Message; }
        try
        {
            result["rollback_refresh_attempted"] = true;
            RefreshDrawListVisual(result);
            result["rollback_graphics_updated"] = true;
        }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            result["rollback_graphics_updated"] = false;
            errors["rollback_graphics"] = error.Message;
        }
        bool verified = false;
        try
        {
            Dictionary<string, DrawListVisualEntry> residual = CaptureDrawListVisualSnapshot(drawList);
            Dictionary<string, string> differences = CompareDrawListVisualSnapshots(before, residual);
            Dictionary<string, string> primitiveDifferences = CompareDrawListPrimitiveSnapshots(
                beforePrimitives, CaptureDrawListPrimitiveSnapshot(drawList));
            result["actual_primitive_residual"] = primitiveDifferences;
            verified = differences.Count == 0 && primitiveDifferences.Count == 0;
            result["actual_residual"] = differences.Keys.Select(refno => new Dictionary<string, object>
            {
                ["refno"] = refno,
                ["difference"] = differences[refno],
                ["visual"] = residual.ContainsKey(refno) ? residual[refno] : null
            }).ToArray();
        }
        catch (Exception error) { ThrowIfNativeFaulted(); errors["rollback_verification"] = error.Message; }
        result["rollback_errors"] = errors;
        result["rollback_verified_original"] = verified;
        result["rollback_succeeded"] = verified && errors.Count == 0;
        result["partial_write"] = !verified;
        result["outcome_uncertain"] = errors.ContainsKey("rollback_verification");
    }

    private static void VerifyExactDrawListVisual(DrawList drawList, DbElement element, DrawListVisualEntry entry)
    {
        DrawListVisualEntry readback = CaptureDrawListVisual(RequireExactDrawListMember(drawList, element));
        if (readback.refno != entry.refno || readback.colour != entry.colour || readback.transparency != entry.transparency ||
            readback.edges != entry.edges || readback.visibility != entry.visibility || readback.shallow != entry.shallow)
            throw new InvalidOperationException("Exact Drawlist visual state failed readback: " + entry.refno);
    }

    private static void WriteDrawListStateFile(string path, string contents)
    {
        string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                byte[] bytes = new UTF8Encoding(false).GetBytes(contents);
                stream.Write(bytes, 0, bytes.Length);
                stream.Flush(true);
            }
            if (File.Exists(path)) File.Replace(temporary, path, null);
            else File.Move(temporary, path);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }

    private static Dictionary<string, object> DrawListPersistenceResult(string status, string error = null)
    {
        string path = null;
        try { path = DrawListStatePath(); }
        catch (Exception pathError) { error = error ?? pathError.Message; }
        return new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
        {
            ["attempted"] = false,
            ["succeeded"] = null,
            ["status"] = status,
            ["path"] = path == null ? null : path + ".json",
            ["error"] = error,
            ["legacy_export"] = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
            {
                ["attempted"] = false,
                ["succeeded"] = null,
                ["status"] = "not_attempted",
                ["path"] = path,
                ["error"] = null
            }
        };
    }

    private static Dictionary<string, object> SaveDrawListState(DrawList drawList)
    {
        ThrowIfNativeFaulted();
        Dictionary<string, object> result = DrawListPersistenceResult("snapshot_failed");
        result["attempted"] = true;
        result["succeeded"] = false;
        try
        {
            if (drawList == null)
            {
                result["status"] = "drawlist_unavailable";
                result["error"] = "No Drawlist is available; the previous persisted state is retained.";
                return result;
            }
            var document = new DrawListVisualDocument { schema_version = 2 };
            result["status"] = "context_unavailable";
            Project project = NativeRead(() => Project.CurrentProject);
            MDB mdb = CurrentMdb();
            if (project == null || mdb == null)
            {
                result["status"] = "context_unavailable";
                result["error"] = "Project/MDB identity is unavailable; the previous persisted state is retained.";
                return result;
            }
            document.project = NativeRead(() => project.Name);
            document.mdb = NativeRead(() => mdb.Name);
            if (string.IsNullOrWhiteSpace(document.project) || string.IsNullOrWhiteSpace(document.mdb))
            {
                result["status"] = "context_unavailable";
                result["error"] = "Project/MDB identity is blank; the previous persisted state is retained.";
                return result;
            }
            // Capture must finish completely before any filesystem write.
            result["status"] = "snapshot_failed";
            document.members.AddRange(CaptureDrawListVisualSnapshot(drawList).Values);
            document.primitive_entries = CaptureDrawListPrimitiveSnapshot(drawList).Values.ToList();
            return PersistDrawListVisualDocument(result, document);
        }
        catch (Exception error)
        {
            ThrowIfNativeFaulted();
            result["error"] = error.Message;
            return result;
        }
    }

    private static Dictionary<string, object> PersistDrawListVisualDocument(
        Dictionary<string, object> result, DrawListVisualDocument document, bool allowEmpty = false)
    {
        ThrowIfNativeFaulted();
        result["attempted"] = true;
        result["succeeded"] = false;
        result["status"] = "snapshot_failed";
        try
        {
            if (!allowEmpty && document != null && document.members != null && document.members.Count == 0)
            {
                result["status"] = "empty_drawlist";
                result["error"] = "The Drawlist is empty; the previous persisted state is retained.";
                return result;
            }
            ValidateDrawListVisualDocument(document);
            string json = JsonConvert.SerializeObject(document, Formatting.Indented);
            var legacy = (Dictionary<string, object>)result["legacy_export"];
            result["status"] = "write_failed";
            string path = result["path"] as string;
            if (string.IsNullOrWhiteSpace(path))
                throw new InvalidOperationException("The Drawlist persistence path is unavailable.");
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            // This single atomic JSON snapshot is authoritative. A legacy
            // reference export cannot invalidate or partially commit it.
            WriteDrawListStateFile(path, json);
            result["succeeded"] = true;
            result["status"] = "saved";
            result["schema_version"] = document.schema_version;
            result["primitive_entry_count"] = document.primitive_entries == null ? (object)null : document.primitive_entries.Count;
            result["visual_scope"] = document.schema_version == 2
                ? "Observed top-level member and primitive-entry values; hidden override/inheritance metadata is not exposed."
                : "Observed top-level member values only.";
            result["error"] = null;
            legacy["attempted"] = true;
            try
            {
                WriteDrawListStateFile((string)legacy["path"], string.Join(Environment.NewLine,
                    document.members.Select(entry => entry.refno)) + Environment.NewLine);
                legacy["succeeded"] = true;
                legacy["status"] = "exported";
            }
            catch (Exception error)
            {
                legacy["succeeded"] = false;
                legacy["status"] = "write_failed";
                legacy["error"] = error.Message;
            }
        }
        catch (Exception error)
        {
            result["error"] = error.Message;
        }
        // Persistence is independent of display success and must not trigger
        // display replay or rollback, even when snapshot/write/export fails.
        return result;
    }

    private static void ReadDrawListState(string path, out List<string> refs, out DrawListVisualDocument visualState)
    {
        refs = new List<string>();
        visualState = null;
        string json = null;
        try
        {
            json = File.ReadAllText(path + ".json");
        }
        catch (FileNotFoundException) { }
        catch (DirectoryNotFoundException) { }
        if (json != null)
        {
            visualState = JsonConvert.DeserializeObject<DrawListVisualDocument>(json);
            ValidateDrawListVisualDocument(visualState);
            refs.AddRange(visualState.members.Select(entry => entry.refno));
            return;
        }
        // A missing JSON snapshot alone enables the old membership-only format.
        // Locked/unreadable or malformed persisted files must surface as errors.
        string[] lines;
        try { lines = File.ReadAllLines(path); }
        catch (FileNotFoundException) { return; }
        catch (DirectoryNotFoundException) { return; }
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (string line in lines)
        {
            string refno = (line ?? "").Trim();
            if (refno.Length > 0 && seen.Add(refno)) refs.Add(refno);
        }
    }

    private static int RestoreDrawListState(DrawList drawList, IList<string> refs, DrawListVisualDocument visualState,
        bool fitView, out int pending, out Dictionary<string, object> details)
    {
        ThrowIfNativeFaulted();
        pending = 0;
        var addErrors = new Dictionary<string, string>(StringComparer.Ordinal);
        var visualErrors = new Dictionary<string, string>(StringComparer.Ordinal);
        details = new Dictionary<string, object>
        {
            ["restore_succeeded"] = false,
            ["visual_state_available"] = visualState != null,
            ["visual_restore_succeeded"] = null,
            ["visual_restored_count"] = 0,
            ["visual_apply_attempted_count"] = 0,
            ["write_attempted"] = false,
            ["add_attempted"] = false,
            ["refresh_attempted"] = false,
            ["refresh_attempt_count"] = 0,
            ["graphics_updated"] = false,
            ["fit"] = false,
            ["fit_attempted"] = false,
            ["primitive_apply_attempted"] = false,
            ["unsupported_transparency_state"] = false,
            ["rollback_attempted"] = false,
            ["transparency_reset_members"] = new List<string>(),
            ["visual_scope"] = "exact top-level DrawListMember; shallow is on-top rendering",
            ["schema_version"] = visualState == null ? (object)null : visualState.schema_version,
            ["primitive_restore_succeeded"] = null,
            ["primitive_restored_count"] = 0,
            ["override_metadata_verified"] = false,
            ["add_errors"] = addErrors,
            ["visual_errors"] = visualErrors,
            ["legacy_membership_only"] = visualState == null
        };
        if (drawList == null)
            return 0;
        int restored = 0;
        Dictionary<string, DrawListVisualEntry> initialMembers = null;
        Dictionary<string, DrawListPrimitiveVisualEntry> initialPrimitives = null;
        try
        {
            initialMembers = CaptureDrawListVisualSnapshot(drawList);
            initialPrimitives = CaptureDrawListPrimitiveSnapshot(drawList);
            if (visualState != null && visualState.schema_version == 2)
            {
                var preflightMembers = initialMembers.ToDictionary(item => item.Key, item => CloneDrawListVisual(item.Value), StringComparer.Ordinal);
                var preflightPrimitives = initialPrimitives.ToDictionary(item => item.Key, item => PrimitiveVisualClone(item.Value), StringComparer.Ordinal);
                foreach (var member in visualState.members) preflightMembers[member.refno] = member;
                foreach (var primitive in visualState.primitive_entries) preflightPrimitives[PrimitiveVisualKey(primitive)] = primitive;
                PrimitiveVisualPreflightTransparency(drawList, PrimitiveVisualReadMap(drawList), preflightPrimitives, preflightMembers);
            }
            Dictionary<string, DbElement> resolved = ResolvePersistedElements(refs);
            List<DbElement> restorationOrder = OrderDrawListElementsByOwner(resolved.Values);
            // First establish every membership. Adding an ancestor can cause
            // E3D to add descendants and initialize their visual state.
            foreach (string refno in refs)
            {
                DbElement element;
                if (!resolved.TryGetValue(NormalizeRef(refno), out element) || !IsUsable(element))
                {
                    pending++;
                    continue;
                }
                try
                {
                    bool exactPresent = NativeRead(() => drawList.Members()).Any(member => member != null && IsUsable(member.DbElement) && ElementRef(member.DbElement) == refno);
                    if (!exactPresent)
                    {
                        details["write_attempted"] = true;
                        details["add_attempted"] = true;
                        NativeWrite(() => drawList.Add(element));
                        RefreshDrawListVisual(details);
                    }
                    // Add can return without creating an exact top-level member.
                    // Confirm membership even for legacy snapshots with no visuals.
                    RequireExactDrawListMember(drawList, element);
                    if (!exactPresent) restored++;
                }
                catch (Exception ex) { ThrowIfNativeFaulted(); addErrors[refno] = ex.Message; pending++; }
            }
            // Restore ancestors before descendants: public member setters can
            // propagate, so the persisted enumeration order is not sufficient.
            int restoredVisuals = 0;
            if (visualState != null)
            {
                var visualEntries = visualState.members.ToDictionary(entry => NormalizeRef(entry.refno), StringComparer.OrdinalIgnoreCase);
                Dictionary<string, DrawListPrimitiveVisualEntry> expectedPrimitives = null;
                Dictionary<string, DrawListVisualEntry> expectedMembers = null;
                if (visualState.schema_version == 2)
                {
                    details["visual_scope"] = "Observed top-level member and primitive-entry values; no claim about hidden override/inheritance metadata or pixels.";
                    details["primitive_restore_succeeded"] = false;
                    if (pending != 0 || addErrors.Count != 0)
                        throw new InvalidOperationException("Schema-2 primitive restoration requires every saved top-level member to be available.");
                    // Add must have materialized the saved entries. Restoration
                    // never fabricates a missing primitive or changes its owner.
                    expectedPrimitives = CaptureDrawListPrimitiveSnapshot(drawList);
                    var savedPrimitiveKeys = new HashSet<string>(visualState.primitive_entries.Select(entry => entry.owner_refno + "|" + entry.refno), StringComparer.Ordinal);
                    RequireDrawListVisualRestoreKeys(initialPrimitives.Keys, savedPrimitiveKeys, expectedPrimitives.Keys, "primitive entry");
                    var savedPrimitiveRefs = new HashSet<string>(visualState.primitive_entries.Select(entry => entry.refno), StringComparer.Ordinal);
                    foreach (KeyValuePair<string, DrawListPrimitiveVisualEntry> actual in expectedPrimitives)
                        if (savedPrimitiveRefs.Contains(actual.Value.refno) && !savedPrimitiveKeys.Contains(actual.Key))
                            throw new InvalidOperationException("A saved primitive has a different graphical alias mapping: " + actual.Key);
                    foreach (KeyValuePair<string, DrawListPrimitiveVisualEntry> original in initialPrimitives)
                    {
                        if (!expectedPrimitives.ContainsKey(original.Key))
                            throw new InvalidOperationException("Membership restoration removed a pre-existing primitive entry: " + original.Key);
                        if (!savedPrimitiveKeys.Contains(original.Key)) expectedPrimitives[original.Key] = original.Value;
                    }
                    foreach (DrawListPrimitiveVisualEntry entry in visualState.primitive_entries)
                    {
                        string key = entry.owner_refno + "|" + entry.refno;
                        if (!expectedPrimitives.ContainsKey(key))
                            throw new InvalidOperationException("A saved primitive entry is absent after membership restoration: " + key);
                        expectedPrimitives[key] = entry;
                    }
                    expectedMembers = CaptureDrawListVisualSnapshot(drawList);
                    RequireDrawListVisualRestoreKeys(initialMembers.Keys, visualState.members.Select(entry => entry.refno), expectedMembers.Keys, "exact member");
                    foreach (KeyValuePair<string, DrawListVisualEntry> original in initialMembers)
                    {
                        if (!expectedMembers.ContainsKey(original.Key))
                            throw new InvalidOperationException("Membership restoration removed a pre-existing member: " + original.Key);
                        if (!visualEntries.ContainsKey(NormalizeRef(original.Key))) expectedMembers[original.Key] = original.Value;
                    }
                    foreach (DrawListVisualEntry entry in visualState.members) expectedMembers[entry.refno] = entry;
                    restorationOrder = OrderDrawListElementsByOwner(drawList.Members().Select(member => member.DbElement));
                }
                foreach (DrawListVisualEntry entry in visualState.members)
                {
                    DbElement element;
                    if (!resolved.TryGetValue(NormalizeRef(entry.refno), out element) || !IsUsable(element))
                        visualErrors[entry.refno] = "Saved element could not be resolved for exact visual restoration.";
                }
                bool refreshNeeded = false;
                Dictionary<string, object> mutationDetails = details;
                foreach (DbElement element in restorationOrder)
                {
                    DrawListVisualEntry entry;
                    if (expectedMembers != null) entry = expectedMembers[ElementRef(element)];
                    else if (!visualEntries.TryGetValue(NormalizeRef(ElementRef(element)), out entry)) continue;
                    details["visual_apply_attempted_count"] = (int)details["visual_apply_attempted_count"] + 1;
                    try { ApplyExactDrawListVisual(drawList, element, entry, () =>
                    {
                        mutationDetails["write_attempted"] = true;
                        refreshNeeded = true;
                    }); }
                    catch (Exception ex) { ThrowIfNativeFaulted(); visualErrors[entry.refno] = ex.Message; }
                }
                if (refreshNeeded) RefreshDrawListVisual(details);
                refreshNeeded = false;
                if (expectedPrimitives != null)
                {
                    if (CompareDrawListPrimitiveSnapshots(expectedPrimitives, CaptureDrawListPrimitiveSnapshot(drawList)).Count != 0)
                    {
                        details["primitive_apply_attempted"] = true;
                        details["write_attempted"] = true;
                        ApplyDrawListPrimitiveSnapshot(drawList, expectedPrimitives, true, expectedMembers, null,
                            (List<string>)details["transparency_reset_members"]);
                        refreshNeeded = true;
                    }
                }
                FitDrawListForRestore(fitView, restored, details);
                if (refreshNeeded || Equals(details["fit_attempted"], true)) RefreshDrawListVisual(details);
                // No refresh or fit is permitted after these final complete
                // comparisons. Saving below only reads the verified display.
                if (expectedPrimitives != null)
                {
                    var primitiveDifferences = CompareDrawListPrimitiveSnapshots(expectedPrimitives, CaptureDrawListPrimitiveSnapshot(drawList));
                    var memberDifferences = CompareDrawListVisualSnapshots(expectedMembers, CaptureDrawListVisualSnapshot(drawList));
                    details["primitive_verification_errors"] = primitiveDifferences;
                    details["member_verification_errors"] = memberDifferences;
                    if (primitiveDifferences.Count != 0 || memberDifferences.Count != 0)
                        throw new InvalidOperationException("Schema-2 restored member/primitive observed values failed complete readback.");
                    details["primitive_restore_succeeded"] = true;
                    details["primitive_restored_count"] = visualState.primitive_entries.Count;
                }
                // Verify the whole final state after every setter has run, so a
                // later application cannot silently alter an earlier member.
                foreach (DrawListVisualEntry entry in visualState.members)
                {
                    DbElement element;
                    if (!resolved.TryGetValue(NormalizeRef(entry.refno), out element) || !IsUsable(element)) continue;
                    try { VerifyExactDrawListVisual(drawList, element, entry); restoredVisuals++; }
                    catch (Exception ex) { ThrowIfNativeFaulted(); visualErrors[entry.refno] = ex.Message; }
                }
            }
            else
            {
                FitDrawListForRestore(fitView, restored, details);
                if (Equals(details["fit_attempted"], true)) RefreshDrawListVisual(details);
                foreach (DbElement element in resolved.Values) RequireExactDrawListMember(drawList, element);
            }
            details["visual_restored_count"] = restoredVisuals;
            details["visual_restore_succeeded"] = visualState == null ? (object)null : visualErrors.Count == 0 && restoredVisuals == visualState.members.Count;
            details["restore_succeeded"] = pending == 0 && addErrors.Count == 0 && visualErrors.Count == 0;
            details["legacy_membership_only"] = visualState == null && visualErrors.Count == 0;
        }
        catch (Exception ex)
        {
            ThrowIfNativeFaulted();
            details["unsupported_transparency_state"] = ex is UnsupportedTransparencyStateException;
            visualErrors["restore"] = ex.Message;
        }
        if (!Equals(details["restore_succeeded"], true))
            CaptureDrawListVisualResidual(drawList, initialMembers, initialPrimitives, details);
        return restored;
    }

    private static void RequireDrawListVisualRestoreKeys(IEnumerable<string> initial, IEnumerable<string> saved,
        IEnumerable<string> actual, string label)
    {
        var expected = new HashSet<string>(initial, StringComparer.Ordinal);
        expected.UnionWith(saved);
        var observed = new HashSet<string>(actual, StringComparer.Ordinal);
        if (!expected.SetEquals(observed))
            throw new InvalidOperationException("Schema-2 restore changed the complete " + label + " key set; missing: " +
                string.Join(", ", expected.Except(observed)) + "; unexpected: " + string.Join(", ", observed.Except(expected)));
    }

    private static void FitDrawListForRestore(bool requested, int restored, IDictionary<string, object> details)
    {
        ThrowIfNativeFaulted();
        if (!requested || restored == 0) return;
        object view = CurrentView3D();
        double[] limits = view == null ? null : InvokeDoubleArray(view, "GetDrawlistLimits");
        if (limits == null || limits.Length < 6)
            throw new InvalidOperationException("Restored Drawlist limits are unavailable for the requested fit.");
        details["fit_attempted"] = true;
        ApplyViewFit(view, limits, true, true);
        details["fit"] = true;
    }

    private static void ValidateDrawListVisualState(DrawListVisualDocument document, string project, string mdb)
    {
        ValidateDrawListVisualDocument(document);
        if (string.IsNullOrEmpty(project) || string.IsNullOrEmpty(mdb) || document.project != project || document.mdb != mdb)
            throw new InvalidOperationException("Drawlist visual snapshot belongs to a different project/MDB.");
    }

    private static void ValidateDrawListVisualDocument(DrawListVisualDocument document)
    {
        if (document == null || (document.schema_version != 1 && document.schema_version != 2) ||
            document.members == null || (document.members.Count == 0 && document.schema_version != 2) || document.members.Count > DisplayStateMemberLimit)
            throw new InvalidOperationException("Drawlist visual snapshot has an unsupported version or incomplete member state.");
        if (string.IsNullOrWhiteSpace(document.project) || string.IsNullOrWhiteSpace(document.mdb))
            throw new InvalidOperationException("Drawlist visual snapshot has no project/MDB identity.");
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (DrawListVisualEntry entry in document.members)
            if (entry == null || string.IsNullOrWhiteSpace(entry.refno) || !seen.Add(entry.refno) ||
                (document.schema_version == 2 && !IsNumericVisualReference(entry.refno)) ||
                entry.transparency < 0 || entry.transparency > 100)
                throw new InvalidOperationException("Drawlist visual snapshot has invalid or duplicate member data.");
        if (document.schema_version == 1)
        {
            if (document.primitive_entries != null)
                throw new InvalidOperationException("Schema 1 cannot declare primitive entries; use schema 2.");
            return;
        }
        if (document.primitive_entries == null || document.primitive_entries.Count > DisplayStatePrimitiveLimit)
            throw new InvalidOperationException("Schema 2 requires a complete primitive-entry collection within the 20000-entry limit.");
        var primitiveKeys = new HashSet<string>(StringComparer.Ordinal);
        var primitiveValues = new Dictionary<string, DrawListPrimitiveVisualEntry>(StringComparer.Ordinal);
        foreach (DrawListPrimitiveVisualEntry entry in document.primitive_entries)
        {
            if (entry == null || !IsNumericVisualReference(entry.refno) || !IsNumericVisualReference(entry.owner_refno) ||
                !seen.Contains(entry.owner_refno) || entry.transparency < 0 || entry.transparency > 100 ||
                !primitiveKeys.Add(entry.owner_refno + "|" + entry.refno))
                throw new InvalidOperationException("Schema 2 has invalid, duplicate or unowned primitive-entry data.");
            DrawListPrimitiveVisualEntry alias;
            if (primitiveValues.TryGetValue(entry.refno, out alias) &&
                (alias.colour != entry.colour || alias.transparency != entry.transparency || alias.edges != entry.edges ||
                 alias.visibility != entry.visibility || alias.shallow != entry.shallow))
                throw new InvalidOperationException("Schema 2 contains conflicting observations for aliases of the same primitive.");
            primitiveValues[entry.refno] = entry;
        }
    }

    private static bool IsNumericVisualReference(string reference)
    {
        if (string.IsNullOrEmpty(reference) || reference[0] != '=') return false;
        string[] parts = reference.Substring(1).Split('/');
        int database, element;
        return parts.Length == 2 && int.TryParse(parts[0], out database) && int.TryParse(parts[1], out element) &&
            database > 0 && element >= 0 && reference == "=" + database.ToString(CultureInfo.InvariantCulture) + "/" + element.ToString(CultureInfo.InvariantCulture);
    }

    private static Dictionary<string, DbElement> ResolvePersistedElements(IList<string> refs)
    {
        ThrowIfNativeFaulted();
        Dictionary<string, DbElement> resolved = new Dictionary<string, DbElement>(StringComparer.OrdinalIgnoreCase);
        if (refs == null || refs.Count == 0)
            return resolved;

        MDB mdb = CurrentMdb();
        if (mdb == null)
            return resolved;

        HashSet<string> wanted = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (string refno in refs)
        {
            if (string.IsNullOrWhiteSpace(refno))
                continue;
            string key = NormalizeRef(refno);
            if (key.Length > 0)
                wanted.Add(key);
            if (!refno.Trim().StartsWith("=", StringComparison.Ordinal))
            {
                try
                {
                    DbElement direct = NativeRead(() => mdb.FindElement(refno.Trim()));
                    if (IsUsable(direct))
                    {
                        resolved[key] = direct;
                        wanted.Remove(key);
                    }
                }
                catch
                {
                    ThrowIfNativeFaulted();
                }
            }
        }

        if (wanted.Count == 0)
            return resolved;

        // Resolve all persisted database references in one bounded walk.  A
        // separate search for every member can block the E3D UI repeatedly.
        try
        {
            DbElement root = NativeRead(() => mdb.GetFirstWorld(DbType.Design));
            if (!IsUsable(root))
                return resolved;
            Queue<DbElement> queue = new Queue<DbElement>();
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            Stopwatch timer = Stopwatch.StartNew();
            int visited = 0;
            queue.Enqueue(root);
            while (queue.Count > 0 && wanted.Count > 0 && visited < 50000 && timer.ElapsedMilliseconds < 5000)
            {
                DbElement element = queue.Dequeue();
                visited++;
                if (!IsUsable(element))
                    continue;
                string key = ElementRef(element);
                if (!string.IsNullOrEmpty(key) && !seen.Add(key))
                    continue;
                string normalized = NormalizeRef(key);
                if (wanted.Contains(normalized))
                {
                    resolved[normalized] = element;
                    wanted.Remove(normalized);
                }
                try
                {
                    foreach (DbElement child in NativeRead(() => element.Members()))
                        if (IsUsable(child))
                            queue.Enqueue(child);
                }
                catch
                {
                    ThrowIfNativeFaulted();
                }
            }
        }
        catch
        {
            ThrowIfNativeFaulted();
        }
        return resolved;
    }

    private static DbElement ResolveElement(string refno)
    {
        ThrowIfNativeFaulted();
        if (string.IsNullOrWhiteSpace(refno))
            return CurrentElement();
        MDB mdb = CurrentMdb();
        if (mdb == null)
            return null;
        string wanted = refno.Trim();

        // FindElement(string) is a name lookup. REF's numeric pair has a
        // dedicated SDK lookup, independent of model size and hierarchy depth.
        if (wanted.StartsWith("=", StringComparison.Ordinal))
        {
            string[] pair = wanted.Substring(1).Split('/');
            int databasePart, elementPart;
            if (pair.Length != 2 ||
                !int.TryParse(pair[0], NumberStyles.Integer, CultureInfo.InvariantCulture, out databasePart) ||
                !int.TryParse(pair[1], NumberStyles.Integer, CultureInfo.InvariantCulture, out elementPart))
                return null;
            try
            {
                DbElement direct = NativeRead(() => DbElement.GetElement(new[] { databasePart, elementPart }));
                return IsUsable(direct) ? direct : null;
            }
            catch
            {
                ThrowIfNativeFaulted();
                return null;
            }
        }

        try
        {
            DbElement direct = NativeRead(() => mdb.FindElement(wanted));
            if (IsUsable(direct))
                return direct;
        }
        catch
        {
            ThrowIfNativeFaulted();
        }

        // Retain the bounded legacy fallback for non-numeric references.
        try
        {
            List<DbElement> matches = new List<DbElement>();
            CollectMatches(NativeRead(() => mdb.GetFirstWorld(DbType.Design)), wanted, 1, matches, false, 10000, 1500);
            return matches.Count == 0 ? null : matches[0];
        }
        catch
        {
            return null;
        }
    }

    private static DbElement ResolveElementByType(string refno, string expectedType)
    {
        string requestedType = string.IsNullOrWhiteSpace(expectedType)
            ? "" : ModelEditInput.TypeName(expectedType).ToUpperInvariant();
        DbElement element = ResolveElement(refno);
        if (!IsUsable(element) || requestedType.Length == 0)
            return element;
        if (string.Equals(ElementType(element), requestedType, StringComparison.OrdinalIgnoreCase))
            return element;
        if (!string.Equals(requestedType, "TUBI", StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("Resolved element type does not match expected_type " + requestedType + ".");
        int[] pair = NativeRead(() => element.Ref);
        if (!ModelEditInput.IsReferencePair(pair))
            throw new InvalidOperationException("TUBI alias selection requires a valid numeric anchor reference.");
        return ConnectionResolveImplicitTubeElement(pair, element);
    }

    private static BoundedHierarchy<DbElement> ReadHierarchy(DbElement element, int depth, int limit)
    {
        return BoundedHierarchy<DbElement>.Read(element, depth, limit, ReadChildren);
    }

    private static IEnumerable<DbElement> ReadChildren(DbElement element)
    {
        foreach (DbElement child in NativeRead(() => element.Members()))
        {
            if (!IsUsable(child))
                throw new InvalidOperationException("Hierarchy contains an unreadable member of " + ElementRef(element));
            yield return child;
        }
    }

    private static Dictionary<string, object> HierarchyInfo(BoundedHierarchy<DbElement> hierarchy, int depth, int limit)
    {
        return new Dictionary<string, object>
        {
            ["complete"] = hierarchy.Complete,
            ["truncated"] = hierarchy.TruncationReasons.Count > 0,
            ["element_count"] = hierarchy.Elements.Count,
            ["depth"] = depth,
            ["limit"] = limit,
            ["truncation_reasons"] = hierarchy.TruncationReasons,
            ["read_errors"] = hierarchy.ReadErrors
        };
    }

    private static Dictionary<string, object> BuildSnapshot(BoundedHierarchy<DbElement> hierarchy, int depth, int limit)
    {
        Dictionary<string, object> result = SnapshotNode(hierarchy.Root);
        result["snapshot_info"] = HierarchyInfo(hierarchy, depth, limit);
        return result;
    }

    private static Dictionary<string, object> SnapshotNode(BoundedHierarchy<DbElement>.Node node)
    {
        Dictionary<string, object> result = ElementInfo(node.Value);
        result["members"] = node.Children.Select(child => (object)SnapshotNode(child)).ToList();
        return result;
    }

    private static string ModelValidationShape(string type)
    {
        switch ((type ?? "").Trim().ToUpperInvariant())
        {
            case "BOX": case "NBOX": return "BOX";
            case "CYLI": case "CYLINDER": case "NCYL": return "CYLI";
            case "CONE": case "NCON": return "CONE";
            case "DISH": return "DISH";
            case "NOZZ": case "NOZZLE": return "NOZZ";
            default: return null;
        }
    }

    private static string[] ModelValidationAttributes(string type)
    {
        string shape = ModelValidationShape(type);
        switch (shape)
        {
            case "BOX": return new[] { "XLEN", "YLEN", "ZLEN" };
            case "CYLI": return new[] { "DIAM", "HEIG" };
            case "CONE": return new[] { "DTOP", "DBOT", "HEIG" };
            case "DISH": return new[] { "DIAM", "HEIG", "RADI" };
            default:
                return string.Equals(type, "NOZZ", StringComparison.OrdinalIgnoreCase) ||
                       string.Equals(type, "NOZZLE", StringComparison.OrdinalIgnoreCase)
                    ? new[] { "HEIG" } : new string[0];
        }
    }

    private static List<object> ModelValidationDimensionRules()
    {
        return new List<object>
        {
            new Dictionary<string, object>
            {
                ["types"] = new[] { "BOX", "NBOX" }, ["attributes"] = new[] { "XLEN", "YLEN", "ZLEN" },
                ["rule"] = "all_finite_and_positive", ["status"] = "supported"
            },
            new Dictionary<string, object>
            {
                ["types"] = new[] { "CYLI", "CYLINDER", "NCYL" }, ["attributes"] = new[] { "DIAM", "HEIG" },
                ["rule"] = "all_finite_and_positive", ["status"] = "supported"
            },
            new Dictionary<string, object>
            {
                ["types"] = new[] { "CONE", "NCON" }, ["attributes"] = new[] { "DTOP", "DBOT", "HEIG" },
                ["rule"] = "finite_diameters_nonnegative_at_least_one_positive_height_positive", ["status"] = "supported"
            },
            new Dictionary<string, object>
            {
                ["types"] = new[] { "DISH" }, ["attributes"] = new[] { "DIAM", "HEIG", "RADI" },
                ["rule"] = "diameter_and_height_positive_radius_nonnegative", ["status"] = "supported_mcp_policy"
            },
            new Dictionary<string, object>
            {
                ["types"] = new[] { "NOZZ", "NOZZLE" }, ["attributes"] = new[] { "HEIG" },
                ["rule"] = "height_positive", ["status"] = "partial", ["unsupported_attributes"] = new[] { "DIAM" }
            }
        };
    }

    private static List<string> ModelValidationDimensionIssues(
        string elementRef, string shape, IDictionary<string, double> values)
    {
        List<string> issues = new List<string>();
        foreach (KeyValuePair<string, double> item in values)
        {
            if (double.IsNaN(item.Value) || double.IsInfinity(item.Value))
                issues.Add(elementRef + " has non-finite " + item.Key + ".");
        }
        Func<string, bool> has = field => values.ContainsKey(field) &&
            !double.IsNaN(values[field]) && !double.IsInfinity(values[field]);
        Func<string, string> format = field => values[field].ToString("R", CultureInfo.InvariantCulture) + " mm";
        if (shape == "BOX")
        {
            foreach (string field in new[] { "XLEN", "YLEN", "ZLEN" })
                if (has(field) && values[field] <= 0) issues.Add(elementRef + " has invalid " + field + ": " + format(field) + "; expected > 0.");
        }
        else if (shape == "CYLI")
        {
            foreach (string field in new[] { "DIAM", "HEIG" })
                if (has(field) && values[field] <= 0) issues.Add(elementRef + " has invalid " + field + ": " + format(field) + "; expected > 0.");
        }
        else if (shape == "CONE")
        {
            foreach (string field in new[] { "DTOP", "DBOT" })
                if (has(field) && values[field] < 0) issues.Add(elementRef + " has invalid " + field + ": " + format(field) + "; expected >= 0.");
            if (has("HEIG") && values["HEIG"] <= 0) issues.Add(elementRef + " has invalid HEIG: " + format("HEIG") + "; expected > 0.");
            if (has("DTOP") && has("DBOT") && values["DTOP"] == 0 && values["DBOT"] == 0)
                issues.Add(elementRef + " has invalid CONE/NCON dimensions: DTOP and DBOT cannot both be zero.");
        }
        else if (shape == "DISH")
        {
            foreach (string field in new[] { "DIAM", "HEIG" })
                if (has(field) && values[field] <= 0) issues.Add(elementRef + " has invalid " + field + ": " + format(field) + "; expected > 0.");
            if (has("RADI") && values["RADI"] < 0) issues.Add(elementRef + " has invalid RADI: " + format("RADI") + "; expected >= 0.");
        }
        else if (shape == "NOZZ")
        {
            if (has("HEIG") && values["HEIG"] <= 0) issues.Add(elementRef + " has invalid HEIG: " + format("HEIG") + "; expected > 0.");
        }
        return issues;
    }

    private static double ValidationDistanceMillimetres(DbElement element, string attributeName)
    {
        ThrowIfNativeFaulted();
        DbAttribute attribute = NativeRead(() => DbAttribute.GetDbAttribute(attributeName));
        if (attribute == null || !NativeRead(() => attribute.IsValid) || !NativeRead(() => element.IsAttributeValid(attribute)))
            throw new InvalidOperationException("The SDK reports " + attributeName + " unavailable.");
        DbDouble value = NativeRead(() => element.GetDbDouble(attribute));
        if (value == null)
            throw new InvalidOperationException("The SDK returned no dimensioned value for " + attributeName + ".");
        DbDoubleDimension dimension = NativeRead(() => value.Dimension);
        if (dimension == null || !NativeRead(() => dimension.Set) ||
            NativeRead(() => dimension.StandardDimension) != DbDimension.DIST)
            throw new InvalidOperationException(attributeName + " did not return a set distance dimension.");
        DbDoubleUnits millimetres = NativeRead(() => DbDoubleUnits.GetUnits("mm", dimension));
        if (millimetres == null || !NativeRead(() => millimetres.Set) ||
            NativeRead(() => millimetres.Dimension) != dimension)
            throw new InvalidOperationException("The SDK could not resolve explicit millimetre units for " + attributeName + ".");
        DbDouble converted = NativeRead(() => value.ConvertUnits(millimetres));
        if (converted == null || NativeRead(() => converted.Dimension) != dimension ||
            NativeRead(() => converted.Units) != millimetres)
            throw new InvalidOperationException("The SDK could not verify millimetre conversion for " + attributeName + ".");
        return converted.Value;
    }

    private void CaptureOutput(PdmsOutput message)
    {
        if (message == null)
            return;
        Dictionary<string, object> value = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase)
        {
            ["type"] = OutputString(delegate { return message.Type; }),
            ["description"] = OutputString(delegate { return message.Description; }),
            ["details"] = OutputString(delegate { return message.Details; }),
            ["category"] = OutputString(delegate { return message.Category; }),
            ["date"] = OutputString(delegate { return message.Date.ToString(); })
        };
        // Never throw the fault barrier back through a native output callback.
        // Latch before queuing so draining the message ring cannot clear it.
        if (nativeFaultState != null)
            nativeFaultState.Observe((string)value["description"], (string)value["date"]);
        lock (messageLock)
        {
            value["sequence"] = ++outputMessageSequence;
            if (outputMessages.Count >= 500)
                outputMessages.Dequeue();
            outputMessages.Enqueue(value);
        }
    }

    private static string OutputString(Func<string> read)
    {
        try { return read() ?? ""; }
        catch { return ""; }
    }

    private long MessageSequence()
    {
        lock (messageLock)
            return outputMessageSequence;
    }

    private void AddDiagnostics(Dictionary<string, object> result, long messageMark)
    {
        ThrowIfNativeFaulted();
        IList<object> messages;
        long endSequence;
        long omittedCount;
        long? firstRetainedSequence;
        lock (messageLock)
        {
            endSequence = outputMessageSequence;
            messages = outputMessages
                .Where(message => Convert.ToInt64(message["sequence"], CultureInfo.InvariantCulture) > messageMark &&
                    Convert.ToInt64(message["sequence"], CultureInfo.InvariantCulture) <= endSequence)
                .Cast<object>().ToList();
            long expectedCount = Math.Max(0, endSequence - messageMark);
            omittedCount = Math.Max(0, expectedCount - messages.Count);
            firstRetainedSequence = outputMessages.Count == 0
                ? (long?)null
                : Convert.ToInt64(outputMessages.Peek()["sequence"], CultureInfo.InvariantCulture);
        }
        if (messages.Count > 0)
            result["messages"] = messages;
        result["message_window"] = new Dictionary<string, object>
        {
            ["start_sequence_exclusive"] = messageMark,
            ["end_sequence_inclusive"] = endSequence,
            ["first_retained_sequence"] = firstRetainedSequence,
            ["observed_count"] = messages.Count,
            ["omitted_count"] = omittedCount,
            ["complete"] = omittedCount == 0,
            ["status"] = omittedCount == 0 ? "complete" : "overwritten",
            ["scope"] = "Messages observed in the command sequence window. The window does not establish message causation."
        };

        PdmsMessage lastError = null;
        try
        {
            lastError = NativeRead(() => PdmsMessage.GetLastError());
        }
        catch
        {
        }
        string errorText = MessageText(lastError);
        if (!string.IsNullOrEmpty(errorText))
            result["last_error"] = errorText;
    }

    private static Dictionary<string, object> ValidateCommand(string command, DbElement current)
    {
        string keyword = FirstWord(command);
        if (string.Equals(keyword, "UP", StringComparison.OrdinalIgnoreCase))
            return new Dictionary<string, object>
            {
                ["success"] = false,
                ["error"] = "UP is disabled by the E3D MCP bridge because it can enter an interactive wait; use END to close the current primitive."
            };

        if (string.Equals(keyword, "3DORBIT", StringComparison.OrdinalIgnoreCase)
            || string.Equals(keyword, "EDGES", StringComparison.OrdinalIgnoreCase))
            return new Dictionary<string, object>
            {
                ["success"] = false,
                ["error"] = "This command is interactive in E3D 2.1 and is blocked by the MCP bridge to prevent an undetectable UI wait; use Drawlist visual APIs for display properties."
            };

        if (string.Equals(keyword, "DESC", StringComparison.OrdinalIgnoreCase) && !SupportsDescription(ElementType(current)))
            return new Dictionary<string, object>
            {
                ["success"] = false,
                ["error"] = "Current element type '" + ElementType(current) + "' does not support DESC. Set DESC on EQUIPMENT, SUBEQUIPMENT, or NOZZLE, then use END for geometry primitives.",
                ["current_element"] = ElementRef(current),
                ["current_element_type"] = ElementType(current)
            };

        return null;
    }

    private static string FirstWord(string command)
    {
        string value = (command ?? "").Trim();
        int separator = value.IndexOfAny(new[] { ' ', '\t', '\r', '\n' });
        return (separator < 0 ? value : value.Substring(0, separator)).Trim().ToUpperInvariant();
    }

    private static bool SupportsDescription(string type)
    {
        switch ((type ?? "").Trim().ToUpperInvariant())
        {
            case "WORLD":
            case "SITE":
            case "ZONE":
            case "EQUI":
            case "EQUIPMENT":
            case "SUBEQUI":
            case "SUBEQUIPMENT":
            case "NOZZ":
            case "NOZZLE":
            case "PIPE":
            case "BRAN":
            case "BRANCH":
                return true;
            default:
                return false;
        }
    }

    private static string ElementRef(DbElement element)
    {
        return element == null ? "" : SafeString(delegate { return element.GetAsString(DbAttributeInstance.REF); });
    }

    private static string NormalizeRef(string value)
    {
        return (value ?? "").Trim().TrimStart('=');
    }

    private static string ElementType(DbElement element)
    {
        ThrowIfNativeFaulted();
        if (element == null)
            return "";
        try
        {
            var type = NativeRead(() => element.GetElementType());
            string shortName = NativeRead(() => type.ShortName);
            return string.IsNullOrEmpty(shortName) ? NativeRead(() => type.Name) : shortName;
        }
        catch
        {
            ThrowIfNativeFaulted();
            return "";
        }
    }

    private static string ElementName(DbElement element)
    {
        if (element == null)
            return "";
        foreach (DbAttribute attribute in new[] { DbAttributeInstance.FLNN, DbAttributeInstance.NAME, DbAttributeInstance.NAMN })
        {
            string value = SafeString(delegate { return element.GetAsString(attribute); });
            if (!string.IsNullOrEmpty(value) && !value.StartsWith("Unknown", StringComparison.OrdinalIgnoreCase))
                return value;
        }
        return "";
    }

    private static bool IsUsable(DbElement element)
    {
        ThrowIfNativeFaulted();
        try
        {
            bool result = element != null && element.IsValid;
            ThrowIfNativeFaulted();
            result = result && !element.IsNull;
            ThrowIfNativeFaulted();
            return result;
        }
        catch
        {
            ThrowIfNativeFaulted();
            return false;
        }
    }

    private static string SafeFormattedAttribute(DbElement element, DbAttribute attribute)
    {
        ThrowIfNativeFaulted();
        try
        {
            // This SDK entry point uses the same native formatter as GetAsString,
            // without constructing an exception for an unavailable attribute.
            string result = null;
            bool available = element.GetValidAsString(attribute, ref result);
            ThrowIfNativeFaulted();
            return available ? result ?? "" : "";
        }
        catch
        {
            ThrowIfNativeFaulted();
            return "";
        }
    }

    private static string SafeString(Func<string> read)
    {
        ThrowIfNativeFaulted();
        try
        {
            string result = read() ?? "";
            ThrowIfNativeFaulted();
            return result;
        }
        catch
        {
            ThrowIfNativeFaulted();
            return "";
        }
    }

    private static int SafeInt(Func<int> read)
    {
        ThrowIfNativeFaulted();
        try
        {
            int result = read();
            ThrowIfNativeFaulted();
            return result;
        }
        catch
        {
            ThrowIfNativeFaulted();
            return 0;
        }
    }

    private static bool SafeBool(Func<bool> read)
    {
        ThrowIfNativeFaulted();
        try
        {
            bool result = read();
            ThrowIfNativeFaulted();
            return result;
        }
        catch
        {
            ThrowIfNativeFaulted();
            return false;
        }
    }


    private static string MessageText(PdmsMessage message)
    {
        if (message == null)
            return "";
        return SafeString(delegate { return message.MessageText(); });
    }

    private T OnUi<T>(Func<T> operation, bool allowNativeFault = false)
    {
        if (currentNativeFaultState != null)
            throw new InvalidOperationException("Nested E3D SDK dispatch is not allowed.");
        if (invokeOnUi == null)
            throw new InvalidOperationException("The verified E3D UI dispatcher is unavailable; no SDK action was dispatched.");
        if (!allowNativeFault && nativeFaultState != null && nativeFaultState.HasFault)
            throw new NativeDatabaseFaultException();
        if (!allowNativeFault) ThrowIfGeneratedCsgQuarantined();
        T result = default(T);
        Exception failure = null;
        invokeOnUi(delegate
        {
            if (currentNativeFaultState != null)
                throw new InvalidOperationException("A reentrant UI callback cannot enter an active E3D SDK scope.");
            NativeDatabaseFaultState previousState = currentNativeFaultState;
            long previousGeneration = permittedNativeFaultGeneration;
            bool previousCsgRecovery = generatedCsgRecoveryPermitted;
            // Recovery may cross the pre-existing fault, never a newly raised one.
            currentNativeFaultState = nativeFaultState;
            permittedNativeFaultGeneration = allowNativeFault && nativeFaultState != null ? nativeFaultState.Generation : 0;
            generatedCsgRecoveryPermitted = allowNativeFault;
            try
            {
                ThrowIfNativeFaulted();
                result = operation();
                ThrowIfNativeFaulted();
            }
            catch (Exception ex)
            {
                failure = ex;
            }
            finally
            {
                currentNativeFaultState = previousState;
                permittedNativeFaultGeneration = previousGeneration;
                generatedCsgRecoveryPermitted = previousCsgRecovery;
            }
        });
        if (failure != null)
            System.Runtime.ExceptionServices.ExceptionDispatchInfo.Capture(failure).Throw();
        return result;
    }
}
