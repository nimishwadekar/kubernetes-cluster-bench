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
- For NIXL, a NIXLBench image containing `nixlbench`. GPU-enabled nodes are needed by default; set `execution.nixl.gpu: false` to run on CPU-only nodes with a CPU-only image (see `images/Dockerfile.nixl-cpu`).

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
- [images/Dockerfile.nixl-gpu](./images/Dockerfile.nixl-gpu): GPU NIXLBench image (CUDA 13). NIXL and UCX come from the `nixl-cu13` wheel, on a minimal CUDA base image.
- [images/Dockerfile.nixl-cpu](./images/Dockerfile.nixl-cpu): CPU-only NIXLBench image built from source on Ubuntu (UCX, NIXL with the UCX plugin, `nixlbench`; about 130 MB). Use it with `execution.nixl.gpu: false`.

Build the iperf3 image from the repository root:

```bash
docker build \
  -f images/Dockerfile.iperf3 \
  -t kubernetes-cluster-bench:local \
  .
```

Build the GPU NIXL image for the cluster architecture. It copies nothing from the build context, so use the small `images/` directory as the context. `--build-arg CUDA_VERSION=...` and `--build-arg NIXL_VERSION=...` override the CUDA base image and the NIXL version:

```bash
docker buildx build \
  --platform linux/amd64 \
  -f images/Dockerfile.nixl-gpu \
  -t <registry>/nixl-benchmark:gpu \
  --push \
  images/
```

Build the CPU-only NIXL image. It copies nothing from the build context, so use the small `images/` directory as the context:

```bash
docker build \
  -f images/Dockerfile.nixl-cpu \
  -t <registry>/nixl-benchmark:cpu \
  images/
```

It builds UCX 1.22.0 and NIXL v1.4.1 from source (`--build-arg UCX_VERSION=...` and `--build-arg NIXL_VERSION=...` override them), so the first build takes a while. It supports DRAM to DRAM transfers only; VRAM is not available.

Push each image to a registry reachable by every node and place the resulting image reference in the profile. The Python benchmark runner does not build or push images.

## Running benchmarks

The benchmark frontend intentionally accepts only two options:

```text
--profile FILE
--help
```

All benchmark settings belong in the profile. Start from a template and keep your own cluster-specific copy in `profiles/local/`, which is git-ignored:

```bash
cp profiles/iperf3-template.yaml profiles/local/my-iperf3.yaml   # or nixl-template.yaml
$EDITOR profiles/local/my-iperf3.yaml                            # fill in nodes, namespace, image
./run-benchmark.sh --profile profiles/local/my-iperf3.yaml
```

The templates ([iperf3](./profiles/iperf3-template.yaml), [nixl](./profiles/nixl-template.yaml)) are commented and are checked by the test suite, so they always load. The other files in `profiles/` (`pirate-*.yaml`) are fixed profiles for a specific cluster and are kept as reproducible examples only.

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
name: my-iperf3

test_id: optional-stable-id

nodes:
  initiator: <initiator-node>
  target: <target-node>

dimensions:
  threads: "1:4"

execution:
  namespace: <namespace>
  image: <registry>/kubernetes-cluster-bench:iperf3
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
name: my-nixl

nodes:
  initiator: <initiator-node>
  target: <target-node>

dimensions:
  threads: "1:4"
  block_size: "1K:8M"
  batch_size: "1:8"

execution:
  namespace: <namespace>
  image: <registry>/nixl-benchmark:<tag>
  log_directory: logs
  runs: 100
  timeout_seconds: 120
  keep_resources: false
  nixl:
    warmup_runs: 10
    run_timeout_seconds: 1800  # optional, default 1800
    gpu: true                  # optional, default true
    parallelism: threads       # optional: threads (default) or processes, see "NIXL parallelism"
