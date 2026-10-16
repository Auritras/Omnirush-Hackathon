"""Deterministic verification of an approved patch in a disposable copy."""

from __future__ import annotations

from dataclasses import dataclass, field
import ast
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import math
from typing import Any, Sequence

from .experiments import validate_guarded_command
from .models import CheckResult
from .patches import apply_approved_patch, is_patch_approved
from .runner import CommandPolicy, run_command


@dataclass(frozen=True)
class VerificationPlan:
    original_failure_command: str | Sequence[str]
    regression_test: str | Sequence[str]
    relevant_tests: list[str | Sequence[str]] = field(default_factory=list)
    broader_suite: str | Sequence[str] | None = None
    timeout_seconds: float = 30.0
    scope_files: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.timeout_seconds, (int, float)) or isinstance(self.timeout_seconds, bool) or not math.isfinite(float(self.timeout_seconds)) or not 0 < float(self.timeout_seconds) <= 300:
            raise ValueError("verification timeout must be finite, positive, and no greater than 300 seconds")
        if not _command_text(self.regression_test):
            raise ValueError("an explicit regression command is required")

    @property
    def original_command(self) -> str | Sequence[str]:
        return self.original_failure_command


@dataclass
class VerificationSummary:
    status: str
    checks: list[CheckResult] = field(default_factory=list)
    workspace: str | None = None

    @property
    def state(self) -> str:
        return self.status

    @property
    def failed_check(self) -> CheckResult | None:
        return next((check for check in self.checks if check.status in {"failed", "blocked"}), None)

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [check for check in self.checks if check.status in {"failed", "blocked"}]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": [check.to_dict() for check in self.checks],
            "workspace": self.workspace,
            "failed_check": self.failed_check.to_dict() if self.failed_check else None,
        }



def _command_text(command: str | Sequence[str] | None) -> str:
    if isinstance(command, str):
        return command.strip()
    if isinstance(command, Sequence) and not isinstance(command, (bytes, bytearray)):
        return " ".join(str(item) for item in command).strip()
    return ""

def verify_patch(
    patch: Any,
    workspace: os.PathLike[str] | str,
    runner: Any = run_command,
    plan: VerificationPlan | None = None,
    run_id: str | None = None,
    run_root: os.PathLike[str] | str | None = None,
) -> VerificationSummary:
    """Apply an approved patch to a fresh copy and execute checks in order."""

    if not isinstance(plan, VerificationPlan):
        raise TypeError("verification plan is required")
    source = Path(workspace).expanduser().resolve(strict=False)
    if not source.exists() or not source.is_dir():
        return VerificationSummary("verification_blocked", [_blocked_check("patch_application", "workspace does not exist")])
    if not is_patch_approved(patch, run_id=run_id):
        return VerificationSummary("verification_blocked", [_blocked_check("patch_application", "patch has not been explicitly approved")])

    created_controlled_root = run_root is None
    controlled_root = Path(run_root).expanduser().resolve(strict=False) if run_root is not None else (source.parent / ".proofix-verification")
    if not controlled_root.exists():
        controlled_root.mkdir(parents=True, exist_ok=True)
    try:
        temporary_root = Path(tempfile.mkdtemp(prefix="verification-", dir=str(controlled_root)))
    except Exception as exc:
        return VerificationSummary("verification_blocked", [_blocked_check("patch_application", _bounded_error(exc))])
    verification_workspace = temporary_root / "workspace"
    try:
        try:
            shutil.copytree(source, verification_workspace, symlinks=False, ignore=shutil.ignore_patterns(".proofix-artifacts"))
        except Exception as exc:
            return VerificationSummary("verification_blocked", [_blocked_check("patch_application", _bounded_error(exc))])
        try:
            apply_approved_patch(patch, verification_workspace, run_id=run_id)
        except Exception as exc:
            return VerificationSummary("verification_blocked", [_blocked_check("patch_application", _bounded_error(exc))])

        checks: list[CheckResult] = []
        command_checks = [
            ("original_failure", plan.original_failure_command),
            ("regression_test", plan.regression_test),
        ]
        command_checks.extend((f"relevant_test_{index}", command) for index, command in enumerate(plan.relevant_tests, 1))
        if plan.broader_suite is not None:
            command_checks.append(("broader_suite", plan.broader_suite))
        for name, command in command_checks:
            check = _run_check(name, command, verification_workspace, runner, plan.timeout_seconds)
            checks.append(check)
            if check.status != "passed":
                return VerificationSummary("verification_blocked" if check.status == "blocked" else "verification_failed", checks)

        try:
            final_check = _diff_scope_syntax_check(patch, verification_workspace, plan)
        except Exception as exc:
            final_check = _blocked_check("diff_scope_syntax", _bounded_error(exc), "git diff --check")
        checks.append(final_check)
        return VerificationSummary("verified" if final_check.status == "passed" else "verification_blocked" if final_check.status == "blocked" else "verification_failed", checks)
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
        if created_controlled_root:
            shutil.rmtree(controlled_root, ignore_errors=True)


