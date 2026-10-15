import unittest

import proofix


class ProofFixSmokeTest(unittest.TestCase):
    def test_package_exposes_non_empty_version(self):
        self.assertIsInstance(proofix.__version__, str)
        self.assertTrue(proofix.__version__)


if __name__ == "__main__":
    unittest.main()
