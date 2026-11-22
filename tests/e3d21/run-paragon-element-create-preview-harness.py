"""Compile the preview's native SDK caller offline against read-only test doubles."""
from pathlib import Path
import subprocess
import tempfile
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[2]
SDK = Path(r"C:\Program Files (x86)\AVEVA\Everything3D2.10")
ADMIN_21_SDK = Path(r"D:\AVEVA\Administration2.1")
SOURCE = ROOT / "bridge" / "e3d21" / "ParagonElementCreatePreview.cs"
HARNESS = Path(__file__).with_name("ParagonElementCreatePreviewHarness.cs")


def main() -> None:
    if not (SDK / "Newtonsoft.Json.dll").is_file():
        raise SystemExit("The installed E3D SDK Newtonsoft.Json reference is unavailable; no live E3D access is used.")
    with tempfile.TemporaryDirectory(prefix="paragon-create-preview-") as directory:
        output = Path(directory)
        for label, sdk_root in (("E3D210", SDK), ("Admin21", ADMIN_21_SDK)):
            assemblies = ("Aveva.Core.Database", "Aveva.Core.DatabaseInternal", "Aveva.Core.Utilities")
            if not all((sdk_root / (assembly + ".dll")).is_file() for assembly in assemblies):
                continue
            api_project = (
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Library</OutputType><LangVersion>latest</LangVersion><EnableDefaultCompileItems>false</EnableDefaultCompileItems>'
                '</PropertyGroup><ItemGroup><Compile Include="' + escape(str(SOURCE)) + '" /></ItemGroup><ItemGroup>'
            )
            for assembly in assemblies:
                api_project += (
                    '<Reference Include="' + assembly + '"><HintPath>' + escape(str(sdk_root / (assembly + ".dll")))
                    + '</HintPath><Private>False</Private></Reference>'
                )
            api_project += '</ItemGroup></Project>'
            project_name = label + "SdkApiCompile.csproj"
            (output / project_name).write_text(api_project, encoding="utf-8")
            subprocess.run(["dotnet", "build", project_name, "-c", "Release", "--nologo"], cwd=output, check=True)

        project = (
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
            '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion><EnableDefaultCompileItems>false</EnableDefaultCompileItems>'
            '</PropertyGroup><ItemGroup><Compile Include="' + escape(str(SOURCE)) + '" />'
            '<Compile Include="' + escape(str(HARNESS)) + '" /></ItemGroup><ItemGroup>'
            '<Reference Include="Newtonsoft.Json"><HintPath>' + escape(str(SDK / "Newtonsoft.Json.dll"))
            + '</HintPath><Private>True</Private></Reference></ItemGroup></Project>'
        )
        (output / "Harness.csproj").write_text(project, encoding="utf-8")
        subprocess.run(["dotnet", "build", "Harness.csproj", "-c", "Release", "--nologo"], cwd=output, check=True)
        subprocess.run([str(output / "bin" / "Release" / "net48" / "Harness.exe")], cwd=output, check=True)


if __name__ == "__main__":
    main()
