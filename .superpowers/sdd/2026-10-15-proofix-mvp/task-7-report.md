# Task 7 implementation report

## Status

Complete.

## Commit

`db77874c756b505c71e7c256eb5814a4ca5b3c12` — `feat: add auditable reports and offline demo fixture`

## Files changed

- `src/proofix/reports.py`
- `fixtures/broken-parser-repo/README.md`
- `fixtures/broken-parser-repo/parser.py`
- `fixtures/broken-parser-repo/tests/test_parser.py`
- `fixtures/broken-parser-repo/reset.sh`
- `tests/test_reports.py`
- `tests/test_fixture_repo.py`

## Tests and checks

- TDD red phase: `PYTHONPATH=src python -m unittest tests.test_reports tests.test_fixture_repo -v` failed before implementation because `proofix.reports` and the fixture repository were not present.
- `PYTHONPATH=src python -m unittest tests.test_reports tests.test_fixture_repo -v`: passed; 5 focused tests ran successfully.
- `make test`: passed; 92 tests ran successfully.
- `python -m py_compile src/proofix/reports.py tests/test_reports.py tests/test_fixture_repo.py fixtures/broken-parser-repo/parser.py fixtures/broken-parser-repo/tests/test_parser.py`: passed.
- `git diff --check`: passed.

## Concerns

- `write_report` resolves an opaque run ID from `PROOFIX_RUN_ROOT`, common local run directories, or accepts a persisted snapshot path; callers with a custom store should pass the snapshot path until the Task 8 server supplies its configured run root.
- Artifact bodies are intentionally omitted from exports; reports retain relative artifact references and bounded summaries so full logs remain auditable without leaking raw credentials.
- The demo fixture is intentionally broken until the one-line `split("=", 1)` root-cause patch is applied; `reset.sh` restores that baseline offline.

## Fix round 1

### Status

Complete.

### Commit

`1ee0085d4f8be7d65ac4266776df002859b23963` — `fix: harden task 7 reports and demo fixture`

### Changes

- Made credential-assignment redaction work inside arbitrary strings and remain idempotent across duplicated run/event payloads.
- Derived recorded/demo provenance from persisted event `details.mode` and preserved exact runner commands, exit codes, durations, artifact references, and bounded summaries.
- Persisted repository commit, branch, remote, snapshot mode, and materialization metadata in reproduction events and run evidence for restart-safe exports.
- Expanded JSON/Markdown export coverage for real records, directory destinations, artifact summaries, and embedded credential cases.
- Corrected the fixture's root-cause line and observed `ValueError`, and verified reset returns the fixture to the failing state offline.

### Exact checks and results

- TDD red phase: `PYTHONPATH=src python -m unittest tests.test_reports tests.test_fixture_repo tests.test_orchestrator -v` initially failed on embedded redaction, event provenance, runner artifact fields, and persisted snapshot expectations.
- `PYTHONPATH=src python -m unittest tests.test_reports tests.test_fixture_repo tests.test_orchestrator -v`: passed; 23 focused tests ran successfully.
- `make test`: passed; 95 tests ran successfully.
- `PYTHONPATH=src python -m compileall -q src tests fixtures/broken-parser-repo`: passed.
- `git diff --check`: passed.

### Concerns

- Repositories without an origin or an available branch name retain explicit `null` metadata for those fields; commit and snapshot mode remain persisted.
- Full logs remain outside reports behind artifact references; inline summaries are redacted and bounded to preserve auditability without embedding raw output.
