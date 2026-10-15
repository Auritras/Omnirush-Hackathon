# ProofFix MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible localhost ProofFix workflow that accepts a failing command, proves or rejects reproduction in a disposable workspace, records evidence-backed diagnosis and patch approval, verifies the approved patch with real commands, and exports an auditable report.

**Architecture:** A Python 3.12 standard-library local server owns the run state, event log, safety policy, disposable worktrees, deterministic command execution, and report generation. A vanilla HTML/CSS/JavaScript dashboard reads snapshots and events through the local HTTP server. OmniRush integration is isolated behind a validated session-client boundary with recorded fixtures for offline tests and demo mode; no other generative provider is called.

**Tech Stack:** Python 3.12 standard library, `unittest`, `http.server`, Git CLI, optional `gh` CLI, vanilla HTML/CSS/JavaScript, JSON and JSONL artifacts, Docker, Make.

**Spec:** `docs/superpowers/specs/2026-10-15-proofix-design.md`

## Global Constraints

- Support Python first; defer Node compatibility.
- Use `gh` CLI for the initial GitHub adapter; GitHub writes remain separately approval-gated and are not required for the core offline demo.
- Start the server with a local CLI; keep plugin integration as an adapter around the same orchestrator rather than a second state machine.
- Always use disposable worktrees or copied workspaces for experiments and verification; never modify the user's working tree during a run.
- Use a checked-in fixture repository and recorded OmniRush response fixtures so the test suite and demo protocol run without a model provider.
- The local orchestrator, not an agent or browser, owns all state transitions and verification statuses.
- Validate every model response against a schema and require evidence references for claims used in state transitions.
- Redact likely credentials before agent context, browser display, or exported reports.
- Keep large command output and diffs in run artifacts; snapshots contain bounded summaries and references.
- Keep repository paths and served artifact paths contained within explicitly authorized roots.
- Never auto-merge, force-push, deploy, or expose GitHub credentials to an OmniRush session.
- The project must contain a pinned Python runtime, lockfile or dependency manifest, Dockerfile, offline `make test`, `.env.example`, and README setup/run/test/deploy instructions.

## Review Focus

- A user enters a command containing shell metacharacters or a destructive operation; the runner must reject or safely constrain it rather than execute arbitrary host commands. **Test in Task 4.**
- A repository has a dirty working tree, ignored secret files, or untracked files; the snapshot must be explicit and must not silently leak or overwrite them. **Test in Task 3.**
- A command succeeds, fails differently, or cannot start because tooling is missing; the reproduction result must distinguish `not_reproduced` from `blocked`. **Test in Task 4.**
- A model response omits evidence, contains invalid paths, or uses an unknown schema version; the orchestrator must reject it without changing run state. **Test in Task 5.**
- A verification command fails after approval or the server restarts mid-run; the report must show the failed check and preserve an auditable state rather than claim success. **Test in Tasks 6 and 8.**

---

### Task 1: Establish the reproducible repository baseline

**Files:**
- Create: `.python-version`
- Create: `pyproject.toml`
- Create: `Makefile`
- Create: `Dockerfile`
- Create: `.env.example`
- Create: `.gitignore`
- Modify: `README.md`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Produces the project commands `make test`, `make run`, and `make demo`.
- Pins Python 3.12 and declares that the implementation uses only the standard library for the MVP.

- [ ] **Step 1: Write the failing smoke test**

  Add `tests/test_smoke.py` with a test that imports `proofix` from `src/` and asserts the package exposes a version string.

- [ ] **Step 2: Run the test to verify the baseline fails**

  Run: `make test`

  Expected: FAIL because the package and test runner configuration do not exist yet.

- [ ] **Step 3: Add the runtime and project configuration**

  Configure a `src` layout, Python 3.12, an offline `unittest discover` command, and a `make test` target that sets `PYTHONPATH=src`. Add a Dockerfile that installs Git and the pinned Python runtime, copies the repository, and runs the same test command. Add `.env.example` with placeholders only.

- [ ] **Step 4: Add the minimal package and README commands**

  Create `src/proofix/__init__.py` with the version constant used by the smoke test. Document exact setup, `make test`, `make run`, `make demo`, Docker build/run, and deployment limitations in `README.md`; state that the MVP is localhost-only and does not deploy a hosted service.

- [ ] **Step 5: Run the baseline checks**

  Run: `make test`

  Expected: PASS with the smoke test discovered offline.

