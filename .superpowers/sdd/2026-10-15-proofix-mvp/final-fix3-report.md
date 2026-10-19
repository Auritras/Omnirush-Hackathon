# ProofFix MVP final fix wave 3 report

## Status

Both residual final-review findings are fixed and covered by direct regressions.

## Fixes

1. `CommandPolicy` now rejects booleans, non-numeric values, NaN, Infinity,
   non-positive timeouts, and values above the 300-second project maximum during
   direct construction. `0.001` and `300` seconds remain valid.
2. Restarted patch proposals now resolve persisted intake through
   `self.allowed_roots` before validation. A restarted orchestrator rejects a
   proposal when the persisted repository is outside its authorized roots.

## Exact checks

- `PYTHONPATH=src python -m unittest tests.test_runner tests.test_orchestrator tests.test_configuration tests.test_server tests.test_safety` — passed, 57 tests.
- `make test` — passed, 143 tests, 1 expected Node type-strip check skipped because the available Node cannot type-strip during `--check`.
- `PYTHONPATH=src python -m compileall -q src tests` — passed.
- `node --version && node --check web/app.js` — passed with Node v22.23.3.
- `git diff --check` — passed.

## Unresolved concerns

- Docker build/runtime was not run because Docker is unavailable in this environment.
