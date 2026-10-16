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

The fixture-backed demo starts the same localhost server with the checked-in
broken-parser repository and recorded OmniRush responses. It is clearly
labeled `recorded/demo`; every client intake override is ignored, including
the repository path, command, failure text, constraints, GitHub references,
branch/commit metadata, and session configuration:

```sh
make demo
```

To restore the intentionally failing fixture after an experiment, run:

```sh
make fixture-reset
```

Then open `http://127.0.0.1:8000/` in a browser. Select the supplied
`broken-parser-repo` fixture in the dashboard, start the recorded run, run the
failing command, and observe the reproduction evidence. Review the root-cause
patch, explicitly approve it with the displayed digest, observe verification,
and export both Markdown and JSON reports from the dashboard. The server runs
the command and verification in disposable workspaces; the source working tree
must remain unchanged.

For the complete reproduction path, the fixture needs to be a standalone Git
worktree. The checked-in fixture is intentionally kept inside this project
checkout, so make a disposable copy before starting the server:

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

Use the dashboard/API flow above against this server, then remove the
temporary directory after exporting the reports.

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
