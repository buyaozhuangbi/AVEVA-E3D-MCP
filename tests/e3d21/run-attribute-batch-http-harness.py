"""Compile the exact production batch attribute HTTP branch without E3D."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "bridge" / "e3d21" / "E3d21HttpAdapter.cs"
SDK = Path(os.environ.get("E3D21_ROOT", r"C:\Program Files (x86)\AVEVA\Everything3D2.10"))


def block_end(source: str, opening: int) -> int:
    depth = 0
    state = "code"
    index = opening
    while index < len(source):
        ch = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "code":
            if ch == '"':
                state = "string"
            elif ch == "'":
                state = "char"
            elif ch == "/" and following == "/":
                state = "line"
                index += 1
            elif ch == "/" and following == "*":
                state = "comment"
                index += 1
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return index + 1
        elif state in {"string", "char"}:
            if ch == "\\":
                index += 1
            elif (state == "string" and ch == '"') or (state == "char" and ch == "'"):
                state = "code"
        elif state == "line" and ch == "\n":
            state = "code"
        elif state == "comment" and ch == "*" and following == "/":
            state = "code"
            index += 1
        index += 1
    raise ValueError("Unterminated source block")


def method(source: str, name: str) -> str:
    match = re.search(r"^\s+private static[^\r\n]*\b" + re.escape(name) + r"\(", source, re.MULTILINE)
    if match is None:
        raise ValueError("Missing method " + name)
    start = match.start()
    opening = source.index("{", match.end())
    return source[start:block_end(source, opening)]


def main() -> None:
    adapter = ADAPTER.read_text(encoding="utf-8-sig")
    marker = 'else if (method == "POST" && path == "/api/v1/element/attributes/set")'
    opening = adapter.index("{", adapter.index(marker) + len(marker))
    route = adapter[opening + 1:block_end(adapter, opening) - 1]
    methods = [method(adapter, name) for name in ("ReadUniqueBodyAsync", "ReadString", "ReadBoolean")]
    methods[0] = methods[0].replace("HttpListenerRequest request", "BatchInnerRequest request")
    harness = (Path(__file__).with_name("AttributeBatchHttpHarness.cs")).read_text(encoding="utf-8")
    harness = harness.replace("// PRODUCTION_ROUTE", route).replace("// PRODUCTION_METHODS", "\n\n".join(methods))
    with tempfile.TemporaryDirectory(prefix="attribute-batch-http-") as directory:
        output = Path(directory)
        (output / "Harness.cs").write_text(harness, encoding="utf-8")
        (output / "Offline.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
            '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup><ItemGroup>'
            '<Reference Include="Newtonsoft.Json"><HintPath>' + escape(str(SDK / "Newtonsoft.Json.dll")) +
            '</HintPath><Private>True</Private></Reference></ItemGroup></Project>', encoding="utf-8")
        subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo"], cwd=output, check=True)
        subprocess.run([str(output / "bin" / "Release" / "net48" / "Offline.exe")], cwd=output, check=True)


if __name__ == "__main__":
    main()
