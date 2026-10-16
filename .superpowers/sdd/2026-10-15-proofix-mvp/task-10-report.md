# Task 10 implementation report

## Status

Complete.

## Commit

`98ba200088397482b78c4c63d32e750ac1e9e269` — `feat: add GitHub and OmniRush integration boundaries`

## Files changed

- `src/proofix/github.py`
- `.opencode/plugins/proofix.ts`
- `.opencode/skills/proofix/SKILL.md`
- `tests/test_github.py`
- `tests/test_plugin_contract.py`
- `README.md`
- `Dockerfile`
- `Makefile`

## Exact tests and results

- TDD red phase: `PYTHONPATH=src python -m unittest tests.test_github tests.test_plugin_contract -v` failed before implementation because the GitHub adapter, plugin, and skill files were absent.
- `PYTHONPATH=src python -m unittest tests.test_github tests.test_plugin_contract -v`: passed; 9 focused tests ran successfully.
- `make test`: passed; 121 tests ran successfully offline.
- `PYTHONPATH=src python -m compileall -q src tests`: passed.
- `git diff --check`: passed.

## Clean-checkout demo results

- Cloned commit `493d8c6` into a fresh temporary checkout, served the dashboard, and confirmed the root returned `200` HTML containing ProofFix.
- Started a local server with the checked-in recorded OmniRush fixture and an isolated Git copy of the broken-parser fixture.
- Created a run, reproduced the failing test, loaded recorded diagnosis, proposed and explicitly approved the root-cause `split("=", 1)` patch, verified the patched original/regression test, and reached `verified`.
- Exported both Markdown and JSON reports and confirmed the source checkout's fixture file bytes were unchanged.
- The direct `make demo` default fixture starts and serves correctly, but its nested fixture path is not itself a Git worktree root; the full reproduction protocol therefore used the documented disposable standalone fixture copy while preserving the source checkout.

## Concerns

- Docker image build was not run because Docker is unavailable in this execution environment; the Dockerfile still runs the pinned Python 3.12 offline test command.
- Live GitHub reads were not attempted because `gh` is not installed; missing CLI/auth/network paths are covered by blocked-read tests with safe next steps.
- The default checked-in fixture remains nested under the project Git worktree, so a full reproduction requires a disposable standalone Git copy as recorded above; no source files are modified.

## Fix round 1

### Status

Complete.

### Commit

`c806b608cdbf993344cee1b342235e016d16413a` — `fix: harden GitHub and plugin boundaries`

### Changes

- Replaced unbounded GitHub `capture_output=True` execution with `Popen` pipe readers that retain bounded prefixes, terminate on stdout/stderr overflow or timeout, and return blocked results with safe next steps.
- Rejected path-like and token-shaped GitHub identifiers, and redacted token-shaped values from returned command records and `to_dict()`.
- Added finite timeout, bounded text/list, safe command/scope path, and canonical UTC date validation to the plugin; added executable Node type-stripping behavioral coverage for invalid plans and approvals.
- Corrected the README to state that the dashboard loads existing runs through `?run=RUN_ID`, while localhost API calls create/advance/verify runs and the dashboard refreshes recorded state.

### Exact checks and results

- `PYTHONPATH=src python -m unittest tests.test_github tests.test_plugin_contract -v`: passed; 14 tests ran, with 1 expected skip because Node cannot type-strip during `--check`.
- `make test`: passed; 126 tests ran, with 1 expected syntax-check skip.
- `PYTHONPATH=src python -m compileall -q src tests`: passed.
- `git diff --check`: passed.
- `node --experimental-strip-types .opencode/plugins/proofix.ts`: passed.
- `node --check web/app.js`: passed.

### Corrected demo results

- From a fresh clone, served the dashboard, created a run through `POST /api/runs`, loaded `/?run=RUN_ID`, reproduced the fixture failure, loaded recorded diagnosis, proposed and approved the patch, verified through the documented API plan, and exported Markdown and JSON.
- Final run state was `verified`; the source checkout fixture remained byte-for-byte unchanged.

### Concerns

- The available Bun launcher is not usable in this container; Node 22's `--experimental-strip-types` executed the plugin and behavioral contract successfully, while `node --check` cannot strip TypeScript types and is explicitly skipped.
- Docker and live `gh` remain unavailable in this environment; blocked CLI/auth/network behavior is covered by focused tests.
