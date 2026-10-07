# kubernetes-cluster-bench

Basic framework for measuring network performance between Kubernetes pods with `iperf3`.

## Build the benchmark image

Build the image and make it available to the nodes that will run the benchmark:

```bash
docker build -t kubernetes-cluster-bench:local .
```

For a multi-node cluster, push the image to a registry accessible by every node and pass that image with `--image`.

## Run a benchmark

The runner requires `kubectl` and a configured Kubernetes context. It deploys one server pod and one client pod, pins them to the requested nodes, collects JSON output, and removes the Kubernetes resources when finished.

```bash
./run-benchmark.sh \
  --client worker-a \
  --server worker-b
```

Useful options include:

- TCP throughput testing
- `--duration SECONDS` (default: `10` measured seconds)
- `--warmup SECONDS` (default: `5`; duration tests only), omitted from the measured results and added to the total iperf3 runtime
- `--threads SPEC` (default: `1`), using `start[:end[:step]]`; for example, `4`, `1:4`, or `1:5:2`.
- `-r, --run COUNT` (default: `1`), runs each thread-count test multiple times and records runs as `-run1`, `-run2`, and so on in filenames while keeping the base test ID unchanged.
- `-d, --dir DIR` (default: `<PWD>/logs`)
- `--namespace NAMESPACE`
- `--image IMAGE`
- `--keep-resources`

Each client test is attempted up to three times. Failed attempts clear the client JSON before retrying; if all attempts fail, the runner continues with the remaining tests and exits nonzero after reporting all failed tests.

Results are written under the selected log directory:

- `<test-id>-tTHREADS-runRUN-client.json`: `iperf3` JSON result, including structured server output for the completed test
- `<test-id>-server.json`: JSON server output, with one result per completed client test
- `<test-id>-tTHREADS-runRUN-metadata.json`: benchmark parameters, resource names, timestamps, and numeric `run_id`

The manifest uses normal Kubernetes pod networking and a per-run Service for the server. The runner does not use `hostNetwork`.

## Plot benchmark results

Install the Python plotting dependency:

```bash
python3 -m pip install -r requirements.txt
```

Plot all metadata results in [`logs/`](./logs/):

```bash
./plot-benchmark.sh --x transfer_size
```

You can also select metadata files explicitly:

```bash
./plot-benchmark.sh \
  --dir logs \
  --x threads \
  --y throughput \
  --max-throughput 200 \
  --output graphs/throughput.png
```

`--max-throughput` adds a horizontal theoretical-maximum line in Gbit/s and is valid only for throughput graphs. `-p, --percentile` plots the selected throughput percentile, adds `-pX` to the default graph filename, and appends ` - Xth percentile` to the title. Percentiles are calculated from each run's valid server interval throughput values before repeated runs are aggregated. Graph points use hollow markers.

Available X-axis parameters are `transfer_size` and `threads`. Available Y-axis metrics are `throughput`, `retransmits`, and `client_cpu_util`. Throughput points use the mean of valid server per-interval sum bitrates at least 0.5 seconds long, with standard-deviation error bars. Intervals with inconsistent start/end timing are excluded. Repeated results from the same test are aggregated by run mean, with between-run standard deviation as the throughput error bar.
