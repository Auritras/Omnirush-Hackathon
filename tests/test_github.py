from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from proofix.github import GitHubReader, GitHubRead


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
        completed = subprocess.CompletedProcess(
            ["gh"],
            0,
            stdout=json.dumps({"nameWithOwner": "octo/example", "description": "api_key=hidden"}),
            stderr="",
        )
        with patch("proofix.github.shutil.which", return_value="/usr/bin/gh"), patch(
            "proofix.github.subprocess.run", return_value=completed
        ) as run:
            result = reader.repository_metadata("octo/example")
        self.assertIsInstance(result, GitHubRead)
        self.assertEqual(result.status, "ok")
        self.assertLessEqual(result.raw_size, 80)
        self.assertEqual(result.data["description"], "api_key=[REDACTED]")
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], reader.repository_command("octo/example"))
        self.assertFalse(run.call_args.kwargs["shell"])

    def test_missing_cli_and_auth_are_blocked_with_next_steps(self):
        missing = GitHubReader("gh")
        with patch("proofix.github.shutil.which", return_value=None):
            result = missing.repository_metadata("octo/example")
        self.assertEqual(result.status, "blocked")
        self.assertTrue(result.next_steps)
        self.assertNotIn("gh", result.error or "")

        auth = GitHubReader("gh")
        completed = subprocess.CompletedProcess(
            ["gh"], 4, stdout="", stderr="not logged into any GitHub hosts; run gh auth login"
        )
        with patch("proofix.github.shutil.which", return_value="/usr/bin/gh"), patch(
            "proofix.github.subprocess.run", return_value=completed
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
        with self.assertRaises(ValueError):
            reader.repository_command("octo/example; touch /tmp/pwned")
        with self.assertRaises(ValueError):
            reader.actions_logs_command("octo/example", "$(whoami)")


if __name__ == "__main__":
    unittest.main()
