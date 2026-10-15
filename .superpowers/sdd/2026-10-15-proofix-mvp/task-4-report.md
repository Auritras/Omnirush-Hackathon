# Task 4 implementation report

## Status

Complete.

## Commit

`59187d8` — `feat: add bounded runner and reproduction gate`

## Files changed

- `src/proofix/runner.py`
- `src/proofix/reproduction.py`
- `tests/test_runner.py`
- `tests/test_reproduction.py`

## Tests and checks

- Test-first red phase: `PYTHONPATH=src python -m unittest tests.test_runner tests.test_reproduction -v` failed before implementation because the new modules were not present.
- `PYTHONPATH=src python -m unittest tests.test_runner tests.test_reproduction -v`: passed; 8 focused tests ran successfully.
- `make test`: passed; 31 tests ran successfully.
- `git diff --check`: passed before the implementation commit.
- `PYTHONPATH=src python -m compileall -q src tests`: passed.

## Concerns

- The focused command requires `PYTHONPATH=src` in this src-layout repository; `make test` supplies it automatically.
- Runtime availability remains dependent on the allowlisted executable being installed on the local machine; unavailable executables are returned as `blocked`.
- The runner uses the standard-library process-group termination path on Linux and falls back to direct process termination where process groups are unavailable.

## Fix round 1

### Status

Complete.

### Commit

`7a8e24e` — `fix: harden bounded runner and reproduction gate`

### Changes

- Rejected shell and interpreter command-string forms (`sh/bash -c`, `python -c`, `node -e`, equivalent flags, and shell metacharacters) before execution.
- Added lexical and final-path symlink checks plus `O_NOFOLLOW`/exclusive artifact creation so artifacts cannot escape the disposable root.
- Added explicit cwd containment and environment allowlist enforcement.
- Replaced unbounded `communicate()` collection with selector-based bounded pipe draining; retained bounded full redacted artifacts, truncation markers, digests, and stderr references.
- Compared reproduction evidence against full artifact contents, redacted supplied failure evidence, and strengthened PID/temp-path normalization.
- Added regressions for shell forms and marker non-creation, cwd and artifact escapes, child timeout cleanup, stderr artifacts, environment policy, full-output matching beyond inline limits, and redacted evidence equivalence.

### Exact checks and results

- Test-first red phase: `PYTHONPATH=src python -m unittest tests.test_runner tests.test_reproduction -v` failed with 9 failures and 1 error before the fix implementation.
- `PYTHONPATH=src python -m unittest tests.test_runner tests.test_reproduction -v`: passed; 15 focused tests ran successfully.
- `make test`: passed; 38 tests ran successfully.
- `PYTHONPATH=src python -m compileall -q src tests`: passed.
- `git diff --check`: passed before the fix commit.

### Concerns

- Captured output is explicitly capped at 1 MiB per stream by default; output beyond that cap is marked truncated and cannot participate in reproduction comparison.
- The focused command requires `PYTHONPATH=src` in this src-layout repository; `make test` supplies it automatically.
- Runtime availability remains dependent on the allowlisted executable being installed locally; unavailable runtimes remain `blocked`.
