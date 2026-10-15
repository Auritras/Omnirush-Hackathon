import json
import os
from pathlib import Path
import tempfile
import unittest

from proofix.runner import CommandPolicy, run_command


class RunnerTests(unittest.TestCase):
    def test_allowlisted_command_runs_in_the_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            result = run_command(
                ["python", "-c", "from pathlib import Path; print(Path.cwd().name)"],
                workspace,
                policy=CommandPolicy(allowed_commands={"python"}),
            )

            self.assertFalse(result.blocked)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.stdout.strip(), workspace.name)
            self.assertIn("PATH", result.environment_metadata["names"])

    def test_unsafe_and_disallowed_commands_are_rejected_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            policy = CommandPolicy(allowed_commands={"python"})
            commands = (
                "python -c 'print(1)' > escaped.txt",
                ["python", str(Path(directory).parent / "outside.py")],
                ["rm", "-rf", "marker"],
            )
            for command in commands:
                with self.subTest(command=command):
                    result = run_command(command, workspace, policy=policy)
                    self.assertTrue(result.blocked)

    def test_timeout_terminates_child_bounds_inline_output_and_writes_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            result = run_command(
                [
                    "python",
                    "-c",
                    "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); print('x' * 10000, flush=True); time.sleep(30)",
                ],
                workspace,
                policy=CommandPolicy(allowed_commands={"python"}, timeout_seconds=0.2, max_inline_output=80),
            )

            self.assertTrue(result.timed_out)
            self.assertTrue(result.blocked is False)
            self.assertLessEqual(len(result.stdout), 80)
            self.assertTrue(result.stdout_artifact_ref)
            artifact = workspace / result.stdout_artifact_ref
            self.assertTrue(artifact.exists())
            self.assertEqual(result.stdout_digest, "sha256:" + __import__("hashlib").sha256(artifact.read_bytes()).hexdigest())

    def test_environment_metadata_has_names_without_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_command(
                ["python", "-c", "import os; print(os.environ['API_KEY'])"],
                Path(directory),
                policy=CommandPolicy(allowed_commands={"python"}),
                environment={"API_KEY": "never-store-this", "BUILD_ID": "42"},
            )

            self.assertNotIn("never-store-this", json.dumps(result.to_dict()))
            self.assertIn("API_KEY", result.environment_metadata["names"])
            self.assertNotIn("never-store-this", result.stderr + result.stdout)

    def test_missing_executable_and_required_input_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            missing_executable = run_command(
                ["proofix-runtime-that-does-not-exist"],
                workspace,
                policy=CommandPolicy(allowed_commands={"proofix-runtime-that-does-not-exist"}),
            )
            missing_input = run_command(
                ["python", "-c", "print('unreachable')"],
                workspace,
                policy=CommandPolicy(allowed_commands={"python"}, required_inputs=("input.txt",)),
            )
            self.assertTrue(missing_executable.blocked)
            self.assertTrue(missing_input.blocked)


if __name__ == "__main__":
    unittest.main()
