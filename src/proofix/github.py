"""Explicit, read-only GitHub CLI access for ProofFix.

The adapter is deliberately small: a caller must ask for one of the read
operations, and every invocation is an argument-array subprocess with bounded,
redacted output.  It never receives a token and it exposes no GitHub write
operation.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import shutil
import subprocess
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

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "data": self.data,
            "next_steps": list(self.next_steps),
            "error": self.error,
            "command": list(self.command),
            "raw_size": self.raw_size,
            "truncated": self.truncated,
        }


class GitHubReader:
    """Run explicit, read-only ``gh`` queries without exposing credentials."""

    def __init__(
        self,
        gh_path: str = "gh",
        *,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
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
        safe_command = tuple(str(item) for item in command)
        if shutil.which(self.gh_path) is None:
            return GitHubRead(
                "blocked",
                next_steps=("Install the GitHub CLI (gh) and retry the explicit GitHub read.",),
                error="GitHub CLI is unavailable",
                command=safe_command,
            )
        try:
            runner = self._runner or subprocess.run
            completed = runner(
                list(command),
                check=False,
                capture_output=True,
                text=True,
                shell=False,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError:
            return GitHubRead("blocked", next_steps=("Install the GitHub CLI (gh) and retry the explicit GitHub read.",), error="GitHub CLI is unavailable", command=safe_command)
        except subprocess.TimeoutExpired:
            return GitHubRead("blocked", next_steps=("Retry the GitHub read when the network is available.",), error="GitHub CLI read timed out", command=safe_command)
        except OSError:
            return GitHubRead("blocked", next_steps=("Check local GitHub CLI permissions and retry the read.",), error="GitHub CLI could not be started", command=safe_command)

        stdout = completed.stdout if isinstance(completed.stdout, str) else ""
        stderr = completed.stderr if isinstance(completed.stderr, str) else ""
        bounded, raw_size, truncated = _bounded(stdout, self.max_output_bytes)
        if completed.returncode != 0:
            next_steps = _next_steps(stderr)
            return GitHubRead(
                "blocked",
                next_steps=next_steps,
                error="GitHub CLI read was not successful",
                command=safe_command,
                raw_size=raw_size,
                truncated=truncated,
            )
        if truncated:
            return GitHubRead(
                "blocked",
                next_steps=("Narrow the GitHub read or use a bounded fixture before retrying.",),
                error="GitHub output exceeded the configured bound",
                command=safe_command,
                raw_size=raw_size,
                truncated=True,
            )
        cleaned = _redact_value(bounded)
        if json_output:
            try:
                data = json.loads(cleaned, parse_constant=_reject_constant)
            except (TypeError, ValueError):
                return GitHubRead("error", next_steps=("Retry the GitHub read and inspect the CLI version/output format.",), error="GitHub CLI returned malformed JSON", command=safe_command, raw_size=raw_size)
        else:
            data = cleaned
        return GitHubRead("ok", data=data, command=safe_command, raw_size=raw_size)


def _repository(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or "\x00" in value:
        raise ValueError("repository must be bounded owner/name text")
    parts = value.split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts) or any(ch.isspace() for ch in value):
        raise ValueError("repository must be an owner/name reference")
    if any(ch in value for ch in "$;&|`<>\\\"'"):
        raise ValueError("repository contains unsafe characters")
    return value


def _identifier(value: Any, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{label} identifier must be text or an integer")
    text = str(value)
    if not text or len(text) > 128 or any(ch.isspace() for ch in text) or any(ch in text for ch in "$;&|`<>\\\"'") or text.startswith("-"):
        raise ValueError(f"{label} identifier contains unsafe characters")
    return text


def _bounded(value: str, limit: int) -> tuple[str, int, bool]:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return value, len(encoded), False
    bounded = encoded[:limit].decode("utf-8", errors="ignore")
    return bounded, len(encoded), True


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
