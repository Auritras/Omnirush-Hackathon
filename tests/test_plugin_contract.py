from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
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
        for marker in ("assertRunId", "assertActor", "assertDigest", "assertObject", "assertFormat", "assertTimeout", "assertCommand", "assertScopePaths", "assertTimestamp"):
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
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        for phrase in ("clean checkout", "fixture", "dashboard", "gh", "localhost", "approval", "report"):
            self.assertIn(phrase.casefold(), readme.casefold())
        self.assertIn("\ntest:", makefile)
        self.assertIn("make test", dockerfile)
        self.assertIn("python:3.12", dockerfile)
        self.assertIn("/usr/local/bin", dockerfile)
        self.assertIn("PROOFIX_DATA_ROOT=.proofix-data", env_example)
        self.assertIn(".proofix-data", makefile)

    def test_typescript_runtime_executes_exact_plugin_validation_rules_when_available(self):
        bun = shutil.which("bun")
        node = shutil.which("node")
        runtime = None
        prefix = []
        if bun is not None:
            try:
                usable = subprocess.run([bun, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            except OSError:
                usable = None
            if usable is not None and usable.returncode == 0:
                runtime = bun
        if runtime is None and node is not None:
            runtime = node
            prefix = ["--experimental-strip-types", "--input-type=module"]
        if runtime is None:
            self.skipTest("no usable Bun or Node TypeScript runtime; source contract checks still run")
        script = r'''
import { operations } from "./.opencode/plugins/proofix.ts";

const validPlan = {
  original_failure_command: "python -m unittest tests.test_parser",
  regression_test: "python -m unittest tests.test_parser",
  relevant_tests: [],
  scope_files: ["parser.py"],
  timeout_seconds: 30,
};
const cases = [
  ["proofix.verify_run", { run_id: "run-validation", plan: { ...validPlan, timeout_seconds: Infinity } }],
  ["proofix.verify_run", { run_id: "run-validation", plan: { ...validPlan, timeout_seconds: NaN } }],
  ["proofix.verify_run", { run_id: "run-validation", plan: { ...validPlan, timeout_seconds: 301 } }],
  ["proofix.verify_run", { run_id: "run-validation", plan: { ...validPlan, relevant_tests: Array(65).fill("python -m unittest tests.test_parser") } }],
  ["proofix.verify_run", { run_id: "run-validation", plan: { ...validPlan, scope_files: ["../parser.py"] } }],
  ["proofix.verify_run", { run_id: "run-validation", plan: { ...validPlan, scope_files: ["/tmp/parser.py"] } }],
  ["proofix.verify_run", { run_id: "run-validation", plan: { ...validPlan, original_failure_command: "python -m unittest tests.test_parser; rm -rf /" } }],
  ["proofix.approve_patch", { run_id: "run-validation", actor: "operator", timestamp: "2026-02-30T00:00:00Z", patch_digest: "sha256:" + "a".repeat(64) }],
  ["proofix.approve_patch", { run_id: "run-validation", actor: "operator", timestamp: "2026-01-01T00:00:00+00:00", patch_digest: "sha256:" + "a".repeat(64) }],
  ["proofix.start_run", { repository_path: "/tmp/repo", command: "python -m unittest tests.test_parser", failure_text: "failure", constraints: Array(17).fill("x") }],
  ["proofix.start_run", { repository_path: "/tmp/repo", command: "python -m unittest tests.test_parser", failure_text: "x".repeat(4097) }],
];
for (const [name, args] of cases) {
  let rejected = false;
  try { await operations[name](args); } catch (_) { rejected = true; }
  if (!rejected) throw new Error(`validation accepted invalid ${name}`);
}
'''
        result = subprocess.run([runtime, *prefix, "-e", script], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_plugin_type_syntax_check_when_node_supports_type_stripping(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable; source contract checks still run")
        result = subprocess.run([node, "--experimental-strip-types", "--check", str(PLUGIN)], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0 and "Unexpected identifier 'JsonObject'" in result.stderr:
            self.skipTest("node cannot type-strip during --check; use node execution check instead")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
