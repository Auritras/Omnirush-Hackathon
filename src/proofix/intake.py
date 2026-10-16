"""Validated, redacted user input for one ProofFix run."""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .models import MAX_INLINE_FIELD
from .redaction import redact_text


MAX_CONSTRAINTS = 16
_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,256}$")
_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


@dataclass(frozen=True)
class IntakeRequest:
    """The only intake fields that may enter the orchestrator context."""

    repository_path: str
    command: str
    failure_text: str
    constraints: tuple[str, ...] = ()
    issue_url: str | None = None
    pr_url: str | None = None
    commit_sha: str | None = None
    branch: str | None = None

    def __post_init__(self) -> None:
        """Normalize direct construction too; callers cannot bypass the boundary."""
        object.__setattr__(self, "repository_path", _repository_path(self.repository_path))
        object.__setattr__(self, "command", _text(self.command, "command", required=True))
        object.__setattr__(self, "failure_text", _text(self.failure_text, "failure_text", required=True))
        object.__setattr__(self, "constraints", _constraints(self.constraints))
        object.__setattr__(self, "issue_url", _reference_url(self.issue_url, "issue_url", "/issues/"))
        object.__setattr__(self, "pr_url", _reference_url(self.pr_url, "pr_url", "/pull/"))
        commit_sha = self.commit_sha
        if commit_sha is not None and (not isinstance(commit_sha, str) or not _SHA.fullmatch(commit_sha)):
            raise ValueError("commit_sha must be a hexadecimal commit reference")
        object.__setattr__(self, "commit_sha", commit_sha.lower() if commit_sha is not None else None)
        branch = self.branch
        if branch is not None and (not isinstance(branch, str) or not _BRANCH.fullmatch(branch) or ".." in branch or branch.startswith(("/", "-"))):
            raise ValueError("branch is invalid")
        object.__setattr__(self, "branch", branch)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository_path": self.repository_path,
            "command": self.command,
            "failure_text": self.failure_text,
            "constraints": list(self.constraints),
            "issue_url": self.issue_url,
            "pr_url": self.pr_url,
            "commit_sha": self.commit_sha,
            "branch": self.branch,
        }

    def agent_context(self) -> dict[str, Any]:
        """Return a deliberately explicit context; no arbitrary input survives."""
        return {
            "command": self.command,
            "failure_text": self.failure_text,
            "constraints": list(self.constraints),
            "references": {
                "issue_url": self.issue_url,
                "pr_url": self.pr_url,
                "commit_sha": self.commit_sha,
                "branch": self.branch,
            },
            "context_is_untrusted": True,
        }


def sanitize_intake(payload: Mapping[str, Any]) -> IntakeRequest:
    """Select, bound, and redact intake fields before persistence or prompting."""

    if not isinstance(payload, Mapping):
        raise ValueError("intake must be a mapping")
    repository_path = _repository_path(payload.get("repository_path"))
    command = _text(payload.get("command"), "command", required=True)
    failure_text = _text(payload.get("failure_text", ""), "failure_text", required=True)
    constraints_value = payload.get("constraints", ())
    if isinstance(constraints_value, str):
        constraints_value = (constraints_value,)
    constraints = _constraints(constraints_value)
    issue_url = _reference_url(payload.get("issue_url"), "issue_url", "/issues/")
    pr_url = _reference_url(payload.get("pr_url"), "pr_url", "/pull/")
    commit_sha = payload.get("commit_sha")
    if commit_sha is not None:
        if not isinstance(commit_sha, str) or not _SHA.fullmatch(commit_sha):
            raise ValueError("commit_sha must be a hexadecimal commit reference")
        commit_sha = commit_sha.lower()
    branch = payload.get("branch")
    if branch is not None and (not isinstance(branch, str) or not _BRANCH.fullmatch(branch) or ".." in branch or branch.startswith(("/", "-"))):
        raise ValueError("branch is invalid")
    return IntakeRequest(repository_path, command, failure_text, constraints, issue_url, pr_url, commit_sha, branch)


def _constraints(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, (list, tuple)) or len(value) > MAX_CONSTRAINTS:
        raise ValueError("constraints must be a bounded list of text")
    return tuple(_text(item, "constraint", required=True) for item in value)


def _text(value: Any, label: str, *, required: bool) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    if "\x00" in value or len(value) > MAX_INLINE_FIELD:
        raise ValueError(f"{label} is too large or contains a null byte")
    cleaned, _ = redact_text(value.strip())
    if required and not cleaned:
        raise ValueError(f"{label} is required")
    return cleaned


def _repository_path(value: Any) -> str:
    if isinstance(value, Path):
        value = str(value)
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > MAX_INLINE_FIELD:
        raise ValueError("repository_path is required and bounded")
    if _WINDOWS_ABSOLUTE.match(value):
        raise ValueError("repository_path must not be a Windows absolute path")
    normalized = value.replace("\\", "/")
    parts = Path(normalized).parts
    if ".." in parts:
        raise ValueError("repository_path may not contain parent traversal")
    return str(Path(normalized).expanduser())


def _reference_url(value: Any, label: str, required_fragment: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_INLINE_FIELD:
        raise ValueError(f"{label} must be a bounded URL")
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"} or not parsed.netloc:
        raise ValueError(f"{label} must be an HTTPS GitHub URL")
    if required_fragment not in parsed.path or parsed.query or parsed.fragment:
        raise ValueError(f"{label} has an invalid GitHub reference")
    return value
