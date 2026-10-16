import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from proofix.paths import ensure_contained, resolve_repository, safe_artifact_path
from proofix.redaction import redact_environment, redact_text
from proofix.worktree import WorkspaceManager


class PathSafetyTests(unittest.TestCase):
    def test_repository_and_artifact_paths_are_contained_after_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowed = root / "allowed"
            repository = allowed / "repo"
            repository.mkdir(parents=True)
            self.assertEqual(resolve_repository(repository, [allowed]), repository.resolve())
            self.assertEqual(ensure_contained(repository / "src", allowed), (repository / "src").resolve())
            self.assertEqual(safe_artifact_path(root, "logs/output.txt"), (root / "logs/output.txt").resolve())
            for value in ("../outside", "nested/../../outside", "/tmp/output", "C:\\secret\\output", "C:/secret/output", "\\\\server\\share", "\\leading\\path"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    safe_artifact_path(root, value)
            with self.assertRaises(ValueError):
                resolve_repository(root / "outside", [allowed])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowed = root / "allowed"
            outside = root / "outside"
            allowed.mkdir()
            outside.mkdir()
            (allowed / "link").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                ensure_contained(allowed / "link" / "file.txt", allowed)
            with self.assertRaises(ValueError):
                safe_artifact_path(allowed, "link/file.txt")


class RedactionTests(unittest.TestCase):
    def test_redacts_tokens_keys_private_blocks_and_env_lines(self):
        source = (
            "Authorization: Bearer bearer-secret\n"
            "github=ghp_1234567890abcdefghijklmnopqrstuvwxyz\n"
            "password = \"super-secret\"\n"
            "API_KEY: api-secret\n"
            "PRIVATE=-----BEGIN PRIVATE KEY-----\nsecret-material\n-----END PRIVATE KEY-----\n"
            "DATABASE_URL=postgres://user:password@host/db\n"
        )
        redacted, findings = redact_text(source)
        self.assertNotIn("bearer-secret", redacted)
        self.assertNotIn("ghp_1234567890abcdefghijklmnopqrstuvwxyz", redacted)
        self.assertNotIn("super-secret", redacted)
        self.assertNotIn("api-secret", redacted)
        self.assertNotIn("secret-material", redacted)
        self.assertIn("Authorization", redacted)
        self.assertEqual(redacted.count("\n"), source.count("\n"))
        self.assertTrue(findings)

    def test_environment_keeps_names_and_safe_metadata_but_masks_secret_values(self):
        redacted = redact_environment(
            {
                "API_KEY": "do-not-export",
                "GITHUB_TOKEN": "ghp_1234567890abcdefghijklmnopqrstuvwxyz",
                "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
                "AZURE_CLIENT_SECRET": "azure-client-secret",
                "GOOGLE_APPLICATION_CREDENTIALS": "/secrets/service-account.json",
                "GOOGLE_CLOUD_KEYFILE_JSON": '{"private_key":"-----BEGIN PRIVATE KEY-----\nembedded-secret\n-----END PRIVATE KEY-----"}',
                "DOCKER_AUTH_CONFIG": "{\"auths\":{}}",
                "CI": "true",
                "BUILD_NUMBER": "42",
            }
        )
        self.assertEqual(redacted["CI"], "true")
        self.assertEqual(redacted["BUILD_NUMBER"], "42")
        self.assertNotIn("do-not-export", redacted.values())
        self.assertNotIn("ghp_1234567890abcdefghijklmnopqrstuvwxyz", redacted.values())
        for name in ("API_KEY", "GITHUB_TOKEN", "AWS_ACCESS_KEY_ID", "AZURE_CLIENT_SECRET", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_KEYFILE_JSON", "DOCKER_AUTH_CONFIG"):
            self.assertEqual(redacted[name], "[REDACTED]")


class WorkspaceSafetyTests(unittest.TestCase):
    def _git(self, repository: Path, *args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=repository, text=True).strip()

    def _make_repository(self, root: Path) -> Path:
        repository = root / "repository"
        repository.mkdir()
        self._git(repository, "init", "-q")
        self._git(repository, "config", "user.email", "proofix@example.test")
        self._git(repository, "config", "user.name", "ProofFix Tests")
        (repository / "tracked.txt").write_text("committed\n", encoding="utf-8")
        tracked_secrets = {
            ".env": "API_KEY=secret\n",
            ".envrc": "export API_KEY=secret\n",
            ".netrc": "machine example.test login user password secret\n",
            ".npmrc": "//registry.example.test/:_authToken=secret\n",
            ".pypirc": "[distutils]\npassword=secret\n",
            "id_ecdsa": "ecdsa-private-key\n",
            "id_dsa": "dsa-private-key\n",
            ".docker/config.json": "{\"auths\":{}}\n",
        }
        for relative, content in tracked_secrets.items():
            secret_path = repository / relative
            secret_path.parent.mkdir(parents=True, exist_ok=True)
            secret_path.write_text(content, encoding="utf-8")
        self._git(repository, "add", "tracked.txt", *tracked_secrets)
        self._git(repository, "commit", "-qm", "initial")
        (repository / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        (repository / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        (repository / "local.pem").write_text("private\n", encoding="utf-8")
        (repository / ".envrc").write_text("export UNTRACKED_SECRET=secret\n", encoding="utf-8")
        (repository / ".netrc").write_text("machine untracked.test password secret\n", encoding="utf-8")
        (repository / ".npmrc").write_text("//registry.example.test/:_authToken=secret\n", encoding="utf-8")
        (repository / ".pypirc").write_text("password=secret\n", encoding="utf-8")
        (repository / "id_xmss").write_text("xmss-private-key\n", encoding="utf-8")
        (repository / ".docker").mkdir(exist_ok=True)
        (repository / ".docker" / "config.json").write_text("{\"auths\":{}}\n", encoding="utf-8")
        return repository

    def test_clean_snapshot_records_mode_and_commit_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self._make_repository(root)
            before = {path.relative_to(repository): path.read_bytes() for path in repository.rglob("*") if path.is_file() and ".git" not in path.relative_to(repository).parts}
            expected_commit = self._git(repository, "rev-parse", "HEAD")
            manager = WorkspaceManager(root / "run")
            snapshot = manager.snapshot_repository(repository)
            self.assertEqual(snapshot.snapshot_mode, "clean")
            self.assertEqual(snapshot.commit, expected_commit)
            self.assertEqual((snapshot.path / "tracked.txt").read_text(encoding="utf-8"), "committed\n")
            for relative in (".env", ".envrc", ".netrc", ".npmrc", ".pypirc", "id_ecdsa", "id_dsa", "id_xmss", ".docker/config.json", "local.pem"):
                self.assertFalse((snapshot.path / relative).exists(), relative)
            after = {path.relative_to(repository): path.read_bytes() for path in repository.rglob("*") if path.is_file() and ".git" not in path.relative_to(repository).parts}
            self.assertEqual(before, after)
            manager.cleanup(snapshot)
            self.assertFalse(snapshot.path.exists())
            self.assertTrue((repository / "untracked.txt").exists())

    def test_nested_repository_path_is_rejected_without_expanding_to_git_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self._make_repository(root)
            nested = repository / "nested"
            nested.mkdir()
            manager = WorkspaceManager(root / "run")
            with self.assertRaises(ValueError):
                manager.snapshot_repository(nested)
            self.assertFalse((root / "run").exists())

    def test_run_root_inside_source_is_rejected_without_source_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self._make_repository(root)
            run_root = repository / "proofix-run"
            manager = WorkspaceManager(run_root)
            with self.assertRaises(ValueError):
                manager.snapshot_repository(repository)
            self.assertFalse(run_root.exists())
            self.assertFalse((repository / "workspaces").exists())

    def test_cleanup_surfaces_failed_git_worktree_removal_and_preserves_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self._make_repository(root)
            manager = WorkspaceManager(root / "run")
            snapshot = manager.snapshot_repository(repository)
            original_git = manager._git
            try:
                def fail_remove(_repository, *args):
                    raise subprocess.CalledProcessError(1, ["git", *args])
                manager._git = fail_remove
                with self.assertRaises(RuntimeError):
                    manager.cleanup(snapshot)
                self.assertTrue(snapshot.path.exists())
                registrations = self._git(repository, "worktree", "list", "--porcelain")
                self.assertIn(str(snapshot.path), registrations)
            finally:
                manager._git = original_git
                manager.cleanup(snapshot)
            self.assertFalse(snapshot.path.exists())

    def test_cleanup_all_removes_every_live_snapshot_and_git_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self._make_repository(root)
            manager = WorkspaceManager(root / "run")
            first = manager.snapshot_repository(repository)
            second = manager.snapshot_repository(repository)
            manager.cleanup_all()
            self.assertFalse(first.path.exists())
            self.assertFalse(second.path.exists())
            registrations = self._git(repository, "worktree", "list", "--porcelain")
            self.assertNotIn(str(first.path), registrations)
            self.assertNotIn(str(second.path), registrations)

    def test_dirty_inclusion_is_explicit_and_cleanup_cannot_remove_outside_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self._make_repository(root)
            manager = WorkspaceManager(root / "run")
            snapshot = manager.snapshot_repository(repository, snapshot_mode="include_dirty")
            self.assertEqual(snapshot.snapshot_mode, "include_dirty")
            self.assertEqual((snapshot.path / "tracked.txt").read_text(encoding="utf-8"), "dirty\n")
            self.assertTrue((snapshot.path / "untracked.txt").exists())
            for relative in (".env", ".envrc", ".netrc", ".npmrc", ".pypirc", "id_ecdsa", "id_dsa", "id_xmss", ".docker/config.json", "local.pem"):
                self.assertFalse((snapshot.path / relative).exists(), relative)
            with self.assertRaises(ValueError):
                manager.cleanup(type(snapshot)(path=root / "outside", repository=repository, snapshot_mode="copy", commit=None, materialization="copy"))
            manager.cleanup(snapshot)
            self.assertFalse(snapshot.path.exists())


if __name__ == "__main__":
    unittest.main()
