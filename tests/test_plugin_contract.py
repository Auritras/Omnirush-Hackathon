from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / ".opencode" / "plugins" / "proofix.ts"
SKILL = ROOT / ".opencode" / "skills" / "proofix" / "SKILL.md"


class ProofFixPluginContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PLUGIN.read_text(encoding="utf-8")
        cls.skill = SKILL.read_text(encoding="utf-8")

    def test_exposes_only_guarded_proofix_operations(self):
        for operation in (
            "proofix.start_run",
            "proofix.get_run",
            "proofix.approve_patch",
            "proofix.reject_patch",
            "proofix.verify_run",
            "proofix.export_report",
        ):
            self.assertIn(operation, self.source)
        self.assertIn("awaiting_approval", self.source)
        self.assertIn("applying", self.source)
        self.assertIn("fetch(", self.source)

    def test_plugin_is_localhost_only_and_cannot_request_unrestricted_access(self):
        self.assertRegex(self.source, r"127\.0\.0\.1|localhost")
        for forbidden in ("child_process", "exec(", "spawn(", "shell: true", "readFile", "writeFile", "rm -rf"):
            self.assertNotIn(forbidden, self.source)
        self.assertNotRegex(self.source, r"https?://[^\"']+\$\{")

    def test_operation_arguments_are_validated_and_skill_records_boundaries(self):
        for marker in ("assertRunId", "assertActor", "assertDigest", "assertObject", "assertFormat"):
            self.assertIn(marker, self.source)
        for phrase in (
            "read-only",
            "recorded-fixture",
            "approval",
            "evidence",
            "localhost",
            "GitHub",
        ):
            self.assertIn(phrase.casefold(), self.skill.casefold())

    def test_readme_and_packaging_document_the_offline_boundary(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        for phrase in ("clean checkout", "fixture", "dashboard", "gh", "localhost", "approval", "report"):
            self.assertIn(phrase.casefold(), readme.casefold())
        self.assertIn("\ntest:", makefile)
        self.assertIn("make test", dockerfile)
        self.assertIn("python:3.12", dockerfile)


if __name__ == "__main__":
    unittest.main()