```

`gpu: false` removes the `nvidia.com/gpu` request from both pods and replaces `UCX_TLS=tcp,self,cuda_copy` with `tcp,self`, so the pods can run on nodes without GPUs. The data path is DRAM to DRAM either way. Use an image that starts `nixlbench` without a CUDA driver, such as [Dockerfile.nixl-cpu](./images/Dockerfile.nixl-cpu). The GPU image ([Dockerfile.nixl-gpu](./images/Dockerfile.nixl-gpu)) needs the NVIDIA driver at run time.

`run_timeout_seconds` bounds each NIXLBench execution (one per thread count). If it expires, both processes are killed, the run is recorded as failed, and the benchmark continues with the next thread count. Unlike `timeout_seconds`, which covers pod readiness and initiator startup, it should be sized to the full block/batch sweep.

NIXL currently hardcodes these execution settings:

```text
runtime: ASIO
transport/backend: UCX
initiator memory: DRAM
target memory: DRAM
ASIO port: 12345 (each nixlbench pair of a run gets the next port)
```

The runner derives the ASIO address from the initiator pod IP (or from its secondary interface when a `network` section is set). It maps:

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

### Secondary network (Multus)

By default the iperf3 client connects to the server pod's Kubernetes Service, and NIXL uses the initiator pod's `eth0` IP, so traffic uses the pods' primary interface (`eth0`). To benchmark another NIC, attach it with [Multus](https://github.com/k8snetworkplumbingwg/multus-cni) and add a top-level `network` section. The initiator and target are configured separately:

```yaml
network:
  initiator:
    attachment: nic-a          # NetworkAttachmentDefinition name, or <namespace>/<name>
    interface: net1            # optional, default net1
    ip: 10.10.0.1/24           # optional
  target:
    attachment: nic-b
    interface: net1
    ip: 10.10.0.2/24
```

The `NetworkAttachmentDefinition`s must already exist in the cluster (the framework does not create them). Before creating any pods it checks for each one, and fails with an explicit error if the cluster has no `NetworkAttachmentDefinition` resource (Multus is not installed) or if a named attachment does not exist. If `attachment` has no namespace, Multus looks for it in `execution.namespace`. Each pod is annotated with `k8s.v1.cni.cncf.io/networks` for its own side, and the framework then:

1. Reads each pod's `k8s.v1.cni.cncf.io/network-status` annotation to find the IP on that side's `interface`. If the pod has no such annotation or interface, the run fails with an error naming what was found (typically Multus is missing or the attachment is wrong).
2. Uses those addresses instead of `eth0`:
   - **iperf3** runs the client as `iperf3 -c <target address> -B <initiator address>`, so the connection starts and ends on the secondary interfaces instead of using the Service.
   - **NIXL** uses the initiator's secondary address as the ASIO rendezvous address (`--asio_address`), and sets `UCX_NET_DEVICES=<interface>` in each pod so UCX carries the data over that interface and not `eth0`.

`ip` is optional. Without it the address comes from the attachment's IPAM. With it, the address is requested through Multus (`ips` in the annotation, which needs a static-capable IPAM) and the run fails before any traffic if the pod ends up with a different address. It must include a prefix length. The resolved addresses are recorded under `network` in each `*-command.json`.

Backends that do not support `network` reject the section instead of ignoring it. NIXL runs over TCP here (`UCX_TLS` is unchanged), so this selects the NIC, not RDMA.

### NIXL parallelism: `threads` or `processes`

`dimensions.threads` is the number of parallel flows, like `iperf3 -P N`. `execution.nixl.parallelism` chooses how NIXL builds them:

| `parallelism` | What runs for `threads: N` |
|---|---|
| `threads` (default) | One nixlbench pair with `--num_threads N`, running the whole block/batch sweep in one go. |
| `processes` | N nixlbench pairs with one thread each, running together, one (block size, batch size) point at a time. |

With the default UCX transport over TCP, the threads of one nixlbench pair do not add up on the receiving side. The initiator drives one UCX worker per thread, but the target progresses all of its workers from a single thread (`nixlUcxEngine::progress()` loops over them), and that thread also performs the `recv()` copies. The pair is therefore limited by what one core can receive, however many threads the initiator uses. `enable_pt` and `progress_threads` do not change this: `enable_pt` uses one shared thread for all workers, and `progress_threads` only splits large batches on the sending side. With `processes`, each pair has its own ASIO port and its own target process, so the receive work spreads over N cores. Use `threads` to measure NIXL's own multi-threading and `processes` to see how far TCP gets with enough cores; the graph x-axis is `threads` in both cases and the mode is recorded in each `*-command.json`.

```yaml
dimensions:
  threads: "1:4"
execution:
  nixl:
    parallelism: processes
