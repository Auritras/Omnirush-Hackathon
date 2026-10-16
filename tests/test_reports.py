import json
import tempfile
import unittest
from pathlib import Path

from proofix.reports import render_json, render_markdown, write_report


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.run = {
            "schema_version": "1.0",
            "run_id": "run-report-1",
            "state": "verification_failed",
            "mode": "recorded/demo",
            "repository": {
                "path": "fixtures/broken-parser-repo",
                "commit": "abc123",
                "branch": "main",
            },
            "input": {
                "repository_path": "fixtures/broken-parser-repo",
                "command": "python -m unittest discover -s tests -v",
                "failure_text": "password=super-secret",
            },
            "hypotheses": [
                {
                    "hypothesis": "The parser splits values too aggressively",
                    "confidence": 0.9,
                    "supporting_evidence": [
                        {"kind": "file", "path": "parser.py", "line": 9, "summary": "Root cause"},
                        {"kind": "command", "command": "python -m unittest tests.test_parser", "result": "failed"},
                    ],
                    "contradicting_evidence": [
                        {"kind": "file", "path": "tests/test_parser.py", "line": 4, "summary": "Simple values pass"}
                    ],
                    "session_id": "omni-recorded-parser",
                }
            ],
            "patch": {
                "digest": "sha256:" + "a" * 64,
                "files": ["parser.py"],
                "approval": {"action": "approved", "actor": "alice", "timestamp": "2026-10-15T00:00:00Z"},
            },
            "verification": {
                "status": "verification_failed",
                "checks": [
                    {"name": "original_failure", "status": "passed", "command": "python parser.py", "exit_code": 0, "duration_ms": 12},
                    {"name": "regression_test", "status": "failed", "command": "python -m unittest tests.test_parser", "exit_code": 1, "duration_ms": 34},
                ],
            },
            "omnirush_sessions": ["omni-recorded-parser"],
            "evidence": [
                {"kind": "artifact", "summary": "Full stderr", "artifact_ref": "artifacts/stderr.log"}
            ],
        }
        self.events = [
            {
                "event_id": "evt-1",
                "run_id": "run-report-1",
                "timestamp": "2026-10-15T00:00:00Z",
                "kind": "command_result",
                "stage": "reproduction",
                "command": "python -m unittest discover -s tests -v",
                "exit_code": 1,
                "details": {"duration_ms": 88, "stdout_ref": "artifacts/stdout.log"},
            },
        ]

    def test_markdown_and_json_include_auditable_fields_and_exact_refs(self):
        markdown = render_markdown(self.run, self.events, {"artifacts/stderr.log": "full secret output"})
        payload = json.loads(render_json(self.run, self.events))
        for value in (
            "run-report-1", "abc123", "python -m unittest discover -s tests -v",
            "exit code", "duration", "parser.py:9", "artifacts/stderr.log",
            "omni-recorded-parser", "sha256:" + "a" * 64, "regression_test",
            "contradict", "recorded/demo",
        ):
            self.assertIn(value, markdown.lower() if value == "exit code" else markdown)
        self.assertEqual(payload["run_id"], "run-report-1")
        self.assertEqual(payload["repository_snapshot"]["commit"], "abc123")
        self.assertEqual(payload["events"][0]["exit_code"], 1)
        self.assertEqual(payload["verification"]["checks"][1]["name"], "regression_test")
        self.assertEqual(payload["evidence"][0]["artifact_ref"], "artifacts/stderr.log")

    def test_credentials_are_redacted_and_inline_logs_are_bounded(self):
        run = dict(self.run)
        run["input"] = {"command": "echo", "failure_text": "API_KEY=top-secret"}
        run["evidence"] = [{"kind": "log", "summary": "TOKEN=inline-secret", "details": {"stdout": "x" * 10000}}]
        markdown = render_markdown(run, self.events, {})
        encoded = render_json(run, self.events)
        for secret in ("top-secret", "inline-secret", "full secret output"):
            self.assertNotIn(secret, markdown)
            self.assertNotIn(secret, encoded)
        payload = json.loads(encoded)
        self.assertLessEqual(len(payload["evidence"][0]["details"]["stdout"]), 1024)
        self.assertTrue(payload["redactions"])
        self.assertIn("artifacts/stdout.log", encoded)

    def test_unverified_exports_are_explicit_and_write_report_supports_snapshot_path(self):
        run = dict(self.run)
        run["state"] = "awaiting_approval"
        run["patch"] = None
        run["verification"] = None
        markdown = render_markdown(run, [], {})
        payload = json.loads(render_json(run, []))
        self.assertIn("UNVERIFIED", markdown)
        self.assertFalse(payload["verified"])
        self.assertEqual(payload["terminal_status"], "awaiting_approval")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run-report-1"
            root.mkdir()
            (root / "snapshot.json").write_text(json.dumps(self.run), encoding="utf-8")
            (root / "events.jsonl").write_text("\n".join(json.dumps(item) for item in self.events) + "\n", encoding="utf-8")
            destination = Path(directory) / "export.md"
            self.assertEqual(write_report(str(root / "snapshot.json"), destination, "markdown"), destination)
            self.assertIn("run-report-1", destination.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
