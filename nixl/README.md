# NIXLBench with UCX

This directory contains a CUDA-enabled image and Kubernetes template for measuring GPU KV-cache transfers with NIXLBench and the UCX backend.

## Build the image

Build from the repository root. The source-build Dockerfile pins NIXL and UCX independently:

```bash
docker build \
  -f nixl/Dockerfile \
  --build-arg NIXL_VERSION=v1.4.1 \
  --build-arg UCX_VERSION=v1.21.0 \
  -t kubernetes-cluster-bench-nixl:local .
```

NIXL also provides the NVIDIA CUDA DL base image used by [`Dockerfile.cuda-dl-base`](./Dockerfile.cuda-dl-base). Authenticate to NGC first, then build this variant:

```bash
docker login nvcr.io

docker buildx build \
  --platform linux/amd64 \
  -f nixl/Dockerfile.cuda-dl-base \
  -t kubernetes-cluster-bench-nixl:cuda-dl-base \
  --load .
```

This variant uses UCX supplied by the base image instead of compiling UCX from source. Check the base image's UCX version with `ucx_info -v`; use the source-build Dockerfile when an exact UCX version is required.

The vLLM runtime variant starts from `vllm/vllm-openai:v0.31.0`, which contains NIXL 1.4.1 and bundled UCX 1.22.0 runtime libraries. [`Dockerfile.vllm-nixlbench`](./Dockerfile.vllm-nixlbench) reuses those libraries, clones matching NIXL headers and benchmark sources, and builds only NIXLBench:

```bash
docker buildx build \
  --platform linux/amd64 \
  -f nixl/Dockerfile.vllm-nixlbench \
  -t kubernetes-cluster-bench-nixl:vllm-0.31.0 \
  --load .
```

The source-build image builds UCX with CUDA and verbs support, builds NIXL with the UCX plugin, builds NIXLBench, and includes the etcd C++ client required for worker coordination. GDRCopy is not built because its kernel module must match the host NVIDIA driver; UCX and NIXL work without it. Its other build arguments include `CUDA_VERSION`, `ETCD_CPP_VERSION`, and the installation prefixes used inside the image.

The CUDA DL base variant supports `BASE_IMAGE`, `BASE_IMAGE_TAG`, `NIXL_VERSION`, `ETCD_CPP_VERSION`, `ABSL_VERSION`, `GRPC_VERSION`, and the installation prefixes. It does not accept `UCX_VERSION` because it uses the UCX installation supplied by the base image.

## Deploy the benchmark resources

[nixl-benchmark.yaml](./nixl-benchmark.yaml) creates an etcd instance and one GPU-enabled benchmark pod on each requested node. It uses host networking so UCX can access the node network and RDMA path when the cluster exposes one.

Replace the placeholders before applying the template:

```bash
sed \
  -e 's/__TEST_ID__/gpu-kv-01/g' \
  -e 's#__IMAGE__#registry.example.com/kubernetes-cluster-bench-nixl:latest#g' \
  -e 's/__CLIENT_NODE__/worker-a/g' \
  -e 's/__SERVER_NODE__/worker-b/g' \
  nixl/nixl-benchmark.yaml | kubectl apply -f -
```

The cluster must have the NVIDIA device plugin installed and expose one `nvidia.com/gpu` resource on each benchmark node. RDMA support additionally requires the cluster's RDMA device plugin and host drivers.

## Run NIXLBench

After the pods are ready, run the same command in both pods. NIXLBench uses etcd to coordinate the initiator and target workers:

```bash
ETCD_ENDPOINT=http://nixlbench-etcd-gpu-kv-01:2379
ARGS=(
  --etcd_endpoints "$ETCD_ENDPOINT"
  --backend UCX
  --initiator_seg_type VRAM
  --target_seg_type VRAM
  --total_buffer_size 8GiB
  --start_block_size 4MiB
  --max_block_size 256MiB
  --num_iter 1000
  --warmup_iter 100
)

kubectl exec nixlbench-server-gpu-kv-01 -- nixlbench "${ARGS[@]}" > server.txt
kubectl exec nixlbench-client-gpu-kv-01 -- nixlbench "${ARGS[@]}" > client.txt
```

The exact NIXLBench command-line options depend on the NIXL version selected at image-build time. Use `nixlbench --help` in the image for the authoritative options.
