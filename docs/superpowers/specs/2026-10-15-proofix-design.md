# ProofFix Design

**Date:** 2026-10-15  
**Status:** Design draft for builder review; implementation is not approved yet  
**Working title:** ProofFix  
**Primary buildathon track:** Agents & Automation  
**Secondary track:** Developer Tools & Open Source

## 1. Product intent

ProofFix is a local-first, GitHub-aware build-failure investigation tool. It turns a failing build, test, or GitHub Actions run into a reproducible diagnosis, a human-approved patch, and a verified evidence report.

The product's central promise is:

> **Reproduce first. Patch second. Prove the result.**

ProofFix must be willing to stop. If it cannot reproduce a failure in a controlled workspace, it must not fabricate a diagnosis or generate a patch. If a patch does not pass the original failure and a regression test, it must not be called fixed.

ProofFix is not intended to replace a general coding agent. It is a guarded workflow around failure investigation, with deterministic gates surrounding OmniRush reasoning.

## 2. User and concrete moment

The primary user is an individual developer or maintainer who receives a failing local test, build, or GitHub Actions run and needs to know what happened and whether a proposed fix is safe.

The motivating moment is:

> “CI failed. I have a log and a repository, but I do not know whether this is a real regression, an environment problem, or a flaky test. I want a trustworthy path from failure to a verified pull request without letting an agent blindly edit my code.”

The first demo user will work with a deliberately broken sample repository. The demo must be reproducible without private credentials or network-dependent model providers.

## 3. Goals

### MVP goals

1. Accept a local repository path and a user-provided failing command.
2. Accept pasted failure output and optionally a GitHub Actions run or issue reference.
3. Triage the failure into a small set of categories.
4. Run the actual command in an isolated temporary copy and record whether it reproduces.
5. Stop with an evidence-backed report when reproduction fails.
6. When reproduction succeeds, ask multiple OmniRush sub-agents for competing diagnoses.
7. Require every diagnosis to cite evidence and propose a targeted experiment.
8. Run deterministic experiments and rank hypotheses using their observed results.
9. Generate a minimal patch and a regression test proposal.
10. Require explicit human approval before applying a patch.
11. Verify the original failure, the regression test, and the relevant existing tests.
12. Export a Markdown and JSON evidence report.
13. Provide a polished localhost dashboard showing the workflow and evidence.
14. Demonstrate the complete flow in a public, reproducible repository.

### GitHub MVP goals

GitHub integration is read-first and approval-gated:

- Import repository metadata, branch, commit, issue text, pull-request diff, or failed Actions logs.
- Use the GitHub CLI when available, with clear setup instructions.
- Create a branch, commit an approved patch, open a pull request, and post the report only after explicit approval.
- Never auto-merge, force-push, or expose the user's token to an OmniRush agent.

### Success criteria

The MVP is successful when a fresh user can:

1. Start the local app.
2. Select the supplied demo repository.
3. Provide a failing command or import a prepared CI failure.
4. Watch triage, reproduction, diagnosis, patch, and verification stages.
5. See the system stop or proceed based on real command results.
6. Approve a patch.
7. See a passing regression test and verification summary.
8. Download a report that another developer can audit.

## 4. Non-goals and anti-goals

### Non-goals for the hackathon MVP

- Supporting every language, build system, and CI provider.
- Autonomous production deployments.
- Automatic merge or release.
- A hosted multi-tenant SaaS backend.
- A general-purpose chat interface.
- Arbitrary browser automation.
- Long-running cloud jobs.
- Building a custom foundation model or direct competing model API integration.
- Fully automatic GitHub OAuth/App infrastructure if it threatens the core demo.

### Anti-goals

ProofFix must not:

- Claim a failure was reproduced when the command did not actually fail.
- Call a patch verified based only on an agent's prose.
- Apply code changes without human approval.
- Hide uncertainty or contradictory evidence.
- Send raw secrets, access tokens, or unredacted private logs into prompts.
- overwrite the user's working tree without an explicit, reversible action.
- make a large refactor when a focused fix is sufficient.

## 5. Core workflow

```text
Input failure
    ↓
[1] Triage
    ↓
[2] Reproduce in isolated workspace
    ├── not reproduced → STOP + evidence report
    └── reproduced
            ↓
       [3] Diagnose competing hypotheses
            ↓
       [4] Test hypotheses with focused experiments
            ↓
       [5] Propose minimal and root-cause patches
            ↓
       Human approval gate
            ↓
       [6] Apply approved patch in a disposable worktree
            ↓
       [7] Verify original failure + regression + relevant suite
            ├── failed → return to diagnosis with evidence
            └── passed → verified report
            ↓
       [8] Optional GitHub branch / commit / pull request
```

