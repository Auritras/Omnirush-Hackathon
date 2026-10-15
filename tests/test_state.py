import json
import tempfile
import unittest
from pathlib import Path

from proofix.models import EventRecord
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
                    event_kind=f"{target}_started",
                    details={"source": "orchestrator"},
                )
                store.append_event(
                    EventRecord.new(
                        run_id=run.run_id,
                        kind=f"{target}_started",
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
            run = RunStore(Path(tempfile.mkdtemp())).create_run({"command": "true"})
            for target in path:
                run = transition(run, target, event_kind="orchestrator_step", details={})
            with self.assertRaises(DomainError):
                transition(run, "diagnosing", event_kind="agent_claim", details={})

    def test_invalid_skips_and_agent_status_changes_are_rejected(self):
        run = RunStore(Path(tempfile.mkdtemp())).create_run({"command": "false"})
        self.assertFalse(can_transition("created", "reproduced"))
        with self.assertRaises(DomainError):
            transition(run, "reproduced", event_kind="agent_claim", details={})
        with self.assertRaises(DomainError):
            transition(run, "triaging", event_kind="agent_claim", details={"state": "verified"})
        with self.assertRaises(DomainError):
            transition(run, "triaging", event_kind="agent_claim", details={"status": "verified"})
        with self.assertRaises(DomainError):
            transition(run, "unknown", event_kind="orchestrator_step", details={})


class StorePersistenceTests(unittest.TestCase):
    def test_snapshot_jsonl_append_order_and_replay_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RunStore(root)
            run = store.create_run({"command": "pytest tests/test_parser.py"})
            first = EventRecord.new(run_id=run.run_id, kind="triage_started", state_after="triaging")
            second = EventRecord.new(run_id=run.run_id, kind="reproduce_started", state_after="reproducing")
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
            self.assertLessEqual(len(event.to_dict().get("stdout", "")), 4096)

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


if __name__ == "__main__":
    unittest.main()
