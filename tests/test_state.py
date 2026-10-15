import json
import tempfile
import unittest
from pathlib import Path

from proofix.models import CheckResult, EventRecord, EvidenceRecord, PatchProposal, RunRecord
from proofix.state import DomainError, can_transition, transition
from proofix.store import RunStore


class StateTransitionTests(unittest.TestCase):
    def test_happy_path_reaches_verified_and_github_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory))
            run = store.create_run({"command": "python -m unittest", "failure_text": "boom"})

            for target in (
                "triaging",
                "reproducing",
                "reproduced",
                "diagnosing",
                "experimenting",
                "patch_proposed",
                "awaiting_approval",
                "applying",
                "verifying",
                "verified",
                "github_pending_approval",
                "github_complete",
            ):
                updated = transition(
                    run,
                    target,
                    event_kind=f"orchestrator:{target}_started",
                    details={"source": "orchestrator"},
                )
                store.append_event(
                    EventRecord.new(
                        run_id=run.run_id,
                        kind=f"orchestrator:{target}_started",
                        state_after=target,
                        details={"source": "orchestrator"},
                    )
                )
                run = updated

            self.assertEqual(run.state, "github_complete")
            self.assertEqual(store.replay(run.run_id).state, "github_complete")

    def test_terminal_outcomes_cannot_transition(self):
        terminal_paths = {
            "not_reproduced": ("triaging", "reproducing", "not_reproduced"),
            "blocked": ("triaging", "reproducing", "blocked"),
            "verification_failed": (
                "triaging",
                "reproducing",
                "reproduced",
                "diagnosing",
                "patch_proposed",
                "awaiting_approval",
                "applying",
                "verifying",
                "verification_failed",
            ),
            "verification_blocked": (
                "triaging",
                "reproducing",
                "reproduced",
                "diagnosing",
                "patch_proposed",
                "awaiting_approval",
                "applying",
                "verifying",
                "verification_blocked",
            ),
        }
        for terminal, path in terminal_paths.items():
            with tempfile.TemporaryDirectory() as directory:
                run = RunStore(Path(directory)).create_run({"command": "true"})
                for target in path:
                    run = transition(run, target, event_kind="orchestrator_step", details={})
                with self.assertRaises(DomainError):
                    transition(run, "diagnosing", event_kind="agent_claim", details={})

    def test_invalid_skips_and_agent_status_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            run = RunStore(Path(directory)).create_run({"command": "false"})
            self.assertFalse(can_transition("created", "reproduced"))
            with self.assertRaises(DomainError):
                transition(run, "reproduced", event_kind="agent_claim", details={})
            with self.assertRaises(DomainError):
                transition(run, "reproduced", event_kind="browser_approved", details={})
            with self.assertRaises(DomainError):
                transition(run, "triaging", event_kind="agent_claim", details={"state": "verified"})
            with self.assertRaises(DomainError):
                transition(run, "triaging", event_kind="agent_claim", details={"status": "verified"})
            with self.assertRaises(DomainError):
                transition(run, "unknown", event_kind="orchestrator_step", details={})

    def test_only_orchestrator_owned_events_can_mutate_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory))
            run = store.create_run({"command": "false"})
            for kind in ("agent:verified", "browser:verified", "browser_approved"):
                with self.assertRaises(DomainError):
                    store.append_event(EventRecord.new(run.run_id, kind, state_after="triaging"))
            self.assertEqual(store.get_run(run.run_id).state, "created")

    def test_nested_log_fields_must_be_bounded_or_referenced(self):
        with self.assertRaises(DomainError):
            EventRecord.new(
                "run-test",
                "orchestrator:command_result",
                details={"result": {"stdout": "x" * 5000}},
            )
        event = EventRecord.new(
            "run-test",
            "orchestrator:command_result",
            details={"result": {"stdout_ref": "artifacts/stdout.log", "stderr_ref": "artifacts/stderr.log"}},
        )
        self.assertEqual(event.details["result"]["stdout_ref"], "artifacts/stdout.log")

    def test_record_json_round_trips_are_strict_and_deterministic(self):
        records = [
            RunRecord.new({"command": "pytest", "failure_text": "failed"}),
            EventRecord.new("run-test", "orchestrator:triage_started", details={"confidence": 0.5}),
            EvidenceRecord(kind="file", summary="Relevant source", details={"line": 42}),
            CheckResult(name="regression", status="passed", command="pytest tests/test_fix.py"),
            PatchProposal(digest="sha256:abc", diff_ref="artifacts/patch.diff", files=["src/fix.py"]),
        ]
        for record in records:
            self.assertEqual(record, type(record).from_json(record.to_json()))
            self.assertEqual(record.to_json(), record.to_json())

        with self.assertRaises(DomainError):
            EventRecord.new("run-test", "orchestrator:result", details={"value": float("nan")})

    def test_artifact_references_reject_windows_absolute_paths(self):
        with self.assertRaises(DomainError):
            EventRecord.new("run-test", "orchestrator:result", stdout_ref="C:\\secret\\output.log")


