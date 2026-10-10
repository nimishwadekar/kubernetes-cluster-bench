from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import json
import subprocess
import tempfile
import unittest

from new_src.benchmark.artifacts import (
    create_run_directory,
    create_test_directory,
    write_json,
)
from new_src.benchmark.kubernetes import KubectlClient, KubectlError
from new_src.graphing.contracts import (
    AnalysisRequest,
    GraphPoint,
    MissingDimensionError,
    Observation,
)
from new_src.graphing.renderer import GraphRenderConfig, render_graph
from new_src.graphing.selection import numeric_dimension, select_observations
from new_src.graphing.statistics import (
    mean,
    mean_and_standard_deviation,
    percentile,
    standard_deviation,
)


class SharedFactoringTests(unittest.TestCase):
    def test_artifact_helpers_create_backend_neutral_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            test_directory, raw_directory = create_test_directory(
                Path(directory), "iperf3", "test-1"
            )
            run_directory = create_run_directory(raw_directory, 2, 3)
            artifact = run_directory / "command.json"
            write_json(artifact, {"backend": "iperf3", "threads": 2})

            self.assertEqual(
                run_directory,
                Path(directory) / "iperf3" / "test-1" / "raw" / "t2-run3",
            )
            self.assertEqual(json.loads(artifact.read_text())["threads"], 2)
            self.assertEqual(test_directory / "raw", raw_directory)

    def test_statistics_match_existing_graphing_rules(self) -> None:
        values = (1.0, 2.0, 4.0)
        self.assertAlmostEqual(mean(values), 7 / 3)
        self.assertAlmostEqual(standard_deviation(values), 1.5275252316519468)
        self.assertEqual(mean_and_standard_deviation((2.0,)), (2.0, 0.0))
        self.assertEqual(percentile(values, 50), 2.0)
        self.assertAlmostEqual(percentile(values, 75), 3.0)

    def test_selection_filters_and_requires_dimensions(self) -> None:
        observations = (
            Observation(
                metric="throughput",
                value=1e9,
                unit="bit/s",
                dimensions={"threads": 1, "batch_size": 1},
                sample_kind="interval",
                run_id="run-1",
            ),
            Observation(
                metric="throughput",
                value=2e9,
                unit="bit/s",
                dimensions={"threads": 1, "batch_size": 2},
                sample_kind="interval",
                run_id="run-1",
            ),
        )
        selected = select_observations(
            observations,
            AnalysisRequest(
                metric="throughput",
                x_dimension="batch_size",
                filters={"threads": 1},
            ),
        )
        self.assertEqual([item.value for item in selected], [1e9, 2e9])
        self.assertEqual(numeric_dimension(selected[0], "batch_size"), 1.0)
        with self.assertRaises(MissingDimensionError):
            select_observations(
                observations,
                AnalysisRequest(metric="throughput", x_dimension="block_size_bytes"),
            )

    def test_renderer_handles_multiple_series_and_error_bars(self) -> None:
        points = (
            # The second series models a future NIXL batch-size series.
            GraphPoint(
                x_value=1,
                y_value=1.0,
                x_label="1",
                series_key=("batch=1",),
                y_error=(0.1, 0.1),
            ),
            GraphPoint(
                x_value=2,
                y_value=1.5,
                x_label="2",
                series_key=("batch=1",),
                y_error=(0.1, 0.1),
            ),
            GraphPoint(
                x_value=1,
                y_value=2.0,
                x_label="1",
                series_key=("batch=2",),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "graphs" / "throughput.png"
            render_graph(
                points,
                GraphRenderConfig(
                    output_path=output,
                    x_label="Block size",
                    y_label="Throughput",
                    title="Throughput by block size",
                ),
            )
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 0)

    @patch("new_src.benchmark.kubernetes.subprocess.run")
    def test_kubectl_wrapper_builds_shared_commands(self, run_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=["kubectl"], returncode=0, stdout="pod/x\n", stderr=""
        )
        client = KubectlClient()
        self.assertEqual(client.output(["config", "current-context"]), "pod/x")
        client.apply_manifest("bench", Path("manifest.yaml"))
        client.wait_for("bench", "pod/x", "jsonpath={.status.phase}=Running", 30)
        self.assertGreaterEqual(run_mock.call_count, 3)

    @patch("new_src.benchmark.kubernetes.subprocess.run")
    def test_kubectl_errors_are_actionable(self, run_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=["kubectl", "get"], returncode=1, stdout="", stderr="forbidden"
        )
        with self.assertRaises(KubectlError) as raised:
            KubectlClient().output(["get", "pods"])
        self.assertIn("forbidden", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
