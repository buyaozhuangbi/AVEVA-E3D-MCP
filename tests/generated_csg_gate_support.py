"""Extract the real managed CSG quarantine dependency for SDK-fake harnesses.

The existing harnesses extract native guards into their context class. Include
the actual isolation state, recovery scope and exception alongside that guard,
so their compiled production paths exercise the new gate rather than a stub.
"""

from pathlib import Path
import re


_BRIDGE = Path(__file__).resolve().parents[1] / "bridge/e3d21"
_TOKENS = re.compile(r'@"(?:""|[^"])*"|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/|[{}]', re.S)


def _class(source, name):
    declaration = re.search(rf"^(?:    private|internal) sealed class {name}\b[^\n]*", source, re.M)
    if declaration is None:
        raise AssertionError("Missing real generated CSG gate class: " + name)
    opening = source.index("{", declaration.end())
    depth = 0
    for token in _TOKENS.finditer(source, opening):
        if token.group() == "{":
            depth += 1
        elif token.group() == "}":
            depth -= 1
            if depth == 0:
                return source[declaration.start():token.end()]
    raise AssertionError("Unterminated generated CSG gate class: " + name)


def generated_csg_gate_members(extract_method):
    source = (_BRIDGE / "E3d21GeneratedCsg.cs").read_text(encoding="utf-8-sig")
    pieces = []
    for pattern in (r"^    private static readonly GeneratedCsgIsolation generatedCsgIsolation[^\n]+;",
                    r"^    \[ThreadStatic\] private static bool generatedCsgRecoveryPermitted;"):
        match = re.search(pattern, source, re.M)
        if match is None:
            raise AssertionError("Missing real generated CSG gate state: " + pattern)
        pieces.append(match.group())
    pieces.extend(extract_method(source, name) for name in (
        "IsGeneratedCsgQuarantined", "ThrowIfGeneratedCsgQuarantined"))
    # The production diagnostic accessor is an instance method only because it
    # hangs off E3d21Context. Offline extracted harnesses host guards in static
    # classes; retain its exact body while adapting the hosting declaration.
    accessor = extract_method(source, "GetGeneratedCsgQuarantineState")
    accessor = accessor.replace("public object GetGeneratedCsgQuarantineState()",
                                "private static object GetGeneratedCsgQuarantineState()", 1)
    pieces.append(accessor)
    pieces.extend(_class(source, name) for name in ("GeneratedCsgIsolation", "GeneratedCsgQuarantineException"))
    result = "\n\n".join(pieces)
    # Fake-context templates have different using directives. Qualify only
    # standard library names; the extracted control flow/state stays intact.
    for original, qualified in (
        (r"\bDictionary<", "System.Collections.Generic.Dictionary<"),
        (r"\bList<", "System.Collections.Generic.List<"),
        (r"\bDateTime\.", "System.DateTime."),
        (r"\bCultureInfo\.", "System.Globalization.CultureInfo."),
        (r"\bInvalidOperationException\b", "System.InvalidOperationException"),
        (r"\bException inner\b", "System.Exception inner"),
        (r"\[ThreadStatic\]", "[System.ThreadStatic]"),
    ):
        result = re.sub(original, qualified, result)
    return result
