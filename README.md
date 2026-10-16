# ProofFix

ProofFix is a local-first, evidence-backed workflow for investigating failing
commands. The MVP runs on localhost and uses Python's standard library; it
does not deploy a hosted service.

## Requirements

- Python 3.12
- GNU Make
- Git (required by the later disposable-worktree workflow)
- Docker, only for the container workflow

The repository pins Python with `.python-version`, declares no third-party
Python dependencies in `pyproject.toml`, and records that empty dependency set
in the portable `requirements.lock` file.

## Setup

From a clean checkout, create local configuration from the checked-in example:

```sh
cp .env.example .env
```

No credentials are needed for the offline baseline. Keep any local secrets in
`.env`; that file is ignored by Git.

## Test

Run the single supported offline test command:

```sh
make test
```

This invokes `unittest discover` with `PYTHONPATH=src` and requires no network
access or globally installed package.

## Offline demo and dashboard

The local server entry point is started with:

```sh
make run
```

The fixture-backed demo starts the same localhost server with recorded
OmniRush responses. `make demo` starts the server only; it does not create a
run and the dashboard does not contain an intake form. Create and advance a
run through the localhost API, then load that existing run in the dashboard
with `?run=RUN_ID`:

```sh
make demo
```

To restore the intentionally failing fixture after an experiment, run:

```sh
make fixture-reset
```

For the complete reproduction path, the fixture needs to be a standalone Git
worktree. The checked-in fixture is intentionally kept inside this project
checkout, so make a disposable copy before starting the server. The following
commands start the server, create a recorded run, and advance it through
reproduction and diagnosis:

```sh
DEMO_FIXTURE=$(mktemp -d)
cp -R fixtures/broken-parser-repo/. "$DEMO_FIXTURE"/
git -C "$DEMO_FIXTURE" init -q
git -C "$DEMO_FIXTURE" config user.email proofix-demo@example.test
git -C "$DEMO_FIXTURE" config user.name "ProofFix Demo"
git -C "$DEMO_FIXTURE" add .
git -C "$DEMO_FIXTURE" commit -qm "fixture baseline"
PYTHONPATH=src python -m proofix.cli run \
  --repository "$DEMO_FIXTURE" \
  --recorded-fixture fixtures/omnirush/reproducible-run.json \
  --data-root .proofix-data
```

In another terminal, create the run and keep its ID:

```sh
RUN_JSON=$(curl -fsS -X POST http://127.0.0.1:8000/api/runs \
  -H 'Content-Type: application/json' \
  -d "{\"repository_path\":\"$DEMO_FIXTURE\",\"command\":\"python -m unittest discover -s tests -v\",\"failure_text\":\"ValueError: too many values to unpack (expected 2)\"}")
RUN_ID=$(printf '%s' "$RUN_JSON" | python -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
curl -fsS -X POST "http://127.0.0.1:8000/api/runs/$RUN_ID/reproduce" \
  -H 'Content-Type: application/json' -d '{}'
curl -fsS -X POST "http://127.0.0.1:8000/api/runs/$RUN_ID/diagnose" \
  -H 'Content-Type: application/json' -d '{}'
```

Open `http://127.0.0.1:8000/?run=$RUN_ID` to inspect the authoritative
snapshot and events. Submit the proposed unified diff to
`POST /api/runs/$RUN_ID/propose` with `{"diff":"...","scope":["parser.py"]}`.
Read the returned `patch.digest`, then approve through the dashboard's
**Approve patch** control (or `POST /api/runs/$RUN_ID/approve` with
`actor`, canonical UTC `timestamp`, and `patch_digest`). After approval, submit
verification through `POST /api/runs/$RUN_ID/verify` with
`original_failure_command`, `regression_test`, and optional test/scope fields;
then use the dashboard's **Refresh** control to display the recorded checks.
Finally use **Export .md** and **Export .json**
or `POST /api/runs/$RUN_ID/export` with `{"format":"markdown"}` and
`{"format":"json"}`. The server runs commands and verification in disposable
workspaces; the source working tree must remain unchanged. Remove
`$DEMO_FIXTURE` after exporting reports.

Both commands run through the source checkout (`PYTHONPATH=src`) rather than
requiring a global ProofFix installation. The server binds to localhost by
default; it is not an internet-facing deployment.

To export a persisted run through the same report layer, use the CLI without
installing ProofFix globally:

```sh
PYTHONPATH=src python -m proofix.cli export RUN_ID exports/report.json --format json --data-root .proofix
```

The equivalent Make target is `make export RUN_ID=run-... OUTPUT=exports/report.md
FORMAT=markdown`. The HTTP API exposes JSON snapshots and events under
`/api/runs/RUN_ID`, artifact reads under `/api/runs/RUN_ID/artifacts/REF`, and
Markdown/JSON reports under `/api/runs/RUN_ID/report?format=markdown|json`.

## Optional GitHub reads

GitHub import is read-first and optional. Install and authenticate the GitHub
CLI only when a user explicitly requests repository metadata, issue text, a
pull-request diff, Actions status, or Actions logs:

```sh
gh auth login
```

Missing `gh`, authentication, or network access is reported as a blocked read
with next steps. GitHub tokens are never passed in command arguments or stored
in ProofFix records. Branch creation, commits, PR creation, and other GitHub
writes are approval-gated stretch functionality and are not needed for the
offline demo.

## Docker

Build the reproducible image (the build installs Git and Make, then runs the
same offline test command):

```sh
docker build -t proofix .
```

Run the local server from the image:

```sh
docker run --rm --env-file .env -p 127.0.0.1:8000:8000 proofix
```

The container workflow is also localhost-only. Docker does not turn the MVP
into a hosted or production deployment.

## Deployment limitations

The MVP is intentionally a local development tool. It stores run data under
the configured local data root, has no hosted control plane or multi-user
authentication, and must not be exposed publicly. Production deployment,
automatic merging, unattended GitHub writes, and PR creation without an
explicit approval are outside the MVP scope. A clean-checkout demo is the
supported deployment verification; the app has no hosted control plane.
