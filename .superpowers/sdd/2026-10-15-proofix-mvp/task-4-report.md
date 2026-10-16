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

## Fix round 2

### Status

Complete.

### Commit

`80500bf` — `fix: enforce bounded process-group termination`

### Changes

- Rejected bundled command-string flags for any allowlisted executable, covering versioned and custom interpreter names such as `python3.11 -cpass` and `custom-python -cpass`.
- Made timeout escalation unconditional after the monotonic SIGTERM grace deadline, including when the parent exits while a child ignores SIGTERM.
- Kept timeout enforcement active after stdout/stderr close and replaced the unbounded process wait with bounded reaping and pipe cleanup.
- Added regressions asserting versioned/custom eval rejection, SIGTERM-ignoring child termination, closed-pipe timeout behavior, bounded return time, and child-process absence.

### Exact checks and results

- Test-first red phase: `PYTHONPATH=src python -m unittest tests.test_runner.RunnerTests.test_versioned_and_custom_allowlisted_interpreters_reject_bundled_eval_flags -v` failed with 2 failures before the implementation fix.
- `PYTHONPATH=src python -m unittest tests.test_runner tests.test_reproduction -v`: passed; 18 focused tests ran successfully.
- `make test`: passed; 41 tests ran successfully.
- `PYTHONPATH=src python -m compileall -q src tests`: passed.
- `git diff --check`: passed before the fix commit.

### Concerns

- Process-group termination and `/proc` child-liveness assertions are Linux-oriented; the runner retains its direct-process fallback where process groups are unavailable.
- The runner keeps the existing explicit 1 MiB-per-stream capture cap and marks output beyond the cap as truncated.

## Fix round 3

### Status

Complete.

### Commit

`1b7878a` — `fix: scope command evaluation policy`

### Changes

- Scoped command-string rejection to shell/interpreter basenames, versioned interpreter patterns, and explicit `CommandPolicy.interpreter_commands`/`shell_commands` metadata instead of rejecting `-c`/`-e` for every tool.
- Added an ordinary-tool regression proving `echo -e hello` remains allowed.
- Strengthened the versioned/custom interpreter regression with marker-creating payloads and explicit custom interpreter metadata.
- Scoped `/proc` child-liveness assertions to Linux, added a portable POSIX fallback for PID checks, and made non-POSIX process termination explicitly direct-process behavior.

### Exact checks and results

- `PYTHONPATH=src python -m unittest tests.test_runner tests.test_reproduction -v`: passed; 19 focused tests ran successfully.
- `make test`: passed; 42 tests ran successfully.
- `PYTHONPATH=src python -m compileall -q src tests`: passed.
- `git diff --check`: passed before the fix commit.

### Concerns

- Linux process-group cleanup and `/proc` liveness assertions are covered on Linux; non-POSIX platforms use direct-parent termination and do not claim descendant cleanup.
- The runner keeps the existing explicit 1 MiB-per-stream capture cap and marks output beyond the cap as truncated.

## Fix round 4

### Status

Complete.

### Commit

`da61b69` — `fix: close bundled shell command gap`

### Changes

- Added positive-control executions for copied versioned and custom Python interpreters; each valid `-c` payload creates a marker before the runner-guarded forms are tested.
- Added regressions for bundled shell command-string options including `sh -ce`, `bash -ec`, and `bash -xec`, while retaining the ordinary-tool `echo -e hello` control.
- Rejected bundled short shell options containing `c` (for example, `-ec` and `-xec`) only for classified shell executables, preserving ordinary tool flags and existing POSIX timeout/artifact behavior.

### Exact checks and results

- `PYTHONPATH=src python -m unittest tests.test_runner.RunnerTests.test_shell_and_eval_forms_are_rejected_without_executing_marker -v`: initially failed for the new bundled shell cases (3 failures), then passed after the runner fix.
- `PYTHONPATH=src python -m unittest tests.test_runner.RunnerTests.test_shell_and_eval_forms_are_rejected_without_executing_marker tests.test_runner.RunnerTests.test_versioned_and_custom_allowlisted_interpreters_reject_bundled_eval_flags tests.test_runner.RunnerTests.test_ordinary_allowlisted_tool_keeps_legitimate_e_flag -v`: passed; 3 tests ran successfully.
- `PYTHONPATH=src python -m unittest tests.test_runner tests.test_reproduction -v`: passed; 19 focused tests ran successfully.
- `make test`: passed; 42 tests ran successfully.
- `PYTHONPATH=src python -m compileall -q src tests`: passed.
- `git diff --check`: passed.

### Concerns

- Shell bundled-option detection remains intentionally scoped to shell basenames or explicit `CommandPolicy.shell_commands`; ordinary tool options such as `echo -e hello` remain permitted.
- Linux process-group cleanup and `/proc` child-liveness assertions remain Linux-scoped; non-POSIX platforms retain direct-parent termination behavior.
