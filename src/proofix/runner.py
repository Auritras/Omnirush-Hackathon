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
import selectors
import stat
import subprocess
import time
from typing import Mapping, Sequence

from .paths import ensure_contained
from .redaction import redact_environment, redact_text


_SHELL_OPERATORS = set(";&|><`$(){}\n\r")
_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
_SHELL_BASENAME = re.compile(r"(?:^|[-_.])(?:ba|da|z|k|c|tc)?sh(?:[-_.]?\d+(?:\.\d+)*)?$|(?:^|[-_.])fish(?:[-_.]?\d+(?:\.\d+)*)?$", re.IGNORECASE)
_INTERPRETER_BASENAME = re.compile(
    r"(?:^|[-_.])(?:python|pypy|node|nodejs|ruby|perl|php|lua|deno)(?:[-_.]?\d+(?:\.\d+)*)?$",
    re.IGNORECASE,
)
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
    interpreter_commands: frozenset[str] | set[str] | tuple[str, ...] = ()
    shell_commands: frozenset[str] | set[str] | tuple[str, ...] = ()
    timeout_seconds: float = 30.0
    max_inline_output: int = 4096
    max_output_bytes: int = 1_048_576
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
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        object.__setattr__(self, "allowed_commands", commands)
        object.__setattr__(self, "interpreter_commands", frozenset(str(item).casefold() for item in self.interpreter_commands))
        object.__setattr__(self, "shell_commands", frozenset(str(item).casefold() for item in self.shell_commands))
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
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    _stdout_artifact_path: Path | None = field(default=None, repr=False, compare=False)
    _stderr_artifact_path: Path | None = field(default=None, repr=False, compare=False)

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

    def artifact_text(self, stream: str) -> str:
        """Read one full redacted artifact for deterministic comparison."""
        path = self._stdout_artifact_path if stream == "stdout" else self._stderr_artifact_path if stream == "stderr" else None
        if path is None:
            return self.stdout if stream == "stdout" else self.stderr if stream == "stderr" else ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return self.stdout if stream == "stdout" else self.stderr

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
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
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
        _reject_command_string_forms(executable_name, argv[1:], policy)
        _validate_argument_paths(argv[1:], workspace, root)
        _validate_required_inputs(workspace, root, policy.required_inputs)
        child_environment = _child_environment(environment, policy)
    except ValueError as exc:
        return _blocked(argv, workspace, str(exc), metadata, started, policy)
    if executable_path is None:
        return _blocked(argv, workspace, "required runtime or executable is unavailable", metadata, started, policy)
    argv[0] = executable_path

    artifact_directory = Path(policy.artifact_dir).expanduser() if policy.artifact_dir else workspace / ".proofix-artifacts"
    if not artifact_directory.is_absolute():
        artifact_directory = workspace / artifact_directory
    artifact_directory = artifact_directory.absolute()
    try:
        ensure_contained(artifact_directory, root)
        _ensure_artifact_directory(artifact_directory, root)
    except (OSError, ValueError) as exc:
        return _blocked(display_argv, workspace, f"artifact directory is unavailable: {exc}", metadata, started, policy)

    try:
        process = subprocess.Popen(
            argv,
            cwd=workspace,
            env=child_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
        stdout_raw, stderr_raw, stdout_truncated, stderr_truncated, timed_out = _capture_process(
            process, timeout_seconds=policy.timeout_seconds, max_output_bytes=policy.max_output_bytes
        )
        exit_code = process.returncode
    except (FileNotFoundError, PermissionError):
        return _blocked(display_argv, workspace, "required runtime or executable is unavailable", metadata, started, policy)
    except OSError as exc:
        return _blocked(display_argv, workspace, f"command could not start: {exc}", metadata, started, policy)

    stdout = _redact_output(stdout_raw.decode("utf-8", errors="replace"), environment or {})
    stderr = _redact_output(stderr_raw.decode("utf-8", errors="replace"), environment or {})
    if stdout_truncated:
        stdout += f"\n[ProofFix stdout truncated after {policy.max_output_bytes} bytes]\n"
    if stderr_truncated:
        stderr += f"\n[ProofFix stderr truncated after {policy.max_output_bytes} bytes]\n"
    try:
        stdout_digest, stdout_ref, stdout_path = _write_artifact(artifact_directory, root, "stdout", stdout)
        stderr_digest, stderr_ref, stderr_path = _write_artifact(artifact_directory, root, "stderr", stderr)
    except (OSError, ValueError) as exc:
        return _blocked(display_argv, workspace, f"artifact write was blocked: {exc}", metadata, started, policy)
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
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        _stdout_artifact_path=stdout_path,
        _stderr_artifact_path=stderr_path,
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
    if any(character in _SHELL_OPERATORS for item in argv for character in item):
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


def _reject_command_string_forms(executable: str, arguments: Sequence[str], policy: CommandPolicy) -> None:
    """Reject source/shell-string flags for classified interpreters and shells."""
    name = executable.casefold()
    is_shell = name in policy.shell_commands or bool(_SHELL_BASENAME.search(name))
    is_interpreter = name in policy.interpreter_commands or bool(_INTERPRETER_BASENAME.search(name))
    if not (is_shell or is_interpreter):
        return
    flags = {"-c", "--command"}
    if is_interpreter:
        flags.update({"--eval", "-e", "--execute", "--print"})
    for argument in arguments:
        if argument in flags:
            raise ValueError("command-string execution is not allowed")
        if argument == "-":
            raise ValueError("stdin command execution is not allowed")
        if is_shell and _is_bundled_shell_command_flag(argument):
            raise ValueError("command-string execution is not allowed")
        if argument.startswith(("-c", "--command=")) or (is_interpreter and argument.startswith(("-e", "--eval=", "--execute=", "--print="))):
            raise ValueError("command-string execution is not allowed")


def _is_bundled_shell_command_flag(argument: str) -> bool:
    """Recognize ``sh -ec``/``bash -xec`` without treating tool flags alike."""
    return argument.startswith("-") and not argument.startswith("--") and "c" in argument[1:]


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
    for argument in arguments:
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
        "PATH": os.environ.get("PATH") or os.defpath,
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


def _ensure_artifact_directory(directory: Path, root: Path) -> None:
    ensure_contained(directory, root)
    _reject_symlink_components(directory, root)
    relative = directory.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        try:
            current.mkdir()
        except FileExistsError:
            pass
        item = current.lstat()
        if stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode):
            raise ValueError("artifact directory contains an unsafe component")
        _reject_symlink_components(current, root)


