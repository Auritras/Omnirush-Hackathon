"""Durable local snapshot and append-only event storage for ProofFix runs."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .models import DomainError, EventRecord, RunRecord, ValidationError
from .state import transition


_NUMERIC_EVENT_ID = re.compile(r"^evt-(\d+)(?:-|$)")


class RunStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.runs_root = self.root / "runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)

    def create_run(self, intake: Mapping[str, Any]) -> RunRecord:
        if not isinstance(intake, Mapping):
            raise DomainError("intake must be a JSON object")
        try:
            json.dumps(intake, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise DomainError("intake must be JSON-compatible") from exc
        run = RunRecord.new(intake)
        run_dir = self._run_dir(run.run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        self._write_snapshot(run)
        self._events_path(run.run_id).touch()
        return run

    def get_run(self, run_id: str) -> RunRecord:
        snapshot = self._snapshot_path(run_id)
        try:
            data = json.loads(snapshot.read_text(encoding="utf-8"))
            return RunRecord.from_dict(data)
        except FileNotFoundError as exc:
            raise DomainError(f"unknown run: {run_id}") from exc
        except (OSError, json.JSONDecodeError, TypeError, ValidationError) as exc:
            if isinstance(exc, DomainError):
                raise
            raise DomainError(f"malformed run snapshot: {run_id}") from exc

    def append_event(self, event: EventRecord) -> None:
        if not isinstance(event, EventRecord):
            raise DomainError("append_event requires an EventRecord")
        run = self.get_run(event.run_id)
        events = self.list_events(event.run_id)
        if any(existing.event_id == event.event_id for existing in events):
            raise DomainError(f"duplicate event ID: {event.event_id}")
        if events and self._event_key(event.event_id) <= self._event_key(events[-1].event_id):
            raise DomainError("event IDs must be appended in order")
        if event.state_after is not None:
            if event.state_before is not None and event.state_before != run.state:
                raise DomainError("event state_before does not match the run snapshot")
            try:
                updated = transition(run, event.state_after, event_kind=event.kind, details=event.details)
            except DomainError:
                raise
            updated = replace(updated, updated_at=event.timestamp)
        else:
            updated = run
        try:
            with self._events_path(event.run_id).open("a", encoding="utf-8") as handle:
                handle.write(event.to_json() + "\n")
                handle.flush()
        except OSError as exc:
            raise DomainError("could not append event") from exc
        if updated.state != run.state:
            self._write_snapshot(updated)

    def list_events(self, run_id: str) -> list[EventRecord]:
        events_path = self._events_path(run_id)
        if not events_path.exists():
            if self._snapshot_path(run_id).exists():
                raise DomainError(f"missing event log for run: {run_id}")
            raise DomainError(f"unknown run: {run_id}")
        result: list[EventRecord] = []
        try:
            lines = events_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise DomainError("could not read event log") from exc
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                raise DomainError(f"blank event record at line {line_number}")
            try:
                event = EventRecord.from_dict(json.loads(line))
            except (json.JSONDecodeError, TypeError, ValidationError, ValueError) as exc:
                raise DomainError(f"malformed event record at line {line_number}") from exc
            if event.run_id != run_id:
                raise DomainError(f"event run ID mismatch at line {line_number}")
            if result and self._event_key(event.event_id) <= self._event_key(result[-1].event_id):
                raise DomainError(f"event ordering error at line {line_number}")
            if result and event.timestamp < result[-1].timestamp:
                raise DomainError(f"event timestamp ordering error at line {line_number}")
            result.append(event)
        return result

    def replay(self, run_id: str) -> RunRecord:
        snapshot = self.get_run(run_id)
        replayed = replace(snapshot, state="created", updated_at=snapshot.created_at)
        for event in self.list_events(run_id):
            if event.state_after is not None:
                replayed = transition(replayed, event.state_after, event_kind=event.kind, details=event.details)
                replayed = replace(replayed, updated_at=event.timestamp)
        if replayed.state != snapshot.state:
            raise DomainError("run snapshot does not match its event log")
        return replayed

    def _run_dir(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not run_id.startswith("run-") or "/" in run_id or "\\" in run_id:
            raise DomainError("invalid run ID")
        return self.runs_root / run_id

    def _snapshot_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "snapshot.json"

    def _events_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "events.jsonl"

    def _write_snapshot(self, run: RunRecord) -> None:
        path = self._snapshot_path(run.run_id)
        temporary = path.with_suffix(".json.tmp")
        try:
            temporary.write_text(run.to_json() + "\n", encoding="utf-8")
            temporary.replace(path)
        except OSError as exc:
            raise DomainError("could not write run snapshot") from exc

    @staticmethod
    def _event_key(event_id: str) -> tuple[int, Any]:
        match = _NUMERIC_EVENT_ID.match(event_id)
        if match:
            return (0, int(match.group(1)))
        return (1, event_id)