- [ ] **Step 6: Commit**

  ```bash
  git add .python-version pyproject.toml Makefile Dockerfile .env.example .gitignore README.md src/proofix tests/test_smoke.py
  git commit -m "chore: establish reproducible project baseline"
  ```

### Task 2: Define run records, events, and validated state transitions

**Files:**
- Create: `src/proofix/models.py`
- Create: `src/proofix/state.py`
- Create: `src/proofix/store.py`
- Test: `tests/test_state.py`

**Interfaces:**
- `models.py` defines typed JSON-compatible records for `RunRecord`, `EventRecord`, `EvidenceRecord`, `CheckResult`, and `PatchProposal`.
- `state.py` exposes `ALLOWED_TRANSITIONS`, `can_transition(current, target) -> bool`, and `transition(run, target, *, event_kind, details) -> RunRecord`.
- `store.py` exposes `RunStore(root: Path)`, `create_run(intake) -> RunRecord`, `get_run(run_id) -> RunRecord`, `append_event(event) -> None`, `list_events(run_id) -> list[EventRecord]`, and `replay(run_id) -> RunRecord`.

- [ ] **Step 1: Write tests for valid and invalid transitions**

  Cover the complete happy-path sequence through `verified`, terminal `not_reproduced`, `blocked`, `verification_failed`, and `verification_blocked`; assert invalid skips and agent-supplied status changes raise a domain error.

- [ ] **Step 2: Write tests for JSONL persistence and replay**

  Assert a created run writes an immutable snapshot, events append in order, large fields are represented by artifact references, and replay reconstructs the last valid state after a process restart.

- [ ] **Step 3: Implement the model and transition layer**

  Use dataclasses with explicit `schema_version`, UTC timestamps, opaque run/event IDs, and JSON serialization helpers. Encode the state graph from the design, including an explicit `github_pending_approval` state and terminal outcomes.

- [ ] **Step 4: Implement the append-only store**

  Write run snapshots and per-run JSONL events under a configured data root. Validate event run IDs, monotonically order event IDs, and reject malformed records instead of silently repairing them.

- [ ] **Step 5: Run focused and full tests**

  Run: `python -m unittest tests.test_state -v` and `make test`

  Expected: PASS; invalid transitions and malformed events are rejected.

- [ ] **Step 6: Commit**

  ```bash
  git add src/proofix/models.py src/proofix/state.py src/proofix/store.py tests/test_state.py
  git commit -m "feat: add validated run state and event store"
  ```

### Task 3: Add repository snapshots, path containment, and redaction

**Files:**
- Create: `src/proofix/paths.py`
- Create: `src/proofix/redaction.py`
- Create: `src/proofix/worktree.py`
- Test: `tests/test_safety.py`

**Interfaces:**
- `paths.py` exposes `resolve_repository(path, allowed_roots) -> Path`, `ensure_contained(path, root) -> Path`, and `safe_artifact_path(run_root, relative_path) -> Path`.
- `redaction.py` exposes `redact_text(text) -> tuple[str, list[str]]` and `redact_environment(environ) -> dict[str, str]` where values are omitted or masked by default.
- `worktree.py` exposes `WorkspaceManager(run_root)`, `snapshot_repository(repository, *, snapshot_mode) -> WorkspaceSnapshot`, and `cleanup(snapshot) -> None`.

- [ ] **Step 1: Write safety tests first**

  Cover parent traversal, symlink escape, repositories outside allowed roots, token-like strings in logs, `.env` content, environment-value omission, and preservation of a dirty source repository after a disposable snapshot is created.

- [ ] **Step 2: Implement path and artifact containment**

  Resolve paths before validation, reject paths outside configured roots, and serve artifacts only through paths that remain beneath the run directory after resolution.

- [ ] **Step 3: Implement redaction**

  Mask common bearer tokens, GitHub token forms, private-key blocks, password/API-key assignments, and secret environment values while preserving line/column context needed for evidence.

- [ ] **Step 4: Implement disposable repository snapshots**

  Default to a clean committed snapshot using Git worktrees when possible; record the selected snapshot mode and commit. Make dirty/untracked inclusion explicit rather than implicit, and exclude obvious secret files from copied artifacts.

- [ ] **Step 5: Run the safety suite**

  Run: `python -m unittest tests.test_safety -v` and `make test`

  Expected: PASS with all containment and redaction cases covered.

- [ ] **Step 6: Commit**

  ```bash
  git add src/proofix/paths.py src/proofix/redaction.py src/proofix/worktree.py tests/test_safety.py
  git commit -m "feat: enforce workspace containment and redaction"
  ```

