import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from proofix.server import ServerConfig, create_server


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
        status, _, _ = self.request("PUT", "/api/runs")
        self.assertEqual(status, 405)

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

    def test_demo_uses_checked_in_fixture_defaults_and_recorded_label(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.connection.close()
        self.server = create_server(ServerConfig(data_root=Path(self.tmp.name) / "demo-data", port=0, demo=True))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = HTTPConnection(self.server.server_address[0], self.server.server_address[1])
        status, _, body = self.request("POST", "/api/runs", {})
        self.assertEqual(status, 201)
        run = json.loads(body)
        self.assertEqual(run["input"]["mode"], "recorded/demo")
        status, _, body = self.request("GET", f"/api/runs/{run['run_id']}/report?format=json")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["result_label"], "recorded/demo")


if __name__ == "__main__":
    unittest.main()
