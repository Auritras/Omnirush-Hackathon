import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


FIXTURE = Path(__file__).parents[1] / "fixtures" / "broken-parser-repo"


class FixtureRepositoryTests(unittest.TestCase):
    def _copy(self):
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name) / "repo"
        shutil.copytree(FIXTURE, root)
        return directory, root

    def _test_command(self, root):
        return [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]

    def test_broken_fixture_fails_then_intended_root_cause_patch_passes(self):
        temporary, root = self._copy()
        try:
            before = subprocess.run(self._test_command(root), cwd=root, text=True, capture_output=True)
            self.assertNotEqual(before.returncode, 0)
            self.assertIn("ValueError: too many values to unpack", before.stdout + before.stderr)
            source = (root / "parser.py").read_text(encoding="utf-8")
            self.assertIn("split(\"=\")", source)
            self.assertIn("equals", (root / "tests" / "test_parser.py").read_text(encoding="utf-8"))
            (root / "parser.py").write_text(source.replace('split("=")', 'split("=", 1)'), encoding="utf-8")
            after = subprocess.run(self._test_command(root), cwd=root, text=True, capture_output=True)
            self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
            reset = subprocess.run(["sh", "reset.sh"], cwd=root, text=True, capture_output=True)
            self.assertEqual(reset.returncode, 0, reset.stderr)
            rerun = subprocess.run(self._test_command(root), cwd=root, text=True, capture_output=True)
            self.assertNotEqual(rerun.returncode, 0)
        finally:
            temporary.cleanup()

    def test_reset_restores_broken_state_offline_and_fixture_is_dependency_free(self):
        temporary, root = self._copy()
        try:
            parser = root / "parser.py"
            parser.write_text(parser.read_text(encoding="utf-8").replace('split("=")', 'split("=", 1)'), encoding="utf-8")
            result = subprocess.run(["sh", "reset.sh"], cwd=root, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('split("=")', parser.read_text(encoding="utf-8"))
            failed = subprocess.run(self._test_command(root), cwd=root, text=True, capture_output=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("ValueError: too many values to unpack", failed.stdout + failed.stderr)
            self.assertNotIn("requests", " ".join(path.name for path in root.rglob("*")))
            self.assertFalse((root / "requirements.txt").exists())
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
