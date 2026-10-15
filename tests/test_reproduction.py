import tempfile
import unittest
from pathlib import Path

from proofix.reproduction import ReproductionStatus, compare_failure
from proofix.runner import CommandPolicy, run_command


def _script(workspace: Path, name: str, source: str) -> str:
    path = workspace / name
    path.write_text(source, encoding="utf-8")
    return name


class ReproductionTests(unittest.TestCase):
    def _run(self, workspace: Path, source: str, *, timeout_seconds=5, max_inline_output=4096):
        script = _script(workspace, "case.py", source)
        return run_command(
            ["python", script],
            workspace,
            policy=CommandPolicy(allowed_commands={"python"}, timeout_seconds=timeout_seconds, max_inline_output=max_inline_output),
        )

    def test_matching_failure_is_reproduced_with_normalized_paths_timestamps_and_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(
                Path(directory),
                "import sys\nprint('password=runtime-secret')\nprint('failure at /tmp/build-123.py:2026-10-15T12:34:56Z')\nprint('worker 92341')\nsys.exit(2)\n",
            )
            assessment = compare_failure(
                {
                    "exit_code": 2,
                    "failure_text": "password=other-secret\nfailure at /home/runner/project/app.py:2025-01-01T00:00:00Z\nworker 10002",
                },
                result,
            )
            self.assertEqual(assessment.status, ReproductionStatus.reproduced)
            self.assertNotIn("other-secret", assessment.reason + assessment.normalized_evidence)
            self.assertNotIn("runtime-secret", assessment.reason + assessment.normalized_evidence)

    def test_full_artifact_content_is_compared_beyond_inline_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(
                Path(directory),
                "import sys\nprint('prefix-' + ('x' * 3000) + '-needle')\nsys.exit(2)\n",
                max_inline_output=20,
            )
            self.assertNotIn("needle", result.stdout)
            assessment = compare_failure({"exit_code": 2, "failure_text": "needle"}, result)
            self.assertEqual(assessment.status, ReproductionStatus.reproduced)

    def test_stderr_failure_is_compared_from_full_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(
                Path(directory),
                "import sys\nprint('stderr needle', file=sys.stderr)\nsys.exit(3)\n",
            )
            assessment = compare_failure({"exit_code": 3, "failure_text": "stderr needle"}, result)
            self.assertEqual(assessment.status, ReproductionStatus.reproduced)

    def test_equivalent_redacted_failure_matches_without_secret_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(Path(directory), "import sys\nprint('API_KEY=runtime-secret')\nsys.exit(1)\n")
            assessment = compare_failure({"exit_code": 1, "failure_text": "API_KEY=input-secret"}, result)
            self.assertEqual(assessment.status, ReproductionStatus.reproduced)
            self.assertNotIn("input-secret", repr(assessment))
            self.assertNotIn("runtime-secret", repr(assessment))

    def test_success_and_different_failure_are_not_reproduced(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            success = self._run(workspace, "print('all good')\n")
            different = self._run(workspace, "import sys\nprint('other failure')\nsys.exit(1)\n")
            self.assertEqual(compare_failure({"exit_code": 1, "failure_text": "failure"}, success).status, ReproductionStatus.not_reproduced)
            self.assertEqual(compare_failure({"exit_code": 2, "failure_text": "expected failure"}, different).status, ReproductionStatus.not_reproduced)

    def test_blocked_runner_result_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(Path(directory), "print('unused')\n")
            result.blocked = True
            result.blocked_reason = "required runtime unavailable"
            assessment = compare_failure({"exit_code": 1, "failure_text": "failure"}, result)
            self.assertEqual(assessment.status, ReproductionStatus.blocked)


if __name__ == "__main__":
    unittest.main()
