import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from proofix import cli
from proofix.store import RunStore


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

    def test_render_port_and_public_demo_environment_are_wired(self):
        with tempfile.TemporaryDirectory() as directory:
            captured = []

            class FakeServer:
                def serve_forever(self):
                    return None

                def server_close(self):
                    return None

            with patch.dict(os.environ, {"PORT": "9123", "PROOFIX_DEMO": "true", "PROOFIX_PUBLIC_DEMO": "true"}, clear=False), patch.object(cli, "create_server", return_value=FakeServer()) as factory:
                self.assertEqual(cli.main(["run", "--data-root", directory, "--host", "0.0.0.0"]), 0)
            config = factory.call_args.args[0]
            self.assertEqual(config.port, 9123)
            self.assertTrue(config.demo)
            self.assertTrue(config.public_demo)

    def test_export_delegates_to_report_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory) / "data"
            run = RunStore(data_root).create_run({"repository_path": ".", "command": "false", "failure_text": "f"})
            output = data_root / "exports" / "out.json"
            with patch.object(cli, "write_report", return_value=output) as writer:
                self.assertEqual(cli.main(["export", run.run_id, "exports/out.json", "--format", "json", "--data-root", str(data_root)]), 0)
            writer.assert_called_once()
            self.assertEqual(writer.call_args.args[0].run_id, run.run_id)
            self.assertEqual(writer.call_args.args[2], "json")

    def test_repository_must_be_inside_configured_cli_allowed_root(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            allowed = Path(directory) / "allowed"
            repository = allowed / "repository"
            repository.mkdir(parents=True)
            denied = Path(outside) / "repository"
            denied.mkdir()

            class FakeServer:
                def serve_forever(self):
                    return None

                def server_close(self):
                    return None

            with patch.object(cli, "create_server", return_value=FakeServer()) as factory:
                self.assertEqual(cli.main(["run", "--data-root", str(Path(directory) / "data"), "--allowed-root", str(allowed), "--repository", str(repository)]), 0)
                self.assertEqual(factory.call_args.args[0].allowed_roots, (allowed.resolve(),))
            with self.assertRaises(ValueError):
                cli.main(["run", "--data-root", str(Path(directory) / "data"), "--allowed-root", str(allowed), "--repository", str(denied)])

    def test_export_rejects_unknown_or_path_run_ids_and_outside_destinations(self):
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory) / "data"
            run = RunStore(data_root).create_run({"repository_path": ".", "command": "false", "failure_text": "f"})
            with self.assertRaises(ValueError):
                cli.main(["export", "run-missing", "exports/out.json", "--data-root", str(data_root)])
            with self.assertRaises(ValueError):
                cli.main(["export", "../" + run.run_id, "exports/out.json", "--data-root", str(data_root)])
            with self.assertRaises(ValueError):
                cli.main(["export", run.run_id, str(Path(directory).parent / "outside.json"), "--data-root", str(data_root)])


if __name__ == "__main__":
    unittest.main()
