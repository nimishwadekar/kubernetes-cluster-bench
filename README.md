# kubernetes-cluster-bench

Basic framework for measuring network performance between Kubernetes pods with `iperf3`.

## Build the benchmark image

Build the image and make it available to the nodes that will run the benchmark:

```bash
docker build -f images/Dockerfile.iperf3 -t kubernetes-cluster-bench:local .
```

For a multi-node cluster, push the image to a registry accessible by every node and set the required image in the benchmark profile.

## Run a benchmark

The runner requires `kubectl` and a configured Kubernetes context. It deploys one server pod and one client pod, pins them to the requested nodes, collects raw output, and removes Kubernetes resources when finished.

```bash
./run-benchmark.sh --profile profiles/pirate-iperf3.yaml
```

The benchmark frontend accepts only `--profile FILE` and `--help`; benchmark settings belong in the profile.

For example:

```bash
./run-benchmark.sh --profile profiles/pirate-iperf3.yaml
```

A profile contains the benchmark settings:

```yaml
backend: iperf3
name: pirate-iperf3
nodes:
  initiator: a100-03
  target: a100-04
dimensions:
  threads: "1:4"
execution:
  namespace: nwadekar-pirate
  image: quay.io/nwadekar/kubernetes-cluster-bench:latest
  log_directory: logs
  runs: 3
  timeout_seconds: 120
  keep_resources: false
  iperf3:
    duration_seconds: 5
    warmup_seconds: 1
```

Profile validation is split between the framework and the selected backend. Common fields are `backend`, `name`, `test_id`, `nodes.initiator`, `nodes.target`, `dimensions.threads`, `execution.namespace`, `execution.image`, `execution.log_directory`, `execution.runs`, `execution.timeout_seconds`, and `execution.keep_resources`. The iperf3 backend exposes `execution.iperf3.duration_seconds` and `execution.iperf3.warmup_seconds`; the NIXL backend exposes `execution.nixl.warmup_runs`. Both backends expose `dimensions.threads`; NIXL additionally exposes `dimensions.block_size` and `dimensions.batch_size`. These NIXL ranges are translated to the corresponding `_start` and `_max` flags; `block_size` accepts `K`, `M`, and `G` suffixes as powers of 1024, both NIXL ranges must increase through powers of two, and the framework automatically calculates and passes the minimum valid `total_buffer_size` for each thread count (`max_block_size × max_batch_size × threads`, assuming one initiator/target device), and only declared profile dimensions may be used for graphing. Other profile fields are rejected. For iperf3, `execution.runs` controls repeated framework runs; for NIXL, it controls `--num_iter` and NIXL executes once per thread dimension.

Results use this layout:

```text
logs/<test-directory>/
├── manifest.yaml
├── raw/
│   ├── t1/
│   │   ├── <run>-client.json
│   │   └── <run>-command.json
│   ├── t2/
│   └── <test-id>-server.json
└── graphs/
```

The raw files are the source of truth. Graphing parses and aggregates them when requested; no normalized result file is required. The directory name is not authoritative, so it may be renamed without changing the test identity recorded in command metadata.

## Plot benchmark results

Install the Python plotting dependency:

```bash
python3 -m pip install -r requirements.txt
```

Plot raw benchmark observations by a numeric dimension:

```bash
./plot-benchmark.sh \
  --backend iperf3 \
  --name run-a \
  --dir logs \
  --x threads \
  --max-throughput 200
```

`--backend` selects the registered analyzer and `--name` selects `logs/<name>`. Both are required. Only throughput is currently supported. Each backend declares the dimensions it exposes to graphing; requesting another `--x`, `--filter`, or `--series` dimension is rejected. If a result does not contain a declared requested dimension, graphing also fails instead of silently skipping that result. By default, graphs are written to the selected test directory's `graphs/` subdirectory.
When other dimensions vary, graphing rejects the request as ambiguous instead of silently averaging unrelated configurations. Use `--filter NAME=VALUE` to select one value, or `--series DIMENSION` to draw a separate line for each value.

## NIXLBench with UCX

GPU buffer-transfer benchmarking is available through the same profile-based frontend. The example profile uses `quay.io/nwadekar/nixl-benchmark:og-nixl1.4.1-ucx1.22.0`:

```bash
./run-benchmark.sh --profile profiles/pirate-nixl.yaml
```

Plot NIXL rows by block size for a selected batch size:

```bash
./plot-benchmark.sh \
  --backend nixl \
  --name pirate-nixl \
  --x block_size_bytes \
  --filter batch_size=8 \
  --filter threads=4
```

Or compare batch sizes as separate lines:

```bash
./plot-benchmark.sh \
  --backend nixl \
  --name pirate-nixl \
  --x block_size_bytes \
  --filter threads=4 \
  --series batch_size
```

NIXL rejects ambiguous plots when a non-X dimension varies without a filter or series. NIXL artifacts use `initiator.log`, `target.log`, and command metadata under `raw/tTHREADS/`. GPU image-build assets remain in [`nixl/`](./nixl/).
