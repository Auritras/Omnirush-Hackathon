"""Orchestrator-owned intake, reproduction, and diagnosis gates."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from .intake import IntakeRequest, sanitize_intake
from .models import DomainError, EventRecord, RunRecord
from .omnirush import OmniRushSessionClient, RecordedSessionClient, run_specialists
from .reproduction import compare_failure
from .runner import CommandPolicy, run_command
from .state import transition


class ProofFixOrchestrator:
    """Own every state transition; session responses can only add evidence."""

    def __init__(self, store: Any, workspace_manager: Any, runner: Any = run_command, session_client: OmniRushSessionClient | None = None):
        self.store = store
        self.workspace_manager = workspace_manager
        self.runner = runner
        self.session_client = session_client if session_client is not None else RecordedSessionClient()
        self._snapshots: dict[str, Any] = {}

    def start_run(self, request: IntakeRequest | Mapping[str, Any]) -> str:
        intake = request if isinstance(request, IntakeRequest) else sanitize_intake(request)
        run = self.store.create_run(intake.to_dict())
        triage = _triage(intake)
        self._append_transition(run, "triaging", "orchestrator:triage", {"stage": "triage", "triage": triage})
        run = self.store.get_run(run.run_id)
        self._append_transition(run, "reproducing", "orchestrator:reproduction_started", {"stage": "reproduction"})
        updated = self.store.get_run(run.run_id)
        self.store.update_run(replace(updated, stages=[triage]))
        return run.run_id

    def advance_reproduction(self, run_id: str) -> RunRecord:
        run = self.store.get_run(run_id)
        if run.state != "reproducing":
            raise DomainError(f"run is not ready for reproduction: {run.state}")
        intake = sanitize_intake(run.intake)
        try:
            snapshot = self.workspace_manager.snapshot_repository(intake.repository_path)
            self._snapshots[run_id] = snapshot
            result = self._run_local(intake.command, snapshot)
            assessment = compare_failure(intake.failure_text, result)
            status = assessment.status.value
            details = {
                "stage": "reproduction",
                "status": status,
                "reason": assessment.reason,
                "expected_exit_code": assessment.expected_exit_code,
                "actual_exit_code": assessment.actual_exit_code,
                "result": result.to_dict() if hasattr(result, "to_dict") else _result_dict(result),
            }
            evidence = {
                "kind": "reproduction",
                "summary": assessment.reason,
                "details": details,
            }
        except (OSError, ValueError, RuntimeError) as exc:
            status = "blocked"
            details = {"stage": "reproduction", "status": status, "reason": str(exc)}
            evidence = {"kind": "reproduction", "summary": str(exc), "details": details}
        target = status if status in {"reproduced", "not_reproduced", "blocked"} else "blocked"
        run = self.store.get_run(run_id)
        self._append_transition(run, target, "orchestrator:reproduction_result", details)
        updated = self.store.get_run(run_id)
        self.store.update_run(replace(updated, evidence=[*updated.evidence, evidence]))
        return self.store.get_run(run_id)

    def advance_diagnosis(self, run_id: str) -> RunRecord:
        run = self.store.get_run(run_id)
        if run.state != "reproduced":
            raise DomainError(f"diagnosis is gated by reproduction: {run.state}")
        if self.session_client is None:
            raise DomainError("no OmniRush session client is configured")
        context = {
            "intake": sanitize_intake(run.intake).agent_context(),
            "reproduction_evidence": list(run.evidence),
            "untrusted_context": True,
        }
        # Validate every response before appending an event or mutating state.
        hypotheses = run_specialists(context, self.session_client)
        details = {
            "stage": "diagnosis",
            "specialists": [item.to_dict() for item in hypotheses],
            "mode": getattr(self.session_client, "mode", "live"),
        }
        run = self.store.get_run(run_id)
        self._append_transition(run, "diagnosing", "orchestrator:diagnosis_started", details)
        updated = self.store.get_run(run_id)
        sessions = [item.session_id for item in hypotheses if item.session_id]
        self.store.update_run(
            replace(
                updated,
                hypotheses=[item.to_dict() for item in hypotheses],
                omnirush_sessions=[*updated.omnirush_sessions, *sessions],
            )
        )
        return self.store.get_run(run_id)

    def _run_local(self, command: str, snapshot: Any) -> Any:
        workspace = getattr(snapshot, "path", getattr(snapshot, "workspace", snapshot))
        root = Path(workspace)
        policy = CommandPolicy(workspace_root=root, artifact_dir=root / ".proofix-artifacts")
        target = self.runner
        if hasattr(target, "run_command"):
            return target.run_command(command, root, policy=policy)
        if hasattr(target, "run"):
            try:
                return target.run(command, root, policy=policy)
            except TypeError:
                return target.run(command, root)
        try:
            return target(command, root, policy=policy)
        except TypeError:
            return target(command, root)

    def _append_transition(self, run: RunRecord, target: str, kind: str, details: Mapping[str, Any]) -> None:
        updated = transition(run, target, event_kind=kind, details=details)
        event = EventRecord.new(
            run_id=run.run_id,
            kind=kind,
            stage=str(details.get("stage")) if details.get("stage") else None,
            status=str(details.get("status")) if details.get("status") else None,
            state_before=run.state,
            state_after=updated.state,
            details=dict(details),
        )
        self.store.append_event(event)


def _triage(intake: IntakeRequest) -> dict[str, Any]:
    command = intake.command.casefold()
    if any(token in command for token in ("pytest", "unittest", "test")):
        category = "test_assertion"
    elif any(token in command for token in ("build", "make", "cargo")):
        category = "build"
    else:
        category = "unknown"
    return {
        "name": "triage",
        "category": category,
        "suspected_subsystem": "command-under-investigation",
        "relevant_files": [],
        "command_assumptions": ["The command is run in a clean disposable workspace."],
        "confidence": 0.25,
        "untrusted_failure_text": True,
    }


def _result_dict(result: Any) -> dict[str, Any]:
    return {
        "exit_code": getattr(result, "exit_code", getattr(result, "returncode", None)),
        "stdout": getattr(result, "stdout", ""),
        "stderr": getattr(result, "stderr", ""),
        "blocked": bool(getattr(result, "blocked", False)),
        "blocked_reason": getattr(result, "blocked_reason", None),
    }
