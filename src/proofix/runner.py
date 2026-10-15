"""A small, deterministic command runner for disposable ProofFix workspaces."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import time
from typing import Mapping, Sequence

from .paths import ensure_contained
from .redaction import redact_environment, redact_text


_SHELL_OPERATORS = set(";&|><`\n\r")
_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
_DEFAULT_COMMANDS = frozenset(
    {
        "bash",
        "cargo",
        "dotnet",
        "echo",
        "false",
        "go",
        "java",
        "make",
        "node",
        "npm",
        "python",
        "python3",
        "pytest",
        "ruby",
        "sh",
        "sleep",
        "true",
    }
)


@dataclass(frozen=True)
class CommandPolicy:
    """Explicit limits for one command invocation.

    ``workspace_root`` is optional for compatibility with callers that already
    pass a disposable directory as ``cwd``.  When supplied, both the cwd and
    artifact directory must remain below it.
    """

    allowed_commands: frozenset[str] | set[str] | tuple[str, ...] = field(default_factory=lambda: _DEFAULT_COMMANDS)
    timeout_seconds: float = 30.0
    max_inline_output: int = 4096
    workspace_root: os.PathLike[str] | str | None = None
    artifact_dir: os.PathLike[str] | str | None = None
    required_inputs: tuple[str, ...] = ()
    environment_allowlist: frozenset[str] | set[str] | tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        commands = frozenset(str(item) for item in self.allowed_commands)
        if not commands or any(not item or "/" in item or "\\" in item for item in commands):
            raise ValueError("allowed_commands must contain executable names")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_inline_output <= 0:
            raise ValueError("max_inline_output must be positive")
        object.__setattr__(self, "allowed_commands", commands)
        object.__setattr__(self, "required_inputs", tuple(str(item) for item in self.required_inputs))
        if self.environment_allowlist is not None:
            object.__setattr__(self, "environment_allowlist", frozenset(str(item) for item in self.environment_allowlist))


@dataclass
class CommandResult:
    """Redacted, serializable evidence from a single local command attempt."""

    command: list[str]
    cwd: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    blocked: bool = False
    blocked_reason: str | None = None
    status: str = "completed"
    environment_metadata: dict[str, object] = field(default_factory=dict)
    stdout_artifact_ref: str | None = None
    stderr_artifact_ref: str | None = None
    stdout_digest: str | None = None
    stderr_digest: str | None = None

    @property
    def timeout(self) -> bool:
        return self.timed_out

    @property
    def returncode(self) -> int | None:
        return self.exit_code

    @property
    def duration(self) -> float:
        return self.duration_ms / 1000.0

    @property
    def artifact_refs(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "stdout": self.stdout_artifact_ref,
                "stderr": self.stderr_artifact_ref,
            }.items()
            if value is not None
        }

    @property
    def stdout_ref(self) -> str | None:
        return self.stdout_artifact_ref

    @property
    def stderr_ref(self) -> str | None:
        return self.stderr_artifact_ref

    def to_dict(self) -> dict[str, object]:
        return {
            "command": list(self.command),
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "status": self.status,
            "environment_metadata": dict(self.environment_metadata),
            "artifact_refs": self.artifact_refs,
            "stdout_digest": self.stdout_digest,
            "stderr_digest": self.stderr_digest,
        }


def run_command(
    command: str | Sequence[str],
    cwd: os.PathLike[str] | str,
    *,
    policy: CommandPolicy,
    environment: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run one policy-approved command without invoking a shell."""

    started = time.monotonic()
    root = Path(policy.workspace_root or cwd).expanduser().resolve(strict=False)
    workspace = Path(cwd).expanduser().resolve(strict=False)
    metadata = _environment_metadata(environment or {})
    if not workspace.exists() or not workspace.is_dir():
        return _blocked([], workspace, "workspace does not exist", metadata, started, policy)
    try:
        ensure_contained(workspace, root)
    except ValueError:
        return _blocked([], workspace, "workspace is outside the disposable root", metadata, started, policy)

    try:
        argv = _parse_command(command)
    except ValueError as exc:
        return _blocked([], workspace, str(exc), metadata, started, policy)
    if not argv:
        return _blocked(argv, workspace, "command is empty", metadata, started, policy)
    display_argv = list(argv)

    executable = argv[0]
    executable_name = Path(executable).name
    if executable_name not in policy.allowed_commands:
        return _blocked(argv, workspace, "command is not allowlisted", metadata, started, policy)
    try:
        executable_path = _resolve_executable(executable, executable_name, workspace, root)
        _validate_argument_paths(argv[1:], workspace, root)
        _validate_required_inputs(workspace, root, policy.required_inputs)
        child_environment = _child_environment(environment, policy)
    except ValueError as exc:
        return _blocked(argv, workspace, str(exc), metadata, started, policy)
    if executable_path is None:
        return _blocked(argv, workspace, "required runtime or executable is unavailable", metadata, started, policy)
    argv[0] = executable_path

    artifact_directory = Path(policy.artifact_dir).expanduser().resolve(strict=False) if policy.artifact_dir else workspace / ".proofix-artifacts"
    try:
        ensure_contained(artifact_directory, root)
        artifact_directory.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        return _blocked(argv, workspace, f"artifact directory is unavailable: {exc}", metadata, started, policy)

    stdout_raw = b""
    stderr_raw = b""
    timed_out = False
    exit_code: int | None = None
    try:
        process = subprocess.Popen(
            argv,
            cwd=workspace,
            env=child_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout_raw, stderr_raw = process.communicate(timeout=policy.timeout_seconds)
            exit_code = process.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout_raw = exc.output or b""
            stderr_raw = exc.stderr or b""
            _terminate_process_group(process)
            remaining_stdout, remaining_stderr = process.communicate()
            stdout_raw += remaining_stdout or b""
            stderr_raw += remaining_stderr or b""
            exit_code = process.returncode
    except (FileNotFoundError, PermissionError):
        return _blocked(argv, workspace, "required runtime or executable is unavailable", metadata, started, policy)
    except OSError as exc:
        return _blocked(argv, workspace, f"command could not start: {exc}", metadata, started, policy)

    stdout = _redact_output(stdout_raw.decode("utf-8", errors="replace"), environment or {})
    stderr = _redact_output(stderr_raw.decode("utf-8", errors="replace"), environment or {})
    stdout_digest, stdout_ref = _write_artifact(artifact_directory, "stdout", stdout)
    stderr_digest, stderr_ref = _write_artifact(artifact_directory, "stderr", stderr)
    status = "timed_out" if timed_out else "completed"
    return CommandResult(
        command=_display_command(display_argv),
        cwd=_portable_path(workspace, root),
        exit_code=exit_code,
        stdout=stdout[: policy.max_inline_output],
        stderr=stderr[: policy.max_inline_output],
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        timed_out=timed_out,
        status=status,
        environment_metadata=metadata,
        stdout_artifact_ref=stdout_ref,
        stderr_artifact_ref=stderr_ref,
        stdout_digest=stdout_digest,
        stderr_digest=stderr_digest,
    )


def _parse_command(command: str | Sequence[str]) -> list[str]:
    if isinstance(command, str):
        if "\x00" in command:
            raise ValueError("command contains a null byte")
        _reject_unquoted_shell_operators(command)
        try:
            argv = shlex.split(command, posix=True)
        except ValueError as exc:
            raise ValueError("command has invalid shell quoting") from exc
    elif isinstance(command, Sequence) and not isinstance(command, (bytes, bytearray)):
        argv = list(command)
    else:
        raise ValueError("command must be text or an argument sequence")
    if any(not isinstance(item, str) or not item or "\x00" in item for item in argv):
        raise ValueError("command arguments must be non-empty text")
    if any(token in _SHELL_OPERATORS for item in argv for token in item if item != argv[0]):
        # Sequence input is already shell-free, but shell control tokens are
        # still rejected as an explicit policy boundary.
        for item in argv[1:]:
            if item in {";", "|", "||", "&", "&&", ">", ">>", "<", "2>", "`"}:
                raise ValueError("shell metacharacters are not allowed")
    return argv


def _reject_unquoted_shell_operators(command: str) -> None:
    quote: str | None = None
    escaped = False
    for character in command:
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote != "'":
            escaped = True
            continue
        if character in {"'", '"'}:
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
            continue
        if quote is None and character in _SHELL_OPERATORS:
            raise ValueError("shell metacharacters are not allowed")


def _resolve_executable(executable: str, name: str, workspace: Path, root: Path) -> str | None:
    if _WINDOWS_ABSOLUTE.match(executable) or Path(executable).is_absolute():
        raise ValueError("absolute executable paths are not allowed")
    if "/" in executable or "\\" in executable:
        candidate = (workspace / executable).resolve(strict=False)
        ensure_contained(candidate, root)
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            return None
        return str(candidate)
    return shutil.which(name, path=os.defpath)


def _validate_argument_paths(arguments: Sequence[str], workspace: Path, root: Path) -> None:
    skip_next = False
    for argument in arguments:
        if skip_next:
            skip_next = False
            continue
        if argument in {"-c", "--command", "-e", "--eval"}:
            skip_next = True
            continue
        if _WINDOWS_ABSOLUTE.match(argument) or Path(argument).is_absolute():
            raise ValueError("absolute command paths are not allowed")
        parts = Path(argument.replace("\\", "/")).parts
        if ".." in parts:
            raise ValueError("command path escapes the disposable workspace")
        if "/" in argument or "\\" in argument:
            candidate = (workspace / argument).resolve(strict=False)
            ensure_contained(candidate, root)


def _validate_required_inputs(workspace: Path, root: Path, required: Sequence[str]) -> None:
    for relative in required:
        if _WINDOWS_ABSOLUTE.match(relative) or Path(relative).is_absolute():
            raise ValueError("required input must be relative")
        candidate = (workspace / relative).resolve(strict=False)
        ensure_contained(candidate, root)
        if not candidate.exists():
            raise ValueError(f"required input is missing: {relative}")


def _child_environment(environment: Mapping[str, str] | None, policy: CommandPolicy) -> dict[str, str]:
    result = {
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "LC_ALL": "C",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name, value in (environment or {}).items():
        name_text = str(name)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name_text) or "\x00" in name_text:
            raise ValueError("environment names must be portable identifiers")
        if policy.environment_allowlist is not None and name_text not in policy.environment_allowlist:
            raise ValueError(f"environment variable is not allowlisted: {name_text}")
        value_text = str(value)
        if "\x00" in value_text:
            raise ValueError("environment values may not contain null bytes")
        result[name_text] = value_text
    return result


def _environment_metadata(environment: Mapping[str, str]) -> dict[str, object]:
    names = sorted({"PATH", "LANG", "LC_ALL", "PYTHONIOENCODING", "PYTHONDONTWRITEBYTECODE", *(str(key) for key in environment)})
    return {"names": names, "count": len(names)}


def _redact_output(text: str, environment: Mapping[str, str]) -> str:
    result, _ = redact_text(text)
    redacted_environment = redact_environment(environment)
    for name, value in environment.items():
        if redacted_environment.get(str(name)) == "[REDACTED]" and value:
            result = result.replace(str(value), "[REDACTED]")
    return result


def _write_artifact(directory: Path, kind: str, content: str) -> tuple[str, str]:
    encoded = content.encode("utf-8")
    digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
    path = directory / f"{kind}-{hashlib.sha256(encoded).hexdigest()}.log"
    path.write_bytes(encoded)
    return digest, path.relative_to(directory.parent).as_posix()


def _display_command(argv: Sequence[str]) -> list[str]:
    return [redact_text(item)[0] for item in argv]


def _portable_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix() or "."
    except ValueError:
        return "."


def _blocked(argv: list[str], workspace: Path, reason: str, metadata: dict[str, object], started: float, policy: CommandPolicy) -> CommandResult:
    return CommandResult(
        command=_display_command(argv),
        cwd=_portable_path(workspace, Path(policy.workspace_root or workspace).resolve(strict=False)),
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        blocked=True,
        blocked_reason=reason,
        status="blocked",
        environment_metadata=metadata,
    )


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + 0.5
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
    except (AttributeError, ProcessLookupError, PermissionError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
