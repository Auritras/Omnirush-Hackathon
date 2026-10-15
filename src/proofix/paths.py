"""Path containment helpers for repositories and run artifacts."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
from typing import Iterable


_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class PathSafetyError(ValueError):
    """Raised when a path cannot be safely contained by its configured root."""


def _resolved(path: os.PathLike[str] | str) -> Path:
    try:
        return Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise PathSafetyError(f"invalid path: {path!r}") from exc


def ensure_contained(path: os.PathLike[str] | str, root: os.PathLike[str] | str) -> Path:
    """Resolve *path* and require it to remain below (or equal to) *root*.

    Resolution happens before the containment comparison so existing symlinks and
    ``..`` components cannot be used to escape the controlled directory.
    """

    resolved_root = _resolved(root)
    resolved_path = _resolved(path)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise PathSafetyError(f"path is outside its allowed root: {path!r}") from exc
    return resolved_path


def resolve_repository(
    path: os.PathLike[str] | str,
    allowed_roots: Iterable[os.PathLike[str] | str] | os.PathLike[str] | str,
) -> Path:
    """Resolve an existing repository and require it under an allowed root."""

    resolved = _resolved(path)
    if not resolved.exists() or not resolved.is_dir():
        raise PathSafetyError(f"repository does not exist or is not a directory: {path!r}")
    roots: Iterable[os.PathLike[str] | str]
    if isinstance(allowed_roots, (str, bytes, os.PathLike)):
        roots = (allowed_roots,)
    else:
        roots = allowed_roots
    for root in roots:
        try:
            resolved.relative_to(_resolved(root))
            return resolved
        except ValueError:
            continue
    raise PathSafetyError(f"repository is outside the allowed roots: {path!r}")


def safe_artifact_path(run_root: os.PathLike[str] | str, relative_path: str) -> Path:
    """Return an artifact path safely rooted below ``run_root``.

    Artifact references are portable POSIX-style relative paths.  Windows and
    UNC spellings are rejected explicitly even when this code runs on POSIX.
    """

    if not isinstance(relative_path, str) or not relative_path or "\x00" in relative_path:
        raise PathSafetyError("artifact path must be a non-empty relative string")
    if relative_path.startswith(("/", "\\")) or "\\" in relative_path or _WINDOWS_DRIVE.match(relative_path):
        raise PathSafetyError("artifact path must be a portable relative path")
    parts = PurePosixPath(relative_path).parts
    if not parts or parts == (".",) or any(part in ("", ".", "..") for part in parts):
        raise PathSafetyError("artifact path contains an unsafe traversal")
    candidate = _resolved(Path(run_root) / Path(*parts))
    return ensure_contained(candidate, run_root)
