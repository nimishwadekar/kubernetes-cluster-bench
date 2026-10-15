# kubernetes-cluster-bench

`kubernetes-cluster-bench` is a profile-driven benchmarking and graphing framework for Kubernetes workloads. It provides one frontend for multiple benchmark backends while keeping execution, parsing, statistics, and rendering backend-specific where necessary.

Current backends:

- **iperf3**: TCP throughput between Kubernetes pods.
- **NIXLBench**: GPU/CPU buffer-transfer throughput using the NIXLBench image and UCX.

Only the `throughput` metric is currently supported.

## How it works

```text
YAML profile
    ↓
benchmark frontend
    ↓
backend runner → Kubernetes pods → raw logs/results
    ↓
graphing frontend
    ↓
backend parser/analyzer → shared renderer → PNG graph
```

The raw artifacts are authoritative. The framework does not require a normalized results file. Each backend owns its native command construction and parsing, while the frontends provide common profile loading, lifecycle handling, dimension validation, filtering, aggregation, and graph rendering.

## Requirements

- Python environment with dependencies from [requirements.txt](./requirements.txt).
- `kubectl` configured for the target Kubernetes cluster.
- Nodes and container images suitable for the selected backend.
- For NIXL, GPU-enabled nodes and a NIXLBench image containing `nixlbench`.

The benchmark expects a Python venv, create and activate it:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install Python dependencies with:

```bash
python3 -m pip install -r requirements.txt
```

## Building images

The Dockerfiles are in [images/](./images/):

- [images/Dockerfile.iperf3](./images/Dockerfile.iperf3): iperf3 runtime image.
- [images/Dockerfile.nixl](./images/Dockerfile.nixl): vLLM-based NIXLBench image.

Build the iperf3 image from the repository root:

```bash
docker build \
  -f images/Dockerfile.iperf3 \
  -t kubernetes-cluster-bench:local \
  .
```

Build the NIXL image for the cluster architecture:

```bash
docker buildx build \
  --platform linux/amd64 \
  -f images/Dockerfile.nixl \
  -t <registry>/nixl-benchmark:<tag> \
  --push \
  .
```

Push each image to a registry reachable by every node and place the resulting image reference in the profile. The Python benchmark runner does not build or push images.

## Running benchmarks

The benchmark frontend intentionally accepts only two options:

```text
--profile FILE
--help
```

All benchmark settings belong in the profile. Run a benchmark with:

```bash
./run-benchmark.sh --profile profiles/pirate-iperf3.yaml
```

The runner creates a rendered manifest under the selected output directory, deploys backend resources, runs the configured dimensions, stores raw artifacts, and removes resources unless `keep_resources` is true. Deployment cleanup is attempted even when startup is interrupted.

## Profile format

A profile contains six conceptual parts:

- `backend`: registered backend name.
- `name`: dataset/output directory name.
- `nodes`: Kubernetes node placement.
- `dimensions`: values swept or reported as benchmark dimensions.
- `execution`: common lifecycle settings and backend execution settings.
- `test_id`: optional Kubernetes/resource identity; generated if omitted.

### iperf3 profile

```yaml
backend: iperf3
name: pirate-iperf3-test

test_id: optional-stable-id

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

`threads: "1:4"` runs thread counts 1, 2, 3, and 4. iperf3 maps the current thread count to `iperf3 -P N`. `runs` controls repeated framework runs for each thread count. iperf3 uses duration plus warmup; transfer-size mode is not supported.

### NIXL profile

```yaml
backend: nixl
name: pirate-nixl

nodes:
  initiator: a100-03
  target: a100-04

dimensions:
  threads: "1:4"
  block_size: "1K:8M"
  batch_size: "1:8"

execution:
  namespace: nwadekar-pirate
  image: quay.io/nwadekar/nixl-benchmark:og-nixl1.4.1-ucx1.22.0
  log_directory: logs
  runs: 100
  timeout_seconds: 120
  keep_resources: false
  nixl:
    warmup_runs: 10
