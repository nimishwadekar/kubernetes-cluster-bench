from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from src.backends.registry import default_registry
from src.graphing.cli import main as graph_main
from src.graphing.loader import discover_test_directories, load_observations


class GraphingCliTests(unittest.TestCase):
    def make_fixture(self, root: Path) -> None:
        raw = root / "x" / "raw" / "t1"
        raw.mkdir(parents=True)
        (raw / "run-initiator.log").write_text(
            "Num threads (--num_threads=N) : 1\n1024 1 1.25\n",
            encoding="utf-8",
        )
        (raw / "run-target.log").write_text("ready\n", encoding="utf-8")
        (raw / "run-command.json").write_text(json.dumps({
            "backend": "nixl", "test_id": "x", "run_number": 1,
            "initiator_file": "run-initiator.log", "target_file": "run-target.log",
            "initiator_status": 0, "target_status": 0,
        }), encoding="utf-8")

    def test_graph_cli_renders_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root)
            status = graph_main([
                "--backend", "nixl", "--name", "x", "--dir", directory,
                "--x", "threads", "--filter", "block_size=1K",
                "--filter", "batch_size=1",
            ], default_registry())
            self.assertEqual(status, 0)
            self.assertTrue((root / "x" / "graphs" / "throughput-threads.png").is_file())
            self.assertEqual(len(load_observations("nixl", root / "x", default_registry())), 1)

    def test_graph_cli_rejects_unknown_dimension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            status = graph_main(
                ["--backend", "nixl", "--name", "x", "--dir", directory, "--x", "nope"],
                default_registry(),
            )
        self.assertEqual(status, 2)

    def test_loader_discovers_manifest_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a" / "raw").mkdir(parents=True)
            (root / "a" / "manifest.yaml").write_text("kind: Pod\n")
            self.assertEqual(discover_test_directories(root), (root / "a",))


if __name__ == "__main__":
    unittest.main()
