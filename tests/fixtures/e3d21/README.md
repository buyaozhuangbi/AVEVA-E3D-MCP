# Native clash contracts retained during suspension

`NativeClashPreSuspensionHarness.cs.txt` is the full production-fake harness from immediately before the 2026-11-16 native spatial-map suspension. It is intentionally a text fixture, excluded from compilation and test discovery. It is historical evidence, not active test coverage.

The active `tests/e3d21/NativeClashHarness.cs` runs the currently supported contract: bounded metadata preview, input budgets and fault barriers, and fixed suspension of every `SpatialMap.Instance`, `CheckSpatialMap`, Clasher factory and actual native check path. It does not bypass the production policy.

The retained fixture preserves the earlier `SpatialMapPreflight`, `ResultsAndLimits`, `PreflightFailuresAndFaults` and `RequestLocalIgnoreClearing` expectations, including all four native result categories, per-record scope, global result limits, resource disposal/finalizer suppression, option readback, ignore-clash clearing and failure behavior after native allocation. Those native-execution paths cannot be exercised through the current public entry point while its fixed suspension is in force.

Before any future removal or narrowing of the suspension, migrate the applicable historical cases into active tests for the revised implementation and rerun them. Update expectations explicitly where the native contract has changed; do not silently discard them, claim this fixture passed, or introduce a runtime/test-only policy bypass simply to make the old harness run. Offline fakes also do not establish native stability or spatial-map correctness; separate controlled live evidence is still required.

This is a historical harness snapshot captured before the active native-clash suspension was introduced. This fixture requires the former harness's production method injection and AVEVA fakes if reactivated; it is not a standalone live E3D program.
