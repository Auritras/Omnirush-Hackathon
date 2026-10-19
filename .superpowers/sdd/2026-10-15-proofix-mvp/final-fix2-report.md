# ProofFix MVP final fix wave 2 report

## Status

Final review findings are addressed in the final commit with the conventional
message `fix: close final ProofFix review gaps`.

## Fixes

1. `ServerConfig.data_root` now defaults to `.proofix-data`, matching Makefile,
   CLI, README, and `.env.example` defaults. Explicit data-root environment,
   CLI, Make, and config values remain authoritative.
2. Experiment timeout validation is shared across `ExperimentSpec`, the runner,
   and HTTP route. Booleans, NaN, Infinity, non-positive values, and values
   above 300 seconds are rejected; `0.001` and `300` seconds remain valid.
3. Authorized repository roots are explicit in `ServerConfig`, CLI
   `--allowed-root`/`PROOFIX_ALLOWED_ROOTS`, intake, orchestrator, and
   `WorkspaceManager`. Repository paths are resolved with
   `resolve_repository(..., allowed_roots)` before snapshotting or command
   execution. Demo materialization is contained below the configured data root.

## Exact checks

- `PYTHONPATH=src python -m unittest tests.test_configuration tests.test_experiments tests.test_cli tests.test_safety tests.test_server` — passed, 36 tests.
- `make test` — passed, 141 tests, 1 expected Node type-strip check skipped because the available Node cannot type-strip during `--check`.
- `PYTHONPATH=src python -m unittest tests.test_plugin_contract` — passed, 6 tests, 1 expected Node type-strip check skipped.
- `python3 -m compileall -q src tests` — passed.
- `node --check web/app.js` — passed with Node v22.23.3.
- `git diff --check` — passed.
- Clean checkout at the final fix commit: `make test` — passed, 141 tests.
- Clean-checkout README demo: copied `.env.example` to `.env`, ran `make demo`, health check, created a demo run, reproduced, diagnosed, and verified final state `diagnosing`; clean checkout `git status --short --untracked-files=all` was empty after shutdown.
- `docker --version` / Docker build — unavailable because Docker is not installed in this environment.

## Unresolved concerns

- Docker image build and runtime remain unverified locally due to the unavailable Docker executable; Dockerfile and offline test configuration remain source-controlled.
