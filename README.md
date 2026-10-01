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
- `--parallel STREAMS` (default: `1`)
- `--namespace NAMESPACE`
- `--image IMAGE`
- `--keep-resources`

Results are written under [`logs/`](./logs/):

- `<run-id>-client.json`: `iperf3` JSON result
- `<run-id>-server.log`: server output
- `<run-id>-metadata.txt`: benchmark parameters and timestamps

The manifest uses normal Kubernetes pod networking and a per-run Service for the server. The runner does not use `hostNetwork`.
