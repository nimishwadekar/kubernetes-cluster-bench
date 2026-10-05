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
- `--duration SECONDS` (default: `10`)
- `--parallel SPEC` (default: `1`), where `SPEC` is `N`, `START:END`, or `START:END:STEP`; ranges run one benchmark per stream count, inclusively and sequentially using the same pod pair. Range runs append `-pSTREAMS` to result and metadata filenames.
- `--logs-dir DIR` (default: `logs/`)
- `--namespace NAMESPACE`
- `--image IMAGE`
- `--keep-resources`

Results are written under the selected log directory:

- `<run-id>-client.json`: `iperf3` JSON result
- `<run-id>-server.log`: server output
- `<run-id>-metadata.json`: benchmark parameters, resource names, and timestamps

The manifest uses normal Kubernetes pod networking and a per-run Service for the server. The runner does not use `hostNetwork`.

## Plot benchmark results

Install the Python plotting dependency:

```bash
python3 -m pip install -r requirements.txt
```

Plot all metadata results in [`logs/`](./logs/):

```bash
./plot-benchmark.sh --x transfer_size --y throughput_gbps
```

You can also select metadata files explicitly:

```bash
./plot-benchmark.sh \
  --logs-dir logs \
  --x parallel_streams \
  --y throughput_gbps \
  --output graphs/throughput.png
```

Available X-axis parameters include `transfer_size`, `duration`, and `parallel_streams`. Available Y-axis metrics include receiver throughput, received bytes, transfer duration, retransmissions, and CPU utilization.
