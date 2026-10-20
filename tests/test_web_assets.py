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
        self.assertIn("Evidence check", html)
        self.assertIn("Fix review", html)
        self.assertIn("Focused evidence check", html)
        self.assertIn("Proposed fix review", html)
        self.assertIn("Verify", html)
        self.assertIn("not_reproduced", html)
        self.assertIn("verification_failed", html)
        self.assertIn("verification_blocked", html)
        self.assertIn("verified", html)
        self.assertIn("Claims", html)
        self.assertIn('id="claim-evidence"', html)
        self.assertIn('href="/styles.css"', html)
        self.assertIn('src="/app.js"', html)
        self.assertIn('id="experiment-button"', html)
        self.assertIn('id="experiment-context"', html)
        self.assertIn('id="experiment-recommendation"', html)
        self.assertIn('id="experiment-hypothesis"', html)
        self.assertIn('id="experiment-validation"', html)
        self.assertIn('id="experiment-regression"', html)
        self.assertIn('id="propose-button"', html)
        self.assertIn('id="proposal-validation"', html)
        self.assertIn('id="patch-safety"', html)
        self.assertIn('id="patch-safety-copy"', html)
        self.assertIn('id="patch-recommendation"', html)
        self.assertIn('id="patch-empty-state"', html)
        self.assertIn('id="patch-preview"', html)
        self.assertIn('id="regression-input"', html)
        self.assertIn('id="recovery-button"', html)
        self.assertIn('id="decision-controls"', html)
        self.assertIn('id="approval-form"', html)
        self.assertIn('readonly', html)
        self.assertNotIn('id="demo-start-button"', html)
        self.assertEqual(html.count('id="masthead-demo-button"'), 1)
        self.assertIn('id="masthead-demo-button"', html)
        self.assertIn('id="reproduce-button"', html)
        self.assertIn('id="diagnose-button"', html)
        self.assertIn('id="mode-badge"', html)
        self.assertIn('id="surface-label"', html)

    def test_copy_and_loading_semantics_are_plain_and_stage_labels_are_intentional(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("live-dot", html)
        self.assertNotIn('id="connection-label"', html)
        self.assertNotIn("connection-label", js)
        self.assertIn('aria-busy="true"', html)
        self.assertIn("Loading run data.", html)
        self.assertIn("setBusy", js)
        for phrase in (
            "The immutable intake",
            "Server-owned sequence",
            "Continue only through server-authorized transitions",
            "server-owned evidence",
            "HOSTED DEMO",
            "RECOMMENDED CHECK",
            "NO FIX PROPOSAL YET",
        ):
            self.assertNotIn(phrase, html)
            self.assertNotIn(phrase, js)
        for eyebrow in (
            "01 / TRIAGE",
            "02 / REPRODUCE",
            "03 / DIAGNOSE",
            "04 / EVIDENCE CHECK",
            "05 / FIX REVIEW",
            "06 / VERIFY",
        ):
            self.assertIn(f">{eyebrow}<", html)
        self.assertIn("Start a demo run", html)
        self.assertIn("Hosted demo, recorded evidence", html)
        self.assertIn("Approval uses this exact diff digest", js)
        self.assertNotIn("— without", html)

    def test_frontend_contract_has_safe_rendering_and_api_actions(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", js)
        self.assertIn("textContent", js)
        self.assertNotIn("TERMINAL_STATES", js)
        for endpoint in (
            "/api/demo",
            "/api/demo/start",
            "/api/runs/",
            "/reproduce",
            "/diagnose",
            "/events",
            "/experiment",
            "/propose",
            "/return-to-diagnosis",
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
        self.assertIn("updatePanelVisibility", js)
        self.assertIn("advanceStage", js)
        self.assertIn("report.${extension}", js)
        self.assertIn("link.download", js)
        self.assertIn("Start a demo run", html)
        self.assertIn("runExperiment", js)
        self.assertIn("proposePatch", js)
        self.assertIn("regression_test", js)
        self.assertIn("experimentValidation", js)
        self.assertIn("proposalValidation", js)
        self.assertIn("renderExperimentPanel", js)
        self.assertIn("utcTimestampNow", js)
        self.assertIn("decision-controls", js)
        self.assertIn("patch-safety-copy", js)
        self.assertIn("A changed diff needs a new review.", js)
        self.assertIn("patch-empty-state", js)
        self.assertIn("Calculated after proposal", js)
        self.assertIn("RECOMMENDED_DEMO_DIFF", js)
        self.assertIn("RECOMMENDED_DEMO_COMMAND", js)
        self.assertIn('value = "demo-judge"', js)
        self.assertIn("renderReproduction({ state: \"created\", input: {} }, []);", js)
        self.assertIn("renderVerification({ state: \"created\", verification: null });", js)
        self.assertGreaterEqual(js.count("await loadRun();"), 2)

    def test_css_has_responsive_and_reduced_motion_hooks(self):
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn(":focus-visible", css)
        self.assertIn("overflow", css)
        self.assertIn("overflow-x: hidden", css)
        self.assertIn("--font-sans", css)
        self.assertIn("--space-8: 64px", css)
        self.assertIn("--text-md: 14px", css)
        self.assertIn("--faint: #82909f", css)
        self.assertIn("opacity: .68", css)
        self.assertNotIn("Georgia", css)
        self.assertIn(".panel-reproduction { grid-row: auto; }", css)
        self.assertIn(".log-grid > div { min-width: 0; }", css)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr))", css)
        for stage_class in ("stage-active", "stage-complete", "stage-blocked", "stage-failed"):
            self.assertIn(stage_class, css)
        self.assertNotIn(".is-active", css)

    def test_css_bundles_local_plex_faces_and_keeps_label_case_intentional(self):
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        self.assertNotRegex(css, r"url\(\s*[\"']?(?:https?:)?//")
        for family, weights in (("IBM Plex Sans", (400, 600, 700)), ("IBM Plex Mono", (400,))):
            for weight in weights:
                matching_faces = re.findall(r"@font-face\s*\{(.*?)\}", css, flags=re.DOTALL)
                self.assertTrue(
                    any(
                        f'font-family: "{family}"' in body
                        and re.search(rf"font-weight:\s*{weight}\b", body)
                        and "font-display: swap" in body
                        and "format(\"woff2\")" in body
                        for body in matching_faces
                    ),
                    f"missing local {family} {weight} face",
                )
        self.assertNotIn(".live-dot", css)
        self.assertRegex(css, r"\.hypothesis-card\s*\{[^}]*border:\s*1px solid var\(--line\)")
        self.assertNotRegex(css, r"\.hypothesis-card\s*\{[^}]*border-left")
        uppercase_selectors = re.findall(r"([^{}]+)\{[^{}]*text-transform:\s*uppercase", css, flags=re.DOTALL)
        self.assertTrue(uppercase_selectors)
        self.assertTrue(
            all(".workspace-grid .section-heading .eyebrow" in selector or ".run-strip .section-heading .eyebrow" in selector or ".step-label" in selector for selector in uppercase_selectors)
        )
        self.assertIn("font-variant-numeric: tabular-nums", css)
        self.assertIn("[aria-busy=\"true\"]", css)
        self.assertRegex(css, r"\.button:hover:not\(:disabled\)[^{]*\{[^}]*transform:")

    def test_font_files_are_real_woff2_assets_with_licenses(self):
        font_dir = WEB / "fonts"
        fonts = (
            "ibm-plex-sans-400.woff2",
            "ibm-plex-sans-600.woff2",
            "ibm-plex-sans-700.woff2",
            "ibm-plex-mono-400.woff2",
        )
        for name in fonts:
            asset = font_dir / name
            self.assertTrue(asset.is_file(), name)
            self.assertEqual(asset.read_bytes()[:4], b"wOF2", name)
        for name in ("OFL-IBM-Plex-Sans.txt", "OFL-IBM-Plex-Mono.txt"):
            license_text = (font_dir / name).read_text(encoding="utf-8")
            self.assertIn("SIL OPEN FONT LICENSE", license_text.upper(), name)


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

    def test_bundled_fonts_and_licenses_are_served_from_allowlist(self):
        expected = {
            "/fonts/ibm-plex-sans-400.woff2": "font/woff2",
            "/fonts/ibm-plex-sans-600.woff2": "font/woff2",
            "/fonts/ibm-plex-sans-700.woff2": "font/woff2",
            "/fonts/ibm-plex-mono-400.woff2": "font/woff2",
            "/fonts/OFL-IBM-Plex-Sans.txt": "text/plain; charset=utf-8",
            "/fonts/OFL-IBM-Plex-Mono.txt": "text/plain; charset=utf-8",
        }
        for path, content_type in expected.items():
            status, received_type, body = self.request(path)
            self.assertEqual(status, 200, path)
            self.assertEqual(received_type, content_type, path)
            self.assertTrue(body, path)
            if path.endswith(".woff2"):
                self.assertEqual(body[:4], b"wOF2", path)
            else:
                self.assertIn(b"SIL OPEN FONT LICENSE", body.upper(), path)

    def test_static_rejects_traversal_and_unsafe_types(self):
        for path in ("/%2e%2e/src/proofix/server.py", "/server.py", "/missing.css"):
            status, content_type, body = self.request(path)
            self.assertIn(status, (400, 404), path)
            self.assertIn("application/json", content_type)
            self.assertIsInstance(json.loads(body), dict)


if __name__ == "__main__":
    unittest.main()
