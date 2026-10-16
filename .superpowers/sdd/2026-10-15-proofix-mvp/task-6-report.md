# Task 6 implementation report

## Status

Complete.

## Commit

`666d2a7` — `feat: add guarded experiments and verification`

## Files changed

- `src/proofix/experiments.py`
- `src/proofix/patches.py`
- `src/proofix/verification.py`
- `src/proofix/orchestrator.py`
- `tests/test_experiments.py`
- `tests/test_patches.py`
- `tests/test_verification.py`

## Tests and checks

- Test-first red phase: `PYTHONPATH=src python -m unittest tests.test_experiments tests.test_patches tests.test_verification -v` failed before implementation because the new modules were not present.
- `PYTHONPATH=src python -m unittest tests.test_experiments tests.test_patches tests.test_verification -v`: passed; 10 focused tests ran successfully.
- `make test`: passed; 71 tests ran successfully.
- `PYTHONPATH=src python -m compileall -q src tests`: passed.
- `git diff --check`: passed.

## Concerns

- Approval records are process-local until the existing run snapshot persists the orchestrator's approval record; a process restart before persistence cannot resume an in-memory proposal object.
- Parent-commit comparison is represented as a bounded experiment kind and metadata; the current MVP does not materialize a second parent snapshot automatically.
- Syntax validation is intentionally limited to changed Python files; other runtimes rely on their configured verification commands.

## Fix round 1

### Status

Complete.

### Commit

`cfcc5bc` — `fix: harden task 6 guarded verification`

### Changes

- Enforced approved executable and repository-relative test/build target vocabularies for experiments and verification, rejecting arbitrary Python scripts without executing them.
- Bound approval to the persisted run proposal digest and persisted the diff and approval record so a restarted orchestrator can reconstruct and verify the proposal without process-local correctness state.
- Added diagnosis recovery transitions from approval and verification outcomes, clearing approval on recovery so a new proposal/approval is required while preserving verification evidence.
- Implemented parent-commit comparison using a safe Git archive and distinct proposed-regression semantics requiring an explicit regression command.
- Accepted standard Git diff metadata such as `index` and mode lines while preserving unified-diff path, scope, and hunk validation.
- Converted unexpected verification runner, workspace, patch-application, and final-check failures into bounded `verification_blocked` evidence.

### Exact checks and results

- `PYTHONPATH=src python -m unittest tests.test_experiments tests.test_patches tests.test_verification -v`: passed; 13 focused tests ran successfully.
- `make test`: passed; 76 tests ran successfully.
- `PYTHONPATH=src python -m compileall -q src tests`: passed.
- `git diff --check`: passed.

### Concerns

- The guarded command vocabulary intentionally supports a narrow Python test-module and common build-target subset; unsupported runtimes/targets are blocked rather than executed.
- Syntax validation remains limited to changed Python files; other runtimes require configured verification commands.
