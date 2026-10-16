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
OmniRush responses. On startup it automatically materializes
`fixtures/broken-parser-repo` as a standalone committed repository under the
configured run-data directory, so no manual Git workaround is needed. Create
and advance a run through the localhost API, then load that run in the
dashboard with `?run=RUN_ID`:

```sh
make demo
```

To restore the intentionally failing fixture after an experiment, run:

```sh
make fixture-reset
```

For the complete reproduction path, use the server-owned demo fixture and
its guarded API vocabulary. In another terminal, create the run and keep its
ID:

```sh
RUN_JSON=$(curl -fsS -X POST http://127.0.0.1:8000/api/runs \
  -H 'Content-Type: application/json' -d '{}')
RUN_ID=$(printf '%s' "$RUN_JSON" | python -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
curl -fsS -X POST "http://127.0.0.1:8000/api/runs/$RUN_ID/reproduce" \
  -H 'Content-Type: application/json' -d '{}'
curl -fsS -X POST "http://127.0.0.1:8000/api/runs/$RUN_ID/diagnose" \
  -H 'Content-Type: application/json' -d '{}'
```

Open `http://127.0.0.1:8000/?run=$RUN_ID` to inspect the authoritative
snapshot and events. Run a focused guarded experiment through
`POST /api/runs/$RUN_ID/experiment`, then propose a unified diff with an
**explicit regression command** through
`POST /api/runs/$RUN_ID/propose`:
`{"diff":"...","regression_test":"python -m unittest tests.test_parser","scope":["parser.py"]}`. The regression command must be non-empty and
different from the original failure command. Read the returned `patch.digest`,
then approve through the dashboard's **Approve patch** control (or
`POST /api/runs/$RUN_ID/approve` with `actor`, canonical UTC `timestamp`, and
`patch_digest`). After approval, submit verification through
`POST /api/runs/$RUN_ID/verify` with the original command and the same explicit
regression command. A failed or blocked verification can be sent back with
`POST /api/runs/$RUN_ID/return-to-diagnosis` and an audit actor/timestamp.
Finally use **Export .md** and **Export .json** or the export endpoint. The
server runs commands and verification in disposable workspaces; the source
fixture remains unchanged.

Both commands run through the source checkout (`PYTHONPATH=src`) rather than
requiring a global ProofFix installation. The server binds to localhost by
default; it is not an internet-facing deployment.

To export a persisted run through the same report layer, use the CLI without
installing ProofFix globally:

```sh
PYTHONPATH=src python -m proofix.cli export RUN_ID exports/report.json --format json --data-root .proofix-data
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
