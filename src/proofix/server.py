"""Bounded HTTP API for the ProofFix orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
import math
import shutil
import subprocess
import ipaddress
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

from .experiments import validate_experiment_timeout
from .intake import sanitize_intake
from .models import DomainError
from .omnirush import OmniRushSessionClient, RecordedSessionClient
from .orchestrator import ProofFixOrchestrator
from .paths import PathSafetyError, ensure_contained, resolve_repository, safe_artifact_path
from .reports import render_json, render_markdown, write_report
from .redaction import redact_text
from .runner import run_command
from .store import RunStore
from .verification import VerificationPlan
from .worktree import WorkspaceManager


MAX_REQUEST_BODY = 1_048_576
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_VERIFICATION_TIMEOUT_SECONDS = 300.0
WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
STATIC_ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "fonts/ibm-plex-sans-400.woff2": "font/woff2",
    "fonts/ibm-plex-sans-600.woff2": "font/woff2",
    "fonts/ibm-plex-sans-700.woff2": "font/woff2",
    "fonts/ibm-plex-mono-400.woff2": "font/woff2",
    "fonts/OFL-IBM-Plex-Sans.txt": "text/plain; charset=utf-8",
    "fonts/OFL-IBM-Plex-Mono.txt": "text/plain; charset=utf-8",
}


@dataclass
class ServerConfig:
    """Local server wiring and safety limits.

    The mapping form is also accepted by :func:`create_server` for small host
    integrations that do not want to import this dataclass.
    """

    data_root: Path | str = Path(".proofix-data")
    host: str = "127.0.0.1"
    port: int = 8000
    max_body_bytes: int = MAX_REQUEST_BODY
    max_artifact_bytes: int = MAX_ARTIFACT_BYTES
    repository: Path | str | None = None
    allowed_roots: tuple[Path | str, ...] | list[Path | str] | Path | str | None = None
    recorded_fixture: Path | str | None = None
    session_client: OmniRushSessionClient | Any | None = None
    runner: Any = run_command
    demo: bool = False
    public_demo: bool = False

    def normalized(self) -> "ServerConfig":
        root = Path(self.data_root).expanduser().resolve(strict=False)
        demo = _enabled_flag(self.demo)
        public_demo = _enabled_flag(self.public_demo)
        if not _is_loopback(self.host) and not (demo and public_demo):
            raise ValueError("non-loopback binding requires demo mode and the explicit public-demo flag")
        if not isinstance(self.port, int) or not 0 <= self.port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        if self.max_body_bytes <= 0 or self.max_artifact_bytes <= 0:
            raise ValueError("request and artifact limits must be positive")
        raw_roots = self.allowed_roots
        if raw_roots is None:
            roots = (root,)
        elif isinstance(raw_roots, (str, bytes, Path)):
            roots = (Path(raw_roots).expanduser().resolve(strict=False),)
        else:
            roots = tuple(Path(value).expanduser().resolve(strict=False) for value in raw_roots)
        if not roots:
            raise ValueError("at least one allowed repository root is required")
        if demo and root not in roots:
            roots = (*roots, root)
        if self.repository is not None and not demo:
            resolve_repository(self.repository, roots)
        return ServerConfig(
            data_root=root,
            host=self.host,
            port=self.port,
            max_body_bytes=self.max_body_bytes,
            max_artifact_bytes=self.max_artifact_bytes,
            repository=_default_fixture_repository() if demo else self.repository,
            allowed_roots=roots,
            recorded_fixture=_default_recorded_fixture() if demo else self.recorded_fixture,
            session_client=None if demo else self.session_client,
            runner=self.runner,
            demo=demo,
            public_demo=public_demo,
        )


class ProofFixHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], config: ServerConfig):
        self.config = config
        self.store = RunStore(config.data_root)
        self.workspace_manager = WorkspaceManager(config.data_root / "workspace-root", config.allowed_roots)
        self.demo_repository = _materialize_demo_fixture(config.data_root) if config.demo else None
        if config.demo:
            session_client = RecordedSessionClient(config.recorded_fixture or _default_recorded_fixture())
        elif config.session_client is not None:
            session_client = config.session_client
        else:
            fixture = config.recorded_fixture
            session_client = RecordedSessionClient(fixture) if fixture is not None else RecordedSessionClient()
        metadata = {"mode": "recorded/demo", "provenance": "recorded/demo"} if config.demo else None
        self.orchestrator = ProofFixOrchestrator(self.store, self.workspace_manager, config.runner, session_client, metadata, config.allowed_roots)
        super().__init__(address, _make_handler())


    def shutdown(self):
        try:
            self.orchestrator.cleanup()
        finally:
            return super().shutdown()

    def server_close(self):
        try:
            self.orchestrator.cleanup()
        finally:
            return super().server_close()


def create_server(config: ServerConfig | Mapping[str, Any] | None = None) -> ThreadingHTTPServer:
    """Construct a configured, localhost-only threaded HTTP server."""

    if config is None:
        normalized = ServerConfig().normalized()
    elif isinstance(config, ServerConfig):
        normalized = config.normalized()
    elif isinstance(config, Mapping):
        normalized = ServerConfig(**dict(config)).normalized()
    elif hasattr(config, "__dict__"):
        normalized = ServerConfig(**vars(config)).normalized()
    else:
        raise TypeError("config must be ServerConfig, mapping, or None")
    return ProofFixHTTPServer((normalized.host, normalized.port), normalized)


def _make_handler():
    class Handler(BaseHTTPRequestHandler):
        server: ProofFixHTTPServer
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802 - stdlib handler API
            self._handle("GET")

        def do_POST(self):  # noqa: N802 - stdlib handler API
            self._handle("POST")

        def do_PUT(self):  # noqa: N802 - stdlib handler API
            self._method_not_allowed()

        def do_DELETE(self):  # noqa: N802 - stdlib handler API
            self._method_not_allowed()

        def do_PATCH(self):  # noqa: N802 - stdlib handler API
            self._method_not_allowed()

        def do_HEAD(self):  # noqa: N802 - stdlib handler API
            self._method_not_allowed()

        def do_OPTIONS(self):  # noqa: N802 - stdlib handler API
            self._method_not_allowed()

        def do_CONNECT(self):  # noqa: N802 - stdlib handler API
            self._method_not_allowed()

        def do_TRACE(self):  # noqa: N802 - stdlib handler API
            self._method_not_allowed()

        def send_error(self, code, message=None, explain=None):  # noqa: N802 - stdlib handler API
            self._send_error(code, message or "HTTP request is not supported")

        def log_message(self, _format, *args):
            return

        def handle(self):
            try:
                super().handle()
            except ConnectionResetError:
                return

        def _handle(self, method: str):
            try:
                parsed = urlsplit(self.path)
                route = [unquote(item) for item in parsed.path.split("/") if item]
                if any(item in {".", ".."} or "/" in item or "\\" in item for item in route):
                    raise _HTTPError(400, "unsafe path")
                api_route = bool(route and (route[0] == "api" or route == ["health"]))
                if route and route[0] == "api":
                    route.pop(0)
                if method == "GET":
                    if api_route:
                        status, content_type, body = self._get(route, parse_qs(parsed.query))
                    else:
                        status, content_type, body = self._static(route)
                else:
                    if not api_route:
                        raise _HTTPError(404, "not found")
                    payload = self._read_json()
                    status, content_type, body = self._post(route, payload)
                self._send(status, content_type, body)
            except _HTTPError as exc:
                self._send_error(exc.status, exc.message)
            except DomainError as exc:
                message = str(exc)
                status = 404 if message.startswith("unknown run") else 400 if message.startswith(("invalid regression", "an explicit")) else 409
                self._send_error(status, message)
            except (PathSafetyError, ValueError, TypeError, KeyError) as exc:
                self._send_error(400, _safe_error(exc))
            except OSError as exc:
                self._send_error(404, _safe_error(exc))

        def _get(self, route: list[str], query: Mapping[str, list[str]]):
            if not route or route == ["health"]:
                return 200, "application/json; charset=utf-8", {"ok": True, "service": "proofix"}
            if route == ["demo"]:
                return 200, "application/json; charset=utf-8", {
                    "enabled": self.server.config.demo,
                    "public_demo": self.server.config.public_demo,
                    "mode": "recorded/demo" if self.server.config.demo else "local",
                    "fixture": "checked-in/broken-parser-repo" if self.server.config.demo else None,
                    "omnirush": "recorded" if self.server.config.demo else "configured at runtime",
                    "repository_intake": "fixed fixture only" if self.server.config.demo else "allowed roots",
                    "commands": "existing guarded experiment vocabulary",
                }
            if route[0] != "runs" or len(route) < 2:
                raise _HTTPError(404, "not found")
            run_id = route[1]
            run = self.server.store.get_run(run_id)
            if len(route) == 2:
                return 200, "application/json; charset=utf-8", run.to_dict()
            if route[2] == "events" and len(route) == 3:
                return 200, "application/json; charset=utf-8", {
                    "run_id": run_id,
                    "events": [event.to_dict() for event in self.server.store.list_events(run_id)],
                }
            if route[2] in {"report", "report.json", "report.md", "reports"}:
                report_format = _report_format(route[2], query)
                if route[2] == "reports" and len(route) >= 4:
                    report_format = _report_format(f"report.{route[3]}", query)
                events = self.server.store.list_events(run_id)
                if report_format == "json":
                    return 200, "application/json; charset=utf-8", render_json(run, events)
                return 200, "text/markdown; charset=utf-8", render_markdown(run, events, {})
            if route[2] == "artifacts" and len(route) >= 4:
                return self._artifact(run_id, "/".join(route[3:]))
            raise _HTTPError(404, "not found")

        def _static(self, route: list[str]):
            """Serve the small, allow-listed vanilla frontend from ``web/`` only."""

            asset = "index.html" if not route else "/".join(route)
            content_type = STATIC_ASSETS.get(asset)
            if content_type is None:
                raise _HTTPError(404, "not found")
            root = WEB_ROOT.resolve(strict=False)
            selected = (root / asset).resolve(strict=False)
            if not ensure_contained(selected, root) or not selected.is_file():
                raise _HTTPError(404, "not found")
            return 200, content_type, selected.read_bytes()

        def _artifact(self, run_id: str, reference: str):
            run = self.server.store.get_run(run_id)
            run_root = self.server.config.data_root / "runs" / run_id
            candidates = [safe_artifact_path(run_root, reference)]
            workspace_root = self.server.config.data_root / "workspace-root"
            snapshot = getattr(self.server.orchestrator, "_snapshots", {}).get(run_id)
            if snapshot is not None:
                candidates.append(safe_artifact_path(snapshot.path, reference))
            for evidence in run.evidence:
                if not isinstance(evidence, Mapping) or evidence.get("kind") != "repository_snapshot":
                    continue
                details = evidence.get("details")
                if not isinstance(details, Mapping) or not isinstance(details.get("path"), str):
                    continue
                candidates.append(safe_artifact_path(workspace_root, f"{details['path']}/{reference}"))
            selected = next((path for path in candidates if path.is_file() and ensure_contained(path, self.server.config.data_root)), None)
            if selected is None:
                raise _HTTPError(404, "artifact not found")
            if selected.stat().st_size > self.server.config.max_artifact_bytes:
                raise _HTTPError(413, "artifact exceeds the configured size limit")
            body = selected.read_bytes()
            return 200, _artifact_content_type(selected), body

        def _post(self, route: list[str], payload: Any):
            if route == ["demo", "start"]:
                if not self.server.config.demo:
                    raise _HTTPError(404, "public demo is not enabled")
                _reject_demo_overrides(payload)
                return self._start_demo_run()
            if route == ["runs"] or route == ["intake"]:
                if self.server.config.demo:
                    _reject_demo_overrides(payload)
                    return self._start_demo_run()
                else:
                    data = dict(payload)
                run_id = self.server.orchestrator.start_run(sanitize_intake(data, self.server.config.allowed_roots))
                return 201, "application/json; charset=utf-8", self.server.store.get_run(run_id).to_dict()
            if len(route) < 3 or route[0] != "runs":
                raise _HTTPError(404, "not found")
            run_id = route[1]
            action = route[2].casefold().replace("_", "-")
            if action == "actions" and len(route) >= 4:
                action = route[3].casefold().replace("_", "-")
                subaction = route[4].casefold().replace("_", "-") if len(route) >= 5 else ""
            else:
                subaction = route[3].casefold().replace("_", "-") if len(route) >= 4 else ""
            orchestrator = self.server.orchestrator
            if action in {"reproduce", "reproduction", "advance-reproduction"}:
                result = orchestrator.advance_reproduction(run_id)
            elif action in {"diagnose", "diagnosis", "advance-diagnosis", "retry-diagnosis", "request-diagnosis", "diagnosis-retry"} or (action in {"patch", "diagnose", "diagnosis"} and subaction in {"request-diagnosis", "diagnosis-retry", "retry", "request-another-diagnosis"}):
                if action in {"retry-diagnosis", "request-diagnosis", "diagnosis-retry"} or subaction in {"request-diagnosis", "diagnosis-retry", "retry", "request-another-diagnosis"}:
                    result = orchestrator.request_another_diagnosis(run_id, _actor(payload), _timestamp(payload), _digest(payload))
                else:
                    result = orchestrator.advance_diagnosis(run_id)
            elif action in {"approve", "approval"} or (action == "patch" and subaction == "approve"):
                result = orchestrator.approve_patch(run_id, _actor(payload), _timestamp(payload), _digest(payload))
            elif action == "reject" or (action == "patch" and subaction == "reject"):
                result = orchestrator.reject_patch(run_id, _actor(payload), _timestamp(payload), _digest(payload))
            elif action in {"return-to-diagnosis", "recover", "recovery"}:
                result = orchestrator.return_to_diagnosis(run_id, _actor(payload), _timestamp(payload))
            elif action in {"experiment", "run-experiment"}:
                allowed = {"kind", "command", "hypothesis_id", "hypothesis", "environment", "env_change", "env_name", "env_value", "instrumentation", "parent_commit", "regression_test", "timeout_seconds"}
                unknown = set(payload).difference(allowed)
                if unknown:
                    raise _HTTPError(400, "experiment contains unsupported fields")
                validate_experiment_timeout(payload.get("timeout_seconds", 30.0))
                result = orchestrator.run_experiment(run_id, payload)
            elif action in {"propose", "patch"}:
                result = orchestrator.propose_patch(run_id, _required_text(payload, "diff"), _required_text(payload, "regression_test"), payload.get("scope"))
            elif action in {"verify", "verification"}:
                result = orchestrator.verify_patch(run_id, _verification_plan(payload))
            elif action == "export":
                return self._export(run_id, payload)
            else:
                raise _HTTPError(404, "unknown run action")
            return 200, "application/json; charset=utf-8", result.to_dict()

        def _start_demo_run(self):
            data = _demo_intake(self.server.demo_repository or _default_fixture_repository())
            run_id = self.server.orchestrator.start_run(sanitize_intake(data, self.server.config.allowed_roots))
            return 201, "application/json; charset=utf-8", self.server.store.get_run(run_id).to_dict()

        def _export(self, run_id: str, payload: Mapping[str, Any]):
            run = self.server.store.get_run(run_id)
            events = self.server.store.list_events(run_id)
            report_format = str(payload.get("format", "markdown")).casefold()
            if report_format not in {"markdown", "md", "json"}:
                raise _HTTPError(400, "report format must be markdown or json")
            normalized = "json" if report_format == "json" else "md"
            export_root = self.server.config.data_root / "exports"
            export_root.mkdir(parents=True, exist_ok=True)
            filename = f"{run_id}.{normalized}"
            if isinstance(payload.get("filename"), str) and payload["filename"]:
                requested = payload["filename"]
                if Path(requested).name != requested or "/" in requested or "\\" in requested or requested in {".", ".."}:
                    raise _HTTPError(400, "export filename must be a simple relative filename")
                filename = requested
                if not filename.endswith(f".{normalized}"):
                    filename += f".{normalized}"
            destination = ensure_contained(export_root / filename, export_root)
            output = write_report(run, destination, report_format, events=events)
            return 200, "application/json; charset=utf-8", {"run_id": run_id, "format": report_format, "path": str(output)}

        def _read_json(self):
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length or "0")
            except ValueError as exc:
                raise _HTTPError(400, "invalid Content-Length") from exc
            if length < 0 or length > self.server.config.max_body_bytes:
                raise _HTTPError(413, "request body exceeds the configured bounded size")
            content_type = self.headers.get("Content-Type", "")
            if content_type.split(";", 1)[0].strip().casefold() != "application/json":
                raise _HTTPError(415, "JSON content type is required")
            if length == 0:
                raise _HTTPError(415, "JSON request body is required")
            raw = self.rfile.read(length)
            try:
                decoded = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise _HTTPError(400, "malformed JSON body") from exc
            if not isinstance(decoded, Mapping):
                raise _HTTPError(400, "JSON body must be an object")
            return dict(decoded)

        def _send(self, status: int, content_type: str, body: Any, headers: Mapping[str, str] | None = None, *, write_body: bool = True):
            if isinstance(body, (dict, list)):
                encoded = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
                content_type = "application/json; charset=utf-8"
            elif isinstance(body, str):
                encoded = body.encode("utf-8")
            else:
                encoded = bytes(body)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if write_body:
                self.wfile.write(encoded)

        def _send_error(self, status: int, message: str):
            self._send(status, "application/json; charset=utf-8", {"error": _safe_error(message)})

        def _method_not_allowed(self):
            self._send(
                405,
                "application/json; charset=utf-8",
                {"error": "method is not supported"},
                {"Allow": "GET, POST"},
                write_body=self.command != "HEAD",
            )

    return Handler


class _HTTPError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _enabled_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _demo_intake(repository: Path) -> dict[str, Any]:
    return {
        "repository_path": str(repository),
        "command": "python -m unittest discover -s tests -v",
        "failure_text": "ValueError: too many values to unpack (expected 2)",
        "constraints": [],
        "issue_url": None,
        "pr_url": None,
        "commit_sha": None,
        "branch": None,
    }


def _reject_demo_overrides(payload: Mapping[str, Any]) -> None:
    if payload:
        raise _HTTPError(400, "public demo uses a fixed checked-in fixture; repository and command overrides are not accepted")


def _safe_error(value: Any) -> str:
    message = redact_text(str(value).replace("\x00", ""))[0]
    return message[:512]


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _report_format(route: str, query: Mapping[str, list[str]]) -> str:
    if route == "report.json":
        return "json"
    if route == "report.md":
        return "markdown"
    if route == "reports" and query.get("format", ["markdown"])[0].casefold() == "json":
        return "json"
    value = query.get("format", ["markdown"])[0].casefold()
    if value not in {"markdown", "md", "json"}:
        raise _HTTPError(400, "report format must be markdown or json")
    return "json" if value == "json" else "markdown"


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise _HTTPError(400, f"{key} is required")
    return value


def _actor(payload: Mapping[str, Any]) -> str:
    return _required_text(payload, "actor")


def _timestamp(payload: Mapping[str, Any]) -> str:
    return _required_text(payload, "timestamp")


def _digest(payload: Mapping[str, Any]) -> str:
    return _required_text(payload, "patch_digest")


def _verification_plan(payload: Mapping[str, Any]) -> VerificationPlan:
    plan = payload.get("plan", payload)
    if not isinstance(plan, Mapping):
        raise _HTTPError(400, "verification plan must be an object")
    raw_timeout = plan.get("timeout_seconds", 30.0)
    if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)) or not math.isfinite(float(raw_timeout)) or not 0 < float(raw_timeout) <= MAX_VERIFICATION_TIMEOUT_SECONDS:
        raise _HTTPError(400, "timeout_seconds must be finite, positive, and no greater than 300")
    relevant = plan.get("relevant_tests", [])
    scope = plan.get("scope_files", [])
    if not isinstance(relevant, list) or not isinstance(scope, list):
        raise _HTTPError(400, "relevant_tests and scope_files must be lists")
    return VerificationPlan(
        original_failure_command=_required_plan_value(plan, "original_failure_command", "original_command"),
        regression_test=_required_plan_value(plan, "regression_test"),
        relevant_tests=list(relevant),
        broader_suite=plan.get("broader_suite"),
        timeout_seconds=float(raw_timeout),
        scope_files=list(scope),
    )


def _required_plan_value(payload: Mapping[str, Any], *keys: str):
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    raise _HTTPError(400, f"{keys[0]} is required")


def _materialize_demo_fixture(data_root: Path) -> Path:
    """Create an isolated, committed demo repository inside controlled run data."""
    source = _default_fixture_repository()
    destination = ensure_contained(data_root / "demo-fixture", data_root)
    if destination.exists():
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git", ".proofix-artifacts", "__pycache__"))
    subprocess.run(["git", "init", "-q"], cwd=destination, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "proofix-demo@example.test"], cwd=destination, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.name", "ProofFix Demo"], cwd=destination, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "add", "."], cwd=destination, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "commit", "-qm", "ProofFix demo fixture baseline"], cwd=destination, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return destination

def _default_fixture_repository() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures" / "broken-parser-repo"


def _default_recorded_fixture() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures" / "omnirush" / "reproducible-run.json"


def _artifact_content_type(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix == ".json":
        return "application/json; charset=utf-8"
    if suffix in {".md", ".markdown"}:
        return "text/markdown; charset=utf-8"
    if suffix in {".log", ".txt", ".out", ".err"}:
        return "text/plain; charset=utf-8"
    return "application/octet-stream"
