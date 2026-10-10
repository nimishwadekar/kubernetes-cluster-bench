from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from new_src.benchmark.contracts import BenchmarkConfig
from new_src.benchmark.overwrite import (
    OverwriteDeclined,
    confirm_overwrite,
    test_directory_path,
)
from new_src.common.console import Colors


NO_COLORS = Colors("", "", "", "", "", "")


class OverwriteTests(unittest.TestCase):
    def make_config(self, directory: Path) -> BenchmarkConfig:
        return BenchmarkConfig(
            backend="iperf3",
            benchmark="network-throughput",
            test_id="test-id",
            initiator_node="worker-a",
            target_node="worker-b",
            namespace="bench",
            image="example/iperf3:latest",
            log_directory=directory,
            name="friendly-name",
        )

    def test_absent_directory_does_not_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            confirm_overwrite(
                config,
                input_fn=lambda _: self.fail("unexpected prompt"),
                colors=NO_COLORS,
            )

    def test_no_declines_and_preserves_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            output = test_directory_path(config)
            output.mkdir(parents=True)
            marker = output / "old.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaises(OverwriteDeclined):
                confirm_overwrite(
                    config,
                    input_fn=lambda _: "n",
                    colors=NO_COLORS,
                )
            self.assertTrue(marker.is_file())

    def test_yes_deletes_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            output = test_directory_path(config)
            output.mkdir(parents=True)
            (output / "old.txt").write_text("delete", encoding="utf-8")
            confirm_overwrite(
                config,
                input_fn=lambda _: "y",
                colors=NO_COLORS,
            )
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