```

In `processes` mode:

- The pods need cores for N processes and memory for N sets of buffers (`resources` applies to the pod, not per process).
- Each point is a separate nixlbench execution, so `run_timeout_seconds` bounds one point, `warmup_runs` is spent again at every point, and the sweep takes longer than in `threads` mode. A failing point stops the run and the error names it.
- The result of a point is N times the rate of its slowest process, which is the data all pairs moved divided by the time the slowest took. Adding the processes' own averages would be wrong when they finish at different times, because a fast pair then reports a rate measured while the link was shared and a slow pair one that includes the end of the run when it had the link alone. The aggregate cannot exceed what the link carried. The ratio between the fastest and slowest process is stored as `skew` in each observation (1.0 means they were even; a high value means the aggregate is below the rate the link sustained while all of them ran).
- Logs are named `<test_id>-t<N>-run<n>-b<block>-n<batch>-p<k>-initiator.log` (and `-target.log`), and `*-command.json` lists them under `points`.
- `pipeline_depth` is checked against one thread per process.

### NIXL tuning: `args` and `env`

`execution.nixl.args` passes extra flags to `nixlbench`, and `execution.nixl.env` sets environment variables on both `nixlbench` processes (the same values on the initiator and the target). Both are optional, and the values are recorded in each `*-command.json` (`nixl_args`, `nixl_env`).

```yaml
execution:
  nixl:
    args:
      pipeline_depth: 16       # --pipeline_depth=16
    env:
      UCX_ZCOPY_THRESH: 1K
      UCX_TCP_RX_SEG_SIZE: 128K
```

Supported `args`: `pipeline_depth` and `large_blk_iter_ftr` (integers, at least 1), `progress_threads` (integer, at least 0), `enable_pt`, `prepared_xfer`, `recreate_xfer`, `reregister_mem`, `use_hugepages`, `enable_vmm` and `check_consistency` (true or false), and `op_type` (`READ` or `WRITE`). Flags the runner computes from the profile (block and batch sizes, threads, iterations, buffer size, runtime and backend) are rejected with a pointer to the profile field that sets them.

`pipeline_depth` has a limit that comes from how `nixlbench` splits its iterations. It runs `runs / threads` iterations per thread, and for blocks above 1 MiB divides that by `large_blk_iter_ftr` (16) again. The same applies to `warmup_runs`. If a thread runs fewer iterations than `pipeline_depth`, `nixlbench` caps the depth and then aborts with "descriptor count ... is not evenly divisible by pipeline depth". The profile is therefore rejected before any pod is created unless, for every thread count in the sweep and for blocks above 1 MiB (when `block_size` goes beyond 1M), both `runs` and `warmup_runs` are at least `pipeline_depth × threads × 16`. For depth 16 and 8 threads that is 2048.

`env` names must be `UCX_*`, `NIXL_*` or `OMP_*` variables in capitals. `UCX_NET_DEVICES` is rejected when a `network` section is set, because the interface names there already set it. List UCX variables with `ucx_info -cf` in the image (it is `/opt/ucx/bin/ucx_info` in the CPU image).

When `env` is set, UCX is asked to report unknown variables, and a run fails if either process logs `unused environment variable: ...`, so a misspelled name cannot silently leave a setting unapplied.

### Failure handling

A failed run is recorded and the benchmark continues with the next run. `execution.max_consecutive_failures` (default `5`, `0` disables) aborts the benchmark after that many failed runs in a row, so a broken cluster does not burn hours on runs that cannot succeed. Resources are still cleaned up, and remaining runs are reported as "not run". Any success resets the counter. At the end a summary line (`N/M runs succeeded, K failed, J not run`) is printed and the exit status is 1 if any run failed or the benchmark aborted.

### Pod resources

`execution.resources` sets CPU and memory for every benchmark container, mirroring Kubernetes `requests` and `limits`:

```yaml
execution:
  resources:
    requests: {cpu: 4, memory: 8Gi}
    limits:   {cpu: 4, memory: 8Gi}
