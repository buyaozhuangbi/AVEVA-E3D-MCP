"""Offline Python contracts only: no SDK, live network or native subprocess."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.team_database_identity import (
    DEFAULT_LIMIT, OPERATION, PATH, _parameters, route_wiring_contract, team_database_identity_manifest,
    validate_team_database_identity_manifest,
)

SESSION = "offline-team-db-session"
BUILD = "12438d22-d714-442e-9c51-272285ab238c"
CONTEXT = hashlib.sha256(f"{SESSION}\nPLANT\nALL".encode()).hexdigest()
ARGS = {"expected_context": CONTEXT}
ROOT = Path(__file__).resolve().parents[1]


class NoIOBridge:
    def __getattribute__(self, name):
        raise AssertionError(f"Unwired wrapper accessed bridge.{name}")


def identity(number, kind, *, database=5, storage="System", code=10):
    # These codes match installed E3D21 DbType.System/Global metadata.
    refno = f"={database}/{number}"
    return {"refno": refno, "type": kind, "db_type": storage,
            "db_type_code": code, "identity_key": f"{code}|{refno}|{kind}"}


def error(path, code="read_unavailable", message="Offline read failed."):
    return {"path": path, "code": code, "message": message}


def scan(count, *, limit=DEFAULT_LIMIT):
    available = count is not None and count <= 10000
    returned = min(count, limit) if available else 0
    return {"available": available, "materialized_count": count, "returned_count": returned,
            "prefix_complete": available and returned == count,
            "has_more": returned < count if available else None, "materializes_before_limit": True}


def database(index, name, extno, matched):
    team = identity(10, "TEAM")
    return {
        "index": index, "identity": identity(30 + index, "DB"), "namedb": "/*" + name,
        "canonical_name": name, "dbno": 77, "fino": 900 + index, "extno": extno,
        "extract_owner_state": "none" if extno == 0 else "observed",
        "extown": None if extno == 0 else identity(30, "DB"),
        "extract_kind": "master_candidate" if extno == 0 else "extract_candidate",
        "owner_path": [identity(20, "DBLI"), team],
        "team_relation": {"status": "confirmed", "source": "typed DB.Owner(DBLI).Owner(TEAM)",
                          "owner_chain_supported": True, "observed_team": deepcopy(team),
                          "listed_team_index": 0, "name_agreement": True, "confirmed_team": deepcopy(team)},
        "current_mdb": {"status": "indeterminate", "match_fields": ["canonical_name", "DBNO/Number", "raw_EXTNO/raw_ExtractNumber"],
                        "candidate_indexes": [matched], "matched_index": None, "semantic_equivalence_verified": False},
        "read_errors": [],
    }


def mdb(index, name, extract):
    return {"index": index, "name": name, "canonical_name": name, "database_number": 77,
            "extract_number": extract, "is_extract": extract > 0, "identity_coherent": None, "read_errors": []}


@pytest.fixture
def reply():
    boundary = {"project": "PLANT", "mdb": "ALL", "context_id": CONTEXT,
                "ce": identity(1, "EQUI", database=1, storage="Design", code=1), "ce_identity_key": "1|=1/1|EQUI"}
    return {"ok": True, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD, "result": {
        "operation": OPERATION, "schema_version": 1, "read_only": True, "single_dispatch": True, "atomic_snapshot": False,
        "source": "SDK DbSystem.GetAll(TEAM/DB) and current MDB.GetDBArray", "limit_per_source": DEFAULT_LIMIT, "owner_hop_limit": 2,
        "scans": {"teams": scan(2), "databases": scan(3), "current_mdb": scan(3)},
        "teams": [{"index": 0, "identity": identity(10, "TEAM"), "namn": "/*PIPING", "canonical_name": "PIPING", "read_errors": []},
                  {"index": 1, "identity": identity(11, "TEAM"), "namn": "/CATALOGUE", "canonical_name": "CATALOGUE", "read_errors": []}],
        "databases": [database(0, "PIPING/BASE", 0, 1), database(1, "PIPING/WORK", 1, 2), database(2, "PIPING/OTHER", 2, 0)],
        "current_mdb": [mdb(0, "PIPING/OTHER", 2), mdb(1, "PIPING/BASE", 0), mdb(2, "PIPING/WORK", 1)],
        "context": {"before": boundary, "after": deepcopy(boundary), "boundary_agreement": True, "session_id": SESSION, "bridge_build": BUILD},
        "complete": True, "identity_consistent": True, "all_team_relations_confirmed": True, "success": True,
        "read_errors": [], "identity_findings": [],
        "scope": {"kind": "bounded_sdk_visible_arrays", "project_coverage_verified": False, "world_level_equivalence_inspected": False,
                  "admin_permission_verified": False, "file_existence_verified": False, "database_creation_verified": False,
                  "extract_number_semantics_verified": False, "mdb_record_join_verified": False},
    }}


def validate(reply, args=None, **bindings):
    return validate_team_database_identity_manifest(reply, args or ARGS, expected_session=bindings.get("session", SESSION), expected_build=bindings.get("build", BUILD))


def partial(result):
    result["complete"] = result["success"] = False


def indeterminate_mdb(result):
    for row in result["databases"]:
        row["current_mdb"].update(status="indeterminate", matched_index=None)


def assert_unknown(observed):
    for row in observed["positive_matches"]:
        assert row["team"] is row["current_mdb"] is row["extract_identity"] is None
        assert row["team_identity"] is row["current_mdb_index"] is row["extract_owner"] is None
        assert row["extract_kind"] == "unknown"


def test_mock_is_explicitly_unavailable_and_contract_describes_public_route():
    class MockBridge:
        settings = Settings(mode="mock", version="E3D21")

        def __getattr__(self, name):
            raise AssertionError(f"Mock wrapper accessed bridge.{name}")

    result = team_database_identity_manifest(MockBridge(), ARGS)
    assert result["status"] == "unavailable" and result["reason"] == "native_unavailable"
    assert result["available"] is result["complete"] is result["success"] is False
    assert result["single_dispatch"] is None and result["native_observation"] is None
    assert result["positive_matches"] == [] and result["request"]["limit"] == 64
    contract = result["route_wiring_contract"]
    assert contract["wired"] is True and contract["public_http_route"] == PATH
    assert "ReadTeamDatabaseIdentityManifest(sessionId, expected_context, limit)" in contract["dispatch"]
    assert set(contract["request_parameters"]) == {"expected_context", "limit"}
    assert "bridge_build" in contract["envelope_fields"] and "sessionId" in contract["server_arguments"]
    contract["limits"]["per_source"] = 999
    assert route_wiring_contract()["limits"]["per_source"] == 200


def test_contract_matches_public_optional_interface_and_route():
    native = (ROOT / "bridge/e3d21/E3d21TeamDatabaseIdentityManifest.cs").read_text(encoding="utf-8-sig")
    managed = (ROOT / "bridge/e3d21/TeamDatabaseIdentityManifest.cs").read_text(encoding="utf-8-sig")
    adapter = (ROOT / "bridge/e3d21/E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
    assert "public object ReadTeamDatabaseIdentityManifest(string sessionId, string expectedContext, int limit = 64)" in native
    assert "public interface IE3d21TeamDatabaseIdentityManifestContext" in managed
    assert route_wiring_contract()["native_method"] + ";" in managed
    assert PATH in adapter and "ReadTeamDatabaseIdentityManifest" in adapter
    assert "team_database_identity_manifest" in adapter


@pytest.mark.parametrize("args", [None, [], {}, {"expected_context": ""}, {"expected_context": "A" * 64},
    {"expected_context": "a" * 63}, {"expected_context": "a" * 64 + "\n"}, {"expected_context": 1},
    *[{**ARGS, "limit": value} for value in (True, False, 0, -1, 201, "1", 1.0, None)],
    *[{**ARGS, key: "x"} for key in ("offset", "sessionId", "session_id", "bridge_build", "team", "name")]])
def test_invalid_native_arguments_fail_without_io(args):
    with pytest.raises(ValueError):
        team_database_identity_manifest(NoIOBridge(), args)


@pytest.mark.parametrize("limit", [1, 64, 200])
def test_valid_limit_boundaries_are_preserved_without_normalization(limit):
    assert _parameters({**ARGS, "limit": limit}) == {**ARGS, "limit": limit}


def test_positive_typed_chain_keeps_raw_mdb_tuple_equivalence_unknown(reply):
    original = deepcopy(reply)
    result = validate(reply)
    assert result["status"] == "observed" and result["complete"] is True
    assert result["native_observation"] is reply["result"] and reply == original
    rows = result["positive_matches"]
    assert all(row["current_mdb_index"] is row["current_mdb"] is None for row in rows)
    assert [row["current_mdb"]["candidate_indexes"] for row in reply["result"]["databases"]] == [[1], [2], [0]]
    assert all(row["team"] is True and row["extract_identity"] is None for row in rows)
    assert all(row["team_identity"]["type"] == "TEAM" for row in rows)
    assert [row["extract_kind"] for row in rows] == ["master_candidate", "extract_candidate", "extract_candidate"]
    assert rows[0]["extract_owner"] is None and rows[1]["extract_owner"]["type"] == "DB"
    assert result["project_coverage_verified"] is False


@pytest.mark.parametrize("raw", ["PIPING/BASE", "/PIPING/BASE", "/*piping/base"])
def test_documented_name_prefixes_and_case_only(reply, raw):
    reply["result"]["databases"][0]["namedb"] = raw
    observed = validate(reply)
    assert observed["positive_matches"][0]["current_mdb"] is None
    assert observed["native_observation"]["databases"][0]["current_mdb"]["candidate_indexes"] == [1]


def test_global_typed_storage_requires_installed_sdk_code(reply):
    def convert(value):
        if isinstance(value, dict):
            if value.get("db_type") == "System":
                value.update(db_type="Global", db_type_code=11, identity_key=f"11|{value['refno']}|{value['type']}")
            for child in value.values():
                convert(child)
        elif isinstance(value, list):
            for child in value:
                convert(child)
    convert(reply)
    assert validate(reply)["positive_matches"][0]["team_identity"]["db_type_code"] == 11


def test_fino_is_not_extract_number_or_a_join_key(reply):
    reply["result"]["databases"][1]["fino"] = 0
    observed = validate(reply)
    assert observed["positive_matches"][1]["current_mdb_index"] is None
    assert observed["native_observation"]["databases"][1]["current_mdb"]["candidate_indexes"] == [2]


@pytest.mark.parametrize("storage,code", [("System", 11), ("Global", 119), ("Design", 10),
                                          ("Catalogue", 1), ("Unknown", 0), ("Invalid", -1)])
def test_self_consistent_forged_or_unknown_native_storage_codes_fail_closed(reply, storage, code):
    identity_value = reply["result"]["teams"][0]["identity"]
    identity_value.update(db_type=storage, db_type_code=code,
                          identity_key=f"{code}|{identity_value['refno']}|TEAM")
    with pytest.raises(ValueError):
        validate(reply)


@pytest.mark.parametrize("storage,code", [("Global", 119), ("Unknown", 0), ("Design", 11)])
def test_typed_ce_storage_also_requires_installed_enum_identity(reply, storage, code):
    for boundary in ("before", "after"):
        value = reply["result"]["context"][boundary]["ce"]
        value.update(db_type=storage, db_type_code=code, identity_key=f"{code}|{value['refno']}|EQUI")
        reply["result"]["context"][boundary]["ce_identity_key"] = value["identity_key"]
    with pytest.raises(ValueError):
        validate(reply)


def test_legitimate_primary_number_one_and_false_extract_flag_remain_independent(reply):
    result = reply["result"]
    result["databases"][0]["extno"] = 1
    result["current_mdb"][1].update(extract_number=1, is_extract=False)
    observed = validate(reply)
    assert observed["success"] is True and observed["complete"] is True
    assert result["databases"][0]["extract_kind"] == "master_candidate"
    assert result["current_mdb"][1]["identity_coherent"] is None
    assert result["identity_findings"] == []
    assert result["databases"][0]["current_mdb"]["candidate_indexes"] == [1]
    assert result["databases"][0]["current_mdb"]["semantic_equivalence_verified"] is False
    assert observed["positive_matches"][0]["team"] is True
    assert observed["positive_matches"][0]["current_mdb"] is None
    assert observed["positive_matches"][0]["extract_identity"] is None
    assert observed["positive_matches"][0]["extract_kind"] == "master_candidate"
    assert observed["positive_matches"][0]["extract_owner"] is None
    assert result["scope"]["extract_number_semantics_verified"] is False
    assert result["scope"]["mdb_record_join_verified"] is False


def test_extract_owner_evidence_does_not_depend_on_extno_nonzero(reply):
    result = reply["result"]
    result["databases"][1]["extno"] = 0
    result["databases"][1]["current_mdb"]["candidate_indexes"] = []
    observed = validate(reply)
    assert observed["success"] is True
    assert observed["positive_matches"][1]["extract_identity"] is None
    assert observed["positive_matches"][1]["extract_kind"] == "extract_candidate"
    assert observed["positive_matches"][1]["extract_owner"]["type"] == "DB"


def test_self_extract_owner_is_unknown_without_inventing_number_inconsistency(reply):
    result = reply["result"]
    result["databases"][1]["extown"] = deepcopy(result["databases"][1]["identity"])
    result["databases"][1]["extract_kind"] = "unknown"
    observed = validate(reply)
    assert result["identity_findings"] == []
    assert observed["positive_matches"][1]["extract_identity"] is None


@pytest.mark.parametrize("flag", [True, False])
def test_extract_number_coherence_cannot_be_claimed_without_verified_semantics(reply, flag):
    reply["result"]["current_mdb"][0]["identity_coherent"] = flag
    with pytest.raises(ValueError):
        validate(reply)


@pytest.mark.parametrize("flag", ["extract_number_semantics_verified", "mdb_record_join_verified"])
def test_scope_cannot_promote_unknown_number_semantics_or_mdb_joins(reply, flag):
    reply["result"]["scope"][flag] = True
    with pytest.raises(ValueError):
        validate(reply)


def test_database_identity_prefix_may_hide_duplicates_and_prevents_team_confirmation(reply):
    result = reply["result"]
    for source in ("teams", "databases", "current_mdb"):
        result[source] = result[source][:1]
        result["scans"][source] = scan(2 if source == "databases" else 1, limit=1)
    result["current_mdb"][0] = mdb(0, "PIPING/BASE", 0)
    row = result["databases"][0]
    row["current_mdb"]["candidate_indexes"] = [0]
    row["team_relation"].update(status="unverified", confirmed_team=None)
    result.update(limit_per_source=1, all_team_relations_confirmed=False)
    partial(result)
    assert_unknown(validate(reply, {**ARGS, "limit": 1}))
    assert row["team_relation"]["observed_team"]["type"] == "TEAM"
    row["team_relation"].update(status="confirmed", confirmed_team=deepcopy(row["owner_path"][1]))
    with pytest.raises(ValueError):
        validate(reply, {**ARGS, "limit": 1})


def test_unknown_record_identity_may_hide_duplicate_and_keeps_other_owner_observations(reply):
    result = reply["result"]
    missing = result["databases"][2]
    missing.update(identity=None, namedb=None, canonical_name=None, dbno=None, fino=None, extno=None,
                   extract_owner_state="unknown", extown=None, extract_kind="unknown", owner_path=[],
                   read_errors=[error("databases[2].identity")])
    missing["team_relation"].update(status="unverified", owner_chain_supported=False, observed_team=None,
                                    listed_team_index=None, name_agreement=None, confirmed_team=None)
    missing["current_mdb"]["candidate_indexes"] = []
    for row in result["databases"][:2]:
        row["team_relation"].update(status="unverified", confirmed_team=None)
    result["read_errors"] = deepcopy(missing["read_errors"])
    result["all_team_relations_confirmed"] = False
    partial(result)
    assert_unknown(validate(reply))
    assert result["databases"][0]["team_relation"]["observed_team"]["type"] == "TEAM"
    row = result["databases"][0]
    row["team_relation"].update(status="confirmed", confirmed_team=deepcopy(row["owner_path"][1]))
    with pytest.raises(ValueError):
        validate(reply)


@pytest.mark.parametrize("changed", ["extract_number", "database_number", "full_name"])
def test_full_tuple_mismatch_is_unknown_never_absent(reply, changed):
    result = reply["result"]
    row = result["current_mdb"][2]
    if changed == "full_name":
        row.update(name="CATALOGUE/WORK", canonical_name="CATALOGUE/WORK")
    else:
        row[changed] += 1
    result["databases"][1]["current_mdb"].update(status="indeterminate", candidate_indexes=[], matched_index=None)
    observed = validate(reply)
    match = observed["positive_matches"][1]
    assert observed["complete"] is True and match["team"] is True
    assert match["current_mdb"] is None and match["current_mdb_index"] is None
    assert observed["native_observation"]["databases"][1]["current_mdb"]["status"] == "indeterminate"


def test_extract_owner_may_be_typed_but_outside_returned_catalog(reply):
    reply["result"]["databases"][1]["extown"] = identity(500, "DB", database=9, storage="Global", code=11)
    observed = validate(reply)["positive_matches"][1]
    assert observed["extract_identity"] is None and observed["extract_kind"] == "extract_candidate"
    assert observed["extract_owner"]["refno"] == "=9/500"


def test_prefix_with_observed_candidate_stays_unknown(reply):
    result = reply["result"]
    for source in ("teams", "databases", "current_mdb"):
        result[source] = result[source][:1]
        result["scans"][source] = scan(2 if source == "teams" else 3, limit=1)
    result["current_mdb"][0] = mdb(0, "PIPING/BASE", 0)
    result["databases"][0]["team_relation"].update(status="unverified", confirmed_team=None)
    result["databases"][0]["current_mdb"].update(status="indeterminate", candidate_indexes=[0], matched_index=None)
    result.update(limit_per_source=1, all_team_relations_confirmed=False)
    partial(result)
    observed = validate(reply, {**ARGS, "limit": 1})
    assert observed["status"] == "partial" and not observed["complete"]
    assert_unknown(observed)


@pytest.mark.parametrize("count", [None, 10001])
def test_unavailable_or_oversized_materialized_array_does_not_mean_empty_catalog(reply, count):
    result = reply["result"]
    result["teams"] = []
    result["scans"]["teams"] = scan(count)
    result["read_errors"] = [error("teams.array", "array_unavailable")]
    for row in result["databases"]:
        row["team_relation"].update(status="unverified", listed_team_index=None, name_agreement=None, confirmed_team=None)
    result["all_team_relations_confirmed"] = False
    partial(result)
    assert_unknown(validate(reply))


def test_empty_readable_arrays_establish_no_absence_claim(reply):
    result = reply["result"]
    for source in ("teams", "databases", "current_mdb"):
        result[source] = []
        result["scans"][source] = scan(0)
    observed = validate(reply)
    assert observed["complete"] is True and observed["positive_matches"] == []
    assert observed["project_coverage_verified"] is False


def test_matched_row_with_fino_read_error_remains_unknown(reply):
    result = reply["result"]
    row = result["databases"][0]
    row["fino"] = None
    row["read_errors"] = [error("databases[0].FINO")]
    result["read_errors"] = deepcopy(row["read_errors"])
    partial(result)
    observed = validate(reply)
    assert row["current_mdb"]["status"] == "indeterminate"
    assert_unknown(observed)


def test_missing_extno_is_unknown_instead_of_master_zero(reply):
    result = reply["result"]
    row = result["databases"][0]
    row.update(extno=None, read_errors=[error("databases[0].EXTNO")])
    result["read_errors"] = deepcopy(row["read_errors"])
    row["current_mdb"]["candidate_indexes"] = []
    indeterminate_mdb(result)
    partial(result)
    assert_unknown(validate(reply))


@pytest.mark.parametrize("raw", ["BASE", "//PIPING/BASE", "/*PIPING/BASE/EXTRA", "PIPING/*", "PIPING/UNSET", "PIPING/BASE "])
def test_malformed_native_name_with_matching_error_is_partial(reply, raw):
    result = reply["result"]
    row = result["databases"][0]
    row.update(namedb=raw, canonical_name=None, read_errors=[error("databases[0].canonical_name")])
    row["team_relation"].update(status="unverified", name_agreement=None, confirmed_team=None)
    row["current_mdb"]["candidate_indexes"] = []
    result["read_errors"] = deepcopy(row["read_errors"])
    result["all_team_relations_confirmed"] = False
    indeterminate_mdb(result)
    partial(result)
    assert_unknown(validate(reply))


def test_partial_owner_chain_is_retained_without_name_based_confirmation(reply):
    result = reply["result"]
    row = result["databases"][0]
    row["owner_path"] = [identity(20, "EQUI")]
    row["team_relation"].update(status="unverified", owner_chain_supported=False, observed_team=None,
                                listed_team_index=None, name_agreement=None, confirmed_team=None)
    row["read_errors"] = [error("databases[0].owner_path")]
    result["read_errors"] = deepcopy(row["read_errors"])
    result["all_team_relations_confirmed"] = False
    partial(result)
    assert_unknown(validate(reply))


def test_same_team_name_cannot_replace_typed_owner_identity(reply):
    result = reply["result"]
    result["teams"][0]["identity"] = identity(99, "TEAM")
    for row in result["databases"]:
        row["team_relation"].update(status="unverified", listed_team_index=None, name_agreement=None, confirmed_team=None)
    result.update(all_team_relations_confirmed=False, success=False)
    assert_unknown(validate(reply))


def test_native_team_name_conflicts_are_preserved_as_unknown(reply):
    result = reply["result"]
    result["teams"][0].update(namn="/WIRING", canonical_name="WIRING")
    for row in result["databases"]:
        row["team_relation"].update(status="conflict", name_agreement=False, confirmed_team=None)
        result["identity_findings"].append(error(f"databases[{row['index']}].team_relation", "conflict"))
    result.update(identity_consistent=False, all_team_relations_confirmed=False, success=False)
    assert_unknown(validate(reply))


@pytest.mark.parametrize("target", ["db_type", "dbref", "base_type"])
def test_supported_owner_chain_must_share_database_and_native_types(reply, target):
    row = reply["result"]["databases"][0]
    old = row["owner_path"][0]
    row["owner_path"][0] = identity(20, "SECT" if target == "base_type" else "DBLI",
        database=6 if target == "dbref" else 5, storage="Global" if target == "db_type" else "System", code=11 if target == "db_type" else 10)
    assert old != row["owner_path"][0]
    with pytest.raises(ValueError):
        validate(reply)


@pytest.mark.parametrize("mutation", ["untyped_owner", "self_owner", "wrong_key", "fino_as_extno"])
def test_invented_extract_evidence_is_rejected(reply, mutation):
    row = reply["result"]["databases"][1]
    if mutation == "untyped_owner":
        row["extown"] = identity(30, "EQUI")
    elif mutation == "self_owner":
        row["extown"] = deepcopy(row["identity"])
    elif mutation == "wrong_key":
        row["identity"]["identity_key"] = "=5/31|DB"  # Another prototype's key format is incompatible.
    else:
        row["extno"] = row["fino"]
    with pytest.raises(ValueError):
        validate(reply)


def test_duplicate_mdb_raw_tuple_keeps_all_candidates_without_claiming_semantic_ambiguity(reply):
    result = reply["result"]
    duplicate = deepcopy(result["current_mdb"][1])
    duplicate["index"] = 3
    result["current_mdb"].append(duplicate)
    result["scans"]["current_mdb"] = scan(4)
    result["databases"][0]["current_mdb"].update(candidate_indexes=[1, 3])
    observed = validate(reply)
    assert observed["complete"] is True and observed["success"] is True
    assert result["identity_findings"] == []
    assert observed["positive_matches"][0]["team"] is True
    assert observed["positive_matches"][0]["current_mdb"] is None


def test_duplicate_typed_db_identity_requires_all_native_findings(reply):
    result = reply["result"]
    duplicate = deepcopy(result["databases"][0])
    duplicate["index"] = 3
    result["databases"].append(duplicate)
    result["scans"]["databases"] = scan(4)
    with pytest.raises(ValueError):
        validate(reply)  # A superficially matched duplicated record is invalid.
    for index in (0, 3):
        row = result["databases"][index]
        row["team_relation"].update(status="ambiguous", confirmed_team=None)
        result["identity_findings"].extend([error(f"databases[{index}]", "duplicate_typed_identity"),
            error(f"databases[{index}].team_relation", "ambiguous")])
    result.update(identity_consistent=False, all_team_relations_confirmed=False, success=False)
    assert_unknown(validate(reply))


@pytest.mark.parametrize("section", ["envelope_session", "envelope_build", "native_session", "native_build", "expected_context", "ce_key", "version"])
def test_independent_context_and_envelope_bindings_are_required(reply, section):
    if section.startswith("envelope_"):
        reply["session_id" if section.endswith("session") else "bridge_build"] = "wrong"
    elif section.startswith("native_"):
        reply["result"]["context"]["session_id" if section.endswith("session") else "bridge_build"] = "wrong"
    elif section == "expected_context":
        with pytest.raises(ValueError):
            validate(reply, {"expected_context": "0" * 64})
        return
    elif section == "ce_key":
        reply["result"]["context"]["before"]["ce_identity_key"] = "=1/1|EQUI"
    else:
        reply["version"] = "E3D31"
    with pytest.raises(ValueError):
        validate(reply)


@pytest.mark.parametrize("unreadable", [False, True])
def test_changed_or_unreadable_after_ce_keeps_all_evidence_unknown(reply, unreadable):
    result = reply["result"]
    context = result["context"]
    context["boundary_agreement"] = False
    context["after"]["ce"] = None if unreadable else identity(2, "EQUI", database=1, storage="Design", code=1)
    context["after"]["ce_identity_key"] = None if unreadable else "1|=1/2|EQUI"
    result["read_errors"] = [error("context", "context_changed_or_unreadable")]
    if unreadable:
        result["read_errors"].append(error("after.ce"))
    for row in result["databases"]:
        row["team_relation"].update(status="context_unverified", confirmed_team=None)
    result["all_team_relations_confirmed"] = False
    indeterminate_mdb(result)
    partial(result)
    assert_unknown(validate(reply))


@pytest.mark.parametrize("mutation", ["limit", "array_cap", "return_count", "hop_cap", "owner_path", "candidate_cap", "matched_index", "bool_index", "scope", "flags"])
def test_caps_indexes_and_overclaims_fail_closed(reply, mutation):
    result = reply["result"]
    if mutation == "limit":
        result["limit_per_source"] = 200
    elif mutation == "array_cap":
        result["scans"]["databases"]["materialized_count"] = 10001
    elif mutation == "return_count":
        result["scans"]["databases"]["returned_count"] = 2
    elif mutation == "hop_cap":
        result["owner_hop_limit"] = 3
    elif mutation == "owner_path":
        result["databases"][0]["owner_path"].append(identity(50, "WORL"))
    elif mutation == "candidate_cap":
        result["databases"][0]["current_mdb"]["candidate_indexes"] = list(range(201))
    elif mutation == "matched_index":
        result["databases"][0]["current_mdb"]["matched_index"] = True
    elif mutation == "bool_index":
        result["teams"][0]["index"] = False
    elif mutation == "scope":
        result["scope"]["admin_permission_verified"] = True
    else:
        result["complete"] = 1
    with pytest.raises(ValueError):
        validate(reply)


@pytest.mark.parametrize("mutation", ["error_count", "error_text", "row_error_cap", "global_only", "row_only", "unmatched_path", "missing_value"])
def test_read_error_caps_and_matched_row_error_coverage(reply, mutation):
    result = reply["result"]
    row = result["databases"][0]
    if mutation == "error_count":
        result["read_errors"] = [error("system")] * (16 * DEFAULT_LIMIT + 33)
    elif mutation == "error_text":
        result["read_errors"] = [error("system", message="X" * 513)]
    elif mutation == "row_error_cap":
        row["read_errors"] = [error("databases[0].FINO")] * 9
        result["read_errors"] = deepcopy(row["read_errors"])
    elif mutation == "global_only":
        result["read_errors"] = [error("databases[0].FINO")]
    elif mutation == "row_only":
        row["read_errors"] = [error("databases[0].FINO")]
    elif mutation == "unmatched_path":
        result["read_errors"] = [error("databases[200].FINO")]
    else:
        row["fino"] = None
    partial(result)
    with pytest.raises(ValueError):
        validate(reply)


@pytest.mark.parametrize("marker", [{"has_fault": True}, {"native_fault": True}, {"native_fault_latched": True},
                                  {"error_code": "native_database_fault"}, {"code": "native_fault"}])
def test_recursive_fault_markers_precede_schema_validation_or_availability(marker):
    envelope = {"ok": False, "result": [{"details": [marker]}]}
    with pytest.raises(BridgeError) as caught:
        validate_team_database_identity_manifest(envelope, {}, expected_session="", expected_build="")
    assert caught.value.diagnostics is envelope
    assert "do not retry" in str(caught.value)


def test_fault_inside_oversized_read_errors_is_not_hidden_by_cap(reply):
    reply["result"]["read_errors"] = [error("system")] * 2000 + [{"details": [{"native_fault_latched": True}]}]
    with pytest.raises(BridgeError) as caught:
        validate(reply)
    assert caught.value.diagnostics is reply


@pytest.mark.parametrize("status", [None, 404, 500])
def test_native_fault_bridge_error_is_rethrown_unchanged_even_for_404(status):
    original = BridgeError("native stop", http_status=status, diagnostics={"state": {"nested": [{"has_fault": True}], "retry_safe": False}})
    with pytest.raises(BridgeError) as caught:
        validate(original)
    assert caught.value is original and caught.value.diagnostics["state"]["retry_safe"] is False


def test_nonfault_404_is_unavailable_and_other_bridge_errors_are_unchanged():
    assert validate(BridgeError("Not found", http_status=404))["reason"] == "native_feature_unavailable"
    original = BridgeError("Offline transport error", transport_error=True)
    with pytest.raises(BridgeError) as caught:
        validate(original)
    assert caught.value is original


def test_fault_scan_handles_deep_and_cyclic_offline_objects():
    envelope = {}
    cursor = envelope
    for _ in range(1500):
        cursor["nested"] = [{}]
        cursor = cursor["nested"][0]
    cursor["self"] = envelope
    cursor["native_fault_latched"] = True
    with pytest.raises(BridgeError) as caught:
        validate(envelope)
    assert caught.value.diagnostics is envelope
    cycle = {}
    cycle["self"] = cycle
    with pytest.raises(ValueError):
        validate(cycle)
