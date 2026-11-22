"""Validate bounded native generated-CSG descriptors without interpreting them.

No SDK objects, native calls, geometry conversion or cross-request cache belong
here. Raw RData/IData, transform values and typed SDK facet coordinates remain
observations; accepting this transport contract never admits a final mesh.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any


MAX_TREES = 16
MAX_ITEMS = 256
MAX_ARRAY_VALUES = 4096
MAX_TOTAL_ARRAY_VALUES = 65536
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
_FACET_GROUP_LIMITS = {
    "max_facets_per_group": 1024,
    "max_loops_per_facet": 128,
    "max_entries_per_loop_array": 1024,
    "max_total_facets": 2048,
    "max_total_loops": 4096,
    "max_total_coordinate_values": 16384,
}
_MAX_JSON_NODES = 150_000
_MAX_JSON_DEPTH = 24
_MAX_STRING_LENGTH = 8192
_INT32_MIN, _INT32_MAX = -(2 ** 31), 2 ** 31 - 1
_ROOT_TYPES = {"BOX", "CYLI", "CONE", "DISH", "NOZZ", "PANE", "PANEL"}
_UNVERIFIED_FLAGS = (
    "native_tessellation", "cuts_applied", "post_boolean_mesh_verified",
    "transform_convention_verified", "raw_data_layout_verified",
)
_BOOL_OPTIONS = {
    "hard_obstructions", "soft_obstructions", "center_line", "tube",
    "insulation", "obstruction", "holes", "solid_only", "pline", "wire_line",
}
_INT_OPTIONS = {"level", "insulation_translucency", "obstruction_translucency"}
_CSG_TYPES = dict(enumerate((
    "CSG_ANY_PRIMITIVE", "CSG_BOX", "CSG_CYLI", "CSG_CONE", "CSG_CTORUS",
    "CSG_RTORUS", "CSG_SPHERE", "CSG_SNOUT", "CSG_PYRAMID", "CSG_DISH",
    "CSG_POINT", "CSG_LINE", "CSG_POLYGON", "CSG_FACET", "CSG_SRECT",
    "CSG_SANNU", "CSG_EXTRUSION", "CSG_EXTRUSION_GROUP", "CSG_NURBS_SURF",
    "CSG_FACET_GROUP", "CSG_PLINE", "CSG_ARC", "CSG_SYMBOL",
)))
_CSG_TYPES.update({51 + index: "CSG_NEG_" + name for index, name in enumerate(
    ("BOX", "CYLI", "CONE", "CTORUS", "RTORUS", "SPHERE", "SNOUT", "PYRAMID", "DISH"))})
_CSG_TYPES.update({66: "CSG_NEG_EXTRUSION", 67: "CSG_NEG_EXTRUSION_GROUP", 68: "CSG_NEG_FACET_GROUP"})
_FACET_GROUP_TYPES = {19, 68}


class _InvalidReceipt(ValueError):
    def __init__(self, code: str, path: str):
        self.code, self.path = code, path
        super().__init__(code)


def _require(condition, code, path):
    if not condition:
        raise _InvalidReceipt(code, path)


def _text(value, path, *, maximum=1024, empty=False, controls=False):
    _require(type(value) is str and len(value) <= maximum and (empty or bool(value.strip()))
             and (controls or not any(ord(char) < 32 for char in value)), "generated_csg_invalid_text", path)
    return value


def _integer(value, path, minimum=0, maximum=_INT32_MAX):
    _require(type(value) is int and minimum <= value <= maximum, "generated_csg_invalid_integer", path)
    return value


def _number(value, path):
    try:
        valid = type(value) in {int, float} and math.isfinite(value)
    except (ValueError, OverflowError):
        valid = False
    _require(valid, "generated_csg_nonfinite_or_invalid_number", path)
    return value


def _reference(value, path):
    _text(value, path, maximum=128)
    match = re.fullmatch(r"=([0-9]+)/([0-9]+)", value)
    _require(match is not None, "generated_csg_numeric_identity_required", path)
    reference = tuple(int(part) for part in match.groups())
    _require(0 < reference[0] <= _INT32_MAX and 0 <= reference[1] <= _INT32_MAX,
             "generated_csg_numeric_identity_required", path)
    return reference


def _identity(value, path):
    _require(type(value) is dict, "generated_csg_source_identity_required", path)
    key = _reference(value.get("refno"), path + ".refno")
    _text(value.get("name"), path + ".name", maximum=4096, empty=True)
    _text(value.get("type"), path + ".type", maximum=64)
    return key


def _matches(requested, identity):
    if requested.startswith("="):
        return _reference(requested, "requested_refno") == _reference(identity["refno"], "source.refno")
    return requested.casefold() == ("/" + identity["name"].lstrip("/")).casefold()


def _json_copy(value):
    """Bound traversal before serializing/copying an untrusted receipt."""
    remaining, ancestors = _MAX_JSON_NODES, set()

    def visit(item, depth):
        nonlocal remaining
        remaining -= 1
        _require(remaining >= 0 and depth <= _MAX_JSON_DEPTH,
                 "generated_csg_json_structure_limit_exceeded", "response")
        kind = type(item)
        if kind in {dict, list}:
            _require(id(item) not in ancestors, "generated_csg_invalid_json", "response")
            _require(len(item) <= (256 if kind is dict else MAX_TOTAL_ARRAY_VALUES),
                     "generated_csg_json_structure_limit_exceeded", "response")
            ancestors.add(id(item))
            try:
                if kind is dict:
                    result = {}
                    for key, member in item.items():
                        _require(type(key) is str and len(key) <= 128, "generated_csg_invalid_json", "response")
                        result[key] = visit(member, depth + 1)
                    return result
                return [visit(member, depth + 1) for member in item]
            finally:
                ancestors.remove(id(item))
        if kind is str:
            _require(len(item) <= _MAX_STRING_LENGTH, "generated_csg_json_structure_limit_exceeded", "response")
            return item
        if kind in {float, int}:
            _number(item, "response")
            return item
        _require(item is None or kind is bool, "generated_csg_invalid_json", "response")
        return item

    copied = visit(value, 0)
    size = 0
    try:
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(copied):
            size += len(chunk.encode("utf-8"))
            _require(size <= MAX_RECEIPT_BYTES, "generated_csg_output_limit_exceeded", "response")
    except _InvalidReceipt:
        raise
    except (ValueError, UnicodeError, OverflowError):
        raise _InvalidReceipt("generated_csg_invalid_json", "response") from None
    return copied


def _errors(value, path):
    _require(type(value) is list and len(value) <= 256, "generated_csg_invalid_errors", path)
    for index, error in enumerate(value):
        here = f"{path}[{index}]"
        _require(type(error) is dict, "generated_csg_invalid_errors", here)
        for name in ("stage", "code", "message"):
            _text(error.get(name), here + "." + name, maximum=2048, empty=name == "message", controls=name == "message")
    return value


def _options(value, path):
    _require(type(value) is dict and set(value) == _BOOL_OPTIONS | _INT_OPTIONS,
             "generated_csg_options_incomplete", path)
    for key in _BOOL_OPTIONS:
        _require(type(value[key]) is bool, "generated_csg_invalid_options", path + "." + key)
    for key in _INT_OPTIONS:
        _integer(value[key], path + "." + key, _INT32_MIN)


def _effective_options(value, path):
    _require(type(value) is dict and set(value) == _BOOL_OPTIONS | _INT_OPTIONS,
             "generated_csg_options_incomplete", path)
    for key in _BOOL_OPTIONS:
        _require(type(value[key]) is bool, "generated_csg_invalid_options", path + "." + key)
    # The bridge intentionally marks disabled translucent channels as unknown
    # in the effective comparison rather than trusting unstable SDK getters.
    for key in ("insulation_translucency", "obstruction_translucency"):
        if value[key] is not None:
            _integer(value[key], path + "." + key, _INT32_MIN)
    for key in _INT_OPTIONS - {"insulation_translucency", "obstruction_translucency"}:
        _integer(value[key], path + "." + key, _INT32_MIN)
    return value


def _validate_effective_option_policy(options, success):
    _require(type(options.get("comparison_scope")) is str and options["comparison_scope"],
             "generated_csg_options_scope_unreported", "result.options.comparison_scope")
    snapshots = {}
    for raw_field, effective_field in (("before", "effective_before"), ("after", "effective_after")):
        raw, effective = options.get(raw_field), options.get(effective_field)
        if raw is None:
            _require(not success and effective is None,
                     "generated_csg_options_effective_mismatch", "result.options." + effective_field)
            continue
        _effective_options(effective, "result.options." + effective_field)
        expected = dict(raw)
        for channel in ("insulation", "obstruction"):
            if raw[channel] is False:
                expected[channel + "_translucency"] = None
        _require(effective == expected, "generated_csg_options_effective_mismatch", "result.options." + effective_field)
        snapshots[raw_field] = raw
    for field in ("raw_mismatched_fields", "effective_mismatched_fields", "inactive_fields"):
        _string_list(options.get(field), "result.options." + field)
    if len(snapshots) < 2:
        _require(not success and options.get("raw_restored") is None and options.get("effective_restored") is None
                 and options["restored"] is None and not options["raw_mismatched_fields"]
                 and not options["effective_mismatched_fields"] and not options["inactive_fields"],
                 "generated_csg_options_partial_comparison_invalid", "result.options")
        return
    before, after = snapshots["before"], snapshots["after"]
    raw_mismatches = [key for key in before if before[key] != after[key]]
    effective_mismatches = [key for key in options["effective_before"]
                            if options["effective_before"][key] != options["effective_after"][key]]
    _require(options["raw_mismatched_fields"] == raw_mismatches
             and options["effective_mismatched_fields"] == effective_mismatches,
             "generated_csg_options_mismatch_report", "result.options")
    expected_inactive = [channel + "_translucency" for channel in ("insulation", "obstruction")
                         if before[channel] is False and after[channel] is False]
    _require(options["inactive_fields"] == expected_inactive,
             "generated_csg_options_inactive_field_invalid", "result.options.inactive_fields")
    _require(type(options.get("raw_restored")) is bool and options["raw_restored"] is (not raw_mismatches),
             "generated_csg_options_restoration_invalid", "result.options.raw_restored")
    _require(type(options.get("effective_restored")) is bool and options["effective_restored"] is (not effective_mismatches)
             and options["restored"] is options["effective_restored"],
             "generated_csg_options_restoration_invalid", "result.options.effective_restored")
    if success:
        _require(options["effective_restored"] is True, "generated_csg_options_not_restored", "result.options")


def _string_list(value, path, maximum=64):
    _require(type(value) is list and len(value) <= maximum, "generated_csg_string_list_invalid", path)
    for index, member in enumerate(value):
        _text(member, f"{path}[{index}]", maximum=128)
    return value


def _subtree(value, path):
    _require(type(value) is list and len(value) <= 64, "generated_csg_subtree_invalid", path)
    seen = set()
    for index, member in enumerate(value):
        here = f"{path}[{index}]"
        key = _identity(member, here)
        _require(key not in seen, "generated_csg_subtree_duplicate", here)
        seen.add(key)
        _integer(member.get("depth"), here + ".depth", 1, 2)
        _reference(member.get("parent_refno"), here + ".parent_refno")
        _integer(member.get("sibling_index"), here + ".sibling_index", 1, 33)
    return value


def _validate_facet_group(group, path):
    _require(type(group) is dict and set(group) == {"source", "units", "coordinate_frame", "facets"},
             "generated_csg_facet_group_invalid", path)
    _require(group["source"] == "CSGFacetGroup.Facets"
             and group["units"] == "sdk_raw_unverified"
             and group["coordinate_frame"] == "sdk_facet_coordinates_unverified",
             "generated_csg_facet_group_provenance_invalid", path)
    facets = group["facets"]
    _require(type(facets) is list and len(facets) <= _FACET_GROUP_LIMITS["max_facets_per_group"],
             "generated_csg_facet_count_limit_exceeded", path + ".facets")
    loop_count = coordinate_count = 0
    for facet_index, facet in enumerate(facets):
        here = f"{path}.facets[{facet_index}]"
        _require(type(facet) is dict and set(facet) == {"loops"}, "generated_csg_facet_invalid", here)
        loops = facet["loops"]
        _require(type(loops) is list and len(loops) <= _FACET_GROUP_LIMITS["max_loops_per_facet"],
                 "generated_csg_facet_loop_count_limit_exceeded", here + ".loops")
        loop_count += len(loops)
        _require(loop_count <= _FACET_GROUP_LIMITS["max_total_loops"],
                 "generated_csg_facet_loop_count_limit_exceeded", path + ".facets")
        for loop_index, loop in enumerate(loops):
            there = f"{here}.loops[{loop_index}]"
            _require(type(loop) is dict and set(loop) == {"vertices", "normals"},
                     "generated_csg_facet_loop_invalid", there)
            for field in ("vertices", "normals"):
                entries = loop[field]
                _require(type(entries) is list and len(entries) <= _FACET_GROUP_LIMITS["max_entries_per_loop_array"],
                         "generated_csg_facet_loop_entry_limit_exceeded", there + "." + field)
                coordinate_count += 3 * len(entries)
                _require(coordinate_count <= _FACET_GROUP_LIMITS["max_total_coordinate_values"],
                         "generated_csg_facet_coordinate_value_limit_exceeded", path + ".facets")
                for index, xyz in enumerate(entries):
                    member_path = f"{there}.{field}[{index}]"
                    _require(type(xyz) is list and len(xyz) == 3,
                             "generated_csg_facet_coordinate_invalid", member_path)
                    for component in xyz:
                        _number(component, member_path)
    return len(facets), loop_count, coordinate_count


def _validate_item(item, index, path, max_array_values):
    _require(type(item) is dict, "generated_csg_invalid_item", path)
    _require(_integer(item.get("index"), path + ".index") == index,
             "generated_csg_item_index_mismatch", path)
    _require(type(item.get("complete")) is bool, "generated_csg_item_completeness_unreported", path)
    errors = _errors(item.get("errors"), path + ".errors")
    _require(item["complete"] is not bool(errors), "generated_csg_item_completeness_mismatch", path)
    for field in ("gml_item", "translucency"):
        _require(field in item, "generated_csg_item_identity_unreported", path + "." + field)
        if item[field] is not None or item["complete"]:
            _integer(item[field], path + "." + field, _INT32_MIN)
    kind = item.get("type")
    _require("type" in item, "generated_csg_type_required", path + ".type")
    code = None
    if kind is not None or item["complete"]:
        _require(type(kind) is dict, "generated_csg_type_required", path + ".type")
        name = _text(kind.get("name"), path + ".type.name", maximum=128)
        code = _integer(kind.get("value"), path + ".type.value", _INT32_MIN)
        _require(type(kind.get("known")) is bool, "generated_csg_type_required", path + ".type.known")
        if code in _CSG_TYPES:
            _require(kind["known"] is True and name == _CSG_TYPES[code], "generated_csg_type_mismatch", path + ".type")
        else:
            _require(kind["known"] is False and name == str(code), "generated_csg_type_mismatch", path + ".type")
    values = 0
    for field in ("rdata", "idata", "transform_as_array12"):
        _require(field in item, "generated_csg_array_unreported", path + "." + field)
        array = item[field]
        if field != "transform_as_array12":
            length_field = field + "_length"
            _require(length_field in item, "generated_csg_array_length_unreported", path + "." + length_field)
            length = item[length_field]
            if length is not None:
                _integer(length, path + "." + length_field)
            _require((array is None and (length is None or not item["complete"]))
                     or type(array) is list and type(length) is int and length == len(array),
                     "generated_csg_array_length_mismatch", path + "." + length_field)
        if array is None:
            continue
        _require(type(array) is list and len(array) <= max_array_values,
                 "generated_csg_array_limit_exceeded", path + "." + field)
        if field == "transform_as_array12":
            _require(len(array) == 12, "generated_csg_transform_length_invalid", path + "." + field)
        for offset, member in enumerate(array):
            here = f"{path}.{field}[{offset}]"
            if field == "idata":
                _integer(member, here, _INT32_MIN)
            else:
                _number(member, here)
        values += len(array)
    _require("local_limits" in item, "generated_csg_limits_unreported", path)
    limits = item["local_limits"]
    if limits is not None:
        _require(type(limits) is dict and limits.get("frame") == "untransformed",
                 "generated_csg_limits_frame_invalid", path + ".local_limits")
        for field in ("min", "max"):
            array = limits.get(field)
            _require(type(array) is list and len(array) == 3, "generated_csg_limits_invalid", path + ".local_limits." + field)
            for member in array:
                _number(member, path + ".local_limits." + field)
        _require(all(a <= b for a, b in zip(limits["min"], limits["max"])),
                 "generated_csg_limits_invalid", path + ".local_limits")
        values += 6
    return values, code is not None and code not in _CSG_TYPES, code in _CSG_TYPES and _CSG_TYPES[code].startswith("CSG_NEG_")


def _failure(code, requested_refno, *, path=None, version=None):
    return {"ok": False, "version": version if type(version) is str and len(version) <= 64 else "E3D21",
        "error": code, "result": {
            "operation": "generated_csg", "requested_refno": requested_refno if type(requested_refno) is str and len(requested_refno) <= 1024 else None,
            "success": False, "available": False, "complete": False, "generated_csg": True,
            "error_code": code, "errors": [{"stage": "python_descriptor_validation", "code": code, "message": path or "response"}],
            "trees": [], "native_tessellation": False, "cuts_applied": False, "post_boolean_mesh_verified": False,
            "transform_convention_verified": False, "raw_data_layout_verified": False,
            "units": "sdk_raw_unverified", "coordinate_frame": "sdk_item_transform_output_unverified",
            "descriptor_validation": {"valid": False, "mesh_conversion_admitted": False, "path": path},
        }}


def validate_generated_csg_response(response: Any, requested_refno: str,
                                    max_trees: int, max_items: int) -> dict[str, Any]:
    """Return an independently copied descriptor receipt or a closed failure.

    A valid truncated/native-failure receipt keeps its original failure status
    and bounded observations. Even a complete receipt is not a mesh admission.
    This function never multiplies transforms, converts units or decodes data.
    """
    try:
        _text(requested_refno, "requested_refno")
        _require(requested_refno.startswith(("/", "=")), "generated_csg_explicit_reference_required", "requested_refno")
        if requested_refno.startswith("="):
            _reference(requested_refno, "requested_refno")
        _integer(max_trees, "max_trees", 1, MAX_TREES)
        _integer(max_items, "max_items", 1, MAX_ITEMS)
        copied = _json_copy(response)
        _require(type(copied) is dict and type(copied.get("ok")) is bool,
                 "generated_csg_invalid_envelope", "response")
        failed_result = copied.get("result")
        empty_failure = (type(failed_result) is dict and failed_result.get("trees") == []
                         and all(failed_result.get(field) is False for field in ("success", "available", "complete"))
                         and set(failed_result) <= {"success", "available", "complete", "source", "refno", "trees", "error"})
        if copied["ok"] is False and "trees" not in copied and (
                failed_result is None or type(failed_result) is dict and "trees" not in failed_result
                or empty_failure and isinstance(copied.get("error"), str) and copied["error"]):
            # Transport/preflight/mock failures contain no generated descriptors
            # to admit. Preserve their original reason and failure envelope.
            return copied
        if (copied.get("version") == "E3D21" and "trees" not in copied and empty_failure
                and failed_result.get("source") == "mock"
                and isinstance(failed_result.get("error"), str) and failed_result["error"]):
            return copied
        _require(copied.get("version") == "E3D21", "generated_csg_version_mismatch", "response.version")
        result = copied.get("result")
        _require(type(result) is dict and result.get("operation") == "generated_csg",
                 "generated_csg_result_required", "response.result")
        echoed = _text(result.get("requested_refno"), "result.requested_refno")
        same_request = (_reference(echoed, "result.requested_refno") == _reference(requested_refno, "requested_refno")
                        if requested_refno.startswith("=") and echoed.startswith("=")
                        else echoed.casefold() == requested_refno.casefold())
        _require(same_request,
                 "generated_csg_request_mismatch", "result.requested_refno")
        _require(result.get("generated_csg") is True, "generated_csg_provenance_unreported", "result.generated_csg")
        for field in _UNVERIFIED_FLAGS:
            _require(result.get(field) is False, "generated_csg_unverified_claim", "result." + field)
        for field in ("atomic_snapshot", "native_work_bounded", "native_cancellation_supported",
                      "material_boundary_validated", "native_cut_application_verified"):
            if field in result:
                _require(result[field] is False, "generated_csg_unverified_claim", "result." + field)
        _require(result.get("units") == "sdk_raw_unverified" and result.get("coordinate_frame") == "sdk_item_transform_output_unverified",
                 "generated_csg_coordinate_claim_invalid", "result")
        for field in ("success", "available", "complete", "truncated"):
            _require(type(result.get(field)) is bool, "generated_csg_status_unreported", "result." + field)
        errors = _errors(result.get("errors"), "result.errors")
        success = result["success"]
        _require(not success or copied["ok"] is True and result["available"] and result["complete"]
                 and not result["truncated"] and not errors, "generated_csg_success_inconsistent", "result")
        _require(success or result["complete"] is False, "generated_csg_failure_marked_complete", "result.complete")
        source = result.get("source")
        if source is not None or success:
            _identity(source, "result.source")
            _require(source["type"] in _ROOT_TYPES and _matches(requested_refno, source)
                     and result.get("refno") == source["refno"], "generated_csg_source_mismatch", "result.source")
        limits = result.get("limits")
        _require(type(limits) is dict, "generated_csg_limits_policy_required", "result.limits")
        for key, expected in (("max_trees", max_trees), ("max_items", max_items),
                              ("max_array_values", MAX_ARRAY_VALUES), ("max_total_array_values", MAX_TOTAL_ARRAY_VALUES)):
            _require(type(limits.get(key)) is int and limits[key] == expected,
                     "generated_csg_limits_policy_mismatch", "result.limits." + key)
        for key, expected in (("max_input_nodes", 64), ("max_direct_members", 32), ("max_input_depth", 2)):
            if key in limits:
                _require(type(limits[key]) is int and limits[key] == expected,
                         "generated_csg_limits_policy_mismatch", "result.limits." + key)
        typed_facets = "facet_group_policy" in result
        if typed_facets:
            _require(result["facet_group_policy"] == "sdk_typed_v1",
                     "generated_csg_facet_group_policy_invalid", "result.facet_group_policy")
            _require(limits.get("facet_groups") == _FACET_GROUP_LIMITS,
                     "generated_csg_limits_policy_mismatch", "result.limits.facet_groups")
        else:
            _require("facet_groups" not in limits,
                     "generated_csg_facet_group_policy_invalid", "result.limits.facet_groups")
        trees = result.get("trees")
        _require(type(trees) is list and len(trees) <= max_trees, "generated_csg_tree_limit_exceeded", "result.trees")
        emitted_items = observed_items = observed_trees = scalar_count = unknown_count = negative_count = 0
        returned_groups = returned_facets = returned_loops = copied_facet_values = 0
        for index, tree in enumerate(trees):
            path = f"result.trees[{index}]"
            _require(type(tree) is dict and type(tree.get("index")) is int and tree["index"] == index,
                     "generated_csg_tree_index_mismatch", path)
            key = _identity(tree.get("source"), path + ".source")
            _require(type(tree.get("complete")) is bool, "generated_csg_tree_completeness_unreported", path)
            _require(type(tree.get("source_matches_requested")) is bool and tree["source_matches_requested"] is (
                source is not None and key == _reference(source["refno"], "result.source.refno")),
                "generated_csg_tree_source_mismatch", path)
            _require("item_count" in tree, "generated_csg_tree_item_count_unreported", path)
            item_count = tree["item_count"]
            if item_count is not None:
                _integer(item_count, path + ".item_count")
                observed_trees += 1
                observed_items += item_count
            items = tree.get("items")
            _require(type(items) is list and (item_count is not None and len(items) <= item_count
                     or item_count is None and not items and tree["complete"] is False and not success)
                     and emitted_items + len(items) <= max_items,
                     "generated_csg_item_limit_exceeded", path + ".items")
            for item_index, item in enumerate(items):
                item_path = f"{path}.items[{item_index}]"
                values, unknown, negative = _validate_item(item, item_index, item_path, MAX_ARRAY_VALUES)
                scalar_count += values
                unknown_count += unknown
                negative_count += negative
                _require(scalar_count <= MAX_TOTAL_ARRAY_VALUES, "generated_csg_total_array_limit_exceeded", path)
                _require(not success or item["complete"], "generated_csg_success_has_incomplete_item", path)
                facet_path = item_path + ".facet_group"
                _require(("facet_group" in item) == typed_facets, "generated_csg_facet_group_policy_invalid", facet_path)
                group = item.get("facet_group")
                if typed_facets and item["complete"] and item["type"]["value"] in _FACET_GROUP_TYPES:
                    _require(group is not None, "generated_csg_facet_group_invalid", facet_path)
                if group is not None:
                    _require(item["complete"] and item["type"]["value"] in _FACET_GROUP_TYPES,
                             "generated_csg_facet_group_invalid", facet_path)
                    facets, loops, coords = _validate_facet_group(group, facet_path)
                    returned_groups += 1
                    returned_facets += facets
                    returned_loops += loops
                    copied_facet_values += coords
                    for key, value, error in (
                        ("max_total_facets", returned_facets, "generated_csg_facet_count_limit_exceeded"),
                        ("max_total_loops", returned_loops, "generated_csg_facet_loop_count_limit_exceeded"),
                        ("max_total_coordinate_values", copied_facet_values, "generated_csg_facet_coordinate_value_limit_exceeded"),
                    ):
                        _require(value <= _FACET_GROUP_LIMITS[key], error, facet_path)
            emitted_items += len(items)
            _require(not tree["complete"] or item_count == len(items) and all(item["complete"] for item in items),
                     "generated_csg_incomplete_tree", path)
            _require(not success or tree["complete"], "generated_csg_incomplete_tree", path)
        counts = result.get("counts")
        _require(type(counts) is dict, "generated_csg_counts_required", "result.counts")
        for key, expected in (("returned_tree_count", len(trees)), ("returned_item_count", emitted_items),
                              ("observed_item_count", observed_items), ("observed_tree_count", observed_trees)):
            _require(type(counts.get(key)) is int and counts[key] == expected,
                     "generated_csg_counts_mismatch", "result.counts." + key)
        total_trees = counts.get("total_tree_count")
        if total_trees is not None:
            _integer(total_trees, "result.counts.total_tree_count")
        _require((total_trees is not None and total_trees >= len(trees) or total_trees is None and not trees and not success)
                 and counts.get("item_count_scope") == "inspected_trees_only",
                 "generated_csg_counts_mismatch", "result.counts")
        copied_values = _integer(counts.get("copied_array_value_count"), "result.counts.copied_array_value_count", 0, MAX_TOTAL_ARRAY_VALUES)
        _require(copied_values >= scalar_count and (not success or copied_values == scalar_count),
                 "generated_csg_counts_mismatch", "result.counts.copied_array_value_count")
        if typed_facets:
            facet_counts = counts.get("facet_groups")
            expected = {
                "returned_group_count": returned_groups, "returned_facet_count": returned_facets,
                "returned_loop_count": returned_loops, "copied_coordinate_value_count": copied_facet_values,
            }
            _require(type(facet_counts) is dict and facet_counts == expected
                     and all(type(value) is int for value in facet_counts.values()),
                     "generated_csg_counts_mismatch", "result.counts.facet_groups")
        else:
            _require("facet_groups" not in counts,
                     "generated_csg_facet_group_policy_invalid", "result.counts.facet_groups")
        _require(not success or total_trees == len(trees), "generated_csg_incomplete_tree_coverage", "result.counts")
        reasons = result.get("truncation_reasons")
        _require(type(reasons) is list and len(reasons) <= 256 and all(type(reason) is str and 0 < len(reason) <= 128 for reason in reasons),
                 "generated_csg_truncation_reasons_invalid", "result.truncation_reasons")
        _require(result["truncated"] is bool(reasons) and not (success and reasons),
                 "generated_csg_truncation_mismatch", "result")
        options = result.get("options")
        _require(type(options) is dict and (type(options.get("restored")) is bool
                 or options.get("restored") is None and not success),
                 "generated_csg_options_unreported", "result.options")
        for field in ("before", "requested", "after"):
            snapshot = options.get(field)
            if snapshot is not None or success:
                _options(snapshot, "result.options." + field)
        policy = options.get("comparison_policy")
        if policy is None:
            if options["restored"] or success:
                _require(options["restored"] is True and options.get("before") is not None
                         and options.get("before") == options.get("after"), "generated_csg_options_not_restored", "result.options")
        elif policy == "enabled_geometry_options_v1":
            _validate_effective_option_policy(options, success)
        else:
            raise _InvalidReceipt("generated_csg_options_policy_unknown", "result.options.comparison_policy")
        context = result.get("context")
        _require(type(context) is dict, "generated_csg_context_unreported", "result.context")
        for key in ("ce_unchanged", "identity_unchanged"):
            if success:
                _require(context.get(key) is True, "generated_csg_context_unverified", "result.context." + key)
        for first, second in (("ce_before", "ce_after"), ("project_before", "project_after"), ("mdb_before", "mdb_after")):
            if success:
                _text(context.get(first), "result.context." + first, maximum=4096)
                _require(first in context and second in context and context[first] is not None
                         and context[first] == context[second], "generated_csg_context_changed", "result.context." + first)
        if success:
            _identity(result.get("source_after"), "result.source_after")
            _require(result.get("source_unchanged") is True and source == result["source_after"],
                     "generated_csg_source_changed", "result.source_after")
            _require(result.get("generation_attempted") is True and result.get("generation_returned") is True
                     and result.get("generated_geometry_available") is True and emitted_items > 0,
                     "generated_csg_generation_unverified", "result")
            before_subtree = _subtree(result.get("source_subtree_before"), "result.source_subtree_before")
            after_subtree = _subtree(result.get("source_subtree_after"), "result.source_subtree_after")
            _require(result.get("source_subtree_unchanged") is True and before_subtree == after_subtree,
                     "generated_csg_source_subtree_changed", "result.source_subtree_after")
            _require(type(result.get("resources")) is dict and result["resources"].get("complete") is True
                     and result["resources"].get("borrowed_dispose_attempted_count") == 0,
                     "generated_csg_resource_completion_unverified", "result.resources")
            _require(type(result.get("generated_csg_quarantine")) is dict
                     and result["generated_csg_quarantine"].get("active") is False,
                     "generated_csg_quarantine_active_or_unreported", "result.generated_csg_quarantine")
        result["descriptor_validation"] = {
            "valid": True, "scope": "returned native descriptors only; no geometry decoding, unit conversion or transform interpretation",
            "generation_complete": result["complete"], "returned_tree_count": len(trees),
            "returned_item_count": emitted_items, "copied_array_values": scalar_count,
            "unknown_type_count": unknown_count, "negative_type_count": negative_count,
            "mesh_conversion_admitted": False, "native_tessellation": False,
            "coordinate_frame_verified": False, "units_verified": False,
            "source_subtree_checked": success,
        }
        return copied
    except _InvalidReceipt as exc:
        version = response.get("version") if type(response) is dict else None
        return _failure(exc.code, requested_refno, path=exc.path, version=version)