### Task 4: Implement the bounded command runner and reproduction gate

**Files:**
- Create: `src/proofix/runner.py`
- Create: `src/proofix/reproduction.py`
- Test: `tests/test_runner.py`
- Test: `tests/test_reproduction.py`

**Interfaces:**
- `runner.py` exposes `CommandPolicy`, `CommandResult`, and `run_command(command, cwd, *, policy, environment=None) -> CommandResult`.
- `reproduction.py` exposes `ReproductionStatus` with `reproduced`, `not_reproduced`, and `blocked`, plus `compare_failure(input_failure, command_result) -> ReproductionAssessment`.

- [ ] **Step 1: Write command-policy tests**

  Assert allowlisted commands run in the disposable workspace, shell injection syntax is rejected, timeouts terminate the process group, output is bounded and stored as an artifact, and environment values are not captured by default.

- [ ] **Step 2: Write reproduction-gate tests**

  Cover matching failing exit code and normalized failure evidence as `reproduced`; success or materially different failure as `not_reproduced`; missing executable, missing required input, and unavailable runtime as `blocked`.

- [ ] **Step 3: Implement the bounded runner**

  Execute without an implicit shell where possible, use a fixed working directory and timeout, capture exit code/stdout/stderr/duration, terminate child processes on timeout, and write full redacted output to run artifacts with digests in the result.

- [ ] **Step 4: Implement evidence comparison**

  Compare exit status and normalized failure signatures rather than raw timestamps or absolute paths. Record mismatches and the reason for each gate status.

- [ ] **Step 5: Run focused and full tests**

  Run: `python -m unittest tests.test_runner tests.test_reproduction -v` and `make test`

  Expected: PASS; no test executes outside its disposable workspace.

- [ ] **Step 6: Commit**

  ```bash
  git add src/proofix/runner.py src/proofix/reproduction.py tests/test_runner.py tests/test_reproduction.py
  git commit -m "feat: add bounded runner and reproduction gate"
  ```

### Task 5: Add intake, triage, and the OmniRush response boundary

**Files:**
- Create: `src/proofix/intake.py`
- Create: `src/proofix/schemas.py`
- Create: `src/proofix/omnirush.py`
- Create: `src/proofix/orchestrator.py`
- Create: `fixtures/omnirush/reproducible-run.json`
- Create: `fixtures/omnirush/not-reproduced-run.json`
- Test: `tests/test_intake.py`
- Test: `tests/test_orchestrator.py`
- Test: `tests/test_schemas.py`

**Interfaces:**
- `intake.py` exposes `IntakeRequest` and `sanitize_intake(payload) -> IntakeRequest`.
- `schemas.py` exposes `validate_triage(payload)`, `validate_hypothesis(payload)`, and `validate_patch_proposal(payload)`; each returns a typed record or raises `SchemaError`.
- `omnirush.py` exposes `OmniRushSessionClient`, `RecordedSessionClient`, and `run_specialists(context, client) -> list[Hypothesis]`.
- `orchestrator.py` exposes `ProofFixOrchestrator(store, workspace_manager, runner, session_client)` and `start_run(request) -> str`, `advance_reproduction(run_id) -> RunRecord`, and `advance_diagnosis(run_id) -> RunRecord`.

- [ ] **Step 1: Write intake and schema tests**

  Assert commands and failure text are bounded and sanitized, URLs are parsed as optional references, unknown fields do not become agent instructions, triage/hypothesis/patch responses require schema version and evidence references, invalid paths are rejected, and malformed recorded responses cannot advance state.

- [ ] **Step 2: Write orchestrator integration tests with recorded sessions**

  Use a temporary fixture repository and recorded OmniRush responses to prove the reproduced path advances only after a real failing command, while the non-reproduced and blocked paths terminate with evidence and never request diagnosis.

- [ ] **Step 3: Implement intake and schema validation**

  Keep protocol records JSON-compatible and reject missing evidence, unknown evidence kinds, out-of-repository paths, confidence values outside `[0, 1]`, and unsupported schema versions.

- [ ] **Step 4: Implement the OmniRush adapter boundary**

  Make recorded responses the offline default. Define the live client interface without calling any other model provider; attach OmniRush session IDs and specialist roles to events and reports when supplied by the runtime.

