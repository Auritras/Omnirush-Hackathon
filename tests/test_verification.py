import tempfile
import unittest
from pathlib import Path
import subprocess

from proofix.patches import approve_patch, validate_unified_diff
from proofix.verification import VerificationPlan, verify_patch
from proofix.runner import run_command


class VerificationTests(unittest.TestCase):
    def _repo(self, root: Path, source: str) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "proofix@example.test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "ProofFix Tests"], cwd=repo, check=True)
        (repo / "case.py").write_text(source, encoding="utf-8")
        (repo / "tests").mkdir()
        (repo / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "tests" / "test_case.py").write_text("import subprocess, sys, unittest\nclass CaseTest(unittest.TestCase):\n    def test_case(self): self.assertEqual(subprocess.run([sys.executable, 'case.py']).returncode, 0)\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
        return repo

    def test_unapproved_patch_is_blocked_and_source_is_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self._repo(root, "print('bad')\n")
            diff = """diff --git a/case.py b/case.py\n--- a/case.py\n+++ b/case.py\n@@ -1 +1 @@\n-print('bad')\n+print('good')\n"""
            proposal = validate_unified_diff(diff, repo)
            summary = verify_patch(proposal, repo, run_command, VerificationPlan("python -m unittest tests.test_case", "python -m unittest tests.test_case"))
            self.assertEqual(summary.status, "verification_blocked")
            self.assertEqual((repo / "case.py").read_text(), "print('bad')\n")

    def test_correct_patch_passes_in_required_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self._repo(root, "import sys; sys.exit(1)\n")
            diff = """diff --git a/case.py b/case.py\n--- a/case.py\n+++ b/case.py\n@@ -1 +1 @@\n-import sys; sys.exit(1)\n+print('fixed')\n"""
            proposal = validate_unified_diff(diff, repo)
            approve_patch("run-verify", proposal.digest, "alice", "2026-10-15T00:00:00Z", proposal=proposal, current_digest=proposal.digest)
            plan = VerificationPlan("python -m unittest tests.test_case", "python -m unittest tests.test_case", relevant_tests=["python -m unittest tests.test_case"], broader_suite="python -m unittest tests.test_case")
            summary = verify_patch(proposal, repo, run_command, plan)
            self.assertEqual(summary.status, "verified")
            self.assertEqual([check.name for check in summary.checks], ["original_failure", "regression_test", "relevant_test_1", "broader_suite", "diff_scope_syntax"])

    def test_failed_regression_is_retained_and_blocked_command_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self._repo(root, "import sys; sys.exit(1)\n")
            diff = """diff --git a/case.py b/case.py\n--- a/case.py\n+++ b/case.py\n@@ -1 +1 @@\n-import sys; sys.exit(1)\n+print('fixed')\n"""
            proposal = validate_unified_diff(diff, repo)
            approve_patch("run-fail", proposal.digest, "alice", "2026-10-15T00:00:00Z", proposal=proposal, current_digest=proposal.digest)
            plan = VerificationPlan("python -m unittest tests.test_case", "python -m unittest tests.missing_module")
            summary = verify_patch(proposal, repo, run_command, plan)
            self.assertEqual(summary.status, "verification_failed")
            self.assertEqual(summary.failed_check.name, "regression_test")
            self.assertEqual(summary.failed_check.status, "failed")

            blocked = verify_patch(proposal, repo, run_command, VerificationPlan("python evil.py", "python -m unittest tests.test_case"))
            self.assertEqual(blocked.status, "verification_blocked")
            self.assertEqual(blocked.failed_check.status, "blocked")

            marker = repo / "verification-marker"
            (repo / "evil.py").write_text(f"from pathlib import Path; Path({str(marker)!r}).touch()\n", encoding="utf-8")
            blocked = verify_patch(proposal, repo, run_command, VerificationPlan("python evil.py", "python -m unittest tests.test_case"))
            self.assertFalse(marker.exists())
            self.assertEqual(blocked.status, "verification_blocked")

    def test_runner_exception_is_a_bounded_blocked_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self._repo(root, "import sys; sys.exit(1)\n")
            diff = """diff --git a/case.py b/case.py\n--- a/case.py\n+++ b/case.py\n@@ -1 +1 @@\n-import sys; sys.exit(1)\n+print('fixed')\n"""
            proposal = validate_unified_diff(diff, repo)
            approve_patch("run-exception", proposal.digest, "alice", "2026-10-15T00:00:00Z", proposal=proposal, current_digest=proposal.digest)
            def exploding_runner(*_args, **_kwargs):
                raise OSError("runner unavailable")
            summary = verify_patch(proposal, repo, exploding_runner, VerificationPlan("python -m unittest tests.test_case", "python -m unittest tests.test_case"))
            self.assertEqual(summary.status, "verification_blocked")
            self.assertIn("runner unavailable", summary.failed_check.details["error"])


if __name__ == "__main__":
    unittest.main()
