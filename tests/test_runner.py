import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys
import time
import unittest

from proofix.runner import CommandPolicy, run_command


def _script(workspace: Path, name: str, source: str) -> str:
    path = workspace / name
    path.write_text(source, encoding="utf-8")
    return name


def _assert_pid_gone(test_case: unittest.TestCase, pid_file: Path) -> None:
    deadline = time.monotonic() + 1.0
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    test_case.assertTrue(pid_file.exists(), "child did not record its pid")
    pid = int(pid_file.read_text(encoding="utf-8"))
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        if sys.platform.startswith("linux"):
            proc_stat = Path(f"/proc/{pid}/stat")
            try:
                state = proc_stat.read_text(encoding="utf-8").split()[2]
            except (FileNotFoundError, IndexError):
                return
            if state == "Z":
                return
        time.sleep(0.02)
    test_case.fail(f"child process {pid} remained alive after timeout")


class RunnerTests(unittest.TestCase):
    def test_python_investigation_uses_runtime_path(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "tests").mkdir()
            (workspace / "tests" / "__init__.py").write_text("", encoding="utf-8")
            (workspace / "tests" / "test_path.py").write_text("import unittest\nclass PathTest(unittest.TestCase):\n    def test_runtime(self): self.assertTrue(True)\n", encoding="utf-8")
            result = run_command("python -m unittest tests.test_path", workspace, policy=CommandPolicy(workspace_root=workspace, artifact_dir=workspace / "artifacts"))
            self.assertFalse(result.blocked, result.blocked_reason)
            self.assertEqual(result.exit_code, 0)

    def test_allowlisted_command_runs_in_the_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            script = _script(workspace, "cwd.py", "from pathlib import Path\nprint(Path.cwd().name)\n")
            result = run_command(
                ["python", script],
                workspace,
                policy=CommandPolicy(allowed_commands={"python"}),
            )

            self.assertFalse(result.blocked)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.stdout.strip(), workspace.name)
            self.assertIn("PATH", result.environment_metadata["names"])

    def test_shell_and_eval_forms_are_rejected_without_executing_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            marker = workspace / "marker"
            policy = CommandPolicy(allowed_commands={"python", "sh", "bash", "node"})
            commands = (
                ["sh", "-c", f"touch {marker}"],
                ["bash", "-c", f"touch {marker}"],
                ["sh", "-ce", f"touch {marker}"],
                ["bash", "-ec", f"touch {marker}"],
                ["bash", "-xec", f"touch {marker}"],
                ["python", "-c", f"open({str(marker)!r}, 'w').write('x')"],
                ["node", "-e", f"require('fs').writeFileSync({str(marker)!r}, 'x')"],
                "python script.py > escaped.txt",
            )
            for command in commands:
                with self.subTest(command=command):
                    result = run_command(command, workspace, policy=policy)
                    self.assertTrue(result.blocked)
            self.assertFalse(marker.exists())
            self.assertFalse((workspace / "escaped.txt").exists())

    def test_versioned_and_custom_allowlisted_interpreters_reject_bundled_eval_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            marker = workspace / "marker"
            versioned = "python3.11"
            policy = CommandPolicy(
                allowed_commands={versioned, "custom-runner"},
                interpreter_commands={"custom-runner"},
            )
            commands = (
                [f"./{versioned}", "-c", f"open({str(marker)!r}, 'w').write('versioned')"],
                [f"./{versioned}", "-cpass"],
            )
            versioned_path = workspace / versioned
            custom_path = workspace / "custom-runner"
            shutil.copy2(sys.executable, versioned_path)
            shutil.copy2(sys.executable, custom_path)
            (workspace / versioned).chmod(0o755)
            (workspace / "custom-runner").chmod(0o755)
            subprocess.run(
                [str(versioned_path), "-c", f"open({str(marker)!r}, 'w').write('versioned')"],
                cwd=workspace,
                check=True,
            )
            self.assertEqual(marker.read_text(encoding="utf-8"), "versioned")
            marker.unlink()
            subprocess.run(
                [str(custom_path), "-c", f"open({str(marker)!r}, 'w').write('custom')"],
                cwd=workspace,
                check=True,
            )
            self.assertEqual(marker.read_text(encoding="utf-8"), "custom")
            marker.unlink()
            commands += (
                ("./custom-runner", "-e", f"open({str(marker)!r}, 'w').write('custom')"),
                ("./custom-runner", "-cpass"),
            )
            for command in commands:
                with self.subTest(command=command):
                    result = run_command(command, workspace, policy=policy)
                    self.assertTrue(result.blocked)
            self.assertFalse(marker.exists())

    def test_ordinary_allowlisted_tool_keeps_legitimate_e_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            result = run_command(
                ["echo", "-e", "hello"],
                workspace,
                policy=CommandPolicy(allowed_commands={"echo"}),
            )
            self.assertFalse(result.blocked)
            self.assertEqual(result.stdout, "hello\n")

    def test_absolute_paths_cwd_escapes_and_disallowed_commands_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            outside = root.parent / f"{root.name}-outside"
            workspace.mkdir()
            outside.mkdir()
            policy = CommandPolicy(allowed_commands={"python"}, workspace_root=root)
            script = _script(workspace, "touch.py", "from pathlib import Path\nPath('marker').touch()\n")
            self.assertTrue(run_command(["python", str(outside / "outside.py")], workspace, policy=policy).blocked)
            self.assertTrue(run_command(["python", "../outside/outside.py"], workspace, policy=policy).blocked)
            self.assertTrue(run_command(["rm", "-rf", "marker"], workspace, policy=policy).blocked)
            self.assertTrue(run_command(["python", script], outside, policy=policy).blocked)
            outside.rmdir()

    @unittest.skipUnless(sys.platform.startswith("linux"), "child process liveness assertion requires Linux")
    def test_timeout_terminates_child_process_and_bounds_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            child = _script(
                workspace,
                "child.py",
                "import pathlib, sys, time\npathlib.Path(sys.argv[1]).write_text(str(__import__('os').getpid()))\ntime.sleep(30)\npathlib.Path(sys.argv[2]).write_text('child survived')\n",
            )
            parent = _script(
                workspace,
                "parent.py",
                "import pathlib, subprocess, sys, time\nsubprocess.Popen([sys.executable, 'child.py', sys.argv[1], sys.argv[2]])\nprint('x' * 100000, flush=True)\ntime.sleep(30)\n",
            )
            pid_file = workspace / "child.pid"
            survived = workspace / "child-survived"
            result = run_command(
                ["python", parent, pid_file.name, survived.name],
                workspace,
                policy=CommandPolicy(allowed_commands={"python"}, timeout_seconds=0.3, max_inline_output=80, max_output_bytes=2048),
            )

            self.assertTrue(result.timed_out)
            self.assertFalse(result.blocked)
            self.assertLessEqual(len(result.stdout), 80)
            self.assertTrue(result.stdout_truncated)
            self.assertTrue(result.stdout_artifact_ref)
            artifact = workspace / result.stdout_artifact_ref
            self.assertTrue(artifact.exists())
            self.assertEqual(result.stdout_digest, "sha256:" + __import__("hashlib").sha256(artifact.read_bytes()).hexdigest())
            time.sleep(0.2)
            self.assertFalse(survived.exists(), "child process continued after timeout")
            _assert_pid_gone(self, pid_file)

    @unittest.skipUnless(sys.platform.startswith("linux"), "child process liveness assertion requires Linux")
    def test_timeout_escalates_after_parent_exits_and_child_ignores_sigterm(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _script(
                workspace,
                "ignore-term-child.py",
                "import pathlib, signal, sys, time\nsignal.signal(signal.SIGTERM, lambda *_: None)\npathlib.Path(sys.argv[1]).write_text(str(__import__('os').getpid()))\ntime.sleep(30)\npathlib.Path(sys.argv[2]).write_text('child survived')\n",
            )
            parent = _script(
                workspace,
                "ignore-term-parent.py",
                "import subprocess, sys, time\nsubprocess.Popen([sys.executable, 'ignore-term-child.py', sys.argv[1], sys.argv[2]])\ntime.sleep(30)\n",
            )
            pid_file = workspace / "ignored-child.pid"
            survived = workspace / "ignored-child-survived"
            started = time.monotonic()
            result = run_command(
                ["python", parent, pid_file.name, survived.name],
                workspace,
                policy=CommandPolicy(allowed_commands={"python"}, timeout_seconds=0.2),
            )
            elapsed = time.monotonic() - started
            self.assertTrue(result.timed_out)
            self.assertLess(elapsed, 2.0)
            time.sleep(0.2)
            self.assertFalse(survived.exists(), "SIGTERM-ignoring child survived escalation")
            _assert_pid_gone(self, pid_file)

    @unittest.skipUnless(os.name == "posix", "process-group cleanup is POSIX-only")
    def test_closed_pipes_do_not_bypass_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            script = _script(
                workspace,
                "closed-pipes.py",
                "import pathlib, sys, time\nsys.stdout.close()\nsys.stderr.close()\ntime.sleep(30)\npathlib.Path('closed-pipes-survived').write_text('survived')\n",
            )
            started = time.monotonic()
            result = run_command(
                ["python", script],
                workspace,
                policy=CommandPolicy(allowed_commands={"python"}, timeout_seconds=0.2),
            )
            elapsed = time.monotonic() - started
            self.assertTrue(result.timed_out)
            self.assertLess(elapsed, 2.0)
            self.assertFalse((workspace / "closed-pipes-survived").exists())

    def test_stderr_has_a_full_artifact_and_environment_policy_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            script = _script(workspace, "stderr.py", "import os, sys\nprint(os.environ.get('MODE', 'missing'), file=sys.stderr)\n")
            denied = run_command(
                ["python", script],
                workspace,
                policy=CommandPolicy(allowed_commands={"python"}, environment_allowlist={"OTHER"}),
                environment={"MODE": "test"},
            )
            self.assertTrue(denied.blocked)
            result = run_command(
                ["python", script],
                workspace,
                policy=CommandPolicy(allowed_commands={"python"}, environment_allowlist={"MODE"}),
                environment={"MODE": "test"},
            )
            self.assertEqual(result.exit_code, 0)
            self.assertEqual((workspace / result.stderr_artifact_ref).read_text(encoding="utf-8"), "test\n")
            self.assertEqual(result.stderr, "test\n")

    def test_environment_metadata_has_names_without_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            script = _script(workspace, "env.py", "import os\nprint(os.environ['API_KEY'])\n")
            result = run_command(
                ["python", script],
                workspace,
                policy=CommandPolicy(allowed_commands={"python"}),
                environment={"API_KEY": "never-store-this", "BUILD_ID": "42"},
            )

            self.assertNotIn("never-store-this", json.dumps(result.to_dict()))
            self.assertIn("API_KEY", result.environment_metadata["names"])
            self.assertNotIn("never-store-this", result.stderr + result.stdout)

    def test_artifact_symlink_escape_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            outside = root / "outside"
            workspace.mkdir()
            outside.mkdir()
            (workspace / "artifacts").symlink_to(outside, target_is_directory=True)
            script = _script(workspace, "ok.py", "print('ok')\n")
            result = run_command(
                ["python", script],
                workspace,
                policy=CommandPolicy(allowed_commands={"python"}, workspace_root=root, artifact_dir=workspace / "artifacts"),
            )
            self.assertTrue(result.blocked)
            self.assertFalse(list(outside.iterdir()))

    def test_final_artifact_symlink_is_not_replaced_or_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            outside = root / "outside"
            workspace.mkdir()
            outside.mkdir()
            artifacts = workspace / ".proofix-artifacts"
            artifacts.mkdir()
            digest = hashlib.sha256(b"ok\n").hexdigest()
            (artifacts / f"stdout-{digest}.log").symlink_to(outside / "escaped.log")
            script = _script(workspace, "ok.py", "print('ok')\n")
            result = run_command(["python", script], workspace, policy=CommandPolicy(allowed_commands={"python"}))
            self.assertTrue(result.blocked)
            self.assertFalse((outside / "escaped.log").exists())

    def test_missing_executable_and_required_input_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            missing_executable = run_command(
                ["proofix-runtime-that-does-not-exist"],
                workspace,
                policy=CommandPolicy(allowed_commands={"proofix-runtime-that-does-not-exist"}),
            )
            missing_input = run_command(
                ["python", "missing.py"],
                workspace,
                policy=CommandPolicy(allowed_commands={"python"}, required_inputs=("input.txt",)),
            )
            self.assertTrue(missing_executable.blocked)
            self.assertTrue(missing_input.blocked)


if __name__ == "__main__":
    unittest.main()
