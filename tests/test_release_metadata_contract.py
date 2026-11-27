"""Keep source, generated metadata and any local wheel in one release version."""
from __future__ import annotations

import csv
import hashlib
import io
import re
import sys
import tomllib
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aveva_e3d_mcp import __version__


def _project_version() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return project["project"]["version"]


def _wheel_version(wheel: Path) -> str:
    with ZipFile(wheel) as archive:
        metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        version = next(line.split(": ", 1)[1] for line in archive.read(metadata).decode().splitlines()
                       if line.startswith("Version: "))
        return version


def test_source_and_generated_package_metadata_agree():
    version = _project_version()
    assert re.fullmatch(r"0\.\d+\.\d+", version)
    assert __version__ == version
    egg_info = ROOT / "src" / "aveva_e3d_mcp.egg-info" / "PKG-INFO"
    if egg_info.is_file():
        metadata_version = next(line.split(": ", 1)[1] for line in egg_info.read_text(encoding="utf-8").splitlines()
                                if line.startswith("Version: "))
        assert metadata_version == version
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## v{version} — " in changelog


def test_local_wheels_have_matching_filename_metadata_and_record():
    version = _project_version()
    release_dir = ROOT / "build" / f"release-{version}"
    wheels = sorted(release_dir.glob("aveva_e3d_mcp-*.whl")) if release_dir.is_dir() else []
    if not wheels:
        return
    for wheel in wheels:
        assert wheel.name == f"aveva_e3d_mcp-{version}-py3-none-any.whl", wheel
        with ZipFile(wheel) as archive:
            assert _wheel_version(wheel) == version
            names = set(archive.namelist())
            record_name = next(name for name in names if name.endswith(".dist-info/RECORD"))
            rows = csv.reader(io.StringIO(archive.read(record_name).decode()))
            for name, digest, _size in rows:
                if not digest or name not in names:
                    continue
                algorithm, encoded = digest.split("=", 1)
                assert algorithm == "sha256"
                actual = hashlib.sha256(archive.read(name)).digest()
                import base64
                assert base64.urlsafe_b64encode(actual).rstrip(b"=").decode() == encoded, name
