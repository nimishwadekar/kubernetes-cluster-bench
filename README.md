# kubernetes-cluster-bench

Basic framework for measuring network performance between Kubernetes pods with `iperf3`.

## Build the benchmark image

Build the image and make it available to the nodes that will run the benchmark:

```bash
docker build -t kubernetes-cluster-bench:local .
```

For a multi-node cluster, push the image to a registry accessible by every node and pass that image with `--image`.

## Run a benchmark

The runner requires `kubectl` and a configured Kubernetes context. It deploys one server pod and one client pod, pins them to the requested nodes, collects raw output, and removes Kubernetes resources when finished.

```bash
./run-benchmark.sh \
  --backend iperf3 \
  --client worker-a \
  --server worker-b \
  --name run-a \
  --threads 1:4 \
  --run 3
```

Useful options include:

- `--duration SECONDS` (default: `10` measured seconds)
- `--warmup SECONDS` (default: `5`; duration tests only)
- `--threads SPEC` using `start[:end[:step]]`, such as `4`, `1:4`, or `1:5:2`
- `-r, --run COUNT` to repeat each thread-count test
- `--test-id ID` (benchmark identity stored in command metadata and Kubernetes resources)
- `--name NAME` (log directory name; defaults to the test ID)
- `-d, --dir DIR` (default: `logs`)
- `--namespace NAMESPACE`
- `--image IMAGE`
- `--keep-resources`

Results use this layout:

```text
logs/iperf3/<test-directory>/
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

`--backend` selects the registered analyzer and `--name` selects `logs/<backend>/<name>`. Both are required. Only throughput is currently supported. If a result does not contain the requested dimension, graphing fails instead of silently skipping that result. By default, graphs are written to the selected test directory's `graphs/` subdirectory.

When other dimensions vary, graphing rejects the request as ambiguous instead of silently averaging unrelated configurations. Use `--filter NAME=VALUE` to select one value, or `--series DIMENSION` to draw a separate line for each value.

## NIXLBench with UCX

GPU buffer-transfer benchmarking is available through the same frontend. When `--image` is omitted, the NIXL backend uses `quay.io/nwadekar/nixl-benchmark:og-nixl1.4.1-ucx1.22.0`:

```bash
./run-benchmark.sh \
  --backend nixl \
  --initiator worker-a \
  --target worker-b \
  --name vram-ucx \
  --threads 1:4 \
  --parameter initiator_seg_type=VRAM \
  --parameter target_seg_type=VRAM
```

Plot NIXL rows by block size for a selected batch size:

```bash
./plot-benchmark.sh \
  --backend nixl \
  --name vram-ucx \
  --x block_size_bytes \
  --filter batch_size=8 \
  --filter threads=4
```

Or compare batch sizes as separate lines:

```bash
./plot-benchmark.sh \
  --backend nixl \
  --name vram-ucx \
  --x block_size_bytes \
  --filter threads=4 \
  --series batch_size
```

NIXL rejects ambiguous plots when a non-X dimension varies without a filter or series. NIXL artifacts use `initiator.log`, `target.log`, and command metadata under `raw/tTHREADS/`. GPU image-build assets remain in [`nixl/`](./nixl/).
