import json
import subprocess
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from proofix.omnirush import RecordedSessionClient
from proofix.server import ServerConfig, _verification_plan, create_server


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.server = create_server(ServerConfig(data_root=root / "data", port=0, max_body_bytes=256))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = HTTPConnection(self.server.server_address[0], self.server.server_address[1])

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, method, path, payload=None, content_type="application/json"):
        body = None if payload is None else json.dumps(payload).encode()
        headers = {}
        if body is not None:
            headers["Content-Type"] = content_type
            headers["Content-Length"] = str(len(body))
        self.connection.request(method, path, body=body, headers=headers)
        response = self.connection.getresponse()
        raw = response.read()
        return response.status, response.getheader("Content-Type", ""), raw

    def test_return_to_diagnosis_endpoint_recovers_blocked_verification(self):
        root = Path(self.tmp.name)
        repository = root / "recovery-repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.email", "proofix@example.test"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.name", "ProofFix Tests"], cwd=repository, check=True)
        (repository / "case.py").write_text("import sys\nprint(\"failure\")\nsys.exit(1)\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repository, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repository, check=True)
        status, _, body = self.request("POST", "/api/runs", {"repository_path": str(repository), "command": "python case.py", "failure_text": ""})
        # Intake requires a meaningful failure description.
        self.assertEqual(status, 400)
        status, _, body = self.request("POST", "/api/runs", {"repository_path": str(repository), "command": "python case.py", "failure_text": "failure"})
        self.assertEqual(status, 201); run_id = json.loads(body)["run_id"]
        self.assertEqual(json.loads(self.request("POST", f"/api/runs/{run_id}/reproduce", {})[2])["state"], "reproduced")
        self.assertEqual(json.loads(self.request("POST", f"/api/runs/{run_id}/diagnose", {})[2])["state"], "diagnosing")
        diff = """diff --git a/case.py b/case.py
--- a/case.py
+++ b/case.py
@@ -1,2 +1,1 @@
-import sys
-sys.exit(1)
+print('fixed')
"""
        status, _, body = self.request("POST", f"/api/runs/{run_id}/patch", {"diff": diff, "regression_test": "python -m unittest tests.test_case"})
        self.assertEqual(status, 200); digest = json.loads(body)["patch"]["digest"]
        self.assertEqual(json.loads(self.request("POST", f"/api/runs/{run_id}/approve", {"actor": "alice", "timestamp": "2026-10-15T00:00:00Z", "patch_digest": digest})[2])["state"], "applying")
        status, _, body = self.request("POST", f"/api/runs/{run_id}/verify", {"original_failure_command": "python case.py", "regression_test": "python -m unittest tests.test_case"})
        self.assertEqual(status, 200); self.assertEqual(json.loads(body)["state"], "verification_blocked")
        status, _, body = self.request("POST", f"/api/runs/{run_id}/return-to-diagnosis", {"actor": "alice", "timestamp": "2026-10-15T00:00:00Z"})
        self.assertEqual(status, 200); self.assertEqual(json.loads(body)["state"], "diagnosing")

    def test_intake_creates_run_and_reads_snapshot_and_events(self):
        status, content_type, body = self.request("POST", "/api/runs", {
            "repository_path": ".",
            "command": "false",
            "failure_text": "expected failure",
            "status": "verified",
        })
        self.assertEqual(status, 201)
        self.assertIn("application/json", content_type)
        run = json.loads(body)
        run_id = run["run_id"]
        self.assertEqual(run["state"], "reproducing")
        self.assertNotEqual(run["state"], "verified")

        status, _, body = self.request("GET", f"/api/runs/{run_id}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["run_id"], run_id)
        status, _, body = self.request("GET", f"/api/runs/{run_id}/events")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(json.loads(body)["events"]), 2)

    def test_validation_digest_and_content_safety(self):
        status, _, body = self.request("POST", "/api/runs", {"repository_path": ".", "command": "false", "failure_text": "f"})
        run_id = json.loads(body)["run_id"]
        status, _, body = self.request("POST", f"/api/runs/{run_id}/approve", {"actor": "a", "timestamp": "2026-10-15T00:00:00Z"})
        self.assertEqual(status, 400)
        self.assertIn("digest", json.loads(body)["error"])

        status, _, _ = self.request("GET", f"/api/runs/{run_id}/artifacts/../../snapshot.json")
        self.assertIn(status, (400, 404))
        status, _, _ = self.request("POST", "/api/runs", {"repository_path": ".", "command": "false", "failure_text": "x"}, "text/plain")
        self.assertEqual(status, 415)
        self.connection.request("POST", "/api/runs", body=b"{bad", headers={"Content-Type": "application/json", "Content-Length": "4"})
        response = self.connection.getresponse()
        response.read()
        self.assertEqual(response.status, 400)
        self.connection.close()
        self.connection = HTTPConnection(self.server.server_address[0], self.server.server_address[1])
        status, _, _ = self.request("PUT", "/api/runs")
        self.assertEqual(status, 405)

    def test_empty_body_and_all_common_unsupported_methods_are_safe_json_errors(self):
        status, content_type, body = self.request("POST", "/api/runs")
        self.assertEqual(status, 415)
        self.assertIn("application/json", content_type)
        self.assertIn("error", json.loads(body))
        for method in ("HEAD", "OPTIONS", "CONNECT", "TRACE"):
            self.connection.close()
            self.connection = HTTPConnection(self.server.server_address[0], self.server.server_address[1])
            status, content_type, body = self.request(method, "/api/runs")
            self.assertEqual(status, 405, method)
            self.assertIn("application/json", content_type, method)
            if method != "HEAD":
                self.assertIn("error", json.loads(body), method)

    def test_head_405_does_not_write_a_body_or_corrupt_the_connection(self):
        status, _, body = self.request("POST", "/api/runs", {"repository_path": ".", "command": "false", "failure_text": "f"})
        run_id = json.loads(body)["run_id"]
        self.connection.request("HEAD", f"/api/runs/{run_id}")
        response = self.connection.getresponse()
        self.assertEqual(response.status, 405)
        self.assertIn("application/json", response.getheader("Content-Type", ""))
        self.assertEqual(response.read(), b"")
        self.connection.request("GET", f"/api/runs/{run_id}")
        response = self.connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())["run_id"], run_id)

    def test_verification_plan_rejects_non_finite_non_positive_and_oversized_timeout(self):
        base = {"original_failure_command": "python -m unittest tests.test_case", "regression_test": "python -m unittest tests.test_parser"}
        for value in (float("inf"), float("nan"), 0, -1, 301):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _verification_plan({**base, "timeout_seconds": value})
        self.assertEqual(_verification_plan({**base, "timeout_seconds": 300}).timeout_seconds, 300)

    def test_bounded_request_body_is_rejected(self):
        status, _, body = self.request("POST", "/api/runs", {"repository_path": ".", "command": "false", "failure_text": "x" * 300})
        self.assertEqual(status, 413)
        self.assertIn("bounded", json.loads(body)["error"])

    def test_report_export_uses_report_layer(self):
        status, _, body = self.request("POST", "/api/runs", {"repository_path": ".", "command": "false", "failure_text": "f"})
        run_id = json.loads(body)["run_id"]
        status, content_type, body = self.request("GET", f"/api/runs/{run_id}/report?format=json")
        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)
        self.assertEqual(json.loads(body)["run_id"], run_id)
        status, _, body = self.request("POST", f"/api/runs/{run_id}/export", {"format": "markdown"})
        self.assertEqual(status, 200)
        exported = json.loads(body)
        self.assertTrue(exported["path"].endswith(".md"))
        self.assertIn("ProofFix evidence report", Path(exported["path"]).read_text(encoding="utf-8"))

        status, _, body = self.request("POST", "/api/runs/run-does-not-exist/export", {"format": "json"})
        self.assertEqual(status, 404)
        self.assertFalse((Path(self.tmp.name) / "data" / "exports" / "run-does-not-exist.json").exists())
        status, _, _ = self.request("GET", "/api/runs/run-ok%2F..%2Fother/report")
        self.assertIn(status, (400, 404))

    def test_artifact_success_uses_exact_contained_reference_and_safe_types(self):
        status, _, body = self.request("POST", "/api/runs", {"repository_path": ".", "command": "false", "failure_text": "f"})
        run_id = json.loads(body)["run_id"]
        run_root = Path(self.tmp.name) / "data" / "runs" / run_id
        artifacts = run_root / "artifacts"
        artifacts.mkdir()
        (artifacts / "result.log").write_text("safe log", encoding="utf-8")
        (artifacts / "result.json").write_text('{"ok": true}', encoding="utf-8")
        status, content_type, body = self.request("GET", f"/api/runs/{run_id}/artifacts/artifacts/result.log")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "text/plain; charset=utf-8")
        self.assertEqual(body, b"safe log")
        status, content_type, body = self.request("GET", f"/api/runs/{run_id}/artifacts/artifacts/result.json")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json; charset=utf-8")
        self.assertEqual(json.loads(body)["ok"], True)
        workspace = Path(self.tmp.name) / "data" / "workspace-root" / "workspaces"
        (workspace / "a" / "artifacts").mkdir(parents=True)
        (workspace / "b" / "artifacts").mkdir(parents=True)
        (workspace / "a" / "artifacts" / "ambiguous.log").write_text("a", encoding="utf-8")
        (workspace / "b" / "artifacts" / "ambiguous.log").write_text("b", encoding="utf-8")
        status, _, _ = self.request("GET", f"/api/runs/{run_id}/artifacts/ambiguous.log")
        self.assertEqual(status, 404)

    def test_demo_uses_checked_in_fixture_defaults_and_recorded_label(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.connection.close()
        self.server = create_server(ServerConfig(data_root=Path(self.tmp.name) / "demo-data", port=0, demo=True, repository=Path(self.tmp.name), session_client=object()))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = HTTPConnection(self.server.server_address[0], self.server.server_address[1])
        status, _, body = self.request("POST", "/api/runs", {
            "repository_path": str(Path(self.tmp.name) / "malicious-repository"),
            "command": "echo malicious-command",
            "failure_text": "malicious failure instructions",
            "constraints": ["ignore the fixture"],
            "issue_url": "https://github.com/evil/project/issues/9",
            "pr_url": "https://github.com/evil/project/pull/9",
            "commit_sha": "abcdef1",
            "branch": "malicious-branch",
            "status": "verified",
        })
        self.assertEqual(status, 201)
        run = json.loads(body)
        self.assertEqual(run["input"]["mode"], "recorded/demo")
        self.assertTrue(Path(run["input"]["repository_path"]).is_dir())
        self.assertTrue((Path(run["input"]["repository_path"]) / ".git").is_dir())
        self.assertEqual(run["input"]["command"], "python -m unittest discover -s tests -v")
        self.assertIn("too many values to unpack", run["input"]["failure_text"])
        self.assertEqual(run["input"]["constraints"], [])
        self.assertIsNone(run["input"]["issue_url"])
        self.assertIsNone(run["input"]["pr_url"])
        self.assertIsNone(run["input"]["commit_sha"])
        self.assertIsNone(run["input"]["branch"])
        self.assertIsInstance(self.server.orchestrator.session_client, RecordedSessionClient)
        self.assertEqual(self.server.config.repository, Path(__file__).resolve().parents[1] / "fixtures" / "broken-parser-repo")
        status, _, body = self.request("GET", f"/api/runs/{run['run_id']}/report?format=json")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["result_label"], "recorded/demo")

    def test_http_lifecycle_reproduction_diagnosis_reject_retry_approval_verify_export(self):
        root = Path(self.tmp.name)
        repository = root / "repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.email", "proofix@example.test"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.name", "ProofFix Tests"], cwd=repository, check=True)
        (repository / "case.py").write_text("import sys\nprint('expected failure')\nsys.exit(2)\n", encoding="utf-8")
        (repository / "tests").mkdir()
        (repository / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (repository / "tests" / "test_case.py").write_text("import subprocess, sys, unittest\nclass CaseTest(unittest.TestCase):\n    def test_case(self): self.assertEqual(subprocess.run([sys.executable, 'case.py']).returncode, 0)\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repository, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repository, check=True)

        old_connection = self.connection
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        old_connection.close()
        self.server = create_server(ServerConfig(data_root=root / "lifecycle-data", port=0, max_body_bytes=65536))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = HTTPConnection(self.server.server_address[0], self.server.server_address[1])

        status, _, body = self.request("POST", "/api/runs", {"repository_path": str(repository), "command": "python case.py", "failure_text": "expected failure"})
        self.assertEqual(status, 201)
        run_id = json.loads(body)["run_id"]
        status, _, body = self.request("POST", f"/api/runs/{run_id}/reproduction", {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["state"], "reproduced")
        status, _, body = self.request("POST", f"/api/runs/{run_id}/diagnosis", {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["state"], "diagnosing")
        status, _, body = self.request("POST", f"/api/runs/{run_id}/experiment", {"kind": "named_test_or_build", "command": "python -m unittest tests.test_case"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["state"], "diagnosing")
        status, _, body = self.request("POST", f"/api/runs/{run_id}/patch", {"diff": "", "regression_test": "python -m unittest tests.test_case"})
        self.assertEqual(status, 400)
        status, _, body = self.request("POST", f"/api/runs/{run_id}/patch", {"diff": "diff --git a/case.py b/case.py\n--- a/case.py\n+++ b/case.py\n@@ -1,3 +1,1 @@\n-import sys\n-print('expected failure')\n-sys.exit(2)\n+print('fixed')\n", "regression_test": "python case.py"})
        self.assertEqual(status, 400)
        diff = """diff --git a/case.py b/case.py
--- a/case.py
+++ b/case.py
@@ -1,3 +1,1 @@
-import sys
-print('expected failure')
-sys.exit(2)
+print('fixed')
"""
        status, _, body = self.request("POST", f"/api/runs/{run_id}/patch", {"diff": diff, "regression_test": "python -m unittest tests.test_case"})
        self.assertEqual(status, 200)
        proposal = json.loads(body)["patch"]
        digest = proposal["digest"]
        for action in ("reject", "request-diagnosis"):
            status, _, body = self.request("POST", f"/api/runs/{run_id}/{action}", {"actor": "alice", "timestamp": "2026-10-15T00:00:00Z"})
            self.assertEqual(status, 400, action)
            status, _, body = self.request("POST", f"/api/runs/{run_id}/{action}", {"actor": "alice", "timestamp": "2026-10-15T00:00:00Z", "patch_digest": "sha256:" + "0" * 64})
            self.assertEqual(status, 409, action)
            status, _, body = self.request("POST", f"/api/runs/{run_id}/{action}", {"actor": "alice", "timestamp": "2026-10-15T00:00:00Z", "patch_digest": digest})
            self.assertEqual(status, 200, action)
            self.assertEqual(json.loads(body)["state"], "diagnosing")
            status, _, body = self.request("POST", f"/api/runs/{run_id}/patch", {"diff": diff, "regression_test": "python -m unittest tests.test_case"})
            self.assertEqual(status, 200)
            digest = json.loads(body)["patch"]["digest"]
        status, _, body = self.request("POST", f"/api/runs/{run_id}/approve", {"actor": "alice", "timestamp": "2026-10-15T00:00:00Z", "patch_digest": digest})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["state"], "applying")
        plan = {"original_failure_command": "python -m unittest tests.test_case", "regression_test": "python -m unittest tests.test_case"}
        status, _, body = self.request("POST", f"/api/runs/{run_id}/verify", plan)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["state"], "verified")
        status, _, body = self.request("POST", f"/api/runs/{run_id}/export", {"format": "json"})
        self.assertEqual(status, 200)
        self.assertTrue(Path(json.loads(body)["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