### Stage 0: Intake

The user supplies:

- Repository path or GitHub repository reference
- Failing command, such as `pytest tests/test_parser.py` or `npm test -- parser`
- Failure output, optionally pasted or imported from GitHub Actions
- Optional issue URL, pull-request URL, commit SHA, or branch
- Optional constraints, such as “do not change dependencies”

ProofFix creates a run ID and stores an immutable intake record. Input fields are sanitized before being placed into agent context.

### Stage 1: Triage

The triage agent classifies the failure and identifies likely relevant areas. It may use read-only repository inspection and the supplied output, but it must not edit files.

Supported initial categories:

- Test assertion failure
- Build or compilation failure
- Type-check or lint failure
- Dependency or lockfile failure
- Environment or configuration failure
- Permission or filesystem failure
- Timeout, flaky, or nondeterministic failure
- Unknown

Triage output must include category, suspected subsystem, relevant files, command assumptions, and confidence.

### Stage 2: Reproduction

The local orchestrator—not the model—runs the failing command in a disposable worktree or copied workspace. It records the exact command, runtime information, exit code, stdout, stderr, duration, and relevant environment metadata.

The reproduction gate has three states:

- `reproduced`: the command fails in the controlled workspace with materially matching evidence;
- `not_reproduced`: the command succeeds, cannot start, or fails differently;
- `blocked`: required tooling or input is missing.

Only `reproduced` can advance to diagnosis. `not_reproduced` and `blocked` produce a terminal report with next steps.

### Stage 3: Diagnosis

After reproduction, ProofFix launches several focused OmniRush sub-agents in parallel. They share the intake record and reproduction evidence but have distinct responsibilities:

- **Stack-trace analyst:** traces the failure through relevant code paths.
- **Recent-change analyst:** inspects Git history and the relevant diff.
- **Dependency/environment analyst:** checks versions, configuration, and external assumptions.
- **Test analyst:** examines test intent, fixtures, setup, and missing coverage.
- **Code-path analyst:** follows the observed input through implementation branches.

Each agent returns structured JSON, not only prose:

```json
{
  "hypothesis": "Configuration is parsed before the environment defaults are loaded",
  "supporting_evidence": [
    {"kind": "file", "path": "src/config.py", "line": 42},
    {"kind": "command", "command": "pytest tests/test_config.py", "result": "failed"}
  ],
  "contradicting_evidence": [],
  "confidence": 0.78,
  "experiment": "Run the focused test with the default-loading step instrumented"
}
```

The lead diagnosis step ranks hypotheses but cannot mark one as proven without experiment evidence.

### Stage 4: Hypothesis experiments

The orchestrator runs safe, focused experiments proposed by the leading hypotheses. The MVP should support a small explicit experiment vocabulary rather than arbitrary agent commands:

- Run a named test or build command.
- Run with one controlled environment-variable change.
- Add and remove temporary instrumentation in the disposable worktree.
- Compare behavior against the parent commit.
- Inspect a dependency or runtime version.
- Run a proposed regression test.

Every experiment records its command, exit code, output digest, changed files, and relationship to the hypothesis. Experiments must run in a disposable workspace and have timeouts.

### Stage 5: Patch proposal

The patch agent receives the winning evidence and produces:

1. A minimal symptom patch, when relevant.
2. A root-cause patch, when different from the symptom patch.
3. A regression test.
4. A risk summary.
5. A verification plan.

The orchestrator validates the patch as a unified diff, checks that it stays within the intended repository, and rejects malformed or out-of-scope changes. The patch is not applied to the user's working tree yet.

### Human approval gate

The dashboard must present a review card containing:

- Diagnosis and evidence
- Files and lines changed
- Proposed regression test
- New dependencies, if any
- Risk and rollback information
- Commands ProofFix will run after approval

Buttons:

- `Approve patch`
- `Reject patch`
- `Request another diagnosis`
- `Export report without applying`

Approval must be explicit and recorded with timestamp, user action, and patch digest.

### Stage 6: Apply

The approved diff is applied to a disposable verification worktree. The user's original working tree remains untouched until a later, separately confirmed export or GitHub action.

ProofFix must run `git diff --check` or an equivalent patch validation step before tests.

### Stage 7: Verification

Verification is deterministic and owned by the local orchestrator. It must run, in order:

