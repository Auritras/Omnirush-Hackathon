"""Patch validation and the explicit human approval gate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any, Iterable

from .models import PatchProposal
from .paths import ensure_contained


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class PatchAction:
    run_id: str
    action: str
    actor: str
    timestamp: str
    patch_digest: str | None = None

    @property
    def state(self) -> str:
        return "approved" if self.action == "approved" else "rejected"

    def to_dict(self) -> dict[str, str | None]:
        return {
            "run_id": self.run_id,
            "action": self.action,
            "actor": self.actor,
            "timestamp": self.timestamp,
            "patch_digest": self.patch_digest,
        }


def validate_unified_diff(
    diff: str,
    repository_root: os.PathLike[str] | str,
    scope: Iterable[str] | None = None,
) -> PatchProposal:
    """Parse a normal unified diff without changing ``repository_root``."""

    if not isinstance(diff, str) or not diff.strip() or "\x00" in diff:
        raise ValueError("patch diff is empty or malformed")
    root = Path(repository_root).expanduser().resolve(strict=False)
    if not root.exists() or not root.is_dir():
        raise ValueError("repository root must be an existing directory")
    files = _parse_diff(diff)
    allowed = {_normalize_path(item) for item in scope} if scope is not None else None
    if allowed is not None and not set(files).issubset(allowed):
        raise ValueError("patch changes files outside the approved scope")
    for relative in files:
        candidate = ensure_contained(root / relative, root)
        if candidate == root:
            raise ValueError("patch path is outside repository scope")
    digest = "sha256:" + hashlib.sha256(diff.encode("utf-8")).hexdigest()
    proposal = PatchProposal(
        digest=digest,
        diff_ref=f"patches/{digest.removeprefix('sha256:')}.diff",
        files=files,
        risk_summary="Validated unified diff; application remains approval-gated.",
        verification_plan=["original failure", "regression test", "relevant tests", "diff/syntax/scope"],
    )
    # PatchProposal is intentionally the portable model used by the store.  The
    # raw diff is retained only in memory for the apply/verify gate.
    proposal.diff = diff  # type: ignore[attr-defined]
    proposal.repository_root = root  # type: ignore[attr-defined]
    return proposal


def approve_patch(
    run_id: str,
    patch_digest: str,
    actor: str,
    timestamp: str,
    *,
    proposal: PatchProposal | None = None,
    current_digest: str | None = None,
) -> PatchAction:
    _validate_action_inputs(run_id, patch_digest, actor, timestamp)
    if proposal is not None:
        _validate_proposal(proposal, current_digest)
        if proposal.digest != patch_digest:
            raise ValueError("patch digest does not match the current proposal")
        if isinstance(getattr(proposal, "approval", None), dict) and proposal.approval.get("action") in {"approved", "rejected"}:  # type: ignore[attr-defined]
            raise ValueError("patch approval state is already final")
    elif current_digest is not None and patch_digest != current_digest:
        raise ValueError("patch digest does not match the current proposal")
    action = PatchAction(run_id, "approved", actor, timestamp, patch_digest)
    if proposal is not None:
        proposal.approval = action.to_dict()  # type: ignore[attr-defined]
    return action


def reject_patch(
    run_id: str,
    actor: str,
    timestamp: str,
    digest: str | None = None,
    *,
    proposal: PatchProposal | None = None,
    current_digest: str | None = None,
) -> PatchAction:
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run ID is required")
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("approval actor is required")
    _validate_timestamp(timestamp)
    if proposal is not None:
        _validate_proposal(proposal, current_digest)
        if digest is not None and digest != proposal.digest:
            raise ValueError("patch digest does not match the current proposal")
        digest = proposal.digest
        if isinstance(getattr(proposal, "approval", None), dict) and proposal.approval.get("action") in {"approved", "rejected"}:  # type: ignore[attr-defined]
            raise ValueError("patch approval state is already final")
    elif current_digest is not None and digest != current_digest:
        raise ValueError("patch digest does not match the current proposal")
    patch_digest = digest
    action = PatchAction(run_id, "rejected", actor, timestamp, patch_digest)
    if proposal is not None:
        proposal.approval = action.to_dict()  # type: ignore[attr-defined]
    return action


def request_another_diagnosis(run_id: str, actor: str, timestamp: str, digest: str | None = None) -> PatchAction:
    """Record a diagnosis request without touching any repository workspace."""
    if not isinstance(run_id, str) or not run_id.strip() or not isinstance(actor, str) or not actor.strip():
        raise ValueError("run ID and actor are required")
    _validate_timestamp(timestamp)
    return PatchAction(run_id, "request_another_diagnosis", actor, timestamp, digest)


def is_patch_approved(patch: Any, run_id: str | None = None) -> bool:
    digest = getattr(patch, "digest", None) or _digest_for_text(patch)
    if not digest:
        return False
    approval = getattr(patch, "approval", None)
    if not isinstance(approval, dict) or approval.get("action") != "approved" or approval.get("patch_digest") != digest:
        return False
    if run_id is not None:
        return approval.get("run_id") == run_id
    return True


def apply_approved_patch(patch: Any, workspace: os.PathLike[str] | str, run_id: str | None = None) -> None:
    """Apply an approved patch only inside the supplied disposable workspace."""
    if not is_patch_approved(patch, run_id):
        raise ValueError("patch has not been explicitly approved")
    diff = getattr(patch, "diff", None)
    if not isinstance(diff, str):
        raise ValueError("patch text is unavailable")
    root = Path(workspace).expanduser().resolve(strict=False)
    if not root.exists() or not root.is_dir():
        raise ValueError("verification workspace does not exist")
    source_root = getattr(patch, "repository_root", None)
    if source_root is not None and root == Path(source_root).expanduser().resolve(strict=False):
        raise ValueError("approved patches may only be applied to a disposable workspace")
    for relative in getattr(patch, "files", []):
        ensure_contained(root / relative, root)
    try:
        subprocess.run(["git", "apply", "--check", "--", "-"], cwd=root, input=diff, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        subprocess.run(["git", "apply", "--", "-"], cwd=root, input=diff, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("approved patch could not be applied") from exc


def _parse_diff(diff: str) -> list[str]:
    lines = diff.splitlines()
    files: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("diff --git "):
            raise ValueError("malformed unified diff header")
        header = line[len("diff --git "):].split()
        if len(header) != 2:
            raise ValueError("malformed diff paths")
        old = _normalize_path(header[0], allow_dev_null=True)
        new = _normalize_path(header[1], allow_dev_null=True)
        index += 1
        while index < len(lines) and not lines[index].startswith(("--- ", "+++ ")):
            metadata = lines[index]
            if metadata.startswith(("index ", "old mode ", "new mode ", "new file mode ", "deleted file mode ", "similarity index ", "dissimilarity index ", "rename from ", "rename to ")):
                index += 1
                continue
            if metadata.startswith("Binary files "):
                raise ValueError("binary patches are not supported")
            break
        if index + 1 >= len(lines) or not lines[index].startswith("--- ") or not lines[index + 1].startswith("+++ "):
            raise ValueError("unified diff is missing file headers")
        old_header = _normalize_path(lines[index][4:].split("\t", 1)[0], allow_dev_null=True)
        new_header = _normalize_path(lines[index + 1][4:].split("\t", 1)[0], allow_dev_null=True)
        if old != old_header or new != new_header or old == new == "/dev/null":
            raise ValueError("diff file headers do not match diff metadata")
        relative = new if new != "/dev/null" else old
        if relative == "/dev/null" or relative.startswith(".git/") or relative == ".git" or relative.startswith(".proofix"):
            raise ValueError("patch changes a protected or out-of-scope path")
        if relative not in files:
            files.append(relative)
        index += 2
        hunk_count = 0
        while index < len(lines) and not lines[index].startswith("diff --git "):
            hunk = _HUNK.match(lines[index])
            if not hunk:
                raise ValueError("malformed unified diff hunk")
            old_count = int(hunk.group(2) or "1")
            new_count = int(hunk.group(4) or "1")
            index += 1
            seen_old = seen_new = 0
            while index < len(lines) and not lines[index].startswith(("@@ ", "diff --git ")):
                content = lines[index]
                if content == r"\ No newline at end of file":
                    index += 1
                    continue
                if not content or content[0] not in " +-":
                    raise ValueError("malformed unified diff hunk line")
                if content[0] in " -":
                    seen_old += 1
                if content[0] in " +":
                    seen_new += 1
                index += 1
            if seen_old != old_count or seen_new != new_count:
                raise ValueError("unified diff hunk line counts do not match")
            hunk_count += 1
        if hunk_count == 0:
            raise ValueError("unified diff contains no hunks")
    if not files:
        raise ValueError("unified diff contains no files")
    return files


def _normalize_path(value: str, allow_dev_null: bool = False) -> str:
    value = value.strip()
    if allow_dev_null and value == "/dev/null":
        return value
    if not value or value.startswith(('/', '\\')) or re.match(r"^[A-Za-z]:[\\/]", value) or "\\" in value:
        raise ValueError("patch paths must be relative portable paths")
    if value.startswith("a/") or value.startswith("b/"):
        value = value[2:]
    path = PurePosixPath(value)
    if not value or value == "." or ".." in path.parts or any(part in {"", "."} for part in path.parts):
        raise ValueError("patch path contains traversal")
    return path.as_posix()


def _validate_action_inputs(run_id: str, digest: str, actor: str, timestamp: str) -> None:
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run ID is required")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("patch digest is invalid")
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("approval actor is required")
    _validate_timestamp(timestamp)


def _validate_timestamp(value: str) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp must be UTC ISO-8601")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("timestamp must be UTC ISO-8601") from exc
    if parsed.tzinfo != timezone.utc:
        raise ValueError("timestamp must be UTC ISO-8601")


def _digest_for_text(patch: Any) -> str | None:
    diff = getattr(patch, "diff", None)
    return "sha256:" + hashlib.sha256(diff.encode()).hexdigest() if isinstance(diff, str) else None


def _validate_proposal(proposal: PatchProposal, current_digest: str | None) -> None:
    diff = getattr(proposal, "diff", None)
    if not isinstance(diff, str):
        raise ValueError("current proposal does not contain patch text")
    calculated = "sha256:" + hashlib.sha256(diff.encode("utf-8")).hexdigest()
    if calculated != proposal.digest or (current_digest is not None and calculated != current_digest):
        raise ValueError("current proposal digest is invalid or stale")
