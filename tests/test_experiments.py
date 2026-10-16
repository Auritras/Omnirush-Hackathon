import hashlib
import tempfile
import unittest
from pathlib import Path
import subprocess

from proofix.experiments import ExperimentSpec, run_experiment
from proofix.runner import CommandPolicy, run_command


class ExperimentTests(unittest.TestCase):
    def test_each_named_experiment_records_evidence_and_hypothesis(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "tests").mkdir()
            (workspace / "tests" / "__init__.py").write_text("", encoding="utf-8")
            (workspace / "tests" / "test_case.py").write_text("import unittest\nclass CaseTest(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n", encoding="utf-8")
            command = "python -m unittest tests.test_case"
            specs = [
                ExperimentSpec("named_test_or_build", command, hypothesis_id="h-1"),
                ExperimentSpec("controlled_environment", command, hypothesis_id="h-2", environment={"MODE": "test"}),
                ExperimentSpec("temporary_instrumentation", command, hypothesis_id="h-3", instrumentation={"trace.txt": "trace\n"}),
                ExperimentSpec("dependency_runtime_inspection", "python --version", hypothesis_id="h-5"),
                ExperimentSpec("proposed_regression_test", command, regression_test=command, hypothesis_id="h-6"),
            ]
            for spec in specs:
                with self.subTest(kind=spec.kind):
                    result = run_experiment(spec, workspace, run_command)
                    self.assertEqual(result.hypothesis_id, spec.hypothesis_id)
                    self.assertIsNotNone(result.exit_code)
                    self.assertEqual(result.output_digest, "sha256:" + hashlib.sha256((result.stdout + result.stderr).encode()).hexdigest())
                    self.assertIsInstance(result.changed_files, list)
                    self.assertIsInstance(result.artifacts, dict)

    def test_arbitrary_commands_and_unknown_kinds_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with self.assertRaises(ValueError):
                run_experiment(ExperimentSpec("agent_command", "rm -rf ."), workspace, run_command)
            with self.assertRaises(ValueError):
                run_experiment(ExperimentSpec("named_test_or_build", "python -c 'open(\"pwned\", \"w\")'"), workspace, run_command)
            marker = workspace / "marker"
            (workspace / "evil.py").write_text(f"from pathlib import Path; Path({str(marker)!r}).touch()\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                run_experiment(ExperimentSpec("named_test_or_build", "python evil.py"), workspace, run_command)
            self.assertFalse(marker.exists())

    def test_dependency_runtime_inspection_is_read_only_and_fixed(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            marker = workspace / "side-effect"
            with self.assertRaises(ValueError):
                run_experiment(ExperimentSpec("dependency_runtime_inspection", f"python -m venv {marker}"), workspace, run_command)
            self.assertFalse(marker.exists())
            with self.assertRaises(ValueError):
                run_experiment(ExperimentSpec("dependency_runtime_inspection", "python -m pip install proofix-test"), workspace, run_command)
            result = run_experiment(ExperimentSpec("dependency_runtime_inspection", "python -m pip --version"), workspace, run_command)
            self.assertEqual(result.exit_code, 0)

    def test_parent_commit_runs_the_same_named_target_and_regression_requires_a_distinct_spec(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "proofix@example.test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "ProofFix Tests"], cwd=repo, check=True)
            (repo / "version.txt").write_text("old\n", encoding="utf-8")
            (repo / "tests").mkdir()
            (repo / "tests" / "__init__.py").write_text("", encoding="utf-8")
            (repo / "tests" / "test_version.py").write_text("import unittest\nfrom pathlib import Path\nclass VersionTest(unittest.TestCase):\n    def test_current(self): self.assertEqual(Path('version.txt').read_text(), 'new\\n')\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "parent"], cwd=repo, check=True)
            (repo / "version.txt").write_text("new\n", encoding="utf-8")
            subprocess.run(["git", "add", "version.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "current"], cwd=repo, check=True)
            spec = ExperimentSpec("parent_commit_comparison", "python -m unittest tests.test_version", parent_commit="HEAD^")
            result = run_experiment(spec, repo, run_command)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.parent_exit_code, 1)
            self.assertEqual(result.parent_commit, "HEAD^")
            self.assertTrue(result.comparison)
            with self.assertRaises(ValueError):
                run_experiment(ExperimentSpec("proposed_regression_test", "python -m unittest tests.test_version"), repo, run_command)

    def test_timeout_and_changed_files_are_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "tests").mkdir()
            (workspace / "tests" / "__init__.py").write_text("", encoding="utf-8")
            (workspace / "tests" / "test_slow.py").write_text("import time; time.sleep(2)\n", encoding="utf-8")
            spec = ExperimentSpec("named_test_or_build", "python -m unittest tests.test_slow", hypothesis_id="h-timeout", timeout_seconds=0.01)
            result = run_experiment(spec, workspace, run_command)
            self.assertTrue(result.timed_out)
            self.assertEqual(result.exit_code, -15)
            self.assertEqual(result.changed_files, [])


if __name__ == "__main__":
    unittest.main()
