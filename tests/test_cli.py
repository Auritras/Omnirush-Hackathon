import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proofix import cli


class CLITests(unittest.TestCase):
    def test_run_and_demo_wire_local_server_without_install(self):
        with tempfile.TemporaryDirectory() as directory:
            captured = []

            class FakeServer:
                def serve_forever(self):
                    captured.append("served")

                def server_close(self):
                    captured.append("closed")

            with patch.object(cli, "create_server", return_value=FakeServer()) as factory:
                self.assertEqual(cli.main(["run", "--data-root", directory]), 0)
                self.assertEqual(cli.main(["demo", "--data-root", directory]), 0)
            self.assertEqual(factory.call_count, 2)
            self.assertEqual(captured, ["served", "closed", "served", "closed"])
            self.assertTrue(factory.call_args_list[1].args[0].demo)

    def test_export_delegates_to_report_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.json"
            with patch.object(cli, "write_report", return_value=output) as writer:
                self.assertEqual(cli.main(["export", "run-123", str(output), "--format", "json"]), 0)
            writer.assert_called_once()
            self.assertEqual(writer.call_args.args[0], "run-123")
            self.assertEqual(writer.call_args.args[2], "json")


if __name__ == "__main__":
    unittest.main()