```

Only `cpu` and `memory` are supported, and only the values you give replace the manifest's. The built-in manifests use these defaults:

| Backend | `requests` | `limits` |
|---|---|---|
| iperf3 | cpu 1, memory 1Gi | cpu 8, memory 16Gi |
| nixl | cpu 1, memory 8Gi | cpu 8, memory 32Gi |

The CPU limit is a CFS quota. If the work needs more cores than the limit (for example 8 threads with `limits.cpu: 1`), the pod is throttled and the result reflects the limit, not the network. Setting requests equal to limits makes the pods Guaranteed QoS, which gives more repeatable results and lets a node's static CPU manager assign dedicated cores if it is configured for that. A request above its limit (including the manifest's default limit) is rejected before any pod is created. The overrides are recorded as `resource_overrides` in each `*-command.json`.

### Image pull policy

`execution.image_pull_policy` sets `imagePullPolicy` on every benchmark pod: `Always` (default), `IfNotPresent`, or `Never`. Use `IfNotPresent` or `Never` for images loaded directly onto the nodes (e.g. a local build that is not in a registry), where `Always` would fail the pull.

### Profile validation

The loader rejects unknown fields. Common fields are:

```text
backend, name, test_id
network.initiator.{attachment,interface,ip}, network.target.{attachment,interface,ip} (iperf3 and nixl)
nodes.initiator, nodes.target
dimensions.threads
execution.namespace, execution.image, execution.log_directory
execution.runs, execution.timeout_seconds, execution.keep_resources
execution.max_consecutive_failures, execution.image_pull_policy
execution.resources.requests.{cpu,memory}, execution.resources.limits.{cpu,memory}
```

Backend-exposed fields are:

| Backend | Execution fields | Dimensions |
|---|---|---|
| iperf3 | `duration_seconds`, `warmup_seconds` | `threads` |
| nixl | `warmup_runs`, `run_timeout_seconds`, `gpu`, `parallelism`, `args`, `env` | `threads`, `block_size`, `batch_size` |

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
  --name my-iperf3 \
  --x threads
```

Example for one NIXL configuration:

```bash
./plot-benchmark.sh \
  --backend nixl \
  --name my-nixl \
  --x threads \
  --filter block_size=4M \
  --filter batch_size=1
```

Compare batch sizes as separate series:

```bash
./plot-benchmark.sh \
  --backend nixl \
  --name my-nixl \
  --x threads \
  --filter block_size=4M \
  --series batch_size
```

Compare block sizes as separate series:

```bash
./plot-benchmark.sh \
  --backend nixl \
  --name my-nixl \
  --x threads \
  --filter batch_size=1 \
  --series block_size
```

Plot block size on a logarithmic axis with base 2:

```bash
./plot-benchmark.sh \
  --backend nixl \
  --name my-nixl \
  --x block_size \
  --filter batch_size=1 \
  --filter threads=1 \
  --x-log 2
```

Supported graph dimensions are declared by each backend. Current dimensions are `threads` for iperf3 and `threads`, `block_size`, and `batch_size` for NIXL. Only `throughput` is supported as the Y metric.

Filters accept numeric values and, for byte dimensions, `K`, `M`, and `G` suffixes:

```text
block_size=1K
block_size=4M
block_size=1G
```

If a dimension varies but is neither the X axis, a filter, nor a series, graphing rejects the request. This prevents unrelated configurations from being silently averaged. Constant and filtered dimensions are included in the graph title. Legends are displayed outside the plot and series are assigned distinct colors.

Default graph path:

```text
logs/<name>/graphs/throughput-<x-dimension>[__<option>...].png
```

With no options the file is `throughput-<x-dimension>.png`. Every option that changes what the graph shows adds a part, joined with `__`, so different plots of one dataset do not overwrite each other:

| Option | Part added |
|---|---|
| `--filter NAME=VALUE` | `NAME-VALUE`, sorted by name, so their order does not matter |
| `--series DIM` | `by-DIM` (several series are joined with `-`) |
| `--percentile P` | `pP` |
| `--x-log BASE` | `xlogBASE` |
| `--max-throughput V` | `maxV` |

For example `--x threads --filter block_size=4M --series batch_size` writes `throughput-threads__block_size-4M__by-batch_size.png`. The same options always give the same name, so re-plotting overwrites only its own earlier graph. Characters other than letters, digits, dots and underscores become `-`, and very long names are shortened with a short hash. `--title` does not affect the name.

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
profiles/     Profile templates (profiles/local/ is git-ignored for your own)
src/          Framework, backends, graphing, and tests
logs/         Benchmark datasets and generated graphs
```
