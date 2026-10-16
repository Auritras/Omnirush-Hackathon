# ProofFix MVP final fix report

## Status

Final review fix wave complete. The branch now enforces the remaining proof-chain, lifecycle, restart, demo, configuration, and dashboard/API contracts while preserving the read-only GitHub and guarded OmniRush/plugin boundaries.

## Critical and important fixes

1. **Explicit regression proposals** — `PatchProposal` persistence requires a non-empty `regression_test`; proposal validation compares shell-token identities against the immutable intake command and rejects missing, empty, or identical commands. Orchestrator, HTTP, dashboard proposal controls, verification, and regression tests all use the persisted explicit command; no server/dashboard fallback to the original command remains.
2. **Standalone offline demo** — demo startup materializes the checked-in `fixtures/broken-parser-repo` into a committed standalone repository beneath the configured run-data root. The documented `make demo` + API/dashboard path works without manually copying or initializing Git.
3. **Lifecycle cleanup** — workspace managers track owned snapshots, cleanup is performed for terminal reproduction results, verification completion, server shutdown, and server close, and experiments/verification allocate temporary directories below the controlled run root rather than process `/tmp`. Git worktree registration cleanup is retained as a safe retry when removal fails.
4. **Restart/revision integrity** — reproduction evidence persists the snapshot commit. A restarted orchestrator restores that exact commit and blocks verification if the source checkout HEAD changed after reproduction; it never silently verifies against a changed revision.
5. **Fixture evidence** — recorded OmniRush evidence now points to `parser.py`, `tests/test_parser.py`, and `python -m unittest tests.test_parser`; fixture tests assert those references.
6. **Docker/runtime PATH** — Docker declares a runtime PATH including `/usr/local/bin`, and the runner preserves the configured runtime PATH in child investigations. A Python unittest investigation test and packaging contract test cover this.
7. **Verification timeout** — HTTP `_verification_plan()` and `VerificationPlan` reject NaN, Infinity, non-positive, boolean, and values above 300 seconds.
8. **Run-data configuration** — `.proofix-data` is the default in Make, CLI, README, `.env.example`, Docker usage, and ignore rules. Make exports `.env` values into CLI invocation; CLI also consumes the same environment variables directly.
9. **Complete dashboard/API workflow** — guarded experiment execution, explicit regression patch proposals, verification, approval, and return-to-diagnosis recovery are exposed through HTTP routes and dashboard controls. Experiment commands continue through the existing explicit vocabulary and command policy; no arbitrary-command endpoint was added.

## Checks

- `make test` — passed, 133 tests, 1 environment-dependent Node TypeScript check skipped because the available Node cannot type-strip during `--check`.
- `PYTHONPATH=src python -m unittest tests.test_patches tests.test_verification tests.test_orchestrator tests.test_server tests.test_safety tests.test_runner tests.test_web_assets tests.test_plugin_contract -v` — passed during focused iteration.
- `python -m compileall -q src tests` — passed.
- `node --check web/app.js` — passed.
- `git diff --check` — passed.
- `make demo` API/dashboard flow — passed end to end: materialized fixture, reproduce, diagnose, guarded experiment, explicit patch proposal, approval, verification, JSON export, and shutdown cleanup.
- Clean-checkout simulation — passed compile, Node syntax, and the same end-to-end demo flow from a cloned checkout with no source checkout mutation.
- Source fixture cleanliness — checked-in fixture bytes remain unchanged; generated run data and disposable workspaces were removed after the demo.

## Remaining concern

Docker image build/runtime execution could not be run in this environment because the Docker executable is unavailable. Dockerfile PATH and offline test configuration are covered by deterministic source/contract tests.