1. The original failing command.
2. The new regression test.
3. Relevant existing tests selected from the diagnosis.
4. A bounded broader test command when configured.
5. Diff scope and syntax checks.

The status is:

- `verified`: all required checks pass;
- `verification_failed`: one or more checks fail, with a link back to the failing stage;
- `verification_blocked`: the required command cannot run.

An agent cannot override these statuses.

### Stage 8: GitHub action

GitHub is an optional adapter, not a requirement for the local workflow.

Read operations:

- Repository metadata
- Default branch and commit
- Issue body and comments
- Pull-request diff
- GitHub Actions run status and logs

Approval-gated write operations:

- Create a branch
- Commit the approved patch
- Push the branch
- Open a pull request
- Add the ProofFix report as a PR comment

The first implementation may use the authenticated `gh` CLI to reduce OAuth work. A future GitHub App can provide a polished least-privilege login flow.

## 6. OmniRush integration

ProofFix must use OmniRush as its only generative AI runtime. It must not call OpenAI, Anthropic, Google, or another generative provider directly.

### Native integration shape

The project should contain an OmniRush plugin and skill:

```text
.opencode/
  plugins/
    proofix.ts
  skills/
    proofix/
      SKILL.md
```

The plugin should expose a small set of guarded operations or commands:

- `proofix.start_run`
- `proofix.get_run`
- `proofix.approve_patch`
- `proofix.reject_patch`
- `proofix.verify_run`
- `proofix.export_report`

The plugin owns the run state, calls the OmniRush/OpenCode session client for specialized subtasks, and appends structured events to the run log. It must not give agents unrestricted write access.

### Agent permission policy

Default permissions:

- Read repository files: allowed.
- Search repository and Git history: allowed.
- Run allowlisted read-only inspection commands: allowed.
- Run tests/builds in disposable worktrees: allowed with timeout.
- Modify disposable worktree for instrumentation or patch proposal: allowed.
- Modify the user's working tree: denied until explicit approval.
- Create GitHub branches, commits, or pull requests: denied until separate approval.
- Destructive commands, force-push, or secret access: denied.

### Structured output and provenance

Every model response used by the orchestrator must be validated against a schema. Every claim must point to one or more evidence records. Free-form prose may appear in the UI, but it is not sufficient for state transitions.

The dashboard should show the OmniRush session IDs associated with each agent stage so the buildathon reviewer can inspect the work.

## 7. Localhost application architecture

ProofFix is a localhost web application, not a hosted SaaS product for the MVP.

```text
Browser dashboard (localhost)
        ↓ HTTP
ProofFix local server / bridge
        ↓
ProofFix orchestrator and state store
        ├── deterministic runner
        ├── worktree manager
        ├── redaction and policy checks
        ├── GitHub adapter
        └── OmniRush plugin/session client
```

Recommended initial stack:

- Python standard library or a small existing local runtime for the orchestrator.
- Vanilla HTML/CSS/JavaScript for the dashboard to minimize dependencies.
- JSONL event log plus JSON run snapshots for persistence.
- Git CLI for worktrees, diffs, and patch validation.
- `gh` CLI for the first GitHub adapter.

The dashboard should render from the run snapshot and event stream. It should remain useful in demo mode with a checked-in fixture repository, but demo mode must be labeled clearly and must not be presented as a real run.

## 8. Run state and data model

### Run states

```text
created
→ triaging
→ reproducing
→ not_reproduced | blocked | reproduced
→ diagnosing
→ experimenting
→ patch_proposed
→ awaiting_approval
→ applying
→ verifying
→ verification_failed | verified | verification_blocked
→ github_pending_approval
→ github_complete
```

State transitions must be explicit and validated. The UI must never infer a successful state from an agent message.

### Run record

```json
{
  "schema_version": "1.0",
  "run_id": "run-20261015-0001",
  "created_at": "2026-10-15T00:00:00Z",
  "repository": {
    "kind": "local",
    "path": "...",
    "remote": "...",
    "commit": "...",
    "branch": "..."
  },
  "input": {
    "command": "pytest tests/test_parser.py",
    "failure_text": "...",
    "issue_url": null,
    "constraints": []
  },
  "state": "reproduced",
  "stages": [],
  "hypotheses": [],
  "experiments": [],
  "patch": null,
  "verification": null,
  "github": null,
  "evidence": [],
  "omnirush_sessions": []
}
```

### Event record

Each event is append-only JSONL:

