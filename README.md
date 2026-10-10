# kubernetes-cluster-bench

Basic framework for measuring network performance between Kubernetes pods with `iperf3`.

## Build the benchmark image

Build the image and make it available to the nodes that will run the benchmark:

```bash
docker build -t kubernetes-cluster-bench:local .
```

For a multi-node cluster, push the image to a registry accessible by every node and pass that image with `--image`.

## Run a benchmark

The runner requires `kubectl` and a configured Kubernetes context. It deploys one server pod and one client pod, pins them to the requested nodes, collects raw output, writes canonical throughput results, and removes Kubernetes resources when finished.

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
- `--test-id ID` (benchmark identity stored in metadata and Kubernetes resources)
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
│   ├── <thread/run>/
│   │   ├── <run>-client.json
│   │   └── <run>-command.json
│   └── <test-id>-server.json
└── graphs/
```

The raw files are the source of truth. Graphing parses and aggregates them when requested; no normalized result file is required. The directory name is not authoritative, so it may be renamed without changing the test identity recorded in command metadata.

## Plot benchmark results

Install the Python plotting dependency:

```bash
python3 -m pip install -r requirements.txt
```

Plot canonical results by a numeric dimension:

```bash
./plot-benchmark.sh \
  --backend iperf3 \
  --name run-a \
  --dir logs \
  --x threads \
  --max-throughput 200
```

`--backend` selects the registered analyzer and `--name` selects `logs/<backend>/<name>`. Both are required. Only throughput is currently supported. If a result does not contain the requested dimension, graphing fails instead of silently skipping that result. By default, graphs are written to the selected test directory's `graphs/` subdirectory.

## NIXLBench with UCX

GPU KV-cache transfer benchmarking assets are available in [`nixl/`](./nixl/). See [`nixl/README.md`](./nixl/README.md) for the version-pinned image build and Kubernetes deployment instructions.
