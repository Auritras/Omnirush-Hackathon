import re
import unittest
from pathlib import Path

from proofix.server import ServerConfig


class ConfigurationContractTests(unittest.TestCase):
    ROOT = Path(__file__).parents[1]

    def test_default_data_root_matches_makefile_cli_readme_and_env_example(self):
        expected = ".proofix-data"
        self.assertEqual(str(ServerConfig().data_root), expected)
        makefile = (self.ROOT / "Makefile").read_text(encoding="utf-8")
        cli = (self.ROOT / "src" / "proofix" / "cli.py").read_text(encoding="utf-8")
        readme = (self.ROOT / "README.md").read_text(encoding="utf-8")
        env_example = (self.ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertRegex(makefile, rf"PROOFIX_DATA_ROOT\s*\?=\s*{re.escape(expected)}")
        self.assertIn('".proofix-data"', cli)
        self.assertIn("--data-root .proofix-data", readme)
        self.assertIsNotNone(re.search(rf"^PROOFIX_DATA_ROOT={re.escape(expected)}$", env_example, re.MULTILINE))

    def test_non_loopback_binding_requires_demo_and_explicit_public_flag(self):
        with self.assertRaisesRegex(ValueError, "non-loopback"):
            ServerConfig(host="0.0.0.0").normalized()
        config = ServerConfig(host="0.0.0.0", demo=True, public_demo=True).normalized()
        self.assertEqual(config.host, "0.0.0.0")
        self.assertTrue(config.demo)
        self.assertTrue(config.public_demo)

    def test_render_blueprint_uses_port_independent_docker_health_and_no_credentials(self):
        blueprint = (self.ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("name: proofix-demo", blueprint)
        self.assertIn("dockerfilePath: ./Dockerfile", blueprint)
        self.assertIn("healthCheckPath: /health", blueprint)
        self.assertIn("PROOFIX_DATA_ROOT", blueprint)
        self.assertIn(".proofix-data", blueprint)
        self.assertNotRegex(blueprint, r"(?i)(github|token|secret|password|credential)")


if __name__ == "__main__":
    unittest.main()
