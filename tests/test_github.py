from __future__ import annotations

import json
import io
import subprocess
import sys
import unittest
from unittest.mock import patch

from proofix.github import GitHubReader, GitHubRead


class _FakeProcess:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self.stdout = io.BytesIO(stdout if isinstance(stdout, bytes) else stdout.encode())
        self.stderr = io.BytesIO(stderr if isinstance(stderr, bytes) else stderr.encode())
        self.returncode = returncode
        self.pid = 999999

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class GitHubReaderTests(unittest.TestCase):
    def test_commands_are_argument_arrays_without_shell_or_credentials(self):
        reader = GitHubReader("gh")

        self.assertEqual(
            reader.repository_command("octo/example"),
            ["gh", "repo", "view", "octo/example", "--json", "nameWithOwner,description,defaultBranchRef,url"],
        )
        self.assertEqual(
            reader.issue_command("octo/example", 12),
            ["gh", "issue", "view", "12", "--repo", "octo/example", "--json", "title,body,url,number"],
        )
        self.assertEqual(
            reader.pull_request_diff_command("octo/example", "17"),
            ["gh", "pr", "diff", "17", "--repo", "octo/example"],
        )
        self.assertEqual(
            reader.actions_status_command("octo/example", "987"),
            ["gh", "run", "view", "987", "--repo", "octo/example", "--json", "status,conclusion,workflowName,headBranch,url"],
        )
        self.assertEqual(
            reader.actions_logs_command("octo/example", "987"),
            ["gh", "run", "view", "987", "--repo", "octo/example", "--log"],
        )
        serialized = json.dumps(reader.actions_logs_command("octo/example", "987"))
        self.assertNotIn("token", serialized.casefold())
        self.assertNotIn("secret", serialized.casefold())
        self.assertNotIn("$", serialized)
        self.assertNotIn(";", serialized)

    def test_successful_json_and_text_reads_are_bounded_and_redacted(self):
        reader = GitHubReader("gh", max_output_bytes=80)
        completed = _FakeProcess(stdout=json.dumps({"nameWithOwner": "octo/example", "description": "api_key=hidden"}))
        with patch("proofix.github.shutil.which", return_value="/usr/bin/gh"), patch(
            "proofix.github.subprocess.Popen", return_value=completed
        ) as popen:
            result = reader.repository_metadata("octo/example")
        self.assertIsInstance(result, GitHubRead)
        self.assertEqual(result.status, "ok")
        self.assertLessEqual(result.stdout_size, 80)
        self.assertEqual(result.data["description"], "api_key=[REDACTED]")
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], reader.repository_command("octo/example"))
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertNotIn("capture_output", popen.call_args.kwargs)
        self.assertIs(popen.call_args.kwargs["stdout"], subprocess.PIPE)

    def test_oversized_stdout_and_stderr_are_blocked_at_the_process_boundary(self):
        for stream, output in (("stdout", b"x" * 200), ("stderr", b"warning " * 40)):
            reader = GitHubReader("gh", max_output_bytes=32)
            process = _FakeProcess(**{stream: output})
            with patch("proofix.github.shutil.which", return_value="/usr/bin/gh"), patch(
                "proofix.github.subprocess.Popen", return_value=process
            ):
                result = reader.actions_logs("octo/example", "987")
            self.assertEqual(result.status, "blocked")
            self.assertTrue(result.truncated)
            self.assertGreater(getattr(result, f"{stream}_size"), 32)
            self.assertIsNone(result.data)
            self.assertLessEqual(len(result.to_dict()["command"][-1]), 64)

    def test_timeout_is_a_blocked_read(self):
        reader = GitHubReader("gh", max_output_bytes=32, timeout_seconds=0.05)
        command = [sys.executable, "-c", "import time; time.sleep(2)"]
        with patch("proofix.github.shutil.which", return_value="/usr/bin/gh"):
            result = reader._read(command, json_output=False)
        self.assertEqual(result.status, "blocked")
        self.assertTrue(result.timed_out)
        self.assertIn("timed out", result.error or "")

    def test_missing_cli_and_auth_are_blocked_with_next_steps(self):
        missing = GitHubReader("gh")
        with patch("proofix.github.shutil.which", return_value=None):
            result = missing.repository_metadata("octo/example")
        self.assertEqual(result.status, "blocked")
        self.assertTrue(result.next_steps)
        self.assertNotIn("gh", result.error or "")

        auth = GitHubReader("gh")
        completed = _FakeProcess(returncode=4, stderr="not logged into any GitHub hosts; run gh auth login")
        with patch("proofix.github.shutil.which", return_value="/usr/bin/gh"), patch(
            "proofix.github.subprocess.Popen", return_value=completed
        ):
            result = auth.repository_metadata("octo/example")
        self.assertEqual(result.status, "blocked")
        self.assertTrue(any("auth" in item.casefold() for item in result.next_steps))
        self.assertNotIn("not logged", result.error or "")

    def test_reader_exposes_no_default_github_write_operations(self):
        names = {name.casefold() for name in dir(GitHubReader) if not name.startswith("_")}
        self.assertFalse(names & {"create_branch", "push", "create_pull_request", "merge", "write"})

    def test_invalid_identifiers_never_reach_gh(self):
        reader = GitHubReader("gh")
        for repository in ("octo/example; touch /tmp/pwned", "octo/child/repo", "/tmp/repo", "octo/..", "octo/ghp_1234567890"):
            with self.subTest(repository=repository), self.assertRaises(ValueError):
                reader.repository_command(repository)
        for identifier in ("$(whoami)", "foo/bar", "../987", "/tmp/987", "C:\\tmp\\987", "ghp_1234567890"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                reader.actions_logs_command("octo/example", identifier)

    def test_returned_command_records_redact_token_shaped_values(self):
        result = GitHubRead("blocked", command=("gh", "repo", "view", "ghp_1234567890"))
        self.assertNotIn("ghp_1234567890", result.command)
        self.assertNotIn("ghp_1234567890", json.dumps(result.to_dict()))


if __name__ == "__main__":
    unittest.main()
