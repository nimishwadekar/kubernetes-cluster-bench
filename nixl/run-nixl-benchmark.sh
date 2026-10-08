#!/usr/bin/env bash
set -euo pipefail

TEST_ID="${TEST_ID:-gpu-kv-01}"
NAMESPACE="${NAMESPACE:-nwadekar-pirate}"
IMAGE="${IMAGE:-quay.io/nwadekar/nixl-benchmark:og-nixl1.4.1-ucx1.22.0}"
CLIENT="${CLIENT:?Set CLIENT}"
SERVER="${SERVER:?Set SERVER}"

rendered="$(mktemp)"
server_pid=""
client_pid=""

cleanup() {
  status=$?

  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" 2>/dev/null || true
  fi
  if [[ -n "$client_pid" ]]; then
    kill "$client_pid" 2>/dev/null || true
  fi

  if [[ -s "$rendered" ]]; then
    echo "Cleaning up NIXLBench resources..."
    kubectl delete -n "$NAMESPACE" -f "$rendered" \
      --ignore-not-found=true >/dev/null 2>&1 || true
  fi

  rm -f "$rendered"
  exit "$status"
}

trap cleanup EXIT
trap 'exit 130' INT TERM

sed \
  -e "s/__TEST_ID__/${TEST_ID}/g" \
  -e "s#__IMAGE__#${IMAGE}#g" \
  -e "s/__CLIENT_NODE__/${CLIENT}/g" \
  -e "s/__SERVER_NODE__/${SERVER}/g" \
  nixl-benchmark.yaml > "$rendered"

echo "Applying NIXLBench resources in namespace: $NAMESPACE"
kubectl apply -n "$NAMESPACE" -f "$rendered"

echo "Waiting for benchmark pods..."
kubectl wait -n "$NAMESPACE" \
  --for=jsonpath='{.status.phase}'=Running \
  "pod/nixlbench-server-${TEST_ID}" \
  "pod/nixlbench-client-${TEST_ID}" \
  --timeout=300s

SERVER_IP=$(kubectl get pod "nixlbench-server-${TEST_ID}" \
  -n "$NAMESPACE" \
  -o jsonpath='{.status.podIP}')
if [[ -z "$SERVER_IP" ]]; then
  echo "Unable to determine the server pod IP." >&2
  exit 1
fi

ARGS=(
  --runtime_type ASIO
  --asio_address "$SERVER_IP"
  --asio_port 12345
  --backend UCX
  --initiator_seg_type DRAM
  --target_seg_type DRAM
  --total_buffer_size 8589934592
  --start_block_size 4194304
  --max_block_size 67108864
  # --max_block_size 4194304
  --start_batch_size 1
  --max_batch_size 8
  --num_iter 50
  --warmup_iter 10
  --num_threads 4
)

echo "Starting NIXLBench on both pods..."
kubectl exec -n "$NAMESPACE" \
  "nixlbench-server-${TEST_ID}" -- nixlbench "${ARGS[@]}" > server.log 2>&1 &
server_pid=$!

# ASIO accepts one direct connection and does not reliably retry an early
# client connection, so give the server time to bind before starting the client.
sleep 3

kubectl exec -n "$NAMESPACE" \
  "nixlbench-client-${TEST_ID}" -- nixlbench "${ARGS[@]}" > client.log 2>&1 &
client_pid=$!

wait "$server_pid" "$client_pid"
echo "NIXLBench completed. Results: server.log and client.log"
