"""Disposable repository snapshots kept below a run's controlled directory."""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
from typing import Any
from uuid import uuid4

from .paths import ensure_contained


_SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".secret"}
_SECRET_NAMES = {".env", ".env.local", ".env.production", ".env.development", "credentials", "credentials.json", "secrets.json"}


def _is_secret_name(name: str) -> bool:
    lowered = name.casefold()
    if lowered in _SECRET_NAMES:
        return True
    if lowered.startswith(".env.") and lowered not in {".env.example", ".env.sample", ".env.template"}:
        return True
    if lowered.endswith(tuple(_SECRET_SUFFIXES)):
        return True
    if lowered.startswith("id_rsa") or lowered.startswith("id_ed25519"):
        return True
    return lowered in {".aws", ".ssh"}


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name == ".git" or _is_secret_name(name):
            ignored.add(name)
    return ignored


def _remove_unsafe_symlinks(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if not path.is_symlink():
            continue
        try:
            path.resolve(strict=False).relative_to(root.resolve())
        except ValueError:
            path.unlink(missing_ok=True)


def _remove_secret_files(root: Path) -> None:
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        for name in list(names):
            if _is_secret_name(name):
                path = directory_path / name
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
                names.remove(name)
        for name in files:
            if _is_secret_name(name):
                (directory_path / name).unlink(missing_ok=True)


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Runtime handle and portable metadata for one disposable snapshot."""

    path: Path
    repository: Path
    snapshot_mode: str
    commit: str | None
    materialization: str = "copy"
    manager_token: object | None = field(default=None, repr=False, compare=False)

    @property
    def mode(self) -> str:
        return self.snapshot_mode

    @property
    def workspace(self) -> Path:
        return self.path

    @property
    def relative_path(self) -> str:
        return self.path.name if self.path.parent.name == "workspaces" else self.path.name

    def to_dict(self) -> dict[str, Any]:
        """Serialize only portable snapshot metadata, never machine paths."""

        return {
            "path": f"workspaces/{self.path.name}",
            "repository": self.repository.name,
            "snapshot_mode": self.snapshot_mode,
            "materialization": self.materialization,
            "commit": self.commit,
        }


class WorkspaceManager:
    """Create and remove snapshots whose lifecycle is owned by this manager."""

    def __init__(self, run_root: os.PathLike[str] | str):
        self.run_root = Path(run_root).expanduser().resolve(strict=False)
        self.run_root.mkdir(parents=True, exist_ok=True)
        self._workspaces_root = self.run_root / "workspaces"
        self._workspaces_root.mkdir(parents=True, exist_ok=True)
        self._token = object()

    def snapshot_repository(
        self,
        repository: os.PathLike[str] | str,
        *,
        snapshot_mode: str = "clean",
    ) -> WorkspaceSnapshot:
        """Create a clean committed or explicitly dirty disposable workspace."""

        source = Path(repository).expanduser().resolve(strict=False)
        if not source.exists() or not source.is_dir():
            raise ValueError("repository must be an existing directory")
        try:
            self.run_root.relative_to(source)
        except ValueError:
            pass
        else:
            raise ValueError("run root may not be inside the source repository")

        normalized_mode = {"dirty": "include_dirty", "working_tree": "include_dirty"}.get(snapshot_mode, snapshot_mode)
        if normalized_mode not in {"clean", "include_dirty"}:
            raise ValueError("snapshot_mode must be 'clean' or 'include_dirty'")
        git_root = self._git_root(source)
        commit = self._git_output(git_root, "rev-parse", "HEAD")
        destination = self._new_destination()
        materialization = "copy"
        try:
            if normalized_mode == "include_dirty":
                self._copy_working_tree(git_root, destination)
                materialization = "copy"
            else:
                try:
                    self._git(git_root, "worktree", "add", "--detach", str(destination), commit)
                    materialization = "git-worktree"
                except (FileNotFoundError, subprocess.CalledProcessError):
                    if destination.exists():
                        shutil.rmtree(destination, ignore_errors=True)
                    self._archive_committed(git_root, commit, destination)
                    materialization = "git-archive"
            _remove_secret_files(destination)
            _remove_unsafe_symlinks(destination)
        except Exception:
            self._discard_path(destination)
            raise
        return WorkspaceSnapshot(
            path=destination,
            repository=git_root,
            snapshot_mode=normalized_mode,
            commit=commit,
            materialization=materialization,
            manager_token=self._token,
        )

    def cleanup(self, snapshot: WorkspaceSnapshot) -> None:
        """Remove only a live snapshot created by this manager."""

        if not isinstance(snapshot, WorkspaceSnapshot) or snapshot.manager_token is not self._token:
            raise ValueError("snapshot is not owned by this workspace manager")
        path = ensure_contained(snapshot.path, self._workspaces_root)
        if path == self._workspaces_root or path.parent != self._workspaces_root:
            raise ValueError("snapshot path is not a managed workspace")
        if snapshot.materialization == "git-worktree":
            try:
                self._git(snapshot.repository, "worktree", "remove", "--force", str(path))
            except (FileNotFoundError, subprocess.CalledProcessError):
                pass
        if path.exists() or path.is_symlink():
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)

    def _new_destination(self) -> Path:
        return self._workspaces_root / f"snapshot-{uuid4().hex}"

    @staticmethod
    def _git_output(repository: Path, *args: str) -> str:
        try:
            return subprocess.check_output(["git", *args], cwd=repository, text=True, stderr=subprocess.PIPE).strip()
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise ValueError("repository must be a Git repository with a commit") from exc

    @staticmethod
    def _git(repository: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=repository, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def _git_root(self, repository: Path) -> Path:
        root = Path(self._git_output(repository, "rev-parse", "--show-toplevel")).resolve()
        try:
            root.relative_to(self.run_root)
        except ValueError:
            return root
        raise ValueError("run root may not be inside the source repository")

    @staticmethod
    def _copy_working_tree(source: Path, destination: Path) -> None:
        shutil.copytree(source, destination, ignore=_copy_ignore, symlinks=True)

    @staticmethod
    def _archive_committed(source: Path, commit: str, destination: Path) -> None:
        result = subprocess.run(
            ["git", "archive", "--format=tar", commit],
            cwd=source,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        destination.mkdir(parents=True)
        with tarfile.open(fileobj=BytesIO(result.stdout), mode="r:") as archive:
            members = []
            for member in archive.getmembers():
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise ValueError("Git archive contains an unsafe path")
                members.append(member)
            archive.extractall(destination, members=members)

    def _discard_path(self, path: Path) -> None:
        try:
            resolved = path.resolve(strict=False)
            resolved.relative_to(self._workspaces_root.resolve())
        except ValueError:
            return
        if resolved == self._workspaces_root.resolve() or resolved.parent != self._workspaces_root.resolve():
            return
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists() or path.is_symlink():
            path.unlink(missing_ok=True)
