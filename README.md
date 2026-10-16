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

## Run and demo

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
automatic merging, and unattended GitHub writes are outside the MVP scope.
