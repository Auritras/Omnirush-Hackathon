"""Strict response records exchanged across the OmniRush boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlparse

from .models import MAX_INLINE_FIELD, SCHEMA_VERSION
from .redaction import redact_text


class SchemaError(ValueError):
    """Raised when an untrusted structured response is not admissible."""


_EVIDENCE_KINDS = frozenset({"file", "command", "log", "artifact", "issue", "test", "environment"})
_TRIAGE_CATEGORIES = frozenset(
    {
        "test_assertion",
        "test_assertion_failure",
        "build",
        "build_failure",
        "typecheck",
        "type_check",
        "lint",
        "lint_failure",
        "dependency",
        "dependency_failure",
        "environment",
        "environment_failure",
        "permission",
        "filesystem",
        "timeout",
        "flaky",
        "unknown",
    }
)


@dataclass(frozen=True)
class Evidence:
    kind: str
    summary: str = ""
    path: str | None = None
    line: int | None = None
    command: str | None = None
    result: str | None = None
    artifact_ref: str | None = None
    url: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in {
            "kind": self.kind,
            "summary": self.summary,
            "path": self.path,
            "line": self.line,
            "command": self.command,
            "result": self.result,
            "artifact_ref": self.artifact_ref,
            "url": self.url,
            "details": self.details,
        }.items() if value is not None and value != ""}


@dataclass(frozen=True)
class Triage:
    schema_version: str
    category: str
    suspected_subsystem: str
    relevant_files: tuple[str, ...]
    command_assumptions: tuple[str, ...]
    confidence: float
    evidence: tuple[Evidence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "category": self.category,
            "suspected_subsystem": self.suspected_subsystem,
            "relevant_files": list(self.relevant_files),
            "command_assumptions": list(self.command_assumptions),
            "confidence": self.confidence,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class Hypothesis:
    schema_version: str
    hypothesis: str
    supporting_evidence: tuple[Evidence, ...]
    contradicting_evidence: tuple[Evidence, ...]
    confidence: float
    experiment: str
    role: str | None = None
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "hypothesis": self.hypothesis,
            "supporting_evidence": [item.to_dict() for item in self.supporting_evidence],
            "contradicting_evidence": [item.to_dict() for item in self.contradicting_evidence],
            "confidence": self.confidence,
            "experiment": self.experiment,
            "role": self.role,
            "session_id": self.session_id,
        }


@dataclass(frozen=True)
class PatchProposal:
    schema_version: str
    digest: str
    diff_ref: str | None
    files: tuple[str, ...]
    regression_test: str
    risk_summary: str
    verification_plan: tuple[str, ...]
    evidence: tuple[Evidence, ...]
    diff: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "digest": self.digest,
            "diff_ref": self.diff_ref,
            "files": list(self.files),
            "regression_test": self.regression_test,
            "risk_summary": self.risk_summary,
            "verification_plan": list(self.verification_plan),
            "evidence": [item.to_dict() for item in self.evidence],
            "diff": self.diff,
        }


def validate_triage(payload: Mapping[str, Any]) -> Triage:
    data = _object(payload, "triage", {
        "schema_version", "category", "suspected_subsystem", "relevant_files", "command_assumptions", "confidence", "evidence"
    })
    category = _text(data["category"], "triage category")
    if category not in _TRIAGE_CATEGORIES:
        raise SchemaError("unsupported triage category")
    files = _paths(data["relevant_files"], "relevant_files", allow_empty=True)
    assumptions = _texts(data["command_assumptions"], "command_assumptions")
    return Triage(
        SCHEMA_VERSION,
        category,
        _text(data["suspected_subsystem"], "suspected_subsystem"),
        files,
        assumptions,
        _confidence(data["confidence"]),
        _evidence(data["evidence"], "evidence", required=True),
    )


def validate_hypothesis(payload: Mapping[str, Any]) -> Hypothesis:
    data = _object(payload, "hypothesis", {
        "schema_version", "hypothesis", "supporting_evidence", "contradicting_evidence", "confidence", "experiment", "role", "session_id"
    })
    supporting = _evidence(data["supporting_evidence"], "supporting_evidence", required=True)
    contradicting = _evidence(data["contradicting_evidence"], "contradicting_evidence", required=False)
    return Hypothesis(
        SCHEMA_VERSION,
        _text(data["hypothesis"], "hypothesis"),
        supporting,
        contradicting,
        _confidence(data["confidence"]),
        _text(data["experiment"], "experiment"),
        _optional_text(data.get("role"), "role"),
        _optional_text(data.get("session_id"), "session_id"),
    )


def validate_patch_proposal(payload: Mapping[str, Any]) -> PatchProposal:
    data = _object(payload, "patch proposal", {
        "schema_version", "digest", "diff_ref", "files", "regression_test", "risk_summary", "verification_plan", "evidence", "diff"
    })
    digest = _text(data["digest"], "patch digest")
    if len(digest) > 256:
        raise SchemaError("patch digest is too long")
    diff_ref = data.get("diff_ref")
    if diff_ref is not None:
        diff_ref = _relative_path(diff_ref, "diff_ref")
    diff = data.get("diff")
    if diff is not None:
        diff = _text(diff, "diff")
    return PatchProposal(
        SCHEMA_VERSION,
        digest,
        diff_ref,
        _paths(data["files"], "patch files", allow_empty=False),
        _text(data["regression_test"], "regression_test"),
        _text(data["risk_summary"], "risk_summary"),
        _texts(data["verification_plan"], "verification_plan", required=True),
        _evidence(data["evidence"], "evidence", required=True),
        diff,
    )


def _object(payload: Mapping[str, Any], label: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise SchemaError(f"{label} must be a JSON object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise SchemaError(f"unsupported {label} schema version")
    unknown = set(payload).difference(allowed)
    if unknown:
        raise SchemaError(f"unknown {label} fields: {', '.join(sorted(unknown))}")
    missing = {"schema_version"}.difference(payload)
    if label == "triage":
        missing |= {"category", "suspected_subsystem", "relevant_files", "command_assumptions", "confidence", "evidence"}.difference(payload)
    elif label == "hypothesis":
        missing |= {"hypothesis", "supporting_evidence", "contradicting_evidence", "confidence", "experiment"}.difference(payload)
    else:
        missing |= {"digest", "files", "regression_test", "risk_summary", "verification_plan", "evidence"}.difference(payload)
    if missing:
        raise SchemaError(f"{label} is missing required fields: {', '.join(sorted(missing))}")
    return dict(payload)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > MAX_INLINE_FIELD:
        raise SchemaError(f"{label} must be bounded non-empty text")
    return _redact(value)


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _texts(value: Any, label: str, *, required: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise SchemaError(f"{label} must be a list")
    if required and not value:
        raise SchemaError(f"{label} must not be empty")
    if len(value) > 64:
        raise SchemaError(f"{label} has too many entries")
    return tuple(_text(item, label) for item in value)


def _paths(value: Any, label: str, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or (not allow_empty and not value):
        raise SchemaError(f"{label} must be a non-empty list of relative paths")
    if len(value) > 128:
        raise SchemaError(f"{label} has too many entries")
    return tuple(_relative_path(item, label) for item in value)


def _relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/") or (len(value) >= 3 and value[1] == ":" and value[2] in "/\\"):
        raise SchemaError(f"{label} must be a repository-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise SchemaError(f"{label} escapes the repository")
    return value


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise SchemaError("confidence must be a number between 0 and 1")
    return float(value)


def _evidence(value: Any, label: str, *, required: bool) -> tuple[Evidence, ...]:
    if not isinstance(value, (list, tuple)) or (required and not value):
        raise SchemaError(f"{label} must contain evidence")
    if len(value) > 64:
        raise SchemaError(f"{label} has too many records")
    result: list[Evidence] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise SchemaError(f"{label} entries must be objects")
        allowed = {"kind", "summary", "path", "line", "command", "result", "artifact_ref", "url", "details"}
        unknown = set(item).difference(allowed)
        if unknown:
            raise SchemaError(f"unknown evidence fields: {', '.join(sorted(unknown))}")
        kind = item.get("kind")
        if not isinstance(kind, str):
            raise SchemaError("evidence kind must be text")
        if kind not in _EVIDENCE_KINDS:
            raise SchemaError(f"unsupported evidence kind: {kind}")
        summary = item.get("summary", "")
        if not isinstance(summary, str) or len(summary) > MAX_INLINE_FIELD or "\x00" in summary:
            raise SchemaError("evidence summary is invalid")
        summary = _redact(summary)
        if kind == "file" and "path" not in item:
            raise SchemaError("file evidence requires path")
        path = _relative_path(item["path"], "evidence path") if kind == "file" else item.get("path")
        if path is not None and kind != "file":
            path = _relative_path(path, "evidence path")
        line = item.get("line")
        if line is not None and (isinstance(line, bool) or not isinstance(line, int) or line < 1):
            raise SchemaError("evidence line must be a positive integer")
        command = item.get("command")
        result_text = item.get("result")
        if kind in {"command", "test"} and not isinstance(command, str):
            raise SchemaError("command evidence requires a command")
        if command is not None:
            command = _text(command, "evidence command")
        if result_text is not None:
            result_text = _text(result_text, "evidence result")
        artifact_ref = item.get("artifact_ref")
        if artifact_ref is not None:
            artifact_ref = _relative_path(artifact_ref, "artifact_ref")
        if kind == "artifact" and not artifact_ref:
            raise SchemaError("artifact evidence requires artifact_ref")
        url = item.get("url")
        if url is not None:
            parsed = urlparse(url) if isinstance(url, str) else None
            if parsed is None or parsed.scheme != "https" or not parsed.netloc:
                raise SchemaError("evidence URL must be HTTPS")
            url = _redact(url)
        details = item.get("details", {})
        if not isinstance(details, Mapping):
            raise SchemaError("evidence details must be an object")
        details = _details(details)
        result.append(Evidence(kind, summary, path, line, command, result_text, artifact_ref, url, details))
    return tuple(result)


def _redact(value: str) -> str:
    return redact_text(value)[0]


def _details(value: Mapping[str, Any]) -> dict[str, Any]:
    if any(not isinstance(key, str) for key in value):
        raise SchemaError("evidence detail keys must be text")
    cleaned = _redact_value(dict(value))
    try:
        encoded = json.dumps(cleaned, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SchemaError("evidence details must be JSON-compatible") from exc
    if len(encoded) > MAX_INLINE_FIELD * 4:
        raise SchemaError("evidence details are too large")
    return cleaned


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) > MAX_INLINE_FIELD:
            raise SchemaError("evidence detail text is too large")
        return _redact(value)
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise SchemaError("evidence detail keys must be text")
        return {key: _redact_value(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        raise SchemaError("evidence details must use JSON arrays, not tuples")
    return value