- [ ] **Step 5: Implement triage, reproduction orchestration, and diagnosis gating**

  Persist immutable intake, invoke the local runner, transition to the exact reproduction status, stop on `not_reproduced` or `blocked`, and only launch competing specialists after `reproduced`. Treat repository files, issue text, and logs as untrusted context.

- [ ] **Step 6: Run focused and full tests**

  Run: `python -m unittest tests.test_intake tests.test_schemas tests.test_orchestrator -v` and `make test`

  Expected: PASS; a model response cannot bypass deterministic gates.

- [ ] **Step 7: Commit**

  ```bash
  git add src/proofix/intake.py src/proofix/schemas.py src/proofix/omnirush.py src/proofix/orchestrator.py fixtures/omnirush tests/test_intake.py tests/test_schemas.py tests/test_orchestrator.py
  git commit -m "feat: add guarded intake and OmniRush orchestration"
  ```

### Task 6: Implement experiments, patch validation, approval, and verification

**Files:**
- Create: `src/proofix/experiments.py`
- Create: `src/proofix/patches.py`
- Create: `src/proofix/verification.py`
- Modify: `src/proofix/orchestrator.py`
- Test: `tests/test_experiments.py`
- Test: `tests/test_patches.py`
- Test: `tests/test_verification.py`

**Interfaces:**
- `experiments.py` exposes `ExperimentSpec`, `ExperimentResult`, and `run_experiment(spec, workspace, runner) -> ExperimentResult` for the explicit vocabulary in the design only.
- `patches.py` exposes `validate_unified_diff(diff, repository_root) -> PatchProposal`, `approve_patch(run_id, patch_digest, actor, timestamp)`, and `reject_patch(run_id, actor, timestamp)`.
- `verification.py` exposes `VerificationPlan` and `verify_patch(patch, workspace, runner, plan) -> VerificationSummary`.

- [ ] **Step 1: Write experiment and patch-safety tests**

  Cover each supported experiment kind, timeout and artifact recording, malformed unified diffs, absolute/path-traversal paths, changes outside the repository, unapproved patch application, and approval records containing timestamp, actor, and digest.

- [ ] **Step 2: Write verification tests**

  Use fixture scenarios for a symptom-only patch that fails the regression test, a correct patch that passes, an existing-test failure, a syntax/diff-check failure, and a blocked command; assert checks run in the specified order and statuses are deterministic.

- [ ] **Step 3: Implement the explicit experiment vocabulary**

  Translate validated specs into runner calls; never execute arbitrary commands supplied by an agent. Record hypothesis linkage, command, exit code, output digest, changed files, and timeout status.

- [ ] **Step 4: Implement diff validation and approval transitions**

  Parse unified diffs, enforce repository containment and scope, compute a patch digest, keep proposals unapplied until explicit approval, and record reject/request-another-diagnosis actions without mutating the source worktree.

- [ ] **Step 5: Implement disposable application and ordered verification**

  Apply only an approved diff to a fresh verification workspace after `git diff --check`; run the original failure command, regression test, selected relevant tests, optional bounded broader suite, and syntax/scope checks in order.

- [ ] **Step 6: Run focused and full tests**

  Run: `python -m unittest tests.test_experiments tests.test_patches tests.test_verification -v` and `make test`

  Expected: PASS; no agent prose can create `verified`, and failed verification retains the exact failing check.

- [ ] **Step 7: Commit**

  ```bash
  git add src/proofix/experiments.py src/proofix/patches.py src/proofix/verification.py src/proofix/orchestrator.py tests/test_experiments.py tests/test_patches.py tests/test_verification.py
  git commit -m "feat: add guarded experiments and verification"
  ```

### Task 7: Build report generation and the checked-in demo fixture

**Files:**
- Create: `src/proofix/reports.py`
- Create: `fixtures/broken-parser-repo/README.md`
- Create: `fixtures/broken-parser-repo/parser.py`
- Create: `fixtures/broken-parser-repo/tests/test_parser.py`
- Create: `fixtures/broken-parser-repo/reset.sh`
- Create: `tests/test_reports.py`
- Create: `tests/test_fixture_repo.py`

**Interfaces:**
- `reports.py` exposes `render_markdown(run, events, artifacts) -> str`, `render_json(run, events) -> str`, and `write_report(run_id, destination, format) -> Path`.
- The fixture reset command restores the known broken state without network access and the fixture test command fails before the intended patch and passes after it.

- [ ] **Step 1: Write report tests**

  Assert Markdown and JSON include the run ID, repository snapshot, exact commands, exit codes, redaction notices, hypotheses with evidence and contradictions, approvals, patch digest, every verification check, terminal status, and OmniRush session IDs. Assert secrets and raw credentials never appear.

