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


if __name__ == "__main__":
    unittest.main()
