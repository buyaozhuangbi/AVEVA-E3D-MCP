from __future__ import annotations

import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.paragon_create_preview import (
    PATH, TOOL, _validate,
    paragon_element_create_preview, register_paragon_element_create_preview,
)
from aveva_e3d_mcp.protocol import McpServer


SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=False)
FIXTURE = Path(__file__).with_name("fixtures") / "paragon_element_create_preview.json"


def sample():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def call(args):
    server = McpServer("paragon-create-preview-test", "0")
    register_paragon_element_create_preview(server, BridgeClient(SETTINGS))
    return server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": TOOL, "arguments": args}})["result"]


def test_preview_tool_has_explicit_target_and_read_only_annotation():
    server = McpServer("paragon-create-preview-test", "0")
    register_paragon_element_create_preview(server, BridgeClient(SETTINGS))
    tool, = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]
    assert tool["name"] == TOOL and tool["annotations"]["readOnlyHint"] is True
    assert tool["inputSchema"]["required"] == ["refno", "element_type"]
    assert tool["inputSchema"]["additionalProperties"] is False

    native = sample()
    with patch.object(BridgeClient, "_request", return_value=native) as request:
        response = call({"refno": "=1/100", "element_type": "sect"})
    assert not response.get("isError"), response
    request.assert_called_once_with("POST", PATH, {"refno": "=1/100", "element_type": "SECT"}, version="E3D21")


def test_explicit_numeric_reference_is_normalized_and_sent_once():
    native = sample()
    with patch.object(BridgeClient, "_request", return_value=native) as request:
        response = paragon_element_create_preview(BridgeClient(SETTINGS), {"refno": "=01/0100", "element_type": " sect "})
    request.assert_called_once_with("POST", PATH, {"refno": "=1/100", "element_type": "SECT"}, version="E3D21")
    assert response == native
    _validate(response, {"refno": "=1/100", "element_type": "SECT"})


def test_named_target_must_match_resolved_native_name():
    response = sample()
    result = response["result"]
    result["requested_refno"] = result["target"]["requested_refno"] = "/cat/section"
    result["target"]["resolved_name"] = "/CAT/SECTION"
    _validate(response, {"refno": "/cat/section", "element_type": "SECT"})

    result["target"]["resolved_name"] = "/CAT"
    result["target"]["resolved_refno"] = "=1/101"
    result["target"]["target_matches_request"] = True
    with pytest.raises(ValueError):
        _validate(response, {"refno": "/cat/section", "element_type": "SECT"})


def test_success_is_only_sdk_observation_and_has_unknown_module_permission_revision():
    response = sample()
    _validate(response, {"refno": "=1/100", "element_type": "SECT"})
    result = response["result"]
    assert result["candidate"]["type"] == "SECT"
    assert result["candidate"]["member_type"] is True
    assert result["candidate"]["sdk_is_creatable_now"] is True
    assert result["scope"]["module"] is None
    assert result["scope"]["permissions_verified"] is None
    assert result["scope"]["revision"] is None
    assert result["scope"]["pml_executed"] is False
    assert result["scope"]["element_creation_attempted"] is False


def test_member_types_read_failure_stays_unknown_and_does_not_claim_false():
    response = sample()
    result = response["result"]
    row = result["candidate"]
    row.update(member_type=None,
               errors={"member_types": "offline fake failure"}, complete=False)
    row.update(sdk_is_creatable_now=True)
    result["read_errors"]["candidate"] = row["errors"]
    result["complete"] = result["success"] = False
    _validate(response, {"refno": "=1/100", "element_type": "SECT"})
    assert row["member_type"] is None and row["sdk_is_creatable_now"] is True


def test_is_creatable_failure_stays_unknown_instead_of_false():
    response = sample()
    result = response["result"]
    row = result["candidate"]
    row.update(sdk_is_creatable_now=None, errors={"is_creatable": "offline fake failure"}, complete=False)
    result["read_errors"]["candidate"] = row["errors"]
    result["complete"] = result["success"] = False
    _validate(response, {"refno": "=1/100", "element_type": "SECT"})
    assert row["sdk_is_creatable_now"] is None


def test_unresolved_target_keeps_candidate_unknown():
    response = sample()
    result = response["result"]
    result["target"].update(resolved_refno=None, resolved_name=None, element_type=None, database_type=None,
                            database_type_code=None, target_matches_request=None, catalogue_target=None,
                            errors={"resolve": "offline fake failure"}, complete=False)
    row = result["candidate"]
    row.update(member_type=None, sdk_is_creatable_now=None,
               errors={"member_types": "not queried", "target": "unresolved"}, complete=False)
    result["read_errors"] = {"target": result["target"]["errors"], "candidate": row["errors"]}
    result["complete"] = result["success"] = False
    _validate(response, {"refno": "=1/100", "element_type": "SECT"})


@pytest.mark.parametrize("args", [
    {}, {"refno": "CE", "element_type": "SECT"}, {"refno": "/", "element_type": "SECT"},
    {"refno": "/CAT/*", "element_type": "SECT"}, {"refno": "=0/1", "element_type": "SECT"},
    {"refno": "=1/-1", "element_type": "SECT"}, {"refno": "=1/2147483648", "element_type": "SECT"},
    {"refno": "/CAT", "element_type": "OTHER"}, {"refno": "/CAT", "element_type": "CATE", "revision": 1},
    {"refno": None, "element_type": "CATA"}, {"refno": "/CAT"},
])
def test_invalid_or_implicit_target_never_contacts_bridge(args):
    with patch.object(BridgeClient, "_request") as request:
        with pytest.raises((ValueError, BridgeError)):
            paragon_element_create_preview(BridgeClient(SETTINGS), args)
    request.assert_not_called()


def test_response_cannot_upgrade_unknown_permissions_or_sdk_read_errors():
    response = sample()
    response["result"]["scope"]["permissions_verified"] = True
    with pytest.raises(ValueError):
        _validate(response, {"refno": "=1/100", "element_type": "SECT"})

    response = sample()
    response["result"]["candidate"]["errors"] = {"member_types": "read failed"}
    with pytest.raises(ValueError):
        _validate(response, {"refno": "=1/100", "element_type": "SECT"})


def test_catalogue_database_target_is_required():
    response = sample()
    response["result"]["target"]["database_type"] = "Design"
    with pytest.raises(ValueError):
        _validate(response, {"refno": "=1/100", "element_type": "SECT"})
