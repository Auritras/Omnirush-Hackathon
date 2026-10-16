"""Command-line lifecycle for the local ProofFix server and reports."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .reports import write_report
from .server import ServerConfig, create_server


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "export":
        source = args.run_id
        candidate = Path(args.data_root) / "runs" / args.run_id
        if candidate.exists():
            source = candidate
        output = write_report(source, args.destination, args.format)
        print(output)
        return 0

    config = ServerConfig(
        data_root=Path(args.data_root),
        host=args.host,
        port=args.port,
        demo=args.command == "demo",
        repository=Path(args.repository) if args.repository else None,
        recorded_fixture=Path(args.recorded_fixture) if args.recorded_fixture else None,
    )
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
    export.add_argument("--data-root", default=".proofix")
    return parser


def _server_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-root", default=".proofix")
    parser.add_argument("--repository")
    parser.add_argument("--recorded-fixture")


if __name__ == "__main__":
    raise SystemExit(main())
