import hashlib
import json
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aveva_e3d_mcp import __version__
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server
from catalog_compatibility import (CURRENT_MOCK_TOOL_COUNT, MODELING_TEAM_TOOLS, E3D_IDENTITY_TOOLS,
                                    with_modeling_additions, with_transform_additions)


def test_release_version_and_additive_tool_catalog():
    raw = (ROOT / "tests/fixtures/paragon_standard_generation_catalog119.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "d1611ddf9326cd48a41040428a0ca9fee55de10906bd9805f26592f62cc4784c"
    old = {row["name"]: row for row in json.loads(raw)["tools"]}
    server = create_server(Settings(mode="mock"))
    reply = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    current = {row["name"]: row for row in reply["result"]["tools"]}
    assert len(old) == 119
    assert len(reply["result"]["tools"]) == len(current) == CURRENT_MOCK_TOOL_COUNT
    assert set(current) - set(old) == {"e3d_paragon_generate_from_standard", "e3d_paragon_component_create", "e3d_paragon_component_create_status", "e3d_piping_route_create", "e3d_piping_route_verify", *MODELING_TEAM_TOOLS, *E3D_IDENTITY_TOOLS}
    assert {name: current[name] for name in old} == {
        name: with_transform_additions(with_modeling_additions(row)) for name, row in old.items()}
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert package["project"]["version"] == server.version == __version__ == "0.2.4"
