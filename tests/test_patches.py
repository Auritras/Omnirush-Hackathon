import tempfile
import unittest
from pathlib import Path

from proofix.patches import approve_patch, reject_patch, validate_unified_diff


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
            rejection = reject_patch("run-reject", "alice", "2026-10-15T00:00:00Z", digest=proposal.digest)
            self.assertEqual(rejection.action, "rejected")
            self.assertFalse((Path(directory) / "case.py").exists())


if __name__ == "__main__":
    unittest.main()
