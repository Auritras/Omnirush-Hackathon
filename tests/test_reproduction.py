import tempfile
import unittest
from pathlib import Path

from proofix.reproduction import ReproductionStatus, compare_failure
from proofix.runner import CommandPolicy, run_command


class ReproductionTests(unittest.TestCase):
    def _run(self, workspace, code, *, timeout_seconds=5):
        return run_command(
            ["python", "-c", code],
            workspace,
            policy=CommandPolicy(allowed_commands={"python"}, timeout_seconds=timeout_seconds),
        )

    def test_matching_failure_is_reproduced(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(Path(directory), "import sys; print('failure at /tmp/build-123.py:2026-10-15T12:34:56Z'); sys.exit(2)")
            assessment = compare_failure(
                {"exit_code": 2, "failure_text": "failure at /home/runner/project/app.py:2025-01-01T00:00:00Z"},
                result,
            )
            self.assertEqual(assessment.status, ReproductionStatus.reproduced)

    def test_success_and_different_failure_are_not_reproduced(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            success = self._run(workspace, "print('all good')")
            different = self._run(workspace, "import sys; print('other failure'); sys.exit(1)")
            self.assertEqual(compare_failure({"exit_code": 1, "failure_text": "failure"}, success).status, ReproductionStatus.not_reproduced)
            self.assertEqual(compare_failure({"exit_code": 2, "failure_text": "expected failure"}, different).status, ReproductionStatus.not_reproduced)

    def test_blocked_runner_result_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(Path(directory), "print('unused')")
            result.blocked = True
            result.blocked_reason = "required runtime unavailable"
            assessment = compare_failure({"exit_code": 1, "failure_text": "failure"}, result)
            self.assertEqual(assessment.status, ReproductionStatus.blocked)


if __name__ == "__main__":
    unittest.main()