```

NIXL currently hardcodes these execution settings:

```text
runtime: ASIO
transport/backend: UCX
initiator memory: DRAM
target memory: DRAM
ASIO port: 12345
```

The runner derives the ASIO address from the initiator pod IP. It maps:

```text
threads      → --num_threads
runs         → --num_iter
warmup_runs → --warmup_iter
block_size   → --start_block_size / --max_block_size
batch_size   → --start_batch_size / --max_batch_size
```

`block_size` accepts `K`, `M`, and `G` suffixes using powers of 1024. `block_size` and `batch_size` ranges must increase by powers of two. The framework calculates `total_buffer_size` per thread using the current image’s observed validation requirement:

```text
max_block_size × max_batch_size × num_threads
```

assuming one initiator and one target device, then passes the calculated value to NIXLBench. NIXLBench may adjust iteration counts to distribute work evenly across threads.

For NIXL, `runs` is passed to NIXLBench as `--num_iter`; the framework executes once per thread dimension rather than repeating the whole NIXL process.

### Profile validation

The loader rejects unknown fields. Common fields are:

```text
backend, name, test_id
nodes.initiator, nodes.target
dimensions.threads
execution.namespace, execution.image, execution.log_directory
execution.runs, execution.timeout_seconds, execution.keep_resources
```

Backend-exposed fields are:

| Backend | Execution fields | Dimensions |
|---|---|---|
| iperf3 | `duration_seconds`, `warmup_seconds` | `threads` |
| nixl | `warmup_runs` | `threads`, `block_size`, `batch_size` |

Values are validated before Kubernetes resources are created.

## Output layout

Benchmark output is stored as `logs/<name>/`, not under a backend subdirectory:

```text
logs/<name>/
├── manifest.yaml
├── raw/
│   ├── t1/
│   │   ├── <run>-command.json
│   │   ├── <run>-initiator.log or <run>-client.json
│   │   └── <run>-target.log
│   └── t2/
└── graphs/
```

The raw command metadata records the backend, nodes, image, parameters, translated native command, status, and timestamps. NIXL logs contain the initiator measurement table and target coordination log. A failed run may have incomplete artifacts and will be reported by graphing rather than silently ignored.

## Graphing

The graph frontend requires:

```bash
./plot-benchmark.sh \
  --backend BACKEND \
  --name DATASET \
  --x DIMENSION
```

Example for iperf3:

```bash
./plot-benchmark.sh \
  --backend iperf3 \
  --name pirate-iperf3-test \
  --x threads
```

Example for one NIXL configuration:

```bash
./plot-benchmark.sh \
  --backend nixl \
  --name pirate-nixl \
  --x threads \
  --filter block_size=4M \
  --filter batch_size=1
```

Compare batch sizes as separate series:

```bash
./plot-benchmark.sh \
  --backend nixl \
  --name pirate-nixl \
  --x threads \
  --filter block_size=4M \
  --series batch_size
```

Compare block sizes as separate series:

```bash
./plot-benchmark.sh \
  --backend nixl \
  --name pirate-nixl \
  --x threads \
  --filter batch_size=1 \
  --series block_size
```

Plot block size on a logarithmic axis with base 2:

```bash
./plot-benchmark.sh \
  --backend nixl \
  --name pirate-nixl \
  --x block_size \
  --filter batch_size=1 \
  --filter threads=1 \
  --x-log 2
```

Supported graph dimensions are declared by each backend and must also be valid profile dimensions. Current dimensions are `threads` for iperf3 and `threads`, `block_size`, and `batch_size` for NIXL. Only `throughput` is supported as the Y metric.

Filters accept numeric values and, for byte dimensions, `K`, `M`, and `G` suffixes:

```text
block_size=1K
block_size=4M
block_size=1G
```

If a dimension varies but is neither the X axis, a filter, nor a series, graphing rejects the request. This prevents unrelated configurations from being silently averaged. Constant and filtered dimensions are included in the graph title. Legends are displayed outside the plot and series are assigned distinct colors.

Default graph path:

```text
logs/<name>/graphs/throughput-<x-dimension>.png
```

Use `--output FILE` to choose another path. Use `--title TEXT` to override the generated title. `--max-throughput VALUE` adds a reference line in Gbit/s, and `--percentile PERCENT` selects a percentile before aggregation where supported.

## Current backend limitations

- Only throughput is implemented.
- No live cluster validation, image discovery, or NIXL version discovery is performed.
- NIXL runtime settings are intentionally fixed as described above.
- NIXL image behavior depends on the NIXLBench version inside the selected image.
- Live Kubernetes execution is not covered by the unit tests; tests use mocked Kubernetes/process boundaries and embedded parser fixtures.

## Repository map

```text
images/       Backend image Dockerfiles
manifests/    Backend Kubernetes manifest templates
profiles/     Reproducible benchmark profiles
src/          Framework, backends, graphing, and tests
logs/         Benchmark datasets and generated graphs
```
