"""Explicit, read-only GitHub CLI access for ProofFix.

The adapter is deliberately small: a caller must ask for one of the read
operations, and every invocation is an argument-array subprocess with bounded,
redacted output.  It never receives a token and it exposes no GitHub write
operation.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import signal
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Sequence

from .redaction import redact_text


DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024
DEFAULT_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class GitHubRead:
    """A bounded result from one explicit GitHub read.

    ``status`` is ``ok`` only when the CLI completed and its output parsed.
    Missing CLI, authentication, network, timeout, and malformed output are
    represented as non-success results with safe next steps.
    """

    status: str
    data: Any = None
    next_steps: tuple[str, ...] = ()
    error: str | None = None
    command: tuple[str, ...] = ()
    raw_size: int = 0
    truncated: bool = False
    stdout_size: int = 0
    stderr_size: int = 0
    timed_out: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "command", tuple(_safe_command_item(str(item)) for item in self.command))

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "data": self.data,
            "next_steps": list(self.next_steps),
            "error": self.error,
            "command": [_safe_command_item(str(item)) for item in self.command],
            "raw_size": self.raw_size,
            "truncated": self.truncated,
            "stdout_size": self.stdout_size,
            "stderr_size": self.stderr_size,
            "timed_out": self.timed_out,
        }


class GitHubReader:
    """Run explicit, read-only ``gh`` queries without exposing credentials."""

    def __init__(
        self,
        gh_path: str = "gh",
        *,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        runner: Callable[..., Any] | None = None,
    ) -> None:
        if not isinstance(gh_path, str) or not gh_path or "\x00" in gh_path:
            raise ValueError("gh_path must be non-empty text")
        if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.gh_path = gh_path
        self.max_output_bytes = max_output_bytes
        self.timeout_seconds = float(timeout_seconds)
        # Resolve the stdlib runner at read time so hosts/tests can replace the
        # process boundary without having to construct a second reader.
        self._runner = runner

    # Public command builders make the no-shell boundary inspectable by hosts.
    def repository_command(self, repository: str) -> list[str]:
        repo = _repository(repository)
        return [self.gh_path, "repo", "view", repo, "--json", "nameWithOwner,description,defaultBranchRef,url"]

    def issue_command(self, repository: str, issue: str | int) -> list[str]:
        return [self.gh_path, "issue", "view", _identifier(issue, "issue"), "--repo", _repository(repository), "--json", "title,body,url,number"]

    def pull_request_diff_command(self, repository: str, pull_request: str | int) -> list[str]:
        return [self.gh_path, "pr", "diff", _identifier(pull_request, "pull request"), "--repo", _repository(repository)]

    def actions_status_command(self, repository: str, run_id: str | int) -> list[str]:
        return [self.gh_path, "run", "view", _identifier(run_id, "Actions run"), "--repo", _repository(repository), "--json", "status,conclusion,workflowName,headBranch,url"]

    def actions_logs_command(self, repository: str, run_id: str | int) -> list[str]:
        return [self.gh_path, "run", "view", _identifier(run_id, "Actions run"), "--repo", _repository(repository), "--log"]

    def repository_metadata(self, repository: str) -> GitHubRead:
        return self._read(self.repository_command(repository), json_output=True)

    def issue_text(self, repository: str, issue: str | int) -> GitHubRead:
        return self._read(self.issue_command(repository, issue), json_output=True)

    def pull_request_diff(self, repository: str, pull_request: str | int) -> GitHubRead:
        return self._read(self.pull_request_diff_command(repository, pull_request), json_output=False)

    def actions_status(self, repository: str, run_id: str | int) -> GitHubRead:
        return self._read(self.actions_status_command(repository, run_id), json_output=True)

    def actions_logs(self, repository: str, run_id: str | int) -> GitHubRead:
        return self._read(self.actions_logs_command(repository, run_id), json_output=False)

    # Descriptive aliases for integrations that use a read_* naming scheme.
    read_repository_metadata = repository_metadata
    read_issue_text = issue_text
    read_pull_request_diff = pull_request_diff
    read_actions_status = actions_status
    read_actions_logs = actions_logs

    def _read(self, command: Sequence[str], *, json_output: bool) -> GitHubRead:
        safe_command = tuple(_safe_command_item(str(item)) for item in command)
        if shutil.which(self.gh_path) is None:
            return GitHubRead(
                "blocked",
                next_steps=("Install the GitHub CLI (gh) and retry the explicit GitHub read.",),
                error="GitHub CLI is unavailable",
                command=safe_command,
            )
        try:
            runner = self._runner or subprocess.Popen
            captured = _run_bounded(
                runner,
                list(command),
                max_output_bytes=self.max_output_bytes,
                timeout_seconds=self.timeout_seconds,
            )
        except FileNotFoundError:
            return GitHubRead("blocked", next_steps=("Install the GitHub CLI (gh) and retry the explicit GitHub read.",), error="GitHub CLI is unavailable", command=safe_command)
        except subprocess.TimeoutExpired:
            return GitHubRead("blocked", next_steps=("Retry the GitHub read when the network is available.",), error="GitHub CLI read timed out", command=safe_command)
        except OSError:
            return GitHubRead("blocked", next_steps=("Check local GitHub CLI permissions and retry the read.",), error="GitHub CLI could not be started", command=safe_command)

        stdout = captured.stdout
        stderr = captured.stderr
        raw_size = captured.stdout_size + captured.stderr_size
        if captured.truncated:
            if captured.timed_out:
                return GitHubRead(
                    "blocked",
                    next_steps=("Retry the GitHub read when the network is available.",),
                    error="GitHub CLI read timed out",
                    command=safe_command,
                    raw_size=raw_size,
                    truncated=True,
                    stdout_size=captured.stdout_size,
                    stderr_size=captured.stderr_size,
                    timed_out=True,
                )
            reason = "GitHub output exceeded the configured bound"
            return GitHubRead(
                "blocked",
                next_steps=("Narrow the GitHub read or use a bounded fixture before retrying.",),
                error=reason,
                command=safe_command,
                raw_size=raw_size,
                truncated=True,
                stdout_size=captured.stdout_size,
                stderr_size=captured.stderr_size,
            )
        if captured.returncode != 0:
            return GitHubRead(
                "blocked",
                next_steps=_next_steps(stderr),
                error="GitHub CLI read was not successful",
                command=safe_command,
                raw_size=raw_size,
                stdout_size=captured.stdout_size,
                stderr_size=captured.stderr_size,
            )
        cleaned = _redact_value(stdout)
        if json_output:
            try:
                data = json.loads(cleaned, parse_constant=_reject_constant)
            except (TypeError, ValueError):
                return GitHubRead("error", next_steps=("Retry the GitHub read and inspect the CLI version/output format.",), error="GitHub CLI returned malformed JSON", command=safe_command, raw_size=raw_size, stdout_size=captured.stdout_size, stderr_size=captured.stderr_size)
        else:
            data = cleaned
        return GitHubRead("ok", data=data, command=safe_command, raw_size=raw_size, stdout_size=captured.stdout_size, stderr_size=captured.stderr_size)


def _repository(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or "\x00" in value:
        raise ValueError("repository must be bounded owner/name text")
    parts = value.split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts) or any(ch.isspace() for ch in value):
        raise ValueError("repository must be an owner/name reference")
    if _is_absolute_identifier(value) or ".." in value or any(ch in value for ch in "$;&|`<>\\\"'") or _TOKEN_SHAPE.search(value):
        raise ValueError("repository contains unsafe characters")
    return value


def _identifier(value: Any, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{label} identifier must be text or an integer")
    text = str(value)
    if (
        not text
        or len(text) > 128
        or any(ch.isspace() for ch in text)
        or any(ch in text for ch in "/\\")
        or ".." in text
        or text.startswith(("-", "."))
        or _is_absolute_identifier(text)
        or any(ch in text for ch in "$;&|`<>\\\"'")
        or _TOKEN_SHAPE.search(text)
    ):
        raise ValueError(f"{label} identifier contains unsafe characters")
    return text


_TOKEN_SHAPE = re.compile(r"(?i)(?:gh[pousr]_[a-z0-9_-]{8,}|github_pat_[a-z0-9_-]{8,}|(?:token|secret|api[_-]?key)[=:][^\s]+)")


def _is_absolute_identifier(value: str) -> bool:
    return value.startswith(("/", "\\")) or bool(re.match(r"^[A-Za-z]:[\\/]", value))


def _safe_command_item(value: str) -> str:
    cleaned = redact_text(value)[0]
    if _TOKEN_SHAPE.search(cleaned):
        return "[REDACTED]"
    return cleaned


@dataclass
class _BoundedProcessResult:
    returncode: int
    stdout: str
    stderr: str
    stdout_size: int
    stderr_size: int
    truncated: bool
    timed_out: bool


def _run_bounded(
    runner: Callable[..., Any],
    command: list[str],
    *,
    max_output_bytes: int,
    timeout_seconds: float,
) -> _BoundedProcessResult:
    """Run a CLI while hard-capping both pipes at the process boundary.

    Reader threads continuously drain both pipes so a noisy CLI cannot block on
    a full pipe. Once either stream crosses its cap, the process group is
    terminated and only the bounded prefixes are retained.
    """

    process = runner(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
    )
    streams: dict[str, dict[str, Any]] = {
        "stdout": {"pipe": process.stdout, "data": bytearray(), "size": 0, "overflow": False},
        "stderr": {"pipe": process.stderr, "data": bytearray(), "size": 0, "overflow": False},
    }
    overflow = threading.Event()

    def drain(record: dict[str, Any]) -> None:
        pipe = record["pipe"]
        try:
            while True:
                chunk = pipe.read(65536)
                if not chunk:
                    return
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                record["size"] += len(chunk)
                remaining = max_output_bytes - len(record["data"])
                if remaining > 0:
                    record["data"].extend(chunk[:remaining])
                if record["size"] > max_output_bytes:
                    record["overflow"] = True
                    overflow.set()
                    return
        finally:
            try:
                pipe.close()
            except OSError:
                pass

    threads = [threading.Thread(target=drain, args=(record,), daemon=True) for record in streams.values()]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    while process.poll() is None:
        if overflow.is_set():
            _terminate_process(process)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            _terminate_process(process)
            break
        time.sleep(0.005)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        _kill_process(process)
        process.wait(timeout=1)
    for thread in threads:
        thread.join(timeout=1)
    stdout = bytes(streams["stdout"]["data"]).decode("utf-8", errors="replace")
    stderr = bytes(streams["stderr"]["data"]).decode("utf-8", errors="replace")
    return _BoundedProcessResult(
        returncode=process.returncode if process.returncode is not None else 1,
        stdout=stdout,
        stderr=stderr,
        stdout_size=streams["stdout"]["size"],
        stderr_size=streams["stderr"]["size"],
        truncated=timed_out or overflow.is_set(),
        timed_out=timed_out,
    )


def _terminate_process(process: Any) -> None:
    try:
        if os.name == "posix" and getattr(process, "pid", None):
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except (OSError, ProcessLookupError):
        pass


def _kill_process(process: Any) -> None:
    try:
        if os.name == "posix" and getattr(process, "pid", None):
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        pass


def _redact_value(value: Any, key: str = "") -> Any:
    if isinstance(value, str):
        if key and any(word in key.casefold() for word in ("token", "secret", "password", "credential", "api_key")):
            return "[REDACTED]"
        return redact_text(value)[0]
    if isinstance(value, dict):
        return {str(name): _redact_value(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, key) for item in value]
    return value


def _next_steps(stderr: str) -> tuple[str, ...]:
    lower = stderr.casefold()
    if any(marker in lower for marker in ("auth", "logged in", "token", "credential")):
        return ("Run gh auth login, then retry the explicit GitHub read.",)
    if any(marker in lower for marker in ("network", "connect", "timeout", "resolve", "dns")):
        return ("Check network access to GitHub, then retry the explicit read.",)
    return ("Check the repository reference and GitHub CLI permissions, then retry the explicit read.",)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is not allowed: {value}")
