"""Portable, JSON-compatible records used by the ProofFix state machine."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
import json
from pathlib import PurePosixPath
import time
from typing import Any, ClassVar, Mapping
from uuid import uuid4


SCHEMA_VERSION = "1.0"
MAX_INLINE_FIELD = 4096
KNOWN_STATES = frozenset(
    {
        "created",
        "triaging",
        "reproducing",
        "not_reproduced",
        "blocked",
        "reproduced",
        "diagnosing",
        "experimenting",
        "patch_proposed",
        "awaiting_approval",
        "applying",
        "verifying",
        "verification_failed",
        "verified",
        "verification_blocked",
        "github_pending_approval",
        "github_complete",
    }
)


class DomainError(ValueError):
    """Base error for invalid domain records or state changes."""


class ValidationError(DomainError):
    """Raised when a record cannot be safely decoded or persisted."""


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("timestamps must be UTC ISO-8601 strings")
    try:
        parsed = _parse_timestamp(value)
    except ValueError as exc:
        raise ValidationError("invalid UTC timestamp") from exc
    if parsed.tzinfo is None:
        raise ValidationError("timestamps must include a UTC offset")
    return value


def _parse_timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized)


def _validate_json(value: Any, *, field_name: str = "value") -> Any:
    try:
        json.dumps(_json_compatible(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} must be JSON-compatible") from exc
    return value


def _opaque_id(value: Any, prefix: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix) or len(value) <= len(prefix):
        raise ValidationError(f"{prefix.rstrip('-')} ID is invalid")
    if any(character.isspace() for character in value):
        raise ValidationError("opaque IDs may not contain whitespace")
    return value


def _artifact_ref(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or value.startswith("\\")
        or (len(value) >= 3 and value[1] == ":" and value[0].isalpha() and value[2] in ("/", "\\"))
        or "\\" in value
    ):
        raise ValidationError("artifact references must be relative paths")
    path = PurePosixPath(value)
    if ".." in path.parts:
        raise ValidationError("artifact references may not escape the run root")
    return value


class JsonRecord:
    schema_version: ClassVar[str] = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {item.name: _json_compatible(getattr(self, item.name)) for item in fields(self)}
        _validate_json(result)
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def from_json(cls, value: str) -> "JsonRecord":
        try:
            decoded = json.loads(value, parse_constant=_reject_non_finite)
        except (TypeError, ValueError) as exc:
            raise ValidationError("record JSON is malformed") from exc
        decoder = getattr(cls, "from_dict", None)
        if decoder is None:
            raise ValidationError(f"{cls.__name__} does not define a decoder")
        return decoder(decoded)


@dataclass
class EvidenceRecord(JsonRecord):
    schema_version: str = SCHEMA_VERSION
    kind: str = ""
    summary: str = ""
    artifact_ref: str | None = None
    source: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version, "evidence")
        if not self.kind or not isinstance(self.kind, str):
            raise ValidationError("evidence kind is required")
        if not isinstance(self.summary, str):
            raise ValidationError("evidence summary must be text")
        if self.artifact_ref is not None:
            self.artifact_ref = _artifact_ref(self.artifact_ref)
        _validate_details(self.details, field_name="evidence details")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceRecord":
        required = {"schema_version", "kind", "summary", "details"}
        _require_mapping_fields(value, required, "evidence")
        return cls(**{key: value[key] for key in ("schema_version", "kind", "summary", "artifact_ref", "source", "details") if key in value})


@dataclass
class CheckResult(JsonRecord):
    schema_version: str = SCHEMA_VERSION
    name: str = ""
    status: str = ""
    command: str | None = None
    command_ref: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    stdout_ref: str | None = None
    stderr_ref: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version, "check")
        if not self.name or not self.status:
            raise ValidationError("check name and status are required")
        _validate_bounded(self.command, "command")
        for name in ("command_ref", "stdout_ref", "stderr_ref"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, _artifact_ref(value))
        _validate_details(self.details, field_name="check details")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CheckResult":
        _require_mapping_fields(value, {"schema_version", "name", "status", "details"}, "check")
        return cls(**{item.name: value[item.name] for item in fields(cls) if item.name in value})


@dataclass
class PatchProposal(JsonRecord):
    schema_version: str = SCHEMA_VERSION
    digest: str = ""
    diff_ref: str | None = None
    files: list[str] = field(default_factory=list)
    regression_test: str | None = None
    risk_summary: str = ""
    verification_plan: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version, "patch")
        if not self.digest:
            raise ValidationError("patch digest is required")
        if self.diff_ref is not None:
            self.diff_ref = _artifact_ref(self.diff_ref)
        _validate_bounded(self.regression_test, "regression_test")
        _validate_json(self.files, field_name="patch files")
        _validate_json(self.verification_plan, field_name="verification plan")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PatchProposal":
        _require_mapping_fields(value, {"schema_version", "digest", "files", "verification_plan"}, "patch")
        return cls(**{item.name: value[item.name] for item in fields(cls) if item.name in value})


@dataclass
class EventRecord(JsonRecord):
    schema_version: str = SCHEMA_VERSION
    event_id: str = ""
    run_id: str = ""
    timestamp: str = ""
    kind: str = ""
    stage: str | None = None
    status: str | None = None
    command: str | None = None
    command_ref: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    stdout_ref: str | None = None
    stderr_ref: str | None = None
    state_before: str | None = None
    state_after: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version, "event")
        _opaque_id(self.event_id, "evt-")
        _opaque_id(self.run_id, "run-")
        _validate_timestamp(self.timestamp)
        if not self.kind or not isinstance(self.kind, str):
            raise ValidationError("event kind is required")
        _validate_bounded(self.command, "command")
        _validate_bounded(self.stdout, "stdout")
        _validate_bounded(self.stderr, "stderr")
        for name in ("command_ref", "stdout_ref", "stderr_ref"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, _artifact_ref(value))
        _validate_details(self.details, field_name="event details")
        for name in ("state_before", "state_after"):
            state = getattr(self, name)
            if state is not None and state not in KNOWN_STATES:
                raise ValidationError(f"unknown {name}: {state}")

    @staticmethod
    def now_timestamp() -> str:
        return utc_timestamp()

    @classmethod
    def new(cls, run_id: str, kind: str, **kwargs: Any) -> "EventRecord":
        # The timestamp-like prefix keeps append order deterministic across
        # independently-created store instances while the random suffix keeps
        # the identifier opaque and collision-resistant.
        return cls(event_id=f"evt-{time.time_ns():020d}-{uuid4().hex}", run_id=run_id, timestamp=utc_timestamp(), kind=kind, **kwargs)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EventRecord":
        _require_mapping_fields(value, {"schema_version", "event_id", "run_id", "timestamp", "kind", "details"}, "event")
        return cls(**{item.name: value[item.name] for item in fields(cls) if item.name in value})


@dataclass
class RunRecord(JsonRecord):
    schema_version: str = SCHEMA_VERSION
    run_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    state: str = "created"
    intake: dict[str, Any] = field(default_factory=dict)
    stages: list[dict[str, Any]] = field(default_factory=list)
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    experiments: list[dict[str, Any]] = field(default_factory=list)
    patch: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    github: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    omnirush_sessions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version, "run")
        _opaque_id(self.run_id, "run-")
        _validate_timestamp(self.created_at)
        _validate_timestamp(self.updated_at)
        if not isinstance(self.state, str) or not self.state:
            raise ValidationError("run state is required")
        if self.state not in KNOWN_STATES:
            raise ValidationError(f"unknown run state: {self.state}")
        _validate_json(self.intake, field_name="intake")
        for name in ("stages", "hypotheses", "experiments", "evidence", "omnirush_sessions"):
            _validate_json(getattr(self, name), field_name=name)
        for name in ("patch", "verification", "github"):
            value = getattr(self, name)
            if value is not None:
                _validate_json(value, field_name=name)

    @property
    def input(self) -> dict[str, Any]:
        """The design document calls the immutable intake field ``input``."""
        return self.intake

    @property
    def status(self) -> str:
        return self.state

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        result["input"] = result.pop("intake")
        return result

    @classmethod
    def new(cls, intake: Mapping[str, Any]) -> "RunRecord":
        now = utc_timestamp()
        return cls(run_id=f"run-{uuid4().hex}", created_at=now, updated_at=now, intake=dict(intake))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunRecord":
        _require_mapping_fields(value, {"schema_version", "run_id", "created_at", "updated_at", "state", "input"}, "run")
        data = dict(value)
        data["intake"] = data.pop("input")
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: data[key] for key in allowed if key in data})


def _require_mapping_fields(value: Mapping[str, Any], required: set[str], label: str) -> None:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{label} must be a JSON object")
    missing = sorted(required.difference(value.keys()))
    if missing:
        raise ValidationError(f"{label} is missing required fields: {', '.join(missing)}")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError(f"unsupported {label} schema version")


def _validate_schema_version(value: Any, label: str) -> None:
    if value != SCHEMA_VERSION:
        raise ValidationError(f"unsupported {label} schema version")


def _json_compatible(value: Any) -> Any:
    if isinstance(value, JsonRecord):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    return value


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _validate_details(value: Any, *, field_name: str) -> None:
    _validate_json(value, field_name=field_name)
    encoded = json.dumps(_json_compatible(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded) > MAX_INLINE_FIELD * 4:
        raise ValidationError(f"{field_name} is too large; store content as artifact references")
    _walk_detail_values(value, field_name)


def _walk_detail_values(value: Any, field_name: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key).lower()
            if key_text.endswith("_ref") and isinstance(nested, str):
                _artifact_ref(nested)
            if isinstance(nested, str) and len(nested) > MAX_INLINE_FIELD and not key_text.endswith("_ref"):
                raise ValidationError(f"{field_name}.{key} is too large; store content as an artifact reference")
            _walk_detail_values(nested, f"{field_name}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _walk_detail_values(nested, f"{field_name}[{index}]")


def _validate_bounded(value: str | None, field_name: str) -> None:
    if value is not None and (not isinstance(value, str) or len(value) > MAX_INLINE_FIELD):
        raise ValidationError(f"{field_name} is too large; store it as an artifact reference")
