import tempfile
import unittest
from pathlib import Path
import subprocess

from proofix.patches import approve_patch, is_patch_approved, reject_patch, request_another_diagnosis, validate_unified_diff


GOOD_DIFF = """diff --git a/case.py b/case.py
index 1234567..89abcde 100644
--- a/case.py
+++ b/case.py
@@ -1 +1 @@
-print('bad')
+print('good')
"""


class PatchTests(unittest.TestCase):
    def test_valid_diff_has_digest_and_files(self):
        with tempfile.TemporaryDirectory() as directory:
            proposal = validate_unified_diff(GOOD_DIFF, Path(directory))
            self.assertTrue(proposal.digest.startswith("sha256:"))
            self.assertEqual(proposal.files, ["case.py"])
            self.assertEqual(proposal.diff, GOOD_DIFF)

    def test_quoted_paths_and_pure_rename_or_mode_metadata_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            quoted = '''diff --git "a/space name.txt" "b/space name.txt"\nindex 1111111..2222222 100644\n--- "a/space name.txt"\n+++ "b/space name.txt"\n@@ -1 +1 @@\n-old\n+new\n'''
            proposal = validate_unified_diff(quoted, root)
            self.assertEqual(proposal.files, ["space name.txt"])
            rename = '''diff --git "a/old name.txt" "b/new name.txt"\nsimilarity index 100%\nrename from old name.txt\nrename to new name.txt\n'''
            self.assertEqual(validate_unified_diff(rename, root).files, ["new name.txt"])
            mode = "diff --git a/script.sh b/script.sh\nold mode 100644\nnew mode 100755\n"
            self.assertEqual(validate_unified_diff(mode, root).files, ["script.sh"])

    def test_real_git_unquoted_space_rename_diff_is_parsed_from_file_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "proofix@example.test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "ProofFix Tests"], cwd=repo, check=True)
            (repo / "old name.txt").write_text("same\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "old"], cwd=repo, check=True)
            (repo / "new b").mkdir()
            subprocess.run(["git", "mv", "old name.txt", "new b/file.txt"], cwd=repo, check=True)
            # Use Git's rename form, which is unquoted for this fixture.
            diff = subprocess.check_output(["git", "diff", "--cached", "--no-ext-diff", "--no-color"], cwd=repo, text=True)
            self.assertIn("diff --git a/old name.txt b/new b/file.txt", diff)
            self.assertEqual(validate_unified_diff(diff, repo).files, ["new b/file.txt"])

    def test_real_git_unquoted_space_new_file_uses_non_dev_null_header(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "proofix@example.test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "ProofFix Tests"], cwd=repo, check=True)
            new_file = repo / "new b" / "file.txt"
            new_file.parent.mkdir()
            new_file.write_text("new\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            diff = subprocess.check_output(["git", "diff", "--cached", "--no-ext-diff", "--no-color"], cwd=repo, text=True)
            self.assertIn("--- /dev/null", diff)
            self.assertIn("+++ b/new b/file.txt", diff)
            self.assertEqual(validate_unified_diff(diff, repo).files, ["new b/file.txt"])

    def test_malformed_absolute_traversal_and_scope_diffs_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            malformed = "diff --git a/case.py b/case.py\n--- a/case.py\n+++ b/case.py\n@@ nope\n"
            absolute = GOOD_DIFF.replace("a/case.py b/case.py", "a//etc/passwd b//etc/passwd").replace("a/case.py", "/etc/passwd").replace("b/case.py", "/etc/passwd")
            traversal = GOOD_DIFF.replace("a/case.py b/case.py", "a/../outside b/../outside").replace("a/case.py", "a/../outside").replace("b/case.py", "b/../outside")
            out_of_scope = GOOD_DIFF.replace("case.py", ".git/config")
            for diff in (malformed, absolute, traversal, out_of_scope):
                with self.subTest(diff=diff), self.assertRaises(ValueError):
                    validate_unified_diff(diff, root)

    def test_approval_requires_matching_digest_state_actor_and_time(self):
        with tempfile.TemporaryDirectory() as directory:
            proposal = validate_unified_diff(GOOD_DIFF, Path(directory))
            with self.assertRaises(ValueError):
                approve_patch("run-patch", "sha256:wrong", "alice", "2026-10-15T00:00:00Z")
            with self.assertRaises(ValueError):
                approve_patch("run-patch", proposal.digest, "", "2026-10-15T00:00:00Z")
            with self.assertRaises(ValueError):
                approve_patch("run-patch", proposal.digest, "alice", "")
            approval = approve_patch("run-patch", proposal.digest, "alice", "2026-10-15T00:00:00Z", proposal=proposal, current_digest=proposal.digest)
            self.assertEqual(approval.patch_digest, proposal.digest)
            self.assertTrue(is_patch_approved(proposal, "run-patch"))
            self.assertFalse(is_patch_approved(proposal))
            self.assertFalse(is_patch_approved(proposal, "run-other"))
            with self.assertRaises(ValueError):
                approve_patch("run-patch", proposal.digest, "alice", "2026-10-15T00:00:00Z")
            with self.assertRaises(ValueError):
                approve_patch("run-patch", proposal.digest, "alice", "2026-10-15T00:00:00Z", proposal=proposal, current_digest=proposal.digest)
            with self.assertRaises(ValueError):
                reject_patch("run-patch", "alice", "2026-10-15T00:00:01Z", digest=proposal.digest, proposal=proposal, current_digest=proposal.digest)

    def test_approval_rejects_a_digest_from_another_run_proposal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = validate_unified_diff(GOOD_DIFF, root)
            second = validate_unified_diff(GOOD_DIFF.replace("print('good')", "print('other')"), root)
            approve_patch("run-one", first.digest, "alice", "2026-10-15T00:00:00Z", proposal=first, current_digest=first.digest)
            with self.assertRaises(ValueError):
                approve_patch("run-two", first.digest, "alice", "2026-10-15T00:00:01Z", proposal=second, current_digest=second.digest)

    def test_rejection_is_recorded_without_applying_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            proposal = validate_unified_diff(GOOD_DIFF, Path(directory))
            rejection = reject_patch("run-reject", "alice", "2026-10-15T00:00:00Z", digest=proposal.digest, proposal=proposal, current_digest=proposal.digest)
            self.assertEqual(rejection.action, "rejected")
            self.assertFalse((Path(directory) / "case.py").exists())

    def test_audit_helpers_require_actor_and_canonical_utc_time(self):
        with self.assertRaises(ValueError):
            reject_patch("run-audit", "", "2026-10-15T00:00:00Z", digest="sha256:" + "0" * 64)
        with self.assertRaises(ValueError):
            reject_patch("run-audit", "alice", "2026-10-15T00:00:00+00:00")
        with self.assertRaises(ValueError):
            request_another_diagnosis("run-audit", "", "2026-10-15T00:00:00Z")
        with self.assertRaises(ValueError):
            request_another_diagnosis("run-audit", "alice", "2026-10-15T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