- [ ] **Step 2: Write fixture behavior tests**

  Assert the fixture has a realistic parser root-cause failure, a nearby misleading symptom, a missing regression case, and a quick deterministic reset; assert the pre-fix command fails.

- [ ] **Step 3: Implement the fixture repository**

  Keep it small and dependency-free. Make the broken behavior and correct patch obvious enough for a recorded demo while still requiring evidence to distinguish symptom from cause.

- [ ] **Step 4: Implement report rendering**

  Render evidence links to files, lines, commands, and artifact references; label recorded/demo results separately from live OmniRush results; bound inline logs and keep full content in artifacts.

- [ ] **Step 5: Run focused and full tests**

  Run: `python -m unittest tests.test_reports tests.test_fixture_repo -v` and `make test`

  Expected: PASS; a report exported without applying a patch remains useful and clearly unverified.

- [ ] **Step 6: Commit**

  ```bash
  git add src/proofix/reports.py fixtures/broken-parser-repo tests/test_reports.py tests/test_fixture_repo.py
  git commit -m "feat: add auditable reports and offline demo fixture"
  ```

### Task 8: Add the localhost HTTP API and CLI lifecycle

**Files:**
- Create: `src/proofix/server.py`
- Create: `src/proofix/cli.py`
- Modify: `src/proofix/__init__.py`
- Modify: `Makefile`
- Test: `tests/test_server.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- `server.py` exposes `create_server(config) -> ThreadingHTTPServer` with read endpoints for runs, events, artifacts, and reports plus explicit action endpoints for intake, approval, rejection, diagnosis retry, verification, and export.
- `cli.py` exposes `main(argv=None) -> int` with `run`, `demo`, and `export` subcommands.

- [ ] **Step 1: Write API and lifecycle tests**

  Assert intake creates a run, GET endpoints render snapshots/events, artifact traversal is denied, action endpoints require the expected state and patch digest, malformed JSON gets a safe error, and `make run` starts the server without a global install.

- [ ] **Step 2: Implement the CLI wiring**

  Construct the store, workspace manager, runner, recorded/live session adapter, and orchestrator from local configuration. Make `demo` select the checked-in fixture and recorded responses while labeling the run as demo/recorded.

- [ ] **Step 3: Implement the HTTP API**

  Use the standard-library HTTP server, JSON responses, bounded request bodies, explicit content types, and state-changing calls that delegate to the orchestrator rather than mutating snapshots directly.

- [ ] **Step 4: Add the server lifecycle targets**

  Wire `make run` and `make demo`, document host/port and data-root configuration, and keep all process data beneath a controlled local directory.

- [ ] **Step 5: Run focused and full tests**

  Run: `python -m unittest tests.test_server tests.test_cli -v` and `make test`

  Expected: PASS; the API cannot infer success from client-provided status fields.

- [ ] **Step 6: Commit**

  ```bash
  git add src/proofix/server.py src/proofix/cli.py src/proofix/__init__.py Makefile tests/test_server.py tests/test_cli.py
  git commit -m "feat: expose ProofFix localhost lifecycle"
  ```

### Task 9: Build the proof-chain dashboard

**Files:**
- Create: `web/index.html`
- Create: `web/styles.css`
- Create: `web/app.js`
- Modify: `src/proofix/server.py`
- Test: `tests/test_web_assets.py`

**Interfaces:**
- The dashboard consumes the read-only API from Task 8 and renders the run snapshot/event stream without maintaining an independent workflow state.
- `web/app.js` provides accessible stage-rail, reproduction, hypothesis, patch-review, verification, evidence, and export views for every terminal state.

- [ ] **Step 1: Write asset and contract tests**

  Assert the HTML includes labeled controls, a live-region/status target, keyboard-accessible approval controls, and references to the static assets; assert the API serves assets only from `web/` and applies a safe content type.

- [ ] **Step 2: Implement the semantic dashboard structure**

  Show repository/branch/commit/run status, stage rail, exact reproduction command and exit code, bounded output, hypothesis evidence/contradictions, diff/risk/regression review, verification checks, evidence drawer, and Markdown/JSON export controls.

- [ ] **Step 3: Implement the dashboard behavior**

  Poll or refresh from snapshots/events, render explicit `not_reproduced`, `blocked`, `verification_failed`, and `verified` states, disable unavailable actions by server state, and visibly distinguish demo/recorded data from live runs.

- [ ] **Step 4: Apply accessibility and responsive styling**

  Add non-color status labels, visible focus states, narrow-width layout, bounded log scrolling, reduced-motion handling, and accessible names for all controls.

- [ ] **Step 5: Run browser-independent asset checks and the full suite**

  Run: `python -m unittest tests.test_web_assets -v` and `make test`

  Expected: PASS; all dashboard behavior remains server-state-driven.

- [ ] **Step 6: Commit**

  ```bash
  git add web src/proofix/server.py tests/test_web_assets.py
  git commit -m "feat: add proof-chain dashboard"
  ```

### Task 10: Add the GitHub read adapter, plugin boundary, and final clean-checkout verification

**Files:**
- Create: `src/proofix/github.py`
- Create: `.opencode/plugins/proofix.ts`
- Create: `.opencode/skills/proofix/SKILL.md`
- Create: `tests/test_github.py`
- Create: `tests/test_plugin_contract.py`
- Modify: `README.md`
- Modify: `Dockerfile`
- Modify: `Makefile`

**Interfaces:**
- `github.py` exposes `GitHubReader(gh_path="gh")` with read-only methods for repository metadata, issue text, pull-request diff, Actions status, and logs; command construction returns argument arrays and never embeds credentials.
- The plugin exposes guarded operations matching `proofix.start_run`, `proofix.get_run`, `proofix.approve_patch`, `proofix.reject_patch`, `proofix.verify_run`, and `proofix.export_report`, delegating to the localhost orchestrator/API.

- [ ] **Step 1: Write adapter and plugin contract tests**

  Assert `gh` arguments are constructed without shell interpolation or token values, missing authentication is represented as a blocked read, GitHub write methods are absent from the default reader, plugin operations validate arguments and current state, and the plugin cannot request unrestricted file or shell access.

- [ ] **Step 2: Implement read-only GitHub import**

  Use `gh` only when explicitly requested and available, parse bounded JSON/text output, redact logs before storage, and represent unavailable CLI/auth/network as a non-success state with next steps.

- [ ] **Step 3: Implement the plugin and skill contract**

  Keep the plugin as a thin guarded adapter over the same state store and orchestrator. Document permissions, recorded-fixture mode, evidence requirements, and the approval boundary in `SKILL.md`; do not create a competing run state.

- [ ] **Step 4: Finish reproducibility documentation and packaging checks**

  Document the exact clean-checkout commands, fixture reset, dashboard flow, optional `gh` setup, Docker build/run, report export, and the fact that PR creation is approval-gated stretch functionality. Ensure the Dockerfile and Makefile run without network after dependencies are present.

- [ ] **Step 5: Run the complete verification matrix**

  Run: `make test`

  Then from a fresh checkout run the README end-to-end demo: start the local server, open the dashboard, select the fixture, run the failing command, observe reproduction, approve the root-cause patch, observe verification, and export Markdown and JSON reports.

  Expected: all tests pass offline; the fixture flow uses real commands, requires explicit approval, produces a verified report, and leaves the source working tree untouched.

- [ ] **Step 6: Commit**

  ```bash
  git add src/proofix/github.py .opencode/plugins/proofix.ts .opencode/skills/proofix/SKILL.md tests/test_github.py tests/test_plugin_contract.py README.md Dockerfile Makefile
  git commit -m "feat: add GitHub and OmniRush integration boundaries"
  ```

## Self-review against the design

- Product intent and stop conditions are covered by Tasks 2, 4, 5, and 6.
- Local intake, triage, reproduction, competing diagnoses, experiments, patch approval, verification, reports, and dashboard are covered by Tasks 5 through 9.
- GitHub read operations and approval-gated integration are covered by Task 10; automatic merge, force-push, deployment, and hosted SaaS remain out of scope.
- Structured schemas, provenance, redaction, containment, bounded commands, and disposable workspaces are covered by Tasks 2 through 6 and their tests.
- The fixture, offline recorded protocol, Dockerfile, pinned runtime, README, and single test command are covered by Tasks 1, 5, 7, 8, and 10.
- The plan intentionally resolves open implementation choices as builder calls: Python 3.12, standard library, localhost CLI startup, recorded responses for offline mode, read-only `gh` in the MVP, and disposable worktrees for all experiments and verification.
- Remaining owner-dependent choices are isolated behind interfaces: live OmniRush session transport, optional GitHub writes, and whether the public demo account/repository is used.
