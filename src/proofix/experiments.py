"""Bounded, explicit hypothesis experiments.

Experiments deliberately accept a small vocabulary instead of an agent supplied
program.  The command runner remains the final command-policy boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import tarfile
import tempfile
from typing import Any, Mapping, Sequence

from .paths import ensure_contained
from .runner import CommandPolicy, run_command


EXPERIMENT_KINDS = frozenset(
    {
        "named_test_or_build",
        "controlled_environment",
        "temporary_instrumentation",
        "parent_commit_comparison",
        "dependency_runtime_inspection",
        "proposed_regression_test",
    }
)
_ALIASES = {
    "named_test": "named_test_or_build",
    "named_build": "named_test_or_build",
    "named_command": "named_test_or_build",
    "environment_change": "controlled_environment",
    "env_change": "controlled_environment",
    "controlled_env": "controlled_environment",
    "instrumentation": "temporary_instrumentation",
    "parent_commit": "parent_commit_comparison",
    "dependency_inspection": "dependency_runtime_inspection",
    "dependency_runtime": "dependency_runtime_inspection",
    "runtime_inspection": "dependency_runtime_inspection",
        "regression_test": "proposed_regression_test",
}
_APPROVED_EXECUTABLES = frozenset({"cargo", "dotnet", "go", "make", "mvn", "npm", "pytest", "python", "python3"})
_APPROVED_BUILD_TARGETS = frozenset({"all", "build", "check", "lint", "test", "tests", "verify"})


@dataclass(frozen=True)
class ExperimentSpec:
    kind: str
    command: str | Sequence[str]
    hypothesis_id: str | None = None
    hypothesis: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    env_change: Mapping[str, str] = field(default_factory=dict)
    env_name: str | None = None
    env_value: str | None = None
    instrumentation: Mapping[str, str] = field(default_factory=dict)
    parent_commit: str | None = None
    regression_test: str | Sequence[str] | None = None
    timeout_seconds: float = 30.0

    @property
    def normalized_kind(self) -> str:
        kind = self.kind.casefold().strip() if isinstance(self.kind, str) else ""
        return _ALIASES.get(kind, kind)

    @property
    def controlled_environment(self) -> dict[str, str]:
        values = dict(self.environment)
        values.update(self.env_change)
        if self.env_name is not None:
            values[self.env_name] = "" if self.env_value is None else self.env_value
        return values


@dataclass
class ExperimentResult:
    kind: str
    hypothesis_id: str | None
    hypothesis: str | None
    command: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    output_digest: str
    changed_files: list[str]
    timed_out: bool
    timeout_seconds: float
    artifacts: dict[str, str]
    status: str = "completed"
    parent_commit: str | None = None
    parent_exit_code: int | None = None
    comparison: dict[str, Any] = field(default_factory=dict)

    @property
    def timeout(self) -> bool:
        return self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "hypothesis_id": self.hypothesis_id,
            "hypothesis": self.hypothesis,
            "command": list(self.command),
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "output_digest": self.output_digest,
            "changed_files": list(self.changed_files),
            "timed_out": self.timed_out,
            "timeout_seconds": self.timeout_seconds,
            "artifacts": dict(self.artifacts),
            "status": self.status,
            "parent_commit": self.parent_commit,
            "parent_exit_code": self.parent_exit_code,
            "comparison": dict(self.comparison),
        }


def run_experiment(spec: ExperimentSpec, workspace: os.PathLike[str] | str, runner: Any = run_command) -> ExperimentResult:
    """Run one supported experiment below ``workspace`` and record evidence."""

    if not isinstance(spec, ExperimentSpec):
        raise TypeError("experiment spec is required")
    kind = spec.normalized_kind
    if kind not in EXPERIMENT_KINDS:
        raise ValueError("experiment kind is not in the explicit ProofFix vocabulary")
    if spec.timeout_seconds <= 0:
        raise ValueError("experiment timeout must be positive")
    if kind == "controlled_environment" and len(spec.controlled_environment) != 1:
        raise ValueError("controlled environment experiments must change exactly one variable")
    root = Path(workspace).expanduser().resolve(strict=False)
    if not root.exists() or not root.is_dir():
        raise ValueError("experiment workspace must be an existing directory")

    command_value: str | Sequence[str] = spec.command
    if kind == "proposed_regression_test":
        if spec.regression_test is None:
            raise ValueError("proposed regression experiments require an explicit regression test")
        command_value = spec.regression_test
    command = validate_guarded_command(command_value, root, kind)
    if kind == "parent_commit_comparison" and not spec.parent_commit:
        raise ValueError("parent-commit comparison requires a parent commit")

    before = _snapshot(root)
    touched: set[str] = set()
    temporary_paths: list[Path] = []
    try:
        if kind == "temporary_instrumentation":
            for relative, content in spec.instrumentation.items():
                path = _safe_relative(root, relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists():
                    raise ValueError("temporary instrumentation may only create new files")
                path.write_text(str(content), encoding="utf-8")
                temporary_paths.append(path)
                touched.add(path.relative_to(root).as_posix())
        environment = spec.controlled_environment if kind == "controlled_environment" else {}
        result = _invoke_runner(runner, command, root, spec.timeout_seconds, environment)
    finally:
        for path in reversed(temporary_paths):
            path.unlink(missing_ok=True)

    after = _snapshot(root)
    changed = sorted(set(before).symmetric_difference(after) | {name for name in before.keys() & after.keys() if before[name] != after[name]} | touched)
    stdout = str(getattr(result, "stdout", ""))
    stderr = str(getattr(result, "stderr", ""))
    output_digest = "sha256:" + hashlib.sha256((stdout + stderr).encode("utf-8")).hexdigest()
    artifacts = dict(getattr(result, "artifact_refs", getattr(result, "artifacts", {})) or {})
    parent_result = None
    if kind == "parent_commit_comparison":
        parent_result = _run_against_parent(spec.parent_commit or "", command, root, runner, spec.timeout_seconds)
    comparison = {}
    if parent_result is not None:
        comparison = {
            "current_exit_code": getattr(result, "exit_code", getattr(result, "returncode", None)),
            "current_output_digest": output_digest,
            "parent_exit_code": getattr(parent_result, "exit_code", getattr(parent_result, "returncode", None)),
            "parent_output_digest": _result_digest(parent_result),
        }
    return ExperimentResult(
        kind=kind,
        hypothesis_id=spec.hypothesis_id,
        hypothesis=spec.hypothesis,
        command=[str(item) for item in getattr(result, "command", command)],
        exit_code=getattr(result, "exit_code", getattr(result, "returncode", None)),
        stdout=stdout,
        stderr=stderr,
        output_digest=output_digest,
        changed_files=changed,
        timed_out=bool(getattr(result, "timed_out", getattr(result, "timeout", False))),
        timeout_seconds=spec.timeout_seconds,
        artifacts=artifacts,
        status=str(getattr(result, "status", "completed")),
        parent_commit=spec.parent_commit,
        parent_exit_code=getattr(parent_result, "exit_code", getattr(parent_result, "returncode", None)) if parent_result is not None else None,
        comparison=comparison,
    )


def _safe_argv(command: str | Sequence[str]) -> list[str]:
    if isinstance(command, str):
        if not command.strip() or any(token in command for token in (";", "|", "&", ">", "<", "`", "\n", "\r", "$", "\x00")):
            raise ValueError("experiment command contains unsafe shell syntax")
        try:
            argv = shlex.split(command, posix=True)
        except ValueError as exc:
            raise ValueError("experiment command has invalid quoting") from exc
    else:
        argv = list(command)
    if not argv or any(not isinstance(item, str) or not item or "\x00" in item for item in argv):
        raise ValueError("experiment command must contain non-empty text")
    if any(item in {"-c", "--command", "--eval", "-e", "--execute", "--print"} or item.startswith(("-c", "--command=", "--eval=", "--execute=", "--print=")) for item in argv[1:]):
        raise ValueError("arbitrary command execution is not an experiment")
    return argv


def validate_guarded_command(command: str | Sequence[str], workspace: os.PathLike[str] | str, kind: str = "named_test_or_build") -> list[str]:
    """Validate the explicit executable and repository target vocabulary.

    In particular, a Python script path is never an accepted experiment or
    verification target. Tests are named modules below ``tests``/``test``;
    build tools use a small fixed target vocabulary.
    """
    argv = _safe_argv(command)
    executable = Path(argv[0]).name.casefold()
    if executable not in _APPROVED_EXECUTABLES or "/" in argv[0] or "\\" in argv[0]:
        raise ValueError("executable is not in the ProofFix command vocabulary")
    args = argv[1:]
    if executable in {"python", "python3"}:
        if "-m" in args:
            index = args.index("-m")
            if index + 1 >= len(args):
                raise ValueError("Python module target is missing")
            module = args[index + 1]
            target_modules = args[index + 2:] if module == "unittest" else [module]
            allowed_module = module.startswith(("tests.", "test.")) or (module == "unittest" and bool(target_modules) and all(item.startswith(("tests.", "test.")) for item in target_modules))
            if kind == "dependency_runtime_inspection" and args == ["-m", "pip", "--version"]:
                allowed_module = True
            if not allowed_module or any("/" in item or "\\" in item for item in target_modules):
                raise ValueError("Python module target is outside the approved test/runtime vocabulary")
        elif kind != "dependency_runtime_inspection" or not any(item in {"--version", "-V"} for item in args):
            raise ValueError("Python script targets are not supported; use a named test module")
    elif executable == "pytest":
        targets = [item for item in args if not item.startswith("-")]
        if not targets or any(not _allowed_repository_target(item) for item in targets):
            raise ValueError("pytest targets must be repository-relative tests")
    elif executable == "make":
        targets = [item for item in args if not item.startswith("-")]
        if any(item not in _APPROVED_BUILD_TARGETS for item in targets):
            raise ValueError("make target is not approved")
    elif executable in {"npm", "cargo", "go", "dotnet", "mvn"}:
        if not args or not any(item in _APPROVED_BUILD_TARGETS | {"run", "check", "test"} for item in args):
            raise ValueError("build target is not approved")
        if any("/" in item or "\\" in item or ".." in Path(item).parts for item in args):
            raise ValueError("build target path is not contained")
    if kind == "dependency_runtime_inspection":
        if executable not in {"python", "python3"}:
            raise ValueError("dependency/runtime inspection executable is not supported")
        if args not in (["--version"], ["-V"], ["-m", "pip", "--version"]):
            raise ValueError("dependency/runtime inspection is limited to read-only version checks")
    return argv


def _allowed_repository_target(value: str) -> bool:
    path = Path(value.replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts and path.parts and path.parts[0] in {"test", "tests"}


def _safe_relative(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative.replace("\\", "/")).parts:
        raise ValueError("instrumentation path must be relative and contained")
    return ensure_contained(root / relative, root)


def _snapshot(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or ".proofix-artifacts" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        try:
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            result[relative] = "<unreadable>"
    return result


def _invoke_runner(runner: Any, command: list[str], workspace: Path, timeout: float, environment: Mapping[str, str]) -> Any:
    policy = CommandPolicy(workspace_root=workspace, artifact_dir=workspace / ".proofix-artifacts", timeout_seconds=timeout)
    target = getattr(runner, "run_command", runner)
    try:
        return target(command, workspace, policy=policy, environment=environment)
    except TypeError:
        try:
            return target(command, workspace, policy=policy)
        except TypeError:
            return target(command, workspace)


def _run_against_parent(parent_commit: str, command: list[str], workspace: Path, runner: Any, timeout: float) -> Any:
    try:
        subprocess.run(["git", "rev-parse", "--verify", f"{parent_commit}^{{commit}}"], cwd=workspace, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        with tempfile.TemporaryDirectory(prefix="proofix-parent-") as directory:
            parent_root = Path(directory) / "workspace"
            archive = subprocess.run(["git", "archive", "--format=tar", parent_commit], cwd=workspace, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            parent_root.mkdir()
            with tarfile.open(fileobj=__import__("io").BytesIO(archive.stdout), mode="r:") as handle:
                members = []
                for member in handle.getmembers():
                    path = Path(member.name)
                    if path.is_absolute() or ".." in path.parts:
                        raise ValueError("parent archive contains an unsafe path")
                    members.append(member)
                handle.extractall(parent_root, members=members)
            return _invoke_runner(runner, command, parent_root, timeout, {})
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise ValueError(f"parent-commit comparison is unavailable: {exc}") from exc


def _result_digest(result: Any) -> str:
    return "sha256:" + hashlib.sha256((str(getattr(result, "stdout", "")) + str(getattr(result, "stderr", ""))).encode("utf-8")).hexdigest()
