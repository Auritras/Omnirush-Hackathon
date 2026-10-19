# ProofFix MVP path cleanup

- **Status:** Complete; tracked test fixtures no longer embed machine-specific absolute path examples.
- **Changes:** Replaced fixed POSIX and Windows path literals in intake, GitHub, reproduction, safety, schema, state, orchestrator, and plugin-contract tests with runtime-derived or constructed values. Production path validation was unchanged.
- **Audit:** `git grep` found no tracked machine-specific POSIX temporary, home, or user paths, nor Windows drive-root literals. Remaining absolute-looking paths are portable protocol/system fixtures or generated at runtime.
- **Checks:** `make test` — pass (143 tests, 1 skipped); `python -m compileall -q src tests` — pass; `node --check web/app.js` — pass; `git diff --check` — pass.
