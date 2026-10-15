"""The orchestrator-owned ProofFix run state graph."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .models import DomainError, RunRecord, utc_timestamp


ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"triaging"}),
    "triaging": frozenset({"reproducing"}),
    "reproducing": frozenset({"not_reproduced", "blocked", "reproduced"}),
    "not_reproduced": frozenset(),
    "blocked": frozenset(),
    "reproduced": frozenset({"diagnosing"}),
    "diagnosing": frozenset({"experimenting", "patch_proposed"}),
    "experimenting": frozenset({"diagnosing", "patch_proposed"}),
    "patch_proposed": frozenset({"awaiting_approval"}),
    "awaiting_approval": frozenset({"applying"}),
    "applying": frozenset({"verifying"}),
    "verifying": frozenset({"verification_failed", "verified", "verification_blocked"}),
    "verification_failed": frozenset(),
    "verified": frozenset({"github_pending_approval"}),
    "verification_blocked": frozenset(),
    "github_pending_approval": frozenset({"github_complete"}),
    "github_complete": frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    return current in ALLOWED_TRANSITIONS and target in ALLOWED_TRANSITIONS and target in ALLOWED_TRANSITIONS[current]


def transition(run: RunRecord, target: str, *, event_kind: str, details: Mapping[str, Any]) -> RunRecord:
    if not isinstance(run, RunRecord):
        raise DomainError("transition requires a RunRecord")
    if not isinstance(details, Mapping):
        raise DomainError("transition details must be a mapping")
    if not event_kind or not isinstance(event_kind, str):
        raise DomainError("transition event kind is required")
    owner = event_kind.lower()
    if "agent" in owner or "browser" in owner:
        raise DomainError("agents and browsers cannot own run state transitions")
    if not (owner.startswith("orchestrator:") or owner.startswith("orchestrator_") or owner.startswith("orchestrator.")):
        raise DomainError("only orchestrator-owned events may change run state")
    forbidden = {"state", "status", "target_state", "next_state", "state_after"}
    if forbidden.intersection(details):
        raise DomainError("agent-supplied status changes are not accepted")
    if run.state not in ALLOWED_TRANSITIONS or target not in ALLOWED_TRANSITIONS:
        raise DomainError(f"unknown run state: {run.state if run.state not in ALLOWED_TRANSITIONS else target}")
    if not can_transition(run.state, target):
        raise DomainError(f"invalid transition: {run.state} -> {target}")
    return replace(run, state=target, updated_at=utc_timestamp())
