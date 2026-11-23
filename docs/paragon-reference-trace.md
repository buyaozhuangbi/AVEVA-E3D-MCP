# PARAGON reference trace

`e3d_paragon_reference_trace` reads a bounded graph of existing Design, Catalogue and Property references in one E3D UI dispatch. It does not execute PML, select CE, create catalogue objects or change a database. The graph reports SDK base element types; it does not establish actual UDET identity, material suitability or engineering connectivity.

## Request

MCP tool arguments and HTTP `POST /api/v1/paragon/reference-trace` share the same two parameters:

```json
{"refno":"=1/21","trace_limit":24}
```

Replace the example with an existing reference. `refno` must be a canonical ASCII numeric DBREF: positive Int32 database component and nonnegative Int32 element component. Names, implicit CE, leading zeros, whitespace and extra parameters are rejected. `trace_limit` defaults to 24 and accepts integers from 1 through 32. HTTP accepts at most 4096 raw UTF-8 body bytes and no query parameters; duplicate JSON keys are rejected.

The Addin advertises `paragon_reference_trace` in `/health.api_features` only if its context implements the optional reader. An older Addin returns HTTP 404. A method other than POST returns HTTP 405 with `Allow: POST`.

## Fixed paths

Traversal uses depth-first order. Each attempted path consumes one budget entry, including root, failed reads, unset references, cycles and a repeated DBREF reached through another branch. Only ancestor repetition is a cycle; separate paths to the same record retain separate observations.

| Observed source | Next paths, in order |
| --- | --- |
| Design element | `SPREF`; accepts Catalogue SPCO, or SCOM for NOZZ/ELCONN |
| Catalogue SPCO | `CATREF`, `PRTREF`, `CMPREF`, `OWNER` |
| Catalogue SELE | `OWNER` towards SELE or SPEC |
| Catalogue GPART | `CATREF`, `CMPREF` |
| Catalogue SPEC | `MATREF`, `FLUREF` |
| Catalogue SCOM/SPRF/SFIT/JOIN | `OWNER` towards its supported category/section |
| Catalogue CATE/STCA/SECT/STSE | `OWNER` towards its supported section/catalogue |
| Supported Property record or Catalogue CATA | No further expansion |

Every edge checks the expected raw SDK database code and base noun. Unexpected types are reported as `out_of_scope` and are not expanded. `db_type_codes` records only paths on which DbType was actually read; a missing code is unknown, not inferred from the parent or reference number.

## Response and coverage

Native HTTP responses use `{ok, version, session_id, bridge_build, result}`. `version` must be the observed `E3D21` string and `bridge_build` is the Addin assembly MVID. Python does not fill a missing native version from configuration.

The native result has exactly five fields:

| Field | Meaning |
| --- | --- |
| `status` | `observed`, `partial`, `unavailable`, `busy` or `context_unverified` |
| `trace_limit` | Requested `limit`, attempted-path `used`, and whether an additional fixed path was omitted (`reached`) |
| `trace` | Path-keyed rows such as `root.SPREF.CATREF`, each containing `ref`, base `type`, direct `owner` and `status` |
| `db_type_codes` | Path-keyed raw Int32 SDK database codes |
| `context` | Before/after samples and project/MDB instance comparisons |

`observed` means all planned paths in the supported graph were observed or explicitly unset. It does not mean that every reference or optional value exists. `partial` preserves the rows that were read alongside their error or truncation statuses. `unavailable` means the root could not be observed in the supported scope. `used == limit` alone does not imply truncation: inspect `reached`.

Each context sample binds the server-owned session ID and assembly build to project/MDB names and a typed CE `{refno, type, db_type_code}`. CE is null only for an observed native null element; a failed boundary read makes the entire sample null. Same-named project/MDB objects are also compared by native object instance. Changed or unread boundaries produce `context_unverified`, clear `trace` and `db_type_codes`, and preserve attempted-path counts. These boundary samples do not lock the database or establish that individual graph records stayed unchanged throughout the read.

Unsupported mode/version, missing feature or malformed native responses return a separate Python unavailability result with `available=false`, `status=unknown` and a reason. Such a result contains no native trace or fabricated session/build observations. Ordinary HTTP failures remain failures. Native fault markers take precedence over 404 classification, preserve the original diagnostics and stop all follow-up reads; there is no retry, alternative lookup or health/command-state diagnostic read.

## Verification

The tests execute the production HTTP handler, managed validator and native reader with recording SDK fakes, then validate the resulting native receipts in Python. A separate SDK compile-only check verifies installed E3D21 signatures without opening a project or database. The catalog compatibility test freezes all 114 preceding tool definitions and admits only this one new tool. Offline verification does not deploy the Addin or certify a live model.