def _run_check(name: str, command: str | Sequence[str], workspace: Path, runner: Any, timeout: float) -> CheckResult:
    try:
        validate_guarded_command(command, workspace, "verification")
    except Exception as exc:
        return _blocked_check(name, _bounded_error(exc), command)
    policy = CommandPolicy(workspace_root=workspace, artifact_dir=workspace / ".proofix-artifacts", timeout_seconds=timeout)
    target = getattr(runner, "run_command", runner)
    try:
        try:
            result = target(command, workspace, policy=policy)
        except TypeError:
            result = target(command, workspace)
    except Exception as exc:
        return _blocked_check(name, _bounded_error(exc), command)
    exit_code = getattr(result, "exit_code", getattr(result, "returncode", None))
    if bool(getattr(result, "blocked", False)) or bool(getattr(result, "timed_out", getattr(result, "timeout", False))):
        status = "blocked"
    else:
        status = "passed" if exit_code == 0 else "failed"
    return CheckResult(
        name=name,
        status=status,
        command=_display_command(command),
        exit_code=exit_code,
        duration_ms=getattr(result, "duration_ms", None),
        stdout_ref=getattr(result, "stdout_artifact_ref", None),
        stderr_ref=getattr(result, "stderr_artifact_ref", None),
        details={
            "stdout": str(getattr(result, "stdout", "")),
            "stderr": str(getattr(result, "stderr", "")),
            "blocked_reason": getattr(result, "blocked_reason", None),
            "timed_out": bool(getattr(result, "timed_out", getattr(result, "timeout", False))),
        },
    )


def _diff_scope_syntax_check(patch: Any, workspace: Path, plan: VerificationPlan) -> CheckResult:
    details: dict[str, Any] = {}
    try:
        diff_result = subprocess.run(["git", "diff", "--check"], cwd=workspace, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except Exception as exc:
        return CheckResult(name="diff_scope_syntax", status="blocked", command="git diff --check", details={"error": _bounded_error(exc)})
    if diff_result.returncode != 0:
        return CheckResult(name="diff_scope_syntax", status="failed", command="git diff --check", exit_code=diff_result.returncode, details={"stderr": diff_result.stderr, "stdout": diff_result.stdout})

    expected = set(getattr(patch, "files", []))
    actual_result = subprocess.run(["git", "diff", "--name-only"], cwd=workspace, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    actual = {line.strip() for line in actual_result.stdout.splitlines() if line.strip()}
    status_result = subprocess.run(["git", "status", "--porcelain"], cwd=workspace, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    for line in status_result.stdout.splitlines():
        if len(line) >= 4:
            relative = line[3:].strip().strip('"')
            if relative and not relative.startswith(".proofix-artifacts"):
                actual.add(relative)
    scope = set(plan.scope_files) if plan.scope_files else expected
    changed = actual & expected if expected else actual
    if actual_result.returncode != 0 or (scope and not actual.issubset(scope)) or (expected and not expected.issubset(actual)):
        return CheckResult(name="diff_scope_syntax", status="failed", command="git diff --name-only", exit_code=actual_result.returncode or 1, details={"changed_files": sorted(actual), "scope": sorted(scope), "stderr": actual_result.stderr})

    syntax_files = sorted(expected or actual)
    for relative in syntax_files:
        if relative.endswith(".py"):
            path = workspace / relative
            if path.exists():
                try:
                    ast.parse(path.read_text(encoding="utf-8"), filename=relative)
                except (OSError, SyntaxError) as exc:
                    return CheckResult(name="diff_scope_syntax", status="failed", command="python syntax check", exit_code=1, details={"file": relative, "error": str(exc)})
    details.update({"changed_files": sorted(actual), "scope": sorted(scope), "syntax": "passed"})
    return CheckResult(name="diff_scope_syntax", status="passed", command="git diff --check; syntax", exit_code=0, details=details)


def _blocked_check(name: str, reason: str, command: str | Sequence[str] | None = None) -> CheckResult:
    return CheckResult(name=name, status="blocked", command=_display_command(command) if command is not None else None, details={"blocked_reason": reason, "error": reason})


def _display_command(command: str | Sequence[str]) -> str:
    return command if isinstance(command, str) else " ".join(str(item) for item in command)


def _bounded_error(error: BaseException) -> str:
    return str(error).replace("\x00", "")[:4096] or error.__class__.__name__