class StorePersistenceTests(unittest.TestCase):
    def test_snapshot_jsonl_append_order_and_replay_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RunStore(root)
            run = store.create_run({"command": "pytest tests/test_parser.py"})
            first = EventRecord.new(run_id=run.run_id, kind="orchestrator:triage_started", state_after="triaging")
            second = EventRecord.new(run_id=run.run_id, kind="orchestrator:reproduce_started", state_after="reproducing")
            store.append_event(first)
            store.append_event(second)

            snapshot = root / "runs" / run.run_id / "snapshot.json"
            events = root / "runs" / run.run_id / "events.jsonl"
            self.assertTrue(snapshot.exists())
            self.assertTrue(events.exists())
            self.assertEqual([event.event_id for event in store.list_events(run.run_id)], [first.event_id, second.event_id])
            self.assertEqual(RunStore(root).replay(run.run_id).state, "reproducing")
            json.loads(snapshot.read_text(encoding="utf-8"))
            self.assertEqual(len(events.read_text(encoding="utf-8").splitlines()), 2)

    def test_large_output_uses_artifact_references(self):
        with tempfile.TemporaryDirectory() as directory:
            run = RunStore(Path(directory)).create_run({"command": "pytest"})
            event = EventRecord.new(
                run_id=run.run_id,
                kind="command_result",
                stdout_ref="artifacts/evt.stdout",
                stderr_ref="artifacts/evt.stderr",
                details={"stdout_bytes": 100_000},
            )
            self.assertIsNone(event.stdout)
            self.assertEqual(event.stdout_ref, "artifacts/evt.stdout")
            self.assertIsNone(event.to_dict().get("stdout"))

    def test_mismatched_ids_duplicate_or_out_of_order_and_malformed_records_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RunStore(root)
            run = store.create_run({"command": "pytest"})
            event = EventRecord(
                schema_version="1.0",
                event_id="evt-0002",
                run_id=run.run_id,
                timestamp=EventRecord.now_timestamp(),
                kind="one",
            )
            store.append_event(event)
            with self.assertRaises(DomainError):
                store.append_event(event)
            with self.assertRaises(DomainError):
                store.append_event(
                    EventRecord(
                        schema_version="1.0",
                        event_id="evt-0001",
                        run_id=run.run_id,
                        timestamp=EventRecord.now_timestamp(),
                        kind="out_of_order",
                    )
                )
            with self.assertRaises(DomainError):
                store.append_event(
                    EventRecord(
                        schema_version="1.0",
                        event_id="evt-0003",
                        run_id="run-other",
                        timestamp=EventRecord.now_timestamp(),
                        kind="wrong_run",
                    )
                )
            events = root / "runs" / run.run_id / "events.jsonl"
            with events.open("a", encoding="utf-8") as handle:
                handle.write("{not-json}\n")
            with self.assertRaises(DomainError):
                store.list_events(run.run_id)

            snapshot = root / "runs" / run.run_id / "snapshot.json"
            snapshot.write_text(json.dumps({"schema_version": "1.0", "run_id": run.run_id}), encoding="utf-8")
            with self.assertRaises(DomainError):
                store.get_run(run.run_id)

    def test_out_of_order_timestamp_is_rejected_before_jsonl_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RunStore(root)
            run = store.create_run({"command": "pytest"})
            store.append_event(
                EventRecord(
                    event_id="evt-0001",
                    run_id=run.run_id,
                    timestamp="2026-01-01T01:30:00+01:00",
                    kind="orchestrator:triage_started",
                )
            )
            events = root / "runs" / run.run_id / "events.jsonl"
            before = events.read_text(encoding="utf-8")
            with self.assertRaises(DomainError):
                store.append_event(
                    EventRecord(
                        event_id="evt-0002",
                        run_id=run.run_id,
                        timestamp="2026-01-01T00:00:00Z",
                        kind="orchestrator:late_event",
                    )
                )
            self.assertEqual(events.read_text(encoding="utf-8"), before)

    def test_replay_validates_state_before_and_terminal_outcomes(self):
        terminal_paths = {
            "not_reproduced": ("triaging", "reproducing", "not_reproduced"),
            "blocked": ("triaging", "reproducing", "blocked"),
            "verification_failed": (
                "triaging", "reproducing", "reproduced", "diagnosing", "patch_proposed",
                "awaiting_approval", "applying", "verifying", "verification_failed",
            ),
            "verification_blocked": (
                "triaging", "reproducing", "reproduced", "diagnosing", "patch_proposed",
                "awaiting_approval", "applying", "verifying", "verification_blocked",
            ),
        }
        for terminal, path in terminal_paths.items():
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as directory:
                store = RunStore(Path(directory))
                run = store.create_run({"command": "pytest"})
                previous = "created"
                for target in path:
                    event = EventRecord.new(
                        run.run_id,
                        "orchestrator:state_transition",
                        state_before=previous,
                        state_after=target,
                    )
                    store.append_event(event)
                    previous = target
                self.assertEqual(RunStore(Path(directory)).replay(run.run_id).state, terminal)

        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory))
            run = store.create_run({"command": "pytest"})
            store.append_event(EventRecord.new(run.run_id, "orchestrator:triage", state_after="triaging"))
            events = Path(directory) / "runs" / run.run_id / "events.jsonl"
            tampered = events.read_text(encoding="utf-8").replace(
                '"state_before":null',
                '"state_before":"blocked"',
            )
            events.write_text(tampered, encoding="utf-8")
            with self.assertRaises(DomainError):
                store.replay(run.run_id)


if __name__ == "__main__":
    unittest.main()