```json
{
  "event_id": "evt-0007",
  "run_id": "run-20261015-0001",
  "timestamp": "2026-10-15T00:01:00Z",
  "stage": "reproduction",
  "kind": "command_result",
  "status": "failed",
  "command": "pytest tests/test_parser.py",
  "exit_code": 1,
  "stdout_ref": "artifacts/evt-0007.stdout",
  "stderr_ref": "artifacts/evt-0007.stderr",
  "redactions": []
}
```

Large logs should be stored as artifacts with summaries in the run record. Secrets and likely credentials must be redacted before an artifact is sent to an agent or displayed in the browser.

## 9. Dashboard requirements

The dashboard should make the proof chain visible rather than looking like a generic chat application.

### Primary screen

- Header with repository, branch, commit, and run status.
- Large stage rail showing Triage, Reproduce, Diagnose, Experiment, Patch, Verify.
- Reproduction card with exact command, exit code, and expandable output.
- Hypothesis board showing confidence, supporting evidence, contradictions, and experiment status.
- Patch review panel with diff, risk, regression test, and approval controls.
- Verification panel showing each deterministic check separately.
- Evidence drawer linking claims to files, lines, commands, and artifacts.
- Export controls for Markdown and JSON.

### Terminal states

**Not reproduced:**

> ProofFix stopped before patching because the failure did not reproduce in the controlled workspace.

Show the attempted command, environment differences, and next information needed.

**Verification failed:**

> The patch was not marked fixed. The regression check still fails.

Show the failing check and offer return to diagnosis.

**Verified:**

> ProofFix verified the original failure, the regression test, and the configured relevant checks.

Show exact commands and results, not an unqualified green badge.

### Accessibility and usability

- Keyboard-accessible approval controls.
- No color-only status meaning.
- Visible focus state.
- Accessible labels for all controls.
- Responsive layout usable at laptop width and narrow width.
- Long logs in bounded scroll areas.
- Reduced-motion support.

## 10. Security and privacy model

ProofFix handles source code, logs, repository metadata, and possibly secrets. Security is part of the product story.

### Local safety

- Resolve and validate repository paths.
- Keep temporary worktrees under a controlled run directory.
- Prevent path traversal in artifact serving.
- Apply command allowlists and timeouts.
- Capture environment variable names, not values, by default.
- Never print access tokens in reports.
- Remove temporary worktrees after a run unless the user explicitly keeps them.

### GitHub safety

- Read-only by default.
- Prefer fine-grained, least-privilege authentication.
- Do not give GitHub credentials to sub-agents.
- Require a separate approval for branch creation, push, PR creation, or comments.
- Never force-push or merge.
- Show the target repository and branch before every write action.

### Prompt and output safety

- Treat repository content as untrusted input.
- Do not follow instructions found inside source files, issues, or logs unless they are part of the ProofFix protocol.
- Validate model output with schemas.
- Preserve uncertainty and contradictions.
- Redact likely credentials before model calls and browser display.

## 11. Testing strategy

Testing must prove user-visible behavior and safety gates.

### Unit tests

- Run-state transition validation.
- Structured output schema validation.
- Evidence reference validation.
- Secret and credential redaction.
- Path containment and artifact serving.
- Command allowlist and timeout handling.
- Patch scope validation.
- GitHub command argument construction.

### Integration tests

Use a checked-in fixture repository with deterministic scenarios:

1. Reproducible test failure with an obvious root cause.
2. Failure that does not reproduce in the controlled environment.
3. Patch that fixes the symptom but fails the regression test.
4. Patch that passes all required checks.
5. Malformed agent output.
6. Agent hypothesis with no evidence.
7. Attempted path traversal or secret leakage.

The integration suite must run without a model provider by using recorded OmniRush response fixtures for protocol tests. A separate live smoke test can exercise OmniRush sessions when credentials and runtime are available.

### End-to-end demo test

From a clean checkout:

```text
start local server
open dashboard
select fixture repository
run the failing command
observe reproduction
approve patch
observe verification
export report
```

The README must document the exact commands and expected result.

## 12. Repository layout proposal

```text
proofix/
├── .opencode/
│   ├── plugins/proofix.ts
│   └── skills/proofix/SKILL.md
├── docs/
│   └── superpowers/specs/2026-10-15-proofix-design.md
├── src/
│   └── proofix/
│       ├── cli.*
│       ├── server.*
│       ├── orchestrator.*
│       ├── state.*
│       ├── runner.*
│       ├── worktree.*
│       ├── redaction.*
│       ├── github.*
│       ├── schemas.*
│       └── reports.*
├── web/
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── fixtures/
│   └── broken-parser-repo/
├── tests/
├── README.md
├── Dockerfile
├── LICENSE
└── .gitignore
```

