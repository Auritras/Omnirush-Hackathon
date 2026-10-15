"""Deterministic classification of a command result against supplied failure evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from collections.abc import Mapping
from typing import Any

from .runner import CommandResult


class ReproductionStatus(str, Enum):
    reproduced = "reproduced"
    not_reproduced = "not_reproduced"
    blocked = "blocked"
    REPRODUCED = reproduced
    NOT_REPRODUCED = not_reproduced
    BLOCKED = blocked


@dataclass(frozen=True)
class ReproductionAssessment:
    status: ReproductionStatus
    reason: str
    expected_exit_code: int | None = None
    actual_exit_code: int | None = None
    normalized_evidence: str = ""

    @property
    def reproduced(self) -> bool:
        return self.status is ReproductionStatus.reproduced


def compare_failure(input_failure: Any, command_result: CommandResult) -> ReproductionAssessment:
    """Compare exit status and normalized failure evidence, without model judgment."""

    if command_result.blocked:
        return ReproductionAssessment(ReproductionStatus.blocked, command_result.blocked_reason or "command execution was blocked", actual_exit_code=command_result.exit_code)

    expected_code = _expected_exit_code(input_failure)
    actual_code = command_result.exit_code
    evidence = _failure_evidence(input_failure)
    normalized_expected = _normalize(evidence)
    normalized_actual = _normalize(f"{command_result.stdout}\n{command_result.stderr}")
    if actual_code == 0:
        return ReproductionAssessment(ReproductionStatus.not_reproduced, "command succeeded", expected_code, actual_code, normalized_expected)
    if command_result.timed_out and not _expects_timeout(input_failure):
        return ReproductionAssessment(ReproductionStatus.not_reproduced, "command timed out differently from the supplied failure", expected_code, actual_code, normalized_expected)
    if expected_code is not None and actual_code != expected_code:
        return ReproductionAssessment(ReproductionStatus.not_reproduced, "failure exit code differs", expected_code, actual_code, normalized_expected)
    if normalized_expected and not _evidence_matches(normalized_expected, normalized_actual):
        return ReproductionAssessment(ReproductionStatus.not_reproduced, "failure evidence differs", expected_code, actual_code, normalized_expected)
    if expected_code is None and not normalized_expected:
        return ReproductionAssessment(ReproductionStatus.not_reproduced, "supplied failure has no comparable signature", expected_code, actual_code, normalized_expected)
    return ReproductionAssessment(ReproductionStatus.reproduced, "failure exit code and normalized evidence match", expected_code, actual_code, normalized_expected)


def _expected_exit_code(value: Any) -> int | None:
    candidate = _get(value, "exit_code", "expected_exit_code", "returncode", "code")
    if candidate is None:
        return None
    try:
        return int(candidate)
    except (TypeError, ValueError):
        return None


def _failure_evidence(value: Any) -> str:
    if isinstance(value, str):
        return value
    pieces: list[str] = []
    for key in ("failure_text", "output", "stderr", "stdout", "message", "error", "signature"):
        candidate = _get(value, key)
        if isinstance(candidate, str) and candidate:
            pieces.append(candidate)
    evidence = _get(value, "evidence")
    if isinstance(evidence, str):
        pieces.append(evidence)
    elif isinstance(evidence, (list, tuple)):
        pieces.extend(str(item) for item in evidence)
    return "\n".join(pieces)


def _get(value: Any, *names: str) -> Any:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _normalize(value: str) -> str:
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    value = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b", "<TIMESTAMP>", value)
    value = re.sub(r"\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b", "<TIME>", value)
    value = re.sub(r"(?:[A-Za-z]:[\\/]|/)[^\s:'\"]+", "<PATH>", value)
    value = re.sub(r"\b(?:pid|process|worker)[=:# ]+\d+\b", lambda match: re.sub(r"\d+", "<PID>", match.group(0), count=1), value, flags=re.IGNORECASE)
    return " ".join(value.casefold().split())


def _evidence_matches(expected: str, actual: str) -> bool:
    expected_parts = [part for part in expected.split("\n") if part]
    if len(expected_parts) == 1:
        return expected_parts[0] in actual
    return all(part in actual for part in expected_parts)


def _expects_timeout(value: Any) -> bool:
    evidence = _normalize(_failure_evidence(value))
    return "timeout" in evidence or "timed out" in evidence
