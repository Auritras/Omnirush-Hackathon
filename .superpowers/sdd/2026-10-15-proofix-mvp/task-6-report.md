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

## Fix round 2

### Status

Complete.

### Commit

`49c8b75` — `fix: close task 6 approval and diff gaps`

### Changes

- Restricted dependency/runtime experiments to read-only `python --version`, `python -V`, and `python -m pip --version`; side-effecting modules and install commands are rejected before execution.
- Required approval callers to provide the current proposal and digest, enforced exact run binding in approval/verification, and removed the unbound `is_patch_approved` bypass.
- Validated actor and canonical UTC timestamps for rejection, diagnosis-request, and return-to-diagnosis audit actions.
- Added quoted/space-containing Git path parsing and support for pure rename and mode-only metadata diffs.
- Persisted reject/request/recovery decisions in the patch record, transitioned rejected proposals back to diagnosis, and verified replay does not reopen approval.
- Recomputed persisted and in-memory patch digests and file scope before approval or verification; tampered proposals now produce bounded verification-blocked evidence.

### Exact checks and results

- `PYTHONPATH=src python -m unittest tests.test_experiments tests.test_patches tests.test_verification -v`: passed; 17 focused tests ran successfully.
- `make test`: passed; 82 tests ran successfully.
- `PYTHONPATH=src python -m compileall -q src tests`: passed.
- `git diff --check`: passed.

### Concerns

- Dependency/runtime inspection is deliberately limited to the fixed Python version vocabulary; unsupported runtime managers are blocked.
- Git binary patches remain unsupported; textual rename, mode, quoted-path, and standard metadata forms are covered.

## Fix round 3

### Status

Complete.

### Changes

- Made orchestrator approval reconstruct the proposal exclusively from the persisted current run, require an explicit matching digest, verify the persisted run binding, and reject missing/stale/mismatched approval inputs.
- Added robust parsing for Git-generated unquoted space-containing `diff --git` headers while retaining quoted-path, rename, mode-only, scope, and file-header validation.
- Recomputed proposal file scope from diff content during approval and verification, rejecting mutable in-memory or persisted `files` tampering before patch application.
- Added regressions for approval restart/replay binding, real Git unquoted-space renames, and tampered proposal scope.

### Exact checks and results

- `PYTHONPATH=src python -m unittest tests.test_experiments tests.test_patches tests.test_verification tests.test_orchestrator -v`: passed; 33 focused tests ran successfully.
- `make test`: passed; 85 tests ran successfully.
- `PYTHONPATH=src python -m compileall -q src tests`: passed.
- `git diff --check`: passed.

### Concerns

- Git binary patches remain unsupported; textual rename, mode, quoted-path, unquoted-space, and standard metadata forms are covered.

## Fix round 4

### Status

Complete.

### Commit

`2b6892d` — `fix: harden task 6 patch verification`

### Changes

- Disambiguated unquoted Git path headers against rename metadata or file headers, preserving path containment and metadata matching for paths containing ` b/`.
- Added a regression using an actual Git-generated rename diff for `old name.txt` to `new b/file.txt`.
- Made orchestrator verification reconstruct the proposal from the persisted run after approval instead of preferring the process-local proposal cache.
- Added same-process regressions proving persisted diff and file-scope mutations return `verification_blocked` before checks run.

### Exact checks and results

- TDD red phase: `PYTHONPATH=src python -m unittest tests.test_patches.PatchTests.test_real_git_unquoted_space_rename_diff_is_parsed_from_file_headers tests.test_orchestrator.OrchestratorTests.test_verification_reconstructs_current_persisted_proposal_after_approval -v`: failed before the fix (one parser error and two mutation-test failures).
- `PYTHONPATH=src python -m unittest tests.test_experiments tests.test_patches tests.test_verification tests.test_orchestrator -v`: passed; 34 focused tests ran successfully.
- `make test`: passed; 86 tests ran successfully.
- `PYTHONPATH=src python -m compileall -q src tests`: passed.
- `git diff --check`: passed.

### Concerns

- Ambiguous unquoted `diff --git` path operands are rejected unless Git file headers or rename metadata provide the authoritative split; this preserves strict metadata and path validation.

## Fix round 5

### Status

Complete.

### Commit

`b063137` — `fix: resolve git space paths with null headers`

### Changes

- Used the authoritative non-`/dev/null` `+++` or `---` file header to resolve unquoted Git path operands for new-file and deletion diffs containing ` b/`.
- Resolved unquoted-space mode-only diffs using Git's same-path operand invariant while retaining strict containment, traversal, metadata, and quoted/rename validation.
- Added a regression using a real Git-generated unquoted-space new-file diff whose old file header is `/dev/null`.

### Exact checks and results

- TDD red phase: `PYTHONPATH=src python -m unittest tests.test_patches.PatchTests.test_real_git_unquoted_space_new_file_uses_non_dev_null_header -v`: failed before the fix with `ValueError: ambiguous diff paths`.
- `PYTHONPATH=src python -m unittest tests.test_patches -v`: passed; 9 patch tests ran successfully.
- `make test`: passed; 87 tests ran successfully.
- `PYTHONPATH=src python -m compileall -q src tests`: passed.
- `git diff --check`: passed.
- A concurrent post-report check batch produced two timeout-test `pid_file` failures; an isolated serial `make test` rerun passed all 87 tests.

### Concerns

- Git binary patches remain unsupported; textual new-file, deletion, mode-only, rename, quoted-path, and standard metadata forms are covered.
