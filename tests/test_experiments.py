import hashlib
import tempfile
import unittest
from pathlib import Path

from proofix.experiments import ExperimentSpec, run_experiment
from proofix.runner import CommandPolicy, run_command


class ExperimentTests(unittest.TestCase):
    def test_each_named_experiment_records_evidence_and_hypothesis(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "case.py").write_text("print('ok')\n", encoding="utf-8")
            specs = [
                ExperimentSpec("named_test_or_build", "python case.py", hypothesis_id="h-1"),
                ExperimentSpec("controlled_environment", "python case.py", hypothesis_id="h-2", environment={"MODE": "test"}),
                ExperimentSpec("temporary_instrumentation", "python case.py", hypothesis_id="h-3", instrumentation={"trace.txt": "trace\n"}),
                ExperimentSpec("parent_commit_comparison", "python case.py", hypothesis_id="h-4"),
                ExperimentSpec("dependency_runtime_inspection", "python --version", hypothesis_id="h-5"),
                ExperimentSpec("proposed_regression_test", "python case.py", hypothesis_id="h-6"),
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

    def test_timeout_and_changed_files_are_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "slow.py").write_text("import time; time.sleep(2)\n", encoding="utf-8")
            spec = ExperimentSpec("named_test_or_build", "python slow.py", hypothesis_id="h-timeout", timeout_seconds=0.01)
            result = run_experiment(spec, workspace, run_command)
            self.assertTrue(result.timed_out)
            self.assertEqual(result.exit_code, -15)
            self.assertEqual(result.changed_files, [])


if __name__ == "__main__":
    unittest.main()
