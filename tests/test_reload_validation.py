"""Offline checks for SAVEWORK/DESIGN project identity validation."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from reload_validation import validate_project_reload


def project(**changes):
    value = {
        "name": "APS",
        "mdb": "ALL",
        "connected": True,
        "current_element": "=2013286676/4008",
    }
    value.update(changes)
    return value


def test_design_current_element_change_is_recorded_not_rejected():
    observation = validate_project_reload(project(), project(current_element="=2013286676/1810"))
    assert observation == {
        "current_element_before": "=2013286676/4008",
        "current_element_after": "=2013286676/1810",
        "current_element_changed": True,
        "project_mdb_unchanged": True,
    }


def test_same_current_element_is_recorded_as_unchanged():
    observation = validate_project_reload(project(), project())
    assert observation["current_element_changed"] is False
    assert observation["project_mdb_unchanged"] is True


@pytest.mark.parametrize("changes, message", [
    ({"name": "OTHER"}, "project name"),
    ({"mdb": "OTHER"}, "project mdb"),
    ({"connected": False}, "connected"),
])
def test_project_identity_or_connection_change_still_fails(changes, message):
    with pytest.raises(AssertionError, match=message):
        validate_project_reload(project(), project(**changes))
