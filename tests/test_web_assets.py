import json
import re
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from proofix.server import ServerConfig, create_server


WORKTREE = Path(__file__).resolve().parents[1]
WEB = WORKTREE / "web"


class WebAssetContractTests(unittest.TestCase):
    def test_semantic_html_contract_and_static_references(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertRegex(html, r"<main\b")
        self.assertRegex(html, r"<header\b")
        self.assertRegex(html, r"<nav\b")
        self.assertRegex(html, r"<section\b")
        self.assertRegex(html, r"<button\b[^>]+aria-label=")
        self.assertIn('aria-live="polite"', html)
        self.assertIn("Triage", html)
        self.assertIn("Reproduce", html)
        self.assertIn("Diagnose", html)
        self.assertIn("Experiment", html)
        self.assertIn("Patch", html)
        self.assertIn("Verify", html)
        self.assertIn("not_reproduced", html)
        self.assertIn("verification_failed", html)
        self.assertIn("verification_blocked", html)
        self.assertIn("verified", html)
        self.assertIn("Claims", html)
        self.assertIn('id="claim-evidence"', html)
        self.assertIn('href="/styles.css"', html)
        self.assertIn('src="/app.js"', html)

    def test_frontend_contract_has_safe_rendering_and_api_actions(self):
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", js)
        self.assertIn("textContent", js)
        self.assertNotIn("TERMINAL_STATES", js)
        for endpoint in (
            "/api/runs/",
            "/events",
            "/approve",
            "/reject",
            "/request-diagnosis",
            "/verify",
            "/export",
        ):
            self.assertIn(endpoint, js)
        for terminal in ("not_reproduced", "blocked", "verification_failed", "verified"):
            self.assertIn(terminal, js)
        self.assertIn('if (state === "blocked") return stage === "triage" ? "Complete" : stage === "reproduction" ? "Blocked" : "Pending";', js)
        self.assertIn('if (state === "verification_blocked") return stage === "verification" ? "Blocked" : index < 5 ? "Complete" : "Pending";', js)
        self.assertIn("run.experiments", js)
        self.assertIn("hypothesis.experiment", js)
        self.assertIn("renderExperiment", js)
        self.assertIn("collectClaims", js)
        self.assertIn("renderClaimsList", js)
        self.assertIn("renderReproduction({ state: \"created\", input: {} }, []);", js)
        self.assertIn("renderVerification({ state: \"created\", verification: null });", js)
        self.assertGreaterEqual(js.count("await loadRun();"), 2)

    def test_css_has_responsive_and_reduced_motion_hooks(self):
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn(":focus-visible", css)
        self.assertIn("overflow", css)
        for stage_class in ("stage-active", "stage-complete", "stage-blocked", "stage-failed"):
            self.assertIn(stage_class, css)
        self.assertNotIn(".is-active", css)


class StaticWebServingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = create_server(ServerConfig(data_root=Path(self.tmp.name) / "data", port=0))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = HTTPConnection(*self.server.server_address)

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, path):
        self.connection.request("GET", path)
        response = self.connection.getresponse()
        return response.status, response.getheader("Content-Type", ""), response.read()

    def test_root_and_assets_are_served_with_safe_content_types(self):
        status, content_type, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "text/html; charset=utf-8")
        self.assertIn(b"ProofFix", body)
        status, content_type, body = self.request("/styles.css")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "text/css; charset=utf-8")
        self.assertIn(b"prefers-reduced-motion", body)
        status, content_type, body = self.request("/app.js")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "text/javascript; charset=utf-8")
        self.assertIn(b"textContent", body)

    def test_static_rejects_traversal_and_unsafe_types(self):
        for path in ("/%2e%2e/src/proofix/server.py", "/server.py", "/missing.css"):
            status, content_type, body = self.request(path)
            self.assertIn(status, (400, 404), path)
            self.assertIn("application/json", content_type)
            self.assertIsInstance(json.loads(body), dict)


if __name__ == "__main__":
    unittest.main()
