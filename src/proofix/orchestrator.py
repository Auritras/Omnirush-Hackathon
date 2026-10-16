"""Orchestrator-owned intake, reproduction, and diagnosis gates."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
from typing import Any, Mapping

from .intake import IntakeRequest, sanitize_intake
from .models import DomainError, EventRecord, PatchProposal, RunRecord
from .omnirush import OmniRushSessionClient, RecordedSessionClient, run_specialists
from .redaction import redact_text
from .reproduction import compare_failure
from .runner import CommandPolicy, run_command
from .state import transition
from .experiments import ExperimentSpec, run_experiment
from .patches import approve_patch, reject_patch, request_another_diagnosis, validate_audit, validate_proposal_digest, validate_unified_diff
from .verification import VerificationPlan, verify_patch


class ProofFixOrchestrator:
    """Own every state transition; session responses can only add evidence."""

    def __init__(
        self,
        store: Any,
        workspace_manager: Any,
        runner: Any = run_command,
        session_client: OmniRushSessionClient | None = None,
        run_metadata: Mapping[str, Any] | None = None,
    ):
        self.store = store
        self.workspace_manager = workspace_manager
        self.runner = runner
        self.session_client = session_client if session_client is not None else RecordedSessionClient()
        self.run_metadata = dict(run_metadata or {})
        self._snapshots: dict[str, Any] = {}
        self._patches: dict[str, Any] = {}

    def start_run(self, request: IntakeRequest | Mapping[str, Any]) -> str:
        intake = sanitize_intake(request.to_dict() if isinstance(request, IntakeRequest) else request)
        intake_data = intake.to_dict()
        # Metadata is host-owned provenance (for example demo/recorded mode),
        # never user instructions and never a client-provided status field.
        intake_data.update(self.run_metadata)
        run = self.store.create_run(intake_data)
        triage = _triage(intake)
        self._append_transition(run, "triaging", "orchestrator:triage", {"stage": "triage", "triage": triage, **self.run_metadata})
        run = self.store.get_run(run.run_id)
        self._append_transition(run, "reproducing", "orchestrator:reproduction_started", {"stage": "reproduction", **self.run_metadata})
        updated = self.store.get_run(run.run_id)
        self.store.update_run(replace(updated, stages=[triage]))
        return run.run_id

    def advance_reproduction(self, run_id: str) -> RunRecord:
        run = self.store.get_run(run_id)
        if run.state != "reproducing":
            raise DomainError(f"run is not ready for reproduction: {run.state}")
        try:
            intake = sanitize_intake(run.intake)
            snapshot = self.workspace_manager.snapshot_repository(intake.repository_path)
            self._snapshots[run_id] = snapshot
            result = self._run_local(intake.command, snapshot)
            assessment = compare_failure(intake.failure_text, result)
            status = assessment.status.value
            result_data = result.to_dict() if hasattr(result, "to_dict") else _result_dict(result)
            artifact_refs = result_data.get("artifact_refs") if isinstance(result_data.get("artifact_refs"), Mapping) else {}
            for stream in ("stdout", "stderr"):
                ref = result_data.get(f"{stream}_artifact_ref") or result_data.get(f"{stream}_ref") or artifact_refs.get(stream)
                if ref:
                    result_data.setdefault(f"{stream}_artifact_ref", ref)
            snapshot_metadata = _snapshot_metadata(snapshot, intake)
            details = {
                "stage": "reproduction",
                "outcome": status,
                "reason": assessment.reason,
                "expected_exit_code": assessment.expected_exit_code,
                "actual_exit_code": assessment.actual_exit_code,
                "command": result_data.get("command", intake.command),
                "exit_code": result_data.get("exit_code"),
                "duration_ms": result_data.get("duration_ms"),
                "stdout_ref": result_data.get("stdout_artifact_ref") or result_data.get("stdout_ref"),
                "stderr_ref": result_data.get("stderr_artifact_ref") or result_data.get("stderr_ref"),
                "repository_snapshot": snapshot_metadata,
                "result": _without_transition_fields(result_data),
            }
            evidence = {
                "kind": "reproduction",
                "summary": assessment.reason,
                "details": {"status": status, **details, "result": result_data},
            }
        except Exception as exc:
            status = "blocked"
            reason = redact_text(str(exc))[0][:4096]
            details = {"stage": "reproduction", "outcome": status, "reason": reason}
            evidence = {"kind": "reproduction", "summary": reason, "details": {"status": status, **details}}
        target = status if status in {"reproduced", "not_reproduced", "blocked"} else "blocked"
        run = self.store.get_run(run_id)
        self._append_transition(run, target, "orchestrator:reproduction_result", details)
        updated = self.store.get_run(run_id)
        snapshot_evidence = None
        if isinstance(details.get("repository_snapshot"), Mapping):
            snapshot_evidence = {
                "kind": "repository_snapshot",
                "summary": "Disposable repository snapshot used for reproduction.",
                "details": dict(details["repository_snapshot"]),
            }
        additions = [evidence] + ([snapshot_evidence] if snapshot_evidence else [])
        self.store.update_run(replace(updated, evidence=[*updated.evidence, *additions]))
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

    def run_experiment(self, run_id: str, spec: ExperimentSpec) -> RunRecord:
        """Run one explicit experiment and return to diagnosis with evidence."""
        run = self.store.get_run(run_id)
        if run.state not in {"diagnosing", "experimenting"}:
            raise DomainError(f"experiments are gated by diagnosis: {run.state}")
        snapshot = self._snapshots.get(run_id)
        if snapshot is None:
            raise DomainError("experiments require a reproduction snapshot")
        if run.state == "diagnosing":
            self._append_transition(run, "experimenting", "orchestrator:experiment_started", {"stage": "experiment", "kind": spec.normalized_kind})
        workspace = getattr(snapshot, "path", getattr(snapshot, "workspace", snapshot))
        result = run_experiment(spec, workspace, self.runner)
        updated = self.store.get_run(run_id)
        self.store.update_run(replace(updated, experiments=[*updated.experiments, result.to_dict()]))
        current = self.store.get_run(run_id)
        if current.state == "experimenting":
            self._append_transition(current, "diagnosing", "orchestrator:experiment_result", {"stage": "experiment", "outcome": result.status, "output_digest": result.output_digest})
        return self.store.get_run(run_id)

    def propose_patch(self, run_id: str, diff: str, scope: list[str] | None = None) -> RunRecord:
        run = self.store.get_run(run_id)
        if run.state not in {"diagnosing", "experimenting"}:
            raise DomainError(f"patch proposals are gated by diagnosis: {run.state}")
        snapshot = self._snapshots.get(run_id)
        repository = getattr(snapshot, "path", getattr(snapshot, "workspace", snapshot)) if snapshot is not None else sanitize_intake(run.intake).repository_path
        proposal = validate_unified_diff(diff, repository, scope)
        details = {"stage": "patch", "outcome": "proposed", "patch_digest": proposal.digest, "files": proposal.files}
        self._append_transition(run, "patch_proposed", "orchestrator:patch_proposed", details)
        current = self.store.get_run(run_id)
        self.store.update_run(replace(current, patch={**proposal.to_dict(), "run_id": run_id, "diff": diff}))
        self._append_transition(self.store.get_run(run_id), "awaiting_approval", "orchestrator:approval_requested", {"stage": "patch", "outcome": "awaiting_approval", "patch_digest": proposal.digest})
        return self.store.get_run(run_id)

    def approve_patch(self, run_id: str, actor: str, timestamp: str, patch_digest: str | None = None) -> RunRecord:
        run = self.store.get_run(run_id)
        if run.state != "awaiting_approval" or not run.patch:
            raise DomainError(f"patch is not awaiting approval: {run.state}")
        proposal, current_digest = self._current_proposal(run, patch_digest)
        action = approve_patch(run_id, patch_digest, actor, timestamp, proposal=proposal, current_digest=current_digest)
        self._patches[run_id] = proposal
        self._append_transition(run, "applying", "orchestrator:patch_approved", {"stage": "patch", "outcome": "approved", "patch_digest": action.patch_digest, "actor": actor, "timestamp": timestamp})
        current = self.store.get_run(run_id)
        self.store.update_run(replace(current, patch={**current.patch, "approval": action.to_dict()}))
        return self.store.get_run(run_id)

    def reject_patch(self, run_id: str, actor: str, timestamp: str, patch_digest: str | None = None) -> RunRecord:
        run = self.store.get_run(run_id)
        if run.state != "awaiting_approval" or not run.patch:
            raise DomainError(f"patch is not awaiting approval: {run.state}")
        proposal, current_digest = self._current_proposal(run, patch_digest)
        action = reject_patch(run_id, actor, timestamp, digest=current_digest, proposal=proposal, current_digest=current_digest)
        current = self.store.get_run(run_id)
        self.store.update_run(replace(current, patch={**current.patch, "decision": action.to_dict()}))
        self._append_transition(self.store.get_run(run_id), "diagnosing", "orchestrator:patch_rejected", {"stage": "patch", "outcome": "rejected", "actor": actor, "timestamp": timestamp, "patch_digest": action.patch_digest})
        return self.store.get_run(run_id)

    def request_another_diagnosis(self, run_id: str, actor: str, timestamp: str, patch_digest: str | None = None) -> RunRecord:
        run = self.store.get_run(run_id)
        if run.state != "awaiting_approval" or not run.patch:
            raise DomainError(f"patch is not awaiting approval: {run.state}")
        current = self.store.get_run(run_id)
        _proposal, current_digest = self._current_proposal(current, patch_digest)
        action = request_another_diagnosis(run_id, actor, timestamp, digest=current_digest)
        decision = action.to_dict()
        self.store.update_run(replace(current, patch={**current.patch, "decision": decision}))
        self._append_transition(self.store.get_run(run_id), "diagnosing", "orchestrator:request_another_diagnosis", {"stage": "patch", "outcome": "diagnosis_requested", "actor": actor, "timestamp": timestamp, "patch_digest": decision["patch_digest"]})
        return self.store.get_run(run_id)

    def return_to_diagnosis(self, run_id: str, actor: str, timestamp: str) -> RunRecord:
        """Recover a failed/blocked verification while preserving its evidence."""
        run = self.store.get_run(run_id)
        if run.state not in {"verification_failed", "verification_blocked"}:
            raise DomainError(f"run is not recoverable from verification: {run.state}")
        validate_audit(actor, timestamp)
        patch = dict(run.patch or {})
        patch["approval"] = None
        patch["decision"] = {"action": "return_to_diagnosis", "actor": actor, "timestamp": timestamp, "patch_digest": patch.get("digest")}
        self.store.update_run(replace(run, patch=patch))
        self._append_transition(self.store.get_run(run_id), "diagnosing", "orchestrator:return_to_diagnosis", {"stage": "verification", "outcome": "diagnosis_requested", "actor": actor, "timestamp": timestamp})
        return self.store.get_run(run_id)

    def verify_patch(self, run_id: str, plan: VerificationPlan) -> RunRecord:
        run = self.store.get_run(run_id)
        if run.state != "applying":
            raise DomainError(f"patch is not approved for verification: {run.state}")
        try:
            # Verification must be bound to the durable proposal that is
            # current after approval; an in-memory proposal may be stale.
            proposal = self._proposal_from_run(run)
        except DomainError as exc:
            self._append_transition(run, "verifying", "orchestrator:verification_started", {"stage": "verification", "outcome": "integrity_check"})
            blocked = {"status": "verification_blocked", "checks": [{"name": "patch_integrity", "status": "blocked", "details": {"error": str(exc)[:4096]}}]}
            current = self.store.get_run(run_id)
            self.store.update_run(replace(current, verification=blocked))
            self._append_transition(self.store.get_run(run_id), "verification_blocked", "orchestrator:verification_result", {"stage": "verification", "outcome": "verification_blocked", "failed_check": "patch_integrity"})
            return self.store.get_run(run_id)
        snapshot = self._snapshots.get(run_id)
        if snapshot is None:
            snapshot = self.workspace_manager.snapshot_repository(sanitize_intake(run.intake).repository_path)
            self._snapshots[run_id] = snapshot
        if proposal is None or snapshot is None:
            raise DomainError("approved patch or reproduction snapshot is unavailable")
        self._patches[run_id] = proposal
        self._append_transition(run, "verifying", "orchestrator:verification_started", {"stage": "verification", "patch_digest": proposal.digest})
        source = getattr(snapshot, "path", getattr(snapshot, "repository", snapshot))
        summary = verify_patch(proposal, source, self.runner, plan, run_id=run_id)
        current = self.store.get_run(run_id)
        self.store.update_run(replace(current, verification=summary.to_dict()))
        target = {"verified": "verified", "verification_failed": "verification_failed", "verification_blocked": "verification_blocked"}[summary.status]
        failed = summary.failed_check
        self._append_transition(self.store.get_run(run_id), target, "orchestrator:verification_result", {"stage": "verification", "outcome": summary.status, "failed_check": failed.name if failed else None})
        return self.store.get_run(run_id)

    def _proposal_from_run(self, run: RunRecord) -> PatchProposal | None:
        if not isinstance(run.patch, Mapping) or not run.patch.get("digest") or not isinstance(run.patch.get("diff"), str):
            return None
        data = {key: value for key, value in run.patch.items() if key in {"schema_version", "digest", "diff_ref", "files", "regression_test", "risk_summary", "verification_plan"}}
        data.setdefault("schema_version", "1.0")
        try:
            proposal = PatchProposal.from_dict(data)
            proposal.diff = run.patch["diff"]  # type: ignore[attr-defined]
            proposal.run_id = run.run_id  # type: ignore[attr-defined]
            validate_proposal_digest(proposal, str(run.patch.get("digest")))
        except Exception as exc:
            raise DomainError("persisted patch proposal digest is invalid") from exc
        if run.patch.get("approval"):
            proposal.approval = run.patch["approval"]  # type: ignore[attr-defined]
        elif isinstance(run.patch.get("decision"), Mapping):
            proposal.approval = run.patch["decision"]  # type: ignore[attr-defined]
        return proposal

    def _current_proposal(self, run: RunRecord, patch_digest: str | None) -> tuple[PatchProposal, str]:
        """Validate an explicit action digest against the durable proposal."""
        if not isinstance(patch_digest, str) or not patch_digest:
            raise DomainError("explicit current patch digest is required")
        proposal = self._proposal_from_run(run)
        if proposal is None:
            raise DomainError("patch proposal is unavailable")
        current_digest = str((run.patch or {}).get("digest"))
        if (run.patch or {}).get("run_id") != run.run_id:
            raise DomainError("patch proposal is bound to a different run")
        if patch_digest != current_digest or patch_digest != proposal.digest:
            raise DomainError("patch digest does not match the persisted current proposal")
        return proposal, current_digest

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
        result = details.get("result") if isinstance(details.get("result"), Mapping) else {}
        command = details.get("command", result.get("command"))
        if isinstance(command, (list, tuple)):
            command = " ".join(str(item) for item in command)
        exit_code = details.get("exit_code", result.get("exit_code"))
        duration_ms = details.get("duration_ms", result.get("duration_ms"))
        stdout_ref = details.get("stdout_ref", result.get("stdout_artifact_ref", result.get("stdout_ref")))
        stderr_ref = details.get("stderr_ref", result.get("stderr_artifact_ref", result.get("stderr_ref")))
        event = EventRecord.new(
            run_id=run.run_id,
            kind=kind,
            stage=str(details.get("stage")) if details.get("stage") else None,
            status=str(details.get("outcome")) if details.get("outcome") else None,
            command=command if isinstance(command, str) else None,
            exit_code=exit_code if isinstance(exit_code, int) and not isinstance(exit_code, bool) else None,
            stdout_ref=stdout_ref if isinstance(stdout_ref, str) else None,
            stderr_ref=stderr_ref if isinstance(stderr_ref, str) else None,
            state_before=run.state,
            state_after=updated.state,
            details={**dict(details), **({"duration_ms": duration_ms} if duration_ms is not None else {})},
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


def _snapshot_metadata(snapshot: Any, intake: IntakeRequest) -> dict[str, Any]:
    """Return portable snapshot provenance for persistence and report export."""
    metadata = snapshot.to_dict() if hasattr(snapshot, "to_dict") else {}
    if not isinstance(metadata, dict):
        metadata = {}
    repository = getattr(snapshot, "repository", None)
    if repository is not None:
        repository_path = Path(repository)
        metadata.setdefault("repository", repository_path.name)
        metadata["branch"] = intake.branch or _git_metadata(repository_path, "branch", "--show-current")
        remote = _git_metadata(repository_path, "remote", "get-url", "origin")
        metadata["remote"] = redact_text(remote)[0] if remote else None
    metadata.setdefault("commit", getattr(snapshot, "commit", None))
    metadata.setdefault("snapshot_mode", getattr(snapshot, "snapshot_mode", getattr(snapshot, "mode", None)))
    metadata.setdefault("materialization", getattr(snapshot, "materialization", None))
    return metadata


def _git_metadata(repository: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=repository, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
        )
    except OSError:
        return None
    value = result.stdout.strip()
    return value or None


def _without_transition_fields(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _without_transition_fields(item) for key, item in value.items() if key not in {"state", "status", "target_state", "next_state", "state_after"}}
    if isinstance(value, list):
        return [_without_transition_fields(item) for item in value]
    return value
