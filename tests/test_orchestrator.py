import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from proofix.intake import sanitize_intake
from proofix.models import DomainError, EventRecord
from proofix.omnirush import RecordedSessionClient, run_specialists
from proofix.orchestrator import ProofFixOrchestrator
from proofix.runner import CommandPolicy, run_command
from proofix.reports import render_json
from proofix.verification import VerificationPlan
from proofix.schemas import SchemaError
from proofix.state import transition
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
            self.assertTrue(any(event.status == "reproduced" for event in events))
            self.assertTrue(all(hypothesis.get("role") for hypothesis in diagnosed.hypotheses))

            reproduction = next(event for event in events if event.kind == "orchestrator:reproduction_result")
            result = reproduction.details["result"]
            self.assertEqual(result["exit_code"], 2)
            self.assertGreaterEqual(result["duration_ms"], 0)
            self.assertTrue(result["stdout_artifact_ref"])
            self.assertTrue(result["stderr_artifact_ref"])
            self.assertIn("repository_snapshot", reproduction.details)
            self.assertEqual(reproduction.details["repository_snapshot"]["commit"], request.commit_sha)
            persisted = orchestrator.store.get_run(run_id)
            self.assertTrue(any(item.get("kind") == "repository_snapshot" for item in persisted.evidence))
            restarted_run = RunStore(root / "runs").get_run(run_id)
            restarted_events = RunStore(root / "runs").list_events(run_id)
            report = json.loads(render_json(restarted_run, restarted_events))
            self.assertEqual(report["repository_snapshot"]["commit"], request.commit_sha)
            self.assertIn("stdout_artifact_ref", json.dumps(report))
            self.assertEqual(report["result_label"], "recorded/demo")

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
            bad.write_text(json.dumps({"mode": "recorded/demo", "responses": [{"role": "bad", "session_id": "s", "response": {"schema_version": "1.0", "hypothesis": "no evidence", "supporting_evidence": [], "contradicting_evidence": [], "confidence": 0.5, "experiment": "try"}}]}), encoding="utf-8")
            orchestrator = ProofFixOrchestrator(RunStore(root / "runs"), WorkspaceManager(root / "workspace-root"), run_command, RecordedSessionClient(bad))
            run_id = orchestrator.start_run(sanitize_intake({"repository_path": str(repository), "command": "python case.py", "failure_text": "failure"}))
            self.assertEqual(orchestrator.advance_reproduction(run_id).state, "reproduced")
            with self.assertRaises(SchemaError):
                orchestrator.advance_diagnosis(run_id)
            self.assertEqual(orchestrator.store.get_run(run_id).state, "reproduced")

    def test_unexpected_runner_failure_is_recorded_and_blocks_the_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self._repository(root, "print('failure')\n")

            def exploding_runner(*_args, **_kwargs):
                raise ZeroDivisionError("runner exploded")

            orchestrator = ProofFixOrchestrator(
                RunStore(root / "runs"),
                WorkspaceManager(root / "workspace-root"),
                exploding_runner,
                RecordedSessionClient(Path("fixtures/omnirush/not-reproduced-run.json")),
            )
            run_id = orchestrator.start_run(sanitize_intake({"repository_path": str(repository), "command": "python case.py", "failure_text": "failure"}))
            result = orchestrator.advance_reproduction(run_id)
            self.assertEqual(result.state, "blocked")
            self.assertTrue(result.evidence)
            self.assertIn("runner exploded", repr(result.evidence))

    def test_orchestrator_prefixed_client_status_cannot_transition_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory))
            run = store.create_run({"repository_path": "/tmp/repo", "command": "false", "failure_text": "failure"})
            with self.assertRaises(DomainError):
                transition(run, "triaging", event_kind="orchestrator:client_status", details={"status": "verified"})
            with self.assertRaises(DomainError):
                store.append_event(
                    EventRecord.new(
                        run.run_id,
                        "orchestrator:client_status",
                        state_after="triaging",
                        details={"target_state": "verified"},
                    )
                )
            self.assertEqual(store.get_run(run.run_id).state, "created")

    def test_outer_session_id_is_authoritative_and_nested_spoofing_is_rejected(self):
        response = {
            "schema_version": "1.0",
            "hypothesis": "A cause",
            "supporting_evidence": [{"kind": "file", "path": "src/parser.py"}],
            "contradicting_evidence": [],
            "confidence": 0.5,
            "experiment": "Run the focused check",
            "session_id": "nested-spoofed-session",
        }
        client = RecordedSessionClient(
            [{"role": "single-specialist", "session_id": "outer-session", "response": response}]
        )
        with self.assertRaises(SchemaError):
            run_specialists({}, client)

    def test_model_hypothesis_and_evidence_text_are_redacted_before_return(self):
        response = {
            "schema_version": "1.0",
            "hypothesis": "password=model-hypothesis-secret",
            "supporting_evidence": [
                {
                    "kind": "log",
                    "summary": "TOKEN=model-summary-secret",
                    "details": {"message": "API_KEY=model-detail-secret"},
                }
            ],
            "contradicting_evidence": [],
            "confidence": 0.5,
            "experiment": "Run the focused check",
        }
        client = RecordedSessionClient(
            [{"role": "redaction-specialist", "session_id": "outer-session", "response": response}]
        )
        hypothesis = run_specialists({}, client)[0]
        encoded = repr(hypothesis.to_dict())
        for secret in ("model-hypothesis-secret", "model-summary-secret", "model-detail-secret"):
            self.assertNotIn(secret, encoded)

    def test_recorded_client_requires_explicit_recorded_demo_label(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.json"
            fixture.write_text(json.dumps({"mode": "recorded", "responses": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                RecordedSessionClient(fixture)

    def test_direct_specialist_response_cannot_claim_state(self):
        client = RecordedSessionClient(Path("fixtures/omnirush/reproducible-run.json"))
        hypotheses = run_specialists({"untrusted": "repository text"}, client)
        self.assertTrue(hypotheses)
        self.assertTrue(all(hypothesis.role and hypothesis.session_id for hypothesis in hypotheses))
        self.assertFalse(any("state" in hypothesis.to_dict() for hypothesis in hypotheses))

    def test_persisted_patch_approval_reconstructs_after_orchestrator_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self._repository(root, "import sys\nprint('failure')\nsys.exit(1)\n")
            (repository / "tests").mkdir()
            (repository / "tests" / "__init__.py").write_text("", encoding="utf-8")
            (repository / "tests" / "test_case.py").write_text("import subprocess, sys, unittest\nclass CaseTest(unittest.TestCase):\n    def test_case(self): self.assertEqual(subprocess.run([sys.executable, 'case.py']).returncode, 0)\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-qm", "tests"], cwd=repository, check=True)
            store = RunStore(root / "runs")
            manager = WorkspaceManager(root / "workspace-root")
            orchestrator = ProofFixOrchestrator(store, manager, run_command, RecordedSessionClient(Path("fixtures/omnirush/reproducible-run.json")))
            run_id = orchestrator.start_run(sanitize_intake({"repository_path": str(repository), "command": "python case.py", "failure_text": "failure"}))
            self.assertEqual(orchestrator.advance_reproduction(run_id).state, "reproduced")
            self.assertEqual(orchestrator.advance_diagnosis(run_id).state, "diagnosing")
            diff = """diff --git a/case.py b/case.py
--- a/case.py
+++ b/case.py
@@ -1,3 +1 @@
-import sys
-print('failure')
-sys.exit(1)
+print('fixed')
"""
            self.assertEqual(orchestrator.propose_patch(run_id, diff, "python -m unittest tests.test_case").state, "awaiting_approval")
            approved = orchestrator.approve_patch(run_id, "alice", "2026-10-15T00:00:00Z", patch_digest=orchestrator.store.get_run(run_id).patch["digest"])
            self.assertEqual(approved.state, "applying")
            restarted = ProofFixOrchestrator(store, WorkspaceManager(root / "workspace-root"), run_command, RecordedSessionClient(Path("fixtures/omnirush/reproducible-run.json")))
            result = restarted.verify_patch(run_id, VerificationPlan("python -m unittest tests.test_case", "python -m unittest tests.test_case"))
            self.assertEqual(result.state, "verified")
            self.assertEqual(RunStore(root / "runs").replay(run_id).state, "verified")

    def test_approval_requires_explicit_persisted_digest_and_run_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self._repository(root, "import sys\nprint('failure')\nsys.exit(1)\n")
            orchestrator = self._orchestrator(root, repository, "reproducible-run.json")
            run_id = orchestrator.start_run(sanitize_intake({"repository_path": str(repository), "command": "python case.py", "failure_text": "failure"}))
            orchestrator.advance_reproduction(run_id)
            orchestrator.advance_diagnosis(run_id)
            diff = """diff --git a/case.py b/case.py
--- a/case.py
+++ b/case.py
@@ -1,3 +1 @@
-import sys
-print('failure')
-sys.exit(1)
+print('fixed')
"""
            orchestrator.propose_patch(run_id, diff, "python -m unittest tests.test_case")
            current_digest = orchestrator.store.get_run(run_id).patch["digest"]
            with self.assertRaises(DomainError):
                orchestrator.approve_patch(run_id, "alice", "2026-10-15T00:00:00Z")
            with self.assertRaises(DomainError):
                orchestrator.approve_patch(run_id, "alice", "2026-10-15T00:00:00Z", patch_digest="sha256:" + "0" * 64)
            persisted = orchestrator.store.get_run(run_id)
            persisted.patch["run_id"] = "run-other"
            orchestrator.store.update_run(persisted)
            with self.assertRaises(DomainError):
                orchestrator.approve_patch(run_id, "alice", "2026-10-15T00:00:00Z", patch_digest=current_digest)

    def test_reject_and_request_decisions_persist_and_replay_as_diagnosing(self):
        for action in ("reject", "request"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repository = self._repository(root, "import sys\nprint('failure')\nsys.exit(1)\n")
                orchestrator = self._orchestrator(root, repository, "reproducible-run.json")
                run_id = orchestrator.start_run(sanitize_intake({"repository_path": str(repository), "command": "python case.py", "failure_text": "failure"}))
                orchestrator.advance_reproduction(run_id)
                orchestrator.advance_diagnosis(run_id)
                diff = """diff --git a/case.py b/case.py
--- a/case.py
+++ b/case.py
@@ -1,3 +1 @@
-import sys
-print('failure')
-sys.exit(1)
+print('fixed')
"""
                orchestrator.propose_patch(run_id, diff, "python -m unittest tests.test_case")
                digest = orchestrator.store.get_run(run_id).patch["digest"]
                if action == "reject":
                    result = orchestrator.reject_patch(run_id, "alice", "2026-10-15T00:00:00Z", digest)
                    expected = "rejected"
                else:
                    result = orchestrator.request_another_diagnosis(run_id, "alice", "2026-10-15T00:00:00Z", digest)
                    expected = "request_another_diagnosis"
                self.assertEqual(result.state, "diagnosing")
                self.assertEqual(result.patch["decision"]["action"], expected)
                self.assertEqual(RunStore(root / "runs").replay(run_id).state, "diagnosing")

    def test_verification_reconstructs_current_persisted_proposal_after_approval(self):
        for mutation in ("diff", "files"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repository = self._repository(root, "import sys\nprint('failure')\nsys.exit(1)\n")
                orchestrator = self._orchestrator(root, repository, "reproducible-run.json")
                run_id = orchestrator.start_run(sanitize_intake({"repository_path": str(repository), "command": "python case.py", "failure_text": "failure"}))
                orchestrator.advance_reproduction(run_id)
                orchestrator.advance_diagnosis(run_id)
                diff = """diff --git a/case.py b/case.py
--- a/case.py
+++ b/case.py
@@ -1,3 +1 @@
-import sys
-print('failure')
-sys.exit(1)
+print('fixed')
"""
                orchestrator.propose_patch(run_id, diff, "python -m unittest tests.test_case")
                current = orchestrator.store.get_run(run_id)
                orchestrator.approve_patch(run_id, "alice", "2026-10-15T00:00:00Z", patch_digest=current.patch["digest"])

                persisted = orchestrator.store.get_run(run_id)
                if mutation == "diff":
                    persisted.patch["diff"] = persisted.patch["diff"].replace("print('fixed')", "print('tampered')")
                else:
                    persisted.patch["files"] = ["other.py"]
                orchestrator.store.update_run(persisted)

                result = orchestrator.verify_patch(run_id, VerificationPlan("python case.py", "python case.py"))
                self.assertEqual(result.state, "verification_blocked")
                self.assertEqual(result.verification["checks"][0]["name"], "patch_integrity")

    def test_restart_blocks_verification_when_source_revision_changed_after_reproduction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self._repository(root, "import sys\nprint('failure')\nsys.exit(1)\n")
            orchestrator = self._orchestrator(root, repository, "reproducible-run.json")
            run_id = orchestrator.start_run(sanitize_intake({"repository_path": str(repository), "command": "python case.py", "failure_text": "failure"}))
            orchestrator.advance_reproduction(run_id)
            orchestrator.advance_diagnosis(run_id)
            diff = """diff --git a/case.py b/case.py
--- a/case.py
+++ b/case.py
@@ -1,3 +1 @@
-import sys
-print('failure')
-sys.exit(1)
+print('fixed')
"""
            orchestrator.propose_patch(run_id, diff, "python -m unittest tests.test_parser")
            digest = orchestrator.store.get_run(run_id).patch["digest"]
            orchestrator.approve_patch(run_id, "alice", "2026-10-15T00:00:00Z", digest)
            (repository / "unrelated.txt").write_text("changed\n", encoding="utf-8")
            subprocess.run(["git", "add", "unrelated.txt"], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-qm", "changed source revision"], cwd=repository, check=True)
            restarted = self._orchestrator(root, repository, "reproducible-run.json")
            result = restarted.verify_patch(run_id, VerificationPlan("python case.py", "python -m unittest tests.test_parser"))
            self.assertEqual(result.state, "verification_blocked")
            self.assertEqual(result.verification["checks"][0]["name"], "snapshot_revision")
            self.assertEqual(restarted.store.get_run(run_id).evidence[-1]["kind"], "repository_snapshot")

    def test_restart_rejects_tampered_persisted_patch_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self._repository(root, "import sys\nprint('failure')\nsys.exit(1)\n")
            orchestrator = self._orchestrator(root, repository, "reproducible-run.json")
            run_id = orchestrator.start_run(sanitize_intake({"repository_path": str(repository), "command": "python case.py", "failure_text": "failure"}))
            orchestrator.advance_reproduction(run_id)
            orchestrator.advance_diagnosis(run_id)
            diff = """diff --git a/case.py b/case.py
--- a/case.py
+++ b/case.py
@@ -1,3 +1 @@
-import sys
-print('failure')
-sys.exit(1)
+print('fixed')
"""
            orchestrator.propose_patch(run_id, diff, "python -m unittest tests.test_case")
            orchestrator.approve_patch(run_id, "alice", "2026-10-15T00:00:00Z", patch_digest=orchestrator.store.get_run(run_id).patch["digest"])
            snapshot = root / "runs" / "runs" / run_id / "snapshot.json"
            persisted = json.loads(snapshot.read_text(encoding="utf-8"))
            persisted["patch"]["diff"] = persisted["patch"]["diff"].replace("print('fixed')", "print('tampered')")
            snapshot.write_text(json.dumps(persisted), encoding="utf-8")
            restarted = self._orchestrator(root, repository, "reproducible-run.json")
            result = restarted.verify_patch(run_id, VerificationPlan("python -m unittest tests.test_case", "python -m unittest tests.test_case"))
            self.assertEqual(result.state, "verification_blocked")
            self.assertEqual(result.verification["checks"][0]["name"], "patch_integrity")


if __name__ == "__main__":
    unittest.main()
