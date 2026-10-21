from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from src.backends.registry import default_registry
from src.graphing.cli import build_parser, default_graph_filename, main as graph_main
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
            self.assertTrue(
                (root / "x" / "graphs" / "throughput-threads__batch_size-1__block_size-1K.png").is_file()
            )
            self.assertEqual(len(load_observations("nixl", root / "x", default_registry())), 1)

    def name_for(self, *options: str) -> str:
        args = build_parser().parse_args(
            ["--backend", "nixl", "--name", "x", "--x", "threads", *options]
        )
        return default_graph_filename(args)

    def test_default_name_is_unchanged_without_options(self) -> None:
        self.assertEqual(self.name_for(), "throughput-threads.png")

    def test_default_name_contains_the_options_that_change_the_plot(self) -> None:
        self.assertEqual(
            self.name_for("--filter", "block_size=4M", "--series", "batch_size"),
            "throughput-threads__block_size-4M__by-batch_size.png",
        )
        self.assertEqual(
            self.name_for("--percentile", "99", "--x-log", "2", "--max-throughput", "100"),
            "throughput-threads__p99__xlog2__max100.png",
        )
        self.assertEqual(
            self.name_for("--series", "batch_size", "--series", "block_size"),
            "throughput-threads__by-batch_size-block_size.png",
        )

    def test_filter_order_does_not_change_the_name(self) -> None:
        first = self.name_for("--filter", "block_size=4M", "--filter", "batch_size=1")
        second = self.name_for("--filter", "batch_size=1", "--filter", "block_size=4M")
        self.assertEqual(first, second)
        self.assertEqual(first, "throughput-threads__batch_size-1__block_size-4M.png")
        self.assertNotEqual(first, self.name_for("--filter", "block_size=4M", "--filter", "batch_size=2"))

    def test_default_name_is_filesystem_safe_and_bounded(self) -> None:
        self.assertEqual(
            self.name_for("--filter", "block_size=a/b c"),
            "throughput-threads__block_size-a-b-c.png",
        )
        long_name = self.name_for(*(o for i in range(40) for o in ("--filter", f"dimension_{i}=value_{i}")))
        self.assertLessEqual(len(long_name), 205)
        self.assertTrue(long_name.endswith(".png"))
        self.assertEqual(
            long_name,
            self.name_for(*(o for i in range(40) for o in ("--filter", f"dimension_{i}=value_{i}"))),
        )

    def test_explicit_output_is_not_renamed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root)
            target = root / "custom.png"
            status = graph_main([
                "--backend", "nixl", "--name", "x", "--dir", directory, "--x", "threads",
                "--filter", "block_size=1K", "--filter", "batch_size=1", "--output", str(target),
            ], default_registry())
            self.assertEqual(status, 0)
            self.assertTrue(target.is_file())
            self.assertFalse((root / "x" / "graphs").exists())

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
