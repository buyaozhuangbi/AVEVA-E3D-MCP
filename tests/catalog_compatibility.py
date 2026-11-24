"""Keep historical tool definitions exact except enumerated optional fields."""

from copy import deepcopy


OPTIONAL_CHORD_TOOLS = frozenset({
    "e3d_primitive_mesh", "e3d_primitive_csg_mesh",
    "e3d_project_primitive_csg_drawing", "e3d_element_mesh",
})
CHORD_TOLERANCE_SCHEMA = {
    "type": "number", "exclusiveMinimum": 0,
    "description": "Optional maximum analytic operand surface deviation in model millimetres. "
                   "Refines each curved parent and cutter separately, using radial_segments as a minimum. "
                   "Impossible tolerance or geometry budget fails without partial geometry. "
                   "Does not certify native tessellation or Boolean intersection accuracy.",
}
CONNECTION_POINTS_EXPECTED_TYPE_SCHEMA = {
    "type": "string", "maxLength": 64,
    "description": "Optional required Design type ShortName. Only implicit TUBI has cross-type resolution "
                   "from a shared BRAN/ELBO anchor; other mismatches fail.",
}
CONNECTION_PAIR_EXPECTED_TYPE_SCHEMA = {
    "type": "string", "maxLength": 64,
    "description": "Optional required Design type ShortName for the {endpoint} element. Only implicit TUBI has cross-type resolution "
                   "from a shared BRAN/ELBO anchor; other mismatches fail.",
}
CREATE_EXPECTED_REVISION_SCHEMA = {
    "type": "string", "pattern": "^[0-9a-fA-F]{64}$", "minLength": 64, "maxLength": 64,
    "description": "Revision returned by a create preview. The E3D21 bridge requires it for guarded execution; omitted previews remain valid.",
}
CREATE_EXECUTION_REVISION_REQUIRED = [{
    "if": {"properties": {"dry_run": {"const": False}}, "required": ["dry_run"]},
    "then": {"required": ["expected_revision"]},
}]
CREATE_GUARDED_DESCRIPTION = (
    "Create a child through the E3D SDK under an explicit parent, with optional dimensions/text and transform. "
    "Preview validates type/owner compatibility and inputs without creating anything; it returns a revision when the "
    "connected E3D21 bridge supports guarded execution. Pass that revision as expected_revision when executing so a "
    "changed parent, frame or request is rejected before creation. Older preview calls remain compatible. Preview "
    "does not prove all database constraints. A failed initialization attempts to remove only the new element; "
    "inspect cleanup and residual state before retrying."
)

OPTIONAL_MODELING_FIELDS = {
    "e3d_database_preflight": ("root_name", {
        "type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]{0,31}$", "maxLength": 32,
        "description": "Optional CATA Catalogue root element name without the leading slash. "
                       "ADMIN creates the root when the user applies the form.",
    }),
    "e3d_equipment_model_plan": ("include_native_previews", {
        "type": "boolean", "default": False,
        "description": "Request exact native dry_run previews after the read-only plan is complete. No model writes are performed.",
    }),
    "e3d_piping_route_plan": ("include_native_previews", {
        "type": "boolean", "default": False,
        "description": "Request exact create dry_run previews for planned direct children of the observed root. "
                       "No model edits are performed; planned descendants remain deferred.",
    }),
}

_MODELING_DESCRIPTION_EDITS = {
    "e3d_database_preflight": (
        ("Admin creates CATA databases for PARAGON; this tool does not specify or create a CATA root element.",
         "For PARAGON CATA, optional root_name adds a WITH CATA root clause for the user-configured ADMIN form; "
         "this tool never creates the database or root."),
    ),
    "e3d_equipment_model_plan": (
        ("fresh context snapshots. Inspect", "fresh context snapshots. Set include_native_previews=true to request "
         "the exact create/copy/transform dry_run routes and receive native create/copy revisions when context remains stable. Inspect"),
        ("A ready result is not a native edit preview or engineering approval. "
         "Create/copy revisions are obtained only by a later dry_run on their existing write tools.",
         "A ready result is not engineering approval."),
        ("This tool never calls create/copy/transform write routes, PML, selection, display or capture.",
         "This tool does not execute model writes, PML, selection, display or capture."),
    ),
    "e3d_piping_route_plan": (
        ("Returns a proposed ordered create_element/set_attributes dry-run sequence only; every revision is null and no "
         "create/set preview or write is called.",
         "Returns a proposed ordered create_element/set_attributes dry-run sequence. Set include_native_previews=true "
         "to request exact create dry_run receipts and revisions for currently existing root children; descendants of planned "
         "new parents and post-create attribute setters stay deferred. No model write is issued."),
    ),
}


