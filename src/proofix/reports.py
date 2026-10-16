"""Auditable, redacted report exports derived from run and event records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import os
import re
from typing import Any

from .redaction import redact_text


REPORT_SCHEMA_VERSION = "1.0"
_INLINE_LOG_LIMIT = 1024
_SENSITIVE_KEY = re.compile(
    r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key|api[_-]?token|access[_-]?(?:key|token)|client[_-]?secret|credential|auth)"
)
_LOG_KEY = re.compile(r"(?i)(?:stdout|stderr|log|output|failure_text|traceback|stack)" )


def render_markdown(run: Any, events: Sequence[Any], artifacts: Mapping[str, Any] | None = None) -> str:
    """Render an evidence report without trusting UI-derived state.

    ``run`` and ``events`` may be the portable model records or ordinary JSON
    mappings. Artifact bodies are intentionally never embedded; only bounded
    summaries and relative references are displayed.
    """

    payload = _build_payload(run, events, artifacts or {})
    lines = [
        "# ProofFix evidence report",
        "",
        f"- **Run ID:** `{payload['run_id']}`",
        f"- **Schema version:** `{payload['schema_version']}`",
        f"- **Result provenance:** **{payload['result_label']}**",
        f"- **Terminal status:** `{payload['terminal_status']}`",
        f"- **Verification:** **{'VERIFIED' if payload['verified'] else 'UNVERIFIED'}**",
        "",
        "## Repository snapshot",
        "",
    ]
    snapshot = payload["repository_snapshot"]
    if snapshot:
        for key in ("path", "repository", "remote", "branch", "commit", "commit_sha", "snapshot_mode", "materialization"):
            if snapshot.get(key) not in (None, ""):
                lines.append(f"- **{key.replace('_', ' ').title()}:** `{_md(snapshot[key])}`")
    else:
        lines.append("- Snapshot unavailable.")

    intake = payload.get("input", {})
    if isinstance(intake, Mapping) and intake.get("failure_text"):
        lines.extend(["", "## Intake failure summary", "", f"- {_md(intake['failure_text'])}"])

    lines.extend(["", "## Commands and events", ""])
    if payload["commands"]:
        for command in payload["commands"]:
            text = f"- `{_md(command.get('command', ''))}` — exit code `{command.get('exit_code')}`"
            if command.get("duration_ms") is not None:
                text += f", duration `{command['duration_ms']} ms`"
            refs = [command.get("stdout_ref"), command.get("stderr_ref"), command.get("command_ref")]
            refs = [ref for ref in refs if ref]
            if refs:
                text += ", artifacts: " + ", ".join(f"`{_md(ref)}`" for ref in refs)
            lines.append(text)
    else:
        lines.append("- No command events recorded.")

    lines.extend(["", "## Hypotheses and evidence", ""])
    if payload["hypotheses"]:
        for index, hypothesis in enumerate(payload["hypotheses"], 1):
            lines.append(f"### Hypothesis {index}: {_md(hypothesis.get('hypothesis', ''))}")
            if hypothesis.get("confidence") is not None:
                lines.append(f"- Confidence: `{hypothesis['confidence']}`")
            lines.append("- Supporting evidence:")
            _append_evidence(lines, hypothesis.get("supporting_evidence", []))
            lines.append("- contradicting evidence:")
            _append_evidence(lines, hypothesis.get("contradicting_evidence", []))
            if hypothesis.get("session_id"):
                lines.append(f"- OmniRush session: `{_md(hypothesis['session_id'])}`")
    else:
        lines.append("- No hypotheses recorded.")
    if payload["evidence"]:
        lines.extend(["", "### Run evidence", ""])
        _append_evidence(lines, payload["evidence"])

    lines.extend(["", "## Patch and approvals", ""])
    patch = payload.get("patch")
    if patch:
        lines.append(f"- Patch digest: `{_md(patch.get('digest', ''))}`")
        if patch.get("files"):
            lines.append("- Files: " + ", ".join(f"`{_md(item)}`" for item in patch["files"]))
        if patch.get("diff_ref"):
            lines.append(f"- Diff artifact: `{_md(patch['diff_ref'])}`")
        approvals = payload["approvals"]
        if approvals:
            for approval in approvals:
                lines.append(f"- Approval/action: `{_md(approval.get('action', ''))}` by `{_md(approval.get('actor', ''))}` at `{_md(approval.get('timestamp', ''))}`")
        else:
            lines.append("- Approval/action: none recorded.")
    else:
        lines.append("- No patch was applied or approved.")

    lines.extend(["", "## Verification checks", ""])
    checks = payload.get("verification", {}).get("checks", []) if payload.get("verification") else []
    if checks:
        for check in checks:
            lines.append(f"- `{_md(check.get('name', ''))}`: **{_md(check.get('status', ''))}** — `{_md(check.get('command', ''))}`, exit code `{check.get('exit_code')}`, duration `{check.get('duration_ms')}` ms")
    else:
        lines.append("- No verification checks recorded; export remains **UNVERIFIED**.")

    lines.extend(["", "## OmniRush sessions", ""])
    if payload["omnirush_sessions"]:
        lines.extend(f"- `{_md(session)}`" for session in payload["omnirush_sessions"])
    else:
        lines.append("- None recorded.")

    lines.extend(["", "## Artifacts", ""])
    if payload["artifacts"]:
        for artifact in payload["artifacts"]:
            summary = f" — {_md(artifact['summary'])}" if artifact.get("summary") else ""
            lines.append(f"- `{_md(artifact['ref'])}`{summary}")
    else:
        lines.append("- None recorded.")

    lines.extend(["", "## Redaction notices", ""])
    if payload["redactions"]:
        lines.extend(f"- `{_md(item)}`" for item in payload["redactions"])
    else:
        lines.append("- No redactions were needed by the report renderer.")
    return "\n".join(lines) + "\n"


def render_json(run: Any, events: Sequence[Any]) -> str:
    """Render a stable JSON report with redacted, bounded record content."""

    return json.dumps(_build_payload(run, events, {}), sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_report(run_id: Any, destination: os.PathLike[str] | str, format: str) -> Path:
    """Load a persisted run and write a Markdown or JSON report.

    The normal server integration passes a snapshot path or a run record. For
    convenience, an opaque run ID is also resolved from ``PROOFIX_RUN_ROOT``,
    ``runs/``, or ``.proofix/runs/`` without requiring a global store.
    """

    normalized = str(format).casefold()
    if normalized not in {"markdown", "md", "json"}:
        raise ValueError("report format must be markdown or json")
    run, events = _load_run_source(run_id, Path(destination))
    target = Path(destination)
    suffix = ".json" if normalized == "json" else ".md"
    if target.exists() and target.is_dir() or target.suffix == "":
        target = target / f"{_run_id(run)}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    content = render_json(run, events) if normalized == "json" else render_markdown(run, events, {})
    target.write_text(content, encoding="utf-8")
    return target


def _build_payload(run: Any, events: Sequence[Any], artifacts: Mapping[str, Any]) -> dict[str, Any]:
    notices: list[str] = []
    raw_run = _record_dict(run)
    raw_events = [_record_dict(event) for event in events]
    cleaned_run = _clean(raw_run, "run", notices)
    cleaned_events = [_clean(event, f"events[{index}]", notices) for index, event in enumerate(raw_events)]
    run_id = _run_id(raw_run)
    state = str(raw_run.get("state", raw_run.get("status", "unknown")))
    repository = _repository_snapshot(raw_run)
    hypotheses = _list(raw_run.get("hypotheses"))
    evidence = _list(raw_run.get("evidence"))
    patch = raw_run.get("patch")
    verification = raw_run.get("verification")
    approvals = _approvals(raw_run, raw_events)
    command_records = _commands(raw_run, raw_events, verification)
    artifact_records = _artifacts(raw_run, raw_events, artifacts, notices)
    label = _result_label(raw_run, raw_events)
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "result_label": label,
        "terminal_status": state,
        "verified": state == "verified" or (isinstance(verification, Mapping) and verification.get("status") == "verified"),
        "repository_snapshot": _clean(repository, "repository_snapshot", notices),
        "input": _clean(raw_run.get("input", raw_run.get("intake", {})), "input", notices),
        "commands": _clean(command_records, "commands", notices),
        "hypotheses": _clean(hypotheses, "hypotheses", notices),
        "evidence": _clean(evidence, "evidence", notices),
        "approvals": _clean(approvals, "approvals", notices),
        "patch": _clean(patch, "patch", notices) if patch is not None else None,
        "verification": _clean(verification, "verification", notices) if verification is not None else None,
        "omnirush_sessions": _clean(_sessions(raw_run, raw_events), "omnirush_sessions", notices),
        "artifacts": artifact_records,
        "events": cleaned_events,
        "run": cleaned_run,
        "redactions": sorted(set(notices)),
    }
    return payload


def _record_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    converter = getattr(value, "to_dict", None)
    if callable(converter):
        converted = converter()
        return dict(converted) if isinstance(converted, Mapping) else {}
    try:
        return dict(vars(value))
    except TypeError:
        return {"value": value}


def _clean(value: Any, path: str, notices: list[str], key: str = "") -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return {str(name): _clean(nested, f"{path}.{name}", notices, str(name)) for name, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item, f"{path}[{index}]", notices, key) for index, item in enumerate(value)]
    if not isinstance(value, str):
        return _clean(str(value), path, notices, key)
    if _SENSITIVE_KEY.search(key):
        if value:
            notices.append(f"credential redacted at {path}")
        return "[REDACTED]" if value else value
    cleaned, findings = redact_text(value)
    notices.extend(f"{finding} redacted at {path}" for finding in findings)
    limit = _INLINE_LOG_LIMIT if _LOG_KEY.search(key) else 8192
    if len(cleaned) > limit:
        notices.append(f"inline log bounded at {path}; full content remains an artifact reference")
        marker = "\n[content bounded; see artifact reference]"
        cleaned = cleaned[: max(0, limit - len(marker))] + marker
    return cleaned


def _repository_snapshot(run: Mapping[str, Any]) -> dict[str, Any]:
    candidate = run.get("repository") or run.get("repository_snapshot") or run.get("snapshot")
    if isinstance(candidate, Mapping):
        return dict(candidate)
    for evidence in _list(run.get("evidence")):
        if isinstance(evidence, Mapping) and evidence.get("kind") == "repository_snapshot":
            details = evidence.get("details")
            if isinstance(details, Mapping):
                return dict(details)
    intake = run.get("input", run.get("intake", {}))
    if not isinstance(intake, Mapping):
        return {}
    keys = ("repository_path", "remote", "remote_url", "branch", "commit", "commit_sha")
    return {key: intake[key] for key in keys if key in intake}


def _commands(run: Mapping[str, Any], events: Sequence[Mapping[str, Any]], verification: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    input_data = run.get("input", run.get("intake", {}))
    if isinstance(input_data, Mapping) and input_data.get("command"):
        result.append({"command": input_data["command"], "source": "intake"})
    for event in events:
        command = event.get("command")
        details = event.get("details") if isinstance(event.get("details"), Mapping) else {}
        nested = details.get("result") if isinstance(details.get("result"), Mapping) else {}
        command = command or nested.get("command")
        if command:
            artifact_refs = nested.get("artifact_refs") if isinstance(nested.get("artifact_refs"), Mapping) else {}
            stdout_ref = _first_value(event, nested, "stdout_ref", "stdout_artifact_ref") or artifact_refs.get("stdout")
            stderr_ref = _first_value(event, nested, "stderr_ref", "stderr_artifact_ref") or artifact_refs.get("stderr")
            result.append({
                "command": command,
                "exit_code": _first_value(event, nested, "exit_code") if _first_value(event, nested, "exit_code") is not None else None,
                "duration_ms": _first_value(event, details, "duration_ms") if _first_value(event, details, "duration_ms") is not None else nested.get("duration_ms"),
                "stdout_ref": stdout_ref,
                "stderr_ref": stderr_ref,
                "command_ref": _first_value(event, nested, "command_ref"),
                "stdout_summary": nested.get("stdout", details.get("stdout")),
                "stderr_summary": nested.get("stderr", details.get("stderr")),
                "source": event.get("kind", "event"),
            })
    if isinstance(verification, Mapping):
        for check in _list(verification.get("checks")):
            if isinstance(check, Mapping) and check.get("command"):
                result.append({key: check[key] for key in ("command", "exit_code", "duration_ms", "stdout_ref", "stderr_ref", "stdout_artifact_ref", "stderr_artifact_ref", "command_ref", "name", "status", "details") if key in check})
    return result


def _first_value(first: Mapping[str, Any], second: Mapping[str, Any], *keys: str) -> Any:
    for mapping in (first, second):
        for key in keys:
            if mapping.get(key) is not None:
                return mapping[key]
    return None


def _approvals(run: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    patch = run.get("patch")
    if isinstance(patch, Mapping):
        for name in ("approval", "decision"):
            if isinstance(patch.get(name), Mapping):
                result.append(dict(patch[name]))
    for event in events:
        kind = str(event.get("kind", ""))
        if "approval" in kind or "patch_rejected" in kind or "diagnosis" in kind and event.get("details"):
            details = event.get("details") if isinstance(event.get("details"), Mapping) else {}
            if details.get("actor") or details.get("outcome"):
                result.append({"action": details.get("outcome", kind), "actor": details.get("actor", ""), "timestamp": details.get("timestamp", event.get("timestamp", "")), "patch_digest": details.get("patch_digest")})
    return result


def _sessions(run: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> list[str]:
    result = [str(item) for item in _list(run.get("omnirush_sessions")) if item]
    for hypothesis in _list(run.get("hypotheses")):
        if isinstance(hypothesis, Mapping) and hypothesis.get("session_id"):
            result.append(str(hypothesis["session_id"]))
    for event in events:
        details = event.get("details") if isinstance(event.get("details"), Mapping) else {}
        for item in _list(details.get("session_ids")):
            if item:
                result.append(str(item))
    return list(dict.fromkeys(result))


def _artifacts(run: Mapping[str, Any], events: Sequence[Mapping[str, Any]], supplied: Mapping[str, Any], notices: list[str]) -> list[dict[str, str]]:
    refs: dict[str, str] = {}
    def scan(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if str(key).endswith("_ref") and isinstance(nested, str) and nested:
                    summary_key = str(key).replace("_artifact_ref", "").replace("_ref", "")
                    summary = value.get(summary_key) or value.get(f"{summary_key}_summary") or ""
                    refs.setdefault(nested, str(summary))
                elif str(key) == "artifact_refs" and isinstance(nested, Mapping):
                    for stream, ref in nested.items():
                        if isinstance(ref, str) and ref:
                            summary = value.get(stream) or value.get(f"{stream}_summary") or ""
                            refs.setdefault(ref, str(summary))
                scan(nested)
        elif isinstance(value, (list, tuple)):
            for item in value:
                scan(item)
    scan(run)
    scan(events)
    for ref, content in supplied.items():
        if not isinstance(ref, str):
            continue
        summary, findings = redact_text(str(content) if content is not None else "")
        notices.extend(f"{finding} redacted in artifact summary {ref}" for finding in findings)
        if len(summary) > 256:
            notices.append(f"artifact summary bounded for {ref}")
            summary = summary[:220] + "\n[summary bounded; see artifact reference]"
        refs[ref] = summary or "artifact has no inline summary; see the referenced artifact"
    cleaned_refs: list[dict[str, str]] = []
    for ref, summary in sorted(refs.items()):
        cleaned, findings = redact_text(summary)
        notices.extend(f"{finding} redacted in artifact summary {ref}" for finding in findings)
        if len(cleaned) > 256:
            notices.append(f"artifact summary bounded for {ref}")
            cleaned = cleaned[:220] + "\n[summary bounded; see artifact reference]"
        cleaned_refs.append({"ref": ref, "summary": cleaned})
    return cleaned_refs


def _result_label(run: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> str:
    values = [run.get("mode"), run.get("result_label"), run.get("provenance")]
    for event in events:
        values.append(event.get("mode"))
        details = event.get("details")
        if isinstance(details, Mapping):
            values.extend((details.get("mode"), details.get("provenance"), details.get("result_label")))
    if any(isinstance(value, str) and ("recorded" in value.casefold() or "demo" in value.casefold()) for value in values):
        return "recorded/demo"
    return "live OmniRush"


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _run_id(run: Mapping[str, Any]) -> str:
    return str(run.get("run_id", "unknown-run"))


def _append_evidence(lines: list[str], evidence: Any) -> None:
    for item in _list(evidence):
        if not isinstance(item, Mapping):
            continue
        refs: list[str] = []
        if item.get("path"):
            refs.append(str(item["path"]) + (f":{item['line']}" if item.get("line") else ""))
        if item.get("command"):
            refs.append("command=" + str(item["command"]))
        if item.get("artifact_ref"):
            refs.append("artifact=" + str(item["artifact_ref"]))
        detail = f" ({'; '.join(f'`{_md(ref)}`' for ref in refs)})" if refs else ""
        lines.append(f"  - {_md(item.get('summary', 'Evidence'))}{detail}")


def _md(value: Any) -> str:
    return str(value).replace("`", "\\`").replace("\n", " ")


def _load_run_source(source: Any, destination: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if isinstance(source, Mapping) or hasattr(source, "to_dict"):
        run = _record_dict(source)
        return run, _list(run.get("events"))
    candidate = Path(str(source))
    snapshot: Path | None = None
    if candidate.is_file():
        snapshot = candidate
    elif candidate.is_dir() and (candidate / "snapshot.json").is_file():
        snapshot = candidate / "snapshot.json"
    else:
        run_name = str(source)
        roots = []
        if os.environ.get("PROOFIX_RUN_ROOT"):
            roots.append(Path(os.environ["PROOFIX_RUN_ROOT"]))
        roots.extend([destination.parent, Path.cwd(), Path.cwd() / ".proofix"])
        for root in roots:
            for path in (root / run_name / "snapshot.json", root / "runs" / run_name / "snapshot.json", root / ".proofix" / "runs" / run_name / "snapshot.json"):
                if path.is_file():
                    snapshot = path
                    break
            if snapshot:
                break
    if snapshot is None:
        raise FileNotFoundError(f"could not locate run snapshot: {source}")
    run = json.loads(snapshot.read_text(encoding="utf-8"))
    events_path = snapshot.parent / "events.jsonl"
    events: list[dict[str, Any]] = []
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return dict(run), events
