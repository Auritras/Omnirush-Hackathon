"""Command-line lifecycle for the local ProofFix server and reports."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re

from .paths import ensure_contained, resolve_repository
from .reports import write_report
from .server import ServerConfig, create_server
from .store import RunStore


_RUN_ID = re.compile(r"^run-[A-Za-z0-9][A-Za-z0-9._-]*$")


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "export":
        data_root = Path(args.data_root).expanduser().resolve(strict=False)
        if not _RUN_ID.fullmatch(args.run_id):
            raise ValueError("run_id must be an opaque local run ID")
        store = RunStore(data_root)
        run = store.get_run(args.run_id)
        events = store.list_events(args.run_id)
        destination = Path(args.destination).expanduser()
        if not destination.is_absolute():
            destination = data_root / destination
        destination = ensure_contained(destination, data_root)
        output = write_report(run, destination, args.format, events=events)
        print(output)
        return 0

    config = ServerConfig(
        data_root=Path(args.data_root),
        host=args.host,
        port=args.port,
        demo=args.command == "demo" or _env_flag("PROOFIX_DEMO"),
        public_demo=args.public_demo or _env_flag("PROOFIX_PUBLIC_DEMO"),
        repository=Path(args.repository) if args.repository else None,
        recorded_fixture=Path(args.recorded_fixture) if args.recorded_fixture else None,
        allowed_roots=_allowed_roots(args),
    )
    if args.repository:
        roots = config.allowed_roots if config.allowed_roots is not None else config.data_root
        resolve_repository(args.repository, roots)
    server = create_server(config)
    try:
        address = getattr(server, "server_address", (args.host, args.port))
        print(f"ProofFix listening on http://{address[0]}:{address[1]}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proofix")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "demo"):
        command = subparsers.add_parser(name)
        _server_options(command)
    export = subparsers.add_parser("export")
    export.add_argument("run_id")
    export.add_argument("destination")
    export.add_argument("--format", choices=("markdown", "md", "json"), default="markdown")
    export.add_argument("--data-root", default=os.environ.get("PROOFIX_DATA_ROOT", ".proofix-data"))
    return parser


def _server_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=os.environ.get("PROOFIX_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", os.environ.get("PROOFIX_PORT", "8000"))))
    parser.add_argument("--data-root", default=os.environ.get("PROOFIX_DATA_ROOT", ".proofix-data"))
    parser.add_argument("--repository")
    parser.add_argument("--recorded-fixture")
    parser.add_argument("--allowed-root", action="append", dest="allowed_root")
    parser.add_argument("--public-demo", action="store_true", help="allow a demo server to bind beyond loopback")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().casefold() in {"1", "true", "yes", "on"}


def _allowed_roots(args):
    values = args.allowed_root
    if values is None:
        configured = os.environ.get("PROOFIX_ALLOWED_ROOTS", "")
        values = [item for item in configured.split(os.pathsep) if item] or None
    if values is None:
        return None
    expanded = [item for value in values for item in value.split(os.pathsep) if item]
    return tuple(Path(item).expanduser().resolve(strict=False) for item in expanded) or None


if __name__ == "__main__":
    raise SystemExit(main())
