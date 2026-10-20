# ProofFix

ProofFix is an evidence-backed workflow for investigating a failing command.
It records the immutable intake, reproduction result, competing diagnosis,
guarded experiments, patch approval, and deterministic verification as one
auditable proof chain. The repository is standard-library Python 3.12 with a
small vanilla JavaScript dashboard.

## Why the proof gates matter

Failure text and model suggestions are untrusted evidence. ProofFix keeps the
important boundaries server-owned:

- **Reproduce** records whether the exact failure happened in a disposable
  workspace; a successful or different failure stops the chain.
- **Diagnose** records competing specialist hypotheses and their evidence.
- **Experiment** accepts only the existing guarded vocabulary and command
  allowlist, rather than arbitrary agent-supplied programs.
- **Patch** requires a bounded unified diff and an explicit, distinct
  regression command.
- **Approve** requires an actor, canonical UTC timestamp, and the persisted
  patch digest.
- **Verify** checks the approved patch in a fresh workspace and distinguishes
  verified, failed, and blocked outcomes.

The dashboard renders persisted snapshots and events; it does not invent
workflow state in the browser.

> **Hosted demo:** [Open the restricted ProofFix demo](https://proofix-demo.onrender.com/)
> in recorded/demo mode using the checked-in fixture.

## Two-minute hosted demo

1. Open the hosted demo link above.
2. Click **Start the fixed demo** in the current-state panel. No run ID or repository path is required.
3. Review the fixed fixture's recorded reproduction and OmniRush diagnosis.
4. Advance the visible guarded stages as desired. The dashboard enables only
   actions allowed by the server snapshot.
5. Use **Export .md** or **Export .json** to inspect the evidence report.
6. Check `/api/demo` for the deployment's restricted demo status and `/health`
   for health.

The hosted demo is deliberately restricted: it materializes only the checked-in
`fixtures/broken-parser-repo`, uses checked-in recorded OmniRush responses,
rejects repository and command intake overrides, and accepts no GitHub
credentials. Experiments still pass through the existing guarded vocabulary;
the service is not a general remote shell.

## Get the source

The simplest setup is to clone the repository with Git:

```sh
git clone https://github.com/Auritras/Omnirush-Hackathon.git proofix
cd proofix
```

You can also use GitHub's **Code → Download ZIP** option and extract the
archive. A Git checkout is recommended for the full local workflow because
ProofFix creates disposable Git worktree snapshots, and every target
repository supplied to local mode must itself be a Git repository.

## Full local mode

### Requirements and setup

- Python 3.12 for ProofFix (pinned by `.python-version`); install the runtime
  required by each target repository too
- GNU Make on Linux/macOS; Windows PowerShell can use the direct Python
  commands below
- Git, for disposable worktree snapshots and target-repository commits
- Docker, only for the container workflow

The project has no third-party Python dependencies. `requirements.lock` records
that empty dependency set. Create local configuration if useful:

```sh
cp .env.example .env
```

No credentials are needed for the offline baseline. `.env` is ignored by Git.
`PROOFIX_DATA_ROOT` defaults to `.proofix-data`; repository intake is limited to
the path-separated `PROOFIX_ALLOWED_ROOTS` list, which defaults to that data
root.

Start the normal loopback-only server:

```sh
make run
```

Open `http://127.0.0.1:8000/`. Local intake can use the API or the existing
run workflow. The equivalent CLI commands run directly from the checkout with
`PYTHONPATH=src` and do not require a global ProofFix install.

This is a localhost-only server in normal mode; it is intentionally not an
internet-facing deployment.

### Windows PowerShell without GNU Make

GNU Make is convenient on Linux and macOS, but it is not included with a
standard Windows PowerShell installation. Run the same full local server
directly with Python instead:

```powershell
cd C:\path\to\proofix
$env:PYTHONPATH = "src"
python -m proofix.cli run `
  --host 127.0.0.1 `
  --port 8000 `
  --data-root .proofix-data `
  --allowed-root "C:\path\to\repositories"
```

If `python` is not on PATH, use the Windows Python launcher:

```powershell
$env:PYTHONPATH = "src"
py -3.12 -m proofix.cli run `
  --host 127.0.0.1 `
  --port 8000 `
  --data-root .proofix-data `
  --allowed-root "C:\path\to\repositories"
```

Open `http://127.0.0.1:8000/` after the server prints its listening address.
Replace the example paths with real paths on your computer. The
`--allowed-root` value must be a parent directory containing the local
repositories you want ProofFix to inspect. To create a run against one of
those repositories, open a second PowerShell window and use the API:

```powershell
$body = @{
  repository_path = "C:\path\to\repositories\your-repo"
  command = "python -m unittest discover -s tests -v"
  failure_text = "AssertionError: expected value differs"
} | ConvertTo-Json

$run = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/runs" `
  -ContentType "application/json" `
  -Body $body

Start-Process "http://127.0.0.1:8000/?run=$($run.run_id)"
```

### Use your own broken repository

Normal local mode is not limited to the checked-in fixture. Before creating a
run, prepare the target repository:

1. Make sure it is a Git repository and that the broken state is committed.
   ProofFix's default clean snapshot runs the committed `HEAD`, not arbitrary
   uncommitted edits.
2. Make sure the repository is below the `--allowed-root` passed to the server.
3. Confirm the failure locally with the same command you will submit.
4. Submit a concise, stable failure signature. A one-line signature such as
   `AssertionError: 4 != 5` is more portable than copying a complete
   OS-specific traceback.

For example, on Windows:

```powershell
$repo = "C:\path\to\repositories\my-broken-app"
Set-Location $repo

git init                 # only if this is not already a Git repository
git add .
git commit -m "Record broken baseline"

python -m unittest discover -s tests -v
```

Then create the run from a second PowerShell window:

```powershell
$body = @{
  repository_path = "C:\path\to\repositories\my-broken-app"
  command = "python -m unittest discover -s tests -v"
  failure_text = "AssertionError: 4 != 5"
} | ConvertTo-Json -Depth 5

$run = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/runs" `
  -ContentType "application/json" `
  -Body $body

$runId = [string]$run.run_id
Start-Process "http://127.0.0.1:8000/?run=$runId"
```

The dashboard then gates the workflow in order: reproduce the failure, run
diagnosis and evidence checks, review a unified diff, approve it with an actor,
verify it in a fresh workspace, and export the Markdown or JSON report. The
source repository is not modified by reproduction or verification.

Commands must use the guarded command vocabulary and begin with an allowlisted
executable such as `python`, `pytest`, `node`, `npm`, `go`, `cargo`, `dotnet`,
or another configured tool. Shell pipelines, redirection, shell/eval forms,
unsafe paths, and arbitrary unapproved executables are rejected.

The offline fixture demo is also loopback-only by default:

```sh
make demo
```

On Windows without GNU Make, run the equivalent demo command from the ProofFix
checkout:

```powershell
$env:PYTHONPATH = "src"
py -3.12 -m proofix.cli demo `
  --host 127.0.0.1 `
  --port 8000 `
  --data-root .proofix-data `
  --allowed-root ".proofix-data"
```

It uses the checked-in fixture and recorded OmniRush responses. The server
materializes a standalone committed copy under `.proofix-data/demo-fixture`.
To restore the intentionally failing source fixture after an experiment:

```sh
make fixture-reset
```

### Public-demo bind rule

Normal mode always rejects non-loopback hosts. A non-loopback bind is accepted
only when both demo mode and the explicit public-demo flag are enabled:

```sh
PROOFIX_DEMO=true \
PROOFIX_PUBLIC_DEMO=true \
PROOFIX_HOST=0.0.0.0 \
PROOFIX_DATA_ROOT=.proofix-data \
make run
```

`--public-demo` is the equivalent CLI flag. Do not use this configuration for
an unrestricted deployment. The checked-in Render Blueprint is the restricted
public-demo configuration.

## Feature coverage

- Immutable, validated, redacted run intake and durable JSONL snapshots/events
- Clean disposable Git worktree snapshots with source-repository preservation
- Reproduction comparison with bounded stdout/stderr artifacts
- Recorded specialist hypotheses and session provenance
- Guarded named test/build, environment, instrumentation, parent-commit, and
  runtime-inspection experiments
- Unified-diff scope validation, digest-bound approval, and deterministic
  verification
- Markdown and JSON evidence reports
- Safe static dashboard rendering with keyboard focus and reduced-motion hooks
- Optional read-only GitHub metadata integration
- Offline fixture and recorded OmniRush demo mode

## Offline, recorded, and live OmniRush

- **Offline**: local tests and the fixture workflow use no network and no model
  provider.
- **Recorded**: `fixtures/omnirush/reproducible-run.json` is explicitly labeled
  `recorded/demo`; the hosted and `make demo` paths use it.
- **Live**: a host runtime may provide an `OmniRushSessionClient` transport for
  local integrations. The repository does not embed provider SDKs or API keys.

The provenance label is persisted in the run/report so recorded evidence is not
presented as a live model result. The standard offline checkout does not include
a live session client or provider credentials: it can reproduce local failures,
but live diagnosis and model-backed patch proposals require the host runtime to
inject that transport. The fixed demo remains available without that setup
because it uses the explicitly recorded specialist response.

## Optional GitHub read mode

GitHub access is optional and read-first. If a local operator explicitly needs
repository metadata, issue text, a pull-request diff, Actions status, or Actions
logs, authenticate the GitHub CLI outside ProofFix:

```sh
gh auth login
```

Missing CLI, authentication, or network access becomes a bounded blocked read.
ProofFix never accepts GitHub credentials in hosted-demo intake, never places
tokens in command arguments or run records, and exposes no default GitHub write
operation. Branch, commit, PR, and other writes are outside this MVP.

## Safety and privacy

Repository paths must resolve beneath configured allowed roots. Commands run in
disposable workspaces through the runner policy; shell syntax, eval forms,
unapproved executables, unsafe targets, path traversal, oversized requests, and
unsafe artifact references are rejected. Logs, environment metadata, GitHub
references, and model text are redacted and bounded before persistence.

The public demo accepts no credentials, does not accept arbitrary repositories,
does not accept command overrides for its fixed run, and does not perform GitHub
writes. Do not place secrets in `.env.example`, Render configuration, fixtures,
or committed reports.

## Testing and reproducibility

The single supported offline test command is:

```sh
make test
```

On Windows without GNU Make, run the same test suite directly:

```powershell
$env:PYTHONPATH = "src"
py -3.12 -m unittest discover -s tests -v
```

Additional checks used for a clean submission are:

```sh
python -m compileall -q src tests
node --check web/app.js
git diff --check
```

The runtime and dependency inputs are pinned by `.python-version` and
`requirements.lock`. No global Python package installation is required.

## Docker

The Dockerfile installs the system packages needed by the app, copies the
repository, and runs `make test` during the image build:

```sh
docker build -t proofix .
docker run --rm --env-file .env -p 127.0.0.1:8000:8000 proofix
```

The default container is loopback-only. Render supplies its `PORT` value; the
CLI and Makefile retain `8000` as the local default. The image command uses
`make run`, and Render's `PROOFIX_DEMO=true` environment selects the fixed demo
path without changing local defaults.

## Hosted deployment

`render.yaml` defines one Docker web service named `proofix-demo`, uses the
existing Dockerfile, checks `/health`, binds Render's injected `PORT`, and
stores the demo data root at `.proofix-data`. It sets only public-demo flags and
contains no GitHub credentials or secrets. Deploy it from the repository in
Render's Blueprint flow; the hosted demo link above points to the deployed
service.

Hosted-demo limitations are intentional: the service has no user
authentication, no multi-user control plane, no arbitrary repository intake,
no credential intake, no GitHub writes, recorded rather than live OmniRush
responses, and deployment-local data storage. Public access is suitable for a
restricted judge walkthrough only; it is not a production ProofFix service.

## Useful API examples

Create and inspect a normal local run:

```sh
RUN_JSON=$(curl -fsS -X POST http://127.0.0.1:8000/api/runs \
  -H 'Content-Type: application/json' \
  -d '{"repository_path":".","command":"false","failure_text":"expected failure"}')
RUN_ID=$(printf '%s' "$RUN_JSON" | python -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
curl -fsS "http://127.0.0.1:8000/api/runs/$RUN_ID"
```

The demo uses the fixed action instead:

```sh
curl -fsS http://127.0.0.1:8000/api/demo
RUN_JSON=$(curl -fsS -X POST http://127.0.0.1:8000/api/demo/start \
  -H 'Content-Type: application/json' -d '{}')
```

The normal lifecycle endpoints remain available for local runs:
`/reproduce`, `/diagnose`, `/experiment`, `/propose`, `/approve`, `/verify`,
`/return-to-diagnosis`, `/events`, `/report`, and `/export`.

For a persisted report export, use:

```sh
PYTHONPATH=src python -m proofix.cli export RUN_ID exports/report.json \
  --format json --data-root .proofix-data
```

For submission verification, use a clean checkout and repeat the hosted-demo
flow without relying on local run data from an earlier session.