def _reject_symlink_components(path: Path, root: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError("artifact path is outside the disposable root") from exc
    current = root
    for part in relative.parts:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise ValueError("artifact path contains a symlink component")
        except FileNotFoundError:
            continue


def _write_artifact(directory: Path, root: Path, kind: str, content: str) -> tuple[str, str, Path]:
    encoded = content.encode("utf-8")
    digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
    path = directory / f"{kind}-{hashlib.sha256(encoded).hexdigest()}.log"
    ensure_contained(path, root)
    _reject_symlink_components(path, root)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        _reject_symlink_components(path, root)
        if not path.is_file() or path.read_bytes() != encoded:
            raise ValueError("artifact path already exists with different content")
    else:
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
        except Exception:
            path.unlink(missing_ok=True)
            raise
    return digest, path.relative_to(root).as_posix(), path


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


def _capture_process(
    process: subprocess.Popen[bytes], *, timeout_seconds: float, max_output_bytes: int
) -> tuple[bytes, bytes, bool, bool, bool]:
    """Drain both pipes without retaining more than the explicit per-stream cap."""
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    streams = {process.stdout: "stdout", process.stderr: "stderr"}
    for stream, name in streams.items():
        if stream is None:
            continue
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    # Very short limits still need a bounded interpreter-startup window;
    # otherwise a child process can be killed before it records deterministic
    # evidence in a disposable workspace.
    deadline = time.monotonic() + max(timeout_seconds, 0.5)
    timed_out = False
    termination_deadline: float | None = None
    try:
        while True:
            now = time.monotonic()
            if not timed_out and now >= deadline:
                timed_out = True
                _terminate_process_group(process)
                termination_deadline = time.monotonic() + 0.5

            registered = selector.get_map()
            if not registered:
                if process.poll() is not None:
                    break
                if timed_out:
                    _reap_process_bounded(process)
                    break
                time.sleep(min(0.05, max(0.0, deadline - now)))
                continue

            if timed_out and termination_deadline is not None and now >= termination_deadline:
                _reap_process_bounded(process)
                break
            wait_for = 0.05 if timed_out else max(0.0, min(0.05, deadline - now))
            events = selector.select(wait_for)
            if not events:
                continue
            for key, _ in events:
                stream = key.fileobj
                name = key.data
                try:
                    chunk = os.read(stream.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                remaining = max_output_bytes - len(buffers[name])
                if remaining > 0:
                    buffers[name].extend(chunk[:remaining])
                if len(chunk) > max(0, remaining):
                    truncated[name] = True
        if process.poll() is None:
            _reap_process_bounded(process)
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
    return bytes(buffers["stdout"]), bytes(buffers["stderr"]), truncated["stdout"], truncated["stderr"], timed_out


def _reap_process_bounded(process: subprocess.Popen[bytes], timeout: float = 0.5) -> None:
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if os.name != "posix":
        try:
            process.terminate()
        except ProcessLookupError:
            return
        grace_deadline = time.monotonic() + 0.5
        while time.monotonic() < grace_deadline:
            time.sleep(0.01)
        try:
            process.kill()
        except ProcessLookupError:
            pass
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (AttributeError, ProcessLookupError, PermissionError):
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    grace_deadline = time.monotonic() + 0.5
    while time.monotonic() < grace_deadline:
        time.sleep(0.01)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (AttributeError, ProcessLookupError, PermissionError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