def with_modeling_additions(historical):
    name = historical["name"]
    if name not in OPTIONAL_MODELING_FIELDS:
        return historical
    field, schema = OPTIONAL_MODELING_FIELDS[name]
    updated = deepcopy(historical)
    properties = updated["inputSchema"]["properties"]
    if field not in properties:
        properties[field] = schema
        for old, new in _MODELING_DESCRIPTION_EDITS[name]:
            assert old in updated["description"], (name, old)
            updated["description"] = updated["description"].replace(old, new, 1)
        if name == "e3d_database_preflight":
            properties["database_type"]["description"] = (
                "DESI is Design; CATA is the Catalogue database type. Optional root_name adds a CATA root through WITH CATA /NAME."
            )
    return updated


def assert_legacy_tool_definition(testcase, current, historical):
    """Remove exactly the permitted optional additions, then compare everything.

    Compare the approved schema and description deltas without changing the
    saved fixtures.
    """
    candidate = deepcopy(current)
    name = historical["name"]
    if name in OPTIONAL_CHORD_TOOLS:
        if "chord_tolerance_mm" not in historical["inputSchema"]["properties"]:
            testcase.assertNotIn("chord_tolerance_mm", candidate["inputSchema"].get("required", []), name)
            testcase.assertEqual(candidate["inputSchema"]["properties"].pop("chord_tolerance_mm"),
                                 CHORD_TOLERANCE_SCHEMA, name)
    if name == "e3d_connection_points":
        if "expected_type" not in historical["inputSchema"]["properties"]:
            testcase.assertNotIn("expected_type", candidate["inputSchema"].get("required", []), name)
            testcase.assertEqual(candidate["inputSchema"]["properties"].pop("expected_type"),
                                 CONNECTION_POINTS_EXPECTED_TYPE_SCHEMA, name)
    if name == "e3d_check_connection_pair":
        for endpoint in ("first", "second"):
            field = endpoint + "_expected_type"
            testcase.assertNotIn(field, historical["inputSchema"]["properties"], name)
            testcase.assertNotIn(field, candidate["inputSchema"].get("required", []), name)
            expected = {key: value.format(endpoint=endpoint) if isinstance(value, str) else value
                        for key, value in CONNECTION_PAIR_EXPECTED_TYPE_SCHEMA.items()}
            testcase.assertEqual(candidate["inputSchema"]["properties"].pop(field), expected, name)
        testcase.assertEqual(set(candidate["inputSchema"]["required"]), set(historical["inputSchema"]["required"]), name)
        testcase.assertEqual(candidate["description"].replace(
            " Optional first_expected_type and second_expected_type constrain each resolved endpoint to a Design type ShortName; only implicit TUBI has cross-type resolution from a shared BRAN/ELBO anchor.", ""), historical["description"], name)
        candidate["description"] = historical["description"]
    if name == "e3d_create_element":
        if "expected_revision" not in historical["inputSchema"]["properties"]:
            testcase.assertNotIn("expected_revision", candidate["inputSchema"].get("required", []), name)
            testcase.assertNotIn("allOf", historical["inputSchema"], name)
            testcase.assertEqual(candidate["inputSchema"]["properties"].pop("expected_revision"),
                                 CREATE_EXPECTED_REVISION_SCHEMA, name)
            testcase.assertEqual(candidate["inputSchema"].pop("allOf"), CREATE_EXECUTION_REVISION_REQUIRED, name)
            testcase.assertEqual(candidate["description"], CREATE_GUARDED_DESCRIPTION, name)
            candidate["description"] = historical["description"]
    testcase.assertEqual(candidate, with_modeling_additions(historical), name)