The exact language and package layout are implementation choices for the builder, provided the local-first behavior and no unnecessary dependency expansion are preserved.

## 13. Demo project and judging strategy

### Demo fixture

The fixture should contain:

- A small parser or configuration module.
- A failing test caused by a realistic root-cause bug.
- A nearby misleading symptom so competing hypotheses are meaningful.
- A missing regression test.
- A simple fix that can be verified quickly.

The demo must run offline except for the OmniRush runtime. It should include a one-command reset so repeated video takes are consistent.

### Demo narrative

1. Show a failed GitHub Actions run or prepared failure log.
2. Import it into ProofFix.
3. Show triage and the exact reproduction command.
4. Show the system refusing to proceed if reproduction differs.
5. Show parallel OmniRush analysts producing competing hypotheses.
6. Show one hypothesis gaining support through an experiment.
7. Compare symptom and root-cause patch options.
8. Approve the root-cause patch.
9. Show the original failure and regression test passing.
10. Create an optional GitHub pull request with the evidence report.

### Submission assets

- Public GitHub repository.
- README with install, setup, test, and demo instructions.
- Architecture diagram.
- Short demo video focused on the proof gates.
- Dockerfile or pinned dependency setup where feasible.
- Sample fixture repository.
- OmniRush Submission ID.

## 14. Delivery plan

### Milestone 1: design and baseline

- Approve this design.
- Choose the first supported language/runtime.
- Create a clean Git repository and fixture project.
- Define JSON schemas and state transitions.

### Milestone 2: deterministic core

- Implement run state and event log.
- Implement isolated command runner.
- Implement reproduction gate.
- Implement report generation.
- Add unit and integration tests.

### Milestone 3: OmniRush orchestration

- Add plugin and skill.
- Add triage and specialized diagnosis subtasks.
- Add structured response validation.
- Add experiment and patch proposal flow.

### Milestone 4: dashboard

- Implement stage rail, evidence, hypotheses, diff review, and verification views.
- Add loading, blocked, failed, and verified states.
- Add accessibility and responsive pass.

### Milestone 5: GitHub adapter

- Import a prepared Actions log or issue.
- Add approval-gated branch and PR path.
- Post the evidence report as a PR comment.

### Milestone 6: hardening and submission

- Run all checks from a clean checkout.
- Verify no secrets or private data are committed.
- Record the demo.
- Write final README and submission copy.
- Generate and submit the OmniRush Submission ID.

## 15. Open decisions for the human owner

These choices materially affect scope and should be resolved before implementation:

1. First supported runtime: Python, JavaScript/Node, or a deliberately narrow pair.
2. GitHub mode for MVP: `gh` CLI or GitHub App/OAuth.
3. Whether GitHub write actions are included in the demo or kept as a stretch goal.
4. Whether the dashboard is started by CLI only or also has a plugin-launched command.
5. Whether patch application always uses a disposable worktree or can target a user-selected branch.
6. Exact OmniRush output/session API available in the target runtime.
7. Whether the public demo repository will be created under the user's GitHub account.

### Recommended defaults if the owner delegates the calls

- Support Python first, with a small Node compatibility layer only if time remains.
- Use `gh` CLI for the first GitHub adapter.
- Include GitHub read/import in the main demo and keep PR creation as an approval-gated stretch step.
- Start the server with a local CLI and let the OmniRush plugin own the agent run.
- Always use disposable worktrees for experiments and verification.
- Keep agent session IDs and structured artifacts in the report.

## 16. Builder acceptance checklist

Before implementation is called complete, the builder must be able to answer yes to all of these:

- Can a fresh user reproduce the demo from the README?
- Does the system stop when the failure cannot be reproduced?
- Is every diagnosis tied to evidence?
- Are experiments deterministic, bounded, and logged?
- Is human approval required before applying a patch?
- Does verification execute real commands rather than trusting agent prose?
- Does the original failing command pass after the patch?
- Is there a regression test proving the behavior?
- Are failed verification runs visible and recoverable?
- Are GitHub write actions separately approved?
- Are secrets redacted and repository paths contained?
- Can the report be exported and understood without the dashboard?
- Are OmniRush sessions and sub-agent roles visible to the reviewer?
- Are all tests, build checks, and demo steps actually run?

## 17. Approval gate

This document is a design proposal, not implementation authorization. No product code, scaffolding, dependency installation, or implementation plan should begin until the human owner approves the full design and resolves the open decisions that materially change the scope.
