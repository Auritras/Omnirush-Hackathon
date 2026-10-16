import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from proofix.intake import sanitize_intake
from proofix.omnirush import RecordedSessionClient, run_specialists
from proofix.orchestrator import ProofFixOrchestrator
from proofix.runner import CommandPolicy, run_command
from proofix.store import RunStore
from proofix.worktree import WorkspaceManager


class OrchestratorTests(unittest.TestCase):
    def _repository(self, root: Path, source: str) -> Path:
        repository = root / "repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.email", "proofix@example.test"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.name", "ProofFix Tests"], cwd=repository, check=True)
        (repository / "case.py").write_text(source, encoding="utf-8")
        subprocess.run(["git", "add", "case.py"], cwd=repository, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repository, check=True)
        return repository

    def _orchestrator(self, root: Path, repository: Path, fixture: str):
        client = RecordedSessionClient(Path("fixtures/omnirush") / fixture)
        return ProofFixOrchestrator(
            RunStore(root / "runs"),
            WorkspaceManager(root / "workspace-root"),
            run_command,
            client,
        )

    def test_reproduced_path_runs_real_command_then_launches_recorded_specialists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self._repository(root, "import sys\nprint('expected failure')\nsys.exit(2)\n")
            orchestrator = self._orchestrator(root, repository, "reproducible-run.json")
            request = sanitize_intake({"repository_path": str(repository), "command": "python case.py", "failure_text": "expected failure", "commit_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()})
            run_id = orchestrator.start_run(request)
            self.assertEqual(orchestrator.store.get_run(run_id).state, "reproducing")
            reproduced = orchestrator.advance_reproduction(run_id)
            self.assertEqual(reproduced.state, "reproduced")
            diagnosed = orchestrator.advance_diagnosis(run_id)
            self.assertEqual(diagnosed.state, "diagnosing")
            self.assertEqual(len(diagnosed.hypotheses), 5)
            self.assertTrue(diagnosed.omnirush_sessions)
            events = orchestrator.store.list_events(run_id)
            self.assertTrue(any(event.details.get("status") == "reproduced" for event in events))
            self.assertTrue(all(hypothesis.get("role") for hypothesis in diagnosed.hypotheses))

    def test_successful_or_different_failure_stops_with_evidence_without_diagnosis(self):
        for source, failure in (("print('all good')\n", "expected failure"), ("import sys\nprint('other')\nsys.exit(1)\n", "expected failure")):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repository = self._repository(root, source)
                orchestrator = self._orchestrator(root, repository, "not-reproduced-run.json")
                run_id = orchestrator.start_run(sanitize_intake({"repository_path": str(repository), "command": "python case.py", "failure_text": failure}))
                result = orchestrator.advance_reproduction(run_id)
                self.assertEqual(result.state, "not_reproduced")
                self.assertTrue(result.evidence)
                with self.assertRaises(ValueError):
                    orchestrator.advance_diagnosis(run_id)
                self.assertFalse(any(event.kind == "orchestrator:diagnosis_started" for event in orchestrator.store.list_events(run_id)))

    def test_blocked_path_stops_and_agent_status_fields_cannot_advance_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self._repository(root, "print('ok')\n")
            orchestrator = self._orchestrator(root, repository, "not-reproduced-run.json")
            run_id = orchestrator.start_run(sanitize_intake({"repository_path": str(repository), "command": "missing-proofix-command", "failure_text": "failure"}))
            result = orchestrator.advance_reproduction(run_id)
            self.assertEqual(result.state, "blocked")
            self.assertTrue(result.evidence)
            self.assertEqual(orchestrator.store.get_run(run_id).state, "blocked")

    def test_malformed_recorded_response_is_rejected_without_state_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self._repository(root, "import sys\nprint('failure')\nsys.exit(2)\n")
            bad = root / "bad.json"
            bad.write_text(json.dumps({"mode": "recorded", "responses": [{"role": "bad", "session_id": "s", "response": {"schema_version": "1.0", "hypothesis": "no evidence", "supporting_evidence": [], "contradicting_evidence": [], "confidence": 0.5, "experiment": "try"}}]}), encoding="utf-8")
            orchestrator = ProofFixOrchestrator(RunStore(root / "runs"), WorkspaceManager(root / "workspace-root"), run_command, RecordedSessionClient(bad))
            run_id = orchestrator.start_run(sanitize_intake({"repository_path": str(repository), "command": "python case.py", "failure_text": "failure"}))
            self.assertEqual(orchestrator.advance_reproduction(run_id).state, "reproduced")
            with self.assertRaises(Exception):
                orchestrator.advance_diagnosis(run_id)
            self.assertEqual(orchestrator.store.get_run(run_id).state, "reproduced")

    def test_direct_specialist_response_cannot_claim_state(self):
        client = RecordedSessionClient(Path("fixtures/omnirush/reproducible-run.json"))
        hypotheses = run_specialists({"untrusted": "repository text"}, client)
        self.assertTrue(hypotheses)
        self.assertTrue(all(hypothesis.role and hypothesis.session_id for hypothesis in hypotheses))
        self.assertFalse(any("state" in hypothesis.to_dict() for hypothesis in hypotheses))


if __name__ == "__main__":
    unittest.main()
